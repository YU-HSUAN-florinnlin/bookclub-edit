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
         + _const(FCJS, "FC_REDO_LABEL") + _const(FCJS, "FC_NO_REASON_SHOW")
         + _const(FCJS, "FC_FIX_TEXT") + _const(FCJS, "FC_FIX_OPTS")
         + _fn(FCJS, "fcCanFix") + _fn(FCJS, "fcFixOff") + _fn(FCJS, "fcFixOptions") + _fn(FCJS, "fcFixRecords")
         + _const(FCJS, "FC_TAB_ALL") + _const(FCJS, "FC_TYPE_TABS")
         + _const(FCJS, "FC_OUT_PASSED") + _const(FCJS, "FC_OUT_NONE")
         + _fn(FCJS, "fcShortStamp") + _fn(FCJS, "fcCardLabel") + _fn(FCJS, "fcReasonText") + _fn(FCJS, "fcStatusLine")
         + _const(FCJS, "FC_NO_REASON") + _fn(FCJS, "fcWhyLine") + _fn(FCJS, "fcSharedLine") + _fn(FCJS, "fcRedoCount")
         + _const(FCJS, "FC_NOTE_FIELDS") + _fn(FCJS, "fcNoteOnly")
         + _fn(FCJS, "fcTabList") + _fn(FCJS, "fcPickTab") + _fn(FCJS, "fcStepPick") + _fn(FCJS, "fcFixPrefill")
         + _fn(FCJS, "fcSrcTime") + _fn(FCJS, "fcOutTime") + _fn(FCJS, "fcGotoOut") + _fn(FCJS, "fcPadOf") + _fn(FCJS, "fcVclip") + _fn(FCJS, "fcCardAbPlan")
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
    # 10-15 要修改合併（1015-4）：退回、已改，等重做對老師一律寫「要重做」（已改的小字「已改」）
    assert got[2] == {"text": "要重做", "cls": "back", "small": ["舊紀錄"]}
    assert got[3] == {"text": "要重做", "cls": "edited", "small": ["已改"]} and got[4]["cls"] == "made"
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
    assert got["why"] == ["要重做（沒寫原因）", "要重做（沒寫原因）", "要重做（沒寫原因）", "原因：念錯字"]
    assert got["shared"] == ["跟 #12 共用，那張還是退回", "跟 另一張卡 共用，那張還是退回", ""]
    assert got["lab"] == [["連帶"], ["在第 3 步改過"]]
    assert got["redo"] == [{"show": True, "n": 2}, {"show": True, "n": 0}, {"show": False, "n": 0}, {"show": False, "n": 0}]
    assert got["st"] == ["改過的還沒重做：按下面『重做這幾張』", "全部看完了，可以輸出"]
    assert got["note"] == [True, True, True, False, False, False]


