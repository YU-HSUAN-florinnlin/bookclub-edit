"""第 5 步新畫面（`bookclub/web/finalcheck.js`，10-15 第5步2.0 第2段）的 node 測試。

- 純函式：頂端那一句（31 號設計 §6.8 的優先順序）、卡片標籤、分頁、上一張下一張、通過後找下一張、試聽怎麼播、
  原片↔成品時間（跟後端同一套換算）、退回原因那一行、拿檔按鈕旁那一行（fileio.js）、第 4 步跑完要不要跳回第 5 步（app.js）
- 整頁：用真的後端算出來的 `/api/final`（tests/test_needs.py 的假工作區），在 node 的假瀏覽器（tests/web_harness.js）
  畫第 5 步，按通過／退回（選原因）／改（第 3 步卡片元件、存檔後記「已改」）／新增「不確定」／重做這幾張，比送出去的 API
- 提示文字：§6.8 拿掉的那幾句、§8 拿掉的按鈕都不在
網頁不開瀏覽器；畫面行為要人實際點過（見 CHANGELOG）。獨立可跑：.venv/bin/python tests/test_final_cards_web.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401

import io
import json
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

WEB = REPO_ROOT / "bookclub" / "web"
FCJS = (WEB / "finalcheck.js").read_text(encoding="utf-8")
APPJS = (WEB / "app.js").read_text(encoding="utf-8")
FIOJS = (WEB / "fileio.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
HARNESS = REPO_ROOT / "tests" / "web_harness.js"
_ESC = 'const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");\n'


def _fn(src: str, name: str) -> str:
    m = re.search(rf"^(async )?function {name}\(.*?^\}}\n", src, re.S | re.M)
    assert m, name
    return m.group(0)


def _const(src: str, name: str) -> str:
    m = re.search(rf"^const {name} = [^\n]*\n", src, re.M)
    assert m, name
    return m.group(0)


def _node(code: str):
    return json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True).stdout)


_PURE = (_ESC + _const(FCJS, "FC_AB_CONTEXT") + _const(FCJS, "FC_ST") + _const(FCJS, "FC_TODO") + _const(FCJS, "FC_REDO")
         + _const(FCJS, "FC_TAB_ALL") + _const(FCJS, "FC_TYPE_TABS")
         + _fn(FCJS, "fcShortStamp") + _fn(FCJS, "fcCardLabel") + _fn(FCJS, "fcReasonText") + _fn(FCJS, "fcStatusLine")
         + _const(FCJS, "FC_NO_REASON") + _fn(FCJS, "fcWhyLine") + _fn(FCJS, "fcSharedLine") + _fn(FCJS, "fcRedoCount")
         + _const(FCJS, "FC_NOTE_FIELDS") + _fn(FCJS, "fcNoteOnly")
         + _fn(FCJS, "fcTabList") + _fn(FCJS, "fcPickTab") + _fn(FCJS, "fcStepPick") + _fn(FCJS, "fcNextUnseen")
         + _fn(FCJS, "fcSrcTime") + _fn(FCJS, "fcOutTime") + _fn(FCJS, "fcPadOf") + _fn(FCJS, "fcVclip") + _fn(FCJS, "fcCardAbPlan")
         + _fn(FCJS, "fcOrigText") + _fn(FCJS, "fcCardTextHtml")
         + _fn(FCJS, "fcVersionText") + _fn(FCJS, "fcVersionMenuHtml") + _fn(FCJS, "fcCanJudge") + _fn(FCJS, "fcCanReturn"))


def test_status_line_one_sentence_in_priority():
    if not NODE:
        print("（沒有 node，略過）")
        return
    got = _node(_PURE + """
      const base = {"卡片統計": {"還沒看": 3, "要重做": 0}, "需留意": {"列": []}};
      const name = {"列": [{"名字": true}, {"名字": true}, {"名字": false}]};
      const out = {"時間": "2026-10-14T14:20:05"};
      console.log(JSON.stringify([
        fcStatusLine({...base, "需留意": name, "AI執行中": true}),
        fcStatusLine({...base, "AI執行中": true, "卡片統計": {"要重做": 2}}),
        fcStatusLine({...base, "卡片統計": {"要重做": 2, "還沒看": 1}, "上次執行": {"狀態": "停止"}}),
        fcStatusLine({...base, "卡片統計": {"要重做": 2, "還沒看": 1}, "上次執行": {"狀態": "做完"}}),
        fcStatusLine(base),
        fcStatusLine({...base, "卡片統計": {}, "輸出成品": out}),
        fcStatusLine({...base, "卡片統計": {}, "輸出成品": out, "輸出成品過期": {"原因": "x"}}),
        fcStatusLine({...base, "卡片統計": {}}),
      ].map((x) => [x.text, x.go || null])));""")
    assert got == [["⚠️ 這 2 處會照原聲念出本名", "需留意"], ["正在重做…", None],
                   ["上次重做沒做完：按『重做這幾張』繼續", None], ["改過的還沒重做：按下面『重做這幾張』", None],
                   ["還有 3 張沒看", None], ["已輸出（10/14 14:20）", None], ["全部看完了，可以輸出", None],
                   ["全部看完了，可以輸出", None]], got


def test_card_label_and_reason():
    if not NODE:
        return
    got = _node(_PURE + """
      console.log(JSON.stringify([
        fcCardLabel({"狀態": "通過", "沿用自": 2}), fcCardLabel({"狀態": "還沒看", "要人聽": true, "第幾版": 3}),
        fcCardLabel({"狀態": "退回", "舊紀錄": true}), fcCardLabel({"狀態": "已改，等重做"}), fcCardLabel({"狀態": "還沒生成"}),
        fcReasonText({"選項": ["念錯字", "其他"], "補充": "第二句"}), fcReasonText({"選項": [], "補充": "舊的一句"}), fcReasonText(null)]));""")
    assert got[0] == {"text": "通過 ✓", "cls": "ok", "small": ["沿用第 2 版"]}
    assert got[1] == {"text": "還沒看", "cls": "todo", "small": ["請仔細聽", "第 3 版"]}
    assert got[2]["cls"] == "back" and got[2]["small"] == ["舊紀錄"]
    assert got[3] == {"text": "已改，等重做", "cls": "edited", "small": []} and got[4]["cls"] == "made"
    assert got[5:] == ["念錯字、其他：第二句", "舊的一句", ""]


def test_review_fixes_pure():
    """10-15 第2段審查修正：退回沒寫原因、連帶那一行、舊檔模式一律有「重做這幾張」、只改備註不標已改。"""
    if not NODE:
        return
    got = _node(_PURE + """
      console.log(JSON.stringify({
        why: [fcWhyLine({"退回原因": {"選項": [], "補充": ""}}), fcWhyLine({"退回原因": {"選項": [], "補充": "退回（沒寫原因）"}}),
              fcWhyLine({"退回原因": null}), fcWhyLine({"退回原因": {"選項": ["念錯字"], "補充": ""}})],
        shared: [fcSharedLine({"連帶自": [{"id": "K1", "編號": 12}]}), fcSharedLine({"連帶自": [{"id": "x", "編號": null}]}), fcSharedLine({})],
        lab: [fcCardLabel({"狀態": "退回", "連帶自": [{"編號": 3}]}).small, fcCardLabel({"狀態": "已改，等重做", "第3步改過": true}).small],
        redo: [fcRedoCount({"卡片統計": {"要重做": 2}}), fcRedoCount({"卡片來源": "舊檔", "卡片統計": {}}),
               fcRedoCount({"卡片來源": "舊檔", "卡片統計": {}, "AI執行中": true}), fcRedoCount({"卡片來源": "新紀錄", "卡片統計": {}})],
        st: [fcStatusLine({"卡片統計": {"還沒看": 0}, "舊檔改過": true}).text, fcStatusLine({"卡片統計": {"還沒看": 0}}).text],
        note: [fcNoteOnly({id: "T1", "問老師": true}), fcNoteOnly({id: "T1", "問老師備註": "x"}), fcNoteOnly({id: "M1", "備註": "x"}),
               fcNoteOnly({id: "T1", "校對稿": "x"}), fcNoteOnly({id: "T1", "問老師": true, "說話者": "老師"}), fcNoteOnly({})]}));""")
    assert got["why"] == ["退回（沒寫原因）", "退回（沒寫原因）", "退回（沒寫原因）", "原因：念錯字"]
    assert got["shared"] == ["跟 #12 共用，那張還是退回", "跟 另一張卡 共用，那張還是退回", ""]
    assert got["lab"] == [["連帶"], ["在第 3 步改過"]]
    assert got["redo"] == [{"show": True, "n": 2}, {"show": True, "n": 0}, {"show": False, "n": 0}, {"show": False, "n": 0}]
    assert got["st"] == ["改過的還沒重做：按下面『重做這幾張』", "全部看完了，可以輸出"]
    assert got["note"] == [True, True, True, False, False, False]


def test_tabs_step_and_next():
    if not NODE:
        return
    got = _node(_PURE + """
      const C = [{"id": "a", "類型": "學員段落", "狀態": "通過", "成品": [10, 12]}, {"id": "b", "類型": "名字", "狀態": "還沒看", "成品": [30, 31]},
                 {"id": "c", "類型": "待釐清", "狀態": "退回", "成品": null}, {"id": "d", "類型": "學員段落", "狀態": "還沒看", "成品": [50, 52]},
                 {"id": "e", "類型": "刪除段落", "狀態": "已改，等重做", "成品": [60, 60]}];
      const T = fcTabList(C, 2, {"列": [1]});
      console.log(JSON.stringify({
        tabs: T.map((t) => [t.id, t["名稱"], (t.cards || []).map((c) => c.id), t["筆數"] || null]),
        pick: [fcPickTab(T, "名字"), fcPickTab(T, "重疊"), fcPickTab(T, null)],
        step: [fcStepPick(C, "b", 0, 1), fcStepPick(C, "a", 0, -1), fcStepPick(C, "x", 40, 1), fcStepPick(C, "x", 40, -1), fcStepPick([], "x", 0, 1)],
        next: [fcNextUnseen(C, "b"), fcNextUnseen(C, "d"), fcNextUnseen([{"id": "z", "狀態": "通過"}], "z")] }));""")
    assert got["tabs"] == [["全部", "全部", ["a", "b", "c", "d", "e"], None], ["還沒看完", "還沒看完", ["b", "c", "d", "e"], None],
                           ["學員段落", "學員段落", ["a", "d"], None], ["名字", "名字", ["b"], None], ["刪除段落", "剪掉", ["e"], None],
                           ["其他", "其他", ["c"], None], ["不修改", "第 3 步選定不修改", [], 2], ["需留意", "⚠️ 需留意", [], 1]]
    assert got["pick"] == ["名字", "全部", "全部"]
    assert got["step"] == ["c", "a", "d", "b", None]
    assert got["next"] == ["d", "b", None]   # 通過後往後找還沒看的；後面沒有才繞回前面


def test_time_mapping_matches_backend_and_ab_plan():
    if not NODE:
        return
    from bookclub import finalcheck as fc
    from bookclub import render

    plist = render.pieces(10.0, 40.0, [(13.0, 15.0)], [{"at": 17.0, "dur": 0.5}])
    pts = [10.0, 12.5, 15.0, 16.9, 17.0, 20.0, 39.0]
    got = _node(_PURE + f"""
      const P = {json.dumps(plist)};
      console.log(JSON.stringify({{out: {json.dumps(pts)}.map((t) => fcOutTime(t, P)), cut: fcOutTime(14.0, P), none: fcOutTime(5, null),
        back: {json.dumps(pts)}.map((t) => fcOutTime(t, P)).map((o) => fcSrcTime(o, P)),
        now: fcCardAbPlan({{"原片": [100, 104], "成品": [80, 85]}}, "現在"), orig: fcCardAbPlan({{"原片": [100, 104], "成品": [80, 85]}}, "原本"),
        cut1: fcCardAbPlan({{"原片": [200, 210], "成品": null}}, "現在"), cut2: fcCardAbPlan({{"原片": [200, 210], "成品": null}}, "原本"),
        noSrc: fcCardAbPlan({{"原片": null, "成品": [1, 2]}}, "原本"),
        now0: fcCardAbPlan({{"原片": [100, 104], "成品": [80, 85]}}, "現在", 0), orig0: fcCardAbPlan({{"原片": [100, 104], "成品": [80, 85]}}, "原本", 0),
        pt0: fcCardAbPlan({{"原片": [100, 104], "成品": [80, 80]}}, "現在", 0)}}));""")
    want = [round(fc.to_output_time(t, plist), 3) for t in pts]
    assert [round(x, 3) for x in got["out"]] == want, (got["out"], want)
    assert got["cut"] is None and got["none"] == 5
    assert [round(x, 3) for x in got["back"]] == [round(fc.from_output_time(o, plist), 3) for o in want]
    assert got["now"] == {"video": {"from": 78, "to": 87, "muted": False}, "audio": None}
    assert got["orig"]["video"] == {"from": 78, "to": 86, "muted": True} and got["orig"]["audio"] == "/api/audio?start=98.00&end=106.00"
    assert got["cut1"] is None and got["cut2"]["video"] is None and got["noSrc"] is None
    # 10-15 宇軒驗證回饋：「前後多聽 2 秒」開關關著（pad＝0）只播修改的那一段；只有一個時間點的照樣前後 2 秒
    assert got["now0"] == {"video": {"from": 80, "to": 85, "muted": False}, "audio": None}
    assert got["orig0"]["video"] == {"from": 80, "to": 84, "muted": True} and got["orig0"]["audio"] == "/api/audio?start=100.00&end=104.00"
    assert got["pt0"]["video"] == {"from": 78, "to": 82, "muted": False}


def test_fileio_which_line_and_redo_return():
    if not NODE:
        return
    got = _node(_ESC + _fn(FIOJS, "foWhichLine") + _fn(APPJS, "execRedoReturn") + """
      const stale = {"說明": "上次輸出之後，第 4 步又組裝了新的成品"};
      const since = 1000.5;   // 10-15 第2段審查修正：伺服器回傳的 started_at（秒），不是瀏覽器時間
      console.log(JSON.stringify({
        w: [foWhichLine({"上次輸出過期": stale, "版本": {"輸出版本": 1, "最新版本": 2}}, null),
            foWhichLine({"上次輸出過期": stale, "版本": null}, null),
            foWhichLine({"上次輸出過期": null, "可以複製到Windows": true, "成品是": "最終成品"}, null),
            foWhichLine({"上次輸出過期": null, "可以複製到Windows": true, "成品是": "最終成品"}, {"狀態": "完成"}),
            foWhichLine({"上次輸出過期": null, "可以複製到Windows": false, "成品是": "最終成品"}, null)],
        r: [execRedoReturn({running: true}, since), execRedoReturn({running: false, started_at: 1001, finished_at: 1500}, since),
            execRedoReturn({running: false, started_at: 1001, finished_at: 1500, stopped: true}, since),
            execRedoReturn({running: false, started_at: 1001, "進度": {"錯誤": "x"}}, since),
            execRedoReturn({running: false, started_at: 1000, finished_at: 1500}, since),
            execRedoReturn({running: false, started_at: 1001, finished_at: 1500}, null)] }));""")
    assert "這是第 1 版的，最新是第 2 版" in got["w"][0]   # 10-15 第5步2.0 第3段：統一成 §6.7 的句子
    assert "第 4 步又組裝了新的成品" in got["w"][1]
    assert "要拿檔案，按『複製到 Windows』" in got["w"][2] and got["w"][3] == "" and got["w"][4] == ""
    assert got["r"] == [None, "回第5步", "留下", "留下", None, None]   # 第 5 個：更早的那一次（started_at 比按的那一次早）
    # 10-15 第3段審查修正（建議）：Windows 複製拿的不是最新一版時問的那一句
    ask = _node(_fn(FIOJS, "foStaleAsk") + """console.log(JSON.stringify([
        foStaleAsk("這是第 1 版的，最新是第 2 版", {"輸出版本": 1, "最新版本": 2}), foStaleAsk("上次輸出之後又組裝了", null)]));""")
    assert "要複製第 1 版嗎" in ask[0] and "這是第 1 版的，最新是第 2 版" in ask[0] and "現在這一支還沒按" in ask[1]
    poll = _fn(APPJS, "startExecPoll")
    assert "execRedoGoBack(d)" in poll and "execRedoGoBack(d)" in _fn(APPJS, "renderExecuteBody")


def test_hint_texts_follow_design():
    """§6.8：留下來的句子在；砍掉的、§8 拿掉的按鈕都不在。"""
    for want in ("還沒有成品，請先到", "看過 <b", "為什麼退回？", "補充（可不填）", "這一張重做只會重新放一次，聲音不會變；要改內容請按『改』",
                 "確定退回", "取消退回", "上一張", "下一張", "＋在這裡新增修改", "重做這幾張（", "我不確定是哪一種，先記下來", "只看要仔細聽的",
                 "這一頁看完了", "這一版沒有任何修改", "第 ${idx + 1}／${list.length} 張", "請仔細聽", "原本 ▶", "現在 ▶"):
        assert want in FCJS, want
    for gone in ("沒列在時間軸上的地方＝原片沒動", "送回 AI 重做", "逐筆看", "整片看", "調整時間範圍", "AI 重做時照這個改", "AI 照這個改",
                 "已換成品", "要重新輸出", "處理紀錄產生於", "超過 2 倍速播的不算看過", "念的稿子：", "檢查哪一支", "跳到 38:26",
                 "退回重做</button>", "上一筆", "下一筆", "處理前", "處理後", "試聽（前後各多 2 秒）", "目前這一筆在", "fcPreviewHtml"):
        assert gone not in FCJS, gone


# ---------- 整頁（node 假瀏覽器＋真的後端資料） ----------

def _api_final() -> tuple[dict, Path]:
    import test_needs as tn

    from bookclub import finalcards, finalcheck, needs

    w = tn._fresh()
    log = tn._log()
    tn._setup(w, log, tn._check(log, {"學員重念@40.00": ["通過"], "學員重念@55.00": ["退回重做", "第二格念錯"]}))
    tn._quiet(needs.upgrade, w)
    tn._prod(w, "成品_0-3_sw.mp4", tn.T1)
    with redirect_stdout(io.StringIO()):
        page = needs.compute_page(w)
        d = finalcheck.page_data(w)
        d.update(finalcards.page_part(w, page))
    return json.loads(json.dumps(d, ensure_ascii=False, default=str)), w


def _run_page(scenarios: list[dict], data: dict | None = None) -> dict:
    if data is None:
        data, _w = _api_final()
    files = [str(WEB / f) for f in ("cards.js", "review.js", "finalcheck.js")]
    pre = (f"H.route = 'step5'; const DATA = {json.dumps(data, ensure_ascii=False)};\n"
           "H.apiGetData['/api/final/outputs'] = {}; H.apiGetData['/api/final'] = DATA;\n"
           "H.api['/api/final/card'] = {'卡片': null, '統計': DATA['卡片統計']};\n"
           "H.api['/api/final/edited'] = {'記了': true};\n"
           "H.api['*'] = {'段落': {'校對稿': 'x', '問老師': false}, '決定': {'做法': '整句換掉', 'tags': [], 'note': ''}};\n"
           "function card(key) { return DATA['卡片'].find((c) => c['第3步鍵'] === key); }\n"
           "async function open(key) { await renderFinal(); document.getElementById('fc-video').currentTime = 60;"
           " H.listeners.length = 0; H.log.length = 0; fcSelect(card(key).id, false); }\n"
           "function click(id) { return H.fireAll((l) => l.el.id === id && l.type === 'click'); }\n")
    d = Path(tempfile.mkdtemp())
    (d / "spec.json").write_text(json.dumps({"前置": pre, "情境": scenarios}, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([NODE, str(HARNESS), ",".join(files), str(d / "spec.json")], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def test_page_renders_and_buttons_post_right_things():
    if not NODE:
        print("（沒有 node，略過）")
        return
    got = _run_page([
        {"名稱": "畫面", "程式": """
          await renderFinal();
          return {status: document.getElementById("fc-status").innerHTML, right: document.getElementById("fc-right").innerHTML,
                  lower: document.getElementById("fc-lower").innerHTML, tl: document.getElementById("fc-tl").innerHTML,
                  bar: document.getElementById("fc-actbar").innerHTML, left: contentEl.innerHTML,
                  n: DATA["卡片"].length, src: DATA["卡片來源"]};"""},
        {"名稱": "通過", "程式": """
          await open("名字:1"); await click("fc-pass"); return H.log;"""},
        {"名稱": "退回選原因", "程式": """
          await open("名字:1"); await click("fc-redo");
          const opts = document.querySelectorAll(".fc-why-opt"); opts[0].checked = true; opts[2].checked = true;
          document.getElementById("fc-why-extra").value = "第三句";
          const right = document.getElementById("fc-right").innerHTML;
          await click("fc-redo-go"); return {log: H.log, right};"""},
        {"名稱": "改字之後記已改", "程式": """
          await open("學員段落:T005"); await click("fc-edit");
          const right = document.getElementById("fc-right").innerHTML;
          const ta = document.getElementById("rv-text"); ta.value = "改過的字";
          await H.fireAll((l) => l.el.id === "rv-text" && l.type === "blur");
          return {log: H.log, right, state: fcCards().find((c) => c["第3步鍵"] === "學員段落:T005")["狀態"], status: document.getElementById("fc-status").innerHTML};"""},
        {"名稱": "裡面有老師的聲音", "程式": """
          await open("學員段落:T005"); await click("fc-edit");
          const right = document.getElementById("fc-right").innerHTML;
          await click("rv-isteacher"); return {log: H.log, right};"""},
        {"名稱": "新增不確定", "程式": """
          await renderFinal(); fc.video.currentTime = 100; H.log.length = 0;
          await click("fc-add"); fc.ed.kind = "不確定"; fc.ed.b = fc.ed.a + 1; fcRenderIO();
          const io = document.getElementById("fc-io").innerHTML;
          await H.fireAll((l) => "teSave" && l.el.attrs && "data-te-save" in l.el.attrs); return {log: H.log, io};"""},
        {"名稱": "新增消音", "程式": """
          await renderFinal(); fc.video.currentTime = 100; H.log.length = 0; H.api['/api/review/manual'] = {'類型': '局部消音', 'id': 'M009', '對齊結果': {}};
          await click("fc-add"); fc.ed.kind = "局部消音"; fc.ed.b = fc.ed.a + 1; fcRenderIO();
          await H.fireAll((l) => l.el.attrs && "data-te-save" in l.el.attrs); return H.log;"""},
        {"名稱": "重做這幾張", "程式": """
          H.api['/api/execute/start'] = {started: true, started_at: 1234.5};
          await renderFinal(); H.log.length = 0; await click("fc-redoall"); return {log: H.log, hash: location.hash, back: fcRedoBackGet()};"""},
        {"名稱": "只改問老師不標已改", "程式": """
          await open("學員段落:T005"); await click("fc-edit");
          await FC_CARD_CTX.post("/api/turns/save", {id: "T005", "問老師": true}); return H.log;"""},
        {"名稱": "改完用回傳的整頁資料", "程式": """
          const page = JSON.parse(JSON.stringify(DATA)); page["卡片"].find((c) => c["第3步鍵"] === "學員段落:T005")["狀態"] = "已改，等重做";
          H.api['/api/final/edited'] = {'記了': true, '頁面': page};
          await open("學員段落:T005"); await FC_CARD_CTX.post("/api/turns/save", {id: "T005", "說話者": "老師"}); await FC_CARD_CTX.reload();
          return {log: H.log, state: fcCards().find((c) => c["第3步鍵"] === "學員段落:T005")["狀態"]};"""},
        {"名稱": "舊檔模式", "程式": """
          const old = JSON.parse(JSON.stringify(DATA)); old["卡片來源"] = "舊檔"; old["卡片統計"] = {"還沒看": 0, "要重做": 0};
          old["卡片"].forEach((c) => { c["狀態"] = "通過"; });
          H.apiGetData['/api/final'] = old; H.api['/api/final/edited'] = {'記了': false};
          await renderFinal();
          const before = {status: document.getElementById("fc-status").innerHTML, lower: document.getElementById("fc-actbar").innerHTML};
          await open("學員段落:T005"); await FC_CARD_CTX.post("/api/turns/save", {id: "T005", "校對稿": "x"});
          const after = document.getElementById("fc-status").innerHTML;
          old["開發者"] = true; await renderFinal();
          return {before, after, dev: document.getElementById("fc-status").innerHTML};"""},
        {"名稱": "連帶與沒寫原因", "程式": """
          const k = card("學員段落:T005"); k["狀態"] = "退回"; k["退回原因"] = {"選項": [], "補充": ""}; k["連帶自"] = [{"id": card("名字:1").id, "編號": card("名字:1")["編號"]}];
          await open("學員段落:T005"); return {right: document.getElementById("fc-right").innerHTML, n: card("名字:1")["編號"]};"""},
    ])
    v = got["畫面"]
    assert "錯誤" not in v, v
    assert v["src"] == "新紀錄" and v["n"] >= 6
    assert "改過的還沒重做" in v["status"]                                   # T003 退回了 → 要重做
    assert "原本 ▶" in v["right"] and "現在 ▶" in v["right"] and 'id="fc-pass"' in v["right"] and "第 1／" in v["right"]
    assert re.search(r'class="idtag"[^>]*>#\d+<', v["right"])                 # #103：每張卡顯示 #編號
    # 10-15 宇軒驗證回饋：兩顆按鈕搬到時間軸下面、「看過 P%」之下（以前在清單底下 fc-lower）
    assert "重做這幾張（1 張）" in v["bar"] and "＋在這裡新增修改" in v["bar"] and 'data-tab="還沒看完"' in v["lower"]
    assert "重做這幾張" not in v["lower"] and "＋在這裡新增修改" not in v["lower"]
    assert v["left"].index("fc-seenpct") < v["left"].index('id="fc-actbar"') < v["left"].index('id="fc-io"')
    assert 'id="fc-pad"' in v["right"] and "前後多聽 2 秒" in v["right"] and 'id="fc-pad" checked' not in v["right"]   # 開關預設關
    assert "fc-preview" not in v["right"]   # 舊預覽的霧化樣式不能套到卡片的字
    assert "fc-back" in v["tl"] and "fc-todo" in v["tl"]
    posts = [x for x in got["通過"] if x[0] == "POST"]
    assert posts[0][1] == "/api/final/card" and posts[0][2]["結果"] == "通過" and posts[0][2]["id"].startswith("K")
    r = got["退回選原因"]
    assert "為什麼退回？" in r["right"] and "這一張重做只會重新放一次" not in r["right"]   # 名字卡有生成檔：會換一種念法
    p = [x for x in r["log"] if x[0] == "POST"][0]
    assert p[1] == "/api/final/card" and p[2]["結果"] == "退回" and p[2]["選項"] == ["念錯字", "長度或停頓怪"] and p[2]["補充"] == "第三句"
    e = got["改字之後記已改"]
    assert 'id="rv-text"' in e["right"] and "裡面有老師的聲音" in e["right"] and "這段其實是老師" not in e["right"]
    posts = [x for x in e["log"] if x[0] == "POST"]
    assert [x[1] for x in posts] == ["/api/turns/save", "/api/final/edited"], posts
    assert posts[0][2]["校對稿"] == "改過的字" and posts[1][2]["鍵"] == "學員段落:T005"
    assert e["state"] == "已改，等重做" and "改過的還沒重做" in e["status"]
    t = got["裡面有老師的聲音"]
    posts = [x for x in t["log"] if x[0] == "POST"]
    assert posts[0][1] == "/api/turns/save" and posts[0][2]["說話者"] == "老師" and posts[1][1] == "/api/final/edited"
    u = got["新增不確定"]
    assert "我不確定是哪一種，先記下來" in u["io"]
    posts = [x for x in u["log"] if x[0] == "POST"]
    assert posts[0][1] == "/api/final/flag" and posts[0][2]["原因"] == "不確定是哪一種" and abs(posts[0][2]["成品秒"] - 100) < 0.01, posts
    assert not any(x[1] == "/api/final/edited" for x in posts)
    m = [x for x in got["新增消音"] if x[0] == "POST"]
    assert m[0][1] == "/api/review/manual" and m[0][2]["類型"] == "局部消音" and m[0][2]["start"] == 100
    assert m[1][1] == "/api/final/edited" and m[1][2] == {"鍵": "局部消音:M009", "新增": True}   # 新增的卡記「第5步新增」、不是記在目前這張
    rd = got["重做這幾張"]
    assert [x[1] for x in rd["log"] if x[0] == "POST"] == ["/api/execute/start"] and rd["hash"] == "#step4" and rd["back"] == 1234.5
    # 10-15 第2段審查修正
    q = [x[1] for x in got["只改問老師不標已改"] if x[0] == "POST"]
    assert q == ["/api/turns/save"], q
    e2 = got["改完用回傳的整頁資料"]
    assert [x[1] for x in e2["log"] if x[0] in ("POST", "GET")] == ["/api/turns/save", "/api/final/edited"], e2["log"]
    assert e2["state"] == "已改，等重做"
    o = got["舊檔模式"]
    assert "錯誤" not in o, o
    assert "重做這幾張" in o["before"]["lower"] and "重做這幾張（" not in o["before"]["lower"]   # 必修 3：舊檔模式一律有按鈕
    assert "全部看完了" in o["before"]["status"] and "改過的還沒重做" in o["after"]           # 按「改」之後這次畫面記著
    assert "先照舊的紀錄顯示" not in o["before"]["status"] and "先照舊的紀錄顯示" in o["dev"]   # 只在開發者設定顯示
    lk = got["連帶與沒寫原因"]
    assert "right" in lk, lk
    assert "退回（沒寫原因）" in lk["right"] and f"跟 #{lk['n']} 共用，那張還是退回" in lk["right"] and "連帶" in lk["right"]



def test_card_two_lines():
    # 10-15 宇軒驗證回饋：AI 重新生成的卡列「原本：」「改成：」兩行；沒改字兩行一樣照列；沒有要念的字照舊一行摘要
    if not NODE:
        return
    got = _node(_PURE + """
      const fcWho = (s) => s; function fcPreview() { return "摘要"; }
      const stu = {"類型": "學員段落", "原文": "我跟小美說", "校對稿": "我跟小美說"};
      const nm = {"類型": "名字", "整句": {"原文": "剛剛小美分享得很好"}};
      console.log(JSON.stringify({
        two: fcCardTextHtml({"要念的字": ["我跟Amy說"]}, stu), same: fcCardTextHtml({"要念的字": ["我跟小美說"]}, stu),
        name: fcCardTextHtml({"要念的字": ["剛剛Amy分享得很好"]}, nm), none: fcCardTextHtml({"要念的字": []}, stu),
        noItem: fcCardTextHtml({"要念的字": ["老師的字"]}, null),
        ov: fcOrigText({"類型": "重疊", "老師整句": null, "老師文字": "嗯", "學員文字": "我覺得"}) }));""")
    assert "原本：</span>我跟小美說" in got["two"] and "改成：</span>我跟Amy說" in got["two"]
    assert got["same"].count("我跟小美說") == 2
    assert "原本：</span>剛剛小美分享得很好" in got["name"] and "改成：</span>剛剛Amy分享得很好" in got["name"]
    assert got["none"] == '<p class="fc-cardtext">摘要</p>'
    assert "找不到原句" in got["noItem"] and "改成：</span>老師的字" in got["noItem"]
    assert got["ov"] == "嗯／我覺得"


# ---------- 10-15 第5步2.0 第3段：版本 ----------

def test_versions_pure():
    if not NODE:
        return
    got = _node(_PURE + """
      const L = [{"版本號": 3, "時間": "2026-10-15T14:20:00", "標記": "0-97", "最新": true}, {"版本號": 2, "時間": "2026-10-15T10:05:00", "標記": "0-3", "最新": false}];
      console.log(JSON.stringify({
        text: [fcVersionText(L[0], false), fcVersionText(L[1], false), fcVersionText(L[1], true)],
        one: fcVersionMenuHtml({"版本清單": [L[0]]}), none: fcVersionMenuHtml({"版本清單": []}),
        menu: fcVersionMenuHtml({"版本清單": L, "目前版本": 2}),
        dev: fcVersionMenuHtml({"版本清單": L, "目前版本": 2, "開發者": true, "看的是舊版": true}),
        devNew: fcVersionMenuHtml({"版本清單": L, "目前版本": 3, "開發者": true, "看的是舊版": false}),
        lab: fcCardLabel({"狀態": "還沒生成", "這一版沒處理到": true}),
        prev: fcCardAbPlan({"原片": [100, 104], "成品": [80, 85], "上一版": {"版本號": 2, "成品": [70, 75]}}, "上一版"),
        noPrev: fcCardAbPlan({"原片": [100, 104], "成品": [80, 85], "上一版": null}, "上一版"),
        crossNow: fcCardAbPlan({"原片": [100, 104], "成品": [80, 85], "跨版本": true, "版本號": 1}, "現在"),
        crossOrig: fcCardAbPlan({"原片": [100, 104], "成品": [80, 85], "跨版本": true, "版本號": 1}, "原本"),
        todo: fcTabList([{"id": "a", "類型": "名字", "狀態": "還沒生成", "這一版沒處理到": true}, {"id": "b", "類型": "名字", "狀態": "還沒看"}], 0, null)[1].cards.map((c) => c.id),
        // 10-15 第3段審查修正（必修 1）：舊版、跨版本卡只能通過
        ret: [fcCanReturn({"狀態": "還沒看"}, false), fcCanReturn({"狀態": "還沒看"}, true), fcCanReturn({"狀態": "還沒看", "跨版本": true}, false),
              fcCanReturn({"狀態": "還沒生成"}, false)],
        judge: [fcCanJudge({"狀態": "還沒看"}), fcCanJudge({"狀態": "還沒看", "跨版本": true}), fcCanJudge({"狀態": "通過", "這一版沒處理到": true}), fcCanJudge(null)] }));""")
    assert got["text"] == ["第 3 版　10/15 14:20（最新）", "第 2 版　10/15 10:05", "第 2 版　10/15 10:05　0-3"]
    # 10-15 宇軒驗證回饋：只有一版也出現選單（只有一項）；一版都沒有才不出現（以前只有一版是空字串）
    assert "看哪一版" in got["one"] and got["one"].count("<option") == 1 and got["none"] == ""
    assert "看哪一版" in got["menu"] and 'value="2" selected' in got["menu"] and "以這一版為基礎再組裝" not in got["menu"]
    assert "0-97" not in got["menu"] and "0-97" in got["dev"]   # 範圍標記只有開發者看得到
    assert "以這一版為基礎再組裝" in got["dev"] and "以這一版為基礎再組裝" not in got["devNew"]
    assert got["lab"] == {"text": "這一版沒處理到", "cls": "made", "small": []}
    assert got["prev"] == {"video": None, "audio": "/api/final/vclip?版本=2&start=68.00&end=77.00"} and got["noPrev"] is None
    assert got["crossNow"]["audio"].startswith("/api/final/vclip?版本=1&") and got["crossNow"]["video"] is None
    assert got["crossOrig"]["video"] is None and got["crossOrig"]["audio"].startswith("/api/audio?")
    assert got["todo"] == ["b"]                                # 沒處理到的不算「還沒看完」
    assert got["ret"] == [True, False, False, False] and got["judge"] == [True, True, False, False]
    # 第 4 步：範圍「從／到」、「只重新組裝」只有開發者設定才畫（31 號設計 §5.3）
    body = _fn(APPJS, "renderExecuteBody")
    assert "const ra = dev ?" in body and '${dev ? `<div class="exec-opts">' in body and '${dev ? `<button id="btnReassemble"' in body


def _version_data(old: bool = False) -> dict:
    import test_versions as tv

    from bookclub import finalcards, finalcheck, needs

    w = tv._three_versions()
    with redirect_stdout(io.StringIO()):
        page = needs.compute_page(w)
        if old:
            d = finalcards.version_page(w, 1, page)
        else:
            d = finalcheck.page_data(w)
            d.update(finalcards.page_part(w, page))
    return json.loads(json.dumps(d, ensure_ascii=False, default=str))


def test_version_page_renders():
    if not NODE:
        return
    latest = _version_data()
    got = _run_page([
        {"名稱": "最新", "程式": """
          await renderFinal();
          const t5 = DATA["跨版本卡片"].find((c) => c["第3步鍵"] === "學員段落:T005");
          H.listeners.length = 0; H.log.length = 0; fcSelect(t5.id, false);
          const right = document.getElementById("fc-right").innerHTML;
          await click("fc-redo");   // 10-15 第3段審查修正：跨版本卡退回打不開、不送出
          const opened = !document.getElementById("fc-redobox").hidden;
          await click("fc-pass");
          return {top: contentEl.innerHTML, lower: document.getElementById("fc-lower").innerHTML, right, opened, log: H.log};"""},
        {"名稱": "換版", "程式": """
          await renderFinal(); H.log.length = 0;
          const sel = document.getElementById("fc-ver"); sel.value = "1";
          await H.fireAll((l) => l.el.id === "fc-ver" && l.type === "change");
          return {ver: fc.ver, log: H.log};"""},
    ], data=latest)
    v = got["最新"]
    assert "錯誤" not in v, v
    assert "看哪一版" in v["top"] and "第 2 版" in v["top"] and "（最新）" in v["top"]
    assert "上一版還沒看的（第 1 版）" in v["lower"] and "這一版沒處理到" in v["lower"] and "fc-faded" in v["lower"]
    assert "現在 ▶" in v["right"]
    assert re.search(r'id="fc-redo"[^>]*disabled title="要退回請回到最新一版"', v["right"]) and not v["opened"]
    posts = [x for x in v["log"] if x[0] == "POST"]
    assert posts[0][1] == "/api/final/card" and posts[0][2]["版本"] == 1   # 上一版那一區按的：記在第 1 版
    assert got["換版"]["ver"] == 1 and any(x[0] == "GET" and x[1] == "/api/final?版本=1" for x in got["換版"]["log"])
    old = _version_data(old=True)
    got = _run_page([{"名稱": "舊版", "程式": """
          await renderFinal();
          const t5 = DATA["卡片"].find((c) => c["第3步鍵"] === "學員段落:T005");
          H.listeners.length = 0; H.log.length = 0; fcSelect(t5.id, false);
          const right = document.getElementById("fc-right").innerHTML;
          await click("fc-pass");
          const log = H.log.slice(); H.log.length = 0;
          await fcExport();
          return {right, lower: document.getElementById("fc-lower").innerHTML + document.getElementById("fc-actbar").innerHTML, video: document.getElementById("fc-video").getAttribute("src"),
                  log, exp: H.log};"""}], data=old)
    o = got["舊版"]
    assert "錯誤" not in o, o
    assert 'id="fc-edit"' not in o["right"] and "＋在這裡新增修改" not in o["lower"] and "重做這幾張" not in o["lower"]
    assert re.search(r'id="fc-redo"[^>]*disabled', o["right"])   # 10-15 第3段審查修正：舊版只能通過
    assert not any(x[0] == "POST" and x[2].get("結果") == "退回" for x in o["log"])
    assert o["video"].startswith("/api/final/video?版本=1&v=")
    assert [x for x in o["log"] if x[0] == "POST"][0][2]["版本"] == 1
    assert [x for x in o["exp"] if x[0] == "POST"][0][2] == {"版本": 1}


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
