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
            **({"重疊項目": g["重疊項目"]} if g.get("重疊項目") else {}),
            **({"疊放": True, "重疊": g["重疊項目"][0]} if g.get("疊放") else {}),
        })
    for m in plan.get("消音", []):
        edits.append({"類型": "消音", "start": m["start"], "end": m["end"], "候選": [m["候選"]]})

    # 重疊：長的優先（B 方案兩邊都生成的那一對不算搶，見 stackable）
    edits.sort(key=lambda e: -(e["end"] - e["start"]))
    kept: list[dict] = []
    for e in edits:
        if any(e["start"] < k["end"] and k["start"] < e["end"] and not stackable(e, k) for k in kept):
            warnings.append(f"候選 {e['候選']} 跟別筆重疊，被較長的那筆蓋過")
            continue
        kept.append(e)
    kept.sort(key=lambda e: e["start"])
    return kept, warnings


def stackable(e: dict, k: dict) -> bool:
    """兩筆動作可以疊在一起放（10-01 B 方案）：同一處重疊、兩邊都標了疊放（學員一句＋老師一句，聲音相加）。"""
    return bool(e.get("疊放") and k.get("疊放") and e.get("重疊") and e.get("重疊") == k.get("重疊"))


MUTE_KINDS = ("消音", "名字消音", "局部消音", "學員名字消音")   # 墊環境底噪的動作（其他是換聲音）
SWAP_KINDS = ("換聲音", "學員重念", "名字整句換掉")


