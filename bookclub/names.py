"""流程第 1 步的第四個零件：找名字。

在老師的句子裡找名冊上的學員名字（精確比對＋讀音放寬）與敏感詞（精確比對），
每筆判斷句首／句中／句尾、建議做法，並算出「名字那一小段」的建議切點——方案 1
（宇軒 2026-09-23 定案，不做逐字對位）：以 Groq 的字層級時間為初值，再用音量
找最近的停頓修正切點，切點不落在相鄰的字裡面。

隱私：名冊、敏感詞、學員說的內容都是個資。這支模組可以讀、可以寫進
`workdir/名字候選.json`（供覆核網頁使用），但任何終端機 print 或回傳給呼叫端
的統計摘要都只放時間、數字、層級——不印名字、不印句子內容。
"""

from __future__ import annotations

import csv
import re
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from bookclub.workdir import name_candidates_dir, names_path, read_json, write_json

# ---------- 讀音放寬（照 spikes/nametest/name_test_ab.py 驗證過的做法） ----------

INIT_CANON = {"zh": "z", "ch": "c", "sh": "s", "n": "l", "f": "h"}
FINAL_CANON = {"in": "ing", "en": "eng", "an": "ang", "ian": "iang"}
LEVEL_ORDER = {"精確": 0, "A1": 1, "A2": 2}
# 信心分級（宇軒 2026-09-24 定案）：只由讀音放寬層級決定，跟切點信心是兩件事。
# 精確命中最可信；A1（同音不同調）次之；A2（放寬台灣口音常混的音）最容易誤抓
# （疊字、地名這類假警報幾乎都出在這層），標「低」但仍然列入候選讓人決定。
LEVEL_CONFIDENCE = {"精確": "高", "A1": "中", "A2": "低"}

# ---------- 切點修正 ----------

PAUSE_SEARCH_S = 0.3      # 初始切點往前往後各找最多這麼多秒
PAUSE_FRAME_S = 0.02      # 音框長度，跟 refpick.FRAME 一致
PAUSE_CONTEXT_S = 1.0     # 算「安靜」門檻用的上下文範圍（比搜尋範圍寬，門檻才有意義）
PAUSE_DB_DROP = 20.0      # 音框音量比上下文裡最大聲的地方低這麼多 dB，算安靜
DEFAULT_BUFFER_S = 0.05   # 切點確定是乾淨停頓時，建議的前後緩衝
CANDIDATE_CLIP_PAD_S = 2.0  # 候選小段音檔，名字前後各留幾秒


def _syllable_parts(ch: str):
    from pypinyin import Style, lazy_pinyin

    ini = lazy_pinyin(ch, style=Style.INITIALS, strict=False)[0]
    fin = lazy_pinyin(ch, style=Style.FINALS, strict=False)[0]
    full = lazy_pinyin(ch)[0]
    return ini, fin, full


def _match_level(window: str, name: str, name_parts) -> str | None:
    if window == name:
        return "精確"
    win_parts = [_syllable_parts(c) for c in window]
    if all(w[2] == n[2] for w, n in zip(win_parts, name_parts)):
        return "A1"
    if all(
        INIT_CANON.get(w[0], w[0]) == INIT_CANON.get(n[0], n[0])
        and FINAL_CANON.get(w[1], w[1]) == FINAL_CANON.get(n[1], n[1])
        for w, n in zip(win_parts, name_parts)
    ):
        return "A2"
    return None


# ---------- 名冊、敏感詞 ----------

def load_roster(path: Path) -> list[dict]:
    """讀 `名冊.csv`（欄位：中文名,其他寫法,英文代號,聲線,性別），展開成
    [{"寫法": 中文名或其他寫法, "canonical": 中文名, "代號": 英文代號}, ...]，
    一個人可能對應好幾筆（本名＋其他寫法各一筆）。"""
    path = Path(path)
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            canonical = (row.get("中文名") or "").strip()
            code = (row.get("英文代號") or "").strip()
            if not canonical:
                continue
            out.append({"寫法": canonical, "canonical": canonical, "代號": code})
            variants = (row.get("其他寫法") or "").strip()
            for v in re.split(r"[、,，/]", variants):
                v = v.strip()
                if v:
                    out.append({"寫法": v, "canonical": canonical, "代號": code})
    return out


