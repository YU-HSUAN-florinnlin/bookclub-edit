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
         + _const(FCJS, "FC_REDO_LABEL") + _const(FCJS, "FC_NO_REASON_SHOW") + _const(FCJS, "FC_GO4")
         + _const(FCJS, "FC_FIX_TEXT") + _const(FCJS, "FC_FIX_OPTS")
         + _fn(FCJS, "fcNoFix") + _fn(FCJS, "fcCanFix") + _fn(FCJS, "fcFixOff") + _fn(FCJS, "fcCanUndo") + _fn(FCJS, "fcLatestRedoLine") + _fn(FCJS, "fcFixVer") + _fn(FCJS, "fcFixOptions") + _fn(FCJS, "fcFixRecords")
         + _const(FCJS, "FC_TAB_ALL") + _const(FCJS, "FC_TYPE_TABS")
         + _const(FCJS, "FC_OUT_PASSED") + _const(FCJS, "FC_OUT_NONE")
         + _fn(FCJS, "fcShortStamp") + _fn(FCJS, "fcCardLabel") + _fn(FCJS, "fcReasonText") + _fn(FCJS, "fcStatusLine") + _fn(FCJS, "fcLeftCount") + _fn(FCJS, "fcOldRedoCount")
         + _const(FCJS, "FC_NO_REASON") + _fn(FCJS, "fcWhyLine") + _fn(FCJS, "fcSharedLine") + _fn(FCJS, "fcRedoCount")
         + _const(FCJS, "FC_NOTE_FIELDS") + _fn(FCJS, "fcNoteOnly")
         + _fn(FCJS, "fcTabList") + _fn(FCJS, "fcIsPassed") + _fn(FCJS, "fcStatusOf") + _fn(FCJS, "fcStatusGroups") + _fn(FCJS, "fcSplitLists") + _fn(FCJS, "fcPickTab") + _fn(FCJS, "fcStepPick") + _fn(FCJS, "fcFixPrefill")
         + _fn(FCJS, "fcSrcTime") + _fn(FCJS, "fcOutTime") + _fn(FCJS, "fcGotoOut") + _fn(FCJS, "fcPadOf") + _fn(FCJS, "fcVclip") + _fn(FCJS, "fcCardAbPlan")
         + _fn(FCJS, "fcOrigText") + _fn(FCJS, "fcCardTextHtml")
         + _fn(FCJS, "fcVersionText") + _fn(FCJS, "fcVersionMenuHtml") + _fn(FCJS, "fcCanJudge"))


