"""流程第 4 步（組裝影片，09-28 測試版）：一段範圍內，換聲音＋刪除＋停格＋模糊示範，輸出影片。

`bookclub render video <工作區> --start 37:00 --end 55:23 [--label] [--methods hw,sw,smart]`

先聲音、後畫面、最後只編碼一次：

1. 聲音（原片時間軸）：從原片抽範圍內 48kHz 聲音，換上學員重念（`生成/學員紀錄.json`）、老師名字整句換掉
   （`生成/名字處理計畫.json`＋`生成/老師紀錄.json`），音量對齊、接縫淡入淡出（沿用 `assemble.py`）
2. 刪除段落：覆核決定裡確認刪除的範圍（聲音畫面一起刪），剪點對齊到畫面格（1/25 秒）
3. 停格：學員重念比時間格長 15% 以上（`fit.py` 標紅）→ 在時間格結尾停格補長，多出來的聲音放在停格裡；
   範圍內沒有「重疊兩邊都重生成、前後排開」時，挑一筆重疊做一次停格示範（`--demo-freeze`）
4. 模糊示範：一段 30 秒模糊畫面右上四分之一
5. 輸出三種做法比速度：整段硬體編碼（h264_videotoolbox）、整段軟體編碼（libx264 medium）、
   只重做有動到的片段（其他直接複製，片段接在一起）
6. 驗證：長度＝原長度－刪除＋停格（誤差 < 0.1 秒）、聲音畫面等長、剪點前後沒有黑畫面

`--label`：另外輸出標字試看版：AI 處理過的時段左上角小字、生成的時段下方字幕是餵給模型的文字。

產出放在工作區 `輸出/`，檔名帶範圍（例如 `37-55`）。含學員分享內容，不要貼進對話或 commit。
"""

from __future__ import annotations

import difflib
import html
import json
import math
import subprocess
import time
from pathlib import Path

import numpy as np

from bookclub import workdir as wd

SR = 48000
FPS = 25
FONT_CANDIDATES = ["/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",
                   "/System/Library/Fonts/STHeiti Light.ttc"]
DEMO_FREEZE_S = 1.0
BLUR_S = 30.0
JOIN_FADE_S = 0.01


# ---------- 時間（純函式） ----------

def snap(t: float) -> float:
    """對齊到畫面格。"""
    return round(round(t * FPS) / FPS, 6)


def ceil_frames(d: float) -> float:
    return math.ceil(d * FPS - 1e-6) / FPS


def pieces(a: float, b: float, cuts: list[tuple[float, float]], freezes: list[dict]) -> list[dict]:
    """範圍 [a, b] 扣掉刪除段落 → 依序的片段 [{src:[s,e], freeze:秒}]；停格放在它所在片段的那個時間點切開。"""
    keep, pos = [], a
    for x, y in sorted(cuts):
        if y <= a or x >= b:
            continue
        if x > pos:
            keep.append([pos, x])
        pos = max(pos, y)
    if pos < b:
        keep.append([pos, b])
    out = []
    fz = sorted(freezes, key=lambda f: f["at"])
    for s, e in keep:
        cur = s
        for f in fz:
            if s < f["at"] <= e:
                out.append({"src": [cur, f["at"]], "freeze": f["dur"], "停格": f})
                cur = f["at"]
        if cur < e:
            out.append({"src": [cur, e], "freeze": 0.0})
    return out


def to_output_time(t: float, plist: list[dict]) -> float | None:
    """原片時間 → 成品時間（落在刪除範圍裡回傳 None）。停格在 t 這一點之後才加。"""
    acc = 0.0
    for p in plist:
        s, e = p["src"]
        if s <= t <= e:
            return acc + (t - s)
        acc += (e - s) + p["freeze"]
    return None


def output_length(plist: list[dict]) -> float:
    return sum(p["src"][1] - p["src"][0] + p["freeze"] for p in plist)


# ---------- 剪輯決策 ----------

def _in(a: float, b: float, lo: float, hi: float) -> bool:
    return a < hi and lo < b


def _chosen_heard(r: dict) -> str | None:
    tries = r.get("嘗試") or []
    k = (r.get("選定") or 1) - 1
    return tries[k].get("轉回文字") if 0 <= k < len(tries) else None


