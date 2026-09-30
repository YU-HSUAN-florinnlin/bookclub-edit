"""第 4 步「AI 執行」一個指令跑完：老師名字 → 學員重念 → 保留原聲學員的名字 → 組裝（09-29 宇軒）。

`bookclub run execute <工作區> [--start 0:00 --end 1:38:00] [--methods sw]`

情境：振興老師有時候已經自己檢查完全片、標好要改的地方、挑好參考聲音，想直接請 AI 從第 4 步做下去。
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
        missing.append(f"找不到學員匿名聲線（{'、'.join(lack)}聲）：{students.voice_dir() / students.CANDIDATE_DIR}"
                       f" 或 {'、'.join(str(students.default_refs()[g]) for g in lack)}")
    from bookclub import review

    if not review.video_path(workdir):
        missing.append("找不到原片（分析結果記錄的影片路徑不在了）")
    from bookclub import epcodes

    lack_codes = epcodes.missing(workdir)
    if lack_codes:
        missing.append(f"有 {len(lack_codes)} 個名字這一集還沒選英文代號：第 3 步開始前 ②（學員）或 ③（其他名稱）選好，"
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
    dec = review.load_decisions(workdir)
    if not any(dec["開始前確認"].values()):
        notes.append("第 3 步還沒覆核：照第 1 步的建議做（名字整句換掉、學員全部重念、建議刪除的段落不刪）")
    return {"可以開始": not missing, "缺": missing, "提醒": notes, "缺代號": len(lack_codes)}


# ---------- 開始前總檢查（10-01 宇軒 7-5，只讀） ----------

GEN_SPEED = 16.5          # 這台 Mac 每 1 秒聲音約 14–19 秒（09-30 實測）
ASSEMBLE_S = 21 * 60      # 整支組裝約 21 分鐘（09-30 實測）
LONG_TEACHER_S = 10.0
OUTSIDE_MIN_S = 0.3       # 學員的話落在段落外面超過這麼久才算


def _uncovered(a: float, b: float, ranges: list[tuple[float, float]]) -> float:
    from bookclub import assemble

    return sum(e - s for s, e in assemble.subtract(a, b, ranges))


def final_check(workdir: str | Path) -> dict:
    """第 3 步全部通過之後、開始第 4 步之前的總檢查（只讀）。回傳：
    {一定要處理: [列], 請看一眼: [列], 可以開始: bool}；每一列 {key, start, end, 說明, 可以按聽過?, 已按聽過?}。
    「一定要處理」有還沒按聽過的列就不能開始；「請看一眼」不擋（網頁上要按一次「我看過了」）。"""
    import shutil

    from bookclub import assemble, nameplan, review, students
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    dec = review.load_decisions(workdir)
    heard = set((dec.get("總檢查") or {}).get("聽過") or [])
    tdata = turns_mod.page_data(workdir)
    turns = tdata.get("段落", []) if not tdata.get("尚未準備") else []
    sents = (wd.read_json(wd.speakers_path(workdir), default={}) or {}).get("sentences", [])
    by_id = {x["id"]: x for x in sents}
    words = (wd.read_json(wd.merged_transcript_path(workdir), default={}) or {}).get("words") or []
    kept = {k for k, v in dec["學員聲音"].items() if v == "保留原聲"}
    try:
        items, _ = students.build_items(workdir)
    except FileNotFoundError:
        items = []
    plan = nameplan.compute_plan(workdir) if wd.read_json(wd.names_path(workdir), default=None) else {"生成": [], "消音": [], "要人處理": []}
    cuts = [(c["start"], c["end"]) for c in dec["刪除段落"] if c.get("狀態") != "還原"]
    mutes = [(m["start"], m["end"]) for m in assemble.local_mutes(dec)] + [(m["start"], m["end"]) for m in plan.get("消音", [])]
    teacher = [tuple(g["slot"]) for g in plan["生成"]]
    stu_slots = [tuple(it["slot"]) for it in items]
    handled = cuts + mutes + teacher + stu_slots
    must, look = [], []

    def row(lst, key, a, b, text, ack=False):
        lst.append({"key": key, "start": round(a, 3), "end": round(b, 3), "說明": text,
                    **({"可以按聽過": True, "已按聽過": key in heard} if ack else {})})

    # 1. 學員的話落在段落外面（人改過段落的開頭或結尾，句子的一部分在外面、又沒被別的處理蓋到）
    for t in turns:
        if t.get("說話者") in (None, "老師") or t["說話者"] in kept:
            continue
        for sid in t.get("句子", []):
            x = by_id.get(sid)
            if not x:
                continue
            for a, b in ((x["start"], min(x["end"], t["start"])), (max(x["start"], t["end"]), x["end"])):
                if b - a >= OUTSIDE_MIN_S and _uncovered(a, b, handled) >= OUTSIDE_MIN_S:
                    row(must, f"段落外:{t['id']}:{a:.1f}", a, b,
                        f"{t['說話者']} 的句子有 {b - a:.1f} 秒在段落（{wd.fmt_time(t['start'])}–{wd.fmt_time(t['end'])}）外面，"
                        "會是學員原聲：把段落的起訖改回去包住，或另外處理這一段；"
                        "聽過確定外面那一段不是學員（例如是老師接話），按「我聽過了」", ack=True)
    # 2. 名字換不了代號
    names = wd.read_json(wd.names_path(workdir), default={}) or {}
    ndec = wd.read_json(review.name_decisions_path(workdir), default={}) or {}
    cands = review.effective_name_candidates(workdir, names.get("candidates", []), ndec) if names else []
    when = {str(c.get("id") or i): c for i, c in enumerate(cands, start=1)}
    for m in plan.get("要人處理", []):
        c = when.get(str(m["候選"]), {})
        row(must, f"名字:{m['候選']}", c.get("start", 0.0), c.get("end", 0.0), f"老師提到名字（第 {m['候選']} 筆）：{m['原因']}")
    # 3. 要念的字數跟那段時間逐字稿的字數差太多
    for g in plan["生成"]:
        src = nameplan.range_words(words, *g["slot"])
        if nameplan.too_short(g["text"], src):
            row(must, f"字太少:{g['id']}", g["slot"][0], g["slot"][1],
                f"老師重念 {g['id']}：要念 {nameplan.say_count(g['text'])} 個字，這段時間逐字稿有 {nameplan.say_count(src)} 個字，"
                "其他的話會不見：把重念範圍改小，或把話補齊")
    # 4. 重疊選了要生成、但缺文字或缺學員是誰
    for o in review.overlap_choices(workdir):
        why = review.overlap_gen_problem(o, [tuple(it["slot"]) for it in items if not it.get("重疊")])
        if why:
            row(must, f"重疊:{o['id']}", o["start"], o["end"], f"重疊（{review.OVERLAP_LABEL.get(o['做法'], o['做法'])}）：{why}")
    # 5. 聲紋判成「不是老師」的句子，整句都不在任何處理的範圍、也不在學員段落裡（可能是漏抓的學員發言）
    #    段落的聲音判斷也不是老師的才放「一定要處理」；段落判成老師的（第一堂 92 句，多半是誤判）在「請看一眼」彙總一列
    stu_turns = [(t["start"], t["end"]) for t in turns if t.get("說話者") not in (None, "老師")]
    soft = []
    for x in sents:
        if x.get("label") != "不是老師" or x["end"] - x["start"] < OUTSIDE_MIN_S:
            continue
        if any(a < x["end"] and x["start"] < b for a, b in handled + stu_turns):
            continue
        mid = (x["start"] + x["end"]) / 2
        home = next((t for t in turns if t["start"] <= mid <= t["end"]), None)
        if home and home.get("聲音判斷") == "老師":
            soft.append(x)
            continue
        row(must, f"聲紋:{x['id']}", x["start"], x["end"],
            f"聲紋判成不是老師、{x['end'] - x['start']:.1f} 秒，不在任何學員段落或處理範圍裡：可能是漏抓的學員發言。"
            "聽一下；沒有學員的聲音就按「我聽過了」", ack=True)

    # 請看一眼
    for c in dec["刪除段落"]:
        if c.get("狀態") != "還原":
            row(look, f"剪掉:{c['id']}", c["start"], c["end"], f"剪掉 {c['end'] - c['start']:.1f} 秒（聲音和畫面都拿掉，影片會變短，畫面會跳一下）")
    for m in assemble.local_mutes(dec):
        row(look, f"消音:{m['id']}", m["start"], m["end"], f"消音 {m['end'] - m['start']:.1f} 秒（只拿掉聲音，畫面留著）")
    for m in plan.get("消音", []):
        row(look, f"名字消音:{m['候選']}", m["start"], m["end"], f"老師提到名字直接消音 {m['end'] - m['start']:.1f} 秒")
    for g in plan["生成"]:
        if g["slot"][1] - g["slot"][0] > LONG_TEACHER_S:
            row(look, f"長句:{g['id']}", g["slot"][0], g["slot"][1],
                f"老師 AI 聲音重念 {g['slot'][1] - g['slot'][0]:.1f} 秒（超過 {LONG_TEACHER_S:.0f} 秒）")
    if soft:
        row(look, "聲紋:段落是老師", soft[0]["start"], soft[-1]["end"],
            f"另外 {len(soft)} 句聲紋判成不是老師、但整段的聲音判斷是老師（多半是誤判，例如冥想引導、老師壓低聲音）。"
            "時間：" + "、".join(wd.fmt_time(x["start"]) for x in soft[:40]) + ("⋯" if len(soft) > 40 else ""))
    covered = review.covered_overlaps(workdir, dec, turns) if turns else []
    gen_s = sum(g["slot"][1] - g["slot"][0] for g in plan["生成"]) + sum(it["slot_s"] for it in items)
    free = shutil.disk_usage(str(workdir)).free / 1e9
    summary = {"自動算處理好": [{"id": x["id"], "start": x["start"], "end": x["end"], "涵蓋": x["涵蓋"]["名稱"]} for x in covered],
               "要生成秒數": round(gen_s), "預估秒數": round(gen_s * GEN_SPEED + ASSEMBLE_S), "硬碟可用GB": round(free, 1),
               "提醒": "執行期間關掉其他程式（Zoom、瀏覽器分頁）；接上電源、筆電不要闔上（螢幕可以關）"}
    left = [r for r in must if not r.get("已按聽過")]
    return {"一定要處理": sorted(must, key=lambda r: r["start"]), "請看一眼": sorted(look, key=lambda r: r["start"]),
            "摘要": summary, "可以開始": not left, "還要處理": len(left),
            "看過": bool((dec.get("總檢查") or {}).get("看過"))}


def ack_final(workdir: str | Path, key: str | None = None, heard: bool = True, seen: bool | None = None) -> dict:
    """`POST /api/execute/finalcheck`：「一定要處理」裡可以按的那一列按「我聽過了」（key），或整頁「我看過了」（seen）。"""
    from bookclub import review

    workdir = Path(workdir)
    with review._lock:
        dec = review.load_decisions(workdir)
        fc = dec.setdefault("總檢查", {"聽過": [], "看過": False})
        fc.setdefault("聽過", [])
        if key:
            if heard and key not in fc["聽過"]:
                fc["聽過"].append(key)
            elif not heard and key in fc["聽過"]:
                fc["聽過"].remove(key)
        if seen is not None:
            fc["看過"] = bool(seen)
            fc["看過時間"] = _now()
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
    ref_wav, ref_txt = wd.ref_dir(workdir) / "ref.wav", wd.ref_dir(workdir) / "ref.txt"
    if recs and tlog.get("參考音") and ref_wav.is_file() and ref_txt.is_file() and not (
            tts.same_ref_file(tlog, ref_wav) and tlog.get("參考音逐字稿") == ref_txt.read_text(encoding="utf-8").strip()):
        return False, f"老師參考音換過了，{len(gen)} 句都要重新生成"
    table = tts.load_pron_table(workdir / tts.PRON_TABLE_NAME if (workdir / tts.PRON_TABLE_NAME).is_file() else None)
    left = [g["id"] for g in gen if not (recs.get(g["id"]) or {}).get("放回時間格")
            or tts.record_stale(recs[g["id"]], {**g, "生成用文字": tts.apply_pron(g["text"], table)[0]})]
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
    left = [it["id"] for it in items if not (recs.get(it["id"]) or {}).get("放回時間格")
            or tts.record_stale(recs[it["id"]], {**it, "生成用文字": tts.apply_pron(it["text"], table)[0]},
                                recs[it["id"]].get("參考音"))]
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
                                     recs[g["id"]].get("參考音")))]
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
    summ = wd.read_json(workdir / "輸出" / f"輸出摘要_{tag}.json", default=None) or {}
    bad = [m for m in methods if not ((summ.get("輸出") or {}).get(m, {}).get("驗證") or {}).get("通過")]
    if bad:
        return False, f"成品沒有通過驗證（{'、'.join(bad)}），要重新組裝"
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
    a = 0.0 if start is None else float(start)
    b = float(end) if end is not None else float(video_duration(workdir) or 0.0)
    if b <= a:
        raise ValueError("不知道影片多長，用 --end 指定到幾分幾秒")
    ctx = {"範圍": [a, b], "輸出做法": list(methods or default_methods()), "標記": tag_for(a, b)}
    runners_given = bool(runners)
    runners = {**_default_runners(), **(runners or {})}
    checks = {**_default_checks(), **(checks or {})}
    prog = {"開始時間": _now(), "結束時間": None, "範圍": ctx["範圍"], "輸出做法": ctx["輸出做法"],
            "步驟": {k: {"說明": desc, "狀態": "等待"} for k, desc in STEPS}, "錯誤": None}

    def save() -> None:
        wd.write_json(progress_path(workdir), prog)

    from bookclub.tts import StopRequested, check_stop

    _clear_stop(workdir)   # 上次按的停止不算這一次
    save()
    awake_off = (lambda: None) if runners_given else keep_awake(log)   # 測試用假步驟時不用
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
            except StopRequested as e:
                return _stopped(prog, st, e, save, log)
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
    finally:
        awake_off()


def _stopped(prog: dict, st: dict, e: Exception, save: Callable[[], None], log: Callable[[str], None]) -> dict:
    """按了停止：這一步標「停止」、整份標停止，不算失敗。"""
    st.update({"狀態": "停止", "結束": _now(), "訊息": str(e)})
    prog["停止"] = True
    prog["結束時間"] = _now()
    save()
    log(f"[AI 執行] {e}")
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
