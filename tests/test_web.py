"""網頁（`bookclub/web/`）的靜態檢查：不開瀏覽器，只讀檔案確認幾個定案過的規則沒被改掉。

互動行為（收合、存檔、點按鈕）用 `tests/fake_workdir.py` 的假工作區開伺服器實際點過，見 CHANGELOG；
這支只擋「改版時不小心把定案拿掉」：步驟列每步的 AI／人工、localStorage 讀寫都包 try/catch。

獨立可跑：.venv/bin/python tests/test_web.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 10-01：這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB = REPO_ROOT / "bookclub" / "web"


def _step_defs() -> list[tuple[int, str]]:
    js = (WEB / "app.js").read_text(encoding="utf-8")
    block = js[js.index("const STEP_DEFS = ["):]
    block = block[:block.index("];")]
    return [(int(n), who) for n, who in re.findall(r'num: (\d+),.*?who: "(AI|人工)"', block)]


def test_steps_marked_ai_or_human():
    # 09-29 宇軒：0 人工、1 AI、2 人工、3 人工、4 AI、5 人工
    got = dict(_step_defs())
    want = {0: "人工", 1: "AI", 2: "人工", 3: "人工", 4: "AI", 5: "人工"}
    for n, who in want.items():
        assert got.get(n) == who, (n, got.get(n))


def test_steps_zero_to_five():
    # 09-29：原本第 5 步逐筆覆核＋第 6 步整片檢查合成第 5 步「成品檢查」
    assert [n for n, _ in _step_defs()] == [0, 1, 2, 3, 4, 5]
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert '"成品檢查"' in js and "整片檢查" not in js.split("const STEP_DEFS")[1].split("];")[0]


def test_local_storage_wrapped_in_try():
    # 私密視窗、封鎖網站資料時 localStorage 會丟例外；讀不到要當作預設值，不能讓整頁掛掉
    for name in ("app.js", "cards.js", "review.js", "finalcheck.js", "fileio.js"):
        path = WEB / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if "localStorage." in line:
                assert "try" in line, f"{name}：{line.strip()}"


def test_watched_only_counts_up_to_2x():
    # 09-29 宇軒：2 倍速以下播過的才算看過；不能改回用 video.played（它連快轉的也算）
    js = (WEB / "finalcheck.js").read_text(encoding="utf-8")
    assert "const FC_MAX_RATE = 2;" in js and "playbackRate <= FC_MAX_RATE" in js
    assert "fc.video.played" not in js and "v.played" not in js


def test_nav_toggle_exists():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'id="navToggle"' in html
    # 1022-1（#266）：三條線的圖示（內嵌 SVG），收合、展開都同一個，不再換成 « »
    i = html.index('id="navToggle"')
    assert "<svg" in html[i:i + 600] and "«" not in html and "»" not in html
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert '"»"' not in js and '"«"' not in js and 'b.setAttribute("aria-expanded"' in js



def test_step3_go4_button_and_confirm():
    # 09-29 宇軒：第 3 步全部通過才能按；確認視窗兩個按鈕「開始修改」「回去檢查」，預設焦點在回去檢查
    js = ((WEB / "review.js").read_text(encoding="utf-8") + (WEB / "cards.js").read_text(encoding="utf-8"))   # 10-15：卡片在 cards.js
    assert 'id="rv-go4" disabled' in js
    assert "開始修改" in js and "回去檢查" in js
    assert '#rv-confirm-back").focus()' in js
    assert 'location.hash = "#step4"' in js


def test_step5_no_product_one_line():
    # 10-15 第5步2.0 第2段（31 號設計 §6.8）：還沒有成品時一句「還沒有成品，請先到第 4 步」＋連結（霧化預覽拿掉）
    js = (WEB / "finalcheck.js").read_text(encoding="utf-8")
    assert "還沒有成品，請先到 <a href=\"#step4\">第 4 步</a>" in js and "fcPreviewHtml" not in js and "inert" not in js


def test_save_failure_banner():
    # 09-30：存檔失敗時畫面最上面紅色橫幅（寫清楚沒存到、請重新整理），沒接住的錯誤也要顯示
    js = (WEB / "app.js").read_text(encoding="utf-8")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'id="saveError"' in html
    assert "這次修改沒存到，請重新整理" in js
    assert 'addEventListener("unhandledrejection"' in js
    post = js[js.index("async function apiPost"):js.index("function showSaveError")]
    assert "showSaveError" in post and "catch" in post   # 連不上伺服器（fetch 丟例外）也算


def test_step3_wording():
    # 09-30：主按鈕寫動作「通過」（不是狀態「未通過」）；名字卡是「不是名字，不用改」；開始前是 4 件事
    js = ((WEB / "review.js").read_text(encoding="utf-8") + (WEB / "cards.js").read_text(encoding="utf-8"))   # 10-15：卡片在 cards.js
    assert "未通過" not in js and "✓ 已通過（再按取消）" in js
    assert "保留原聲（不用改）" not in js and "不是名字，不用改" in js
    assert "3 件事" not in js and "開始前 4 件事" in js


def test_step4_stop_and_step3_readonly():
    # 09-30：第 4 步有停止按鈕、預估剩餘時間；執行中第 3 步唯讀＋上方橫幅
    app = (WEB / "app.js").read_text(encoding="utf-8")
    rv = ((WEB / "review.js").read_text(encoding="utf-8") + (WEB / "cards.js").read_text(encoding="utf-8"))   # 10-15：卡片在 cards.js
    assert 'id="btnStop"' in app and "/api/execute/stop" in app and "execEtaText" in app
    assert 'id="rv-busy"' in rv and "rvApplyReadonly" in rv and 'rv.data["AI執行中"]' in rv


def test_pick_button_left_of_path():
    # 10-08 宇軒：總覽「選影片」按鈕放在「影片」字樣右邊、路徑左邊（以前在最右邊，版面寬就看不到）；片頭片尾同一個版型
    fio = (WEB / "fileio.js").read_text(encoding="utf-8")
    css = (WEB / "style.css").read_text(encoding="utf-8")
    for fn in ("function pkRowHtml", "const row = (p) =>"):
        i = fio.index(fn)
        body = fio[i:fio.index("</tr>`", i)]
        assert body.index('class="pk-what"') < body.index('class="pk-btns"') < body.index('class="pk-file"'), fn
    assert "td.pk-file" in css and "overflow-wrap: anywhere" in css


def test_step4_eta_hidden():
    # 10-05 宇軒：網頁第 4 步的預估時間先不顯示（後端照樣算，網頁用 SHOW_EXEC_ETA 一個開關藏起來，之後好恢復）
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "const SHOW_EXEC_ETA = false;" in app
    # 每一處預估時間的文字都包在開關裡
    for frag in ("（含組裝約 21 分鐘）", "整支約 ${Math.round", "預估剩餘時間：第一句生成完才算得出來", "預估生成還要約"):
        for line in app.splitlines():
            if frag in line:
                assert "SHOW_EXEC_ETA" in line or "if (s == null)" in line or "return `預估生成還要約" in line, line
    i = app.index("function execEtaText")
    body = app[i:app.index("\n}", i)]
    assert body.index("if (!SHOW_EXEC_ETA) return") < body.index("預估剩餘時間：")


def test_no_promises_of_unbuilt_redo():
    # 09-30：第 5 步退回、第 4 步「從這裡開始」的說明要跟實際一致（一鍵只重做還沒做好；第 2 步參考音要選好）
    fc = (WEB / "finalcheck.js").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    # 10-01 第三批：一鍵只重做做好了；畫面上不再叫人照指令手動重做、也不出現指令
    assert "手動重做" not in fc and "只重做這幾張" in app   # 10-15 總檢查修正（1015-7）：「只重做退回的這幾筆」→「只重做這幾張」
    # 10-15 第5步2.0 第2段：第 5 步只有一顆「重做這幾張」（「送回 AI 重做」拿掉）
    assert "重做這幾張" in fc and "送回 AI 重做" not in fc and "/api/final/sendback" not in fc
    assert "bookclub gen" not in app and "<code>${esc(it[\"建議指令\"])}" not in app
    assert "能省掉的是第 2、3 步的人工" not in app and 'id="btnAutoCode"' in app
    skill = (REPO_ROOT / "skills" / "bookclub-edit" / "SKILL.md").read_text(encoding="utf-8")
    assert "0.1.1" not in skill and "總覽選影片" in skill


def test_pick_with_system_dialog():
    # 10-07 宇軒：選影片改用系統內建的選檔視窗，頁面只留路徑；網頁資料夾瀏覽只在叫不起來或按取消時出現
    html = (WEB / "index.html").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    fio = (WEB / "fileio.js").read_text(encoding="utf-8")
    assert html.index('src="app.js"') < html.index('src="fileio.js"') < html.index('src="finalcheck.js"')
    ov = app[app.index("async function renderOverview"):app.index("async function renderPicker")]
    assert "pickCardHtml()" in ov and "bindPickCard(" in ov and "renderPicker(" not in ov
    assert '"用選的": true' in app and "pickBrowsed(" in app
    assert '"/api/pick"' in fio and "退回網頁" in fio and "pkShowFallback" in fio and "/api/copy/cancel" in fio
    # 10-08 宇軒：片頭、片尾搬到第 5 步輸出成品這一區（總覽不再有）；可以不選；這一版只先記錄、還不會接上（畫面照實說）
    assert "可以不選" in fio and "還不會接上" in fio and "feRenderExtras" in fio
    overview_rows = fio[fio.index("function pkRenderState"):fio.index("function feRenderExtras")]
    assert '"片頭"' not in overview_rows and '"片尾"' not in overview_rows and "extrasHtml" not in app
    init = fio[fio.index("async function fileOutInit"):]
    assert 'id="feExtras"' in init and 'id="pkFallback"' in init and 'id="picker"' in init
    # 10-08 宇軒：「先把片頭片尾的選取功能隱藏掉」——用開關關掉，程式留著
    assert "const SHOW_EXTRAS = false;" in fio
    fe = fio[fio.index("function feRenderExtras"):fio.index("function pkBind")]
    assert fe.index("if (!SHOW_EXTRAS)") < fe.index("box.innerHTML = `<h3")
    # 按下去馬上換字（10-08：宇軒覺得按了沒反應）
    assert "正在打開選檔視窗…" in fio
    # 第 5 步兩顆按鈕：finalcheck.js 只放容器＋呼叫（好合併），按鈕在 fileio.js
    fc = (WEB / "finalcheck.js").read_text(encoding="utf-8")
    assert 'id="fc-fileout"' in fc and "fileOutInit(" in fc
    assert "/api/final/reveal" in fio and "/api/final/to_windows" in fio and "複製成品到 Windows 的下載資料夾" in fio
    # 這兩個端點不送路徑
    for ep in ("/api/final/reveal", "/api/final/to_windows"):
        line = next(x for x in fio.splitlines() if ep in x)
        assert "{}" in line, line


def test_pick_button_always_released():
    # 1008-5：選檔視窗按取消（或叫不起來、出錯）之後，按鈕一定要恢復可以按、1.5 秒提示要清掉
    fio = (WEB / "fileio.js").read_text(encoding="utf-8")
    body = fio[fio.index("async function pkPick"):]
    body = body[:body.index("\n}\n")]
    after = body[body.index('await apiPost("/api/pick"'):]
    # 正常回來（含「取消」「退回網頁」）：先清提示、放開按鈕，才去處理退回網頁
    ok_part = after[after.index("} catch (e) {"):]
    ok_part = ok_part[ok_part.index("\n  }\n"):]
    assert ok_part.index("clearTimeout(later)") < ok_part.index('if (r["退回網頁"])')
    assert ok_part.index("btn.disabled = false") < ok_part.index('if (r["退回網頁"])')
    # 出錯：一樣放開按鈕
    err = after[after.index("} catch (e) {"):after.index("\n  }\n")]
    assert "clearTimeout(later)" in err and "btn.disabled = false" in err


def test_overview_no_horizontal_scroll():
    # 1008-5：按取消／Esc 退回網頁瀏覽時頁面不往右滑：只縱向捲、主內容區可以比內容窄、長路徑可以換行（不是硬藏橫向捲軸）
    fio = (WEB / "fileio.js").read_text(encoding="utf-8")
    css = (WEB / "style.css").read_text(encoding="utf-8")
    for line in fio.splitlines():
        if "scrollIntoView(" in line:
            assert 'inline: "nearest"' in line, line
    content = css[css.index(".content {"):]
    content = content[:content.index("}")]
    assert "min-width: 0" in content
    assert "overflow-wrap: anywhere" in css and "overflow-x: hidden" not in css.split("body")[1].split("}")[0]


def test_keep_all_voices_needs_confirm():
    # 09-30：「全部保留原聲」先跳確認視窗（會讓 N 位學員保留原聲、不換聲音），取消不變
    js = ((WEB / "review.js").read_text(encoding="utf-8") + (WEB / "cards.js").read_text(encoding="utf-8"))   # 10-15：卡片在 cards.js
    block = js[js.index('querySelectorAll(".rv-voice-all")'):]
    block = block[:block.index("}));")]
    # 1022-1（#263）：改成工具自己的視窗 uiConfirm
    assert "uiConfirm(" in block and "位學員保留原聲、不換聲音" in block
    assert block.index("uiConfirm(") < block.index('apiPost("/api/review/voice"')


def test_interface_1001():
    # 10-01 介面修改：網頁上不留「09-18 定：」這類內部備註；第 3、5 步共用同一套起訖編輯器；
    # 第 5 步紅線重畫時放在目前時間；總檢查每一列可以去第 3 步；重疊卡片沒人選時顯示「（請選）」
    import re as _re

    for name in ("app.js", "cards.js", "review.js", "finalcheck.js"):
        code = "\n".join(_re.sub(r"//.*$", "", ln) for ln in (WEB / name).read_text(encoding="utf-8").splitlines()
                         if not ln.lstrip().startswith(("*", "/*")))
        assert not _re.search(r"\d{2}-\d{2} ?定", code), name
    rv = ((WEB / "review.js").read_text(encoding="utf-8") + (WEB / "cards.js").read_text(encoding="utf-8"))   # 10-15：卡片在 cards.js
    fcjs = (WEB / "finalcheck.js").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "function teRender(ctx)" in rv and "teRender(rvEdCtx)" in rv and "teRender(FC_ED_CTX)" in fcjs
    assert "function rvEdSave" not in rv and "rv-ed-save" not in rv           # 舊的那一份拿掉了，只留共用的
    assert 'id="fc-head" style="left:' in fcjs and '"seeked"' in fcjs
    assert "data-fcgo" in app and "data-fcpath" in app and "rvJump(" in app and "function rvJump" in rv
    assert "（請選）" in rv and "就是這一位" in rv and "程式猜是" in rv
    assert '"primary on"' in fcjs and "已退回重做" not in fcjs   # 10-15：按過的按鈕變色，不另加字


def test_walkthrough_1001():
    """10-01 網頁走查修的幾處（只擋改版時被拿掉；行為在瀏覽器實際點過）。"""
    import shutil
    import subprocess

    app = (WEB / "app.js").read_text(encoding="utf-8")
    rv = ((WEB / "review.js").read_text(encoding="utf-8") + (WEB / "cards.js").read_text(encoding="utf-8"))   # 10-15：卡片在 cards.js
    fcjs = (WEB / "finalcheck.js").read_text(encoding="utf-8")
    assert "renderSeq" in app and "renderDone" in app                  # 很快連點兩步，不會停在慢的那一頁
    assert '["局部消音", "消音"]' in rv and '["改成老師", "改成老師"]' in rv   # 篩選加起來等於全部
    assert "fmtStamp(" in app and "fcShortStamp(" in fcjs              # 不顯示 2026-10-01T03:16:01
    assert '"剪掉（連畫面）"' in rv and "刪除點" not in fcjs            # 10-15：第 5 步卡片的類型名稱跟第 3 步同一份
    assert 'it["狀態"] === "還原" ? "不消音"' in rv
    assert "rvRuleHint(ed.kind, ed.whole, !!ed.id)" in fcjs
    assert "存逐字稿（音檔不換）" in app and "換成這個候選的新音檔" in app
    node = shutil.which("node")
    if node:   # rvFmt 不會出現「:60.0」
        fn = rv[rv.index("function rvFmt"):rv.index("function rvParseTime")]
        out = subprocess.run([node, "-e", fn + "console.log(rvFmt(3299.96, 1), rvFmt(59.97, 1), rvFmt(59.97))"],
                             capture_output=True, text=True, check=True).stdout.split()
        assert out == ["55:00.0", "1:00.0", "0:59"], out


def test_third_batch_1001():
    """10-01 第三批：畫面上的字與新按鈕（不出現專有名詞、內部編號、終端機指令）。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    rv = ((WEB / "review.js").read_text(encoding="utf-8") + (WEB / "cards.js").read_text(encoding="utf-8"))   # 10-15：卡片在 cards.js
    fcj = (WEB / "finalcheck.js").read_text(encoding="utf-8")
    assert "重新分析（做完的會跳過）" in app                                     # 10
    assert "進階設定" in app and "伺服器埠號" not in app and "<b>settings.toml</b>" not in app   # 8
    assert "硬體編碼（Mac）" not in app and "軟體編碼" not in app and "輸出做法選項" in app
    assert "<b>匿名聲線</b>" not in app and "替代聲音" in app
    assert "聲紋分出來" not in rv and "匿名聲線" not in rv and '"霧化": "聲音霧化"' in rv
    assert "execGoCheck" not in rv and "execGoCheck" in app   # 9：10-08 流程簡化後第 3 步不再帶去第 4 步總檢查（總檢查預設略過）
    assert "第 5 步要重做：" in rv                                                   # 5
    assert "現在狀態" in app and "上次執行：" in app                               # 4
    assert "調整時間範圍" not in fcj   # 11：10-15 第 5 步改範圍改用第 3 步卡片的「改時間」（面板拿掉）
    assert "已還原的（" in rv and "刪掉這一筆" in rv and "刪掉的紀錄（" in rv       # 第三批 3
    assert "會換掉的範圍" in rv and "裡面老師的聲音都不保留" in rv                   # 第三批 1
    for old in ("老師整句生成", "學員整句生成（", "老師 AI 重念：", "剪掉片段（連畫面）"):
        assert f'"{old}' not in rv and f">{old}" not in rv, old                   # 7：統一叫法


