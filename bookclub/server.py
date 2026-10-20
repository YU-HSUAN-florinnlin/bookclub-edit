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


LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
AWAKE_STARTING = "正在請電腦不要睡⋯"   # 10-07 #172：exec_state／run_state「防睡眠」欄位，開成沒有之前先顯示這句


def _hostname(value: str) -> str:
    v = (value or "").strip()
    if v.startswith("["):            # [::1]:8766
        return v[1:].split("]", 1)[0]
    return v.rsplit(":", 1)[0] if v.count(":") == 1 else v


def request_allowed(host: str | None, origin: str | None, writes: bool) -> bool:
    """這個請求是不是從這台電腦、這個工具自己的網頁來的（純函式，09-30）。

    工具開著的時候，瀏覽器裡別的網站也能對 127.0.0.1 送請求（例如把全部學員改成保留原聲、啟動執行）。
    - `Host` 要是本機（擋掉把別的網域指到 127.0.0.1 來讀資料的做法）
    - 帶 `Origin` 的（瀏覽器跨站送出的一定會帶）要是本機的網頁；會改資料的請求連 `Origin: null` 也不收
    指令列工具（curl、測試）不帶 `Origin`，照常可以用。"""
    if host and _hostname(host) not in LOCAL_HOSTS:
        return False
    if origin is None or origin == "":
        return True
    if origin == "null":
        return not writes
    return (urlparse(origin).hostname or "") in LOCAL_HOSTS


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

from bookclub.fileio import VIDEO_EXTS  # noqa: E402  10-07：跟系統選檔視窗同一份副檔名清單


def groq_key_howto(system: str | None = None, *, cli: bool = False) -> str:
    """讀不到 Groq 金鑰時「怎麼補」那一句，依平台只講適用的那一種。網頁伺服器（`cli=False`）與第 4 步的終端機指令
    （`cli=True`，`execute.execute_key_problem`）共用，兩邊說法一致。金鑰檔跟 doctor.key_file() 同一個：
    macOS ~/.zshrc、Linux／WSL2 ~/.profile（Ubuntu 的 ~/.bashrc 在非互動模式會提早結束，寫在裡面讀不到）。"""
    import platform

    system = system or platform.system()
    if system == "Darwin":
        if cli:
            return ("金鑰設在 ~/.zshrc：雙擊「啟動.command」開網頁再按開始執行，"
                    "或在終端機（zsh）開一個新視窗再跑這個指令（也可以用 /bin/zsh -ic '…' 跑）")
        return ("金鑰設在 ~/.zshrc：關掉這個伺服器，改用雙擊「啟動.command」重開，"
                "或在終端機（zsh）進工具資料夾執行 .venv/bin/bookclub serve")
    head = "金鑰要寫在 ~/.profile（寫在 ~/.bashrc 讀不到）：加一行 export GROQ_API_KEY=你的金鑰，"
    if cli:
        return head + "開一個新的終端機視窗，進工具資料夾再跑這個指令"
    return head + "關掉這個伺服器（終端機按 Ctrl+C），開一個新的終端機視窗，進工具資料夾再執行 .venv/bin/bookclub serve"


def groq_key_missing_message(system: str | None = None) -> str:
    """讀不到 Groq 金鑰時給人看的話，依平台只講適用的那一種（10-03 第九批 #29：以前 Linux／WSL2 也叫人
    「雙擊啟動.command」）。怎麼補那一句見 `groq_key_howto`。"""
    return "這個網頁伺服器讀不到 Groq 金鑰，沒辦法轉文字。" + groq_key_howto(system)


def groq_key_execute_message(system: str | None = None, *, cli: bool = False) -> str:
    """第 4 步要生成、卻讀不到 Groq 金鑰時擋下的那一句（網頁 `start_execute` 與終端機 `run execute` 共用開頭）。"""
    return "讀不到 Groq 金鑰（GROQ_API_KEY），生成時沒辦法檢查「念對沒有」，先不開始。" + groq_key_howto(system, cli=cli)


GROQ_KEY_MISSING = groq_key_missing_message()


def groq_key_ready() -> bool:
    """轉文字（Groq）要的金鑰這個程式讀不讀得到。只檢查有沒有，不讀出內容。"""
    import os

    return bool(os.environ.get("GROQ_API_KEY"))


class NoProject(Exception):
    """還沒選專案（`bookclub serve` 沒帶工作區、網頁上也還沒選影片）。"""


class Closed(Exception):
    """1015-8 第 3 段（32 號設計 §5.2）：這支影片已結案，這裡只能看紀錄（所有會寫工作區的 API 一律 409）。"""


CLOSED_MSG = "這支影片已結案，這裡只能看紀錄。"
# 已結案時照樣放行的 POST（不寫這個工作區）：結案框（再存一份紀錄）、切換／開始別的專案、設定包、複製工作、給新影片的選檔
CLOSED_POST_OK = ("/api/close/", "/api/projects/", "/api/handoff/import/", "/api/handoff/clean-episode")
CLOSED_POST_OK_EXACT = ("/api/profile/import", "/api/copy/cancel", "/api/pick/forget")
# 已結案時擋的 GET（會在工作區裡切試聽檔、寫匯出檔）
CLOSED_GET_BLOCK = ("/api/final/clip", "/api/final/vclip", "/api/review/export.zip")


def wd_no_writes():
    from bookclub.workdir import no_writes

    return no_writes()


def _handoff_running(server) -> bool:
    """33 號設計：交接打包或匯入交接包正在做（測試用的假伺服器沒有這兩欄＝沒在做）。"""
    return any(j is not None and j.running for j in (getattr(server, "handoff_job", None), getattr(server, "import_job", None)))


def _server_closed(server) -> bool:
    """目前的工作區已結案（沒有專案＝False）。"""
    try:
        w = server.workdir
    except Exception:  # noqa: BLE001 — 沒有專案
        return False
    return _closed(w)


def _closed(workdir) -> bool:
    from bookclub import closing

    try:
        return closing.closed(workdir)
    except Exception:  # noqa: BLE001
        return False


def closed_post_allowed(path: str, body: dict) -> bool:
    """（純函式）已結案時這個 POST 能不能照樣做。選檔視窗只有「給新影片」的能用（給目前專案的片頭片尾會寫工作區設定）。"""
    if path.startswith(CLOSED_POST_OK) or path in CLOSED_POST_OK_EXACT:
        return True
    if path in ("/api/pick", "/api/pick/browsed"):
        return str((body or {}).get("給", "新影片")) != "目前"
    return False


class Busy(Exception):
    """AI 執行（第 4 步）還在跑：第 3 步的東西這時候不能改（09-30）。"""


# 第 4 步跑的時候擋掉的寫入（第 3 步覆核工作台的存檔）；計時、匯出不算修改
STEP3_WRITES = ("/api/review/", "/api/turns/", "/api/people/", "/api/codes/", "/api/students/")
STEP3_WRITES_OK = ("/api/review/time", "/api/review/export", "/api/people/rescan/seen")   # 10-16 #234：通知看過歸零，第 4 步執行中也要能記


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
    data = read_json(registry_path(), default=None) or {}
    items = [str(x) for x in data.get("專案", [])]
    if str(d) not in items:
        items.append(str(d))
        write_json(registry_path(), {**data, "專案": items})


def unregister_project(d: Path) -> None:
    """33 號設計 #260：整集清掉之後，從專案清單拿掉（「目前」指著它也一起清掉）。"""
    data = read_json(registry_path(), default=None) or {}
    items = [str(x) for x in data.get("專案", []) if str(x) != str(d)]
    cur = data.get("目前")
    write_json(registry_path(), {**data, "專案": items, "目前": None if cur == str(d) else cur})


def remember_current(d: Path | None) -> None:
    """記下「目前在做哪支影片」（09-30）：伺服器重開後自動接回，不會所有 API 都回 409。"""
    data = read_json(registry_path(), default=None) or {}
    cur = str(d) if d else None
    if data.get("目前") == cur:
        return
    write_json(registry_path(), {**data, "專案": data.get("專案", []), "目前": cur})


def adopt_project(d: Path) -> None:
    """10-04 #129：`bookclub serve <工作區>` 明確給了工作區 → 一律以它為準：不在清單上就加進去，並設成「目前」。
    以前只放在記憶體裡，清單和「目前」還指著別的（例如從舊資料夾複製來的 `專案清單.json`），
    總覽只列得出舊的那個（資料夾名稱一樣分不出來），下次不帶工作區重開也會接回舊的。"""
    try:
        register_project(d)
        remember_current(d)
    except (OSError, ValueError) as e:   # 記不下來（寫不進去、清單檔內容壞掉）只是清單上看不到、下次重開要重選，不擋啟動
        print(f"⚠️ [網頁伺服器] 記不下目前的專案：{e}")


def recall_current() -> Path | None:
    """伺服器啟動時接回上次的專案；資料夾不在了、或不是清單上的專案就不接。"""
    cur = (read_json(registry_path(), default=None) or {}).get("目前")
    if not cur:
        return None
    d = Path(cur)
    return d if d.is_dir() and known_project(d) else None


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


def _mark_interrupted(workdir: Path | None) -> None:
    """進度檔殘留「進行中」＝上次跑到一半伺服器被關掉 → 改成「中斷」（09-30）。"""
    if not workdir or _closed(workdir):   # 1015-8 第 3 段：已結案的瘦身版不補寫任何檔（§9.3）
        return
    from bookclub.execute import mark_interrupted

    try:
        if mark_interrupted(workdir):
            print("[網頁伺服器] 上次第 4 步跑到一半伺服器被關掉，進度改成「中斷」（按開始執行會接著做）")
    except Exception as e:  # noqa: BLE001 — 進度檔壞掉不擋啟動
        print(f"⚠️ [網頁伺服器] 讀不了第 4 步進度：{e}")


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


TEST_WORDS = ("測試", "驗證")   # 工作區資料夾本身、上一層、上上層的名稱有這兩個詞＝測試用的工作區（不看整條路徑）


def project_label(d: str | Path) -> dict:
    """10-08（宇軒：兩個專案名稱一樣，切錯了看不出來）：工作區在哪一層、是正式還是測試（純函式）。
    所在＝往上第一個不是「工作區」的資料夾名稱；標示＝工作區資料夾本身、上一層、上上層（跳過叫「工作區」的那層）的名稱有「測試」「驗證」的是測試，其他算正式；
    顯示名稱＝「名稱（所在）」。"""
    d = Path(d)
    ups = [p.name for p in d.parents if p.name and p.name != "工作區"]   # 往上的資料夾（跳過叫「工作區」的那層）
    where = ups[0] if ups else ""
    test = any(w in part for part in [d.name] + ups[:2] for w in TEST_WORDS)
    return {"所在": where, "標示": "測試" if test else "正式", "顯示名稱": f"{d.name}（{where}）" if where else d.name}


def _project_row(d: Path, current: Path | None) -> dict:
    analysis = read_json(analysis_result_path(d), default={}) or {}
    video = analysis.get("video")
    return {
        "名稱": d.name, "路徑": str(d), "位置": str(d.parent), **project_label(d),
        "影片": Path(video).name if video else None,
        "長度": fmt_time(analysis["影片長度"]) if analysis.get("影片長度") else None,
        "分析完成": bool(analysis.get("elapsed", {}).get("總耗時")) or bool(analysis.get("句數")),
        "有覆核": (d / "覆核" / "覆核決定.json").exists(),
        "舊位置": d.parent == workroot(),
        "修改時間": datetime.fromtimestamp(d.stat().st_mtime).strftime("%m-%d %H:%M"),
        "已結案": _closed_info(d),   # 1015-8 第 3 段（§5.3）：總覽放到最下面「已結案」那一組
        "交接": _handoff_info(d),    # 33 號設計 #260：已交出去（第 5 步交接打包）＋清掉這集要空出多少
    }


def _handoff_info(d: Path) -> dict | None:
    from bookclub import handoff

    try:
        return handoff.episode_clean_info(d)
    except Exception:  # noqa: BLE001 — 讀不到當沒交出去（不擋總覽）
        return None


def _closed_info(d: Path) -> dict | None:
    from bookclub import closing

    try:
        return closing.closed_info(d)
    except Exception:  # noqa: BLE001 — 讀不到當沒結案（不擋總覽）
        return None


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
    unique_labels(rows)
    return {"目前": str(current) if current else None, "工作區根目錄": str(root), "專案": rows}


def _ancestors(path: str) -> list[str]:
    return [p.name for p in Path(path).parents if p.name and p.name != "工作區"]


def unique_labels(rows: list[dict], max_depth: int = 4) -> None:
    """（純函式，就地改）顯示名稱還是一樣的幾列（上一層資料夾也同名），往上多帶幾層資料夾名，直到分得出來。"""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r.get("顯示名稱") or r["名稱"], []).append(r)
    for same in groups.values():
        if len(same) < 2:
            continue
        for depth in range(2, max_depth + 1):
            labels = ["／".join(reversed(_ancestors(r["路徑"])[:depth])) for r in same]
            if len(set(labels)) == len(labels) or depth == max_depth:
                for r, lab in zip(same, labels):
                    r["所在"] = lab
                    r["顯示名稱"] = f"{r['名稱']}（{lab}）"
                break


