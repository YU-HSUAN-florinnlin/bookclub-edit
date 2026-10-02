"""bookclub/silentedge.py（老師重念範圍前後沒有人講話，10-02 第六批第二件）＋第三、四件的小測試。

- 純函式：哪一邊沒有字、建議範圍、門檻
- 假工作區：名字整句換掉、人工標的老師整段各一筆前後沒有字 → 第 3 步卡片、第 4 步總檢查「請看一眼」、第 5 步那一筆都看得到；
  「照建議縮小」走原本改範圍的路；縮小後留下的原聲有名字候選時擋下；不會自動改任何範圍
- 第三件：第 5 步「只看要人聽的」、處理標記同時列原片與成品
- 第四件：執行訊息不出現「種子」
獨立可跑：.venv/bin/python tests/test_silentedge.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_workdir  # noqa: E402

from bookclub import execute, finalcheck, render, review, silentedge, tts  # noqa: E402

_BASE = None


def _r(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _w(p: Path, d) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def fresh(drop: list[tuple[float, float]] = ()) -> Path:
    """假工作區（同一支測試共用影片，每次複製一份新的工作區），拿掉某幾段時間的字。"""
    import shutil

    global _BASE
    if _BASE is None:
        _BASE = fake_workdir.make(tempfile.mkdtemp())
    w = Path(tempfile.mkdtemp()) / "工作區"
    shutil.copytree(_BASE, w)
    m = _r(w / "transcript" / "merged.json")
    m["words"] = [x for x in m["words"] if not any(a <= x["start"] < b for a, b in drop)]
    _w(w / "transcript" / "merged.json", m)
    return w


# ---------- 純函式 ----------

def test_edges_pure():
    words = [{"start": 2581.5, "end": 2581.8}, {"start": 2584.0, "end": 2586.7}]
    e = silentedge.edges(2579.8, 2587.4, words)     # 第一堂那一筆：前面 1.7 秒沒有字
    assert e["前"] == 1.7 and e["後"] == 0.0 and e["建議"] == [2581.35, 2587.4]
    assert "這個範圍前面有 1.7 秒沒有人講話，建議縮成 0:43:01." in silentedge.describe(e)
    assert silentedge.describe(e).endswith("–0:43:07.4")
    assert silentedge.edges(0, 10, [{"start": 0.5, "end": 9.4}]) is None              # 兩邊都在門檻內
    assert silentedge.edges(0, 10, []) is None                                        # 整段沒有字：不是這個提醒
    e = silentedge.edges(0, 10, [{"start": 0.5, "end": 8.0}])
    assert e["前"] == 0.0 and e["後"] == 2.0 and e["建議"] == [0, 8.15]
    assert silentedge.edges(0, 10, [{"start": 0.9, "end": 9.0}], min_s=silentedge.EDGE_SILENT_S)["前"] == 0.9


def test_route():
    assert silentedge._route({"候選": [3]}, {"3": {}})[1] == silentedge.HOW_RANGE
    assert silentedge._route({"候選": ["NM001"]}, {"NM001": {"老師整段": True}})[1] == silentedge.HOW_WHOLE
    assert silentedge._route({"候選": [1, 2]}, {})[1] is None
    assert silentedge._route({"候選": [], "重疊項目": ["O1.00"], "疊放": True}, {})[:3] == ("重疊:O1.00", "老師起訖", "O1.00")
    k, how, _, why = silentedge._route({"id": "V0000100", "候選": [], "重疊項目": ["O1.00"]}, {})
    assert k == "重疊:O1.00" and how is None and "生成老師聲音" in why


# ---------- 假工作區：名字整句換掉 ----------

def test_name_range_hint_and_shrink():
    # 名字 1（76.3 秒，句子 76.0–79.6 以逗號結尾，接下一句到 83.6）：把第二句的字拿掉 → 後面 4 秒沒有字
    w = fresh([(80.0, 84.0)])
    h = silentedge.hints(w)
    assert list(h) == ["名字:1"], h.keys()
    x = h["名字:1"]
    assert x["範圍"] == [76.0, 83.6] and x["前"] == 0.0 and x["後"] > 3.9 and x["可以縮"] and x["改法"] == "重念範圍"
    # 第 3 步卡片
    items = {f"{it['類型']}:{it['id']}": it for it in review.page_data(w)["項目"]}
    assert items["名字:1"]["前後沒聲音"]["建議"] == x["建議"]
    # 第 4 步總檢查「請看一眼」
    fc = execute.final_check(w)
    rows = [r for r in fc["請看一眼"] if r["key"].startswith("前後沒聲音:")]
    assert len(rows) == 1 and rows[0]["縮小"]["鍵"] == "名字:1" and "建議縮成" in rows[0]["說明"]
    # 沒有自動改：名字決定裡沒有重念範圍
    assert "整句起訖" not in (_r(w / "名字覆核決定.json") if (w / "名字覆核決定.json").is_file() else {}).get("1", {})
    # 照建議縮小 → 存到名字卡片的重念範圍（第 3 步同一條路）
    res = silentedge.apply(w, "名字:1")
    assert res["改成"] == x["建議"]
    assert _r(w / "名字覆核決定.json")["1"]["整句起訖"] == x["建議"]
    assert silentedge.hints(w) == {}                    # 縮過了就不再提醒
    try:
        silentedge.apply(w, "名字:1")
        raise AssertionError("縮過了還能再縮")
    except ValueError:
        pass


def test_teacher_whole_hint_and_shrink_no_snap():
    # 人工標的老師整段 160.0–167.5，前 2 秒的字拿掉
    w = fresh([(160.0, 162.0)])
    dec = review.load_decisions(w)
    dec["人工名字"].append({"id": "NM001", "start": 160.0, "end": 167.5, "代號": "", "matched_text": "", "sentence_id": None,
                          "sentence": "", "老師整段": True, "整段文字": "老師這一段", "來源": "人工新增"})
    _w(review.review_path(w), dec)
    h = silentedge.hints(w)["名字:NM001"]
    assert h["改法"] == "老師整段" and h["前"] >= 1.9 and h["可以縮"]
    silentedge.apply(w, "名字:NM001")
    nm = review.load_decisions(w)["人工名字"][0]
    assert [nm["start"], nm["end"]] == h["建議"]       # 照填的時間，沒有被對齊縮回句子開頭（160.0）
    assert nm["對齊到"] == "照填的時間"


def test_block_when_name_left_in_original_sound():
    # 老師整段 118.5–126.0，前面的字拿掉；名字 2（阿明 120.0–120.6）落在縮掉的那幾秒 → 不給縮
    w = fresh([(118.0, 121.0)])
    dec = review.load_decisions(w)
    dec["人工名字"].append({"id": "NM001", "start": 118.5, "end": 126.0, "代號": "", "matched_text": "", "sentence_id": None,
                          "sentence": "", "老師整段": True, "整段文字": "老師這一段", "來源": "人工新增"})
    _w(review.review_path(w), dec)
    h = silentedge.hints(w)["名字:NM001"]
    assert not h["可以縮"] and h["名字擋下"] == 1 and "名字" in h["原因"]
    try:
        silentedge.apply(w, "名字:NM001")
        raise AssertionError("名字會留在成品裡，應該擋下")
    except ValueError:
        pass
    assert review.load_decisions(w)["人工名字"][0]["start"] == 118.5   # 沒改
    _w(w / "名字覆核決定.json", {"2": {"做法": "直接消音", "tags": []}})   # 那個名字本來就另外消音：不擋
    assert silentedge.hints(w)["名字:NM001"]["可以縮"]


def test_step5_card_and_shrink_marks_redo():
    w = fresh([(80.0, 84.0)])
    _w(w / "生成" / "處理紀錄.json", {
        "版本": 1, "來源": "render video 試看", "範圍": [0.0, 180.0], "產生時間": "2026-10-02T18:00:00",
        "片段": [{"src": [0.0, 50.0], "freeze": 0.0}, {"src": [60.0, 180.0], "freeze": 1.0}],
        "紀錄": [{"編號": 1, "類型": "名字整句換掉", "原片": [76.0, 83.6], "成品": [66.0, 73.6], "做了什麼": "x",
                 "覆核項目": ["名字:1"], "要人聽": True, "動到聲音": True},
                {"編號": 2, "類型": "刪除", "原片": [50.0, 60.0], "成品": [50.0, 50.0], "覆核項目": [], "要人聽": False, "動到聲音": True}],
        "未登記的變動": []})
    d = finalcheck.page_data(w)
    rec = {r["鍵"]: r for r in d["紀錄"]}
    assert rec["名字整句換掉@76.00"]["前後沒聲音"]["鍵"] == "名字:1"
    assert rec["名字整句換掉@76.00"]["要人看"] and not rec["刪除@50.00"]["要人看"]   # 「只看要人聽的」
    silentedge.apply(w, "名字:1", "名字整句換掉@76.00")
    chk = finalcheck.load_check(w)["逐筆"]["名字整句換掉@76.00"]
    assert chk["結果"] == finalcheck.REDO and chk["改範圍"]["改成"][1] < 80.0 and chk["改範圍"]["第3步"] == "名字:1"
    # 第 4 步總檢查的列附上成品時間（停格也算進去）
    fc = execute.final_check(w)
    r = next(x for x in fc["請看一眼"] if x["key"] == "聲紋:段落是老師")
    assert r["成品起訖"][0] == r["start"]                     # 剪掉的地方（50–60 秒）之前：時間一樣
    assert all("成品起訖" not in x for x in fc["一定要處理"] if 50.0 < x["start"] and x["end"] < 60.0)


def test_marks_list_both_times():
    rows = [{"類型": "學員重念", "原片": [2579.8, 2587.4], "成品": [2306.4, 2314.0], "做了什麼": "x", "要人聽": True}]
    md = render.marks_md(rows, "全片", [])
    assert "原片 0:42:59.8–0:43:07.4／成品 0:38:26.4–0:38:34.0" in md
    html = render.marks_html(rows, "全片", [])
    assert html.index("原片時間") < html.index("成品時間") and "0:42:59.8" in html and "0:38:26.4" in html


def test_attempt_line_plain_words():
    line = tts.attempt_line(2, 1, 1.0, 2.9, 41.0, 0.72, False, 7.6, 0.8)
    assert "種子" not in line and "第 2 種念法" in line and "正常速度" in line
    assert "念的字對了 72%（不到 85%，換一種念法再試）" in line and "比原本的時間短 62%" in line
    line = tts.attempt_line(3, 1, 1.15, 6.0, 30.0, 0.95, True, 5.0, None)
    assert "念快一點（1.15 倍）" in line and "比原本的時間長 20%" in line
    assert [tts.way_number(s) for s in (42, 1, 2026, 2027, 2028)] == [1, 2, 3, 4, 5]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