def test_fourth_batch_1002():
    """10-02 第四批：總檢查確認過的列留著（灰色、可以取消或改答案、可以收合且記得）；退回重做換一種念法、看得出第幾版。
    畫面上不出現「種子」。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    rv = ((WEB / "review.js").read_text(encoding="utf-8") + (WEB / "cards.js").read_text(encoding="utf-8"))   # 10-15：卡片在 cards.js
    fcj = (WEB / "finalcheck.js").read_text(encoding="utf-8")
    css = (WEB / "style.css").read_text(encoding="utf-8")
    assert "fc-done" in app and "tr.fc-done" in css and "已確認" in app
    assert "把確認好的收合起來" in app and "（點了展開）" in app and "FC_FOLD_KEY" in app
    assert "data-fcout" in app and "/api/review/outside" in app and "改答案" in app
    assert "換一種念法" in app and '第 ${c["第幾版"]} 版' in fcj   # 10-15：第 5 步只留「第 N 版」標籤
    assert "多半會跟上一版一樣" not in app and "生成的規則一樣" not in fcj
    assert "第 4 步總檢查不會再問這幾秒" not in rv
    for js in (app, rv, fcj):   # 畫面上的字不用「種子」
        for line in js.splitlines():
            code = line.split("//")[0]
            assert "種子" not in code, line.strip()


def test_poll_failure_shows_conn_banner_after_repeated_failures():
    """10-03 第九批（#24）：輪詢連續連不上才出「連不上伺服器」橫幅，一次失敗不跳；成功一次就收掉；
    伺服器有回應只是回錯誤不算。兩個輪詢（第 1 步、第 4 步）都接上。用 node 跑真的函式，假的 document。"""
    import shutil
    import subprocess

    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert app.count("pollFailed(e);") == 2 and app.count("pollOk();") == 2
    assert "連不上伺服器，可能已經關掉了；請回終端機看，或重新雙擊啟動" in app
    assert "/* 輪詢失敗，下一次再試 */ }" not in app and "// 輪詢失敗不中斷，下一次再試\n" not in app   # 不再靜默吞掉
    node = shutil.which("node")
    if not node:
        return
    block = app[app.index("const CONN_FAIL_LIMIT"):app.index("function esc(s)")]
    fake_dom = """
