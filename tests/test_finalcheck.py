"""bookclub/finalcheck.py（第 5 步成品檢查）的測試：看過比例的計算、退回清單的存檔、全部通過且看完才能輸出。

用合成資料：處理紀錄是手寫的、成品影片用 ffmpeg 的 testsrc 產生 10 秒，不碰真的影片、模型。
獨立可跑：.venv/bin/python tests/test_finalcheck.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 10-01：這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import finalcheck as fc  # noqa: E402
from bookclub import proclog  # noqa: E402
from bookclub import workdir as wd  # noqa: E402


# ---------- 看過比例（純函式） ----------

def test_merge_ranges_union():
    assert fc.merge_ranges([[5, 8], [0, 2], [1, 3], [8, 9], [7, 7]]) == [[0.0, 3.0], [5.0, 9.0]]
    assert fc.merge_ranges([]) == []


def test_watched_ratio_counts_union_only():
    # 同一段看兩次不會變兩倍；跳過去的地方不算
    assert fc.watched_seconds([[0, 10], [5, 15], [5, 15]], 100) == 15
    assert fc.watched_ratio([[0, 10], [5, 15]], 100) == 0.15
    assert fc.watched_ratio([[0, 50], [60, 100]], 100) == 0.9
    assert fc.watched_ratio([[0, 200]], 100) == 1.0            # 超出片長的不多算
    assert fc.watched_ratio([], 0) == 0.0


def test_watched_ratio_slack_at_end():
    # 播放器最後一格、四捨五入：沒看到的加起來不超過 0.5 秒算看完；超過就不算
    assert fc.watched_ratio([[0, 99.7]], 100) == 1.0
    assert fc.watched_ratio([[0, 40], [40.3, 99.8]], 100) == 1.0
    assert fc.watched_ratio([[0, 99.0]], 100) == 0.99


def test_from_output_time_inverts_pieces():
    from bookclub import render

    plist = render.pieces(10.0, 20.0, [(13.0, 15.0)], [{"at": 17.0, "dur": 0.5}])
    assert fc.from_output_time(1.0, plist) == 11.0
    assert fc.from_output_time(3.5, plist) == 15.5      # 刪掉 13–15 之後
    assert fc.from_output_time(5.2, plist) == 17.0      # 落在停格裡 → 停格那一點
    assert abs(fc.from_output_time(6.0, plist) - 17.5) < 1e-9
    assert fc.from_output_time(3.0, None) == 3.0


# ---------- 狀態、退回清單（純函式） ----------

LOG = {"產生時間": "t1", "片段": None, "紀錄": [
    {"類型": "學員重念", "原片": [10.0, 18.0], "成品": [10.0, 18.0], "做了什麼": "學員1 重念", "檔案": "a.wav",
     "覆核項目": ["學員段落:T003"], "文字": "稿子"},
    {"類型": "刪除", "原片": [30.0, 35.0], "成品": [30.0, 30.0], "做了什麼": "刪除 5 秒", "檔案": None, "覆核項目": ["刪除段落:S1"]},
], "未登記的變動": [{"原片": [50.0, 50.4], "長度秒": 0.4, "格數": 20}]}


def _check(**kw) -> dict:
    base = {"逐筆": {}, "整片退回": [], "未登記確認": {}, "看過區段": [], "成品長度": 100.0}
    base.update(kw)
    return base


def test_status_blocks_export_until_everything_done():
    k1, k2 = (fc.record_key(r) for r in LOG["紀錄"])
    st = fc.status(LOG, _check())
    assert not st["可以輸出"] and len(st["還不能輸出的原因"]) == 3
    passed = {k1: {"結果": "通過"}, k2: {"結果": "通過"}}
    un_ok = {"50.00": {"結果": "沒問題"}}
    st = fc.status(LOG, _check(逐筆=passed, 未登記確認=un_ok, 看過區段=[[0, 90]]))
    assert not st["可以輸出"] and st["還不能輸出的原因"] == ["整片只看過 90%，要看完 100%"]
    st = fc.status(LOG, _check(逐筆=passed, 未登記確認=un_ok, 看過區段=[[0, 100]]))
    assert st["可以輸出"] and st["看過比例"] == 1.0
    # 看完了，但有一筆整片看時退回的 → 不能輸出
    flag = [{"id": "R001", "成品秒": 12.0, "原片秒": 12.0, "原因": "怪怪的"}]
    st = fc.status(LOG, _check(逐筆=passed, 未登記確認=un_ok, 看過區段=[[0, 100]], 整片退回=flag))
    assert not st["可以輸出"] and st["退回數"] == 1
    assert not fc.status(None, _check())["可以輸出"]


def test_redo_items_from_three_sources():
    k1 = fc.record_key(LOG["紀錄"][0])
    chk = _check(逐筆={k1: {"結果": "退回重做", "原因": "念錯字"}},
                 整片退回=[{"id": "R001", "成品秒": 40.0, "原片秒": 45.0, "原因": "畫面停太久", "覆核項目": ["重疊:O40.00"]}],
                 未登記確認={"50.00": {"結果": "退回重做", "原因": "不該變"}})
    items = fc.redo_items(LOG, chk)
    assert [i["來源"] for i in items] == ["逐筆", "整片看", "沒登記的變動"]
    assert items[0]["覆核項目"] == ["學員段落:T003"] and items[0]["原因"] == "念錯字"
    assert items[1]["原片"] == [45.0, 45.0] and items[2]["原片"] == [50.0, 50.4]


def test_items_near_finds_review_items():
    assert fc.items_near(LOG, 12.0) == ["學員段落:T003"]
    assert fc.items_near(LOG, 30.5) == ["刪除段落:S1"]
    assert fc.items_near(LOG, 70.0) == []


def test_refresh_drops_stale_results_after_rerender():
    k1, k2 = (fc.record_key(r) for r in LOG["紀錄"])
    chk = _check(逐筆={k1: {"結果": "退回重做", "指紋": fc.record_print(LOG["紀錄"][0])},
                     k2: {"結果": "通過", "指紋": fc.record_print(LOG["紀錄"][1])}},
                 看過區段=[[0, 100]], 處理紀錄產生時間="t1")
    assert fc.refresh(chk, LOG) == chk                      # 紀錄沒重寫：不動
    new = {**LOG, "產生時間": "t2", "紀錄": [{**LOG["紀錄"][0], "檔案": "b.wav"}, LOG["紀錄"][1]]}
    out = fc.refresh(chk, new)
    assert k1 not in out["逐筆"] and out["逐筆"][k2]["結果"] == "通過"   # 重做過的那筆要重看，沒變的保留
    assert out["看過區段"] == [] and out["處理紀錄產生時間"] == "t2"      # 新的成品要重新看完


# ---------- 存檔（假工作區） ----------

def _workdir() -> Path:
    w = Path(tempfile.mkdtemp()) / "工作區"
    (w / "輸出").mkdir(parents=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=10:size=160x90:rate=10",
                    "-f", "lavfi", "-i", "sine=duration=10", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    str(w / "輸出" / "成品_0-0_sw.mp4")], check=True)
    log = {**LOG, "紀錄": [{**r, "成品": [min(r["成品"][0], 9.0), min(r["成品"][1], 9.0)]} for r in LOG["紀錄"]]}
    wd.write_json(proclog.log_path(w), log)
    return w


def test_save_flow_sendback_and_export():
    if not shutil.which("ffmpeg"):
        print("（沒有 ffmpeg，跳過）")
        return
    w = _workdir()
    d = fc.page_data(w)
    assert d["成品影片"] == "輸出/成品_0-0_sw.mp4" and 9.5 < d["成品長度"] < 10.5
    k1, k2 = (r["鍵"] for r in d["紀錄"])
    try:
        fc.decide_record(w, k1, "退回重做", "  ")
        raise AssertionError("退回沒寫原因應該擋下來")
    except ValueError:
        pass
    fc.decide_record(w, k1, "退回重做", "第二句念錯")
    fc.decide_record(w, k2, "通過")
    r = fc.add_whole_redo(w, 5.0, "聲音突然變小")
    assert r["項目"]["id"] == "R001" and r["項目"]["原片秒"] == 5.0
    fc.decide_unlogged(w, "50.00", "沒問題")
    sent = fc.send_back(w)
    assert sent["筆數"] == 2
    saved = wd.read_json(fc.check_path(w))
    assert [x["原因"] for x in saved["送回AI重做"]["項目"]] == ["第二句念錯", "聲音突然變小"]
    lst = fc.redo_list(w)
    assert lst["已送回"] and lst["項目"][0]["建議指令"].startswith("bookclub gen students") and "T003" in lst["項目"][0]["建議指令"]
    # 還不能輸出：有退回、沒看完
    try:
        fc.export_final(w)
        raise AssertionError("還沒全部通過、沒看完，應該不能輸出")
    except ValueError as e:
        assert "100%" in str(e)
    fc.decide_record(w, k1, "通過")
    fc.remove_whole_redo(w, "R001")
    fc.add_watched(w, [[0, 4.0]])
    assert fc.page_data(w)["狀態"]["看過比例"] < 1
    r = fc.add_watched(w, [[3.5, 10.0]])
    assert r["看過比例"] == 1.0 and r["看過區段"] == [[0.0, 10.0]]
    out = fc.export_final(w)
    assert (w / out["檔案"]).is_file() and out["檔案"] == "輸出/最終成品_0-0_sw.mp4"


def test_watched_rejects_other_product():
    if not shutil.which("ffmpeg"):
        return
    w = _workdir()
    try:
        fc.add_watched(w, [[0, 1]], product="輸出/別支.mp4")
        raise AssertionError
    except ValueError:
        pass


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