def test_status_line_one_sentence_in_priority():
    if not NODE:
        print("（沒有 node，略過）")
        return
    got = _node(_PURE + """
      // 10-15 總檢查修正（1015-7）：「還有 N 張沒看」的 N＝卡片統計.還要確認（後端，含跨版本卡）
      const base = {"卡片統計": {"還沒看": 3, "要重做": 0, "還要確認": 3}, "需留意": {"列": []}};
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
      ].map((x) => [x.text, x.sub || "", x.btn ? x.btn.kind : null, x.warn || null])));""")
    # 1025-3 #294：狀態卡——有要重做＝「N 張要重做」＋回第 4 步＋按鈕到第 4 步；還有沒看＝只有一句；全部看完＝輸出成品；本名警告另放一行
    W = "⚠️ 這 2 處會照原聲念出本名"
    assert got == [["正在重做…", "做完會回到這裡", None, W],
                   ["正在重做…", "做完會回到這裡", None, None],
                   ["2 張要重做", "上次重做沒做完，回第 4 步按「繼續執行」", "step4", None],
                   ["2 張要重做", "回第 4 步按「更新成品」", "step4", None],
                   ["還有 3 張沒看", "", None, None],
                   ["全部看完了（已輸出 10/14 14:20）", "", "export", None],
                   ["全部看完了", "", "export", None],
                   ["全部看完了", "", "export", None]], got


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
        grp: (() => { const g = fcStatusGroups([{id: "a", "狀態": "還沒看"}, {id: "b", "狀態": "退回"}, {id: "c", "狀態": "通過"}, {id: "d", "狀態": "已改，等重做"},
            {id: "e", "狀態": "通過", "最新版要重做": {"選項": []}}, {id: "f", "狀態": "還沒生成"}]);
          return Object.fromEntries(Object.entries(g).map(([k, v]) => [k, v.map((c) => c.id)])); })(),
        note: [fcNoteOnly({id: "T1", "問老師": true}), fcNoteOnly({id: "T1", "問老師備註": "x"}), fcNoteOnly({id: "M1", "備註": "x"}),
               fcNoteOnly({id: "T1", "校對稿": "x"}), fcNoteOnly({id: "T1", "問老師": true, "說話者": "老師"}), fcNoteOnly({})]}));""")
    assert got["why"] == ["要重做（沒寫原因）", "要重做（沒寫原因）", "要重做（沒寫原因）", "原因：念錯字"]
    assert got["shared"] == ["跟 #12 共用，那張還是要重做", "跟 另一張卡 共用，那張還是要重做", ""]   # 10-15 總檢查修正（1015-7）
    assert got["lab"] == [["連帶"], ["在第 3 步改過"]]
    assert got["redo"] == [{"show": True, "n": 2}, {"show": True, "n": 0}, {"show": False, "n": 0}, {"show": False, "n": 0}]
    assert got["st"] == ["1 張要重做", "全部看完了"]
    assert got["grp"] == {"還沒看": ["a"], "要重做": ["b", "d", "f"], "已通過": ["c", "e"]}, got["grp"]
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
        off: [fcFixOff(null, true), fcFixOff(null, false)],
        old: [fcCanFix({"狀態": "通過"}, true), fcCanFix({"狀態": "通過", "跨版本": true, "版本號": 2}, true),
              fcCanFix({"狀態": "退回", "跨版本": true}, false), fcCanFix({"狀態": "還沒生成"}, false), fcCanFix({"狀態": "通過"}, false)],
        ver: [fcFixVer({"跨版本": true, "版本號": 2}, false), fcFixVer({"版本號": 1}, true), fcFixVer({"版本號": 3}, false),
              fcFixVer({"這一版沒處理到": true, "版本號": 3}, false), fcFixVer(null, true)],
        opts: [fcFixOptions({"可重新生成": true}, all), fcFixOptions({"可重新生成": false}, all), fcFixOptions({"可重新生成": true}, [])],
        rec: [fcFixRecords({"狀態": "還沒看"}), fcFixRecords({"狀態": "還沒生成"}), fcFixRecords({"狀態": "已改，等重做", "紀錄鍵": ["a"]}),
              fcFixRecords({"狀態": "已改，等重做", "紀錄鍵": []}), fcFixRecords({"狀態": "退回"}),
              fcFixRecords({"狀態": "還沒生成", "這一版沒處理到": true}), fcFixRecords({"狀態": "已改，等重做", "紀錄鍵": [], "範圍外改了": true}),
              fcFixRecords({"狀態": "還沒看", "跨版本": true}), fcFixRecords({"狀態": "還沒生成"}, true)],
        outOld: fcCanFix({"狀態": "還沒生成", "這一版沒處理到": true}, true)}));""")
    # 10-15 宇軒驗證回饋六：不論狀態、跨版本、看哪一版，有卡就能按「要修改」；停用的字只剩沒卡時，不再出現「回到最新一版」
    assert got["can"] == [True, True, True, True, True, False] and got["outOld"] is True and got["old"] == [True] * 5
    assert got["off"] == ["先選一張卡"] * 2 and not any("最新一版" in x for x in got["off"])
    # 跨版本卡、舊版的卡帶那張卡的版本號（後端記在卡片的等重做）；最新版這一版的卡（含這一版沒處理到）不帶
    assert got["ver"] == [2, 1, None, None, None]
    assert got["opts"][0] == ["念錯字", "聲音不像、不自然", "長度或停頓怪", "裡面有老師的聲音", "其他"] == got["opts"][2]
    assert got["opts"][1] == ["念錯字", "長度或停頓怪", "裡面有老師的聲音", "其他"]
    assert got["rec"] == [True, False, True, False, True, True, True, True, True]   # 範圍外、跨版本、舊版的：原因記在卡片的等重做


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
        // 10-15 宇軒驗證回饋六：分頁含跨版本卡、沒有「還沒看完」；兩個清單照原片時間排；上一張／下一張到頭到尾停住、照時間找不拿跨版本卡比
        cross: (() => {
          const X = [{"id": "p", "類型": "名字", "狀態": "還沒看", "跨版本": true, "版本號": 2, "原片": [5, 6], "成品": [41, 42]},
                     {"id": "q", "類型": "學員段落", "狀態": "退回", "跨版本": true, "版本號": 2, "原片": [1, 2], "成品": [44, 45]}];
          const D = C.map((c, i) => ({...c, "原片": [[30, 20, null, 10, 40][i], 99]})).concat([{"id": "o", "類型": "名字", "狀態": "還沒生成", "這一版沒處理到": true, "上一版已通過": true, "原片": [3, 4]},
                     {"id": "n", "類型": "名字", "狀態": "還沒生成", "這一版沒處理到": true, "上一版已通過": false, "原片": [2, 3]}]);
          const T2 = fcTabList(D, 0, null, X);
          const sp = fcSplitLists(T2[0].cards);
          const nav = sp.todo.concat(sp.passed);
          return {tabs: T2.map((t) => [t.id, t.cards.map((c) => c.id)]), todo: sp.todo.map((c) => c.id), passed: sp.passed.map((c) => c.id),
                  stepEnd: [fcStepPick(nav, nav[nav.length - 1].id, 0, 1), fcStepPick(nav, nav[0].id, 0, -1)],
                  stepJoin: [fcStepPick(nav, sp.todo[sp.todo.length - 1].id, 0, 1), fcStepPick(nav, sp.passed[0].id, 0, -1)],
                  byTime: [fcStepPick(nav, "zz", 40, 1), fcStepPick(nav, "zz", 43, -1)]};
        })(),
        pre: [fcFixPrefill({"狀態": "退回", "退回原因": {"選項": ["長度或停頓怪"], "補充": "尾巴切太早"}}),
              fcFixPrefill({"狀態": "已改，等重做", "退回原因": {"選項": ["裡面有老師的聲音"], "補充": ""}}),
              fcFixPrefill({"狀態": "已改，等重做"}), fcFixPrefill({"狀態": "通過", "退回原因": {"選項": ["其他"], "補充": "x"}}),
              fcFixPrefill({"狀態": "退回", "退回原因": {"選項": ["聲音不像、不自然"], "補充": "y"}}, ["念錯字", "其他"])] }));""")
    # 10-15 宇軒驗證回饋六：「還沒看完」分頁拿掉
    assert got["tabs"] == [["全部", "全部", ["a", "b", "c", "d", "e"], None],
                           ["學員段落", "學員段落", ["a", "d"], None], ["名字", "名字", ["b"], None], ["刪除段落", "剪掉", ["e"], None],
                           ["其他", "其他", ["c"], None], ["不修改", "第 3 步選定不修改", [], 2], ["需留意", "⚠️ 需留意", [], 1]]
    assert got["pick"] == ["名字", "全部", "全部"]
    assert got["step"] == ["c", "a", "d", "b", None]
    x = got["cross"]
    assert ["名字", ["b", "o", "n", "p"]] in x["tabs"] and ["學員段落", ["a", "d", "q"]] in x["tabs"] and x["tabs"][0][1][-2:] == ["p", "q"]
    assert all(t[0] != "還沒看完" for t in x["tabs"])
    # 上面：還沒看／退回／已改／還沒生成＋這一版沒做這張（上一版沒通過）＋跨版本，照原片時間（沒有原片時間的 c 排最後）；下面：通過＋已通過（沿用上一版）
    assert x["todo"] == ["q", "n", "p", "d", "b", "e", "c"] and x["passed"] == ["o", "a"]
    assert x["stepEnd"] == ["a", "q"]                      # 到尾、到頭停住
    assert x["stepJoin"] == ["o", "c"]                     # 上面最後一張 → 下面第一張；下面第一張 → 上面最後一張
    assert x["byTime"] == ["d", "b"]                       # 照成品時間找只拿這一版的卡（p、q 的成品時間是舊版的）
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
        go: [fcGotoOut(12.5, 30), fcGotoOut(30, 30), fcGotoOut(31, 30), fcGotoOut(null, 30), fcGotoOut(-1, 30), fcGotoOut(99, 0)],
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
    # 10-26 宇軒：「跳到」框打的是成品（修改後影片）時間，原樣跳；空的、負的、比成品長度晚＝null；長度未知（0）不限
    assert got["go"] == [12.5, 30, None, None, None, 99], got["go"]
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
                 "會換一種念法重新生成", "要修改<kbd>E</kbd>", "取消要重做", "要重做（沒寫原因）", "上一張", "下一張", "＋在這裡新增修改", "打開成品資料夾", "匯入交接包…", "依類型", "我不確定是哪一種，先記下來", "只看要仔細聽的",
                 "這一頁都看完了", "這一版沒有任何修改", "第 ${idx + 1}／${list.length} 張", "請仔細聽", "原本 ▶", "現在 ▶"):
        assert want in FCJS, want
    for gone in ("沒列在時間軸上的地方＝原片沒動", "送回 AI 重做", "逐筆看", "整片看", "調整時間範圍", "AI 重做時照這個改", "AI 照這個改",
                 "已換成品", "要重新輸出", "處理紀錄產生於", "超過 2 倍速播的不算看過", "念的稿子：", "檢查哪一支", "跳到 38:26",
                 "退回重做</button>", "上一筆", "下一筆", "為什麼退回？", "確定退回", "取消退回", ">退回</button>", ">改<kbd>",
                 'id="fc-redo"', 'id="fc-edit"', "要退回請回到最新一版", "要修改請回到最新一版", "回到最新一版", "fc-cross", "處理前", "處理後", "試聽（前後各多 2 秒）", "目前這一筆在", "fcPreviewHtml"):
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
           "function posts() { return H.log.filter((x) => x[0] === 'POST'); }\n"
           # 10-15 修正：後端 finalcards.decide 回傳的卡沒有 `有第3步卡片`（只在整頁資料裡有）——假回傳照真的形狀拿掉
           "function bare(k, patch) { const x = {...k, ...(patch || {})}; delete x['有第3步卡片']; return x; }\n")
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
                  bar: document.getElementById("fc-actbar").innerHTML, top: "", left: contentEl.innerHTML,
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
          H.api['/api/final/card'] = {'卡片': bare(k, {'狀態': '通過'}), '統計': DATA['卡片統計']};
          await open("名字:1"); await last("fc-pass");
          const r = {cur: fc.cur === k.id, right: document.getElementById("fc-right").innerHTML,
                     lower: document.getElementById("fc-lower").innerHTML, tl: document.getElementById("fc-tl").innerHTML, id: k.id};
          H.api['/api/final/card'] = {'卡片': bare(k, {'狀態': '還沒看'}), '統計': DATA['卡片統計']};
          await last("fc-pass"); r.cur2 = fc.cur === k.id; r.log = posts(); r.right2 = document.getElementById("fc-right").innerHTML;
          return r;"""},
        {"名稱": "跳到", "程式": """
          await renderFinal();
          const box = document.getElementById("fc-goto"); const left = contentEl.innerHTML;
          const l = H.listeners.filter((x) => x.el.id === "fc-goto" && x.type === "keydown").pop();
          box.value = "1:05"; await l.fn({key: "Enter", isComposing: false, target: box, preventDefault() {}});
          const t1 = fc.video.currentTime, want = 65;
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
        {"名稱": "到第4步", "程式": """
          globalThis.sessionStorage = {s: {}, getItem(k) { return k in this.s ? this.s[k] : null; }, setItem(k, v) { this.s[k] = String(v); }, removeItem(k) { delete this.s[k]; }};
          await renderFinal(); const before = fcRedoArmGet(); H.log.length = 0; await click("fc-go4");
          const armed = fcRedoArmGet(); await renderFinal();
          return {log: H.log, hash: location.hash, gone: !document.getElementById("fc-redoall"), before, armed, afterBack: fcRedoArmGet()};"""},
        {"名稱": "只改問老師不標已改", "程式": """
          await open("學員段落:T005"); await last("fc-fix"); await pick("其他");
          await FC_CARD_CTX.post("/api/turns/save", {id: "T005", "問老師": true}); return H.log;"""},
        {"名稱": "改完用回傳的整頁資料", "程式": """
          const page = JSON.parse(JSON.stringify(DATA)); page["卡片"].find((c) => c["第3步鍵"] === "學員段落:T005")["狀態"] = "已改，等重做";
          H.api['/api/final/edited'] = {'記了': true, '頁面': page};
          await open("學員段落:T005"); await FC_CARD_CTX.post("/api/turns/save", {id: "T005", "說話者": "老師"}); await FC_CARD_CTX.reload();
          return {log: H.log, state: fcCards().find((c) => c["第3步鍵"] === "學員段落:T005")["狀態"]};"""},
        {"名稱": "舊檔模式", "程式": """
          // 10-15 總檢查修正（1015-7）：照後端舊檔模式真的會給的樣子（finalcards.build：沒有跨版本卡、每張卡的 已通過的 跟狀態一致、
          // 卡片統計 帶 還要確認）——以前只改狀態，後端算好的 已通過的 還是 false，畫面照它算成「還有 N 張沒看」
          const old = JSON.parse(JSON.stringify(DATA)); old["卡片來源"] = "舊檔"; old["跨版本卡片"] = [];
          old["卡片"].forEach((c) => { c["狀態"] = "通過"; c["已通過的"] = true; });
          old["卡片統計"] = {"還沒看": 0, "要重做": 0, "已通過的": old["卡片"].length, "還要確認": 0};
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
    assert "張要重做" in v["status"] and "回第 4 步按「更新成品」" in v["status"]   # T003 退回了 → 要重做
    assert "原本 ▶" in v["right"] and "現在 ▶" in v["right"] and 'id="fc-pass"' in v["right"] and "第 1／" in v["right"]
    assert re.search(r'class="idtag"[^>]*>#\d+<', v["right"])                 # #103：每張卡顯示 #編號
    # 10-15 宇軒驗證回饋：兩顆按鈕搬到時間軸下面、「看過 P%」之下（以前在清單底下 fc-lower）
    # 10-16 宇軒：「重做這幾張」搬到右上角「輸出成品」左邊；時間軸下面只剩「＋在這裡新增修改」
    # 1025-3 #294：「重做這幾張」拿掉（狀態卡的「到第 4 步 →」帶去第 4 步按「更新成品」）；右上「⋯」選單放看哪一版、打開成品資料夾、輸出成品、交接
    assert "重做這幾張" not in v["bar"] + v["lower"] + v["left"] and "＋在這裡新增修改" in v["bar"] and "＋在這裡新增修改" not in v["lower"]
    assert "打開成品資料夾" in v["left"] and "交接打包…" in v["left"] and "匯入交接包…" in v["left"] and "tool-menu-hint" in v["left"] and "輸出成品" in v["left"]
    assert 'data-sf="還沒看"' in v["lower"] and 'data-sf="需留意"' in v["lower"] and 'id="fc-type"' in v["lower"] and 'data-tab="還沒看完"' not in v["lower"]
    assert "fc-seenpct" not in v["left"] and 'id="fc-pct"' in v["left"] and 'aria-label="更多"' in v["left"] and 'id="fc-go4"' in v["status"]
    assert v["left"].index('id="fc-actbar"') < v["left"].index('id="fc-io"')
    assert 'id="fc-pad"' in v["right"] and "前後多聽 2 秒" in v["right"] and 'class="fc-switch"' in v["right"] and 'role="switch"' in v["right"]
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
    assert t1["mid"]["state"] == "已改，等重做" and "張要重做" in t1["mid"]["status"] and t1["open"] is False
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
    # 10-16 #225 宇軒：「裡面有老師的聲音」不再等於「其他」——學員段落卡先給「整段改成老師」「從游標處切開」（說明在問號），其他做法收起來
    assert te["t"] != te["o"] and te["more"] is True
    assert ">整段改成老師</button>" in te["t"] and ">從游標處切開</button>" in te["t"] and 'class="fc-other"' in te["t"]
    assert te["t"].count('id="rv-split"') == 1 and te["t"].count('id="rv-isteacher"') == 1
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
    assert "取消要重做" not in ps["log"][-1][2]   # 取消通過不帶「取消要重做」
    for k in ("right", "right2"):   # 10-15 修正：換成後端回傳的卡之後照樣找得到第 3 步卡片（小標、那一行字不變）
        assert 'class="rv-chip name"' in ps[k] and "fc-ov" not in ps[k] and "剛剛Amy分享得很好" in ps[k], ps[k][:800]
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
    assert un["log"][0][1] == "/api/final/card" and un["log"][0][2]["結果"] is None and un["log"][0][2]["取消要重做"] is True
    u = got["新增不確定"]
    assert "我不確定是哪一種，先記下來" in u["io"]
    posts = [x for x in u["log"] if x[0] == "POST"]
    assert posts[0][1] == "/api/final/flag" and posts[0][2]["原因"] == "不確定是哪一種" and abs(posts[0][2]["成品秒"] - 100) < 0.01, posts
    assert not any(x[1] == "/api/final/edited" for x in posts)
    m = [x for x in got["新增消音"] if x[0] == "POST"]
    assert m[0][1] == "/api/review/manual" and m[0][2]["類型"] == "局部消音" and m[0][2]["start"] == 100
    assert m[1][1] == "/api/final/edited" and m[1][2] == {"鍵": "局部消音:M009", "新增": True}   # 新增的卡記「第5步新增」、不是記在目前這張
    rd = got["到第4步"]   # 1025-3 #294：右上的「重做這幾張」拿掉，狀態卡的按鈕只換頁、不送請求
    assert [x for x in rd["log"] if x[0] == "POST"] == [] and rd["hash"] == "#step4" and rd["gone"]
    # 按「到第 4 步 →」記下「從第 5 步來的」，第 4 步按開始時（app.js）才記 started_at、跑完自動跳回；回到第 5 步就取消
    assert rd["before"] is False and rd["armed"] is True and rd["afterBack"] is False, rd
    app = APPJS[APPJS.index('const execBtn = document.getElementById("btnExec")'):]
    assert "fcRedoArmGet()" in app[:1500] and 'fcRedoBackSet(started && started["started_at"])' in app[:1500]
    # 10-15 第2段審查修正
    q = [x[1] for x in got["只改問老師不標已改"] if x[0] == "POST"]
    assert q == ["/api/turns/save"], q
    e2 = got["改完用回傳的整頁資料"]
    assert [x[1] for x in e2["log"] if x[0] in ("POST", "GET")] == ["/api/turns/save", "/api/final/edited"], e2["log"]
    assert e2["state"] == "已改，等重做"
    o = got["舊檔模式"]
    assert "錯誤" not in o, o
    assert "重做這幾張" not in o["before"]["lower"]
    assert "全部看完了" in o["before"]["status"] and "1 張要重做" in o["after"]           # 按「改」之後這次畫面記著
    assert "先照舊的紀錄顯示" not in o["before"]["status"] and "先照舊的紀錄顯示" in o["dev"]   # 只在開發者設定顯示
    lk = got["連帶與沒寫原因"]
    assert "right" in lk, lk
    assert "要重做（沒寫原因）" in lk["right"] and f"跟 #{lk['n']} 共用，那張還是要重做" in lk["right"] and "連帶" in lk["right"]



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
        todo: fcSplitLists([{"id": "a", "類型": "名字", "狀態": "還沒生成", "這一版沒處理到": true}, {"id": "b", "類型": "名字", "狀態": "還沒看"}]).todo.map((c) => c.id),
        judge: [fcCanJudge({"狀態": "還沒看"}), fcCanJudge({"狀態": "還沒看", "跨版本": true}), fcCanJudge({"狀態": "通過", "這一版沒處理到": true}), fcCanJudge(null)] }));""")
    assert got["text"] == ["第 3 版　10/15 14:20（最新）", "第 2 版　10/15 10:05", "第 2 版　10/15 10:05　0-3"]
    # 10-15 宇軒驗證回饋：只有一版也出現選單（只有一項）；一版都沒有才不出現（以前只有一版是空字串）
    assert "看哪一版" in got["one"] and got["one"].count("<option") == 1 and got["none"] == ""
    assert "看哪一版" in got["menu"] and 'value="2" selected' in got["menu"] and "以這一版為基礎再組裝" not in got["menu"]
    assert "0-97" not in got["menu"] and "0-97" in got["dev"]   # 範圍標記只有開發者看得到
    assert "以這一版為基礎再組裝" in got["dev"] and "以這一版為基礎再組裝" not in got["devNew"]
    # 10-15 宇軒驗證回饋三：「這一版沒處理到」改標＋小字；之前沒通過過的（舊版畫面）標「這一版沒做這張」
    # 10-15 宇軒驗證回饋四：改成「已通過（沿用上一版）」、跟「通過 ✓」同一個綠色系（cls ok）
    assert got["lab"] == got["labPass"] == {"text": "已通過（沿用上一版）", "cls": "ok", "small": ["這版沒改到這裡，不用再看"]}
    assert got["labNone"] == {"text": "這一版沒做這張", "cls": "made", "small": ["這一版的成品沒有處理這一張"]}
    assert got["prev"] == {"video": None, "audio": "/api/final/vclip?版本=2&start=68.00&end=77.00"} and got["noPrev"] is None
    assert got["crossNow"]["audio"].startswith("/api/final/vclip?版本=1&") and got["crossNow"]["video"] is None
    assert got["crossOrig"]["video"] is None and got["crossOrig"]["audio"].startswith("/api/audio?")
    assert got["todo"] == ["b"]                                # 已通過（沿用上一版）的不在「還要確認的」
    assert got["judge"] == [True, True, False, False]          # 10-15 宇軒驗證回饋六：fcCanReturn 拿掉（要修改所有卡都能按）
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
          const lowerT = document.getElementById("fc-lower").innerHTML; fcSetStatusFilter("已通過"); const lowerP = document.getElementById("fc-lower").innerHTML; fcSetStatusFilter("還沒看");
          H.listeners.length = 0; H.log.length = 0; fcSelect(t5.id, false);
          const right = document.getElementById("fc-right").innerHTML;
          await last("fc-fix");   // 10-15 宇軒驗證回饋六：跨版本卡「要修改」也能按、打得開
          const opened = fc.fix.open && !!document.getElementById("fc-fixbox");
          fcFixReset(); fcRenderRight(); H.log.length = 0;
          await last("fc-pass");
          return {top: contentEl.innerHTML, lower: lowerT, lowerP, right, opened, log: H.log};"""},
        {"名稱": "範圍外", "程式": """
          await renderFinal();
          const n1 = DATA["卡片"].find((c) => c["第3步鍵"] === "名字:1");
          H.listeners.length = 0; H.log.length = 0; fcSelect(n1.id, false);
          const before = document.getElementById("fc-right").innerHTML;
          await last("fc-fix"); const right = document.getElementById("fc-right").innerHTML; const o = opts();
          await pick("聲音不像、不自然"); const picked = document.getElementById("fc-right").innerHTML;
          document.getElementById("fc-fix-extra").value = "太快"; await last("fc-fix-extra", "input");
          await last("fc-fix-go");
          return {id: n1.id, before, right, opts: o, picked, log: H.log};"""},
        {"名稱": "換版", "程式": """
          await renderFinal(); H.log.length = 0;
          const sel = document.getElementById("fc-ver"); sel.value = "1";
          await H.fireAll((l) => l.el.id === "fc-ver" && l.type === "change");
          return {ver: fc.ver, log: H.log};"""},
    ], data=latest)
    v = got["最新"]
    assert "錯誤" not in v, v
    assert "看哪一版" in v["top"] and "第 2 版" in v["top"] and "（最新）" in v["top"]
    # 10-15 宇軒驗證回饋六：上面「還要確認的」含跨版本卡（時間格下小字「第 1 版」）、下面「已通過的」；不再有「上一版還沒看的」那一區
    # 1025-3：同一個清單照「還沒看／要重做／已通過」篩，lower＝還沒看那一格、lowerP＝已通過那一格
    assert "上一版還沒看的" not in v["lower"]
    top_part, low_part = v["lower"], v["lowerP"]
    assert 'class="fc-vtag">第 1 版</small>' in top_part and "fc-vtag" not in low_part
    assert "已通過（沿用上一版）" in low_part and "已通過（沿用上一版）" not in top_part and "這一版沒處理到" not in v["lower"] + v["lowerP"]
    # 10-15 宇軒驗證回饋四／五：「已通過（沿用上一版）」的列 done＋綠色標籤 fc-tag-ok；文字刷淡用 fc-faded-ok（標籤不淡），不是整列 fc-faded
    row = re.search(r'<li data-id="[^"]*" class="([^"]*)">(?:(?!</li>).)*已通過（沿用上一版）', v["lowerP"], re.S)
    assert row and "done" in row.group(1) and "fc-faded-ok" in row.group(1) and " fc-faded " not in row.group(1) + " "
    assert re.search(r'fc-tag-ok"[^>]*>已通過（沿用上一版）', v["lowerP"])
    # 最新版上「已通過（沿用上一版）」的卡：「要修改」可以按、通過不行；確定 → 記原因（退回＋選項）
    o = got["範圍外"]
    assert "錯誤" not in o, o
    assert re.search(r'id="fc-pass"[^>]*disabled', o["before"]) and not re.search(r'id="fc-fix"[^>]*disabled', o["before"])
    assert "哪裡出問題" in o["right"] and "聲音不像、不自然" in o["opts"] and "這一張還沒做進成品" not in o["picked"]
    assert [x for x in o["log"] if x[0] == "POST"] == [["POST", "/api/final/card", {"id": o["id"], "結果": "退回", "選項": ["聲音不像、不自然"], "補充": "太快"}]]
    assert "現在 ▶" in v["right"]
    assert not re.search(r'id="fc-fix"[^>]*disabled', v["right"]) and v["opened"] and "回到最新一版" not in v["right"]
    posts = [x for x in v["log"] if x[0] == "POST"]
    assert posts[0][1] == "/api/final/card" and posts[0][2]["版本"] == 1   # 上一版那一區按的：記在第 1 版
    assert got["換版"]["ver"] == 1 and any(x[0] == "GET" and x[1] == "/api/final?版本=1" for x in got["換版"]["log"])
    old = _version_data(old=True)
    got = _run_page([{"名稱": "舊版", "程式": """
          await renderFinal();
          const t5 = DATA["卡片"].find((c) => c["第3步鍵"] === "學員段落:T005");
          H.listeners.length = 0; H.log.length = 0; fcSelect(t5.id, false);
          const right = document.getElementById("fc-right").innerHTML;
          await last("fc-pass");
          const log = H.log.slice(); H.log.length = 0;
          await fcExport();
          return {right, lower: document.getElementById("fc-lower").innerHTML + document.getElementById("fc-actbar").innerHTML, status: document.getElementById("fc-status").innerHTML, video: document.getElementById("fc-video").getAttribute("src"),
                  log, exp: H.log};"""}], data=old)
    o = got["舊版"]
    assert "錯誤" not in o, o
    # 10-16 宇軒：重做這幾張一直在右上角，看舊版時變灰按不下去
    assert "＋在這裡新增修改" not in o["lower"] and "fc-redoall" not in o["lower"]
    assert not re.search(r'id="fc-fix"[^>]*disabled', o["right"]) and "回到最新一版" not in o["right"]   # 10-15 宇軒驗證回饋六：舊版也能按
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
    # 1025-3（#294）：分頁改成「依類型」下拉（記住的是已拿掉的分頁、或「第 3 步選定不修改」「需留意」→ 回到全部），上面一排是還沒看／要重做／已通過／需留意
    sc = """%s LS.store["fc-tab"] = TAB;
          await renderFinal();
          const lower = document.getElementById("fc-lower").innerHTML;
          const all = {};
          for (const k of FC_SF) { fcSetStatusFilter(k); all[k] = document.getElementById("fc-lower").innerHTML; }
          return {lower, all, err: H.log.filter((x) => x[0] === "錯誤")};"""
    for name, data in (("最新", latest), ("舊版", old), ("第3版樣子", v3), ("缺欄位", bare), ("怪欄位", weird), ("有不修改需留意", base)):
        got = _run_page([{"名稱": t, "程式": f"const TAB = {json.dumps(t, ensure_ascii=False)};" + sc % _LS.replace("\n", " ")}
                         for t in tabs], data=data)
        for t in tabs:
            v = got[t]
            assert isinstance(v, dict) and "錯誤" not in v and not v["err"], (name, t, v)
            assert 'class="rv-filters fc-tabs fc-sf"' in v["lower"], (name, t)
            assert 'data-tab="還沒看完"' not in v["lower"], (name, t)
            if t in ("還沒看完", "不修改", "需留意", "不存在的分頁"):
                assert re.search(r'<option value="全部" selected', v["lower"]), (name, t, v["lower"][:400])
            for k, html in v["all"].items():
                assert f'class="on" data-sf="{k}"' in html or k == "需留意" and not data.get("需留意") and not data.get("不修改"), (name, t, k)
    keep = _run_page([{"名稱": "k", "程式": "const TAB = '全部';" + sc % _LS.replace("\n", " ")}], data=base)["k"]
    if base.get("不修改"):
        assert 'id="fc-ulist"' in keep["all"]["需留意"]