const nodes = {};
function mk(id) { return { id, hidden: true, className: "", innerHTML: "", parentNode: null,
  setAttribute() {}, nextSibling: null }; }
nodes.saveError = mk("saveError");
const body = { firstChild: null, insertBefore(el) { nodes[el.id] = el; } };
nodes.saveError.parentNode = body;
const document = { body, getElementById: (id) => nodes[id] || null, createElement: () => mk("") };
"""
    script = fake_dom + block + """
const net = Object.assign(new Error("x"), { network: true });
const out = [];
const shown = () => !!(document.getElementById("connLost") && !document.getElementById("connLost").hidden);
pollFailed(net); out.push(shown());
pollFailed(net); out.push(shown());
pollFailed(new Error("500")); out.push(shown());   // 伺服器回錯誤：不算
pollFailed(net); out.push(shown());
pollFailed(net); out.push(shown());
pollOk(); out.push(shown());
pollFailed(net); out.push(shown());
console.log(JSON.stringify(out), document.getElementById("connLost").innerHTML.includes("重新雙擊啟動"));
"""
    r = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[false,false,false,true,true,false,false] true", r.stdout


def test_step4_whole_check_folded_by_default():
    """10-08 宇軒：第 4 步「開始前總檢查」整區預設收成一行，展開狀態記在瀏覽器；硬擋的訊息放在開始執行按鈕旁邊、不收合。"""
    import json
    import shutil
    import subprocess

    app = (WEB / "app.js").read_text(encoding="utf-8")
    fn = re.search(r"^function finalCheckHtml\(.*?^\}\n", app, re.S | re.M).group(0)
    assert fn.lstrip().count('<details class="fc-whole" id="fcWhole"') == 1 and fn.rstrip().endswith("</div></details>`;\n}")
    assert "fcCheckOpen() || !!execGoCheck || !!execBackRow" in fn
    body = re.search(r"^async function renderExecuteBody\(.*?^\}\n", app, re.S | re.M).group(0)
    assert body.index('id="execBlockNear"') < body.index('<button id="btnExec"')
    assert body.count("還不能開始") == 1   # 10-09：只留按鈕上方那一塊，頁面最上面不再重複一張卡
    assert 'whole.addEventListener("toggle", () => fcSetCheckOpen(whole.open))' in app
    node = shutil.which("node")
    if not node:
        return
    pick = lambda name: re.search(rf"^function {name}\(.*?^\}}\n", app, re.S | re.M).group(0)
    fc = {"一定要處理": [{"處理好": False}] * 8, "請看一眼": [{"已看過": False}] * 24}
    script = (pick("fcFoldHeads") + pick("fcWholeHead") + pick("fcCheckOpen") + 'const FC_CHECK_OPEN_KEY = "fc-check-open";\n'
              + f"const fc = {json.dumps(fc, ensure_ascii=False)};"
              + 'console.log(JSON.stringify([fcWholeHead(fc), fcWholeHead({"一定要處理": [{"處理好": true}], "請看一眼": [{"已看過": true}]}),'
              + ' fcWholeHead({"一定要處理": [], "請看一眼": []}), fcCheckOpen()]));')
    r = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == ["開始前總檢查：一定要處理 8 列、請看一眼 24 處（預設先略過）",
                                    "開始前總檢查：一定要處理 0 列、請看一眼 0 處（都處理好了）",
                                    "開始前總檢查：沒有要處理、要看的", False]   # 沒有 localStorage（讀不到）＝收合


def test_step0_moved_to_tool_menu():
    """1022-1（#267）：第 0 步不列在步驟列，收進左下角齒輪「工具設定」選單（第 3 步那顆「設定」不改名）。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'id="toolMenuBtn"' in html and "工具設定" in html and 'id="toolMenu"' in html and 'aria-haspopup="menu"' in html
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'id: "step0", num: 0, title: "初始化設定", real: true, who: "人工", nav: false' in js
    assert 'STEP_DEFS.filter((s) => s.nav !== false)' in js
    i = js.index("const TOOL_MENU = [")
    assert 'route: "step0", label: "初始化設定"' in js[i:i + 300]
    assert "<h1>0　初始化設定</h1>" not in js
    rv = (WEB / "review.js").read_text(encoding="utf-8")
    assert 'id="rv-set-btn" aria-expanded="false">設定</button>' in rv


