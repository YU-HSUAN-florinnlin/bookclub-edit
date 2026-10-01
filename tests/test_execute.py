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
    assert "swap" in prog["停止原因"] and "9.0 GB" in prog["停止原因"] and "8.5 GB" in prog["停止原因"]
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
    """10-02：退回的那一句文字、範圍都沒改：第 4 步寫清楚重新生成多半跟上一版一樣；改了字就不寫。"""
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
    assert it["沒改"] and "多半會跟上一版一樣" in it["說明"]
    rec = wd.read_json(students.log_path(w))
    rec["句子"][0]["text"] = "上一次念的字"                                    # 這次要念的字跟上一次不一樣
    wd.write_json(students.log_path(w), rec)
    it = finalcheck.redo_list(w)["項目"][0]
    assert not it["沒改"] and "上一版" not in it["說明"]


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


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
