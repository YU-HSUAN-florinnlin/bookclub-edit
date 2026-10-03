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

10-04 #62：人聲片段之間補空白（跟下一段的原始間隔，最多 `KEEP_PAUSE_S` 秒）再送 Groq，句子斷得比較細；
跨過被挖掉的長停頓的句子在停頓那裡斷開（`split_at_long_pauses`）。

重跑：`workdir/transcript/merged.json` 存在就直接讀出來回傳，不重跑 VAD、
不打 Groq（已經轉好的工作區照舊，不會因為改了做法重轉）。中途中斷的話，已經轉完的塊（`chunk_XXXX.json`）不會重轉，只補
沒做完的塊。
"""

from __future__ import annotations

import json
import re
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

# 10-04 #62（做法丙，宇軒 10-03 定）：送 Groq 前人聲片段之間補空白＝跟下一段的原始間隔，最多這麼多秒。
# 以前停頓全挖掉，轉文字模型聽不到停頓就少斷句（第一堂 635 句、句長中位數 5.3 秒）。10-03 實驗
# （`轉文字停頓實驗_1003/比較.txt`）：保留 1 秒＝1417 句、中位數 2.4 秒、送 Groq 63 → 73 分鐘（呼叫次數不變）。
KEEP_PAUSE_S = 1.0
# 句子跨過原片安靜超過「保留秒數＋人聲前後緩衝（0.3＋0.3）」的地方（被挖掉的長停頓）→ 在那裡斷開
LONG_PAUSE_SPLIT_S = KEEP_PAUSE_S + 2 * SPEECH_PAD_S
CACHE_PAUSE_KEY = "_保留停頓秒數"   # 塊級快取記下當時補了多少空白（舊快取沒有這個欄位＝0，照舊換算）

CHUNK_PREFIX = "chunk_"  # 跟 refpick.py 的 2 位數（00.flac）分開，共用同個資料夾不會互踩


# ---------- 步驟 1：抽音（跟 refpick.py 的抽音步驟共用同一個檔案） ----------

def extract_audio(video: str | Path, workdir: Path) -> tuple[Path, float]:
    """10-03 第八批（#11）：跟 refpick 共用 `refpick.ensure_audio`（殘檔比長度、先寫暫存檔名）。"""
    from bookclub.refpick import ensure_audio

    return ensure_audio(video, workdir)


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


def _pause_samples(segments: list[dict], keep_pause: float = KEEP_PAUSE_S) -> list[int]:
    """每一段後面補幾個樣本的空白：跟下一段的原始間隔，最多 keep_pause 秒；最後一段不補。
    `_build_chunk_audio` 與 `_compute_mapping` 共用，兩邊一定對得上。"""
    out = []
    for k, seg in enumerate(segments):
        if k + 1 < len(segments) and keep_pause > 0:
            gap = max(0.0, segments[k + 1]["start"] - seg["end"])
            out.append(int(round(min(gap, keep_pause) * SR)))
        else:
            out.append(0)
    return out


def _compute_mapping(segments: list[dict], keep_pause: float = KEEP_PAUSE_S) -> list[dict]:
    """segments 接起來後的時間對應表：[{concat_start, concat_end, original_start, pause}, ...]。

    10-04 #62：段跟段之間補的空白（`pause` 秒）算前一段的延伸——`concat_end` 含空白，空白裡的時間
    換算回原片＝這一段結尾之後的那一小段原片（原片真的安靜的地方，因為空白不超過原始間隔）。
    （10-03 實驗漏了這一點：空白沒算進對應表，後面的時間全部往後錯、超出的被算到整塊最後，句子最長變成 1034 秒。）"""
    mapping: list[dict] = []
    cursor = 0.0
    for seg, pad in zip(segments, _pause_samples(segments, keep_pause)):
        dur = seg["end"] - seg["start"]
        p = pad / SR
        mapping.append({"concat_start": cursor, "concat_end": cursor + dur + p,
                        "original_start": seg["start"], "pause": p})
        cursor += dur + p
    return mapping


