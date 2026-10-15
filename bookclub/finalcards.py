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


# 10-15 宇軒驗證回饋：AI 重新生成聲音的紀錄類型（學員重念、名字整句換掉、換聲音、學員名字換代號）——卡片上列「原本／改成」兩行
GEN_TEXT_KINDS = ("學員重念", "名字整句換掉", "換聲音", "學員名字換代號")


def _rec_info(recs: list[dict]) -> dict:
    """一張卡對到的處理紀錄 → 畫面要的幾個小欄位。"""
    said: list[str] = []
    for r in recs:   # 10-15 宇軒驗證回饋：要念的字＝處理紀錄的「文字」（標字版的「餵給模型的文字」），同一張卡幾筆照順序、重複的不列
        t = str(r.get("文字") or "").strip()
        if r.get("類型") in GEN_TEXT_KINDS and t and t not in said:
            said.append(t)
    return {"要人聽": any(fc.needs_look(r) for r in recs),
            # 有生成檔＝退回會換一種念法重新生成；沒有（剪掉、消音、停格、空隙）＝重做只會重新放一次（31 號設計 §6.4）
            "可重新生成": any(fc.gen_id(r) or r.get("檔案") for r in recs),
            "要念的字": said}


def legacy_state(results: list[str | None]) -> str:
    """（純函式）舊檔一張卡對到的每一筆判斷 → 卡片狀態：任一筆退回＝退回、每一筆都通過＝通過、其他＝還沒看（第 1 段定的對法）。"""
    if any(r == fc.REDO for r in results):
        return needs.RETURNED
    if results and all(r == fc.PASS for r in results):
        return needs.PASSED
    return needs.NOT_SEEN


def summary(cards: list[dict]) -> dict:
    """（純函式）頂端那一句要的數字：還沒看幾張、要重做幾張（退回＋已改＋還沒生成）、全部幾張。"""
    cards = [c for c in cards if not c.get("這一版沒處理到")]   # 10-15 第5步2.0 第3段：範圍外的不算
    return {"全部": len(cards), "還沒看": sum(1 for c in cards if c["狀態"] == needs.NOT_SEEN),
            "要重做": sum(1 for c in cards if c["狀態"] in REDO_STATES),
            "退回": sum(1 for c in cards if c["狀態"] == needs.RETURNED),
            "已改": sum(1 for c in cards if c["狀態"] == EDITED),
            "通過": sum(1 for c in cards if c["狀態"] == needs.PASSED)}


# ---------- 新紀錄 ----------

def _older_row(st: needs.Store, k: str, n: int) -> tuple[int, dict] | None:
    """10-15 第5步2.0 第3段：第 n 版之前、最近一個有這張卡狀態列的版本（版本號, 狀態列）。"""
    for m in sorted((x for x in st.versions if x < n), reverse=True):
        row = st.states.get((k, m))
        if row:
            return m, row
    return None


def _prev_take(workdir: Path, st: needs.Store, k: str, n: int, videos: dict) -> dict | None:
    """10-15 第5步2.0 第3段（31 號設計 §6.3）：「上一版 ▶」——這張卡在第 n 版重做過（`這一版做了哪幾張`），播上一個有它的版本的
    成品同一張卡。上一版的影片清掉了、上一版沒有它在成品裡的時間 → None（不出現）。"""
    if k not in ((st.versions.get(n) or {}).get("這一版做了哪幾張") or []):
        return None
    old = _older_row(st, k, n)
    if not old:
        return None
    m, row = old
    o = row.get("成品") or [None]
    if o[0] is None:
        return None
    if m not in videos:
        videos[m] = needs.version_video(workdir, st.versions.get(m))
    return {"版本號": m, "成品": row["成品"]} if videos[m] else None


def _step3_turned_on(st: needs.Store, k: str, c: dict, v: dict, hist: list | None) -> bool:
    """10-15 第5步2.0 第3段（第 2 段報告第 7 條）：第 3 步選定不修改（這一版沒有它的處理紀錄）、之後在第 3 步改成要處理的卡。
    這一版記了 `其他卡指紋`（這一段起組裝的版本）→ 現在的聲音指紋跟當時不同；沒記（舊的版本）→ 看歷史：這一版之後
    「改了第3步決定」、改的欄位有會影響聲音的。"""
    others = v.get("其他卡指紋")
    if others is not None:
        return k in others and others[k] != needs._sound_print(c)
    since = str(v.get("建立時間") or "")
    for h in hist or []:
        if h.get("卡號") == k and h.get("動作") == "改了第3步決定" and str(h.get("時間") or "") > since:
            if any(f not in needs.NOT_SOUND_KEYS for f in ((h.get("後") or {}).get("欄位") or [])):
                return True
    return False


