"""bookclub/tts.py、bookclub/fit.py 的單元測試：不載入 CosyVoice、不打 Groq，
生成與轉回文字都用假的函式代替，測重試策略、續跑、放回時間格。

獨立可跑（不需要 pytest）：.venv/bin/python tests/test_tts.py
裝了 pytest 的話也可以：.venv/bin/python -m pytest tests/test_tts.py
"""

from __future__ import annotations

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


if __name__ == "__main__":
    sys.exit(_run_all())
