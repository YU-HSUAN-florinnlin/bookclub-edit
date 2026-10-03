"""bookclub/safeview.py（`bookclub inspect` 安全查詢指令，10-02 第六批）的測試。

做法：用 `tests/fake_workdir.py` 建假工作區，再往每一份資料檔的文字欄位塞假名字、假逐字稿、假原因
（還有沒見過的新欄位、以本名當鍵的物件），每一個主題都跑一次，輸出裡不能出現那些字串。
另外驗：時間換算跟 `render.to_output_time` 一樣（含停格）、查詢不寫任何檔、說話者是本名時遮掉。
獨立可跑：.venv/bin/python tests/test_safeview.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_workdir  # noqa: E402

from bookclub import render, safeview, timemap  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

SECRETS = ["秘密逐字稿甲乙丙", "王大明", "憂鬱症的假原因", "假名字林小花", "新欄位的祕密", "本名當鍵", "小美", "阿明",
           "老師說的", "分享的第", "藏在老師段落", "假路徑祕密", "假警告祕密", "秘密自打代號"]
# 10-02 第七批：代號（Amy、艾瑪這類）不是個資，新舊代號名單上的照印；自己打的代號照樣遮（秘密自打代號）
PLIST = [{"src": [0.0, 50.0], "freeze": 0.0}, {"src": [50.0, 70.0], "freeze": 1.5}, {"src": [80.0, 180.0], "freeze": 0.0}]


def _w(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _r(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


_W = None


def make() -> Path:
    """假工作區＋每一份檔案都塞秘密字串（同一支測試共用一份）。"""
    global _W
    if _W:
        return _W
    w = fake_workdir.make(tempfile.mkdtemp())
    t = _r(w / "校對" / "段落.json")
    t["段落"][2].update({"校對稿": "秘密逐字稿甲乙丙", "新欄位": "新欄位的祕密", "巢狀": {"本名當鍵": "王大明"}})
    t["段落"][4]["說話者"] = "王大明"                      # 說話者欄位是本名（不是學員N）
    t["學員"]["王大明"] = {"秒數": 3.0, "段數": 1, "代號": "Amy"}
    t["學員"]["學員9"] = {"秒數": 1.0, "段數": 1, "代號": "秘密自打代號"}
    _w(w / "校對" / "段落.json", t)
    s = _r(w / "說話者判斷.json")
    s["sentences"][0].update({"text": "秘密逐字稿甲乙丙", "判斷依據": "假名字林小花"})
    _w(w / "說話者判斷.json", s)
    m = _r(w / "transcript" / "merged.json")
    m["words"][0]["word"] = "王大明"
    _w(w / "transcript" / "merged.json", m)
    _w(w / "名字覆核決定.json", {"1": {"做法": "整句換掉", "tags": [], "note": "憂鬱症的假原因", "已確認": True,
                                     "改稿": "假名字林小花", "新欄位": "新欄位的祕密"},
                                "2": {"做法": "假名字林小花", "tags": ["假名字林小花"]}})
    _w(w / "覆核" / "覆核決定.json", {
        "版本": 1, "重疊": {"O69.60": {"做法": "只留老師", "老師文字": "秘密逐字稿甲乙丙", "備註": "憂鬱症的假原因", "已確認": True}},
        "刪除段落": [{"id": "D001", "start": 10.0, "end": 12.0, "狀態": "刪除", "備註": "憂鬱症的假原因"}],
        "局部消音": [{"id": "M001", "start": 30.0, "end": 31.0, "方式": "墊底噪", "狀態": "消音", "備註": "假名字林小花",
                   "新欄位": "新欄位的祕密"}],
        "人工名字": [{"id": "NM001", "start": 100.0, "end": 104.0, "代號": "Tom", "matched_text": "", "sentence": "",
                   "老師整段": True, "整段文字": "秘密逐字稿甲乙丙"}],
        "學員聲音": {"王大明": "保留原聲", "學員2": "重新生成"}, "刪除建議": {"S1": {"決定": "刪除"}},
        "總檢查": {"聽過": ["段落外:T003:12.0", "王大明"], "看過": True}})
    gen = w / "生成"
    tlog = {"句子": [{"id": "S001", "text": "秘密逐字稿甲乙丙", "生成用文字": "假名字林小花", "slot": [76.0, 80.0], "slot_s": 4.0,
                      "嘗試": [{"第幾次": 1, "種子": 42, "語速": 1.0, "長度秒": 2.9, "轉回文字": "王大明", "內容相似度": 0.7,
                               "內容通過": False, "長度通過": False, "新欄位": "新欄位的祕密"}],
                      "選定": 1, "檔案": "生成/老師/假路徑祕密.wav", "要人聽": True, "第幾版": 2,
                      "放回時間格": {"放回做法": "標紅", "差異比例": -0.27, "檔案": "假路徑祕密", "原因": "憂鬱症的假原因"},
                      "以前的版本": [{"第幾版": 1, "種子": 42, "試過的種子": [42, 1, 2026], "退回原因": "憂鬱症的假原因"}]}],
            "統計": {"句數": 1, "要人聽句數": 1}}
    _w(gen / "老師紀錄.json", tlog)
    _w(gen / "學員紀錄.json", {"句子": [{"id": "T003_01", "段落": "T003", "學員": "王大明", "聲線": "女", "聲線名稱": "女3",
                                      "text": "秘密逐字稿甲乙丙", "slot": [40.0, 44.0], "嘗試": []}],
                               "學員聲線": {"王大明": {"聲線": "女"}}, "統計": {"段數": 1}})
    (gen / "老師").mkdir(parents=True, exist_ok=True)
    _w(gen / "老師" / "_重新生成版本.json", {"S001": {"text": "秘密逐字稿甲乙丙", "避開": [42], "以前的版本": [{"退回原因": "憂鬱症的假原因"}]}})
    (gen / "子程式紀錄.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [
        {"名稱": "老師聲音：生成", "參數": ["老師名字", "生成"], "秒": 12.0, "結束碼": 0},
        {"名稱": "學員聲音 王大明：生成", "參數": ["--voice", "假路徑祕密"], "秒": 3.0, "結束碼": 1, "新欄位": "新欄位的祕密"}]) + "\n",
        encoding="utf-8")
    _w(gen / "處理紀錄.json", {"版本": 1, "來源": "render video 試看", "範圍": [0.0, 180.0], "產生時間": "2026-10-02T18:00:00",
                               "片段": PLIST, "紀錄": [
                                   {"編號": 1, "類型": "名字整句換掉", "原片": [76.0, 80.0], "成品": [76.0, 80.0], "做了什麼": "假名字林小花",
                                    "覆核項目": ["名字:1"], "檔案": "假路徑祕密", "文字": "秘密逐字稿甲乙丙", "要人聽": True, "動到聲音": True,
                                    "新欄位": "新欄位的祕密"},
                                   {"編號": 2, "類型": "局部消音", "原片": [30.0, 31.0], "成品": [30.0, 31.0], "做了什麼": "王大明",
                                    "覆核項目": ["局部消音:M001", "王大明"], "要人聽": False, "動到聲音": True}],
                               "未登記的變動": [{"原片": [120.0, 120.5], "長度秒": 0.5, "格數": 25}]})
    _w(w / "覆核" / "成品檢查.json", {"版本": 1, "處理紀錄產生時間": "2026-10-02T18:00:00", "成品長度": 171.5,
                                     "逐筆": {"名字整句換掉@76.00": {"結果": "退回重做", "原因": "憂鬱症的假原因", "指紋": "x",
                                                                    "改範圍": {"名稱": "王大明", "改成": [76.5, 80.0]}}},
                                     "未登記確認": {"120.00": {"結果": "沒問題", "原因": "假名字林小花"}},
                                     "整片退回": [{"id": "R001", "成品秒": 10.0, "原片秒": 10.0, "覆核項目": [], "原因": "憂鬱症的假原因"}],
                                     "看過區段": [[0, 10]]})
    _w(w / "輸出" / "剪輯決策_試看.json", {"範圍": [0.0, 180.0], "刪除": [[70.0, 80.0]], "停格": [{"at": 70.0, "dur": 1.5, "原因": "王大明"}],
                                       "動作": [{"類型": "學員重念", "id": "T003_01", "start": 40.0, "end": 44.0, "text": "秘密逐字稿甲乙丙",
                                               "學員": "王大明", "新欄位": "新欄位的祕密", "要人聽": False}],
                                       "標記": [{"類型": "重疊", "id": "O69.60", "start": 69.6, "end": 70.1, "做法": "只留老師", "處理": "假名字林小花"}],
                                       "警告": ["T003_02：段落 T003 現在是王大明，舊的重念不用", "假警告祕密"], "片段": PLIST})
    _W = w
    return w


def _all_output(w: Path) -> str:
    chunks = []
    for topic in safeview.TOPICS:
        argv = [str(w), topic]
        if topic == "換算":
            argv += ["--原片", "60", "--原片", "1:30", "--成品", "100"]
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = safeview.main(argv)
        assert rc == 0, (topic, buf.getvalue()[:200])
        chunks.append(f"### {topic}\n{buf.getvalue()}")
    for extra in (["生成", "--who", "老師"], ["成品檢查", "--要人聽"], ["段落", "--from", "0:40", "--to", "1:10"],
                  ["名字", "--id", "1"], ["剪輯決策", "--tag", "試看"], ["字", "--from", "0", "--to", "5"], ["不存在的主題"]):
        buf = io.StringIO()
        with redirect_stdout(buf):
            safeview.main([str(w), *extra])
        chunks.append(buf.getvalue())
    return "\n".join(chunks)


def test_no_secret_in_any_topic():
    out = _all_output(make())
    for s in SECRETS:
        assert s not in out, f"輸出裡出現了「{s}」"
    # 該印的有印：編號、時間、狀態、數字
    for want in ("T003", "0:01:16.0", "退回重做", "標紅", "種子=42", "第幾版=2", "<文字", "墊底噪", "說話者=<文字 3 字>"):
        assert want in out, want


def test_new_unknown_field_is_masked():
    w = make()
    t = _r(w / "校對" / "段落.json")
    t["段落"][0]["今天才加的欄位"] = "假名字林小花今天才加"
    t["段落"][0]["今天才加的清單"] = ["假名字林小花", "王大明"]
    t["段落"][0]["今天才加的物件"] = {"王大明": "假名字林小花"}
    _w(w / "校對" / "段落.json", t)
    buf = io.StringIO()
    with redirect_stdout(buf):
        safeview.main([str(w), "段落"])
    out = buf.getvalue()
    assert "今天才加的欄位=<文字 10 字>" in out
    assert "今天才加的清單=<清單 2 筆>" in out and "今天才加的物件=<1 個欄位>" in out
    assert "假名字林小花" not in out and "王大明" not in out


def test_val_whitelist():
    assert safeview.val("狀態", "通過") == "通過"
    assert safeview.val("狀態", "王大明") == "<文字 3 字>"            # 白名單欄位、不認得的值 → 遮掉
    assert safeview.val("說話者", "學員3") == "學員3"
    assert safeview.val("說話者", "王大明") == "<文字 3 字>"
    assert safeview.val("id", "T003m1_2") == "T003m1_2"
    assert safeview.val("鍵", "學員重念@10.00") == "學員重念@10.00"
    assert safeview.val("鍵", "王大明@10.00") == "<文字 9 字>"
    assert safeview.val("覆核項目", ["名字:3", "王大明:3"]) == "[名字:3, <文字 5 字>]"
    assert safeview.val("start", 2579.8) == "0:42:59.8"
    assert safeview.val("原片", [2579.8, 2587.4]) == "0:42:59.8–0:43:07.4"
    assert safeview.val("校對稿", "") == "<空>"
    assert safeview.val("聲線", "女3") == "女3"
    assert safeview.val("更新時間", "2026-10-02T18:00:00") == "2026-10-02T18:00:00"
    assert safeview.val("更新時間", "王大明") == "<文字 3 字>"
    # 10-03 第八批補修 #102：學員段落空隙的新類型、欄位印得出來
    assert safeview.val("類型", "學員空隙消音") == "學員空隙消音" and safeview.val("類型", "學員空隙保留原聲") == "學員空隙保留原聲"
    assert safeview.val("鍵", "學員空隙消音@51.60") == "學員空隙消音@51.60"
    assert safeview.val("id", "空隙:T003_01") == "空隙:T003_01" and safeview.val("前一格", "T003_01") == "T003_01"
    assert safeview.val("保留原因", "老師的話") == "老師的話" and safeview.val("保留原因", "王大明") == "<文字 3 字>"
    assert safeview.val("空隙秒", 0.4) == "0.4"


def test_convert_matches_render_with_freeze():
    m = {"片段": PLIST, "範圍": [0, 180]}
    for t in (10.0, 55.0, 69.9, 85.0, 150.0):
        assert timemap.to_output(t, m) == render.to_output_time(t, PLIST)
    assert timemap.to_output(75.0, m) is None                      # 剪掉的地方
    assert abs(timemap.to_output(85.0, m) - (50 + 20 + 1.5 + 5)) < 1e-9   # 停格要算進去（只扣剪掉的會少 1.5 秒）
    assert abs(timemap.to_source(76.5, m) - 85.0) < 1e-9
    assert timemap.to_output(42.0, {"片段": None}) == 42.0        # 只換聲音：時間不變
    assert timemap.both(85.0, 86.0, m) == "原片 0:01:25.0–0:01:26.0／成品 0:01:16.5–0:01:17.5"
    assert timemap.both(85.0, 86.0, None) == "原片 0:01:25.0–0:01:26.0"
    buf = io.StringIO()
    with redirect_stdout(buf):
        safeview.main([str(make()), "換算", "--原片", "1:25", "--成品", "1:16.5"])
    assert "原片 0:01:25.0 → 成品 0:01:16.5" in buf.getvalue() and "成品 0:01:16.5 → 原片 0:01:25.0" in buf.getvalue()


def test_inspect_does_not_write():
    w = make()
    before = {p: p.stat().st_mtime_ns for p in w.rglob("*") if p.is_file()}
    _all_output(w)
    after = {p: p.stat().st_mtime_ns for p in w.rglob("*") if p.is_file()}
    assert before == after, "查詢改到了檔案：" + str([str(p.relative_to(w)) for p in after if before.get(p) != after[p]][:5])
    assert wd.write_json.__name__ == "write_json"                # 跑完換回來


def test_cli_entry():
    from bookclub import cli

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(["inspect", str(make()), "成品檢查"])
    assert rc == 0 and "逐筆 通過 0／退回 1／共 2" in buf.getvalue()
    assert "憂鬱症的假原因" not in buf.getvalue()


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
