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
import functools
import hashlib
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
# 10-02 第四批：第 5 步退回重做、文字和範圍都沒改的句子，重新生成時避開用過的種子（換一種念法）；
# 每一句以前的版本也記在這裡（`finalcheck.prepare_redo` 寫、`run_generation` 讀）。格式見 `redo_avoid`。
REDO_VERSIONS = "_重新生成版本.json"


# ---------- 路徑 ----------

def gen_dir(workdir: Path) -> Path:
    return workdir / GEN_DIR_NAME


def teacher_out_dir(workdir: Path) -> Path:
    return gen_dir(workdir) / "老師"


def teacher_log_path(workdir: Path) -> Path:
    return gen_dir(workdir) / "老師紀錄.json"


STOP_FLAG = "_停止執行"   # 09-30：網頁第 4 步按「停止」時放這個檔，生成完目前這一次就停


class StopRequested(Exception):
    """網頁第 4 步按了「停止」：停在目前這一次生成之後（每一次都記在 `_嘗試快取.json`，下次接著做）。"""


def stop_flag_path(workdir: Path) -> Path:
    return gen_dir(Path(workdir)) / STOP_FLAG


def check_stop(workdir: Path) -> None:
    if stop_flag_path(workdir).exists():
        raise StopRequested("按了停止：停在目前這一句生成完之後，下次按「開始執行」會接著做")


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
    from bookclub import csvfile

    path = Path(path).expanduser() if path else pron_table_path()
    if not path.is_file():
        return []
    # 10-03 第九批 #35：Excel 另存的 Big5 也讀得了（見 csvfile）
    rows = [((r.get("原字") or "").strip(), (r.get("生成用") or "").strip()) for r in csvfile.read_rows(path)]
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
    check_failed: bool = False      # 09-30：要檢查內容但沒做成（網路、Groq 有問題）→ 這一句標要人聽

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
            "內容通過": self.content_ok(), "內容檢查沒做成": self.check_failed,
            "長度通過": self.length_ok(slot_s, tolerance),
            "聲紋相似度": self.similarity,
        }


def seed_order(avoid=()) -> list[int]:
    """依序要試的種子：SEEDS；10-02 第四批：有要避開的（退回重做換一種念法）而 SEEDS 不夠用時，
    接在最後一個後面一個一個往上加（2027、2028⋯），每一次退回都還有沒用過的。"""
    avoid = set(avoid or ())
    if not avoid:
        return list(SEEDS)
    out, nxt = list(SEEDS), SEEDS[-1] + 1
    while len([x for x in out if x not in avoid]) < MAX_ATTEMPTS:
        out.append(nxt)
        nxt += 1
    return out


def next_attempt(history: list[Attempt], slot_s: float | None, tolerance: float,
                 avoid=()) -> tuple[int, float] | None:
    """看前幾次的結果，決定下一次用什麼種子、語速；回傳 None 表示不用再試。

    - 還沒試過：種子 42、語速 1.0
    - 上一次內容不過：換下一個種子（語速回到 1.0）
    - 內容過、長度不過：同一個種子，語速改成讓長度接近時間格
    - 都過：不用再試
    avoid（10-02 第四批）：退回重做、文字和範圍都沒改的句子，以前版本用過的種子；照同樣的順序跳過這幾個
    （第一次就從還沒用過的下一個開始）。
    """
    order = seed_order(avoid)
    skip = set(avoid or ())
    if not history:
        return next(x for x in order if x not in skip), 1.0
    if len(history) >= MAX_ATTEMPTS:
        return None
    last = history[-1]
    if not last.content_ok():
        used = {a.seed for a in history} | skip
        for s in order:
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
    wd.unlink_if_link(path)   # 10-02 第五批：不順著連結寫回原本的工作區
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
    wd.unlink_if_link(clip)
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
    heard, check_failed = None, False
    if hear:
        try:
            heard = hear(path)
        except Exception as exc:  # noqa: BLE001 — 重試完還是連不上：不要讓整晚的生成停在這裡，這一句標要人聽
            check_failed = True
            log(f"  ⚠️ 念對沒有的檢查沒做成（{type(exc).__name__}，多半是網路），這一句先標要人聽，生成照常往下")
    att = Attempt(seed, speed, audio_s, elapsed, heard, content_score(text, heard) if heard is not None else None,
                  check_failed=check_failed)
    if similar:
        try:
            att.similarity = similar(path)
        except Exception as exc:  # 聲紋只是參考，算不出來不擋生成
            log(f"  ⚠️ 像不像老師聲音算不出來（只記錄用，不影響）：{type(exc).__name__}")
    log(attempt_line(n, seed, speed, audio_s, elapsed, att.content, att.content_ok(), slot_s, att.similarity))
    return att