def build_decisions(workdir: Path, a: float, b: float, *, demo_freeze: bool = True) -> dict:
    """讀工作區，排出範圍內的所有動作（原片時間）。"""
    from bookclub import assemble, nameplan, overlap as overlap_mod, review, students, tts
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    dec = review.load_decisions(workdir)
    cuts = [(snap(c["start"]), snap(c["end"])) for c in dec["刪除段落"]
            if c.get("狀態") != "還原" and _in(c["start"], c["end"], a, b)]
    edits, marks, warnings = [], [], []

    st = wd.read_json(students.log_path(workdir), default=None) or {}
    voices = st.get("學員聲線", {})
    for r in st.get("句子", []):
        s0, s1 = r["slot"]
        if not _in(s0, s1, a, b) or not r.get("放回時間格"):
            continue
        fitted = r["放回時間格"]
        e = {"類型": "學員重念", "start": s0, "end": s1, "id": r["id"], "學員": r.get("學員"), "聲線": r.get("聲線"),
             "檔案": fitted["檔案"], "來源檔案": fitted.get("來源檔案"), "放回做法": fitted["放回做法"],
             "差異比例": fitted["差異比例"], "要人聽": r.get("要人聽", False), "text": r["text"],
             "生成用文字": r.get("生成用文字") or r["text"], "轉回文字": _chosen_heard(r),
             "生成秒數": _chosen_len(r), "文字來源": r.get("文字來源")}
        if fitted["放回做法"] == "標紅" and fitted["差異比例"] > 0 and fitted.get("來源檔案"):
            e["停格秒"] = ceil_frames(e["生成秒數"] - (s1 - s0)) if e["生成秒數"] else 0.0
        edits.append(e)

    plan = wd.read_json(nameplan.plan_path(workdir), default=None) or {}
    tlog = wd.read_json(tts.teacher_log_path(workdir), default=None)
    recs = {r["id"]: r for r in (tlog or {}).get("句子", [])}
    names_edl, w = assemble.build_edl(plan, tlog)
    warnings += w
    for n in names_edl:
        if not _in(n["start"], n["end"], a, b):
            continue
        r = recs.get(n.get("生成編號"), {})
        if n["類型"] == "換聲音":
            edits.append({"類型": "名字整句換掉", "start": n["start"], "end": n["end"], "id": n["生成編號"],
                          "檔案": n["檔案"], "候選": n["候選"], "要人聽": n.get("要人聽", False), "text": r.get("text", n["文字"]),
                          "生成用文字": r.get("生成用文字") or n["文字"], "轉回文字": _chosen_heard(r),
                          "生成秒數": _chosen_len(r), "放回做法": (r.get("放回時間格") or {}).get("放回做法")})
        else:
            edits.append({"類型": "名字消音", "start": n["start"], "end": n["end"], "候選": n["候選"]})
    for m in plan.get("要人處理", []):
        marks.append({"類型": "名字要人處理", "候選": m["候選"], "原因": m["原因"]})

    # 重疊：這輪測試所有學員都重念（不管覆核的「保留原聲」），建議照 voices={} 重算
    ov = wd.read_json(wd.overlap_path(workdir), default=None) or {}
    overlap_mod.apply_simple_filters(ov)
    ov["overlaps"] = review.effective_overlaps(workdir, ov.get("overlaps", []), dec)   # 覆核時人工補的、改過時間的
    tdata = turns_mod.page_data(workdir)
    turns = tdata.get("段落", []) if not tdata.get("尚未準備") else []
    freezes = []
    for o in ov.get("overlaps", []):
        if o.get("已自動跳過") or not _in(o["start"], o["end"], a, b):
            continue
        d = dec["重疊"].get(review.overlap_id(o), {})
        sug = review.suggest_overlap(o, turns, None, {})
        how = d.get("做法") or sug["做法"]
        covered = next((e["id"] for e in edits if e["類型"] == "學員重念" and e["start"] <= o["start"] and o["end"] <= e["end"]), None)
        marks.append({"類型": "重疊", "start": o["start"], "end": o["end"], "做法": how,
                      "處理": f"學員那邊在 {covered} 整段重念時一起換掉（重疊的老師小聲回應跟著拿掉）" if covered and how == "只留學員"
                      else "這輪沒有另外處理"})
        if how == "兩邊都重生成" and (d.get("排法") or sug.get("排法")) == "前後排開":
            freezes.append({"at": snap(o["end"]), "dur": ceil_frames(o["end"] - o["start"]), "原因": "重疊前後排開"})
    if demo_freeze and not freezes and marks:
        o = next((m for m in marks if m["類型"] == "重疊"), None)
        if o:
            freezes.append({"at": snap(o["start"]), "dur": DEMO_FREEZE_S, "原因": "停格示範（範圍內沒有「前後排開」的重疊，挑這筆重疊示範）",
                            "示範": True})
    for e in edits:
        if e.get("停格秒"):
            freezes.append({"at": snap(e["end"]), "dur": e["停格秒"], "原因": f"{e['id']} 重念比時間格長 {e['差異比例']:+.0%}，停格補長",
                            "edit": e["id"]})

    # 重疊的動作：長的優先（跟 assemble 一樣）
    edits.sort(key=lambda e: -(e["end"] - e["start"]))
    kept = []
    for e in edits:
        if any(_in(e["start"], e["end"], k["start"], k["end"]) for k in kept):
            warnings.append(f"{e['id'] if 'id' in e else e['類型']} 跟別筆重疊，被較長的那筆蓋過")
            continue
        kept.append(e)
    kept.sort(key=lambda e: e["start"])
    # 落在刪除範圍裡的換聲音不用做
    kept = [e for e in kept if not any(x <= e["start"] and e["end"] <= y for x, y in cuts)]
    blur = pick_blur(kept, cuts, a, b)
    return {"範圍": [a, b], "刪除": cuts, "動作": kept, "停格": sorted(freezes, key=lambda f: f["at"]),
            "模糊": blur, "標記": marks, "警告": warnings, "學員聲線": voices}


def _chosen_len(r: dict) -> float | None:
    """放回時間格那個版本的長度（插入停頓後的長度、或改語速重生成的長度）。"""
    if (r.get("放回時間格") or {}).get("長度秒"):
        return r["放回時間格"]["長度秒"]
    tries = r.get("嘗試") or []
    k = (r.get("選定") or 1) - 1
    if not (0 <= k < len(tries)):
        return None
    t = tries[k]
    return t.get("插入停頓後長度秒") or t.get("長度秒")


def pick_blur(edits: list[dict], cuts: list, a: float, b: float) -> list[float]:
    """模糊示範：挑第一段學員重念開始處的 30 秒（沒有就範圍開頭），避開刪除範圍。"""
    st = next((e["start"] for e in edits if e["類型"] == "學員重念"), a)
    s = snap(st)
    for x, y in cuts:
        if x <= s < y:
            s = y
    return [s, snap(min(b, s + BLUR_S))]


# ---------- 聲音 ----------

def _extract(video: Path, a: float, b: float, dst: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(video),
                    "-vn", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(dst)], check=True)


