"""第 5 步的卡片（10-15 第5步2.0 第2段，31 號設計 §6）。

第 5 步改成用第 3 步的卡片：一張卡一個狀態（還沒生成／還沒看／通過／退回／已改，等重做），不再是「處理紀錄的每一筆」。

讀：
- 新紀錄（`needs`，修改需求紀錄）能用時（`needs.healthy()`）：卡片表＋最新一版的成品狀態，一張卡對到這一版處理紀錄的哪幾筆
  照版本表的 `紀錄卡號`。
- 不能用時（還沒升級、同步出過錯、第 4 步執行中第一次打開）：退回讀舊檔——處理紀錄每一筆照 `覆核項目` 歸到第 3 步那張卡，
  狀態照第 1 段定的規則算（任一筆退回＝退回、每一筆都通過＝通過、其他＝還沒看）。畫面頂端多一句小字說明。
寫（雙寫照第 1 段）：卡片按通過／退回 → 舊檔 `覆核/成品檢查.json` 這張卡對到的每一筆一起寫（通過＝每一筆通過；退回＝每一筆退回、
原因同一句；沒登記的變動、整片退回照樣對到），存檔後 `needs.mirror_final` 重算卡片狀態，再補上退回原因的選項（`needs.set_reason`）。
第 3 步照舊讀寫第 3 步的檔；第 5 步按「改」走第 3 步原本的 API，改完記「已改，等重做」（`needs.mark_edited`）。

10-15 第2段審查修正：
- 一筆處理紀錄可以對到好幾張卡（名字＋疊到它的重疊卡、學員段落切兩格⋯）。舊檔那一筆記「哪幾張卡退回了它」（`各卡原因`），
  一張卡按通過／取消時，還有別張卡退回的共用筆照樣是退回（跳過，回傳告訴畫面「跟 #N 共用，那張還是退回」）；
  一張卡退回只寫這張卡自己的筆（獨有＋共用），共用那幾筆連帶讓別張卡也變退回，畫面在那張標「連帶」（`連帶自`）。
- 新紀錄對不到這一版處理紀錄（版本表裡沒有）時讀舊檔，不拿上一版湊。
- 第 3 步改過、這一版還沒做進去的卡：聲音指紋跟這一版 `卡片指紋` 不同 → 「已改，等重做」（算進「重做這幾張」）。
"""

from __future__ import annotations

from pathlib import Path

from bookclub import finalcheck as fc
from bookclub import needs, proclog

SRC_NEW, SRC_OLD = "新紀錄", "舊檔"
EDITED = "已改，等重做"
OLD_LABEL = "舊紀錄"   # 升級、組裝時對不到卡片的「待釐清」卡（31 號設計 §4.3：畫面標小字「舊紀錄」）
STEP3_KINDS = needs.STEP3_TYPES
# 卡片狀態（畫面上的標籤）：照這個順序算「還沒看完」
STATES = (needs.NOT_MADE, needs.NOT_SEEN, needs.PASSED, needs.RETURNED, EDITED)
TODO_STATES = (needs.NOT_SEEN, needs.RETURNED, EDITED, needs.NOT_MADE)
REDO_STATES = (needs.RETURNED, EDITED, needs.NOT_MADE)   # 「重做這幾張」算的


def _key(it: dict) -> str:
    return f"{it['類型']}:{it['id']}"


def owner_key(c: dict) -> str:
    """10-15 第2段審查修正：舊檔 `各卡原因` 記哪張卡用的鍵——有第 3 步鍵就用它（新紀錄、舊檔模式都一樣），沒有用卡片 id。"""
    return str(c.get("第3步鍵") or c["id"])


def _span(rows: list, field: str) -> list | None:
    """幾筆處理紀錄的 原片／成品 取聯集（沒有就 None）。"""
    a = [r[field][0] for r in rows if (r.get(field) or [None])[0] is not None]
    b = [r[field][1] if r[field][1] is not None else r[field][0] for r in rows if (r.get(field) or [None])[0] is not None]
    return [round(min(a), 3), round(max(b), 3)] if a else None


