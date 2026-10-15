"""修改需求紀錄（10-14 第5步2.0 第1段，設計見 31 號設計檔 §3、§4）。

一張卡從第 3 步建立、第 4 步生成、第 5 步檢查與重做，都是同一筆紀錄；每次組裝是一個版本。
工作區新資料夾 `修改需求/`，**讀寫只走這一支模組**（其他程式只呼叫這裡的函式，不自己開這些檔）：

| 檔案 | 表 | 主鍵 |
|---|---|---|
| `卡片.json` | 卡片 | 卡號（`K0001` 起，永遠不換、不重用） |
| `生成句.json` | 卡片↔生成句 | 句id＋角色 |
| `版本.json` | 每次組裝 | 版本號（1 起） |
| `成品狀態.json` | 每張卡在每一版的狀態 | 卡號＋版本號 |
| `歷史.jsonl` | 每個決定一條，只往下加 | 序號 |
| `格式.json` | 格式版本、升級自哪個程式版本、升級時間、上次和舊檔同步的指紋、下一個卡號 | — |
| `版本/v<N>/處理紀錄.json` | 那一版的處理紀錄副本 | — |

照「將來可換 SQLite」排：每張表是「一列一筆」的清單、每列有主鍵；表跟表之間只用編號互相指；歷史另一張表只往下加；
對外只給「查／加／改」這類函式（`load`、`card_rows`⋯），換成 SQLite 時只改這支模組裡面。

第 1 段（這一段）的範圍：
- 舊工作區升級（`upgrade`）：只讀舊檔、只寫 `修改需求/`；小檔先備份；對不到的變「待釐清」卡；只跑一次
- 並存期間雙寫：第 3 步照舊寫第 3 步的檔，這裡同步卡片與歷史（`sync_cards`）；第 4 步每次組裝建一個版本
  （`add_version`，`proclog` 寫處理紀錄時呼叫）、排計畫時記生成句（`record_gen_plan`）；第 5 步的判斷照舊寫
  `覆核/成品檢查.json`，存檔後這裡算出每張卡在那一版的狀態（`mirror_final`）
- **讀的那一邊這一段不換**：現有畫面與第 4 步照舊讀舊檔；這份紀錄給第 2～4 段接手

第 5 步舊檔是「處理紀錄的每一筆」一個判斷，新紀錄是「每張卡」一個狀態：一張卡對到好幾筆處理紀錄時（學員段落切成好幾格），
任何一筆退回＝這張卡退回、每一筆都通過＝通過、其他＝還沒看（第 2 段畫面改成以卡片為單位後就一致了）。

隱私：含學員文字的有三處（10-15 第1段審查修正：以前只寫了第一處）——`卡片.json` 的 `第3步決定`（第 3 步檔案的同步副本，
含要念的文字）、`版本/v*/處理紀錄.json`（處理紀錄副本，含文字）、`升級前備份_*/`（第 3 步、第 5 步舊檔的備份，含逐字稿、
退回原因）。跟其他工作區檔案一樣只留在本機、不直接讀；`成品狀態.json` 的 `退回原因` 也是文字。
`歷史.jsonl`、`格式.json`、升級報告只放編號、數字、狀態與指紋，不放學員分享原文與退回原因文字。
AI 助手查這份紀錄一律用 `bookclub inspect <工作區> 修改需求`。#222（結案打包）若整包帶走 `修改需求/`，打包檔一樣含學員文字。
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from bookclub import workdir as wd

DIR_NAME = "修改需求"
FORMAT_VERSION = 1

T_CARDS, T_GEN, T_VERSIONS, T_STATES = "卡片", "生成句", "版本", "成品狀態"
TABLE_KEYS = {T_CARDS: ("卡號",), T_GEN: ("句id", "角色"), T_VERSIONS: ("版本號",), T_STATES: ("卡號", "版本號")}
HISTORY_FILE = "歷史.jsonl"
FORMAT_FILE = "格式.json"
REPORT_JSON, REPORT_MD = "升級報告.json", "升級報告.md"
BACKUP_PREFIX = "升級前備份_"
# 10-15 第1段審查修正（必修 2）：升級先寫到這個資料夾，全部寫完才一次改名成 `修改需求/`（中途失敗不會留下半套 `修改需求/`）
STAGING = f"{DIR_NAME}.升級中"
LOCK_FILE = ".寫入鎖"
# 10-15 第1段審查修正（建議）：同步失敗記在 `格式.json`，`inspect 修改需求` 印出來；第 2 段讀新紀錄前看到有錯誤就退回讀舊檔
ERR_KEY = "同步錯誤"
LAST_ERR_KEY = "最後一次錯誤"   # 10-15 第1段審查二修正：同步錯誤清成 0 次之後，inspect 還看得到最後一次
ERR_WHERE = ("打開工作區", "第5步存檔", "組裝建版本", "成品記到版本", "開始執行")
# 打開工作區（GET）升級／補差異失敗後，這段時間內不再重試（不要每次打開第 3／4／5 步都整套重跑）
RETRY_AFTER_S = 300.0
# `render audio`（開發者指令，只組聲音軌）寫的處理紀錄：不建版本（沒有成品影片）
AUDIO_ONLY_SOURCE = "render audio"

# 卡片類型：第 3 步的七種（沿用 `review.page_data` 的 `類型`）＋第 5 步才會出現的兩種
STEP3_TYPES = ("學員段落", "改成老師", "名字", "學員名字", "重疊", "刪除段落", "局部消音")
UNLOGGED, UNCLEAR = "沒登記的變動", "待釐清"
# 卡片狀態
ALIVE, REMOVED, MERGED, SPLIT, GONE = "有效", "刪掉", "併入", "拆成", "消失"
# 卡片來源
SRC_AUTO, SRC_STEP3, SRC_STEP5, SRC_UPGRADE, SRC_ASSEMBLE = "自動", "第3步新增", "第5步新增", "升級時對不到", "組裝時對不到"
# 成品狀態
NOT_MADE, NOT_SEEN, PASSED, RETURNED = "還沒生成", "還沒看", "通過", "退回"
# 版本備註
NOTE_UPGRADE, NOTE_OLD_TOOL = "升級前的成品", "舊版工具組的"
# 等重做的起因
WHY_RETURNED, WHY_EDITED = "退回", "改了"

# 第 3 步卡片「做了什麼決定」的摘要欄位（同步副本；指紋只看這些，計時之類的欄位不算）
DECISION_KEYS = ("做法", "排法", "方式", "狀態", "決定", "說話者", "學員說話者", "已確認", "校對稿", "老師文字", "學員文字",
                 "救回", "tags", "代號", "問老師")
# 10-15 第1段審查修正（建議）、審查二修正：這幾欄（tags、已確認）會影響聲音，但靠處理紀錄的內容指紋把關（聲音真的變了，
# 處理紀錄那幾筆的內容指紋就會變），所以版本的「卡片指紋」不算它們——不算「這一版做了」、也不擋沿用上一版的通過；
# 問老師只是備註。`第3步決定指紋` 照舊全部算：改了照樣記歷史
NOT_SOUND_KEYS = ("已確認", "tags", "問老師")
# 第 3 步的檔：改了就要重新同步卡片（`覆核/覆核決定.json` 看內容、不看計時欄位，其他看大小＋修改時間）
STEP3_FILES = ("校對/段落.json", "名字覆核決定.json", "覆核/覆核決定.json", "學員名字覆核決定.json", "名字候選.json",
               "校對/刪除建議.json", "重疊.json", "學員名字候選.json")
REVIEW_TIMER_KEYS = ("覆核秒數",)

_lock = threading.RLock()
_held = threading.local()


# ---------- 路徑、時間、指紋 ----------

def needs_dir(workdir: str | Path) -> Path:
    return Path(workdir) / DIR_NAME


def _p(workdir: str | Path, name: str) -> Path:
    return needs_dir(workdir) / name


def table_path(workdir: str | Path, table: str) -> Path:
    return _p(workdir, f"{table}.json")


def exists(workdir: str | Path) -> bool:
    """這個工作區升級過了沒有（`修改需求/格式.json` 在不在）。"""
    return _p(workdir, FORMAT_FILE).is_file()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _fp(data) -> str:
    return wd.fingerprint(data)


def card_no(n: int) -> str:
    return f"K{n:04d}"


def _card_num(k: str) -> int:
    try:
        return int(str(k)[1:])
    except ValueError:
        return 0


def _r3(x) -> float | None:
    return None if x is None else round(float(x), 3)


# ---------- 讀寫（只有這幾支碰檔案） ----------

def _write(path: Path, data) -> None:
    """自己的寫檔（不受 `workdir.no_writes()` 影響；同樣先寫暫存檔再換上）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.寫入中")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def _locked(workdir: str | Path):
    """同一支程式用執行緒鎖；第 4 步組裝在另一支程式（子程式）也會寫版本，另外用檔案鎖（Mac、Linux／WSL）。"""
    with _lock:
        fh = None
        depth = getattr(_held, "n", 0)
        _held.n = depth + 1
        try:
            d = needs_dir(workdir)
            if depth == 0 and d.is_dir():   # 同一個執行緒裡面又鎖一次（巢狀）：檔案鎖只拿最外層那一次
                import fcntl

                fh = open(d / LOCK_FILE, "a+")
                fcntl.flock(fh, fcntl.LOCK_EX)
        except (ImportError, OSError):
            fh = None
        try:
            yield
        finally:
            _held.n = depth
            if fh is not None:
                try:
                    import fcntl

                    fcntl.flock(fh, fcntl.LOCK_UN)
                finally:
                    fh.close()


class Store:
    """修改需求紀錄的記憶體版（一次讀進來、改完一次寫回）。試跑升級時只在這裡算，不寫檔。"""

    def __init__(self) -> None:
        self.cards: dict[str, dict] = {}
        self.gen: dict[tuple, dict] = {}
        self.versions: dict[int, dict] = {}
        self.states: dict[tuple, dict] = {}
        self.history: list[dict] = []        # 這次新加的（寫的時候接在檔案後面）
        self.fmt: dict = {}

    # --- 卡片 ---
    def new_card(self, fields: dict, now: str) -> dict:
        n = max([int(self.fmt.get("下一個卡號") or 1)] + [_card_num(k) + 1 for k in self.cards])
        no = card_no(n)
        self.fmt["下一個卡號"] = n + 1
        row = {"卡號": no, "類型": None, "第3步鍵": None, "錨點": None, "原片": None, "來源": SRC_AUTO, "狀態": ALIVE,
               "指向": [], "第3步決定": {}, "第3步決定指紋": None, "等重做": None, "舊檔鍵": None,
               "建立時間": now, "更新時間": now, **fields}
        row["卡號"] = no
        self.cards[no] = row
        return row

    def alive_by_key(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for k in sorted(self.cards, key=_card_num):
            c = self.cards[k]
            if c.get("第3步鍵") and c["狀態"] in (ALIVE, REMOVED):
                out.setdefault(c["第3步鍵"], c)
        return out

    def by_anchor(self, anchor: str) -> dict | None:
        return next((self.cards[k] for k in sorted(self.cards, key=_card_num) if self.cards[k].get("錨點") == anchor), None)

    # --- 歷史 ---
    def log(self, now: str, card: str | None, step: int, action: str, before=None, after=None) -> None:
        n = int(self.fmt.get("下一個歷史序號") or 1)
        self.fmt["下一個歷史序號"] = n + 1
        self.history.append({"序號": n, "時間": now, "卡號": card, "步驟": step, "動作": action, "前": before, "後": after})

    # --- 版本 ---
    def latest_version(self) -> dict | None:
        return self.versions[max(self.versions)] if self.versions else None

    def version_at(self, stamp) -> dict | None:
        return next((v for v in self.versions.values() if stamp and v.get("時間") == stamp), None)


def _fmt(workdir: Path) -> dict:
    """只讀 `格式.json`（判斷要不要同步時用，不用讀整份紀錄）。"""
    try:
        return wd.read_json(_p(workdir, FORMAT_FILE), default=None) or {}
    except (OSError, ValueError):
        return {}


def _read_table(workdir: Path, table: str) -> list[dict]:
    data = wd.read_json(table_path(workdir, table), default=None) or {}
    return list(data.get("列") or [])


def _last_history_no(path: Path) -> int:
    """`歷史.jsonl` 最後一條的序號（沒有檔、讀不懂＝0）。只讀檔尾，不整份讀。"""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536))
            tail = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return 0
    for line in reversed(tail.splitlines()):
        if line.strip():
            try:
                return int(json.loads(line).get("序號") or 0)
            except (ValueError, AttributeError, TypeError):
                continue   # 10-15 第1段審查二修正：檔尾那一行寫到一半（中斷）→ 往前一行找
    return 0


def _ends_with_newline(path: Path) -> bool:
    """10-15 第1段審查二修正：檔案最後一個字元是不是換行（沒有檔、空檔＝是）。"""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                return True
            f.seek(-1, os.SEEK_END)
            return f.read(1) == b"\n"
    except OSError:
        return True


