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
- `<句子編號>_<做法>_放回時間格.wav`：有時間格時，每種做法放回時間格的等長版本（插入停頓／補靜音／
  改語速重生成／拉長，見 `_finalize`），`<句子編號>_放回時間格.wav` 是建議的那個
- `<句子編號>_原聲.wav`：原片時間格那一段（插入停頓要對照）
- `生成/老師紀錄.json`：每句每次嘗試的種子、語速、長度、耗時、檢查結果

可以中斷續跑：紀錄裡已經有結果的句子直接跳過。
"""

from __future__ import annotations

import difflib
import re
import shutil
import subprocess
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
ATTEMPT_CACHE = "_嘗試快取.json"   # 每次生成完就記下來，中斷後重跑同一句同一次不重生成


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


# ---------- 發音對照表 ----------

PRON_TABLE_NAME = "發音對照表.csv"


def pron_table_path() -> Path:
    from bookclub.config import data_dir

    return data_dir() / PRON_TABLE_NAME


def load_pron_table(path: str | Path | None = None) -> list[tuple[str, str]]:
    """讀發音對照表（欄位：原字,生成用,原因,建立日期）。

    CosyVoice 的口音不完全是台灣口音，有些詞念起來會偏掉（「愉快」會念成
    「玉快」）。生成前把這些詞換成念起來比較接近台灣口音的寫法（「魚快」）。
    只影響送進模型念的文字；內容檢查、對位、標點判斷都還是用原本的文字。
    檔案不存在就是沒有對照表。長的詞先換，避免短詞先換掉長詞的一部分。
    """
    import csv

    path = Path(path).expanduser() if path else pron_table_path()
    if not path.is_file():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = [(r.get("原字", "").strip(), r.get("生成用", "").strip()) for r in csv.DictReader(f)]
    rows = [(a, b) for a, b in rows if a and b and a != b]
    return sorted(rows, key=lambda r: -len(r[0]))


def apply_pron(text: str, table: list[tuple[str, str]]) -> tuple[str, list[str]]:
    """回傳（送進模型念的文字、用到的對照，例如 ["愉快→魚快"]）。"""
    used = []
    for a, b in table:
        if a in text:
            text = text.replace(a, b)
            used.append(f"{a}→{b}")
    return text, used


# ---------- 句子清單 ----------

def load_sentences(path: str | Path) -> list[dict]:
    """讀句子清單。兩種格式：

    - `.json`：`[{"id": "T1", "text": "...", "slot": [開始秒, 結束秒]}, ...]`，
      `slot`（原片時間格）可以省略；也可以改給 `slot_s`（只給長度）。有給 `slot` 時
      `text` 可以省略，預設用原片那段的逐字稿（照原話念，含重複的字）
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
        text = str(it.get("text") or "").strip()
        slot_s = it.get("slot_s")
        slot = it.get("slot")
        if slot is not None:
            slot = [float(slot[0]), float(slot[1])]
            slot_s = slot[1] - slot[0]
        if not text and slot is None:
            raise ValueError(f"第 {sid} 句沒有文字（有給原片時間格 slot 才能省略，會改用原片那段的逐字稿）")
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
    paused_s: float | None = None   # 照原片停頓插入空白後的長度；沒做就是 None

    def content_ok(self) -> bool:
        return self.content is None or self.content >= CONTENT_MIN

    def lengths(self) -> list[float]:
        return [self.audio_s] + ([self.paused_s] if self.paused_s else [])

    def length_gap(self, slot_s: float | None) -> float:
        """原始或插入停頓後，跟時間格差距比較小的那個（比例）。"""
        return min(abs(x / slot_s - 1) for x in self.lengths()) if slot_s else 0.0

    def length_ok(self, slot_s: float | None, tolerance: float) -> bool:
        return slot_s is None or self.length_gap(slot_s) <= tolerance

    def to_dict(self, n: int, slot_s: float | None, tolerance: float) -> dict:
        return {
            "第幾次": n, "種子": self.seed, "語速": self.speed,
            "長度秒": round(self.audio_s, 2), "插入停頓後長度秒": round(self.paused_s, 2) if self.paused_s else None,
            "耗時秒": round(self.elapsed_s, 1),
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
        return (a.content_ok(), a.length_ok(slot_s, tolerance), a.content or 0.0, -a.length_gap(slot_s))

    return max(range(len(history)), key=key)


# ---------- 真的會跑模型的部分 ----------

Synth = Callable[[str, int, float], tuple[np.ndarray, int]]
Hear = Callable[[Path], str]
Similar = Callable[[Path], float]
Align = Callable[[Path, str], list]  # (聲音檔, 文字) → [pauses.Char, ...]


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


VARIANT_ORDER = ["插入停頓", "補靜音", "改語速重生成", "拉長"]  # 都能放進時間格時，建議的先後順序
STRETCH_MIN = 0.85  # 「拉長」最多放慢到 0.85 倍，再慢聲音會糊


def slot_text(merged: dict | None, start: float, end: float) -> str:
    """原片時間格裡的逐字稿：句子中點落在時間格內的都算，照順序接起來。"""
    if not merged:
        return ""
    parts = [s["text"] for s in merged.get("sentences", []) if start <= (s["start"] + s["end"]) / 2 <= end]
    return "".join(parts).strip()


def prepare_original(workdir: Path, item: dict, out_dir: Path, align: Align) -> dict | None:
    """切出原片那一段、對位、找出老師在哪幾個字後面停頓。沒有 audio.flac 或逐字稿就回傳 None。"""
    import soundfile as sf

    from bookclub import pauses

    audio = wd.audio_path(workdir)
    if not audio.is_file() or not item.get("原文") or not item.get("slot"):
        return None
    start, end = item["slot"]
    clip = out_dir / f"{item['id']}_原聲.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(audio), str(clip)],
        check=True,
    )
    chars = align(clip, item["原文"])
    x, sr = sf.read(str(clip))
    found = pauses.pauses_after_chars(chars, pauses.detect_silences(x, sr))
    return {"chars": chars, "pauses": found, "clip": clip}


