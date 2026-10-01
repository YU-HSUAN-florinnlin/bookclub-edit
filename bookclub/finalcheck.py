"""第 5 步「成品檢查」（09-29 宇軒：原本第 5 步逐筆覆核＋第 6 步整片檢查合成一步）。

畫面（`bookclub/web/finalcheck.js`）像第 3 步：左邊成品影片＋時間軸（AI 處理過的地方全部標出來，資料來自
`生成/處理紀錄.json`），右邊「目前這一筆」。最上面固定一行：沒列在時間軸上的地方＝原片沒動。

兩種模式：
- 逐筆看：每一筆切換「處理前／處理後」試聽，按「通過」或「退回重做」（要寫原因）
- 整片看：記錄實際播放過的時間區段（網頁只送 2 倍速以下連續播的，09-29 宇軒；取聯集），顯示「已經看過全片的 x%」；看到問題按一下就在目前時間建一筆退回重做

另外一區：處理紀錄的「未登記的變動」（聲音變了但沒有紀錄），要人一筆一筆確認（沒問題／退回重做）。

按「送回 AI 重做（N 筆）」寫進 `覆核/成品檢查.json` 的 `送回AI重做`，`bookclub redo list <工作區>` 列得出來。
**全部通過、而且整片看過 100% 才能按「輸出成品」**（09-18 宇軒：最後一定要有人完整看過整支）。

存檔：`覆核/成品檢查.json`（格式見 `docs/工作區格式.md`）。純函式為主（看過比例、退回清單、能不能輸出），
`bookclub/server.py` 只負責轉手。
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from bookclub import proclog
from bookclub import workdir as wd

PASS, REDO = "通過", "退回重做"
UNLOGGED_OK = "沒問題"
FULL_WATCH_SLACK_S = 0.5     # 看過比例：沒看到的地方加起來不超過 0.5 秒就算看完（播放器最後一格、四捨五入）
CONTEXT_S = 2.0              # 處理前／處理後試聽，前後各多 2 秒
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".mkv")

_lock = threading.Lock()


def check_path(workdir: Path) -> Path:
    return Path(workdir) / "覆核" / "成品檢查.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- 純函式 ----------

def merge_ranges(ranges: list) -> list[list[float]]:
    """時間區段取聯集（相接的也併起來），依時間排序。"""
    out: list[list[float]] = []
    for s, e in sorted(([float(a), float(b)] for a, b in ranges if b > a), key=lambda x: x[0]):
        if out and s <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [[round(s, 3), round(e, 3)] for s, e in out]


def watched_seconds(ranges: list, total: float) -> float:
    return sum(min(e, total) - max(s, 0.0) for s, e in merge_ranges(ranges) if min(e, total) > max(s, 0.0))


def watched_ratio(ranges: list, total: float) -> float:
    """看過全片的比例（0～1）。沒看到的加起來不超過 FULL_WATCH_SLACK_S 秒算 1。"""
    if total <= 0:
        return 0.0
    seen = watched_seconds(ranges, total)
    if total - seen <= FULL_WATCH_SLACK_S:
        return 1.0
    return min(1.0, seen / total)


def record_key(r: dict) -> str:
    """處理紀錄的一筆 → 穩定的鍵（重組之後編號可能變，用類型＋原片起點）。"""
    s = (r.get("原片") or [None])[0]
    return f"{r['類型']}@{s:.2f}" if s is not None else f"{r['類型']}#{'、'.join(r.get('覆核項目') or [])}"


def record_print(r: dict) -> str:
    """這一筆的內容指紋：重組之後內容變了（換了生成檔、時間變了），之前的通過／退回就不算數。"""
    return "|".join(str(x) for x in (r.get("做了什麼"), r.get("檔案"), r.get("成品"), r.get("文字")))


def unlogged_key(u: dict) -> str:
    return f"{u['原片'][0]:.2f}"


def from_output_time(t: float, plist: list[dict] | None) -> float:
    """成品時間 → 原片時間（落在停格裡算停格那一點）。render audio 沒有片段表，時間一樣。"""
    if not plist:
        return t
    acc = 0.0
    for p in plist:
        s, e = p["src"]
        if t <= acc + (e - s):
            return s + max(0.0, t - acc)
        acc += e - s
        if t <= acc + p["freeze"]:
            return e
        acc += p["freeze"]
    return plist[-1]["src"][1]


def to_output_time(t: float, plist: list[dict] | None) -> float | None:
    if not plist:
        return t
    from bookclub.render import to_output_time as f

    return f(t, plist)


def status(log: dict | None, check: dict, total: float | None = None) -> dict:
    """進度、退回清單、能不能輸出（純函式）。"""
    recs = (log or {}).get("紀錄", [])
    items = check.get("逐筆", {})
    passed = sum(1 for r in recs if items.get(record_key(r), {}).get("結果") == PASS)
    redo_n = sum(1 for r in recs if items.get(record_key(r), {}).get("結果") == REDO)
    un = (log or {}).get("未登記的變動", [])
    un_dec = check.get("未登記確認", {})
    un_ok = sum(1 for u in un if un_dec.get(unlogged_key(u), {}).get("結果") == UNLOGGED_OK)
    un_redo = sum(1 for u in un if un_dec.get(unlogged_key(u), {}).get("結果") == REDO)
    total = float(total if total is not None else check.get("成品長度") or 0.0)
    ratio = watched_ratio(check.get("看過區段", []), total)
    redo = redo_items(log, check)
    why = []
    if not log:
        why.append("還沒有處理紀錄（第 4 步還沒組裝）")
    if passed < len(recs):
        why.append(f"還有 {len(recs) - passed} 筆沒通過")
    if un_ok < len(un):
        why.append(f"還有 {len(un) - un_ok} 處沒登記的變動沒確認沒問題")
    if redo:
        why.append(f"有 {len(redo)} 筆退回重做，要先送回 AI 重做、重新組裝")
    if ratio < 1.0:
        why.append(f"整片只看過 {int(ratio * 100)}%，要看完 100%")
    return {"逐筆": {"通過": passed, "退回": redo_n, "總數": len(recs)},
            "未登記": {"沒問題": un_ok, "退回": un_redo, "總數": len(un)},
            "看過比例": round(ratio, 4), "看過秒數": round(watched_seconds(check.get("看過區段", []), total), 1),
            "成品長度": total, "退回數": len(redo), "可以輸出": not why, "還不能輸出的原因": why}


REGEN_KINDS = ("學員重念", "名字整句換掉", "換聲音")   # 改了範圍要重新生成的（其他的只要重新組裝）


def retime_target(rec: dict, index: dict) -> dict:
    """第 5 步按「退回重做」時，能不能在這裡直接改這一筆的時間範圍（10-01 2-4，純函式）。

    能改的，改的是第 3 步同一個地方（跟第 3 步「改時間」同一支 API），回傳
    {可以: True, 方式: 改時間｜重念範圍, 類型（改時間面板的類型）, id, 名稱, 第3步, start, end, 重做}；
    不能改的回傳 {可以: False, 原因, 下一步, 第3步, 名稱}，畫面上帶到第 3 步那一張卡片。
    `index` 是 `review.item_index`。"""
    from bookclub.review import item_name

    kind = rec["類型"]
    keys = rec.get("覆核項目") or []
    cards = [k for k in keys if (index.get(k) or {}).get("第3步")]
    first = cards[0] if cards else None
    base = {"第3步": index[first]["第3步"] if first else None, "名稱": item_name(index, first) if first else ""}
    redo = "重新生成這一筆、再重新組裝" if kind in REGEN_KINDS else "重新組裝"

    def no(why: str, nxt: str) -> dict:
        return {**base, "可以": False, "原因": why, "下一步": nxt}

    def yes(info: dict, how: str = "改時間", a: float | None = None, b: float | None = None) -> dict:
        return {**base, "可以": True, "方式": how, "類型": info.get("改時間"), "id": info["id"], "名稱": info["名稱"],
                "第3步": info["第3步"], "老師整段": bool(info.get("老師整段")), "start": round(info["start"] if a is None else a, 3),
                "end": round(info["end"] if b is None else b, 3), "重做": redo}

    def editable(k: str | None) -> dict | None:
        info = index.get(k or "")
        return info if info and info.get("第3步") and info.get("改時間") else None

    go = f"到第 3 步〈{base['名稱']}〉" if first else "到第 3 步找原片這個時間附近的那一筆"
    names = [k for k in keys if k.startswith("名字:")]
    ovs = [k for k in keys if k.startswith("重疊:") or (k.startswith("學員段落:") and (index.get(k) or {}).get("類型") == "重疊")]
    if kind == "停格":
        return no("停格是自動加的：重念的聲音比原本長，畫面停一下補長，不能單獨改範圍", f"{go}改範圍或要念的字")
    if kind in ("學員名字消音", "學員名字換代號"):
        return no("學員提到名字的範圍照逐字稿的字自動抓，第 3 步也沒有改時間", f"{go}改做法（直接消音或換成代號）")
    if kind == "模糊示範":
        return no("畫面模糊是示範用的，沒有範圍可以改", "不用改")
    if kind == "名字要人處理":
        return no("這一筆沒有自動處理（原片沒動）", f"{go}把要重念的句子改好（名字寫成代號），或改成直接消音")
    if ovs and any((index.get(k) or {}).get("疊放") for k in ovs):
        return no("這一處重疊選了兩邊都重新生成：老師和學員各有自己的起訖", f"{go}的「改做法」裡改兩邊各自的起訖")
    if kind in ("名字整句換掉", "換聲音"):
        if len(names) > 1:
            return no("這一句同時換掉好幾個名字（" + "、".join(f"〈{item_name(index, k)}〉" for k in names)
                      + "），重念範圍是合起來的", "到第 3 步這幾張卡片各自改重念範圍")
        if names and ovs:
            return no("這一句同時是名字重念和重疊的老師那一句", f"{go}改重念範圍")
        if not names:
            return no("這一句是重疊選了「生成老師聲音」：重念的是老師整句，範圍照逐字稿的句子走", f"{go}改重疊的時間或做法")
        info = editable(names[0])
        if not info:
            return no("第 3 步現在找不到這一筆", go)
        if info.get("老師整段"):
            return yes(info)
        o = rec.get("原片") or [info["start"], info["end"]]
        return yes(info, "重念範圍", o[0], o[1])
    if kind in ("名字消音", "消音"):
        info = editable(names[0]) if len(names) == 1 else None
        return yes(info) if info else no("對不到第 3 步的名字卡片", go)
    target = editable(first)
    if kind in ("學員重念", "局部消音", "刪除", "重疊") and target:
        if kind == "刪除":
            o = rec.get("原片") or [target["start"], target["end"]]
            return yes(target, a=o[0], b=o[1])
        return yes(target)
    return no("第 3 步現在找不到可以改時間的那一筆", go)


def label_items(items: list[dict], index: dict) -> list[dict]:
    """每一筆加 `覆核名稱`（畫面上看得到的名稱，10-01 1-5：不顯示 T062、O5602.14 這類內部編號）。"""
    from bookclub.review import item_name

    for it in items:
        it["覆核名稱"] = [item_name(index, k) for k in it.get("覆核項目") or []]
    return items


_ID_RE = None


def plain_ids(text: str, index: dict, workdir: Path | None = None) -> str:
    """處理紀錄「做了什麼」裡的內部編號（T034_15、V0542232、M001⋯）換成畫面上看得到的名稱（10-01 走查：
    第 5 步停格、重疊那幾筆還寫著 T034_15）。找不到的寫「另一筆」，不露編號。紀錄檔本身不改。"""
    import re

    global _ID_RE
    if not text:
        return text
    if _ID_RE is None:
        _ID_RE = re.compile(r"(?<![A-Za-z0-9_])(T\d{3}(?:m\d+)*(?:_\d+)?|S\d{3}|SNM\d{3}|NM\d{3}|V\d{7}|M\d{3}|D\d{3}|O\d+\.\d+)(?![A-Za-z0-9_])")
    names = {row["id"]: row["名稱"] for row in index.values() if row.get("id")}
    if workdir is not None:
        try:
            from bookclub import nameplan

            for g in (wd.read_json(nameplan.plan_path(Path(workdir)), default=None) or {}).get("生成", []):
                if g.get("id") and g.get("slot"):
                    names.setdefault(g["id"], f"老師重念 {wd.fmt_time(g['slot'][0])}")
        except Exception:  # noqa: BLE001 — 讀不到計畫就只用第 3 步的名稱
            pass

    def name_of(tok: str) -> str | None:
        base = tok
        while base:
            if base in names:
                return names[base]
            nxt = re.sub(r"(_\d+|m\d+)$", "", base)
            if nxt == base:
                return None
            base = nxt
        return None

    def sub(m) -> str:
        tok = m.group(1)
        nm = name_of(tok)
        if nm is None:
            return "另一筆"
        return f"〈{nm}〉裡的一句" if re.search(r"_\d+$", tok) else f"〈{nm}〉"
    out = _ID_RE.sub(sub, text)
    out = re.sub(r"\s*(〈[^〉]*〉(?:裡的一句)?)\s*", r"\1", out)
    return plain_words(out)


def plain_words(text: str) -> str:
    """10-01 走查：第 5 步跟第 3 步用同一套說法——重疊的做法用畫面上的名稱（存檔的值不變）、刪除叫「剪掉」。"""
    import re

    from bookclub.review import OVERLAP_LABEL

    text = re.sub(r"^刪除 ([\d.]+) 秒（聲音畫面一起刪）", r"剪掉 \1 秒（聲音和畫面都拿掉）", text)
    for raw in sorted(OVERLAP_LABEL, key=len, reverse=True):
        text = text.replace(f"重疊（{raw}）", f"重疊（{OVERLAP_LABEL[raw]}）").replace(f"做法：{raw}）", f"做法：{OVERLAP_LABEL[raw]}）")
    return text


def _index(workdir: Path) -> dict:
    from bookclub import review

    try:
        return review.item_index(workdir)
    except Exception:  # noqa: BLE001 — 讀不到第 3 步的資料不要擋住成品檢查，只是名稱退回類型
        return {}


def redo_items(log: dict | None, check: dict) -> list[dict]:
    """要送回 AI 重做的：逐筆退回的、整片看時退回的、沒登記的變動退回的。每一筆帶對應的第 3 步覆核項目。"""
    out = []
    recs = (log or {}).get("紀錄", [])
    items = check.get("逐筆", {})
    for r in recs:
        d = items.get(record_key(r), {})
        if d.get("結果") == REDO:
            out.append({"來源": "逐筆", "鍵": record_key(r), "類型": r["類型"], "原片": r.get("原片"), "成品": r.get("成品"),
                        "覆核項目": r.get("覆核項目", []), "做了什麼": r.get("做了什麼"), "原因": d.get("原因", ""),
                        **({"改範圍": d["改範圍"]} if d.get("改範圍") else {})})
    for x in check.get("整片退回", []):
        out.append({"來源": "整片看", "鍵": x["id"], "類型": "整片看時標的", "原片": [x["原片秒"], x["原片秒"]],
                    "成品": [x["成品秒"], x["成品秒"]], "覆核項目": x.get("覆核項目", []), "做了什麼": "",
                    "原因": x.get("原因", "")})
    un_dec = check.get("未登記確認", {})
    for u in (log or {}).get("未登記的變動", []):
        d = un_dec.get(unlogged_key(u), {})
        if d.get("結果") == REDO:
            out.append({"來源": "沒登記的變動", "鍵": unlogged_key(u), "類型": "沒登記的變動", "原片": u["原片"],
                        "成品": u.get("成品"), "覆核項目": [], "做了什麼": "聲音變了但沒有紀錄", "原因": d.get("原因", "")})
    return out


def items_near(log: dict | None, t_out: float, pad: float = 1.0) -> list[str]:
    """整片看時標的時間（成品）附近的處理紀錄 → 覆核項目（送回重做時知道是哪一筆）。"""
    keys: list[str] = []
    for r in (log or {}).get("紀錄", []):
        c = r.get("成品")
        if c and c[0] is not None and c[1] is not None and c[0] - pad <= t_out <= c[1] + pad:
            keys += [k for k in r.get("覆核項目", []) if k not in keys]
    return keys


def refresh(check: dict, log: dict | None) -> dict:
    """處理紀錄重寫過（重新組裝）：內容變了的那幾筆，之前的通過／退回不算數；成品變了，看過區段歸零
    （新的成品要重新看完）。回傳整理過的 check（不改傳進來的）。"""
    import copy

    check = copy.deepcopy(check)
    if not log:
        return check
    stamp = log.get("產生時間")
    if check.get("處理紀錄產生時間") == stamp:
        return check
    prints = {record_key(r): record_print(r) for r in log.get("紀錄", [])}
    check["逐筆"] = {k: v for k, v in check.get("逐筆", {}).items() if prints.get(k) == v.get("指紋")}
    keep_un = {unlogged_key(u) for u in log.get("未登記的變動", [])}
    check["未登記確認"] = {k: v for k, v in check.get("未登記確認", {}).items() if k in keep_un}
    if check.get("處理紀錄產生時間"):
        check["看過區段"] = []
        check["整片退回"] = []
    check["處理紀錄產生時間"] = stamp
    return check


# ---------- 讀寫工作區 ----------

def load_check(workdir: Path) -> dict:
    data = wd.read_json(check_path(workdir), default=None) or {}
    data.setdefault("版本", 1)
    for k, v in (("逐筆", {}), ("整片退回", []), ("未登記確認", {}), ("看過區段", []), ("成品影片", None)):
        data.setdefault(k, v)
    return data


def _save(workdir: Path, data: dict) -> None:
    wd.write_json(check_path(workdir), data)


def products(workdir: Path) -> list[str]:
    """可以檢查的成品影片（`輸出/成品_*`，相對工作區，新的在前）。"""
    out = Path(workdir) / "輸出"
    if not out.is_dir():
        return []
    files = [p for p in out.iterdir() if p.name.startswith("成品_") and p.suffix.lower() in VIDEO_EXTS
             and "_驗證沒過" not in p.stem]   # 09-29：驗證沒過的成品不拿來檢查
    # 標字版是給人看 AI 改了哪裡的輔助版，排在正式成品後面，免得被預設選去檢查
    files.sort(key=lambda p: ("標字版" in p.stem, -p.stat().st_mtime))
    return [str(p.relative_to(workdir)) for p in files]


def probe_duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _current(workdir: Path) -> tuple[dict | None, dict]:
    log = proclog.load(workdir)
    check = refresh(load_check(workdir), log)
    prods = products(workdir)
    if check.get("成品影片") not in prods:
        check["成品影片"] = prods[0] if prods else None
        check["看過區段"] = []
    if check["成品影片"] and not check.get("成品長度"):
        check["成品長度"] = round(probe_duration(Path(workdir) / check["成品影片"]), 3)
    return log, check


def page_data(workdir: str | Path) -> dict:
    """`GET /api/final`：成品檢查頁一次要的全部資料。"""
    workdir = Path(workdir)
    with _lock:
        log, check = _current(workdir)
        _save(workdir, check)
    plist = (log or {}).get("片段")
    index = _index(workdir)
    recs = []
    for r in (log or {}).get("紀錄", []):
        d = check["逐筆"].get(record_key(r), {})
        tgt = retime_target(r, index)
        done = d.get("改範圍")
        if tgt.get("可以") and done and done.get("改成"):   # 10-01 2-4：改過了（還沒重新組裝），面板上顯示改後的
            tgt["start"], tgt["end"] = done["改成"]
        recs.append({**r, "鍵": record_key(r), "結果": d.get("結果"), "原因": d.get("原因", ""),
                     "做了什麼": plain_ids(r.get("做了什麼") or "", index, workdir),
                     "覆核名稱": [review_name(index, k) for k in r.get("覆核項目") or []],
                     "改範圍": tgt, "已改範圍": done})
    flags = label_items([dict(x) for x in check["整片退回"]], index)
    un = []
    for u in (log or {}).get("未登記的變動", []):
        d = check["未登記確認"].get(unlogged_key(u), {})
        t = to_output_time(u["原片"][0], plist)
        un.append({**u, "鍵": unlogged_key(u), "成品秒": t, "結果": d.get("結果"), "原因": d.get("原因", "")})
    return {
        "有處理紀錄": log is not None, "來源": (log or {}).get("來源"), "範圍": (log or {}).get("範圍"),
        "處理紀錄產生時間": (log or {}).get("產生時間"),
        "成品影片": check["成品影片"], "成品影片清單": products(workdir), "成品長度": check.get("成品長度") or 0.0,
        "影片網址": "/api/final/video" if check["成品影片"] else None,
        "紀錄": recs, "未登記的變動": un, "整片退回": flags, "看過區段": check["看過區段"], "片段": plist,
        "送回AI重做": check.get("送回AI重做"), "輸出成品": check.get("輸出成品"),
        "狀態": status(log, check),
    }


def choose_product(workdir: str | Path, rel: str) -> dict:
    workdir = Path(workdir)
    if rel not in products(workdir):
        raise ValueError(f"不是這個工作區的成品影片：{rel}")
    with _lock:
        log, check = _current(workdir)
        if check["成品影片"] != rel:
            check.update({"成品影片": rel, "看過區段": [], "成品長度": round(probe_duration(workdir / rel), 3)})
        _save(workdir, check)
    return {"ok": True}


def review_name(index: dict, key: str) -> str:
    from bookclub.review import item_name

    return item_name(index, key)


def decide_record(workdir: str | Path, key: str, result: str | None, reason: str = "", retime: dict | None = None) -> dict:
    """`POST /api/final/item`：一筆通過或退回重做（退回要寫原因）；result 給 None 是改回還沒看。
    retime（10-01 2-4）：退回時在這裡改了時間範圍 → 記在這一筆的 `改範圍`（{名稱, 原本, 改成, 第3步, 重做, 時間}），
    第 4 步的退回清單照這個寫「按開始執行只重做這一筆」。範圍本身已經存在第 3 步的地方（`/api/review/manual`、`/api/review/name`）。"""
    if result not in (PASS, REDO, None):
        raise ValueError("只能選：通過、退回重做")
    if result == REDO and not str(reason).strip():
        raise ValueError("退回重做要寫原因（AI 重做時才知道要改什麼）")
    workdir = Path(workdir)
    with _lock:
        log, check = _current(workdir)
        rec = next((r for r in (log or {}).get("紀錄", []) if record_key(r) == key), None)
        if rec is None:
            raise KeyError(f"處理紀錄裡沒有這一筆：{key}")
        if result is None:
            check["逐筆"].pop(key, None)
        else:
            old = (check["逐筆"].get(key) or {}).get("改範圍") if result == REDO else None
            check["逐筆"][key] = {"結果": result, "原因": str(reason).strip(), "指紋": record_print(rec), "更新時間": _now()}
            if result == REDO and (retime or old):
                new = {k: v for k, v in (retime or {}).items() if k in ("名稱", "原本", "改成", "第3步", "重做")}
                merged = {**(old or {}), **new, "時間": _now()}
                if old and old.get("原本"):   # 改了好幾次：「原本」留第一次改之前的
                    merged["原本"] = old["原本"]
                check["逐筆"][key]["改範圍"] = merged
        _save(workdir, check)
        return {"ok": True, "狀態": status(log, check)}


def decide_unlogged(workdir: str | Path, key: str, result: str | None, reason: str = "") -> dict:
    """`POST /api/final/unlogged`：沒登記的變動確認沒問題或退回重做。"""
    if result not in (UNLOGGED_OK, REDO, None):
        raise ValueError("只能選：沒問題、退回重做")
    if result == REDO and not str(reason).strip():
        raise ValueError("退回重做要寫原因")
    workdir = Path(workdir)
    with _lock:
        log, check = _current(workdir)
        if not any(unlogged_key(u) == key for u in (log or {}).get("未登記的變動", [])):
            raise KeyError(f"沒有這一處：{key}")
        if result is None:
            check["未登記確認"].pop(key, None)
        else:
            check["未登記確認"][key] = {"結果": result, "原因": str(reason).strip(), "更新時間": _now()}
        _save(workdir, check)
        return {"ok": True, "狀態": status(log, check)}


def add_whole_redo(workdir: str | Path, t_out: float, reason: str) -> dict:
    """`POST /api/final/flag`：整片看時，在目前時間（成品秒數）建一筆退回重做。"""
    if not str(reason).strip():
        raise ValueError("退回重做要寫原因")
    workdir = Path(workdir)
    with _lock:
        log, check = _current(workdir)
        plist = (log or {}).get("片段")
        n = 1 + max([int(x["id"][1:]) for x in check["整片退回"] if x["id"][1:].isdigit()] or [0])
        item = {"id": f"R{n:03d}", "成品秒": round(float(t_out), 3), "原片秒": round(from_output_time(float(t_out), plist), 3),
                "覆核項目": items_near(log, float(t_out)), "原因": str(reason).strip(), "建立時間": _now()}
        check["整片退回"].append(item)
        _save(workdir, check)
        return {"ok": True, "項目": item, "狀態": status(log, check)}


def remove_whole_redo(workdir: str | Path, rid: str) -> dict:
    workdir = Path(workdir)
    with _lock:
        log, check = _current(workdir)
        check["整片退回"] = [x for x in check["整片退回"] if x["id"] != rid]
        _save(workdir, check)
        return {"ok": True, "狀態": status(log, check)}


def add_watched(workdir: str | Path, ranges: list, product: str | None = None) -> dict:
    """`POST /api/final/watched`：播放器實際播過的區段（網頁只送 2 倍速以下連續播的，成品秒數），跟之前的取聯集。"""
    workdir = Path(workdir)
    with _lock:
        log, check = _current(workdir)
        if product and product != check["成品影片"]:
            raise ValueError("播放中的影片不是目前要檢查的成品，重新整理再看")
        check["看過區段"] = merge_ranges(check["看過區段"] + [[float(a), float(b)] for a, b in ranges])
        _save(workdir, check)
        st = status(log, check)
        return {"ok": True, "看過比例": st["看過比例"], "看過區段": check["看過區段"], "狀態": st}


def send_back(workdir: str | Path) -> dict:
    """`POST /api/final/sendback`：退回的全部寫進 `送回AI重做`，第 4 步讀這份（`bookclub redo list`）。"""
    workdir = Path(workdir)
    with _lock:
        log, check = _current(workdir)
        items = redo_items(log, check)
        if not items:
            raise ValueError("沒有退回重做的項目")
        check["送回AI重做"] = {"時間": _now(), "處理紀錄產生時間": (log or {}).get("產生時間"), "項目": items}
        _save(workdir, check)
        return {"ok": True, "筆數": len(items), "送回AI重做": check["送回AI重做"]}


def export_final(workdir: str | Path) -> dict:
    """`POST /api/final/export`：全部通過、整片看過 100% 才能輸出——把檢查過的成品複製成 `輸出/最終成品_<檔名>`。"""
    workdir = Path(workdir)
    with _lock:
        log, check = _current(workdir)
        st = status(log, check)
        if not st["可以輸出"]:
            raise ValueError("還不能輸出：" + "；".join(st["還不能輸出的原因"]))
        src = workdir / check["成品影片"]
        dst = src.with_name("最終成品_" + src.name.removeprefix("成品_"))
        shutil.copy2(src, dst)
        check["輸出成品"] = {"時間": _now(), "來源": check["成品影片"], "檔案": str(dst.relative_to(workdir))}
        _save(workdir, check)
        return {"ok": True, **check["輸出成品"]}


def redo_list(workdir: str | Path) -> dict:
    """`bookclub redo list`：第 4 步要重做的項目（送回的那一份；還沒按送回就列目前退回的），附建議指令。"""
    workdir = Path(workdir)
    log, check = _current(workdir)
    sent = check.get("送回AI重做")
    now = redo_items(log, check)
    if sent:   # 送回之後又改成通過的、重新組裝後不算數的，拿掉
        keys = {i["鍵"] for i in now}
        items = [i for i in sent["項目"] if i["鍵"] in keys]
        sent = sent if items else None
    if sent:   # 10-01 走查：送回之後才退回的也要列（以前要再按一次送回，第 4 步才看得到）
        have = {i["鍵"] for i in items}
        items = items + [i for i in now if i["鍵"] not in have]
    else:
        items = now
    if sent:   # 送回之後才在第 5 步改了範圍的，照最新的
        latest = {i["鍵"]: i for i in now}
        items = [{**i, **({"改範圍": latest[i["鍵"]]["改範圍"]} if latest.get(i["鍵"], {}).get("改範圍") else {})} for i in items]
    index = _index(workdir)
    for it in items:
        it["建議指令"] = suggest_command(workdir, it)
        it["做了什麼"] = plain_ids(it.get("做了什麼") or "", index, workdir)   # 10-01 走查：第 4 步也不露內部編號
    label_items(items, index)
    return {"已送回": bool(sent), "時間": (sent or {}).get("時間"), "項目": items}


def suggest_command(workdir: Path, it: dict) -> str:
    """每一筆退回要怎麼重做（09-29 宇軒：先給指令手動做；之後改一鍵只重做這幾筆，方向見 docs/之後要做.md）。

    TODO（09-29）：真的只重做這幾筆，要（1）清掉那一段的生成快取（`生成/學員/_嘗試快取.json` 那一段、
    `生成/老師紀錄.json` 那一句），不然重跑會沿用舊的結果；（2）把退回原因交給 AI（例如改稿子、換種子、改停頓）；
    （3）重跑 `render video` 同一個範圍。宇軒本機同時在改 tts／students／render，等那邊定案再串。"""
    if it.get("改範圍"):   # 10-01 2-4：範圍改過了，第 4 步的「做過沒有」會看出這一筆的時間格變了
        return f"（範圍已經改了：第 4 步按「開始執行」會{it['改範圍'].get('重做') or '重新組裝'}，其他做好的不重做）"
    stu = sorted({k.split(":", 1)[1] for k in it.get("覆核項目", []) if k.startswith("學員段落:")})
    names = sorted({k.split(":", 1)[1] for k in it.get("覆核項目", []) if k.startswith("名字:")})
    w = str(workdir)
    if stu:
        return f"bookclub gen students {w} --only {','.join(stu)}"
    if names:
        nums = [n for n in names if n.isdigit()]
        return f"bookclub gen names {w} --only {','.join(nums)}" if nums else f"bookclub gen names {w}"
    return "（這一筆要人看原因決定怎麼改，例如回第 3 步調整刪除段落或重疊的做法）"


def clip(workdir: str | Path, which: str, key: str) -> Path:
    """處理前／處理後試聽檔：處理前＝原聲（原片時間）、處理後＝新聲音（成品時間），前後各多 2 秒。"""
    workdir = Path(workdir)
    log = proclog.load(workdir) or {}
    rec = next((r for r in log.get("紀錄", []) if record_key(r) == key), None)
    if rec is None or not rec.get("原片"):
        raise KeyError(f"處理紀錄裡沒有這一筆：{key}")
    if which == "前":
        src, (s, e) = workdir / log["原聲"], rec["原片"]
        offset = (log.get("範圍") or [0.0])[0]
    elif which == "後":
        src, (s, e) = workdir / log["新聲音"], rec["成品"]
        offset = 0.0
        if s is None or e is None:
            raise ValueError("這一筆在成品裡被刪掉了，沒有處理後的聲音")
    else:
        raise ValueError("which 只能是 前 或 後")
    a, b = max(0.0, s - offset - CONTEXT_S), e - offset + CONTEXT_S
    cache = workdir / "web快取" / "成品檢查"
    cache.mkdir(parents=True, exist_ok=True)
    dst = cache / f"{which}_{(log.get('產生時間') or '').replace(':', '')}_{a:.2f}_{b:.2f}.wav"
    if not dst.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(src),
                        str(dst)], check=True)
    return dst


def video_file(workdir: str | Path) -> Path:
    workdir = Path(workdir)
    _, check = _current(workdir)
    if not check.get("成品影片"):
        raise FileNotFoundError("還沒有成品影片（輸出/成品_*），先在第 4 步組裝")
    return workdir / check["成品影片"]
