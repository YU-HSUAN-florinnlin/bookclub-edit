"""1015-8 第 2 段（32 號設計 §3，以 §1a 為準）：結案打包——存、逐檔核對、失敗重存、停止與繼續、再存一份、位置檢查、
選資料夾視窗（全用假的子程式，不叫真的視窗）、API。

合成資料（假影片位元組、假逐字稿），全部寫在系統暫存資料夾；不碰 ~/讀書會剪輯資料。
獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_closing_pack.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from bookclub import closing, fileio  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

DAY = time.strftime("%Y%m%d")


def _mk(p: Path, data: bytes) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _workdir(root: Path, *, hardlink: bool = False) -> Path:
    """假工作區：輸出過一次（最終成品、成品、標字版）、逐字稿、修改需求、處理標記⋯。"""
    w = root / "第一堂_剪輯工作區"
    prod = _mk(w / "輸出" / "成品_v1_0-3_sw.mp4", os.urandom(300_000))
    final = w / "輸出" / "最終成品_v1_0-3_sw_20261016-101500.mp4"
    if hardlink:
        os.link(prod, final)       # #79 之後輸出成品可能是硬連結
    else:
        _mk(final, prod.read_bytes())
    _mk(w / "輸出" / "成品_v1_0-3_標字版.mp4", os.urandom(50_000))
    _mk(w / "輸出" / "成品_v0_0-3_sw.mp4", os.urandom(40_000))          # 別版的成品：不打包
    _mk(w / "輸出" / "原聲_0-3.wav", os.urandom(10_000))               # 比對用聲音：不打包
    _mk(w / "輸出" / "處理標記_0-3.md", "標記".encode())
    _mk(w / "輸出" / "剪輯決策_0-3.json", b"{}")
    _mk(w / "生成" / "處理紀錄.json", json.dumps({"產生時間": "2026-10-16T10:00:00", "片段": None, "紀錄": []}).encode())
    _mk(w / "生成" / "剪輯決策.json", b"{}")
    _mk(w / "生成" / "老師" / "S001.wav", os.urandom(5_000))           # 生成的聲音：不打包
    _mk(w / "修改需求" / "卡片.json", b"[]")
    _mk(w / "修改需求" / "版本" / "v1" / "處理紀錄.json", b"{}")
    _mk(w / "修改需求" / ".寫入鎖", b"")                                 # 鎖檔：不打包
    _mk(w / "報告.md", "報告".encode())
    wd.write_json(w / "分析結果.json", {"video": str(root / "第一堂.mp4")})
    wd.write_json(w / "transcript" / "merged.json", {"sentences": [{"id": "1", "start": 0.0, "end": 1.0, "text": "甲"}]})
    wd.write_json(w / "覆核" / "成品檢查.json", {"輸出成品": {"檔案": str(final.relative_to(w)), "來源": "輸出/成品_v1_0-3_sw.mp4",
                                                         "處理紀錄產生時間": "2026-10-16T10:00:00"}})
    return w


def _snap(w: Path) -> dict:
    return {str(p.relative_to(w)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(w.rglob("*"))
            if p.name != closing.RECORD_FILE}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _run(w: Path, loc: Path, **kw) -> closing.PackJob:
    job = closing.PackJob(w, loc, **kw)
    job.run()          # 同一條執行緒跑完（測試好看結果）
    return job


def test_pack_contents_verified_and_workdir_untouched():
    with tempfile.TemporaryDirectory() as t:
        w, loc = _workdir(Path(t)), Path(t) / "下載"
        loc.mkdir()
        before = _snap(w)
        job = _run(w, loc)
        assert job.state == "已存好", job.error
        out = loc / closing.PACK_ROOT / f"第一堂_{DAY}"
        assert out.is_dir() and not list((loc / closing.PACK_ROOT).glob(f"*{closing.PACKING_SUFFIX}"))
        names = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
        assert names == sorted(["最終成品_v1_0-3_sw_20261016-101500.mp4", "成品_v1_0-3_標字版.mp4", "逐字稿.srt", "逐字稿.txt",
                                "逐字稿_成品時間.srt", "打包清單.txt", "紀錄/修改需求/卡片.json",
                                "紀錄/修改需求/版本/v1/處理紀錄.json", "紀錄/處理標記_0-3.md", "紀錄/剪輯決策_0-3.json",
                                "紀錄/剪輯決策.json", "紀錄/處理紀錄.json", "紀錄/成品檢查.json", "紀錄/分析結果.json",
                                "紀錄/報告.md", "紀錄/transcript/merged.json"]), names   # 10-19 #253：其他小紀錄也帶
        assert (out / "最終成品_v1_0-3_sw_20261016-101500.mp4").read_bytes() == \
            (w / "輸出" / "最終成品_v1_0-3_sw_20261016-101500.mp4").read_bytes()
        lst = (out / "打包清單.txt").read_bytes().decode("utf-8")
        assert lst.startswith(closing.PRIVACY_LINES[0] + "\r\n" + closing.PRIVACY_LINES[1])
        rec = closing.read_record(w)
        assert rec["狀態"] == closing.PACKED and len(rec["打包"]) == 1
        e = rec["打包"][0]
        assert e["核對"] == "通過" and e["資料夾"] == str(out) and len(e["檔案"]) == len(names)
        for f in e["檔案"]:
            assert _sha(out / f["相對路徑"]) == f["sha256"] and (out / f["相對路徑"]).stat().st_size == f["大小"]
        assert _snap(w) == before          # 工作區一個檔都沒動（只多了 結案.json）
        assert not closing.closed(w)       # 已打包不是已結案


def test_hardlinked_source_is_really_copied():
    with tempfile.TemporaryDirectory() as t:
        w, loc = _workdir(Path(t), hardlink=True), Path(t) / "D"
        loc.mkdir()
        src = w / "輸出" / "最終成品_v1_0-3_sw_20261016-101500.mp4"
        assert src.stat().st_nlink == 2
        job = _run(w, loc)
        dst = loc / closing.PACK_ROOT / f"第一堂_{DAY}" / src.name
        assert job.state == "已存好" and dst.stat().st_nlink == 1 and dst.stat().st_ino != src.stat().st_ino
        assert dst.read_bytes() == src.read_bytes()


def test_corrupt_once_resaved_twice_stops():
    with tempfile.TemporaryDirectory() as t:
        w, loc = _workdir(Path(t)), Path(t) / "下載"
        loc.mkdir()
        tries: list = []
        job = _run(w, loc, fail_hook=lambda rel, n: (tries.append((rel, n)) or (rel.endswith("sw_20261016-101500.mp4") and n == 1)))
        assert job.state == "已存好" and ("最終成品_v1_0-3_sw_20261016-101500.mp4", 2) in tries
        loc2 = Path(t) / "壞的隨身碟"
        loc2.mkdir()
        job = _run(w, loc2, fail_hook=lambda rel, n: rel == "逐字稿.srt")
        assert job.state == "失敗" and "存過去不一樣（逐字稿.srt）" in job.error
        rec = closing.read_record(w)
        # 前一次已經核對通過：狀態留「已打包」（審查建議），這一筆沒核對完
        assert rec["狀態"] == closing.PACKED and rec["打包"][-1]["核對"] == "還沒"
        assert list((loc2 / closing.PACK_ROOT).glob(f"*{closing.PACKING_SUFFIX}"))   # 資料夾名稱還帶「_打包中」
        assert not list(loc2.rglob(f"*{closing.PACK_TMP_SUFFIX}"))                    # 暫存檔清掉了


class _StopAfter(closing.PackJob):
    """存到第 n 個檔就按停止；記下真的複製了哪些檔。"""

    def __init__(self, *a, stop_after: int | None = None, log: list | None = None, **kw):
        super().__init__(*a, **kw)
        self.stop_after, self.log = stop_after, log if log is not None else []

    def _copy(self, src, data, tmp):
        self.log.append(tmp.name)
        got = super()._copy(src, data, tmp)
        if self.stop_after is not None and len(self.log) >= self.stop_after:
            self.cancel()
        return got


def test_stop_then_resume_only_copies_missing():
    """中途停止（或伺服器被關掉，工作只在記憶體裡）→ 新的工作「繼續」：同一個 `_打包中` 資料夾，已經存好的重算對得上就跳過。"""
    with tempfile.TemporaryDirectory() as t:
        w, loc = _workdir(Path(t)), Path(t) / "下載"
        loc.mkdir()
        job = _StopAfter(w, loc, stop_after=3)
        job.run()
        assert job.state == "停止" and "繼續" in job.error
        st = closing.pack_state(w)
        assert st["可以繼續"] and st["狀態"] == closing.PACKING
        total = len(closing.pack_items(w)) + 1
        again = _StopAfter(w, loc, resume=True)
        again.run()
        assert again.state == "已存好", again.error
        assert len(again.log) == total - 3, (again.log, total)
        out = loc / closing.PACK_ROOT / f"第一堂_{DAY}"
        assert out.is_dir() and len(closing.read_record(w)["打包"]) == 1
        # 沒有存到一半的可以繼續
        try:
            closing.PackJob(w, loc, resume=True)._run()
            raise AssertionError("應該擋下")
        except closing.PackError as e:
            assert "重新按" in str(e)


def test_pack_again_same_day_numbered_and_records_only_after_closed():
    with tempfile.TemporaryDirectory() as t:
        w, loc = _workdir(Path(t)), Path(t) / "下載"
        loc.mkdir()
        assert _run(w, loc).state == "已存好"
        assert _run(w, loc).state == "已存好"   # §3.7 再存一份
        assert (loc / closing.PACK_ROOT / f"第一堂_{DAY} (2)").is_dir()
        rec = closing.read_record(w)
        assert len(rec["打包"]) == 2 and all(e["核對"] == "通過" for e in rec["打包"])
        rec["狀態"] = closing.CLOSED   # 已結案（第 3 段清完）→ 只存逐字稿、清單、紀錄
        wd.write_json(closing.record_path(w), rec)
        job = _run(w, loc, records_only=True)
        out = Path(job.folder)
        assert job.state == "已存好" and not list(out.glob("*.mp4")) and (out / "逐字稿.srt").is_file()
        assert closing.read_record(w)["狀態"] == closing.CLOSED and closing.read_record(w)["打包"][-1]["只有紀錄"]


def test_location_checks():
    with tempfile.TemporaryDirectory() as t:
        w = _workdir(Path(t))
        for bad, why in ((Path(t) / "沒有這個", "找不到"), (w / "輸出", "工作資料夾裡"), (w, "工作資料夾裡")):
            try:
                closing.check_location(w, bad, 1000)
                raise AssertionError(bad)
            except closing.PackError as e:
                assert why in str(e), (bad, e)
        ok = Path(t) / "下載"
        ok.mkdir()

        class U:
            free = 250_000_000
        try:
            closing.check_location(w, ok, 100_000_000, usage=lambda p: U)
            raise AssertionError("空間不夠應該擋下")
        except closing.PackError as e:
            assert "空間不夠" in str(e) and "0.1 GB" in str(e)
        assert closing.check_location(w, ok, 10_000_000, usage=lambda p: U) == ok.resolve()


def test_locations_d_drive_only_when_mounted():
    with tempfile.TemporaryDirectory() as t:
        dl = Path(t)

        class U:
            free = 9_000_000_000
        got = closing.pack_locations("wsl", ismount=lambda p: False, downloads=lambda: dl, usage=lambda p: U)
        assert [x["代號"] for x in got] == ["下載", "自己選"]
        got = closing.pack_locations("wsl", ismount=lambda p: p == "/mnt/d", downloads=lambda: dl, usage=lambda p: U)
        assert [x["代號"] for x in got] == ["下載", "D", "自己選"] and got[1]["位置顯示"] == "D:\\"
        got = closing.pack_locations("mac", downloads=lambda: dl, usage=lambda p: U)
        assert [x["代號"] for x in got] == ["下載", "自己選"] and got[0]["剩餘位元組"] == 9_000_000_000
        assert closing.windows_display("/mnt/d/讀書會剪輯結案/第一堂") == "D:\\讀書會剪輯結案\\第一堂"
        assert closing.windows_display("/Users/老師/Downloads") == "/Users/老師/Downloads"


def _fake_run(out: str, code: int = 0, err: str = "", wsl_to: str | None = None, calls: list | None = None):
    def run(cmd, **kw):
        if calls is not None:
            calls.append(cmd)
        if cmd[0] == "wslpath":
            return subprocess.CompletedProcess(cmd, 0, (wsl_to or "").encode(), b"")
        return subprocess.CompletedProcess(cmd, code, out.encode(), err.encode())
    return run


def test_pick_folder_fake_dialogs_and_master_switch():
    which = lambda name: f"/假的/{name}"  # noqa: E731
    calls: list = []
    assert fileio.pick_folder("選", method="mac", run=_fake_run("/Users/老師/Downloads\n", calls=calls), env={}) == \
        Path("/Users/老師/Downloads")
    assert "chooseFolder" in calls[-1][-1]
    assert fileio.pick_folder("選", method="mac", run=_fake_run("", 1, "User canceled. (-128)"), env={}) is None
    assert fileio.pick_folder("選", method="wsl", which=which, run=_fake_run(fileio.CANCEL_MARK), env={}) is None
    got = fileio.pick_folder("選", method="wsl", which=which, run=_fake_run("D:\\備份\n", wsl_to="/mnt/d/備份"), env={})
    assert got == Path("/mnt/d/備份")
    script = fileio.powershell_folder_script("選")
    assert "FolderBrowserDialog" in script and "shell:Downloads" in script
    assert fileio.folder_command("zenity", "選")[:3] == ["zenity", "--file-selection", "--directory"]
    try:
        fileio.pick_folder("選", method="mac", run=_fake_run("x"), env={fileio.FOLDER_PICKER_ENV: "0"})
        raise AssertionError("總開關關掉應該不叫視窗")
    except fileio.PickUnavailable as e:
        assert "BOOKCLUB_FOLDER_PICKER=0" in str(e)
    try:
        fileio.pick_folder("選", method="wsl", which=which, run=_fake_run("", 1, "Exec format error"), env={})
        raise AssertionError("叫不起來")
    except fileio.PickUnavailable:
        pass


def test_state_flags_not_latest_version():
    """§1a：最後輸出的不是最新版 → `不是最新版`（框裡多一行紅字，不擋）。"""
    import test_needs as tn
    import test_redo_list as trl
    import test_versions as tv
    from bookclub import finalcheck as fc

    w = trl._upgraded()
    tv._assemble(w, tv._t(1), trl._records())
    chk = wd.read_json(fc.check_path(w))
    final = tn._prod(w, "最終成品_v1_0-3_sw_20261016-101500.mp4", tn.T1)
    chk["輸出成品"] = {"檔案": final, "來源": "輸出/成品_0-3_sw.mp4", "版本號": 1, "處理紀錄產生時間": tn.T1}
    wd.write_json(fc.check_path(w), chk)
    st = closing.pack_state(w)
    assert st["輸出過"] and st["最後輸出版本"] == 1 and st["最新版本"] == 2 and st["不是最新版"] is True
    assert st["打包大小"] > 0 and st["檔數"] > 3


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


def test_api_cancel_changes_nothing_then_pack():
    with tempfile.TemporaryDirectory() as t:
        w, loc = _workdir(Path(t)), Path(t) / "自己選的"
        loc.mkdir()
        httpd, base = _serve(w)
        orig = fileio.pick_folder
        try:
            code, st = _call(base, "/api/close/state")
            assert code == 200 and st["輸出過"] and st["可以打包"] and st["狀態"] is None and st["工作"] is None
            before, before_loc = _snap(w), sorted(loc.rglob("*"))
            fileio.pick_folder = lambda title: None              # 假的視窗：按了取消
            code, r = _call(base, "/api/close/pick-folder", {})
            assert code == 200 and r == {"取消": True}
            assert _snap(w) == before and not closing.record_path(w).exists() and sorted(loc.rglob("*")) == before_loc
            fileio.pick_folder = orig
            os.environ[fileio.FOLDER_PICKER_ENV] = "0"              # 總開關：退回網頁的路徑框
            code, r = _call(base, "/api/close/pick-folder", {})
            assert code == 200 and r["退回網頁"] and "BOOKCLUB_FOLDER_PICKER" in r["說明"]
            assert _call(base, "/api/close/state")[1]["系統視窗"] is False
            code, r = _call(base, "/api/close/pack", {"位置": "自己選", "路徑": str(w / "輸出")})
            assert code == 400 and "工作資料夾裡" in r["error"]
            code, r = _call(base, "/api/close/pack", {"位置": "自己選", "路徑": "相對路徑"})
            assert code == 400
            httpd.exec_state["running"] = True
            code, r = _call(base, "/api/close/pack", {"位置": "自己選", "路徑": str(loc)})
            assert code == 400 and "第 4 步正在跑" in r["error"]
            assert "第 4 步正在跑" in _call(base, "/api/close/state")[1]["不行的原因"]
            httpd.exec_state["running"] = False
            code, r = _call(base, "/api/close/pack", {"位置": "自己選", "路徑": str(loc)})
            assert code == 200 and r["ok"]
            for _ in range(200):
                st = _call(base, "/api/close/state")[1]
                if st["工作"] and st["工作"]["狀態"] not in ("等待", "存檔中", "核對中"):
                    break
                time.sleep(0.05)
            assert st["工作"]["狀態"] == "已存好" and st["狀態"] == closing.PACKED and st["核對通過的"]["檔案"] >= 10
            assert _call(base, "/api/close/cancel", {})[0] == 400   # 沒在存
            assert _snap(w) == before
        finally:
            fileio.pick_folder = orig
            os.environ.pop(fileio.FOLDER_PICKER_ENV, None)
            httpd.shutdown()
            httpd.server_close()


def test_api_not_exported_cannot_pack():
    with tempfile.TemporaryDirectory() as t:
        w = _workdir(Path(t))
        wd.write_json(w / "覆核" / "成品檢查.json", {})
        httpd, base = _serve(w)
        try:
            st = _call(base, "/api/close/state")[1]
            assert not st["輸出過"] and not st["可以打包"] and "輸出成品" in st["不行的原因"]
            assert _call(base, "/api/close/pack", {"位置": "自己選", "路徑": t})[0] == 400
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_js_close_box_view():
    import shutil as _sh

    node = _sh.which("node")
    if not node:
        return
    src = (REPO_ROOT / "bookclub" / "web" / "fileio.js").read_text(encoding="utf-8")
    import re

    fns = "".join(re.search(rf"^function {n}\(.*?^\}}\n", src, re.S | re.M).group(0) for n in ("clGB", "clView"))
    code = fns + """
      const base = {"暫存大小": 2.6e9, "系統": "mac", "狀態": null, "工作": null, "可以繼續": false};
      console.log(JSON.stringify([
        clView(base),
        clView({...base, "系統": "wsl", "不是最新版": true, "最後輸出版本": 2, "最新版本": 3}),
        clView({...base, "狀態": "已打包", "核對通過的": {"資料夾": "D:\\\\讀書會剪輯結案\\\\第一堂_20261016"}}),
        clView({...base, "狀態": "打包中", "可以繼續": true}),
        clView({...base, "工作": {"狀態": "存檔中", "第幾個": 2, "共幾個": 9}}),
        clView({...base, "狀態": "已結案", "只有紀錄": true}),
      ]));"""
    v = json.loads(subprocess.run([node, "-e", code], capture_output=True, text=True, check=True).stdout)
    # 第 3 段：接上 clean_plan 之後那一句補回「然後刪掉這裡的暫存檔（共 X GB）」（32 號設計 §1a 第 1 點）
    assert v[0]["btn"] == "結案打包…" and v[0]["line"] == "會把成品影片、逐字稿、修改紀錄存到你選的地方，然後刪掉這裡的暫存檔（共 2.6 GB）。"
    assert not v[0]["warn"] and not v[0]["status"]
    assert v[1]["warn"] == "最後輸出的是第 2 版，最新是第 3 版。"
    assert v[2]["btn"] == "再存一份…" and "已存好" in v[2]["status"] and "第一堂_20261016" in v[2]["status"]
    assert v[3]["resume"] and v[3]["status"] == "上次存到一半。"
    assert v[4]["running"] and v[4]["status"] == "正在存…（第 2／9 個檔）"
    assert v[5]["btn"] == "再存一份紀錄…" and "只存逐字稿和紀錄" in v[5]["line"]


# ---------- 第 2 段審查修正 ----------

def test_fix1_missing_final_product_blocks_and_marks_has_product():
    """必修 1：輸出紀錄記的最終成品不在 → 不能打包（不會「核對通過」卻沒有成品，第 3 段就會把成品全清掉）；
    有成品的打包記 `含成品: true`。"""
    with tempfile.TemporaryDirectory() as t:
        w, loc = _workdir(Path(t)), Path(t) / "下載"
        loc.mkdir()
        job = _run(w, loc)
        assert job.state == "已存好" and closing.read_record(w)["打包"][-1]["含成品"] is True
        (w / "輸出" / "最終成品_v1_0-3_sw_20261016-101500.mp4").rename(w / "輸出" / "移走了.mp4")
        job = _run(w, loc)
        assert job.state == "失敗" and "找不到最後輸出的成品影片" in job.error, job.error
        assert len(closing.read_record(w)["打包"]) == 1   # 沒有新增一筆
        try:
            closing.pack_items(w)
            raise AssertionError("應該擋下")
        except closing.PackError:
            pass
        st = closing.pack_state(w)
        assert st["成品不在"] is True
        httpd, base = _serve(w)
        try:
            code, st = _call(base, "/api/close/state")
            assert not st["可以打包"] and "找不到最後輸出的成品影片" in st["不行的原因"]
            code, r = _call(base, "/api/close/pack", {"位置": "自己選", "路徑": str(loc)})
            assert code == 400 and "找不到最後輸出的成品影片" in r["error"]
        finally:
            httpd.shutdown()
            httpd.server_close()


class _Running:
    running = True
    purpose = "結案打包"
    workdir = None

    def to_dict(self):
        return {"狀態": "存檔中"}


def test_fix2_packing_blocks_execute_export_analyze():
    """必修 2：結案打包在存時，第 4 步開始、第 5 步輸出成品（含輸出舊版）、影片分析都擋下。"""
    with tempfile.TemporaryDirectory() as t:
        w = _workdir(Path(t))
        httpd, base = _serve(w)
        try:
            httpd.close_job = _Running()
            code, r = _call(base, "/api/execute/start", {})
            assert code == 409 and "結案打包正在存" in r["error"], r
            code, r = _call(base, "/api/run/analyze", {})
            assert code == 409 and "結案打包正在存" in r["error"], r
            code, r = _call(base, "/api/final/export", {"確定": True})
            assert code in (400, 409) and "結案打包正在存" in r["error"], r
            code, r = _call(base, "/api/final/export", {"確定": True, "版本": 99})
            assert code in (400, 409) and "結案打包正在存" in r["error"], r
        finally:
            httpd.shutdown()
            httpd.server_close()


class _TouchSource(closing.PackJob):
    """存最終成品的時候，來源檔被改了（例如有人又輸出了一次）。"""

    def _copy(self, src, data, tmp):
        got = super()._copy(src, data, tmp)
        if src is not None and src.suffix == ".mp4" and "最終成品" in src.name:
            with open(src, "ab") as f:
                f.write(b"x")
        return got


def test_fix2_source_changed_while_copying_fails():
    with tempfile.TemporaryDirectory() as t:
        w, loc = _workdir(Path(t)), Path(t) / "下載"
        loc.mkdir()
        job = _TouchSource(w, loc)
        job.run()
        assert job.state == "失敗" and "存的時候工作區裡的檔案變了" in job.error, job.error
        assert "Error" not in job.error
        assert closing.read_record(w)["打包"][-1]["核對"] != "通過"


def test_fix3_state_does_not_probe_locations():
    """必修 3：`/api/close/state` 不叫 `pack_locations`（WSL 上會啟動 PowerShell）；要位置選項另外問 `/api/close/locations`。"""
    with tempfile.TemporaryDirectory() as t:
        w = _workdir(Path(t))
        calls = []
        orig = closing.pack_locations
        closing.pack_locations = lambda *a, **k: calls.append(1) or [{"代號": "自己選"}]
        httpd, base = _serve(w)
        try:
            st = _call(base, "/api/close/state")[1]
            assert "位置選項" not in st and calls == []
            code, r = _call(base, "/api/close/locations")
            assert code == 200 and r["位置選項"] == [{"代號": "自己選"}] and calls == [1]
        finally:
            closing.pack_locations = orig
            httpd.shutdown()
            httpd.server_close()


def test_fix_os_error_messages_plain():
    import errno

    m = closing.os_message
    assert "4 GB" in m(OSError(errno.EFBIG, "File too large")) and "OSError" not in m(OSError(errno.EFBIG, "x"))
    assert "路徑太長" in m(OSError(errno.ENAMETOOLONG, "x"))
    assert "滿了" in m(OSError(errno.ENOSPC, "x"))
    assert "拔掉" in m(OSError(errno.EIO, "x")) and "拔掉" in m(OSError(errno.ENOENT, "x"))
    assert "Error" not in m(OSError(errno.EIO, "Input/output error"))


def test_fix_repack_stopped_keeps_packed_state():
    """建議：已經有核對通過的打包，再存一份中途停掉 → 狀態留「已打包」（不倒退成打包中）。"""
    with tempfile.TemporaryDirectory() as t:
        w, loc = _workdir(Path(t)), Path(t) / "下載"
        loc.mkdir()
        assert _run(w, loc).state == "已存好"
        job = _StopAfter(w, loc, stop_after=2)
        job.run()
        assert job.state == "停止" and closing.read_record(w)["狀態"] == closing.PACKED
        assert closing.pack_state(w)["可以繼續"]


def test_fix_resume_after_rename_before_record():
    """建議：改名成正式名稱之後、還沒寫 結案.json 就當掉 →「繼續」找得到去掉 `_打包中` 的同名資料夾，重新核對。"""
    with tempfile.TemporaryDirectory() as t:
        w, loc = _workdir(Path(t)), Path(t) / "下載"
        loc.mkdir()
        assert _run(w, loc).state == "已存好"
        rec = closing.read_record(w)
        e = rec["打包"][-1]
        e.update({"核對": "還沒", "資料夾": e["資料夾"] + closing.PACKING_SUFFIX})
        rec["狀態"] = closing.PACKING
        wd.write_json(closing.record_path(w), rec)
        assert closing.pack_state(w)["可以繼續"]
        again = _StopAfter(w, loc, resume=True)
        again.run()
        assert again.state == "已存好" and again.log == [], (again.error, again.log)   # 都對得上：一個都不用重存
        assert closing.read_record(w)["打包"][-1]["核對"] == "通過"
        assert not (loc / closing.PACK_ROOT / f"第一堂_{DAY} (2)").exists()


def test_fix_resume_space_counts_saved_part():
    with tempfile.TemporaryDirectory() as t:
        w, loc = _workdir(Path(t)), Path(t) / "下載"
        loc.mkdir()
        job = _StopAfter(w, loc, stop_after=1)   # 第一個檔（最終成品 300 KB）存好了
        job.run()
        folder = Path(closing.read_record(w)["打包"][-1]["資料夾"])
        need = closing.pack_size(closing.pack_items(w))
        assert closing.remaining_bytes(closing.pack_items(w), folder) <= need - 300_000


def test_fix_reveal_and_ubuntu_warning():
    assert closing.ubuntu_inside_warning("wsl", "/home/老師/結案") and "Windows 看不到" in \
        closing.ubuntu_inside_warning("wsl", "/home/老師/結案")
    assert closing.ubuntu_inside_warning("wsl", "/mnt/d/結案") is None
    assert closing.ubuntu_inside_warning("mac", "/Users/老師/Downloads") is None
    with tempfile.TemporaryDirectory() as t:
        w, loc = _workdir(Path(t)), Path(t) / "下載"
        loc.mkdir()
        httpd, base = _serve(w)
        orig = fileio.reveal
        seen = []
        fileio.reveal = lambda p, **k: seen.append(Path(p)) or "打開了"
        try:
            assert _call(base, "/api/close/reveal", {})[0] == 400   # 還沒存好
            _run(w, loc)
            code, r = _call(base, "/api/close/reveal", {})
            assert code == 200 and seen == [loc / closing.PACK_ROOT / f"第一堂_{DAY}"], (r, seen)
        finally:
            fileio.reveal = orig
            httpd.shutdown()
            httpd.server_close()


# ---------- 第 2 段第二輪審查修正 ----------

def test_fix_r2_export_running_blocks_pack():
    """必修：第 5 步「輸出成品」正在複製最終成品（卡住）時，結案打包不能開始（以前只看「複製到 Windows」）。"""
    from bookclub import finalcheck as fc

    with tempfile.TemporaryDirectory() as t:
        w, loc = _workdir(Path(t)), Path(t) / "下載"
        loc.mkdir()
        httpd, base = _serve(w)
        gate, entered = threading.Event(), threading.Event()
        orig = fc.export_final

        def stuck(workdir, confirm=False):
            entered.set()
            gate.wait(10)
            return {"ok": True}
        fc.export_final = stuck
        th = threading.Thread(target=lambda: _call(base, "/api/final/export", {"確定": True}), daemon=True)
        try:
            th.start()
            assert entered.wait(5)
            code, r = _call(base, "/api/close/pack", {"位置": "自己選", "路徑": str(loc)})
            assert code == 400 and "成品正在輸出" in r["error"], r
            assert "成品正在輸出" in (_call(base, "/api/close/state")[1]["不行的原因"] or "")
            gate.set()
            th.join(5)
            assert _call(base, "/api/close/state")[1]["可以打包"]
        finally:
            gate.set()
            fc.export_final = orig
            httpd.shutdown()
            httpd.server_close()


def test_fix_r2_resume_uses_recorded_rename_target():
    """建議 1：同一天第一份已存好；第二份改名成 ` (2)` 後、還沒寫 結案.json 就當掉 → 繼續找的是 ` (2)`，第一份完全沒被動。"""
    with tempfile.TemporaryDirectory() as t:
        w, loc = _workdir(Path(t)), Path(t) / "下載"
        loc.mkdir()
        first = _run(w, loc)
        second = _run(w, loc)
        one, two = Path(first.folder), Path(second.folder)
        assert two.name.endswith(" (2)")
        rec = closing.read_record(w)
        e = rec["打包"][-1]
        assert e["改名成"] == str(two)
        e.update({"核對": "還沒", "資料夾": str(two.with_name(f"第一堂_{DAY}{closing.PACKING_SUFFIX}"))})
        wd.write_json(closing.record_path(w), rec)
        before = {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in one.rglob("*")}
        again = _StopAfter(w, loc, resume=True)
        again.run()
        assert again.state == "已存好" and Path(again.folder) == two, (again.error, again.folder)
        assert {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in one.rglob("*")} == before
        assert closing.read_record(w)["打包"][-1]["資料夾"] == str(two)


class _BusyRecord(closing.PackJob):
    """第 5 步邊看邊存：成品檢查.json 在存的時候被改了 times 次。"""

    def __init__(self, *a, times: int = 2, **kw):
        super().__init__(*a, **kw)
        self.times = times

    def _copy(self, src, data, tmp):
        got = super()._copy(src, data, tmp)
        if src is not None and src.name == "成品檢查.json" and self.times > 0:
            self.times -= 1
            time.sleep(0.01)
            src.write_bytes(src.read_bytes())   # 內容一樣、修改時間變了
            os.utime(src, ns=(time.time_ns(), time.time_ns() + self.times + 1))
        return got


def test_fix_r2_small_record_changed_retried():
    """建議 2：小的紀錄檔（成品檢查.json）存的時候被改了 → 重讀重存，最多 3 次；一直變才失敗、訊息照實際原因寫。影片一變就失敗（上面另一條）。"""
    with tempfile.TemporaryDirectory() as t:
        w, loc = _workdir(Path(t)), Path(t) / "下載"
        loc.mkdir()
        job = _BusyRecord(w, loc, times=2)
        job.run()
        assert job.state == "已存好", job.error
        job = _BusyRecord(w, loc, times=99)
        job.run()
        assert job.state == "失敗" and "成品檢查.json" in job.error and "一直在變" in job.error, job.error


def test_fix_r2_dot_files_in_needs_skipped():
    """建議 3：`修改需求/` 裡用點開頭的檔（`.寫入中`、`.寫入鎖`）不打包。"""
    with tempfile.TemporaryDirectory() as t:
        w = _workdir(Path(t))
        _mk(w / "修改需求" / ".卡片.json.寫入中", b"{}")
        _mk(w / "修改需求" / "版本" / ".暫存", b"x")
        names = [it["相對路徑"] for it in closing.pack_items(w)]
        assert not any(Path(n).name.startswith(".") for n in names), names
        assert "紀錄/修改需求/卡片.json" in names


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
