"""結案（32 號設計 #222）。第 1 段（1015-8）先放：

- `closed(workdir)`：「這支影片已結案」唯一的判斷（看 `<工作區>/結案.json`）。#228 的 `needs.redo_list` 已經問它；
  打包、清理（第 2、3 段）寫這份檔。
- 逐字稿匯出（§6，純函式，先不接畫面）：`transcript_srt`、`transcript_txt`、`transcript_product_srt`（§1a 問題 3：成品時間，
  剪掉的句子不寫；§1b 第 2 點：片頭長度參數）；`export_transcripts` 是 `bookclub close --transcript` 用的。
- `disk_report`：`bookclub disk`（§4.5＋§1b 第 1 點）查佔用空間，**只讀、只印，不刪任何東西**。

逐字稿是原始逐字稿（不套代號、不換名字），含學員本名與分享內容（§6.4）；這支模組不印句子內容，只寫到指定的資料夾。
"""

from __future__ import annotations

import os
import shutil
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from bookclub import workdir as wd

RECORD_FILE = "結案.json"
# 結案.json 的 `狀態`
PACKING, PACKED, CLEANING, CLOSED = "打包中", "已打包", "清理中", "已結案"

SRT_NAME, TXT_NAME, PRODUCT_SRT_NAME = "逐字稿.srt", "逐字稿.txt", "逐字稿_成品時間.srt"
TXT_HEAD = "時間是原始影片的時間，剪過的成品會對不上。"
MIN_DUR_S = 0.5   # end <= start 的句子補成 start + 0.5 秒（§6.2）
NL = "\r\n"       # Windows 的字幕軟體比較不挑（§6.2）


# ---------- 已結案 ----------

def record_path(workdir: str | Path) -> Path:
    return Path(workdir) / RECORD_FILE


def state(workdir: str | Path) -> str | None:
    """`結案.json` 的狀態；沒有這個檔（或讀不懂）＝None。"""
    try:
        data = wd.read_json(record_path(workdir), default=None)
    except (OSError, ValueError):
        return None
    return data.get("狀態") if isinstance(data, dict) else None


def closed(workdir: str | Path) -> bool:
    """這支影片已結案（只看 `結案.json`）。清理開始之後（清理中）也算：聲音檔已經在搬走、不能再重做。"""
    return state(workdir) in (CLEANING, CLOSED)


# ---------- 逐字稿（§6） ----------

def sentences(workdir: str | Path) -> list[dict]:
    """`transcript/merged.json` 的句子（原片秒數）。"""
    data = wd.read_json(wd.merged_transcript_path(Path(workdir)), default=None) or {}
    return list(data.get("sentences") or [])


def _clean(sents: list[dict]) -> list[tuple[float, float, str]]:
    """照 start 排；文字去頭尾空白後是空的略過；end <= start 補成 start + 0.5 秒。"""
    out = []
    for s in sents:
        text = str(s.get("text") or "").strip()
        if not text or s.get("start") is None:
            continue
        a = float(s["start"])
        b = float(s["end"]) if s.get("end") is not None else a
        if b <= a:
            b = a + MIN_DUR_S
        out.append((a, b, text))
    out.sort(key=lambda x: (x[0], x[1]))
    return out


