"""#298：老師點名句的最後一個名字，聲音比句子結束晚一點點，被算進下一句（下一句的字裡沒有這個名字）。

以前：要念的字從逐字稿拼成「我 代號剛剛…」，名字多念一次、順序錯；現在歸到文字真的含名字的前一句，
時間格延到名字結束，但不延進會整段重念的學員段落（延進去會跟學員那一格搶、整句被扣成消音）。

全部是合成資料（假名字＋自編句子）。獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_name_home_sentence.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 暫存資料夾跑完自己清（要在 tempfile 之前）

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import nameplan  # noqa: E402


def _s(sid, a, b, text, label="老師"):
    return {"id": sid, "start": a, "end": b, "text": text, "label": label}


def _cand(sid, a, b):
    return {"matched_text": "小安", "name": "小安", "canonical": "小安", "代號": "Amy", "sentence_id": sid, "start": a, "end": b}


def _sents():
    return [_s("a", 0, 2, "等一下 小安先好嗎 小安"), _s("b", 2, 8, "我剛剛本來想到都還沒有回答")]


def test_home_sentence_goes_to_previous():
    sents = _sents()
    pos = {s["id"]: k for k, s in enumerate(sents)}
    assert nameplan.home_sentence(sents, pos, _cand("b", 2.1, 2.4)) == "a"        # 名字的聲音在 b 的時間裡、字在 a
    assert nameplan.home_sentence(sents, pos, _cand("a", 0.5, 0.8)) == "a"        # 字就在自己這一句
    assert nameplan.home_sentence(sents, pos, _cand("b", 5.0, 5.3)) == "b"        # 離 a 的邊界太遠，不動
    far = [_s("a", 0, 2, "等一下 小安先好嗎 小安"), _s("b", 2, 8, "我剛剛本來想到都還沒有回答", "不是老師")]
    assert nameplan.home_sentence(far, pos, _cand("b", 2.1, 2.4)) == "a"          # 鄰句是 a（老師），b 自己的標籤不影響
    stu = [_s("a", 0, 2, "等一下 小安先好嗎 小安", "不是老師"), _s("b", 2, 8, "我剛剛本來想到都還沒有回答")]
    assert nameplan.home_sentence(stu, pos, _cand("b", 2.1, 2.4)) == "b"          # 鄰句不是老師的話，不歸過去


def test_plan_text_and_slot():
    sents = _sents()
    cands = [_cand("a", 0.5, 0.8), _cand("b", 2.1, 2.4)]
    words = [{"word": "我", "start": 1.9, "end": 3.0}, {"word": "小安", "start": 2.1, "end": 2.4}, {"word": "剛剛", "start": 3.0, "end": 3.4}]
    plan = nameplan.build_plan(cands, {}, {s["id"]: s for s in sents}, words=words)
    assert len(plan["生成"]) == 1 and not plan["要人處理"], plan
    g = plan["生成"][0]
    assert g["text"] == "等一下 Amy先好嗎 Amy", g["text"]
    assert g["候選"] == [1, 2] and g["slot"][1] >= 2.4, g


def test_edited_text_not_overwritten_by_later_card():
    """審查：同一句另一張卡人已經改好要念的字，後到的卡併進來不能蓋掉。"""
    sents = [_s("a", 0, 2, "請 小安 和 小美 先說"), _s("b", 2, 8, "我剛剛想到")]
    c1 = {**_cand("a", 0.2, 0.5), "代號": "Amy"}
    c2 = {"matched_text": "小美", "name": "小美", "canonical": "小美", "代號": "Bella", "sentence_id": "b", "start": 2.05, "end": 2.3}
    sents[0]["text"] = "請 小安 和 小美"   # 小美在句尾，聲音溢進下一句
    plan = nameplan.build_plan([c1, c2], {"1": {"改稿": "Amy 跟 Bella 請先說", "已確認": True}}, {s["id"]: s for s in sents})
    assert len(plan["生成"]) == 1 and plan["生成"][0]["text"] == "Amy 跟 Bella 請先說" and plan["生成"][0].get("改稿"), plan


def test_neighbor_must_be_teacher_label():
    sents = [_s("a", 0, 2, "等一下 小安先好嗎 小安", "太短"), _s("b", 2, 8, "我剛剛本來想到都還沒有回答")]
    pos = {s["id"]: k for k, s in enumerate(sents)}
    assert nameplan.home_sentence(sents, pos, _cand("b", 2.1, 2.4)) == "b"


def test_cap_end_at_turns():
    items = [{"id": "S1", "slot": [0.0, 2.4]}, {"id": "S2", "slot": [5.0, 9.0]}, {"id": "S3", "slot": [0.0, 2.0]}]
    nameplan.cap_end_at_turns(items, {"S1": 2.0, "S2": 8.9, "S3": 2.0}, [2.0, 9.5])
    assert items[0]["slot"] == [0.0, 2.0], items          # 延進學員段落的收回到句子結束
    assert items[1]["slot"] == [5.0, 9.0], items          # 學員段落在句子結束之後，不動
    assert items[2]["slot"] == [0.0, 2.0], items          # 本來就沒延，不動
    items = [{"id": "S1", "slot": [0.0, 2.4]}]
    nameplan.cap_end_at_turns(items, {"S1": 2.0}, [])
    assert items[0]["slot"] == [0.0, 2.4], items          # 沒有學員段落就照舊


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
