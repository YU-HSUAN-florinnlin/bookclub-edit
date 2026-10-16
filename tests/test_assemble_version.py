"""#122（批次 1017-1）：工具更新改了組裝做法，第 4 步「做過沒有」要看得出來、要重新組裝。

- 輸出摘要記 `組裝做法版本`（`render.ASSEMBLE_VERSION`），`execute.render_done` 比對；舊摘要沒這一欄當第 1 版。
- `說話者判斷.json` 比成品新也要重新組裝（組裝會讀它找老師的句子）。
- 組裝相關程式的雜湊記在下面：程式改了這支測試就會失敗，提醒開發者選一個——
  1. 改了成品聽起來或看起來會不一樣的做法 → `render.ASSEMBLE_VERSION` 加 1，再更新下面的雜湊與版本號；
  2. 只是重構、改註解 → 只更新下面的雜湊，旁邊寫一句「這次沒改成品的聲音和畫面」。

獨立可跑：.venv/bin/python tests/test_assemble_version.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 暫存資料夾跑完自己清（要在 tempfile 之前）

import hashlib
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import fake_workdir  # noqa: E402

from bookclub import execute, render, roomtone  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

TAG = "0-3"

# 組裝相關程式 → 記下的雜湊（SHA-256 前 16 碼）。改了請看檔頭說明。
ASSEMBLE_FILES_VERSION = 1
ASSEMBLE_FILES = {   # 10-17 #122 第 1 版的起點
    "bookclub/render.py": "1ff34b63373c060e",
    "bookclub/assemble.py": "c29a103f9b113d70",
    "bookclub/roomtone.py": "4ea9c4a050cbe090",
    "bookclub/silentedge.py": "6715c0e2b51c5ee8",
    "bookclub/timemap.py": "a824a2b104fe012c",
}


def _digest(rel: str) -> str:
    return hashlib.sha256((REPO_ROOT / rel).read_bytes()).hexdigest()[:16]


def _done_workdir(summary_extra: dict | None = None) -> Path:
    """假工作區＋一支比所有輸入檔都新、驗證通過的成品（基準：render_done 應該說做過了）。"""
    w = fake_workdir.make(Path(tempfile.mkdtemp()) / "w")
    out = w / "輸出"
    out.mkdir(exist_ok=True)
    summ = {"輸出": {"sw": {"驗證": {"通過": True}}}, "底噪挑法": roomtone.METHOD}
    summ.update(summary_extra or {})
    wd.write_json(out / f"輸出摘要_{TAG}.json", summ)
    time.sleep(0.02)
    (out / f"成品_v1_{TAG}_sw.mp4").write_bytes(b"x")
    return w


def test_old_summary_without_field_counts_as_version_1():
    """舊摘要沒有 `組裝做法版本` → 當第 1 版；發布這版不會讓所有工作區都要重組。"""
    w = _done_workdir()
    assert render.ASSEMBLE_VERSION == 1
    done, why = execute.render_done(w, TAG, ["sw"])
    assert done, why
    shutil.rmtree(w.parent.parent)


def test_assemble_version_bump_requires_reassemble():
    """重現 #122：工具更新改了組裝做法（版本號 +1），成品時間比輸入檔新也要重新組裝。"""
    w = _done_workdir({"組裝做法版本": getattr(render, "ASSEMBLE_VERSION", 1)})
    assert execute.render_done(w, TAG, ["sw"])[0]
    old = getattr(render, "ASSEMBLE_VERSION", 1)
    render.ASSEMBLE_VERSION = old + 1
    try:
        done, why = execute.render_done(w, TAG, ["sw"])
        assert not done, why
        assert "組裝做法" in why and "沿用" in why, why
    finally:
        render.ASSEMBLE_VERSION = old
    assert execute.render_done(w, TAG, ["sw"])[0]
    shutil.rmtree(w.parent.parent)


def test_speakers_newer_than_product_requires_reassemble():
    """`說話者判斷.json` 比成品新 → 要重新組裝（組裝會讀它找老師的句子）。"""
    w = _done_workdir({"組裝做法版本": 1})
    assert execute.render_done(w, TAG, ["sw"])[0]
    time.sleep(0.02)
    sp = wd.speakers_path(w)
    sp.write_bytes(sp.read_bytes())
    done, why = execute.render_done(w, TAG, ["sw"])
    assert not done, why
    shutil.rmtree(w.parent.parent)


def test_render_summary_writes_assemble_version():
    """`render_video` 寫輸出摘要時要帶 `組裝做法版本`（不跑 ffmpeg，看程式碼）。"""
    src = (REPO_ROOT / "bookclub" / "render.py").read_text(encoding="utf-8")
    assert '"組裝做法版本": ASSEMBLE_VERSION' in src


def test_assemble_code_hash_matches_recorded_version():
    """組裝程式改了沒調號就失敗（看檔頭說明怎麼處理）。"""
    assert render.ASSEMBLE_VERSION == ASSEMBLE_FILES_VERSION, (
        f"render.ASSEMBLE_VERSION 是 {render.ASSEMBLE_VERSION}，這支測試記的是 {ASSEMBLE_FILES_VERSION}：一起更新")
    changed = [rel for rel, h in ASSEMBLE_FILES.items() if _digest(rel) != h]
    assert not changed, (
        "組裝相關程式改了：" + "、".join(changed) + "。改了成品的聲音或畫面 → render.ASSEMBLE_VERSION 加 1；"
        "沒改 → 只更新這支測試的雜湊並寫一句「這次沒改成品的聲音和畫面」。現在的雜湊："
        + "、".join(f"{rel}={_digest(rel)}" for rel in changed))


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