def _from_needs(workdir: Path, log: dict | None, check: dict, flags_text: dict, index: dict,
                cross: list | None = None) -> list[dict] | None:
    """新紀錄 → 卡片。對不到這一版處理紀錄（版本表裡沒有這一份）＝None（呼叫的地方改讀舊檔）。
    10-15 第5步2.0 第3段：
    - 這一版範圍外（開發者用範圍組裝）、之前的版本處理過的卡：舊版還沒看／退回的 → 放進 cross（畫面「上一版還沒看的（第 N 版）」，
      狀態、成品時間照那一版）；其他 → 「這一版沒處理到」（不能按通過、不算進要重做；畫面標「已通過（沿用上一版）」，
      10-15 宇軒驗證回饋三、四）。10-15 宇軒驗證回饋四：在最新版按「要修改」之後（等重做，起因改了）→ 已改，等重做＋「範圍外改了」
    - 第 3 步選定不修改、之後在第 3 步改成要處理的 → 已改，等重做（小字「在第 3 步改過」）
    - 重做過的卡帶「上一版」（上一版 ▶ 試聽）"""
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
    videos: dict = {}
    hist: list | None = None
    vlogs: dict[int, dict] = {}   # 10-15 宇軒驗證回饋四：之前版本的處理紀錄（範圍外的卡用，同一版只讀一次）
    if v is not None and v.get("其他卡指紋") is None:
        hist = needs.read_history(workdir)   # 舊的版本沒記其他卡指紋：看歷史（只有這種版本才讀）
    for k in sorted(st.cards, key=needs._card_num):
        c = st.cards[k]
        row = st.states.get((k, vn)) if vn else None
        pend = c.get("等重做") or {}
        if c["狀態"] != needs.ALIVE and not pend:
            continue   # 刪掉、併入、拆成、消失的：沒有等重做就不列（刪掉的待釐清卡＝整片退回取消了）
        outside = False
        prev_ok = None   # 10-15 宇軒驗證回饋三：範圍外的卡，之前最近那一版是不是通過（畫面標「上一版已通過」）
        turned_on = False
        older = None     # 10-15 宇軒驗證回饋四：範圍外的卡，之前最近一個有它的版本（版本號, 狀態列）——念的字、能不能重新生成照那一版
        out_edit = False   # 10-15 宇軒驗證回饋四：範圍外、在最新版按「要修改」的卡（已改，等重做）
        if pend.get("起因") == needs.WHY_EDITED:
            state = EDITED
            if not row and v is not None and str(c.get("建立時間") or "") <= vtime and not needs._in_range(c, v):
                older = _older_row(st, k, vn)
                out_edit = older is not None
        elif not row and v is not None and str(c.get("建立時間") or "") <= vtime and not needs._in_range(c, v):
            # 10-15 第5步2.0 第3段：這一版範圍外
            old = _older_row(st, k, vn)
            if old is None:
                continue
            m, orow = old
            if orow.get("狀態") in (needs.NOT_SEEN, needs.RETURNED) and c["狀態"] == needs.ALIVE and cross is not None:
                cross.append(_version_card(workdir, st, k, m, orow, videos))
                continue
            state, outside, older = needs.NOT_MADE, True, old
            prev_ok = orow.get("狀態") == needs.PASSED
        elif not row and v is not None and str(c.get("建立時間") or "") <= vtime and c["狀態"] == needs.ALIVE \
                and _step3_turned_on(st, k, c, v, hist):
            state, turned_on = EDITED, True
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
        if older and not recs:
            # 10-15 宇軒驗證回饋四：範圍外的卡在最新版可以按「要修改」——要念的字、能不能換一種念法照之前有它的那一版的處理紀錄
            m, orow = older
            if m not in vlogs:
                vlogs[m] = {fc.record_key(r): r for r in _version_log(workdir, st.versions.get(m)).get("紀錄") or []}
            info = _rec_info([vlogs[m][x] for x in orow.get("紀錄鍵") or [] if x in vlogs[m]])
        # 10-15 要修改合併（1015-4）：「要修改」先記原因（退回）再套用修改（已改，等重做）——已改的卡照樣帶這一版記的原因
        why = (row or {}).get("退回原因") if state == needs.RETURNED or (state == EDITED and (row or {}).get("狀態") == needs.RETURNED) else None
        if state == EDITED and not why and pend.get("退回原因"):
            why = pend["退回原因"]   # 10-15 宇軒驗證回饋四：範圍外的卡按「要修改」記的原因（這一版沒有它的狀態列，記在等重做）
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
            "刪掉了": c["狀態"] != needs.ALIVE, "第3步改過": step3_changed or turned_on,
            "這一版沒處理到": outside, "上一版已通過": prev_ok, "範圍外改了": out_edit, "版本號": vn,
            "上一版": _prev_take(workdir, st, k, vn, videos) if vn and row else None,
        })
    return out


