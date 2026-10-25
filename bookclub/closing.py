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
                      (".cache/uv", "安裝工具時的下載暫存；多數跟工具共用同一份檔案，清了只省很少（實測約 0.2GB），不用特地清")):
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
# 10-19 #253：之後要能「重新匯入繼續修改」——工作區裡其他的小紀錄（第 3 步決定、名字、逐字時間、生成紀錄、參考音文字檔⋯）
# 也一起帶，放 `紀錄/<原本的相對路徑>`。只帶文字類的檔；影片、聲音、圖片等大檔不帶（`輸出/` 照舊只帶最後輸出那支與上面幾種紀錄）
PACK_TEXT_EXTS = (".json", ".jsonl", ".md", ".txt", ".csv", ".srt", ".html")
PACK_RECORD_MAX_BYTES = 50_000_000       # 10-19 #253：單檔上限，避免把意外變大的檔當小紀錄帶走（超過的寫進清單「沒帶的檔」）
PACK_SKIP_DIRS = ("輸出", "修改需求")      # 這兩個上面另外處理（修改需求整份、輸出只帶指定的）
NO_VIDEO_LINE = "原始影片沒有打包，請另外保留。"   # 10-19 #253：宇軒定的字句（以後做匯入功能時再在匯入那邊提醒）
VOICE_LINE = "生成好的聲音已經存在紀錄裡，之後匯入只要重做改到的地方。"   # 10-19 #253 做法 A
# 10-19 #253 做法 A：老師名字、學員重念、保留原聲學員名字的生成紀錄（每一句指到的選定檔、放回時間格檔、來源檔要帶）
GEN_LOGS = ("生成/老師紀錄.json", "生成/學員紀錄.json", "生成/保留原聲學員紀錄.json")


REPORT_TAIL = "請將錯誤訊息通知開發者。"   # 意外的錯誤（使用者自己改不了）一律用這句結尾


def unexpected(msg: str) -> bool:
    """（純函式）這句錯誤是不是意外的錯誤（畫面跳錯誤視窗）；其他（換個位置、等它做完再按一次⋯）是可以自己改正的，留紅字。"""
    return str(msg or "").rstrip().endswith(REPORT_TAIL)


class PackError(Exception):
    """打包不能開始、或做到一半停下來（訊息是給老師看的一句）。"""


MISSING_PRODUCT = "找不到最後輸出的成品影片，先到第 5 步重新輸出一次再打包。"
SOURCE_CHANGED = "存的時候工作區裡的檔案變了（{}），可能剛好又輸出或組裝了一次。等它做完再按一次。"
SOURCE_BUSY = "紀錄檔「{}」一直在變（第 5 步可能還開著、一直在記看過的地方），先關掉第 5 步那一頁或等一下再按一次。"
SMALL_RECORD_EXTS = PACK_TEXT_EXTS   # 10-19 #253：新帶的小紀錄（.md、.txt⋯）一樣，存的時候變了重讀重存
SMALL_RECORD_BYTES = 5_000_000
SMALL_RECORD_TRIES = 3


class _SourceChanged(Exception):
    """存一個檔的時候，工作區裡的來源變了（內部用，`_one` 決定要不要重試）。"""


def os_message(e: OSError) -> str:
    """（純函式）存檔時的系統錯誤 → 給老師看的一句（不帶錯誤名稱；細節另外印在終端機）。"""
    import errno

    if e.errno == errno.ENOSPC:
        return "存的地方滿了，清出空間或換個位置再按「繼續」。"
    if e.errno == errno.EFBIG:
        return "這個隨身碟一個檔不能超過 4 GB，換個位置再試。"
    if e.errno == errno.ENAMETOOLONG:
        return "存的地方路徑太長，換個位置再試。"
    if e.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
        return "存的地方不能寫入（沒有權限或唯讀），換個位置再試。"
    return "存的地方讀不到了，是不是拔掉了？插回去再按「繼續」。"


def ubuntu_inside_warning(kind: str, path: str | Path) -> str | None:
    """（純函式）WSL 上選的不是 Windows 的磁碟（`/mnt/<槽>`）：存在 Ubuntu 裡面，提醒一句（不擋）。"""
    if kind != "wsl":
        return None
    parts = Path(str(path)).parts
    if len(parts) >= 3 and parts[1] == "mnt" and len(parts[2]) == 1:
        return None
    return "這個位置存在 Ubuntu 裡面，Windows 看不到、也不會空出空間。"


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


def pack_items(workdir: str | Path, *, records_only: bool = False, strict: bool = True) -> list[dict]:
    """要打包的檔（§3.3）：[{相對路徑（打包裡）, 來源（工作區裡的檔）或 None（現場產生的）, 產生（逐字稿／清單的種類）}]。
    records_only：已結案後再存一份（影片已經清掉了）只存逐字稿、打包清單、紀錄。
    第 2 段審查必修 1：不是只存紀錄時，輸出紀錄記的最終成品不在（或還沒輸出過）→ 丟 PackError（不退回打包別的檔；
    不然會「核對通過」卻沒有成品，第 3 段清空間就把成品全清掉）。strict=False（畫面讀狀態用）不丟，成品那一項略過。"""
    w = Path(workdir)
    out: list[dict] = []
    if not records_only:
        rec = last_export(w)
        src = w / rec["檔案"] if rec else None
        if src is None or not src.is_file() or src.is_symlink():
            if strict:
                raise PackError(MISSING_PRODUCT)
        else:
            out.append({"相對路徑": src.name, "來源": src, "成品": True})
        if rec:
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
                if f.startswith(".") or p.is_symlink() or not p.is_file():   # .寫入鎖、.寫入中 之類的不打包
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
    out += extra_record_items(w, out)   # 10-19 #253：其他小紀錄（之後重新匯入要用）
    out += voice_items(w, out)          # 10-19 #253 做法 A：選定的生成聲音（之後匯入只重做改到的卡）
    return out


def voice_files(workdir: str | Path) -> list[Path]:
    """10-19 #253 做法 A：生成紀錄裡每一句指到的聲音檔——跟 `tts.output_missing` 檢查的同三個：`檔案`（選定那一次）、
    `放回時間格.檔案`、`放回時間格.來源檔案`（停格補長用）。`wd.localize` 解析，只收工作區裡面、現在還在的一般檔；
    同一個檔只列一次。沒被紀錄指到的嘗試檔、`_嘗試快取.json` 不在裡面。只讀。"""
    w = Path(workdir)
    root = w.resolve()
    seen: set[Path] = set()
    out: list[Path] = []
    for rel in GEN_LOGS:
        try:
            rec = wd.read_json(w / rel, default=None) or {}
        except (OSError, ValueError):
            continue
        rows = (rec.get("句子") if isinstance(rec, dict) else None) or []
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict):
                continue
            fit = r.get("放回時間格") if isinstance(r.get("放回時間格"), dict) else {}
            for path in (r.get("檔案"), fit.get("檔案"), fit.get("來源檔案")):
                p = wd.localize(path, w) if path else None
                if p is None or p.is_symlink() or not p.is_file() or not _inside(p, root):
                    continue
                key = p.resolve()
                if key not in seen:
                    seen.add(key)
                    out.append(w / key.relative_to(root))
    return out


def voice_items(workdir: str | Path, taken: list[dict]) -> list[dict]:
    """10-19 #253 做法 A：選定的生成聲音放 `紀錄/<原相對路徑>`（例如 `紀錄/生成/老師/S001_放回時間格.wav`），之後匯入直接
    放回原位。不套 50 MB 上限：這些是匯入時不用重新生成的關鍵，一句的長度有限，大小不會失控（真的很大也照帶）。
    工作區裡的這些檔清空間照舊清（`clean_plan` 不變），打包裡多一份。"""
    w = Path(workdir)
    srcs = {Path(it["來源"]).resolve() for it in taken if it.get("來源") is not None}
    dsts = {it["相對路徑"] for it in taken}
    out = []
    for p in voice_files(w):
        dst = str(Path("紀錄") / p.relative_to(w))
        if p.resolve() in srcs or dst in dsts:
            continue
        dsts.add(dst)
        out.append({"相對路徑": dst, "來源": p, "生成聲音": True})
    return out