def _rec_info(recs: list[dict]) -> dict:
    """一張卡對到的處理紀錄 → 畫面要的幾個小欄位。"""
    return {"要人聽": any(fc.needs_look(r) for r in recs),
            # 有生成檔＝退回會換一種念法重新生成；沒有（剪掉、消音、停格、空隙）＝重做只會重新放一次（31 號設計 §6.4）
            "可重新生成": any(fc.gen_id(r) or r.get("檔案") for r in recs)}


def legacy_state(results: list[str | None]) -> str:
    """（純函式）舊檔一張卡對到的每一筆判斷 → 卡片狀態：任一筆退回＝退回、每一筆都通過＝通過、其他＝還沒看（第 1 段定的對法）。"""
    if any(r == fc.REDO for r in results):
        return needs.RETURNED
    if results and all(r == fc.PASS for r in results):
        return needs.PASSED
    return needs.NOT_SEEN


def summary(cards: list[dict]) -> dict:
    """（純函式）頂端那一句要的數字：還沒看幾張、要重做幾張（退回＋已改＋還沒生成）、全部幾張。"""
    return {"全部": len(cards), "還沒看": sum(1 for c in cards if c["狀態"] == needs.NOT_SEEN),
            "要重做": sum(1 for c in cards if c["狀態"] in REDO_STATES),
            "退回": sum(1 for c in cards if c["狀態"] == needs.RETURNED),
            "已改": sum(1 for c in cards if c["狀態"] == EDITED),
            "通過": sum(1 for c in cards if c["狀態"] == needs.PASSED)}


# ---------- 新紀錄 ----------