def _paused_version(src: Path, text: str, ctx: dict, align: Align, dst: Path) -> tuple[float, list, float]:
    """照原片停頓在生成的聲音裡插入空白，回傳（新長度、插入點、開頭位移）。"""
    import soundfile as sf

    from bookclub import pauses

    x, sr = sf.read(str(src))
    gen_chars = align(src, text)
    # 只在標點的位置補空白（宇軒 09-25：照原片位置補會停在句子中間，不自然）
    inserts = pauses.plan_inserts(ctx["chars"], ctx["pauses"], gen_chars, x, sr, snap_text=text)
    lead = pauses.lead_offset(ctx["chars"], gen_chars)
    y = pauses.apply_inserts(x, sr, inserts, lead)
    _save_wav(dst, y, sr)
    return len(y) / sr, inserts, lead


def _run_attempt(
    item: dict, n: int, seed: int, speed: float, out_dir: Path,
    synth: Synth, hear: Hear | None, similar: Similar | None, log: Callable[[str], None],
) -> Attempt:
    """生成一次並做內容、聲紋檢查。"""
    sid, text, slot_s = item["id"], item["text"], item.get("slot_s")
    t = time.time()
    wav, sr = synth(item.get("生成用文字") or text, seed, speed)
    elapsed = time.time() - t
    from bookclub.pauses import trim_silence

    wav = trim_silence(wav, sr)  # 生成的聲音開頭常空 0.5 秒以上，先裁掉再比長度
    audio_s = len(wav) / sr
    path = out_dir / f"{sid}_第{n}次.wav"
    _save_wav(path, wav, sr)
    heard = hear(path) if hear else None
    att = Attempt(seed, speed, audio_s, elapsed, heard, content_score(text, heard) if heard is not None else None)
    if similar:
        try:
            att.similarity = similar(path)
        except Exception as exc:  # 聲紋只是參考，算不出來不擋生成
            log(f"  ⚠️ 聲紋相似度算不出來：{exc}")
    parts = [f"  第 {n} 次：種子 {seed}、語速 {speed}，聲音 {audio_s:.1f} 秒，花 {elapsed:.0f} 秒（{elapsed / audio_s:.1f} 倍）"]
    if att.content is not None:
        parts.append(f"內容 {att.content:.2f}{'' if att.content_ok() else '（不過）'}")
    if slot_s:
        parts.append(f"長度差 {audio_s / slot_s - 1:+.0%}")
    if att.similarity is not None:
        parts.append(f"聲紋 {att.similarity:.2f}")
    log("，".join(parts))
    return att


