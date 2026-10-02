"""原片時間 ↔ 成品時間（10-02 第六批）：給人看的清單一律同時列「原片 0:42:59.8／成品 0:38:26.4」。

換算一律用組裝寫下的片段表（`生成/處理紀錄.json` 或 `輸出/剪輯決策_<範圍>.json` 的 `片段`），
原片 → 成品用 `render.to_output_time`（扣掉剪掉的、加上停格），成品 → 原片用 `finalcheck.from_output_time`。
10-02：有人自己只扣剪掉的片段、沒算停格，後段算錯 13 秒；不要自己另外算。
"""

from __future__ import annotations

from pathlib import Path

from bookclub import workdir as wd


def t1(sec: float | None) -> str:
    """秒數 → 「0:42:59.8」（到零點一秒）。None → 「—」。"""
    if sec is None:
        return "—"
    sec = round(max(0.0, float(sec)), 1)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:04.1f}"


def load(workdir: str | Path, tag: str | None = None) -> dict | None:
    """片段表：{片段（None＝只換聲音、時間不變）, 範圍（原片 [起, 訖] 或 None＝整支）, 來源}。
    tag 給了就讀 `輸出/剪輯決策_<tag>.json`，沒給讀 `生成/處理紀錄.json`（第 5 步檢查的那一份）。都沒有回傳 None。"""
    workdir = Path(workdir)
    if tag:
        d = wd.read_json(workdir / "輸出" / f"剪輯決策_{tag}.json", default=None)
        if not d:
            return None
        return {"片段": d.get("片段"), "範圍": d.get("範圍"), "來源": f"輸出/剪輯決策_{tag}.json"}
    from bookclub import proclog

    log = proclog.load(workdir)
    if not log:
        return None
    return {"片段": log.get("片段"), "範圍": log.get("範圍"), "來源": "生成/處理紀錄.json"}


def to_output(t: float, m: dict | None) -> float | None:
    """原片 → 成品。沒有片段表（還沒組裝）、落在剪掉的地方、不在組裝範圍裡，回傳 None。"""
    if not m:
        return None
    plist = m.get("片段")
    if plist is None:   # render audio：沒有剪掉、停格，時間一樣
        return float(t)
    if not plist:
        return None
    from bookclub.render import to_output_time

    return to_output_time(float(t), plist)


def to_source(t: float, m: dict | None) -> float | None:
    """成品 → 原片（落在停格裡算停格那一點）。"""
    if not m:
        return None
    plist = m.get("片段")
    if plist is None:
        return float(t)
    if not plist:
        return None
    from bookclub.finalcheck import from_output_time

    return from_output_time(float(t), plist)


def span(a: float | None, b: float | None) -> str:
    if a is None:
        return "—"
    return t1(a) if b is None or abs(b - a) < 0.05 else f"{t1(a)}–{t1(b)}"


def both(a: float, b: float | None, m: dict | None) -> str:
    """「原片 0:42:59.8–0:43:07.4／成品 0:38:26.4–0:38:34.0」。沒有成品時只列原片；剪掉的寫「成品裡剪掉了」。"""
    src = f"原片 {span(a, b)}"
    if not m:
        return src
    oa = to_output(a, m)
    ob = to_output(b, m) if b is not None else None
    if oa is None and ob is None:
        return f"{src}／成品裡沒有（剪掉了或不在這次組裝的範圍）"
    return f"{src}／成品 {span(oa if oa is not None else ob, ob)}"
