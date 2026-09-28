"""網頁（`bookclub/web/`）的靜態檢查：不開瀏覽器，只讀檔案確認幾個定案過的規則沒被改掉。

互動行為（收合、存檔、點按鈕）用 `tests/fake_workdir.py` 的假工作區開伺服器實際點過，見 CHANGELOG；
這支只擋「改版時不小心把定案拿掉」：步驟列每步的 AI／人工、localStorage 讀寫都包 try/catch。

獨立可跑：.venv/bin/python tests/test_web.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB = REPO_ROOT / "bookclub" / "web"


def _step_defs() -> list[tuple[int, str]]:
    js = (WEB / "app.js").read_text(encoding="utf-8")
    block = js[js.index("const STEP_DEFS = ["):]
    block = block[:block.index("];")]
    return [(int(n), who) for n, who in re.findall(r'num: (\d+),.*?who: "(AI|人工)"', block)]


def test_steps_marked_ai_or_human():
    # 09-29 宇軒：0 人工、1 AI、2 人工、3 人工、4 AI、5 人工
    got = dict(_step_defs())
    want = {0: "人工", 1: "AI", 2: "人工", 3: "人工", 4: "AI", 5: "人工"}
    for n, who in want.items():
        assert got.get(n) == who, (n, got.get(n))


def test_local_storage_wrapped_in_try():
    # 私密視窗、封鎖網站資料時 localStorage 會丟例外；讀不到要當作預設值，不能讓整頁掛掉
    for name in ("app.js", "review.js", "finalcheck.js"):
        path = WEB / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if "localStorage." in line:
                assert "try" in line, f"{name}：{line.strip()}"


def test_nav_toggle_exists():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'id="navToggle"' in html


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
    sys.exit(0)
