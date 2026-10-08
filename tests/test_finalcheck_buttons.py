"""10-05 第 4 步「開始前總檢查」三項修改（宇軒 10-05 實際操作後提出）：
- #176「跳過去聽」只播那一列的起點到終點（不加前後緩衝）
- #177「一定要處理」每一列的「照目前設定做」（按下去就照第 3 步的決定進行）：五類可以按（含原本「我聽過了」的三類，
  合成同一顆），按了算處理完、可以取消、存在覆核資料、inspect 看得到；名字換不了代號、重疊缺東西不開放，放「回第 3 步補」
- #178「請看一眼」每一列一個「我看過了」＋整區「全部看過了」；全部看過才算，之後新增的列回到沒看過

後端用 tests/fake_workdir.py 的合成資料（不載入模型、不連網）；網頁的純函式用 node 跑（沒有 node 就略過那幾項）。
獨立可跑：.venv/bin/python tests/test_finalcheck_buttons.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

_DATA = Path(tempfile.mkdtemp())
(_DATA / "名冊.csv").write_text("中文名,其他寫法,英文代號,聲線,性別\n小美,,Amy,,女\n阿明,,Tom,,男\n", encoding="utf-8")
os.environ["BOOKCLUB_DATA_DIR"] = str(_DATA)

import fake_workdir  # noqa: E402

from bookclub import codeswap, execute, review  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

APPJS = (REPO_ROOT / "bookclub" / "web" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
_ROOT = None


def _fresh() -> Path:
    global _ROOT
    if _ROOT is None:
        _ROOT = Path(tempfile.mkdtemp()) / "base"
        fake_workdir.make(_ROOT)
    d = Path(tempfile.mkdtemp())
    shutil.copytree(_ROOT, d / "base")
    w = d / "base" / "工作區"
    data = wd.read_json(w / "分析結果.json")
    data["video"] = str(d / "base" / "假影片.mp4")
    wd.write_json(w / "分析結果.json", data)
    return w


def _fn(src: str, name: str) -> str:
    """從 JS 原始碼切出一個頂層函式（到下一個行首的 `}`）。"""
    m = re.search(rf"^(async )?function {name}\(.*?^\}}\n", src, re.S | re.M)
    assert m, name
    return m.group(0)


def _node(code: str):
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


_ESC = """const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
"""


class _FakeCodes:
    """把 codeswap.leftover_texts 換成固定的一筆（英文代號那一類是可以按「不改」的），測完還原。"""

    def __init__(self, codes: list[str]):
        self.codes = codes

    def __enter__(self):
        self.old = codeswap.leftover_texts
        codeswap.leftover_texts = lambda w: [{"卡片": "沒有這張卡:1", "欄位": "改稿", "代號": list(self.codes)}]
        return self

    def __exit__(self, *a):
        codeswap.leftover_texts = self.old


def _must(w: Path, prefix: str) -> list[dict]:
    return [r for r in execute.final_check(w)["一定要處理"] if r["key"].startswith(prefix)]


# ---------- #176 跳過去聽只播那一段 ----------

def test_listen_plays_exact_range_without_padding():
    fn = _fn(APPJS, "bindFinalCheck")
    assert "a - 1" not in fn and "e + 1" not in fn, "跳過去聽還在加前後緩衝"
    assert "start=${a.toFixed(2)}&end=${e.toFixed(2)}" in fn
    assert "跳過去聽" in _fn(APPJS, "finalCheckHtml")   # 按鈕文字維持「跳過去聽」
    if not NODE:
        print("（沒有 node，略過純函式）")
        return
    code = _ESC + "function fcTime(t){return String(t);}\n" + _fn(APPJS, "fcListenRange") + _fn(APPJS, "fcListenBtn") + """
      console.log(JSON.stringify([fcListenRange(12.3, 15.8), fcListenRange(0, 0), fcListenRange(null, 3), fcListenRange(5, 4),
        fcListenRange(-0.2, 1), fcListenBtn(12.3, 15.8, "跳過去聽"), fcListenBtn(0, 0, "跳過去聽")]));"""
    got = _node(code)
    assert got[0] == [12.3, 15.8] and got[1] is None and got[2] is None and got[3] is None and got[4] == [0, 1], got
    assert 'data-fcplay="12.3|15.8"' in got[5] and ">跳過去聽<" in got[5] and got[6] == "", got


# ---------- #177 照目前設定做（10-05 宇軒：按下去就照第 3 步的決定進行） ----------

def _row(kind, **kw):
    return {"key": f"{kind}:x", "類別": kind, "start": 1.0, "end": 2.0, "依據": "abc", **kw}


def test_keep_policy_uses_kind_field():
    for k in (execute.KIND_CODE, execute.KIND_SHORT, execute.KIND_OUTSIDE, execute.KIND_VOICE, execute.KIND_NO_TEXT):
        assert execute.keep_policy(k) == (True, ""), k
    for k in (execute.KIND_NAME, execute.KIND_OVERLAP):   # 10-08：不開放「照目前設定做」，但不擋開始（說明寫略過的後果）
        ok, why = execute.keep_policy(k)
        assert not ok and "回第 3 步補" in why and "預設略過" in why, k
    for k in (None, "", "以後新的類別", "段落外:T1:1.0"):              # 不認得的（包括鍵的樣子）一律不開放
        ok, why = execute.keep_policy(k)
        assert not ok and why, k
    # 列上的判斷看「類別」欄位，不看鍵：鍵像英文代號、類別不認得 → 不開放
    r = {"key": "英文代號:a:b", "start": 1.0, "end": 2.0}
    execute.apply_keep(r, {"start": 1.0, "end": 2.0, "依據": None})
    assert not r["可以按照目前設定做"] and not r["處理好"]


def test_apply_keep_ack_kinds_old_and_new_records():
    """原本「我聽過了」的三類：舊的「聽過」紀錄照算；這次之後按的多記起訖與內容，變了就不算（取嚴格的）。"""
    for kind in execute.ACK_KINDS:
        r = _row(kind, 已按聽過=True)
        execute.apply_keep(r, None)                                         # 只有舊的「聽過」→ 照算
        assert r["處理好"] and r["已按照目前設定做"] and r["照目前設定做的後果"], kind
        r = _row(kind, 已按聽過=True)
        execute.apply_keep(r, {"start": 1.0, "end": 2.0, "依據": "abc"})     # 新紀錄沒變 → 算
        assert r["處理好"]
        r = _row(kind, 已按聽過=True)
        execute.apply_keep(r, {"start": 1.6, "end": 2.0, "依據": "abc"})     # 範圍變了（沒有字的舊規則容許 1 秒，這裡從嚴）
        assert not r["處理好"] and r["照目前設定做後變了"]
        r = _row(kind, 已按聽過=True)
        execute.apply_keep(r, {"start": 1.0, "end": 2.0, "依據": "zzz"})     # 內容變了
        assert not r["處理好"] and r["照目前設定做後變了"]
        r = _row(kind, 已按聽過=False)
        execute.apply_keep(r, {"start": 1.0, "end": 2.0, "依據": "abc"})     # 第 3 步改了答案（聽過拿掉）→ 不算
        assert not r["處理好"] and not r.get("照目前設定做後變了")
    for kind in (execute.KIND_NAME, execute.KIND_OVERLAP):                  # 後兩類：存檔怎麼塞都不算，有「回第 3 步補」
        r = _row(kind, 已按聽過=True)
        execute.apply_keep(r, {"start": 1.0, "end": 2.0, "依據": "abc"})
        assert not r["處理好"] and r["回第3步補"] and not r["可以按照目前設定做"], kind


def test_keep_marks_row_done_persists_and_can_undo():
    w = _fresh()
    with _FakeCodes(["Emma"]):
        r = _must(w, "英文代號:")[0]
        assert r["類別"] == execute.KIND_CODE and r["可以按照目前設定做"] and not r["處理好"]
        before = execute.final_check(w)
        execute.keep_final(w, r["key"])
        saved = review.load_decisions(w)["總檢查"][execute.KEEP_FIELD][r["key"]]   # 存在覆核資料
        assert set(saved) == {"start", "end", "依據", "時間"} and "Emma" not in json.dumps(saved, ensure_ascii=False)
        after = execute.final_check(w)
        r2 = next(x for x in after["一定要處理"] if x["key"] == r["key"])
        assert r2["已按照目前設定做"] and r2["處理好"] and after["已按照目前設定做"] == 1
        assert after["還要處理"] == before["還要處理"] - 1 and after["已確認"] == before["已確認"] + 1
    with _FakeCodes(["Emma", "Rose"]):                                     # 內容變了 → 不算、標出來
        r3 = _must(w, "英文代號:")[0]
        assert not r3["處理好"] and r3["照目前設定做後變了"]
    with _FakeCodes(["Emma"]):
        assert _must(w, "英文代號:")[0]["處理好"]
        execute.keep_final(w, r["key"], keep=False)                        # 反悔
        r4 = _must(w, "英文代號:")[0]
        assert not r4["處理好"] and not r4.get("照目前設定做後變了")
        # b6291f7 的舊名字（總檢查.不改）也認
        dec = review.load_decisions(w)
        dec["總檢查"][execute.KEEP_FIELD_OLD] = {r["key"]: dict(saved)}
        review._save_decisions(w, dec)
        assert _must(w, "英文代號:")[0]["處理好"]
        execute.keep_final(w, r["key"], keep=False)
        assert not _must(w, "英文代號:")[0]["處理好"]


def _outside_setup(w: Path) -> str:
    oid = review.manual_edit(w, {"類型": "重疊", "start": 80.2, "end": 81.0})["id"]
    review.save_overlap(w, oid, {"做法": "只留學員"})                      # 缺學員是誰 → 重疊缺東西
    t = next(t for t in review.page_data(w)["項目"] if t["類型"] == "學員段落")
    review.retime_turn(w, t["id"], t["start"] + 1.0, t["end"])             # 開頭晚 1 秒 → 段落外
    return t["id"]


def test_outside_kind_open_merged_with_heard_and_old_record():
    w = _fresh()
    tid = _outside_setup(w)
    out = _must(w, "段落外:")[0]
    assert out["類別"] == execute.KIND_OUTSIDE and out["可以按照目前設定做"] and not out["處理好"]
    assert "原聲" in out["照目前設定做的後果"]
    execute.keep_final(w, out["key"])                                      # 按一顆＝以前的「我聽過了」
    r = _must(w, "段落外:")[0]
    assert r["處理好"] and r["已按聽過"] and out["key"] in review.load_decisions(w)["總檢查"]["聽過"]
    assert execute.outside_questions(w)[tid][0]["答案"] == execute.OUT_A   # 第 3 步看得到「老師的話，不用處理」
    # 內容變了（存的依據不一樣）→ 不算
    dec = review.load_decisions(w)
    dec["總檢查"][execute.KEEP_FIELD][out["key"]]["依據"] = "舊的"
    review._save_decisions(w, dec)
    assert not _must(w, "段落外:")[0]["處理好"] and _must(w, "段落外:")[0]["照目前設定做後變了"]
    execute.keep_final(w, out["key"])                                      # 再按一次就好
    assert _must(w, "段落外:")[0]["處理好"]
    execute.keep_final(w, out["key"], keep=False)                          # 取消：聽過、第 3 步答案一起拿掉
    assert not _must(w, "段落外:")[0]["處理好"] and execute.outside_questions(w)[tid][0]["答案"] is None
    # 舊資料：只有「聽過」（以前勾的「我聽過了」）→ 照算
    dec = review.load_decisions(w)
    dec["總檢查"].pop(execute.KEEP_FIELD, None)
    review._save_decisions(w, dec)
    execute.ack_final(w, out["key"])
    assert _must(w, "段落外:")[0]["處理好"]


def test_back3_kinds_still_refused():
    w = _fresh()
    _outside_setup(w)
    ov = _must(w, "重疊:")[0]
    assert ov["類別"] == execute.KIND_OVERLAP and ov["回第3步補"] and not ov["可以按照目前設定做"] and ov["第3步"]
    try:
        execute.keep_final(w, ov["key"])
        raise AssertionError("重疊缺東西不該能照目前設定做")
    except ValueError as e:
        assert "回第 3 步補" in str(e)
    dec = review.load_decisions(w)                                         # 手動塞進存檔也不算
    dec.setdefault("總檢查", {}).setdefault("聽過", []).append(ov["key"])
    dec["總檢查"][execute.KEEP_FIELD] = {ov["key"]: {"start": ov["start"], "end": ov["end"], "依據": ov["依據"], "時間": "t"}}
    review._save_decisions(w, dec)
    assert not _must(w, "重疊:")[0]["處理好"] and not execute.final_check(w)["可以開始"]
    try:
        execute.keep_final(w, "英文代號:不存在:改稿")
        raise AssertionError("不在清單的列不該能按")
    except ValueError as e:
        assert "不在" in str(e)


def test_no_text_kind_open():
    """沒有字（#117）：開放「照目前設定做」；舊的「聽過」鍵容許 1 秒移動照算。"""
    import test_untranscribed as tu

    w = tu._fresh()
    r = _must(w, "沒有字:")[0]
    assert r["類別"] == execute.KIND_NO_TEXT and r["可以按照目前設定做"] and not r["處理好"]
    execute.keep_final(w, r["key"])
    assert _must(w, "沒有字:")[0]["處理好"]