def _version_log(workdir: Path, v: dict | None) -> dict:
    """那一版的處理紀錄副本（讀不到＝空的）。"""
    if not v or not v.get("處理紀錄"):
        return {}
    try:
        from bookclub import workdir as wd

        return wd.read_json(Path(workdir) / v["處理紀錄"], default=None) or {}
    except (OSError, ValueError):
        return {}


def _version_card(workdir: Path, st: needs.Store, k: str, n: int, row: dict | None, videos: dict,
                  vlog: dict | None = None, outside: bool = False) -> dict:
    """10-15 第5步2.0 第3段：一張卡在第 n 版的樣子（舊版畫面、「上一版還沒看的」那一區用）。"""
    c = st.cards[k]
    v = st.versions.get(n) or {}
    if vlog is None:
        vlog = _version_log(workdir, v)
    rks = sorted((row or {}).get("紀錄鍵") or [])
    by = {fc.record_key(r): r for r in vlog.get("紀錄") or []}
    recs = [by[x] for x in rks if x in by]
    state = (row or {}).get("狀態") or needs.NOT_MADE
    note = ""
    if c["類型"] == needs.UNLOGGED:
        note = "聲音變了但沒有紀錄：聽一下，正常就按通過"
    elif c["類型"] == needs.UNCLEAR:
        note = "；".join(str(r.get("做了什麼") or r.get("類型") or "") for r in recs) or "不確定是哪一種"
    return {"id": k, "卡號": k, "編號": needs._card_num(k), "類型": c["類型"], "第3步鍵": c.get("第3步鍵"),
            "狀態": state, "退回原因": (row or {}).get("退回原因") if state == needs.RETURNED else None,
            "沿用自": (row or {}).get("沿用自") if state == needs.PASSED else None,
            "原片": c.get("原片") or _span(recs, "原片"), "成品": (row or {}).get("成品") or _span(recs, "成品"),
            "紀錄鍵": rks, "未登記鍵": [], "整片退回": [], "第幾版": None,
            "舊紀錄": c.get("來源") in (needs.SRC_UPGRADE, needs.SRC_ASSEMBLE), "說明": note, **_rec_info(recs),
            "刪掉了": c["狀態"] != needs.ALIVE, "第3步改過": False, "這一版沒處理到": outside, "版本號": n,
            "上一版": _prev_take(workdir, st, k, n, videos) if row else None}


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
                    "要人聽": False, "可重新生成": False, "要念的字": [], "刪掉了": False, "第3步改過": False})
    for x in check.get("整片退回") or []:
        out.append({"id": f"整片:{x['id']}", "卡號": None, "編號": None, "類型": needs.UNCLEAR, "第3步鍵": None,
                    "狀態": needs.RETURNED, "退回原因": {"選項": [], "補充": x.get("原因", "")}, "沿用自": None,
                    "原片": [x.get("原片秒"), x.get("原片秒")], "成品": [x.get("成品秒"), x.get("成品秒")], "紀錄鍵": [],
                    "未登記鍵": [], "整片退回": [x["id"]], "第幾版": None, "舊紀錄": False,
                    "說明": flags_text.get(x["id"]) or "不確定是哪一種", "要人聽": False, "可重新生成": False, "要念的字": [], "刪掉了": False, "第3步改過": False})
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
    cross: list[dict] = []
    cards = _from_needs(workdir, log, check, flags_text, index, cross) if needs.healthy(workdir) else None
    src = SRC_NEW
    if cards is None:
        src, cards, cross = SRC_OLD, _from_old(workdir, log, check, index, flags_text), []
    cards.sort(key=_sort_key)
    _mark_shared(cards, check)
    cross.sort(key=lambda c: (-int(c["版本號"]), _sort_key(c)))
    for c in cross:
        c["共用"], c["連帶自"], c["跨版本"] = [], [], True   # 跨版本：狀態、成品時間是那一版的（畫面試聽播那一版的成品）
    return {"來源": src, "卡片": cards, "統計": summary(cards), "跨版本卡片": cross}


