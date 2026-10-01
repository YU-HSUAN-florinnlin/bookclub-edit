"""覆核結果匯出／匯入（09-25 宇軒：夥伴的電腦最強，AI 執行由他跑）。

`bookclub review export <工作區> [--out 檔案]`（網頁第 3 步也有「匯出覆核結果」按鈕）產出一個 zip：
- `覆核結果.json`：格式版本、工具版本、影片（檔名／長度／大小，對檔用）、學員與英文名、保留原聲設定、
  學員段落（時間、說話者、英文名、校對稿、已確認、問老師）、名字處理（每筆的時間、做法、代號、整句的時間
  與代號後的文字，加上排好的處理計畫）、重疊決定、刪除段落、局部消音
- `ref.wav`、`ref.txt`（老師參考音）、`發音對照表.csv`（有才放）
- `給夥伴的說明.txt`
不放 Groq 的原始逐字稿（`原文`）、名字候選的本名與整句原文；盡量只放代號後的文字。
學員段落的校對稿、重疊的文字裡如果還有名冊上的本名（還沒校對完），匯出時列出是哪幾筆提醒。

`bookclub review import <zip> <新工作區> --video <影片>`：建工作區、比對影片長度、從影片抽 `audio.flac`、
放好檔案，讓 `bookclub gen names`、`bookclub render audio` 直接能跑。
"""

from __future__ import annotations

import csv
import json
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

from bookclub import workdir as wd

FORMAT_VERSION = 1
RESULT_NAME = "覆核結果.json"
README_NAME = "給夥伴的說明.txt"
PRON_NAME = "發音對照表.csv"
DURATION_TOLERANCE_S = 1.0


def tool_version() -> str:
    from bookclub import __version__   # 跟 pyproject.toml 的 version 一起改

    return __version__


def _roster_gender() -> dict[str, str]:
    """名冊的英文代號 → 性別（匿名聲線男生換男生、女生換女生）。"""
    from bookclub.config import data_dir

    path = data_dir() / "名冊.csv"
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {(r.get("英文代號") or "").strip(): (r.get("性別") or "").strip()
                for r in csv.DictReader(f) if (r.get("英文代號") or "").strip()}


def build_result(workdir: str | Path, video: str | Path | None = None) -> dict:
    """組 `覆核結果.json` 的內容（純讀檔，不寫檔）。"""
    from bookclub import nameplan, review
    from bookclub import turns as turns_mod

    workdir = Path(workdir).expanduser()
    page = review.page_data(workdir, video=video)
    vp = review.video_path(workdir, video)
    dec = review.load_decisions(workdir)
    tdata = wd.read_json(turns_mod.turns_path(workdir), default={}) or {}
    people = tdata.get("學員", {})
    gender = _roster_gender()
    code_of = {k: v.get("代號") for k, v in people.items()}

    students = {k: {"代號": v.get("代號"), "性別": gender.get(v.get("代號") or "", ""),
                    "聲音": dec["學員聲音"].get(k, "重新生成"), "秒數": v.get("秒數"), "段數": v.get("段數")}
                for k, v in people.items()}
    segments = [{"id": t["id"], "start": t["start"], "end": t["end"], "說話者": t["說話者"],
                 "代號": code_of.get(t["說話者"]), "聲音": dec["學員聲音"].get(t["說話者"], "重新生成"),
                 "校對稿": t.get("校對稿", ""), "已確認": bool(t.get("已確認")), "問老師": bool(t.get("問老師")),
                 "問老師備註": t.get("問老師備註", "")}
                for t in tdata.get("段落", []) if t.get("說話者") != "老師"]

    names_src = wd.read_json(wd.names_path(workdir), default=None)
    plan = nameplan.compute_plan(workdir, names_src) if names_src else {"生成": [], "消音": [], "略過": [], "要人處理": []}
    gen_of = {c: g for g in plan["生成"] for c in g["候選"]}
    mute_of = {m["候選"]: m for m in plan["消音"]}
    skip_of = {s["候選"]: s["原因"] for s in plan["略過"]}
    manual_of = {m["候選"]: m["原因"] for m in plan["要人處理"]}
    names = []
    for it in (x for x in page["項目"] if x["類型"] == "名字"):
        i = int(it["id"]) if str(it["id"]).isdigit() else it["id"]   # 10-01 走查：人工新增的名字 id 是 NM001，以前這裡整個匯出失敗
        row = {"候選": i, "start": it["start"], "end": it["end"], "做法": it["做法"], "代號": it["代號"],
               "標記": it["tags"], "備註": it["note"], "已確認": it["已確認"]}
        if i in skip_of:
            row["結果"] = f"略過（{skip_of[i]}）"
        elif i in manual_of:
            row["結果"] = f"要人處理（{manual_of[i]}）"
        elif i in mute_of:
            row["結果"] = "消音"
            row["消音起訖"] = [mute_of[i]["start"], mute_of[i]["end"]]
        elif i in gen_of:
            g = gen_of[i]
            row["結果"] = "生成"
            row["生成"] = {"id": g["id"], "起訖": g["slot"], "文字": g["text"]}
        names.append(row)

    overlaps = [{k: it.get(k) for k in ("id", "start", "end", "length", "做法", "排法", "老師文字", "學員文字",
                                        "學員說話者", "備註", "已確認")}
                | {"代號": code_of.get(it.get("學員說話者"))}
                for it in page["項目"] if it["類型"] == "重疊"]
    words = review.roster_words()
    real_names = [f"學員段落 {x['id']}" for x in segments if review.has_real_name(x["校對稿"], words)] + \
        [f"重疊 {x['id']}" for x in overlaps
         if review.has_real_name(x.get("老師文字"), words) or review.has_real_name(x.get("學員文字"), words)]
    # 10-01 走查：跟第 3 步上面的「幾／幾筆」同一個算法（被別筆涵蓋的算處理好、還缺東西的不算）
    pending = [it for it in page["項目"]
               if not ((it.get("已確認") or it.get("不用處理") or it.get("涵蓋")) and not it.get("還缺"))]
    by_type: dict[str, int] = {}
    for it in pending:
        by_type[it["類型"]] = by_type.get(it["類型"], 0) + 1

    return {
        "格式版本": FORMAT_VERSION,
        "工具版本": tool_version(),
        "匯出時間": datetime.now().isoformat(timespec="seconds"),
        "影片": {"檔名": vp.name if vp else page["影片"]["檔名"], "長度": page["影片"]["長度"],
                 "大小": vp.stat().st_size if vp else None},
        "學員": students,
        "學員段落": segments,
        "名字處理": names,
        "名字處理計畫": plan,
        "重疊": overlaps,
        "刪除段落": [c for c in dec["刪除段落"]],
        "局部消音": [m for m in dec["局部消音"]],
        "老師聲紋中心": ((wd.read_json(wd.speakers_path(workdir), default={}) or {})
                       .get("cluster_info", {}).get("老師聲紋中心")),
        "還有本名的地方": real_names,
        "未確認數": len(pending),
        "未確認各類": by_type,
        "統計": page["進度"],
    }


