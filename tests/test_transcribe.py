"""bookclub/transcribe.py 的單元測試：只測純數字運算的部分（切塊、時間換算），
不碰音檔、VAD 模型或 Groq。

獨立可跑：.venv/bin/python tests/test_transcribe.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 10-01：這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import transcribe as tc


def test_group_into_chunks_splits_on_max_seconds():
    # 人聲時長各 5 秒：前兩段合起來 10 秒 <= 12 秒上限，第三段會讓總長超過，另起一塊
    segs = [{"start": 0.0, "end": 5.0}, {"start": 5.5, "end": 10.5}, {"start": 11.0, "end": 16.0}]
    chunks = tc._group_into_chunks(segs, max_seconds=12.0)
    assert len(chunks) == 2
    assert chunks[0] == segs[:2]
    assert chunks[1] == segs[2:]


def test_group_into_chunks_single_chunk_when_short():
    segs = [{"start": 0.0, "end": 5.0}, {"start": 5.5, "end": 9.0}]
    chunks = tc._group_into_chunks(segs, max_seconds=100.0)
    assert len(chunks) == 1
    assert chunks[0] == segs


def test_group_into_chunks_empty_input():
    assert tc._group_into_chunks([], max_seconds=100.0) == []


def test_compute_mapping_concat_offsets():
    segs = [{"start": 10.0, "end": 12.0}, {"start": 20.0, "end": 21.0}]
    mapping = tc._compute_mapping(segs, keep_pause=0.0)
    assert mapping[0] == {"concat_start": 0.0, "concat_end": 2.0, "original_start": 10.0, "pause": 0.0}
    assert mapping[1] == {"concat_start": 2.0, "concat_end": 3.0, "original_start": 20.0, "pause": 0.0}


def test_map_time_to_original_start_prefers_next_segment_after_gap():
    segs = [{"start": 10.0, "end": 12.0}, {"start": 20.0, "end": 21.0}]
    mapping = tc._compute_mapping(segs, keep_pause=0.0)
    # concat 時間 2.0 剛好在接縫上：start 語意歸給後一段開頭（原始時間 20.0）
    t = tc._map_time_to_original(mapping, 2.0, prefer="start")
    assert abs(t - 20.0) < 1e-6


def test_map_time_to_original_end_prefers_previous_segment_before_gap():
    segs = [{"start": 10.0, "end": 12.0}, {"start": 20.0, "end": 21.0}]
    mapping = tc._compute_mapping(segs, keep_pause=0.0)
    # concat 時間 2.0 剛好在接縫上：end 語意歸給前一段結尾（原始時間 12.0）
    t = tc._map_time_to_original(mapping, 2.0, prefer="end")
    assert abs(t - 12.0) < 1e-6


def test_map_time_to_original_within_segment():
    segs = [{"start": 10.0, "end": 12.0}, {"start": 20.0, "end": 21.0}]
    mapping = tc._compute_mapping(segs, keep_pause=0.0)
    t = tc._map_time_to_original(mapping, 0.5, prefer="start")
    assert abs(t - 10.5) < 1e-6
    t2 = tc._map_time_to_original(mapping, 2.5, prefer="end")
    assert abs(t2 - 20.5) < 1e-6


def test_map_time_to_original_out_of_range_clamped():
    segs = [{"start": 10.0, "end": 12.0}]
    mapping = tc._compute_mapping(segs, keep_pause=0.0)
    t = tc._map_time_to_original(mapping, 5.0, prefer="end")  # 超出總長 2.0 秒很多
    assert t >= 10.0  # 夾到最後一段，不會噴錯或給負值


def test_build_prompt_without_roster():
    prompt = tc._build_prompt(None)
    assert "今天提到的學員" not in prompt


def test_build_prompt_with_roster():
    prompt = tc._build_prompt(["詩涵", "欣欣"])
    assert "詩涵" in prompt and "欣欣" in prompt
    assert "今天提到的學員" in prompt


# ---------- 10-04 #62：段跟段之間補空白（最多 1 秒）再送 Groq ----------

SEGS = [{"start": 10.0, "end": 12.0}, {"start": 12.5, "end": 13.5}, {"start": 20.0, "end": 21.0}]
# 間隔 0.5 秒 → 補 0.5；間隔 6.5 秒 → 補 1.0（上限）；最後一段不補


def test_pause_samples_capped_at_keep_pause():
    pads = tc._pause_samples(SEGS, keep_pause=1.0)
    assert pads == [int(0.5 * tc.SR), int(1.0 * tc.SR), 0]
    assert tc.KEEP_PAUSE_S == 1.0


def test_compute_mapping_pause_belongs_to_previous_segment():
    m = tc._compute_mapping(SEGS, keep_pause=1.0)
    assert [round(x["concat_start"], 3) for x in m] == [0.0, 2.5, 4.5]
    assert [round(x["concat_end"], 3) for x in m] == [2.5, 4.5, 5.5]
    assert [round(x["pause"], 3) for x in m] == [0.5, 1.0, 0.0]


def _quiet(t: float) -> bool:
    """原片時間 t 是不是在人聲片段之外（VAD 判定的安靜處）。"""
    return not any(s["start"] + 1e-6 < t < s["end"] - 1e-6 for s in SEGS)


def test_time_inside_pause_maps_to_original_quiet_not_chunk_end():
    m = tc._compute_mapping(SEGS, keep_pause=1.0)
    # 補的空白裡的時間：第一段空白（接起來 2.0–2.5）、第二段空白（3.5–4.5）
    for t in (2.1, 2.3, 2.49, 3.6, 4.0, 4.49):
        for prefer in ("start", "end"):
            o = tc._map_time_to_original(m, t, prefer=prefer)
            assert _quiet(o), (t, prefer, o)
            assert o < 20.0, f"空白裡的時間不能跑到後面的段落或整塊最後：{t} → {o}"
    assert abs(tc._map_time_to_original(m, 4.0, prefer="end") - 14.0) < 1e-6   # 13.5 之後 0.5 秒，原片安靜處
    # 人聲裡的時間照舊
    assert abs(tc._map_time_to_original(m, 1.0, prefer="start") - 11.0) < 1e-6
    assert abs(tc._map_time_to_original(m, 3.0, prefer="start") - 13.0) < 1e-6
    assert abs(tc._map_time_to_original(m, 5.0, prefer="start") - 20.5) < 1e-6


def test_no_time_maps_past_chunk_end_and_sentences_stay_short():
    """10-03 實驗的錯：空白沒算進對應表，後面的時間全部往後錯、超出的外插到整塊最後，句子最長 1034 秒。"""
    m = tc._compute_mapping(SEGS, keep_pause=1.0)
    total = m[-1]["concat_end"]
    assert abs(total - 5.5) < 1e-6
    for k in range(0, 80):
        t = k * 0.1
        for prefer in ("start", "end"):
            assert tc._map_time_to_original(m, t, prefer=prefer) <= 21.0 + 1e-6
    # 每一句（接起來的時間 0.5 秒長）換算回去不超過「這一段＋空白」的長度，除非真的跨過挖掉的長停頓
    for k in range(0, 50):
        a = k * 0.1
        b = min(total, a + 0.5)
        oa = tc._map_time_to_original(m, a, prefer="start")
        ob = tc._map_time_to_original(m, b, prefer="end")
        assert ob - oa <= 0.5 + (20.0 - 14.5) + 1e-6   # 最多跨過一次挖掉的 5.5 秒


def test_build_chunk_audio_inserts_silence_matching_mapping():
    audio = np.ones(25 * tc.SR, dtype="float32") * 0.5
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "c.flac"
        tc._build_chunk_audio(audio, SEGS, out, keep_pause=1.0)
        x, sr = sf.read(str(out), dtype="float32")
    m = tc._compute_mapping(SEGS, keep_pause=1.0)
    assert sr == tc.SR
    assert abs(len(x) / sr - m[-1]["concat_end"]) < 2 / sr
    # 2.0–2.5、3.5–4.5 是補的空白（0），其他是原片
    assert np.abs(x[int(2.05 * sr):int(2.45 * sr)]).max() < 1e-3
    assert np.abs(x[int(3.55 * sr):int(4.45 * sr)]).max() < 1e-3
    assert x[int(1.0 * sr)] > 0.4 and x[int(3.0 * sr)] > 0.4 and x[int(5.0 * sr)] > 0.4


def test_map_word_across_cut_silence_stays_on_one_side():
    """10-04 #105：字橫跨接縫、接縫那裡有挖掉的靜音 → 字不能把挖掉的靜音包進去。"""
    m = tc._compute_mapping(SEGS, keep_pause=1.0)
    # 接起來 4.3–4.7：前面 0.2 秒在第二段的空白裡、後面 0.2 秒在第三段 → 原片 13.5+0.8 … 20.2，會包進 5.5 秒挖掉的靜音
    s, e = tc._map_word(m, 4.3, 4.8)
    assert e - s <= 0.5 + 1e-6
    assert abs(s - 20.0) < 1e-6 and abs(e - 20.3) < 1e-6   # 後一段佔比較多 → 留在後一段
    s, e = tc._map_word(m, 4.1, 4.6)
    assert e - s <= 0.5 + 1e-6 and e <= 14.5 + 1e-6        # 前一段（含空白）佔比較多 → 留在前一段
    # 接縫沒有挖掉東西（間隔 0.5 秒整段補回去）→ 照舊
    s, e = tc._map_word(m, 2.3, 2.7)
    assert abs(s - 12.3) < 1e-6 and abs(e - 12.7) < 1e-6