# ---------------------------------------------------------------------------
# 片頭、片尾（10-07 #146 第一步：先能選、存進工作區設定；組裝時接上留給下一批）
# ---------------------------------------------------------------------------

EXTRA_PURPOSES = ("片頭", "片尾")
HANDOFF_VIDEO = "交接原片"   # 33 號設計 #258：匯入交接包第 ② 格選的原片（跟「選影片」同一套：WSL2 會複製進 Ubuntu）
PICK_PURPOSES = ("影片", *EXTRA_PURPOSES, HANDOFF_VIDEO)
PICK_TITLES = {"影片": "選讀書會的影片", "片頭": "選片頭影片（可以不選）", "片尾": "選片尾影片（可以不選）",
               HANDOFF_VIDEO: "選從雲端空間下載的原片"}
_extras_lock = threading.Lock()


def workspace_settings_path(workdir: Path) -> Path:
    """工作區設定：`<工作區>/工作區設定.json`（10-07 起只放片頭、片尾）。"""
    return Path(workdir) / "工作區設定.json"


def _file_info(p: Path, origin: Path | None = None) -> dict:
    p = Path(p)
    try:
        size = round(p.stat().st_size / 1e6, 1)
    except OSError:
        size = None
    return {"路徑": str(p), "檔名": p.name, "大小MB": size, "原本的位置": str(origin) if origin else None}


