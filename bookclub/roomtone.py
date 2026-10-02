"""墊底噪的挑法（10-02 第六批第五件，宇軒選做法丙）。

起因：預演版成品 1:29:04–1:29:06（原片 1:33:24.8–1:33:26.2，一筆局部消音、墊底噪）有奇怪的雜訊。
舊挑法（`assemble.room_tone`，09-28）在前後 20 秒內找「最安靜」的 0.5 秒、避開所有處理過的範圍，重複鋪滿；
沒有音量上限。那一筆前後 20 秒幾乎都是學員段落與老師重念，避開之後只剩 7 個 0.5 秒窗可以挑，挑到的是
1:33:32.8–1:33:33.3（−29.4 dBFS，有人在講話），每 0.5 秒重複一次 → 聽起來像雜訊，而且是別人的原聲。

新挑法（所有墊底噪的地方共用：局部消音、名字消音、生成聲音底下墊的環境聲（也蓋住補靜音的空白）、停格補的聲音）：
1. 先看整支影片安靜處的水準（20 毫秒一格的音量，第 `FLOOR_PCT` 百分位，第一堂約 −90 dBFS），
   上限＝那個水準＋`REL_DB`，再不超過 `ABS_MAX_DB`。低於上限＝沒有聽得出來的人聲（第一堂上限約 −78 dBFS，
   講話的地方是 −20 到 −35 dBFS，差 40 dB 以上）
2. 就近挑：前後 `SEARCH_S` 秒內，每一格都低於上限、連續至少 `MIN_RUN_S` 秒的地方（頭尾各再讓 `TRIM_S`，
   避開講話的尾音）。處理過的範圍（學員段落、老師重念）裡夠安靜的地方也可以取——低於上限就是沒有人聲；
   高於上限的一律不用
3. 需要多長就盡量取一段夠長的連續安靜片段；不夠長才把附近幾段接起來，接縫交叉淡入淡出（`XFADE_S`）；
   附近幾段加起來還是不夠長（要重複才鋪得滿）就跟找不到一樣，改用全片底噪
4. 附近找不到夠安靜的：用「全片底噪」——分析時（或第 2 步打開時、組裝前）從整支影片挑一段最長的安靜片段
   （`GLOBAL_LEN_S` 秒），存在 `參考音/底噪.json`；第 2 步讓人播放確認（「這段可以」「換一段」）。
   整支影片都找不到夠安靜的，才墊全靜音
5. 墊進去的那一段頭尾跟前後原聲交界，沿用 `assemble.splice` 的 10 毫秒交叉淡入淡出

挑法改了（`METHOD`），組裝做過沒有的判斷會要求重新組裝（`execute.render_done` 比 `輸出摘要` 的 `底噪挑法`）；
生成好的聲音不受影響（底噪只在組裝時墊）。
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np

from bookclub import workdir as wd

METHOD = "就近夠安靜才用-1002"   # 改挑法就換這個字串（組裝要重做）
FRAME_S = 0.02
FLOOR_PCT = 5           # 全片安靜處的水準：20 毫秒一格音量的第 5 百分位（第一堂 −90.2 dBFS，16k 與 48k 差 0.4 dB）
REL_DB = 12.0           # 上限＝安靜處水準＋12 dB（第一堂 −78）；比 30% 的格子還安靜一點，講話的格子遠高於這個
ABS_MAX_DB = -60.0      # 不管影片多吵，上限最多 −60 dBFS（再高就可能是很小聲的講話）
DIGITAL_ZERO_DB = -110.0   # 比這個還小的格子是全靜音（Zoom 偶爾有），算水準時不算進去
SEARCH_S = 20.0         # 就近挑：前後各找多遠
MIN_RUN_S = 0.3         # 一段安靜片段至少多長才拿來用
TRIM_S = 0.04           # 安靜片段頭尾各讓多少（避開講話的尾音、吸氣）
XFADE_S = 0.03          # 接縫交叉淡入淡出
GLOBAL_LEN_S = 8.0      # 全片底噪最多取多長（附近不夠時要鋪的長度可能好幾秒，越長越不會聽出重複）
GLOBAL_MIN_S = 1.0      # 全片底噪候選至少多長
N_CANDIDATES = 8        # 全片底噪留幾個候選（「換一段」輪流換）


# ---------- 純函式 ----------

def frame_db(x: np.ndarray, sr: int) -> np.ndarray:
    fr = max(1, int(sr * FRAME_S))
    n = len(x) // fr
    if n == 0:
        return np.zeros(0)
    r = np.sqrt(np.mean(x[: n * fr].reshape(n, fr).astype(np.float64) ** 2, axis=1))
    return 20 * np.log10(r + 1e-12)


def level_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(x.astype(np.float64) ** 2)) + 1e-12)) if len(x) else -240.0


def floor_db(db: np.ndarray) -> float:
    live = db[db > DIGITAL_ZERO_DB]
    return float(np.percentile(live, FLOOR_PCT)) if len(live) else DIGITAL_ZERO_DB


def ceiling(floor: float) -> float:
    return min(ABS_MAX_DB, floor + REL_DB)


def quiet_runs(db: np.ndarray, ceil_db: float, min_frames: int, trim: int) -> list[tuple[int, int]]:
    """每一格都低於上限的連續格子（格子編號 [a, b)），頭尾各讓 trim 格，剩下至少 min_frames 格。"""
    q = np.concatenate([[False], db < ceil_db, [False]])
    d = np.diff(q.astype(np.int8))
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    out = []
    for a, b in zip(starts, ends):
        a2 = a + (trim if a > 0 else 0)
        b2 = b - (trim if b < len(db) else 0)
        if b2 - a2 >= min_frames:
            out.append((int(a2), int(b2)))
    return out


def crossjoin(pieces: list[np.ndarray], n: int, sr: int) -> np.ndarray:
    """把幾段接成 n 個取樣點，接縫交叉淡入淡出；不夠長就從頭再接一次（一樣交叉淡入淡出）。"""
    pieces = [p.astype(np.float32) for p in pieces if len(p)]
    if not pieces or n <= 0:
        return np.zeros(max(0, n), np.float32)
    out = pieces[0].copy()
    k = 1
    while len(out) < n:
        nxt = pieces[k % len(pieces)]
        k += 1
        f = min(int(sr * XFADE_S), len(out) // 2, len(nxt) // 2)
        if f > 0:
            ramp = np.linspace(0, 1, f, dtype=np.float32)
            mid = out[-f:] * np.sqrt(1 - ramp) + nxt[:f] * np.sqrt(ramp)   # 等功率：底噪是不相關的雜訊，接縫不會變小聲
            out = np.concatenate([out[:-f], mid, nxt[f:]])
        else:
            out = np.concatenate([out, nxt])
        if k > 10000:
            break
    return out[:n]


def pick(x: np.ndarray, s: int, e: int, n: int, sr: int, ceil_db: float | None = None,
         fallback: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """就近挑夠安靜的底噪（純函式）。x 是原聲（取樣點），[s, e) 是要換掉的地方，要 n 個取樣點。
    回傳（聲音, {來源: 附近／全片底噪／全靜音, 片段: [(起, 訖) 取樣點], dBFS}）。"""
    fr = max(1, int(sr * FRAME_S))
    lo, hi = max(0, s - int(sr * SEARCH_S)), min(len(x), e + int(sr * SEARCH_S))
    if ceil_db is None:   # 沒給（舊的呼叫、測試）：用這一段自己的安靜水準
        ceil_db = ceiling(floor_db(frame_db(x[lo:hi], sr)))
    runs = []
    for part_lo, part_hi in ((lo, s), (e, hi)):   # 這一筆自己的範圍不用
        if part_hi - part_lo < fr:
            continue
        db = frame_db(x[part_lo:part_hi], sr)
        for a, b in quiet_runs(db, ceil_db, int(np.ceil(MIN_RUN_S / FRAME_S)), int(round(TRIM_S / FRAME_S))):
            ra, rb = part_lo + a * fr, part_lo + b * fr
            dist = s - rb if rb <= s else ra - e
            runs.append((max(0, dist), ra, rb))
    runs.sort()
    if runs:
        long = [r for r in runs if r[2] - r[1] >= n]
        if long:   # 最近的一段夠長的連續安靜片段：取最靠近這一筆的那一頭
            _, ra, rb = long[0]
            a = rb - n if rb <= s else ra
            seg = x[a:a + n]
            return seg.astype(np.float32).copy(), {"來源": "附近", "片段": [(a, a + n)], "dBFS": round(level_db(seg), 1)}
        used, total = [], 0
        for _, ra, rb in runs:
            used.append((ra, rb))
            total += rb - ra
            if total >= n:
                break
        # 附近的安靜片段加起來不夠長：要重複才鋪得滿 → 改用全片底噪（一段幾秒長的，比零點幾秒一直重複自然）；
        # 連全片底噪都沒有，才重複附近這幾段
        if total >= n or fallback is None or not len(fallback):
            segs = [x[a:b] for a, b in used]
            out = crossjoin(segs, n, sr)
            return out, {"來源": "附近", "片段": used, "dBFS": round(level_db(np.concatenate(segs)), 1)}
    if fallback is not None and len(fallback):
        out = crossjoin([fallback], n, sr)
        return out, {"來源": "全片底噪", "片段": [], "dBFS": round(level_db(fallback), 1)}
    return np.zeros(n, np.float32), {"來源": "全靜音", "片段": [], "dBFS": None}


class Bed:
    """組裝時用的底噪設定：上限（dBFS）＋全片底噪（48k 聲音）。"""

    def __init__(self, ceil_db: float | None, clip: np.ndarray | None):
        self.ceiling = ceil_db
        self.clip = clip

    def take(self, x: np.ndarray, s: int, e: int, n: int, sr: int) -> np.ndarray:
        return pick(x, s, e, n, sr, self.ceiling, self.clip)[0]


# ---------- 全片底噪（工作區） ----------

def info_path(workdir: str | Path) -> Path:
    return Path(workdir) / "參考音" / "底噪.json"


def clip_path(workdir: str | Path) -> Path:
    return Path(workdir) / "參考音" / "底噪_48k.wav"


def load_info(workdir: str | Path) -> dict | None:
    return wd.read_json(info_path(workdir), default=None)


def candidates_from_db(db: np.ndarray, frame_s: float = FRAME_S) -> tuple[float, float, list[dict]]:
    """整支影片每 20 毫秒的音量 → (安靜處水準, 上限, 候選[{start, end, dBFS}])（純函式）。
    候選＝最長的幾段安靜片段，各取中間 GLOBAL_LEN_S 秒；越長越前面。"""
    fl = floor_db(db)
    ceil_db = ceiling(fl)
    runs = quiet_runs(db, ceil_db, int(np.ceil(GLOBAL_MIN_S / frame_s)), int(round(TRIM_S / frame_s)))
    runs.sort(key=lambda r: -(r[1] - r[0]))
    out = []
    for a, b in runs[:N_CANDIDATES]:
        L = min(b - a, int(round(GLOBAL_LEN_S / frame_s)))
        m = (a + b) // 2
        a2 = max(a, m - L // 2)
        b2 = a2 + L
        out.append({"start": round(a2 * frame_s, 3), "end": round(b2 * frame_s, 3),
                    "dBFS": round(float(10 * np.log10(np.mean(10 ** (db[a2:b2] / 10)) + 1e-24)), 1)})
    return round(fl, 1), round(ceil_db, 1), out


def analyze(audio: Path) -> dict:
    """整支影片的聲音（工作區的 audio.flac）一段一段讀，算安靜處水準與全片底噪候選。"""
    import soundfile as sf

    dbs = []
    with sf.SoundFile(str(audio)) as f:
        sr = f.samplerate
        while True:
            x = f.read(sr * 60, dtype="float32")
            if len(x) == 0:
                break
            if x.ndim > 1:
                x = x.mean(axis=1)
            dbs.append(frame_db(x, sr))
    db = np.concatenate(dbs) if dbs else np.zeros(0)
    fl, ceil_db, cands = candidates_from_db(db)
    return {"安靜處水準dBFS": fl, "上限dBFS": ceil_db, "候選": cands}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_info(workdir: str | Path) -> dict | None:
    """全片底噪的資料：沒有（舊工作區）或挑法改了就自動補挑（要有 audio.flac），回傳資料；沒有聲音檔回傳 None。
    已經確認過的那一段，補挑後候選裡還在的話照樣算確認過。"""
    workdir = Path(workdir)
    info = load_info(workdir)
    if info and info.get("挑法") == METHOD:
        return info
    audio = wd.audio_path(workdir)
    if not audio.exists():
        return info
    new = {"版本": 1, "挑法": METHOD, **analyze(audio), "選第幾個": 0, "已確認": False, "產生時間": _now()}
    if info and info.get("已確認"):
        same = next((i for i, c in enumerate(new["候選"]) if abs(c["start"] - info.get("start", -1)) < 0.05), None)
        if same is not None:
            new.update({"選第幾個": same, "已確認": True, "確認時間": info.get("確認時間")})
    _apply_choice(new)
    wd.write_json(info_path(workdir), new)
    return new


def _apply_choice(info: dict) -> None:
    c = (info.get("候選") or [None])[info.get("選第幾個", 0)] if info.get("候選") else None
    info.update({"start": c["start"], "end": c["end"], "dBFS": c["dBFS"]} if c else {"start": None, "end": None, "dBFS": None})


def decide(workdir: str | Path, action: str) -> dict:
    """第 2 步：「這段可以」（action＝確認）或「換一段」（換下一個候選，回到還沒確認）。"""
    workdir = Path(workdir)
    info = ensure_info(workdir)
    if not info or not info.get("候選"):
        raise ValueError("這支影片找不到夠安靜的片段可以當全片底噪（會墊全靜音）")
    if action == "確認":
        info.update({"已確認": True, "確認時間": _now()})
    elif action == "換一段":
        info["選第幾個"] = (info.get("選第幾個", 0) + 1) % len(info["候選"])
        info["已確認"] = False
        info.pop("確認時間", None)
    else:
        raise ValueError("只能選：確認、換一段")
    _apply_choice(info)
    wd.write_json(info_path(workdir), info)
    return info


def bed_for(workdir: str | Path, video: str | Path | None, sr: int = 48000) -> Bed:
    """組裝用：自動補挑全片底噪，從原片切出 48k 的那一段（存在 參考音/底噪_48k.wav，換一段才重切）。"""
    workdir = Path(workdir)
    try:
        info = ensure_info(workdir)
    except Exception as exc:  # noqa: BLE001 — 挑不到不擋組裝：附近找不到時墊全靜音
        print(f"⚠️ 全片底噪沒挑成（{type(exc).__name__}），附近找不到夠安靜的地方時會墊全靜音")
        info = None
    if not info:
        return Bed(None, None)
    ceil_db = info.get("上限dBFS")
    if info.get("start") is None or not video:
        return Bed(ceil_db, None)
    cp = clip_path(workdir)
    meta = cp.with_suffix(".json")
    key = {"start": info["start"], "end": info["end"], "sr": sr}
    import soundfile as sf

    if not (cp.is_file() and wd.read_json(meta, default=None) == key):
        wd.unlink_if_link(cp)
        cp.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{info['start']:.3f}", "-t",
                        f"{info['end'] - info['start']:.3f}", "-i", str(video), "-vn", "-ac", "1", "-ar", str(sr),
                        "-c:a", "pcm_s16le", str(cp)], check=True)
        wd.write_json(meta, key)
    clip, _ = sf.read(str(cp), dtype="float32")
    return Bed(ceil_db, clip)