def test_own_dialogs_replace_browser_boxes():
    """1022-1（#263）：瀏覽器內建的 confirm／prompt 全部換成工具自己的視窗（ui.js），錯誤類的 alert 也換；
    回報成功的 alert 在 1022-2（#264）改成 Toast（見 test_success_toasts）。"""
    import re
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert html.index('src="ui.js"') < html.index('src="app.js"')   # 其他程式之前載入
    ui = (WEB / "ui.js").read_text(encoding="utf-8")
    for fn in ("function uiConfirm(", "function uiPrompt(", "function uiAlert(", "showModal()", '"cancel"'):
        assert fn in ui, fn
    assert ".innerHTML" not in ui and "textContent = line" in ui   # 文字一律 textContent，不拼 HTML
    assert 'dlg.className = "rv-modal ui-dialog"' in ui and "document.body.appendChild(dlg)" in ui   # 掛在 body：第 3 步唯讀鎖不到
    assert "o.danger && cancelBtn ? cancelBtn : okBtn" in ui   # 危險動作停在取消
    others = {f: (WEB / f).read_text(encoding="utf-8") for f in ("app.js", "cards.js", "review.js", "finalcheck.js", "fileio.js")}
    for f, js in others.items():
        assert not re.search(r"(?<![\w.])confirm\(", js), f
        assert not re.search(r"(?<![\w.])prompt\(", js), f
        # 1022-2（#264）：回報成功的 alert 也改成浮動通知 uiToast，一個裸的 alert( 都不留
        assert not re.search(r"(?<![\w.])alert\(", js), (f, re.findall(r"(?<![\w.])alert\((.{0,40})", js))
    cards = others["cards.js"]
    # 重念範圍改了：兩顆按鈕直接寫做法，關掉視窗＝保留
    assert 'ok: "照新範圍重排", cancel: "保留我改的字"' in cards
    # 第 3 步唯讀時視窗裡的按鈕不在 #content 裡，鎖不到（rvApplyReadonly 只鎖 .rv2 底下）
    rv = others["review.js"]
    assert 'root.querySelectorAll("#rv-right, #rv-lower, #rv-settings, #rv-io, .rv-bar-btns")' in rv



