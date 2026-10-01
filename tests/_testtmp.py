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


def _cleanup() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)


atexit.register(_cleanup)