def _map_time_to_original(mapping: list[dict], t: float, prefer: str = "end") -> float:
    """接起來後音檔上的時間 t，換算回原始音檔時間。`prefer` 決定 t 剛好落在接縫上時
    歸給哪一段：`"start"`（字的開始時間）歸後一段開頭，`"end"`（字的結束時間）歸前
    一段結尾——接在長靜音後面的第一個字才不會被標成靜音開始的時間。
    超出整塊的時間夾在最後一段（含空白）的結尾，不會往後外插。"""
    if not mapping:
        return t
    if prefer == "start":
        for m in mapping:
            if t < m["concat_end"] - 1e-6:
                return m["original_start"] + max(0.0, t - m["concat_start"])
    else:
        for m in mapping:
            if m["concat_start"] - 0.05 <= t <= m["concat_end"] + 0.05:
                return m["original_start"] + min(t - m["concat_start"], m["concat_end"] - m["concat_start"])
    if t < mapping[0]["concat_start"]:
        return mapping[0]["original_start"]
    last = mapping[-1]
    return last["original_start"] + max(0.0, min(t - last["concat_start"], last["concat_end"] - last["concat_start"]))


def _seg_index(mapping: list[dict], t: float, prefer: str) -> int:
    if prefer == "start":
        for k, m in enumerate(mapping):
            if t < m["concat_end"] - 1e-6:
                return k
        return len(mapping) - 1
    for k, m in enumerate(mapping):
        if m["concat_start"] - 0.05 <= t <= m["concat_end"] + 0.05:
            return k
    return 0 if t < mapping[0]["concat_start"] else len(mapping) - 1


def _map_word(mapping: list[dict], s: float, e: float) -> tuple[float, float]:
    """一個字的起訖換算回原片（10-04 #105）。

    字橫跨接縫、而接縫那裡有被挖掉的靜音時（換算回去的長度比接起來的長度多），以前起點算前一段、終點算後一段，
    字的時間就把挖掉的靜音整段包進去（第一堂 43:06.9–43:09.1 一個字 2.2 秒，裡面有 1.5 秒是挖掉的靜音）
    → 找名字時名字範圍抓太長，直接消音消掉一大段。改成：字留在接起來時佔比較多的那一段，另一頭夾在那一段的邊上。"""
    os_, oe = _map_time_to_original(mapping, s, prefer="start"), _map_time_to_original(mapping, e, prefer="end")
    if not mapping or oe - os_ <= (e - s) + 0.05:
        return os_, oe
    i, j = _seg_index(mapping, s, "start"), _seg_index(mapping, e, "end")
    if i >= j:
        return os_, oe
    mi, mj = mapping[i], mapping[j]
    if mi["concat_end"] - s >= e - mj["concat_start"]:   # 留在前一段：終點夾在前一段（含空白）的結尾
        return os_, min(oe, mi["original_start"] + (mi["concat_end"] - mi["concat_start"]))
    return max(os_, mj["original_start"]), oe          # 留在後一段：起點夾在後一段的開頭


def _build_chunk_audio(audio: np.ndarray, segments: list[dict], out_path: Path,
                       keep_pause: float = KEEP_PAUSE_S) -> None:
    """人聲片段接起來；10-04 #62：段跟段之間補空白（`_pause_samples`，最多 keep_pause 秒），轉文字模型才聽得到停頓。"""
    pieces = []
    for seg, pad in zip(segments, _pause_samples(segments, keep_pause)):
        s = max(0, int(seg["start"] * SR))
        e = min(len(audio), int(seg["end"] * SR))
        pieces.append(audio[s:e])
        if pad:
            pieces.append(np.zeros(pad, dtype=audio.dtype))
    concatenated = np.concatenate(pieces) if pieces else np.array([], dtype=audio.dtype)
    sf.write(str(out_path), concatenated, SR, format="FLAC")


_CLEAN_RE = re.compile(r"[\w一-鿿]", re.UNICODE)


def _clean_len(text: str) -> int:
    return len(_CLEAN_RE.findall(text or ""))


def _split_text(text: str, n_clean: int) -> tuple[str, str] | None:
    """句子文字在第 n_clean 個字（不算標點空白）後面切開，緊接的標點留在前半；切不到回傳 None。"""
    if n_clean <= 0:
        return None
    seen = 0
    for k, ch in enumerate(text):
        if _CLEAN_RE.match(ch):
            seen += 1
            if seen == n_clean:
                cut = k + 1
                while cut < len(text) and not _CLEAN_RE.match(text[cut]) and not text[cut].isspace():
                    cut += 1
                head, tail = text[:cut].strip(), text[cut:].strip()
                return (head, tail) if head and tail else None
    return None