def load(workdir: str | Path) -> Store:
    """讀整份紀錄（沒有的表當空的）。"""
    w = Path(workdir)
    st = Store()
    st.fmt = wd.read_json(_p(w, FORMAT_FILE), default=None) or {}
    st.cards = {r["卡號"]: r for r in _read_table(w, T_CARDS)}
    st.gen = {(r["句id"], r["角色"]): r for r in _read_table(w, T_GEN)}
    st.versions = {int(r["版本號"]): r for r in _read_table(w, T_VERSIONS)}
    st.states = {(r["卡號"], int(r["版本號"])): r for r in _read_table(w, T_STATES)}
    # 10-15 第1段審查修正（必修 2）：下一個歷史序號跟 `歷史.jsonl` 最後一條比、取大的
    # （上次歷史寫了、`格式.json` 沒寫到就中斷時，序號不會重複）
    last = _last_history_no(_p(w, HISTORY_FILE))
    if last >= int(st.fmt.get("下一個歷史序號") or 1):
        st.fmt["下一個歷史序號"] = last + 1
    return st


def _table_data(table: str, rows: list[dict]) -> dict:
    return {"表": table, "格式版本": FORMAT_VERSION, "主鍵": list(TABLE_KEYS[table]), "列": rows}


def save(workdir: str | Path, st: Store, *, base: Path | None = None) -> None:
    """整份寫回（每張表一個檔；歷史只往下加）。base：寫到哪個資料夾（升級時是 `修改需求.升級中/`，平常是 `修改需求/`）。"""
    d = Path(base) if base is not None else needs_dir(workdir)
    d.mkdir(parents=True, exist_ok=True)
    _write(d / f"{T_CARDS}.json", _table_data(T_CARDS, [st.cards[k] for k in sorted(st.cards, key=_card_num)]))
    _write(d / f"{T_GEN}.json", _table_data(T_GEN, [st.gen[k] for k in sorted(st.gen)]))
    _write(d / f"{T_VERSIONS}.json", _table_data(T_VERSIONS, [st.versions[k] for k in sorted(st.versions)]))
    _write(d / f"{T_STATES}.json", _table_data(T_STATES, [st.states[k] for k in sorted(st.states, key=lambda x: (_card_num(x[0]), x[1]))]))
    if st.history:
        hp = d / HISTORY_FILE
        lead = "" if _ends_with_newline(hp) else "\n"   # 10-15 第1段審查二修正：上次寫到一半沒換行 → 先補一個，新的一條不黏在後面
        with open(hp, "a", encoding="utf-8") as f:
            f.write(lead)
            for h in st.history:
                f.write(json.dumps(h, ensure_ascii=False) + "\n")
        st.history = []
    st.fmt.setdefault("格式版本", FORMAT_VERSION)
    _write(d / FORMAT_FILE, st.fmt)   # 格式.json 最後寫：中途失敗時下次打開會重新升級，不會只寫一半就當作做完


def _save_fmt(workdir: str | Path, st: Store) -> None:
    """10-15 第1段審查修正（建議）：只有同步指紋變了（表沒變）時只寫 `格式.json`，不整份重寫。"""
    _write(_p(workdir, FORMAT_FILE), st.fmt)


def note_error(workdir: str | Path, where: str, e: BaseException) -> None:
    """10-15 第1段審查修正（建議）：雙寫／同步失敗記到 `格式.json` 的 `同步錯誤`（次數、第一次、最後時間、哪裡、錯誤種類；
    不記錯誤訊息，可能帶到資料內容）。`inspect 修改需求` 會印出來；第 2 段開始讀新紀錄前看 `healthy()`，有錯誤就退回讀舊檔。
    沒升級過的工作區不記。自己絕不丟錯（記不下來就算了，已經印過提醒）。"""
    try:
        w = Path(workdir)
        if not exists(w):
            return
        with _locked(w):
            fm = _fmt(w)
            if not fm:
                return
            old = fm.get(ERR_KEY) or {}
            now = _now()
            fm[ERR_KEY] = {"次數": int(old.get("次數") or 0) + 1, "第一次時間": old.get("第一次時間") or now, "最後時間": now,
                           "哪裡": where if where in ERR_WHERE else "其他", "種類": type(e).__name__}
            fm[LAST_ERR_KEY] = dict(fm[ERR_KEY])   # 10-15 第1段審查二修正：清掉之後 inspect 還看得到最後一次
            _write(_p(w, FORMAT_FILE), fm)
    except Exception:  # noqa: BLE001
        pass


def sync_errors(workdir: str | Path) -> dict | None:
    """`格式.json` 記的同步錯誤（沒有、或重新同步成功後清成 0 次＝None）。"""
    er = _fmt(Path(workdir)).get(ERR_KEY) or None
    return er if er and int(er.get("次數") or 0) > 0 else None


def healthy(workdir: str | Path) -> bool:
    """新紀錄能不能拿來讀：升級過、而且沒有還沒清掉的同步錯誤（第 2 段起讀新紀錄前先問這個，False 就讀舊檔）。
    10-15 第1段審查二修正：有錯誤時，下一次打開工作區（`ensure`）照舊檔整份重新同步一次（`resync`）；成功就把 `同步錯誤`
    清成 0 次（`最後一次錯誤` 留給 inspect 看）。要整個重建：把 `修改需求/` 改名，重新升級一次。"""
    w = Path(workdir)
    return exists(w) and not sync_errors(w)


def resync(workdir: str | Path) -> bool:
    """10-15 第1段審查二修正：有同步錯誤時照舊檔整份重新同步一次（第 3 步卡片、處理紀錄的版本、成品檢查的判斷，都強制重算），
    成功就把 `同步錯誤` 清成 0 次、留 `最後一次錯誤`。沒有錯誤什麼都不做。失敗照樣丟錯（呼叫的地方記錯誤）。回傳有沒有清。"""
    from bookclub import proclog

    w = Path(workdir)
    if not sync_errors(w):
        return False
    sync_cards(w, force=True)
    log = proclog.load(w)
    if log and not audio_only(log) and load(w).version_at(log.get("產生時間")) is None:
        add_version(w, log, note=NOTE_OLD_TOOL)
    mirror_final(w, force=True)
    with _locked(w):
        fm = _fmt(w)
        er = fm.get(ERR_KEY) or {}
        fm[LAST_ERR_KEY] = fm.get(LAST_ERR_KEY) or dict(er)
        fm[ERR_KEY] = {"次數": 0, "清掉時間": _now()}
        _write(_p(w, FORMAT_FILE), fm)
    return True


def read_history(workdir: str | Path) -> list[dict]:
    path = _p(workdir, HISTORY_FILE)
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


# ---------- 對外的查詢（只讀；給 inspect、測試、之後幾段用） ----------

def card_rows(workdir: str | Path) -> list[dict]:
    st = load(workdir)
    return [st.cards[k] for k in sorted(st.cards, key=_card_num)]


def version_rows(workdir: str | Path) -> list[dict]:
    st = load(workdir)
    return [st.versions[k] for k in sorted(st.versions)]


def state_rows(workdir: str | Path, version: int | None = None) -> list[dict]:
    st = load(workdir)
    return [r for k, r in sorted(st.states.items(), key=lambda x: (_card_num(x[0][0]), x[0][1]))
            if version is None or k[1] == version]


def gen_rows(workdir: str | Path) -> list[dict]:
    st = load(workdir)
    return [st.gen[k] for k in sorted(st.gen)]


def card_state(st: Store, card: str, version: int) -> str:
    """這張卡在這一版的狀態；這一版沒有它的列＝還沒生成（這一版沒處理到、或範圍外）。"""
    return (st.states.get((card, version)) or {}).get("狀態") or NOT_MADE


# ---------- 第 3 步卡片 → 卡片列（同步副本） ----------

def _turn_sentences(workdir: Path) -> dict[str, list[str]]:
    data = wd.read_json(Path(workdir) / "校對" / "段落.json", default=None) or {}
    return {t["id"]: list(t.get("句子") or []) for t in data.get("段落") or [] if t.get("id")}


def _anchor(it: dict, sents: dict[str, list[str]]) -> str:
    """第 3 步鍵對不上時找回同一張卡用的錨點（不放文字：名字用指紋）。"""
    kind = it["類型"]
    a = float(it.get("start") or 0.0)
    if kind in ("學員段落", "改成老師"):
        ss = sents.get(str(it.get("id"))) or []
        return "句子:" + ",".join(sorted(ss)) if ss else f"段落範圍:{a:.2f}-{float(it.get('end') or a):.2f}"
    if kind == "名字":
        if str(it.get("id", "")).startswith("NM"):
            return f"名字:{it['id']}"
        return "名字指紋:" + _fp([round(a, 1), it.get("matched_text", "")])
    if kind in ("重疊", "學員名字"):
        return f"{kind}:{it.get('id')}"
    return f"{kind}@{a:.1f}"


def _decision(it: dict) -> dict:
    d = {k: it[k] for k in DECISION_KEYS if k in it and it[k] not in (None, "", [])}
    whole = it.get("整句") or {}
    if isinstance(whole, dict) and whole.get("改稿"):
        d["改稿"] = whole["改稿"]
    if it.get("不用處理"):
        d["不用處理"] = True
    sug = (it.get("建議") or {}).get("做法")
    if sug:
        d["建議做法"] = sug
    return d


def _card_fields(it: dict, sents: dict[str, list[str]]) -> dict:
    dec = _decision(it)
    a, b = _r3(it.get("start")), _r3(it.get("end"))
    return {"類型": it["類型"], "第3步鍵": f"{it['類型']}:{it['id']}", "錨點": _anchor(it, sents), "原片": [a, b],
            "第3步決定": dec, "第3步決定指紋": _fp(dec)}


def _sent_set(card: dict) -> set[str]:
    a = str(card.get("錨點") or "")
    return set(a[3:].split(",")) - {""} if a.startswith("句子:") else set()


def _same_family(a: str, b: str) -> bool:
    fam = {"學員段落": "段落", "改成老師": "段落"}
    return fam.get(a, a) == fam.get(b, b)


def _apply_sync(st: Store, items: list[dict], restored: list[dict], deleted: set[str], sents: dict[str, list[str]],
                now: str, *, src_new: str = SRC_AUTO, step: int = 3) -> dict:
    """第 3 步現在的卡片（`review.page_data` 的 `項目`、`已還原`）跟卡片表對齊（純函式：只改 st）。

    對法：第 3 步鍵一樣 → 同一張；學員段落↔改成老師 同一個段落 id → 同一張（改類型）；鍵不在了、錨點一樣 → 同一張（改鍵）；
    其他建新卡。鍵不在了、沒對到的舊卡：人工新增刪掉的 → 刪掉；學員段落的句子整個併進另一張 → 併入；
    拆到好幾張 → 拆成；其他 → 消失。回傳 {新增, 更新, 刪掉, 併入, 拆成, 消失}。"""
    cur: list[tuple[dict, str]] = [(it, ALIVE) for it in items if it.get("類型") in STEP3_TYPES and it.get("id") is not None]
    cur += [(it, REMOVED) for it in restored if it.get("類型") in STEP3_TYPES and it.get("id") is not None]
    fields = [_card_fields(it, sents) for it, _s in cur]
    keys_now = {f["第3步鍵"] for f in fields}
    by_key = st.alive_by_key()
    used: set[str] = set()
    match: dict[int, dict] = {}
    for i, f in enumerate(fields):   # 1. 鍵一樣
        c = by_key.get(f["第3步鍵"])
        if c is not None and c["卡號"] not in used and _same_family(c["類型"], f["類型"]):
            match[i] = c
            used.add(c["卡號"])
    for i, f in enumerate(fields):   # 2. 學員段落↔改成老師（同一個段落）
        if i in match or f["類型"] not in ("學員段落", "改成老師"):
            continue
        tid = f["第3步鍵"].split(":", 1)[1]
        for alt in (f"學員段落:{tid}", f"改成老師:{tid}"):
            c = by_key.get(alt)
            if c is not None and c["卡號"] not in used and alt not in keys_now:
                match[i] = c
                used.add(c["卡號"])
                break
    for i, f in enumerate(fields):   # 3. 鍵不在了、錨點一樣（切段併段後 id 換了、名字順位換了）
        if i in match:
            continue
        for k in sorted(st.cards, key=_card_num):
            c = st.cards[k]
            if k in used or c["類型"] not in STEP3_TYPES or not _same_family(c["類型"], f["類型"]):
                continue
            if c.get("錨點") == f["錨點"] and (c.get("第3步鍵") not in keys_now or c["狀態"] in (GONE, MERGED, SPLIT)):
                match[i] = c
                used.add(k)
                break
    out = {"新增": 0, "更新": 0, "刪掉": 0, "併入": 0, "拆成": 0, "消失": 0}
    old_sents = {k: _sent_set(c) for k, c in st.cards.items() if c["類型"] == "學員段落"}   # 這次同步之前的句子
    for i, ((it, status), f) in enumerate(zip(cur, fields)):
        c = match.get(i)
        if c is None:
            src = SRC_STEP3 if it.get("人工新增") else src_new
            c = st.new_card({**f, "來源": src, "狀態": status}, now)
            used.add(c["卡號"])
            st.log(now, c["卡號"], step, "建立", None, {"類型": f["類型"], "第3步鍵": f["第3步鍵"], "原片": f["原片"],
                                                       "來源": src, "決定指紋": f["第3步決定指紋"]})
            out["新增"] += 1
            if f["類型"] == "學員段落":   # 切段：句子跟還在的另一張卡重疊 → 記一筆「從那張拆出來」
                mine = _sent_set(c)
                for k2 in sorted(old_sents, key=_card_num):
                    if mine and mine & old_sents[k2]:
                        st.log(now, c["卡號"], step, "從別張拆出", None, {"從": k2})
                        st.log(now, k2, step, "拆出一張", None, {"到": c["卡號"]})
            continue
        changed = False
        if c["第3步鍵"] != f["第3步鍵"]:
            st.log(now, c["卡號"], step, "改鍵", c["第3步鍵"], f["第3步鍵"])
            changed = True
        if c["類型"] != f["類型"]:
            st.log(now, c["卡號"], step, "改類型", c["類型"], f["類型"])
            changed = True
        if c.get("原片") != f["原片"]:
            st.log(now, c["卡號"], step, "改範圍", c.get("原片"), f["原片"])
            changed = True
        if c.get("第3步決定指紋") != f["第3步決定指紋"]:
            old = c.get("第3步決定") or {}
            diff = sorted(k for k in set(old) | set(f["第3步決定"]) if old.get(k) != f["第3步決定"].get(k))
            st.log(now, c["卡號"], step, "改了第3步決定", {"指紋": c.get("第3步決定指紋")},
                   {"指紋": f["第3步決定指紋"], "欄位": diff})
            changed = True
        if c["狀態"] != status:
            st.log(now, c["卡號"], step, "還原" if status == REMOVED else "恢復有效", c["狀態"], status)
            changed = True
        if changed or c.get("錨點") != f["錨點"]:
            c.update({**f, "狀態": status, "指向": [] if status == ALIVE else c.get("指向") or [], "更新時間": now})
            out["更新"] += changed
    alive_turns = [st.cards[k] for k in used if st.cards[k]["類型"] == "學員段落" and st.cards[k]["狀態"] == ALIVE]
    for k in sorted(st.cards, key=_card_num):
        c = st.cards[k]
        if k in used or c["類型"] not in STEP3_TYPES or c["狀態"] not in (ALIVE, REMOVED):
            continue
        if c.get("第3步鍵") in deleted:
            new, to = REMOVED, []
        elif c["類型"] == "學員段落" and _sent_set(c):
            mine = _sent_set(c)
            hits = [o for o in alive_turns if mine & _sent_set(o)]
            if len(hits) == 1 and mine <= _sent_set(hits[0]):
                new, to = MERGED, [hits[0]["卡號"]]
            elif hits:
                new, to = SPLIT, sorted(o["卡號"] for o in hits)
            else:
                new, to = GONE, []
        else:
            new, to = GONE, []
        if c["狀態"] == new and c.get("指向") == to:
            continue
        st.log(now, k, step, {REMOVED: "刪掉", MERGED: "併入", SPLIT: "拆成", GONE: "消失"}[new], c["狀態"],
               {"狀態": new, "指向": to})
        c.update({"狀態": new, "指向": to, "更新時間": now})
        out[{REMOVED: "刪掉", MERGED: "併入", SPLIT: "拆成", GONE: "消失"}[new]] += 1
    return out


