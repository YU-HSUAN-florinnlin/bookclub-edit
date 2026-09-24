"""流程第 5 步（一）：用老師的 AI 聲音重念指定句子。

設定照 09-18～09-22 盲聽驗證過的做法（07 說明第四節）：

1. 文字一律先轉簡體再生成（繁體會念成怪聲）；參考音逐字稿也要轉
2. 參考音逐字稿前面加 `You are a helpful assistant.<|endofprompt|>`
3. 參考音的聲音特徵用 `add_zero_shot_spk()` 算一次，之後每句重複用
4. 每句生成前固定亂數種子（第一個是 42，盲聽最好）
5. 生成完自動檢查，不過才重試（09-19 定案：「種子 42＋內容／長度檢查＋不過才換種子」）：
   - 內容：用 Groq 轉回文字，跟原句比對，念錯、漏字就換下一個種子
   - 長度：有給時間格時，差太多就用同一個種子改 `speed` 再生成一次
   三次都不過，挑最好的一次，標「要人聽」
6. 有 `說話者判斷.json` 的話，順便算每次生成跟這支影片老師聲紋的相似度，
   只記錄、不拿來淘汰（09-19 實測相似度跟人耳評分的相關性很弱）

輸入：工作區的 `參考音/ref.wav`、`ref.txt`（`bookclub ref use` 選定的），
加上一份句子清單。輸出放在工作區的 `生成/老師/`：

- `<句子編號>_第<N>次.wav`：每一次嘗試的原始生成檔（24kHz）
- `<句子編號>.wav`：選定的那一次
- `<句子編號>_放回時間格.wav`：有時間格時，照 `bookclub/fit.py` 補靜音或微調語速後、剛好等長的版本
- `生成/老師紀錄.json`：每句每次嘗試的種子、語速、長度、耗時、檢查結果

可以中斷續跑：紀錄裡已經有結果的句子直接跳過。
"""

from __future__ import annotations

import difflib
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from bookclub import workdir as wd

PROMPT_PREFIX = "You are a helpful assistant.<|endofprompt|>"
SPK_ID = "teacher"

SEEDS = [42, 1, 2026]          # 09-19 盲聽：42 最好；後兩個是同一輪測過的種子
MAX_ATTEMPTS = 3
CONTENT_MIN = 0.85             # 轉回文字跟原句的相似度門檻（0–1）
SPEED_MIN, SPEED_MAX = 0.85, 1.2  # 改語速重生成時的上下限，再多聽起來就不自然

GEN_DIR_NAME = "生成"


# ---------- 路徑 ----------

def gen_dir(workdir: Path) -> Path:
    return workdir / GEN_DIR_NAME


def teacher_out_dir(workdir: Path) -> Path:
    return gen_dir(workdir) / "老師"


def teacher_log_path(workdir: Path) -> Path:
    return gen_dir(workdir) / "老師紀錄.json"


# ---------- 文字處理（純函式） ----------

_s2t = None
_t2s = None


def to_simplified(text: str) -> str:
    global _t2s
    if _t2s is None:
        import opencc

        _t2s = opencc.OpenCC("t2s")
    return _t2s.convert(text)


def to_traditional(text: str) -> str:
    global _s2t
    if _s2t is None:
        import opencc

        _s2t = opencc.OpenCC("s2twp")
    return _s2t.convert(text)


def prompt_text(ref_text: str) -> str:
    """參考音逐字稿 → 餵給 CosyVoice 的格式：轉簡體、加固定開頭。"""
    return PROMPT_PREFIX + to_simplified(ref_text.strip())


def normalize_for_compare(text: str) -> str:
    """比對內容用：統一成簡體、英文小寫，拿掉標點與空白。"""
    text = to_simplified(unicodedata.normalize("NFKC", text)).lower()
    return "".join(ch for ch in text if ch.isalnum())


def content_score(expected: str, heard: str) -> float:
    """原句跟轉回來的文字有多像（0–1，字元層級）。"""
    a, b = normalize_for_compare(expected), normalize_for_compare(heard)
    if not a:
        return 1.0 if not b else 0.0
    return round(difflib.SequenceMatcher(None, a, b, autojunk=False).ratio(), 4)


# ---------- 句子清單 ----------

