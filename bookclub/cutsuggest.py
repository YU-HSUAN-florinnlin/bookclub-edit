"""影片分析：建議刪除段落（09-26 加，給第 3 步「開始前 3 件事 ③」與清單用）。

找五種可以整段刪掉的地方：開頭空白、結尾道別、直播互動（老師念聊天區留言）、小組討論前後、技術問題。
老師靜音咳嗽這類要看畫面才知道的**不偵測**（09-26 宇軒）。

做法（選開發快、跑得快、夠準的）：
1. **逐字稿交給另一個 Claude 呼叫**（`claude -p`），整支一次送（第一堂 1446 句、1.4 萬字，一次放得下，
   Claude 看得到全片才分得出開頭、結尾）。跟段落分析**同時**在背景跑，不改段落分析的提示詞
   （`校對/段落_文字.json` 快取才不會失效）。逐字稿裡超過 8 秒沒人說話的地方插一行「空白 N 秒」
2. **畫面凍結只在候選附近檢查**：09-26 實測整支 98 分鐘全解碼要約 12 分鐘（只解關鍵畫面雖然 2 秒，但 Zoom 錄影
   36 秒才一張關鍵畫面，沒用），超過 3 分鐘，所以只對「開頭空白」「技術問題」候選前後各 5 秒跑 ffmpeg
   `freezedetect`（每秒 2 張、縮到 160 寬、硬體解碼），畫面靜止的比例寫進原因，當佐證、不改起訖
   （Zoom 畫面常常本來就不動，只靠凍結判斷太多誤抓）
3. 剪點對齊附近安靜處（沿用 `review.snap_to_quiet`）；影片開頭、結尾那一端不對齊

結果快取成 `校對/刪除建議.json`（存在就不再呼叫 Claude）。Claude 失敗就沒有建議，不擋其他步驟。
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from bookclub import workdir as wd

KINDS = ("開頭空白", "結尾道別", "直播互動", "小組討論前後", "技術問題")
GAP_MARK_S = 8.0          # 逐字稿裡超過這麼多秒沒人說話，插一行「空白 N 秒」
FREEZE_KINDS = ("開頭空白", "技術問題")
FREEZE_PAD_S = 5.0
FREEZE_MAX_S = 600.0      # 候選太長（超過 10 分鐘）就不檢查畫面，省時間
MIN_LEN_S = 2.0

PROMPT = """你在協助剪輯一支讀書會的 Zoom 錄影。請找出「可以整段刪掉」的地方，只找下面五種：

1. 開頭空白：正式開始之前（等人進來、測試聲音、還沒開始上課）。起點一律是影片開頭 0:00:00
2. 結尾道別：課程內容結束之後的道別（「再見」「下次見」「謝謝大家」「拜拜」）。從開始道別那一句刪到影片結尾
3. 直播互動：老師念聊天區的留言、回應聊天區（「聊天區」「留言」「我看到大家」「有人打字說」）
4. 小組討論前後：宣布進分組討論室到大家回來之間的過場與空白（「分組」「進小組」「討論室」「回來了」＋附近的長空白）
5. 技術問題：分享畫面、聲音、網路出問題與排除的過程（「分享畫面」「看得到嗎」「卡住」「聽得到嗎」）

注意：
- 冥想引導、帶領練習中的長停頓**不是**空白，不要列
- 語音轉文字在沒人說話時可能冒出不相干的字（例如「字幕」「謝謝觀看」「請訂閱」），這種地方可能其實是空白
- 只列有把握的；老師講課、學員分享的內容不要列
- 同一件事前後連在一起的，合成一段

下面是逐字稿，每行格式：「起始時間-結束時間|文字」；「——空白 N 秒——」表示那段時間沒人說話。
影片總長 {duration}。

只輸出 JSON，不要其他文字，格式：
{{"建議":[{{"類型":"開頭空白／結尾道別／直播互動／小組討論前後／技術問題","起":"h:mm:ss.s","迄":"h:mm:ss.s","原因":"一句話說明看到什麼線索"}}]}}

