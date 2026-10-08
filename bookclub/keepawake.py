"""第 1 步分析、第 4 步執行掛著跑的時候，不讓電腦睡著（09-30 Mac；10-07 #172 加 Windows／WSL2、第 1 步）。

- macOS：系統內建的 `caffeinate -i -m -s -w <自己的 pid>`（螢幕可以關，電腦不睡；伺服器死掉 caffeinate 也停）。
- Windows（WSL2）：從 WSL2 叫起 Windows 端的 `powershell.exe`，在裡面呼叫
  `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` 後一直等；跑完（關掉它的 stdin）就結束，
  效果跟著消失，不改使用者的電源設定。WSL 這邊每 60 秒送一行心跳，5 分鐘沒收到心跳、或超過最長時限，
  PowerShell 自己退出（萬一 WSL 這邊被砍掉、EOF 沒傳過去，也不會讓電腦一直醒著）。
  研究與實機驗證清單：reference/…/28-研究-1007-Windows自動防睡眠.md、docs/之後要做.md「防睡眠」。
- 其他狀況（不是 Mac 也不是 WSL2、interop 關掉、找不到 powershell.exe、PowerShell 回報失敗或沒回應）：
  安靜退回，不中斷工作，只印一行提醒、回報「沒開成」的原因。

**Windows 的部分還沒在實機上驗過**（只在 Mac 上用假的 powershell 測過）。
"""

from __future__ import annotations

import base64
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

INTEROP_FILES = ("/proc/sys/fs/binfmt_misc/WSLInterop", "/proc/sys/fs/binfmt_misc/WSLInterop-late")
POWERSHELL_FIXED = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
HEARTBEAT_S = 60            # WSL 這邊多久送一行心跳
NO_HEARTBEAT_EXIT_S = 300   # PowerShell 多久沒收到心跳就自己退出
MAX_HOURS = 24              # 最長時限（保險；第 4 步整晚跑也在這之內）
OK_TIMEOUT_S = 20.0         # 等 PowerShell 回 OK 最多幾秒（啟動約 1–3 秒）
STOP_WAIT_S = 5.0           # 結束時等 PowerShell 自己退出幾秒，還在就砍掉

README_HINT = "請照 README 第 3 節「Windows：跑之前先把電腦設成不睡眠」手動設定"
ON_WINDOWS = "已請電腦不要睡；仍請接電源、不要闔上螢幕、暫停 Windows 更新"
ON_MAC = "已請電腦不要睡（caffeinate）；仍請接電源、不要闔上螢幕"
BATTERY_NOTE = "偵測到現在用電池：用電池時最多撐約 5 分鐘就會睡，請接上電源"


def powershell_script(heartbeat_exit_s: int = NO_HEARTBEAT_EXIT_S, max_hours: int = MAX_HOURS) -> str:
    """Windows 端要跑的 PowerShell（Windows PowerShell 5.1 也能跑）。

    輸出設成 UTF-8：FAIL 的原因（例外訊息）可能有中文，WSL 這邊用 UTF-8 解。
    旗標用十進位：5.1 會把 0x80000001 讀成負的 Int32，轉 uint32 會出錯。
    成功先印一行 `OK <插電狀態>`（Online／Offline／Unknown）；失敗印 `FAIL <原因>` 並以非 0 結束。
    讀 stdin 用自己開的 StreamReader：.NET Framework 的 [Console]::In.ReadLineAsync 其實是同步的，
    會卡住等下一行，「5 分鐘沒心跳就退出」就不會生效。"""
    return f"""$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
function Say($s) {{ [Console]::Out.WriteLine($s); [Console]::Out.Flush() }}
try {{
  $k = Add-Type -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);' -Name K -Namespace BookclubAwake -PassThru
}} catch {{ Say ('FAIL Add-Type: ' + $_.Exception.Message); exit 3 }}
if ($k::SetThreadExecutionState([uint32]2147483649) -eq 0) {{ Say 'FAIL SetThreadExecutionState'; exit 2 }}
$power = 'Unknown'
try {{ Add-Type -AssemblyName System.Windows.Forms; $power = [string][System.Windows.Forms.SystemInformation]::PowerStatus.PowerLineStatus }} catch {{ }}
Say ('OK ' + $power)
$deadline = (Get-Date).AddHours({max_hours})
$last = Get-Date
$in = New-Object System.IO.StreamReader([Console]::OpenStandardInput())
$t = $in.ReadLineAsync()
while ($true) {{
  if ($t.Wait(5000)) {{
    if ($t.Result -eq $null) {{ break }}
    $last = Get-Date
    $t = $in.ReadLineAsync()
  }}
  $now = Get-Date
  if ((($now - $last).TotalSeconds -gt {heartbeat_exit_s}) -or ($now -gt $deadline)) {{ break }}
}}
$k::SetThreadExecutionState([uint32]2147483648) | Out-Null
exit 0
"""


