"""接力交接（33 號設計，#257–#260）：老師 → 夥伴 → 老師，用一個 zip（交接包）交棒。

- 交接打包（#257 甲）：第 3 步（老師交給夥伴）、第 5 步（夥伴交回老師）。zip 裡是 `工作區/<相對路徑>` 的紀錄、參考音
  （第 5 步再加目前的成品影片與選定的生成聲音），加一份 `交接清單.json`（工具版本、哪一段、哪台電腦（系統＋本機代號）、匯出時間、原片檔名／長度／大小、
  每個檔的 SHA-256 與原始修改時間、聲線檔指紋）。不帶原片與快取。寫 zip 用暫存名、寫完換名、再從目的地重讀逐檔核對。
- 這一集的設定快照（#257、#259 丙）：`交接/設定快照/名冊.csv`、`敏感詞.csv`、`發音對照表.csv`——只放這一集用得到的列。
  工作區有快照時，第 4 步、老師句子最後一道保險、代號、發音對照表都讀快照（`setting_path`），沒有就照舊讀本機。
- 匯入交接包（#258 乙）：逐檔核對 → 原片比對 → 聲線比對（不同只提醒）→ 這台沒有這一集就建新；已有就先看交接後有沒有動過
  （有就列出、問要不要繼續）→ 只備份紀錄到 `備份/交接前_<日期>/` → 換成包裡的 → 還原修改時間 → 換原片路徑 → 歷史記一筆。
- 清理（#260 丁）：第 3 步交接打包後清這一集的暫存與 Ubuntu 裡的原片複本（`copy_clean_plan`／`clean_copy`）；
  第 5 步交出去之後整集清掉（`episode_clean_info`／`clean_episode`）。原片只清工具自己複製進 Ubuntu 的那份（WSL），
  Mac 上的原片絕對不碰。

本機的交接紀錄在 `交接/交接紀錄.json`（不放進交接包）。畫面與說明文字一律寫「雲端空間」。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat as _stat
import threading
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from bookclub import workdir as wd

FORMAT_VERSION = 1
LIST_NAME = "交接清單.json"
README_NAME = "說明.txt"
ZIP_ROOT = "工作區"
DIR_NAME = "交接"
RECORD_NAME = "交接紀錄.json"
SNAP_DIR = "設定快照"
SETTING_FILES = ("名冊.csv", "敏感詞.csv", "發音對照表.csv")
# 快照只留這幾欄（名冊不會帶到聯絡資料之類的欄位）
SETTING_COLUMNS = {"名冊.csv": ("中文名", "其他寫法", "英文代號", "聲線", "性別"),
                   "敏感詞.csv": ("原詞", "替代詞"),
                   "發音對照表.csv": ("原字", "生成用", "原因", "建立日期")}
STAGE3, STAGE5 = "第3步", "第5步"
STAGES = (STAGE3, STAGE5)
TMP_SUFFIX = ".寫入中"
STAGING_DIR = ".交接匯入中"
BACKUP_DIR = "備份"
BACKUP_PREFIX = "交接前_"
HASH_CHUNK = 8 * 1024 * 1024
DURATION_TOLERANCE_S = 1.0
PACK_MARGIN_BYTES = 200_000_000
# 比「交接後有沒有動過」時不算的欄位（打開第 3 步就會累加的計時）
VOLATILE_KEYS = {"覆核/覆核決定.json": ("覆核秒數",)}
# 「交接後有沒有動過」只比人做的判斷（審查 M2）：第 3 步的決定、代號、學員聲線、參考音與底噪的選擇、第 5 步的通過／退回、
# 修改需求的卡片／版本／成品狀態、第 4 步做出的生成紀錄與處理紀錄、工作區設定。其他一律不比，例如：
# `修改需求/格式.json`（打開第 3、5 步就會同步）、`修改需求/看過.json`、`修改需求/歷史.jsonl`、`修改需求/生成句.json`、
# 升級產物（`修改需求/升級前備份_*`、升級報告）、`覆核/成品檢查.json` 裡的看過區段與量長度欄位、`transcript/`、
# `說話者判斷.json`、`分析結果.json`、`報告.md`、`輸出/` 的紀錄、設定快照、`學員名字候選.json`（打開第 3 步自動找）、
# `校對/代號表紀錄.json`（代號對齊時自動記的上一份代號表）（這些是分析結果或工具自己寫的）。
JUDGED_FILES = ("校對/段落.json", "名字覆核決定.json", "覆核/覆核決定.json", "學員名字覆核決定.json", "名字候選.json",
                "校對/刪除建議.json", "重疊.json",          # needs.STEP3_FILES 扣掉打開第 3 步就會自動產生的「學員名字候選.json」
                "校對/人名決定.json", "生成/學員聲線.json",   # 代號自動配.json 進第 3 步就會寫（三審），不比
                "參考音/ref.wav", "參考音/ref.txt", "參考音/底噪.json", "工作區設定.json",
                "覆核/成品檢查.json", "修改需求/卡片.json", "修改需求/版本.json", "修改需求/成品狀態.json",
                "生成/老師紀錄.json", "生成/學員紀錄.json", "生成/保留原聲學員紀錄.json", "生成/處理紀錄.json")
# 成品檢查只比人按的：逐筆通過／退回、整片退回、未登記確認（打開第 5 步才建的空檔＝沒有）
KEEP_KEYS = {"覆核/成品檢查.json": ("逐筆", "整片退回", "未登記確認")}
MACHINE_FILE = "本機代號.json"   # 宇軒 10-20：不存主機名，存系統類別＋這台第一次用時隨機產生的短代號（在本機資料資料夾）
# 交接包不帶、匯入也不換的資料夾（最上層）
SKIP_TOP = ("輸出", BACKUP_DIR, "匯出", DIR_NAME, STAGING_DIR)
# 匯入後只改內容、修改時間不還原的檔（歷史剛記了一筆）
HISTORY_FILES = ("修改需求/歷史.jsonl", "修改需求/格式.json")

NOT_READY = "交接包還沒下載完（或檔案壞了），請等雲端空間同步好再試。"
NOT_PACKAGE = "這不是交接包（裡面沒有交接清單）。請選從雲端空間下載的交接 zip。"
UPLOAD_LINE = "已存成 {}，請上傳到雲端空間。"


class HandoffError(Exception):
    """交接打包、匯入、清理不能做（訊息是給人看的一句）。使用者自己可以改正的（選錯原片、選錯檔、打錯位置⋯）用這個：
    畫面留在原處顯示紅字，讓人改了再試（宇軒 10-23）。"""


class HandoffProblem(HandoffError):
    """意外的錯誤（zip 壞掉或沒下載完、寫檔失敗、空間不夠、工作資料夾裡有奇怪的捷徑⋯使用者自己改不了的）：
    畫面跳錯誤視窗（宇軒 10-23）。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(HASH_CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def machine_label() -> str:
    """這台電腦給人看的名字：「Mac・K3F9」「Windows・7QXA」。代號第一次用時隨機產生，存在 `~/讀書會剪輯資料/本機代號.json`，
    之後固定（宇軒 10-20：不存主機名）。"""
    import secrets

    from bookclub import fileio
    from bookclub.config import data_dir

    kind = {"mac": "Mac", "wsl": "Windows"}.get(fileio.platform_kind(), "Linux")
    p = data_dir() / MACHINE_FILE
    code = None
    try:
        code = (wd.read_json(p, default=None) or {}).get("代號")
    except (OSError, ValueError):
        code = None
    if not code:
        code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(4))
        try:
            wd.write_json(p, {"代號": code})
        except OSError:
            pass
    return f"{kind}・{code}"


def tool_version() -> str:
    from bookclub import __version__

    return __version__


# ---------------------------------------------------------------------------
# 本機的交接紀錄（`交接/交接紀錄.json`）
# ---------------------------------------------------------------------------

def record_path(workdir: str | Path) -> Path:
    return Path(workdir) / DIR_NAME / RECORD_NAME


def read_record(workdir: str | Path) -> dict:
    try:
        data = wd.read_json(record_path(workdir), default=None)
    except (OSError, ValueError):
        data = None
    return data if isinstance(data, dict) else {}


def _write_record(workdir: str | Path, data: dict) -> None:
    wd.write_json(record_path(workdir), {**data, "程式版本": tool_version()})


def last_export(workdir: str | Path, stage: str | None = None) -> dict | None:
    """最近一次核對通過的交接打包（stage 給了就只看那一段）。"""
    for e in reversed(read_record(workdir).get("交出") or []):
        if e.get("核對") == "通過" and (stage is None or e.get("哪一段") == stage):
            return e
    return None


def handed_over(workdir: str | Path) -> str | None:
    """「已交出去」的時間（第 5 步交接打包成功）；沒有＝None。之後又匯入過就不算。"""
    return read_record(workdir).get("已交出去") or None


def source_workdirs(workdir: str | Path) -> list[Path]:
    """匯入過的交接包記的「對方的工作區位置」（`wd.recorded_workdirs` 用：對方紀錄裡的完整路徑換成這裡的）。"""
    out = []
    for e in read_record(workdir).get("匯入") or []:
        v = e.get("來源工作區")
        if v and Path(v) not in out:
            out.append(Path(v))
    return out


