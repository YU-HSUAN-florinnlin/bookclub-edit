"""10-18 #244：第 4 步「做過沒有」的「還有 N 句要生成」寫看得懂的位置：原片時間＋第 3 步畫面上的卡片類型。

以前寫「還有 1／42 句要生成（S018）」，S018 畫面上看不到；現在寫「還有 1／42 句要生成：1:11:52（老師提到名字）」，
多句用「、」接、照時間排，超過 5 句寫「…等 N 句」。時間跟第 3 步卡片清單一樣（`cards.js` 的 `rvFmt(秒)`）。
老師名字（S／N → 老師提到名字；只有重疊的 V／W → 重疊）、學員重念（學員段落；重疊卡片的學員那句 → 重疊）、
保留原聲學員名字（學員提到名字）三步都一樣。`_left_count` 照樣讀得出句數。

全部是合成資料。獨立可跑：.venv/bin/python tests/test_done_where.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 暫存資料夾跑完自己清（要在 tempfile 之前）

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from bookclub import execute  # noqa: E402
from bookclub import workdir as wd  # noqa: E402


def test_time_format_same_as_cards():
    assert execute.fmt_card_time(4312.9) == "1:11:52"
    assert execute.fmt_card_time(185.99) == "3:05"
    assert execute.fmt_card_time(7.2) == "0:07"
    assert execute.fmt_card_time(3600) == "1:00:00"
    assert execute.fmt_card_time(-3) == "0:00"


def test_where_note_sorted_and_capped():
    assert execute._where_note([(4312.9, "老師提到名字")]) == "1:11:52（老師提到名字）"
    assert execute._where_note([(80, "重疊"), (65, "老師提到名字")]) == "1:05（老師提到名字）、1:20（重疊）"
    rows = [(60 * k, "老師提到名字") for k in range(7, 0, -1)]
    got = execute._where_note(rows)
    assert got == "、".join(f"{k}:00（老師提到名字）" for k in range(1, 6)) + "…等 7 句", got
    assert execute._where_note([(60 * k, "x") for k in range(5)]).count("、") == 4 and "等" not in execute._where_note(
        [(60 * k, "x") for k in range(5)])


def test_left_count_still_reads():
    assert execute._left_count("還有 1／42 句要生成：1:11:52（老師提到名字）") == 1
    assert execute._left_count("還有 7／9 段要生成：1:00（學員段落）…等 7 句") == 7
    assert execute._left_count("老師參考音換過了，10 句都要重新生成") == 10


def test_teacher_where_kinds():
    starts = {"1": 76.3, "NM001": 30.0}
    assert execute._teacher_where({"候選": [1], "slot": [70.0, 80.0]}, starts) == (76.3, "老師提到名字")
    assert execute._teacher_where({"候選": ["NM001"], "slot": [29.0, 33.0]}, starts) == (30.0, "老師提到名字")
    assert execute._teacher_where({"候選": [], "重疊項目": ["O69.60"], "slot": [66.0, 71.0]}, starts) == (69.6, "重疊")
    assert execute._teacher_where({"候選": [], "重疊項目": ["自訂"], "slot": [66.0, 71.0]}, starts) == (66.0, "重疊")


def test_three_steps_messages():
    """假工作區：三步都還沒生成，訊息寫時間＋卡片類型，不出現內部編號。"""
    import test_name_drop as T

    w = T._make()
    done, why = execute.names_done(w)
    cands = wd.read_json(wd.names_path(w))["candidates"]
    want = "、".join(f"{execute.fmt_card_time(c['start'])}（老師提到名字）" for c in sorted(cands, key=lambda c: c["start"]))
    assert not done and why.endswith("句要生成：" + want), why
    assert not re.search(r"\b[SNVW]\d{3}", why) and execute._left_count(why) == len(cands), why

    done, why = execute.students_done(w, None, None)
    assert not done and "段要生成：" in why and "（學員段落）" in why and not re.search(r"T\d{3}", why), why
    from bookclub import turns as turns_mod

    first = min(t["start"] for t in turns_mod.page_data(w)["段落"] if t["說話者"] != "老師")
    assert why.split("：", 1)[1].startswith(execute.fmt_card_time(first) + "（學員段落）"), why
    assert execute._left_count(why) and ("…等" in why) == (execute._left_count(why) > 5), why

    undo = T._student_setup(w)
    try:
        done, why = execute.stunames_done(w)
        from bookclub import studentnames

        g = studentnames.plan(w)["生成"][0]
    finally:
        undo()
    t0 = float(g["候選"][0].rsplit("@", 1)[1])
    assert not done and why == f"還有 1／1 句要生成：{execute.fmt_card_time(t0)}（學員提到名字）", why


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
