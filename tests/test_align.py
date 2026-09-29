"""第 3 步「新增修改」的測試：對齊規則（`bookclub/align.py`，純函式）＋在假工作區上每一種類型新增、改時間一次
（`bookclub/review.py` 的 `manual_edit`）。用 tests/fake_workdir.py 的合成資料，不碰真的影片、不呼叫 Claude／Groq。

獨立可跑：.venv/bin/python tests/test_align.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import fake_workdir  # noqa: E402

_DATA = Path(tempfile.mkdtemp())
(_DATA / "名冊.csv").write_text("中文名,其他寫法,英文代號,聲線,性別\n小美,,Amy,,女\n阿明,,Tom,,男\n", encoding="utf-8")
os.environ["BOOKCLUB_DATA_DIR"] = str(_DATA)

from bookclub import align, nameplan, review  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

WORDS = [{"word": "剛", "start": 10.0, "end": 10.3}, {"word": "剛", "start": 10.3, "end": 10.6},
         {"word": "小", "start": 10.7, "end": 11.0}, {"word": "美", "start": 11.0, "end": 11.3},
         {"word": "分", "start": 11.32, "end": 11.6}]
SENTS = [{"id": "a", "start": 10.0, "end": 13.6, "text": "第一句。"}, {"id": "b", "start": 14.0, "end": 17.6, "text": "第二句，"},
         {"id": "c", "start": 18.0, "end": 21.6, "text": "第三句。"}]


# ---------- 純函式 ----------

def test_words_cover_marked_chars_with_pause_but_not_neighbors():
    r = align.align_words(10.75, 11.25, WORDS)
    assert r["文字"] == "小美" and r["對齊"] == [True, True] and r["對齊到"] == "逐字稿的字"
    assert r["start"] == 10.65                  # 前面留 0.05 秒（前一個字 10.6 就結束了）
    assert r["end"] == 11.32                    # 後面想留 0.05 秒，但下一個字 11.32 就開始了，不吃進去
    assert r["標的起訖"] == [10.75, 11.25]


def test_words_sloppy_mark_uses_overlap_then_nearest():
    # 標得太窄、沒有字的中點在裡面：用有重疊的字
    assert align.align_words(10.95, 11.05, WORDS)["文字"] == "小美"
    # 標在兩個字中間的空白：找 0.5 秒內最近的字
    assert align.align_words(10.62, 10.66, WORDS)["文字"] in ("剛", "小")


def test_words_nothing_nearby_keeps_time():
    r = align.align_words(30.0, 31.0, WORDS)
    assert r["對齊"] == [False, False] and (r["start"], r["end"]) == (30.0, 31.0) and r["文字"] == ""
    assert align.align_words(1.0, 2.0, [])["對齊"] == [False, False]


def test_sentences_snap_each_end_within_one_second():
    r = align.align_sentences(14.3, 21.2, SENTS)
    assert (r["start"], r["end"], r["對齊"]) == (14.0, 21.6, [True, True])
    assert r["文字"] == "第二句，第三句。"
    r = align.align_sentences(15.5, 21.2, SENTS)          # 起點離句子開頭 1.5 秒：不對，保留
    assert (r["start"], r["end"], r["對齊"]) == (15.5, 21.6, [False, True])


def test_sentences_collapse_keeps_original():
    # 起點、終點對到同一個地方或顛倒 → 兩端都不對齊
    r = align.align_sentences(13.9, 13.95, SENTS)
    assert r["對齊"] == [False, False] and (r["start"], r["end"]) == (13.9, 13.95)


def test_quiet_uses_snap_result_only_when_clean():
    snaps = {5.0: (4.82, True), 9.0: (9.1, False)}
    r = align.align_quiet(5.0, 9.0, lambda t: snaps[t])
    assert (r["start"], r["end"], r["對齊"], r["對齊到"]) == (4.82, 9.0, [True, False], "安靜處")


def test_rules_per_kind():
    assert align.RULES == {"刪除段落": "安靜處", "局部消音": "安靜處", "名字": "逐字稿的字",
                           "學員發言": "句子邊界", "重疊": "句子邊界"}
    assert set(align.RULES) == set(review.MANUAL_KINDS)


def test_retime_turns_moves_sentences_between_neighbors():
    sent = {f"s{k}": {"id": f"s{k}", "start": 4.0 * k, "end": 4.0 * k + 3.6, "text": f"句{k}。"} for k in range(6)}
    turns = [{"id": "T1", "start": 0.0, "end": 7.6, "句子": ["s0", "s1"], "說話者": "老師", "原文": "句0。句1。", "校對稿": "句0。句1。", "已確認": True},
             {"id": "T2", "start": 8.0, "end": 19.6, "句子": ["s2", "s3", "s4"], "說話者": "學員1", "原文": "句2。句3。句4。",
              "校對稿": "人改過的稿子", "已確認": True},
             {"id": "T3", "start": 20.0, "end": 23.6, "句子": ["s5"], "說話者": "老師", "原文": "句5。", "校對稿": "句5。", "已確認": False}]
    out = review.retime_turns(turns, "T2", 4.0, 15.6, sent)     # 往前拿一句 s1、後面放掉 s4
    by = {t["id"]: t for t in out}
    assert by["T2"]["句子"] == ["s1", "s2", "s3"] and (by["T2"]["start"], by["T2"]["end"]) == (4.0, 15.6)
    assert by["T2"]["校對稿"] == "人改過的稿子" and by["T2"]["已確認"] is False   # 人改過的稿子保留、要重看
    assert by["T1"]["句子"] == ["s0"] and by["T1"]["end"] == 3.6 and by["T1"]["校對稿"] == "句0。"
    assert by["T3"]["句子"] == ["s4", "s5"] and by["T3"]["start"] == 16.0
    assert turns[1]["句子"] == ["s2", "s3", "s4"]                 # 不改到傳進來的資料


# ---------- 假工作區：每一種類型新增、改時間一次 ----------

_ROOT = None


def _fresh() -> Path:
    global _ROOT
    if _ROOT is None:
        _ROOT = Path(tempfile.mkdtemp()) / "base"
        fake_workdir.make(_ROOT)
    d = Path(tempfile.mkdtemp())
    shutil.copytree(_ROOT, d / "base")
    return d / "base" / "工作區"


def _items(w: Path) -> dict:
    return {f"{x['類型']}:{x['id']}": x for x in review.page_data(w)["項目"]}


def test_manual_add_and_edit_every_kind():
    w = _fresh()
    # 刪除段落：兩端都在句子之間的空白附近（假資料每句 3.6 秒、句間 0.4 秒安靜）
    r = review.manual_edit(w, {"類型": "刪除段落", "start": "0:43.8", "end": "0:51.7"})
    assert r["新增"] and r["id"] == "D001" and r["對齊結果"]["對齊"] == [True, True]
    assert 43.6 <= r["對齊結果"]["start"] <= 44.0 and 51.6 <= r["對齊結果"]["end"] <= 52.0
    it = _items(w)["刪除段落:D001"]
    assert it["人工新增"] and it["來源"] == "手動" and it["標的起訖"] == [43.8, 51.7]
    r = review.manual_edit(w, {"類型": "刪除段落", "id": "D001", "start": 47.9, "end": 51.8})
    assert r["id"] == "D001" and not r["新增"] and _items(w)["刪除段落:D001"]["標的起訖"] == [47.9, 51.8]

    # 局部消音：標在講話中間，附近 0.5 秒沒有安靜處 → 保留原本的時間、沒對齊
    r = review.manual_edit(w, {"類型": "局部消音", "start": 45.0, "end": 46.0, "方式": "霧化"})
    assert r["id"] == "M001" and r["對齊結果"]["對齊"] == [False, False]
    m = _items(w)["局部消音:M001"]
    assert (m["start"], m["end"], m["方式"], m["來源"]) == (45.0, 46.0, "霧化", "人工新增")
    review.manual_edit(w, {"類型": "局部消音", "id": "M001", "start": 45.2, "end": 46.3})
    assert _items(w)["局部消音:M001"]["end"] == 46.3

    # 漏抓的學員發言：藏在老師段落裡的 164–168 秒 → 對齊到句子邊界、建一段學員段落
    r = review.manual_edit(w, {"類型": "學員發言", "start": "2:44.3", "end": "2:47.9", "說話者": "學員2"})
    assert r["類型"] == "學員段落" and r["對齊結果"]["start"] == 164.0 and r["對齊結果"]["end"] == 167.6
    stu = _items(w)[f"學員段落:{r['id']}"]
    assert stu["人工新增"] and stu["說話者"] == "學員2" and stu["對齊"] == [True, True]
    # 改時間（原本就有的學員段落 T003：40–71.6 → 44–67.6，前後的句子還給相鄰的老師段落）
    r = review.manual_edit(w, {"類型": "學員發言", "id": "T003", "start": 44.2, "end": 67.5})
    turns = {t["id"]: t for t in wd.read_json(w / "校對" / "段落.json")["段落"]}
    assert (turns["T003"]["start"], turns["T003"]["end"]) == (44.0, 67.6)
    assert turns["T002"]["end"] == 43.6 and turns["T004"]["start"] == 68.0

    # 漏抓的老師提到名字：對齊到字、找所在的句子、要選代號
    try:
        review.manual_edit(w, {"類型": "名字", "start": 84.1, "end": 84.8})
        raise AssertionError("沒選代號應該擋下來")
    except ValueError:
        pass
    r = review.manual_edit(w, {"類型": "名字", "start": 84.12, "end": 84.8, "代號": "Tom"})
    assert r["id"] == "NM001" and r["對齊結果"]["對齊到"] == "逐字稿的字" and r["對齊結果"]["對齊"] == [True, True]
    nm = _items(w)["名字:NM001"]
    assert nm["人工新增"] and nm["matched_text"] == "老師" and nm["代號"] == "Tom"
    plan = nameplan.compute_plan(w)
    g = next(x for x in plan["生成"] if "NM001" in x["候選"])
    assert g["id"] == "SNM001" and g["text"].startswith("Tom說的第21句")
    review.save_name(w, "NM001", {"做法": "直接消音", "已確認": True})
    plan = nameplan.compute_plan(w)
    assert any(m["候選"] == "NM001" for m in plan["消音"])
    review.manual_edit(w, {"類型": "名字", "id": "NM001", "start": 84.1, "end": 85.2})
    assert _items(w)["名字:NM001"]["end"] > 84.8
    # 自動抓到的名字改時間：記在名字覆核決定，計畫跟著用新的時間
    review.save_name(w, "2", {"做法": "直接消音"})
    review.manual_edit(w, {"類型": "名字", "id": "2", "start": 120.1, "end": 120.9})
    it = _items(w)["名字:2"]
    assert it["標的起訖"] == [120.1, 120.9] and it["start"] != 120.0
    mute = next(m for m in nameplan.compute_plan(w)["消音"] if m["候選"] == 2)
    assert (mute["start"], mute["end"]) == (it["start"], it["end"])

    # 漏抓的重疊：對齊到句子邊界，清單裡照樣有建議
    r = review.manual_edit(w, {"類型": "重疊", "start": 72.2, "end": 75.3})
    ov = _items(w)["重疊:OM001"]
    assert (ov["start"], ov["end"]) == (72.0, 75.6) and ov["人工新增"] and ov["建議"]["做法"]
    review.save_overlap(w, "OM001", {"做法": "只留老師", "已確認": True})
    review.manual_edit(w, {"類型": "重疊", "id": "OM001", "start": 76.1, "end": 79.5})
    ov = _items(w)["重疊:OM001"]
    assert (ov["start"], ov["end"], ov["做法"], ov["已確認"]) == (76.0, 79.6, "只留老師", True)
    # 自動抓到的重疊改時間：id 不變（決定對得上），時間換新的
    review.manual_edit(w, {"類型": "重疊", "id": "O69.60", "start": 68.3, "end": 71.4})
    ov = _items(w)["重疊:O69.60"]
    assert (ov["start"], ov["end"]) == (68.0, 71.6)

    # 影片分析建議刪除的（S1）改時間＝確認刪除、用新的起訖
    review.manual_edit(w, {"類型": "刪除段落", "id": "S1", "start": 0.1, "end": 3.7})
    s1 = _items(w)["刪除段落:S1"]
    assert s1["決定"] == "刪除" and s1["標的起訖"] == [0.1, 3.7]

    # 存檔格式
    dec = wd.read_json(review.review_path(w))
    assert [x["id"] for x in dec["人工重疊"]] == ["OM001"] and [x["id"] for x in dec["人工名字"]] == ["NM001"]
    assert dec["重疊"]["O69.60"]["改過的起訖"] == [68.0, 71.6]


def test_bad_input_rejected():
    w = _fresh()
    for f in ({"類型": "亂寫", "start": 1, "end": 2}, {"類型": "刪除段落", "start": 5, "end": 4},
              {"類型": "刪除段落", "start": "a:b", "end": 4}):
        try:
            review.manual_edit(w, f)
            raise AssertionError(f)
        except ValueError:
            pass


def test_old_decisions_file_still_loads():
    # 0.1.6 以前的 覆核決定.json 沒有 人工重疊／人工名字，照樣讀得進來
    w = _fresh()
    old = {"版本": 1, "重疊": {}, "刪除段落": [{"id": "D001", "start": 10.0, "end": 12.0, "狀態": "刪除"}],
           "局部消音": [], "學員聲音": {}, "覆核秒數": 0}
    review.review_path(w).parent.mkdir(parents=True, exist_ok=True)
    review.review_path(w).write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    d = review.load_decisions(w)
    assert d["人工重疊"] == [] and d["人工名字"] == [] and d["刪除段落"][0]["id"] == "D001"
    assert "刪除段落:D001" in _items(w)
    r = review.manual_edit(w, {"類型": "刪除段落", "start": 55.7, "end": 59.8})
    assert r["id"] == "D002"


def test_effective_overlaps_for_render():
    w = _fresh()
    review.manual_edit(w, {"類型": "重疊", "start": 72.2, "end": 75.3})
    ov = wd.read_json(wd.overlap_path(w))
    out = review.effective_overlaps(w, ov["overlaps"])
    assert [review.overlap_id(o) for o in out] == ["O69.60", "OM001", "O100.00", "O150.20"]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