def build_audio(workdir: Path, video: Path, d: dict, out: Path, tag: str) -> dict:
    """原片時間軸上換好聲音 → 依片段接起來（刪除、停格）→ 新聲音軌。回傳每筆的放置資訊。"""
    import soundfile as sf

    from bookclub import assemble

    a, b = d["範圍"]
    orig = out / f"原聲_{tag}.wav"
    if not orig.is_file():
        _extract(video, a, b, orig)
    x, _ = sf.read(str(orig), dtype="float32")
    y = x.copy()
    tails: dict[str, np.ndarray] = {}
    placed = {}
    spans = [(int((e["start"] - a) * SR), int((e["end"] - a) * SR)) for e in d["動作"]]
    for e, (s, t) in zip(d["動作"], spans):
        s, t = max(0, s), min(len(x), t)
        if e["類型"] == "名字消音":
            local = [sp for sp in spans if sp != (s, t)]
            new = assemble.room_tone(x, s, t, t - s, SR, avoid=local)
            assemble.splice(y, s, new, SR)
            continue
        src = e["來源檔案"] if e.get("停格秒") else e["檔案"]
        clip = assemble._read_audio(workdir / src)
        gain = _gain(clip, x[s:t])
        clip = (clip * gain).astype(np.float32)
        head = assemble.fit_length(clip, t - s)
        assemble.splice(y, s, head, SR)
        if e.get("停格秒"):
            n = int(round(e["停格秒"] * SR))
            tail = assemble.fit_length(clip[t - s:], n)
            f = min(int(SR * 0.02), len(tail) // 2)
            if f:
                tail[-f:] *= np.linspace(1, 0, f, dtype=np.float32)
            tails[e["id"]] = tail
        placed[e["id"]] = {"增益": round(float(gain), 3)}

    plist = pieces(a, b, d["刪除"], d["停格"])
    parts = []
    for i, p in enumerate(plist):
        s, e = (int(round((t - a) * SR)) for t in p["src"])
        seg = y[s:e].copy()
        nxt = plist[i + 1] if i + 1 < len(plist) else None
        is_cut = nxt is not None and not p["freeze"] and abs(nxt["src"][0] - p["src"][1]) > 1e-6
        prev_cut = i > 0 and not plist[i - 1]["freeze"] and abs(plist[i - 1]["src"][1] - p["src"][0]) > 1e-6
        # 刪除剪點：前後各 10 毫秒淡出淡入，避免爆音
        f = min(int(SR * JOIN_FADE_S), len(seg) // 2)
        if is_cut and f:
            seg[-f:] *= np.linspace(1, 0, f, dtype=np.float32)
        if prev_cut and f:
            seg[:f] *= np.linspace(0, 1, f, dtype=np.float32)
        parts.append(seg)
        if p["freeze"]:
            n = int(round(p["freeze"] * SR))
            fz = p["停格"]
            fill = tails.get(fz.get("edit")) if fz.get("edit") else None
            if fill is None:   # 停格示範：墊環境底噪
                fill = assemble.room_tone(x, e, e + 1, n, SR)
            parts.append(assemble.fit_length(fill.astype(np.float32), n))
    new = np.concatenate(parts)
    dst = out / f"新聲音_{tag}.wav"
    sf.write(str(dst), np.clip(new, -1, 1), SR, subtype="PCM_16")
    return {"原聲": orig, "新聲音": dst, "片段": plist, "放置": placed, "長度": len(new) / SR}


def _gain(clip: np.ndarray, ref: np.ndarray, max_gain: float = 8.0) -> float:
    from bookclub import assemble

    ca, cb = assemble.active_rms(clip), assemble.active_rms(ref)
    return min(cb / ca, max_gain) if ca > 0 and cb > 0 else 1.0


# ---------- 畫面 ----------

def keyframes(video: Path, a: float, b: float) -> list[float]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-skip_frame", "nokey", "-read_intervals",
                        f"{max(0, a - 60)}%{b + 60}", "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True, check=True).stdout
    return sorted({round(float(x), 6) for x in r.split() if x.strip()})


def _blur_chain(inp: str, out: str, blur: list[float] | None, offset: float) -> str:
    """右上四分之一模糊（enable 用這一段輸入的時間）。"""
    if not blur:
        return f"[{inp}]null[{out}]"
    s, e = blur[0] - offset, blur[1] - offset
    return (f"[{inp}]split[{out}b][{out}t];[{out}t]crop=iw/2:ih/2:iw/2:0,boxblur=24:4[{out}c];"
            f"[{out}b][{out}c]overlay=W/2:0:enable='between(t,{s:.3f},{e:.3f})'[{out}]")


def full_filter(d: dict, plist: list[dict], overlays: list[dict]) -> str:
    """整段重新編碼的 filter_complex：模糊 → 切片段（停格用 tpad 複製最後一格）→ 接起來 → 疊標字。"""
    a = d["範圍"][0]
    n = len(plist)
    chain = [_blur_chain("0:v", "vb", d["模糊"], a), f"[vb]split={n}" + "".join(f"[s{i}]" for i in range(n))]
    for i, p in enumerate(plist):
        s, e = p["src"][0] - a, p["src"][1] - a
        f = f"[s{i}]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS"
        if p["freeze"]:
            f += f",tpad=stop_mode=clone:stop_duration={p['freeze']:.3f}"
        chain.append(f + f"[p{i}]")
    chain.append("".join(f"[p{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vc]")
    last = "vc"
    for k, o in enumerate(overlays):
        chain.append(f"[{last}][{k + 2}:v]overlay=0:0:enable='between(t,{o['start']:.3f},{o['end']:.3f})'[o{k}]")
        last = f"o{k}"
    chain.append(f"[{last}]format=yuv420p[vout]")
    return ";".join(chain)


ENCODERS = {
    "hw": ["-c:v", "h264_videotoolbox", "-b:v", "2000k", "-profile:v", "high"],
    "sw": ["-c:v", "libx264", "-preset", "medium", "-crf", "23", "-profile:v", "high"],
}


def render_full(video: Path, d: dict, plist: list[dict], audio: Path, dst: Path, method: str,
                overlays: list[dict] = ()) -> float:
    a, b = d["範圍"]
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(video), "-i", str(audio)]
    for o in overlays:
        cmd += ["-i", str(o["png"])]
    fc = full_filter(d, plist, list(overlays))
    script = dst.with_suffix(".filter.txt")
    script.write_text(fc, encoding="utf-8")
    cmd += ["-filter_complex_script", str(script), "-map", "[vout]", "-map", "1:a", *ENCODERS[method],
            "-r", str(FPS), "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-movflags", "+faststart", str(dst)]
    t = time.time()
    subprocess.run(cmd, check=True)
    return time.time() - t


def smart_plan(plist: list[dict], keys: list[float], blur: list[float] | None) -> list[dict]:
    """只重做有動到的片段：每個片段切在關鍵畫面上；開頭不是關鍵畫面、碰到模糊、後面接停格的那一小段重新編碼，
    其他直接複製。回傳 [{src:[s,e], 做法: 複製/重做, freeze}]（純函式）。"""
    out = []
    for p in plist:
        s, e = p["src"]
        cuts = [s] + [k for k in keys if s < k < e] + [e]
        segs = []
        for x, y in zip(cuts, cuts[1:]):
            dirty = (not any(abs(x - k) < 1e-3 for k in keys)) or (blur and _in(x, y, blur[0], blur[1]))
            segs.append({"src": [x, y], "做法": "重做" if dirty else "複製", "freeze": 0.0})
        if p["freeze"]:
            segs[-1]["做法"] = "重做"
            segs[-1]["freeze"] = p["freeze"]
        # 相鄰同做法合併（重做的合併成一段編碼；複製的合併成一段複製）
        merged = []
        for sg in segs:
            if merged and merged[-1]["做法"] == sg["做法"] and not merged[-1]["freeze"]:
                merged[-1]["src"][1] = sg["src"][1]
                merged[-1]["freeze"] = sg["freeze"]
            else:
                merged.append(sg)
        out += merged
    return out


def render_smart(video: Path, d: dict, plist: list[dict], audio: Path, dst: Path, work: Path) -> tuple[float, dict]:
    keys = keyframes(video, *d["範圍"])
    plan = smart_plan(plist, keys, d["模糊"])
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    listing = []
    for i, sg in enumerate(plan):
        s, e = sg["src"]
        part = work / f"{i:03d}.ts"
        if sg["做法"] == "複製":
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", str(video),
                   "-map", "0:v", "-c:v", "copy", "-bsf:v", "h264_mp4toannexb", "-f", "mpegts", str(part)]
        else:
            blur = d["模糊"]
            if blur and _in(s, e, *blur):
                bs, be = blur[0] - s, blur[1] - s
                fc = (f"[0:v]split[b][t];[t]crop=iw/2:ih/2:iw/2:0,boxblur=24:4[c];"
                      f"[b][c]overlay=W/2:0:enable='between(t,{bs:.3f},{be:.3f})'")
            else:
                fc = "[0:v]null"
            if sg["freeze"]:
                fc += f",tpad=stop_mode=clone:stop_duration={sg['freeze']:.3f}"
            fc += "[v]"
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", str(video),
                   "-filter_complex", fc, "-map", "[v]", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                   "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(FPS), "-bf", "0", "-g", "250",
                   "-f", "mpegts", str(part)]
        subprocess.run(cmd, check=True)
        listing.append(f"file '{part}'")
    lst = work / "片段.txt"
    lst.write_text("\n".join(listing) + "\n", encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(audio),
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ac", "2",
                    "-movflags", "+faststart", str(dst)], check=True)
    spent = time.time() - t0
    stat = {"片段數": len(plan), "重做段數": sum(1 for sg in plan if sg["做法"] == "重做"),
            "重做秒數": round(sum(sg["src"][1] - sg["src"][0] for sg in plan if sg["做法"] == "重做"), 1),
            "複製秒數": round(sum(sg["src"][1] - sg["src"][0] for sg in plan if sg["做法"] == "複製"), 1)}
    return spent, stat


