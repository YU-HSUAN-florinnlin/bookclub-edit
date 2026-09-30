"""流程第 5 步（二）：老師提到學員名字的地方，排出要怎麼處理（02 規格第三節）。

讀第 1 步找到的 `名字候選.json`，扣掉第 4 步覆核標成「不是名字」「是地名」的，
每一筆照做法分三種：

| 做法 | 這支程式產出 |
| --- | --- |
| 直接消音 | 剪輯決策：名字那一小段消音（組裝時墊環境底噪） |
| 整句換掉 | 生成清單：整句改寫成代號，交給老師 AI 聲音重念，放回整句的時間格 |
| 只換名字 | 生成清單：只念代號，放回名字那一小段的時間格 |

做法：覆核決定（第 3 步覆核工作台）裡有 `做法` 欄位就用覆核的，沒有就整句換掉（09-25 定案）。
整句換掉時照標點把 Groq 的半句擴成完整句子；同一句有好幾個名字時，合成一筆、一次換完。

產出（工作區）：
- `生成/名字句子.json`：給 `bookclub gen teacher` 的句子清單（只含代號，不含本名）
- `生成/名字處理計畫.json`：每筆候選對到哪一筆生成或消音，組裝時讀
"""

from __future__ import annotations

from pathlib import Path

from bookclub import workdir as wd

DECISIONS_FILE_NAME = "名字覆核決定.json"   # 跟 bookclub/server.py 一致
SKIP_TAGS = {"不是名字", "是地名"}
MUTE, WHOLE, NAME_ONLY = "直接消音", "整句換掉", "只換名字"


def plan_path(workdir: Path) -> Path:
    return workdir / "生成" / "名字處理計畫.json"


def sentences_path(workdir: Path) -> Path:
    return workdir / "生成" / "名字句子.json"


def replace_name(sentence: str, cand: dict) -> str | None:
    """把句子裡的名字換成代號。依序試：比對到的字、名冊寫法、本名；都找不到回傳 None。"""
    for key in ("matched_text", "name", "canonical"):
        word = (cand.get(key) or "").strip()
        if word and word in sentence:
            return sentence.replace(word, cand["代號"], 1)
    return None


END_PUNCT = "。！？!?﹖﹗…"          # 句子結束
CONT_PUNCT = "，,、；;：:﹐﹑"        # 句子還沒完（Groq 在這裡切開的是半句）
MAX_SENTENCE_S = 25.0               # 擴句的上限：整句不超過這麼長
MAX_JOIN_GAP_S = 0.8                # 前後兩段中間空白超過這個秒數，不接在一起