def card_stats_line(workdir: str | Path) -> str:
    """10-19 #253：打包清單第一段的卡片統計，照最後輸出那一版、用第 5 步同一套分類（`finalcards.build`；最後輸出的不是
    現在處理紀錄那一版時用 `finalcards.version_page`）。宇軒 10-19 定的寫法：
    「卡片：做進成品 N 張（人工確認過 X、還沒確認 Y、退回 R）；沿用上一版 W 張；還沒處理 Z 張；第 3 步選定不修改 U 張。」
    - 做進成品＝這一版有它（通過＝人工確認過、還沒看＝還沒確認、退回＝退回）
    - 沿用上一版＝第 5 步標「已通過（沿用上一版）」的（範圍外、之前那一版通過、沒按過要修改）
    - 還沒處理＝這一版之後新增、還沒生成的，和「已改，等重做」的；範圍外、之前那一版還沒看完或退回的也算這裡
    - 第 3 步選定不修改＝第 5 步「第 3 步選定不修改」那一頁的筆數（`finalcheck.unchanged_rows` 的列數；不是修改需求的卡數，
      各段加總不一定等於有效卡數）
    刪掉、併入、拆成、消失的卡不算。為 0 的段落省略（做進成品那段、括號裡的確認過／還沒確認照寫，退回 0 省略）。
    算不出來（沒升級過、紀錄壞掉、對不到那一版）寫「卡片：查不到」。只讀。"""
    from bookclub import finalcards, needs, proclog

    w = Path(workdir)
    try:
        n = (last_export(w) or {}).get("版本號")
        if not isinstance(n, int):
            n = last_output_log(w)[0]
        if not isinstance(n, int) or not needs.exists(w) or not needs.healthy(w):
            return "卡片：查不到"
        st = needs.load(w)
        if n not in st.versions:
            return "卡片：查不到"
        log = proclog.load(w)
        cur = st.version_at((log or {}).get("產生時間")) if log else None
        if cur is not None and cur["版本號"] == n:
            part = finalcards.build(w, log=log)
            if part["來源"] != finalcards.SRC_NEW:
                return "卡片：查不到"
            cards, cross = part["卡片"], part["跨版本卡片"]
        else:
            cards, cross = finalcards.version_page(w, n, None)["卡片"], []
        ok = seen = back = todo = 0
        carry = {needs.PASSED: 0, needs.NOT_SEEN: 0, needs.RETURNED: 0}   # 沿用上一版：照之前最近那一版的狀態拆開
        listed: set[str] = set()
        for c in cards + cross:
            k = str(c.get("卡號"))
            listed.add(k)
            if c.get("刪掉了"):
                continue
            if c.get("跨版本") or c.get("這一版沒處理到"):
                # 10-20 宇軒定（甲）：範圍外的卡（第 5 步的沿用卡、跨版本卡）都算沿用上一版，狀態照之前最近那一版；
                # 按過「要修改」（已改，等重做）的才是還沒處理。沒有之前那一版的（看舊版時、之後的版本才做的）也是還沒處理
                old = finalcards._older_row(st, k, n)
                s3 = (old[1].get("狀態") if old else None)
                if c.get("狀態") == finalcards.EDITED or c.get("範圍外改了") or s3 not in carry:
                    todo += 1
                else:
                    carry[s3] += 1
            elif c.get("狀態") == needs.PASSED:
                ok += 1
            elif c.get("狀態") == needs.RETURNED:
                back += 1
            elif c.get("狀態") == needs.NOT_SEEN:
                seen += 1
            else:   # 還沒生成、已改，等重做
                todo += 1
        # 第 5 步清單沒列的有效卡。10-20 審查建議 3：照 `finalcards._from_needs` 同一套判斷，那一版之後才建的（第 5 步新增
        # 還沒生成）、有等重做的、第 3 步改成要處理的 → 還沒處理（看舊版那條路才會遇到）；其他不在這裡算（見下面「選定不修改」）
        v = st.versions[n]
        vtime = str(v.get("建立時間") or "")
        hist = needs.read_history(w) if v.get("其他卡指紋") is None else None
        for k, c in st.cards.items():
            if c.get("狀態") != needs.ALIVE or k in listed:
                continue
            if (c.get("等重做") or str(c.get("建立時間") or "") > vtime
                    or finalcards._step3_turned_on(st, k, c, v, hist)):
                todo += 1
        # 10-20 宇軒 8799 實測（第 5 步那頁 14、清單 12 對不上）：「第 3 步選定不修改」照第 5 步那一頁的筆數——
        # `finalcheck.unchanged_rows`（直接從第 3 步決定算，例如保留原聲的學員段落，不一定是修改需求裡的卡；網頁 keepN＝列數）。
        # 所以各段加起來不一定等於有效卡數。最後輸出的是現在處理紀錄那一版時用同一份處理紀錄（跟畫面一模一樣）；
        # 是舊版時用那一版的處理紀錄算（第 5 步看舊版沒有這一頁）
        from bookclub import finalcheck

        keep = len(finalcheck.unchanged_rows(w, log if cur is not None and cur["版本號"] == n else last_output_log(w)[1]))
    except Exception:  # noqa: BLE001 — 讀不到就寫查不到，不擋打包
        return "卡片：查不到"
    detail = f"人工確認過 {ok}、還沒確認 {seen}" + (f"、退回 {back}" if back else "")
    parts = [f"做進成品 {ok + seen + back} 張（{detail}）"]
    w_n = sum(carry.values())
    if w_n:
        sub = [f"{name} {carry[key]}" for key, name in ((needs.PASSED, "人工確認過"), (needs.NOT_SEEN, "還沒確認"),
                                                          (needs.RETURNED, "退回")) if carry[key]]
        parts += [f"沿用上一版 {w_n} 張" + ("" if carry[needs.PASSED] == w_n else f"（{'、'.join(sub)}）")]
    parts += [f"還沒處理 {todo} 張"] if todo else []
    parts += [f"第 3 步選定不修改 {keep} 張"] if keep else []
    return "卡片：" + "；".join(parts) + "。"


def voice_match(workdir: str | Path) -> tuple[bool | None, int]:
    """10-19 #253（宇軒選甲）：打包帶的是現在的生成聲音；拿最後輸出那一版的處理紀錄（`修改需求/版本/vN/處理紀錄.json`）
    每一筆的 `檔案指紋`（`proclog.stamp_contents` 寫的生成檔內容 sha1 前 16 碼）跟現在的檔重算比對。
    回 (同一版, 不一樣的檔數)：True＝都一樣；False＝有之後又生成過的（檔不在也算不一樣；同一個檔被好幾筆用到只算一個）；
    None＝查不到（舊紀錄沒指紋、對不到那一版、內容格式不對——10-20 審查：整個包起來，不能讓打包在最後一步失敗）。只讀。"""
    from bookclub import proclog

    w = Path(workdir)
    try:
        _n, log = last_output_log(w)
        if not isinstance(log, dict) or not isinstance(log.get("紀錄") or [], list):
            return None, 0
        recs = [r for r in log.get("紀錄") or [] if isinstance(r, dict) and r.get("檔案")]
        if any(not isinstance(r["檔案"], str) or not r.get("檔案指紋") for r in recs):
            return None, 0
        want: dict[str, str] = {}
        for r in recs:
            f = str(r["檔案"])
            if want.setdefault(f, str(r["檔案指紋"])) != str(r["檔案指紋"]):
                return None, 0   # 同一個檔記了兩個不同的指紋：紀錄本身對不起來
        diff = 0
        for f, fp in want.items():
            p = wd.localize(f, w)
            if (proclog.file_print(p) if p is not None else None) != fp:
                diff += 1
        return diff == 0, diff
    except Exception:  # noqa: BLE001 — 查不到就寫查不到
        return None, 0


def voice_match_line(same: bool | None, diff: int) -> str | None:
    """（純函式）打包清單第一段那一句：同一版不寫；不一樣、查不到各一句。"""
    if same is True:
        return None
    if same is False:
        return f"生成聲音是之後改過的狀態，比打包的成品新（有 {diff} 個聲音檔不一樣）。"
    return "生成聲音跟打包的成品是不是同一版：查不到。"


