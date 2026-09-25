"""流程第 3 步：學員逐字稿校對——資料準備（第一塊）。

成品裡學員的聲音是照這份文字用匿名聲線重念的，文字錯一個字就念錯一個字，
所以學員說的每一句都要人逐句聽過、改對。這支模組把「要校對的句子」整理好，
網頁（下一塊）讀它、存回它。

每一句：
- 時間、第 1 步的說話者判斷（不是老師／不確定／太短）
- **聲音**：學員的句子重新抽聲紋、分群，依講話總秒數排成 A、B、C⋯⋯
  （第 2 步「聲音編號指認」：人再把 A、B、C 對到名冊上的學員）
- **原文**（Groq 轉出來的）與**校對稿**（名冊上的名字、敏感詞已換成代號，人在這份上改）
- **名字提示**：跟名冊讀音相近、但沒有自動替換的字（同音不同字），提醒人看一下

產出：`校對/校對稿.json`。只處理指定的時間範圍（測試先做 5 分鐘）。
隱私：校對稿含學員分享內容，跟其他逐字稿一樣不貼進對話、不進 git。
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from bookclub import workdir as wd

STUDENT_LABELS = ("不是老師", "不確定", "太短")
CLUSTER_DISTANCE_T = 0.6   # 跟 bookclub/refpick.py 認老師用的分群門檻一樣
MIN_EMB_S = 0.8


def proofread_dir(workdir: Path) -> Path:
    return workdir / "校對"


def proofread_path(workdir: Path) -> Path:
    return proofread_dir(workdir) / "校對稿.json"


# ---------- 名字、敏感詞替換（純函式） ----------

def replace_terms(text: str, terms: list[dict]) -> tuple[str, list[dict]]:
    """精確比對名冊寫法與敏感詞，換成代號。長的先換。回傳（校對稿、換了哪些）。"""
    done = []
    for t in sorted(terms, key=lambda t: -len(t["寫法"])):
        if t["寫法"] and t["代號"] and t["寫法"] in text:
            text = text.replace(t["寫法"], t["代號"])
            done.append({"原字": t["寫法"], "換成": t["代號"]})
    return text, done


def sound_alike_hints(text: str, roster: list[dict]) -> list[dict]:
    """讀音相近（同音不同調、台灣口音常混的音）但沒被精確換掉的字，列出來提醒人看。"""
    from bookclub.names import _match_level, _syllable_parts

    hints, seen = [], set()
    chars = [c for c in text]
    for r in roster:
        name = r["寫法"]
        if len(name) < 2 or not all("一" <= c <= "鿿" for c in name):
            continue
        parts = [_syllable_parts(c) for c in name]
        for i in range(len(chars) - len(name) + 1):
            window = "".join(chars[i:i + len(name)])
            if window == name or not all("一" <= c <= "鿿" for c in window):
                continue
            level = _match_level(window, name, parts)
            if level == "A1" and (window, r["代號"]) not in seen:
                seen.add((window, r["代號"]))
                hints.append({"字": window, "可能是": r["代號"], "層級": level})
    return hints


# ---------- 聲音分群 ----------

def cluster_voices(embs: np.ndarray, durs: list[float]) -> list[str]:
    """聲紋分群，依總秒數排成 A、B、C⋯⋯（純函式，給測試用）。"""
    from scipy.cluster.hierarchy import fcluster, linkage

    if len(embs) == 1:
        return ["A"]
    ids = fcluster(linkage(embs, method="average", metric="cosine"), t=CLUSTER_DISTANCE_T, criterion="distance")
    secs: dict[int, float] = {}
    for c, d in zip(ids, durs):
        secs[int(c)] = secs.get(int(c), 0.0) + d
    order = sorted(secs, key=lambda c: -secs[c])
    name = {c: (chr(ord("A") + k) if k < 26 else f"Z{k}") for k, c in enumerate(order)}
    return [name[int(c)] for c in ids]


def build_sample(
    workdir: str | Path, start: float, end: float, *,
    roster_path: str | Path | None = None, sensitive_path: str | Path | None = None,
) -> dict:
    """整理 [start, end) 秒之間學員說的句子，寫出 `校對/校對稿.json`。"""
    from bookclub import names
    from bookclub.config import data_dir
    from bookclub.refpick import _l2norm, _load_embed_model

    workdir = Path(workdir).expanduser()
    speakers = wd.read_json(wd.speakers_path(workdir))
    if not speakers:
        raise FileNotFoundError(f"找不到 {wd.speakers_path(workdir)}，先跑 `bookclub run analyze`。")
    roster = names.load_roster(Path(roster_path) if roster_path else data_dir() / "名冊.csv")
    sensitive = names.load_sensitive_words(Path(sensitive_path) if sensitive_path else data_dir() / "敏感詞.csv")

    sents = [s for s in speakers["sentences"] if s["label"] in STUDENT_LABELS and start <= s["start"] < end]
    print(f"[校對] {wd.fmt_time(start)}–{wd.fmt_time(end)}：學員句子 {len(sents)} 句")

    t0 = time.time()
    from pyannote.core import Segment

    inference = _load_embed_model()
    audio = str(wd.audio_path(workdir))
    idx, embs, durs = [], [], []
    for i, s in enumerate(sents):
        if s["end"] - s["start"] >= MIN_EMB_S:
            embs.append(_l2norm(inference.crop(audio, Segment(s["start"], s["end"])).reshape(-1)))
            idx.append(i)
            durs.append(s["end"] - s["start"])
    voices = ["?"] * len(sents)
    if embs:
        E = np.stack(embs)
        labels = cluster_voices(E, durs)
        for i, v in zip(idx, labels):
            voices[i] = v
        # 太短抽不準的句子：就近歸到前一句同一個聲音，標成「猜的」讓人確認
    emb_s = time.time() - t0

    items = []
    for i, s in enumerate(sents):
        draft, replaced = replace_terms(s["text"], roster + sensitive)
        guess = voices[i] == "?"
        if guess:
            prev = next((voices[k] for k in range(i - 1, -1, -1) if voices[k] != "?"), "?")
            voices[i] = prev
        items.append({
            "id": s["id"], "start": s["start"], "end": s["end"], "說話者判斷": s["label"],
            "聲音": voices[i], "聲音是猜的": guess,
            "原文": s["text"], "校對稿": draft, "已替換": replaced,
            "名字提示": sound_alike_hints(draft, roster),
            "已校對": False, "校對秒數": None,
        })

    voice_secs: dict[str, float] = {}
    for it in items:
        voice_secs[it["聲音"]] = voice_secs.get(it["聲音"], 0.0) + it["end"] - it["start"]
    data = {
        "範圍": [start, end], "句子": items,
        "聲音": {v: {"秒數": round(t, 1), "學員": None} for v, t in sorted(voice_secs.items(), key=lambda kv: -kv[1])},
        "統計": {
            "句數": len(items), "學員講話秒數": round(sum(it["end"] - it["start"] for it in items), 1),
            "聲音數": len(voice_secs), "自動替換句數": sum(1 for it in items if it["已替換"]),
            "有名字提示句數": sum(1 for it in items if it["名字提示"]), "聲音是猜的句數": sum(1 for it in items if it["聲音是猜的"]),
            "抽聲紋分群秒": round(emb_s, 1),
        },
    }
    wd.write_json(proofread_path(workdir), data)
    st = data["統計"]
    print(f"[校對] {st['句數']} 句、學員講 {st['學員講話秒數']:.0f} 秒；分成 {st['聲音數']} 個聲音；"
          f"自動換代號 {st['自動替換句數']} 句、要看一下的名字提示 {st['有名字提示句數']} 句；"
          f"太短、聲音用猜的 {st['聲音是猜的句數']} 句（{st['抽聲紋分群秒']:.0f} 秒）→ {proofread_path(workdir)}")
    return data
