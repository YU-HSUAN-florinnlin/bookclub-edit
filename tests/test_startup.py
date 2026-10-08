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
from bookclub import fileio as _fileio

# 1008-4：serve() 在 Mac 上會背景編譯、開常駐選檔小程式；測試不碰真的 ~/.cache 與真的小程式
_fileio.MAC_PICKER.start_in_background = lambda: None


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


# ── #30：埠被占用（連點兩次啟動）不印 Python 錯誤 ─────────────────


def _serve_capture(**kwargs):
    """在暫存工作區上呼叫 serve()，抓印出來的字與回傳值（stderr 一起抓，確認沒有 Traceback）。"""
    import tempfile

    from bookclub import server as sv

    out = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp, \
            mock.patch.object(sv, "_mark_interrupted") as marked, \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = sv.serve(workdir=Path(tmp) / "工作區", **kwargs)
    return rc, out.getvalue(), marked


@contextlib.contextmanager
def _occupied_port(ours: bool):
    """開一個臨時埠（不是 8766）。ours=True：假裝是讀書會剪輯工具（/api/projects 回這個工具才有的欄位）。"""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps({"轉文字金鑰": True} if ours else {"hello": 1}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # noqa: ANN002
            pass

    httpd = HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_port_in_use_by_bookclub_says_already_open():
    from bookclub import server as sv

    with _occupied_port(ours=True) as port, \
            mock.patch.object(sv, "browser_plan", return_value="open"), \
            mock.patch.object(sv.webbrowser, "open", return_value=True) as wb:
        rc, text, marked = _serve_capture(port=port, open_browser=True)
    assert rc == 0
    assert "Traceback" not in text and "OSError" not in text
    assert "已經開著了" in text and f"http://127.0.0.1:{port}/" in text and "Ctrl+C" in text
    wb.assert_called_once_with(f"http://127.0.0.1:{port}/")   # Mac：直接打開舊的那一個
    marked.assert_not_called()   # 第二個沒拿到埠，不能把第一個跑到一半的進度改成「中斷」
    assert len([ln for ln in text.splitlines() if ln.strip()]) <= 4, text


def test_port_in_use_by_other_program():
    from bookclub import server as sv

    with _occupied_port(ours=False) as port, \
            mock.patch.object(sv, "browser_plan", return_value="open"), \
            mock.patch.object(sv.webbrowser, "open") as wb:
        rc, text, marked = _serve_capture(port=port, open_browser=True)
    assert rc == 1
    assert "Traceback" not in text
    assert f"{port} 埠被占用" in text and f"--port {port + 1}" in text
    wb.assert_not_called()
    marked.assert_not_called()


def test_port_in_use_on_wsl_points_to_windows_localhost():
    from bookclub import server as sv

    with _occupied_port(ours=True) as port, \
            mock.patch.object(sv, "browser_plan", return_value="wsl"), \
            mock.patch.object(sv.webbrowser, "open") as wb:
        rc, text, _ = _serve_capture(port=port, open_browser=True)
    assert rc == 0 and f"http://localhost:{port}/" in text
    wb.assert_not_called()


def test_other_bind_errors_still_raise():
    import errno

    from bookclub import server as sv

    err = OSError(errno.EACCES, "Permission denied")
    with mock.patch.object(sv, "BookclubServer", side_effect=err):
        try:
            _serve_capture(port=1, open_browser=False)
        except OSError as exc:
            assert exc.errno == errno.EACCES
        else:
            raise AssertionError("不是埠被占用的錯誤不該被吞掉")


def test_cli_serve_passes_exit_code():
    from bookclub import cli
    from bookclub import server as sv

    with mock.patch.object(sv, "serve", return_value=1):
        assert cli.main(["serve", "--no-open"]) == 1
    with mock.patch.object(sv, "serve", return_value=None):
        assert cli.main(["serve", "--no-open"]) == 0


# ── #29：WSL2 不自動開瀏覽器、金鑰訊息依平台 ───────────────────────


def test_browser_plan():
    from bookclub import server as sv

    assert sv.browser_plan("Darwin", wsl=False, env={}) == "open"
    assert sv.browser_plan("Linux", wsl=True, env={"DISPLAY": ":0"}) == "wsl"   # WSLg 也有 DISPLAY，仍走 Windows 瀏覽器
    assert sv.browser_plan("Linux", wsl=False, env={}) == "manual"
    assert sv.browser_plan("Linux", wsl=False, env={"WAYLAND_DISPLAY": "wayland-0"}) == "open"
    # 不帶 wsl：從 /proc/version 判斷
    with mock.patch.object(sv, "_is_wsl", return_value=True):
        assert sv.browser_plan("Linux", env={}) == "wsl"


class _FakeServer:
    def __init__(self, *a, **k):  # noqa: ANN002, ANN003
        pass

    def serve_forever(self):
        raise KeyboardInterrupt

    def server_close(self):
        pass


def test_wsl_start_prints_windows_url_and_does_not_open():
    from bookclub import server as sv

    with mock.patch.object(sv, "BookclubServer", _FakeServer), \
            mock.patch.object(sv, "browser_plan", return_value="wsl"), \
            mock.patch.object(sv.threading, "Timer") as timer, \
            mock.patch.object(sv.webbrowser, "open") as wb:
        rc, text, _ = _serve_capture(port=8766, open_browser=True)
    assert rc == 0
    assert "Windows 的瀏覽器" in text and "http://localhost:8766/" in text
    timer.assert_not_called()
    wb.assert_not_called()


def test_mac_start_still_opens_browser():
    from bookclub import server as sv

    with mock.patch.object(sv, "BookclubServer", _FakeServer), \
            mock.patch.object(sv, "browser_plan", return_value="open"), \
            mock.patch.object(sv.threading, "Timer") as timer:
        rc, text, _ = _serve_capture(port=8766, open_browser=True)
    assert rc == 0 and "http://127.0.0.1:8766/" in text
    timer.assert_called_once()
    # 開不起來（例如沒有預設瀏覽器）就印網址
    open_fn = timer.call_args[0][1]
    out = io.StringIO()
    with mock.patch.object(sv.webbrowser, "open", return_value=False), contextlib.redirect_stdout(out):
        open_fn()
    assert "請在瀏覽器開 http://127.0.0.1:8766/" in out.getvalue()


def test_groq_message_per_platform():
    from bookclub import server as sv

    mac = sv.groq_key_missing_message("Darwin")
    linux = sv.groq_key_missing_message("Linux")
    assert "啟動.command" in mac and "~/.zshrc" in mac
    assert "啟動.command" not in linux and "~/.profile" in linux and "~/.bashrc" in linux
    # 跟 doctor 說的金鑰檔一致
    with mock.patch("platform.system", return_value="Linux"):
        assert dr.key_file() in linux
    with mock.patch("platform.system", return_value="Darwin"):
        assert dr.key_file() in mac


# ── #31：install.sh 的 skill 捷徑 ─────────────────────────────────
# 只抽出 [10/11] 這一段、在假的 HOME 裡跑（不執行整支 install.sh、不碰真的 ~/.claude）。


def _run_skill_step(home: Path, no_skill: bool = False):
    import os
    import shlex
    import subprocess

    text = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    section = text.split("# ── [10/11]", 1)[1].split("\n", 1)[1].split("# ── [11/11]", 1)[0]   # 去掉標題那一行的剩餘
    script = ("set -euo pipefail\nstep() { echo \"== $*\"; }\n"
              f"SCRIPT_DIR={shlex.quote(str(REPO_ROOT))}\nNO_SKILL={int(no_skill)}\n" + section)
    env = dict(os.environ, HOME=str(home))
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=20)


def test_install_skill_link_creates_skills_folder():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        (home / ".claude").mkdir()   # 裝過 Claude Code，但還沒有 skills 這一層
        r = _run_skill_step(home)
        assert r.returncode == 0, r.stderr
        link = home / ".claude" / "skills" / "bookclub-edit"
        assert link.is_symlink() and link.resolve() == (REPO_ROOT / "skills" / "bookclub-edit").resolve()
        assert "已建立" in r.stdout
        r2 = _run_skill_step(home)   # 重跑不會出錯
        assert r2.returncode == 0 and "捷徑已存在" in r2.stdout


def test_install_skill_link_without_claude_prints_how_to_add_later():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        r = _run_skill_step(home)
        assert r.returncode == 0, r.stderr
        assert not (home / ".claude").exists()   # 沒裝 Claude Code 就不替它建資料夾
        assert "mkdir -p ~/.claude/skills && ln -s" in r.stdout
        assert str(REPO_ROOT / "skills" / "bookclub-edit") in r.stdout


def test_install_skill_link_respects_existing_and_no_skill():
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        skills = home / ".claude" / "skills"
        skills.mkdir(parents=True)
        os.symlink(home / "不存在的地方", skills / "bookclub-edit")   # 壞掉的捷徑：以前會讓 ln 失敗、整支中斷
        r = _run_skill_step(home)
        assert r.returncode == 0, r.stderr
        assert "不是指到這個倉庫" in r.stdout
        assert os.readlink(skills / "bookclub-edit") == str(home / "不存在的地方")
    with tempfile.TemporaryDirectory() as tmp:
        r = _run_skill_step(Path(tmp), no_skill=True)
        assert r.returncode == 0 and "--no-skill" in r.stdout and not (Path(tmp) / ".claude").exists()