def _from_needs(workdir: Path, log: dict | None, check: dict, flags_text: dict, index: dict) -> list[dict] | None:
    """新紀錄 → 卡片。對不到這一版處理紀錄（版本表裡沒有這一份）＝None（呼叫的地方改讀舊檔）。"""
    st = needs.load(workdir)
    by_key = st.alive_by_key()
    flag_of: dict[str, list[str]] = {}   # 整片退回對到的卡（只對到一張的；待釐清卡另外用 舊檔鍵 對）
    for x in check.get("整片退回") or []:
        cs = needs._resolve(x.get("覆核項目") or [], index, by_key)
        if len(cs) == 1:
            flag_of.setdefault(cs[0], []).append(x["id"])
    v = st.latest_version()
    if v is not None and log and v.get("時間") != log.get("產生時間"):
        # 10-15 第2段審查修正（建議）：對不到這一版 → 讀舊檔（以前退回用上一版，卡片對到的處理紀錄會錯）
        v = st.version_at(log.get("產生時間"))
        if v is None:
            return None
    elif v is None and log and not needs.audio_only(log):
        return None   # 有處理紀錄、還沒建任何版本（例如第 4 步執行中第一次打開）：讀舊檔
    vn = v["版本號"] if v else None
    recs_by_key = {fc.record_key(r): r for r in (log or {}).get("紀錄", [])}
    un_by_key = {fc.unlogged_key(u): u for u in (log or {}).get("未登記的變動", [])}
    per: dict[str, list[str]] = {}
    for rk, cs in ((v or {}).get("紀錄卡號") or {}).items():
        for c in cs:
            per.setdefault(c, []).append(rk)
    un_of = {c: uk for uk, c in ((v or {}).get("未登記卡號") or {}).items()}
    gen_round: dict[str, int] = {}
    for g in st.gen.values():
        for c in g.get("卡號") or []:
            gen_round[c] = max(gen_round.get(c, 1), int(g.get("第幾版") or 1))
    vtime = str((v or {}).get("建立時間") or "")
    vprints = (v or {}).get("卡片指紋") or {}
    now_prints: dict | None = None
    out = []
    for k in sorted(st.cards, key=needs._card_num):
        c = st.cards[k]
        row = st.states.get((k, vn)) if vn else None
        pend = c.get("等重做") or {}
        if c["狀態"] != needs.ALIVE and not pend:
            continue   # 刪掉、併入、拆成、消失的：沒有等重做就不列（刪掉的待釐清卡＝整片退回取消了）
        if pend.get("起因") == needs.WHY_EDITED:
            state = EDITED
        elif row:
            state = row.get("狀態") or needs.NOT_SEEN
        elif v is None or str(c.get("建立時間") or "") > vtime:
            # 10-15 第2段審查修正（必修 2）：拿掉「第 5 步新增的一律還沒生成」——重做之後這一版沒有它的紀錄（例如新增的消音
            # 又改回還原），以前永遠「還沒生成」、「重做這幾張」不會消失；現在只看是不是這一版之後才出現的卡
            state = needs.NOT_MADE   # 新增的、或這一版之後才出現的卡
        else:
            continue   # 第 3 步選定不修改的卡（這一版沒有處理它）：不列在清單（收在「第 3 步選定不修改」那一頁）
        rks = sorted(per.get(k) or [])
        recs = [recs_by_key[x] for x in rks if x in recs_by_key]
        step3_changed = False
        if row and state != EDITED and vprints.get(k) and recs and c["狀態"] == needs.ALIVE:
            # 10-15 第2段審查修正（第 6 條，暫定這一段做）：在第 3 步改過、還沒做進這一版的卡——照組裝時算卡片指紋的同一個算法
            # （`needs._card_prints`：這一版處理紀錄的內容指紋＋第 3 步決定裡會影響聲音的欄位）重算，跟這一版記的不同＝已改，等重做
            # 10-15 第2段審查二修正：直接用 `needs._card_prints`（組裝時算卡片指紋的那一支）——自己重算時兩筆同類型同起點
            # （紀錄鍵一樣）會對不上、永遠「已改」
            if now_prints is None:
                now_prints = needs._card_prints(st, log, (v or {}).get("紀錄卡號") or {})
            if now_prints.get(k) != vprints[k]:
                state, step3_changed = EDITED, True
        uk = un_of.get(k)
        info = _rec_info(recs)
        why = (row or {}).get("退回原因") if state == needs.RETURNED else None
        note = ""
        if c["類型"] == needs.UNLOGGED:
            note = "聲音變了但沒有紀錄：聽一下，正常就按通過"
        elif c["類型"] == needs.UNCLEAR:
            fid = (c.get("舊檔鍵") or {}).get("整片退回")
            note = flags_text.get(fid) or "；".join(str(r.get("做了什麼") or r.get("類型") or "") for r in recs) or "不確定是哪一種"
        flag_ids = [x["id"] for x in check.get("整片退回") or [] if
                    (c.get("舊檔鍵") or {}).get("整片退回") == x["id"]] + flag_of.get(k, [])
        out.append({
            "id": k, "卡號": k, "編號": needs._card_num(k), "類型": c["類型"], "第3步鍵": c.get("第3步鍵"),
            "狀態": state, "退回原因": why, "沿用自": (row or {}).get("沿用自") if state == needs.PASSED else None,
            "原片": c.get("原片") or _span(recs, "原片") or (un_by_key.get(uk) or {}).get("原片"),
            "成品": (row or {}).get("成品") or _span(recs, "成品"),
            "紀錄鍵": rks, "未登記鍵": [uk] if uk else [], "整片退回": flag_ids,
            "第幾版": gen_round.get(k) if gen_round.get(k, 1) > 1 else None,
            "舊紀錄": c.get("來源") in (needs.SRC_UPGRADE, needs.SRC_ASSEMBLE), "說明": note, **info,
            "刪掉了": c["狀態"] != needs.ALIVE, "第3步改過": step3_changed,
        })
    return out


# ---------- 退回讀舊檔 ----------

