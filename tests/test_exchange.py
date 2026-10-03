"""bookclub/exchange.py（覆核結果匯出／匯入）10-03 第九批 #36 的測試：匯入的工作區只能做老師提到名字的部分，
說明檔、匯出與匯入的回傳都寫明能做什麼、不能做什麼，以及「交接整個專案請複製整個工作區資料夾」。
用 tests/fake_workdir.py 的假工作區與暫存的 BOOKCLUB_DATA_DIR，不載入模型。

獨立可跑：.venv/bin/python tests/test_exchange.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

_DATA = Path(tempfile.mkdtemp())
(_DATA / "名冊.csv").write_text("中文名,其他寫法,英文代號,聲線,性別\n小美,,Amy,,女\n阿明,,Tom,,男\n", encoding="utf-8")
os.environ["BOOKCLUB_DATA_DIR"] = str(_DATA)

import fake_workdir  # noqa: E402
from bookclub import exchange, execute  # noqa: E402

_W = None


def _workdir() -> Path:
    global _W
    if _W is None:
        root = Path(tempfile.mkdtemp()) / "base"
        fake_workdir.make(root)
        _W = root / "工作區"
    return _W


def test_readme_says_scope_and_whole_folder_handover():
    result = {"影片": {"檔名": "第1堂.mp4", "長度": 60.0, "大小": 1}, "匯出時間": "2026-10-03T10:00:00",
              "工具版本": "x", "未確認數": 0, "未確認各類": {}}
    text = exchange._readme(result)
    assert "只含老師提到名字的處理" in text
    assert "不能做：第 4 步「開始 AI 修改」" in text and "第 5 步" in text
    assert "複製整個工作區資料夾" in text and "匯入你的設定包" in text
    assert "之後的版本直接讀這份" not in text            # 舊說法拿掉


def test_export_returns_scope_and_zip_readme_has_it():
    w = _workdir()
    r = exchange.export_review(w, out=w.parent / "匯出_範圍.zip")
    assert r["用途"] == list(exchange.SCOPE_LINES)
    with zipfile.ZipFile(r["檔案"]) as z:
        readme = z.read(exchange.README_NAME).decode("utf-8")
        names = set(z.namelist())
    assert all(line in readme for line in exchange.SCOPE_LINES)
    # 刻意不帶第 4 步要的原始資料（原始逐字稿、段落分析、名字候選）
    assert not names & {"transcript/merged.json", "校對/段落.json", "名字候選.json"}


def test_imported_workdir_says_what_it_can_do_and_step4_is_blocked():
    w = _workdir()
    r = exchange.export_review(w, out=w.parent / "匯出_匯入.zip")
    video = Path(json.loads((w / "分析結果.json").read_text(encoding="utf-8"))["video"])
    new = w.parent / "夥伴工作區_範圍"
    got = exchange.import_review(r["檔案"], new, video)
    assert got["可以做"] == exchange.SCOPE_CAN and "gen names" in got["可以做"] and "render audio" in got["可以做"]
    assert got["不能做"] == exchange.SCOPE_CANNOT and "複製整個工作區資料夾" in got["交接整個專案"]
    # 寫明的「不能做」跟實際一致：第 4 步的前置檢查擋在缺逐字稿與段落分析
    pre = execute.precheck(new)
    assert not pre["可以開始"]
    assert any("還沒有逐字稿" in m for m in pre["缺"]) and any("段落分析" in m for m in pre["缺"])
    assert (new / exchange.README_NAME).read_text(encoding="utf-8").count("複製整個工作區資料夾") == 1


def _run_all() -> int:
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"✓ {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"✗ {name}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} 通過")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
