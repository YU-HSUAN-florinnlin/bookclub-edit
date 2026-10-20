"""10-19 #234（宇軒定）：③ 背景補找出錯、第 4 步按第二次放行開始的 → 第 5 步「需留意」多一行提醒。

- 第 4 步開始時記進略過紀錄（`execute.skipped_snapshot` 的 `補找沒做完`），跟「人名清單沒跑成功」同一種做法
- 補找正常 → 沒有；之後補找成功、重新跑第 4 步（重記一次）→ 沒有
- 網頁（node 跑純函式）：需留意那一頁有這一句；輸出確認視窗那一句也帶到
不跑模型、不碰真的工作區。獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_rescan_attention_234.py
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

from bookclub import execute, finalcheck, personnames  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

NODE = shutil.which("node")
LOG = {"片段": None, "紀錄": [], "範圍": [0.0, 10.0]}
TEXT = "這次補找沒做完：剛在 ③ 選的名字，老師講到的其他地方可能沒換掉，請聽一次"


def _start_step4(w: Path) -> dict:
    """模擬第 4 步開始：記下略過紀錄寫進執行進度（組裝成功後就是這一份），回第 5 步的需留意。"""
    snap = execute.skipped_snapshot(w)
    assert snap is not None
    wd.write_json(execute.progress_path(w), {execute.SKIPPED_FIELD: snap})
    return finalcheck.attention_rows(w, LOG)


def _rescan_error(w: Path) -> None:
    wd.write_json(personnames.rescan_path(w), {"待補找": True, "要求": 1, "錯誤": "RuntimeError：假的錯誤",
                                                "錯誤時間": "2026-10-19T10:00:00", "錯誤已提醒": False})


def test_rescan_error_released_listed():
    w = TE._fresh()
    _rescan_error(w)
    assert personnames.rescan_error_gate(w)          # 第一次按：擋一次
    assert personnames.rescan_error_gate(w) is None  # 再按：放行
    att = _start_step4(w)
    assert att["補找沒做完"] and att["有紀錄"]
    g = finalcheck.attention_gaps(att)
    assert g["補找沒做完"] and "③ 剛選的名字這次補找沒做完" in g["需留意說明"]
    assert finalcheck.RESCAN_UNFINISHED == TEXT


def test_rescan_ok_not_listed_and_cleared_after_success():
    w = TE._fresh()
    att = _start_step4(w)                             # 從來沒補找過
    assert not att["補找沒做完"] and not finalcheck.attention_gaps(att)["補找沒做完"]
    _rescan_error(w)
    personnames.rescan_error_gate(w)
    assert _start_step4(w)["補找沒做完"]
    # 之後補找成功（背景做完會清掉待補找與錯誤）→ 重新跑第 4 步就不再列
    wd.write_json(personnames.rescan_path(w), {"待補找": False, "要求": 2})
    att = _start_step4(w)
    assert not att["補找沒做完"]
    assert "補找" not in finalcheck.attention_gaps(att)["需留意說明"]
    # 舊的執行紀錄沒有這一欄：當成沒有
    wd.write_json(execute.progress_path(w), {execute.SKIPPED_FIELD: {"沒處理": 0, "列": []}})
    assert not finalcheck.attention_rows(w, LOG)["補找沒做完"]


def test_web_attention_line():
    if not NODE:
        print("（沒有 node，略過）")
        return
    fcj = (REPO_ROOT / "bookclub" / "web" / "finalcheck.js").read_text(encoding="utf-8")
    pre = SF._ESC + "".join(SF._fn(fcj, n) for n in ("fcAttentionHtml",))
    code = pre + """
      const on = fcAttentionHtml({"有紀錄": true, "列": [], "補找沒做完": true}, [], []);
      const off = fcAttentionHtml({"有紀錄": true, "列": []}, [], []);
      console.log(JSON.stringify({on, off}));"""
    got = json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True).stdout)
    assert TEXT + "。" in got["on"] and "沒有要留意的地方" in got["on"]
    assert "補找沒做完" not in got["off"]


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