def _workdir_files(w: Path):
    """工作區裡的一般檔：產出 (相對路徑, 完整路徑, 大小)。不跟捷徑；點開頭的檔與資料夾（`.結案清理中/`、`.寫入鎖`、
    `.xxx.寫入中` 之類）和 `結案.json`（打包的時候正在寫）略過。只讀。"""
    import stat as _stat

    for root, dirs, files in os.walk(w, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        top = Path(root) == w
        for f in sorted(files):
            if f.startswith(".") or (top and f == RECORD_FILE):
                continue
            fp = Path(root) / f
            try:
                st = os.lstat(fp)
            except OSError:
                continue
            if _stat.S_ISREG(st.st_mode):
                yield fp.relative_to(w), fp, st.st_size


def extra_record_items(workdir: str | Path, taken: list[dict]) -> list[dict]:
    """10-19 #253：打包多帶的小紀錄——工作區裡文字類的檔（`PACK_TEXT_EXTS`），放 `紀錄/<原本的相對路徑>`
    （例如 `紀錄/覆核/覆核決定.json`、`紀錄/transcript/merged.json`）。不帶：`輸出/`、`修改需求/`（上面另外處理）、
    快取資料夾（`CACHE_DIRS`：`web快取/`、`chunks/`⋯）、`修改需求.升級中/`、已經在 taken 裡的（`RECORD_FILES` 那幾個）、超過 `PACK_RECORD_MAX_BYTES` 的、清空間會清掉的暫存（例如生成用的快取）。
    打包裡已經有同一個路徑的（理論上不會）也不帶。只讀，不改變清空間的清單（工作區原本就留著這些檔）。"""
    w = Path(workdir)
    srcs = {Path(it["來源"]) for it in taken if it.get("來源") is not None}
    dsts = {it["相對路徑"] for it in taken}
    out: list[dict] = []
    from bookclub import needs

    skip_dirs = PACK_SKIP_DIRS + CACHE_DIRS + (needs.STAGING,)   # 0b7a6dd 審查：快取資料夾、升級中途留下的備份不帶
    for rel, fp, size in _workdir_files(w):
        if len(rel.parts) > 1 and rel.parts[0] in skip_dirs:
            continue
        if fp in srcs or fp.suffix.lower() not in PACK_TEXT_EXTS or size > PACK_RECORD_MAX_BYTES:
            continue
        if classify(rel.as_posix())[0] == "清":
            continue
        dst = str(Path("紀錄") / rel)
        if dst in dsts:
            continue
        dsts.add(dst)
        out.append({"相對路徑": dst, "來源": fp})
    return out


def unpacked_lines(workdir: str | Path, items: list[dict]) -> list[str]:
    """10-19 #253：打包清單「沒帶的檔」那一段（工作區裡沒打包的檔）。超過上限的紀錄檔一個一行；其他（影片、聲音、
    其他版本的成品⋯）照最上層資料夾合成一行（檔數、副檔名、大小），不然生成的聲音檔會列好幾百行。只讀。"""
    w = Path(workdir)
    srcs = {Path(it["來源"]) for it in items if it.get("來源") is not None}
    big: list[str] = []
    groups: dict[str, list] = {}
    for rel, fp, size in _workdir_files(w):
        if fp in srcs:
            continue
        if fp.suffix.lower() in PACK_TEXT_EXTS and size > PACK_RECORD_MAX_BYTES:
            big.append(f"{rel.as_posix()}：{human(size)}（紀錄檔超過 {human(PACK_RECORD_MAX_BYTES)}，太大沒帶）")
            continue
        key = f"{rel.parts[0]}/" if len(rel.parts) > 1 else "工作區最上層"
        g = groups.setdefault(key, [0, 0, set()])
        g[0] += 1
        g[1] += size
        g[2].add(fp.suffix.lower() or "沒有副檔名")
    return big + [f"{k}：{n} 個（{'、'.join(sorted(exts))}），共 {human(sz)}"
                  for k, (n, sz, exts) in sorted(groups.items())]


def pack_version_line(workdir: str | Path) -> str:
    """10-19 #253：打包清單第一段「最後輸出的是第 N 版（共 M 版）。」N＝成品檢查檔 `輸出成品` 記的版本號（沒記的舊紀錄照
    處理紀錄產生時間對版本表）、M＝`修改需求/` 版本表的版本數（最新的版本號）。只有 N 寫「最後輸出的是第 N 版。」；
    都算不出來寫「版本：查不到」。"""
    from bookclub import needs

    w = Path(workdir)
    try:
        n = (last_export(w) or {}).get("版本號")
        if not isinstance(n, int):
            n = last_output_log(w)[0]
        vers = needs.load(w).versions if needs.exists(w) else {}
        m = max(vers) if vers else None
    except Exception:  # noqa: BLE001 — 讀不到就寫查不到，不擋打包
        n = m = None
    if isinstance(n, int) and isinstance(m, int):
        return f"最後輸出的是第 {n} 版（共 {m} 版）。"
    if isinstance(n, int):   # 0b7a6dd 審查：沒升級過（沒有版本表）但輸出紀錄有版本號——跟畫面一樣寫第 N 版，不寫共幾版
        return f"最後輸出的是第 {n} 版。"
    return "版本：查不到"


def pack_size(items: list[dict]) -> int:
    return sum(it["來源"].stat().st_size for it in items if it.get("來源") is not None and it["來源"].is_file())


def remaining_bytes(items: list[dict], folder: Path | None) -> int:
    """「繼續」還要存多少（空間檢查用）：打包資料夾裡已經在、大小對的不算（不重算檢查碼，存的時候會再核對）。"""
    total = 0
    for it in items:
        src = it.get("來源")
        if src is None or not src.is_file():
            continue
        n = src.stat().st_size
        dst = folder / it["相對路徑"] if folder else None
        if dst is not None and dst.is_file() and dst.stat().st_size == n:
            continue
        total += n
    return total


def resume_folder(entry: dict | None) -> Path | None:
    """上次沒核對完的那一次打包，「繼續」要用的資料夾：記的 `…_打包中` 還在就用它；不在、但改名前記下的 `改名成`
    那個資料夾在（改名完成、還沒寫完成就當掉）也用它（重新核對）。都沒有＝None。"""
    if not entry or entry.get("核對") == "通過" or not entry.get("資料夾"):
        return None
    p = Path(entry["資料夾"])
    if p.is_dir() and not p.is_symlink():
        return p
    alt = Path(entry["改名成"]) if entry.get("改名成") else None   # 只認改名前記下的名字，不用猜
    if alt is not None and alt.is_dir() and not alt.is_symlink():
        return alt
    return None


def clean_estimate(workdir: str | Path) -> int:
    """框裡那一句「共 N GB」（第 3 段起＝`clean_plan` 的合計；舊名字留著給別的地方用）。只讀。"""
    return clean_plan(workdir)["合計"]


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(HASH_CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def pack_base(location: str | Path) -> Path:
    """打包資料夾的上一層。10-19 #252：選到的資料夾本身就叫「讀書會剪輯結案」（第二支影片結案時老師很可能直接點進上次那個）
    → 直接放在它下面，不再多建一層變成 `讀書會剪輯結案/讀書會剪輯結案/`；選一般的資料夾照舊在下面建「讀書會剪輯結案」。"""
    loc = Path(location)
    return loc if loc.name == PACK_ROOT else loc / PACK_ROOT


def check_location(workdir: str | Path, location: str | Path, need: int, usage=None) -> Path:
    """打包位置能不能用（§3.4、§3.6）：在不在、是不是資料夾、不在工作區裡（也不是工作區的上層——打包資料夾會建在
    工作區裡面）、空間夠不夠（要 need＋200 MB）。能用回 resolve 後的路徑，不能用丟 PackError（給老師看的一句）。"""
    from bookclub import fileio

    w = Path(workdir).resolve()
    loc = Path(location).expanduser()
    if not loc.is_dir():
        raise PackError(f"找不到這個資料夾：{location}（D 槽沒插？），換個位置再試。")
    loc = loc.resolve()
    if _inside(loc, w) or _inside(w, pack_base(loc)):   # 10-19 #252：跟真的建資料夾的地方用同一個算法
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


def pack_list_text(files: list[dict], *, version: str | None = None, skipped: list[str] | None = None,
                   cards: str | None = None, voices: bool = False, voice_note: str | None = None) -> str:
    """`打包清單.txt`（§3.3、§6.4）：最上面兩句說明，之後每個檔的大小與 SHA-256。`\\r\\n`（Windows 記事本好讀）。
    10-19 #253：第一段再補版本那一行（version）與「原始影片沒有打包，請另外保留。」；最後一段「沒帶的檔」（skipped）。"""
    # 10-19 #253 做法 A：卡片統計（cards）接在版本行下面；有帶生成聲音（voices）時在原始影片那一句後面補一句
    head = [*PRIVACY_LINES] + ([version] + ([cards] if cards else []) + [NO_VIDEO_LINE] + ([VOICE_LINE] if voices else [])
                               + ([voice_note] if voices and voice_note else [])
                               if version is not None else [])
    lines = [*head, "", f"共 {len(files)} 個檔（不含這份清單）：", ""]
    for f in files:
        lines.append(f"{f['相對路徑']}\t{f['大小']} 位元組\tSHA-256 {f['sha256']}")
    if skipped:
        lines += ["", "沒帶的檔（影片、聲音、暫存檔，和其他版本的成品；路徑是工作區裡的位置）：", ""] + skipped
    return NL.join(lines) + NL


class PackJob:
    """背景打包一次（網頁看得到第幾個檔、可以停）。只寫目的地與 `結案.json`。"""

    def __init__(self, workdir: Path, location: Path, *, display: str | None = None, resume: bool = False,
                 records_only: bool = False, fail_hook=None, clean_fp: str | None = None, busy=None):
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
        # 第 3 段（§1a 第 3 點）：存→核對→全對才清，一個按鈕跑到底。clean_fp＝老師按下去那一刻畫面上的清單指紋；
        # busy()＝伺服器問「第 4 步、輸出、複製有沒有在跑」（回原因或 None）
        self.clean_fp, self._busy = clean_fp, busy
        self.clean_error: str | None = None
        self.saved: int | None = None

    @property
    def running(self) -> bool:
        return self.state in ("等待", "存檔中", "核對中", "清理中")

    def cancel(self) -> None:
        self._cancel.set()

    def start(self) -> "PackJob":
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        return self

    def to_dict(self) -> dict:
        return {"用途": self.purpose, "狀態": self.state, "第幾個": self.index, "共幾個": self.total,
                "檔名": self.current, "錯誤": self.error, "位置顯示": self.display,
                "資料夾": windows_display(self.folder) if self.folder else None,
                "清理錯誤": self.clean_error, "省下位元組": self.saved}

    # --- 主流程 ---
    def run(self) -> None:
        try:
            self._run()
            if self.clean_fp and not self.records_only:
                self.state = "清理中"   # 從這裡開始不給停止（§4.4）
                try:
                    self.saved = clean(self.workdir, self.clean_fp, busy=self._busy)["省下位元組"]
                    self.state = CLOSED
                except CleanError as e:   # 清之前的再確認沒過：存好的留著，一個檔都沒刪
                    self.state, self.clean_error = "已存好", str(e)
                except Exception as e:  # noqa: BLE001 — 搬到一半：結案.json 是「清理中」，下次「繼續清」
                    print(f"⚠️ [結案清理] {type(e).__name__}：{e}")
                    self.state, self.error = "失敗", CLEAN_FAILED
                return
        except PackError as e:
            self.state, self.error = ("停止" if self._cancel.is_set() else "失敗"), str(e)
        except OSError as e:
            print(f"⚠️ [結案打包] {type(e).__name__}：{e}")   # 細節印終端機；畫面只給一句白話
            self.state, self.error = "失敗", os_message(e)
        except Exception as e:  # noqa: BLE001 — 背景執行緒要把失敗記下來給網頁看
            print(f"⚠️ [結案打包] {type(e).__name__}：{e}")
            self.state, self.error = "失敗", "存的時候出了問題，換個位置再試；還是不行，請將錯誤訊息通知開發者。"

    def _entry(self, rec: dict) -> dict | None:
        packs = rec.get("打包") or []
        return packs[-1] if packs else None

    def _run(self) -> None:
        w = self.workdir
        rec = read_record(w)
        items = pack_items(w, records_only=self.records_only)
        self.total = len(items) + 1   # ＋打包清單
        last = self._entry(rec)
        folder = resume_folder(last) if self.resume else None
        if self.resume and folder is None:
            raise PackError("找不到上次存到一半的資料夾（換了位置、或已經移走了），請重新按「結案打包…」選位置。")
        if folder is None:
            day = datetime.now().strftime("%Y%m%d")
            base = pack_base(self.location)   # 10-19 #252：選到「讀書會剪輯結案」本身就不再多建一層
            base.mkdir(parents=True, exist_ok=True)
            folder = _numbered_dir(base / f"{video_stem(w)}_{day}{PACKING_SUFFIX}")
            folder.mkdir(parents=True)
            entry = {"時間": _now(), "位置": str(self.location), "位置顯示": self.display,
                     "版本號": export_version_info(w)["最後輸出版本"], "資料夾": str(folder), "檔案": [],
                     "核對": "還沒", "只有紀錄": self.records_only,
                     "含成品": any(it.get("成品") for it in items)}   # 第 3 段清空間前檢查這一欄
            rec.setdefault("打包", []).append(entry)
        else:
            entry = last
        self.folder = folder
        # 已經有核對通過的打包（再存一份）：狀態留「已打包」，中途停掉不倒退成「打包中」
        if rec.get("狀態") not in (CLEANING, CLOSED) and not any(
                p.get("核對") == "通過" for p in rec.get("打包") or []):
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
        # 10-19 #253（宇軒選甲）：帶的是現在的聲音，跟最後輸出那一版的處理紀錄比指紋，不一樣在清單寫一句、結案.json 記下
        voices = any(it.get("生成聲音") for it in items)
        same, diff = voice_match(w) if voices else (None, 0)
        text = pack_list_text(done, version=pack_version_line(w), skipped=unpacked_lines(w, items),   # 10-19 #253
                              cards=card_stats_line(w), voices=voices, voice_note=voice_match_line(same, diff))
        lst = {"相對路徑": PACK_LIST_NAME, "來源": None, "內容": text.encode("utf-8")}
        done.append(self._one(folder, lst))
        self.state = "核對中"
        for f in done:   # 最後整份再對一次大小（剛才每個檔都重讀算過檢查碼）
            if (folder / f["相對路徑"]).stat().st_size != f["大小"]:
                raise PackError(f"有 1 個檔存過去不一樣（{f['相對路徑']}），換個位置再試。")
        if folder.name.endswith(PACKING_SUFFIX):
            final = _numbered_dir(folder.with_name(folder.name.removesuffix(PACKING_SUFFIX)))
            # 第二輪審查建議 1：改名前先把要改成的名字記進 結案.json——改名後、記完成前當掉，「繼續」只認這個名字
            rec = read_record(w)
            e2 = self._entry(rec)
            if e2 is not None:
                e2["改名成"] = str(final)
                _write_record(w, rec)
            entry["改名成"] = str(final)
            os.rename(folder, final)
        else:
            final = folder   # 上次已經改好名、還沒寫 結案.json：重新核對過了，照用
        self.folder = final
        rec = read_record(w)
        entry = self._entry(rec) or entry
        entry.update({"資料夾": str(final), "檔案": done, "核對": "通過", "完成時間": _now()})
        if voices:   # 10-19 #253：聲音跟成品是不是同一版（None＝查不到）
            entry.update({"聲音跟成品同一版": same, "聲音不一樣檔數": diff})
        if rec.get("狀態") not in (CLEANING, CLOSED):
            rec["狀態"] = PACKED
        _write_record(w, rec)
        self.state = "已存好"

    def _one(self, folder: Path, it: dict) -> dict:
        """存一個檔、從目的地重讀核對；不一樣刪掉重存一次，第二次還不一樣就停。已經在（繼續）而且對得上的跳過。
        第 2 段第二輪審查建議 2：小的紀錄檔（.json／.jsonl，< 5 MB；例如第 5 步邊看邊存的 成品檢查.json）存的時候來源變了
        → 重讀重存，最多 3 次，還是變才失敗；影片等大檔一變就失敗。"""
        rel = it["相對路徑"]
        src = it.get("來源")
        try:   # 第 2 段審查建議 (b)：來源剛好不見了（又組裝、被移走）＝來源變了，不是「出了問題」
            small = src is not None and src.suffix.lower() in SMALL_RECORD_EXTS and src.stat().st_size < SMALL_RECORD_BYTES
        except OSError:
            raise PackError(SOURCE_CHANGED.format(rel)) from None
        for n in range(1, (SMALL_RECORD_TRIES if small else 1) + 1):
            try:
                return self._one_try(folder, it)
            except _SourceChanged:
                if n >= (SMALL_RECORD_TRIES if small else 1):
                    if small:
                        raise PackError(SOURCE_BUSY.format(src.name)) from None
                    raise PackError(SOURCE_CHANGED.format(rel)) from None
                time.sleep(0.2)
        raise PackError(SOURCE_CHANGED.format(rel))

    def _one_try(self, folder: Path, it: dict) -> dict:
        rel = it["相對路徑"]
        dst = folder / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        data = it.get("內容")
        if data is None and it.get("來源") is None:
            data = _gen_text(self.workdir, it["產生"])
        src = it.get("來源")
        if src is not None and not src.is_file():
            raise _SourceChanged()
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

        def stamp():
            st = src.stat()
            return (st.st_size, st.st_mtime_ns, st.st_ino)

        for attempt in (1, 2):
            tmp = dst.with_name(f".{dst.name}{PACK_TMP_SUFFIX}")
            before = stamp() if src is not None else None
            try:
                got = self._copy(src, data, tmp)   # 一邊讀來源一邊算 SHA-256
                # 第 2 段審查必修 2：複製前後來源的大小、修改時間、inode 有變＝存的時候被改了（又輸出、又組裝）
                if src is not None and (not src.is_file() or stamp() != before or before[0] != size):
                    raise _SourceChanged()
                os.replace(tmp, dst)
            finally:
                if tmp.exists():
                    tmp.unlink()   # 只清自己寫的暫存檔
            if want is None:
                want = got
            elif got != want:
                raise _SourceChanged()
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
    items = pack_items(w, records_only=records_only, strict=False) if exp or records_only else []
    missing = bool(exp) and not records_only and not any(it.get("成品") for it in items)
    ver = export_version_info(w)
    resumable = resume_folder(last) is not None
    # 10-17 畫面測試：存好之後又輸出了新的成品（跟 clean_check 同一個判斷）——框裡一打開就寫那一句、清掉鈕灰掉，不用按下去才被擋
    new_export = None
    if ok and exp and st == PACKED:
        saved = {Path(str(f.get("相對路徑") or "")).name for f in ok.get("檔案") or []}
        if Path(str(exp["檔案"])).name not in saved:
            new_export = NEW_EXPORT
    if ok:
        ok = {**ok, "資料夾": windows_display(ok.get("資料夾") or ""), "檔案": len(ok.get("檔案") or [])}
    # 第 3 段：框裡那一句的大小、按下去要帶的清單指紋（清理中、已結案不重算：清單是上次記下的）
    plan = clean_plan(w) if st not in (CLEANING, CLOSED) else None
    cl = rec.get("清理") or {}
    clean_info = None
    if cl:
        clean_info = {"省下位元組": cl.get("省下位元組"), "開始": cl.get("開始"), "完成": cl.get("完成"),
                      "檔數": len(cl.get("清單") or []), "成品存在": windows_display(cl.get("打包資料夾") or "")}
    return {"狀態": st, "輸出過": bool(exp), "打包大小": pack_size(items), "檔數": len(items) + (1 if items else 0),
            "暫存大小": plan["合計"] if plan else 0, "清單指紋": plan["清單指紋"] if plan else None,
            "含影片副本": bool(plan and plan["含影片副本"]), "清理": clean_info, "已結案": closed(w),
            "可以繼續清": st == CLEANING and not cl.get("搬回一部分"),   # 開發者 --restore 暫停中：畫面不給「繼續清」
            "最後輸出版本": ver["最後輸出版本"], "最新版本": ver["最新版本"],
            "不是最新版": bool(ver["最後輸出版本"] and ver["最新版本"] and ver["最後輸出版本"] != ver["最新版本"]),
            "最近一次": {k: v for k, v in last.items() if k != "檔案"} if last else None, "核對通過的": ok,
            "成品不在": missing, "可以繼續": resumable, "只有紀錄": records_only, "存好後又輸出": new_export}


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


# ===========================================================================
# 清空間（32 號設計 §4，以 §1a、§1b 為準；1015-8 第 3 段）
# ===========================================================================
#
# 三道保險（§9.1）：
# 1. 沒存好就不能清：最近一次核對通過的打包、`含成品: true`；清之前再看一次打包的每個檔在、大小對（`clean_check`）。
# 2. 只刪認得的：清的規則和留的規則寫在下面同一個地方（`classify`），一個檔兩邊都對到時留的贏、兩邊都沒對到＝不認得、不動；
#    只在工作區裡找、不跟捷徑（`os.walk(followlinks=False)`，捷徑本身也不刪）。工作區外只有一個例外：WSL 上複製進
#    `~/讀書會剪輯資料/影片/` 的那一支影片（§1a 問題 1 甲；Mac 上影片是原檔，不碰）。
# 3. 看到的就是刪的：清單指紋要跟老師按下去那一刻的一樣；先把清單寫進 `結案.json`（清理中）→ 一個一個搬進 `.結案清理中/`
#    → 寫「刪除開始」→ 整個資料夾刪掉 → 寫已結案。中斷了「繼續清」照記下的清單做完；「刪」開始前開發者可以 `--restore` 搬回去。

import errno
import fnmatch
import json
from pathlib import PurePosixPath

CLEAN_DIR = ".結案清理中"
OUTSIDE_DIR = "_工作區外"                     # .結案清理中/ 底下放工作區外那支影片副本的地方（工作區自己的檔不會叫這個名字：不認得）
CAT_VIDEO, CAT_AB, CAT_GEN, CAT_TMP, CAT_COPY = "影片", "比對用聲音", "AI 生成的聲音", "暫存", "影片副本"
CAT_NOTES = {CAT_VIDEO: "各版本的成品影片（最後輸出的那一支已經存好）", CAT_AB: "比對、試聽用的聲音檔",
             CAT_GEN: "AI 生成的聲音", CAT_TMP: "播放和分析用的暫存", CAT_COPY: "複製進來的那支原始影片（Windows 那邊有原檔）"}

# ---- 留的規則（留的贏）----
KEEP_DIRS = ("transcript", "修改需求", "校對", "覆核", "匯出", "備份", CLEAN_DIR)   # 整個資料夾都留
KEEP_EXTS = (".json", ".jsonl", ".md", ".txt", ".csv")                             # 紀錄：在哪裡都留（§9.1）
CACHE_JSON = ("_嘗試快取.json", "_停頓快取.json")                                  # 例外：§2.2 生成用的快取要清
TOP_KEEP_EXTS = KEEP_EXTS + (".html",)                                            # 工作區最上層：名字覆核.html 之類
# ---- 清的規則 ----
CACHE_DIRS = ("web快取", "chunks", "名字候選", "重疊")                              # 整個資料夾是暫存（.json 照樣留）
OUT_VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".mkv")
AB_TRACKS = ("原聲音軌.wav", "新聲音軌.wav")

PACK_GONE = "找不到剛才存的地方（D 槽沒插？），插回去才能清。"
PLAN_CHANGED = "剛剛這裡的檔案又有變動，按「清掉這一集的暫存檔」再清一次。"
NEW_EXPORT = "存好之後又輸出了新的成品。先按「再存一份…」把新的存起來，再按「清掉這一集的暫存檔」。"
NO_PRODUCT = "存好的那一份裡沒有成品影片，不能清。先在第 5 步重新輸出成品，再結案打包一次。"
TMP_THERE = "上次清理沒有做完（可能中途關機或當掉），為了不刪錯東西，這次先不清。請將錯誤訊息通知開發者。"   # 開發者用 bookclub close --restore 或手動看過再清
CLEAN_FAILED = "清的時候出了問題，按「繼續清」再試一次；還是不行，請將錯誤訊息通知開發者。"


class CleanError(Exception):
    """不能清（訊息是給老師看的一句）。丟出來的時候一個檔都還沒動。"""


HANDOFF_BACKUP = "交接前_"   # 33 號設計 §6：匯入交接包之前的紀錄備份（`備份/交接前_<日期>/`），結案清空間時一起清


def _handoff_backup(parts: tuple) -> bool:
    return len(parts) > 2 and parts[0] == "備份" and parts[1].startswith(HANDOFF_BACKUP)


def _keep(parts: tuple, name: str) -> bool:
    suf = PurePosixPath(name).suffix.lower()
    if _handoff_backup(parts):
        return False
    if parts[0] in KEEP_DIRS:
        return True
    if len(parts) == 1 and (name == RECORD_FILE or suf in TOP_KEEP_EXTS):
        return True
    if suf in KEEP_EXTS and name not in CACHE_JSON:
        return True
    if parts[0] == "輸出" and len(parts) == 2 and any(fnmatch.fnmatchcase(name, g) for g in OUTPUT_RECORD_GLOBS):
        return True
    return False


def _clean_cat(parts: tuple, name: str) -> str | None:
    top, n, low = parts[0], len(parts), name.lower()
    if n == 1:
        if name in ("audio.flac", "audio.抽音中.flac") or (name.startswith("audio.殘檔-") and low.endswith(".flac")):
            return CAT_TMP
        return None
    if top in CACHE_DIRS or _handoff_backup(parts):
        return CAT_TMP
    if top == "輸出":
        if n == 2:
            if name.startswith("_輸出中_") or (name.startswith(".") and name.endswith(".輸出中")):
                return CAT_TMP
            if name.startswith(("成品_", "最終成品_")) and low.endswith(OUT_VIDEO_EXTS):
                return CAT_VIDEO
            if low.endswith(".wav") and (name.startswith(("原聲_", "新聲音_")) or name in AB_TRACKS):
                return CAT_AB
            return None
        return CAT_AB if parts[1] == "處理前後" else None
    if top == "生成":
        if low.endswith(".wav") or name in CACHE_JSON or any(p.startswith("重做前_") for p in parts[1:-1]):
            return CAT_GEN
        return None
    if top == "參考音":
        if n == 2 and low.endswith(".wav"):
            return CAT_TMP
        if n >= 3 and parts[1].startswith("舊候選備份_"):
            return CAT_TMP
    return None


def classify(rel: str) -> tuple[str | None, str | None]:
    """（純函式）工作區裡的一個檔（相對路徑）→ ("留", None)／("清", 分類)／(None, None)＝不認得、不動。留的規則先看（留的贏）。"""
    parts = PurePosixPath(str(rel).replace(os.sep, "/")).parts
    if not parts or any(p in ("..", "") for p in parts):
        return None, None
    name = parts[-1]
    if _keep(parts, name):
        return "留", None
    cat = _clean_cat(parts, name)
    return ("清", cat) if cat else (None, None)


def _analysis_video(w: Path) -> Path | None:
    try:
        v = (wd.read_json(wd.analysis_result_path(w), default=None) or {}).get("video")
    except (OSError, ValueError):
        return None
    return Path(str(v)) if v else None


def _video_copy_row(w: Path, kind: str, data: Path | None) -> dict | None:
    """§1a 問題 1 甲：WSL 上這個工作區的影片是複製進 `~/讀書會剪輯資料/影片/` 的那一份 → 列進清單（只有這一支）。
    不是 WSL、影片不在匯入資料夾最上層、是捷徑、別的還沒結案的專案也用這支影片 → 不列。"""
    from bookclub.fileio import VIDEO_EXTS

    if kind != "wsl":
        return None
    v = _analysis_video(w)
    if v is None:
        return None
    if data is None:
        from bookclub.config import data_dir

        data = data_dir()
    try:
        imp = (Path(data) / "影片").resolve()
        if v.is_symlink() or not v.is_file() or not v.name.lower().endswith(VIDEO_EXTS):
            return None
        if v.parent.resolve() != imp:
            return None
        me, target = w.resolve(), v.resolve()
        for d in project_dirs(Path(data)):   # 專案清單.json 壞掉（ValueError）：下面接住，讀不到就不列影片副本
            if d.resolve() == me or closed(d):
                continue
            other = _analysis_video(d)
            if other is not None and other.exists() and other.resolve() == target:
                return None   # 別的專案還要用這支影片
        size = v.stat().st_size
    except (OSError, ValueError):   # 審查建議 1：專案清單、分析結果壞掉 → 不列（寧可少清）
        return None
    return {"相對路徑": f"{OUTSIDE_DIR}/{v.name}", "大小": size, "分類": CAT_COPY, "說明": CAT_NOTES[CAT_COPY],
            "路徑": str(v)}


FP_SKIP = ("web快取/",)   # 審查必修 C：播放用的快取（老師邊存邊聽卡片就會多），不算進指紋


def _fingerprint(rows: list[dict]) -> str:
    """清單指紋。`web快取/` 底下的不算：那裡只會因為聽了一段而多出試聽檔（隨時能重切），不該擋清；
    真的清的時候用伺服器當下重算的清單（同一套規則），多出來的試聽檔一起清。其他地方有任何一個檔不一樣，指紋就不同。"""
    key = [[r["相對路徑"], r["大小"], r.get("路徑")] for r in rows if not r["相對路徑"].startswith(FP_SKIP)]
    return hashlib.sha1(json.dumps(key, ensure_ascii=False).encode("utf-8")).hexdigest()


def clean_plan(workdir: str | Path, *, kind: str | None = None, data: Path | None = None) -> dict:
    """（唯一來源，§4.1）這個工作區結案要清哪些檔。只讀。
    回 {檔案: [{相對路徑, 大小, 分類, 說明, 路徑（只有工作區外那支影片）}], 分類: [{分類, 檔數, 大小}], 合計, 清單指紋, 含影片副本}。
    合計＝真的會空出來的位元組：硬連結用 (st_dev, st_ino) 去重（跟 `dir_size(seen)` 同一套），跟留下來的檔同一個 inode 的不算。"""
    import stat as _stat

    w = Path(workdir)
    if kind is None:
        from bookclub import fileio

        kind = fileio.platform_kind()
    rows: list[dict] = []
    keys: list[tuple] = []
    others: set = set()
    for root, dirs, files in os.walk(w, followlinks=False):
        if Path(root) == w and CLEAN_DIR in dirs:
            dirs.remove(CLEAN_DIR)
        dirs.sort()
        for f in sorted(files):
            fp = os.path.join(root, f)
            try:
                st = os.lstat(fp)
            except OSError:
                continue
            if not _stat.S_ISREG(st.st_mode):   # 捷徑（symlink）一律跳過：不刪捷徑、也不刪它指到的東西
                continue
            rel = Path(fp).relative_to(w).as_posix()
            verdict, cat = classify(rel)
            if verdict == "清":
                rows.append({"相對路徑": rel, "大小": st.st_size, "分類": cat, "說明": CAT_NOTES[cat]})
                keys.append((st.st_dev, st.st_ino))
            else:
                others.add((st.st_dev, st.st_ino))
    seen = set(others)
    total = 0
    by: dict[str, list[int]] = {}
    for r, k in zip(rows, keys):
        n = 0 if k in seen else r["大小"]
        seen.add(k)
        total += n
        c = by.setdefault(r["分類"], [0, 0])
        c[0] += 1
        c[1] += n
    extra = _video_copy_row(w, kind, data)
    if extra:
        rows.append(extra)
        total += extra["大小"]
        by[CAT_COPY] = [1, extra["大小"]]
    rows.sort(key=lambda r: r["相對路徑"])
    cats = [{"分類": c, "檔數": v[0], "大小": v[1]} for c, v in sorted(by.items(), key=lambda x: -x[1][1])]
    return {"檔案": rows, "分類": cats, "合計": total, "清單指紋": _fingerprint(rows), "含影片副本": extra is not None}


def _verified_pack(rec: dict) -> dict | None:
    return next((p for p in reversed(rec.get("打包") or []) if p.get("核對") == "通過"), None)


def clean_check(workdir: str | Path, fingerprint: str, *, busy=None, kind: str | None = None,
                data: Path | None = None) -> tuple[dict, dict]:
    """§4.3 清之前再確認（照順序）：①最近一次核對通過的打包、含成品 ②那一份的每個檔在、大小對（不重算檢查碼）
    ③清單指紋跟現在重算的一樣 ④第 4 步、輸出、複製都沒在跑（busy() 回原因或 None）。沒過丟 CleanError；過了回 (清單, 打包)。只讀。"""
    w = Path(workdir)
    rec = read_record(w)
    st = rec.get("狀態")
    if st == CLOSED:
        raise CleanError("這支影片已結案了，沒有東西可以清。")
    if st == CLEANING:
        raise CleanError("上次清到一半，按「繼續清」。")
    ok = _verified_pack(rec)
    if ok is None:
        raise CleanError("還沒存好（每個檔都核對過）不能清，先按「結案打包…」。")
    if ok.get("含成品") is not True or ok.get("只有紀錄"):
        raise CleanError(NO_PRODUCT)
    # 審查必修 A：不只信旗標——那一份裡真的要有影片檔；而且最後一次輸出的成品要在那一份裡
    # （存好之後又輸出了新的，清掉就會刪掉沒存過的最新成品）
    saved = {Path(str(f.get("相對路徑") or "")).name for f in ok.get("檔案") or []}
    if not any(n.lower().endswith(OUT_VIDEO_EXTS) for n in saved):
        raise CleanError(NO_PRODUCT)
    exp = last_export(w)
    if exp and Path(str(exp["檔案"])).name not in saved:
        raise CleanError(NEW_EXPORT)
    folder = Path(ok.get("資料夾") or "")
    try:
        if not ok.get("資料夾") or not folder.is_dir() or not ok.get("檔案"):
            raise CleanError(PACK_GONE)
        for f in ok["檔案"]:
            p = folder / f["相對路徑"]
            if p.is_symlink() or not p.is_file() or p.stat().st_size != int(f["大小"]):
                raise CleanError(PACK_GONE)
    except OSError:
        raise CleanError(PACK_GONE) from None
    plan = clean_plan(w, kind=kind, data=data)
    if plan["清單指紋"] != fingerprint:
        raise CleanError(PLAN_CHANGED)
    why = busy() if busy else None
    if why:
        raise CleanError(why)
    tmp = w / CLEAN_DIR
    if tmp.exists() or tmp.is_symlink():   # 審查必修 B：不是這一次建的（或是捷徑）——不往裡面搬、也不整個刪
        print(f"⚠️ [結案清理] {tmp} 已經存在（{'捷徑' if tmp.is_symlink() else '資料夾'}），不清。請將錯誤訊息通知開發者。")
        raise CleanError(TMP_THERE)
    return plan, ok


def clean(workdir: str | Path, fingerprint: str, *, busy=None, kind: str | None = None, data: Path | None = None,
          hook=None) -> dict:
    """清空間（§4.4）：再確認 → `結案.json` 先寫「清理中」＋整份清單 → 搬 → 刪 → 寫已結案。回 {省下位元組}。
    hook(階段, 相對路徑)：測試用（"搬" 每個檔之前、"搬好" 全部搬完、"刪" 刪之前）。"""
    w = Path(workdir)
    plan, ok = clean_check(w, fingerprint, busy=busy, kind=kind, data=data)
    rec = read_record(w)
    rec["狀態"] = CLEANING
    rec["清理"] = {"清單指紋": plan["清單指紋"], "開始": _now(), "省下位元組": plan["合計"],
                 "打包資料夾": ok.get("資料夾"), "打包時間": ok.get("時間"),
                 "匯入資料夾": _import_dir(data) if plan["含影片副本"] else None,
                 "清單": [{k: r[k] for k in ("相對路徑", "大小", "分類", "路徑") if k in r} for r in plan["檔案"]]}
    _write_record(w, rec)   # 先寫再動手
    return _finish(w, hook)


def clean_resume(workdir: str | Path, hook=None) -> dict:
    """「繼續清」：照 `結案.json` 記的清單把還在原位的搬完、再刪（清單是上次確認過的，不重算、不再問）。"""
    w = Path(workdir)
    rec = read_record(w)
    if rec.get("狀態") != CLEANING:
        raise CleanError("沒有清到一半的東西。")
    paused_check(rec)
    return _finish(w, hook)


PAUSED = "這支影片的清理暫停了，先不要清。請將錯誤訊息通知開發者。"


def paused_check(rec: dict) -> None:
    """第二輪專審：開發者 --restore 只搬回一部分（`清理.搬回一部分`）＝暫停中，不能繼續清
    （會把剛搬回去的檔和原位的新檔一起刪掉）。"""
    if (rec.get("清理") or {}).get("搬回一部分"):
        print("⚠️ [結案清理] 清理被 --restore 暫停（有檔原位已經有同名的，留在 .結案清理中/）：比對後移走其中一邊，"
              "再跑一次 bookclub close --restore <工作區>；全部搬回後才能重新清")
        raise CleanError(PAUSED)


def _import_dir(data: Path | None) -> str:
    if data is None:
        from bookclub.config import data_dir

        data = data_dir()
    return str((Path(data) / "影片").resolve())


def _outside_ok(row: dict, cl: dict) -> bool:
    """審查建議 3、4：工作區外那一列（影片副本）真的要動之前再看一次——上一層是記下的 `<資料>/影片`、影片副檔名、
    不是捷徑、大小跟記下的一樣。不符合就不搬、不刪（印出來）。"""
    from bookclub.fileio import VIDEO_EXTS

    p = Path(str(row.get("路徑") or ""))
    try:
        ok = (bool(cl.get("匯入資料夾")) and p.name.lower().endswith(VIDEO_EXTS) and not p.is_symlink() and p.is_file()
              and str(p.parent.resolve()) == str(Path(cl["匯入資料夾"]).resolve()) and p.stat().st_size == int(row["大小"]))
    except (OSError, ValueError, KeyError):
        ok = False
    if not ok and p.exists():
        print(f"⚠️ [結案清理] 影片副本跟記下的不一樣（位置、種類或大小），不動：{p}")
    return ok


def _inside_real(p: Path, w: Path) -> bool:
    """審查建議 2：這個檔所在的資料夾（照實際位置，跟著捷徑走）還在工作區裡面。"""
    rw, rp = os.path.realpath(w), os.path.realpath(p.parent)
    return rp == rw or rp.startswith(rw + os.sep)


def _src_of(w: Path, row: dict) -> Path:
    return Path(row["路徑"]) if row.get("路徑") else w / row["相對路徑"]


def _prune_empty(dirs: set[Path], stop: Path) -> None:
    """清完（或搬回去）之後，這些資料夾空了就收掉（只收空的；從最深的開始；不收 stop 本身）。"""
    for d in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
        while d != stop and stop in d.parents:
            try:
                d.rmdir()
            except OSError:
                break
            d = d.parent


def _finish(w: Path, hook=None) -> dict:
    rec = read_record(w)
    cl = rec["清理"]
    tmp = w / CLEAN_DIR
    if tmp.is_symlink():   # 審查必修 B：清理資料夾變成捷徑了——不往裡面搬、也不刪它指到的東西
        print(f"⚠️ [結案清理] {tmp} 是捷徑，停下來。請將錯誤訊息通知開發者。")
        raise CleanError(TMP_THERE)
    later: list[dict] = []   # 不在同一顆磁碟、搬不進來的影片副本：到「刪」那一步（再確認一次）直接刪
    if not cl.get("刪除開始"):
        parents: set[Path] = set()
        for row in cl.get("清單") or []:
            if hook:
                hook("搬", row["相對路徑"])
            src = _src_of(w, row)
            if row.get("路徑"):
                if not _outside_ok(row, cl):
                    continue
            elif classify(row["相對路徑"])[0] != "清" or not _inside_real(src, w):   # 再保險：規則不認得、跑到工作區外的不動
                continue
            if src.is_symlink() or not src.is_file():   # 搬過了、或本來就不在：略過
                continue
            dst = tmp / row["相對路徑"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.rename(src, dst)
            except OSError as e:
                if e.errno == errno.EXDEV and row.get("路徑"):
                    later.append(row)
                    continue
                raise
            if not row.get("路徑"):
                parents.add(src.parent)
        _prune_empty(parents, w)
        cl["搬好"] = _now()
        _write_record(w, rec)
        if hook:
            hook("搬好", None)
        cl["刪除開始"] = _now()
        _write_record(w, rec)
    else:
        later = [r for r in cl.get("清單") or [] if r.get("路徑") and _src_of(w, r).is_file()]
    if hook:
        hook("刪", None)
    if tmp.is_symlink():
        raise CleanError(TMP_THERE)
    if tmp.is_dir():
        shutil.rmtree(tmp)
    for row in later:
        if _outside_ok(row, cl):   # 審查建議 4：刪之前再比一次大小、位置
            _src_of(w, row).unlink()
    rec = read_record(w)
    cl = rec["清理"]
    cl["完成"] = _now()
    rec["狀態"] = CLOSED
    _write_record(w, rec)
    try:   # §4.2：版本表每一版 `影片還在` 改 false、歷史記一條「結案」（只有數字）
        from bookclub import needs

        needs.mark_closed(w, int(cl.get("省下位元組") or 0), len(cl.get("清單") or []))
    except Exception as e:  # noqa: BLE001 — 版本表記不下來不影響已經清好
        print(f"⚠️ [結案清理] 版本表沒有改到（影片還在）：{type(e).__name__}")
    return {"省下位元組": int(cl.get("省下位元組") or 0)}


def restore(workdir: str | Path) -> int:
    """`bookclub close --restore`（只給開發者）：「刪」開始前，把 `.結案清理中/` 的東西搬回原位，`結案.json` 回到「已打包」。
    原位已經有同名的檔不蓋掉（留在 `.結案清理中/`，印出來）。回搬回幾個。"""
    w = Path(workdir)
    rec = read_record(w)
    if rec.get("狀態") != CLEANING:
        raise CleanError("不是清到一半的工作區，沒有東西可以搬回去。")
    cl = rec.get("清理") or {}
    if cl.get("刪除開始"):
        raise CleanError("已經開始刪了，搬不回去（照「繼續清」清完）。")
    tmp = w / CLEAN_DIR
    n, left = 0, []
    parents: set[Path] = set()
    for row in cl.get("清單") or []:
        src = tmp / row["相對路徑"]
        if src.is_symlink() or not src.is_file():
            continue
        dst = _src_of(w, row)
        if dst.exists() or dst.is_symlink():
            left.append(row["相對路徑"])
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, dst)
        parents.add(src.parent)
        n += 1
    _prune_empty(parents, w)
    try:
        tmp.rmdir()
    except OSError:
        pass
    if left:   # 審查必修 B：有東西留下——狀態留「清理中」（不退回已打包），請開發者先手動處理
        print(f"⚠️ 原位已經有同名的檔，這 {len(left)} 個留在 {CLEAN_DIR}/：" + "、".join(left[:10]))
        cl["搬回一部分"] = _now()
        _write_record(w, rec)
        raise CleanError(f"搬回 {n} 個；有 {len(left)} 個檔原位已經有同名的檔，留在 {CLEAN_DIR}/。請手動比對後移走其中一邊，"
                         f"再跑一次 --restore。請將錯誤訊息通知開發者。")
    cl["搬回"] = _now()
    cl.pop("搬回一部分", None)   # 全部搬回了：暫停解除
    rec["狀態"] = PACKED if _verified_pack(rec) else PACKING
    _write_record(w, rec)
    return n


def reopen(workdir: str | Path) -> Path:
    """`bookclub close --reopen`（§1a 問題 2：不給老師「取消結案」，開發者手動）：`結案.json` 改名成
    `結案_取消_<時間>.json`。清到一半（`.結案清理中/` 還在）不行：先 --restore 或繼續清完。回改好的路徑。"""
    w = Path(workdir)
    p = record_path(w)
    if not p.is_file():
        raise CleanError("這個工作區沒有 結案.json，不用取消。")
    if read_record(w).get("狀態") == CLEANING and (w / CLEAN_DIR).exists():
        raise CleanError("清到一半：先用 --restore 搬回去，或在網頁按「繼續清」清完再取消。")
    new = w / f"結案_取消_{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    os.rename(p, new)
    return new


def closed_info(workdir: str | Path) -> dict | None:
    """總覽、頂端那一句用（§5.3）：{日期, 位置顯示, 省下位元組, 狀態}；沒結案＝None。只讀。"""
    w = Path(workdir)
    if not closed(w):
        return None
    rec = read_record(w)
    cl = rec.get("清理") or {}
    when = cl.get("完成") or cl.get("開始") or ""
    day = f"{when[5:7]}-{when[8:10]}" if len(when) >= 10 else ""
    return {"日期": day, "位置顯示": windows_display(cl.get("打包資料夾") or ""), "省下位元組": cl.get("省下位元組"),
            "狀態": rec.get("狀態")}


class CleanJob:
    """背景清一次（網頁看得到「正在清…」）。不能停（§4.4）。resume＝「繼續清」。"""

    def __init__(self, workdir: Path, fingerprint: str | None = None, *, resume: bool = False, busy=None):
        self.workdir = Path(workdir)
        self.fingerprint, self.resume, self._busy = fingerprint, resume, busy
        self.state = "清理中"
        self.error: str | None = None
        self.saved: int | None = None
        self.purpose = "結案清理"
        self.thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.state == "清理中"

    def cancel(self) -> None:   # 清理開始後不給停止
        pass

    def start(self) -> "CleanJob":
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        return self

    def run(self) -> None:
        try:
            r = clean_resume(self.workdir) if self.resume else clean(self.workdir, self.fingerprint, busy=self._busy)
            self.saved, self.state = r["省下位元組"], CLOSED
        except CleanError as e:
            self.state, self.error = "失敗", str(e)
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ [結案清理] {type(e).__name__}：{e}")
            self.state, self.error = "失敗", CLEAN_FAILED

    def to_dict(self) -> dict:
        return {"用途": self.purpose, "狀態": self.state, "錯誤": self.error, "省下位元組": self.saved,
                "第幾個": 0, "共幾個": 0, "檔名": None, "位置顯示": None, "資料夾": None, "清理錯誤": None}


def dry_run_lines(workdir: str | Path) -> list[str]:
    """`bookclub close --dry-run <工作區>`（給宇軒查「看每一個檔」）：會清哪些檔、各多大、合計、清單指紋。只讀，不刪。"""
    plan = clean_plan(workdir)
    lines = [f"會清的檔（{len(plan['檔案'])} 個；只看、不刪）："]
    for r in plan["檔案"]:
        lines.append(f"  {r['相對路徑']}\t{human(r['大小'])}\t{r['分類']}" + (f"\t{r['路徑']}" if r.get("路徑") else ""))
    lines.append("分類：" + "、".join(f"{c['分類']} {c['檔數']} 個 {human(c['大小'])}" for c in plan["分類"]))
    lines.append(f"合計（同一個檔不重複算）：{human(plan['合計'])}（{plan['合計']} 位元組）")
    lines.append(f"清單指紋：{plan['清單指紋']}")
    st = state(workdir)
    if st:
        lines.append(f"結案.json 狀態：{st}")
    return lines
