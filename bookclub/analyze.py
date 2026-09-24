"""串流程：`bookclub run analyze <影片> <工作區>`。

依序呼叫流程第 1 步的四個零件＋refpick：轉文字 → 認老師 → 找重疊 → 挑老師
參考音（呼叫 `bookclub/refpick.py`）→ 找名字。每個零件自己的快取檔案存在就
跳過，這支只是照順序呼叫、彙總結果；中途中斷重跑，已經做完的步驟不會重做。

最後寫 `workdir/分析結果.json`（彙整索引＋各步驟耗時）與 `workdir/報告.md`
（只放統計與時間，不放逐字稿或學員說的內容——內容留在各步驟自己的檔案裡）。
"""

from __future__ import annotations

import time
from pathlib import Path

from bookclub import names as names_mod
from bookclub import namespage as namespage_mod
from bookclub import overlap as overlap_mod
from bookclub import speakers as speakers_mod
from bookclub import transcribe as transcribe_mod
from bookclub.refpick import pick_reference
from bookclub.workdir import analysis_result_path, audio_path as _audio_path, ensure, fmt_time, report_path, write_json


def _roster_names(roster_path: str | Path | None) -> list[str] | None:
    """只取名字給 Groq 的 prompt 用（幫助辨識，不影響快取判斷）。"""
    if not roster_path:
        return None
    roster = names_mod.load_roster(Path(roster_path).expanduser())
    seen: list[str] = []
    for r in roster:
        if r["canonical"] not in seen:
            seen.append(r["canonical"])
    return seen or None


def run_analyze(
    video: str | Path,
    workdir: str | Path,
    roster_path: str | Path | None = None,
    sensitive_path: str | Path | None = None,
    exclusion_path: str | Path | None = None,
    skip_overlap: bool = False,
    ref_n: int = 5,
) -> dict:
    workdir = ensure(workdir)
    video = Path(video).expanduser()
    t_grand0 = time.time()
    elapsed: dict[str, float] = {}

    print("=" * 48)
    print(f"[分析一條龍] 影片：{video.name}")
    print(f"[分析一條龍] 工作區：{workdir}")
    print("=" * 48)

    # ---------- 1. 轉文字 ----------
    t0 = time.time()
    transcript = transcribe_mod.transcribe(video, workdir, roster_names=_roster_names(roster_path))
    elapsed["1_轉文字"] = round(time.time() - t0, 1)
    sentences = transcript["sentences"]
    words = transcript["words"]
    duration = transcript["duration"]
    print(f"[分析一條龍] 1/5 轉文字完成：{len(sentences)} 句、{len(words)} 個字、"
          f"影片長度 {fmt_time(duration)}")

    # ---------- 2. 認老師 ----------
    t0 = time.time()
    speaker_result = speakers_mod.classify_speakers(_audio_path(workdir), workdir, sentences)
    elapsed["2_認老師"] = round(time.time() - t0, 1)
    labeled_sentences = speaker_result["sentences"]
    scan_regions = speaker_result["scan_regions"]
    print(f"[分析一條龍] 2/5 認老師完成：老師群佔比 "
          f"{speaker_result['cluster_info']['老師群佔可比對總秒數比例']}")

    # ---------- 3. 找重疊 ----------
    if skip_overlap:
        print("[分析一條龍] 3/5 --skip-overlap，跳過找重疊")
        overlap_result = {
            "overlaps": [], "重疊數": 0, "已自動跳過數": 0,
            "掃描區域數": len(scan_regions), "掃描總秒數": 0.0, "elapsed": 0.0, "跳過": True,
        }
    else:
        t0 = time.time()
        overlap_result = overlap_mod.find_overlaps(
            _audio_path(workdir), workdir, scan_regions,
            speaker_result["cluster_info"]["老師聲紋中心"],
        )
        elapsed["3_找重疊"] = round(time.time() - t0, 1)
        print(f"[分析一條龍] 3/5 找重疊完成：{overlap_result['重疊數']} 處，"
              f"自動跳過 {overlap_result['已自動跳過數']} 處")

    # ---------- 4. 挑老師參考音（呼叫 refpick） ----------
    t0 = time.time()
    ref_record = pick_reference(video, workdir, n=ref_n)
    elapsed["4_挑參考音"] = round(time.time() - t0, 1)
    print(f"[分析一條龍] 4/5 挑參考音完成：{ref_record.get('候選數', 0)} 個候選")

    # ---------- 5. 找名字 ----------
    if roster_path:
        t0 = time.time()
        names_result = names_mod.find_names(
            _audio_path(workdir), workdir, labeled_sentences, words,
            Path(roster_path).expanduser(),
            Path(sensitive_path).expanduser() if sensitive_path else None,
            Path(exclusion_path).expanduser() if exclusion_path else None,
        )
        elapsed["5_找名字"] = round(time.time() - t0, 1)
        excluded_n = names_result.get("統計", {}).get("已自動排除數", 0)
        print(f"[分析一條龍] 5/5 找名字完成：{names_result['統計']['總筆數']} 筆候選"
              f"（另外自動排除 {excluded_n} 筆）")

        t0 = time.time()
        review_page_path = namespage_mod.build_review_page(workdir, names_result)
        elapsed["5b_名字覆核頁"] = round(time.time() - t0, 1)
        print(f"[分析一條龍] 5b/5 名字覆核頁：{review_page_path}")
    else:
        print("[分析一條龍] 5/5 沒給 --roster，跳過找名字")
        names_result = {"candidates": [], "統計": {"總筆數": 0}, "elapsed": 0.0, "跳過": True}

    total_elapsed = time.time() - t_grand0
    elapsed["總耗時"] = round(total_elapsed, 1)

    result = {
        "video": str(video),
        "workdir": str(workdir),
        "影片長度": round(duration, 1),
        "句數": len(sentences),
        "字數": len(words),
        "老師群佔可比對總秒數比例": speaker_result["cluster_info"]["老師群佔可比對總秒數比例"],
        "各群秒數": speaker_result["cluster_info"]["各群秒數"],
        "重疊掃描區域數": len(scan_regions),
        "重疊掃描總秒數": round(sum(e - s for s, e in scan_regions), 1),
        "重疊數": overlap_result.get("重疊數", 0),
        "重疊已自動跳過數": overlap_result.get("已自動跳過數", 0),
        "參考音候選數": ref_record.get("候選數", 0),
        "名字候選數": names_result["統計"].get("總筆數", 0),
        "名字候選統計": names_result["統計"],
        "elapsed": elapsed,
    }
    write_json(analysis_result_path(workdir), result)
    report_path(workdir).write_text(_build_report(result), encoding="utf-8")

    print("=" * 48)
    print(f"[分析一條龍] 全部完成，總耗時 {fmt_time(total_elapsed)}（{total_elapsed:.1f} 秒）")
    print(f"[分析一條龍] 分析結果：{analysis_result_path(workdir)}")
    print(f"[分析一條龍] 報告：{report_path(workdir)}")
    return result