def _from_old(workdir: Path, log: dict | None, check: dict, index: dict, flags_text: dict) -> list[dict]:
    groups: dict[str, list[str]] = {}
    recs = (log or {}).get("紀錄", [])
    by_key = {fc.record_key(r): r for r in recs}
    for r in recs:
        rk = fc.record_key(r)
        ks = []
        for k in r.get("覆核項目") or []:
            k3 = (index.get(k) or {}).get("第3步") or k
            if k3 not in ks:
                ks.append(k3)
        for k in ks or [f"紀錄:{rk}"]:
            groups.setdefault(k, []).append(rk)
    out = []
    items = check.get("逐筆") or {}
    for gid, rks in groups.items():
        res = [(items.get(x) or {}).get("結果") for x in rks]
        state = legacy_state(res)
        why = "；".join((items.get(x) or {}).get("原因", "") for x in rks if (items.get(x) or {}).get("結果") == fc.REDO)
        rr = [by_key[x] for x in rks]
        step3 = not gid.startswith("紀錄:")
        out.append({"id": gid, "卡號": None, "編號": None, "類型": gid.split(":", 1)[0] if step3 else needs.UNCLEAR,
                    "第3步鍵": gid if step3 else None, "狀態": state,
                    "退回原因": {"選項": [], "補充": why} if state == needs.RETURNED else None, "沿用自": None,
                    "原片": _span(rr, "原片"), "成品": _span(rr, "成品"), "紀錄鍵": rks, "未登記鍵": [], "整片退回": [],
                    "第幾版": None, "舊紀錄": False,
                    "說明": "" if step3 else "；".join(str(r.get("做了什麼") or r.get("類型") or "") for r in rr),
                    **_rec_info(rr), "刪掉了": False, "第3步改過": False})
    plist = (log or {}).get("片段")
    un_dec = check.get("未登記確認") or {}
    for u in (log or {}).get("未登記的變動", []):
        uk = fc.unlogged_key(u)
        d = (un_dec.get(uk) or {}).get("結果")
        state = needs.PASSED if d == fc.UNLOGGED_OK else needs.RETURNED if d == fc.REDO else needs.NOT_SEEN
        t = fc.to_output_time(u["原片"][0], plist)
        out.append({"id": f"沒登記:{uk}", "卡號": None, "編號": None, "類型": needs.UNLOGGED, "第3步鍵": None, "狀態": state,
                    "退回原因": {"選項": [], "補充": (un_dec.get(uk) or {}).get("原因", "")} if state == needs.RETURNED else None,
                    "沿用自": None, "原片": u["原片"], "成品": [t, t] if t is not None else None, "紀錄鍵": [], "未登記鍵": [uk],
                    "整片退回": [], "第幾版": None, "舊紀錄": False, "說明": "聲音變了但沒有紀錄：聽一下，正常就按通過",
                    "要人聽": False, "可重新生成": False, "刪掉了": False, "第3步改過": False})
    for x in check.get("整片退回") or []:
        out.append({"id": f"整片:{x['id']}", "卡號": None, "編號": None, "類型": needs.UNCLEAR, "第3步鍵": None,
                    "狀態": needs.RETURNED, "退回原因": {"選項": [], "補充": x.get("原因", "")}, "沿用自": None,
                    "原片": [x.get("原片秒"), x.get("原片秒")], "成品": [x.get("成品秒"), x.get("成品秒")], "紀錄鍵": [],
                    "未登記鍵": [], "整片退回": [x["id"]], "第幾版": None, "舊紀錄": False,
                    "說明": flags_text.get(x["id"]) or "不確定是哪一種", "要人聽": False, "可重新生成": False, "刪掉了": False, "第3步改過": False})
    return out


def _sort_key(c: dict):
    o = c.get("原片") or [None]
    return (o[0] if o[0] is not None else 1e18, c.get("編號") or 0, str(c["id"]))


def _mark_shared(cards: list[dict], check: dict) -> None:
    """10-15 第2段審查修正（必修 1）：共用處理紀錄的卡（改 cards）。
    - `共用`：跟這張卡共用處理紀錄的別張卡 id；
    - `連帶自`：這張卡是退回，可是退回的那幾筆都是別張卡退回的（`各卡原因` 裡沒有這張）→ [{id, 編號}]，畫面標「連帶」、
      寫「跟 #N 共用，那張還是退回」。舊檔沒記 `各卡原因` 的（舊工具、升級前的）不算連帶。"""
    rows = check.get("逐筆") or {}
    by_rk: dict[str, list[dict]] = {}
    for c in cards:
        for rk in c.get("紀錄鍵") or []:
            by_rk.setdefault(rk, []).append(c)
    by_owner = {owner_key(c): c for c in cards}
    for c in cards:
        mates = []
        for rk in c.get("紀錄鍵") or []:
            mates += [x["id"] for x in by_rk.get(rk, []) if x is not c and x["id"] not in mates]
        c["共用"] = mates
        c["連帶自"] = []
        if c["狀態"] != needs.RETURNED or not mates:
            continue
        backs = [rows.get(rk) or {} for rk in c.get("紀錄鍵") or []]
        backs = [d for d in backs if d.get("結果") == fc.REDO]
        un_back = any(((check.get("未登記確認") or {}).get(u) or {}).get("結果") == fc.REDO for u in c.get("未登記鍵") or [])
        if not backs or c.get("整片退回") or un_back:   # 整片退回、沒登記的變動退回：是這張卡自己的
            continue
        owners: list[str] = []
        for d in backs:
            per = d.get("各卡原因")
            if not per:
                owners = []
                break
            owners += [o for o in per if o not in owners]
        if owners and owner_key(c) not in owners:
            c["連帶自"] = [{"id": by_owner[o]["id"], "編號": by_owner[o].get("編號")} for o in owners if o in by_owner]