逐字稿：
{lines}
"""


def cut_suggest_path(workdir: Path) -> Path:
    return Path(workdir) / "校對" / "刪除建議.json"


def _hms(sec: float) -> str:
    h, rem = divmod(max(0.0, sec), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:04.1f}"


def format_lines(sentences: list[dict], duration: float, gap_s: float = GAP_MARK_S) -> str:
    """逐字稿轉成給 Claude 看的行；長空白插一行（純函式）。"""
    out = []
    prev_end = 0.0
    for s in sentences:
        if s["start"] - prev_end > gap_s:
            out.append(f"——空白 {s['start'] - prev_end:.0f} 秒——")
        out.append(f"{_hms(s['start'])}-{_hms(s['end'])}|{s['text']}")
        prev_end = max(prev_end, s["end"])
    if duration - prev_end > gap_s:
        out.append(f"——空白 {duration - prev_end:.0f} 秒（到影片結尾）——")
    return "\n".join(out)


def parse_time(text: str) -> float:
    parts = str(text).strip().replace("：", ":").split(":")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def parse_reply(text: str, duration: float) -> list[dict]:
    """Claude 的回覆 → 建議清單（純函式）：類型不對、時間看不懂、太短的丟掉；開頭空白從 0 開始、
    結尾道別刪到結尾；照時間排、同類型重疊的合併。"""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("Claude 的回覆裡找不到 JSON")
    raw = json.loads(m.group(0)).get("建議", [])
    out = []
    for r in raw:
        kind = str(r.get("類型", "")).strip()
        if kind not in KINDS:
            continue
        try:
            a, b = parse_time(r["起"]), parse_time(r["迄"])
        except (KeyError, ValueError):
            continue
        if kind == "開頭空白":
            a = 0.0
        if kind == "結尾道別":
            b = duration
        a, b = max(0.0, a), min(duration, b)
        if b - a < MIN_LEN_S:
            continue
        out.append({"類型": kind, "start": round(a, 2), "end": round(b, 2), "原因": str(r.get("原因", "")).strip()})
    out.sort(key=lambda x: x["start"])
    merged: list[dict] = []
    for x in out:
        if merged and merged[-1]["類型"] == x["類型"] and x["start"] <= merged[-1]["end"] + 1.0:
            merged[-1]["end"] = max(merged[-1]["end"], x["end"])
            continue
        merged.append(x)
    return merged


def frozen_ratio(video: Path, start: float, end: float) -> float | None:
    """[start, end] 之間畫面靜止的比例（ffmpeg freezedetect，每秒 2 張、縮小、硬體解碼）。失敗回 None。"""
    a = max(0.0, start - FREEZE_PAD_S)
    length = end - a + FREEZE_PAD_S
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-hwaccel", "auto", "-ss", f"{a:.2f}", "-t", f"{length:.2f}",
           "-i", str(video), "-an", "-vf", "fps=2,scale=160:-2,freezedetect=n=0.003:d=2", "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=max(120, length))
    except (OSError, subprocess.TimeoutExpired):
        return None
    starts = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"freeze_end: ([\d.]+)", r.stderr)]
    ends += [length] * (len(starts) - len(ends))      # 到結尾都還靜止，沒有 freeze_end
    lo, hi = start - a, end - a
    frozen = sum(max(0.0, min(e, hi) - max(s, lo)) for s, e in zip(starts, ends))
    return round(frozen / max(1e-6, hi - lo), 2)


def suggest_cuts(workdir: str | Path, sentences: list[dict] | None = None, *, duration: float | None = None,
                 video: str | Path | None = None, model: str | None = None, log=print, force: bool = False) -> dict:
    """找建議刪除段落，寫 `校對/刪除建議.json`。已經有就直接讀（`force` 重跑）。"""
    from bookclub.config import load_settings
    from bookclub.review import snap_to_quiet
    from bookclub.turns import call_claude

    workdir = Path(workdir).expanduser()
    path = cut_suggest_path(workdir)
    cached = wd.read_json(path, default=None)
    if cached is not None and not force:
        log(f"[刪除建議] 沿用 {path.name}（{len(cached.get('建議', []))} 筆）")
        return cached
    merged = wd.read_json(wd.merged_transcript_path(workdir), default={}) or {}
    sentences = sentences if sentences is not None else merged.get("sentences", [])
    duration = duration or merged.get("duration") or (sentences[-1]["end"] if sentences else 0.0)
    if video is None:
        from bookclub.review import video_path

        video = video_path(workdir)
    model = model or load_settings().claude_models.turns

    t0 = time.time()
    reply = None
    for attempt in range(2):   # 失敗重送一次（跟段落分析一樣）
        try:
            reply = call_claude(PROMPT.format(duration=_hms(duration), lines=format_lines(sentences, duration)), model)
            items = parse_reply(reply, duration)
            break
        except Exception as exc:  # noqa: BLE001
            if attempt:
                raise
            log(f"[刪除建議] Claude 第一次失敗，重送：{exc}")
    claude_s = time.time() - t0

    t1 = time.time()
    for x in items:
        if x["start"] > 0.05:
            x["start"], ca = snap_to_quiet(workdir, x["start"])
        else:
            ca = True
        if x["end"] < duration - 0.05:
            x["end"], cb = snap_to_quiet(workdir, x["end"])
        else:
            cb = True
        x["剪點對齊安靜處"] = [ca, cb]
    snap_s = time.time() - t1

    t2 = time.time()
    checked = 0
    if video and Path(video).exists():
        for x in items:
            if x["類型"] in FREEZE_KINDS and x["end"] - x["start"] <= FREEZE_MAX_S:
                r = frozen_ratio(Path(video), x["start"], x["end"])
                checked += 1
                if r is not None:
                    x["畫面靜止比例"] = r
                    x["原因"] = f"{x['原因']}（畫面靜止約 {r:.0%}）".strip()
    freeze_s = time.time() - t2

    for k, x in enumerate(items, start=1):
        x["id"] = f"S{k}"
    data = {"建議": items, "模型": model, "句數": len(sentences),
            "耗時": {"Claude": round(claude_s, 1), "對齊安靜處": round(snap_s, 1), "畫面凍結": round(freeze_s, 1),
                     "畫面檢查段數": checked},
            "統計": {k: sum(1 for x in items if x["類型"] == k) for k in KINDS}}
    wd.write_json(path, data)
    log(f"[刪除建議] 完成：{len(items)} 筆（Claude {claude_s:.0f} 秒、畫面檢查 {checked} 段 {freeze_s:.0f} 秒）→ {path}")
    return data
