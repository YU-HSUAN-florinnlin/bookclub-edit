"""bookclub/fileio.py 的測試（10-07 檔案進出）：系統選檔視窗的指令與輸出解讀、Windows 路徑、複製（進度、沿用、改名、
取消）、Windows 的下載資料夾、在檔案總管打開。WSL2 的部分全部用假的子程序模擬（Mac 上跑得過），不會真的跳出視窗。

獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_fileio.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import base64
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections import namedtuple
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import fileio  # noqa: E402

R = namedtuple("R", "returncode stdout stderr")
Usage = namedtuple("Usage", "total used free")
WIN_VIDEO = "C:\\Users\\老師\\我的 影片\\第一堂 讀書會.mp4"
LINUX_VIDEO = "/mnt/c/Users/老師/我的 影片/第一堂 讀書會.mp4"


def _which(*have):
    return lambda name: f"/fake/{name}" if name in have else None


def test_platform_and_method():
    assert fileio.platform_kind("Darwin") == "mac"
    assert fileio.platform_kind("Linux", wsl=True) == "wsl"
    assert fileio.platform_kind("Linux", wsl=False) == "linux"
    assert fileio.pick_method("mac", which=_which("osascript")) == "mac"
    assert fileio.pick_method("wsl", which=_which("powershell.exe")) == "wsl"
    # WSL 沒把 Windows 的 PATH 接進來、固定位置也沒有（Mac 上沒有 /mnt/c）→ 叫不起來
    assert fileio.pick_method("wsl", which=_which()) is None
    assert fileio.pick_method("linux", env={}, which=_which("zenity")) is None          # 沒有桌面
    assert fileio.pick_method("linux", env={"DISPLAY": ":0"}, which=_which("zenity")) == "zenity"
    assert fileio.pick_method("linux", env={"WAYLAND_DISPLAY": "w"}, which=_which("kdialog")) == "kdialog"
    assert fileio.pick_method("linux", env={"DISPLAY": ":0"}, which=_which()) is None


def test_pick_commands():
    mac = fileio.pick_command("mac", "選 \"片頭\"")
    # 10-08：改用 JXA（AppleScript 的 activate 每次多等約 2.2 秒）；不再用 AppleScript 的 activate
    assert mac[:3] == ["osascript", "-l", "JavaScript"] and "-e" in mac and "activate" not in mac
    js = mac[-1]
    assert "chooseFile(" in js and '"public.movie"' in js and '"mp4"' in js and "app.activate()" in js
    assert '"選 \\"片頭\\""' in js   # 標題用 JSON 字串（引號有跳脫）
    wsl = fileio.pick_command("wsl", "選影片", which=_which("powershell.exe"))
    assert wsl[0] == "/fake/powershell.exe" and "-STA" in wsl and "-EncodedCommand" in wsl
    script = base64.b64decode(wsl[wsl.index("-EncodedCommand") + 1]).decode("utf-16-le")
    assert "OpenFileDialog" in script and "UTF8" in script and "'選影片'" in script and "*.mp4;*.mov" in script
    assert fileio.CANCEL_MARK in script and "TopMost" in script
    z = fileio.pick_command("zenity", "選影片")
    assert z[:2] == ["zenity", "--file-selection"] and any("*.mkv" in x for x in z)


def test_parse_pick():
    assert fileio.parse_pick("mac", 0, "/Users/x/影片 一.mp4\n".encode(), b"") == "/Users/x/影片 一.mp4"
    assert fileio.parse_pick("mac", 1, b"", "execution error: 使用者已取消。 (-128)".encode()) is None
    try:
        fileio.parse_pick("mac", 1, b"", b"no GUI")
        raise AssertionError("要丟 PickUnavailable")
    except fileio.PickUnavailable:
        pass
    # WSL：UTF-8 加 BOM、中文與空白
    out = ("\ufeff" + WIN_VIDEO + "\r\n").encode("utf-8")
    assert fileio.parse_pick("wsl", 0, out, b"") == WIN_VIDEO
    assert fileio.parse_pick("wsl", 0, (fileio.CANCEL_MARK + "\r\n").encode(), b"") is None
    for rc, so in ((1, b""), (0, b"")):
        try:
            fileio.parse_pick("wsl", rc, so, b"Exec format error")
            raise AssertionError("要丟 PickUnavailable")
        except fileio.PickUnavailable as e:
            assert "Windows" in str(e)
    assert fileio.parse_pick("zenity", 1, b"", b"") is None
    assert fileio.parse_pick("kdialog", 0, b"/home/a/b.mp4\n", b"") == "/home/a/b.mp4"


def _fake_wsl_run(calls, pick_out=None, pick_exc=None):
    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "wslpath":
            if cmd[1] == "-u":
                assert cmd[2] == WIN_VIDEO
                return R(0, (LINUX_VIDEO + "\n").encode(), b"")
            return R(0, b"C:\\out\n", b"")
        if pick_exc:
            raise pick_exc
        return R(0, (pick_out or WIN_VIDEO + "\r\n").encode("utf-8"), b"")
    return run


def test_pick_file_wsl_simulated():
    calls = []
    got = fileio.pick_file("選影片", method="wsl", run=_fake_wsl_run(calls), which=_which("powershell.exe"))
    assert got == Path(LINUX_VIDEO)                     # 中文、空白都照原樣轉過來
    assert calls[0][0] == "/fake/powershell.exe" and calls[1][:2] == ["wslpath", "-u"]
    # 按取消
    assert fileio.pick_file("選", method="wsl", run=_fake_wsl_run([], pick_out=fileio.CANCEL_MARK),
                            which=_which("powershell.exe")) is None
    # interop 關掉：執行 Windows 程式丟 OSError（Exec format error）
    for exc in (OSError(8, "Exec format error"), subprocess.TimeoutExpired("powershell.exe", 1)):
        try:
            fileio.pick_file("選", method="wsl", run=_fake_wsl_run([], pick_exc=exc), which=_which("powershell.exe"))
            raise AssertionError("要丟 PickUnavailable")
        except fileio.PickUnavailable:
            pass
    # 沒有任何選檔方式
    try:
        fileio.pick_file("選", run=_fake_wsl_run([]), which=_which())
    except fileio.PickUnavailable:
        pass


def test_pick_one_window_at_a_time():
    started, release = threading.Event(), threading.Event()

    def slow(cmd, **kw):
        started.set()
        release.wait(5)
        return R(0, b"/a/b.mp4\n", b"")

    th = threading.Thread(target=lambda: fileio.pick_file("選", method="mac", run=slow))
    th.start()
    assert started.wait(5)
    try:
        fileio.pick_file("選", method="mac", run=slow)
        raise AssertionError("第二個要被擋")
    except fileio.PickUnavailable as e:
        assert "已經開著" in str(e)
    release.set()
    th.join(5)


def test_windows_mount_and_needs_import():
    assert fileio.is_windows_mount("/mnt/c/Users/a.mp4") and fileio.is_windows_mount("/mnt/d/影片/b.mp4")
    assert not fileio.is_windows_mount("/mnt/wsl/a.mp4") and not fileio.is_windows_mount("/home/a/b.mp4")
    assert not fileio.is_windows_mount("/mnt") and not fileio.is_windows_mount("/Users/a/mnt/c/x.mp4")
    assert fileio.needs_import("/mnt/c/a.mp4", kind="wsl")
    assert not fileio.needs_import("/mnt/c/a.mp4", kind="mac")       # Mac 一律照用
    assert not fileio.needs_import("/home/u/a.mp4", kind="wsl")


def test_check_video():
    d = Path(tempfile.mkdtemp())
    (d / "a.txt").write_text("x")
    for name, exc in (("a.txt", ValueError), ("沒有.mp4", FileNotFoundError)):
        try:
            fileio.check_video(d / name)
            raise AssertionError(name)
        except exc:
            pass
    (d / "片 頭.MOV").write_bytes(b"1")
    assert fileio.check_video(d / "片 頭.MOV").name == "片 頭.MOV"


def test_choose_dest_reuse_and_rename():
    d = Path(tempfile.mkdtemp())
    assert fileio.choose_dest("第一堂.mp4", 10, d, True) == (d / "第一堂.mp4", False)
    (d / "第一堂.mp4").write_bytes(b"x" * 10)
    assert fileio.choose_dest("第一堂.mp4", 10, d, True) == (d / "第一堂.mp4", True)       # 同名同大小沿用
    assert fileio.choose_dest("第一堂.mp4", 11, d, True) == (d / "第一堂 (2).mp4", False)  # 不同大小改名
    (d / "第一堂 (2).mp4").write_bytes(b"x" * 11)
    assert fileio.choose_dest("第一堂.mp4", 11, d, True) == (d / "第一堂 (2).mp4", True)
    assert fileio.choose_dest("第一堂.mp4", 12, d, True) == (d / "第一堂 (3).mp4", False)
    # 成品拿出去：同名一律加編號
    assert fileio.choose_dest("第一堂.mp4", 10, d, False) == (d / "第一堂 (3).mp4", False)


def test_space_problem():
    d = Path(tempfile.mkdtemp()) / "還沒建" / "影片"
    gb = 1_000_000_000
    assert fileio.space_problem(2 * gb, d, gb, usage=lambda p: Usage(0, 0, 4 * gb)) is None
    msg = fileio.space_problem(2 * gb, d, gb, usage=lambda p: Usage(0, 0, int(2.5 * gb)))
    assert msg and "3.0 GB" in msg and "2.5 GB" in msg and "還差 0.5 GB" in msg
    src = Path(tempfile.mkdtemp()) / "a.mp4"
    src.write_bytes(b"x" * 100)
    try:
        fileio.plan_import(src, d, usage=lambda p: Usage(0, 0, 10))
        raise AssertionError("空間不夠要擋")
    except ValueError as e:
        assert "空間不夠" in str(e)
    assert fileio.plan_import(src, d, usage=lambda p: Usage(0, 0, 10 * gb)) == (d / "a.mp4", False)


def _wait(job, t=5):
    end = time.time() + t
    while job.running and time.time() < end:
        time.sleep(0.02)
    return job


def test_copy_job_copies_with_progress():
    src_dir, dst_dir = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp()) / "影片"
    src = src_dir / "第一堂 讀書會.mp4"
    src.write_bytes(bytes(range(256)) * 400)
    done = []
    job = fileio.CopyJob(src, dst_dir / src.name, purpose="影片", on_done=done.append, chunk=1000).start()
    _wait(job)
    d = job.to_dict()
    assert d["狀態"] == "完成" and d["百分比"] == 100.0 and (dst_dir / src.name).read_bytes() == src.read_bytes()
    assert done == [job] and not job.tmp.exists()
    # 沿用：不複製、直接當作完成
    job2 = fileio.CopyJob(src, dst_dir / src.name, purpose="影片", reused=True, on_done=done.append).start()
    assert job2.to_dict()["狀態"] == "沿用" and done[-1] is job2


def test_copy_job_cancel_and_fail_leave_nothing():
    src = Path(tempfile.mkdtemp()) / "a.mp4"
    src.write_bytes(b"x" * 5000)
    dst = Path(tempfile.mkdtemp()) / "a.mp4"
    done = []
    job = fileio.CopyJob(src, dst, purpose="影片", on_done=done.append, chunk=100)
    job.cancel()                       # 一開始就取消：第一塊之前就停
    _wait(job.start())
    assert job.state == "取消" and not dst.exists() and not job.tmp.exists() and not done
    job = fileio.CopyJob(src, dst, purpose="影片", on_done=done.append)
    src.unlink()                       # 來源不見了
    _wait(job.start())
    assert job.state == "失敗" and job.error and not dst.exists() and not job.tmp.exists() and not done


def test_windows_downloads_simulated():
    dl = Path(tempfile.mkdtemp()) / "Downloads"
    dl.mkdir()
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "wslpath":
            assert cmd[2] == "C:\\Users\\老師\\Downloads"
            return R(0, (str(dl) + "\n").encode(), b"")
        if cmd[0].endswith("powershell.exe"):
            return R(0, "\ufeffC:\\Users\\老師\\Downloads\r\n".encode("utf-8"), b"")
        raise AssertionError(cmd)

    assert fileio.windows_downloads(run=run, which=_which("powershell.exe", "cmd.exe")) == dl
    # PowerShell 不行 → cmd.exe /c echo %USERPROFILE% ＋ \Downloads
    calls.clear()

    def run2(cmd, **kw):
        calls.append(cmd)
        if cmd[0].endswith("powershell.exe"):
            raise OSError(8, "Exec format error")
        if cmd[0].endswith("cmd.exe"):
            assert cmd[1:] == ["/c", "echo %USERPROFILE%"]
            return R(0, b"C:\\Users\\\xe8\x80\x81\xe5\xb8\xab\r\n", b"")
        return run(cmd, **kw)

    assert fileio.windows_downloads(run=run2, which=_which("powershell.exe", "cmd.exe")) == dl
    try:
        fileio.windows_downloads(run=run2, which=_which())
        raise AssertionError("沒有 cmd.exe 要說清楚")
    except RuntimeError as e:
        assert "cmd.exe" in str(e)


def test_reveal_simulated():
    d = Path(tempfile.mkdtemp())
    f = d / "最終成品_0-98_sw.mp4"
    f.write_bytes(b"1")
    runs, opens = [], []

    def run(cmd, **kw):
        runs.append(cmd)
        if cmd[0] == "wslpath":
            return R(0, b"\\\\wsl.localhost\\Ubuntu\\home\\u\\out\n", b"")
        return R(0, b"", b"")

    def popen(cmd, **kw):
        opens.append(cmd)
        return None   # explorer.exe 成功也回 1：不看結束碼，叫得起來就好

    msg = fileio.reveal(f, kind="wsl", run=run, popen=popen, which=_which("explorer.exe"))
    assert "檔案總管" in msg and runs[-1] == ["wslpath", "-w", str(d)]
    assert opens[-1] == ["/fake/explorer.exe", "\\\\wsl.localhost\\Ubuntu\\home\\u\\out"]
    assert "Finder" in fileio.reveal(f, kind="mac", run=run, popen=popen) and runs[-1] == ["open", "-R", str(f)]
    fileio.reveal(d, kind="mac", run=run, popen=popen)
    assert runs[-1] == ["open", str(d)]

    def broken(cmd, **kw):
        raise OSError(8, "Exec format error")

    try:
        fileio.reveal(f, kind="wsl", run=run, popen=broken, which=_which("explorer.exe"))
        raise AssertionError("叫不起來要說")
    except RuntimeError as e:
        assert "檔案總管" in str(e)
    try:
        fileio.reveal(f, kind="mac", run=broken)
        raise AssertionError("Mac 叫不起 Finder 也要說")
    except RuntimeError as e:
        assert "Finder" in str(e)


def test_review_fixes_1007():
    # 審查第 2 點：WSL2 同時看 Ubuntu 與 C 槽，取剩比較少的
    gb = 1_000_000_000
    d1, d2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    free = {str(d1): 100 * gb, str(d2): 2 * gb}
    usage = lambda p: Usage(0, 0, free[p])   # noqa: E731
    msg = fileio.space_problem(3 * gb, d1, gb, "Ubuntu（WSL2）與 C 槽", usage=usage, also=(d2,))
    assert msg and "只剩 2.0 GB" in msg
    assert fileio.space_problem(3 * gb, d1, gb, usage=usage) is None
    assert fileio.wsl_host_disks("mac") == ()
    # 第 3 點：暫存檔名帶工作編號，兩個工作同一個目的地不會互刪
    src = d1 / "a.mp4"
    src.write_bytes(b"x")
    j1 = fileio.CopyJob(src, d2 / "a.mp4", purpose="影片")
    j2 = fileio.CopyJob(src, d2 / "a.mp4", purpose="影片")
    assert j1.tmp != j2.tmp and j1.id in j1.tmp.name and j1.tmp.name.endswith(".複製中")
    # 第 4 點：清理只清自己取名的
    old = d2 / f".a.mp4.{j1.id}.複製中"
    old.write_bytes(b"1")
    (d2 / "a.mp4.複製中").write_bytes(b"1")   # 不是點開頭：不是這個工具取的，不動
    # 10-07：只清超過 1 小時沒改動的（同資料夾另一個伺服器正在複製的不動）
    fresh = d2 / f".b.mp4.{j2.id}.複製中"
    fresh.write_bytes(b"2")
    t = time.time()
    os.utime(old, (t - 7200, t - 7200))
    os.utime(d2 / "a.mp4.複製中", (t - 7200, t - 7200))
    assert fileio.cleanup_partials(d2) == 1 and not old.exists()
    assert fresh.exists() and (d2 / "a.mp4.複製中").exists()
    assert fileio.cleanup_partials(d2, now=t + 7200) == 1 and not fresh.exists()
    assert fileio.cleanup_partials(d2 / "沒有這個資料夾") == 0
    # 第 7 點：叫 Windows 的程式時 stdin 接空的
    seen = {}

    def run(cmd, **kw):
        seen.update(kw)
        return R(0, (fileio.CANCEL_MARK + "\n").encode(), b"")
    fileio.pick_file("選", method="wsl", run=run, which=_which("powershell.exe"))
    assert seen.get("stdin") is subprocess.DEVNULL
    # 第 8 點：wslpath 卡住 → 當作叫不起來（退回網頁瀏覽）
    def slow_wslpath(cmd, **kw):
        if cmd[0] == "wslpath":
            raise subprocess.TimeoutExpired("wslpath", 20)
        return R(0, (WIN_VIDEO + "\n").encode(), b"")
    try:
        fileio.pick_file("選", method="wsl", run=slow_wslpath, which=_which("powershell.exe"))
        raise AssertionError("要丟 PickUnavailable")
    except fileio.PickUnavailable:
        pass


FAKE_PICKER = """#!{py}
import json, sys
print(json.dumps({{"ready": True}}), flush=True)
for line in sys.stdin:
    req = json.loads(line)
    print(json.dumps({{"opening": True}}), flush=True)
    if req["title"] == "取消":
        print(json.dumps({{"cancel": True}}), flush=True)
    elif req["title"] == "掛掉":
        sys.exit(3)
    else:
        print(json.dumps({{"path": "/Users/x/影片 一/" + req["title"] + "." + req["exts"][0]}}, ensure_ascii=False), flush=True)
