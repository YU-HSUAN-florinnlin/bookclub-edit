"""自動挑老師參考音：從一支讀書會影片裡，找出一段 25–29 秒、只有老師講話的
片段，連同 Groq 辨識的逐字稿初稿一起推薦給操作的人（夥伴）。

使用流程（宇軒定案）：程式跑完直接推薦一段參考音＋逐字稿。夥伴聽：裡面有
別人的聲音就按「換一段」；沒問題就邊聽邊修正逐字稿、存檔送出，完成。

這支模組是核心邏輯，CLI（`bookclub ref pick` / `bookclub ref use`）只是外殼；
之後會再包一層本機網頁伺服器，一樣呼叫這裡的函式。

流程（對照 tasks 裡的規格）：
    1. 抽音：16kHz 單聲道 flac（整支影片）。
    2. 整支轉文字：Groq whisper-large-v3，10 分鐘一段、verbose_json、
       每次呼叫間隔 3.1 秒、429 就等待重試。做法照 spikes/diarize/light_pass_test.py
       驗證過的參數，不用學員名單（這支函式不知道是哪一場，只放口頭語）。
    3. 不用老師自錄聲音找老師：逐句抽聲紋、正規化，用 scipy 階層式分群
       （average linkage、cosine 距離、距離門檻 0.6）分群，講話總秒數最多的
       那群＝老師；該群平均聲紋（正規化）當老師聲紋。若有給 teacher_ref
       （老師自錄音檔），只拿來「確認」：算它跟老師群中心的相似度寫進結果，
       不參與判斷。
    4. 找候選區域：連續的老師句子（相似度、信心分數、句間空白都過門檻），
       區域長 30–75 秒。
    3.5+4b. 排除區域：「聲音重疊檢查」這個獨立步驟（不在這支模組裡）跑完會輸出
       混到別人聲音的時間區間，透過 exclude_regions 參數餵進來；這支模組另外
       自己把步驟 3 判成「不是老師」或「不確定」的句子前後各擴 EXCLUDE_PAD_S
       秒（對應 02 規格的 overlap_scan_pad_s）也當排除區域。候選區域只要跟任一
       排除區域重疊，就不列入候選池（不是裁切，直接不列）。
    5+6. 排序（見 _find_and_rank_candidates 的加權公式），依分數高到低組成一個
       候選池（比 n 大一些，供極少數還是不夠長的候選淘汰後遞補）。
    5a. 對齊第一個字／最後一個字：用 Groq 的字層級時間戳，把候選區域的起訖收窄到
       第一個字開始、最後一個字結束（前後留 WORD_PAD_S 秒），字前字後的笑聲、
       雜音會被排除在外。
    5b. 壓縮停頓、切段（make_ref 演算法原樣照搬，沒有改邏輯），在停頓處切到
       ≤29 秒。
    5c. 細看聲紋抓漏：**預設關閉**（window_scan=False，宇軒定案改用排除區域擋
       混到別人聲音的候選，不再逐窗細看）。要打開的話對字對齊後的區域用 1.5 秒
       窗、0.5 秒步進逐窗抽聲紋跟老師聲紋中心比，安靜的窗跳過不比；任一窗相似度
       低於 WINDOW_SIM_THRESHOLD，問題在頭或尾就裁掉那段（裁完還留
       ≥ MIN_CLIP_AFTER_TRIM_S 秒才留下）、問題在中間就整段淘汰換下一個遞補。
    7. 入選的每一段再用 Groq 轉一次文字，OpenCC 轉台灣繁體，存成逐字稿初稿（拼接
       型候選不重跑 Groq，直接把組成的每一小段原本的逐字稿接起來，見下）。
    8. 輸出到 workdir/參考音/：候選{N}.wav/.txt（N 是最終名次）、候選.json
       （含入選跟被淘汰的每一次嘗試）、挑選紀錄.json、試聽.html。

保底順序（宇軒定案，2026-09-22）：單一連續段落（30–75 秒）湊不到
MIN_CANDIDATES（3）個候選時，改試「拼接」——從沒碰到排除區域的老師短片段
（COMBINE_MIN_SEG_S–COMBINE_MAX_SEG_S 秒）裡挑 2–3 段接成 25–29 秒：
    a. 找短片段：邏輯跟找單一段落候選一樣（共用 _build_scored_spans），只是
       長度上限縮小很多，這樣才找得到夠短、可以組合的片段。
    b. 挑段（_pick_combine_set）：優先挑分數高、彼此時間接近（收音條件較一致）
       的組合，公式見函式註解。
    c. 每小段各自字對齊裁頭尾（5a）、壓縮停頓（5b），再用 _assemble_pieces
       接起來：音量 RMS 對齊到第一段、段與段之間插 COMBINE_SILENCE_S 秒靜音、
       接點各 COMBINE_FADE_S 秒淡入淡出。
    d. 逐字稿：把用到的每一小段原本的逐字稿（來自步驟 2 的全片轉文字，不是
       重轉）依時間順序接起來，中間用「。」隔開。
    e. 湊不到 COMBINE_MIN_SEGMENTS 段可用（排除區域擋太多）→ 放寬：「不確定」
       的句子不算排除區域（只用「不是老師」＋外部傳入的 exclude_regions），
       再試一次。
`force_combine=True`（CLI `--force-combine`）測試用：不管單一段落候選夠不夠，
強制只產拼接候選。

隱私：逐字稿含學員分享內容。終端機與 print 只印時間、秒數、分數、統計數字，
不印逐字稿內容；逐字稿只寫進檔案。

重跑時每一個花錢／花時間的步驟（抽音、轉文字、聲紋比對）都有快取，已經跑過
會跳過；排序這幾步是純 Python 運算，很快，不特別快取。候選音檔／逐字稿（步驟
5a–7）每次都重做，不做快取——候選會不會被淘汰、遞補到誰，取決於整批候選池，
單獨快取某一段容易跟其他段的狀態兜不起來。
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

# ---------- 參數（各步驟門檻與常數，出處見上面流程說明） ----------

SR = 16000
FRAME = 0.02                 # 壓縮停頓用的音框長度（秒），照 make_ref.py

# 步驟 2：轉文字
CHUNK_S = 600.0               # 10 分鐘一段，不重疊
GROQ_CALL_INTERVAL_S = 3.1    # 每次呼叫間隔，避免撞到 rate limit
GROQ_RETRY_DEFAULT_WAIT_S = 30.0  # 429 沒帶 retry-after 時的預設等待秒數
FILLER_PROMPT = "嗯，就是說，呃，那個，然後，對啊，其實這個東西，嗯……"

# 步驟 3：找老師（不用老師自錄聲音）
MIN_SEG_S = 0.8                # 短於這個秒數的句子不抽聲紋，標「太短」
CLUSTER_DISTANCE_T = 0.6       # fcluster 的距離門檻
SIM_TEACHER = 0.55             # 相似度 >= 這個門檻判老師
SIM_NOT_TEACHER = 0.35         # 相似度 <= 這個門檻判不是老師（中間「不確定」）
TEACHER_CHUNK_S = 10.0         # teacher_ref 確認用：切幾秒一段取平均聲紋

# 步驟 4：找候選區域
CAND_MIN_SIM = 0.5             # 區域內每句相似度門檻
CAND_MIN_LOGPROB = -0.6        # 區域內每句 Groq 信心分數門檻
CAND_MAX_GAP_S = 1.2           # 句與句之間最大空白
CAND_MIN_DUR_S = 30.0          # 區域最短長度
CAND_MAX_DUR_S = 75.0          # 區域最長長度（超過就只取前 75 秒）

# 排除區域：混到別人聲音的時間區間，候選只要跟這些重疊就不列入
EXCLUDE_PAD_S = 3.0   # 步驟 3 判「不是老師」／「不確定」的句子前後各擴這麼多秒
                      # （對應 02 規格的 overlap_scan_pad_s）

# 步驟 5+6：排序、去重疊
CAND_START_GAP_S = 30.0        # 入選候選彼此起點至少要差這麼多秒
SCORE_W_MIN_SIM = 0.5          # 排序權重：區域內最低相似度（老師講話的純度，權重最高）
SCORE_W_DENSITY = 0.3          # 排序權重：說話密度（用來避開引導練習那種稀疏、長停頓的片段）
SCORE_W_CONFIDENCE = 0.2       # 排序權重：逐字稿信心分數（避開容易出現亂碼字的片段）
DENSITY_REF_CPS = 4.0          # 說話密度正規化基準：每秒 4 個字視為「正常密度」，超過封頂在 1.0

# 步驟 5a：對齊第一個字／最後一個字（裁掉字前字後的笑聲、雜音）
WORD_PAD_S = 0.125   # 對齊到字之後，前後各留的秒數（0.1–0.15 秒之間取中間值）

# 步驟 5b：壓縮停頓、切段（演算法原樣照 spikes/refclip/make_ref.py，不改邏輯）
MAX_S = 29.0        # CosyVoice 參考音上限 30 秒，留一點餘裕
LONG_PAUSE = 0.35   # 超過這麼長的停頓才縮
KEEP_PAUSE = 0.3    # 縮成這麼長

# 步驟 5c：細看聲紋抓漏（預設關閉，見 pick_reference 的 window_scan 參數——
# 宇軒定案改用排除區域擋混到別人聲音的候選，這組常數跟函式保留但預設不跑）
WINDOW_S = 1.5                # 掃描窗長
WINDOW_STEP_S = 0.5           # 掃描步進
WINDOW_SIM_THRESHOLD = 0.35   # 任一窗相似度低於這個門檻視為有問題（常數，之後要調就改這裡）
WINDOW_SILENCE_DB_DROP = 30   # 窗內平均音量比這段裡最大聲的地方低這麼多 dB，視為安靜、不拿來比對
MIN_CLIP_AFTER_TRIM_S = 20.0  # 裁掉問題頭尾之後，壓縮出來的參考音至少要留這麼長，不然整段淘汰

CAND_POOL_MULTIPLIER = 6   # 候選池大小 = max(n * 這個倍數, CAND_POOL_MIN)，供淘汰後遞補
CAND_POOL_MIN = 20

# 保底順序：拼接（單一連續段落候選不夠時的後備方案）
MIN_CANDIDATES = 3          # 單一段落候選少於這個數字，啟動拼接保底
COMBINE_MIN_SEG_S = 8.0     # 拼接用的短片段，每段至少要這麼長
COMBINE_MAX_SEG_S = 20.0    # 每段最長（太長就該早就進入單一段落候選了，不必拼接）
COMBINE_MIN_SEGMENTS = 2    # 最少拼幾段
COMBINE_MAX_SEGMENTS = 3    # 最多拼幾段
COMBINE_TARGET_MIN_S = 25.0  # 拼接目標長度下限
COMBINE_TARGET_MAX_S = 29.0  # 拼接目標長度上限
COMBINE_SILENCE_S = 0.3     # 段與段之間插入的靜音秒數
COMBINE_FADE_S = 0.01       # 每段頭尾淡入淡出秒數（10ms）
COMBINE_TIME_SPREAD_REF_S = 600.0  # 選段公式：時間接近程度的正規化基準（10 分鐘內算接近）
COMBINE_PICK_W_SCORE = 0.7   # 選段權重：片段自己的分數（品質優先）
COMBINE_PICK_W_CLOSE = 0.3   # 選段權重：跟已選片段的時間接近程度（收音條件較一致）
COMBINE_DEDUP_GAP_S = COMBINE_MIN_SEG_S / 2  # 短片段去重疊用的起點最小間距（比單一段落候選的 30 秒門檻小很多，短片段本來就密集）
COMBINE_POOL_CAP = 40        # 短片段候選池最多留幾個（依分數排序後截斷，避免組合爆炸）

REF_DIR_NAME = "參考音"


# ---------- 小工具 ----------

def fmt_time(sec: float) -> str:
    """秒數轉 "HH:MM:SS"，只用於標時間，不涉及音訊內容。"""
    sec = max(0, round(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _mmss(sec: float) -> str:
    sec = max(0, round(sec))
    m, s = divmod(sec, 60)
    return f"{m:02d}:{s:02d}"


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def _l2norm(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def audio_dur_s(path: Path) -> float:
    return sf.info(str(path)).duration


def _ref_dir(workdir: Path, ref_dir_name: str = REF_DIR_NAME) -> Path:
    return workdir / ref_dir_name


# ---------- 步驟 1：抽音 ----------

def _step1_extract_audio(video: Path, workdir: Path) -> tuple[Path, float]:
    workdir.mkdir(parents=True, exist_ok=True)
    audio_path = workdir / "audio.flac"
    if audio_path.exists():
        return audio_path, 0.0
    t0 = time.time()
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
         "-ar", str(SR), "-ac", "1", "-vn", str(audio_path)],
        check=True,
    )
    return audio_path, time.time() - t0


# ---------- 步驟 2：整支轉文字 ----------

def _groq_transcribe_bytes(client, filename: str, data: bytes) -> dict:
    """呼叫 Groq whisper-large-v3，429 就等待重試；金鑰只從環境變數讀，不印出。"""
    from groq import RateLimitError

    while True:
        try:
            r = client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=(filename, data),
                response_format="verbose_json",
                timestamp_granularities=["word", "segment"],
                language="zh",
                prompt=FILLER_PROMPT,
            ).model_dump()
            return r
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


def _step2_transcribe(audio_path: Path, workdir: Path) -> tuple[list[dict], list[dict], dict, float]:
    """回傳 (sentences[start/end/text/avg_logprob]，words[start/end/word]，
    各段耗時 dict，總耗時)。每段結果快取到 transcript/{i:02d}.json，已存在就跳過
    （可續跑）；重跑時如果之前的快取已經存在，這裡不會再打 Groq，只是重新讀檔、
    把字層級時間戳（word）也一起解析出來給步驟 5a 用。"""
    from groq import Groq

    total_dur = audio_dur_s(audio_path)
    chunks_dir = workdir / "chunks"
    transcript_dir = workdir / "transcript"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    transcript_dir.mkdir(parents=True, exist_ok=True)

    n_chunks = math.ceil(total_dur / CHUNK_S)
    client = None
    per_chunk_elapsed: dict[str, float] = {}
    t_total0 = time.time()

    for i in range(n_chunks):
        chunk_start = i * CHUNK_S
        chunk_end = min(chunk_start + CHUNK_S, total_dur)
        dur = chunk_end - chunk_start
        out_json = transcript_dir / f"{i:02d}.json"
        if out_json.exists():
            print(f"[2/轉文字] 段 {i:02d} 已有逐字稿，跳過")
            continue

        chunk_flac = chunks_dir / f"{i:02d}.flac"
        if not chunk_flac.exists():
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error",
                 "-ss", str(chunk_start), "-t", str(dur), "-i", str(audio_path),
                 "-ar", str(SR), "-ac", "1", str(chunk_flac)],
                check=True,
            )

        if client is None:
            client = Groq()  # 金鑰從環境變數讀，不印出

        print(f"[2/轉文字] 段 {i:02d}（{fmt_time(chunk_start)}–{fmt_time(chunk_end)}）轉文字中...")
        t0 = time.time()
        r = _groq_transcribe_bytes(client, f"{i:02d}.flac", chunk_flac.read_bytes())
        elapsed = time.time() - t0
        out_json.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        per_chunk_elapsed[f"{i:02d}"] = round(elapsed, 1)
        print(f"[2/轉文字] 段 {i:02d} 完成，{elapsed:.1f} 秒")
        time.sleep(GROQ_CALL_INTERVAL_S)

    total_elapsed = time.time() - t_total0

    sentences: list[dict] = []
    words: list[dict] = []
    for i in range(n_chunks):
        chunk_start = i * CHUNK_S
        data = json.loads((transcript_dir / f"{i:02d}.json").read_text(encoding="utf-8"))
        for j, seg in enumerate(data.get("segments", [])):
            sentences.append({
                "id": f"{i:02d}_{j:03d}",
                "start": round(chunk_start + seg["start"], 3),
                "end": round(chunk_start + seg["end"], 3),
                "text": seg.get("text", ""),
                "avg_logprob": seg.get("avg_logprob", 0.0),
            })
        for w in data.get("words", []):
            words.append({
                "start": round(chunk_start + w["start"], 3),
                "end": round(chunk_start + w["end"], 3),
                "word": w.get("word", ""),
            })
    sentences.sort(key=lambda s: s["start"])
    words.sort(key=lambda w: w["start"])
    return sentences, words, per_chunk_elapsed, total_elapsed


# ---------- 步驟 3：不用老師自錄聲音找老師 ----------

def _load_embed_model():
    from pyannote.audio import Inference, Model

    model = Model.from_pretrained("pyannote/wespeaker-voxceleb-resnet34-LM")
    return Inference(model, window="whole")


def _classify(sim: float) -> str:
    if sim >= SIM_TEACHER:
        return "老師"
    if sim <= SIM_NOT_TEACHER:
        return "不是老師"
    return "不確定"


def _step3_speaker_classify(
    audio_path: Path, workdir: Path, sentences: list[dict]
) -> tuple[list[dict], dict, float]:
    """回傳 (sentences 附上 sim/label，cluster_info，耗時)。快取到 說話者判斷.json。"""
    cache_path = workdir / "說話者判斷.json"
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        return cached["sentences"], cached["cluster_info"], 0.0

    from pyannote.core import Segment
    from pyannote.audio.core.io import Audio as PyannoteAudio
    from scipy.cluster.hierarchy import fcluster, linkage

    print("[3/找老師] 載入聲紋模型（pyannote/wespeaker-voxceleb-resnet34-LM）...")
    inference = _load_embed_model()
    file_dur = PyannoteAudio().get_duration(str(audio_path))

    t0 = time.time()
    idx_with_emb: list[int] = []
    embs: list[np.ndarray] = []
    durs: list[float] = []
    results: list[dict] = [dict(s) for s in sentences]

    print(f"[3/找老師] {len(sentences)} 句，逐句抽聲紋中...")
    for idx, s in enumerate(results):
        dur = s["end"] - s["start"]
        if dur < MIN_SEG_S:
            s["sim"] = None
            s["label"] = "太短"
            continue
        seg_s, seg_e = max(0.0, s["start"]), min(s["end"], file_dur)
        if seg_e - seg_s < 0.3:
            s["sim"] = None
            s["label"] = "太短"
            continue
        emb = inference.crop(str(audio_path), Segment(seg_s, seg_e)).reshape(-1)
        emb = _l2norm(emb)
        idx_with_emb.append(idx)
        embs.append(emb)
        durs.append(dur)

    if len(embs) < 2:
        raise RuntimeError("可抽聲紋的句子太少，無法分群，請檢查轉文字結果。")

    E = np.stack(embs, axis=0)
    Z = linkage(E, method="average", metric="cosine")
    cluster_ids = fcluster(Z, t=CLUSTER_DISTANCE_T, criterion="distance")

    cluster_seconds: dict[int, float] = {}
    for cid, d in zip(cluster_ids, durs):
        cluster_seconds[int(cid)] = cluster_seconds.get(int(cid), 0.0) + d
    teacher_cid = max(cluster_seconds.items(), key=lambda kv: kv[1])[0]

    teacher_member_embs = [e for e, c in zip(embs, cluster_ids) if int(c) == teacher_cid]
    teacher_center = _l2norm(np.mean(np.stack(teacher_member_embs, axis=0), axis=0))

    for idx, emb in zip(idx_with_emb, embs):
        sim = cosine(emb, teacher_center)
        results[idx]["sim"] = round(sim, 4)
        results[idx]["label"] = _classify(sim)

    elapsed = time.time() - t0
    total_voiced_s = sum(durs)
    teacher_seconds = cluster_seconds[teacher_cid]
    cluster_info = {
        "分群數": len(cluster_seconds),
        "各群秒數": {str(k): round(v, 1) for k, v in sorted(cluster_seconds.items(), key=lambda kv: -kv[1])},
        "老師群編號": int(teacher_cid),
        "老師群佔可比對總秒數比例": round(teacher_seconds / total_voiced_s, 4) if total_voiced_s else None,
        "老師聲紋中心": teacher_center.tolist(),
    }

    cache_path.write_text(
        json.dumps({"sentences": results, "cluster_info": cluster_info}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return results, cluster_info, elapsed


def _teacher_ref_confirmation(teacher_ref: Path, teacher_center: list[float], inference=None) -> float:
    """teacher_ref 只拿來確認，不參與判斷：算它跟老師群中心的相似度。可以傳入已經
    載入的 inference 重複使用（不然每次都重新載入模型很浪費）。"""
    from pyannote.core import Segment

    if inference is None:
        inference = _load_embed_model()
    total = audio_dur_s(teacher_ref)
    chunks = []
    t = 0.0
    while t < total:
        end = min(t + TEACHER_CHUNK_S, total)
        if end - t >= MIN_SEG_S:
            chunks.append(Segment(t, end))
        t += TEACHER_CHUNK_S
    embs = [_l2norm(inference.crop(str(teacher_ref), seg).reshape(-1)) for seg in chunks]
    emb = _l2norm(np.mean(np.stack(embs, axis=0), axis=0))
    return round(cosine(emb, np.array(teacher_center)), 4)


# ---------- 步驟 4：找候選區域＋步驟 6：排序（區域統計量不需要先切音檔，合併成一步） ----------

@dataclass
class _Region:
    start: float
    end: float
    min_sim: float
    density: float          # 每秒字數
    confidence: float       # exp(平均 avg_logprob)，0~1
    score: float
    text: str = field(default="", repr=False)


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """[(start, end), ...] 合併重疊／相接的區間，回傳依起點排序、彼此不重疊的區間。"""
    if not intervals:
        return []
    ivs = sorted(intervals, key=lambda iv: iv[0])
    merged = [list(ivs[0])]
    for s, e in ivs[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def _overlaps_any(start: float, end: float, intervals: list[tuple[float, float]]) -> bool:
    return any(start < e and s < end for s, e in intervals)


def _exclude_regions_from_sentences(
    sentences: list[dict],
    pad_s: float = EXCLUDE_PAD_S,
    labels: tuple[str, ...] = ("不是老師", "不確定"),
) -> list[tuple[float, float]]:
    """把步驟 3 判成 labels 裡任一標籤的句子前後各擴 pad_s 秒，合併重疊的區間，
    當作排除區域——這些句子附近可能混到別人的聲音，候選區域只要跟這些區域重疊
    就不列入候選池。預設 labels 含「不是老師」跟「不確定」；保底順序「放寬」那
    一步只用 labels=("不是老師",)，因為「不確定」只是相似度卡在中間、不等於
    真的是別人的聲音。"""
    raw = [
        (s["start"] - pad_s, s["end"] + pad_s)
        for s in sentences
        if s.get("label") in labels
    ]
    return _merge_intervals(raw)


def _build_scored_spans(sentences: list[dict], max_dur_s: float) -> list[_Region]:
    """從老師句子建立連續的「段落」，單一段落候選（30–75 秒）跟拼接用的短片段
    （8–20 秒）共用這套邏輯，只有長度上限不同（用 max_dur_s 控制）：用每個符合
    門檻（相似度、Groq 信心分數）的句子當起點，往後延伸到條件不再成立或超過
    max_dur_s 為止；同一段連續老師講話會因此產生很多起點相近、高度重疊的段落，
    呼叫端自己再依最短長度、排除區域、彼此不重疊去篩選／去重。這一步只用轉文字
    階段就有的統計量，不需要動到音檔，所以很快。回傳的 _Region 還沒篩最短長度、
    還沒跟排除區域比對。"""
    eligible = [
        s for s in sentences
        if s.get("label") == "老師" and s.get("sim") is not None
        and s["sim"] >= CAND_MIN_SIM and s["avg_logprob"] >= CAND_MIN_LOGPROB
    ]
    eligible.sort(key=lambda s: s["start"])

    spans: list[_Region] = []
    n_sent = len(eligible)
    for i in range(n_sent):
        span = [eligible[i]]
        for j in range(i + 1, n_sent):
            nxt = eligible[j]
            gap = nxt["start"] - span[-1]["end"]
            if gap > CAND_MAX_GAP_S:
                break
            if nxt["sim"] < CAND_MIN_SIM or nxt["avg_logprob"] < CAND_MIN_LOGPROB:
                break
            span.append(nxt)
            if nxt["end"] - span[0]["start"] > max_dur_s:
                span.pop()
                break
        seg_start = span[0]["start"]
        seg_end = span[-1]["end"]
        dur = seg_end - seg_start
        min_sim = min(s["sim"] for s in span)
        text = "".join(s["text"] for s in span)
        density = len(text) / dur if dur > 0 else 0.0
        mean_logprob = sum(s["avg_logprob"] for s in span) / len(span)
        confidence = float(np.clip(math.exp(mean_logprob), 0.0, 1.0))
        density_norm = min(density / DENSITY_REF_CPS, 1.0)
        score = (
            SCORE_W_MIN_SIM * min_sim
            + SCORE_W_DENSITY * density_norm
            + SCORE_W_CONFIDENCE * confidence
        )
        spans.append(_Region(seg_start, seg_end, min_sim, density, confidence, score, text))
    return spans


def _find_and_rank_candidates(
    sentences: list[dict],
    n: int,
    pool_size: int | None = None,
    exclude_regions: list[tuple[float, float]] | None = None,
) -> tuple[list[_Region], float]:
    """回傳依分數排序、彼此不重疊、起點相隔 >=30 秒的候選池，最多 pool_size 個
    （預設就是 n，等於只要最好的 n 個——極少數候選裁完不夠長被淘汰、需要遞補時，
    pick_reference 會傳更大的 pool_size 進來）。exclude_regions 給的話，候選
    區域只要跟任一排除區間重疊就直接不列入（不是裁切）。"""
    if pool_size is None:
        pool_size = n
    t0 = time.time()

    raw_regions = [
        r for r in _build_scored_spans(sentences, CAND_MAX_DUR_S)
        if CAND_MIN_DUR_S <= (r.end - r.start) <= CAND_MAX_DUR_S
        and not (exclude_regions and _overlaps_any(r.start, r.end, exclude_regions))
    ]
    raw_regions.sort(key=lambda r: -r.score)

    selected: list[_Region] = []
    for r in raw_regions:
        if len(selected) >= pool_size:
            break
        too_close = any(abs(r.start - s.start) < CAND_START_GAP_S for s in selected)
        overlaps = any(r.start < s.end and s.start < r.end for s in selected)
        if too_close or overlaps:
            continue
        selected.append(r)

    return selected, time.time() - t0


# ---------- 保底順序：拼接 ----------

def _should_use_combine_mode(pool_size: int, force_combine: bool = False) -> bool:
    """觸發條件：強制拼接，或單一段落候選池不到 MIN_CANDIDATES 個。"""
    return force_combine or pool_size < MIN_CANDIDATES


def _find_combine_segments(
    sentences: list[dict], exclude_regions: list[tuple[float, float]] | None = None
) -> tuple[list[_Region], float]:
    """找可以用來拼接的短老師片段：跟找單一段落候選共用 _build_scored_spans，
    只是長度改成 COMBINE_MIN_SEG_S–COMBINE_MAX_SEG_S 秒（短很多，這樣才找得到
    片段可以組），一樣會跟排除區域比對、重疊的不列入。回傳依分數排序、彼此不
    重疊的短片段池（依分數截斷到 COMBINE_POOL_CAP 個）。"""
    t0 = time.time()

    # 上限也要濾：_build_scored_spans 只保證「往後延伸」不會超過 max_dur_s，
    # 但如果單一句子自己的長度就已經超過 COMBINE_MAX_SEG_S（Groq 有時會給出
    # 一句很長、內容連貫的 segment），span 起手就會超標，要濾掉，不然這種
    # 「假的短片段」分數常常很高（句子完整、密度高），會被 _pick_combine_set
    # 選成基準，反而讓拼接湊不出東西。
    raw = [
        r for r in _build_scored_spans(sentences, COMBINE_MAX_SEG_S)
        if COMBINE_MIN_SEG_S <= (r.end - r.start) <= COMBINE_MAX_SEG_S
        and not (exclude_regions and _overlaps_any(r.start, r.end, exclude_regions))
    ]
    raw.sort(key=lambda r: -r.score)

    selected: list[_Region] = []
    for r in raw:
        if len(selected) >= COMBINE_POOL_CAP:
            break
        too_close = any(abs(r.start - s.start) < COMBINE_DEDUP_GAP_S for s in selected)
        overlaps = any(r.start < s.end and s.start < r.end for s in selected)
        if too_close or overlaps:
            continue
        selected.append(r)

    return selected, time.time() - t0


def _time_gap(a: _Region, b: _Region) -> float:
    """兩個片段之間的時間差（秒）：有重疊算 0，沒重疊算頭尾間的空隙。"""
    if a.start < b.end and b.start < a.end:
        return 0.0
    return max(a.start - b.end, b.start - a.end, 0.0)


def _pick_combine_set(available: list[_Region]) -> list[_Region] | None:
    """從短片段池挑 COMBINE_MIN_SEGMENTS–COMBINE_MAX_SEGMENTS 段組成拼接組合，
    總長度（用每段自己的原始長度粗估，實際壓縮停頓、接靜音之後會再變化，這裡
    只是抓一個合理的組合）落在 [COMBINE_TARGET_MIN_S, COMBINE_TARGET_MAX_S]。

    貪婪法：從分數最高的片段當基準開始選，之後每一步在「還沒選、不跟已選片段
    重疊、選了不會超出長度預算」的片段裡，用底下的 pick_score 排序挑一個加進
    來，直到湊到範圍內或選滿 COMBINE_MAX_SEGMENTS 段：

        pick_score = COMBINE_PICK_W_SCORE * 片段自己的分數（品質優先）
                   + COMBINE_PICK_W_CLOSE * 時間接近分數

        時間接近分數 = 1 / (1 + 跟已選片段最近的時間差 / COMBINE_TIME_SPREAD_REF_S)

    時間接近分數的用意：同一段時間附近錄的收音條件（環境音、麥克風距離）比較
    一致，拼接起來的聲音特質差異會比較小。

    片段不夠（COMBINE_MIN_SEGMENTS 段都湊不到）就回傳 None，呼叫端會用更寬鬆
    的排除區域再試一次。
    """
    if not available:
        return None

    ranked = sorted(available, key=lambda s: -s.score)
    chosen = [ranked[0]]
    total = ranked[0].end - ranked[0].start

    while len(chosen) < COMBINE_MAX_SEGMENTS:
        remaining_budget = COMBINE_TARGET_MAX_S - total
        if remaining_budget < COMBINE_MIN_SEG_S:
            break  # 預算不夠再塞一段最短的了
        pool = [
            s for s in ranked
            if s not in chosen
            and (s.end - s.start) <= remaining_budget
            and not any(s.start < c.end and c.start < s.end for c in chosen)  # 不跟已選重疊
        ]
        if not pool:
            break

        def pick_score(s: _Region) -> float:
            gap = min(_time_gap(s, c) for c in chosen)
            closeness = 1.0 / (1.0 + gap / COMBINE_TIME_SPREAD_REF_S)
            return COMBINE_PICK_W_SCORE * s.score + COMBINE_PICK_W_CLOSE * closeness

        best = max(pool, key=pick_score)
        chosen.append(best)
        total += best.end - best.start
        if len(chosen) >= COMBINE_MIN_SEGMENTS and COMBINE_TARGET_MIN_S <= total <= COMBINE_TARGET_MAX_S:
            break

    if len(chosen) < COMBINE_MIN_SEGMENTS:
        return None

    chosen.sort(key=lambda s: s.start)  # 依時間順序排，接起來敘述才順
    return chosen


# ---------- 步驟 5a：對齊第一個字／最後一個字（裁掉字前字後的笑聲、雜音） ----------

def _word_trim_bounds(words: list[dict], region_start: float, region_end: float) -> tuple[float, float]:
    """把候選區域的起訖對齊到第一個字的開始、最後一個字的結束（前後留 WORD_PAD_S
    秒），這樣字前字後的笑聲、雜音（不會被轉成「字」）就會被排除在切出來的參考音
    之外。找不到任何字落在區域內（理論上不會發生，區域本來就是從有轉文字的句子
    推出來的）就原樣回傳，不裁。"""
    in_range = [w for w in words if w["end"] > region_start and w["start"] < region_end]
    if not in_range:
        return region_start, region_end
    first_start = min(w["start"] for w in in_range)
    last_end = max(w["end"] for w in in_range)
    new_start = max(region_start, first_start - WORD_PAD_S)
    new_end = min(region_end, last_end + WORD_PAD_S)
    if new_end <= new_start:
        return region_start, region_end
    return new_start, new_end


# ---------- 步驟 5b：壓縮停頓、切段（make_ref 演算法原樣搬，不改邏輯） ----------

def _compress_pauses_and_cut(x: np.ndarray, sr: int = SR) -> tuple[np.ndarray, float]:
    """壓縮長停頓、在停頓處切到 MAX_S 秒以內。核心邏輯照 spikes/refclip/make_ref.py
    原樣搬，沒有改動；唯一的差異是原本假設一定找得到 <=MAX_S 的停頓切點會直接
    當掉，這裡多包一層保底（真的找不到就硬切在 MAX_S），因為自動化跑很多段時
    不能讓單一候選讓整個流程掛掉。回傳 (壓縮後切好的音訊, 對應原始音訊的秒數位置)。
    """
    fr = int(FRAME * sr)
    n = len(x) // fr
    if n == 0:
        return x.copy(), len(x) / sr

    db = 20 * np.log10(np.sqrt((x[: n * fr].reshape(n, fr) ** 2).mean(1) + 1e-12))
    quiet = db < np.percentile(db, 90) - 30  # 比說話音量低 30dB 算停頓

    keep = np.ones(n, bool)
    i = 0
    while i < n:
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j < n and quiet[j]:
            j += 1
        if (j - i) * FRAME > LONG_PAUSE:  # 長停頓只留中間 KEEP_PAUSE 秒
            k = int(KEEP_PAUSE / FRAME)
            keep[i + k // 2: j - (k - k // 2)] = False
        i = j

    fade = np.linspace(0, 1, int(0.01 * sr), dtype=np.float32)
    pieces, ends, t, i = [], [], 0.0, 0
    while i < n:
        if not keep[i]:
            i += 1
            continue
        j = i
        while j < n and keep[j]:
            j += 1
        seg = x[i * fr: j * fr].copy()
        if len(seg) > 2 * len(fade):
            seg[: len(fade)] *= fade
            seg[-len(fade):] *= fade[::-1]  # 接點淡入淡出，避免爆音
        pieces.append(seg)
        t += len(seg) / sr
        ends.append((t, j * FRAME))
        i = j

    if not pieces:
        return np.zeros(0, dtype=np.float32), 0.0

    y = np.concatenate(pieces)
    candidates = [e for e in ends if e[0] <= MAX_S]
    if not candidates:
        # 保底：壓縮完還是超過 MAX_S 且找不到停頓切點，直接硬切
        return y[: int(MAX_S * sr)], min(len(x) / sr, MAX_S)
    cut, orig = max(candidates, key=lambda e: e[0])  # 在停頓處切
    return y[: int(cut * sr)], orig


def _make_reference_clip(video: Path, region_start: float, region_end: float) -> dict:
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-ss", str(region_start), "-to", str(region_end),
         "-i", str(video), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    x = np.frombuffer(raw, np.float32).copy()
    y, orig_used = _compress_pauses_and_cut(x, SR)
    return {
        "audio": y,
        "region_start": region_start,
        "region_end": region_end,
        "used_start": region_start,
        "used_end": region_start + orig_used,
        "original_duration": orig_used,
        "compressed_duration": len(y) / SR,
    }


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2) + 1e-12))


def _assemble_pieces(piece_audios: list[np.ndarray], sr: int = SR) -> np.ndarray:
    """把已經處理好（字對齊裁切＋壓縮停頓）的小段音訊接成拼接參考音：
    音量用 RMS 對齊到第一段（後面每段乘一個係數，讓 RMS 跟第一段一樣）、
    每段頭尾各 COMBINE_FADE_S 秒淡入淡出、段與段之間插入 COMBINE_SILENCE_S
    秒靜音。純數字運算，不碰音檔／模型，方便單獨測試。"""
    if not piece_audios:
        return np.zeros(0, dtype=np.float32)

    fade_n = int(COMBINE_FADE_S * sr)
    fade = np.linspace(0, 1, fade_n, dtype=np.float32) if fade_n > 0 else np.array([], dtype=np.float32)
    ref_rms = _rms(piece_audios[0])

    processed = []
    for y in piece_audios:
        y = np.asarray(y, dtype=np.float32).copy()
        rms = _rms(y)
        if rms > 1e-6 and ref_rms > 1e-6:
            y = y * (ref_rms / rms)
        if len(y) > 2 * fade_n and fade_n > 0:
            y[:fade_n] *= fade
            y[-fade_n:] *= fade[::-1]
        processed.append(y)

    silence = np.zeros(int(COMBINE_SILENCE_S * sr), dtype=np.float32)
    parts: list[np.ndarray] = []
    for i, y in enumerate(processed):
        if i > 0:
            parts.append(silence)
        parts.append(y)
    return np.concatenate(parts)


def _build_combined_clip(video: Path, words: list[dict], pieces: list[_Region]) -> dict:
    """把選好的 2–3 個短片段（_pick_combine_set 的結果）各自字對齊裁頭尾、壓縮
    停頓，再用 _assemble_pieces 接起來。回傳組好的音訊跟每一小段實際用到的
    原片時間（給候選.json 的「小段」欄位用）。"""
    piece_audios: list[np.ndarray] = []
    piece_meta: list[dict] = []
    for seg in pieces:
        wstart, wend = _word_trim_bounds(words, seg.start, seg.end)
        raw = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-ss", str(wstart), "-to", str(wend),
             "-i", str(video), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
            capture_output=True, check=True,
        ).stdout
        x = np.frombuffer(raw, np.float32).copy()
        y, orig_used = _compress_pauses_and_cut(x, SR)
        piece_audios.append(y)
        piece_meta.append({
            "原片起訖": [round(wstart, 2), round(wstart + orig_used, 2)],
            "長度": round(len(y) / SR, 2),
            "文字": seg.text,
        })

    audio = _assemble_pieces(piece_audios, SR)
    return {"audio": audio, "compressed_duration": len(audio) / SR, "pieces": piece_meta}


# ---------- 步驟 5c：細看聲紋抓漏 ----------

def _scan_speaker_windows(
    audio_path: Path, inference, teacher_center: np.ndarray, start: float, end: float
) -> list[dict]:
    """在 [start, end]（原片絕對時間）內，用 WINDOW_S 秒窗、WINDOW_STEP_S 秒步進，
    逐窗抽聲紋跟老師聲紋中心比對相似度。安靜的窗（比這段裡最大聲的地方低
    WINDOW_SILENCE_DB_DROP 以上）跳過不比——安靜的時候聲紋不穩定，比出來的數字
    沒有意義，硬要比只會製造假警報。回傳每個真的比對到的窗：{start, end, sim}。"""
    from pyannote.core import Segment

    if end - start < WINDOW_S:
        return []

    with sf.SoundFile(str(audio_path)) as f:
        sr = f.samplerate
        f.seek(int(start * sr))
        x = f.read(int(round((end - start) * sr)), dtype="float32")
    if len(x) == 0:
        return []

    fr = int(FRAME * sr)
    n = len(x) // fr
    if n == 0:
        return []
    db = 20 * np.log10(np.sqrt((x[: n * fr].reshape(n, fr) ** 2).mean(1) + 1e-12))
    quiet_thr = np.percentile(db, 90) - WINDOW_SILENCE_DB_DROP

    starts = list(np.arange(start, end - WINDOW_S + 1e-6, WINDOW_STEP_S))
    if not starts or starts[-1] + WINDOW_S < end - 0.05:
        starts.append(max(start, end - WINDOW_S))  # 補最後一個貼齊尾端的窗，尾端才看得到

    windows: list[dict] = []
    seen: set[float] = set()
    for raw_start in starts:
        w_start = round(float(raw_start), 3)
        if w_start in seen:
            continue
        seen.add(w_start)
        w_end = min(w_start + WINDOW_S, end)
        i0 = int(round((w_start - start) / FRAME))
        i1 = int(round((w_end - start) / FRAME))
        seg_db = db[i0:i1]
        if seg_db.size == 0 or np.mean(seg_db) < quiet_thr:
            continue  # 靜音窗，不比
        emb = inference.crop(str(audio_path), Segment(w_start, w_end)).reshape(-1)
        emb = _l2norm(emb)
        sim = cosine(emb, teacher_center)
        windows.append({"start": round(w_start, 2), "end": round(w_end, 2), "sim": round(float(sim), 4)})
    return windows


def _classify_window_issues(
    windows: list[dict], region_start: float, region_end: float
) -> tuple[str, float, float]:
    """看細看聲紋掃出來的窗，判斷問題在哪裡、建議怎麼裁。回傳
    (問題位置, 建議新起點, 建議新終點)。
    問題位置：
      "無"    沒有窗低於門檻（或根本沒有窗可比），原樣回傳，不裁。
      "頭"／"尾"／"頭尾"  低於門檻的窗只出現在最前面／最後面／頭尾都有，
                裁掉那段之後可以繼續用（新起訖點就是最後一個問題窗的邊界）。
      "中間"  低於門檻的窗落在頭尾裁完之後還剩下的中段，裁不掉，整個候選淘汰。
    """
    if not windows:
        return "無", region_start, region_end

    ordered = sorted(windows, key=lambda w: w["start"])
    bad = [w for w in ordered if w["sim"] < WINDOW_SIM_THRESHOLD]
    if not bad:
        return "無", region_start, region_end

    head_bad_end = None
    for w in ordered:
        if w["sim"] < WINDOW_SIM_THRESHOLD:
            head_bad_end = w["end"]
        else:
            break

    tail_bad_start = None
    for w in reversed(ordered):
        if w["sim"] < WINDOW_SIM_THRESHOLD:
            tail_bad_start = w["start"]
        else:
            break

    new_start = region_start if head_bad_end is None else head_bad_end
    new_end = region_end if tail_bad_start is None else tail_bad_start

    if new_end <= new_start:
        return "中間", region_start, region_end  # 幾乎整段都有問題，裁不出東西

    interior_bad = [w for w in bad if w["start"] >= new_start and w["end"] <= new_end]
    if interior_bad:
        return "中間", region_start, region_end

    if head_bad_end is not None and tail_bad_start is not None:
        return "頭尾", new_start, new_end
    if head_bad_end is not None:
        return "頭", new_start, region_end
    return "尾", region_start, new_end


# ---------- 步驟 7：前 n 名再轉一次文字 ----------

def _transcribe_candidate_text(client, wav_path: Path) -> str:
    import opencc

    data = wav_path.read_bytes()
    r = _groq_transcribe_bytes(client, wav_path.name, data)
    text = r.get("text", "").strip()
    converter = opencc.OpenCC("s2twp")
    return converter.convert(text)


# ---------- 步驟 8：輸出 ----------

def _build_html(candidates: list[dict]) -> str:
    """靜態試聽頁：只顯示第 1 名，按「換一段」切下一名；相對路徑音檔，手機可看。
    逐字稿放進文字框可編輯；不做伺服器存檔，改好之後照頁面上的說明告訴
    Claude Code『用第 N 段』。"""
    def _time_label(c: dict) -> str:
        if c.get("type") == "拼接":
            return "、".join(
                f"{_mmss(p['原片起訖'][0])}–{_mmss(p['原片起訖'][1])}" for p in c["小段"]
            )
        return f"{_mmss(c['used_start'])}–{_mmss(c['used_end'])}"

    payload = json.dumps(
        [
            {
                "rank": c["rank"],
                "audio": f"候選{c['rank']}.wav",
                "time_label": _time_label(c),
                "text": c["transcript"],
            }
            for c in candidates
        ],
        ensure_ascii=False,
    )
    n = len(candidates)
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>老師參考音試聽</title>
<style>
  body {{ font-family: -apple-system, "PingFang TC", sans-serif; max-width: 640px;
         margin: 24px auto; padding: 0 16px; line-height: 1.6; color: #222; }}
  h1 {{ font-size: 20px; }}
  .badge {{ color: #666; font-size: 14px; margin-bottom: 8px; }}
  audio {{ width: 100%; margin: 12px 0; }}
  textarea {{ width: 100%; min-height: 140px; font-size: 16px; padding: 8px;
              box-sizing: border-box; border: 1px solid #ccc; border-radius: 6px; }}
  .btnrow {{ display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }}
  button {{ font-size: 15px; padding: 10px 14px; border-radius: 6px; border: 1px solid #999;
            background: #f5f5f5; }}
  button.primary {{ background: #333; color: #fff; border-color: #333; }}
  .hint {{ font-size: 13px; color: #666; margin-top: 8px; }}
  .done {{ color: #b30000; font-weight: bold; }}
  .notice {{ background: #fff6e0; border: 1px solid #e8d9a8; border-radius: 6px;
             padding: 10px 12px; font-size: 14px; color: #6b5a1e; margin: 12px 0; }}
</style>
</head>
<body>
<h1>老師參考音試聽</h1>
<div class="notice">參考音裡如果有雜音、笑聲、咳嗽，或別人的回應（例如「嗯」「對」），都不適合，請按「換一段」。</div>
<div class="badge" id="badge">第 1 段／共 {n} 段</div>
<div id="timelabel"></div>
<audio id="player" controls preload="none"></audio>
<textarea id="transcript"></textarea>
<div class="btnrow">
  <button id="reject">這段有別人的聲音，換一段</button>
  <button id="copy" class="primary">複製逐字稿</button>
</div>
<div class="hint" id="hint"></div>
<div id="doneMsg" class="done" style="display:none">5 段都聽過了，請回報宇軒。</div>

<script>
const candidates = {payload};
let i = 0;

function render() {{
  if (i >= candidates.length) {{
    document.getElementById('badge').textContent = '';
    document.getElementById('player').style.display = 'none';
    document.getElementById('timelabel').textContent = '';
    document.getElementById('transcript').style.display = 'none';
    document.getElementById('reject').style.display = 'none';
    document.getElementById('copy').style.display = 'none';
    document.getElementById('hint').textContent = '';
    document.getElementById('doneMsg').style.display = 'block';
    return;
  }}
  const c = candidates[i];
  document.getElementById('badge').textContent = `第 ${{c.rank}} 段／共 ${{candidates.length}} 段`;
  document.getElementById('timelabel').textContent = `原片時間：${{c.time_label}}`;
  document.getElementById('player').src = c.audio;
  document.getElementById('transcript').value = c.text;
  document.getElementById('hint').textContent =
    `改好後，告訴 Claude Code「用第 ${{c.rank}} 段」並貼上改好的逐字稿，就會存成 ref.wav／ref.txt`;
}}

document.getElementById('reject').addEventListener('click', () => {{ i += 1; render(); }});
document.getElementById('copy').addEventListener('click', async () => {{
  const text = document.getElementById('transcript').value;
  try {{
    await navigator.clipboard.writeText(text);
    alert('已複製逐字稿');
  }} catch (e) {{
    alert('複製失敗，請手動選取文字框內容複製');
  }}
}});

render();
</script>
</body>
</html>
"""