def test_run_execute_check_matches_buttons():
    """10-08 宇軒（流程簡化）：命令列 run execute 跟網頁一致——總檢查還有沒處理的也不擋（預設略過），
    過了之後用假的 sync 停下，不跑模型；按了「照目前設定做」照樣算處理好。"""
    from bookclub import epcodes

    w = _fresh()
    old_pre, old_sync = execute.precheck, epcodes.sync

    class Passed(Exception):
        pass

    def stop(_w):
        raise Passed()

    execute.precheck = lambda _w: {"可以開始": True, "缺": [], "提醒": [], "缺代號": 0}
    epcodes.sync = stop
    try:
        with _FakeCodes(["Emma"]):
            key = _must(w, "英文代號:")[0]["key"]
            assert not execute.final_check(w)["可以開始"]                  # 還有沒處理的列……
            try:
                execute.run_execute(w, log=lambda m: None)
                raise AssertionError("總檢查不該擋")
            except Passed:                                                  # ……照樣開始（預設略過）
                pass
            execute.keep_final(w, key)
            fc = execute.final_check(w)
            assert fc["可以開始"], [r["key"] for r in fc["一定要處理"] if not r["處理好"]]
            try:
                execute.run_execute(w, log=lambda m: None)
                raise AssertionError("應該過了總檢查")
            except Passed:
                pass
    finally:
        execute.precheck, epcodes.sync = old_pre, old_sync


