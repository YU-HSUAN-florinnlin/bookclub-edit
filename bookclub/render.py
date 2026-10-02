"""流程第 4 步（組裝影片，09-28 測試版）：一段範圍內，換聲音＋刪除＋停格＋模糊示範，輸出影片。

`bookclub render video <工作區> --start 37:00 --end 55:23 [--label] [--methods hw,sw,smart]`

先聲音、後畫面、最後只編碼一次：

1. 聲音（原片時間軸）：從原片抽範圍內 48kHz 聲音，換上學員重念（`生成/學員紀錄.json`）、老師名字整句換掉
   （`生成/名字處理計畫.json`＋`生成/老師紀錄.json`），音量對齊、接縫淡入淡出（沿用 `assemble.py`）
2. 刪除段落：覆核決定裡確認刪除的範圍（聲音畫面一起刪），剪點對齊到畫面格（1/25 秒）
3. 停格：學員重念比時間格長 15% 以上（`fit.py` 標紅）→ 在時間格結尾停格補長，多出來的聲音放在停格裡；
   `--demo-freeze` 挑一筆重疊做一次停格示範
   聲音重疊（09-30）：除了「不用改」，重疊那一小段一律消音（被學員重念蓋到的跟著換掉）；
   「兩邊都重生成、前後排開」還沒做，先消音、不停格。還有重疊留著原聲就不輸出成品
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
from bookclub.assemble import MUTE_KINDS  # noqa: E402  墊底噪的動作（名字消音、局部消音…）

ROOM_UNDER = True   # 生成的聲音底下墊附近原片的環境底噪（生成檔的停頓是數位全靜音，接在原片中間會像突然真空）


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


def build_decisions(workdir: Path, a: float, b: float, *, demo_freeze: bool = False, demo_blur: bool = False,
                    include_kept: bool = False) -> dict:
    """讀工作區，排出範圍內的所有動作（原片時間）。第 3 步設成保留原聲的學員不換聲音（include_kept=True 才照樣換，測試用）。"""
    from bookclub import assemble, nameplan, review, students, tts
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    dec = review.load_decisions(workdir)
    cuts = [(snap(c["start"]), snap(c["end"])) for c in dec["刪除段落"]
            if c.get("狀態") != "還原" and _in(c["start"], c["end"], a, b)]
    edits, marks, warnings = [], [], []

    st = wd.read_json(students.log_path(workdir), default=None) or {}
    voices = st.get("學員聲線", {})
    kept_now = set() if include_kept else {k for k, v in dec["學員聲音"].items() if v == "保留原聲"}
    tinfo = turns_mod.page_data(workdir)
    turn_who = {t["id"]: t.get("說話者") for t in tinfo.get("段落", [])} if not tinfo.get("尚未準備") else {}
    # 09-30：生成紀錄只增不減——段落切開、合併、改時間之後，舊的重念還留在紀錄裡。只用「現在的段落排出來的」那幾筆：
    # 編號已經不存在的不用；編號還在但時間格改過、還沒重新生成的，那一格先消音（不放舊的，也不留學員原聲）
    try:
        now_items, _ = students.build_items(workdir, include_kept=include_kept)
        now_slots = {it["id"]: it["slot"] for it in now_items}
        now_by = {it["id"]: it for it in now_items}
    except FileNotFoundError:
        now_slots, now_by = None, {}   # 沒有段落分析（匯入的工作區）：照紀錄放
    stale_mutes = []
    for r in st.get("句子", []):
        if r.get("學員") in kept_now:
            continue
        if turn_who and r.get("段落") in turn_who and turn_who[r["段落"]] != r.get("學員"):
            warnings.append(f"{r['id']}：段落 {r['段落']} 現在是{turn_who[r['段落']]}，舊的重念不用")
            continue
        if now_slots is not None:
            cur = now_slots.get(r["id"])
            if cur is None:
                if _in(r["slot"][0], r["slot"][1], a, b):
                    warnings.append(f"{r['id']}：段落改過，這一筆舊的重念不用")
                continue
            if abs(cur[0] - r["slot"][0]) > 0.05 or abs(cur[1] - r["slot"][1]) > 0.05:
                if _in(cur[0], cur[1], a, b):
                    warnings.append(f"{r['id']}：時間格改過、還沒重新生成，這一格先消音（重新跑第 4 步的學員重念）")
                    stale_mutes.append({"id": r["id"], "start": max(cur[0], a), "end": min(cur[1], b), "方式": "墊底噪"})
                continue
        s0, s1 = r["slot"]
        if not _in(s0, s1, a, b) or not r.get("放回時間格"):
            continue
        fitted = r["放回時間格"]
        e = {"類型": "學員重念", "start": s0, "end": s1, "id": r["id"], "學員": r.get("學員"), "聲線": r.get("聲線"),
             "檔案": fitted["檔案"], "來源檔案": fitted.get("來源檔案"), "放回做法": fitted["放回做法"],
             "差異比例": fitted["差異比例"], "要人聽": r.get("要人聽", False), "text": r["text"],
             "生成用文字": r.get("生成用文字") or r["text"], "轉回文字": _chosen_heard(r),
             "生成秒數": _chosen_len(r), "文字來源": r.get("文字來源")}
        if (now_by.get(r["id"]) or r).get("疊放"):   # 10-01 B 方案：學員那一句跟老師那一句疊著放，不停格
            e.update({"疊放": True, "重疊": (now_by.get(r["id"]) or r).get("重疊")})
        elif fitted["放回做法"] == "標紅" and fitted["差異比例"] > 0 and fitted.get("來源檔案"):
            # 09-29 宇軒選 C：還是太長就先加快（最多 15%），剩下的才停格
            e["加快"] = speedup_for(e["生成秒數"] or 0.0, s1 - s0)
            e["停格秒"] = ceil_frames(e["生成秒數"] / e["加快"] - (s1 - s0)) if e["生成秒數"] else 0.0
            if e["停格秒"] <= 0:
                e.pop("停格秒")
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
                          "檔案": n["檔案"], "候選": n["候選"], "重疊項目": n.get("重疊項目") or [], "要人聽": n.get("要人聽", False), "text": r.get("text", n["文字"]),
                          "生成用文字": r.get("生成用文字") or n["文字"], "轉回文字": _chosen_heard(r),
                          "生成秒數": _chosen_len(r), "放回做法": (r.get("放回時間格") or {}).get("放回做法"),
                          **({"疊放": True, "重疊": n["重疊"]} if n.get("疊放") else {})})
        else:
            edits.append({"類型": "名字消音", "start": n["start"], "end": n["end"], "候選": n["候選"]})
    for m in plan.get("要人處理", []):
        marks.append({"類型": "名字要人處理", "候選": m["候選"], "原因": m["原因"]})

    # 重疊（09-30）：做法照覆核工作台那一套算（人選的優先、沒選照建議；保留原聲的學員「不用改」）。
    # 除了「不用改」，重疊那一小段一律消音（下面跟局部消音一起加）；「兩邊都重生成、前後排開」還沒做，不再停格
    freezes = []
    for o in review.overlap_choices(workdir, voices={} if include_kept else None):
        if not _in(o["start"], o["end"], a, b):
            continue
        marks.append({"類型": "重疊", "id": o["id"], "start": max(o["start"], a), "end": min(o["end"], b), "做法": o["做法"]})
    if demo_freeze and not freezes and marks:
        o = next((m for m in marks if m["類型"] == "重疊"), None)
        if o:
            freezes.append({"at": snap(o["start"]), "dur": DEMO_FREEZE_S, "原因": "停格示範（範圍內沒有「前後排開」的重疊，挑這筆重疊示範）",
                            "示範": True})
    for e in edits:
        if e.get("停格秒"):
            freezes.append({"at": snap(e["end"]), "dur": e["停格秒"], "原因": f"{e['id']} 重念比時間格長 {e['差異比例']:+.0%}，"
                            f"加快 {e.get('加快', 1.0) - 1:.0%} 後還多出來的停格補長",
                            "edit": e["id"]})

    # 重疊的動作：長的優先（跟 assemble 一樣）
    edits.sort(key=lambda e: -(e["end"] - e["start"]))
    kept = []
    for e in edits:
        if any(_in(e["start"], e["end"], k["start"], k["end"]) and not assemble.stackable(e, k) for k in kept):
            warnings.append(f"{e['id'] if 'id' in e else e['類型']} 跟別筆重疊，被較長的那筆蓋過")
            continue
        kept.append(e)
    kept.sort(key=lambda e: e["start"])
    # 落在刪除範圍裡的換聲音不用做；頭尾碰到刪除範圍（剪點對齊畫面格後差幾毫秒）的推到邊界
    kept = [e for e in kept if not any(x <= e["start"] and e["end"] <= y for x, y in cuts)]
    for e in kept:
        e["start"], e["end"] = clip_to_cuts(e["start"], e["end"], cuts)
    # 第 3 步標的局部消音（09-29：標了就要真的消）：刪除段落裡的不用消，跟換聲音重疊時以換聲音為準
    mutes = [m for m in assemble.local_mutes(dec) if _in(m["start"], m["end"], a, b)]
    mutes = [{**m, "start": max(m["start"], a), "end": min(m["end"], b)} for m in mutes]
    # 校對稿刪光、不生成的學員時間格也要消音（09-29），不然會留學員原聲
    mutes += [m for m in students.empty_chunks(workdir, a, b) if m["學員"] not in kept_now]
    mutes += stale_mutes
    ov_marks = [m for m in marks if m["類型"] == "重疊"]
    mutes += assemble.overlap_mutes(ov_marks)   # 09-30：重疊處的學員原聲不能留在成品
    kept, w = assemble.add_local_mutes(kept, mutes, cuts)
    warnings += w
    # 保留原聲的學員自己講到名字（09-29）：直接消音、或用他自己的聲音生成代號短句
    kept, w = assemble.add_student_name_edits(kept, workdir, a, b, cuts)
    warnings += w
    for e in kept:
        e["start"], e["end"] = clip_to_cuts(e["start"], e["end"], cuts)
    # 每一處重疊最後實際怎麼了；還留著原聲的列出來，`render_video` 看到就不輸出成品
    left = []
    for m in ov_marks:
        res = assemble.overlap_outcome(m, kept, cuts)
        m["處理"] = res["處理"]
        if res.get("涵蓋"):
            m["涵蓋"] = res["涵蓋"]
        if res["沒處理秒"]:
            left.append({"id": m["id"], "start": m["start"], "end": m["end"], "做法": m["做法"], "沒處理秒": res["沒處理秒"]})
    blur = pick_blur(kept, cuts, a, b) if demo_blur else None   # 09-29：模糊只有測試示範才做，正式成品不模糊
    return {"範圍": [a, b], "刪除": cuts, "動作": kept, "停格": sorted(freezes, key=lambda f: f["at"]),
            "模糊": blur, "標記": marks, "警告": warnings, "學員聲線": voices, "重疊沒處理": left}


def clip_to_cuts(a: float, b: float, cuts: list[tuple[float, float]]) -> tuple[float, float]:
    for x, y in cuts:
        if x < a < y:
            a = y
        if x < b < y:
            b = x
    return a, b


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


SPEEDUP_MAX = 1.15   # 09-29 宇軒選 C：學員重念太長時最多再加快 15%


def speedup_for(gen_s: float, slot_s: float, max_speedup: float = SPEEDUP_MAX) -> float:
    """學員重念比時間格長時要加快幾倍（1.0～1.15，純函式）。"""
    if gen_s <= slot_s or slot_s <= 0:
        return 1.0
    return round(min(max_speedup, gen_s / slot_s), 4)


def _read_audio_tempo(path: Path, factor: float = 1.0) -> np.ndarray:
    """讀生成檔（48kHz 單聲道）；factor > 1 時用 ffmpeg atempo 加快（音高不變）。"""
    from bookclub import assemble

    if factor <= 1.0001:
        return assemble._read_audio(path)
    cmd = ["ffmpeg", "-loglevel", "error", "-i", str(path), "-af", f"atempo={factor:.4f}", "-vn", "-ac", "1",
           "-ar", str(SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


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
    # 先抽到暫存檔再換上（09-29）：中斷時不會留下截短的聲音檔被下次沿用
    tmp = dst.with_name(f"_抽取中_{dst.name}")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(video),
                    "-vn", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(tmp)], check=True)
    tmp.replace(dst)


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
    stacks: dict[str, list[int]] = {}   # 10-01 B 方案：同一處重疊兩邊都生成的，聲音相加
    for k, e in enumerate(d["動作"]):
        if e.get("疊放") and e["類型"] not in MUTE_KINDS:
            stacks.setdefault(e["重疊"], []).append(k)
    for oid, ks in stacks.items():
        if len(ks) < 2:
            continue
        s0 = max(0, min(spans[k][0] for k in ks))
        t0 = min(len(x), max(spans[k][1] for k in ks))
        mix = assemble.room_tone(x, s0, t0, t0 - s0, SR) if ROOM_UNDER else np.zeros(t0 - s0, np.float32)
        for k in ks:
            e = d["動作"][k]
            s, t = max(0, spans[k][0]), min(len(x), spans[k][1])
            clip = _read_audio_tempo(workdir / e["檔案"])
            gain = _gain(clip, x[s:t])
            part = assemble.fit_length((clip * gain).astype(np.float32), t - s)
            f = min(int(SR * JOIN_FADE_S), len(part) // 2)
            if f:
                part[:f] *= np.linspace(0, 1, f, dtype=np.float32)
                part[-f:] *= np.linspace(1, 0, f, dtype=np.float32)
            mix[s - s0:t - s0] += part
            placed[e["id"]] = {"增益": round(float(gain), 3), "疊放": oid}
        assemble.splice(y, s0, mix, SR)
    for k, (e, (s, t)) in enumerate(zip(d["動作"], spans)):
        s, t = max(0, s), min(len(x), t)
        if any(k in ks for ks in stacks.values() if len(ks) > 1):
            continue
        if e["類型"] in MUTE_KINDS:
            local = [sp for sp in spans if sp != (s, t)]
            new = assemble.room_tone(x, s, t, t - s, SR, avoid=local)
            assemble.splice(y, s, new, SR)
            continue
        long = e.get("停格秒") or (e.get("加快", 1.0) > 1.0)
        src = e["來源檔案"] if long else e["檔案"]
        clip = _read_audio_tempo(workdir / src, e.get("加快", 1.0))
        gain = _gain(clip, x[s:t])
        clip = (clip * gain).astype(np.float32)
        head = assemble.fit_length(clip, t - s)
        room = assemble.room_tone(x, s, t, len(clip) + (t - s), SR) if ROOM_UNDER else None
        if room is not None:
            head = head + room[:t - s]
        assemble.splice(y, s, head, SR)
        if e.get("停格秒"):
            n = int(round(e["停格秒"] * SR))
            tail = assemble.fit_length(clip[t - s:], n)
            if room is not None:
                tail = tail + assemble.fit_length(room[t - s:], n)
            f = min(int(SR * 0.02), len(tail) // 2)
            if f:
                tail[-f:] *= np.linspace(1, 0, f, dtype=np.float32)
            tails[e["id"]] = tail
        placed[e["id"]] = {"增益": round(float(gain), 3)}

    plist = pieces(a, b, d["刪除"], d["停格"])
    # 09-30：一段一段寫進檔案，不在記憶體裡接成一整條（整支 98 分鐘一條 48kHz 就 1.1 GB，以前同時握四、五條）
    dst = out / f"新聲音_{tag}.wav"
    tmp = out / f"_組聲音中_{dst.name}"
    total = 0
    with sf.SoundFile(str(tmp), "w", SR, 1, subtype="PCM_16") as fw:
        for seg in output_segments(x, y, plist, tails, a):
            fw.write(np.clip(seg, -1, 1))
            total += len(seg)
    tmp.replace(dst)
    del x, y
    return {"原聲": orig, "新聲音": dst, "片段": plist, "放置": placed, "長度": total / SR}


def output_segments(x: np.ndarray, y: np.ndarray, plist: list[dict], tails: dict, a: float):
    """原片時間軸上換好的聲音 y → 依片段（刪除、停格）一段一段吐出成品聲音（09-30 從 build_audio 抽出來，
    讓組聲音可以邊組邊寫檔）。x 是原聲（停格示範墊底噪用）。"""
    from bookclub import assemble

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
        yield seg
        if p["freeze"]:
            n = int(round(p["freeze"] * SR))
            fz = p["停格"]
            fill = tails.get(fz.get("edit")) if fz.get("edit") else None
            if fill is None:   # 停格示範：墊環境底噪
                fill = assemble.room_tone(x, e, e + 1, n, SR)
            yield assemble.fit_length(fill.astype(np.float32), n)


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


def avconcat_helper() -> Path | None:
    """macOS 上把 `avconcat.swift` 編譯到 ~/.cache/bookclub/avconcat（原始碼比較新才重編）；不是 macOS 或沒有 swiftc 回傳 None。"""
    import shutil
    import sys

    if sys.platform != "darwin" or not shutil.which("swiftc"):
        return None
    src = Path(__file__).with_name("avconcat.swift")
    exe = Path.home() / ".cache" / "bookclub" / "avconcat"
    if not exe.is_file() or exe.stat().st_mtime < src.stat().st_mtime:
        exe.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["swiftc", "-O", "-swift-version", "5", "-o", str(exe), str(src)], capture_output=True, text=True)
        if r.returncode != 0:
            return None
    return exe


def quicktime_ok(path: Path) -> bool | None:
    """用 macOS 內建的 avconvert（跟 QuickTime 同一套 AVFoundation）把整支轉成小尺寸，轉得完＝QuickTime 解得了。
    不是 macOS 回傳 None。轉出來的小檔案放暫存資料夾。"""
    import shutil
    import tempfile

    if not shutil.which("avconvert"):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run(["avconvert", "--source", str(path), "--preset", "Preset640x480",
                            "--output", str(Path(tmp) / "t.mov"), "--replace"], capture_output=True, text=True)
        return r.returncode == 0 and (Path(tmp) / "t.mov").is_file()


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
    helper = avconcat_helper()
    if helper:
        # macOS：用 AVFoundation 接（各段編碼參數不同，ffmpeg 接成一支 mp4 時 QuickTime 會在接縫解不了）
        mp4s = []
        for i in range(len(plan)):
            mp4 = work / f"{i:03d}.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(work / f"{i:03d}.ts"), "-c", "copy",
                            str(mp4)], check=True)
            mp4s.append(str(mp4))
        (work / "片段_mp4.txt").write_text("\n".join(mp4s) + "\n", encoding="utf-8")
        m4a = work / "聲音.m4a"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(audio), "-c:a", "aac", "-b:a", "128k", "-ac", "2",
                        str(m4a)], check=True)
        subprocess.run([str(helper), str(work / "片段_mp4.txt"), str(m4a), str(dst)], check=True, capture_output=True)
    else:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(audio),
                        "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ac", "2",
                        "-movflags", "+faststart", str(dst)], check=True)
    spent = time.time() - t0
    stat = {"接片段": "AVFoundation" if helper else "ffmpeg（QuickTime 可能在接縫解不了）", "片段數": len(plan), "重做段數": sum(1 for sg in plan if sg["做法"] == "重做"),
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
        res["QuickTime能解"] = quicktime_ok(path)
    res["通過"] = abs(res["長度誤差秒"]) < 0.1 and abs(res["聲畫差秒"]) < 0.1 and not res["剪點黑畫面"] \
        and res.get("解碼錯誤行數", 0) == 0 and res.get("QuickTime能解") is not False
    return res


# ---------- 標記清單、標字 ----------

def label_text(e: dict) -> str:
    if e["類型"] == "學員重念":
        return f"AI：{e['學員']} 重念（{e['聲線']}聲）"
    if e["類型"] == "名字整句換掉":
        return "AI：名字整句換掉" if e.get("候選") else "AI：老師整句重念（聲音重疊）"
    if e["類型"] == "局部消音" and e.get("重疊"):
        return "重疊處消音"
    if e["類型"] == "局部消音":
        return "局部消音" + ("（霧化還沒做，先墊底噪）" if e.get("霧化") else "")
    if e["類型"] == "學員名字消音":
        return f"AI：{e.get('學員', '學員')} 講到名字消音"
    if e["類型"] == "學員名字換代號":
        return f"AI：{e.get('學員', '學員')} 講到名字換代號（學員聲音生成，音色可能有差）"
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
        if e["類型"] not in MUTE_KINDS:
            a_, b_, n = diff_marks(e["text"], e.get("轉回文字"))
            row.update({"餵給模型的文字": e["text"], "送進模型的文字": e["生成用文字"] if e["生成用文字"] != e["text"] else None,
                        "轉回文字": e.get("轉回文字"), "稿子標記": a_, "轉回標記": b_, "不一樣字數": n,
                        "放回做法": e.get("放回做法"), "差異比例": e.get("差異比例")})
            if e.get("加快", 1.0) > 1.0:
                row["做了什麼"] += f"；比時間格長，加快 {e['加快'] - 1:.0%}"
            if e.get("停格秒"):
                row["做了什麼"] += f"；結尾停格 {e['停格秒']:.2f} 秒"
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
    # 10-02 第六批：到零點一秒（跟第 4、5 步畫面一樣，例如 0:42:59.8）
    from bookclub.timemap import t1

    return t1(x)


HEADER = ("**只處理了 {rng}；範圍內沒列在這裡的地方＝原片沒動。**\n\n"
          "文字欄就是模型拿到的稿子；稿子本身有錯字，是轉文字沒校對到，不是模型的問題；"
          "稿子對、轉回文字不對，才是模型念錯。\n\n"
          "【】框起來的是稿子跟轉回文字不一樣的地方（比對時不看標點）。")


def marks_md(rows: list[dict], rng: str, extra: list[str]) -> str:
    out = [f"# 處理標記清單（{rng}）", "", HEADER.format(rng=rng), ""] + extra + [""]
    for i, r in enumerate(rows, 1):
        # 10-02 第六批：時間同時列原片與成品（「原片 0:42:59.8–⋯／成品 0:38:26.4–⋯」）
        out.append(f"## {i}. 原片 {_t(r['原片'][0] if r.get('原片') else None)}–{_t(r['原片'][1] if r.get('原片') else None)}"
                   f"／成品 {_t(r['成品'][0] if r.get('成品') else None)}–{_t(r['成品'][1] if r.get('成品') else None)}")
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
            out.append(f"- 開口時間差：{p.get('開口差毫秒', '—')} 毫秒；整段對位差：{p.get('對位差毫秒', '—')} 毫秒；"
                       f"生成長度 vs 時間格：{p.get('長度差秒', '—')} 秒")
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
            f"<td>{_t(r['原片'][0] if r.get('原片') else None)}<br><small>{_t(r['原片'][1] if r.get('原片') else None)}</small></td>"
            f"<td>{_t(r['成品'][0] if r.get('成品') else None)}<br><small>{_t(r['成品'][1] if r.get('成品') else None)}</small></td>"
            f"<td><b>{html.escape(r['類型'])}</b> {html.escape(r['做了什麼'])}"
            + (f" <small>({html.escape(r['id'])})</small>" if r.get("id") else "")
            + (f"<br><small>生成 {r['生成秒數']:.1f} 秒・{html.escape(r.get('放回做法') or '')}"
               + (f" {r['差異比例']:+.0%}" if r.get('差異比例') is not None else "") + "</small>" if r.get("生成秒數") else "")
            + (f"<br><small>開口差 {p.get('開口差毫秒')} ms・對位差 {p.get('對位差毫秒')} ms</small>"
               if p.get("開口差毫秒") is not None else "")
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
            + "<div class=wrap><table><tr><th>#</th><th>原片時間</th><th>成品時間</th><th>做了什麼</th><th>要人聽</th></tr>"
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

def _env_db(x: np.ndarray, sr: int = SR, hop_s: float = 0.01) -> np.ndarray:
    f = int(sr * hop_s)
    n = len(x) // f
    if n == 0:
        return np.zeros(0)
    rms = np.sqrt(np.mean(x[: n * f].reshape(n, f).astype(np.float64) ** 2, axis=1)) + 1e-12
    return 20 * np.log10(rms)


def onset(x: np.ndarray, sr: int = SR, db: float = -25.0, min_run_s: float = 0.1) -> float | None:
    """開口時間（秒）：第一段「連續 min_run_s 秒都比這段最大聲低不到 db」的起點。
    要連續才算，避免小聲的雜音、呼吸被當成開口。"""
    e = _env_db(x, sr)
    if not len(e):
        return None
    on = e > e.max() + db
    run = int(round(min_run_s / 0.01))
    for i in range(0, len(on) - run + 1):
        if on[i:i + run].all():
            return i * 0.01
    return None


def envelope_lag(orig: np.ndarray, new: np.ndarray, sr: int = SR, max_lag_s: float = 2.0) -> float | None:
    """整段對位差（秒，正數＝生成的比較晚）：兩條音量曲線（dB，低於最大聲 40 dB 的當安靜）互相比對，
    找最吻合的平移量。聲音不同人，但說話與停頓的節奏對得上時，這個值接近 0。"""
    a, b = _env_db(orig, sr), _env_db(new, sr)
    n = min(len(a), len(b))
    if n < 50:
        return None
    a = np.clip(a[:n] - a[:n].max() + 40, 0, None)
    b = np.clip(b[:n] - b[:n].max() + 40, 0, None)
    a, b = a - a.mean(), b - b.mean()
    if not a.any() or not b.any():
        return None
    m = min(int(max_lag_s / 0.01), n // 2)   # 短片段：平移不超過一半長度
    best, best_v = 0, None
    for lag in range(-m, m + 1):
        v = float(np.dot(a[max(0, -lag):n - max(0, lag)], b[max(0, lag):n - max(0, -lag)]))
        if best_v is None or v > best_v:
            best, best_v = lag, v
    return best * 0.01


def measure(d: dict, orig: np.ndarray, placed: np.ndarray) -> dict:
    """每一筆換上去的聲音：開口時間差（生成 − 原本，毫秒）；生成長度 vs 時間格（秒）。placed 是原片時間軸上換好的聲音。"""
    a = d["範圍"][0]
    res = {}
    for e in d["動作"]:
        if e["類型"] in MUTE_KINDS:
            continue
        s, t = int((e["start"] - a) * SR), int((e["end"] - a) * SR)
        o1, o2 = onset(orig[s:t]), onset(placed[s:t])
        lag = envelope_lag(orig[s:t], placed[s:t])
        gen = e.get("生成秒數")
        res[e["id"]] = {"開口差毫秒": None if o1 is None or o2 is None else int(round((o2 - o1) * 1000)),
                        "對位差毫秒": None if lag is None else int(round(lag * 1000)),
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
                 label: bool = False, methods: list[str] = ("sw",), tag: str | None = None,
                 demo_freeze: bool = False, demo_blur: bool = False, min_free_gb: float = 5.0,
                 include_kept: bool = False) -> dict:
    import shutil

    import soundfile as sf

    from bookclub import review

    workdir = Path(workdir).expanduser()
    video = Path(video).expanduser() if video else review.video_path(workdir)
    if not video or not video.is_file():
        raise FileNotFoundError(wd.video_missing_message(workdir) if video is None else f"找不到原片：{video}，用 --video 指定。")
    a, b = snap(start), snap(end)
    tag = tag or f"{int(a // 60)}-{int(b // 60)}"
    out = workdir / "輸出"
    out.mkdir(parents=True, exist_ok=True)
    # 10-02 第五批：複製來的工作區，輸出資料夾裡這次要寫的檔如果是指回原本工作區的連結，先拿掉連結（ffmpeg、寫音檔會順著連結覆蓋原本的）
    for f in out.iterdir():
        if f.is_symlink() and f"_{tag}" in f.name:
            f.unlink()
    from bookclub import finalcheck

    pending = finalcheck.redo_pending(workdir, a, b)
    if pending:   # 10-02 第五批：退回重做做到一半，還沒重新生成的那幾句組進去會是原聲（名字還在），不組
        raise RuntimeError(f"第 5 步退回重做的還有 {len(pending)} 句還沒重新生成（{'、'.join(pending[:10])}），"
                           "先在第 4 步按「開始執行」把它們做完再組裝；現在組裝的話這幾句會是原本的聲音")
    d = build_decisions(workdir, a, b, demo_freeze=demo_freeze, demo_blur=demo_blur, include_kept=include_kept)
    if d["重疊沒處理"]:   # 09-30：重疊處還留著學員原聲就不輸出（隱私），先擋下來
        where = "、".join(f"{wd.fmt_time(x['start'])}（{x['做法']}，{x['沒處理秒']:.2f} 秒）" for x in d["重疊沒處理"])
        raise RuntimeError(f"有 {len(d['重疊沒處理'])} 處聲音重疊還留著原聲，沒有輸出成品：{where}")
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
    old = wd.read_json(out / f"輸出摘要_{tag}.json", default=None) or {}
    if old.get("範圍") == [a, b]:
        summary["輸出"] = old.get("輸出", {})   # 只重跑某幾種做法時，其他做法的紀錄留著

    # 精準度（原片時間軸上換好的聲音 vs 原聲）
    orig, _ = sf.read(str(au["原聲"]), dtype="float32")
    placed_track = _placed_track(workdir, d, orig)
    prec = measure(d, orig, placed_track)
    del placed_track, orig   # 09-30：用完馬上放掉（整支一條 1.1 GB），後面的處理紀錄分段讀檔
    new, _ = sf.read(str(au["新聲音"]), dtype="float32")
    cut_joins = [to_output_time(x, plist) for x, _y in d["刪除"]]
    cut_joins = [j if j is not None else to_output_time(_y, plist) for j, (_x, _y) in zip(cut_joins, d["刪除"])]
    summary["精準度"] = _prec_summary(prec)
    summary["剪點聲音"] = join_jumps(new, [j for j in cut_joins if j is not None])
    del new

    rng = f"{wd.fmt_time(a)}–{wd.fmt_time(b)}"
    rows = build_marks(d, plist, prec)
    extra = [f"成品長度 {wd.fmt_time(expected)}（原片 {b - a:.1f} 秒，刪除 {summary['刪除秒']:.1f} 秒，停格 {summary['停格秒']:.2f} 秒）"]
    if include_kept:
        extra.append("測試：第 3 步設成保留原聲的學員也照樣重念（--include-kept）。")
    if demo_freeze or demo_blur:
        extra.append("測試：含停格／模糊示範，不是正式成品。")
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
        # 09-29：先輸出到暫存檔名，驗證通過才換成正式檔名；中斷的殘檔、驗證沒過的檔不會被當成做好了
        tmp = out / f"_輸出中_成品_{tag}_{m}.mp4"
        if m == "smart":
            spent, stat = render_smart(video, d, plist, au["新聲音"], tmp, out / f"_片段_{tag}")
        else:
            spent, stat = render_full(video, d, plist, au["新聲音"], tmp, m), {}
        ver = verify(tmp, expected, joins, full_decode=True)
        if ver["通過"]:
            tmp.replace(dst)
            final = dst
        else:
            final = out / f"成品_{tag}_{m}_驗證沒過.mp4"
            tmp.replace(final)
        summary["輸出"][m] = {"耗時秒": round(spent, 1), "大小MB": round(final.stat().st_size / 1e6, 1),
                            "倍速": round((b - a) / spent, 2), "推估整支98分鐘秒": round(spent * 5864 / (b - a)),
                            **stat, "驗證": ver, "檔案": final.name}
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
                                  "標字時段數": len(wins), "驗證": verify(dst, expected, joins, full_decode=True),
                                  "檔案": dst.name}
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
        if e["類型"] in MUTE_KINDS:
            continue
        s, t = int((e["start"] - a) * SR), int((e["end"] - a) * SR)
        s, t = max(0, s), min(len(y), t)
        clip = assemble._read_audio(workdir / e["檔案"])
        y[s:t] = assemble.fit_length(clip, t - s)
    return y


def _prec_summary(prec: dict) -> dict:
    ds = [abs(v["開口差毫秒"]) for v in prec.values() if v["開口差毫秒"] is not None]
    xs = [abs(v["對位差毫秒"]) for v in prec.values() if v.get("對位差毫秒") is not None]
    ls = [v["長度差秒"] for v in prec.values() if v["長度差秒"] is not None]
    return {"筆數": len(prec), "開口差平均毫秒": round(float(np.mean(ds))) if ds else None,
            "開口差最大毫秒": max(ds) if ds else None, "開口差超過100毫秒筆數": sum(1 for x in ds if x > 100),
            "對位差平均毫秒": round(float(np.mean(xs))) if xs else None, "對位差最大毫秒": max(xs) if xs else None,
            "對位差超過100毫秒筆數": sum(1 for x in xs if x > 100),
            "長度差平均秒": round(float(np.mean(ls)), 2) if ls else None,
            "長度差最大秒": round(max(ls, key=abs), 2) if ls else None}
