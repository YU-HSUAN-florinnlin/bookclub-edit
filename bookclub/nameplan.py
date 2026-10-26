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

import json
import re
from pathlib import Path

from bookclub import workdir as wd

DECISIONS_FILE_NAME = "名字覆核決定.json"   # 跟 bookclub/server.py 一致
SKIP_TAGS = {"不是名字", "是地名"}
MUTE, WHOLE, NAME_ONLY = "直接消音", "整句換掉", "只換名字"


def plan_path(workdir: Path) -> Path:
    return workdir / "生成" / "名字處理計畫.json"


def sentences_path(workdir: Path) -> Path:
    return workdir / "生成" / "名字句子.json"


DROP_MARK = "\ue002"   # 10-18 #245：名字拿掉的地方先留這個記號（私用區字元，不會念出來），接成整句後再整理、拿掉


def mark_name(sentence: str, cand: dict) -> str | None:
    """名字換成代號；沒有代號（名字拿掉）時先換成 `DROP_MARK`、空白一個都不動。找不到回傳 None。
    10-18 #245：小段（Groq 切的半句）裡拿掉名字不能去空白——小段的頭尾是句中，接成整句之後才用 `finish_drops` 整理。"""
    for key in ("matched_text", "name", "canonical"):
        word = (cand.get(key) or "").strip()
        if word and word in sentence:
            return sentence.replace(word, cand.get("代號") or DROP_MARK, 1)
    return None


def finish_drops(text: str) -> tuple[str, list[int]]:
    """整句裡的 `DROP_MARK` 拿掉，回傳（要念的文字, 名字被拿掉的位置）（純函式）。
    10-17 #245、10-18 宇軒：名字在整句的句首被拿掉，開頭的空白去掉；在句尾，結尾的空白去掉（含全形空白、tab、換行）；
    句中的空白一律不動。位置給 #247 卡片標「（拿掉）」用，已經扣掉開頭去掉的字數。"""
    out, at = "", []
    for ch in text or "":
        if ch == DROP_MARK:
            at.append(len(out))
        else:
            out += ch
    if any(not out[:p].strip() for p in at):
        lead = len(out) - len(out.lstrip())
        out = out.lstrip()
        at = [max(0, p - lead) for p in at]
    if any(not out[p:].strip() for p in at):
        out = out.rstrip()
        at = [min(p, len(out)) for p in at]
    return out, at


DROP_MARK_LEFT = "程式內部錯誤：名字拿掉的記號沒清掉（要念的字裡還有）。請將錯誤訊息通知開發者。"


def check_no_drop_mark(text: str) -> None:
    """10-18 #245 小保險：要念的字裡還有 `DROP_MARK` 就丟錯，不要靜默清掉（清掉會藏住漏整理的那條路）。"""
    if DROP_MARK in (text or ""):
        raise RuntimeError(DROP_MARK_LEFT)


def replace_name(sentence: str, cand: dict) -> str | None:
    """把句子裡的名字換成代號。依序試：比對到的字、名冊寫法、本名；都找不到回傳 None。
    10-17 #245：沒有代號（名字拿掉）時，`sentence` 當成整句，句首、句尾留下的空白去掉（見 `finish_drops`）。
    小段要再接成整句的，用 `mark_name` 再對整句 `finish_drops`。"""
    new = mark_name(sentence, cand)
    return None if new is None else finish_drops(new)[0]


HOME_NEAR_S = 0.6   # 名字的聲音離鄰句邊界這麼近（秒）、鄰句的字裡有這個名字，名字算鄰句的（#298）


def home_sentence(ordered: list[dict], pos: dict, c: dict) -> str | None:
    """名字候選的聲音落在哪一句的時間裡，句子文字卻不含這個名字、緊鄰的前一句或後一句含有 → 回傳鄰句的 id（純函式）。

    1026 #298 實例：老師點名句的最後一個名字，逐字時間比句子結束晚 0.1 秒，被算進下一句（學員開口的那一句），
    下一句的字裡沒有這個名字，要念的字就從逐字稿拼成「我 代號剛剛本來想到…」。其餘照舊回傳候選自己的句子。"""
    sid = c.get("sentence_id")
    k = pos.get(sid)
    if k is None or mark_name(ordered[k].get("text") or "", c) is not None:
        return sid
    for j in (k - 1, k + 1):
        if not 0 <= j < len(ordered):
            continue
        nb = ordered[j]
        gap = c["start"] - nb["end"] if j < k else nb["start"] - c["end"]
        if -0.05 <= gap <= HOME_NEAR_S and ok_teacher(nb) and mark_name(nb.get("text") or "", c) is not None:
            return nb["id"]
    return sid


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


# ---------- 10-04 #62 第 6 點：整句換掉的範圍延伸到標點或真的停頓 ----------

PAUSE_EPS = CLEAR_GAP_S - 1e-6   # 浮點誤差：0.3 秒的空白算出來可能是 0.29999
EXTEND_MAX_S = 5.0       # 往前、往後各最多延伸這麼多秒；這麼遠還找不到標點或停頓，那一邊不延伸


