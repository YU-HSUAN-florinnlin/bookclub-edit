"""10-04 #117：「有人聲但沒有字」的區間（純函式，不載入模型、不跑 VAD）。

為什麼要有：Groq 轉文字每次都會隨機漏掉幾段（整個 30 秒窗只回 2 個字、或完全沒有句子）。沒有字的地方，
找名字就找不到，名字可能直接留在成品裡。這裡把這些區間算出來，放進第 4 步「開始前總檢查」要人聽過。

資料來源跟 `transcribe.quiet_word_stats`、`inspect 字` 一樣：`transcript/merged.json` 的 `silence_map`
（第 1 步 VAD 判定的安靜處，原片絕對時間；舊做法挖停頓、新做法不挖停頓都有存）、`words`（每個字的起訖）、`duration`。
沒有 `silence_map` 或 `duration` 的工作區（很舊、或假資料）回傳 None：不列這類提醒、不報錯。

做法：
1. 人聲區間＝整支影片扣掉 VAD 安靜處
2. 一個字只算它開頭的前 `WORD_COVER_S` 秒有蓋到（Groq 會把一個字的時間拉長到十幾秒，蓋住其實沒轉出來的話）
3. 在人聲區間裡，連續 ≥ `MIN_PIECE_S` 秒沒有被任何字蓋到的，是一小段
4. 相鄰的小段中間沒有任何字（只隔著 VAD 安靜，或不到 1.5 秒、也沒有字的零星人聲）就併成一處；
   記這一處的起訖、其中人聲合計幾秒（起訖之間的人聲都算，那裡一個字都沒有）、小段數
"""

from __future__ import annotations

# 10-04 第一堂三份逐字稿比對（同一支影片轉三次，漏掉的地方每次不同）定的值：
MIN_PIECE_S = 1.5      # 人聲連續這麼久沒有字才算一小段（短於這個多半是字跟字之間的換氣、拖長音）
WORD_COVER_S = 1.0     # 一個字只算開頭這麼久有蓋到（Groq 會把一個字的時間拉長到十幾秒）
MUST_VOICE_S = 3.0     # 總檢查：人聲合計這麼久以上放「一定要處理」，短的放「請看一眼」


