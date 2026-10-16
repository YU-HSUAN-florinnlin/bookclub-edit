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


# ===========================================================================
# 結案打包（32 號設計 §3，以 §1a 為準；1015-8 第 2 段）
# ===========================================================================
#
# 存到哪：老師在系統的選資料夾視窗選（`fileio.pick_folder`）、或選「下載」「D 槽」——都在伺服器那邊換成一個資料夾路徑，
# 這裡只收路徑。位置下面固定建 `讀書會剪輯結案/<影片>_<YYYYMMDD>/`，做的時候叫 `…_打包中`，全部核對過才改名成正式名稱
# （同名已經在加 ` (2)`，不蓋掉）。每個檔：先寫 `.<檔名>.複製中`（一邊讀來源一邊算 SHA-256，寫完 fsync）→ 換正式檔名 →
# **從目的地重讀一遍**算 SHA-256、比大小；不一樣刪掉重存一次，第二次還不一樣就停。來源是硬連結也一樣真的複製（只讀來源）。
# 這一段不清任何東西（清空間是第 3 段）；不碰工作區裡的檔，只寫 `結案.json`。

import hashlib
import threading
import time
from datetime import datetime

PACK_ROOT = "讀書會剪輯結案"
PACKING_SUFFIX = "_打包中"
PACK_MARGIN_BYTES = 200_000_000          # §3.6：存完還要留 200 MB
PACK_LIST_NAME = "打包清單.txt"
PACK_TMP_SUFFIX = ".複製中"
HASH_CHUNK = 8 * 1024 * 1024
PRIVACY_LINES = ("逐字稿和紀錄裡有學員的本名和分享內容，不要直接公開或傳給別人。",
                 "拿去做行銷前，先挑段落、改掉名字。")
# 要打包的紀錄（§3.3）：工作區裡的相對路徑 → 打包裡的相對路徑
RECORD_FILES = (("生成/剪輯決策.json", "紀錄/剪輯決策.json"), ("生成/處理紀錄.json", "紀錄/處理紀錄.json"),
                ("覆核/成品檢查.json", "紀錄/成品檢查.json"), ("分析結果.json", "紀錄/分析結果.json"),
                ("報告.md", "紀錄/報告.md"))
OUTPUT_RECORD_GLOBS = ("處理標記_*.md", "處理標記_*.html", "剪輯決策_*.json", "輸出摘要_*.json")