def encode_command(script: str) -> str:
    """`powershell.exe -EncodedCommand` 吃的格式：UTF-16LE 再 base64（避開 interop 傳參數的引號與中文問題）。"""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def decode_command(encoded: str) -> str:
    return base64.b64decode(encoded).decode("utf-16-le")


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def interop_enabled(read: Callable[[str], str] = _read) -> bool:
    """WSL2 能不能叫 Windows 的程式：binfmt_misc 的 WSLInterop（新版可能叫 WSLInterop-late）第一行是 enabled。"""
    for f in INTEROP_FILES:
        text = read(f)
        if text and text.splitlines()[0].strip() == "enabled":
            return True
    return False


def find_powershell(which: Callable[[str], str | None] = shutil.which,
                    exists: Callable[[str], bool] = os.path.exists) -> str | None:
    """先找 PATH（appendWindowsPath=false 時 PATH 沒有 Windows 路徑），再試固定路徑。"""
    return which("powershell.exe") or (POWERSHELL_FIXED if exists(POWERSHELL_FIXED) else None)


def _noop() -> None:
    return None


def keep_awake(log: Callable[[str], None] = print, *, prefix: str = "[AI 執行]",
               on_status: Callable[[str], None] | None = None,
               platform: str | None = None, read: Callable[[str], str] = _read,
               which: Callable[[str], str | None] = shutil.which, exists: Callable[[str], bool] = os.path.exists,
               popen=subprocess.Popen, ok_timeout_s: float = OK_TIMEOUT_S,
               heartbeat_s: float = HEARTBEAT_S) -> Callable[[], None]:
    """開始防睡眠，回傳要在結束時呼叫的函式（一定回傳函式、不拋例外）。

    on_status：給網頁「防睡眠」欄位用，收到一句話（「已請電腦不要睡…」或「沒開成：原因…」）。
    platform／read／which／exists／popen／ok_timeout_s／heartbeat_s 給測試換掉。"""
    status = on_status or (lambda s: None)
    platform = sys.platform if platform is None else platform
    try:
        if platform == "darwin":
            return _mac(log, prefix, status, which, popen)
        from bookclub.system_info import is_wsl

        if platform.startswith("linux") and is_wsl(read("/proc/version")):
            return _wsl(log, prefix, status, read, which, exists, popen, ok_timeout_s, heartbeat_s)
        status(f"沒開成：這個系統（{platform}）工具不會自動防睡眠。{README_HINT}")
    except Exception as e:  # noqa: BLE001 — 防睡眠只是補一層，出什麼錯都不能中斷工作
        _fail(log, prefix, status, f"{type(e).__name__}：{e}")
    return _noop


def _fail(log, prefix: str, status, why: str) -> None:
    log(f"{prefix} ⚠️ 沒辦法自動防止電腦睡眠（{why}）。{README_HINT}")
    status(f"沒開成：{why}。{README_HINT}")