def _merge(ivs: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(ivs):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def subtract(ivs: list[tuple[float, float]], holes: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """區間清單扣掉另一組區間（純函式，回傳照時間排、不重疊）。"""
    holes = _merge(holes)
    out = []
    for a, b in _merge(ivs):
        cur = a
        for x, y in holes:
            if y <= cur or x >= b:
                continue
            if x > cur:
                out.append((cur, x))
            cur = max(cur, y)
            if cur >= b:
                break
        if cur < b:
            out.append((cur, b))
    return out


def intersect(ivs: list[tuple[float, float]], a: float, b: float) -> list[tuple[float, float]]:
    return [(max(x, a), min(y, b)) for x, y in ivs if min(y, b) > max(x, a)]


def total(ivs: list[tuple[float, float]]) -> float:
    return sum(b - a for a, b in ivs)


def speech_ranges(duration: float, silence_map: list[dict]) -> list[tuple[float, float]]:
    """人聲區間＝整支 [0, duration] 扣掉 VAD 安靜處。"""
    return subtract([(0.0, float(duration))], [(float(s["start"]), float(s["end"])) for s in silence_map or []])


def word_cover(words: list[dict], cover_s: float = WORD_COVER_S) -> list[tuple[float, float]]:
    """每個字蓋到的範圍：開頭到 min(結尾, 開頭＋cover_s)。"""
    out = []
    for w in words or []:
        try:
            s, e = float(w["start"]), float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append((s, max(s, min(e, s + cover_s))))
    return _merge(out)


def find_regions(duration: float | None, silence_map: list[dict] | None, words: list[dict] | None,
                 min_piece: float = MIN_PIECE_S, cover_s: float = WORD_COVER_S) -> list[dict] | None:
    """有人聲但沒有字的區間（純函式）。回傳 [{start, end, 人聲秒, 小段數, 人聲區間: [[起, 訖], ...]}]，照時間排；
    沒有安靜處清單或影片長度時回傳 None（資料不夠，不列）。"""
    if silence_map is None or not duration:
        return None
    speech = speech_ranges(duration, silence_map)
    cover = word_cover(words or [], cover_s)
    # 沒有被任何字蓋到的時間（整條時間軸），每一段裡面一個字都沒有 → 裡面的小段可以併成一處
    free = subtract([(0.0, float(duration))], cover)
    out = []
    for fa, fb in free:
        sp = intersect(speech, fa, fb)
        pieces = [(a, b) for a, b in sp if b - a >= min_piece]
        if not pieces:
            continue
        a, b = pieces[0][0], pieces[-1][1]
        inside = intersect(speech, a, b)
        out.append({"start": round(a, 3), "end": round(b, 3), "人聲秒": round(total(inside), 3), "小段數": len(pieces),
                    "人聲區間": [[round(x, 3), round(y, 3)] for x, y in inside]})
    return out


def from_merged(merged: dict | None) -> list[dict] | None:
    """`transcript/merged.json` 的內容 → `find_regions`（缺資料回傳 None）。"""
    merged = merged or {}
    return find_regions(merged.get("duration"), merged.get("silence_map"), merged.get("words"))


def stats(regions: list[dict] | None) -> dict | None:
    """統計（只有數字）：處數、人聲合計秒、≥3 秒的處數。"""
    if regions is None:
        return None
    return {"處數": len(regions), "人聲秒": round(sum(r["人聲秒"] for r in regions), 1),
            "三秒以上": sum(1 for r in regions if r["人聲秒"] >= MUST_VOICE_S)}


def summary_line(regions: list[dict] | None, fmt, top: int = 10) -> str:
    """第 1 步轉文字完成時印的那一行（只有數字與時間）。fmt：秒數 → 時間字串。"""
    st = stats(regions)
    if st is None:
        return "[1/轉文字] 有人聲但沒有字：沒有安靜處資料，不統計"
    head = (f"[1/轉文字] 有人聲但沒有字：{st['處數']} 處、人聲合計 {st['人聲秒']:.1f} 秒，"
            f"其中 {MUST_VOICE_S:.0f} 秒以上 {st['三秒以上']} 處")
    if not regions:
        return head
    return head + "：" + "、".join(f"{fmt(r['start'])}–{fmt(r['end'])}（{r['人聲秒']:.1f} 秒）" for r in regions[:top]) \
        + ("⋯" if len(regions) > top else "")


def classify(region: dict, cuts: list[tuple[float, float]], student_turns: list[tuple[float, float]]) -> tuple[str | None, float]:
    """總檢查要放哪一區（純函式）。回傳 (類別, 秒數)：
    - 扣掉會被剪掉的範圍後人聲不到 MIN_PIECE_S 秒 → (None, 0)：不列
    - 扣掉會整段重念的學員段落之後還有 ≥ MUST_VOICE_S 秒 → ("一定要處理", 那幾秒)
    - 還有 MIN_PIECE_S–MUST_VOICE_S 秒 → ("較短", 那幾秒)
    - 其他、在會整段重念的學員段落裡有 ≥ MIN_PIECE_S 秒 → ("學員段落", 在段落裡的秒數)
    - 段落裡外各一點點 → ("較短", 全部秒數)"""
    voice = [(float(a), float(b)) for a, b in region.get("人聲區間") or [(region["start"], region["end"])]]
    left = subtract(voice, cuts)
    if total(left) < MIN_PIECE_S:
        return None, 0.0
    out = subtract(left, student_turns)
    s_out = total(out)
    if s_out >= MUST_VOICE_S:
        return "一定要處理", round(s_out, 1)
    if s_out >= MIN_PIECE_S:
        return "較短", round(s_out, 1)
    s_in = total(left) - s_out
    if s_in >= MIN_PIECE_S:
        return "學員段落", round(s_in, 1)
    return "較短", round(total(left), 1)   # 段落裡外各一點點：當成短的
