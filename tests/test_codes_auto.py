"""10-07 第 3 步代號：選了學員是誰就自動配、依性別配、撞名檢查、「代號對照」抽屜、卡片標題「本名（代號）」的測試。

全部假資料（`tests/fake_workdir.py` ＋假名冊），不跑模型：聲音判斷男女用寫好的假基頻。
獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_codes_auto.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import fake_workdir  # noqa: E402

_DATA = Path(tempfile.mkdtemp())
# 安娜（代號名單第一個女生名）剛好是名冊上一個人的名字：自動配要跳過、手動選要警告
(_DATA / "名冊.csv").write_text("中文名,其他寫法,性別\n小美,美美,女\n阿明,,男\n阿華,,\n阿玉,,\n安娜,,女\n", encoding="utf-8")
os.environ["BOOKCLUB_DATA_DIR"] = str(_DATA)

from bookclub import epcodes, personnames, review, students  # noqa: E402
from bookclub import turns as turns_mod  # noqa: E402
from bookclub import workdir as wd  # noqa: E402


def _new() -> Path:
    root = Path(tempfile.mkdtemp()) / "base"
    fake_workdir.make(root)
    return root / "工作區"


def _pitch(w: Path, table: dict) -> None:
    wd.write_json(students.voices_path(w), {"版本": 2, "學員": {}, "音高": table})


def test_guess_gender_threshold():
    assert students.guess_gender(118) == ("男", "高")
    assert students.guess_gender(140) == ("男", "中")
    assert students.guess_gender(155)[0] is None and students.guess_gender(178)[0] is None   # 165 ± 15 不確定
    assert students.guess_gender(185) == ("女", "中") and students.guess_gender(220) == ("女", "高")
    assert students.guess_gender(None) == (None, "估不出來")
    # 10-07 以前存的只有 hz：照 hz 現算；新存的直接用欄位
    assert students.pitch_gender({"hz": 120}) == ("男", "高")
    assert students.pitch_gender({"hz": 120, "推測性別": None, "信心": "不確定"}) == (None, "不確定")


def test_initial_auto_once_by_roster_gender_and_no_clash():
    w = _new()
    r = epcodes.auto_initial(w)
    assert r["③"] == 2
    codes = epcodes.episode_codes(w)
    assert codes["小美"] == "貝拉", "女生：名單第一個「安娜」跟名冊上的人撞名，要跳過"
    assert codes["阿明"] == "傑克", "男生從男生名單配"
    assert epcodes.unconfirmed(w) == {"小美": "貝拉", "阿明": "傑克"}
    rows = {u["名字"]: u for u in personnames.mentioned(w)}
    assert rows["小美"]["自動配"] and not rows["小美"]["已決定"], "自動配的要人確認（③ 還不能算做完）"
    # 名字候選的代號跟著對齊
    assert {c["canonical"]: c["代號"] for c in wd.read_json(wd.names_path(w))["candidates"]} == {"小美": "貝拉", "阿明": "傑克"}
    # 人改了 → 不再是自動配；再進一次第 3 步不會蓋回去
    personnames.decide(w, "小美", "換成代號", "克洛伊")
    assert epcodes.auto_initial(w)["已配過"]
    assert epcodes.episode_codes(w)["小美"] == "克洛伊"
    assert "小美" not in epcodes.unconfirmed(w)
    # 「就用這個」：拿掉還沒確認
    epcodes.clear_auto(w, "阿明")
    assert epcodes.unconfirmed(w) == {}
    assert {u["名字"]: u["已決定"] for u in personnames.mentioned(w)}["阿明"]


def test_initial_waits_for_people_list():
    w = _new()
    (w / "校對" / "人名清單.json").unlink()
    (w / "名字候選.json").unlink()
    assert epcodes.auto_initial(w).get("還不能配")
    assert not (epcodes.auto_path(w).exists() and wd.read_json(epcodes.auto_path(w)).get("開始前③已配"))


def test_pick_real_name_auto_assigns_by_voice():
    w = _new()
    _pitch(w, {"學員1": {"hz": 210}, "學員2": {"hz": 118}})
    res = turns_mod.set_real_name(w, "學員2", "阿華")   # 名冊沒填性別 → 看聲音：118 Hz 男
    assert res["自動配代號"] == "傑克" and res["代號"] == "傑克"
    assert epcodes.person_gender(w, "阿華") == ("男", "聲音推測（信心高）")
    assert epcodes.unconfirmed(w) == {"阿華": "傑克"}
    # 人在右欄改 → 標記拿掉
    turns_mod.set_name_code(w, "阿華", "湯姆")
    assert epcodes.unconfirmed(w) == {}
    # 已經有代號的本名不重配
    assert turns_mod.set_real_name(w, "學員2", "阿華")["自動配代號"] is None
    # 名冊有性別照名冊（小美 女）
    assert turns_mod.set_real_name(w, "學員1", "小美")["自動配代號"] == "貝拉"


def test_uncertain_voice_uses_both_lists_and_release():
    w = _new()
    _pitch(w, {"學員1": {"hz": 168}, "學員2": {"hz": 118}})
    turns_mod.set_real_name(w, "學員1", "阿玉")
    assert epcodes.person_gender(w, "阿玉") == (None, "聲音不確定")
    assert epcodes.pick_code(w, None, {f"x{i}": c for i, c in enumerate(epcodes.CODE_POOL_F)}) == "傑克", \
        "不確定的兩邊名單都可以"
    assert epcodes.pick_code(w, "男", {f"x{i}": c for i, c in enumerate(epcodes.CODE_POOL_M)}) is None, "男生名單用完回 None"
    # ② 改選別的本名：原本自動配、還沒確認的代號收回來
    first = epcodes.episode_codes(w)["阿玉"]
    turns_mod.set_real_name(w, "學員1", "阿華")
    codes = epcodes.episode_codes(w)
    assert "阿玉" not in codes and codes["阿華"] == first
    assert epcodes.unconfirmed(w) == {"阿華": first}


def test_auto_button_by_gender_and_marks():
    w = _new()
    _pitch(w, {"學員1": {"hz": 210}, "學員2": {"hz": 118}})
    t = wd.read_json(turns_mod.turns_path(w))
    t["學員"]["學員1"]["本名"], t["學員"]["學員2"]["本名"] = "阿玉", "阿華"   # 舊工作區：本名選好、還沒代號
    wd.write_json(turns_mod.turns_path(w), t)
    r = epcodes.auto_assign(w)
    codes = epcodes.episode_codes(w)
    assert r["②"] == 2 and codes["阿玉"] in epcodes.CODE_POOL_F and codes["阿華"] in epcodes.CODE_POOL_M
    assert set(epcodes.unconfirmed(w)) >= {"阿玉", "阿華"}
    assert "安娜" not in codes.values()


def test_code_table_labels_and_clash():
    w = _new()
    wd.write_json(personnames.people_path(w), {"人名": [
        {"id": "P001", "名字": "宜君", "其他寫法": ["怡君"], "是誰": "家人朋友", "說明": "", "名冊本名": None,
         "句子": [], "次數": 1, "老師說": 1, "學員說": 0, "第一次": 30.0}], "模型": "假的"})
    turns_mod.set_real_name(w, "學員1", "小美")
    turns_mod.set_real_name(w, "學員2", "小美")   # 兩位學員 N 同一個本名
    turns_mod.set_name_code(w, "宜君", "露西")
    tab = epcodes.code_table(w)
    rows = {r["本名"]: r for r in tab["對照"]}
    assert rows["小美"]["學員"] == ["學員1", "學員2"] and rows["小美"]["代號"] == "貝拉"
    assert rows["小美"]["來源"] == "② 學員是誰" and rows["小美"]["自動配還沒確認"]
    assert rows["宜君"]["其他寫法"] == ["怡君"] and rows["宜君"]["代號"] == "露西", "同一人幾種寫法合成一列"
    assert "怡君" not in rows
    assert tab["代號給了"]["貝拉"] == ["小美"]
    f = {e["代號"]: e for e in tab["名單"]["女"]}
    assert f["貝拉"]["給了"] == ["小美"] and f["艾瑪"]["給了"] == []
    assert f["安娜"]["撞名"] == ["安娜"] and tab["撞名"]["安娜"] == ["安娜"]
    assert len(tab["名單"]["女"]) == 18 and len(tab["名單"]["男"]) == 11
    assert epcodes.student_labels(w) == {"學員1": "小美（貝拉）・學員1", "學員2": "小美（貝拉）・學員2"}
    # 名冊寫法、人名清單寫法都算撞名
    sp = epcodes.name_spellings(w)
    assert sp["美美"] == "小美" and sp["怡君"] in ("宜君", "怡君")
    # GET /api/review 帶這幾欄（只讀）
    data = review.page_data(w)
    assert data["代號對照"]["對照"] and data["代號自動配還沒確認"]["小美"] == "貝拉"
    assert data["代號開始前已配"] is False


def test_final_labels_in_page_data():
    from bookclub import finalcheck

    w = _new()
    turns_mod.set_real_name(w, "學員1", "小美")
    assert finalcheck.page_data(w)["學員顯示名"] == {"學員1": "小美（貝拉）"}


def test_web_strings():
    js = (REPO_ROOT / "bookclub" / "web" / "review.js").read_text(encoding="utf-8")
    for want in ('id="rv-code-btn"', 'id="rv-codes"', "rvRenderCodeMap", "截圖或分享螢幕時，這裡會露出本名",
                 "/api/codes/initial", "/api/codes/confirm", "自動配的，還沒確認", "rvCodeClashOk", "rvWhoHtml"):
        assert want in js, want
    fcjs = (REPO_ROOT / "bookclub" / "web" / "finalcheck.js").read_text(encoding="utf-8")
    assert "學員顯示名" in fcjs and "fcWho(" in fcjs
    app = (REPO_ROOT / "bookclub" / "web" / "app.js").read_text(encoding="utf-8")
    assert "英文代號" not in app


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