def _deleted_keys(page: dict) -> set[str]:
    out = set()
    for x in page.get("已刪除") or []:
        if isinstance(x, dict) and x.get("類型") and x.get("id") is not None:
            out.add(f"{x['類型']}:{x['id']}")
    return out


def compute_page(workdir: str | Path) -> dict:
    """第 3 步現在的卡片（`review.page_data`），算的時候不寫任何舊檔（`workdir.no_writes`）。"""
    from bookclub import review

    with wd.no_writes():
        return review.page_data(Path(workdir))


def step3_snapshot(workdir: str | Path) -> dict:
    """第 3 步的檔現在的指紋（大小＋修改時間；覆核決定看內容、不看計時欄位）。"""
    w = Path(workdir)
    out = {}
    for rel in STEP3_FILES:
        p = w / rel
        if not p.is_file():
            out[rel] = None
            continue
        if rel == "覆核/覆核決定.json":
            try:
                data = wd.read_json(p, default={}) or {}
                out[rel] = _fp({k: v for k, v in data.items() if k not in REVIEW_TIMER_KEYS})
            except (OSError, ValueError):
                out[rel] = "讀不懂"
            continue
        s = p.stat()
        out[rel] = [s.st_size, s.st_mtime_ns]
    return out


def sync_cards(workdir: str | Path, page: dict | None = None, *, force: bool = False,
               snap: dict | None = None) -> dict | None:
    """第 3 步寫入之後，把卡片表跟第 3 步的檔對齊（同步副本＋歷史）。page 給了就用（`GET /api/review` 已經算好的），
    沒給才自己算（不寫舊檔）。第 3 步的檔沒變時什麼都不做。沒升級過的工作區不做。回傳統計或 None。
    10-15 第1段審查修正（建議）：snap＝算 page **之前**拍的第 3 步快照（page 給了就要一起給）。記的是這份快照：
    算 page 的期間第 3 步又被改了（另一個請求 POST），下次快照不一樣就會再同步，不會漏。"""
    w = Path(workdir)
    if not exists(w):
        return None
    if snap is None:
        snap = step3_snapshot(w)
    if not force and (_fmt(w).get("同步") or {}).get("第3步") == snap:   # 第 3 步的檔沒變：page 算出來也一樣
        return None
    if page is None:
        page = compute_page(w)
    with _locked(w):
        st = load(w)
        before = json.dumps(st.cards, ensure_ascii=False, sort_keys=True)
        now = _now()
        res = _apply_sync(st, page.get("項目") or [], page.get("已還原") or [], _deleted_keys(page), _turn_sentences(w),
                          now, src_new=SRC_AUTO)
        st.fmt.setdefault("同步", {})["第3步"] = snap
        if st.history or json.dumps(st.cards, ensure_ascii=False, sort_keys=True) != before:
            save(w, st)
        else:   # 卡片沒變（例如只改了計時以外、不影響卡片的欄位）：只記同步指紋
            _save_fmt(w, st)
    return res


# ---------- 處理紀錄的每一筆 → 卡片 ----------

def _index(workdir: Path) -> dict:
    from bookclub import review

    try:
        with wd.no_writes():
            return review.item_index(workdir)
    except Exception:  # noqa: BLE001 — 對不出來就只用卡片表裡的鍵
        return {}


def _resolve(keys: list[str], index: dict, by_key: dict[str, dict]) -> list[str]:
    """處理紀錄的 `覆核項目`（`名字:2`、`學員段落:O69.60`⋯）→ 卡號（經 `review.item_index` 的 `第3步` 找主卡）。"""
    out: list[str] = []
    for k in keys or []:
        card3 = (index.get(k) or {}).get("第3步") or k
        c = by_key.get(card3) or by_key.get(k)
        if c is not None and c["卡號"] not in out:
            out.append(c["卡號"])
    return out


def _record_key(r: dict) -> str:
    from bookclub.finalcheck import record_key

    return record_key(r)


def _unlogged_key(u: dict) -> str:
    from bookclub.finalcheck import unlogged_key

    return unlogged_key(u)


def _extra_card(st: Store, kind: str, anchor: str, rng, src: str, old_key: dict, now: str, step: int) -> dict:
    """待釐清、沒登記的變動卡：同一個錨點已經有就沿用。"""
    c = st.by_anchor(anchor)
    if c is not None:
        return c
    c = st.new_card({"類型": kind, "錨點": anchor, "原片": rng, "來源": src, "舊檔鍵": old_key}, now)
    st.log(now, c["卡號"], step, "建立", None, {"類型": kind, "原片": rng, "來源": src})
    return c


def map_records(st: Store, log: dict, index: dict, now: str, *, src: str = SRC_ASSEMBLE, step: int = 4) -> dict:
    """處理紀錄 → {紀錄卡號: {紀錄鍵: [卡號]}, 未登記卡號: {鍵: 卡號}, 沒對到卡片的紀錄: [紀錄鍵]}（改 st：
    對不到卡片、又動到聲音的紀錄建「待釐清」卡；每一處沒登記的變動一張卡）。"""
    by_key = st.alive_by_key()
    rec_cards: dict[str, list[str]] = {}
    lost: list[str] = []
    for r in (log or {}).get("紀錄") or []:
        rk = _record_key(r)
        cs = _resolve(r.get("覆核項目") or [], index, by_key)
        if not cs and r.get("動到聲音"):
            o = r.get("原片") or [None, None]
            c = _extra_card(st, UNCLEAR, f"處理紀錄:{rk}", [_r3(o[0]), _r3(o[1])] if o and o[0] is not None else None,
                            src, {"處理紀錄": rk}, now, step)
            cs = [c["卡號"]]
        if cs:
            rec_cards.setdefault(rk, [])
            rec_cards[rk] += [x for x in cs if x not in rec_cards[rk]]
        else:
            lost.append(rk)
    un: dict[str, str] = {}
    for u in (log or {}).get("未登記的變動") or []:
        uk = _unlogged_key(u)
        o = u.get("原片") or [None, None]
        c = _extra_card(st, UNLOGGED, f"沒登記:{uk}", [_r3(o[0]), _r3(o[1])], SRC_AUTO, {"未登記": uk}, now, step)
        un[uk] = c["卡號"]
    return {"紀錄卡號": rec_cards, "未登記卡號": un, "沒對到卡片的紀錄": lost}


def _card_prints(st: Store, log: dict, rec_cards: dict[str, list[str]]) -> dict[str, str]:
    """這一版裡每張卡的指紋：用到的處理紀錄內容指紋（生成檔內容、接縫做法、文字、原片起訖⋯，`finalcheck.content_print`）
    ＋卡片的第 3 步決定指紋。下一版比這個就知道哪幾張變了。"""
    from bookclub.finalcheck import content_print

    per: dict[str, list[str]] = {}
    for r in (log or {}).get("紀錄") or []:
        for c in rec_cards.get(_record_key(r)) or []:
            per.setdefault(c, []).append(content_print(r))
    return {c: _fp([sorted(ps), _sound_print(st.cards.get(c) or {})]) for c, ps in per.items()}


def _sound_print(card: dict) -> str | None:
    """10-15 第1段審查修正（建議）：第 3 步決定裡會影響聲音的欄位的指紋（`已確認`、`tags`、`問老師` 不算）。"""
    dec = card.get("第3步決定")
    if not dec:
        return card.get("第3步決定指紋")
    return _fp({k: v for k, v in dec.items() if k not in NOT_SOUND_KEYS})


def audio_only(log: dict | None) -> bool:
    """10-15 第1段審查修正（建議）：`render audio`（開發者指令，只組聲音軌、沒有成品影片）寫的處理紀錄，不建版本。"""
    return bool(log) and str(log.get("來源") or "") == AUDIO_ONLY_SOURCE


def _card_spans(log: dict, rec_cards: dict[str, list[str]]) -> dict[str, list]:
    out: dict[str, list] = {}
    for r in (log or {}).get("紀錄") or []:
        o = r.get("成品") or [None, None]
        if o[0] is None or o[1] is None:
            continue
        for c in rec_cards.get(_record_key(r)) or []:
            a, b = out.get(c) or [o[0], o[1]]
            out[c] = [_r3(min(a, o[0])), _r3(max(b, o[1]))]
    return out


def _version_dir(n: int) -> str:
    return f"版本/v{n}"


def _log_tag(log: dict) -> str | None:
    from bookclub.finalcheck import log_tag

    return log_tag(log)


def _ts(x) -> float | None:
    """10-15 第2段審查修正：時間字串（有沒有時區都可以）→ 秒數（比先後用）；讀不懂＝None。沒寫時區的當本機時間。"""
    if not x:
        return None
    try:
        d = datetime.fromisoformat(str(x))
    except ValueError:
        return None
    return d.timestamp()


def _in_range(card: dict, v: dict | None) -> bool:
    """10-15 第5步2.0 第3段：卡片的原片時間跟這一版的範圍有沒有重疊（範圍、原片不知道的當作有，寧可多算）。"""
    rng = (v or {}).get("範圍")
    o = card.get("原片") or [None, None]
    if not rng or rng[0] is None or rng[1] is None or o[0] is None:
        return True
    b = o[1] if o[1] is not None else o[0]
    return float(b) >= float(rng[0]) - 0.05 and float(o[0]) <= float(rng[1]) + 0.05


def carry_source(st: Store, card: str, n: int, base: int | None = None) -> tuple[int, dict] | None:
    """10-15 第5步2.0 第3段：新的一版（n）這張卡的狀態要跟哪一版比（#23、#221）：有指定基礎版本（開發者「以這一版為基礎
    再組裝」）先看那一版；其他照版本號往回找，第一個有這張卡狀態列、也記了這張卡指紋的那一版。
    以前只看上一版：範圍版本（先 0-97、再 0-3、又 0-97）中間那一版沒有這張卡，第 1 版的通過就帶不過來。回傳 (版本號, 狀態列)。"""
    order = ([base] if base and base < n else []) + sorted((k for k in st.versions if k < n and k != base), reverse=True)
    for m in order:
        row = st.states.get((card, m))
        if row and card in ((st.versions.get(m) or {}).get("卡片指紋") or {}):
            return m, row
    return None