# 10-04：延伸先關。三輪審查各重現一個新問題（第一輪：延伸把別張卡的名字包進去、吃掉那張卡的消音；第二輪問題 C：
# 延伸範圍跟前一句另一張卡的整句疊在一起；第三輪問題 D：同一句兩張卡只有一張延伸，另一張被吃掉）。三個都修了、
# 有測試，但宇軒 10-05 就要用新轉的工作區做第 3 步，先保守關掉。打開的條件：宇軒同意後改成 True；不用重轉文字，
# 重排名字處理計畫（第 4 步開頭會自動重排）就會生效。
EXTEND_ON = False


def extend_enabled(merged: dict | None) -> bool:
    """新做法轉的工作區才延伸重念範圍：`轉文字做法`＝「不挖停頓」（10-04 #62 乙，Groq 自己斷句），或有
    `保留停頓秒數`（保留一秒的做法，已停用）。已經轉好、生成過的工作區（第一堂）不動，免得已經生成的句子
    時間格跟著變、要重新生成。`EXTEND_ON` 是 False 時一律不延伸。"""
    if not EXTEND_ON:
        return False
    m = merged or {}
    return m.get("轉文字做法") == "不挖停頓" or bool(m.get("保留停頓秒數"))


def _card_reach(ordered: list[dict], pos: dict, c: dict) -> tuple[str | None, tuple[float, float]]:
    """一張卡最多可能重念到哪裡（純函式）：它的完整句子（`expand_sentence`）再加前一句、後一句（它自己可能延伸
    進去的地方）。回傳 (完整句子第一段的 id, (起, 訖))；找不到句子的（人工補的）就是名字本身。"""
    sid = c.get("sentence_id")
    if sid not in pos:
        return None, (float(c["start"]), float(c["end"]))
    group = expand_sentence(ordered, pos[sid])
    before, after = neighbors(ordered, group)
    a = min([group[0]["start"], c["start"]] + ([before["start"]] if before else []))
    b = max([group[-1]["end"], c["end"]] + ([after["end"]] if after else []))
    return group[0]["id"], (float(a), float(b))


def no_extend_groups(candidates: list[dict], decisions: dict, ordered: list[dict],
                     default_how: str = WHOLE, cut: set | None = None) -> set:
    """10-04 問題 D（純函式）：同一個完整句子裡只要有任何一張卡不能延伸——有改稿、改過時間、人選過重念範圍、
    做法不是整句換掉（老師整段、找不到句子的也算）——整句的卡都不延伸，回傳這些完整句子（第一段的 id）。
    不然一張延伸、一張留在整句，兩筆時間格疊在一起，組裝時長的優先，另一張被吃掉。
    不會產生任何處理的卡（落在剪掉的片段、同一處併進主卡、標成不是名字／是地名）不算。"""
    pos = {x["id"]: k for k, x in enumerate(ordered)}
    out: set = set()
    for i, c in enumerate(candidates, start=1):
        i = c.get("id", i)
        d = decisions.get(str(i), {}) or {}
        if (cut and str(i) in cut) or c.get("同一處") or set(d.get("tags", [])) & SKIP_TAGS:
            continue
        key = _card_reach(ordered, pos, c)[0]
        if key is None:
            continue
        if ((d.get("做法") or default_how) != WHOLE or c.get("老師整段") or c.get("改過時間")
                or (d.get("改稿") or "").strip() or d.get("整句起訖")):
            out.add(key)
    return out


def other_ranges(candidates: list[dict], c: dict, extra=(), ordered: list[dict] | None = None) -> list[tuple[float, float]]:
    """延伸重念範圍時不能碰到的範圍：c 以外每一個名字候選＋extra（保留原聲學員的名字候選）。

    10-04 問題 C：給了 `ordered`（全部句子）的話，再加上別張卡「整句換掉」最多會重念到的地方（它的完整句子＋
    前後各一句，含它自己延伸的部分）——不然兩張卡的時間格疊在一起，組裝時長的優先，另一張卡的話會不見。
    跟 c 同一個完整句子的卡（會併成同一筆）不算。保守：別張卡選什麼做法都算。"""
    out = [(float(x["start"]), float(x["end"])) for x in candidates if x is not c]
    if ordered is not None:
        pos = {x["id"]: k for k, x in enumerate(ordered)}
        key = _card_reach(ordered, pos, c)[0]
        for x in candidates:
            if x is c:
                continue
            k, rng = _card_reach(ordered, pos, x)
            if k is None or k != key:
                out.append(rng)
    return out + list(extra)


def student_name_ranges(workdir: Path) -> list[tuple[float, float]]:
    """保留原聲學員講到名字的候選（`studentnames`）的 (起, 訖)；還沒找過就是空的。"""
    from bookclub import studentnames

    data = wd.read_json(studentnames.cands_path(workdir), default=None) or {}
    return [(float(x["start"]), float(x["end"])) for x in data.get("candidates", []) if "start" in x and "end" in x]


