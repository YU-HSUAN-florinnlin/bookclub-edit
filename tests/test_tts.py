"""bookclub/tts.py、bookclub/fit.py 的單元測試：不載入 CosyVoice、不打 Groq，
生成與轉回文字都用假的函式代替，測重試策略、續跑、放回時間格。

獨立可跑（不需要 pytest）：.venv/bin/python tests/test_tts.py
裝了 pytest 的話也可以：.venv/bin/python -m pytest tests/test_tts.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 10-01：這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import soundfile as sf

from bookclub import fit, pauses, tts
from bookclub import workdir as wd

SR = 24000
TOL = 0.15


def _dur(path: Path) -> float:
    info = sf.info(str(path))
    return info.frames / info.samplerate


# ---------- fit：三種情況 ----------

def test_fit_student_short_pads():
    plan = fit.plan_fit(4.0, 5.0, "學員")
    assert plan["做法"] == fit.PAD and plan["atempo"] is None


def test_fit_student_slightly_long_tempo():
    plan = fit.plan_fit(5.5, 5.0, "學員")
    assert plan["做法"] == fit.TEMPO
    assert abs(plan["atempo"] - 1.1) < 1e-6


def test_fit_student_too_long_flags():
    plan = fit.plan_fit(6.0, 5.0, "學員")
    assert plan["做法"] == fit.FLAG and "停格" in plan["原因"]


def test_fit_teacher_too_short_flags_but_student_pads():
    assert fit.plan_fit(4.0, 5.0, "老師")["做法"] == fit.FLAG
    assert fit.plan_fit(4.0, 5.0, "學員")["做法"] == fit.PAD
    assert fit.plan_fit(4.5, 5.0, "老師")["做法"] == fit.PAD


def test_apply_fit_output_matches_slot():
    if not shutil.which("ffmpeg"):
        print("  （沒有 ffmpeg，略過）")
        return
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "in.wav"
        t = np.arange(int(5.5 * SR)) / SR
        sf.write(str(src), (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), SR)
        for gen_s, slot in ((5.5, 5.0), (5.5, 6.0), (5.5, 4.0)):
            plan = fit.plan_fit(gen_s, slot, "學員")
            out = fit.apply_fit(src, Path(d) / f"out_{slot}.wav", slot, plan)
            assert abs(_dur(out) - slot) < 0.02, (slot, _dur(out))


# ---------- 文字處理 ----------

def test_prompt_text_simplified_with_prefix():
    p = tts.prompt_text("我們來聽聽看這週的練習。")
    assert p.startswith(tts.PROMPT_PREFIX)
    assert "们" in p and "这" in p


def test_content_score_ignores_script_and_punct():
    assert tts.content_score("好，那我們來聽聽看。Bella！", "好那我们来听听看 bella") == 1.0
    # 漏掉半句要掉到門檻以下
    full = "我可以透過覺察來管理情緒，不被情緒帶走，這個是我們今天課程的重點之一。"
    half = "我可以透過覺察，這個是我們今天課程的重點之一。"
    assert tts.content_score(full, half) < tts.CONTENT_MIN


def test_load_sentences_txt_and_json():
    with tempfile.TemporaryDirectory() as d:
        txt = Path(d) / "s.txt"
        txt.write_text("第一句。\n\n第二句。\n", encoding="utf-8")
        items = tts.load_sentences(txt)
        assert [i["id"] for i in items] == ["01", "02"] and items[0]["slot_s"] is None
        js = Path(d) / "s.json"
        js.write_text(json.dumps([{"id": "T4", "text": "原話", "slot": [10.0, 16.5]}]), encoding="utf-8")
        items = tts.load_sentences(js)
        assert items[0]["slot_s"] == 6.5
        js.write_text(json.dumps([{"id": "../x", "text": "a"}]), encoding="utf-8")
        try:
            tts.load_sentences(js)
            raise AssertionError("編號含路徑字元應該要擋")
        except ValueError:
            pass


# ---------- 重試策略 ----------

def _att(seed, speed, audio_s, content):
    return tts.Attempt(seed, speed, audio_s, audio_s * 20, "x", content)


def test_next_attempt_first_is_seed42():
    assert tts.next_attempt([], None, TOL) == (42, 1.0)


def test_next_attempt_content_fail_changes_seed():
    assert tts.next_attempt([_att(42, 1.0, 5.0, 0.5)], None, TOL) == (1, 1.0)


def test_next_attempt_length_fail_keeps_seed_changes_speed():
    seed, speed = tts.next_attempt([_att(42, 1.0, 6.0, 0.95)], 5.0, TOL)
    assert seed == 42 and abs(speed - 1.2) < 1e-6
    seed, speed = tts.next_attempt([_att(42, 1.0, 4.0, 0.95)], 5.0, TOL)
    assert seed == 42 and abs(speed - 0.85) < 1e-6  # 0.8 被夾到下限


def test_next_attempt_stops_when_pass_or_max():
    assert tts.next_attempt([_att(42, 1.0, 5.0, 0.95)], 5.0, TOL) is None
    three = [_att(42, 1.0, 5.0, 0.1), _att(1, 1.0, 5.0, 0.1), _att(2026, 1.0, 5.0, 0.1)]
    assert tts.next_attempt(three, None, TOL) is None


def test_next_attempt_avoids_used_seeds_in_same_order():
    """10-02 第四批：退回重做、文字範圍沒改的，從還沒用過的下一個種子開始；念錯再換也不回到用過的；SEEDS 用完接著往下。"""
    assert tts.next_attempt([], None, TOL, [42]) == (1, 1.0)
    assert tts.next_attempt([_att(1, 1.0, 5.0, 0.5)], None, TOL, [42]) == (2026, 1.0)
    assert tts.next_attempt([], None, TOL, [42, 1, 2026]) == (2027, 1.0)
    assert tts.next_attempt([_att(2027, 1.0, 5.0, 0.5)], None, TOL, [42, 1, 2026]) == (2028, 1.0)
    seed, speed = tts.next_attempt([_att(1, 1.0, 6.0, 0.95)], 5.0, TOL, [42])   # 長度不合：同一個種子改語速
    assert seed == 1 and abs(speed - 1.2) < 1e-6
    assert tts.next_attempt([], None, TOL, []) == (42, 1.0) and tts.seed_order([]) == tts.SEEDS


def _redo_round(work: Path, role: str, ids: list[str], stamp: str) -> dict:
    from bookclub import finalcheck

    got = finalcheck.note_versions(work, role, ids, stamp, {i: "音質" for i in ids})
    finalcheck.clear_generated(work, role, ids, stamp)
    return got


def test_teacher_and_kept_student_redo_change_seed_keep_old_version():
    """10-02 第四批：老師重念、保留原聲學員名字跟學員重念一樣：退回重做、文字和範圍沒改 → 換下一個種子；
    舊的那一版留在紀錄裡、聲音檔在備份資料夾；再退回再換；字改了照原本的規則。老師照第 4 步分三支程式跑。"""
    from bookclub import studentgen

    texts = {"A": "好，那我們來聽聽看大家這週的練習。", "B": "這個是我們今天課程的重點之一。", "N1": "好，謝謝你的分享。"}
    hear_ok = lambda p: texts[Path(p).name.split("_")[0]]  # noqa: E731
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        ref = work / "參考音"
        ref.mkdir()
        sf.write(str(ref / "ref.wav"), np.zeros(SR, dtype=np.float32), SR)
        (ref / "ref.txt").write_text("參考音逐字稿", encoding="utf-8")
        sp = work / "句子.json"
        sp.write_text(json.dumps([{"id": "A", "text": hear_ok("A_")}, {"id": "B", "text": hear_ok("B_")}],
                                 ensure_ascii=False), encoding="utf-8")

        def run_all(synth):
            for phase in tts.PHASES:
                out = tts.generate_teacher(work, sp, synth=synth if phase != "停頓" else _never, hear=hear_ok,
                                           check_similarity=False, use_pauses=False, phase=phase, log=lambda s: None)
            return {r["id"]: r for r in out["句子"]}

        synth = FakeSynth()
        recs = run_all(synth)
        assert [c[1] for c in synth.calls] == [42, 42]
        for n, want in ((2, 1), (3, 2026), (4, 2027)):
            assert _redo_round(work, "老師", ["B"], f"2026-10-02T10:0{n}:00") == {"B": {"第幾版": n}}
            synth = FakeSynth()
            recs = run_all(synth)
            assert [c[1] for c in synth.calls] == [want]                 # 只有 B 重新生成，換下一個沒用過的
            b = recs["B"]
            assert b["第幾版"] == n and b["換一種念法"] and b["嘗試"][b["選定"] - 1]["種子"] == want
            assert [v["種子"] for v in b["以前的版本"]] == [42, 1, 2026][:n - 1]
            assert (work / b["以前的版本"][-1]["備份資料夾"] / "B.wav").is_file()
            assert "第幾版" not in recs["A"]
        # 字改了：照原本的規則從 42 開始（版本照樣記）
        _redo_round(work, "老師", ["B"], "2026-10-02T10:09:00")
        texts["B"] = "這個是我們今天課程的另一個重點。"
        sp.write_text(json.dumps([{"id": "A", "text": texts["A"]}, {"id": "B", "text": texts["B"]}],
                                 ensure_ascii=False), encoding="utf-8")
        synth = FakeSynth()
        recs = run_all(synth)
        assert [c[1] for c in synth.calls] == [42] and recs["B"]["第幾版"] == 5 and not recs["B"]["換一種念法"]

        # 保留原聲學員名字：一樣的生成程式（run_generation），紀錄、資料夾不同
        od = studentgen.out_dir(work)
        od.mkdir(parents=True)
        it = {"id": "N1", "text": texts["N1"], "生成用文字": texts["N1"], "發音對照": [], "slot": None, "slot_s": None}
        done: dict = {}
        save = lambda: wd.write_json(studentgen.log_path(work), {"句子": list(done.values())})   # noqa: E731
        for rnd, want in enumerate((42, 1, 2026), start=1):
            if rnd > 1:
                assert _redo_round(work, "保留原聲學員", ["N1"], f"2026-10-02T11:0{rnd}:00") == {"N1": {"第幾版": rnd}}
                done.clear()
            synth = FakeSynth()
            tts.run_generation(work, [dict(it)], od, ref / "ref.wav", "參考", TOL, save=save, done=done, role="學員",
                               check_similarity=False, use_pauses=False, synth=synth, hear=hear_ok,
                               log=lambda s: None)
            save()
            assert [c[1] for c in synth.calls] == [want]
            assert done["N1"].get("第幾版", 1) == rnd


def test_choose_best_prefers_content_pass():
    h = [_att(42, 1.0, 5.0, 0.6), _att(1, 1.0, 5.0, 0.9), _att(2026, 1.0, 5.0, 0.7)]
    assert tts.choose_best(h, None, TOL) == 1


# ---------- 整條流程（假模型） ----------

class FakeSynth:
    """第一次生成（種子 42）故意念錯，之後念對；長度＝字數 × 0.25 秒。"""

    def __init__(self):
        self.calls = []

    def __call__(self, text, seed, speed):
        self.calls.append((text, seed, speed))
        n = int(len(text) * 0.25 / speed * SR)
        return np.zeros(n, dtype=np.float32) + 0.01, SR


def _fake_hear_factory(synth: FakeSynth, sentences: dict):
    def hear(path: Path) -> str:
        text, seed, _ = synth.calls[-1]
        return "完全不對的內容" if seed == 42 and text == sentences["A"] else text
    return hear


def test_generate_teacher_end_to_end_with_resume():
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        ref = work / "參考音"
        ref.mkdir()
        sf.write(str(ref / "ref.wav"), np.zeros(SR, dtype=np.float32), SR)
        (ref / "ref.txt").write_text("參考音逐字稿", encoding="utf-8")
        sentences = {"A": "好，那我們來聽聽看大家這週的練習。", "B": "這個是我們今天課程的重點之一。"}
        slot_b = len(sentences["B"]) * 0.25  # 剛好等長
        sp = work / "句子.json"
        sp.write_text(json.dumps([
            {"id": "A", "text": sentences["A"]},
            {"id": "B", "text": sentences["B"], "slot": [100.0, 100.0 + slot_b]},
        ], ensure_ascii=False), encoding="utf-8")

        synth = FakeSynth()
        log = tts.generate_teacher(work, sp, synth=synth, hear=_fake_hear_factory(synth, sentences),
                                   check_similarity=False)
        a, b = log["句子"]
        assert [x["種子"] for x in a["嘗試"]] == [42, 1] and a["選定"] == 2 and not a["要人聽"]
        assert len(b["嘗試"]) == 1 and b["放回時間格"]["放回做法"] == fit.PAD and b["建議做法"] == "補靜音"
        assert (work / "生成" / "老師" / "A.wav").is_file()
        if shutil.which("ffmpeg"):
            assert abs(_dur(work / b["放回時間格"]["檔案"]) - slot_b) < 0.02
        assert log["統計"]["生成次數"] == 3

        # 續跑：沒改的句子不重生成
        synth2 = FakeSynth()
        tts.generate_teacher(work, sp, synth=synth2, hear=_fake_hear_factory(synth2, sentences),
                             check_similarity=False)
        assert synth2.calls == []


# ---------- 插入停頓（pauses） ----------

def test_content_check_failure_does_not_stop_generation():
    """09-30：掛一整晚時 Groq 連不上，不能讓整批生成停下來——這一句標要人聽、照常往下；下次續跑只補檢查、不重新生成。"""
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        ref = work / "參考音"
        ref.mkdir()
        sf.write(str(ref / "ref.wav"), np.zeros(SR, dtype=np.float32), SR)
        (ref / "ref.txt").write_text("參考音逐字稿", encoding="utf-8")
        sentences = {"A": "這個是我們今天課程的重點之一。", "B": "我們下週見。"}
        sp = work / "句子.json"
        sp.write_text(json.dumps([{"id": k, "text": v} for k, v in sentences.items()], ensure_ascii=False), encoding="utf-8")

        def broken(path):
            raise ConnectionError("連不上")

        synth = FakeSynth()
        log = tts.generate_teacher(work, sp, synth=synth, hear=broken, check_similarity=False)
        assert [r["要人聽"] for r in log["句子"]] == [True, True] and len(synth.calls) == 2     # 兩句都生成了，沒有整批停
        assert all(r["嘗試"][0]["內容檢查沒做成"] and not r["內容已檢查"] for r in log["句子"])

        # 網路好了重跑（--redo）：聲音沿用上次生成的，只補做內容檢查
        synth2 = FakeSynth()
        log = tts.generate_teacher(work, sp, synth=synth2, hear=lambda p: sentences[Path(p).name.split("_")[0]],
                                   check_similarity=False, redo=True)
        assert synth2.calls == [] and [r["要人聽"] for r in log["句子"]] == [False, False]
        assert all(r["內容已檢查"] for r in log["句子"])


def _tone_s(sec):
    t = np.arange(int(sec * SR)) / SR
    return (0.3 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)


def _sil(sec):
    return np.zeros(int(sec * SR), dtype=np.float32)


def test_detect_silences_finds_long_gap_only():
    x = np.concatenate([_tone_s(1.0), _sil(0.1), _tone_s(1.0), _sil(0.8), _tone_s(1.0)])
    sil = pauses.detect_silences(x, SR)
    assert len(sil) == 1 and abs(sil[0][0] - 2.1) < 0.05 and abs(sil[0][1] - 2.9) < 0.05


def _chars(spec):
    return [pauses.Char(t, s, e) for t, s, e in spec]


def test_pauses_after_chars_maps_gap_between_words():
    chars = _chars([("好", 0.0, 0.3), ("那", 0.3, 0.6), ("我", 1.6, 1.9), ("們", 1.9, 2.2)])
    assert pauses.pauses_after_chars(chars, [(0.62, 1.58)]) == {1: 1.0}
    assert pauses.pauses_after_chars(chars, [(3.0, 4.0)]) == {}  # 結尾的空白不算


def test_plan_inserts_dropped_repeat_takes_max_not_sum():
    # 原片「素，催產素，催產素就」：兩個「素」後面各停 2.0、2.4 秒；生成少念一次「催產素」
    orig = _chars([(c, i, i + 0.5) for i, c in enumerate("加催產素催產素催產素就會")])
    orig_p = {3: 2.0, 6: 2.4}
    gen = _chars([(c, i * 0.3, i * 0.3 + 0.3) for i, c in enumerate("加催產素催產素就會")])
    x = np.concatenate([_tone_s(len(gen) * 0.3)])
    ins = pauses.plan_inserts(orig, orig_p, gen, x, SR)
    assert len(ins) <= 2 and all(add <= 2.4 for _, add in ins)
    assert sum(add for _, add in ins) <= 4.4


def test_apply_inserts_length_and_lead():
    x = _tone_s(3.0)
    y = pauses.apply_inserts(x, SR, [(1.0, 0.5), (2.0, 0.7)], lead=0.4)
    assert abs(len(y) / SR - (3.0 + 0.5 + 0.7 + 0.4)) < 0.01
    # 插入的地方真的是安靜的
    at = int((0.4 + 1.0 + 0.25) * SR)
    assert np.abs(y[at - 100:at + 100]).max() == 0
    y2 = pauses.apply_inserts(x, SR, [], lead=-0.5)
    assert abs(len(y2) / SR - 2.5) < 0.01


def test_generate_teacher_pause_variant_recommended():
    """原片：老師講 2 秒、停 2 秒、講 2 秒（時間格 6 秒）；生成一口氣念完 4 秒 → 插入停頓後剛好 6 秒。"""
    if not shutil.which("ffmpeg"):
        print("  （沒有 ffmpeg，略過）")
        return
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        ref = work / "參考音"
        ref.mkdir()
        sf.write(str(ref / "ref.wav"), np.zeros(SR, dtype=np.float32), SR)
        (ref / "ref.txt").write_text("參考", encoding="utf-8")
        orig = np.concatenate([_sil(10.0), _tone_s(2.0), _sil(2.0), _tone_s(2.0), _sil(5.0)])
        sf.write(str(work / "audio.wav"), orig, SR)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(work / "audio.wav"), str(work / "audio.flac")], check=True)
        (work / "transcript").mkdir()
        (work / "transcript" / "merged.json").write_text(json.dumps({"sentences": [
            {"start": 10.0, "end": 12.0, "text": "甲乙丙丁，"}, {"start": 14.0, "end": 16.0, "text": "戊己庚辛。"}]},
            ensure_ascii=False), encoding="utf-8")
        sp = work / "句子.json"
        sp.write_text(json.dumps([{"id": "P", "slot": [10.0, 16.0]}]), encoding="utf-8")

        def synth(text, seed, speed):
            return _tone_s(4.0), SR

        def align(path, text):
            chars = [c for c in text if c.isalnum()]
            if "原聲" in Path(path).name:  # 原片：前 4 字 0–2 秒，後 4 字 4–6 秒
                return [pauses.Char(c, (i if i < 4 else i + 4) * 0.5, (i if i < 4 else i + 4) * 0.5 + 0.5)
                        for i, c in enumerate(chars)]
            return [pauses.Char(c, i * 0.5, i * 0.5 + 0.5) for i, c in enumerate(chars)]

        log = tts.generate_teacher(work, sp, synth=synth, hear=lambda p: "甲乙丙丁戊己庚辛",
                                   check_similarity=False, align=align)
        r = log["句子"][0]
        assert r["text"] == "甲乙丙丁，戊己庚辛。"  # 沒給文字 → 用原片逐字稿
        assert r["原片停頓"] == [{"在這個字後面": "丁", "秒": 2.0}]
        assert r["建議做法"] == "插入停頓" and not r["要人聽"]
        names = [v["版本"] for v in r["候選做法"]]
        assert names[:2] == ["插入停頓", "補靜音"] and "拉長" in names
        assert abs(_dur(work / r["放回時間格"]["檔案"]) - 6.0) < 0.02
        assert len(r["嘗試"]) == 1  # 插入停頓就過關，不必改語速重生成


def test_generate_teacher_speed_retry_only_after_pauses_fail():
    """沒有原片可以插入停頓、長度又差太多 → 第三階段才改語速重生成，並出現「改語速重生成」「拉長」候選。"""
    if not shutil.which("ffmpeg"):
        return
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        ref = work / "參考音"
        ref.mkdir()
        sf.write(str(ref / "ref.wav"), np.zeros(SR, dtype=np.float32), SR)
        (ref / "ref.txt").write_text("參考", encoding="utf-8")
        sp = work / "句子.json"
        sp.write_text(json.dumps([{"id": "L", "text": "甲乙丙丁", "slot_s": 6.0}]), encoding="utf-8")
        calls = []

        def synth(text, seed, speed):
            calls.append(speed)
            return _tone_s(4.0 / speed), SR

        log = tts.generate_teacher(work, sp, synth=synth, hear=lambda p: "甲乙丙丁", check_similarity=False)
        r = log["句子"][0]
        assert calls == [1.0, 0.85]
        names = [v["版本"] for v in r["候選做法"]]
        assert names == ["補靜音", "改語速重生成", "拉長"] and r["要人聽"]


def test_punct_boundaries_and_snap():
    text = "它會增加催產素，催產素就會讓人愉快。"
    gen = _chars([(c, i * 0.3, i * 0.3 + 0.3) for i, c in enumerate("它會增加催產素催產素就會讓人愉快")])
    b = pauses.punct_boundaries(gen, text)
    assert b == {6, 15}
    # 「會」後面（1）與第二個「素」後面（9）的停頓都移到「素，」（6），取最長的；結尾的句號不算
    assert pauses.snap_to_boundaries({1: 0.5, 9: 2.4}, b, len(gen) - 1) == {6: 2.4}
    # 英文名字也對得上
    gen2 = _chars([("Bella", 0, 0.5), ("妳", 0.5, 0.7), ("好", 0.7, 0.9)])
    assert pauses.punct_boundaries(gen2, "Bella，妳好。") == {0, 2}


def test_pron_table_applies_only_to_synth_text():
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        table = work / "發音對照表.csv"
        table.write_text("原字,生成用,原因,建立日期\n愉快,魚快,念成玉快,2026-09-25\n", encoding="utf-8")
        assert tts.apply_pron("覺得愉快、很愉快", tts.load_pron_table(table)) == ("覺得魚快、很魚快", ["愉快→魚快"])
        assert tts.load_pron_table(work / "沒有這個檔.csv") == []
        ref = work / "參考音"
        ref.mkdir()
        sf.write(str(ref / "ref.wav"), np.zeros(SR, dtype=np.float32), SR)
        (ref / "ref.txt").write_text("參考", encoding="utf-8")
        sp = work / "句子.txt"
        sp.write_text("讓人覺得愉快。\n", encoding="utf-8")
        said = []

        def synth(text, seed, speed):
            said.append(text)
            return _tone_s(1.0), SR

        heard = []

        def hear(p):
            heard.append(p)
            return "讓人覺得愉快"

        log = tts.generate_teacher(work, sp, synth=synth, hear=hear, check_similarity=False, pron_table=table)
        r = log["句子"][0]
        assert said == ["讓人覺得魚快。"] and r["text"] == "讓人覺得愉快。" and r["嘗試"][0]["內容通過"]
        assert r["生成用文字"] == "讓人覺得魚快。" and r["發音對照"] == ["愉快→魚快"]
        # 對照表改了（拿掉）→ 這句要重新生成
        table.write_text("原字,生成用,原因,建立日期\n", encoding="utf-8")
        tts.generate_teacher(work, sp, synth=synth, hear=hear, check_similarity=False, pron_table=table)
        assert said[-1] == "讓人覺得愉快。"


def test_start_offset_and_prepend_silence():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        t = np.arange(SR) / SR
        tone = (0.3 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
        orig = np.concatenate([np.zeros(int(1.2 * SR), np.float32), tone])     # 原片 1.2 秒後才開口
        gen = np.concatenate([np.zeros(int(0.05 * SR), np.float32), tone])     # 生成檔 0.05 秒就開口
        sf.write(str(tmp / "o.wav"), orig, SR)
        sf.write(str(tmp / "g.wav"), gen, SR)
        lead = tts.start_offset(tmp / "o.wav", tmp / "g.wav")
        assert abs(lead - 1.15) < 0.03, lead
        tts.prepend_silence(tmp / "g.wav", tmp / "s.wav", lead)
        assert abs(tts.start_offset(tmp / "o.wav", tmp / "s.wav")) < 0.03
        assert tts.start_offset(tmp / "g.wav", tmp / "o.wav") == 0.0   # 生成的比較晚開口：不補


def test_trim_silence_skips_blip_and_keeps_margin():
    x = np.concatenate([_sil(0.12), _tone_s(0.06), _sil(0.5), _tone_s(2.0), _sil(0.3)])
    y = pauses.trim_silence(x, SR)
    assert abs(len(y) / SR - 2.1) < 0.05, len(y) / SR


def _run_all() -> int:
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, func in tests:
        try:
            func()
            print(f"✓ {name}")
        except Exception as exc:
            failed += 1
            print(f"✗ {name}：{exc!r}")
    print(f"{len(tests) - failed}/{len(tests)} 通過")
    return 1 if failed else 0


def test_record_stale_checks_text_pron_slot_and_ref():
    # 09-29 檢查 #7：生成程式和第 4 步「做過沒有」共用同一個判斷
    import tempfile

    from bookclub import tts as t

    ref = Path(tempfile.mkdtemp()) / "ref.wav"
    ref.write_bytes(b"A")
    rec = {"text": "你好", "生成用文字": "你好", "slot": [1.0, 2.0], "參考音": str(ref), "參考音指紋": t.ref_fingerprint(ref)}
    it = {"text": "你好", "生成用文字": "你好", "slot": [1.0, 2.0]}
    assert not t.record_stale(rec, it, ref)
    assert t.record_stale(None, it)
    assert t.record_stale(rec, {**it, "text": "您好"})
    assert t.record_stale(rec, {**it, "生成用文字": "妳好"})          # 發音對照表改了
    assert t.record_stale(rec, {**it, "slot": [1.0, 2.3]})           # 時間格改了
    assert not t.record_stale(rec, {**it, "slot": [1.01, 2.02]})     # 差一點點不算
    ref.write_bytes(b"B")                                            # 同一個檔名、內容換了
    assert t.record_stale(rec, it, ref)


# ---------- 10-01：三個階段分給三支程式跑（第 4 步分開程式跑） ----------

def _pause_work(d: Path) -> Path:
    """跟 test_generate_teacher_pause_variant_recommended 一樣的原片：講 2 秒、停 2 秒、講 2 秒（時間格 6 秒）。"""
    work = Path(d)
    ref = work / "參考音"
    ref.mkdir()
    sf.write(str(ref / "ref.wav"), np.zeros(SR, dtype=np.float32), SR)
    (ref / "ref.txt").write_text("參考", encoding="utf-8")
    orig = np.concatenate([_sil(10.0), _tone_s(2.0), _sil(2.0), _tone_s(2.0), _sil(5.0)])
    sf.write(str(work / "audio.wav"), orig, SR)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(work / "audio.wav"), str(work / "audio.flac")], check=True)
    (work / "transcript").mkdir()
    (work / "transcript" / "merged.json").write_text(json.dumps({"sentences": [
        {"start": 10.0, "end": 12.0, "text": "甲乙丙丁，"}, {"start": 14.0, "end": 16.0, "text": "戊己庚辛。"}]},
        ensure_ascii=False), encoding="utf-8")
    (work / "句子.json").write_text(json.dumps([{"id": "P", "slot": [10.0, 16.0]}]), encoding="utf-8")
    return work


def _pause_align(calls: list):
    def align(path, text):
        calls.append(Path(path).name)
        chars = [c for c in text if c.isalnum()]
        if "原聲" in Path(path).name:
            return [pauses.Char(c, (i if i < 4 else i + 4) * 0.5, (i if i < 4 else i + 4) * 0.5 + 0.5)
                    for i, c in enumerate(chars)]
        return [pauses.Char(c, i * 0.5, i * 0.5 + 0.5) for i, c in enumerate(chars)]
    return align


def _never(*a, **k):
    raise AssertionError("這一支程式不該用到這個模型")


def test_three_programs_same_result_as_one():
    if not shutil.which("ffmpeg"):
        print("  （沒有ffmpeg，略過）")
        return
    hear = lambda p: "甲乙丙丁戊己庚辛"   # noqa: E731
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        one = _pause_work(Path(d1))
        r1 = tts.generate_teacher(one, one / "句子.json", synth=lambda t, s, v: (_tone_s(4.0), SR), hear=hear,
                                  check_similarity=False, align=_pause_align([]))["句子"][0]
        work = _pause_work(Path(d2))
        sp = work / "句子.json"
        try:                                    # 還沒生成就做插入停頓：停下來報錯，不載入生成模型
            tts.generate_teacher(work, sp, synth=_never, hear=hear, check_similarity=False,
                                 align=_pause_align([]), phase="停頓")
            raise AssertionError("應該報錯")
        except tts.NotGeneratedYet:
            pass
        made = []
        out = tts.generate_teacher(work, sp, synth=lambda t, s, v: made.append(v) or (_tone_s(4.0), SR), hear=hear,
                                   check_similarity=False, align=_never, phase="生成")
        assert out == {} and made == [1.0] and not tts.teacher_log_path(work).exists()   # 生成那一支不寫紀錄
        try:                                    # 還沒插入停頓就收尾：一樣停下來報錯
            tts.generate_teacher(work, sp, synth=_never, hear=hear, check_similarity=False, align=_never, phase="收尾")
            raise AssertionError("應該報錯")
        except tts.NotGeneratedYet:
            pass
        aligned: list = []
        tts.generate_teacher(work, sp, synth=_never, hear=hear, check_similarity=False,
                             align=_pause_align(aligned), phase="停頓")
        assert aligned and (work / "生成" / "老師" / tts.PAUSE_CACHE).is_file()
        again: list = []                        # 停頓那一支重跑：記過的沿用，不再對位
        tts.generate_teacher(work, sp, synth=_never, hear=hear, check_similarity=False,
                             align=_pause_align(again), phase="停頓")
        assert again == []
        r2 = tts.generate_teacher(work, sp, synth=_never, hear=hear, check_similarity=False, align=_never,
                                  phase="收尾")["句子"][0]
        for k in ("建議做法", "原片停頓", "要人聽", "選定"):
            assert r1[k] == r2[k], k
        assert [v["版本"] for v in r1["候選做法"]] == [v["版本"] for v in r2["候選做法"]]
        assert [v["差異比例"] for v in r1["候選做法"]] == [v["差異比例"] for v in r2["候選做法"]]
        assert r2["建議做法"] == "插入停頓" and abs(_dur(work / r2["放回時間格"]["檔案"]) - 6.0) < 0.02


def test_three_programs_speed_retry_only_in_last():
    if not shutil.which("ffmpeg"):
        return
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        ref = work / "參考音"
        ref.mkdir()
        sf.write(str(ref / "ref.wav"), np.zeros(SR, dtype=np.float32), SR)
        (ref / "ref.txt").write_text("參考", encoding="utf-8")
        sp = work / "句子.json"
        sp.write_text(json.dumps([{"id": "L", "text": "甲乙丙丁", "slot_s": 6.0}]), encoding="utf-8")
        calls = []

        def synth(text, seed, speed):
            calls.append(speed)
            return _tone_s(4.0 / speed), SR

        hear = lambda p: "甲乙丙丁"   # noqa: E731
        tts.generate_teacher(work, sp, synth=synth, hear=hear, check_similarity=False, phase="生成")
        assert calls == [1.0]
        tts.generate_teacher(work, sp, synth=_never, hear=hear, check_similarity=False, align=_never, phase="停頓")
        r = tts.generate_teacher(work, sp, synth=synth, hear=hear, check_similarity=False, align=_never,
                                 phase="收尾")["句子"][0]
        assert calls == [1.0, 0.85]             # 改語速重生成在收尾那一支
        assert [v["版本"] for v in r["候選做法"]] == ["補靜音", "改語速重生成", "拉長"]


# ---------- 10-01：工作區複製到別的資料夾，參考音只比內容 ----------

def test_same_ref_file_compares_content_when_fingerprint_recorded():
    a = Path(tempfile.mkdtemp()) / "參考音" / "ref.wav"
    a.parent.mkdir()
    a.write_bytes(b"AAAA")
    b = Path(tempfile.mkdtemp()) / "別的資料夾" / "ref.wav"
    b.parent.mkdir()
    b.write_bytes(b"AAAA")
    rec = {"參考音": str(a), "參考音指紋": tts.ref_fingerprint(a)}
    assert tts.same_ref_file(rec, a)
    assert tts.same_ref_file(rec, b), "路徑不同、內容一樣：算同一個"
    b.write_bytes(b"BBBB")
    assert not tts.same_ref_file(rec, b), "內容換了：不算"
    old = {"參考音": str(a)}                          # 舊紀錄沒有指紋：只比路徑
    assert tts.same_ref_file(old, a) and not tts.same_ref_file(old, b)
    it = {"text": "你好", "slot": [1.0, 2.0]}
    assert not tts.record_stale({**rec, **it}, it, a.parent.parent / "參考音" / "ref.wav")
    assert tts.ref_key(a) == f"參考音#{tts.ref_fingerprint(a)}"
    assert tts.ref_key(a, legacy=True) == f"{a}#{tts.ref_fingerprint(a)}"


def test_copied_workdir_reuses_teacher_sentences():
    """生成完把整個工作區複製到別處：老師的句子全部沿用，不重新生成（10-01）。"""
    with tempfile.TemporaryDirectory() as d:
        work = Path(d) / "原本"
        ref = work / "參考音"
        ref.mkdir(parents=True)
        sf.write(str(ref / "ref.wav"), np.zeros(SR, dtype=np.float32), SR)
        (ref / "ref.txt").write_text("參考", encoding="utf-8")
        (work / "句子.json").write_text(json.dumps([{"id": "A", "text": "甲乙丙丁", "slot_s": 6.0},
                                                   {"id": "B", "text": "戊己庚辛", "slot_s": 6.0}]), encoding="utf-8")
        hear = lambda p: "甲乙丙丁" if "A_" in Path(p).name else "戊己庚辛"   # noqa: E731
        tts.generate_teacher(work, work / "句子.json", synth=lambda t, s, v: (_tone_s(4.0), SR), hear=hear,
                             check_similarity=False, use_pauses=False)
        copy = Path(d) / "複製到別處"
        shutil.copytree(work, copy)
        r = tts.generate_teacher(copy, copy / "句子.json", synth=_never, hear=hear, check_similarity=False,
                                 use_pauses=False)
        assert [x["id"] for x in r["句子"]] == ["A", "B"]


def test_legacy_attempt_cache_key_still_used():
    """10-01 以前的生成快取鍵（路徑#指紋）照樣沿用，跑到一半換新版程式不用重新生成。"""
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        ref = work / "參考音"
        ref.mkdir()
        sf.write(str(ref / "ref.wav"), np.zeros(SR, dtype=np.float32), SR)
        (ref / "ref.txt").write_text("參考", encoding="utf-8")
        (work / "句子.json").write_text(json.dumps([{"id": "A", "text": "甲乙丙丁", "slot_s": 6.0}]), encoding="utf-8")
        hear = lambda p: "甲乙丙丁"   # noqa: E731
        tts.generate_teacher(work, work / "句子.json", synth=lambda t, s, v: (_tone_s(4.0), SR), hear=hear,
                             check_similarity=False, phase="生成")
        od = tts.teacher_out_dir(work)
        cache = json.loads((od / tts.ATTEMPT_CACHE).read_text(encoding="utf-8"))
        fp = tts.ref_fingerprint(ref / "ref.wav")
        old = {k.replace(f"參考音#{fp}", f"{ref / 'ref.wav'}#{fp}"): v for k, v in cache.items()}
        assert old != cache
        (od / tts.ATTEMPT_CACHE).write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        tts.generate_teacher(work, work / "句子.json", synth=_never, hear=hear, check_similarity=False,
                             phase="生成")