def build_version(st: Store, workdir: Path, log: dict, index: dict, now: str, *, note: str | None = None,
                  products: list[str] | None = None, src: str = SRC_ASSEMBLE, step: int = 4) -> dict:
    """處理紀錄 → 版本表一列（改 st，不寫檔；處理紀錄副本由呼叫的地方寫）。同一份處理紀錄（產生時間一樣）只建一次。
    10-15 第5步2.0 第3段：
    - 版本號：處理紀錄帶了 `版本號`（組裝時先預留的，成品檔名用同一個號碼，`next_version_no`）就用它，不然照順序加一
    - 以哪一版為基礎：開發者「以這一版為基礎再組裝」記的那一版（`set_next_base`，用一次就清掉），沒有就是上一版
    - 每張卡的起始狀態照 `carry_source`（不只看上一版）：指紋沒變、那一版通過 → 通過（沿用）
    - `其他卡指紋`：這一版沒有處理紀錄的卡（第 3 步選定不修改、範圍外）當時的聲音指紋——第 3 步把它改成要處理，第 5 步看得出來"""
    have = st.version_at(log.get("產生時間"))
    if have is not None:
        return have
    top = max(st.versions) if st.versions else 0
    want = log.get("版本號")
    n = int(want) if isinstance(want, int) and not isinstance(want, bool) and want > top else top + 1
    base = st.fmt.pop("下次組裝的基礎版本", None)
    base = int(base) if isinstance(base, int) and base in st.versions else None
    prev = st.latest_version()
    m = map_records(st, log, index, now, src=src, step=step)
    prints = _card_prints(st, log, m["紀錄卡號"])
    old = (prev or {}).get("卡片指紋") or {}
    done = sorted((c for c in prints if old.get(c) != prints[c]), key=_card_num)
    methods = sorted({Path(p).stem.rsplit("_", 1)[-1] for p in products or []})
    others = {k: _sound_print(c) for k, c in st.cards.items()
              if k not in prints and c["狀態"] == ALIVE and c["類型"] in STEP3_TYPES}
    row = {"版本號": n, "時間": log.get("產生時間"), "處理紀錄產生時間": log.get("產生時間"), "來源": log.get("來源"),
           "範圍": log.get("範圍"), "標記": _log_tag(log), "輸出做法": methods,
           "以哪一版為基礎": base or (prev["版本號"] if prev else None),
           "處理紀錄": f"{DIR_NAME}/{_version_dir(n)}/處理紀錄.json", "成品檔": list(products or []),
           "卡片指紋": prints, "這一版做了哪幾張": done, "影片還在": True, "輸出紀錄": [], "備註": note,
           "紀錄卡號": m["紀錄卡號"], "未登記卡號": m["未登記卡號"], "沒對到卡片的紀錄": m["沒對到卡片的紀錄"],
           "成品時間": _card_spans(log, m["紀錄卡號"]), "其他卡指紋": others, "建立時間": now}
    st.versions[n] = row
    # 10-15 第5步2.0 第2段：第 5 步按「改」改過的卡（等重做，起因：改了）——組裝會照第 3 步現在的檔做，改過的都做進這一版了。
    # 10-15 第2段審查修正（建議）：只清「改」的時間早於這份處理紀錄產生時間的（處理紀錄產生之後才改的，這一版沒有做進去）
    # 10-15 第5步2.0 第3段：在舊版上退回的（等重做帶 `版本號`）——這一版有它、而且是退回之後才組的，就是重做進來了
    made = _ts(log.get("產生時間"))
    no_carry: set[str] = set()   # 10-15 宇軒驗證回饋四：範圍外按「要修改」記了原因的卡——重做進這一版後不沿用之前的通過
    for k in sorted(st.cards, key=_card_num):
        c = st.cards[k]
        pend = c.get("等重做") or {}
        later = made is None or (_ts(pend.get("時間")) or 0) <= made
        # 10-15 第3段審查修正（必修 1）：舊版退回的要「這一版真的重新做了它」才清——在 `done`（指紋跟上一版不同），
        # 而且指紋也跟退回的那一版不同（上一版範圍外沒有它時，`done` 也會有它）。只是範圍又包到它、指紋沒變不算重做
        # （以前用 `prints`，等重做被清、歷史寫「重做進第 N 版」，其實沒重新生成）
        redone = False
        if pend.get("版本號") and k in done and later:
            at = (st.versions.get(int(pend["版本號"])) or {}).get("卡片指紋") or {}
            redone = at.get(k) != prints[k]
        if (pend.get("起因") == WHY_EDITED and later) or redone:
            if pend.get("退回原因") is not None:
                no_carry.add(k)
            c["等重做"] = None
            c["更新時間"] = now
            st.log(now, k, step, f"重做進第{n}版", {"起因": pend.get("起因")}, None)
    for c in done:
        st.log(now, c, step, f"組裝進第{n}版", None, {"版本號": n, "指紋": prints[c]})
    # 新版每張卡的起始狀態：指紋沒變、比的那一版通過 → 通過（沿用）；其他 → 還沒看（第 5 步的判斷由 mirror_final 接著算）
    for c in prints:
        src_v = carry_source(st, c, n, base)
        ps = src_v[1] if src_v else None
        carry = bool(ps and ps.get("狀態") == PASSED and (st.versions[src_v[0]].get("卡片指紋") or {}).get(c) == prints[c])
        carry = carry and c not in no_carry   # 10-15 宇軒驗證回饋四：老師說要修改的，重做後要重新看
        st.states[(c, n)] = {"卡號": c, "版本號": n, "成品": row["成品時間"].get(c), "狀態": PASSED if carry else NOT_SEEN,
                             "退回原因": None, "沿用自": (ps.get("沿用自") or src_v[0]) if carry else None,
                             "沿用來源": src_v[0] if carry else None,
                             "紀錄鍵": sorted(k for k, cs in m["紀錄卡號"].items() if c in cs), "更新時間": now}
    for uk, c in m["未登記卡號"].items():
        st.states.setdefault((c, n), {"卡號": c, "版本號": n, "成品": None, "狀態": NOT_SEEN, "退回原因": None,
                                      "沿用自": None, "紀錄鍵": [f"{UNLOGGED}@{uk}"], "更新時間": now})
    return row


def _write_version_copy(workdir: Path, n: int, log: dict, *, base: Path | None = None) -> None:
    _write((Path(base) if base is not None else needs_dir(workdir)) / _version_dir(n) / "處理紀錄.json", log)


def add_version(workdir: str | Path, log: dict, *, note: str | None = None) -> dict | None:
    """第 4 步組裝完、換上成品、寫處理紀錄的那一刻（`proclog._write`）：建一個版本（含範圍、處理紀錄副本、
    每張卡的指紋與起始狀態）。沒升級過的工作區不建（之後升級時把最新的處理紀錄當版本 1）。回傳版本列。"""
    w = Path(workdir)
    if not exists(w) or not log or audio_only(log):   # 10-15：render audio 不建版本
        return None
    sync_cards(w)   # 第 3 步改過還沒同步的先同步，鍵才對得上
    index = _index(w)
    with _locked(w):
        st = load(w)
        have = st.version_at(log.get("產生時間"))
        if have is not None:
            return have
        prev = st.latest_version()
        row = build_version(st, w, log, index, _now(), note=note)
        _write_version_copy(w, row["版本號"], log)
        st.fmt.setdefault("同步", {})["處理紀錄"] = log.get("產生時間")
        _update_gen_rounds(st, w, _now())
        save(w, st)
        _keep_prev_watched(w, prev)
    return row


def _keep_prev_watched(w: Path, prev: dict | None) -> None:
    """10-15 第3段審查修正（建議）：建新版時，上一版在成品檢查檔裡的看過區段（還是上一版那份處理紀錄的）補進 `看過.json`
    ——升級前看的、沒經過 `add_watched` 雙寫的也留得住，之後切回上一版看過比例不會是 0%。成品檢查檔換新後就沒有這一份了。
    檢查的成品影片不是上一版 `成品檔` 裡的、或手動換到舊成品（`手動選擇.舊的`）時不補（那些看過區段是別支成品的時間）。"""
    if not prev:
        return
    try:
        from bookclub import finalcheck as fc

        check = fc.load_check(w)
        if check.get("處理紀錄產生時間") != prev.get("時間") or not check.get("看過區段"):
            return
        # 10-15 第3段審查二修正：檢查的那支要是上一版的成品——手動換到舊成品（`手動選擇.舊的`）看過的不是上一版的時間
        if (check.get("手動選擇") or {}).get("舊的") or check.get("成品影片") not in (prev.get("成品檔") or []):
            return
        n = int(prev["版本號"])
        have = watched(w, n)
        merged = fc.merge_ranges(have + [[float(a), float(b)] for a, b in check["看過區段"]])
        if merged != have:
            set_watched(w, n, merged)
    except Exception as e:  # noqa: BLE001 — 補不到不擋建版本（只是看過比例，不記同步錯誤、不讓第 5 步退回讀舊檔）
        print(f"[修改需求紀錄] ⚠️ 上一版的看過區段沒補進看過.json：{type(e).__name__}")


def add_product(workdir: str | Path, stamp: str | None, rel: str) -> None:
    """組裝驗證通過、換上正式檔名的成品記到那一版（`render` 每換上一支呼叫一次）。"""
    w = Path(workdir)
    if not exists(w) or not stamp:
        return
    with _locked(w):
        st = load(w)
        v = st.version_at(stamp)
        if v is None or rel in (v.get("成品檔") or []):
            return
        v.setdefault("成品檔", []).append(rel)
        m = Path(rel).stem.rsplit("_", 1)[-1]
        if m not in (v.get("輸出做法") or []):
            v["輸出做法"] = sorted({*(v.get("輸出做法") or []), m})
        save(w, st)


def next_version_no(workdir: str | Path) -> int | None:
    """10-15 第5步2.0 第3段：下一次組裝會是第幾版（成品檔名 `成品_v<N>_…` 先用這個號碼；處理紀錄帶著它，建版本時用同一個）。
    沒升級過的工作區回 None（成品照舊的檔名，之後升級時記現有檔名當版本 1）。"""
    w = Path(workdir)
    if not exists(w):
        return None
    st = load(w)
    return (max(st.versions) if st.versions else 0) + 1


def set_next_base(workdir: str | Path, n: int | None) -> None:
    """10-15 第5步2.0 第3段（開發者）：下一次組裝「以第 n 版為基礎」（None＝清掉，照上一版）。每次第 4 步開始都會設一次。"""
    w = Path(workdir)
    if not exists(w):
        return
    with _locked(w):
        st = load(w)
        if n is not None and int(n) not in st.versions:
            raise ValueError(f"沒有第 {n} 版")
        if st.fmt.get("下次組裝的基礎版本") == (int(n) if n is not None else None):
            return
        if n is None:
            st.fmt.pop("下次組裝的基礎版本", None)
        else:
            st.fmt["下次組裝的基礎版本"] = int(n)
        _save_fmt(w, st)


def version_video(workdir: str | Path, v: dict | None) -> str | None:
    """10-15 第5步2.0 第3段：這一版的成品影片（相對工作區；第一支還在的、不是標字版）。影片清掉了、這一版沒記成品回 None。"""
    if not v or v.get("影片還在") is False:
        return None
    for rel in v.get("成品檔") or []:
        if "標字版" not in Path(rel).stem and (Path(workdir) / rel).is_file():
            return rel
    return None


WATCHED_FILE = "看過.json"


def watched(workdir: str | Path, n: int) -> list:
    """10-15 第5步2.0 第3段（31 號設計 §3.7）：第 n 版看過的區段（那一版的成品時間）。"""
    data = wd.read_json(_p(workdir, WATCHED_FILE), default=None) or {}
    row = next((r for r in data.get("列") or [] if int(r.get("版本號") or 0) == int(n)), None)
    return list((row or {}).get("看過區段") or [])


def set_watched(workdir: str | Path, n: int, ranges: list) -> None:
    """10-15 第5步2.0 第3段：看過區段每版一份（`修改需求/看過.json`，放成品狀態旁）。最新一版照舊也寫成品檢查檔（雙寫）。"""
    w = Path(workdir)
    if not exists(w):
        return
    with _locked(w):
        data = wd.read_json(_p(w, WATCHED_FILE), default=None) or {"表": "看過", "主鍵": ["版本號"], "列": []}
        rows = [r for r in data.get("列") or [] if int(r.get("版本號") or 0) != int(n)]
        rows.append({"版本號": int(n), "看過區段": [[round(float(a), 3), round(float(b), 3)] for a, b in ranges],
                     "更新時間": _now()})
        data["列"] = sorted(rows, key=lambda r: int(r["版本號"]))
        _write(_p(w, WATCHED_FILE), data)


EXPORT_KEYS = ("時間", "來源", "檔案", "檢查完才輸出", "看過百分比", "沒通過筆數", "版本號")


