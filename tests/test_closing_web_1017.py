"""1015-8 假資料畫面自動測試（Playwright，10-17）抓到的七個問題。先寫這支、在舊程式上確認失敗，再改到通過。

1. 已結案的專案從第 1–4 步回總覽：`ws-closed`／`ws-closed-all` 要先拿掉（不然「切換」「選影片」全灰）
2. 伺服器斷線：第 5 步「結案打包」框留著上次的樣子＋一行「暫時連不到工具，等一下會自動再試」、按鈕都不能按；連回來自動恢復
3. 已結案的第 5 步：快捷鍵 Enter／E／空白鍵／J／L 不做事（↑↓ 換卡片只是看，照樣可以）
4. 已結案的第 3 步：「開始前 4 件事」①～④ 分頁、「開始前 4 件事」開關是只看不改的導覽按鈕，不灰掉；會寫入的照樣灰
5. 已打包後又輸出新成品：狀態一偵測到就直接寫那一句、清掉鈕灰掉
6. 已結案後「再存一份紀錄…」存到一半中斷：重開後有「上次存到一半」＋「繼續」，按下去存完
7. 用字：已結案那一行不重複「已結案」；小於 0.1 GB 用 MB

合成資料，全部在系統暫存資料夾（tests/_testtmp.py）。不開瀏覽器、不叫系統視窗。
獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_closing_web_1017.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from bookclub import closing  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

import test_closing_clean as tc  # noqa: E402

WEB = REPO_ROOT / "bookclub" / "web"
APPJS = (WEB / "app.js").read_text(encoding="utf-8")
FIOJS = (WEB / "fileio.js").read_text(encoding="utf-8")
REVJS = (WEB / "review.js").read_text(encoding="utf-8")
CSS = (WEB / "style.css").read_text(encoding="utf-8")
NODE = shutil.which("node")
HARNESS = REPO_ROOT / "tests" / "web_harness.js"
OFFLINE = "暫時連不到工具，等一下會自動再試"


def _node(code: str):
    r = subprocess.run([NODE, "-e", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _harness(files: list[str], pre: str, scenarios: list[dict]) -> dict:
    d = Path(tempfile.mkdtemp())
    (d / "spec.json").write_text(json.dumps({"前置": pre, "情境": scenarios}, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([NODE, str(HARNESS), ",".join(str(WEB / f) for f in files), str(d / "spec.json")],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


# ---------------------------------------------------------------------------
# 1. 回總覽先拿掉瘦身版的 class
# ---------------------------------------------------------------------------

def test_1_overview_drops_closed_classes():
    if not NODE:
        print("（沒有 node，略過）")
        return
    fn = tc._fn(APPJS, "renderRoute")
    code = """
      const classes = new Set(["ws-closed", "ws-closed-all"]);
      const seen = [];
      const document = {body: {classList: {remove: (...c) => c.forEach((x) => classes.delete(x)), toggle() {}, add() {}}}};
      let navSubWant = null, route = "overview", lastState = {"已結案": true};
      const contentEl = {classList: {remove() {}}, innerHTML: ""};
      async function renderSidebar() {}
      function currentRouteId() { return route; }
      function stopStatusPoll() {}
      async function renderOverview() { seen.push(["overview", [...classes]]); }
      async function renderProfile() { seen.push(["step0", [...classes]]); }
      function esc(s) { return String(s); }
    """ + fn + """
      (async () => {
        await renderRoute();
        classes.add("ws-closed"); classes.add("ws-closed-all"); route = "step0"; await renderRoute();
        console.log(JSON.stringify(seen));
      })();"""
    got = _node(code)
    assert got == [["overview", []], ["step0", []]], got


# ---------------------------------------------------------------------------
# 2. 斷線時框留著＋一行提醒、按鈕不能按；連回來自動恢復
# ---------------------------------------------------------------------------

_BOX_PRE = """
  H.timers = [];
  setTimeout = (fn, ms) => { H.timers.push([fn, ms]); return H.timers.length; };
  clearTimeout = () => {};
  H.apiGetData['/api/final/outputs'] = {"成品": "輸出/最終成品.mp4", "系統": "mac"};
  const ST = {"輸出過": true, "狀態": null, "可以打包": true, "系統視窗": false, "暫存大小": 2.6e9, "清單指紋": "fp",
              "工作": null, "可以繼續": false};
  H.apiGetData['/api/close/state'] = ST;
  const realGet = apiGet;
  function down() { apiGet = async (url) => { H.log.push(["GET 失敗", url]); throw Object.assign(new Error("Failed to fetch"), {network: true}); }; }
  function up() { apiGet = realGet; }
  async function runTimers() { const t = H.timers.splice(0); for (const [fn] of t) await fn(); await runTimers.ticks(); }
  runTimers.ticks = async () => { for (let i = 0; i < 50; i++) await null; };
  function btns(box) { return box.querySelectorAll("button").map((b) => [b.id, !!b.disabled]); }
  function offline(box) { const l = box.querySelector("#cl-offline"); return l && !l.hidden ? l.textContent : ""; }
