"""bookclub/transcribe.py 的單元測試：只測純數字運算的部分（切塊、時間換算），
不碰音檔、VAD 模型或 Groq。

獨立可跑：.venv/bin/python tests/test_transcribe.py
"""

from __future__ import annotations

import sys
from pathlib import Path

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
    mapping = tc._compute_mapping(segs)
    assert mapping[0] == {"concat_start": 0.0, "concat_end": 2.0, "original_start": 10.0}
    assert mapping[1] == {"concat_start": 2.0, "concat_end": 3.0, "original_start": 20.0}


def test_map_time_to_original_start_prefers_next_segment_after_gap():
    segs = [{"start": 10.0, "end": 12.0}, {"start": 20.0, "end": 21.0}]
    mapping = tc._compute_mapping(segs)
    # concat 時間 2.0 剛好在接縫上：start 語意歸給後一段開頭（原始時間 20.0）
    t = tc._map_time_to_original(mapping, 2.0, prefer="start")
    assert abs(t - 20.0) < 1e-6


def test_map_time_to_original_end_prefers_previous_segment_before_gap():
    segs = [{"start": 10.0, "end": 12.0}, {"start": 20.0, "end": 21.0}]
    mapping = tc._compute_mapping(segs)
    # concat 時間 2.0 剛好在接縫上：end 語意歸給前一段結尾（原始時間 12.0）
    t = tc._map_time_to_original(mapping, 2.0, prefer="end")
    assert abs(t - 12.0) < 1e-6


def test_map_time_to_original_within_segment():
    segs = [{"start": 10.0, "end": 12.0}, {"start": 20.0, "end": 21.0}]
    mapping = tc._compute_mapping(segs)
    t = tc._map_time_to_original(mapping, 0.5, prefer="start")
    assert abs(t - 10.5) < 1e-6
    t2 = tc._map_time_to_original(mapping, 2.5, prefer="end")
    assert abs(t2 - 20.5) < 1e-6


def test_map_time_to_original_out_of_range_clamped():
    segs = [{"start": 10.0, "end": 12.0}]
    mapping = tc._compute_mapping(segs)
    t = tc._map_time_to_original(mapping, 5.0, prefer="end")  # 超出總長 2.0 秒很多
    assert t >= 10.0  # 夾到最後一段，不會噴錯或給負值


def test_build_prompt_without_roster():
    prompt = tc._build_prompt(None)
    assert "今天提到的學員" not in prompt


def test_build_prompt_with_roster():
    prompt = tc._build_prompt(["詩涵", "欣欣"])
    assert "詩涵" in prompt and "欣欣" in prompt
    assert "今天提到的學員" in prompt


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"通過：{t.__name__}")
    print(f"\n共 {len(tests)} 個測試通過")


if __name__ == "__main__":
    _run_all()