# ---------- 驗證 ----------

def probe_len(path: Path, stream: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries", "stream=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout.strip()
    if r and r != "N/A":
        return float(r.splitlines()[0])
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True).stdout.strip()
    return float(r)


def count_frames(path: Path) -> int:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                        "stream=nb_read_packets", "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return int(r.stdout.strip() or 0)


def black_near(path: Path, times: list[float], pad: float = 1.0) -> list[dict]:
    """剪點前後 pad 秒內有沒有黑畫面（blackdetect）。"""
    found = []
    for t in times:
        s = max(0.0, t - pad)
        r = subprocess.run(["ffmpeg", "-hide_banner", "-ss", f"{s:.3f}", "-t", f"{2 * pad:.3f}", "-i", str(path),
                            "-vf", "blackdetect=d=0.04:pix_th=0.10", "-an", "-f", "null", "-"],
                           capture_output=True, text=True)
        if "black_start" in r.stderr:
            found.append({"剪點": round(t, 2)})
    return found


def decode_errors(path: Path) -> int:
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True)
    return len([ln for ln in r.stderr.splitlines() if ln.strip()])


def verify(path: Path, expected: float, joins: list[float], full_decode: bool = False) -> dict:
    v, au = probe_len(path, "v:0"), probe_len(path, "a:0")
    frames = count_frames(path)
    res = {"畫面秒": round(v, 3), "聲音秒": round(au, 3), "預期秒": round(expected, 3),
           "畫面格數": frames, "預期格數": int(round(expected * FPS)),
           "長度誤差秒": round(v - expected, 3), "聲畫差秒": round(v - au, 3),
           "剪點黑畫面": black_near(path, joins)}
    if full_decode:
        res["解碼錯誤行數"] = decode_errors(path)
    res["通過"] = abs(res["長度誤差秒"]) < 0.1 and abs(res["聲畫差秒"]) < 0.1 and not res["剪點黑畫面"] \
        and res.get("解碼錯誤行數", 0) == 0
    return res


