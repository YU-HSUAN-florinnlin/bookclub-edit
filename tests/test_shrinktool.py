"""#290、#289：Windows 一鍵「縮小虛擬硬碟」——放到桌面、清理後提醒（只在 WSL）、`bookclub disk` 找新版 WSL 的虛擬硬碟。

全部用假路徑、假的 run／which（不叫 PowerShell、不碰真的桌面）。.ps1 沒辦法在 Mac 執行，只檢查檔案格式與關鍵步驟。
獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_shrinktool.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import closing, shrinktool  # noqa: E402

WEB = REPO_ROOT / "bookclub" / "web"
NODE = shutil.which("node")
TAIL = "請將錯誤訊息通知開發者。"


def _mk(p: Path, n: int = 10) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\0" * n)
    return p


# ---------------------------------------------------------------------------
# #289：新版 WSL 的位置、disk 報告文字
# ---------------------------------------------------------------------------

def test_vhdx_finds_new_and_old_locations():
    with tempfile.TemporaryDirectory() as t:
        c = Path(t)
        old = _mk(c / "Users" / "老師" / "AppData" / "Local" / "Packages" / "CanonicalGroup.Ubuntu" / "LocalState" / "ext4.vhdx")
        new = _mk(c / "Users" / "老師" / "AppData" / "Local" / "wsl" / "{1234-abcd}" / "ext4.vhdx")
        _mk(c / "Users" / "老師" / "AppData" / "Local" / "wsl" / "{1234-abcd}" / "其他.vhdx")   # 不是 ext4.vhdx：不算
        found = closing._vhdx(c)
        assert found == sorted([old, new]), found
        assert closing._vhdx(c / "沒有這個") == []


def test_disk_report_wsl_new_location_and_wording():
    with tempfile.TemporaryDirectory() as t:
        home = Path(t)
        c = home / "c"
        _mk(c / "Users" / "老師" / "AppData" / "Local" / "wsl" / "{1}" / "ext4.vhdx", 9_000_000)
        lines = closing.disk_report(home, home / "讀書會剪輯資料", wsl=True, mnt_c=c)
        text = "\n".join(lines)
        assert "ext4.vhdx" in text and "9 MB" in text and "C 槽還剩" in text
        assert "這是整個 Ubuntu（含系統），上面各項只算讀書會剪輯的資料" in text
        assert "讀書會剪輯工具" in text and "以系統管理員身分執行" in text and "放到桌面" in text
        assert "README" not in text and "網頁" not in text
        # 不是 WSL：不印虛擬硬碟
        assert "虛擬硬碟" not in "\n".join(closing.disk_report(home, home / "讀書會剪輯資料", wsl=False, mnt_c=c))


def test_disk_report_uv_cache_not_suggested_for_cleaning():
    """#254：~/.cache/uv 多數跟 .venv 共用同一份檔案，清了只省約 0.2GB；disk 報告不能叫人去清它。"""
    with tempfile.TemporaryDirectory() as t:
        home = Path(t)
        _mk(home / ".cache" / "uv" / "x.whl", 2_500_000)
        text = "\n".join(closing.disk_report(home, home / "讀書會剪輯資料", wsl=False))
        row = next(l for l in text.splitlines() if "~/.cache/uv" in l)
        assert "不用特地清" in row and "可以清" not in row and "可以刪" not in row, row


# ---------------------------------------------------------------------------
# #290：Windows 桌面路徑（PowerShell → wslpath；PowerShell 不行改 cmd）
# ---------------------------------------------------------------------------

def _fake_run(desk: Path, ps_out: str | None, cmd_out: str = "C:\\Users\\老師\r\n", calls: list | None = None):
    def run(argv, **kw):
        if calls is not None:
            calls.append(argv)
        exe = Path(argv[0]).name
        if exe == "wslpath":
            win = argv[2]
            ok = win.lower().endswith("desktop")
            return SimpleNamespace(returncode=0 if ok else 1, stdout=(str(desk) + "\n").encode() if ok else b"",
                                   stderr=b"")
        if exe == "powershell.exe":
            if ps_out is None:
                return SimpleNamespace(returncode=1, stdout=b"", stderr=b"")
            return SimpleNamespace(returncode=0, stdout=("\ufeff" + ps_out + "\r\n").encode("utf-8"), stderr=b"")
        if exe == "cmd.exe":
            return SimpleNamespace(returncode=0, stdout=cmd_out.encode("utf-8"), stderr=b"")
        raise AssertionError(argv)
    return run