# ---------- 10-03 第九批 #21：改回原文字或原聲線，不沿用被蓋掉的聲音檔 ----------

def _ref_work(d: Path) -> Path:
    work = Path(d)
    ref = work / "參考音"
    ref.mkdir()
    sf.write(str(ref / "ref.wav"), np.zeros(SR, dtype=np.float32), SR)
    (ref / "ref.txt").write_text("參考", encoding="utf-8")
    return work


def _len_synth(calls: list):
    """生成的長度跟字數有關：不同文字的聲音檔內容不一樣。"""
    def synth(text, seed, speed):
        calls.append(text)
        return np.zeros(int(len(text) * 0.25 * SR), dtype=np.float32) + 0.01, SR
    return synth


def test_text_a_b_a_regenerates_instead_of_reusing_overwritten_file():
    with tempfile.TemporaryDirectory() as d:
        work = _ref_work(Path(d))
        sp = work / "句子.json"
        now = {"text": ""}
        hear = lambda p: now["text"]   # noqa: E731
        calls: list = []
        for text in ("甲乙丙丁", "甲乙丙丁戊己庚辛", "甲乙丙丁"):
            now["text"] = text
            sp.write_text(json.dumps([{"id": "A", "text": text}], ensure_ascii=False), encoding="utf-8")
            r = tts.generate_teacher(work, sp, synth=_len_synth(calls), hear=hear, check_similarity=False,
                                     use_pauses=False, log=lambda s: None)["句子"][0]
            assert abs(_dur(work / r["檔案"]) - len(text) * 0.25) < 0.02, (text, _dur(work / r["檔案"]))
        assert calls == ["甲乙丙丁", "甲乙丙丁戊己庚辛", "甲乙丙丁"]   # 改回 A：重新生成，不沿用被 B 蓋掉的檔
        calls.clear()                                                   # 什麼都沒改的續跑（--redo）照樣沿用
        tts.generate_teacher(work, sp, synth=_len_synth(calls), hear=hear, check_similarity=False,
                             use_pauses=False, redo=True, log=lambda s: None)
        assert calls == []


