"""第 4 步「AI 執行」一個指令跑完：老師名字 → 學員重念 → 保留原聲學員的名字 → 組裝（09-29 宇軒）。

`bookclub run execute <工作區> [--start 0:00 --end 1:38:00] [--methods sw]`

情境：振興老師有時候已經自己檢查完全片、標好要改的地方、挑好參考聲音，想直接請 AI 從第 4 步做下去。
限制：學員的話要照逐字稿重念，所以**第 1 步轉文字還是要跑**（電腦自動）；能省掉的是第 2、3 步的人工。
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

import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

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


def video_duration(workdir: Path) -> float | None:
    analysis = wd.read_json(wd.analysis_result_path(workdir), default={}) or {}
    merged = wd.read_json(wd.merged_transcript_path(workdir), default={}) or {}
    return analysis.get("影片長度") or merged.get("duration")


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
    refs = students.default_refs()
    lack = [str(p) for p in refs.values() if not (Path(p).is_file() and Path(p).with_suffix(".txt").is_file())]
    if lack:
        missing.append("找不到學員匿名聲線參考音：" + "、".join(lack))
    from bookclub import review

    if not review.video_path(workdir):
        missing.append("找不到原片（分析結果記錄的影片路徑不在了）")
    from bookclub import epcodes

    lack_codes = epcodes.missing(workdir)
    if lack_codes:
        missing.append(f"有 {len(lack_codes)} 個名字這一集還沒選英文代號：第 3 步開始前 ②（學員）或 ③（其他名稱）選好，"
                       "或按「幫還沒代號的自動配」")
    dec = review.load_decisions(workdir)
    if not any(dec["開始前確認"].values()):
        notes.append("第 3 步還沒覆核：照第 1 步的建議做（名字整句換掉、學員全部重念、建議刪除的段落不刪）")
    return {"可以開始": not missing, "缺": missing, "提醒": notes}


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
    left = [g["id"] for g in gen if not (recs.get(g["id"]) or {}).get("放回時間格")
            or recs[g["id"]].get("text") not in (None, g["text"])]
    return (not left), (f"{len(gen)} 句都生成好了" if not left else f"還有 {len(left)}／{len(gen)} 句要生成")


def students_done(workdir: Path, a: float | None, b: float | None) -> tuple[bool, str]:
    from bookclub import students

    items, _ = students.build_items(workdir, a, b)
    if not items:
        return True, "範圍內沒有學員段落"
    log = wd.read_json(students.log_path(workdir), default=None) or {}
    recs = {r["id"]: r for r in log.get("句子", [])}
    left = [it["id"] for it in items if it["id"] not in recs or recs[it["id"]].get("text") != it["text"]
            or not recs[it["id"]].get("放回時間格")]
    return (not left), (f"{len(items)} 段都生成好了" if not left else f"還有 {len(left)}／{len(items)} 段要生成")


def stunames_done(workdir: Path) -> tuple[bool, str]:
    from bookclub import studentgen, studentnames

    sp = studentnames.plan(workdir)
    if not sp["生成"]:
        return True, f"沒有選換成代號的（直接消音 {len(sp['消音'])} 筆，組裝時處理）"
    rec = wd.read_json(studentgen.log_path(workdir), default=None) or {}
    recs = {r["id"]: r for r in rec.get("句子", [])}
    back = rec.get("退回直接消音") or {}
    left = [g["id"] for g in sp["生成"] if g["id"] not in back
            and not ((recs.get(g["id"]) or {}).get("放回時間格") and recs[g["id"]].get("text") == g["text"])]
    return (not left), (f"{len(sp['生成'])} 句都處理好了" if not left else f"還有 {len(left)}／{len(sp['生成'])} 句要生成")


def render_inputs(workdir: Path) -> list[Path]:
    from bookclub import nameplan, review, studentgen, studentnames, students, tts

    return [p for p in (review.review_path(workdir), nameplan.plan_path(workdir), tts.teacher_log_path(workdir),
                        students.log_path(workdir), workdir / "校對" / "段落.json", workdir / "名字覆核決定.json",
                        studentnames.decisions_path(workdir), studentgen.log_path(workdir))
            if p.exists()]


def render_done(workdir: Path, tag: str, methods: list[str]) -> tuple[bool, str]:
    outs = [workdir / "輸出" / f"成品_{tag}_{m}.mp4" for m in methods]
    if not all(p.exists() for p in outs):
        return False, "還沒組裝"
    newest_in = max((p.stat().st_mtime for p in render_inputs(workdir)), default=0.0)
    if min(p.stat().st_mtime for p in outs) < newest_in:
        return False, "覆核或生成結果比成品新，要重新組裝"
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


def run_execute(workdir: str | Path, *, start: float | None = None, end: float | None = None,
                methods: list[str] | None = None, redo: bool = False, only_steps: list[str] | None = None,
                runners: dict | None = None, checks: dict | None = None, skip_precheck: bool = False,
                log: Callable[[str], None] = print) -> dict:
    """依序跑第 4 步。runners／checks 可以從外面傳（測試用假的，不載入模型）。回傳進度。"""
    workdir = Path(workdir).expanduser()
    if not skip_precheck:
        pre = precheck(workdir)
        if not pre["可以開始"]:
            raise FileNotFoundError("還不能開始第 4 步：\n- " + "\n- ".join(pre["缺"]))
        for n in pre["提醒"]:
            log(f"[AI 執行] 提醒：{n}")
    from bookclub import epcodes

    n = epcodes.sync(workdir)   # 09-29：名字候選的代號跟這一集的代號表對齊
    if n:
        log(f"[AI 執行] 名字代號照這一集的代號表更新了 {n} 筆")
    a = 0.0 if start is None else float(start)
    b = float(end) if end is not None else float(video_duration(workdir) or 0.0)
    if b <= a:
        raise ValueError("不知道影片多長，用 --end 指定到幾分幾秒")
    ctx = {"範圍": [a, b], "輸出做法": list(methods or default_methods()), "標記": tag_for(a, b)}
    runners = {**_default_runners(), **(runners or {})}
    checks = {**_default_checks(), **(checks or {})}
    prog = {"開始時間": _now(), "結束時間": None, "範圍": ctx["範圍"], "輸出做法": ctx["輸出做法"],
            "步驟": {k: {"說明": desc, "狀態": "等待"} for k, desc in STEPS}, "錯誤": None}

    def save() -> None:
        wd.write_json(progress_path(workdir), prog)

    save()
    for key, _desc in STEPS:
        st = prog["步驟"][key]
        if only_steps and key not in only_steps:
            st.update({"狀態": "略過", "訊息": "這次沒選這一步"})
            continue
        done, why = checks[key](workdir, ctx)
        if done and not redo:
            st.update({"狀態": "跳過", "訊息": f"做過了：{why}"})
            log(f"[AI 執行] {key}：做過了，跳過（{why}）")
            save()
            continue
        st.update({"狀態": "進行中", "開始": _now(), "訊息": why})
        save()
        log(f"[AI 執行] {key}：開始（{why}）")
        try:
            runners[key](workdir, ctx)
        except Exception as e:  # noqa: BLE001 — 記下來再往外丟，網頁看得到是哪一步、什麼錯
            st.update({"狀態": "失敗", "結束": _now(), "訊息": f"{type(e).__name__}：{e}"})
            prog["錯誤"] = f"{key}：{type(e).__name__}：{e}"
            prog["結束時間"] = _now()
            save()
            log(traceback.format_exc(limit=3))
            raise
        st.update({"狀態": "做完", "結束": _now()})
        save()
        log(f"[AI 執行] {key}：做完")
    prog["結束時間"] = _now()
    save()
    log("[AI 執行] 全部做完。下一步：網頁第 5 步「成品檢查」")
    return prog


def status(workdir: str | Path) -> dict:
    """`GET /api/execute`：前置檢查、上次的進度、第 5 步退回的清單。"""
    from bookclub import finalcheck

    workdir = Path(workdir)
    redo = []
    try:
        redo = finalcheck.redo_list(workdir)["項目"]
    except Exception:  # noqa: BLE001 — 還沒有成品檢查就是沒有退回
        redo = []
    return {"前置檢查": precheck(workdir), "進度": wd.read_json(progress_path(workdir), default=None),
            "影片長度": video_duration(workdir), "預設輸出做法": default_methods(), "退回清單": redo}