def neighbors(ordered: list[dict], group: list[dict]) -> tuple[dict | None, dict | None]:
    """group（依時間排好的句子）前一句、後一句（純函式）。"""
    ids = [g.get("id") for g in ordered]
    try:
        i, j = ids.index(group[0].get("id")), ids.index(group[-1].get("id"))
    except ValueError:
        return None, None
    return (ordered[i - 1] if i > 0 else None), (ordered[j + 1] if j + 1 < len(ordered) else None)


_CLEAN = re.compile(r"[\w一-鿿]", re.UNICODE)


def _punct_after_words(sent: dict, ws: list[dict]) -> list[bool]:
    """這一句的每個字後面有沒有標點（純函式）：字本身尾巴帶標點，或句子文字裡數到這個字之後是標點
    （字數跟句子文字對不上時只看字本身）。"""
    text = sent.get("text") or ""
    after: set[int] = set()
    n = 0
    for ch in text:
        if _CLEAN.match(ch):
            n += 1
        elif ch in WORD_PUNCT and n:
            after.add(n)
    counts, n = [], 0
    for w in ws:
        n += len(_CLEAN.findall(w.get("word") or ""))
        counts.append(n)
    ok = n == len(_CLEAN.findall(text))
    out = []
    for w, k in zip(ws, counts):
        tail = (w.get("word") or "").rstrip()
        out.append((bool(tail) and tail[-1] in WORD_PUNCT) or (ok and k in after))
    return out


def _sent_words(words: list[dict], sent: dict) -> list[dict]:
    return sorted((w for w in words if sent["start"] - 0.05 <= (w["start"] + w["end"]) / 2 <= sent["end"] + 0.05),
                  key=lambda w: w["start"])


def extend_range(words: list[dict], lo: float, hi: float, before: dict | None, after: dict | None,
                 group: list[dict], avoid=()) -> tuple[float, float]:
    """10-04 #62 第 6 點（純函式）：整句換掉的範圍，起訖斷在話中間的話往前、往後延伸到標點或真的停頓
    （字跟字之間空 CLEAR_GAP_S 秒以上），切在空白的中間。

    句子變短後（轉文字保留停頓），Groq 的「一句」常斷在話中間（@3025.04、@3211.92 那種），照一句的起訖重念，
    接回去會聽到前後的話被切斷。只延伸進老師的句子；起訖本來就在標點或停頓上、或 EXTEND_MAX_S 秒內找不到
    標點或停頓的那一邊不動。
    `avoid`：其他名字候選（老師的每一張卡，不管做法、確認了沒有；保留原聲學員的名字候選）的 (起, 訖)。延伸的那一邊
    會碰到其中任何一個 → 那一邊不延伸（不然別張卡的名字會被包進來重念、那張卡的消音被吃掉）。"""
    ws = sorted(words or [], key=lambda w: w["start"])
    inside = [w for w in ws if lo - 0.05 <= (w["start"] + w["end"]) / 2 <= hi + 0.05]
    if not inside:
        return lo, hi
    a, b = lo, hi
    if before is not None and ok_teacher(before) and not _ends_closed(before.get("text", "")):
        full = _sent_words(ws, before)
        pairs = [(w, p) for w, p in zip(full, _punct_after_words(before, full)) if w["end"] <= inside[0]["start"] + 0.05]
        bw, punct = [w for w, _ in pairs], [p for _, p in pairs]
        if bw and not (inside[0]["start"] - bw[-1]["end"] >= PAUSE_EPS or punct[-1]):
            cut = None
            for k in range(len(bw) - 1, 0, -1):    # bw[k-1] 跟 bw[k] 之間
                if lo - bw[k]["start"] > EXTEND_MAX_S:
                    break
                gap = bw[k]["start"] - bw[k - 1]["end"]
                if gap >= PAUSE_EPS or punct[k - 1]:
                    cut = (bw[k - 1]["end"] + bw[k]["start"]) / 2 if gap > 0 else bw[k]["start"]
                    break
            if cut is None and lo - before["start"] <= EXTEND_MAX_S:
                cut = min(before["start"], bw[0]["start"])   # 前一句整句（句子開頭就是邊界）
            if cut is not None and not any(x < lo - 1e-3 and y > cut + 1e-3 for x, y in avoid):
                a = min(a, cut)
    if after is not None and ok_teacher(after) and not _ends_closed(group[-1].get("text", "")):
        full = _sent_words(ws, after)
        pairs = [(w, p) for w, p in zip(full, _punct_after_words(after, full)) if w["start"] >= inside[-1]["end"] - 0.05]
        aw, punct = [w for w, _ in pairs], [p for _, p in pairs]
        if aw and aw[0]["start"] - inside[-1]["end"] < PAUSE_EPS:
            cut = None
            for k in range(len(aw) - 1):           # aw[k] 跟 aw[k+1] 之間
                if aw[k]["end"] - hi > EXTEND_MAX_S:
                    break
                gap = aw[k + 1]["start"] - aw[k]["end"]
                if gap >= PAUSE_EPS or punct[k]:
                    cut = (aw[k]["end"] + aw[k + 1]["start"]) / 2 if gap > 0 else aw[k]["end"]
                    break
            if cut is None and after["end"] - hi <= EXTEND_MAX_S:
                cut = max(after["end"], aw[-1]["end"])        # 後一句整句（句子結尾就是邊界）
            if cut is not None and not any(x < cut - 1e-3 and y > hi + 1e-3 for x, y in avoid):
                b = max(b, cut)
    return round(a, 3), round(b, 3)