# 第 5 步卡片要用到的第 3 步資料（卡片元件 cards.js 的 data()）
STEP3_FIELDS = ("學員", "這一集代號", "選項", "代號選項", "代號分組", "代號對照", "影片")


def _step3_part(page: dict | None, cards: list[dict]) -> dict:
    """卡片元件要的第 3 步資料（項目＝第 3 步卡片＋清單上有、已經還原的那幾張）；每張卡記 `有第3步卡片`（改 cards）。"""
    step3 = {k: (page or {}).get(k) for k in STEP3_FIELDS}
    keys = {c["第3步鍵"] for c in cards if c.get("第3步鍵")}
    items = list((page or {}).get("項目") or [])
    have = {_key(it) for it in items}
    items += [it for it in (page or {}).get("已還原") or [] if _key(it) in keys and _key(it) not in have]
    step3["項目"] = items
    now = {_key(it) for it in items}
    for c in cards:
        c["有第3步卡片"] = bool(c.get("第3步鍵") and c["第3步鍵"] in now)
    return step3


def version_list(workdir: str | Path, st: needs.Store | None = None) -> list[dict]:
    """10-15 第5步2.0 第3段（31 號設計 §6.5）：「看哪一版」選單的每一項（新的在前）：版本號、時間、範圍標記、有沒有影片、是不是最新。
    新紀錄不能用時是空的（不出現選單）。"""
    workdir = Path(workdir)
    if st is None:
        if not needs.healthy(workdir):
            return []
        st = needs.load(workdir)
    top = max(st.versions) if st.versions else None
    return [{"版本號": n, "時間": v.get("時間"), "標記": v.get("標記"), "範圍": v.get("範圍"), "最新": n == top,
             "有影片": bool(needs.version_video(workdir, v))} for n, v in sorted(st.versions.items(), reverse=True)]


def _export_info(workdir: Path, check: dict, st: needs.Store) -> dict | None:
    """最新一次輸出是第幾版（記了 `版本號` 用它；舊紀錄照處理紀錄產生時間對）。沒輸出過＝None。"""
    out = check.get("輸出成品") or {}
    if not out.get("檔案"):
        return None
    no = out.get("版本號")
    if no is None:
        v = st.version_at(out.get("處理紀錄產生時間"))
        no = v["版本號"] if v else None
    return {"輸出版本": no, "最新版本": max(st.versions) if st.versions else None, "紀錄": out}


