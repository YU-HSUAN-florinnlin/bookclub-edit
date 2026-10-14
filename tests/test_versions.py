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
    assert _by(cards, "學員段落:T003")["版本號"] == 2 and not _by(cards, "學員段落:T003")["這一版沒處理到"]
    assert not any(c.get("第3步鍵") == "學員段落:T007" for c in cards + cross)   # 哪一版都沒處理過的：不列
    st = part["統計"]
    assert st["要重做"] == 0   # 範圍外的不算進「重做這幾張」
    # 沒處理到的不能按通過
    try:
        finalcards.decide(w, n1["id"], needs.PASSED)
        raise AssertionError("沒處理到的不能按通過")
    except ValueError as e:
        assert "沒處理到" in str(e)
    # 在「上一版還沒看的」那一區按通過：記在第 1 版，不寫成品檢查檔
    before = json.dumps(fc.load_check(w), ensure_ascii=False, sort_keys=True)
    r = finalcards.decide(w, t5["id"], needs.PASSED, version=1)
    assert r["卡片"]["狀態"] == needs.PASSED and r["版本號"] == 1
    assert json.dumps(fc.load_check(w), ensure_ascii=False, sort_keys=True) == before
    assert needs.card_state(needs.load(w), t5["卡號"], 1) == needs.PASSED
    part = finalcards.build(w)
    assert not any(c["id"] == t5["id"] for c in part["跨版本卡片"])
    assert _by(part["卡片"], "學員段落:T005")["這一版沒處理到"]


def test_old_version_page_and_decide_and_watch():
    w = _three_versions()
    assert finalcards.current_version(w) == 2
    pg = finalcards.version_page(w, 1, None)
    assert pg["看的是舊版"] and pg["目前版本"] == 1 and pg["最新版本"] == 2 and pg["影片網址"] == "/api/final/video?版本=1"
    assert [v["版本號"] for v in pg["版本清單"]] == [2, 1] and pg["版本清單"][0]["最新"] and pg["版本清單"][1]["有影片"]
    cards = pg["卡片"]
    assert _by(cards, "名字:1")["狀態"] == needs.PASSED and _by(cards, "學員段落:T005")["狀態"] == needs.NOT_SEEN
    assert not any(c["這一版沒處理到"] for c in cards)   # 第 1 版是整支
    # 舊版上退回：記在那一版、卡片等重做（帶版本號）；最新一版的成品檢查檔不動
    t5 = _by(cards, "學員段落:T005")
    finalcards.decide(w, t5["id"], needs.RETURNED, ["念錯字"], "x", version=1)
    st = needs.load(w)
    assert st.states[(t5["卡號"], 1)]["退回原因"] == {"選項": ["念錯字"], "補充": "x"}
    assert st.cards[t5["卡號"]]["等重做"]["版本號"] == 1
    tn._quiet(needs.mirror_final, w, force=True)   # 最新一版重算不會把它清掉（範圍外）
    assert needs.load(w).cards[t5["卡號"]]["等重做"]
    assert _by(finalcards.build(w)["跨版本卡片"], "學員段落:T005")["狀態"] == needs.RETURNED
    # 舊版上看過的：記在那一版自己的一份
    tn._quiet(finalcards.version_watched, w, 1, [[1.0, 2.0]])   # 假影片量不出長度：不截
    tn._quiet(finalcards.version_watched, w, 1, [[1.5, 3.0]])
    assert needs.watched(w, 1) == [[1.0, 3.0]] and needs.watched(w, 2) == []
    # 第 3 版整支又做了 T005（退回之後才組）→ 等重做清掉
    _assemble(w, _t(3), tn._records())
    assert not needs.load(w).cards[t5["卡號"]]["等重做"]


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
        assert tn._quiet(_get, base, "/api/final?版本=2")["看的是舊版"] is False   # 最新那一版＝一般畫面
        s = tn._quiet(_get, base, "/api/state")
        assert s["開發者"] is False
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
