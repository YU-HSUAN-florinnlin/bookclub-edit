"""本機網頁伺服器：`bookclub serve <工作區>`。

只用標準函式庫（`http.server.ThreadingHTTPServer`），只聽 127.0.0.1，把
`bookclub/web/` 的靜態檔案（首頁、`app.js`、`style.css`）與 `/api/` 底下的
JSON API 兜起來。人在網頁上做決定（挑參考音、覆核名字），決定直接存回工作區
檔案——跟之前「產靜態 HTML 給人看」的做法（`bookclub/namespage.py`、
`bookclub/refpick.py` 的試聽頁）不同，這支模組是可以互動、會寫檔的。

設計原則：API 的商業邏輯寫成不依賴 socket 的純函式（`build_state`、
`build_refs`、`build_names`、`names_mark`、`refs_use`、`safe_join`、
`parse_range`），HTTP handler 只負責讀 request、呼叫純函式、包成 JSON 回應。
這樣 `tests/test_server.py` 不用真的開網路埠就能測大部分邏輯。

路徑安全：只允許讀寫「這次伺服器啟動指定的工作區」與 `~/讀書會剪輯資料/`
底下的檔案。`safe_join()` 統一擋 `..` 跳脫與絕對路徑外洩，兩個 API
（`/api/audio`、找名字／參考音的檔案讀寫）都要先過這關。

隱私提醒：`名字候選.json`、`名字覆核決定.json` 這幾個檔案含學員分享內容，
這支模組的 log（`log_message`）只印方法與路徑，不印 request body 或回應內容。
"""

from __future__ import annotations

import contextlib
import csv
import json
import mimetypes
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from bookclub import names as names_mod
from bookclub import namespage as namespage_mod
from bookclub.config import data_dir, load_settings
from bookclub.workdir import (
    analysis_result_path,
    audio_path,
    merged_transcript_path,
    name_candidates_dir,
    names_path,
    overlap_path,
    read_json,
    ref_dir as ref_dir_path,
    speakers_path,
    write_json,
    fmt_time,
)

WEB_DIR = Path(__file__).resolve().parent / "web"
WEB_CACHE_DIR_NAME = "web快取"
DECISIONS_FILE_NAME = "名字覆核決定.json"
EXCLUSION_CSV_NAME = "名字排除清單.csv"

# 左側步驟列這一輪只有這三步是真的，其餘顯示「還沒做」，見 bookclub/web/app.js
REAL_STEPS = ("1", "2", "4")


class SecurityError(Exception):
    """路徑跳出允許範圍（工作區或資料夾根目錄）。"""


# ---------------------------------------------------------------------------
# 路徑安全
# ---------------------------------------------------------------------------