def test_fix_pure():
    """10-15 要修改合併（1015-4）：「要修改」可不可以按、停用時滑過的字、「哪裡出問題？」列哪幾項、確定時記不記原因。"""
    if not NODE:
        return
    got = _node(_PURE + """
      const all = ["念錯字", "聲音不像、不自然", "長度或停頓怪", "裡面有老師的聲音", "其他"];
      console.log(JSON.stringify({
        can: [fcCanFix({"狀態": "還沒看"}, false), fcCanFix({"狀態": "還沒看"}, true), fcCanFix({"狀態": "還沒看", "跨版本": true}, false),
              fcCanFix({"狀態": "還沒看", "這一版沒處理到": true}, false), fcCanFix({"狀態": "已改，等重做"}, false), fcCanFix(null, false)],
        off: [fcFixOff({}, true), fcFixOff({"跨版本": true}, false), fcFixOff({"這一版沒處理到": true}, false)],
        opts: [fcFixOptions({"可重新生成": true}, all), fcFixOptions({"可重新生成": false}, all), fcFixOptions({"可重新生成": true}, [])],
        rec: [fcFixRecords({"狀態": "還沒看"}), fcFixRecords({"狀態": "還沒生成"}), fcFixRecords({"狀態": "已改，等重做", "紀錄鍵": ["a"]}),
              fcFixRecords({"狀態": "已改，等重做", "紀錄鍵": []}), fcFixRecords({"狀態": "退回"})]}));""")
    assert got["can"] == [True, False, False, False, True, False]
    assert got["off"] == ["要修改請回到最新一版", "要修改請回到最新一版", "這張是上一版通過的，這一版沒改到這裡"]
    assert got["opts"][0] == ["念錯字", "聲音不像、不自然", "長度或停頓怪", "裡面有老師的聲音", "其他"] == got["opts"][2]
    assert got["opts"][1] == ["念錯字", "長度或停頓怪", "裡面有老師的聲音", "其他"]
    assert got["rec"] == [True, False, True, False, True]


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
        pre: [fcFixPrefill({"狀態": "退回", "退回原因": {"選項": ["長度或停頓怪"], "補充": "尾巴切太早"}}),
              fcFixPrefill({"狀態": "已改，等重做", "退回原因": {"選項": ["裡面有老師的聲音"], "補充": ""}}),
              fcFixPrefill({"狀態": "已改，等重做"}), fcFixPrefill({"狀態": "通過", "退回原因": {"選項": ["其他"], "補充": "x"}}),
              fcFixPrefill({"狀態": "退回", "退回原因": {"選項": ["聲音不像、不自然"], "補充": "y"}}, ["念錯字", "其他"])] }));""")
    assert got["tabs"] == [["全部", "全部", ["a", "b", "c", "d", "e"], None], ["還沒看完", "還沒看完", ["b", "c", "d", "e"], None],
                           ["學員段落", "學員段落", ["a", "d"], None], ["名字", "名字", ["b"], None], ["刪除段落", "剪掉", ["e"], None],
                           ["其他", "其他", ["c"], None], ["不修改", "第 3 步選定不修改", [], 2], ["需留意", "⚠️ 需留意", [], 1]]
    assert got["pick"] == ["名字", "全部", "全部"]
    assert got["step"] == ["c", "a", "d", "b", None]
    # 10-15 宇軒驗證回饋二：通過後不自動跳下一張（跟第 3 步一樣），fcNextUnseen 拿掉、這裡原本那條斷言一起拿掉；
    # 改測「要重做的卡再按要修改」帶入上次的原因（選項不在這張卡能選的就不帶，補充照帶）
    assert got["pre"] == [{"pick": "長度或停頓怪", "extra": "尾巴切太早"}, {"pick": "裡面有老師的聲音", "extra": ""},
                          {"pick": None, "extra": ""}, {"pick": None, "extra": ""}, {"pick": None, "extra": "y"}]


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
        go: [fcGotoOut(12.5, P), fcGotoOut(14.0, P), fcGotoOut(5, P), fcGotoOut(41, P), fcGotoOut(null, P), fcGotoOut(-1, P)],
        goTo: [fcOutTime(15.0, P), fcOutTime(10.0, P)],
        back: {json.dumps(pts)}.map((t) => fcOutTime(t, P)).map((o) => fcSrcTime(o, P)),
        now: fcCardAbPlan({{"原片": [100, 104], "成品": [80, 85]}}, "現在"), orig: fcCardAbPlan({{"原片": [100, 104], "成品": [80, 85]}}, "原本"),
        cut1: fcCardAbPlan({{"原片": [200, 210], "成品": null}}, "現在"), cut2: fcCardAbPlan({{"原片": [200, 210], "成品": null}}, "原本"),
        noSrc: fcCardAbPlan({{"原片": null, "成品": [1, 2]}}, "原本"),
        now0: fcCardAbPlan({{"原片": [100, 104], "成品": [80, 85]}}, "現在", 0), orig0: fcCardAbPlan({{"原片": [100, 104], "成品": [80, 85]}}, "原本", 0),
        pt0: fcCardAbPlan({{"原片": [100, 104], "成品": [80, 80]}}, "現在", 0)}}));""")
    want = [round(fc.to_output_time(t, plist), 3) for t in pts]
    assert [round(x, 3) for x in got["out"]] == want, (got["out"], want)
    assert got["cut"] is None and got["none"] == 5
    # 10-15 宇軒驗證回饋二：「跳到」框（原片時間）：留著的照換算；剪掉的跳到下一段開頭；片頭之前＝第一段開頭；片尾之後、空的、負的＝null
    assert got["go"][:3] == [got["out"][1], got["goTo"][0], got["goTo"][1]] and got["go"][3:] == [None, None, None], got["go"]
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
    # 10-15 要修改合併（1015-4）：「為什麼退回？」→「哪裡出問題？」；「確定退回」→「確定」；「取消退回」→「取消要重做」
    for want in ("還沒有成品，請先到", "看過 <b", "哪裡出問題？", "補充（可不填）", "這一張重做只會重新放一次，聲音不會變；要改內容請選下面其他項",
                 "會換一種念法重新生成", "要修改<kbd>E</kbd>", "要修改請回到最新一版", "取消要重做", "要重做（沒寫原因）", "上一張", "下一張", "＋在這裡新增修改", "重做這幾張（", "我不確定是哪一種，先記下來", "只看要仔細聽的",
                 "這一頁看完了", "這一版沒有任何修改", "第 ${idx + 1}／${list.length} 張", "請仔細聽", "原本 ▶", "現在 ▶"):
        assert want in FCJS, want
    for gone in ("沒列在時間軸上的地方＝原片沒動", "送回 AI 重做", "逐筆看", "整片看", "調整時間範圍", "AI 重做時照這個改", "AI 照這個改",
                 "已換成品", "要重新輸出", "處理紀錄產生於", "超過 2 倍速播的不算看過", "念的稿子：", "檢查哪一支", "跳到 38:26",
                 "退回重做</button>", "上一筆", "下一筆", "為什麼退回？", "確定退回", "取消退回", ">退回</button>", ">改<kbd>",
                 'id="fc-redo"', 'id="fc-edit"', "要退回請回到最新一版", "處理前", "處理後", "試聽（前後各多 2 秒）", "目前這一筆在", "fcPreviewHtml"):
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
           "function click(id) { return H.fireAll((l) => l.el.id === id && l.type === 'click'); }\n"
           # 10-15 要修改合併（1015-4）：畫面重畫過好幾次、舊的元素還留著事件——只觸發最新那一個
           "async function last(id, type = 'click') { const l = H.listeners.filter((x) => x.el.id === id && x.type === type).pop();"
           " if (!l) throw new Error('沒有 ' + id); await l.fn({target: l.el, preventDefault() {}, stopPropagation() {}}); }\n"
           "async function pick(v) { const l = H.listeners.filter((x) => x.el.className === 'fc-fix-opt' && x.el.value === v && x.type === 'change').pop();"
           " if (!l) throw new Error('沒有選項 ' + v); await l.fn({target: l.el}); }\n"
           "function opts() { return [...document.querySelectorAll('.fc-fix-opt')].map((x) => x.value); }\n"
           "function posts() { return H.log.filter((x) => x[0] === 'POST'); }\n")
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
        {"名稱": "要修改：選項", "程式": """
          await open("名字:1"); const before = document.getElementById("fc-right").innerHTML; await last("fc-fix");
          const right = document.getElementById("fc-right").innerHTML;
          return {before, right, opts: opts()};"""},
        {"名稱": "要修改：聲音不像", "程式": """
          await open("名字:1"); await last("fc-fix"); await pick("聲音不像、不自然");
          const right = document.getElementById("fc-right").innerHTML;
          document.getElementById("fc-fix-extra").value = "第三句"; await last("fc-fix-extra", "input");
          await last("fc-fix-go"); return {log: posts(), right, open: fc.fix.open};"""},
        {"名稱": "要修改：念錯字", "程式": """
          await open("學員段落:T005"); await last("fc-fix"); await pick("念錯字");
          const right = document.getElementById("fc-right").innerHTML;
          const ta = document.getElementById("rv-text"); ta.value = "改過的字";
          await last("rv-text", "blur");
          const mid = {state: fcCards().find((c) => c["第3步鍵"] === "學員段落:T005")["狀態"], status: document.getElementById("fc-status").innerHTML};
          await last("fc-fix-go"); return {log: posts(), right, mid, open: fc.fix.open};"""},
        {"名稱": "要修改：長度或停頓怪", "程式": """
          await open("學員段落:T005"); await last("fc-fix"); await pick("長度或停頓怪");
          const right = document.getElementById("fc-right").innerHTML;
          fc.fixEd.b = fc.fixEd.b + 0.5;
          await last("fc-fix-go"); return {log: posts(), right, open: fc.fix.open};"""},
        {"名稱": "要修改：裡面有老師的聲音", "程式": """
          await open("學員段落:T005"); await last("fc-fix"); await pick("裡面有老師的聲音");
          const right = document.getElementById("fc-right").innerHTML;
          await last("rv-isteacher"); const mid = H.log.filter((x) => x[0] === "POST" || x[0] === "confirm"); H.log.length = 0;
          await last("fc-fix-go"); return {mid, log: H.log.filter((x) => x[0] === "POST" || x[0] === "confirm"), right, open: fc.fix.open};"""},
        {"名稱": "老師聲音等於其他", "程式": """
          await open("學員段落:T005"); await last("fc-fix"); await pick("裡面有老師的聲音");
          const t = document.getElementById("fc-right").innerHTML;
          await last("fc-fix-repick"); await pick("其他");
          const o = document.getElementById("fc-right").innerHTML;
          return {t, o, more: FC_CARD_CTX.moreOpen()};"""},
        {"名稱": "要重做的卡再開", "程式": """
          const k = card("學員段落:T005"); k["狀態"] = "退回"; k["退回原因"] = {"選項": ["長度或停頓怪"], "補充": "尾巴切太早"};
          await open("學員段落:T005"); await last("fc-fix");
          const right = document.getElementById("fc-right").innerHTML; const f = {pick: fc.fix.pick, extra: fc.fix.extra, a: fc.fixEd.a, b: fc.fixEd.b};
          fc.fixEd.b = fc.fixEd.b + 0.3; await last("fc-fix-go"); const log = posts();
          const k2 = card("學員段落:T005"); k2["狀態"] = "已改，等重做"; k2["退回原因"] = {"選項": ["念錯字"], "補充": ""};
          await open("學員段落:T005"); await last("fc-fix");
          return {right, f, log, right2: document.getElementById("fc-right").innerHTML, pick2: fc.fix.pick,
                  it: (DATA["第3步"]["項目"].find((x) => x.id === "T005") || {})};"""},
        {"名稱": "收起不撤回", "程式": """
          await open("學員段落:T005"); await last("fc-fix"); const first = document.getElementById("fc-right").innerHTML;
          await pick("念錯字"); const right = document.getElementById("fc-right").innerHTML;
          document.getElementById("rv-text").value = "改過的字"; await last("rv-text", "blur");
          await last("fc-fix-cancel");
          return {first, right, open: fc.fix.open, log: posts(), state: fcCards().find((c) => c["第3步鍵"] === "學員段落:T005")["狀態"]};"""},
        {"名稱": "通過不跳", "程式": """
          const k = card("名字:1");
          H.api['/api/final/card'] = {'卡片': {...k, '狀態': '通過'}, '統計': DATA['卡片統計']};
          await open("名字:1"); await last("fc-pass");
          const r = {cur: fc.cur === k.id, right: document.getElementById("fc-right").innerHTML,
                     lower: document.getElementById("fc-lower").innerHTML, tl: document.getElementById("fc-tl").innerHTML, id: k.id};
          H.api['/api/final/card'] = {'卡片': {...k, '狀態': '還沒看'}, '統計': DATA['卡片統計']};
          await last("fc-pass"); r.cur2 = fc.cur === k.id; r.log = posts(); r.right2 = document.getElementById("fc-right").innerHTML;
          return r;"""},
        {"名稱": "跳到", "程式": """
          await renderFinal();
          const box = document.getElementById("fc-goto"); const left = contentEl.innerHTML;
          const l = H.listeners.filter((x) => x.el.id === "fc-goto" && x.type === "keydown").pop();
          box.value = "1:05"; await l.fn({key: "Enter", isComposing: false, target: box, preventDefault() {}});
          const t1 = fc.video.currentTime, want = fcGotoOut(65);
          box.value = "亂打"; await l.fn({key: "Enter", isComposing: false, target: box, preventDefault() {}});
          return {left, t1, want, bad: box.classList.contains("bad"), t2: fc.video.currentTime};"""},
        {"名稱": "標題時間", "程式": """
          await open("學員段落:T005"); const a = document.getElementById("fc-right").innerHTML;
          const it = DATA["第3步"]["項目"].find((x) => x.id === "T005"); const c = card("學員段落:T005");
          await open("名字:1"); const b = document.getElementById("fc-right").innerHTML;
          const n = DATA["第3步"]["項目"].find((x) => rvKey(x) === "名字:1");
          return {a, b, wa: `${rvFmt(it.start, 1)}–${rvFmt(it.end, 1)}`, out: fcOutWhen(c), wb: fcWhenText(card("名字:1"), n)};"""},
        {"名稱": "要修改：其他", "程式": """
          await open("學員段落:T005"); await last("fc-fix"); await pick("其他");
          const right = document.getElementById("fc-right").innerHTML;
          document.getElementById("fc-fix-extra").value = "背景有雜音"; await last("fc-fix-extra", "input");
          await last("fc-fix-go"); return {log: posts(), right};"""},
        {"名稱": "要修改：改選與取消", "程式": """
          await open("學員段落:T005"); await last("fc-fix"); await pick("念錯字"); await last("fc-fix-repick");
          const again = opts().length; await last("fc-fix-cancel");
          return {again, open: fc.fix.open, right: document.getElementById("fc-right").innerHTML, log: posts()};"""},
        {"名稱": "要修改：E 鍵", "程式": """
          await open("學員段落:T005"); fcToggleFix(); return {open: fc.fix.open, card: fc.fix.card === card("學員段落:T005").id};"""},
        {"名稱": "要修改：沒有聲音可重做", "程式": """
          await open("刪除段落:S1"); await last("fc-fix");
          return {opts: opts(), right: document.getElementById("fc-right").innerHTML};"""},
        {"名稱": "取消要重做", "程式": """
          await open("學員段落:T003"); const right = document.getElementById("fc-right").innerHTML;
          await last("fc-back-undo"); return {right, log: posts()};"""},
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
          await open("學員段落:T005"); await last("fc-fix"); await pick("其他");
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
    assert 'id="fc-pad"' in v["right"] and "前後多 2 秒" in v["right"] and 'class="fc-switch"' in v["right"] and 'role="switch"' in v["right"]
    assert not re.search(r'id="fc-pad"[^>]*checked', v["right"])   # 滑動開關預設關
    assert "fc-preview" not in v["right"]   # 舊預覽的霧化樣式不能套到卡片的字
    assert "fc-back" in v["tl"] and "fc-todo" in v["tl"]
    posts = [x for x in got["通過"] if x[0] == "POST"]
    assert posts[0][1] == "/api/final/card" and posts[0][2]["結果"] == "通過" and posts[0][2]["id"].startswith("K")
    # 10-15 要修改合併（1015-4）：按鈕列「通過」「要修改」「上一張」「下一張」；「退回」「改」拿掉
    f = got["要修改：選項"]
    assert "錯誤" not in f, f
    assert 'id="fc-fix"' in f["before"] and "要修改" in f["before"] and 'id="fc-redo"' not in f["before"] and 'id="fc-edit"' not in f["before"]
    assert "哪裡出問題？" in f["right"] and "這一張重做只會重新放一次" not in f["right"]   # 名字卡有生成檔：列「聲音不像」
    assert f["opts"] == ["念錯字", "聲音不像、不自然", "長度或停頓怪", "裡面有老師的聲音", "其他"]
    v1 = got["要修改：聲音不像"]
    assert "錯誤" not in v1, v1
    assert "會換一種念法重新生成" in v1["right"] and "補充（可不填）" in v1["right"] and 'id="fc-fix-cancel"' in v1["right"]
    assert [x[1] for x in v1["log"]] == ["/api/final/card"], v1["log"]
    assert v1["log"][0][2]["結果"] == "退回" and v1["log"][0][2]["選項"] == ["聲音不像、不自然"] and v1["log"][0][2]["補充"] == "第三句"
    assert v1["open"] is False
    t1 = got["要修改：念錯字"]
    assert "錯誤" not in t1, t1
    assert 'id="rv-text"' in t1["right"] and "改好要念的字，再按「確定」" in t1["right"] and 'id="rv-isteacher"' not in t1["right"]
    assert [x[1] for x in t1["log"]] == ["/api/turns/save", "/api/final/edited", "/api/final/card"], t1["log"]
    assert t1["log"][0][2]["校對稿"] == "改過的字" and t1["log"][1][2]["鍵"] == "學員段落:T005"
    assert t1["log"][2][2]["選項"] == ["念錯字"] and t1["log"][2][2]["結果"] == "退回"
    assert t1["mid"]["state"] == "已改，等重做" and "改過的還沒重做" in t1["mid"]["status"] and t1["open"] is False
    r1 = got["要修改：長度或停頓怪"]
    assert "錯誤" not in r1, r1
    assert 'id="fc-fix-range"' in r1["right"]
    assert [x[1] for x in r1["log"]] == ["/api/final/card", "/api/review/manual", "/api/final/edited"], r1["log"]
    assert r1["log"][0][2]["選項"] == ["長度或停頓怪"]
    assert r1["log"][1][2]["id"] == "T005" and r1["log"][1][2]["類型"] == "學員發言"
    assert r1["log"][2][2] == {"鍵": "學員段落:T005", "新增": False}   # 改範圍不是新增
    th = got["要修改：裡面有老師的聲音"]
    assert "錯誤" not in th, th
    # 10-15 宇軒驗證回饋二：以前選「裡面有老師的聲音」只寫一句「整段改回老師的原聲」、「確定」直接存「說話者：老師」；
    # 改成跟「其他」一樣帶出整個選單（改回老師在選單裡的按鈕當場存，跟第 3 步一樣先問一次），「確定」只記原因
    assert "整段改回老師的原聲，不重念" not in th["right"] and "從游標處切開" in th["right"]
    assert 'id="rv-isteacher"' in th["right"] and 'id="rv-more"' in th["right"] and 'id="rv-text"' in th["right"]
    assert [x[0] for x in th["mid"]] == ["confirm", "POST", "POST"] and th["mid"][1][1] == "/api/turns/save" and th["mid"][1][2]["說話者"] == "老師"
    assert th["mid"][2][1] == "/api/final/edited"
    assert [x[1] for x in th["log"]] == ["/api/final/card"], th["log"]
    assert th["log"][0][2]["選項"] == ["裡面有老師的聲音"] and th["open"] is False
    te = got["老師聲音等於其他"]
    assert "錯誤" not in te, te
    assert te["t"].replace("<b>裡面有老師的聲音</b>", "<b>其他</b>") == te["o"] and te["more"] is True
    rd2 = got["要重做的卡再開"]
    assert "錯誤" not in rd2, rd2
    assert rd2["f"]["pick"] == "長度或停頓怪" and rd2["f"]["extra"] == "尾巴切太早"          # 帶入上次的原因與補充
    assert rd2["f"]["a"] == rd2["it"]["start"] and rd2["f"]["b"] == rd2["it"]["end"]      # 範圍從現在的值開始
    assert 'id="fc-fix-range"' in rd2["right"] and 'value="尾巴切太早"' in rd2["right"] and ">收起</button>" in rd2["right"]
    assert [x[1] for x in rd2["log"]] == ["/api/final/card", "/api/review/manual", "/api/final/edited"], rd2["log"]
    assert rd2["log"][0][2]["結果"] == "退回" and rd2["log"][0][2]["選項"] == ["長度或停頓怪"] and rd2["log"][0][2]["補充"] == "尾巴切太早"
    assert rd2["pick2"] == "念錯字" and 'id="rv-text"' in rd2["right2"]                  # 已改的卡也能再開、帶入原因
    sq = got["收起不撤回"]
    assert "錯誤" not in sq, sq
    assert ">收起</button>" in sq["first"] and ">收起</button>" in sq["right"] and ">取消</button>" not in sq["right"]
    assert sq["open"] is False and [x[1] for x in sq["log"]] == ["/api/turns/save", "/api/final/edited"]   # 改的字照留、沒記原因
    assert sq["state"] == "已改，等重做"
    ps = got["通過不跳"]
    assert "錯誤" not in ps, ps
    assert ps["cur"] is True and "✓ 已通過（再按取消）" in ps["right"] and 'aria-pressed="true"' in ps["right"]
    assert re.search(rf'data-id="{re.escape(ps["id"])}" class="cur done', ps["lower"]) and "fc-ok" in ps["tl"]
    assert ps["cur2"] is True and ps["log"][-1][2]["結果"] is None and "✓ 已通過" not in ps["right2"] and ">通過<kbd>" in ps["right2"]
    gt = got["跳到"]
    assert "錯誤" not in gt, gt
    assert 'id="fc-goto"' in gt["left"] and 'placeholder="跳到 43:15"' in gt["left"]
    assert gt["left"].index('id="fc-clock"') < gt["left"].index('id="fc-goto"') < gt["left"].index('id="fc-rate"')   # 在「1 倍速」左邊
    assert gt["want"] is not None and abs(gt["t1"] - gt["want"]) < 1e-6 and gt["bad"] is True and gt["t2"] == gt["t1"]
    tt = got["標題時間"]
    assert "錯誤" not in tt, tt
    assert f'<span class="rv-when">{tt["wa"]}</span>' in tt["a"] and tt["out"].startswith("成品 ") and tt["out"] in tt["a"]
    assert f'<span class="rv-when">{tt["wb"]}</span>' in tt["b"]
    o1 = got["要修改：其他"]
    assert "錯誤" not in o1, o1
    assert 'id="rv-isteacher"' in o1["right"] and "裡面有老師的聲音" in o1["right"] and 'id="rv-more"' in o1["right"] and 'id="rv-text"' in o1["right"]
    assert [x[1] for x in o1["log"]] == ["/api/final/card"] and o1["log"][0][2]["選項"] == ["其他"] and o1["log"][0][2]["補充"] == "背景有雜音"
    cc = got["要修改：改選與取消"]
    assert cc["again"] == 5 and cc["open"] is False and 'id="fc-fixbox"' not in cc["right"] and cc["log"] == []
    assert got["要修改：E 鍵"] == {"open": True, "card": True}
    nv = got["要修改：沒有聲音可重做"]
    assert "錯誤" not in nv, nv
    assert "聲音不像、不自然" not in nv["opts"] and nv["opts"] == ["念錯字", "長度或停頓怪", "裡面有老師的聲音", "其他"]
    assert "這一張重做只會重新放一次，聲音不會變；要改內容請選下面其他項" in nv["right"]
    un = got["取消要重做"]
    assert "取消要重做" in un["right"] and ">要重做<" in un["right"] and "原因：第二格念錯" in un["right"]
    assert un["log"][0][1] == "/api/final/card" and un["log"][0][2]["結果"] is None
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
    assert "要重做（沒寫原因）" in lk["right"] and f"跟 #{lk['n']} 共用，那張還是退回" in lk["right"] and "連帶" in lk["right"]



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