def save_extra(workdir: Path, purpose: str, path: Path | None, origin: Path | None = None) -> dict:
    """把片頭或片尾記進工作區設定（path=None＝拿掉）。先寫暫存檔再換上（`write_json`）。"""
    if purpose not in EXTRA_PURPOSES:
        raise ValueError(f"只能設定片頭或片尾：{purpose}")
    with _extras_lock:
        p = workspace_settings_path(workdir)
        data = read_json(p, default=None) or {}
        if path is None:
            data.pop(purpose, None)
        else:
            data[purpose] = {**_file_info(path, origin), "選的時間": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        write_json(p, data)
    return load_extras(workdir)


def load_extras(workdir: Path) -> dict:
    """工作區設定裡的片頭、片尾（加「檔案還在」）。"""
    data = read_json(workspace_settings_path(workdir), default=None) or {}
    out = {}
    for k in EXTRA_PURPOSES:
        v = data.get(k)
        out[k] = {**v, "檔案還在": Path(v["路徑"]).is_file()} if isinstance(v, dict) and v.get("路徑") else None
    return out


TAKE_LATEST = "最新組裝的成品"   # 10-13 審查修正二（建議 4）：final_outputs 用開頭判斷 `拿檔是最新那支`


def final_target(workdir: Path) -> tuple[Path | None, str | None, dict | None]:
    """第 5 步要拿出來的成品：按過「輸出成品」的最終成品優先，沒有就用正在檢查的那一支。
    路徑只從工作區自己的紀錄算，不收網頁傳來的路徑。回傳 (路徑, 是哪一種, 過期資訊)。
    10-13 bug 二（宇軒：輸出後又重做，拿檔按鈕還是拿到重做前的最終成品）：上次輸出的比現在的成品舊
    （`finalcheck.export_stale`）就跳過最終成品，改拿現在的成品、標「還沒重新輸出」；第三個值是
    export_stale 的結果（{原因, 說明, 舊檔}），沒過期是 None。「現在的成品」經過 `_current`（新組裝完還沒打開
    第 5 步也算新的），正在檢查的是舊的那支時改拿最新那支（`finalcheck.take_target`），不會把舊檔拿出去。"""
    from bookclub import finalcards, finalcheck

    workdir = Path(workdir)
    try:   # 10-15 第5步2.0 第3段（31 號設計 §6.7）：新紀錄能用時，拿「最新一次輸出」（沒輸出過拿最新一版的成品），不再判斷過期
        new = finalcards.take(workdir)
    except Exception:  # noqa: BLE001 — 讀不到就照 0.2.1b3 的做法
        new = None
    if new is not None:
        return safe_join(workdir, new["路徑"]), new["是"], None
    tg = finalcheck.take_target(workdir)
    out, stale = tg["輸出成品"] or {}, tg["過期"]
    if out.get("檔案") and not stale:
        p = safe_join(workdir, out["檔案"])
        if p.is_file():
            return p, "最終成品", None
    if tg["現在"]:
        if tg["換成最新"]:
            what = TAKE_LATEST + "（第 5 步正在檢查的不是這支" + ("；還沒重新輸出）" if stale else "）")
        else:
            what = "還在檢查的成品（還沒重新輸出）" if stale else "還在檢查的成品"
        return safe_join(workdir, tg["現在"]), what, stale
    return None, None, None


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

CLAUDE_MISSING = ("找不到 claude 指令（PATH 和 ~/.local/bin 都沒有）：段落分析、建議刪除段落、人名清單會沒跑成功，"
                  "第 3 步沒有學員段落。先裝好 Claude Code、登入一次（bookclub doctor 可以檢查），再按開始分析")


def find_claude() -> str | None:
    from bookclub.system_info import find_claude as _find

    return _find()


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
        from bookclub.workdir import find_video, localize

        video_path = find_video(workdir) or localize(analysis["video"], workdir)   # 10-02 第五批

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

    # 10-16 宇軒：側欄第 3 步下面的子步驟「3-1 開始前 4 件事」完成打勾（4 件都標做完了）
    try:
        from bookclub.review import PREP_KEYS, load_decisions

        prep = load_decisions(workdir).get("開始前確認") or {}
        prep_state = {"done": all(prep.get(k) for k in PREP_KEYS)}
    except Exception:  # noqa: BLE001 — 讀不到不擋側欄
        prep_state = {"done": False}

    return {
        "proofread": proofread_state,
        "開始前": prep_state,
        "workdir": str(workdir),
        "專案標示": project_label(workdir),   # 10-08：頂端常駐「現在在：名稱（上一層）」
        "已結案": _closed(workdir), "結案": _closed_info(workdir),   # 1015-8 第 3 段：每一步頂端那一句
        "video": {
            "path": str(video_path) if video_path else None,
            "name": video_path.name if video_path else None,
            "duration_s": duration,
            "duration_hms": fmt_time(duration) if duration else None,
        },
        "substeps": substeps,
        "總耗時_s": elapsed.get("總耗時"),
        "有分析結果檔": bool(analysis),
        # 10-02 第七批（A3＋B3）：Claude 那幾步沒跑成功要看得到；開始分析前先看找不找得到 claude
        "要注意": analysis.get("要注意") or [],
        "Claude沒跑成功": analysis.get("Claude沒跑成功") or [],
        "找得到claude": bool(find_claude()),
        "開發者": _developer(),   # 10-15 第5步2.0 第3段（31 號設計 §5.3）：開發者設定（settings.toml `developer = true`）
    }


def _developer() -> bool:
    from bookclub import config

    return config.developer()


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
        if rank == record.get("選定名次") and (rd / "ref.txt").exists():
            # 09-30：已經選定的那一個顯示人校對過的逐字稿（ref.txt），不然再按一次存檔會把校對蓋回初稿
            transcript = (rd / "ref.txt").read_text(encoding="utf-8")
        audio_url = None
        if wav_path.exists():
            rel = str(wav_path.relative_to(workdir))
            audio_url = f"/api/audio?path={quote(rel)}"
        same = None
        if rank == record.get("選定名次"):
            from bookclub.refpick import same_audio

            same = same_audio(wav_path, rd / "ref.wav")   # 10-01：重切過的候選跟還在用的舊音檔不是同一份
        candidates.append({
            "rank": rank,
            "取樣率": _wav_rate(wav_path) if wav_path.exists() else None,
            "跟現在用的音檔一樣": same,
            "原片時間": _candidate_time_desc(a),
            "長度秒": a.get("compressed_duration"),
            "字數": a.get("字數", len(transcript)),
            "score": a.get("score"),
            "transcript": transcript,
            "音檔網址": audio_url,
        })

    ref_wav = rd / "ref.wav"
    return {
        "總數": len(candidates),
        "已選定名次": record.get("選定名次"),
        "現在用的音檔取樣率": _wav_rate(ref_wav) if ref_wav.exists() else None,
        "candidates": candidates,
    }


def refs_use(workdir: Path, rank: int, transcript: str, replace_audio: bool | None = None) -> dict:
    """選定某一名次，存成 `ref.wav`／`ref.txt`（呼叫 `bookclub/refpick.py` 的
    `finalize_reference`，這支只是把 API 參數轉一手）。已選定的那一個預設只存逐字稿（10-01）。"""
    from bookclub.refpick import finalize_reference

    return finalize_reference(workdir, rank, transcript, replace_audio)


def _wav_rate(p: Path) -> int | None:
    try:
        import soundfile as sf

        return int(sf.info(str(p)).samplerate)
    except Exception:  # noqa: BLE001 — 讀不到就不顯示
        return None


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



def _remove_exclusion(term: str) -> bool:
    """從 `名字排除清單.csv` 拿掉這個詞（09-30：名字卡「不是名字，不用改」取消時，連排除清單一起拿掉）。
    正規化後相同的都拿掉；其他列照原樣留著。回傳有沒有真的拿掉。"""
    import os

    path = data_dir() / EXCLUSION_CSV_NAME
    norm = names_mod._normalize_match_text(term)
    if not norm or not path.exists():
        return False
    rows = names_mod.load_exclusion_list(path)
    keep = [r for r in rows if names_mod._normalize_match_text(r["詞"]) != norm]
    if len(keep) == len(rows):
        return False
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["詞", "原因", "建立日期"])
        for r in keep:
            writer.writerow([r["詞"], r["原因"], r["建立日期"]])
    os.replace(tmp, path)
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
        self.exec_state: dict = self._fresh_run_state()   # 第 4 步「開始執行」（09-29），跟影片分析同一時間只跑一個
        self.pick_lock = threading.Lock()   # 10-07 檔案進出
        self.picks: dict[str, dict] = {}     # 選了還沒用的：影片、給新影片的片頭片尾
        self.copy_jobs: dict = {}            # 複製工作（Windows 的檔複製進 Ubuntu、成品複製到 Windows 的下載）
        self.pick_token: dict[tuple[str, str], str] = {}   # 每個 (用途, 去處) 最新的那一次選擇（見 accept_pick 上面的說明）
        self.exporting = False                     # 「複製成品到 Windows」正在準備（找下載資料夾）
        self.close_job = None                      # 1015-8：結案打包（closing.PackJob；同時只有一個，佔 pick_lock）
        self.handoff_job = None                    # 33 號設計 #257：交接打包（handoff.PackJob；同時只有一個，佔 pick_lock）
        self.import_job = None                     # 33 號設計 #258：匯入交接包（handoff.ImportJob）
        self.handoff_zip: dict | None = None       # 匯入第 ① 格選好的交接包 {路徑, 檔名, 摘要}
        self.final_exporting = 0                   # 第 5 步「輸出成品」正在複製最終成品的個數（跟結案打包互擋，佔 pick_lock；
        #                                            第 2 段審查建議 (a)：計數器，同時兩個時第一個做完不會清成 0）
        try:   # 上次複製到一半伺服器被關掉留下的暫存檔（只清這個工具自己取名的 .*.複製中）
            from bookclub import fileio

            n = fileio.cleanup_partials(fileio.import_dir())
            if n:
                print(f"[網頁伺服器] 清掉 {n} 個上次複製到一半留下的暫存檔")
        except Exception as e:  # noqa: BLE001 — 清不掉不擋啟動
            print(f"⚠️ [網頁伺服器] 清不掉上次複製到一半的暫存檔：{e}")

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
            if self.run_state["running"] or self.exec_state["running"]:
                raise ValueError("分析或 AI 執行還在跑，等它結束再切換專案")
            self._workdir = Path(workdir)
            self.video = Path(video) if video else None
            self.run_state = self._fresh_run_state()
            self.exec_state = self._fresh_run_state()
        try:
            remember_current(self._workdir)
        except OSError as e:   # 記不下來只是下次重開要重選，不擋切換
            print(f"⚠️ [網頁伺服器] 記不下目前的專案：{e}")
        _mark_interrupted(self._workdir)

    # ---------- 10-07 檔案進出：系統選檔視窗、Windows 的檔案複製進來、成品拿出去 ----------

    def _job_view(self, j) -> dict:
        """複製工作給網頁看的樣子；「給」照這個工作現在要寫去哪裡算（開始分析後片頭片尾會從「新影片」改成那個專案）。"""
        d = j.to_dict()
        proj = getattr(j, "project", None)
        if j.purpose in EXTRA_PURPOSES:
            cur = self._workdir
            d["給"] = "新影片" if proj is None else ("目前" if cur is not None and Path(proj) == Path(cur) else "別的專案")
        return d

    def picks_state(self) -> dict:
        """`GET /api/picks`：總覽要的選檔狀態（選了哪支、片頭片尾、複製進度）。"""
        from bookclub import fileio

        with self.pick_lock:
            picks = {k: {kk: vv for kk, vv in v.items()} for k, v in self.picks.items()}
            jobs = [self._job_view(j) for j in self.copy_jobs.values() if j.purpose in PICK_PURPOSES]
        kind = fileio.platform_kind()
        cur = None
        if self.has_workdir:
            try:
                cur = load_extras(self.workdir)
            except (OSError, ValueError) as e:   # 設定檔壞掉：畫面照樣打得開，說一聲
                cur = {"錯誤": f"讀不了工作區設定：{e}"}
        return {"系統": kind, "選檔方式": fileio.pick_method(kind), "選了": picks, "目前的專案": cur, "複製": jobs}

    def pick(self, purpose: str, target: str = "新影片", title: str | None = None) -> dict:
        """`POST /api/pick`：叫系統的選檔視窗。叫不起來或按取消 → {退回網頁: True, 說明}，網頁改用資料夾瀏覽。"""
        from bookclub import fileio

        if purpose not in PICK_PURPOSES:
            raise ValueError(f"不認得要選什麼：{purpose}")
        if purpose in EXTRA_PURPOSES and target == "目前" and not self.has_workdir:
            raise NoProject("還沒有目前的專案：片頭片尾先在「選影片」那裡選，開始分析時會一起記進新專案")
        t0 = time.time()
        fileio.timing_log(f"收到「選{purpose}」的請求，開始叫選檔視窗"
                          f"（Mac 常駐小程式{'已經準備好' if fileio.MAC_PICKER.usable() else '還沒準備好，這次叫 osascript'}）"
                          if fileio.platform_kind() == "mac" else f"收到「選{purpose}」的請求，開始叫選檔視窗")
        try:
            p = fileio.pick_file(title or PICK_TITLES[purpose])
        except fileio.PickUnavailable as e:
            fileio.timing_log(f"叫不起來，退回網頁瀏覽（{time.time() - t0:.1f} 秒）")
            return {"退回網頁": True, "說明": f"{e}。改用下面的資料夾瀏覽選檔案。"}
        fileio.timing_log(f"這次請求共 {time.time() - t0:.1f} 秒（含人在視窗裡選的時間）")
        if p is None:
            return {"取消": True, "退回網頁": True,
                    "說明": "沒有選檔案（按了取消）。要再選一次就再按一次按鈕，也可以在下面的資料夾裡找。"}
        return self.accept_pick(purpose, fileio.check_video(p), target)

    # 「最新的那一次選擇」記號（10-07 審查必改）：鍵是 (用途, 專案路徑或「新影片」)，值是那一次的編號
    # （要複製的用複製工作的 id）。複製做完時只有記號還是自己的才寫，被後來重選、拿掉、取消蓋掉的就不寫。
    # 片頭片尾在開始分析時還在複製：把那個複製工作的 `project` 改成新專案、記號搬過去（都在 pick_lock 裡做）。

    @staticmethod
    def _pick_key(purpose: str, project: Path | None) -> tuple[str, str]:
        return (purpose, str(project) if project is not None else "新影片")

    def _supersede(self, key: tuple[str, str]) -> None:
        """（要先拿 pick_lock）同一個用途、同一個去處重選：上一個還在複製的取消掉。別的去處的不動。"""
        for j in self.copy_jobs.values():
            if j.running and self._pick_key(j.purpose, getattr(j, "project", None)) == key:
                j.cancel()
        self.pick_token.pop(key, None)

    def accept_pick(self, purpose: str, p: Path, target: str = "新影片") -> dict:
        """選到一支影片之後：WSL2 的 Windows 檔先背景複製進 Ubuntu；其他照用。
        影片 → 記在記憶體，按「開始分析」才用；片頭片尾 → 給目前的專案就寫進工作區設定，給新影片就等開始分析時一起寫。"""
        import uuid

        from bookclub import fileio

        if purpose not in PICK_PURPOSES:
            raise ValueError(f"不認得要選什麼：{purpose}")
        to_current = purpose in EXTRA_PURPOSES and target == "目前"
        project = self.workdir if to_current else None
        key = self._pick_key(purpose, project)
        info = {"用途": purpose, "給": "目前" if to_current else "新影片", "檔名": p.name}
        if fileio.needs_import(p):
            dest, reused = fileio.plan_import(p)   # 空間不夠丟 ValueError（訊息說要多少）
            job = fileio.CopyJob(p, dest, purpose=purpose, reused=reused,
                                 on_done=lambda j: self._picked(j.purpose, j.dest, j.src, j.id, job=j))
            job.project = project
            job.target = info["給"]
            with self.pick_lock:
                self._supersede(key)
                self.pick_token[key] = job.id
                self.copy_jobs[job.id] = job
                if project is None:
                    self.picks[purpose] = {**_file_info(p), "路徑": None, "原本的位置": str(p), "複製": job.id}
            job.start()
            return {**info, "要複製": True, "複製": self._job_view(job), "原本的位置": str(p)}
        token = uuid.uuid4().hex[:12]
        with self.pick_lock:
            self._supersede(key)
            self.pick_token[key] = token
        self._picked(purpose, p, None, token, project=project)
        return {**info, "要複製": False, "路徑": str(p)}

    def _picked(self, purpose: str, path: Path, origin: Path | None, token: str, *, job=None,
                project: Path | None = None) -> None:
        """選好（或複製好）了：記號還是自己的才寫。去處看複製工作現在的 `project`（開始分析時可能被改成新專案）。"""
        with self.pick_lock:
            if job is not None:
                project = getattr(job, "project", None)
            key = self._pick_key(purpose, project)
            if self.pick_token.get(key) != token:
                return   # 後來又重選、拿掉或取消了
            if project is None:
                self.picks[purpose] = _file_info(path, origin)
                return
            self.pick_token.pop(key, None)
            save_extra(project, purpose, path, origin)

    def take_picked_video(self) -> Path:
        from bookclub import fileio

        with self.pick_lock:
            pk = dict(self.picks.get("影片") or {})
            job = self.copy_jobs.get(pk.get("複製")) if pk.get("複製") else None
        if not pk:
            raise ValueError("還沒選影片：先按「選影片」")
        if not pk.get("路徑"):
            if job and job.running:
                raise ValueError("影片還在複製進 Ubuntu，等複製完再按「開始分析」")
            raise ValueError("影片沒有複製完成（取消或失敗了），重新選一次")
        return fileio.check_video(Path(pk["路徑"]))

    def apply_new_extras(self, d: Path) -> None:
        """開始分析時：給新影片選的片頭片尾寫進新專案；還在複製的，把那個複製工作的去處改成新專案，複製好再寫。
        整段在 pick_lock 裡（複製剛好在中間做完也不會寫錯地方：做完的那一刻要等這段結束才拿得到鎖）。"""
        with self.pick_lock:
            self.picks.pop("影片", None)
            self.pick_token.pop(self._pick_key("影片", None), None)
            for k in EXTRA_PURPOSES:
                v = self.picks.pop(k, None)
                old = self._pick_key(k, None)
                token = self.pick_token.pop(old, None)
                if not v:
                    continue
                new = self._pick_key(k, d)
                self._supersede(new)
                if v.get("路徑"):
                    save_extra(d, k, Path(v["路徑"]), Path(v["原本的位置"]) if v.get("原本的位置") else None)
                    continue
                job = self.copy_jobs.get(v.get("複製"))
                if job is not None and job.state not in ("取消", "失敗") and token == job.id:
                    job.project = Path(d)
                    self.pick_token[new] = job.id

    def forget_pick(self, purpose: str) -> None:
        """給新影片選的拿掉（目前專案的用 clear_extra）。"""
        with self.pick_lock:
            self.picks.pop(purpose, None)
            self._supersede(self._pick_key(purpose, None))

    def clear_extra(self, purpose: str) -> dict:
        """目前專案的片頭或片尾拿掉；還在複製的一起取消。"""
        w = self.workdir
        if purpose not in EXTRA_PURPOSES:
            raise ValueError(f"只能設定片頭或片尾：{purpose}")
        with self.pick_lock:
            self._supersede(self._pick_key(purpose, w))
        return save_extra(w, purpose, None)

    def cancel_copy(self, job_id: str) -> dict:
        with self.pick_lock:
            job = self.copy_jobs.get(job_id)
            if job is None:
                raise FileNotFoundError("找不到這個複製工作（可能伺服器重開過）")
            job.cancel()
            for k, v in list(self.picks.items()):
                if v.get("複製") == job_id and not v.get("路徑"):
                    self.picks.pop(k)
            for key, tok in list(self.pick_token.items()):
                if tok == job_id:
                    self.pick_token.pop(key)
        return {"ok": True}

    def final_outputs(self) -> dict:
        """`GET /api/final/outputs`：第 5 步「打開成品資料夾」「複製到 Windows 的下載資料夾」要的狀態。"""
        from bookclub import fileio

        target, what, stale = final_target(self.workdir)
        kind = fileio.platform_kind()
        with self.pick_lock:
            jobs = [j.to_dict() for j in self.copy_jobs.values() if j.purpose == "成品"]
        return {"系統": kind, "成品": target.name if target else None, "成品是": what,
                "可以複製到Windows": kind == "wsl", "複製": jobs[-1:] if jobs else [],
                # 10-13 bug 二：上次輸出的最終成品比現在的成品舊（null＝沒過期）——按鈕旁黃字、複製到 Windows 先問
                "上次輸出過期": {"說明": stale["說明"], "舊檔": stale["舊檔"], "原因": stale["原因"]} if stale else None,
                # 10-13 審查修正二（建議 4）：拿的是最新那支（不是第 5 步正在檢查的這支）——網頁照這欄寫說明
                "拿檔是最新那支": bool(what and what.startswith(TAKE_LATEST)),
                # 10-15 第5步2.0 第2段：上次輸出的是第幾版、最新是第幾版（拿檔按鈕旁只在不是最新時寫一行）
                "版本": self._export_versions()}

    def _export_versions(self) -> dict | None:
        """10-15 第5步2.0 第3段：拿檔按鈕拿的是第幾版、最新是第幾版（只有拿的是最終成品、新紀錄能用時才有）。"""
        from bookclub import finalcards

        try:
            t = finalcards.take(self.workdir)
            return {"輸出版本": t["輸出版本"], "最新版本": t["最新版本"]} if t and t["輸出版本"] else None
        except Exception:  # noqa: BLE001 — 讀不到就照舊的說法
            return None

    def final_reveal(self) -> dict:
        from bookclub import fileio

        target, _, _ = final_target(self.workdir)   # 10-13 bug 二：過期時 final_target 已改指正在檢查的成品，不擋
        where = target or (self.workdir / "輸出")
        if not where.exists():
            raise FileNotFoundError("還沒有成品資料夾（輸出/），先在第 4 步組裝")
        try:
            return {"ok": True, "說明": fileio.reveal(where)}
        except RuntimeError as e:   # 叫不起檔案總管：畫面上說人話（不要出現「RuntimeError：」）
            raise ValueError(str(e)) from None

    def final_to_windows(self, confirm: bool = False) -> dict:
        from bookclub import fileio

        if fileio.platform_kind() != "wsl":
            raise ValueError("這個按鈕只在 Windows（WSL2）上用得到")
        target, what, stale = final_target(self.workdir)
        if target is None:
            raise FileNotFoundError("還沒有成品影片，先在第 4 步組裝")
        if stale and not confirm:   # 10-13 bug 二：上次輸出的是重做前的版本，現在這支還沒輸出過——先問一次再複製
            return {"ok": False, "要確認": True, "成品是": what, "說明": stale["說明"], "舊檔": stale["舊檔"]}
        ver = self._export_versions()
        if ver and ver["輸出版本"] != ver["最新版本"] and not confirm:
            # 10-15 第3段審查修正（建議，宇軒定前暫定）：拿的最終成品不是最新一版（例如在舊版按了輸出）——先問一次再複製
            return {"ok": False, "要確認": True, "成品是": what, "版本": ver,
                    "說明": f"這是第 {ver['輸出版本']} 版的，最新是第 {ver['最新版本']} 版", "舊檔": None}
        with self.pick_lock:   # 「有沒有在跑」與「佔位子」同一段鎖：連按兩下也只會有一個
            if self.exporting or any(j.purpose == "成品" and j.running for j in self.copy_jobs.values()):
                raise ValueError("上一次的複製還沒做完")
            if self.close_job is not None and self.close_job.running:   # 1015-8：結案打包在存，不同時複製
                raise ValueError("結案打包正在存，等它存完再複製")
            self.exporting = True
        try:
            try:
                downloads = fileio.windows_downloads()
            except RuntimeError as e:
                raise ValueError(str(e)) from None
            dest = fileio.plan_export(target, downloads)
            job = fileio.CopyJob(target, dest, purpose="成品")
            with self.pick_lock:
                self.copy_jobs[job.id] = job
            job.start()
        finally:
            with self.pick_lock:
                self.exporting = False
        return {"ok": True, "成品是": what, "複製": job.to_dict()}

    # ---------- 結案打包（1015-8，32 號設計 §3，以 §1a 為準） ----------

    def _rescan_running(self) -> bool:
        """1017 總審查：③ 背景補找老師提到的名字**真的在跑**（只看，不順手開補找；`rescan_busy` 會開）。"""
        from bookclub import personnames

        return self.has_workdir and personnames.rescan_running(self.workdir)

    def _close_busy(self) -> str | None:
        """不能開始打包的原因（§3.6）；沒有＝None。要在 pick_lock 外面呼叫（會拿 run_lock）。"""
        if self.exec_running():
            return "第 4 步正在跑，跑完才能結案打包。"
        if self.run_status_running():
            return "影片分析正在跑，跑完才能結案打包。"
        if self._rescan_running():
            return "還在找老師提到的名字，等它做完再結案打包。"
        with self.pick_lock:
            if self.final_exporting:
                return "成品正在輸出，等它做完再結案打包。"
            if self.exporting or any(j.purpose == "成品" and j.running for j in self.copy_jobs.values()):
                return "成品正在複製，複製完才能結案打包。"
            if self.close_job is not None and self.close_job.running:
                return "結案打包正在存，等它存完。"
        return None

    def close_state(self) -> dict:
        """`GET /api/close/state`：狀態、可以打包＋不行的原因、位置選項、打包大小、最後輸出版本、最新版本、最近一次、工作。只讀。"""
        from bookclub import closing, fileio

        st = closing.pack_state(self.workdir)
        why = self._close_busy()
        job = self.close_job
        if not st["輸出過"] and not st["只有紀錄"]:
            why = why or "還沒輸出過成品：先在第 5 步按「輸出成品」。"
        if st["成品不在"]:
            why = why or closing.MISSING_PRODUCT
        # 第 2 段審查必修 3：不在這裡問位置選項（WSL 上找下載資料夾要啟動 PowerShell，畫面每秒輪詢）——另外問 /api/close/locations
        return {**st, "可以打包": why is None, "不行的原因": why, "系統": fileio.platform_kind(),
                "系統視窗": fileio.folder_picker_enabled(),
                "工作": job.to_dict() if job is not None and job.workdir == self.workdir else None}

    def close_locations(self) -> dict:
        """`GET /api/close/locations`：存到哪的選項（下載、D 槽、自己選，各剩多少）。WSL 上會問 PowerShell 下載資料夾在哪，
        只在要用的時候叫，不放進每秒輪詢的 /api/close/state。"""
        from bookclub import closing

        return {"位置選項": closing.pack_locations()}

    def close_reveal(self) -> dict:
        """`POST /api/close/reveal`：在檔案總管（Finder）打開最近一次核對通過的打包資料夾（路徑是 結案.json 記的，不收網頁的路徑）。"""
        from bookclub import closing, fileio

        ok = next((x for x in reversed(closing.read_record(self.workdir).get("打包") or []) if x.get("核對") == "通過"), None)
        folder = Path(ok["資料夾"]) if ok and ok.get("資料夾") else None
        if folder is None or not folder.is_dir():
            raise ValueError("還沒有存好的打包（或存的地方現在讀不到，D 槽沒插？）")
        try:
            return {"ok": True, "說明": fileio.reveal(folder)}
        except RuntimeError as e:
            raise ValueError(str(e)) from None

    def close_pick_folder(self) -> dict:
        """`POST /api/close/pick-folder`：叫系統的選資料夾視窗。按取消 → {取消: True}（什麼都不動）；叫不起來 →
        {退回網頁: True, 說明}（網頁改成打路徑的框）。"""
        from bookclub import closing, fileio

        try:
            # 10-19 #252：視窗上的說明跟第 5 步框裡那一句一樣（選上次的「讀書會剪輯結案」也不會多一層）
            p = fileio.pick_folder("選放結案資料的地方（例如下載、D 槽）；選上次的「讀書會剪輯結案」也可以")
        except fileio.PickUnavailable as e:
            return {"退回網頁": True, "說明": str(e)}
        if p is None:
            return {"取消": True}
        kind = fileio.platform_kind()
        disp = closing.windows_display(p) if kind == "wsl" else str(p)
        return {"路徑": str(p), "位置顯示": disp, "提醒": closing.ubuntu_inside_warning(kind, p)}

    def close_pack(self, body: dict) -> dict:
        """`POST /api/close/pack` {位置: 下載｜D｜自己選, 路徑?, 繼續?}：先檢查（§3.6），通過就開背景工作，回 `工作`。"""
        from bookclub import closing, fileio

        why = self._close_busy()
        if why:
            raise ValueError(why)
        rec = closing.read_record(self.workdir)
        records_only = rec.get("狀態") == closing.CLOSED
        if not closing.last_export(self.workdir) and not records_only:
            raise ValueError("還沒輸出過成品：先在第 5 步按「輸出成品」。")
        resume = bool(body.get("繼續"))
        kind = fileio.platform_kind()
        if resume:
            last = (rec.get("打包") or [None])[-1] or {}
            loc, disp = last.get("位置"), last.get("位置顯示")
            if not loc:
                raise ValueError("沒有存到一半的打包，請按「結案打包…」選位置。")
        else:
            code = str(body.get("位置") or "自己選")
            if code == "自己選":
                loc = str(body.get("路徑") or "").strip()
                if not loc or not Path(loc).expanduser().is_absolute():
                    raise ValueError("請選一個資料夾（要完整的路徑）。")
            else:
                hit = next((x for x in closing.pack_locations(kind) if x["代號"] == code and x["路徑"]), None)
                if hit is None:
                    raise ValueError("找不到這個位置（D 槽沒插？），換個位置再試。")
                loc = hit["路徑"]
            disp = closing.windows_display(loc) if kind == "wsl" else str(Path(loc).expanduser())
        try:
            items = closing.pack_items(self.workdir, records_only=records_only)
            if resume:   # 建議：繼續的空間檢查扣掉已經存好的部分
                need = closing.remaining_bytes(items, closing.resume_folder((rec.get("打包") or [None])[-1]))
            else:
                need = closing.pack_size(items)
            locp = closing.check_location(self.workdir, loc, need)
        except closing.PackError as e:
            raise ValueError(str(e)) from None
        # 第 3 段（§1a 第 3 點）：還沒打包好過（第一次、或存到一半繼續）而且帶了畫面上的清單指紋 → 存好核對全對就接著清
        fp = body.get("清單指紋")
        then_clean = fp if isinstance(fp, str) and fp and not records_only and \
            rec.get("狀態") in (None, closing.PACKING) else None
        job = closing.PackJob(self.workdir, locp, display=disp, resume=resume, records_only=records_only,
                              clean_fp=then_clean, busy=self._clean_busy)
        with self.pick_lock:
            if self.final_exporting:
                raise ValueError("成品正在輸出，等它做完再結案打包。")
            if (self.close_job is not None and self.close_job.running) or self.exporting \
                    or any(j.purpose == "成品" and j.running for j in self.copy_jobs.values()):
                raise ValueError("上一個存檔的工作還沒做完")
            # 第 2 段審查必修 2：先佔位子、再看第 4 步／分析有沒有剛好開始（start_execute／start_analyze 在 run_lock 裡看
            # close_job）——兩邊一定有一邊看得到另一邊，不會同時開始
            self.close_job = job
            if self.exec_running() or self.run_status_running():
                self.close_job = None
                raise ValueError("第 4 步或影片分析剛開始跑，跑完才能結案打包。")
        job.start()
        return {"ok": True, "工作": job.to_dict(), "提醒": closing.ubuntu_inside_warning(kind, locp)}

    def close_packing(self) -> bool:
        """結案打包正在存、或正在清（第 4 步開始、輸出成品、影片分析都要看這個）。"""
        job = self.close_job
        return bool(job is not None and job.running)

    def close_cancel(self) -> dict:
        """`POST /api/close/cancel`：停止進行中的打包（存好的留著，下次「繼續」只補缺的）。"""
        job = self.close_job
        if job is None or not job.running:
            raise ValueError("現在沒有在結案打包")
        if job.state == "清理中":   # §4.4：清理開始後不給停止
            raise ValueError("已經在清了，清理開始後不能停（幾秒就好）。")
        job.cancel()
        return {"ok": True}

    def _clean_busy(self) -> str | None:
        """清之前（§4.3 第 4 點）：第 4 步、影片分析、輸出成品、複製有沒有在跑（回原因或 None）。不看結案打包自己
        （存好接著清時，打包工作還算在跑）。#236 的更新指令也看同一個 `/api/run/status`。"""
        if self.exec_running():
            return "第 4 步正在跑，跑完才能清。"
        if self.run_status_running():
            return "影片分析正在跑，跑完才能清。"
        if self._rescan_running():
            return "還在找老師提到的名字，等它做完再清。"
        with self.pick_lock:
            if self.final_exporting:
                return "成品正在輸出，等它做完再清。"
            if self.exporting or any(getattr(j, "running", False) for j in self.copy_jobs.values()):
                return "正在複製檔案，複製完才能清。"
        return None

    def close_clean(self, body: dict) -> dict:
        """`POST /api/close/clean` {清單指紋} 或 {繼續: true}（§4.3、§4.4）：再確認都過了就開背景工作清；「繼續清」照上次記的清單做完。"""
        from bookclub import closing

        st = closing.state(self.workdir)
        if st == closing.CLOSED:
            raise ValueError("這支影片已結案了，沒有東西可以清。")
        if body.get("繼續"):
            if st != closing.CLEANING:
                raise ValueError("沒有清到一半的東西。")
            try:   # 第二輪專審：開發者 --restore 暫停中不能繼續清
                closing.paused_check(closing.read_record(self.workdir))
            except closing.CleanError as e:
                raise ValueError(str(e)) from None
            job = closing.CleanJob(self.workdir, resume=True)
        else:
            fp = str(body["清單指紋"])
            why = self._close_busy()
            if why:
                raise ValueError(why)
            try:   # 先在這裡確認一次（不行馬上回一句）；背景工作開始時會再確認一次
                closing.clean_check(self.workdir, fp, busy=self._clean_busy)
            except closing.CleanError as e:
                raise ValueError(str(e)) from None
            job = closing.CleanJob(self.workdir, fp, busy=self._clean_busy)
        with self.pick_lock:
            if self.close_job is not None and self.close_job.running:
                raise ValueError("結案打包正在存，等它存完。")
            self.close_job = job   # 佔位子：第 4 步、輸出成品、分析開始前都看 close_packing()
            if self.exec_running() or self.run_status_running():
                self.close_job = None
                raise ValueError("第 4 步或影片分析剛開始跑，跑完才能清。")
        job.start()
        return {"ok": True, "工作": job.to_dict()}

    # ---------- 接力交接（33 號設計，#257–#260） ----------

    def handoff_running(self) -> bool:
        return any(j is not None and j.running for j in (self.handoff_job, self.import_job))

    def _handoff_busy(self, what: str = "交接打包") -> str | None:
        """不能開始交接打包／匯入的原因；沒有＝None。要在 pick_lock 外面呼叫（會拿 run_lock）。"""
        if self.exec_running():
            return f"第 4 步正在跑，跑完才能{what}。"
        if self.run_status_running():
            return f"影片分析正在跑，跑完才能{what}。"
        if self._rescan_running():
            return f"還在找老師提到的名字，等它做完再{what}。"
        with self.pick_lock:
            if self.final_exporting:
                return f"成品正在輸出，等它做完再{what}。"
            if self.close_job is not None and self.close_job.running:
                return f"結案打包正在做，等它做完再{what}。"
            if self.handoff_running():
                return "上一個交接打包或匯入還沒做完。"
        return None

    def handoff_video_title(self) -> str:
        name = ((self.handoff_zip or {}).get("摘要") or {}).get("原片檔名")
        return f"選從雲端空間下載的「{name}」" if name else PICK_TITLES[HANDOFF_VIDEO]

    def handoff_state(self) -> dict:
        """`GET /api/handoff/state`：交接打包（目前這一集）、匯入交接包（選了哪個包、哪支原片、做到哪）的狀態。只讀。"""
        from bookclub import fileio, handoff

        out: dict = {"系統": fileio.platform_kind(), "系統視窗": fileio.folder_picker_enabled()}
        job = self.handoff_job
        cur = self._workdir
        out["打包"] = job.to_dict() if job is not None and cur is not None and job.workdir == cur else None
        if cur is not None:
            why = self._handoff_busy()
            out["可以打包"] = {
                handoff.STAGE3: why or (None if (cur / "校對" / "段落.json").is_file() else "第 1 步影片分析還沒做完。"),
                handoff.STAGE5: why or (None if handoff.current_products(cur) else "還沒有成品：先到第 4 步組裝一次。")}
            out["已交出去"] = handoff.handed_over(cur)
            e = handoff.last_export(cur)
            out["最近一次"] = {k: e.get(k) for k in ("時間", "哪一段", "檔名", "位置顯示")} if e else None
        with self.pick_lock:
            pk = dict(self.picks.get(HANDOFF_VIDEO) or {})
            pjob = self.copy_jobs.get(pk.get("複製")) if pk.get("複製") else None
        ij = self.import_job
        out["匯入"] = {"交接包": {k: v for k, v in (self.handoff_zip or {}).items() if k != "清單"} or None,
                     "原片": pk or None, "複製": pjob.to_dict() if pjob else None,
                     "工作": ij.to_dict() if ij is not None else None}
        return out

    def handoff_pick_folder(self) -> dict:
        """`POST /api/handoff/pick-folder`：叫系統的選資料夾視窗（存交接包的地方）。取消 → {取消}；叫不起來 → {退回網頁, 說明}。"""
        from bookclub import closing, fileio

        try:
            p = fileio.pick_folder("選放交接包的地方（例如雲端空間的資料夾、下載）")
        except fileio.PickUnavailable as e:
            return {"退回網頁": True, "說明": str(e)}
        if p is None:
            return {"取消": True}
        kind = fileio.platform_kind()
        return {"路徑": str(p), "位置顯示": closing.windows_display(p) if kind == "wsl" else str(p),
                "提醒": closing.ubuntu_inside_warning(kind, p)}

    def handoff_pack(self, body: dict) -> dict:
        """`POST /api/handoff/pack` {段: 第3步｜第5步, 路徑}：檢查通過就開背景工作，回 `工作`。"""
        from bookclub import closing, fileio, handoff

        stage = str(body.get("段") or "")
        if stage not in handoff.STAGES:
            raise ValueError("不認得是第幾步的交接打包。")
        why = self._handoff_busy()
        if why:
            raise ValueError(why)
        loc = str(body.get("路徑") or "").strip()
        if not loc or not Path(loc).expanduser().is_absolute():
            raise ValueError("請選一個資料夾（要完整的路徑）。")
        try:
            items = handoff.pack_items(self.workdir, stage)
            locp = handoff.check_location(self.workdir, loc, handoff.items_size(items))
        except handoff.HandoffError as e:
            raise ValueError(str(e)) from None
        kind = fileio.platform_kind()
        disp = closing.windows_display(locp) if kind == "wsl" else str(locp)
        job = handoff.PackJob(self.workdir, locp, stage, display=disp)
        with self.pick_lock:
            if self.handoff_running() or (self.close_job is not None and self.close_job.running) or self.final_exporting:
                raise ValueError("上一個存檔的工作還沒做完")
            self.handoff_job = job
            if self.exec_running() or self.run_status_running():
                self.handoff_job = None
                raise ValueError("第 4 步或影片分析剛開始跑，跑完才能交接打包。")
        job.start()
        return {"ok": True, "工作": job.to_dict(), "提醒": closing.ubuntu_inside_warning(kind, locp)}

    def handoff_cancel(self) -> dict:
        job = self.handoff_job
        if job is None or not job.running:
            raise ValueError("現在沒有在交接打包")
        job.cancel()
        return {"ok": True}

    def handoff_clean_plan(self) -> dict:
        """`GET /api/handoff/clean-plan`：第 3 步交接打包之後可以清的（只寫總大小與清單指紋）。"""
        from bookclub import handoff

        plan = handoff.copy_clean_plan(self.workdir)
        return {"合計": plan["合計"], "清單指紋": plan["清單指紋"], "含原片複本": plan["含原片複本"],
                "檔數": len(plan["檔案"]), "有交接包": handoff.last_export(self.workdir) is not None}

    def handoff_clean_copy(self, body: dict) -> dict:
        """`POST /api/handoff/clean-copy` {清單指紋}：「清掉（建議）」。"""
        from bookclub import handoff

        why = self._handoff_busy("清")
        if why:
            raise ValueError(why)
        try:
            return {"ok": True, **handoff.clean_copy(self.workdir, str(body.get("清單指紋") or ""),
                                                     busy=self._clean_busy)}
        except handoff.HandoffError as e:
            raise ValueError(str(e)) from None

    def handoff_clean_episode(self, body: dict) -> dict:
        """`POST /api/handoff/clean-episode` {路徑}：總覽「清掉這集」（已交出去的才行）。"""
        from bookclub import handoff

        d = Path(str(body.get("路徑") or ""))
        if not d.is_dir() or not known_project(d):
            raise SecurityError(f"不是專案清單上的專案：{d}")
        is_cur = self._workdir is not None and Path(self._workdir).resolve() == d.resolve()
        why = self._handoff_busy("清")
        if why:
            raise ValueError(why)
        with self.pick_lock:
            if any(getattr(j, "running", False) for j in self.copy_jobs.values()):
                raise ValueError("正在複製檔案，複製完才能清。")
        try:
            r = handoff.clean_episode(d)
        except handoff.HandoffError as e:
            raise ValueError(str(e)) from None
        try:
            unregister_project(d)
        except (OSError, ValueError) as e:
            print(f"⚠️ [網頁伺服器] 專案清單沒拿掉這一集：{e}")
        if is_cur:
            with self.run_lock:
                self._workdir, self.video = None, None
        return {"ok": True, **r}

    def handoff_import_zip(self, body: dict) -> dict:
        """`POST /api/handoff/import/zip` {路徑?}：第 ① 格——叫系統選檔視窗選 zip（或打的路徑），先讀交接清單（很快）。"""
        from bookclub import fileio, handoff

        if self.handoff_running():
            raise ValueError("上一個匯入還沒做完。")
        typed = str(body.get("路徑") or "").strip()
        if typed:
            p = Path(typed).expanduser()
            if not p.is_absolute():
                raise ValueError("請打完整的路徑。")
        else:
            try:
                p = fileio.pick_file("選從雲端空間下載的交接包（zip）", exts=(".zip",))
            except fileio.PickUnavailable as e:
                return {"退回網頁": True, "說明": f"{e}。改在下面打交接包的完整路徑。"}
            if p is None:
                return {"取消": True}
        if p.suffix.lower() != ".zip" or not p.is_file():
            raise ValueError(f"這不是 zip 檔，或找不到：{p.name}")
        try:
            m = handoff.read_manifest(p)
        except handoff.HandoffError as e:
            raise ValueError(str(e)) from None
        self.handoff_zip = {"路徑": str(p), "檔名": p.name, "摘要": handoff.summary(m)}
        self.import_job = None
        return {"ok": True, "交接包": self.handoff_zip}

    def handoff_import_video(self, body: dict) -> dict:
        """`POST /api/handoff/import/video` {路徑}：第 ② 格叫不起系統視窗時，打的原片路徑（家目錄或 Windows 的磁碟）。"""
        from bookclub import fileio

        p = Path(str(body.get("路徑") or "").strip()).expanduser()
        if not p.is_absolute():
            raise ValueError("請打完整的路徑。")
        if not fileio.is_windows_mount(p):
            p = home_path(str(p))
        return self.accept_pick(HANDOFF_VIDEO, fileio.check_video(p))

    def handoff_import_reset(self) -> dict:
        if self.import_job is not None and self.import_job.running:
            raise ValueError("正在匯入，等它做完。")
        self.handoff_zip, self.import_job = None, None
        self.forget_pick(HANDOFF_VIDEO)
        return {"ok": True}

    def handoff_import_start(self, body: dict) -> dict:
        """`POST /api/handoff/import/start` {確認?}：兩格都選好了，開背景匯入（核對 → 原片比對 → 建新或換掉）。"""
        from bookclub import handoff

        if not self.handoff_zip:
            raise ValueError("先選交接包（第 ① 格）。")
        with self.pick_lock:
            pk = dict(self.picks.get(HANDOFF_VIDEO) or {})
            job = self.copy_jobs.get(pk.get("複製")) if pk.get("複製") else None
        if not pk:
            raise ValueError("先選原片（第 ② 格）。")
        if not pk.get("路徑"):
            raise ValueError("原片還在複製進 Ubuntu，等複製完再按。" if job and job.running
                             else "原片沒有複製完成（取消或失敗了），重新選一次。")
        why = self._handoff_busy("匯入交接包")
        if why:
            raise ValueError(why)
        ij = handoff.ImportJob(Path(self.handoff_zip["路徑"]), Path(pk["路徑"]), confirm=bool(body.get("確認")))
        ij.on_done = self._import_done
        with self.pick_lock:
            if self.handoff_running():
                raise ValueError("上一個匯入還沒做完。")
            self.import_job = ij
        ij.start()
        return {"ok": True, "工作": ij.to_dict()}

    def _import_done(self, job) -> None:
        """匯入好了：登記進專案清單、切到這一集（背景執行緒裡呼叫）。"""
        d = Path(job.result["工作區"])
        register_project(d)
        self.use_project(d, job.video)
        self.handoff_zip = None
        self.forget_pick(HANDOFF_VIDEO)

    def exec_running(self) -> bool:
        with self.run_lock:
            return bool(self.exec_state["running"])

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
        # 1015-8（#236：更新指令判斷能不能自己關網頁）：第 4 步有沒有在跑。只讀記憶體裡的 exec_state，不升級、不寫檔
        state["第4步執行中"] = self.exec_running()
        return state

    def start_execute(self, opts: dict) -> dict:
        """第 4 步「開始執行」：背景跑 `execute.run_execute`（老師名字 → 學員重念 → 組裝）。"""
        from bookclub import config
        from bookclub.execute import precheck

        # 10-15 第5步2.0 第3段（31 號設計 §5.3）：範圍「從／到」、「只重新組裝」、「以這一版為基礎再組裝」只有開發者設定開著才收；
        # 老師的設定檔一律整支、照卡片重做（網頁上看不到這幾樣，API 收到也不理）
        opts = dict(opts or {})
        base = opts.pop("基礎版本", None)
        if not config.developer():
            opts.update({"start": None, "end": None, "只重新組裝": False})
            base = None
        with self.run_lock:
            if self.run_state["running"] or self.exec_state["running"]:
                return {"started": False, "error": "已經有分析或 AI 執行在跑，等它結束再按一次"}
            if self.close_packing():   # 1015-8 第 2 段審查必修 2
                return {"started": False, "error": "結案打包正在存，等它存完再開始第 4 步。"}
            if _handoff_running(self):   # 33 號設計：交接打包、匯入交接包做到一半不開始
                return {"started": False, "error": "交接打包或匯入交接包正在做，等它做完再開始第 4 步。"}
            pre = precheck(self.workdir)
            if not pre["可以開始"]:
                return {"started": False, "error": "還不能開始：" + "；".join(pre["缺"])}
            if not opts.get("只重新組裝") and not groq_key_ready():   # 10-04 #110：沒金鑰就不檢查念對沒有，先擋下
                return {"started": False, "error": groq_key_execute_message()}
            # 10-08 宇軒（流程簡化）：開始前總檢查不再擋——「一定要處理」「請看一眼」沒處理＝預設略過，照目前設定做；
            # 開始時記下沒處理的列（execute.skipped_snapshot），第 5 步「需留意」列出成品裡還留著原聲的地方
            if opts.get("只重新組裝") and not self._has_returned():   # 10-05 #97：沒有退回時只重新組裝，先確定聲音都生成好了
                from bookclub.execute import reassemble_problem
                from bookclub.review import parse_time

                why = reassemble_problem(self.workdir, parse_time(opts["start"]) if opts.get("start") else None,
                                         parse_time(opts["end"]) if opts.get("end") else None)
                if why:
                    return {"started": False, "error": why}
            try:   # 10-15 第5步2.0 第3段：這一次組裝以哪一版為基礎（開發者；沒指定＝照上一版，清掉上次留的）
                from bookclub import needs

                needs.set_next_base(self.workdir, int(base) if base else None)
            except ValueError as e:
                return {"started": False, "error": str(e)}
            except Exception as e:  # noqa: BLE001 — 新紀錄寫不了不擋第 4 步
                print(f"[修改需求紀錄] ⚠️ 沒記到基礎版本：{type(e).__name__}")
            self.exec_state = self._fresh_run_state()
            self.exec_state.update(running=True, started_at=time.time(), 防睡眠=AWAKE_STARTING)
            started = self.exec_state["started_at"]
            threading.Thread(target=self._exec_job, args=(opts,), daemon=True).start()
        # 10-15 第2段審查修正（建議）：回傳伺服器記的開始時間——第 5 步「重做這幾張」跑完自動跳回，比的是這個（不比瀏覽器時鐘）
        return {"started": True, "started_at": started}

    def _has_returned(self) -> bool:
        """10-05 #97：第 5 步有沒有退回（或上次重做沒做完）——有的話「只重新組裝」照原本的做法（退回的那幾筆一起收尾），不另外擋。"""
        from bookclub import finalcheck

        try:
            r = finalcheck.redo_list(self.workdir)
        except Exception:  # noqa: BLE001 — 還沒有成品檢查就是沒有退回（跟 execute.status 一樣）
            return False
        return bool(r.get("項目") or r.get("重做中"))   # 10-15 總檢查修正（1015-7）：項目含照卡片重做的（等重做記了原因的卡）

    def _awake_setter(self, state: dict):
        """10-07 #172：防睡眠開成沒有，記進 exec_state／run_state 的「防睡眠」欄位（網頁固定顯示，不會被訊息洗掉）。"""
        def set_status(msg: str) -> None:
            with self.run_lock:
                state["防睡眠"] = msg
        return set_status

    def _awake_done(self, state: dict) -> None:
        """跑完還停在「正在開」（例如還沒開到就出錯）：拿掉，免得畫面一直寫著。"""
        with self.run_lock:
            if state.get("防睡眠") == AWAKE_STARTING:
                state["防睡眠"] = None

    def _exec_job(self, opts: dict) -> None:
        from bookclub.execute import run_execute
        from bookclub.review import parse_time

        state = self.exec_state
        tee = _TeeWriter(sys.stdout, self.exec_state["messages"], self.run_lock)
        try:
            with contextlib.redirect_stdout(tee):
                prog = run_execute(self.workdir, start=parse_time(opts["start"]) if opts.get("start") else None,
                                   end=parse_time(opts["end"]) if opts.get("end") else None,
                                   methods=opts.get("methods") or None, skip_precheck=True,
                                   redo_returned=True,   # 10-01 第三批：第 5 步退回的一起重做（沒有退回的就照常）
                                   reassemble_only=bool(opts.get("只重新組裝")),   # 10-03 第八批 #23：第 4 步「只重新組裝」
                                   awake_status=self._awake_setter(state))   # 10-07 #172：防睡眠開成沒有
            with self.run_lock:
                self.exec_state["stopped"] = bool((prog or {}).get("停止"))
        except Exception as e:  # noqa: BLE001 — 背景執行緒要把失敗記下來給網頁看
            with self.run_lock:
                self.exec_state["error"] = f"{type(e).__name__}：{e}"
        finally:
            self._awake_done(state)
            with self.run_lock:
                self.exec_state["running"] = False
                self.exec_state["finished_at"] = time.time()

    def exec_status(self) -> dict:
        from bookclub.execute import status

        with self.run_lock:
            state = {k: v for k, v in self.exec_state.items() if k != "messages"}
            state["messages"] = list(self.exec_state["messages"][-80:])
        st = status(self.workdir)
        from bookclub import execcounts   # 09-29：逐類要改幾筆、做完幾筆（網頁第 4 步）

        prog = st.get("進度") or {}
        rng = prog.get("範圍") or [None, None]
        done = ((prog.get("步驟") or {}).get("組裝") or {}).get("狀態") in ("做完", "跳過")
        st["統計"] = execcounts.counts(self.workdir, rng[0], rng[1], render_done=done)
        st["預估剩餘秒數"] = execcounts.remaining_seconds(st["統計"]) if state.get("running") else None
        if not state.get("running"):   # 10-01 第三批 4：執行步驟表格顯示現在的狀態（跟統計同一個判斷）
            from bookclub.execute import current_steps

            st["現在狀態"] = current_steps(self.workdir, rng[0], rng[1], prog.get("輸出做法"))
            if not st.get("退回清單") and not st.get("重做中"):   # 10-05 #97：沒有退回時的「只重新組裝」（整支影片）能不能按
                from bookclub.execute import ASSEMBLE_S, reassemble_problem

                whole = rng[0] in (None, 0, 0.0) and rng[1] in (None, st.get("影片長度"))
                try:
                    why = reassemble_problem(self.workdir, steps=st["現在狀態"] if whole else None)
                except Exception as e:  # noqa: BLE001 — 讀不到就不讓按，畫面照樣打得開
                    why = f"讀不到現在的狀態：{e}"
                st["只重新組裝"] = {"可以": why is None, "原因": why, "預估秒數": ASSEMBLE_S}
        from bookclub.execute import stop_requested

        st["停止中"] = bool(state.get("running")) and stop_requested(self.workdir)
        from bookclub.execute import _now, run_times   # 10-08 宇軒：第 4 步「執行時間」

        try:
            st["執行時間"] = run_times(prog, _now())
        except Exception:  # noqa: BLE001 — 算不出來不擋第 4 步
            st["執行時間"] = None
        from bookclub import config

        st["開發者"] = config.developer()   # 10-15 第5步2.0 第3段：開發者設定開著才顯示範圍欄位與「只重新組裝」
        return {**state, **st}

    def start_analyze(self, opts: dict) -> dict:
        with self.run_lock:
            if self.run_state["running"] or self.exec_state["running"]:
                return {"started": False, "error": "已經有分析或 AI 執行在跑，請等它結束再按一次"}
            if self.close_packing():   # 1015-8 第 2 段審查必修 2
                return {"started": False, "error": "結案打包正在存，等它存完再開始分析。"}
            if _handoff_running(self):
                return {"started": False, "error": "交接打包或匯入交接包正在做，等它做完再開始分析。"}

            video = opts.get("video") or (str(self.video) if self.video else None)
            if not video:
                from bookclub.workdir import find_video, video_candidates, video_missing_message

                found = find_video(self.workdir)   # 10-02 第五批：存下來的路徑照「現在這個工作區」解讀
                if found is None and video_candidates(self.workdir):
                    return {"started": False, "error": video_missing_message(self.workdir)}
                video = str(found) if found else None
            if not video:
                return {
                    "started": False,
                    "error": "不知道影片路徑：啟動伺服器時帶 --video，或這次呼叫帶 body.video",
                }

            if not merged_transcript_path(self.workdir).exists() and not groq_key_ready():
                return {"started": False, "error": GROQ_KEY_MISSING}
            if not opts.get("skip_turns") and not find_claude() and not opts.get("沒有claude也開始"):
                # 10-02 第七批（B3）：找不到 claude 先在畫面上說（要開始的話再按一次「還是開始」）
                return {"started": False, "error": CLAUDE_MISSING, "找不到claude": True}

            self.run_state = self._fresh_run_state()
            self.run_state["running"] = True
            self.run_state["started_at"] = time.time()
            self.run_state["防睡眠"] = AWAKE_STARTING
            thread = threading.Thread(target=self._run_job, args=(video, opts), daemon=True)
            thread.start()
        return {"started": True}

    def _run_job(self, video: str, opts: dict) -> None:
        from bookclub.analyze import run_analyze  # 延後載入：避免 serve --help 這類指令也要載入重依賴
        from bookclub import keepawake

        state = self.run_state
        tee = _TeeWriter(sys.stdout, self.run_state["messages"], self.run_lock)
        awake_off = keepawake._noop
        try:
            with contextlib.redirect_stdout(tee):
                # 10-07 #172：第 1 步也不讓電腦睡著（Mac caffeinate、WSL2 請 Windows 不要睡），跟第 4 步同一套
                awake_off = keepawake.keep_awake(print, prefix="[分析一條龍]", on_status=self._awake_setter(state))
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
            awake_off()
            self._awake_done(state)
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

    def _serve_file(self, path: Path, content_type: str | None = None, no_store: bool = False) -> None:
        """no_store（10-13 bug 一）：第 5 步成品影片不讓瀏覽器快取（同名覆寫後還播到舊的）。"""
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
        if no_store:
            self.send_header("Cache-Control", "no-store")
        elif content_type.split(";")[0] in ("text/html", "text/javascript", "application/javascript", "text/css"):
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

    def _from_here(self, writes: bool) -> bool:
        if request_allowed(self.headers.get("Host"), self.headers.get("Origin"), writes):
            return True
        self._send_json(403, {"error": "只接受這台電腦上、這個工具自己的網頁送來的請求"})
        return False

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        if not self._from_here(writes=False):
            return
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
        except Closed as e:
            self._send_json(409, {"error": str(e), "已結案": True})
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
        if _server_closed(server):
            # 1015-8 第 3 段（32 號設計 §5.2、§9.3）：已結案不切試聽檔、不寫匯出檔（聲音也清掉了）；其他 GET 照樣讀，
            # 但打開頁面時順手存檔的地方（第 3、5 步的 page_data 會整理後存檔）一律不寫——瘦身版裡不長出、不改任何檔
            if path in CLOSED_GET_BLOCK or (path == "/api/audio" and "path" not in query):
                raise Closed(CLOSED_MSG)
            with wd_no_writes():
                return self._route_get_api_inner(path, query)
        return self._route_get_api_inner(path, query)

    def _route_get_api_inner(self, path: str, query: dict) -> None:
        server = self.server
        if path == "/api/state":
            if not server.has_workdir:
                self._send_json(200, {"沒有專案": True, "workdir": None, "video": {}, "substeps": {}, "proofread": {}})
                return
            try:   # 10-16 #234：上次背景補找人名沒做完（伺服器關掉）→ 打開網頁就補做
                from bookclub import personnames

                personnames.ensure_rescan(server.workdir)
            except Exception:  # noqa: BLE001 — 不擋畫面
                pass
            self._send_json(200, build_state(server.workdir, video=server.video))
        elif path == "/api/profile":
            from bookclub import profile

            self._send_json(200, profile.summary())
        elif path == "/api/profile/export.zip":
            from bookclub import profile

            out = profile.export_profile()
            self._serve_file(Path(out["檔案"]), content_type="application/zip")
        elif path == "/api/projects":
            self._send_json(200, {**list_projects(server._workdir), "轉文字金鑰": groq_key_ready(), "轉文字金鑰說明": GROQ_KEY_MISSING})
        elif path == "/api/browse":
            self._send_json(200, browse((query.get("path") or [None])[0]))
        elif path == "/api/picks":   # 10-07：總覽的選檔狀態（選了哪支、片頭片尾、複製進度）
            self._send_json(200, server.picks_state())
        elif path == "/api/final/outputs":   # 10-07：第 5 步「打開成品資料夾」「複製到 Windows 的下載資料夾」
            self._send_json(200, server.final_outputs())
        elif path == "/api/refs":
            self._send_json(200, build_refs(server.workdir))
        elif path == "/api/roomtone":   # 10-02 第六批第五件：第 2 步「全片底噪」（沒有就自動補挑）
            from bookclub import roomtone

            # 1015-8 第 3 段（§9.3）：已結案不自動補挑底噪（會去讀已經清掉的 audio.flac、寫檔），只讀記下的
            info = roomtone.load_info(server.workdir) if _closed(server.workdir) else roomtone.ensure_info(server.workdir)
            self._send_json(200, info or {"沒有": True})
        elif path == "/api/names":
            self._send_json(200, build_names(server.workdir))
        elif path == "/api/proofread":
            from bookclub.proofread import page_data

            self._send_json(200, page_data(server.workdir))
        elif path == "/api/turns":
            from bookclub.turns import page_data as turns_page

            self._send_json(200, turns_page(server.workdir))
        elif path == "/api/people/rescan":   # 10-16 #234：③ 背景補找老師提到的名字做到哪了
            from bookclub import personnames

            self._send_json(200, personnames.rescan_status(server.workdir))
        elif path == "/api/review":
            from bookclub.review import page_data as review_page

            snap = _needs_snap(server)   # 10-15 第1段審查修正：第 3 步快照在算 page 之前拍（中間有 POST 下次會再同步）
            page = review_page(server.workdir, video=server.video)
            _needs_ensure(server, page, snap)   # 10-14 第5步2.0 第1段：第一次打開升級；之後同步第 3 步的卡片（用這份 page，不另外算）
            from bookclub import personnames

            if not server.exec_running():   # 1017 總審查：第 4 步執行中不重試出錯的補找（第 4 步開始前已經等過／放行過）
                personnames.ensure_rescan(server.workdir, retry=True)   # 10-16 #234：上次背景補找人名沒做完（或出錯）就補做
            page["人名補找"] = personnames.rescan_status(server.workdir)
            self._send_json(200, {**page, "AI執行中": server.exec_running(), "開發者": _developer()})   # 09-30：執行中第 3 步變唯讀；10-16 #225：開發者設定（「你標的 → 對齊後」只給開發者看）
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
            # 10-15 第5步2.0 第2段：第 5 步用第 3 步的卡片——第 3 步的資料在算之前先拍快照、算的時候不寫舊檔（needs.compute_page），
            # 同一份給修改需求紀錄同步用，不另外算
            page, snap = _final_step3(server)
            _needs_ensure(server, page, snap)   # 10-14 第5步2.0 第1段
            ver = _version_q(query)
            if ver is not None and ver != _current_version(server):   # 10-15 第5步2.0 第3段：看舊版
                from bookclub import finalcards

                self._send_json(200, finalcards.version_page(server.workdir, ver, page, running=server.exec_running()))
                return
            self._send_json(200, _final_data(server, page))
        elif path == "/api/final/video":
            from bookclub import finalcheck

            ver = _version_q(query)
            if ver is not None and ver != _current_version(server):   # 10-15 第5步2.0 第3段：舊版的成品
                from bookclub import finalcards

                self._serve_file(finalcards.version_video(server.workdir, ver), no_store=True)
                return
            self._serve_file(finalcheck.video_file(server.workdir), no_store=True)   # 10-13 bug 一：不快取
        elif path == "/api/final/vclip":   # 10-15 第5步2.0 第3段：第 N 版成品一段的聲音（上一版 ▶、跨版本卡）
            from bookclub import finalcards

            self._serve_file(finalcards.version_clip(server.workdir, int(query["版本"][0]), float(query["start"][0]),
                                                     float(query["end"][0])), content_type="audio/wav")
        elif path == "/api/final/clip":
            from bookclub import finalcheck

            self._serve_file(finalcheck.clip(server.workdir, query["which"][0], query["key"][0]), content_type="audio/wav")
        elif path == "/api/final/goto":   # 10-03 第八批 #64：第 5 步「跳到」框，原片時間 → 成品時間（後端換算）
            from bookclub import finalcheck

            self._send_json(200, finalcheck.goto_output_time(server.workdir, float(query["src"][0])))
        elif path == "/api/handoff/state":   # 33 號設計：交接打包、匯入交接包（只讀）
            self._send_json(200, server.handoff_state())
        elif path == "/api/handoff/clean-plan":
            self._send_json(200, server.handoff_clean_plan())
        elif path == "/api/close/state":   # 1015-8：第 5 步「結案打包」框（只讀）
            self._send_json(200, server.close_state())
        elif path == "/api/close/locations":   # 第 2 段審查必修 3：位置選項另外問（WSL 會叫 PowerShell）
            self._send_json(200, server.close_locations())
        elif path == "/api/run/status":
            self._send_json(200, server.run_status())
        elif path == "/api/execute":
            _needs_ensure(server)   # 10-14 第5步2.0 第1段
            self._send_json(200, server.exec_status())
        elif path == "/api/execute/finalcheck":   # 10-01：開始前總檢查（只讀）
            from bookclub.execute import final_check

            self._send_json(200, final_check(server.workdir))
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
        if not self._from_here(writes=True):
            return
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
        except Busy as e:
            self._send_json(409, {"error": str(e), "執行中": True})
        except Closed as e:
            self._send_json(409, {"error": str(e), "已結案": True})
        except FileNotFoundError as e:
            self._send_json(404, {"error": str(e)})
        except json.JSONDecodeError:
            self._send_json(400, {"error": "送來的資料格式不對（重新整理網頁再試一次）"})
        except KeyError as e:
            arg = str(e.args[0]) if e.args else ""
            self._send_json(400, {"error": arg if "：" in arg else
                                  f"少了「{arg}」這一欄，或找不到這一筆（重新整理網頁再試一次）"})
        except ValueError as e:   # 10-01 走查：畫面上不要出現「ValueError：」
            self._send_json(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._send_json(500, {"error": f"{type(e).__name__}：{e}"})

    def _route_post_api(self, path: str, body: dict) -> None:
        server = self.server
        # 1015-8 第 3 段（32 號設計 §5.2）：伺服器一層擋——已結案（含清理中）所有會寫這個工作區的 API 一律 409
        if _server_closed(server) and not closed_post_allowed(path, body):
            raise Closed(CLOSED_MSG)
        if path.startswith(STEP3_WRITES) and path not in STEP3_WRITES_OK and server.exec_running():
            raise Busy("AI 正在執行第 4 步，第 3 步這時候不能改（改了這次執行也用不到）。等它跑完，或到第 4 步按「停止」")
        if path == "/api/execute/stop":
            if not server.exec_running():
                raise ValueError("第 4 步現在沒有在執行")
            from bookclub.execute import request_stop

            request_stop(server.workdir)
            self._send_json(200, {"ok": True, "停止中": True})
            return
        if path == "/api/pick":   # 10-07：叫系統的選檔視窗（會等到人選好或按取消）
            purpose = str(body.get("用途", "影片"))
            self._send_json(200, server.pick(purpose, str(body.get("給", "新影片")),
                                             server.handoff_video_title() if purpose == HANDOFF_VIDEO else None))
            return
        if path == "/api/pick/browsed":   # 10-07：叫不起系統視窗時，網頁資料夾瀏覽選的（只能家目錄底下）
            p = home_path(str(body["路徑"]))
            from bookclub import fileio

            self._send_json(200, server.accept_pick(str(body.get("用途", "影片")), fileio.check_video(p),
                                                    str(body.get("給", "新影片"))))
            return
        if path == "/api/pick/forget":   # 10-07：選錯了、拿掉（給新影片的；目前專案的片頭片尾用 /api/extras/clear）
            server.forget_pick(str(body["用途"]))
            self._send_json(200, {"ok": True})
            return
        if path == "/api/extras/clear":
            self._send_json(200, server.clear_extra(str(body["用途"])))
            return
        if path == "/api/copy/cancel":
            self._send_json(200, server.cancel_copy(str(body["id"])))
            return
        if path == "/api/final/reveal":   # 10-07：路徑都是伺服器自己算的，不收網頁傳來的路徑
            self._send_json(200, server.final_reveal())
            return
        if path == "/api/close/pick-folder":   # 1015-8：結案打包——叫系統的選資料夾視窗（會等到人選好或按取消）
            self._send_json(200, server.close_pick_folder())
            return
        if path == "/api/close/pack":
            self._send_json(200, server.close_pack(body))
            return
        if path == "/api/close/cancel":
            self._send_json(200, server.close_cancel())
            return
        if path == "/api/close/reveal":   # 打開打包資料夾（路徑是伺服器自己記的）
            self._send_json(200, server.close_reveal())
            return
        if path == "/api/close/clean":   # 1015-8 第 3 段：清空間（再確認 → 搬 → 刪）、繼續清
            self._send_json(200, server.close_clean(body))
            return
        if path.startswith("/api/handoff/"):   # 33 號設計 #257–#260：接力交接
            routes = {"/api/handoff/pick-folder": lambda: server.handoff_pick_folder(),
                      "/api/handoff/pack": lambda: server.handoff_pack(body),
                      "/api/handoff/cancel": lambda: server.handoff_cancel(),
                      "/api/handoff/clean-copy": lambda: server.handoff_clean_copy(body),
                      "/api/handoff/clean-episode": lambda: server.handoff_clean_episode(body),
                      "/api/handoff/import/zip": lambda: server.handoff_import_zip(body),
                      "/api/handoff/import/video": lambda: server.handoff_import_video(body),
                      "/api/handoff/import/start": lambda: server.handoff_import_start(body),
                      "/api/handoff/import/reset": lambda: server.handoff_import_reset()}
            if path not in routes:
                raise FileNotFoundError(f"沒有這個功能：{path}")
            self._send_json(200, routes[path]())
            return
        if path == "/api/final/to_windows":
            self._send_json(200, server.final_to_windows(confirm=bool(body.get("confirm"))))
            return
        if path == "/api/projects/switch":
            d = Path(str(body["路徑"])) if body.get("路徑") else safe_join(workroot(), str(body["名稱"]))
            if not d.is_dir() or not known_project(d):
                raise SecurityError(f"不是專案清單上的專案：{d}")
            analysis = read_json(analysis_result_path(d), default={}) or {}
            server.use_project(d, analysis.get("video"))
            self._send_json(200, {"ok": True, "目前": str(d)})
            return
        if path == "/api/projects/start":
            if body.get("用選的"):   # 10-07：用系統選檔視窗選的（伺服器記著的那一支；WSL2 是複製進來的那一份）
                video = server.take_picked_video()
            else:
                video = home_path(str(body["影片"]))
                if not video.is_file() or video.suffix.lower() not in VIDEO_EXTS:
                    raise FileNotFoundError(f"不是影片檔：{video}")
            d = project_dir_for(video)
            if _closed(d):   # 1015-8 第 3 段：同一支影片的工作區已結案（瘦身版），不在裡面重新分析
                print(f"[結案] {d} 已結案；要重做由開發者取消結案：bookclub close --reopen", file=sys.stderr)   # 開發者指令只印終端機
                raise Closed("這支影片已結案，要重做請找宇軒")
            existed = d.exists()
            if server.run_status_running():
                raise ValueError("分析還在跑，等它結束再開始另一支")
            d.mkdir(parents=True, exist_ok=True)          # 按下「開始分析」才建資料夾（在影片旁邊）
            register_project(d)
            server.use_project(d, video)
            server.apply_new_extras(d)   # 10-07：給新影片選的片頭片尾寫進這個專案
            opts = {k: body[k] for k in ("skip_turns", "skip_overlap", "沒有claude也開始") if k in body}
            result = server.start_analyze({"video": str(video), **opts})
            self._send_json(202 if result.get("started") else 409,
                            {**result, "專案": d.name, "路徑": str(d), "接著做": existed})
            return
        if path == "/api/refs/use":
            rank = int(body["rank"])
            transcript = str(body.get("transcript", ""))
            replace = body.get("換音檔")   # 10-01：只有按「改用這一個」「換成這個候選的新音檔」才換
            self._send_json(200, refs_use(server.workdir, rank, transcript,
                                          None if replace is None else bool(replace)))
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
            elif path == "/api/turns/realname":
                self._send_json(200, _turns.set_real_name(server.workdir, str(body["學員"]), body.get("本名")))
            elif path == "/api/turns/namecode":
                self._send_json(200, _turns.set_name_code(server.workdir, str(body["本名"]), body.get("代號")))
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
        elif path == "/api/students/voice":   # 09-30：第 3 步「學員是誰」改某位學員的聲線（空白＝回到自動配）
            from bookclub import students

            self._send_json(200, students.set_voice_choice(server.workdir, str(body["學員"]), body.get("聲線") or None))
        elif path == "/api/codes/convert":   # 10-02 第七批：把這一集的英文代號換成中文（body：{"對照": {舊: 新}, "試跑": bool}）
            from bookclub import codeswap

            self._send_json(200, codeswap.apply(server.workdir, body.get("對照") or {}, dry_run=bool(body.get("試跑"))))
        elif path == "/api/codes/auto":   # 09-29：幫還沒代號的自動配
            from bookclub import epcodes

            self._send_json(200, epcodes.auto_assign(server.workdir))
        elif path == "/api/codes/initial":   # 10-07：進第 3 步時 ③ 名冊上的人自動配一次代號（做過就不再做）
            from bookclub import epcodes

            self._send_json(200, epcodes.auto_initial(server.workdir))
        elif path == "/api/people/decide":   # 09-29：名冊上沒有的名字怎麼處理（第 1 步人名清單）
            from bookclub import personnames

            self._send_json(200, personnames.decide(server.workdir, str(body["名字"]), str(body["做法"]), body.get("代號"), body.get("同一人")))
        elif path == "/api/people/rescan/seen":   # 10-16 #234：「補找到 N 處」通知跳過了，歸零
            from bookclub import personnames

            self._send_json(200, personnames.rescan_seen(server.workdir))
        elif path.startswith("/api/review/"):
            from bookclub import review as rv

            if path == "/api/review/name":
                self._send_json(200, rv.save_name(server.workdir, str(body["id"]), body))
            elif path == "/api/review/stuname":
                from bookclub import studentnames

                self._send_json(200, studentnames.save(server.workdir, str(body["id"]), body))
            elif path == "/api/review/overlap":
                self._send_json(200, rv.save_overlap(server.workdir, str(body["id"]), body))
            elif path == "/api/review/cut":
                self._send_json(200, rv.save_cut(server.workdir, body))
            elif path == "/api/review/mute":
                self._send_json(200, rv.save_mute(server.workdir, body))
            elif path == "/api/review/outside":   # 10-01 第三批 14：切在段落外面的幾秒是誰的聲音
                from bookclub.execute import answer_outside

                self._send_json(200, answer_outside(server.workdir, str(body["鍵"]), float(body["start"]), float(body["end"]),
                                                    body.get("答案") or None))
            elif path == "/api/review/delete":   # 10-01 第三批：人工新增的加錯了可以直接刪（留紀錄）
                self._send_json(200, rv.delete_manual(server.workdir, str(body["類型"]), str(body["id"])))
            elif path == "/api/review/manual":
                self._send_json(200, rv.manual_edit(server.workdir, body))
            elif path == "/api/review/shrink":   # 10-02 第六批：老師重念範圍「照建議縮小」（第 3、4、5 步共用）
                from bookclub import silentedge

                self._send_json(200, silentedge.apply(server.workdir, str(body["鍵"]), body.get("第5步鍵") or None))
            elif path == "/api/review/voice":
                self._send_json(200, rv.set_voice(server.workdir, body.get("學員"), str(body["聲音"])))
            elif path == "/api/review/passall":   # 10-08 宇軒（流程簡化）：「全部照建議通過」
                self._send_json(200, rv.pass_all(server.workdir))
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
                self._send_json(200, fc.decide_record(w, str(body["鍵"]), body.get("結果"), str(body.get("原因", "")),
                                                     body.get("改範圍")))
            elif path == "/api/final/unlogged":
                self._send_json(200, fc.decide_unlogged(w, str(body["鍵"]), body.get("結果"), str(body.get("原因", ""))))
            elif path == "/api/final/flag":
                self._send_json(200, fc.add_whole_redo(w, float(body["成品秒"]), str(body.get("原因", ""))))
            elif path == "/api/final/unflag":
                self._send_json(200, fc.remove_whole_redo(w, str(body["id"])))
            elif path == "/api/final/watched":
                ver = body.get("版本")
                if isinstance(ver, int) and ver != _current_version(server):   # 10-15 第5步2.0 第3段：舊版看過的記在那一版
                    from bookclub import finalcards

                    self._send_json(200, finalcards.version_watched(w, ver, body.get("區段", [])))
                else:
                    self._send_json(200, fc.add_watched(w, body.get("區段", []), body.get("成品影片")))
            elif path == "/api/final/product":
                self._send_json(200, fc.choose_product(w, str(body["成品影片"])))
            elif path == "/api/final/switchnote":   # 10-13 bug 一：「已改成檢查新的成品」顯示過了就清掉
                self._send_json(200, fc.clear_switch_note(w))
            elif path == "/api/final/card":   # 10-15 第5步2.0 第2段：一張卡通過／退回（回寫舊檔這張卡的每一筆）
                from bookclub import finalcards

                ver = body.get("版本")   # 10-15 第5步2.0 第3段：在第幾版上按的（舊版、跨版本卡）
                # 10-15 總檢查修正（1015-7）：`取消要重做: true`＝畫面明說是「取消要重做」（不是取消通過）；沒給照卡片判斷
                self._send_json(200, finalcards.decide(w, str(body["id"]), body.get("結果"), body.get("選項") or [],
                                                       str(body.get("補充", "")),
                                                       version=ver if isinstance(ver, int) and not isinstance(ver, bool) else None,
                                                       cancel_redo=body.get("取消要重做") is True))
            elif path == "/api/final/edited":   # 10-15：第 5 步按「改」改過 → 那張卡「已改，等重做」
                from bookclub import finalcards

                # 10-15 第2段審查修正（建議，效能）：第 3 步的資料這裡算一次——同步卡片用它、也連同整頁資料一起回傳
                # （網頁接著的重新整理直接用這一份，不再 GET /api/final 重算一次第 3 步）
                page, snap = _final_step3(server)
                r = finalcards.edited(w, str(body["鍵"]), bool(body.get("新增")), page=page, snap=snap)
                r["頁面"] = _final_data(server, page)
                self._send_json(200, r)
            elif path == "/api/final/unedited":   # 10-16 #225：第 5 步「復原」→ 拿掉「已改，等重做」（內容跟這一版一樣才拿）
                from bookclub import finalcards

                page, snap = _final_step3(server)
                r = finalcards.unedited(w, str(body["鍵"]), page=page, snap=snap)
                r["頁面"] = _final_data(server, page)
                self._send_json(200, r)
            elif path == "/api/final/sendback":
                self._send_json(200, fc.send_back(w))
            elif path == "/api/final/export":
                # 1015-8 第 2 段審查：打包在存時不輸出（含輸出舊版）；輸出時記「正在輸出最終成品」，打包那邊也看這個（雙向互擋）
                with server.pick_lock:
                    if server.close_packing():
                        raise Busy("結案打包正在存，等它存完再輸出成品。")
                    server.final_exporting += 1
                try:
                    ver = body.get("版本")
                    if isinstance(ver, int) and not isinstance(ver, bool) and ver != _current_version(server):
                        from bookclub import finalcards   # 10-15 第5步2.0 第3段（§6.7）：輸出選中的那一版

                        out = finalcards.export_version(w, ver, confirm=body.get("確定") is True)
                    else:
                        out = fc.export_final(w, confirm=body.get("確定") is True)   # 10-08：還沒看完要先確認
                finally:
                    with server.pick_lock:
                        server.final_exporting -= 1
                self._send_json(200, out)
            else:
                self._send_json(404, {"error": f"沒有這個 API：{path}"})
        elif path == "/api/roomtone":   # 10-02 第六批第五件：第 2 步「這段可以」「換一段」
            from bookclub import roomtone

            self._send_json(200, roomtone.decide(server.workdir, str(body.get("動作", ""))))
        elif path == "/api/execute/finalcheck":   # 10-01：某一列「我聽過了」、整頁「我看過了」
            from bookclub.execute import ack_final, keep_final

            for field in ("照目前設定做", "不改"):   # 10-05 #177：「一定要處理」某一列「照目前設定做」或取消（「不改」是舊名字）
                if field in body:
                    self._send_json(200, keep_final(server.workdir, body.get("key"), bool(body[field])))
                    return
            # 10-05 #178：帶「看一眼」＝「請看一眼」某一列的「我看過了」；沒帶＝整區「全部看過了」
            self._send_json(200, ack_final(server.workdir, body.get("key"), bool(body.get("聽過", True)),
                                           body.get("看過") if "看過" in body else None,
                                           body.get("看過的列") if isinstance(body.get("看過的列"), list) else None,
                                           look_key=body.get("看一眼") if isinstance(body.get("看一眼"), str) else None))
        elif path == "/api/execute/start":
            from bookclub import personnames

            if server.has_workdir:   # 10-16 #234：③ 補找老師提到的名字還沒做完
                if personnames.rescan_busy(server.workdir):   # 背景真的在做（幾十秒）
                    raise ValueError("還在找老師提到的名字（第 3 步 ③ 剛選的），等半分鐘再按")
                # 出錯停住：提醒一次，再按就放行（不永遠擋住）；已經在執行（會回 409）時不用掉這次提醒
                why = None if server.exec_running() else personnames.rescan_error_gate(server.workdir)
                if why:
                    raise ValueError(why)
            result = server.start_execute(body)
            self._send_json(202 if result.get("started") else 409, result)
        elif path == "/api/run/analyze":
            result = server.start_analyze(body)
            self._send_json(202 if result.get("started") else 409, result)
        else:
            self._send_json(404, {"error": f"沒有這個 API：{path}"})


# ---------------------------------------------------------------------------
# 對外入口
# ---------------------------------------------------------------------------

def _is_wsl() -> bool:
    from bookclub.system_info import is_wsl, read_system_file

    return is_wsl(read_system_file("/proc/version"))


def browser_plan(system: str | None = None, wsl: bool | None = None, env: dict | None = None) -> str:
    """啟動後要不要自動開瀏覽器（10-03 第九批 #29）。回傳：
    - "open"：macOS，或有桌面的 Linux（有 DISPLAY／WAYLAND_DISPLAY）→ 試著開
    - "wsl"：WSL2 → 不試，印「請在 Windows 的瀏覽器開…」（WSL2 裡試著開會默默失敗，
      或叫出終端機文字瀏覽器把畫面卡住）
    - "manual"：沒有桌面的 Linux → 不試，印網址請人自己開"""
    import os
    import platform

    system = system or platform.system()
    if system == "Darwin":
        return "open"
    if wsl is None:
        wsl = system == "Linux" and _is_wsl()
    if wsl:
        return "wsl"
    env = os.environ if env is None else env
    if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"):
        return "open"
    return "manual"


def browser_url(port: int, plan: str) -> str:
    """給人開的網址。WSL2 要從 Windows 的瀏覽器開，寫 localhost（Windows 會轉進 WSL2）。"""
    return f"http://localhost:{port}/" if plan == "wsl" else f"http://127.0.0.1:{port}/"


def open_browser_hint(port: int, plan: str) -> str:
    url = browser_url(port, plan)
    if plan == "wsl":
        return f"[網頁伺服器] 這是 WSL2，不會自動開瀏覽器：請在 Windows 的瀏覽器（Edge 或 Chrome）開 {url}"
    return f"[網頁伺服器] 請在瀏覽器開 {url}"


def _final_step3(server) -> tuple[dict | None, dict | None]:
    """10-15 第5步2.0 第2段：第 5 步要的第 3 步資料——先拍快照、再算（算的時候不寫舊檔，needs.compute_page）。
    算不出來＝(None, None)：卡片照樣列，只是不能按「改」。"""
    from bookclub import needs

    snap = _needs_snap(server)
    try:
        return needs.compute_page(server.workdir), snap
    except Exception as e:  # noqa: BLE001
        print(f"[成品檢查] ⚠️ 第 3 步的卡片讀不到（第 5 步的「改」先不能用）：{type(e).__name__}")
        return None, None


def _version_q(query: dict) -> int | None:
    """`?版本=N`（10-15 第5步2.0 第3段）：沒帶、讀不懂＝None（最新一版）。"""
    try:
        return int((query.get("版本") or [None])[0])
    except (TypeError, ValueError):
        return None


def _current_version(server) -> int | None:
    """最新處理紀錄是第幾版（10-15 第5步2.0 第3段；新紀錄不能用＝None）。"""
    from bookclub import finalcards

    try:
        return finalcards.current_version(server.workdir)
    except Exception:  # noqa: BLE001
        return None


def _final_data(server, page: dict | None) -> dict:
    """`GET /api/final` 的整頁資料（成品檢查＋第 5 步卡片）。10-15 第2段審查修正（建議，效能）：`review.item_index` 只算一次，兩邊共用。"""
    from bookclub import finalcards, finalcheck

    index = finalcheck._index(server.workdir)
    data = finalcheck.page_data(server.workdir, index=index)
    data.update(finalcards.page_part(server.workdir, page, running=server.exec_running(), index=index))
    data["已結案"], data["結案"] = _closed(server.workdir), _closed_info(server.workdir)   # 1015-8 第 3 段：瘦身版
    return data


def _needs_snap(server) -> dict | None:
    """10-15 第1段審查修正（建議）：算第 3 步 page 之前先拍第 3 步的檔的快照（給 `_needs_ensure`）。拍不到＝None（之後自己拍）。"""
    from bookclub import needs

    try:
        return needs.step3_snapshot(server.workdir)
    except Exception:  # noqa: BLE001
        return None


def _needs_ensure(server, page: dict | None = None, snap: dict | None = None) -> None:
    """10-14 第5步2.0 第1段：打開第 3、4、5 步時，修改需求紀錄第一次就升級（第 4 步執行中先不升級）、之後補差異。
    出錯只印提醒，不擋畫面（`needs.ensure` 自己接住）。10-15：另一個請求正在升級就跳過不等；失敗後一段時間內不重試。"""
    from bookclub import needs

    try:
        w = server.workdir
    except Exception:  # noqa: BLE001 — 沒有專案：不用做
        return
    if _closed(w):   # 1015-8 第 3 段（§9.3）：已結案的瘦身版不升級、不同步（不在裡面長出檔案）
        return
    needs.ensure(w, running=server.exec_running(), page=page, snap=snap if page is not None else None)


def _bookclub_already_running(port: int, timeout: float = 2.0) -> bool:
    """這個埠上跑的是不是讀書會剪輯工具（問 /api/projects，看回答裡有沒有這個工具才有的欄位）。"""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/projects", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return isinstance(data, dict) and "轉文字金鑰" in data
    except Exception:
        return False


def port_in_use_message(port: int, ours: bool, plan: str) -> str:
    """埠被占用時給人看的話（10-03 第九批 #30：以前印一大段 Python 錯誤）。"""
    url = browser_url(port, plan)
    if ours:
        close = "要關掉舊的：到開著它的那個終端機視窗按 Ctrl+C（或直接關掉那個視窗）。"
        return (f"[網頁伺服器] 讀書會剪輯工具已經開著了（{port} 埠有它在跑，多半是之前開過、或連點了兩次），"
                f"這次不用再開一個。\n[網頁伺服器] 直接在瀏覽器開 {url} 就好。{close}")
    find = f"lsof -i :{port}" if sys.platform == "darwin" else f"ss -ltnp | grep :{port}"
    return (f"[網頁伺服器] {port} 埠被占用了，這次沒有啟動。多半是之前開的讀書會剪輯工具還沒關（或卡住了）："
            f"找到開著它的終端機視窗按 Ctrl+C；找不到的話，終端機執行 {find} 看是哪個程式在用。\n"
            f"[網頁伺服器] 也可以改用別的埠：.venv/bin/bookclub serve --port {port + 1}"
            f"（之後一直用的話，改 settings.toml 裡 [server] 的 port）。")


def serve(
    workdir: str | Path | None = None,
    video: str | Path | None = None,
    port: int | None = None,
    open_browser: bool = True,
) -> int:
    """不帶工作區：網頁總覽選影片或切換已有的專案（09-26）。帶工作區：舊用法，直接開那一個。
    回傳結束代碼：正常結束 0；埠被別的程式占用 1（10-03 第九批 #30）。"""
    import errno

    if workdir:
        # 10-04 #129：用完整路徑（相對路徑、~ 都展開），清單和「目前」才對得上
        workdir = Path(workdir).expanduser().resolve()
        workdir.mkdir(parents=True, exist_ok=True)
    if port is None:
        port = load_settings().server_port
    if not workdir and not video:
        workdir = recall_current()   # 09-30：重開後接回上次在做的專案
        if workdir:
            video = (read_json(analysis_result_path(workdir), default={}) or {}).get("video")
            print(f"[網頁伺服器] 接回上次的專案：{workdir}")

    plan = browser_plan()
    try:
        httpd = BookclubServer(
            ("127.0.0.1", port), Handler,
            workdir=workdir or None, video=Path(video).expanduser() if video else None,
        )
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        # 10-03 第九批 #30：埠被占用（多半是連點兩次啟動）。不印 Python 錯誤，說人話；
        # 已經開著的是這個工具的話，Mac 上直接幫忙打開瀏覽器到舊的那一個。
        ours = _bookclub_already_running(port)
        print(port_in_use_message(port, ours, plan))
        if ours and open_browser and plan == "open":
            with contextlib.suppress(Exception):
                if webbrowser.open(browser_url(port, plan)):
                    print("[網頁伺服器] 已經幫你在瀏覽器打開舊的那一個。")
        return 0 if ours else 1
    if workdir:
        adopt_project(workdir)
    if sys.platform == "darwin":   # 10-08：背景開好 Mac 的常駐選檔小程式並預熱（含第一次編譯約 5 秒；不擋啟動）
        from bookclub import fileio

        fileio.MAC_PICKER.start_in_background()
    _mark_interrupted(workdir or None)   # 09-30：上次跑到一半伺服器被關掉，進度改「中斷」（要在確定埠拿到之後）
    url = browser_url(port, plan)
    print(f"[網頁伺服器] 網址：{url}")
    print(f"[網頁伺服器] 工作區（實際開的，完整路徑）：{workdir or '（還沒選，到網頁總覽選影片）'}")
    if not groq_key_ready():
        print(f"⚠️ [網頁伺服器] {GROQ_KEY_MISSING}（已經轉好文字的專案不受影響）")
    print("[網頁伺服器] Ctrl+C 結束")

    if open_browser:
        if plan == "open":
            def _open() -> None:
                try:
                    opened = webbrowser.open(url)
                except Exception:
                    opened = False
                if not opened:
                    print(open_browser_hint(port, "manual"))

            threading.Timer(0.4, _open).start()
        else:
            print(open_browser_hint(port, plan))

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        print("\n[網頁伺服器] 已結束")
    return 0
