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
    same = fc.refresh(chk, LOG)                             # 紀錄沒重寫：結果、看過的都不動（只補新格式的欄位）
    assert same["逐筆"] == chk["逐筆"] and same["看過區段"] == chk["看過區段"] and same["看過區段原片"] == [[0.0, 100.0]]
    new = {**LOG, "產生時間": "t2", "紀錄": [{**LOG["紀錄"][0], "檔案": "b.wav"}, LOG["紀錄"][1]]}
    out = fc.refresh(chk, new)
    assert k1 not in out["逐筆"] and out["逐筆"][k2]["結果"] == "通過"   # 重做過的那筆要重看，沒變的保留
    assert out["看過區段"] == [] and out["處理紀錄產生時間"] == "t2"      # 舊格式（沒有原片時間）：這一次歸零


# ---------- 10-03 第八批 #23：重新組裝後，沒變的不用重看 ----------

REC = {"類型": "學員重念", "原片": [50.0, 58.0], "成品": [50.0, 58.0], "做了什麼": "學員1 重念", "檔案": "生成/a.wav",
       "檔案指紋": "aaaa", "覆核項目": ["學員段落:T003"], "文字": "稿子", "接縫做法版本": 1}
CUT = {"類型": "刪除", "原片": [80.0, 85.0], "成品": [80.0, 80.0], "做了什麼": "刪除 5 秒", "檔案": None, "覆核項目": ["刪除段落:S1"]}


def _log(stamp: str, recs: list, plist: list) -> dict:
    return {"產生時間": stamp, "片段": plist, "紀錄": recs, "未登記的變動": []}


def test_content_print_ignores_output_time_but_sees_content():
    p = fc.content_print(REC)
    assert fc.content_print({**REC, "成品": [40.0, 48.0]}) == p              # 成品時間位移不算變了
    assert fc.content_print({**REC, "檔案指紋": "bbbb"}) != p                # 同一個路徑換了新檔
    assert fc.content_print({**REC, "原片": [50.0, 58.5]}) != p
    assert fc.content_print({**REC, "文字": "改過"}) != p
    assert fc.content_print({**REC, "停格秒": 0.4}) != p
    assert fc.content_print({**REC, "接縫做法版本": 2}) != p                  # 接縫做法改了：換聲音類要重聽
    no_ver = {k: v for k, v in REC.items() if k != "接縫做法版本"}
    assert fc.content_print(no_ver) == p                                     # 舊處理紀錄沒寫＝第 1 版
    assert fc.content_print({**CUT, "接縫做法版本": 2}) == fc.content_print(CUT)   # 刪除、消音類不看接縫做法
    old = {k: v for k, v in REC.items() if k != "檔案指紋"}
    assert fc.content_print(old) != p and "生成/a.wav" in fc.content_print(old)   # 沒有檔案指紋：退回用路徑


def test_watched_source_roundtrip_with_cut_and_freeze():
    plist = [{"src": [0.0, 10.0], "freeze": 0.0}, {"src": [20.0, 30.0], "freeze": 1.5}, {"src": [30.0, 40.0], "freeze": 0.0}]
    src, pts = fc.watched_to_source([[5.0, 21.5]], plist)                   # 跨過剪點、整段停格都看過
    assert src == [[5.0, 10.0], [20.0, 30.0]] and pts == [30.0]
    assert fc.watched_from_source(src, pts, plist) == [[5.0, 21.5]]
    src, pts = fc.watched_to_source([[5.0, 20.5]], plist)                   # 停格只看了一部分：不記
    assert pts == [] and fc.watched_from_source(src, pts, plist) == [[5.0, 20.0]]
    # 換一份片段表（前面多剪 5 秒、停格拿掉）：原片時間對回新的成品時間
    new = [{"src": [0.0, 5.0], "freeze": 0.0}, {"src": [20.0, 40.0], "freeze": 0.0}]
    assert fc.watched_from_source([[0.0, 10.0], [20.0, 30.0]], [30.0], new) == [[0.0, 15.0]]
    assert fc.watched_to_source([[1, 2]], None) == ([[1.0, 2.0]], [])
    assert fc.near_output_time(7.0, new) == 5.0 and fc.near_output_time(25.0, new) == 10.0