def _readme(result: dict) -> str:
    v = result["影片"]
    pending = result["未確認數"]
    lines = [
        "讀書會剪輯｜覆核結果（給夥伴）",
        "",
        f"影片：{v['檔名']}（長度 {wd.fmt_time(v['長度'] or 0)}，大小 {v['大小'] or '不明'} bytes）",
        f"匯出時間：{result['匯出時間']}　工具版本：{result['工具版本']}",
        f"還沒確認：{pending} 筆" + (f"（{'、'.join(f'{k} {n}' for k, n in result['未確認各類'].items())}）" if pending else ""),
        "",
        "怎麼用：",
        "1. 把這個 zip 跟原片放在你的電腦上（原片要跟上面是同一支，匯入時會比對長度）",
        "2. 匯入：bookclub review import <這個 zip> <新的工作區資料夾> --video <原片>",
        "3. 老師提到名字的地方，用老師的 AI 聲音生成：bookclub gen names <新的工作區資料夾>",
        "4. 組出新聲音軌與處理前後試聽：bookclub render audio <新的工作區資料夾>",
        "",
        "學員段落的匿名聲線生成還沒做；學員段落、重疊、刪除段落、局部消音的決定都已經在 覆核結果.json 裡，",
        "之後的版本直接讀這份。",
        "",
        "這個 zip 不含原始逐字稿與學員本名，只有代號後的文字。",
    ]
    return "\n".join(lines) + "\n"