def srt_time(t: float) -> str:
    """秒 → `HH:MM:SS,mmm`（毫秒四捨五入：59.9995 → 00:01:00,000；超過 1 小時照樣往上加）。"""
    ms = int((Decimal(str(max(0.0, float(t)))) * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _srt(rows: list[tuple[float, float, str]]) -> str:
    out = []
    for i, (a, b, text) in enumerate(rows, start=1):
        out += [str(i), f"{srt_time(a)} --> {srt_time(b)}", text, ""]
    return NL.join(out) + (NL if out else "")


def transcript_srt(sents: list[dict]) -> str:
    """（純函式）原片時間的 SRT：一句一條、序號從 1 開始、不切行、`\\r\\n`。"""
    return _srt(_clean(sents))


def transcript_txt(sents: list[dict]) -> str:
    """（純函式）第一行說明、空一行，之後一句一行 `[mm:ss] 文字`（超過 60 分鐘分鐘數照樣往上加：`[97:05]`）、`\\r\\n`。"""
    lines = [TXT_HEAD, ""]
    for a, _b, text in _clean(sents):
        sec = int(a)
        lines.append(f"[{sec // 60:02d}:{sec % 60:02d}] {text}")
    return NL.join(lines) + NL


def product_span(s: float, e: float, plist: list[dict] | None, intro_s: float = 0.0) -> tuple[float, float] | None:
    """（純函式）原片的一段 [s, e] → 成品時間（§1a 問題 3）。plist＝那一版處理紀錄的 `片段`（`render.pieces`：每片
    {src: [起, 訖], freeze: 停格秒}）；沒有片段表（只組聲音軌）＝時間不變。整段落在剪掉的範圍裡＝None（不寫）；
    只剪掉一部分的，取留下來那幾片的頭尾。intro_s（§1b 第 2 點，#146 片頭）：所有成品時間加上片頭長度，預設 0。"""
    if not plist:
        return (s + intro_s, e + intro_s)
    acc, lo, hi = 0.0, None, None
    for p in plist:
        ps, pe = float(p["src"][0]), float(p["src"][1])
        a, b = max(s, ps), min(e, pe)
        if b > a:
            oa, ob = acc + (a - ps), acc + (b - ps)
            lo = oa if lo is None else min(lo, oa)
            hi = ob if hi is None else max(hi, ob)
        acc += (pe - ps) + float(p.get("freeze") or 0.0)
    return None if lo is None else (lo + intro_s, hi + intro_s)


def transcript_product_srt(sents: list[dict], plist: list[dict] | None, intro_s: float = 0.0) -> str:
    """（純函式）成品時間的 SRT（`逐字稿_成品時間.srt`）：用最後輸出那一版的片段表換算，剪掉的句子不寫，序號重排。
    文字仍是原始逐字稿（§6.4：疊到成品上會把已處理掉的名字寫出來）。"""
    rows = []
    for a, b, text in _clean(sents):
        got = product_span(a, b, plist, intro_s)
        if got is not None:
            rows.append((got[0], got[1], text))
    return _srt(rows)


def last_output_log(workdir: str | Path) -> tuple[int | None, dict | None]:
    """最後輸出那一版的處理紀錄（換算成品時間用，只讀）：成品檢查檔的 `輸出成品` 記的版本號 → `修改需求/` 版本表那一版的
    處理紀錄副本；沒升級過的照 `處理紀錄產生時間` 對現在的處理紀錄。還沒輸出過、對不到 → (None, None)。"""
    from bookclub import finalcheck, needs, proclog

    w = Path(workdir)
    rec = (wd.read_json(finalcheck.check_path(w), default=None) or {}).get("輸出成品") or {}
    if not rec:
        return None, None
    n = rec.get("版本號")
    if needs.exists(w):
        st = needs.load(w)
        # 沒記版本號的舊輸出紀錄：照處理紀錄產生時間找那一版（跟 `finalcards._export_info` 一樣）
        v = st.versions.get(n) if isinstance(n, int) else st.version_at(rec.get("處理紀錄產生時間"))
        n = v["版本號"] if v else n
        if v and v.get("處理紀錄"):
            log = wd.read_json(w / v["處理紀錄"], default=None)
            if log:
                return n, log
    log = proclog.load(w)
    if log and rec.get("處理紀錄產生時間") and log.get("產生時間") == rec.get("處理紀錄產生時間"):
        return (n if isinstance(n, int) else None), log
    return None, None


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.寫入中")
    with open(tmp, "w", encoding="utf-8", newline="") as f:   # newline=""：照字串裡的 \r\n 寫，不再轉換
        f.write(text)
    os.replace(tmp, path)


def _inside(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def export_transcripts(workdir: str | Path, folder: str | Path, intro_s: float = 0.0) -> dict:
    """`bookclub close --transcript <工作區> <資料夾>`：把逐字稿寫到資料夾（UTF-8 不加 BOM）。
    同名檔已經在就不蓋掉（加 ` (2)`）。回傳 {檔案: [路徑], 句數, 成品時間: 版本號 或 None, 說明}。"""
    w, out = Path(workdir), Path(folder).expanduser()
    if not wd.merged_transcript_path(w).is_file():
        raise FileNotFoundError(f"這個工作區沒有逐字稿（transcript/merged.json）：{w}")
    if _inside(out, w):
        raise ValueError("不能存在這支影片的工作資料夾裡，請選別的地方。")
    out.mkdir(parents=True, exist_ok=True)
    sents = sentences(w)
    files: dict[str, str] = {SRT_NAME: transcript_srt(sents), TXT_NAME: transcript_txt(sents)}
    n, log = last_output_log(w)
    note = None
    if log is not None:
        files[PRODUCT_SRT_NAME] = transcript_product_srt(sents, log.get("片段"), intro_s)
    else:
        note = "還沒輸出過成品（或對不到輸出的那一版），成品時間的逐字稿沒有產生"
    written = []
    for name, text in files.items():
        dst = out / name
        k = 2
        while dst.exists() or dst.is_symlink():
            dst = out / f"{Path(name).stem} ({k}){Path(name).suffix}"
            k += 1
        _write_text(dst, text)
        written.append(str(dst))
    return {"檔案": written, "句數": len(_clean(sents)), "成品時間": n if log is not None else None, "說明": note}


# ---------- 查佔用空間（§4.5，只讀） ----------

def _file_size(fp: str, seen: set | None) -> int:
    """一個檔的大小；seen 有給就用 (st_dev, st_ino) 去重（硬連結、同一個檔算過了就是 0）。捷徑不算。"""
    try:
        st = os.lstat(fp)
    except OSError:
        return 0
    if not os.path.isfile(fp) or os.path.islink(fp):
        return 0
    if seen is not None:
        key = (st.st_dev, st.st_ino)
        if key in seen:
            return 0
        seen.add(key)
    return st.st_size


def dir_size(p: Path, seen: set | None = None) -> int:
    """資料夾（或檔案）大小，位元組。不跟捷徑；讀不到的檔略過。
    seen（審查必修 4）：整份報告共用一個 {(st_dev, st_ino)}，硬連結、重疊的資料夾只算一次。"""
    try:
        if p.is_symlink():
            return 0
        if p.is_file():
            return _file_size(str(p), seen)
    except OSError:
        return 0
    total = 0
    for root, _dirs, files in os.walk(p, followlinks=False):
        for f in files:
            total += _file_size(os.path.join(root, f), seen)
    return total


def video_copies_size(vids: Path, seen: set | None = None) -> int:
    """（審查必修 4）`~/讀書會剪輯資料/影片/` 最上層的影片檔（WSL 匯入的副本）；不進子資料夾——WSL 上工作區就建在
    `影片/<影片>_剪輯工作區/`，那些已經算在工作區那一行。"""
    from bookclub.fileio import VIDEO_EXTS

    total = 0
    try:
        with os.scandir(vids) as it:
            for e in it:
                if e.name.lower().endswith(VIDEO_EXTS) and e.is_file(follow_symlinks=False):
                    total += _file_size(e.path, seen)
    except OSError:
        pass
    return total


def human(n: int | float) -> str:
    """位元組 → `2.7 GB`／`6 MB`／`12 KB`（1000 進位，跟 Windows 檔案總管差一點但好讀）。"""
    n = float(n)
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.0f} MB"
    if n >= 1e3:
        return f"{n / 1e3:.0f} KB"
    return f"{int(n)} B"


def project_dirs(data: Path) -> list[Path]:
    """專案清單（`專案清單.json`）上的工作區＋舊位置 `工作區/` 底下的資料夾（跟總覽同一份來源，不 import 伺服器）。"""
    reg = wd.read_json(data / "專案清單.json", default=None) or {}
    dirs = [Path(x) for x in reg.get("專案", []) if isinstance(x, str)]
    root = data / "工作區"
    if root.is_dir():
        dirs += sorted(x for x in root.iterdir() if x.is_dir() and not x.name.startswith("."))
    seen, out = set(), []
    for d in dirs:
        try:
            ok = d.is_dir()
            key = str(d.resolve())
        except OSError:
            continue
        if ok and key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _width(text: str) -> int:
    """終端機上的顯示寬度（中文字佔兩格），對齊用。"""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _backups(home: Path, projects: list[Path]) -> list[Path]:
    """更新前備份（§1b 第 1 點、#237）：家目錄底下 `讀書會剪輯資料_更新前備份_*`，加上每個工作區旁邊的 `*_更新前備份_*`。"""
    found = [p for p in home.glob("讀書會剪輯資料_更新前備份_*") if p.is_dir() and not p.is_symlink()]
    for d in projects:
        found += [p for p in d.parent.glob("*_更新前備份_*") if p.is_dir() and not p.is_symlink()]
    seen, out = set(), []
    for p in found:
        k = str(p.resolve())
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def _vhdx(mnt_c: Path) -> list[Path]:
    try:
        return sorted(mnt_c.glob("Users/*/AppData/Local/Packages/*/LocalState/ext4.vhdx"))
    except OSError:
        return []


def disk_report(home: Path | None = None, data: Path | None = None, *, wsl: bool | None = None,
                mnt_c: Path = Path("/mnt/c")) -> list[str]:
    """`bookclub disk` 印的每一行（只讀）。沒有的資料夾不印那一行；不是 WSL 不印虛擬硬碟。"""
    from bookclub.config import data_dir

    home = Path.home() if home is None else Path(home)
    data = data_dir() if data is None else Path(data)
    if wsl is None:
        from bookclub import fileio

        wsl = fileio.platform_kind() == "wsl"
    rows: list[tuple[int, str, str, str]] = []   # (縮排, 名稱, 大小, 說明)
    seen: set = set()   # 整份報告共用：硬連結、重疊的資料夾只算一次，合計＝實際佔用
    grand = 0
    projects = project_dirs(data)
    sizes = [(d, dir_size(d, seen)) for d in projects]
    grand += sum(s for _d, s in sizes)
    if sizes:
        rows.append((1, f"工作區（{len(sizes)} 支）", human(sum(s for _d, s in sizes)), ""))
        for d, s in sorted(sizes, key=lambda x: -x[1]):
            rows.append((2, d.name, human(s), "已結案" if closed(d) else "進行中"))
    vids = data / "影片"
    if vids.is_dir():
        n = video_copies_size(vids, seen)
        grand += n
        rows.append((1, "~/讀書會剪輯資料/影片/", human(n), "匯入的影片副本；已結案的那支可以刪（Windows 那邊有原檔）"))
    for sub, note in ((".cache/bookclub", "AI 聲音模型，別刪（刪了要重新下載）"),
                      (".cache/huggingface", "分析用的模型，別刪"),
                      (".cache/uv", "安裝工具時的下載暫存，可以清：uv cache clean")):
        p = home / sub
        if p.is_dir():
            n = dir_size(p, seen)
            grand += n
            rows.append((1, f"~/{sub}", human(n), note))
    old = sorted(p for p in home.glob("bookclub-edit-舊版*") if p.is_dir() and not p.is_symlink())
    if old:
        n = sum(dir_size(p, seen) for p in old)
        grand += n
        rows.append((1, f"舊版工具資料夾（{len(old)} 份）", human(n), "下次更新會自動刪"))
    backs = _backups(home, projects)
    if backs:
        n = sum(dir_size(p, seen) for p in backs)
        grand += n
        rows.append((1, f"更新前備份（{len(backs)} 份）", human(n), "下次更新會自動刪"))
    width = max([_width(name) + 2 * ind for ind, name, _s, _n in rows] + [20])
    lines = ["讀書會剪輯：佔用空間"]
    for ind, name, size, note in rows:
        pad = "  " * ind
        lines.append(f"{pad}{name}{' ' * (width - _width(name) - 2 * ind)}  {size:>8}" + (f"   {note}" if note else ""))
    if rows:
        name = "合計（同一個檔不重複算）"
        lines.append(f"{name}{' ' * max(0, width - _width(name))}  {human(grand):>8}")
    if wsl:
        disks = _vhdx(mnt_c)
        if disks:
            total = sum(p.stat().st_size for p in disks if p.is_file())
            try:
                free = f"   C 槽還剩 {human(shutil.disk_usage(mnt_c).free)}"
            except OSError:
                free = ""
            lines.append(f"Windows 虛擬硬碟（ext4.vhdx）  {human(total)}{free}")
            lines.append("  刪了檔虛擬硬碟不會自己變小，要縮小看 README 附錄「縮小虛擬硬碟」")
    if len(lines) == 1:
        lines.append("  （沒有找到讀書會剪輯的資料夾）")
    return lines