def add_export(workdir: str | Path, n: int, rec: dict) -> None:
    """10-15 第5步2.0 第3段（31 號設計 §6.7）：從第 n 版輸出了一支最終成品——記到那一版的輸出紀錄。
    最新一版的輸出另外由 `apply_check` 從成品檢查檔同步（這裡也記，重複的不會再加）。"""
    w = Path(workdir)
    if not exists(w):
        return
    with _locked(w):
        st = load(w)
        v = st.versions.get(int(n))
        if v is None:
            return
        item = {k: rec.get(k) for k in EXPORT_KEYS}
        if any(x.get("檔案") == item["檔案"] for x in v.get("輸出紀錄") or []):
            return
        v.setdefault("輸出紀錄", []).append(item)
        save(w, st)


def decide_version(workdir: str | Path, n: int, card: str, result: str | None, options: list[str] | None = None,
                   extra: str = "") -> dict:
    """10-15 第5步2.0 第3段（31 號設計 §6.5）：在舊版（不是最新處理紀錄那一版）上按通過／退回／取消——記在那一版的成品狀態，
    不寫成品檢查檔（舊檔只認得最新那一份處理紀錄）。10-15 第3段審查修正：舊版只能通過／取消，退回回 ValueError
    （下面留著的退回分支給第 4 段開放用：那張卡等重做，起因退回、帶 `版本號`；之後組裝真的重做了它才清掉）。
    這一版沒處理到這張卡（沒有狀態列）不能判斷。回傳那一列。"""
    w = Path(workdir)
    if result not in (PASSED, RETURNED, None):
        raise ValueError("只能選：通過、退回")
    if result == RETURNED:
        # 10-15 第3段審查修正（必修 1，宇軒定前暫定）：舊版只能通過——舊版上退回，第 4 步看不到（舊檔只認最新那一份處理紀錄），
        # 組裝後等重做被清、歷史寫「重做進第 N 版」卻沒有重新生成。31 號設計 §5.1 按卡片重做做完（第 4 段）再開放
        raise ValueError("要退回請回到最新一版")
    with _locked(w):
        st = load(w)
        row = st.states.get((card, int(n)))
        if row is None:
            raise ValueError("這一版沒有這張卡，不能判斷")   # 10-15 宇軒驗證回饋三：舊版上（不一定是上一版通過的）
        now = _now()
        c = st.cards.get(card) or {}
        new = NOT_SEEN if result is None else result
        why = None
        if new == RETURNED:
            why = {"選項": [o for o in REASON_OPTIONS if o in (options or [])], "補充": str(extra or "").strip()}
        if row.get("狀態") != new or row.get("退回原因") != why:
            st.log(now, card, 5, {PASSED: "通過", RETURNED: "退回", NOT_SEEN: "改回還沒看"}[new], row.get("狀態"),
                   {"版本號": int(n), **({"原因字數": len(why["補充"])} if why else {})})
        row.update({"狀態": new, "退回原因": why, "沿用自": None, "沿用來源": None, "更新時間": now,
                    **({"取消沿用": True} if new != PASSED and row.get("沿用自") else {})})
        pend = c.get("等重做") or {}
        if new == RETURNED and c and not pend:
            c["等重做"] = {"起因": WHY_RETURNED, "時間": now, "版本號": int(n)}
            c["更新時間"] = now
            st.log(now, card, 5, "等重做", None, {"起因": WHY_RETURNED, "版本號": int(n)})
        elif new != RETURNED and pend.get("版本號") == int(n):
            c["等重做"] = None
            c["更新時間"] = now
            st.log(now, card, 5, "取消等重做", pend, None)
        save(w, st)
        return dict(row)


def uncarry(workdir: str | Path, card: str, n: int) -> bool:
    """10-15 第5步2.0 第3段：最新一版按「取消通過」的卡，通過是沿用別版來的（`沿用來源` 不是上一版：成品檢查檔沒有這一筆）
    → 記 `取消沿用`、改回還沒看（`apply_check` 之後不再把它當沿用的通過留著）。回傳有沒有改。"""
    w = Path(workdir)
    if not exists(w):
        return False
    with _locked(w):
        st = load(w)
        row = st.states.get((card, int(n)))
        if not row or row.get("狀態") != PASSED or not row.get("沿用自"):
            return False
        now = _now()
        st.log(now, card, 5, "改回還沒看", PASSED, {"版本號": int(n), "取消沿用": True})
        row.update({"狀態": NOT_SEEN, "沿用自": None, "沿用來源": None, "取消沿用": True, "更新時間": now})
        save(w, st)
        return True


# ---------- 生成句表 ----------

ROLE_KEYS = ("老師", "學員", "保留原聲學員")


def _gen_keys(role: str, x: dict) -> list[str]:
    if role == "學員":
        if x.get("重疊"):
            return [f"重疊:{x['重疊']}"]
        return [f"學員段落:{x['段落']}"] if x.get("段落") else []
    if role == "老師":
        return [f"名字:{c}" for c in x.get("候選") or []] + [f"重疊:{o}" for o in x.get("重疊項目") or []]
    return [f"學員名字:{c}" for c in x.get("候選") or []]


def _role_log(workdir: Path, role: str) -> tuple[dict, dict]:
    """這個角色的生成紀錄 {id: 句子} 與 `_重新生成版本.json`。"""
    from bookclub import finalcheck, tts

    log_path, od = finalcheck._role_paths(workdir, role)
    recs = {r.get("id"): r for r in (wd.read_json(log_path, default=None) or {}).get("句子") or [] if r.get("id")}
    vers = wd.read_json(od / tts.REDO_VERSIONS, default=None) or {}
    return recs, vers


def _seed(r: dict) -> int | None:
    tries = [a for a in r.get("嘗試") or [] if a.get("種子") is not None]
    sel = r.get("選定")
    return tries[sel - 1]["種子"] if isinstance(sel, int) and 0 < sel <= len(tries) else None


def _update_gen_rounds(st: Store, workdir: Path, now: str) -> None:
    """生成句表的「第幾版」「種子」照生成紀錄與 `_重新生成版本.json` 更新（那兩份檔照舊由 tts 讀寫，這裡只記對應）。"""
    for role in ROLE_KEYS:
        try:
            recs, vers = _role_log(workdir, role)
        except Exception:  # noqa: BLE001
            continue
        for (sid, rl), row in st.gen.items():
            if rl != role:
                continue
            r = recs.get(sid)
            n = int((r or {}).get("第幾版") or len((vers.get(sid) or {}).get("以前的版本") or []) + 1)
            seed = _seed(r) if r else None
            if row.get("第幾版") != n or (seed is not None and row.get("種子") != seed):
                row.update({"第幾版": n, "種子": seed if seed is not None else row.get("種子"), "更新時間": now})


def _apply_gen_plan(st: Store, ctx: dict, index: dict, now: str, how: str = "計畫") -> dict:
    by_key = st.alive_by_key()
    n_ok, lost = 0, []
    for role in ROLE_KEYS:
        for x in ctx.get(role) or []:
            sid = x.get("id")
            if not sid:
                continue
            cards = _resolve(_gen_keys(role, x), index, by_key)
            if not cards:
                lost.append(f"{role}:{sid}")
                continue
            n_ok += 1
            row = st.gen.get((sid, role))
            if row is None:
                st.gen[(sid, role)] = {"句id": sid, "角色": role, "卡號": cards, "第幾版": None, "種子": None,
                                       "對應方式": how, "更新時間": now}
                for c in cards:
                    st.log(now, c, 4, "排進生成", None, {"句id": sid, "角色": role})
            elif row.get("卡號") != cards:
                row.update({"卡號": cards, "對應方式": how, "更新時間": now})
    return {"對上": n_ok, "對不到": lost}


def _apply_old_gen(st: Store, workdir: Path, index: dict, now: str) -> dict:
    """升級：生成紀錄裡有、這次的計畫裡沒有的句子（例如學員以前在範圍裡數的序號、名字順位換過）。
    學員照紀錄的 `段落`；老師照 id 的候選編號（S12／N12 → 名字:12，標「照編號猜」）；其他對不到就不寫
    （那張卡下次重做時照新規則重排計畫，等於重新生成，不會漏）。"""
    import re

    by_key = st.alive_by_key()
    n_ok, lost = 0, []
    for role in ROLE_KEYS:
        try:
            recs, _vers = _role_log(workdir, role)
        except Exception:  # noqa: BLE001
            continue
        for sid, r in recs.items():
            if (sid, role) in st.gen:
                continue
            keys, how = [], "照段落"
            if role == "學員":
                keys = _gen_keys(role, r) or ([f"學員段落:{str(sid).rsplit('_', 1)[0]}"] if "_" in str(sid) else [])
            elif role == "老師":
                keys = _gen_keys(role, r)
                if not keys:
                    m = re.match(r"^[SN](\d+)$", str(sid))
                    keys, how = ([f"名字:{int(m.group(1))}"] if m else []), "照編號猜"
            else:
                keys = _gen_keys(role, r)
            cards = _resolve(keys, index, by_key)
            if not cards:
                lost.append(f"{role}:{sid}")
                continue
            n_ok += 1
            st.gen[(sid, role)] = {"句id": sid, "角色": role, "卡號": cards, "第幾版": None, "種子": None,
                                   "對應方式": how, "更新時間": now}
    return {"對上": n_ok, "對不到": lost}


def plan_ctx(workdir: str | Path) -> dict:
    """現在排出來要生成的句子（不載入模型；跟第 4 步重做退回同一份：`finalcheck._redo_ctx`）。"""
    from bookclub import finalcheck

    with wd.no_writes():
        return finalcheck._redo_ctx(Path(workdir))


def record_gen_plan(workdir: str | Path, ctx: dict | None = None) -> dict | None:
    """第 4 步排計畫的當下：記下每一句生成句是為了哪幾張卡（取代以前用前綴＋時間誤差猜）。沒升級過的不做。"""
    w = Path(workdir)
    if not exists(w):
        return None
    ctx = plan_ctx(w) if ctx is None else ctx
    index = _index(w)
    with _locked(w):
        st = load(w)
        now = _now()
        res = _apply_gen_plan(st, ctx, index, now)
        _update_gen_rounds(st, w, now)
        save(w, st)
    return res


def on_execute_start(workdir: str | Path, log=print) -> None:
    """第 4 步開始執行時（`execute.run_execute`）：第 3 步改過的先同步卡片，再記生成句。失敗只提醒、不擋執行。"""
    w = Path(workdir)
    if not exists(w):
        return
    try:
        sync_cards(w)
        record_gen_plan(w)
    except Exception as e:  # noqa: BLE001
        note_error(w, "開始執行", e)   # 10-15 第1段審查修正（建議）：同步失敗記下來
        log(f"[AI 執行] ⚠️ 修改需求紀錄沒更新到（不影響執行）：{type(e).__name__}")


# ---------- 第 5 步：成品檢查 → 每張卡在那一版的狀態（雙寫） ----------

def _check_print(check: dict) -> str:
    """成品檢查裡跟卡片狀態有關的部分的指紋（看過區段之類的不算：播放時常常存檔，那時不用重算）。"""
    doing = check.get("重做中") or {}
    return _fp({"逐筆": {k: [v.get("結果"), v.get("原因")] for k, v in (check.get("逐筆") or {}).items()},
                "未登記": {k: [v.get("結果"), v.get("原因")] for k, v in (check.get("未登記確認") or {}).items()},
                "整片": [[x.get("id"), x.get("原片秒"), x.get("覆核項目"), x.get("原因")] for x in check.get("整片退回") or []],
                "重做中": [[e.get("鍵"), e.get("覆核項目")] for e in doing.get("項目") or []],
                "輸出": len(check.get("輸出紀錄") or []), "處理紀錄": check.get("處理紀錄產生時間")})


# 10-15 第5步2.0 第2段：退回原因的選項（31 號設計 §9 問題 1 定案方案甲：照「怎麼修」分；「其實是老師的聲音」不放這裡，
# 是卡片上「裡面有老師的聲音」按鈕）。舊檔只有一格文字：選項＋補充合成一句寫進 `成品檢查.json`（`reason_text`）
# 10-15 要修改合併（1015-4）：「退回」「改」合成一顆「要修改」，「裡面有老師的聲音」變成「哪裡出問題？」的一個選項（排在「其他」前面）
REASON_OPTIONS = ("念錯字", "聲音不像、不自然", "長度或停頓怪", "裡面有老師的聲音", "其他")
REASON_NONE = "退回（沒寫原因）"


def reason_text(options: list[str] | None, extra: str = "") -> str:
    """（純函式）退回原因的選項＋補充 → 舊檔那一格文字：「念錯字、長度或停頓怪：補充」；都沒有＝「退回（沒寫原因）」。"""
    opts = [o for o in REASON_OPTIONS if o in (options or [])]
    extra = str(extra or "").strip()
    head = "、".join(opts)
    return f"{head}：{extra}" if head and extra else head or extra or REASON_NONE


def _reason(texts: list[str], prev: dict | None = None) -> dict | None:
    # 10-15 第5步2.0 第2段：一張卡的每一筆寫同一句原因（卡片退回），合起來只留一次
    t = "；".join(dict.fromkeys(x for x in (str(y).strip() for y in texts) if x))
    # 10-15 第5步2.0 第2段：第 5 步按退回時存的選項＋補充（`set_reason`），合起來跟舊檔這一句一樣就留著（不被打平成一句）
    if prev and prev.get("選項") is not None and reason_text(prev.get("選項"), prev.get("補充", "")) == t:
        return prev
    return {"選項": [], "補充": t} if t or texts else None


