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

from bookclub import fit, tts

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
        assert len(b["嘗試"]) == 1 and b["放回時間格"]["做法"] == fit.PAD
        assert (work / "生成" / "老師" / "A.wav").is_file()
        if shutil.which("ffmpeg"):
            assert abs(_dur(work / b["放回時間格"]["檔案"]) - slot_b) < 0.02
        assert log["統計"]["生成次數"] == 3

        # 續跑：沒改的句子不重生成
        synth2 = FakeSynth()
        tts.generate_teacher(work, sp, synth=synth2, hear=_fake_hear_factory(synth2, sentences),
                             check_similarity=False)
        assert synth2.calls == []


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