def test_voice_m1_m2_m1_regenerates():
    with tempfile.TemporaryDirectory() as d:
        work = _ref_work(Path(d))
        od = work / "生成" / "學員"
        od.mkdir(parents=True)
        refs = {}
        for name, f0 in (("男1", 0.0), ("男2", 0.5)):
            refs[name] = work / f"{name}.wav"
            sf.write(str(refs[name]), np.zeros(SR, dtype=np.float32) + f0, SR)
        it = {"id": "S1", "text": "謝謝大家", "生成用文字": "謝謝大家", "發音對照": [], "slot": None, "slot_s": None}
        used = []
        for name in ("男1", "男2", "男1"):
            def synth(text, seed, speed, name=name):
                used.append(name)
                return np.zeros(int((1.0 if name == "男1" else 2.0) * SR), dtype=np.float32) + 0.01, SR
            done: dict = {}
            tts.run_generation(work, [dict(it)], od, refs[name], "參考", TOL, save=lambda: None, done=done,
                               role="學員", check_similarity=False, use_pauses=False, synth=synth,
                               hear=lambda p: "謝謝大家", log=lambda s: None)
            assert abs(_dur(work / done["S1"]["檔案"]) - (1.0 if name == "男1" else 2.0)) < 0.02, name
        assert used == ["男1", "男2", "男1"]


