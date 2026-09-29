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


def test_code_change_updates_written_texts():
    # 09-29：② 改代號後，已經寫進校對稿、老師名字改稿裡的舊代號跟著換；共用的舊代號不自動換、改回還沒確認
    root = Path(tempfile.mkdtemp()) / "base"
    fake_workdir.make(root)
    w = root / "工作區"
    tp = w / "校對" / "段落.json"
    data = wd.read_json(tp)
    data["本名代號"] = {"小美": "Grace"}
    t0, t1 = data["段落"][0], data["段落"][1]
    t0.update({"校對稿": "我是Grace，不是Gracey。", "已確認": True})
    t1.update({"校對稿": "Grace說得對。", "已確認": True})
    wd.write_json(tp, data)
    wd.write_json(w / "名字覆核決定.json", {"1": {"改稿": "謝謝Grace的分享"}})
    epcodes.sync(w)                                    # 第一次：記下代號表
    data = wd.read_json(tp)
    data["本名代號"]["小美"] = "Amy"
    wd.write_json(tp, data)
    epcodes.sync(w)
    data = wd.read_json(tp)
    assert data["段落"][0]["校對稿"] == "我是Amy，不是Gracey。" and data["段落"][0]["已確認"]
    assert data["段落"][1]["校對稿"] == "Amy說得對。"
    assert wd.read_json(w / "名字覆核決定.json")["1"]["改稿"] == "謝謝Amy的分享"
    # 舊代號還有別人在用 → 不換，改回還沒確認
    data["本名代號"] = {"小美": "Zoe", "阿明": "Amy"}
    data["段落"][1]["已確認"] = True
    wd.write_json(tp, data)
    epcodes.sync(w)
    data = wd.read_json(tp)
    assert data["段落"][1]["校對稿"] == "Amy說得對。" and not data["段落"][1]["已確認"]


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