def _base_index(history: list[Attempt]) -> int:
    """候選做法的底：語速 1.0 的生成裡，內容通過、分數最高的一次。"""
    normal = [i for i, a in enumerate(history) if a.speed == 1.0] or list(range(len(history)))
    return max(normal, key=lambda i: (history[i].content_ok(), history[i].content or 0.0))


def _finalize(
    item: dict, out_dir: Path, history: list[Attempt], paused: dict | None, ctx: dict | None,
    tolerance: float, log: Callable[[str], None], role: str = "老師", tag: str = "老師聲音",
) -> dict:
    """挑選定的一次、把每種做法放回時間格、建議一種，回傳這句的紀錄。"""
    from bookclub import fit

    sid, text, slot_s = item["id"], item["text"], item.get("slot_s")
    workdir = out_dir.parent.parent
    best = choose_best(history, slot_s, tolerance)
    chosen = history[best]
    chosen_path = out_dir / f"{sid}.wav"
    shutil.copyfile(out_dir / f"{sid}_第{best + 1}次.wav", chosen_path)
    record = {
        "id": sid, "text": text, "生成用文字": item.get("生成用文字") or text,
        "發音對照": item.get("發音對照", []), "slot": item.get("slot"), "slot_s": slot_s,
        "嘗試": [a.to_dict(i + 1, slot_s, tolerance) for i, a in enumerate(history)],
        "選定": best + 1,
        "檔案": str(chosen_path.relative_to(workdir)),
        "要人聽": not chosen.content_ok(),
        "內容已檢查": chosen.content is not None,
    }
    if not slot_s:
        return record

    if ctx:
        record["原片停頓"] = [
            {"在這個字後面": ctx["chars"][i].text, "秒": d} for i, d in sorted(ctx["pauses"].items())
        ]
        record["原聲檔案"] = str(ctx["clip"].relative_to(workdir))

    base_i = _base_index(history)
    base = history[base_i]
    base_path = out_dir / f"{sid}_第{base_i + 1}次.wav"
    variants = []

    def add(name: str, src: Path, n: int, length: float) -> dict:
        # 「插入停頓」已經照原片對齊開頭；其他版本也要補上原片開口前的空白，不然會比原本早開口（09-29 實測差到 1.7 秒）
        lead = 0.0
        if ctx and name != "插入停頓":
            lead = start_offset(ctx["clip"], src)
            if lead >= 0.02:
                shifted = out_dir / f"{sid}_{name}_對齊開頭.wav"
                prepend_silence(src, shifted, lead)
                src, length = shifted, length + lead
            else:
                lead = 0.0
        plan = fit.plan_fit(length, slot_s, role, tolerance)
        dst = out_dir / f"{sid}_{name}_放回時間格.wav"
        fit.apply_fit(src, dst, slot_s, plan)
        v = {
            "版本": name, "來源第幾次": n, "長度秒": round(length, 2), "放回做法": plan["做法"],
            "差異比例": plan["差異比例"], "atempo": plan["atempo"], "原因": plan["原因"],
            "檔案": str(dst.relative_to(workdir)), "來源檔案": str(Path(src).relative_to(workdir)),
            "開頭對齊秒": round(lead, 3),
        }
        variants.append(v)
        return v

    if paused and base.paused_s:
        v = add("插入停頓", paused["檔案"], base_i + 1, base.paused_s)
        v["插入點"] = [{"秒": at, "補幾秒": add_s} for at, add_s in paused["插入"]]
        v["開頭位移秒"] = paused["開頭位移秒"]
    add("補靜音", base_path, base_i + 1, base.audio_s)
    respeed = [i for i, a in enumerate(history) if a.speed != 1.0 and a.content_ok()]
    if respeed:
        i = respeed[-1]
        add("改語速重生成", out_dir / f"{sid}_第{i + 1}次.wav", i + 1, history[i].audio_s)
    if base.audio_s < slot_s * (1 - tolerance):
        factor = max(STRETCH_MIN, base.audio_s / slot_s)
        stretched = out_dir / f"{sid}_拉長.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(base_path), "-af", f"atempo={factor:.4f}", str(stretched)],
            check=True,
        )
        add("拉長", stretched, base_i + 1, base.audio_s / factor)

    ok = [v for v in variants if v["放回做法"] != fit.FLAG]
    if ok:
        rec = min(ok, key=lambda v: VARIANT_ORDER.index(v["版本"]))
    else:
        rec = min(variants, key=lambda v: abs(v["差異比例"]))
        record["要人聽"] = True
    shutil.copyfile(workdir / rec["檔案"], out_dir / f"{sid}_放回時間格.wav")
    record["候選做法"] = variants
    record["建議做法"] = rec["版本"]
    record["放回時間格"] = {**rec, "檔案": str((out_dir / f"{sid}_放回時間格.wav").relative_to(workdir))}
    summary = "、".join("{} {:+.0%}".format(v["版本"], v["差異比例"]) for v in variants)
    log(f"[{tag}] 第 {sid} 句放回時間格：{summary} → 建議「{rec['版本']}」"
        + ("（都超過容許範圍，要人聽）" if not ok else ""))
    return record


