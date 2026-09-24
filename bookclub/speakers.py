"""流程第 1 步的第二個零件：認老師（輕量判斷，不需要老師自錄的聲音）。

逐句抽聲紋 → 分群 → 講最多話的那群＝老師。做法完全沿用
`bookclub/refpick.py` 步驟 3 已經驗證過的邏輯，直接呼叫同一個函式、共用同一份
快取 `workdir/說話者判斷.json`——`bookclub run analyze` 之後呼叫「挑老師參考音」
（`refpick.pick_reference`）時會直接吃到這裡留下的快取，不會重算一次。

這支模組多做一件 refpick 步驟 3 沒做的事：算出「學員或不確定的句子，前後各
`overlap_scan_pad_s` 秒、合併重疊後」的範圍，交給下一步「找重疊」只在這些
範圍跑 pyannote（純老師講話的長區塊完全不進 pyannote，省下大半時間）。
"""

from __future__ import annotations

import time
from pathlib import Path

from bookclub.config import overlap_scan_pad_s as _default_pad_s
from bookclub.refpick import _exclude_regions_from_sentences, _step3_speaker_classify, audio_dur_s
from bookclub.workdir import read_json, speakers_path, write_json


def classify_speakers(
    audio_path: Path,
    workdir: Path,
    sentences: list[dict],
    pad_s: float | None = None,
) -> dict:
    """回傳 dict：
        sentences:    附上 sim/label 的句子清單（跟 refpick 步驟 3 完全同格式）
        cluster_info: 分群資訊、老師聲紋中心（跟 refpick 步驟 3 完全同格式）
        scan_regions: [[start, end], ...] 學員／不確定句子前後各 pad_s 秒、合併
                      重疊後的區域——下一步「找重疊」只在這些範圍跑 pyannote
        scan_pad_s:   實際用的 pad 秒數
        elapsed:      這次呼叫花的秒數（吃到快取是 0）

    pad_s 不給的話從 `~/讀書會剪輯資料/settings.toml` 的 `overlap.scan_pad_s`
    讀（預設 3 秒，對應 02 規格的 overlap_scan_pad_s）。
    """
    if pad_s is None:
        pad_s = _default_pad_s()
    workdir = Path(workdir)
    audio_path = Path(audio_path)

    t0 = time.time()
    labeled_sentences, cluster_info, elapsed = _step3_speaker_classify(audio_path, workdir, sentences)
    scan_regions = [list(iv) for iv in _exclude_regions_from_sentences(labeled_sentences, pad_s=pad_s)]

    # refpick 自己的快取檔不含 scan_regions；如果這次是吃到既有快取（例如從
    # refpick 或前一次跑的工作區複製過來的），這裡補寫回去，檔案內容才完整。
    # scan_regions 只需要標好 label 的句子就能算，純 Python、很快，不特別快取。
    cache_path = speakers_path(workdir)
    cached = read_json(cache_path, default=None)
    if cached is not None and cached.get("scan_regions") != scan_regions:
        cached["scan_regions"] = scan_regions
        cached["scan_pad_s"] = pad_s
        write_json(cache_path, cached)

    total_dur = audio_dur_s(Path(audio_path))
    scan_total = sum(e - s for s, e in scan_regions)
    pct = f"（佔全片 {scan_total / total_dur * 100:.1f}%）" if total_dur else ""
    print(f"[2/認老師] 老師群佔可比對總秒數比例 {cluster_info['老師群佔可比對總秒數比例']}，"
          f"耗時 {elapsed:.1f} 秒" if elapsed else "[2/認老師] 已有快取，略過")
    print(f"[2/認老師] 需要掃重疊的範圍：{len(scan_regions)} 段、共 {scan_total:.1f} 秒{pct}")

    return {
        "sentences": labeled_sentences,
        "cluster_info": cluster_info,
        "scan_regions": scan_regions,
        "scan_pad_s": pad_s,
        "elapsed": round(elapsed, 1),
    }