# ---------------------------------------------------------------------------
# 設定快照（#259 丙）
# ---------------------------------------------------------------------------

def snapshot_dir(workdir: str | Path) -> Path:
    return Path(workdir) / DIR_NAME / SNAP_DIR


def setting_path(workdir: str | Path | None, name: str) -> Path:
    """這一集要讀的設定檔：工作區有設定快照就讀快照，沒有就照舊讀本機 `~/讀書會剪輯資料/<name>`（舊工作區行為不變）。"""
    from bookclub.config import data_dir

    if workdir is not None:
        p = snapshot_dir(workdir) / name
        if p.is_file():
            return p
    return data_dir() / name


def setting_paths(workdir: str | Path | None, name: str) -> list[Path]:
    """最後一道隱私保險用（審查 M4）：快照＋本機兩份都讀、合起來（快照在前）。快照只是讓第 4 步不因這台名冊不完整而變，
    不能讓保險變弱：本機名冊、敏感詞有的照樣換。沒有快照＝只有本機。"""
    from bookclub.config import data_dir

    first = setting_path(workdir, name)
    local = data_dir() / name
    return [first] if first == local else [first, local]


def has_snapshot(workdir: str | Path) -> bool:
    return all((snapshot_dir(workdir) / n).is_file() for n in SETTING_FILES)


def _strings(x, out: list[str]) -> None:
    if isinstance(x, str):
        out.append(x)
    elif isinstance(x, dict):
        for k, v in x.items():
            out.append(str(k))
            _strings(v, out)
    elif isinstance(x, list):
        for v in x:
            _strings(v, out)


# #272：工具自己寫、內容來自整份名冊的檔（名冊有「英文代號」欄時，代號表紀錄記了名冊每一位有代號的人）。
# 不拿來判斷「這一集用到誰」（不然整份名冊都算用到）；交接包裡也只留這一集的人（`_codes_record`）。
ROSTER_DERIVED = ("校對/代號表紀錄.json",)


def _corpus(workdir: Path) -> str:
    """這一集的文字（逐字稿、段落、名字候選、決定、代號⋯）接在一起：設定檔的某一列「用不用得到」看它的字有沒有出現在這裡。
    JSON 檔把裡面的字串（含鍵）拿出來（不管存檔時中文有沒有轉義），其他文字檔照原文。
    不看設定快照、本機交接紀錄、`ROSTER_DERIVED`（內容來自整份名冊，不是這一集真的出現的字）。"""
    parts: list[str] = []
    for rel, fp in record_files(workdir):
        suf = PurePosixPath(rel).suffix.lower()
        if rel.startswith(f"{DIR_NAME}/") or rel in ROSTER_DERIVED or suf not in (".json", ".jsonl", ".txt", ".csv", ".md"):
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if suf in (".json", ".jsonl"):
            lines = text.splitlines() if suf == ".jsonl" else [text]
            for line in lines:
                try:
                    _strings(json.loads(line), parts)
                except ValueError:
                    parts.append(line)
        else:
            parts.append(text)
    return "\n".join(parts)


def _split_alts(s: str) -> list[str]:
    import re

    return [x.strip() for x in re.split(r"[、,，/]", s or "") if x.strip()]


def _csv_bytes(header: list[str], rows: list[dict]) -> bytes:
    import csv
    import io

    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(header)
    for r in rows:
        w.writerow([(r.get(h) or "") for h in header])
    return ("﻿" + buf.getvalue()).encode("utf-8")   # UTF-8 加 BOM：Excel 打得開，`csvfile` 讀得懂


def build_snapshot(workdir: str | Path, corpus: str | None = None) -> dict[str, bytes]:
    """這一集的設定快照（三份 CSV 的內容）：名冊只留這一集出現的人（本名或其他寫法出現在這一集的文字裡）、
    敏感詞只留這一集碰到的、發音對照表只留這一集用得到的（原字出現在這一集的文字、代號或敏感詞的替代詞裡）。
    工作區已經有快照（匯入過）就照用那一份（不從這台的設定重算）。讀不到的設定檔當作空的。"""
    from bookclub import csvfile
    from bookclub.config import data_dir

    w = Path(workdir)
    if has_snapshot(w):
        return {n: (snapshot_dir(w) / n).read_bytes() for n in SETTING_FILES}
    if corpus is None:
        corpus = _corpus(w)
    out: dict[str, bytes] = {}
    extra: list[str] = []
    for name in SETTING_FILES:
        src = data_dir() / name
        try:
            rows = csvfile.read_rows(src) if src.is_file() else []
        except (OSError, ValueError):
            rows = []
        have = list(rows[0].keys()) if rows else []
        header = [c for c in SETTING_COLUMNS[name] if c in have] or [c for c in SETTING_COLUMNS[name]
                                                                     if name != "名冊.csv" or c != "英文代號"]
        keep = []
        for r in rows:
            r = {k: (v or "").strip() for k, v in r.items() if k}
            if name == "名冊.csv":
                spells = [r.get("中文名", "")] + _split_alts(r.get("其他寫法", ""))
                hit = any(s and s in corpus for s in spells)
                if hit and r.get("英文代號"):
                    extra.append(r["英文代號"])
            elif name == "敏感詞.csv":
                hit = bool(r.get("原詞")) and r["原詞"] in corpus
                if hit and r.get("替代詞"):
                    extra.append(r["替代詞"])
            else:
                key = r.get("原字", "")
                hit = bool(key) and (key in corpus or any(key in x for x in extra))
            if hit:
                keep.append(r)
        out[name] = _csv_bytes(header, keep)
    return out