def test_refresh_cut_earlier_keeps_later_results_and_shifts_watched():
    plist1 = [{"src": [0.0, 100.0], "freeze": 0.0}]
    log1 = _log("t1", [REC], plist1)
    chk = _check(逐筆={fc.record_key(REC): {"結果": "通過", "指紋": fc.record_print(REC)}}, 處理紀錄產生時間="t1",
                 整片退回=[{"id": "R001", "成品秒": 70.0, "原片秒": 70.0, "原因": "聲音怪", "覆核項目": []}])
    chk = fc.refresh(chk, log1)
    fc._set_watched(chk, [[0.0, 100.0]], plist1)                             # add_watched 存的樣子
    # 重新組裝：前面多剪 10–20 秒（新出現一筆刪除），學員重念的成品時間往前 10 秒，內容沒變
    plist2 = [{"src": [0.0, 10.0], "freeze": 0.0}, {"src": [20.0, 100.0], "freeze": 0.0}]
    cut = {**CUT, "原片": [10.0, 20.0], "成品": [10.0, 10.0]}
    log2 = _log("t2", [cut, {**REC, "成品": [40.0, 48.0]}], plist2)
    out = fc.refresh(chk, log2)
    assert out["逐筆"][fc.record_key(REC)]["結果"] == "通過"                   # 後面通過的保留
    assert out["看過區段"] == [[0.0, 8.0], [12.0, 90.0]]                      # 跟著位移；只有新的剪點前後 2 秒要重看
    assert out["整片退回"][0]["成品秒"] == 60.0 and out["整片退回"][0]["原片秒"] == 70.0   # 整片退回還在，成品秒重算
    assert out["處理紀錄產生時間"] == "t2" and fc.record_key(cut) in out["處理紀錄快照"]


def test_refresh_new_file_resets_only_that_record():
    plist = [{"src": [0.0, 100.0], "freeze": 0.0}]
    k1, k2 = fc.record_key(REC), fc.record_key(CUT)
    chk = _check(逐筆={k1: {"結果": "通過", "指紋": fc.record_print(REC)}, k2: {"結果": "通過", "指紋": fc.record_print(CUT)}},
                 處理紀錄產生時間="t1")
    chk = fc.refresh(chk, _log("t1", [REC, CUT], plist))
    fc._set_watched(chk, [[0.0, 100.0]], plist)
    out = fc.refresh(chk, _log("t2", [{**REC, "檔案指紋": "bbbb"}, CUT], plist))   # 換了生成檔（路徑一樣）
    assert k1 not in out["逐筆"] and out["逐筆"][k2]["結果"] == "通過"
    assert out["看過區段"] == [[0.0, 48.0], [60.0, 100.0]]                    # 那一筆的範圍前後各 2 秒要重看
    # 接縫做法改了（#60）：換聲音類回到還沒看、範圍要重看；刪除照舊保留
    chk2 = fc.refresh({**chk, "逐筆": {k1: {"結果": "通過", "指紋": fc.record_print(REC)},
                                      k2: {"結果": "通過", "指紋": fc.record_print(CUT)}}},
                      _log("t3", [{**REC, "接縫做法版本": 2}, {**CUT, "接縫做法版本": 2}], plist))
    assert k1 not in chk2["逐筆"] and k2 in chk2["逐筆"] and chk2["看過區段"] == [[0.0, 48.0], [60.0, 100.0]]


