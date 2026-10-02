"""老師重念範圍的開頭或結尾沒有人講話（10-02 第六批第二件）。

起因（第一堂）：老師重念 0:42:59.8–0:43:07.4（7.6 秒），逐字稿的字是 0:43:01.5–0:43:06.7，前面 1.7 秒只有底噪。
生成的聲音只有約 2.9 秒，換三種念法都放不進時間格，聽了才發現範圍前面不該包進來。

做法：老師重念（名字整句換掉、人工標的老師整段、重疊選了生成老師聲音的那一句）的範圍，開頭或結尾有一段
超過 `EDGE_SILENT_S` 秒沒有任何字，就提醒並給一個建議範圍（有字的地方前後各留 `EDGE_PAD_S` 秒）。
**不自動改任何已經存在的範圍**（改了會讓已經生成好的句子全部重做）：第 3 步卡片、第 4 步總檢查「請看一眼」、
第 5 步那一筆都只提醒，旁邊一顆「照建議縮小」按鈕（`apply`），走原本改範圍的同一條路：
- 名字整句換掉：名字卡片的重念範圍（`review.save_name` 的 `整句起訖`）
- 人工標的老師整段：改時間（`review.manual_edit`，照填的時間、不對齊句子邊界，不然會被縮回句子開頭）
- 重疊選「兩邊都重新生成（照原本的時間）」的老師那一句：老師那邊的起訖（`review.save_overlap` 的 `老師起訖`）
- 重疊選「生成老師聲音」的那一句：範圍照逐字稿的句子走，沒有可以改的地方 → 只提醒，不給按鈕
縮小之後，前後留下來的原聲裡如果有名字候選（沒標「不是名字」「是地名」、也沒有另外消音的），不給縮（不能讓名字留在成品裡）。
學員重念不用做（學員段落本來就整段換）。
"""

from __future__ import annotations

from pathlib import Path

from bookclub import timemap
from bookclub import workdir as wd

EDGE_SILENT_S = 0.8   # 範圍開頭或結尾連續這麼多秒沒有字，才提醒（字的時間本身有零點幾秒的誤差，太小會一直跳出來）
EDGE_PAD_S = 0.15     # 建議範圍在第一個字前、最後一個字後各留這麼多（不要切到字的頭尾）
NAME_OVERLAP_S = 0.02

HOW_RANGE, HOW_WHOLE, HOW_SIDE = "重念範圍", "老師整段", "老師起訖"


def edges(a: float, b: float, words: list[dict], min_s: float = EDGE_SILENT_S, pad: float = EDGE_PAD_S) -> dict | None:
    """範圍 [a, b] 開頭、結尾沒有字的秒數與建議範圍（純函式）。都不超過 min_s、或整段一個字都沒有，回傳 None。"""
    ws = [w for w in words or [] if w["end"] > a and w["start"] < b]
    if not ws:
        return None
    first = min(w["start"] for w in ws)
    last = max(w["end"] for w in ws)
    lead, tail = max(0.0, first - a), max(0.0, b - last)
    if lead <= min_s and tail <= min_s:
        return None
    na = round(max(a, first - pad), 3) if lead > min_s else round(a, 3)
    nb = round(min(b, last + pad), 3) if tail > min_s else round(b, 3)
    return {"前": round(lead, 2) if lead > min_s else 0.0, "後": round(tail, 2) if tail > min_s else 0.0, "建議": [na, nb]}


def describe(h: dict) -> str:
    """「這個範圍前面有 1.7 秒沒有人講話，建議縮成 0:43:01.4–0:43:07.4」"""
    sides = []
    if h["前"]:
        sides.append(f"前面有 {h['前']:.1f} 秒")
    if h["後"]:
        sides.append(f"後面有 {h['後']:.1f} 秒")
    a, b = h["建議"]
    return f"這個範圍{'、'.join(sides)}沒有人講話，建議縮成 {timemap.t1(a)}–{timemap.t1(b)}"


def _route(g: dict, cands: dict) -> tuple[str, str | None, str | None, str]:
    """這一筆老師重念 → (第 3 步卡片的鍵, 改法, 要改的 id, 不能一鍵縮的原因)。"""
    cids = [str(x) for x in g.get("候選") or []]
    ovs = [str(x) for x in g.get("重疊項目") or []]
    if cids:
        key = f"名字:{cids[0]}"
        if len(cids) > 1:
            return key, None, None, "這一句同時換掉好幾個名字，重念範圍是合起來的：到第 3 步這幾張卡片各自改重念範圍"
        if ovs:
            return key, None, None, "這一句同時是名字重念和重疊的老師那一句：到第 3 步名字卡片改重念範圍"
        c = cands.get(cids[0]) or {}
        if c.get("老師整段"):
            return key, HOW_WHOLE, cids[0], ""
        return key, HOW_RANGE, cids[0], ""
    key = f"重疊:{ovs[0]}" if ovs else f"生成:{g.get('id')}"
    if ovs and g.get("疊放"):
        return key, HOW_SIDE, ovs[0], ""
    return key, None, None, ("這一句是重疊選了「生成老師聲音」：重念範圍照逐字稿的句子走，這裡沒辦法直接縮；"
                             "要縮的話，到第 3 步這一處重疊改用「兩邊都重新生成」，自己定老師那邊的起訖")


