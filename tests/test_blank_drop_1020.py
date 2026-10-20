"""10-20 總審查復審（批次 1020-1）：要念的整句拿掉後沒有字、計畫排不出來時的防呆。

1. 隱私：老師要念的一整句只有替代詞留空的敏感詞，拿掉後沒有字（或只剩標點）→ 以前生成程式會改念原文
   （`tts.fill_teacher_text`）＝把那個詞念出來。現在計畫排出來時就不生成、整句改成消音（人設定替代詞留空＝人決定刪），
   第 5 步「需留意」列一行「整句拿掉」讓人聽；學員重念稿同樣情況（拿掉後整格沒有字）也不生成、整格消音、列一行。
2. 防呆：`execute.name_plan_changed` 遇到現在排不出計畫（名字候選檔不在、但計畫檔還在；排的時候出錯）→ 當成沒改，
   不會每次都判要重新組裝。

全部是合成資料（假名＋自編句子）。獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_blank_drop_1020.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_name_plan_reassemble as R  # noqa: E402 — 「組裝過」的假輸出
import test_sensitive_drop_1020 as S  # noqa: E402 — 假工作區＋敏感詞（替代詞留空）

from bookclub import execute, finalcheck, nameplan, students, tts  # noqa: E402
from bookclub import turns as turns_mod  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

WORD = S.WORD


def _make() -> Path:
    """老師那一句只有敏感詞（「秘密基地。」）；學員1 第一段每一句都只有敏感詞（不改校對稿，照逐字稿）。"""
    w = S._make()
    sp = json.loads((w / "說話者判斷.json").read_text(encoding="utf-8"))
    s = next(x for x in sp["sentences"] if x["id"] == "00_019")
    s["text"] = f"{WORD}。"
    (w / "說話者判斷.json").write_text(json.dumps(sp, ensure_ascii=False), encoding="utf-8")
    names = wd.read_json(wd.names_path(w))
    names["candidates"][0].update({"sentence": s["text"], "位置": "句首", "start": s["start"], "end": s["start"] + 0.8})
    wd.write_json(wd.names_path(w), names)
    td = json.loads(turns_mod.turns_path(w).read_text(encoding="utf-8"))
    t1 = next(x for x in td["段落"] if x["說話者"] == "學員1")
    t1["校對稿"] = t1.get("原文", "")   # 沒改稿（S._make 改過，這裡還原）
    turns_mod.turns_path(w).write_text(json.dumps(td, ensure_ascii=False), encoding="utf-8")
    sp = json.loads((w / "說話者判斷.json").read_text(encoding="utf-8"))
    for x in sp["sentences"]:
        if x["id"] in t1["句子"]:
            x["text"] = f"{WORD}。"
    (w / "說話者判斷.json").write_text(json.dumps(sp, ensure_ascii=False), encoding="utf-8")
    return w


def test_blank_text():
    assert nameplan.blank_text("") and nameplan.blank_text(" ，。 ") and nameplan.blank_text(None)
    assert not nameplan.blank_text("嗯") and not nameplan.blank_text("A")


def test_teacher_blank_sentence_muted_not_generated():
    w = _make()
    try:
        plan = nameplan.compute_plan(w)
        assert not any(1 in g.get("候選", []) for g in plan["生成"]), plan["生成"]
        assert all(not nameplan.blank_text(g["text"]) for g in plan["生成"]), plan["生成"]
        m = [x for x in plan["消音"] if x.get(nameplan.BLANK_KEY)]
        assert m and m[0]["候選"] == 1, plan["消音"]
        cand = wd.read_json(wd.names_path(w))["candidates"][0]
        assert m[0]["start"] <= cand["start"] and cand["end"] <= m[0]["end"], (m, cand)   # 整句蓋住那個詞
        # 生成程式拿到的句子裡不會有空的（以前空的會改念原文＝念出敏感詞）
        for it in tts.teacher_plan_items(w, plan["生成"]):
            assert WORD not in it["text"], it
    finally:
        S._cleanup()


def test_student_blank_chunk_muted_not_generated():
    w = _make()
    try:
        items, _ = students.build_items(w)
        assert all(WORD not in it["text"] and not nameplan.blank_text(it["text"]) for it in items), [it["text"] for it in items]
        em = [m for m in students.empty_chunks(w) if m.get(nameplan.BLANK_KEY)]
        assert em and "拿掉後沒有了" in em[0]["原因"], students.empty_chunks(w)
    finally:
        S._cleanup()


def test_step5_attention_lists_blank_drops():
    w = _make()
    try:
        rows = execute.blank_drop_rows(w)
        kinds = {r["key"].split(":")[1] for r in rows}
        assert kinds == {"老師", "學員重念"} and all(r["類別"] == execute.BLANK_KIND for r in rows), rows
        assert any(r["第3步"].startswith("名字:") for r in rows) and any(r["第3步"].startswith("學員段落:") for r in rows)
        snap = execute.skipped_snapshot(w)
        assert sum(1 for r in snap["列"] if r["類別"] == execute.BLANK_KIND) == len(rows)
        log = {"片段": None, "紀錄": [], "範圍": [0.0, 180.0]}
        placed = finalcheck.place_attention(snap["列"], log)
        assert sum(1 for r in placed if r["類別"] == execute.BLANK_KIND) == len(rows)
        assert execute.BLANK_KIND in [k for k, _n, _x in finalcheck.ATTENTION_KINDS]
    finally:
        S._cleanup()


def test_name_plan_changed_when_plan_cannot_be_computed():
    """名字候選檔不在、但計畫檔還在（或排的時候出錯）：判斷幾次都說做過了，不會每次都判要重組。"""
    for fingerprint in (True, False):
        w, undo, _sid = R._setup("換成代號", "克洛伊")
        try:
            R._assembled(w, fingerprint=fingerprint)
            assert execute.render_done(w, R.TAG, ["sw"])[0]
            real = nameplan.compute_plan
            nameplan.compute_plan = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("假的錯誤"))
            try:
                for _ in range(2):
                    assert not execute.name_plan_changed(w, R.TAG, wd.read_json(w / "輸出" / f"輸出摘要_{R.TAG}.json"))
            finally:
                nameplan.compute_plan = real
            wd.names_path(w).unlink()   # 計畫檔還在
            assert nameplan.plan_path(w).is_file() and nameplan.current_plan(w) is None
            for _ in range(2):
                done, why = execute.render_done(w, R.TAG, ["sw"])
                assert done, (fingerprint, why)
        finally:
            undo()


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