def load_sentences(path: str | Path) -> list[dict]:
    """讀句子清單。兩種格式：

    - `.json`：`[{"id": "T1", "text": "...", "slot": [開始秒, 結束秒]}, ...]`，
      `slot`（原片時間格）可以省略；也可以改給 `slot_s`（只給長度）
    - 其他（純文字）：一行一句，空行略過，編號自動給 `01`、`02`…，沒有時間格
    """
    import json

    path = Path(path).expanduser()
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        items = json.loads(raw)
    else:
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        items = [{"id": f"{i:02d}", "text": ln} for i, ln in enumerate(lines, 1)]

    out = []
    seen = set()
    for i, it in enumerate(items, 1):
        sid = str(it.get("id") or f"{i:02d}")
        if sid in seen:
            raise ValueError(f"句子編號重複：{sid}")
        seen.add(sid)
        if not re.fullmatch(r"[\w\-]+", sid):
            raise ValueError(f"句子編號只能用字母、數字、底線、減號（會拿來當檔名）：{sid!r}")
        text = str(it.get("text", "")).strip()
        if not text:
            raise ValueError(f"第 {sid} 句沒有文字")
        slot_s = it.get("slot_s")
        slot = it.get("slot")
        if slot is not None:
            slot_s = float(slot[1]) - float(slot[0])
        out.append({"id": sid, "text": text, "slot": slot, "slot_s": float(slot_s) if slot_s else None})
    return out


# ---------- 重試策略（純函式） ----------

@dataclass
class Attempt:
    seed: int
    speed: float
    audio_s: float
    elapsed_s: float
    heard: str | None       # 轉回來的文字；沒檢查就是 None
    content: float | None   # content_score；沒檢查就是 None
    similarity: float | None = None

    def content_ok(self) -> bool:
        return self.content is None or self.content >= CONTENT_MIN

    def length_ok(self, slot_s: float | None, tolerance: float) -> bool:
        return slot_s is None or abs(self.audio_s / slot_s - 1) <= tolerance

    def to_dict(self, n: int, slot_s: float | None, tolerance: float) -> dict:
        return {
            "第幾次": n, "種子": self.seed, "語速": self.speed,
            "長度秒": round(self.audio_s, 2), "耗時秒": round(self.elapsed_s, 1),
            "倍數": round(self.elapsed_s / self.audio_s, 1) if self.audio_s else None,
            "轉回文字": self.heard, "內容相似度": self.content,
            "內容通過": self.content_ok(),
            "長度通過": self.length_ok(slot_s, tolerance),
            "聲紋相似度": self.similarity,
        }


def next_attempt(history: list[Attempt], slot_s: float | None, tolerance: float) -> tuple[int, float] | None:
    """看前幾次的結果，決定下一次用什麼種子、語速；回傳 None 表示不用再試。

    - 還沒試過：種子 42、語速 1.0
    - 上一次內容不過：換下一個種子（語速回到 1.0）
    - 內容過、長度不過：同一個種子，語速改成讓長度接近時間格
    - 都過：不用再試
    """
    if not history:
        return SEEDS[0], 1.0
    if len(history) >= MAX_ATTEMPTS:
        return None
    last = history[-1]
    if not last.content_ok():
        used = {a.seed for a in history}
        for s in SEEDS:
            if s not in used:
                return s, 1.0
        return None
    if not last.length_ok(slot_s, tolerance):
        # speed > 1 念快一點；audio_s / slot_s 就是要快幾倍
        want = last.speed * last.audio_s / slot_s
        speed = round(min(max(want, SPEED_MIN), SPEED_MAX), 3)
        if abs(speed - last.speed) < 0.01:
            return None  # 已經頂到上下限，再試也一樣
        return last.seed, speed
    return None


def choose_best(history: list[Attempt], slot_s: float | None, tolerance: float) -> int:
    """挑最好的一次（回傳 index）：內容通過優先，其次長度通過，再來內容分數、長度差距。"""
    def key(i: int):
        a = history[i]
        length_gap = abs(a.audio_s / slot_s - 1) if slot_s else 0.0
        return (a.content_ok(), a.length_ok(slot_s, tolerance), a.content or 0.0, -length_gap)

    return max(range(len(history)), key=key)


# ---------- 真的會跑模型的部分 ----------

Synth = Callable[[str, int, float], tuple[np.ndarray, int]]
Hear = Callable[[Path], str]
Similar = Callable[[Path], float]