def _ends_closed(text: str) -> bool:
    """句子文字以標點結尾（句號類或逗號類）＝本來就在標點上，不用延伸。"""
    t = (text or "").strip()
    return bool(t) and t[-1] in WORD_PUNCT


def whole_slot(c: dict, d: dict, group: list[dict], words: list[dict] | None,
               around: tuple[dict | None, dict | None] | None = None, avoid=()) -> dict:
    """「整句換掉」要重念的時間格（純函式；`build_plan` 與覆核工作台共用，兩邊一定一樣）。

    - 名字覆核決定有 `整句起訖`（人在卡片上改的）→ 照人改的
    - 整句超過 LONG_SENTENCE_S、有逐字時間、沒改過名字時間 → 縮成名字所在的那一小句（`name_range`）；
      人已經改過要念的字（`改稿`）的話，改稿的字數比較接近縮小後的範圍才縮（改稿是照整句寫的就照整句）
    - 10-04 #62：`around`＝(前一句, 後一句) 給了的話（轉文字保留停頓的工作區），照整句的範圍起訖斷在話中間時
      往前後延伸到標點或真的停頓（`extend_range`）→ 範圍「延伸」；改稿的字數比較接近原本整句的話不延伸
    回傳 {start, end, 範圍: None｜"自動"｜"人選"｜"延伸", 整句: [起, 訖]}。"""
    lo = min(group[0]["start"], c["start"])
    hi = max(group[-1]["end"], c["end"])
    out = {"start": lo, "end": hi, "範圍": None, "整句": [round(lo, 3), round(hi, 3)]}
    rng = d.get("整句起訖")
    if rng:
        return {**out, "start": float(rng[0]), "end": float(rng[1]), "範圍": "人選"}
    edited = (d.get("改稿") or "").strip()

    def closer(a: float, b: float) -> bool:   # 改稿的字數比較接近 [a, b] 還是原本整句
        n = say_count(edited)
        return abs(n - say_count(range_words(words, a, b))) <= abs(n - say_count(range_words(words, lo, hi)))

    if not words or c.get("改過時間"):
        return out
    if hi - lo > LONG_SENTENCE_S:
        nr = name_range(words, c["start"], c["end"], lo, hi, c.get("位置", ""))
        if nr and (not edited or closer(*nr)):
            return {**out, "start": nr[0], "end": nr[1], "範圍": "自動"}
    if around is not None:
        a, b = extend_range(words, lo, hi, around[0], around[1], group, avoid)
        if (a < lo - 0.01 or b > hi + 0.01) and (not edited or closer(a, b)):
            return {**out, "start": a, "end": b, "範圍": "延伸"}
    return out


CUT_SKIP = "落在剪掉的片段裡（聲音和畫面都拿掉，不用處理）"
NO_CODE_SKIP = "這個名字這一集還沒有英文名：照原聲留著（第 3 步「開始前 4 件事」③ 選英文名）"


def no_code_keep(c: dict, d: dict | None = None) -> bool:
    """10-18 #246 修法 5（宇軒）：老師名字卡沒有英文名（代號空的）→ 照原聲留著，不生成成空的（以前名字被拿掉、只剩「來」）。
    敏感詞（替代詞空的＝刻意拿掉）、人工標的老師整段、人自己改過要念的字（改稿）照舊。"""
    return (not (c.get("代號") or "").strip() and not c.get("敏感詞") and not c.get("老師整段")
            and not ((d or {}).get("改稿") or "").strip())