def _which(name):
    return f"/fake/{name}" if name in ("powershell.exe", "cmd.exe") else None


def test_windows_desktop_via_powershell():
    with tempfile.TemporaryDirectory() as t:
        desk = Path(t) / "Desktop"
        desk.mkdir()
        calls: list = []
        p = shrinktool.windows_desktop(run=_fake_run(desk, "C:\\Users\\老師\\OneDrive\\Desktop", calls=calls), which=_which)
        assert p == desk
        assert calls[0][0] == "/fake/powershell.exe" and "-EncodedCommand" in calls[0]
        assert calls[1] == ["wslpath", "-u", "C:\\Users\\老師\\OneDrive\\Desktop"]


def test_windows_desktop_falls_back_to_cmd():
    with tempfile.TemporaryDirectory() as t:
        desk = Path(t) / "Desktop"
        desk.mkdir()
        calls: list = []
        p = shrinktool.windows_desktop(run=_fake_run(desk, None, calls=calls), which=_which)
        assert p == desk
        assert calls[-1] == ["wslpath", "-u", "C:\\Users\\老師\\Desktop"]


def test_windows_desktop_missing_raises():
    with tempfile.TemporaryDirectory() as t:
        try:
            shrinktool.windows_desktop(run=_fake_run(Path(t) / "沒有", "C:\\Users\\老師\\Desktop"), which=_which)
        except shrinktool.ShrinkToolError as e:
            assert "桌面" in str(e)
        else:
            raise AssertionError("應該找不到桌面")
        try:
            shrinktool.windows_desktop(run=_fake_run(Path(t), None), which=lambda n: None)
        except shrinktool.ShrinkToolError as e:
            assert "cmd.exe" in str(e)
        else:
            raise AssertionError("沒有 cmd.exe 應該丟錯")


# ---------------------------------------------------------------------------
# 放到桌面、狀態
# ---------------------------------------------------------------------------

def test_install_copies_and_overwrites():
    with tempfile.TemporaryDirectory() as t:
        desk = Path(t) / "Desktop"
        desk.mkdir()
        assert not shrinktool.installed(desk)
        dest = shrinktool.install(desk)
        assert dest == desk / "讀書會剪輯工具"
        for n in shrinktool.TOOL_FILES:
            assert (dest / n).read_bytes() == (shrinktool.WIN_DIR / n).read_bytes()
        assert shrinktool.installed(desk)
        assert not any(p.name.endswith(".複製中") for p in dest.iterdir())
        (dest / "縮小虛擬硬碟.ps1").write_text("舊版", encoding="utf-8")   # 已經有 → 蓋成這一版
        shrinktool.install(desk)
        assert (dest / "縮小虛擬硬碟.ps1").read_bytes() == (shrinktool.WIN_DIR / "縮小虛擬硬碟.ps1").read_bytes()
        # 失敗：暫存檔不留在桌面資料夾；被占用（舊視窗還開著）是可以改正的
        import errno as _errno
        from unittest import mock
        real_replace = Path.replace

        def busy_replace(self, target):
            if str(target).endswith(".ps1"):
                raise PermissionError(_errno.EACCES, "Permission denied")
            return real_replace(self, target)
        with mock.patch.object(Path, "replace", busy_replace):
            try:
                shrinktool.install(desk)
            except shrinktool.ShrinkToolBusy as e:
                assert "先關掉縮小虛擬硬碟的視窗" in str(e) and isinstance(e, ValueError)
            else:
                raise AssertionError
        assert not any(".複製中" in p.name for p in dest.iterdir()), list(dest.iterdir())

        def full_replace(self, target):
            raise OSError(_errno.ENOSPC, "No space left on device")
        with mock.patch.object(Path, "replace", full_replace):
            try:
                shrinktool.install(desk)
            except shrinktool.ShrinkToolError as e:
                assert str(e).endswith(TAIL)
            else:
                raise AssertionError
        assert not any(".複製中" in p.name for p in dest.iterdir())
        # 工具資料夾少檔
        try:
            shrinktool.install(desk, src=Path(t) / "空的")
        except shrinktool.ShrinkToolError as e:
            assert str(e).endswith(TAIL)
        else:
            raise AssertionError


