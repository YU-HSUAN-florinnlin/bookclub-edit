"""流程第 1 步的第三個零件：找重疊、分辨說話者。

只在「認老師」那一步找出來的 scan_regions（學員或不確定的句子前後各
`overlap_scan_pad_s` 秒）跑 pyannote 完整的分辨說話者（speaker-diarization-3.1），
純老師講話的長區塊完全不進 pyannote——這是最花時間的一步，做法照
`spikes/diarize/diarize_test.py` 驗證過的搬過來：diarization → `get_overlap()`
找重疊 → 聲紋比對老師（沿用「認老師」那一步算出來的老師聲紋中心，不重算）。

自動過濾（02 規格 2026-09-20 定案）：
    1. 兩位學員之間的重疊 → 跳過（兩邊都會重念，不需要人決定老師那邊怎麼處理）
    2. 老師講話時學員的短附和（< `echo_overlap_max_s` 秒，且落在老師連續講話
       的區間內部）→ 跳過
被跳過的仍然列在輸出清單裡（`已自動跳過` 標成 true、附上原因），不是刪掉——
覆核網頁之後可以一鍵救回。

隱私：只處理聲音的時間、說話者編號、相似度，不轉文字、不輸出任何逐字稿內容。
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import soundfile as sf

from bookclub.config import load_settings
from bookclub.refpick import SIM_TEACHER, _classify, _l2norm, _merge_intervals, cosine, fmt_time
from bookclub.workdir import overlap_path, read_json, write_json

SR = 16000
MIN_SEG_S = 1.0        # 說話者聲紋只用 >= 這個長度的區間（跟 diarize_test.py 一致）
MAX_EMBED_S = 60.0     # 每個說話者最多接 60 秒去算聲紋
REGION_DIR_NAME = "重疊"  # 每個掃描區域的音檔／RTTM 快取放這裡，可續跑


def _region_dir(workdir: Path) -> Path:
    return workdir / REGION_DIR_NAME


def _load_pipeline():
    from pyannote.audio import Pipeline

    return Pipeline.from_pretrained("pyannote/speaker-diarization-3.1")


def _extract_region_audio(audio_path: Path, start: float, end: float, out_path: Path) -> None:
    with sf.SoundFile(str(audio_path)) as f:
        sr = f.samplerate
        f.seek(max(0, int(start * sr)))
        x = f.read(int(round((end - start) * sr)), dtype="float32")
    sf.write(str(out_path), x, sr)


def _diarize_region(pipeline, region_audio: Path, rttm_path: Path, uri: str):
    if rttm_path.exists():
        from pyannote.core import Annotation
        from pyannote.database.util import load_rttm

        # 這段完全沒偵測到任何說話者時，write_rttm 會寫出空檔案，load_rttm 回傳的
        # dict 就不會有這個 uri 的鍵——用空的 Annotation 頂替，不是錯誤。
        loaded = load_rttm(str(rttm_path))
        return loaded.get(uri, Annotation(uri=uri)), 0.0
    t0 = time.time()
    ann = pipeline(str(region_audio))
    ann.uri = uri
    with open(rttm_path, "w", encoding="utf-8") as f:
        ann.write_rttm(f)
    return ann, time.time() - t0


def _speaker_segments_for_embed(ann, label: str, max_end: float):
    """`max_end`：這個區域音檔實際的長度（秒）。RTTM／diarization 內部重取樣會有
    零點幾毫秒的浮點誤差，turn 的結尾偶爾會比音檔實際長度多個幾十毫秒，直接拿去
    `inference.crop` 會噴「超出檔案範圍」，所以先夾進 [0, max_end]。"""
    from pyannote.core import Segment

    segs = []
    for s in ann.label_timeline(label):
        clipped = Segment(max(0.0, s.start), min(s.end, max_end))
        if clipped.duration >= MIN_SEG_S:
            segs.append(clipped)
    segs.sort(key=lambda s: s.start)
    picked, total = [], 0.0
    for s in segs:
        remain = MAX_EMBED_S - total
        if remain <= 0:
            break
        if s.duration <= remain:
            picked.append(s)
            total += s.duration
        else:
            picked.append(Segment(s.start, s.start + remain))
            total += remain
            break
    return picked


def _label_roles(inference, region_audio: Path, ann, teacher_center: np.ndarray) -> dict:
    """回傳 {label: (role, sim)}，role 是 "老師"／"不是老師"／"不確定"
    （沿用 refpick._classify 的三段門檻：>=0.55 老師、<=0.35 不是老師、中間不確定）。"""
    region_dur = sf.info(str(region_audio)).duration
    roles = {}
    for label in ann.labels():
        segs = _speaker_segments_for_embed(ann, label, region_dur)
        if not segs:
            roles[label] = ("不確定", None)
            continue
        emb = _l2norm(inference.crop(str(region_audio), segs).reshape(-1))
        sim = cosine(emb, teacher_center)
        roles[label] = (_classify(sim), round(float(sim), 4))
    return roles


def _teacher_turn_contains(ann, teacher_label: str, start: float, end: float, eps: float = 0.05) -> bool:
    """老師在這個區域裡有沒有一段連續發言，完整涵蓋 [start, end]（含一點誤差容忍）。"""
    for turn in ann.label_timeline(teacher_label):
        if turn.start <= start + eps and turn.end >= end - eps:
            return True
    return False


def _decide_skip(ann, roles: dict, labels: list[str], ov_start: float, ov_end: float, echo_max_s: float):
    """套用 02 規格的兩條自動過濾規則，回傳 (已自動跳過: bool, 原因: str|None)。"""
    teacher_labels = [l for l in labels if roles[l][0] == "老師"]
    student_labels = [l for l in labels if roles[l][0] == "不是老師"]
    unknown_labels = [l for l in labels if roles[l][0] == "不確定"]

    if not teacher_labels and not unknown_labels and len(student_labels) >= 2:
        return True, "兩位學員之間的重疊"

    if (
        len(teacher_labels) == 1
        and student_labels
        and not unknown_labels
        and (ov_end - ov_start) < echo_max_s
        and _teacher_turn_contains(ann, teacher_labels[0], ov_start, ov_end)
    ):
        return True, "老師講話時學員短附和"

    return False, None


def find_overlaps(
    audio_path: Path,
    workdir: Path,
    scan_regions: list[list[float]],
    teacher_center: list[float] | np.ndarray,
) -> dict:
    """回傳 dict：
        overlaps: [{start, end, length, speakers:[{label, role, sim}], 已自動跳過, 原因}, ...]
        已自動跳過數, 重疊數（含跳過的）, 掃描區域數, 掃描總秒數, elapsed

    `workdir/重疊.json` 已存在就直接讀出來回傳；每個掃描區域另外快取
    RTTM（`workdir/重疊/區域NNNN.rttm`），中途中斷只補沒做完的區域。
    """
    workdir = Path(workdir)
    cache_path = overlap_path(workdir)
    cached = read_json(cache_path, default=None)
    if cached is not None:
        print("[3/找重疊] 已有 重疊.json，略過")
        return cached

    audio_path = Path(audio_path)
    teacher_center = np.asarray(teacher_center, dtype=np.float64)
    echo_max_s = load_settings().thresholds.echo_overlap_max_s

    merged_regions = _merge_intervals([(s, e) for s, e in scan_regions])
    region_dir = _region_dir(workdir)
    region_dir.mkdir(parents=True, exist_ok=True)

    scan_total = sum(e - s for s, e in merged_regions)
    print(f"[3/找重疊] {len(merged_regions)} 個掃描區域、共 {fmt_time(scan_total)}（{scan_total:.1f} 秒）")

    if not merged_regions:
        result = {
            "overlaps": [], "已自動跳過數": 0, "重疊數": 0,
            "掃描區域數": 0, "掃描總秒數": 0.0, "elapsed": 0.0,
        }
        write_json(cache_path, result)
        return result

    t_grand0 = time.time()
    pipeline = _load_pipeline()
    from bookclub.refpick import _load_embed_model

    inference = _load_embed_model()

    all_overlaps: list[dict] = []
    n_regions = len(merged_regions)

    for i, (r_start, r_end) in enumerate(merged_regions):
        uri = f"區域{i:04d}"
        region_audio = region_dir / f"{uri}.flac"
        rttm_path = region_dir / f"{uri}.rttm"

        if not region_audio.exists():
            _extract_region_audio(audio_path, r_start, r_end, region_audio)

        t0 = time.time()
        ann, diarize_elapsed = _diarize_region(pipeline, region_audio, rttm_path, uri)
        elapsed_msg = f"{diarize_elapsed:.1f} 秒" if diarize_elapsed else "（用快取）"
        print(f"[3/找重疊] 區域 {i + 1}/{n_regions}（{fmt_time(r_start)}–{fmt_time(r_end)}）"
              f"diarization {elapsed_msg}")

        overlap_tl = ann.get_overlap()
        if len(overlap_tl) == 0:
            continue

        roles = _label_roles(inference, region_audio, ann, teacher_center)

        for seg in overlap_tl:
            labels = sorted(ann.crop(seg).labels())
            ov_start, ov_end = r_start + seg.start, r_start + seg.end
            skipped, reason = _decide_skip(ann, roles, labels, seg.start, seg.end, echo_max_s)
            all_overlaps.append({
                "start": round(ov_start, 3),
                "end": round(ov_end, 3),
                "length": round(ov_end - ov_start, 3),
                "speakers": [
                    {"label": l, "role": roles[l][0], "sim": roles[l][1]} for l in labels
                ],
                "已自動跳過": skipped,
                "原因": reason,
                "區域": uri,
            })

    all_overlaps.sort(key=lambda o: o["start"])
    n_skipped = sum(1 for o in all_overlaps if o["已自動跳過"])
    elapsed = time.time() - t_grand0

    result = {
        "overlaps": all_overlaps,
        "重疊數": len(all_overlaps),
        "已自動跳過數": n_skipped,
        "掃描區域數": n_regions,
        "掃描總秒數": round(scan_total, 1),
        "elapsed": round(elapsed, 1),
    }
    write_json(cache_path, result)
    print(f"[3/找重疊] 完成：共 {len(all_overlaps)} 處重疊，自動跳過 {n_skipped} 處，"
          f"耗時 {elapsed:.1f} 秒")
    return result
