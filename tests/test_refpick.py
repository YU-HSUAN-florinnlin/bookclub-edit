"""bookclub/refpick.py 的輕量單元測試：只測不需要網路、不需要 Groq／pyannote
模型的部分——壓縮停頓、候選區域找出與排序、試聽頁產生。用合成資料，不碰
真的影片或音檔。

獨立可跑（不需要 pytest）：.venv/bin/python tests/test_refpick.py
裝了 pytest 的話也可以：.venv/bin/python -m pytest tests/test_refpick.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from bookclub import refpick


# ---------- 小工具：合成音訊 ----------

def _tone(seconds: float, sr: int = refpick.SR, freq: float = 220.0) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _silence(seconds: float, sr: int = refpick.SR) -> np.ndarray:
    return np.zeros(int(seconds * sr), dtype=np.float32)


# ---------- 壓縮停頓、切段 ----------

def test_compress_pauses_shortens_long_pause():
    """一段語音、一段 1 秒長停頓（> LONG_PAUSE）、再一段語音：壓縮後應該比原始短，
    且『用到原片的秒數』要比壓縮後的音檔長（代表停頓真的被拿掉了）。"""
    x = np.concatenate([_tone(2.0), _silence(1.0), _tone(2.0)])
    y, orig_used = refpick._compress_pauses_and_cut(x, refpick.SR)
    assert len(y) > 0
    compressed_dur = len(y) / refpick.SR
    assert compressed_dur < len(x) / refpick.SR
    assert orig_used > compressed_dur, "長停頓應該被壓縮掉，原片用到的秒數要比壓縮後的音檔長"
    # 兩段語音各 2 秒＋壓縮後的停頓（縮到 KEEP_PAUSE 0.3 秒左右），容許一點誤差
    expected = 2.0 + 2.0 + refpick.KEEP_PAUSE
    assert abs(compressed_dur - expected) < 0.1


def test_compress_pauses_short_pause_not_compressed():
    """短停頓（<= LONG_PAUSE）不該被壓縮，長度應該原樣保留。"""
    x = np.concatenate([_tone(1.0), _silence(0.2), _tone(1.0)])
    y, orig_used = refpick._compress_pauses_and_cut(x, refpick.SR)
    compressed_dur = len(y) / refpick.SR
    assert abs(compressed_dur - len(x) / refpick.SR) < 0.05
    assert abs(orig_used - len(x) / refpick.SR) < 0.05


def test_compress_pauses_cuts_to_max_s():
    """遠超過 MAX_S 的連續語音（中間穿插會被壓縮的長停頓），壓縮後切出來的
    片段長度不能超過 MAX_S，且確實有把音訊縮短（切點落在原始音檔中間）。"""
    blocks = []
    for _ in range(10):
        blocks.append(_tone(4.0))
        blocks.append(_silence(1.0))
    x = np.concatenate(blocks)
    y, orig_used = refpick._compress_pauses_and_cut(x, refpick.SR)
    compressed_dur = len(y) / refpick.SR
    assert compressed_dur <= refpick.MAX_S + 1e-6
    assert compressed_dur > 0
    assert orig_used < len(x) / refpick.SR, "應該在原始音檔中間就切掉，不會用到全部"


def test_compress_pauses_no_cut_point_falls_back_to_hard_cut():
    """完全沒有夠長停頓可以壓縮、又超過 MAX_S 的極端情況：不能當掉，要保底硬切在 MAX_S。"""
    x = _tone(40.0)  # 40 秒連續語音、完全沒有停頓
    y, orig_used = refpick._compress_pauses_and_cut(x, refpick.SR)
    compressed_dur = len(y) / refpick.SR
    assert abs(compressed_dur - refpick.MAX_S) < 0.1
    assert abs(orig_used - refpick.MAX_S) < 0.1


# ---------- 找候選區域、排序 ----------

def _make_sentences(start: float, n: int, sent_dur: float, gap: float, text_len: int,
                     sim: float, avg_logprob: float) -> list[dict]:
    sentences = []
    t = start
    for i in range(n):
        text = "字" * text_len
        sentences.append({
            "id": f"s_{start}_{i}", "start": round(t, 3), "end": round(t + sent_dur, 3),
            "text": text, "avg_logprob": avg_logprob, "sim": sim, "label": "老師",
        })
        t += sent_dur + gap
    return sentences


def test_find_and_rank_prefers_dense_region_over_sparse():
    """兩段相似度、信心分數都一樣的候選，說話密度高的那段（正常語速）應該排在
    說話密度低的那段（像引導練習：句子短、空白長）前面——這是避開開場引導
    練習用的規則。"""
    sparse = _make_sentences(start=60.0, n=15, sent_dur=2.0, gap=1.0, text_len=3,
                              sim=0.7, avg_logprob=-0.1)
    dense = _make_sentences(start=2000.0, n=15, sent_dur=2.0, gap=0.2, text_len=8,
                             sim=0.7, avg_logprob=-0.1)
    sentences = sparse + dense
    selected, _elapsed = refpick._find_and_rank_candidates(sentences, n=2)
    assert len(selected) >= 1
    assert selected[0].start >= 2000.0, "密度高的候選應該排第一"
    starts = sorted(r.start for r in selected)
    assert all(b - a >= refpick.CAND_START_GAP_S for a, b in zip(starts, starts[1:]))


def test_find_and_rank_dedups_overlapping_and_close_starts():
    """三段彼此離很遠的連續老師講話，每段內部會產生很多起點相近、互相重疊的
    候選：選出來的結果應該是每段各選一個，彼此不重疊、起點也要相隔
    >= CAND_START_GAP_S 秒（不會同一段裡選出好幾個幾乎一樣的候選）。"""
    blocks = [
        _make_sentences(start=block_start, n=30, sent_dur=1.0, gap=0.3, text_len=6,
                         sim=0.7, avg_logprob=-0.1)
        for block_start in (0.0, 500.0, 1000.0)
    ]
    sentences = [s for block in blocks for s in block]
    selected, _elapsed = refpick._find_and_rank_candidates(sentences, n=5)
    assert len(selected) == 3, "三段互不相鄰的區塊，應該各選出一個候選"
    for a in selected:
        for b in selected:
            if a is b:
                continue
            assert not (a.start < b.end and b.start < a.end), "候選不該互相重疊"
            assert abs(a.start - b.start) >= refpick.CAND_START_GAP_S


def test_find_and_rank_rejects_too_short_region():
    """區域長度不到 CAND_MIN_DUR_S（30 秒）不該入選。"""
    sentences = _make_sentences(start=0.0, n=5, sent_dur=2.0, gap=0.5, text_len=6,
                                 sim=0.7, avg_logprob=-0.1)  # 總長遠小於 30 秒
    selected, _elapsed = refpick._find_and_rank_candidates(sentences, n=5)
    assert selected == []


def test_find_and_rank_respects_similarity_and_logprob_thresholds():
    """相似度或信心分數沒過門檻的句子不該被算進候選區域。"""
    low_sim = _make_sentences(start=0.0, n=20, sent_dur=2.0, gap=0.3, text_len=6,
                               sim=0.3, avg_logprob=-0.1)  # 相似度不到 0.5
    low_logprob = _make_sentences(start=500.0, n=20, sent_dur=2.0, gap=0.3, text_len=6,
                                   sim=0.7, avg_logprob=-2.0)  # 信心分數不到 -0.6
    sentences = low_sim + low_logprob
    selected, _elapsed = refpick._find_and_rank_candidates(sentences, n=5)
    assert selected == []


# ---------- 排除區域 ----------

def test_merge_intervals_merges_overlapping_and_adjacent():
    merged = refpick._merge_intervals([(10.0, 15.0), (14.0, 20.0), (30.0, 35.0), (5.0, 10.0)])
    assert merged == [(5.0, 20.0), (30.0, 35.0)]


def test_merge_intervals_empty():
    assert refpick._merge_intervals([]) == []


def test_overlaps_any_true_and_false():
    intervals = [(10.0, 20.0), (50.0, 60.0)]
    assert refpick._overlaps_any(15.0, 25.0, intervals) is True   # 跟第一段重疊
    assert refpick._overlaps_any(20.0, 50.0, intervals) is False  # 剛好卡在中間、不重疊
    assert refpick._overlaps_any(0.0, 5.0, intervals) is False


def test_exclude_regions_from_sentences_pads_and_merges():
    sentences = [
        {"start": 100.0, "end": 102.0, "label": "不是老師"},
        {"start": 103.0, "end": 104.0, "label": "不確定"},  # 跟上面那句擴 3 秒後會接起來
        {"start": 200.0, "end": 201.0, "label": "老師"},     # 老師的句子不算排除區域
        {"start": 300.0, "end": 301.0, "label": "太短"},     # 太短也不算
    ]
    excluded = refpick._exclude_regions_from_sentences(sentences, pad_s=3.0)
    assert excluded == [(97.0, 107.0)]


def test_find_and_rank_excludes_region_overlapping_exclude_zone():
    """跟排除區域重疊的候選，不管分數多高都不該出現在候選池裡。"""
    sentences = _make_sentences(start=0.0, n=30, sent_dur=1.0, gap=0.3, text_len=6,
                                 sim=0.7, avg_logprob=-0.1)
    # 這段候選會落在 0～38.7 秒左右，排除區域整個蓋住它
    selected, _elapsed = refpick._find_and_rank_candidates(
        sentences, n=5, exclude_regions=[(0.0, 100.0)]
    )
    assert selected == []


def test_find_and_rank_keeps_region_outside_exclude_zone():
    blocks = [
        _make_sentences(start=block_start, n=30, sent_dur=1.0, gap=0.3, text_len=6,
                         sim=0.7, avg_logprob=-0.1)
        for block_start in (0.0, 500.0)
    ]
    sentences = [s for block in blocks for s in block]
    # 只排除第一段（0 附近），第二段（500 附近）應該還在
    selected, _elapsed = refpick._find_and_rank_candidates(
        sentences, n=5, exclude_regions=[(0.0, 100.0)]
    )
    assert len(selected) == 1
    assert selected[0].start >= 500.0


# ---------- 步驟 5a：對齊第一個字／最後一個字 ----------

def test_word_trim_bounds_cuts_leading_and_trailing_noise():
    """區域本身比實際講話的範圍寬（例如頭尾有笑聲、雜音沒被轉成字），裁切後的
    起訖應該對齊第一個字開始、最後一個字結束，前後留 WORD_PAD_S 秒。"""
    words = [
        {"start": 10.5, "end": 10.8, "word": "A"},
        {"start": 10.9, "end": 11.3, "word": "B"},
        {"start": 15.0, "end": 15.4, "word": "C"},
    ]
    new_start, new_end = refpick._word_trim_bounds(words, region_start=10.0, region_end=16.0)
    assert abs(new_start - (10.5 - refpick.WORD_PAD_S)) < 1e-6
    assert abs(new_end - (15.4 + refpick.WORD_PAD_S)) < 1e-6


def test_word_trim_bounds_does_not_extend_past_region():
    """就算字幾乎貼齊區域邊界，裁切結果也不該跑到原始區域之外。"""
    words = [{"start": 10.02, "end": 10.5, "word": "A"}, {"start": 15.8, "end": 15.99, "word": "B"}]
    new_start, new_end = refpick._word_trim_bounds(words, region_start=10.0, region_end=16.0)
    assert new_start >= 10.0
    assert new_end <= 16.0


def test_word_trim_bounds_no_words_keeps_original():
    new_start, new_end = refpick._word_trim_bounds([], region_start=10.0, region_end=16.0)
    assert (new_start, new_end) == (10.0, 16.0)


# ---------- 步驟 5c：細看聲紋，判斷問題位置 ----------

def _w(start, end, sim):
    return {"start": start, "end": end, "sim": sim}


def test_classify_window_issues_no_problem():
    windows = [_w(0, 1.5, 0.8), _w(0.5, 2.0, 0.75), _w(1.0, 2.5, 0.82)]
    issue, s, e = refpick._classify_window_issues(windows, 0.0, 3.0)
    assert issue == "無"
    assert (s, e) == (0.0, 3.0)


def test_classify_window_issues_head_problem_trims_start():
    windows = [_w(0, 1.5, 0.1), _w(0.5, 2.0, 0.15), _w(1.0, 2.5, 0.8), _w(1.5, 3.0, 0.82)]
    issue, s, e = refpick._classify_window_issues(windows, 0.0, 3.0)
    assert issue == "頭"
    assert s == 2.0  # 最後一個壞窗（0.5–2.0）的結束時間
    assert e == 3.0


def test_classify_window_issues_tail_problem_trims_end():
    windows = [_w(0, 1.5, 0.8), _w(0.5, 2.0, 0.82), _w(1.0, 2.5, 0.1), _w(1.5, 3.0, 0.05)]
    issue, s, e = refpick._classify_window_issues(windows, 0.0, 3.0)
    assert issue == "尾"
    assert s == 0.0
    assert e == 1.0  # 第一個壞窗（1.0–2.5）的開始時間


def test_classify_window_issues_head_and_tail():
    windows = [_w(0, 1.5, 0.1), _w(1.5, 3.0, 0.8), _w(3.0, 4.5, 0.82), _w(4.5, 6.0, 0.05)]
    issue, s, e = refpick._classify_window_issues(windows, 0.0, 6.0)
    assert issue == "頭尾"
    assert s == 1.5
    assert e == 4.5


def test_classify_window_issues_interior_problem_is_rejected():
    """壞窗夾在中間、頭尾都正常：裁不掉，整段淘汰。"""
    windows = [_w(0, 1.5, 0.8), _w(1.5, 3.0, 0.82), _w(3.0, 4.5, 0.1), _w(4.5, 6.0, 0.8), _w(6.0, 7.5, 0.81)]
    issue, s, e = refpick._classify_window_issues(windows, 0.0, 7.5)
    assert issue == "中間"
    assert (s, e) == (0.0, 7.5)  # 淘汰的話起訖不動，呼叫端不會拿這個去裁


def test_classify_window_issues_all_bad_is_rejected():
    windows = [_w(0, 1.5, 0.1), _w(1.5, 3.0, 0.05), _w(3.0, 4.5, 0.2)]
    issue, s, e = refpick._classify_window_issues(windows, 0.0, 4.5)
    assert issue == "中間"


def test_classify_window_issues_no_windows_keeps_original():
    """沒有窗可比（例如整段太短、或全部被判定安靜跳過）：沒有證據說有問題，
    維持原樣，不裁也不淘汰。"""
    issue, s, e = refpick._classify_window_issues([], 2.0, 5.0)
    assert issue == "無"
    assert (s, e) == (2.0, 5.0)


# ---------- 保底順序：拼接 ----------

def _region(start, end, score=0.7, text="測試文字內容"):
    dur = end - start
    return refpick._Region(start, end, min_sim=0.7, density=3.0, confidence=0.8, score=score, text=text)


def test_should_use_combine_mode_triggers_on_low_pool_or_force():
    assert refpick._should_use_combine_mode(pool_size=5, force_combine=False) is False
    assert refpick._should_use_combine_mode(pool_size=refpick.MIN_CANDIDATES - 1, force_combine=False) is True
    assert refpick._should_use_combine_mode(pool_size=0, force_combine=False) is True
    assert refpick._should_use_combine_mode(pool_size=99, force_combine=True) is True


def test_pick_combine_set_returns_none_when_not_enough_segments():
    assert refpick._pick_combine_set([]) is None
    assert refpick._pick_combine_set([_region(0.0, 10.0)]) is None  # 只有 1 段，湊不到 COMBINE_MIN_SEGMENTS


def test_pick_combine_set_reaches_target_length_and_time_order():
    """三段各 10 秒、彼此不重疊：應該選出 >=2 段、總長落在目標範圍附近，
    而且回傳結果依時間先後排序（拼接敘述才會順）。"""
    segments = [
        _region(100.0, 110.0, score=0.9),
        _region(120.0, 130.0, score=0.85),
        _region(140.0, 150.0, score=0.8),
    ]
    combo = refpick._pick_combine_set(segments)
    assert combo is not None
    assert refpick.COMBINE_MIN_SEGMENTS <= len(combo) <= refpick.COMBINE_MAX_SEGMENTS
    total = sum(s.end - s.start for s in combo)
    assert total <= refpick.COMBINE_TARGET_MAX_S + 1e-6
    starts = [s.start for s in combo]
    assert starts == sorted(starts)


def test_pick_combine_set_prefers_close_in_time_segment():
    """基準片段在 t=0；同分數的兩個候選，一個在附近（t=15）、一個很遠
    （t=5000）：應該選比較近的那個（收音條件較一致）。"""
    anchor = _region(0.0, 10.0, score=0.9)
    near = _region(15.0, 25.0, score=0.7)
    far = _region(5000.0, 5010.0, score=0.7)
    combo = refpick._pick_combine_set([anchor, near, far])
    assert combo is not None
    chosen_starts = {s.start for s in combo}
    assert near.start in chosen_starts
    assert far.start not in chosen_starts


def test_pick_combine_set_respects_length_budget():
    """基準片段選定後，剩餘預算不夠塞的片段要被排除，只挑塞得下的。"""
    anchor = _region(0.0, 15.0, score=0.9)         # 15 秒，最高分，當基準；剩餘預算 29-15=14 秒
    too_long = _region(100.0, 116.0, score=0.85)   # 16 秒，超過剩餘預算，該被排除
    fits = _region(200.0, 210.0, score=0.7)        # 10 秒，還塞得下
    combo = refpick._pick_combine_set([anchor, too_long, fits])
    assert combo is not None
    total = sum(s.end - s.start for s in combo)
    assert total <= refpick.COMBINE_TARGET_MAX_S + 1e-6
    chosen_starts = {s.start for s in combo}
    assert too_long.start not in chosen_starts
    assert fits.start in chosen_starts


def test_find_combine_segments_respects_exclude_regions():
    sentences = _make_sentences(start=0.0, n=10, sent_dur=1.0, gap=0.2, text_len=6,
                                 sim=0.7, avg_logprob=-0.1)  # 約 12 秒，介於 8–20 秒之間
    segments, _elapsed = refpick._find_combine_segments(sentences, exclude_regions=[(0.0, 100.0)])
    assert segments == []
    segments2, _elapsed2 = refpick._find_combine_segments(sentences)
    assert len(segments2) >= 1


# ---------- 拼接組裝：接點、長度、音量對齊 ----------

def test_assemble_pieces_length_matches_pieces_plus_silence():
    a = _tone(5.0)
    b = _tone(6.0)
    c = _tone(4.0)
    audio = refpick._assemble_pieces([a, b, c], refpick.SR)
    expected_len = len(a) + len(b) + len(c) + 2 * int(refpick.COMBINE_SILENCE_S * refpick.SR)
    assert len(audio) == expected_len


def test_assemble_pieces_has_silence_gap_between_pieces():
    """兩段之間應該有一段音量近乎零的靜音（COMBINE_SILENCE_S 秒）。"""
    a = _tone(3.0)
    b = _tone(3.0)
    audio = refpick._assemble_pieces([a, b], refpick.SR)
    silence_n = int(refpick.COMBINE_SILENCE_S * refpick.SR)
    gap = audio[len(a): len(a) + silence_n]
    assert np.max(np.abs(gap)) < 1e-6


def test_assemble_pieces_volume_aligned_to_first_piece():
    """第二段音量只有第一段的十分之一：接起來之後兩段的 RMS 應該接近（對齊到第一段）。"""
    loud = _tone(3.0)  # 振幅 0.2
    quiet = (_tone(3.0) / 20.0).astype(np.float32)  # 振幅只有 loud 的 1/20
    audio = refpick._assemble_pieces([loud, quiet], refpick.SR)
    silence_n = int(refpick.COMBINE_SILENCE_S * refpick.SR)
    first_part = audio[: len(loud)]
    second_part = audio[len(loud) + silence_n: len(loud) + silence_n + len(quiet)]
    rms1 = refpick._rms(first_part)
    rms2 = refpick._rms(second_part)
    assert rms2 > 0.5 * rms1  # 對齊前 quiet 只有 loud 的 1/20，對齊後應該接近同一個量級


def test_assemble_pieces_empty_list():
    assert len(refpick._assemble_pieces([], refpick.SR)) == 0


def test_assemble_pieces_fades_piece_edges():
    """每段頭尾應該有淡入淡出：頭幾個樣本的振幅要比中段小很多，中段維持原始振幅。"""
    a = _tone(3.0)
    audio = refpick._assemble_pieces([a], refpick.SR)
    fade_n = int(refpick.COMBINE_FADE_S * refpick.SR)
    head_peak = np.max(np.abs(audio[:fade_n]))
    mid_start = len(audio) // 2 - 200
    mid_peak = np.max(np.abs(audio[mid_start: mid_start + 400]))
    assert mid_peak > 0.15  # 中段沒被淡入淡出影響，還有接近原始振幅（0.2）的音量
    assert head_peak < mid_peak  # 淡入區間的峰值應該比中段小


# ---------- 試聽頁產生 ----------

def test_build_html_contains_all_candidates_and_controls():
    candidates = [
        {"rank": 1, "used_start": 100.0, "used_end": 125.0, "transcript": "第一段逐字稿測試文字"},
        {"rank": 2, "used_start": 300.0, "used_end": 328.0, "transcript": "第二段逐字稿測試文字"},
    ]
    html = refpick._build_html(candidates)
    assert "<html" in html and "</html>" in html
    assert "候選1.wav" in html
    assert "候選2.wav" in html
    assert "第一段逐字稿測試文字" in html
    assert "第二段逐字稿測試文字" in html
    assert "換一段" in html
    assert "複製逐字稿" in html
    assert "請回報宇軒" in html
    assert "用第 ${c.rank} 段" in html  # 動態說明文字的樣板還在


def test_build_html_empty_candidates_does_not_crash():
    html = refpick._build_html([])
    assert "<html" in html


# ---------- 主流程（不需要模型／網路的小工具） ----------

def test_fmt_time():
    assert refpick.fmt_time(0) == "00:00:00"
    assert refpick.fmt_time(3503) == "00:58:23"
    assert refpick.fmt_time(3600 + 23 * 60 + 22) == "01:23:22"


def test_cosine_identical_vectors():
    v = np.array([1.0, 2.0, 3.0])
    assert abs(refpick.cosine(v, v) - 1.0) < 1e-9


def test_cosine_orthogonal_vectors():
    a = np.array([1.0, 0.0])
    b = np.array([0.0, 1.0])
    assert abs(refpick.cosine(a, b)) < 1e-9


TESTS = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]


def main() -> int:
    failed = []
    for fn in TESTS:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            failed.append(fn.__name__)
            print(f"FAIL  {fn.__name__}：{e}")
        except Exception as e:  # noqa: BLE001
            failed.append(fn.__name__)
            print(f"ERROR {fn.__name__}：{type(e).__name__}: {e}")
    print(f"\n{len(TESTS) - len(failed)}/{len(TESTS)} 個測試通過")
    if failed:
        print("失敗：" + "、".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