def test_status_only_on_wsl():
    def boom():
        raise AssertionError("不是 WSL 不該去問桌面")
    for kind in ("mac", "linux", "other"):
        s = shrinktool.status(kind, desktop_fn=boom)
        assert s == {"系統": kind, "顯示": False}
    with tempfile.TemporaryDirectory() as t:
        desk = Path(t)
        s = shrinktool.status("wsl", desktop_fn=lambda: desk)
        assert s["顯示"] and not s["已放到桌面"] and s["提醒"] == shrinktool.REMINDER
        assert "以系統管理員身分執行" in s["提醒"] and "網頁" not in s["提醒"]
        shrinktool.install(desk)
        assert shrinktool.status("wsl", desktop_fn=lambda: desk)["已放到桌面"]

        def nodesk():
            raise shrinktool.ShrinkToolError("找不到 Windows 的桌面")
        assert shrinktool.status("wsl", desktop_fn=nodesk)["已放到桌面"] is False   # 找不到桌面：當作還沒放


def test_put_on_desktop():
    with tempfile.TemporaryDirectory() as t:
        r = shrinktool.put_on_desktop("wsl", desktop_fn=lambda: Path(t))
        assert r["ok"] and "讀書會剪輯工具" in r["說明"] and shrinktool.installed(Path(t))
    try:
        shrinktool.put_on_desktop("mac", desktop_fn=lambda: Path("/不會用到"))
    except ValueError:
        pass
    else:
        raise AssertionError

    def nodesk():
        raise shrinktool.ShrinkToolError("找不到 Windows 的桌面：C:\\x")
    try:
        shrinktool.put_on_desktop("wsl", desktop_fn=nodesk)
    except shrinktool.ShrinkToolError as e:
        assert str(e).endswith(TAIL) and "桌面" in str(e)
    else:
        raise AssertionError


def test_server_routes_wired():
    src = (REPO_ROOT / "bookclub" / "server.py").read_text(encoding="utf-8")
    assert 'path == "/api/shrink-tool"' in src and 'path == "/api/shrink-tool/install"' in src
    from bookclub import server
    assert server.closed_post_allowed("/api/shrink-tool/install", {})   # 已結案也能放到桌面


# ---------------------------------------------------------------------------
# Windows 一鍵檔：格式與關鍵步驟（實際執行只能在 Windows 驗）
# ---------------------------------------------------------------------------

def test_cmd_file_ascii_crlf_and_calls_ps1():
    import base64
    raw = (shrinktool.WIN_DIR / "縮小虛擬硬碟.cmd").read_bytes()
    raw.decode("ascii")                                   # 全部 ASCII：cmd 用哪種編碼讀都不會壞
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    text = raw.decode()
    assert "-ExecutionPolicy Bypass" in text and '"%~dpn0.ps1"' in text and "chcp 65001" in text
    assert 'if not "%errorlevel%"=="0" pause' in text      # 審查 #8：結束碼是負數時也停住
    # 找不到 .ps1：先說原因（中文用 -EncodedCommand 帶）再停住
    assert 'if not exist "%~dpn0.ps1" (' in text and text.index("if not exist") < text.index("-File")
    enc = re.search(r"-EncodedCommand (\S+)", text).group(1)
    msg = base64.b64decode(enc).decode("utf-16-le")
    assert "找不到「縮小虛擬硬碟.ps1」" in msg and msg.rstrip("' -ForegroundColor Yellow").endswith(TAIL)
    block = text[text.index("if not exist"):text.index('powershell.exe -NoProfile -ExecutionPolicy')]
    assert "pause" in block and "exit /b 1" in block


