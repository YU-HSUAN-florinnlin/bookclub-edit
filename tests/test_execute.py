"""bookclub/execute.py（第 4 步一次跑完）的測試：順序、做過的跳過、中斷續跑、前置檢查、做過沒有的判斷。
生成與組裝用假的（不載入模型）；做過沒有的判斷用 tests/fake_workdir.py 的假工作區。

獨立可跑：.venv/bin/python tests/test_execute.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 10-01：這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

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
    assert not pre["可以開始"] and any("替代聲音" in x for x in pre["缺"])
    assert any("第 3 步還沒覆核" in x for x in pre["提醒"])
    try:
        execute.run_execute(w, log=lambda s: None)
        raise AssertionError("缺東西應該擋下來")
    except FileNotFoundError as e:
        assert "替代聲音" in str(e)
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


# ---------- 10-01：每一步、每一個聲線各自一支程式跑（假的子程式，不載入模型） ----------

_FAKE_CHILD = Path(tempfile.mkdtemp()) / "假子程式.py"
_FAKE_CHILD.write_text(r"""
import json, os, sys, time
from pathlib import Path

w = Path(sys.argv[1])
label = " ".join(sys.argv[2:])
conf_path = w / "假子程式.json"
conf = json.loads(conf_path.read_text(encoding="utf-8")) if conf_path.exists() else {}
with open(w / "假子程式紀錄.txt", "a", encoding="utf-8") as f:
    f.write(label + "\n")
print(f"[假子程式] 開始：{label}")
act = conf.get("動作", {}).get(label)
flag = w / "生成" / "_停止執行"
if act == "按停止":                 # 跑到一半有人按了停止，生成迴圈下一句開始前發現
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("x")
    print("[AI 執行] 按了停止")
    sys.exit(3)
if act == "等停止":                 # 一直生成，直到有人放停止旗標
    for _ in range(400):
        if flag.exists():
            print("[AI 執行] 按了停止")
            sys.exit(3)
        time.sleep(0.05)
    sys.exit(0)
if act == "出錯":
    print("[假子程式] 第 3 句生成中")
    raise RuntimeError("假的錯誤：模型載入失敗")
if act in ("被結束", "被結束一次"):
    n = w / ("被結束次數_" + label.replace(" ", "_"))
    k = int(n.read_text()) if n.exists() else 0
    n.write_text(str(k + 1))
    if act == "被結束" or k == 0:
        os.kill(os.getpid(), 9)      # 模擬記憶體不夠被系統結束：沒有任何錯誤訊息
mark = conf.get("做完標記", {}).get(label)
if mark:
    (w / f"做過_{mark}").write_text("1")