def split_at_long_pauses(sentences: list[dict], words: list[dict], silence_map: list[dict],
                         min_gap: float = LONG_PAUSE_SPLIT_S) -> tuple[list[dict], dict]:
    """10-04 #62 第 3 點（純函式）：句子跨過被挖掉的長停頓（原片安靜超過 min_gap 秒）就在那裡斷開，
    句子編號照 `0000_000` 格式（塊號_塊內第幾句）重編。

    文字照字的時間分：停頓前的字算前半、停頓後的算後半，句子文字在前半的字數那裡切開。
    一邊沒有字 → 不切，句子的起訖縮到有字那一邊（停頓的邊上）；字數對不上句子文字 → 不切（算進「對不上」）。
    回傳 (新句子清單, 統計 {切開, 縮邊, 對不上})。"""
    gaps = sorted((g for g in silence_map or [] if g["end"] - g["start"] > min_gap), key=lambda g: g["start"])
    ws = sorted(words or [], key=lambda w: w["start"])
    stats = {"切開": 0, "縮邊": 0, "對不上": 0}
    out: list[dict] = []
    for sent in sorted(sentences, key=lambda x: x["start"]):
        pieces = [dict(sent)]
        for g in gaps:
            cur = pieces[-1]
            if not (cur["start"] < g["start"] and g["end"] < cur["end"]):
                continue
            mid = (g["start"] + g["end"]) / 2
            inside = [w for w in ws if cur["start"] - 0.05 <= (w["start"] + w["end"]) / 2 <= cur["end"] + 0.05]
            before = [w for w in inside if (w["start"] + w["end"]) / 2 < mid]
            after = [w for w in inside if (w["start"] + w["end"]) / 2 >= mid]
            if not before or not after:
                if after:
                    cur["start"] = round(g["end"], 3)
                elif before:
                    cur["end"] = round(g["start"], 3)
                stats["縮邊"] += 1 if (before or after) else 0
                continue
            n_before = sum(_clean_len(w.get("word") or "") for w in before)
            parts = _split_text(cur.get("text") or "", n_before)
            if parts is None:
                stats["對不上"] += 1
                continue
            head = {**cur, "end": round(min(cur["end"], g["start"]), 3), "text": parts[0]}
            tail = {**cur, "start": round(max(cur["start"], g["end"]), 3), "text": parts[1]}
            pieces[-1:] = [head, tail]
            stats["切開"] += 1
        out.extend(pieces)
    counters: dict[str, int] = {}
    for s in out:
        tag = str(s.get("id", "0000_000")).split("_")[0]
        k = counters.get(tag, 0)
        s["id"] = f"{tag}_{k:03d}"
        counters[tag] = k + 1
    return out, stats


# ---------- Groq 呼叫（429 就等重試，做法照 refpick._groq_transcribe_bytes） ----------

def _groq_call_with_retry(client, filename: str, data: bytes, prompt: str) -> dict:
    from bookclub.refpick import groq_retry   # 09-30：429 以外，斷線、逾時、伺服器錯誤也重試

    return groq_retry(lambda: client.audio.transcriptions.create(
        model="whisper-large-v3",
        file=(filename, data),
        response_format="verbose_json",
        timestamp_granularities=["word", "segment"],
        language="zh",
        prompt=prompt,
    ).model_dump())


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

        if cache_json.exists():
            print(f"[1/轉文字] 塊 {tag} 已有逐字稿，跳過")
            result = json.loads(cache_json.read_text(encoding="utf-8"))
        else:
            chunk_audio = cdir / f"{CHUNK_PREFIX}{tag}.flac"
            # 10-04 #62：每次都重接（很快）——舊版留下的塊音檔沒有補空白，跟這次的對應表對不上
            _build_chunk_audio(audio, chunk_segments, chunk_audio, KEEP_PAUSE_S)

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
            result[CACHE_PAUSE_KEY] = KEEP_PAUSE_S
            cache_json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[1/轉文字] 塊 {tag} 完成，{chunk_elapsed:.1f} 秒")

        # 塊級快取當時補了多少空白就照多少換算（舊快取沒記＝沒補空白）
        mapping = _compute_mapping(chunk_segments, float(result.get(CACHE_PAUSE_KEY) or 0.0))
        for w in result.get("words") or []:
            s, e = _map_word(mapping, w.get("start", 0.0), w.get("end", 0.0))
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
    merged_sentences, split_stats = split_at_long_pauses(merged_sentences, merged_words, silence_map)
    print(f"[1/轉文字] 跨過長停頓的句子：切開 {split_stats['切開']} 處、縮邊 {split_stats['縮邊']} 處、"
          f"字數對不上沒切 {split_stats['對不上']} 處")

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
        "保留停頓秒數": KEEP_PAUSE_S,   # 10-04 #62：有這個欄位＝送 Groq 時段跟段之間補了空白（句子比較短）
        "elapsed": elapsed,
    }
    merged_path.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[1/轉文字] 完成：{len(merged_sentences)} 句、{len(merged_words)} 個字，"
          f"寫入 {merged_path}")
    return merged
