"""測試共用（10-01）：這支測試程式建的暫存資料夾都放在同一個資料夾底下，跑完（包含失敗）自己清掉。

以前 tests/ 約 80 處 mkdtemp／copytree 跑完不清，10-01 夜間在系統暫存資料夾累積到約 23 GB。
每個 tests/test_*.py 在 `from __future__` 下面第一個 import 這支（要在任何 tempfile 之前）；
之後 `tempfile.mkdtemp()`、`TemporaryDirectory()`、測試開的子程式（看 TMPDIR）都會放在這個資料夾裡。
"""

from __future__ import annotations

import atexit
import itertools
import os
import shutil
import subprocess
import sys
import tempfile

if sys.platform == "win32":
    # 原生 Windows：標準輸出預設 cp1252／cp950，測試印「✓」「通過」會丟 UnicodeEncodeError；子程式也用 UTF-8 模式。
    # （bookclub 套件載入時也會做，這裡是給不 import bookclub 的測試用）
    os.environ.setdefault("PYTHONUTF8", "1")
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

ROOT = os.path.realpath(tempfile.mkdtemp(prefix="bookclub-測試-"))   # realpath：Windows 的暫存資料夾常是 8.3 短名（RUNNER~1），程式 resolve() 之後會變長名，不換掉路徑比對會對不起來
tempfile.tempdir = ROOT
for _k in ("TMPDIR", "TEMP", "TMP"):   # 子程式（測試開的假子程式、ffmpeg）也放這裡
    os.environ[_k] = ROOT

# 10-13：老師資料（專案清單、設定）一律指到這個暫存資料夾，測試不寫真的 ~/讀書會剪輯資料。
# 以前 test_startup 呼叫 serve() 沒設這個環境變數，`adopt_project` 把暫存工作區寫進真的 `專案清單.json`、
# 「目前」也被指到暫存資料夾（10-12 備份裡累積過 80 筆）。個別測試要自己的資料夾照樣可以再設；
# 「設了再還原」的寫法還原回這裡，不會掉回真的資料夾。開發者 shell 裡設的值也蓋掉（測試不該碰真的資料）。
os.environ["BOOKCLUB_DATA_DIR"] = os.path.join(ROOT, "老師資料")


if sys.platform == "win32":
    # Windows 的指令列最長約 32767 字元，`node -e <很長的程式>` 會丟 WinError 206；太長就先存成檔案再叫 node 跑。
    _orig_run, _node_n = subprocess.run, itertools.count()

    def _run(args, *a, **kw):
        if (isinstance(args, list) and len(args) == 3 and args[1] == "-e" and len(args[2]) > 6000
                and os.path.basename(str(args[0])).lower().startswith("node")):
            p = os.path.join(ROOT, f"_node_{next(_node_n)}.js")
            with open(p, "w", encoding="utf-8") as f:
                f.write(args[2])
            args = [args[0], p]
        return _orig_run(args, *a, **kw)

    subprocess.run = _run


def _cleanup() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)


atexit.register(_cleanup)
