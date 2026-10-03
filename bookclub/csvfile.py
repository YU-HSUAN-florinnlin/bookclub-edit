"""讀 CSV 共用（10-03 第九批 #35）：Excel 另存的 CSV 常是 Big5（cp950），不是 UTF-8。

先用 UTF-8（有沒有 BOM 都可以）讀，讀不了再試 Big5（cp950）；兩種都不對才報錯，
錯誤訊息說是哪個檔、請另存成「CSV UTF-8」。只讀不寫：讀到 Big5 也不幫使用者改檔。
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

ENCODINGS = ("utf-8-sig", "cp950")


class CsvEncodingError(ValueError):
    """CSV 的文字編碼不是 UTF-8 也不是 Big5。"""


def read_text(path: str | Path) -> str:
    """把 CSV 檔讀成文字：UTF-8（含 BOM）→ Big5（cp950）→ 都不行就報看得懂的錯。"""
    path = Path(path)
    data = path.read_bytes()
    for enc in ENCODINGS:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise CsvEncodingError(
        f"讀不懂「{path.name}」的文字編碼（不是 UTF-8 也不是 Big5）。"
        f"請用 Excel 打開，另存新檔時檔案類型選「CSV UTF-8（逗號分隔）」再試一次。檔案位置：{path}")


def read_rows(path: str | Path) -> list[dict]:
    """讀成 csv.DictReader 的每一列（第一列是表頭）。"""
    return list(csv.DictReader(io.StringIO(read_text(path), newline="")))