# ---------- 10-15 宇軒驗證回饋六（1015-6）：所有卡都能按「要修改」、兩個清單互換、上一張／下一張走兩個清單 ----------

def _shape_6() -> dict:
    """照 8799 第 3 版的樣子：這一版自己做的卡 0 張、「已通過（沿用上一版）」10 張、跨版本（第 2 版還沒看）40 張。"""
    latest = _version_data()
    out = next(c for c in latest["卡片"] if c.get("這一版沒處理到"))
    cx = next(c for c in latest["跨版本卡片"] if c.get("第3步鍵") == "學員段落:T005")
    d = json.loads(json.dumps(latest))
    d["卡片"] = [{**out, "id": f"P{i:02d}", "編號": 200 + i, "狀態": "還沒生成", "這一版沒處理到": True, "上一版已通過": True,
                 "原片": [10.0 * i + 5, 10.0 * i + 6], "成品": None, "版本號": 3} for i in range(10)]
    d["跨版本卡片"] = [{**cx, "id": f"X{i:02d}", "編號": 300 + i, "狀態": "還沒看", "跨版本": True, "版本號": 2, "可重新生成": True,
                     "原片": [10.0 * i + 1, 10.0 * i + 2], "成品": [500.0 + i, 501.0 + i]} for i in range(40)]
    d["卡片統計"] = {"全部": 0, "還沒看": 0, "要重做": 0, "退回": 0, "已改": 0, "通過": 0}
    d["目前版本"], d["最新版本"] = 3, 3
    return d