def test_keep_html_and_inspect():
    w = _fresh()
    _outside_setup(w)
    with _FakeCodes(["Emma"]):
        key = _must(w, "英文代號:")[0]["key"]
        execute.keep_final(w, key)
        from bookclub import safeview

        buf = io.StringIO()
        before = wd.read_json(review.review_path(w))
        with redirect_stdout(buf):
            assert safeview.main([str(w), "總檢查"]) == 0
        out = buf.getvalue()
        assert "按了照目前設定做 1" in out and "已按照目前設定做=是" in out and "可以按照目前設定做=是" in out, out
        assert "類別=英文代號" in out and "類別=重疊缺東西" in out and "回第3步補=是" in out and "類別=段落外" in out, out
        assert "Emma" not in out and "不開放原因" not in out and "原聲留在" not in out and "依據=" not in out
        assert wd.read_json(review.review_path(w)) == before
    if not NODE:
        print("（沒有 node，略過網頁）")
        return
    rows = [{"key": "英文代號:x:改稿", "可以按照目前設定做": True, "照目前設定做的後果": "按了：照現在的文字生成"},
            {"key": "段落外:T1:1.0", "可以按照目前設定做": True, "已按照目前設定做": True, "照目前設定做的後果": "這幾秒<照原聲>"},
            {"key": "重疊:O1", "類別": "重疊缺東西", "回第3步補": True, "照目前設定做不開放原因": "這一類要回第 3 步補"},
            {"key": "?:1", "照目前設定做不開放原因": "不行"}]
    got = _node(_ESC + _fn(APPJS, "fcKeepHtml") + f"console.log(JSON.stringify({json.dumps(rows, ensure_ascii=False)}.map((r, i) => fcKeepHtml(r, i))));")
    assert 'data-fckeep="英文代號:x:改稿" data-on="1"' in got[0] and ">照目前設定做<" in got[0] and "照現在的文字生成" in got[0]
    assert 'data-on="0"' in got[1] and "再按取消" in got[1] and "&lt;照原聲&gt;" in got[1]
    assert 'data-fcback="2"' in got[2] and "回第 3 步補" in got[2] and "data-fckeep" not in got[2]
    assert "data-fckeep" not in got[3] and "data-fcback" not in got[3]
    html = _fn(APPJS, "finalCheckHtml")
    assert "不改" not in html and "data-fcheard" not in html                # 不留兩顆做同一件事的按鈕、不再叫「不改」
    assert "!r[\"回第3步補\"]" in html                                     # 回第 3 步補的列不重複放「去第 3 步改這一筆」
    go = _node("const FC_BACK3_CARD = { \"名字換不了代號\": \"老師提到名字\", \"重疊缺東西\": \"重疊\" };\n" + _fn(APPJS, "fcBack3Go") + """
      console.log(JSON.stringify([fcBack3Go({key: "名字:3", "類別": "名字換不了代號", "第3步": "名字:3", "名稱": "A"}),
        fcBack3Go({key: "名字:4", "類別": "名字換不了代號", "名稱": "B"})]));""")
    assert go[0]["key"] == "名字:3" and go[0]["backKey"] == "名字:3"
    assert go[1]["key"] is None and "老師提到名字" in go[1]["note"]