def test_ps1_has_tool_shell_snippet():
    """審查 #2：Has-Tool 的 sh 片段在 Mac 上用假的 /home、/root 實際跑：只有一般帳號有、只有 root 有、都沒有。"""
    s = (shrinktool.WIN_DIR / "縮小虛擬硬碟.ps1").read_bytes()[3:].decode("utf-8")
    sh = re.search(r"^\$HasToolSh = '(.*)'\r$", s, re.M).group(1)
    assert '"' not in sh                                  # PowerShell 5.1 傳給外部程式會弄丟雙引號
    assert "-u root -e sh -c $HasToolSh" in s             # -e：不經過預設 shell（不然 $d 會先被展開）
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        run = lambda: subprocess.run(["sh", "-c", sh.replace("/home/", f"{t}/home/").replace("/root/", f"{t}/root/")]).returncode
        (t / "home" / "teacher").mkdir(parents=True)
        assert run() == 1
        (t / "home" / "teacher" / ".cache" / "bookclub").mkdir(parents=True)
        assert run() == 0                                  # 以前：/root 那個不存在就回失敗
        shutil.rmtree(t / "home")
        (t / "root" / ".cache" / "bookclub").mkdir(parents=True)
        assert run() == 0


def test_ps1_bom_crlf_and_steps():
    raw = (shrinktool.WIN_DIR / "縮小虛擬硬碟.ps1").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")               # UTF-8 BOM：Windows PowerShell 5.1 才認得中文
    assert b"\n" not in raw.replace(b"\r\n", b"")
    s = raw[3:].decode("utf-8")
    assert "網頁" not in s and "set-sparse true" not in s.lower()
    assert "請截圖給開發者" not in s and "請截圖這個視窗。$Tail" in s        # 錯誤句尾統一（專案規則）
    assert "請關掉這個視窗，在檔案上按右鍵選「以系統管理員身分執行」。" in s
    assert "第 4 步正在跑，等它跑完再縮小。" in s and "127.0.0.1:8766/api/run/status" in s and "第4步執行中" in s
    assert "工具已經關掉了，要用的話請重新開（照平常的方式）。" in s
    assert "GetCompressedFileSizeW" in s and "SparseFile" in s and "--set-sparse false" in s
    assert r"Software\Microsoft\Windows\CurrentVersion\Lxss" in s and "LocalState" in s
    assert "-u root -- fstrim -av" in s
    # 順序：admin → 第 4 步 → 找 → fstrim → sparse → shutdown → diskpart
    order = ["IsInRole", "api/run/status", "Find-Distros |", "-u root -- fstrim -av", "--manage $distro --set-sparse false", "Running-Distros $names",
             "'attach vdisk readonly'"]
    pos = [s.index(k) for k in order]
    assert pos == sorted(pos), list(zip(order, pos))
    m = re.search(r"Run-Diskpart @\((.*?)\)\r\n", s)
    assert m and re.findall(r"'([a-z ]+)'", m.group(1)) == ["attach vdisk readonly", "compact vdisk", "detach vdisk"], m
    assert "'detach vdisk noerr'" in s                    # 失敗時也一定卸下
    assert "$ErrorActionPreference = 'Continue'" in s
    # 審查 #1：只有連線被拒才算沒開；逾時 10 秒；不確定時問人；不會先說「沒有在跑」再說「正在跑」
    assert "-TimeoutSec 10" in s and "WebExceptionStatus]::ConnectFailure" in s
    assert "第 4 步現在有沒有在跑？有在跑請直接關掉這個視窗。" in s
    i_busy = s.index("if ($toolState -eq 'busy') { Stop-Here")
    assert s.index("工具開著，沒有在跑第 4 步") > i_busy
    enter = s[s.index("接下來會關掉 Ubuntu"):s.index("按 Enter 開始")]
    assert "第 4 步正在跑的話，請直接關掉這個視窗" in enter
    # 關掉 Ubuntu 之前一定有確認，而且能明確取消（打 n）；取消在 fstrim／shutdown 之前
    i_ask = s.index("$go = Read-Host '按 Enter 開始")
    i_cancel = s.index("已取消：沒有關掉 Ubuntu，也沒有縮小。")
    assert s.index("Step '關掉 Ubuntu'") > i_cancel > i_ask and s.index("-u root -- fstrim -av") > i_cancel
    assert s.count("wsl.exe --shutdown") == 2 and all(s.index("wsl.exe --shutdown") > i_cancel for _ in (0,))
    cancel_re = re.search(r"\$go -match '(.*?)'", s).group(1)
    for ans in ("n", "N", " no ", "否", "不", "取消"):
        assert re.match(cancel_re, ans, re.I), ans
    for ans in ("", "y", "好", "nope"):
        assert not re.match(cancel_re, ans, re.I), ans
    # 審查 #5、#6：VhdFileName、沒有名稱時顯示資料夾、名稱都沒有 Ubuntu 時一律請人選
    assert "VhdFileName" in s and 'Label = "資料夾 $folder"' in s and "$mustAsk = $true" in s
    for line in s.splitlines():                           # 錯誤句尾都用 $Tail（「請將錯誤訊息通知開發者。」）
        assert "開發者" not in line or "$Tail" in line or line.startswith("$Tail =") or line.lstrip().startswith("#"), line