def load_sensitive_words(path: Path) -> list[dict]:
    """讀 `敏感詞.csv`（欄位：原詞,替代詞）。"""
    path = Path(path)
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            orig = (row.get("原詞") or "").strip()
            repl = (row.get("替代詞") or "").strip()
            if orig:
                out.append({"寫法": orig, "canonical": orig, "代號": repl})
    return out


# ---------- 排除清單（宇軒逐筆聽過後回報的誤抓） ----------

def load_exclusion_list(path: Path) -> list[dict]:
    """讀 `名字排除清單.csv`（欄位：詞,原因,建立日期）。檔案不存在就回傳空列表——
    這份清單是宇軒逐筆聽過候選之後才會有的人工回報，不是必要輸入。"""
    path = Path(path)
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            term = (row.get("詞") or "").strip()
            reason = (row.get("原因") or "").strip()
            created = (row.get("建立日期") or "").strip()
            if term:
                out.append({"詞": term, "原因": reason, "建立日期": created})
    return out


def _normalize_match_text(s: str) -> str:
    """排除清單比對用：只留下中英文數字字元，忽略標點與空白——跟 `_clean_char_indices`
    篩掉標點空白的邏輯一致，這樣清單裡寫「風，風」也能比對到候選記下的「風風」。"""
    return "".join(re.findall(r"[\w一-鿿]", s, re.UNICODE))


def _build_exclusion_lookup(exclusions: list[dict]) -> dict[str, str]:
    """回傳 {正規化後的詞: 原因}；同一個正規化字串出現多次，留第一筆的原因。"""
    lookup: dict[str, str] = {}
    for row in exclusions:
        key = _normalize_match_text(row.get("詞", ""))
        if key and key not in lookup:
            lookup[key] = row.get("原因", "")
    return lookup


# ---------- 字層級時間軸：把 word 展開成逐字時間（線性內插） ----------

def _char_timeline(words: list[dict]) -> list[dict]:
    """把 words（可能一個 word 不只一個字）展開成逐字時間，[{"ch","start","end","word_idx"}]，
    字內時間用該 word 的區間線性內插。標點、空白照樣展開（比對時再濾掉），這樣位置索引才
    跟時間軸對得整齊。"""
    chars: list[dict] = []
    for wi, w in enumerate(words):
        text = w.get("word") or ""
        s, e = w.get("start", 0.0), w.get("end", 0.0)
        n = len(text)
        if n == 0:
            continue
        dur = (e - s) / n
        for i, ch in enumerate(text):
            chars.append({
                "ch": ch,
                "start": s + dur * i,
                "end": s + dur * (i + 1),
                "word_idx": wi,
            })
    return chars


def _clean_char_indices(chars: list[dict]) -> list[int]:
    """回傳非空白／非標點字元在 chars 裡的索引清單，供滑動視窗比對用。"""
    return [i for i, c in enumerate(chars) if re.match(r"[\w一-鿿]", c["ch"], re.UNICODE)]


def _scan_chars_for_terms(chars: list[dict], clean_idx: list[int], terms: list[dict]) -> list[dict]:
    """在 chars（用 clean_idx 篩過標點空白）裡找每個候選詞（名冊寫法或敏感詞），
    回傳命中清單：[{"term": term_dict, "level": 精確/A1/A2, "start_ci": clean_idx 位置,
    "end_ci": clean_idx 位置（不含）, "text": 逐字稿裡實際比對到的那幾個字}]。"""
    clean_text = "".join(chars[i]["ch"] for i in clean_idx)
    hits = []
    for term in terms:
        name = term["寫法"]
        L = len(name)
        if L == 0 or L > len(clean_text):
            continue
        is_sensitive = "代號" in term and term.get("_sensitive")
        name_parts = None if is_sensitive else [_syllable_parts(c) for c in name]
        for i in range(len(clean_text) - L + 1):
            window = clean_text[i:i + L]
            if is_sensitive:
                level = "精確" if window == name else None
            else:
                level = _match_level(window, name, name_parts)
            if level is None:
                continue
            hits.append({"term": term, "level": level, "start_ci": i, "end_ci": i + L, "text": window})
    return hits


