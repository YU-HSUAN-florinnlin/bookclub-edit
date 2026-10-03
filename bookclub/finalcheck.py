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
    if check["成品影片"]:
        # 10-02 第七批（C2）：重新組裝後檔名一樣、長度變了 → 成品檔換新（大小或修改時間不同）或處理紀錄換新就重新量；
        # 看過的區段超出新長度的截掉（以前只在沒量過時量一次，「整片看過幾 %」一直用舊長度算）
        fp = product_print(Path(workdir) / check["成品影片"])
        stamp = (log or {}).get("產生時間")
        if not check.get("成品長度") or check.get("成品檔指紋") != fp or check.get("量長度時的處理紀錄") != stamp:
            length = round(probe_duration(Path(workdir) / check["成品影片"]), 3)
            check.update({"成品長度": length, "成品檔指紋": fp, "量長度時的處理紀錄": stamp})
            check["看過區段"] = clip_ranges(check["看過區段"], length)
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
                     "重做過": redone_info(r, check, log)})   # 10-01 第三批：上一次「只重做退回的」重做過的
        hit = next((edge[k] for k in r.get("覆核項目") or [] if k in edge), None) if r["類型"] in ("名字整句換掉", "換聲音") else None
        if hit:
            recs[-1]["前後沒聲音"] = hit
        recs[-1]["要人看"] = needs_look(r, d)   # 10-02 第六批：「只看要人聽的」篩選
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
    }


def choose_product(workdir: str | Path, rel: str) -> dict:
    workdir = Path(workdir)
    if rel not in products(workdir):
        raise ValueError(f"不是這個工作區的成品影片：{rel}")
    with _lock:
        log, check = _current(workdir)
        if check["成品影片"] != rel:
            check.update({"成品影片": rel, "看過區段": [], "成品長度": round(probe_duration(workdir / rel), 3),
                          "成品檔指紋": product_print(workdir / rel), "量長度時的處理紀錄": (log or {}).get("產生時間")})
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
        wd.unlink_if_link(dst)   # 10-02 第五批
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
    ctx = _redo_ctx(workdir) if items else {}
    logs: dict = {}
    doing_keys = {e["鍵"] for e in (check.get("重做中") or {}).get("項目") or []}
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
                 log=print) -> dict | None:
    """第 4 步開始之前：把退回的那幾句清掉（見 clear_generated），記在 `覆核/成品檢查.json` 的 `重做中`。沒有退回的回傳 None。"""
    workdir = Path(workdir)
    plan = redo_plan(workdir, a, b)
    if not plan:
        return None
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
    units = [u for e in doing.get("項目") or [] for u in e.get("生成") or []]
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
        for e in doing["項目"]:
            check["逐筆"].pop(e["鍵"], None)
            check["未登記確認"].pop(e["鍵"], None)
        check.pop("送回AI重做", None)
        check["重做過"] = {"時間": _now(), "處理紀錄產生時間": (log or {}).get("產生時間"), "項目": doing["項目"]}
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
    for e in done.get("項目", []):
        eo = e.get("原片") or [None, None]
        close = o[0] is not None and eo[0] is not None and eo[0] - 1.0 <= o[1] and o[0] <= eo[1] + 1.0
        if e.get("鍵") == key or (keys & set(e.get("覆核項目") or []) and close):
            return {"時間": done["時間"], "原因": e.get("原因", ""), "做法": e.get("做法"),
                    "第幾版": e.get("第幾版"), "換一種念法": bool(e.get("沒改")),   # 10-02 第四批
                    "新版本": (record_print(rec) != e["指紋"]) if e.get("指紋") else None}
    return None


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
