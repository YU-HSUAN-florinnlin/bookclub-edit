"""bookclub/server.py 的單元測試：只測不需要真的開網路埠的部分——路徑安全、
Range 標頭解析、`/api/state`／`/api/refs`／`/api/names` 的純邏輯、
`名字覆核決定.json` 的寫入與更新、排除清單去重。用合成資料跟合成音訊，不碰
真的影片、Groq 或聲紋模型，也不啟動 ThreadingHTTPServer。

實際開伺服器、用 curl 打 API（含 Range 請求）的驗證另外在真實工作區手動跑過，
見這次改動的回報，不寫進這支自動測試（那需要真的工作區資料與 ffmpeg）。

獨立可跑：.venv/bin/python tests/test_server.py
裝了 pytest 的話：.venv/bin/python -m pytest tests/test_server.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import server as srv
from bookclub.workdir import (
    analysis_result_path,
    merged_transcript_path,
    name_candidates_dir,
    names_path,
    overlap_path,
    ref_dir as ref_dir_path,
    speakers_path,
    write_json,
)

SR = 16000


def _tone(seconds: float, sr: int = SR, freq: float = 220.0) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


class _TmpWorkdir:
    def __enter__(self):
        self.path = Path(tempfile.mkdtemp()) / "工作區"
        self.path.mkdir(parents=True)
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self.path.parent, ignore_errors=True)


# ---------- 路徑安全 ----------

def test_safe_join_allows_nested_relative():
    with _TmpWorkdir() as wd:
        target = wd / "參考音" / "候選1.wav"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"x")
        resolved = srv.safe_join(wd, "參考音/候選1.wav")
        assert resolved == target.resolve()


def test_safe_join_rejects_dotdot_escape():
    with _TmpWorkdir() as wd:
        try:
            srv.safe_join(wd, "../../etc/passwd")
            assert False, "應該要丟 SecurityError"
        except srv.SecurityError:
            pass


def test_safe_join_rejects_absolute_path():
    with _TmpWorkdir() as wd:
        try:
            srv.safe_join(wd, "/etc/passwd")
            assert False, "應該要丟 SecurityError"
        except srv.SecurityError:
            pass


def test_safe_join_rejects_empty_path():
    with _TmpWorkdir() as wd:
        try:
            srv.safe_join(wd, "")
            assert False, "應該要丟 SecurityError"
        except srv.SecurityError:
            pass


# ---------- Range 標頭解析 ----------

def test_parse_range_no_header_returns_whole_file():
    assert srv.parse_range(None, 1000) == (0, 999)


def test_parse_range_explicit_bytes():
    assert srv.parse_range("bytes=0-99", 1000) == (0, 99)
    assert srv.parse_range("bytes=100-199", 1000) == (100, 199)


def test_parse_range_open_ended():
    assert srv.parse_range("bytes=900-", 1000) == (900, 999)


def test_parse_range_suffix_length():
    assert srv.parse_range("bytes=-100", 1000) == (900, 999)


def test_parse_range_clamped_to_file_size():
    assert srv.parse_range("bytes=0-5000", 1000) == (0, 999)


def test_parse_range_out_of_bounds_raises():
    try:
        srv.parse_range("bytes=2000-3000", 1000)
        assert False, "應該要丟 ValueError"
    except ValueError:
        pass


def test_parse_range_start_after_end_raises():
    try:
        srv.parse_range("bytes=500-100", 1000)
        assert False, "應該要丟 ValueError"
    except ValueError:
        pass


# ---------- /api/state ----------

def test_build_state_all_missing_reports_not_done():
    with _TmpWorkdir() as wd:
        state = srv.build_state(wd)
        assert state["video"]["name"] is None
        for k in ("轉文字", "認老師", "找重疊", "挑參考音", "找名字"):
            assert state["substeps"][k]["done"] is False


def test_build_state_reports_completed_steps_and_elapsed():
    with _TmpWorkdir() as wd:
        write_json(merged_transcript_path(wd), {
            "duration": 120.0, "sentences": [{"id": 0}, {"id": 1}], "words": [{"word": "a"}],
        })
        write_json(speakers_path(wd), {
            "cluster_info": {"老師群佔可比對總秒數比例": 0.6}, "sentences": [], "scan_regions": [],
        })
        write_json(overlap_path(wd), {"overlaps": [], "重疊數": 2, "已自動跳過數": 1, "跳過": False})
        ref_dir = ref_dir_path(wd)
        ref_dir.mkdir(parents=True)
        write_json(ref_dir / "挑選紀錄.json", {"候選數": 5, "選定名次": None})
        write_json(names_path(wd), {
            "candidates": [{"start": 1.0}], "統計": {"總筆數": 1}, "已自動排除": [],
        })
        write_json(analysis_result_path(wd), {
            "video": "/tmp/某支影片.mp4", "影片長度": 120.0,
            "elapsed": {
                "1_轉文字": 10.0, "2_認老師": 5.0, "3_找重疊": 3.0,
                "4_挑參考音": 20.0, "5_找名字": 4.0, "總耗時": 42.0,
            },
        })

        state = srv.build_state(wd)
        assert state["video"]["name"] == "某支影片.mp4"
        assert state["video"]["duration_s"] == 120.0
        assert state["總耗時_s"] == 42.0
        sub = state["substeps"]
        assert sub["轉文字"]["done"] is True
        assert sub["轉文字"]["elapsed_s"] == 10.0
        assert sub["認老師"]["統計"]["老師群佔可比對總秒數比例"] == 0.6
        assert sub["找重疊"]["done"] is True
        assert sub["找重疊"]["統計"]["重疊數"] == 2
        assert sub["挑參考音"]["done"] is True
        assert sub["挑參考音"]["統計"]["候選數"] == 5
        assert sub["找名字"]["done"] is True
        assert sub["找名字"]["統計"]["總筆數"] == 1


def test_build_state_video_param_overrides_analysis_json():
    with _TmpWorkdir() as wd:
        write_json(analysis_result_path(wd), {"video": "/tmp/舊的.mp4"})
        state = srv.build_state(wd, video=Path("/tmp/新的.mp4"))
        assert state["video"]["name"] == "新的.mp4"


# ---------- /api/refs ----------

def test_build_refs_returns_accepted_sorted_by_rank_with_audio_url():
    with _TmpWorkdir() as wd:
        rd = ref_dir_path(wd)
        rd.mkdir(parents=True)
        attempts = [
            {"嘗試順序": 1, "狀態": "淘汰", "名次": None, "type": "單一段落"},
            {
                "嘗試順序": 2, "狀態": "入選", "名次": 2, "type": "單一段落",
                "used_start": 30.0, "used_end": 55.0, "compressed_duration": 25.0,
                "字數": 40, "transcript": "候選二的逐字稿", "score": 0.8,
            },
            {
                "嘗試順序": 3, "狀態": "入選", "名次": 1, "type": "單一段落",
                "used_start": 10.0, "used_end": 39.0, "compressed_duration": 29.0,
                "字數": 50, "transcript": "候選一的逐字稿", "score": 0.9,
            },
        ]
        write_json(rd / "候選.json", attempts)
        write_json(rd / "挑選紀錄.json", {"候選數": 2, "選定名次": None})
        (rd / "候選1.wav").write_bytes(b"fake-wav-bytes")
        (rd / "候選1.txt").write_text("候選一的逐字稿（檔案版）", encoding="utf-8")
        (rd / "候選2.wav").write_bytes(b"fake-wav-bytes-2")
        (rd / "候選2.txt").write_text("候選二的逐字稿（檔案版）", encoding="utf-8")

        result = srv.build_refs(wd)
        assert result["總數"] == 2
        assert [c["rank"] for c in result["candidates"]] == [1, 2]
        first = result["candidates"][0]
        assert first["transcript"] == "候選一的逐字稿（檔案版）"  # 讀檔案版，不是候選.json裡的舊字串
        assert first["音檔網址"] == "/api/audio?path=%E5%8F%83%E8%80%83%E9%9F%B3/%E5%80%99%E9%81%B81.wav"
        assert first["原片時間"] == "00:00:10–00:00:39"


def test_build_refs_missing_files_returns_empty():
    with _TmpWorkdir() as wd:
        result = srv.build_refs(wd)
        assert result == {"總數": 0, "已選定名次": None, "candidates": []}


# ---------- /api/refs/use ----------

def test_refs_use_writes_ref_files_and_updates_record():
    with _TmpWorkdir() as wd:
        rd = ref_dir_path(wd)
        rd.mkdir(parents=True)
        (rd / "候選1.wav").write_bytes(b"original-clip-bytes")
        write_json(rd / "挑選紀錄.json", {"候選數": 3, "選定名次": None})

        result = srv.refs_use(wd, 1, "修好的逐字稿")

        assert (rd / "ref.wav").read_bytes() == b"original-clip-bytes"
        assert (rd / "ref.txt").read_text(encoding="utf-8") == "修好的逐字稿"
        record = json.loads((rd / "挑選紀錄.json").read_text(encoding="utf-8"))
        assert record["選定名次"] == 1
        assert result["rank"] == 1


# ---------- /api/names ----------

def _make_names_workdir(wd: Path) -> dict:
    clip_dir = name_candidates_dir(wd)
    clip_dir.mkdir(parents=True)
    clip_path = clip_dir / "0000_5.0s.wav"
    audio = np.concatenate([_tone(1.0), _tone(1.0), _tone(1.0)])
    sf.write(str(clip_path), audio, SR)

    names_result = {
        "candidates": [
            {
                "start": 5.0, "end": 5.6, "sentence": "老師說到王小明的時候", "sentence_id": 0,
                "name": "王小明", "canonical": "王小明", "代號": "S1", "敏感詞": False,
                "matched_text": "王小明", "比對層級": "精確", "信心": "高", "位置": "句中",
                "建議做法": "只換名字", "原因": None, "切點信心": "雙邊乾淨", "建議緩衝秒數": 0.05,
                "候選音檔": str(clip_path.relative_to(wd)),
            },
        ],
        "已自動排除": [{"start": 1.0, "matched_text": "風風", "原因": "命中排除清單"}],
        "統計": {"總筆數": 1},
    }
    write_json(names_path(wd), names_result)
    return names_result


def test_build_names_highlights_and_gives_audio_urls():
    with _TmpWorkdir() as wd:
        _make_names_workdir(wd)
        data = srv.build_names(wd)
        assert len(data["candidates"]) == 1
        c = data["candidates"][0]
        assert c["id"] == "1"
        assert "<mark>" in c["sentence_html"]
        assert c["原音網址"].startswith("/api/audio?path=")
        assert c["消音網址"].startswith("/api/audio?path=")
        assert c["已標記"] == {"tags": [], "note": ""}
        assert len(data["已自動排除"]) == 1


def test_build_names_missing_file_returns_empty():
    with _TmpWorkdir() as wd:
        data = srv.build_names(wd)
        assert data == {"candidates": [], "已自動排除": [], "統計": {}}


# ---------- /api/names/mark ----------

def test_names_mark_writes_and_updates_same_id():
    with _TmpWorkdir() as wd:
        _make_names_workdir(wd)
        srv.names_mark(wd, "1", ["切點削到旁邊的字"], "先記著")
        decisions = json.loads((wd / srv.DECISIONS_FILE_NAME).read_text(encoding="utf-8"))
        assert decisions["1"]["tags"] == ["切點削到旁邊的字"]
        assert decisions["1"]["note"] == "先記著"

        # 同一筆再更新一次：應該覆蓋，不是疊加成兩筆
        srv.names_mark(wd, "1", ["不是名字"], "改成這樣")
        decisions = json.loads((wd / srv.DECISIONS_FILE_NAME).read_text(encoding="utf-8"))
        assert len(decisions) == 1
        assert decisions["1"]["tags"] == ["不是名字"]
        assert decisions["1"]["note"] == "改成這樣"


def test_names_mark_cutoff_tag_does_not_touch_exclusion_csv():
    with _TmpWorkdir() as wd:
        data_dir = Path(tempfile.mkdtemp())
        old_env = os.environ.get("BOOKCLUB_DATA_DIR")
        os.environ["BOOKCLUB_DATA_DIR"] = str(data_dir)
        try:
            _make_names_workdir(wd)
            result = srv.names_mark(wd, "1", ["切點削到旁邊的字"], "")
            assert result["已加入排除清單"] is False
            assert not (data_dir / srv.EXCLUSION_CSV_NAME).exists()
        finally:
            if old_env is None:
                os.environ.pop("BOOKCLUB_DATA_DIR", None)
            else:
                os.environ["BOOKCLUB_DATA_DIR"] = old_env
            shutil.rmtree(data_dir, ignore_errors=True)


def test_names_mark_place_tag_appends_to_exclusion_csv_once():
    with _TmpWorkdir() as wd:
        data_dir = Path(tempfile.mkdtemp())
        old_env = os.environ.get("BOOKCLUB_DATA_DIR")
        os.environ["BOOKCLUB_DATA_DIR"] = str(data_dir)
        try:
            _make_names_workdir(wd)
            r1 = srv.names_mark(wd, "1", ["是地名"], "")
            assert r1["已加入排除清單"] is True
            csv_path = data_dir / srv.EXCLUSION_CSV_NAME
            assert csv_path.exists()
            first_content = csv_path.read_text(encoding="utf-8")
            assert first_content.count("王小明") == 1

            # 再標一次同一筆（同一個 matched_text）：正規化後重複，不該再新增一列
            r2 = srv.names_mark(wd, "1", ["是地名", "不是名字"], "")
            assert r2["已加入排除清單"] is False
            second_content = csv_path.read_text(encoding="utf-8")
            assert second_content.count("王小明") == 1
            # 沒有 BOM（用 utf-8 純文字寫，不是 utf-8-sig），避免 append 模式在檔案中間插入 BOM
            assert not csv_path.read_bytes().startswith(b"\xef\xbb\xbf")
        finally:
            if old_env is None:
                os.environ.pop("BOOKCLUB_DATA_DIR", None)
            else:
                os.environ["BOOKCLUB_DATA_DIR"] = old_env
            shutil.rmtree(data_dir, ignore_errors=True)


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"通過：{t.__name__}")
    print(f"\n共 {len(tests)} 個測試通過")


if __name__ == "__main__":
    _run_all()