def safe_join(base: Path, relative: str) -> Path:
    """把 `relative` 接到 `base` 底下，拒絕 `..` 跳脫與絕對路徑。"""
    if not relative:
        raise SecurityError("缺少路徑")
    rel_path = Path(relative)
    if rel_path.is_absolute() or any(part == ".." for part in rel_path.parts):
        raise SecurityError(f"不允許的路徑：{relative}")
    base = base.resolve()
    candidate = (base / rel_path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        raise SecurityError(f"路徑跳出允許範圍：{relative}") from None
    return candidate


# ---------------------------------------------------------------------------
# 專案（09-26：一支影片一個工作區；總覽選影片、切換專案）
# ---------------------------------------------------------------------------

VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi")


GROQ_KEY_MISSING = ("這個網頁伺服器讀不到 Groq 金鑰，沒辦法轉文字。金鑰設在 ~/.zshrc 時，要從會讀 ~/.zshrc 的地方啟動："
                    "雙擊「啟動.command」，或在終端機（zsh）執行 .venv/bin/bookclub serve")


def groq_key_ready() -> bool:
    """轉文字（Groq）要的金鑰這個程式讀不讀得到。只檢查有沒有，不讀出內容。"""
    import os

    return bool(os.environ.get("GROQ_API_KEY"))


class NoProject(Exception):
    """還沒選專案（`bookclub serve` 沒帶工作區、網頁上也還沒選影片）。"""


def workroot() -> Path:
    """09-26 以前的工作區都放在這裡：`~/讀書會剪輯資料/工作區/`（舊專案照樣列得出來、切得過去）。"""
    return data_dir() / "工作區"


PROJECT_SUFFIX = "_剪輯工作區"


def registry_path() -> Path:
    """專案清單：工作資料夾建在影片旁邊（09-27 宇軒），散在各處，靠這份清單列出、切換。"""
    return data_dir() / "專案清單.json"


def registered_projects() -> list[Path]:
    data = read_json(registry_path(), default=None) or {}
    return [Path(x) for x in data.get("專案", [])]


def register_project(d: Path) -> None:
    items = [str(x) for x in registered_projects()]
    if str(d) not in items:
        items.append(str(d))
        write_json(registry_path(), {"專案": items})


def known_project(d: Path) -> bool:
    """只能切換到清單上的專案，或舊的 `~/讀書會剪輯資料/工作區/` 底下的資料夾。"""
    d = Path(d).resolve()
    if any(d == x.resolve() for x in registered_projects()):
        return True
    try:
        d.relative_to(workroot().resolve())
        return d.parent == workroot().resolve()
    except ValueError:
        return False


def home_path(text: str | None) -> Path:
    """網頁資料夾瀏覽只允許家目錄底下：擋 `..`、擋跳出家目錄（含符號連結）。"""
    home = Path.home().resolve()
    if not text:
        return home
    raw = Path(str(text)).expanduser()
    if any(part == ".." for part in raw.parts):
        raise SecurityError(f"不允許的路徑：{text}")
    p = (raw if raw.is_absolute() else home / raw).resolve()
    try:
        p.relative_to(home)
    except ValueError:
        raise SecurityError(f"只能選家目錄底下的檔案：{text}") from None
    return p


def browse(path_text: str | None) -> dict:
    """`GET /api/browse?path=`：列出資料夾與影片檔（不列隱藏檔）。"""
    p = home_path(path_text)
    if not p.is_dir():
        raise FileNotFoundError(f"不是資料夾：{p}")
    home = Path.home().resolve()
    dirs, videos = [], []
    try:
        entries = sorted(p.iterdir(), key=lambda x: x.name)
    except PermissionError as e:
        raise SecurityError(f"沒有權限讀這個資料夾：{p}") from e
    for x in entries:
        if x.name.startswith("."):
            continue
        try:
            if x.is_dir():
                dirs.append(x.name)
            elif x.suffix.lower() in VIDEO_EXTS:
                videos.append({"名稱": x.name, "大小MB": round(x.stat().st_size / 1e6, 1)})
        except OSError:
            continue
    return {"路徑": str(p), "上一層": str(p.parent) if p != home else None, "家目錄": str(home),
            "資料夾": dirs, "影片": videos}


def project_dir_for(video: Path) -> Path:
    """影片對應的專案資料夾：建在影片旁邊，`<影片檔名（不含副檔名）>_剪輯工作區/`（09-27 宇軒）。"""
    video = Path(video)
    name = video.stem.strip().replace("/", "_") or "未命名"
    return video.parent / f"{name}{PROJECT_SUFFIX}"


def _project_row(d: Path, current: Path | None) -> dict:
    analysis = read_json(analysis_result_path(d), default={}) or {}
    video = analysis.get("video")
    return {
        "名稱": d.name, "路徑": str(d), "位置": str(d.parent),
        "影片": Path(video).name if video else None,
        "長度": fmt_time(analysis["影片長度"]) if analysis.get("影片長度") else None,
        "分析完成": bool(analysis.get("elapsed", {}).get("總耗時")) or bool(analysis.get("句數")),
        "有覆核": (d / "覆核" / "覆核決定.json").exists(),
        "舊位置": d.parent == workroot(),
        "修改時間": datetime.fromtimestamp(d.stat().st_mtime).strftime("%m-%d %H:%M"),
    }


def list_projects(current: Path | None = None) -> dict:
    """`GET /api/projects`：專案清單上的專案（影片旁邊的工作資料夾）＋舊位置 `工作區/` 底下的資料夾。"""
    dirs = [d for d in registered_projects() if d.is_dir()]
    root = workroot()
    if root.is_dir():
        dirs += [x for x in root.iterdir() if x.is_dir() and not x.name.startswith(".")]
    seen, rows = set(), []
    for d in sorted(dirs, key=lambda x: x.stat().st_mtime, reverse=True):
        if str(d) in seen:
            continue
        seen.add(str(d))
        rows.append(_project_row(d, current))
    return {"目前": str(current) if current else None, "工作區根目錄": str(root), "專案": rows}


# ---------------------------------------------------------------------------
# Range 請求（給音檔播放器拖曳用）
# ---------------------------------------------------------------------------

def parse_range(range_header: str | None, file_size: int) -> tuple[int, int]:
    """解析 HTTP `Range` 標頭，回傳 `(start, end)`（bytes，含兩端）。

    沒有標頭就回傳整個檔案的範圍。標頭格式不合法或超出檔案大小丟 `ValueError`，
    呼叫端接住後回 416。只處理單一區段（`bytes=a-b`），多重區段只取第一段
    （瀏覽器播放器不會送多重區段，夠用）。
    """
    if file_size <= 0:
        return 0, -1
    if not range_header or not range_header.startswith("bytes="):
        return 0, file_size - 1
    spec = range_header[len("bytes="):].split(",")[0].strip()
    if "-" not in spec:
        raise ValueError(f"無效的 Range：{range_header}")
    s, _, e = spec.partition("-")
    if s == "":
        if not e:
            raise ValueError(f"無效的 Range：{range_header}")
        length = int(e)
        start = max(0, file_size - length)
        end = file_size - 1
    else:
        start = int(s)
        end = int(e) if e else file_size - 1
    end = min(end, file_size - 1)
    if start < 0 or start > end or start >= file_size:
        raise ValueError(f"Range 超出檔案範圍：{range_header}（檔案 {file_size} bytes）")
    return start, end


# ---------------------------------------------------------------------------
# /api/state
# ---------------------------------------------------------------------------

def build_state(workdir: Path, video: Path | None = None) -> dict:
    """工作區摘要：影片資訊、各步驟有沒有完成、各步驟耗時、統計數字。"""
    workdir = Path(workdir)
    analysis = read_json(analysis_result_path(workdir), default={}) or {}
    elapsed = analysis.get("elapsed", {})

    merged = read_json(merged_transcript_path(workdir), default=None)
    speakers = read_json(speakers_path(workdir), default=None)
    overlap = read_json(overlap_path(workdir), default=None)
    ref_record = read_json(ref_dir_path(workdir) / "挑選紀錄.json", default=None)
    names_result = read_json(names_path(workdir), default=None)

    duration = analysis.get("影片長度") or (merged or {}).get("duration")

    video_path = Path(video) if video else None
    if video_path is None and analysis.get("video"):
        video_path = Path(analysis["video"])

    # 子步驟鍵名沿用 bookclub/analyze.py 的中文命名（1_轉文字…5_找名字），前端
    # 第 1 步頁直接照這個順序顯示；「挑選老師參考聲音片段」（前端第 2 步）另外從
    # /api/refs 抓細節，這裡的「認老師」只給總覽用的統計數字。
    substeps = {
        "轉文字": {
            "done": merged is not None,
            "elapsed_s": elapsed.get("1_轉文字"),
            "統計": {
                "句數": len(merged["sentences"]) if merged else analysis.get("句數"),
                "字數": len(merged["words"]) if merged else analysis.get("字數"),
            },
        },
        "認老師": {
            "done": speakers is not None,
            "elapsed_s": elapsed.get("2b_認老師", elapsed.get("2_認老師")),
            "統計": {
                "老師群佔可比對總秒數比例": (speakers or {}).get("cluster_info", {}).get(
                    "老師群佔可比對總秒數比例", analysis.get("老師群佔可比對總秒數比例")
                ),
            },
        },
        "找重疊": {
            "done": overlap is not None and not overlap.get("跳過"),
            "elapsed_s": elapsed.get("4_找重疊", elapsed.get("3_找重疊")),
            "統計": {
                "重疊數": (overlap or {}).get("重疊數", analysis.get("重疊數")),
                "已自動跳過數": (overlap or {}).get("已自動跳過數", analysis.get("重疊已自動跳過數")),
            },
        },
        "挑參考音": {
            "done": ref_record is not None,
            "elapsed_s": elapsed.get("5_挑參考音", elapsed.get("4_挑參考音")),
            "統計": {
                "候選數": (ref_record or {}).get("候選數", analysis.get("參考音候選數")),
                "已選定名次": (ref_record or {}).get("選定名次"),
            },
        },
        "找名字": {
            "done": names_result is not None,
            "elapsed_s": elapsed.get("6_找名字", elapsed.get("5_找名字")),
            "統計": (names_result or {}).get("統計", analysis.get("名字候選統計", {})),
        },
        "段落分析": {
            "done": (workdir / "校對" / "段落.json").exists(),
            "elapsed_s": elapsed.get("2a_段落文字_Claude", 0) + elapsed.get("7_段落聲紋", 0)
            if ("2a_段落文字_Claude" in elapsed or "7_段落聲紋" in elapsed) else elapsed.get("6_段落分析"),
            "統計": analysis.get("段落統計") or {},
        },
    }

    turns_data = read_json(workdir / "校對" / "段落.json", default=None)
    stu_turns = [t for t in (turns_data or {}).get("段落", []) if t.get("說話者") != "老師"]
    proofread_state = {
        "done": bool(stu_turns) and all(t.get("已確認") for t in stu_turns),
        "已確認": sum(1 for t in stu_turns if t.get("已確認")), "學員段落數": len(stu_turns),
    }

    return {
        "proofread": proofread_state,
        "workdir": str(workdir),
        "video": {
            "path": str(video_path) if video_path else None,
            "name": video_path.name if video_path else None,
            "duration_s": duration,
            "duration_hms": fmt_time(duration) if duration else None,
        },
        "substeps": substeps,
        "總耗時_s": elapsed.get("總耗時"),
        "有分析結果檔": bool(analysis),
    }


# ---------------------------------------------------------------------------
# /api/refs、/api/refs/use
# ---------------------------------------------------------------------------

def _candidate_time_desc(attempt: dict) -> str:
    if attempt.get("type") == "拼接":
        pieces = attempt.get("小段", [])
        segs = []
        for p in pieces:
            s, e = p.get("原片起訖", [0, 0])
            segs.append(f"{fmt_time(s)}–{fmt_time(e)}")
        return "、".join(segs) + "（拼接）" if segs else "（拼接，時間不明）"
    s = attempt.get("used_start")
    e = attempt.get("used_end")
    if s is None or e is None:
        s, e = attempt.get("最終起訖", [0, 0])
    return f"{fmt_time(s)}–{fmt_time(e)}"


def build_refs(workdir: Path, ref_dir_name: str = "參考音") -> dict:
    """參考音候選清單：名次、原片時間、長度、字數、分數、逐字稿初稿、音檔網址。"""
    workdir = Path(workdir)
    rd = ref_dir_path(workdir, ref_dir_name)
    attempts = read_json(rd / "候選.json", default=[]) or []
    record = read_json(rd / "挑選紀錄.json", default={}) or {}

    accepted = sorted(
        (a for a in attempts if a.get("狀態") == "入選" and a.get("名次")),
        key=lambda a: a["名次"],
    )

    candidates = []
    for a in accepted:
        rank = a["名次"]
        txt_path = rd / f"候選{rank}.txt"
        wav_path = rd / f"候選{rank}.wav"
        transcript = txt_path.read_text(encoding="utf-8") if txt_path.exists() else a.get("transcript", "")
        audio_url = None
        if wav_path.exists():
            rel = str(wav_path.relative_to(workdir))
            audio_url = f"/api/audio?path={quote(rel)}"
        candidates.append({
            "rank": rank,
            "原片時間": _candidate_time_desc(a),
            "長度秒": a.get("compressed_duration"),
            "字數": a.get("字數", len(transcript)),
            "score": a.get("score"),
            "transcript": transcript,
            "音檔網址": audio_url,
        })

    return {
        "總數": len(candidates),
        "已選定名次": record.get("選定名次"),
        "candidates": candidates,
    }


def refs_use(workdir: Path, rank: int, transcript: str) -> dict:
    """選定某一名次，存成 `ref.wav`／`ref.txt`（呼叫 `bookclub/refpick.py` 的
    `finalize_reference`，這支只是把 API 參數轉一手）。"""
    from bookclub.refpick import finalize_reference

    return finalize_reference(workdir, rank, transcript)


# ---------------------------------------------------------------------------
# /api/names、/api/names/mark
# ---------------------------------------------------------------------------

def build_names(workdir: Path) -> dict:
    """名字候選（含逐字稿整句、音檔網址、已標記的決定）與已自動排除清單。

    每筆候選的 `id` 是它在目前 `名字候選.json` 裡的順位（1 起算，字串）。
    只有重跑找名字、候選清單改變（增減或重新排序）時，`id` 才會對不上舊的
    `名字覆核決定.json`——這一輪先這樣做，之後真的重跑名字覆核再觀察需不需要
    換成內容雜湊當 id。
    """
    workdir = Path(workdir)
    result = read_json(names_path(workdir), default=None)
    if result is None:
        return {"candidates": [], "已自動排除": [], "統計": {}}

    decisions = read_json(workdir / DECISIONS_FILE_NAME, default={}) or {}
    clip_dir = name_candidates_dir(workdir)

    candidates = []
    for i, c in enumerate(result.get("candidates", []), start=1):
        cid = str(i)
        start = c.get("start", 0.0)
        end = c.get("end", 0.0)
        clip_start = max(0.0, start - names_mod.CANDIDATE_CLIP_PAD_S)

        orig_rel = c.get("候選音檔", "")
        orig_path = workdir / orig_rel if orig_rel else None
        orig_url = None
        muted_url = None
        if orig_path is not None and orig_path.exists():
            orig_url = f"/api/audio?path={quote(orig_rel)}"
            muted_path = namespage_mod._mute_clip(orig_path, clip_start, start, end)
            if muted_path is not None:
                muted_rel = str(muted_path.relative_to(workdir))
                muted_url = f"/api/audio?path={quote(muted_rel)}"

        decision = decisions.get(cid, {})
        candidates.append({
            "id": cid,
            "時間": namespage_mod._fmt_hms1(start),
            "start": start,
            "end": end,
            "sentence_html": namespage_mod._highlight_sentence(
                c.get("sentence", ""), c.get("matched_text", ""), c.get("位置", "")
            ),
            "matched_text": c.get("matched_text", ""),
            "代號": c.get("代號", ""),
            "位置": c.get("位置", ""),
            "比對層級": c.get("比對層級", ""),
            "信心": c.get("信心", ""),
            "建議做法": c.get("建議做法", ""),
            "切點信心": c.get("切點信心", ""),
            "原音網址": orig_url,
            "消音網址": muted_url,
            "已標記": {"tags": decision.get("tags", []), "note": decision.get("note", "")},
        })

    _ = clip_dir  # 保留變數方便之後擴充；目前音檔路徑都是從候選記錄裡的相對路徑算

    return {
        "candidates": candidates,
        "已自動排除": result.get("已自動排除", []),
        "統計": result.get("統計", {}),
    }


def names_mark(workdir: Path, cid: str, tags: list[str], note: str) -> dict:
    """勾選即時存檔：寫進／更新 `工作區/名字覆核決定.json`（累積、可重複更新同一筆）。
    tag 含「是地名」或「不是名字」時，同時把該筆的 `matched_text` 追加進
    `~/讀書會剪輯資料/名字排除清單.csv`（正規化後重複就不重寫）。"""
    workdir = Path(workdir)
    cid = str(cid)
    decisions_path = workdir / DECISIONS_FILE_NAME
    decisions = read_json(decisions_path, default={}) or {}
    decisions[cid] = {
        "tags": list(tags or []),
        "note": note or "",
        "更新時間": datetime.now().isoformat(timespec="seconds"),
    }
    write_json(decisions_path, decisions)

    added_to_exclusion = False
    if any(t in ("是地名", "不是名字") for t in (tags or [])):
        names_result = read_json(names_path(workdir), default=None) or {}
        candidates = names_result.get("candidates", [])
        idx = int(cid) - 1
        if 0 <= idx < len(candidates):
            matched_text = candidates[idx].get("matched_text", "")
            if matched_text:
                reason = "、".join(t for t in tags if t in ("是地名", "不是名字"))
                added_to_exclusion = _append_exclusion(matched_text, reason)

    return {"ok": True, "id": cid, "已加入排除清單": added_to_exclusion}


def _append_exclusion(term: str, reason: str) -> bool:
    """加進 `~/讀書會剪輯資料/名字排除清單.csv`。正規化後（拿掉標點空白）跟既有
    的詞重複就不重寫，回傳這次有沒有真的新增一筆。用純文字（無 BOM）寫入——
    `utf-8-sig` 的 BOM 只有在檔案開頭才該出現，用 append 模式每次都用
    `utf-8-sig` 開檔會在檔案中間插入多餘的 BOM bytes，改用 `bookclub/names.py`
    的 `load_exclusion_list`（`utf-8-sig` 讀檔）相容：有沒有 BOM 都讀得對。"""
    path = data_dir() / EXCLUSION_CSV_NAME
    existing = names_mod.load_exclusion_list(path)
    norm_existing = {names_mod._normalize_match_text(row["詞"]) for row in existing}
    norm_term = names_mod._normalize_match_text(term)
    if not norm_term or norm_term in norm_existing:
        return False

    is_new_file = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if is_new_file:
            writer.writerow(["詞", "原因", "建立日期"])
        writer.writerow([term, reason, datetime.now().strftime("%Y-%m-%d")])
    return True


def _import_profile_bytes(raw: bytes) -> dict:
    """`POST /api/profile/import`：網頁上傳的設定包（body 是 zip）先存成暫存檔再匯入。"""
    import tempfile

    from bookclub import profile

    if not raw.startswith(b"PK"):
        raise ValueError("上傳的不是 zip 檔")
    with tempfile.TemporaryDirectory() as tmp:
        z = Path(tmp) / "設定包.zip"
        z.write_bytes(raw)
        return profile.import_profile(z)


# ---------------------------------------------------------------------------
# /api/audio：直接給檔案，或用 ffmpeg 即時切片
# ---------------------------------------------------------------------------

def get_or_make_clip(workdir: Path, start: float, end: float) -> Path:
    """`start`／`end`（原片絕對秒數）指定時，用 ffmpeg 從 `audio.flac` 即時切一段，
    快取到 `工作區/web快取/`，檔名用參數命名，同樣參數第二次直接給快取檔。"""
    if end <= start:
        raise ValueError(f"end（{end}）必須大於 start（{start}）")
    workdir = Path(workdir)
    src = audio_path(workdir)
    if not src.exists():
        raise FileNotFoundError(f"{src} 不存在，還沒跑過轉文字（第 1 步）")

    cache_dir = workdir / WEB_CACHE_DIR_NAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / f"{start:.2f}_{end:.2f}.wav"
    if out_path.exists():
        return out_path

    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
            "-i", str(src),
            str(out_path),
        ],
        check=True,
    )
    return out_path


