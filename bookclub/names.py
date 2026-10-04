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
import io
import re
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from bookclub import csvfile
from bookclub.workdir import fingerprint, name_candidates_dir, names_path, read_json, write_json

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
# 10-04 #105：一個字的時間被拉長（轉文字時字橫跨被挖掉的靜音，第一堂 43:06.9 一個字 2.2 秒）→ 名字範圍照聲音縮短
STRETCHED_CHAR_S = 0.5    # 名字平均一個字超過這麼長，才去看聲音（正常一個字 0.2–0.4 秒）
TRIM_PAUSE_S = 0.3        # 名字範圍裡安靜這麼久以上，算真的停頓（範圍在這裡分段，挑真的有名字的那段）


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

def _example_filter(name: str):
    """10-04 #107：範本的範例列（install.sh 複製過來的「範例學員」這類示範資料）不拿來比對。
    判斷沿用設定包匯出入用的 `profile.is_example_row`（第一欄的值是範本裡的範例列）。回傳 (表頭, 列) → 是不是範例列。"""
    from bookclub import profile

    keys = profile.example_keys(name)
    return lambda header, row: bool(keys) and profile.is_example_row(
        name, header, {k: (v or "").strip() for k, v in row.items() if k is not None}, keys)


def load_roster(path: Path) -> list[dict]:
    """讀 `名冊.csv`（欄位：中文名,其他寫法,英文代號,聲線,性別），展開成
    [{"寫法": 中文名或其他寫法, "canonical": 中文名, "代號": 英文代號}, ...]，
    一個人可能對應好幾筆（本名＋其他寫法各一筆）。範本的範例列不算（#107）。"""
    path = Path(path)
    if not path.exists():
        return []
    out = []
    is_example = _example_filter("名冊.csv")
    with io.StringIO(csvfile.read_text(path), newline="") as f:   # 10-03 #35：Excel 另存的 Big5 也讀得了
        reader = csv.DictReader(f)
        for row in reader:
            canonical = (row.get("中文名") or "").strip()
            code = (row.get("英文代號") or "").strip()
            if not canonical or is_example(reader.fieldnames or [], row):
                continue
            out.append({"寫法": canonical, "canonical": canonical, "代號": code})
            variants = (row.get("其他寫法") or "").strip()
            for v in re.split(r"[、,，/]", variants):
                v = v.strip()
                if v:
                    out.append({"寫法": v, "canonical": canonical, "代號": code})
    return out


def roster_duplicate_spellings(path: Path) -> list[dict]:
    """10-03 第八批（#12）：名冊裡同一個寫法（中文名或其他寫法）出現在兩列以上 → [{寫法, 列: [列號…]}]。
    列號照試算表：標題列算第 1 列。同一處名字會同時比中兩個人、出兩張卡；讓人決定是不是同一人。"""
    path = Path(path)
    if not path.exists():
        return []
    rows: dict[str, list[int]] = {}
    with io.StringIO(csvfile.read_text(path), newline="") as f:   # 10-03 #35：Excel 另存的 Big5 也讀得了
        for n, row in enumerate(csv.DictReader(f), start=2):
            canonical = (row.get("中文名") or "").strip()
            if not canonical:
                continue
            spells = {canonical} | {v.strip() for v in re.split(r"[、,，/]", row.get("其他寫法") or "") if v.strip()}
            for w in spells:
                rows.setdefault(w, []).append(n)
    return [{"寫法": w, "列": ns} for w, ns in sorted(rows.items(), key=lambda kv: kv[1]) if len(ns) > 1]


def load_sensitive_words(path: Path) -> list[dict]:
    """讀 `敏感詞.csv`（欄位：原詞,替代詞）。範本的範例列不算（#107）。"""
    path = Path(path)
    if not path.exists():
        return []
    out = []
    is_example = _example_filter("敏感詞.csv")
    with io.StringIO(csvfile.read_text(path), newline="") as f:   # 10-03 #35：Excel 另存的 Big5 也讀得了
        reader = csv.DictReader(f)
        for row in reader:
            orig = (row.get("原詞") or "").strip()
            repl = (row.get("替代詞") or "").strip()
            if orig and not is_example(reader.fieldnames or [], row):
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
    with io.StringIO(csvfile.read_text(path), newline="") as f:   # 10-03 #35：Excel 另存的 Big5 也讀得了
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


