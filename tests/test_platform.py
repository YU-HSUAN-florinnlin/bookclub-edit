"""跨平台判斷的單元測試：Linux／WSL2 的系統資訊解析，以及 install.sh 的平台分支。

這台開發機是 macOS，Linux 分支沒辦法實跑，所以用「餵假的檔案內容／假的 uname」
來驗判斷邏輯；install.sh 的部分用 grep 確認關鍵分支寫進去了（不執行安裝）。

獨立可跑：.venv/bin/python tests/test_platform.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import doctor as dr


# ── doctor：Linux 系統資訊解析 ────────────────────────────────


def _fake_read_text(mapping: dict[str, str]):
    """把 Path.read_text 換掉：對應到 mapping 的路徑回傳假內容，其餘拋 OSError。"""

    def fake(self, *args, **kwargs):  # noqa: ANN001
        key = str(self)
        if key in mapping:
            return mapping[key]
        raise OSError("測試裡沒有這個檔案")

    return fake


def test_is_wsl_true():
    content = "Linux version 5.15.0-microsoft-standard-WSL2 (gcc ...)"
    with mock.patch.object(Path, "read_text", _fake_read_text({"/proc/version": content})):
        assert dr.is_wsl() is True


def test_is_wsl_false_on_plain_linux():
    with mock.patch.object(Path, "read_text", _fake_read_text({"/proc/version": "Linux version 6.5.0-generic"})):
        assert dr.is_wsl() is False


def test_is_wsl_false_when_file_missing():
    """macOS 沒有 /proc/version，不能因此出錯。"""
    with mock.patch.object(Path, "read_text", _fake_read_text({})):
        assert dr.is_wsl() is False


def test_linux_distro_name():
    content = 'NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 22.04.3 LTS"\nVERSION_ID="22.04"\n'
    with mock.patch.object(Path, "read_text", _fake_read_text({"/etc/os-release": content})):
        assert dr.linux_distro_name() == "Ubuntu 22.04.3 LTS"


def test_linux_distro_name_fallback():
    with mock.patch.object(Path, "read_text", _fake_read_text({"/etc/os-release": 'NAME="Weird"\n'})):
        assert dr.linux_distro_name() == "Linux"
    with mock.patch.object(Path, "read_text", _fake_read_text({})):
        assert dr.linux_distro_name() == "Linux"


def test_linux_mem_gb():
    content = "MemTotal:       32768000 kB\nMemFree:         1000000 kB\n"
    with mock.patch.object(Path, "read_text", _fake_read_text({"/proc/meminfo": content})):
        assert round(dr.linux_mem_gb()) == 31  # 32768000 kB ≈ 31.25 GB


def test_linux_mem_gb_unreadable_returns_zero():
    with mock.patch.object(Path, "read_text", _fake_read_text({})):
        assert dr.linux_mem_gb() == 0.0


def test_check_os_version_on_linux_wsl():
    with mock.patch("platform.system", return_value="Linux"), \
         mock.patch.object(dr, "linux_distro_name", return_value="Ubuntu 22.04.3 LTS"), \
         mock.patch.object(dr, "is_wsl", return_value=True):
        c = dr.check_os_version()
    assert "Ubuntu" in c.detail and "WSL2" in c.detail and c.ok


def test_check_memory_on_linux():
    with mock.patch("platform.system", return_value="Linux"), \
         mock.patch.object(dr, "linux_mem_gb", return_value=31.25):
        c = dr.check_memory()
    assert "31GB" in c.detail


def test_check_memory_on_linux_low_warns():
    with mock.patch("platform.system", return_value="Linux"), \
         mock.patch.object(dr, "linux_mem_gb", return_value=4.0):
        c = dr.check_memory()
    assert "8GB" in c.detail  # 低於建議值時要附提醒


def test_check_chip_linux_label():
    with mock.patch("platform.system", return_value="Linux"), \
         mock.patch("platform.machine", return_value="aarch64"):
        assert "ARM" in dr.check_chip().detail


# ── install.sh：平台分支 ──────────────────────────────────────


INSTALL_SH = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")


def test_install_sh_has_os_detection():
    assert 'OS_KIND="$(uname -s)"' in INSTALL_SH
    assert "microsoft /proc/version" in INSTALL_SH  # WSL2 判斷


def test_install_sh_has_apt_branch():
    for token in ("apt-get", "build-essential", "dpkg -s", "astral.sh/uv/install.sh"):
        assert token in INSTALL_SH, token


def test_install_sh_python_targets_cover_linux():
    for token in ("cpython-3.11-linux-x86_64-gnu", "cpython-3.11-linux-aarch64-gnu",
                  "cpython-3.11-macos-aarch64-none"):
        assert token in INSTALL_SH, token


def test_install_sh_intel_flag_rejected_on_linux():
    assert '--intel 只在 macOS' in INSTALL_SH


def test_install_sh_syntax_ok():
    r = subprocess.run(["bash", "-n", str(REPO_ROOT / "install.sh")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_install_sh_check_only_runs_without_installing():
    """--check-only 要能跑完、回傳 0，而且不碰安裝步驟（畫面上會有那句提示）。"""
    r = subprocess.run(["bash", "install.sh", "--check-only"], cwd=REPO_ROOT,
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-500:]
    assert "只檢查" in r.stdout


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"通過：{t.__name__}")
    print(f"\n共 {len(tests)} 個測試通過")


if __name__ == "__main__":
    _run_all()
