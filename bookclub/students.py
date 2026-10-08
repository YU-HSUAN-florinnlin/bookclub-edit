"""流程第 4 步（學員）：學員段落用匿名聲線（男聲／女聲）整段重念（09-28 測試版）。

做法沿用老師 AI 聲音（`bookclub/tts.py`：種子 42、Groq 轉回文字檢查、不過才換種子、照原片停頓
插入空白、放回時間格），差別：

- 參考音是匿名聲線（`~/讀書會剪輯資料/聲線/男聲_暫定.wav`／`女聲_暫定.wav`，Common Voice CC0），
  依學員分男女：名冊有性別就照名冊，沒有就用原音估基頻（中位數低於 165 Hz 算男聲）
- 切段（10-03 第八批 #61，做法甲）：只在句子之間、原片真的安靜 0.3 秒以上的地方切（量原片聲音，不看標點），
  切點放在停頓正中間；8 秒以上遇到停頓就切，找不到就往後接、最長約 25 秒，還是沒有就切在句子之間最安靜的一點，
  標「切在講話中」。同一段裡相鄰兩格頭尾相接（前一格的結束＝後一格的開始＝切點），不留原聲空隙；
  段落頭尾（隔著老師或別的段落、刪除範圍）照舊是句子的起訖
- 文字：第 3 步覆核的校對稿；還沒確認的段落用「建議稿」（名冊本名換成代號的初稿，轉文字的錯字會照念）
- 落在確認刪除段落裡的句子不生成
- 放回時間格用學員規則（`fit.py`）：比較短補靜音、長 15% 以內微調語速、再長標紅（組裝時用畫面停格補長）

輸出：`生成/學員/`（檔名規則同 `生成/老師/`）、`生成/學員紀錄.json`。不碰 `生成/老師/`
（參考音不同，混在一起會讓老師那邊判定參考音變了而全部重生成）。可以中斷續跑。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Callable

import numpy as np

from bookclub import workdir as wd

MIN_CHUNK_S, MAX_CHUNK_S = 8.0, 25.0   # 10-03 #61：8 秒以上遇到停頓就切；找不到停頓最長放寬到 25 秒
PAUSE_MIN_S = 0.3          # 10-03 #61：原片連續安靜這麼久才算真的停頓
PAUSE_DROP_DB = 20.0       # 比這個學員段落講話音量（20 毫秒一格的第 90 百分位）低這麼多 dB 算安靜（同 names.PAUSE_DB_DROP）
PAUSE_EDGE_TOL_S = 0.3     # 逐字稿的句子起訖會差零點幾秒：停頓可以落在句子交界前後這麼多秒內
QUIET_FRAME_S = 0.02
MID_SPEECH = "切在講話中"   # 生成紀錄、項目的欄位名：這一格的頭／尾切在沒有停頓的地方（原片秒的清單）
MALE_F0_HZ = 165.0


def voice_dir() -> Path:
    from bookclub.config import data_dir

    return data_dir() / "聲線"


def default_refs() -> dict[str, Path]:
    """沒有候選聲線時的後備（09-28 暫定：男 1、女 1 各一份）。"""
    return {"男": voice_dir() / "男聲_暫定.wav", "女": voice_dir() / "女聲_暫定.wav"}


CANDIDATE_DIR = "候選_0928"
SKIP_VOICES = {"女5"}   # 09-29 宇軒聽過：女 5 不用


def voice_pool(base: Path | None = None) -> dict[str, list[Path]]:
    """學員匿名聲線候選（09-30）：`聲線/候選_0928/男1.wav…`、`女1.wav…`，照編號排、跳過不用的；
    逐字稿同檔名 `.txt` 要在。某個性別一個都沒有時，用 `default_refs()` 的暫定聲線當後備。"""
    import re

    pool: dict[str, list[tuple[int, Path]]] = {"男": [], "女": []}
    base = Path(base) if base else voice_dir()   # 10-01：第 0 步數聲線時用這次設定資料夾的
    d = base / CANDIDATE_DIR
    if d.is_dir():
        for f in d.glob("*.wav"):
            m = re.fullmatch(r"(男|女)(\d+)", f.stem)
            if m and f.stem not in SKIP_VOICES and f.with_suffix(".txt").is_file():
                pool[m.group(1)].append((int(m.group(2)), f))
    out = {g: [f for _, f in sorted(v)] for g, v in pool.items()}
    for g, fb in {"男": base / "男聲_暫定.wav", "女": base / "女聲_暫定.wav"}.items():
        if not out[g] and fb.is_file() and fb.with_suffix(".txt").is_file():
            out[g] = [fb]
    return out


def voice_name(path: str | Path) -> str:
    """聲線檔 → 畫面上的名稱（男1、女3；後備的暫定聲線照檔名）。"""
    return Path(path).stem


def out_dir(workdir: Path) -> Path:
    return Path(workdir) / "生成" / "學員"


def log_path(workdir: Path) -> Path:
    return Path(workdir) / "生成" / "學員紀錄.json"


# ---------- 切段（純函式） ----------

def in_ranges(a: float, b: float, ranges: list[tuple[float, float]]) -> bool:
    """句子中點落在任何一個範圍裡。"""
    mid = (a + b) / 2
    return any(x <= mid <= y for x, y in ranges)


def gap_boundary(prev: dict, nxt: dict) -> dict:
    """沒有原片聲音時（測試、聲音檔不在）的句子交界：用逐字稿的句子間隔代替量到的安靜。
    回傳 {停頓: 有沒有 0.3 秒以上的停頓, 切點: 原片秒, 安靜: 越大越安靜（找不到停頓時挑最安靜的一點用）}。"""
    gap = nxt["start"] - prev["end"]
    return {"停頓": gap >= PAUSE_MIN_S, "切點": (prev["end"] + nxt["start"]) / 2, "安靜": gap}


class QuietMap:
    """一段原片的音量（20 毫秒一格的 dB）→ 句子交界有沒有真的停頓、切點放哪裡（純計算，不讀檔）。"""

    def __init__(self, db: np.ndarray, t0: float, frame_s: float = QUIET_FRAME_S, thr: float | None = None):
        self.db, self.t0, self.fs = np.asarray(db, dtype=float), float(t0), float(frame_s)
        if thr is None:
            thr = float(np.percentile(self.db, 90)) - PAUSE_DROP_DB if len(self.db) else 0.0
        self.thr = thr
        q = np.concatenate([[False], self.db <= thr, [False]])
        d = np.diff(q.astype(np.int8))
        self.runs = list(zip(np.where(d == 1)[0].tolist(), np.where(d == -1)[0].tolist()))   # [a, b) 格

    def _f(self, t: float) -> int:
        return int(round((t - self.t0) / self.fs))

    def boundary(self, prev: dict, nxt: dict) -> dict:
        lo_out, hi_out = self._f(prev["start"]), self._f(nxt["end"])   # 切點不能超出前一句開頭～後一句結尾
        lo = max(lo_out, self._f(min(prev["end"], nxt["start"]) - PAUSE_EDGE_TOL_S))
        hi = min(hi_out, self._f(max(prev["end"], nxt["start"]) + PAUSE_EDGE_TOL_S))
        lo, hi = max(lo, 0), min(hi, len(self.db))
        if hi - lo < 1:
            return gap_boundary(prev, nxt)
        need = int(round(PAUSE_MIN_S / self.fs))
        best = None
        for a, b in self.runs:
            a2, b2 = max(a, lo_out, 0), min(b, hi_out, len(self.db))
            if b2 <= lo or a2 >= hi or b2 - a2 < need:   # 要碰到句子交界附近、夠長
                continue
            if best is None or b2 - a2 > best[1] - best[0]:
                best = (a2, b2)
        if best:
            return {"停頓": True, "切點": self.t0 + (best[0] + best[1]) / 2 * self.fs,
                    "安靜": (best[1] - best[0]) * self.fs}
        k = max(1, int(round(0.1 / self.fs)))   # 找不到停頓：交界附近 0.1 秒平均音量最小的一點
        seg = self.db[lo:hi]
        sm = np.convolve(seg, np.ones(k) / k, mode="same") if len(seg) >= k else seg
        i = int(np.argmin(sm))
        return {"停頓": False, "切點": self.t0 + (lo + i + 0.5) * self.fs, "安靜": -float(sm[i])}


def plan_slots(sents: list[dict], cut_ranges: list[tuple[float, float]] = (),
               min_s: float = MIN_CHUNK_S, max_s: float = MAX_CHUNK_S,
               boundary: Callable[[dict, dict], dict] | None = None) -> list[dict]:
    """一個學員段落的句子 → 生成格（10-03 第八批 #61）：[{句子: [...], slot: [起, 訖], 切在講話中: [原片秒]}]。

    - 落在刪除範圍裡的句子拿掉，前後不接在一起（各自一串，串的頭尾照句子起訖）
    - 只在句子之間切（第 3 步校對稿一句一句對應）；boundary(前一句, 後一句) 說那個交界有沒有真的停頓、切點在哪
      （預設 `gap_boundary`；有原片聲音時用 `QuietMap.boundary`）
    - 累積到 min_s 秒以上、遇到停頓就切；再接下一句會超過 max_s：前面有停頓就切在最後一個停頓，
      都沒有就切在最安靜的交界，標「切在講話中」
    - 同一串裡相鄰兩格頭尾相接：前一格的結束＝後一格的開始＝切點（停頓正中間）
    - 最後一格太短（< min_s）而且跟前一格接起來不超過 max_s → 併進前一格
    """
    boundary = boundary or gap_boundary
    runs: list[list[dict]] = [[]]
    for s in sorted(sents, key=lambda x: x["start"]):
        if in_ranges(s["start"], s["end"], list(cut_ranges)):
            if runs[-1]:
                runs.append([])
            continue
        runs[-1].append(s)
    out: list[dict] = []
    for run in runs:
        if not run:
            continue
        bnds = [boundary(run[i], run[i + 1]) for i in range(len(run) - 1)]
        groups: list[tuple[int, int]] = []
        i0 = 0
        while i0 < len(run):
            t0, i = (run[i0]["start"] if i0 == 0 else bnds[i0 - 1]["切點"]), i0   # 格子從切點開始算長度
            while True:
                if i == len(run) - 1:
                    groups.append((i0, i))
                    i0 = len(run)
                    break
                if run[i]["end"] - t0 >= min_s and bnds[i]["停頓"]:
                    groups.append((i0, i))
                    i0 = i + 1
                    break
                if run[i + 1]["end"] - t0 > max_s:
                    cands = list(range(i0, i + 1))
                    paused = [j for j in cands if bnds[j]["停頓"]]
                    j = paused[-1] if paused else max(cands, key=lambda j: (bnds[j]["安靜"], j))
                    groups.append((i0, j))
                    i0 = j + 1
                    break
                i += 1
        if len(groups) >= 2:
            (pa, _), (la, lb) = groups[-2], groups[-1]
            if run[lb]["end"] - run[la]["start"] < min_s and run[lb]["end"] - run[pa]["start"] <= max_s:
                groups[-2:] = [(pa, lb)]
        for k, (a, b) in enumerate(groups):
            head = None if k == 0 else bnds[a - 1]
            tail = None if k == len(groups) - 1 else bnds[b]
            sa = run[a]["start"] if head is None else head["切點"]
            sb = run[b]["end"] if tail is None else tail["切點"]
            sb = max(sb, sa + 0.05)
            if tail is not None:
                tail["切點"] = sb   # 下一格從同一點開始（頭尾相接）
            mid = [round(x["切點"], 3) for x in (head, tail) if x is not None and not x["停頓"]]
            out.append({"句子": run[a:b + 1], "slot": [round(sa, 3), round(sb, 3)], MID_SPEECH: mid})
    return out


def plan_chunks(sents: list[dict], cut_ranges: list[tuple[float, float]] = (),
                min_s: float = MIN_CHUNK_S, max_s: float = MAX_CHUNK_S,
                boundary: Callable[[dict, dict], dict] | None = None) -> list[list[dict]]:
    """`plan_slots` 只取每一格的句子。"""
    return [c["句子"] for c in plan_slots(sents, cut_ranges, min_s, max_s, boundary)]


def _turn_db(audio: Path, a: float, b: float) -> tuple[np.ndarray, float] | None:
    """原片 [a, b] 的音量（20 毫秒一格的 dB）。同一支程式裡同一段只讀一次。"""
    try:
        st = audio.stat()
    except OSError:
        return None
    return _turn_db_cached(str(audio), st.st_mtime_ns, st.st_size, round(a, 2), round(b, 2))


@lru_cache(maxsize=256)
def _turn_db_cached(path: str, _mtime: int, _size: int, a: float, b: float) -> tuple[np.ndarray, float] | None:
    import soundfile as sf

    from bookclub.roomtone import frame_db

    try:
        with sf.SoundFile(path) as f:
            sr = f.samplerate
            a = max(0.0, a)
            f.seek(min(int(a * sr), f.frames))
            x = f.read(max(0, int((b - a) * sr)), dtype="float32")
    except Exception:  # noqa: BLE001 — 讀不到就退回用句子間隔
        return None
    if x.ndim > 1:
        x = x.mean(axis=1)
    db = frame_db(x, sr)
    return (db, a) if len(db) else None


def turn_quiet(workdir: Path, a: float, b: float) -> QuietMap | None:
    """學員段落 [a, b]（前後各多讀 1 秒）的安靜地圖；原片聲音不在就回傳 None（改用句子間隔）。"""
    got = _turn_db(wd.audio_path(Path(workdir)), a - 1.0, b + 1.0)
    return QuietMap(got[0], got[1]) if got else None


def clip_slot(a: float, b: float, cut_ranges: list[tuple[float, float]]) -> tuple[float, float]:
    """時間格的頭尾落在刪除範圍裡就往外推到刪除範圍的邊界（句子中點在外面、邊緣切到刪除範圍的情況）。"""
    for x, y in cut_ranges:
        if x < a < y:
            a = y
        if x < b < y:
            b = x
    return a, b


def in_window(sents: list[dict], lo: float | None, hi: float | None) -> list[dict]:
    """測試範圍過濾：句子中點落在 [lo, hi] 的留下（範圍邊界切到的句子看中點決定）。"""
    lo = -1.0 if lo is None else lo
    hi = 1e12 if hi is None else hi
    return [s for s in sents if lo <= (s["start"] + s["end"]) / 2 <= hi]


# ---------- 男聲／女聲 ----------

def median_f0(x: np.ndarray, sr: int) -> float | None:
    """說話部分的中位數基頻（Hz）；估不出來回傳 None。"""
    import librosa

    if len(x) < sr:
        return None
    f0, voiced, _ = librosa.pyin(x.astype(np.float32), fmin=60, fmax=400, sr=sr, frame_length=1024)
    v = f0[voiced & ~np.isnan(f0)]
    return float(np.median(v)) if len(v) >= 20 else None


def estimate_student_f0(workdir: Path, spans: list[tuple[float, float]], max_s: float = 60.0) -> float | None:
    """從 audio.flac 取這位學員的說話片段（最多 max_s 秒）估基頻。"""
    import soundfile as sf

    parts, total = [], 0.0
    with sf.SoundFile(str(wd.audio_path(workdir))) as f:
        sr = f.samplerate
        for a, b in spans:
            if total >= max_s:
                break
            b = min(b, a + max_s - total)
            f.seek(int(a * sr))
            parts.append(f.read(int((b - a) * sr), dtype="float32"))
            total += b - a
    if not parts:
        return None
    x = np.concatenate(parts)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return median_f0(x, sr)


def roster_gender(real: str | None) -> str | None:
    """名冊（用本名找）這個人有填性別就回傳「男」「女」。09-30：名冊拿掉代號欄後改用本名找（以前用代號找，永遠找不到）。"""
    from bookclub import csvfile
    from bookclub.config import data_dir

    if not real:
        return None
    path = data_dir() / "名冊.csv"
    if not path.is_file():
        return None
    for r in csvfile.read_rows(path):   # 10-03 第九批 #35：Excel 另存的 Big5 也讀得了
        names = [(r.get("中文名") or "").strip()] + [x.strip() for x in re.split(r"[、,，/]", r.get("其他寫法") or "")]
        if real in [n for n in names if n]:
            g = (r.get("性別") or "").strip()
            return "男" if g.startswith("男") else "女" if g.startswith("女") else None
    return None


# ---------- 準備句子清單 ----------

def cut_ranges(workdir: Path) -> list[tuple[float, float]]:
    from bookclub import review

    dec = review.load_decisions(Path(workdir))
    return [(c["start"], c["end"]) for c in dec["刪除段落"] if c.get("狀態") != "還原"]


def build_items(workdir: Path, start: float | None = None, end: float | None = None,
                only: list[str] | None = None, include_kept: bool = False,
                keep_empty: bool = False) -> tuple[list[dict], dict]:
    """學員段落 → 生成項目（每段 id＝`<段落id>_<序號>`）。回傳（項目、各學員說話片段）。
    第 3 步設成「保留原聲」的學員不重念（include_kept=True 才照樣重念，測試聽生成效果用）。
    keep_empty=True 時反過來，只回傳校對稿刪光、不生成的時間格（`empty_chunks` 用）。"""
    from bookclub import review
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    tdata = turns_mod.page_data(workdir)
    if tdata.get("尚未準備"):
        raise FileNotFoundError("還沒有段落分析（校對/段落.json），先跑 `bookclub run analyze`。")
    speakers = wd.read_json(wd.speakers_path(workdir)) or {}
    by_id = {s["id"]: s for s in speakers.get("sentences", [])}
    table = review.replace_table(workdir)
    cuts = cut_ranges(workdir)
    kept = set() if include_kept else {k for k, v in review.load_decisions(workdir)["學員聲音"].items() if v == "保留原聲"}
    lo, hi = start if start is not None else -1.0, end if end is not None else 1e12

    items, spans = [], {}
    for t in tdata["段落"]:
        who = t.get("說話者")
        if not who or who == "老師" or who in kept:
            continue
        if t["end"] <= lo or t["start"] >= hi:
            continue
        spans.setdefault(who, []).append((max(t["start"], lo), min(t["end"], hi)))   # 估男女聲只用範圍內的原音
        if only and t["id"] not in only:
            continue
        # 09-30：句子的時間、文字照段落實際的範圍（人改過段落起訖、切在句子裡面時，句子會比段落長）
        all_sents = turns_mod.turn_sentences(t, by_id)
        sents = in_window(all_sents, lo, hi)
        edited = bool(t.get("校對稿")) and t["校對稿"] != t.get("原文")
        if not all_sents:
            if keep_empty:
                continue
            # 手動標的學員段落裡沒有逐字稿句子（09-29）：照段落起訖，用校對稿整段一次生成，不能留學員原聲
            a, b = clip_slot(max(t["start"], lo), min(t["end"], hi), cuts)
            if b - a > 0.3 and not in_ranges(a, b, list(cuts)) and (t.get("校對稿") or "").strip():
                text, changes = review.replace_real_names(t["校對稿"].strip(), table)
                items.append({
                    "id": f"{t['id']}_01", "段落": t["id"], "學員": who, "text": text, "原文": t.get("原文", ""),
                    "slot": [a, b], "slot_s": b - a, "句子": [], "換成代號": len(changes),
                    "文字來源": "手動標記段落（沒有逐字稿句子，照校對稿整段生成）",
                })
            continue
        pieces = split_edited(t, all_sents) if edited else None
        qm = turn_quiet(workdir, t["start"], t["end"]) if len(sents) > 1 else None
        for k, chunk in enumerate(plan_slots(sents, cuts, boundary=qm.boundary if qm else None), start=1):
            group = chunk["句子"]
            raw = "".join(s["text"] for s in group)
            src = "".join(pieces[s["id"]] for s in group) if pieces else raw
            text, changes = review.replace_real_names(src, table)
            a, b = clip_slot(*chunk["slot"], cuts)
            mid = {MID_SPEECH: chunk[MID_SPEECH]} if chunk[MID_SPEECH] else {}
            if not text.strip():
                # 校對時這幾句的字全刪了：不生成，組裝時整格消音（見 empty_chunks）
                if keep_empty:
                    items.append({"id": f"{t['id']}_{k:02d}", "段落": t["id"], "學員": who, "text": "",
                                  "slot": [a, b], "slot_s": b - a, "文字來源": "校對稿（這幾句刪光了）"})
                continue
            if keep_empty:
                continue
            items.append({
                "id": f"{t['id']}_{k:02d}", "段落": t["id"], "學員": who,
                "text": text, "原文": raw, "slot": [a, b], "slot_s": b - a, "句子": [s["id"] for s in group],
                "換成代號": len(changes), "文字來源": "校對稿" if pieces else "建議稿", **mid,
            })
    if not keep_empty:
        items += overlap_items(workdir, items, spans, kept, cuts, table, lo, hi, only)
    return items, spans


def overlap_items(workdir: Path, turn_items: list[dict], spans: dict, kept: set, cuts: list, table: list,
                  lo: float, hi: float, only: list[str] | None) -> list[dict]:
    """重疊卡片要自己生成的學員那一句（10-01）：
    - 選「生成學員聲音」、又不在任何學員重念的時間格裡 → 用卡片上「學員說的」；時間格＝學員那一整句
      （10-01 第三批：以前只換重疊那一小段，整句擠進去、前後還留學員原聲；見 `review.student_gen_slot`）
    - 選「兩邊都重新生成（照原本的時間）」→ 用「學員說的」、學員那邊的起訖，帶 `疊放`（組裝時跟老師那一句混在一起）
    沒選學員是誰、文字是空的，就不生成（覆核時擋通過、開始前總檢查會列出來；組裝時照消音）。"""
    from bookclub import review

    slots = [tuple(it["slot"]) for it in turn_items]
    out = []
    for o in review.overlap_choices(workdir):
        if not review.overlap_student_gen(o, slots) or review.overlap_gen_problem(o, slots):
            continue
        stacked = review.is_stacked(o["做法"], o.get("排法"))
        who = o["學員"]
        if who in kept or (only and o["id"] not in only):
            continue
        a, b = o["學員起訖"] if stacked else o["學員生成起訖"]
        if b <= lo or a >= hi:
            continue
        a, b = clip_slot(max(a, lo), min(b, hi), cuts)
        if b - a < 0.1 or in_ranges(a, b, list(cuts)):
            continue
        text, changes = review.replace_real_names(o["學員文字"].strip(), table)
        spans.setdefault(who, []).append((a, b))
        out.append({"id": f"{o['id']}_學員", "段落": o["id"], "學員": who, "text": text, "原文": "",
                    "slot": [round(a, 3), round(b, 3)], "slot_s": b - a, "句子": [], "換成代號": len(changes),
                    "文字來源": "重疊卡片的「學員說的」", "重疊": o["id"], **({"疊放": True} if stacked else {})})
    return out


def overlap_student_range(o: dict) -> list[float]:
    """重疊卡片要換掉的學員那一句（原片時間）：兩邊都重新生成照學員那邊的起訖，其他照學員生成起訖（純函式）。"""
    from bookclub import review

    return list(o["學員起訖"] if review.is_stacked(o.get("做法"), o.get("排法")) else o["學員生成起訖"])


OVERLAP_MISSING_REASON = "重疊：學員那句沒資料，整句消音"


def missing_overlap_mutes(workdir: Path, lo: float = 0.0, hi: float = 1e12, kept: set | None = None) -> list[dict]:
    """10-08 宇軒（流程簡化）：重疊選了要生成學員聲音、但缺「學員是誰」或「學員說的」（`overlap_gen_problem`），
    開始執行不再擋——組裝時把學員那一整句（`overlap_student_range`）墊底噪，不留學員原聲（跟局部消音同一套做法）。
    保留原聲的學員不消。回傳局部消音的格式（帶 `重疊`、`重疊缺資料`），處理紀錄寫「重疊：學員那句沒資料，整句消音」。"""
    from bookclub import review

    workdir = Path(workdir)
    kept = kept or set()
    try:
        items, _ = build_items(workdir)
    except FileNotFoundError:
        items = []
    slots = [tuple(it["slot"]) for it in items if not it.get("重疊")]
    out = []
    for o in review.overlap_choices(workdir):
        if not review.overlap_student_gen(o, slots):
            continue
        why = review.overlap_gen_problem(o, slots)
        if not why or (o.get("學員") and o["學員"] in kept):
            continue
        a, b = overlap_student_range(o)
        a, b = max(a, lo), min(b, hi)
        if b - a < 0.05:
            continue
        out.append({"id": f"重疊{o['id']}_學員整句", "start": round(a, 3), "end": round(b, 3), "方式": "墊底噪",
                    "重疊": o["id"], "做法": o.get("做法"), "重疊缺資料": why})
    return out


def split_edited(turn: dict, sents: list[dict]) -> dict[str, str] | None:
    """人在第 3 步改過的校對稿 → 分回每一句（09-29：重念要照校對稿念，不是照原始轉文字）。

    用 difflib 把原文和校對稿對齊：沒改的字照原位置，改過的字照比例分給那幾句。
    句子文字在原文裡找不到（段落被合併、切開過，原文跟句子對不上）就回傳 None，由呼叫端退回整段照原文。"""
    import difflib

    raw, edited = turn.get("原文") or "", (turn.get("校對稿") or "").strip()
    offs, pos = [], 0
    for s in sents:
        i = raw.find(s["text"], pos)
        if i < 0:
            return None
        offs.append((s["id"], i, i + len(s["text"])))
        pos = i + len(s["text"])
    ops = difflib.SequenceMatcher(None, raw, edited, autojunk=False).get_opcodes()

    def to_edited(i: int) -> int:
        for tag, i1, i2, j1, j2 in ops:
            if i1 <= i < i2:
                return j1 + (i - i1 if tag == "equal" else round((i - i1) * (j2 - j1) / (i2 - i1)))
            if i1 == i2 == i:          # 插入的字：放到後面那一句
                return j1
        return len(edited)

    out = {}
    for k, (sid, a, b) in enumerate(offs):
        ea = 0 if k == 0 else to_edited(a)
        eb = len(edited) if k == len(offs) - 1 else to_edited(offs[k + 1][1])
        out[sid] = edited[ea:eb]
    return out


def empty_chunks(workdir: Path, start: float | None = None, end: float | None = None) -> list[dict]:
    """校對稿刪光、不生成的學員時間格（組裝時當局部消音，不能留學員原聲）。"""
    items, _ = build_items(workdir, start, end, keep_empty=True)
    return [{"id": it["id"], "start": it["slot"][0], "end": it["slot"][1], "原因": f"{it['id']} 校對稿刪光了", "學員": it["學員"]}
            for it in items]


def voices_path(workdir: Path) -> Path:
    return Path(workdir) / "生成" / "學員聲線.json"


def voice_key(person: str, info: dict | None) -> str:
    """聲線記在誰名下：選了本名的用本名（合併／拆開學員後不會沿用錯的，09-30）；還沒本名的用「學員N」。"""
    real = (info or {}).get("本名")
    return real if real else person


def load_voice_table(workdir: Path) -> dict:
    """`生成/學員聲線.json` 的 `學員` 表：{鍵（本名或學員N）: {檔案, 名稱, 性別, 依據, 人選的}}。
    09-29 以前的舊格式（{學員N: {聲線: 男／女}}）只拿來當性別參考。"""
    data = wd.read_json(voices_path(workdir), default=None) or {}
    if data.get("版本") == 2:
        return data
    legacy = {k: v for k, v in data.items() if isinstance(v, dict) and v.get("聲線") in ("男", "女")}
    return {"版本": 2, "學員": {}, "舊的性別判斷": legacy}


def _student_gender(workdir: Path, person: str, info: dict, spans: dict, table: dict,
                    estimate: bool) -> tuple[str | None, str]:
    g = roster_gender(info.get("本名"))
    if g:
        return g, "名冊性別"
    old = (table.get("舊的性別判斷") or {}).get(person)
    if old and "基頻" in old.get("依據", ""):
        return old["聲線"], old["依據"]
    pitch = table.setdefault("音高", {})
    if person not in pitch:
        if not estimate:
            return None, "還沒判斷"
        f0 = estimate_student_f0(Path(workdir), spans.get(person, []))
        pitch[person] = {"hz": round(f0) if f0 else None}
    return _gender_from_hz(pitch[person].get("hz"))


def _gender_from_hz(hz: float | None) -> tuple[str, str]:
    if not hz:
        return "女", "基頻估不出來，先用女聲"
    return ("男" if hz < MALE_F0_HZ else "女"), f"原音中位數基頻 {hz:.0f} Hz（< {MALE_F0_HZ:.0f} 算男聲）"


GUESS_MARGIN_HZ = 15.0   # 10-07：代號依性別配用。離 165 Hz 這麼近（150–180 Hz）的算不確定，不指定性別


def guess_gender(hz: float | None) -> tuple[str | None, str]:
    """10-07（第 3 步代號）：原音中位數基頻 → (推測性別, 信心)。給「自動配代號」挑女生／男生名單用。
    估不出來、或離 165 Hz 不到 15 Hz 的回 (None, ...)：不指定性別，兩邊名單都可以配。
    信心：離 165 Hz 30 Hz 以上「高」，其他「中」。跟聲線的 `_gender_from_hz` 分開（那邊估不出來先用女聲）。"""
    if not hz:
        return None, "估不出來"
    if abs(hz - MALE_F0_HZ) < GUESS_MARGIN_HZ:
        return None, "不確定"
    g = "男" if hz < MALE_F0_HZ else "女"
    return g, ("高" if abs(hz - MALE_F0_HZ) >= 2 * GUESS_MARGIN_HZ else "中")


def pitch_gender(entry: dict | None) -> tuple[str | None, str]:
    """`學員聲線.json` 的 `音高` 一筆 → (推測性別, 信心)；10-07 以前存的沒有這兩欄，照 hz 現算。"""
    if not entry:
        return None, "沒有估"
    if "推測性別" in entry:
        return entry.get("推測性別"), entry.get("信心") or ""
    return guess_gender(entry.get("hz"))


def estimate_pitches(workdir: str | Path, log: Callable[[str], None] = print) -> dict:
    """第 1 步段落分析完就先估每位學員的音高（09-30 宇軒：聲線看音質像不像，不看實際男女），
    記在 `生成/學員聲線.json` 的 `音高`，第 3 步「學員是誰」打開就看得到配了哪個聲線。
    每位取最多 60 秒原音，一位約幾秒；整份重估（學員編號重排過也不會沿用舊的）。回傳 {學員N: hz 或 None}。"""
    workdir = Path(workdir)
    from bookclub import turns as turns_mod

    tdata = wd.read_json(turns_mod.turns_path(workdir), default={}) or {}
    spans: dict[str, list[tuple[float, float]]] = {}
    for t in tdata.get("段落", []):
        if t.get("說話者") in (tdata.get("學員") or {}):
            spans.setdefault(t["說話者"], []).append((t["start"], t["end"]))
    table = load_voice_table(workdir)
    table["音高"] = {}
    for who, sp in spans.items():
        f0 = estimate_student_f0(workdir, sp)
        g, conf = guess_gender(f0)
        table["音高"][who] = {"hz": round(f0) if f0 else None, "推測性別": g, "信心": conf,   # 10-07：代號依性別配
                              "秒數": round(sum(b - a for a, b in sp), 1)}   # 合併／拆開後秒數變了就不沿用（epcodes）
    wd.write_json(voices_path(workdir), table)
    log(f"[學員聲音] 估好 {len(spans)} 位學員的音高")
    return {k: v["hz"] for k, v in table["音高"].items()}


def assign_voices(workdir: Path, students: list[str], spans: dict, people: dict | None = None,
                  estimate: bool = True, log: Callable[[str], None] = print) -> dict:
    """每位學員一個聲線檔（09-30 宇軒：依男女自動輪流，同一集每位學員不同）。記在 `生成/學員聲線.json`。

    - students：這一集要重念的學員（學員N），照第一次出現的時間排；男生依序用男 1、男 2⋯，女生用女 1、女 2⋯（女 5 不用）
    - 已經配過（或人在第 3 步改過）的沿用；新的拿同性別還沒人用的下一個，用完了才重複（記在依據）
    - 性別：名冊（用本名找）→ 舊判斷 → 原音估基頻；estimate=False 時不估（網頁載入不跑重的計算）
    回傳 {學員N: {檔案, 名稱, 性別, 依據, 鍵, 人選的}}。"""
    workdir = Path(workdir)
    people = people if people is not None else _people(workdir)
    table = load_voice_table(workdir)
    known: dict = table.setdefault("學員", {})
    pool = voice_pool()
    out: dict = {}
    # 這一集的學員（全部，不只這次範圍內的）已經用掉的聲線；舊鍵（改過本名、合併掉的）不佔位子，人選的照樣佔
    here = {voice_key(n, i) for n, i in people.items()} | set(students)
    # 10-01 走查：比聲線名稱（男1、女4），不比完整路徑——設定資料夾換了位置（別台電腦、匯入設定包），
    # 以前全部當成沒人用、重新從第一個配起，會跟別人撞同一個聲線
    used = {g: {voice_name(v["檔案"]) for k, v in known.items() if v.get("性別") == g and v.get("檔案")
                and (k in here or v.get("人選的"))} for g in ("男", "女")}
    by_name = {voice_name(f): f for fs in pool.values() for f in fs}
    changed = False
    for who in students:
        info = people.get(who, {})
        key = voice_key(who, info)
        rec = known.get(key)
        if rec and rec.get("檔案") and not Path(rec["檔案"]).is_file() and voice_name(rec["檔案"]) in by_name:
            rec = known[key] = {**rec, "檔案": str(by_name[voice_name(rec["檔案"])])}   # 同一個聲線、換了位置
            changed = True
        if rec and rec.get("檔案") and Path(rec["檔案"]).is_file():
            out[who] = {**rec, "鍵": key}
            continue
        g, why = _student_gender(workdir, who, info, spans, table, estimate)
        if g is None:
            out[who] = {"檔案": None, "名稱": None, "性別": None, "依據": why, "鍵": key, "人選的": False}
            continue
        free = [f for f in pool.get(g, []) if voice_name(f) not in used[g]]
        if free:
            pick = free[0]
        elif pool.get(g):
            pick = pool[g][len(used[g]) % len(pool[g])]
            why += f"；{g}聲候選不夠，跟別人重複"
        else:
            raise FileNotFoundError(f"找不到{g}聲的匿名聲線：{voice_dir() / CANDIDATE_DIR}／{default_refs()[g]}")
        used[g].add(voice_name(pick))
        known[key] = {"檔案": str(pick), "名稱": voice_name(pick), "性別": g, "依據": why, "人選的": False}
        changed = True
        out[who] = {**known[key], "鍵": key}
        log(f"[學員聲音] {key}：{voice_name(pick)}（{g}聲，{why}）")
    if changed:
        wd.write_json(voices_path(workdir), table)
    return out


def set_voice_choice(workdir: str | Path, person: str, name: str | None) -> dict:
    """`POST /api/students/voice`：第 3 步「學員是誰」改某位學員的聲線（name＝男1、女3⋯；空白＝回到自動配）。"""
    workdir = Path(workdir)
    people = _people(workdir)
    if person not in people:
        raise KeyError(f"沒有這位學員：{person}")
    key = voice_key(person, people[person])
    table = load_voice_table(workdir)
    known = table.setdefault("學員", {})
    old = known.get(key) or {}
    if not name:
        # 10-01 走查：回到自動配＝回到人選之前自動配的那一個（以前重新配，可能換成別的聲線、整段要重新生成）
        prev = old.get("自動配原本")
        if prev and prev.get("檔案"):
            known[key] = prev
        else:
            known.pop(key, None)
        wd.write_json(voices_path(workdir), table)
        return {"ok": True, "學員": person, "鍵": key, "聲線": (prev or {}).get("名稱")}
    pick = next((f for g, fs in voice_pool().items() for f in fs if voice_name(f) == name), None)
    if pick is None:
        raise ValueError(f"沒有這個聲線：{name}")
    g = "男" if name.startswith("男") else "女" if name.startswith("女") else (known.get(key) or {}).get("性別")
    auto = old.get("自動配原本") if old.get("人選的") else (old if old.get("檔案") else None)
    known[key] = {"檔案": str(pick), "名稱": name, "性別": g, "依據": "人在第 3 步選的", "人選的": True,
                  **({"自動配原本": auto} if auto else {})}
    wd.write_json(voices_path(workdir), table)
    return {"ok": True, "學員": person, "鍵": key, "聲線": name}


def voice_page(workdir: Path, people: dict) -> dict:
    """第 3 步「學員是誰」每位學員旁邊顯示的聲線（不估基頻，網頁載入要快；還沒配的寫「開始生成時自動配」）。"""
    order = sorted(people, key=lambda n: people[n].get("第一次", 0.0))
    got = assign_voices(workdir, order, {}, people=people, estimate=False, log=lambda s: None)
    return {"每位": got, "選項": {g: [voice_name(f) for f in fs] for g, fs in voice_pool().items()}}


def current_refs(workdir: Path, items: list[dict]) -> dict[str, str | None]:
    """第 4 步「做過沒有」用（10-01）：每位學員現在配到的聲線檔（不估基頻、不載入模型）。
    跟生成程式比同一個檔案，才不會一邊說做過了、一邊說要重做；還沒配到的給 None（照紀錄判斷）。"""
    people = _people(workdir)
    order = sorted({it["學員"] for it in items}, key=lambda n: people.get(n, {}).get("第一次", 0.0))
    got = assign_voices(workdir, order, {}, people=people, estimate=False, log=lambda s: None)
    return {who: (v or {}).get("檔案") for who, v in got.items()}


def _people(workdir: Path) -> dict:
    """段落分析的學員表（學員N → 本名等），加上第一次出現的時間（輪流配聲線照這個順序）。"""
    from bookclub import turns as turns_mod

    tdata = wd.read_json(turns_mod.turns_path(Path(workdir)), default={}) or {}
    people = {k: dict(v) for k, v in (tdata.get("學員") or {}).items()}
    for t in tdata.get("段落", []):
        who = t.get("說話者")
        if who in people:
            people[who]["第一次"] = min(people[who].get("第一次", t["start"]), t["start"])
    return people


# ---------- 入口 ----------

def _prepare(workdir: Path, start: float | None, end: float | None, only: list[str] | None, refs: dict[str, Path],
             include_kept: bool, pron_table: str | Path | None, log: Callable[[str], None]) -> list[dict]:
    """要生成的學員段落，每一段配好聲線（參考音檔）與生成用文字。生成與第 4 步排程式（voice_groups）共用。"""
    from bookclub import tts

    items, spans = build_items(workdir, start, end, only, include_kept=include_kept)
    if not items:
        return []
    people = _people(workdir)
    first = {}
    for it in items:
        first.setdefault(it["學員"], it["slot"][0])
    order = sorted(first, key=lambda n: people.get(n, {}).get("第一次", first[n]))
    voices = assign_voices(workdir, order, spans, people=people, log=log)
    table = tts.load_pron_table(pron_table)
    for it in items:
        v = voices[it["學員"]]
        it["聲線"] = v["性別"]
        ref = Path(refs[v["性別"]]).expanduser() if refs.get(v["性別"]) else Path(v["檔案"])
        it["參考音檔"] = str(ref)
        it["聲線名稱"] = voice_name(ref)
        it["生成用文字"], it["發音對照"] = tts.apply_pron(it["text"], table)
        it["_聲線"] = v
    return items


def voice_groups(workdir: str | Path, start: float | None = None, end: float | None = None,
                 log: Callable[[str], None] = print) -> list[dict]:
    """第 4 步分開程式跑（10-01）：範圍內的學員段落依聲線（參考音檔）分組，每組還有幾段要做。不載入模型。
    回傳 [{參考音, 名稱, 學員[], 段數, 要做}]，照參考音檔排（跟 generate_students 的順序一樣）。"""
    from bookclub import tts

    workdir = Path(workdir).expanduser()
    items = _prepare(workdir, start, end, None, {}, False, None, log)
    record = wd.read_json(log_path(workdir), default=None) or {}
    done = {r["id"]: r for r in record.get("句子", [])}
    out = []
    for ref in sorted({it["參考音檔"] for it in items}):
        group = [it for it in items if it["參考音檔"] == ref]
        todo = [it for it in group if tts.needs_work(done.get(it["id"]), it, Path(ref), workdir)]   # #18 停頓沒做成、#19 檔案不在
        out.append({"參考音": ref, "名稱": voice_name(ref), "學員": sorted({it["學員"] for it in group}),
                    "段數": len(group), "要做": len(todo)})
    return out


def generate_students(
    workdir: str | Path, *, start: float | None = None, end: float | None = None, only: list[str] | None = None,
    refs: dict[str, Path] | None = None, check_content: bool = True, use_pauses: bool = True,
    pron_table: str | Path | None = None, synth_factory=None, hear=None, align=None, redo: bool = False,
    include_kept: bool = False, only_ref: str | Path | None = None, phase: str | None = None,
    fresh_pauses: bool = False,
    log: Callable[[str], None] = print,
) -> dict:
    """`bookclub gen students`。synth_factory(ref_wav, ref_text) 可以從外面傳（測試用假的）。

    10-01 第 4 步分開程式跑：only_ref＝只做這一個聲線（參考音檔）；phase＝只做「生成」「停頓」「收尾」其中一段
    （見 `tts.run_generation`）。停頓那一支一次做全部聲線，從外面傳同一個 align（`tts.lazy_aligner`），對位模型只載入一次。"""
    from bookclub import tts
    from bookclub.config import load_settings

    workdir = wd.ensure(workdir)
    refs = refs or {}   # 指令列 --male／--female：那個性別全部用這一個（測試用）；沒給就每位學員各自的聲線
    items = _prepare(workdir, start, end, only, refs, include_kept, pron_table, log)
    if not items:
        log("[學員聲音] 範圍內沒有要生成的學員段落。")
        return {}
    voices = {it["學員"]: it.pop("_聲線") for it in items}
    tolerance = load_settings().thresholds.length_tolerance

    lp = log_path(workdir)
    record = wd.read_json(lp, default=None) or {}
    done = {r["id"]: r for r in record.get("句子", [])}
    if redo:   # 重做放回時間格等後段；每次生成的結果在 _嘗試快取.json，不會重生成
        done = {k: v for k, v in done.items() if k not in {it["id"] for it in items}}
    od = out_dir(workdir)
    od.mkdir(parents=True, exist_ok=True)
    load_total = float(record.get("統計", {}).get("載入模型秒") or 0.0)

    def save() -> dict:
        sents = sorted(done.values(), key=lambda r: r["slot"][0])
        attempts = [a for r in sents for a in r["嘗試"]]
        audio = sum(a["長度秒"] for a in attempts)
        spent = sum(a["耗時秒"] for a in attempts)
        data = {"參考音": {n: v["檔案"] for n, v in voices.items()}, "學員聲線": voices, "句子": sents,
                "統計": {"段數": len(sents), "要人聽": sum(1 for r in sents if r["要人聽"]),
                       "生成次數": len(attempts), "生成總秒數": round(audio, 1), "生成總耗時秒": round(spent, 1),
                       "平均倍數": round(spent / audio, 1) if audio else None, "載入模型秒": round(load_total, 1)}}
        wd.write_json(lp, data)
        return data

    for ref_path in sorted({it["參考音檔"] for it in items}):   # 09-30：每位學員各自的聲線，同一個聲線一起生成（只載入一次）
        if only_ref and Path(ref_path) != Path(only_ref).expanduser():
            continue
        group = [it for it in items if it["參考音檔"] == ref_path]
        ref_wav = Path(ref_path)
        ref_txt = ref_wav.with_suffix(".txt")
        for p in (ref_wav, ref_txt):
            if not p.is_file():
                raise FileNotFoundError(f"找不到學員聲線參考音：{p}")
        ref_text = ref_txt.read_text(encoding="utf-8").strip()
        todo = [it for it in group if tts.needs_work(done.get(it["id"]), it, ref_wav, workdir)]   # #18 停頓沒做成、#19 檔案不在
        log(f"[學員聲音] {voice_name(ref_wav)}（{'、'.join(sorted({it['學員'] for it in group}))}）："
            f"{len(group)} 段（{sum(it['slot_s'] for it in group):.0f} 秒），要生成 {len(todo)} 段")
        if not todo:
            continue
        extra = {it["id"]: {k: it[k] for k in ("段落", "學員", "聲線", "聲線名稱", "句子", "換成代號", "文字來源", "重疊", "疊放",
                                                     MID_SPEECH)
                            if k in it} for it in todo}

        def save_group() -> None:
            for sid, ex in extra.items():
                if sid in done:
                    done[sid].update(ex, 參考音=str(ref_wav), 參考音指紋=tts.ref_fingerprint(ref_wav), 角色="學員")
            save()

        synth = synth_factory(ref_wav, ref_text) if synth_factory else None
        load_total += tts.run_generation(
            workdir, todo, od, ref_wav, ref_text, tolerance, save=save_group, done=done, role="學員",
            tag="學員聲音", check_content=check_content, check_similarity=False, use_pauses=use_pauses,
            synth=synth, hear=hear, align=align, phase=phase, fresh_pauses=fresh_pauses, log=log)
    if phase in ("生成", "停頓"):   # 10-01：紀錄等收尾那一支才寫（這兩段沒有新的結果，也不動別人的紀錄）
        log(f"[學員聲音] 這一支程式（{phase}）做完")
        return record
    data = save()
    st = data["統計"]
    log(f"[學員聲音] 完成：{st['段數']} 段，{st['要人聽']} 段要人聽，平均 1 秒聲音花 {st['平均倍數']} 秒生成。紀錄：{lp}")
    return data