def _voiced_islands(audio: np.ndarray, sr: int, a: float, b: float) -> list[tuple[float, float]]:
    """[a, b] 裡有聲音的幾段（純函式）：安靜門檻跟 `_find_pause_point` 一樣（比前後 1 秒上下文最大聲的地方低
    PAUSE_DB_DROP dB），安靜連續 TRIM_PAUSE_S 秒以上才算分段；頭尾的安靜不算在任何一段裡。"""
    fr = max(1, int(PAUSE_FRAME_S * sr))
    lo, hi = max(0, int(a * sr)), min(len(audio), int(b * sr))
    c0, c1 = max(0, int((a - PAUSE_CONTEXT_S) * sr)), min(len(audio), int((b + PAUSE_CONTEXT_S) * sr))
    n, n_ctx = (hi - lo) // fr, (c1 - c0) // fr
    if n == 0 or n_ctx == 0:
        return []
    ctx = audio[c0:c0 + n_ctx * fr].reshape(n_ctx, fr)
    thr = float(np.percentile(20 * np.log10(np.sqrt((ctx ** 2).mean(1) + 1e-12)), 90)) - PAUSE_DB_DROP
    seg = audio[lo:lo + n * fr].reshape(n, fr)
    loud = 20 * np.log10(np.sqrt((seg ** 2).mean(1) + 1e-12)) > thr
    min_quiet = max(1, int(round(TRIM_PAUSE_S / PAUSE_FRAME_S)))
    islands: list[list[int]] = []
    quiet_run = min_quiet   # 開頭當成剛停頓完
    for k, v in enumerate(loud):
        if v:
            if quiet_run >= min_quiet or not islands:
                islands.append([k, k + 1])
            else:
                islands[-1][1] = k + 1
            quiet_run = 0
        else:
            quiet_run += 1
    t = lambda k: (lo + k * fr) / sr
    return [(t(i0), t(i1)) for i0, i1 in islands]


def _trim_stretched(audio: np.ndarray, sr: int, spans: list[tuple[float, float]]) -> tuple[float, float] | None:
    """10-04 #105（純函式）：名字每個字的時間 spans；平均一個字超過 STRETCHED_CHAR_S 秒、而且範圍裡有真的停頓
    （安靜 TRIM_PAUSE_S 秒以上）或頭尾一大段安靜時，回傳縮短後的 (起, 訖)；不用縮回傳 None。

    根本原因：轉文字送 Groq 前挖掉靜音，一個字橫跨接縫時，換算回原片就把挖掉的靜音整段包進去（舊工作區的逐字稿
    都是這樣；新轉的已經在 `transcribe._map_word` 擋掉）。名字的字是連著念的，所以只留
    有聲音的地方：保留跟所有正常長度的字重疊的有聲段（從第一段的開頭到最後一段的結尾，慢慢念、字中間有停頓的
    名字不會被縮成只剩一個字）。名字的字全都被拉長時，只有一段有聲音才縮；拿不準就回傳 None 沿用原本範圍——
    寧可消音範圍長，不可漏掉名字的一部分。"""
    a, b = spans[0][0], spans[-1][1]
    if b - a <= STRETCHED_CHAR_S * len(spans):
        return None
    islands = _voiced_islands(audio, sr, a, b)
    if not islands:
        return None
    normal = [(x, y) for x, y in spans if y - x <= STRETCHED_CHAR_S]
    if normal:
        hit = [isl for isl in islands if any(min(isl[1], y) > max(isl[0], x) for x, y in normal)]
        if not hit or any(not any(min(isl[1], y) > max(isl[0], x) for isl in islands) for x, y in normal):
            return None   # 有正常長度的字碰不到任何有聲段 → 拿不準
        lo, hi = hit[0][0], hit[-1][1]
    elif len(islands) == 1:
        lo, hi = islands[0]
    else:
        return None
    lo, hi = max(a, lo), min(b, hi)
    if hi - lo < 0.05 or (lo - a < TRIM_PAUSE_S and b - hi < TRIM_PAUSE_S):
        return None
    return lo, hi


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


# ---------- 要掃哪些句子（10-04 #119） ----------