def build(workdir: str | Path, *, log: dict | None = None, check: dict | None = None, index: dict | None = None) -> dict:
    """第 5 步的卡片清單（不含第 3 步卡片的內容）：{來源: 新紀錄／舊檔, 卡片: [...], 統計}。只讀。
    10-15 第2段審查修正（建議，效能）：index＝`review.item_index`（呼叫的地方算好了就傳進來，不重算）。"""
    workdir = Path(workdir)
    log = proclog.load(workdir) if log is None else log
    check = fc.refresh(fc.load_check(workdir), log) if check is None else check
    flags_text = {x["id"]: x.get("原因", "") for x in check.get("整片退回") or []}
    index = fc._index(workdir) if index is None else index
    cards = _from_needs(workdir, log, check, flags_text, index) if needs.healthy(workdir) else None
    src = SRC_NEW
    if cards is None:
        src, cards = SRC_OLD, _from_old(workdir, log, check, index, flags_text)
    cards.sort(key=_sort_key)
    _mark_shared(cards, check)
    return {"來源": src, "卡片": cards, "統計": summary(cards)}


# 第 5 步卡片要用到的第 3 步資料（卡片元件 cards.js 的 data()）
STEP3_FIELDS = ("學員", "這一集代號", "選項", "代號選項", "代號分組", "代號對照", "影片")


def page_part(workdir: str | Path, page: dict | None, *, running: bool = False, index: dict | None = None) -> dict:
    """`GET /api/final` 多給的：卡片清單、卡片要的第 3 步資料、退回原因選項、第 4 步是不是正在跑。page＝第 3 步的
    `review.page_data`（呼叫的地方算好的，算的時候不寫舊檔）；None＝算不出來（卡片照樣列，只是不能按「改」）。"""
    from bookclub import config

    part = build(workdir, index=index)
    step3 = {k: (page or {}).get(k) for k in STEP3_FIELDS}
    keys = {c["第3步鍵"] for c in part["卡片"] if c.get("第3步鍵")}
    items = list((page or {}).get("項目") or [])
    have = {_key(it) for it in items}
    items += [it for it in (page or {}).get("已還原") or [] if _key(it) in keys and _key(it) not in have]
    step3["項目"] = items
    for c in part["卡片"]:
        c["有第3步卡片"] = bool(c.get("第3步鍵") and c["第3步鍵"] in {_key(it) for it in items})
    return {"卡片來源": part["來源"], "卡片": part["卡片"], "卡片統計": part["統計"], "第3步": step3,
            "退回原因選項": list(needs.REASON_OPTIONS), "AI執行中": bool(running),
            "開發者": config.developer()}   # 10-15 第2段審查修正：開發者設定開著才顯示「先照舊的紀錄顯示」這類小字


def find(workdir: str | Path, card_id: str, *, index: dict | None = None) -> dict:
    cards = build(workdir, index=index)["卡片"]
    c = next((x for x in cards if str(x["id"]) == str(card_id)), None)
    if c is None:
        raise KeyError(f"沒有這張卡：{card_id}")
    return c


