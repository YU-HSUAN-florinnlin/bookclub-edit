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

import re
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
    """沒有候選聲線時的後備（09-28 暫定：男 1、女 1 各一份）。"""
    return {"男": voice_dir() / "男聲_暫定.wav", "女": voice_dir() / "女聲_暫定.wav"}


CANDIDATE_DIR = "候選_0928"
SKIP_VOICES = {"女5"}   # 09-29 宇軒聽過：女 5 不用


def voice_pool() -> dict[str, list[Path]]:
    """學員匿名聲線候選（09-30）：`聲線/候選_0928/男1.wav…`、`女1.wav…`，照編號排、跳過不用的；
    逐字稿同檔名 `.txt` 要在。某個性別一個都沒有時，用 `default_refs()` 的暫定聲線當後備。"""
    import re

    pool: dict[str, list[tuple[int, Path]]] = {"男": [], "女": []}
    d = voice_dir() / CANDIDATE_DIR
    if d.is_dir():
        for f in d.glob("*.wav"):
            m = re.fullmatch(r"(男|女)(\d+)", f.stem)
            if m and f.stem not in SKIP_VOICES and f.with_suffix(".txt").is_file():
                pool[m.group(1)].append((int(m.group(2)), f))
    out = {g: [f for _, f in sorted(v)] for g, v in pool.items()}
    for g, fb in default_refs().items():
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


def roster_gender(real: str | None) -> str | None:
    """名冊（用本名找）這個人有填性別就回傳「男」「女」。09-30：名冊拿掉代號欄後改用本名找（以前用代號找，永遠找不到）。"""
    import csv

    from bookclub.config import data_dir

    if not real:
        return None
    path = data_dir() / "名冊.csv"
    if not path.is_file():
        return None
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
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
        table["音高"][who] = {"hz": round(f0) if f0 else None}
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
    used = {g: {v["檔案"] for k, v in known.items() if v.get("性別") == g and v.get("檔案")
                and (k in here or v.get("人選的"))} for g in ("男", "女")}
    changed = False
    for who in students:
        info = people.get(who, {})
        key = voice_key(who, info)
        rec = known.get(key)
        if rec and rec.get("檔案") and Path(rec["檔案"]).is_file():
            out[who] = {**rec, "鍵": key}
            continue
        g, why = _student_gender(workdir, who, info, spans, table, estimate)
        if g is None:
            out[who] = {"檔案": None, "名稱": None, "性別": None, "依據": why, "鍵": key, "人選的": False}
            continue
        free = [f for f in pool.get(g, []) if str(f) not in used[g]]
        if free:
            pick = free[0]
        elif pool.get(g):
            pick = pool[g][len(used[g]) % len(pool[g])]
            why += f"；{g}聲候選不夠，跟別人重複"
        else:
            raise FileNotFoundError(f"找不到{g}聲的匿名聲線：{voice_dir() / CANDIDATE_DIR}／{default_refs()[g]}")
        used[g].add(str(pick))
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
    if not name:
        known.pop(key, None)
        wd.write_json(voices_path(workdir), table)
        return {"ok": True, "學員": person, "鍵": key, "聲線": None}
    pick = next((f for g, fs in voice_pool().items() for f in fs if voice_name(f) == name), None)
    if pick is None:
        raise ValueError(f"沒有這個聲線：{name}")
    g = "男" if name.startswith("男") else "女" if name.startswith("女") else (known.get(key) or {}).get("性別")
    known[key] = {"檔案": str(pick), "名稱": name, "性別": g, "依據": "人在第 3 步選的", "人選的": True}
    wd.write_json(voices_path(workdir), table)
    return {"ok": True, "學員": person, "鍵": key, "聲線": name}


def voice_page(workdir: Path, people: dict) -> dict:
    """第 3 步「學員是誰」每位學員旁邊顯示的聲線（不估基頻，網頁載入要快；還沒配的寫「開始生成時自動配」）。"""
    order = sorted(people, key=lambda n: people[n].get("第一次", 0.0))
    got = assign_voices(workdir, order, {}, people=people, estimate=False, log=lambda s: None)
    return {"每位": got, "選項": {g: [voice_name(f) for f in fs] for g, fs in voice_pool().items()}}


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
    refs = refs or {}   # 指令列 --male／--female：那個性別全部用這一個（測試用）；沒給就每位學員各自的聲線
    items, spans = build_items(workdir, start, end, only, include_kept=include_kept)
    if not items:
        log("[學員聲音] 範圍內沒有要生成的學員段落。")
        return {}
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
        group = [it for it in items if it["參考音檔"] == ref_path]
        ref_wav = Path(ref_path)
        ref_txt = ref_wav.with_suffix(".txt")
        for p in (ref_wav, ref_txt):
            if not p.is_file():
                raise FileNotFoundError(f"找不到學員聲線參考音：{p}")
        ref_text = ref_txt.read_text(encoding="utf-8").strip()
        todo = [it for it in group if tts.record_stale(done.get(it["id"]), it, ref_wav)]
        log(f"[學員聲音] {voice_name(ref_wav)}（{'、'.join(sorted({it['學員'] for it in group}))}）："
            f"{len(group)} 段（{sum(it['slot_s'] for it in group):.0f} 秒），要生成 {len(todo)} 段")
        if not todo:
            continue
        extra = {it["id"]: {k: it[k] for k in ("段落", "學員", "聲線", "聲線名稱", "句子", "換成代號", "文字來源")} for it in todo}

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