def test_card_lines_clamp_toggle():
    """10-15 宇軒驗證回饋：「原本」「改成」超過 2 行各自收合，按「展開 ▾」才看全部；不到 2 行不出現展開鈕；重畫回到收合。"""
    if not NODE:
        return
    got = _run_page([{"名稱": "收合", "程式": """
      card("學員段落:T005")["要念的字"] = ["很長的改成文字"];
      await renderFinal();
      const c = fcCards().find((x) => x["第3步鍵"] === "學員段落:T005");
      if (!c) return {none: true};
      H.listeners.length = 0; fcSelect(c.id, false);
      const box = document.getElementById("fc-right");
      const html = box.innerHTML;
      const btn = (k) => box.querySelector(`[data-fc-more="${k}"]`);
      const line = (k) => box.querySelector(`[data-fc-line="${k}"]`);
      const short0 = [btn("orig").hidden, btn("said").hidden];          // 假瀏覽器 scrollHeight 沒設＝沒超過
      line("orig").scrollHeight = 300; line("orig").clientHeight = 54;   // 「原本」超過 2 行、「改成」沒有
      line("said").scrollHeight = 54; line("said").clientHeight = 54;
      H.listeners.length = 0; fcClampSetup(box);
      const after = [btn("orig").hidden, btn("said").hidden];
      const n = H.listeners.filter((l) => l.el.dataset.fcMore).length;
      const tgl = () => H.listeners.find((l) => l.el.dataset.fcMore === "orig").fn({});
      tgl(); const opened = [line("orig").classList.contains("open"), btn("orig").textContent, btn("orig").getAttribute("aria-expanded"),
                             line("said").classList.contains("open")];
      tgl(); const closed = [line("orig").classList.contains("open"), btn("orig").textContent, btn("orig").getAttribute("aria-expanded")];
      tgl(); fcRenderRight();
      return {html, short0, after, n, opened, closed, reset: [box.querySelector('[data-fc-line="orig"]').classList.contains("open"),
              box.querySelector('[data-fc-more="orig"]').hidden]};"""}])["收合"]
    assert "錯誤" not in got and not got.get("none"), got
    h = got["html"]
    assert h.count('class="fc-clamp"') == 2 and 'data-fc-line="orig"' in h and 'data-fc-line="said"' in h
    assert h.count('class="fc-more"') == 2 and "展開 ▾" in h
    assert got["short0"] == [True, True]                 # 沒超過 2 行：沒有展開鈕
    assert got["after"] == [False, True] and got["n"] == 1   # 只有超過的那一行有、各自獨立
    assert got["opened"] == [True, "收起 ▴", "true", False]
    assert got["closed"] == [False, "展開 ▾", "false"]
    assert got["reset"] == [False, True]                 # 重畫卡片回到收合
    css = (WEB / "style.css").read_text(encoding="utf-8")
    assert "-webkit-line-clamp: 2" in css and re.search(r"\.fc-more \{[^}]*font-size: 1[4-9]px", css)


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
        labPass: fcCardLabel({"狀態": "還沒生成", "這一版沒處理到": true, "上一版已通過": true}),
        labNone: fcCardLabel({"狀態": "還沒生成", "這一版沒處理到": true, "上一版已通過": false}),
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
    # 10-15 宇軒驗證回饋三：「這一版沒處理到」改標「上一版已通過」＋小字；之前沒通過過的（舊版畫面）標「這一版沒做這張」
    assert got["lab"] == got["labPass"] == {"text": "上一版已通過", "cls": "made", "small": ["這版沒改到這裡，不用再看"]}
    assert got["labNone"] == {"text": "這一版沒做這張", "cls": "made", "small": ["這一版的成品沒有處理這一張"]}
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
          await click("fc-fix");   // 10-15 要修改合併（1015-4）：跨版本卡「要修改」整顆停用、打不開
          const opened = fc.fix.open || !!document.getElementById("fc-fixbox");
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
    assert "上一版還沒看的（第 1 版）" in v["lower"] and "上一版已通過" in v["lower"] and "這一版沒處理到" not in v["lower"] and "fc-faded" in v["lower"]
    assert "現在 ▶" in v["right"]
    assert re.search(r'id="fc-fix"[^>]*disabled title="要修改請回到最新一版"', v["right"]) and not v["opened"]
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
    assert "＋在這裡新增修改" not in o["lower"] and "重做這幾張" not in o["lower"]
    assert re.search(r'id="fc-fix"[^>]*disabled title="要修改請回到最新一版"', o["right"])   # 10-15 要修改合併（1015-4）：舊版整顆停用
    assert not any(x[0] == "POST" and x[2].get("結果") == "退回" for x in o["log"])
    assert o["video"].startswith("/api/final/video?版本=1&v=")
    assert [x for x in o["log"] if x[0] == "POST"][0][2]["版本"] == 1
    assert [x for x in o["exp"] if x[0] == "POST"][0][2] == {"版本": 1}