def page_part(workdir: str | Path, page: dict | None, *, running: bool = False, index: dict | None = None) -> dict:
    """`GET /api/final` 多給的：卡片清單、卡片要的第 3 步資料、退回原因選項、第 4 步是不是正在跑。page＝第 3 步的
    `review.page_data`（呼叫的地方算好的，算的時候不寫舊檔）；None＝算不出來（卡片照樣列，只是不能按「改」）。
    10-15 第5步2.0 第3段：多給版本選單（`版本清單`、`目前版本`、`最新版本`）、`跨版本卡片`（上一版還沒看的）；
    新紀錄模式下「要重新輸出」改成看輸出的版本號跟最新版本號（31 號設計 §6.7），`輸出成品過期` 照這個算。"""
    from bookclub import config

    workdir = Path(workdir)
    part = build(workdir, index=index)
    step3 = _step3_part(page, part["卡片"] + part["跨版本卡片"])
    out = {"卡片來源": part["來源"], "卡片": part["卡片"], "卡片統計": part["統計"], "第3步": step3,
           "跨版本卡片": part["跨版本卡片"], "退回原因選項": list(needs.REASON_OPTIONS), "AI執行中": bool(running),
           "開發者": config.developer(),   # 10-15 第2段審查修正：開發者設定開著才顯示「先照舊的紀錄顯示」這類小字
           "版本清單": [], "目前版本": None, "最新版本": None, "看的是舊版": False}
    if part["來源"] == SRC_NEW:
        st = needs.load(workdir)
        log = proclog.load(workdir)
        cur = st.version_at((log or {}).get("產生時間")) if log else None
        out.update({"版本清單": version_list(workdir, st), "目前版本": cur["版本號"] if cur else None,
                    "最新版本": max(st.versions) if st.versions else None})
        ex = _export_info(workdir, fc.load_check(workdir), st)
        # 10-15 第3段審查修正（必修 2）：輸出紀錄對不到任何版本（在 0.2.1b3 輸出過、又更新成品、才升級）→ 不覆寫，
        # 照 0.2.1b3 的過期判斷（finalcheck 算好的 `輸出成品過期`）
        if ex and ex["輸出版本"] is not None:   # §6.7：上次輸出的版本號 ≠ 最新版本號 → 要重新輸出（不再比檔案大小與修改時間）
            stale = ex["輸出版本"] != ex["最新版本"]
            out["輸出成品過期"] = ({"原因": "版本", "說明": f"這是第 {ex['輸出版本']} 版的，最新是第 {ex['最新版本']} 版"}
                             if stale else None)
    return out


def version_page(workdir: str | Path, n: int, page: dict | None, *, running: bool = False) -> dict:
    """10-15 第5步2.0 第3段（31 號設計 §6.5）：`GET /api/final?版本=N` 看舊版（不是最新處理紀錄那一版）的整頁資料。
    卡片清單照舊全部顯示：那一版有處理的照當時的判斷；沒處理到的（別的範圍、之後才有的）變淡、標「這一版沒處理到」。
    影片是那一版的成品（`/api/final/video?版本=N`），看過區段是那一版自己的一份（`修改需求/看過.json`）。只讀。"""
    from bookclub import config

    workdir = Path(workdir)
    if not needs.healthy(workdir):
        raise ValueError("新紀錄現在不能用，只能看最新一版")
    st = needs.load(workdir)
    v = st.versions.get(int(n))
    if v is None:
        raise KeyError(f"沒有第 {n} 版")
    n = int(n)
    vlog = _version_log(workdir, v)
    videos: dict = {}
    cards = []
    for k in sorted(st.cards, key=needs._card_num):
        c = st.cards[k]
        row = st.states.get((k, n))
        if row:
            cards.append(_version_card(workdir, st, k, n, row, videos, vlog))
        elif c["狀態"] == needs.ALIVE and any(kk == k for (kk, _m) in st.states):
            oc = _version_card(workdir, st, k, n, None, videos, vlog, outside=True)
            # 10-15 宇軒驗證回饋三：之前的版本通過過才標「上一版已通過」；之後才有的、之前沒通過的標「這一版沒做這張」
            old = _older_row(st, k, n)
            oc["上一版已通過"] = bool(old and old[1].get("狀態") == needs.PASSED)
            cards.append(oc)
    for oc in cards:
        # 10-15 宇軒驗證回饋六：舊版上按過「要修改」（或在最新版按過、範圍外的）的卡——原因記在卡片的等重做（needs.mark_outside_redo），
        # 這一版的狀態列不動；畫面照卡片現在的樣子標「要重做」、帶上次的原因（再按「要修改」預填）
        pend = st.cards[oc["卡號"]].get("等重做") or {}
        if pend.get("起因") == needs.WHY_EDITED and pend.get("退回原因") is not None:
            oc.update({"狀態": EDITED, "退回原因": pend["退回原因"], "沿用自": None, "這一版沒處理到": False, "範圍外改了": True})
    cards.sort(key=_sort_key)
    for c in cards:
        c["共用"], c["連帶自"] = [], []
    rel = needs.version_video(workdir, v)
    length = 0.0
    if rel:
        length = round(fc.probe_duration(workdir / rel), 3)
    seen = fc.clip_ranges(needs.watched(workdir, n), length) if length else []
    ratio = fc.watched_ratio(seen, length) if length else 0.0
    outs = v.get("輸出紀錄") or []
    try:
        from bookclub import epcodes

        labels = epcodes.student_labels(workdir)
    except Exception:  # noqa: BLE001
        labels = {}
    top = max(st.versions)
    return {
        "有處理紀錄": True, "來源": v.get("來源"), "範圍": v.get("範圍"), "處理紀錄產生時間": v.get("時間"),
        "成品影片": rel, "成品長度": length, "影片網址": f"/api/final/video?版本={n}" if rel else None,
        "成品版本": fc.product_version(workdir / rel) if rel else None, "片段": vlog.get("片段"),
        "看過區段": seen, "狀態": {"看過比例": round(ratio, 4)}, "輸出成品": outs[-1] if outs else None, "輸出成品過期": None,
        "不修改": [], "不修改類型": [], "需留意": None, "需留意類型": [], "學員顯示名": labels, "上次執行": None,
        "卡片來源": SRC_NEW, "卡片": cards, "卡片統計": summary(cards), "第3步": _step3_part(page, cards), "跨版本卡片": [],
        "退回原因選項": list(needs.REASON_OPTIONS), "AI執行中": bool(running), "開發者": config.developer(),
        "版本清單": version_list(workdir, st), "目前版本": n, "最新版本": top, "看的是舊版": True,
        "影片清掉了": not rel,
    }


