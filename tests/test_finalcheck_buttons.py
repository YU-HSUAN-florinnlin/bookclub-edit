"""10-05 第 4 步「開始前總檢查」三項修改（宇軒 10-05 實際操作後提出）：
- #176「跳過去聽」只播那一列的起點到終點（不加前後緩衝）
- #177「一定要處理」每一列都有「不改（維持目前設定）」：可以按的類別按了算處理完、可以取消、存在覆核資料、inspect 看得到；
  維持現狀會讓學員原聲或名字留在成品裡的類別先不開放（按鈕灰掉、後端也擋）
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


# ---------- #177 不改（維持目前設定） ----------

def test_keep_policy_per_kind():
    for k in ("英文代號:學員段落:T1:改稿", "字太少:N001"):
        assert execute.keep_policy(k) == (True, ""), k
    for k in ("段落外:T1:12.0", "聲紋:S12", "聲紋:S12:3.4", "沒有字:131.5", "名字:3", "重疊:O80.20", "以後新的類別:1"):
        ok, why = execute.keep_policy(k)
        assert not ok and why, k
    assert "學員的聲音" in execute.keep_policy("段落外:T1:1.0")[1] and "名字" in execute.keep_policy("沒有字:1.0")[1]


def test_keep_marks_row_done_persists_and_can_undo():
    w = _fresh()
    with _FakeCodes(["Emma"]):
        r = _must(w, "英文代號:")[0]
        assert r["可以按不改"] and not r["已按不改"] and not r["處理好"] and not r.get("可以按聽過")
        before = execute.final_check(w)
        execute.keep_final(w, r["key"])
        dec = review.load_decisions(w)                                     # 存在覆核資料（重新整理頁面後還在）
        saved = dec["總檢查"][execute.KEEP_FIELD][r["key"]]
        assert set(saved) == {"start", "end", "依據", "時間"} and "Emma" not in json.dumps(saved, ensure_ascii=False)
        after = execute.final_check(w)
        r2 = next(x for x in after["一定要處理"] if x["key"] == r["key"])
        assert r2["已按不改"] and r2["處理好"] and after["已按不改"] == 1
        assert after["還要處理"] == before["還要處理"] - 1 and after["已確認"] == before["已確認"] + 1
        # 內容變了（代號多了一個）→ 不再算，標「按了不改之後變了」
    with _FakeCodes(["Emma", "Rose"]):
        r3 = _must(w, "英文代號:")[0]
        assert not r3["已按不改"] and r3["不改後變了"] and not r3["處理好"]
    with _FakeCodes(["Emma"]):
        assert _must(w, "英文代號:")[0]["已按不改"]                         # 改回來：照算
        execute.keep_final(w, r["key"], keep=False)                        # 反悔
        r4 = _must(w, "英文代號:")[0]
        assert not r4["已按不改"] and not r4["處理好"] and not r4.get("不改後變了")
        assert r["key"] not in review.load_decisions(w)["總檢查"][execute.KEEP_FIELD]


def test_keep_refused_for_risky_kinds():
    w = _fresh()
    oid = review.manual_edit(w, {"類型": "重疊", "start": 80.2, "end": 81.0})["id"]
    review.save_overlap(w, oid, {"做法": "只留學員"})                      # 缺學員是誰 → 一定要處理（重疊）
    t = next(t for t in review.page_data(w)["項目"] if t["類型"] == "學員段落")
    review.retime_turn(w, t["id"], t["start"] + 1.0, t["end"])             # 段落外（可以按「我聽過了」那一類）
    fc = execute.final_check(w)
    risky = [r for r in fc["一定要處理"] if r["key"].split(":")[0] in ("重疊", "段落外")]
    assert {r["key"].split(":")[0] for r in risky} == {"重疊", "段落外"}, [r["key"] for r in fc["一定要處理"]]
    for r in risky:
        assert not r["可以按不改"] and r["不改不開放原因"] and not r["已按不改"]
        try:
            execute.keep_final(w, r["key"])
            raise AssertionError(f"{r['key']} 不該能按不改")
        except ValueError as e:
            assert "成品" in str(e)
    # 手動塞進存檔也不算（不開放的類別一律不算處理好）
    dec = review.load_decisions(w)
    dec.setdefault("總檢查", {})[execute.KEEP_FIELD] = {r["key"]: {"start": r["start"], "end": r["end"], "依據": "x", "時間": "t"} for r in risky}
    review._save_decisions(w, dec)
    fc = execute.final_check(w)
    assert not any(r["已按不改"] for r in fc["一定要處理"]) and not fc["可以開始"]
    try:
        execute.keep_final(w, "英文代號:不存在:改稿")
        raise AssertionError("不在清單的列不該能按")
    except ValueError as e:
        assert "不在" in str(e)


def test_keep_html_and_inspect():
    w = _fresh()
    with _FakeCodes(["Emma"]):
        key = _must(w, "英文代號:")[0]["key"]
        execute.keep_final(w, key)
        from bookclub import safeview

        buf = io.StringIO()
        before = wd.read_json(review.review_path(w))
        with redirect_stdout(buf):
            assert safeview.main([str(w), "總檢查"]) == 0
        out = buf.getvalue()
        assert "按了不改 1" in out and "已按不改=是" in out and "可以按不改=是" in out and "處理好=是" in out, out
        assert "Emma" not in out and "不改不開放原因" not in out and "維持現狀" not in out   # 不印說明文字、代號
        assert wd.read_json(review.review_path(w)) == before
    if not NODE:
        print("（沒有 node，略過網頁）")
        return
    rows = [{"key": "英文代號:x:改稿", "可以按不改": True, "已按不改": False},
            {"key": "英文代號:y:改稿", "可以按不改": True, "已按不改": True},
            {"key": "段落外:T1:1.0", "可以按不改": False, "不改不開放原因": "維持現狀的話<學員>原聲會留著"}]
    got = _node(_ESC + _fn(APPJS, "fcKeepHtml") + f"console.log(JSON.stringify({json.dumps(rows, ensure_ascii=False)}.map(fcKeepHtml)));")
    assert 'data-fckeep="英文代號:x:改稿" data-on="1"' in got[0] and ">不改（維持目前設定）<" in got[0]
    assert 'data-on="0"' in got[1] and "再按取消" in got[1] and 'aria-pressed="true"' in got[1]
    assert "disabled" in got[2] and "data-fckeep" not in got[2] and "&lt;學員&gt;" in got[2]


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
    assert fn.index("<b>請看一眼</b>") > head and fn.index('id="fcSeenAll"') > head
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
        srv.Handler._route_post_api(H(), "/api/execute/finalcheck", {"key": key, "不改": True})
        assert sent[-1][0] == 200 and _must(w, "英文代號:")[0]["已按不改"]
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
