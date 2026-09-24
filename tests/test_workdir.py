"""bookclub/workdir.py 的單元測試：路徑約定跟讀寫 JSON，不碰真的影片或模型。

獨立可跑：.venv/bin/python tests/test_workdir.py
裝了 pytest 的話：.venv/bin/python -m pytest tests/test_workdir.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import workdir as wd


def test_fmt_time():
    assert wd.fmt_time(0) == "00:00:00"
    assert wd.fmt_time(61) == "00:01:01"
    assert wd.fmt_time(3661) == "01:01:01"


def test_ensure_creates_directory():
    tmp = Path(tempfile.mkdtemp()) / "新工作區"
    try:
        assert not tmp.exists()
        out = wd.ensure(tmp)
        assert out == tmp
        assert tmp.is_dir()
        # 再呼叫一次不能出錯（目錄已存在）
        wd.ensure(tmp)
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


def test_path_helpers_use_expected_names():
    base = Path("/tmp/不存在的工作區")
    assert wd.audio_path(base) == base / "audio.flac"
    assert wd.chunks_dir(base) == base / "chunks"
    assert wd.transcript_dir(base) == base / "transcript"
    assert wd.merged_transcript_path(base) == base / "transcript" / "merged.json"
    assert wd.speakers_path(base) == base / "說話者判斷.json"
    assert wd.overlap_path(base) == base / "重疊.json"
    assert wd.names_path(base) == base / "名字候選.json"
    assert wd.name_candidates_dir(base) == base / "名字候選"
    assert wd.ref_dir(base) == base / "參考音"
    assert wd.ref_dir(base, ref_dir_name="別的名字") == base / "別的名字"
    assert wd.analysis_result_path(base) == base / "分析結果.json"
    assert wd.report_path(base) == base / "報告.md"


def test_read_json_missing_returns_default():
    missing = Path(tempfile.mkdtemp()) / "不存在.json"
    assert wd.read_json(missing) is None
    assert wd.read_json(missing, default=[]) == []


def test_write_json_then_read_json_roundtrip():
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        path = tmp_dir / "巢狀" / "資料.json"
        data = {"中文鍵": ["值1", 2, {"三": 3.5}], "空": None}
        wd.write_json(path, data)
        assert path.exists()
        # 中文不轉義才方便人直接看檔案內容
        raw = path.read_text(encoding="utf-8")
        assert "中文鍵" in raw
        loaded = wd.read_json(path)
        assert loaded == data
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
