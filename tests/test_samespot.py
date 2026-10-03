"""10-03 第八批（#12）：同一處名字只出一張卡。

用 tests/fake_workdir.py 的合成資料（假名字、電子音），不呼叫 Claude／Groq、不載入模型。

獨立可跑：.venv/bin/python tests/test_samespot.py
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

import fake_workdir  # noqa: E402

# 名冊：「小美」同時是第 2 列的中文名、第 4 列的其他寫法（同一個寫法出現在兩列）
_DATA = Path(tempfile.mkdtemp())
(_DATA / "名冊.csv").write_text("中文名,其他寫法,英文代號,聲線,性別\n小美,,Amy,,女\n阿明,,Tom,,男\n林小美,小美,Mia,,女\n"
                               "曉美,,Sue,,女\n", encoding="utf-8")
os.environ["BOOKCLUB_DATA_DIR"] = str(_DATA)

from bookclub import nameplan, names, review  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

_ROOT = None


def _fresh() -> Path:
    global _ROOT
    if _ROOT is None:
        _ROOT = Path(tempfile.mkdtemp()) / "base"
        fake_workdir.make(_ROOT)
    d = Path(tempfile.mkdtemp())
    shutil.copytree(_ROOT, d / "base")
    w = d / "base" / "工作區"
    data = json.loads((w / "分析結果.json").read_text(encoding="utf-8"))
    data["video"] = str(d / "base" / "假影片.mp4")
    (w / "分析結果.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return w


def _add_same_spot(w: Path) -> None:
    """第 1 筆（小美／Amy，76.3–76.9）同一處再比中兩位：林小美（名冊另一列的其他寫法，精確）、曉美（讀音相近，A1）。"""
    data = wd.read_json(wd.names_path(w))
    c1 = data["candidates"][0]
    data["candidates"].append({**c1, "name": "小美", "canonical": "林小美", "代號": "Mia"})               # 第 3 筆
    data["candidates"].append({**c1, "name": "曉美", "canonical": "曉美", "代號": "Sue", "比對層級": "A1",
                               "信心": "中"})                                                             # 第 4 筆
    wd.write_json(wd.names_path(w), data)


def _names(w: Path) -> dict[str, dict]:
    return {x["id"]: x for x in review.page_data(w)["項目"] if x["類型"] == "名字"}


def test_same_spot_merged_into_one_card():
    w = _fresh()
    _add_same_spot(w)
    cards = _names(w)
    assert set(cards) == {"1", "2"}, set(cards)                       # 4 筆候選、2 張卡
    also = [(a["canonical"], a["代號"]) for a in cards["1"]["也可能是"]]
    assert also == [("林小美", "Mia"), ("曉美", "Sue")] and cards["1"]["代號"] == "Amy"
    plan = nameplan.compute_plan(w)
    assert not plan["要人處理"], plan["要人處理"]
    same = {str(s["候選"]): s.get("同一處") for s in plan["略過"] if s.get("同一處")}
    assert same == {"3": "1", "4": "1"}
    assert sum(1 for g in plan["生成"] if "Amy" in g["text"]) == 1

    # 第 1 張按通過：不會被同一處的其他筆擋住（以前第 2 張「句子裡找不到比對到的字」）
    review.save_name(w, "1", {"已確認": True})
    assert _names(w)["1"]["已確認"]

    # 人選另一位：代號換成那一位的，也可能是改列原本那一位；選空白回到自動選的
    review.save_name(w, "1", {"選的人": "曉美"})
    c = _names(w)["1"]
    assert c["代號"] == "Sue" and c["選的人"] == "曉美" and ("小美", "Amy") in [(a["canonical"], a["代號"]) for a in c["也可能是"]]
    assert any("Sue" in g["text"] for g in nameplan.compute_plan(w)["生成"])
    review.save_name(w, "1", {"選的人": ""})
    assert _names(w)["1"]["代號"] == "Amy"


def test_old_workdir_confirmed_decisions_stay():
    """已經做過的工作區：同一處有一筆已經按過通過（第 3 筆），照第 1 點合併顯示，已確認的決定不動、不錯位。"""
    w = _fresh()
    _add_same_spot(w)
    dp = review.name_decisions_path(w)
    wd.write_json(dp, {"3": {"tags": [], "note": "", "做法": "整句換掉", "已確認": True, "改稿": "剛剛Mia分享得很好，我們再多聽一點。"},
                       "4": {"tags": [], "note": "", "已確認": False}})
    before = json.loads(dp.read_text(encoding="utf-8"))
    cards = _names(w)
    assert set(cards) == {"2", "3"}, set(cards)                         # 已通過的那一筆當主卡，其他併進去
    assert cards["3"]["已確認"] and cards["3"]["做法"] == "整句換掉" and cards["3"]["代號"] == "Mia"
    plan = nameplan.compute_plan(w)
    assert not plan["要人處理"] and {str(s["候選"]) for s in plan["略過"] if s.get("同一處")} == {"1", "4"}
    after = json.loads(dp.read_text(encoding="utf-8"))
    for k in ("3", "4"):                                                # 決定內容不動（只補候選指紋）
        assert {kk: vv for kk, vv in after[k].items() if kk != "候選指紋"} == before[k]
    assert after["3"]["候選指紋"] == review.name_fingerprint(wd.read_json(wd.names_path(w))["candidates"][2])

    # 兩筆都已經按過通過：兩張都留著（不併已確認的）
    review.save_name(w, "1", {"已確認": True})
    assert {"1", "3"} <= set(_names(w))


def test_covered_card_auto_handled_and_back():
    """同一句已經被另一張整句換掉（已通過）→ 這張自動標涵蓋、不用按；那一張退回或改做法 → 回到要處理。"""
    w = _fresh()
    data = wd.read_json(wd.names_path(w))
    c1 = data["candidates"][0]
    s20 = next(s for s in wd.read_json(wd.speakers_path(w))["sentences"] if s["id"] == "00_020")
    # 第 3 筆：同一個完整句子的後半句，抓到的字跟句子文字寫法不一樣（找不到比對到的字）
    data["candidates"].append({**c1, "start": s20["start"] + 0.8, "end": s20["start"] + 1.3, "sentence_id": "00_020",
                               "sentence": s20["text"], "name": "在多", "canonical": "在多", "代號": "Zed",
                               "matched_text": "在多", "位置": "句中"})
    wd.write_json(wd.names_path(w), data)
    plan = nameplan.compute_plan(w)
    assert [str(m["候選"]) for m in plan["要人處理"]] == ["3"]          # 還沒有人通過第 1 張：照舊要人處理
    assert "涵蓋" not in _names(w)["3"]

    review.save_name(w, "1", {"已確認": True})
    c3 = _names(w)["3"]
    assert c3["涵蓋"]["id"] == "1" and review.progress([c3], {}, 0)["已確認"] == 1
    plan = nameplan.compute_plan(w)
    assert not plan["要人處理"] and plan["涵蓋"] == {"3": "S001"}
    g = next(g for g in plan["生成"] if g["id"] == "S001")
    assert 3 in g["候選"] and 1 in g["候選"]
    review.save_name(w, "3", {"已確認": True})                          # 按通過也不會被擋

    review.save_name(w, "3", {"已確認": False})
    review.save_name(w, "1", {"做法": "直接消音"})                       # 那一張改做法 → 回到要處理
    assert "涵蓋" not in _names(w)["3"]
    assert [str(m["候選"]) for m in nameplan.compute_plan(w)["要人處理"]] == ["3"]
    review.save_name(w, "1", {"做法": "整句換掉"})
    assert _names(w)["3"].get("涵蓋")
    review.save_name(w, "1", {"已確認": False})                          # 那一張退回 → 回到要處理
    assert "涵蓋" not in _names(w)["3"]


def test_roster_duplicate_spellings_warning():
    dups = names.roster_duplicate_spellings(_DATA / "名冊.csv")
    assert dups == [{"寫法": "小美", "列": [2, 4]}]
    w = _fresh()
    assert review.page_data(w)["名冊重複寫法"] == dups
    js = (REPO_ROOT / "bookclub" / "web" / "review.js").read_text(encoding="utf-8")
    assert "名冊重複寫法" in js and "也可能是" in js


def test_same_spot_groups_pure():
    base = {"sentence_id": "a", "start": 1.0, "end": 1.5, "matched_text": "小美", "敏感詞": False}
    cands = [{**base, "name": "曉美", "比對層級": "A1"}, {**base, "name": "小美", "比對層級": "精確"},
             {**base, "name": "小梅", "比對層級": "A1"}, {**base, "start": 3.0, "end": 3.5, "name": "小美", "比對層級": "精確"},
             {**base, "name": "小美", "比對層級": "精確", "敏感詞": True}]
    assert review.same_spot_groups(cands, {}) == {1: 2, 3: 2}                  # 精確優先；別處、敏感詞不併
    assert review.same_spot_groups(cands, {"3": {"已確認": True}}) == {1: 3, 2: 3}   # 已通過的當主卡
    assert review.same_spot_groups(cands, {"1": {"已確認": True}, "3": {"已確認": True}}) == {2: 1}


def _run_all() -> int:
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, func in tests:
        try:
            func()
            print(f"✓ {name}")
        except Exception as exc:
            import traceback

            traceback.print_exc()
            failed += 1
            print(f"✗ {name}：{exc!r}")
    print(f"{len(tests) - failed}/{len(tests)} 通過")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