def test_old_check_file_upgrades_prints_and_watched():
    """舊格式（10-03 以前）的成品檢查檔：舊指紋對得上就換新指紋；處理紀錄沒重寫時看過區段補原片時間，之後重新組裝照樣保留。"""
    plist = [{"src": [0.0, 100.0], "freeze": 0.0}]
    old_rec = {k: v for k, v in REC.items() if k not in ("檔案指紋", "接縫做法版本")}
    old_cut = dict(CUT)
    log1 = _log("t1", [old_rec, old_cut], plist)
    k1, k2 = fc.record_key(old_rec), fc.record_key(old_cut)
    chk = {"版本": 1, "逐筆": {k1: {"結果": "退回重做", "原因": "開頭雜音", "指紋": fc.legacy_print(old_rec)},
                             k2: {"結果": "通過", "指紋": fc.legacy_print(old_cut)}},
           "整片退回": [], "未登記確認": {}, "看過區段": [[0.0, 100.0]], "成品長度": 100.0, "處理紀錄產生時間": "t1"}
    up = fc.refresh(chk, log1)
    assert up["指紋版本"] == fc.PRINT_VERSION and up["逐筆"][k2]["指紋"] == fc.content_print(old_cut)
    assert up["逐筆"][k1]["指紋"] == fc.content_print(old_rec) and up["看過區段原片"] == [[0.0, 100.0]]
    # 只重新組裝（新程式寫的處理紀錄：多了檔案指紋、接縫做法版本 → 學員重念算變了；刪除沒變）
    out = fc.refresh(up, _log("t2", [REC, CUT], plist))
    assert k1 not in out["逐筆"] and out["逐筆"][k2]["結果"] == "通過" and out["看過區段"] == [[0.0, 48.0], [60.0, 100.0]]
    # 處理紀錄已經換過才第一次打開（舊指紋含成品時間）：沒位移的照樣對得上
    out2 = fc.refresh(chk, _log("t2", [old_rec, old_cut], plist))
    assert out2["逐筆"][k2]["結果"] == "通過" and out2["看過區段"] == []      # 沒有原片時間：這一次歸零


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
    assert all("生成編號" in r for r in d["紀錄"]) and d["紀錄"][0]["生成編號"] is None   # 10-04 #128：a.wav 不像編號
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
    # 10-01 第三批：一鍵只重做：終端機的指令是 run execute --redo-returned；每一筆寫按下去會怎麼重做
    assert lst["已送回"] and lst["項目"][0]["建議指令"].endswith("--redo-returned")
    assert lst["項目"][0]["做法"] == "重新組裝" and lst["項目"][0]["說明"]   # 假工作區沒有學員段落可以重新生成
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
    # 10-08：每次輸出產生一支新的（檔名帶時間），可以輸出很多次、舊的不蓋掉
    assert (w / out["檔案"]).is_file() and out["檔案"].startswith("輸出/最終成品_0-0_sw_") and out["檔案"].endswith(".mp4")
    assert out["第幾次"] == 1 and out["接上片頭片尾"] is False and out["片頭"] is None
    wd.write_json(w / "工作區設定.json", {"片頭": {"路徑": "/x/片頭.mp4", "檔名": "片頭.mp4"}})
    out2 = fc.export_final(w)
    out3 = fc.export_final(w)   # 同一秒再按：加編號
    files = {out["檔案"], out2["檔案"], out3["檔案"]}
    assert len(files) == 3 and all((w / f).is_file() for f in files)
    assert out3["第幾次"] == 3 and out2["片頭"] == "片頭.mp4"
    saved = wd.read_json(fc.check_path(w))
    assert saved["輸出成品"]["檔案"] == out3["檔案"] and len(saved["輸出紀錄"]) == 3
    assert not list((w / "輸出").glob(".*輸出中"))
    assert fc.final_name("成品_0-98_sw.mp4", "20261008-153012") == "最終成品_0-98_sw_20261008-153012.mp4"