# ---------- 標記清單、標字 ----------

def label_text(e: dict) -> str:
    if e["類型"] == "學員重念":
        return f"AI：{e['學員']} 重念（{e['聲線']}聲）"
    if e["類型"] == "名字整句換掉":
        return "AI：名字整句換掉"
    return "AI：名字消音"


def diff_marks(expected: str, heard: str | None) -> tuple[str, str, int]:
    """（稿子標記版、轉回文字標記版、不一樣的字數）：不一樣的地方用【】框起來。比對前拿掉標點。"""
    if heard is None:
        return expected, "（沒有轉回文字）", -1
    import unicodedata

    from bookclub.tts import to_simplified

    def clean(s: str) -> list[tuple[str, str]]:
        return [(ch, to_simplified(ch)) for ch in unicodedata.normalize("NFKC", s) if ch.isalnum()]

    ea, hb = clean(expected), clean(heard)
    sm = difflib.SequenceMatcher(None, [k for _, k in ea], [k for _, k in hb], autojunk=False)
    out_a, out_b, n = [], [], 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        sa = "".join(c for c, _ in ea[i1:i2])
        sb = "".join(c for c, _ in hb[j1:j2])
        if op == "equal":
            out_a.append(sa)
            out_b.append(sb)
        else:
            n += max(i2 - i1, j2 - j1)
            out_a.append(f"【{sa}】" if sa else "【】")
            out_b.append(f"【{sb}】" if sb else "【】")
    return "".join(out_a), "".join(out_b), n


def build_marks(d: dict, plist: list[dict], precision: dict | None = None) -> list[dict]:
    """處理標記清單的每一筆（成品時間、原片時間、做了什麼…）。"""
    rows = []

    def ot(t: float) -> float | None:
        return to_output_time(t, plist)

    for e in d["動作"]:
        row = {"類型": e["類型"], "原片": [e["start"], e["end"]], "成品": [ot(e["start"]), ot(e["end"])],
               "做了什麼": label_text(e).replace("AI：", ""), "id": e.get("id"), "要人聽": bool(e.get("要人聽")),
               "生成秒數": e.get("生成秒數")}
        if e["類型"] != "名字消音":
            a_, b_, n = diff_marks(e["text"], e.get("轉回文字"))
            row.update({"餵給模型的文字": e["text"], "送進模型的文字": e["生成用文字"] if e["生成用文字"] != e["text"] else None,
                        "轉回文字": e.get("轉回文字"), "稿子標記": a_, "轉回標記": b_, "不一樣字數": n,
                        "放回做法": e.get("放回做法"), "差異比例": e.get("差異比例")})
            if e.get("停格秒"):
                row["做了什麼"] += f"；比時間格長，結尾停格 {e['停格秒']:.2f} 秒"
        if precision and e.get("id") in precision:
            row["精準度"] = precision[e["id"]]
        rows.append(row)
    for x, y in d["刪除"]:
        t = ot(x) if ot(x) is not None else ot(y)
        rows.append({"類型": "刪除", "原片": [x, y], "成品": [t, t], "做了什麼": f"刪除 {y - x:.1f} 秒（聲音畫面一起刪）",
                     "要人聽": False})
    for f in d["停格"]:
        t = ot(f["at"])
        rows.append({"類型": "停格", "原片": [f["at"], f["at"]], "成品": [t, t + f["dur"] if t is not None else None],
                     "做了什麼": f"停格 {f['dur']:.2f} 秒：{f['原因']}", "要人聽": False})
    if d["模糊"]:
        s, e = d["模糊"]
        rows.append({"類型": "模糊示範", "原片": [s, e], "成品": [ot(s), ot(e)],
                     "做了什麼": "畫面右上四分之一模糊（示範，這支影片實際要模糊哪裡還沒判斷）", "要人聽": False})
    for m in d["標記"]:
        if m["類型"] == "重疊":
            rows.append({"類型": "重疊", "原片": [m["start"], m["end"]], "成品": [ot(m["start"]), ot(m["end"])],
                         "做了什麼": f"重疊（建議：{m['做法']}）：{m['處理']}", "要人聽": False})
        elif m["類型"] == "名字要人處理":
            rows.append({"類型": "名字要人處理", "原片": None, "成品": None,
                         "做了什麼": f"名字候選 {m['候選']} 沒有自動處理：{m['原因']}（這筆在原片裡沒動）", "要人聽": True})
    rows.sort(key=lambda r: (r["成品"][0] if r.get("成品") and r["成品"][0] is not None else 1e9))
    return rows


def _t(x: float | None) -> str:
    return "—" if x is None else wd.fmt_time(x)


HEADER = ("**只處理了 {rng}；範圍內沒列在這裡的地方＝原片沒動。**\n\n"
          "文字欄就是模型拿到的稿子；稿子本身有錯字，是轉文字沒校對到，不是模型的問題；"
          "稿子對、轉回文字不對，才是模型念錯。\n\n"
          "【】框起來的是稿子跟轉回文字不一樣的地方（比對時不看標點）。")