def test_legacy_cache_without_file_fingerprint():
    """09 月的快取沒記檔案指紋：這一句這一次只生成過一種才沿用；有過別種文字（檔案可能被蓋掉）就重新生成。"""
    with tempfile.TemporaryDirectory() as d:
        work = _ref_work(Path(d))
        sp = work / "句子.json"
        sp.write_text(json.dumps([{"id": "A", "text": "甲乙丙丁"}], ensure_ascii=False), encoding="utf-8")
        hear = lambda p: "甲乙丙丁"   # noqa: E731
        calls: list = []
        tts.generate_teacher(work, sp, synth=_len_synth(calls), hear=hear, check_similarity=False,
                             use_pauses=False, phase="生成", log=lambda s: None)
        cp = tts.teacher_out_dir(work) / tts.ATTEMPT_CACHE
        cache = json.loads(cp.read_text(encoding="utf-8"))
        legacy = {k: {f: v for f, v in e.items() if f not in ("檔案指紋", "參考音逐字稿指紋")} for k, e in cache.items()}
        cp.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        calls.clear()
        tts.generate_teacher(work, sp, synth=_len_synth(calls), hear=hear, check_similarity=False,
                             use_pauses=False, phase="生成", log=lambda s: None)
        assert calls == []                                       # 只有一種：沿用
        (k, e), = legacy.items()
        legacy[k.replace("甲乙丙丁", "別的文字")] = e             # 同一句第 1 次生成過別的文字
        cp.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        tts.generate_teacher(work, sp, synth=_len_synth(calls), hear=hear, check_similarity=False,
                             use_pauses=False, phase="生成", log=lambda s: None)
        assert calls == ["甲乙丙丁"]


