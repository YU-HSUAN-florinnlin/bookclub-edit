"""第 5 步 2.0 第 3 段（10-15，批次 1015-2）：版本——成品檔名帶版本號、看哪一版、跨版本的卡片、這一版沒處理到、
輸出任一版、拿檔照 §6.7、上一版試聽、第 3 步選定不修改改成要處理。

用 tests/test_needs.py 的假工作區（合成資料）＋手寫的處理紀錄，不跑模型、不碰真的工作區；伺服器起在隨機埠、不開瀏覽器。
獨立可跑：.venv/bin/python tests/test_versions.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401

import json
import sys
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_needs as tn  # noqa: E402

from bookclub import finalcards, needs, render  # noqa: E402
from bookclub import finalcheck as fc  # noqa: E402

_BASE = datetime.now() + timedelta(seconds=5)


def _t(k: int) -> str:
    """第 k 個時間點（都在現在之後，比卡片、等重做的時間晚）。"""
    return (_BASE + timedelta(seconds=10 * k)).isoformat(timespec="seconds")


def _by(cards: list[dict], key: str) -> dict:
    return next(c for c in cards if c.get("第3步鍵") == key)


def _assemble(w: Path, stamp: str, recs: list[dict], rng=(0.0, 180.0), tag="0-3", results: dict | None = None) -> dict:
    """假的一次組裝：預留版本號 → 寫帶版本號的成品檔 → 寫處理紀錄、建版本、記成品（跟 render_video 同一個順序）。"""
    n = needs.next_version_no(w)
    log = tn._log(stamp=stamp, tag=tag, rng=rng, recs=recs)
    log["版本號"] = n
    rel = tn._prod(w, f"成品_v{n}_{tag}_sw.mp4", stamp, after_s=1.0)
    chk = fc.refresh(fc.load_check(w), log) if fc.check_path(w).is_file() else tn._check(log, {})
    for k, v in (results or {}).items():
        r = next(x for x in log["紀錄"] if fc.record_key(x) == k)
        chk["逐筆"][k] = {"結果": v, "原因": "", "指紋": fc.content_print(r), "更新時間": stamp}
    tn._setup(w, log, chk)
    tn._quiet(needs.add_version, w, log)
    needs.add_product(w, stamp, rel)
    tn._quiet(needs.mirror_final, w, force=True)
    return log


def _three_versions():
    """第 1 版整支（名字 1 通過、T005 沒看）→ 第 2 版只做 0–75 秒 → 回傳工作區。"""
    w = tn._fresh()
    tn._quiet(needs.upgrade, w)   # 還沒做過第 4 步：只有卡片
    _assemble(w, _t(1), tn._records(), results={"名字整句換掉@76.00": "通過", "學員重念@40.00": "通過", "學員重念@55.00": "通過"})
    part = [r for r in tn._records() if r["原片"][0] < 75]
    _assemble(w, _t(2), part, rng=(0.0, 75.0), tag="0-1")
    return w


def test_product_name_helpers():
    assert render.product_parts("成品_v3_0-97_sw.mp4") == (3, "成品_0-97_sw.mp4")
    assert render.product_parts("成品_0-97_sw.mp4") == (None, "成品_0-97_sw.mp4")
    assert fc.final_name("成品_v3_0-97_sw.mp4", "20261015-120000") == "最終成品_v3_0-97_sw_20261015-120000.mp4"
    w = tn._fresh()
    for name in ("成品_0-3_sw.mp4", "成品_v2_0-3_sw.mp4", "成品_v10_0-3_sw.mp4", "成品_v11_0-3_hw.mp4", "成品_v12_0-1_sw.mp4"):
        tn._prod(w, name, tn.T1)
    assert render.product_path(w, "0-3", "sw").name == "成品_v10_0-3_sw.mp4"   # 同範圍同做法：版本號最大的那一支
    assert render.product_path(w, "0-9", "sw").name == "成品_0-9_sw.mp4"       # 一支都沒有：舊檔名（不在）
    # 處理紀錄記了版本號：第 5 步只認那一版的檔名（舊的同範圍不會被當成這一次做出的）
    log = tn._log(stamp=tn.T1, tag="0-3")
    log["版本號"] = 2
    assert fc.log_products(w, log) == ["輸出/成品_v2_0-3_sw.mp4"]
    marks = fc.log_marks(w, log)
    assert marks["輸出/成品_v2_0-3_sw.mp4"] == fc.MARK_LATEST and marks["輸出/成品_v10_0-3_sw.mp4"] == fc.MARK_OLD


def test_version_number_reserved_and_same_range_not_overwritten():
    w = tn._fresh()
    assert needs.next_version_no(w) is None   # 沒升級過：成品照舊的檔名
    tn._quiet(needs.upgrade, w)
    assert needs.next_version_no(w) == 1
    _assemble(w, _t(1), tn._records())
    _assemble(w, _t(2), tn._records())   # 同範圍再組一次
    vs = needs.version_rows(w)
    assert [v["版本號"] for v in vs] == [1, 2]
    assert vs[0]["成品檔"] == ["輸出/成品_v1_0-3_sw.mp4"] and vs[1]["成品檔"] == ["輸出/成品_v2_0-3_sw.mp4"]
    assert (w / "輸出" / "成品_v1_0-3_sw.mp4").is_file() and (w / "輸出" / "成品_v2_0-3_sw.mp4").is_file()
    assert (w / vs[1]["處理紀錄"]).is_file() and "其他卡指紋" in vs[1]
    # 預留的號碼被用掉了（例如別的組裝先建了版本）：照順序加一，不會撞號
    st = needs.load(w)
    log = tn._log(stamp=_t(3))
    log["版本號"] = 1
    row = needs.build_version(st, w, log, {}, _t(3))
    assert row["版本號"] == 3


def test_cross_version_cards_and_not_processed():
    """#221：先整支、再只做 0–75 秒 → 範圍外、第 1 版還沒看的卡在「上一版還沒看的（第 1 版）」；第 1 版通過的標「這一版沒處理到」。"""
    w = _three_versions()
    part = finalcards.build(w)
    assert part["來源"] == finalcards.SRC_NEW
    cards, cross = part["卡片"], part["跨版本卡片"]
    t5 = _by(cross, "學員段落:T005")
    assert t5["版本號"] == 1 and t5["狀態"] == needs.NOT_SEEN and t5["跨版本"] and t5["成品"] == [88.4, 108.0]
    assert {c["類型"] for c in cross} == {"學員段落", needs.UNCLEAR}   # 第 1 版的停格（待釐清）也帶過來
    n1 = _by(cards, "名字:1")
    assert n1["這一版沒處理到"] and n1["狀態"] == needs.NOT_MADE and n1["成品"] is None
    assert n1["上一版已通過"] is True   # 10-15 宇軒驗證回饋三／四：畫面標「已通過（沿用上一版）」
    assert _by(cards, "學員段落:T003")["版本號"] == 2 and not _by(cards, "學員段落:T003")["這一版沒處理到"]
    assert not any(c.get("第3步鍵") == "學員段落:T007" for c in cards + cross)   # 哪一版都沒處理過的：不列
    st = part["統計"]
    assert st["要重做"] == 0   # 範圍外的不算進「重做這幾張」
    # 沒處理到的不能按通過
    try:
        finalcards.decide(w, n1["id"], needs.PASSED)
        raise AssertionError("沒處理到的不能按通過")
    except ValueError as e:
        assert "上一版通過的" in str(e)   # 10-15 宇軒驗證回饋三：錯誤訊息改字
    # 在「上一版還沒看的」那一區按通過：記在第 1 版，不寫成品檢查檔
    before = json.dumps(fc.load_check(w), ensure_ascii=False, sort_keys=True)
    r = finalcards.decide(w, t5["id"], needs.PASSED, version=1)
    assert r["卡片"]["狀態"] == needs.PASSED and r["版本號"] == 1
    assert json.dumps(fc.load_check(w), ensure_ascii=False, sort_keys=True) == before
    assert needs.card_state(needs.load(w), t5["卡號"], 1) == needs.PASSED
    part = finalcards.build(w)
    assert not any(c["id"] == t5["id"] for c in part["跨版本卡片"])
    assert _by(part["卡片"], "學員段落:T005")["這一版沒處理到"]