def version_watched(workdir: str | Path, n: int, ranges: list) -> dict:
    """10-15 第5步2.0 第3段：舊版上播過的區段（那一版的成品時間）→ 記在那一版自己的看過區段（不寫成品檢查檔）。"""
    workdir = Path(workdir)
    st = needs.load(workdir)
    v = st.versions.get(int(n))
    rel = needs.version_video(workdir, v)
    if not rel:
        raise ValueError("這一版的影片清掉了")
    length = round(fc.probe_duration(workdir / rel), 3)
    seen = fc.merge_ranges(needs.watched(workdir, n) + [[float(a), float(b)] for a, b in ranges])
    seen = fc.clip_ranges(seen, length) if length else seen
    needs.set_watched(workdir, n, seen)
    ratio = fc.watched_ratio(seen, length) if length else 0.0
    return {"ok": True, "記了": True, "看過區段": seen, "看過比例": round(ratio, 4), "狀態": {"看過比例": round(ratio, 4)}}


def version_video(workdir: str | Path, n: int) -> Path:
    workdir = Path(workdir)
    rel = needs.version_video(workdir, needs.load(workdir).versions.get(int(n)))
    if not rel:
        raise FileNotFoundError("這一版的影片清掉了")
    return workdir / rel


def version_clip(workdir: str | Path, n: int, a: float, b: float) -> Path:
    """10-15 第5步2.0 第3段：第 n 版成品 a～b 秒（成品時間）的聲音——「上一版 ▶」、上一版還沒看的卡的「現在 ▶」播這個
    （不用換掉畫面上的影片）。存在 `web快取/成品檢查/`。"""
    import subprocess

    src = version_video(workdir, n)
    a, b = max(0.0, float(a)), float(b)
    if not b > a:
        raise ValueError("時間不對")
    cache = Path(workdir) / "web快取" / "成品檢查"
    cache.mkdir(parents=True, exist_ok=True)
    dst = cache / f"版本{int(n)}_{a:.2f}_{b:.2f}.wav"
    if not dst.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(src), "-vn",
                        str(dst)], check=True)
    return dst


def find(workdir: str | Path, card_id: str, *, index: dict | None = None) -> dict:
    cards = build(workdir, index=index)["卡片"]
    c = next((x for x in cards if str(x["id"]) == str(card_id)), None)
    if c is None:
        raise KeyError(f"沒有這張卡：{card_id}")
    return c


def current_version(workdir: str | Path) -> int | None:
    """最新處理紀錄是第幾版（新紀錄不能用、版本表裡沒有＝None）。"""
    workdir = Path(workdir)
    if not needs.healthy(workdir):
        return None
    return fc.version_of(workdir, proclog.load(workdir))


