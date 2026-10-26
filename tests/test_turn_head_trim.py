"""#298：學員段落最前面連續幾句聲紋明明是老師 → 起點往後推、那幾句還給前一個老師段落。

條件很保守（前一段是老師而且緊接著、每句相似度夠高、合計夠長、後面大多是不是老師、還沒確認、校對稿沒人改過）。
全部是合成資料。獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_turn_head_trim.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 暫存資料夾跑完自己清（要在 tempfile 之前）

import copy
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import turns  # noqa: E402


def _s(sid, a, b, text, label, sim):
    return {"id": sid, "start": a, "end": b, "text": text, "label": label, "聲紋判斷": label, "sim": sim}


def _data(head=(("s2", 10.0, 16.0, "我先講", "老師", 0.81),), rest_labels=("不是老師",) * 4, confirmed=False, prev_who="老師"):
    sents = [_s("s1", 8.0, 10.0, "請小美先說", "老師", 0.6)]
    for sid, a, b, text, lab, sim in head:
        sents.append(_s(sid, a, b, text, lab, sim))
    t = 16.0
    for k, lab in enumerate(rest_labels):
        sents.append(_s(f"r{k}", t, t + 4.0, f"學員的話{k}", lab, 0.3))
        t += 4.0
    ids = [x["id"] for x in sents]
    n_head = 1 + len(head)
    a = {"id": "T1", "start": 8.0, "end": 10.0, "句子": ["s1"], "文字判斷": prev_who, "原文": "請小美先說", "校對稿": "請小美先說"}
    b = {"id": "T2", "start": 10.0, "end": t, "句子": ids[1:], "文字判斷": "學員", "說話者": "學員1", "已確認": confirmed,
         "原文": "".join(x["text"] for x in sents[1:]), "校對稿": "".join(x["text"] for x in sents[1:])}
    a["說話者"] = prev_who
    return [a, b], {x["id"]: x for x in sents}, n_head


def test_trim_moves_head_to_previous_turn():
    ts, by, _ = _data()
    ch = turns.trim_teacher_heads(ts, by)
    assert ch == [{"段落": "T2", "句數": 1, "秒數": 6.0}], ch
    a, b = ts
    assert a["句子"] == ["s1", "s2"] and a["end"] == 16.0 and a["原文"].endswith("我先講") and a["校對稿"] == a["原文"], a
    assert b["start"] == 16.0 and b["句子"][0] == "r0" and "我先講" not in b["原文"] and b["校對稿"] == b["原文"], b
    assert b["起點修正"] == {"移給前一段句數": 1, "秒數": 6.0}
    assert turns.trim_teacher_heads(ts, by) == []          # 再跑一次不會再動


def test_trim_conditions():
    for kw in (
        {"head": (("s2", 10.0, 11.5, "嗯", "老師", 0.81),)},                      # 太短（不到 2 秒）
        {"head": (("s2", 10.0, 16.0, "我先講", "老師", 0.6),)},                    # 相似度不夠高
        {"head": (("s2", 10.0, 16.0, "我先講", "不確定", 0.81),)},                  # 不是聲紋判老師
        {"rest_labels": ("老師", "老師", "不是老師", "老師")},                      # 後面大多還是老師
        {"confirmed": True},                                                      # 已確認過
        {"prev_who": "學員"},                                                     # 前一段不是老師
    ):
        ts, by, _ = _data(**kw)
        before = copy.deepcopy(ts)
        assert turns.trim_teacher_heads(ts, by) == [], kw
        assert ts == before, kw
    ts, by, _ = _data()
    ts[1]["校對稿"] = "人改過的字"                                                   # 校對稿被人改過
    assert turns.trim_teacher_heads(ts, by) == []
    ts, by, _ = _data(rest_labels=())                                              # 整段都是老師聲紋（沒有剩下的）→ 不動，交給整段提醒
    assert turns.trim_teacher_heads(ts, by) == []


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