# ---------- 切點修正 ----------

def _find_pause_point(x: np.ndarray, sr: int, target_idx: int, lo_idx: int, hi_idx: int,
                       context_lo_idx: int, context_hi_idx: int) -> tuple[int, bool]:
    """在允許範圍 [lo_idx, hi_idx]（已經跟相鄰字的邊界夾好，不會切到字裡面）內，
    用「安靜門檻」（比 [context_lo_idx, context_hi_idx] 這段上下文裡最大聲的地方
    低 PAUSE_DB_DROP dB）找離 target_idx 最近的安靜音框，回傳
    (切點樣本索引, 是否找到乾淨停頓)。找不到就回傳 (target_idx 夾進允許範圍後, False)。"""
    fr = max(1, int(PAUSE_FRAME_S * sr))
    lo_idx = max(0, lo_idx)
    hi_idx = min(len(x), hi_idx)
    fallback = int(np.clip(target_idx, lo_idx, hi_idx))
    if hi_idx - lo_idx < fr:
        return fallback, False

    context_lo_idx = max(0, min(context_lo_idx, lo_idx))
    context_hi_idx = min(len(x), max(context_hi_idx, hi_idx))
    ctx = x[context_lo_idx:context_hi_idx]
    n_ctx = len(ctx) // fr
    if n_ctx == 0:
        return fallback, False
    ctx_db = 20 * np.log10(np.sqrt((ctx[: n_ctx * fr].reshape(n_ctx, fr) ** 2).mean(1) + 1e-12))
    loud_ref = float(np.percentile(ctx_db, 90))
    quiet_thr = loud_ref - PAUSE_DB_DROP

    seg = x[lo_idx:hi_idx]
    n = len(seg) // fr
    if n == 0:
        return fallback, False
    db = 20 * np.log10(np.sqrt((seg[: n * fr].reshape(n, fr) ** 2).mean(1) + 1e-12))
    quiet = db <= quiet_thr
    if not quiet.any():
        return fallback, False

    frame_centers = lo_idx + (np.arange(n) + 0.5) * fr
    order = np.argsort(np.abs(frame_centers - target_idx))
    for i in order:
        if quiet[i]:
            return int(round(frame_centers[i])), True
    return fallback, False


def _refine_cut(audio: np.ndarray, sr: int, target_t: float, lo_t: float, hi_t: float) -> tuple[float, bool]:
    """`_find_pause_point` 的秒數版本：lo_t/hi_t 是已經跟相鄰字夾好的允許範圍（秒）。"""
    target_idx = int(round(target_t * sr))
    lo_idx = int(round(max(lo_t, target_t - PAUSE_SEARCH_S) * sr))
    hi_idx = int(round(min(hi_t, target_t + PAUSE_SEARCH_S) * sr))
    context_lo_idx = int(round(max(0.0, target_t - PAUSE_CONTEXT_S) * sr))
    context_hi_idx = int(round((target_t + PAUSE_CONTEXT_S) * sr))
    idx, clean = _find_pause_point(audio, sr, target_idx, lo_idx, hi_idx, context_lo_idx, context_hi_idx)
    return idx / sr, clean


# ---------- 位置與建議做法 ----------

def _position(start_ci: int, end_ci: int, n_clean: int) -> str:
    if start_ci == 0 and end_ci == n_clean:
        return "句首句尾"  # 整句只有這個詞，理論上很少見，仍要有個歸類
    if start_ci == 0:
        return "句首"
    if end_ci == n_clean:
        return "句尾"
    return "句中"


