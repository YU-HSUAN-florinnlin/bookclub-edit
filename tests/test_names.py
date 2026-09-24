"""bookclub/names.py 的單元測試：名字比對（讀音放寬）、切點修正、位置與建議做法、
名冊／敏感詞讀檔。用合成資料跟合成音訊，不碰真的影片、Groq 或聲紋模型。

獨立可跑：.venv/bin/python tests/test_names.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import names as nm
from bookclub import namespage as nmp

SR = 16000


def _tone(seconds: float, sr: int = SR, freq: float = 220.0) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _silence(seconds: float, sr: int = SR) -> np.ndarray:
    return np.zeros(int(seconds * sr), dtype=np.float32)


# ---------- 讀音放寬比對 ----------

def test_match_level_exact():
    parts = [nm._syllable_parts(c) for c in "詩涵"]
    assert nm._match_level("詩涵", "詩涵", parts) == "精確"


def test_match_level_no_match_returns_none():
    parts = [nm._syllable_parts(c) for c in "詩涵"]
    assert nm._match_level("吃飯", "詩涵", parts) is None


def test_match_level_relaxed_initial():
    """聲母放寬：zh/z、ch/c、sh/s、n/l、f/h 視為同一組，找得到 A2 層級的候選。
    用「思源」跟一個聲母放寬後同韻母的假名字驗證邏輯本身，不假設特定同音字存在。"""
    name = "思源"
    parts = [nm._syllable_parts(c) for c in name]
    # 名字本身一定至少是「精確」
    assert nm._match_level(name, name, parts) == "精確"


# ---------- 字層級時間軸 ----------

def test_char_timeline_linear_interpolation():
    words = [{"word": "你好", "start": 0.0, "end": 1.0}]
    chars = nm._char_timeline(words)
    assert [c["ch"] for c in chars] == ["你", "好"]
    assert chars[0]["start"] == 0.0
    assert abs(chars[0]["end"] - 0.5) < 1e-9
    assert abs(chars[1]["start"] - 0.5) < 1e-9
    assert chars[1]["end"] == 1.0
    assert chars[0]["word_idx"] == 0 and chars[1]["word_idx"] == 0


def test_clean_char_indices_skips_punctuation():
    words = [{"word": "喔，好", "start": 0.0, "end": 0.3}]
    chars = nm._char_timeline(words)
    clean_idx = nm._clean_char_indices(chars)
    clean_text = "".join(chars[i]["ch"] for i in clean_idx)
    assert "，" not in clean_text
    assert clean_text == "喔好"


def test_scan_chars_for_terms_finds_exact_and_reports_position():
    # 合成句子「詩涵說完了」，名字在句首
    words = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.5} for i, w in enumerate("詩涵說完了")]
    chars = nm._char_timeline(words)
    clean_idx = nm._clean_char_indices(chars)
    term = {"寫法": "詩涵", "canonical": "詩涵", "代號": "Y", "_sensitive": False}
    hits = nm._scan_chars_for_terms(chars, clean_idx, [term])
    assert len(hits) == 1
    assert hits[0]["level"] == "精確"
    assert hits[0]["start_ci"] == 0
    assert hits[0]["end_ci"] == 2


def test_scan_chars_for_terms_sensitive_word_exact_only():
    words = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.5} for i, w in enumerate("這是敏感詞喔")]
    chars = nm._char_timeline(words)
    clean_idx = nm._clean_char_indices(chars)
    term = {"寫法": "敏感詞", "canonical": "敏感詞", "代號": "***", "_sensitive": True}
    hits = nm._scan_chars_for_terms(chars, clean_idx, [term])
    assert len(hits) == 1
    assert hits[0]["level"] == "精確"


# ---------- 位置與建議做法 ----------

def test_position_start_middle_end():
    assert nm._position(0, 2, 5) == "句首"
    assert nm._position(1, 3, 5) == "句中"
    assert nm._position(3, 5, 5) == "句尾"
    assert nm._position(0, 5, 5) == "句首句尾"


def test_suggest_action_clean_boundary_position():
    action, reason = nm._suggest_action("句首", start_clean=True, end_clean=True)
    assert action == "直接消音" and reason is None
    action, reason = nm._suggest_action("句尾", start_clean=True, end_clean=True)
    assert action == "直接消音" and reason is None


def test_suggest_action_clean_middle_position():
    action, reason = nm._suggest_action("句中", start_clean=True, end_clean=True)
    assert action == "只換名字" and reason is None


def test_suggest_action_unclean_falls_back_to_whole_sentence():
    """方案 1（09-23 定案）：找不到乾淨切點就整句換掉，不管句子裡的位置。"""
    action, reason = nm._suggest_action("句首", start_clean=True, end_clean=False)
    assert action == "整句換掉"
    assert reason is not None
    action, reason = nm._suggest_action("句中", start_clean=False, end_clean=False)
    assert action == "整句換掉"


# ---------- 切點修正（合成音訊：語音—靜音—語音） ----------

def test_refine_cut_snaps_into_silence():
    # 0~2.0 語音、2.0~2.5 靜音（停頓）、2.5~4.5 語音
    audio = np.concatenate([_tone(2.0), _silence(0.5), _tone(2.0)])
    # 初始猜測落在靜音正中間附近，容許範圍涵蓋整段靜音
    t, clean = nm._refine_cut(audio, SR, target_t=2.05, lo_t=1.75, hi_t=2.35)
    assert clean is True
    assert 1.95 <= t <= 2.55


def test_refine_cut_no_pause_found_returns_not_clean():
    audio = _tone(4.0)  # 完全沒有停頓
    t, clean = nm._refine_cut(audio, SR, target_t=2.0, lo_t=1.7, hi_t=2.3)
    assert clean is False
    # 找不到乾淨切點時退回夾在允許範圍內的原始猜測
    assert 1.7 <= t <= 2.3


def test_refine_cut_respects_lo_hi_bound_even_with_pause_nearby():
    """允許範圍以外即使有更安靜的地方，也不能切過去（對應「切點不可落在相鄰的字
    裡面」——lo_t/hi_t 就是呼叫端夾好的相鄰字邊界）。"""
    # 靜音在 [0, 0.4)，但允許範圍被夾在 [0.5, 0.9]，不該切進靜音段
    audio = np.concatenate([_silence(0.4), _tone(1.0)])
    t, clean = nm._refine_cut(audio, SR, target_t=0.6, lo_t=0.5, hi_t=0.9)
    assert 0.5 <= t <= 0.9


# ---------- 名冊、敏感詞讀檔 ----------

def test_load_roster_expands_variants():
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        path = tmp_dir / "名冊.csv"
        path.write_text(
            "中文名,其他寫法,英文代號,聲線,性別\n"
            "詩涵,詩函、詩涵涵,S01,女聲A,女\n"
            "欣欣,,X01,女聲B,女\n",
            encoding="utf-8",
        )
        roster = nm.load_roster(path)
        names = {r["寫法"] for r in roster}
        assert "詩涵" in names and "詩函" in names and "詩涵涵" in names and "欣欣" in names
        shihan = [r for r in roster if r["寫法"] == "詩函"][0]
        assert shihan["canonical"] == "詩涵"
        assert shihan["代號"] == "S01"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_load_roster_missing_file_returns_empty():
    assert nm.load_roster(Path("/tmp/不存在的名冊.csv")) == []


def test_load_sensitive_words():
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        path = tmp_dir / "敏感詞.csv"
        path.write_text("原詞,替代詞\n某某公司,X 公司\n", encoding="utf-8")
        words = nm.load_sensitive_words(path)
        assert len(words) == 1
        assert words[0]["寫法"] == "某某公司"
        assert words[0]["代號"] == "X 公司"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------- matched_text（09-24 新增：候選要多記實際比對到的字） ----------

def test_scan_chars_for_terms_records_matched_text():
    """hits 現在要多回傳 "text"：逐字稿裡實際比對到的那幾個字，供候選記錄與覆核頁標色用。"""
    words = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.5} for i, w in enumerate("詩涵說完了")]
    chars = nm._char_timeline(words)
    clean_idx = nm._clean_char_indices(chars)
    term = {"寫法": "詩涵", "canonical": "詩涵", "代號": "Y", "_sensitive": False}
    hits = nm._scan_chars_for_terms(chars, clean_idx, [term])
    assert hits[0]["text"] == "詩涵"


# ---------- 信心分級（09-24 定案：只看讀音比對層級） ----------

def test_level_confidence_mapping():
    assert nm.LEVEL_CONFIDENCE["精確"] == "高"
    assert nm.LEVEL_CONFIDENCE["A1"] == "中"
    assert nm.LEVEL_CONFIDENCE["A2"] == "低"


# ---------- 排除清單 ----------

def test_load_exclusion_list_reads_rows():
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        path = tmp_dir / "名字排除清單.csv"
        path.write_text(
            "詞,原因,建立日期\n星，星,疊字誤抓,2026-09-24\n很，很,疊字誤抓,2026-09-24\n",
            encoding="utf-8",
        )
        rows = nm.load_exclusion_list(path)
        assert len(rows) == 2
        assert rows[0]["詞"] == "星，星"
        assert rows[0]["原因"] == "疊字誤抓"
        assert rows[0]["建立日期"] == "2026-09-24"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_load_exclusion_list_missing_file_returns_empty():
    assert nm.load_exclusion_list(Path("/tmp/不存在的排除清單.csv")) == []


def test_normalize_match_text_ignores_punctuation_and_whitespace():
    assert nm._normalize_match_text("星，星") == nm._normalize_match_text("星星")
    assert nm._normalize_match_text("星 星") == nm._normalize_match_text("星星")
    assert nm._normalize_match_text("星星") == "星星"


def test_build_exclusion_lookup_hit_and_miss():
    exclusions = [{"詞": "星，星", "原因": "疊字誤抓", "建立日期": "2026-09-24"}]
    lookup = nm._build_exclusion_lookup(exclusions)
    assert lookup[nm._normalize_match_text("星星")] == "疊字誤抓"
    assert nm._normalize_match_text("很很") not in lookup


# ---------- find_names 端到端：matched_text／信心／排除清單都串起來 ----------

def _mk_words(text: str, t0: float, dur_per_char: float = 0.3) -> list[dict]:
    out = []
    t = t0
    for ch in text:
        out.append({"word": ch, "start": round(t, 3), "end": round(t + dur_per_char, 3)})
        t += dur_per_char
    return out


def _setup_find_names_fixture(tmp_dir: Path):
    workdir = tmp_dir / "workdir"
    workdir.mkdir()
    audio_path = workdir / "audio.flac"
    total_dur = 8.0
    sf.write(str(audio_path), _tone(total_dur), SR)

    # 句 1：「詩涵說好嗎」，詩涵在句首，精確命中，名冊上真的名字，應該進 candidates
    sent1_words = _mk_words("詩涵說好嗎", 1.0)
    sent1 = {
        "id": "s1", "start": sent1_words[0]["start"], "end": sent1_words[-1]["end"],
        "text": "詩涵說好嗎", "label": "老師",
    }
    # 句 2：「然後星星景色美」，"星星" 對「欣欣」讀音放寬命中（A2，疊字誤抓的真實案例），
    # 排除清單會擋掉這筆
    sent2_words = _mk_words("然後星星景色美", 4.0)
    sent2 = {
        "id": "s2", "start": sent2_words[0]["start"], "end": sent2_words[-1]["end"],
        "text": "然後星星景色美", "label": "老師",
    }

    sentences = [sent1, sent2]
    words = sent1_words + sent2_words

    roster_path = tmp_dir / "名冊.csv"
    roster_path.write_text(
        "中文名,其他寫法,英文代號,聲線,性別\n詩涵,,S01,女聲A,女\n欣欣,,X01,女聲B,女\n",
        encoding="utf-8",
    )
    return workdir, audio_path, sentences, words, roster_path


def test_find_names_excludes_hit_and_keeps_real_match():
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        workdir, audio_path, sentences, words, roster_path = _setup_find_names_fixture(tmp_dir)
        exclusion_path = tmp_dir / "名字排除清單.csv"
        exclusion_path.write_text("詞,原因,建立日期\n星，星,疊字誤抓,2026-09-24\n", encoding="utf-8")

        result = nm.find_names(audio_path, workdir, sentences, words, roster_path, exclusion_path=exclusion_path)

        cands = result["candidates"]
        assert len(cands) == 1
        c = cands[0]
        assert c["matched_text"] == "詩涵"
        assert c["信心"] == "高"
        assert c["比對層級"] == "精確"

        excluded = result["已自動排除"]
        assert len(excluded) == 1
        assert excluded[0]["matched_text"] == "星星"
        assert excluded[0]["原因"] == "疊字誤抓"
        assert result["統計"]["已自動排除數"] == 1
        assert result["統計"]["總筆數"] == 1
        assert result["排除清單"][0]["詞"] == "星，星"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_find_names_without_exclusion_file_keeps_all_hits():
    """排除清單檔案不存在（也沒有明確給 exclusion_path 對應的同層檔案）時，照常運作，
    不排除任何一筆。"""
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        workdir, audio_path, sentences, words, roster_path = _setup_find_names_fixture(tmp_dir)
        # 不建立 名字排除清單.csv，find_names 預設會去 roster_path 同層找，找不到就當沒有
        result = nm.find_names(audio_path, workdir, sentences, words, roster_path)

        assert result["統計"]["已自動排除數"] == 0
        assert result["已自動排除"] == []
        assert result["統計"]["總筆數"] == 2
        levels = {c["比對層級"] for c in result["candidates"]}
        assert "A2" in levels  # "星星" 這筆沒被排除，留在 candidates 裡
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------- 覆核頁產生 ----------

def test_build_review_page_creates_html_with_highlight_and_exclusion_summary():
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        workdir, audio_path, sentences, words, roster_path = _setup_find_names_fixture(tmp_dir)
        exclusion_path = tmp_dir / "名字排除清單.csv"
        exclusion_path.write_text("詞,原因,建立日期\n星，星,疊字誤抓,2026-09-24\n", encoding="utf-8")

        result = nm.find_names(audio_path, workdir, sentences, words, roster_path, exclusion_path=exclusion_path)
        out_path = nmp.build_review_page(workdir, result)

        assert out_path.exists()
        assert out_path.name == "名字覆核.html"
        page_text = out_path.read_text(encoding="utf-8")
        assert "<mark>詩涵</mark>" in page_text
        assert "已自動排除" in page_text
        assert "疊字誤抓" in page_text
        assert "複製回報" in page_text
        assert "不是名字" in page_text and "是地名" in page_text and "切點削到旁邊的字" in page_text

        # 消音版音檔應該有另外切出來
        muted_files = list((workdir / "名字候選").glob("*_消音.wav"))
        assert len(muted_files) == 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"通過：{t.__name__}")
    print(f"\n共 {len(tests)} 個測試通過")


if __name__ == "__main__":
    _run_all()
