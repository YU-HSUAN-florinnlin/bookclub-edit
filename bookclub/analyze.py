"""串流程：`bookclub run analyze <影片> <工作區>`。

依序（09-25 宇軒重排）：
1. 轉文字
2. 段落分析的文字部分（Claude，雲端，背景執行緒）‖ 認老師（聲紋，本機），同時跑
3. 合併判斷：冥想引導、導讀段落裡聲紋判成非老師的句子改成老師（原判斷留在 `聲紋判斷`），重算掃描範圍
4. 找重疊（只掃修正後的範圍）
5. 挑老師參考音（`bookclub/refpick.py`；重疊處、冥想導讀段落都排除）
6. 找名字（用修正後的判斷）
7. 段落分析的聲紋部分（學員整段認人）→ `校對/段落.json`（已存在就不覆蓋）
Claude 叫不到時印警告、退回純聲紋判斷繼續跑。每個零件自己的快取檔案存在就
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
from bookclub.workdir import (
    analysis_result_path, audio_path as _audio_path, ensure, fmt_time, overlap_path, read_json, report_path, write_json,
)


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


def skipped_overlap_result(workdir: Path, scan_regions: list) -> dict:
    """`--skip-overlap` 時的重疊結果：已有 `重疊.json` 就沿用（不重新掃描）。

    09-26 查「第一堂重疊 0 筆」：09-24 為了重跑找名字加了 --skip-overlap，舊版這裡直接回傳 0 筆，
    把 `分析結果.json` 的重疊數蓋成 0；其實 09-23 掃出來的 `重疊.json` 一直都在（27 筆）。"""
    cached = read_json(overlap_path(workdir), default=None)
    if cached is not None:
        print("[分析一條龍] 4/7 --skip-overlap：沿用已有的 重疊.json（不重新掃描）")
        return overlap_mod.apply_simple_filters(cached)
    print("[分析一條龍] 4/7 --skip-overlap，跳過找重疊")
    return {
        "overlaps": [], "重疊數": 0, "已自動跳過數": 0,
        "掃描區域數": len(scan_regions), "掃描總秒數": 0.0, "elapsed": 0.0, "跳過": True,
    }


def run_analyze(
    video: str | Path,
    workdir: str | Path,
    roster_path: str | Path | None = None,
    sensitive_path: str | Path | None = None,
    exclusion_path: str | Path | None = None,
    skip_overlap: bool = False,
    ref_n: int = 5,
    skip_turns: bool = False,
    turns_model: str | None = None,
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
    print(f"[分析一條龍] 1/7 轉文字完成：{len(sentences)} 句、{len(words)} 個字、"
          f"影片長度 {fmt_time(duration)}")

    # ---------- 2. 段落分析的文字部分（Claude，雲端）‖ 認老師（聲紋，本機），同時跑 ----------
    # 09-25 宇軒：段落分析以文字為主，冥想引導、導讀整段算老師；提前到這裡，讓後面找重疊、
    # 挑參考音、找名字都用修正後的判斷。Claude 在背景執行緒跑，主執行緒跑認老師。
    from concurrent.futures import ThreadPoolExecutor

    from bookclub import turns as turns_mod

    t0 = time.time()
    text_future = None
    cut_future = None
    pool = None
    if not skip_turns:
        pool = ThreadPoolExecutor(max_workers=2)

        def _text_job():
            t = time.time()
            data = turns_mod.get_text_turns(workdir, sentences, model=turns_model)
            return data, time.time() - t

        def _cut_job():   # 09-26：建議刪除段落，另一個 Claude 呼叫，跟段落分析同時跑
            from bookclub import cutsuggest

            t = time.time()
            data = cutsuggest.suggest_cuts(workdir, sentences, duration=duration, video=video, model=turns_model)
            return data, time.time() - t

        text_future = pool.submit(_text_job)
        cut_future = pool.submit(_cut_job)
    t_sp = time.time()
    speaker_result = speakers_mod.classify_speakers(_audio_path(workdir), workdir, sentences)
    elapsed["2b_認老師"] = round(time.time() - t_sp, 1)

    text_data = None
    if text_future is not None:
        try:
            text_data, text_s = text_future.result()
            elapsed["2a_段落文字_Claude"] = round(text_s, 1)
        except Exception as exc:  # Claude 叫不到、額度用完：退回純聲紋判斷，其他分析照常
            print(f"⚠️ [分析一條龍] 段落分析（Claude）失敗：{exc}")
            print("   → 這次先用純聲紋判斷繼續跑；修好之後重跑 bookclub run analyze（已完成的步驟會沿用），"
                  "或單獨跑 bookclub run turns <工作區>（先用 bookclub doctor --claude 確認叫得到 Claude）")
    elapsed["2_段落文字與認老師"] = round(time.time() - t0, 1)
    labeled_sentences = speaker_result["sentences"]
    scan_regions = speaker_result["scan_regions"]
    print(f"[分析一條龍] 2/7 認老師完成：老師群佔比 "
          f"{speaker_result['cluster_info']['老師群佔可比對總秒數比例']}")

    # ---------- 3. 合併判斷：冥想引導、導讀段落裡聲紋判成非老師的句子，改成老師 ----------
    correction = None
    calm_regions: list[list[float]] = []
    voice_scan_total = round(sum(e - s for s, e in speakers_mod.scan_regions_for(
        labeled_sentences, speaker_result["scan_pad_s"], voice_only=True)), 1)
    if text_data is not None:
        t0 = time.time()
        correction = turns_mod.correct_labels(labeled_sentences, text_data)
        calm_regions = correction.pop("冥想導讀區域")
        scan_regions = speakers_mod.scan_regions_for(labeled_sentences, speaker_result["scan_pad_s"])
        speakers_mod.save_corrected(workdir, labeled_sentences, scan_regions, speaker_result["scan_pad_s"],
                                    {**correction, "只看聲紋的掃描秒數": voice_scan_total})
        elapsed["3_合併判斷"] = round(time.time() - t0, 1)
        print(f"[分析一條龍] 3/7 合併判斷：{correction['改成老師句數']} 句（{correction['改成老師秒數']} 秒）"
              f"落在冥想引導／導讀段落，改成老師；找重疊的掃描範圍 {voice_scan_total:.0f} → "
              f"{sum(e - s for s, e in scan_regions):.0f} 秒")
    else:
        print("[分析一條龍] 3/7 沒有段落分析結果，跳過合併判斷（用純聲紋判斷）")

    # ---------- 4. 找重疊 ----------
    if skip_overlap:
        overlap_result = skipped_overlap_result(workdir, scan_regions)
    else:
        t0 = time.time()
        overlap_result = overlap_mod.find_overlaps(
            _audio_path(workdir), workdir, scan_regions,
            speaker_result["cluster_info"]["老師聲紋中心"],
        )
        elapsed["4_找重疊"] = round(time.time() - t0, 1)
        print(f"[分析一條龍] 4/7 找重疊完成：{overlap_result['重疊數']} 處，"
              f"自動跳過 {overlap_result['已自動跳過數']} 處")

    # ---------- 5. 挑老師參考音（呼叫 refpick） ----------
    # 排除區域：重疊處（02 規格第七節）＋冥想引導、導讀段落（語氣不適合當參考音；
    # 這些句子現在是「老師」了，要明確加進排除區域）
    ref_exclude = [(o["start"], o["end"]) for o in overlap_result.get("overlaps", [])]
    ref_exclude += [tuple(r) for r in calm_regions]
    t0 = time.time()
    ref_record = pick_reference(video, workdir, n=ref_n, exclude_regions=ref_exclude)
    elapsed["5_挑參考音"] = round(time.time() - t0, 1)
    print(f"[分析一條龍] 5/7 挑參考音完成：{ref_record.get('候選數', 0)} 個候選")

    # ---------- 6. 找名字 ----------
    if roster_path:
        t0 = time.time()
        names_result = names_mod.find_names(
            _audio_path(workdir), workdir, labeled_sentences, words,
            Path(roster_path).expanduser(),
            Path(sensitive_path).expanduser() if sensitive_path else None,
            Path(exclusion_path).expanduser() if exclusion_path else None,
        )
        elapsed["6_找名字"] = round(time.time() - t0, 1)
        excluded_n = names_result.get("統計", {}).get("已自動排除數", 0)
        print(f"[分析一條龍] 6/7 找名字完成：{names_result['統計']['總筆數']} 筆候選"
              f"（另外自動排除 {excluded_n} 筆）")

        t0 = time.time()
        review_page_path = namespage_mod.build_review_page(workdir, names_result)
        elapsed["6b_名字覆核頁"] = round(time.time() - t0, 1)
        print(f"[分析一條龍] 6b/7 名字覆核頁：{review_page_path}")
    else:
        print("[分析一條龍] 6/7 沒給 --roster，跳過找名字")
        names_result = {"candidates": [], "統計": {"總筆數": 0}, "elapsed": 0.0, "跳過": True}

    # ---------- 7. 段落分析的聲紋部分（學員整段認人）→ 校對/段落.json ----------
    turns_stats = None
    if skip_turns:
        print("[分析一條龍] 7/7 跳過段落分析（--skip-turns）")
    elif turns_mod.turns_path(workdir).exists():
        print("[分析一條龍] 7/7 段落分析已經有結果，沿用（不覆蓋已經確認過的段落）")
    elif text_data is None:
        print("[分析一條龍] 7/7 沒有段落分析的文字結果（Claude 失敗），跳過學員認人")
    else:
        t0 = time.time()
        try:
            turns_stats = turns_mod.build_turns(workdir, roster_path=roster_path, text=text_data)["統計"]
            elapsed["7_段落聲紋"] = round(time.time() - t0, 1)
            print(f"[分析一條龍] 7/7 段落分析完成：{turns_stats['段落數']} 段、學員 {turns_stats['學員人數']} 位")
        except Exception as exc:
            print(f"⚠️ [分析一條龍] 7/7 段落分析（聲紋部分）失敗：{exc}")
            print("   → 修好之後單獨重跑：bookclub run turns <工作區>")

    # 建議刪除段落在背景跑（Claude＋候選附近的畫面檢查），不擋前面的步驟，最後才收
    cut_data = None
    if cut_future is not None:
        try:
            cut_data, cut_s = cut_future.result()
            elapsed["2c_刪除建議"] = round(cut_s, 1)
            print(f"[分析一條龍] 建議刪除段落：{len(cut_data.get('建議', []))} 筆")
        except Exception as exc:  # Claude 失敗就沒有建議，不擋其他步驟
            print(f"⚠️ [分析一條龍] 建議刪除段落（Claude）失敗：{exc}；這次沒有建議，之後可以單獨跑 bookclub run cuts <工作區>")
    if pool is not None:
        pool.shutdown(wait=False)

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
        "合併判斷": correction,
        "只看聲紋的掃描秒數": voice_scan_total,
        "重疊掃描區域數": len(scan_regions),
        "重疊掃描總秒數": round(sum(e - s for s, e in scan_regions), 1),
        "重疊數": overlap_result.get("重疊數", 0),
        "重疊已自動跳過數": overlap_result.get("已自動跳過數", 0),
        "參考音候選數": ref_record.get("候選數", 0),
        "名字候選數": names_result["統計"].get("總筆數", 0),
        "名字候選統計": names_result["統計"],
        "段落統計": turns_stats,
        "刪除建議數": len((cut_data or {}).get("建議", [])) if cut_data is not None else None,
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

    lines.append("## 合併判斷（冥想引導、導讀段落算老師）")
    c = result.get("合併判斷")
    if c:
        lines.append(f"- 改成老師：{c['改成老師句數']} 句、{c['改成老師秒數']} 秒"
                     f"（冥想導讀 {c['冥想導讀段數']} 段、共 {c['冥想導讀秒數']} 秒）")
    else:
        lines.append("- 沒有段落分析結果，這次用純聲紋判斷")
    lines.append(f"- 找重疊的掃描範圍：只看聲紋 {result.get('只看聲紋的掃描秒數', 0):.0f} 秒 → "
                 f"修正後 {result['重疊掃描總秒數']:.0f} 秒")
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
