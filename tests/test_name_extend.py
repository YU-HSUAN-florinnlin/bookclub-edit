"""10-04 #62 第 6 點：名字整句換掉的重念範圍，斷在話中間時往前後延伸到標點或真的停頓（0.3 秒以上）。

只用合成的句子與逐字時間，不碰音檔、不載入模型。
獨立可跑：.venv/bin/python tests/test_name_extend.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 10-01：這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import nameplan


def _w(word, a, b):
    return {"word": word, "start": a, "end": b}


def _cand(start, end, sid="s2", matched="小美", code="Amy"):
    return {"start": start, "end": end, "建議做法": "整句換掉", "sentence_id": sid, "matched_text": matched,
            "name": matched, "canonical": matched, "代號": code, "建議緩衝秒數": 0.05}


# s1「今天我們先」（話還沒完）→ s2「請小美分享」（名字在這句）→ s3「一下，她的心得。」
# 「今天」跟「我們先」之間停 0.4 秒；s3 裡「一下」後面有逗號
WORDS = [_w("今天", 0.0, 0.6), _w("我們", 1.0, 1.4), _w("先", 1.4, 2.0),
         _w("請", 2.05, 2.3), _w("小美", 2.3, 2.8), _w("分享", 2.8, 3.5),
         _w("一下", 3.55, 3.9), _w("她的", 3.9, 4.3), _w("心得", 4.3, 4.8)]


def _sents(**over):
    s = {"s1": {"id": "s1", "start": 0.0, "end": 2.0, "text": "今天我們先", "label": "老師"},
         "s2": {"id": "s2", "start": 2.05, "end": 3.5, "text": "請小美分享", "label": "老師"},
         "s3": {"id": "s3", "start": 3.55, "end": 4.8, "text": "一下，她的心得。", "label": "老師"}}
    for k, v in over.items():
        s[k] = {**s[k], **v}
    return s


def _slot(sents, extend=True, d=None):
    ordered = sorted(sents.values(), key=lambda x: x["start"])
    group = nameplan.expand_sentence(ordered, [x["id"] for x in ordered].index("s2"))
    around = nameplan.neighbors(ordered, group) if extend else None
    return nameplan.whole_slot(_cand(2.3, 2.8), d or {}, group, WORDS, around)


def test_extends_to_pause_before_and_punct_after():
    ws = _slot(_sents())
    assert ws["範圍"] == "延伸"
    assert abs(ws["start"] - 0.8) < 1e-6      # 「今天」跟「我們」之間 0.4 秒停頓的中間
    assert abs(ws["end"] - 3.9) < 1e-6        # 「一下，」的逗號後面
    assert ws["整句"] == [2.05, 3.5]          # 原本那一句的起訖留著給第 3 步顯示


def test_no_extension_for_old_workspaces():
    ws = _slot(_sents(), extend=False)
    assert ws["範圍"] is None and (ws["start"], ws["end"]) == (2.05, 3.5)
    on = nameplan.EXTEND_ON
    nameplan.EXTEND_ON = True     # 延伸功能打開時的判斷（預設關閉，見 test_extend_off_by_default）
    try:
        _check_enabled()
    finally:
        nameplan.EXTEND_ON = on


def _check_enabled():
    assert nameplan.extend_enabled({"保留停頓秒數": 1.0}) is True
    assert nameplan.extend_enabled({"sentences": []}) is False and nameplan.extend_enabled(None) is False
    assert nameplan.extend_enabled({"轉文字做法": "不挖停頓"}) is True          # 10-04 乙：新轉的工作區
    assert nameplan.extend_enabled({"轉文字做法": "別的"}) is False


def test_no_extension_when_boundary_already_at_punct_or_pause():
    ws = _slot(_sents(s1={"text": "今天我們先。"}, s2={"text": "請小美分享。"}))
    assert ws["範圍"] is None
    words = [dict(w) for w in WORDS]
    words[2] = _w("先", 1.4, 1.6)   # 「先」跟「請」之間停 0.45 秒
    ordered = sorted(_sents().values(), key=lambda x: x["start"])
    a, b = nameplan.extend_range(words, 2.05, 3.5, ordered[0], None, [ordered[1]])
    assert a == 2.05


def test_no_extension_into_student_sentence():
    ws = _slot(_sents(s1={"label": "不是老師"}, s3={"label": "不是老師"}))
    assert ws["範圍"] is None


def test_no_extension_when_no_cut_within_limit():
    ordered = [{"id": "a", "start": -8.0, "end": 2.0, "text": "很長很長沒有停頓", "label": "老師"},
               {"id": "b", "start": 2.05, "end": 3.5, "text": "請小美分享", "label": "老師"}]
    words = [_w("很長", -8.0 + k * 0.25, -8.0 + (k + 1) * 0.25) for k in range(40)] + WORDS[3:6]
    a, b = nameplan.extend_range(words, 2.05, 3.5, ordered[0], None, [ordered[1]])
    assert a == 2.05   # 5 秒內找不到標點或停頓，也不能整句（10 秒）都拿 → 不延伸


def test_whole_previous_sentence_when_short_and_no_inner_cut():
    ordered = [{"id": "a", "start": 0.5, "end": 2.0, "text": "我們先", "label": "老師"},
               {"id": "b", "start": 2.05, "end": 3.5, "text": "請小美分享", "label": "老師"}]
    a, _b = nameplan.extend_range(WORDS[1:6], 2.05, 3.5, ordered[0], None, [ordered[1]])
    assert a == 0.5


def test_edited_text_written_for_original_sentence_keeps_original_range():
    ws = _slot(_sents(), d={"改稿": "請Amy分享"})
    assert ws["範圍"] is None


def test_build_plan_uses_extended_range_and_words():
    plan = nameplan.build_plan([_cand(2.3, 2.8)], {}, _sents(), words=WORDS, extend=True)
    g = plan["生成"][0]
    assert g["slot"] == [0.8, 3.9] and g["範圍"] == "延伸"
    assert g["text"] == "我們先請Amy分享一下"
    old = nameplan.build_plan([_cand(2.3, 2.8)], {}, _sents(), words=WORDS)
    assert old["生成"][0]["slot"] == [2.05, 3.5] and "範圍" not in old["生成"][0]


def test_extension_does_not_swallow_other_name_cards():
    """審查 4：前一句有卡 2 的名字（選直接消音）→ 延伸不能把它包進來重念、吃掉卡 2 的消音。"""
    c1, c2 = _cand(2.3, 2.8), _cand(1.0, 1.4, sid="s1", matched="我們", code="Tom")
    plan = nameplan.build_plan([c1, c2], {"2": {"做法": "直接消音"}}, _sents(), words=WORDS, extend=True)
    g = plan["生成"][0]
    assert g["slot"][0] == 2.05 and g["slot"][1] == 3.9      # 前面那一邊不延伸，後面照樣延伸
    assert "我們" not in g["text"]
    assert [m["候選"] for m in plan["消音"]] == [2]
    # 保留原聲學員的名字候選也一樣不能碰
    plan = nameplan.build_plan([c1], {}, _sents(), words=WORDS, extend=True, avoid=[(3.6, 3.8)])
    assert plan["生成"][0]["slot"] == [0.8, 3.5]


def test_other_ranges_excludes_itself():
    c1, c2 = _cand(2.3, 2.8), _cand(1.0, 1.4)
    assert nameplan.other_ranges([c1, c2], c1, [(9.0, 9.5)]) == [(1.0, 1.4), (9.0, 9.5)]


def test_problem_c_neighbor_card_whole_sentence_not_overlapped():
    """問題 C：s1「謝謝小名，那我們」（卡 2，整句換掉）、s2「請小美分享」（卡 1）、s3「一下。」
    以前卡 1 往前延伸到逗號 [0.925, 3.3]，跟卡 2 的 [0.0, 1.5] 疊在一起，組裝時長的優先、卡 2 那一段老師的話不見。"""
    words = [_w("謝謝", 0.0, 0.4), _w("小名", 0.4, 0.8), _w("那", 0.85, 1.0), _w("我們", 1.0, 1.5),
             _w("請", 1.55, 1.8), _w("小美", 1.8, 2.2), _w("分享", 2.2, 2.7), _w("一下", 2.75, 3.3)]
    sents = {"s1": {"id": "s1", "start": 0.0, "end": 1.5, "text": "謝謝小名，那我們", "label": "老師"},
             "s2": {"id": "s2", "start": 1.55, "end": 2.7, "text": "請小美分享", "label": "老師"},
             "s3": {"id": "s3", "start": 2.75, "end": 3.3, "text": "一下。", "label": "老師"}}
    c1 = _cand(1.8, 2.2, sid="s2", matched="小美", code="Amy")
    c2 = _cand(0.4, 0.8, sid="s1", matched="小名", code="Tom")
    plan = nameplan.build_plan([c1, c2], {}, sents, words=words, extend=True)
    slots = sorted(g["slot"] for g in plan["生成"])
    assert len(slots) == 2, plan["生成"]
    assert slots[0][1] <= slots[1][0] + 1e-6, slots          # 兩筆時間格不重疊
    texts = " ".join(g["text"] for g in plan["生成"])
    assert "Amy" in texts and "Tom" in texts and "小名" not in texts and "小美" not in texts
    g1 = next(g for g in plan["生成"] if 1 in g["候選"])
    assert g1["slot"][0] >= 1.5 - 1e-6 and abs(g1["slot"][1] - 3.3) < 1e-6   # 往後照樣延伸到「一下。」


# ---------- 10-04 問題 D：同一句兩張卡、只有一張能延伸 ----------

WORDS_D = [_w("今天", 0.0, 0.6), _w("我們", 1.0, 1.4), _w("先", 1.4, 2.0),
           _w("請", 2.05, 2.3), _w("小美", 2.3, 2.8), _w("和", 2.8, 3.0), _w("小華", 3.0, 3.5), _w("分享", 3.5, 4.0),
           _w("一下", 4.05, 4.4), _w("她的", 4.4, 4.8), _w("心得", 4.8, 5.3)]


def _sents_d():
    return {"s1": {"id": "s1", "start": 0.0, "end": 2.0, "text": "今天我們先", "label": "老師"},
            "s2": {"id": "s2", "start": 2.05, "end": 4.0, "text": "請小美和小華分享", "label": "老師"},
            "s3": {"id": "s3", "start": 4.05, "end": 5.3, "text": "一下，她的心得。", "label": "老師"}}


def _cands_d(**kw2):
    return [_cand(2.3, 2.8, sid="s2", matched="小美", code="Amy"),
            {**_cand(3.0, 3.5, sid="s2", matched="小華", code="Cat"), **kw2}]


def test_problem_d_one_card_with_edited_text_blocks_whole_sentence():
    dec = {"2": {"改稿": "請Amy和Cat分享"}}
    plan = nameplan.build_plan(_cands_d(), dec, _sents_d(), words=WORDS_D, extend=True)
    plain = nameplan.build_plan(_cands_d(), dec, _sents_d(), words=WORDS_D, extend=False)
    assert plan == plain
    assert len(plan["生成"]) == 1 and plan["生成"][0]["slot"] == [2.05, 4.0]
    assert plan["生成"][0]["text"] == "請Amy和Cat分享" and sorted(plan["生成"][0]["候選"]) == [1, 2]


def test_problem_d_one_card_with_moved_time_blocks_whole_sentence():
    cands = _cands_d(改過時間=True)
    plan = nameplan.build_plan(cands, {}, _sents_d(), words=WORDS_D, extend=True)
    assert plan == nameplan.build_plan(_cands_d(改過時間=True), {}, _sents_d(), words=WORDS_D, extend=False)
    slots = [g["slot"] for g in plan["生成"]]
    assert all(b <= 4.0 + 1e-6 and a >= 2.05 - 1e-6 for a, b in slots), slots


def test_problem_d_other_blockers_and_plain_sentence_still_extends():
    for dec in ({"2": {"整句起訖": [2.0, 4.0]}}, {"2": {"做法": "直接消音"}}, {"2": {"做法": "只換名字"}}):
        plan = nameplan.build_plan(_cands_d(), dec, _sents_d(), words=WORDS_D, extend=True)
        assert plan == nameplan.build_plan(_cands_d(), dec, _sents_d(), words=WORDS_D, extend=False), dec
    # 卡 2 標成不是名字（不會產生處理）→ 不擋；兩張都是一般整句換掉 → 一起延伸成一筆
    plan = nameplan.build_plan(_cands_d(), {"2": {"tags": ["不是名字"]}}, _sents_d(), words=WORDS_D, extend=True)
    assert plan["生成"][0]["slot"] == [0.8, 4.4]
    plan = nameplan.build_plan(_cands_d(), {}, _sents_d(), words=WORDS_D, extend=True)
    assert len(plan["生成"]) == 1 and plan["生成"][0]["slot"] == [0.8, 4.4]
    assert "Amy" in plan["生成"][0]["text"] and "Cat" in plan["生成"][0]["text"]


# ---------- 延伸預設關閉 ----------

def test_extend_off_by_default():
    assert nameplan.EXTEND_ON is False
    assert nameplan.extend_enabled({"轉文字做法": "不挖停頓"}) is False
    assert nameplan.extend_enabled({"保留停頓秒數": 1.0}) is False


def test_extend_off_new_workspace_plan_same_as_before():
    """預設關閉時，新轉的工作區（merged.json 有 轉文字做法）排出來的名字處理計畫跟沒有這個欄位時完全一樣。"""
    import json
    import tempfile

    import fake_workdir

    w = fake_workdir.make(tempfile.mkdtemp())
    before = nameplan.compute_plan(w)
    mp = w / "transcript" / "merged.json"
    m = json.loads(mp.read_text(encoding="utf-8"))
    m["轉文字做法"] = "不挖停頓"
    mp.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    assert nameplan.compute_plan(w) == before


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"通過：{t.__name__}")
    print(f"\n共 {len(tests)} 個測試通過")


if __name__ == "__main__":
    _run_all()