def test_whole_flags_survive_rerender_until_redone():
    """10-03 第八批 #23：整片看時退回的，重新組裝不清掉；送回重做、組裝做完（finish_redo）的那幾筆才拿掉，
    重做過的那幾筆看過的範圍要重看，其他看過的保留。"""
    if not shutil.which("ffmpeg"):
        return
    w = _workdir()
    fc.page_data(w)
    fc.add_whole_redo(w, 2.0, "這裡卡一下")
    fc.add_whole_redo(w, 7.0, "這裡也怪")
    fc.add_watched(w, [[0.0, 10.0]])
    log = proclog.load(w)
    wd.write_json(proclog.log_path(w), {**log, "產生時間": "t-重新組裝"})
    d = fc.page_data(w)
    assert [x["id"] for x in d["整片退回"]] == ["R001", "R002"] and d["看過區段"] == [[0.0, 10.0]]
    chk = fc.load_check(w)
    chk["重做中"] = {"時間": "x", "項目": [{"鍵": "R001", "來源": "整片看", "原片": [2.0, 2.0], "生成": [], "只重新組裝": True}]}
    fc._save(w, chk)
    wd.write_json(proclog.log_path(w), {**log, "產生時間": "t-重做完"})
    back = fc.finish_redo(w)
    assert back["只重新組裝"] is True
    d = fc.page_data(w)
    assert [x["id"] for x in d["整片退回"]] == ["R002"] and d["看過區段"] == [[4.0, 10.0]]


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


def test_retime_target_same_card_names_not_many():
    """10-04 #113：同一張卡（併進主卡的候選）的好幾處不算「好幾個名字」，改的是主卡；真的是不同卡照舊擋、每張只列一次。"""
    idx = {**IDX,
           "名字:5": {**IDX["名字:1"], "名稱": "老師提到名字 00:01:17", "id": "5", "start": 77.0, "end": 77.4,
                     "第3步": "名字:1", "併進": "1"},
           "名字:6": {**IDX["名字:2"], "名稱": "老師提到名字 00:02:01", "id": "6", "start": 121.0, "end": 121.4,
                     "第3步": "名字:2", "併進": "2"}}
    rt = lambda items: fc.retime_target({"類型": "名字整句換掉", "原片": [75.0, 78.0], "覆核項目": items}, idx)  # noqa: E731
    for items in (["名字:1", "名字:5"], ["名字:5", "名字:1"], ["名字:5"]):
        t = rt(items)
        assert t["可以"] and t["方式"] == "重念範圍" and t["id"] == "1" and t["第3步"] == "名字:1", (items, t)
        assert (t["start"], t["end"]) == (75.0, 78.0)
    t = rt(["名字:1", "名字:5", "名字:2", "名字:6"])           # 兩張不同的卡 → 照舊擋，每張卡只列一次
    assert not t["可以"] and "好幾個名字" in t["原因"]
    assert t["原因"].count("〈老師提到名字 00:01:16〉") == 1 and t["原因"].count("〈老師提到名字 00:02:00〉") == 1
    assert "00:01:17" not in t["原因"] and "00:02:01" not in t["原因"]
    t = fc.retime_target({"類型": "名字消音", "原片": [76.0, 77.4], "覆核項目": ["名字:1", "名字:5"]}, idx)
    assert t["可以"] and t["id"] == "1"                          # 直接消音同一張卡的好幾處也一樣


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
    assert item["改範圍"]["改成"] == [10.0, 20.0] and "T003" not in item["建議指令"] and "T003" not in item["說明"]
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
    assert fc.plain_ids("第 3 步標的局部消音 M001（墊環境底噪）", index) == "第 3 步標的消音〈消音 01:28:04〉（墊環境底噪）"
    assert fc.plain_ids("刪除 84.7 秒（聲音畫面一起刪）", {}) == "剪掉 84.7 秒（聲音和畫面都拿掉）"
    # 10-01 第三批 7、8：統一叫法、「霧化」寫成「聲音霧化」（只改顯示）
    assert fc.plain_ids("學員3 用女聲 AI 重念；比時間格長，加快 15%", index) == "學員重念：學員3（女聲的替代聲音）；比時間格長，加快 15%"
    assert fc.plain_ids("老師提到名字：整句用老師 AI 聲音重念、名字換成代號", index) == "老師重念：整句重念、名字換成代號"
    assert fc.plain_ids("第 3 步標的局部消音 M001（墊環境底噪；選的是霧化，霧化還沒做，先墊底噪）", index).count("聲音霧化") == 2


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