def test_outside_passed_card_can_be_fixed_on_latest():
    """10-15 宇軒驗證回饋四：「已通過（沿用上一版）」（範圍外）的卡在最新版可以按「要修改」——記原因、變「要重做」（已改，等重做），
    通過仍不行；舊版也能按（原因一樣記在卡片）。
    10-15 總檢查修正（1015-7）：舊版畫面照那一版的判斷、另給 `最新版要重做`；沒跑「重做這幾張」就組裝 → 等重做留著；
    跑了「重做這幾張」再組裝 → 清掉，新版不沿用之前的通過（要重新看）。"""
    w = _three_versions()
    n1 = _by(finalcards.build(w)["卡片"], "名字:1")
    assert n1["這一版沒處理到"] and not n1["範圍外改了"] and n1["已通過的"]
    assert n1["可重新生成"], "範圍外的卡能不能換一種念法照之前有它的那一版（才列得出「聲音不像、不自然」）"
    for res in (needs.PASSED, None):
        try:
            finalcards.decide(w, n1["id"], res)
            raise AssertionError("範圍外的卡不能通過、取消")
        except ValueError as e:
            assert "上一版通過的" in str(e)
    r = finalcards.decide(w, n1["id"], needs.RETURNED, ["聲音不像、不自然"], "太快了")
    c = r["卡片"]
    assert c["狀態"] == finalcards.EDITED and c["範圍外改了"] and not c["這一版沒處理到"] and not c["已通過的"]
    assert c["退回原因"] == {"選項": ["聲音不像、不自然"], "補充": "太快了"}
    assert r["統計"]["要重做"] == 1
    st = needs.load(w)
    pend = st.cards[n1["卡號"]]["等重做"]
    assert pend["起因"] == needs.WHY_EDITED and pend["退回原因"]["補充"] == "太快了"
    assert (n1["卡號"], 2) not in st.states   # 這一版的狀態列不多寫
    assert "太快了" not in (needs.needs_dir(w) / "歷史.jsonl").read_text(encoding="utf-8")   # 歷史不放原因文字
    # 再按一次「要修改」換原因；之後第 3 步的改（mark_edited）不洗掉原因
    finalcards.decide(w, n1["id"], needs.RETURNED, ["念錯字"], "")
    tn._quiet(finalcards.edited, w, "名字:1")
    c = _by(finalcards.build(w)["卡片"], "名字:1")
    assert c["狀態"] == finalcards.EDITED and c["退回原因"] == {"選項": ["念錯字"], "補充": ""}
    # 10-15 宇軒驗證回饋六：舊版畫面上也能按「要修改」——原因換掉、記在卡片的等重做；第 1 版的狀態列不動（還是通過）
    r = finalcards.decide(w, n1["id"], needs.RETURNED, ["長度或停頓怪"], "尾巴", version=1)
    assert r["卡片"]["狀態"] == finalcards.EDITED and r["卡片"]["退回原因"] == {"選項": ["長度或停頓怪"], "補充": "尾巴"}
    st = needs.load(w)
    assert st.states[(n1["卡號"], 1)]["狀態"] == needs.PASSED and (n1["卡號"], 2) not in st.states
    # 10-15 總檢查修正（1015-7，A6）：舊版畫面照那一版的判斷（通過），另給「最新版要重做」（預填用）
    pg1 = finalcards.version_page(w, 1, None)
    o1 = _by(pg1["卡片"], "名字:1")
    assert o1["狀態"] == needs.PASSED and o1["最新版要重做"] == {"選項": ["長度或停頓怪"], "補充": "尾巴"}
    assert pg1["卡片統計"]["要重做"] == 1
    # 沒跑「重做這幾張」就組裝（只重新組裝）：聲音沒換，等重做留著、歷史不寫「重做進第 3 版」
    _assemble(w, _t(3), tn._records())
    st = needs.load(w)
    assert st.cards[n1["卡號"]]["等重做"]["退回原因"]["補充"] == "尾巴"
    assert not any(h["卡號"] == n1["卡號"] and str(h["動作"]).startswith("重做進第") for h in needs.read_history(w))
    assert _by(finalcards.build(w)["卡片"], "名字:1")["狀態"] == finalcards.EDITED
    # 「重做這幾張」：第 4 步拿得到它（照卡片重做），組裝後等重做清掉、新版不沿用第 1 版的通過；別張通過的照樣沿用
    plan = tn._quiet(fc.redo_plan, w)
    assert [e["卡號"] for e in plan if e.get("卡號")] == [n1["卡號"]]
    tn._quiet(fc.prepare_redo, w, log=lambda s: None)
    _gen_logs(w)   # 1015-8 #228：生成程式把清掉的那一句重新生成了
    _assemble(w, _t(4), tn._records())
    tn._quiet(fc.finish_redo, w)
    st = needs.load(w)
    assert not st.cards[n1["卡號"]].get("等重做")
    assert st.states[(n1["卡號"], 4)]["狀態"] == needs.NOT_SEEN and not st.states[(n1["卡號"], 4)]["沿用自"]
    assert any(h["卡號"] == n1["卡號"] and h["動作"] == "重做進第4版" for h in needs.read_history(w))
    c3 = _by(finalcards.build(w)["卡片"], "名字:1")
    assert c3["狀態"] == needs.NOT_SEEN and not c3["範圍外改了"] and not c3["這一版沒處理到"]


def test_old_version_page_and_decide_and_watch():
    w = _three_versions()
    assert finalcards.current_version(w) == 2
    pg = finalcards.version_page(w, 1, None)
    assert pg["看的是舊版"] and pg["目前版本"] == 1 and pg["最新版本"] == 2 and pg["影片網址"] == "/api/final/video?版本=1"
    assert [v["版本號"] for v in pg["版本清單"]] == [2, 1] and pg["版本清單"][0]["最新"] and pg["版本清單"][1]["有影片"]
    cards = pg["卡片"]
    assert _by(cards, "名字:1")["狀態"] == needs.PASSED and _by(cards, "學員段落:T005")["狀態"] == needs.NOT_SEEN
    assert not any(c["這一版沒處理到"] for c in cards)   # 第 1 版是整支
    t5 = _by(cards, "學員段落:T005")
    # 通過、取消照樣可以（記在那一版）
    assert finalcards.decide(w, t5["id"], needs.PASSED, version=1)["卡片"]["狀態"] == needs.PASSED
    assert finalcards.decide(w, t5["id"], None, version=1)["卡片"]["狀態"] == needs.NOT_SEEN
    # 10-15 宇軒驗證回饋六（第 3 段審查「必修 1」結案拿掉）：舊版也能按「要修改」——原因記在卡片的等重做；
    # 那一版的狀態列、成品檢查檔都不動；最新版上它變「已改，等重做」、算進要重做
    before = json.dumps(fc.load_check(w), ensure_ascii=False, sort_keys=True)
    r = finalcards.decide(w, t5["id"], needs.RETURNED, ["念錯字"], "x", version=1)
    assert r["ok"] and r["卡片"]["狀態"] == finalcards.EDITED and r["卡片"]["退回原因"] == {"選項": ["念錯字"], "補充": "x"}
    st = needs.load(w)
    assert st.states[(t5["卡號"], 1)]["狀態"] == needs.NOT_SEEN
    pend = st.cards[t5["卡號"]]["等重做"]
    assert pend["起因"] == needs.WHY_EDITED and pend["退回原因"] == {"選項": ["念錯字"], "補充": "x"} and not pend.get("版本號")
    assert json.dumps(fc.load_check(w), ensure_ascii=False, sort_keys=True) == before
    part = finalcards.build(w)
    assert not any(c["id"] == t5["id"] for c in part["跨版本卡片"]) and part["統計"]["要重做"] == 1
    o5 = _by(finalcards.version_page(w, 1, None)["卡片"], "學員段落:T005")   # 1015-7 A6：那一版的判斷照舊＋最新版要重做
    assert o5["狀態"] == needs.NOT_SEEN and o5["最新版要重做"] == {"選項": ["念錯字"], "補充": "x"}
    # 舊版上看過的：記在那一版自己的一份
    tn._quiet(finalcards.version_watched, w, 1, [[1.0, 2.0]])   # 假影片量不出長度：不截
    tn._quiet(finalcards.version_watched, w, 1, [[1.5, 3.0]])
    assert needs.watched(w, 1) == [[1.0, 3.0]] and needs.watched(w, 2) == []