def _suggest_action(position: str, start_clean: bool, end_clean: bool) -> tuple[str, str | None]:
    """回傳 (建議做法, 原因)。方案 1（09-23 定案）：找不到乾淨切點就整句換掉；
    切點都乾淨的話，沿用 02 規格——句首／句尾且前後有停頓 → 直接消音，否則只換名字。"""
    if not (start_clean and end_clean):
        return "整句換掉", "找不到乾淨切點（前後 0.3 秒內沒有夠安靜的停頓）"
    if position in ("句首", "句尾", "句首句尾"):
        return "直接消音", None
    return "只換名字", None


# ---------- 主流程 ----------

def find_names(
    audio_path: Path,
    workdir: Path,
    sentences: list[dict],
    words: list[dict],
    roster_path: Path,
    sensitive_path: Path | None = None,
    exclusion_path: Path | None = None,
) -> dict:
    """在標成「老師」的句子裡找名冊上的名字與敏感詞。回傳 dict：
        candidates: 每筆 {start, end, sentence, name, canonical, code, position,
                          suggested_action, reason, cut_confidence, buffer_s,
                          clip_path, level, matched_text, 信心}
        已自動排除: 每筆 {start, end, sentence_id, matched_text, 原因}——matched_text
                    命中「名字排除清單」的候選，不進 candidates，記在這裡
        排除清單: 讀到的排除清單原始內容（詞/原因/建立日期），供覆核頁顯示
        統計: {總筆數, 各層級筆數, 各建議做法筆數, 切點信心分布, 已自動排除數}
        elapsed

    `exclusion_path` 不給的話，預設抓 `roster_path` 同一層目錄下的
    `名字排除清單.csv`（宇軒的名冊、敏感詞、排除清單都放在同一個資料夾）；
    檔案不存在就當作沒有排除清單，照常運作。

    `workdir/名字候選.json` 已存在就直接讀出來回傳。名字與句子內容只寫進檔案，
    不印在終端機。
    """
    workdir = Path(workdir)
    audio_path = Path(audio_path)
    cache_path = names_path(workdir)
    cached = read_json(cache_path, default=None)
    if cached is not None:
        print("[4/找名字] 已有 名字候選.json，略過")
        return cached

    t0 = time.time()
    roster_path = Path(roster_path)
    roster = load_roster(roster_path)
    for t in roster:
        t["_sensitive"] = False
    sensitive = load_sensitive_words(sensitive_path) if sensitive_path else []
    for t in sensitive:
        t["_sensitive"] = True
    terms = roster + sensitive

    if exclusion_path is None:
        exclusion_path = roster_path.parent / "名字排除清單.csv"
    exclusions = load_exclusion_list(exclusion_path)
    exclusion_lookup = _build_exclusion_lookup(exclusions)

    if not terms:
        print("[4/找名字] 名冊／敏感詞都是空的，沒有東西可以找")

    clip_dir = name_candidates_dir(workdir)
    clip_dir.mkdir(parents=True, exist_ok=True)

    with sf.SoundFile(str(audio_path)) as f:
        sr = f.samplerate
        total_samples = f.frames
    total_dur = total_samples / sr

    candidates: list[dict] = []
    excluded: list[dict] = []
    level_counts = {"精確": 0, "A1": 0, "A2": 0}
    confidence_level_counts = {"高": 0, "中": 0, "低": 0}
    action_counts: dict[str, int] = {}
    confidence_counts = {"雙邊乾淨": 0, "單邊乾淨": 0, "都不乾淨": 0}

    teacher_sentences = [s for s in sentences if s.get("label") == "老師"]
    audio_full = None  # 延遲載入，名冊是空的、或這句的命中全部被排除清單擋掉時完全不用碰音檔

    for sent in teacher_sentences:
        sent_words = [w for w in words if w["start"] >= sent["start"] - 0.05 and w["end"] <= sent["end"] + 0.05]
        if not sent_words:
            continue
        chars = _char_timeline(sent_words)
        clean_idx = _clean_char_indices(chars)
        if not clean_idx:
            continue
        hits = _scan_chars_for_terms(chars, clean_idx, terms)
        if not hits:
            continue

        for hit in hits:
            term, level, matched_text = hit["term"], hit["level"], hit["text"]
            start_ci, end_ci = hit["start_ci"], hit["end_ci"]
            first_ci_idx = clean_idx[start_ci]
            last_ci_idx = clean_idx[end_ci - 1]
            raw_start_t = chars[first_ci_idx]["start"]
            raw_end_t = chars[last_ci_idx]["end"]

            excl_reason = exclusion_lookup.get(_normalize_match_text(matched_text))
            if excl_reason is not None:
                excluded.append({
                    "start": round(raw_start_t, 3),
                    "end": round(raw_end_t, 3),
                    "sentence_id": sent.get("id"),
                    "matched_text": matched_text,
                    "原因": excl_reason,
                })
                continue

            if audio_full is None:
                audio_full, sr = sf.read(str(audio_path), dtype="float32")

            # 相鄰字的邊界：切點不可落在前一個字／後一個字裡面
            prev_bound = chars[clean_idx[start_ci - 1]]["end"] if start_ci > 0 else 0.0
            next_bound = chars[clean_idx[end_ci]]["start"] if end_ci < len(clean_idx) else total_dur

            start_t, start_clean = _refine_cut(audio_full, sr, raw_start_t, prev_bound, raw_start_t)
            end_t, end_clean = _refine_cut(audio_full, sr, raw_end_t, raw_end_t, next_bound)
            if end_t <= start_t:
                start_t, end_t = raw_start_t, raw_end_t

            position = _position(start_ci, end_ci, len(clean_idx))
            action, reason = _suggest_action(position, start_clean, end_clean)

            if start_clean and end_clean:
                confidence = "雙邊乾淨"
            elif start_clean or end_clean:
                confidence = "單邊乾淨"
            else:
                confidence = "都不乾淨"

            clip_start = max(0.0, start_t - CANDIDATE_CLIP_PAD_S)
            clip_end = min(total_dur, end_t + CANDIDATE_CLIP_PAD_S)
            clip_idx = len(candidates)
            clip_name = f"{clip_idx:04d}_{clip_start:.1f}s.wav"
            clip_path = clip_dir / clip_name
            if not clip_path.exists():
                s0, s1 = int(clip_start * sr), int(clip_end * sr)
                sf.write(str(clip_path), audio_full[s0:s1], sr)

            match_confidence = LEVEL_CONFIDENCE[level]
            candidates.append({
                "start": round(start_t, 3),
                "end": round(end_t, 3),
                "sentence": sent.get("text", ""),
                "sentence_id": sent.get("id"),
                "name": term["寫法"],
                "canonical": term["canonical"],
                "代號": term["代號"],
                "敏感詞": bool(term.get("_sensitive")),
                "matched_text": matched_text,
                "比對層級": level,
                "信心": match_confidence,
                "位置": position,
                "建議做法": action,
                "原因": reason,
                "切點信心": confidence,
                "建議緩衝秒數": DEFAULT_BUFFER_S,
                "候選音檔": str(clip_path.relative_to(workdir)),
            })
            if not term.get("_sensitive"):
                level_counts[level] += 1
            confidence_level_counts[match_confidence] += 1
            action_counts[action] = action_counts.get(action, 0) + 1
            confidence_counts[confidence] += 1

    elapsed = time.time() - t0
    result = {
        "candidates": candidates,
        "已自動排除": excluded,
        "排除清單": exclusions,
        "統計": {
            "總筆數": len(candidates),
            "各層級筆數": level_counts,
            "信心分布": confidence_level_counts,
            "各建議做法筆數": action_counts,
            "切點信心分布": confidence_counts,
            "已自動排除數": len(excluded),
        },
        "elapsed": round(elapsed, 1),
    }
    write_json(cache_path, result)
    print(f"[4/找名字] 完成：{len(candidates)} 筆候選，耗時 {elapsed:.1f} 秒")
    if excluded:
        print(f"[4/找名字] 已自動排除 {len(excluded)} 筆（命中「名字排除清單」）")
    print(f"[4/找名字] 讀音比對層級分布：{level_counts}　信心分布：{confidence_level_counts}")
    print(f"[4/找名字] 建議做法分布：{action_counts}")
    print(f"[4/找名字] 切點信心分布：{confidence_counts}")
    return result