def test_success_toasts():
    """1022-2（#264）：成功／完成的訊息改成右下角浮動通知（ui.js uiToast），5 秒自己消失、滑上去暫停、最多 3 則；
    要人接著動手的那一句（代號不夠、還有沒換到的）仍用要按「知道了」的視窗。"""
    import re
    ui = (WEB / "ui.js").read_text(encoding="utf-8")
    assert "const UI_TOAST_MS = 5000;" in ui and "const UI_TOAST_MAX = 3;" in ui
    for s in ('function uiToast(', 'box.setAttribute("role", "status")', 'box.setAttribute("aria-live", "polite")',
              '"mouseenter"', '"mouseleave"', '"focusin"', '"focusout"', '"關閉通知"', "prefers-reduced-motion: reduce"):
        assert s in ui, s
    toast = ui[ui.index("function uiToast("):].split("/* 1022-2（#265）")[0]
    assert ".focus(" not in toast and ".innerHTML" not in toast   # 不搶焦點、文字不拼 HTML
    css = (WEB / "style.css").read_text(encoding="utf-8")
    i = css.index(".ui-toasts {")
    assert "right: 16px" in css[i:i + 200] and "bottom: 16px" in css[i:i + 200]   # 右下角，不擋左下角齒輪
    cards = (WEB / "cards.js").read_text(encoding="utf-8")
    rv = (WEB / "review.js").read_text(encoding="utf-8")
    assert len(re.findall(r"uiToast\(", cards + rv)) == 7
    i = rv.index('apiPost("/api/codes/auto"')
    block = rv[i:i + 700]
    assert 'if (r["代號不夠"]) uiAlert(' in block and "剩下的請自己打一個" in block and "else uiToast(" in block
    i = rv.index('apiPost("/api/codes/convert"')
    block = rv[i:i + 700]
    assert 'r["還剩"].length) uiAlert(' in block and "else uiToast(msg)" in block
    # 錯誤一律還是視窗
    assert 'uiAlert({ title: "沒有配成"' in rv and 'uiAlert({ title: "沒有換成"' in rv



