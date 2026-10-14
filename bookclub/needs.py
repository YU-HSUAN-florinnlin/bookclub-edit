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

隱私：`卡片.json` 的 `第3步決定` 是第 3 步檔案的同步副本（含要念的文字），跟其他工作區檔案一樣只留在本機；
`歷史.jsonl`、升級報告只放編號、數字、狀態與指紋，不放學員分享原文與退回原因文字。
AI 助手查這份紀錄一律用 `bookclub inspect <工作區> 修改需求`。
"""

from __future__ import annotations

import json
import os
import shutil
import threading
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

                fh = open(d / ".寫入鎖", "a+")
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


def load(workdir: str | Path) -> Store:
    """讀整份紀錄（沒有的表當空的）。"""
    w = Path(workdir)
    st = Store()
    st.fmt = wd.read_json(_p(w, FORMAT_FILE), default=None) or {}
    st.cards = {r["卡號"]: r for r in _read_table(w, T_CARDS)}
    st.gen = {(r["句id"], r["角色"]): r for r in _read_table(w, T_GEN)}
    st.versions = {int(r["版本號"]): r for r in _read_table(w, T_VERSIONS)}
    st.states = {(r["卡號"], int(r["版本號"])): r for r in _read_table(w, T_STATES)}
    return st


def _table_data(table: str, rows: list[dict]) -> dict:
    return {"表": table, "格式版本": FORMAT_VERSION, "主鍵": list(TABLE_KEYS[table]), "列": rows}


def save(workdir: str | Path, st: Store) -> None:
    """整份寫回（每張表一個檔；歷史只往下加）。"""
    w = Path(workdir)
    needs_dir(w).mkdir(parents=True, exist_ok=True)
    _write(table_path(w, T_CARDS), _table_data(T_CARDS, [st.cards[k] for k in sorted(st.cards, key=_card_num)]))
    _write(table_path(w, T_GEN), _table_data(T_GEN, [st.gen[k] for k in sorted(st.gen)]))
    _write(table_path(w, T_VERSIONS), _table_data(T_VERSIONS, [st.versions[k] for k in sorted(st.versions)]))
    _write(table_path(w, T_STATES), _table_data(T_STATES, [st.states[k] for k in sorted(st.states, key=lambda x: (_card_num(x[0]), x[1]))]))
    if st.history:
        with open(_p(w, HISTORY_FILE), "a", encoding="utf-8") as f:
            for h in st.history:
                f.write(json.dumps(h, ensure_ascii=False) + "\n")
        st.history = []
    st.fmt.setdefault("格式版本", FORMAT_VERSION)
    _write(_p(w, FORMAT_FILE), st.fmt)   # 格式.json 最後寫：中途失敗時下次打開會重新升級，不會只寫一半就當作做完


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


def sync_cards(workdir: str | Path, page: dict | None = None, *, force: bool = False) -> dict | None:
    """第 3 步寫入之後，把卡片表跟第 3 步的檔對齊（同步副本＋歷史）。page 給了就用（`GET /api/review` 已經算好的），
    沒給才自己算（不寫舊檔）。第 3 步的檔沒變、又沒給 page 時什麼都不做。沒升級過的工作區不做。回傳統計或 None。"""
    w = Path(workdir)
    if not exists(w):
        return None
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
            _write(_p(w, FORMAT_FILE), st.fmt)
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
    return {c: _fp([sorted(ps), (st.cards.get(c) or {}).get("第3步決定指紋")]) for c, ps in per.items()}


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


def build_version(st: Store, workdir: Path, log: dict, index: dict, now: str, *, note: str | None = None,
                  products: list[str] | None = None, src: str = SRC_ASSEMBLE, step: int = 4) -> dict:
    """處理紀錄 → 版本表一列（改 st，不寫檔；處理紀錄副本由呼叫的地方寫）。同一份處理紀錄（產生時間一樣）只建一次。"""
    have = st.version_at(log.get("產生時間"))
    if have is not None:
        return have
    prev = st.latest_version()
    n = (max(st.versions) + 1) if st.versions else 1
    m = map_records(st, log, index, now, src=src, step=step)
    prints = _card_prints(st, log, m["紀錄卡號"])
    old = (prev or {}).get("卡片指紋") or {}
    done = sorted((c for c in prints if old.get(c) != prints[c]), key=_card_num)
    methods = sorted({Path(p).stem.rsplit("_", 1)[-1] for p in products or []})
    row = {"版本號": n, "時間": log.get("產生時間"), "處理紀錄產生時間": log.get("產生時間"), "來源": log.get("來源"),
           "範圍": log.get("範圍"), "標記": _log_tag(log), "輸出做法": methods,
           "以哪一版為基礎": prev["版本號"] if prev else None,
           "處理紀錄": f"{DIR_NAME}/{_version_dir(n)}/處理紀錄.json", "成品檔": list(products or []),
           "卡片指紋": prints, "這一版做了哪幾張": done, "影片還在": True, "輸出紀錄": [], "備註": note,
           "紀錄卡號": m["紀錄卡號"], "未登記卡號": m["未登記卡號"], "沒對到卡片的紀錄": m["沒對到卡片的紀錄"],
           "成品時間": _card_spans(log, m["紀錄卡號"]), "建立時間": now}
    st.versions[n] = row
    for c in done:
        st.log(now, c, step, f"組裝進第{n}版", None, {"版本號": n, "指紋": prints[c]})
    # 新版每張卡的起始狀態：指紋沒變、上一版通過 → 通過（沿用）；其他 → 還沒看（第 5 步的判斷由 mirror_final 接著算）
    for c in prints:
        ps = st.states.get((c, prev["版本號"])) if prev else None
        carry = bool(ps and ps.get("狀態") == PASSED and old.get(c) == prints[c])
        st.states[(c, n)] = {"卡號": c, "版本號": n, "成品": row["成品時間"].get(c), "狀態": PASSED if carry else NOT_SEEN,
                             "退回原因": None, "沿用自": (ps.get("沿用自") or prev["版本號"]) if carry else None,
                             "紀錄鍵": sorted(k for k, cs in m["紀錄卡號"].items() if c in cs), "更新時間": now}
    for uk, c in m["未登記卡號"].items():
        st.states.setdefault((c, n), {"卡號": c, "版本號": n, "成品": None, "狀態": NOT_SEEN, "退回原因": None,
                                      "沿用自": None, "紀錄鍵": [f"{UNLOGGED}@{uk}"], "更新時間": now})
    return row


def _write_version_copy(workdir: Path, n: int, log: dict) -> None:
    _write(needs_dir(workdir) / _version_dir(n) / "處理紀錄.json", log)


def add_version(workdir: str | Path, log: dict, *, note: str | None = None) -> dict | None:
    """第 4 步組裝完、換上成品、寫處理紀錄的那一刻（`proclog._write`）：建一個版本（含範圍、處理紀錄副本、
    每張卡的指紋與起始狀態）。沒升級過的工作區不建（之後升級時把最新的處理紀錄當版本 1）。回傳版本列。"""
    w = Path(workdir)
    if not exists(w) or not log:
        return None
    sync_cards(w)   # 第 3 步改過還沒同步的先同步，鍵才對得上
    index = _index(w)
    with _locked(w):
        st = load(w)
        have = st.version_at(log.get("產生時間"))
        if have is not None:
            return have
        row = build_version(st, w, log, index, _now(), note=note)
        _write_version_copy(w, row["版本號"], log)
        st.fmt.setdefault("同步", {})["處理紀錄"] = log.get("產生時間")
        _update_gen_rounds(st, w, _now())
        save(w, st)
    return row


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


def _reason(texts: list[str]) -> dict | None:
    t = "；".join(x for x in (str(y).strip() for y in texts) if x)
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
            new[c] = {"狀態": RETURNED, "原因": [d.get("原因", "") for d in backs]}
        elif rks and all(d.get("結果") == PASS for d in res):
            new[c] = {"狀態": PASSED, "原因": []}
        else:
            new[c] = {"狀態": NOT_SEEN, "原因": []}
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
            if (c, n) not in st.states:
                t = _r3(x.get("成品秒"))
                st.states[(c, n)] = {"卡號": c, "版本號": n, "成品": [t, t] if t is not None else None, "狀態": NOT_SEEN,
                                     "退回原因": None, "沿用自": None, "紀錄鍵": [], "更新時間": now}
        prev = new.get(c) or {"狀態": RETURNED, "原因": []}
        new[c] = {"狀態": RETURNED, "原因": (prev["原因"] if prev["狀態"] == RETURNED else []) + [x.get("原因", "")]}
    prev_v = max([k for k in st.versions if k < n], default=None)
    changed = 0
    for c, s in new.items():
        row = st.states.get((c, n)) or {"卡號": c, "版本號": n, "成品": (v.get("成品時間") or {}).get(c), "狀態": NOT_SEEN,
                                        "退回原因": None, "沿用自": None, "紀錄鍵": sorted(per.get(c) or []), "更新時間": now}
        why = _reason(s["原因"]) if s["狀態"] == RETURNED else None
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
        elif k not in want and cur and cur.get("起因") == WHY_RETURNED:
            c["等重做"] = None
            c["更新時間"] = now
            redone = str(v.get("時間") or "") > str(cur.get("時間") or "")   # 這一版比退回的時候新＝重做進這一版
            st.log(now, k, step, f"重做進第{n}版" if redone else "取消等重做", cur, None)
    # 這一版的輸出紀錄
    outs = []
    for rec in check.get("輸出紀錄") or []:
        if rec.get("處理紀錄產生時間") == v.get("時間") or (not rec.get("處理紀錄產生時間") and rec.get("來源") in (v.get("成品檔") or [])):
            outs.append({k: rec.get(k) for k in ("時間", "來源", "檔案", "檢查完才輸出", "看過百分比", "沒通過筆數")})
    if outs != (v.get("輸出紀錄") or []):
        v["輸出紀錄"] = outs
    return {"狀態有變": changed, "等重做": sorted(want, key=_card_num)}


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
    st = load(w)
    log = proclog.load(w) if log is None else log
    if not log:
        with _locked(w):
            st = load(w)
            st.fmt.setdefault("同步", {})["成品檢查"] = fp
            save(w, st)
        return None
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


def _backup(workdir: Path, stamp: str) -> tuple[str, int]:
    dst = needs_dir(workdir) / f"{BACKUP_PREFIX}{stamp}"
    n = 0
    for p in _small_files(workdir):
        rel = p.relative_to(workdir)
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst / rel)
        n += 1
    return str(dst.relative_to(workdir)), n


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


def plan_upgrade(workdir: str | Path, page: dict | None = None) -> tuple[Store, dict]:
    """升級要寫的全部東西（只讀舊檔，不寫任何檔）：回傳（記憶體裡的紀錄, 報告）。"""
    from bookclub import __version__, finalcheck, proclog

    w = Path(workdir)
    now = _now()
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
    st.fmt.setdefault("同步", {})["第3步"] = step3_snapshot(w)
    rep = _report(st, vrow, {"對上": g1["對上"] + g2["對上"], "對不到": g1["對不到"] + g2["對不到"]}, other, redo_hist, log)
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
    return out


def _report_md(rep: dict, backup: tuple[str, int] | None) -> str:
    lines = ["# 修改需求紀錄升級報告", "",
             f"升級時間：{rep['升級時間']}（程式版本 {rep['升級自程式版本']}）", "",
             "舊檔一個都沒改、沒刪；這份報告只有數字、編號、狀態，不含逐字稿與退回原因。", ""]
    lines += [f"- {x}" for x in report_lines(rep)[1:]]
    if backup:
        lines += ["", f"升級前的小檔備份在 `{backup[0]}/`（{backup[1]} 個檔）。",
                  "要重新升級：把 `修改需求/` 整個改名（例如 `修改需求_停用`），下次打開工作區就會重跑；不用刪。"]
    return "\n".join(lines) + "\n"


def upgrade(workdir: str | Path, *, dry_run: bool = False, page: dict | None = None, force: bool = False) -> dict:
    """舊工作區升級（31 號設計 §4）：只讀舊檔、只寫 `修改需求/`；小檔先備份；只跑一次（`格式.json` 在就不跑）。
    dry_run：只算、印數字，不寫任何檔。回傳報告（`升級了`＝這次有沒有真的寫）。"""
    w = Path(workdir)
    if exists(w) and not force:
        return {"升級了": False, "原因": "已經升級過了"}
    st, rep = plan_upgrade(w, page)
    if dry_run:
        return {**rep, "升級了": False, "試跑": True}
    with _locked(w):
        if exists(w) and not force:
            return {"升級了": False, "原因": "已經升級過了"}
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bk = _backup(w, stamp)
        st.fmt["升級前備份"] = bk[0]
        v = st.latest_version()
        if v is not None:
            log = wd.read_json(w / "生成" / "處理紀錄.json", default=None)
            if log:
                _write_version_copy(w, v["版本號"], log)
        _write(_p(w, REPORT_JSON), rep)
        (_p(w, REPORT_MD)).write_text(_report_md(rep, bk), encoding="utf-8")
        save(w, st)
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
    if log:
        st = load(w)
        if st.version_at(log.get("產生時間")) is None:
            add_version(w, log, note=NOTE_OLD_TOOL)
    mirror_final(w)
    with _locked(w):
        st = load(w)
        st.fmt.setdefault("同步", {})["檔案"] = stats
        save(w, st)


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


def ensure(workdir: str | Path, *, running: bool = False, page: dict | None = None, log=print) -> dict | None:
    """打開工作區時呼叫：沒升級過就升級（第 4 步執行中先不升級，等跑完）；升級過就補差異。
    任何錯誤都只印提醒，不擋畫面（第 1 段讀的那一邊還是舊檔）。"""
    w = Path(workdir)
    try:
        if not exists(w):
            if running:
                return None
            rep = upgrade(w, page=page)
            if rep.get("升級了"):
                log(f"[修改需求紀錄] 第一次用新版打開：已升級（卡片 {rep['卡片']['總數']} 張；舊檔沒動），"
                    f"報告在 {DIR_NAME}/{REPORT_MD}")
            return rep
        if page is not None:
            sync_cards(w, page)
        if not running:
            catch_up(w)
        return None
    except Exception as e:  # noqa: BLE001 — 新紀錄出錯不能擋住老師用舊功能
        log(f"[修改需求紀錄] ⚠️ 沒更新到（不影響畫面）：{type(e).__name__}")
        return None


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
        print(f"升級失敗：{type(e).__name__}（錯誤訊息不印，可能含資料內容）；舊檔沒動")
        return 1
    if not rep.get("升級了") and not rep.get("試跑"):
        print(f"這個工作區{rep.get('原因', '不用升級')}（{DIR_NAME}/{FORMAT_FILE} 已經在）。"
              f"要重新升級：把 {DIR_NAME}/ 改名後再跑一次")
        return 0
    sys.stdout.write("\n".join(report_lines(rep, dry=bool(rep.get("試跑")))) + "\n")
    if rep.get("升級了"):
        print(f"已寫入 {DIR_NAME}/（升級報告：{DIR_NAME}/{REPORT_MD}；小檔備份：{rep['備份']}/，{rep['備份檔數']} 個檔）")
    return 0