def subtract(a: float, b: float, blockers: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """[a, b] 扣掉 blockers 蓋到的部分，回傳剩下的小段（純函式）。"""
    parts = [(a, b)]
    for x, y in sorted(blockers):
        nxt = []
        for s, e in parts:
            if y <= s or x >= e:
                nxt.append((s, e))
                continue
            if s < x:
                nxt.append((s, x))
            if y < e:
                nxt.append((y, e))
        parts = nxt
    return [(s, e) for s, e in parts if e - s >= 0.01]


def local_mutes(dec: dict) -> list[dict]:
    """第 3 步標的局部消音（`覆核決定.json` 的 `局部消音[]`，狀態不是「還原」的）。"""
    return [m for m in dec.get("局部消音", []) if m.get("狀態") != "還原" and m.get("end", 0) > m.get("start", 0)]


def add_local_mutes(edits: list[dict], mutes: list[dict], cuts: list[tuple[float, float]] = ()) -> tuple[list[dict], list[str]]:
    """把局部消音加進剪輯決策（純函式，09-29 宇軒：局部消音保留，標了就要真的消）。

    - 跟刪除段落重疊的部分不用消（已經剪掉）
    - 跟既有的動作（換聲音、名字消音）重疊的部分以既有那筆為準，列在警告裡
    - 霧化還沒做，先一樣墊底噪（`霧化` 欄位記下來，處理紀錄註明）
    回傳（加好、依時間排序的剪輯決策、警告）。"""
    out, warnings = list(edits), []
    taken = [(e["start"], e["end"]) for e in edits]
    for m in mutes:
        ov = m.get("重疊")   # 重疊處的消音（`overlap_mutes`）：被學員重念蓋到是正常的，不列警告
        free = subtract(m["start"], m["end"], list(cuts))
        if not free:
            if not ov:
                warnings.append(f"局部消音 {m['id']} 整段落在刪除段落裡，不用消")
            continue
        parts = [p for s, e in free for p in subtract(s, e, taken)]
        if not ov and (len(parts) != len(free) or sum(e - s for s, e in parts) < sum(e - s for s, e in free) - 0.01):
            warnings.append(f"局部消音 {m['id']} 跟換聲音或名字的處理重疊，重疊的地方以那一筆為準")
        for s, e in parts:
            out.append({"類型": "局部消音", "start": s, "end": e, "id": m["id"], "方式": m.get("方式", "墊底噪"),
                        "霧化": m.get("方式") == "霧化", "候選": [],
                        **({"重疊": ov, "做法": m.get("做法")} if ov else {})})
    out.sort(key=lambda e: e["start"])
    return out, warnings


OVERLAP_KEEP = "不用改"            # 重疊的做法裡，只有這個會把原聲留在成品
OVERLAP_NOT_BUILT = ("兩邊都重生成", "兩邊都不留")   # 還沒做的做法，先消音（第 3 步也不顯示了）
OVERLAP_LEFT_TOL_S = 0.05          # 重疊處沒蓋到的原聲在這個秒數以內不算漏（剪點對齊畫面格的誤差）


def overlap_mutes(overlaps: list[dict]) -> list[dict]:
    """重疊處要消音的範圍（純函式，09-30）：兩個聲音混在同一段錄音裡，學員原聲拿不掉，
    所以除了「不用改」，不管選哪個做法，重疊那一小段一律消音（老師跟著靜音零點幾秒）。
    被學員重念、名字處理蓋到的部分由 `add_local_mutes` 讓給那一筆。
    `overlaps`：[{id, start, end, 做法}]。"""
    return [{"id": f"重疊{o['id']}", "start": o["start"], "end": o["end"], "方式": "墊底噪",
             "重疊": o["id"], "做法": o["做法"]}
            for o in overlaps if o["做法"] != OVERLAP_KEEP and o["end"] > o["start"]]


COVER_TOL_S = 0.05   # 涵蓋的判斷容許的誤差（剪點對齊畫面格、四捨五入）


def find_cover(a: float, b: float, coverers: list[dict], oid: str | None = None) -> dict | None:
    """[a, b] 整個落在哪一筆「會把原聲換掉」的範圍裡（純函式，10-01 宇軒 7-4）：學員重念的時間格、
    老師重念的範圍、剪掉的片段。這一處重疊自己產生的那一筆（`重疊項目`／`重疊` 是 oid）不算。
    `coverers`：[{類型, id, start, end, ...}]；回傳最短的那一筆，沒有就 None。覆核工作台與組裝共用。"""
    hits = [k for k in coverers
            if k["start"] - COVER_TOL_S <= a and b <= k["end"] + COVER_TOL_S
            and not (oid and (oid in (k.get("重疊項目") or []) or k.get("重疊") == oid))]
    return min(hits, key=lambda k: k["end"] - k["start"]) if hits else None


def overlap_outcome(o: dict, edits: list[dict], cuts: list[tuple[float, float]] = ()) -> dict:
    """這一處重疊在剪輯決策裡實際怎麼了（純函式）：{處理: 一句話, 沒處理秒: 還留著原聲的秒數, 涵蓋: 那一筆}。
    `edits` 是排好的全部動作（換聲音、消音都會把那段原聲拿掉）。
    10-01：整個落在別筆換聲音的範圍裡（`find_cover`）→ 跟著那一筆換掉，不管這一處選了什麼。"""
    swaps = [e for e in edits if e["類型"] in SWAP_KINDS]
    cov = find_cover(o["start"], o["end"], [{**e, "id": e.get("id") or e.get("生成編號") or e["類型"]} for e in swaps],
                     o["id"])
    if cov:
        return {"處理": f"整段落在 {cov['id']} 的範圍裡，跟著換掉", "沒處理秒": 0.0,
                "涵蓋": {"類型": cov["類型"], "id": cov["id"], "start": cov["start"], "end": cov["end"]}}
    if o["做法"] == OVERLAP_KEEP:
        return {"處理": "照原樣，沒有動（不用改）", "沒處理秒": 0.0}
    free = subtract(o["start"], o["end"], list(cuts))
    if not free:
        return {"處理": "落在刪除段落裡，已經剪掉", "沒處理秒": 0.0}
    muted = sum(min(e["end"], y) - max(e["start"], x) for x, y in free for e in edits
                if e.get("重疊") == o["id"] and e["類型"] in MUTE_KINDS and e["start"] < y and x < e["end"])   # B 方案的兩句也帶 重疊，不算消音
    others = [e for e in edits if e.get("重疊") != o["id"]]
    left = sum(e - s for x, y in free for s, e in subtract(x, y, [(k["start"], k["end"]) for k in edits]))
    by = "、".join(dict.fromkeys(str(e.get("id") or e["類型"]) for e in others
                                if any(e["start"] < y and x < e["end"] for x, y in free)))
    if left > OVERLAP_LEFT_TOL_S:
        return {"處理": f"還有 {left:.2f} 秒原聲沒處理", "沒處理秒": round(left, 3)}
    if muted < 0.01:
        mine = [e for e in edits if e.get("疊放") and e.get("重疊") == o["id"]]
        if len(mine) >= 2:
            return {"處理": f"兩邊都重新生成、照原本的時間疊著（{'、'.join(str(e.get('id')) for e in mine)}）", "沒處理秒": 0.0}
        return {"處理": f"在 {by} 換聲音時一起換掉", "沒處理秒": 0.0}
    text = f"消音 {muted:.2f} 秒（墊環境底噪，老師的聲音跟著靜音）"
    if by:
        text += f"，其餘在 {by} 換聲音時一起換掉"
    if o["做法"] in OVERLAP_NOT_BUILT:
        text += f"；「{o['做法']}」還沒做，先消音"
    elif o["做法"] == "只留老師":
        text += "；選的是生成老師聲音，但老師這一句還沒生成或找不到句子，先消音"
    return {"處理": text, "沒處理秒": 0.0}


def student_name_edits(sp: dict) -> list[dict]:
    """保留原聲學員講到名字（`studentnames.plan()`）→ 剪輯決策：直接消音的墊底噪、換成代號的放生成檔（有生成紀錄才放）。"""
    out = [{"類型": "學員名字消音", "start": m["start"], "end": m["end"], "id": m["id"], "學員": m["學員"], "候選": [m["id"]]}
           for m in sp.get("消音", [])]
    return out


def add_student_name_edits(edits: list[dict], workdir: Path, a: float = 0.0, b: float = 1e12,
                           cuts: list[tuple[float, float]] = ()) -> tuple[list[dict], list[str]]:
    """把保留原聲學員講到名字的處理加進剪輯決策；跟既有動作重疊時以既有那筆為準（例如測試時學員整段重念）。"""
    from bookclub import studentgen, studentnames

    sp = studentnames.plan(workdir)
    new = [e for e in student_name_edits(sp) + studentgen.swap_edits(workdir, sp) if e["start"] < b and a < e["end"]]
    out, warnings = list(edits), []
    for e in new:
        if any(x <= e["start"] and e["end"] <= y for x, y in cuts):
            continue
        if any(e["start"] < k["end"] and k["start"] < e["end"] for k in out):
            warnings.append(f"{e['類型']} {e['id']} 跟別筆重疊，以那一筆為準")
            continue
        out.append(e)
    out.sort(key=lambda e: e["start"])
    return out, warnings


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


def room_tone(x: np.ndarray, s: int, e: int, n: int, sr: int = SR, avoid: list[tuple[int, int]] = (), bed=None) -> np.ndarray:
    """[s, e) 要墊的底噪，n 個取樣點。10-02 第六批第五件改挑法（見 `bookclub/roomtone.py`）：前後 20 秒內
    夠安靜（低於上限）的連續片段，處理過的範圍裡的也可以；附近沒有就用全片底噪（bed）。
    `avoid` 是舊挑法用的（避開處理過的範圍），現在不用，留著參數讓舊的呼叫照樣能跑。"""
    from bookclub import roomtone

    if bed is not None:
        return bed.take(x, s, e, n, sr)
    return roomtone.pick(x, s, e, n, sr)[0]


def render_edit(window: np.ndarray, w0: int, edit: dict, clip: np.ndarray | None,
                spans: list[tuple[int, int]], sr: int = SR, bed=None) -> np.ndarray:
    """處理一筆：window 是原聲從第 w0 個取樣點開始的一段（涵蓋這筆前後 20 秒），
    回傳這筆時間範圍 [s, t) 處理後的聲音。spans 是所有筆的範圍（找底噪時避開）。"""
    s, t = int(edit["start"] * sr) - w0, int(edit["end"] * sr) - w0
    s, t = max(0, s), min(len(window), t)
    y = window.astype(np.float32).copy()
    if t <= s:
        return y[s:t]
    if edit["類型"] not in MUTE_KINDS:
        new = match_loudness(fit_length(clip, t - s), window[s:t], sr)
    else:
        local = [(a - w0, b - w0) for a, b in spans if (a - w0, b - w0) != (s, t)]
        new = room_tone(window, s, t, t - s, sr, avoid=local, bed=bed)
    splice(y, s, new, sr)
    return y[s:t]


def apply_edits(x: np.ndarray, edits: list[dict], clips: dict[int, np.ndarray], sr: int = SR, bed=None) -> np.ndarray:
    """整條聲音一次處理（測試用；正式組裝走 render_audio 逐段讀寫，不整條放進記憶體）。"""
    y = x.astype(np.float32).copy()
    spans = [(int(e["start"] * sr), int(e["end"] * sr)) for e in edits]
    for i, e in enumerate(edits):
        s = max(0, spans[i][0])
        seg = render_edit(x, 0, e, clips.get(i), spans, sr, bed=bed)
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
    wd.unlink_if_link(path)   # 10-02 第五批：複製來的工作區裡是連結的話，寫成自己的檔（不順著連結寫回原本的工作區）
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
    from bookclub import review

    edits, w2 = add_local_mutes(edits, local_mutes(review.load_decisions(workdir)))   # 聲音軌跟原片等長，刪除段落不套用
    warnings += w2
    edits, w3 = add_student_name_edits(edits, workdir)
    warnings += w3
    for w in warnings:
        print(f"⚠️ {w}")

    if video is None:   # 10-02 第五批：存下來的路徑照「現在這個工作區」解讀（見 wd.find_video）
        video = wd.find_video(workdir)
        if video is None:
            raise FileNotFoundError(wd.video_missing_message(workdir))
    if not Path(video).is_file():
        raise FileNotFoundError(f"找不到原片影片：{video}\n→ 用 --video 指定影片路徑。")

    out = out_dir(workdir)
    out.mkdir(parents=True, exist_ok=True)
    orig_path, new_path = out / "原聲音軌.wav", out / "新聲音軌.wav"
    if not orig_path.is_file():
        print(f"[組裝] 從原片抽出 48kHz 聲音：{Path(video).name}")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(SR),
                        "-c:a", "pcm_s16le", str(orig_path)], check=True)
    spans = [(max(0, int(e["start"] * SR)), int(e["end"] * SR)) for e in edits]
    from bookclub import roomtone

    bed = roomtone.bed_for(workdir, video, SR)   # 10-02 第六批：夠安靜才用、附近沒有用全片底噪
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
            clip = _read_audio(workdir / e["檔案"]) if e["類型"] not in MUTE_KINDS else None
            seg = render_edit(window, w0, e, clip, spans, bed=bed)
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
        "局部消音": sum(1 for e in edits if e["類型"] == "局部消音"),
        "要人聽": sum(1 for e in edits if e.get("要人聽")),
        "要人處理": len(plan.get("要人處理", [])),
        "警告": warnings,
        "剪輯決策": edits,
        "底噪挑法": roomtone.METHOD,
    }
    wd.write_json(edl_path(workdir), summary)
    from bookclub import proclog   # 09-29：AI 處理紀錄＋沒登記的變動檢查（生成/處理紀錄.json，第 5 步讀）
    proclog.write_audio_log(workdir, edits, orig_path, new_path)
    print(f"[組裝] 完成：換聲音 {summary['換聲音']} 段、消音 {summary['消音']} 段、局部消音 {summary['局部消音']} 段；"
          f"新聲音軌 {summary['長度秒'] / 60:.1f} 分鐘 → {new_path}")
    return summary


def _ab_page(rows: list[dict]) -> str:
    items = []
    for r in rows:
        what = (f"換成老師 AI 聲音：{html.escape(r['文字'])}" if r["類型"] == "換聲音"
                else f"局部消音 {r['id']}（墊環境底噪{'；霧化還沒做' if r.get('霧化') else ''}）" if r["類型"] == "局部消音"
                else "名字消音（墊環境底噪）")
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
