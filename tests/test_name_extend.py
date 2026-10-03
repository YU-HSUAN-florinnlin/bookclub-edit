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
    assert nameplan.extend_enabled({"保留停頓秒數": 1.0}) is True
    assert nameplan.extend_enabled({"sentences": []}) is False and nameplan.extend_enabled(None) is False


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


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"通過：{t.__name__}")
    print(f"\n共 {len(tests)} 個測試通過")


if __name__ == "__main__":
    _run_all()
