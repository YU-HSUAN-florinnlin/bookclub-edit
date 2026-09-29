"""流程第 4 步（學員）：學員段落用匿名聲線（男聲／女聲）整段重念（09-28 測試版）。

做法沿用老師 AI 聲音（`bookclub/tts.py`：種子 42、Groq 轉回文字檢查、不過才換種子、照原片停頓
插入空白、放回時間格），差別：

- 參考音是匿名聲線（`~/讀書會剪輯資料/聲線/男聲_暫定.wav`／`女聲_暫定.wav`，Common Voice CC0），
  依學員分男女：名冊有性別就照名冊，沒有就用原音估基頻（中位數低於 165 Hz 算男聲）
- 切段：學員段落照逐字稿的句子切，相鄰句子接成 8～20 秒一段，只在標點（或句子之間停頓 1.5 秒以上）
  斷開；每段的時間格＝那幾句的起訖
- 文字：第 3 步覆核的校對稿；還沒確認的段落用「建議稿」（名冊本名換成代號的初稿，轉文字的錯字會照念）
- 落在確認刪除段落裡的句子不生成
- 放回時間格用學員規則（`fit.py`）：比較短補靜音、長 15% 以內微調語速、再長標紅（組裝時用畫面停格補長）

輸出：`生成/學員/`（檔名規則同 `生成/老師/`）、`生成/學員紀錄.json`。不碰 `生成/老師/`
（參考音不同，混在一起會讓老師那邊判定參考音變了而全部重生成）。可以中斷續跑。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

from bookclub import workdir as wd

END_PUNCT = "。！？!?…"
CONT_PUNCT = "，,、；;：:"
MIN_CHUNK_S, MAX_CHUNK_S = 8.0, 20.0
GAP_BREAK_S = 1.5          # 句子之間停頓這麼久，沒有標點也可以斷
MALE_F0_HZ = 165.0


def voice_dir() -> Path:
    from bookclub.config import data_dir

    return data_dir() / "聲線"


def default_refs() -> dict[str, Path]:
    return {"男": voice_dir() / "男聲_暫定.wav", "女": voice_dir() / "女聲_暫定.wav"}


def out_dir(workdir: Path) -> Path:
    return Path(workdir) / "生成" / "學員"


def log_path(workdir: Path) -> Path:
    return Path(workdir) / "生成" / "學員紀錄.json"


# ---------- 切段（純函式） ----------

def _breakable(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and t[-1] in END_PUNCT + CONT_PUNCT


def in_ranges(a: float, b: float, ranges: list[tuple[float, float]]) -> bool:
    """句子中點落在任何一個範圍裡。"""
    mid = (a + b) / 2
    return any(x <= mid <= y for x, y in ranges)


def plan_chunks(sents: list[dict], cut_ranges: list[tuple[float, float]] = (),
                min_s: float = MIN_CHUNK_S, max_s: float = MAX_CHUNK_S) -> list[list[dict]]:
    """一個學員段落的句子 → 生成段落（每段是幾句的清單）。

    - 落在刪除範圍裡的句子拿掉，前後不接在一起
    - 累積到 min_s 秒以上、這句結尾是標點（或跟下一句中間停頓 ≥ GAP_BREAK_S）就斷
    - 再接下一句會超過 max_s 時：這句結尾可以斷就斷；都不能斷的話超過 max_s 也硬斷（避免一段太長）
    - 最後一段太短（< min_s）而且跟前一段接起來不超過 max_s → 併進前一段
    """
    runs: list[list[dict]] = [[]]
    for s in sorted(sents, key=lambda x: x["start"]):
        if in_ranges(s["start"], s["end"], list(cut_ranges)):
            if runs[-1]:
                runs.append([])
            continue
        runs[-1].append(s)
    chunks: list[list[dict]] = []
    for run in runs:
        if not run:
            continue
        cur: list[dict] = []
        start_idx = len(chunks)
        for i, s in enumerate(run):
            cur.append(s)
            dur = cur[-1]["end"] - cur[0]["start"]
            nxt = run[i + 1] if i + 1 < len(run) else None
            if nxt is None:
                break
            gap = nxt["start"] - s["end"]
            can = _breakable(s["text"]) or gap >= GAP_BREAK_S
            too_long = nxt["end"] - cur[0]["start"] > max_s
            if (dur >= min_s and can) or (too_long and (can or dur >= max_s)):
                chunks.append(cur)
                cur = []
        if cur:
            if len(chunks) > start_idx and cur[-1]["end"] - cur[0]["start"] < min_s \
                    and cur[-1]["end"] - chunks[-1][0]["start"] <= max_s:
                chunks[-1].extend(cur)
            else:
                chunks.append(cur)
    return chunks


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


def roster_gender(student: str, code: str | None) -> str | None:
    """名冊裡這個代號有填性別就回傳「男」「女」。"""
    import csv

    from bookclub.config import data_dir

    if not code:
        return None
    path = data_dir() / "名冊.csv"
    if not path.is_file():
        return None
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if (r.get("英文代號") or "").strip() == code:
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
        all_sents = [by_id[i] for i in t.get("句子", []) if i in by_id]
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
        for k, group in enumerate(plan_chunks(sents, cuts), start=1):
            raw = "".join(s["text"] for s in group)
            src = "".join(pieces[s["id"]] for s in group) if pieces else raw
            text, changes = review.replace_real_names(src, table)
            a, b = clip_slot(group[0]["start"], group[-1]["end"], cuts)
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
                "換成代號": len(changes), "文字來源": "校對稿" if pieces else "建議稿",
            })
    return items, spans


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


def assign_voices(workdir: Path, students: list[str], spans: dict, codes: dict | None = None,
                  log: Callable[[str], None] = print) -> dict:
    """每位學員用男聲或女聲，記在 `生成/學員聲線.json`（算過就沿用）。"""
    path = Path(workdir) / "生成" / "學員聲線.json"
    known = wd.read_json(path, default=None) or {}
    for who in students:
        if who in known:
            continue
        g = roster_gender(who, (codes or {}).get(who))
        if g:
            known[who] = {"聲線": g, "依據": "名冊性別"}
            continue
        f0 = estimate_student_f0(Path(workdir), spans.get(who, []))
        if f0 is None:
            known[who] = {"聲線": "女", "依據": "基頻估不出來，先用女聲"}
        else:
            known[who] = {"聲線": "男" if f0 < MALE_F0_HZ else "女", "依據": f"原音中位數基頻 {f0:.0f} Hz（< {MALE_F0_HZ:.0f} 算男聲）",
                          "基頻Hz": round(f0, 1)}
        log(f"[學員聲音] {who}：{known[who]['聲線']}聲（{known[who]['依據']}）")
    wd.write_json(path, known)
    return known


# ---------- 入口 ----------

def generate_students(
    workdir: str | Path, *, start: float | None = None, end: float | None = None, only: list[str] | None = None,
    refs: dict[str, Path] | None = None, check_content: bool = True, use_pauses: bool = True,
    pron_table: str | Path | None = None, synth_factory=None, hear=None, align=None, redo: bool = False,
    include_kept: bool = False,
    log: Callable[[str], None] = print,
) -> dict:
    """`bookclub gen students`。synth_factory(ref_wav, ref_text) 可以從外面傳（測試用假的）。"""
    from bookclub import tts
    from bookclub.config import load_settings

    workdir = wd.ensure(workdir)
    refs = refs or default_refs()
    items, spans = build_items(workdir, start, end, only, include_kept=include_kept)
    if not items:
        log("[學員聲音] 範圍內沒有要生成的學員段落。")
        return {}
    voices = assign_voices(workdir, sorted({it["學員"] for it in items}), spans, log=log)
    table = tts.load_pron_table(pron_table)
    for it in items:
        it["聲線"] = voices[it["學員"]]["聲線"]
        it["生成用文字"], it["發音對照"] = tts.apply_pron(it["text"], table)
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
        data = {"參考音": {k: str(v) for k, v in refs.items()}, "學員聲線": voices, "句子": sents,
                "統計": {"段數": len(sents), "要人聽": sum(1 for r in sents if r["要人聽"]),
                       "生成次數": len(attempts), "生成總秒數": round(audio, 1), "生成總耗時秒": round(spent, 1),
                       "平均倍數": round(spent / audio, 1) if audio else None, "載入模型秒": round(load_total, 1)}}
        wd.write_json(lp, data)
        return data

    for sex in ("男", "女"):
        group = [it for it in items if it["聲線"] == sex]
        if not group:
            continue
        ref_wav = Path(refs[sex]).expanduser()
        ref_txt = ref_wav.with_suffix(".txt")
        for p in (ref_wav, ref_txt):
            if not p.is_file():
                raise FileNotFoundError(f"找不到{sex}聲參考音：{p}")
        ref_text = ref_txt.read_text(encoding="utf-8").strip()
        todo = [it for it in group if not (
            it["id"] in done and tts.same_ref_file(done[it["id"]], ref_wav)
            and done[it["id"]]["text"] == it["text"] and done[it["id"]].get("生成用文字") == it["生成用文字"])]
        log(f"[學員聲音] {sex}聲：{len(group)} 段（{sum(it['slot_s'] for it in group):.0f} 秒），要生成 {len(todo)} 段")
        if not todo:
            continue
        extra = {it["id"]: {k: it[k] for k in ("段落", "學員", "聲線", "句子", "換成代號", "文字來源")} for it in todo}

        def save_group() -> None:
            for sid, ex in extra.items():
                if sid in done:
                    done[sid].update(ex, 參考音=str(ref_wav), 參考音指紋=tts.ref_fingerprint(ref_wav), 角色="學員")
            save()

        synth = synth_factory(ref_wav, ref_text) if synth_factory else None
        load_total += tts.run_generation(
            workdir, todo, od, ref_wav, ref_text, tolerance, save=save_group, done=done, role="學員",
            tag="學員聲音", check_content=check_content, check_similarity=False, use_pauses=use_pauses,
            synth=synth, hear=hear, align=align, log=log)
    data = save()
    st = data["統計"]
    log(f"[學員聲音] 完成：{st['段數']} 段，{st['要人聽']} 段要人聽，平均倍數 {st['平均倍數']}。紀錄：{lp}")
    return data
