"""第 4 步「AI 執行」一個指令跑完：老師名字 → 學員重念 → 保留原聲學員的名字 → 組裝（09-29 宇軒）。

`bookclub run execute <工作區> [--start 0:00 --end 1:38:00] [--methods sw]`

情境：老師有時候已經自己檢查完全片、標好要改的地方、挑好參考聲音，想直接請 AI 從第 4 步做下去。
限制：學員的話要照逐字稿重念，所以**第 1 步轉文字還是要跑**（電腦自動）；老師參考音（第 2 步）要選好、
會出現的名字都要有英文代號（沒有的可以「幫還沒代號的自動配」），前置檢查會擋；能省掉的是第 3 步逐筆覆核的人工。
「匯入老師的標記清單」先不做——要等宇軒問老師標記是什麼形式、參考聲音是給音檔還是時間點。

每一步都可以中斷續跑、做過的跳過：
- 老師名字：`gen names`（排計畫＋`tts.generate_teacher`，那邊本來就沿用已經生成好的句子）；計畫裡每一句都已經有
  「放回時間格」就跳過
- 學員重念：`gen students`（`students.generate_students`，本來就沿用已經生成好的段落）；範圍內每一段都已經生成、
  文字沒變就跳過
- 保留原聲學員的名字：`gen stunames`（`studentgen.generate`）；選了換成代號的每一句都生成好（或挑不到參考音、退回直接消音）
  就跳過；直接消音的不用生成，組裝時處理
- 組裝：`render video`；成品影片比它讀的東西（覆核決定、名字計畫、老師／學員紀錄）都新就跳過
- 輸出做法預設「整段軟體編碼」（09-29 宇軒：Mac、Windows 結果一樣）

進度寫在 `生成/執行進度.json`（網頁第 4 步讀），終端機照常印。這支不改生成與組裝的邏輯，只負責排順序、判斷做過沒有。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from bookclub import untranscribed
from bookclub import workdir as wd

STEPS = (("老師名字", "老師提到名字：用老師 AI 聲音整句重念（gen names）"),
         ("學員重念", "學員段落：匿名聲線重念（gen students）"),
         ("保留原聲學員名字", "保留原聲的學員講到名字：選了換成代號的，用他自己的聲音生成（gen stunames）"),
         ("組裝", "換聲音＋刪除＋停格，輸出成品影片（render video）"))


def progress_path(workdir: Path) -> Path:
    return Path(workdir) / "生成" / "執行進度.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def default_methods() -> list[str]:
    """整段軟體編碼（09-29 宇軒定案：Mac、Windows 同一套 libx264，結果一樣；硬體編碼、只重做片段是選項）。"""
    return ["sw"]


_METHODS_CACHE: list | None = None


def method_options() -> list[list[str]]:
    """網頁第 4 步「進階設定」裡的輸出方式（10-01 第三批 8）：標準輸出（軟體編碼，Mac、Windows／WSL 都能跑）一定有；
    硬體編碼只有 Mac 的 ffmpeg 有 h264_videotoolbox 才列；只重做有動到的片段要 Mac 的 AVFoundation（Windows 接出來的
    QuickTime 解不了），也只在 Mac 列。回傳 [[值, 畫面上的名稱], ...]。"""
    global _METHODS_CACHE
    if _METHODS_CACHE is not None:
        return _METHODS_CACHE
    out = [["sw", "標準輸出"]]
    if sys.platform == "darwin" and shutil.which("ffmpeg"):
        try:
            enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            enc = ""
        if "h264_videotoolbox" in enc:
            out.append(["hw", "快的輸出方式（用這台 Mac 的顯示晶片，只有 Mac 能用）"])
        out.append(["smart", "只重做有動到的片段（只有 Mac 能用）"])
    _METHODS_CACHE = out
    return out


def video_duration(workdir: Path) -> float | None:
    analysis = wd.read_json(wd.analysis_result_path(workdir), default={}) or {}
    merged = wd.read_json(wd.merged_transcript_path(workdir), default={}) or {}
    return analysis.get("影片長度") or merged.get("duration")


def request_stop(workdir: str | Path) -> None:
    """`POST /api/execute/stop`：放停止旗標，生成完目前這一次就停（組裝中按的話等組裝做完才停）。"""
    from bookclub import tts

    f = tts.stop_flag_path(Path(workdir))
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(_now(), encoding="utf-8")


def _clear_stop(workdir: Path) -> None:
    from bookclub import tts

    tts.stop_flag_path(workdir).unlink(missing_ok=True)


def stop_requested(workdir: str | Path) -> bool:
    from bookclub import tts

    return tts.stop_flag_path(Path(workdir)).exists()


def mark_interrupted(workdir: str | Path) -> bool:
    """網頁伺服器啟動（或切換專案）時：進度檔殘留「進行中」＝上次跑到一半伺服器被關掉，改成「中斷」。
    回傳有沒有改。"""
    path = progress_path(Path(workdir))
    prog = wd.read_json(path, default=None)
    if not prog:
        return False
    hit = [k for k, st in (prog.get("步驟") or {}).items() if st.get("狀態") == "進行中"]
    if not hit:
        return False
    for k in hit:
        prog["步驟"][k].update({"狀態": "中斷", "訊息": "上次跑到一半網頁伺服器被關掉了；按「開始執行」會接著做（做好的不重做）"})
    prog["中斷"] = True
    wd.write_json(path, prog)
    return True


def tag_for(a: float, b: float) -> str:
    return f"{int(a // 60)}-{int(b // 60)}"      # 跟 render.render_video 的預設檔名標記一樣


# ---------- 前置檢查（不載入模型） ----------

def precheck(workdir: str | Path) -> dict:
    """開始之前先看缺什麼，免得跑到一半才失敗。回傳 {可以開始, 缺[], 提醒[]}。"""
    from bookclub import students

    workdir = Path(workdir)
    missing, notes = [], []
    if not wd.merged_transcript_path(workdir).exists():
        missing.append("還沒有逐字稿：第 1 步轉文字要先跑（學員的話要照逐字稿重念）")
    if not (workdir / "校對" / "段落.json").exists():
        missing.append("還沒有段落分析（校對/段落.json）：第 1 步影片分析要跑完")
    ref = wd.ref_dir(workdir)
    if not ((ref / "ref.wav").is_file() and (ref / "ref.txt").is_file()):
        missing.append("還沒選定老師參考音（參考音/ref.wav、ref.txt）：第 2 步，或 bookclub ref use")
    pool = students.voice_pool()   # 09-30：每位學員各自的聲線（候選_0928），沒有候選才用暫定的
    lack = [g for g in ("男", "女") if not pool.get(g)]
    if lack:
        missing.append(f"找不到學員的替代聲音（{'、'.join(lack)}聲）：{students.voice_dir() / students.CANDIDATE_DIR}"
                       f" 或 {'、'.join(str(students.default_refs()[g]) for g in lack)}")
    from bookclub import review

    if not review.video_path(workdir):
        missing.append(wd.video_missing_message(workdir))   # 10-02 第五批：寫清楚記的是哪裡、怎麼處理
    from bookclub import epcodes

    lack_codes = epcodes.missing(workdir)
    if lack_codes:
        missing.append(f"有 {len(lack_codes)} 個名字這一集還沒選代號：第 3 步開始前 ②（學員）或 ③（其他名稱）選好，"
                       "或按「幫還沒代號的自動配」")
    # 09-30：老師提到名字裡有「換不了代號」的（句子裡找不到名字），不處理的話成品會照原聲念出名字 → 不能開始
    if wd.read_json(wd.names_path(workdir), default=None):
        from bookclub import nameplan

        try:
            stuck = nameplan.compute_plan(workdir)["要人處理"]
        except Exception:  # noqa: BLE001 — 排不出計畫的話，生成那一步會講清楚
            stuck = []
        if stuck:
            names = wd.read_json(wd.names_path(workdir), default={}) or {}
            decisions = wd.read_json(review.name_decisions_path(workdir), default={}) or {}
            cands = review.effective_name_candidates(workdir, names.get("candidates", []), decisions)
            when = {str(c.get("id") or i): c["start"] for i, c in enumerate(cands, start=1)}
            where = "、".join(wd.fmt_time(when[str(m["候選"])]) for m in stuck if str(m["候選"]) in when)
            missing.append(f"老師提到名字有 {len(stuck)} 筆還處理不了（{where}）：句子裡找不到名字、換不了代號，成品會照原聲念出來。"
                           "在第 3 步那一筆的卡片上改好要重念的句子，或改成直接消音")
    if review.people_list_missing(workdir):   # 10-02 第七批（A3）：名冊上沒有的名字沒人看過，成品可能照原聲念出來
        missing.append(review.PEOPLE_MISSING.replace("跑成功之後才能標完成", "跑成功、在第 3 步 ③ 決定完再開始"))
    dec = review.load_decisions(workdir)
    if not any(dec["開始前確認"].values()):
        notes.append("第 3 步還沒覆核：照第 1 步的建議做（名字整句換掉、學員全部重念、建議刪除的段落不刪）")
    return {"可以開始": not missing, "缺": missing, "提醒": notes, "缺代號": len(lack_codes)}


# ---------- 開始前總檢查（10-01 宇軒 7-5，只讀） ----------

GEN_SPEED = 16.5          # 這台 Mac 每 1 秒聲音約 14–19 秒（09-30 實測）
ASSEMBLE_S = 21 * 60      # 整支組裝約 21 分鐘（09-30 實測）
LONG_TEACHER_S = 10.0
OUTSIDE_MIN_S = 0.3       # 學員的話落在段落外面超過這麼久才算
STUDENT_TURN_KEY = "重疊:學員段落"   # 10-04 #111：「請看一眼」裡學員段落裡自動處理的重疊那一列


UNTRANSCRIBED_KEY = "沒有字"            # 10-04 #117：「一定要處理」每一處一列，鍵 沒有字:{起點秒:.1f}
UNTRANSCRIBED_LOOK_KEY = "沒有字:彙總"   # 10-04 #117：「請看一眼」比較短的、在學員段落裡的合成一列
UNTRANSCRIBED_MOVE_S = 1.0              # 重算之後起點移動不超過這麼多秒，之前按的「我聽過了」照算
TEACHER_TURN_KEY = "聲紋:學員段落是老師"   # 10-04 #127：「請看一眼」每一段一列，鍵 聲紋:學員段落是老師:{段落編號}


def untranscribed_key(start: float, heard: set) -> str:
    """#117 那一列的鍵（純函式）：之前按過「我聽過了」的鍵起點跟現在差不超過 1 秒，就沿用那個鍵（重算之後小幅移動
    不用重聽）；否則是新的鍵 沒有字:{起點秒:.1f}。"""
    best = None
    for k in heard:
        if not str(k).startswith(UNTRANSCRIBED_KEY + ":") or k == UNTRANSCRIBED_LOOK_KEY:
            continue
        try:
            x = float(str(k).split(":", 1)[1])
        except ValueError:
            continue
        d = abs(x - start)
        if d <= UNTRANSCRIBED_MOVE_S and (best is None or d < best[0]):
            best = (d, k)
    return best[1] if best else f"{UNTRANSCRIBED_KEY}:{start:.1f}"


def _untranscribed_rows(workdir: Path, dec: dict, heard: set, stu_turns: list[dict], kept: set, index: dict,
                        row, must: list) -> list[dict]:
    """#117：算「有人聲但沒有字」的區間，≥3 秒的直接加到「一定要處理」；回傳要放「請看一眼」的那幾處。
    資料不夠（沒有安靜處清單）或算不出來 → 不列、不擋。"""
    from bookclub import review

    try:
        regions = untranscribed.from_merged(wd.read_json(wd.merged_transcript_path(workdir), default={}))
    except Exception:  # noqa: BLE001 — 算不出來不擋總檢查
        regions = None
    if not regions:
        return []
    cuts = [(float(c["start"]), float(c["end"])) for c in dec.get("刪除段落") or [] if c.get("狀態") != "還原"]
    again = [t for t in stu_turns if t["說話者"] not in kept]   # 會整段重念的學員段落（保留原聲的不算）
    look = []
    for r in regions:
        kind, secs = untranscribed.classify(r, cuts, [(t["start"], t["end"]) for t in again])
        if kind is None:
            continue
        a, b = r["start"], r["end"]
        if kind == "一定要處理":
            key = untranscribed_key(a, heard)
            row(must, key, a, b,
                f"這裡有人講話（約 {secs:.1f} 秒）但逐字稿沒有字，工具找不到這裡的名字。請聽一下："
                "有提到名字就到第 3 步手動補名字卡；沒有就按「我聽過了」", ack=True,
                name=f"{t1(a)} 沒有字的地方",
                todo="有提到名字：到第 3 步手動新增名字卡（起訖填這一段時間），補好之後回來按「我聽過了」")
            must[-1].update({"人聲秒": secs, "小段數": r["小段數"],
                             "聽過字": "我聽過了，沒有提到名字（或已經補好名字卡）"})
            continue
        home = next((t for t in again if t["start"] <= (a + b) / 2 <= t["end"]), None)
        look.append({"start": a, "end": b, "秒": secs, "類別": kind,
                     "名稱": review.item_name(index, f"學員段落:{home['id']}") if home and kind == "學員段落" else ""})
    return look


def _uncovered(a: float, b: float, ranges: list[tuple[float, float]]) -> float:
    from bookclub import assemble

    return sum(e - s for s, e in assemble.subtract(a, b, ranges))


def t1(sec: float) -> str:
    """秒數 → 「0:53:18.8」（總檢查的說明要看得出零點幾秒的差別；wd.fmt_time 只到秒）。"""
    sec = round(max(0.0, float(sec)), 1)   # 10-01：先四捨五入，59.96 秒不會變成「:60.0」
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:04.1f}"


def _gap_rows(a: float, b: float, named: list[dict], min_s: float) -> tuple[list[tuple[float, float]], list[dict]]:
    """[a, b] 裡沒被任何處理蓋到的小段（每段至少 min_s 秒），與蓋到一部分的那幾筆（純函式，10-01 1-4）。"""
    from bookclub import assemble

    left = [(s, e) for s, e in assemble.subtract(a, b, [(x["start"], x["end"]) for x in named]) if e - s >= min_s]
    hit = [x for x in named if x["start"] < b and a < x["end"]]
    return left, hit


def _fix_paths(a: float, b: float, near: list[dict], index: dict, *, turn: dict | None = None) -> list[dict]:
    """「聽了有學員的聲音」可以走的路（10-01 1-3）：每一條帶到第 3 步，起訖先填好。
    near：這段前後 0.5 秒內的處理（可以延長包住）；turn：最近的學員段落（可以把起訖改大包住）。"""
    paths = []
    if turn is not None:
        key = f"學員段落:{turn['id']}"
        name = (index.get(key) or {}).get("名稱") or f"學員段落 {wd.fmt_time(turn['start'])}"
        paths.append({"文字": f"把〈{name}〉的起訖改大，包住這一段", "第3步": key,
                      "改時間": {"類型": "學員發言", "id": turn["id"], "名稱": name,
                              "start": round(min(turn["start"], a), 3), "end": round(max(turn["end"], b), 3)}})
    for x in near:
        info = index.get(x.get("鍵") or "")
        if not info or not info.get("改時間") or info.get("類型") in ("學員段落",) or info.get("疊放"):
            continue
        if info.get("類型") == "名字" and not info.get("老師整段"):
            continue   # 老師提到名字的範圍照句子走，延長要在卡片上改重念範圍
        paths.append({"文字": f"把〈{info['名稱']}〉的起訖改大，包住這一段", "第3步": info["第3步"],
                      "改時間": {"類型": info["改時間"], "id": info["id"], "名稱": info["名稱"], "老師整段": bool(info.get("老師整段")),
                              "start": round(min(info["start"], a), 3), "end": round(max(info["end"], b), 3)}})
    who = turn.get("說話者") if turn else None
    paths += [{"文字": "新增一筆「漏抓的發言」（這一段用學員的匿名聲音重念）",
               "新增": {"類型": "學員發言", "start": round(a, 3), "end": round(b, 3), **({"說話者": who} if who else {})}},
              {"文字": "新增一筆「消音」（只拿掉這一段的聲音）", "新增": {"類型": "局部消音", "start": round(a, 3), "end": round(b, 3)}},
              {"文字": "新增一筆「重疊」（老師和學員同時在講）", "新增": {"類型": "重疊", "start": round(a, 3), "end": round(b, 3)}}]
    return paths


def coverage(workdir: Path, dec: dict, turns: list[dict]) -> dict:
    """會把原聲換掉或拿掉的範圍，每一筆帶畫面上的名稱（說明「哪一筆蓋到了」用）。總檢查與第 3 步「切在外面的這幾秒」共用。
    回傳 {index, items（學員重念）, plan（老師的計畫）, named[{start, end, 名稱, 鍵}], gen_name}。"""
    from bookclub import assemble, nameplan, review, students

    index = review.item_index(workdir, dec, turns)
    try:
        items, _ = students.build_items(workdir)
    except FileNotFoundError:
        items = []
    plan = nameplan.compute_plan(workdir) if wd.read_json(wd.names_path(workdir), default=None) else {"生成": [], "消音": [], "要人處理": []}

    def gen_name(g: dict) -> tuple[str, str | None]:
        """老師重念那一筆 → (名稱, 卡片的鍵)。"""
        cands = [str(c) for c in g.get("候選", [])]
        if cands and f"名字:{cands[0]}" in index:
            info = index[f"名字:{cands[0]}"]
            return (info["名稱"] if info.get("老師整段") else f"老師重念 {wd.fmt_time(g['slot'][0])}（{info['名稱']}）"), f"名字:{cands[0]}"
        ovs = g.get("重疊項目") or []
        if ovs and f"重疊:{ovs[0]}" in index:
            return f"{index[f'重疊:{ovs[0]}']['名稱']} 的老師那一句", f"重疊:{ovs[0]}"
        return f"老師重念 {wd.fmt_time(g['slot'][0])}", None

    named: list[dict] = []
    for c in dec["刪除段落"]:
        if c.get("狀態") != "還原":
            k = f"刪除段落:{c.get('建議id') or c['id']}"
            named.append({"start": c["start"], "end": c["end"], "名稱": review.item_name(index, k), "鍵": k})
    for m in assemble.local_mutes(dec):
        k = f"局部消音:{m['id']}"
        named.append({"start": m["start"], "end": m["end"], "名稱": review.item_name(index, k), "鍵": k})
    for m in plan.get("消音", []):
        k = f"名字:{m['候選']}"
        named.append({"start": m["start"], "end": m["end"], "名稱": f"{review.item_name(index, k)}（直接消音）", "鍵": k})
    for g in plan["生成"]:
        nm, k = gen_name(g)
        named.append({"start": g["slot"][0], "end": g["slot"][1], "名稱": nm, "鍵": k})
    for it in items:
        k = f"重疊:{it['重疊']}" if it.get("重疊") else f"學員段落:{it['段落']}"
        nm = f"{review.item_name(index, k)} 的學員那一句" if it.get("重疊") else review.item_name(index, k)
        named.append({"start": it["slot"][0], "end": it["slot"][1], "名稱": nm, "鍵": k})
    return {"index": index, "items": items, "plan": plan, "named": named, "gen_name": gen_name}


def outside_gaps(turns: list[dict], by_id: dict, named: list[dict], kept: set) -> list[dict]:
    """學員段落的句子落在段落外面、又沒被任何處理蓋到的那幾秒（純函式，至少 OUTSIDE_MIN_S 秒）。
    每一筆 {段落, a, b（句子在外面的那一段）, start, end（沒蓋到的那幾秒）, 蓋到[], 鍵（段落外:<段落>:<起點>）}。"""
    from bookclub import assemble

    out = []
    stu = [t for t in turns if t.get("說話者") not in (None, "老師")]
    stu_ids = {i for t in stu for i in t.get("句子", [])}
    stu_ranges = [(t["start"], t["end"]) for t in stu]
    for t in turns:
        if t.get("說話者") in (None, "老師") or t["說話者"] in kept:
            continue
        # 10-02 第三批 14：切短時整句還給老師段落的（`切到外面`，見 review.retime_turns）：整句都算切在外面；
        # 那一句後來又歸到哪個學員段落、或被學員段落的範圍包住的部分不算
        for sid in t.get("切到外面") or []:
            x = by_id.get(sid)
            if not x or sid in stu_ids:
                continue
            for a, b in assemble.subtract(x["start"], x["end"], stu_ranges):
                if b - a < OUTSIDE_MIN_S:
                    continue
                left, hit = _gap_rows(a, b, named, OUTSIDE_MIN_S)
                for s, e in left:
                    out.append({"段落": t, "a": a, "b": b, "start": s, "end": e, "蓋到": hit, "整句": True,
                                "鍵": f"段落外:{t['id']}:{s:.1f}"})
        for sid in t.get("句子", []):
            x = by_id.get(sid)
            if not x:
                continue
            for a, b in ((x["start"], min(x["end"], t["start"])), (max(x["start"], t["end"]), x["end"])):
                if b - a < OUTSIDE_MIN_S:
                    continue
                left, hit = _gap_rows(a, b, named, OUTSIDE_MIN_S)
                for s, e in left:
                    out.append({"段落": t, "a": a, "b": b, "start": s, "end": e, "蓋到": hit, "鍵": f"段落外:{t['id']}:{s:.1f}"})
    return out


# 10-01 第三批 14：「切在外面的這幾秒是誰的聲音」的答案（存在覆核決定的 `段落外答案`，鍵跟總檢查那一列一樣）
OUT_A, OUT_B, OUT_C, OUT_D = "老師不用處理", "老師重念", "還是學員", "好幾個人"
OUT_ANSWERS = (OUT_A, OUT_B, OUT_C, OUT_D)
OUT_TODO = {OUT_B: "第 3 步答了「老師的話，要用老師聲音重念」，但這幾秒還沒有老師重念的那一筆：到第 3 步按「新增老師重念這幾秒」，起訖已經填好。",
            OUT_C: "第 3 步答了「還是學員的聲音」：那就不該切在這裡，把段落的起訖改回去包住這幾秒，或另外新增一筆漏抓的發言。",
            OUT_D: "第 3 步答了「還有好幾個人的聲音」：要再切開，一個人一段新增（剩下沒處理的幾秒會再問一次）。"}
OUT_GO = {OUT_B: "到第 3 步〈{name}〉，在「切在段落外面的這幾秒」按「新增老師重念這幾秒」",   # 答過之後「怎麼改」那一行
          OUT_C: "到第 3 步〈{name}〉，在「切在段落外面的這幾秒」按「把這一段的起訖改回去」或「另外新增一筆漏抓的發言」",
          OUT_D: "到第 3 步〈{name}〉，在「切在段落外面的這幾秒」按「切出其中一段」，一個人一段新增"}
OUT_TOL_S = 0.05


def outside_answer(answers: dict, heard: set, key: str, start: float, end: float) -> str | None:
    """這幾秒現在的答案（純函式）：存的範圍要跟現在的一樣（前後差 0.05 秒以內），範圍變了舊答案就不算；
    總檢查按過「我聽過了」（只記得起點）等於答「老師的話，不用處理」。"""
    a = answers.get(key)
    if a and abs(a["start"] - start) <= OUT_TOL_S and abs(a["end"] - end) <= OUT_TOL_S and a.get("答案") in OUT_ANSWERS:
        return a["答案"]
    if key in heard and not a:
        return OUT_A
    return None


def outside_questions(workdir: str | Path) -> dict[str, list[dict]]:
    """第 3 步學員段落卡片上要問的「切在外面的這幾秒是誰的聲音」：{段落 id: [{鍵, start, end, a, b, 答案}]}。只讀。"""
    from bookclub import review
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    dec = review.load_decisions(workdir)
    tdata = turns_mod.page_data(workdir)
    turns = tdata.get("段落", []) if not tdata.get("尚未準備") else []
    sents = (wd.read_json(wd.speakers_path(workdir), default={}) or {}).get("sentences", [])
    kept = {k for k, v in dec["學員聲音"].items() if v == "保留原聲"}
    heard = set((dec.get("總檢查") or {}).get("聽過") or [])
    answers = dec.get("段落外答案") or {}
    named = coverage(workdir, dec, turns)["named"]
    out: dict[str, list[dict]] = {}
    for g in outside_gaps(turns, {x["id"]: x for x in sents}, named, kept):
        out.setdefault(g["段落"]["id"], []).append({
            "鍵": g["鍵"], "start": round(g["start"], 3), "end": round(g["end"], 3), "句子外面": [round(g["a"], 3), round(g["b"], 3)],
            "整句": bool(g.get("整句")),
            "答案": outside_answer(answers, heard, g["鍵"], g["start"], g["end"])})
    return out


def answer_outside(workdir: str | Path, key: str, start: float, end: float, answer: str | None) -> dict:
    """`POST /api/review/outside`：回答（或「先不回答」＝answer None）某一段切在外面的幾秒是誰的聲音。
    答「老師的話，不用處理」同時記成總檢查那一列的「我聽過了」；改成別的答案或先不回答，那一列的「我聽過了」拿掉。"""
    from bookclub import review

    if answer is not None and answer not in OUT_ANSWERS:
        raise ValueError(f"只能選：{'、'.join(OUT_ANSWERS)}")
    if not str(key).startswith("段落外:"):
        raise ValueError("不是切在段落外面的那幾秒")
    with review._lock:
        dec = review.load_decisions(Path(workdir))
        ans = dec.setdefault("段落外答案", {})
        fc = dec.setdefault("總檢查", {"聽過": [], "看過": False})
        fc.setdefault("聽過", [])
        if answer is None:
            ans.pop(key, None)
        else:
            ans[key] = {"段落": key.split(":")[1], "start": round(float(start), 3), "end": round(float(end), 3),
                        "答案": answer, "時間": _now()}
        if answer == OUT_A and key not in fc["聽過"]:
            fc["聽過"].append(key)
        elif answer != OUT_A and key in fc["聽過"]:
            fc["聽過"].remove(key)
        review._save_decisions(Path(workdir), dec)
    return {"ok": True, "答案": answer}


def final_check(workdir: str | Path) -> dict:
    """第 3 步全部通過之後、開始第 4 步之前的總檢查（只讀）。回傳：
    {一定要處理: [列], 請看一眼: [列], 可以開始: bool}；每一列 {key, start, end, 說明, 可以按聽過?, 已按聽過?,
    名稱（畫面上看得到的名稱）, 第3步（第 3 步那一張卡片的鍵，沒有卡片是 None）, 去改（去第 3 步要改什麼）,
    有學員聲音（聽了有學員聲音時可以走的路，見 `_fix_paths`）}。
    「一定要處理」有還沒按聽過的列就不能開始；「請看一眼」不擋（網頁上要按一次「我看過了」）。
    10-01：說明一律用畫面上看得到的名稱（不寫 T062、O5602.14 這類內部編號）；學員的話落在段落外面的，
    已經被別筆處理蓋到的那一部分不再列（只列沒蓋到的那幾秒，說明寫哪一筆蓋了多少）。"""
    import shutil

    from bookclub import assemble, nameplan, review, students
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    dec = review.load_decisions(workdir)
    heard = set((dec.get("總檢查") or {}).get("聽過") or [])
    answers = dec.get("段落外答案") or {}
    tdata = turns_mod.page_data(workdir)
    turns = tdata.get("段落", []) if not tdata.get("尚未準備") else []
    sents = (wd.read_json(wd.speakers_path(workdir), default={}) or {}).get("sentences", [])
    by_id = {x["id"]: x for x in sents}
    words = (wd.read_json(wd.merged_transcript_path(workdir), default={}) or {}).get("words") or []
    kept = {k for k, v in dec["學員聲音"].items() if v == "保留原聲"}
    cov = coverage(workdir, dec, turns)
    index, items, plan, named, gen_name = cov["index"], cov["items"], cov["plan"], cov["named"], cov["gen_name"]
    handled = [(x["start"], x["end"]) for x in named]
    must, look = [], []

    from bookclub.safeview import safe_id

    def show_id(key, card3):
        """10-04 #128：畫面上小字顯示的編號（跟 inspect 總檢查的鍵一樣）。鍵含自己打的文字或名字（inspect 會印成
        `<文字 12 字>`）就改顯示第 3 步卡片的鍵；兩個都不能印就不顯示。"""
        for k in (key, card3):
            if isinstance(k, str) and safe_id(k):
                return k
        return None

    def row(lst, key, a, b, text, ack=False, *, name="", card=None, todo="", paths=None, heard_now=None):
        card3 = (index.get(card) or {}).get("第3步") if card else None
        lst.append({"key": key, "start": round(a, 3), "end": round(b, 3), "說明": text, "名稱": name,
                    "第3步": card3, "顯示編號": show_id(key, card3),
                    "去改": todo, **({"有學員聲音": paths} if paths else {}),
                    **({"可以按聽過": True, "已按聽過": key in heard if heard_now is None else heard_now} if ack else {})})

    def near(a: float, b: float) -> list[dict]:
        return [x for x in named if x["start"] - 0.5 <= b and a <= x["end"] + 0.5]

    # 1. 學員的話落在段落外面（人改過段落的開頭或結尾，句子的一部分在外面、又沒被別的處理蓋到）
    #    10-01 1-4：只列沒被蓋到的那幾秒；鍵照沒被蓋到的那一段的起點（整段都沒蓋到時跟以前一樣，之前按過的「聽過」照算）
    #    10-01 第三批 14：第 3 步切的當下問過「外面這幾秒是誰的聲音」：答「老師的話，不用處理」（＝這裡的「我聽過了」）算確認過；
    #    其他答案照樣看有沒有處理（處理了就被蓋到、不會列），沒處理的說明寫上答了什麼、還差什麼
    #    10-02 第四批：確認過的那一列不再拿掉（已按聽過＝True，不算還要處理），網頁上顯示成灰色、可以取消或改答案
    gaps = outside_gaps(turns, by_id, named, kept)
    for g in gaps:
        t, a, b, s0, e = g["段落"], g["a"], g["b"], g["start"], g["end"]
        key = g["鍵"]
        ans = outside_answer(answers, heard, key, s0, e)
        tname = review.item_name(index, f"學員段落:{t['id']}")
        hit = g["蓋到"]
        done = "、".join(f"〈{h['名稱']}〉" for h in hit)
        part = (f"其中 {b - a - sum(y - x0 for x0, y in assemble.subtract(a, b, handled)):.1f} 秒已經由{done}處理，"
                f"剩下 {t1(s0)}–{t1(e)}（{e - s0:.1f} 秒）沒有處理，") if hit else ""
        said = OUT_TODO.get(ans, "")
        row(must, key, s0, e,
            (f"〈{tname}〉（{t1(t['start'])}–{t1(t['end'])}）切短的時候，原本屬於這一段的一句（{t1(a)}–{t1(b)}，{b - a:.1f} 秒）"
             f"整句落在段落外面；{part}" if g.get("整句") else
             f"〈{tname}〉（{t1(t['start'])}–{t1(t['end'])}）的句子有 {b - a:.1f} 秒在段落外面（{t1(a)}–{t1(b)}）；{part}")
            + ("已確認是老師的話，不用處理（照原聲留著）。" if ans == OUT_A else
               said or "這幾秒會是學員原聲。聽一下：真的有學員的聲音，按「有學員的聲音」選怎麼處理；"
                       "外面那一段不是學員（例如是老師接話），按「我聽過了」"), ack=True,
            name=tname, card=f"學員段落:{t['id']}",
            todo=("要改的話：取消勾「我聽過了」，或在「改答案」選別的" if ans == OUT_A else
                  OUT_GO[ans].format(name=tname) if ans in OUT_GO else
                  f"到第 3 步〈{tname}〉回答「切在外面的這幾秒是誰的聲音」，或按「改時間」把起訖改大包住 {t1(s0)}–{t1(e)}"),
            paths=_fix_paths(s0, e, near(s0, e), index, turn=t), heard_now=ans == OUT_A)
        # 10-02 第四批：答過「老師的話，不用處理」（或這裡勾了「我聽過了」）的不再拿掉，留著顯示成確認過、可以在這裡改答案
        must[-1].update({"段落外答案": ans, "段落外起訖": [round(s0, 3), round(e, 3)]})
    # 10-02 第七批（任務單 26）：要念的文字裡還有舊英文代號 → 一定要處理（送去生成的文字不該再有英文代號）
    from bookclub import codeswap

    try:
        left_codes = codeswap.leftover_texts(workdir)
    except Exception:  # noqa: BLE001 — 讀不到不擋（其他檢查照常）
        left_codes = []
    for x in left_codes:
        info = index.get(x["卡片"]) or {}
        nm = review.item_name(index, x["卡片"]) if info else x["卡片"].split(":")[0]
        row(must, f"英文代號:{x['卡片']}:{x['欄位']}", float(info.get("start") or 0.0), float(info.get("end") or 0.0),
            f"〈{nm}〉要念的文字裡還有英文代號（{'、'.join(x['代號'])}），送去生成會念英文",
            name=nm, card=x["卡片"] if info else None,
            todo="到第 3 步「開始前 4 件事」② 上面按「把這一集的英文代號換成中文」，"
                 "或命令列 bookclub codes convert <工作區>；只有這一句的話也可以在卡片上直接改字")
    # 2. 名字換不了代號
    for m in plan.get("要人處理", []):
        k = f"名字:{m['候選']}"
        info = index.get(k, {})
        nm = review.item_name(index, k)
        row(must, k, info.get("start", 0.0), info.get("end", 0.0), f"〈{nm}〉：{m['原因']}", name=nm, card=k,
            todo=f"到第 3 步〈{nm}〉把「老師 AI 聲音要重念的句子」改好（名字寫成代號），或在「改做法」選直接消音")
    # 3. 要念的字數跟那段時間逐字稿的字數差太多
    for g in plan["生成"]:
        src = nameplan.range_words(words, *g["slot"])
        if nameplan.too_short(g["text"], src):
            nm, k = gen_name(g)
            row(must, f"字太少:{g['id']}", g["slot"][0], g["slot"][1],
                f"〈{nm}〉：要念 {nameplan.say_count(g['text'])} 個字，這段時間逐字稿有 {nameplan.say_count(src)} 個字，"
                "其他的話會不見", name=nm, card=k, todo=f"到第 3 步〈{nm}〉把重念範圍改小，或把要念的話補齊")
    # 4. 重疊選了要生成、但缺文字或缺學員是誰
    for o in review.overlap_choices(workdir):
        why = review.overlap_gen_problem(o, [tuple(it["slot"]) for it in items if not it.get("重疊")])
        if why:
            k = f"重疊:{o['id']}"
            nm = review.item_name(index, k)
            guess = "" if o.get("學員已選") or not o.get("學員") or o.get("學員") == "老師" else f"（程式猜是 {o['學員']}，按「就是這一位」就好）"
            todo = f"到第 3 步〈{nm}〉的「改做法」"
            todo += f"，在「學員說的」旁邊選學員是誰{guess}" if "學員是誰" in why else "，把空的那一欄填好"
            row(must, k, o["start"], o["end"], f"〈{nm}〉（{review.OVERLAP_LABEL.get(o['做法'], o['做法'])}）：{why}",
                name=nm, card=k, todo=todo)
    # 5. 聲紋判成「不是老師」的句子，整句都不在任何處理的範圍、也不在學員段落裡（可能是漏抓的學員發言）
    #    段落的聲音判斷也不是老師的才放「一定要處理」；段落判成老師的（第一堂 92 句，多半是誤判）在「請看一眼」彙總一列
    #    10-01：句子只有一部分被處理蓋到的，沒蓋到的部分（至少 0.3 秒）照樣列（以前整句跳過，例如重疊只蓋到 0.7 秒、
    #    句子其他 2.8 秒的學員原聲沒人處理）；已經列在「段落外」的那幾秒不重複列
    stu_turns = [t for t in turns if t.get("說話者") not in (None, "老師")]
    # 10-02：「段落外」那幾秒不管答了沒有都不在這裡重複列（答「老師的話，不用處理」的上面已經列成確認過，這裡不能再冒出來）
    blockers = handled + [(t["start"], t["end"]) for t in stu_turns] + [(g["start"], g["end"]) for g in gaps]
    soft = []
    for x in sents:
        if x.get("label") != "不是老師" or x["end"] - x["start"] < OUTSIDE_MIN_S:
            continue
        left = [(a, b) for a, b in assemble.subtract(x["start"], x["end"], blockers) if b - a >= OUTSIDE_MIN_S]
        if not left:
            continue
        mid = (x["start"] + x["end"]) / 2
        home = next((t for t in turns if t["start"] <= mid <= t["end"]), None)
        if home and home.get("聲音判斷") == "老師":
            soft.append(x)
            continue
        hit = [h for h in named if h["start"] < x["end"] and x["start"] < h["end"]]
        for a, b in left:
            gap = lambda t: max(0.0, t["start"] - b, a - t["end"])  # noqa: E731
            close = min(stu_turns, key=gap, default=None)
            close = close if close is not None and gap(close) <= 10 else None
            whole = abs(a - x["start"]) < 1e-6 and abs(b - x["end"]) < 1e-6
            part = "" if whole else (f"這一句 {t1(x['start'])}–{t1(x['end'])} 有一部分已經由"
                                     + ("、".join(f"〈{h['名稱']}〉" for h in hit) or "學員段落") + "處理，這裡只列沒處理的這幾秒；")
            row(must, f"聲紋:{x['id']}" if whole else f"聲紋:{x['id']}:{a:.1f}", a, b,
                f"聲音特徵判斷不是老師、{b - a:.1f} 秒，不在任何學員段落或處理範圍裡：可能是漏抓的學員發言。{part}"
                "聽一下：沒有學員的聲音就按「我聽過了」；有的話按「有學員的聲音」選怎麼處理", ack=True,
                name=f"{t1(a)} 這一句",
                todo="第 3 步沒有這一句的卡片：有學員的聲音時，用下面「有學員的聲音」帶著這段時間去第 3 步新增或延長",
                paths=_fix_paths(a, b, near(a, b), index, turn=close))
    # 10-04 #117：有人聲但逐字稿沒有字（Groq 漏轉）→ 找名字找不到，名字可能留在成品裡。見 bookclub/untranscribed.py
    nz_look = _untranscribed_rows(workdir, dec, heard, stu_turns, kept, index, row, must)

    # 請看一眼
    for c in dec["刪除段落"]:
        if c.get("狀態") != "還原":
            k = f"刪除段落:{c.get('建議id') or c['id']}"
            row(look, f"剪掉:{c['id']}", c["start"], c["end"], f"剪掉 {c['end'] - c['start']:.1f} 秒（聲音和畫面都拿掉，影片會變短，畫面會跳一下）",
                name=review.item_name(index, k), card=k)
    for m in assemble.local_mutes(dec):
        k = f"局部消音:{m['id']}"
        row(look, f"消音:{m['id']}", m["start"], m["end"], f"消音 {m['end'] - m['start']:.1f} 秒（只拿掉聲音，畫面留著）",
            name=review.item_name(index, k), card=k)
    for m in plan.get("消音", []):
        k = f"名字:{m['候選']}"
        row(look, f"名字消音:{m['候選']}", m["start"], m["end"], f"〈{review.item_name(index, k)}〉直接消音 {m['end'] - m['start']:.1f} 秒",
            name=review.item_name(index, k), card=k)
    for g in plan["生成"]:
        if g["slot"][1] - g["slot"][0] > LONG_TEACHER_S:
            nm, k = gen_name(g)
            row(look, f"長句:{g['id']}", g["slot"][0], g["slot"][1],
                f"〈{nm}〉老師重念 {g['slot'][1] - g['slot'][0]:.1f} 秒（超過 {LONG_TEACHER_S:.0f} 秒）", name=nm, card=k)
    # 10-02 第六批：老師重念範圍開頭或結尾有一段沒有人講話 → 提醒＋建議範圍（不自動改，見 bookclub/silentedge.py）
    try:
        from bookclub import silentedge

        edge = silentedge.hints(workdir, plan)
    except Exception:  # noqa: BLE001 — 算不出來不擋總檢查
        edge = {}
    gens = {g.get("id"): g for g in plan["生成"]}
    for h in edge.values():
        g = gens.get(h["生成編號"])
        if not g:
            continue
        nm, k = gen_name(g)
        a, b = h["範圍"]
        row(look, f"前後沒聲音:{h['生成編號']}", a, b,
            f"〈{nm}〉老師重念 {t1(a)}–{t1(b)}：{h['說明']}。"
            + ("按「照建議縮小」會把重念範圍改成建議的範圍（這一句要重新生成）" if h["可以縮"] else h["原因"]),
            name=nm, card=k)
        look[-1]["縮小"] = h
    # 10-02 第六批第五件：全片底噪還沒在第 2 步確認 → 提醒（不擋；沒確認照樣用自動挑的那一段，它本來就夠安靜）
    try:
        from bookclub import roomtone

        rt = roomtone.load_info(workdir)
    except Exception:  # noqa: BLE001
        rt = None
    if not rt or not rt.get("已確認"):
        a, b = (rt.get("start"), rt.get("end")) if rt and rt.get("start") is not None else (0.0, 0.0)
        row(look, "底噪:確認", a, b,
            ("全片底噪還沒挑（打開第 2 步會自動挑）" if not rt else
             "這支影片找不到夠安靜的片段當全片底噪，附近找不到時會墊全靜音" if rt.get("start") is None else
             f"全片底噪（{t1(a)}–{t1(b)}）還沒確認")
            + "：到第 2 步「全片底噪」播放、聆聽，如果聽到任何聲音，它就不能當作底噪。沒確認的話照樣用自動挑的那一段",
            todo="到第 2 步（挑老師參考音那一頁）最下面的「全片底噪」")
    if soft:
        row(look, "聲紋:段落是老師", soft[0]["start"], soft[-1]["end"],
            f"另外 {len(soft)} 句聲音特徵判斷不是老師、但整段的聲音判斷是老師（多半是誤判，例如冥想引導、老師壓低聲音）。"
            "時間：" + "、".join(wd.fmt_time(x["start"]) for x in soft[:40]) + ("⋯" if len(soft) > 40 else ""))
    # 10-04 #111：學員段落裡、兩邊都沒有老師的重疊，第 3 步不出卡、自動算處理好 → 這裡列筆數與每一處的時間，可以點過去聽
    #    （其實是老師插話、沒被認出是老師時，會跟著學員那一段整段重念被蓋掉；聽到老師的聲音到第 3 步「設定」救回）
    auto_ov = review.student_turn_overlaps(workdir, dec, turns) if turns else []
    if auto_ov:
        row(look, STUDENT_TURN_KEY, auto_ov[0]["start"], auto_ov[-1]["end"],
            f"{len(auto_ov)} 處重疊落在會整段重念的學員段落裡、兩邊都沒有老師，第 3 步沒有出卡、自動算處理好"
            "（跟著那一段整段重念）。下面每一處可以點過去聽：如果聽到其實是老師插話，那一小段會被蓋掉，要救回。"
            "時間：" + "、".join(wd.fmt_time(x["start"]) for x in auto_ov[:40]) + ("⋯" if len(auto_ov) > 40 else ""),
            name="學員段落裡的重疊",
            todo="聽到老師的聲音：到第 3 步「設定」的「已自動跳過的重疊」，按那一處的「救回」，再選怎麼處理")
        look[-1]["時間點"] = [{"id": x["id"], "start": round(x["start"], 3), "end": round(x["end"], 3),
                            "名稱": review.item_name(index, f"學員段落:{x['學員段落']}") if x.get("學員段落") else ""}
                           for x in auto_ov]
    if nz_look:   # 10-04 #117：沒有字、但比較短，或落在會整段重念的學員段落裡 → 合成一列，每一處可以點過去聽
        short = [x for x in nz_look if x["類別"] == "較短"]
        inturn = [x for x in nz_look if x["類別"] == "學員段落"]
        text = []
        if short:
            text.append(f"{len(short)} 處有人講話但逐字稿沒有字（人聲不到 {untranscribed.MUST_VOICE_S:.0f} 秒，"
                        "或只有一部分在學員段落外面），工具找不到這裡的名字；有空的話聽一下，有提到名字就到第 3 步手動補名字卡。")
        if inturn:
            text.append(f"{len(inturn)} 處在會整段重念的學員段落裡：重念時會少念這幾秒的內容；隱私不受影響。")
        row(look, UNTRANSCRIBED_LOOK_KEY, nz_look[0]["start"], nz_look[-1]["end"],
            "".join(text) + "時間：" + "、".join(wd.fmt_time(x["start"]) for x in nz_look[:40]) + ("⋯" if len(nz_look) > 40 else ""),
            name="有人聲但沒有字", todo="有提到名字：到第 3 步手動新增名字卡（起訖填這一處的時間）")
        look[-1]["時間點"] = [{"id": f"沒有字:{x['start']:.1f}", "start": round(x["start"], 3), "end": round(x["end"], 3),
                            "類型": x["類別"], "秒": x["秒"],
                            "名稱": (f"{x['名稱']}裡，" if x.get("名稱") else "") + f"約 {x['秒']:.1f} 秒沒有字"}
                           for x in nz_look]
    # 10-04 #127：學員段落裡聲紋多半判成老師 → 可能是老師的話被標成學員（會被當成學員重念）；已確認的段落也照列
    from bookclub import turnvoice

    for t in stu_turns:
        v = turnvoice.turn_voice(t, sents, by_id)
        if not v or not v["多半是老師"]:
            continue
        k = f"學員段落:{t['id']}"
        nm = review.item_name(index, k)
        row(look, f"{TEACHER_TURN_KEY}:{t['id']}", t["start"], t["end"],
            f"〈{nm}〉的聲音特徵有不少像老師（{v['句數']} 句裡 {v['老師句數']} 句、約 {v['老師秒']:.0f} 秒，"
            f"占 {v['比例'] * 100:.0f}%）" + ("；這一段已經確認過" if t.get("已確認") else "")
            + "。請聽一下：是老師在講話，到第 3 步按「這段其實是老師」，否則老師的話會被當成學員、用替代聲音重念",
            name=nm, card=k, todo=f"到第 3 步〈{nm}〉按「這段其實是老師」（只有一部分是老師：先用「改做法」→「從游標處切開」）；聽過確定是學員就不用改")
        look[-1]["聲紋老師"] = v
    covered = review.covered_overlaps(workdir, dec, turns) if turns else []
    gen_s = sum(g["slot"][1] - g["slot"][0] for g in plan["生成"]) + sum(it["slot_s"] for it in items)
    free = shutil.disk_usage(str(workdir)).free / 1e9
    summary = {"自動算處理好": [{"id": x["id"], "start": x["start"], "end": x["end"], "涵蓋": x["涵蓋"]["名稱"],
                             "名稱": review.item_name(index, f"重疊:{x['id']}")} for x in covered],
               "學員段落裡自動處理": [{"id": x["id"], "start": x["start"], "end": x["end"]} for x in auto_ov],   # 10-04 #111
               "要生成秒數": round(gen_s), "預估秒數": round(gen_s * GEN_SPEED + ASSEMBLE_S), "硬碟可用GB": round(free, 1),
               "記憶體": memory_status(),   # 10-02 第五批：開始前就提醒記憶體偏滿
               "提醒": "執行期間關掉其他程式（Zoom、瀏覽器分頁）；接上電源、筆電不要闔上（螢幕可以關）"}
    # 10-03 第八批 #66：要念的文字裡有英文詞 → 請看一眼（不擋）。AI 念英文容易念錯（happy 念成 heavy）
    try:
        en_texts = codeswap.english_texts(workdir)
    except Exception:  # noqa: BLE001 — 讀不到不擋
        en_texts = []
    for x in en_texts:
        info = index.get(x["卡片"]) or {}
        if not info:
            continue
        nm = review.item_name(index, x["卡片"])
        row(look, f"英文:{x['卡片']}", float(info.get("start") or 0.0), float(info.get("end") or 0.0),
            f"〈{nm}〉要念的文字裡有英文（{x['幾個']} 個詞）。AI 念英文容易念錯；不是專有名詞的話，建議改成中文再生成",
            name=nm, card=x["卡片"], todo="到第 3 步這張卡片把英文改成中文；是專有名詞、要照念的就不用改")
    # 10-02 第六批：已經組裝過的話，每一列附上成品時間（還沒有成品時只列原片）
    try:
        from bookclub import timemap

        tm = timemap.load(workdir)
    except Exception:  # noqa: BLE001
        tm = None
    if tm:
        for r in must + look:
            oa, ob = timemap.to_output(r["start"], tm), timemap.to_output(r["end"], tm)
            if oa is not None or ob is not None:
                r["成品起訖"] = [round(oa if oa is not None else ob, 3), round(ob if ob is not None else oa, 3)]
    left = [r for r in must if not r.get("已按聽過")]
    seen, new_keys = seen_state(dec.get("總檢查") or {}, look, dec)
    for r in look:
        if r["key"] in new_keys:
            r["新的"] = True   # 10-02 第五批：按「我看過了」之後才多出來、或時間範圍變了的那幾列
    return {"一定要處理": sorted(must, key=lambda r: r["start"]), "請看一眼": sorted(look, key=lambda r: r["start"]),
            "摘要": summary, "可以開始": not left, "還要處理": len(left), "已確認": len(must) - len(left),
            "看過": seen, "看過後新增": len(new_keys)}


LOOK_RANGE_TOL_S = 0.05   # 「請看一眼」某一列的起訖跟按「我看過了」時差超過這麼多秒，算這一列變了


def look_snapshot(rows: list[dict]) -> list[dict]:
    """按「我看過了」時記下的「請看一眼」清單：每一列的鍵與起訖（純函式）。"""
    return [{"key": r["key"], "start": round(float(r["start"]), 3), "end": round(float(r["end"]), 3)} for r in rows]


def seen_state(fc: dict, look: list[dict], dec: dict | None = None) -> tuple[bool, list[str]]:
    """10-02 第五批：「我看過了」現在還算不算（純函式）。回傳（算不算, 新的列的鍵）。
    - 按的時候有記清單（`看過的列`）：現在多了列、或某一列的起訖變了 → 不算，那幾列是新的；只是少了列照算
    - 舊資料（10-02 以前只記了 `看過`、`看過時間`）：剪掉、消音在按「我看過了」之後才新增或改過（建立時間／更新時間比較晚）的
      算新的；名字消音、超過 10 秒的老師重念沒有時間可比，當作看過了（不讓人莫名被擋）"""
    if not fc.get("看過"):
        return False, []
    snap = fc.get("看過的列")
    if isinstance(snap, list):
        old = {x.get("key"): x for x in snap if isinstance(x, dict)}
        new = [r["key"] for r in look if r["key"] not in old
               or abs(float(old[r["key"]].get("start", 0)) - r["start"]) > LOOK_RANGE_TOL_S
               or abs(float(old[r["key"]].get("end", 0)) - r["end"]) > LOOK_RANGE_TOL_S]
        return not new, new
    at = fc.get("看過時間") or ""
    if not at:
        return True, []
    stamps: dict[str, str] = {}
    for kind, prefix in (("刪除段落", "剪掉"), ("局部消音", "消音")):
        for x in (dec or {}).get(kind) or []:
            stamps[f"{prefix}:{x.get('id')}"] = max(str(x.get("建立時間") or ""), str(x.get("更新時間") or ""))
    new = [r["key"] for r in look if stamps.get(r["key"], "") > at]
    return not new, new


def ack_final(workdir: str | Path, key: str | None = None, heard: bool = True, seen: bool | None = None,
              rows: list[dict] | None = None) -> dict:
    """`POST /api/execute/finalcheck`：「一定要處理」裡可以按的那一列按「我聽過了」（key），或整頁「我看過了」（seen）。
    10-02 第五批：按「我看過了」時記下當時「請看一眼」的清單（rows＝網頁上顯示的那幾列；沒給就照現在算的），
    之後清單多了列或時間範圍變了，「我看過了」就回到沒勾（見 seen_state）。"""
    from bookclub import review

    workdir = Path(workdir)
    snap = None
    if seen:
        try:
            snap = look_snapshot(rows) if rows is not None else look_snapshot(final_check(workdir)["請看一眼"])
        except (KeyError, TypeError, ValueError):
            snap = look_snapshot(final_check(workdir)["請看一眼"])
    rng = None
    if key and str(key).startswith("段落外:") and heard:   # 那一列現在的範圍（記答案用）
        g = next((x for qs in outside_questions(workdir).values() for x in qs if x["鍵"] == key), None)
        rng = [g["start"], g["end"]] if g else None
    with review._lock:
        dec = review.load_decisions(workdir)
        fc = dec.setdefault("總檢查", {"聽過": [], "看過": False})
        fc.setdefault("聽過", [])
        if key:
            if heard and key not in fc["聽過"]:
                fc["聽過"].append(key)
            elif not heard and key in fc["聽過"]:
                fc["聽過"].remove(key)
            if str(key).startswith("段落外:"):   # 10-01 第三批 14：「我聽過了」＝第 3 步答「老師的話，不用處理」，兩邊同一件事
                ans = dec.setdefault("段落外答案", {})
                if heard and rng:
                    ans[key] = {"段落": key.split(":")[1], "start": rng[0], "end": rng[1], "答案": OUT_A, "時間": _now()}
                elif not heard and (ans.get(key) or {}).get("答案") == OUT_A:
                    ans.pop(key, None)
        if seen is not None:
            fc["看過"] = bool(seen)
            fc["看過時間"] = _now()
            if seen:
                fc["看過的列"] = snap
            else:
                fc.pop("看過的列", None)
        review._save_decisions(workdir, dec)
    return {"ok": True, "總檢查": fc}


# ---------- 每一步做過沒有（不載入模型） ----------

def names_done(workdir: Path) -> tuple[bool, str]:
    from bookclub import nameplan, tts

    if not wd.read_json(wd.names_path(workdir), default=None) and not (workdir / nameplan.IMPORTED_PATH).exists():
        return True, "沒有名字候選，不用做"
    plan = nameplan.compute_plan(workdir) if wd.read_json(wd.names_path(workdir), default=None) else \
        (wd.read_json(nameplan.plan_path(workdir), default=None) or {})
    gen = plan.get("生成", [])
    if not gen:
        return True, "沒有要生成的名字句子（都是消音或略過）"
    tlog = wd.read_json(tts.teacher_log_path(workdir), default=None) or {}
    recs = {r["id"]: r for r in tlog.get("句子", [])}
    # 09-29 檢查 #7：跟生成程式用同一個判斷（文字、發音對照表、時間格、參考音）
    if tts.teacher_ref_changed(workdir, tlog):
        return False, f"老師參考音換過了，{len(gen)} 句都要重新生成"
    table = tts.load_pron_table(workdir / tts.PRON_TABLE_NAME if (workdir / tts.PRON_TABLE_NAME).is_file() else None)
    left = [g["id"] for g in gen if not (recs.get(g["id"]) or {}).get("放回時間格")
            or tts.record_stale(recs[g["id"]], {**g, "生成用文字": tts.apply_pron(g["text"], table)[0]})
            or tts.output_missing(recs[g["id"]], workdir)]   # 10-03 第九批 #19：聲音檔不在當作沒做過
    return (not left), (f"{len(gen)} 句都生成好了" if not left else f"還有 {len(left)}／{len(gen)} 句要生成")


def students_done(workdir: Path, a: float | None, b: float | None) -> tuple[bool, str]:
    from bookclub import students

    items, _ = students.build_items(workdir, a, b)
    if not items:
        return True, "範圍內沒有學員段落"
    from bookclub import tts

    log = wd.read_json(students.log_path(workdir), default=None) or {}
    recs = {r["id"]: r for r in log.get("句子", [])}
    table = tts.load_pron_table()
    # 09-29 檢查 #7：跟生成程式用同一個判斷（文字、發音對照表、時間格、參考音內容）
    # 10-01：比「現在配到的聲線」，不是紀錄自己記的路徑（以前等於自己比自己，換了聲線也看不出來）
    now = students.current_refs(workdir, items) if recs else {}
    left = [it["id"] for it in items if not (recs.get(it["id"]) or {}).get("放回時間格")
            or tts.record_stale(recs[it["id"]], {**it, "生成用文字": tts.apply_pron(it["text"], table)[0]},
                                now.get(it["學員"]) or wd.localize(recs[it["id"]].get("參考音"), workdir))   # 10-02 第五批
            or tts.output_missing(recs[it["id"]], workdir)]   # 10-03 第九批 #19：聲音檔不在當作沒做過
    return (not left), (f"{len(items)} 段都生成好了" if not left else f"還有 {len(left)}／{len(items)} 段要生成")


def stunames_done(workdir: Path) -> tuple[bool, str]:
    from bookclub import studentgen, studentnames

    sp = studentnames.plan(workdir)
    if not sp["生成"]:
        return True, f"沒有選換成代號的（直接消音 {len(sp['消音'])} 筆，組裝時處理）"
    rec = wd.read_json(studentgen.log_path(workdir), default=None) or {}
    recs = {r["id"]: r for r in rec.get("句子", [])}
    back = rec.get("退回直接消音") or {}
    from bookclub import tts

    table = tts.load_pron_table()
    left = [g["id"] for g in sp["生成"] if g["id"] not in back
            and (not (recs.get(g["id"]) or {}).get("放回時間格")
                 or tts.record_stale(recs[g["id"]], {**g, "生成用文字": tts.apply_pron(g["text"], table)[0]},
                                     studentgen.current_ref(workdir, g["學員"], recs[g["id"]]))
                 or tts.output_missing(recs[g["id"]], workdir))]   # 10-03 第九批 #19：聲音檔不在當作沒做過
    return (not left), (f"{len(sp['生成'])} 句都處理好了" if not left else f"還有 {len(left)}／{len(sp['生成'])} 句要生成")


def render_inputs(workdir: Path) -> list[Path]:
    from bookclub import nameplan, review, studentgen, studentnames, students, tts

    return [p for p in (review.review_path(workdir), nameplan.plan_path(workdir), tts.teacher_log_path(workdir),
                        students.log_path(workdir), workdir / "校對" / "段落.json", workdir / "名字覆核決定.json",
                        studentnames.decisions_path(workdir), studentgen.log_path(workdir),
                        workdir / "參考音" / "底噪.json")   # 10-02 第六批：全片底噪換一段 → 要重新組裝
            if p.exists()]


def render_done(workdir: Path, tag: str, methods: list[str]) -> tuple[bool, str]:
    outs = [workdir / "輸出" / f"成品_{tag}_{m}.mp4" for m in methods]
    if not all(p.exists() for p in outs):
        return False, "還沒組裝"
    newest_in = max((p.stat().st_mtime for p in render_inputs(workdir)), default=0.0)
    if min(p.stat().st_mtime for p in outs) < newest_in:
        return False, "覆核或生成結果比成品新，要重新組裝"
    summ = wd.read_json(workdir / "輸出" / f"輸出摘要_{tag}.json", default=None) or {}
    bad = [m for m in methods if not ((summ.get("輸出") or {}).get(m, {}).get("驗證") or {}).get("通過")]
    if bad:
        return False, f"成品沒有通過驗證（{'、'.join(bad)}），要重新組裝"
    from bookclub import roomtone

    if summ.get("底噪挑法") != roomtone.METHOD:   # 10-02 第六批第五件：墊底噪的挑法改了，聲音會不一樣（生成好的聲音不用重做）
        return False, "墊底噪的挑法改了，要重新組裝（生成好的聲音沿用）"
    return True, "成品比覆核、生成結果都新"


# ---------- 串起來 ----------

def _default_runners() -> dict[str, Callable]:
    def names(workdir: Path, ctx: dict) -> None:
        from bookclub import nameplan, tts

        plan = nameplan.make_plan(workdir)
        if plan["生成"]:
            tts.generate_teacher(workdir, nameplan.sentences_path(workdir))

    def stu(workdir: Path, ctx: dict) -> None:
        from bookclub import students

        students.generate_students(workdir, start=ctx["範圍"][0], end=ctx["範圍"][1])

    def stunames(workdir: Path, ctx: dict) -> None:
        from bookclub import studentgen

        studentgen.generate(workdir)

    def render(workdir: Path, ctx: dict) -> None:
        from bookclub.render import render_video

        render_video(workdir, ctx["範圍"][0], ctx["範圍"][1], methods=ctx["輸出做法"], tag=ctx["標記"])

    return {"老師名字": names, "學員重念": stu, "保留原聲學員名字": stunames, "組裝": render}


def _default_checks() -> dict[str, Callable]:
    return {"老師名字": lambda w, c: names_done(w),
            "學員重念": lambda w, c: students_done(w, *c["範圍"]),
            "保留原聲學員名字": lambda w, c: stunames_done(w),
            "組裝": lambda w, c: render_done(w, c["標記"], c["輸出做法"])}


# ---------- 10-01：每一步、每一個聲線各自一支程式跑 ----------
#
# 10-01 夜間第一堂試跑：四步在同一支程式裡跑，換步驟、換學員時前一個模型的記憶體沒有還給系統
# （`gc.collect()` 不保證），8GB 的 Mac swap 衝到 11 GB、程式沒留訊息就結束。改成每一段各自一支程式，
# 做完那支程式就結束，記憶體一定還給系統；任何時候記憶體裡最多一個大模型：
#   老師名字：生成 → 插入停頓 → 收尾（各一支）
#   學員重念：每個聲線一支「生成」→ 全部聲線一支「插入停頓」（對位模型只載入一次）→ 每個聲線一支「收尾」
#   保留原聲學員名字：同上，以學員分（每位用自己的聲音）
#   組裝：一支
# 「收尾」＝長度還是不過的才改語速重生成（要的話才載入生成模型）＋放回時間格、寫紀錄。

PART_STOPPED = 3   # 子程式因為按了停止（或硬碟、記憶體不夠被請停）而結束的結束碼
PEAK_PREFIX = "[記憶體] 這支程式最高用到"
_PEAK_RE = re.compile(r"\[記憶體\] 這支程式最高用到 ([\d.]+) GB")
PART_LOG = "子程式紀錄.jsonl"   # 生成/ 底下，每一支子程式一行：記憶體高峰、swap 最高、硬碟最低（晚上實測用）
STOP_MSG = "按了停止：停在目前這一句做完之後，下次按「開始執行」會接著做（做好的不重做）"
GB = 1024 ** 3


class PartFailed(RuntimeError):
    """子程式出錯（訊息帶最後幾行），或被系統結束、重試一次還是一樣。"""


def part_log_path(workdir: Path) -> Path:
    return Path(workdir) / "生成" / PART_LOG


def parse_swapusage(text: str) -> float | None:
    """`sysctl -n vm.swapusage` 的輸出（例如 `total = 10240.00M  used = 6144.25M  free = ...`）→ used 幾 GB。"""
    m = re.search(r"used\s*=\s*([\d.]+)\s*([KMG])", text or "")
    if not m:
        return None
    return float(m.group(1)) / {"K": 1024 ** 2, "M": 1024, "G": 1}[m.group(2)]


# 10-03 第九批（#28）：Linux（夥伴的 WSL2）以前讀不到 swap，跑的過程中的記憶體門檻完全沒作用。
# 改成 Linux 讀 `/proc/meminfo`：swap 用量照舊比 swapGB 門檻；另外看「可用記憶體」——WSL2 預設 swap 只有
# 記憶體的四分之一（32GB 的電腦約 8GB），swap 門檻 8.5 GB 幾乎碰不到，記憶體真的不夠時是可用記憶體先見底。
MIN_AVAILABLE_GB = 1.5   # Linux 可用記憶體低於這個數字就自動停（Mac 讀不到這個數字，不受影響）
MEMINFO_PATH = "/proc/meminfo"


def parse_meminfo(text: str) -> dict:
    """`/proc/meminfo` 的內容 → {可用GB, swapGB}（單位 kB 換成 GB）；讀不到的欄位是 None（純函式）。
    可用＝MemAvailable；swap 用量＝SwapTotal − SwapFree。"""
    kb = {}
    for line in (text or "").splitlines():
        m = re.match(r"(\w+):\s+(\d+)\s*kB", line.strip())
        if m:
            kb[m.group(1)] = int(m.group(2))
    avail = kb.get("MemAvailable")
    swap = (kb["SwapTotal"] - kb["SwapFree"]) if "SwapTotal" in kb and "SwapFree" in kb else None
    return {"可用GB": avail / 1024 ** 2 if avail is not None else None,
            "swapGB": max(0, swap) / 1024 ** 2 if swap is not None else None}


def read_meminfo(path: str | Path | None = None) -> dict:
    """讀 `/proc/meminfo`（測試可以給假檔案路徑）；不是 Linux、讀不到都回 {可用GB: None, swapGB: None}。"""
    try:
        return parse_meminfo(Path(path or MEMINFO_PATH).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {"可用GB": None, "swapGB": None}


def swap_used_gb() -> float | None:
    """swap 用了幾 GB。macOS 讀 `sysctl vm.swapusage`；Linux（含 WSL2）讀 `/proc/meminfo`；其他系統、讀不到回 None。"""
    if sys.platform.startswith("linux"):
        return read_meminfo()["swapGB"]
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_swapusage(out)


def available_mem_gb() -> float | None:
    """可用記憶體幾 GB：只有 Linux 讀（`/proc/meminfo` 的 MemAvailable）；其他系統回 None。"""
    if not sys.platform.startswith("linux"):
        return None
    return read_meminfo()["可用GB"]


def read_resources(workdir: Path) -> dict:
    """{硬碟GB: 工作區那顆硬碟可用空間, swapGB: swap 用量或 None, 可用記憶體GB: Linux 才有，其他 None}。"""
    try:
        disk = shutil.disk_usage(str(workdir)).free / GB
    except OSError:
        disk = None
    return {"硬碟GB": disk, "swapGB": swap_used_gb(), "可用記憶體GB": available_mem_gb()}


def default_limits() -> dict:
    """門檻（settings.toml 的 [thresholds]，10-01）。"""
    from bookclub.config import load_settings

    th = load_settings().thresholds
    return {"開始前硬碟GB": th.execute_min_disk_gb_start, "硬碟GB": th.execute_min_disk_gb, "swapGB": th.execute_max_swap_gb}


def resource_problem(res: dict, limits: dict, *, starting: bool = False) -> str | None:
    """硬碟、swap 超過門檻就回傳要給人看的說明（哪一個數字、現在多少、門檻多少、怎麼處理）；沒事回 None。
    讀不到的數字（None）不算。starting＝這次第一支程式開始前（硬碟門檻用「開始前」那個）。"""
    disk, swap = res.get("硬碟GB"), res.get("swapGB")
    need = limits["開始前硬碟GB"] if starting else limits["硬碟GB"]
    if disk is not None and disk < need:
        return (f"硬碟可用空間剩 {disk:.1f} GB，{'開始前' if starting else '跑的過程'}至少要 {need:g} GB。"
                "處理：清掉用不到的檔案（例如舊的測試工作區、輸出資料夾裡用不到的中間檔），或重開機讓系統收回暫存空間；"
                "再按一次「開始執行」會接著做（做好的不重做）")
    if swap is not None and swap > limits["swapGB"]:
        return (f"記憶體不夠，系統拿硬碟頂替的量到了 {swap:.1f} GB，門檻是 {limits['swapGB']:g} GB。"
                "處理：關掉瀏覽器其他分頁與用不到的程式，或重開機；再按一次「開始執行」會接著做（做好的不重做）")
    avail, need_mem = res.get("可用記憶體GB"), limits.get("可用記憶體GB", MIN_AVAILABLE_GB)
    if avail is not None and avail < need_mem:   # 10-03 第九批（#28）：Linux／WSL2 才讀得到
        return (f"記憶體不夠：可用記憶體剩 {avail:.1f} GB，至少要 {need_mem:g} GB。"
                "處理：關掉 Windows 上用不到的程式與瀏覽器其他分頁，或重開機（WSL2 的記憶體上限在 Windows 的 .wslconfig 設定）；"
                "再按一次「開始執行」會接著做（做好的不重做）")
    return None


# 10-02 第五批：開始前就看一次記憶體（只提醒，不擋；門檻數字與跑的過程中的判斷照舊，見 resource_problem）。
# 載入模型時「系統拿硬碟頂替的量」大約會多這麼多：10-02 下午預演從 6.2 GB 衝到 13 GB；10-02 凌晨整合測試從低點開始，最高 5.8 GB
MEM_LOAD_GB = 5.0


def memory_status(swap: float | None = ..., limit: float | None = None, avail: float | None = ...) -> dict:
    """{讀得到, 現在GB, 門檻GB, 載入約多GB, 偏滿, 說明, 怎麼處理}。swap 不給就自己讀（讀不到＝None，不出錯）。
    偏滿＝現在的量加上載入模型大約會多的量，超過跑的過程中會停下來的門檻。畫面上的字不用「swap」這個詞。
    10-03 第九批（#28）：Linux／WSL2 另外看可用記憶體 avail（swap 自己讀時才跟著讀；給了 swap 沒給 avail＝不看）：
    模型載入吃的是記憶體，可用記憶體扣掉載入量低於 MIN_AVAILABLE_GB 就算偏滿。"""
    if avail is ...:
        avail = available_mem_gb() if swap is ... else None
    if swap is ...:
        swap = swap_used_gb()
    if limit is None:
        try:
            limit = default_limits()["swapGB"]
        except Exception:  # noqa: BLE001 — 設定讀不到不影響開始前的提醒
            limit = 8.5
    if swap is None and avail is None:
        return {"讀得到": False, "現在GB": None, "門檻GB": limit, "載入約多GB": MEM_LOAD_GB, "偏滿": False,
                "說明": "這台電腦讀不到記憶體不夠時系統拿硬碟頂替的量；跑的過程只看硬碟空間", "怎麼處理": ""}
    tip_text = ("現在已經偏滿，照這樣開始，可能載入模型就被停下來。開始之前：關掉瀏覽器的其他分頁、其他瀏覽器視窗和用不到的程式"
                "（Zoom、LINE、Notion、剪輯軟體等），過一兩分鐘再看一次這個數字有沒有降；這個數字常常要重開機才會降下來，"
                "降不下來就重開機，開機後先不要開別的程式，直接回來開始跑")
    if avail is not None:   # Linux／WSL2：模型載入吃記憶體，看可用記憶體夠不夠
        tight = avail - MEM_LOAD_GB < MIN_AVAILABLE_GB or (swap is not None and swap > limit)
        text = (f"可用記憶體：現在 {avail:.1f} GB；跑的過程中低於 {MIN_AVAILABLE_GB:g} GB 會自動停下來，"
                f"載入模型通常會用掉 {MEM_LOAD_GB:g} GB 左右")
        if swap is not None:
            text += f"；系統拿硬碟頂替的量現在 {swap:.1f} GB，超過 {limit:g} GB 也會停"
        return {"讀得到": True, "現在GB": round(swap, 1) if swap is not None else None, "可用GB": round(avail, 1),
                "門檻GB": limit, "載入約多GB": MEM_LOAD_GB, "偏滿": tight, "說明": text,
                "怎麼處理": tip_text if tight else ""}
    tight = swap + MEM_LOAD_GB > limit
    text = (f"記憶體不夠時系統拿硬碟頂替的量：現在 {swap:.1f} GB；跑的過程中超過 {limit:g} GB 會自動停下來，"
            f"載入模型通常會再多 {MEM_LOAD_GB:g} GB 左右")
    return {"讀得到": True, "現在GB": round(swap, 1), "門檻GB": limit, "載入約多GB": MEM_LOAD_GB, "偏滿": tight,
            "說明": text, "怎麼處理": tip_text if tight else ""}


def _child_rss_gb(pid: int) -> float | None:
    """從外面看子程式現在用了多少記憶體（`ps`，Mac、Linux 都有）；讀不到回 None。被系統結束時這是唯一的數字。"""
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout
        return int(out.strip()) * 1024 / GB if out.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def peak_rss_gb() -> float | None:
    """這支程式自己到目前為止的記憶體高峰（macOS 單位是 byte、Linux 是 KB）。"""
    try:
        import resource
    except ImportError:   # Windows 原生沒有；夥伴用 WSL2 是 Linux，有
        return None
    v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return v / GB if sys.platform == "darwin" else v * 1024 / GB


def default_part_command(workdir: Path, args: list[str]) -> list[str]:
    """子程式的指令：用同一個 Python（`sys.executable`）跑 `bookclub run part`，不靠 shell 腳本（Windows／WSL 也一樣）。"""
    return [sys.executable, "-m", "bookclub.cli", "run", "part", str(workdir), *args]


def _child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"        # 子程式的輸出一行一行即時送回來（10-01 夜間 生成.log 要等結束才寫）
    env["PYTHONIOENCODING"] = "utf-8"
    root = str(Path(__file__).resolve().parent.parent)   # 子程式用跟這支程式同一份程式碼（分身資料夾也一樣）
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


@dataclass
class PartOptions:
    """分開程式跑的設定。測試從外面傳假的子程式指令、假的硬碟／swap 數字、短的等待時間。"""

    command: Callable[[Path, list[str]], list[str]] | None = None     # (工作區, 參數) → 指令
    planners: dict | None = None        # 步驟 → (工作區, ctx) → [(名稱, 參數), ...]；不給用 _default_planners
    probe: Callable[[Path], dict] | None = None   # 工作區 → {硬碟GB, swapGB}
    limits: dict | None = None          # 不給讀 settings.toml
    check_every_s: float = 20.0         # 跑的過程中多久看一次硬碟、swap
    retry_wait_s: float = 30.0          # 被系統結束後等多久重試
    keep_awake: bool = True


def _plan_names(workdir: Path, ctx: dict) -> list[tuple[str, list[str]]]:
    from bookclub import nameplan, tts

    plan = nameplan.make_plan(workdir)   # 排計畫、寫句子清單（不載入模型），子程式讀句子清單
    if not plan["生成"]:
        return []
    return [(f"老師聲音：{p}", ["老師名字", p]) for p in tts.PHASES]


def _range_args(ctx: dict) -> list[str]:
    return ["--start", repr(float(ctx["範圍"][0])), "--end", repr(float(ctx["範圍"][1]))]


def _plan_students(workdir: Path, ctx: dict) -> list[tuple[str, list[str]]]:
    from bookclub import students

    rng = _range_args(ctx)
    groups = [g for g in students.voice_groups(workdir, *ctx["範圍"]) if g["要做"]]
    if not groups:
        return []
    return ([(f"學員聲音 {g['名稱']}：生成（{g['要做']} 段）", ["學員重念", "生成", "--voice", g["參考音"], *rng])
             for g in groups]
            + [(f"學員聲音：插入停頓（{len(groups)} 個聲線一起）", ["學員重念", "停頓", *rng])]
            + [(f"學員聲音 {g['名稱']}：收尾", ["學員重念", "收尾", "--voice", g["參考音"], *rng]) for g in groups])


def _plan_stunames(workdir: Path, ctx: dict) -> list[tuple[str, list[str]]]:
    from bookclub import studentgen

    who = studentgen.pending(workdir)
    if not who:
        return []
    return ([(f"保留原聲學員名字 {w}：生成", ["保留原聲學員名字", "生成", "--who", w]) for w in who]
            + [(f"保留原聲學員名字：插入停頓（{len(who)} 位一起）", ["保留原聲學員名字", "停頓"])]
            + [(f"保留原聲學員名字 {w}：收尾", ["保留原聲學員名字", "收尾", "--who", w]) for w in who])


def _plan_render(workdir: Path, ctx: dict) -> list[tuple[str, list[str]]]:
    return [("組裝成品", ["組裝", *_range_args(ctx), "--methods", ",".join(ctx["輸出做法"]), "--tag", ctx["標記"]])]


def _default_planners() -> dict[str, Callable]:
    return {"老師名字": _plan_names, "學員重念": _plan_students, "保留原聲學員名字": _plan_stunames, "組裝": _plan_render}


def _part_runners(opts: PartOptions, say: Callable[[str], None]) -> dict[str, Callable]:
    """每一步的「跑法」：排出這一步要開哪幾支子程式，一支一支開（開之前看停止、硬碟、swap）。"""
    from bookclub.tts import StopRequested

    planners = {**_default_planners(), **(opts.planners or {})}
    probe = opts.probe or read_resources
    state = {"開過": False, "停止原因": None, "limits": None}

    def limits() -> dict:
        if state["limits"] is None:
            state["limits"] = opts.limits or default_limits()
        return state["limits"]

    def stop_reason() -> str:
        return state["停止原因"] or STOP_MSG

    def guard(workdir: Path) -> None:
        if stop_requested(workdir):
            raise StopRequested(stop_reason())
        why = resource_problem(probe(workdir), limits(), starting=not state["開過"])
        if why:
            state["停止原因"] = why
            raise StopRequested(why)

    def wait(workdir: Path, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            if stop_requested(workdir):
                raise StopRequested(stop_reason())
            time.sleep(min(0.5, max(0.0, end - time.time())))

    def run_child(workdir: Path, label: str, args: list[str]) -> dict:
        cmd = (opts.command or default_part_command)(workdir, args)
        state["開過"] = True
        started = _now()
        t0 = time.time()
        say(f"[AI 執行] 開一支程式：{label}")
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                             env=_child_env(), text=True, encoding="utf-8", errors="replace", bufsize=1)
        tail: deque = deque(maxlen=20)
        seen: dict = {"自己量": None}

        def pump_out() -> None:   # 一般訊息：照以前一樣進網頁訊息列與終端機
            for line in p.stdout:
                line = line.rstrip("\r\n")
                if not line.strip():
                    continue
                tail.append(line)
                m = _PEAK_RE.search(line)
                if m:
                    seen["自己量"] = float(m.group(1))
                say(line)

        def pump_err() -> None:   # 錯誤訊息、模型的進度條：只到終端機（以前同一支程式時也是），留最後幾行給出錯時看
            for line in p.stderr:
                last_seg = line.rstrip("\r\n").split("\r")[-1]
                if last_seg.strip():
                    tail.append(last_seg)
                try:
                    if sys.__stderr__:
                        sys.__stderr__.write(line)
                        sys.__stderr__.flush()
                except (OSError, ValueError):
                    pass

        readers = [threading.Thread(target=f, daemon=True) for f in (pump_out, pump_err)]
        for r in readers:
            r.start()
        peak = {"swap": None, "rss": None, "disk": None}
        try:
            last = 0.0
            while p.poll() is None:
                if time.time() - last >= opts.check_every_s:
                    last = time.time()
                    res = probe(workdir)
                    if res.get("swapGB") is not None:
                        peak["swap"] = max(peak["swap"] or 0.0, res["swapGB"])
                    if res.get("硬碟GB") is not None:
                        peak["disk"] = res["硬碟GB"] if peak["disk"] is None else min(peak["disk"], res["硬碟GB"])
                    rss = _child_rss_gb(p.pid)
                    if rss is not None:
                        peak["rss"] = max(peak["rss"] or 0.0, rss)
                    if not state["停止原因"]:
                        why = resource_problem(res, limits())
                        if why:
                            state["停止原因"] = why
                            say(f"[AI 執行] ⚠️ {why}")
                            say("[AI 執行] 已請目前這一支程式做完這一句就停")
                            request_stop(workdir)
                time.sleep(min(0.2, opts.check_every_s))
        finally:
            if p.poll() is None:   # 母程式自己出事（例如按了 Ctrl-C）：子程式不要留著
                p.terminate()
        for r in readers:
            r.join(timeout=10)
        swap_max, rss_max, disk_min = peak["swap"], peak["rss"], peak["disk"]
        rc = p.returncode
        rec = {"名稱": label, "參數": args, "開始": started, "結束": _now(), "秒": round(time.time() - t0, 1),
               "結束碼": rc, "被系統結束": rc is not None and rc < 0,
               "記憶體高峰GB": round(seen["自己量"], 2) if seen["自己量"] is not None else None,
               "從外面看到的最高記憶體GB": round(rss_max, 2) if rss_max is not None else None,
               "swap最高GB": round(swap_max, 2) if swap_max is not None else None,
               "硬碟最低GB": round(disk_min, 1) if disk_min is not None else None, "最後幾行": list(tail)}
        try:   # 每一支都記一行，之後才有實測數字（10-01：不用再靠推論）
            path = part_log_path(workdir)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({k: v for k, v in rec.items() if k != "最後幾行"}, ensure_ascii=False) + "\n")
        except OSError:
            pass
        top = rec["記憶體高峰GB"] if rec["記憶體高峰GB"] is not None else rec["從外面看到的最高記憶體GB"]
        say(f"[AI 執行] {label}：結束（{rec['秒']:.0f} 秒"
            + (f"，記憶體高峰 {top:.1f} GB" if top is not None else "")
            + (f"，系統拿硬碟頂替最高 {swap_max:.1f} GB" if swap_max is not None else "") + "）")
        return rec

    def run_one(workdir: Path, label: str, args: list[str], report: Callable) -> None:
        for n in (1, 2):
            guard(workdir)
            rec = run_child(workdir, label, args)
            report(rec=rec)
            rc = rec["結束碼"]
            if rc == 0:
                return
            if rc == PART_STOPPED:
                raise StopRequested(stop_reason())
            if rec["被系統結束"]:
                if stop_requested(workdir):   # 停止中被結束的：照停止處理
                    raise StopRequested(stop_reason())
                if n == 1:
                    say(f"[AI 執行] ⚠️ {label}：程式被系統結束、沒有留下錯誤訊息，可能是記憶體不夠，程式被系統結束。"
                        f"等 {opts.retry_wait_s:.0f} 秒重試一次（做好的留在快取，不重做）")
                    wait(workdir, opts.retry_wait_s)
                    continue
                raise PartFailed(f"{label}：程式被系統結束、沒有留下錯誤訊息，可能是記憶體不夠，程式被系統結束；"
                                 "重試一次還是一樣。關掉瀏覽器其他分頁與用不到的程式（或重開機）後，"
                                 "再按「開始執行」會接著做（做好的留在快取，不重做）")
            raise PartFailed(f"{label} 出錯（結束碼 {rc}）。最後幾行：\n" + "\n".join(rec["最後幾行"][-8:]))

    def runner(key: str) -> Callable:
        def run(workdir: Path, ctx: dict) -> None:
            report = ctx.get("回報") or (lambda *a, **k: None)
            parts = planners[key](workdir, ctx)
            if not parts:
                say(f"[AI 執行] {key}：沒有要開的程式")
                return
            say(f"[AI 執行] {key}：分成 {len(parts)} 支程式跑（一支只載入一個模型，做完就結束）")
            for i, (label, args) in enumerate(parts, 1):
                report(f"第 {i}/{len(parts)} 支程式：{label}")
                run_one(workdir, label, args, report)
        return run

    return {k: runner(k) for k, _ in STEPS}


def run_part(workdir: str | Path, step: str, phase: str | None = None, *, voice: str | None = None,
             who: str | None = None, start: float | None = None, end: float | None = None,
             methods: list[str] | None = None, tag: str | None = None, log: Callable[[str], None] = print) -> int:
    """`bookclub run part`（第 4 步內部用）：一支程式只做一段。回傳結束碼：0 做完、PART_STOPPED 按了停止；
    出錯直接丟出去（Python 印錯誤訊息、結束碼 1，母程式抓最後幾行）。結束前印這支程式的記憶體高峰。"""
    from bookclub.tts import StopRequested

    workdir = Path(workdir).expanduser()
    from bookclub import refpick, tts as _tts

    # 10-03 第九批（#17）：生成後用 Groq 檢查內容時碰到額度用完，等待中按「停止」也要有效（丟 StopRequested）
    refpick.set_groq_stop_check(lambda: _tts.check_stop(workdir))
    try:
        if step == "老師名字":
            from bookclub import nameplan, tts

            tts.generate_teacher(workdir, nameplan.sentences_path(workdir), phase=phase, log=log)
        elif step == "學員重念":
            from bookclub import students, tts

            align = tts.lazy_aligner(log, "學員聲音") if phase == "停頓" else None   # 全部聲線共用，只載入一次
            students.generate_students(workdir, start=start, end=end, only_ref=voice, phase=phase, align=align, log=log)
        elif step == "保留原聲學員名字":
            from bookclub import studentgen, tts

            align = tts.lazy_aligner(log, studentgen.TAG) if phase == "停頓" else None
            studentgen.generate(workdir, only_who=who, phase=phase, align=align, log=log)
        elif step == "組裝":
            from bookclub.render import render_video

            render_video(workdir, start, end, methods=methods or default_methods(), tag=tag or tag_for(start, end))
        else:
            raise ValueError(f"沒有這一步：{step}")
        return 0
    except StopRequested as e:
        log(f"[AI 執行] {e}")
        return PART_STOPPED
    finally:
        peak = peak_rss_gb()
        if peak is not None:
            log(f"{PEAK_PREFIX} {peak:.2f} GB")


def keep_awake(log: Callable[[str], None] = print):
    """第 4 步掛著跑的時候不讓電腦睡著（09-30）。macOS 用內建的 `caffeinate`（螢幕可以關，電腦不睡）；
    其他系統不處理（Windows／WSL 照 README 設電源選項）。回傳要在結束時呼叫的函式。"""
    import os
    import shutil
    import subprocess
    import sys

    if sys.platform != "darwin" or not shutil.which("caffeinate"):
        return lambda: None
    try:
        p = subprocess.Popen(["caffeinate", "-i", "-m", "-s", "-w", str(os.getpid())])
    except OSError:
        return lambda: None
    log("[AI 執行] 執行期間不讓電腦睡著（caffeinate），做完自動恢復")

    def stop() -> None:
        try:
            p.terminate()
        except OSError:
            pass
    return stop


def execute_key_problem(*, reassemble_only: bool = False, only_steps: list[str] | None = None,
                        allow: bool = False, env: dict | None = None, system: str | None = None) -> str | None:
    """10-04 #110：第 4 步要生成、卻讀不到 Groq 金鑰時回傳要給人看的說明（可以開始就回傳 None）。

    從終端機直接跑（不是雙擊啟動）常讀不到金鑰，「念對沒有」的檢查整晚都不會做。只跑組裝不用金鑰。
    怎麼補金鑰依平台只講適用的那一種，跟網頁伺服器同一套說法（`server.groq_key_howto`）；`system` 給測試用。"""
    env = os.environ if env is None else env
    if allow or reassemble_only or env.get("GROQ_API_KEY"):
        return None
    if only_steps is not None and not [s for s in only_steps if s != "組裝"]:
        return None
    from bookclub.server import groq_key_execute_message

    return (groq_key_execute_message(system, cli=True)
            + "。確定不檢查也要跑（每一句都標要人聽）就加 --allow-no-key")


def run_execute(workdir: str | Path, *, start: float | None = None, end: float | None = None,
                methods: list[str] | None = None, redo: bool = False, only_steps: list[str] | None = None,
                runners: dict | None = None, checks: dict | None = None, skip_precheck: bool = False,
                parts: PartOptions | None = None, redo_returned: bool = False, reassemble_only: bool = False,
                log: Callable[[str], None] = print) -> dict:
    """依序跑第 4 步。回傳進度。

    redo_returned（10-01 第三批）：第 5 步退回的那幾筆一起重做——開始前先清掉那幾句的生成結果
    （`finalcheck.prepare_redo`），之後照常一步一步跑（只有清掉的會重新生成），組裝一定重做；
    組裝做完，那幾筆在第 5 步回到「還沒看」、標「重做過」（`finalcheck.finish_redo`）。

    reassemble_only（10-03 第八批 #23）：「只重新組裝」——退回的那幾筆記進重做中，但不清生成、不換念法
    （`prepare_redo(reassemble_only=True)`），只跑「組裝」這一步；組裝做完一樣回到還沒看、標「重做過（只重新組裝）」。
    沒有退回的也照樣重新組裝一次。

    10-01：每一步、每一個聲線各自開一支程式跑（`_part_runners`，見上面「每一步、每一個聲線各自一支程式跑」）；
    parts 可以從外面傳假的子程式指令、假的硬碟／swap 數字（測試用）。
    runners／checks 從外面傳的話（測試用假的，不載入模型）照舊在同一支程式裡跑。"""
    workdir = Path(workdir).expanduser()
    lock = threading.Lock()
    raw_log = log

    def log(s: str) -> None:   # 子程式的輸出由另一個執行緒送進來，一次印一行
        with lock:
            raw_log(s)

    if not skip_precheck:
        pre = precheck(workdir)
        if not pre["可以開始"]:
            raise FileNotFoundError("還不能開始第 4 步：\n- " + "\n- ".join(pre["缺"]))
        for n in pre["提醒"]:
            log(f"[AI 執行] 提醒：{n}")
        mem = memory_status()   # 10-02 第五批：命令列也在開始時印記憶體狀況，偏滿的話先提醒（不擋）
        log(f"[AI 執行] {mem['說明']}")
        if mem["偏滿"]:
            log(f"[AI 執行] ⚠️ {mem['怎麼處理']}")
        if not only_steps or "組裝" in only_steps:   # 10-01：要組成品才看總檢查（只生成聲音不影響成品，不擋）
            fc = final_check(workdir)
            if not fc["可以開始"]:
                rows = [r for r in fc["一定要處理"] if not r.get("已按聽過")]
                raise FileNotFoundError("開始前總檢查還有一定要處理的：\n- " + "\n- ".join(
                    f"{wd.fmt_time(r['start'])} {r['說明']}" for r in rows[:20]) + ("\n（還有更多）" if len(rows) > 20 else ""))
    from bookclub import epcodes

    n = epcodes.sync(workdir)   # 09-29：名字候選的代號跟這一集的代號表對齊
    if n:
        log(f"[AI 執行] 名字代號照這一集的代號表更新了 {n} 筆")
    from bookclub import nameplan

    nameplan.refresh_plan(workdir)   # 10-02 第七批（A1）：每次都重排名字處理計畫，組裝才拿得到最新的做法
    a = 0.0 if start is None else float(start)
    b = float(end) if end is not None else float(video_duration(workdir) or 0.0)
    if b <= a:
        raise ValueError("不知道影片多長，用 --end 指定到幾分幾秒")
    ctx = {"範圍": [a, b], "輸出做法": list(methods or default_methods()), "標記": tag_for(a, b)}
    from bookclub import finalcheck

    redoing = None
    if reassemble_only:
        only_steps = list(only_steps or ["組裝"])
        redoing = finalcheck.prepare_redo(workdir, a, b, log=log, reassemble_only=True) or {finalcheck.REASSEMBLE_ONLY: True}
    elif redo_returned:
        redoing = finalcheck.prepare_redo(workdir, a, b, log=log)
    if not redoing and (finalcheck.load_check(workdir).get("重做中") or {}).get("項目"):
        redoing = {"接著做": True}   # 上次重做退回的沒做完（停止、失敗）：這次組裝做完一樣收尾
    runners_given = bool(runners)
    opts = parts or PartOptions()
    runners = {**(_default_runners() if runners_given else _part_runners(opts, log)), **(runners or {})}
    checks = {**_default_checks(), **(checks or {})}
    prog = {"開始時間": _now(), "結束時間": None, "範圍": ctx["範圍"], "輸出做法": ctx["輸出做法"],
            "步驟": {k: {"說明": desc, "狀態": "等待"} for k, desc in STEPS}, "錯誤": None}

    def save() -> None:
        wd.write_json(progress_path(workdir), prog)

    from bookclub.tts import StopRequested, check_stop

    _clear_stop(workdir)   # 上次按的停止不算這一次
    save()
    awake_off = keep_awake(log) if not runners_given and opts.keep_awake else (lambda: None)   # 測試用假步驟時不用
    try:
        for key, _desc in STEPS:
            st = prog["步驟"][key]
            if only_steps and key not in only_steps:
                st.update({"狀態": "略過", "訊息": "這次沒選這一步"})
                continue
            try:
                check_stop(workdir)
            except StopRequested as e:
                return _stopped(prog, st, e, save, log)
            done, why = checks[key](workdir, ctx)
            if done and redoing and key == "組裝":   # 退回的只要重新組裝（剪掉、消音⋯）也要真的組一次
                done, why = False, "第 5 步退回的要重新組裝"
            if done and not redo:
                st.update({"狀態": "跳過", "訊息": f"做過了：{why}"})
                log(f"[AI 執行] {key}：做過了，跳過（{why}）")
                save()
                continue
            st.update({"狀態": "進行中", "開始": _now(), "訊息": why})
            save()
            log(f"[AI 執行] {key}：開始（{why}）")

            def report(msg: str | None = None, rec: dict | None = None, st=st, why=why) -> None:
                """分開程式跑時：目前跑到第幾支（網頁「說明」欄）、每一支的結束碼與記憶體高峰。"""
                if msg:
                    st["訊息"] = f"{why}｜{msg}"
                if rec:
                    st.setdefault("子程式", []).append({k: v for k, v in rec.items() if k not in ("參數", "最後幾行")})
                save()

            ctx["回報"] = report
            try:
                runners[key](workdir, ctx)
            except StopRequested as e:
                return _stopped(prog, st, e, save, log)
            except Exception as e:  # noqa: BLE001 — 記下來再往外丟，網頁看得到是哪一步、什麼錯
                st.update({"狀態": "失敗", "結束": _now(), "訊息": f"{type(e).__name__}：{e}"})
                prog["錯誤"] = f"{key}：{type(e).__name__}：{e}"
                prog["結束時間"] = _now()
                save()
                log(traceback.format_exc(limit=3))
                raise
            ctx.pop("回報", None)
            # 10-02 第七批（C1）：跑完再檢查一次真的做好了沒有（例如組裝沒產出成品、驗證沒過），沒有就標失敗、不收尾
            ok, why_after = checks[key](workdir, ctx)
            if not ok:
                st.update({"狀態": "失敗", "結束": _now(), "訊息": f"跑完了，但還沒做好：{why_after}"})
                prog["錯誤"] = f"{key}：跑完了，但還沒做好：{why_after}"
                prog["結束時間"] = _now()
                save()
                log(f"[AI 執行] ⚠️ {key}：跑完了，但還沒做好（{why_after}）")
                raise RuntimeError(f"{key}：跑完了，但還沒做好：{why_after}")
            st.update({"狀態": "做完", "結束": _now(), "訊息": why if "子程式" not in st else f"{why}｜{len(st['子程式'])} 支程式做完"})
            save()
            log(f"[AI 執行] {key}：做完")
            if key == "組裝" and redoing:
                try:
                    back = finalcheck.finish_redo(workdir)
                    if back:
                        log(f"[AI 執行] 第 5 步退回的 {len(back['項目'])} 筆重做好了：到第 5 步重新看這幾筆")
                except Exception as e:  # noqa: BLE001 — 收尾失敗不算這次執行失敗（第 5 步照樣看得到新成品）
                    log(f"[AI 執行] ⚠️ 第 5 步退回的那幾筆沒標成「重做過」：{e}")
        prog["結束時間"] = _now()
        save()
        log("[AI 執行] 全部做完。下一步：網頁第 5 步「成品檢查」")
        return prog
    finally:
        awake_off()


def _stopped(prog: dict, st: dict, e: Exception, save: Callable[[], None], log: Callable[[str], None]) -> dict:
    """按了停止：這一步標「停止」、整份標停止，不算失敗。"""
    st.update({"狀態": "停止", "結束": _now(), "訊息": str(e)})
    prog["停止"] = True
    prog["停止原因"] = str(e)
    prog["結束時間"] = _now()
    save()
    log(f"[AI 執行] {e}")
    return prog


def current_steps(workdir: str | Path, a: float | None = None, b: float | None = None,
                  methods: list[str] | None = None) -> dict:
    """10-01 第三批 4：第 4 步「執行步驟」表格沒在執行時顯示現在的狀態（以前是上一次執行的結果，跟上面的統計對不起來）。
    跟按「開始執行」時判斷做過沒有用同一套（`_default_checks`，統計也是同一個判斷）。回傳 {步驟: {做好了, 說明}}；讀不到的給 None。"""
    workdir = Path(workdir)
    a = 0.0 if a is None else float(a)
    b = float(b) if b is not None else float(video_duration(workdir) or 0.0)
    ctx = {"範圍": [a, b], "輸出做法": list(methods or default_methods()), "標記": tag_for(a, b)}
    out = {}
    for key, check in _default_checks().items():
        try:
            done, why = check(workdir, ctx)
            out[key] = {"做好了": bool(done), "說明": why}
        except Exception as e:  # noqa: BLE001 — 某一步讀不到不影響其他步
            out[key] = {"做好了": None, "說明": f"讀不到：{e}"}
    return out


GEN_STEP_KEYS = ("老師名字", "學員重念", "保留原聲學員名字")   # 10-05 #97：要生成聲音的三步（組裝以外）


def _left_count(why: str) -> int | None:
    """三步「做過沒有」說明裡還沒生成的句數（`還有 3／10 句要生成`、`老師參考音換過了，10 句都要重新生成`）；看不出來回 None。"""
    m = re.search(r"還有 (\d+)／", why or "") or re.search(r"(\d+) 句都要重新生成", why or "")
    return int(m.group(1)) if m else None


def reassemble_problem(workdir: str | Path, a: float | None = None, b: float | None = None,
                       steps: dict | None = None) -> str | None:
    """10-05 #97：第 5 步沒有退回時按「只重新組裝」之前的檢查（只讀）。可以按回傳 None，不行回傳要給人看的一句話。
    - 還沒有組好的成品（這個範圍的 `輸出/成品_{標記}_*.mp4`，驗證沒過的不算）：請按「開始執行」
    - 三步生成（老師名字、學員重念、保留原聲學員名字）有還沒生成的：只重新組裝會把那些地方變成消音，請按「開始執行」
    跟「開始執行」判斷做過沒有用同一套（`current_steps`）；steps 可以傳已經算好的（同一個範圍）。"""
    workdir = Path(workdir)
    a = 0.0 if a is None else float(a)
    b = float(b) if b is not None else float(video_duration(workdir) or 0.0)
    out = workdir / "輸出"
    tag = tag_for(a, b)
    made = [p for p in (out.iterdir() if out.is_dir() else [])
            if p.name.startswith(f"成品_{tag}_") and p.suffix.lower() == ".mp4" and "_驗證沒過" not in p.stem]
    if not made:
        return "還沒有組裝好的成品，請按「開始執行」"
    steps = current_steps(workdir, a, b) if steps is None else steps
    unread = [k for k in GEN_STEP_KEYS if (steps.get(k) or {}).get("做好了") is None]
    if unread:
        return f"讀不到「{'、'.join(unread)}」有沒有生成好，請按「開始執行」"
    left = [(k, steps[k].get("說明") or "") for k in GEN_STEP_KEYS if not steps[k]["做好了"]]
    if not left:
        return None
    counts = [_left_count(why) for _k, why in left]
    detail = "；".join(f"{k}：{why}" for k, why in left)
    head = f"還有 {sum(counts)} 句聲音沒生成" if None not in counts else "還有聲音沒生成"
    return f"{head}（{detail}），只重新組裝會把這些地方變成消音，請按「開始執行」"


def status(workdir: str | Path) -> dict:
    """`GET /api/execute`：前置檢查、上次的進度、第 5 步退回的清單。"""
    from bookclub import finalcheck

    workdir = Path(workdir)
    redo, doing = [], False
    try:
        r = finalcheck.redo_list(workdir)
        redo, doing = r["項目"], r.get("重做中", False)
    except Exception:  # noqa: BLE001 — 還沒有成品檢查就是沒有退回
        redo = []
    return {"前置檢查": precheck(workdir), "進度": wd.read_json(progress_path(workdir), default=None),
            "影片長度": video_duration(workdir), "預設輸出做法": default_methods(), "輸出做法選項": method_options(),
            "退回清單": redo,
            "重做中": doing}
