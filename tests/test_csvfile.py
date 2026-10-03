"""bookclub/csvfile.py（10-03 第九批 #35）：Excel 另存的 CSV 是 Big5、或 UTF-8 帶 BOM，都要讀得了。

內容都是虛構的。獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_csvfile.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import csvfile, students, tts

PRON = "原字,生成用,原因,建立日期\r\n愉快,魚快,測試用,2026-10-03\r\n"
ROSTER = "中文名,其他寫法,性別\r\n林測試,小測、阿測,男\r\n陳虛構,,女\r\n"


def _with_data_dir(d: Path):
    old = os.environ.get("BOOKCLUB_DATA_DIR")
    os.environ["BOOKCLUB_DATA_DIR"] = str(d)
    return old


def _restore(old):
    if old is None:
        os.environ.pop("BOOKCLUB_DATA_DIR", None)
    else:
        os.environ["BOOKCLUB_DATA_DIR"] = old


def test_read_rows_utf8_bom_big5():
    with tempfile.TemporaryDirectory() as d:
        for name, data in (("plain.csv", PRON.encode("utf-8")), ("bom.csv", PRON.encode("utf-8-sig")),
                           ("big5.csv", PRON.encode("cp950"))):
            p = Path(d) / name
            p.write_bytes(data)
            rows = csvfile.read_rows(p)
            assert rows == [{"原字": "愉快", "生成用": "魚快", "原因": "測試用", "建立日期": "2026-10-03"}], (name, rows)


def test_unreadable_encoding_says_which_file_and_how_to_fix():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "發音對照表.csv"
        p.write_bytes(PRON.encode("utf-16"))   # 不是 UTF-8 也不是 Big5
        try:
            csvfile.read_rows(p)
            raise AssertionError("應該報錯")
        except csvfile.CsvEncodingError as exc:
            msg = str(exc)
            assert "發音對照表.csv" in msg and "UTF-8" in msg, msg


def test_pron_table_big5_and_bom():
    with tempfile.TemporaryDirectory() as d:
        for enc in ("cp950", "utf-8-sig"):
            p = Path(d) / f"發音對照表_{enc}.csv"
            p.write_bytes(PRON.encode(enc))
            assert tts.load_pron_table(p) == [("愉快", "魚快")], enc


def test_roster_gender_big5_and_bom():
    with tempfile.TemporaryDirectory() as d:
        old = _with_data_dir(Path(d))
        try:
            for enc in ("cp950", "utf-8-sig"):
                (Path(d) / "名冊.csv").write_bytes(ROSTER.encode(enc))
                assert students.roster_gender("林測試") == "男", enc
                assert students.roster_gender("小測") == "男", enc
                assert students.roster_gender("陳虛構") == "女", enc
                assert students.roster_gender("不存在") is None, enc
        finally:
            _restore(old)


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