def _card_of_redo(st: Store, e: dict, index: dict) -> list[str]:
    """重做中（或重做過、送回）的一項 → 卡號：鍵在哪一版的處理紀錄出現過就照那一版；整片看標的 → 待釐清卡；
    沒登記的變動 → 那張卡；都不是就照 `覆核項目`。"""
    key = e.get("鍵")
    if e.get("來源") == "沒登記的變動" or e.get("類型") == UNLOGGED:
        c = st.by_anchor(f"沒登記:{key}")
        if c is not None:
            return [c["卡號"]]
    for n in sorted(st.versions, reverse=True):
        cs = (st.versions[n].get("紀錄卡號") or {}).get(key)
        if cs:
            return list(cs)
    if e.get("來源") == "整片看":
        c = next((st.cards[k] for k in sorted(st.cards, key=_card_num)
                  if (st.cards[k].get("舊檔鍵") or {}).get("整片退回") == key), None)
        if c is not None:
            return [c["卡號"]]
    return _resolve(e.get("覆核項目") or [], index, st.alive_by_key())


def apply_check(st: Store, v: dict, check: dict, index: dict, now: str, *, src_unclear: str = SRC_STEP5,
                step: int = 5) -> dict:
    """成品檢查（已 refresh 到這一版的處理紀錄）→ 這一版每張卡的狀態、退回原因；卡片的等重做；這一版的輸出紀錄（改 st）。
    規則（一張卡對到好幾筆處理紀錄）：任何一筆退回＝退回；每一筆都通過＝通過；其他＝還沒看。
    沒登記的變動：沒問題＝通過、退回重做＝退回。整片看的退回：對到一張卡＝那張卡退回；對到 0 張或好幾張＝建「待釐清」卡。
    重做中的每一項：那張卡等重做（起因退回）——不管是不是殘留（31 號設計 §4.2 第 7 列、§8）。"""
    from bookclub.finalcheck import PASS, REDO, UNLOGGED_OK

    n = v["版本號"]
    per: dict[str, list[str]] = {}
    for rk, cs in (v.get("紀錄卡號") or {}).items():
        for c in cs:
            per.setdefault(c, []).append(rk)
    new: dict[str, dict] = {}
    for c, rks in per.items():
        res = [(check.get("逐筆") or {}).get(rk) or {} for rk in rks]
        backs = [d for d in res if d.get("結果") == REDO]
        if backs:
            # 10-15 第2段審查修正（必修 1）：共用的那一筆記了各張卡的原因（`各卡原因`，鍵＝第 3 步鍵或卡號）→ 用這張卡自己的；
            # 這張卡沒退回它（連帶）就用合起來那一句
            k3 = (st.cards.get(c) or {}).get("第3步鍵") or c
            new[c] = {"狀態": RETURNED, "原因": [(d.get("各卡原因") or {}).get(k3, d.get("原因", "")) for d in backs]}
        elif rks and all(d.get("結果") == PASS for d in res):
            new[c] = {"狀態": PASSED, "原因": []}
        else:
            new[c] = {"狀態": NOT_SEEN, "原因": [], "沒判斷": not any(d.get("結果") for d in res)}
    for uk, c in (v.get("未登記卡號") or {}).items():
        d = (check.get("未登記確認") or {}).get(uk) or {}
        new[c] = {"狀態": PASSED if d.get("結果") == UNLOGGED_OK else RETURNED if d.get("結果") == REDO else NOT_SEEN,
                  "原因": [d.get("原因", "")] if d.get("結果") == REDO else []}
    by_key = st.alive_by_key()
    for x in check.get("整片退回") or []:
        cs = _resolve(x.get("覆核項目") or [], index, by_key)
        if len(cs) == 1:
            c = cs[0]
        else:
            sec = x.get("原片秒")
            anchor = f"整片退回:{x.get('id')}@{float(sec):.2f}" if sec is not None else f"整片退回:{x.get('id')}"
            card = _extra_card(st, UNCLEAR, anchor, [_r3(sec), _r3(sec)] if sec is not None else None, src_unclear,
                               {"整片退回": x.get("id")}, now, step)
            c = card["卡號"]
            if card["狀態"] == REMOVED:   # 10-15：同一處整片退回取消後又標回來 → 同一張卡恢復有效
                st.log(now, c, step, "恢復有效", REMOVED, ALIVE)
                card.update({"狀態": ALIVE, "更新時間": now})
            if (c, n) not in st.states:
                t = _r3(x.get("成品秒"))
                st.states[(c, n)] = {"卡號": c, "版本號": n, "成品": [t, t] if t is not None else None, "狀態": NOT_SEEN,
                                     "退回原因": None, "沿用自": None, "紀錄鍵": [], "更新時間": now}
        prev = new.get(c) or {"狀態": RETURNED, "原因": []}
        new[c] = {"狀態": RETURNED, "原因": (prev["原因"] if prev["狀態"] == RETURNED else []) + [x.get("原因", "")]}
    prev_v = max([k for k in st.versions if k < n], default=None)
    changed = 0
    for c, s in new.items():
        old_row = st.states.get((c, n)) or {}
        if (s["狀態"] == NOT_SEEN and s.get("沒判斷") and old_row.get("狀態") == PASSED and old_row.get("沿用自")
                and old_row.get("沿用來源") not in (None, prev_v_of(st, n)) and not old_row.get("取消沿用")):
            # 10-15 第5步2.0 第3段：通過是從更早的一版沿用來的（範圍版本：中間那一版沒有這張卡），成品檢查檔早就沒有這一筆
            # （換處理紀錄時 refresh 拿掉了）——不是老師取消了，留著沿用的通過
            continue
        row = st.states.get((c, n)) or {"卡號": c, "版本號": n, "成品": (v.get("成品時間") or {}).get(c), "狀態": NOT_SEEN,
                                        "退回原因": None, "沿用自": None, "紀錄鍵": sorted(per.get(c) or []), "更新時間": now}
        why = _reason(s["原因"], row.get("退回原因")) if s["狀態"] == RETURNED else None
        carry = None
        if s["狀態"] == PASSED and prev_v is not None:
            ps = st.states.get((c, prev_v))
            pv = st.versions[prev_v]
            if ps and ps.get("狀態") == PASSED and (pv.get("卡片指紋") or {}).get(c) == (v.get("卡片指紋") or {}).get(c):
                carry = ps.get("沿用自") or prev_v
        if row.get("狀態") != s["狀態"] or row.get("退回原因") != why:
            act = {PASSED: "通過", RETURNED: "退回", NOT_SEEN: "改回還沒看"}[s["狀態"]]
            st.log(now, c, step, act, row.get("狀態"),
                   {"版本號": n, "原因字數": len((why or {}).get("補充") or "")} if why else {"版本號": n})
            changed += 1
            row.update({"狀態": s["狀態"], "退回原因": why, "沿用自": carry if s["狀態"] == PASSED else None, "更新時間": now})
        elif s["狀態"] == PASSED and row.get("沿用自") != carry and carry is not None:
            row["沿用自"] = carry
        st.states[(c, n)] = row
    # 10-15 第1段審查修正（必修 1）：這一版已經有狀態列、這次卻沒算到的卡也要重算（不能停在上一次算的「退回」）。
    # 只有整片退回會讓這種列出現：整片退回取消了（舊檔裡那一筆不在了）——
    #   待釐清卡：卡片標「刪掉」（舊檔已經沒有這一筆），這一版改回「還沒看」；
    #   整片退回對到的一般卡（這一版的處理紀錄沒有它）：拿掉這一列（＝回到「還沒生成」）。
    for (c, m) in sorted((k for k in st.states if k[1] == n and k[0] not in new), key=lambda k: _card_num(k[0])):
        row = st.states[(c, m)]
        card = st.cards.get(c) or {}
        if card.get("類型") == UNCLEAR and (card.get("舊檔鍵") or {}).get("整片退回") is not None:
            if row.get("狀態") != NOT_SEEN or row.get("退回原因"):
                st.log(now, c, step, "改回還沒看", row.get("狀態"), {"版本號": n})
                row.update({"狀態": NOT_SEEN, "退回原因": None, "沿用自": None, "更新時間": now})
                changed += 1
            if card.get("狀態") == ALIVE:
                st.log(now, c, step, "刪掉", ALIVE, {"狀態": REMOVED, "指向": []})
                card.update({"狀態": REMOVED, "指向": [], "更新時間": now})
        elif not row.get("紀錄鍵"):
            st.log(now, c, step, "改回還沒生成", row.get("狀態"), {"版本號": n})
            del st.states[(c, m)]
            changed += 1
    # 等重做：這一版退回的＋重做中的每一項
    want: dict[str, dict] = {c: {"起因": WHY_RETURNED} for c, s in new.items() if s["狀態"] == RETURNED}
    doing = check.get("重做中") or {}
    for e in doing.get("項目") or []:
        for c in _card_of_redo(st, e, index):
            want.setdefault(c, {"起因": WHY_RETURNED})
    for k in sorted(st.cards, key=_card_num):
        c = st.cards[k]
        cur = c.get("等重做")
        if k in want and not cur:
            c["等重做"] = {"起因": want[k]["起因"], "時間": doing.get("時間") or now}
            c["更新時間"] = now
            st.log(now, k, step, "等重做", None, {"起因": want[k]["起因"]})
        elif (k not in want and cur and cur.get("起因") == WHY_RETURNED and not cur.get("版本號")
              and ((k, n) in st.states or _in_range(c, v))):
            # 10-15 第5步2.0 第3段：這一版沒處理到（範圍外）的卡不清——退回的是別的範圍那一版，這一版的成品檢查檔看不到它；
            # 在舊版上退回的（帶 `版本號`）由那一版的判斷或之後組裝的新版本清（`decide_version`、`build_version`）
            c["等重做"] = None
            c["更新時間"] = now
            redone = str(v.get("時間") or "") > str(cur.get("時間") or "")   # 這一版比退回的時候新＝重做進這一版
            st.log(now, k, step, f"重做進第{n}版" if redone else "取消等重做", cur, None)
    # 這一版的輸出紀錄
    outs = []
    for rec in check.get("輸出紀錄") or []:
        if rec.get("處理紀錄產生時間") == v.get("時間") or (not rec.get("處理紀錄產生時間") and rec.get("來源") in (v.get("成品檔") or [])):
            outs.append({k: rec.get(k) for k in EXPORT_KEYS})
    # 10-15 第5步2.0 第3段：從這一版輸出、卻不在成品檢查檔裡的（`add_export` 直接記的）留著
    keep = [x for x in v.get("輸出紀錄") or [] if not any(o.get("檔案") == x.get("檔案") for o in outs)]
    if keep + outs != (v.get("輸出紀錄") or []):
        v["輸出紀錄"] = keep + outs
    return {"狀態有變": changed, "等重做": sorted(want, key=_card_num)}


def prev_v_of(st: Store, n: int) -> int | None:
    """第 n 版的上一版（版本號比它小、最大的那一個）。"""
    return max([k for k in st.versions if k < n], default=None)


def set_reason(workdir: str | Path, card: str, options: list[str] | None, extra: str = "") -> bool:
    """10-15 第5步2.0 第2段：第 5 步按卡片「退回」時選的原因（選項＋補充）記到最新一版這張卡的 `退回原因`。
    舊檔照樣寫合成的一句（雙寫：先寫舊檔、`mirror_final` 重算狀態，再呼叫這裡補上結構）；之後重算時合起來一樣就留著。
    這一版這張卡不是退回（例如舊檔寫失敗）就不寫。回傳有沒有寫。"""
    w = Path(workdir)
    if not exists(w):
        return False
    opts = [o for o in REASON_OPTIONS if o in (options or [])]
    with _locked(w):
        st = load(w)
        v = st.latest_version()
        row = st.states.get((card, v["版本號"])) if v else None
        if not row or row.get("狀態") != RETURNED:
            return False
        want = {"選項": opts, "補充": str(extra or "").strip()}
        if row.get("退回原因") == want:
            return False
        row["退回原因"] = want
        row["更新時間"] = _now()
        save(w, st)
    return True


def mark_outside_redo(workdir: str | Path, card: str, options: list[str] | None, extra: str = "") -> bool:
    """10-15 宇軒驗證回饋四：「已通過（沿用上一版）」（範圍外、這一版沒處理到）的卡在最新版按「要修改」記的原因。
    這一版沒有它的狀態列（`set_reason` 記不了），記在卡片的 `等重做`：{起因: 改了, 時間, 退回原因: {選項, 補充}}——
    畫面變「要重做」（已改，等重做）、算進「重做這幾張」；下次組裝 `build_version` 清掉，而且新版不沿用之前的通過
    （指紋沒變也要重新看）。已經等重做（例如先在選單裡改了字）就補上原因。回傳有沒有寫。"""
    w = Path(workdir)
    if not exists(w):
        return False
    why = {"選項": [o for o in REASON_OPTIONS if o in (options or [])], "補充": str(extra or "").strip()}
    with _locked(w):
        st = load(w)
        c = st.cards.get(card)
        if c is None:
            raise KeyError(f"沒有這張卡：{card}")
        cur = c.get("等重做") or {}
        if cur.get("起因") == WHY_EDITED and cur.get("退回原因") == why:
            return False
        now = _now()
        new = {"起因": WHY_EDITED, "時間": now, "退回原因": why}
        c["等重做"] = new
        c["更新時間"] = now
        # 歷史不放退回原因文字（工作區格式：歷史只放編號、狀態、欄位名）
        st.log(now, card, 5, "等重做", {"起因": cur["起因"]} if cur.get("起因") else None, {"起因": WHY_EDITED, "範圍外": True})
        save(w, st)
    return True