def decide(workdir: str | Path, card_id: str, result: str | None, options: list[str] | None = None, extra: str = "") -> dict:
    """`POST /api/final/card`：一張卡通過／退回／改回還沒看（result：通過、退回、None）。
    回寫舊檔這張卡對到的每一筆（`finalcheck.decide_many`），新紀錄由舊檔存檔後重算（雙寫照第 1 段），退回再補上選項。"""
    if result not in (needs.PASSED, needs.RETURNED, None):
        raise ValueError("只能選：通過、退回")
    workdir = Path(workdir)
    index = fc._index(workdir)   # 10-15 第2段審查修正（建議，效能）：前後兩次 build 共用同一份
    cards = build(workdir, index=index)["卡片"]
    c = next((x for x in cards if str(x["id"]) == str(card_id)), None)
    if c is None:
        raise KeyError(f"沒有這張卡：{card_id}")
    if c["狀態"] in (needs.NOT_MADE, EDITED):
        raise ValueError("這張卡還沒做進成品（改過或新增的）：按「重做這幾張」做好之後再看")
    text = needs.reason_text(options, extra) if result == needs.RETURNED else ""
    old = {needs.PASSED: fc.PASS, needs.RETURNED: fc.REDO, None: None}[result]
    # 10-15 第2段審查修正（必修 1）：每一筆處理紀錄還有哪幾張（別的）卡也對到它
    sharers: dict[str, list[str]] = {}
    for x in cards:
        if x is c:
            continue
        for rk in x.get("紀錄鍵") or []:
            sharers.setdefault(rk, []).append(owner_key(x))
    res = fc.decide_many(workdir, c["紀錄鍵"], old, text, unlogged=c["未登記鍵"], flags=c["整片退回"],
                         owner=owner_key(c), sharers=sharers)
    if result == needs.RETURNED and c.get("卡號"):
        try:
            needs.set_reason(workdir, c["卡號"], options, extra)
        except Exception as e:  # noqa: BLE001 — 新紀錄出錯不擋第 5 步（舊檔已經寫好）
            needs.note_error(workdir, "第5步存檔", e)
    part = build(workdir, index=index)
    after = next((x for x in part["卡片"] if str(x["id"]) == str(card_id)), None)   # 整片退回的待釐清卡通過後就不在了
    by_owner = {owner_key(x): x for x in part["卡片"]}
    skipped = []   # 通過／取消時沒改的共用筆：哪幾張卡還退回著它
    for rk, owners in (res.get("跳過") or {}).items():
        for o in owners:
            x = by_owner.get(o)
            if x and not any(s["id"] == x["id"] for s in skipped):
                skipped.append({"id": x["id"], "編號": x.get("編號")})
    # 共用處理紀錄的別張卡（狀態可能跟著變：連帶退回、或那一筆通過了）——畫面換成新的
    mate_ids = set(c.get("共用") or [])
    mates = [x for x in part["卡片"] if x["id"] in mate_ids]
    return {"ok": True, "卡片": after, "統計": part["統計"], "狀態": res["狀態"], "跳過": skipped, "共用卡片": mates}


def edited(workdir: str | Path, key: str, new: bool = False, *, page: dict | None = None, snap: dict | None = None) -> dict:
    """`POST /api/final/edited`：第 5 步按「改」改了第 3 步的東西之後，那張卡標「已改，等重做」。舊檔模式不記（回 記了: False；
    畫面照「舊檔模式」一律顯示「重做這幾張」，見 finalcheck.js `fcRedoCount`）。
    10-15 第2段審查修正（建議，效能）：page／snap＝伺服器剛算好的第 3 步資料與快照（同步卡片用這一份，不另外算）。"""
    workdir = Path(workdir)
    if not needs.healthy(workdir):
        return {"ok": True, "記了": False}
    no = needs.mark_edited(workdir, str(key), new=bool(new), page=page, snap=snap)
    return {"ok": True, "記了": bool(no), "卡號": no}


def export_versions(workdir: str | Path) -> dict | None:
    """拿檔按鈕旁那一行要的：上次輸出的是第幾版、最新是第幾版（新紀錄不能用、沒輸出過、對不到版本＝None）。"""
    workdir = Path(workdir)
    if not needs.healthy(workdir):
        return None
    out = fc.load_check(workdir).get("輸出成品") or {}
    st = needs.load(workdir)
    v = st.version_at(out.get("處理紀錄產生時間")) if out.get("處理紀錄產生時間") else None
    last = st.latest_version()
    if v is None or last is None:
        return None
    return {"輸出版本": v["版本號"], "最新版本": last["版本號"]}
