"""第 5 步下方清單（10-08 宇軒）：照第 3 步用分頁切換類型——「有修改的」依類型分頁、「第 3 步有卡片但選定不修改」一頁。

- `change_group`：處理紀錄的類型 → 哪一組（重疊卡片相關的歸重疊）
- `place_unchanged`：扣掉被別筆動到的、剪掉的、超出組裝範圍的；換成品時間
- `_unchanged_sources`：從第 3 步的決定檔算出每一種「不修改」（假工作區，只讀）
- 舊工作區（處理紀錄沒有新欄位、沒有第 3 步檔案）照常顯示，輸出條件不變
- inspect 成品檢查：多印各類型筆數，不印名字
- 網頁：分組、只播那一段（node 跑純函式）
獨立可跑：.venv/bin/python tests/test_finalcheck_groups.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import finalcheck as fc  # noqa: E402
from bookclub import proclog  # noqa: E402
from bookclub import review  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

FCJS = (REPO_ROOT / "bookclub" / "web" / "finalcheck.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _rec(kind, a, b, items=(), **kw):
    return {"類型": kind, "原片": [a, b], "成品": [a, b], "動到聲音": kind != "重疊", "要人聽": False,
            "覆核項目": list(items), "做了什麼": kind, **kw}


# ---------- 分組（純函式） ----------

def test_change_group_by_kind():
    g = fc.change_group
    assert g(_rec("學員重念", 1, 2, ["學員段落:T001"])) == "學員重念"
    assert g(_rec("學員空隙消音", 1, 2)) == "學員空隙" and g(_rec("學員空隙保留原聲", 1, 2)) == "學員空隙"
    assert g(_rec("名字整句換掉", 1, 2, ["名字:3"])) == "名字重念" and g(_rec("換聲音", 1, 2)) == "名字重念"
    assert g(_rec("名字消音", 1, 2)) == "名字消音" and g(_rec("消音", 1, 2)) == "名字消音"
    assert g({"類型": "名字要人處理", "原片": None, "覆核項目": ["名字:4"]}) == "名字要人處理"
    assert g(_rec("學員名字消音", 1, 2)) == "學員名字" and g(_rec("學員名字換代號", 1, 2)) == "學員名字"
    assert g(_rec("刪除", 1, 2)) == "剪掉" and g(_rec("局部消音", 1, 2, ["局部消音:M001"])) == "消音"
    assert g(_rec("停格", 2, 2)) == "停格" and g(_rec("模糊示範", 1, 2)) == "其他" and g(_rec("沒見過的", 1, 2)) == "其他"
    # 重疊相關：覆核項目第一個是重疊的歸重疊；名字＋重疊的照第一個（名字），只列一次
    assert g(_rec("局部消音", 1, 2, ["重疊:O1.00"])) == "重疊" and g(_rec("重疊", 1, 2)) == "重疊"
    assert g(_rec("名字整句換掉", 1, 2, ["重疊:O1.00"])) == "重疊"
    assert g(_rec("名字整句換掉", 1, 2, ["名字:2", "重疊:O1.00"])) == "名字重念"
    # 重疊卡片自己生成的學員那一句（覆核項目寫成 學員段落:<重疊 id>）
    idx = {"學員段落:O5.00": {"類型": "重疊"}, "學員段落:T001": {"類型": "學員段落"}}
    assert g(_rec("學員重念", 5, 6, ["學員段落:O5.00"]), idx) == "重疊"
    assert g(_rec("學員重念", 1, 2, ["學員段落:T001"]), idx) == "學員重念"
    # 每一組都有畫面上的名稱
    ids = [x[0] for x in fc.CHANGE_GROUPS]
    assert len(ids) == len(set(ids)) and set(fc._GROUP_OF_KIND.values()) <= set(ids) and "其他" in ids


def test_group_counts():
    log = {"紀錄": [_rec("學員重念", 1, 2), _rec("學員重念", 3, 4), _rec("刪除", 5, 6), _rec("停格", 4, 4)]}
    assert fc.group_counts(log) == {"學員重念": 2, "剪掉": 1, "停格": 1}
    assert fc.group_counts(None) == {}


def test_place_unchanged_subtracts_cut_and_covered():
    from bookclub import render

    plist = render.pieces(0.0, 100.0, [(40.0, 50.0)], [])     # 40–50 剪掉
    log = {"範圍": [0.0, 100.0], "片段": plist,
           "紀錄": [_rec("刪除", 40.0, 50.0), _rec("名字整句換掉", 12.0, 14.0, ["名字:1"]), _rec("重疊", 60.0, 61.0)]}
    rows = [{"鍵": "不修改:改成老師:T1", "子類": "改成老師", "名稱": "a", "start": 10.0, "end": 20.0, "第3步": "改成老師:T1"},
            {"鍵": "不修改:還原:D1", "子類": "剪掉還原", "名稱": "b", "start": 41.0, "end": 49.0, "第3步": None},     # 整段被剪掉
            {"鍵": "不修改:重疊:O60", "子類": "重疊不用改", "名稱": "c", "start": 60.0, "end": 61.0, "第3步": "重疊:O60"},  # 標記不算動到聲音
            {"鍵": "不修改:保留原聲:T2", "子類": "保留原聲", "名稱": "d", "start": 38.0, "end": 52.0, "第3步": "學員段落:T2"},
            {"鍵": "不修改:名字:3", "子類": "名字略過", "名稱": "e", "start": 12.5, "end": 13.0, "第3步": "名字:3"},     # 落在重念裡
            {"鍵": "x", "子類": "人名不處理", "名稱": "f", "start": 150.0, "end": 151.0, "第3步": None}]               # 超出組裝範圍
    out = {r["鍵"]: r for r in fc.place_unchanged(rows, log)}
    assert set(out) == {"不修改:改成老師:T1", "不修改:重疊:O60", "不修改:保留原聲:T2"}
    a = out["不修改:改成老師:T1"]
    assert a["剩下"] == [[10.0, 12.0], [14.0, 20.0]] and a["剩下秒"] == 8.0 and a["成品"] == [10.0, 20.0] and a["原片"] == [10.0, 20.0]
    d = out["不修改:保留原聲:T2"]
    assert d["剩下"] == [[38.0, 40.0], [50.0, 52.0]] and d["成品"] == [38.0, 42.0]   # 剪掉之後的成品時間
    assert out["不修改:重疊:O60"]["成品"] == [50.0, 51.0]
    # 排序照子類的順序
    assert [r["子類"] for r in fc.place_unchanged(rows, log)] == ["改成老師", "保留原聲", "重疊不用改"]
    assert fc.place_unchanged(rows, None) == []


# ---------- 不修改的來源（假工作區） ----------

def _fake_workdir() -> Path:
    from bookclub import personnames, studentnames
    from bookclub import turns as turns_mod
    from bookclub.execute import OUT_A, OUT_B

    w = Path(tempfile.mkdtemp()) / "工作區"
    w.mkdir(parents=True)
    wd.write_json(turns_mod.turns_path(w), {"段落": [
        {"id": "T001", "start": 10.0, "end": 20.0, "說話者": "老師", "說話者是人改的": True},
        {"id": "T002", "start": 30.0, "end": 35.0, "說話者": "學員1"},     # 保留原聲
        {"id": "T003", "start": 50.0, "end": 55.0, "說話者": "學員2"},     # 重新生成：不列
        {"id": "T004", "start": 60.0, "end": 70.0, "說話者": "老師"},       # 本來就是老師：不列
    ], "學員": {}})
    wd.write_json(review.review_path(w), {
        "學員聲音": {"學員1": "保留原聲", "學員2": "重新生成"},
        "刪除建議": {"S1": {"決定": "不刪"}, "S2": {"決定": "刪除"}},
        "刪除段落": [{"id": "D001", "start": 80.0, "end": 82.0, "狀態": "還原"}, {"id": "D002", "start": 84.0, "end": 85.0}],
        "局部消音": [{"id": "M001", "start": 90.0, "end": 91.0, "狀態": "還原"}, {"id": "M002", "start": 92.0, "end": 93.0}],
        "段落外答案": {"段落外:T003:56.0": {"段落": "T003", "start": 56.0, "end": 57.0, "答案": OUT_A},
                   "段落外:T003:58.0": {"段落": "T003", "start": 58.0, "end": 59.0, "答案": OUT_B}},
    })
    wd.write_json(review.cut_suggest_path(w), {"建議": [{"id": "S1", "start": 1.0, "end": 3.0, "類型": "開頭"},
                                                    {"id": "S2", "start": 4.0, "end": 5.0, "類型": "結尾"}]})
    wd.write_json(wd.names_path(w), {"candidates": [
        {"start": 100.0, "end": 100.5, "sentence_id": "s10", "matched_text": "王小明", "name": "王小明", "代號": "小樹"},
        {"start": 102.0, "end": 102.5, "sentence_id": "s11", "matched_text": "台北", "name": "台北", "代號": "小樹"},
        {"start": 104.0, "end": 104.5, "sentence_id": "s12", "matched_text": "林小華", "name": "林小華", "代號": "小草"}]})
    wd.write_json(review.name_decisions_path(w), {"1": {"tags": ["不是名字"]}, "2": {"tags": ["是地名"]},
                                                 "3": {"做法": "整句換掉"}})
    wd.write_json(studentnames.cands_path(w), {"candidates": [
        {"id": "SN1", "start": 31.0, "end": 31.5, "學員": "學員1"}, {"id": "SN2", "start": 33.0, "end": 33.5, "學員": "學員1"},
        {"id": "SN3", "start": 52.0, "end": 52.5, "學員": "學員2"}]})
    wd.write_json(studentnames.decisions_path(w), {"SN1": {"tags": ["不是名字"]}, "SN3": {"tags": ["不是名字"]}})
    wd.write_json(wd.speakers_path(w), {"sentences": [{"id": "s20", "start": 110.0, "end": 112.0, "text": "x"},
                                                      {"id": "s21", "start": 120.0, "end": 121.0, "text": "y"}]})
    wd.write_json(personnames.people_path(w), {"人名": [
        {"id": "P1", "名字": "陳大文", "其他寫法": [], "句子": ["s21", "s20"], "是誰": "其他人", "次數": 2, "行號": []},
        {"id": "P2", "名字": "張三", "其他寫法": [], "句子": ["s20"], "是誰": "其他人", "次數": 1, "行號": []},
        {"id": "P3", "名字": "李四", "其他寫法": [], "句子": [], "是誰": "其他人", "次數": 0, "行號": []}]})
    wd.write_json(personnames.decisions_path(w), {"陳大文": {"做法": "不用處理"}, "張三": {"做法": "換成代號"},
                                                  "李四": {"做法": "不是名字"}})
    return w


def _snapshot(w: Path) -> dict:
    return {str(p.relative_to(w)): p.read_bytes() for p in w.rglob("*") if p.is_file()}


def test_unchanged_sources_from_step3_files():
    w = _fake_workdir()
    before = _snapshot(w)
    orig = review.overlap_choices
    review.overlap_choices = lambda workdir, voices=None: [   # 重疊的決定要讀很多檔，這裡直接給
        {"id": "O130.00", "start": 130.0, "end": 131.0, "做法": "不用改"},
        {"id": "O140.00", "start": 140.0, "end": 141.0, "做法": "只留老師"}]
    try:
        rows = fc._unchanged_sources(w)
    finally:
        review.overlap_choices = orig
    assert _snapshot(w) == before, "算不修改區不能寫任何檔"
    got = {r["鍵"]: r for r in rows}
    assert set(got) == {"不修改:改成老師:T001", "不修改:保留原聲:T002", "不修改:重疊:O130.00", "不修改:名字:1", "不修改:名字:2",
                        "不修改:學員名字:SN1", "不修改:還原:S1", "不修改:還原:D001", "不修改:還原:M001",
                        "段落外:T003:56.0", "不修改:人名:P1"}, sorted(got)
    assert got["不修改:改成老師:T001"]["第3步"] == "改成老師:T001" and got["不修改:保留原聲:T002"]["第3步"] == "學員段落:T002"
    assert got["不修改:保留原聲:T002"]["學員"] == "學員1"
    assert got["不修改:重疊:O130.00"]["第3步"] == "重疊:O130.00" and got["不修改:名字:2"]["子類"] == "名字略過"
    assert got["不修改:還原:S1"]["子類"] == "不剪" and got["不修改:還原:D001"]["子類"] == "剪掉還原"
    assert got["不修改:還原:M001"]["子類"] == "消音還原" and got["不修改:還原:S1"]["第3步"] is None
    assert got["段落外:T003:56.0"]["第3步"] == "學員段落:T003" and got["段落外:T003:56.0"]["子類"] == "段落外老師"
    p = got["不修改:人名:P1"]
    assert p["處數"] == 2 and p["start"] == 110.0 and p["end"] == 112.0   # 第一次出現的那一句
    kinds = {k for k, _n, _x in fc.UNCHANGED_KINDS}
    assert {r["子類"] for r in rows} <= kinds


def test_unchanged_sources_empty_workdir():
    w = Path(tempfile.mkdtemp()) / "空的"
    w.mkdir()
    assert fc._unchanged_sources(w) == []
    assert fc.unchanged_rows(w, None) == []


# ---------- 舊工作區：照常顯示、輸出條件不變 ----------

def test_old_workdir_page_data_and_status_unchanged():
    if not shutil.which("ffmpeg"):
        print("（沒有 ffmpeg，跳過）")
        return
    w = Path(tempfile.mkdtemp()) / "工作區"
    (w / "輸出").mkdir(parents=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=10:size=160x90:rate=10",
                    "-f", "lavfi", "-i", "sine=duration=10", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    str(w / "輸出" / "成品_0-0_sw.mp4")], check=True)
    # 舊的處理紀錄：沒有「組」、沒有範圍、沒有片段
    wd.write_json(proclog.log_path(w), {"產生時間": "t1", "來源": "render audio",
                                        "紀錄": [_rec("名字消音", 1.0, 2.0, ["名字:1"], 編號=1), _rec("刪除", 3.0, 4.0, 編號=2)],
                                        "未登記的變動": []})
    d = fc.page_data(w)
    assert [r["組"] for r in d["紀錄"]] == ["名字消音", "剪掉"]
    assert [g["組"] for g in d["修改類型"]] == [x[0] for x in fc.CHANGE_GROUPS]
    assert d["不修改"] == [] and len(d["不修改類型"]) == len(fc.UNCHANGED_KINDS)
    # 輸出條件照舊：逐筆通過＋整片看完；不修改區不影響
    for r in d["紀錄"]:
        fc.decide_record(w, r["鍵"], "通過")
    fc.add_watched(w, [[0.0, 10.0]])
    st = fc.page_data(w)["狀態"]
    assert st["可以輸出"] and st["還不能輸出的原因"] == []
    chk = wd.read_json(fc.check_path(w))
    assert set(chk["逐筆"]) == {fc.record_key(r) for r in d["紀錄"]}   # 逐筆通過的鍵沒變
    assert "不修改" not in chk and "分區" not in chk                   # 存檔不多欄位


# ---------- inspect ----------

def test_inspect_prints_group_counts_without_names():
    from bookclub import safeview

    w = _fake_workdir()
    from bookclub import render

    plist = render.pieces(0.0, 200.0, [(84.0, 85.0)], [])
    wd.write_json(proclog.log_path(w), {"產生時間": "t1", "範圍": [0.0, 200.0], "片段": plist, "未登記的變動": [],
                                        "紀錄": [_rec("學員重念", 50.0, 55.0, ["學員段落:T003"], 編號=1),
                                               _rec("刪除", 84.0, 85.0, 編號=2), _rec("局部消音", 92.0, 93.0, ["局部消音:M002"], 編號=3)]})
    out: list[str] = []
    safeview.topic_finalcheck(w, safeview.Filter(), out)
    text = "\n".join(out)
    assert "有修改的各類型 學員重念 1、剪掉 1、消音 1" in text, text
    assert re.search(r"第 3 步選定不修改 \d+ 筆：改成老師 1、保留原聲 1", text), text
    assert "組=學員重念" in text
    for name in ("王小明", "陳大文", "台北", "林小華"):
        assert name not in text, name


# ---------- 網頁（node 跑純函式） ----------

def _fn(name: str) -> str:
    m = re.search(rf"^(async )?function {name}\(.*?^\}}\n", FCJS, re.S | re.M)
    assert m, name
    return m.group(0)


def _node(code: str):
    return json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True).stdout)


def test_web_group_recs_and_unchanged():
    if not NODE:
        print("（沒有 node，略過）")
        return
    groups = [{"組": g, "名稱": n, "說明": x} for g, n, x in fc.CHANGE_GROUPS]
    recs = [{"鍵": "a", "組": "剪掉", "結果": "通過"}, {"鍵": "b", "組": "學員重念"}, {"鍵": "c", "組": "剪掉", "要人看": True},
            {"鍵": "d"}, {"鍵": "e", "組": "不認得的"}]
    kinds = [{"子類": k, "名稱": n, "去改": x} for k, n, x in fc.UNCHANGED_KINDS]
    rows = [{"鍵": "u1", "子類": "保留原聲"}, {"鍵": "u2", "子類": "改成老師"}, {"鍵": "u3", "子類": "保留原聲"}, {"鍵": "u4", "子類": "新的"}]
    got = _node(_fn("fcGroupRecs") + _fn("fcGroupUnchanged") + f"""
      const G = fcGroupRecs({json.dumps(recs, ensure_ascii=False)}, {json.dumps(groups, ensure_ascii=False)});
      const O = fcGroupRecs([{{"鍵": "z"}}], undefined);   // 舊的後端沒有「修改類型」
      const U = fcGroupUnchanged({json.dumps(rows, ensure_ascii=False)}, {json.dumps(kinds, ensure_ascii=False)});
      console.log(JSON.stringify({{
        g: G.map((x) => [x["組"], x.recs.map((r) => r["鍵"]), x["通過"], x["要人看"]]),
        o: O.map((x) => [x["組"], x.recs.length]),
        u: U.map((x) => [x["子類"], x.rows.map((r) => r["鍵"])]),
        none: fcGroupUnchanged(undefined, undefined).length }}));""")
    assert got["g"] == [["學員重念", ["b"], 0, 0], ["剪掉", ["a", "c"], 1, 1], ["其他", ["d", "e"], 0, 0]]
    assert got["o"] == [["其他", 1]]
    assert got["u"] == [["改成老師", ["u2"]], ["保留原聲", ["u1", "u3"]], ["新的", ["u4"]]]
    assert got["none"] == 0


def test_web_play_span_stops_at_end():
    if not NODE:
        return
    got = _node("const FC_SPAN_WAIT_MS = 10000;\n" + _fn("fcSpanStep") + """
      const s = { a: 10, b: 12, armed: false };
      const out = [fcSpanStep(s, 3)];            // 還沒跳到：等
      out.push(fcSpanStep(s, 10.0)); s.armed = true;
      out.push(fcSpanStep(s, 11.5), fcSpanStep(s, 12.0), fcSpanStep(s, 30), fcSpanStep(s, 2), fcSpanStep(null, 1));
      console.log(JSON.stringify(out));""")
    assert got == ["wait", "play", "play", "stop", "drop", "drop", "drop"]


def test_web_play_span_gives_up_after_10s():
    # 10-08：按了「跳過去聽」之後影片一直沒跳到那一段，等超過 10 秒就放掉（不再卡在等著）
    if not NODE:
        return
    got = _node("const FC_SPAN_WAIT_MS = 10000;\n" + _fn("fcSpanStep") + """
      const s = { a: 10, b: 12, armed: false, since: 1000 };
      console.log(JSON.stringify([fcSpanStep(s, 3, 1000 + 9000), fcSpanStep(s, 3, 1000 + 10001),
                                  fcSpanStep(s, 10.5, 1000 + 20000)]));""")
    assert got == ["wait", "drop", "play"]
    js = (REPO_ROOT / "bookclub" / "web" / "finalcheck.js").read_text(encoding="utf-8")
    assert "fcSpanStep(fc.span, t, Date.now())" in js and "FC_SPAN_WAIT_MS + 50" in js


def _consts() -> str:
    m = re.search(r"^const FC_TAB_ALL = .*?;\n", FCJS, re.M)
    assert m
    return m.group(0)


def test_web_tabs_like_step3():
    """10-08 宇軒：第 5 步下方照第 3 步用分頁。分頁列：全部、還沒通過、每一組（有筆數的）、第 3 步選定不修改（有才列）；
    徽章寫通過 x／n 與要人聽幾筆；記住的頁不見了回全部。"""
    if not NODE:
        return
    groups = [{"組": g, "名稱": n, "說明": x} for g, n, x in fc.CHANGE_GROUPS]
    recs = [{"鍵": "a", "組": "剪掉", "結果": "通過"}, {"鍵": "b", "組": "學員重念", "要人看": True}, {"鍵": "c", "組": "剪掉"},
            {"鍵": "d", "組": "學員重念", "結果": "通過"}]
    got = _node(_consts() + _fn("fcGroupRecs") + _fn("fcTabList") + _fn("fcPickTab") + f"""
      const R = {json.dumps(recs, ensure_ascii=False)}, G = {json.dumps(groups, ensure_ascii=False)};
      const T = fcTabList(R, G, [{{"鍵": "u1"}}], "d");
      const T2 = fcTabList(R, G, [], null);
      console.log(JSON.stringify({{
        t: T.map((x) => [x.id, x["名稱"], x.recs.map((r) => r["鍵"]), x["通過"], x["筆數"], x["要人看"] || 0]),
        keep: T[T.length - 1]["不修改"] === true,
        t2: T2.map((x) => x.id),
        pick: [fcPickTab(T, "組:剪掉"), fcPickTab(T2, "不修改"), fcPickTab(T, "組:停格"), fcPickTab(T, null)] }}));""")
    assert got["t"] == [["全部", "全部", ["a", "b", "c", "d"], 2, 4, 1],
                        ["還沒通過", "還沒通過", ["b", "c", "d"], 1, 3, 1],    # 目前這一筆（d）通過了也先留著
                        ["組:學員重念", "學員段落：AI 重念", ["b", "d"], 1, 2, 1],
                        ["組:剪掉", "剪掉（連畫面）", ["a", "c"], 1, 2, 0],
                        ["不修改", "第 3 步選定不修改", [], 0, 1, 0]]
    assert got["keep"] and got["t2"] == ["全部", "還沒通過", "組:學員重念", "組:剪掉"]   # 沒有不修改就不列那一頁
    assert got["pick"] == ["組:剪掉", "全部", "全部", "全部"]


def test_web_lower_wiring():
    """分頁列沿用第 3 步的 .rv-filters；頁面記在瀏覽器；只看要人聽的、上一筆／下一筆、通過後找下一筆都在目前這一頁裡走；
    從第 3 步回來切到那一筆所在的頁；按鈕接到只播那一段與回第 3 步。"""
    lower = _fn("fcRenderLower")
    assert 'class="rv-filters fc-tabs"' in lower and "fcTabSet(b.dataset.tab)" in lower and "fcTabBadge(" in lower
    assert 'id="fc-only"' in lower and "fcShown()" in lower and "<details" not in FCJS   # 不再用可收合的群組
    assert 'const FC_TAB_KEY = "fc-tab"' in FCJS and "localStorage.getItem(FC_TAB_KEY)" in FCJS
    shown = _fn("fcShown")
    assert "fcTab()" in shown and "fcOnlyLook()" in shown
    assert "fcShown()" in _fn("fcStep")
    assert "fcTab() !== FC_TAB_ALL ? fcShown()" in _fn("fcDecide")
    rf = _fn("renderFinal")
    assert "fcTabSet(fcTabOf(key))" in rf and "fcTabSet(FC_TAB_KEEP)" in rf
    assert "fcPlaySpan(" in lower and "rvJump({ key: u[\"第3步\"] || null, back: \"step5\", backKey: u[\"鍵\"]" in lower
    assert 'id="fc-list"' in lower and 'id="fc-ulist"' in lower and "fcSpanTick(v.currentTime)" in FCJS
    assert FCJS.count("fileOutInit(") >= 1 and 'id="fc-fileout"' in FCJS   # 10-07 加的檔案進出照留


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