# ---------- 跨過被挖掉的長停頓就斷句 ----------

def _w(word, a, b):
    return {"word": word, "start": a, "end": b}


def test_split_at_long_pauses_splits_and_renumbers():
    sents = [
        {"id": "0000_000", "start": 0.0, "end": 9.0, "text": "我們先坐好，然後深呼吸。", "avg_logprob": -0.2},
        {"id": "0000_001", "start": 9.5, "end": 11.0, "text": "很好", "avg_logprob": -0.3},
        {"id": "0001_000", "start": 30.0, "end": 31.0, "text": "下一塊", "avg_logprob": -0.1},
    ]
    words = [_w("我們", 0.0, 0.4), _w("先", 0.4, 0.6), _w("坐好", 0.6, 1.0),
             _w("然後", 6.0, 6.4), _w("深呼吸", 6.4, 7.0), _w("很好", 9.5, 10.0), _w("下一塊", 30.0, 31.0)]
    silence = [{"start": 1.2, "end": 5.8}, {"start": 7.2, "end": 9.4}]   # 第二個 2.2 秒但不在句子裡面
    out, st = tc.split_at_long_pauses(sents, words, silence)
    assert [s["id"] for s in out] == ["0000_000", "0000_001", "0000_002", "0001_000"]
    assert out[0]["text"] == "我們先坐好，" and out[1]["text"] == "然後深呼吸。"
    assert out[0]["end"] == 1.2 and out[1]["start"] == 5.8
    assert out[1]["avg_logprob"] == -0.2
    assert st["切開"] == 1


