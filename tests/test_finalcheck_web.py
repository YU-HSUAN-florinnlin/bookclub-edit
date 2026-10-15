"""第 5 步網頁（`bookclub/web/finalcheck.js`）的測試與 #64 用到的後端換算（10-15 起網頁的純函式測試在 test_final_cards_web.py）。

網頁本身不開瀏覽器：能抽成純函式的（試聽怎麼播、通過後換哪一筆）用 node 跑；畫面行為要人實際點過（見 CHANGELOG）。
後端：`finalcheck.goto_output_time`（原片時間 → 成品時間）用手寫的處理紀錄，不碰真的影片、模型。
獨立可跑：.venv/bin/python tests/test_finalcheck_web.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import finalcheck as fc  # noqa: E402
from bookclub import proclog, render  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

WEB = REPO_ROOT / "bookclub" / "web"
FCJS = (WEB / "finalcheck.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _fn(src: str, name: str) -> str:
    """從 JS 原始碼切出一個頂層函式（到下一個行首的 `}`）。"""
    m = re.search(rf"^(async )?function {name}\(.*?^\}}\n", src, re.S | re.M)
    assert m, name
    return m.group(0)


def _node(code: str):
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


# 10-15 第5步2.0 第2段：第 5 步改用第 3 步的卡片，網頁的純函式測試搬到 tests/test_final_cards_web.py
# （通過後找下一張、試聽怎麼播、頂端一句、分頁…）；「跳到」框收進開發者設定（第 3 段），網頁先拿掉，後端換算照留。

def test_goto_output_time_uses_same_mapping():
    w = Path(tempfile.mkdtemp()) / "工作區"
    plist = render.pieces(10.0, 20.0, [(13.0, 15.0)], [{"at": 17.0, "dur": 0.5}])
    wd.write_json(proclog.log_path(w), {"產生時間": "t1", "片段": plist, "紀錄": []})
    assert fc.goto_output_time(w, 11.0) == {"成品秒": 1.0, "說明": ""}
    assert fc.goto_output_time(w, 18.0)["成品秒"] == round(fc.to_output_time(18.0, plist), 3)   # 停格之後
    r = fc.goto_output_time(w, 14.0)                    # 剪掉的範圍裡 → 剪掉之後第一個留下來的地方
    assert r["成品秒"] == 3.0 and "剪掉" in r["說明"], r
    r = fc.goto_output_time(w, 25.0)                    # 之後都不在成品裡
    assert r["成品秒"] is None and r["說明"], r
    # render audio（沒有片段表）：時間一樣
    w2 = Path(tempfile.mkdtemp()) / "工作區"
    wd.write_json(proclog.log_path(w2), {"產生時間": "t1", "片段": None, "紀錄": []})
    assert fc.goto_output_time(w2, 42.5) == {"成品秒": 42.5, "說明": ""}


def test_video_url_carries_version():
    assert '"成品影片"] + "|" + (d["成品版本"] || "")' in FCJS   # 10-13 bug 一：同名覆寫後瀏覽器不播快取的舊檔


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
    sys.exit(0)