# ---------- #178 請看一眼：每一列「我看過了」＋「全部看過了」 ----------

def test_look_each_row_then_all_seen_and_new_rows_reset():
    w = _fresh()
    m1 = review.manual_edit(w, {"類型": "局部消音", "start": 30.0, "end": 31.0})["id"]
    review.manual_edit(w, {"類型": "局部消音", "start": 40.0, "end": 41.0})
    fc = execute.final_check(w)
    look = fc["請看一眼"]
    rows = [{"key": r["key"], "start": r["start"], "end": r["end"]} for r in look]
    assert len(look) >= 2 and not fc["看過"] and fc["已看過列數"] == 0 and not any(r["已看過"] for r in look)
    for i, r in enumerate(look[:-1]):                                     # 一列一列按，最後一列之前都還不算
        execute.ack_final(w, seen=True, rows=rows, look_key=r["key"])
        fc = execute.final_check(w)
        assert not fc["看過"] and fc["已看過列數"] == i + 1 and not any(x.get("新的") for x in fc["請看一眼"])
    execute.ack_final(w, seen=True, rows=rows, look_key=look[-1]["key"])
    fc = execute.final_check(w)
    assert fc["看過"] and fc["已看過列數"] == len(look) and all(r["已看過"] for r in fc["請看一眼"])
    # 取消其中一列 → 不算全部看過，其他列照樣看過
    execute.ack_final(w, seen=False, rows=rows, look_key=look[0]["key"])
    fc = execute.final_check(w)
    assert not fc["看過"] and fc["已看過列數"] == len(look) - 1
    execute.ack_final(w, seen=True, rows=rows, look_key=look[0]["key"])
    assert execute.final_check(w)["看過"]
    # 全部看過之後多了一列：那一列標「新的」、沒看過，其他維持看過
    m3 = review.manual_edit(w, {"類型": "局部消音", "start": 50.0, "end": 51.0})["id"]
    fc = execute.final_check(w)
    new = [r for r in fc["請看一眼"] if r.get("新的")]
    assert not fc["看過"] and fc["看過後新增"] == 1 and [r["key"] for r in new] == [f"消音:{m3}"] and not new[0]["已看過"]
    assert fc["已看過列數"] == len(fc["請看一眼"]) - 1
    rows = [{"key": r["key"], "start": r["start"], "end": r["end"]} for r in fc["請看一眼"]]
    execute.ack_final(w, seen=True, rows=rows, look_key=f"消音:{m3}")
    assert execute.final_check(w)["看過"]
    # 某一列範圍變了：只有那一列回到沒看過
    review.manual_edit(w, {"類型": "局部消音", "id": m1, "start": 30.0, "end": 32.5})
    fc = execute.final_check(w)
    assert not fc["看過"] and [r["key"] for r in fc["請看一眼"] if not r["已看過"]] == [f"消音:{m1}"]
    # 「全部看過了」一次全按；再按取消全部
    execute.ack_final(w, seen=True, rows=[{"key": r["key"], "start": r["start"], "end": r["end"]} for r in fc["請看一眼"]])
    assert execute.final_check(w)["看過"]
    execute.ack_final(w, seen=False)
    fc = execute.final_check(w)
    assert not fc["看過"] and fc["已看過列數"] == 0
    try:
        execute.ack_final(w, seen=True, rows=rows, look_key="不存在:1")
        raise AssertionError("不在清單的列不該能按")
    except ValueError:
        pass
    # 畫面是舊的（那一列的起訖跟伺服器現在算的不一樣）→ 不記，請人重新整理；記下的起訖一律用伺服器算的
    fc = execute.final_check(w)
    r0 = fc["請看一眼"][0]
    stale = [{"key": r0["key"], "start": r0["start"] - 3, "end": r0["end"]}]
    try:
        execute.ack_final(w, seen=True, rows=stale, look_key=r0["key"])
        raise AssertionError("畫面上的範圍是舊的，不該記")
    except ValueError as e:
        assert "重新整理" in str(e)
    execute.ack_final(w, seen=True, look_key=r0["key"])
    snap = review.load_decisions(w)["總檢查"]["看過的列"]
    assert next(x for x in snap if x["key"] == r0["key"])["start"] == round(r0["start"], 3)