# ---------- 主流程 ----------

def pick_reference(
    video: str | Path,
    workdir: str | Path,
    n: int = 5,
    teacher_ref: str | Path | None = None,
    exclude_regions: list[tuple[float, float]] | None = None,
    window_scan: bool = False,
    force_combine: bool = False,
    ref_dir_name: str = REF_DIR_NAME,
) -> dict:
    """exclude_regions：外部（例如「聲音重疊檢查」步驟）算好的排除區間
    [(start, end), ...]，原片絕對秒數；跟步驟 3 自己推出來的排除區域（「不是
    老師」／「不確定」的句子前後擴 EXCLUDE_PAD_S 秒）合併一起用。
    window_scan：要不要跑步驟 5c 逐窗細看聲紋，預設 False（不跑、不載入模型，
    改靠排除區域擋混到別人聲音的候選）。
    force_combine：不管單一段落候選夠不夠，強制只產拼接候選（測試用）。
    ref_dir_name：輸出子資料夾名稱，預設「參考音」；抽音／轉文字／找老師這些
    共用快取還是放在 workdir 底下，跟輸出資料夾分開，換個名字不會影響快取。"""
    video = Path(video).expanduser()
    workdir = Path(workdir).expanduser()
    teacher_ref = Path(teacher_ref).expanduser() if teacher_ref else None
    ref_dir = _ref_dir(workdir, ref_dir_name)
    ref_dir.mkdir(parents=True, exist_ok=True)

    t_grand0 = time.time()
    elapsed: dict[str, object] = {}

    print("[1/抽音] ffmpeg 轉 16kHz 單聲道 flac...")
    audio_path, t1 = _step1_extract_audio(video, workdir)
    elapsed["1_抽音"] = round(t1, 1)
    print(f"[1/抽音] 完成，{t1:.1f} 秒" if t1 else "[1/抽音] 已有快取，略過")

    total_dur = audio_dur_s(audio_path)
    print(f"[主流程] 影片音訊長度：{fmt_time(total_dur)}")

    sentences, words, per_chunk, t2 = _step2_transcribe(audio_path, workdir)
    elapsed["2_轉文字_總計"] = round(t2, 1)
    elapsed["2_轉文字_各段"] = per_chunk
    print(f"[2/轉文字] 共 {len(sentences)} 句、{len(words)} 個字，總耗時 {t2:.1f} 秒")

    sentences, cluster_info, t3 = _step3_speaker_classify(audio_path, workdir, sentences)
    elapsed["3_找老師"] = round(t3, 1)
    print(f"[3/找老師] 老師群佔比 {cluster_info['老師群佔可比對總秒數比例']}，耗時 {t3:.1f} 秒"
          if t3 else "[3/找老師] 已有快取，略過")

    # 細看聲紋（步驟 5c，預設關閉）跟 teacher_ref 確認都要用聲紋模型；只在真的
    # 需要的時候才載入（步驟 3 如果吃快取就不會載入模型），兩邊共用同一個
    # inference，不重複載入。
    inference = _load_embed_model() if (teacher_ref is not None or window_scan) else None
    teacher_center = np.array(cluster_info["老師聲紋中心"])

    teacher_confirmation = None
    if teacher_ref is not None:
        t_confirm0 = time.time()
        teacher_confirmation = _teacher_ref_confirmation(teacher_ref, cluster_info["老師聲紋中心"], inference)
        elapsed["3b_teacher_ref確認"] = round(time.time() - t_confirm0, 1)
        print(f"[3/找老師] teacher_ref 確認相似度：{teacher_confirmation}")

    sentence_exclude = _exclude_regions_from_sentences(sentences)
    all_exclude = _merge_intervals(sentence_exclude + list(exclude_regions or []))
    exclude_total_s = sum(e - s for s, e in all_exclude)
    print(f"[3.5/排除區域] {len(all_exclude)} 段、共 {exclude_total_s:.1f} 秒"
          f"（佔全片 {exclude_total_s / total_dur * 100:.1f}%）")

    pool_size = max(n * CAND_POOL_MULTIPLIER, CAND_POOL_MIN)
    pool, t46 = _find_and_rank_candidates(sentences, n, pool_size=pool_size, exclude_regions=all_exclude)
    elapsed["4+6_找候選區域＋排序"] = round(t46, 2)
    print(f"[4+6/找候選區域] 候選池 {len(pool)} 個（目標選出 {n} 個）")

    # ---------- 保底順序：候選不夠（或強制測試）就改拼接 ----------
    mode = "平常"
    combine_pool: list[_Region] = []
    t5d_total = 0.0  # 拼接找片段
    t5e_total = 0.0  # 拼接組裝

    if _should_use_combine_mode(len(pool), force_combine):
        mode = "拼接"
        combine_pool, t_find = _find_combine_segments(sentences, exclude_regions=all_exclude)
        t5d_total += t_find
        print(f"[5d/拼接找片段] 單一段落候選只有 {len(pool)} 個（{'強制拼接' if force_combine else f'不到 {MIN_CANDIDATES} 個'}），"
              f"改試拼接：嚴格排除下找到 {len(combine_pool)} 個短片段")
        if _pick_combine_set(combine_pool) is None:
            mode = "放寬"
            relaxed_exclude = _merge_intervals(
                _exclude_regions_from_sentences(sentences, labels=("不是老師",)) + list(exclude_regions or [])
            )
            combine_pool, t_find2 = _find_combine_segments(sentences, exclude_regions=relaxed_exclude)
            t5d_total += t_find2
            print(f"[5d/拼接找片段] 嚴格排除湊不到 {COMBINE_MIN_SEGMENTS} 段，放寬「不確定」不算排除後"
                  f"找到 {len(combine_pool)} 個短片段")

    from groq import Groq
    client = None

    t5a_total = 0.0  # 字對齊裁切
    t5b_total = 0.0  # 壓縮停頓、切段
    t5c_total = 0.0  # 細看聲紋掃描
    t7_total = 0.0

    attempts_out: list[dict] = []
    candidates_out: list[dict] = []  # 只放入選的（rank 1..n）
    rank = 0

    if mode in ("拼接", "放寬"):
        remaining = list(combine_pool)
        attempt_i = 0
        while rank < n:
            attempt_i += 1
            combo = _pick_combine_set(remaining)
            if combo is None:
                print(f"[5/拼接] 短片段池用完，只選到 {rank} 個（目標 {n} 個）")
                break
            used_starts = {seg.start for seg in combo}
            remaining = [s for s in remaining if s.start not in used_starts]

            t5e_0 = time.time()
            clip = _build_combined_clip(video, words, combo)
            t5e_total += time.time() - t5e_0

            rank += 1
            wav_path = ref_dir / f"候選{rank}.wav"
            txt_path = ref_dir / f"候選{rank}.txt"
            sf.write(str(wav_path), clip["audio"], SR)

            import opencc
            raw_text = "。".join(seg.text.strip() for seg in combo if seg.text.strip())
            transcript = opencc.OpenCC("s2twp").convert(raw_text)
            txt_path.write_text(transcript, encoding="utf-8")

            attempt_record = {
                "嘗試順序": attempt_i,
                "狀態": "入選",
                "名次": rank,
                "淘汰原因": None,
                "type": "拼接",
                "拼接模式": mode,
                "小段": clip["pieces"],
                "compressed_duration": round(clip["compressed_duration"], 2),
                "字數": len(transcript),
                "transcript": transcript,
                "score": round(sum(s.score for s in combo) / len(combo), 4),
            }
            attempts_out.append(attempt_record)
            candidates_out.append({**attempt_record, "rank": rank})

            pieces_desc = "、".join(
                f"{fmt_time(p['原片起訖'][0])}–{fmt_time(p['原片起訖'][1])}" for p in clip["pieces"]
            )
            print(f"[5+7/候選 {rank}（拼接／{mode}）] 完成：{pieces_desc}，"
                  f"共 {clip['compressed_duration']:.1f} 秒")

        elapsed["5d_拼接找片段"] = round(t5d_total, 2)
        elapsed["5e_拼接組裝"] = round(t5e_total, 2)

    else:
        # ---------- 平常：現行單一連續段落做法 ----------
        for attempt_i, region in enumerate(pool, start=1):
            if rank >= n:
                break

            t5a_0 = time.time()
            wstart, wend = _word_trim_bounds(words, region.start, region.end)
            t5a_total += time.time() - t5a_0

            fstart, fend = wstart, wend
            min_window_sim = None
            status, reject_reason = "入選", None

            if window_scan:
                t5c_0 = time.time()
                scan_windows = _scan_speaker_windows(audio_path, inference, teacher_center, wstart, wend)
                t5c_total += time.time() - t5c_0
                issue, fstart, fend = _classify_window_issues(scan_windows, wstart, wend)
                min_window_sim = min((w["sim"] for w in scan_windows), default=None)
                if issue == "中間":
                    status = "淘汰"
                    reject_reason = "細看聲紋在區域中間發現相似度過低的窗（可能混入別人的聲音），裁頭尾解決不了"
                    fstart, fend = wstart, wend  # 只是記錄用，沒有真的裁切

            clip = None
            if status == "入選":
                t5b_0 = time.time()
                clip = _make_reference_clip(video, fstart, fend)
                t5b_total += time.time() - t5b_0
                if clip["compressed_duration"] < MIN_CLIP_AFTER_TRIM_S:
                    status = "淘汰"
                    reject_reason = (
                        f"裁掉問題頭尾後，壓縮出來只剩 {clip['compressed_duration']:.1f} 秒，"
                        f"不到 {MIN_CLIP_AFTER_TRIM_S:.0f} 秒下限"
                    )
                    clip = None

            attempt_record = {
                "嘗試順序": attempt_i,
                "狀態": status,
                "名次": None,
                "淘汰原因": reject_reason,
                "type": "單一段落",
                "region_start": round(region.start, 2), "region_end": round(region.end, 2),
                "字對齊起訖": [round(wstart, 2), round(wend, 2)],
                "最終起訖": [round(fstart, 2), round(fend, 2)],
                "頭部裁切秒數": round(fstart - region.start, 2),
                "尾部裁切秒數": round(region.end - fend, 2),
                "最低窗相似度": round(min_window_sim, 4) if min_window_sim is not None else None,
                "min_sim": round(region.min_sim, 4), "density": round(region.density, 3),
                "confidence": round(region.confidence, 4), "score": round(region.score, 4),
            }

            if status == "淘汰":
                print(f"[5/候選（嘗試 {attempt_i}）] 淘汰（原片 {fmt_time(region.start)}–{fmt_time(region.end)}）："
                      f"{reject_reason}")
                attempts_out.append(attempt_record)
                continue

            rank += 1
            wav_path = ref_dir / f"候選{rank}.wav"
            txt_path = ref_dir / f"候選{rank}.txt"
            sf.write(str(wav_path), clip["audio"], SR)

            print(f"[7/候選 {rank}] Groq 轉文字中...")
            if client is None:
                client = Groq()
            t7_0 = time.time()
            transcript = _transcribe_candidate_text(client, wav_path)
            time.sleep(GROQ_CALL_INTERVAL_S)
            t7_total += time.time() - t7_0
            txt_path.write_text(transcript, encoding="utf-8")

            attempt_record["名次"] = rank
            attempt_record.update({
                "used_start": round(clip["used_start"], 2), "used_end": round(clip["used_end"], 2),
                "original_duration": round(clip["original_duration"], 2),
                "compressed_duration": round(clip["compressed_duration"], 2),
                "字數": len(transcript), "transcript": transcript,
            })
            attempts_out.append(attempt_record)
            candidates_out.append({**attempt_record, "rank": rank})

            trim_note = ""
            if attempt_record["頭部裁切秒數"] > 0.05:
                trim_note += f"，頭裁 {attempt_record['頭部裁切秒數']:.1f} 秒"
            if attempt_record["尾部裁切秒數"] > 0.05:
                trim_note += f"，尾裁 {attempt_record['尾部裁切秒數']:.1f} 秒"
            print(f"[5+7/候選 {rank}] 完成：{fmt_time(clip['used_start'])}–{fmt_time(clip['used_end'])}，"
                  f"壓縮後 {clip['compressed_duration']:.1f} 秒，最低窗相似度 "
                  f"{attempt_record['最低窗相似度']}{trim_note}")

        if rank < n:
            print(f"[5+7/候選] 候選池用完，只選到 {rank} 個（目標 {n} 個）")

    elapsed["5a_字對齊裁切"] = round(t5a_total, 2)
    elapsed["5b_壓縮停頓切段"] = round(t5b_total, 2)
    elapsed["5c_細看聲紋掃描"] = round(t5c_total, 1)
    elapsed.setdefault("5d_拼接找片段", round(t5d_total, 2))
    elapsed.setdefault("5e_拼接組裝", round(t5e_total, 2))
    elapsed["7_候選轉文字"] = round(t7_total, 1)

    (ref_dir / "候選.json").write_text(
        json.dumps(attempts_out, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    html = _build_html(candidates_out)
    html_path = ref_dir / "試聽.html"
    html_path.write_text(html, encoding="utf-8")

    t_grand = time.time() - t_grand0
    elapsed["總耗時"] = round(t_grand, 1)

    record = {
        "video": str(video),
        "workdir": str(workdir),
        "elapsed": elapsed,
        "老師群佔可比對總秒數比例": cluster_info["老師群佔可比對總秒數比例"],
        "各群秒數": cluster_info["各群秒數"],
        "teacher_ref確認相似度": teacher_confirmation,
        "模式": mode,
        "排除區域數": len(all_exclude),
        "排除區域總秒數": round(exclude_total_s, 1),
        "排除區域佔全片比例": round(exclude_total_s / total_dur, 4) if total_dur else None,
        "候選池大小": len(pool),
        "嘗試次數": len(attempts_out),
        "淘汰數": sum(1 for a in attempts_out if a["狀態"] == "淘汰"),
        "候選數": len(candidates_out),
        "選定名次": None,
    }
    (ref_dir / "挑選紀錄.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    print(f"\n[完成] 共花費 {t_grand:.1f} 秒，輸出於 {ref_dir}")
    print(f"[完成] 試聽頁：{html_path}")

    return {**record, "candidates": candidates_out, "html_path": str(html_path)}


def finalize_reference(workdir: str | Path, rank: int, transcript_text: str) -> dict:
    """夥伴聽完、逐字稿修好之後呼叫：把候選{rank}.wav 存成 ref.wav，逐字稿存成 ref.txt。"""
    workdir = Path(workdir).expanduser()
    ref_dir = _ref_dir(workdir)
    src_wav = ref_dir / f"候選{rank}.wav"
    if not src_wav.exists():
        raise FileNotFoundError(f"找不到 {src_wav}，先跑過 pick_reference 才有候選音檔。")

    dst_wav = ref_dir / "ref.wav"
    dst_txt = ref_dir / "ref.txt"
    shutil.copyfile(src_wav, dst_wav)
    dst_txt.write_text(transcript_text, encoding="utf-8")

    record_path = ref_dir / "挑選紀錄.json"
    record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {}
    record["選定名次"] = rank
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[完成] 已存成 {dst_wav} 與 {dst_txt}")
    return {"ref_wav": str(dst_wav), "ref_txt": str(dst_txt), "rank": rank}