def way_number(seed: int) -> int:
    """種子 → 畫面上的「第幾種念法」（照 seed_order 的順序：42 是第 1 種、1 是第 2 種、2026 是第 3 種，之後往上加）。"""
    if seed in SEEDS:
        return SEEDS.index(seed) + 1
    if seed > SEEDS[-1]:
        return len(SEEDS) + seed - SEEDS[-1]
    return len(SEEDS) + 1


def attempt_line(n: int, seed: int, speed: float, audio_s: float, elapsed: float, content: float | None,
                 content_ok: bool, slot_s: float | None, similarity: float | None) -> str:
    """第 4 步執行訊息裡每一次生成的那一行（10-02 第六批：使用者看得到，「種子」「內容分數」「長度差」改成白話；
    紀錄檔的欄位不變）。例：「第 2 次生成：第 2 種念法、正常速度，聲音 2.9 秒（花 41 秒）；念的字對了 92%；比原本的時間短 61%」"""
    pace = "正常速度" if abs(speed - 1.0) < 1e-6 else (f"念快一點（{speed:.2f} 倍）" if speed > 1 else f"念慢一點（{speed:.2f} 倍）")
    parts = [f"  第 {n} 次生成：第 {way_number(seed)} 種念法、{pace}，聲音 {audio_s:.1f} 秒（花 {elapsed:.0f} 秒）"]
    if content is not None:
        parts.append(f"念的字對了 {content:.0%}" + ("" if content_ok else f"（不到 {CONTENT_MIN:.0%}，換一種念法再試）"))
    if slot_s:
        d = audio_s / slot_s - 1
        parts.append("跟原本的時間差不多" if abs(d) < 0.005 else f"比原本的時間{'長' if d > 0 else '短'} {abs(d):.0%}")
    if similarity is not None:
        parts.append(f"像老師聲音的程度 {similarity:.2f}（只記錄，不影響）")
    return "；".join(parts)


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
    wd.unlink_if_link(chosen_path)
    shutil.copyfile(out_dir / f"{sid}_第{best + 1}次.wav", chosen_path)
    record = {
        "id": sid, "text": text, "生成用文字": item.get("生成用文字") or text,
        "發音對照": item.get("發音對照", []), "slot": item.get("slot"), "slot_s": slot_s,
        "嘗試": [a.to_dict(i + 1, slot_s, tolerance) for i, a in enumerate(history)],
        "選定": best + 1,
        "檔案": str(chosen_path.relative_to(workdir)),
        "要人聽": not chosen.content_ok() or chosen.check_failed,
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
        wd.unlink_if_link(stretched)
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
    wd.unlink_if_link(out_dir / f"{sid}_放回時間格.wav")
    shutil.copyfile(workdir / rec["檔案"], out_dir / f"{sid}_放回時間格.wav")
    record["候選做法"] = variants
    record["建議做法"] = rec["版本"]
    record["放回時間格"] = {**rec, "檔案": str((out_dir / f"{sid}_放回時間格.wav").relative_to(workdir))}
    # 10-02 第六批：白話（「+12%」→「長 12%」）
    summary = "、".join(f"{v['版本']}（{'跟原本差不多' if abs(v['差異比例']) < 0.005 else ('長' if v['差異比例'] > 0 else '短') + format(abs(v['差異比例']), '.0%')}）"
                        for v in variants)
    log(f"[{tag}] 第 {sid} 句放回原本的時間：{summary} → 用「{rec['版本']}」"
        + ("（每一種都差太多，標成要人聽）" if not ok else ""))
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


def ref_fingerprint(path: str | Path) -> str | None:
    """參考音檔內容的指紋（09-29）：重挑參考音常寫到同一個檔名，只比路徑會沿用舊聲音生成的結果。"""
    p = Path(path)
    if not p.is_file():
        return None
    st = p.stat()
    return _fingerprint(str(p), st.st_mtime_ns, st.st_size)   # 第 4 步網頁每幾秒問一次，檔案沒變就不重算


@functools.lru_cache(maxsize=64)
def _fingerprint(path: str, _mtime_ns: int, _size: int) -> str:
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()[:12]


def file_fingerprint(path: str | Path) -> str | None:
    """生成檔內容的指紋（10-03 第九批 #21）：每次都重算（不靠修改時間，同一秒內覆寫也分得出來）。"""
    p = Path(path)
    return hashlib.sha1(p.read_bytes()).hexdigest()[:12] if p.is_file() else None


def text_fingerprint(text: str) -> str:
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()[:12]


ATTEMPT_FIELDS = frozenset(Attempt.__dataclass_fields__)


def same_ref_file(rec: dict, ref_wav: str | Path) -> bool:
    """紀錄裡的參考音跟現在的是不是同一個。紀錄有指紋就只比內容（10-01：工作區複製到別的資料夾，
    路徑不同但內容一樣，不該整批重新生成）；沒有指紋的舊紀錄才比路徑。"""
    fp = rec.get("參考音指紋")
    if fp:
        return fp == ref_fingerprint(ref_wav)
    # 10-02 第五批：沒有指紋的舊紀錄，路徑在工作區裡的位置一樣（例如都是 參考音/ref.wav，工作區被複製或搬了）也算同一個
    return wd.same_stored_file(rec.get("參考音"), ref_wav)


_LEGACY_REF_RE = re.compile(r"\|(/[^|]*)#([0-9a-f]{12}|None)(?=\||$)")


def normalize_cache_key(key: str) -> str:
    """10-02 第五批：10-01 以前的快取鍵裡參考音那一段是「完整路徑#指紋」，換成新寫法「參考音#指紋」。
    工作區被複製、搬家之後，舊鍵裡的路徑是原本的位置；只看指紋（內容一樣）就沿用，不用重新生成。"""
    return _LEGACY_REF_RE.sub(lambda m: f"|參考音#{m.group(2)}", key)


def ref_key(ref_wav: str | Path, ref_fp: str | None = None, legacy: bool = False) -> str:
    """生成快取鍵裡的參考音部分。10-01 起有指紋只記指紋（複製到別處照樣沿用）；
    legacy=True 是 10-01 以前的寫法（路徑#指紋），讀舊快取用。"""
    fp = ref_fingerprint(ref_wav) if ref_fp is None else ref_fp
    if legacy or not fp:
        return f"{ref_wav}#{fp}"
    return f"參考音#{fp}"


def teacher_ref_changed(workdir: str | Path, tlog: dict | None) -> bool:
    """老師參考音（ref.wav／ref.txt）跟生成紀錄記的不一樣（10-01：第 4 步「做過沒有」和逐類統計共用，
    才不會一邊寫 24／25、一邊寫 25 句都要重新生成）。還沒有紀錄或還沒選參考音就不算換過。"""
    tlog = tlog or {}
    rd = Path(workdir) / "參考音"
    ref_wav, ref_txt = rd / "ref.wav", rd / "ref.txt"
    if not (tlog.get("句子") and tlog.get("參考音") and ref_wav.is_file() and ref_txt.is_file()):
        return False
    return not (same_ref_file(tlog, ref_wav) and tlog.get("參考音逐字稿") == ref_txt.read_text(encoding="utf-8").strip())


SLOT_TOLERANCE_S = 0.05


def record_stale(rec: dict | None, it: dict, ref_wav: str | Path | None = None) -> bool:
    """這一句上次的生成結果還能不能沿用（09-29 檢查 #7）。生成程式和第 4 步「做過沒有」共用這一個判斷，
    兩邊才不會一個說做過了、一個說要重做。

    要重做：沒有紀錄、要念的文字改了、發音對照表改了（生成用文字不同）、時間格改了（改起訖、加了刪除段落）、
    給了 ref_wav 而參考音換了（學員那邊每一句各記參考音；老師那邊整份紀錄記一個，另外比）。"""
    if not rec:
        return True
    if rec.get("text") != it.get("text"):
        return True
    if "生成用文字" in it and rec.get("生成用文字", rec.get("text")) != it["生成用文字"]:
        return True
    old, new = rec.get("slot"), it.get("slot")
    if old and new and (abs(old[0] - new[0]) > SLOT_TOLERANCE_S or abs(old[1] - new[1]) > SLOT_TOLERANCE_S):
        return True
    return ref_wav is not None and not same_ref_file(rec, ref_wav)


PHASES = ("生成", "停頓", "收尾")   # 10-01：第 4 步每一段各自一支程式跑（見 run_generation 的 phase）
PAUSE_CACHE = "_停頓快取.json"     # 10-01：插入停頓那一支程式的結果，收尾那一支讀（不用再載入對位模型）


class NotGeneratedYet(RuntimeError):
    """10-01：插入停頓／收尾那一支程式發現有句子還沒生成過（生成那一支沒做完），不在這裡載入生成模型。"""


def lazy_aligner(log: Callable[[str], None] = print, tag: str = "插入停頓") -> Align:
    """10-01：要用到才載入逐字對位模型；同一支程式裡好幾組（好幾個聲線）共用一個，只載入一次。"""
    holder: dict = {}

    def align(path, text: str) -> list:
        if "align" not in holder:
            log(f"[{tag}] 載入逐字對位模型，照原片停頓插入空白...")
            from bookclub.pauses import Aligner

            holder["align"] = Aligner().align
        return holder["align"](path, text)

    return align


def _ctx_to_json(ctx: dict) -> dict:
    return {"chars": [[c.text, c.start, c.end] for c in ctx["chars"]],
            "pauses": [[i, d] for i, d in sorted(ctx["pauses"].items())], "clip": Path(ctx["clip"]).name}


def _ctx_from_json(data: dict, out_dir: Path) -> dict:
    from bookclub.pauses import Char

    return {"chars": [Char(t, float(a), float(b)) for t, a, b in data["chars"]],
            "pauses": {int(i): float(d) for i, d in data["pauses"]}, "clip": out_dir / data["clip"]}


def redo_avoid(entry: dict | None, it: dict) -> list[int]:
    """這一句重新生成時要避開的種子（純函式）。entry 是 `REDO_VERSIONS` 裡這一句的那一筆：
    {text, 生成用文字, slot, 避開: [種子], 以前的版本: [{第幾版, 種子, 試過的種子, 嘗試, 選定, 備份資料夾, 重做時間, 退回原因}]}。
    要念的字、生成用文字、時間格跟上一版一樣才避開（換一種念法）；有改的照原本的規則（從種子 42 開始）。"""
    if not entry or not entry.get("避開") or not same_content(entry, it):
        return []
    return [int(x) for x in entry["避開"]]


def same_content(a: dict, b: dict) -> bool:
    """兩邊要念的字、生成用文字、時間格是不是一樣（時間格前後差 0.05 秒以內）（純函式）。"""
    if (a.get("text") or "") != (b.get("text") or ""):
        return False
    if (a.get("生成用文字") or a.get("text") or "") != (b.get("生成用文字") or b.get("text") or ""):
        return False
    old, new = a.get("slot"), b.get("slot")
    if bool(old) != bool(new):
        return False
    return not (old and new and (abs(old[0] - new[0]) > SLOT_TOLERANCE_S or abs(old[1] - new[1]) > SLOT_TOLERANCE_S))


def run_generation(
    workdir: Path, todo: list[dict], out_dir: Path, ref_wav: Path, ref_text: str, tolerance: float, *,
    save: Callable[[], object], done: dict, role: str = "老師", tag: str = "老師聲音",
    check_content: bool = True, check_similarity: bool = True, use_pauses: bool = True,
    synth: Synth | None = None, hear: Hear | None = None, similar: Similar | None = None,
    align: Align | None = None, phase: str | None = None, fresh_pauses: bool = False,
    log: Callable[[str], None] = print,
) -> float:
    """三階段生成（老師、學員共用）：todo 每一項要先準備好 id／text／生成用文字／發音對照／slot／slot_s／原文。
    每句做完就放進 done 並呼叫 save()（中斷續跑用）。回傳載入模型花的秒數。

    phase（10-01，第 4 步分開程式跑用；None＝三個階段在同一支程式裡跑完，跟以前一樣）：
    - "生成"：只做階段一（只載入生成模型），做完就結束；結果都在 `_嘗試快取.json`
    - "停頓"：階段一全部從快取沿用（不載入生成模型，快取裡沒有就停下來報錯），只做階段二（只載入對位模型），
      結果記在 `_停頓快取.json`；fresh_pauses＝已經記過的也重做
    - "收尾"：階段一從快取沿用、階段二讀 `_停頓快取.json`，做階段三（要改語速重生成才載入生成模型）與放回時間格
    """
    if phase not in (None, *PHASES):
        raise ValueError(f"phase 只能是 {PHASES} 或不給：{phase}")
    load_s = 0.0
    own_synth = synth is None
    own_align = align is None
    replay = phase in ("停頓", "收尾")   # 階段一只能從快取沿用
    allow_new = not replay
    say = (lambda s: None) if replay else log   # 從快取沿用的那幾行不再印一遍

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
    want_similar = similar is None and check_similarity and phase != "停頓"   # 10-01：要生成時才載入

    histories: dict[str, list[Attempt]] = {it["id"]: [] for it in todo}
    versions = wd.read_json(out_dir / REDO_VERSIONS, default=None) or {}   # 10-02 第四批：退回重做換一種念法
    avoid = {it["id"]: redo_avoid(versions.get(it["id"]), it) for it in todo}
    paused: dict[str, dict] = {}
    ctxs: dict[str, dict] = {}
    cache_path = out_dir / ATTEMPT_CACHE
    cache = wd.read_json(cache_path, default=None) or {}
    ref_fp = ref_fingerprint(ref_wav)
    ref_text_fp = text_fingerprint(ref_text)

    def akey(it: dict, n: int, seed: int, speed: float, legacy: bool = False) -> str:
        return f"{it['id']}|{n}|{seed}|{speed}|{it.get('生成用文字') or it['text']}|{ref_key(ref_wav, ref_fp, legacy)}"

    legacy_index: dict[str, str] = {}   # 10-02 第五批：舊寫法的鍵（路徑可能是複製前的工作區）→ 換成新寫法後的鍵
    for k in cache:
        nk = normalize_cache_key(k)
        if nk != k:
            legacy_index.setdefault(nk, k)

    def cache_hit(key_new: str, key_old: str) -> tuple[str, dict | None]:
        """10-01：先找新寫法的鍵，找不到再找舊寫法（路徑#指紋）；找到舊的就搬成新鍵。
        10-02 第五批：舊寫法的路徑是別的位置（工作區被複製、搬家）也算，只看指紋。"""
        if key_new in cache:
            return key_new, cache[key_new]
        old = key_old if key_old != key_new and key_old in cache else legacy_index.get(key_new)
        if old is not None and old in cache:
            cache[key_new] = cache[old]
            return key_new, cache[key_new]
        return key_new, None

    def save_entry(key: str, att: Attempt, wav: Path) -> None:
        """記進 `_嘗試快取.json`，連同聲音檔內容和參考音逐字稿的指紋（10-03 第九批 #21：沿用前核對）。"""
        cache[key] = {**att.__dict__, "檔案指紋": file_fingerprint(wav), "參考音逐字稿指紋": ref_text_fp}
        wd.write_json(cache_path, cache)

    def hit_usable(it: dict, n: int, hit: dict, wav: Path) -> bool:
        """10-03 第九批 #21：`<句子>_第<N>次.wav` 是同一個檔名，文字 A→B→A、聲線 男1→男2→男1 時，
        A／男1 的快取還在，但檔案已經被 B／男2 蓋掉了。沿用前核對檔案內容是不是當時記下的那一個。"""
        if not wav.is_file():
            return False
        if hit.get("參考音逐字稿指紋") and hit["參考音逐字稿指紋"] != ref_text_fp:
            return False   # 參考音檔一樣、逐字稿改了
        fp = hit.get("檔案指紋")
        if fp:
            return fp == file_fingerprint(wav)
        # 舊快取沒記指紋：這一句這一次只生成過一種（文字、聲線、參考音都一樣）才沿用；有過別種，檔案可能被蓋掉，重新生成
        prefix = f"{it['id']}|{n}|"
        return len({normalize_cache_key(k) for k in cache if k.startswith(prefix)}) <= 1

    def attempt(it: dict, n: int, seed: int, speed: float) -> Attempt:
        """生成一次；同一句同一種子語速文字已經生成過（上次中斷），直接沿用檔案與檢查結果。"""
        nonlocal synth, similar, want_similar
        key, hit = cache_hit(akey(it, n, seed, speed), akey(it, n, seed, speed, legacy=True))
        wav = out_dir / f"{it['id']}_第{n}次.wav"
        if hit and not hit_usable(it, n, hit, wav):
            if wav.is_file():
                say(f"  第 {n} 次生成：上次的聲音檔已經被別的文字或聲線蓋掉了，重新生成")
            hit = None
        if hit:
            say(f"  第 {n} 次生成：沿用上次生成好的聲音")
            att = Attempt(**{k: v for k, v in hit.items() if k in ATTEMPT_FIELDS})
            if att.check_failed and hear:   # 上次內容檢查沒做成（網路）：聲音不用重新生成，補檢查就好
                try:
                    att.heard = hear(out_dir / f"{it['id']}_第{n}次.wav")
                    att.content = content_score(it["text"], att.heard)
                    att.check_failed = False
                    save_entry(key, att, wav)
                    log(f"  第 {n} 次生成：補做念對沒有的檢查，念的字對了 {att.content:.0%}")
                except Exception as exc:  # noqa: BLE001
                    log(f"  ⚠️ 補做念對沒有的檢查還是沒成（{type(exc).__name__}），維持要人聽")
            return att
        if not allow_new:
            raise NotGeneratedYet(f"[{tag}] 第 {it['id']} 句第 {n} 次還沒生成（生成那一支程式沒做完），"
                                  "這一支不載入生成模型；再按一次「開始執行」會從生成接著做")
        check_stop(workdir)   # 09-30：按了停止就不再開始新的生成（已經生成的都在快取裡）
        if synth is None:
            synth = load_synth()
        if want_similar:
            want_similar = False
            similar = make_similarity(workdir)
        att = _run_attempt(it, n, seed, speed, out_dir, synth, hear, similar, log)
        save_entry(key, att, wav)
        return att

    # 階段一：生成到內容通過（長度先不管，下一階段插入停頓可能就過了）
    for it in todo:
        say(f"[{tag}] 第 {it['id']} 句（{len(it['text'])} 字）"
            + (f"，發音對照：{'、'.join(it['發音對照'])}" if it["發音對照"] else ""))
        h = histories[it["id"]]
        if avoid[it["id"]] and not h:
            say(f"  第 {len(versions[it['id']].get('以前的版本') or []) + 1} 版：上一版退回重做、文字和範圍沒改，換一種念法重新生成")
        while (nxt := next_attempt(h, None, tolerance, avoid[it["id"]])) is not None:
            h.append(attempt(it, len(h) + 1, *nxt))
    if phase == "生成":
        log(f"[{tag}] 這一支程式只生成：{len(todo)} 句做完；插入停頓、放回時間格交給下一支程式")
        return load_s

    # 階段二：插入停頓
    slotted = [it for it in todo if it["slot"] and it.get("原文")]
    pcache_path = out_dir / PAUSE_CACHE
    pcache = (wd.read_json(pcache_path, default=None) or {}) if phase else {}

    def pkey(it: dict, bi: int, legacy: bool = False) -> str:
        a = histories[it["id"]][bi]
        return f"{akey(it, bi + 1, a.seed, a.speed, legacy)}|{it.get('原文')}|{it['slot']}"

    def pkey_ok(ent: dict | None, it: dict, bi: int) -> bool:
        """停頓快取對不對得上（10-01：舊寫法的鍵也算）。10-03 第九批 #21：有記來源聲音檔的指紋就核對
        （那個檔被別的文字、聲線重新生成過，插入停頓要重做）。"""
        if not ent or not (ent.get("鍵") in (pkey(it, bi), pkey(it, bi, legacy=True))
                           or normalize_cache_key(ent.get("鍵") or "") == pkey(it, bi)):   # 10-02 第五批：複製來的工作區
            return False
        fp = ent.get("來源指紋")
        return not fp or fp == file_fingerprint(out_dir / f"{it['id']}_第{bi + 1}次.wav")

    def use_entry(it: dict, ent: dict) -> None:
        sid = it["id"]
        h = histories[sid]
        bi = _base_index(h)
        if ent.get("ctx"):
            ctxs[sid] = _ctx_from_json(ent["ctx"], out_dir)
        p = ent.get("插入停頓")
        if p and (out_dir / p["檔案"]).is_file():
            h[bi].paused_s = p["長度秒"]
            paused[sid] = {"檔案": out_dir / p["檔案"], "插入": [tuple(x) for x in p["插入"]], "開頭位移秒": p["開頭位移秒"]}

    if use_pauses and slotted and (align is not None or wd.audio_path(workdir).is_file()):
        if phase == "收尾":
            miss = []
            for it in slotted:
                ent = pcache.get(it["id"])
                if pkey_ok(ent, it, _base_index(histories[it["id"]])):
                    use_entry(it, ent)
                else:
                    miss.append(it["id"])
            if miss:
                raise NotGeneratedYet(f"[{tag}] 第 {'、'.join(miss[:10])} 句還沒做插入停頓（插入停頓那一支程式沒做完）；"
                                      "再按一次「開始執行」會接著做")
        else:
            if own_synth:
                synth = None
                _free_memory()
            real_align = align

            def use_align(path, text):
                nonlocal real_align, load_s
                if real_align is None:   # 10-01：要用到才載入（停頓那一支全部沿用的話就不載入）
                    t = time.time()
                    log(f"[{tag}] 載入逐字對位模型，照原片停頓插入空白...")
                    from bookclub.pauses import Aligner

                    real_align = Aligner().align
                    load_s += time.time() - t
                return real_align(path, text)

            for it in slotted:
                sid = it["id"]
                h = histories[sid]
                bi = _base_index(h)
                if phase == "停頓":
                    check_stop(workdir)   # 10-01：停頓那一支也是做完一句就停
                    ent = pcache.get(sid)
                    if not fresh_pauses and pkey_ok(ent, it, bi):
                        use_entry(it, ent)
                        continue
                ent = {"鍵": pkey(it, bi), "ctx": None, "插入停頓": None,
                       "來源指紋": file_fingerprint(out_dir / f"{sid}_第{bi + 1}次.wav")}
                try:
                    ctx = prepare_original(workdir, it, out_dir, use_align)
                except Exception as exc:
                    log(f"  ⚠️ 第 {sid} 句原片停頓分析失敗，不做插入停頓：{exc}")
                    ctx = None
                if ctx:
                    ctxs[sid] = ctx
                    ent["ctx"] = _ctx_to_json(ctx)
                    if h[bi].content_ok():
                        try:
                            dst = out_dir / f"{sid}_第{bi + 1}次_插入停頓.wav"
                            h[bi].paused_s, inserts, lead = _paused_version(
                                out_dir / f"{sid}_第{bi + 1}次.wav", it["text"], ctx, use_align, dst)
                            paused[sid] = {"檔案": dst, "插入": inserts, "開頭位移秒": lead}
                            ent["插入停頓"] = {"檔案": dst.name, "插入": [list(x) for x in inserts], "開頭位移秒": lead,
                                           "長度秒": h[bi].paused_s}
                            log(f"  第 {sid} 句：原片 {len(ctx['pauses'])} 個停頓，插入 {len(inserts)} 段空白，"
                                f"長度 {h[bi].audio_s:.1f} → {h[bi].paused_s:.1f} 秒（時間格 {it['slot_s']:.1f} 秒）")
                        except Exception as exc:
                            log(f"  ⚠️ 第 {sid} 句插入停頓失敗：{exc}")
                if phase == "停頓":
                    pcache[sid] = ent
                    wd.write_json(pcache_path, pcache)
            if own_align:
                real_align = None
                _free_memory()
    if phase == "停頓":
        log(f"[{tag}] 這一支程式只插入停頓：{len(slotted)} 句做完；放回時間格交給下一支程式")
        return load_s

    # 階段三：長度還是不過的才改語速重生成
    allow_new = True
    say = log
    retry = [it for it in todo if it["slot_s"]
             and next_attempt(histories[it["id"]], it["slot_s"], tolerance, avoid[it["id"]]) is not None]
    if retry:
        for it in retry:
            log(f"[{tag}] 第 {it['id']} 句長度跟原本差太多，調整說話快慢再念一次")
            h = histories[it["id"]]
            while (nxt := next_attempt(h, it["slot_s"], tolerance, avoid[it["id"]])) is not None:
                h.append(attempt(it, len(h) + 1, *nxt))

    for it in todo:
        done[it["id"]] = _finalize(it, out_dir, histories[it["id"]], paused.get(it["id"]),
                                   ctxs.get(it["id"]), tolerance, log, role=role, tag=tag)
        ent = versions.get(it["id"])
        if ent and ent.get("以前的版本"):   # 10-02 第四批：退回重做過的句子，記第幾版、以前的版本（舊的那幾版留在紀錄裡）
            done[it["id"]].update({"第幾版": len(ent["以前的版本"]) + 1, "以前的版本": ent["以前的版本"],
                                   "換一種念法": bool(avoid[it["id"]])})
        save()
    return load_s


def generate_teacher(
    workdir: str | Path, sentences_path: str | Path, *,
    ref_wav: str | Path | None = None, ref_text_path: str | Path | None = None,
    check_content: bool = True, check_similarity: bool = True, use_pauses: bool = True, redo: bool = False,
    pron_table: str | Path | None = None,
    synth: Synth | None = None, hear: Hear | None = None, similar: Similar | None = None,
    align: Align | None = None, phase: str | None = None, fresh_pauses: bool = False,
    log: Callable[[str], None] = print,
) -> dict:
    """流程第 5 步（一）的入口：`bookclub gen teacher`。回傳並存下 `生成/老師紀錄.json`。

    分三個階段跑，同一時間只載入一個大模型（8GB 的 Mac 兩個一起放會一直搬
    swap，慢好幾倍）：

    1. 生成模型：每句生成到內容檢查通過（念錯才換種子）
    2. 對位模型：有時間格的句子，照原片停頓插入空白
    3. 生成模型：原始跟插入停頓後長度都不過的句子，才改語速重生成

    synth／hear／similar／align 可以從外面傳進來（測試用假的），不傳就載入真的模型。
    phase（10-01）：第 4 步把三個階段分給三支程式跑（見 `run_generation`）；"生成"、"停頓" 不寫老師紀錄、回傳 {}。
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
    same_ref = same_ref_file(record, ref_wav) and record.get("參考音逐字稿") == ref_text
    if done and not same_ref:
        log("參考音跟上次不同，全部重新生成。")
        done = {}

    todo = [it for it in items if record_stale(done.get(it["id"]), it)]
    log(f"[老師聲音] 共 {len(items)} 句，要生成 {len(todo)} 句（其他 {len(items) - len(todo)} 句沿用上次結果）")
    out_dir = teacher_out_dir(workdir)
    out_dir.mkdir(parents=True, exist_ok=True)
    load_s = 0.0

    if todo:
        load_s = run_generation(
            workdir, todo, out_dir, ref_wav, ref_text, tolerance,
            save=lambda: _write_log(log_path, ref_wav, ref_text, items, done, load_s), done=done,
            check_content=check_content, check_similarity=check_similarity, use_pauses=use_pauses,
            synth=synth, hear=hear, similar=similar, align=align, phase=phase, fresh_pauses=fresh_pauses, log=log)
        if phase in ("生成", "停頓"):
            return {}

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
        "參考音指紋": ref_fingerprint(ref_wav),
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