def test_cross_card_fix_and_old_return_assembly_history():
    """10-15 宇軒驗證回饋六：跨版本卡（之前版本還沒看完的）按「要修改」→ 原因記在卡片的等重做（不寫成品檢查檔、那一版狀態列不動），
    最新版變「已改，等重做」；needs 層 `decide_version` 的退回也改記在卡片的等重做（1015-7 A8）。
    10-15 第3段審查修正（必修 1）：之前留下的舊版退回（等重做帶版本號），之後的組裝沒有真的重做它（指紋跟退回那一版一樣）
    → 等重做留著、歷史不寫「重做進第 N 版」；真的換了聲音才清。"""
    w = _three_versions()
    t5 = _by(finalcards.build(w)["跨版本卡片"], "學員段落:T005")
    before = json.dumps(fc.load_check(w), ensure_ascii=False, sort_keys=True)
    r = finalcards.decide(w, t5["id"], needs.RETURNED, ["聲音不像、不自然"], "太快", version=1)
    assert r["ok"] and r["卡片"]["狀態"] == finalcards.EDITED and r["卡片"]["範圍外改了"]
    assert json.dumps(fc.load_check(w), ensure_ascii=False, sort_keys=True) == before
    st = needs.load(w)
    assert st.states[(t5["卡號"], 1)]["狀態"] == needs.NOT_SEEN and (t5["卡號"], 2) not in st.states
    assert st.cards[t5["卡號"]]["等重做"]["退回原因"] == {"選項": ["聲音不像、不自然"], "補充": "太快"}
    c = _by(finalcards.build(w)["卡片"], "學員段落:T005")
    assert c["狀態"] == finalcards.EDITED and c["退回原因"]["補充"] == "太快"
    # 再按一次換原因
    finalcards.decide(w, t5["id"], needs.RETURNED, ["念錯字"], "", version=1)
    assert needs.load(w).cards[t5["卡號"]]["等重做"]["退回原因"] == {"選項": ["念錯字"], "補充": ""}
    row = needs.decide_version(w, 1, t5["卡號"], needs.RETURNED, ["其他"], "y")   # 1015-7 A8：不擋，改記卡片的等重做
    assert row["狀態"] == needs.NOT_SEEN
    assert needs.load(w).cards[t5["卡號"]]["等重做"]["退回原因"] == {"選項": ["其他"], "補充": "y"}
    # 0.2.2b1 第 3 段原版留下的舊版退回（照當時的寫法造一筆）
    with needs._locked(w):
        st = needs.load(w)
        st.cards[t5["卡號"]]["等重做"] = {"起因": needs.WHY_RETURNED, "時間": _t(2), "版本號": 1}
        st.states[(t5["卡號"], 1)]["狀態"] = needs.RETURNED
        needs.save(w, st)
    # 第 3 版整支：T005 在上一版（0–75 秒）範圍外 → 在 `這一版做了哪幾張`，可是指紋跟第 1 版一樣（沒有重新生成）
    _assemble(w, _t(3), tn._records())
    st = needs.load(w)
    assert t5["卡號"] in st.versions[3]["這一版做了哪幾張"]
    assert st.cards[t5["卡號"]]["等重做"]["版本號"] == 1
    assert not any(h["卡號"] == t5["卡號"] and str(h["動作"]).startswith("重做進第") for h in needs.read_history(w))
    # 第 4 版 T005 真的換了聲音 → 清掉、歷史寫「重做進第4版」
    recs = tn._records()
    recs[4] = {**recs[4], "檔案指紋": "fp-T005-重念"}
    _assemble(w, _t(4), recs)
    assert not needs.load(w).cards[t5["卡號"]]["等重做"]
    assert any(h["卡號"] == t5["卡號"] and h["動作"] == "重做進第4版" for h in needs.read_history(w))


def test_carry_pass_from_older_version_across_range_version():
    """#23＋範圍版本：第 1 版通過、第 2 版範圍外沒有它、第 3 版整支又有它而且指紋沒變 → 通過（沿用第 1 版），
    成品檢查檔早就沒有這一筆也不會被洗回還沒看；老師按「取消」才改回還沒看。"""
    w = _three_versions()
    _assemble(w, _t(3), tn._records())
    st = needs.load(w)
    n1 = _by(finalcards.build(w)["卡片"], "名字:1")
    row = st.states[(n1["卡號"], 3)]
    assert row["狀態"] == needs.PASSED and row["沿用自"] == 1 and row["沿用來源"] == 1
    assert st.versions[3]["以哪一版為基礎"] == 2
    tn._quiet(needs.mirror_final, w, force=True)
    assert needs.card_state(needs.load(w), n1["卡號"], 3) == needs.PASSED
    tn._quiet(finalcards.decide, w, n1["id"], None)
    assert needs.card_state(needs.load(w), n1["卡號"], 3) == needs.NOT_SEEN
    tn._quiet(needs.mirror_final, w, force=True)
    assert needs.card_state(needs.load(w), n1["卡號"], 3) == needs.NOT_SEEN


def test_base_version_for_next_assembly():
    """開發者「以這一版為基礎再組裝」：下一版的基礎記那一版、用一次就清掉；沒有那一版不收。"""
    w = _three_versions()
    needs.set_next_base(w, 1)
    _assemble(w, _t(3), tn._records())
    st = needs.load(w)
    assert st.versions[3]["以哪一版為基礎"] == 1 and "下次組裝的基礎版本" not in st.fmt
    try:
        needs.set_next_base(w, 9)
        raise AssertionError("沒有的版本不收")
    except ValueError:
        pass


def test_prev_take_for_redone_card():
    """重做過的卡多「上一版 ▶」：播上一個有它的版本那一段（上一版影片還在才有）。"""
    w = tn._fresh()
    tn._quiet(needs.upgrade, w)
    _assemble(w, _t(1), tn._records())
    recs = tn._records()
    recs[1] = {**recs[1], "檔案指紋": "fp-換了一種念法"}   # T003 第一格重新生成
    _assemble(w, _t(2), recs)
    cards = finalcards.build(w)["卡片"]
    t3 = _by(cards, "學員段落:T003")
    assert t3["上一版"] == {"版本號": 1, "成品": [36.4, 68.0]}
    assert _by(cards, "名字:1")["上一版"] is None   # 沒重做
    (w / "輸出" / "成品_v1_0-3_sw.mp4").unlink()    # 上一版的影片清掉了：不出現
    assert _by(finalcards.build(w)["卡片"], "學員段落:T003")["上一版"] is None


def test_step3_keep_turned_on_shows_edited():
    """第 2 段報告第 7 條：第 3 步選定不修改（這一版沒處理）的卡，在第 3 步改成要處理 → 第 5 步「已改，等重做」。"""
    from bookclub import turns as _turns

    w = tn._fresh()
    tn._quiet(needs.upgrade, w)
    _assemble(w, _t(1), tn._records())
    assert not any(c.get("第3步鍵") == "學員段落:T007" for c in finalcards.build(w)["卡片"])
    _turns.save_turn(w, "T007", {"校對稿": "改成要重念"})
    needs.sync_cards(w)
    part = finalcards.build(w)
    c = _by(part["卡片"], "學員段落:T007")
    assert c["狀態"] == finalcards.EDITED and c["第3步改過"] and part["統計"]["要重做"] == 1


def test_export_any_version_and_take_rule():
    """§6.7：輸出選中的那一版；拿檔按鈕拿最新一次輸出，不是最新版時寫「這是第 N 版的，最新是第 M 版」。"""
    w = _three_versions()
    r = finalcards.export_version(w, 1)
    assert r["要確認"] and "張沒看完" in r["還差"]["說明"][0]
    r = finalcards.export_version(w, 1, confirm=True)
    assert r["ok"] and r["版本號"] == 1 and Path(r["檔案"]).name.startswith("最終成品_v1_0-3_sw_")
    assert (w / r["檔案"]).is_file()
    st = needs.load(w)
    assert [x["版本號"] for x in st.versions[1]["輸出紀錄"]] == [1]
    tn._quiet(needs.mirror_final, w, force=True)   # 最新一版重算不會洗掉舊版的輸出紀錄
    assert len(needs.load(w).versions[1]["輸出紀錄"]) == 1
    t = finalcards.take(w)
    assert t["是"] == "最終成品" and t["輸出版本"] == 1 and t["最新版本"] == 2 and t["路徑"] == r["檔案"]
    pp = finalcards.page_part(w, None)
    assert pp["輸出成品過期"]["說明"] == "這是第 1 版的，最新是第 2 版" and pp["目前版本"] == 2
    # 影片清掉的那一版不能輸出
    (w / "輸出" / "成品_v1_0-3_sw.mp4").unlink()
    try:
        finalcards.export_version(w, 1, confirm=True)
        raise AssertionError("影片清掉了不能輸出")
    except ValueError as e:
        assert "清掉了" in str(e)