def test_feedback6_fix_lists_and_nav():
    if not NODE:
        print("（沒有 node，略過）")
        return
    d = _shape_6()
    got = _run_page([
        {"名稱": "畫面", "程式": """
          await renderFinal();
          const lower = document.getElementById("fc-lower").innerHTML; fcSetStatusFilter("已通過"); const lowerP = document.getElementById("fc-lower").innerHTML; fcSetStatusFilter("還沒看");
          const rights = [];
          for (const id of ["X00", "X39", "P00", "P09"]) { fcSelect(id, false); rights.push(document.getElementById("fc-right").innerHTML); }
          return {lower, lowerP, rights, top: contentEl.innerHTML};"""},
        {"名稱": "跨版本要修改", "程式": """
          await renderFinal(); H.listeners.length = 0; H.log.length = 0; fcSelect("X05", false);
          await last("fc-fix"); await pick("聲音不像、不自然");
          const picked = document.getElementById("fc-right").innerHTML;
          await last("fc-fix-go");
          return {picked, log: posts(), open: fc.fix.open, err: H.log.filter((x) => x[0] === "alert")};"""},
        {"名稱": "沿用卡要修改", "程式": """
          await renderFinal(); H.listeners.length = 0; H.log.length = 0; fcSelect("P03", false);
          await last("fc-fix"); await pick("聲音不像、不自然"); await last("fc-fix-go");
          return {log: posts()};"""},
        {"名稱": "上一張下一張", "程式": """
          await renderFinal();
          const nav = fcNavList().map((c) => c.id);
          const v = document.getElementById("fc-video"); v.currentTime = 123;
          fc.cur = "X39"; fcMarkRow(); fcStep(1); const a = fc.cur, aSeek = v.currentTime;
          const curRows = [...document.querySelectorAll("li.cur")].map((li) => li.dataset.id);
          fcStep(-1); const b = fc.cur, bSeek = v.currentTime;
          const curRows2 = [...document.querySelectorAll("li.cur")].map((li) => li.dataset.id);
          fc.cur = nav[0]; fcStep(-1); const c = fc.cur;
          fc.cur = nav[nav.length - 1]; fcStep(1); const e = fc.cur;
          return {nav, a, b, aSeek, bSeek, curRows, curRows2, c, e};"""},
    ], data=d)
    v = got["畫面"]
    assert "錯誤" not in v, v
    # 1. 最新版上跨版本卡、沿用卡的「要修改」都能按；畫面上找不到「回到最新一版」
    for r in v["rights"]:
        assert 'id="fc-fix"' in r and not re.search(r'id="fc-fix"[^>]*disabled', r), r[:400]
    assert "回到最新一版" not in v["top"] and "回到最新一版" not in v["lower"]
    # 2. 上面「還要確認的」含 40 張跨版本（小字「第 2 版」）、下面「已通過的」含那 10 張；分頁沒有「還沒看完」
    top_part, low_part = v["lower"], v["lowerP"]
    top_ids = re.findall(r'<li data-id="([^"]+)"', top_part)
    low_ids = re.findall(r'<li data-id="([^"]+)"', low_part)
    assert top_ids == [f"X{i:02d}" for i in range(40)] and low_ids == [f"P{i:02d}" for i in range(10)]
    assert top_part.count('class="fc-vtag">第 2 版</small>') == 40 and "fc-vtag" not in low_part
    assert 'data-tab="還沒看完"' not in v["lower"] and "這一頁都看完了" not in v["lower"]
    assert re.search(r'data-sf="還沒看">還沒看 <span>40</span>', v["lower"]) and re.search(r'data-sf="已通過">已通過 <span>10</span>', v["lower"])   # 含跨版本卡
    assert "全部（50）" in v["lower"]
    # 3. 跨版本卡按「要修改」→ 聲音不像 → 確定：退回＋選項＋版本 2（後端記在卡片的等重做，見 tests/test_versions.py）
    x = got["跨版本要修改"]
    assert "錯誤" not in x, x
    assert "這一張還沒做進成品" not in x["picked"] and not x["err"]
    assert x["log"] == [["POST", "/api/final/card", {"id": "X05", "結果": "退回", "選項": ["聲音不像、不自然"], "補充": "", "版本": 2}]], x["log"]
    assert x["open"] is False
    # 沿用卡（這一版沒處理到）：照回饋四那條路，不帶版本
    assert got["沿用卡要修改"]["log"] == [["POST", "/api/final/card", {"id": "P03", "結果": "退回", "選項": ["聲音不像、不自然"], "補充": ""}]]
    # 4. 上一張／下一張：上面最後一張 → 下面第一張；再上一張回上面最後一張（跨版本卡，影片不跳）；到頭到尾停住；黃底兩個清單都標
    n = got["上一張下一張"]
    assert "錯誤" not in n, n
    assert n["nav"] == [f"X{i:02d}" for i in range(40)] + [f"P{i:02d}" for i in range(10)]
    assert n["a"] == "P00" and n["curRows"] == ["P00"]
    assert n["b"] == "X39" and n["bSeek"] == 123 and n["curRows2"] == ["X39"]   # 黃底只有一張（假瀏覽器分不出在哪個清單，靠 id）
    assert n["c"] == "X00" and n["e"] == "P09"
    # 舊版：同樣兩個清單、「要修改」能按，送出帶那一版的版本號
    old = _version_data(old=True)
    for c in old["卡片"]:
        c["可重新生成"] = True
    o = _run_page([{"名稱": "舊版", "程式": """
          await renderFinal(); const lower = document.getElementById("fc-lower").innerHTML;
          const t5 = DATA["卡片"].find((c) => c["第3步鍵"] === "學員段落:T005");
          H.listeners.length = 0; H.log.length = 0; fcSelect(t5.id, false);
          const right = document.getElementById("fc-right").innerHTML;
          await last("fc-fix"); await pick("聲音不像、不自然"); await last("fc-fix-go");
          return {lower, right, id: t5.id, log: posts()};"""}], data=old)["舊版"]
    assert "錯誤" not in o, o
    assert 'data-sf="還沒看"' in o["lower"] and 'data-tab="還沒看完"' not in o["lower"] and "fc-vtag" not in o["lower"]
    assert not re.search(r'id="fc-fix"[^>]*disabled', o["right"]) and "回到最新一版" not in o["right"]
    assert o["log"] == [["POST", "/api/final/card", {"id": o["id"], "結果": "退回", "選項": ["聲音不像、不自然"], "補充": "", "版本": 1}]], o["log"]