def test_ref_text_changed_regenerates_attempt():
    with tempfile.TemporaryDirectory() as d:
        work = _ref_work(Path(d))
        sp = work / "句子.json"
        sp.write_text(json.dumps([{"id": "A", "text": "甲乙丙丁"}], ensure_ascii=False), encoding="utf-8")
        calls: list = []
        for _ in range(2):
            tts.generate_teacher(work, sp, synth=_len_synth(calls), hear=lambda p: "甲乙丙丁", check_similarity=False,
                                 use_pauses=False, log=lambda s: None)
        assert len(calls) == 1
        (work / "參考音" / "ref.txt").write_text("換了逐字稿", encoding="utf-8")   # 參考音檔一樣、逐字稿改了
        tts.generate_teacher(work, sp, synth=_len_synth(calls), hear=lambda p: "甲乙丙丁", check_similarity=False,
                             use_pauses=False, log=lambda s: None)
        assert len(calls) == 2


def test_pause_cache_redone_when_source_file_changed():
    if not shutil.which("ffmpeg"):
        return
    hear = lambda p: "甲乙丙丁戊己庚辛"   # noqa: E731
    with tempfile.TemporaryDirectory() as d:
        work = _pause_work(Path(d))
        sp = work / "句子.json"
        tts.generate_teacher(work, sp, synth=lambda t, s, v: (_tone_s(4.0), SR), hear=hear, check_similarity=False,
                             phase="生成", log=lambda s: None)
        tts.generate_teacher(work, sp, synth=_never, hear=hear, check_similarity=False, align=_pause_align([]),
                             phase="停頓", log=lambda s: None)
        src = tts.teacher_out_dir(work) / "P_第1次.wav"
        sf.write(str(src), _tone_s(3.0), SR)          # 這個檔被別次生成蓋掉（快取那邊也對不上，會先重新生成）
        tts.generate_teacher(work, sp, synth=lambda t, s, v: (_tone_s(4.2), SR), hear=hear, check_similarity=False,
                             phase="生成", log=lambda s: None)
        again: list = []
        tts.generate_teacher(work, sp, synth=_never, hear=hear, check_similarity=False, align=_pause_align(again),
                             phase="停頓", log=lambda s: None)
        assert again, "來源聲音檔換過，插入停頓要重做"