# 10-15 前後 2 秒開關統一（1015-5）：第 5 步的開關跟第 3 步共用同一個鍵（fc-ab-pad）；「要修改」裡不再多一顆；
# 關著只播範圍本身、開著前後各多 2 秒（「原本／現在 ▶」與「要修改」裡的「聽這一段」都照它）
_LS = ("globalThis.LS = {fake: true, store: {}, getItem(k) { return k in this.store ? this.store[k] : null; }, setItem(k, v) { this.store[k] = String(v); }};"
       " globalThis.localStorage = LS;\n")


def test_pad_switch_shared_step5():
    if not NODE:
        print("（沒有 node，略過）")
        return
    sc = """
          %s if (ON) LS.store["fc-ab-pad"] = "1";
          await open("名字:1");
          const before = document.getElementById("fc-right").innerHTML;
          const c = card("名字:1"); fcPlayAB(c, "現在"); const ab = {from: fc.ab && fc.ab.from, to: fc.ab && fc.ab.to}; fcStopAB();
          await last("fc-fix"); await pick("念錯字");
          const right = document.getElementById("fc-right").innerHTML;
          const w = FC_CARD_CTX.data()["項目"].find((x) => rvKey(x) === "名字:1")["整句"];
          await last("rv-playrange");
          const src = fc.audio.src;
          const pad = document.getElementById("fc-pad"); pad.checked = !ON; await last("fc-pad", "change");
          return {before, right, ab, p: c["成品"], w: [w.start, w.end], src, stored: LS.store["fc-ab-pad"]};"""
    got = {}
    for name, on in (("關", "false"), ("開", "true")):
        got[name] = _run_page([{"名稱": name, "程式": f"const ON = {on};" + sc % _LS.replace("\n", " ")}])[name]
    off, on = got["關"], got["開"]
    assert "錯誤" not in off and "錯誤" not in on, (off, on)
    assert not re.search(r'id="fc-pad"[^>]*checked', off["before"]) and re.search(r'id="fc-pad"[^>]*checked', on["before"])
    for v in (off, on):
        assert v["right"].count("data-cv-pad") == 1, "一張卡只能有一顆開關（要修改裡的重念範圍不再放）"
        assert "聽這一段" in v["right"] and "只播重念範圍" not in v["right"]
    p = off["p"]
    assert (off["ab"]["from"], off["ab"]["to"]) == (max(0, p[0]), p[1]), off["ab"]                 # 關：只播範圍本身
    assert (on["ab"]["from"], on["ab"]["to"]) == (max(0, p[0] - 2), p[1] + 2), on["ab"]            # 開：前後各多 2 秒
    a, b = off["w"]
    assert off["src"] == f"/api/audio?start={a:.2f}&end={b:.2f}", off["src"]
    assert on["src"] == f"/api/audio?start={max(0, a - 2):.2f}&end={b + 2:.2f}", on["src"]
    assert off["stored"] == "1" and on["stored"] == "0"   # 撥開關寫回同一個鍵


