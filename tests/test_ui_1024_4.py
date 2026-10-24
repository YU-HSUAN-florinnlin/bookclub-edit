"""1024-4 #286～#288 總覽、側欄頂端、第 1 步、第 2 步介面減量的網頁測試（node 抽出純函式＋靜態檢查）。

合成資料；不開瀏覽器。獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_ui_1024_4.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401

import json
import re
import shutil
import subprocess
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "bookclub" / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")
FIO = (WEB / "fileio.js").read_text(encoding="utf-8")
CSS = (WEB / "style.css").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _fn(name: str) -> str:
    m = re.search(rf"^(async )?function {name}\(.*?^\}}\n", APP, re.S | re.M)
    assert m, name
    return m.group(0)


def _run(code: str):
    r = subprocess.run([NODE, "-e", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


PRE = 'function esc(s){return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}\n'
NAMES = ["轉文字", "認老師", "找重疊", "挑參考音", "找名字", "段落分析"]


def _state(done_n, pr=None):
    return {"substeps": {k: {"done": i < done_n} for i, k in enumerate(NAMES)}, "proofread": pr or {}}


def test_overview_progress_and_next_step():
    if not NODE:
        return
    cases = [_state(0), _state(3),
             _state(6, {"done": False, "已確認": 2, "學員段落數": 5}), _state(6, {"done": False, "已確認": 0, "學員段落數": 5}),
             _state(6, {"done": True, "已確認": 5, "學員段落數": 5})]
    got = _run(PRE + _fn("ovProgress") + f"console.log(JSON.stringify({json.dumps(cases)}.map(ovProgress)));")
    assert [g["步驟"] for g in got] == [1, 1, 3, 3, 4], got
    assert got[0]["進度"] is None and got[1]["進度"] == {"已": 3, "共": 6}
    assert got[2]["進度"] == {"已": 2, "共": 5} and "到一半" in got[2]["句"]
    assert got[3]["進度"] == {"已": 0, "共": 5} and "還沒開始覆核" in got[3]["句"]
    assert got[4]["進度"] is None   # 沒有真實數字就不畫進度條


def test_overview_structure():
    ov = APP[APP.index("async function renderOverview"):APP.index("async function renderPicker")]
    assert "目前的專案" not in ov and "<h2>已有的專案" not in ov
    assert "ovContinueHtml(" in ov and "繼續第" in APP and 'id="ovGo"' in APP and "ov-others" in ov
    assert 'class="secondary pj-switch"' in ov and "/api/projects/switch" in ov   # 切換專案功能還在
    assert 'title="${esc(p["路徑"])}"' in ov   # 路徑只放 title
    assert "pickCardHtml()" in ov and "hoOverviewCardHtml" in ov and "bindPickCard(" in ov
    assert "從電腦選一支讀書會錄影。" in FIO and 'id="pkStart"' in FIO
    assert "收到別人傳來的 zip 時用這個。" in (WEB / "handoff.js").read_text(encoding="utf-8")
    assert "按「選影片」會跳出電腦內建" not in FIO
    assert "/* 1024-4 總覽、第1、2步 */" in CSS


def test_sidebar_top_short_name():
    if not NODE:
        return
    st = {"video": {"name": "某次讀書會.mp4", "duration_hms": "01:00:00"}, "專案標示": {"標示": "正式", "顯示名稱": "x（y）"}, "workdir": "/a/b_剪輯工作區"}
    got = _run(PRE + _fn("pjShortHtml") + f"console.log(JSON.stringify(pjShortHtml({json.dumps(st)})));")
    assert "某次讀書會" in got and ".mp4" not in got.split("title=")[1].split(">")[1] and "正式" in got
    assert 'title="' in got and "/a/b_剪輯工作區" in got
    assert "<br>" not in APP[APP.index("async function renderSidebar"):APP.index("function navSubHtml")].split("videoInfoEl.innerHTML")[1].split("\n")[0]


def test_step1_states():
    s1 = APP[APP.index("async function renderStep1Body"):APP.index("async function startAnalyze")]
    for need in ("把說話變文字", "找出老師在說話的地方", "找出兩人同時說話", "挑老師聲音的候選", "找出提到的人名", "分出學員分享段落"):
        assert need in APP
    assert 'id="btnAnalyze"' in s1 and 'id="runLog"' in s1 and 'id="runAwake"' in s1 and 'id="step1Warn"' in s1
    assert "下一步：挑老師的聲音" in s1 and 'href="#step2"' in s1 and "詳細資料" in s1 and "現在在：" in s1
    assert s1.index("${claudeCard}") < s1.index("${body}")   # 沒跑成功的紅卡放最上面
    # 耗時欄在詳細資料裡
    assert s1.index("耗時") > s1.index("s1-detail")


def test_step2_structure():
    if not NODE:
        return
    s2 = APP[APP.index("function renderStep2Body"):APP.index("async function renderRoomCard")]
    assert 'id="btnPrev"' not in s2 and "正在聽：" not in s2 and "目前選定：" not in s2 and 'class="hint"' not in s2
    assert "逐字稿有錯字嗎？展開修改" in s2 and "改用這一個（第" in s2 and 'id="btnUse"' in s2 and 'id="btnSwapAudio"' in s2
    assert "只有老師一個人的聲音" in s2 and "沒有笑聲、咳嗽、別人應聲" in s2 and "聽起來最像老師平常說話" in s2
    assert "鍵盤 ← → 也可以切換" in s2 and "（已選定）" in s2 and "（試聽中）" in s2
    assert "現在用的音檔就是這個候選的音檔" not in APP
    note = _fn("refAudioNote")
    got = _run(PRE + 'const refsCache = {"現在用的音檔取樣率": 16000};\nfunction refRateName(r){return r>=44100?"新版":"舊版";}\n' + note +
               'console.log(JSON.stringify([refAudioNote({"跟現在用的音檔一樣": true}, true), refAudioNote({"跟現在用的音檔一樣": false, "取樣率": 48000}, true)]));')
    assert got[0] == "" and "兩個不是同一份" in got[1]
    # 頁頂小清單與下一步
    pre = PRE + 'let refsCache, roomInfo = null;\n' + _fn("s2Items") + _fn("s2TopHtml")
    cases = _run(pre + """
      refsCache = {"已選定名次": 2, candidates: [{rank: 1}, {rank: 2}]};
      const a = s2TopHtml(); roomInfo = {"已確認": false, start: 1}; const b = s2TopHtml(); roomInfo = {"已確認": true, start: 1}; const c = s2Items();
      refsCache = {"已選定名次": null, candidates: [{rank: 1}]}; const d = s2Items();
      console.log(JSON.stringify([a, b, c, d]));""")
    assert "確認底噪" in cases[0] and "還沒確認" not in cases[0] and "還沒確認" in cases[1]
    assert cases[2]["picked"] and cases[2]["roomDone"] and not cases[3]["picked"]
    assert 'href="#step3"' in APP and "下一步：到第 3 步" in APP
    room = APP[APP.index("async function renderRoomCard"):APP.index("function refRateName")]
    assert "底噪已確認" in room and "重聽" in room and 'id="roomOk"' in room and 'id="roomNext"' in room
    assert "聽聽看這段是不是完全安靜（很小聲是正常的，可以開大音量）。" in room and "還沒確認也可以先往下做" in room


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
