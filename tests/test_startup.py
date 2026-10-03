"""第九批：夥伴第一天會碰到的安裝、啟動、檢查問題（#25、#29、#30、#31、#32、#33）。

這台開發機是 macOS，Linux／WSL2 的分支沒辦法實跑，一律把平台判斷換成假的值來驗邏輯
（只有模擬測試，要夥伴在 WSL2 實機上再驗一次）。不載入模型、不啟動 8766 埠、不執行整支 install.sh。

獨立可跑：.venv/bin/python tests/test_startup.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import contextlib
import io
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import bookclub
from bookclub import doctor as dr


# ── #25：doctor 印工具版本 ─────────────────────────────────────


def test_doctor_prints_tool_version():
    buf = io.StringIO()
    with mock.patch.object(dr, "run_checks", return_value=[]), \
            mock.patch.object(dr, "print_report", return_value=True), \
            contextlib.redirect_stdout(buf):
        assert dr.main([]) == 0
    first = buf.getvalue().splitlines()[0]
    assert bookclub.__version__ in first, first
    assert dr.tool_version() == bookclub.__version__


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"通過：{t.__name__}")
    print(f"\n共 {len(tests)} 個測試通過")


if __name__ == "__main__":
    _run_all()