def hints(workdir: str | Path, plan: dict | None = None) -> dict[str, dict]:
    """每一筆要提醒的老師重念：{第 3 步卡片的鍵: {生成編號, 範圍, 前, 後, 建議, 可以縮, 改法, id, 原因, 說明, 名字擋下}}。只讀。"""
    from bookclub import nameplan, review

    workdir = Path(workdir)
    if plan is None:
        if not wd.read_json(wd.names_path(workdir), default=None):
            return {}
        plan = nameplan.compute_plan(workdir)
    words = (wd.read_json(wd.merged_transcript_path(workdir), default={}) or {}).get("words") or []
    if not words or not plan.get("生成"):
        return {}
    decisions = wd.read_json(review.name_decisions_path(workdir), default={}) or {}
    raw = (wd.read_json(wd.names_path(workdir), default=None) or {}).get("candidates", [])
    cands = {}
    for i, c in enumerate(review.effective_name_candidates(workdir, raw, decisions), start=1):
        cid = str(c.get("id") or i)
        if set((decisions.get(cid) or {}).get("tags") or []) & {"不是名字", "是地名"}:
            continue
        cands[cid] = c
    mutes = plan.get("消音") or []
    out: dict[str, dict] = {}
    for g in plan["生成"]:
        a, b = float(g["slot"][0]), float(g["slot"][1])
        e = edges(a, b, words)
        if not e:
            continue
        key, how, iid, why = _route(g, cands)
        na, nb = e["建議"]
        left = [(x, y) for x, y in ((a, na), (nb, b)) if y - x > 0]
        # 縮掉的那幾秒回到原聲：裡面有名字候選（包括這一句自己的）就不給縮；只有那個名字本來就另外消音的不算
        hits = [cid for cid, c in cands.items() if not c.get("老師整段")
                and any(min(c["end"], y) - max(c["start"], x) > NAME_OVERLAP_S for x, y in left)
                and not any(m["start"] <= c["start"] + NAME_OVERLAP_S and c["end"] - NAME_OVERLAP_S <= m["end"] for m in mutes)]
        if hits:
            how, why = None, (f"縮小之後留下來的原聲裡有 {len(hits)} 個名字候選，縮了名字會留在成品裡："
                              "到第 3 步自己聽過再決定範圍")
        h = {"生成編號": g.get("id"), "範圍": [round(a, 3), round(b, 3)], **e, "可以縮": how is not None,
             "改法": how, "id": iid, "原因": why, "名字擋下": len(hits), "鍵": key}
        h["說明"] = describe(h)
        out[key] = h
    return out


def apply(workdir: str | Path, key: str, final_key: str | None = None) -> dict:
    """「照建議縮小」（`POST /api/review/shrink`）：重新算一次建議，能縮才改，走原本改範圍的同一條路。
    final_key（第 5 步那一筆的鍵）給了的話，那一筆同時標成退回重做、記下範圍改了（跟第 5 步「調整時間範圍」一樣）。"""
    from bookclub import review

    workdir = Path(workdir)
    h = hints(workdir).get(str(key))
    if not h:
        raise ValueError("這一筆現在沒有要縮小的建議（可能已經縮過了）")
    if not h["可以縮"]:
        raise ValueError(h["原因"])
    a, b = h["建議"]
    if h["改法"] == HOW_RANGE:
        review.save_name(workdir, h["id"], {"整句起訖": [a, b]})
    elif h["改法"] == HOW_WHOLE:
        review.manual_edit(workdir, {"類型": "名字", "id": h["id"], "start": a, "end": b, "不對齊": True})
    elif h["改法"] == HOW_SIDE:
        review.save_overlap(workdir, h["id"], {"老師起訖": [a, b]})
    else:
        raise ValueError(h["原因"] or "這一筆沒辦法直接縮")
    if final_key:
        from bookclub import finalcheck

        name = review.item_name(review.item_index(workdir), h["鍵"])
        finalcheck.decide_record(workdir, str(final_key), finalcheck.REDO,
                                 f"照建議縮小重念範圍：{timemap.t1(a)}–{timemap.t1(b)}（原本 {timemap.t1(h['範圍'][0])}–{timemap.t1(h['範圍'][1])}）",
                                 {"名稱": name, "原本": h["範圍"], "改成": [a, b], "第3步": h["鍵"],
                                  "重做": "重新生成這一筆、再重新組裝"})
    return {"ok": True, "鍵": h["鍵"], "原本": h["範圍"], "改成": [a, b]}
