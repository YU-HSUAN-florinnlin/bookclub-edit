"""照老師原本停頓的位置，在生成的聲音裡插入空白（宇軒 09-25 定案）。

老師講話常常一句話中間停很久（想下一個字、重複一次重點）。整句生成出來的
AI 聲音會一口氣念完，比原片短很多，放回時間格嘴型也對不上。做法：

1. 原片那一段用音量找出停頓（≥ 0.3 秒、比這段最大聲低 35 dB）
2. 用逐字對位模型（Qwen3-ForcedAligner）找出原片每個字的時間，確認每個
   停頓落在哪兩個字之間。Groq 的逐字時間會把停頓算進字的長度裡（「如果」
   佔 1.6 秒），不能直接用
3. 生成的聲音也對位一次，把「原片第 i 個字後面的停頓」對應到生成聲音裡
   同一個字後面；**只在標點的位置插入**，落在句子中間的停頓移到最近的
   標點（宇軒 09-25 試聽：老師常在句子中間停下來想，照原位置補會「斷在
   半空中」）。生成本來就有的空白會扣掉，不重複加
4. 開頭也照原片對齊：原片老師在時間格第幾秒才開口，生成的聲音就從第幾秒開始

只增加空白、不動字本身，所以不需要重新生成。對位模型約 1 分鐘內，比生成一次
（約 2.5 分鐘）快。純函式跟模型分開：`detect_silences`、`pauses_after_chars`、
`plan_inserts`、`apply_inserts` 都能用合成資料測試。
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

import numpy as np

SILENCE_MIN_S = 0.3      # 短於這個秒數的安靜不算停頓（字與字之間本來就有短暫安靜）
SILENCE_DB = -35.0       # 比這段最大聲低這麼多 dB 算安靜
FRAME_S = 0.02
FADE_S = 0.01            # 插入點前後淡出淡入，避免爆音
SEARCH_PAD_S = 0.04      # 在兩字之間找最安靜的插入點時，往兩邊多看的秒數


@dataclass
class Char:
    text: str
    start: float
    end: float


# ---------- 找停頓 ----------

def detect_silences(x: np.ndarray, sr: int, min_s: float = SILENCE_MIN_S, db: float = SILENCE_DB) -> list[tuple[float, float]]:
    """回傳 [(開始秒, 結束秒), ...]，這段聲音裡夠長的安靜區段。"""
    if x.ndim > 1:
        x = x.mean(axis=1)
    fr = max(1, int(sr * FRAME_S))
    n = len(x) // fr
    if n == 0:
        return []
    rms = np.sqrt(np.mean(x[: n * fr].reshape(n, fr).astype(np.float64) ** 2, axis=1)) + 1e-9
    quiet = 20 * np.log10(rms / rms.max()) < db
    out, i = [], 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if (j - i) * FRAME_S >= min_s:
                out.append((round(i * FRAME_S, 3), round(j * FRAME_S, 3)))
            i = j
        else:
            i += 1
    return out


def pauses_after_chars(chars: list[Char], silences: list[tuple[float, float]]) -> dict[int, float]:
    """把每段安靜對到「第 i 個字後面」。回傳 {i: 停頓秒數}。

    只算落在兩個字之間的安靜（開頭、結尾的空白另外處理）。停頓秒數取
    「下一個字開始 − 這個字結束」，也就是對位模型看到的實際間隔，
    同一個字後面有兩段安靜（中間夾一點呼吸聲）也只算一次。
    """
    out: dict[int, float] = {}
    for s, e in silences:
        mid = (s + e) / 2
        for i in range(len(chars) - 1):
            if chars[i].end <= mid <= chars[i + 1].start:
                out[i] = round(chars[i + 1].start - chars[i].end, 3)
                break
    return out


def speech_start(x: np.ndarray, sr: int, db: float = SILENCE_DB, min_active_s: float = 0.1) -> float:
    """開始講話的時間（秒）：第一段連續 min_active_s 秒以上夠大聲的地方（規則同 trim_silence）。找不到回傳 0。"""
    if x.ndim > 1:
        x = x.mean(axis=1)
    fr = max(1, int(sr * FRAME_S))
    n = len(x) // fr
    if n == 0:
        return 0.0
    rms = np.sqrt(np.mean(x[: n * fr].reshape(n, fr).astype(np.float64) ** 2, axis=1)) + 1e-9
    active = 20 * np.log10(rms / rms.max()) >= db
    run = max(1, int(round(min_active_s / FRAME_S)))
    for i in range(n - run + 1):
        if active[i:i + run].all():
            return i * fr / sr
    return 0.0


def trim_silence(x: np.ndarray, sr: int, db: float = SILENCE_DB, min_active_s: float = 0.1,
                 keep_s: float = 0.05) -> np.ndarray:
    """裁掉頭尾的空白，前後各留 keep_s 秒。

    CosyVoice 生成的聲音開頭常有 0.5 秒以上的空白（09-25 第一堂名字句實測：
    2 秒的句子前面空了 0.68 秒），不裁掉長度判斷會失準。只有連續 min_active_s
    秒以上夠大聲才算開始講話，開頭零點幾秒的小雜音不算。
    """
    if x.ndim > 1:
        x = x.mean(axis=1)
    fr = max(1, int(sr * FRAME_S))
    n = len(x) // fr
    if n == 0:
        return x
    rms = np.sqrt(np.mean(x[: n * fr].reshape(n, fr).astype(np.float64) ** 2, axis=1)) + 1e-9
    active = 20 * np.log10(rms / rms.max()) >= db
    run = max(1, int(round(min_active_s / FRAME_S)))
    starts = [i for i in range(n - run + 1) if active[i:i + run].all()]
    if not starts:
        return x
    first = starts[0]
    last = starts[-1] + run  # 最後一段連續講話的結尾（音框）
    keep = int(keep_s * sr)
    return x[max(0, first * fr - keep): min(len(x), last * fr + keep)]


# ---------- 對應到生成的聲音 ----------

def map_chars(orig: list[Char], gen: list[Char]) -> dict[int, int]:
    """原片第 i 個字 → 生成第 j 個字。只收兩邊文字對得上的字。"""
    a = [c.text for c in orig]
    b = [c.text for c in gen]
    out: dict[int, int] = {}
    for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            out[blk.a + k] = blk.b + k
    return out


def _quietest_point(x: np.ndarray, sr: int, lo: float, hi: float) -> float:
    """lo～hi 秒之間最安靜的 10 毫秒的中心點。"""
    fr = max(1, int(sr * 0.01))
    a, b = max(0, int(lo * sr)), min(len(x), int(hi * sr))
    if b - a <= fr:
        return (lo + hi) / 2
    seg = x[a:b]
    n = len(seg) // fr
    rms = np.sqrt(np.mean(seg[: n * fr].reshape(n, fr).astype(np.float64) ** 2, axis=1))
    k = int(np.argmin(rms))
    return (a + k * fr + fr / 2) / sr


PUNCT = set("，,。.、；;：:！!？?…—")


def punct_boundaries(gen: list[Char], text: str) -> set[int]:
    """生成文字裡「第 j 個字後面緊接著標點」的 j。對位模型給的字不含標點，
    這裡照順序在原文字裡找到每個字，再看它後面是不是標點。"""
    out: set[int] = set()
    pos = 0
    for j, c in enumerate(gen):
        k = text.find(c.text, pos)
        if k < 0:
            continue
        pos = k + len(c.text)
        rest = text[pos:].lstrip()
        if rest and rest[0] in PUNCT:
            out.add(j)
    return out


def snap_to_boundaries(wanted: dict[int, float], boundaries: set[int], last: int) -> dict[int, float]:
    """不在標點上的停頓移到最近的標點（距離一樣時往前）；同一個位置取最長的。
    最後一個字的標點不算（結尾空白交給補靜音）。"""
    spots = sorted(b for b in boundaries if b < last)
    if not spots:
        return dict(wanted)
    out: dict[int, float] = {}
    for j, dur in wanted.items():
        k = j if j in boundaries else min(spots, key=lambda b: (abs(b - j), b > j))
        out[k] = max(out.get(k, 0.0), dur)
    return out


def plan_inserts(
    orig: list[Char], orig_pauses: dict[int, float], gen: list[Char], gen_x: np.ndarray, sr: int,
    snap_text: str | None = None,
) -> list[tuple[float, float]]:
    """回傳 [(生成聲音裡的插入秒數, 要補幾秒空白), ...]，依時間排序。

    原片停頓的那個字如果在生成的文字裡找不到（例如原話重複的字被刪掉），
    往前找最近一個對得上的字；對到同一個位置的多個停頓取最長的，不相加。

    snap_text 給了（要念的文字）就只在標點的位置插入，`bookclub/tts.py` 一律
    這樣用：老師常在句子中間停（想下一個字），照原位置補空白會「斷在半空中」
    （宇軒 09-25 試聽）。不給 snap_text 是照原位置補，只留給測試對照。
    """
    mapping = map_chars(orig, gen)
    wanted: dict[int, float] = {}
    for i, dur in orig_pauses.items():
        k = i
        while k >= 0 and k not in mapping:
            k -= 1
        if k < 0:
            continue
        j = mapping[k]
        if j >= len(gen) - 1:
            continue  # 最後一個字後面的空白交給補靜音
        wanted[j] = max(wanted.get(j, 0.0), dur)

    if snap_text is not None:
        wanted = snap_to_boundaries(wanted, punct_boundaries(gen, snap_text), len(gen) - 1)

    inserts = []
    for j, dur in sorted(wanted.items()):
        existing = max(0.0, gen[j + 1].start - gen[j].end)
        add = round(dur - existing, 3)
        if add < 0.05:
            continue
        at = _quietest_point(gen_x, sr, gen[j].end - SEARCH_PAD_S, gen[j + 1].start + SEARCH_PAD_S)
        inserts.append((round(at, 3), add))
    return inserts


def lead_offset(orig: list[Char], gen: list[Char]) -> float:
    """原片老師開口的時間 − 生成聲音開口的時間：正數表示前面要補空白，負數表示要裁掉。"""
    if not orig or not gen:
        return 0.0
    return round(orig[0].start - gen[0].start, 3)


def apply_inserts(x: np.ndarray, sr: int, inserts: list[tuple[float, float]], lead: float = 0.0) -> np.ndarray:
    """在指定位置插入空白（前後 10 毫秒淡出淡入），並照 lead 在開頭補空白或裁掉。"""
    if x.ndim > 1:
        x = x.mean(axis=1)
    x = x.astype(np.float32)
    fade = max(1, int(sr * FADE_S))
    inserts = sorted(inserts)
    cuts = [min(max(int(at * sr), 0), len(x)) for at, _ in inserts]
    bounds = [0] + cuts + [len(x)]
    pieces = []
    for k in range(len(bounds) - 1):
        seg = x[bounds[k]:bounds[k + 1]].copy()
        if len(seg) >= 2 * fade:
            if k > 0:
                seg[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
            if k < len(cuts):
                seg[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
        pieces.append(seg)
        if k < len(cuts):
            pieces.append(np.zeros(int(inserts[k][1] * sr), dtype=np.float32))
    y = np.concatenate(pieces)
    if lead > 0:
        y = np.concatenate([np.zeros(int(lead * sr), dtype=np.float32), y])
    elif lead < 0:
        y = y[min(len(y), int(-lead * sr)):]
    return y


# ---------- 對位模型 ----------

class Aligner:
    """Qwen3-ForcedAligner 包裝：載入一次，之後每段聲音呼叫 `align(路徑, 文字)`。"""

    def __init__(self):
        import torch
        from qwen_asr import Qwen3ForcedAligner

        from bookclub.config import aligner_model_id

        self.model = Qwen3ForcedAligner.from_pretrained(aligner_model_id(), dtype=torch.float32, device_map="cpu")

    def align(self, path, text: str) -> list[Char]:
        r = self.model.align(audio=str(path), text=text, language="Chinese")
        return [Char(it.text, float(it.start_time), float(it.end_time)) for it in r[0]]