print("[記憶體] 這支程式最高用到 0.05 GB")
""", encoding="utf-8")

_PLAN = {
    "老師名字": [("老師 生成", ["老師名字", "生成"]), ("老師 停頓", ["老師名字", "停頓"]), ("老師 收尾", ["老師名字", "收尾"])],
    "學員重念": [("A 生成", ["學員重念", "生成", "--voice", "A"]), ("B 生成", ["學員重念", "生成", "--voice", "B"]),
             ("停頓", ["學員重念", "停頓"]), ("A 收尾", ["學員重念", "收尾", "--voice", "A"]),
             ("B 收尾", ["學員重念", "收尾", "--voice", "B"])],
    "保留原聲學員名字": [],
    "組裝": [("組裝", ["組裝"])],
}
_ORDER = ["老師名字 生成", "老師名字 停頓", "老師名字 收尾", "學員重念 生成 --voice A", "學員重念 生成 --voice B",
          "學員重念 停頓", "學員重念 收尾 --voice A", "學員重念 收尾 --voice B", "組裝"]
_MARKS = {"老師名字 收尾": "老師名字", "學員重念 收尾 --voice B": "學員重念", "組裝": "組裝"}


def _parts_setup(w: Path, actions: dict | None = None, probe=None, plan: dict | None = None):
    """假的子程式（真的開一支 Python，只印字、寫檔）＋固定的排程＋假的硬碟／swap 數字。"""
    (w / "假子程式.json").write_text(json.dumps({"動作": actions or {}, "做完標記": _MARKS}, ensure_ascii=False),
                                   encoding="utf-8")
    plan = plan or _PLAN
    opts = execute.PartOptions(
        command=lambda wk, args: [sys.executable, str(_FAKE_CHILD), str(wk), *args],
        planners={k: (lambda wk, ctx, k=k: list(plan[k])) for k in plan},
        probe=probe or (lambda wk: {"硬碟GB": 100.0, "swapGB": 1.0}),
        limits={"開始前硬碟GB": 5.0, "硬碟GB": 2.5, "swapGB": 8.5},
        check_every_s=0.05, retry_wait_s=0.0, keep_awake=False)
    checks = {k: (lambda wk, ctx, k=k: ((wk / f"做過_{k}").exists() or not _PLAN[k], "假的")) for k, _ in execute.STEPS}
    return opts, checks


def _calls(w: Path) -> list[str]:
    p = w / "假子程式紀錄.txt"
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def test_parts_run_in_order_skip_done_and_progress():
    w = _fresh()
    opts, checks = _parts_setup(w)
    lines: list = []
    prog = execute.run_execute(w, checks=checks, parts=opts, skip_precheck=True, log=lines.append)
    assert _calls(w) == _ORDER
    st = prog["步驟"]
    assert [st[k]["狀態"] for k, _ in execute.STEPS] == ["做完", "做完", "跳過", "做完"]   # 保留原聲學員名字：沒有要做的
    assert len(st["學員重念"]["子程式"]) == 5 and len(st["老師名字"]["子程式"]) == 3
    one = st["學員重念"]["子程式"][2]
    assert one["名稱"] == "停頓" and one["結束碼"] == 0 and one["記憶體高峰GB"] == 0.05
    saved = wd.read_json(execute.progress_path(w))
    assert saved["步驟"]["組裝"]["狀態"] == "做完" and len(saved["步驟"]["組裝"]["子程式"]) == 1
    assert "[假子程式] 開始：學員重念 停頓" in lines                    # 子程式的輸出一行一行送回來
    rows = execute.part_log_path(w).read_text(encoding="utf-8").splitlines()
    assert len(rows) == 9 and json.loads(rows[0])["名稱"] == "老師 生成"   # 每一支都記一行
    t = time.time()
    prog = execute.run_execute(w, checks=checks, parts=opts, skip_precheck=True, log=lambda s: None)
    assert _calls(w) == _ORDER and time.time() - t < 5                  # 第二次全部跳過，沒有開新的程式
    assert all(prog["步驟"][k]["狀態"] == "跳過" for k, _ in execute.STEPS)


def test_parts_stop_does_not_open_next():
    w = _fresh()
    opts, checks = _parts_setup(w, {"學員重念 生成 --voice A": "按停止"})
    prog = execute.run_execute(w, checks=checks, parts=opts, skip_precheck=True, log=lambda s: None)
    assert _calls(w) == _ORDER[:4]                                      # 停在 A 生成，B 生成以後都沒開
    assert prog["停止"] and prog["步驟"]["學員重念"]["狀態"] == "停止" and "停止" in prog["步驟"]["學員重念"]["訊息"]
    assert prog["步驟"]["組裝"]["狀態"] == "等待" and not prog.get("錯誤")
    (w / "假子程式.json").write_text(json.dumps({"做完標記": _MARKS}, ensure_ascii=False), encoding="utf-8")
    prog = execute.run_execute(w, checks=checks, parts=opts, skip_precheck=True, log=lambda s: None)
    assert _calls(w)[4:] == _ORDER[3:] and not prog.get("停止")         # 再按一次：老師跳過，學員那一步接著做


def test_parts_child_error_shows_last_lines():
    w = _fresh()
    opts, checks = _parts_setup(w, {"學員重念 生成 --voice B": "出錯"})
    try:
        execute.run_execute(w, checks=checks, parts=opts, skip_precheck=True, log=lambda s: None)
        raise AssertionError("應該失敗")
    except execute.PartFailed as e:
        assert "B 生成 出錯" in str(e) and "假的錯誤：模型載入失敗" in str(e) and "第 3 句生成中" in str(e)
    saved = wd.read_json(execute.progress_path(w))
    st = saved["步驟"]["學員重念"]
    assert st["狀態"] == "失敗" and "假的錯誤：模型載入失敗" in st["訊息"] and saved["步驟"]["組裝"]["狀態"] == "等待"
    assert _calls(w) == _ORDER[:5]                                      # 正常報錯不重試


def test_parts_killed_by_system_retries_once():
    w = _fresh()
    opts, checks = _parts_setup(w, {"學員重念 停頓": "被結束一次"})
    lines: list = []
    prog = execute.run_execute(w, checks=checks, parts=opts, skip_precheck=True, log=lines.append)
    assert _calls(w).count("學員重念 停頓") == 2 and prog["步驟"]["組裝"]["狀態"] == "做完"
    assert any("可能是記憶體不夠，程式被系統結束" in x and "重試一次" in x for x in lines)
    subs = prog["步驟"]["學員重念"]["子程式"]
    assert [x["被系統結束"] for x in subs if x["名稱"] == "停頓"] == [True, False]

    w = _fresh()
    opts, checks = _parts_setup(w, {"學員重念 停頓": "被結束"})
    try:
        execute.run_execute(w, checks=checks, parts=opts, skip_precheck=True, log=lambda s: None)
        raise AssertionError("應該失敗")
    except execute.PartFailed as e:
        assert "可能是記憶體不夠，程式被系統結束" in str(e) and "重試一次還是一樣" in str(e)
    assert _calls(w).count("學員重念 停頓") == 2 and "學員重念 收尾 --voice A" not in _calls(w)
    assert wd.read_json(execute.progress_path(w))["步驟"]["學員重念"]["狀態"] == "失敗"


def test_parts_low_disk_before_start_opens_nothing():
    w = _fresh()
    opts, checks = _parts_setup(w, probe=lambda wk: {"硬碟GB": 4.0, "swapGB": None})   # 讀不到 swap 也不出錯
    prog = execute.run_execute(w, checks=checks, parts=opts, skip_precheck=True, log=lambda s: None)
    assert _calls(w) == [] and prog["停止"]
    why = prog["停止原因"]
    assert "硬碟可用空間剩 4.0 GB" in why and "5 GB" in why and "再按一次「開始執行」會接著做" in why
    assert prog["步驟"]["老師名字"]["狀態"] == "停止"


def test_parts_swap_high_while_running_asks_stop():
    w = _fresh()
    seen = {"n": 0}

    def probe(wk):   # 開始前正常；跑起來之後 swap 衝到 9 GB
        seen["n"] += 1
        return {"硬碟GB": 50.0, "swapGB": 1.0 if seen["n"] <= 1 else 9.0}

    opts, checks = _parts_setup(w, {"老師名字 生成": "等停止"}, probe=probe)
    lines: list = []
    prog = execute.run_execute(w, checks=checks, parts=opts, skip_precheck=True, log=lines.append)
    assert _calls(w) == ["老師名字 生成"] and prog["停止"]
    assert "系統拿硬碟頂替" in prog["停止原因"] and "swap" not in prog["停止原因"] and "9.0 GB" in prog["停止原因"] and "8.5 GB" in prog["停止原因"]
    assert prog["步驟"]["老師名字"]["子程式"][0]["swap最高GB"] == 9.0
    assert any("已請目前這一支程式做完這一句就停" in x for x in lines)


def test_resource_numbers_and_no_swap_reader():
    assert execute.parse_swapusage("total = 10240.00M  used = 6144.00M  free = 4096.00M  (encrypted)") == 6.0
    assert execute.parse_swapusage("total = 0.00M  used = 0.00M  free = 0.00M") == 0.0
    assert execute.parse_swapusage("") is None and execute.parse_swapusage("沒有這個") is None
    lim = {"開始前硬碟GB": 5.0, "硬碟GB": 2.5, "swapGB": 8.5}
    assert execute.resource_problem({"硬碟GB": 4.0, "swapGB": None}, lim) is None             # 跑的過程門檻 2.5
    assert "開始前至少要 5 GB" in execute.resource_problem({"硬碟GB": 4.0, "swapGB": None}, lim, starting=True)
    assert execute.resource_problem({"硬碟GB": None, "swapGB": None}, lim, starting=True) is None  # 都讀不到不擋
    assert "8.6 GB" in execute.resource_problem({"硬碟GB": 50.0, "swapGB": 8.6}, lim)
    real = sys.platform
    try:
        execute.sys.platform = "linux"            # 其他系統：swap 讀不到就回 None，不出錯
        assert execute.swap_used_gb() is None
        res = execute.read_resources(Path(tempfile.gettempdir()))
        assert res["swapGB"] is None and res["硬碟GB"] > 0
    finally:
        execute.sys.platform = real


def test_settings_thresholds_from_toml():
    from bookclub import config

    (_DATA / "settings.toml").write_text("[thresholds]\nexecute_min_disk_gb = 3.5\nexecute_max_swap_gb = 7\n",
                                         encoding="utf-8")
    config.load_settings.cache_clear()
    try:
        lim = execute.default_limits()
        assert lim == {"開始前硬碟GB": 5.0, "硬碟GB": 3.5, "swapGB": 7.0}
    finally:
        (_DATA / "settings.toml").unlink()
        config.load_settings.cache_clear()


def test_real_child_command_runs_part():
    # 真的開 `python -m bookclub.cli run part`（不載入模型的那一段：沒有選換成代號的保留原聲學員名字）
    import subprocess

    w = _fresh()
    p = subprocess.run(execute.default_part_command(w, ["保留原聲學員名字", "生成"]), capture_output=True, text=True,
                       encoding="utf-8", env=execute._child_env(), timeout=120)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    assert "沒有選「換成代號」" in p.stdout and execute.PEAK_PREFIX in p.stdout


def test_default_planners_split_by_voice_and_skip_done():
    w = _fresh()
    voices = _DATA / "聲線"
    voices.mkdir(exist_ok=True)
    made = []
    for sex in ("男", "女"):
        for ext, data in ((".wav", b"RIFF"), (".txt", "假的".encode())):
            f = voices / f"{sex}聲_暫定{ext}"
            f.write_bytes(data)
            made.append(f)
    try:
        ctx = {"範圍": [0.0, 180.0], "輸出做法": ["sw"], "標記": "0-3"}
        parts = execute._plan_students(w, ctx)
        groups = students.voice_groups(w, 0.0, 180.0, log=lambda s: None)
        n = len([g for g in groups if g["要做"]])
        assert n >= 1 and len(parts) == 2 * n + 1
        kinds = [a[1] for _, a in parts]
        assert kinds == ["生成"] * n + ["停頓"] + ["收尾"] * n
        assert all("--voice" in a for _, a in parts if a[1] != "停頓")
        assert [a[3] for _, a in parts[:n]] == [a[3] for _, a in parts[n + 1:]]   # 生成、收尾同樣的聲線順序
        items, _ = students.build_items(w)
        ref = {it["id"]: next(g["參考音"] for g in groups if it["學員"] in g["學員"]) for it in items}
        wd.write_json(students.log_path(w), {"句子": [
            {"id": it["id"], "text": it["text"], "生成用文字": it["text"], "slot": it["slot"], "參考音": ref[it["id"]],
             "參考音指紋": tts.ref_fingerprint(ref[it["id"]]), "放回時間格": {"檔案": "y.wav"}} for it in items]})
        assert execute._plan_students(w, ctx) == []                       # 都做過了：不開任何程式
        assert execute._plan_render(w, ctx)[0][1][0] == "組裝"
    finally:
        for f in made:
            f.unlink()


def test_students_three_programs_per_voice():
    # 學員重念分三段跑（假的生成模型、假的對位模型）：生成那一支只生成、不寫紀錄；停頓那一支不生成；收尾才寫紀錄
    import numpy as np

    if not shutil.which("ffmpeg"):
        return
    w = _fresh()
    voices = _DATA / "聲線"
    voices.mkdir(exist_ok=True)
    made = []
    for sex in ("男", "女"):
        for ext, data in ((".wav", b"RIFF"), (".txt", "假的".encode())):
            f = voices / f"{sex}聲_暫定{ext}"
            f.write_bytes(data)
            made.append(f)
    try:
        groups = students.voice_groups(w, log=lambda s: None)
        ref = groups[0]["參考音"]
        calls: list = []

        def factory(ref_wav, ref_text):
            def synth(text, seed, speed):
                calls.append(speed)
                return np.full(int(len(text) * 0.2 / speed * 24000), 0.05, dtype=np.float32), 24000
            return synth

        def never(*a, **k):
            raise AssertionError("這一支程式不該載入生成模型")

        def align(path, text):
            chars = [c for c in text if c.isalnum()]
            return [tts_char(c, i * 0.2, i * 0.2 + 0.2) for i, c in enumerate(chars)]

        from bookclub.pauses import Char as tts_char

        q = dict(check_content=False, log=lambda s: None)
        students.generate_students(w, only_ref="/沒有這個聲線.wav", synth_factory=never, phase="生成", **q)
        assert calls == []                                               # 別的聲線：這一支什麼都不做
        students.generate_students(w, only_ref=ref, synth_factory=factory, phase="生成", **q)
        n = len(calls)
        assert n >= 1 and not students.log_path(w).exists()              # 生成那一支不寫紀錄
        students.generate_students(w, synth_factory=lambda *a: never, align=align, phase="停頓", **q)
        assert (students.out_dir(w) / tts.PAUSE_CACHE).is_file()
        students.generate_students(w, only_ref=ref, synth_factory=factory, align=never, phase="收尾", **q)
        assert execute.students_done(w, None, None)[0]
        assert [g["要做"] for g in students.voice_groups(w, log=lambda s: None)] == [0]
    finally:
        for f in made:
            f.unlink()

def test_time_text_never_shows_60_seconds():
    """10-01 走查：59.96 秒以前寫成「0:54:60.0」。"""
    from bookclub import cutsuggest, namespage

    assert execute.t1(3299.96) == "0:55:00.0"
    assert cutsuggest._hms(59.97) == "0:01:00.0"
    assert namespage._fmt_hms1(3599.99) == "1:00:00.0"


def test_analyze_keeps_elapsed_when_everything_reused():
    """10-01 走查：接著做、每一步都沿用時，總覽不要寫「影片分析花了 0.1 秒」。"""
    from bookclub.analyze import keep_elapsed_if_all_reused

    old = {"elapsed": {"1_轉文字": 600.0, "總耗時": 1800.0}}
    assert keep_elapsed_if_all_reused({"1_轉文字": 0.0, "總耗時": 0.1}, old)["總耗時"] == 1800.0
    assert keep_elapsed_if_all_reused({"1_轉文字": 300.0, "總耗時": 320.0}, old)["總耗時"] == 320.0
    assert keep_elapsed_if_all_reused({"1_轉文字": 0.0, "總耗時": 0.1}, None)["總耗時"] == 0.1


def _all_generated(w: Path) -> list[dict]:
    """假的學員紀錄：每一段都生成好了，快取、聲音檔各一份（不載入模型）。"""
    items, _ = students.build_items(w)
    od = students.out_dir(w)
    od.mkdir(parents=True, exist_ok=True)
    cache, recs = {}, []
    for it in items:
        (od / f"{it['id']}_第1次.wav").write_bytes(b"RIFF")
        cache[f"{it['id']}|1|42|1.0|{it['text']}|參考音#abc"] = {"seed": 42}
        recs.append({"id": it["id"], "text": it["text"], "生成用文字": it["text"], "slot": it["slot"], "段落": it["段落"],
                     "學員": it["學員"], "放回時間格": {"檔案": f"{it['id']}_放回時間格.wav"}})
    wd.write_json(od / tts.ATTEMPT_CACHE, cache)
    wd.write_json(students.log_path(w), {"句子": recs})
    return items


def test_redo_returned_regenerates_only_returned_then_step5_unseen():
    """10-01 第三批：第 4 步一鍵只重做第 5 步退回的：退回的那一段清掉重新生成（舊檔搬走、別段不動），
    剪掉這種沒有聲音要生成的只重新組裝；組裝一定重做；做完那幾筆在第 5 步回到還沒看、標重做過。"""
    from bookclub import finalcheck, proclog

    w = _fresh()
    items = _all_generated(w)
    target, other = items[0], items[1]
    log = {"產生時間": "t1", "片段": None, "紀錄": [
        {"類型": "學員重念", "原片": list(target["slot"]), "成品": list(target["slot"]), "做了什麼": "學員1 用女聲 AI 重念",
         "檔案": "a.wav", "覆核項目": [f"學員段落:{target['段落']}"], "文字": target["text"]},
        {"類型": "刪除", "原片": [0.0, 3.6], "成品": [0.0, 0.0], "做了什麼": "刪除 3.6 秒（聲音畫面一起刪）", "檔案": None,
         "覆核項目": ["刪除段落:S1"]}]}
    wd.write_json(proclog.log_path(w), log)
    k1, k2 = (finalcheck.record_key(r) for r in log["紀錄"])
    finalcheck.decide_record(w, k1, "退回重做", "念錯字")
    finalcheck.decide_record(w, k2, "退回重做", "剪太多")
    plan = finalcheck.redo_plan(w)
    assert [e["做法"] for e in plan] == ["重新生成", "重新組裝"] and [u["id"] for u in plan[0]["生成"]] == [target["id"]]
    regen, rendered = [], []

    def stu(wk, ctx):   # 假的學員重念：紀錄裡沒有的才生成（跟真的生成程式同一個規則）
        rec = wd.read_json(students.log_path(wk))
        have = {r["id"] for r in rec["句子"]}
        for it in students.build_items(wk)[0]:
            if it["id"] not in have:
                regen.append(it["id"])
                rec["句子"].append({"id": it["id"], "text": it["text"], "slot": it["slot"], "放回時間格": {"檔案": "新.wav"}})
        wd.write_json(students.log_path(wk), rec)

    def render(wk, ctx):
        rendered.append(1)
        wd.write_json(proclog.log_path(wk), {**log, "產生時間": "t2", "紀錄": [{**log["紀錄"][0], "檔案": "b.wav"}, log["紀錄"][1]]})

    def done_students(wk, ctx):
        have = {r["id"] for r in wd.read_json(students.log_path(wk))["句子"]}
        return all(it["id"] in have for it in students.build_items(wk)[0]), "假的"

    runners = {"老師名字": lambda wk, c: None, "學員重念": stu, "保留原聲學員名字": lambda wk, c: None, "組裝": render}
    checks = {"老師名字": lambda wk, c: (True, "假的"), "學員重念": done_students,
              "保留原聲學員名字": lambda wk, c: (True, "假的"), "組裝": lambda wk, c: (True, "假的：成品比較新")}
    prog = execute.run_execute(w, runners=runners, checks=checks, skip_precheck=True, redo_returned=True, log=lambda s: None)
    assert regen == [target["id"]] and rendered == [1]                    # 只重新生成退回的那一段；組裝一定重做
    assert prog["步驟"]["組裝"]["狀態"] == "做完" and prog["步驟"]["老師名字"]["狀態"] == "跳過"
    od = students.out_dir(w)
    assert not (od / f"{target['id']}_第1次.wav").exists() and (od / f"{other['id']}_第1次.wav").exists()
    assert list(od.glob(f"重做前_*/{target['id']}_第1次.wav"))             # 舊的聲音檔搬到備份，沒有刪
    cache = wd.read_json(od / tts.ATTEMPT_CACHE)
    assert not any(k.startswith(target["id"] + "|") for k in cache) and any(k.startswith(other["id"] + "|") for k in cache)
    chk = wd.read_json(finalcheck.check_path(w))
    assert k1 not in chk["逐筆"] and k2 not in chk["逐筆"] and "重做中" not in chk and len(chk["重做過"]["項目"]) == 2
    d = finalcheck.page_data(w)
    got = {r["鍵"]: r["重做過"] for r in d["紀錄"]}
    assert got[k1]["做法"] == "重新生成" and got[k1]["新版本"] is True and got[k1]["原因"] == "念錯字"
    assert got[k2]["做法"] == "重新組裝" and got[k2]["新版本"] is False
    assert all(r["結果"] is None for r in d["紀錄"]) and not finalcheck.redo_list(w)["項目"]
    # 沒有退回的時候照常（不清任何東西、組裝做過就跳過）
    prog = execute.run_execute(w, runners=runners, checks=checks, skip_precheck=True, redo_returned=True, log=lambda s: None)
    assert prog["步驟"]["組裝"]["狀態"] == "跳過" and rendered == [1]


def test_redo_list_warns_when_text_and_range_unchanged():
    """10-02：退回的那一句文字、範圍都沒改：第 4 步寫清楚會換一種念法重新生成、是第幾版；改了字就照改過的。"""
    from bookclub import finalcheck, proclog

    w = _fresh()
    items = _all_generated(w)
    t = items[0]
    log = {"產生時間": "t1", "片段": None, "紀錄": [
        {"類型": "學員重念", "原片": list(t["slot"]), "成品": list(t["slot"]), "做了什麼": "x", "檔案": "a.wav",
         "覆核項目": [f"學員段落:{t['段落']}"], "文字": t["text"]}]}
    wd.write_json(proclog.log_path(w), log)
    finalcheck.decide_record(w, finalcheck.record_key(log["紀錄"][0]), "退回重做", "念錯字")
    it = finalcheck.redo_list(w)["項目"][0]
    assert it["沒改"] and "換一種念法" in it["說明"] and "第 2 版" in it["說明"] and it["第幾版"] == 2
    assert "種子" not in it["說明"] and "多半" not in it["說明"]
    rec = wd.read_json(students.log_path(w))
    rec["句子"][0]["text"] = "上一次念的字"                                    # 這次要念的字跟上一次不一樣
    wd.write_json(students.log_path(w), rec)
    it = finalcheck.redo_list(w)["項目"][0]
    assert not it["沒改"] and "換一種念法" not in it["說明"] and "照改過的內容" in it["說明"]


def test_redo_unchanged_regenerates_with_next_seed_and_keeps_old_versions():
    """10-02 第四批：第 5 步退回、文字和範圍都沒改的，重做時換一種念法：用還沒用過的下一個種子（照重試的順序），
    舊的那一版留在紀錄裡（聲音檔搬到備份資料夾），新的成為選定的版本、記第幾版；同一筆再退回再換下一個，不回到用過的。
    學員重念用假的生成程式、照第 4 步一步一支程式（生成、停頓、收尾各跑一次）走真的 run_generation。"""
    import numpy as np

    from bookclub import finalcheck, proclog

    w = _fresh()
    items, _ = students.build_items(w)
    target = items[0]
    od = students.out_dir(w)
    od.mkdir(parents=True, exist_ok=True)
    ref = w / "假聲線.wav"
    ref.write_bytes(b"RIFF-fake")
    seeds: list = []

    def synth(text, seed, speed):
        seeds.append(seed)
        return np.zeros(int(24000 * 1.0), dtype=np.float32) + 0.01, 24000

    def prep(it):
        return {**it, "slot": list(it["slot"]), "slot_s": it["slot"][1] - it["slot"][0], "生成用文字": it["text"],
                "發音對照": []}

    def gen(wk, ctx=None):   # 假的學員重念：紀錄裡沒有的（被清掉的）才生成；跟第 4 步一樣分三支程式跑
        rec = wd.read_json(students.log_path(wk), default=None) or {"句子": []}
        done = {r["id"]: r for r in rec["句子"]}
        todo = [prep(it) for it in students.build_items(wk)[0] if it["id"] not in done]
        if not todo:
            return
        save = lambda: wd.write_json(students.log_path(wk), {"句子": list(done.values())})   # noqa: E731
        texts = {it["id"]: it["text"] for it in todo}
        for phase in tts.PHASES:
            tts.run_generation(wk, todo, od, ref, "假的", 0.15, save=save, done=done, role="學員", tag="學員聲音",
                               check_content=True, check_similarity=False, use_pauses=False,
                               synth=synth if phase != "停頓" else None, hear=lambda path: texts[Path(path).name.split("_第")[0]],
                               phase=phase, log=lambda s: None)

    gen(w)                                                       # 第一次：每一段都從 42 開始
    assert set(seeds) == {42}
    rec0 = {r["id"]: r for r in wd.read_json(students.log_path(w))["句子"]}
    assert rec0[target["id"]]["嘗試"][0]["種子"] == 42 and "第幾版" not in rec0[target["id"]]

    log = {"產生時間": "t1", "片段": None, "紀錄": [
        {"類型": "學員重念", "原片": list(target["slot"]), "成品": list(target["slot"]), "做了什麼": "x", "檔案": "a.wav",
         "覆核項目": [f"學員段落:{target['段落']}"], "文字": target["text"]}]}
    renders = []

    def render(wk, ctx):
        renders.append(1)
        wd.write_json(proclog.log_path(wk), {**log, "產生時間": f"t{len(renders) + 1}",
                                             "紀錄": [{**log["紀錄"][0], "檔案": f"b{len(renders)}.wav"}]})

    runners = {"老師名字": lambda wk, c: None, "學員重念": gen, "保留原聲學員名字": lambda wk, c: None, "組裝": render}
    checks = {"老師名字": lambda wk, c: (True, "假的"),
              "學員重念": lambda wk, c: (all(i["id"] in {r["id"] for r in wd.read_json(students.log_path(wk))["句子"]}
                                         for i in students.build_items(wk)[0]), "假的"),
              "保留原聲學員名字": lambda wk, c: (True, "假的"), "組裝": lambda wk, c: (True, "假的")}
    want = [1, 2026, 2027]   # 照重試的順序（42、1、2026），用完了接著往下
    for rnd, seed in enumerate(want, start=2):
        wd.write_json(proclog.log_path(w), {**log, "產生時間": wd.read_json(proclog.log_path(w))["產生時間"]}
                      if proclog.log_path(w).exists() else log)
        cur = wd.read_json(proclog.log_path(w))
        key = finalcheck.record_key(cur["紀錄"][0])
        finalcheck.decide_record(w, key, "退回重做", f"音質不好 {rnd}")
        it = finalcheck.redo_list(w)["項目"][0]
        assert it["沒改"] and it["第幾版"] == rnd and "換一種念法" in it["說明"]
        seeds.clear()
        execute.run_execute(w, runners=runners, checks=checks, skip_precheck=True, redo_returned=True, log=lambda s: None)
        assert seeds and set(seeds) == {seed}, (rnd, seeds)      # 只重新生成退回的那一段，換下一個沒用過的（長度不合改語速是同一個）
        r = {x["id"]: x for x in wd.read_json(students.log_path(w))["句子"]}[target["id"]]
        assert r["第幾版"] == rnd and r["換一種念法"] and r["嘗試"][r["選定"] - 1]["種子"] == seed
        assert [v["第幾版"] for v in r["以前的版本"]] == list(range(1, rnd))      # 舊的那幾版留在紀錄裡
        assert [v["種子"] for v in r["以前的版本"]] == [42, *want][:rnd - 1]
        last_old = r["以前的版本"][-1]
        assert (w / last_old["備份資料夾"] / f"{target['id']}.wav").is_file()      # 上一版的聲音檔留著備份
        assert last_old["退回原因"] == f"音質不好 {rnd}"
        others = {x["id"]: x for x in wd.read_json(students.log_path(w))["句子"]}
        assert all("第幾版" not in others[i["id"]] for i in items[1:])            # 別段不動
        d = finalcheck.page_data(w)
        got = d["紀錄"][0]["重做過"]
        assert got["第幾版"] == rnd and got["換一種念法"] and got["做法"] == "重新生成"   # 第 5 步看得出是第幾版
    # 文字或範圍改了：照原本的規則（不避開，從 42 開始）
    entry = wd.read_json(od / tts.REDO_VERSIONS)[target["id"]]
    assert entry["避開"] == [1, 42, 2026]                                          # 第 4 版生成時避開的（前三版用過的）
    assert tts.redo_avoid(entry, {**prep(target), "text": "改過的字"}) == []
    assert tts.redo_avoid(entry, {**prep(target), "slot": [target["slot"][0] + 0.5, target["slot"][1]]}) == []
    assert tts.redo_avoid(entry, prep(target)) == [1, 42, 2026]


def test_redo_stopped_midway_finishes_on_next_run():
    """重做到一半按停止：清掉的那幾句下次接著生成；下一次組裝做完一樣收尾（回到還沒看）。"""
    from bookclub import finalcheck, proclog

    w = _fresh()
    items = _all_generated(w)
    log = {"產生時間": "t1", "片段": None, "紀錄": [
        {"類型": "學員重念", "原片": list(items[0]["slot"]), "成品": list(items[0]["slot"]), "做了什麼": "x",
         "檔案": "a.wav", "覆核項目": [f"學員段落:{items[0]['段落']}"], "文字": "y"}]}
    wd.write_json(proclog.log_path(w), log)
    k1 = finalcheck.record_key(log["紀錄"][0])
    finalcheck.decide_record(w, k1, "退回重做", "念錯字")
    calls: list = []
    runners, checks = _fake(calls, {"失敗": "學員重念"})
    try:
        execute.run_execute(w, runners=runners, checks=checks, skip_precheck=True, redo_returned=True, log=lambda s: None)
        raise AssertionError("應該失敗")
    except RuntimeError:
        pass
    assert wd.read_json(finalcheck.check_path(w))["重做中"]["項目"][0]["鍵"] == k1
    runners, checks = _fake(calls)
    execute.run_execute(w, runners=runners, checks=checks, skip_precheck=True, log=lambda s: None)   # 一般的開始執行
    chk = wd.read_json(finalcheck.check_path(w))
    assert "重做中" not in chk and k1 not in chk["逐筆"] and chk["重做過"]["項目"][0]["鍵"] == k1


def test_redo_planner_opens_only_cleared_voice():
    """分開程式跑（一步一支程式）：清掉一段之後，學員重念只開那一個聲線的生成、停頓、收尾。"""
    from bookclub import finalcheck

    w = _fresh()
    voices = _DATA / "聲線"
    voices.mkdir(exist_ok=True)
    made = []
    for sex in ("男", "女"):
        for ext, data in ((".wav", b"RIFF"), (".txt", "假的".encode())):
            f = voices / f"{sex}聲_暫定{ext}"
            f.write_bytes(data)
            made.append(f)
    try:
        ctx = {"範圍": [0.0, 180.0], "輸出做法": ["sw"], "標記": "0-3"}
        groups = students.voice_groups(w, 0.0, 180.0, log=lambda s: None)
        items, _ = students.build_items(w)
        ref = {it["id"]: next(g["參考音"] for g in groups if it["學員"] in g["學員"]) for it in items}
        wd.write_json(students.log_path(w), {"句子": [
            {"id": it["id"], "text": it["text"], "生成用文字": it["text"], "slot": it["slot"], "參考音": ref[it["id"]],
             "參考音指紋": tts.ref_fingerprint(ref[it["id"]]), "放回時間格": {"檔案": "y.wav"}} for it in items]})
        assert execute._plan_students(w, ctx) == []
        finalcheck.clear_generated(w, "學員", [items[0]["id"]], "2026-10-01T23:00:00")
        parts = execute._plan_students(w, ctx)
        assert [a[1] for _, a in parts] == ["生成", "停頓", "收尾"] and parts[0][1][3] == ref[items[0]["id"]]
        assert "（1 段）" in parts[0][0]
    finally:
        for f in made:
            f.unlink()


def test_current_steps_and_output_methods():
    """10-01 第三批 4：執行步驟表格沒在執行時顯示現在的狀態（跟做過沒有同一個判斷）；
    8：輸出方式標準輸出（軟體編碼）一定有、排第一個，其他只在這台電腦支援時才列。"""
    w = _fresh()
    now = execute.current_steps(w)
    assert set(now) == {k for k, _ in execute.STEPS} and all("說明" in v for v in now.values())
    assert now["組裝"]["做好了"] is False and now["組裝"]["說明"] == "還沒組裝"
    opts = execute.method_options()
    assert opts[0] == ["sw", "標準輸出"] and execute.default_methods() == ["sw"]
    if sys.platform != "darwin":
        assert [m for m, _ in opts] == ["sw"]
    assert not any("編碼" in label for _, label in opts)     # 畫面上不用「硬體編碼／軟體編碼」


def _redo_setup(w: Path, n: int = 3):
    """10-02 第五批：學員重念先全部生成好（假的生成程式，種子 42），再把前 n 段在第 5 步退回（文字、範圍都沒改）。
    回傳（targets, 假的生成程式工廠, runners 工廠, checks, 生成時念過的句子）。生成照第 4 步一步一支程式（生成、停頓、收尾）。"""
    import numpy as np

    from bookclub import finalcheck, proclog

    items, _ = students.build_items(w)
    targets = items[:n]
    od = students.out_dir(w)
    od.mkdir(parents=True, exist_ok=True)
    ref = w / "假聲線.wav"
    ref.write_bytes(b"RIFF-fake")
    said: list = []

    def prep(it):
        return {**it, "slot": list(it["slot"]), "slot_s": it["slot"][1] - it["slot"][0], "生成用文字": it["text"],
                "發音對照": []}

    def gen_factory(stop_after: int | None = None, per_item: bool = False, stop_after_items: int | None = None):
        """stop_after：生成到第幾次就按停止（模擬記憶體門檻停下來）；per_item：一段一段做完（做完就寫回紀錄），
        stop_after_items：做完（寫回）幾段之後按停止。"""
        def synth(text, seed, speed):
            said.append((text, seed, speed))
            if stop_after is not None and len(said) >= stop_after:
                execute.request_stop(w)
            return np.zeros(int(24000 * 1.0), dtype=np.float32) + 0.01, 24000

        def gen(wk, ctx=None):
            rec = wd.read_json(students.log_path(wk), default=None) or {"句子": []}
            done = {r["id"]: r for r in rec["句子"]}
            todo = [prep(it) for it in students.build_items(wk)[0] if it["id"] not in done]
            save = lambda: wd.write_json(students.log_path(wk), {"句子": list(done.values())})   # noqa: E731
            texts = {it["id"]: it["text"] for it in todo}
            for gi, group in enumerate([[it] for it in todo] if per_item else [todo] if todo else []):
                if stop_after_items is not None and gi == stop_after_items:
                    execute.request_stop(w)
                for phase in tts.PHASES:
                    tts.run_generation(wk, group, od, ref, "假的", 0.15, save=save, done=done, role="學員", tag="學員聲音",
                                       check_content=True, check_similarity=False, use_pauses=False,
                                       synth=synth if phase != "停頓" else None,
                                       hear=lambda path: texts[Path(path).name.split("_第")[0]], phase=phase, log=lambda s: None)
        return gen

    gen_factory()(w)                                            # 第一次：全部生成好（種子 42）
    log = {"產生時間": "t1", "片段": None, "紀錄": [
        {"類型": "學員重念", "原片": list(t["slot"]), "成品": list(t["slot"]), "做了什麼": "x", "檔案": "a.wav",
         "覆核項目": [f"學員段落:{t['段落']}"], "文字": t["text"]} for t in targets]}
    wd.write_json(proclog.log_path(w), log)
    for r in log["紀錄"]:
        finalcheck.decide_record(w, finalcheck.record_key(r), "退回重做", "音質不好")
    renders: list = []

    def render(wk, ctx):
        renders.append(1)
        wd.write_json(proclog.log_path(wk), {**log, "產生時間": "t2"})

    def runners(gen):
        return {"老師名字": lambda wk, c: None, "學員重念": gen, "保留原聲學員名字": lambda wk, c: None, "組裝": render}

    checks = {"老師名字": lambda wk, c: (True, "假的"),
              "學員重念": lambda wk, c: execute.students_done(wk, None, None) if False else
              (all(i["id"] in {r["id"] for r in wd.read_json(students.log_path(wk))["句子"]} for i in students.build_items(wk)[0]),
               "假的"),
              "保留原聲學員名字": lambda wk, c: (True, "假的"), "組裝": lambda wk, c: (True, "假的")}
    said.clear()
    return targets, gen_factory, runners, checks, said, renders


def test_redo_stopped_after_one_generated_resumes_without_redoing_it():
    """10-02 第五批（預演時遇到的）：退回重做 3 段，第 1 段的新版生成好了、還沒寫回紀錄就停下來（記憶體門檻）。
    停在一半時：3 段都不在紀錄裡、版本已經記過；第 4 步寫「接著做」、第幾版正確；組裝擋下來（不然這幾段會是原聲）。
    再按一次「開始執行」：缺的補回來、已經生成好的那一段從快取沿用（不重新生成）、不會多一個以前的版本或跳號、用過的念法紀錄不變。"""
    from bookclub import finalcheck, render as render_mod

    w = _fresh()
    targets, gen_factory, runners, checks, said, renders = _redo_setup(w)
    ids = [t["id"] for t in targets]
    prog = execute.run_execute(w, runners=runners(gen_factory(stop_after=1)), checks=checks, skip_precheck=True,
                               redo_returned=True, log=lambda s: None)
    assert prog.get("停止") and prog["步驟"]["學員重念"]["狀態"] == "停止" and not renders
    assert len(said) == 1 and said[0][1:] == (1, 1.0)               # 第 1 段換一種念法（種子 1）生成好了
    od = students.out_dir(w)
    have = {r["id"] for r in wd.read_json(students.log_path(w))["句子"]}
    assert not set(ids) & have                                      # 3 段都清掉了、新版還沒寫回
    vers = wd.read_json(od / tts.REDO_VERSIONS)
    assert all(len(vers[i]["以前的版本"]) == 1 and vers[i]["避開"] == [42] for i in ids)
    # 停在一半時的畫面與組裝
    assert sorted(finalcheck.redo_pending(w)) == sorted(ids)
    items = finalcheck.redo_list(w)["項目"]
    assert all(it["接著做"] and it["第幾版"] == 2 and it["沒改"] and "接著做" in it["說明"] for it in items), items
    st = execute.status(w)
    assert st["重做中"] and len(st["退回清單"]) == 3
    assert not execute.students_done(w, None, None)[0]
    page = finalcheck.page_data(w)                                   # 第 5 步照樣打得開（看的是上一次組好的成品）
    assert len(page["紀錄"]) == 3
    try:
        render_mod.render_video(w, 0.0, 180.0, tag="測試")
        raise AssertionError("退回重做還沒做完不該組裝")
    except RuntimeError as e:
        assert "還沒重新生成" in str(e) and "開始執行" in str(e)
    except FileNotFoundError:
        pass                                                         # 沒有 ffmpeg 做不出假影片：找原片那一步就停了
    # 再按一次「開始執行」
    said.clear()
    prog = execute.run_execute(w, runners=runners(gen_factory()), checks=checks, skip_precheck=True,
                               redo_returned=True, log=lambda s: None)
    assert not prog.get("停止") and renders == [1]
    assert (targets[0]["text"], 1, 1.0) not in said                  # 已經生成好的那一段沒有重新生成（長度不合改語速是另一回事）
    assert {x[0] for x in said if x[2] == 1.0} == {t["text"] for t in targets[1:]} and {x[1] for x in said} == {1}
    recs = {r["id"]: r for r in wd.read_json(students.log_path(w))["句子"]}
    for i in ids:
        r = recs[i]
        assert r["第幾版"] == 2 and r["換一種念法"] and r["嘗試"][r["選定"] - 1]["種子"] == 1
        assert [v["第幾版"] for v in r["以前的版本"]] == [1] and r["以前的版本"][0]["種子"] == 42
    vers = wd.read_json(od / tts.REDO_VERSIONS)
    assert all(len(vers[i]["以前的版本"]) == 1 and vers[i]["避開"] == [42] for i in ids)
    assert len(list(od.glob("重做前_*"))) == 1                       # 沒有因為再按一次多搬一份
    assert (od / f"{ids[0]}_第1次.wav").is_file()
    chk = wd.read_json(finalcheck.check_path(w))
    assert "重做中" not in chk and len(chk["重做過"]["項目"]) == 3
    assert not finalcheck.redo_pending(w)


def test_redo_stopped_after_one_written_back_does_not_jump_version():
    """退回重做 3 段，第 1 段整段做完、已經寫回紀錄（第 2 版）才停下來；再按一次：第 1 段不會被當成「以前的版本」再重做成第 3 版。"""
    from bookclub import finalcheck

    w = _fresh()
    targets, gen_factory, runners, checks, said, renders = _redo_setup(w)
    ids = [t["id"] for t in targets]
    execute.run_execute(w, runners=runners(gen_factory(per_item=True, stop_after_items=1)), checks=checks, skip_precheck=True,
                        redo_returned=True, log=lambda s: None)
    recs = {r["id"]: r for r in wd.read_json(students.log_path(w))["句子"]}
    assert recs[ids[0]]["第幾版"] == 2 and not set(ids[1:]) & set(recs)
    assert finalcheck.redo_pending(w) == ids[1:]
    it0 = next(it for it in finalcheck.redo_list(w)["項目"] if it["生成"][0]["id"] == ids[0])
    assert it0["第幾版"] == 2 and "都重新生成好了" in it0["說明"]
    said.clear()
    execute.run_execute(w, runners=runners(gen_factory(per_item=True)), checks=checks, skip_precheck=True,
                        redo_returned=True, log=lambda s: None)
    assert {x[0] for x in said} == {t["text"] for t in targets[1:]}   # 做完寫回的第 1 段完全沒動
    recs = {r["id"]: r for r in wd.read_json(students.log_path(w))["句子"]}
    assert all(recs[i]["第幾版"] == 2 and len(recs[i]["以前的版本"]) == 1 for i in ids)
    vers = wd.read_json(students.out_dir(w) / tts.REDO_VERSIONS)
    assert all(len(vers[i]["以前的版本"]) == 1 and vers[i]["避開"] == [42] for i in ids)


def test_memory_status_warns_before_start_without_changing_threshold():
    """10-02 第五批：開始前就看記憶體（系統拿硬碟頂替的量）：偏滿就提醒怎麼處理；讀不到（非 macOS）不出錯；門檻照設定。"""
    ok = execute.memory_status(1.0, 8.5)
    assert ok["讀得到"] and not ok["偏滿"] and "1.0 GB" in ok["說明"] and "8.5 GB" in ok["說明"] and not ok["怎麼處理"]
    tight = execute.memory_status(6.2, 8.5)
    assert tight["偏滿"] and "重開機" in tight["怎麼處理"] and "瀏覽器" in tight["怎麼處理"]
    none = execute.memory_status(None, 8.5)
    assert not none["讀得到"] and not none["偏滿"] and "讀不到" in none["說明"]
    for m in (ok, tight, none):
        assert "swap" not in m["說明"] + m["怎麼處理"] and "很簡單" not in m["怎麼處理"]
    assert execute.parse_swapusage("") is None
    old = sys.platform
    try:   # 非 macOS：讀不到、不出錯
        sys.platform = "linux"
        assert execute.memory_status()["讀得到"] is False
    finally:
        sys.platform = old
    # 總檢查的摘要帶記憶體狀況
    w = _fresh()
    assert "記憶體" in execute.final_check(w)["摘要"]
    # 命令列（不跳過前置檢查）開始時印出來
    lines: list = []
    orig, orig_pre = execute.swap_used_gb, execute.precheck
    try:
        execute.swap_used_gb = lambda: 7.0
        execute.precheck = lambda wk: {"可以開始": True, "缺": [], "提醒": []}
        runners, checks = _fake([])
        execute.run_execute(w, runners=runners, checks=checks, only_steps=["老師名字"], log=lines.append)
    finally:
        execute.swap_used_gb, execute.precheck = orig, orig_pre
    text = "\n".join(lines)
    assert "現在 7.0 GB" in text and "偏滿" in text and "重開機" in text and "swap" not in text


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
