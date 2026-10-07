"""網頁伺服器的檔案進出 API（10-07）：系統選檔視窗選影片／片頭／片尾、WSL2 的 Windows 檔複製進 Ubuntu、
第 5 步打開成品資料夾與複製到 Windows 的下載資料夾。

真的開一個伺服器（埠 0，系統隨便給一個）打 API；系統選檔視窗、WSL2、Windows 的下載資料夾都換成假的
（`fileio.pick_file`、`fileio.needs_import`、`fileio.platform_kind`、`fileio.windows_downloads`），不會跳出視窗。
不開始分析（`start_analyze` 換成假的）、不碰真的工作區（`BOOKCLUB_DATA_DIR` 指到暫存資料夾）。

獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_fileio_server.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import fileio  # noqa: E402
from bookclub import server as srv  # noqa: E402


@contextmanager
def _server(workdir=None):
    data = Path(tempfile.mkdtemp())
    old = os.environ.get("BOOKCLUB_DATA_DIR")
    os.environ["BOOKCLUB_DATA_DIR"] = str(data)
    saved = {k: getattr(fileio, k) for k in ("pick_file", "needs_import", "platform_kind", "windows_downloads", "reveal")}
    httpd = srv.BookclubServer(("127.0.0.1", 0), srv.Handler, workdir=workdir, video=None)
    httpd.start_analyze = lambda opts: {"started": True}   # 不真的分析
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    port = httpd.server_address[1]

    def call(method, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                     data=json.dumps(body or {}).encode() if method == "POST" else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        yield httpd, call, data
    finally:
        for k, v in saved.items():
            setattr(fileio, k, v)
        httpd.shutdown()
        httpd.server_close()
        if old is None:
            os.environ.pop("BOOKCLUB_DATA_DIR", None)
        else:
            os.environ["BOOKCLUB_DATA_DIR"] = old


def _wait_until(fn, t=5):
    end = time.time() + t
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("等太久")


def test_pick_fallback_and_cancel():
    with _server() as (httpd, call, _data):
        def unavailable(title):
            raise fileio.PickUnavailable("Windows 的選檔視窗叫不起來")
        fileio.pick_file = unavailable
        code, r = call("POST", "/api/pick", {"用途": "影片"})
        assert code == 200 and r["退回網頁"] and "資料夾瀏覽" in r["說明"]
        fileio.pick_file = lambda title: None
        code, r = call("POST", "/api/pick", {"用途": "影片"})
        assert code == 200 and r["取消"] and r["退回網頁"] and "取消" in r["說明"]
        assert call("POST", "/api/pick", {"用途": "別的"})[0] == 400
        assert call("POST", "/api/pick", {"用途": "片頭", "給": "目前"})[0] == 409    # 沒有目前的專案：不開視窗
        # 網頁資料夾瀏覽只能選家目錄底下
        outside = Path(tempfile.mkdtemp()) / "a.mp4"
        outside.write_bytes(b"1")
        if not str(outside.resolve()).startswith(str(Path.home().resolve())):
            assert call("POST", "/api/pick/browsed", {"用途": "影片", "路徑": str(outside)})[0] == 403
        # 選到不是影片的檔
        txt = Path(tempfile.mkdtemp()) / "a.txt"
        txt.write_text("x")
        fileio.pick_file = lambda title: txt
        code, r = call("POST", "/api/pick", {"用途": "影片"})
        assert code == 400 and "影片檔" in r["error"]
        # 沒選就開始分析
        code, r = call("POST", "/api/projects/start", {"用選的": True})
        assert code == 400 and "還沒選影片" in r["error"]


def test_wsl_pick_copies_into_ubuntu_then_start():
    with _server() as (httpd, call, data):
        win = Path(tempfile.mkdtemp()) / "C槽" / "我的 影片"
        win.mkdir(parents=True)
        video = win / "第一堂 讀書會.mp4"
        video.write_bytes(b"v" * 300_000)
        intro = win / "片頭.mov"
        intro.write_bytes(b"i" * 1000)
        fileio.needs_import = lambda p, kind=None: str(p).startswith(str(win))   # 假裝這個資料夾是 /mnt/c
        fileio.platform_kind = lambda *a, **k: "wsl"
        fileio.pick_file = lambda title: video if "讀書會" in title else intro

        code, r = call("POST", "/api/pick", {"用途": "影片"})
        assert code == 200 and r["要複製"] and r["複製"]["用途"] == "影片"
        pk = _wait_until(lambda: (call("GET", "/api/picks")[1]["選了"].get("影片") or {}).get("路徑") and call("GET", "/api/picks")[1])
        copied = Path(pk["選了"]["影片"]["路徑"])
        assert copied == data / "影片" / video.name and copied.read_bytes() == video.read_bytes()
        assert pk["選了"]["影片"]["原本的位置"] == str(video) and pk["系統"] == "wsl"
        assert pk["複製"][0]["百分比"] == 100.0

        # 給新影片的片頭（也複製）
        code, r = call("POST", "/api/pick", {"用途": "片頭", "給": "新影片"})
        assert code == 200 and r["要複製"]
        _wait_until(lambda: (call("GET", "/api/picks")[1]["選了"].get("片頭") or {}).get("路徑"))

        # 再選同一支：同名同大小沿用、不再複製
        code, r = call("POST", "/api/pick", {"用途": "影片"})
        assert r["複製"]["狀態"] == "沿用"

        code, r = call("POST", "/api/projects/start", {"用選的": True})
        assert code == 202, r
        proj = Path(r["路徑"])
        assert proj == data / "影片" / "第一堂 讀書會_剪輯工作區"                # 工作區建在複製過來的影片旁邊
        extras = srv.load_extras(proj)
        assert extras["片頭"]["路徑"] == str(data / "影片" / "片頭.mov") and extras["片頭"]["原本的位置"] == str(intro)
        assert extras["片頭"]["檔案還在"] and extras["片尾"] is None
        st = call("GET", "/api/picks")[1]
        assert st["選了"] == {} and st["目前的專案"]["片頭"]["檔名"] == "片頭.mov"

        # 同名不同大小：改名「(2)」
        video.write_bytes(b"w" * 1000)
        code, r = call("POST", "/api/pick", {"用途": "影片"})
        _wait_until(lambda: (call("GET", "/api/picks")[1]["選了"].get("影片") or {}).get("路徑"))
        assert call("GET", "/api/picks")[1]["選了"]["影片"]["檔名"] == "第一堂 讀書會 (2).mp4"


def test_space_shortage_and_cancel():
    with _server() as (httpd, call, data):
        win = Path(tempfile.mkdtemp())
        video = win / "大.mp4"
        video.write_bytes(b"x" * 1000)
        fileio.needs_import = lambda p, kind=None: True
        fileio.pick_file = lambda title: video
        real_usage = fileio.shutil.disk_usage
        fileio.shutil.disk_usage = lambda p: real_usage(p)._replace(free=500)
        try:
            code, r = call("POST", "/api/pick", {"用途": "影片"})
        finally:
            fileio.shutil.disk_usage = real_usage
        assert code == 400 and "空間不夠" in r["error"] and "一共要" in r["error"]
        assert not (data / "影片" / "大.mp4").exists()

        # 複製中取消（不讓它真的開始跑，固定在「複製中」）
        orig_start = fileio.CopyJob.start

        def hold(self):
            self.state = "複製中"
            return self
        fileio.CopyJob.start = hold
        try:
            code, r = call("POST", "/api/pick", {"用途": "影片"})
            assert code == 200 and r["複製"]["狀態"] == "複製中"
            code, r2 = call("POST", "/api/projects/start", {"用選的": True})
            assert code == 400 and "還在複製" in r2["error"]
            assert call("POST", "/api/copy/cancel", {"id": r["複製"]["id"]})[0] == 200
            assert "影片" not in call("GET", "/api/picks")[1]["選了"]
            assert call("POST", "/api/copy/cancel", {"id": "沒有這個"})[0] == 404
        finally:
            fileio.CopyJob.start = orig_start


def test_mac_pick_uses_file_in_place_and_extras_for_current():
    w = Path(tempfile.mkdtemp()) / "第二堂_剪輯工作區"
    w.mkdir()
    with _server(workdir=w) as (httpd, call, data):
        vids = Path(tempfile.mkdtemp())
        outro = vids / "片尾 2026.mp4"
        outro.write_bytes(b"o" * 10)
        fileio.platform_kind = lambda *a, **k: "mac"
        fileio.pick_file = lambda title: outro
        code, r = call("POST", "/api/pick", {"用途": "片尾", "給": "目前"})
        assert code == 200 and not r["要複製"] and r["路徑"] == str(outro)       # Mac：照用、不複製
        saved = json.loads((w / "工作區設定.json").read_text(encoding="utf-8"))
        assert saved["片尾"]["路徑"] == str(outro) and saved["片尾"]["原本的位置"] is None
        assert not list(w.glob(".*寫入中"))                                       # 原子寫入沒留下暫存檔
        assert call("GET", "/api/picks")[1]["目前的專案"]["片尾"]["檔名"] == "片尾 2026.mp4"
        assert call("POST", "/api/extras/clear", {"用途": "片尾"})[0] == 200
        assert "片尾" not in json.loads((w / "工作區設定.json").read_text(encoding="utf-8"))
        assert call("POST", "/api/extras/clear", {"用途": "片中"})[0] == 400
        assert not (data / "影片").exists()


def test_final_outputs_reveal_and_copy_to_windows():
    w = Path(tempfile.mkdtemp()) / "第三堂_剪輯工作區"
    (w / "輸出").mkdir(parents=True)
    prod = w / "輸出" / "成品_0-98_sw.mp4"
    prod.write_bytes(b"p" * 2000)
    with _server(workdir=w) as (httpd, call, data):
        fileio.platform_kind = lambda *a, **k: "mac"
        code, r = call("GET", "/api/final/outputs")
        assert code == 200 and r["成品"] == prod.name and r["成品是"] == "還在檢查的成品" and not r["可以複製到Windows"]
        seen = []
        fileio.reveal = lambda p: seen.append(Path(p)) or "已經在 Finder 打開"
        code, r = call("POST", "/api/final/reveal", {"路徑": "/etc"})       # 網頁傳來的路徑不理
        assert code == 200 and [p.resolve() for p in seen] == [prod.resolve()], (code, seen)
        assert call("POST", "/api/final/to_windows", {})[0] == 400              # Mac 沒有這個按鈕

        # 按過「輸出成品」：最終成品優先
        final = w / "輸出" / "最終成品_0-98_sw.mp4"
        final.write_bytes(b"f" * 3000)
        (w / "覆核").mkdir()
        (w / "覆核" / "成品檢查.json").write_text(json.dumps({"輸出成品": {"檔案": "輸出/最終成品_0-98_sw.mp4"}}),
                                                encoding="utf-8")
        fileio.platform_kind = lambda *a, **k: "wsl"
        dl = Path(tempfile.mkdtemp()) / "Downloads"
        dl.mkdir()
        fileio.windows_downloads = lambda: dl
        r = call("GET", "/api/final/outputs")[1]
        assert r["成品"] == final.name and r["可以複製到Windows"]
        code, r = call("POST", "/api/final/to_windows", {"路徑": "/etc/hosts"})
        assert code == 200 and r["成品是"] == "最終成品"
        _wait_until(lambda: (call("GET", "/api/final/outputs")[1]["複製"] or [{}])[0].get("狀態") == "完成")
        assert (dl / final.name).read_bytes() == final.read_bytes()
        call("POST", "/api/final/to_windows", {})
        _wait_until(lambda: (dl / "最終成品_0-98_sw (2).mp4").exists() and
                    call("GET", "/api/final/outputs")[1]["複製"][0]["狀態"] == "完成")   # 同名加編號，不蓋掉

        def no_windows():
            raise RuntimeError("問不到 Windows 的使用者資料夾")
        fileio.windows_downloads = no_windows
        code, r = call("POST", "/api/final/to_windows", {})
        assert code == 400 and "Windows" in r["error"]


def test_final_endpoints_without_product_or_project():
    with _server() as (httpd, call, _):
        assert call("POST", "/api/final/reveal", {})[0] == 409                  # 還沒選專案
    w = Path(tempfile.mkdtemp()) / "空_剪輯工作區"
    w.mkdir()
    with _server(workdir=w) as (httpd, call, _):
        r = call("GET", "/api/final/outputs")[1]
        assert r["成品"] is None
        assert call("POST", "/api/final/reveal", {})[0] == 404


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"通過：{t.__name__}")
    print(f"\n共 {len(tests)} 個測試通過")


if __name__ == "__main__":
    _run_all()
