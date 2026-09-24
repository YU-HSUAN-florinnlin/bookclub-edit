"""跨平台判斷的單元測試：Linux／WSL2 的系統資訊解析，以及 install.sh 的平台分支。

這台開發機是 macOS，Linux 分支沒辦法實跑，所以用「餵假的檔案內容／假的 uname」
來驗判斷邏輯；install.sh 的部分以替身指令執行平台分支（不執行安裝）。

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
        assert dr.linux_distro_name() == "Weird"
    with mock.patch.object(Path, "read_text", _fake_read_text({})):
        assert dr.linux_distro_name() == "無法偵測"


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
    for token in ("apt-get", "build-essential", "dpkg-query", "astral.sh/uv/install.sh"):
        assert token in INSTALL_SH, token


def test_install_sh_python_targets_cover_linux():
    from bookclub.system_info import python_target
    for system, arch, expected in (
        ("Linux", "x86_64", "linux-x86_64-gnu"),
        ("Linux", "aarch64", "linux-aarch64-gnu"),
        ("Darwin", "arm64", "macos-aarch64-none"),
        ("Darwin", "x86_64", "macos-x86_64-none"),
    ):
        assert python_target(system, arch) == "cpython-3.11-" + expected


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


def test_meminfo_pure_parser():
    from bookclub.system_info import parse_meminfo
    assert parse_meminfo("MemFree: 2 kB\nMemTotal: 8388608 kB\n") == 8 * 1024**3
    for text in ("", "MemTotal: nope kB", "MemTotal: -1 kB", "MemTotal: 1 MB", "MemTotal: 0 kB"):
        assert parse_meminfo(text) is None


def test_os_release_pure_parser():
    from bookclub.system_info import parse_os_release
    assert parse_os_release("# comment\nPRETTY_NAME='Ubuntu LTS'\n") == "Ubuntu LTS"
    assert parse_os_release('NAME=Ubuntu\nVERSION="24.04 LTS"') == "Ubuntu 24.04 LTS"
    assert parse_os_release('PRETTY_NAME="broken') == "無法偵測"
    assert parse_os_release('PRETTY_NAME="$(touch should-not-exist)"') == "$(touch should-not-exist)"
    assert parse_os_release("") == "無法偵測"


def test_wsl_pure_parser():
    from bookclub.system_info import is_wsl
    assert is_wsl("Linux version MICROSOFT-standard-WSL2")
    assert not is_wsl("Linux version generic")
    assert not is_wsl("")


def test_python_target_intel_and_unsupported():
    from bookclub.system_info import python_target
    assert python_target("Darwin", "arm64", True) == "cpython-3.11-macos-x86_64-none"
    for args in (("Linux", "x86_64", True), ("Linux", "riscv64"), ("Windows", "x86_64")):
        try:
            python_target(*args)
        except ValueError:
            pass
        else:
            raise AssertionError(args)


def _run_linux_installer(*args, arch="x86_64", apt=True, uv=True, root=False, unknown=False):
    """只執行到 [2]：所有外部安裝指令均為替身，不能連網或安裝。"""
    import shlex
    prefix = INSTALL_SH.split("# ── [3/11]", 1)[0]
    # -c 的 BASH_SOURCE 為空，換成測試倉庫位置；不改實際腳本。
    prefix = prefix.replace('SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
                            "SCRIPT_DIR=" + shlex.quote(str(REPO_ROOT)))
    with tempfile.TemporaryDirectory() as tmp:
        setup = r'''
set -euo pipefail
uname() { if [ "$1" = "-s" ]; then echo Linux; else echo "$TEST_ARCH"; fi; }
python3() {
  if [ "$1" = "bookclub/detect.py" ]; then return 0; fi
  if [ "$1" = "bookclub/system_info.py" ]; then
    case "$2" in
      os) if [ "$TEST_UNKNOWN" = 1 ]; then echo 無法偵測; else echo Ubuntu; fi; return ;;
      memory) if [ "$TEST_UNKNOWN" != 1 ]; then echo 16; fi; return ;;
    esac
  fi
  "$TEST_PYTHON" "$@"
}
grep() { if [ "${3:-}" = /proc/version ]; then return 0; fi; builtin command grep "$@"; }
df() { echo 'Filesystem 1024-blocks Used Available Capacity Mounted'; echo '/dev/test 999999999 999999998 41943040 99% /'; }
id() { echo "$TEST_UID"; }
dpkg-query() { return 1; }
apt-get() { echo "APT $*"; }
sudo() { echo SUDO; "$@"; }
brew() { echo 'ERROR brew on Linux'; exit 91; }
sw_vers() { echo 'ERROR sw_vers on Linux'; exit 92; }
sysctl() { echo 'ERROR sysctl on Linux'; exit 93; }
curl() { echo 'mock installer'; }
sh() { cat >/dev/null; touch "$TEST_UV_MARKER"; echo UV_INSTALLED; }
command() {
  if [ "${1:-}" = -v ]; then
    case "$2" in
      apt-get) [ "$TEST_APT" = 1 ] || return 1; echo apt-get; return ;;
      uv) [ "$TEST_UV" = 1 ] || [ -f "$TEST_UV_MARKER" ] || return 1; echo uv; return ;;
      sudo) echo sudo; return ;;
    esac
  fi
  builtin command "$@"
}
'''
        import os
        env = dict(os.environ, TEST_ARCH=arch, TEST_APT=str(int(apt)), TEST_UV=str(int(uv)),
                   TEST_UID="0" if root else "1000", TEST_UNKNOWN=str(int(unknown)),
                   TEST_PYTHON=sys.executable, TEST_UV_MARKER=str(Path(tmp) / "uv-ready"))
        return subprocess.run(["bash", "-c", setup + prefix + '\necho "TARGET=$PY_TARGET"', "test", *args],
                              cwd=REPO_ROOT, env=env, capture_output=True, text=True, errors="replace", timeout=20)


def test_linux_shell_installs_packages_and_selects_python():
    for arch in ("x86_64", "aarch64"):
        r = _run_linux_installer(arch=arch)
        assert r.returncode == 0, r.stderr
        assert f"TARGET=cpython-3.11-linux-{arch}-gnu" in r.stdout
        assert "APT install -y ffmpeg sox git build-essential curl unzip" in r.stdout
        assert "密碼提示" in r.stdout and "SUDO" in r.stdout
        assert "剩餘空間：約 40GB" in r.stdout  # 可用空間，第 4 欄
        assert "偵測到 WSL2" in r.stdout
        assert "ERROR" not in r.stdout


def test_linux_shell_missing_apt():
    r = _run_linux_installer(apt=False)
    assert r.returncode == 1 and "沒有 apt-get" in r.stdout
    assert "APT install" not in r.stdout


def test_linux_shell_uv_install_and_root():
    r = _run_linux_installer(uv=False, root=True)
    assert r.returncode == 0, r.stderr
    assert "UV_INSTALLED" in r.stdout and "source ~/.bashrc" in r.stdout
    assert "SUDO" not in r.stdout


def test_linux_shell_unknown_system_info_continues():
    r = _run_linux_installer(unknown=True)
    assert r.returncode == 0, r.stderr
    assert "無法偵測" in r.stdout and "TARGET=" in r.stdout


def test_linux_shell_intel_rejected_even_check_only():
    r = _run_linux_installer("--intel", "--check-only")
    assert r.returncode == 1 and "--intel 只在 macOS" in r.stdout
    assert "APT" not in r.stdout


def test_linux_shell_check_only_skips_packages():
    r = _run_linux_installer("--check-only", apt=False, uv=False)
    assert r.returncode == 0 and "偵測到 WSL2" in r.stdout
    assert "APT" not in r.stdout and "UV_INSTALLED" not in r.stdout


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"通過：{t.__name__}")
    print(f"\n共 {len(tests)} 個測試通過")


if __name__ == "__main__":
    _run_all()