def test_split_at_long_pauses_keeps_short_pause():
    sents = [{"id": "0000_000", "start": 0.0, "end": 4.0, "text": "一二三四"}]
    words = [_w("一二", 0.0, 1.0), _w("三四", 2.4, 4.0)]
    out, st = tc.split_at_long_pauses(sents, words, [{"start": 1.0, "end": 2.4}])   # 1.4 秒 < 1.6
    assert len(out) == 1 and st["切開"] == 0
    assert tc.LONG_PAUSE_SPLIT_S == tc.KEEP_PAUSE_S + 0.6


def test_split_at_long_pauses_one_side_words_shrinks_edge():
    sents = [{"id": "0000_000", "start": 0.0, "end": 8.0, "text": "好"}]
    words = [_w("好", 7.0, 7.5)]
    out, st = tc.split_at_long_pauses(sents, words, [{"start": 0.5, "end": 6.8}])
    assert len(out) == 1 and out[0]["start"] == 6.8 and st["縮邊"] == 1


def test_split_at_long_pauses_text_mismatch_not_split():
    sents = [{"id": "0000_000", "start": 0.0, "end": 8.0, "text": "短"}]
    words = [_w("一二三", 0.0, 1.0), _w("四", 7.0, 7.5)]
    out, st = tc.split_at_long_pauses(sents, words, [{"start": 1.2, "end": 6.8}])
    assert len(out) == 1 and out[0]["text"] == "短" and st["對不上"] == 1


