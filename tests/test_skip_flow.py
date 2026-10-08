"""10-08 宇軒（流程簡化）：第 3 步一鍵照建議通過、開始前只擋 ② 學員是誰與硬體、第 4 步總檢查預設略過、
第 5 步「⚠️ 需留意」、分頁徽章只寫「完成 x／n」。

- 第 3 步：一鍵通過的排除規則（純函式）與摘要；假工作區上真的按一次（寫檔、再按一次不重複）
- precheck：② 沒做擋；名字換不了代號、人名清單沒跑成功、名字還沒代號只提醒；硬碟不夠擋
- 第 4 步：總檢查有沒處理的列也能開始（網頁伺服器、命令列 run_execute 同一套）；開始時記下沒處理的列
- 第 5 步：需留意的扣除（被重念／剪掉蓋到的不列）、成品時間換算、名字置頂、文字類只扣剪掉；舊工作區沒有紀錄
- 網頁（node 跑純函式）：第 3 步按鈕狀態與摘要、第 4 步收合摘要、第 5 步徽章、需留意分頁、輸出確認視窗
不跑模型、不碰真的工作區。獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_skip_flow.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_execute as TE  # noqa: E402 — 假工作區、假名冊（BOOKCLUB_DATA_DIR）

from bookclub import execute, finalcheck, review  # noqa: E402
from bookclub import turns as turns_mod  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

WEB = REPO_ROOT / "bookclub" / "web"
NODE = shutil.which("node")
_ESC = """const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
"""


def _fn(src: str, name: str) -> str:
    m = re.search(rf"^(async )?function {name}\(.*?^\}}\n", src, re.S | re.M)
    assert m, name
    return m.group(0)


def _const(src: str, name: str) -> str:
    m = re.search(rf"^const {name} = [^\n]*\n", src, re.M)   # 一行的常數
    assert m, name
    return m.group(0)


def _node(code: str):
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _who_done(w: Path) -> None:
    for person in turns_mod.page_data(w)["學員"]:
        turns_mod.set_real_name(w, person, turns_mod.UNKNOWN_REAL)


# ---------- 第 3 步：一鍵照建議通過 ----------

def test_bulk_skip_rules_pure():
    words = ["王小明"]
    stu = {"類型": "學員段落", "id": "T1", "建議": {"做法": "通過"}, "建議稿": "Amy 說的"}
    assert review.bulk_skip_reason(stu, set(), words) is None
    cases = [
        ({**stu, "建議": {"做法": "刪除這段"}}, "建議剪掉"),
        ({**stu, "建議稿": "王小明說的"}, "還有本名"),
        ({**stu, "含本名": True}, "還有本名"),
        ({**stu, "代號改過": {"舊": "A", "新": "B"}}, "代號改過"),
        ({**stu, "人工新增": True}, "人工新增"),
        ({**stu, "手動標記": True}, "人工新增"),
        ({**stu, "第5步退回": ["念錯"]}, "第5步退回"),
        ({"類型": "重疊", "id": "O1", "建議": {"做法": "只留學員"}, "還缺": "還沒選學員是誰"}, "重疊缺資料"),
        ({"類型": "重疊", "id": "O1", "建議": {}}, "沒有建議"),
        ({"類型": "名字", "id": "3", "做法": "整句換掉", "整句": {"換成代號": "Amy 好"}}, "名字換不了代號"),   # stuck 裡有 3
        ({"類型": "名字", "id": "4", "做法": "整句換掉", "整句": None}, "名字換不了代號"),
        ({"類型": "名字", "id": "5", "做法": "整句換掉", "整句": {"換成代號": "Amy", "字太少": True}}, "字太少"),
        ({"類型": "名字", "id": "6", "做法": "整句換掉", "整句": {"換成代號": "Amy", "實際會念": "王小明好"}}, "還有本名"),
        ({"類型": "名字", "id": "7", "做法": "整句換掉", "老師整段": True, "整句": {"換成代號": "x"}}, "人工新增"),
        ({"類型": "名字", "id": "8", "做法": "整句換掉", "分開決定過": True, "整句": {"換成代號": "x"}}, "分開決定過"),
        ({"類型": "名字", "id": "9", "同一張卡候選": ["9", "3"], "做法": "整句換掉", "整句": {"換成代號": "x"}}, "名字換不了代號"),
        ({"類型": "刪除段落", "id": "S1", "來源": "建議", "建議": {"做法": "刪除"}}, "建議剪掉"),
    ]
    for it, want in cases:
        assert review.bulk_skip_reason(it, {"3"}, words) == want, (it, want)
    # 可以通過的：短句建議剪掉但人選了留下、直接消音的名字（不看要念的字）、保留原聲學員的名字、有建議的重疊
    assert review.bulk_skip_reason({**stu, "建議": {"做法": "刪除這段"}, "短句保留": True}, set(), words) is None
    assert review.bulk_skip_reason({"類型": "名字", "id": "4", "做法": "直接消音", "整句": None}, set(), words) is None
    assert review.bulk_skip_reason({"類型": "學員名字", "id": "SN1", "做法": "直接消音"}, set(), words) is None
    assert review.bulk_skip_reason({"類型": "重疊", "id": "O2", "建議": {"做法": "只留老師"}}, set(), words) is None
    # 分組：處理好的不列；摘要依原因的順序
    items = [stu, {**stu, "id": "T2", "已確認": True}, {**stu, "id": "T3", "不用處理": "剪掉"},
             {**stu, "id": "T4", "建議稿": "王小明"}, {"類型": "刪除段落", "id": "S1", "來源": "建議"}]
    ok, left = review.bulk_pass_split(items, set(), words)
    assert [x["id"] for x in ok] == ["T1"] and [(x["id"], why) for x, why in left] == [("T4", "還有本名"), ("S1", "建議剪掉")]
    s = review.bulk_summary(len(ok), left)
    assert s["通過"] == 1 and s["還要看"] == 2 and [k["原因"] for k in s["依類型"]] == ["還有本名", "建議剪掉"]
    assert s["說明"].startswith("通過了 1 張，還有 2 張要看：要念的文字裡還有本名 1 張；") and "不擋「開始 AI 修改」" in s["說明"]
    assert review.bulk_summary(3, [])["說明"] == "通過了 3 張"


def test_pass_all_on_fake_workdir():
    w = TE._fresh()
    before = review.page_data(w)
    assert before["進度"]["已確認"] == 0
    r = review.pass_all(w)
    # 假工作區：3 段學員、2 個名字、1 處重疊通過；2 段建議刪除（會連畫面剪掉）不幫忙按
    assert r["通過"] == 6 and r["還要看"] == 2 and r["依類型"] == [{"原因": "建議剪掉", "說明": review.BULK_SKIP_TEXT["建議剪掉"], "筆數": 2}]
    after = review.page_data(w)
    done = {f"{x['類型']}:{x['id']}" for x in after["項目"] if review.item_done(x)}
    assert all(not k.startswith("刪除段落:") for k in done) and len(done) == 6
    assert all(x["已確認"] for x in after["項目"] if x["類型"] in ("學員段落", "名字", "重疊"))
    t = next(x for x in wd.read_json(turns_mod.turns_path(w))["段落"] if x["id"] == "T005")
    assert t["已確認"] and t["校對稿"]                                # 學員段落：照建議稿存、標通過
    assert not any(x["決定"] for x in after["刪除建議"])              # 建議刪除沒被動到（沒選＝不剪）
    again = review.pass_all(w)                                        # 再按一次：沒有可以通過的
    assert again["通過"] == 0 and again["還要看"] == 2


def test_pass_all_reverts_name_that_cannot_be_replaced():
    """名字換不了代號：一開始就不幫忙按；人工新增的（老師整段、人工補的名字）也不按。"""
    w = TE._fresh()
    nid = review.manual_edit(w, {"類型": "名字", "start": 84.12, "end": 84.8, "代號": "Tom", "名字": "逐字稿沒有的字"})["id"]
    r = review.pass_all(w)
    item = {f"{x['類型']}:{x['id']}": x for x in review.page_data(w)["項目"]}[f"名字:{nid}"]
    assert not item["已確認"] and r["還要看"] >= 1
    assert any(k["原因"] in ("人工新增", "名字換不了代號") for k in r["依類型"])


# ---------- precheck：只剩 ② 與硬體 ----------

def _voices():
    voices = TE._DATA / "聲線"
    voices.mkdir(exist_ok=True)
    for sex in ("男", "女"):
        (voices / f"{sex}聲_暫定.wav").write_bytes(b"RIFF")
        (voices / f"{sex}聲_暫定.txt").write_text("假的", encoding="utf-8")


def test_precheck_blocks_only_who_and_disk():
    _voices()
    w = TE._fresh()
    pre = execute.precheck(w)
    assert not pre["可以開始"] and len(pre["缺"]) == 1 and "② 學員是誰" in pre["缺"][0]
    assert review.students_pending(w) and review.page_data(w)["②還沒做"]
    _who_done(w)
    assert review.students_pending(w) == [] and review.page_data(w)["②還沒做"] == []
    pre = execute.precheck(w)
    assert pre["可以開始"] and pre["硬碟"]["可用GB"] is not None
    # 名字換不了代號、人名清單沒跑成功：只提醒
    review.manual_edit(w, {"類型": "名字", "start": 84.12, "end": 84.8, "代號": "Tom", "名字": "逐字稿沒有的字"})
    from bookclub import personnames

    personnames.people_path(w).unlink()
    pre = execute.precheck(w)
    assert pre["可以開始"], pre["缺"]
    assert any("換不了代號（不擋）" in x for x in pre["提醒"]) and any("人名清單沒跑成功（不擋）" in x for x in pre["提醒"])
    # 硬碟不夠：擋
    old = execute.default_limits
    execute.default_limits = lambda: {"開始前硬碟GB": 10 ** 9, "硬碟GB": 1, "swapGB": 8.5}
    try:
        pre = execute.precheck(w)
        assert not pre["可以開始"] and any("硬碟可用空間" in x for x in pre["缺"])
    finally:
        execute.default_limits = old


def test_start_not_blocked_by_final_check_web_and_cli():
    """網頁伺服器與命令列：總檢查有沒處理的列（請看一眼也沒按）照樣開始；開始時記下沒處理的列。"""
    from bookclub import epcodes
    from bookclub import server as srv

    _voices()
    w = TE._fresh()
    _who_done(w)
    review.manual_edit(w, {"類型": "名字", "start": 84.12, "end": 84.8, "代號": "Tom", "名字": "逐字稿沒有的字"})   # 名字換不了代號
    fc = execute.final_check(w)
    assert fc["一定要處理"] and not fc["可以開始"] and fc["預設略過"]["一定要處理"] >= 1
    # 命令列（run_execute，cli 也是呼叫它）：過了前置檢查與總檢查，停在假的 sync（不跑模型）
    class Passed(Exception):
        pass

    def stop(_w):
        raise Passed()

    old_sync = epcodes.sync
    epcodes.sync = stop
    try:
        try:
            execute.run_execute(w, log=lambda m: None)
            raise AssertionError("不該擋")
        except Passed:
            pass
    finally:
        epcodes.sync = old_sync
    # 網頁伺服器：start_execute 不看總檢查
    httpd = srv.BookclubServer(("127.0.0.1", 0), srv.Handler, workdir=w, video=None)
    started = []
    old_run, old_key = execute.run_execute, srv.groq_key_ready
    execute.run_execute = lambda wk, **kw: started.append(kw) or {}
    srv.groq_key_ready = lambda: True
    try:
        r = httpd.start_execute({"start": None, "end": None, "methods": ["sw"]})
        assert r["started"], r
        for _ in range(100):
            if not httpd.exec_running():
                break
            import time
            time.sleep(0.02)
        assert len(started) == 1
    finally:
        execute.run_execute, srv.groq_key_ready = old_run, old_key
        httpd.server_close()
    assert "final_check" not in (REPO_ROOT / "bookclub" / "cli.py").read_text(encoding="utf-8")


def test_run_execute_records_skipped_rows():
    """組裝那一步要跑時，開始當下總檢查沒處理的列記在執行進度；只跑生成就沿用上一次的。"""
    w = TE._fresh()
    review.manual_edit(w, {"類型": "名字", "start": 84.12, "end": 84.8, "代號": "Tom", "名字": "逐字稿沒有的字"})   # 名字換不了代號
    calls = []
    runners, checks = TE._fake(calls)
    prog = execute.run_execute(w, only_steps=["組裝"], runners=runners, checks=checks, skip_precheck=True, log=lambda m: None)
    snap = prog[execute.SKIPPED_FIELD]
    assert snap["沒處理"] == snap["一定要處理"] + snap["請看一眼"] and snap["一定要處理"] >= 1 and snap["時間"]
    assert all(set(r) == set(execute.SKIPPED_ROW_KEYS) for r in snap["列"])   # 只記鍵、類別、時間、卡片（不記說明）
    assert any(r["類別"] == execute.KIND_NAME for r in snap["列"])
    assert wd.read_json(execute.progress_path(w))[execute.SKIPPED_FIELD] == snap
    prog2 = execute.run_execute(w, only_steps=["老師名字"], runners=runners, checks=checks, skip_precheck=True, log=lambda m: None)
    assert prog2[execute.SKIPPED_FIELD] == snap


def test_skipped_rows_pure():
    fc = {"一定要處理": [{"key": "段落外:T1:1.0", "類別": "段落外", "start": 1.0, "end": 2.0, "第3步": "學員段落:T1", "說明": "王小明", "處理好": False},
                        {"key": "聲紋:S1", "類別": "聲紋", "start": 3.0, "end": 4.0, "第3步": None, "處理好": True}],
          "請看一眼": [{"key": "剪掉:D1", "已看過": False}, {"key": "消音:M1", "已看過": True}]}
    s = execute.skipped_rows(fc)
    assert s == {"一定要處理": 1, "請看一眼": 1, "沒處理": 2,
                 "列": [{"key": "段落外:T1:1.0", "類別": "段落外", "start": 1.0, "end": 2.0, "第3步": "學員段落:T1"}]}


# ---------- 第 5 步：需留意 ----------

def test_place_attention_pure():
    # 成品：0–10 原樣，10–20 剪掉，20–40 接在後面（成品 10–30）
    plist = [{"src": [0.0, 10.0], "freeze": 0.0}, {"src": [20.0, 40.0], "freeze": 0.0}]
    recs = [{"類型": "刪除", "原片": [10.0, 20.0], "動到聲音": True},
            {"類型": "學員重念", "原片": [22.0, 25.0], "動到聲音": True}]
    log = {"片段": plist, "紀錄": recs, "範圍": [0.0, 40.0]}
    rows = [
        {"key": "聲紋:S1", "類別": "聲紋", "start": 2.0, "end": 4.0, "第3步": None},                # 整段留著
        {"key": "段落外:T1:21", "類別": "段落外", "start": 21.0, "end": 26.0, "第3步": "學員段落:T1"},   # 中間被重念蓋掉
        {"key": "沒有字:12.0", "類別": "沒有字", "start": 12.0, "end": 15.0, "第3步": None},          # 整段剪掉 → 不列
        {"key": "聲紋:S2", "類別": "聲紋", "start": 23.0, "end": 24.0, "第3步": None},              # 整段被重念蓋掉 → 不列
        {"key": "名字:4", "類別": "名字換不了代號", "start": 30.0, "end": 31.0, "第3步": "名字:4"},    # 名字：置頂
        {"key": "字太少:G1", "類別": "字太少", "start": 22.0, "end": 25.0, "第3步": "名字:2"},        # 文字類：重念不扣
        {"key": "英文代號:x", "類別": "英文代號", "start": 11.0, "end": 12.0, "第3步": "學員段落:T9"},  # 文字類：剪掉的扣
        {"key": "重疊:O1", "類別": "重疊缺東西", "start": 39.0, "end": 45.0, "第3步": "重疊:O1"},     # 超出範圍的部分不算
        {"key": "聲紋:S3", "類別": "聲紋", "start": 5.0, "end": 5.2, "第3步": None},                # 不到 0.3 秒 → 不列
    ]
    got = finalcheck.place_attention(rows, log)
    assert [r["鍵"] for r in got] == ["名字:4", "段落外:T1:21", "聲紋:S1", "重疊:O1", "字太少:G1"]
    name, out, voice, ov, short = got
    assert name["名字"] and name["成品"] == [20.0, 21.0]
    assert out["剩下"] == [[21.0, 22.0], [25.0, 26.0]] and out["剩下秒"] == 2.0
    assert out["成品段"] == [[11.0, 12.0], [15.0, 16.0]] and out["成品"] == [11.0, 16.0]
    assert voice["成品"] == [2.0, 4.0] and not voice["名字"]
    assert ov["剩下"] == [[39.0, 40.0]] and ov["成品"] == [29.0, 30.0]
    assert short["剩下"] == [[22.0, 25.0]]
    assert finalcheck.place_attention(rows, None) == []


def test_attention_rows_old_and_new_workdir():
    w = TE._fresh()
    log = {"片段": None, "紀錄": [], "範圍": [0.0, 180.0]}
    old = finalcheck.attention_rows(w, log)                          # 舊工作區：沒有執行進度、沒有紀錄
    assert old == {"有紀錄": False, "列": [], "總數": 0, "名字": 0, "第4步沒處理": None}
    wd.write_json(execute.progress_path(w), {"開始時間": "x"})       # 舊工具的執行進度（沒有這個欄位）
    assert not finalcheck.attention_rows(w, log)["有紀錄"]
    wd.write_json(execute.progress_path(w), {execute.SKIPPED_FIELD: {"沒處理": 3, "列": [
        {"key": "名字:4", "類別": "名字換不了代號", "start": 30.0, "end": 31.0, "第3步": "名字:4"},
        {"key": "聲紋:S1", "類別": "聲紋", "start": 2.0, "end": 4.0, "第3步": None}]}})
    att = finalcheck.attention_rows(w, log)
    assert att["有紀錄"] and att["總數"] == 2 and att["名字"] == 1 and att["第4步沒處理"] == 3
    assert finalcheck.attention_gaps(att) == {"需留意": 2, "需留意名字": 1, "需留意說明": "需留意 2 處（含名字 1 處）還沒聽過"}
    assert finalcheck.attention_gaps({})["需留意說明"] == ""


def test_inspect_prints_attention_numbers_not_names():
    from contextlib import redirect_stdout
    import io

    import test_finalcheck as TF

    from bookclub import safeview

    w = TF._workdir()
    wd.write_json(execute.progress_path(w), {execute.SKIPPED_FIELD: {"沒處理": 2, "列": [
        {"key": "名字:4", "類別": "名字換不了代號", "start": 1.0, "end": 2.0, "第3步": "名字:4"}]}})
    buf = io.StringIO()
    with redirect_stdout(buf):
        safeview.main([str(w), "成品檢查"])
    text = buf.getvalue()
    assert "需留意 1 處（名字換不了代號 1）；第 4 步執行時總檢查沒處理 2 列" in text
    assert "需留意 鍵=名字:4  類別=名字換不了代號" in text


# ---------- 網頁（node 跑純函式） ----------

def test_web_step3_buttons():
    rv = (WEB / "review.js").read_text(encoding="utf-8")
    assert 'id="rv-go4" disabled>開始 AI 修改<' in rv and "全部通過，開始 AI 修改" not in rv
    assert "/api/execute/finalcheck" not in rv                       # 開始前不再先查總檢查
    side = _fn(rv, "rvRenderPrepSide")
    assert side.index('id="rv-start"') < side.index("rvPassAllHtml()")   # 跟「開始逐筆看」同一列
    assert "rvPassAllHtml()" in _fn(rv, "rvRenderList")              # 逐筆清單頂端也有
    if not NODE:
        return
    got = _node(_ESC + _fn(rv, "rvGo4State") + _const(rv, "RV_PREP_DEFAULT") + _fn(rv, "rvGo4Notes") + _fn(rv, "rvPassAllMsg") + """
      console.log(JSON.stringify({
        who: rvGo4State(["學員1 還沒選本名"], 5, false), left: rvGo4State([], 5, false), none: rvGo4State([], 0, false),
        ro: rvGo4State([], 0, true),
        notes: rvGo4Notes(3, {"學員": true, "刪除": true}), notes0: rvGo4Notes(0, {"刪除": true, "學員": true, "名字": true, "保留原聲": true}),
        msg: rvPassAllMsg({"通過": 6, "還要看": 2, "依類型": [{"說明": "建議剪掉這段", "筆數": 2}]}) }));""")
    assert got["who"]["disabled"] and "② 學員是誰" in got["who"]["title"]
    assert not got["left"]["disabled"] and "還有 5 張卡片沒看" in got["left"]["title"]
    assert not got["none"]["disabled"] and got["ro"]["disabled"]
    assert got["notes"][0].startswith("還有 3 張卡片沒看") and "③ 名冊上的人已自動換代號" in got["notes"][1] and "① " not in got["notes"][1]
    assert got["notes0"] == []
    assert got["msg"]["title"] == "通過了 6 張" and got["msg"]["lines"] == ["建議剪掉這段：2 張"] and "不擋" in got["msg"]["note"]


def test_web_step4_folded_default_skip():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    body = _fn(app, "renderExecuteBody")
    assert 'const execBlocked = running || !pre["可以開始"];' in body and '<button id="btnExec" ${execBlocked ? "disabled" : ""}>' in body
    fn = _fn(app, "finalCheckHtml")
    assert fn.count('<details class="fc-fold"') == 2 and "展開查看" in fn
    assert "fc-back3" in _fn(app, "fcKeepHtml") and "rv-warnline fc-keepwhy" in _fn(app, "fcKeepHtml")
    if not NODE:
        return
    fc = {"一定要處理": [{"處理好": False}, {"處理好": True}, {"處理好": False}], "請看一眼": [{"已看過": False}] * 24}
    got = _node(_fn(app, "fcFoldHeads") + _fn(app, "execSkipNote") + f"""
      const fc = {json.dumps(fc, ensure_ascii=False)};
      console.log(JSON.stringify({{ h: fcFoldHeads(fc), note: execSkipNote(fc),
        empty: fcFoldHeads({{"一定要處理": [], "請看一眼": []}}), done: execSkipNote({{"一定要處理": [{{"處理好": true}}], "請看一眼": []}}) }}));""")
    assert got["h"]["must"] == "⚠️ 一定要處理 2 列（預設先略過），已處理 1 列"
    assert got["h"]["look"] == "👀 請看一眼 24 處（預設先略過）"
    assert got["note"].startswith("開始前總檢查預設略過：一定要處理 2 列、請看一眼 24 處")
    assert got["empty"]["must"] == "⚠️ 一定要處理：沒有" and got["done"] == "開始前總檢查都處理好了"


def test_web_step5_badges_tab_and_attention():
    fcj = (WEB / "finalcheck.js").read_text(encoding="utf-8")
    assert "fc-tab-look" not in _fn(fcj, "fcTabBadge")                # 「要人聽 k」不寫在分頁上
    assert "只看要人聽的" in _fn(fcj, "fcRenderLower")                  # 篩選留在清單裡
    if not NODE:
        return
    pre = (_ESC + "function fcFmt(t) { return String(t); }\nfunction fcWho(t) { return t; }\n"
           + _const(fcj, "FC_TAB_KEY") + "const FC_TAB_ALL = \"全部\", FC_TAB_TODO = \"還沒通過\", FC_TAB_KEEP = \"不修改\", FC_TAB_ATT = \"需留意\";\n"
           + _fn(fcj, "fcSpan") + _fn(fcj, "fcGroupRecs") + _fn(fcj, "fcTabList") + _fn(fcj, "fcTabBadge")
           + _const(fcj, "FC_ATT_MAX_PLAY") + _fn(fcj, "fcAttentionRow") + _fn(fcj, "fcAttentionHtml") + _fn(fcj, "fcAttentionGo"))
    recs = [{"鍵": "a", "組": "剪掉", "結果": "通過", "要人看": True}, {"鍵": "b", "組": "剪掉", "要人看": True}]
    att = {"有紀錄": True, "第4步沒處理": 5, "列": [
        {"鍵": "名字:4", "類別": "名字換不了代號", "第3步": "名字:4", "原片": [30, 31], "剩下": [[30, 31]], "剩下秒": 1,
         "成品段": [[20, 21]], "成品": [20, 21], "名字": True, "卡片名稱": "老師提到名字 0:30"},
        {"鍵": "聲紋:S1", "類別": "聲紋", "第3步": None, "原片": [2, 5], "剩下": [[2, 3], [4, 5]], "剩下秒": 2,
         "成品段": [[2, 3], [4, 5]], "成品": [2, 5], "名字": False, "卡片名稱": ""}]}
    kinds = [{"類別": "名字換不了代號", "名稱": "名字換不了代號（會念出本名）", "說明": ""}, {"類別": "聲紋", "名稱": "聲音不像老師", "說明": "x"}]
    got = _node(pre + f"""
      const recs = {json.dumps(recs, ensure_ascii=False)}, att = {json.dumps(att, ensure_ascii=False)}, kinds = {json.dumps(kinds, ensure_ascii=False)};
      const tabs = fcTabList(recs, [{{"組": "剪掉", "名稱": "剪掉", "說明": ""}}], [{{"子類": "x"}}], null, att);
      const flat = [];
      console.log(JSON.stringify({{ ids: tabs.map((t) => t.id), badges: tabs.map(fcTabBadge), html: fcAttentionHtml(att, kinds, flat), n: flat.length,
        go1: fcAttentionGo(att["列"][0]), go2: fcAttentionGo(att["列"][1]), old: fcAttentionHtml({{"有紀錄": false, "列": []}}, kinds, []),
        noAtt: fcTabList(recs, [], [], null, undefined).map((t) => t.id) }}));""")
    assert got["ids"] == ["全部", "還沒通過", "組:剪掉", "不修改", "需留意"]          # 需留意在最後、不修改旁邊
    assert got["badges"] == ["<span>完成 1／2</span>", "<span>1</span>", "<span>完成 1／2</span>", "<span>1</span>", "<span>2</span>"]
    h = got["html"]
    assert h.index("fc-att-red") < h.index("聲音不像老師") and "這 1 處會照原聲念出本名" in h and got["n"] == 2
    assert h.count("fc-a-play") == 3 and "還留著 2 秒原聲（其餘被別筆處理蓋到），2 段" in h and "總檢查有 5 列沒處理" in h
    assert got["go1"]["key"] == "名字:4" and got["go1"]["back"] == "step5"
    assert got["go2"]["key"] is None and got["go2"]["edit"] == {"類型": "學員發言", "start": 2, "end": 3}
    assert "舊版工具組裝的" in got["old"] and "需留意" not in got["noAtt"]


def test_web_export_dialog_names_not_blocked():
    fcj = (WEB / "finalcheck.js").read_text(encoding="utf-8")
    if not NODE:
        return
    g = {"說明": [], "擋下": [], "名字提醒": ["還有 1 處名字程式沒處理、原片沒動"], "退回清單": [], "提醒": [],
         "需留意說明": "需留意 3 處（含名字 1 處）還沒聽過"}
    got = _node(_fn(fcj, "fcExportAsk") + f"console.log(JSON.stringify(fcExportAsk({json.dumps(g, ensure_ascii=False)}, false)));")
    assert not got["blocked"] and got["names"] == g["名字提醒"] and got["att"] == g["需留意說明"] and got["title"] == "確定要輸出嗎？"
    dlg = _fn(fcj, "fcConfirmExport")
    assert "fc-export-names" in dlg and "fc-export-att" in dlg


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
