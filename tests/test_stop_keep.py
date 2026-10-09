"""10-08 宇軒（批次 1008-8）：第 4 步停止、失敗都不能讓上一支完整的成品從第 5 步消失；同名專案分得出來；
第 3 步確認視窗精簡；第 4 步按鈕照狀態改字。

- 組裝（render_video）：處理紀錄、剪輯決策等成品驗證通過、換上正式檔名之後才寫；編碼到一半失敗時舊的都不動
- run_execute 停止、失敗：第 5 步照樣有舊成品，頂端多一行「第 4 步上次執行到一半被停止（時間）」
- 第 5 步沒有成品：寫「這個工作區還沒組裝過成品，請先在第 4 步執行」
- 專案標示（上一層資料夾、正式／測試）；網頁純函式（node）
不跑模型、不真的編碼（假的 render_full），不碰真的工作區。獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_stop_keep.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_execute as TE  # noqa: E402 — 假工作區、假名冊

from bookclub import execute, finalcheck, proclog, render, server  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

WEB = REPO_ROOT / "bookclub" / "web"
NODE = shutil.which("node")
_ESC = """const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
"""


def _fn(src: str, name: str) -> str:
    m = re.search(rf"^(async )?function {name}\(.*?^\}}\n", src, re.S | re.M)
    assert m, name
    return m.group(0)


def _node(code: str):
    return json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True).stdout)


def _with_product(w: Path) -> tuple[Path, dict]:
    """假工作區放一支「上一次的成品」與它的處理紀錄（不用真的編碼）。"""
    out = w / "輸出"
    out.mkdir(exist_ok=True)
    prod = out / "成品_0-3_sw.mp4"
    prod.write_bytes(b"old product")
    old_log = {"版本": 1, "來源": "render video 0-3", "產生時間": "舊的", "範圍": [0.0, 180.0], "片段": None, "紀錄": [], "未登記的變動": []}
    wd.write_json(proclog.log_path(w), old_log)
    (out / "剪輯決策_0-3.json").write_text('{"舊的": true}', encoding="utf-8")
    return prod, old_log


class _FakeRender:
    """把 render_video 裡重的部分換成假的（不抽聲音、不編碼），測完還原。fail＝編碼時丟例外（模擬停止、失敗）。"""

    NAMES = ("build_decisions", "build_audio", "measure", "join_jumps", "build_marks", "marks_md", "marks_html",
             "_placed_track", "render_full", "verify")

    def __init__(self, w: Path, fail: bool, bad: tuple = ()):
        self.w, self.fail, self.bad = w, fail, bad   # bad＝驗證沒過的輸出方式

    def __enter__(self):
        self.old = {n: getattr(render, n) for n in self.NAMES}
        self.old_log = proclog.build_render_log
        wav = self.w / "輸出" / "假聲音.wav"
        sf.write(str(wav), np.zeros(1600, dtype="float32"), 16000)
        plist = [{"src": [0.0, 180.0], "freeze": 0.0}]
        render.build_decisions = lambda *a, **k: {"範圍": [0.0, 180.0], "刪除": [], "停格": [], "動作": [], "警告": [],
                                                  "重疊沒處理": [], "名字沒處理": [], "標記": []}
        render.build_audio = lambda *a, **k: {"片段": plist, "原聲": wav, "新聲音": wav}
        render.measure = lambda *a, **k: {}
        render.join_jumps = lambda *a, **k: []
        render.build_marks = lambda *a, **k: []
        render.marks_md = lambda *a, **k: "新的標記"
        render.marks_html = lambda *a, **k: "新的標記"
        render._placed_track = lambda *a, **k: np.zeros(1, dtype="float32")
        proclog.build_render_log = lambda *a, **k: {"版本": 1, "來源": "render video 0-3", "產生時間": "新的",
                                                    "範圍": [0.0, 180.0], "片段": None, "紀錄": [], "未登記的變動": []}

        def full(video, d, plist, audio, dst, method, **k):
            if self.fail:
                Path(dst).write_bytes(b"half")
                raise RuntimeError("按了停止（假的）")
            Path(dst).write_bytes(b"new product")
            return 1.0
        render.render_full = full
        bad = self.bad
        render.verify = lambda path, *a, **k: {"通過": not any(f"_{m}.mp4" in Path(path).name for m in bad)}
        return self

    def __exit__(self, *exc):
        for n, f in self.old.items():
            setattr(render, n, f)
        proclog.build_render_log = self.old_log


def test_render_keeps_old_log_and_product_when_encoding_stops():
    w = TE._fresh()
    prod, old_log = _with_product(w)
    with _FakeRender(w, fail=True):
        try:
            render.render_video(w, 0.0, 180.0, tag="0-3", min_free_gb=0)
            raise AssertionError("應該丟例外")
        except RuntimeError:
            pass
    assert prod.read_bytes() == b"old product"                           # 舊成品沒被動到
    assert proclog.load(w)["產生時間"] == "舊的"                          # 處理紀錄還是舊的
    assert json.loads((w / "輸出" / "剪輯決策_0-3.json").read_text(encoding="utf-8")) == {"舊的": True}
    d = finalcheck.page_data(w)
    assert d["成品影片"] == "輸出/成品_0-3_sw.mp4" and d["有處理紀錄"]
    with _FakeRender(w, fail=False):                                     # 編碼做完：成品、處理紀錄一起換新
        render.render_video(w, 0.0, 180.0, tag="0-3", min_free_gb=0)
    assert prod.read_bytes() == b"new product" and proclog.load(w)["產生時間"] == "新的"


def test_run_execute_stop_or_fail_keeps_step5():
    from bookclub.tts import StopRequested

    for kind in ("停止", "失敗"):
        w = TE._fresh()
        prod, _ = _with_product(w)
        calls = []
        runners, checks = TE._fake(calls)

        def boom(_w, _ctx, kind=kind):
            if kind == "停止":
                raise StopRequested("按了停止：停在目前這一句做完之後")
            raise RuntimeError("模型載入失敗（假的）")
        runners["學員重念"] = boom
        try:
            execute.run_execute(w, runners=runners, checks=checks, skip_precheck=True, log=lambda m: None)
        except RuntimeError:
            assert kind == "失敗"
        d = finalcheck.page_data(w)
        assert d["成品影片"] == "輸出/成品_0-3_sw.mp4" and prod.read_bytes() == b"old product", kind
        run = d["上次執行"]
        assert run["狀態"] == kind and run["說明"].startswith(f"第 4 步上次執行到一半{finalcheck.RUN_WORD[kind]}"), run
        assert execute.status(w)["有成品"]


def test_last_run_pure():
    assert finalcheck.last_run(None) is None and finalcheck.last_run({}) is None
    base = {"開始時間": "2026-10-08T23:39:10", "步驟": {"組裝": {"狀態": "等待"}}}
    assert finalcheck.last_run({**base, "停止": True, "結束時間": "x"})["說明"] == "第 4 步上次執行到一半被停止（10-08 23:39 開始）"
    assert finalcheck.last_run({**base, "錯誤": "e", "結束時間": "x"})["狀態"] == "失敗"
    assert finalcheck.last_run({**base, "中斷": True})["狀態"] == "中斷"
    assert finalcheck.last_run(base)["狀態"] == "執行中" and finalcheck.last_run(base)["說明"] == ""
    done = finalcheck.last_run({**base, "結束時間": "y", "步驟": {"組裝": {"狀態": "做完"}}})
    assert done["狀態"] == "做完" and done["組裝做完"] and done["說明"] == ""


def test_project_label():
    a = server.project_label("/home/u/課程/開發專案X/2025-01-01 第一堂_剪輯工作區")
    b = server.project_label("/home/u/剪輯資料/舊測試區/從零_b/工作區/2025-01-01 第一堂_剪輯工作區")
    assert a == {"所在": "開發專案X", "標示": "正式", "顯示名稱": "2025-01-01 第一堂_剪輯工作區（開發專案X）"}
    assert b["所在"] == "從零_b" and b["標示"] == "測試" and b["顯示名稱"] != a["顯示名稱"]
    assert server.project_label("/tmp/驗證_0926d")["標示"] == "測試"
    # 10-08 審查：只看工作區資料夾本身、上一層、上上層（跳過「工作區」）的名稱；test 這類英文不算
    assert server.project_label("/Users/x/latest/tester/第一堂_剪輯工作區")["標示"] == "正式"
    assert server.project_label("/home/u/剪輯資料/舊測試區/從零_b/工作區/第一堂")["標示"] == "測試"   # 上上層
    assert server.project_label("/Users/x/測試/a/b/第一堂")["標示"] == "正式"   # 再往上一層的不算
    assert server.project_label("/home/u/舊測試區/第一堂")["標示"] == "測試"
    # 上一層資料夾也同名：往上多帶幾層，直到分得出來
    rows = [{"名稱": "工作區", "路徑": "/s/甲_ui/base/工作區", **server.project_label("/s/甲_ui/base/工作區")},
            {"名稱": "工作區", "路徑": "/s/乙_ui/base/工作區", **server.project_label("/s/乙_ui/base/工作區")},
            {"名稱": "別的", "路徑": "/s/丙/別的", **server.project_label("/s/丙/別的")}]
    server.unique_labels(rows)
    assert [r["顯示名稱"] for r in rows] == ["工作區（甲_ui／base）", "工作區（乙_ui／base）", "別的（丙）"]
    w = TE._fresh()
    rows = server.list_projects(w)["專案"]
    assert all({"所在", "標示", "顯示名稱"} <= set(r) for r in rows)
    assert server.build_state(w)["專案標示"]["顯示名稱"].startswith(w.name)


def test_web_pure_functions():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    rv = (WEB / "review.js").read_text(encoding="utf-8")
    fcj = (WEB / "finalcheck.js").read_text(encoding="utf-8")
    assert 'id="rv-confirm-main"' in rv and '<details class="rv-confirm-more"><summary>詳細</summary>' in rv
    assert "略過或照建議通過的項目，第 5 步都可以再檢查。要開始 AI 修改嗎？" in rv
    assert "confirm(`切換到「" in app                                     # 切換專案前先說要切去哪一個
    assert "fcRunNoteHtml(d[\"上次執行\"], true)" in fcj and "FC_NO_PRODUCT" in _fn(fcj, "renderFinal")
    if not NODE:
        return
    fmt = _fn(app, "fmtStamp")
    got = _node(_ESC + fmt + 'const EXEC_UPDATE_NOTE = "只補做第 3 步改過的部分，做過的不重做";\n' + _fn(app, "execBtnState") + _fn(app, "pjNowHtml") + """
      const st = (r, p, h) => execBtnState(r, p, h);
      const p0 = {"開始時間": "2026-10-08T23:39:10"};
      console.log(JSON.stringify({
        never: st(false, null, false), running: st(true, p0, true),
        stopped: st(false, {...p0, "停止": true, "結束時間": "2026-10-08T23:41:52"}, true),
        failed: st(false, {...p0, "錯誤": "x", "結束時間": "2026-10-08T23:41:52"}, false),
        cut: st(false, {...p0, "中斷": true}, true),
        done: st(false, {...p0, "結束時間": "2026-10-09T01:02:03", "步驟": {"組裝": {"狀態": "做完"}}}, true),
        now: pjNowHtml({"顯示名稱": "第一堂（開發專案）", "標示": "正式"}), none: pjNowHtml(null) }));""")
    assert got["never"]["label"] == "開始執行" and "還沒執行過" in got["never"]["status"]
    assert got["running"]["label"] == "執行中…" and "第 5 步看的還是上一支成品" in got["running"]["status"]
    assert got["stopped"]["label"] == "繼續執行" and "第 5 步看的是上一支完整的成品" in got["stopped"]["status"]
    assert got["failed"]["label"] == "繼續執行" and "第 5 步還沒有成品" in got["failed"]["status"]
    assert got["cut"]["label"] == "繼續執行" and "跑到一半網頁伺服器被關掉" in got["cut"]["status"]
    assert got["done"]["label"] == "更新成品" and got["done"]["note"] == "只補做第 3 步改過的部分，做過的不重做"
    assert "已完成（10-09 01:02）" in got["done"]["status"]
    body = _fn(app, "renderExecuteBody")   # 「只重新組裝」收在「進階設定」裡（平常不顯示）
    adv = body[body.index('<details class="adv">'):body.index("</details>", body.index('<details class="adv">'))]
    assert 'id="btnReassembleAll"' in adv and body.count('id="btnReassembleAll"') == 1
    assert 'const EXEC_UPDATE_NOTE = "只補做第 3 步改過的部分，做過的不重做";' in app
    assert "現在在：第一堂（開發專案）" in got["now"] and got["none"] == ""
    got = _node(_ESC + "const FC_NO_PRODUCT = 1;\n" + _fn(fcj, "fcRunNoteHtml") + _fn(fcj, "fcWhereHtml") + """
      const run = {"說明": "第 4 步上次執行到一半被停止（10-08 23:39 開始）"};
      console.log(JSON.stringify([fcRunNoteHtml(run, true), fcRunNoteHtml(run, false), fcRunNoteHtml({"說明": ""}, true),
        fcWhereHtml({"顯示名稱": "第一堂（從零_b）", "標示": "測試"})]));""")
    assert "現在看的是上一支完整的成品" in got[0] and "還沒有完整的成品" in got[1] and got[2] == ""
    assert "現在在：<b>第一堂（從零_b）</b>（測試）" in got[3]
    assert '這個工作區還沒組裝過成品，請先在第 4 步執行' in fcj


def test_render_multi_method_one_fails_and_none_pass():
    """10-08 審查：多種輸出方式其中一支驗證沒過——處理紀錄、處理標記照樣換新（有一支成品換上了），
    輸出摘要、剪輯決策的摘要含全部做法；一支都沒換上——輸出摘要、處理標記都不動。"""
    w = TE._fresh()
    _with_product(w)
    out = w / "輸出"
    (out / "處理標記_0-3.md").write_text("舊的標記", encoding="utf-8")
    (out / "輸出摘要_0-3.json").write_text('{"舊的": true}', encoding="utf-8")
    with _FakeRender(w, fail=False, bad=("sw",)):                        # 一支都沒過
        try:
            render.render_video(w, 0.0, 180.0, tag="0-3", methods=["sw"], min_free_gb=0)
            raise AssertionError("應該丟例外")
        except RuntimeError as e:
            assert "驗證沒過" in str(e)
    assert proclog.load(w)["產生時間"] == "舊的" and (out / "處理標記_0-3.md").read_text(encoding="utf-8") == "舊的標記"
    assert json.loads((out / "輸出摘要_0-3.json").read_text(encoding="utf-8")) == {"舊的": True}
    with _FakeRender(w, fail=False, bad=("hw",)):                        # sw 過、hw 沒過
        try:
            render.render_video(w, 0.0, 180.0, tag="0-3", methods=["sw", "hw"], min_free_gb=0)
            raise AssertionError("應該丟例外")
        except RuntimeError as e:
            assert "hw" in str(e)
    assert proclog.load(w)["產生時間"] == "新的" and (out / "處理標記_0-3.md").read_text(encoding="utf-8") == "新的標記"
    summ = json.loads((out / "輸出摘要_0-3.json").read_text(encoding="utf-8"))
    assert set(summ["輸出"]) == {"sw", "hw"} and summ["輸出"]["hw"]["檔案"].endswith("驗證沒過.mp4")
    dec = json.loads((out / "剪輯決策_0-3.json").read_text(encoding="utf-8"))
    assert set(dec["摘要"]["輸出"]) == {"sw", "hw"}


def test_skipped_moves_when_log_changes_even_if_assembly_fails():
    """跟 1008-7 的略過紀錄對齊：組裝那一步失敗，但處理紀錄已經換新（有一支成品換上了）→ 略過紀錄跟著換成這一次的；
    處理紀錄沒換 → 沿用上一次的。"""
    for wrote in (True, False):
        w = TE._fresh()
        _with_product(w)
        wd.write_json(execute.progress_path(w), {execute.SKIPPED_FIELD: {"沒處理": 99, "列": [], "時間": "舊的"}})
        calls = []
        runners, checks = TE._fake(calls)

        def half(_w, _ctx, wrote=wrote):
            if wrote:
                proclog.write_log(_w, {"版本": 1, "產生時間": "新的", "範圍": [0.0, 180.0], "片段": None, "紀錄": [],
                                        "未登記的變動": []})
            raise RuntimeError("hw：成品驗證沒過（假的）")
        runners["組裝"] = half
        try:
            execute.run_execute(w, only_steps=["組裝"], runners=runners, checks=checks, skip_precheck=True, log=lambda m: None)
            raise AssertionError("應該失敗")
        except RuntimeError:
            pass
        prog = wd.read_json(execute.progress_path(w))
        if wrote:
            assert prog[execute.SKIPPED_FIELD]["時間"] != "舊的" and execute.SKIPPED_PENDING not in prog
        else:
            assert prog[execute.SKIPPED_FIELD]["時間"] == "舊的" and execute.SKIPPED_PENDING in prog


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