def _codes_record(workdir: Path, roster_csv: bytes, corpus: str | None = None) -> bytes | None:
    """#272：交接包裡的 `校對/代號表紀錄.json` 只留這一集的人——本名在這一集的名冊快照裡，或名字出現在這一集的文字裡
    （③ 自己決定換成代號、名冊上沒有的名字）。名冊上其他人（只因為名冊有英文代號欄才記進來的）拿掉。
    匯入那台用快照名冊算出來的代號表跟這份一樣，打開第 3 步不會當成「代號改了」。讀不懂＝None（照原檔帶）。"""
    import csv
    import io

    try:
        data = wd.read_json(workdir / ROSTER_DERIVED[0], default=None)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    keep = {(r.get("中文名") or "").strip() for r in csv.DictReader(io.StringIO(roster_csv.decode("utf-8-sig")))}
    text = _corpus(workdir) if corpus is None else corpus
    out = {k: v for k, v in data.items() if k and (k in keep or k in text)}
    return (json.dumps(out, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def snapshot_counts(snap: dict[str, bytes]) -> dict[str, int]:
    import csv
    import io

    out = {}
    for n, b in snap.items():
        out[n.removesuffix(".csv")] = max(0, len(list(csv.reader(io.StringIO(b.decode("utf-8-sig"))))) - 1)
    return out


# ---------------------------------------------------------------------------
# 交接包裡有什麼（#257 甲）
# ---------------------------------------------------------------------------

def _ref_audio(parts: tuple, name: str) -> bool:
    """參考音裡要帶的聲音：老師參考音 `ref.wav`、全片底噪 `底噪*.wav`（候選、舊候選備份不帶）。"""
    low = name.lower()
    return len(parts) == 2 and parts[0] == "參考音" and low.endswith(".wav") and (name == "ref.wav" or name.startswith("底噪"))


def record_files(workdir: str | Path) -> list[tuple[str, Path]]:
    """兩種交接包都帶的檔（工作區相對路徑 posix, 完整路徑）：人做的判斷、分析紀錄（文字類的檔，跟結案打包同一套規則：
    快取資料夾、清空間會清的暫存、超過 50 MB 的不帶）、`修改需求/` 整份、參考音、`輸出/` 裡的處理標記等紀錄、設定快照。
    不帶：原片、`audio.flac`、`web快取/` 等快取、`輸出/` 的影片與聲音、`備份/`、`匯出/`、本機的交接紀錄。只讀。"""
    from bookclub import closing, needs

    w = Path(workdir)
    skip_dirs = set(closing.CACHE_DIRS) | {needs.STAGING, closing.CLEAN_DIR} | set(SKIP_TOP)
    out: list[tuple[str, Path]] = []
    for rel, fp, size in closing._workdir_files(w):
        parts = rel.parts
        posix = rel.as_posix()
        top = parts[0]
        if len(parts) > 1 and top == DIR_NAME and parts[1] == SNAP_DIR:
            out.append((posix, fp))
            continue
        if len(parts) > 1 and top in skip_dirs:
            continue
        if top == "修改需求" and len(parts) > 1:
            out.append((posix, fp))
            continue
        if _ref_audio(parts, parts[-1]):
            out.append((posix, fp))
            continue
        if fp.suffix.lower() not in closing.PACK_TEXT_EXTS or size > closing.PACK_RECORD_MAX_BYTES:
            continue
        if closing.classify(posix)[0] == "清":
            continue
        out.append((posix, fp))
    outdir = w / "輸出"
    if outdir.is_dir():
        for pat in closing.OUTPUT_RECORD_GLOBS:
            for p in sorted(outdir.glob(pat)):
                if p.is_file() and not p.is_symlink():
                    out.append((f"輸出/{p.name}", p))
    return out


def current_products(workdir: str | Path) -> list[str]:
    """第 5 步的包要帶的成品（相對工作區）：最近一次組裝做出的成品（`finalcheck.log_products`，不含標字版）；
    推不出來就用第 5 步正在檢查的那一支。"""
    from bookclub import finalcheck, proclog

    w = Path(workdir)
    try:
        prods = finalcheck.log_products(w, proclog.load(w)) or []
    except Exception:  # noqa: BLE001 — 推不出來：用正在檢查的那一支
        prods = []
    if not prods:
        cur = (finalcheck.load_check(w) or {}).get("成品影片")
        prods = [cur] if cur else []
    return [r for r in prods if (w / r).is_file() and not (w / r).is_symlink()]


def pack_items(workdir: str | Path, stage: str) -> list[dict]:
    """要放進交接包的檔：[{相對路徑, 來源 或 內容}]。第 5 步的包另外帶目前的成品影片與選定的生成聲音；
    還沒有成品（沒組裝過）丟 HandoffError。設定快照（工作區還沒有的話）現場產生。"""
    from bookclub import closing

    if stage not in STAGES:
        raise HandoffError(f"不認得是哪一段：{stage}")
    w = Path(workdir)
    if not wd.merged_transcript_path(w).is_file() or not (w / "校對" / "段落.json").is_file():
        raise HandoffError("第 1 步影片分析還沒做完，還不能交接打包。")
    items = [{"相對路徑": rel, "來源": fp} for rel, fp in record_files(w)]
    have = {it["相對路徑"] for it in items}
    corpus = _corpus(w)   # 審查 C：快照與代號表紀錄共用，只算一次
    snap = build_snapshot(w, corpus)
    if not has_snapshot(w):
        for name, data in snap.items():
            rel = f"{DIR_NAME}/{SNAP_DIR}/{name}"
            if rel not in have:
                items.append({"相對路徑": rel, "內容": data})
                have.add(rel)
    # #272：內容來自整份名冊的檔只帶這一集的人（修改時間照原檔，匯入後不會被當成比成品新）
    for it in items:
        if it["相對路徑"] in ROSTER_DERIVED and it.get("來源") is not None:
            src: Path = it["來源"]
            st = src.stat()
            data = _codes_record(w, snap["名冊.csv"], corpus)
            if data is not None:
                it["修改時間"] = st.st_mtime_ns
                it["內容"] = data
                it["原檔"] = (src, (st.st_size, st.st_mtime_ns, st.st_ino))   # 審查 B：寫進包時再核對一次
                del it["來源"]
    if stage == STAGE5:
        prods = current_products(w)
        if not prods:
            raise HandoffError("還沒有成品：先到第 4 步組裝一次，再交接打包。")
        for rel in prods:
            if rel not in have:
                items.append({"相對路徑": rel, "來源": w / rel, "成品": True})
                have.add(rel)
        for p in closing.voice_files(w):
            rel = p.relative_to(w).as_posix()
            if rel not in have:
                items.append({"相對路徑": rel, "來源": p, "生成聲音": True})
                have.add(rel)
    return items


def items_size(items: list[dict]) -> int:
    n = 0
    for it in items:
        if it.get("內容") is not None:
            n += len(it["內容"])
        else:
            try:
                n += it["來源"].stat().st_size
            except OSError:
                pass
    return n


def video_info(workdir: str | Path) -> dict:
    """交接清單的原片欄：檔名、長度（分析結果記的）、大小（找得到原片才有）。"""
    w = Path(workdir)
    ana = wd.read_json(wd.analysis_result_path(w), default=None) or {}
    v = wd.find_video(w)
    name = v.name if v else (Path(str(ana.get("video"))).name if ana.get("video") else None)
    size = None
    if v is not None:
        try:
            size = v.stat().st_size
        except OSError:
            size = None
    dur = ana.get("影片長度")
    return {"檔名": name, "長度": float(dur) if isinstance(dur, (int, float)) else None, "大小": size}


def voice_prints(base: Path | None = None) -> dict[str, str]:
    """這台的學員匿名聲線檔指紋 {檔名: 指紋}（候選聲線＋暫定聲線；兩邊是同一套檔案，匯入時比對，不一樣只提醒）。"""
    from bookclub import students, tts

    base = Path(base) if base else students.voice_dir()
    out: dict[str, str] = {}
    cand = base / students.CANDIDATE_DIR
    files = sorted(cand.glob("*.wav")) if cand.is_dir() else []
    files += [p for p in (base / "男聲_暫定.wav", base / "女聲_暫定.wav") if p.is_file()]
    for p in files:
        fp = tts.file_fingerprint(p)
        if fp:
            out[p.relative_to(base).as_posix()] = fp
    return out


def voice_diff(theirs: dict | None, mine: dict | None = None) -> list[str]:
    """（純函式為主）兩邊聲線檔不一樣的檔名（對方有、這台沒有或內容不同）。"""
    mine = voice_prints() if mine is None else mine
    return sorted(k for k, v in (theirs or {}).items() if mine.get(k) != v)


def norm_print(rel: str, path: Path) -> str | None:
    """比「交接後有沒有動過」用的指紋：檔案內容的 SHA-256；`覆核決定.json` 不算計時欄位。"""
    try:
        keys = VOLATILE_KEYS.get(rel)
        if keys:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = {k: v for k, v in data.items() if k not in keys}
            return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        return _sha_file(path)
    except (OSError, ValueError):
        return None


CHANGE_LABELS = (   # 確認視窗用的白話分類（不列檔名；宇軒 10-22）
    (("校對/段落.json",), "第 3 步的段落與校對稿"),
    (("名字覆核決定.json", "學員名字覆核決定.json", "名字候選.json", "校對/人名決定.json"), "第 3 步名字的處理與代號"),
    (("覆核/覆核決定.json", "校對/刪除建議.json", "重疊.json"), "第 3 步的覆核決定（刪除、重疊、消音）"),
    (("生成/學員聲線.json",), "第 3 步的學員聲線"),
    (("參考音/",), "第 2 步的老師參考音或底噪"),
    (("生成/",), "第 4 步的 AI 修改紀錄"),
    (("覆核/成品檢查.json",), "第 5 步的通過／退回"),
    (("修改需求/",), "第 5 步的修改需求卡片"),
    (("工作區設定.json",), "片頭片尾設定"),
)


def change_kinds(changes: list[str]) -> list[str]:
    """（純函式）改過的檔 → 白話分類（照固定順序、不重複）。"""
    rels = [c.split("（")[0] for c in changes]
    out = []
    for keys, label in CHANGE_LABELS:
        if any(r == k or (k.endswith("/") and r.startswith(k)) for r in rels for k in keys) and label not in out:
            out.append(label)
    return out or (["其他紀錄"] if changes else [])


def judged_print(rel: str, data: bytes | None) -> str | None:
    """人做的判斷那幾個檔的指紋（data＝檔案內容；None＝檔案不在）。`覆核決定.json` 不算計時欄位、`成品檢查.json`
    只看人按的那幾欄（不在時當作空的，打開第 5 步才建的不算改過）。"""
    keep, drop = KEEP_KEYS.get(rel), VOLATILE_KEYS.get(rel)
    if data is None:
        if not keep:
            return None
        data = b"{}"
    if keep or drop:
        try:
            obj = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return hashlib.sha256(data).hexdigest()
        if isinstance(obj, dict):
            if keep:
                obj = {k: obj.get(k) or ({} if k != "整片退回" else []) for k in keep}
            else:
                obj = {k: v for k, v in obj.items() if k not in drop}
        return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return hashlib.sha256(data).hexdigest()


def record_prints(workdir: str | Path) -> dict[str, str]:
    """這個工作區現在「人做的判斷」的指紋 {相對路徑: 指紋}（只有 JUDGED_FILES 那幾個）。"""
    w = Path(workdir)
    out = {}
    for rel in JUDGED_FILES:
        p = w / rel
        try:
            data = p.read_bytes() if p.is_file() and not p.is_symlink() else None
        except OSError:
            data = None
        fp = judged_print(rel, data)
        if fp:
            out[rel] = fp
    return out


def changed_since(old: dict, now: dict) -> list[str]:
    """（純函式）兩份紀錄指紋比對：改過、多出來、不見的檔（相對路徑，排好）。"""
    out = []
    for rel in sorted(set(old) | set(now)):
        if rel not in now:
            out.append(f"{rel}（不見了）")
        elif rel not in old:
            out.append(f"{rel}（新的）")
        elif old[rel] != now[rel]:
            out.append(rel)
    return out


def changes_after_export(workdir: str | Path) -> list[str] | None:
    """這台這一集在最近一次交接打包之後有沒有動過：回改過的檔（空的＝沒動過）；沒有交接打包紀錄回 None。"""
    e = last_export(workdir)
    if not e or not isinstance(e.get("紀錄指紋"), dict):
        return None
    return _changes(e["紀錄指紋"], record_prints(workdir))


def _changes(old: dict, now: dict) -> list[str]:
    """交接時記下的 vs 現在的。交接時還沒有修改需求紀錄（舊工作區、之後才升級）→ 修改需求那幾個不比。"""
    old = {k: v for k, v in old.items() if k in JUDGED_FILES}
    if not any(k.startswith("修改需求/") for k in old):
        now = {k: v for k, v in now.items() if not k.startswith("修改需求/")}
    return changed_since(old, now)


def zip_name(workdir: str | Path, stage: str, now: datetime | None = None) -> str:
    from bookclub import closing

    now = now or datetime.now()
    return f"{closing.video_stem(Path(workdir))}_交接{stage}_{now.strftime('%Y%m%d-%H%M')}.zip"


def _numbered_file(path: Path) -> Path:
    if not path.exists() and not path.is_symlink():
        return path
    for n in range(2, 1000):
        cand = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not cand.exists() and not cand.is_symlink():
            return cand
    raise HandoffError(f"{path.parent} 裡同名的檔太多了，先整理一下再試。")


def check_location(workdir: str | Path, location: str | Path, need: int, usage=None) -> Path:
    """存交接包的位置能不能用：在、是資料夾、不在這一集的工作資料夾裡、空間夠（要 need＋200 MB）。"""
    from bookclub import fileio

    w = Path(workdir).resolve()
    loc = Path(location).expanduser()
    if not loc.is_dir():
        raise HandoffError(f"找不到這個資料夾：{location}，換個位置再試。")
    loc = loc.resolve()
    if loc == w or w in loc.parents:
        raise HandoffError("不能存在這支影片的工作資料夾裡，請選別的地方（例如雲端空間或下載）。")
    why = fileio.space_problem(need, loc, PACK_MARGIN_BYTES, "存的地方", usage=usage)
    if why:
        raise HandoffError(f"空間不夠：要 {need / 1e9:.1f} GB，換個位置或清出空間再試。")
    return loc


def readme_text(manifest: dict) -> str:
    v = manifest.get("原片") or {}
    lines = ["這是讀書會剪輯工具的交接包。",
             "不要自己解壓縮：在工具裡按「匯入交接包…」，選這個 zip，再選原片。",
             f"原片：{v.get('檔名') or '（不知道）'}（原片沒有放在包裡，請另外從雲端空間下載）",
             f"哪一段：{manifest.get('哪一段')}；匯出時間：{manifest.get('匯出時間')}",
             "裡面有學員的本名和分享內容，不要公開或傳給不相關的人。"]
    return "\r\n".join(lines) + "\r\n"


class PackJob:
    """背景交接打包一次（網頁看得到第幾個檔、可以停）。只寫目的地的 zip 與 `交接/交接紀錄.json`。"""

    def __init__(self, workdir: Path, location: Path, stage: str, *, display: str | None = None, fail_hook=None):
        self.workdir, self.location, self.stage = Path(workdir), Path(location), stage
        self.display = display or str(location)
        self.state = "等待"   # 等待／存檔中／核對中／已存好／停止／失敗
        self.error: str | None = None
        self.index, self.total = 0, 0
        self.current: str | None = None
        self.dest: Path | None = None
        self.size: int | None = None
        self._cancel = threading.Event()
        self._fail_hook = fail_hook   # 測試用：fail_hook(zip 路徑) → 把存好的 zip 改壞
        self.thread: threading.Thread | None = None
        self.purpose = "交接打包"
        self.unexpected = False

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
        from bookclub import closing

        name = self.dest.name if self.dest else None
        return {"用途": self.purpose, "哪一段": self.stage, "狀態": self.state, "第幾個": self.index, "共幾個": self.total,
                "檔名": name, "錯誤": self.error, "位置顯示": self.display, "大小": self.size,
                "位置": closing.windows_display(self.dest.parent) if self.dest else None,
                "說明": UPLOAD_LINE.format(name) if self.state == "已存好" and name else None, "意外": self.unexpected}

    def run(self) -> None:
        from bookclub import closing

        try:
            self._run()
        except HandoffError as e:
            self.state, self.error = ("停止" if self._cancel.is_set() else "失敗"), str(e)
            self.unexpected = isinstance(e, HandoffProblem) and not self._cancel.is_set()
        except OSError as e:
            self.unexpected = True
            print(f"⚠️ [交接打包] {type(e).__name__}：{e}")
            self.state, self.error = "失敗", closing.os_message(e).replace("再按「繼續」", "再試一次")
        except Exception as e:  # noqa: BLE001 — 背景執行緒要把失敗記下來給網頁看
            self.unexpected = True
            print(f"⚠️ [交接打包] {type(e).__name__}：{e}")
            self.state, self.error = "失敗", "存的時候出了問題，換個位置再試；還是不行，請將錯誤訊息通知開發者。"

    def _run(self) -> None:
        w = self.workdir
        items = pack_items(w, self.stage)
        self.total = len(items)
        self.state = "存檔中"
        dest = _numbered_file(self.location / zip_name(w, self.stage))
        tmp = dest.with_name(f".{dest.name}{TMP_SUFFIX}")
        self.dest = dest
        files: list[dict] = []
        self.judged: dict[str, str] = {}
        try:
            with zipfile.ZipFile(tmp, "w", allowZip64=True) as zf:
                for i, it in enumerate(items, start=1):
                    if self._cancel.is_set():
                        raise HandoffError("停下來了：這次沒有存，要交接再按一次「交接打包…」。")
                    self.index, self.current = i, it["相對路徑"]
                    files.append(self._add(zf, it))
                rec0 = read_record(w)
                wid = rec0.get("工作區ID") or uuid.uuid4().hex[:12]
                prev = (rec0.get("匯入") or [{}])[-1].get("交接編號")
                manifest = {"格式版本": FORMAT_VERSION, "工具版本": tool_version(), "哪一段": self.stage,
                            "電腦": machine_label(), "匯出時間": _now(), "交接編號": uuid.uuid4().hex[:12],
                            "工作區ID": wid, "上一棒交接編號": prev,
                            "工作區名稱": w.name, "工作區": str(w), "原片": video_info(w), "檔案": files,
                            "聲線": voice_prints(),
                            "設定快照": snapshot_counts({n: self._snap_bytes(items, n) for n in SETTING_FILES})}
                zf.writestr(LIST_NAME, json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"))
                zf.writestr(README_NAME, readme_text(manifest).encode("utf-8"))
            with open(tmp, "rb+") as f:
                os.fsync(f.fileno())
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                tmp.unlink()   # 只清自己寫的暫存檔
        if self._fail_hook:
            self._fail_hook(dest)
        self.state = "核對中"
        try:
            verify_zip(dest, cancel=self._cancel)
        except HandoffError:
            dest.unlink(missing_ok=True)   # 存過去不一樣：刪掉自己剛存的這一份
            raise HandoffProblem("存過去的交接包核對不對，換個位置再試。") from None
        self.size = dest.stat().st_size
        rec = read_record(w)
        rec["工作區ID"] = manifest["工作區ID"]
        # 審查 M1：交接時的樣子＝包裡實際放的內容（打包時算的），不是打包完再重讀工作區
        judged = {rel: self.judged[rel] for rel in JUDGED_FILES if rel in self.judged}
        for rel in JUDGED_FILES:
            if rel not in judged and rel in KEEP_KEYS:
                judged[rel] = judged_print(rel, None)
        rec.setdefault("交出", []).append({
            "時間": _now(), "哪一段": self.stage, "檔名": dest.name, "路徑": str(dest), "位置顯示": self.display,
            "大小": self.size, "交接編號": manifest["交接編號"], "核對": "通過", "紀錄指紋": judged})
        if self.stage == STAGE5:
            rec["已交出去"] = _now()
        _write_record(w, rec)
        self.state = "已存好"

    @staticmethod
    def _snap_bytes(items: list[dict], name: str) -> bytes:
        rel = f"{DIR_NAME}/{SNAP_DIR}/{name}"
        for it in items:
            if it["相對路徑"] == rel:
                return it["內容"] if it.get("內容") is not None else it["來源"].read_bytes()
        return b""

    def _add(self, zf: zipfile.ZipFile, it: dict) -> dict:
        rel = it["相對路徑"]
        arc = f"{ZIP_ROOT}/{rel}"
        data = it.get("內容")
        if it.get("原檔"):   # 審查 B：先讀好、換過內容的檔（代號表紀錄）：讀完到寫進包之間原檔變了就停，跟其他檔同一套
            src0, before0 = it["原檔"]
            try:
                st0 = src0.stat()
                now0 = (st0.st_size, st0.st_mtime_ns, st0.st_ino)
            except OSError:
                now0 = None
            if now0 != before0:
                raise HandoffError(f"存的時候工作區裡的檔案變了（{rel}），等它做完再按一次。")
        if data is not None:
            mtime_ns = it.get("修改時間") or time.time_ns()
            zi = zipfile.ZipInfo(arc, date_time=time.localtime(mtime_ns / 1e9)[:6])
            zi.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(zi, data)
            return {"路徑": rel, "大小": len(data), "sha256": hashlib.sha256(data).hexdigest(), "修改時間": mtime_ns}
        src: Path = it["來源"]
        st = src.stat()
        before = (st.st_size, st.st_mtime_ns, st.st_ino)
        if rel in JUDGED_FILES and st.st_size < 50_000_000:   # 人做的判斷（小檔）：用寫進包裡的同一份內容算指紋
            data = src.read_bytes()
            st2 = src.stat()
            if (st2.st_size, st2.st_mtime_ns, st2.st_ino) != before or len(data) != before[0]:
                raise HandoffError(f"存的時候工作區裡的檔案變了（{rel}），等它做完再按一次。")
            zi = zipfile.ZipInfo.from_file(src, arc, strict_timestamps=False)
            zi.compress_type = zipfile.ZIP_STORED if src.suffix.lower() == ".wav" else zipfile.ZIP_DEFLATED
            zf.writestr(zi, data)
            self.judged[rel] = judged_print(rel, data)
            return {"路徑": rel, "大小": len(data), "sha256": hashlib.sha256(data).hexdigest(), "修改時間": before[1]}
        zi = zipfile.ZipInfo.from_file(src, arc, strict_timestamps=False)
        big = src.suffix.lower() in (".mp4", ".mov", ".m4v", ".mkv", ".wav", ".flac")
        zi.compress_type = zipfile.ZIP_STORED if big else zipfile.ZIP_DEFLATED
        h = hashlib.sha256()
        n = 0
        with open(src, "rb") as fi, zf.open(zi, "w", force_zip64=True) as fo:
            while True:
                if self._cancel.is_set():
                    raise HandoffError("停下來了：這次沒有存，要交接再按一次「交接打包…」。")
                b = fi.read(HASH_CHUNK)
                if not b:
                    break
                fo.write(b)
                h.update(b)
                n += len(b)
        st2 = src.stat()
        if (st2.st_size, st2.st_mtime_ns, st2.st_ino) != before or n != before[0]:
            raise HandoffError(f"存的時候工作區裡的檔案變了（{rel}），等它做完再按一次。")
        return {"路徑": rel, "大小": n, "sha256": h.hexdigest(), "修改時間": before[1]}


# ---------------------------------------------------------------------------
# 讀交接包、核對（#258 乙）
# ---------------------------------------------------------------------------

def _safe_rel(rel: str) -> str:
    p = PurePosixPath(str(rel))
    if not str(rel) or p.is_absolute() or any(x in ("..", "", ".") for x in p.parts) or "\\" in str(rel):
        raise HandoffError(NOT_PACKAGE)
    if p.parts[0] in (STAGING_DIR, BACKUP_DIR) or p.parts[0].startswith("."):
        raise HandoffError(NOT_PACKAGE)
    if p.as_posix() in (f"{DIR_NAME}/{RECORD_NAME}", "結案.json"):   # 本機才有的檔，包裡不該有
        raise HandoffError(NOT_PACKAGE)
    return p.as_posix()


def read_manifest(zip_path: str | Path) -> dict:
    """只讀交接清單（很快；還不核對每個檔）。讀不懂、不是交接包丟 HandoffError。"""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            try:
                raw = zf.read(LIST_NAME)
            except KeyError:
                raise HandoffError(NOT_PACKAGE) from None
    except (zipfile.BadZipFile, OSError, EOFError):
        raise HandoffProblem(NOT_READY) from None
    try:
        m = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise HandoffProblem(NOT_READY) from None
    if not isinstance(m, dict) or not isinstance(m.get("檔案"), list):
        raise HandoffError(NOT_PACKAGE)
    if int(m.get("格式版本") or 0) > FORMAT_VERSION:
        raise HandoffError("這個交接包是比較新的工具做的，先把這台的工具更新到新版再匯入。")
    if m.get("哪一段") not in STAGES:
        raise HandoffError(NOT_PACKAGE)
    seen = set()
    for f in m["檔案"]:
        f["路徑"] = _safe_rel(f.get("路徑"))
        if f["路徑"] in seen:
            raise HandoffError(NOT_PACKAGE)
        seen.add(f["路徑"])
    return m


def summary(m: dict) -> dict:
    """給畫面看的交接包摘要。"""
    v = m.get("原片") or {}
    return {"哪一段": m.get("哪一段"), "匯出時間": m.get("匯出時間"),
            "電腦": str(m.get("電腦") or "").split("・")[0],   # 宇軒 10-20：畫面只寫 Mac／Windows（代號只存在清單與歷史）
            "工具版本": m.get("工具版本"),
            "原片檔名": v.get("檔名"), "原片長度": v.get("長度"), "原片大小": v.get("大小"),
            "檔數": len(m.get("檔案") or []), "有成品": m.get("哪一段") == STAGE5}


def verify_zip(zip_path: str | Path, *, cancel: threading.Event | None = None, progress=None) -> dict:
    """逐檔核對：清單上的每個檔都在、大小對、SHA-256 對。不對丟 HandoffError（還沒下載完）。回清單。"""
    m = read_manifest(zip_path)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            for i, f in enumerate(m["檔案"], start=1):
                if cancel is not None and cancel.is_set():
                    raise HandoffError("停下來了。")
                if progress:
                    progress(i, len(m["檔案"]), f["路徑"])
                arc = f"{ZIP_ROOT}/{f['路徑']}"
                if arc not in names:
                    raise HandoffProblem(NOT_READY)
                h = hashlib.sha256()
                n = 0
                with zf.open(arc) as fi:
                    while True:
                        b = fi.read(HASH_CHUNK)
                        if not b:
                            break
                        h.update(b)
                        n += len(b)
                if n != int(f["大小"]) or h.hexdigest() != f["sha256"]:
                    raise HandoffProblem(NOT_READY)
    except (zipfile.BadZipFile, OSError, EOFError, KeyError, ValueError):
        raise HandoffProblem(NOT_READY) from None
    return m


def probe_duration(video: Path) -> float | None:
    from bookclub import finalcheck

    try:
        got = float(finalcheck.probe_duration(Path(video)))   # 10-23：匯出覆核結果刪了，量長度改用第 5 步同一支
        return got if got > 0 else None
    except Exception:  # noqa: BLE001 — 量不到就不比長度
        return None


def check_video(m: dict, video: str | Path, probe=None) -> None:
    """選的原片跟交接清單記的是不是同一支：大小（記了才比）一樣、長度差 1 秒內。不是丟 HandoffError。"""
    v = m.get("原片") or {}
    p = Path(video)
    if not p.is_file():
        raise HandoffError(f"找不到選的原片：{p.name}")
    want_name = v.get("檔名") or "交接清單記的原片"
    size = v.get("大小")
    if isinstance(size, int) and p.stat().st_size != size:
        raise HandoffError(f"選的原片跟交接包記的不是同一支（大小不一樣）。請選從雲端空間下載的「{want_name}」。")
    dur = v.get("長度")
    if isinstance(dur, (int, float)):
        got = (probe or probe_duration)(p)
        if got is not None and abs(float(got) - float(dur)) > DURATION_TOLERANCE_S:
            raise HandoffError(f"選的原片跟交接包記的不是同一支（長度差 {abs(float(got) - float(dur)):.0f} 秒）。"
                               f"請選從雲端空間下載的「{want_name}」。")


def target_for(video: str | Path) -> Path:
    """這支原片的工作區位置（跟「選影片 → 開始分析」同一套：影片旁邊的 `<檔名>_剪輯工作區/`）。"""
    from bookclub.server import project_dir_for

    return project_dir_for(Path(video))


def is_existing(target: Path) -> bool:
    return target.is_dir() and wd.analysis_result_path(target).is_file()


def find_targets(m: dict, video: str | Path, candidates: list[Path] | None = None) -> tuple[list[Path], str]:
    """這個交接包要匯入哪一份工作區（審查 M3：不靠原片放在哪）。回 (符合的工作區, 怎麼找到的)：
    1. 這台的工作區（專案清單上的）有交出過「上一棒交接編號」那個包 →「上一棒」
    2. 有同一個工作區 ID、而且不是交出這個包本身的那一份 →「工作區ID」
    3. 都沒有：原片旁邊的 `<檔名>_剪輯工作區/`（跟「選影片」同一套）→「原片旁邊」
    1、2 符合的不只一份時全部回（二審 N5：讓人選）。"""
    prev, wid, me = m.get("上一棒交接編號"), m.get("工作區ID"), m.get("交接編號")
    cands = []
    for d in candidates or []:
        try:
            if Path(d).is_dir() and not Path(d).is_symlink() and is_existing(Path(d)) and Path(d) not in cands:
                cands.append(Path(d))
        except OSError:
            continue
    if prev:
        hit = [d for d in cands if any(e.get("交接編號") == prev and e.get("核對") == "通過"
                                       for e in read_record(d).get("交出") or [])]
        if hit:
            return hit, "上一棒"
    if wid:
        hit = []
        for d in cands:
            rec = read_record(d)
            if rec.get("工作區ID") == wid and not any(e.get("交接編號") == me for e in rec.get("交出") or []):
                hit.append(d)
        if hit:
            return hit, "工作區ID"
    t = target_for(video)
    for d in cands:   # 三審 4b：Mac 路徑不分大小寫，用 samefile 比，回專案清單上記的那個寫法
        if wd.same_path(d, t):
            return [d], "原片旁邊"
    return [t], "原片旁邊"


def find_target(m: dict, video: str | Path, candidates: list[Path] | None = None) -> Path:
    return find_targets(m, video, candidates)[0][0]


def own_package(m: dict, candidates: list[Path] | None) -> Path | None:
    """這台專案清單上交出過這個包的那一份工作區（同一個工作區 ID、交出紀錄有這個交接編號）；沒有回 None。"""
    for d in candidates or []:
        try:
            if not Path(d).is_dir():
                continue
            rec = read_record(d)
        except OSError:
            continue
        if rec.get("工作區ID") == m.get("工作區ID") and any(
                e.get("交接編號") == m.get("交接編號") for e in rec.get("交出") or []):
            return Path(d)
    return None


def export_after(workdir: str | Path, num: str | None) -> tuple[dict | None, list[dict]]:
    """這台交出過的「num 那一包」，和它之後又交出的（核對通過的）。找不到 num 回 (None, [])。"""
    out = [e for e in read_record(workdir).get("交出") or [] if e.get("核對") == "通過"]
    for i, e in enumerate(out):
        if num and e.get("交接編號") == num:
            return e, out[i + 1:]
    return None, []


def _short_time(t: str | None) -> str:
    t = str(t or "")
    return f"{t[5:7]}-{t[8:10]} {t[11:16]}" if len(t) >= 16 else t


def _registered() -> list[Path]:
    try:
        from bookclub.server import registered_projects

        return registered_projects()
    except Exception:  # noqa: BLE001 — 專案清單讀不到：只看原片旁邊
        return []


# ---------------------------------------------------------------------------
# 匯入（#258 乙）
# ---------------------------------------------------------------------------

class ImportJob:
    """背景匯入一次。狀態：等待／核對中／比對原片／匯入中／要確認／完成／失敗。
    要確認＝要先問人的情況（`_ask`：這台交接之後又改過、比較舊的包、已經匯入過、找不到原本那份、有好幾份、自己交出的第 3 步包⋯），
    確認內容是一句重點＋一行說明＋白話分類，不列檔名；`confirm=True` 再開一次才換。
    人選了某一份或打了位置（target）：先確認工作區 ID 跟包一樣，再照常跑一次 `_ask`（總審 T1、T2），不會因為選了就跳過比對。"""

    def __init__(self, zip_path: Path, video: Path, *, confirm: bool = False, probe=None, extract_audio=None,
                 target: Path | None = None, candidates: list[Path] | None = None):
        self.zip_path, self.video = Path(zip_path), Path(video)
        self.confirm = confirm
        self._probe, self._extract = probe, extract_audio
        self.target = Path(target) if target else None
        self.candidates = candidates
        self.resolved: Path | None = None   # 實際要匯入（換掉）的工作區（伺服器擋寫入用）
        self.state = "等待"
        self.error: str | None = None
        self.ask: dict | None = None
        self.result: dict | None = None
        self.index, self.total = 0, 0
        self._cancel = threading.Event()
        self.thread: threading.Thread | None = None
        self.purpose = "匯入交接包"
        self.id = uuid.uuid4().hex[:12]
        self.unexpected = False   # 失敗的是不是意外的錯誤（畫面跳錯誤視窗）；使用者可以改正的留在原處紅字
        self.on_done = None   # 伺服器：匯入好了登記專案、切過去

    @property
    def running(self) -> bool:
        return self.state in ("等待", "核對中", "比對原片", "匯入中")

    def cancel(self) -> None:
        self._cancel.set()

    def start(self) -> "ImportJob":
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        return self

    def to_dict(self) -> dict:
        return {"用途": self.purpose, "編號": self.id, "狀態": self.state, "錯誤": self.error, "意外": self.unexpected,
                "要確認": self.ask, "結果": self.result, "第幾個": self.index, "共幾個": self.total}

    def run(self) -> None:
        try:
            self.result = self._run()
            if self.result is not None:
                if self.on_done:
                    try:
                        self.on_done(self)
                    except Exception as e:  # noqa: BLE001 — 已經匯入好了；只是沒切過去
                        print(f"⚠️ [匯入交接包] 匯入好了，但沒有切到這一集：{type(e).__name__}：{e}")
                        self.result.setdefault("提醒", []).append("匯入好了，但沒有自動切過去：到總覽「已有的專案」按「切換」。")
                self.state = "完成"
        except HandoffError as e:
            self.state, self.error = "失敗", str(e)
            self.unexpected = isinstance(e, HandoffProblem)
        except OSError as e:
            import errno

            self.unexpected = True
            print(f"⚠️ [匯入交接包] {type(e).__name__}：{e}")
            self.state, self.error = "失敗", ("空間不夠了：清出空間再匯入一次（這台的紀錄沒有被換掉）。" if e.errno == errno.ENOSPC
                                            else "匯入的時候出了問題（這台的紀錄沒有被換掉）；請將錯誤訊息通知開發者。")
        except Exception as e:  # noqa: BLE001
            self.unexpected = True
            print(f"⚠️ [匯入交接包] {type(e).__name__}：{e}")
            self.state, self.error = "失敗", "匯入的時候出了問題；請將錯誤訊息通知開發者。"

    def _progress(self, i: int, n: int, _rel: str) -> None:
        self.index, self.total = i, n

    def _ask(self, m: dict, targets: list[Path], how: str, existing: bool) -> dict | None:
        """要不要先問（回確認內容或 None）。宇軒 10-22：每種情況「粗體一句重點＋一行說明」，不列檔名。
        回 {重點, 說明, 改過類別, 工作區, 工作區名稱, 選項, 可以另建, 可以指定, 建議不匯入, 只能不匯入}。"""
        target = targets[0]
        base = {"改過類別": [], "工作區": str(target), "工作區名稱": target.name, "選項": [], "可以另建": False,
                "可以指定": False, "建議不匯入": False, "只能不匯入": False,
                "已指定": how == "指定"}   # 總審 T1：人選／打的那一份，確認時要再帶回來

        def ask(head: str, line: str, **kw) -> dict:
            return {**base, "重點": head, "說明": line, **kw}

        prev = m.get("上一棒交接編號")
        if len(targets) > 1:
            return ask("這台有好幾份工作區都是這一集", "請選要換掉哪一份，其他的不會動。",
                       選項=[str(t) for t in targets])
        if not existing:
            if m.get("哪一段") == STAGE5 and prev and how == "原片旁邊":
                return ask("找不到你當初交出去的那一份工作區",
                           "可能改過名或搬過。可以另外建一份，或在下面打原本那一份工作資料夾的位置。",
                           可以另建=True, 可以指定=True)
            return None
        rec = read_record(target)
        imports = [e for e in rec.get("匯入") or [] if e.get("時間")]
        same = next((e for e in reversed(imports) if e.get("交接編號") and e.get("交接編號") == m.get("交接編號")), None)
        if same:
            return ask("這個交接包已經匯入過了", f"{_short_time(same.get('時間'))} 已經匯入過這一包，通常不用再匯入。",
                       建議不匯入=True)
        olds = [e for e in imports if e.get("哪一段") == m.get("哪一段") and e.get("匯出時間")]
        if olds and str(olds[-1]["匯出時間"]) > str(m.get("匯出時間") or ""):
            return ask("這是比較舊的交接包",
                       f"你在 {_short_time(olds[-1].get('時間'))} 已經匯入過比較新的版本，通常不用再匯入這包。",
                       建議不匯入=True)
        if rec.get("工作區ID") and m.get("工作區ID") and rec["工作區ID"] != m["工作區ID"]:
            return ask("這台的這一集是另一份工作區",
                       "不是這個交接包那一份。繼續匯入會換成包裡的版本，這台原本的會先備份起來。")
        exp, later = export_after(target, prev)
        if exp is None:
            exp = last_export(target)
        # 三審：比對基準拿「上一棒那次交出」和「最近一次匯入」比較晚的那一筆（匯入過之後，夥伴的內容不算老師改的）
        cands = [e for e in ([exp] if exp else []) + imports[-1:] if isinstance(e.get("紀錄指紋"), dict)]
        if not cands:
            return ask("看不出來這台交出去之後有沒有改過", "這台的這一集沒有交接打包過的紀錄。繼續匯入會換成包裡的版本，原本的會先備份起來。")
        ref = max(cands, key=lambda e: (str(e.get("時間") or ""), e is not exp))   # 同一秒：匯入在交出之後
        if later and ref is exp:
            return ask("這包是照你比較早交出去的那一份做的",
                       f"你在 {_short_time(later[-1].get('時間'))} 又交接過一次。繼續匯入會換成夥伴的版本，你後來改的部分會先備份起來，不會出現在新版本裡。",
                       改過類別=change_kinds(_changes(ref["紀錄指紋"], record_prints(target))))
        changes = _changes(ref["紀錄指紋"], record_prints(target))
        if changes:
            return ask("你交接之後，這台又改過這一集",
                       "繼續匯入會換成夥伴的版本，你後來改的部分會先備份起來，不會出現在新版本裡。",
                       改過類別=change_kinds(changes))
        return None

    def _run(self) -> dict | None:
        from bookclub import closing

        # 1. 逐檔核對
        self.state = "核對中"
        m = verify_zip(self.zip_path, cancel=self._cancel, progress=self._progress)
        # 2. 原片比對
        self.state = "比對原片"
        check_video(m, self.video, self._probe)
        # 3. 聲線比對（不擋）
        voices = voice_diff(m.get("聲線"))
        # 4. 這台有沒有這一集
        if self.target:
            targets, how = [self.target], "指定"
        else:
            targets, how = find_targets(m, self.video, self.candidates if self.candidates is not None else _registered())
        if m.get("哪一段") == STAGE3 and how != "指定":
            mine = own_package(m, self.candidates if self.candidates is not None else _registered())
            if mine is not None:   # 三審 4a：這台自己交出去的第 3 步包
                self.state = "要確認"
                self.ask = {"重點": "這是這台自己交出去的交接包", "說明": "不用在這台匯入；這個包是要給夥伴的。",
                            "改過類別": [], "工作區": str(mine), "工作區名稱": mine.name, "選項": [],
                            "可以另建": False, "可以指定": False, "建議不匯入": True, "只能不匯入": True}
                self.resolved = mine
                return None
        target = targets[0]
        self.resolved = target
        if how == "指定":   # 總審 T1：人選或打的位置，工作區 ID 要跟包一樣（是同一集、交出這一包的那一份）
            rid = read_record(target).get("工作區ID") if target.is_dir() else None
            if not m.get("工作區ID") or rid != m.get("工作區ID"):
                raise HandoffError("這不是同一集的工作區：選的位置不是當初交出這個交接包的那一份，沒有匯入。")
        if target.is_symlink():
            raise HandoffProblem("這一集的工作資料夾是捷徑，不能匯入；先不要動。請將錯誤訊息通知開發者。")
        existing = is_existing(target)
        if existing and closing.closed(target):
            raise HandoffProblem("這支影片在這台已經結案了，不能匯入。請將錯誤訊息通知開發者。")
        if existing and any(e.get("交接編號") == m.get("交接編號") for e in read_record(target).get("交出") or []):
            raise HandoffError("這個交接包是這台交出去的，不用在這台匯入。")
        space_check(target, m)   # 審查 S4：開始搬之前先看空間
        if not self.confirm:
            ask = self._ask(m, targets, how, existing)
            if ask:
                self.state = "要確認"
                self.ask = ask
                return None
        self.state = "匯入中"
        return import_into(target, self.zip_path, m, self.video, existing=existing, voices=voices,
                           extract_audio=self._extract)


def _extract(zip_path: Path, m: dict, staging: Path) -> None:
    """把清單上的檔解到暫存資料夾（一邊解一邊算 SHA-256；不對就停）。"""
    with zipfile.ZipFile(zip_path) as zf:
        for f in m["檔案"]:
            dst = staging / f["路徑"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            h = hashlib.sha256()
            with zf.open(f"{ZIP_ROOT}/{f['路徑']}") as fi, open(dst, "wb") as fo:
                while True:
                    b = fi.read(HASH_CHUNK)
                    if not b:
                        break
                    fo.write(b)
                    h.update(b)
                fo.flush()
                os.fsync(fo.fileno())
            if h.hexdigest() != f["sha256"]:
                raise HandoffProblem(NOT_READY)


def _move_in(src: Path, dst: Path, root: Path) -> None:
    """搬一個檔進 root 裡的 dst。審查 S7：dst 所在的資料夾（照實際位置）要在 root 裡面，中間有捷徑跑到外面就停。"""
    from bookclub import closing

    dst.parent.mkdir(parents=True, exist_ok=True)
    if not closing._inside_real(dst, root):
        raise HandoffProblem(f"工作資料夾裡的「{dst.parent.relative_to(root)}」是捷徑，指到工作資料夾外面，不能匯入；先不要動。請將錯誤訊息通知開發者。")
    if dst.is_symlink():
        dst.unlink()   # 要換的是捷徑：拿掉捷徑本身，不寫到它指的地方
    os.replace(src, dst)


def space_check(target: Path, m: dict, usage=None) -> None:
    """匯入前看空間（審查 S4）：要解開的大小＋1 GB 餘裕（之後抽音軌、組裝要寫硬碟）。不夠丟 HandoffError（說要多少）。"""
    from bookclub import fileio

    need = sum(int(f.get("大小") or 0) for f in m.get("檔案") or [])
    where = target if target.is_dir() else next((p for p in target.parents if p.is_dir()), target.parent)
    why = fileio.space_problem(need, where, fileio.IMPORT_MARGIN_BYTES, "這台電腦", usage=usage,
                               also=fileio.wsl_host_disks())
    if why:
        raise HandoffProblem(f"空間不夠，沒有匯入：{why}")


def backup_dir_name(now: datetime | None = None) -> str:
    return f"{BACKUP_PREFIX}{(now or datetime.now()).strftime('%Y%m%d-%H%M%S')}"


def _rewrite_video(target: Path, video: Path) -> None:
    """原片路徑改成這台的位置（分析結果、逐字稿、參考音挑選紀錄）。工作區裡的其他完整路徑讀的時候換（`wd.localize`）。"""
    for p, k in ((wd.analysis_result_path(target), "video"), (wd.merged_transcript_path(target), "source"),
                 (wd.ref_dir(target) / "挑選紀錄.json", "video")):
        try:
            data = wd.read_json(p, default=None)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and k in data and data.get(k) != str(video):
            data[k] = str(video)
            wd.write_json(p, data)


def _log_history(target: Path, m: dict) -> bool:
    """`修改需求/歷史.jsonl` 記一筆「交接匯入」（從哪台、哪一段、什麼時候匯出的）。沒升級過的工作區不記。"""
    from bookclub import needs

    if not needs.exists(target):
        return False
    with needs._locked(target):
        st = needs.load(target)
        st.log(_now(), None, 5 if m.get("哪一段") == STAGE5 else 3, "交接匯入", None,
               {"電腦": m.get("電腦"), "哪一段": m.get("哪一段"), "匯出時間": m.get("匯出時間"),
                "交接編號": m.get("交接編號")})
        needs.save(target, st)
    return True


def import_into(target: Path, zip_path: Path, m: dict, video: Path, *, existing: bool, voices: list[str] | None = None,
                extract_audio=None) -> dict:
    """把交接包換進 target（已經核對過、原片比對過）。這台已有這一集：只把紀錄搬到 `備份/交接前_<日期>/`（快取不備份、
    `web快取/` 直接丟掉）再換成包裡的；出錯時把備份搬回去。回 {工作區, 去哪一步, 備份, 新建, 聲線不同, 提醒}。"""
    target = Path(target)
    if target.is_symlink():
        raise HandoffProblem("這一集的工作資料夾是捷徑，不能匯入；先不要動。請將錯誤訊息通知開發者。")
    created = not target.exists()
    target.mkdir(parents=True, exist_ok=True)
    staging = target / STAGING_DIR
    if staging.is_symlink():
        raise HandoffProblem("這一集的工作資料夾裡有一個奇怪的捷徑（.交接匯入中），先不要動。請將錯誤訊息通知開發者。")
    if staging.exists():
        shutil.rmtree(staging)   # 上次匯入到一半留下的（自己的暫存資料夾）
    staging.mkdir()
    backup_rel = None
    moved: list[str] = []     # 搬進備份的（原本這台的檔）
    placed: list[str] = []    # 從包裡搬進來的
    try:
        _extract(zip_path, m, staging)
        if existing:
            backup_rel = f"{BACKUP_DIR}/{backup_dir_name()}"
            bdir = target / backup_rel
            # 這台的紀錄全部備份；包裡要放進來的位置原本有檔的（成品、生成聲音⋯）也一起備份，失敗時才回得去
            olds = {rel for rel, _fp in record_files(target)} | {f["路徑"] for f in m["檔案"]}
            for rel in sorted(olds):
                fp = target / rel
                if fp.is_symlink() or not fp.is_file():
                    continue
                _move_in(fp, bdir / rel, target)
                moved.append(rel)
        for f in m["檔案"]:
            _move_in(staging / f["路徑"], target / f["路徑"], target)
            placed.append(f["路徑"])
        _rewrite_video(target, Path(video))
        _log_history(target, m)
        rec = read_record(target)
        rec.setdefault("匯入", []).append({
            "時間": _now(), "來自電腦": m.get("電腦"), "哪一段": m.get("哪一段"), "匯出時間": m.get("匯出時間"),
            "交接編號": m.get("交接編號"), "來源工作區": m.get("工作區"), "備份": backup_rel, "新建": not existing,
            "聲線不同": voices or []})
        if m.get("工作區ID"):
            rec["工作區ID"] = m["工作區ID"]
        rec.pop("已交出去", None)
        _write_record(target, rec)
    except BaseException:
        # 審查 S1：換到一半失敗——包裡搬進來的全部拿掉、備份搬回原位，這台回到匯入前的樣子
        for rel in placed:
            try:
                p = target / rel
                if p.is_file() and not p.is_symlink():
                    p.unlink()
            except OSError:
                print(f"⚠️ [匯入交接包] 拿不掉：{rel}")
        for rel in moved:
            try:
                _move_in(target / backup_rel / rel, target / rel, target)
            except (OSError, HandoffError):
                print(f"⚠️ [匯入交接包] 搬不回去：{rel}（在 {backup_rel}/ 裡）")
        if backup_rel:
            shutil.rmtree(target / backup_rel, ignore_errors=True)   # 都搬回去了：空的備份資料夾不留
        if created:
            shutil.rmtree(target, ignore_errors=True)   # 這次才建的資料夾整個拿掉
        raise
    finally:
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging, ignore_errors=True)
    cache = target / "web快取"
    if existing and cache.is_dir() and not cache.is_symlink():
        shutil.rmtree(cache, ignore_errors=True)   # 播放用的快取，用到再切（換成對方的成品後舊的對不上）
    # 修改時間照交接清單還原（Mac／Ubuntu 時區不同、解壓縮都會讓修改時間變，被誤判要重新組裝）
    for f in m["檔案"]:
        if f["路徑"] in HISTORY_FILES:
            continue
        p = target / f["路徑"]
        try:
            if isinstance(f.get("修改時間"), int) and p.is_file() and not p.is_symlink():
                os.utime(p, ns=(f["修改時間"], f["修改時間"]))
        except OSError:
            pass
    try:   # 三審：記下匯入後的樣子，之後再匯入時拿它比（夥伴的內容不算這台改的）
        rec = read_record(target)
        if rec.get("匯入"):
            rec["匯入"][-1]["紀錄指紋"] = record_prints(target)
            _write_record(target, rec)
    except OSError:
        pass
    notes = []
    if not wd.audio_path(target).is_file():
        try:
            (extract_audio or _ensure_audio)(Path(video), target)
        except Exception as e:  # noqa: BLE001 — 抽不出來不擋匯入，第 1 步可以補
            print(f"⚠️ [匯入交接包] 音軌沒有抽成功：{type(e).__name__}：{e}")
            notes.append("音軌沒有抽成功：到第 1 步按「重新分析（做完的會跳過）」補抽。")
    if voices:
        notes.append(f"這台的學員聲線檔跟對方的不一樣（{'、'.join(voices[:5])}{'⋯' if len(voices) > 5 else ''}），"
                     "用到的學員段落可能會被判定要重新生成。要一樣的話，先匯入老師最新的設定包。")
    return {"工作區": str(target), "去哪一步": "step5" if m.get("哪一段") == STAGE5 else "step4", "備份": backup_rel,
            "新建": not existing, "聲線不同": voices or [], "提醒": notes, "哪一段": m.get("哪一段")}


def _ensure_audio(video: Path, target: Path) -> None:
    from bookclub import refpick

    refpick.ensure_audio(video, target, log=lambda s: None)


# ---------------------------------------------------------------------------
# 清理（#260 丁）
# ---------------------------------------------------------------------------

def copy_clean_plan(workdir: str | Path, *, kind: str | None = None, data: Path | None = None) -> dict:
    """第 3 步交接打包之後可以清的（只讀）：這一集的暫存（`audio.flac`、`web快取/`、`chunks/`、`名字候選/`、`重疊/`
    裡的聲音片段；跟結案清空間「暫存」同一套規則，但參考音不清）＋ WSL 上工具複製進 Ubuntu、沒有別的專案在用的原片複本
    （Mac 的原片是原檔，不列）。回 {檔案, 合計, 清單指紋, 含原片複本}。判斷紀錄不在裡面。"""
    from bookclub import closing, fileio

    w = Path(workdir)
    kind = kind or fileio.platform_kind()
    rows = []
    for root, dirs, files in os.walk(w, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for f in sorted(files):
            fp = Path(root) / f
            try:
                st = os.lstat(fp)
            except OSError:
                continue
            if not _stat.S_ISREG(st.st_mode):
                continue
            rel = fp.relative_to(w).as_posix()
            verdict, cat = closing.classify(rel)
            if verdict == "清" and cat == closing.CAT_TMP and not rel.startswith(("參考音/", f"{BACKUP_DIR}/")):
                rows.append({"相對路徑": rel, "大小": st.st_size, "分類": cat})
    extra = closing._video_copy_row(w, kind, data)
    if extra:
        rows.append(extra)
    return {"檔案": rows, "合計": sum(r["大小"] for r in rows), "清單指紋": closing._fingerprint(rows),
            "含原片複本": extra is not None}


def clean_copy(workdir: str | Path, fingerprint: str, *, kind: str | None = None, data: Path | None = None,
               busy=None) -> dict:
    """第 3 步交接打包成功後「清掉（建議）」：三道保險——①這一集有核對通過的交接包 ②清單跟按下去那一刻一樣
    ③只刪認得的暫存（規則、在工作區裡、不是捷徑）與核對過的原片複本（在 `~/讀書會剪輯資料/影片/`、大小對、不是捷徑）。
    回 {省下位元組, 原片複本}。"""
    from bookclub import closing

    w = Path(workdir)
    if closing.closed(w):
        raise HandoffError("這支影片已結案。")
    if last_export(w) is None:
        raise HandoffError("還沒有存好的交接包，不能清。")
    plan = copy_clean_plan(w, kind=kind, data=data)
    if plan["清單指紋"] != fingerprint:
        raise HandoffError("剛剛這裡的檔案又有變動，重新整理後再按一次。")
    why = busy() if busy else None
    if why:
        raise HandoffError(why)
    saved = 0
    parents: set[Path] = set()
    cl = {"匯入資料夾": closing._import_dir(data)}
    copy_done = False
    for row in plan["檔案"]:
        if row.get("路徑"):
            if closing._outside_ok(row, cl):
                Path(row["路徑"]).unlink()
                saved += row["大小"]
                copy_done = True
            continue
        p = w / row["相對路徑"]
        if closing.classify(row["相對路徑"]) != ("清", closing.CAT_TMP) or not closing._inside_real(p, w) \
                or row["相對路徑"].startswith(("參考音/", f"{BACKUP_DIR}/")):
            continue
        if p.is_symlink() or not p.is_file():
            continue
        p.unlink()
        parents.add(p.parent)
        saved += row["大小"]
    closing._prune_empty(parents, w)
    rec = read_record(w)
    rec.setdefault("清掉暫存", []).append({"時間": _now(), "省下位元組": saved, "原片複本": copy_done})
    _write_record(w, rec)
    return {"省下位元組": saved, "原片複本": copy_done}


def imported_episode(workdir: str | Path) -> bool:
    """審查 S3：這一集是匯入第 3 步交接包建出來的（夥伴端）。老師自己第 1 步建的工作區不算。"""
    return any(e.get("哪一段") == STAGE3 and e.get("新建") for e in read_record(workdir).get("匯入") or [])


def episode_clean_info(workdir: str | Path, *, kind: str | None = None, data: Path | None = None) -> dict | None:
    """總覽「清掉這集」用（只讀）：已交出去的才有 → {已交出去, 大小, 含原片複本}；沒交出去回 None。"""
    from bookclub import closing, fileio

    w = Path(workdir)
    when = handed_over(w)
    if not when or not imported_episode(w):
        return None
    kind = kind or fileio.platform_kind()
    try:
        size = closing.dir_size(w)
    except OSError:
        size = 0
    extra = closing._video_copy_row(w, kind, data)
    return {"已交出去": when, "大小": size + (extra["大小"] if extra else 0), "含原片複本": extra is not None}


def episode_clean_check(workdir: str | Path, *, kind: str | None = None, data: Path | None = None) -> dict | None:
    """「清掉這集」之前再確認（照順序，沒過丟 HandoffError）：①已交出去（最近一次第 5 步交接包核對通過）、這一集是匯入
    第 3 步交接包建的（宇軒 10-20：不再看 zip 還在不在，改由確認視窗請人確認已上傳到雲端空間）②交出去之後人做的判斷沒有再改過 ③工作區是真的資料夾（不是捷徑）、名稱是「…_剪輯工作區」、有分析結果、沒結案。回原片複本那一列或 None。"""
    from bookclub import closing, fileio

    w = Path(workdir)
    kind = kind or fileio.platform_kind()
    e = last_export(w, STAGE5)
    if not handed_over(w) or e is None:
        raise HandoffError("這一集還沒交出去（第 5 步交接打包），不能整集清掉。")
    if not imported_episode(w):
        raise HandoffError("這一集是在這台從頭做的（不是匯入交接包來的），不能整集清掉。")
    if _changes(e.get("紀錄指紋") or {}, record_prints(w)):
        raise HandoffError("交出去之後這一集又改過了。要交回去的話先再按一次「交接打包…」，存好再清。")
    from bookclub.workdir import WORKSPACE_SUFFIX

    if w.is_symlink() or not w.is_dir() or not w.name.endswith(WORKSPACE_SUFFIX) or not wd.analysis_result_path(w).is_file():
        raise HandoffProblem("這個資料夾看起來不是這個工具建的工作資料夾，不清。請將錯誤訊息通知開發者。")
    if closing.closed(w):
        raise HandoffError("這支影片已結案。")
    return closing._video_copy_row(w, kind, data)


def clean_episode(workdir: str | Path, *, kind: str | None = None, data: Path | None = None, busy=None) -> dict:
    """夥伴端「清掉這集」：再確認都過了，整個工作區移除，WSL 上工具複製進 Ubuntu、沒有別的專案在用的原片複本一起刪
    （Mac 的原片不碰）。回 {省下位元組}。"""
    from bookclub import closing

    w = Path(workdir)
    row = episode_clean_check(w, kind=kind, data=data)
    why = busy() if busy else None
    if why:
        raise HandoffError(why)
    saved = closing.dir_size(w)
    if row and closing._outside_ok(row, {"匯入資料夾": closing._import_dir(data)}):
        Path(row["路徑"]).unlink()
        saved += row["大小"]
    shutil.rmtree(w)
    return {"省下位元組": saved}