def test_redo_units_per_kind():
    """10-01 第三批：每一種退回要重新生成哪幾句（純函式）；剪掉、消音、沒登記的變動只重新組裝。"""
    ctx = {"學員": [{"id": "T003_01", "段落": "T003", "slot": [10.0, 18.0]}, {"id": "T003_02", "段落": "T003", "slot": [18.4, 26.0]},
                   {"id": "O80.00_學員", "段落": "O80.00", "重疊": "O80.00", "slot": [79.0, 82.0]}],
           "老師": [{"id": "S002", "候選": [2], "slot": [75.0, 78.0]}, {"id": "V0009000", "候選": [], "重疊項目": ["O90.00"], "slot": [89.0, 92.0]}],
           "保留原聲學員": [{"id": "SS3", "候選": ["3"], "slot": [150.0, 151.0]}]}
    ids = lambda it: [u["id"] for u in fc.redo_units(it, ctx)]  # noqa: E731
    assert ids({"類型": "學員重念", "原片": [18.4, 26.0], "覆核項目": ["學員段落:T003"]}) == ["T003_02"]
    assert ids({"類型": "學員重念", "原片": [79.0, 82.0], "覆核項目": ["學員段落:O80.00"]}) == ["O80.00_學員"]
    assert ids({"類型": "停格", "原片": [18.0, 18.0], "覆核項目": ["學員段落:T003"]}) == ["T003_01"]
    assert ids({"類型": "名字整句換掉", "原片": [75.0, 78.0], "覆核項目": ["名字:2"]}) == ["S002"]
    assert ids({"類型": "名字整句換掉", "原片": [74.0, 78.0], "覆核項目": ["名字:2"]}) == ["S002"]     # 範圍改過：照名字找
    assert ids({"類型": "名字整句換掉", "原片": [89.0, 92.0], "覆核項目": ["重疊:O90.00"]}) == ["V0009000"]
    assert ids({"類型": "學員名字換代號", "原片": [150.0, 151.0], "覆核項目": ["學員名字:3"]}) == ["SS3"]
    assert ids({"來源": "整片看", "類型": "整片看時標的", "原片": [19.0, 19.0], "覆核項目": ["學員段落:T003"]}) == ["T003_01", "T003_02"]
    for kind, keys in (("刪除", ["刪除段落:S1"]), ("局部消音", ["局部消音:M001"]), ("名字消音", ["名字:1"]),
                       ("沒登記的變動", []), ("重疊", ["重疊:O80.00"])):
        assert ids({"類型": kind, "原片": [10.0, 12.0], "覆核項目": keys}) == [], kind


def _remake_product(w: Path, seconds: int) -> None:
    """同一個檔名換成另一支長度的成品（重新組裝後的樣子），修改時間一定不一樣。"""
    import os
    import time as _time

    dst = w / "輸出" / "成品_0-0_sw.mp4"
    tmp = w / "輸出" / "_新成品.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc=duration={seconds}:size=160x90:rate=10", "-f", "lavfi", "-i", f"sine=duration={seconds}",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast", str(tmp)], check=True)
    tmp.replace(dst)
    t = _time.time() + 5
    os.utime(dst, (t, t))