def make_cosyvoice_synth(ref_wav: Path, ref_text: str) -> Synth:
    """載入 CosyVoice3、把參考音存成說話者快取，回傳 `synth(文字, 種子, 語速)`。"""
    import torch

    from bookclub._cosyvoice_compat import load_cosyvoice

    model = load_cosyvoice()
    from cosyvoice.utils.common import set_all_random_seed

    model.add_zero_shot_spk(prompt_text(ref_text), str(ref_wav), SPK_ID)

    def synth(text: str, seed: int, speed: float) -> tuple[np.ndarray, int]:
        set_all_random_seed(seed)
        chunks = [
            out["tts_speech"]
            for out in model.inference_zero_shot(
                to_simplified(text), "", "", zero_shot_spk_id=SPK_ID, stream=False, speed=speed
            )
        ]
        wav = torch.cat(chunks, dim=1).squeeze(0).numpy()
        return wav, model.sample_rate

    return synth


def make_groq_hear() -> Hear | None:
    """回傳用 Groq 把聲音轉回文字的函式；沒有金鑰就回傳 None（內容檢查略過）。"""
    import os

    if not os.environ.get("GROQ_API_KEY"):
        return None
    from groq import Groq

    from bookclub.refpick import _groq_transcribe_bytes

    client = Groq()

    def hear(path: Path) -> str:
        r = _groq_transcribe_bytes(client, path.name, path.read_bytes())
        return to_traditional(r.get("text", "").strip())

    return hear


def make_similarity(workdir: Path) -> Similar | None:
    """回傳算「跟這支影片老師聲紋相似度」的函式；沒有 `說話者判斷.json` 就回傳 None。"""
    info = wd.read_json(wd.speakers_path(workdir))
    center = (info or {}).get("cluster_info", {}).get("老師聲紋中心")
    if not center:
        return None
    from bookclub.refpick import _load_embed_model, _teacher_ref_confirmation

    inference = _load_embed_model()

    def similar(path: Path) -> float:
        return _teacher_ref_confirmation(path, center, inference=inference)

    return similar


def _save_wav(path: Path, wav: np.ndarray, sr: int) -> None:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), wav, sr)


def generate_one(
    item: dict, out_dir: Path, synth: Synth, hear: Hear | None, similar: Similar | None,
    tolerance: float, log: Callable[[str], None] = print,
) -> dict:
    """生成一句：照 next_attempt 的策略最多試 MAX_ATTEMPTS 次，回傳這句的紀錄。"""
    from bookclub import fit

    sid, text, slot_s = item["id"], item["text"], item.get("slot_s")
    history: list[Attempt] = []
    while (nxt := next_attempt(history, slot_s, tolerance)) is not None:
        seed, speed = nxt
        n = len(history) + 1
        t = time.time()
        wav, sr = synth(text, seed, speed)
        elapsed = time.time() - t
        audio_s = len(wav) / sr
        path = out_dir / f"{sid}_第{n}次.wav"
        _save_wav(path, wav, sr)
        heard = hear(path) if hear else None
        att = Attempt(seed, speed, audio_s, elapsed, heard,
                      content_score(text, heard) if heard is not None else None)
        if similar:
            try:
                att.similarity = similar(path)
            except Exception as exc:  # 聲紋只是參考，算不出來不擋生成
                log(f"  ⚠️ 聲紋相似度算不出來：{exc}")
        history.append(att)
        parts = [f"  第 {n} 次：種子 {seed}、語速 {speed}，聲音 {audio_s:.1f} 秒，花 {elapsed:.0f} 秒（{elapsed / audio_s:.1f} 倍）"]
        if att.content is not None:
            parts.append(f"內容 {att.content:.2f}{'' if att.content_ok() else '（不過）'}")
        if slot_s:
            parts.append(f"長度差 {audio_s / slot_s - 1:+.0%}{'' if att.length_ok(slot_s, tolerance) else '（不過）'}")
        if att.similarity is not None:
            parts.append(f"聲紋 {att.similarity:.2f}")
        log("，".join(parts))

    best = choose_best(history, slot_s, tolerance)
    chosen = history[best]
    chosen_path = out_dir / f"{sid}.wav"
    chosen_path.write_bytes((out_dir / f"{sid}_第{best + 1}次.wav").read_bytes())

    record = {
        "id": sid, "text": text, "slot": item.get("slot"), "slot_s": slot_s,
        "嘗試": [a.to_dict(i + 1, slot_s, tolerance) for i, a in enumerate(history)],
        "選定": best + 1,
        "檔案": str(chosen_path.relative_to(out_dir.parent.parent)),
        "要人聽": not (chosen.content_ok() and chosen.length_ok(slot_s, tolerance)),
        "內容已檢查": chosen.content is not None,
    }
    if slot_s:
        plan = fit.plan_fit(chosen.audio_s, slot_s, "老師", tolerance)
        fitted = out_dir / f"{sid}_放回時間格.wav"
        fit.apply_fit(chosen_path, fitted, slot_s, plan)
        record["放回時間格"] = {**plan, "檔案": str(fitted.relative_to(out_dir.parent.parent))}
        if plan["做法"] == fit.FLAG:
            record["要人聽"] = True
    return record


