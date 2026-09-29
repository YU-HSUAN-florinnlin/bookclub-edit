"""bookclub/proclog.py（AI 處理紀錄＋沒登記的變動檢查）的測試：合成聲音，不碰真的影片、模型。

三件事（09-29 宇軒的完成標準）：登記過的變動不會被列出、沒登記的變動會被列出、完全沒變動時清單是空的。
另外測：成品時間軸（刪除、停格之後）放回原片時間軸、紀錄對回第 3 步的覆核項目、render audio 的檔案版本。

獨立可跑：.venv/bin/python tests/test_proclog.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import proclog, render  # noqa: E402

SR = 48000


def _speech(dur: float, seed: int = 0) -> np.ndarray:
    """像講話的合成聲音：幾個頻率疊起來、音量一段一段起伏，加一點底噪。"""
    t = np.arange(int(dur * SR)) / SR
    rng = np.random.default_rng(seed)
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 3.1 * t) ** 2
    x = env * (0.12 * np.sin(2 * np.pi * 180 * t) + 0.05 * np.sin(2 * np.pi * 420 * t))
    return (x + 0.003 * rng.standard_normal(len(t))).astype(np.float32)


def _rec(kind: str, s: float, e: float) -> dict:
    return {"類型": kind, "原片": [s, e], "動到聲音": True}


def test_no_change_empty():
    x = _speech(10.0)
    r = proclog.check_arrays(x, x.copy(), SR, [])
    assert r["未登記的變動"] == [] and r["檢查"]["有變動的格數"] == 0


def test_logged_change_not_listed_unlogged_listed():
    x = _speech(20.0)
    y = x.copy()
    y[int(3.0 * SR):int(5.0 * SR)] = _speech(2.0, seed=1) * 0.8           # 有登記：3–5 秒換聲音
    y[int(12.0 * SR):int(12.5 * SR)] *= 0.2                               # 沒登記：12–12.5 秒被調小聲
    r = proclog.check_arrays(x, y, SR, [_rec("學員重念", 3.0, 5.0)])
    found = r["未登記的變動"]
    assert len(found) == 1, found
    s, e = found[0]["原片"]
    assert abs(s - 12.0) <= 0.02 and abs(e - 12.5) <= 0.02
    # 同一處補上紀錄就不再列出
    r = proclog.check_arrays(x, y, SR, [_rec("學員重念", 3.0, 5.0), _rec("名字消音", 12.0, 12.5)])
    assert r["未登記的變動"] == []


def test_pad_covers_fade_just_outside_span():
    x = _speech(6.0)
    y = x.copy()
    y[int(2.0 * SR):int(3.05 * SR)] = 0.0         # 紀錄寫 2–3 秒，接縫淡出多碰到 0.05 秒：在 0.1 秒內，不算
    assert proclog.check_arrays(x, y, SR, [_rec("名字消音", 2.0, 3.0)])["未登記的變動"] == []
    y[int(3.3 * SR):int(3.4 * SR)] = 0.0         # 離紀錄 0.3 秒：算沒登記
    found = proclog.check_arrays(x, y, SR, [_rec("名字消音", 2.0, 3.0)])["未登記的變動"]
    assert len(found) == 1 and abs(found[0]["原片"][0] - 3.3) <= 0.02


def test_non_audio_records_do_not_cover():
    # 模糊示範、重疊標記不會動到聲音：聲音在那段裡變了，還是要列出來
    x = _speech(6.0)
    y = x.copy()
    y[int(1.0 * SR):int(1.5 * SR)] *= -1.0
    recs = [{"類型": "模糊示範", "原片": [0.0, 6.0], "動到聲音": False}, {"類型": "重疊", "原片": [0.8, 1.8], "動到聲音": False}]
    assert len(proclog.check_arrays(x, y, SR, recs)["未登記的變動"]) == 1


def test_offset_and_merge_gap():
    x = _speech(8.0)
    y = x.copy()
    y[int(2.0 * SR):int(2.2 * SR)] = 0.0
    y[int(2.3 * SR):int(2.5 * SR)] = 0.0          # 隔 0.1 秒：併成一處
    found = proclog.check_arrays(x, y, SR, [], offset=100.0)["未登記的變動"]
    assert len(found) == 1 and abs(found[0]["原片"][0] - 102.0) <= 0.02 and abs(found[0]["原片"][1] - 102.5) <= 0.02


def test_source_timeline_after_cut_and_freeze():
    # 原片範圍 10–20 秒：刪 13–15、在 17 停格 0.5 秒
    x = _speech(10.0)
    plist = render.pieces(10.0, 20.0, [(13.0, 15.0)], [{"at": 17.0, "dur": 0.5}])
    y = x.copy()
    y[int(6.0 * SR):int(6.5 * SR)] = 0.0          # 原片 16–16.5 秒被改了（沒登記）
    parts = []
    for p in plist:
        s, e = (int(round((t - 10.0) * SR)) for t in p["src"])
        parts.append(y[s:e])
        if p["freeze"]:
            parts.append(np.full(int(round(p["freeze"] * SR)), 0.3, dtype=np.float32))   # 停格補的聲音不算
    new = np.concatenate(parts)
    back = proclog.to_source_timeline(new, plist, 10.0, SR, x)
    assert len(back) == len(x)
    recs = [_rec("刪除", 13.0, 15.0), _rec("停格", 17.0, 17.0)]
    found = proclog.check_arrays(x, back, SR, recs, offset=10.0)["未登記的變動"]
    assert len(found) == 1 and abs(found[0]["原片"][0] - 16.0) <= 0.02 and abs(found[0]["原片"][1] - 16.5) <= 0.02


def test_build_records_links_to_review_items():
    d = {"範圍": [0.0, 60.0], "刪除": [(30.0, 35.0)],
         "動作": [{"類型": "學員重念", "start": 10.0, "end": 18.0, "id": "T003_01", "學員": "學員1", "聲線": "女",
                  "檔案": "生成/學員/T003_01_放回時間格.wav", "來源檔案": "生成/學員/T003_01.wav", "停格秒": 1.2, "text": "稿子"},
                 {"類型": "名字整句換掉", "start": 40.0, "end": 44.0, "id": "S001", "候選": [1, "NM001"], "檔案": "生成/老師/S001.wav",
                  "text": "Amy 好"},
                 {"類型": "名字消音", "start": 50.0, "end": 50.5, "候選": [2]}],
         "停格": [{"at": 18.0, "dur": 1.2, "原因": "重念太長", "edit": "T003_01"}],
         "模糊": [0.0, 30.0], "標記": [{"類型": "重疊", "start": 20.0, "end": 21.0, "做法": "只留學員", "處理": "這輪沒有另外處理"}]}
    plist = render.pieces(0.0, 60.0, d["刪除"], d["停格"])
    links = {"段落": {"T003_01": "T003"}, "刪除": [[30.0, 35.0, "刪除段落:S2"]], "重疊": [[20.0, 21.0, "重疊:O20.00"]]}
    recs = proclog.build_records(d, plist, links)
    by = {r["類型"]: r for r in recs}
    assert by["學員重念"]["覆核項目"] == ["學員段落:T003"] and by["學員重念"]["檔案"].endswith("T003_01.wav")
    assert by["學員重念"]["成品"] == [10.0, 19.2]                  # 停格 1.2 秒算在這一筆的成品時間裡
    assert by["名字整句換掉"]["覆核項目"] == ["名字:1", "名字:NM001"]
    assert by["名字整句換掉"]["成品"] == [36.2, 40.2]              # 前面刪了 5 秒、停格 1.2 秒
    assert by["刪除"]["覆核項目"] == ["刪除段落:S2"] and by["刪除"]["成品"][0] == by["刪除"]["成品"][1]
    assert by["停格"]["覆核項目"] == ["學員段落:T003"]
    assert by["重疊"]["覆核項目"] == ["重疊:O20.00"] and not by["重疊"]["動到聲音"]
    assert not by["模糊示範"]["動到聲音"]
    assert [r["編號"] for r in recs] == list(range(1, len(recs) + 1))
    assert (0.0, 30.0) not in proclog.audio_spans(recs)


def test_check_files_streams():
    x = _speech(130.0)
    y = x.copy()
    y[int(70.0 * SR):int(71.0 * SR)] = 0.0         # 跨過第一塊（60 秒）之後
    y[int(100.0 * SR):int(101.0 * SR)] = 0.0
    with tempfile.TemporaryDirectory() as d:
        a, b = Path(d) / "原.wav", Path(d) / "新.wav"
        sf.write(str(a), x, SR, subtype="PCM_16")
        sf.write(str(b), y, SR, subtype="PCM_16")
        recs = proclog.records_from_edl([{"類型": "消音", "start": 100.0, "end": 101.0, "候選": [3]}])
        r = proclog.check_files(a, b, recs)
    found = r["未登記的變動"]
    assert len(found) == 1 and abs(found[0]["原片"][0] - 70.0) <= 0.02
    assert recs[0]["覆核項目"] == ["名字:3"]



def test_render_check_chunked_matches_whole():
    # 09-30：render video 的處理紀錄改成分段讀、分段比；結果要跟整條讀進來比一模一樣
    a = 10.0
    x = _speech(40.0)
    plist = render.pieces(a, a + 40.0, [(13.0, 15.0), (31.0, 33.5)], [{"at": 20.0, "dur": 0.5}, {"at": 44.0, "dur": 1.0}])
    y = x.copy()
    y[int(6.0 * SR):int(6.5 * SR)] = 0.0              # 原片 16–16.5 沒登記
    y[int(25.0 * SR):int(26.0 * SR)] *= 0.1            # 原片 35–36 沒登記
    y[int(9.0 * SR):int(12.0 * SR)] = 0.2 * y[int(9.0 * SR):int(12.0 * SR)]   # 原片 19–22 有登記
    parts = []
    for p in plist:
        s0, e0 = (int(round((t - a) * SR)) for t in p["src"])
        parts.append(y[s0:e0])
        if p["freeze"]:
            parts.append(np.full(int(round(p["freeze"] * SR)), 0.3, dtype=np.float32))
    new = np.concatenate(parts)
    recs = [_rec("刪除", 13.0, 15.0), _rec("刪除", 31.0, 33.5), _rec("學員重念", 19.0, 22.0)]
    with tempfile.TemporaryDirectory() as d:
        po, pn = Path(d) / "原聲.wav", Path(d) / "新聲音.wav"
        sf.write(str(po), x, SR, subtype="PCM_16")
        sf.write(str(pn), new, SR, subtype="PCM_16")
        xo, _ = sf.read(str(po), dtype="float32")
        xn, _ = sf.read(str(pn), dtype="float32")
        whole = proclog.check_arrays(xo, proclog.to_source_timeline(xn, plist, a, SR, xo), SR, recs, offset=a)
        for block in (0.7, 7.3, 60.0):                  # 塊的邊界切在片段中間、停格旁邊都要對
            got = proclog.check_render_files(po, pn, plist, a, recs, block_s=block)
            assert got == whole, (block, got["檢查"], whole["檢查"])
    assert len(whole["未登記的變動"]) == 2

if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