def mark_edited(workdir: str | Path, key: str, *, new: bool = False, page: dict | None = None,
                snap: dict | None = None) -> str | None:
    """10-15 第5步2.0 第2段：第 5 步按「改」改了第 3 步的東西（改字、改範圍、這是老師聲音、刪掉、新增）之後：
    先把第 3 步的檔同步成卡片，再把那張卡標「等重做（起因：改了）」——畫面上是「已改，等重做」，組裝做進新的一版時清掉
    （`build_version`）。new：這張是在第 5 步新增的（來源改「第5步新增」）。key：第 3 步鍵（`學員段落:T003`）。
    找不到卡（例如刪掉的人工新增，第 3 步那一筆已經沒有了）回 None。沒升級過的工作區不做。"""
    w = Path(workdir)
    if not exists(w):
        return None
    sync_cards(w, page, snap=snap)   # 10-15 第2段審查修正（建議，效能）：page／snap 伺服器算好了就用，不另外算
    with _locked(w):
        st = load(w)
        c = st.alive_by_key().get(key)
        if c is None:
            c = next((st.cards[k] for k in sorted(st.cards, key=_card_num, reverse=True) if st.cards[k].get("第3步鍵") == key), None)
        if c is None:
            return None
        now = _now()
        changed = False
        if new and c.get("來源") in (SRC_STEP3, SRC_AUTO):
            st.log(now, c["卡號"], 5, "改來源", c["來源"], SRC_STEP5)
            c["來源"] = SRC_STEP5
            changed = True
        cur = c.get("等重做")
        if not cur:
            c["等重做"] = {"起因": WHY_EDITED, "時間": now}
            st.log(now, c["卡號"], 5, "等重做", None, {"起因": WHY_EDITED})
            changed = True
        if changed:
            c["更新時間"] = now
            save(w, st)
        return c["卡號"]


def mirror_final(workdir: str | Path, check: dict | None = None, log: dict | None = None, *, force: bool = False) -> dict | None:
    """第 5 步成品檢查存檔之後（`finalcheck._save`）：照舊格式寫完，這裡算出每張卡在那一版的狀態（雙寫）。
    判斷相關的欄位沒變（例如只是記看過區段）就什麼都不做。處理紀錄是舊版工具組的（版本表裡沒有）→ 先補一個版本。
    沒升級過的工作區不做。"""
    from bookclub import finalcheck, proclog

    w = Path(workdir)
    if not exists(w):
        return None
    if check is None:
        check = finalcheck.load_check(w)
    fp = _check_print(check)
    if not force and (_fmt(w).get("同步") or {}).get("成品檢查") == fp:   # 判斷沒變（例如只記了看過區段）
        return None
    log = proclog.load(w) if log is None else log
    if not log or audio_only(log):   # 10-15：render audio 的處理紀錄不建版本，跟沒有處理紀錄一樣
        with _locked(w):
            st = load(w)
            st.fmt.setdefault("同步", {})["成品檢查"] = fp
            _save_fmt(w, st)
        return None
    st = load(w)
    if not st.version_at(log.get("產生時間")):
        add_version(w, log, note=NOTE_OLD_TOOL)
    if check.get("處理紀錄產生時間") != log.get("產生時間"):
        check = finalcheck.refresh(check, log)
    index = _index(w)
    with _locked(w):
        st = load(w)
        v = st.version_at(log.get("產生時間"))
        if v is None:
            return None
        res = apply_check(st, v, check, index, _now())
        st.fmt.setdefault("同步", {})["成品檢查"] = fp
        save(w, st)
    return res


# ---------- 舊工作區升級（§4） ----------

def _small_files(workdir: Path) -> list[Path]:
    """升級前要備份的小檔（不含 wav、mp4）。"""
    from bookclub import finalcheck, tts

    w = Path(workdir)
    out: list[Path] = []
    rv = w / "覆核"
    if rv.is_dir():
        out += sorted(p for p in rv.rglob("*.json") if p.is_file())
    for rel in ("校對/段落.json", "名字覆核決定.json", "學員名字覆核決定.json"):
        if (w / rel).is_file():
            out.append(w / rel)
    gen = w / "生成"
    if gen.is_dir():
        out += sorted(p for p in gen.glob("*.json") if p.is_file())
    for role in ROLE_KEYS:
        try:
            p = finalcheck._role_paths(w, role)[1] / tts.REDO_VERSIONS
        except Exception:  # noqa: BLE001
            continue
        if p.is_file():
            out.append(p)
    return out


def _backup(workdir: Path, stamp: str, *, base: Path | None = None) -> tuple[str, int]:
    """小檔備份到 base（升級時是 `修改需求.升級中/`）底下；回傳改名成 `修改需求/` 之後的相對路徑。"""
    name = f"{BACKUP_PREFIX}{stamp}"
    dst = (Path(base) if base is not None else needs_dir(workdir)) / name
    n = 0
    for p in _small_files(workdir):
        rel = p.relative_to(workdir)
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst / rel)
        n += 1
    return f"{DIR_NAME}/{name}", n


def _history_from_old(st: Store, check: dict, index: dict, now: str) -> int:
    """升級：成品檢查的 `重做過`、`送回AI重做` 寫進歷史（不改狀態）。"""
    n = 0
    for part, act in (("重做過", "重做過（升級時記）"), ("送回AI重做", "送回AI重做（升級時記）")):
        sec = check.get(part) or {}
        for e in sec.get("項目") or []:
            for c in _card_of_redo(st, e, index):
                st.log(now, c, 5, act, None, {"時間": sec.get("時間"), "第幾版": e.get("第幾版"), "做法": e.get("做法"),
                                              "原因字數": len(str(e.get("原因") or ""))})
                n += 1
    return n


def _current_product(workdir: Path, log: dict, check: dict) -> list[str]:
    from bookclub import finalcheck

    try:
        latest = finalcheck.log_products(workdir, log) or []
    except Exception:  # noqa: BLE001
        latest = []
    cur = check.get("成品影片")
    out = list(latest)
    if cur and cur not in out and (workdir / cur).is_file() and finalcheck.MARK_LABEL not in Path(cur).stem:
        marks = finalcheck.log_marks(workdir, log)
        if marks.get(cur) != finalcheck.MARK_OLD:
            out.insert(0, cur)
    return out


def _renumbered(workdir: Path, log: dict | None) -> int:
    """10-15 第1段審查修正（必修 3）：學員生成句改成在整個段落裡數之後，照上一次組裝的範圍會換號的格數
    （範圍切到段落的那幾格；下次第 4 步那幾格會重新生成一次）。整支範圍、沒組裝過、算不出來＝0。"""
    from bookclub import students

    rng = (log or {}).get("範圍")
    if not rng or rng[0] is None or rng[1] is None:
        return 0
    try:
        with wd.no_writes():
            return students.renumbered_slots(workdir, float(rng[0]), float(rng[1]))
    except Exception:  # noqa: BLE001 — 算不出來不擋升級
        return 0


def plan_upgrade(workdir: str | Path, page: dict | None = None, snap: dict | None = None) -> tuple[Store, dict]:
    """升級要寫的全部東西（只讀舊檔，不寫任何檔）：回傳（記憶體裡的紀錄, 報告）。
    snap：算 page 之前拍的第 3 步快照（page 是呼叫的地方算好的才給；見 `sync_cards`）。"""
    from bookclub import __version__, finalcheck, proclog

    w = Path(workdir)
    now = _now()
    if snap is None or page is None:
        snap = step3_snapshot(w)
    st = Store()
    st.fmt = {"格式版本": FORMAT_VERSION, "升級自程式版本": __version__, "升級時間": now, "下一個卡號": 1, "下一個歷史序號": 1}
    page = compute_page(w) if page is None else page
    sents = _turn_sentences(w)
    # 1. 第 3 步的卡片（還原的 → 刪掉）
    _apply_sync(st, page.get("項目") or [], page.get("已還原") or [], _deleted_keys(page), sents, now, step=3)
    for c in st.cards.values():   # 升級時建的卡：照第 3 步的「人工新增」分來源，不記成第 3 步剛新增的
        c["建立時間"] = now
    index = _index(w)
    # 2. 生成句表：現在的計畫＋生成紀錄裡另外有的
    try:
        ctx = plan_ctx(w)
    except Exception:  # noqa: BLE001
        ctx = {}
    g1 = _apply_gen_plan(st, ctx, index, now)
    g2 = _apply_old_gen(st, w, index, now)
    _update_gen_rounds(st, w, now)
    # 3–9. 處理紀錄＋成品檢查 → 版本 1、成品狀態、等重做、歷史、輸出紀錄
    log = proclog.load(w)
    renum = _renumbered(w, log)
    if audio_only(log):   # 10-15：render audio 的處理紀錄不建版本
        log = None
    raw = finalcheck.load_check(w)
    vrow, other = None, []
    redo_hist = 0
    if log:
        check = finalcheck.refresh(raw, log)
        prods = _current_product(w, log, check)
        vrow = build_version(st, w, log, index, now, note=NOTE_UPGRADE, products=prods, src=SRC_UPGRADE, step=4)
        apply_check(st, vrow, check, index, now, src_unclear=SRC_UPGRADE, step=5)
        redo_hist = _history_from_old(st, check, index, now)
        other = [p for p in finalcheck.products(w) if p not in (vrow.get("成品檔") or [])]
        st.fmt.setdefault("同步", {})["處理紀錄"] = log.get("產生時間")
        st.fmt["同步"]["成品檢查"] = _check_print(raw)
    st.fmt.setdefault("同步", {})["第3步"] = snap
    rep = _report(st, vrow, {"對上": g1["對上"] + g2["對上"], "對不到": g1["對不到"] + g2["對不到"]}, other, redo_hist, log)
    rep["學員換號格數"] = renum
    return st, rep


def _count(rows, key) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
    return out


def _report(st: Store, v: dict | None, gen: dict, other: list[str], redo_hist: int, log: dict | None) -> dict:
    cards = [st.cards[k] for k in sorted(st.cards, key=_card_num)]
    unclear = [c for c in cards if c["類型"] == UNCLEAR]
    states = [r for (c, n), r in st.states.items() if v and n == v["版本號"]]
    alive = [c for c in cards if c["狀態"] == ALIVE]
    in_v = {r["卡號"] for r in states}
    not_made = [c["卡號"] for c in alive if v and c["卡號"] not in in_v]
    return {
        "升級時間": st.fmt.get("升級時間"), "升級自程式版本": st.fmt.get("升級自程式版本"),
        "卡片": {"總數": len(cards), "類型": _count(cards, "類型"), "狀態": _count(cards, "狀態"), "來源": _count(cards, "來源")},
        "生成句": {"對上": gen["對上"], "對不到": len(gen["對不到"]), "對不到的句子": gen["對不到"][:50]},
        "版本": None if not v else {"版本號": v["版本號"], "時間": v["時間"], "範圍": v["範圍"], "標記": v["標記"],
                                    "成品檔": v["成品檔"], "卡片數": len(v["卡片指紋"]),
                                    "沒對到卡片的紀錄": len(v.get("沒對到卡片的紀錄") or [])},
        "沒有處理紀錄": log is None,
        "成品狀態": _count(states, "狀態") | ({NOT_MADE: len(not_made)} if v else {}),
        "等重做": sorted((c["卡號"] for c in cards if c.get("等重做")), key=_card_num),
        "對不到": [{"卡號": c["卡號"], "來源": c["來源"], "舊檔鍵": c.get("舊檔鍵"), "原片": c.get("原片")} for c in unclear],
        "重做過送回寫進歷史": redo_hist,
        "舊成品沒建版本": len(other),
    }


def _fmt_t(x) -> str:
    from bookclub import timemap

    return "—" if x is None else timemap.t1(float(x))