# ---------- 10-15 宇軒驗證回饋三：「載入失敗：flat.push is not a function」 ----------

def _web_globals() -> dict[str, list[str]]:
    """網頁 js 的最上層函式、常數名稱 → 在哪幾支檔案（同一個網頁共用全域，同名會互相蓋掉）。"""
    seen: dict[str, list[str]] = {}
    for f in sorted(WEB.glob("*.js")):
        src = f.read_text(encoding="utf-8")
        names = re.findall(r"^(?:async )?function ([\w$]+)", src, re.M)
        for m in re.finditer(r"^(?:const|let|var) (.+?);?$", src, re.M):
            # 一行宣告好幾個（const A = 1, B = 2;）：只抓等號前、頂層逗號後的名稱
            names += re.findall(r"(?:^|,\s*)([A-Za-z_$][\w$]*)\s*=", m.group(1))[:1] + re.findall(r",\s*([A-Z_$][A-Z0-9_$]*)\s*=", m.group(1))
        for n in set(names):
            seen.setdefault(n, []).append(f.name)
    return seen


def test_web_globals_unique():
    """根本原因：app.js 第 4 步「開始前總檢查」的 fcKeepHtml(r, i) 跟 finalcheck.js「第 3 步選定不修改」那一頁的 fcKeepHtml(flat)
    同名；finalcheck.js 最後載入、蓋掉第 4 步那一支，第 4 步畫「一定要處理」時把那一列物件當 flat → flat.push is not a function。"""
    dup = {k: v for k, v in _web_globals().items() if len(v) > 1}
    assert not dup, f"網頁 js 的全域名稱重複（會互相蓋掉）：{dup}"
    assert "fcKeepHtml" in _web_globals() and _web_globals()["fcKeepHtml"] == ["app.js"]