def decide(workdir: str | Path, card_id: str, result: str | None, options: list[str] | None = None, extra: str = "",
           version: int | None = None) -> dict:
    """`POST /api/final/card`：一張卡通過／退回／改回還沒看（result：通過、退回、None）。
    回寫舊檔這張卡對到的每一筆（`finalcheck.decide_many`），新紀錄由舊檔存檔後重算（雙寫照第 1 段），退回再補上選項。
    10-15 第5步2.0 第3段：version＝在第幾版上按的（舊版、或「上一版還沒看的」那一區）。不是最新處理紀錄那一版 → 記在那一版
    （`needs.decide_version`，不寫舊檔）；回傳 {ok, 卡片: 那一版的這張卡}，畫面整頁重新整理。
    10-15 宇軒驗證回饋六（第 3 段審查「必修 1」結案拿掉）：舊版、跨版本卡（version 不是最新那一版）按「要修改」（退回）→
    原因記在卡片自己的等重做（`needs.mark_outside_redo`，跟回饋四「已通過（沿用上一版）」同一條路），不寫進任何版本的成品檢查紀錄；
    卡片在最新版變「已改，等重做」、下次「重做這幾張」包含它。通過、取消照舊記在那一版（`needs.decide_version`）。"""
    if result not in (needs.PASSED, needs.RETURNED, None):
        raise ValueError("只能選：通過、退回")
    workdir = Path(workdir)
    if version is not None and int(version) != current_version(workdir):
        if not needs.healthy(workdir):
            raise ValueError("新紀錄現在不能用，只能在最新一版判斷")
        if result == needs.RETURNED:
            # 10-15 宇軒驗證回饋六：舊版、跨版本卡也能按「要修改」——原因記在卡片的等重做（不動那一版的狀態列、不寫成品檢查檔）
            needs.mark_outside_redo(workdir, str(card_id), options, extra)
            part = build(workdir)
            after = next((x for x in part["卡片"] + part["跨版本卡片"] if str(x["id"]) == str(card_id)), None)
            return {"ok": True, "卡片": after, "統計": part["統計"], "版本號": int(version), "跳過": [], "共用卡片": []}
        row = needs.decide_version(workdir, int(version), str(card_id), result, options, extra)
        st = needs.load(workdir)
        return {"ok": True, "卡片": _version_card(workdir, st, str(card_id), int(version), row, {}), "版本號": int(version)}
    index = fc._index(workdir)   # 10-15 第2段審查修正（建議，效能）：前後兩次 build 共用同一份
    cards = build(workdir, index=index)["卡片"]
    c = next((x for x in cards if str(x["id"]) == str(card_id)), None)
    if c is None:
        raise KeyError(f"沒有這張卡：{card_id}")
    if c.get("這一版沒處理到") or c.get("範圍外改了"):
        # 10-15 宇軒驗證回饋四：「已通過（沿用上一版）」的卡在最新版可以按「要修改」（發現改得不好還是要能送修改）——
        # 這一版沒有它的狀態列、成品檢查檔也沒有它，原因記在卡片的等重做（起因：改了），畫面變「要重做」，
        # 下次「重做這幾張」整支重做會包含它（`needs.build_version` 清掉、新版不沿用通過）。通過、取消仍不行（不是這一版做的）
        if result != needs.RETURNED or not c.get("卡號"):
            raise ValueError("這張是上一版通過的，這一版沒改，不用再判斷")   # 10-15 宇軒驗證回饋三
        needs.mark_outside_redo(workdir, c["卡號"], options, extra)
        part = build(workdir, index=index)
        after = next((x for x in part["卡片"] if str(x["id"]) == str(card_id)), None)
        return {"ok": True, "卡片": after, "統計": part["統計"], "狀態": part.get("狀態"), "跳過": [], "共用卡片": []}
    # 10-15 要修改合併（1015-4）：「要修改」的改字、改範圍存檔之後（卡片已經「已改，等重做」）還要記原因——
    # 已改的卡只要還在這一版成品裡（有對到處理紀錄），可以退回（記原因）；通過照舊不行
    in_product = c["狀態"] == EDITED and result == needs.RETURNED and bool(c.get("紀錄鍵"))
    if c["狀態"] in (needs.NOT_MADE, EDITED) and not in_product:
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
    if result is None and c.get("卡號") and c.get("版本號"):
        try:   # 10-15 第5步2.0 第3段：取消的是從更早一版沿用來的通過（舊檔沒有這一筆）→ 新紀錄也改回還沒看
            needs.uncarry(workdir, c["卡號"], int(c["版本號"]))
        except Exception as e:  # noqa: BLE001
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


