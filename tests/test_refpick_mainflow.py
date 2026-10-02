"""10-02 第七批（B1＋B5）：挑參考音改讀主流程的逐字稿，不再整支送 Groq 第二次、不覆寫說話者判斷.json。

全部用合成資料：假影片（電子音）、假逐字稿、假的聲紋模型（看時間回傳老師／學員的向量）、
假的 Claude（段落分析、建議刪除、人名清單）、假的候選轉文字。不打網路、不載入任何模型。

獨立可跑：.venv/bin/python tests/test_refpick_mainflow.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from bookclub import refpick  # noqa: E402

SR = 16000
DUR = 300.0
SENT_S, GAP_S = 3.6, 0.4
# (起, 訖, 誰, 內容類型)；老師連續講話要夠長（候選區域 30–75 秒、不能碰到學員前後 3 秒）
LAYOUT = [(0, 20, "老師", "冥想引導"), (20, 120, "老師", "講解"), (120, 140, "學員", "學員分享"),
          (140, 240, "老師", "講解"), (240, 260, "學員", "學員分享"), (260, 300, "老師", "提問與回應")]
E_TEACHER = np.array([1.0, 0, 0, 0, 0, 0, 0, 0])
E_STUDENT = np.array([0, 1.0, 0, 0, 0, 0, 0, 0])


def _who(t: float) -> tuple[str, str]:
    for a, b, w, k in LAYOUT:
        if a <= t < b:
            return w, k
    return "老師", "講解"


def _voice_vec(a: float, b: float) -> np.ndarray:
    mid = (a + b) / 2
    who, kind = _who(mid)
    if who == "老師" and kind == "冥想引導" and int(mid // (SENT_S + GAP_S)) % 2:
        return E_STUDENT.copy()   # 冥想引導時聲紋誤判（合併判斷會改回老師）
    return (E_TEACHER if who == "老師" else E_STUDENT).copy()


class _FakeInference:
    def crop(self, path, seg):
        return _voice_vec(seg.start, seg.end)


def _merged() -> dict:
    """主流程格式的逐字稿：句子編號 0000_000，每句 4 個字平均排開（字跟字之間 0.05 秒）。"""
    sents, words = [], []
    k = 0
    t = 0.0
    while t + SENT_S <= DUR:
        tag = f"{int(t // 60):04d}"
        who, _ = _who(t)
        sents.append({"id": f"{tag}_{k:03d}", "start": round(t, 3), "end": round(t + SENT_S, 3),
                      "text": f"{'老師' if who == '老師' else '學員'}第{k}句話。", "avg_logprob": -0.2})
        for j in range(4):
            a = t + 0.1 + j * 0.85
            words.append({"word": "字字", "start": round(a, 3), "end": round(a + 0.8, 3)})
        k += 1
        t += SENT_S + GAP_S
    return {"source": "假影片.wav", "duration": DUR, "sentences": sents, "words": words, "silence_map": [], "elapsed": {}}


def _make_audio(path: Path, sr: int = SR) -> None:
    n = int(DUR * sr)
    t = np.arange(n) / sr
    x = np.zeros(n, dtype=np.float32)
    for s in _merged()["sentences"]:
        a, b = int(s["start"] * sr), int(s["end"] * sr)
        f = 220.0 if _who(s["start"])[0] == "老師" else 440.0
        x[a:b] = 0.2 * np.sin(2 * np.pi * f * t[a:b])
    sf.write(str(path), x, sr)


def _text_turns(sentences: list[dict]) -> dict:
    turns = []
    for a, b, who, kind in LAYOUT:
        idx = [i for i, s in enumerate(sentences) if a <= s["start"] < b]
        if idx:
            turns.append({"起": idx[0], "迄": idx[-1], "說話者": who, "學員編號": "S1" if who == "學員" else None,
                          "內容類型": kind, "句子": [sentences[i]["id"] for i in idx]})
    return {"段落": turns, "句數": len(sentences), "模型": "假的", "文字判斷秒": 0}


def test_split_short_sentences_pure():
    """自己編的字與時間：停頓超過 0.3 秒斷句、一句超過 4 秒在下一個字前斷；文字是字接起來。"""
    words = [{"word": "我", "start": 0.0, "end": 0.3}, {"word": "們", "start": 0.35, "end": 0.6},
             {"word": "開始", "start": 1.2, "end": 1.6},                       # 前面空 0.6 秒 → 斷
             {"word": "一", "start": 1.65, "end": 2.6}, {"word": "二", "start": 2.65, "end": 3.6},
             {"word": "三", "start": 3.65, "end": 4.6}, {"word": "四", "start": 4.65, "end": 5.6},  # 超過 4 秒 → 斷
             {"word": "五", "start": 5.65, "end": 6.0}]
    out = refpick.split_short_sentences(words)
    assert [(s["start"], s["end"], s["text"]) for s in out] == [
        (0.0, 0.6, "我們"), (1.2, 4.6, "開始一二三"), (4.65, 6.0, "四五")], out
    assert [s["id"] for s in out] == ["R00000", "R00001", "R00002"]
    assert refpick.split_short_sentences([]) == []
    # 對到說話者判斷：重疊最多的那一句；一句都沒對到的標「太短」
    judged = [{"id": "0000_000", "start": 0.0, "end": 1.0, "label": "不是老師", "sim": 0.1, "avg_logprob": -0.3},
              {"id": "0000_001", "start": 1.0, "end": 5.0, "label": "老師", "sim": 0.8, "avg_logprob": -0.2}]
    lab = refpick.label_short_sentences(out + [{"id": "R9", "start": 9.0, "end": 9.5, "text": "x"}], judged)
    assert [(r["label"], r.get("對到")) for r in lab] == [("不是老師", "0000_000"), ("老師", "0000_001"),
                                                         ("老師", "0000_001"), ("太短", None)]
    assert lab[1]["sim"] == 0.8 and lab[1]["avg_logprob"] == -0.2
    assert refpick.is_main_flow_speakers({"sentences": judged})
    assert not refpick.is_main_flow_speakers({"sentences": [{"id": "00_000"}]})


def test_step3_does_not_write_when_told():
    """單獨跑 ref pick、說話者判斷.json 是主流程的：只算不寫。"""
    with tempfile.TemporaryDirectory() as d:
        w = Path(d)
        _make_audio(w / "audio.flac")
        cache = w / "說話者判斷.json"
        cache.write_text(json.dumps({"sentences": [{"id": "0000_000", "start": 0, "end": 1, "text": "x"}],
                                     "cluster_info": {}}, ensure_ascii=False), encoding="utf-8")
        before = cache.read_bytes()
        old = refpick._load_embed_model
        refpick._load_embed_model = lambda: _FakeInference()
        try:
            sents = [{"id": f"00_{k:03d}", "start": s["start"], "end": s["end"], "text": s["text"], "avg_logprob": -0.2}
                     for k, s in enumerate(_merged()["sentences"])]
            out, info, _ = refpick._step3_speaker_classify(w / "audio.flac", w, sents, write_cache=False)
        finally:
            refpick._load_embed_model = old
        assert cache.read_bytes() == before and len(out) == len(sents) and info["老師聲紋中心"]


def test_analyze_from_scratch_keeps_main_flow_speakers():
    """假資料從零跑 analyze 的順序：跑完說話者判斷.json 的句子編號、筆數跟 merged.json 一致，
    合併判斷的欄位還在；段落分析拿到的是主流程的句子；整支第二次轉文字沒被呼叫、transcript/ 沒有 00.json；
    參考音有候選。"""
    from bookclub import analyze, cutsuggest, personnames, students, roomtone
    from bookclub import turns as turns_mod

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        video = root / "假影片.wav"
        _make_audio(video)
        w = root / "工作區"
        (w / "transcript").mkdir(parents=True)
        merged = _merged()
        (w / "transcript" / "merged.json").write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
        _make_audio(w / "audio.flac")

        seen: dict = {}

        def fake_build_turns(workdir, roster_path=None, text=None, **kw):
            data = json.loads((Path(workdir) / "說話者判斷.json").read_text(encoding="utf-8"))
            seen["段落用的句子"] = [s["id"] for s in data["sentences"]]
            return {"統計": {"段落數": len(text["段落"]), "學員人數": 1}}

        def no_second_transcribe(*a, **k):
            raise AssertionError("有 merged.json 時不能整支再轉一次")

        patches = [
            (refpick, "_load_embed_model", lambda: _FakeInference()),
            (refpick, "_step2_transcribe", no_second_transcribe),
            (refpick, "_transcribe_candidate_text", lambda client, wav, data=None: "假的逐字稿初稿"),
            (refpick, "GROQ_CALL_INTERVAL_S", 0.0),
            (turns_mod, "get_text_turns", lambda workdir, sentences, **k: _text_turns(sentences)),
            (turns_mod, "build_turns", fake_build_turns),
            (cutsuggest, "suggest_cuts", lambda *a, **k: {"建議": []}),
            (personnames, "find_people", lambda *a, **k: {"統計": {"名字數": 0, "名冊上沒有": 0}}),
            (students, "estimate_pitches", lambda *a, **k: None),
            (roomtone, "ensure_info", lambda *a, **k: None),
        ]
        olds = [(m, n, getattr(m, n)) for m, n, _ in patches]
        old_key = os.environ.get("GROQ_API_KEY")
        os.environ["GROQ_API_KEY"] = "gsk_假的金鑰"   # Groq() 建立時要有；候選轉文字是假的，不會真的連線
        try:
            for m, n, v in patches:
                setattr(m, n, v)
            result = analyze.run_analyze(video, w, skip_overlap=True, ref_n=3)
        finally:
            for m, n, v in olds:
                setattr(m, n, v)
            if old_key is None:
                os.environ.pop("GROQ_API_KEY", None)
            else:
                os.environ["GROQ_API_KEY"] = old_key

        sp = json.loads((w / "說話者判斷.json").read_text(encoding="utf-8"))
        ids = [s["id"] for s in merged["sentences"]]
        assert [s["id"] for s in sp["sentences"]] == ids and len(sp["sentences"]) == len(ids)
        assert sp.get("文字修正") and sp["文字修正"]["改成老師句數"] > 0              # 合併判斷的欄位還在
        assert all("聲紋判斷" in s and "判斷依據" in s for s in sp["sentences"])
        assert any(s["判斷依據"].startswith("文字") for s in sp["sentences"])
        assert seen["段落用的句子"] == ids
        assert not (w / "transcript" / "00.json").exists()
        rec = json.loads((w / "參考音" / "挑選紀錄.json").read_text(encoding="utf-8"))
        assert rec["逐字稿來源"].startswith("主流程") and rec["候選數"] >= 1 and result["參考音候選數"] == rec["候選數"]
        cands = json.loads((w / "參考音" / "候選.json").read_text(encoding="utf-8"))
        chosen = [c for c in cands if c["狀態"] == "入選"]
        for c in chosen:   # 候選不碰學員、不碰冥想引導
            a, b = c.get("used_start", c.get("region_start")), c.get("used_end", c.get("region_end"))
            assert all(not (a < y and x < b) for x, y, who, kind in
                       [(x, y, who, kind) for x, y, who, kind in LAYOUT if who == "學員" or kind == "冥想引導"]), c
        # 再挑一次（例如參考音資料夾改名後）：說話者判斷.json 一樣不動
        before = (w / "說話者判斷.json").read_bytes()
        (w / "參考音").rename(w / "參考音_舊")
        for m, n, v in patches:
            setattr(m, n, v)
        os.environ["GROQ_API_KEY"] = "gsk_假的金鑰"
        try:
            refpick.pick_reference(video, w, n=2)
        finally:
            for m, n, v in olds:
                setattr(m, n, v)
            if old_key is None:
                os.environ.pop("GROQ_API_KEY", None)
            else:
                os.environ["GROQ_API_KEY"] = old_key
        assert (w / "說話者判斷.json").read_bytes() == before


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