def test_rerender_updates_product_length():
    """10-02 第七批（C2）：重新組裝後成品檔名一樣、長度變了 → 重新量長度，看過的區段截到新長度。
    10 秒換成 7 秒 → 看完整支是 100%；7 秒換成 10 秒 → 只看過前 7 秒不是 100%。"""
    if not shutil.which("ffmpeg"):
        print("（沒有 ffmpeg，跳過）")
        return
    w = _workdir()
    st = fc.add_watched(w, [[0, 10]])
    assert st["看過比例"] == 1.0
    _remake_product(w, 7)
    d = fc.page_data(w)
    assert abs(d["成品長度"] - 7.0) < 0.3 and d["狀態"]["看過比例"] == 1.0, (d["成品長度"], d["狀態"]["看過比例"])
    assert all(b <= d["成品長度"] for _a, b in d["看過區段"])
    fc.add_watched(w, [])   # 存一次（網頁播放時會一直送）
    _remake_product(w, 10)
    d = fc.page_data(w)
    assert abs(d["成品長度"] - 10.0) < 0.3 and d["狀態"]["看過比例"] < 1.0, (d["成品長度"], d["狀態"]["看過比例"])
    assert not d["狀態"]["可以輸出"] and any("只看過" in x for x in d["狀態"]["還不能輸出的原因"])


def test_redone_reason_stays_on_its_own_record_not_the_neighbour():
    """10-03 補修：同一個學員段落切成相鄰好幾格，重做過的原因只顯示在被退回的那一格，不會錯位到隔壁。"""
    def rec(a, b, kind="學員重念"):
        return {"類型": kind, "原片": [a, b], "成品": [a, b], "覆核項目": ["學員段落:T034"], "做了什麼": "x", "檔案": None, "文字": None}
    r1, r2, r3 = rec(10.0, 20.0), rec(20.0, 28.0), rec(28.0, 36.0)
    log = {"產生時間": "t1", "紀錄": [r1, r2, r3]}
    done = {"時間": "t", "處理紀錄產生時間": "t1", "項目": [
        {"鍵": fc.record_key(r1), "原片": [10.0, 20.0], "覆核項目": ["學員段落:T034"], "原因": "第一格的原因", "做法": "重新生成"},
        {"鍵": fc.record_key(r3), "原片": [28.0, 36.0], "覆核項目": ["學員段落:T034"], "原因": "第三格的原因", "做法": "重新生成"}]}
    check = {"重做過": done}
    assert fc.redone_info(r1, check, log)["原因"] == "第一格的原因"
    assert fc.redone_info(r2, check, log) is None                 # 隔壁沒退回過：不顯示
    assert fc.redone_info(r3, check, log)["原因"] == "第三格的原因"   # 以前會拿到第一格或隔壁的
    # 重做後範圍改了（鍵變了）：用時間重疊找回來
    moved = rec(10.4, 20.0)
    log2 = {"產生時間": "t1", "紀錄": [moved, r2, r3]}
    assert fc.redone_info(moved, check, log2)["原因"] == "第一格的原因"
    assert fc.redone_info(r2, check, log2) is None
    # 整片看時標的（一個時間點）：照舊對到附近那一筆
    done["項目"].append({"鍵": "R001", "原片": [24.0, 24.0], "覆核項目": ["學員段落:T034"], "原因": "整片看時標的", "做法": "重新生成"})
    assert fc.redone_info(r2, check, log)["原因"] == "整片看時標的"



def test_gen_id_from_generated_file_name():
    """10-04 #128：第 5 步畫面小字顯示的生成編號，從生成檔的檔名取；長得不像程式編號的不給。"""
    assert fc.gen_id({"檔案": "生成/學員/T034_13_放回時間格.wav"}) == "T034_13"
    assert fc.gen_id({"檔案": "生成/學員/T034_13.wav"}) == "T034_13"
    assert fc.gen_id({"檔案": "生成/老師/SNM001_第2次.wav"}) == "SNM001"
    assert fc.gen_id({"檔案": "生成/老師/NM012.wav"}) == "NM012"
    assert fc.gen_id({"檔案": "生成/學員/T034m2_3_拉長.wav"}) == "T034m2_3"
    for bad in (None, "", "a.wav", "生成/小美.wav", "生成/男聲_暫定.wav", "生成/Amy_1.wav", "T034X.wav"):
        assert fc.gen_id({"檔案": bad}) is None, bad
    assert fc.gen_id({}) is None


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
