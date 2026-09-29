"""bookclub/students.py 的單元測試：切段規則、範圍過濾、刪除範圍排除、男女聲判斷。不載入模型。

獨立可跑：.venv/bin/python tests/test_students.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from bookclub import students


def S(i: int, a: float, b: float, text: str) -> dict:
    return {"id": f"s{i}", "start": a, "end": b, "text": text}


def _spans(chunks):
    return [(c[0]["start"], c[-1]["end"]) for c in chunks]


def test_chunks_join_to_8_20_seconds_at_punctuation():
    sents = [S(i, i * 3.0, i * 3.0 + 2.9, "一句話，") for i in range(10)]   # 每句 3 秒、都以逗號結尾
    chunks = students.plan_chunks(sents)
    for a, b in _spans(chunks):
        assert 8 <= b - a <= 20, (a, b)
    assert sum(len(c) for c in chunks) == 10


def test_no_break_without_punctuation_until_max():
    sents = [S(i, i * 3.0, i * 3.0 + 2.9, "沒有標點") for i in range(10)]
    chunks = students.plan_chunks(sents)
    # 不能在句中斷：一直接到超過 20 秒才硬斷
    assert len(chunks[0]) >= 7


def test_long_gap_allows_break():
    sents = [S(0, 0, 4.9, "沒有標點"), S(1, 5, 9.9, "沒有標點"), S(2, 12, 16, "沒有標點"), S(3, 16.1, 20, "結尾。")]
    chunks = students.plan_chunks(sents)
    assert _spans(chunks)[0] == (0, 9.9)   # 9.9 秒之後停頓 2.1 秒，可以斷


def test_short_tail_merges_into_previous():
    sents = [S(0, 0, 9, "第一句。"), S(1, 9, 12, "尾巴。")]
    chunks = students.plan_chunks(sents)
    assert len(chunks) == 1 and _spans(chunks) == [(0, 12)]


def test_single_short_turn_kept():
    chunks = students.plan_chunks([S(0, 100, 107, "短短一句")])
    assert _spans(chunks) == [(100, 107)]


def test_cut_range_excluded_and_splits():
    sents = [S(i, i * 3.0, i * 3.0 + 2.9, "一句話。") for i in range(10)]
    chunks = students.plan_chunks(sents, cut_ranges=[(8.5, 15.5)])   # 蓋掉 s3、s4（中點 10.45、13.45）
    ids = [s["id"] for c in chunks for s in c]
    assert "s3" not in ids and "s4" not in ids
    # 刪除範圍前後不接在一起
    assert all(not ({"s2", "s5"} <= {s["id"] for s in c}) for c in chunks)


def test_clip_slot_stays_out_of_cut():
    assert students.clip_slot(2491.6, 2511.1, [(2328.9, 2491.8)]) == (2491.8, 2511.1)
    assert students.clip_slot(10, 20, [(19, 30)]) == (10, 19)
    assert students.clip_slot(10, 20, [(30, 40)]) == (10, 20)


def test_in_ranges_uses_midpoint():
    assert students.in_ranges(9, 11, [(10, 20)])
    assert not students.in_ranges(5, 11, [(10, 20)])


def test_in_window_filters_by_midpoint():
    sents = [S(0, 2210, 2225, "a"), S(1, 2215, 2228, "b"), S(2, 3320, 3330, "c"), S(3, 3318, 3322, "d")]
    kept = [s["id"] for s in students.in_window(sents, 2220, 3323)]
    assert kept == ["s1", "s3"]
    assert len(students.in_window(sents, None, None)) == 4


def test_split_edited_follows_proofread_text():
    # 09-29：重念照校對稿念；改過的字分回原本那一句，刪光的句子變空字串
    sents = [S(1, 0, 2, "我叫小美，"), S(2, 2, 4, "我在台北上班。"), S(3, 4, 6, "今天很開心。")]
    t = {"原文": "".join(s["text"] for s in sents), "校對稿": "我叫Amy，我在某公司上班。今天很開心！"}
    assert students.split_edited(t, sents) == {"s1": "我叫Amy，", "s2": "我在某公司上班。", "s3": "今天很開心！"}
    t["校對稿"] = "我叫小美，今天很開心。"
    assert students.split_edited(t, sents)["s2"] == ""


def test_split_edited_gives_up_when_text_not_found():
    sents = [S(1, 0, 2, "甲乙丙")]
    assert students.split_edited({"原文": "完全不同", "校對稿": "改過"}, sents) is None


def test_median_f0_male_female():
    sr = 16000
    t = np.arange(sr * 3) / sr
    low = 0.3 * np.sin(2 * np.pi * 120 * t) + 0.1 * np.sin(2 * np.pi * 240 * t)
    high = 0.3 * np.sin(2 * np.pi * 220 * t) + 0.1 * np.sin(2 * np.pi * 440 * t)
    assert students.median_f0(low.astype(np.float32), sr) < students.MALE_F0_HZ
    assert students.median_f0(high.astype(np.float32), sr) > students.MALE_F0_HZ


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
