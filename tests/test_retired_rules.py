"""10-15 總檢查修正（1015-7）：第 5 步 2.0「已作廢」的規則不能再出現在程式、畫面文字、說明文件裡。

依據：`drafts/2026-10-15-讀書會剪輯-第5步2.0-現行定案清單.md` 最後一節「已作廢」（宇軒的 AI 助理資料夾，不在這個 repo）。
每一條作廢規則對一組要找的字串，掃 `bookclub/`（.py、.js、.css、.html）、`README.md`、`docs/*.md`、`CHANGELOG.md`；找到就失敗，
並印出檔案、行號、那一行。

白名單（不算違規）：
- CHANGELOG.md 已發布版本的段落（第一個「## 0.2.1b3」以下）全部是歷史；未發布段落裡寫「…改成…」「拿掉」「作廢」「以前」「→」
  這種講變更經過的那一行也算歷史。
- 資料欄位名 `這一版沒處理到`、`上一版已通過`：寫成 `["欄位"]`、`"欄位":`、`.get("欄位"`、`` `欄位` ``（文件裡的欄位名）不算；
  寫成「…」、標籤文字、註解裡的白話都算。
- 「還沒看完」只抓分頁名稱的寫法（「還沒看完」、"還沒看完"）；`FC_TAB_GONE` 那一行是把瀏覽器記住的舊分頁改回「全部」，要留著。
- tests/ 不掃（測試裡有「不該出現」的斷言字串、假資料的欄位名）；這一支自己也不掃。
獨立可跑：.venv/bin/python tests/test_retired_rules.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# （作廢的規則, [要找的字串（正規表示式）]）
RETIRED = [
    ("舊版、上一版還沒看的那一區只能通過不能退回", [r"只能通過"]),
    ("「要修改請回到最新一版」這句提示", [r"回到最新一版"]),
    ("「退回」「改」兩顆分開的按鈕（舊的取消鈕、舊的判斷函式）", [r"取消退回", r"fcCanReturn"]),
    ("「這一版沒處理到」「上一版已通過」標籤文字", [r"這一版沒處理到", r"上一版已通過"]),
    ("「還沒看完」分頁", [r"「還沒看完」", r"\"還沒看完\"", r"FC_TAB_TODO"]),
    ("「上一版還沒看的（第 N 版）」獨立分組的那一區", [r"上一版還沒看的", r"fcCross\("]),
]
FIELDS = ("這一版沒處理到", "上一版已通過")
FIELD_OK = [r'\[\s*"{f}"\s*\]', r'"{f}"\s*:', r'\.get\(\s*"{f}"', r"`{f}(?=[`:：\s])", r'"{f}"\s*(,|\))']
HISTORY_MARK = re.compile(r"改成|拿掉|作廢|以前|→|不再")


def _files() -> list[Path]:
    out = [p for p in (REPO_ROOT / "bookclub").rglob("*") if p.suffix in (".py", ".js", ".css", ".html") and p.is_file()]
    out += [REPO_ROOT / "README.md", REPO_ROOT / "CHANGELOG.md"]
    out += sorted((REPO_ROOT / "docs").glob("*.md"))
    return [p for p in out if p.exists()]


def _strip_fields(line: str) -> str:
    """資料欄位名的寫法換成空白（剩下的才算）。"""
    for f in FIELDS:
        for pat in FIELD_OK:
            line = re.sub(pat.format(f=re.escape(f)), " ", line)
    return line


def _history_lines(path: Path, lines: list[str]) -> set[int]:
    """CHANGELOG 的歷史行（行號從 1 起）。"""
    if path.name != "CHANGELOG.md":
        return set()
    first_old = next((i for i, x in enumerate(lines) if x.startswith("## ") and "未發布" not in x and i > 0), len(lines))
    keep = set(range(first_old + 1, len(lines) + 1))
    keep |= {i + 1 for i, x in enumerate(lines[:first_old]) if HISTORY_MARK.search(x)}
    return keep


def find_retired(files: list[Path] | None = None) -> list[tuple[str, str, int, str]]:
    """回傳 [(作廢規則, 檔案, 行號, 那一行)]。"""
    hits = []
    for p in files or _files():
        lines = p.read_text(encoding="utf-8").splitlines()
        hist = _history_lines(p, lines)
        for i, line in enumerate(lines, 1):
            if i in hist or "FC_TAB_GONE" in line:
                continue
            body = _strip_fields(line)
            for rule, pats in RETIRED:
                if any(re.search(x, body) for x in pats):
                    hits.append((rule, str(p.relative_to(REPO_ROOT)), i, line.strip()[:160]))
    return hits


def test_no_retired_rules():
    hits = find_retired()
    if hits:
        msg = "\n".join(f"  {f}:{n}（{rule}）{line}" for rule, f, n, line in hits)
        raise AssertionError(f"作廢的規則還在（{len(hits)} 處）：\n{msg}")


def test_whitelist_rules():
    """白名單本身：欄位名的寫法不算、標籤文字算；CHANGELOG 歷史段落不算。"""
    assert _strip_fields('c["這一版沒處理到"] && x.get("上一版已通過") `這一版沒處理到`').count("這一版沒處理到") == 0
    assert "上一版已通過" in _strip_fields('const FC_OUT_PASSED = "上一版已通過"；標「上一版已通過」')
    lines = ["# 變更紀錄", "## 0.2.2b1（未發布）", "- 標籤「上一版已通過」改成「已通過（沿用上一版）」", "- 只能通過", "## 0.2.1b3", "- 只能通過"]
    assert _history_lines(Path("CHANGELOG.md"), lines) == {3, 5, 6}


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