def test_export_record_matches_no_version():
    """10-15 第3段審查修正（必修 2）：在 0.2.1b3 輸出過→又更新成品→才升級，輸出紀錄對不到任何版本 → 拿檔不拿那支舊的最終成品、
    不寫「這是第 None 版」，照 0.2.1b3 的過期判斷（take_target／export_stale）。"""
    from bookclub import server

    w = _three_versions()
    chk = fc.load_check(w)
    chk["輸出成品"] = {"檔案": "輸出/最終成品_0-3_sw_x.mp4", "來源": "輸出/成品_0-3_sw.mp4",
                    "處理紀錄產生時間": "2020-01-01T00:00:00", "時間": "2020-01-01T00:00:00"}
    (w / "輸出" / "最終成品_0-3_sw_x.mp4").write_bytes(b"old-final")
    tn._quiet(fc._save, w, chk)
    assert finalcards.take(w) is None
    pp = finalcards.page_part(w, None)
    assert "輸出成品過期" not in pp   # 不覆寫 finalcheck 算好的
    full = tn._quiet(fc.page_data, w)
    full.update(pp)
    assert full["輸出成品過期"] and full["輸出成品過期"]["原因"] == fc.STALE_OTHER
    path, what, stale = tn._quiet(server.final_target, w)
    assert path.name != "最終成品_0-3_sw_x.mp4" and "還沒重新輸出" in what and stale
    httpd = server.BookclubServer(("127.0.0.1", 0), server.Handler, workdir=w, video=None)
    try:
        assert httpd._export_versions() is None
    finally:
        httpd.server_close()


def test_old_version_export_asks_when_not_fully_watched():
    """10-15 第3段審查修正（建議）：舊版輸出——卡都看完了、整片沒看完（看過不到 100%）也先問一次。"""
    w = _three_versions()
    st = needs.load(w)
    for (k, n) in list(st.states):
        if n == 1:
            tn._quiet(finalcards.decide, w, next(c["id"] for c in finalcards.version_page(w, 1, None)["卡片"]
                                                  if c.get("卡號") == k), needs.PASSED, version=1)
    pg = finalcards.version_page(w, 1, None)
    assert pg["卡片統計"]["還沒看"] == 0 and pg["卡片統計"]["退回"] == 0 and pg["狀態"]["看過比例"] < 1
    r = finalcards.export_version(w, 1)
    assert r["要確認"] and r["還差"]["說明"] == [f"整片還沒看完（看過 {int(pg['狀態']['看過比例'] * 100)}%）"]
    assert finalcards.export_version(w, 1, confirm=True)["ok"]


def test_old_version_export_warns_not_latest_but_not_blocked():
    """10-15 宇軒定案：按「輸出成品」時輸出的不是最新版本 → 確認視窗最上面「目前輸出的不是最新版本（這是第 N 版，最新是第 M 版）」，
    不擋（確定就輸出）；卡都看完、整片看完也要問一次（只為了這一行）。最新版不出現。"""
    w = _three_versions()
    r = finalcards.export_version(w, 1)
    assert r["要確認"] and r["還差"]["不是最新版本"] == "目前輸出的不是最新版本（這是第 1 版，最新是第 2 版）"
    for (k, n) in list(needs.load(w).states):   # 第 1 版卡都通過、整片看完：只剩「不是最新版本」也要問
        if n == 1:
            tn._quiet(finalcards.decide, w, next(c["id"] for c in finalcards.version_page(w, 1, None)["卡片"]
                                                  if c.get("卡號") == k), needs.PASSED, version=1)
    assert finalcards.version_page(w, 1, None)["卡片統計"]["還要確認"] == 0
    orig = finalcards.version_page   # 假工作區的成品量不出長度（看過比例一直是 0）：這裡當作整片看完

    def full(*a, **k):
        pg = orig(*a, **k)
        return {**pg, "狀態": {**pg["狀態"], "看過比例": 1.0}}

    finalcards.version_page = full
    try:
        r = finalcards.export_version(w, 1)
    finally:
        finalcards.version_page = orig
    assert r["要確認"] and r["還差"]["說明"] == [] and r["還差"]["不是最新版本"].startswith("目前輸出的不是最新版本")
    r = finalcards.export_version(w, 1, confirm=True)
    assert r["ok"] and r["版本號"] == 1   # 不擋
    # 最新版（第 2 版走 export_final）：沒有這一行
    r = tn._quiet(fc.export_final, w)
    assert r["要確認"] and r["還差"]["不是最新版本"] == ""
    assert fc.not_latest_note(w, 2) == "" and fc.not_latest_note(w, None) == ""
    # 舊檔模式（新紀錄不能用）：不顯示
    w2 = tn._fresh()
    assert not needs.exists(w2) and fc.not_latest_note(w2, 1) == ""


def test_new_version_keeps_prev_watched():
    """10-15 第3段審查修正（建議）：建新版時，上一版在成品檢查檔的看過區段補進看過.json（升級前看的也留得住）。
    審查二修正：檢查的成品影片要是上一版 `成品檔` 裡的那支，手動換到舊成品（`手動選擇.舊的`）看過的不補。"""
    w = tn._fresh()
    tn._quiet(needs.upgrade, w)
    _assemble(w, _t(1), tn._records())
    chk = fc.load_check(w)
    chk["成品影片"] = "輸出/成品_v1_0-3_sw.mp4"
    chk["看過區段"] = [[0.0, 30.0]]   # 沒經過 add_watched 雙寫（例如升級前看的）
    tn._quiet(fc._save, w, chk)
    assert needs.watched(w, 1) == []
    # 組裝寫處理紀錄、建版本的那一刻，成品檢查檔還是上一版那一份（打開第 5 步才換新）
    log = tn._log(stamp=_t(2))
    log["版本號"] = needs.next_version_no(w)
    rel2 = tn._prod(w, "成品_v2_0-3_sw.mp4", _t(2), after_s=1.0)
    tn._setup(w, log, None)
    tn._quiet(needs.add_version, w, log)
    needs.add_product(w, _t(2), rel2)
    assert needs.watched(w, 1) == [[0.0, 30.0]] and needs.watched(w, 2) == []
    # 成品檢查檔換成第 2 版那一份：再建第 3 版時補的是第 2 版（第 1 版不會被第 2 版的時間汙染）
    chk = fc.refresh(fc.load_check(w), log)
    chk["成品影片"] = rel2
    chk["看過區段"] = [[50.0, 60.0]]
    tn._quiet(fc._save, w, chk)
    log3 = tn._log(stamp=_t(3))
    log3["版本號"] = needs.next_version_no(w)
    rel3 = tn._prod(w, "成品_v3_0-3_sw.mp4", _t(3), after_s=1.0)
    tn._setup(w, log3, None)
    tn._quiet(needs.add_version, w, log3)
    needs.add_product(w, _t(3), rel3)
    assert needs.watched(w, 1) == [[0.0, 30.0]] and needs.watched(w, 2) == [[50.0, 60.0]], needs.watched(w, 2)   # 第 2 版那一份補進第 2 版，第 1 版不動
    # 審查二：手動換到舊成品（第 1 版那支，`手動選擇.舊的`）看過的，建第 4 版時不併進第 3 版
    chk = fc.refresh(fc.load_check(w), log3)
    chk["成品影片"] = "輸出/成品_v1_0-3_sw.mp4"
    chk["手動選擇"] = {"成品影片": chk["成品影片"], "處理紀錄產生時間": _t(3), "舊的": True}
    chk["看過區段"] = [[100.0, 120.0]]
    tn._quiet(fc._save, w, chk)
    log4 = tn._log(stamp=_t(4))
    tn._setup(w, log4, None)
    tn._quiet(needs.add_version, w, log4)
    assert needs.watched(w, 3) == [], needs.watched(w, 3)
    # 沒記 `舊的`、但檢查的那支不在上一版的成品檔裡：也不併
    chk["處理紀錄產生時間"] = _t(4)
    chk.pop("手動選擇")
    chk["成品影片"] = rel2
    tn._quiet(fc._save, w, chk)
    log5 = tn._log(stamp=_t(5))
    tn._setup(w, log5, None)
    tn._quiet(needs.add_version, w, log5)
    assert needs.watched(w, 4) == [] and needs.watched(w, 2) == [[50.0, 60.0]] and needs.watched(w, 1) == [[0.0, 30.0]]