"""


def test_2_offline_keeps_box_and_recovers():
    if not NODE:
        print("（沒有 node，略過）")
        return
    got = _harness(["fileio.js"], _BOX_PRE, [
        {"名稱": "框自己的輪詢斷線", "程式": """
          const box = H.makeEl("div", {id: "clBox"}, "");
          await closeBoxInit(box);
          const before = box.innerHTML;
          down(); await closeBoxInit(box);
          const off = {hidden: box.hidden, html: offline(box), btns: btns(box), kept: box.innerHTML.includes("結案打包…"),
                       retry: H.timers.length};
          up(); await runTimers();
          return {before: before.includes("結案打包…"), off, back: {hidden: box.hidden, html: offline(box), btns: btns(box)}};"""},
        {"名稱": "存檔中斷線：停止鈕不能按", "程式": """
          H.apiGetData['/api/close/state'] = {...ST, "工作": {"狀態": "存檔中", "第幾個": 2, "共幾個": 9}};
          const box = H.makeEl("div", {id: "clBox"}, "");
          await closeBoxInit(box);
          H.timers.length = 0;
          down(); await closeBoxInit(box);
          return {hidden: box.hidden, html: offline(box), btns: btns(box), retry: H.timers.length};"""},
        {"名稱": "整個第 5 步下面那塊斷線", "程式": """
          const el = H.makeEl("div", {id: "foOut"}, "");
          await fileOutInit(el); await runTimers();
          down(); await fileOutInit(el); await runTimers.ticks();
          const box = el.querySelector("#clBox"), main = el.querySelector("#foMain");
          const off = {mainKept: main.innerHTML.includes("fo-open"), boxHidden: box.hidden, boxHtml: offline(box),
                       foOpen: main.querySelector("#fo-open").disabled, retry: H.timers.length};
          up(); await runTimers();
          return {off, backBox: offline(box), backOpen: main.querySelector("#fo-open").disabled};"""},
    ])
    a = got["框自己的輪詢斷線"]
    assert a["before"], a
    assert not a["off"]["hidden"] and a["off"]["kept"] and OFFLINE in a["off"]["html"], a
    assert a["off"]["btns"] and all(d for _i, d in a["off"]["btns"]), a["off"]["btns"]
    assert a["off"]["retry"] >= 1, a
    assert OFFLINE not in a["back"]["html"] and not a["back"]["hidden"], a["back"]
    assert ("cl-go", False) in [tuple(x) for x in a["back"]["btns"]], a["back"]["btns"]
    b = got["存檔中斷線：停止鈕不能按"]
    assert not b["hidden"] and OFFLINE in b["html"] and ["cl-stop", True] in b["btns"] and b["retry"] >= 1, b
    c = got["整個第 5 步下面那塊斷線"]
    assert c["off"]["mainKept"] and c["off"]["foOpen"] and not c["off"]["boxHidden"] and OFFLINE in c["off"]["boxHtml"], c
    assert c["off"]["retry"] >= 1, c
    assert OFFLINE not in c["backBox"] and c["backOpen"] is False, c


# ---------------------------------------------------------------------------
# 3. 已結案的第 5 步快捷鍵不送出
# ---------------------------------------------------------------------------

def test_3_closed_step5_shortcuts_do_nothing():
    if not NODE:
        print("（沒有 node，略過）")
        return
    import test_final_cards_web as tw

    w = tc._closed_fresh()
    httpd, base = tc._serve(w)
    try:
        code, data = tc._call(base, "/api/final")
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert code == 200 and data["已結案"]
    got = tw._run_page([{"名稱": "快捷鍵", "程式": """
          await renderFinal();
          const c = DATA["卡片"].find((x) => fcCanJudge(x));
          if (!c) return {錯誤: "沒有能判斷的卡"};
          fcSelect(c.id, false);
          H.log.length = 0;
          const kd = H.docListeners.filter((l) => l.type === "keydown");
          for (const key of ["Enter", "e", " ", "j", "l"]) {
            for (const l of kd) await l.fn({key, keyCode: 0, target: {tagName: "DIV"}, preventDefault() {}, isComposing: false});
          }
          const posts = H.log.filter((x) => x[0] === "POST");
          const before = fc.cur;
          for (const l of kd) await l.fn({key: "ArrowDown", keyCode: 0, target: {tagName: "DIV"}, preventDefault() {}, isComposing: false});
          return {n: kd.length, posts, fixOpen: !!document.getElementById("fc-fix-box") && !document.getElementById("fc-fix-box").hidden,
                  moved: fc.cur !== before, cards: DATA["卡片"].length};"""}], data=data)
    r = got["快捷鍵"]
    assert r.get("n", 0) >= 1, r
    assert r["posts"] == [], r["posts"]
    if r["cards"] > 1:
        assert r["moved"], r          # ↑↓ 換卡片只是看，照樣可以


# ---------------------------------------------------------------------------
# 4. 已結案第 3 步：只看不改的導覽按鈕不灰掉
# ---------------------------------------------------------------------------

def test_4_closed_step3_nav_buttons_not_greyed():
    rule = CSS[CSS.index("body.ws-closed-all #content button"):]
    rule = rule[:rule.index("{")]
    assert "body.ws-closed-all #content button:not(.ws-nav)" in rule, rule
    # 已結案時畫面（rvApplyReadonly）替只看不改的按鈕補上 ws-nav：開始前 4 件事 ①～④ 分頁與「開始前 4 件事／逐筆確認」兩個分頁、右上「⋯」、篩選、上一筆下一筆、
    # 設定、快捷鍵、代號對照；播放（聲音清掉了）、匯出（會寫檔）、開始逐筆看不在內。沒結案時畫面一個字都不變（快照）
    m = re.search(r'^const RV_CLOSED_NAV = "([^"]+)";', REVJS, re.M)
    assert m, "RV_CLOSED_NAV"
    nav = [x.strip() for x in m.group(1).split(",")]
    for want in ("[data-tab]", "#rv-prep-btn", "#rv-cards-btn", "#rv-more-btn", "#rv-prepnext", ".rv-filters [data-f]", "#rv-prev", "#rv-next", "#rv-key-btn"):
        assert want in nav, want
    for no in ("#rv-export", "#rv-start", "#rv-prepdone", ".rv-segplay", "#rv-playonly", "[data-te-play]"):
        assert no not in nav, no
    body = tc._fn(REVJS, "rvApplyReadonly")
    assert "RV_CLOSED_NAV" in body and "wsClosed()" in body


def test_4_closed_step3_prep_tabs_get_ws_nav_on_page():
    """畫第 3 步「開始前 4 件事」：已結案時 ①～④ 分頁有 ws-nav、「開始逐筆看」沒有；沒結案時一個都沒有。"""
    if not NODE:
        print("（沒有 node，略過）")
        return
    import test_cards_snapshot as cs

    spec = cs._scenarios(cs._items())[0]
    pre = spec["前置"] + "H.els['rvroot'] = H.makeEl('div', {id: 'rvroot', class: 'rv2'}, '');\n"
    prog = """
          setup(); rv.prepOpen = true; rv.prepTab = "刪除"; rvRenderPrepSide(); rvRenderPrep();
          const tabs = document.querySelectorAll("[data-tab]").map((b) => b.classList.contains("ws-nav"));
          const start = document.getElementById("rv-prepdone").classList.contains("ws-nav");
          return {tabs, start};"""
    got = _harness(cs.STEP3_FILES and [Path(f).name for f in cs.STEP3_FILES], pre, [
        {"名稱": "已結案", "程式": "wsClosed = () => true;" + prog},
        {"名稱": "沒結案", "程式": "wsClosed = () => false;" + prog},
    ])
    a, b = got["已結案"], got["沒結案"]
    assert a["tabs"] and all(a["tabs"]) and not a["start"], a
    assert b["tabs"] and not any(b["tabs"]) and not b["start"], b


def test_3b_closed_step3_shortcuts_do_nothing():
    """已結案的第 3 步：Enter（通過）／E／空白鍵／J／L／I／O 不做事（以前「通過＝剪掉這段」先跳確認、按了確定沒反應）；↑↓ 換卡片照樣可以。"""
    if not NODE:
        print("（沒有 node，略過）")
        return
    import test_cards_snapshot as cs

    spec = cs._scenarios(cs._items())[0]
    prog = """
          setup(); rv.cur = "名字:1"; rv.open = false; rvRenderCard(); H.log.length = 0;
          const kd = H.docListeners.filter((l) => l.type === "keydown");
          const ev = (key) => ({key, keyCode: 0, target: {tagName: "DIV", id: "", closest: () => null}, preventDefault() {}, isComposing: false});
          for (const key of ["Enter", "e", " ", "j", "l", "i", "o"]) for (const l of kd) await l.fn(ev(key));
          const log = H.log.filter((x) => ["POST", "confirm", "alert"].includes(x[0]));
          const open = rv.open, edOpen = !!(rv.ed && rv.ed.open), before = rv.cur;
          for (const l of kd) await l.fn(ev("ArrowDown"));
          return {n: kd.length, log, open, edOpen, moved: rv.cur !== before};"""
    got = _harness([Path(f).name for f in cs.STEP3_FILES], spec["前置"], [
        {"名稱": "已結案", "程式": "wsClosed = () => true;" + prog},
        {"名稱": "沒結案", "程式": "wsClosed = () => false;" + prog},
    ])
    a, b = got["已結案"], got["沒結案"]
    assert a.get("n", 0) >= 1, a
    assert a["log"] == [] and not a["open"] and not a["edOpen"], a
    assert a["moved"], a                      # ↑↓ 換卡片只是看
    assert b["log"] or b["open"] or b["edOpen"], b   # 沒結案：快捷鍵照常（對照組）


# ---------------------------------------------------------------------------
# 5. 已打包後又輸出新成品：直接寫那一句、清掉鈕灰掉
# ---------------------------------------------------------------------------

def test_5_new_export_after_pack_shown_up_front():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        w = tc._lesson1(root)
        with redirect_stdout(io.StringIO()):
            tc._pack(w, root / "D槽")
        assert not closing.pack_state(w).get("存好後又輸出")
        new = tc._mk(w / "輸出/最終成品_0-97_sw_20261017-090000.mp4", os.urandom(3000))
        chk = wd.read_json(w / "覆核/成品檢查.json")
        chk["輸出成品"]["檔案"] = str(new.relative_to(w))
        wd.write_json(w / "覆核/成品檢查.json", chk)
        st = closing.pack_state(w)
    assert st["狀態"] == closing.PACKED and st["存好後又輸出"] == closing.NEW_EXPORT, st.get("存好後又輸出")
    if not NODE:
        return
    st = {**st, "工作": {"狀態": "已存好", "資料夾": "D:\\x"}, "可以打包": True, "系統視窗": False}
    v = _node(tc._fn(FIOJS, "clGB") + tc._fn(FIOJS, "clView") + f"console.log(JSON.stringify(clView({json.dumps(st, ensure_ascii=False)})));")
    assert v["status"] == closing.NEW_EXPORT and "已存好" not in v["status"], v
    assert v["cleanBtn"] and v["cleanOff"], v
    got = _harness(["fileio.js"], _BOX_PRE + f"H.apiGetData['/api/close/state'] = {json.dumps(st, ensure_ascii=False)};", [
        {"名稱": "框", "程式": """
          const box = H.makeEl("div", {id: "clBox"}, ""); await closeBoxInit(box);
          return {html: box.innerHTML, btns: btns(box)};"""}])
    r = got["框"]
    assert ["cl-clean", True] in r["btns"] and closing.NEW_EXPORT in r["html"].replace("&quot;", '"'), r


# ---------------------------------------------------------------------------
# 6. 已結案後「再存一份紀錄…」存到一半中斷：重開後能「繼續」
# ---------------------------------------------------------------------------

def test_6_closed_records_pack_resume_after_restart():
    w = tc._closed_fresh()
    loc = w.parent / "再存一份"
    loc.mkdir()
    job = closing.PackJob(w, loc, records_only=True)
    orig, n = job._one, [0]

    def one(folder, it):   # 存到第 2 個檔時伺服器被關掉
        n[0] += 1
        if n[0] == 2:
            raise KeyboardInterrupt
        return orig(folder, it)

    job._one = one
    try:
        job._run()
    except KeyboardInterrupt:
        pass
    left = list((loc / closing.PACK_ROOT).iterdir())
    assert len(left) == 1 and left[0].name.endswith(closing.PACKING_SUFFIX), left
    httpd, base = tc._serve(w)   # 重開：伺服器沒有工作
    try:
        code, st = tc._call(base, "/api/close/state")
        assert code == 200 and st["狀態"] == closing.CLOSED and st["可以繼續"] and st["工作"] is None, st
        if NODE:
            v = _node(tc._fn(FIOJS, "clGB") + tc._fn(FIOJS, "clView") + f"console.log(JSON.stringify(clView({json.dumps(st, ensure_ascii=False)})));")
            assert v["resume"] and "上次存到一半" in v["status"], v
        code, r = tc._call(base, "/api/close/pack", {"繼續": True})
        assert code == 200, r
        st = tc._wait(base)
        assert st["工作"]["狀態"] == "已存好", st["工作"]
        assert st["狀態"] == closing.CLOSED and not st["可以繼續"]
    finally:
        httpd.shutdown()
        httpd.server_close()
    names = [p.name for p in (loc / closing.PACK_ROOT).iterdir()]
    assert len(names) == 1 and not names[0].endswith(closing.PACKING_SUFFIX), names


# ---------------------------------------------------------------------------
# 7. 用字
# ---------------------------------------------------------------------------

def test_7_wording_closed_line_and_mb():
    if not NODE:
        print("（沒有 node，略過）")
        return
    code = tc._fn(FIOJS, "clGB") + tc._fn(FIOJS, "clView") + """
      const base = {"暫存大小": 4.2e7, "系統": "mac", "狀態": null, "工作": null, "可以繼續": false};
      console.log(JSON.stringify({
        gb: [clGB(0), clGB(4.2e7), clGB(9.9e7), clGB(1e8), clGB(2.6e9), clGB(3e5)],
        line: clView(base).line,
        closed: clView({...base, "狀態": "已結案", "只有紀錄": true, "清理": {"省下位元組": 3.1e7, "成品存在": "D:\\\\x"}}),
      }));"""
    v = _node(code)
    assert v["gb"] == ["0 MB", "42 MB", "99 MB", "0.1 GB", "2.6 GB", "不到 1 MB"], v["gb"]
    assert "（共 42 MB）" in v["line"], v["line"]
    c = v["closed"]
    assert c["status"].startswith("已結案，空出 31 MB。") and c["badge"] == "", c


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            with redirect_stdout(io.StringIO()):
                fn()
            print(f"✓ {fn.__name__}")
        except (Exception, SystemExit) as e:  # noqa: BLE001 — TDD：全部跑完、列出哪幾條沒過
            failed += 1
            print(f"✗ {fn.__name__}：{type(e).__name__}：{str(e)[:200]}")
    print(f"{len(tests) - failed}/{len(tests)} 通過")
    sys.exit(1 if failed else 0)
