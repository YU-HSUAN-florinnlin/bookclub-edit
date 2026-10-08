"""第 5 步「成品檢查」（09-29 宇軒：原本第 5 步逐筆覆核＋第 6 步整片檢查合成一步）。

畫面（`bookclub/web/finalcheck.js`）像第 3 步：左邊成品影片＋時間軸（AI 處理過的地方全部標出來，資料來自
`生成/處理紀錄.json`），右邊「目前這一筆」。最上面固定一行：沒列在時間軸上的地方＝原片沒動。

兩種模式：
- 逐筆看：每一筆切換「處理前／處理後」試聽，按「通過」或「退回重做」（要寫原因）
- 整片看：記錄實際播放過的時間區段（網頁只送 2 倍速以下連續播的，09-29 宇軒；取聯集），顯示「已經看過全片的 x%」；看到問題按一下就在目前時間建一筆退回重做

另外一區：處理紀錄的「未登記的變動」（聲音變了但沒有紀錄），要人一筆一筆確認（沒問題／退回重做）。

按「送回 AI 重做（N 筆）」寫進 `覆核/成品檢查.json` 的 `送回AI重做`，`bookclub redo list <工作區>` 列得出來。
**全部通過、而且整片看過 100% 才算檢查完**（09-18 宇軒：最後一定要有人完整看過整支）；10-08 宇軒放寬：沒檢查完也可以輸出，
但要先確認（`export_final(confirm=True)`），輸出紀錄記下當時還差什麼。

存檔：`覆核/成品檢查.json`（格式見 `docs/工作區格式.md`）。純函式為主（看過比例、退回清單、能不能輸出），
`bookclub/server.py` 只負責轉手。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from bookclub import proclog
from bookclub import workdir as wd

PASS, REDO = "通過", "退回重做"
UNLOGGED_OK = "沒問題"
REASSEMBLE_ONLY = "只重新組裝"   # 10-03 第八批 #23：第 4 步「只重新組裝」（不重新生成）的退回項目
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


PRINT_VERSION = 2            # 成品檢查檔 `指紋版本`：2＝content_print（10-03 第八批 #23）；沒寫的是舊指紋（legacy_print）
WATCH_CLEAR_PAD_S = 2.0      # 重新組裝後，內容變了的那幾筆成品範圍前後各多 2 秒裡看過的部分要重看


def content_print(r: dict) -> str:
    """處理紀錄一筆的**內容指紋**（10-03 第八批 #23；之後「修改需求紀錄」#90 沿用，純函式）。

    內容一樣＝成品這一段聽起來一樣，之前的通過／退回／看過照算；不一樣就要重看。含：
    - `做了什麼`：處理方式（學員重念、整句換掉、消音、剪掉⋯⋯與加快、停格的說明）
    - 檔案：用了哪個生成檔，看**檔案內容**（處理紀錄的 `檔案指紋`，`proclog.stamp_contents` 寫）——同一個路徑換了新檔也算變了；
      舊的處理紀錄沒有 `檔案指紋` 時退回用路徑
    - `原片` 起訖、`文字`（要念的字）、`停格秒`
    - 接縫做法版本（`接縫做法版本`，`assemble.SEAM_VERSION`）：只算換聲音類與停格（`proclog.SEAM_KINDS`）；
      舊的處理紀錄沒寫的當第 1 版。刪除、消音類不受接縫做法影響，不算進去
    **不含成品時間**：前面多剪一段、停格秒數變，後面每一筆的成品時間都位移，但聲音內容沒變，不算變了。"""
    import json

    kind = r.get("類型")
    seam = r.get("接縫做法版本", 1) if kind in proclog.SEAM_KINDS else None
    parts = {"類型": kind, "做了什麼": r.get("做了什麼"), "檔案": r.get("檔案指紋") or r.get("檔案"),
             "原片": r.get("原片"), "文字": r.get("文字"), "停格秒": r.get("停格秒"), "接縫": seam}
    return json.dumps(parts, ensure_ascii=False, sort_keys=True)


def record_print(r: dict) -> str:
    """這一筆的內容指紋（第 5 步逐筆結果用）：見 `content_print`。"""
    return content_print(r)


def legacy_print(r: dict) -> str:
    """10-03 以前的指紋（做了什麼＋檔案路徑＋成品時間＋文字）。只拿來認舊的成品檢查檔：對得上就換成新指紋。"""
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


def near_output_time(t: float, plist: list[dict] | None) -> float:
    """原片時間 → 成品時間；落在剪掉的範圍裡的，算剪點（後面那一段的開頭）。整片退回、拿掉的紀錄換算用（純函式）。"""
    if not plist:
        return t
    acc = 0.0
    for p in plist:
        s, e = p["src"]
        if t < s:
            return acc
        if t <= e:
            return acc + (t - s)
        acc += (e - s) + p["freeze"]
    return acc


def watched_to_source(ranges: list, plist: list[dict] | None) -> tuple[list[list[float]], list[float]]:
    """看過區段（成品時間）→（原片時間的區段, 整段看過的停格點）（10-03 第八批 #23，純函式）。
    跨過剪點的一段拆成好幾段原片；停格整段都看過才記那一個停格點（原片時間），只看了一部分的不記。"""
    ranges = merge_ranges(ranges)
    if not plist:
        return ranges, []
    src: list[list[float]] = []
    pts: list[float] = []
    eps = 1e-3
    for x, y in ranges:
        acc = 0.0
        for p in plist:
            s, e = p["src"]
            n = e - s
            lo, hi = max(x, acc), min(y, acc + n)
            if hi > lo:
                src.append([s + (lo - acc), s + (hi - acc)])
            acc += n
            if p["freeze"]:
                if x <= acc + eps and y >= acc + p["freeze"] - eps:
                    pts.append(round(e, 3))
                acc += p["freeze"]
    return merge_ranges(src), sorted(set(pts))


def watched_from_source(src: list, points: list, plist: list[dict] | None) -> list[list[float]]:
    """原片時間的看過區段＋看過的停格點 → 新片段表的成品時間（10-03 第八批 #23，純函式）。
    原片被剪掉的部分不會出現；新的停格只有記過那一點（前後 1 毫秒內）才算看過。"""
    if not plist:
        return merge_ranges(src)
    out: list[list[float]] = []
    acc = 0.0
    for p in plist:
        s, e = p["src"]
        for a, b in src:
            lo, hi = max(a, s), min(b, e)
            if hi > lo:
                out.append([acc + (lo - s), acc + (hi - s)])
        acc += e - s
        if p["freeze"]:
            if any(abs(e - q) <= 1e-3 for q in points or []):
                out.append([acc, acc + p["freeze"]])
            acc += p["freeze"]
    return merge_ranges(out)


def subtract_ranges(ranges: list, holes: list) -> list[list[float]]:
    """區段扣掉另一組區段（純函式）。"""
    out = merge_ranges(ranges)
    for a, b in merge_ranges(holes):
        nxt = []
        for s, e in out:
            if e <= a or s >= b:
                nxt.append([s, e])
                continue
            if s < a:
                nxt.append([s, a])
            if e > b:
                nxt.append([b, e])
        out = nxt
    return [[round(s, 3), round(e, 3)] for s, e in out if e > s]


def _set_watched(check: dict, ranges: list, plist: list[dict] | None) -> None:
    """看過區段（成品時間）跟原片時間的那一份一起存（重新組裝後用原片時間換回新的成品時間）。"""
    check["看過區段"] = merge_ranges(ranges)
    src, pts = watched_to_source(check["看過區段"], plist)
    check["看過區段原片"] = [[round(a, 3), round(b, 3)] for a, b in src]
    check["看過停格原片"] = pts


def snapshot(log: dict | None) -> dict:
    """處理紀錄每一筆的 {鍵: {指紋, 原片}}：下一次重新組裝時比對哪幾筆內容變了、哪幾筆不見了。"""
    return {record_key(r): {"指紋": content_print(r), "原片": r.get("原片")} for r in (log or {}).get("紀錄", [])}


def changed_windows(log: dict, old: dict) -> list[list[float]]:
    """重新組裝後，內容變了（或新出現、不見了）的那幾筆的成品範圍，前後各多 2 秒（純函式）。
    `old` 是上一份處理紀錄的 `snapshot`。不見了的那一筆用它的原片時間換到新的成品時間。"""
    plist = log.get("片段")
    pad = WATCH_CLEAR_PAD_S
    out: list[list[float]] = []
    now = set()
    for r in log.get("紀錄", []):
        k = record_key(r)
        now.add(k)
        if (old.get(k) or {}).get("指紋") == content_print(r):
            continue
        c = r.get("成品") or [None, None]
        if c[0] is not None and c[1] is not None:
            out.append([c[0] - pad, c[1] + pad])
        elif r.get("原片"):
            t = near_output_time(r["原片"][0], plist)
            out.append([t - pad, t + pad])
    for k, v in old.items():
        if k in now or not v.get("原片"):
            continue
        a, b = (near_output_time(t, plist) for t in v["原片"])
        out.append([a - pad, b + pad])
    return merge_ranges([[max(0.0, a), b] for a, b in out])


def needs_look(r: dict, d: dict | None = None) -> bool:
    """第 5 步「只看要人聽的」（10-02 第六批）：生成檢查沒過、放不進時間格（標紅）——處理紀錄的 `要人聽` 就是這兩種；
    另外「名字要人處理」（原片沒動）本來就要人看。"""
    return bool(r.get("要人聽")) or r.get("類型") == "名字要人處理"


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
    # 10-04 #113：同一張卡（同一處、同一句同代號，併進主卡的候選）的好幾處只算一個名字，改的是那張主卡；
    # 真的是好幾張不同的卡才算「好幾個名字」，而且每張卡只列一次
    by_card: dict[str, str] = {}
    for k in names:
        by_card.setdefault((index.get(k) or {}).get("第3步") or k, k)
    if len(by_card) == 1:
        card, k0 = next(iter(by_card.items()))
        names = [card if editable(card) else k0]
    ovs = [k for k in keys if k.startswith("重疊:") or (k.startswith("學員段落:") and (index.get(k) or {}).get("類型") == "重疊")]
    if kind == "停格":
        return no("停格是自動加的：重念的聲音比原本長，畫面停一下補長，不能單獨改範圍", f"{go}改範圍或要念的字")
    if kind in ("學員名字消音", "學員名字換代號"):
        return no("學員提到名字的範圍照逐字稿的字自動抓，第 3 步也沒有改時間", f"{go}改做法（直接消音或換成代號）")
    if kind == "模糊示範":
        return no("畫面模糊是示範用的，沒有範圍可以改", "不用改")
    if kind in ("學員空隙消音", "學員空隙保留原聲"):   # 10-03 第八批補修 #102
        return no("學員段落裡兩格之間的空隙是組裝時自動處理的，沒有自己的範圍可以改", f"{go}改學員段落的範圍或要念的字")
    if kind == "名字要人處理":
        return no("這一筆沒有自動處理（原片沒動）", f"{go}把要重念的句子改好（名字寫成代號），或改成直接消音")
    if ovs and any((index.get(k) or {}).get("疊放") for k in ovs):
        return no("這一處重疊選了兩邊都重新生成：老師和學員各有自己的起訖", f"{go}的「改做法」裡改兩邊各自的起訖")
    if kind in ("名字整句換掉", "換聲音"):
        if len(by_card) > 1:
            return no("這一句同時換掉好幾個名字（" + "、".join(f"〈{item_name(index, c if c in index else k)}〉"
                                                       for c, k in by_card.items())
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
    if kind == "學員重念" and target and target.get("學員生成"):
        # 10-01 第三批：重疊選生成學員聲音（不在學員段落裡）：改的是「會換掉的範圍」（學員那一整句），不是重疊本身
        a, b = target["學員生成"]
        return yes(target, "學員起訖", a, b)
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
    # 10-01 第三批 7、8：統一叫「老師重念」「學員重念」「消音」，「霧化」寫成「聲音霧化」（只改顯示，紀錄檔不改，
    # 不然處理紀錄的內容指紋會變、第 5 步已經通過的全部要重看）
    text = text.replace("老師提到名字：整句用老師 AI 聲音重念、名字換成代號", "老師重念：整句重念、名字換成代號")
    text = text.replace("老師提到名字：用老師 AI 聲音重念、名字換成代號", "老師重念：名字換成代號")
    text = text.replace("老師整句用 AI 聲音重念", "老師整句老師重念")
    text = re.sub(r"^(.*?) 用(男|女)?聲 AI 重念", lambda m: f"學員重念：{m.group(1)}" + (f"（{m.group(2)}聲的替代聲音）" if m.group(2) else "（替代聲音）"), text)
    text = text.replace("第 3 步標的局部消音", "第 3 步標的消音").replace("局部消音", "消音")
    text = re.sub(r"(?<!聲音)霧化", "聲音霧化", text)
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


def upgrade_prints(check: dict, log: dict | None) -> None:
    """舊的成品檢查檔（10-03 以前，指紋含成品時間）：照舊指紋跟這份處理紀錄比一次，對得上就換成新指紋（直接改傳進來的）。
    對不上的留著舊指紋（之後重新組裝時會被當成內容變了）。重做中、重做過記的指紋也一起換。"""
    if check.get("指紋版本") == PRINT_VERSION or not log:
        return
    by_key = {record_key(r): r for r in log.get("紀錄", [])}

    def up(key: str, v: dict) -> None:
        r = by_key.get(key)
        if r is not None and v.get("指紋") is not None and v["指紋"] == legacy_print(r):
            v["指紋"] = content_print(r)

    for k, v in check.get("逐筆", {}).items():
        up(k, v)
    for part in ("重做中", "重做過"):
        for e in (check.get(part) or {}).get("項目") or []:
            up(e.get("鍵"), e)
    check["指紋版本"] = PRINT_VERSION


def refresh(check: dict, log: dict | None) -> dict:
    """處理紀錄重寫過（重新組裝）時整理成品檢查（回傳整理過的 check，不改傳進來的）。10-03 第八批 #23 起：
    - 逐筆：內容指紋（`content_print`，不含成品時間）變了的那幾筆，之前的通過／退回不算數；其他保留
    - 看過區段：用存著的原片時間（`看過區段原片`、`看過停格原片`）換到新的成品時間，只扣掉內容變了（含新出現、不見了）
      的那幾筆成品範圍前後各 2 秒；舊檔沒有原片時間的，這一次歸零（只會發生一次）
    - 整片退回：照存著的原片秒保留，成品秒用新的片段表重算（送回重做、組裝做完才拿掉，見 `finish_redo`）
    處理紀錄沒重寫時：只補新格式要的欄位（舊指紋換新、看過區段補原片時間、處理紀錄快照）。"""
    import copy

    check = copy.deepcopy(check)
    if not log:
        return check
    plist = log.get("片段")
    stamp = log.get("產生時間")
    if check.get("處理紀錄產生時間") == stamp:
        upgrade_prints(check, log)   # 這份處理紀錄就是舊指紋當時的那一份
        if "看過區段原片" not in check:
            _set_watched(check, check.get("看過區段", []), plist)
        check.setdefault("處理紀錄快照", snapshot(log))
        return check
    upgrade_prints(check, log)   # 處理紀錄已經換了：舊指紋（含成品時間）只有完全沒位移的對得上
    prints = {record_key(r): content_print(r) for r in log.get("紀錄", [])}
    check["逐筆"] = {k: v for k, v in check.get("逐筆", {}).items() if prints.get(k) == v.get("指紋")}
    keep_un = {unlogged_key(u) for u in log.get("未登記的變動", [])}
    check["未登記確認"] = {k: v for k, v in check.get("未登記確認", {}).items() if k in keep_un}
    if check.get("處理紀錄產生時間"):
        old = check.get("處理紀錄快照")
        if old is None or "看過區段原片" not in check:
            _set_watched(check, [], plist)
        else:
            ranges = watched_from_source(check["看過區段原片"], check.get("看過停格原片") or [], plist)
            _set_watched(check, subtract_ranges(ranges, changed_windows(log, old)), plist)
        for x in check.get("整片退回", []):
            if x.get("原片秒") is not None:
                x["成品秒"] = round(near_output_time(float(x["原片秒"]), plist), 3)
    check["處理紀錄快照"] = snapshot(log)
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
    plist = (log or {}).get("片段")
    if check.get("成品影片") not in prods:
        check["成品影片"] = prods[0] if prods else None
        _set_watched(check, [], plist)
    if check["成品影片"]:
        # 10-02 第七批（C2）：重新組裝後檔名一樣、長度變了 → 成品檔換新（大小或修改時間不同）或處理紀錄換新就重新量；
        # 看過的區段超出新長度的截掉（以前只在沒量過時量一次，「整片看過幾 %」一直用舊長度算）
        fp = product_print(Path(workdir) / check["成品影片"])
        stamp = (log or {}).get("產生時間")
        if not check.get("成品長度") or check.get("成品檔指紋") != fp or check.get("量長度時的處理紀錄") != stamp:
            length = round(probe_duration(Path(workdir) / check["成品影片"]), 3)
            check.update({"成品長度": length, "成品檔指紋": fp, "量長度時的處理紀錄": stamp})
            _set_watched(check, clip_ranges(check["看過區段"], length), plist)
    return log, check


def product_print(path: Path) -> list | None:
    """成品檔的指紋：[大小, 修改時間（奈秒）]；讀不到回 None。"""
    try:
        st = path.stat()
    except OSError:
        return None
    return [st.st_size, st.st_mtime_ns]


def clip_ranges(ranges: list, total: float) -> list:
    """看過的區段截到 [0, total]（純函式）：整段超出的拿掉、跨過的截掉。"""
    return [[a, min(b, total)] for a, b in ranges if a < total and total > 0]


_GEN_FILE_RE = None


def gen_id(r: dict) -> str | None:
    """10-04 #128：處理紀錄一筆用的生成檔是哪一句（`T034_13`、`SNM001`⋯），給第 5 步畫面小字顯示、跟 AI 助手溝通用（純函式）。
    處理紀錄本身沒存生成編號，從生成檔的檔名取（檔名是程式取的：`<編號>.wav`、`<編號>_第2次.wav`、`<編號>_放回時間格.wav`）；
    取出來的還要過 `safeview.safe_id`（長得像程式編號才給）。沒有生成檔、對不上就回 None。不影響任何判斷。"""
    import re

    from bookclub.safeview import safe_id

    global _GEN_FILE_RE
    if _GEN_FILE_RE is None:
        _GEN_FILE_RE = re.compile(r"^([A-Z]{1,4}\d+(?:m\d+)*(?:_\d+)?)(?=[_.]|$)")
    f = r.get("檔案")
    if not isinstance(f, str) or not f:
        return None
    m = _GEN_FILE_RE.match(Path(f).name)
    return m.group(1) if m and safe_id(m.group(1)) else None


# ---------- 第 5 步下方清單分兩區（10-08 宇軒）：「有修改的」依類型分組＋「第 3 步有卡片但選定不修改」 ----------

# 「有修改的」每一組：(組, 畫面上的名稱, 一行說明)。依這個順序顯示；處理紀錄的 `類型` 怎麼歸組見 `change_group`
CHANGE_GROUPS = (
    ("學員重念", "學員段落：AI 重念", "學員的話用 AI 聲音重念（加快、結尾停格、結尾切掉都寫在這一筆裡）"),
    ("學員空隙", "學員段落：兩格之間的空隙", "第 4 步組裝時處理的：兩格重念之間的空隙墊底噪；空隙裡有別的聲音的照原聲留著、要人聽"),
    ("名字重念", "老師講到名字：整句重念", "老師講到名字，整句用老師 AI 聲音重念、名字換成代號"),
    ("名字消音", "老師講到名字：消音", "老師講到名字，名字那幾個字消音（墊環境底噪）"),
    ("名字要人處理", "名字：沒有自動處理", "程式換不了、原片沒動，要人看"),
    ("學員名字", "保留原聲的學員講到名字", "保留原聲的學員講到名字：消音，或用他自己的聲音重念這句"),
    ("重疊", "聲音重疊", "老師和學員聲音疊在一起的地方：重念、消音，或只標出來"),
    ("剪掉", "剪掉（連畫面）", "聲音和畫面一起拿掉，影片變短"),
    ("消音", "消音（第 3 步標的）", "第 3 步手動標的局部消音（墊環境底噪）"),
    ("停格", "停格（第 4 步組裝時加的）", "重念比原本長，畫面停一下補長"),
    ("其他", "其他", "模糊示範等"),
)
_GROUP_OF_KIND = {"學員重念": "學員重念", "學員空隙消音": "學員空隙", "學員空隙保留原聲": "學員空隙",
                  "名字整句換掉": "名字重念", "換聲音": "名字重念", "名字消音": "名字消音", "消音": "名字消音",
                  "名字要人處理": "名字要人處理", "學員名字消音": "學員名字", "學員名字換代號": "學員名字",
                  "重疊": "重疊", "刪除": "剪掉", "局部消音": "消音", "停格": "停格"}


def change_group(r: dict, index: dict | None = None) -> str:
    """處理紀錄一筆 → 「有修改的」哪一組（純函式）。對到第 3 步重疊卡片的（`覆核項目` 第一個是 `重疊:`，
    或重疊卡片自己生成的學員那一句）歸「重疊」，一筆只列一次（照 `覆核項目` 第一個）。"""
    kind = r.get("類型")
    first = (r.get("覆核項目") or [""])[0]
    if kind in ("局部消音", "名字整句換掉", "換聲音", "學員重念") and str(first).startswith("重疊:"):
        return "重疊"
    if kind == "學員重念" and index and (index.get(first) or {}).get("類型") == "重疊":
        return "重疊"
    return _GROUP_OF_KIND.get(kind, "其他")


# 「第 3 步有卡片但選定不修改」每一種：(子類, 畫面上的名稱, 回第 3 步怎麼改)
UNCHANGED_KINDS = (
    ("改成老師", "改成老師的段落（照原聲）", "要改回學員、改時間或改成老師重念，在這張卡片的「改做法」裡選"),
    ("保留原聲", "保留原聲的學員段落", "要改成 AI 重念：在「開始前 4 件事」的學員聲音改成「重新生成」"),
    ("重疊不用改", "聲音重疊：選了「不用改」", "要處理就在這張卡片換做法"),
    ("名字略過", "老師講到名字：標成「不是名字」或「是地名」", "其實是名字的話，把標記拿掉"),
    ("學員名字略過", "保留原聲的學員講到名字：標成「不是名字」或「是地名」", "其實是名字的話，把標記拿掉"),
    ("不剪", "建議剪掉：選了「不剪」", "要剪掉：在「設定」的「已還原的」救回"),
    ("剪掉還原", "手動剪掉、後來還原的", "要剪掉：在「設定」的「已還原的」救回"),
    ("消音還原", "手動消音、後來還原的", "要消音：在「設定」的「已還原的」救回"),
    ("段落外老師", "段落外面那幾秒：答「老師的話，不用處理」", "答錯了：在這張學員段落卡片的「切在段落外面的這幾秒」改答案"),
    ("人名不處理", "名字清單：選「不用處理」或「不是名字」", "要換成代號：在「開始前 4 件事」的 ③ 改"),
)
UNCHANGED_MIN_S = 0.3        # 扣掉被別筆處理動到的地方之後剩不到 0.3 秒就不列（整段都改了、剪掉了）


def _unchanged_sources(workdir: Path) -> list[dict]:
    """第 3 步有卡片但選定不修改的（原片時間，還沒扣掉被動到的地方）：[{鍵, 子類, 名稱, start, end, 第3步, 處數?}]。
    只讀檔，不寫任何檔；每一種各自算，哪一種讀不到就跳過那一種（不擋第 5 步）。"""
    from bookclub import review

    workdir = Path(workdir)
    rows: list[dict] = []

    def add(kind: str, key: str, name: str, a, b, card: str | None, **extra) -> None:
        try:
            a, b = float(a), float(b)
        except (TypeError, ValueError):
            return
        if b > a:
            rows.append({"鍵": key, "子類": kind, "名稱": name, "start": round(a, 3), "end": round(b, 3), "第3步": card, **extra})

    def safe(fn) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001 — 某一種算不出來就不列那一種
            pass

    dec = review.load_decisions(workdir)
    kept = {k for k, v in (dec.get("學員聲音") or {}).items() if v == "保留原聲"}

    def turns() -> None:
        from bookclub import turns as turns_mod

        data = wd.read_json(turns_mod.turns_path(workdir), default=None) or {}
        for t in data.get("段落", []):
            if t.get("說話者") == "老師" and t.get("說話者是人改的"):
                add("改成老師", f"不修改:改成老師:{t['id']}", f"改成老師 {wd.fmt_time(t['start'])}", t["start"], t["end"],
                    f"改成老師:{t['id']}")
            elif t.get("說話者") in kept:
                add("保留原聲", f"不修改:保留原聲:{t['id']}", f"學員段落 {wd.fmt_time(t['start'])}", t["start"], t["end"],
                    f"學員段落:{t['id']}", 學員=t.get("說話者"))

    def overlaps() -> None:
        for o in review.overlap_choices(workdir):
            if o.get("做法") == "不用改":
                add("重疊不用改", f"不修改:重疊:{o['id']}", f"重疊 {wd.fmt_time(o['start'])}", o["start"], o["end"], f"重疊:{o['id']}")

    def names() -> None:
        from bookclub.nameplan import SKIP_TAGS

        data = wd.read_json(wd.names_path(workdir), default=None) or {}
        ndec = wd.read_json(review.name_decisions_path(workdir), default={}) or {}
        cands = review.effective_name_candidates(workdir, data.get("candidates", []), ndec)
        decs = review.card_decisions(cands, ndec)
        cards = review.card_of(cands)
        seen: dict[str, dict] = {}
        for i, c in enumerate(cands, start=1):
            cid = str(c.get("id") or i)
            if c.get("同一處") or not set((decs.get(cid) or {}).get("tags") or []) & SKIP_TAGS:
                continue
            card = cards.get(cid, cid)
            if card in seen:   # 同一張卡片（同一句同代號）的另一處：併成一列
                r = seen[card]
                r["start"], r["end"] = min(r["start"], round(float(c["start"]), 3)), max(r["end"], round(float(c["end"]), 3))
                r["處數"] += 1
                continue
            n = len(rows)
            add("名字略過", f"不修改:名字:{card}", f"老師提到名字 {wd.fmt_time(c['start'])}", c["start"], c["end"],
                f"名字:{card}", 處數=1)
            if len(rows) > n:
                seen[card] = rows[-1]

    def student_names() -> None:
        from bookclub import studentnames

        cands = (wd.read_json(studentnames.cands_path(workdir), default=None) or {}).get("candidates", [])
        sdec = wd.read_json(studentnames.decisions_path(workdir), default={}) or {}
        for c in cands:
            if c.get("學員") not in kept:
                continue
            if set((sdec.get(c["id"]) or {}).get("tags") or []) & studentnames.SKIP_TAGS:
                add("學員名字略過", f"不修改:學員名字:{c['id']}", f"學員提到名字 {wd.fmt_time(c['start'])}", c["start"], c["end"],
                    f"學員名字:{c['id']}")

    def restored() -> None:
        linked = {c["建議id"]: c for c in dec["刪除段落"] if c.get("建議id")}
        for sg in review.load_cut_suggestions(workdir):
            if (dec["刪除建議"].get(sg["id"]) or {}).get("決定") == "不刪":
                c = linked.get(sg["id"], {})
                add("不剪", f"不修改:還原:{sg['id']}", f"建議剪掉 {wd.fmt_time(c.get('start', sg['start']))}",
                    c.get("start", sg["start"]), c.get("end", sg["end"]), None)
        for c in dec["刪除段落"]:
            if not c.get("建議id") and c.get("狀態") == "還原":
                add("剪掉還原", f"不修改:還原:{c['id']}", f"剪掉 {wd.fmt_time(c['start'])}", c["start"], c["end"], None)
        for m in dec["局部消音"]:
            if m.get("狀態") == "還原":
                add("消音還原", f"不修改:還原:{m['id']}", f"消音 {wd.fmt_time(m['start'])}", m["start"], m["end"], None)

    def outside() -> None:
        from bookclub.execute import OUT_A

        for k, a in (dec.get("段落外答案") or {}).items():
            if (a or {}).get("答案") == OUT_A and str(k).startswith("段落外:"):
                tid = a.get("段落") or str(k).split(":")[1]
                add("段落外老師", str(k), f"學員段落外面 {wd.fmt_time(a['start'])}", a["start"], a["end"], f"學員段落:{tid}")

    def people() -> None:
        from bookclub import personnames

        pdec = wd.read_json(personnames.decisions_path(workdir), default={}) or {}
        skip = {n for n, d in pdec.items() if (d or {}).get("做法") in ("不是名字", "不用處理")}
        if not skip:
            return
        sents = {s["id"]: s for s in (wd.read_json(wd.speakers_path(workdir), default={}) or {}).get("sentences", [])}
        for k, p in enumerate((wd.read_json(personnames.people_path(workdir), default=None) or {}).get("人名", []), start=1):
            if p.get("名字") not in skip:
                continue
            ss = sorted((sents[i] for i in p.get("句子") or [] if i in sents), key=lambda s: s["start"])
            if ss:   # 有句子編號的才有時間；一個名字一列，時間是第一次出現的那一句
                add("人名不處理", f"不修改:人名:{p.get('id') or k}", p["名字"], ss[0]["start"], ss[0]["end"], None,
                    處數=len(ss), 做法=pdec[p["名字"]].get("做法"))

    for fn in (turns, overlaps, names, student_names, restored, outside, people):
        safe(fn)
    return rows


def place_unchanged(rows: list[dict], log: dict | None) -> list[dict]:
    """不修改的每一列換成成品時間（純函式）：扣掉處理紀錄會動到聲音的範圍（剪掉、重念、消音…），
    剩不到 0.3 秒的不列；超出這次組裝範圍的不列。回傳每一列多 `原片`、`剩下`、`剩下秒`、`成品`（剩下的第一段起點～最後一段終點）。"""
    if not log:
        return []
    plist = log.get("片段")
    holes = [list(x) for x in proclog.audio_spans(log.get("紀錄", []))]
    rng = log.get("範圍")
    order = {k: i for i, (k, _n, _g) in enumerate(UNCHANGED_KINDS)}
    out = []
    for r in rows:
        left = [[r["start"], r["end"]]]
        if rng:
            left = [[max(a, rng[0]), min(b, rng[1])] for a, b in left if min(b, rng[1]) > max(a, rng[0])]
        left = subtract_ranges(left, holes)
        secs = round(sum(b - a for a, b in left), 3)
        if secs < UNCHANGED_MIN_S:
            continue
        a, b = near_output_time(left[0][0], plist), near_output_time(left[-1][1], plist)
        out.append({**r, "原片": [r["start"], r["end"]], "剩下": left, "剩下秒": secs, "成品": [round(a, 3), round(b, 3)]})
    out.sort(key=lambda x: (order.get(x["子類"], 99), x["start"]))
    return out


def unchanged_rows(workdir: str | Path, log: dict | None) -> list[dict]:
    """`GET /api/final` 的「不修改」：第 3 步有卡片但選定不修改的，換成成品時間（不擋輸出、不用按通過）。"""
    if not log:
        return []
    try:
        return place_unchanged(_unchanged_sources(Path(workdir)), log)
    except Exception:  # noqa: BLE001 — 算不出來不擋第 5 步
        return []


def group_counts(log: dict | None, index: dict | None = None) -> dict[str, int]:
    """處理紀錄每一組幾筆（inspect 用，純函式）。"""
    out: dict[str, int] = {}
    for r in (log or {}).get("紀錄", []):
        g = change_group(r, index)
        out[g] = out.get(g, 0) + 1
    return out


def page_data(workdir: str | Path) -> dict:
    """`GET /api/final`：成品檢查頁一次要的全部資料。"""
    workdir = Path(workdir)
    with _lock:
        log, check = _current(workdir)
        _save(workdir, check)
    plist = (log or {}).get("片段")
    index = _index(workdir)
    try:   # 10-02 第六批：老師重念範圍前後沒有人講話的那幾筆，卡片上提醒＋「照建議縮小」
        from bookclub import silentedge

        edge = silentedge.hints(workdir) if log else {}
    except Exception:  # noqa: BLE001 — 算不出來不擋第 5 步
        edge = {}
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
                     "改範圍": tgt, "已改範圍": done,
                     "重做過": redone_info(r, check, log),   # 10-01 第三批：上一次「只重做退回的」重做過的
                     "生成編號": gen_id(r)})   # 10-04 #128：畫面小字顯示（跟 inspect 一樣的編號）
        hit = next((edge[k] for k in r.get("覆核項目") or [] if k in edge), None) if r["類型"] in ("名字整句換掉", "換聲音") else None
        if hit:
            recs[-1]["前後沒聲音"] = hit
        recs[-1]["要人看"] = needs_look(r, d)   # 10-02 第六批：「只看要人聽的」篩選
        recs[-1]["組"] = change_group(r, index)   # 10-08：下方「有修改的」依類型分組
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
        "重做中": check.get("重做中"),   # 10-01 第三批：第 4 步正在（或上次沒做完）重做退回的
        "重做過": check.get("重做過") if (check.get("重做過") or {}).get("處理紀錄產生時間") == (log or {}).get("產生時間") else None,
        "狀態": status(log, check),
        # 10-08 宇軒：下方清單分兩區——「有修改的」依類型分組（組的順序、名稱）、「第 3 步有卡片但選定不修改」（不擋輸出）
        "修改類型": [{"組": g, "名稱": n, "說明": x} for g, n, x in CHANGE_GROUPS],
        "不修改類型": [{"子類": k, "名稱": n, "去改": x} for k, n, x in UNCHANGED_KINDS],
        "不修改": unchanged_rows(workdir, log),
        "學員顯示名": _student_labels(workdir),   # 10-07：畫面上「學員3」換成「本名（代號）」（只在畫面換，紀錄檔不寫本名）
    }


def _student_labels(workdir: Path) -> dict[str, str]:
    try:
        from bookclub import epcodes

        return epcodes.student_labels(workdir)
    except Exception:  # noqa: BLE001 — 算不出來就照舊顯示「學員 N」
        return {}


def choose_product(workdir: str | Path, rel: str) -> dict:
    workdir = Path(workdir)
    if rel not in products(workdir):
        raise ValueError(f"不是這個工作區的成品影片：{rel}")
    with _lock:
        log, check = _current(workdir)
        if check["成品影片"] != rel:
            check.update({"成品影片": rel, "成品長度": round(probe_duration(workdir / rel), 3),
                          "成品檔指紋": product_print(workdir / rel), "量長度時的處理紀錄": (log or {}).get("產生時間")})
            _set_watched(check, [], (log or {}).get("片段"))
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
        # 10-03 第八批 #23：同時存原片時間（用現在這份處理紀錄的片段表換），重新組裝後換得回新的成品時間
        _set_watched(check, check["看過區段"] + [[float(a), float(b)] for a, b in ranges], (log or {}).get("片段"))
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


def final_name(src_name: str, stamp: str) -> str:
    """最終成品的檔名（純函式）：`成品_0-98_sw.mp4` → `最終成品_0-98_sw_20261008-153012.mp4`。"""
    p = Path(src_name)
    return f"最終成品_{p.stem.removeprefix('成品_')}_{stamp}{p.suffix}"


def export_gaps(st: dict) -> dict:
    """輸出時「還差什麼」（純函式，10-08 宇軒放寬輸出）：{看過比例, 看過百分比, 沒通過, 退回, 沒確認的變動, 說明[]}。
    說明是給確認視窗的白話句子；都達標時說明是空的。`沒通過` 含退回重做的（退回的另外也數）。"""
    pct = int(st["看過比例"] * 100)
    left = st["逐筆"]["總數"] - st["逐筆"]["通過"]
    un = st["未登記"]["總數"] - st["未登記"]["沒問題"]
    why = []
    if st["看過比例"] < 1.0:
        why.append(f"整片還沒看完（看過 {pct}%）")
    if left:
        why.append(f"還有 {left} 筆沒通過" + (f"（其中 {st['逐筆']['退回']} 筆是退回重做、還沒重做）" if st["逐筆"]["退回"] else ""))
    if un:
        why.append(f"還有 {un} 處沒登記的變動沒確認")
    if st.get("退回數") and not st["逐筆"]["退回"]:
        why.append(f"有 {st['退回數']} 處退回重做還沒重做")
    return {"看過比例": st["看過比例"], "看過百分比": pct, "沒通過": left, "退回": st["逐筆"]["退回"],
            "沒確認的變動": un, "說明": why, "擋下": [], "退回清單": [], "提醒": []}


PRIVACY_PREFIXES = ("名字:", "學員名字:", "重疊:")   # 退回重做的這幾類跟名字、重疊有關：沒重做不能輸出


def export_blocks(log: dict | None, check: dict, product: str | None) -> dict:
    """輸出前一定要先處理、確認了也不能輸出的（純函式，10-08 審查）：
    - 「名字要人處理」（程式換不了代號、原片名字還在原聲裡）沒按通過的
    - 退回重做、對應名字或重疊的（覆核項目是 名字:／學員名字:／重疊:）還沒重做的
    - 檢查的是「標字版」（給人看 AI 改了哪裡用的，畫面上有字）
    另外回：其他退回重做的每一筆（時間、原因，只顯示）、第 4 步正在重做的提醒。
    回傳 {擋下: [白話句子], 退回清單: [{成品秒, 原片秒, 原因, 來源}], 提醒: [白話句子]}。"""
    recs = (log or {}).get("紀錄", [])
    items = check.get("逐筆", {})
    block, others, note = [], [], []
    manual = [r for r in recs if r.get("類型") == "名字要人處理" and items.get(record_key(r), {}).get("結果") != PASS]
    if manual:
        block.append(f"還有 {len(manual)} 處名字程式沒處理、原片沒動（名字還在原聲裡），要先在清單裡看過、按通過")
    privacy = 0
    for x in redo_items(log, check):
        if any(str(k).startswith(PRIVACY_PREFIXES) for k in x.get("覆核項目") or []):
            privacy += 1
            continue
        out = (x.get("成品") or [None])[0]
        src = (x.get("原片") or [None])[0]
        others.append({"成品秒": out, "原片秒": src, "原因": x.get("原因", ""), "來源": x.get("來源")})
    if privacy:
        block.append(f"有 {privacy} 筆退回重做的跟名字或聲音重疊有關，還沒重做：要先送回 AI 重做、重新組裝")
    if product and "標字版" in Path(product).stem:
        block.append("現在檢查的是「標字版」（畫面上標了 AI 改了哪裡，給人對照用），不能當成品輸出：在影片上方「檢查哪一支」換成正式的成品")
    if check.get("重做中"):
        note.append("第 4 步正在重做退回的那幾筆（或上次沒做完）：現在輸出的是重做之前的版本")
    return {"擋下": block, "退回清單": others, "提醒": note}


def export_final(workdir: str | Path, confirm: bool = False) -> dict:
    """`POST /api/final/export`：把檢查用的那一支成品複製成 `輸出/最終成品_<檔名>_<時間>.mp4`。
    10-08 宇軒：輸出可以做很多次（片頭片尾、前面的步驟可能選錯，要重新設定再輸出）。每次產生一支新的、檔名帶時間，
    不蓋掉舊的；`輸出成品` 記最新一次，`輸出紀錄` 記每一次（含當時選的片頭片尾，這一版只記錄、還沒接上）。
    10-08 宇軒放寬：沒全部通過、沒看完也可以輸出，但要確認——還沒達標又沒帶 `confirm` 時不輸出，回傳
    {要確認: True, 還差: export_gaps}；帶 `confirm` 就照目前的狀態輸出，輸出紀錄記下當時看過幾 %、幾筆沒通過、幾處變動沒確認。
    還沒有處理紀錄或成品影片照樣不能輸出；`export_blocks` 的幾種（程式處理不了的名字沒按通過、名字／重疊退回沒重做、標字版）
    確認了也不能輸出。第 4 步正在重做時也要確認（輸出的是重做前的版本；第 4 步換成品檔是先寫暫存檔再換名，不會拿到半支）。"""
    from datetime import datetime

    workdir = Path(workdir)
    with _lock:
        log, check = _current(workdir)
        st = status(log, check)
        if not log or not check.get("成品影片"):
            raise ValueError("還不能輸出：" + ("還沒有處理紀錄（第 4 步還沒組裝）" if not log else "找不到成品影片"))
        gaps = {**export_gaps(st), **export_blocks(log, check, check.get("成品影片"))}
        if gaps["擋下"]:   # 10-08 審查：名字沒處理、名字／重疊退回沒重做、標字版：確認了也不能輸出
            if confirm:
                raise ValueError("還不能輸出：" + "；".join(gaps["擋下"]))
            return {"ok": False, "要確認": True, "不能輸出": True, "還差": gaps}
        if (not st["可以輸出"] or gaps["提醒"]) and not confirm:
            return {"ok": False, "要確認": True, "還差": gaps}
        src = workdir / check["成品影片"]
        dst = src.with_name(final_name(src.name, datetime.now().strftime("%Y%m%d-%H%M%S")))
        n = 2
        while dst.exists() or dst.is_symlink():   # 同一秒按兩次：加編號，一樣不蓋掉
            dst = src.with_name(f"{Path(final_name(src.name, datetime.now().strftime('%Y%m%d-%H%M%S'))).stem} ({n}){src.suffix}")
            n += 1
        from bookclub.fileio import cleanup_partials

        # 10-08：上次輸出到一半斷掉（伺服器被關掉）留下、超過 1 小時沒動的 `.最終成品_….輸出中` 先清掉
        cleanup_partials(dst.parent, suffix=".輸出中")
        tmp = dst.with_name(f".{dst.name}.輸出中")
        shutil.copy2(src, tmp)   # 先複製到暫存檔、完整了才換上（複製到一半中斷不會留下半個最終成品）
        os.replace(tmp, dst)
        extras = wd.read_json(workdir / "工作區設定.json", default=None) or {}
        rec = {"時間": _now(), "來源": check["成品影片"], "檔案": str(dst.relative_to(workdir)),
               "片頭": (extras.get("片頭") or {}).get("檔名"), "片尾": (extras.get("片尾") or {}).get("檔名"),
               "接上片頭片尾": False,
               # 10-08：輸出當時的檢查狀態（沒看完、沒全部通過也能輸出，第 5 步會提示「這次輸出時還沒看完」）
               "看過百分比": gaps["看過百分比"], "沒通過筆數": gaps["沒通過"], "沒確認變動筆數": gaps["沒確認的變動"],
               "檢查完才輸出": st["可以輸出"]}
        check["輸出成品"] = rec
        check.setdefault("輸出紀錄", []).append(rec)
        _save(workdir, check)
        return {"ok": True, **rec, "第幾次": len(check["輸出紀錄"])}


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
    ctx = _redo_ctx(workdir) if items else {}
    logs: dict = {}
    doing_items = (check.get("重做中") or {}).get("項目") or []
    doing_keys = {e["鍵"] for e in doing_items if not e.get(REASSEMBLE_ONLY)}
    doing_only = {e["鍵"] for e in doing_items if e.get(REASSEMBLE_ONLY)}   # 10-03：上次「只重新組裝」沒做完的
    pending = set(redo_pending(workdir, ctx=ctx)) if doing_keys else set()
    for it in items:
        it["建議指令"] = suggest_command(workdir, it)
        it["做了什麼"] = plain_ids(it.get("做了什麼") or "", index, workdir)   # 10-01 走查：第 4 步也不露內部編號
        # 10-01 第三批：按「開始執行」這一筆會怎麼重做（重新生成哪幾句，或只重新組裝）
        it["生成"] = redo_units(it, ctx)
        it["做法"] = "重新生成" if it["生成"] else "重新組裝"
        it["說明"] = (f"重新生成這一筆的 {len(it['生成'])} 句聲音，再重新組裝" if it["生成"]
                    else "只重新組裝：這一筆沒有聲音要重新生成；要改的地方先到第 3 步改好")
        # 10-02 第四批：文字和範圍都沒改的，換一種念法重新生成（避開以前用過的種子，見 note_versions、tts.redo_avoid）；
        # 上一版的聲音留著備份。畫面上寫會是第幾版
        it["沒改"] = bool(it["生成"]) and all(_same_as_last(workdir, u, ctx, logs) for u in it["生成"])
        if it["生成"]:
            it["第幾版"] = max(int((_last_rec(workdir, u, logs) or {}).get("第幾版") or 1) for u in it["生成"]) + 1
        if it["鍵"] in doing_only:
            it.update({"做法": REASSEMBLE_ONLY, "沒改": False, "接著做": True,
                       "說明": "上次「只重新組裝」還沒做完：生成的聲音不動，再組裝一次"})
            continue
        if it["生成"] and it["鍵"] in doing_keys:
            # 10-02 第五批：上次重做到一半停下來的：這一版已經記過（`_重新生成版本.json`），接著做同一版，不會再跳一版
            it["接著做"] = True
            it["第幾版"] = max(_doing_version(workdir, u, logs) for u in it["生成"])
            it["沒改"] = all(_same_as_doing(workdir, u, ctx, logs) for u in it["生成"])
            left = [u["id"] for u in it["生成"] if u["id"] in pending]
            it["說明"] = (f"上次重做到一半停下來，接著做（第 {it['第幾版']} 版）：" +
                        (f"還有 {len(left)}／{len(it['生成'])} 句要重新生成，已經生成好的不重做，" if left else
                         f"{len(it['生成'])} 句都重新生成好了，") + "再重新組裝")
            continue
        if it["沒改"]:
            it["說明"] = (f"文字和範圍都沒改：換一種念法重新生成這一筆的 {len(it['生成'])} 句聲音（會是第 {it['第幾版']} 版），再重新組裝。"
                        "上一版的聲音留著備份")
        elif it["生成"]:
            it["說明"] += f"（會是第 {it['第幾版']} 版；要念的字或範圍改過的，照改過的內容重新生成）"
    label_items(items, index)
    doing = check.get("重做中")
    return {"已送回": bool(sent), "時間": (sent or {}).get("時間"), "項目": items,
            "重做中": bool(doing), "重做中時間": (doing or {}).get("時間")}


# ---------- 10-01 第三批：第 4 步一鍵只重做退回的這幾筆 ----------
#
# 照 docs/之後要做.md「做法 B」（09-29 宇軒定的方向）：
#   1. 每一筆退回用 `覆核項目`、原片時間找到要重新生成的那幾句（學員重念的那一段、老師重念的那一句、保留原聲學員的代號短句）
#   2. 只清掉那幾句的生成紀錄與生成快取（舊的聲音檔搬到 `重做前_<時間>/`，不刪），其他的不動
#   3. 走第 4 步原本的跑法（`execute.run_execute`，一步一支程式）：只有清掉的那幾句會重新生成，接著重新組裝
#   4. 組裝做完：那幾筆在第 5 步回到「還沒看」，標「重做過」
# 退回的原因目前只給人看、沒有交給生成程式（做法 B 第 3 步「把原因交給 AI」還沒做）。
# 10-02 第四批：文字、範圍沒改的句子，重新生成時避開以前版本用過的種子（照 `tts.next_attempt` 同樣的順序往下換），
# 每一次退回都換一個沒用過的；以前的版本記在生成資料夾的 `_重新生成版本.json`，新的一版記「第幾版」「以前的版本」。

REDO_FILE_DIR = "重做前"


def _redo_ctx(workdir: Path) -> dict:
    """要重新生成的東西現在排出來的樣子（不載入模型）：學員重念的每一段、老師重念的每一句、保留原聲學員的代號短句。"""
    from bookclub import nameplan, students, studentnames

    ctx = {"學員": [], "老師": [], "保留原聲學員": []}
    try:
        ctx["學員"], _ = students.build_items(workdir)
    except FileNotFoundError:
        pass
    try:
        if wd.read_json(wd.names_path(workdir), default=None):
            ctx["老師"] = nameplan.compute_plan(workdir)["生成"]
    except Exception:  # noqa: BLE001 — 排不出老師的計畫：老師那邊不重做
        pass
    try:
        ctx["保留原聲學員"] = studentnames.plan(workdir)["生成"]
    except Exception:  # noqa: BLE001
        pass
    return ctx


def redo_units(it: dict, ctx: dict) -> list[dict]:
    """一筆退回 → 要重新生成哪幾句（純函式）：[{角色, id, slot}]。沒有聲音要生成的（剪掉、消音、重疊標記、沒登記的變動⋯）回空的，只重新組裝。"""
    kind = it.get("類型")
    keys = it.get("覆核項目") or []
    src = it.get("原片") or [None, None]
    turns = {k.split(":", 1)[1] for k in keys if k.startswith("學員段落:")}
    ovs = {k.split(":", 1)[1] for k in keys if k.startswith("重疊:")}
    names = {k.split(":", 1)[1] for k in keys if k.startswith("名字:")}
    stunames = {k.split(":", 1)[1] for k in keys if k.startswith("學員名字:")}

    def same(slot, tol=0.05) -> bool:
        return src[0] is not None and abs(slot[0] - src[0]) <= tol and abs(slot[1] - src[1]) <= tol

    def near(slot, pad=1.0) -> bool:
        return src[0] is not None and slot[0] - pad <= src[1] and src[0] <= slot[1] + pad

    stu = [s for s in ctx["學員"] if s.get("段落") in turns or s.get("重疊") in turns | ovs]
    tea = [g for g in ctx["老師"] if names & {str(c) for c in g.get("候選", [])} or ovs & set(g.get("重疊項目") or [])]
    sn = [g for g in ctx["保留原聲學員"] if stunames & {str(c) for c in g.get("候選", [])}]
    pick: list[tuple[str, dict]] = []
    if kind == "學員重念":
        hit = [s for s in stu if same(s["slot"])] or [s for s in stu if near(s["slot"], 0.0)]
        pick = [("學員", s) for s in hit]
    elif kind == "停格":   # 學員重念比時間格長、結尾停格：重做那一段（停格接在那一段的結尾）
        hit = [s for s in stu if abs(s["slot"][1] - src[0]) <= 0.1] if src[0] is not None else []
        pick = [("學員", s) for s in hit]
    elif kind in ("名字整句換掉", "換聲音"):
        hit = [g for g in ctx["老師"] if same(g["slot"])] or tea
        pick = [("老師", g) for g in hit]
    elif kind == "學員名字換代號":
        pick = [("保留原聲學員", g) for g in sn]
    elif it.get("來源") == "整片看":   # 整片看時標的：附近（前後 1 秒）跟這幾筆有關的那幾句
        pick = [("學員", s) for s in stu if near(s["slot"])] + [("老師", g) for g in tea if near(g["slot"])] \
            + [("保留原聲學員", g) for g in sn if near(g["slot"])]
    out, seen = [], set()
    for role, x in pick:
        if (role, x["id"]) not in seen:
            seen.add((role, x["id"]))
            out.append({"角色": role, "id": x["id"], "slot": [round(x["slot"][0], 3), round(x["slot"][1], 3)]})
    return out


def _last_rec(workdir: Path, unit: dict, logs: dict) -> dict | None:
    """這一句上一次生成的紀錄（logs 是快取：{角色: {id: 紀錄}}）。
    10-02 第五批：重做到一半停下來時，被清掉的那幾句生成紀錄裡沒有了，改用 `_重新生成版本.json` 記的上一版
    （文字、範圍、第幾版），第 4 步才不會說「改過」「第 2 版」。"""
    role = unit["角色"]
    if role not in logs:
        rec = wd.read_json(_role_paths(Path(workdir), role)[0], default=None) or {}
        logs[role] = {r.get("id"): r for r in rec.get("句子") or []}
    got = logs[role].get(unit["id"])
    if got is not None:
        return got
    ent = _versions(workdir, role, logs).get(unit["id"])
    if ent and ent.get("以前的版本"):
        return {"text": ent.get("text"), "生成用文字": ent.get("生成用文字"), "slot": ent.get("slot"),
                "第幾版": ent["以前的版本"][-1].get("第幾版")}
    return None


def _versions(workdir: Path, role: str, logs: dict) -> dict:
    """生成資料夾的 `_重新生成版本.json`（logs 一起當快取）。"""
    from bookclub import tts

    k = ("版本", role)
    if k not in logs:
        logs[k] = wd.read_json(_role_paths(Path(workdir), role)[1] / tts.REDO_VERSIONS, default=None) or {}
    return logs[k]


def _doing_version(workdir: Path, unit: dict, logs: dict) -> int:
    """重做中的這一句正在做第幾版：`_重新生成版本.json` 記了幾個以前的版本＋1（純讀）。"""
    ent = _versions(workdir, unit["角色"], logs).get(unit["id"]) or {}
    return len(ent.get("以前的版本") or []) + 1


def _same_as_doing(workdir: Path, unit: dict, ctx: dict, logs: dict) -> bool:
    """重做中的這一句，現在要念的字、範圍跟被退回的那一版一樣嗎（一樣＝這一版是換一種念法）。"""
    from bookclub import tts

    ent = _versions(workdir, unit["角色"], logs).get(unit["id"])
    now = next((x for x in ctx.get(unit["角色"], []) if x.get("id") == unit["id"]), None)
    return bool(ent and now) and tts.same_content(ent, now)


def _same_as_last(workdir: Path, unit: dict, ctx: dict, logs: dict) -> bool:
    """這一句現在要念的字、時間格跟上一次生成的一樣嗎（一樣的話重新生成多半是同一個聲音）。讀不到就當作有改。"""
    role = unit["角色"]
    last = _last_rec(workdir, unit, logs)
    now = next((x for x in ctx.get(role, []) if x.get("id") == unit["id"]), None)
    if not last or not now:
        return False
    same_slot = all(abs(a - b) <= 0.05 for a, b in zip(now["slot"], last.get("slot") or [None, None]) if a is not None and b is not None) \
        and len(last.get("slot") or []) == 2
    return same_slot and (now.get("text") or "") == (last.get("text") or "")


def _role_paths(workdir: Path, role: str) -> tuple[Path, Path]:
    from bookclub import studentgen, students, tts

    return {"老師": (tts.teacher_log_path(workdir), tts.teacher_out_dir(workdir)),
            "學員": (students.log_path(workdir), students.out_dir(workdir)),
            "保留原聲學員": (studentgen.log_path(workdir), studentgen.out_dir(workdir))}[role]


def redo_folder(stamp: str) -> str:
    """舊的聲音檔搬去的資料夾名稱（生成資料夾底下）。"""
    return f"{REDO_FILE_DIR}_{stamp.replace(':', '').replace('-', '')}"


def note_versions(workdir: Path, role: str, ids: list[str], stamp: str, reasons: dict | None = None) -> dict[str, dict]:
    """10-02 第四批：清掉之前，把這幾句現在的版本記進生成資料夾的 `_重新生成版本.json`（`tts.REDO_VERSIONS`）：
    - 以前的版本：加一筆（第幾版、選定的種子、試過的種子、每一次的結果、聲音檔搬去哪個資料夾、退回原因）
    - 避開：上一版的文字、範圍跟再上一版一樣的話，接著累加；不一樣就從上一版用過的重新算
      重新生成時，文字和範圍都沒改的才真的避開（`tts.redo_avoid`），換一種念法；有改的照原本的規則
    回傳 {id: {第幾版（這次會生成的）, 換一種念法（目前文字範圍沒改＝會避開）}}；沒生成過的句子不記。"""
    from bookclub import tts

    log_path, od = _role_paths(workdir, role)
    recs = {r.get("id"): r for r in (wd.read_json(log_path, default=None) or {}).get("句子") or []}
    path = od / tts.REDO_VERSIONS
    data = wd.read_json(path, default=None) or {}
    folder = str((od / redo_folder(stamp)).relative_to(workdir))
    out: dict[str, dict] = {}
    for i in ids:
        r = recs.get(i)
        if not r:
            continue
        ent = data.get(i) or {}
        tries = [a for a in r.get("嘗試") or [] if a.get("種子") is not None]
        sel = r.get("選定")
        chosen = tries[sel - 1]["種子"] if isinstance(sel, int) and 0 < sel <= len(tries) else None
        olds = list(ent.get("以前的版本") or [])
        n = int(r.get("第幾版") or len(olds) + 1)
        olds.append({"第幾版": n, "種子": chosen, "試過的種子": sorted({int(a["種子"]) for a in tries}),
                     "嘗試": r.get("嘗試") or [], "選定": sel, "備份資料夾": folder, "重做時間": stamp,
                     "退回原因": (reasons or {}).get(i, "")})
        keep = set(ent.get("避開") or []) if ent and tts.same_content(ent, r) else set()   # 上一版跟再上一版文字範圍一樣：接著累加
        avoid = sorted(keep | {int(a["種子"]) for a in tries})
        data[i] = {"text": r.get("text"), "生成用文字": r.get("生成用文字", r.get("text")), "slot": r.get("slot"),
                   "避開": avoid, "以前的版本": olds}
        out[i] = {"第幾版": n + 1}
    if out:
        od.mkdir(parents=True, exist_ok=True)
        wd.write_json(path, data)
    return out


def clear_generated(workdir: Path, role: str, ids: list[str], stamp: str) -> dict:
    """清掉這幾句的生成結果，下一次第 4 步會重新生成（不動別句）：生成紀錄裡那幾句、`_嘗試快取.json` 裡那幾句每一次的結果、
    `_停頓快取.json` 裡那幾句；舊的聲音檔（`<id>_*`）搬到 `重做前_<時間>/`（不刪，要比對可以聽）。回傳 {紀錄, 快取, 檔案} 各清了幾筆。"""
    from bookclub import tts

    log_path, od = _role_paths(workdir, role)
    ids = set(ids)
    n = {"紀錄": 0, "快取": 0, "檔案": 0}
    rec = wd.read_json(log_path, default=None)
    if rec and rec.get("句子"):
        keep = [r for r in rec["句子"] if r.get("id") not in ids]
        n["紀錄"] = len(rec["句子"]) - len(keep)
        if n["紀錄"]:
            wd.write_json(log_path, {**rec, "句子": keep})
    for name in (tts.ATTEMPT_CACHE, tts.PAUSE_CACHE):
        cache = wd.read_json(od / name, default=None)
        if not cache:
            continue
        keep = {k: v for k, v in cache.items() if k.split("|", 1)[0] not in ids}
        if len(keep) != len(cache):
            n["快取"] += len(cache) - len(keep)
            wd.write_json(od / name, keep)
    if od.is_dir():
        dst = od / redo_folder(stamp)
        for i in ids:
            # 10-02 第四批：選定的那一個（`<id>.wav`）也一起搬，以前的版本才聽得到
            for f in [*od.glob(f"{i}_*"), od / f"{i}.wav"]:
                if f.is_file():
                    dst.mkdir(parents=True, exist_ok=True)
                    f.rename(dst / f.name)
                    n["檔案"] += 1
    return n


def redo_plan(workdir: str | Path, a: float | None = None, b: float | None = None) -> list[dict]:
    """第 4 步按「開始執行」時，退回的每一筆要怎麼重做（不寫檔）：
    [{鍵, 來源, 類型, 原片, 覆核項目, 覆核名稱, 原因, 指紋, 做法（重新生成／重新組裝）, 生成[{角色, id, slot}], 說明}]。
    a、b：這次執行的範圍，原片時間不在範圍裡的不算。"""
    workdir = Path(workdir)
    lst = redo_list(workdir)["項目"]
    if not lst:
        return []
    _log, check = _current(workdir)
    out = []
    for it in lst:
        o = it.get("原片")
        if o and o[0] is not None and ((a is not None and o[1] < a) or (b is not None and o[0] > b)):
            continue
        e = {k: it.get(k) for k in ("鍵", "來源", "類型", "原片", "覆核項目", "覆核名稱", "原因", "生成", "做法", "說明", "沒改")}
        e["指紋"] = (check["逐筆"].get(it["鍵"]) or {}).get("指紋")
        out.append(e)
    return out


def prepare_redo(workdir: str | Path, a: float | None = None, b: float | None = None,
                 log=print, reassemble_only: bool = False) -> dict | None:
    """第 4 步開始之前：把退回的那幾句清掉（見 clear_generated），記在 `覆核/成品檢查.json` 的 `重做中`。沒有退回的回傳 None。

    reassemble_only（10-03 第八批 #23）：「只重新組裝」——退回的那幾筆一樣記進 `重做中`（組裝做完照 `finish_redo`
    回到還沒看），但**不清生成、不記新版本、不換種子**（跳過 `note_versions`、`clear_generated`），每一筆標 `只重新組裝`。
    給「生成的聲音沒問題、要改的是組裝」用（例如接縫做法改了）。"""
    workdir = Path(workdir)
    plan = redo_plan(workdir, a, b)
    if not plan:
        return None
    if reassemble_only:
        doing = {e["鍵"] for e in (load_check(workdir).get("重做中") or {}).get("項目") or []}
        plan = [e for e in plan if e["鍵"] not in doing]   # 上次沒做完的照上次的做法接著做
        if not plan:
            return {"項目": [], "清掉": {}, "接著做": True}
        stamp = _now()
        for e in plan:
            e.update({REASSEMBLE_ONLY: True, "做法": REASSEMBLE_ONLY, "沒改": False,
                      "說明": "只重新組裝：生成的聲音不動（不重新生成、不換念法），照現在的做法重新放回去"})
        with _lock:
            _log, check = _current(workdir)
            old = check.get("重做中") or {}
            have = {e["鍵"] for e in plan}
            check["重做中"] = {"時間": stamp, "項目": [e for e in old.get("項目", []) if e["鍵"] not in have] + plan}
            _save(workdir, check)
        log(f"[AI 執行] 第 5 步退回的 {len(plan)} 筆只重新組裝：生成的聲音不動，組裝做完回到第 5 步「還沒看」")
        return {"項目": plan, "清掉": {}, REASSEMBLE_ONLY: True}
    # 10-02 第五批：上次重做到一半停下來（停止、記憶體或硬碟門檻、失敗）的那幾筆，清過了、版本也記過了：
    # 不再清一次（不然已經生成好、還沒寫回紀錄的那一句會被當成沒做，已經寫回的會變成「以前的版本」又跳一版）。
    # 這次只清新退回的；接著做的那幾筆，生成紀錄裡沒有的會照常生成（已經生成過的從快取沿用）
    doing = {e["鍵"] for e in (load_check(workdir).get("重做中") or {}).get("項目") or []}
    resumed = [e for e in plan if e["鍵"] in doing]
    plan = [e for e in plan if e["鍵"] not in doing]
    if resumed:
        left = redo_pending(workdir, a, b)
        log(f"[AI 執行] 上次重做到一半停下來的 {len(resumed)} 筆接著做：" +
            (f"還有 {len(left)} 句要重新生成（已經生成好的不重做）" if left else "要生成的都做好了，接著組裝"))
    if not plan:
        return {"項目": resumed, "清掉": {}, "接著做": True} if resumed else None
    stamp = _now()
    by_role: dict[str, list[str]] = {}
    reasons: dict[tuple, str] = {}
    for e in plan:
        for u in e["生成"]:
            by_role.setdefault(u["角色"], []).append(u["id"])
            reasons.setdefault((u["角色"], u["id"]), e.get("原因") or "")
    # 10-02 第四批：清掉之前先記下現在的版本；文字和範圍都沒改的，重新生成時換一種念法（避開用過的種子）
    vers = {role: note_versions(workdir, role, ids, stamp, {i: reasons.get((role, i), "") for i in ids})
            for role, ids in by_role.items()}
    for e in plan:
        ns = [vers.get(u["角色"], {}).get(u["id"], {}).get("第幾版") for u in e["生成"]]
        ns = [x for x in ns if x]
        if ns:
            e["第幾版"] = max(ns)
    cleared = {role: clear_generated(workdir, role, ids, stamp) for role, ids in by_role.items()}
    with _lock:
        _log, check = _current(workdir)
        old = check.get("重做中") or {}
        have = {e["鍵"] for e in plan}
        check["重做中"] = {"時間": stamp, "項目": [e for e in old.get("項目", []) if e["鍵"] not in have] + plan}
        _save(workdir, check)
    n = sum(len(v) for v in by_role.values())
    log(f"[AI 執行] 第 5 步退回的 {len(plan)} 筆：{n} 句清掉舊的、等一下重新生成；全部做完會重新組裝")
    return {"項目": plan, "清掉": cleared}


def redo_pending(workdir: str | Path, a: float | None = None, b: float | None = None, *, ctx: dict | None = None) -> list[str]:
    """10-02 第五批：「重做中」（清掉了、要重新生成）的那幾句，還沒重新生成好的 id（生成紀錄裡沒有、或沒放回時間格）。
    只算這次範圍（a～b）裡、現在的計畫裡還有的句子（第 3 步改到沒有了的不算）；保留原聲學員退回直接消音的算處理好。
    組裝前用：還有沒做好的就不組（組進去會是原本的聲音，名字還在）。只讀。"""
    workdir = Path(workdir)
    doing = load_check(workdir).get("重做中") or {}
    units = [u for e in doing.get("項目") or [] if not e.get(REASSEMBLE_ONLY) for u in e.get("生成") or []]
    if not units:
        return []
    ctx = _redo_ctx(workdir) if ctx is None else ctx
    alive = {(role, x.get("id")) for role, xs in ctx.items() for x in xs}
    logs: dict = {}
    out: list[str] = []
    for u in units:
        s0, s1 = (u.get("slot") or [None, None])[:2]
        if (a is not None and s1 is not None and s1 < a) or (b is not None and s0 is not None and s0 > b):
            continue
        role = u["角色"]
        if (role, u["id"]) not in alive:
            continue
        if role not in logs:
            rec = wd.read_json(_role_paths(workdir, role)[0], default=None) or {}
            logs[role] = ({r.get("id"): r for r in rec.get("句子") or []}, rec.get("退回直接消音") or {})
        recs, back = logs[role]
        if u["id"] in back or (recs.get(u["id"]) or {}).get("放回時間格"):
            continue
        if u["id"] not in out:
            out.append(u["id"])
    return out


def finish_redo(workdir: str | Path) -> dict | None:
    """組裝做完之後：`重做中` 的那幾筆在第 5 步回到「還沒看」，記成 `重做過`（第 5 步看得出是重做過的新版本）。"""
    workdir = Path(workdir)
    with _lock:
        log, check = _current(workdir)
        doing = check.pop("重做中", None)
        if not doing:
            return None
        keys = {e["鍵"] for e in doing["項目"]}
        for e in doing["項目"]:
            check["逐筆"].pop(e["鍵"], None)
            check["未登記確認"].pop(e["鍵"], None)
        # 10-03 第八批 #23：整片看時退回的，送回重做、組裝做完才拿掉（重新組裝本身不再清空整片退回）
        check["整片退回"] = [x for x in check.get("整片退回", []) if x.get("id") not in keys]
        # 重做過的那幾筆，成品範圍前後各 2 秒裡看過的部分要重看（內容沒變的只重新組裝也一樣，退回過就要再聽一次）
        plist = (log or {}).get("片段")
        holes = []
        recs = {record_key(r): r for r in (log or {}).get("紀錄", [])}
        for e in doing["項目"]:
            r = recs.get(e["鍵"])
            c = (r or {}).get("成品") or [None, None]
            if c[0] is not None and c[1] is not None:
                holes.append([c[0] - WATCH_CLEAR_PAD_S, c[1] + WATCH_CLEAR_PAD_S])
            elif e.get("原片") and e["原片"][0] is not None:
                t0, t1 = (near_output_time(float(t), plist) for t in e["原片"])
                holes.append([t0 - WATCH_CLEAR_PAD_S, t1 + WATCH_CLEAR_PAD_S])
        if holes:
            _set_watched(check, subtract_ranges(check["看過區段"], holes), plist)
        check.pop("送回AI重做", None)
        check["重做過"] = {"時間": _now(), "處理紀錄產生時間": (log or {}).get("產生時間"), "項目": doing["項目"],
                        **({REASSEMBLE_ONLY: True} if any(e.get(REASSEMBLE_ONLY) for e in doing["項目"]) else {})}
        _save(workdir, check)
    return check["重做過"]


def returned_by_card(workdir: str | Path, index: dict) -> dict[str, list[str]]:
    """10-01 第三批 5：第 5 步退回（還沒重做好）的，對到第 3 步哪一張卡片 → 退回的原因。只讀，不寫檔。
    重做完（第 4 步組裝做完、回到還沒看）就不在退回清單裡，卡片上的那一行跟著消失。"""
    log = proclog.load(Path(workdir))
    if not log:
        return {}
    check = refresh(load_check(Path(workdir)), log)
    out: dict[str, list[str]] = {}
    for it in redo_items(log, check):
        for k in it.get("覆核項目") or []:
            card = (index.get(k) or {}).get("第3步")
            why = (it.get("原因") or "").strip() or "（沒寫原因）"
            if card and why not in out.setdefault(card, []):
                out[card].append(why)
    return out


def redone_info(rec: dict, check: dict, log: dict | None) -> dict | None:
    """這一筆是不是上一次「只重做退回的」重做過的（純函式）：同一個鍵，或對到同樣的第 3 步項目、時間也接近。"""
    done = check.get("重做過")
    if not done or done.get("處理紀錄產生時間") != (log or {}).get("產生時間"):
        return None
    key = record_key(rec)
    keys = set(rec.get("覆核項目") or [])
    o = rec.get("原片") or [None, None]
    # 10-03 第八批補修：先找同一個鍵；找不到（重做後範圍改了、鍵變了）才用「同一張第 3 步卡片＋時間有重疊」找。
    # 以前是「同一張卡片＋相差 1 秒內」就算：同一個學員段落切成好幾格、格子相鄰時，會拿到隔壁那一格的退回原因
    # （第一堂 T034 24 格，原因整排錯位；沒退回過的格子也顯示「重做過」）
    items = done.get("項目", [])
    hit = next((e for e in items if e.get("鍵") == key), None)
    if hit is None:
        taken = {record_key(r) for r in (log or {}).get("紀錄", [])}
        for e in items:
            if e.get("鍵") in taken or not keys & set(e.get("覆核項目") or []):
                continue          # 那一筆退回自己還在（鍵沒變）：它的原因只屬於它自己
            eo = e.get("原片") or [None, None]
            if o[0] is None or eo[0] is None:
                continue
            if eo[0] == eo[1]:   # 整片看時標的（一個時間點）：落在這一筆前後 1 秒內
                ok = o[0] - 1.0 <= eo[0] <= o[1] + 1.0
            else:
                both = min(o[1], eo[1]) - max(o[0], eo[0])
                ok = both > 0 and both >= 0.5 * min(o[1] - o[0], eo[1] - eo[0])
            if ok:
                hit = e
                break
    if hit is None:
        return None
    e = hit
    return {"時間": done["時間"], "原因": e.get("原因", ""), "做法": e.get("做法"),
            "第幾版": e.get("第幾版"), "換一種念法": bool(e.get("沒改")),   # 10-02 第四批
            REASSEMBLE_ONLY: bool(e.get(REASSEMBLE_ONLY)),   # 10-03 第八批 #23
            "標籤": "重做過（只重新組裝）" if e.get(REASSEMBLE_ONLY) else "重做過",
            "新版本": (record_print(rec) != e["指紋"]) if e.get("指紋") else None}


def suggest_command(workdir: Path, it: dict) -> str:
    """終端機（`bookclub redo list`）上給的指令。10-01 第三批：一鍵只重做做好了，一律給 `run execute --redo-returned`
    （以前給 `gen students --only`、`gen names --only`：文字、範圍沒改的話什麼都不會重做；`gen names --only` 還會把
    名字處理計畫整份換成只有那幾筆，下一次組裝只換那幾個名字）。"""
    return f"bookclub run execute {workdir} --redo-returned"


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


def goto_output_time(workdir: str | Path, t_src: float) -> dict:
    """`GET /api/final/goto?src=原片秒數`（10-03 第八批 #64）：第 5 步「跳到」框選原片時間時，換成成品時間。
    換算用 `to_output_time`（跟頁面其他地方同一支）；落在剪掉的範圍裡，跳到剪掉之後第一個留下來的地方並說明。
    回傳 {成品秒, 說明}；成品秒是 None 表示原片這個時間之後都剪掉了。"""
    log = proclog.load(Path(workdir)) or {}
    plist = log.get("片段")
    t_src = float(t_src)
    out = to_output_time(t_src, plist)
    if out is not None:
        return {"成品秒": round(out, 3), "說明": ""}
    nxt = next((p for p in plist or [] if p["src"][0] > t_src), None)
    if nxt is None:
        return {"成品秒": None, "說明": "原片這個時間之後都剪掉了，成品裡沒有"}
    return {"成品秒": round(to_output_time(nxt["src"][0], plist), 3),
            "說明": "原片這個時間在成品裡剪掉了，跳到剪掉之後的地方"}
