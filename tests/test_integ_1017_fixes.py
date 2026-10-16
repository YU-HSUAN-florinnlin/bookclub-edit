"""1017 合併前總審查、發版前全項檢查抓到的問題（整合-1017 分支）。先寫這支、在舊程式上確認失敗，再改到通過。

1. 已結案的工作區：背景人名補找不能再寫入（`no_writes()` 管不到背景執行緒）——打開 /api/review、/api/state、
   GET /api/people/rescan 都不新增、不修改任何檔；補找真的在跑時，結案打包、清空間要擋（白話）
2. 名冊上的人（② 沒選、沒有代號）在 ③ 選「不用處理／不是名字」：學員重念稿裡的名字也要拿掉（跟老師句子一樣換成空的）
3. 第 4 步執行中打開第 3 步：不重試出錯的補找
4. 已結案的影片按「開始分析」：給老師看的訊息不出現開發者指令
5. ③ 名冊上的人那一列：「不用處理」「不是名字」標「（名字照樣拿掉）」；說明縮短

全部是合成資料（假名＋自編句子）。獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_integ_1017_fixes.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 暫存資料夾跑完自己清（要在 tempfile 之前）

import hashlib
import json
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

import test_people_rescan as T  # noqa: E402 — 合成工作區、名冊（設好 BOOKCLUB_DATA_DIR）

from bookclub import closing, epcodes, personnames, review, students  # noqa: E402
from bookclub import turns as turns_mod  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

WEB = REPO_ROOT / "bookclub" / "web"
NODE = shutil.which("node")


def _snap(w: Path) -> dict:
    return {str(p.relative_to(w)): hashlib.md5(p.read_bytes()).hexdigest() for p in sorted(w.rglob("*")) if p.is_file()}


def _closed_with_pending() -> Path:
    """已結案、但「待補找」還留著（例如補找出錯、第 4 步按第二次放行，之後輸出、打包、清完）。"""
    w = T._make()
    wd.write_json(personnames.rescan_path(w), {"待補找": True, "要求": 1, "未通知": 0, "錯誤": "X", "錯誤時間": 0,
                                                "錯誤已提醒": True})
    wd.write_json(closing.record_path(w), {"狀態": closing.CLOSED})
    return w


def _serve(w: Path):
    from bookclub import server as srv

    httpd = srv.BookclubServer(("127.0.0.1", 0), srv.Handler, workdir=w, video=None)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _call(base, path, body=None):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ---------- 1. 已結案不補找 ----------

def test_1_closed_no_background_rescan_writes():
    w = _closed_with_pending()
    before = _snap(w)
    with wd.no_writes():
        assert personnames.ensure_rescan(w, retry=True) is False
    assert personnames.request_rescan(w) is False
    personnames.wait_rescan(w, 30)
    key = str(w.resolve())
    personnames._rescan_worker(w, key)   # 背景執行緒本身：每一輪開始也看結案
    after = _snap(w)
    assert after == before, [k for k in after if before.get(k) != after[k]] + [k for k in before if k not in after]


def test_1_closed_api_open_pages_change_nothing():
    w = _closed_with_pending()
    before = _snap(w)
    httpd, base = _serve(w)
    try:
        for p in ("/api/state", "/api/review", "/api/people/rescan"):
            code, r = _call(base, p)
            assert code == 200, (p, code, r)
        _call(base, "/api/people/rescan")
        personnames.wait_rescan(w, 30)
        time.sleep(0.2)
        after = _snap(w)
        assert after == before, [k for k in after if before.get(k) != after[k]] + [k for k in before if k not in after]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_1_rescan_running_blocks_pack_and_clean():
    from bookclub import server as srv

    w = T._make()
    httpd = srv.BookclubServer(("127.0.0.1", 0), srv.Handler, workdir=w, video=None)
    gate = threading.Event()
    th = threading.Thread(target=lambda: gate.wait(10), daemon=True)
    th.start()
    key = str(w.resolve())
    personnames._workers[key] = th   # 背景補找真的在跑
    try:
        why = httpd._close_busy()
        assert why and "還在找老師提到的名字" in why and "再結案打包" in why, why
        why = httpd._clean_busy()
        assert why and "還在找老師提到的名字" in why and "再清" in why, why
        gate.set()
        th.join(5)
        assert httpd._close_busy() is None and httpd._clean_busy() is None
        # 判斷「在跑」不能順手開補找（待補找還在、沒有背景）
        wd.write_json(personnames.rescan_path(w), {"待補找": True, "要求": 1})
        personnames._workers.pop(key, None)
        orig = personnames.ensure_rescan
        personnames.ensure_rescan = lambda *a, **k: (_ for _ in ()).throw(AssertionError("不該開補找"))
        try:
            assert httpd._close_busy() is None and httpd._clean_busy() is None
        finally:
            personnames.ensure_rescan = orig
    finally:
        gate.set()
        personnames._workers.pop(key, None)
        httpd.server_close()


# ---------- 2. 名冊上的人選不用處理／不是名字：學員重念稿也拿掉 ----------

ROSTER2 = "中文名,其他寫法,性別\n小美,美美,女\n阿明,,男\n小華,華華,女\n"
STUDENT_LINE = "我覺得美美說的一樣，華華沒來，阿明也是"


def _privacy_workdir(how: str) -> Path:
    w = T._make()
    (T._DATA / "名冊.csv").write_text(ROSTER2, encoding="utf-8")
    sp = json.loads((w / "說話者判斷.json").read_text(encoding="utf-8"))
    teach = [k for k, s in enumerate(sp["sentences"]) if s.get("label") == "老師"]
    sp["sentences"][teach[3]]["text"] = "小華上次也有提到"
    k2 = next(k for k, s in enumerate(sp["sentences"]) if "我跟小美一樣" in s["text"])
    old = sp["sentences"][k2]["text"]
    sp["sentences"][k2]["text"] = STUDENT_LINE
    (w / "說話者判斷.json").write_text(json.dumps(sp, ensure_ascii=False), encoding="utf-8")
    tp = turns_mod.turns_path(w)
    tp.write_text(tp.read_text(encoding="utf-8").replace(old, STUDENT_LINE), encoding="utf-8")
    m = json.loads((w / "transcript" / "merged.json").read_text(encoding="utf-8"))
    m["sentences"] = sp["sentences"]
    m["words"] = T.fake_workdir._words(sp["sentences"])
    (w / "transcript" / "merged.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    personnames.request_rescan(w)
    personnames.wait_rescan(w)
    turns_mod.set_name_code(w, "小美", "艾瑪", add_roster=False)
    personnames.decide(w, "小華", how)
    personnames.wait_rescan(w)
    return w


def test_2_roster_person_not_needed_removed_from_student_redo():
    try:
        for how in ("不用處理", "不是名字"):
            w = _privacy_workdir(how)
            items, _ = students.build_items(w)
            texts = [it.get("text") or it.get("文字") or "" for it in items]
            hit = [t for t in texts if "沒來" in t]
            assert hit, (how, texts)
            assert not any("華華" in t or "小華" in t for t in texts), (how, hit)
            assert any("艾瑪" in t for t in hit), hit   # 有代號的照舊換代號
            assert any("阿明也是" in t for t in hit), hit   # 沒決定的名冊學員不動（那個由開始前檢查擋）
            # 第 3 步建議稿也一樣（不再標含本名）
            page = review.page_data(w)
            flagged = [s["id"] for s in page["項目"] if s.get("含本名") and "沒來" in (s.get("建議稿") or "")]
            assert not any("華華" in (s.get("建議稿") or "") for s in page["項目"]), (how, flagged)
            tab = {r["寫法"]: r for r in epcodes.replace_table(w)}
            assert tab["華華"].get("拿掉") and not tab["華華"]["代號"], tab["華華"]
            assert not tab["阿明"].get("拿掉"), tab["阿明"]
    finally:
        (T._DATA / "名冊.csv").write_text(T._ROSTER, encoding="utf-8")


def test_2_replace_real_names_drop_rows():
    text, ch = review.replace_real_names("華華說小美也來", [{"寫法": "華華", "代號": "", "拿掉": True},
                                                          {"寫法": "小美", "代號": "艾瑪"},
                                                          {"寫法": "阿明", "代號": ""}])
    assert text == "說艾瑪也來", text
    assert ch[0] == {"原字": "華華", "換成": "", "位置": 0}, ch
    text, _ = review.replace_real_names("阿明來了", [{"寫法": "阿明", "代號": ""}])
    assert text == "阿明來了"


# ---------- 3. 第 4 步執行中不重試補找 ----------

def test_3_no_retry_while_executing():
    w = T._make()
    wd.write_json(personnames.rescan_path(w), {"待補找": True, "要求": 1, "未通知": 0, "錯誤": "X", "錯誤時間": 0,
                                                "錯誤已提醒": False})
    httpd, base = _serve(w)
    calls = []
    orig = personnames._scan_teacher
    personnames._scan_teacher = lambda workdir: calls.append(1) or orig(workdir)
    try:
        with httpd.run_lock:
            httpd.exec_state["running"] = True
        code, r = _call(base, "/api/review")
        assert code == 200, r
        personnames.wait_rescan(w, 30)
        assert not calls, "第 4 步執行中不該重試補找"
        with httpd.run_lock:
            httpd.exec_state["running"] = False
        _call(base, "/api/review")
        personnames.wait_rescan(w, 30)
        assert calls, "沒在執行時打開第 3 步照舊重試"
    finally:
        personnames._scan_teacher = orig
        httpd.shutdown()
        httpd.server_close()


# ---------- 4. 已結案的影片「開始分析」訊息 ----------

def test_4_closed_start_message_plain():
    from bookclub import server as srv

    root = Path(tempfile.mkdtemp())
    video = root / "第一堂.mp4"
    video.write_bytes(b"fake")
    d = srv.project_dir_for(video)
    d.mkdir()
    wd.write_json(closing.record_path(d), {"狀態": closing.CLOSED})
    httpd, base = _serve(T._make())
    orig_home = srv.home_path
    srv.home_path = lambda text: Path(text)   # 假影片在系統暫存資料夾（不在家目錄底下）
    try:
        code, r = _call(base, "/api/projects/start", {"影片": str(video)})
        assert code == 409 and r.get("已結案"), (code, r)
        assert r["error"] == "這支影片已結案，要重做請找宇軒", r
        assert "bookclub" not in r["error"]
    finally:
        srv.home_path = orig_home
        httpd.shutdown()
        httpd.server_close()


# ---------- 5. ③ 名冊上的人那一列的選單字 ----------

def test_5_mentioned_roster_flag():
    w = T._make()
    rows = {u["名字"]: u for u in personnames.mentioned(w)}
    assert rows["小美"]["名冊上的人"] is True, rows["小美"]
    assert rows["阿德"]["名冊上的人"] is False, rows["阿德"]


_JS5 = r"""
const { makeEnv } = require(process.argv[2]);
const ctx = makeEnv(process.argv[3].split(","), "");
const out = require("vm").runInContext(`(() => {
  rv.data = {"學員": {}, "這一集代號": {}};
  const a = rvNamePickOpts({"名字": "小美", "名冊上的人": true, "做法": "不用處理"});
  const b = rvNamePickOpts({"名字": "阿德", "名冊上的人": false, "做法": ""});
  return {a, b};
})()`, ctx);
process.stdout.write(JSON.stringify(out));
"""


def test_5_dropdown_labels_and_hint():
    review_js = (WEB / "review.js").read_text(encoding="utf-8")
    assert "名冊上的人會把名字拿掉" in review_js
    assert "名冊上的人選「不用處理」也會拿掉名字" not in review_js
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "名冊上的人會把名字拿掉" in readme and "名冊上的人選「不用處理」也會拿掉名字" not in readme
    if not NODE:
        print("（沒有 node，略過選單）")
        return
    d = Path(tempfile.mkdtemp())
    (d / "t.js").write_text(_JS5, encoding="utf-8")
    files = ",".join(str(WEB / f) for f in ("cards.js", "review.js") if (WEB / f).is_file())
    r = subprocess.run([NODE, str(d / "t.js"), str(REPO_ROOT / "tests" / "web_harness.js"), files],
                       capture_output=True, text=True, check=True)
    out = json.loads(r.stdout)
    assert 'value="how:不用處理" selected>不用處理（名字照樣拿掉）</option>' in out["a"], out["a"]
    assert '>不是名字（名字照樣拿掉）</option>' in out["a"], out["a"]
    assert '>不用處理</option>' in out["b"] and '>不是名字</option>' in out["b"] and "照樣拿掉" not in out["b"], out["b"]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    bad = 0
    for fn in tests:
        try:
            fn()
            print(f"✓ {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"✗ {fn.__name__}: {type(e).__name__}: {str(e)[:300]}")
    print(f"{len(tests) - bad}/{len(tests)} 通過")
    sys.exit(1 if bad else 0)