def test_feedback6_done_line_and_gone_tab():
    """「這一頁看完了」：上面清單空、下面有東西時顯示；記住的分頁是拿掉的「還沒看完」→ 回到全部；「只看要仔細聽的」只篩上面。"""
    if not NODE:
        return
    d = _shape_6()
    d["跨版本卡片"] = d["跨版本卡片"][:3]
    d["跨版本卡片"][1]["要人聽"] = True
    sc = """%s LS.store["fc-tab"] = "還沒看完"; LS.store["fc-only-look"] = ONLY;
          await renderFinal(); const lower = document.getElementById("fc-lower").innerHTML; fcSetStatusFilter("已通過"); const lowerP = document.getElementById("fc-lower").innerHTML; fcSetStatusFilter("還沒看");
          return {lower, lowerP, lower2: document.getElementById("fc-lower").innerHTML, tab: fcTab()};"""
    got = _run_page([{"名稱": "篩", "程式": "const ONLY = '1';" + sc % _LS.replace("\n", " ")}], data=d)["篩"]
    assert got["tab"] == "全部" and "只看要仔細聽的（1 張）" in got["lower"]
    top_part, low_part = got["lower"], got["lowerP"]
    assert re.findall(r'<li data-id="([^"]+)"', top_part) == ["X01"] and len(re.findall(r'<li data-id=', low_part)) == 10
    d["跨版本卡片"] = []
    got = _run_page([{"名稱": "完", "程式": "const ONLY = '0';" + sc % _LS.replace("\n", " ")}], data=d)["完"]
    assert "這一頁都看完了" in got["lower2"] and 'data-sf="已通過"' in got["lower2"] and "fc-passed" in got["lowerP"]