def _mac(log, prefix, status, which, popen) -> Callable[[], None]:
    """跟 09-30 的做法一樣（只是搬過來）。"""
    if not which("caffeinate"):
        status("沒開成：找不到 caffeinate。請在「系統設定」把閒置後進入睡眠的時間拉長")
        return _noop
    try:
        p = popen(["caffeinate", "-i", "-m", "-s", "-w", str(os.getpid())])
    except OSError:
        status("沒開成：caffeinate 叫不起來。請在「系統設定」把閒置後進入睡眠的時間拉長")
        return _noop
    log(f"{prefix} 執行期間不讓電腦睡著（caffeinate），做完自動恢復")
    status(ON_MAC)

    def stop() -> None:
        try:
            p.terminate()
        except OSError:
            pass
    return stop


def _wsl(log, prefix, status, read, which, exists, popen, ok_timeout_s, heartbeat_s) -> Callable[[], None]:
    if not interop_enabled(read):
        _fail(log, prefix, status, "WSL2 叫不了 Windows 的程式（interop 關著）")
        return _noop
    ps = find_powershell(which, exists)
    if not ps:
        _fail(log, prefix, status, "找不到 Windows 的 powershell.exe")
        return _noop
    args = [ps, "-NoProfile", "-NonInteractive", "-EncodedCommand", encode_command(powershell_script())]
    try:
        p = popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as e:
        _fail(log, prefix, status, f"powershell.exe 叫不起來：{e}")
        return _noop

    lines: queue.Queue = queue.Queue()

    def reader() -> None:   # 一直讀到結束，PowerShell 那邊的輸出不會塞住
        try:
            for raw in p.stdout:
                lines.put(raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else str(raw).strip())
        except (OSError, ValueError):
            pass
        lines.put(None)

    threading.Thread(target=reader, daemon=True).start()
    first = _wait_ok(lines, ok_timeout_s)
    if first is None or not first.startswith("OK"):
        why = ("PowerShell 沒有回應" if first is None else
               "PowerShell 結束了、沒回報" if first == "" else f"PowerShell 回報：{first.removeprefix('FAIL').strip()}")
        _kill(p)
        _fail(log, prefix, status, why)
        return _noop

    on_battery = first.split()[1:2] == ["Offline"]
    msg = ON_WINDOWS + (f"。⚠️ {BATTERY_NOTE}" if on_battery else "")
    log(f"{prefix} 執行期間請 Windows 不要睡（做完自動恢復）；仍請接電源、不要闔上螢幕、暫停 Windows 更新")
    if on_battery:
        log(f"{prefix} ⚠️ {BATTERY_NOTE}")
    status(msg)

    stopped = threading.Event()

    def heartbeat() -> None:
        while not stopped.wait(heartbeat_s):
            try:
                p.stdin.write(b"\n")
                p.stdin.flush()
            except (OSError, ValueError):
                return

    threading.Thread(target=heartbeat, daemon=True).start()

    def stop() -> None:
        if stopped.is_set():
            return
        stopped.set()
        try:
            p.stdin.close()   # PowerShell 讀到 EOF 就清掉請求、結束
        except (OSError, ValueError):
            pass
        try:
            p.wait(timeout=STOP_WAIT_S)
        except subprocess.TimeoutExpired:
            _kill(p)
        except OSError:
            pass
    return stop


def _wait_ok(lines: queue.Queue, timeout_s: float) -> str | None:
    """等第一行 OK／FAIL。回傳那一行；PowerShell 先結束回傳 ""；逾時回傳 None。其他輸出略過。"""
    import time

    end = time.monotonic() + timeout_s
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return None
        try:
            line = lines.get(timeout=left)
        except queue.Empty:
            return None
        if line is None:
            return ""
        if line.startswith(("OK", "FAIL")):
            return line


def _kill(p) -> None:
    try:
        p.terminate()
        p.wait(timeout=STOP_WAIT_S)
    except Exception:  # noqa: BLE001
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass
