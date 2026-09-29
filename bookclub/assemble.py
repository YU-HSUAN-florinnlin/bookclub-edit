"""流程第 5 步（三）：把生成的聲音、消音放回原本的時間，組成一條新的聲音軌。

開發計畫 03 §2.5：所有「換聲音」動作都在原始時間軸上做，產出一條跟原片等長
的新聲音軌；刪除段落與停格留到最後一次套用（還沒做）。

- 聲音從**原片影片**重新抽（48kHz 單聲道，存成 `輸出/原聲音軌.wav`），不用分析用的 16kHz
  `audio.flac`；只讀寫有動到的段落，整支影片不放進記憶體
- 換聲音：生成檔（24kHz）轉成 48kHz、裁補成剛好等於時間格，音量對齊原本那一段，
  接縫前後各 10 毫秒交叉淡入淡出
- 消音：墊環境底噪——在前後 20 秒內找最安靜的 0.5 秒（沒人說話的地方），重複鋪滿
- 兩筆重疊時，時間長的（整句換掉）蓋過短的（名字消音）

產出（工作區 `輸出/`）：
- `新聲音軌.wav`：跟原片等長，可以直接交給剪輯軟體換掉原本的聲音
- `處理前後/`：每一筆前後各 2 秒的「處理前」「處理後」試聽檔，給第 5 步成品檢查
- `處理前後.html`：試聽頁（只放代號後的文字，不放本名）
- `生成/剪輯決策.json`：這次套用了哪些動作
"""

from __future__ import annotations

import html
import subprocess
from pathlib import Path

import numpy as np

from bookclub import workdir as wd

SR = 48000
FADE_S = 0.01
ROOM_SEARCH_S = 20.0
ROOM_WIN_S = 0.5
CONTEXT_S = 2.0
ACTIVE_DB = -40.0   # 算音量時只看比最大聲低不到 40 dB 的音框（講話的部分）


def out_dir(workdir: Path) -> Path:
    return workdir / "輸出"


def edl_path(workdir: Path) -> Path:
    return workdir / "生成" / "剪輯決策.json"


# ---------- 剪輯決策（純函式） ----------

def build_edl(plan: dict, teacher_log: dict | None) -> tuple[list[dict], list[str]]:
    """名字處理計畫＋老師生成紀錄 → 剪輯決策清單（依時間排序、已處理重疊）與警告。"""
    records = {r["id"]: r for r in (teacher_log or {}).get("句子", [])}
    edits, warnings = [], []
    for g in plan.get("生成", []):
        r = records.get(g["id"])
        if not r or not r.get("放回時間格"):
            warnings.append(f"{g['id']} 還沒生成，這次先不放")
            continue
        edits.append({
            "類型": "換聲音", "start": g["slot"][0], "end": g["slot"][1], "檔案": r["放回時間格"]["檔案"],
            "文字": g["text"], "候選": g["候選"], "要人聽": r.get("要人聽", False), "生成編號": g["id"],
        })
    for m in plan.get("消音", []):
        edits.append({"類型": "消音", "start": m["start"], "end": m["end"], "候選": [m["候選"]]})

    # 重疊：長的優先
    edits.sort(key=lambda e: -(e["end"] - e["start"]))
    kept: list[dict] = []
    for e in edits:
        if any(e["start"] < k["end"] and k["start"] < e["end"] for k in kept):
            warnings.append(f"候選 {e['候選']} 跟別筆重疊，被較長的那筆蓋過")
            continue
        kept.append(e)
    kept.sort(key=lambda e: e["start"])
    return kept, warnings


# ---------- 聲音處理（純函式） ----------

def _frame_rms(x: np.ndarray, frame: int) -> np.ndarray:
    n = len(x) // frame
    if n == 0:
        return np.array([np.sqrt(np.mean(x.astype(np.float64) ** 2))]) if len(x) else np.zeros(1)
    return np.sqrt(np.mean(x[: n * frame].reshape(n, frame).astype(np.float64) ** 2, axis=1))


def active_rms(x: np.ndarray, sr: int = SR) -> float:
    """講話部分的音量：只算不太安靜的音框。"""
    rms = _frame_rms(x, max(1, int(sr * 0.02)))
    if rms.max() <= 0:
        return 0.0
    loud = rms[20 * np.log10(rms / rms.max() + 1e-12) > ACTIVE_DB]
    return float(np.sqrt(np.mean(loud ** 2))) if len(loud) else 0.0


def match_loudness(clip: np.ndarray, ref: np.ndarray, sr: int = SR, max_gain: float = 8.0) -> np.ndarray:
    """把 clip 的講話音量調到跟 ref 一樣（最多放大 8 倍，避免把底噪放大）。"""
    a, b = active_rms(clip, sr), active_rms(ref, sr)
    if a <= 0 or b <= 0:
        return clip
    return (clip * min(b / a, max_gain)).astype(np.float32)


def fit_length(clip: np.ndarray, n: int) -> np.ndarray:
    if len(clip) >= n:
        return clip[:n]
    return np.concatenate([clip, np.zeros(n - len(clip), dtype=np.float32)])


def splice(y: np.ndarray, s: int, clip: np.ndarray, sr: int = SR) -> None:
    """把 clip 放進 y[s:s+len(clip)]，頭尾各 10 毫秒跟原本的聲音交叉淡入淡出。"""
    n = len(clip)
    f = min(int(sr * FADE_S), n // 2)
    seg = clip.astype(np.float32).copy()
    if f > 0:
        ramp = np.linspace(0, 1, f, dtype=np.float32)
        seg[:f] = y[s:s + f] * (1 - ramp) + seg[:f] * ramp
        seg[-f:] = seg[-f:] * (1 - ramp) + y[s + n - f:s + n] * ramp
    y[s:s + n] = seg


def room_tone(x: np.ndarray, s: int, e: int, n: int, sr: int = SR, avoid: list[tuple[int, int]] = ()) -> np.ndarray:
    """在 [s, e) 前後 20 秒內找最安靜的 0.5 秒（避開 avoid 區間與 [s, e) 本身），重複鋪到 n 個取樣點。"""
    win = int(sr * ROOM_WIN_S)
    lo, hi = max(0, s - int(sr * ROOM_SEARCH_S)), min(len(x), e + int(sr * ROOM_SEARCH_S))
    blocked = [(s, e), *avoid]
    best, best_rms = None, None
    step = win // 2
    for a in range(lo, max(lo, hi - win) + 1, step):
        b = a + win
        if any(a < be and bs < b for bs, be in blocked):
            continue
        r = float(np.sqrt(np.mean(x[a:b].astype(np.float64) ** 2)))
        if best_rms is None or r < best_rms:
            best, best_rms = a, r
    if best is None:
        return np.zeros(n, dtype=np.float32)
    tile = x[best:best + win].astype(np.float32)
    reps = int(np.ceil(n / len(tile)))
    return np.tile(tile, reps)[:n]


def render_edit(window: np.ndarray, w0: int, edit: dict, clip: np.ndarray | None,
                spans: list[tuple[int, int]], sr: int = SR) -> np.ndarray:
    """處理一筆：window 是原聲從第 w0 個取樣點開始的一段（涵蓋這筆前後 20 秒），
    回傳這筆時間範圍 [s, t) 處理後的聲音。spans 是所有筆的範圍（找底噪時避開）。"""
    s, t = int(edit["start"] * sr) - w0, int(edit["end"] * sr) - w0
    s, t = max(0, s), min(len(window), t)
    y = window.astype(np.float32).copy()
    if t <= s:
        return y[s:t]
    if edit["類型"] == "換聲音":
        new = match_loudness(fit_length(clip, t - s), window[s:t], sr)
    else:
        local = [(a - w0, b - w0) for a, b in spans if (a - w0, b - w0) != (s, t)]
        new = room_tone(window, s, t, t - s, sr, avoid=local)
    splice(y, s, new, sr)
    return y[s:t]


def apply_edits(x: np.ndarray, edits: list[dict], clips: dict[int, np.ndarray], sr: int = SR) -> np.ndarray:
    """整條聲音一次處理（測試用；正式組裝走 render_audio 逐段讀寫，不整條放進記憶體）。"""
    y = x.astype(np.float32).copy()
    spans = [(int(e["start"] * sr), int(e["end"] * sr)) for e in edits]
    for i, e in enumerate(edits):
        s = max(0, spans[i][0])
        seg = render_edit(x, 0, e, clips.get(i), spans, sr)
        y[s:s + len(seg)] = seg
    return y


# ---------- 讀寫檔案 ----------

def _read_audio(path: Path) -> np.ndarray:
    """短檔（生成的聲音）用 ffmpeg 讀成 48kHz 單聲道 float32。"""
    cmd = ["ffmpeg", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def _read_range(path: Path, a: int, b: int) -> np.ndarray:
    import soundfile as sf

    with sf.SoundFile(str(path)) as f:
        a = max(0, a)
        f.seek(a)
        return f.read(max(0, min(b, f.frames) - a), dtype="float32")


def _write_wav(path: Path, x: np.ndarray) -> None:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.clip(x, -1, 1), SR, subtype="PCM_16")


def render_audio(workdir: str | Path, video: str | Path | None = None) -> dict:
    """`bookclub render audio`：讀名字處理計畫與老師生成紀錄，組出新聲音軌與處理前後試聽。

    整支影片的聲音不整條放進記憶體（98 分鐘約 1GB）：先用 ffmpeg 抽一份 48kHz
    原聲檔，再一段段抄到新聲音軌，碰到要處理的地方才讀前後 20 秒來處理。
    """
    import soundfile as sf

    from bookclub import nameplan, tts

    workdir = Path(workdir).expanduser()
    plan = wd.read_json(nameplan.plan_path(workdir))
    if not plan:
        raise FileNotFoundError(f"找不到 {nameplan.plan_path(workdir)}，先跑 `bookclub gen names`。")
    teacher_log = wd.read_json(tts.teacher_log_path(workdir))
    edits, warnings = build_edl(plan, teacher_log)
    for w in warnings:
        print(f"⚠️ {w}")

    if video is None:
        merged = wd.read_json(wd.merged_transcript_path(workdir)) or {}
        video = merged.get("source") or (wd.read_json(wd.analysis_result_path(workdir)) or {}).get("video")
    if not video or not Path(video).is_file():
        raise FileNotFoundError(f"找不到原片影片：{video}\n→ 用 --video 指定影片路徑。")

    out = out_dir(workdir)
    out.mkdir(parents=True, exist_ok=True)
    orig_path, new_path = out / "原聲音軌.wav", out / "新聲音軌.wav"
    if not orig_path.is_file():
        print(f"[組裝] 從原片抽出 48kHz 聲音：{Path(video).name}")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(SR),
                        "-c:a", "pcm_s16le", str(orig_path)], check=True)
    spans = [(max(0, int(e["start"] * SR)), int(e["end"] * SR)) for e in edits]
    pad = int((ROOM_SEARCH_S + ROOM_WIN_S) * SR)
    block = SR * 30
    with sf.SoundFile(str(orig_path)) as src, \
            sf.SoundFile(str(new_path), "w", samplerate=SR, channels=1, subtype="PCM_16") as dst:
        total = src.frames
        pos = 0

        def copy_until(end: int) -> None:
            nonlocal pos
            src.seek(pos)
            while pos < end:
                n = min(block, end - pos)
                dst.write(src.read(n, dtype="float32"))
                pos += n

        for i, e in enumerate(edits):
            s0, t0 = min(spans[i][0], total), min(spans[i][1], total)
            copy_until(s0)
            w0 = max(0, s0 - pad)
            window = _read_range(orig_path, w0, t0 + pad)
            clip = _read_audio(workdir / e["檔案"]) if e["類型"] == "換聲音" else None
            seg = render_edit(window, w0, e, clip, spans)
            dst.write(np.clip(seg, -1, 1))
            pos = s0 + len(seg)
        copy_until(total)

    ab = out / "處理前後"
    rows = []
    for n, e in enumerate(edits, start=1):
        a, b = int((e["start"] - CONTEXT_S) * SR), int((e["end"] + CONTEXT_S) * SR)
        tag = f"{n:03d}_{wd.fmt_time(e['start']).replace(':', '')}"
        _write_wav(ab / f"{tag}_前.wav", _read_range(orig_path, a, b))
        _write_wav(ab / f"{tag}_後.wav", _read_range(new_path, a, b))
        rows.append({**e, "編號": n, "前": f"處理前後/{tag}_前.wav", "後": f"處理前後/{tag}_後.wav"})
    (out / "處理前後.html").write_text(_ab_page(rows), encoding="utf-8")

    summary = {
        "原片": str(video), "長度秒": round(total / SR, 2),
        "換聲音": sum(1 for e in edits if e["類型"] == "換聲音"),
        "消音": sum(1 for e in edits if e["類型"] == "消音"),
        "要人聽": sum(1 for e in edits if e.get("要人聽")),
        "要人處理": len(plan.get("要人處理", [])),
        "警告": warnings,
        "剪輯決策": edits,
    }
    wd.write_json(edl_path(workdir), summary)
    from bookclub import proclog   # 09-29：AI 處理紀錄＋沒登記的變動檢查（生成/處理紀錄.json，第 5 步讀）
    proclog.write_audio_log(workdir, edits, orig_path, new_path)
    print(f"[組裝] 完成：換聲音 {summary['換聲音']} 段、消音 {summary['消音']} 段；"
          f"新聲音軌 {summary['長度秒'] / 60:.1f} 分鐘 → {new_path}")
    return summary


def _ab_page(rows: list[dict]) -> str:
    items = []
    for r in rows:
        what = f"換成老師 AI 聲音：{html.escape(r['文字'])}" if r["類型"] == "換聲音" else "名字消音（墊環境底噪）"
        flag = '<span class="flag">要人聽</span>' if r.get("要人聽") else ""
        items.append(
            f'<section><b>#{r["編號"]}　{wd.fmt_time(r["start"])}–{wd.fmt_time(r["end"])}</b>　'
            f'<small>{r["類型"]}・候選 {"、".join(map(str, r["候選"]))}</small> {flag}'
            f'<p>{what}</p><p>處理前<audio controls preload="none" src="{r["前"]}"></audio></p>'
            f'<p>處理後<audio controls preload="none" src="{r["後"]}"></audio></p></section>'
        )
    return (
        '<!doctype html><html lang="zh-Hant"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>處理前後試聽</title>'
        "<style>body{font-family:-apple-system,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;"
        "line-height:1.6}section{border:1px solid #ddd;border-radius:8px;padding:10px 16px;margin:12px 0}"
        "audio{width:100%}small{color:#666}.flag{color:#b00;font-size:.9em}</style>"
        f"<h1>處理前後試聽（{len(rows)} 筆）</h1>"
        "<p><small>每筆前後各多 2 秒。這頁含逐字稿（已換成代號），不要外傳。</small></p>"
        + "".join(items) + "</html>"
    )