def start_offset(original: Path, generated: Path) -> float:
    """原片開口時間 − 生成檔開口時間（秒，至少 0）：生成檔前面要補這麼多空白，開口才會跟原片對齊。"""
    import soundfile as sf

    from bookclub.pauses import speech_start

    a, sra = sf.read(str(original))
    b, srb = sf.read(str(generated))
    return max(0.0, speech_start(a, sra) - speech_start(b, srb))


def prepend_silence(src: Path, dst: Path, seconds: float) -> None:
    import soundfile as sf

    x, sr = sf.read(str(src))
    pad = np.zeros((int(round(seconds * sr)),) + x.shape[1:], dtype=x.dtype)
    _save_wav(dst, np.concatenate([pad, x]), sr)


def _free_memory() -> None:
    """呼叫前先把模型變數設成 None，這裡回收記憶體。8GB 的 Mac 放不下生成模型和對位模型同時在記憶體裡。"""
    import gc

    gc.collect()


def run_generation(
    workdir: Path, todo: list[dict], out_dir: Path, ref_wav: Path, ref_text: str, tolerance: float, *,
    save: Callable[[], object], done: dict, role: str = "老師", tag: str = "老師聲音",
    check_content: bool = True, check_similarity: bool = True, use_pauses: bool = True,
    synth: Synth | None = None, hear: Hear | None = None, similar: Similar | None = None,
    align: Align | None = None, log: Callable[[str], None] = print,
) -> float:
    """三階段生成（老師、學員共用）：todo 每一項要先準備好 id／text／生成用文字／發音對照／slot／slot_s／原文。
    每句做完就放進 done 並呼叫 save()（中斷續跑用）。回傳載入模型花的秒數。"""
    load_s = 0.0
    own_synth = synth is None
    own_align = align is None

    def load_synth() -> Synth:
        nonlocal load_s
        t = time.time()
        log(f"[{tag}] 載入 CosyVoice3，並記住參考音的聲音特徵（約半分鐘到一分鐘）...")
        s = make_cosyvoice_synth(ref_wav, ref_text)
        load_s += time.time() - t
        return s

    if hear is None and check_content:
        hear = make_groq_hear()
        if hear is None:
            log("⚠️ 沒有設定 GROQ_API_KEY，這次不檢查念得對不對，每句都要人聽。")
    if similar is None and check_similarity:
        similar = make_similarity(workdir)

    histories: dict[str, list[Attempt]] = {it["id"]: [] for it in todo}
    paused: dict[str, dict] = {}
    ctxs: dict[str, dict] = {}
    cache_path = out_dir / ATTEMPT_CACHE
    cache = wd.read_json(cache_path, default=None) or {}

    def attempt(it: dict, n: int, seed: int, speed: float) -> Attempt:
        """生成一次；同一句同一種子語速文字已經生成過（上次中斷），直接沿用檔案與檢查結果。"""
        key = f"{it['id']}|{n}|{seed}|{speed}|{it.get('生成用文字') or it['text']}|{ref_wav}"
        hit = cache.get(key)
        if hit and (out_dir / f"{it['id']}_第{n}次.wav").is_file():
            log(f"  第 {n} 次：沿用上次生成的檔案")
            return Attempt(**hit)
        nonlocal synth
        if synth is None:
            synth = load_synth()
        att = _run_attempt(it, n, seed, speed, out_dir, synth, hear, similar, log)
        cache[key] = att.__dict__.copy()
        wd.write_json(cache_path, cache)
        return att

    # 階段一：生成到內容通過（長度先不管，下一階段插入停頓可能就過了）
    for it in todo:
        log(f"[{tag}] 第 {it['id']} 句（{len(it['text'])} 字）"
            + (f"，發音對照：{'、'.join(it['發音對照'])}" if it["發音對照"] else ""))
        h = histories[it["id"]]
        while (nxt := next_attempt(h, None, tolerance)) is not None:
            h.append(attempt(it, len(h) + 1, *nxt))

    # 階段二：插入停頓
    slotted = [it for it in todo if it["slot"] and it.get("原文")]
    if use_pauses and slotted and (align is not None or wd.audio_path(workdir).is_file()):
        if own_synth:
            synth = None
            _free_memory()
        if align is None:
            t = time.time()
            log(f"[{tag}] 載入逐字對位模型，照原片停頓插入空白...")
            from bookclub.pauses import Aligner

            align = Aligner().align
            load_s += time.time() - t
        for it in slotted:
            sid = it["id"]
            try:
                ctx = prepare_original(workdir, it, out_dir, align)
            except Exception as exc:
                log(f"  ⚠️ 第 {sid} 句原片停頓分析失敗，不做插入停頓：{exc}")
                continue
            if not ctx:
                continue
            ctxs[sid] = ctx
            h = histories[sid]
            bi = _base_index(h)
            if not h[bi].content_ok():
                continue
            try:
                dst = out_dir / f"{sid}_第{bi + 1}次_插入停頓.wav"
                h[bi].paused_s, inserts, lead = _paused_version(
                    out_dir / f"{sid}_第{bi + 1}次.wav", it["text"], ctx, align, dst)
                paused[sid] = {"檔案": dst, "插入": inserts, "開頭位移秒": lead}
                log(f"  第 {sid} 句：原片 {len(ctx['pauses'])} 個停頓，插入 {len(inserts)} 段空白，"
                    f"長度 {h[bi].audio_s:.1f} → {h[bi].paused_s:.1f} 秒（時間格 {it['slot_s']:.1f} 秒）")
            except Exception as exc:
                log(f"  ⚠️ 第 {sid} 句插入停頓失敗：{exc}")
        if own_align:
            align = None
            _free_memory()

    # 階段三：長度還是不過的才改語速重生成
    retry = [it for it in todo if it["slot_s"]
             and next_attempt(histories[it["id"]], it["slot_s"], tolerance) is not None]
    if retry:
        for it in retry:
            log(f"[{tag}] 第 {it['id']} 句長度差太多，改語速重生成")
            h = histories[it["id"]]
            while (nxt := next_attempt(h, it["slot_s"], tolerance)) is not None:
                h.append(attempt(it, len(h) + 1, *nxt))

    for it in todo:
        done[it["id"]] = _finalize(it, out_dir, histories[it["id"]], paused.get(it["id"]),
                                   ctxs.get(it["id"]), tolerance, log, role=role, tag=tag)
        save()
    return load_s


