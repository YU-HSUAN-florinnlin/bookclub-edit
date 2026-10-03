"""第 5 步網頁（`bookclub/web/finalcheck.js`）10-03 第八批 #63–#65 的測試，與 #64 用到的後端換算。

網頁本身不開瀏覽器：能抽成純函式的（試聽怎麼播、通過後換哪一筆）用 node 跑；畫面行為要人實際點過（見 CHANGELOG）。
後端：`finalcheck.goto_output_time`（原片時間 → 成品時間）用手寫的處理紀錄，不碰真的影片、模型。
獨立可跑：.venv/bin/python tests/test_finalcheck_web.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import finalcheck as fc  # noqa: E402
from bookclub import proclog, render  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

WEB = REPO_ROOT / "bookclub" / "web"
FCJS = (WEB / "finalcheck.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _fn(src: str, name: str) -> str:
    """從 JS 原始碼切出一個頂層函式（到下一個行首的 `}`）。"""
    m = re.search(rf"^(async )?function {name}\(.*?^\}}\n", src, re.S | re.M)
    assert m, name
    return m.group(0)


def _node(code: str):
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


# ---------- #65：通過後從目前這一筆往後找 ----------

def test_next_unseen_searches_forward_from_current():
    if not NODE:
        print("（沒有 node，略過）")
        return
    fn = _fn(FCJS, "fcNextUnseen")
    recs = [{"鍵": "a"}, {"鍵": "b"}, {"鍵": "c", "結果": "通過"}, {"鍵": "d"}, {"鍵": "e"}]
    got = _node(fn + f"""
      const R = {json.dumps(recs, ensure_ascii=False)};
      console.log(JSON.stringify([
        fcNextUnseen(R, "b"),                 // 往後找：c 看過了 → d（以前從頭找會回到 a）
        fcNextUnseen(R, "d"),                 // → e
        fcNextUnseen(R, "e"),                 // 後面沒有了才繞回前面 → a
        fcNextUnseen(R, "b", ["a", "e"]),     // 只看要人聽的：只在 a、e 裡找，往後先找到 e
        fcNextUnseen([{{"鍵": "x", "結果": "通過"}}], "x"),   // 全部看了 → null
        fcNextUnseen(R, "不在清單"),            // 目前這一筆找不到 → 從頭找
      ]));""")
    assert got == ["d", "e", "a", "e", None, "a"], got


def test_decide_uses_forward_search():
    body = _fn(FCJS, "fcDecide")
    assert "fcNextUnseen(" in body and 'fcRecs().find((x) => !x["結果"])' not in body


# ---------- #63：處理後用影片本身播、處理前影片同步靜音 ----------

def test_ab_plan_after_plays_video_itself_and_before_mutes_video():
    if not NODE:
        print("（沒有 node，略過）")
        return
    const = re.search(r"^const FC_AB_CONTEXT = .*\n", FCJS, re.M).group(0)
    fn = const + _fn(FCJS, "fcAbPlan")
    got = _node(fn + """
      const r = {"鍵": "k1", "原片": [100, 104], "成品": [80, 85]};
      const cut = {"鍵": "k2", "原片": [200, 210], "成品": [null, null]};
      const near0 = {"鍵": "k3", "原片": [1, 2], "成品": [1, 2]};
      console.log(JSON.stringify([fcAbPlan(r, "後"), fcAbPlan(r, "前"), fcAbPlan(cut, "後"), fcAbPlan(cut, "前"), fcAbPlan(near0, "後")]));""")
    after, before, cut_after, cut_before, near0 = got
    assert after == {"video": {"from": 78, "to": 87, "muted": False}, "audio": None}, after   # 成品起點前 2 秒到終點後 2 秒，聲音就是影片的
    assert before["video"] == {"from": 78, "to": 86, "muted": True}, before                   # 影片同步、靜音；長度＝原聲 4＋4 秒
    assert before["audio"].startswith("/api/final/clip?which=") and "k1" in before["audio"]
    assert cut_after is None                                                                   # 成品裡剪掉了：沒有處理後
    assert cut_before["video"] is None and cut_before["audio"]                                 # 只有原聲、影片不動
    assert near0["video"]["from"] == 0


def test_ab_wiring():
    """接線（只擋被拿掉；行為要人點）：試聽中不換右邊那一筆、處理前靜音那段不算看過、停按鈕會停影片。"""
    play = _fn(FCJS, "fcPlayAB")
    assert "fcAbPlan(" in play and "v.muted = plan.video.muted" in play
    assert "fc.ab ||" in _fn(FCJS, "fcFollow")
    assert 'fc.ab.which === "前"' in _fn(FCJS, "fcTrack")
    assert "fcAbTick(v.currentTime)" in FCJS and "fcStopAB();" in FCJS


# ---------- #64：輸入時間跳過去（成品／原片） ----------

def test_goto_box_follows_step3_and_asks_backend_for_source_time():
    goto = _fn(FCJS, "fcGoto")
    assert "rvParseTime(" in goto                       # 跟第 3 步同一支時間解析
    assert "/api/final/goto?src=" in goto               # 原片時間由後端換算，不在前端自己扣
    assert "fcSrcTime(" not in goto and '"片段"' not in goto
    assert 'id="fc-goto"' in FCJS and '<option value="原片">原片時間</option>' in FCJS
    server = (REPO_ROOT / "bookclub" / "server.py").read_text(encoding="utf-8")
    assert '"/api/final/goto"' in server and "goto_output_time(" in server


def test_goto_output_time_uses_same_mapping():
    w = Path(tempfile.mkdtemp()) / "工作區"
    plist = render.pieces(10.0, 20.0, [(13.0, 15.0)], [{"at": 17.0, "dur": 0.5}])
    wd.write_json(proclog.log_path(w), {"產生時間": "t1", "片段": plist, "紀錄": []})
    assert fc.goto_output_time(w, 11.0) == {"成品秒": 1.0, "說明": ""}
    assert fc.goto_output_time(w, 18.0)["成品秒"] == round(fc.to_output_time(18.0, plist), 3)   # 停格之後
    r = fc.goto_output_time(w, 14.0)                    # 剪掉的範圍裡 → 剪掉之後第一個留下來的地方
    assert r["成品秒"] == 3.0 and "剪掉" in r["說明"], r
    r = fc.goto_output_time(w, 25.0)                    # 之後都不在成品裡
    assert r["成品秒"] is None and r["說明"], r
    # render audio（沒有片段表）：時間一樣
    w2 = Path(tempfile.mkdtemp()) / "工作區"
    wd.write_json(proclog.log_path(w2), {"產生時間": "t1", "片段": None, "紀錄": []})
    assert fc.goto_output_time(w2, 42.5) == {"成品秒": 42.5, "說明": ""}


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
    sys.exit(0)