def build_plan(candidates: list[dict], decisions: dict, sentences: dict[str, dict],
               default_how: str = WHOLE, words: list[dict] | None = None, cut: set | None = None,
               extend: bool = False, avoid=()) -> dict:
    """純函式：候選＋覆核決定＋句子（id → {start, end, text}）→ 處理計畫。

    做法：覆核決定的 `做法` 優先；沒有就用 default_how（09-25 宇軒定案：預設整句換掉；
    第 1 步的「建議做法」只當成覆核時的參考）。整句換掉時，Groq 的半句照標點擴成完整句子
    （`expand_sentence`），同一個完整句子裡的名字合成一筆、一次換完。

    回傳 {"生成": [...句子清單...], "消音": [...], "略過": [...], "要人處理": [...]}，
    每一筆都帶 `候選` 編號（1 起算，跟覆核決定的 id 一致）。
    `words`（逐字時間）給了的話，整句太長時只重念名字所在的那一小句（`whole_slot`，10-01），
    這種項目帶 `範圍`（自動／人選）與 `整句`（原本整句的起訖），文字照範圍裡逐字稿的字。
    `cut`（10-01 第三批）：落在剪掉的片段裡的候選編號（字串）→ 放進略過，不生成、不消音（剪掉的地方本來就沒有聲音）。
    `extend`（10-04 #62）：整句換掉的範圍斷在話中間時延伸到標點或真的停頓（`whole_slot` 的 around）；
    延伸時不碰其他名字候選、別張卡最多會重念到的地方（`other_ranges`）與 `avoid`（保留原聲學員的名字候選）。
    """
    gen, mutes, skipped, manual = [], [], [], []
    blocked = no_extend_groups(candidates, decisions, sorted(sentences.values(), key=lambda x: x["start"]),
                               default_how, cut) if extend else set()
    ranged: list[dict] = []       # 縮小範圍的（範圍疊在一起的名字併成一筆）
    whole: dict[str, dict] = {}   # 完整句子第一段的 id → 生成項目（同一句合併）
    ordered = sorted(sentences.values(), key=lambda x: x["start"])
    pos = {x["id"]: k for k, x in enumerate(ordered)}

    for i, c in enumerate(candidates, start=1):
        i = c.get("id", i)             # 人工補的名字（覆核工作台「新增修改」）自己帶 id（NM001…）
        d = decisions.get(str(i), {}) or {}
        tags = set(d.get("tags", []))
        if cut and str(i) in cut:
            skipped.append({"候選": i, "原因": CUT_SKIP})
            continue
        if c.get("同一處"):   # 10-03 第八批（#12）：同一處比中好幾個人，已經併進主卡，照主卡處理
            skipped.append({"候選": i, "原因": f"跟第 {c['同一處']} 筆是同一處（合成一張卡，照那一張處理）",
                            "同一處": c["同一處"]})
            continue
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

        if no_code_keep(c, d):   # 10-18 #246 修法 5：沒有英文名不生成成空的，照原聲留著（第 4 步提醒、第 5 步「需留意」列出）
            skipped.append({"候選": i, "原因": NO_CODE_SKIP})
            continue

        if how == NAME_ONLY:
            gen.append({"id": f"N{_num(i)}", "text": c["代號"], "slot": [c["start"] - pad, c["end"] + pad], "候選": [i]})
            continue

        sid = home_sentence(ordered, pos, c)
        if sid not in pos:
            manual.append({"候選": i, "原因": "逐字稿裡找不到這個名字所在的句子"})   # 10-01 1-5：不寫內部編號
            continue
        group = expand_sentence(ordered, pos[sid])
        if c.get("改過時間"):   # 09-29 宇軒：改時間把後面幾秒也納進來（逐字稿漏了第二次叫名字）→ 範圍內的句子一起重念
            group = [g for g in ordered if g["end"] > min(c["start"], group[0]["start"]) + 0.05
                     and g["start"] < max(c["end"], group[-1]["end"]) - 0.05 and (g in group or ok_teacher(g))] or group
        ext = extend and group[0]["id"] not in blocked   # 問題 D：同一句有卡不能延伸 → 整句都不延伸
        ws = whole_slot(c, d, group, words, neighbors(ordered, group) if ext else None,
                        other_ranges(candidates, c, avoid, ordered) if ext else ())
        if ws["範圍"]:
            _add_ranged(ranged, manual, c, i, d, ws, words)
            continue
        key = group[0]["id"]
        item = whole.get(key)
        # 名字只在原本那一段裡換（避免換到前後段同音的字），再接成整句
        texts = item["_texts"] if item else {g["id"]: g["text"] for g in group}
        if item:   # #296：先建的那一組沒含這一組的某幾句（例如名字所在句標「不是老師」，往前接上老師句）→ 補進去再查
            for g in group:
                texts.setdefault(g["id"], g["text"])
            item["句子"] = sorted(set(item["句子"]) | {g["id"] for g in group}, key=lambda k: pos.get(k, 0))
        new = mark_name(texts[sid], c)   # 10-18 #245：小段裡先留記號，接成整句後才去句首句尾的空白
        edited = (d.get("改稿") or "").strip()
        if new is None and not item and words:
            # 10-01（17 號 2-7）：找名字掃的是逐字時間的字，句子文字是另一份轉文字結果，寫法可能不一樣
            # → 句子裡找不到比對到的字時，改用這段時間逐字時間的字（裡面一定有抓到的那幾個字）
            lo_, hi_ = min(group[0]["start"], c["start"]), max(group[-1]["end"], c["end"])
            if replace_name(range_words(words, lo_, hi_), c) is not None:
                _add_ranged(ranged, manual, c, i, d, {"start": lo_, "end": hi_, "範圍": "逐字", "整句": [lo_, hi_]}, words)
                continue
        if new is None and item and _same_card_in(item, c, candidates):
            # 10-03 補修：同一張卡（同一句同代號）的另一處已經在這一句裡換好了（名字疊在一起、字已經換掉）→ 併進那一句
            item["候選"].append(i)
            item["slot"] = [min(item["slot"][0], c["start"]), max(item["slot"][1], c["end"])]
            continue
        if new is None and not edited and not (item and item.get("改稿")):
            manual.append({"候選": i, "原因": "句子裡找不到比對到的字，無法自動換成代號"})
            continue
        if new is not None:     # 找不到字但人已經改好要念的句子（09-30）：照人改的念，不算「要人處理」
            texts[sid] = new    # 10-03 第八批（#12）：同一句另一張卡已經整句改好（改稿）→ 併進那一句，跟縮小範圍的一樣
        full = finish_drops("".join(texts[g["id"]] for g in group))[0]
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
    plan = {"生成": gen, "消音": mutes, "略過": skipped, "要人處理": manual}
    _apply_covers(plan, candidates, decisions, default_how)
    return plan


