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


# ---------- 10-01 介面修改 2-4：退回重做時能不能在第 5 步改範圍（純函式）、改了之後記在哪裡 ----------

IDX = {
    "學員段落:T003": {"名稱": "學員段落 00:00:10", "類型": "學員段落", "id": "T003", "start": 10.0, "end": 18.0,
                    "第3步": "學員段落:T003", "改時間": "學員發言"},
    "名字:1": {"名稱": "老師提到名字 00:01:16", "類型": "名字", "id": "1", "start": 76.0, "end": 76.5, "第3步": "名字:1",
              "改時間": "名字", "老師整段": False},
    "名字:2": {"名稱": "老師提到名字 00:02:00", "類型": "名字", "id": "2", "start": 120.0, "end": 120.5, "第3步": "名字:2",
              "改時間": "名字", "老師整段": False},
    "名字:NM001": {"名稱": "老師重念 00:02:10", "類型": "名字", "id": "NM001", "start": 130.0, "end": 133.0,
                  "第3步": "名字:NM001", "改時間": "名字", "老師整段": True},
    "重疊:O1": {"名稱": "重疊 00:01:20", "類型": "重疊", "id": "O1", "start": 80.0, "end": 81.0, "第3步": "重疊:O1",
               "改時間": "重疊", "疊放": False},
    "學員段落:O1": None,
    "重疊:O2": {"名稱": "重疊 00:01:30", "類型": "重疊", "id": "O2", "start": 90.0, "end": 91.0, "第3步": "重疊:O2",
               "改時間": "重疊", "疊放": True},
    "刪除段落:S1": {"名稱": "剪掉片段 00:00:30", "類型": "刪除段落", "id": "S1", "start": 30.0, "end": 35.0,
                  "第3步": "刪除段落:S1", "改時間": "刪除段落"},
    "學員名字:3": {"名稱": "學員提到名字 00:02:30", "類型": "學員名字", "id": "3", "start": 150.0, "end": 150.4,
                 "第3步": "學員名字:3", "改時間": None},
}
IDX["學員段落:O1"] = IDX["重疊:O1"]


def _rt(kind, items, orig=(10.0, 18.0)):
    return fc.retime_target({"類型": kind, "原片": list(orig), "覆核項目": items}, IDX)


def test_retime_target_kinds():
    t = _rt("學員重念", ["學員段落:T003"])
    assert t["可以"] and t["方式"] == "改時間" and t["類型"] == "學員發言" and t["id"] == "T003" and "重新生成" in t["重做"]
    t = _rt("學員重念", ["學員段落:O1"])                       # 重疊卡片自己生成的學員那一句 → 改重疊的起訖
    assert t["可以"] and t["類型"] == "重疊" and t["id"] == "O1"
    t = _rt("名字整句換掉", ["名字:1"], (75.0, 78.0))             # 一個名字 → 改重念範圍，起訖照處理紀錄
    assert t["可以"] and t["方式"] == "重念範圍" and (t["start"], t["end"]) == (75.0, 78.0)
    t = _rt("名字整句換掉", ["名字:NM001"], (130.0, 133.0))       # 人工標的老師整段 → 改那一段的起訖
    assert t["可以"] and t["方式"] == "改時間" and t["老師整段"]
    t = _rt("刪除", ["刪除段落:S1"], (30.0, 35.0))
    assert t["可以"] and t["類型"] == "刪除段落" and t["重做"] == "重新組裝"
    assert _rt("局部消音", ["重疊:O1"])["可以"] and _rt("重疊", ["重疊:O1"])["可以"] and _rt("名字消音", ["名字:1"])["可以"]
    # 不能改的：講原因、帶到第 3 步那一張卡片
    for kind, items, card in (("停格", ["學員段落:T003"], "學員段落:T003"), ("學員名字消音", ["學員名字:3"], "學員名字:3"),
                              ("名字整句換掉", ["名字:1", "名字:2"], "名字:1"), ("名字整句換掉", ["重疊:O1"], "重疊:O1"),
                              ("學員重念", ["學員段落:O2"] if "學員段落:O2" in IDX else ["重疊:O2"], "重疊:O2"),
                              ("名字要人處理", ["名字:1"], "名字:1")):
        t = _rt(kind, items)
        assert not t["可以"] and t["原因"] and t["下一步"] and t["第3步"] == card, (kind, t)
    assert not _rt("模糊示範", [])["可以"]


