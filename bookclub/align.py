"""第 3 步「新增修改」：人標的起訖，對齊到小數點以下的精準時間（09-29 宇軒）。

人在影片上標起點終點，手會慢零點幾秒、也看不準字從哪裡開始。按「新增」之後後端照類型對齊：

| 類型 | 對齊到 | 為什麼 |
| --- | --- | --- |
| 刪除段落、局部消音 | 附近的安靜處（`review.snap_to_quiet`） | 剪在講話中間會切掉半個字、有爆音 |
| 老師提到名字 | 逐字稿裡字的時間（`merged.json` 的 `words`），前後留一點停頓 | 名字只有一兩個字，要剛好蓋住那幾個字 |
| 學員發言、重疊 | 句子邊界（`merged.json` 的 `sentences`） | 重念、重疊都是整句處理，斷在句子中間念起來不自然 |

附近找不到可以對齊的地方：保留人標的時間，標「沒對齊」（不猜）。兩端各自判斷。

這支只有純函式（不讀檔），`bookclub/review.py` 讀好資料再呼叫，單元測試直接餵假資料。
"""

from __future__ import annotations

from typing import Callable

QUIET, WORDS, SENTENCES = "安靜處", "逐字稿的字", "句子邊界"
RULES = {"刪除段落": QUIET, "局部消音": QUIET, "名字": WORDS, "學員發言": SENTENCES, "重疊": SENTENCES}

WORD_PAD_S = 0.05      # 名字前後留的停頓（跟找名字切點的建議緩衝一樣）
WORD_SEARCH_S = 0.5    # 標的範圍裡沒有字時，往外找最近的字最多這麼遠
SENT_SEARCH_S = 1.0    # 起點／終點離句子邊界這麼近才對過去（手標通常差不到 1 秒）


def _mid(x: dict) -> float:
    return (x["start"] + x["end"]) / 2


def _result(a: float, b: float, na: float | None, nb: float | None, rule: str, text: str = "") -> dict:
    """兩端各自：有對到就用對齊後的時間，沒對到保留人標的。對完變成終點不晚於起點就整個不對。"""
    s = round(na, 3) if na is not None else round(a, 3)
    e = round(nb, 3) if nb is not None else round(b, 3)
    ok = [na is not None, nb is not None]
    if e <= s:
        s, e, ok = round(a, 3), round(b, 3), [False, False]
    return {"start": s, "end": e, "標的起訖": [round(a, 3), round(b, 3)], "對齊": ok, "對齊到": rule, "文字": text}


def align_quiet(a: float, b: float, snap: Callable[[float], tuple[float, bool]]) -> dict:
    """刪除段落、局部消音：兩端各自對到附近的安靜處。snap(t) → (時間, 有沒有找到安靜處)。"""
    ta, ca = snap(a)
    tb, cb = snap(b)
    return _result(a, b, ta if ca else None, tb if cb else None, QUIET)


def align_words(a: float, b: float, words: list[dict], pad: float = WORD_PAD_S,
                search: float = WORD_SEARCH_S) -> dict:
    """老師提到名字：蓋住標的範圍裡的字，前後各留 pad 秒，但不吃進前後相鄰的字。

    哪幾個字：字的中點落在範圍裡的；一個都沒有就看跟範圍有重疊的；還是沒有就找 search 秒內最近的一個字。
    回傳的 `文字` 是蓋到的字接起來（名字處理拿來當「比對到的字」）。"""
    ws = sorted((w for w in words if w.get("end", 0) > w.get("start", 0)), key=lambda w: w["start"])
    hit = [k for k, w in enumerate(ws) if a <= _mid(w) <= b] \
        or [k for k, w in enumerate(ws) if w["start"] < b and a < w["end"]]
    if not hit and ws:
        dist = [max(0.0, w["start"] - b, a - w["end"]) for w in ws]
        k = min(range(len(ws)), key=dist.__getitem__)
        hit = [k] if dist[k] <= search else []
    if not hit:
        return _result(a, b, None, None, WORDS)
    i, j = hit[0], hit[-1]
    first, last = ws[i], ws[j]
    s = first["start"] - pad
    if i > 0:
        s = max(s, ws[i - 1]["end"])
    e = last["end"] + pad
    if j + 1 < len(ws):
        e = min(e, ws[j + 1]["start"])
    s, e = max(0.0, min(s, first["start"])), max(e, last["end"])
    text = "".join(str(w.get("word", "")).strip() for w in ws[i:j + 1])
    return _result(a, b, s, e, WORDS, text)


def align_sentences(a: float, b: float, sentences: list[dict], search: float = SENT_SEARCH_S) -> dict:
    """學員發言、重疊：起點對到最近的句子開頭、終點對到最近的句子結尾（各自 search 秒內）。"""
    ss = sorted(sentences, key=lambda s: s["start"])

    def nearest(t: float, key: str) -> float | None:
        best = min((s[key] for s in ss), key=lambda v: abs(v - t), default=None)
        return best if best is not None and abs(best - t) <= search else None

    na, nb = nearest(a, "start"), nearest(b, "end")
    r = _result(a, b, na, nb, SENTENCES)
    r["文字"] = "".join(s.get("text", "") for s in ss if r["start"] <= _mid(s) <= r["end"])
    return r


def sentence_at(t: float, sentences: list[dict]) -> dict | None:
    """時間 t 落在哪一句（名字新增時找所在的句子，整句換掉要用）。落在兩句中間就挑最近的。"""
    inside = [s for s in sentences if s["start"] <= t <= s["end"]]
    if inside:
        return inside[0]
    return min(sentences, key=lambda s: min(abs(s["start"] - t), abs(s["end"] - t)), default=None)
