"""流程第 1 步的第一個零件：轉文字。

抽出整支影片的音軌 → Silero VAD 掃出人聲區段（挖掉靜音，省下 Groq 的音訊時長
與呼叫次數）→ 依人聲區段合併成不超過 18 分鐘一塊 → 逐塊呼叫 Groq
whisper-large-v3（verbose_json，word＋segment 時間戳）→ 把塊內時間換算回整支
影片的時間、合併、轉台灣繁體 → 存成 `workdir/transcript/merged.json`。

做法照 `tools/podcast_edit/transcribe.py`（正式在用的版本）搬過來，改寫成
`bookclub/refpick.py` 那種「函式可呼叫、結果存檔、每步可續跑、耗時記錄」的
寫法，不用它的 Pipeline／status.json 類別（那是給 Dashboard 網頁用的，這裡
不需要）。

音檔（`workdir/audio.flac`）跟 `bookclub/refpick.py` 抽出來的完全一樣（同樣
16kHz 單聲道 flac、同一個檔名），所以 `bookclub run analyze` 呼叫「挑老師
參考音」時，refpick 的抽音步驟會直接吃到這裡留下的檔案，不必重抽一次。塊級
快取檔名前綴跟 refpick 不一樣（這裡用 4 位數 `chunk_0000.*`，refpick 用
2 位數 `00.*`），兩邊可以共用同一個 `chunks/`／`transcript/` 資料夾，不會互踩。

隱私：逐字稿含學員分享內容。終端機與 print 只印時間、秒數、段數、統計數字，
不印逐字稿內容；逐字稿只寫進 `merged.json`。

重跑：`workdir/transcript/merged.json` 存在就直接讀出來回傳，不重跑 VAD、
不打 Groq。中途中斷的話，已經轉完的塊（`chunk_XXXX.json`）不會重轉，只補
沒做完的塊。
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from bookclub.refpick import FILLER_PROMPT, audio_dur_s, fmt_time
from bookclub.workdir import audio_path, chunks_dir, ensure, merged_transcript_path, transcript_dir

SR = 16000
MAX_CHUNK_S = 18 * 60.0        # 一塊最長 18 分鐘（人聲時長，不含挖掉的靜音）
SPEECH_PAD_S = 0.3             # VAD 抓到的人聲區段前後各留這麼多緩衝
FREE_TIER_MIN_INTERVAL_S = 3.1  # Groq 免費層 20 次/分鐘，呼叫間至少間隔這麼久
GROQ_RETRY_DEFAULT_WAIT_S = 30.0  # 429 沒帶 retry-after 時的預設等待秒數

CHUNK_PREFIX = "chunk_"  # 跟 refpick.py 的 2 位數（00.flac）分開，共用同個資料夾不會互踩


# ---------- 步驟 1：抽音（跟 refpick.py 的抽音步驟共用同一個檔案） ----------

def extract_audio(video: str | Path, workdir: Path) -> tuple[Path, float]:
    video = Path(video).expanduser()
    out = audio_path(workdir)
    if out.exists():
        return out, 0.0
    t0 = time.time()
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
         "-ar", str(SR), "-ac", "1", "-vn", str(out)],
        check=True,
    )
    return out, time.time() - t0


# ---------- VAD：找人聲區段、挖掉靜音 ----------

def _run_vad(audio_file: Path) -> tuple[np.ndarray, list[dict], list[dict], float]:
    """回傳 (整支音訊陣列, 人聲區段[start/end]（已合併重疊的 padding）,
    靜音區段[start/end], 總長秒數)。"""
    import torch
    from silero_vad import get_speech_timestamps, load_silero_vad

    audio, sr = sf.read(str(audio_file), dtype="float32")
    if sr != SR:
        raise RuntimeError(f"音檔取樣率不是 {SR}，實際為 {sr}")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    duration = len(audio) / SR

    model = load_silero_vad(onnx=True)
    speech_timestamps = get_speech_timestamps(
        torch.from_numpy(audio), model, sampling_rate=SR,
        speech_pad_ms=int(SPEECH_PAD_S * 1000),
        max_speech_duration_s=MAX_CHUNK_S,
        return_seconds=True, time_resolution=3,
    )
    raw = [{"start": float(s["start"]), "end": float(s["end"])} for s in speech_timestamps]

    speech_segments: list[dict] = []
    for seg in sorted(raw, key=lambda s: s["start"]):
        if speech_segments and seg["start"] <= speech_segments[-1]["end"]:
            speech_segments[-1]["end"] = max(speech_segments[-1]["end"], seg["end"])
        else:
            speech_segments.append(dict(seg))

    silence_map: list[dict] = []
    cursor = 0.0
    for seg in speech_segments:
        if seg["start"] > cursor:
            silence_map.append({"start": round(cursor, 3), "end": round(seg["start"], 3)})
        cursor = max(cursor, seg["end"])
    if cursor < duration:
        silence_map.append({"start": round(cursor, 3), "end": round(duration, 3)})

    return audio, speech_segments, silence_map, duration


def _group_into_chunks(speech_segments: list[dict], max_seconds: float = MAX_CHUNK_S) -> list[list[dict]]:
    """依序把人聲區段合併成塊，用「片段實際音訊時長總和」（不含中間靜音）判斷是否
    超過 max_seconds。純數字運算，不碰音檔，方便單獨測試。"""
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_dur = 0.0
    for seg in speech_segments:
        seg_dur = seg["end"] - seg["start"]
        if current and current_dur + seg_dur > max_seconds:
            chunks.append(current)
            current = [seg]
            current_dur = seg_dur
        else:
            current.append(seg)
            current_dur += seg_dur
    if current:
        chunks.append(current)
    return chunks


def _compute_mapping(segments: list[dict]) -> list[dict]:
    """segments 接起來後的時間對應表：[{concat_start, concat_end, original_start}, ...]。"""
    mapping: list[dict] = []
    cursor = 0.0
    for seg in segments:
        dur = seg["end"] - seg["start"]
        mapping.append({"concat_start": cursor, "concat_end": cursor + dur, "original_start": seg["start"]})
        cursor += dur
    return mapping


def _map_time_to_original(mapping: list[dict], t: float, prefer: str = "end") -> float:
    """接起來後音檔上的時間 t，換算回原始音檔時間。`prefer` 決定 t 剛好落在接縫上時
    歸給哪一段：`"start"`（字的開始時間）歸後一段開頭，`"end"`（字的結束時間）歸前
    一段結尾——接在長靜音後面的第一個字才不會被標成靜音開始的時間。"""
    if not mapping:
        return t
    if prefer == "start":
        for m in mapping:
            if t < m["concat_end"] - 1e-6:
                return m["original_start"] + max(0.0, t - m["concat_start"])
    else:
        for m in mapping:
            if m["concat_start"] - 0.05 <= t <= m["concat_end"] + 0.05:
                return m["original_start"] + (t - m["concat_start"])
    if t < mapping[0]["concat_start"]:
        return mapping[0]["original_start"]
    last = mapping[-1]
    return last["original_start"] + (t - last["concat_start"])


def _build_chunk_audio(audio: np.ndarray, segments: list[dict], out_path: Path) -> None:
    pieces = []
    for seg in segments:
        s = max(0, int(seg["start"] * SR))
        e = min(len(audio), int(seg["end"] * SR))
        pieces.append(audio[s:e])
    concatenated = np.concatenate(pieces) if pieces else np.array([], dtype=audio.dtype)
    sf.write(str(out_path), concatenated, SR, format="FLAC")


# ---------- Groq 呼叫（429 就等重試，做法照 refpick._groq_transcribe_bytes） ----------

def _groq_call_with_retry(client, filename: str, data: bytes, prompt: str) -> dict:
    from groq import RateLimitError

    while True:
        try:
            return client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=(filename, data),
                response_format="verbose_json",
                timestamp_granularities=["word", "segment"],
                language="zh",
                prompt=prompt,
            ).model_dump()
        except RateLimitError as e:
            wait_s = GROQ_RETRY_DEFAULT_WAIT_S
            try:
                retry_after = e.response.headers.get("retry-after")
                if retry_after:
                    wait_s = float(retry_after)
            except Exception:
                pass
            print(f"[轉文字] 碰到 429（速率限制），等待 {wait_s:.0f} 秒後重試...")
            time.sleep(wait_s)


def _build_prompt(roster_names: list[str] | None) -> str:
    if not roster_names:
        return FILLER_PROMPT
    return FILLER_PROMPT + "今天提到的學員：" + "、".join(roster_names) + "。"


# ---------- 主流程 ----------

def transcribe(
    video: str | Path,
    workdir: str | Path,
    roster_names: list[str] | None = None,
) -> dict:
    """回傳 dict：
        sentences: [{id, start, end, text, avg_logprob}, ...]（原影片絕對時間）
        words:     [{start, end, word}, ...]（原影片絕對時間）
        duration:  影片音訊總長秒數
        silence_map: [{start, end}, ...] VAD 判定的靜音區段（原影片絕對時間）
        elapsed:   各步驟耗時 dict

    `workdir/transcript/merged.json` 已存在就直接讀出來回傳，不重跑 VAD、不打
    Groq。roster_names 只影響 Groq 的 prompt（幫助辨識學員名字），不影響快取
    判斷——快取一律照已經轉好的結果為準。
    """
    workdir = ensure(workdir)
    merged_path = merged_transcript_path(workdir)
    if merged_path.exists():
        cached = json.loads(merged_path.read_text(encoding="utf-8"))
        cached.setdefault("elapsed", {})
        print("[1/轉文字] 已有 transcript/merged.json，略過（不重跑 VAD、不打 Groq）")
        return cached

    video = Path(video).expanduser()
    elapsed: dict[str, object] = {}

    print("[1/轉文字] ffmpeg 抽音（16kHz 單聲道 flac）...")
    audio_file, t_extract = extract_audio(video, workdir)
    elapsed["抽音"] = round(t_extract, 1)
    print(f"[1/轉文字] 抽音完成，{t_extract:.1f} 秒" if t_extract else "[1/轉文字] 音軌已有快取，略過")

    print("[1/轉文字] Silero VAD 掃人聲、挖靜音...")
    t0 = time.time()
    audio, speech_segments, silence_map, duration = _run_vad(audio_file)
    t_vad = time.time() - t0
    elapsed["VAD掃描"] = round(t_vad, 1)
    speech_total = sum(s["end"] - s["start"] for s in speech_segments)
    print(f"[1/轉文字] VAD 完成，{t_vad:.1f} 秒（人聲 {len(speech_segments)} 段、共 "
          f"{fmt_time(speech_total)}，佔整支 {speech_total / duration * 100:.1f}%）")

    chunks = _group_into_chunks(speech_segments, MAX_CHUNK_S)
    print(f"[1/轉文字] 切成 {len(chunks)} 塊（每塊人聲最長 {MAX_CHUNK_S / 60:.0f} 分鐘）")

    cdir = chunks_dir(workdir)
    tdir = transcript_dir(workdir)
    cdir.mkdir(parents=True, exist_ok=True)
    tdir.mkdir(parents=True, exist_ok=True)

    prompt = _build_prompt(roster_names)
    client = None
    last_call_t = 0.0
    per_chunk_elapsed: dict[str, float] = {}
    t_groq0 = time.time()

    merged_words: list[dict] = []
    merged_sentences: list[dict] = []

    for i, chunk_segments in enumerate(chunks):
        tag = f"{i:04d}"
        cache_json = tdir / f"{CHUNK_PREFIX}{tag}.json"
        mapping = _compute_mapping(chunk_segments)

        if cache_json.exists():
            print(f"[1/轉文字] 塊 {tag} 已有逐字稿，跳過")
            result = json.loads(cache_json.read_text(encoding="utf-8"))
        else:
            chunk_audio = cdir / f"{CHUNK_PREFIX}{tag}.flac"
            if not chunk_audio.exists():
                _build_chunk_audio(audio, chunk_segments, chunk_audio)

            if client is None:
                from groq import Groq

                client = Groq()  # 金鑰從環境變數 GROQ_API_KEY 讀，不印出、不寫檔

            wait = FREE_TIER_MIN_INTERVAL_S - (time.time() - last_call_t)
            if wait > 0:
                time.sleep(wait)

            print(f"[1/轉文字] 塊 {tag}（人聲共 {fmt_time(sum(s['end']-s['start'] for s in chunk_segments))}）轉文字中...")
            t1 = time.time()
            result = _groq_call_with_retry(client, f"{tag}.flac", chunk_audio.read_bytes(), prompt)
            last_call_t = time.time()
            chunk_elapsed = last_call_t - t1
            per_chunk_elapsed[tag] = round(chunk_elapsed, 1)
            cache_json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[1/轉文字] 塊 {tag} 完成，{chunk_elapsed:.1f} 秒")

        for w in result.get("words") or []:
            s = _map_time_to_original(mapping, w.get("start", 0.0), prefer="start")
            e = _map_time_to_original(mapping, w.get("end", 0.0), prefer="end")
            merged_words.append({"word": w.get("word"), "start": round(s, 3), "end": round(e, 3)})
        for j, seg in enumerate(result.get("segments") or []):
            s = _map_time_to_original(mapping, seg.get("start", 0.0), prefer="start")
            e = _map_time_to_original(mapping, seg.get("end", 0.0), prefer="end")
            merged_sentences.append({
                "id": f"{tag}_{j:03d}",
                "start": round(s, 3), "end": round(e, 3),
                "text": seg.get("text", ""),
                "avg_logprob": seg.get("avg_logprob", 0.0),
            })

    elapsed["轉文字_各段"] = per_chunk_elapsed
    elapsed["轉文字_總計"] = round(time.time() - t_groq0, 1)

    merged_words.sort(key=lambda w: w["start"])
    merged_sentences.sort(key=lambda s: s["start"])

    print("[1/轉文字] 轉台灣繁體...")
    t2 = time.time()
    import opencc

    converter = opencc.OpenCC("s2twp")
    for s in merged_sentences:
        s["text"] = converter.convert(s.get("text") or "")
    for w in merged_words:
        w["word"] = converter.convert(w.get("word") or "")
    elapsed["轉繁體"] = round(time.time() - t2, 1)

    merged = {
        "source": str(video),
        "duration": round(duration, 3),
        "sentences": merged_sentences,
        "words": merged_words,
        "silence_map": silence_map,
        "elapsed": elapsed,
    }
    merged_path.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[1/轉文字] 完成：{len(merged_sentences)} 句、{len(merged_words)} 個字，"
          f"寫入 {merged_path}")
    return merged