def take(workdir: str | Path) -> dict | None:
    """10-15 第5步2.0 第3段（31 號設計 §6.7）：拿檔按鈕拿哪一支——「最新一次輸出」；沒輸出過拿最新一版的成品。
    回傳 {路徑（相對工作區）, 是: 最終成品／還在檢查的成品, 輸出版本, 最新版本}；新紀錄不能用＝None（照 0.2.1b3 的過期判斷）。
    不再比檔案大小、修改時間：那一版之後有新版本，拿檔按鈕旁寫「這是第 N 版的，最新是第 M 版」。"""
    workdir = Path(workdir)
    if not needs.healthy(workdir):
        return None
    st = needs.load(workdir)
    if not st.versions:
        return None
    ex = _export_info(workdir, fc.load_check(workdir), st)
    if ex and ex["輸出版本"] is None:
        # 10-15 第3段審查修正（必修 2）：輸出紀錄對不到任何版本 → 不知道它跟現在差幾版，回 None 走 0.2.1b3 的
        # `take_target`／`export_stale`（比來源檔指紋，舊了改拿現在的成品、標還沒重新輸出）
        return None
    if ex and (workdir / ex["紀錄"]["檔案"]).is_file():
        return {"路徑": ex["紀錄"]["檔案"], "是": "最終成品", "輸出版本": ex["輸出版本"], "最新版本": ex["最新版本"]}
    rel = needs.version_video(workdir, st.latest_version())
    if rel is None:
        return None
    return {"路徑": rel, "是": "還在檢查的成品", "輸出版本": None, "最新版本": max(st.versions)}


def export_version(workdir: str | Path, n: int, confirm: bool = False) -> dict:
    """10-15 第5步2.0 第3段（31 號設計 §6.5、§6.7）：輸出舊的那一版（選單選到哪一版就輸出哪一版）。規則照 0.2.1b3：沒看完也能輸出，
    先問一次（還有幾張沒看完）。那一版的影片清掉了不能輸出。輸出紀錄記到那一版（版本表），成品檢查檔照舊記最新一次輸出（雙寫）。"""
    import os
    import shutil
    from datetime import datetime

    from bookclub import workdir as wd

    workdir = Path(workdir)
    st = needs.load(workdir)
    v = st.versions.get(int(n))
    if v is None:
        raise KeyError(f"沒有第 {n} 版")
    rel = needs.version_video(workdir, v)
    if not rel:
        raise ValueError("這一版的影片清掉了")
    page = version_page(workdir, int(n), None)
    stat = page["卡片統計"]
    left = stat["還沒看"] + stat["退回"]
    pct = int(page["狀態"]["看過比例"] * 100)
    if (left or pct < 100) and not confirm:   # 10-15 第3段審查修正（建議）：卡都看完、整片沒看完也先問一次（照 0.2.1b3）
        why = ([f"還有 {left} 張沒看完"] if left else []) + ([f"整片還沒看完（看過 {pct}%）"] if pct < 100 else [])
        return {"ok": False, "要確認": True, "還差": {"說明": why, "擋下": [], "名字提醒": [], "退回清單": [], "提醒": [],
                                                  "需留意說明": "", "看過百分比": pct}}
    src = workdir / rel
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = src.with_name(fc.final_name(src.name, stamp))
    k = 2
    while dst.exists() or dst.is_symlink():
        dst = src.with_name(f"{Path(fc.final_name(src.name, stamp)).stem} ({k}){src.suffix}")
        k += 1
    tmp = dst.with_name(f".{dst.name}.輸出中")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)
    extras = wd.read_json(workdir / "工作區設定.json", default=None) or {}
    rec = {"時間": fc._now(), "來源": rel, "檔案": str(dst.relative_to(workdir)),
           "片頭": (extras.get("片頭") or {}).get("檔名"), "片尾": (extras.get("片尾") or {}).get("檔名"), "接上片頭片尾": False,
           "看過百分比": pct, "沒通過筆數": left, "沒確認變動筆數": 0, "檢查完才輸出": not left,
           "成品檔指紋": fc.product_print(src), "處理紀錄產生時間": v.get("時間"), "版本號": int(n)}
    with fc._lock:
        check = fc.load_check(workdir)
        check["輸出成品"] = rec
        check.setdefault("輸出紀錄", []).append(rec)
        fc._save(workdir, check)
    needs.add_export(workdir, int(n), rec)
    return {"ok": True, **rec, "第幾次": len(check["輸出紀錄"])}
