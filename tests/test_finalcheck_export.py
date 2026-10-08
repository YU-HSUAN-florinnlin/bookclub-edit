"""第 5 步「輸出成品」放寬（10-08 宇軒）：沒看完、沒全部通過也可以輸出，按下去先確認。

- API：還沒檢查完時 `POST /api/final/export` 不輸出、回「要確認」＋還差什麼；帶 `確定` 才輸出；都檢查完直接輸出
- 網頁：確認視窗的文字、輸出紀錄「這次輸出時還沒看完」的提示（node 跑純函式）
真的開一個伺服器（埠 0），工作區是假的（ffmpeg 產生 10 秒的成品），不碰真的工作區。
獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_finalcheck_export.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from bookclub import finalcheck as fc  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

FCJS = (REPO_ROOT / "bookclub" / "web" / "finalcheck.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _call(port, method, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=json.dumps(body or {}).encode() if method == "POST" else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_api_asks_before_export_then_exports_with_confirm():
    if not shutil.which("ffmpeg"):
        print("（沒有 ffmpeg，跳過）")
        return
    import test_finalcheck as T

    from bookclub import server as srv

    w = T._workdir()
    old = os.environ.get("BOOKCLUB_DATA_DIR")
    os.environ["BOOKCLUB_DATA_DIR"] = str(Path(tempfile.mkdtemp()))
    httpd = srv.BookclubServer(("127.0.0.1", 0), srv.Handler, workdir=w, video=None)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        _call(port, "GET", "/api/final")
        code, r = _call(port, "POST", "/api/final/export", {})
        assert code == 200 and r["要確認"] is True and r["還差"]["沒通過"] == 2 and r["還差"]["說明"], r
        assert not list((w / "輸出").glob("最終成品_*"))                    # 沒確認：什麼都沒輸出
        code, r = _call(port, "POST", "/api/final/export", {"確定": True})
        assert code == 200 and r["ok"] and r["檢查完才輸出"] is False and r["沒通過筆數"] == 2
        assert (w / r["檔案"]).is_file()
        d = _call(port, "GET", "/api/final")[1]
        assert d["輸出成品"]["看過百分比"] == 0 and d["輸出成品"]["沒確認變動筆數"] == 1
        # 都檢查完：直接輸出不問
        for x in d["紀錄"]:
            fc.decide_record(w, x["鍵"], "通過")
        for u in d["未登記的變動"]:
            fc.decide_unlogged(w, u["鍵"], "沒問題")
        fc.add_watched(w, [[0.0, 10.0]])
        code, r = _call(port, "POST", "/api/final/export", {})
        assert code == 200 and r["ok"] and r["檢查完才輸出"] is True and r["第幾次"] == 2
        assert len(wd.read_json(fc.check_path(w))["輸出紀錄"]) == 2
    finally:
        httpd.shutdown()
        httpd.server_close()
        if old is None:
            os.environ.pop("BOOKCLUB_DATA_DIR", None)
        else:
            os.environ["BOOKCLUB_DATA_DIR"] = old


def test_export_blocks_pure():
    """確認了也不能輸出的幾種、只顯示的退回清單、第 4 步重做中的提醒（純函式）。"""
    name = {"類型": "名字要人處理", "原片": None, "成品": None, "覆核項目": ["名字:4"]}
    ov = {"類型": "局部消音", "原片": [5.0, 6.0], "成品": [5.0, 6.0], "覆核項目": ["重疊:O5.00"]}
    stu = {"類型": "學員重念", "原片": [10.0, 18.0], "成品": [9.0, 17.0], "覆核項目": ["學員段落:T003"]}
    log = {"紀錄": [name, ov, stu], "未登記的變動": []}
    k = fc.record_key
    # 沒按通過的名字要人處理、名字／重疊退回 → 擋；學員段落退回 → 只列出來
    chk = {"逐筆": {k(ov): {"結果": "退回重做", "原因": "還聽得到"}, k(stu): {"結果": "退回重做", "原因": "第二句念錯"}},
           "整片退回": [], "未登記確認": {}}
    b = fc.export_blocks(log, chk, "輸出/成品_0-0_sw.mp4")
    assert len(b["擋下"]) == 2 and "1 處名字程式沒處理、原片沒動" in b["擋下"][0] and "名字或聲音重疊" in b["擋下"][1]
    assert b["退回清單"] == [{"成品秒": 9.0, "原片秒": 10.0, "原因": "第二句念錯", "來源": "逐筆"}] and b["提醒"] == []
    # 名字要人處理按了通過、重疊那一筆沒退回 → 不擋
    chk2 = {"逐筆": {k(name): {"結果": "通過"}}, "整片退回": [], "未登記確認": {}}
    assert fc.export_blocks(log, chk2, "輸出/成品_0-0_sw.mp4")["擋下"] == []
    # 名字要人處理退回（不是通過）也擋
    chk3 = {"逐筆": {k(name): {"結果": "退回重做", "原因": "x"}}, "整片退回": [], "未登記確認": {}}
    assert len(fc.export_blocks(log, chk3, "輸出/成品_0-0_sw.mp4")["擋下"]) == 2   # 沒通過＋名字退回
    # 標字版：擋；重做中：提醒
    b4 = fc.export_blocks({"紀錄": []}, {"逐筆": {}, "重做中": {"項目": []}}, "輸出/成品_0-0_標字版.mp4")
    assert "標字版" in b4["擋下"][0] and "重做之前的版本" in b4["提醒"][0]


def _export_workdir(recs_extra=()):
    import test_finalcheck as T

    from bookclub import proclog

    w = T._workdir()
    log = proclog.load(w)
    log["紀錄"] = log["紀錄"] + list(recs_extra)
    wd.write_json(proclog.log_path(w), log)
    return w


def _check_all(w):
    d = fc.page_data(w)
    for x in d["紀錄"]:
        fc.decide_record(w, x["鍵"], "通過")
    for u in d["未登記的變動"]:
        fc.decide_unlogged(w, u["鍵"], "沒問題")
    fc.add_watched(w, [[0.0, 10.0]])


def test_name_needs_person_blocks_even_with_confirm():
    if not shutil.which("ffmpeg"):
        return
    name = {"類型": "名字要人處理", "原片": None, "成品": None, "動到聲音": False, "要人聽": True, "覆核項目": ["名字:4"],
            "做了什麼": "沒有自動處理"}
    w = _export_workdir([name])
    fc.page_data(w)
    r = fc.export_final(w)
    assert r["要確認"] and r["不能輸出"] and "名字程式沒處理" in r["還差"]["擋下"][0]
    try:
        fc.export_final(w, confirm=True)
        raise AssertionError("名字沒處理，確認了也不能輸出")
    except ValueError as e:
        assert "名字程式沒處理" in str(e)
    assert not list((w / "輸出").glob("最終成品_*"))
    _check_all(w)                                     # 看過、按了通過：可以輸出
    assert fc.export_final(w)["ok"]


def test_redo_in_progress_asks_even_when_checked():
    if not shutil.which("ffmpeg"):
        return
    w = _export_workdir()
    _check_all(w)
    chk = fc.load_check(w)
    chk["重做中"] = {"時間": "x", "項目": [{"鍵": "R001"}]}
    fc._save(w, chk)
    r = fc.export_final(w)
    assert r["要確認"] and not r.get("不能輸出") and "重做之前的版本" in r["還差"]["提醒"][0]
    assert fc.export_final(w, confirm=True)["ok"]


def test_api_confirm_must_be_true():
    """「確定」一定要是 true：送 "false"、1、"true" 都當作沒確認。"""
    if not shutil.which("ffmpeg"):
        return
    from bookclub import server as srv

    w = _export_workdir()
    old = os.environ.get("BOOKCLUB_DATA_DIR")
    os.environ["BOOKCLUB_DATA_DIR"] = str(Path(tempfile.mkdtemp()))
    httpd = srv.BookclubServer(("127.0.0.1", 0), srv.Handler, workdir=w, video=None)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        _call(port, "GET", "/api/final")
        for v in ("false", 1, "true", "是"):
            code, r = _call(port, "POST", "/api/final/export", {"確定": v})
            assert code == 200 and r["要確認"] is True and not r["ok"], v
        assert not list((w / "輸出").glob("最終成品_*"))
    finally:
        httpd.shutdown()
        httpd.server_close()
        if old is None:
            os.environ.pop("BOOKCLUB_DATA_DIR", None)
        else:
            os.environ["BOOKCLUB_DATA_DIR"] = old


def test_web_blocked_dialog_has_no_export_button():
    if not NODE:
        return
    blocked = {"說明": ["整片還沒看完（看過 10%）"], "擋下": ["還有 1 處名字程式沒處理、原片沒動（名字還在原聲裡），要先在清單裡看過、按通過"],
               "退回清單": [], "提醒": []}
    redo = {"說明": ["還有 1 筆沒通過（其中 1 筆是退回重做、還沒重做）"], "擋下": [],
            "退回清單": [{"成品秒": 9.0, "原片秒": 10.0, "原因": "第二句念錯"}, {"成品秒": None, "原片秒": 30.0, "原因": "x"}],
            "提醒": ["第 4 步正在重做退回的那幾筆（或上次沒做完）：現在輸出的是重做之前的版本"]}
    got = _node(_fn("fcExportAsk") + f"""
      console.log(JSON.stringify([fcExportAsk({json.dumps(blocked, ensure_ascii=False)}, true),
                                  fcExportAsk({json.dumps(redo, ensure_ascii=False)}, false)]));""")
    b, r = got
    assert b["blocked"] and b["title"] == "還不能輸出" and "名字程式沒處理" in b["body"] and b["win"] == ""
    assert not r["blocked"] and r["redoHead"] == "有 2 筆退回重做還沒重做，輸出的是重做之前的樣子"
    assert r["redo"] == [{"t": 9.0, "kind": "成品", "why": "第二句念錯"}, {"t": 30.0, "kind": "原片", "why": "x"}]
    assert "重做之前的版本" in r["warn"][0]
    dlg = _fn("fcConfirmExport")
    assert "a.blocked ?" in dlg and 'id="fc-export-yes"' in dlg and "知道了" in dlg


def _fn(name: str) -> str:
    m = re.search(rf"^(async )?function {name}\(.*?^\}}\n", FCJS, re.S | re.M)
    assert m, name
    return m.group(0)


def _node(code: str):
    return json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True).stdout)


def test_web_confirm_text_and_early_note():
    if not NODE:
        print("（沒有 node，略過）")
        return
    gaps = fc.export_gaps({"看過比例": 0.73, "逐筆": {"通過": 8, "退回": 0, "總數": 20}, "未登記": {"沒問題": 0, "總數": 0}, "退回數": 0})
    got = _node(_fn("fcExportAsk") + _fn("fcExportedEarly") + f"""
      const g = {json.dumps(gaps, ensure_ascii=False)};
      console.log(JSON.stringify({{ mac: fcExportAsk(g, false), win: fcExportAsk(g, true),
        early: fcExportedEarly({{"檢查完才輸出": false, "看過百分比": 73, "沒通過筆數": 12, "沒確認變動筆數": 0}}),
        ok: fcExportedEarly({{"檢查完才輸出": true, "看過百分比": 100}}),
        old: fcExportedEarly({{"檔案": "輸出/最終成品_x.mp4"}}), none: fcExportedEarly(null) }}));""")
    assert got["mac"]["body"] == "整片還沒看完（看過 73%）、還有 12 筆沒通過。確定要以目前的狀態輸出嗎？"
    assert got["mac"]["win"] == "" and "複製成品到 Windows 的下載資料夾" in got["win"]["win"]
    assert got["early"] == "這次輸出時還沒看完（看過 73%）、還有 12 筆沒通過"
    assert got["ok"] == "" and got["old"] == "" and got["none"] == ""   # 舊的輸出紀錄沒這幾欄：不提示


def test_web_export_wiring():
    ex = _fn("fcExport")
    assert '{ "確定": true }' in ex and "fcConfirmExport(" in ex and 'r["要確認"]' in ex
    assert "fileOutInit(" in ex                                       # 輸出後按鈕改指向剛輸出的那一支
    top = _fn("fcRenderTop")
    assert 'ex.disabled = !fc.data["有處理紀錄"] || !fc.data["成品影片"]' in top and "fcExportedEarly(" in top


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