def generate_teacher(
    workdir: str | Path, sentences_path: str | Path, *,
    ref_wav: str | Path | None = None, ref_text_path: str | Path | None = None,
    check_content: bool = True, check_similarity: bool = True, use_pauses: bool = True, redo: bool = False,
    pron_table: str | Path | None = None,
    synth: Synth | None = None, hear: Hear | None = None, similar: Similar | None = None,
    align: Align | None = None, log: Callable[[str], None] = print,
) -> dict:
    """流程第 5 步（一）的入口：`bookclub gen teacher`。回傳並存下 `生成/老師紀錄.json`。

    分三個階段跑，同一時間只載入一個大模型（8GB 的 Mac 兩個一起放會一直搬
    swap，慢好幾倍）：

    1. 生成模型：每句生成到內容檢查通過（念錯才換種子）
    2. 對位模型：有時間格的句子，照原片停頓插入空白
    3. 生成模型：原始跟插入停頓後長度都不過的句子，才改語速重生成

    synth／hear／similar／align 可以從外面傳進來（測試用假的），不傳就載入真的模型。
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

    merged = wd.read_json(wd.merged_transcript_path(workdir))
    for it in items:
        if it["slot"] and merged:
            it["原文"] = slot_text(merged, *it["slot"])
        elif it["slot"] and it["text"]:
            # 匯入的工作區沒有逐字稿（不帶學員本名出去）：插入停頓的對位改用代號版的句子，名字那幾個字對不準，其他字照常
            it["原文"] = it["text"]
        if not it["text"]:
            if not it.get("原文"):
                raise ValueError(f"第 {it['id']} 句沒有文字，工作區裡也找不到原片那段的逐字稿（transcript/merged.json）")
            it["text"] = it["原文"]
    if pron_table is None and (workdir / PRON_TABLE_NAME).is_file():
        pron_table = workdir / PRON_TABLE_NAME     # 匯入的工作區：用匯出時一起帶過來的那份
    table = load_pron_table(pron_table)
    for it in items:
        it["生成用文字"], it["發音對照"] = apply_pron(it["text"], table)

    log_path = teacher_log_path(workdir)
    record = wd.read_json(log_path, default=None) or {}
    done = {} if redo else {r["id"]: r for r in record.get("句子", [])}
    same_ref = record.get("參考音") == str(ref_wav) and record.get("參考音逐字稿") == ref_text
    if done and not same_ref:
        log("參考音跟上次不同，全部重新生成。")
        done = {}

    todo = [it for it in items if it["id"] not in done or done[it["id"]]["text"] != it["text"]
            or done[it["id"]].get("生成用文字", it["text"]) != it["生成用文字"]]
    log(f"[老師聲音] 共 {len(items)} 句，要生成 {len(todo)} 句（其他 {len(items) - len(todo)} 句沿用上次結果）")
    out_dir = teacher_out_dir(workdir)
    out_dir.mkdir(parents=True, exist_ok=True)
    load_s = 0.0

    if todo:
        load_s = run_generation(
            workdir, todo, out_dir, ref_wav, ref_text, tolerance,
            save=lambda: _write_log(log_path, ref_wav, ref_text, items, done, load_s), done=done,
            check_content=check_content, check_similarity=check_similarity, use_pauses=use_pauses,
            synth=synth, hear=hear, similar=similar, align=align, log=log)

    results = [done[it["id"]] for it in items]
    summary = _write_log(log_path, ref_wav, ref_text, items, done, load_s)
    flagged = [r["id"] for r in results if r["要人聽"]]
    log(f"[老師聲音] 完成：{len(results)} 句，{len(flagged)} 句要人聽"
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