def marks_md(rows: list[dict], rng: str, extra: list[str]) -> str:
    out = [f"# 處理標記清單（{rng}）", "", HEADER.format(rng=rng), ""] + extra + [""]
    for i, r in enumerate(rows, 1):
        out.append(f"## {i}. 成品 {_t(r['成品'][0] if r.get('成品') else None)}–{_t(r['成品'][1] if r.get('成品') else None)}"
                   f"　（原片 {_t(r['原片'][0] if r.get('原片') else None)}–{_t(r['原片'][1] if r.get('原片') else None)}）")
        out.append(f"- 做了什麼：{r['做了什麼']}" + (f"（{r['id']}）" if r.get("id") else ""))
        if r.get("生成秒數"):
            out.append(f"- 生成秒數：{r['生成秒數']:.1f}　放回做法：{r.get('放回做法') or '—'}"
                       + (f"（{r['差異比例']:+.0%}）" if r.get("差異比例") is not None else ""))
        out.append(f"- 要人聽：{'要' if r['要人聽'] else '不用'}")
        if "餵給模型的文字" in r:
            out.append(f"- 餵給模型的文字：{r['稿子標記']}")
            if r.get("送進模型的文字"):
                out.append(f"- 套用發音對照表後真正送進模型：{r['送進模型的文字']}")
            out.append(f"- 生成後轉回的文字：{r['轉回標記']}")
            out.append(f"- 不一樣的字數：{r['不一樣字數']}")
        if r.get("精準度"):
            p = r["精準度"]
            out.append(f"- 開口時間差：{p.get('開口差毫秒', '—')} 毫秒；生成長度 vs 時間格：{p.get('長度差秒', '—')} 秒")
        out.append("")
    return "\n".join(out)


def marks_html(rows: list[dict], rng: str, extra: list[str]) -> str:
    def mark(s: str) -> str:
        return html.escape(s).replace("【", "<mark>").replace("】", "</mark>")

    trs = []
    for i, r in enumerate(rows, 1):
        text = ""
        if "餵給模型的文字" in r:
            text = (f"<div class=t><b>餵給模型</b>{mark(r['稿子標記'])}</div>"
                    + (f"<div class=t><b>送進模型</b>{html.escape(r['送進模型的文字'])}</div>" if r.get("送進模型的文字") else "")
                    + f"<div class=t><b>轉回文字</b>{mark(r['轉回標記'])}</div>")
        p = r.get("精準度") or {}
        trs.append(
            f"<tr class='{'flag' if r['要人聽'] else ''}'><td>{i}</td>"
            f"<td>{_t(r['成品'][0] if r.get('成品') else None)}<br><small>{_t(r['成品'][1] if r.get('成品') else None)}</small></td>"
            f"<td>{_t(r['原片'][0] if r.get('原片') else None)}<br><small>{_t(r['原片'][1] if r.get('原片') else None)}</small></td>"
            f"<td><b>{html.escape(r['類型'])}</b> {html.escape(r['做了什麼'])}"
            + (f" <small>({html.escape(r['id'])})</small>" if r.get("id") else "")
            + (f"<br><small>生成 {r['生成秒數']:.1f} 秒・{html.escape(r.get('放回做法') or '')}"
               + (f" {r['差異比例']:+.0%}" if r.get('差異比例') is not None else "") + "</small>" if r.get("生成秒數") else "")
            + (f"<br><small>開口差 {p.get('開口差毫秒')} ms</small>" if p.get("開口差毫秒") is not None else "")
            + f"{text}</td><td>{'要' if r['要人聽'] else ''}</td></tr>")
    css = (":root{color-scheme:light dark;--bg:#fff;--fg:#222;--mut:#666;--line:#ddd;--hl:#ffe08a;--flag:#fff3f3}"
           "@media (prefers-color-scheme:dark){:root{--bg:#1b1b1d;--fg:#eee;--mut:#aaa;--line:#444;--hl:#7a5d00;--flag:#3a2222}}"
           "body{background:var(--bg);color:var(--fg);font-family:-apple-system,'PingFang TC',sans-serif;max-width:1100px;"
           "margin:24px auto;padding:0 16px;line-height:1.55}table{border-collapse:collapse;width:100%}"
           "td,th{border-bottom:1px solid var(--line);padding:8px 6px;vertical-align:top;text-align:left}"
           "small{color:var(--mut)}mark{background:var(--hl);color:inherit}.t{margin-top:4px;font-size:.95em}"
           ".t b{display:inline-block;min-width:5em;color:var(--mut);font-weight:500}tr.flag{background:var(--flag)}"
           ".top{border:2px solid #c33;border-radius:8px;padding:10px 14px}.wrap{overflow-x:auto}")
    head = HEADER.format(rng=rng).replace("**", "")
    return ("<!doctype html><html lang='zh-Hant'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>處理標記清單</title><style>{css}</style><h1>處理標記清單（{html.escape(rng)}）</h1>"
            f"<div class=top>{'<br>'.join(html.escape(x) for x in head.split(chr(10)) if x)}</div>"
            + "".join(f"<p><small>{html.escape(x)}</small></p>" for x in extra)
            + "<div class=wrap><table><tr><th>#</th><th>成品時間</th><th>原片時間</th><th>做了什麼</th><th>要人聽</th></tr>"
            + "".join(trs) + "</table></div></html>")


def _font(size: int):
    from PIL import ImageFont

    for p in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    raise FileNotFoundError("找不到中文字型（PingFang／STHeiti）")


