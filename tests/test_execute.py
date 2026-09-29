"""bookclub/execute.py（第 4 步一次跑完）的測試：順序、做過的跳過、中斷續跑、前置檢查、做過沒有的判斷。
生成與組裝用假的（不載入模型）；做過沒有的判斷用 tests/fake_workdir.py 的假工作區。

獨立可跑：.venv/bin/python tests/test_execute.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import fake_workdir  # noqa: E402

_DATA = Path(tempfile.mkdtemp())
(_DATA / "名冊.csv").write_text("中文名,其他寫法,英文代號,聲線,性別\n小美,,Amy,,女\n阿明,,Tom,,男\n", encoding="utf-8")
os.environ["BOOKCLUB_DATA_DIR"] = str(_DATA)

from bookclub import execute, nameplan, students, tts  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

_ROOT = None


def _fresh() -> Path:
    global _ROOT
    if _ROOT is None:
        _ROOT = Path(tempfile.mkdtemp()) / "base"
        fake_workdir.make(_ROOT)
    d = Path(tempfile.mkdtemp())
    shutil.copytree(_ROOT, d / "base")
    w = d / "base" / "工作區"
    for name in ("分析結果.json",):
        data = json.loads((w / name).read_text(encoding="utf-8"))
        data["video"] = str(d / "base" / "假影片.mp4")
        (w / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return w


def _fake(calls: list, state: dict | None = None) -> tuple[dict, dict]:
    """假的三步：跑過就記在 state["做過"]；state["失敗"] 是哪一步就在那一步丟例外（模擬模型載入失敗）。"""
    state = state if state is not None else {}
    state.setdefault("做過", set())

    def runner(key):
        def run(w, ctx):
            calls.append(key)
            if key == state.get("失敗"):
                raise RuntimeError("模型載入失敗（假的）")
            state["做過"].add(key)
        return run

    def check(key):
        return lambda w, ctx: (key in state["做過"], "假的")

    keys = [k for k, _ in execute.STEPS]
    return {k: runner(k) for k in keys}, {k: check(k) for k in keys}


def test_runs_in_order_then_skips_done():
    w = _fresh()
    calls: list = []
    runners, checks = _fake(calls)
    prog = execute.run_execute(w, runners=runners, checks=checks, skip_precheck=True, methods=["sw"], log=lambda s: None)
    assert calls == ["老師名字", "學員重念", "保留原聲學員名字", "組裝"]
    assert [prog["步驟"][k]["狀態"] for k, _ in execute.STEPS] == ["做完"] * len(execute.STEPS)
    assert prog["範圍"] == [0.0, 180.0] and prog["輸出做法"] == ["sw"]
    saved = wd.read_json(execute.progress_path(w))
    assert saved["結束時間"] and saved["步驟"]["組裝"]["狀態"] == "做完"
    prog = execute.run_execute(w, runners=runners, checks=checks, skip_precheck=True, log=lambda s: None)
    assert calls == ["老師名字", "學員重念", "保留原聲學員名字", "組裝"]            # 第二次全部跳過
    assert all(prog["步驟"][k]["狀態"] == "跳過" for k, _ in execute.STEPS)


def test_resume_after_failure():
    w = _fresh()
    calls: list = []
    state = {"失敗": "學員重念"}
    runners, checks = _fake(calls, state)
    try:
        execute.run_execute(w, runners=runners, checks=checks, skip_precheck=True, log=lambda s: None)
        raise AssertionError("應該失敗")
    except RuntimeError:
        pass
    saved = wd.read_json(execute.progress_path(w))
    assert saved["步驟"]["學員重念"]["狀態"] == "失敗" and "模型載入失敗" in saved["錯誤"]
    assert saved["步驟"]["組裝"]["狀態"] == "等待"
    state["失敗"] = None                          # 修好了再跑一次：老師名字做過了跳過，從學員重念接著做
    calls.clear()
    prog = execute.run_execute(w, runners=runners, checks=checks, skip_precheck=True, log=lambda s: None)
    assert calls == ["學員重念", "保留原聲學員名字", "組裝"] and prog["步驟"]["老師名字"]["狀態"] == "跳過"


def test_stop_requested_stops_after_current_and_resumes():
    # 09-30：網頁按「停止」→ 停在目前這一句之後，標「停止」不算失敗；下次接著做
    w = _fresh()
    calls: list = []
    state: dict = {"做過": set()}

    def stu(w2, ctx):
        calls.append("學員重念")
        execute.request_stop(w2)            # 跑到一半有人按了停止
        tts.check_stop(w2)                  # 生成迴圈下一句開始前會檢查

    runners, checks = _fake(calls, state)
    runners["學員重念"] = stu
    prog = execute.run_execute(w, runners=runners, checks=checks, skip_precheck=True, log=lambda s: None)
    assert prog["停止"] and prog["步驟"]["學員重念"]["狀態"] == "停止" and prog["步驟"]["組裝"]["狀態"] == "等待"
    assert not prog.get("錯誤") and execute.stop_requested(w)
    calls.clear()
    runners2, _ = _fake(calls, state)
    prog = execute.run_execute(w, runners=runners2, checks=checks, skip_precheck=True, log=lambda s: None)
    assert not execute.stop_requested(w)   # 開始時清掉上次的停止
    assert calls == ["學員重念", "保留原聲學員名字", "組裝"] and not prog.get("停止")


def test_stale_running_marked_interrupted():
    # 09-30：伺服器被關掉，進度檔永遠「進行中」→ 啟動時改成「中斷」
    w = _fresh()
    assert execute.mark_interrupted(w) is False                 # 沒有進度檔
    wd.write_json(execute.progress_path(w), {"步驟": {"老師名字": {"狀態": "做完"}, "學員重念": {"狀態": "進行中"}}})
    assert execute.mark_interrupted(w) is True
    prog = wd.read_json(execute.progress_path(w))
    assert prog["中斷"] and prog["步驟"]["學員重念"]["狀態"] == "中斷" and prog["步驟"]["老師名字"]["狀態"] == "做完"
    assert execute.mark_interrupted(w) is False                 # 改過一次就好


def test_only_steps_and_range():
    w = _fresh()
    calls: list = []
    runners, checks = _fake(calls)
    prog = execute.run_execute(w, start=60.0, end=120.0, only_steps=["組裝"], runners=runners, checks=checks,
                               skip_precheck=True, log=lambda s: None)
    assert calls == ["組裝"] and prog["範圍"] == [60.0, 120.0] and prog["步驟"]["老師名字"]["狀態"] == "略過"
    assert execute.tag_for(60.0, 120.0) == "1-2"


def test_precheck_lists_what_is_missing():
    w = _fresh()
    pre = execute.precheck(w)
    assert not pre["可以開始"] and any("匿名聲線" in x for x in pre["缺"])
    assert any("第 3 步還沒覆核" in x for x in pre["提醒"])
    try:
        execute.run_execute(w, log=lambda s: None)
        raise AssertionError("缺東西應該擋下來")
    except FileNotFoundError as e:
        assert "匿名聲線" in str(e)
    voices = _DATA / "聲線"
    voices.mkdir(exist_ok=True)
    for sex in ("男", "女"):
        (voices / f"{sex}聲_暫定.wav").write_bytes(b"RIFF")
        (voices / f"{sex}聲_暫定.txt").write_text("假的", encoding="utf-8")
    assert execute.precheck(w)["可以開始"]
    (w / "transcript" / "merged.json").unlink()
    pre = execute.precheck(w)
    assert not pre["可以開始"] and any("轉文字" in x for x in pre["缺"])
    for sex in ("男", "女"):
        (voices / f"{sex}聲_暫定.wav").unlink()


def test_done_checks_on_fake_workdir():
    w = _fresh()
    done, why = execute.names_done(w)
    assert not done and "要生成" in why                  # 假資料有 2 筆名字，老師紀錄還沒有
    plan = nameplan.compute_plan(w)
    wd.write_json(tts.teacher_log_path(w), {"句子": [{"id": g["id"], "text": g["text"], "放回時間格": {"檔案": "x.wav"}}
                                                    for g in plan["生成"]]})
    assert execute.names_done(w)[0]
    done, why = execute.students_done(w, None, None)
    assert not done
    items, _ = students.build_items(w)
    wd.write_json(students.log_path(w), {"句子": [{"id": it["id"], "text": it["text"], "放回時間格": {"檔案": "y.wav"}}
                                                 for it in items]})
    assert execute.students_done(w, None, None)[0]
    tag = execute.tag_for(0, 180)
    assert not execute.render_done(w, tag, ["sw"])[0]
    out = w / "輸出"
    out.mkdir(exist_ok=True)
    time.sleep(0.02)
    (out / f"成品_{tag}_sw.mp4").write_bytes(b"x")
    assert not execute.render_done(w, tag, ["sw"])[0]    # 09-29：摘要裡沒有驗證通過的紀錄，不算做好
    wd.write_json(out / f"輸出摘要_{tag}.json", {"輸出": {"sw": {"驗證": {"通過": False}}}})
    (out / f"成品_{tag}_sw.mp4").touch()
    assert "驗證" in execute.render_done(w, tag, ["sw"])[1]
    wd.write_json(out / f"輸出摘要_{tag}.json", {"輸出": {"sw": {"驗證": {"通過": True}}}})
    time.sleep(0.02)
    (out / f"成品_{tag}_sw.mp4").touch()
    assert execute.render_done(w, tag, ["sw"])[0]
    time.sleep(0.02)
    wd.write_json(students.log_path(w), {"句子": []})   # 生成結果比成品新 → 要重新組裝
    assert not execute.render_done(w, tag, ["sw"])[0]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
