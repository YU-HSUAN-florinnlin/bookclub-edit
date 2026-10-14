"""測試共用（10-01）：這支測試程式建的暫存資料夾都放在同一個資料夾底下，跑完（包含失敗）自己清掉。

以前 tests/ 約 80 處 mkdtemp／copytree 跑完不清，10-01 夜間在系統暫存資料夾累積到約 23 GB。
每個 tests/test_*.py 在 `from __future__` 下面第一個 import 這支（要在任何 tempfile 之前）；
之後 `tempfile.mkdtemp()`、`TemporaryDirectory()`、測試開的子程式（看 TMPDIR）都會放在這個資料夾裡。
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

ROOT = tempfile.mkdtemp(prefix="bookclub-測試-")
tempfile.tempdir = ROOT
for _k in ("TMPDIR", "TEMP", "TMP"):   # 子程式（測試開的假子程式、ffmpeg）也放這裡
    os.environ[_k] = ROOT

# 10-13：老師資料（專案清單、設定）一律指到這個暫存資料夾，測試不寫真的 ~/讀書會剪輯資料。
# 以前 test_startup 呼叫 serve() 沒設這個環境變數，`adopt_project` 把暫存工作區寫進真的 `專案清單.json`、
# 「目前」也被指到暫存資料夾（10-12 備份裡累積過 80 筆）。個別測試要自己的資料夾照樣可以再設；
# 「設了再還原」的寫法還原回這裡，不會掉回真的資料夾。開發者 shell 裡設的值也蓋掉（測試不該碰真的資料）。
os.environ["BOOKCLUB_DATA_DIR"] = os.path.join(ROOT, "老師資料")


def _cleanup() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)


atexit.register(_cleanup)