def test_step4_keep_buttons_with_finalcheck_loaded():
    """第 4 步「一定要處理」的按鈕：app.js 的 fcKeepHtml 之後再載入整支 finalcheck.js，還是第 4 步那一支（不丟錯）。"""
    if not NODE:
        return
    fc_fns = "".join(m.group(0) for m in re.finditer(r"^(async )?function [\w$]+\(.*?^\}\n", FCJS, re.S | re.M))
    code = (_ESC + _fn(APPJS, "fcKeepHtml") + fc_fns   # finalcheck.js 的函式全部在後面宣告（跟網頁載入順序一樣）
            + """;console.log(JSON.stringify([fcKeepHtml({"可以按照目前設定做": true, key: "k1"}, 0),
                 fcKeepHtml({"回第3步補": true, "照目前設定做不開放原因": "x"}, 1)]));""")
    got = _node(code)
    assert 'data-fckeep="k1"' in got[0] and 'data-fcback="1"' in got[1]


def test_every_tab_every_version_renders():
    """各版本（最新、舊版、缺欄位的版本頁）× 各分頁（含停在「第 3 步選定不修改」「需留意」再切版本）都畫得出來、不丟錯。"""
    if not NODE:
        return
    latest, old = _version_data(), _version_data(old=True)
    base, _w = _api_final()
    # 照 8799 複本第 3 版（範圍 0-3）那一份的樣子：卡片 10 張、跨版本 40 張、需留意沒有（null）
    v3 = json.loads(json.dumps(latest))
    v3["需留意"], v3["不修改"] = None, []
    # 更舊的回應：那幾個欄位整個沒有
    bare = json.loads(json.dumps(old))
    for k in ("不修改", "不修改類型", "需留意", "需留意類型", "跨版本卡片", "看過區段"):
        bare.pop(k, None)
    weird = json.loads(json.dumps(old))
    weird["不修改"], weird["需留意"] = {}, {"有紀錄": True, "列": None}
    tabs = ["全部", "還沒看完", "不修改", "需留意", "名字", "學員段落", "其他", "不存在的分頁"]
    sc = """%s LS.store["fc-tab"] = TAB;
          await renderFinal();
          const lower = document.getElementById("fc-lower").innerHTML;
          return {lower, err: H.log.filter((x) => x[0] === "錯誤")};"""
    for name, data in (("最新", latest), ("舊版", old), ("第3版樣子", v3), ("缺欄位", bare), ("怪欄位", weird), ("有不修改需留意", base)):
        got = _run_page([{"名稱": t, "程式": f"const TAB = {json.dumps(t, ensure_ascii=False)};" + sc % _LS.replace("\n", " ")}
                         for t in tabs], data=data)
        for t in tabs:
            v = got[t]
            assert isinstance(v, dict) and "錯誤" not in v and not v["err"], (name, t, v)
            assert 'class="rv-filters fc-tabs"' in v["lower"], (name, t)
            has_keep = bool((data.get("不修改") or []) if isinstance(data.get("不修改"), list) else False)
            if t == "不修改" and not has_keep:
                assert "這一版沒有「第 3 步選定不修改」這一頁" in v["lower"], (name, v["lower"][:300])
            if t == "需留意" and not data.get("需留意"):
                assert "這一版沒有「需留意」這一頁" in v["lower"], (name, v["lower"][:300])
    keep = _run_page([{"名稱": "k", "程式": "const TAB = '不修改';" + sc % _LS.replace("\n", " ")}], data=base)["k"]
    if base.get("不修改"):
        assert 'id="fc-ulist"' in keep["lower"] and "這一版沒有" not in keep["lower"]


def test_fill_defaults_pure():
    if not NODE:
        return
    got = _node(_const(FCJS, "FC_LIST_FIELDS") + _fn(FCJS, "fcFillDefaults") + """
      console.log(JSON.stringify([fcFillDefaults({"不修改": null, "需留意": {"有紀錄": true}}), fcFillDefaults(null)]));""")
    a, b = got
    assert a["不修改"] == [] and a["跨版本卡片"] == [] and a["需留意"] == {"有紀錄": True, "列": []}
    assert b["卡片"] == [] and b["需留意"] is None and b["卡片統計"] == {}


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