"""


def test_mac_resident_picker_protocol():
    # 10-08：Mac 改用常駐的選檔小程式（預熱好之後約 0.3～0.7 秒出視窗）。這裡用假的小程式測溝通方式，不會跳出視窗
    fake = Path(tempfile.mkdtemp()) / "fake_picker"
    fake.write_text(FAKE_PICKER.format(py=sys.executable), encoding="utf-8")
    fake.chmod(0o755)
    mp = fileio.MacPicker()
    mp.build = lambda which=None: fake
    assert not mp.usable()
    assert mp.start() and mp.usable()
    got, opened = mp.pick("第一堂", (".mp4",))
    assert got == "/Users/x/影片 一/第一堂.mp4" and opened >= 0
    assert mp.pick("取消", (".mp4",))[0] is None
    try:
        mp.pick("掛掉", (".mp4",))
        raise AssertionError("小程式掛掉要丟 PickUnavailable")
    except fileio.PickUnavailable:
        pass
    assert not mp.usable() and mp.proc is None
    assert mp.start() and mp.usable()          # 下一次重開
    # pick_file：常駐小程式好了就用它；它掛掉就這一次改叫 osascript
    saved = fileio.MAC_PICKER
    fileio.MAC_PICKER = mp
    try:
        assert fileio.pick_file("第二堂", method="mac", run=lambda *a, **k: 1 / 0) == Path("/Users/x/影片 一/第二堂.mp4")
        calls = []
        mp.start_in_background = lambda: calls.append("重開")
        def osa(cmd, **kw):
            calls.append(cmd[0])
            return R(0, b"/a/b.mp4\n", b"")
        assert fileio.pick_file("掛掉", method="mac", run=osa) == Path("/a/b.mp4")
        assert calls == ["重開", "osascript"]
    finally:
        fileio.MAC_PICKER = saved
    # 沒有 swiftc（或不是 Mac）：不編譯、回 None，照舊叫 osascript
    assert fileio.MacPicker.build(which=lambda name: None) is None


def test_filepicker_swift_compiles():
    # 原始碼編得過（不執行，不會跳出視窗）。沒有 swiftc 的電腦略過
    import shutil as _sh

    if sys.platform != "darwin" or not _sh.which("swiftc"):
        print("（沒有 swiftc，略過）")
        return
    out = Path(tempfile.mkdtemp()) / "filepicker"
    import os as _os

    env = {k: v for k, v in _os.environ.items() if k not in ("TMPDIR", "TEMP", "TMP")}   # swiftc 遇到中文暫存路徑會當掉
    r = subprocess.run(["swiftc", "-O", "-o", str(out), str(REPO_ROOT / "bookclub" / "filepicker.swift")],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0 and out.is_file(), r.stderr[-500:]


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"通過：{t.__name__}")
    print(f"\n共 {len(tests)} 個測試通過")


if __name__ == "__main__":
    _run_all()
