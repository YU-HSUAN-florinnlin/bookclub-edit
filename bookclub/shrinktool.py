"""Windows 一鍵「縮小虛擬硬碟」（#290）：把 `windows/縮小虛擬硬碟.cmd`＋`.ps1` 放到 Windows 桌面，清理後提醒。

Ubuntu（WSL2）整個裝在 C 槽的 `ext4.vhdx`，在 Ubuntu 裡刪檔不會讓 C 槽多出空間，要關掉 Ubuntu、用系統管理員身分
跑 diskpart 的 compact 才會縮小（10-25 舊 Windows 實測 15.02→13.06 GB；sparse 不可靠，不用）。這件事只能在 Windows 那邊做，
所以工具只負責把那兩個檔複製到桌面「讀書會剪輯工具」資料夾，讓人在檔案上按右鍵「以系統管理員身分執行」。

只在 WSL 上有用；Mac／一般 Linux 一律不顯示、不複製。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from bookclub import fileio

WIN_DIR = Path(__file__).resolve().parent.parent / "windows"
TOOL_FILES = ("縮小虛擬硬碟.cmd", "縮小虛擬硬碟.ps1")
DESKTOP_FOLDER = "讀書會剪輯工具"
MENU_LABEL = "把『縮小虛擬硬碟』放到桌面"

REMINDER = ("Ubuntu 裡的空間清出來了；要把空間還給 C 槽，請關掉工具，到桌面「讀書會剪輯工具」資料夾，"
            "在「縮小虛擬硬碟」上按右鍵選「以系統管理員身分執行」。")

DESKTOP_PS = "\n".join([
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
    "$p = [Environment]::GetFolderPath('Desktop')",
    "if (-not $p) { $p = Join-Path $env:USERPROFILE 'Desktop' }",
    "Write-Output $p",
])


class ShrinkToolError(RuntimeError):
    """放到桌面失敗（訊息給人看）。"""


def windows_desktop(run=None, which=None) -> Path:
    """Windows 桌面在 Ubuntu 這邊的路徑。先問 PowerShell（桌面被雲端空間接管、搬過位置也找得到、中文帳號不亂碼）；
    不行再用 `cmd.exe /c echo %USERPROFILE%`＋`\\Desktop`。找不到就丟 ShrinkToolError。"""
    which = which or shutil.which
    run = run or subprocess.run
    kw = {"cwd": "/mnt/c"} if Path("/mnt/c").is_dir() else {}
    win = None
    ps = fileio._find_exe("powershell.exe", fileio.PS_CANDIDATES, which)
    if ps:
        try:
            r = run([ps, "-NoProfile", "-NonInteractive", "-EncodedCommand", fileio.encode_ps(DESKTOP_PS)],
                    capture_output=True, stdin=subprocess.DEVNULL, timeout=fileio.QUICK_TIMEOUT_S, **kw)
            out = fileio._decode(r.stdout).replace("﻿", "").strip().splitlines()
            if r.returncode == 0 and out and ":" in out[-1]:
                win = out[-1].strip()
        except (OSError, subprocess.TimeoutExpired):
            win = None
    if not win:
        cmd = fileio._find_exe("cmd.exe", fileio.CMD_CANDIDATES, which)
        if not cmd:
            raise ShrinkToolError("找不到 Windows 的 cmd.exe（Ubuntu 呼叫 Windows 程式的功能可能被關掉了）")
        try:
            r = run([cmd, "/c", "echo %USERPROFILE%"], capture_output=True, stdin=subprocess.DEVNULL,
                    timeout=fileio.QUICK_TIMEOUT_S, **kw)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ShrinkToolError(f"問不到 Windows 的使用者資料夾：{e}") from None
        prof = fileio._decode(r.stdout).strip().splitlines()
        if r.returncode != 0 or not prof or "%USERPROFILE%" in prof[-1]:
            raise ShrinkToolError("問不到 Windows 的使用者資料夾")
        win = prof[-1].strip().rstrip("\\") + "\\Desktop"
    try:
        p = Path(fileio.wsl_to_linux(win, run=run))
    except fileio.PickUnavailable as e:
        raise ShrinkToolError(str(e)) from None
    if not p.is_dir():
        raise ShrinkToolError(f"找不到 Windows 的桌面：{win}")
    return p


def installed(desktop: Path) -> bool:
    """桌面「讀書會剪輯工具」資料夾裡兩個檔都在。"""
    d = Path(desktop) / DESKTOP_FOLDER
    return all((d / n).is_file() for n in TOOL_FILES)


def install(desktop: Path, src: Path | None = None) -> Path:
    """把 .cmd＋.ps1 複製到 `<桌面>/讀書會剪輯工具/`（已經有就蓋成這一版）。回傳那個資料夾。"""
    src = WIN_DIR if src is None else Path(src)
    missing = [n for n in TOOL_FILES if not (src / n).is_file()]
    if missing:
        raise ShrinkToolError(f"工具資料夾裡少了 {'、'.join(missing)}，請將錯誤訊息通知開發者。")
    dest = Path(desktop) / DESKTOP_FOLDER
    try:
        dest.mkdir(parents=True, exist_ok=True)
        for n in TOOL_FILES:
            tmp = dest / f".{n}.複製中"
            shutil.copyfile(src / n, tmp)
            tmp.replace(dest / n)
    except OSError as e:
        raise ShrinkToolError(f"沒辦法放到桌面（{e.strerror or e}）。請將錯誤訊息通知開發者。") from None
    return dest


def status(kind: str | None = None, desktop_fn=None) -> dict:
    """`GET /api/shrink-tool`：要不要顯示提醒與按鈕、桌面上有沒有那個檔。不是 WSL 一律不顯示（也不去問 Windows）。"""
    kind = kind or fileio.platform_kind()
    if kind != "wsl":
        return {"系統": kind, "顯示": False}
    out = {"系統": kind, "顯示": True, "提醒": REMINDER, "按鈕": MENU_LABEL, "已放到桌面": False}
    try:
        out["已放到桌面"] = installed((desktop_fn or windows_desktop)())
    except ShrinkToolError:
        pass   # 找不到桌面：當作還沒放，按「放到桌面」時才把原因說出來
    return out


def put_on_desktop(kind: str | None = None, desktop_fn=None, src: Path | None = None) -> dict:
    """`POST /api/shrink-tool/install`：放到桌面。不是 WSL 回 ValueError。"""
    kind = kind or fileio.platform_kind()
    if kind != "wsl":
        raise ValueError("這個功能只在 Windows 上用。")
    try:
        dest = install((desktop_fn or windows_desktop)(), src=src)
    except ShrinkToolError as e:
        msg = str(e)
        tail = "請將錯誤訊息通知開發者。"
        raise ShrinkToolError(msg if msg.endswith(tail) else f"{msg.rstrip('。')}。{tail}") from None
    return {"ok": True, "資料夾": str(dest),
            "說明": f"桌面多了「{DESKTOP_FOLDER}」資料夾，裡面的「縮小虛擬硬碟」要用時按右鍵選「以系統管理員身分執行」。"}
