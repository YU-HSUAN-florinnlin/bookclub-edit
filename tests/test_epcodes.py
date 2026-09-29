"""bookclub/epcodes.py（這一集的代號表）的測試：名冊沒有英文代號欄（09-29 宇軒決定拿掉）。

獨立可跑：.venv/bin/python tests/test_epcodes.py
"""

from __future__ import annotations

import csv
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import fake_workdir  # noqa: E402

_DATA = Path(tempfile.mkdtemp())
(_DATA / "名冊.csv").write_text("中文名,其他寫法,性別\n小美,美美,女\n阿明,Tom,男\n", encoding="utf-8")
os.environ["BOOKCLUB_DATA_DIR"] = str(_DATA)

from bookclub import epcodes, personnames  # noqa: E402
from bookclub import workdir as wd  # noqa: E402


def test_no_roster_codes_then_auto_assign():
    root = Path(tempfile.mkdtemp()) / "base"
    fake_workdir.make(root)
    w = root / "工作區"
    assert epcodes.episode_codes(w) == {}
    epcodes.sync(w)   # 名字候選裡原本手寫的代號清掉：這一集還沒選
    cands = [c for c in wd.read_json(wd.names_path(w))["candidates"] if not c.get("敏感詞")]
    assert cands and all(c["代號"] == "" for c in cands)
    lack = epcodes.missing(w)
    assert lack, "老師講到的名冊名字沒有代號，第 4 步開始前要擋"
    r = epcodes.auto_assign(w)
    assert r["③"] >= 1 and epcodes.missing(w) == []
    codes = epcodes.episode_codes(w)
    assert len(set(codes.values())) == len(codes) and set(codes.values()) <= set(epcodes.CODE_POOL)
    assert all(c["代號"] for c in wd.read_json(wd.names_path(w))["candidates"] if not c.get("敏感詞"))
    # 選單：常用英文名＋這一集自己打過的
    assert "Grace" in epcodes.code_options(w)


def test_add_to_roster_without_code_column():
    assert personnames.add_to_roster("阿強", ["強強"], "Kevin")
    with open(_DATA / "名冊.csv", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0].keys()) == ["中文名", "其他寫法", "性別"]
    assert rows[-1]["中文名"] == "阿強" and rows[-1]["其他寫法"] == "強強"


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
