"""檔案進出（10-07）：系統內建的選檔視窗、Windows 的檔案複製進 Ubuntu、成品拿出來。

老師 10-06 在 Windows 11＋WSL2 實際用過後的需求：
- 選影片改用作業系統自己的選檔視窗（以前在網頁上一層一層點資料夾，眼花、不直覺）。
  Mac：`osascript` 的 choose file；WSL2：透過 interop 叫 `powershell.exe` 開 OpenFileDialog（要 STA）；
  有桌面的 Linux：zenity／kdialog。叫不起來（interop 關掉、沒有桌面、按取消）就退回網頁上的資料夾瀏覽。
- WSL2 選到 `/mnt/<磁碟>/…`（Windows 的檔案）先複製到 `~/讀書會剪輯資料/影片/`，工作區建在複製過來的影片旁邊
  （工作區寫在 C 槽的速度、防毒、OneDrive 都沒測過）。Mac 選到的檔案照用、不複製。
- 第 5 步：在檔案總管（Finder）打開成品資料夾；WSL2 另外可以把成品複製到 Windows 的「下載」資料夾。

這支模組只管「叫系統視窗、轉路徑、複製檔案」，不管網頁與工作區；子程序一律透過 `run` 參數呼叫，
測試用假的子程序就能在 Mac 上模擬 WSL2（`tests/test_fileio.py`）。

隱私：選到的檔案路徑只回給這台電腦的網頁，log 不印路徑以外的內容；不讀影片內容（複製只是搬位元組）。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path, PurePosixPath

VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi")   # bookclub/server.py 也用這一份
CANCEL_MARK = "__BOOKCLUB_CANCEL__"
PICK_TIMEOUT_S = 15 * 60       # 選檔視窗最久等 15 分鐘（人走開了，伺服器那條執行緒不要一直卡著）
QUICK_TIMEOUT_S = 20           # wslpath、cmd.exe 這類一下就回來的
COPY_CHUNK = 8 * 1024 * 1024
IMPORT_MARGIN_BYTES = 1_000_000_000   # 複製進來之後至少還要留 1 GB（之後抽聲音、組裝都要寫硬碟）
EXPORT_MARGIN_BYTES = 200_000_000

PS_CANDIDATES = ("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",)
CMD_CANDIDATES = ("/mnt/c/Windows/System32/cmd.exe",)
EXPLORER_CANDIDATES = ("/mnt/c/Windows/explorer.exe",)


class PickUnavailable(Exception):
    """叫不起系統的選檔視窗（訊息是給人看的一句白話）。"""


# ---------------------------------------------------------------------------
# 現在是哪一種系統
# ---------------------------------------------------------------------------

def _is_wsl() -> bool:
    from bookclub.system_info import is_wsl, read_system_file

    return is_wsl(read_system_file("/proc/version"))


def platform_kind(system: str | None = None, wsl: bool | None = None) -> str:
    """"mac"／"wsl"／"linux"／"other"。"""
    import platform

    system = system or platform.system()
    if system == "Darwin":
        return "mac"
    if system == "Linux":
        if wsl is None:
            wsl = _is_wsl()
        return "wsl" if wsl else "linux"
    return "other"


def _find_exe(name: str, candidates: tuple[str, ...] = (), which=None) -> str | None:
    """找 Windows 的程式：PATH 上有（WSL 預設把 Windows 的 PATH 接進來）就用；沒有再找固定位置。"""
    which = which or shutil.which
    found = which(name)
    if found:
        return found
    for c in candidates:
        if Path(c).is_file():
            return c
    return None


def pick_method(kind: str | None = None, env: dict | None = None, which=None) -> str | None:
    """用哪一種系統選檔視窗：mac／wsl／zenity／kdialog；沒有可用的回 None（網頁上的資料夾瀏覽）。"""
    which = which or shutil.which
    kind = kind or platform_kind()
    if kind == "mac":
        return "mac" if which("osascript") else None
    if kind == "wsl":
        return "wsl" if _find_exe("powershell.exe", PS_CANDIDATES, which) else None
    if kind == "linux":
        env = os.environ if env is None else env
        if not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
            return None
        if which("zenity"):
            return "zenity"
        if which("kdialog"):
            return "kdialog"
    return None


# ---------------------------------------------------------------------------
# 選檔視窗（指令組法、輸出解讀都是純函式）
# ---------------------------------------------------------------------------

def _applescript_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _ps_str(s: str) -> str:
    """PowerShell 單引號字串：只處理一般的單引號（' → ''）。目前傳進來的都是程式裡寫死的標題與副檔名清單，
    不會有人輸入的文字；PowerShell 也把「‘ ’」這類彎引號當成單引號，之後要放人輸入的文字時要一起處理。"""
    return "'" + s.replace("'", "''") + "'"


def powershell_script(title: str, exts: tuple[str, ...] = VIDEO_EXTS) -> str:
    """WSL2 的選檔視窗（Windows 的 OpenFileDialog）。輸出改成 UTF-8（中文路徑不會變亂碼）；按取消印 CANCEL_MARK。
    用一個看不見、最上層的小視窗當主人，選檔視窗才不會躲在瀏覽器後面。"""
    pats = ";".join(f"*{e}" for e in exts)
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
        "Add-Type -AssemblyName System.Windows.Forms",
        "$owner = New-Object System.Windows.Forms.Form",
        "$owner.TopMost = $true",
        "$owner.ShowInTaskbar = $false",
        "$owner.StartPosition = 'CenterScreen'",
        "$owner.Size = New-Object System.Drawing.Size(1, 1)",
        "$owner.Opacity = 0",
        "$owner.Show()",
        "$owner.Activate()",
        "$d = New-Object System.Windows.Forms.OpenFileDialog",
        f"$d.Title = {_ps_str(title)}",
        f"$d.Filter = {_ps_str(f'影片 ({pats})|{pats}')}",
        "$d.Multiselect = $false",
        "$d.InitialDirectory = [Environment]::GetFolderPath('MyVideos')",
        "$r = $d.ShowDialog($owner)",
        "$owner.Close()",
        f"if ($r -eq [System.Windows.Forms.DialogResult]::OK) {{ Write-Output $d.FileName }} else {{ Write-Output {_ps_str(CANCEL_MARK)} }}",
    ])


def encode_ps(script: str) -> str:
    """`powershell.exe -EncodedCommand` 要的格式（UTF-16LE 再 base64）：中文、引號、空白都不用再跳脫。"""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def pick_command(method: str, title: str, exts: tuple[str, ...] = VIDEO_EXTS, which=None) -> list[str]:
    which = which or shutil.which
    if method == "mac":
        # 10-08：改用 JavaScript 版的 osascript（JXA）。AppleScript 的 `activate` 每次要等約 2.2 秒才回來
        # （開發者的 Mac 實測：osascript -e 'activate' 3 次都是 2.15～2.18 秒；不 activate 0.09 秒、JXA 的 activate 0.2 秒），
        # 宇軒按「選影片」覺得卡的主要原因就是它。JXA 照樣先 activate（讓視窗盡量跳在前面），只多約 0.15 秒。
        types = json.dumps(["public.movie", *(e.lstrip(".") for e in exts)], ensure_ascii=False)
        script = ("var app = Application.currentApplication(); app.includeStandardAdditions = true; app.activate(); "
                  f"String(app.chooseFile({{withPrompt: {json.dumps(title, ensure_ascii=False)}, ofType: {types}}}))")
        return ["osascript", "-l", "JavaScript", "-e", script]
    if method == "wsl":
        ps = _find_exe("powershell.exe", PS_CANDIDATES, which) or "powershell.exe"
        return [ps, "-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy", "Bypass",
                "-EncodedCommand", encode_ps(powershell_script(title, exts))]
    if method == "zenity":
        pats = " ".join(f"*{e}" for e in exts)
        return ["zenity", "--file-selection", f"--title={title}", f"--file-filter=影片 | {pats}"]
    if method == "kdialog":
        pats = " ".join(f"*{e}" for e in exts)
        return ["kdialog", "--title", title, "--getopenfilename", str(Path.home()), f"{pats}|影片"]
    raise ValueError(f"不認得的選檔方式：{method}")


def _decode(b) -> str:
    if isinstance(b, str):
        return b
    try:
        return (b or b"").decode("utf-8")
    except UnicodeDecodeError:
        return (b or b"").decode("utf-8", errors="replace")


def parse_pick(method: str, returncode: int, stdout, stderr) -> str | None:
    """選檔視窗的結果 → 選到的路徑（系統原生的寫法）；按取消回 None；叫不起來丟 PickUnavailable（純函式）。"""
    out = _decode(stdout).replace("\ufeff", "").strip()
    err = _decode(stderr)
    if method == "mac":
        if returncode == 0 and out:
            return out.splitlines()[-1]
        if "-128" in err or "User canceled" in err or "使用者已取消" in err:
            return None
        raise PickUnavailable("Mac 的選檔視窗叫不起來" + (f"（{err.strip()[:120]}）" if err.strip() else ""))
    if method == "wsl":
        lines = [x.strip() for x in out.splitlines() if x.strip()]
        if returncode == 0 and lines:
            return None if lines[-1] == CANCEL_MARK else lines[-1]
        raise PickUnavailable("Windows 的選檔視窗叫不起來（WSL 呼叫 Windows 程式的功能可能被關掉了）"
                              + (f"：{err.strip()[:120]}" if err.strip() else ""))
    if method in ("zenity", "kdialog"):
        if returncode == 0 and out:
            return out.splitlines()[-1]
        if returncode == 1:
            return None
        raise PickUnavailable(f"{method} 選檔視窗叫不起來" + (f"（{err.strip()[:120]}）" if err.strip() else ""))
    raise ValueError(f"不認得的選檔方式：{method}")


def wsl_to_linux(win_path: str, run=None) -> str:
    """`C:\\Users\\老師\\影片\\第一堂.mp4` → `/mnt/c/Users/老師/影片/第一堂.mp4`（`wslpath -u`）。已經是 Linux 路徑就照用。"""
    run = run or subprocess.run
    if win_path.startswith("/"):
        return win_path
    try:
        r = run(["wslpath", "-u", win_path], capture_output=True, timeout=QUICK_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:   # 卡住或叫不起來：當作叫不起選檔視窗，退回網頁瀏覽
        raise PickUnavailable(f"Windows 路徑換不成 Ubuntu 的路徑（wslpath 沒有回應：{e}）") from None
    out = _decode(r.stdout).strip()
    if r.returncode != 0 or not out:
        raise PickUnavailable(f"Windows 路徑換不成 Ubuntu 的路徑：{win_path}")
    return out


def linux_to_windows(path: Path | str, run=None) -> str:
    """Ubuntu 的路徑 → Windows 看得懂的寫法（`wslpath -w`），給檔案總管用。"""
    run = run or subprocess.run
    try:
        r = run(["wslpath", "-w", str(path)], capture_output=True, timeout=QUICK_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"路徑換不成 Windows 的寫法（wslpath 沒有回應：{e}）") from None
    out = _decode(r.stdout).strip()
    if r.returncode != 0 or not out:
        raise RuntimeError(f"路徑換不成 Windows 的寫法：{path}")
    return out


_pick_lock = threading.Lock()


def pick_file(title: str, *, method: str | None = None, run=None, exts: tuple[str, ...] = VIDEO_EXTS,
              which=None) -> Path | None:
    """叫系統的選檔視窗選一支影片，回傳 Ubuntu／Mac 這邊的路徑；按取消回 None；叫不起來丟 PickUnavailable。
    同一時間只開一個視窗（第二個丟 PickUnavailable：說已經開著了）。"""
    which = which or shutil.which
    run = run or subprocess.run
    method = method or pick_method(which=which)
    if not method:
        raise PickUnavailable("這台電腦叫不起系統的選檔視窗（沒有桌面環境）")
    if not _pick_lock.acquire(blocking=False):
        raise PickUnavailable("選檔視窗已經開著了（可能躲在瀏覽器後面），先在那個視窗選好或按取消")
    try:
        if method == "mac" and MAC_PICKER.usable():   # 10-08：常駐小程式（約 0.5 秒）；還沒好就照舊叫 osascript
            t0 = time.time()
            try:
                got, opened = MAC_PICKER.pick(title, exts)
                timing_log(f"常駐小程式：送出到開視窗 {opened:.2f} 秒，到選好／取消共 {time.time() - t0:.1f} 秒")
                return Path(got) if got else None
            except PickUnavailable as e:
                timing_log(f"常駐小程式不能用（{e}），這次改叫 osascript；下次重開小程式")
                MAC_PICKER.start_in_background()
        t0 = time.time()
        cmd = pick_command(method, title, exts, which=which)
        try:
            kw = {"cwd": "/mnt/c"} if method == "wsl" and Path("/mnt/c").is_dir() else {}
            # stdin 接到空的：Windows 的程式不會去讀（或改壞）終端機的輸入狀態
            r = run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=PICK_TIMEOUT_S, **kw)
        except subprocess.TimeoutExpired:
            raise PickUnavailable("選檔視窗開太久沒有選（超過 15 分鐘），已經不等了") from None
        except OSError as e:   # 找不到程式、WSL interop 關掉（Exec format error）
            raise PickUnavailable(f"叫不起系統的選檔視窗（{e.strerror or e}）") from None
        timing_log(f"{'osascript' if method == 'mac' else method}：叫起到選好／取消共 {time.time() - t0:.1f} 秒"
                   "（這一種量不到視窗什麼時候出現）")
        got = parse_pick(method, r.returncode, r.stdout, r.stderr)
        if got is None:
            return None
        if method == "wsl":
            got = wsl_to_linux(got, run=run)
        return Path(got)
    finally:
        _pick_lock.release()


# ---------------------------------------------------------------------------
# Mac：常駐的選檔小程式（10-08，bookclub/filepicker.swift）
# ---------------------------------------------------------------------------
#
# 10-08 宇軒在 8775 實按：第一次 4 秒、之後約 2 秒才跳出視窗。開發者的 Mac 用「看畫面上什麼時候多出一個視窗」量：
# 每次叫 osascript（JXA）第一次 2.15 秒、之後 1.18～1.45 秒；不 activate、改 AppleScript 都差不多（1.2～3.2 秒）；
# 自己寫的選檔小程式每次重開也要 1.0～1.8 秒——時間花在「建選檔視窗」本身。同一支程式第二次以後開只要約 0.55 秒，
# 所以改成伺服器啟動時就在背景開好一支常駐的小程式並預熱，之後每次用它開：量到 0.30～0.73 秒（含第一次）。
# 沒有 swiftc（沒裝 Xcode 命令列工具）、編譯失敗、小程式掛掉時，退回每次叫 osascript（慢約 1 秒，但照樣能用）。

class MacPicker:
    """常駐選檔小程式的管理：編譯、背景啟動預熱、送一次要求、掛掉就重開（下一次）。"""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.ready = False
        self.lock = threading.Lock()
        self.failed: str | None = None

    @staticmethod
    def exe_path() -> Path:
        return Path.home() / ".cache" / "bookclub" / "filepicker"

    @classmethod
    def build(cls, which=None) -> Path | None:
        """編譯到 ~/.cache/bookclub/filepicker（原始碼比較新才重編，跟 render.avconcat_helper 同一種做法）。"""
        import sys

        which = which or shutil.which
        if sys.platform != "darwin" or not which("swiftc"):
            return None
        src = Path(__file__).with_name("filepicker.swift")
        exe = cls.exe_path()
        if not exe.is_file() or exe.stat().st_mtime < src.stat().st_mtime:
            exe.parent.mkdir(parents=True, exist_ok=True)
            tmp = exe.with_name(f".filepicker.{os.getpid()}.編譯中")
            env = dict(os.environ)
            if not env.get("TMPDIR", "").isascii():   # swiftc 遇到含中文的暫存資料夾路徑會當掉（測試時遇過）
                env.pop("TMPDIR", None)
            r = subprocess.run(["swiftc", "-O", "-o", str(tmp), str(src)], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, env=env)
            if r.returncode != 0:
                with _ignore_os():
                    tmp.unlink()
                return None
            os.replace(tmp, exe)
        return exe

    def start(self) -> bool:
        """（背景執行緒呼叫）編譯＋啟動＋等預熱好。成功回 True。"""
        with self.lock:
            if self.proc is not None and self.proc.poll() is None and self.ready:
                return True
            t0 = time.time()
            exe = self.build()
            if exe is None:
                self.failed = "沒有 swiftc 或編譯失敗"
                return False
            try:
                self.proc = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)
                line = self.proc.stdout.readline()
                self.ready = '"ready"' in line
            except OSError as e:
                self.failed, self.ready = str(e), False
                return False
            if self.ready:
                timing_log(f"Mac 選檔小程式預熱好了（{time.time() - t0:.2f} 秒）")
            return self.ready

    def start_in_background(self) -> None:
        threading.Thread(target=self.start, daemon=True).start()

    def usable(self) -> bool:
        return self.ready and self.proc is not None and self.proc.poll() is None and not self.lock.locked()

    def pick(self, title: str, exts: tuple[str, ...], timeout: float = PICK_TIMEOUT_S) -> tuple[str | None, float]:
        """開一次選檔視窗。回傳 (路徑或 None＝取消, 送出要求到視窗要開的秒數)。小程式掛掉丟 PickUnavailable。"""
        import json as _json

        with self.lock:
            p = self.proc
            if p is None or p.poll() is not None or not self.ready:
                raise PickUnavailable("選檔小程式還沒準備好")
            t0 = time.time()
            opened = None
            try:
                p.stdin.write(_json.dumps({"title": title, "exts": [e.lstrip(".") for e in exts]}, ensure_ascii=False) + "\n")
                p.stdin.flush()
                deadline = t0 + timeout
                while True:
                    if time.time() > deadline:
                        self._kill()
                        raise PickUnavailable("選檔視窗開太久沒有選（超過 15 分鐘），已經不等了")
                    line = p.stdout.readline()
                    if not line:
                        self._kill()
                        raise PickUnavailable("Mac 的選檔小程式中途結束了")
                    msg = _json.loads(line)
                    if msg.get("opening"):
                        opened = time.time() - t0
                    elif "path" in msg:
                        return msg["path"], opened or 0.0
                    elif msg.get("cancel"):
                        return None, opened or 0.0
            except (OSError, ValueError) as e:
                self._kill()
                raise PickUnavailable(f"Mac 的選檔小程式出錯（{e}）") from None

    def _kill(self) -> None:
        self.ready = False
        if self.proc is not None:
            with _ignore_os():
                self.proc.kill()
        self.proc = None


MAC_PICKER = MacPicker()


def timing_log(msg: str) -> None:
    """選檔的計時紀錄，寫到伺服器的終端機（跟網頁伺服器其他紀錄同一個地方），不含檔名以外的內容。"""
    import sys

    sys.stderr.write(f"[選檔計時] {msg}\n")
    sys.stderr.flush()


def check_video(p: Path, exts: tuple[str, ...] = VIDEO_EXTS) -> Path:
    p = Path(p)
    if p.suffix.lower() not in exts:
        raise ValueError(f"這不是工具認得的影片檔（{'、'.join(e.lstrip('.') for e in exts)}）：{p.name}")
    if not p.is_file():
        raise FileNotFoundError(f"找不到這個檔案：{p}")
    return p


# ---------------------------------------------------------------------------
# Windows 的檔案（WSL2 的 /mnt/<磁碟>/…）
# ---------------------------------------------------------------------------

def is_windows_mount(p: Path | str) -> bool:
    """`/mnt/c/…`、`/mnt/d/…` 這種 WSL2 掛進來的 Windows 磁碟（純函式）。"""
    parts = PurePosixPath(str(p)).parts
    return len(parts) >= 3 and parts[0] == "/" and parts[1] == "mnt" and len(parts[2]) == 1 and parts[2].isalpha()


def needs_import(p: Path | str, kind: str | None = None) -> bool:
    """這個檔要不要先複製進 Ubuntu：只有 WSL2、而且在 Windows 磁碟上的才要。Mac 一律照用。"""
    return (kind or platform_kind()) == "wsl" and is_windows_mount(p)


def import_dir() -> Path:
    from bookclub.config import data_dir

    return data_dir() / "影片"


def numbered(path: Path, n: int) -> Path:
    """`第一堂.mp4` → `第一堂 (2).mp4`（純函式）。"""
    return path.with_name(f"{path.stem} ({n}){path.suffix}")


def choose_dest(name: str, size: int, dest_dir: Path, reuse_same_size: bool) -> tuple[Path, bool]:
    """要複製到哪裡、要不要沿用已經在的那一份。回傳 (目的地, 沿用)。
    reuse_same_size=True（複製進 Ubuntu）：同名同大小沿用；同名不同大小改名「(2)」「(3)」…（改名的也是同大小就沿用）。
    reuse_same_size=False（成品拿出去）：同名一律加編號，不蓋掉 Windows 那邊的檔。"""
    first = Path(dest_dir) / name
    for n in range(1, 1000):
        cand = first if n == 1 else numbered(first, n)
        if not cand.exists():
            return cand, False
        if reuse_same_size and cand.is_file() and cand.stat().st_size == size:
            return cand, True
    raise RuntimeError(f"{dest_dir} 裡同名的檔太多了（{name}），先整理一下再試")


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB"


def space_problem(size: int, dest_dir: Path, margin: int, where: str = "Ubuntu", usage=None,
                  also: tuple[Path | str, ...] = ()) -> str | None:
    """空間夠不夠：不夠回一句說明（要多少、剩多少），夠回 None。
    `also`：另外也要看的地方，取剩最少的那一個（WSL2 的 Ubuntu 是一個會長大的虛擬硬碟，Ubuntu 裡看到的剩餘空間
    是虛擬硬碟的上限，不是 C 槽實際剩下的；虛擬硬碟預設放在 C 槽，所以也看 /mnt/c）。"""
    usage = usage or shutil.disk_usage
    probes = []
    for d in (dest_dir, *also):
        probe = Path(d)
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        probes.append(probe)
    free = min(usage(str(probe)).free for probe in probes)
    need = size + margin
    if free >= need:
        return None
    return (f"{where}的空間不夠：這支檔案 {_gb(size)}，複製之後還要留 {_gb(margin)} 給之後的工作，"
            f"一共要 {_gb(need)}，目前只剩 {_gb(free)}（還差 {_gb(need - free)}）。清出空間後再選一次")


PARTIAL_SUFFIX = ".複製中"


PARTIAL_STALE_S = 3600   # 暫存檔超過這麼多秒沒改動才算「上次留下的」


def cleanup_partials(folder: Path, *, older_than_s: float = PARTIAL_STALE_S, now: float | None = None,
                     suffix: str = PARTIAL_SUFFIX) -> int:
    """伺服器啟動時清掉上次複製到一半（伺服器被關掉）留下的暫存檔：只清這個工具自己取的名字
    （以點開頭、結尾是「.複製中」），而且超過 1 小時沒改動的（同一個資料夾同時開著另一個伺服器、
    正在複製的不會被誤刪），其他檔不動、不往子資料夾找。回傳清掉幾個。
    `suffix`：10-08 起第 5 步「輸出成品」也用這支清 `輸出/` 裡中途斷掉的 `.最終成品_….mp4.輸出中`。"""
    n = 0
    now = time.time() if now is None else now
    try:
        items = list(Path(folder).glob(f".*{suffix}"))
    except OSError:
        return 0
    for f in items:
        with _ignore_os():
            if f.is_file() and now - f.stat().st_mtime > older_than_s:
                f.unlink()
                n += 1
    return n


class CopyCancelled(Exception):
    pass


class CopyJob:
    """背景複製一個檔（網頁看得到百分比、可以取消）。先寫到同資料夾的暫存檔，複製完才換成正式檔名；
    取消或失敗會把暫存檔刪掉，不會留下半個檔被當成好的。"""

    def __init__(self, src: Path, dest: Path, *, purpose: str, reused: bool = False, on_done=None,
                 chunk: int = COPY_CHUNK):
        self.id = uuid.uuid4().hex[:12]
        self.src, self.dest, self.purpose = Path(src), Path(dest), purpose
        self.total = self.src.stat().st_size
        self.done_bytes = self.total if reused else 0
        self.state = "沿用" if reused else "等待"
        self.error: str | None = None
        self.started = time.time()
        self.finished: float | None = time.time() if reused else None
        self._cancel = threading.Event()
        self._on_done = on_done
        self._chunk = chunk
        self.thread: threading.Thread | None = None
        self.target: str | None = None   # 片頭片尾：給「目前」的專案還是「新影片」（網頁分開顯示用）
        self.project: Path | None = None   # 複製好要寫進哪個專案（片頭片尾；None＝新影片／影片本身，由伺服器管）

    @property
    def tmp(self) -> Path:
        return self.dest.with_name(f".{self.dest.name}.{self.id}{PARTIAL_SUFFIX}")   # 帶工作編號：兩個工作不會互刪

    def start(self) -> "CopyJob":
        if self.state == "沿用":
            self._finish_ok()
            return self
        self.state = "複製中"
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self) -> None:
        try:
            self.dest.parent.mkdir(parents=True, exist_ok=True)
            with open(self.src, "rb") as fi, open(self.tmp, "wb") as fo:
                while True:
                    if self._cancel.is_set():
                        raise CopyCancelled()
                    buf = fi.read(self._chunk)
                    if not buf:
                        break
                    fo.write(buf)
                    self.done_bytes += len(buf)
            if self.tmp.stat().st_size != self.total:
                raise OSError(f"複製出來的大小不對（{self.tmp.stat().st_size} ≠ {self.total}）")
            with _ignore_os():
                shutil.copystat(self.src, self.tmp)
            os.replace(self.tmp, self.dest)
            self._finish_ok()
        except CopyCancelled:
            self._cleanup()
            self.state, self.finished = "取消", time.time()
        except Exception as e:  # noqa: BLE001 — 背景執行緒要把失敗記下來給網頁看
            self._cleanup()
            self.state, self.error, self.finished = "失敗", f"{type(e).__name__}：{e}", time.time()

    def _cleanup(self) -> None:
        with _ignore_os():
            self.tmp.unlink()

    def _finish_ok(self) -> None:
        if self.state != "沿用":
            self.state = "完成"
        self.finished = time.time()
        if self._on_done:
            try:
                self._on_done(self)
            except Exception as e:  # noqa: BLE001
                self.error = f"複製好了，但記下來的時候出錯：{e}"

    @property
    def running(self) -> bool:
        return self.state in ("等待", "複製中")

    def to_dict(self) -> dict:
        pct = 100.0 if self.total == 0 else round(self.done_bytes * 100.0 / self.total, 1)
        return {"id": self.id, "用途": self.purpose, "狀態": self.state, "百分比": pct,
                "已複製MB": round(self.done_bytes / 1e6, 1), "大小MB": round(self.total / 1e6, 1),
                "檔名": self.dest.name, "來源": str(self.src), "目的地": str(self.dest), "錯誤": self.error,
                "給": self.target}


class _ignore_os:
    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        return et is not None and issubclass(et, OSError)


def wsl_host_disks(kind: str | None = None) -> tuple[str, ...]:
    """WSL2 的虛擬硬碟所在的 Windows 磁碟（預設在 C 槽，`%LOCALAPPDATA%\Packages\…\ext4.vhdx`）。
    使用者把 Ubuntu 搬到別的磁碟（`wsl --export／--import`、`wsl --manage --move`）的話這裡會看錯，實機要確認。"""
    if (kind or platform_kind()) == "wsl" and Path("/mnt/c").is_dir():
        return ("/mnt/c",)
    return ()


def plan_import(src: Path, dest_dir: Path | None = None, usage=None, kind: str | None = None) -> tuple[Path, bool]:
    """複製進 Ubuntu 前：決定目的地（同名同大小沿用、不同大小改名），空間不夠丟 ValueError（訊息說要多少）。
    WSL2 同時看 Ubuntu 這邊與 C 槽的剩餘空間，取小的。"""
    src = Path(src)
    dest_dir = Path(dest_dir) if dest_dir else import_dir()
    size = src.stat().st_size
    dest, reused = choose_dest(src.name, size, dest_dir, reuse_same_size=True)
    if not reused:
        also = wsl_host_disks(kind)
        why = space_problem(size, dest_dir, IMPORT_MARGIN_BYTES, "Ubuntu（WSL2）與 C 槽" if also else "這台電腦",
                            usage=usage, also=also)
        if why:
            raise ValueError(why)
    return dest, reused


def plan_export(src: Path, dest_dir: Path, usage=None) -> Path:
    src = Path(src)
    dest, _ = choose_dest(src.name, src.stat().st_size, dest_dir, reuse_same_size=False)
    why = space_problem(src.stat().st_size, dest_dir, EXPORT_MARGIN_BYTES, "Windows 的下載資料夾所在的磁碟", usage=usage)
    if why:
        raise ValueError(why)
    return dest


# ---------------------------------------------------------------------------
# 成品拿出來：Windows 的下載資料夾、在檔案總管（Finder）打開
# ---------------------------------------------------------------------------

DOWNLOADS_PS = "\n".join([
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
    "try { $p = (New-Object -ComObject Shell.Application).NameSpace('shell:Downloads').Self.Path } catch { $p = '' }",
    "if (-not $p) { $p = Join-Path $env:USERPROFILE 'Downloads' }",
    "Write-Output $p",
])


def windows_downloads(run=None, which=None) -> Path:
    """Windows 的「下載」資料夾在 Ubuntu 這邊的路徑。
    先問 PowerShell（輸出 UTF-8，使用者名稱是中文也不會亂碼；下載資料夾搬過位置也找得到）；
    不行再用 `cmd.exe /c echo %USERPROFILE%`＋`\\Downloads`（cmd 的輸出是 Windows 的舊編碼，中文名稱可能讀錯）。"""
    which = which or shutil.which
    run = run or subprocess.run
    kw = {"cwd": "/mnt/c"} if Path("/mnt/c").is_dir() else {}
    win = None
    ps = _find_exe("powershell.exe", PS_CANDIDATES, which)
    if ps:
        try:
            r = run([ps, "-NoProfile", "-NonInteractive", "-EncodedCommand", encode_ps(DOWNLOADS_PS)],
                    capture_output=True, stdin=subprocess.DEVNULL, timeout=QUICK_TIMEOUT_S, **kw)
            out = _decode(r.stdout).replace("\ufeff", "").strip().splitlines()
            if r.returncode == 0 and out and ":" in out[-1]:
                win = out[-1].strip()
        except (OSError, subprocess.TimeoutExpired):
            win = None
    if not win:
        cmd = _find_exe("cmd.exe", CMD_CANDIDATES, which)
        if not cmd:
            raise RuntimeError("找不到 Windows 的 cmd.exe（WSL 呼叫 Windows 程式的功能可能被關掉了）")
        try:
            r = run([cmd, "/c", "echo %USERPROFILE%"], capture_output=True, stdin=subprocess.DEVNULL,
                    timeout=QUICK_TIMEOUT_S, **kw)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f"問不到 Windows 的使用者資料夾：{e}") from None
        prof = _decode(r.stdout).strip().splitlines()
        if r.returncode != 0 or not prof or "%USERPROFILE%" in prof[-1]:
            raise RuntimeError("問不到 Windows 的使用者資料夾")
        win = prof[-1].strip().rstrip("\\") + "\\Downloads"
    try:
        p = Path(wsl_to_linux(win, run=run))
    except PickUnavailable as e:
        raise RuntimeError(str(e)) from None
    if not p.is_dir():
        raise RuntimeError(f"找不到 Windows 的下載資料夾：{win}")
    return p


def reveal(path: Path, kind: str | None = None, run=None, popen=None, which=None) -> str:
    """在檔案總管（Finder）打開。`path` 是檔案就選起來（Mac）／打開它所在的資料夾；是資料夾就打開它。
    回傳一句說明。WSL2 的 explorer.exe 成功也回結束碼 1，不拿結束碼判斷，只看叫不叫得起來。"""
    which = which or shutil.which
    run = run or subprocess.run
    popen = popen or subprocess.Popen
    kind = kind or platform_kind()
    path = Path(path)
    if kind == "mac":
        cmd = ["open", "-R", str(path)] if path.is_file() else ["open", str(path)]
        try:
            r = run(cmd, capture_output=True, timeout=QUICK_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f"叫不起 Finder：{e}") from None
        if r.returncode != 0:
            raise RuntimeError(f"Finder 打不開：{_decode(r.stderr).strip()[:120]}")
        return "已經在 Finder 打開" + ("，成品檔已經選起來" if path.is_file() else "")
    folder = path.parent if path.is_file() else path
    if kind == "wsl":
        exe = _find_exe("explorer.exe", EXPLORER_CANDIDATES, which)
        if not exe:
            raise RuntimeError("找不到 Windows 的檔案總管（WSL 呼叫 Windows 程式的功能可能被關掉了）")
        win = linux_to_windows(folder, run=run)
        kw = {"cwd": "/mnt/c"} if Path("/mnt/c").is_dir() else {}
        try:
            popen([exe, win], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
        except OSError as e:
            raise RuntimeError(f"叫不起 Windows 的檔案總管：{e.strerror or e}") from None
        return "已經叫 Windows 的檔案總管打開（沒看到的話，看一下工作列）"
    opener = which("xdg-open")
    if not opener:
        raise RuntimeError(f"這台電腦沒有可以打開資料夾的程式，請自己到這個資料夾：{folder}")
    try:
        popen([opener, str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        raise RuntimeError(f"叫不起檔案管理員：{e.strerror or e}") from None
    return "已經叫檔案管理員打開"