# ── #32：doctor --smoke 在 Linux 不因為沒有 say 而失敗 ───────────────


def test_smoke_skips_on_linux_without_samples():
    out = io.StringIO()
    with mock.patch.object(dr, "samples_ready", return_value=False), \
            mock.patch.object(dr.platform, "system", return_value="Linux"), \
            mock.patch.object(dr.subprocess, "run") as run, \
            contextlib.redirect_stdout(out):
        res = dr.run_smoke()
    run.assert_not_called()   # 不去跑 make_sample、也不載入模型
    assert res["ok"] is None and "skipped" in res
    text = out.getvalue()
    assert "只在 Mac 上跑" in text and "不算失敗" in text and "one.wav" in text and "Linux" in text
    # doctor 的結束代碼只看必要檢查，--smoke 跳過不影響
    with mock.patch.object(dr, "run_checks", return_value=[]), \
            mock.patch.object(dr, "print_report", return_value=True), \
            mock.patch.object(dr, "run_smoke", return_value=res), \
            contextlib.redirect_stdout(io.StringIO()):
        assert dr.main(["--smoke"]) == 0


def test_can_make_samples():
    with mock.patch.object(dr.shutil, "which", return_value="/usr/bin/say"):
        assert dr.can_make_samples("Darwin") is True
        assert dr.can_make_samples("Linux") is False
    with mock.patch.object(dr.shutil, "which", return_value=None):
        assert dr.can_make_samples("Darwin") is False


def test_smoke_on_linux_runs_when_samples_copied_over():
    """從 Mac 複製測試音檔過來後，Linux 也照常跑（這裡把跑模型的子程式換成假的）。"""
    import subprocess
    import tempfile

    fake = subprocess.CompletedProcess([], 0, stdout='{"ok": true}\n', stderr="")
    with tempfile.TemporaryDirectory() as tmp, \
            mock.patch.object(dr, "samples_ready", return_value=True), \
            mock.patch.object(dr.platform, "system", return_value="Linux"), \
            mock.patch.object(dr, "data_dir", return_value=Path(tmp)), \
            mock.patch.object(dr.subprocess, "run", return_value=fake) as run, \
            contextlib.redirect_stdout(io.StringIO()):
        res = dr.run_smoke()
    assert run.call_count == len(dr.SMOKE_SCRIPTS)
    assert all(r["ok"] for r in res["results"].values())


def test_smoke_on_mac_still_makes_samples():
    out = io.StringIO()
    with mock.patch.object(dr, "samples_ready", return_value=False), \
            mock.patch.object(dr, "can_make_samples", return_value=True), \
            mock.patch.object(dr, "_ensure_samples", return_value=False) as ens, \
            contextlib.redirect_stdout(out):
        res = dr.run_smoke()
    ens.assert_called_once()
    assert res["ok"] is False


def test_make_sample_without_say_explains():
    import importlib.util

    spec = importlib.util.spec_from_file_location("make_sample", REPO_ROOT / "tests" / "smoke" / "make_sample.py")
    ms = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ms)
    err = io.StringIO()
    with mock.patch("shutil.which", return_value=None), contextlib.redirect_stderr(err):
        assert ms.main() == 1
    assert "只在 Mac 上跑" in err.getvalue()


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"通過：{t.__name__}")
    print(f"\n共 {len(tests)} 個測試通過")


if __name__ == "__main__":
    _run_all()
