"""10-16 #234 #235：第 3 步「③ 辨識其他名稱如何替換」——選完先存、補找老師提到的名字改到背景做；每個名字一個下拉選單。

測的事：
- 補找很慢的時候，選了馬上回應、決定已經存好；連續快速選好幾個名字（同時送出）全部都存到，不會互相蓋掉
- 補找的途中又有人選：做完會再補找一輪（新加進名冊的名字不漏）
- 伺服器在補找途中關掉（「待補找」還在、沒有背景在做）：打開網頁就補做，補找到的名字照樣加進去
- 補找出錯：留著「待補找」，網頁輪詢不重試，重新打開網頁才再試
- 舊工作區的 `校對/人名決定.json`（沒有新欄位）照樣讀得懂
- 網頁：一個下拉選單選完只更新那一列；兩列同時存檔，兩個都送出去、都記下來

全部是合成資料（假名＋自編句子）。獨立可跑：.venv/bin/python tests/test_people_rescan.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import fake_workdir  # noqa: E402

_DATA = Path(tempfile.mkdtemp())
_ROSTER = "中文名,其他寫法,性別\n小美,美美,女\n阿明,,男\n"
os.environ["BOOKCLUB_DATA_DIR"] = str(_DATA)

from bookclub import epcodes, personnames  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

NAMES = ["阿強", "阿德", "路人甲", "某作者"]


def _make() -> Path:
    """假工作區：老師的幾句話裡提到 阿強、阿德（名冊上還沒有）；人名清單列 4 個名冊上沒有的名字。"""
    (_DATA / "名冊.csv").write_text(_ROSTER, encoding="utf-8")
    root = Path(tempfile.mkdtemp()) / "base"
    w = fake_workdir.make(root)
    sp = json.loads((w / "說話者判斷.json").read_text(encoding="utf-8"))
    teach = [k for k, s in enumerate(sp["sentences"]) if s.get("label") == "老師"]
    sp["sentences"][teach[2]]["text"] = "阿強你要不要說說看"
    sp["sentences"][teach[5]]["text"] = "阿德上次也有提到"
    (w / "說話者判斷.json").write_text(json.dumps(sp, ensure_ascii=False), encoding="utf-8")
    m = json.loads((w / "transcript" / "merged.json").read_text(encoding="utf-8"))
    m["words"] = fake_workdir._words(sp["sentences"])
    (w / "transcript" / "merged.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    people = [{"id": f"P{i:03d}", "名字": n, "其他寫法": [], "是誰": "書中人物或作者" if n == "某作者" else "其他人",
               "說明": "", "句子": [], "次數": 1, "老師說": 1, "學員說": 0, "第一次": 10.0 + i} for i, n in enumerate(NAMES)]
    pp = personnames.people_path(w)
    d = json.loads(pp.read_text(encoding="utf-8"))
    d["人名"] = people
    pp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return w


def _slow_scan():
    """把補找換成「等到放行才做」，記下做了幾輪。"""
    gate = threading.Event()
    calls = []
    real = personnames._scan_teacher

    def fake(workdir):
        calls.append(time.time())
        gate.wait(30)
        return real(workdir)

    personnames._scan_teacher = fake
    return gate, calls, real


def test_decide_answers_fast_and_quick_picks_all_saved():
    w = _make()
    gate, calls, real = _slow_scan()
    try:
        t0 = time.time()
        res = personnames.decide(w, "阿強", "換成代號", "傑克")
        assert time.time() - t0 < 2.0 and res["補找中"], res   # 不用等補找
        assert personnames.rescan_status(w)["補找中"]
        # 補找還卡著，其他三列同時送出（老師連續快速選）
        errs = []

        def pick(name, how, code=None):
            try:
                personnames.decide(w, name, how, code)
            except Exception as e:  # noqa: BLE001
                errs.append(e)

        ths = [threading.Thread(target=pick, args=a) for a in
               (("阿德", "換成代號", "湯姆"), ("路人甲", "不是名字"), ("某作者", "不用處理"))]
        t1 = time.time()
        for t in ths:
            t.start()
        for t in ths:
            t.join(10)
        assert not errs and time.time() - t1 < 5.0, errs
        dec = wd.read_json(personnames.decisions_path(w))
        assert {n: dec[n]["做法"] for n in NAMES} == {"阿強": "換成代號", "阿德": "換成代號", "路人甲": "不是名字", "某作者": "不用處理"}
        assert dec["阿強"]["代號"] == "傑克" and dec["阿德"]["代號"] == "湯姆"
        n0 = len(wd.read_json(wd.names_path(w))["candidates"])
        gate.set()
        st = personnames.wait_rescan(w, 120)
        assert not st["補找中"] and not st["錯誤"], st
        # 補找的途中又加了「阿德」→ 做完再補找一輪，兩個名字都找到
        assert len(calls) >= 2, calls
        cands = wd.read_json(wd.names_path(w))["candidates"]
        found = {c["canonical"]: c["代號"] for c in cands[n0:]} | {c["canonical"]: c["代號"] for c in cands if c.get("補找")}
        assert found.get("阿強") == "傑克" and found.get("阿德") == "湯姆", found
        assert st["未通知"] >= 2
        personnames.rescan_seen(w)
        assert personnames.rescan_status(w)["未通知"] == 0
        assert not wd.read_json(personnames.rescan_path(w))["待補找"]
    finally:
        personnames._scan_teacher = real
        gate.set()
        personnames.wait_rescan(w, 60)


def test_pending_rescan_after_restart_is_done_on_open():
    w = _make()
    # 模擬：存了決定、名字加進名冊，背景補找還沒做完伺服器就關了（只剩檔案裡的「待補找」）
    personnames.add_to_roster("阿強", [])
    wd.write_json(personnames.decisions_path(w), {"阿強": {"做法": "換成代號", "代號": "傑克", "同一人": None, "更新時間": "2026-10-16T20:00:00"}})
    wd.write_json(personnames.rescan_path(w), {"待補找": True, "要求": 1, "未通知": 0})
    assert not personnames._workers.get(str(w.resolve()))
    gate, calls, real = _slow_scan()
    try:
        assert personnames.rescan_busy(w)   # 第 4 步開始前：還沒補找完（同時開背景補做）
        gate.set()
        st = personnames.wait_rescan(w, 120)
        assert not st["補找中"] and st["未通知"] >= 1, st
        assert any(c["canonical"] == "阿強" and c.get("補找") and c["代號"] == "傑克"
                   for c in wd.read_json(wd.names_path(w))["candidates"])
        assert not personnames.rescan_busy(w)
    finally:
        personnames._scan_teacher = real
        gate.set()


def test_rescan_error_keeps_pending_until_page_reopens():
    w = _make()
    real = personnames._scan_teacher
    gate = threading.Event()

    def boom(workdir):
        raise RuntimeError("假的錯誤")

    personnames._scan_teacher = boom
    retry0 = personnames.RETRY_AFTER_S
    try:
        personnames.decide(w, "阿強", "換成代號", "傑克")
        st = personnames.wait_rescan(w, 30)
        # 網頁只看到白話，不給例外字樣
        assert not st["補找中"] and st["錯誤"] == personnames.ERROR_MSG and "RuntimeError" not in st["錯誤"], st
        assert wd.read_json(personnames.rescan_path(w))["待補找"]   # 沒有默默當成做完
        assert not personnames.ensure_rescan(w)                     # 網頁輪詢不重試
        assert not personnames.ensure_rescan(w, retry=True)         # 剛出錯：打開網頁也先不重掃（隔 RETRY_AFTER_S）
        # 第 4 步不會永遠擋住：背景沒在做 → 不算「還在找」；出錯提醒一次，再按就放行
        assert not personnames.rescan_busy(w)
        assert personnames.rescan_error_gate(w).startswith(personnames.ERROR_MSG)
        assert personnames.rescan_error_gate(w) is None

        # 重試的那一輪在跑的時候：狀態是「補找中」，不顯示錯誤；第 4 步等它
        def slow(workdir):
            gate.wait(30)
            return real(workdir)

        personnames._scan_teacher = slow
        personnames.RETRY_AFTER_S = 0
        assert personnames.ensure_rescan(w, retry=True)
        st = personnames.rescan_status(w)
        assert st["補找中"] and st["錯誤"] is None and personnames.rescan_busy(w), st
        gate.set()
        st = personnames.wait_rescan(w, 120)
        assert not st["補找中"] and not st["錯誤"] and st["未通知"] >= 1, st
        assert personnames.rescan_error_gate(w) is None
    finally:
        personnames._scan_teacher = real
        personnames.RETRY_AFTER_S = retry0
        gate.set()
        personnames.wait_rescan(w, 60)


def test_turn_append_does_not_overwrite_background_merge():
    """審查 #3：② 把一段改成老師（turns → names.append_candidates）掃的途中，背景補找先接上了；
    以前 append_candidates 讀→掃→寫整份，會把背景加的名字蓋掉（名字漏消）。"""
    from bookclub import names

    w = _make()
    real_find = names.find_names
    gate = threading.Event()
    entered = threading.Event()

    def find(*a, **k):   # 只卡住「改成老師」那一個執行緒的掃描；背景補找照常
        if threading.current_thread().name == "改成老師":
            entered.set()
            gate.wait(30)
        return real_find(*a, **k)

    names.find_names = find
    try:
        stu = ["00_023"]   # 假資料：學員 2 說「我跟小美一樣…」（名冊上的小美），改成老師後補找得到
        out = {}
        t = threading.Thread(target=lambda: out.setdefault("n", names.append_candidates(w, stu)), name="改成老師")
        t.start()
        assert entered.wait(30)
        personnames.decide(w, "阿強", "換成代號", "傑克")   # 背景補找：名冊加了阿強
        st = personnames.wait_rescan(w, 120)
        assert st["未通知"] >= 1
        before = [c for c in wd.read_json(wd.names_path(w))["candidates"] if c["canonical"] == "阿強"]
        assert before
        gate.set()
        t.join(60)
        after = [c for c in wd.read_json(wd.names_path(w))["candidates"] if c["canonical"] == "阿強"]
        assert len(after) == len(before) and after[0]["代號"] == "傑克", (before, after)
        assert out.get("n") == 1   # 改成老師那邊補到的也在
        assert any(c["sentence_id"] == "00_023" for c in wd.read_json(wd.names_path(w))["candidates"])
    finally:
        names.find_names = real_find
        gate.set()


def test_merge_skipped_when_input_changed_during_scan():
    """掃的途中第 1 步重跑（名字候選的輸入指紋變了）：這輪不接上，回 None 要重掃。"""
    from bookclub import names

    w = _make()
    personnames.add_to_roster("阿強", [])
    scan = names.scan_candidates(w, personnames._teacher_sentence_ids(w))
    assert scan and scan["candidates"]
    cur = wd.read_json(wd.names_path(w))
    cur["輸入指紋"] = "第 1 步重跑過"
    wd.write_json(wd.names_path(w), cur)
    n0 = len(cur["candidates"])
    assert names.merge_candidates(w, scan, "測試") is None
    assert len(wd.read_json(wd.names_path(w))["candidates"]) == n0


def test_pinyin_cache_same_hits():
    """`names._syllable_parts` 加了快取：比對到的候選跟不快取時一模一樣。"""
    from bookclub import names

    w = _make()
    personnames.add_to_roster("阿強", [])
    personnames.add_to_roster("阿德", [])
    ids = personnames._teacher_sentence_ids(w)
    cached = names._syllable_parts
    names._syllable_parts = cached.__wrapped__   # 不快取
    try:
        plain = names.scan_candidates(w, ids)["candidates"]
    finally:
        names._syllable_parts = cached
    cached.cache_clear()
    fast = names.scan_candidates(w, ids)["candidates"]
    key = lambda c: (c["sentence_id"], c["start"], c["end"], c["canonical"], c["比對層級"])  # noqa: E731
    assert plain and [key(c) for c in plain] == [key(c) for c in fast]
    assert cached.cache_info().hits > 0


def test_seen_allowed_while_executing():
    from bookclub import server

    assert "/api/people/rescan/seen" in server.STEP3_WRITES_OK


def test_old_decisions_file_still_read():
    w = _make()
    epcodes.sync(w)
    # 10-16 以前的格式：只有 做法／代號／同一人／更新時間（也有更早沒有「同一人」的），沒有補找檔
    from bookclub import turns as turns_mod

    turns_mod.set_name_code(w, "小美", "安娜", add_roster=False)
    wd.write_json(personnames.decisions_path(w), {
        "阿強": {"做法": "換成代號", "代號": "傑克", "更新時間": "2026-10-01T10:00:00"},
        "阿德": {"做法": "是上面的學員", "代號": "安娜", "同一人": "小美", "更新時間": "2026-10-01T10:00:00"},
        "路人甲": {"做法": "不是名字", "代號": None, "更新時間": "2026-10-01T10:00:00"},
    })
    rows = {u["名字"]: u for u in personnames.mentioned(w)}
    assert (rows["阿強"]["做法"], rows["阿強"]["代號"], rows["阿強"]["已決定"]) == ("換成代號", "傑克", True)
    assert (rows["阿德"]["做法"], rows["阿德"]["同一人"], rows["阿德"]["代號"]) == ("是上面的學員", "小美", "安娜")
    assert rows["路人甲"]["做法"] == "不是名字" and rows["某作者"]["做法"] == "不用處理"
    assert epcodes.episode_codes(w).get("阿強") == "傑克"
    assert personnames.rescan_status(w) == {"補找中": False, "未通知": 0, "錯誤": None}


# ---------- 網頁 ----------

NODE = shutil.which("node")
WEB = REPO_ROOT / "bookclub" / "web"
HARNESS = REPO_ROOT / "tests" / "web_harness.js"

_JS = r"""
const { makeEnv } = require(process.argv[2]);
(async () => {
  const ctx = makeEnv(process.argv[3].split(","), "");
  const out = await require("vm").runInContext(`(async () => {
    rv.data = {
      "提到的名字": [
        {id: "P1", "名字": "阿強", "其他寫法": [], "是誰": "其他人", "說明": "", "名冊本名": null, "次數": 1, "老師說": 1, "學員說": 0,
         "第一次": 1, "刪除段落外次數": 1, "都在刪除段落": false, "② 已決定": false, "做法": null, "代號": null, "同一人": null, "自動配": false, "已決定": false},
        {id: "P2", "名字": "美美", "其他寫法": [], "是誰": "學員", "說明": "", "名冊本名": null, "次數": 2, "老師說": 2, "學員說": 0,
         "第一次": 2, "刪除段落外次數": 2, "都在刪除段落": false, "② 已決定": false, "做法": null, "代號": null, "同一人": null, "自動配": false, "已決定": false},
        {id: "P3", "名字": "某作者", "其他寫法": [], "是誰": "書中人物或作者", "說明": "", "名冊本名": null, "次數": 1, "老師說": 1, "學員說": 0,
         "第一次": 3, "刪除段落外次數": 1, "都在刪除段落": false, "② 已決定": false, "做法": "不用處理", "代號": null, "同一人": null, "自動配": false, "已決定": false}],
      "學員": {"學員1": {"本名": "小美"}, "學員2": {"本名": "阿明"}},
      "這一集代號": {"小美": "安娜"},
      "代號分組": {"女": ["安娜", "貝拉"], "男": ["湯姆"], "這一集用到的其他代號": []},
      "代號對照": {"代號給了": {"安娜": ["小美"]}, "撞名": {}},
    };
    const html = rvPrepNamesHtml();
    const box = H.makeEl("div", {}, ""); box.innerHTML = html;
    // 送出先卡住，看兩列同時存檔
    const pend = [];
    globalThis.apiPost = (url, body) => { H.log.push(["POST", url, body]); return new Promise((r) => pend.push(() => r({ok: true, "補找中": false}))); };
    rvBindNameRow(box);
    const sels = box.querySelectorAll(".rv-unpick");
    const fire = (el) => H.listeners.find((l) => l.el === el && l.type === "change").fn({target: el});
    sels[0].value = "code:貝拉"; const p1 = fire(sels[0]);
    sels[1].value = "same:小美"; const p2 = fire(sels[1]);
    const savingBoth = Object.keys(rv.nameSaving).sort();
    const rowSaving = rvNameRowHtml(rv.data["提到的名字"][0]);
    pend.forEach((f) => f()); await p1; await p2;
    return {html, savingBoth, rowSaving, posts: H.log.filter((x) => x[0] === "POST"), saving: Object.keys(rv.nameSaving),
            rows: rv.data["提到的名字"].map((u) => [u["名字"], u["做法"], u["代號"], u["同一人"], u["已決定"]]),
            after: rvNameRowHtml(rv.data["提到的名字"][1])};
  })()`, ctx);
  process.stdout.write(JSON.stringify(out));
})();
"""


_JS_SEEN = r"""
const { makeEnv } = require(process.argv[2]);
(async () => {
  const out = {};
  for (const ok of [true, false]) {
    const ctx = makeEnv(process.argv[3].split(","), "");
    out[ok ? "ok" : "fail"] = await require("vm").runInContext(`(async () => {
      rv.data = {"人名補找": {"補找中": false, "未通知": 3}};
      globalThis.apiGet = async (u) => { H.log.push(["GET", u]); return {"補找中": false, "未通知": 3, "錯誤": null}; };
      globalThis.apiPost = async (u) => { H.log.push(["POST", u]); if (!${ok}) throw new Error("第 4 步執行中"); return {ok: true}; };
      await rvRescanPoll();
      return {alerts: H.log.filter((x) => x[0] === "alert").length, mute: !!rv.rescanMute};
    })()`, ctx);
  }
  process.stdout.write(JSON.stringify(out));
})();
"""


def test_web_seen_fail_no_alert_loop():
    if not NODE:
        print("（沒有 node，略過）")
        return
    d = Path(tempfile.mkdtemp())
    (d / "t.js").write_text(_JS_SEEN, encoding="utf-8")
    files = ",".join(str(WEB / f) for f in ("cards.js", "review.js") if (WEB / f).is_file())
    r = subprocess.run([NODE, str(d / "t.js"), str(HARNESS), files], capture_output=True, text=True, check=True)
    out = json.loads(r.stdout)
    assert out["ok"] == {"alerts": 1, "mute": False} and out["fail"] == {"alerts": 0, "mute": True}, out


def test_web_one_dropdown_and_parallel_saves():
    if not NODE:
        print("（沒有 node，略過）")
        return
    d = Path(tempfile.mkdtemp())
    (d / "t.js").write_text(_JS, encoding="utf-8")
    files = ",".join(str(WEB / f) for f in ("cards.js", "review.js") if (WEB / f).is_file())
    r = subprocess.run([NODE, str(d / "t.js"), str(HARNESS), files], capture_output=True, text=True, check=True)
    out = json.loads(r.stdout)
    html = out["html"]
    # 一個下拉選單：② 的學員（同一人）、英文名、不是名字、不用處理；沒有以前的「換成代號」「是上面的學員」字樣
    assert html.count('class="rv-unpick"') == 3 and "rv-unhow" not in html
    assert "同一人：小美（安娜）" in html and "同一人：阿明（② 還沒選英文名）" in html
    assert "換成英文名（女）" in html and "自己打一個英文名…" in html
    assert "換成代號" not in html and "是上面的學員" not in html and "上面" not in html
    assert 'value="how:不用處理" selected' in html
    # 兩列同時在存：都鎖住、都送出、都記下來
    assert out["savingBoth"] == ["美美", "阿強"] and "存檔中…" in out["rowSaving"] and "disabled" in out["rowSaving"]
    assert [p[2] for p in out["posts"]] == [{"名字": "阿強", "做法": "換成代號", "代號": "貝拉"},
                                           {"名字": "美美", "做法": "是上面的學員", "同一人": "小美"}], out["posts"]
    assert out["saving"] == []
    assert out["rows"][:2] == [["阿強", "換成代號", "貝拉", None, True], ["美美", "是上面的學員", "安娜", "小美", True]]
    assert 'value="same:小美" selected' in out["after"] and "存檔中" not in out["after"]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