def test_look_legacy_seen_flag_counts_rows_as_seen():
    """舊資料（只記了 看過、看過時間）：之前算看過的列照樣顯示看過；在這上面按其中一列不會把其他列洗掉。"""
    w = _fresh()
    review.manual_edit(w, {"類型": "局部消音", "start": 30.0, "end": 31.0})
    dec = review.load_decisions(w)
    dec.setdefault("總檢查", {}).update({"看過": True, "看過時間": "2999-01-01T00:00:00"})
    review._save_decisions(w, dec)
    fc = execute.final_check(w)
    assert fc["看過"] and all(r["已看過"] for r in fc["請看一眼"])
    k = fc["請看一眼"][0]["key"]
    execute.ack_final(w, seen=False, look_key=k)
    fc = execute.final_check(w)
    assert not fc["看過"] and [r["key"] for r in fc["請看一眼"] if not r["已看過"]] == [k]
    # 純函式
    look = [{"key": "a", "start": 1.0, "end": 2.0}, {"key": "b", "start": 3.0, "end": 4.0}]
    snap = execute.look_snapshot(look[:1])
    assert execute.look_state({"看過的列": snap}, look) == (False, [], {"a"})
    assert execute.look_state({"看過": True, "看過的列": snap}, look) == (False, ["b"], {"a"})
    assert execute.look_state({"看過的列": execute.look_snapshot(look)}, look)[0] is True
    assert execute.look_state({"看過": False}, []) == (False, [], set())
    assert execute.look_state({"看過": True, "看過的列": []}, []) == (True, [], set())