# ---------- 10-15 總檢查修正（1015-7）：頂端張數、預設黃底、只看要仔細聽的、取消要重做、刪掉的卡、舊版小字、退路 ----------

def test_1015_7_pure():
    if not NODE:
        return
    got = _node(_PURE + _fn(APPJS, "fmtRange") + _fn(APPJS, "execRedoRows") + _fn(APPJS, "execRangeText") + """
      const cross = Array.from({length: 40}, (_, i) => ({"id": "X" + i, "狀態": "還沒看", "跨版本": true, "原片": [i, i + 1]}));
      const out = Array.from({length: 10}, (_, i) => ({"id": "P" + i, "狀態": "還沒生成", "這一版沒處理到": true, "上一版已通過": true}));
      const z = {"全部": 0, "還沒看": 0, "要重做": 0, "退回": 0, "已改": 0, "通過": 0};
      const red = {"選項": ["聲音不像、不自然"], "補充": "太快"};
      console.log(JSON.stringify({
        // B3：後端還沒給「還要確認」→ 照兩個清單算（含跨版本卡）；有就用後端的
        left: [fcLeftCount({"卡片": out, "跨版本卡片": cross, "卡片統計": z}), fcLeftCount({"卡片統計": {"還要確認": 7}, "卡片": out})],
        st: [fcStatusLine({"卡片": out, "跨版本卡片": cross, "卡片統計": z}).text,
             fcStatusLine({"看的是舊版": true, "卡片": [{"狀態": "通過", "最新版要重做": red}, {"狀態": "退回"}, {"狀態": "還沒看"}],
                           "卡片統計": {"要重做": 2, "還要確認": 2}, "AI執行中": true}).text,   // 審查建議 6：張數用後端的 要重做
             fcStatusLine({"看的是舊版": true, "卡片": [{"狀態": "還沒看"}], "卡片統計": {"還要確認": 1}}).text],
        passed: [fcIsPassed({"狀態": "還沒看", "已通過的": true}), fcIsPassed({"狀態": "通過", "已通過的": false}), fcIsPassed({"狀態": "通過"})],
        // B8：照時間找不到 → 往後退到上面清單最後一張
        pick: [fcStepPick([{"id": "a", "跨版本": true}, {"id": "b", "跨版本": true}, {"id": "c", "成品": [1, 2]}, {"id": "d", "成品": [3, 4]}], "zz", 99, 1, 2),
               fcStepPick([{"id": "a"}, {"id": "b"}], "zz", 99, 1)],
        // B4：刪掉／併入的卡不能按「要修改」；取消要重做給哪幾種卡
        fix: [fcCanFix({"不能修改": true, "刪掉了": false}), fcCanFix({"不能修改": false, "刪掉了": true}), fcCanFix({"刪掉了": true}), fcCanFix({}),
              fcFixOff({"不能修改": true, "不能修改原因": "這張卡已併到 #3"}), fcFixOff({"刪掉了": true}), fcFixOff(null)],
        undo: [fcCanUndo({"狀態": "退回"}, false), fcCanUndo({"狀態": "已改，等重做", "範圍外改了": true, "退回原因": red}, false),
               fcCanUndo({"狀態": "已改，等重做", "範圍外改了": true, "退回原因": red}, true), fcCanUndo({"狀態": "已改，等重做", "退回原因": red}, false),
               fcCanUndo({"狀態": "已改，等重做", "範圍外改了": true}, false), fcCanUndo({"狀態": "退回", "不能修改": true}, false)],
        // B5：舊版小字、預填
        latest: [fcLatestRedoLine({"最新版要重做": red}), fcLatestRedoLine({"最新版要重做": {"選項": [], "補充": ""}}), fcLatestRedoLine({})],
        pre: fcFixPrefill({"狀態": "通過", "最新版要重做": red}, FC_FIX_OPTS),
        // B7：第 4 步清單（新欄位 要重做；沒有時讀舊的 退回清單）
        rows: [execRedoRows({"要重做": [{"卡號": "K0012", "類型": "學員段落", "原片": [61, 65], "原因": red}], "退回清單": [{"類型": "x"}, {"類型": "y"}]}),
               execRedoRows({"退回清單": [{"類型": "名字", "覆核名稱": ["名字 1"], "原因": "念錯字", "說明": "重新生成"}]}), execRedoRows({})]}));""")
    assert got["left"] == [40, 7]
    assert got["st"] == ["還有 40 張沒看", "2 張要重做", "還有 1 張沒看"], got["st"]
    assert got["passed"] == [True, False, True]
    assert got["pick"] == ["b", "b"]
    assert got["fix"] == [False, True, False, True, "這張卡已併到 #3", "這張卡已刪掉，沒有可以重做的", "先選一張卡"]
    assert got["undo"] == [True, True, False, False, False, False]
    assert got["latest"] == ["最新版：要重做（聲音不像、不自然：太快）", "最新版：要重做", ""]
    assert got["pre"] == {"pick": "聲音不像、不自然", "extra": "太快"}
    new_rows, old_rows, none = got["rows"]
    assert new_rows == [{"kind": "學員段落", "name": "#12", "when": "原片 1:01–1:05", "why": "聲音不像、不自然：太快", "range": "", "how": ""}]
    assert old_rows[0]["name"] == "名字 1" and old_rows[0]["why"] == "念錯字" and old_rows[0]["how"] == "重新生成" and none == []