class PackError(Exception):
    """打包不能開始、或做到一半停下來（訊息是給老師看的一句）。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_record(workdir: str | Path) -> dict:
    try:
        data = wd.read_json(record_path(workdir), default=None)
    except (OSError, ValueError):
        data = None
    return data if isinstance(data, dict) else {}


def _write_record(workdir: Path, data: dict) -> None:
    from bookclub import __version__

    data = {**data, "程式版本": __version__}
    wd.write_json(record_path(workdir), data)


def last_export(workdir: str | Path) -> dict | None:
    """最後一次輸出的最終成品（成品檢查檔的 `輸出成品`）；沒輸出過＝None。"""
    from bookclub import finalcheck

    rec = (wd.read_json(finalcheck.check_path(Path(workdir)), default=None) or {}).get("輸出成品") or {}
    return rec if rec.get("檔案") else None


def export_version_info(workdir: str | Path) -> dict:
    """{最後輸出版本, 最新版本}（§1a：最後輸出的不是最新版時框裡多一行紅字，不擋）。讀不到的給 None。"""
    from bookclub import needs

    n, _log = last_output_log(workdir)
    latest = None
    try:
        if needs.exists(Path(workdir)):
            v = needs.load(Path(workdir)).latest_version()
            latest = v["版本號"] if v else None
    except Exception:  # noqa: BLE001 — 讀不到就不提
        latest = None
    return {"最後輸出版本": n, "最新版本": latest}


def video_stem(workdir: Path) -> str:
    """打包資料夾名稱用的影片檔名（不含副檔名）：分析結果記的影片；沒有就用工作區名稱去掉「_剪輯工作區」。"""
    v = (wd.read_json(wd.analysis_result_path(workdir), default=None) or {}).get("video")
    if v:
        return Path(str(v)).stem
    return workdir.name.removesuffix("_剪輯工作區")


def pack_items(workdir: str | Path, *, records_only: bool = False) -> list[dict]:
    """要打包的檔（§3.3）：[{相對路徑（打包裡）, 來源（工作區裡的檔）或 None（現場產生的）, 產生（逐字稿／清單的種類）}]。
    records_only：已結案後再存一份（影片已經清掉了）只存逐字稿、打包清單、紀錄。"""
    w = Path(workdir)
    out: list[dict] = []
    if not records_only:
        rec = last_export(w)
        if rec:
            src = w / rec["檔案"]
            if src.is_file():
                out.append({"相對路徑": src.name, "來源": src})
            prod = Path(str(rec.get("來源") or ""))
            if prod.name:
                label = w / prod.parent / f"{prod.stem.rsplit('_', 1)[0]}_標字版{prod.suffix}"
                if label.is_file():
                    out.append({"相對路徑": label.name, "來源": label})
    if wd.merged_transcript_path(w).is_file():
        out.append({"相對路徑": SRT_NAME, "來源": None, "產生": "srt"})
        out.append({"相對路徑": TXT_NAME, "來源": None, "產生": "txt"})
        if last_output_log(w)[1] is not None:
            out.append({"相對路徑": PRODUCT_SRT_NAME, "來源": None, "產生": "product"})
    needs_dir = w / "修改需求"
    if needs_dir.is_dir():
        for root, _dirs, files in os.walk(needs_dir, followlinks=False):
            for f in sorted(files):
                p = Path(root) / f
                if f == ".寫入鎖" or p.is_symlink() or not p.is_file():
                    continue
                out.append({"相對路徑": str(Path("紀錄") / p.relative_to(w)), "來源": p})
    outdir = w / "輸出"
    if outdir.is_dir():
        for pat in OUTPUT_RECORD_GLOBS:
            for p in sorted(outdir.glob(pat)):
                if p.is_file() and not p.is_symlink():
                    out.append({"相對路徑": str(Path("紀錄") / p.name), "來源": p})
    for rel, dst in RECORD_FILES:
        p = w / rel
        if p.is_file() and not p.is_symlink():
            out.append({"相對路徑": dst, "來源": p})
    return out


def pack_size(items: list[dict]) -> int:
    return sum(it["來源"].stat().st_size for it in items if it.get("來源") is not None and it["來源"].is_file())


def clean_estimate(workdir: str | Path) -> int:
    """（第 2 段的佔位，第 3 段換成 `clean_plan` 的合計）框裡那一句「共 N GB」：工作區裡大的暫存檔大概多大——
    `輸出/` 的影片與聲音、`生成/` 的聲音、`web快取/`、`audio*.flac`、`名字候選/`、`重疊/`、`參考音/` 的聲音。只讀。"""
    w = Path(workdir)
    total = 0
    for sub, exts in (("輸出", (".mp4", ".mov", ".wav")), ("生成", (".wav",)), ("web快取", None), ("名字候選", None),
                      ("重疊", None), ("參考音", (".wav",)), ("chunks", None)):
        d = w / sub
        if not d.is_dir():
            continue
        for root, _dirs, files in os.walk(d, followlinks=False):
            for f in files:
                if exts is None or f.lower().endswith(exts):
                    total += _file_size(os.path.join(root, f), None)
    for p in w.glob("audio*.flac"):
        total += _file_size(str(p), None)
    return total


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(HASH_CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def check_location(workdir: str | Path, location: str | Path, need: int, usage=None) -> Path:
    """打包位置能不能用（§3.4、§3.6）：在不在、是不是資料夾、不在工作區裡（也不是工作區的上層——打包資料夾會建在
    工作區裡面）、空間夠不夠（要 need＋200 MB）。能用回 resolve 後的路徑，不能用丟 PackError（給老師看的一句）。"""
    from bookclub import fileio

    w = Path(workdir).resolve()
    loc = Path(location).expanduser()
    if not loc.is_dir():
        raise PackError(f"找不到這個資料夾：{location}（D 槽沒插？），換個位置再試。")
    loc = loc.resolve()
    if _inside(loc, w) or _inside(w, loc / PACK_ROOT):
        raise PackError("不能存在這支影片的工作資料夾裡，請選別的地方。")
    why = fileio.space_problem(need, loc, PACK_MARGIN_BYTES, "存的地方", usage=usage)
    if why:
        raise PackError(f"空間不夠：要 {need / 1e9:.1f} GB，"
                        f"只剩 {(usage or shutil.disk_usage)(str(loc)).free / 1e9:.1f} GB。換個位置或清出空間再試。")
    return loc


def _numbered_dir(path: Path) -> Path:
    if not path.exists() and not path.is_symlink():
        return path
    for n in range(2, 1000):
        cand = path.with_name(f"{path.name} ({n})")
        if not cand.exists() and not cand.is_symlink():
            return cand
    raise PackError(f"{path.parent} 裡同名的資料夾太多了，先整理一下再試。")


def _gen_text(workdir: Path, kind: str) -> bytes:
    sents = sentences(workdir)
    if kind == "srt":
        text = transcript_srt(sents)
    elif kind == "txt":
        text = transcript_txt(sents)
    else:
        _n, log = last_output_log(workdir)
        text = transcript_product_srt(sents, (log or {}).get("片段"))
    return text.encode("utf-8")


def pack_list_text(files: list[dict]) -> str:
    """`打包清單.txt`（§3.3、§6.4）：最上面兩句說明，之後每個檔的大小與 SHA-256。`\\r\\n`（Windows 記事本好讀）。"""
    lines = [*PRIVACY_LINES, "", f"共 {len(files)} 個檔（不含這份清單）：", ""]
    for f in files:
        lines.append(f"{f['相對路徑']}\t{f['大小']} 位元組\tSHA-256 {f['sha256']}")
    return NL.join(lines) + NL


class PackJob:
    """背景打包一次（網頁看得到第幾個檔、可以停）。只寫目的地與 `結案.json`。"""

    def __init__(self, workdir: Path, location: Path, *, display: str | None = None, resume: bool = False,
                 records_only: bool = False, fail_hook=None):
        self.workdir, self.location = Path(workdir), Path(location)
        self.display = display or str(location)
        self.resume, self.records_only = resume, records_only
        self.state = "等待"        # 等待／存檔中／核對中／已存好／停止／失敗
        self.error: str | None = None
        self.index, self.total = 0, 0
        self.current: str | None = None
        self.folder: Path | None = None
        self._cancel = threading.Event()
        self._fail_hook = fail_hook   # 測試用：fail_hook(相對路徑, 第幾次) → 回 True 就把目的地那個檔改壞一個位元組
        self.thread: threading.Thread | None = None
        self.purpose = "結案打包"

    @property
    def running(self) -> bool:
        return self.state in ("等待", "存檔中", "核對中")

    def cancel(self) -> None:
        self._cancel.set()

    def start(self) -> "PackJob":
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        return self

    def to_dict(self) -> dict:
        return {"用途": self.purpose, "狀態": self.state, "第幾個": self.index, "共幾個": self.total,
                "檔名": self.current, "錯誤": self.error, "位置顯示": self.display,
                "資料夾": windows_display(self.folder) if self.folder else None}

    # --- 主流程 ---
    def run(self) -> None:
        try:
            self._run()
        except PackError as e:
            self.state, self.error = ("停止" if self._cancel.is_set() else "失敗"), str(e)
        except OSError as e:
            import errno

            if e.errno == errno.ENOSPC:
                msg = "存的地方滿了，清出空間或換個位置再按「繼續」。"
            else:
                msg = "存的地方讀不到了，是不是拔掉了？插回去再按「繼續」。"
            self.state, self.error = "失敗", f"{msg}（{type(e).__name__}：{e.strerror or e}）"
        except Exception as e:  # noqa: BLE001 — 背景執行緒要把失敗記下來給網頁看
            self.state, self.error = "失敗", f"{type(e).__name__}：{e}"

    def _entry(self, rec: dict) -> dict | None:
        packs = rec.get("打包") or []
        return packs[-1] if packs else None

    def _run(self) -> None:
        w = self.workdir
        rec = read_record(w)
        items = pack_items(w, records_only=self.records_only)
        self.total = len(items) + 1   # ＋打包清單
        last = self._entry(rec)
        folder = None
        if self.resume and last and last.get("核對") != "通過" and last.get("資料夾"):
            p = Path(last["資料夾"])
            if p.is_dir() and p.name.endswith(PACKING_SUFFIX):
                folder = p
        if self.resume and folder is None:
            raise PackError("找不到上次存到一半的資料夾（換了位置、或已經移走了），請重新按「結案打包…」選位置。")
        if folder is None:
            day = datetime.now().strftime("%Y%m%d")
            base = self.location / PACK_ROOT
            base.mkdir(parents=True, exist_ok=True)
            folder = _numbered_dir(base / f"{video_stem(w)}_{day}{PACKING_SUFFIX}")
            folder.mkdir(parents=True)
            entry = {"時間": _now(), "位置": str(self.location), "位置顯示": self.display,
                     "版本號": export_version_info(w)["最後輸出版本"], "資料夾": str(folder), "檔案": [],
                     "核對": "還沒", "只有紀錄": self.records_only}
            rec.setdefault("打包", []).append(entry)
        else:
            entry = last
        self.folder = folder
        if rec.get("狀態") not in (CLEANING, CLOSED):
            rec["狀態"] = PACKING
        _write_record(w, rec)
        self.state = "存檔中"
        done: list[dict] = []
        for i, it in enumerate(items, start=1):
            if self._cancel.is_set():
                raise PackError("停下來了：存好的留著，按「繼續」接著存。")
            self.index, self.current = i, it["相對路徑"]
            if not folder.is_dir():
                raise OSError(2, "打包資料夾不見了")
            done.append(self._one(folder, it))
            entry["檔案"] = done + []
        # 打包清單（現場產生的：寫完一樣重讀比對）
        self.index, self.current = self.total, PACK_LIST_NAME
        lst = {"相對路徑": PACK_LIST_NAME, "來源": None, "內容": pack_list_text(done).encode("utf-8")}
        done.append(self._one(folder, lst))
        self.state = "核對中"
        for f in done:   # 最後整份再對一次大小（剛才每個檔都重讀算過檢查碼）
            if (folder / f["相對路徑"]).stat().st_size != f["大小"]:
                raise PackError(f"有 1 個檔存過去不一樣（{f['相對路徑']}），換個位置再試。")
        final = _numbered_dir(folder.with_name(folder.name.removesuffix(PACKING_SUFFIX)))
        os.rename(folder, final)
        self.folder = final
        rec = read_record(w)
        entry = self._entry(rec) or entry
        entry.update({"資料夾": str(final), "檔案": done, "核對": "通過", "完成時間": _now()})
        if rec.get("狀態") not in (CLEANING, CLOSED):
            rec["狀態"] = PACKED
        _write_record(w, rec)
        self.state = "已存好"

    def _one(self, folder: Path, it: dict) -> dict:
        """存一個檔、從目的地重讀核對；不一樣刪掉重存一次，第二次還不一樣就停。已經在（繼續）而且對得上的跳過。"""
        rel = it["相對路徑"]
        dst = folder / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        data = it.get("內容")
        if data is None and it.get("來源") is None:
            data = _gen_text(self.workdir, it["產生"])
        src = it.get("來源")
        size = len(data) if data is not None else src.stat().st_size
        known = hashlib.sha256(data).hexdigest() if data is not None else None

        def row(sha: str) -> dict:
            return {"相對路徑": rel, "來源": str(src.relative_to(self.workdir)) if src else None, "大小": size,
                    "sha256": sha}

        if dst.is_file() and dst.stat().st_size == size:   # 繼續：已經在的重算，對得上就跳過
            want = known or _sha(src)
            if _sha(dst) == want:
                return row(want)
        want = known
        for attempt in (1, 2):
            tmp = dst.with_name(f".{dst.name}{PACK_TMP_SUFFIX}")
            try:
                got = self._copy(src, data, tmp)   # 一邊讀來源一邊算 SHA-256
                os.replace(tmp, dst)
            finally:
                if tmp.exists():
                    tmp.unlink()   # 只清自己寫的暫存檔
            if want is None:
                want = got
            elif got != want:
                raise PackError(f"來源檔在存的時候變了（{rel}），等第 4 步、輸出都停了再試。")
            if self._fail_hook and self._fail_hook(rel, attempt):
                with open(dst, "r+b") as f:   # 測試：模擬寫壞一個位元組
                    b = f.read(1)
                    f.seek(0)
                    f.write(bytes([(b[0] ^ 0xFF) if b else 0]))
            if dst.stat().st_size == size and _sha(dst) == want:   # 從目的地重讀一遍
                return row(want)
            dst.unlink()   # 存過去不一樣：刪掉重存（只刪這次自己存的那一份）
        raise PackError(f"有 1 個檔存過去不一樣（{rel}），換個位置再試。")

    def _copy(self, src: Path | None, data: bytes | None, tmp: Path) -> str:
        h = hashlib.sha256()
        with open(tmp, "wb") as fo:
            if data is not None:
                fo.write(data)
                h.update(data)
            else:
                with open(src, "rb") as fi:   # 只讀來源：來源是硬連結也一樣，目的地是真的一份
                    while True:
                        if self._cancel.is_set():
                            raise PackError("停下來了：存好的留著，按「繼續」接著存。")
                        b = fi.read(HASH_CHUNK)
                        if not b:
                            break
                        fo.write(b)
                        h.update(b)
            fo.flush()
            os.fsync(fo.fileno())
        return h.hexdigest()


def pack_state(workdir: str | Path) -> dict:
    """`GET /api/close/state` 跟工作區有關的那一半（伺服器另外補 可以打包／位置選項／工作）。只讀。"""
    w = Path(workdir)
    rec = read_record(w)
    last = (rec.get("打包") or [None])[-1]
    ok = next((p for p in reversed(rec.get("打包") or []) if p.get("核對") == "通過"), None)
    exp = last_export(w)
    st = rec.get("狀態")
    records_only = st == CLOSED
    items = pack_items(w, records_only=records_only) if exp or records_only else []
    ver = export_version_info(w)
    resumable = bool(last and last.get("核對") != "通過" and last.get("資料夾") and Path(last["資料夾"]).is_dir())
    if ok:
        ok = {**ok, "資料夾": windows_display(ok.get("資料夾") or ""), "檔案": len(ok.get("檔案") or [])}
    return {"狀態": st, "輸出過": bool(exp), "打包大小": pack_size(items), "檔數": len(items) + (1 if items else 0),
            "暫存大小": clean_estimate(w), "最後輸出版本": ver["最後輸出版本"], "最新版本": ver["最新版本"],
            "不是最新版": bool(ver["最後輸出版本"] and ver["最新版本"] and ver["最後輸出版本"] != ver["最新版本"]),
            "最近一次": {k: v for k, v in last.items() if k != "檔案"} if last else None, "核對通過的": ok, "可以繼續": resumable, "只有紀錄": records_only}


def windows_display(p: Path | str) -> str:
    """（純函式）WSL 的 `/mnt/d/讀書會剪輯結案` → `D:\\讀書會剪輯結案`；其他照原樣。"""
    s = str(p)
    parts = Path(s).parts
    if len(parts) >= 3 and parts[0] == "/" and parts[1] == "mnt" and len(parts[2]) == 1 and parts[2].isalpha():
        return parts[2].upper() + ":\\" + "\\".join(parts[3:])
    return s


def pack_locations(kind: str | None = None, *, ismount=os.path.ismount, downloads=None, usage=None) -> list[dict]:
    """存到哪的選項（§1a 定案後畫面只有一顆鈕＋系統視窗；這份給 API／終端機用）：[{代號, 顯示名稱, 路徑, 剩餘位元組}]。
    下載：WSL 是 Windows 的下載資料夾（`fileio.windows_downloads`），其他是 ~/Downloads；D 槽：只有 WSL、而且 /mnt/d 有掛上
    （ismount 可以傳假的）；自己選：沒有路徑。找不到的選項不列。"""
    from bookclub import fileio

    kind = kind or fileio.platform_kind()
    usage = usage or shutil.disk_usage
    out: list[dict] = []

    def add(code: str, name: str, p: Path | None) -> None:
        free = None
        if p is not None:
            try:
                free = usage(str(p)).free
            except OSError:
                return
        out.append({"代號": code, "顯示名稱": name, "路徑": str(p) if p else None,
                    "位置顯示": windows_display(p) if p and kind == "wsl" else (str(p) if p else None), "剩餘位元組": free})

    try:
        dl = downloads() if downloads else (fileio.windows_downloads() if kind == "wsl" else Path.home() / "Downloads")
        if Path(dl).is_dir():
            add("下載", "下載資料夾", Path(dl))
    except Exception:  # noqa: BLE001 — 找不到下載資料夾：不列這一項
        pass
    if kind == "wsl" and ismount("/mnt/d"):
        add("D", "D 槽", Path("/mnt/d"))
    add("自己選", "自己選", None)
    return out