# 名字候選.json 的「掃描範圍」：2＝除了判成老師的句子，太短、不確定的句子、老師段落裡判成不是老師的句子也掃（#119）。
# 沒有這個欄位＝舊版（只掃判成老師的句子），重跑分析時會補掃放寬的那幾句、加在最後面（見 `find_names`）。
SCAN_SCOPE = 2
SOURCE_SHORT = "太短句"
SOURCE_UNSURE = "不確定句"
SOURCE_NOT_TEACHER = "老師段落裡的不是老師句"
SUPPLEMENT_TAG = "找名字範圍放寬後補找"


def _is_teacher(s: dict) -> bool:
    return s.get("label") == "老師"


def sentence_turn_roles(workdir: str | Path) -> dict[str, str]:
    """句子 id → 「老師」或「學員」（只讀檔）：有 `校對/段落.json` 就用它（含第 3 步人改過的說話者）；
    還沒有就用 `校對/段落_文字.json`（段落分析的文字那一半，分析第 2 步就存好了）；都沒有回傳空的。"""
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    for path in (turns_mod.turns_path(workdir), turns_mod.text_turns_path(workdir)):
        data = read_json(path, default=None)
        if not data or not data.get("段落"):
            continue
        out: dict[str, str] = {}
        for t in data["段落"]:
            who = t.get("說話者") or t.get("文字判斷")
            if not who:
                continue
            for sid in t.get("句子") or []:
                out[sid] = "老師" if who == "老師" else "學員"
        return out
    return {}


def extra_scope(sentences: list[dict], roles: dict[str, str]) -> dict[str, str]:
    """10-04 #119（純函式）：判成老師的句子之外，還要掃名字的句子 → 來源（太短句／不確定句／老師段落裡的不是老師句）。

    - 太短、不確定（以及其他不是「老師」「不是老師」的判斷）：除非落在學員段落，都要掃——寧可多出卡片讓人判斷，不可漏
    - 不是老師：落在老師段落才掃
    - 學員段落裡的句子照舊不掃（學員說的名字另有「學員名字候選」那一套，或整段重念時在校對稿換掉）
    `roles` 是 `sentence_turn_roles` 的結果；沒有段落資料（空的）時，太短、不確定的句子全掃，不是老師的句子不掃。"""
    out: dict[str, str] = {}
    for s in sentences:
        sid, lab = s.get("id"), s.get("label")
        if sid is None or lab == "老師":
            continue
        role = roles.get(sid)
        if lab == "不是老師":
            if role == "老師":
                out[sid] = SOURCE_NOT_TEACHER
        elif role != "學員":
            out[sid] = SOURCE_SHORT if lab == "太短" else SOURCE_UNSURE
    return out


# ---------- 主流程 ----------

def _load_terms(roster_path: Path, sensitive_path: Path | None) -> list[dict]:
    roster = load_roster(roster_path)
    for t in roster:
        t["_sensitive"] = False
    sensitive = load_sensitive_words(sensitive_path) if sensitive_path else []
    for t in sensitive:
        t["_sensitive"] = True
    return roster + sensitive


def _scan_sentences(sents: list[dict], owned: dict, terms: list[dict], exclusion_lookup: dict[str, str],
                    audio: dict, workdir: Path, clip_dir: Path, first_idx: int, annotate, write_clips: bool
                    ) -> tuple[list[dict], list[dict]]:
    """掃這幾句，回傳 (候選, 命中排除清單的)。`audio`：{path, sr, total_dur, data}，data 用到才載入（就地存回）。
    候選音檔檔名的編號從 `first_idx` 起算；`write_clips=False` 時不寫音檔（`inspect 名字 --重算` 用）。"""
    candidates: list[dict] = []
    excluded: list[dict] = []
    for sent in sents:
        sent_words = owned.get(sent.get("id"), [])
        if not sent_words:
            continue
        chars = _char_timeline(sent_words)
        clean_idx = _clean_char_indices(chars)
        if not clean_idx:
            continue
        hits = _scan_chars_for_terms(chars, clean_idx, terms)
        for hit in hits:
            term, level, matched_text = hit["term"], hit["level"], hit["text"]
            start_ci, end_ci = hit["start_ci"], hit["end_ci"]
            raw_start_t = chars[clean_idx[start_ci]]["start"]
            raw_end_t = chars[clean_idx[end_ci - 1]]["end"]

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

            if audio.get("data") is None:
                audio["data"], audio["sr"] = sf.read(str(audio["path"]), dtype="float32")
            audio_full, sr, total_dur = audio["data"], audio["sr"], audio["total_dur"]

            stretched = _trim_stretched(audio_full, sr, [(chars[clean_idx[k]]["start"], chars[clean_idx[k]]["end"])
                                                         for k in range(start_ci, end_ci)])
            raw_len = raw_end_t - raw_start_t
            if stretched:   # 10-04 #105：字的時間被拉長，名字範圍照聲音縮短（直接消音才不會消掉一大段）
                raw_start_t, raw_end_t = stretched

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
            clip_idx = first_idx + len(candidates)
            clip_name = f"{clip_idx:04d}_{clip_start:.1f}s.wav"
            clip_path = clip_dir / clip_name
            if write_clips and not clip_path.exists():
                s0, s1 = int(clip_start * sr), int(clip_end * sr)
                sf.write(str(clip_path), audio_full[s0:s1], sr)

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
                "信心": LEVEL_CONFIDENCE[level],
                "位置": position,
                "建議做法": action,
                "原因": reason,
                "切點信心": confidence,
                "建議緩衝秒數": DEFAULT_BUFFER_S,
                "候選音檔": str(clip_path.relative_to(workdir)),
                **({"逐字時間拉長秒數": round(raw_len, 3)} if stretched else {}),
                **(annotate(sent) if annotate else {}),
            })
    return candidates, excluded