COVER_TOL_S = 0.05


def _apply_covers(plan: dict, candidates: list[dict], decisions: dict, default_how: str = WHOLE) -> None:
    """10-03 第八批（#12）：名字（整句換掉、沒標不是名字），整個落在另一筆**已通過**、整句換掉的
    重念範圍裡 → 由那一句處理：從「要人處理」拿掉（還沒通過的，連自己單獨一筆的生成也拿掉），併進那一句的 `候選`，記在 `涵蓋`。
    跟覆核工作台 `review.mark_name_covers` 同一個規則（卡片上寫「已由那一張涵蓋」）。那一筆退回、改做法，就不再涵蓋。"""
    def dec(i) -> dict:
        return decisions.get(str(i), {}) or {}

    def plain(i) -> bool:
        d = dec(i)
        return (d.get("做法") or default_how) == WHOLE and not (set(d.get("tags", [])) & SKIP_TAGS)

    by_id = {str(c.get("id", i)): c for i, c in enumerate(candidates, start=1)}
    covers = [g for g in plan["生成"] if str(g.get("id", "")).startswith("S") and not g.get("整段")
              and any(dec(k).get("已確認") and plain(k) for k in g.get("候選", []))]
    if not covers:
        return
    out: dict[str, str] = {}
    for key, c in by_id.items():
        if not plain(key) or c.get("老師整段") or c.get("同一處"):
            continue
        g = next((g for g in covers if key not in {str(x) for x in g["候選"]}
                  and g["slot"][0] - COVER_TOL_S <= c["start"] and c["end"] <= g["slot"][1] + COVER_TOL_S), None)
        if g is None:
            continue
        own = [x for x in plan["生成"] if x is not g and key in {str(y) for y in x.get("候選", [])}]
        if any(len(x["候選"]) > 1 for x in own) or (own and dec(key).get("已確認")):
            continue   # 自己那一句還有別的名字、或這一筆自己通過了要生成的句子：照舊（已通過的只從「要人處理」救回）
        in_manual = [m for m in plan["要人處理"] if str(m["候選"]) == key]
        if not own and not in_manual:
            continue
        plan["生成"] = [x for x in plan["生成"] if not any(x is o for o in own)]
        plan["要人處理"] = [m for m in plan["要人處理"] if str(m["候選"]) != key]
        g["候選"].append(c.get("id", int(key) if key.isdigit() else key))
        out[key] = g["id"]
    if out:
        plan["涵蓋"] = out


def _same_card_in(item: dict, c: dict, candidates: list[dict]) -> bool:
    """這一筆跟生成項目裡已經有的某一筆是同一張卡（同一句、同一個代號，`review._mark_name_cards` 標的）。"""
    key = c.get("同一張卡")
    if not key:
        return False
    by_id = {str(x.get("id", k)): x for k, x in enumerate(candidates, start=1)}
    return any((by_id.get(str(k)) or {}).get("同一張卡") == key for k in item.get("候選", []))


def _names_in(text: str, cands: list[dict]) -> str | None:
    """範圍裡的字，名字一個一個換成代號；有任何一個找不到就回傳 None。
    10-03 補修：同一張卡（同一句同代號）的另一處已經換到的，這一處找不到不算（字已經換成代號了）。"""
    done: set[str] = set()
    for c in cands:
        new = mark_name(text, c)
        if new is None:
            if c.get("同一張卡") and c["同一張卡"] in done:
                continue
            return None
        text = new
        if c.get("同一張卡"):
            done.add(c["同一張卡"])
    return finish_drops(text)[0]   # 10-18 #245：範圍裡的字就是整句，換完才去句首句尾的空白


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
    if ws["範圍"] == "人選" or item["範圍"] == "逐字":
        item["範圍"] = ws["範圍"]
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
                    "候選": rng["候選"][0], "原因": f"重念範圍跟重疊 {wd.fmt_time(o['start'])}（選了生成老師聲音）只疊到一部分："
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


IMPORTED_PATH = Path("覆核") / "覆核結果.json"   # 舊版「匯入覆核結果」（10-23 已刪）放進來的覆核結果；舊工作區照讀