def generate_teacher(
    workdir: str | Path, sentences_path: str | Path, *,
    ref_wav: str | Path | None = None, ref_text_path: str | Path | None = None,
    check_content: bool = True, check_similarity: bool = True, redo: bool = False,
    synth: Synth | None = None, hear: Hear | None = None, similar: Similar | None = None,
) -> dict:
    """流程第 5 步（一）的入口：`bookclub gen teacher`。回傳並存下 `生成/老師紀錄.json`。

    synth／hear／similar 可以從外面傳進來（測試用假的），不傳就載入真的模型。
    """
    from bookclub.config import load_settings

    workdir = wd.ensure(workdir)
    ref_wav = Path(ref_wav).expanduser() if ref_wav else wd.ref_dir(workdir) / "ref.wav"
    ref_text_path = Path(ref_text_path).expanduser() if ref_text_path else wd.ref_dir(workdir) / "ref.txt"
    for p in (ref_wav, ref_text_path):
        if not p.is_file():
            raise FileNotFoundError(
                f"找不到參考音：{p}\n→ 先用 `bookclub ref use <工作區> <名次> --text-file <逐字稿>` 選定參考音。"
            )
    ref_text = ref_text_path.read_text(encoding="utf-8").strip()
    items = load_sentences(sentences_path)
    tolerance = load_settings().thresholds.length_tolerance

    log_path = teacher_log_path(workdir)
    record = wd.read_json(log_path, default=None) or {}
    done = {} if redo else {r["id"]: r for r in record.get("句子", [])}
    same_ref = record.get("參考音") == str(ref_wav) and record.get("參考音逐字稿") == ref_text
    if done and not same_ref:
        print("參考音跟上次不同，全部重新生成。")
        done = {}

    todo = [it for it in items if it["id"] not in done or done[it["id"]]["text"] != it["text"]]
    print(f"[老師聲音] 共 {len(items)} 句，要生成 {len(todo)} 句（其他 {len(items) - len(todo)} 句沿用上次結果）")

    t0 = time.time()
    if todo:
        if synth is None:
            print("[老師聲音] 載入 CosyVoice3，並記住參考音的聲音特徵（約半分鐘到一分鐘）...")
            synth = make_cosyvoice_synth(ref_wav, ref_text)
        if hear is None and check_content:
            hear = make_groq_hear()
            if hear is None:
                print("⚠️ 沒有設定 GROQ_API_KEY，這次不檢查念得對不對，每句都要人聽。")
        if similar is None and check_similarity:
            similar = make_similarity(workdir)
    load_s = time.time() - t0

    out_dir = teacher_out_dir(workdir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for it in items:
        if it in todo:
            print(f"[老師聲音] 第 {it['id']} 句（{len(it['text'])} 字）")
            done[it["id"]] = generate_one(it, out_dir, synth, hear, similar, tolerance)
            _write_log(log_path, ref_wav, ref_text, items, done, load_s)
        results.append(done[it["id"]])

    summary = _write_log(log_path, ref_wav, ref_text, items, done, load_s)
    flagged = [r["id"] for r in results if r["要人聽"]]
    print(f"[老師聲音] 完成：{len(results)} 句，{len(flagged)} 句要人聽"
          + (f"（{'、'.join(flagged)}）" if flagged else "") + f"。紀錄：{log_path}")
    return summary


def _write_log(log_path: Path, ref_wav: Path, ref_text: str, items: list[dict], done: dict, load_s: float) -> dict:
    sentences = [done[it["id"]] for it in items if it["id"] in done]
    attempts = [a for r in sentences for a in r["嘗試"]]
    audio = sum(a["長度秒"] for a in attempts)
    spent = sum(a["耗時秒"] for a in attempts)
    data = {
        "參考音": str(ref_wav),
        "參考音逐字稿": ref_text,
        "句子": sentences,
        "統計": {
            "句數": len(sentences),
            "要人聽": sum(1 for r in sentences if r["要人聽"]),
            "生成次數": len(attempts),
            "生成總秒數": round(audio, 1),
            "生成總耗時秒": round(spent, 1),
            "平均倍數": round(spent / audio, 1) if audio else None,
            "載入模型秒": round(load_s, 1),
        },
    }
    wd.write_json(log_path, data)
    return data