def report_lines(rep: dict, *, dry: bool = False) -> list[str]:
    """升級報告給人看的幾行（只有數字、編號、狀態）。"""
    out = ["修改需求紀錄升級" + ("（試跑，不寫任何檔）" if dry else "")]
    c = rep["卡片"]
    out.append(f"卡片 {c['總數']} 張：" + ("、".join(f"{k} {n}" for k, n in c["類型"].items()) or "（沒有）"))
    out.append("  狀態：" + ("、".join(f"{k} {n}" for k, n in c["狀態"].items()) or "—")
               + "；來源：" + ("、".join(f"{k} {n}" for k, n in c["來源"].items()) or "—"))
    g = rep["生成句"]
    out.append(f"生成句 {g['對上']} 句對到卡片；對不到 {g['對不到']} 句" + (
        "（" + "、".join(x for x in g["對不到的句子"][:10]) + ("⋯" if g["對不到"] > 10 else "") + "）" if g["對不到"] else ""))
    v = rep.get("版本")
    if v:
        rng = v.get("範圍")
        out.append(f"版本 {v['版本號']}（升級前的成品）：範圍 {_fmt_t(rng[0]) if rng else '整支'}–{_fmt_t(rng[1]) if rng else ''}"
                   f"，標記 {v.get('標記') or '—'}，成品 {len(v['成品檔'])} 支，{v['卡片數']} 張卡在這一版裡")
        out.append("成品狀態：" + ("、".join(f"{k} {n}" for k, n in rep["成品狀態"].items()) or "—"))
    else:
        out.append("還沒有處理紀錄（第 4 步還沒組裝過）：沒有建版本")
    out.append(f"等重做 {len(rep['等重做'])} 張" + ("：" + "、".join(rep["等重做"][:20]) if rep["等重做"] else ""))
    if rep["對不到"]:
        out.append(f"對不到、標成「待釐清」{len(rep['對不到'])} 張：")
        for x in rep["對不到"][:30]:
            key = x.get("舊檔鍵") or {}
            what = "、".join(f"{k} {v}" for k, v in key.items())
            out.append(f"  {x['卡號']}（{x['來源']}）原片 {_fmt_t((x.get('原片') or [None])[0])}：{what}")
    else:
        out.append("對不到的：沒有")
    if rep.get("重做過送回寫進歷史"):
        out.append(f"以前的「重做過」「送回 AI 重做」寫進歷史 {rep['重做過送回寫進歷史']} 條")
    if rep.get("舊成品沒建版本"):
        out.append(f"輸出/ 裡另外 {rep['舊成品沒建版本']} 支舊成品沒有處理紀錄，不建版本（檔案照舊留著）")
    n = int(rep.get("學員換號格數") or 0)   # 10-15 第1段審查修正（必修 3）
    out.append(f"會換號的學員句：{n} 格" + ("（上次組裝的範圍切到段落；學員生成句改成在整個段落裡數，"
                                          "下次第 4 步這幾格會重新生成一次）" if n else "（沒有：上次是整支、或沒切到段落）"))
    return out


def _report_md(rep: dict, backup: tuple[str, int] | None) -> str:
    lines = ["# 修改需求紀錄升級報告", "",
             f"升級時間：{rep['升級時間']}（程式版本 {rep['升級自程式版本']}）", "",
             "舊檔一個都沒改、沒刪；這份報告只有數字、編號、狀態，不含逐字稿與退回原因。", ""]
    lines += [f"- {x}" for x in report_lines(rep)[1:]]
    lines += ["", "做過部分範圍的工作區，切到段落的那幾格下次會重新生成一次（學員生成句的編號改成在整個段落裡數；"
                  "聲音會換、第 5 步那幾張要重看）。"]
    if backup:
        lines += ["", f"升級前的小檔備份在 `{backup[0]}/`（{backup[1]} 個檔）。",
                  "要重新升級：把 `修改需求/` 整個改名（例如 `修改需求_停用`），下次打開工作區就會重跑；不用刪。"]
    return "\n".join(lines) + "\n"


class Busy(Exception):
    """別的程式（或伺服器另一個請求）正在升級這個工作區。"""


@contextmanager
def _stage_lock(stage: Path, *, wait: bool = True):
    """10-15 第1段審查修正（建議）：第一次升級的檔案鎖——先建 `修改需求.升級中/` 再鎖它裡面的 `.寫入鎖`
    （以前 `修改需求/` 還不在就沒上鎖，伺服器跟 `bookclub upgrade` 同時跑時沒有互斥）。wait=False 時拿不到就丟 Busy。
    改名成 `修改需求/` 之後鎖檔跟著過去，就是平常 `_locked` 用的那一個。"""
    stage.mkdir(parents=True, exist_ok=True)
    fh = None
    try:
        import fcntl

        try:
            fh = open(stage / LOCK_FILE, "a+")
        except FileNotFoundError:   # 10-15 第1段審查二修正：別人剛把 `.升級中` 改名走（升級做完了）→ 當作正在忙，不記同步錯誤
            raise Busy() from None
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            fh.close()
            raise Busy() from None
    except ImportError:
        fh = None
    try:
        yield
    finally:
        if fh is not None:
            try:
                import fcntl

                fcntl.flock(fh, fcntl.LOCK_UN)
            finally:
                fh.close()


def _clear_stage(stage: Path) -> None:
    """清掉上次升級失敗留在 `修改需求.升級中/` 的半套（鎖檔留著）。這個資料夾只有升級自己會寫。"""
    for p in stage.iterdir():
        if p.name == LOCK_FILE:
            continue
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink()


def _drop_stage(stage: Path) -> None:
    """已經升級好了（別的程式剛做完）：剛剛為了上鎖建的空 `修改需求.升級中/` 收掉（裡面只有鎖檔才收）。"""
    try:
        left = [p for p in stage.iterdir() if p.name != LOCK_FILE]
        if not left:
            (stage / LOCK_FILE).unlink(missing_ok=True)
            stage.rmdir()
    except OSError:
        pass


def upgrade(workdir: str | Path, *, dry_run: bool = False, page: dict | None = None, force: bool = False,
            snap: dict | None = None, wait: bool = True) -> dict:
    """舊工作區升級（31 號設計 §4）：只讀舊檔、只寫 `修改需求/`；小檔先備份；只跑一次（`格式.json` 在就不跑）。
    dry_run：只算、印數字，不寫任何檔（也不建資料夾）。回傳報告（`升級了`＝這次有沒有真的寫）。
    10-15 第1段審查修正（必修 2）：先全部寫到 `修改需求.升級中/`，寫完才一次改名成 `修改需求/`；中途失敗留在 `.升級中`
    （`修改需求/` 不會出現半套、歷史序號不會重複），下次重跑先清掉它。wait=False：別的程式正在升級就丟 Busy、不等。"""
    w = Path(workdir)
    if exists(w) and not force:
        return {"升級了": False, "原因": "已經升級過了"}
    if dry_run:
        st, rep = plan_upgrade(w, page, snap)
        return {**rep, "升級了": False, "試跑": True}
    stage = w / STAGING
    with _stage_lock(stage, wait=wait):
        if exists(w) and not force:
            _drop_stage(stage)
            return {"升級了": False, "原因": "已經升級過了"}
        _clear_stage(stage)
        st, rep = plan_upgrade(w, page, snap)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bk = _backup(w, stamp, base=stage)
        st.fmt["升級前備份"] = bk[0]
        v = st.latest_version()
        if v is not None:
            log = wd.read_json(w / "生成" / "處理紀錄.json", default=None)
            if log:
                _write_version_copy(w, v["版本號"], log, base=stage)
        _write(stage / REPORT_JSON, rep)
        (stage / REPORT_MD).write_text(_report_md(rep, bk), encoding="utf-8")
        save(w, st, base=stage)
        final = needs_dir(w)
        if final.exists():   # 沒有 格式.json 的 修改需求/（舊版程式升級失敗留下的）或 force：搬開、不刪
            final.rename(w / f"{DIR_NAME}_升級前_{stamp}")
        os.rename(stage, final)   # 一次換上：從這一刻起 exists() 才是 True
    return {**rep, "升級了": True, "備份": bk[0], "備份檔數": bk[1]}


# ---------- 打開工作區時（伺服器 GET /api/review、/api/final、/api/execute） ----------

def catch_up(workdir: str | Path) -> None:
    """已升級的工作區打開時，補上舊檔比紀錄新的部分（31 號設計 §4.4：回滾後又用新版打開、或舊程式改過）：
    第 3 步的檔變了 → 同步卡片；處理紀錄換新、版本表裡沒有 → 建一個新版本（標「舊版工具組的」）；
    成品檢查的判斷變了 → 重算卡片狀態。"""
    from bookclub import proclog

    w = Path(workdir)
    if not exists(w):
        return
    sync_cards(w)
    stats = _file_stats(w)   # 第 4 步頁面會一直問狀態：處理紀錄、成品檢查兩個檔沒動過就不用讀
    if (_fmt(w).get("同步") or {}).get("檔案") == stats:
        return
    log = proclog.load(w)
    if log and not audio_only(log):
        st = load(w)
        if st.version_at(log.get("產生時間")) is None:
            add_version(w, log, note=NOTE_OLD_TOOL)
    mirror_final(w)
    with _locked(w):   # 10-15 第1段審查修正（建議）：這裡只記檔案指紋，只寫 格式.json（以前整份表重寫）
        st = load(w)
        st.fmt.setdefault("同步", {})["檔案"] = stats
        _save_fmt(w, st)


def _file_stats(workdir: Path) -> dict:
    out = {}
    for rel in ("生成/處理紀錄.json", "覆核/成品檢查.json"):
        p = Path(workdir) / rel
        try:
            s = p.stat()
            out[rel] = [s.st_size, s.st_mtime_ns]
        except OSError:
            out[rel] = None
    return out


_busy: set[str] = set()
_busy_lock = threading.Lock()
_failed_at: dict[str, float] = {}


def ensure(workdir: str | Path, *, running: bool = False, page: dict | None = None, snap: dict | None = None,
           log=print) -> dict | None:
    """打開工作區時呼叫：沒升級過就升級（第 4 步執行中先不升級，等跑完）；升級過就補差異。
    任何錯誤都只印提醒，不擋畫面（第 1 段讀的那一邊還是舊檔）。
    10-15 第1段審查修正（建議）：這是在 GET 請求裡同步跑的——
    - 非阻塞：同一個工作區已經有一個請求在升級／補差異，這個請求直接跳過（不等、不重算一次）；別的程式（`bookclub upgrade`）
      正在升級也不等；
    - 失敗冷卻：失敗後 `RETRY_AFTER_S` 秒內不重試（以前升級一直失敗，每次打開第 3／4／5 步都整套重跑），失敗記進 `格式.json`。"""
    w = Path(workdir)
    key = str(w)
    if time.monotonic() - _failed_at.get(key, -1e18) < RETRY_AFTER_S:
        return None
    with _busy_lock:
        if key in _busy:
            return None
        _busy.add(key)
    try:
        if not exists(w):
            if running:
                return None
            try:
                rep = upgrade(w, page=page, snap=snap, wait=False)
            except Busy:
                return None
            if rep.get("升級了"):
                log(f"[修改需求紀錄] 第一次用新版打開：已升級（卡片 {rep['卡片']['總數']} 張；舊檔沒動），"
                    f"報告在 {DIR_NAME}/{REPORT_MD}")
            return rep
        if page is not None:
            sync_cards(w, page, snap=snap)
        if not running:
            catch_up(w)
            resync(w)   # 10-15 第1段審查二修正：有同步錯誤就整份重新同步一次，成功清成 0 次
        return None
    except Exception as e:  # noqa: BLE001 — 新紀錄出錯不能擋住老師用舊功能
        _failed_at[key] = time.monotonic()
        note_error(w, "打開工作區", e)
        log(f"[修改需求紀錄] ⚠️ 沒更新到（不影響畫面；{int(RETRY_AFTER_S // 60)} 分鐘內不再重試）：{type(e).__name__}")
        return None
    finally:
        with _busy_lock:
            _busy.discard(key)


# ---------- `bookclub upgrade` ----------

def main(argv: list[str]) -> int:
    import argparse
    import io
    import sys
    from contextlib import redirect_stdout

    p = argparse.ArgumentParser(prog="bookclub upgrade",
                                description="舊工作區升級成「修改需求紀錄」（只讀舊檔、只寫 修改需求/；只印數字與編號）")
    p.add_argument("workdir", help="工作區路徑")
    p.add_argument("--dry-run", action="store_true", help="試跑：只印會建幾張卡、哪些對不到，不寫任何檔")
    args = p.parse_args(argv)
    w = Path(args.workdir).expanduser()
    if not w.is_dir():
        print("找不到工作區資料夾")
        return 2
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):   # 呼叫到的函式自己印的東西（可能含文字）不放出去
            rep = upgrade(w, dry_run=args.dry_run)
    except Exception as e:  # noqa: BLE001 — 錯誤訊息可能帶到資料內容，只印錯誤種類
        print(f"升級失敗：{type(e).__name__}（錯誤訊息不印，可能含資料內容）；舊檔沒動"
              + ("" if args.dry_run else f"，寫到一半的留在 {STAGING}/，下次升級會先清掉"))
        return 1
    if not rep.get("升級了") and not rep.get("試跑"):
        print(f"這個工作區{rep.get('原因', '不用升級')}（{DIR_NAME}/{FORMAT_FILE} 已經在）。"
              f"要重新升級：把 {DIR_NAME}/ 改名後再跑一次")
        return 0
    sys.stdout.write("\n".join(report_lines(rep, dry=bool(rep.get("試跑")))) + "\n")
    if rep.get("升級了"):
        print(f"已寫入 {DIR_NAME}/（升級報告：{DIR_NAME}/{REPORT_MD}；小檔備份：{rep['備份']}/，{rep['備份檔數']} 個檔）")
    return 0