def make_plan(workdir: str | Path, only: list[int] | None = None) -> dict:
    """`bookclub gen names` 的第一步：排出計畫、寫出句子清單。only 給了就只處理那幾筆候選（測試用）。

    舊版「匯入覆核結果」建的工作區（10-23 起這個功能已刪除，舊工作區照讀）沒有名字候選與逐字稿，改用覆核結果裡
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


def refresh_plan(workdir: str | Path) -> dict | None:
    """10-02 第七批（A1）：第 4 步每次執行開頭都重排名字處理計畫（組裝讀這個檔）。

    以前只有「老師名字有句子要生成」那一步才寫，名字全部選直接消音、或跑過一次後把某句改成直接消音，
    組裝拿到的是舊的或空的計畫（名字沒消音）。沒有名字候選、也不是匯入的工作區 → 不用排，回 None。
    內容跟檔案一樣就不重寫（`_write_plan`）。"""
    workdir = Path(workdir).expanduser()
    if not wd.read_json(wd.names_path(workdir), default=None) and not (workdir / IMPORTED_PATH).exists():
        return None
    try:
        return make_plan(workdir)
    except FileNotFoundError:   # 匯入的覆核結果裡沒有計畫
        return None


def current_plan(workdir: str | Path) -> dict | None:
    """10-20 總審查：第 4 步開頭 `refresh_plan` 會寫進計畫檔的那一份（不寫檔）；不用排的回 None。
    給 `execute.render_done` 比對「組裝用的計畫檔」是不是還是現在的做法。"""
    workdir = Path(workdir).expanduser()
    names = wd.read_json(wd.names_path(workdir), default=None)
    if names:
        return compute_plan(workdir, names)
    exported = wd.read_json(workdir / IMPORTED_PATH, default=None) if (workdir / IMPORTED_PATH).exists() else None
    if exported and exported.get("名字處理計畫") is not None:
        return exported["名字處理計畫"]
    return None


PLAN_KEY_PARTS = ("生成", "消音")   # 會改變成品的部分（略過、要人處理只影響提醒，不影響聲音）
_PLAN_KEY_SKIP = ("更新時間", "時間戳", "產生時間")


def plan_key(plan: dict | None) -> str:
    """10-20 總審查：名字處理計畫（老師或保留原聲學員那一份）會影響成品的內容做成一個指紋。
    只看生成、消音；每一筆的順序、時間戳不算（排序不同、重寫一次不會被當成改了做法）。"""
    import hashlib

    def clean(x):
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items() if k not in _PLAN_KEY_SKIP}
        if isinstance(x, list):
            return [clean(v) for v in x]
        return x

    plan = json.loads(json.dumps(plan or {}, ensure_ascii=False))   # 跟寫進檔案後讀回來的一樣（tuple→list 等）
    body = {k: sorted((json.dumps(clean(e), ensure_ascii=False, sort_keys=True) for e in plan.get(k) or []))
            for k in PLAN_KEY_PARTS}
    return hashlib.sha1(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def compute_plan(workdir: Path, names: dict | None = None, only: list[int] | None = None) -> dict:
    """排出處理計畫（不寫檔）：`make_plan` 用（以前的匯出覆核結果也用同一支，10-23 已刪）。"""
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
    decisions = review.card_decisions(candidates, decisions)   # 10-03 補修：一張卡的決定套到同一句同代號的每一處
    # 1016-3：③ 這一集選了「不用處理／不是名字」、沒有代號的名字 → 照原聲留著（只在計算時略過，不寫回覆核決定）。
    # 10-18 #250：名冊上的人也一樣照原聲（推翻 1016-3「決定時名冊上就有的照樣拿掉」）；名冊上是別人的寫法照舊換
    # （epcodes.kept_spellings）。第 5 步「需留意」列出這些地方（execute.kept_name_rows）
    from bookclub import epcodes

    skip3 = epcodes.keep_original_names(workdir)
    if skip3:
        decisions = dict(decisions)
        for i, c in enumerate(candidates, start=1):
            if epcodes.is_kept_original(c, skip3):
                k = str(c.get("id") or i)
                d = dict(decisions.get(k) or {})
                d["tags"] = sorted(set(d.get("tags") or []) | {"不是名字"})
                decisions[k] = d
    if only:
        candidates = [c for c in candidates if "id" not in c]     # 測試只處理幾筆時，人工補的先不做
        keep = set(only)
        decisions = dict(decisions)
        for i in range(1, len(candidates) + 1):
            if i not in keep:
                decisions[str(i)] = {"tags": ["不是名字"]}  # 只在計算時略過，不寫回覆核決定
    merged = wd.read_json(wd.merged_transcript_path(workdir), default={}) or {}
    words = merged.get("words") or []
    # 10-01 第三批：名字落在剪掉的片段裡 → 不生成（以前照樣生成、第 4 步也算進去；跟第 3 步「已剪掉」同一個判斷）。
    # 剪掉的片段還原，下一次排計畫就回到要生成
    cuts = [(c["start"], c["end"]) for c in review.load_decisions(workdir)["刪除段落"] if c.get("狀態") != "還原"]
    cut = {str(c.get("id") or i) for i, c in enumerate(candidates, start=1) if review._in_ranges(c["start"], c["end"], cuts)}
    extend = extend_enabled(merged)
    plan = build_plan(candidates, decisions, sentences, words=words, cut=cut, extend=extend,
                      avoid=student_name_ranges(workdir) if extend else ())
    cap_at_student_turns(plan, workdir, sentences)
    if not only:   # 09-30：重疊選「生成老師聲音」的，老師整句一起排進生成清單
        choices = [o for o in review.overlap_choices(workdir) if not review._in_ranges(o["start"], o["end"], cuts)]
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
        check_no_drop_mark(g["text"])   # 10-18 #245：要念的字裡不能留著拿掉名字的記號
    blank_to_mute(plan)
    if only:
        plan["略過"] = [s for s in plan["略過"] if s["候選"] in set(only)]
        plan["只處理"] = sorted(set(only))
    return plan


def cap_end_at_turns(items: list[dict], group_ends: dict, starts: list[float]) -> None:
    """整句重念的時間格，因為名字的聲音比句子結束晚而往後延，延進了會整段重念的學員段落（純函式，就地改 items，#298）：
    延進去的那一點會跟學員那一格搶，整句被扣成消音，老師點名的話不見 → 時間格收回到句子結束。
    延進去的那一點由學員那一格蓋掉（跟以前一樣）。`group_ends`：{生成編號: 句子結束}；沒有記就不動。"""
    for g in items:
        end0 = group_ends.get(g.get("id"))
        if end0 is None:
            continue
        for t0 in starts:
            if end0 - 0.05 <= t0 < g["slot"][1] and g["slot"][0] < t0:
                g["slot"] = [g["slot"][0], round(t0, 3)]
                break


def cap_at_student_turns(plan: dict, workdir: Path, sentences: dict[str, dict]) -> None:
    """`cap_end_at_turns` 的呼叫端：只看會整段重念（不是保留原聲）的學員段落；還沒有段落資料就不動。"""
    from bookclub import review
    from bookclub import turns as turns_mod

    tdata = turns_mod.page_data(workdir)
    if tdata.get("尚未準備"):
        return
    kept = {k for k, v in review.load_decisions(workdir)["學員聲音"].items() if v == "保留原聲"}
    starts = sorted(float(t["start"]) for t in tdata.get("段落", [])
                    if t.get("說話者") not in (None, "老師") and t.get("說話者") not in kept)
    ends = {g["id"]: max(sentences[i]["end"] for i in g["句子"] if i in sentences)
            for g in plan["生成"] if any(i in sentences for i in g.get("句子") or [])}
    cap_end_at_turns(plan["生成"], ends, starts)


BLANK_KEY = "拿掉後沒有字"   # 10-20 總審查：消音那一筆是「要念的整句拿掉後沒有字」改來的（第 5 步「需留意」列出來）


def blank_text(text: str | None) -> bool:
    """要念的字是空的、或只剩標點空白（純函式）。"""
    return not any(ch.isalnum() for ch in (text or ""))


def blank_to_mute(plan: dict) -> None:
    """10-20 總審查（隱私）：老師要念的一整句只有替代詞留空的敏感詞（或名字），拿掉後沒有字（或只剩標點空白）
    → 不生成、整句改成消音（就地改）。以前生成程式看到空的會改念原文（`tts.fill_teacher_text`），等於把那個詞念出來。
    人設定替代詞留空＝人決定刪，消音符合原則；消音那一筆標 `拿掉後沒有字`，第 5 步「需留意」列一行讓人聽。"""
    keep, mutes = [], []
    for g in plan.get("生成") or []:
        if not blank_text(g.get("text")):
            keep.append(g)
            continue
        cands = list(g.get("候選") or []) or [g["id"]]
        for c in cands:
            mutes.append({"候選": c, "start": g["slot"][0], "end": g["slot"][1], BLANK_KEY: True, "生成編號": g["id"],
                          **({"重疊項目": g["重疊項目"]} if g.get("重疊項目") else {})})
    if mutes:
        plan["生成"] = keep
        plan["消音"] = list(plan.get("消音") or []) + mutes


def _write_plan(workdir: Path, plan: dict, n_cands: int | None = None, only: list[int] | None = None,
                note: str = "") -> dict:
    # 10-02 第七批（A1）：內容跟檔案一樣就不重寫——每次執行都會重排，改了修改時間組裝會以為要重做
    sents = [{k: g[k] for k in ("id", "text", "slot")} for g in plan["生成"]]
    if wd.read_json(sentences_path(workdir), default=None) != sents:
        wd.write_json(sentences_path(workdir), sents)
    if wd.read_json(plan_path(workdir), default=None) != json.loads(json.dumps(plan, ensure_ascii=False)):
        wd.write_json(plan_path(workdir), plan)
    print(f"[名字處理] {note}" + (f"候選 {n_cands} 筆" if n_cands is not None else "")
          + (f"（這次只處理 {len(only)} 筆）" if only else "")
          + f"：生成 {len(plan['生成'])} 段、消音 {len(plan['消音'])} 段、略過 {len(plan['略過'])} 筆、"
          f"要人處理 {len(plan['要人處理'])} 筆")
    return plan
