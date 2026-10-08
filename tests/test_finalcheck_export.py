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
