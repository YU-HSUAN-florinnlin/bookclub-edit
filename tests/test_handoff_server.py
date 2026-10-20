"""33 號設計（#257–#260）接力交接的網頁 API：交接打包（選資料夾視窗取消不動、位置檢查、第 4 步在跑擋下）、
清掉暫存、匯入交接包（① zip ② 原片 → 開始 → 切到那一集）、總覽清掉這集。

選檔、選資料夾視窗都用假的（不叫真的視窗）；量長度、抽音軌用假的（不叫 ffprobe／ffmpeg）。合成資料，不碰 ~/讀書會剪輯資料。
獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_handoff_server.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import fake_workdir  # noqa: E402
from bookclub import fileio, handoff  # noqa: E402
from bookclub import server as srv  # noqa: E402
from bookclub import turns as turns_mod  # noqa: E402

_BASE: Path | None = None


def _teacher() -> tuple[Path, Path]:
    global _BASE
    if _BASE is None:
        _BASE = Path(tempfile.mkdtemp()) / "base"
        fake_workdir.make(_BASE)
    root = Path(tempfile.mkdtemp()) / "老師"
    shutil.copytree(_BASE, root)
    w = root / "假影片_剪輯工作區"
    os.rename(root / "工作區", w)
    video = root / "假影片.mp4"
    data = json.loads((w / "分析結果.json").read_text(encoding="utf-8"))
    data.update({"video": str(video), "workdir": str(w)})
    (w / "分析結果.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for person in turns_mod.page_data(w)["學員"]:
        turns_mod.set_real_name(w, person, turns_mod.UNKNOWN_REAL)
    return w, video


def _serve(w: Path | None):
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


def _wait(base, key):
    st = None
    for _ in range(400):
        st = _call(base, "/api/handoff/state")[1]
        job = st["打包"] if key == "打包" else st["匯入"]["工作"]
        if job and job["狀態"] not in ("等待", "存檔中", "核對中", "比對原片", "匯入中"):
            return st
        time.sleep(0.05)
    raise AssertionError(f"等太久：{st}")


def _files(w: Path) -> dict:
    return {str(p.relative_to(w)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(w.rglob("*")) if p.is_file()}


def test_pack_api_and_clean():
    w, video = _teacher()
    loc = Path(tempfile.mkdtemp()) / "雲端空間"
    loc.mkdir()
    httpd, base = _serve(w)
    orig = fileio.pick_folder
    try:
        code, st = _call(base, "/api/handoff/state")
        assert code == 200 and st["可以打包"]["第3步"] is None and "第 4 步" in st["可以打包"]["第5步"]
        assert st["打包"] is None and st["匯入"]["交接包"] is None
        before = _files(w)
        fileio.pick_folder = lambda title: None              # 假的視窗：按了取消
        assert _call(base, "/api/handoff/pick-folder", {}) == (200, {"取消": True})
        assert _files(w) == before and not list(loc.iterdir())
        fileio.pick_folder = lambda title: loc
        code, r = _call(base, "/api/handoff/pick-folder", {})
        assert code == 200 and r["路徑"] == str(loc)
        code, r = _call(base, "/api/handoff/pack", {"段": "第3步", "路徑": str(w / "校對")})
        assert code == 400 and "工作資料夾裡" in r["error"]
        code, r = _call(base, "/api/handoff/pack", {"段": "第5步", "路徑": str(loc)})
        assert code == 400 and "第 4 步" in r["error"]
        httpd.exec_state["running"] = True
        code, r = _call(base, "/api/handoff/pack", {"段": "第3步", "路徑": str(loc)})
        assert code == 400 and "第 4 步正在跑" in r["error"]
        httpd.exec_state["running"] = False
        code, r = _call(base, "/api/handoff/pack", {"段": "第3步", "路徑": str(loc)})
        assert code == 200 and r["ok"]
        st = _wait(base, "打包")
        assert st["打包"]["狀態"] == "已存好" and "請上傳到雲端空間" in st["打包"]["說明"]
        assert st["最近一次"]["哪一段"] == "第3步" and len(list(loc.glob("*.zip"))) == 1
        plan = _call(base, "/api/handoff/clean-plan")[1]
        assert plan["有交接包"] and plan["合計"] > 0 and not plan["含原片複本"]
        code, r = _call(base, "/api/handoff/clean-copy", {"清單指紋": "不對"})
        assert code == 400
        code, r = _call(base, "/api/handoff/clean-copy", {"清單指紋": plan["清單指紋"]})
        assert code == 200 and r["省下位元組"] > 0 and video.is_file() and not (w / "audio.flac").exists()
    finally:
        fileio.pick_folder = orig
        httpd.shutdown()
        httpd.server_close()


def test_import_api_switches_project_then_clean_episode():
    w, video = _teacher()
    loc = Path(tempfile.mkdtemp())
    job = handoff.PackJob(w, loc, handoff.STAGE3)
    job.run()
    z = job.dest
    dl = Path(tempfile.mkdtemp()) / "夥伴下載"
    dl.mkdir()
    v2 = dl / video.name
    shutil.copy2(video, v2)
    httpd, base = _serve(None)   # 夥伴這台還沒有任何專案
    orig_pick, orig_probe, orig_audio = fileio.pick_file, handoff.probe_duration, handoff._ensure_audio
    titles = []
    try:
        handoff.probe_duration = lambda p: fake_workdir.DUR
        handoff._ensure_audio = lambda video, target: (Path(target) / "audio.flac").write_bytes(b"fake")
        code, r = _call(base, "/api/handoff/import/start", {})
        assert code == 400 and "交接包" in r["error"]
        fileio.pick_file = lambda title, **kw: None             # ① 按了取消
        assert _call(base, "/api/handoff/import/zip", {}) == (200, {"取消": True})
        code, r = _call(base, "/api/handoff/import/zip", {"路徑": str(v2)})
        assert code == 400
        code, r = _call(base, "/api/handoff/import/zip", {"路徑": str(z)})
        assert code == 200 and r["交接包"]["摘要"]["原片檔名"] == "假影片.mp4"
        code, r = _call(base, "/api/handoff/import/start", {})
        assert code == 400 and "原片" in r["error"]

        def fake_pick(title, **kw):
            titles.append(title)
            return v2
        fileio.pick_file = fake_pick                             # ② 選原片（系統視窗，標題寫要選哪一支）
        code, r = _call(base, "/api/pick", {"用途": "交接原片"})
        assert code == 200 and "假影片.mp4" in titles[-1] and "雲端空間" in titles[-1]
        st = _call(base, "/api/handoff/state")[1]
        assert st["匯入"]["原片"]["路徑"] == str(v2)
        code, r = _call(base, "/api/handoff/import/start", {})
        assert code == 200
        st = _wait(base, "匯入")
        res = st["匯入"]["工作"]["結果"]
        assert st["匯入"]["工作"]["狀態"] == "完成" and res["去哪一步"] == "step4" and res["新建"]
        w2 = Path(res["工作區"])
        assert httpd.workdir == w2, "匯入好了切到這一集"
        assert str(w2) in [p["路徑"] for p in _call(base, "/api/projects")[1]["專案"]]
        assert _call(base, "/api/handoff/state")[1]["匯入"]["交接包"] is None, "做完清掉選過的包與原片"
        # 夥伴做完、第 5 步交接打包 → 總覽「已交出去」→ 清掉這集
        prod = w2 / "輸出" / "成品_v1_0-3_sw.mp4"
        prod.parent.mkdir(parents=True, exist_ok=True)
        prod.write_bytes(b"0" * 1000)
        (w2 / "覆核").mkdir(exist_ok=True)
        (w2 / "覆核" / "成品檢查.json").write_text(json.dumps({"成品影片": "輸出/成品_v1_0-3_sw.mp4"}), encoding="utf-8")
        code, r = _call(base, "/api/handoff/clean-episode", {"路徑": str(w2)})
        assert code == 400 and "還沒交出去" in r["error"]
        code, r = _call(base, "/api/handoff/pack", {"段": "第5步", "路徑": str(loc)})
        assert code == 200
        assert _wait(base, "打包")["打包"]["狀態"] == "已存好"
        row = next(p for p in _call(base, "/api/projects")[1]["專案"] if p["路徑"] == str(w2))
        assert row["交接"] and row["交接"]["大小"] > 0
        code, r = _call(base, "/api/handoff/clean-episode", {"路徑": str(w2)})
        assert code == 200 and not w2.exists() and v2.is_file()
        assert str(w2) not in [p["路徑"] for p in _call(base, "/api/projects")[1]["專案"]]
        assert not httpd.has_workdir
        code, r = _call(base, "/api/handoff/clean-episode", {"路徑": str(w)})
        assert code == 403, "不在專案清單上的不清"
    finally:
        fileio.pick_file, handoff.probe_duration, handoff._ensure_audio = orig_pick, orig_probe, orig_audio
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        fn()
        print(f"✓ {name}")
    print(f"全部 {len(tests)} 個通過")
