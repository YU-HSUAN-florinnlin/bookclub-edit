"""10-20 總審查（批次 1020-1）：第 4 步開始時「照原聲留著的名字」算不出來，第 5 步要說出來（不擋）。

以前只印一行、清單當空的，第 5 步「需留意」看起來像沒有照原聲留著的名字。現在照「補找沒做完」（#234）的做法：
- 第 4 步開始時記進略過紀錄（`execute.skipped_snapshot` 的 `照原聲名字算不出來`）
- 第 5 步「需留意」那一頁多一行；輸出確認視窗那一句也帶到（要確認、不擋）
- 算得出來 → 沒有這一行；舊的執行紀錄沒有這一欄當成沒有
不跑模型、不碰真的工作區。獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_kept_unlisted_1020.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_execute as TE  # noqa: E402 — 假工作區、假名冊（BOOKCLUB_DATA_DIR）
import test_skip_flow as SF  # noqa: E402

from bookclub import execute, finalcheck  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

NODE = shutil.which("node")
LOG = {"片段": None, "紀錄": [], "範圍": [0.0, 10.0]}
TEXT = "照原聲留著的名字這次算不出來，下面沒有列出：③ 選不用處理、不是名字，或還沒有英文名的名字，請自己聽一次"


def _start_step4(w: Path) -> dict:
    snap = execute.skipped_snapshot(w)
    assert snap is not None
    wd.write_json(execute.progress_path(w), {execute.SKIPPED_FIELD: snap})
    return finalcheck.attention_rows(w, LOG)


def test_kept_rows_error_listed():
    w = TE._fresh()
    real = execute.kept_name_rows

    def boom(workdir):
        raise RuntimeError("假的錯誤")

    execute.kept_name_rows = boom
    try:
        att = _start_step4(w)
    finally:
        execute.kept_name_rows = real
    assert att["照原聲名字算不出來"] and att["有紀錄"], att
    g = finalcheck.attention_gaps(att)
    assert g["照原聲名字算不出來"] and "照原聲留著的名字這次算不出來" in g["需留意說明"], g
    assert finalcheck.KEPT_UNLISTED == TEXT


def test_kept_rows_ok_not_listed():
    w = TE._fresh()
    att = _start_step4(w)
    assert not att["照原聲名字算不出來"]
    g = finalcheck.attention_gaps(att)
    assert not g["照原聲名字算不出來"] and "算不出來" not in g["需留意說明"]
    wd.write_json(execute.progress_path(w), {execute.SKIPPED_FIELD: {"沒處理": 0, "列": []}})   # 舊紀錄沒這一欄
    assert not finalcheck.attention_rows(w, LOG)["照原聲名字算不出來"]


def test_web_attention_line():
    if not NODE:
        print("（沒有 node，略過）")
        return
    fcj = (REPO_ROOT / "bookclub" / "web" / "finalcheck.js").read_text(encoding="utf-8")
    pre = SF._ESC + "".join(SF._fn(fcj, n) for n in ("fcAttentionHtml",))
    code = pre + """
      const on = fcAttentionHtml({"有紀錄": true, "列": [], "照原聲名字算不出來": true}, [], []);
      const off = fcAttentionHtml({"有紀錄": true, "列": []}, [], []);
      console.log(JSON.stringify({on, off}));"""
    got = json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True).stdout)
    assert TEXT + "。" in got["on"], got["on"]
    assert "算不出來" not in got["off"]


if __name__ == "__main__":
    fns = [(k, v) for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print(f"✓ {name}")
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"✗ {name}：{type(e).__name__}：{e}")
    print(f"{len(fns) - bad}/{len(fns)} 通過")
    sys.exit(1 if bad else 0)
