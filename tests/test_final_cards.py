"""第 5 步卡片（`bookclub/finalcards.py`，10-15 第5步2.0 第2段）的後端測試。

- 新紀錄（needs）能用時：卡片狀態照成品狀態表；不能用（同步錯誤、沒升級）時退回讀舊檔，結果一樣
- 卡片通過／退回回寫舊檔的對應筆：卡片通過→每一筆通過；卡片退回→每一筆退回、原因同一句；兩邊讀起來一致
- 退回原因的選項存在新紀錄、舊檔是合成的一句；重算之後選項還在
- 第 5 步按「改」→「已改，等重做」；下一次組裝（建版本）清掉
- `GET /api/final` 帶卡片與第 3 步資料、`POST /api/final/card`、`/api/final/edited`（隨機埠起伺服器，不開瀏覽器）
用 tests/test_needs.py 的假工作區（合成資料），不跑模型。獨立可跑：.venv/bin/python tests/test_final_cards.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401

import json
import sys
import threading
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_needs as tn  # noqa: E402 — 假工作區、處理紀錄、成品檢查的小工具

from bookclub import finalcards, needs  # noqa: E402
from bookclub import finalcheck as fc  # noqa: E402

T1, T2 = tn.T1, tn.T2


def _ready(results: dict | None = None):
    w = tn._fresh()
    log = tn._log()
    tn._setup(w, log, tn._check(log, results or {}))
    tn._quiet(needs.upgrade, w)
    return w, log


def _by(cards: list[dict], key: str) -> dict:
    return next(c for c in cards if c.get("第3步鍵") == key)


def test_cards_from_needs_and_legacy_agree():
    w, log = _ready({"學員重念@40.00": ["通過"], "學員重念@55.00": ["退回重做", "第二格念錯"], "名字整句換掉@76.00": ["通過"]})
    part = finalcards.build(w)
    assert part["來源"] == finalcards.SRC_NEW
    cards = part["卡片"]
    t3 = _by(cards, "學員段落:T003")
    assert t3["狀態"] == needs.RETURNED and t3["紀錄鍵"] == ["學員重念@40.00", "學員重念@55.00"] and t3["卡號"].startswith("K")
    assert t3["退回原因"]["補充"] == "第二格念錯" and t3["可重新生成"] and t3["編號"] == int(t3["卡號"][1:])
    assert _by(cards, "名字:1")["狀態"] == needs.PASSED and _by(cards, "學員段落:T005")["狀態"] == needs.NOT_SEEN
    assert _by(cards, "刪除段落:S1")["可重新生成"] is False   # 剪掉：重做只會重新放一次
    kinds = sorted(c["類型"] for c in cards)
    assert needs.UNLOGGED in kinds and needs.UNCLEAR in kinds   # 沒登記的變動、對不到卡片的停格
    # 退回讀舊檔：同一份判斷，卡片狀態一樣
    old = finalcards._from_old(w, log, fc.refresh(fc.load_check(w), log), fc._index(w), {})
    for k in ("學員段落:T003", "名字:1", "學員段落:T005", "刪除段落:S1"):
        assert _by(old, k)["狀態"] == _by(cards, k)["狀態"], k
    assert part["統計"]["退回"] == 1 and part["統計"]["要重做"] == 1


def test_unhealthy_reads_legacy():
    w, log = _ready({"學員重念@40.00": ["退回重做", "x"]})
    needs.note_error(w, "第5步存檔", RuntimeError("boom"))
    part = finalcards.build(w)
    assert part["來源"] == finalcards.SRC_OLD
    t3 = _by(part["卡片"], "學員段落:T003")
    assert t3["卡號"] is None and t3["id"] == "學員段落:T003" and t3["狀態"] == needs.RETURNED
    # 舊檔模式也能按：卡片通過 → 舊檔每一筆通過
    finalcards.decide(w, "學員段落:T003", needs.PASSED)
    rows = fc.load_check(w)["逐筆"]
    assert rows["學員重念@40.00"]["結果"] == fc.PASS and rows["學員重念@55.00"]["結果"] == fc.PASS


def test_card_pass_and_return_write_back_every_record():
    """反向規則：卡片通過→該卡所有筆通過；卡片退回→所有筆退回、原因同一份；新紀錄跟舊檔讀起來一致。"""
    w, log = _ready({"學員重念@40.00": ["通過"]})
    c = _by(finalcards.build(w)["卡片"], "學員段落:T003")
    tn._quiet(finalcards.decide, w, c["id"], needs.RETURNED, ["念錯字", "長度或停頓怪"], "第三句")
    rows = fc.load_check(w)["逐筆"]
    want = "念錯字、長度或停頓怪：第三句"
    assert [rows[k]["結果"] for k in c["紀錄鍵"]] == [fc.REDO, fc.REDO] and {rows[k]["原因"] for k in c["紀錄鍵"]} == {want}
    st = needs.load(w)
    v = st.latest_version()["版本號"]
    row = st.states[(c["卡號"], v)]
    assert row["狀態"] == needs.RETURNED and row["退回原因"] == {"選項": ["念錯字", "長度或停頓怪"], "補充": "第三句"}
    assert (st.cards[c["卡號"]].get("等重做") or {}).get("起因") == needs.WHY_RETURNED
    # 別的卡存檔（重算）之後，選項還在
    tn._quiet(finalcards.decide, w, _by(finalcards.build(w)["卡片"], "名字:1")["id"], needs.PASSED)
    assert needs.load(w).states[(c["卡號"], v)]["退回原因"]["選項"] == ["念錯字", "長度或停頓怪"]
    # 通過 → 每一筆通過；舊檔的對法（任一筆退回＝退回）算出來也是通過
    tn._quiet(finalcards.decide, w, c["id"], needs.PASSED)
    rows = fc.load_check(w)["逐筆"]
    assert all(rows[k]["結果"] == fc.PASS for k in c["紀錄鍵"])
    assert finalcards.legacy_state([rows[k]["結果"] for k in c["紀錄鍵"]]) == needs.PASSED
    assert _by(finalcards.build(w)["卡片"], "學員段落:T003")["狀態"] == needs.PASSED
    assert not needs.load(w).cards[c["卡號"]].get("等重做")
    # 改回還沒看 → 每一筆拿掉
    tn._quiet(finalcards.decide, w, c["id"], None)
    rows = fc.load_check(w)["逐筆"]
    assert not any(k in rows for k in c["紀錄鍵"]) and _by(finalcards.build(w)["卡片"], "學員段落:T003")["狀態"] == needs.NOT_SEEN


def test_unlogged_and_unclear_cards():
    w, log = _ready()
    cards = finalcards.build(w)["卡片"]
    un = next(c for c in cards if c["類型"] == needs.UNLOGGED)
    tn._quiet(finalcards.decide, w, un["id"], needs.PASSED)
    assert list(fc.load_check(w)["未登記確認"].values())[0]["結果"] == fc.UNLOGGED_OK
    assert next(c for c in finalcards.build(w)["卡片"] if c["id"] == un["id"])["狀態"] == needs.PASSED
    # 整片退回（對不到一張卡）→ 待釐清卡；通過＝拿掉那一筆整片退回，卡片從清單消失
    tn._quiet(fc.add_whole_redo, w, 130.0, "這裡怪怪的")
    flag = next(c for c in finalcards.build(w)["卡片"] if c["整片退回"] and c["類型"] == needs.UNCLEAR)
    assert flag["狀態"] == needs.RETURNED and flag["說明"] == "這裡怪怪的"
    tn._quiet(finalcards.decide, w, flag["id"], needs.PASSED)
    assert fc.load_check(w)["整片退回"] == [] and not any(c["id"] == flag["id"] for c in finalcards.build(w)["卡片"])


def test_edited_then_next_version_clears():
    w, log = _ready()
    r = finalcards.edited(w, "學員段落:T005")
    assert r["記了"]
    c = _by(finalcards.build(w)["卡片"], "學員段落:T005")
    assert c["狀態"] == finalcards.EDITED and finalcards.build(w)["統計"]["已改"] == 1
    try:
        tn._quiet(finalcards.decide, w, c["id"], needs.PASSED)
        raise AssertionError("已改的不能按通過")
    except ValueError:
        pass
    # 第 4 步組裝出新的一版 → 已改清掉（做進這一版了）
    log2 = tn._log(stamp=T2)
    tn._setup(w, log2, None)
    tn._quiet(needs.add_version, w, log2)
    c2 = _by(finalcards.build(w)["卡片"], "學員段落:T005")
    assert c2["狀態"] == needs.NOT_SEEN and not needs.load(w).cards[c2["卡號"]].get("等重做")


def test_reason_text_pure():
    assert needs.reason_text(["其他", "念錯字"], "  ") == "念錯字、其他"
    assert needs.reason_text([], "只有補充") == "只有補充"
    assert needs.reason_text(["亂寫的"], "") == needs.REASON_NONE
    assert finalcards.legacy_state([fc.PASS, None]) == needs.NOT_SEEN and finalcards.legacy_state([]) == needs.NOT_SEEN


def _serve(w: Path):
    from bookclub import server

    httpd = server.BookclubServer(("127.0.0.1", 0), server.Handler, workdir=w, video=None)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Origin": base}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def test_api_final_cards_endpoints():
    w, log = _ready()
    httpd, base = _serve(w)
    try:
        d = tn._quiet(_get, base, "/api/final")
        assert d["卡片來源"] == finalcards.SRC_NEW and d["卡片"] and d["退回原因選項"] == list(needs.REASON_OPTIONS)
        assert {"項目", "學員", "選項"} <= set(d["第3步"]) and d["AI執行中"] is False
        t3 = _by(d["卡片"], "學員段落:T003")
        assert t3["有第3步卡片"] and any(f"{it['類型']}:{it['id']}" == "學員段落:T003" for it in d["第3步"]["項目"])
        r = tn._quiet(_post, base, "/api/final/card", {"id": t3["id"], "結果": "退回", "選項": ["念錯字"], "補充": ""})
        assert r["卡片"]["狀態"] == needs.RETURNED and r["卡片"]["退回原因"]["選項"] == ["念錯字"]
        assert fc.load_check(w)["逐筆"]["學員重念@55.00"]["原因"] == "念錯字"
        r = tn._quiet(_post, base, "/api/final/edited", {"鍵": "學員段落:T005"})
        assert r["記了"]
        d = tn._quiet(_get, base, "/api/final")
        assert _by(d["卡片"], "學員段落:T005")["狀態"] == finalcards.EDITED and d["卡片統計"]["要重做"] == 2
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