def test_1015_7_page():
    if not NODE:
        return
    d = _shape_6()
    red = {"選項": ["聲音不像、不自然"], "補充": ""}
    # 已通過的、可取消要重做＝後端 finalcards.mark_flags 會給的值（等重做有原因 → 已通過的 false、可取消要重做 true；
    # 複製來的卡原本是 true／false）
    d["卡片"][3].update({"狀態": "已改，等重做", "這一版沒處理到": False, "上一版已通過": None, "範圍外改了": True, "退回原因": red,
                       "已通過的": False, "可取消要重做": True})
    d["卡片"][4].update({"不能修改": True, "不能修改原因": "這張卡已刪掉，沒有可以重做的"})
    got = _run_page([
        {"名稱": "一開啟", "程式": """
          await renderFinal();
          return {cur: fc.cur, top: document.getElementById("fc-status").innerHTML};"""},
        {"名稱": "只看要仔細聽的", "程式": _LS.replace("\n", " ") + """ LS.store["fc-only-look"] = "1";
          await renderFinal(); return {lower: document.getElementById("fc-lower").innerHTML};"""},
        {"名稱": "取消要重做", "程式": """
          await renderFinal(); H.listeners.length = 0; H.log.length = 0; fcSelect("P03", false);
          const right = document.getElementById("fc-right").innerHTML;
          await last("fc-back-undo");
          return {right, log: posts()};"""},
        {"名稱": "刪掉的卡", "程式": """
          await renderFinal(); fcSelect("P04", false); return {right: document.getElementById("fc-right").innerHTML};"""},
        {"名稱": "退路", "程式": """
          await renderFinal(); const v = document.getElementById("fc-video"); v.currentTime = 99999;
          fc.cur = "不在清單"; fcStep(1); return {cur: fc.cur, todo: fcLists().todo.map((c) => c.id)};"""},
    ], data=d)
    a = got["一開啟"]
    assert "錯誤" not in a, a
    # B2：預設黃底＝上面清單第一張（X00 原片 1 秒；P03 已改等重做排在 X03 之後）；B3：頂端算進 40 張跨版本卡＋1 張要重做
    assert a["cur"] == "X00", a["cur"]
    assert "還有 41 張沒看" in a["top"], a["top"]
    # B1：「只看要仔細聽的」開著、一張要仔細聽的都沒有：勾選框照樣在、寫一句
    b = got["只看要仔細聽的"]
    assert "錯誤" not in b, b
    assert re.search(r'id="fc-only" checked', b["lower"]) and "只看要仔細聽的（0 張）" in b["lower"]
    assert "沒有要仔細聽的卡片（取消勾選就看得到全部）" in b["lower"]
    u = got["取消要重做"]
    assert "錯誤" not in u, u
    assert 'id="fc-back-undo"' in u["right"] and "取消要重做" in u["right"]
    # B4：等重做那條路的卡取消要重做：結果 null、不帶版本（後端清掉等重做）
    assert u["log"] == [["POST", "/api/final/card", {"id": "P03", "結果": None, "選項": [], "補充": "", "取消要重做": True}]], u["log"]
    r = got["刪掉的卡"]["right"]
    assert re.search(r'id="fc-fix"[^>]*disabled title="這張卡已刪掉，沒有可以重做的"', r), r[:800]
    # B8：影片在所有卡之後、目前這張不在清單：往後退到上面清單最後一張（以前是下面清單最後一張 P09）
    t = got["退路"]
    assert t["cur"] == t["todo"][-1] == "X39", t
    # 舊版：最新版要重做的卡底下小字、按「要修改」預填；頂端「有 N 張要重做」
    old = _version_data(old=True)
    t5 = next(c for c in old["卡片"] if c.get("第3步鍵") == "學員段落:T005")
    t5["可重新生成"] = True
    t5["最新版要重做"] = {"選項": ["聲音不像、不自然"], "補充": "太快"}
    old["卡片統計"]["要重做"] = 1   # 後端照 needs.redo_cards 算（有最新版要重做的卡就至少 1 張；審查建議 6 頂端用這個數）
    o = _run_page([{"名稱": "舊版", "程式": """
          await renderFinal(); const top = document.getElementById("fc-status").innerHTML;
          const t5 = DATA["卡片"].find((c) => c["第3步鍵"] === "學員段落:T005");
          H.listeners.length = 0; fcSelect(t5.id, false); const right = document.getElementById("fc-right").innerHTML;
          await last("fc-fix"); const fixed = document.getElementById("fc-right").innerHTML;
          return {top, right, fixed};"""}], data=old)["舊版"]
    assert "錯誤" not in o, o
    assert "最新版：要重做（聲音不像、不自然：太快）" in o["right"]
    assert "哪裡出問題：<b>聲音不像、不自然</b>" in o["fixed"] and 'value="太快"' in o["fixed"]
    assert re.search(r"\d+ 張要重做", o["top"]) and "到最新版，回第 4 步按「更新成品」" in o["top"], o["top"]


def test_1015_7_name_spots_pad_switch():
    """B12：第 3 步名字卡沒有 整句（沒有重念範圍那一列）時，「第 N 處」旁邊補一顆「前後多 2 秒」開關；有 整句 的、第 5 步不補（一張卡一顆）。"""
    if not NODE:
        return
    cj = (WEB / "cards.js").read_text(encoding="utf-8")
    got = _node(_ESC + "globalThis.localStorage = {getItem() { return null; }};\n" + _const(cj, "cv") + _const(cj, "CV_PAD_KEY") + _const(cj, "CV_PAD_SEC")
                + _fn(cj, "cvPadOn") + _fn(cj, "cvPadHtml") + _fn(cj, "rvFmt") + _fn(cj, "rvSpotsHtml") + """
      const spots = [{"start": 1, "end": 2}, {"start": 5, "end": 6}];
      cv.ctx = {mode: "第3步"};
      const a = rvSpotsHtml({"這一句的處": spots}), b = rvSpotsHtml({"這一句的處": spots, "整句": {"start": 0, "end": 7}});
      cv.ctx = {mode: "第5步"};
      const c = rvSpotsHtml({"這一句的處": spots});
      console.log(JSON.stringify([a, b, c]));""")
    a, b, c = got
    assert 'id="rv-pad"' in a and "第 2 處" in a
    assert 'id="rv-pad"' not in b and 'id="rv-pad"' not in c


