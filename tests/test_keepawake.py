"""防睡眠（10-07 #172，`bookclub/keepawake.py`）的單元測試。

開發機是 macOS、沒有 Windows：WSL2 那一支用「假的 powershell」（Python 小腳本）代替，
/proc 的檔案、which、檔案在不在都從外面餵；不會真的呼叫 powershell.exe。Mac 那一支也不真的開 caffeinate。

獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_keepawake.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import os
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import keepawake as ka

TMP = Path(tempfile.mkdtemp(prefix="防睡眠_"))

# 假的 powershell：照模式回第一行，之後數心跳，讀到 EOF 就把結果寫進檔案、結束
FAKE_PS = TMP / "防睡眠_假powershell.py"
FAKE_PS.write_text('''import sys, time
mode, out = sys.argv[1], sys.argv[2]
args = sys.argv[3:]
open(out + ".args", "w", encoding="utf-8").write("\\n".join(args))
if mode == "fail":
    print("FAIL SetThreadExecutionState", flush=True); sys.exit(2)
if mode == "exit":
    sys.exit(1)
if mode == "silent":
    print("WARNING: 雜訊", flush=True)
    time.sleep(30); sys.exit(0)
print("一行不相干的輸出", flush=True)
print("OK Offline" if mode == "battery" else "OK Online", flush=True)
beats = 0
for line in sys.stdin:
    beats += 1
open(out, "w", encoding="utf-8").write(f"beats={beats} eof=1")
''', encoding="utf-8")

WSL_FILES = {"/proc/version": "Linux version 5.15.167.4-microsoft-standard-WSL2",
             "/proc/sys/fs/binfmt_misc/WSLInterop": "enabled\ninterpreter /init\n"}


def _reader(files: dict[str, str]):
    return lambda p: files.get(p, "")


class FakePopen:
    """把 powershell.exe 換成跑假腳本；記下每一支程序，測完確認都結束了。"""

    def __init__(self, mode: str):
        self.mode = mode
        self.out = str(TMP / f"防睡眠_{mode}_{time.monotonic_ns()}.txt")
        self.procs: list[subprocess.Popen] = []
        self.calls: list[list[str]] = []

    def __call__(self, args, **kw):
        self.calls.append(list(args))
        p = subprocess.Popen([sys.executable, str(FAKE_PS), self.mode, self.out, *args[1:]], **kw)
        self.procs.append(p)
        return p


def _wsl(mode: str = "ok", files: dict | None = None, which=lambda n: "/usr/bin/powershell.exe",
         exists=lambda p: False, ok_timeout_s: float = 10.0, heartbeat_s: float = 0.05):
    fp = FakePopen(mode)
    logs: list[str] = []
    st: list[str] = []
    stop = ka.keep_awake(logs.append, prefix="[測試]", on_status=st.append, platform="linux",
                         read=_reader(WSL_FILES if files is None else files), which=which, exists=exists,
                         popen=fp, ok_timeout_s=ok_timeout_s, heartbeat_s=heartbeat_s)
    return stop, fp, logs, st


# ── 編碼 ────────────────────────────────────────────────


def test_encoded_command_roundtrip():
    s = ka.powershell_script()
    assert ka.decode_command(ka.encode_command(s)) == s
    assert ka.decode_command(ka.encode_command("中文 '引號' \"雙引號\"")) == "中文 '引號' \"雙引號\""
    assert "[uint32]2147483649" in s and "[uint32]2147483648" in s and "0x8000" not in s
    assert s.splitlines()[1] == "[Console]::OutputEncoding = [Text.Encoding]::UTF8"
    assert "StreamReader" in s and ".TotalSeconds -gt 300" in s and "AddHours(24)" in s


# ── WSL2：正常 ──────────────────────────────────────────


def test_wsl_start_heartbeat_and_stop():
    stop, fp, logs, st = _wsl("ok")
    assert st == [ka.ON_WINDOWS], st
    assert any("請 Windows 不要睡" in x for x in logs), logs
    args = fp.calls[0]
    assert args[0] == "/usr/bin/powershell.exe" and "-EncodedCommand" in args
    assert "-NoProfile" in args and "-NonInteractive" in args
    assert ka.decode_command(args[args.index("-EncodedCommand") + 1]) == ka.powershell_script()
    time.sleep(0.4)   # 送幾次心跳
    p = fp.procs[0]
    assert p.poll() is None, "還在跑的時候 PowerShell 不能先結束"
    stop()
    assert p.wait(timeout=5) == 0
    res = Path(fp.out).read_text(encoding="utf-8")
    beats = int(res.split()[0].split("=")[1])
    assert beats >= 2 and "eof=1" in res, res
    stop()   # 再呼叫一次不會出錯


def test_wsl_interop_late_and_fixed_path():
    files = {"/proc/version": WSL_FILES["/proc/version"],
             "/proc/sys/fs/binfmt_misc/WSLInterop-late": "enabled\n"}
    stop, fp, logs, st = _wsl("ok", files=files, which=lambda n: None, exists=lambda p: p == ka.POWERSHELL_FIXED)
    assert st == [ka.ON_WINDOWS], st
    assert fp.calls[0][0] == ka.POWERSHELL_FIXED
    stop()
    assert fp.procs[0].wait(timeout=5) == 0


def test_wsl_battery_warns():
    stop, fp, logs, st = _wsl("battery")
    assert st[0].startswith(ka.ON_WINDOWS) and "電池" in st[0], st
    assert any("電池" in x for x in logs)
    stop()
    fp.procs[0].wait(timeout=5)


# ── WSL2：退回 ──────────────────────────────────────────


def _assert_fallback(stop, st, logs, words: str):
    assert stop is ka._noop
    assert len(st) == 1 and st[0].startswith("沒開成") and words in st[0] and "README 第 3 節" in st[0], st
    assert any("沒辦法自動防止電腦睡眠" in x and words in x for x in logs), logs


def test_wsl_powershell_reports_fail():
    stop, fp, logs, st = _wsl("fail")
    _assert_fallback(stop, st, logs, "PowerShell 回報")
    assert fp.procs[0].wait(timeout=5) is not None


def test_wsl_powershell_exits_without_ok():
    stop, fp, logs, st = _wsl("exit")
    _assert_fallback(stop, st, logs, "PowerShell 結束了")


def test_wsl_no_ok_timeout_kills():
    t0 = time.monotonic()
    stop, fp, logs, st = _wsl("silent", ok_timeout_s=0.5)
    _assert_fallback(stop, st, logs, "沒有回應")
    assert time.monotonic() - t0 < 10
    assert fp.procs[0].poll() is not None, "沒回 OK 的要砍掉"


def test_wsl_interop_disabled_or_missing():
    for interop in ({"/proc/sys/fs/binfmt_misc/WSLInterop": "disabled\n"}, {}):
        files = {"/proc/version": WSL_FILES["/proc/version"], **interop}
        stop, fp, logs, st = _wsl("ok", files=files)
        _assert_fallback(stop, st, logs, "interop 關著")
        assert fp.calls == []


def test_wsl_powershell_not_found():
    stop, fp, logs, st = _wsl("ok", which=lambda n: None, exists=lambda p: False)
    _assert_fallback(stop, st, logs, "找不到 Windows 的 powershell.exe")
    assert fp.calls == []


def test_wsl_popen_oserror():
    def boom(*a, **k):
        raise OSError("Exec format error")
    st: list[str] = []
    stop = ka.keep_awake(lambda s: None, on_status=st.append, platform="linux", read=_reader(WSL_FILES),
                         which=lambda n: "/x/powershell.exe", popen=boom)
    assert stop is ka._noop and st[0].startswith("沒開成") and "叫不起來" in st[0]


def test_plain_linux_not_wsl():
    called = []
    st: list[str] = []
    stop = ka.keep_awake(lambda s: None, on_status=st.append, platform="linux",
                         read=_reader({"/proc/version": "Linux version 6.1 generic"}),
                         popen=lambda *a, **k: called.append(a))
    assert stop is ka._noop and called == [] and st[0].startswith("沒開成")


# ── Mac：跟 09-30 一樣 ─────────────────────────────────


def test_mac_caffeinate_unchanged():
    calls = []
    term = []

    def fake_popen(args, **kw):
        calls.append((list(args), kw))
        return types.SimpleNamespace(terminate=lambda: term.append(1))
    logs: list[str] = []
    st: list[str] = []
    stop = ka.keep_awake(logs.append, prefix="[AI 執行]", on_status=st.append, platform="darwin",
                         which=lambda n: "/usr/bin/caffeinate" if n == "caffeinate" else None, popen=fake_popen)
    assert calls == [(["caffeinate", "-i", "-m", "-s", "-w", str(os.getpid())], {})], calls
    assert logs == ["[AI 執行] 執行期間不讓電腦睡著（caffeinate），做完自動恢復"], logs
    assert st == [ka.ON_MAC]
    stop()
    assert term == [1]


def test_mac_without_caffeinate():
    called = []
    stop = ka.keep_awake(lambda s: None, platform="darwin", which=lambda n: None,
                         popen=lambda *a, **k: called.append(a))
    assert stop is ka._noop and called == []


def test_execute_keep_awake_delegates():
    from bookclub import execute

    seen = {}

    def fake(log, *, prefix, on_status):
        seen.update(prefix=prefix, on_status=on_status)
        return ka._noop
    cb = lambda s: None  # noqa: E731
    with mock.patch.object(ka, "keep_awake", fake):
        assert execute.keep_awake(print, cb) is ka._noop
    assert seen == {"prefix": "[AI 執行]", "on_status": cb}


# ── 第 1 步也包到（#172） ───────────────────────────────


def _fake_server():
    from bookclub.server import BookclubServer

    srv = types.SimpleNamespace(run_lock=threading.Lock(), workdir=TMP / "防睡眠_工作區",
                                run_state={**BookclubServer._fresh_run_state(), "running": True},
                                exec_state={**BookclubServer._fresh_run_state(), "running": True})
    srv._awake_setter = lambda state: BookclubServer._awake_setter(srv, state)
    srv._awake_done = lambda state: BookclubServer._awake_done(srv, state)
    return srv


def test_server_run_job_wraps_analyze():
    from bookclub import analyze, server

    for fail in (False, True):
        order: list[str] = []

        def fake_keep(log, *, prefix, on_status):
            order.append(f"開:{prefix}")
            on_status("已請電腦不要睡；測試")
            return lambda: order.append("關")

        def fake_analyze(*a, **k):
            order.append("分析")
            if fail:
                raise RuntimeError("分析出錯")

        srv = _fake_server()
        with mock.patch.object(ka, "keep_awake", fake_keep), mock.patch.object(analyze, "run_analyze", fake_analyze), \
                mock.patch.object(server, "data_dir", lambda: TMP):
            server.BookclubServer._run_job(srv, "影片.mp4", {})
        assert order == ["開:[分析一條龍]", "分析", "關"], order
        assert srv.run_state["防睡眠"] == "已請電腦不要睡；測試"
        assert srv.run_state["running"] is False
        assert (srv.run_state["error"] is not None) == fail


def test_server_exec_job_passes_status():
    from bookclub import execute, server

    def fake_run_execute(workdir, **kw):
        kw["awake_status"]("沒開成：測試原因")
        return {}
    srv = _fake_server()
    srv.exec_state["防睡眠"] = server.AWAKE_STARTING
    with mock.patch.object(execute, "run_execute", fake_run_execute):
        server.BookclubServer._exec_job(srv, {})
    assert srv.exec_state["防睡眠"] == "沒開成：測試原因"

    def no_awake(workdir, **kw):   # 還沒開到防睡眠就出錯：不要一直顯示「正在請電腦不要睡」
        raise RuntimeError("提早出錯")
    srv = _fake_server()
    srv.exec_state["防睡眠"] = server.AWAKE_STARTING
    with mock.patch.object(execute, "run_execute", no_awake):
        server.BookclubServer._exec_job(srv, {})
    assert srv.exec_state["防睡眠"] is None and "提早出錯" in srv.exec_state["error"]


def test_cli_run_analyze_wraps():
    from bookclub import analyze, cli

    order: list[str] = []
    with mock.patch.object(ka, "keep_awake", lambda log, prefix: (order.append(prefix), lambda: order.append("關"))[1]), \
            mock.patch.object(analyze, "run_analyze", lambda *a, **k: order.append("分析")):
        assert cli.main(["run", "analyze", "影片.mp4", str(TMP / "防睡眠_工作區")]) == 0
    assert order == ["[分析一條龍]", "分析", "關"], order


def test_run_execute_uses_awake_status():
    """run_execute 有傳 awake_status 給 keep_awake（真的步驟才開；測試用假步驟時不開，跟以前一樣）。"""
    import inspect

    from bookclub import execute

    src = inspect.getsource(execute.run_execute)
    assert "keep_awake(log, awake_status)" in src
    assert "awake_status" in inspect.signature(execute.run_execute).parameters


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"通過：{t.__name__}")
    print(f"\n共 {len(tests)} 個測試通過")


if __name__ == "__main__":
    _run_all()
