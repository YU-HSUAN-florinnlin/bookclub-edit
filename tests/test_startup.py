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


# ── #33：models.py 不再寫死 ~/Downloads/bookclub-edit ─────────────


def test_hf_login_steps_follow_repo_location():
    from bookclub import models

    assert models.hf_cli_display() in models.HF_LOGIN_STEPS
    home = Path("/home/partner")
    assert models.hf_cli_display(home / "bookclub-edit", home) == "~/bookclub-edit/.venv/bin/hf"
    assert models.hf_cli_display(home / "工具" / "bookclub-edit", home) == "~/工具/bookclub-edit/.venv/bin/hf"
    # 不在家目錄底下：寫完整路徑
    assert models.hf_cli_display(Path("/opt/bookclub-edit"), home) == "/opt/bookclub-edit/.venv/bin/hf"
    # 有空白：整段加引號、不用 ~（引號裡的 ~ 不會展開）
    assert (models.hf_cli_display(home / "my tools" / "bookclub-edit", home)
            == "'/home/partner/my tools/bookclub-edit/.venv/bin/hf'")
    # 原始碼裡不再出現寫死的路徑
    assert "Downloads/bookclub-edit" not in (REPO_ROOT / "bookclub" / "models.py").read_text(encoding="utf-8")


def test_doctor_hf_fix_uses_same_path():
    from bookclub import models

    with mock.patch("huggingface_hub.get_token", return_value=None):
        c = dr.check_hf_login()
    assert not c.ok and f"{models.hf_cli_display()} auth login" in c.fix


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"通過：{t.__name__}")
    print(f"\n共 {len(tests)} 個測試通過")


if __name__ == "__main__":
    _run_all()