def test_original_text_survives_decide_with_real_backend_reply():
    """10-15 修正：按「通過」「取消通過」「取消要重做」後，畫面用後端回傳的卡（finalcards.decide 真的回傳，沒有 `有第3步卡片`）
    換掉舊卡——「原本：」照樣顯示原句、不出現「（找不到原句）」；「取消要重做」送 `取消要重做: true`。"""
    if not NODE:
        return
    import test_needs as tn

    from bookclub import finalcards, needs

    data, w = _api_final()
    by = {c.get("第3步鍵"): c for c in data["卡片"]}
    t3, n1 = by["學員段落:T003"], by["名字:1"]
    assert t3["狀態"] == needs.RETURNED

    def real(card_id, result, **kw):
        r = tn._quiet(finalcards.decide, w, card_id, result, **kw)
        return json.loads(json.dumps(r, ensure_ascii=False, default=str))

    undo = real(t3["id"], None, cancel_redo=True)   # 真的後端：取消要重做（旗標不擋合法的退回卡）
    ok = real(n1["id"], needs.PASSED)
    back = real(n1["id"], None)
    for r in (undo, ok, back):
        assert r["ok"] and r["卡片"] and "有第3步卡片" not in r["卡片"], r
    assert undo["卡片"]["狀態"] == needs.NOT_SEEN and ok["卡片"]["狀態"] == needs.PASSED and back["卡片"]["狀態"] == needs.NOT_SEEN
    try:   # 已經不是要重做了：再送一次取消要重做要擋（不會變成取消通過）
        tn._quiet(finalcards.decide, w, n1["id"], None, cancel_redo=True)
        raise AssertionError("沒有要重做的不能取消要重做")
    except ValueError as e:
        assert "沒有要重做" in str(e)
    # 假工作區的卡沒有要念的字（畫面只出一行）：整頁資料與回傳的卡都補一樣的要念的字，才會出「原本：」「改成：」兩行
    for c in (t3, n1, undo["卡片"], ok["卡片"], back["卡片"]):
        c["要念的字"] = ["改成的字"]
    replies = json.dumps({"undo": undo, "ok": ok, "back": back}, ensure_ascii=False)
    got = _run_page([
        {"名稱": "取消要重做", "程式": f"const R = {replies};" + """
          H.api['/api/final/card'] = R.undo; await open("學員段落:T003"); const before = document.getElementById("fc-right").innerHTML;
          await last("fc-back-undo");
          return {before, after: document.getElementById("fc-right").innerHTML, log: posts(),
                  has: "有第3步卡片" in fcCards().find((c) => c["第3步鍵"] === "學員段落:T003"),
                  state: fcCards().find((c) => c["第3步鍵"] === "學員段落:T003")["狀態"]};"""},
        {"名稱": "通過再取消", "程式": f"const R = {replies};" + """
          H.api['/api/final/card'] = R.ok; await open("名字:1"); await last("fc-pass");
          const a = document.getElementById("fc-right").innerHTML;
          H.api['/api/final/card'] = R.back; await last("fc-pass");
          return {a, b: document.getElementById("fc-right").innerHTML, log: posts()};"""},
    ], data=data)
    u = got["取消要重做"]
    assert "錯誤" not in u, u
    assert u["has"] is False and u["state"] == "還沒看"   # 換成後端回傳的卡了
    assert u["log"] == [["POST", "/api/final/card", {"id": t3["id"], "結果": None, "選項": [], "補充": "", "取消要重做": True}]], u["log"]
    assert "原本：" in u["before"] and "找不到原句" not in u["before"]
    assert "原本：" in u["after"] and "找不到原句" not in u["after"], u["after"][:800]
    assert re.search(r"原本：</span>([^<]+)<", u["before"]).group(1) == re.search(r"原本：</span>([^<]+)<", u["after"]).group(1)
    p = got["通過再取消"]
    assert "錯誤" not in p, p
    assert "✓ 已通過（再按取消）" in p["a"] and ">通過<kbd>" in p["b"]
    assert [x[2]["結果"] for x in p["log"]] == ["通過", None] and all("取消要重做" not in x[2] for x in p["log"])
    for k in ("a", "b"):
        assert "原本：" in p[k] and "找不到原句" not in p[k], p[k][:800]


def test_attention_head_text():
    """10-15 宇軒定：第 5 步「⚠️ 需留意」最上面的說明改短；紅字那句保留。"""
    if not NODE:
        return
    got = _node(_ESC + _fn(FCJS, "fcAttentionHtml") + """
      console.log(JSON.stringify(fcAttentionHtml({"有紀錄": true, "列": []}, [], [])));""")
    assert "目前成品裡還留著原聲的部分，不會擋輸出，但請多留意此部分；想確認就按「跳過去聽」，要改就「回第 3 步改」，改完要重新組裝。" in got
    assert "這一頁會顯示學員本名，截圖或分享螢幕時請注意。" in got
    assert "開始前總檢查" not in got and "『" not in got


def test_1025_3_menu_state_card_and_filters():
    """1025-3（#294）：右上「⋯」選單（開、點外面關、Esc 關、輸出成品走原本的確認流程）、狀態卡三種長相、篩選列、卡片頂端換張。"""
    if not NODE:
        return
    d = json.loads(json.dumps(_api_final()[0]))
    allok = json.loads(json.dumps(d))
    for c in allok["卡片"]:
        c["狀態"], c["已通過的"] = "通過", True
    allok["跨版本卡片"] = []
    allok["卡片統計"] = {"還沒看": 0, "要重做": 0, "還要確認": 0}
    allok["需留意"] = {"列": []}
    todo = json.loads(json.dumps(allok))
    todo["卡片"][0]["狀態"], todo["卡片"][0]["已通過的"] = "還沒看", False
    todo["卡片統計"] = {"還沒看": 1, "要重做": 0, "還要確認": 1}
    menu = """
          await renderFinal();
          const m = () => document.getElementById("fc-menu");
          const out = {start: m().hidden, expanded0: document.getElementById("fc-more").attrs && 0};
          await H.fireAll((l) => l.el.id === "fc-more");
          out.opened = m().hidden;
          const clicks = H.docListeners.filter((l) => l.type === "click"), keys = H.docListeners.filter((l) => l.type === "keydown");
          clicks.forEach((l) => l.fn({target: document.getElementById("fc-now")}));
          out.outside = m().hidden;
          await H.fireAll((l) => l.el.id === "fc-more");
          const reopened = m().hidden;
          keys.forEach((l) => l.fn({key: "Escape", target: {tagName: "BODY"}, stopPropagation() {}, preventDefault() {}}));
          out.esc = [reopened, m().hidden];
          await H.fireAll((l) => l.el.id === "fc-more"); H.log.length = 0;
          await H.fireAll((l) => l.el.id === "fc-menu-export");
          out.exp = H.log.filter((x) => x[0] === "POST").map((x) => x[1]); out.closedAfter = m().hidden;
          return out;"""
    got = _run_page([{"名稱": "選單", "程式": menu},
                     {"名稱": "全過", "程式": 'await renderFinal(); return {st: document.getElementById("fc-status").innerHTML, bar: document.getElementById("fc-lower").innerHTML};'}], data=allok)
    m = got["選單"]
    assert "錯誤" not in m, m
    assert m["start"] is True and m["opened"] is False and m["outside"] is True and m["esc"] == [False, True], m
    assert m["exp"] == ["/api/final/export"] and m["closedAfter"] is True, m
    a = got["全過"]
    assert "全部看完了" in a["st"] and 'id="fc-export"' in a["st"] and "輸出成品" in a["st"] and "fc-go4" not in a["st"]
    assert 'data-sf="已通過">已通過' in a["bar"] and 'class="on" data-sf="已通過"' in a["bar"]   # 沒有還沒看的：第一次畫停在已通過
    t = _run_page([{"名稱": "沒看完", "程式": 'await renderFinal(); return {st: document.getElementById("fc-status").innerHTML, right: document.getElementById("fc-right").innerHTML};'}], data=todo)["沒看完"]
    assert "還有 1 張沒看" in t["st"] and "fc-export" not in t["st"] and "fc-go4" not in t["st"]    # 只有一句，不放大按鈕
    r = t["right"]   # 換張搬到卡片頂端，底部只剩通過／要修改
    assert r.index('id="fc-prev"') < r.index('id="fc-pass"') and r.index('id="fc-next"') < r.index('id="fc-pass"') and r.index("第 1／") < r.index('id="fc-pass"')
    assert r.index('id="fc-fix"') < r.index('id="fc-back-undo"') if 'id="fc-back-undo"' in r else True


def test_1025_3_menu_handoff_states_pure():
    """交接兩項：已結案、交接框讀不到、打包中（改成「停止打包」）、不能打包的原因。"""
    if not NODE:
        return
    src = FCJS[FCJS.index("const FC_MENU_HINT = {"):FCJS.index("function fcMenuHtml")] + _fn(FCJS, "fcMenuHandoff")
    got = _node(src + """
      const ok = {closed: false, hasBox: true, running: false, packWhy: "", off: false};
      console.log(JSON.stringify([fcMenuHandoff("pack", {...ok, closed: true}), fcMenuHandoff("imp", {...ok, hasBox: false}),
        fcMenuHandoff("pack", {...ok, running: true}), fcMenuHandoff("imp", {...ok, running: true}),
        fcMenuHandoff("pack", {...ok, packWhy: "還沒有成品", packOff: true}), fcMenuHandoff("imp", ok)]));""")
    assert got[0]["off"] and "已結案" in got[0]["hint"] and got[1]["off"] and "讀不到" in got[1]["hint"]
    assert got[2] == {"label": "停止打包", "hint": "正在打包；按了會停下來", "off": False} and got[3]["off"]
    assert got[4]["off"] and got[4]["hint"] == "還沒有成品" and got[5] == {"label": "匯入交接包…", "hint": "收到別人傳來的交接包時用。", "off": False}


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