# ---------------------------------------------------------------------------
# 畫面：提醒只在 WSL、桌面沒有檔才給「放到桌面」
# ---------------------------------------------------------------------------

def _js_fn(src: str, name: str) -> str:
    m = re.search(rf"^(async )?function {name}\(.*?^\}}\n", src, re.S | re.M)
    assert m, name
    return m.group(0)


def test_js_cleaned_alert_opts():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    if not NODE:
        print("（沒有 node，略過）")
        return
    code = _js_fn(app, "cleanedAlertOpts") + """
      const R = "Ubuntu 裡的空間清出來了；要把空間還給 C 槽……";
      console.log(JSON.stringify([
        cleanedAlertOpts("清掉了", "已清掉，空出 2 GB。", {"系統": "mac", "顯示": false}),
        cleanedAlertOpts("清掉了", "已清掉，空出 2 GB。", null),
        cleanedAlertOpts("清掉了", "已清掉，空出 2 GB。", {"顯示": true, "提醒": R, "已放到桌面": true}),
        cleanedAlertOpts("清掉了", "已清掉，空出 2 GB。", {"顯示": true, "提醒": R, "已放到桌面": false}),
      ]));"""
    r = subprocess.run([NODE, "-e", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    mac, none, have, need = json.loads(r.stdout)
    assert mac == none == {"kind": "alert", "title": "清掉了", "message": "已清掉，空出 2 GB。", "ok": "好"}
    assert have["kind"] == "alert" and have["message"] == "已清掉，空出 2 GB。\nUbuntu 裡的空間清出來了；要把空間還給 C 槽……"
    assert need["kind"] == "confirm" and need["ok"] == "放到桌面" and need["cancel"] == "好" and "桌面還沒有" in need["message"]


def test_js_wiring():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    ho = (WEB / "handoff.js").read_text(encoding="utf-8")
    fio = (WEB / "fileio.js").read_text(encoding="utf-8")
    i = app.index("const TOOL_MENU = [")
    menu = app[i:i + 600]
    assert 'action: "shrinkDesktop"' in menu and "wslOnly: true" in menu and "把『縮小虛擬硬碟』放到桌面" in menu
    assert "filter((m) => !m.wslOnly || wsl)" in app
    assert 'title: "還不能放到桌面"' in app and 'e.data["意外"]) await uiErrorAlert' in app   # 被占用：一般提醒；意外：錯誤視窗
    assert "cleanedAlertOpts(title, message, await shrinkStatus())" in app            # 審查 #13：沿用載入時查到的
    assert ho.count('cleanedAlert("清掉了"') == 2                  # 第 3 步交接後清掉、總覽清掉這集
    assert 'uiAlert({ title: "清掉了"' not in ho
    # 第 5 步清完：Mac、Windows 都跳成功視窗（宇軒 10-25），提醒段落只有 WSL 才加（cleanedAlertOpts 管）
    i5 = fio.index("#290（宇軒 10-25）")
    seg = fio[i5:i5 + 500]
    assert 'cleanedAlert("清好了", okMsg)' in seg and 'uiAlert({ title: "清好了", message: okMsg, ok: "好" })' in seg
    assert "shrinkStatus(true).then" not in seg and 'info["顯示"]' not in seg


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