def test_look_html_buttons_near_title_and_no_bottom_checkbox():
    fn = _fn(APPJS, "finalCheckHtml")
    assert 'id="fcSeen"' not in fn and "fcSeen\"" not in fn                 # 最下面的勾選拿掉了，不留兩套
    head = fn.index('id="fcLookHead"')
    # 10-08 宇軒（流程簡化）：請看一眼收合成一行摘要（預設先略過），展開後「全部看過了」在清單上面
    assert fn.index('data-fcfold="請看一眼"') < head and fn.index('id="fcSeenAll"') > head
    assert fn.index('id="fcSeenAll"') < fn.index("look.map((r) => row(r, true))")   # 「全部看過了」在標題旁、清單上面
    if not NODE:
        print("（沒有 node，略過網頁）")
        return
    got = _node(_ESC + _fn(APPJS, "fcLookBtnHtml") + """console.log(JSON.stringify([
      fcLookBtnHtml({key: "消音:M1", "已看過": false}), fcLookBtnHtml({key: "消音:M2", "已看過": true})]));""")
    assert 'data-fclook="消音:M1" data-on="1"' in got[0] and ">我看過了<" in got[0]
    assert 'data-fclook="消音:M2" data-on="0"' in got[1] and "再按取消" in got[1]


def test_server_routes_keep_and_look():
    """POST /api/execute/finalcheck：帶「不改」走 keep_final、帶「看一眼」走那一列的我看過了（直接呼叫路由，不開網路埠）。"""
    from bookclub import server as srv

    w = _fresh()
    review.manual_edit(w, {"類型": "局部消音", "start": 30.0, "end": 31.0})
    sent = []

    class H:
        server = type("S", (), {"workdir": w})()

        def _send_json(self, code, data):
            sent.append((code, data))

    with _FakeCodes(["Emma"]):
        key = _must(w, "英文代號:")[0]["key"]
        srv.Handler._route_post_api(H(), "/api/execute/finalcheck", {"key": key, "照目前設定做": True})
        assert sent[-1][0] == 200 and _must(w, "英文代號:")[0]["處理好"]
        srv.Handler._route_post_api(H(), "/api/execute/finalcheck", {"key": key, "不改": False})   # 舊名字也收
        assert not _must(w, "英文代號:")[0]["處理好"]
        look = execute.final_check(w)["請看一眼"]
        rows = [{"key": r["key"], "start": r["start"], "end": r["end"]} for r in look]
        srv.Handler._route_post_api(H(), "/api/execute/finalcheck", {"看一眼": look[0]["key"], "看過": True, "看過的列": rows})
        assert sent[-1][0] == 200
        assert next(r for r in execute.final_check(w)["請看一眼"] if r["key"] == look[0]["key"])["已看過"]


def _run_all() -> int:
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, func in tests:
        try:
            func()
            print(f"✓ {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback

            traceback.print_exc()
            print(f"✗ {name}：{exc!r}")
    print(f"{len(tests) - failed}/{len(tests)} 通過")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
