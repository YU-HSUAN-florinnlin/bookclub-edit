"""10-20 總審查（批次 1020-1）：名字的處理方式改了，第 4 步「組裝」要看得出來、要重新組裝。

以前 `execute.render_done` 只比檔案修改時間：舊工作區升級（例如 #250 起名冊上的人選不用處理改成照原聲）、
或在 ③ 改了人名決定，計畫內容變了、檔案時間看不出來，步驟表說都做過了，成品還是舊做法（名字被拿掉）。

測的事：
- 舊做法（名字拿掉）的計畫檔＋新程式算出照原聲 → 判要重新組裝（老師那份比計畫檔；學員那份舊摘要沒指紋時比剪輯決策）
- ③ 把有英文名的人改成「不用處理」→ 判要重新組裝
- 什麼都沒改 → 不重新組裝（重複判斷也一樣；計畫只是排序不同也不算改了）

全部是合成資料（假名＋自編句子）。獨立可跑：.venv/bin/python tests/test_name_plan_reassemble.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_people_rescan as T  # noqa: E402 — 假工作區、假名冊（小美：美美；阿明）

from bookclub import execute, nameplan, personnames, render, review, roomtone, studentnames  # noqa: E402
from bookclub import turns as turns_mod  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

TAG = "0-3"


def _setup(how: str, code: str | None = None):
    """學員2 保留原聲、講到美美（學員名字候選照 find 的格式給）；老師句子有小美。③ 小美選 how。
    回傳 (工作區, 還原 find 的函式, 學員名字候選 id)。"""
    (T._DATA / "名冊.csv").write_text(T._ROSTER, encoding="utf-8")
    w = T._make()
    td = json.loads(turns_mod.turns_path(w).read_text(encoding="utf-8"))
    t2 = next(x for x in td["段落"] if x["說話者"] == "學員2")
    dec = review.load_decisions(w)
    dec["學員聲音"] = {"學員2": "保留原聲"}
    review._save_decisions(w, dec)
    sp = json.loads((w / "說話者判斷.json").read_text(encoding="utf-8"))
    s2 = next(x for x in sp["sentences"] if x["id"] == t2["句子"][0])
    c = {"matched_text": "美美", "name": "美美", "canonical": "小美", "代號": "", "sentence_id": s2["id"], "sentence": s2["text"],
         "start": s2["start"] + 0.2, "end": s2["start"] + 0.6, "學員": "學員2", "位置": "句中", "信心": "高",
         "比對層級": "精確", "敏感詞": False}
    c["id"] = studentnames.cand_id(c)
    real = studentnames.find
    studentnames.find = lambda workdir, force=False: {"保留原聲學員": ["學員2"], "candidates": [dict(c)]}
    personnames.decide(w, "小美", how, code)
    personnames.wait_rescan(w)
    return w, (lambda: setattr(studentnames, "find", real)), c["id"]


def _assembled(w: Path, *, fingerprint: bool = True, stu_actions: list[dict] | None = None) -> Path:
    """照現在的做法「組裝過」：計畫檔寫成現在的、輸出摘要（驗證通過）、剪輯決策、比所有輸入都新的成品。"""
    nameplan.refresh_plan(w)
    out = w / "輸出"
    out.mkdir(exist_ok=True)
    summ = {"輸出": {"sw": {"驗證": {"通過": True}}}, "底噪挑法": roomtone.METHOD, "組裝做法版本": render.ASSEMBLE_VERSION}
    if fingerprint:   # 這版起組裝會記；0.2.2b3 以前的摘要沒有這兩欄
        summ["老師名字計畫指紋"] = nameplan.plan_key(wd.read_json(nameplan.plan_path(w), default=None))
        summ["學員名字計畫指紋"] = nameplan.plan_key(studentnames.plan(w))
    wd.write_json(out / f"輸出摘要_{TAG}.json", summ)
    if stu_actions is None:   # 照現在的學員名字計畫處理過（跟組裝放進剪輯決策的一樣）
        sp = studentnames.plan(w)
        stu_actions = [{"類型": "學員名字消音", "start": m["start"], "end": m["end"], "id": m["id"], "候選": [m["id"]]}
                       for m in sp["消音"]] + [{"類型": "學員名字換代號", "start": g["slot"][0], "end": g["slot"][1],
                                                "id": g["id"], "候選": g["候選"]} for g in sp["生成"]]
    wd.write_json(out / f"剪輯決策_{TAG}.json", {"範圍": [0.0, 180.0], "刪除": [], "動作": stu_actions})
    time.sleep(0.02)
    p = out / f"成品_v1_{TAG}_sw.mp4"
    p.write_bytes(b"x")
    return p


def _fresh(p: Path) -> None:
    """成品時間調到最新：只看計畫內容，不讓檔案修改時間的判斷搶先。"""
    t = time.time() + 60
    os.utime(p, (t, t))


def _mei_cands(w: Path) -> list[tuple[int, dict]]:
    return [(k, x) for k, x in enumerate(wd.read_json(wd.names_path(w))["candidates"], start=1) if x.get("canonical") == "小美"]


def _old_style_plan(w: Path) -> dict:
    """1016-3 的舊做法：小美（③ 選不用處理）照樣消音。"""
    old = wd.read_json(nameplan.plan_path(w))
    idx = _mei_cands(w)
    assert idx
    old["消音"] = (old.get("消音") or []) + [{"候選": k, "start": x["start"], "end": x["end"]} for k, x in idx]
    return old


def test_old_plan_removed_names_now_kept():
    """舊工作區升級：計畫檔是舊做法（小美消音），新程式算出照原聲 → 要重新組裝；說明寫白話。"""
    for fingerprint in (False, True):   # 0.2.2b3 的舊摘要（沒指紋）／這版組裝過、記著舊計畫的指紋
        w, undo, sid = _setup("不用處理")
        try:
            p = _assembled(w, fingerprint=False)
            assert execute.render_done(w, TAG, ["sw"])[0]
            wd.write_json(nameplan.plan_path(w), _old_style_plan(w))
            if fingerprint:
                p = _assembled(w, fingerprint=True)   # 會先重排計畫檔，指紋要記舊的
                wd.write_json(nameplan.plan_path(w), _old_style_plan(w))
                summ = wd.read_json(w / "輸出" / f"輸出摘要_{TAG}.json")
                summ["老師名字計畫指紋"] = nameplan.plan_key(wd.read_json(nameplan.plan_path(w)))
                wd.write_json(w / "輸出" / f"輸出摘要_{TAG}.json", summ)
            _fresh(p)
            done, why = execute.render_done(w, TAG, ["sw"])
            assert not done and why == execute.NAME_PLAN_CHANGED, (fingerprint, why)
            assert "名字的處理方式改了" in why and "生成好的聲音沿用" in why
            # 步驟表（current_steps）跟「開始執行」用同一套判斷
            st = execute.current_steps(w, 0.0, 180.0, ["sw"])["組裝"]
            assert st["做好了"] is False and st["說明"] == execute.NAME_PLAN_CHANGED, st
        finally:
            undo()


def test_old_summary_student_name_muted_now_kept():
    """舊版摘要沒有學員名字計畫指紋：成品把保留原聲學員講到的美美消音了，新程式照原聲 → 要重新組裝；
    成品本來就沒處理（跟現在一樣）→ 不重新組裝。"""
    w, undo, sid = _setup("不用處理")
    try:
        assert not studentnames.plan(w)["消音"]   # 照原聲
        p = _assembled(w, fingerprint=False, stu_actions=[
            {"類型": "學員名字消音", "start": 1.0, "end": 1.5, "id": sid, "學員": "學員2", "候選": [sid]}])
        _fresh(p)
        done, why = execute.render_done(w, TAG, ["sw"])
        assert not done and why == execute.NAME_PLAN_CHANGED, why
        p = _assembled(w, fingerprint=False, stu_actions=[])
        _fresh(p)
        assert execute.render_done(w, TAG, ["sw"])[0]
    finally:
        undo()


def test_step3_code_to_not_needed_requires_reassemble():
    """③ 小美本來換成英文名，組裝完改成「不用處理」→ 要重新組裝（老師、保留原聲學員兩份計畫都變了）。"""
    w, undo, sid = _setup("換成代號", "克洛伊")
    try:
        before = nameplan.plan_key(nameplan.current_plan(w))
        stu_before = nameplan.plan_key(studentnames.plan(w))
        p = _assembled(w)
        assert execute.render_done(w, TAG, ["sw"])[0], execute.render_done(w, TAG, ["sw"])
        personnames.decide(w, "小美", "不用處理")
        personnames.wait_rescan(w)
        assert nameplan.plan_key(nameplan.current_plan(w)) != before
        assert nameplan.plan_key(studentnames.plan(w)) != stu_before
        _fresh(p)
        done, why = execute.render_done(w, TAG, ["sw"])
        assert not done and why == execute.NAME_PLAN_CHANGED, why
        # 重新組裝後（計畫檔、指紋都換成新的）就不再判要重組
        p = _assembled(w)
        assert execute.render_done(w, TAG, ["sw"])[0]
    finally:
        undo()


def test_nothing_changed_no_reassemble():
    """什麼都沒改：判斷幾次都說做過了（新舊摘要都一樣）；計畫檔只是每一筆順序不同、或只改了略過
    （開始執行重排時重寫、檔案時間比成品新）也不算改了做法。"""
    for fingerprint in (True, False):
        w, undo, sid = _setup("換成代號", "克洛伊")
        try:
            _assembled(w, fingerprint=fingerprint)
            for _ in range(3):
                done, why = execute.render_done(w, TAG, ["sw"])
                assert done, (fingerprint, why)
                assert execute.current_steps(w, 0.0, 180.0, ["sw"])["組裝"]["做好了"], fingerprint
        finally:
            undo()
    w, undo, sid = _setup("換成代號", "克洛伊")
    try:
        _assembled(w)
        plan = wd.read_json(nameplan.plan_path(w))
        for k in ("生成", "消音"):
            plan[k] = list(reversed(plan.get(k) or []))
        plan["略過"] = []   # 不影響成品的部分不算
        time.sleep(0.02)
        wd.write_json(nameplan.plan_path(w), plan)   # 計畫檔比成品新（不調成品時間）
        done, why = execute.render_done(w, TAG, ["sw"])
        assert done, why
    finally:
        undo()


def test_plan_key_ignores_order_and_timestamps():
    a = {"生成": [{"id": "N1", "text": "甲"}, {"id": "N2", "text": "乙"}], "消音": [{"候選": 3, "start": 1.0, "end": 2.0}]}
    b = {"生成": [{"id": "N2", "text": "乙", "更新時間": "x"}, {"id": "N1", "text": "甲"}],
         "消音": [{"候選": 3, "start": 1.0, "end": 2.0}], "略過": [{"候選": 9}]}
    assert nameplan.plan_key(a) == nameplan.plan_key(b)
    assert nameplan.plan_key(a) != nameplan.plan_key({**a, "消音": []})
    assert nameplan.plan_key(None) == nameplan.plan_key({})


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