def test_retime_saved_with_redo_and_step4_told():
    if not shutil.which("ffmpeg"):
        return
    w = _workdir()
    k1 = fc.page_data(w)["紀錄"][0]["鍵"]
    info = {"名稱": "學員段落 00:00:10", "原本": [10.0, 18.0], "改成": [10.0, 19.5], "第3步": "學員段落:T003",
            "重做": "重新生成這一筆、再重新組裝", "沒用的欄位": 1}
    fc.decide_record(w, k1, "退回重做", "結尾抓太早", info)
    fc.decide_record(w, k1, "退回重做", "結尾抓太早", {**info, "原本": [10.0, 19.5], "改成": [10.0, 20.0]})   # 再改一次
    saved = wd.read_json(fc.check_path(w))["逐筆"][k1]["改範圍"]
    assert saved["原本"] == [10.0, 18.0] and saved["改成"] == [10.0, 20.0] and "沒用的欄位" not in saved
    rec = next(r for r in fc.page_data(w)["紀錄"] if r["鍵"] == k1)
    assert rec["已改範圍"]["改成"] == [10.0, 20.0]
    item = fc.redo_list(w)["項目"][0]
    assert item["改範圍"]["改成"] == [10.0, 20.0] and "開始執行" in item["建議指令"] and "T003" not in item["建議指令"]
    assert item["覆核名稱"] and "T003" not in "".join(item["覆核名稱"])
    fc.decide_record(w, k1, "通過")                    # 改成通過：改範圍的紀錄拿掉
    assert "改範圍" not in wd.read_json(fc.check_path(w))["逐筆"][k1]


def test_plain_ids_hides_internal_ids_and_uses_step3_words():
    """10-01 走查：第 5 步「做了什麼」不露 T034_15、V0542232、M001，重疊的做法、剪掉用第 3 步的說法。"""
    index = {"學員段落:T034": {"id": "T034", "名稱": "學員段落 00:43:21"},
             "局部消音:M001": {"id": "M001", "名稱": "消音 01:28:04"}}
    t = fc.plain_ids("停格 1.36 秒：T034_01 重念比時間格長 +30%", index)
    assert t == "停格 1.36 秒：〈學員段落 00:43:21〉裡的一句重念比時間格長 +30%", t
    assert fc.plain_ids("重疊（只留學員）：整段落在 T047m1_01 的範圍裡", index) == "重疊（生成學員聲音）：整段落在 另一筆 的範圍裡"
    assert fc.plain_ids("第 3 步標的局部消音 M001（墊環境底噪）", index) == "第 3 步標的局部消音〈消音 01:28:04〉（墊環境底噪）"
    assert fc.plain_ids("刪除 84.7 秒（聲音畫面一起刪）", {}) == "剪掉 84.7 秒（聲音和畫面都拿掉）"
    assert fc.plain_ids("學員3 用女聲 AI 重念", index) == "學員3 用女聲 AI 重念"


def test_redo_list_includes_items_after_sendback():
    """10-01 走查：按過「送回 AI 重做」之後才退回的，第 4 步也要列出來。"""
    if not shutil.which("ffmpeg"):
        return
    w = _workdir()
    k1, k2 = (r["鍵"] for r in fc.page_data(w)["紀錄"])
    fc.decide_record(w, k1, "退回重做", "第一句")
    fc.send_back(w)
    fc.decide_record(w, k2, "退回重做", "送回之後才退回")
    lst = fc.redo_list(w)
    assert [x["原因"] for x in lst["項目"]] == ["第一句", "送回之後才退回"], lst["項目"]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
