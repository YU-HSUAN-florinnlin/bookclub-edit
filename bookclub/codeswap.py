"""把這一集的英文代號換成中文寫法（10-02 第七批，任務單 26 號第一段）。

起因：代號原本是英文名，聲音模型念英文名常念得怪、每次不一樣。宇軒 10-02 定：代號一律用外國人名的中文寫法
（`epcodes.CODE_POOL_F`／`CODE_POOL_M`）。已經配好英文代號的舊集數**不自動改**，用這裡的「換代號」一次換：

- `plan(workdir)`：列出這一集還在用的每一個英文代號、用在幾處、建議換成哪個中文（舊名單裡有對應的才有建議）
- `apply(workdir, mapping)`：照對照表換。存代號的地方（代號表、名字候選、人名決定、段落的本名代號、人工新增的名字）
  與「要念的文字」（學員段落的校對稿與建議稿、老師名字的改稿、重疊兩邊的文字與老師整句改稿、人工新增的老師整段文字）
  一起換。英文代號用整個詞比對、不分大小寫（`Rose` 不會動到 `Roseanne`）。換之前把會改到的檔備份到
  `<工作區>/備份/換代號_<時間>/`。換過的句子照現有規則判定要重新生成（生成紀錄的文字對不上就會重做）
- `leftover_texts(workdir)`：要念的文字裡還有舊英文代號名單（`epcodes.OLD_CODE_POOL`）上的名字（第 4 步開始前總檢查用）

命令列：`bookclub codes convert <工作區> [--map Joan=潔西 --map Emma=艾瑪] [--dry-run]`。
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

from bookclub import epcodes
from bookclub import workdir as wd

BACKUP_DIR = "備份"

# 「要念的文字」放在哪些欄位（只換這些，不碰逐字稿原文）
TURN_TEXT_KEYS = ("校對稿", "建議稿")
NAME_DEC_TEXT_KEYS = ("改稿",)
OVERLAP_TEXT_KEYS = ("老師文字", "學員文字", "老師整句改稿")
MANUAL_TEXT_KEYS = ("整段文字",)


def is_english(code: str | None) -> bool:
    return bool(code) and bool(re.search(r"[A-Za-z]", code))


def _pat(code: str):
    """英文代號：整個詞、不分大小寫（前後不能接英文字母）。"""
    return re.compile(rf"(?<![A-Za-z]){re.escape(code)}(?![A-Za-z])", re.I)


def count_in(text: str, code: str) -> int:
    return len(_pat(code).findall(text or "")) if text and code else 0


def replace_in(text: str, old: str, new: str) -> tuple[str, int]:
    """文字裡的舊代號換成新的（整個詞、不分大小寫）。回傳（換好的文字, 換了幾處）。"""
    if not text or not old:
        return text, 0
    return _pat(old).subn(lambda _m: new, text)


def _same(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and a.strip().lower() == b.strip().lower()


# ---------- 這一集有哪些地方存了代號、哪些是要念的文字 ----------

def _files(workdir: Path) -> dict[str, Path]:
    from bookclub import personnames, review, studentnames
    from bookclub import turns as turns_mod

    return {"段落": turns_mod.turns_path(workdir), "人名決定": personnames.decisions_path(workdir),
            "名字候選": wd.names_path(workdir), "學員名字候選": studentnames.cands_path(workdir),
            "名字覆核決定": review.name_decisions_path(workdir), "覆核決定": review.review_path(workdir),
            "代號表紀錄": epcodes.snapshot_path(workdir)}


def _code_slots(data: dict[str, object]) -> list[tuple[str, str, object, object]]:
    """存代號的每一格：(檔, 說明, 容器, 鍵)。容器[鍵] 是代號字串。"""
    out = []
    turns = data.get("段落") or {}
    for real, code in (turns.get("本名代號") or {}).items():
        if code:
            out.append(("段落", "本名代號", turns["本名代號"], real))
    for p in (turns.get("學員") or {}).values():
        for k in ("代號", "建議代號"):
            if p.get(k):
                out.append(("段落", f"學員{k}", p, k))
    for d in (data.get("人名決定") or {}).values():
        if isinstance(d, dict) and d.get("代號"):
            out.append(("人名決定", "代號", d, "代號"))
    for f in ("名字候選", "學員名字候選"):
        for c in (data.get(f) or {}).get("candidates", []) or []:
            if c.get("代號") and not c.get("敏感詞"):
                out.append((f, "代號", c, "代號"))
    for m in (data.get("覆核決定") or {}).get("人工名字", []) or []:
        if m.get("代號"):
            out.append(("覆核決定", "人工名字代號", m, "代號"))
    snap = data.get("代號表紀錄")
    if isinstance(snap, dict):
        for real, code in snap.items():
            if code:
                out.append(("代號表紀錄", "代號表紀錄", snap, real))
    return out


def _text_slots(data: dict[str, object]) -> list[dict]:
    """要念的文字的每一格：{檔, 欄位, 卡片, 容器, 鍵}（卡片＝第 3 步的項目鍵，總檢查帶過去）。"""
    out = []
    for t in (data.get("段落") or {}).get("段落", []) or []:
        for k in TURN_TEXT_KEYS:
            if t.get(k):
                out.append({"檔": "段落", "欄位": k, "卡片": f"學員段落:{t.get('id')}", "容器": t, "鍵": k})
    for cid, d in (data.get("名字覆核決定") or {}).items():
        if isinstance(d, dict):
            for k in NAME_DEC_TEXT_KEYS:
                if d.get(k):
                    out.append({"檔": "名字覆核決定", "欄位": k, "卡片": f"名字:{cid}", "容器": d, "鍵": k})
    dec = data.get("覆核決定") or {}
    for oid, d in (dec.get("重疊") or {}).items():
        if isinstance(d, dict):
            for k in OVERLAP_TEXT_KEYS:
                if d.get(k):
                    out.append({"檔": "覆核決定", "欄位": k, "卡片": f"重疊:{oid}", "容器": d, "鍵": k})
    for m in dec.get("人工名字", []) or []:
        for k in MANUAL_TEXT_KEYS:
            if m.get(k):
                out.append({"檔": "覆核決定", "欄位": k, "卡片": f"名字:{m.get('id')}", "容器": m, "鍵": k})
    return out


def _load(workdir: Path) -> dict[str, object]:
    return {k: wd.read_json(p, default=None) for k, p in _files(workdir).items()}


# ---------- 列出、換 ----------

def plan(workdir: str | Path) -> dict:
    """這一集還在用的英文代號：[{舊, 建議, 幾處（存代號的格數＋要念的文字裡出現幾次）}]，加上新名單（分女男）。"""
    workdir = Path(workdir)
    data = _load(workdir)
    codes: dict[str, str] = {}   # 小寫 → 第一次看到的寫法
    for real, code in epcodes.episode_codes(workdir).items():
        if is_english(code):
            codes.setdefault(code.lower(), code)
    for _f, _what, box, key in _code_slots(data):
        if is_english(box[key]):
            codes.setdefault(box[key].lower(), box[key])
    texts = _text_slots(data)
    for old in epcodes.OLD_CODE_POOL:   # 要念的文字裡出現的舊名單上的名字（人直接打進稿子的）
        if any(count_in(s["容器"][s["鍵"]], old) for s in texts):
            codes.setdefault(old.lower(), old)
    rows = []
    for low, code in sorted(codes.items()):
        n_store = sum(1 for _f, _w, box, key in _code_slots(data) if _same(box[key], code))
        n_text = sum(count_in(s["容器"][s["鍵"]], code) for s in texts)
        rows.append({"舊": code, "建議": epcodes.suggest_chinese(code), "存代號的地方": n_store, "要念的文字": n_text})
    return {"英文代號": rows, "選項": {"女": list(epcodes.CODE_POOL_F), "男": list(epcodes.CODE_POOL_M)}}


def resolve_mapping(workdir: str | Path, given: dict[str, str] | None = None) -> dict[str, str]:
    """給的對照（不分大小寫）＋舊名單有對應的預設 → {舊: 新}。還有沒對照的、新代號撞到別人的，丟 ValueError。"""
    workdir = Path(workdir)
    rows = plan(workdir)["英文代號"]
    given = {k.strip(): (v or "").strip() for k, v in (given or {}).items() if k and k.strip()}
    unknown = [k for k in given if not any(_same(k, r["舊"]) for r in rows)]
    if unknown:
        raise ValueError(f"這一集沒有用到這些代號：{'、'.join(unknown)}")
    out: dict[str, str] = {}
    for r in rows:
        new = next((v for k, v in given.items() if _same(k, r["舊"])), None) or r["建議"]
        if new:
            out[r["舊"]] = new
    lack = [r["舊"] for r in rows if r["舊"] not in out]
    if lack:
        raise ValueError(f"這幾個英文代號舊名單裡沒有對應，要選一個新的：{'、'.join(lack)}"
                         f"（命令列用 --map {lack[0]}=潔西 這種寫法）")
    bad = [f"{o}→{n}" for o, n in out.items() if is_english(n)]
    if bad:
        raise ValueError(f"新的代號要用中文寫法：{'、'.join(bad)}")
    by_new: dict[str, list[str]] = {}
    for o, n in out.items():
        by_new.setdefault(n, []).append(o)
    dup = {n: os_ for n, os_ in by_new.items() if len(os_) > 1}
    if dup:
        raise ValueError("同一集不能兩個人換成同一個代號：" + "；".join(f"{'、'.join(os_)} 都換成 {n}" for n, os_ in dup.items()))
    used = {c for c in epcodes.episode_codes(workdir).values() if c and not is_english(c)}
    clash = [f"{o}→{n}" for o, n in out.items() if n in used]
    if clash:
        raise ValueError(f"新的代號這一集已經有人用了：{'、'.join(clash)}")
    return out


def apply(workdir: str | Path, mapping: dict[str, str] | None = None, *, dry_run: bool = False) -> dict:
    """照對照表換這一集的英文代號（見檔案開頭）。回傳 {對照, 每個代號換了幾處, 改到的檔, 備份, 還剩}。"""
    from bookclub import review
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    mp = resolve_mapping(workdir, mapping)
    files = _files(workdir)
    with turns_mod._lock, review._lock:
        data = _load(workdir)
        before = {k: wd.read_json(p, default=None) for k, p in files.items()}
        counts = {o: 0 for o in mp}
        for _f, _what, box, key in _code_slots(data):
            for o, n in mp.items():
                if _same(box[key], o):
                    box[key] = n
                    counts[o] += 1
        for s in _text_slots(data):
            txt = s["容器"][s["鍵"]]
            for o, n in mp.items():
                txt, k = replace_in(txt, o, n)
                counts[o] += k
            s["容器"][s["鍵"]] = txt
        changed = [k for k in files if data.get(k) is not None and data[k] != before[k]]
        result = {"對照": mp, "每個代號換了幾處": counts, "改到的檔": [str(files[k].relative_to(workdir)) for k in changed],
                  "備份": None, "試跑": dry_run}
        if dry_run:
            return result
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bdir = workdir / BACKUP_DIR / f"換代號_{stamp}"
        for k in files:
            if files[k].exists():
                dst = bdir / files[k].relative_to(workdir)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(files[k], dst)
        result["備份"] = str(bdir.relative_to(workdir))
        for k in changed:
            wd.write_json(files[k], data[k])
    # 名冊上的代號（相容用，名冊不動）：上面換完這一集還是英文的，就是從名冊來的——改記在這一集：
    # ② 選過的人寫進本名代號，其他寫成 ③ 的「換成代號」（鎖外面做）
    roster_only = [(real, code) for real, code in epcodes.episode_codes(workdir).items()
                   if is_english(code) and any(_same(code, o) for o in mp)]
    if roster_only:
        tdata = wd.read_json(files["段落"], default={}) or {}
        two = {p.get("本名") for p in (tdata.get("學員") or {}).values() if p.get("本名")}
        pdec_path = files["人名決定"]
        pdec = wd.read_json(pdec_path, default={}) or {}
        for real, code in roster_only:
            new = next(n for o, n in mp.items() if _same(code, o))
            if real in two:
                with turns_mod._lock:
                    td = wd.read_json(files["段落"], default={}) or {}
                    td.setdefault("本名代號", {})[real] = new
                    wd.write_json(files["段落"], td)
            elif not pdec.get(real):
                pdec[real] = {"做法": "換成代號", "代號": new, "同一人": None,
                              "更新時間": datetime.now().isoformat(timespec="seconds"), "來源": "換代號（原本用名冊上的代號）"}
            counts[next(o for o in mp if _same(code, o))] += 1
        wd.write_json(pdec_path, pdec)
    wd.write_json(epcodes.snapshot_path(workdir), epcodes.episode_codes(workdir))   # 代號表紀錄跟著新的（不再當成「代號改了」）
    epcodes.sync(workdir)
    result["還剩"] = [r["舊"] for r in plan(workdir)["英文代號"]]
    return result


def leftover_texts(workdir: str | Path) -> list[dict]:
    """要念的文字裡還有舊英文代號名單上的名字（第 4 步開始前總檢查用）：[{卡片, 欄位, 代號: [...]}]。"""
    data = _load(Path(workdir))
    out = []
    for s in _text_slots(data):
        hit = [c for c in epcodes.OLD_CODE_POOL if count_in(s["容器"][s["鍵"]], c)]
        if hit:
            out.append({"卡片": s["卡片"], "欄位": s["欄位"], "代號": hit})
    return out


def parse_map(items: list[str] | None) -> dict[str, str]:
    """命令列的 `--map Joan=潔西`（可以給好幾次，也可以一次給「Joan=潔西,Emma=艾瑪」）。"""
    out = {}
    for it in items or []:
        for part in re.split(r"[,，]", it):
            if not part.strip():
                continue
            if "=" not in part:
                raise ValueError(f"看不懂的對照：{part}（要寫成 舊=新，例如 Joan=潔西）")
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out