def test_button_tooltips():
    """1022-2（#265）：重要按鈕的說明改成馬上出現的提示框（ui.js，按鈕加 data-tip），拿掉原本的 title；
    停用時「為什麼不能按」、狀態標籤、時間軸、步驟列連結的 title 不動。"""
    import re
    ui = (WEB / "ui.js").read_text(encoding="utf-8")
    for s in ("const UI_TIP_DELAY_MS = 150;", 'document.addEventListener("mouseover"', 'document.addEventListener("focusin"',
              ':focus-visible', '"Escape"', 'setAttribute("role", "tooltip")', '"aria-describedby"', "uiTipEl.textContent = t.dataset.tip"):
        assert s in ui, s
    assert 'uiTipEl.classList.add("below")' in ui   # 上面放不下放下面
    css = (WEB / "style.css").read_text(encoding="utf-8")
    i = css.index(".ui-tip {")
    assert "font-size: 15px" in css[i:i + 400] and "pointer-events: none" in css[i:i + 400]
    web = {f: (WEB / f).read_text(encoding="utf-8") for f in ("app.js", "cards.js", "review.js", "finalcheck.js", "index.html")}
    want = {
        "cards.js": ['id="rv-isteacher" data-tip=', 'id="rv-retime" data-tip=', 'data-tip="抓錯了', 'id="rv-tai" data-tip=',
                     'id="rv-del" data-tip=', 'data-tip="聽 ', 'data-te-now="${which}" data-tip='],
        "review.js": ['id="rv-code-btn" aria-expanded="false" data-tip=', 'id="rv-export" data-tip=', 'data-tip="有建議、還沒確認',
                      'id="rv-pass" data-tip=', 'data-tip="一位其實是兩個人'],
        "finalcheck.js": ['id="fc-pass" data-tip='],
        "app.js": ['data-fcplay="${rg[0]}|${rg[1]}" data-tip=', "b.dataset.tip = label;", 'b.setAttribute("aria-label", label)'],
        "index.html": ['data-tip="收合步驟列" aria-label="收合步驟列"', 'data-tip="工具設定" aria-label="工具設定"'],
    }
    for f, keys in want.items():
        for k in keys:
            assert k in web[f], (f, k)
    # 改了的按鈕不再同時有 title（兩個提示一起出現）
    for f, js in web.items():
        for m in re.finditer(r"<button[^>]*data-tip=[^>]*>", js):
            assert " title=" not in m.group(0), (f, m.group(0)[:120])
    assert "b.title =" not in web["app.js"]
    # 刻意不動：停用時的原因、時間軸、狀態標籤
    assert 'disabled title="${esc(fcFixOff(c, fcOld()))}"' in web["finalcheck.js"]
    assert 'id="rv-tl" title="點一下或拖拉' in web["review.js"]
    assert "btn.title = st.title;" in web["review.js"]   # 「開始 AI 修改」常常是停用的，原因留在 title


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
    sys.exit(0)