# ---------- 10-03 第九批 #20：停頓／收尾補做內容檢查，補出「不過」不讓整步失敗 ----------

def test_recheck_fail_in_later_programs_does_not_stop_step():
    def broken(path):
        raise ConnectionError("連不上")

    with tempfile.TemporaryDirectory() as d:
        work = _ref_work(Path(d))
        sp = work / "句子.json"
        sp.write_text(json.dumps([{"id": "A", "text": "甲乙丙丁"}, {"id": "B", "text": "戊己庚辛", "slot_s": 1.0}],
                                 ensure_ascii=False), encoding="utf-8")
        calls: list = []
        tts.generate_teacher(work, sp, synth=_len_synth(calls), hear=broken, check_similarity=False,
                             use_pauses=False, phase="生成", log=lambda s: None)
        assert len(calls) == 2
        # Groq 恢復了，但補做的檢查發現第 1 次沒念對；第 2 次（收尾換一種念法）念對了
        wrong_first = lambda p: "完全不對的內容" if "第1次" in Path(p).name else {"A": "甲乙丙丁", "B": "戊己庚辛"}[Path(p).name[0]]  # noqa: E731
        msgs: list = []
        out = tts.generate_teacher(work, sp, synth=_never, hear=wrong_first, check_similarity=False,
                                   use_pauses=False, phase="停頓", log=msgs.append)
        assert out == {} and any("補做的檢查沒過" in m for m in msgs), msgs
        calls.clear()
        r = {x["id"]: x for x in tts.generate_teacher(work, sp, synth=_len_synth(calls), hear=wrong_first,
                                                       check_similarity=False, use_pauses=False, phase="收尾",
                                                       log=lambda s: None)["句子"]}
        # 沒有時間格的 A：不重新生成，標要人聽；有時間格的 B：收尾那一支換一種念法再念，念對了
        assert r["A"]["要人聽"] and len(r["A"]["嘗試"]) == 1 and not r["A"]["嘗試"][0]["內容通過"]
        assert [a["種子"] for a in r["B"]["嘗試"]] == [42, 1] and r["B"]["選定"] == 2 and not r["B"]["要人聽"]
        assert calls == ["戊己庚辛"]


def test_recheck_fail_in_one_program_regenerates():
    def broken(path):
        raise ConnectionError("連不上")

    with tempfile.TemporaryDirectory() as d:
        work = _ref_work(Path(d))
        sp = work / "句子.json"
        sp.write_text(json.dumps([{"id": "A", "text": "甲乙丙丁"}], ensure_ascii=False), encoding="utf-8")
        calls: list = []
        tts.generate_teacher(work, sp, synth=_len_synth(calls), hear=broken, check_similarity=False,
                             use_pauses=False, log=lambda s: None)
        wrong_first = lambda p: "完全不對的內容" if "第1次" in Path(p).name else "甲乙丙丁"  # noqa: E731
        r = tts.generate_teacher(work, sp, synth=_len_synth(calls), hear=wrong_first, check_similarity=False,
                                 use_pauses=False, redo=True, log=lambda s: None)["句子"][0]
        assert len(calls) == 2 and r["選定"] == 2 and not r["要人聽"]   # 照一般「內容沒過」：換一種念法重念