def export_review(workdir: str | Path, out: str | Path | None = None, video: str | Path | None = None) -> dict:
    """寫出 zip，回傳 {檔案, 未確認數, 統計}。"""
    from bookclub.tts import pron_table_path

    workdir = Path(workdir).expanduser()
    result = build_result(workdir, video=video)
    if out is None:
        stem = Path(result["影片"]["檔名"] or "影片").stem
        out = workdir / "匯出" / f"覆核結果_{stem}_{datetime.now().strftime('%Y%m%d-%H%M')}.zip"
    out = Path(out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    ref = wd.ref_dir(workdir)
    files = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(RESULT_NAME, json.dumps(result, ensure_ascii=False, indent=1))
        z.writestr(README_NAME, _readme(result))
        for name in ("ref.wav", "ref.txt"):
            if (ref / name).is_file():
                z.write(ref / name, name)
                files.append(name)
        pron = workdir / PRON_NAME if (workdir / PRON_NAME).is_file() else pron_table_path()
        if pron.is_file():
            z.write(pron, PRON_NAME)
            files.append(PRON_NAME)
    missing_ref = [n for n in ("ref.wav", "ref.txt") if n not in files]
    if missing_ref:
        print(f"⚠️ [匯出] 還沒選定老師參考音（缺 {'、'.join(missing_ref)}）：先在第 2 步選好，不然夥伴沒辦法生成老師的聲音")
    if result["未確認數"]:
        print(f"⚠️ [匯出] 還有 {result['未確認數']} 筆沒確認：{result['未確認各類']}")
    if result["還有本名的地方"]:
        print(f"⚠️ [匯出] 還有 {len(result['還有本名的地方'])} 處文字裡有名冊上的本名，還沒換成代號："
              f"{'、'.join(result['還有本名的地方'][:10])}")
    print(f"[匯出] {out}")
    return {"檔案": str(out), "未確認數": result["未確認數"], "未確認各類": result["未確認各類"],
            "還有本名的地方": result["還有本名的地方"],
            "內含": [RESULT_NAME, README_NAME, *files], "缺參考音": missing_ref}


def probe_duration(video: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def import_review(zip_path: str | Path, workdir: str | Path, video: str | Path, force: bool = False) -> dict:
    """建新工作區、放好檔案、從影片抽 audio.flac。影片長度對不上（差超過 1 秒）就停下來，除非 force。"""
    zip_path, workdir, video = (Path(x).expanduser() for x in (zip_path, workdir, video))
    if not video.is_file():
        raise FileNotFoundError(f"找不到影片：{video}")
    target = workdir / "覆核" / RESULT_NAME
    if target.exists():
        raise FileExistsError(f"{workdir} 已經匯入過覆核結果；換一個新的資料夾，避免蓋掉之前的檔案")
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        if RESULT_NAME not in names:
            raise ValueError(f"{zip_path.name} 裡沒有 {RESULT_NAME}，不是覆核結果的匯出檔")
        result = json.loads(z.read(RESULT_NAME).decode("utf-8"))
        if result.get("格式版本", 0) > FORMAT_VERSION:
            raise ValueError(f"這份覆核結果是新版工具匯出的（格式版本 {result['格式版本']}），請先更新工具")
        want = float(result["影片"]["長度"] or 0)
        got = probe_duration(video)
        warnings = []
        if want and abs(got - want) > DURATION_TOLERANCE_S:
            msg = f"影片長度對不上：覆核結果是 {want:.1f} 秒，這支影片是 {got:.1f} 秒——可能不是同一支影片"
            if not force:
                raise ValueError(msg + "（確定沒問題就加 --force）")
            warnings.append(msg)
        size = result["影片"].get("大小")
        if size and video.stat().st_size != size:
            warnings.append(f"影片檔案大小不同（{video.stat().st_size} vs {size} bytes）；長度一樣，多半是轉檔過，可以繼續")

        workdir.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(z.read(RESULT_NAME))
        for name, dest in (("ref.wav", wd.ref_dir(workdir) / "ref.wav"), ("ref.txt", wd.ref_dir(workdir) / "ref.txt"),
                           (PRON_NAME, workdir / PRON_NAME), (README_NAME, workdir / README_NAME)):
            if name in names:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(name))
    if result.get("老師聲紋中心"):
        wd.write_json(wd.speakers_path(workdir), {"sentences": [], "cluster_info": {"老師聲紋中心": result["老師聲紋中心"]},
                                                 "匯入": True})
    wd.write_json(wd.analysis_result_path(workdir), {"video": str(video), "workdir": str(workdir),
                                                     "影片長度": round(got, 1), "匯入自": zip_path.name,
                                                     "匯入時間": datetime.now().isoformat(timespec="seconds")})
    audio = wd.audio_path(workdir)
    if not audio.exists():
        print(f"[匯入] 從影片抽聲音（16kHz 單聲道）：{video.name}")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-ar", "16000", "-ac", "1", "-vn",
                        str(audio)], check=True)
    for w in warnings:
        print(f"⚠️ [匯入] {w}")
    plan = result.get("名字處理計畫") or {}
    print(f"[匯入] 完成：{workdir}")
    print(f"[匯入] 名字：生成 {len(plan.get('生成', []))} 段、消音 {len(plan.get('消音', []))} 段；"
          f"學員段落 {len(result.get('學員段落', []))} 段；還沒確認 {result.get('未確認數', 0)} 筆")
    print(f"[匯入] 下一步：bookclub gen names {workdir}　→　bookclub render audio {workdir}")
    return {"工作區": str(workdir), "警告": warnings, "影片長度": got}