def _wrap(text: str, font, width: int) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if font.getlength(cur + ch) > width:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def make_overlay(path: Path, size: tuple[int, int], label: str | None, caption: str | None) -> None:
    """整張畫面大小的透明 PNG：左上角小標籤、下方字幕（半透明黑底）。"""
    from PIL import Image, ImageDraw

    w, h = size
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    if label:
        f = _font(30)
        tw = int(f.getlength(label))
        dr.rounded_rectangle([16, 14, 16 + tw + 24, 14 + 46], radius=8, fill=(200, 30, 30, 215))
        dr.text((28, 20), label, font=f, fill=(255, 255, 255, 255))
    if caption:
        f = _font(30)
        lines = _wrap(caption, f, w - 160)
        if len(lines) > 5:   # 太長只顯示前面，畫面放不下
            lines = lines[:4] + [lines[4][:-1] + "…"]
        lh = 42
        top = h - 30 - lh * len(lines) - 16
        dr.rectangle([60, top, w - 60, h - 30], fill=(0, 0, 0, 170))
        for i, ln in enumerate(lines):
            dr.text((80, top + 8 + i * lh), ln, font=f, fill=(255, 255, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def label_windows(d: dict, plist: list[dict]) -> list[dict]:
    """標字版每個時段顯示什麼（成品時間）。"""
    wins = []
    for e in d["動作"]:
        s, t = to_output_time(e["start"], plist), to_output_time(e["end"], plist)
        if s is None or t is None:
            continue
        if e.get("停格秒"):
            t += e["停格秒"]
        wins.append({"start": s, "end": t, "label": label_text(e), "caption": e.get("text")})
    for x, y in d["刪除"]:
        t = to_output_time(x, plist) or to_output_time(y, plist)
        if t is not None:
            wins.append({"start": max(0, t - 1.0), "end": t + 1.5, "label": f"刪除點（刪掉 {y - x:.0f} 秒）", "caption": None})
    for f in d["停格"]:
        t = to_output_time(f["at"], plist)
        if t is not None and not f.get("edit"):
            wins.append({"start": t, "end": t + f["dur"], "label": "停格（示範）" if f.get("示範") else "停格", "caption": None})
        elif t is not None:
            wins.append({"start": t, "end": t + f["dur"], "label": "停格（重念太長補長）", "caption": None, "副標": True})
    if d["模糊"]:
        s, e = (to_output_time(x, plist) for x in d["模糊"])
        if s is not None and e is not None:
            wins.append({"start": s, "end": e, "label": "模糊示範（右上）", "caption": None, "副標": True})
    return merge_windows(wins)


def merge_windows(wins: list[dict]) -> list[dict]:
    """時段重疊的標籤合在一起（例如模糊示範＋學員重念）：切成不重疊的小段，每段列出當下所有標籤。"""
    pts = sorted({w["start"] for w in wins} | {w["end"] for w in wins})
    out = []
    for s, e in zip(pts, pts[1:]):
        act = [w for w in wins if w["start"] <= s and e <= w["end"]]
        if not act:
            continue
        label = "｜".join(dict.fromkeys(w["label"] for w in act))
        cap = next((w["caption"] for w in act if w.get("caption")), None)
        if out and out[-1]["label"] == label and out[-1]["caption"] == cap and abs(out[-1]["end"] - s) < 1e-6:
            out[-1]["end"] = e
        else:
            out.append({"start": s, "end": e, "label": label, "caption": cap})
    return out


# ---------- 精準度 ----------

def onset(x: np.ndarray, sr: int = SR, db: float = -35.0) -> float | None:
    """第一個比這段最大聲低不到 db 的 10 毫秒音框（開口時間，秒）。"""
    f = int(sr * 0.01)
    n = len(x) // f
    if n == 0:
        return None
    rms = np.sqrt(np.mean(x[: n * f].reshape(n, f).astype(np.float64) ** 2, axis=1)) + 1e-12
    on = np.where(20 * np.log10(rms / rms.max()) > db)[0]
    return float(on[0] * f / sr) if len(on) else None


def measure(d: dict, orig: np.ndarray, placed: np.ndarray) -> dict:
    """每一筆換上去的聲音：開口時間差（生成 − 原本，毫秒）；生成長度 vs 時間格（秒）。placed 是原片時間軸上換好的聲音。"""
    a = d["範圍"][0]
    res = {}
    for e in d["動作"]:
        if e["類型"] == "名字消音":
            continue
        s, t = int((e["start"] - a) * SR), int((e["end"] - a) * SR)
        o1, o2 = onset(orig[s:t]), onset(placed[s:t])
        gen = e.get("生成秒數")
        res[e["id"]] = {"開口差毫秒": None if o1 is None or o2 is None else int(round((o2 - o1) * 1000)),
                        "長度差秒": None if gen is None else round(gen - (e["end"] - e["start"]), 2)}
    return res


def join_jumps(new: np.ndarray, joins: list[float]) -> list[dict]:
    """剪點前後各 1 秒：剪點兩側 20 毫秒的音量差（dB）與剪點上最大的取樣跳動。"""
    out = []
    for t in joins:
        k = int(t * SR)
        f = int(SR * 0.02)
        if k - SR < 0 or k + SR > len(new):
            continue
        l, r = new[k - f:k], new[k:k + f]
        rl = np.sqrt(np.mean(l.astype(np.float64) ** 2)) + 1e-9
        rr = np.sqrt(np.mean(r.astype(np.float64) ** 2)) + 1e-9
        win = new[k - SR:k + SR].astype(np.float64)
        jump = float(np.max(np.abs(np.diff(new[k - 240:k + 240].astype(np.float64)))))
        typical = float(np.percentile(np.abs(np.diff(win)), 99.9))
        out.append({"剪點成品秒": round(t, 2), "兩側音量差dB": round(float(20 * np.log10(rr / rl)), 1),
                    "剪點最大跳動": round(jump, 4), "前後一秒的99.9百分位跳動": round(typical, 4),
                    "疑似爆音": jump > max(0.05, 3 * typical)})
    return out


# ---------- 入口 ----------

def render_video(workdir: str | Path, start: float, end: float, *, video: str | Path | None = None,
                 label: bool = False, methods: list[str] = ("hw", "sw", "smart"), tag: str | None = None,
                 demo_freeze: bool = True, min_free_gb: float = 5.0) -> dict:
    import shutil

    import soundfile as sf

    from bookclub import review

    workdir = Path(workdir).expanduser()
    video = Path(video).expanduser() if video else review.video_path(workdir)
    if not video or not video.is_file():
        raise FileNotFoundError("找不到原片，用 --video 指定。")
    a, b = snap(start), snap(end)
    tag = tag or f"{int(a // 60)}-{int(b // 60)}"
    out = workdir / "輸出"
    out.mkdir(parents=True, exist_ok=True)
    d = build_decisions(workdir, a, b, demo_freeze=demo_freeze)
    t = time.time()
    au = build_audio(workdir, video, d, out, tag)
    audio_s = time.time() - t
    plist = au["片段"]
    expected = output_length(plist)
    joins = []
    acc = 0.0
    for i, p in enumerate(plist[:-1]):
        acc += p["src"][1] - p["src"][0] + p["freeze"]
        joins.append(acc)
    summary = {"範圍": [a, b], "原長度秒": round(b - a, 3), "刪除秒": round(sum(y - x for x, y in d["刪除"]), 3),
               "停格秒": round(sum(f["dur"] for f in d["停格"]), 3), "預期成品秒": round(expected, 3),
               "片段數": len(plist), "聲音處理秒": round(audio_s, 1), "動作數": len(d["動作"]),
               "警告": d["警告"], "輸出": {}}

    # 精準度（原片時間軸上換好的聲音 vs 原聲）
    orig, _ = sf.read(str(au["原聲"]), dtype="float32")
    placed_track = _placed_track(workdir, d, orig)
    prec = measure(d, orig, placed_track)
    new, _ = sf.read(str(au["新聲音"]), dtype="float32")
    cut_joins = [to_output_time(x, plist) for x, _y in d["刪除"]]
    cut_joins = [j if j is not None else to_output_time(_y, plist) for j, (_x, _y) in zip(cut_joins, d["刪除"])]
    summary["精準度"] = _prec_summary(prec)
    summary["剪點聲音"] = join_jumps(new, [j for j in cut_joins if j is not None])

    rng = f"{wd.fmt_time(a)}–{wd.fmt_time(b)}"
    rows = build_marks(d, plist, prec)
    extra = [f"成品長度 {wd.fmt_time(expected)}（原片 {b - a:.1f} 秒，刪除 {summary['刪除秒']:.1f} 秒，停格 {summary['停格秒']:.2f} 秒）",
             "這輪測試：宇軒在工作台設了學員 1、2、3「保留原聲」，測試照樣全部重念；學員段落都還沒校對，用建議稿（名冊本名已換代號的初稿）。"]
    (out / f"處理標記_{tag}.md").write_text(marks_md(rows, rng, extra), encoding="utf-8")
    (out / f"處理標記_{tag}.html").write_text(marks_html(rows, rng, extra), encoding="utf-8")
    wd.write_json(out / f"剪輯決策_{tag}.json", {**d, "片段": plist, "精準度": prec, "摘要": summary})
    from bookclub import proclog   # 09-29：AI 處理紀錄＋沒登記的變動檢查（生成/處理紀錄.json，第 5 步讀）
    proclog.write_render_log(workdir, d, plist, au["原聲"], au["新聲音"], tag)

    for m in methods:
        free = shutil.disk_usage(str(out)).free / 1e9
        if free < min_free_gb:
            summary["輸出"][m] = {"略過": f"硬碟只剩 {free:.1f} GB"}
            continue
        dst = out / f"成品_{tag}_{m}.mp4"
        if m == "smart":
            spent, stat = render_smart(video, d, plist, au["新聲音"], dst, out / f"_片段_{tag}")
        else:
            spent, stat = render_full(video, d, plist, au["新聲音"], dst, m), {}
        ver = verify(dst, expected, joins, full_decode=(m == "smart"))
        summary["輸出"][m] = {"耗時秒": round(spent, 1), "大小MB": round(dst.stat().st_size / 1e6, 1),
                            "倍速": round((b - a) / spent, 2), "推估整支98分鐘秒": round(spent * 5864 / (b - a)),
                            **stat, "驗證": ver, "檔案": dst.name}
        wd.write_json(out / f"輸出摘要_{tag}.json", summary)

    if label:
        size = _video_size(video)
        wins = label_windows(d, plist)
        ov_dir = out / f"_標字_{tag}"
        for k, w in enumerate(wins):
            w["png"] = ov_dir / f"{k:03d}.png"
            make_overlay(w["png"], size, w["label"], w["caption"])
        dst = out / f"成品_{tag}_標字版.mp4"
        spent = render_full(video, d, plist, au["新聲音"], dst, "hw", overlays=wins)
        summary["輸出"]["標字版"] = {"耗時秒": round(spent, 1), "大小MB": round(dst.stat().st_size / 1e6, 1),
                                  "標字時段數": len(wins), "驗證": verify(dst, expected, joins), "檔案": dst.name}
    wd.write_json(out / f"輸出摘要_{tag}.json", summary)
    return summary


def _video_size(video: Path) -> tuple[int, int]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", str(video)], capture_output=True, text=True, check=True).stdout.strip()
    w, h = r.split(",")[:2]
    return int(w), int(h)


def _placed_track(workdir: Path, d: dict, orig: np.ndarray) -> np.ndarray:
    """原片時間軸上只換聲音（不刪不停格）的版本，量精準度用。"""
    from bookclub import assemble

    a = d["範圍"][0]
    y = orig.copy()
    for e in d["動作"]:
        if e["類型"] == "名字消音":
            continue
        s, t = int((e["start"] - a) * SR), int((e["end"] - a) * SR)
        s, t = max(0, s), min(len(y), t)
        clip = assemble._read_audio(workdir / e["檔案"])
        y[s:t] = assemble.fit_length(clip, t - s)
    return y


def _prec_summary(prec: dict) -> dict:
    ds = [abs(v["開口差毫秒"]) for v in prec.values() if v["開口差毫秒"] is not None]
    ls = [v["長度差秒"] for v in prec.values() if v["長度差秒"] is not None]
    return {"筆數": len(prec), "開口差平均毫秒": round(float(np.mean(ds))) if ds else None,
            "開口差最大毫秒": max(ds) if ds else None, "開口差超過100毫秒筆數": sum(1 for x in ds if x > 100),
            "長度差平均秒": round(float(np.mean(ls)), 2) if ls else None,
            "長度差最大秒": round(max(ls, key=abs), 2) if ls else None}
