"""bookclub/codeswap.py（10-02 第七批，任務單 26 號第一段）：把這一集的英文代號換成中文。

假工作區（tests/fake_workdir.py）＋假名冊（小美＝Amy、阿明＝Tom，舊名冊還有代號欄的樣子），
每一種存代號的地方、每一種要念的文字都放一個英文代號，換完之後這一集裡舊代號 0 處。不載入模型。

獨立可跑：.venv/bin/python tests/test_codeswap.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

_DATA = Path(tempfile.mkdtemp())
(_DATA / "名冊.csv").write_text("中文名,其他寫法,英文代號,聲線,性別\n小美,,Amy,,女\n阿明,,Tom,,男\n", encoding="utf-8")
os.environ["BOOKCLUB_DATA_DIR"] = str(_DATA)

import fake_workdir  # noqa: E402

from bookclub import codeswap, epcodes, execute, personnames, review, studentnames  # noqa: E402
from bookclub import turns as turns_mod  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

_ROOT = None


def _fresh() -> Path:
    global _ROOT
    if _ROOT is None:
        _ROOT = Path(tempfile.mkdtemp()) / "base"
        fake_workdir.make(_ROOT)
    d = Path(tempfile.mkdtemp())
    shutil.copytree(_ROOT, d / "base")
    return d / "base" / "工作區"


def _seed(w: Path) -> None:
    """每一種存代號的地方、每一種要念的文字都放英文代號（假名、自己編的句子）。"""
    tp = turns_mod.turns_path(w)
    t = wd.read_json(tp)
    t["本名代號"] = {"阿華": "Emma", "阿珍": "joan"}
    stu = next(iter(t["學員"]))
    t["學員"][stu].update({"本名": "阿華", "代號": "Emma"})
    t["段落"][2].update({"校對稿": "我是Emma，Roseanne 和 Emmanuel 不是代號。EMMA 也要換。", "建議稿": "joan說得對"})
    wd.write_json(tp, t)
    wd.write_json(personnames.decisions_path(w), {"阿強": {"做法": "換成代號", "代號": "Rose", "同一人": None}})
    names = wd.read_json(wd.names_path(w))
    names["candidates"][0]["代號"] = "Emma"
    wd.write_json(wd.names_path(w), names)
    wd.write_json(studentnames.cands_path(w), {"candidates": [{"id": "SN1", "start": 50.0, "end": 50.4, "canonical": "阿珍", "代號": "Joan"}]})
    wd.write_json(review.name_decisions_path(w), {"1": {"做法": "整句換掉", "改稿": "謝謝Rose的分享，Rosemary 不用換。"}})
    dec = review.load_decisions(w)
    dec["重疊"]["O69.60"] = {"做法": "生成老師聲音", "老師文字": "Emma你說", "學員文字": "好的Joan", "老師整句改稿": "請Rose接著"}
    dec["人工名字"].append({"id": "NM001", "start": 120.0, "end": 123.0, "代號": "Joan", "老師整段": True,
                         "整段文字": "Joan和Emma先分享"})
    review._save_decisions(w, dec)
    wd.write_json(epcodes.snapshot_path(w), {"阿華": "Emma"})


def test_whole_word_and_case():
    assert codeswap.replace_in("Rose、Roseanne、rose，ROSE。", "Rose", "蘿絲") == ("蘿絲、Roseanne、蘿絲，蘿絲。", 3)
    assert codeswap.replace_in("Emmanuel", "Emma", "艾瑪") == ("Emmanuel", 0)
    assert codeswap.count_in("我是Emma", "emma") == 1
    assert codeswap.parse_map(["Joan=潔西", "Emma=艾瑪，Rose=蘿絲"]) == {"Joan": "潔西", "Emma": "艾瑪", "Rose": "蘿絲"}


def test_plan_lists_codes_with_suggestions():
    w = _fresh()
    _seed(w)
    rows = {r["舊"].lower(): r for r in codeswap.plan(w)["英文代號"]}
    assert {"emma", "joan", "rose", "amy", "tom"} <= set(rows), rows
    assert rows["emma"]["建議"] == "艾瑪" and rows["rose"]["建議"] == "蘿絲" and rows["tom"]["建議"] == "湯姆"
    assert rows["joan"]["建議"] is None and rows["amy"]["建議"] is None   # 舊名單沒有對應，要人選
    assert rows["emma"]["要念的文字"] >= 4 and rows["emma"]["存代號的地方"] >= 3


def test_mapping_rules():
    w = _fresh()
    _seed(w)
    try:
        codeswap.resolve_mapping(w, {})
        raise AssertionError("沒有對應的代號沒選也能換")
    except ValueError as e:
        assert "Joan" in str(e) or "joan" in str(e)
    try:
        codeswap.resolve_mapping(w, {"Joan": "艾瑪", "Amy": "安娜"})   # Emma 預設也是艾瑪
        raise AssertionError("兩個人換成同一個代號沒擋")
    except ValueError as e:
        assert "同一個代號" in str(e)
    try:
        codeswap.resolve_mapping(w, {"Joan": "Jessie", "Amy": "安娜"})
        raise AssertionError("換成英文沒擋")
    except ValueError as e:
        assert "中文" in str(e)
    try:
        codeswap.resolve_mapping(w, {"Lily": "露西"})
        raise AssertionError("這一集沒有的代號沒擋")
    except ValueError:
        pass
    mp = codeswap.resolve_mapping(w, {"joan": "潔西", "Amy": "安娜"})
    assert {k.lower(): v for k, v in mp.items()} == {"emma": "艾瑪", "joan": "潔西", "rose": "蘿絲", "amy": "安娜", "tom": "湯姆"}


def test_apply_replaces_everywhere_with_backup_and_check():
    w = _fresh()
    _seed(w)
    # 換之前：總檢查有「要念的文字裡還有英文代號」
    fc = execute.final_check(w)
    keys = [r["key"] for r in fc["一定要處理"] if r["key"].startswith("英文代號:")]
    assert any("學員段落:" in k for k in keys) and any("重疊:O69.60" in k for k in keys) and any("名字:NM001" in k for k in keys)
    assert any("名字:1" in k for k in keys)
    before = {p: p.read_bytes() for p in (turns_mod.turns_path(w), review.review_path(w))}
    dry = codeswap.apply(w, {"Joan": "潔西", "Amy": "安娜"}, dry_run=True)
    assert dry["備份"] is None and all(p.read_bytes() == b for p, b in before.items())
    res = codeswap.apply(w, {"Joan": "潔西", "Amy": "安娜"})
    assert res["每個代號換了幾處"]["Emma"] >= 7 and res["還剩"] == []
    # 備份：會改到的檔都在，內容是換之前的
    bdir = w / res["備份"]
    assert (bdir / "校對" / "段落.json").read_bytes() == before[turns_mod.turns_path(w)]
    assert (bdir / "覆核" / "覆核決定.json").read_bytes() == before[review.review_path(w)]
    # 存代號的地方
    t = wd.read_json(turns_mod.turns_path(w))
    assert t["本名代號"] == {"阿華": "艾瑪", "阿珍": "潔西"}
    assert any(p.get("代號") == "艾瑪" for p in t["學員"].values())
    assert wd.read_json(personnames.decisions_path(w))["阿強"]["代號"] == "蘿絲"
    assert wd.read_json(studentnames.cands_path(w))["candidates"][0]["代號"] == "潔西"
    dec = review.load_decisions(w)
    nm = next(m for m in dec["人工名字"] if m["id"] == "NM001")
    assert nm["代號"] == "潔西"
    # 要念的文字（整個詞：Roseanne、Emmanuel、Rosemary 不動）
    assert t["段落"][2]["校對稿"] == "我是艾瑪，Roseanne 和 Emmanuel 不是代號。艾瑪 也要換。"
    assert t["段落"][2]["建議稿"] == "潔西說得對"
    assert wd.read_json(review.name_decisions_path(w))["1"]["改稿"] == "謝謝蘿絲的分享，Rosemary 不用換。"
    o = dec["重疊"]["O69.60"]
    assert (o["老師文字"], o["學員文字"], o["老師整句改稿"]) == ("艾瑪你說", "好的潔西", "請蘿絲接著")
    assert nm["整段文字"] == "潔西和艾瑪先分享"
    # 名冊上來的代號（小美＝Amy、阿明＝Tom）：名冊不動，這一集改記在人名決定
    assert "Amy" in (_DATA / "名冊.csv").read_text(encoding="utf-8")
    codes = epcodes.episode_codes(w)
    assert codes["小美"] == "安娜" and codes["阿明"] == "湯姆" and not any(codeswap.is_english(c) for c in codes.values())
    # 這一集裡舊代號 0 處、總檢查那一項消失、代號表紀錄跟著新的
    assert codeswap.plan(w)["英文代號"] == [] and codeswap.leftover_texts(w) == []
    assert not [r for r in execute.final_check(w)["一定要處理"] if r["key"].startswith("英文代號:")]
    assert wd.read_json(epcodes.snapshot_path(w)) == codes
    names = wd.read_json(wd.names_path(w))
    assert all(not codeswap.is_english(c.get("代號")) for c in names["candidates"] if not c.get("敏感詞"))


def test_new_code_already_used_is_blocked():
    w = _fresh()
    _seed(w)
    t = wd.read_json(turns_mod.turns_path(w))
    t["本名代號"]["阿德"] = "潔西"   # 這一集已經有人用潔西
    wd.write_json(turns_mod.turns_path(w), t)
    try:
        codeswap.resolve_mapping(w, {"Joan": "潔西", "Amy": "安娜"})
        raise AssertionError("新代號撞到這一集已經有人用的沒擋")
    except ValueError as e:
        assert "已經有人用" in str(e)


def test_cli_convert():
    from bookclub import cli

    w = _fresh()
    _seed(w)
    assert cli.main(["codes", "convert", str(w)]) == 2          # Joan、Amy 沒給對照：不換
    assert codeswap.plan(w)["英文代號"]
    assert cli.main(["codes", "convert", str(w), "--map", "Joan=潔西", "--map", "Amy=安娜", "--dry-run"]) == 0
    assert codeswap.plan(w)["英文代號"]
    assert cli.main(["codes", "convert", str(w), "--map", "Joan=潔西,Amy=安娜"]) == 0
    assert codeswap.plan(w)["英文代號"] == []
    assert cli.main(["codes", "convert", str(w)]) == 0           # 沒有英文代號了


def test_review_page_shows_english_codes():
    w = _fresh()
    _seed(w)
    data = review.page_data(w)
    assert {r["舊"].lower() for r in data["英文代號換中文"]} >= {"emma", "joan"}
    assert data["代號分組"]["女"][0] == "安娜" and "傑克" in data["代號分組"]["男"]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
