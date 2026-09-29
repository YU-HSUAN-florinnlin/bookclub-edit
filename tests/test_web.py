"""網頁（`bookclub/web/`）的靜態檢查：不開瀏覽器，只讀檔案確認幾個定案過的規則沒被改掉。

互動行為（收合、存檔、點按鈕）用 `tests/fake_workdir.py` 的假工作區開伺服器實際點過，見 CHANGELOG；
這支只擋「改版時不小心把定案拿掉」：步驟列每步的 AI／人工、localStorage 讀寫都包 try/catch。

獨立可跑：.venv/bin/python tests/test_web.py
"""

from __future__ import annotations

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
    for name in ("app.js", "review.js", "finalcheck.js"):
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



def test_step3_go4_button_and_confirm():
    # 09-29 宇軒：第 3 步全部通過才能按；確認視窗兩個按鈕「開始修改」「回去檢查」，預設焦點在回去檢查
    js = (WEB / "review.js").read_text(encoding="utf-8")
    assert 'id="rv-go4" disabled' in js
    assert "開始修改" in js and "回去檢查" in js
    assert '#rv-confirm-back").focus()' in js
    assert 'location.hash = "#step4"' in js


def test_step5_blurred_preview_when_no_product():
    # 09-29 宇軒：還沒有成品時照正式版面擺霧化預覽，按不下去（inert）
    js = (WEB / "finalcheck.js").read_text(encoding="utf-8")
    assert "fcPreviewHtml" in js and "inert" in js


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
    js = (WEB / "review.js").read_text(encoding="utf-8")
    assert "未通過" not in js and "✓ 已通過（再按取消）" in js
    assert "保留原聲（不用改）" not in js and "不是名字，不用改" in js
    assert "3 件事" not in js and "開始前 4 件事" in js


def test_step4_stop_and_step3_readonly():
    # 09-30：第 4 步有停止按鈕、預估剩餘時間；執行中第 3 步唯讀＋上方橫幅
    app = (WEB / "app.js").read_text(encoding="utf-8")
    rv = (WEB / "review.js").read_text(encoding="utf-8")
    assert 'id="btnStop"' in app and "/api/execute/stop" in app and "execEtaText" in app
    assert 'id="rv-busy"' in rv and "rvApplyReadonly" in rv and 'rv.data["AI執行中"]' in rv


def test_no_promises_of_unbuilt_redo():
    # 09-30：第 5 步退回、第 4 步「從這裡開始」的說明要跟實際一致（一鍵只重做還沒做好；第 2 步參考音要選好）
    fc = (WEB / "finalcheck.js").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "第 4 步會照這份清單只重做這幾筆" not in fc and "手動重做" in fc
    assert "能省掉的是第 2、3 步的人工" not in app and 'id="btnAutoCode"' in app
    skill = (REPO_ROOT / "skills" / "bookclub-edit" / "SKILL.md").read_text(encoding="utf-8")
    assert "0.1.1" not in skill and "總覽選影片" in skill


def test_keep_all_voices_needs_confirm():
    # 09-30：「全部保留原聲」先跳確認視窗（會讓 N 位學員保留原聲、不換聲音），取消不變
    js = (WEB / "review.js").read_text(encoding="utf-8")
    block = js[js.index('querySelectorAll(".rv-voice-all")'):]
    block = block[:block.index("}));")]
    assert "confirm(" in block and "位學員保留原聲、不換聲音" in block
    assert block.index("confirm(") < block.index('apiPost("/api/review/voice"')


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
    sys.exit(0)