# ---------------------------------------------------------------------------
# 背景執行「開始分析」
# ---------------------------------------------------------------------------

class _TeeWriter:
    """`print()` 照樣輸出到終端機，同時把整行存進 `sink`（給 `/api/run/status`
    輪詢用），只保留最後 `max_lines` 行，避免長時間跑下來無限吃記憶體。"""

    def __init__(self, real, sink: list, lock: threading.Lock, max_lines: int = 300):
        self._real = real
        self._sink = sink
        self._lock = lock
        self._buf = ""
        self._max = max_lines

    def write(self, s: str) -> int:
        self._real.write(s)
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                with self._lock:
                    self._sink.append(line)
                    if len(self._sink) > self._max:
                        del self._sink[: len(self._sink) - self._max]
        return len(s)

    def flush(self) -> None:
        self._real.flush()


class BookclubServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler_cls, *, workdir: Path | None, video: Path | None):
        super().__init__(addr, handler_cls)
        self._workdir = Path(workdir) if workdir else None
        self.video = Path(video) if video else None
        self.run_lock = threading.Lock()
        self.run_state: dict = self._fresh_run_state()

    @property
    def workdir(self) -> Path:
        if self._workdir is None:
            raise NoProject("還沒選影片：到「總覽」選一支影片，或切換到已有的專案")
        return self._workdir

    @property
    def has_workdir(self) -> bool:
        return self._workdir is not None

    def use_project(self, workdir: Path, video: Path | None = None) -> None:
        """切換「目前的工作區」（所有 API 都讀這個）。分析跑到一半不能換。"""
        with self.run_lock:
            if self.run_state["running"]:
                raise ValueError("分析還在跑，等它結束再切換專案")
            self._workdir = Path(workdir)
            self.video = Path(video) if video else None
            self.run_state = self._fresh_run_state()

    @staticmethod
    def _fresh_run_state() -> dict:
        return {"running": False, "started_at": None, "finished_at": None, "error": None, "messages": []}

    def run_status_running(self) -> bool:
        with self.run_lock:
            return bool(self.run_state["running"])

    def run_status(self) -> dict:
        with self.run_lock:
            state = {k: v for k, v in self.run_state.items() if k != "messages"}
            state["messages"] = list(self.run_state["messages"][-50:])
        state.update(build_state(self.workdir, video=self.video))
        return state

    def start_analyze(self, opts: dict) -> dict:
        with self.run_lock:
            if self.run_state["running"]:
                return {"started": False, "error": "已經有分析在跑，請等它結束再按一次"}

            video = opts.get("video") or (str(self.video) if self.video else None)
            if not video:
                analysis = read_json(analysis_result_path(self.workdir), default={}) or {}
                video = analysis.get("video")
            if not video:
                return {
                    "started": False,
                    "error": "不知道影片路徑：啟動伺服器時帶 --video，或這次呼叫帶 body.video",
                }

            if not merged_transcript_path(self.workdir).exists() and not groq_key_ready():
                return {"started": False, "error": GROQ_KEY_MISSING}

            self.run_state = self._fresh_run_state()
            self.run_state["running"] = True
            self.run_state["started_at"] = time.time()
            thread = threading.Thread(target=self._run_job, args=(video, opts), daemon=True)
            thread.start()
        return {"started": True}

    def _run_job(self, video: str, opts: dict) -> None:
        from bookclub.analyze import run_analyze  # 延後載入：避免 serve --help 這類指令也要載入重依賴

        tee = _TeeWriter(sys.stdout, self.run_state["messages"], self.run_lock)
        try:
            with contextlib.redirect_stdout(tee):
                d = data_dir()
                default = lambda name: str(d / name) if (d / name).exists() else None  # noqa: E731
                run_analyze(
                    video,
                    self.workdir,
                    roster_path=opts.get("roster") or default("名冊.csv"),
                    sensitive_path=opts.get("sensitive") or default("敏感詞.csv"),
                    exclusion_path=opts.get("exclusions"),
                    skip_overlap=bool(opts.get("skip_overlap", False)),
                    ref_n=int(opts.get("ref_n", 5)),
                    skip_turns=bool(opts.get("skip_turns", False)),
                )
        except Exception as e:  # noqa: BLE001 — 背景執行緒要把任何失敗記進 run_state，不能讓它默默死掉
            with self.run_lock:
                self.run_state["error"] = f"{type(e).__name__}：{e}"
        finally:
            with self.run_lock:
                self.run_state["running"] = False
                self.run_state["finished_at"] = time.time()


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "BookclubServer/0.1"
    server: BookclubServer  # type: ignore[assignment]

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"[網頁伺服器] {self.address_string()} {fmt % args}\n")

    # ---------- 共用：送 JSON、送檔案 ----------

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _serve_file(self, path: Path, content_type: str | None = None) -> None:
        if not path.is_file():
            raise FileNotFoundError(str(path))
        content_type = content_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        file_size = path.stat().st_size

        try:
            start, end = parse_range(self.headers.get("Range"), file_size)
        except ValueError:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{file_size}")
            self.end_headers()
            return

        is_partial = self.headers.get("Range") is not None and file_size > 0
        length = max(0, end - start + 1)
        self.send_response(HTTPStatus.PARTIAL_CONTENT if is_partial else HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if content_type.split(";")[0] in ("text/html", "text/javascript", "application/javascript", "text/css"):
            self.send_header("Cache-Control", "no-cache")  # 更新工具後重新整理就拿到新版網頁，不會卡在舊快取
        if is_partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.end_headers()
        if self.command == "HEAD" or length == 0:
            return
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # ---------- GET／HEAD ----------

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                self._route_get_api(path, query)
            else:
                self._serve_static(path)
        except (BrokenPipeError, ConnectionResetError):
            return   # 瀏覽器拖曳影片時會中斷前一個請求，正常現象
        except SecurityError as e:
            self._send_json(403, {"error": str(e)})
        except NoProject as e:
            self._send_json(409, {"error": str(e), "沒有專案": True})
        except FileNotFoundError as e:
            self._send_json(404, {"error": str(e)})
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._send_json(500, {"error": f"{type(e).__name__}：{e}"})

    def _serve_static(self, path: str) -> None:
        if path == "/":
            path = "/index.html"
        file_path = safe_join(WEB_DIR, path.lstrip("/"))
        self._serve_file(file_path)

    def _route_get_api(self, path: str, query: dict) -> None:
        server = self.server
        if path == "/api/state":
            if not server.has_workdir:
                self._send_json(200, {"沒有專案": True, "workdir": None, "video": {}, "substeps": {}, "proofread": {}})
                return
            self._send_json(200, build_state(server.workdir, video=server.video))
        elif path == "/api/profile":
            from bookclub import profile

            self._send_json(200, profile.summary())
        elif path == "/api/profile/export.zip":
            from bookclub import profile

            out = profile.export_profile()
            self._serve_file(Path(out["檔案"]), content_type="application/zip")
        elif path == "/api/projects":
            self._send_json(200, {**list_projects(server._workdir), "轉文字金鑰": groq_key_ready()})
        elif path == "/api/browse":
            self._send_json(200, browse((query.get("path") or [None])[0]))
        elif path == "/api/refs":
            self._send_json(200, build_refs(server.workdir))
        elif path == "/api/names":
            self._send_json(200, build_names(server.workdir))
        elif path == "/api/proofread":
            from bookclub.proofread import page_data

            self._send_json(200, page_data(server.workdir))
        elif path == "/api/turns":
            from bookclub.turns import page_data as turns_page

            self._send_json(200, turns_page(server.workdir))
        elif path == "/api/review":
            from bookclub.review import page_data as review_page

            self._send_json(200, review_page(server.workdir, video=server.video))
        elif path == "/api/video":
            from bookclub.review import video_path

            vp = video_path(server.workdir, server.video)
            if vp is None:
                raise FileNotFoundError("找不到原片：啟動伺服器時帶 --video，或確認分析結果記錄的影片路徑還在")
            self._serve_file(vp)
        elif path == "/api/review/export.zip":
            from bookclub.exchange import export_review

            out = export_review(server.workdir, video=server.video)
            self._serve_file(Path(out["檔案"]), content_type="application/zip")
        elif path == "/api/audio":
            self._handle_audio(query)
        elif path == "/api/final":
            from bookclub import finalcheck

            self._send_json(200, finalcheck.page_data(server.workdir))
        elif path == "/api/final/video":
            from bookclub import finalcheck

            self._serve_file(finalcheck.video_file(server.workdir))
        elif path == "/api/final/clip":
            from bookclub import finalcheck

            self._serve_file(finalcheck.clip(server.workdir, query["which"][0], query["key"][0]), content_type="audio/wav")
        elif path == "/api/run/status":
            self._send_json(200, server.run_status())
        else:
            self._send_json(404, {"error": f"沒有這個 API：{path}"})

    def _handle_audio(self, query: dict) -> None:
        workdir = self.server.workdir
        if "path" in query:
            file_path = safe_join(workdir, query["path"][0])
            self._serve_file(file_path)
            return
        if "start" in query and "end" in query:
            start = float(query["start"][0])
            end = float(query["end"][0])
            clip_path = get_or_make_clip(workdir, start, end)
            self._serve_file(clip_path, content_type="audio/wav")
            return
        raise ValueError("/api/audio 需要 path 參數，或 start＋end 參數")

    # ---------- POST ----------

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            if path == "/api/profile/import":        # 設定包：body 是 zip 本身
                self._send_json(200, _import_profile_bytes(raw))
                return
            body = json.loads(raw.decode("utf-8")) if raw.strip() else {}
            self._route_post_api(path, body)
        except SecurityError as e:
            self._send_json(403, {"error": str(e)})
        except NoProject as e:
            self._send_json(409, {"error": str(e), "沒有專案": True})
        except FileNotFoundError as e:
            self._send_json(404, {"error": str(e)})
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            self._send_json(400, {"error": f"{type(e).__name__}：{e}"})
        except Exception as e:  # noqa: BLE001
            self._send_json(500, {"error": f"{type(e).__name__}：{e}"})

    def _route_post_api(self, path: str, body: dict) -> None:
        server = self.server
        if path == "/api/projects/switch":
            d = Path(str(body["路徑"])) if body.get("路徑") else safe_join(workroot(), str(body["名稱"]))
            if not d.is_dir() or not known_project(d):
                raise SecurityError(f"不是專案清單上的專案：{d}")
            analysis = read_json(analysis_result_path(d), default={}) or {}
            server.use_project(d, analysis.get("video"))
            self._send_json(200, {"ok": True, "目前": str(d)})
            return
        if path == "/api/projects/start":
            video = home_path(str(body["影片"]))
            if not video.is_file() or video.suffix.lower() not in VIDEO_EXTS:
                raise FileNotFoundError(f"不是影片檔：{video}")
            d = project_dir_for(video)
            existed = d.exists()
            if server.run_status_running():
                raise ValueError("分析還在跑，等它結束再開始另一支")
            d.mkdir(parents=True, exist_ok=True)          # 按下「開始分析」才建資料夾（在影片旁邊）
            register_project(d)
            server.use_project(d, video)
            opts = {k: body[k] for k in ("skip_turns", "skip_overlap") if k in body}
            result = server.start_analyze({"video": str(video), **opts})
            self._send_json(202 if result.get("started") else 409,
                            {**result, "專案": d.name, "路徑": str(d), "接著做": existed})
            return
        if path == "/api/refs/use":
            rank = int(body["rank"])
            transcript = str(body.get("transcript", ""))
            self._send_json(200, refs_use(server.workdir, rank, transcript))
        elif path == "/api/names/mark":
            cid = str(body["id"])
            tags = body.get("tags", [])
            note = str(body.get("note", ""))
            self._send_json(200, names_mark(server.workdir, cid, tags, note))
        elif path == "/api/proofread/save":
            from bookclub.proofread import save_item

            self._send_json(200, save_item(server.workdir, str(body["id"]), body))
        elif path == "/api/proofread/voice":
            from bookclub.proofread import set_voice

            self._send_json(200, set_voice(server.workdir, str(body["聲音"]), body.get("學員")))
        elif path.startswith("/api/turns/"):
            from bookclub import turns as _turns

            if path == "/api/turns/merge_person":
                self._send_json(200, _turns.merge_person(server.workdir, str(body["從"]), str(body["併進"])))
            elif path == "/api/turns/reassign":
                self._send_json(200, _turns.reassign_turns(server.workdir, [str(x) for x in body["ids"]],
                                                           str(body["說話者"])))
            elif path == "/api/turns/mark_student":
                self._send_json(200, _turns.mark_student(server.workdir, float(body["start"]), float(body["end"]),
                                                         str(body.get("說話者") or "新學員")))
            elif path == "/api/turns/save":
                self._send_json(200, _turns.save_turn(server.workdir, str(body["id"]), body))
            elif path == "/api/turns/merge":
                self._send_json(200, _turns.merge_turn(server.workdir, str(body["id"])))
            elif path == "/api/turns/split":
                self._send_json(200, _turns.split_turn(server.workdir, str(body["id"]), int(body["at"])))
            elif path == "/api/turns/person":
                self._send_json(200, _turns.set_person_code(server.workdir, str(body["學員"]), body.get("代號")))
            else:
                self._send_json(404, {"error": f"沒有這個 API：{path}"})
        elif path.startswith("/api/review/"):
            from bookclub import review as rv

            if path == "/api/review/name":
                self._send_json(200, rv.save_name(server.workdir, str(body["id"]), body))
            elif path == "/api/review/overlap":
                self._send_json(200, rv.save_overlap(server.workdir, str(body["id"]), body))
            elif path == "/api/review/cut":
                self._send_json(200, rv.save_cut(server.workdir, body))
            elif path == "/api/review/mute":
                self._send_json(200, rv.save_mute(server.workdir, body))
            elif path == "/api/review/manual":
                self._send_json(200, rv.manual_edit(server.workdir, body))
            elif path == "/api/review/voice":
                self._send_json(200, rv.set_voice(server.workdir, body.get("學員"), str(body["聲音"])))
            elif path == "/api/review/prep":
                self._send_json(200, rv.set_prep(server.workdir, str(body["項目"]), bool(body.get("完成", True))))
            elif path == "/api/review/cutsuggest":
                self._send_json(200, rv.decide_cut_suggestion(server.workdir, str(body["id"]), str(body["決定"])))
            elif path == "/api/review/time":
                self._send_json(200, rv.add_time(server.workdir, float(body.get("秒數", 0))))
            elif path == "/api/review/export":
                from bookclub.exchange import export_review

                self._send_json(200, export_review(server.workdir, video=server.video))
            else:
                self._send_json(404, {"error": f"沒有這個 API：{path}"})
        elif path.startswith("/api/final/"):
            from bookclub import finalcheck as fc

            w = server.workdir
            if path == "/api/final/item":
                self._send_json(200, fc.decide_record(w, str(body["鍵"]), body.get("結果"), str(body.get("原因", ""))))
            elif path == "/api/final/unlogged":
                self._send_json(200, fc.decide_unlogged(w, str(body["鍵"]), body.get("結果"), str(body.get("原因", ""))))
            elif path == "/api/final/flag":
                self._send_json(200, fc.add_whole_redo(w, float(body["成品秒"]), str(body.get("原因", ""))))
            elif path == "/api/final/unflag":
                self._send_json(200, fc.remove_whole_redo(w, str(body["id"])))
            elif path == "/api/final/watched":
                self._send_json(200, fc.add_watched(w, body.get("區段", []), body.get("成品影片")))
            elif path == "/api/final/product":
                self._send_json(200, fc.choose_product(w, str(body["成品影片"])))
            elif path == "/api/final/sendback":
                self._send_json(200, fc.send_back(w))
            elif path == "/api/final/export":
                self._send_json(200, fc.export_final(w))
            else:
                self._send_json(404, {"error": f"沒有這個 API：{path}"})
        elif path == "/api/run/analyze":
            result = server.start_analyze(body)
            self._send_json(202 if result.get("started") else 409, result)
        else:
            self._send_json(404, {"error": f"沒有這個 API：{path}"})


# ---------------------------------------------------------------------------
# 對外入口
# ---------------------------------------------------------------------------

def serve(
    workdir: str | Path | None = None,
    video: str | Path | None = None,
    port: int | None = None,
    open_browser: bool = True,
) -> None:
    """不帶工作區：網頁總覽選影片或切換已有的專案（09-26）。帶工作區：舊用法，直接開那一個。"""
    if workdir:
        workdir = Path(workdir).expanduser()
        workdir.mkdir(parents=True, exist_ok=True)
    if port is None:
        port = load_settings().server_port

    httpd = BookclubServer(
        ("127.0.0.1", port), Handler,
        workdir=workdir or None, video=Path(video).expanduser() if video else None,
    )
    url = f"http://127.0.0.1:{port}/"
    print(f"[網頁伺服器] 網址：{url}")
    print(f"[網頁伺服器] 工作區：{workdir or '（還沒選，到網頁總覽選影片）'}")
    if not groq_key_ready():
        print(f"⚠️ [網頁伺服器] {GROQ_KEY_MISSING}（已經轉好文字的專案不受影響）")
    print("[網頁伺服器] Ctrl+C 結束")

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        print("\n[網頁伺服器] 已結束")