def _build_report(result: dict) -> str:
    lines = ["# 讀書會剪輯｜分析一條龍 報告", ""]
    lines.append(f"- 影片：`{Path(result['video']).name}`")
    lines.append(f"- 影片長度：{fmt_time(result['影片長度'])}（{result['影片長度']:.0f} 秒）")
    lines.append(f"- 句數：{result['句數']}　字數：{result['字數']}")
    lines.append("")

    lines.append("## 各步驟耗時")
    lines.append("| 步驟 | 耗時（秒） |")
    lines.append("| --- | --- |")
    for k, v in result["elapsed"].items():
        if k in ("轉文字_各段",):
            continue
        lines.append(f"| {k} | {v} |")
    lines.append("")

    lines.append("## 認老師")
    lines.append(f"- 老師群佔可比對總秒數比例：{result['老師群佔可比對總秒數比例']}")
    groups = list(result["各群秒數"].items())
    top_n = 8
    lines.append(f"- 各群秒數（前 {min(top_n, len(groups))}／共 {len(groups)} 群，其餘多半是聲紋不穩定的雜訊小群）：")
    for cid, secs in groups[:top_n]:
        lines.append(f"  - 群 {cid}：{secs} 秒")
    if len(groups) > top_n:
        rest_total = sum(secs for _, secs in groups[top_n:])
        lines.append(f"  - （其餘 {len(groups) - top_n} 群，共 {rest_total:.1f} 秒）")
    lines.append("")

    lines.append("## 找重疊")
    lines.append(f"- 掃描區域：{result['重疊掃描區域數']} 段、共 "
                  f"{fmt_time(result['重疊掃描總秒數'])}（{result['重疊掃描總秒數']:.0f} 秒）")
    lines.append(f"- 重疊處數：{result['重疊數']}")
    lines.append(f"- 已自動跳過：{result['重疊已自動跳過數']}")
    lines.append("")

    lines.append("## 挑老師參考音")
    lines.append(f"- 候選數：{result['參考音候選數']}")
    lines.append("")

    lines.append("## 找名字")
    stats = result.get("名字候選統計", {})
    lines.append(f"- 候選總筆數：{result['名字候選數']}")
    if stats.get("已自動排除數") is not None:
        lines.append(f"- 已自動排除（命中排除清單）：{stats['已自動排除數']} 筆")
    if stats.get("各層級筆數"):
        lines.append(f"- 讀音比對層級分布：{stats['各層級筆數']}")
    if stats.get("信心分布"):
        lines.append(f"- 信心分布：{stats['信心分布']}")
    if stats.get("各建議做法筆數"):
        lines.append(f"- 建議做法分布：{stats['各建議做法筆數']}")
    if stats.get("切點信心分布"):
        lines.append(f"- 切點信心分布：{stats['切點信心分布']}")
    lines.append("")

    return "\n".join(lines) + "\n"