def _serve(w: Path):
    from bookclub import server

    httpd = server.BookclubServer(("127.0.0.1", 0), server.Handler, workdir=w, video=None)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _get(base, path):
    with urllib.request.urlopen(base + urllib.parse.quote(path, safe="/?=&"), timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Origin": base}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def test_api_versions():
    w = _three_versions()
    httpd, base = _serve(w)
    try:
        d = tn._quiet(_get, base, "/api/final")
        assert not d["看的是舊版"] and d["目前版本"] == 2 and len(d["版本清單"]) == 2
        assert any(c.get("第3步鍵") == "學員段落:T005" for c in d["跨版本卡片"])
        o = tn._quiet(_get, base, "/api/final?版本=1")
        assert o["看的是舊版"] and o["目前版本"] == 1
        t5 = _by(o["卡片"], "學員段落:T005")
        r = tn._quiet(_post, base, "/api/final/card", {"id": t5["id"], "結果": "通過", "版本": 1})
        assert r["卡片"]["狀態"] == needs.PASSED
        # 10-15 宇軒驗證回饋六：舊版上按「要修改」（退回＋版本）不被擋——原因記在卡片的等重做，不寫成品檢查檔
        before = json.dumps(fc.load_check(w), ensure_ascii=False, sort_keys=True)
        n1 = _by(o["卡片"], "名字:1")
        r = tn._quiet(_post, base, "/api/final/card", {"id": n1["id"], "結果": "退回", "選項": ["聲音不像、不自然"], "補充": "", "版本": 1})
        assert r["ok"] and r["卡片"]["狀態"] == finalcards.EDITED
        assert json.dumps(fc.load_check(w), ensure_ascii=False, sort_keys=True) == before
        assert needs.load(w).cards[n1["卡號"]]["等重做"]["退回原因"]["選項"] == ["聲音不像、不自然"]
        assert tn._quiet(_get, base, "/api/final?版本=2")["看的是舊版"] is False   # 最新那一版＝一般畫面
        s = tn._quiet(_get, base, "/api/state")
        assert s["開發者"] is False
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_api_cancel_redo_on_latest_page_without_version():
    """1015-7 合併收尾：前端「取消要重做」的送法——最新版頁面 `POST /api/final/card {id, 結果: null, 選項: [], 補充: ""}`
    （不帶版本、不帶「取消要重做」），用在 `範圍外改了`＋有退回原因的卡 → 清掉等重做（不是回「這張是上一版通過的…」）。"""
    w = _three_versions()
    httpd, base = _serve(w)
    try:
        n1 = _by(tn._quiet(_get, base, "/api/final")["卡片"], "名字:1")
        r = tn._quiet(_post, base, "/api/final/card", {"id": n1["id"], "結果": "退回", "選項": ["聲音不像、不自然"], "補充": "快"})
        assert r["卡片"]["範圍外改了"] and r["卡片"]["退回原因"]["補充"] == "快" and r["統計"]["要重做"] == 1
        r = tn._quiet(_post, base, "/api/final/card", {"id": n1["id"], "結果": None, "選項": [], "補充": ""})
        assert r["ok"] and r["統計"]["要重做"] == 0, r
        c = r["卡片"]
        assert c["這一版沒處理到"] and not c["範圍外改了"] and c["已通過的"] and c["退回原因"] is None
        assert not needs.load(w).cards[n1["卡號"]].get("等重做")
        d = tn._quiet(_get, base, "/api/final")
        assert d["卡片統計"]["要重做"] == 0 and _by(d["卡片"], "名字:1")["已通過的"]
        # 沒有等重做的時候照舊擋（通過、取消都不是這一版做的）
        try:
            tn._quiet(_post, base, "/api/final/card", {"id": n1["id"], "結果": None, "選項": [], "補充": ""})
            raise AssertionError("沒有要重做的範圍外卡，取消照舊擋")
        except urllib.error.HTTPError as e:
            assert "上一版通過的" in json.loads(e.read().decode("utf-8"))["error"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_redo_avoid_follows_plan_judgment_despite_slot_drift():
    """10-15 C1 實跑修正（1015-7）：跨版本卡按「聲音不像」、計畫重算後時間格漂 0.1 秒——第 4 步判「沒改」，
    `_重新生成版本.json` 那一句記 `換一種念法: true`，生成時 `tts.redo_avoid` 照它避開用過的種子（以前判成改了、又選回 42）。"""
    from bookclub import tts
    from bookclub import workdir as wd

    w = _three_versions()
    _gen_logs(w)
    lp, od = fc._role_paths(w, "學員")
    rec = wd.read_json(lp)
    for r in rec["句子"]:   # 上一次生成的時間格比現在的計畫早 0.1 秒（升級的舊工作區常見）
        if r["id"] == "T005_01":
            r["slot"] = [r["slot"][0] - 0.1, r["slot"][1]]
            r["嘗試"] = [{"種子": 42}]
    wd.write_json(lp, rec)
    t5 = _by(finalcards.build(w)["跨版本卡片"], "學員段落:T005")
    tn._quiet(finalcards.decide, w, t5["id"], needs.RETURNED, ["聲音不像、不自然"], "", version=1)
    e = next(x for x in tn._quiet(fc.redo_plan, w) if x.get("卡號") == t5["卡號"])
    assert e["沒改"] is True
    tn._quiet(fc.prepare_redo, w, log=lambda s: None)
    entry = json.loads((od / "_重新生成版本.json").read_text(encoding="utf-8"))["T005_01"]
    assert entry["換一種念法"] is True and entry["避開"] == [42]
    now = next(x for x in fc._redo_ctx(w)["學員"] if x["id"] == "T005_01")
    assert abs(entry["slot"][0] - now["slot"][0]) > tts.SLOT_TOLERANCE_S   # 真的漂了（舊判法會說改了）
    assert tts.redo_avoid(entry, now) == [42]
    assert tts.redo_avoid(entry, {**now, "text": "改過的字"}) == []


def test_windows_copy_asks_when_not_latest():
    """10-15 第3段審查修正（建議，宇軒定前暫定）：複製到 Windows 拿的最終成品不是最新一版 → 先問一次；確定才複製。"""
    from bookclub import fileio
    from bookclub import server as srv

    w = _three_versions()
    tn._quiet(finalcards.export_version, w, 1, confirm=True)
    httpd = srv.BookclubServer(("127.0.0.1", 0), srv.Handler, workdir=w, video=None)
    olds = (fileio.platform_kind, fileio.windows_downloads)
    started = []
    try:
        fileio.platform_kind = lambda: "wsl"
        fileio.windows_downloads = lambda: (_ for _ in ()).throw(RuntimeError("測試：不真的複製"))
        r = httpd.final_to_windows()
        assert r["要確認"] and r["說明"] == "這是第 1 版的，最新是第 2 版" and r["版本"] == {"輸出版本": 1, "最新版本": 2}
        try:
            httpd.final_to_windows(confirm=True)   # 確定之後才走到複製（這裡假的下載資料夾丟錯，代表有往下走）
        except ValueError as e:
            started.append(str(e))
        assert started == ["測試：不真的複製"]
    finally:
        fileio.platform_kind, fileio.windows_downloads = olds
        httpd.server_close()


def test_render_video_names_with_version_after_upgrade():
    """10-15 第3段審查修正（建議）：升級過的工作區，render_video（假編碼器）產生 `成品_v<N>_…`、處理紀錄記同一個版本號，
    升級前的舊檔名成品不動；失敗的那一次不佔號碼。"""
    import test_execute as te
    import test_stop_keep as sk

    from bookclub import execute, proclog

    w = te._fresh()
    prod, _old = sk._with_product(w)
    tn._quiet(needs.upgrade, w)
    n = needs.next_version_no(w)
    with sk._FakeRender(w, fail=True):
        try:
            tn._quiet(render.render_video, w, 0.0, 180.0, tag="0-3", min_free_gb=0)
        except RuntimeError:
            pass
    assert needs.next_version_no(w) == n   # 失敗：號碼還在
    with sk._FakeRender(w, fail=False):
        tn._quiet(render.render_video, w, 0.0, 180.0, tag="0-3", min_free_gb=0)
    assert (w / "輸出" / f"成品_v{n}_0-3_sw.mp4").is_file() and prod.read_bytes() == b"old product"
    assert proclog.load(w)["版本號"] == n
    assert needs.load(w).versions[n]["成品檔"] == [f"輸出/成品_v{n}_0-3_sw.mp4"]
    assert execute.render_done(w, "0-3", ["sw"])[0]
    assert render.product_path(w, "0-3", "sw").name == f"成品_v{n}_0-3_sw.mp4"


def test_api_vclip():
    """10-15 第3段審查修正（建議）：`/api/final/vclip?版本=N&start&end`——第 N 版成品那一段的聲音（ffmpeg 換成假的）。"""
    import subprocess
    import urllib.error

    w = _three_versions()
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"RIFF-fake-wav")
        return subprocess.CompletedProcess(cmd, 0)

    old = subprocess.run
    subprocess.run = fake_run
    httpd, base = _serve(w)
    try:
        with urllib.request.urlopen(base + urllib.parse.quote("/api/final/vclip?版本=1&start=10&end=12.5", safe="/?=&"),
                                    timeout=30) as r:
            assert r.status == 200 and r.headers.get("Content-Type", "").startswith("audio/wav") and r.read() == b"RIFF-fake-wav"
        assert len(calls) == 1 and calls[0][calls[0].index("-i") + 1].endswith("成品_v1_0-3_sw.mp4")
        assert (w / "web快取" / "成品檢查" / "版本1_10.00_12.50.wav").is_file()
        with urllib.request.urlopen(base + urllib.parse.quote("/api/final/vclip?版本=1&start=10&end=12.5", safe="/?=&"),
                                    timeout=30) as r:
            r.read()
        assert len(calls) == 1   # 快取：同一段不再切
        (w / "輸出" / "成品_v1_0-3_sw.mp4").unlink()
        try:
            tn._quiet(urllib.request.urlopen, base + urllib.parse.quote("/api/final/vclip?版本=1&start=1&end=2", safe="/?=&"),
                      timeout=30)
            raise AssertionError("影片清掉了要回錯誤")
        except urllib.error.HTTPError as e:
            assert e.code >= 400
    finally:
        subprocess.run = old
        httpd.shutdown()
        httpd.server_close()


def test_api_execute_start_base_version_developer_only():
    """10-15 第3段審查修正（建議）：開發者設定開著時 `/api/execute/start` 帶 `基礎版本` → 記下次組裝的基礎版本；
    沒有那一版回錯誤；沒開開發者設定收到也不理（清掉）。"""
    import urllib.error

    from bookclub import config, execute
    from bookclub import server as srv

    w = _three_versions()
    olds = (config.developer, execute.precheck, execute.run_execute, srv.groq_key_ready)
    httpd, base = _serve(w)

    def wait_idle():
        for _ in range(200):
            if not httpd.exec_running():
                return
            threading.Event().wait(0.02)

    try:
        config.developer = lambda: True
        execute.precheck = lambda wk: {"可以開始": True, "缺": [], "提醒": [], "缺代號": 0}
        execute.run_execute = lambda wk, **kw: {}
        srv.groq_key_ready = lambda: True
        r = tn._quiet(_post, base, "/api/execute/start", {"methods": ["sw"], "基礎版本": 1})
        assert r["started"], r
        wait_idle()
        assert needs.load(w).fmt.get("下次組裝的基礎版本") == 1
        try:
            tn._quiet(_post, base, "/api/execute/start", {"methods": ["sw"], "基礎版本": 9})
            raise AssertionError("沒有的版本不收")
        except urllib.error.HTTPError as e:
            assert e.code == 409 and "9" in json.loads(e.read().decode("utf-8"))["error"]
        config.developer = lambda: False
        r = tn._quiet(_post, base, "/api/execute/start", {"methods": ["sw"], "基礎版本": 1})
        assert r["started"], r
        wait_idle()
        assert "下次組裝的基礎版本" not in needs.load(w).fmt
    finally:
        (config.developer, execute.precheck, execute.run_execute, srv.groq_key_ready) = olds
        httpd.shutdown()
        httpd.server_close()


# ---------- 10-15 總檢查修正（1015-7）：要修改的分流、第 4 步拿得到原因、redo_cards 一份清單 ----------

def _gen_logs(w: Path) -> None:
    """假的生成紀錄：照現在的計畫，全部做好（`test_needs.gen_logs`，1015-8 起假工作區預設就有）。"""
    tn.gen_logs(w)


def _gen_ids(w: Path, role: str) -> set:
    from bookclub import workdir as wd

    return {r["id"] for r in (wd.read_json(fc._role_paths(w, role)[0], default=None) or {}).get("句子") or []}


def test_outside_fix_reaches_step4_and_clears_only_when_redone():
    """A1／A10：跨版本卡、沿用卡、舊版畫面上的卡按「聲音不像、不自然」→ 第 4 步 `redo_plan` 有它（照卡片重做、文字沒改＝換一種念法），
    `prepare_redo` 記版本、清生成句；組裝後等重做清掉、版本 +1、歷史「重做進第 N 版」、新版要重新看。
    只重新組裝 → 等重做留著、歷史不寫「重做進」。"""
    cases = [("跨版本卡", "學員段落:T005", 1, "學員", "T005_01"),
             ("沿用卡（最新版按）", "名字:1", None, "老師", "S001"),
             ("舊版畫面上按", "名字:1", 1, "老師", "S001")]
    for name, key, ver, role, sid in cases:
        for only in (True, False):
            w = _three_versions()
            _gen_logs(w)
            part = finalcards.build(w)
            c = _by(part["卡片"] + part["跨版本卡片"], key)
            r = tn._quiet(finalcards.decide, w, c["id"], needs.RETURNED, ["聲音不像、不自然"], name, version=ver)
            assert r["ok"] and r["統計"]["要重做"] == 1, name
            k = c["卡號"]
            st = needs.load(w)
            assert needs.outside_reason(st.cards[k]) == {"選項": ["聲音不像、不自然"], "補充": name}
            assert [x["卡號"] for x in needs.redo_cards(st)] == [k]
            before = json.dumps(fc.load_check(w)["逐筆"], ensure_ascii=False, sort_keys=True)
            plan = tn._quiet(fc.redo_plan, w)
            e = next(x for x in plan if x.get("卡號") == k)
            assert e["鍵"] == f"卡片:{k}" and e["做法"] == "重新生成" and e["沒改"] is True, (name, e)
            assert [u["id"] for u in e["生成"]] == [sid] and "聲音不像" in e["原因"]
            tn._quiet(fc.prepare_redo, w, log=lambda s: None, reassemble_only=only)
            assert json.dumps(fc.load_check(w)["逐筆"], ensure_ascii=False, sort_keys=True) == before   # 不寫成品檢查檔的逐筆
            if only:
                assert sid in _gen_ids(w, role) and needs.redo_run_cards(w) == set()
            else:
                assert sid not in _gen_ids(w, role) and needs.redo_run_cards(w) == {k}   # 清掉、等一下重新生成
                vers = json.loads((fc._role_paths(w, role)[1] / "_重新生成版本.json").read_text(encoding="utf-8"))
                assert vers[sid]["避開"] == [7]   # 換一種念法：避開用過的種子
            recs = tn._records()
            if not only:   # 真的重新生成：那一句的聲音檔換了
                i = next(j for j, x in enumerate(recs) if x.get("檔案") and sid in x["檔案"])
                recs[i] = {**recs[i], "檔案指紋": f"fp-{sid}-第2版"}
            _assemble(w, _t(3), recs)
            tn._quiet(fc.finish_redo, w)
            st = needs.load(w)
            assert max(st.versions) == 3
            hist = [h for h in needs.read_history(w) if h["卡號"] == k and str(h["動作"]).startswith("重做進第")]
            if only:
                assert needs.outside_reason(st.cards[k]) is not None and not hist, name   # 聲音沒換：留著
                assert _by(finalcards.build(w)["卡片"], key)["狀態"] == finalcards.EDITED
                assert [x["卡號"] for x in needs.redo_cards(st)] == [k]
            else:
                assert not st.cards[k].get("等重做") and [h["動作"] for h in hist] == ["重做進第3版"], name
                assert st.states[(k, 3)]["狀態"] == needs.NOT_SEEN and not st.states[(k, 3)]["沿用自"]
                assert needs.redo_cards(st) == [] and tn._quiet(fc.redo_plan, w) == []


def test_latest_own_card_fixed_on_old_page_goes_normal_way():
    """分流乙：最新一版自己的卡（最新版有它的狀態列）在舊版畫面按「要修改」→ 走最新版正常路（成品檢查檔退回），
    第 4 步照舊從成品檢查檔拿到；那一版的狀態列不動、卡片不記等重做。
    審查修正 7：舊版頁的取消一律撤那一版顯示的狀態（最新版的退回不動）；最新版的退回在最新版頁撤。"""
    w = _three_versions()
    for key in ("學員段落:T003", "刪除段落:S1"):
        o = _by(finalcards.version_page(w, 1, None)["卡片"], key)
        v1_before = needs.card_state(needs.load(w), o["卡號"], 1)
        r = tn._quiet(finalcards.decide, w, o["id"], needs.RETURNED, ["聲音不像、不自然"], "乙", version=1)
        assert r["ok"] and r["版本號"] == 1 and r["卡片"]["狀態"] == needs.RETURNED
        st = needs.load(w)
        assert st.states[(o["卡號"], 2)]["狀態"] == needs.RETURNED
        assert st.states[(o["卡號"], 2)]["退回原因"] == {"選項": ["聲音不像、不自然"], "補充": "乙"}
        assert needs.card_state(st, o["卡號"], 1) == v1_before and needs.outside_reason(st.cards[o["卡號"]]) is None
        keys = [e["鍵"] for e in tn._quiet(fc.redo_plan, w)]
        assert set(_by(finalcards.build(w)["卡片"], key)["紀錄鍵"]) <= set(keys) and not any(k.startswith("卡片:") for k in keys)
        o1 = _by(finalcards.version_page(w, 1, None)["卡片"], key)
        assert o1["最新版要重做"]["補充"] == "乙" and o1["可取消要重做"] is False   # 舊版頁上那一版不是退回：沒有取消要重做
        # 舊版頁送取消：撤的是第 1 版顯示的狀態（T003 第 1 版通過 → 改還沒看；S1 本來就還沒看），最新版的退回不動
        tn._quiet(finalcards.decide, w, o["id"], None, version=1)
        st = needs.load(w)
        assert st.states[(o["卡號"], 2)]["狀態"] == needs.RETURNED and needs.card_state(st, o["卡號"], 1) == needs.NOT_SEEN
        # 最新版頁撤最新版的退回
        c = _by(finalcards.build(w)["卡片"], key)
        assert c["可取消要重做"] is True
        tn._quiet(finalcards.decide, w, c["id"], None)
        st = needs.load(w)
        assert st.states[(o["卡號"], 2)]["狀態"] == needs.NOT_SEEN
        assert not any(e["鍵"] in o["紀錄鍵"] for e in tn._quiet(fc.redo_plan, w))
    c = _by(finalcards.build(w)["卡片"], "學員段落:T003")
    try:
        tn._quiet(finalcards.decide, w, c["id"], None, cancel_redo=True)
        raise AssertionError("沒有要重做的不能取消要重做")
    except ValueError as e:
        assert "沒有要重做" in str(e)


def test_old_page_cancel_undoes_that_version_return():
    """審查修正 7：卡在舊版是退回、最新版也是退回——舊版頁的取消撤舊版那一個，最新版的不動。"""
    w = _three_versions()
    t3 = _by(finalcards.build(w)["卡片"], "學員段落:T003")
    tn._quiet(finalcards.decide, w, t3["id"], needs.RETURNED, ["念錯字"], "最新")
    with needs._locked(w):
        st = needs.load(w)
        st.states[(t3["卡號"], 1)].update({"狀態": needs.RETURNED, "退回原因": {"選項": ["其他"], "補充": "舊"}})
        needs.save(w, st)
    o = _by(finalcards.version_page(w, 1, None)["卡片"], "學員段落:T003")
    assert o["狀態"] == needs.RETURNED and o["可取消要重做"] is True
    tn._quiet(finalcards.decide, w, o["id"], None, version=1)
    st = needs.load(w)
    assert needs.card_state(st, t3["卡號"], 1) == needs.NOT_SEEN and needs.card_state(st, t3["卡號"], 2) == needs.RETURNED


def test_cross_card_returned_on_old_latest_cancel_clears():
    """審查必修 1：第 1 版（當時最新）退回、第 2 版範圍外沒做 → 跨版本卡是退回；在第 1 版取消 → 等重做（起因退回、沒版本號）
    一起清，redo_cards、第 4 步、統計都沒有它（以前卡在「改了、沒原因」，第 4 步列 1 張卻什麼都不做）。"""
    from bookclub import execute

    w = tn._fresh()
    tn._quiet(needs.upgrade, w)
    _assemble(w, _t(1), tn._records(), results={"學員重念@92.00": "退回重做", "名字整句換掉@76.00": "通過"})
    _assemble(w, _t(2), [r for r in tn._records() if r["原片"][0] < 75], rng=(0.0, 75.0), tag="0-1")
    part = finalcards.build(w)
    t5 = _by(part["跨版本卡片"], "學員段落:T005")
    assert t5["狀態"] == needs.RETURNED and t5["可取消要重做"] and part["統計"]["要重做"] == 1
    assert [e["鍵"] for e in tn._quiet(fc.redo_plan, w)] == [f"卡片:{t5['卡號']}"]
    tn._quiet(finalcards.decide, w, t5["id"], None, version=1)
    st = needs.load(w)
    assert not st.cards[t5["卡號"]].get("等重做") and needs.card_state(st, t5["卡號"], 1) == needs.NOT_SEEN
    assert needs.redo_cards(st) == [] and execute.redo_cards_view(w) == [] and tn._quiet(fc.redo_plan, w) == []
    assert finalcards.build(w)["統計"]["要重做"] == 0
    # 起因退回、卻找不到任何退回列的等重做（舊資料）：redo_cards 不算
    with needs._locked(w):
        st = needs.load(w)
        st.cards[t5["卡號"]]["等重做"] = {"起因": needs.WHY_RETURNED, "時間": _t(2)}
        needs.save(w, st)
    assert needs.redo_cards(needs.load(w)) == []


def test_edit_after_reason_keeps_edit_on_cancel():
    """審查必修 2：先記原因（要修改）、之後才改字／範圍 → 記 有改；取消要重做只拿掉原因，改的字照樣要做。"""
    w = _three_versions()
    n1 = _by(finalcards.build(w)["卡片"], "名字:1")
    tn._quiet(finalcards.decide, w, n1["id"], needs.RETURNED, ["聲音不像、不自然"], "")
    tn._quiet(finalcards.edited, w, "名字:1")
    assert needs.load(w).cards[n1["卡號"]]["等重做"]["有改"] is True
    tn._quiet(finalcards.decide, w, n1["id"], None)
    pend = needs.load(w).cards[n1["卡號"]]["等重做"]
    assert pend["起因"] == needs.WHY_EDITED and "退回原因" not in pend
    assert [x["起因"] for x in needs.redo_cards(needs.load(w))] == [needs.REDO_EDITED]


def test_reason_after_reassemble_only_not_orphaned():
    """審查必修 3：沿用卡按要修改 → 只重新組裝 → 新版成了最新版自己的卡、等重做（帶原因）還在：
    (c) 最新版頁有「取消要重做」（可取消要重做），送 null 清掉；(b) 改走正常路的要修改收掉卡片等重做的原因；
    (a) 正常路重做（成品檢查檔退回、重做中沒卡號）組裝後也清等重做，不會再多跑一輪。"""
    for how in ("取消", "再要修改"):
        w = _three_versions()
        _gen_logs(w)
        n1 = _by(finalcards.build(w)["卡片"], "名字:1")
        tn._quiet(finalcards.decide, w, n1["id"], needs.RETURNED, ["聲音不像、不自然"], "快")
        tn._quiet(fc.prepare_redo, w, log=lambda s: None, reassemble_only=True)
        _assemble(w, _t(3), tn._records())
        tn._quiet(fc.finish_redo, w)
        c = _by(finalcards.build(w)["卡片"], "名字:1")
        assert c["狀態"] == finalcards.EDITED and not c["範圍外改了"] and c["可取消要重做"] is True
        if how == "取消":
            r = tn._quiet(finalcards.decide, w, c["id"], None)
            assert r["ok"] and needs.outside_reason(needs.load(w).cards[c["卡號"]]) is None
            assert needs.redo_cards(needs.load(w)) == []
            continue
        tn._quiet(finalcards.decide, w, c["id"], needs.RETURNED, ["聲音不像、不自然"], "再一次")
        st = needs.load(w)
        assert needs.outside_reason(st.cards[c["卡號"]]) is None and st.states[(c["卡號"], 3)]["狀態"] == needs.RETURNED
        assert [x["原因"]["補充"] for x in needs.redo_cards(st)] == ["再一次"]
        plan = tn._quiet(fc.redo_plan, w)
        assert [e["鍵"] for e in plan] == c["紀錄鍵"]
        tn._quiet(fc.prepare_redo, w, log=lambda s: None)
        assert needs.redo_run_cards(w, needs.load(w), fc._index(w)) == {c["卡號"]}
        _gen_logs(w)   # 1015-8 #228：生成程式把清掉的那一句重新生成了（不然 redo_list 照生成程式的判斷還是「已改，等重做」）
        recs = tn._records()
        i = next(j for j, x in enumerate(recs) if x.get("檔案") and "S001" in x["檔案"])
        recs[i] = {**recs[i], "檔案指紋": "fp-new"}
        _assemble(w, _t(4), recs)
        tn._quiet(fc.finish_redo, w)
        st = needs.load(w)
        assert not st.cards[c["卡號"]].get("等重做") and needs.redo_cards(st) == []
        assert _by(finalcards.build(w)["卡片"], "名字:1")["狀態"] == needs.NOT_SEEN


def test_redo_view_has_how_and_range():
    """審查建議 8：第 4 步 要重做 每筆帶 說明（按下去會怎麼重做）、改範圍。"""
    from bookclub import execute

    w = _three_versions()
    _gen_logs(w)
    n1 = _by(finalcards.build(w)["卡片"], "名字:1")
    tn._quiet(finalcards.decide, w, n1["id"], needs.RETURNED, ["聲音不像、不自然"], "")
    st = execute.status(w)
    row = next(x for x in st["要重做"] if x["卡號"] == n1["卡號"])
    assert "換一種念法" in row["說明"] and "改範圍" in row and "等重做那條路" not in row


def test_cancel_outside_redo():
    """A2（暫定）：走等重做那條路的卡可以「取消要重做」（結果 None）→ 清掉等重做；先改了字的只拿掉原因（改的字還要做）。"""
    w = _three_versions()
    n1 = _by(finalcards.build(w)["卡片"], "名字:1")
    tn._quiet(finalcards.decide, w, n1["id"], needs.RETURNED, ["聲音不像、不自然"], "")
    r = tn._quiet(finalcards.decide, w, n1["id"], None)
    c = r["卡片"]
    assert c["這一版沒處理到"] and c["已通過的"] and not needs.load(w).cards[n1["卡號"]].get("等重做")
    assert r["統計"]["要重做"] == 0
    # 舊版畫面上取消（跨版本卡）
    t5 = _by(finalcards.build(w)["跨版本卡片"], "學員段落:T005")
    tn._quiet(finalcards.decide, w, t5["id"], needs.RETURNED, ["念錯字"], "", version=1)
    tn._quiet(finalcards.decide, w, t5["id"], None)   # 按過之後它在最新版清單（已改，等重做），取消不帶版本
    st = needs.load(w)
    assert not st.cards[t5["卡號"]].get("等重做") and st.states[(t5["卡號"], 1)]["狀態"] == needs.NOT_SEEN
    assert any(c["id"] == t5["id"] for c in finalcards.build(w)["跨版本卡片"])
    # 先在第 3 步改了字（等重做，起因改了）、再記原因 → 取消只拿掉原因
    tn._quiet(finalcards.edited, w, "名字:1")
    tn._quiet(finalcards.decide, w, n1["id"], needs.RETURNED, ["念錯字"], "")
    assert needs.load(w).cards[n1["卡號"]]["等重做"].get("有改")
    tn._quiet(finalcards.decide, w, n1["id"], None)
    pend = needs.load(w).cards[n1["卡號"]]["等重做"]
    assert pend["起因"] == needs.WHY_EDITED and "退回原因" not in pend
    assert [x["起因"] for x in needs.redo_cards(needs.load(w))] == [needs.REDO_EDITED]


def test_counts_to_confirm_and_passed():
    """A3：整頁資料給 `還要確認`（上面清單）、`已通過的`（下面清單），每張卡 `已通過的`；舊版頁一樣；舊版輸出問的張數照它。"""
    w = _three_versions()
    part = finalcards.build(w)
    cards, cross, stat = part["卡片"], part["跨版本卡片"], part["統計"]
    ok = [c for c in cards if c["已通過的"]]
    # T003 指紋沒變、第 1 版通過 → 第 2 版通過（沿用）；名字 1 範圍外 → 已通過（沿用上一版）
    assert {c["第3步鍵"] for c in ok} == {"名字:1", "學員段落:T003"}
    assert stat["已通過的"] == 2 and stat["還要確認"] == 2 + len(cross) == 4 and not any(c["已通過的"] for c in cross)
    assert all(c["不能修改"] is False and c["最新版要重做"] is None for c in cards + cross)
    pg = finalcards.version_page(w, 1, None)
    s1 = pg["卡片統計"]
    assert s1["已通過的"] == sum(1 for c in pg["卡片"] if c["已通過的"]) == 2   # 名字 1、T003（照第 1 版）
    assert s1["還要確認"] == len(pg["卡片"]) - 2 == 4
    r = finalcards.export_version(w, 1)
    assert f"還有 {s1['還要確認']} 張沒看完" in r["還差"]["說明"][0]


def test_deleted_card_cannot_fix():
    """A5：刪掉／併入的卡 `不能修改`，按「要修改」擋下。"""
    w = _three_versions()
    t3 = _by(finalcards.build(w)["卡片"], "學員段落:T003")
    with needs._locked(w):
        st = needs.load(w)
        st.cards[t3["卡號"]].update({"狀態": needs.REMOVED, "等重做": {"起因": needs.WHY_EDITED, "時間": _t(2)}})
        needs.save(w, st)
    c = _by(finalcards.build(w)["卡片"], "學員段落:T003")
    assert c["刪掉了"] and c["不能修改"] and c["不能修改原因"] == finalcards.DELETED_NO_FIX
    for ver in (None, 1):
        try:
            tn._quiet(finalcards.decide, w, c["id"], needs.RETURNED, ["其他"], "", version=ver)
            raise AssertionError("刪掉的卡不能要修改")
        except ValueError as e:
            assert str(e) == finalcards.DELETED_NO_FIX


def test_old_page_outside_card_uses_other_version_records():
    """A4：舊版畫面上那一版沒有的卡，能不能換一種念法照最近一個有它的版本（跟最新版一樣）。"""
    w = _three_versions()
    _assemble(w, _t(3), [r for r in tn._records() if r["原片"][0] < 75], rng=(0.0, 75.0), tag="0-1")
    o = _by(finalcards.version_page(w, 2, None)["卡片"], "名字:1")
    assert o["這一版沒處理到"] and o["可重新生成"]


def test_one_redo_list_for_step3_4_5():
    """A9：第 4 步 `要重做`、第 5 步 `統計.要重做`、第 3 步「第 5 步要重做」都照 `needs.redo_cards`。"""
    from bookclub import execute

    w = _three_versions()
    n1 = _by(finalcards.build(w)["卡片"], "名字:1")
    tn._quiet(finalcards.decide, w, n1["id"], needs.RETURNED, ["聲音不像、不自然"], "快")
    s1 = _by(finalcards.build(w)["卡片"], "刪除段落:S1")
    tn._quiet(finalcards.decide, w, s1["id"], needs.RETURNED, ["其他"], "")
    view = execute.redo_cards_view(w)
    assert [x["卡號"] for x in view] == [s1["卡號"], n1["卡號"]] == [x["卡號"] for x in needs.redo_cards(needs.load(w))]
    assert view[1]["原因"] == {"選項": ["聲音不像、不自然"], "補充": "快"} and view[1]["原片"] == [76.3, 76.9]
    assert finalcards.build(w)["統計"]["要重做"] == len(view)
    back = fc.returned_by_card(w, fc._index(w))
    assert back["名字:1"] == ["聲音不像、不自然：快"] and back["刪除段落:S1"] == ["其他"]
    st = execute.status(w)
    assert len(st["要重做"]) == 2 and {x.get("卡號") for x in st["退回清單"]} >= {n1["卡號"]}


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