# ---------- 10-03 第九批 #18：對位模型出錯不吞掉、不寫成「沒有停頓」的結果 ----------

def _two_pause_sentences(work: Path) -> Path:
    sp = work / "句子.json"
    sp.write_text(json.dumps([{"id": "P", "slot": [10.0, 16.0]}, {"id": "Q", "slot": [10.0, 16.0]}]), encoding="utf-8")
    return sp


def test_aligner_load_failure_not_cached_and_retried():
    if not shutil.which("ffmpeg"):
        return
    hear = lambda p: "甲乙丙丁戊己庚辛"   # noqa: E731
    loads = []

    class BrokenAligner:
        def __init__(self):
            loads.append(1)
            raise MemoryError("記憶體不夠")

    real = pauses.Aligner
    with tempfile.TemporaryDirectory() as d:
        work = _pause_work(Path(d))
        sp = _two_pause_sentences(work)
        tts.generate_teacher(work, sp, synth=lambda t, s, v: (_tone_s(4.0), SR), hear=hear, check_similarity=False,
                             phase="生成", log=lambda s: None)
        msgs: list = []
        pauses.Aligner = BrokenAligner
        try:
            tts.generate_teacher(work, sp, synth=_never, hear=hear, check_similarity=False, phase="停頓", log=msgs.append)
        finally:
            pauses.Aligner = real
        assert loads == [1], "載入失敗一次就好，不每句重載"
        assert any("對位模型載入失敗" in m and "MemoryError" in m for m in msgs), msgs
        pc = json.loads((tts.teacher_out_dir(work) / tts.PAUSE_CACHE).read_text(encoding="utf-8"))
        assert all("MemoryError" in (pc[k]["沒做成"] or "") for k in ("P", "Q")), pc
        # 收尾照樣做完（流程不停），紀錄看得出停頓沒做成、原因
        msgs.clear()
        tone = lambda t, s, v: (_tone_s(4.0 / v), SR)   # noqa: E731 — 沒插入停頓：收尾會改語速重生成
        recs = tts.generate_teacher(work, sp, synth=tone, hear=hear, check_similarity=False, align=_never,
                                    phase="收尾", log=msgs.append)["句子"]
        assert all("MemoryError" in r["停頓沒做成"] for r in recs), recs
        assert all("插入停頓" not in [v["版本"] for v in r["候選做法"]] for r in recs)
        assert any("沒照原片停頓" in m for m in msgs)
        # 下次這一步有執行：停頓沒做成的句子再做一次（聲音沿用，不重新生成），這次對位成功
        aligned: list = []
        tts.generate_teacher(work, sp, synth=_never, hear=hear, check_similarity=False, phase="生成", log=lambda s: None)
        tts.generate_teacher(work, sp, synth=_never, hear=hear, check_similarity=False, align=_pause_align(aligned),
                             phase="停頓", log=lambda s: None)
        assert aligned, "上次沒做成的要再試"
        recs = tts.generate_teacher(work, sp, synth=tone, hear=hear, check_similarity=False, align=_never,
                                    phase="收尾", log=lambda s: None)["句子"]
        assert all("停頓沒做成" not in r and r["建議做法"] == "插入停頓" for r in recs), recs


def test_align_error_in_one_program_recorded():
    if not shutil.which("ffmpeg"):
        return

    def bad_align(path, text):
        raise RuntimeError("模型檔不完整")

    with tempfile.TemporaryDirectory() as d:
        work = _pause_work(Path(d))
        r = tts.generate_teacher(work, work / "句子.json", synth=lambda t, s, v: (_tone_s(4.0), SR),
                                 hear=lambda p: "甲乙丙丁戊己庚辛", check_similarity=False, align=bad_align,
                                 log=lambda s: None)["句子"][0]
        assert "模型檔不完整" in r["停頓沒做成"] and r["放回時間格"]


def test_legacy_pause_cache_failure_retried():
    """10-03 以前的停頓快取沒有「沒做成」欄位：有原片聲音卻沒有原片停頓分析，就是當時出錯被吞掉，要再試。"""
    if not shutil.which("ffmpeg"):
        return
    hear = lambda p: "甲乙丙丁戊己庚辛"   # noqa: E731
    with tempfile.TemporaryDirectory() as d:
        work = _pause_work(Path(d))
        sp = work / "句子.json"
        tts.generate_teacher(work, sp, synth=lambda t, s, v: (_tone_s(4.0), SR), hear=hear, check_similarity=False,
                             phase="生成", log=lambda s: None)
        tts.generate_teacher(work, sp, synth=_never, hear=hear, check_similarity=False, align=_pause_align([]),
                             phase="停頓", log=lambda s: None)
        pp = tts.teacher_out_dir(work) / tts.PAUSE_CACHE
        pc = json.loads(pp.read_text(encoding="utf-8"))
        assert pc["P"]["沒做成"] is None
        ok = {k: v for k, v in pc["P"].items() if k != "沒做成"}
        pp.write_text(json.dumps({"P": ok}, ensure_ascii=False), encoding="utf-8")   # 舊的、成功的：沿用
        again: list = []
        tts.generate_teacher(work, sp, synth=_never, hear=hear, check_similarity=False, align=_pause_align(again),
                             phase="停頓", log=lambda s: None)
        assert again == []
        pp.write_text(json.dumps({"P": {**ok, "ctx": None, "插入停頓": None}}, ensure_ascii=False), encoding="utf-8")
        tts.generate_teacher(work, sp, synth=_never, hear=hear, check_similarity=False, align=_pause_align(again),
                             phase="停頓", log=lambda s: None)
        assert again, "舊快取裡被吞掉的錯誤要再試"


# ---------- 漏字、頭尾少念（10-03 第八批 #61、#104） ----------

def test_missing_run_blocks_even_when_similarity_high():
    text = "今天我想分享一下這週練習的心得，" * 6 + "其實我發現呼吸的時候比較能夠放鬆下來。" + "然後我們繼續往下看。" * 3
    heard = text.replace("其實我發現呼吸的時候比較能夠放鬆下來。", "")   # 整句不見
    assert tts.content_score(text, heard) >= tts.CONTENT_MIN
    problem, run = tts.content_problem(text, heard)
    assert problem == tts.MISSING_RUN and run >= 18
    # 只漏一兩個字、或聽成同音別字：不算漏一串
    assert tts.content_problem(text, text.replace("想分享", "想", 1))[0] is None
    assert tts.missing_run(text, text.replace("練習", "連息", 1)) == 0


def test_tail_missing_char_detected_homophones_and_fillers_ok():
    text = "我覺得這次的練習讓我在面對壓力的時候比較能夠停下來看看自己現在的感覺，真的很好"
    assert tts.content_score(text, text[:-1]) >= tts.CONTENT_MIN
    assert tts.content_problem(text, text[:-1])[0] == tts.TAIL_MISSING        # 最後一個字沒念
    assert tts.content_problem(text, text[:-1] + "號")[0] is None               # 同音別字（好／號）不算少念
    assert tts.content_problem(text + "啊", text)[0] is None                    # 結尾語助詞轉文字沒寫出來不算
    assert tts.content_problem(text, text + "。嗯")[0] is None                  # 轉文字多聽到一個語助詞也不算
    assert tts.content_problem(text, "我" + text)[0] is None                    # 開頭多一個字
    assert tts.content_problem(text, text[2:])[0] == tts.HEAD_MISSING           # 開頭兩個字沒念
    assert tts.content_problem("總共有三十個人", "總共有30個人")[0] is None     # 數字寫法不同不判斷
    assert tts.content_problem("好的", "")[0] is None                          # 太短的不做頭尾檢查