def _stats(candidates: list[dict], excluded: list[dict]) -> dict:
    level_counts = {"精確": 0, "A1": 0, "A2": 0}
    confidence_level_counts = {"高": 0, "中": 0, "低": 0}
    action_counts: dict[str, int] = {}
    confidence_counts = {"雙邊乾淨": 0, "單邊乾淨": 0, "都不乾淨": 0}
    for c in candidates:
        if not c.get("敏感詞"):
            level_counts[c["比對層級"]] += 1
        confidence_level_counts[c["信心"]] += 1
        action_counts[c["建議做法"]] = action_counts.get(c["建議做法"], 0) + 1
        confidence_counts[c["切點信心"]] += 1
    return {
        "總筆數": len(candidates),
        "各層級筆數": level_counts,
        "信心分布": confidence_level_counts,
        "各建議做法筆數": action_counts,
        "切點信心分布": confidence_counts,
        "已自動排除數": len(excluded),
    }


def _source_counts(candidates: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in candidates:
        if c.get("來源"):
            out[c["來源"]] = out.get(c["來源"], 0) + 1
    return out


def _same_spot(a: dict, b: dict) -> bool:
    """同一句、同一個名冊寫法、起點差不到 0.05 秒：算同一筆候選（補找時不重複加）。"""
    return (a.get("sentence_id") == b.get("sentence_id") and a.get("name") == b.get("name")
            and abs(float(a["start"]) - float(b["start"])) < 0.05)


def supplement(
    audio_path: Path,
    workdir: Path,
    sentences: list[dict],
    words: list[dict],
    roster_path: Path,
    sensitive_path: Path | None = None,
    exclusion_path: Path | None = None,
    *,
    existing: list[dict],
    roles: dict[str, str] | None = None,
    clip_dir: Path | None = None,
    write_clips: bool = True,
) -> dict:
    """10-04 #119：只掃放寬的那幾句（`extra_scope`），回傳
        candidates: 現有候選（`existing`）裡沒有的新候選，每筆帶 `來源` 與 `補找`＝SUPPLEMENT_TAG
        找到: 放寬的句子裡找到的全部候選（含現有候選已經有的）
        已自動排除: 命中排除清單的
        多掃句數: {來源: 句數}
    不寫 `名字候選.json`（呼叫端決定要不要加進去）；`write_clips=False` 時也不寫候選音檔。
    新候選的音檔編號接在現有候選後面，原本的候選編號、覆核決定都不動。"""
    workdir = Path(workdir)
    roles = sentence_turn_roles(workdir) if roles is None else roles
    extra = extra_scope(sentences, roles)
    by_source: dict[str, int] = {}
    for v in extra.values():
        by_source[v] = by_source.get(v, 0) + 1
    terms = _load_terms(Path(roster_path), sensitive_path)
    if exclusion_path is None:
        exclusion_path = Path(roster_path).parent / "名字排除清單.csv"
    lookup = _build_exclusion_lookup(load_exclusion_list(exclusion_path))
    clip_dir = Path(clip_dir) if clip_dir else name_candidates_dir(workdir)
    if write_clips:
        clip_dir.mkdir(parents=True, exist_ok=True)
    found: list[dict] = []
    excluded: list[dict] = []
    if extra and terms:
        with sf.SoundFile(str(audio_path)) as f:
            audio = {"path": audio_path, "sr": f.samplerate, "total_dur": f.frames / f.samplerate, "data": None}
        found, excluded = _scan_sentences(
            [s for s in sentences if s.get("id") in extra], assign_words(sentences, words), terms, lookup, audio,
            workdir, clip_dir, len(existing), lambda s: {"來源": extra[s["id"]]}, write_clips)
    new = [{**c, "補找": SUPPLEMENT_TAG} for c in found if not any(_same_spot(o, c) for o in existing)]
    return {"candidates": new, "找到": found, "已自動排除": excluded, "多掃句數": by_source}


def find_names(
    audio_path: Path,
    workdir: Path,
    sentences: list[dict],
    words: list[dict],
    roster_path: Path,
    sensitive_path: Path | None = None,
    exclusion_path: Path | None = None,
    *,
    select=None,
    cache_path: Path | None = None,
    clip_dir: Path | None = None,
    use_cache: bool = True,
    annotate=None,
) -> dict:
    """在老師的句子裡找名冊上的名字與敏感詞。回傳 dict：
        candidates: 每筆 {start, end, sentence, name, canonical, code, position,
                          suggested_action, reason, cut_confidence, buffer_s,
                          clip_path, level, matched_text, 信心}
        已自動排除: 每筆 {start, end, sentence_id, matched_text, 原因}——matched_text
                    命中「名字排除清單」的候選，不進 candidates，記在這裡
        排除清單: 讀到的排除清單原始內容（詞/原因/建立日期），供覆核頁顯示
        統計: {總筆數, 各層級筆數, 各建議做法筆數, 切點信心分布, 已自動排除數}
        elapsed

    掃哪些句子（10-04 #119）：判成老師的句子先掃（候選照句子順序排在前面，跟以前一樣）；再掃 `extra_scope` 放寬的
    句子——太短、不確定的句子（不在學員段落裡的）、老師段落裡判成不是老師的句子——這些候選排在後面、多一個
    `來源` 欄位（太短句／不確定句／老師段落裡的不是老師句），信心照比對層級。段落資料見 `sentence_turn_roles`。

    `exclusion_path` 不給的話，預設抓 `roster_path` 同一層目錄下的
    `名字排除清單.csv`（宇軒的名冊、敏感詞、排除清單都放在同一個資料夾）；
    檔案不存在就當作沒有排除清單，照常運作。

    `workdir/名字候選.json` 已存在就直接讀出來回傳。舊版找的（沒有 `掃描範圍`）先補掃放寬的句子，新的候選加在最後面
    （原本的候選編號不變，覆核決定照舊對得上）。名字與句子內容只寫進檔案，不印在終端機。

    09-29 加（保留原聲學員講到名字，`bookclub/studentnames.py` 用）：`select(句子) → bool` 換掉「只掃老師」、
    `cache_path`／`clip_dir` 換存放位置、`use_cache=False` 不沿用舊結果、`annotate(句子) → dict` 每筆候選多加的欄位。
    給了 `select` 就只掃 `select` 選的句子（不放寬）。都不給時照上面的預設。
    """
    workdir = Path(workdir)
    audio_path = Path(audio_path)
    roster_path = Path(roster_path)
    cache_path = Path(cache_path) if cache_path else names_path(workdir)
    cached = read_json(cache_path, default=None) if use_cache else None
    default_scope = select is None
    select = select or _is_teacher
    # 指紋只算判成老師的句子（跟以前一樣，舊工作區的指紋才對得上）
    in_fp = fingerprint([[s["id"], s["text"]] for s in sentences if select(s)])
    if cached is not None:
        print("[4/找名字] 已有 名字候選.json，略過")
        dirty = False
        if default_scope and cached.get("掃描範圍") != SCAN_SCOPE:
            # 10-04 #119：舊版只掃判成老師的句子；補掃放寬的句子，新的加在最後面（不重找、不重排原本的候選）
            extra = supplement(audio_path, workdir, sentences, words, roster_path, sensitive_path, exclusion_path,
                               existing=cached.get("candidates", []), clip_dir=clip_dir)
            cached["candidates"] = cached.get("candidates", []) + extra["candidates"]
            cached["掃描範圍"] = SCAN_SCOPE
            st = cached.setdefault("統計", {})
            st["總筆數"] = len(cached["candidates"])
            st["放寬範圍補找數"] = len(extra["candidates"])
            dirty = True
            print(f"[4/找名字] 找名字範圍放寬（太短、不確定的句子也掃）：補找到 {len(extra['candidates'])} 筆，"
                  "加在最後面（原本的候選編號不變）")
        # 09-29 檢查 #7：記下是從哪些老師句子找的；之後說話者判斷或逐字稿改了，大聲提醒（不自動重算：
        # 名字候選已經接了人工覆核決定、補找的名字，自動重算會丟東西）
        if cached.get("輸入指紋") is None:
            cached["輸入指紋"] = in_fp
            dirty = True
        if dirty:
            write_json(cache_path, cached)
        if cached["輸入指紋"] != in_fp:
            msg = ("名字候選是用舊的說話者判斷／逐字稿找的，這次的不一樣：可能漏掉新判成老師的句子裡的名字。"
                   "要重找就把 名字候選.json 改名後重跑（覆核決定會照候選內容對回去）")
            print(f"⚠️ [4/找名字] {msg}")
            cached = {**cached, "輸入改過": msg}
        return cached

    t0 = time.time()
    terms = _load_terms(roster_path, sensitive_path)

    if exclusion_path is None:
        exclusion_path = roster_path.parent / "名字排除清單.csv"
    exclusions = load_exclusion_list(exclusion_path)
    exclusion_lookup = _build_exclusion_lookup(exclusions)

    if not terms:
        print("[4/找名字] 名冊／敏感詞都是空的，沒有東西可以找")

    clip_dir = Path(clip_dir) if clip_dir else name_candidates_dir(workdir)
    clip_dir.mkdir(parents=True, exist_ok=True)

    with sf.SoundFile(str(audio_path)) as f:
        # 音檔內容延遲載入：名冊是空的、或命中全部被排除清單擋掉時完全不用碰
        audio = {"path": audio_path, "sr": f.samplerate, "total_dur": f.frames / f.samplerate, "data": None}

    owned = assign_words(sentences, words)
    candidates, excluded = _scan_sentences([s for s in sentences if select(s)], owned, terms, exclusion_lookup,
                                           audio, workdir, clip_dir, 0, annotate, True)
    if default_scope:   # 10-04 #119：放寬的句子排在後面掃
        extra = extra_scope(sentences, sentence_turn_roles(workdir))
        more, more_ex = _scan_sentences(
            [s for s in sentences if s.get("id") in extra], owned, terms, exclusion_lookup, audio, workdir, clip_dir,
            len(candidates), lambda s: {**(annotate(s) if annotate else {}), "來源": extra[s["id"]]}, True)
        candidates += more
        excluded += more_ex

    elapsed = time.time() - t0
    stats = _stats(candidates, excluded)
    if default_scope:
        stats["各來源筆數"] = _source_counts(candidates)
    result = {
        "candidates": candidates,
        "輸入指紋": in_fp,
        **({"掃描範圍": SCAN_SCOPE} if default_scope else {}),
        "已自動排除": excluded,
        "排除清單": exclusions,
        "統計": stats,
        "elapsed": round(elapsed, 1),
    }
    write_json(cache_path, result)
    print(f"[4/找名字] 完成：{len(candidates)} 筆候選，耗時 {elapsed:.1f} 秒")
    if default_scope and stats["各來源筆數"]:
        print(f"[4/找名字] 其中太短、不確定的句子與老師段落裡的不是老師句：{stats['各來源筆數']}")
    if excluded:
        print(f"[4/找名字] 已自動排除 {len(excluded)} 筆（命中「名字排除清單」）")
    print(f"[4/找名字] 讀音比對層級分布：{stats['各層級筆數']}　信心分布：{stats['信心分布']}")
    print(f"[4/找名字] 建議做法分布：{stats['各建議做法筆數']}")
    print(f"[4/找名字] 切點信心分布：{stats['切點信心分布']}")
    return result


def preview_supplement(workdir: str | Path) -> dict:
    """`bookclub inspect <工作區> 名字 --重算` 用：用現在的程式、工作區現在的資料，在放寬的句子裡重找一次名字，
    不寫任何檔（不寫名字候選.json、不寫候選音檔）。回傳 `supplement` 的結果，另加 `現有候選數`、`掃描範圍`。"""
    from bookclub.config import data_dir
    from bookclub.workdir import audio_path, merged_transcript_path, speakers_path

    workdir = Path(workdir)
    roster = data_dir() / "名冊.csv"
    cur = read_json(names_path(workdir), default=None) or {}
    existing = cur.get("candidates", [])
    base = {"現有候選數": len(existing), "掃描範圍": cur.get("掃描範圍")}
    if not roster.is_file() or not audio_path(workdir).is_file():
        return {**base, "candidates": [], "找到": [], "已自動排除": [], "多掃句數": {}, "沒辦法重算": True}
    sents = (read_json(speakers_path(workdir), default={}) or {}).get("sentences", [])
    words = (read_json(merged_transcript_path(workdir), default={}) or {}).get("words", [])
    sensitive = data_dir() / "敏感詞.csv"
    res = supplement(audio_path(workdir), workdir, sents, words, roster, sensitive if sensitive.is_file() else None,
                     existing=existing, write_clips=False)
    return {**base, **res}


def assign_words(sentences: list[dict], words: list[dict], max_gap_s: float = 0.6) -> dict[str, list[dict]]:
    """每個字分給重疊最多的句子（純函式）；落在句子之間空白的，分給 max_gap_s 內最近的一句。
    09-29 宇軒：以前只收「完全落在句子時間內」的字，句子開頭的字時間早了半秒就被丟掉（「淑芳很輕輕的說」只剩「芳很⋯」，名字漏抓）。"""
    import bisect

    ss = sorted((s for s in sentences if s.get("id") is not None), key=lambda s: s["start"])
    starts = [s["start"] for s in ss]
    out: dict[str, list[dict]] = {}
    for w in words:
        k = bisect.bisect_right(starts, w["end"])
        best, best_ov, best_gap = None, 0.0, None
        for s in ss[max(0, k - 4):k + 1]:
            ov = min(w["end"], s["end"]) - max(w["start"], s["start"])
            if ov > best_ov:
                best, best_ov = s, ov
            elif best_ov <= 0:
                gap = max(s["start"] - w["end"], w["start"] - s["end"], 0.0)
                if gap <= max_gap_s and (best_gap is None or gap < best_gap):
                    best, best_gap = s, gap
        if best is not None:
            out.setdefault(best["id"], []).append(w)
    return out


def append_candidates(workdir: str | Path, sentence_ids: list[str]) -> int:
    """第 3 步把一段學員段落改成老師（09-29 宇軒：那段其實是老師在講話）之後，補找這幾句裡老師提到的名字，
    加在 `名字候選.json` 最後面（原本的候選編號不變，覆核決定照舊對得上）。回傳補了幾筆；還沒跑過找名字回傳 0。"""
    import tempfile

    from bookclub.config import data_dir
    from bookclub.workdir import audio_path, merged_transcript_path, speakers_path

    workdir = Path(workdir)
    cur = read_json(names_path(workdir), default=None)
    roster = data_dir() / "名冊.csv"
    if cur is None or not roster.is_file() or not audio_path(workdir).is_file():
        return 0
    ids = set(sentence_ids)
    sents = (read_json(speakers_path(workdir), default={}) or {}).get("sentences", [])
    words = (read_json(merged_transcript_path(workdir), default={}) or {}).get("words", [])
    sensitive = data_dir() / "敏感詞.csv"
    with tempfile.TemporaryDirectory() as tmp:
        res = find_names(audio_path(workdir), workdir, sents, words, roster, sensitive if sensitive.is_file() else None,
                         select=lambda s: s.get("id") in ids, cache_path=Path(tmp) / "補找.json", use_cache=False)
    old = cur.get("candidates", [])
    new = [c for c in res["candidates"]
           if not any(o.get("sentence_id") == c.get("sentence_id") and abs(o["start"] - c["start"]) < 0.05 for o in old)]
    if new:
        for c in new:
            c["補找"] = "段落改成老師後補找"
        cur["candidates"] = old + new
        cur.setdefault("統計", {})["總筆數"] = len(cur["candidates"])
        write_json(names_path(workdir), cur)
    return len(new)
