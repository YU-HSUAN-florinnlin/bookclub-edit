"""10-02 第七批（A3＋B3）：Claude 那三步（段落分析、建議刪除段落、人名清單）沒跑成功要看得到；claude 找法一致。

全部假資料（借 tests/test_refpick_mainflow.py 的假影片、假逐字稿、假聲紋模型），假的 call_claude 丟例外，
不打網路、不載入模型。

獨立可跑：.venv/bin/python tests/test_claude_steps.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

_DATA = Path(tempfile.mkdtemp())
(_DATA / "名冊.csv").write_text("中文名,其他寫法,英文代號,聲線,性別\n小美,,Amy,,女\n阿明,,Tom,,男\n", encoding="utf-8")
os.environ["BOOKCLUB_DATA_DIR"] = str(_DATA)

import fake_workdir  # noqa: E402
import test_refpick_mainflow as tm  # noqa: E402

from bookclub import refpick  # noqa: E402


def test_find_claude_same_for_doctor_and_call():
    """PATH 裡沒有 claude、~/.local/bin/claude 有 → doctor 跟段落分析都找得到同一個；兩邊都沒有 → 都找不到，
    call_claude 的錯誤寫清楚。"""
    from bookclub import doctor, system_info, turns

    home = Path(tempfile.mkdtemp())
    exe = home / ".local" / "bin" / "claude"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\necho '{\"段落\": []}'\n", encoding="utf-8")
    exe.chmod(0o755)
    old_home, old_which = os.environ.get("HOME"), shutil.which
    os.environ["HOME"] = str(home)
    shutil.which = lambda name, *a, **k: None
    try:
        assert system_info.find_claude() == str(exe)
        chk = doctor.check_claude_cli()
        assert chk.ok and chk.detail == str(exe)
        assert "段落" in turns.call_claude("hi", "假的模型")   # 用的是 ~/.local/bin/claude
        exe.unlink()
        assert system_info.find_claude() is None and not doctor.check_claude_cli().ok
        try:
            turns.call_claude("hi", "假的模型")
            raise AssertionError("找不到 claude 還沒出錯")
        except RuntimeError as e:
            assert "找不到 claude 指令" in str(e)
    finally:
        shutil.which = old_which
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home


def test_claude_steps_failed_are_visible():
    """假的 call_claude 丟例外 → 分析結果.json 的「要注意」與「Claude沒跑成功」列出三步、怎麼重跑；
    網頁的 /api/state 帶得到；人名清單沒產出 → 第 3 步 ③ 不能標完成、第 4 步 precheck 提醒（10-08 起不擋）。"""
    from bookclub import analyze, execute, review, roomtone, server, students
    from bookclub import turns as turns_mod

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        video = root / "假影片.wav"
        tm._make_audio(video)
        w = root / "工作區"
        (w / "transcript").mkdir(parents=True)
        (w / "transcript" / "merged.json").write_text(json.dumps(tm._merged(), ensure_ascii=False), encoding="utf-8")
        tm._make_audio(w / "audio.flac")

        def broken_claude(prompt, model, timeout_s=600):
            raise RuntimeError("claude -p 失敗：假的逾時")

        fake_time = types.SimpleNamespace(time=__import__("time").time, sleep=lambda s: None)   # 重送前不用真的等
        patches = [
            (refpick, "_load_embed_model", lambda: tm._FakeInference()),
            (refpick, "_transcribe_candidate_text", lambda client, wav, data=None: "假的逐字稿初稿"),
            (refpick, "GROQ_CALL_INTERVAL_S", 0.0),
            (turns_mod, "call_claude", broken_claude),
            (turns_mod, "time", fake_time),
            (students, "estimate_pitches", lambda *a, **k: None),
            (roomtone, "ensure_info", lambda *a, **k: None),
        ]
        olds = [(m, n, getattr(m, n)) for m, n, _ in patches]
        old_key = os.environ.get("GROQ_API_KEY")
        os.environ["GROQ_API_KEY"] = "gsk_假的金鑰"
        try:
            for m, n, v in patches:
                setattr(m, n, v)
            result = analyze.run_analyze(video, w, skip_overlap=True, ref_n=2)
        finally:
            for m, n, v in olds:
                setattr(m, n, v)
            if old_key is None:
                os.environ.pop("GROQ_API_KEY", None)
            else:
                os.environ["GROQ_API_KEY"] = old_key

        saved = json.loads((w / "分析結果.json").read_text(encoding="utf-8"))
        steps = [f["步驟"] for f in saved["Claude沒跑成功"]]
        assert sorted(steps) == sorted(["段落分析", "建議刪除段落", "人名清單"]), steps
        assert all("重跑" in m for m in saved["要注意"]) and len(saved["要注意"]) == 3
        assert any("bookclub run people" in f["怎麼重跑"] for f in saved["Claude沒跑成功"])
        assert result["Claude沒跑成功"] == saved["Claude沒跑成功"]
        st = server.build_state(w)
        assert [f["步驟"] for f in st["Claude沒跑成功"]] == steps and st["要注意"] == saved["要注意"]
        assert review.people_list_missing(w)

    # ③ 不能標完成、precheck 提醒（用假工作區：其他都齊，只拿掉人名清單）
    base = Path(tempfile.mkdtemp()) / "base"
    fake_workdir.make(base)
    w = base / "工作區"
    from bookclub import personnames

    personnames.people_path(w).unlink()
    data = review.page_data(w)
    assert data["人名清單沒跑成功"] and review.PEOPLE_MISSING in data["開始前待處理"]["名字"]
    try:
        review.set_prep(w, "名字", True)
        raise AssertionError("人名清單沒跑成功還能標完成")
    except ValueError as e:
        assert "人名清單沒跑成功" in str(e)
    pre = execute.precheck(w)   # 10-08 宇軒（流程簡化）：只提醒、不擋
    assert not any("人名清單沒跑成功" in m for m in pre["缺"]) and any("人名清單沒跑成功" in m for m in pre["提醒"])


def test_empty_turns_reply_is_a_claude_failure():
    """10-03 第八批（#4）：段落分析每一次都回 {"段落": []}（格式對、內容空）→ 不再默默留缺口：
    重送一次還是空 → 寫進「Claude沒跑成功」，不產生 段落_文字.json。"""
    from bookclub import analyze, roomtone, students
    from bookclub import turns as turns_mod

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        video = root / "假影片.wav"
        tm._make_audio(video)
        w = root / "工作區"
        (w / "transcript").mkdir(parents=True)
        (w / "transcript" / "merged.json").write_text(json.dumps(tm._merged(), ensure_ascii=False), encoding="utf-8")
        tm._make_audio(w / "audio.flac")
        calls = [0]

        def empty_claude(prompt, model, timeout_s=600):
            if prompt.startswith(turns_mod.PROMPT):
                calls[0] += 1
                return '{"段落": []}'
            raise RuntimeError("claude -p 失敗：假的逾時")

        patches = [
            (refpick, "_load_embed_model", lambda: tm._FakeInference()),
            (refpick, "_transcribe_candidate_text", lambda client, wav, data=None: "假的逐字稿初稿"),
            (refpick, "GROQ_CALL_INTERVAL_S", 0.0),
            (turns_mod, "call_claude", empty_claude),
            (turns_mod, "RETRY_WAITS_S", (0, 0, 0)),
            (students, "estimate_pitches", lambda *a, **k: None),
            (roomtone, "ensure_info", lambda *a, **k: None),
        ]
        olds = [(m, n, getattr(m, n)) for m, n, _ in patches]
        old_key = os.environ.get("GROQ_API_KEY")
        os.environ["GROQ_API_KEY"] = "gsk_假的金鑰"
        try:
            for m, n, v in patches:
                setattr(m, n, v)
            analyze.run_analyze(video, w, skip_overlap=True, ref_n=2)
        finally:
            for m, n, v in olds:
                setattr(m, n, v)
            if old_key is None:
                os.environ.pop("GROQ_API_KEY", None)
            else:
                os.environ["GROQ_API_KEY"] = old_key

        saved = json.loads((w / "分析結果.json").read_text(encoding="utf-8"))
        turn_fail = [f for f in saved["Claude沒跑成功"] if f["步驟"] == "段落分析"]
        assert turn_fail and "空" in turn_fail[0]["原因"], saved["Claude沒跑成功"]
        assert calls[0] >= 2 and not turns_mod.text_turns_path(w).exists()


def test_start_analyze_says_when_claude_missing():
    """網頁按開始分析前先看找不找得到 claude：找不到 → 不開始、畫面拿得到說明；確定要開始再送一次才開始。"""
    from bookclub import server

    srv = types.SimpleNamespace()
    w = Path(tempfile.mkdtemp())
    (w / "transcript").mkdir()
    (w / "transcript" / "merged.json").write_text("{}", encoding="utf-8")
    old = server.find_claude
    server.find_claude = lambda: None
    started = []
    try:
        import threading

        srv.run_lock = threading.Lock()
        srv.run_state = {"running": False}
        srv.exec_state = {"running": False}
        srv.video = Path("/假的/影片.mp4")
        srv.workdir = w
        srv._fresh_run_state = lambda: {"running": False, "messages": []}
        srv._run_job = lambda video, opts: None

        class _T:
            def __init__(self, target=None, args=(), daemon=None):
                pass

            def start(self):
                started.append(1)

        old_thread = server.threading.Thread
        server.threading.Thread = _T
        try:
            r = server.BookclubServer.start_analyze(srv, {})
            assert not r["started"] and r["找不到claude"] and "找不到 claude 指令" in r["error"] and not started
            assert server.build_state(w)["找得到claude"] is False
            r = server.BookclubServer.start_analyze(srv, {"沒有claude也開始": True})
            assert r["started"] and started
        finally:
            server.threading.Thread = old_thread
    finally:
        server.find_claude = old


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