def test_tail_missing_retries_then_flags_for_listening():
    """結尾少念：照現有規則換種子重念；三次都少念 → 要人聽，原因寫「結尾可能少念了字」。"""
    with tempfile.TemporaryDirectory() as d:
        work = _ref_work(Path(d))
        text = "我覺得這次的練習讓我在面對壓力的時候比較能夠停下來，真的很好"
        sp = work / "句子.json"
        sp.write_text(json.dumps([{"id": "A", "text": text}], ensure_ascii=False), encoding="utf-8")
        calls: list = []
        log = tts.generate_teacher(work, sp, synth=_len_synth(calls), hear=lambda p: text[:-1],
                                   check_similarity=False, use_pauses=False, log=lambda s: None)
        r = log["句子"][0]
        assert len(calls) == tts.MAX_ATTEMPTS and r["要人聽"] and r["內容問題"] == tts.TAIL_MISSING
        assert all(a["內容問題"] == tts.TAIL_MISSING and not a["內容通過"] for a in r["嘗試"])

        # 第二次念對了：通過、沒有內容問題
        (Path(d) / "二").mkdir()
        work2 = _ref_work(Path(d) / "二")
        sp2 = work2 / "句子.json"
        sp2.write_text(json.dumps([{"id": "A", "text": text}], ensure_ascii=False), encoding="utf-8")
        heard = iter([text[:-1], text])
        r = tts.generate_teacher(work2, sp2, synth=_len_synth([]), hear=lambda p: next(heard),
                                 check_similarity=False, use_pauses=False, log=lambda s: None)["句子"][0]
        assert r["選定"] == 2 and not r["要人聽"] and "內容問題" not in r


def test_old_cache_rechecked_with_new_rules_without_regenerating_same_attempt():
    """舊快取（沒有內容問題欄位）沿用時照新規則重新判斷：結尾少念的那一次不算通過，換種子再念；那一次本身不重新生成。"""
    with tempfile.TemporaryDirectory() as d:
        work = _ref_work(Path(d))
        text = "我覺得這次的練習讓我在面對壓力的時候比較能夠停下來，真的很好"
        sp = work / "句子.json"
        sp.write_text(json.dumps([{"id": "A", "text": text}], ensure_ascii=False), encoding="utf-8")
        calls: list = []
        tts.generate_teacher(work, sp, synth=_len_synth(calls), hear=lambda p: text, check_similarity=False,
                             use_pauses=False, log=lambda s: None)
        cp = tts.teacher_out_dir(work) / tts.ATTEMPT_CACHE
        cache = json.loads(cp.read_text(encoding="utf-8"))
        for v in cache.values():
            v["heard"] = text[:-1]
            v.pop("problem", None)
            v.pop("missing", None)
        cp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        calls.clear()
        r = tts.generate_teacher(work, sp, synth=_len_synth(calls), hear=lambda p: text, check_similarity=False,
                                 use_pauses=False, redo=True, log=lambda s: None)["句子"][0]
        assert len(calls) == 1 and r["選定"] == 2 and r["嘗試"][0]["內容問題"] == tts.TAIL_MISSING


# ---------- 聲音檔不在當作沒做過（10-03 第九批 #19，全面檢查 C5） ----------

def test_output_missing_checks_chosen_and_fitted_files():
    with tempfile.TemporaryDirectory() as d:
        w = Path(d)
        (w / "生成").mkdir()
        rec = {"檔案": "生成/A.wav", "放回時間格": {"檔案": "生成/A_放回時間格.wav", "來源檔案": "生成/A_第1次.wav"}}
        assert tts.output_missing(rec, w)
        for f in ("A.wav", "A_放回時間格.wav", "A_第1次.wav"):
            (w / "生成" / f).write_bytes(b"RIFF")
        assert not tts.output_missing(rec, w)
        (w / "生成" / "A_第1次.wav").unlink()
        assert tts.output_missing(rec, w)          # 停格補長用的來源檔不在也不行
        assert not tts.output_missing(None, w)     # 沒有紀錄：交給 record_stale
        it = {"id": "A", "text": "甲"}
        assert not tts.needs_work({**rec, "text": "甲"}, it) and tts.needs_work({**rec, "text": "甲"}, it, workdir=w)


def test_deleted_wavs_regenerated_reusing_attempt_cache():
    """清硬碟刪了選定檔、放回時間格的檔：再跑一次會補回來（嘗試快取的聲音還在就沿用、不重新生成）；
    連每一次生成的檔都刪了才真的重新生成。"""
    with tempfile.TemporaryDirectory() as d:
        work = _ref_work(Path(d))
        text = "這個是我們今天課程的重點之一。"
        sp = work / "句子.json"
        sp.write_text(json.dumps([{"id": "A", "text": text, "slot": [10.0, 10.0 + len(text) * 0.25]}],
                                 ensure_ascii=False), encoding="utf-8")
        calls: list = []
        q = dict(hear=lambda p: text, check_similarity=False, use_pauses=False, log=lambda s: None)
        r = tts.generate_teacher(work, sp, synth=_len_synth(calls), **q)["句子"][0]
        assert len(calls) == 1
        chosen, fitted = work / r["檔案"], work / r["放回時間格"]["檔案"]
        chosen.unlink()
        fitted.unlink()
        calls.clear()
        r = tts.generate_teacher(work, sp, synth=_len_synth(calls), **q)["句子"][0]
        assert calls == [] and chosen.is_file() and fitted.is_file()     # 沿用快取，檔案補回來了
        for f in tts.teacher_out_dir(work).glob("*.wav"):
            f.unlink()
        tts.generate_teacher(work, sp, synth=_len_synth(calls), **q)
        assert calls == [text] and chosen.is_file() and fitted.is_file()  # 快取的聲音也不在：重新生成


# ---------- 10-04 #109：念對沒有的檢查沒做成，訊息依原因說清楚 ----------

def test_check_fail_message_says_why():
    """Groq 額度用完（429）不再寫「多半是網路」；連不上才寫；其他錯誤不猜。不連網：例外是自己建的。"""
    import httpx
    import groq

    from bookclub.refpick import GroqQuotaExhausted

    req = httpx.Request("POST", "https://example.invalid/x")
    cases = [(groq.RateLimitError("rate", response=httpx.Response(429, request=req), body=None), "額度用完或被限流"),
             (GroqQuotaExhausted("Groq 額度用完了"), "額度用完或被限流"),
             (groq.APIConnectionError(request=req), "連不上 Groq"),
             (groq.APITimeoutError(request=req), "連不上 Groq"),
             (ConnectionError("連不上"), "連不上 Groq"),
             (ValueError("壞掉"), "不是網路或額度的問題")]
    for exc, want in cases:
        with tempfile.TemporaryDirectory() as d:
            work = _ref_work(Path(d))
            sp = work / "句子.json"
            sp.write_text(json.dumps([{"id": "A", "text": "甲乙丙丁"}], ensure_ascii=False), encoding="utf-8")

            def broken(path, exc=exc):
                raise exc

            msgs: list = []
            r = tts.generate_teacher(work, sp, synth=_len_synth([]), hear=broken, check_similarity=False,
                                     use_pauses=False, log=msgs.append)["句子"][0]
            lines = [m for m in msgs if "念對沒有的檢查沒做成" in m]
            assert len(lines) == 1 and want in lines[0], (type(exc).__name__, lines)
            assert ("多半是網路" in lines[0]) == (want == "連不上 Groq"), lines
            assert r["要人聽"] and r["嘗試"][0]["內容檢查沒做成"]          # 流程照舊：標要人聽、生成照常往下


if __name__ == "__main__":
    sys.exit(_run_all())
