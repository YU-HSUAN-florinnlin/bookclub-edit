"""10-08 宇軒：第 4 步的執行時間——總經過、實際執行、中斷幾次（時間）、各步花多久。

- 純函式：run_entry／run_history／run_times（含停止、伺服器被關掉再繼續、全部做完後重新一輪、執行中、舊版沒有歷史）
- run_execute（假的步驟，不跑模型）：停止一次再繼續做完，執行進度記得兩次；伺服器被關掉用最後一次存檔時間當結束
- inspect 執行進度、網頁「執行時間」一塊（node 跑純函式）
獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_run_times.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import io
import json
import re
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_execute as TE  # noqa: E402 — 假工作區、假名冊

from bookclub import execute  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

NODE = shutil.which("node")


def _p(start, end=None, **kw):
    return {"開始時間": start, "結束時間": end, "步驟": kw.pop("步驟", {}), **kw}


def test_run_times_with_stop_interrupt_and_done():
    hist = [execute.run_entry(_p("2026-10-08T10:00:00", "2026-10-08T10:30:00", 停止=True,
                                 步驟={"老師名字": {"開始": "2026-10-08T10:00:00", "結束": "2026-10-08T10:30:00", "狀態": "停止"}}))]
    hist += execute.run_history(_p("2026-10-08T11:00:00", None, 中斷=True, 最後存檔="2026-10-08T11:20:00",
                                   步驟={"老師名字": {"開始": "2026-10-08T11:00:00", "狀態": "中斷"}}))
    assert hist[1] == {"開始": "2026-10-08T11:00:00", "結束": "2026-10-08T11:20:00", "結果": "中斷", "步驟秒": {"老師名字": 1200.0}}
    done = _p("2026-10-08T12:00:00", "2026-10-08T13:00:00", 執行歷史=hist,
              步驟={"老師名字": {"開始": "2026-10-08T12:00:00", "結束": "2026-10-08T12:10:00", "狀態": "做完"},
                    "學員重念": {"開始": "2026-10-08T12:10:00", "結束": "2026-10-08T12:40:00", "狀態": "做完"},
                    "保留原聲學員名字": {"狀態": "跳過"},
                    "組裝": {"開始": "2026-10-08T12:40:00", "結束": "2026-10-08T13:00:00", "狀態": "做完"}})
    t = execute.run_times(done, "2026-10-08T14:00:00")
    assert t["總經過秒"] == 3 * 3600 and t["實際執行秒"] == (30 + 20 + 60) * 60 and t["執行次數"] == 3
    assert t["中斷"] == [{"時間": "2026-10-08T10:30:00", "結果": "停止"}, {"時間": "2026-10-08T11:20:00", "結果": "中斷"}]
    assert t["各步秒"] == {"老師名字": 3600.0, "學員重念": 1800.0, "組裝": 1200.0} and t["最後結果"] == "做完"
    assert t["有歷史"] and not t["執行中"] and t["已執行秒"] is None and t["算不出的次數"] == 0
    # 全部做完之後再按一次：新的一輪，只算這一次（執行中算到現在）
    again = _p("2026-10-08T15:00:00", None, 執行歷史=execute.run_history(done))
    t2 = execute.run_times(again, "2026-10-08T15:10:00")
    assert t2["執行中"] and t2["執行次數"] == 1 and t2["已執行秒"] == 600 and t2["實際執行秒"] == 600 and t2["中斷"] == []


def test_old_format_and_unknown_end():
    old = _p("2026-10-04T04:50:49", "2026-10-04T05:14:03", 步驟={"組裝": {"開始": "2026-10-04T04:50:49", "結束": "2026-10-04T05:14:03"}})
    t = execute.run_times(old)
    assert not t["有歷史"] and t["實際執行秒"] == 1394 and t["執行次數"] == 1
    # 舊版的中斷沒有最後存檔時間：結束不知道，算不出的那一次另外記
    t = execute.run_times(_p("2026-10-08T23:39:10", None, 中斷=True, 執行歷史=[]))
    assert t["算不出的次數"] == 1 and t["實際執行秒"] == 0 and t["總經過秒"] is None
    assert execute.run_times(None) is None and execute.run_times({}) is None
    # 上一份沒收尾（還寫著執行中）的，開始新的一次時當作中斷
    assert execute.run_history(_p("2026-10-08T01:00:00"))[0]["結果"] == "中斷"


def test_run_execute_stop_then_continue_records_history():
    from bookclub.tts import StopRequested

    w = TE._fresh()
    calls = []
    runners, checks = TE._fake(calls)
    state = {"停": True}
    orig = runners["學員重念"]

    def maybe_stop(wk, ctx):
        if state["停"]:
            raise StopRequested("按了停止：停在目前這一句做完之後")
        return orig(wk, ctx)
    runners["學員重念"] = maybe_stop
    prog = execute.run_execute(w, runners=runners, checks=checks, skip_precheck=True, log=lambda m: None)
    assert prog["停止"] and prog[execute.HISTORY_FIELD] == [] and prog["最後存檔"]
    state["停"] = False
    prog = execute.run_execute(w, runners=runners, checks=checks, skip_precheck=True, log=lambda m: None)
    hist = prog[execute.HISTORY_FIELD]
    assert len(hist) == 1 and hist[0]["結果"] == "停止" and hist[0]["結束"]
    t = execute.run_times(wd.read_json(execute.progress_path(w)), execute._now())
    assert t["執行次數"] == 2 and len(t["中斷"]) == 1 and t["中斷"][0]["結果"] == "停止" and t["最後結果"] == "做完"
    assert set(t["各步秒"]) >= {"老師名字", "組裝"}
    # 伺服器被關掉：用最後一次存檔的時間當作結束
    p = wd.read_json(execute.progress_path(w))
    p.update({"結束時間": None, "最後存檔": "2026-10-08T23:41:52"})
    p["步驟"]["組裝"] = {"狀態": "進行中", "開始": "2026-10-08T23:40:00"}
    wd.write_json(execute.progress_path(w), p)
    assert execute.mark_interrupted(w)
    p = wd.read_json(execute.progress_path(w))
    assert p["結束時間"] == "2026-10-08T23:41:52" and p["步驟"]["組裝"]["結束"] == "2026-10-08T23:41:52"
    assert "繼續執行" in p["步驟"]["組裝"]["訊息"]
    # inspect 執行進度：只印時間與數字
    from bookclub import safeview

    buf = io.StringIO()
    with redirect_stdout(buf):
        safeview.main([str(w), "執行進度"])
    text = buf.getvalue()
    assert "這一輪：第一次開始" in text and "中斷" in text and "各步秒" in text and "結果=停止" in text


def test_web_exec_time_html():
    app = (REPO_ROOT / "bookclub" / "web" / "app.js").read_text(encoding="utf-8")
    body = re.search(r"^async function renderExecuteBody\(.*?^\}\n", app, re.S | re.M).group(0)
    assert body.index('<tbody id="execSteps">') < body.index('id="execTimeBox"')   # 步驟表下面
    assert 'tb.innerHTML = execTimeHtml(d["執行時間"])' in app                      # 執行中跟著更新
    if not NODE:
        return
    pick = lambda n: re.search(rf"^function {n}\(.*?^\}}\n", app, re.S | re.M).group(0)
    esc = 'const esc = (s) => String(s == null ? "" : s);\n'
    stop = re.search(r"^const EXEC_STOP_WORD = .*\n", app, re.M).group(0)
    t = {"總經過秒": 10800, "實際執行秒": 6600, "執行次數": 3, "中斷": [{"時間": "2026-10-08T10:30:00", "結果": "停止"},
         {"時間": "2026-10-08T11:20:00", "結果": "中斷"}], "各步秒": {"老師名字": 3600, "組裝": 1200}, "執行中": False,
         "已執行秒": None, "有歷史": True, "算不出的次數": 0, "開始": "2026-10-08T10:00:00", "結束": "2026-10-08T13:00:00"}
    run = {**t, "執行中": True, "已執行秒": 600, "有歷史": False}
    code = (esc + pick("fmtStamp") + pick("execDur") + stop + pick("execTimeHtml")
            + f"const t = {json.dumps(t, ensure_ascii=False)}, r = {json.dumps(run, ensure_ascii=False)};"
            + "console.log(JSON.stringify([execTimeHtml(t), execTimeHtml(r), execTimeHtml(null)]));")
    done, running, none = json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True).stdout)
    assert "總經過時間</td><td>3 小時 0 分" in done and "實際執行時間</td><td>1 小時 50 分" in done
    assert "2 次：10-08 10:30（按了停止）、10-08 11:20（伺服器被關掉）" in done and "老師名字 1 小時 0 分；組裝 20 分 0 秒" in done
    assert "已執行 10 分 0 秒" in running and "舊版沒有記錄更早的執行" in running and none == ""


def test_unfinished_running_uses_last_save_as_end():
    """10-09：上一次還寫著執行中、沒被標成中斷的：用最後存檔當結束，不算進「算不出的次數」。"""
    prev = _p("2026-10-08T20:00:00", None, 最後存檔="2026-10-08T20:45:00",
              步驟={"學員重念": {"開始": "2026-10-08T20:00:00", "狀態": "進行中"}})
    hist = execute.run_history(prev)
    assert hist == [{"開始": "2026-10-08T20:00:00", "結束": "2026-10-08T20:45:00", "結果": "中斷", "步驟秒": {"學員重念": 2700.0}}]
    t = execute.run_times(_p("2026-10-08T21:00:00", "2026-10-08T21:10:00", 執行歷史=hist))
    assert t["算不出的次數"] == 0 and t["實際執行秒"] == 45 * 60 + 10 * 60 and t["執行次數"] == 2
    assert t["中斷"] == [{"時間": "2026-10-08T20:45:00", "結果": "中斷"}]


def test_heartbeat_updates_last_save_while_step_runs():
    """10-09：長的步驟跑到一半，「最後存檔」也會定時更新（伺服器被關掉時少算的時間不超過一個間隔）；結束後不再寫。"""
    import time

    w = TE._fresh()
    calls = []
    runners, checks = TE._fake(calls)
    orig = runners["學員重念"]
    seen = []

    def slow(wk, ctx):
        first = wd.read_json(execute.progress_path(wk))["最後存檔"]
        deadline = time.time() + 5
        while time.time() < deadline:   # 等定時存檔把時間往後推（不靠這一步自己存）
            time.sleep(0.1)
            now = wd.read_json(execute.progress_path(wk))["最後存檔"]
            if now != first:
                seen.append((first, now))
                break
        return orig(wk, ctx)
    runners["學員重念"] = slow
    old = execute.HEARTBEAT_SECS
    execute.HEARTBEAT_SECS = 0.3
    try:
        prog = execute.run_execute(w, runners=runners, checks=checks, skip_precheck=True, log=lambda m: None)
    finally:
        execute.HEARTBEAT_SECS = old
    assert seen and seen[0][1] > seen[0][0], seen
    assert prog["結束時間"]
    path = execute.progress_path(w)
    before = path.stat().st_mtime_ns
    time.sleep(1.0)
    assert path.stat().st_mtime_ns == before   # 結束後定時存檔停了
    assert not [t for t in __import__("threading").enumerate() if t.name == "執行進度定時存檔"]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