# ---------- 舊工作區不重轉、舊塊級快取照舊換算 ----------

def test_existing_merged_json_is_reused_without_vad_or_groq():
    orig_extract, orig_vad = tc.extract_audio, tc._run_vad

    def boom(*a, **k):
        raise AssertionError("merged.json 存在時不能重抽音、重跑 VAD")

    tc.extract_audio = tc._run_vad = boom
    try:
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            (w / "transcript").mkdir()
            old = {"duration": 10.0, "sentences": [{"id": "0000_000", "start": 0.0, "end": 9.0, "text": "x"}],
                   "words": [], "silence_map": []}
            (w / "transcript" / "merged.json").write_text(json.dumps(old), encoding="utf-8")
            got = tc.transcribe(w / "沒有這支影片.mp4", w)
            assert got["sentences"] == old["sentences"] and "保留停頓秒數" not in got
    finally:
        tc.extract_audio, tc._run_vad = orig_extract, orig_vad


def _fake_run(w: Path, chunk_result: dict) -> dict:
    """假的抽音／VAD，塊級快取已經在 → 不呼叫 Groq，跑完整個合併流程。"""
    audio = np.zeros(25 * tc.SR, dtype="float32")
    sil = [{"start": 0.0, "end": 10.0}, {"start": 12.0, "end": 12.5}, {"start": 13.5, "end": 20.0},
           {"start": 21.0, "end": 25.0}]
    orig_extract, orig_vad = tc.extract_audio, tc._run_vad
    tc.extract_audio = lambda video, workdir: (w / "audio.flac", 0.0)
    tc._run_vad = lambda f: (audio, [dict(s) for s in SEGS], sil, 25.0)
    try:
        (w / "transcript").mkdir(exist_ok=True)
        (w / "transcript" / "chunk_0000.json").write_text(json.dumps(chunk_result), encoding="utf-8")
        return tc.transcribe(w / "v.mp4", w)
    finally:
        tc.extract_audio, tc._run_vad = orig_extract, orig_vad


def test_transcribe_new_chunk_maps_pause_and_splits_long_pause():
    res = {tc.CACHE_PAUSE_KEY: 1.0,
           "words": [{"word": "大家", "start": 0.0, "end": 1.0}, {"word": "好", "start": 1.0, "end": 2.0},
                     {"word": "我們", "start": 2.5, "end": 3.4}, {"word": "開始", "start": 4.6, "end": 5.4}],
           "segments": [{"start": 0.0, "end": 5.5, "text": "大家好我們開始", "avg_logprob": -0.2}]}
    with tempfile.TemporaryDirectory() as d:
        got = _fake_run(Path(d), res)
    ws = got["words"]
    assert [(w["start"], w["end"]) for w in ws] == [(10.0, 11.0), (11.0, 12.0), (12.5, 13.4), (20.1, 20.9)]
    # 跨過 13.5–20.0（6.5 秒 > 1.6）的長停頓 → 斷成兩句，編號重編
    assert [s["id"] for s in got["sentences"]] == ["0000_000", "0000_001"]
    assert got["sentences"][0]["end"] == 13.5 and got["sentences"][1]["start"] == 20.0
    assert got["sentences"][1]["text"] == "開始"
    assert got["保留停頓秒數"] == tc.KEEP_PAUSE_S
    assert max(s["end"] - s["start"] for s in got["sentences"]) < 5.0


def test_transcribe_old_chunk_cache_without_pause_uses_old_mapping():
    res = {"words": [{"word": "好", "start": 2.2, "end": 2.6}],
           "segments": [{"start": 2.2, "end": 2.6, "text": "好"}]}
    with tempfile.TemporaryDirectory() as d:
        got = _fake_run(Path(d), res)
    w = got["words"][0]
    assert abs(w["start"] - 12.7) < 1e-6 and abs(w["end"] - 13.1) < 1e-6   # 沒補空白：2.2 → 12.5+0.2


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"通過：{t.__name__}")
    print(f"\n共 {len(tests)} 個測試通過")


if __name__ == "__main__":
    _run_all()
