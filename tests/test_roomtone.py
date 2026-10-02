"""bookclub/roomtone.py（墊底噪的挑法，10-02 第六批第五件）的測試。全部合成聲音，不用模型。

- 附近只有人聲時不挑人聲、退回全片底噪
- 夠安靜的片段在處理過的範圍裡也會被取用
- 接縫與頭尾沒有跳變（量相鄰取樣點的差）
- 舊工作區（沒有 參考音/底噪.json）自動補挑；第 2 步「換一段」「這段可以」
- 挑法改了，組裝做過沒有的判斷要求重新組裝
獨立可跑：.venv/bin/python tests/test_roomtone.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import assemble, execute, roomtone  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

SR = 16000
RNG = np.random.default_rng(1)


def speech(sec: float, amp: float = 0.2) -> np.ndarray:
    """像講話：幾個頻率、音量起伏（一直有聲音）。"""
    t = np.arange(int(sec * SR)) / SR
    env = 0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)
    return (amp * env * (np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 330 * t))).astype(np.float32)


def hiss(sec: float, amp: float = 3e-5) -> np.ndarray:
    return (amp * RNG.standard_normal(int(sec * SR))).astype(np.float32)


def test_ceiling():
    assert roomtone.ceiling(-90.0) == -78.0          # 第一堂：安靜處 −90 → 上限 −78
    assert roomtone.ceiling(-50.0) == -60.0          # 很吵的影片：上限最多 −60


def test_speech_only_nearby_falls_back_to_global():
    x = speech(60.0) + hiss(60.0)                    # 前後全是人聲
    bed = hiss(4.0, 2e-5)
    out, info = roomtone.pick(x, 30 * SR, 31 * SR, SR, SR, ceil_db=-78.0, fallback=bed)
    assert info["來源"] == "全片底噪"
    assert roomtone.level_db(out) < -78                # 沒有把人聲墊進去
    out, info = roomtone.pick(x, 30 * SR, 31 * SR, SR, SR, ceil_db=-78.0, fallback=None)
    assert info["來源"] == "全靜音" and not out.any()  # 連全片底噪都沒有：全靜音，不退而求其次用吵的
    # 舊挑法會挑到人聲（這就是 10-02 聽到的雜訊）：前後 20 秒最安靜的 0.5 秒還是講話的音量
    win = int(SR * 0.5)
    best = min(roomtone.level_db(x[a:a + win]) for a in range(10 * SR, 51 * SR - win, win // 2) if not (a < 31 * SR and 30 * SR < a + win))
    assert best > -40


def test_quiet_inside_processed_range_is_used():
    # 0–25 秒講話、25–28 秒很安靜（這一段在學員段落裡，以前會被避開）、28–60 秒講話；要墊的是 30–31 秒
    x = np.concatenate([speech(25.0), hiss(3.0), speech(32.0)])
    out, info = roomtone.pick(x, 30 * SR, 31 * SR, SR, SR, ceil_db=-78.0, fallback=hiss(4.0))
    assert info["來源"] == "附近"
    (a, b), = info["片段"]
    assert 25 * SR <= a and b <= 28 * SR             # 一段夠長的連續安靜片段，不是 0.5 秒重複
    assert roomtone.level_db(out) < -78
    # 舊的呼叫方式（assemble.room_tone 給了 avoid）也一樣用得到
    out2 = assemble.room_tone(x, 30 * SR, 31 * SR, SR, SR, avoid=[(20 * SR, 29 * SR)],
                              bed=roomtone.Bed(-78.0, hiss(4.0)))
    assert roomtone.level_db(out2) < -78


def test_short_runs_joined_without_jumps():
    # 附近只有幾段 0.6 秒的安靜（講話中間的停頓），要墊 2 秒 → 接起來，接縫交叉淡入淡出
    parts = []
    for _ in range(6):
        parts += [speech(3.0), hiss(0.6, 1e-4 * (1 + RNG.random()))]
    x = np.concatenate(parts + [speech(10.0)])
    s = int(len(x) - 5 * SR)
    out, info = roomtone.pick(x, s, s + SR, 2 * SR, SR, ceil_db=-70.0, fallback=None)
    assert info["來源"] == "附近" and len(info["片段"]) >= 4 and len(out) == 2 * SR
    assert roomtone.level_db(out) < -70
    jump = np.abs(np.diff(out)).max()
    typical = np.abs(np.diff(x[info["片段"][0][0]:info["片段"][0][1]])).max()
    assert jump <= typical * 1.5, (jump, typical)       # 接縫沒有比底噪本身的起伏大


def test_too_little_nearby_uses_global_not_repeat():
    # 附近只有一段 0.4 秒的安靜，要墊 3 秒：不要把 0.4 秒重複 8 次，改用全片底噪
    x = np.concatenate([speech(20.0), hiss(0.4), speech(30.0)])
    out, info = roomtone.pick(x, 30 * SR, 33 * SR, 3 * SR, SR, ceil_db=-78.0, fallback=hiss(6.0, 2e-5))
    assert info["來源"] == "全片底噪"
    out, info = roomtone.pick(x, 30 * SR, 33 * SR, 3 * SR, SR, ceil_db=-78.0, fallback=None)
    assert info["來源"] == "附近"                     # 沒有全片底噪時才重複附近那一段


def test_splice_edges_no_click():
    x = speech(10.0)
    y = x.copy()
    s, e = 4 * SR, 5 * SR
    assemble.splice(y, s, roomtone.crossjoin([hiss(0.4), hiss(0.4)], e - s, SR), SR)
    d = np.abs(np.diff(y[s - 200:e + 200]))
    assert d.max() <= np.abs(np.diff(x)).max() * 1.05   # 跟前後原聲的交界淡入淡出，不會比講話本身的起伏還陡


def _workdir_with_audio() -> Path:
    w = Path(tempfile.mkdtemp()) / "工作區"
    w.mkdir(parents=True)
    x = np.concatenate([speech(20.0), hiss(1.5), speech(30.0), hiss(6.0), speech(40.0), hiss(2.5), speech(20.0)])
    sf.write(str(w / "audio.flac"), x + hiss(len(x) / SR, 1e-6), SR)
    return w


def test_old_workdir_backfill_and_step2():
    w = _workdir_with_audio()
    assert roomtone.load_info(w) is None             # 舊工作區：沒有這份
    info = roomtone.ensure_info(w)
    assert roomtone.info_path(w).is_file() and info["挑法"] == roomtone.METHOD
    assert 51.5 <= info["start"] and info["end"] <= 57.5 and info["end"] - info["start"] >= 5.5   # 最長那一段（6 秒）
    assert info["dBFS"] < info["上限dBFS"] and not info["已確認"] and len(info["候選"]) == 3
    nxt = roomtone.decide(w, "換一段")
    assert nxt["選第幾個"] == 1 and nxt["start"] != info["start"] and not nxt["已確認"]
    ok = roomtone.decide(w, "確認")
    assert ok["已確認"] and roomtone.ensure_info(w)["已確認"]      # 已經有了不重挑
    # 總檢查「請看一眼」：確認了就不再列；沒確認列一列
    roomtone.decide(w, "換一段")
    try:
        roomtone.decide(w, "亂按")
        raise AssertionError("不認得的動作要擋")
    except ValueError:
        pass


def test_no_audio_no_error():
    w = Path(tempfile.mkdtemp())
    assert roomtone.ensure_info(w) is None
    bed = roomtone.bed_for(w, None)
    assert bed.clip is None and bed.ceiling is None


def test_render_done_wants_new_method():
    w = Path(tempfile.mkdtemp())
    out = w / "輸出"
    out.mkdir()
    (out / "成品_t_sw.mp4").write_bytes(b"x")
    wd.write_json(out / "輸出摘要_t.json", {"輸出": {"sw": {"驗證": {"通過": True}}}})   # 舊的摘要：沒有記挑法
    done, why = execute.render_done(w, "t", ["sw"])
    assert not done and "底噪" in why
    wd.write_json(out / "輸出摘要_t.json", {"輸出": {"sw": {"驗證": {"通過": True}}}, "底噪挑法": roomtone.METHOD})
    import os
    import time

    t = time.time() + 5
    os.utime(out / "成品_t_sw.mp4", (t, t))
    assert execute.render_done(w, "t", ["sw"])[0]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