def _ends_open(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and t[-1] in CONT_PUNCT


def expand_sentence(ordered: list[dict], idx: int, same=None) -> list[dict]:
    """把 Groq 切的半句擴成完整句子（純函式，09-25 宇軒：句子起訖照標點，不斷在句子中間）。

    往前：前一段以逗號類結尾（句子還沒完）、也是老師、中間空白不超過 MAX_JOIN_GAP_S → 接上；
    往後：這一段以逗號類結尾 → 接下一段。以句號、問號、刪節號或沒有標點結尾的地方當成句子邊界。
    總長不超過 MAX_SENTENCE_S。回傳依時間排序的句子清單（至少含原本那一句）。"""
    group = [ordered[idx]]
    lo, hi = idx, idx

    def ok(s: dict) -> bool:   # 同一個人說的才接（預設：老師；學員講到名字時由呼叫端給判斷）
        return same(s) if same else s.get("label", "老師") == "老師"

    while lo > 0:
        prev = ordered[lo - 1]
        if not (_ends_open(prev["text"]) and ok(prev) and group[0]["start"] - prev["end"] <= MAX_JOIN_GAP_S
                and group[-1]["end"] - prev["start"] <= MAX_SENTENCE_S):
            break
        lo -= 1
        group.insert(0, prev)
    while hi + 1 < len(ordered):
        nxt = ordered[hi + 1]
        if not (_ends_open(group[-1]["text"]) and ok(nxt) and nxt["start"] - group[-1]["end"] <= MAX_JOIN_GAP_S
                and nxt["end"] - group[0]["start"] <= MAX_SENTENCE_S):
            break
        hi += 1
        group.append(nxt)
    return group


def ok_teacher(s: dict) -> bool:
    return s.get("label", "老師") in ("老師", "太短", "不確定")


# ---------- 重念範圍（10-01 宇軒：整句太長時，只重念名字所在的那一小句） ----------

LONG_SENTENCE_S = 10.0   # 整句超過這麼長，預設改成只重念名字所在的那一小句
RANGE_MIN_S = 2.0        # 縮小後的範圍至少這麼長（太短念不成一句話）
RANGE_MAX_S = 10.0
CLEAR_GAP_S = 0.3        # 字跟字之間的空白超過這個秒數，算「明顯的空白」
NEAR_S = 3.0             # 先在名字前後這麼遠裡找切點，找不到才放寬到 RANGE_MAX_S
WORD_PUNCT = END_PUNCT + CONT_PUNCT


def _gaps(words: list[dict]) -> list[dict]:
    """相鄰兩個字之間的切點候選：{t: 空白的中間, gap: 空白秒數, punct: 前一個字後面有標點}。"""
    out = []
    for w, n in zip(words, words[1:]):
        gap = n["start"] - w["end"]
        tail = (w.get("word") or "").rstrip()
        out.append({"t": (w["end"] + n["start"]) / 2 if gap > 0 else w["end"], "gap": max(0.0, gap),
                    "punct": bool(tail) and tail[-1] in WORD_PUNCT})
    return out


def _is_cut(g: dict) -> bool:
    return g["gap"] >= CLEAR_GAP_S or g["punct"]


def name_range(words: list[dict], name_a: float, name_b: float, lo: float, hi: float,
               position: str = "") -> tuple[float, float] | None:
    """名字所在的那一小句（純函式）：從名字往前、往後各找一個切點，範圍約 2–10 秒、名字一定包在裡面。

    切點＝逐字稿相鄰兩個字之間的空白，切在空白的中間。先在名字前後 NEAR_S 秒裡找，找不到再放寬到 RANGE_MAX_S；
    優先順序：明顯的空白（≥ CLEAR_GAP_S）又落在標點上 → 明顯的空白 → 標點。找不到切點的那一邊用整句的邊界。
    太短（< RANGE_MIN_S）就往外再多取一段：名字在句尾多取前面，在句首多取後面，其他兩邊輪流。
    `lo`、`hi` 是整句的起訖（範圍不會超出整句）。找不到逐字時間、或縮不到 RANGE_MAX_S 以內，回傳 None（照整句）。"""
    ws = sorted((w for w in words or [] if lo - 0.05 <= (w["start"] + w["end"]) / 2 <= hi + 0.05), key=lambda w: w["start"])
    if len(ws) < 2:
        return None
    gaps = _gaps(ws)
    left = sorted((g for g in gaps if g["t"] <= name_a), key=lambda g: -g["t"])
    right = sorted((g for g in gaps if g["t"] >= name_b), key=lambda g: g["t"])

    def pick(side: list[dict], dist) -> float | None:
        for reach in (NEAR_S, RANGE_MAX_S):
            near = [g for g in side if dist(g) <= reach]
            for test in (lambda g: g["gap"] >= CLEAR_GAP_S and g["punct"], lambda g: g["gap"] >= CLEAR_GAP_S,
                         lambda g: g["punct"]):
                hit = next((g for g in near if test(g)), None)
                if hit:
                    return hit["t"]
        return None

    a = pick(left, lambda g: name_a - g["t"])
    b = pick(right, lambda g: g["t"] - name_b)
    a = lo if a is None else a
    b = hi if b is None else b

    def further(side: list[dict], cur: float, before: bool) -> float | None:
        nxt = next((g["t"] for g in side if _is_cut(g) and (g["t"] < cur - 0.01 if before else g["t"] > cur + 0.01)), None)
        edge = lo if before else hi
        if nxt is not None:
            return nxt
        return edge if abs(edge - cur) > 0.01 else None

    fixed = {"句尾": ("前", "後"), "句首": ("後", "前")}.get(position)
    turn = 0
    while b - a < RANGE_MIN_S:
        sides = fixed or (("前", "後") if turn % 2 == 0 else ("後", "前"))
        turn += 1
        for s in sides:
            n = further(left, a, True) if s == "前" else further(right, b, False)
            if n is not None:
                a, b = (n, b) if s == "前" else (a, n)
                break
        else:
            break
    if b - a > RANGE_MAX_S or not (a <= name_a + 0.01 and name_b - 0.01 <= b):
        return None
    return round(max(a, lo), 3), round(min(b, hi), 3)


def range_words(words: list[dict], a: float, b: float) -> str:
    """這段時間裡逐字稿的字（純函式，跟 `review.words_text` 同一個挑法：字的中點落在範圍裡）。"""
    return "".join(w.get("word", "") for w in words or [] if a <= (w["start"] + w["end"]) / 2 <= b).strip()


def say_count(text: str) -> int:
    """要念的字有幾個（純函式）：中文一個字算 1；英文、數字連在一起的算 2（代號 Jasmine 念起來約兩個字）；
    標點、空白不算。"""
    n, run = 0, False
    for ch in text or "":
        if ch.isascii() and ch.isalnum():
            if not run:
                n += 2
            run = True
            continue
        run = False
        if ch.isspace() or ch in WORD_PUNCT or ch in "「」『』（）()〔〕[]-—～~\"'…":
            continue
        n += 1
    return n


TOO_SHORT_RATIO = 0.5    # 要念的字不到那段時間逐字稿字數的一半 → 擋下來（這一段其他的話會不見）
TOO_SHORT_MIN = 6        # 逐字稿不到這麼多字的不檢查（太短，比例不準）


def too_short(say: str, source: str) -> bool:
    """要念的字比那段時間逐字稿的字少太多（純函式，2-1 的保護）。"""
    src = say_count(source)
    return src >= TOO_SHORT_MIN and say_count(say) < src * TOO_SHORT_RATIO


def whole_slot(c: dict, d: dict, group: list[dict], words: list[dict] | None) -> dict:
    """「整句換掉」要重念的時間格（純函式；`build_plan` 與覆核工作台共用，兩邊一定一樣）。

    - 名字覆核決定有 `整句起訖`（人在卡片上改的）→ 照人改的
    - 整句超過 LONG_SENTENCE_S、有逐字時間、沒改過名字時間 → 縮成名字所在的那一小句（`name_range`）；
      人已經改過要念的字（`改稿`）的話，改稿的字數比較接近縮小後的範圍才縮（改稿是照整句寫的就照整句）
    回傳 {start, end, 範圍: None｜"自動"｜"人選", 整句: [起, 訖]}。"""
    lo = min(group[0]["start"], c["start"])
    hi = max(group[-1]["end"], c["end"])
    out = {"start": lo, "end": hi, "範圍": None, "整句": [round(lo, 3), round(hi, 3)]}
    rng = d.get("整句起訖")
    if rng:
        return {**out, "start": float(rng[0]), "end": float(rng[1]), "範圍": "人選"}
    if not words or c.get("改過時間") or hi - lo <= LONG_SENTENCE_S:
        return out
    nr = name_range(words, c["start"], c["end"], lo, hi, c.get("位置", ""))
    if not nr:
        return out
    edited = (d.get("改稿") or "").strip()
    if edited:
        n = say_count(edited)
        if abs(n - say_count(range_words(words, *nr))) > abs(n - say_count(range_words(words, lo, hi))):
            return out
    return {**out, "start": nr[0], "end": nr[1], "範圍": "自動"}


def build_plan(candidates: list[dict], decisions: dict, sentences: dict[str, dict],
               default_how: str = WHOLE, words: list[dict] | None = None) -> dict:
    """純函式：候選＋覆核決定＋句子（id → {start, end, text}）→ 處理計畫。

    做法：覆核決定的 `做法` 優先；沒有就用 default_how（09-25 宇軒定案：預設整句換掉；
    第 1 步的「建議做法」只當成覆核時的參考）。整句換掉時，Groq 的半句照標點擴成完整句子
    （`expand_sentence`），同一個完整句子裡的名字合成一筆、一次換完。

    回傳 {"生成": [...句子清單...], "消音": [...], "略過": [...], "要人處理": [...]}，
    每一筆都帶 `候選` 編號（1 起算，跟覆核決定的 id 一致）。
    `words`（逐字時間）給了的話，整句太長時只重念名字所在的那一小句（`whole_slot`，10-01），
    這種項目帶 `範圍`（自動／人選）與 `整句`（原本整句的起訖），文字照範圍裡逐字稿的字。
    """
    gen, mutes, skipped, manual = [], [], [], []
    ranged: list[dict] = []       # 縮小範圍的（範圍疊在一起的名字併成一筆）
    whole: dict[str, dict] = {}   # 完整句子第一段的 id → 生成項目（同一句合併）
    ordered = sorted(sentences.values(), key=lambda x: x["start"])
    pos = {x["id"]: k for k, x in enumerate(ordered)}

    for i, c in enumerate(candidates, start=1):
        i = c.get("id", i)             # 人工補的名字（覆核工作台「新增修改」）自己帶 id（NM001…）
        d = decisions.get(str(i), {}) or {}
        tags = set(d.get("tags", []))
        if tags & SKIP_TAGS:
            skipped.append({"候選": i, "原因": "、".join(sorted(tags & SKIP_TAGS))})
            continue
        how = d.get("做法") or default_how
        pad = float(c.get("建議緩衝秒數") or 0.0)

        if c.get("老師整段"):   # 09-30：人工標的一段老師的話：整段照打的字重念；沒有字、或選直接消音就整段消音
            text = ((d.get("改稿") or "").strip() or (c.get("整段文字") or "").strip())
            if how == MUTE or not text:
                mutes.append({"候選": i, "start": round(c["start"], 3), "end": round(c["end"], 3)})
            else:
                gen.append({"id": f"S{_num(i)}", "text": text, "slot": [round(c["start"], 3), round(c["end"], 3)],
                            "候選": [i], "句子": [], "整段": True})
            continue

        if how == MUTE:
            mutes.append({"候選": i, "start": round(c["start"] - pad, 3), "end": round(c["end"] + pad, 3)})
            continue

        if how == NAME_ONLY:
            gen.append({"id": f"N{_num(i)}", "text": c["代號"], "slot": [c["start"] - pad, c["end"] + pad], "候選": [i]})
            continue

        sid = c.get("sentence_id")
        if sid not in pos:
            manual.append({"候選": i, "原因": f"找不到所在的句子（{sid}）"})
            continue
        group = expand_sentence(ordered, pos[sid])
        if c.get("改過時間"):   # 09-29 宇軒：改時間把後面幾秒也納進來（逐字稿漏了第二次叫名字）→ 範圍內的句子一起重念
            group = [g for g in ordered if g["end"] > min(c["start"], group[0]["start"]) + 0.05
                     and g["start"] < max(c["end"], group[-1]["end"]) - 0.05 and (g in group or ok_teacher(g))] or group
        ws = whole_slot(c, d, group, words)
        if ws["範圍"]:
            _add_ranged(ranged, manual, c, i, d, ws, words)
            continue
        key = group[0]["id"]
        item = whole.get(key)
        # 名字只在原本那一段裡換（避免換到前後段同音的字），再接成整句
        texts = item["_texts"] if item else {g["id"]: g["text"] for g in group}
        new = replace_name(texts[sid], c)
        edited = (d.get("改稿") or "").strip()
        if new is None and not edited:
            manual.append({"候選": i, "原因": "句子裡找不到比對到的字，無法自動換成代號"})
            continue
        if new is not None:     # 找不到字但人已經改好要念的句子（09-30）：照人改的念，不算「要人處理」
            texts[sid] = new
        full = "".join(texts[g["id"]] for g in group)
        lo = min(group[0]["start"], c["start"])   # 名字本身一定包進時間格（句首的字可能比句子早開始）
        hi = max(group[-1]["end"], c["end"])
        if item:
            item["text"] = full
            item["候選"].append(i)
            item["slot"] = [min(item["slot"][0], lo), max(item["slot"][1], hi)]
        else:
            item = whole[key] = {"id": f"S{_num(i)}", "text": full, "slot": [lo, hi],
                                 "候選": [i], "句子": [g["id"] for g in group], "_texts": texts}
        if (d.get("改稿") or "").strip():   # 09-29 宇軒：人直接改要重念的句子（逐字稿漏字、名字不只一次）
            item["text"] = d["改稿"].strip()
            item["改稿"] = True

    for item in whole.values():
        item.pop("_texts", None)
        item["slot"] = [round(item["slot"][0], 3), round(item["slot"][1], 3)]
    for item in ranged:
        item.pop("_cands", None)
    gen.extend(whole.values())
    gen.extend(ranged)
    gen.sort(key=lambda g: g["slot"][0])
    return {"生成": gen, "消音": mutes, "略過": skipped, "要人處理": manual}


def _names_in(text: str, cands: list[dict]) -> str | None:
    """範圍裡的字，名字一個一個換成代號；有任何一個找不到就回傳 None。"""
    for c in cands:
        text = replace_name(text, c)
        if text is None:
            return None
    return text


def _add_ranged(ranged: list[dict], manual: list[dict], c: dict, i, d: dict, ws: dict, words: list[dict] | None) -> None:
    """縮小範圍的名字加進生成清單；範圍跟已經有的疊在一起就併成一筆（文字照合起來的範圍重排）。"""
    edited = (d.get("改稿") or "").strip()
    item = next((g for g in ranged if g["slot"][0] < ws["end"] and ws["start"] < g["slot"][1]), None)
    a, b = (min(item["slot"][0], ws["start"]), max(item["slot"][1], ws["end"])) if item else (ws["start"], ws["end"])
    cands = (item["_cands"] if item else []) + [c]
    new = _names_in(range_words(words, a, b), cands)
    if new is None and not edited and not (item and item.get("改稿")):
        manual.append({"候選": i, "原因": "重念範圍裡的逐字稿找不到比對到的字，無法自動換成代號"})
        return
    if item is None:
        item = {"id": f"S{_num(i)}", "text": "", "slot": [a, b], "候選": [], "句子": [], "範圍": ws["範圍"],
                "整句": ws["整句"], "_cands": []}
        ranged.append(item)
    item["slot"] = [round(a, 3), round(b, 3)]
    item["候選"].append(i)
    item["_cands"].append(c)
    if ws["範圍"] == "人選":
        item["範圍"] = "人選"
    if not item.get("改稿"):
        item["text"] = new or item["text"]
    if edited:   # 人直接改的要念的句子優先
        item["text"], item["改稿"] = edited, True


def teacher_by_turns(turns: list[dict]):
    """「這一句是不是老師說的」照段落判斷（純函式，回傳判斷用的函式）。重疊的地方兩個聲音混在一起，
    逐句的聲紋標籤常常判成「不是老師」，段落（整段誰在講）比較可靠：句子中點落在老師的段落裡就算老師的、
    落在學員的段落裡就不算；兩邊都沒蓋到才看逐句的標籤。"""
    teacher = [(t["start"], t["end"]) for t in turns if t.get("說話者") == "老師"]
    others = [(t["start"], t["end"]) for t in turns if t.get("說話者") != "老師"]

    def same(s: dict) -> bool:
        mid = (s["start"] + s["end"]) / 2
        if any(a <= mid <= b for a, b in teacher):
            return True
        if any(a <= mid <= b for a, b in others):
            return False
        return s.get("label", "老師") == "老師"

    return same


def overlap_sentence(o: dict, ordered: list[dict], is_teacher=None) -> list[dict] | None:
    """重疊選「生成老師聲音」時要重念的老師整句（純函式）：蓋到重疊最多的那一句老師的話，照標點擴成完整句子。
    `ordered` 是照時間排的全部句子；`is_teacher` 見 `teacher_by_turns`（沒給就看逐句標籤）。找不到老師的句子回傳 None。"""
    is_teacher = is_teacher or (lambda s: s.get("label", "老師") == "老師")
    best, best_k = 0.0, None
    for k, s in enumerate(ordered):
        if s["start"] > o["end"] + 0.3:
            break
        if not is_teacher(s):
            continue
        cover = min(s["end"], o["end"] + 0.3) - max(s["start"], o["start"] - 0.3)
        if cover > best:
            best, best_k = cover, k
    return expand_sentence(ordered, best_k, same=is_teacher) if best_k is not None else None


def add_overlap_items(plan: dict, picks: list[dict], sentences: dict[str, dict], is_teacher=None) -> dict:
    """重疊選了「生成老師聲音」（做法＝只留老師）的，加進老師的生成清單（純函式，改 plan 本身）：
    老師那一整句用 AI 聲音重念，學員疊在上面的聲音跟著拿掉。

    - 這一句已經因為名字要重念 → 併在同一筆（加 `重疊項目`），文字用名字那一筆的
    - 找不到老師的句子 → 記在 `重疊沒句子`，組裝時那一小段照消音處理
    `picks`：[{id, start, end, 老師整句改稿}]。"""
    ordered = sorted(sentences.values(), key=lambda x: x["start"])
    for o in picks:
        group = overlap_sentence(o, ordered, is_teacher)
        if not group:
            plan.setdefault("重疊沒句子", []).append(o["id"])
            continue
        lo, hi = round(min(group[0]["start"], o["start"]), 3), round(max(group[-1]["end"], o["end"]), 3)
        item = next((g for g in plan["生成"] if (g.get("句子") or [None])[0] == group[0]["id"]), None)
        edited = (o.get("老師整句改稿") or "").strip()
        rng = next((g for g in plan["生成"] if g.get("範圍") and g["slot"][0] < hi and lo < g["slot"][1]), None)
        if rng is not None:   # 10-01：名字只重念一小句的那一筆跟老師這一句疊到
            if rng["slot"][0] <= o["start"] and o["end"] <= rng["slot"][1]:
                rng.setdefault("重疊項目", []).append(o["id"])   # 重疊整個在那一小句裡：跟著換掉
            else:   # 只疊到一部分：兩筆會搶同一段時間，請人把名字的重念範圍改大到包住重疊
                plan.setdefault("要人處理", []).append({
                    "候選": rng["候選"][0], "原因": f"重念範圍跟重疊 {o['id']}（選了生成老師聲音）只疊到一部分："
                                                 "把重念範圍改大到包住重疊，或重疊改選別的做法"})
                plan.setdefault("重疊沒句子", []).append(o["id"])
            continue
        if item:
            item.setdefault("重疊項目", []).append(o["id"])
            item["slot"] = [min(item["slot"][0], lo), max(item["slot"][1], hi)]
            if edited and not item.get("候選"):
                item["text"], item["改稿"] = edited, True
            continue
        item = {"id": f"V{int(round(o['start'] * 100)):07d}", "text": edited or "".join(g["text"] for g in group),
                "slot": [lo, hi], "候選": [], "句子": [g["id"] for g in group], "重疊項目": [o["id"]]}
        if edited:
            item["改稿"] = True
        plan["生成"].append(item)
    plan["生成"].sort(key=lambda g: g["slot"][0])
    return plan


def add_stacked_items(plan: dict, choices: list[dict]) -> dict:
    """重疊選「兩邊都重新生成（照原本的時間）」的老師那一句（10-01 B 方案，純函式，改 plan 本身）：
    照卡片上「老師說的」、老師那邊的起訖重念，帶 `疊放`（組裝時跟學員那一句混在一起，不互相蓋掉）。
    「老師說的」是空的不排（覆核時擋通過、開始前總檢查會列出來）。"""
    from bookclub import review

    for o in choices:
        text = (o.get("老師文字") or "").strip()
        if not review.is_stacked(o["做法"], o.get("排法")) or not text:
            continue
        a, b = o["老師起訖"]
        plan["生成"].append({"id": f"W{int(round(o['start'] * 100)):07d}", "text": text, "slot": [a, b], "候選": [],
                           "句子": [], "重疊項目": [o["id"]], "疊放": True})
    plan["生成"].sort(key=lambda g: g["slot"][0])
    return plan


def _num(i) -> str:
    return f"{i:03d}" if isinstance(i, int) else str(i)


IMPORTED_PATH = Path("覆核") / "覆核結果.json"   # `bookclub review import` 放進來的覆核結果


def make_plan(workdir: str | Path, only: list[int] | None = None) -> dict:
    """`bookclub gen names` 的第一步：排出計畫、寫出句子清單。only 給了就只處理那幾筆候選（測試用）。

    匯入的工作區（夥伴的電腦，`bookclub review import` 建的）沒有名字候選與逐字稿，改用覆核結果裡
    匯出時排好的計畫（只含代號，不含本名）。"""
    workdir = Path(workdir).expanduser()
    names = wd.read_json(wd.names_path(workdir))
    if not names:
        exported = wd.read_json(workdir / IMPORTED_PATH)
        if exported and exported.get("名字處理計畫") is not None:
            return _write_plan(workdir, exported["名字處理計畫"], note="（用匯入的覆核結果）")
        raise FileNotFoundError(f"找不到 {wd.names_path(workdir)}，先跑 `bookclub run analyze`。")
    plan = compute_plan(workdir, names, only=only)
    return _write_plan(workdir, plan, n_cands=len(names.get("candidates", [])), only=only)


def compute_plan(workdir: Path, names: dict | None = None, only: list[int] | None = None) -> dict:
    """排出處理計畫（不寫檔）：`make_plan` 與匯出覆核結果共用，兩邊排出來的計畫一定一樣。"""
    workdir = Path(workdir).expanduser()
    names = names if names is not None else (wd.read_json(wd.names_path(workdir)) or {})
    speakers = wd.read_json(wd.speakers_path(workdir)) or {}
    sentences = {s["id"]: s for s in speakers.get("sentences", [])}
    from bookclub import review

    if wd.names_path(workdir).is_file():
        review.anchor_name_decisions(workdir)   # 09-29：候選重算過，決定跟著內容走
    decisions = wd.read_json(workdir / DECISIONS_FILE_NAME, default={}) or {}
    candidates = names.get("candidates", [])
    candidates = review.effective_name_candidates(workdir, candidates, decisions)   # 人工補的、改過時間的
    if only:
        candidates = [c for c in candidates if "id" not in c]     # 測試只處理幾筆時，人工補的先不做
        keep = set(only)
        decisions = dict(decisions)
        for i in range(1, len(candidates) + 1):
            if i not in keep:
                decisions[str(i)] = {"tags": ["不是名字"]}  # 只在計算時略過，不寫回覆核決定
    words = (wd.read_json(wd.merged_transcript_path(workdir), default={}) or {}).get("words") or []
    plan = build_plan(candidates, decisions, sentences, words=words)
    if not only:   # 09-30：重疊選「生成老師聲音」的，老師整句一起排進生成清單
        choices = review.overlap_choices(workdir)
        picks = [o for o in choices if o["做法"] == "只留老師"]
        add_stacked_items(plan, choices)
        if picks:
            from bookclub import turns as turns_mod

            tdata = turns_mod.page_data(workdir)
            turns = tdata.get("段落", []) if not tdata.get("尚未準備") else []
            add_overlap_items(plan, picks, sentences, teacher_by_turns(turns))
    # 09-30：最後一道保險——老師要念的每一句再過一次名冊（本名、其他寫法、敏感詞 → 代號），
    # 同一句裡的第二個名字、人改稿時漏掉的也會換（卡片上看得到「實際會念」）
    table = review.replace_table(workdir)
    for g in plan["生成"]:
        g["text"], _changes = review.replace_real_names(g["text"], table)
    if only:
        plan["略過"] = [s for s in plan["略過"] if s["候選"] in set(only)]
        plan["只處理"] = sorted(set(only))
    return plan


def _write_plan(workdir: Path, plan: dict, n_cands: int | None = None, only: list[int] | None = None,
                note: str = "") -> dict:
    wd.write_json(sentences_path(workdir), [{k: g[k] for k in ("id", "text", "slot")} for g in plan["生成"]])
    wd.write_json(plan_path(workdir), plan)
    print(f"[名字處理] {note}" + (f"候選 {n_cands} 筆" if n_cands is not None else "")
          + (f"（這次只處理 {len(only)} 筆）" if only else "")
          + f"：生成 {len(plan['生成'])} 段、消音 {len(plan['消音'])} 段、略過 {len(plan['略過'])} 筆、"
          f"要人處理 {len(plan['要人處理'])} 筆")
    return plan
