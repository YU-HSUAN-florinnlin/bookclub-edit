"""流程第 1 步的第三個零件：找重疊、分辨說話者。

只在「認老師」那一步找出來的 scan_regions（學員或不確定的句子前後各
`overlap_scan_pad_s` 秒）跑 pyannote 完整的分辨說話者（speaker-diarization-3.1），
純老師講話的長區塊完全不進 pyannote——這是最花時間的一步，做法照
`spikes/diarize/diarize_test.py` 驗證過的搬過來：diarization → `get_overlap()`
找重疊 → 聲紋比對老師（沿用「認老師」那一步算出來的老師聲紋中心，不重算）。

自動過濾（02 規格 2026-09-20 定案；第 3 條 09-26 加）：
    1. 兩位學員之間的重疊 → 跳過（兩邊都會重念，不需要人決定老師那邊怎麼處理）
    2. 老師講話時學員的短附和（< `echo_overlap_max_s` 秒，且落在老師連續講話
       的區間內部）→ 跳過
    3. 重疊不到 0.05 秒（分辨說話者的邊界誤差，`apply_simple_filters`，讀快取時也會套用）→ 跳過
    4. （10-04 #111）落在會整段重念的學員段落裡、兩邊都沒有老師 → 自動算處理好。要看第 3 步的段落與學員聲音設定，
       不寫進 重疊.json，讀的時候在記憶體裡套（`student_turn_home`、`review.mark_student_turn_overlaps`）
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
from bookclub.workdir import fingerprint, overlap_path, read_json, write_json

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


MIN_OVERLAP_S = 0.05   # 短於這個長度的重疊是分辨說話者的邊界誤差（09-26 第一堂有 6 筆 0.0 秒），自動跳過
ZERO_REASON = "重疊不到 0.05 秒（邊界誤差）"


def apply_simple_filters(result: dict) -> dict:
    """不需要重跑 pyannote 的過濾規則，套在新算的或讀快取的結果上都行（重複套用結果一樣）。
    目前一條：長度不到 MIN_OVERLAP_S 的重疊自動跳過。套完重算統計數字。"""
    for o in result.get("overlaps", []):
        if not o.get("已自動跳過") and o.get("length", 0) < MIN_OVERLAP_S:
            o["已自動跳過"] = True
            o["原因"] = ZERO_REASON
    ovs = result.get("overlaps", [])
    result["重疊數"] = len(ovs)
    result["已自動跳過數"] = sum(1 for o in ovs if o.get("已自動跳過"))
    result["要人決定數"] = result["重疊數"] - result["已自動跳過數"]
    return result


# 10-04 #111（宇軒定做法 A）：重疊落在「會整段重念的學員段落」裡、兩邊都沒有老師 → 不出卡、自動算處理好。
# 這條要看段落與學員聲音設定（第 3 步才有），所以不寫進 重疊.json，每次讀的時候在記憶體裡套（`review.mark_student_turn_overlaps`）。
STUDENT_TURN_REASON = "落在會整段重念的學員段落裡、兩邊都沒有老師（跟著那一段整段重念）"
STUDENT_TURN_TAG = "學員段落"   # 重疊上 `自動處理` 欄位的值（inspect 印得出來的固定說法）
STUDENT_TURN_TOL_S = 0.05       # 重疊頭尾超出學員段落（句子範圍）這麼多秒以內還算在裡面


def student_turn_home(o: dict, turns: list[dict], sents_by_id: dict[str, dict], kept: set) -> dict | None:
    """這一處重疊是不是「會整段重念的學員段落」裡、兩邊都沒有老師的重疊（純函式）。是的話回傳那一段，不是回傳 None。

    - 兩邊都沒有老師：分辨說話者有標出角色（`speakers` 不是空的；人工補的重疊沒有角色，不算），而且沒有一邊是「老師」
      （「不是老師」「不確定」都算沒有老師）
    - 會整段重念的學員段落：說話者是學員（不是老師、不是空的）、這位學員不是「保留原聲」（`kept`）；
      重疊整個落在這一段的句子範圍裡（`turns.turn_sentences`，頭尾夾在段落起訖裡，跟 `students.build_items` 排時間格用的是同一份；
      時間格從第一句排到最後一句，中間只有剪掉的地方會斷開，斷開的那一段本來就剪掉了）。
      手動標的段落沒有句子時，照段落起訖、校對稿不是空的才算（空的不會生成）。

    已知風險（宇軒 10-04 知道）：那一小段其實是老師插話、但分辨說話者沒認出是老師（標成不確定或不是老師）時，
    會跟著學員那一段整段重念被蓋掉。總檢查「請看一眼」列出每一處讓人聽；第 3 步「設定」可以救回。"""
    from bookclub.turns import turn_sentences

    roles = [s.get("role") for s in (o.get("speakers") or [])]
    if not roles or "老師" in roles:
        return None
    a, b = o["start"], o["end"]
    for t in turns:
        who = t.get("說話者")
        if not who or who == "老師" or who in kept:
            continue
        if not (t["start"] - STUDENT_TURN_TOL_S <= a and b <= t["end"] + STUDENT_TURN_TOL_S):
            continue
        ss = turn_sentences(t, sents_by_id)
        if ss:
            lo, hi = min(s["start"] for s in ss), max(s["end"] for s in ss)
        elif (t.get("校對稿") or "").strip():
            lo, hi = t["start"], t["end"]
        else:
            continue
        if lo - STUDENT_TURN_TOL_S <= a and b <= hi + STUDENT_TURN_TOL_S:
            return t
    return None


def _region_files(region_dir: Path, start: float, end: float) -> tuple[str, Path, Path]:
    """區域的快取檔用起訖時間命名（09-26 改；以前用流水號，掃描範圍一改就會拿到別的區域的快取）。"""
    uri = f"區域_{start:.2f}_{end:.2f}"
    return uri, region_dir / f"{uri}.flac", region_dir / f"{uri}.rttm"


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
    RTTM（`workdir/重疊/區域_起_訖.rttm`，用時間命名），中途中斷只補沒做完的區域。
    """
    workdir = Path(workdir)
    cache_path = overlap_path(workdir)
    cached = read_json(cache_path, default=None)
    in_fp = fingerprint([[round(s, 1), round(e, 1)] for s, e in _merge_intervals([(s, e) for s, e in scan_regions])])
    if cached is not None:
        print("[3/找重疊] 已有 重疊.json，略過")
        before = cached.get("已自動跳過數")
        apply_simple_filters(cached)
        # 09-29 檢查 #7：記下掃描範圍；之後說話者判斷改了、範圍不同，大聲提醒（不自動重算：重疊已經接了人工覆核決定）
        fp_missing = cached.get("輸入指紋") is None
        if fp_missing:
            cached["輸入指紋"] = in_fp
        if cached["已自動跳過數"] != before or fp_missing:
            write_json(cache_path, cached)
        if cached["輸入指紋"] != in_fp:
            msg = ("重疊是用舊的掃描範圍找的，這次說話者判斷不一樣：新的學員區域裡的重疊可能沒找。"
                   "要重找就把 重疊.json 改名後重跑（每個區域有快取，只補新的區域）")
            print(f"⚠️ [3/找重疊] {msg}")
            cached = {**cached, "輸入改過": msg}
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
    n_cached = 0

    for i, (r_start, r_end) in enumerate(merged_regions):
        uri, region_audio, rttm_path = _region_files(region_dir, r_start, r_end)
        if rttm_path.exists():
            n_cached += 1
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
        "輸入指紋": in_fp,
        "用到快取的區域數": n_cached,
        "重疊數": len(all_overlaps),
        "已自動跳過數": n_skipped,
        "掃描區域數": n_regions,
        "掃描總秒數": round(scan_total, 1),
        "elapsed": round(elapsed, 1),
    }
    apply_simple_filters(result)
    n_skipped = result["已自動跳過數"]
    write_json(cache_path, result)
    print(f"[3/找重疊] 完成：共 {len(all_overlaps)} 處重疊，自動跳過 {n_skipped} 處，"
          f"耗時 {elapsed:.1f} 秒")
    return result
