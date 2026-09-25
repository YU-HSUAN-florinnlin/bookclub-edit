"""bookclub/review.py（覆核工作台）與 bookclub/exchange.py（匯出／匯入）的測試：用 tests/fake_workdir.py
的合成資料（電子音、假逐字稿），不碰真的影片、不呼叫 Claude／Groq。

獨立可跑：.venv/bin/python tests/test_review.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import fake_workdir  # noqa: E402

from bookclub import review  # noqa: E402

_ROOT = None


def _fresh() -> Path:
    """每個測試一份新的假工作區（影片只做一次，複製比較快）。"""
    import shutil

    global _ROOT
    if _ROOT is None:
        _ROOT = Path(tempfile.mkdtemp()) / "base"
        fake_workdir.make(_ROOT)
    d = Path(tempfile.mkdtemp())
    shutil.copytree(_ROOT, d / "base")
    w = d / "base" / "工作區"
    # 影片路徑指到複製過去的那一份
    for name in ("分析結果.json",):
        data = json.loads((w / name).read_text(encoding="utf-8"))
        data["video"] = str(d / "base" / "假影片.mp4")
        (w / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return w


def test_parse_time():
    assert review.parse_time("43:15") == 2595 and review.parse_time("1:05:00") == 3900
    assert review.parse_time("95.5") == 95.5 and review.parse_time("1：00") == 60
    for bad in ("", "a:b", "1:2:3:4", "1::2"):
        try:
            review.parse_time(bad)
            raise AssertionError(bad)
        except ValueError:
            pass


def test_page_data_items_bands_and_skips():
    w = _fresh()
    d = review.page_data(w)
    kinds = [x["類型"] for x in d["項目"]]
    assert kinds.count("學員段落") == 3 and kinds.count("名字") == 2 and kinds.count("重疊") == 1
    assert [x["start"] for x in d["項目"]] == sorted(x["start"] for x in d["項目"])        # 照時間排
    assert {o["原因"] for o in d["已自動跳過的重疊"]} == {"兩位學員之間的重疊", "重疊不到 0.05 秒（邊界誤差）"}
    assert any(b["冥想導讀"] for b in d["色帶"]) and d["影片"]["有影片"]
    name1 = next(x for x in d["項目"] if x["類型"] == "名字")
    assert name1["做法"] == "整句換掉"                                                     # 預設整句
    assert name1["整句"]["換成代號"] == "剛剛Amy分享得很好，我們再多聽一點。"            # 半句擴成完整句
    ov = next(x for x in d["項目"] if x["類型"] == "重疊")
    assert ov["學員說話者"] == "學員1" and ov["做法"] is None
    assert json.loads((w / "重疊.json").read_text(encoding="utf-8"))["已自動跳過數"] == 1   # 過濾只在記憶體


def test_save_name_overlap_voice_time():
    w = _fresh()
    review.save_name(w, "1", {"做法": "直接消音", "tags": ["切點削到旁邊的字", "亂寫的"], "note": "x", "已確認": True})
    dec = json.loads((w / "名字覆核決定.json").read_text(encoding="utf-8"))["1"]
    assert dec["做法"] == "直接消音" and dec["tags"] == ["切點削到旁邊的字"] and dec["已確認"]
    try:
        review.save_name(w, "1", {"做法": "亂選"})
        raise AssertionError
    except ValueError:
        pass
    try:
        review.save_overlap(w, "O69.60", {"已確認": True})          # 沒選做法不能確認
        raise AssertionError
    except ValueError:
        pass
    review.save_overlap(w, "O69.60", {"做法": "兩邊都重生成", "排法": "照原位置疊著", "學員文字": "改過", "已確認": True})
    review.save_overlap(w, "O150.20", {"救回": True})
    d = review.page_data(w)
    ovs = {x["id"]: x for x in d["項目"] if x["類型"] == "重疊"}
    assert ovs["O69.60"]["排法"] == "照原位置疊著" and ovs["O69.60"]["學員文字"] == "改過" and "O150.20" in ovs
    review.set_voice(w, "學員1", "保留原聲")
    assert review.page_data(w)["學員"]["學員1"]["聲音"] == "保留原聲"
    review.set_voice(w, "全部", "重新生成")
    assert set(review.load_decisions(w)["學員聲音"].values()) == {"重新生成"}
    review.add_time(w, 30)
    review.add_time(w, 99999)
    assert review.load_decisions(w)["覆核秒數"] == 30 + review.MAX_TIME_STEP_S


def test_cut_snaps_to_quiet_and_mute():
    w = _fresh()
    r = review.save_cut(w, {"start": 31.3, "end": 35.9})["項目"]
    assert r["id"] == "D001" and r["狀態"] == "刪除" and abs(r["start"] - 31.6) < 0.05 and r["剪點對齊安靜處"][0]
    review.save_cut(w, {"id": "D001", "狀態": "還原"})
    assert review.load_decisions(w)["刪除段落"][0]["狀態"] == "還原"
    try:
        review.save_cut(w, {"start": 5, "end": 4})
        raise AssertionError
    except ValueError:
        pass
    m = review.save_mute(w, {"start": 44.5, "end": 46.5})["項目"]
    assert m["方式"] == "墊底噪" and m["start"] == 44.5                                    # 消音不對齊
    review.save_mute(w, {"id": m["id"], "方式": "霧化"})
    assert review.load_decisions(w)["局部消音"][0]["方式"] == "霧化"
    p = review.page_data(w)["進度"]
    assert p["各類"]["刪除段落"] == {"已確認": 1, "總數": 1}


def test_progress_estimate():
    items = [{"類型": "a", "已確認": True}, {"類型": "a", "已確認": False}, {"類型": "b", "已確認": False}]
    p = review.progress(items, {"覆核秒數": 120}, 1000)
    assert p["已確認"] == 1 and p["總數"] == 3 and p["推算全部秒數"] == 360


def test_export_import_roundtrip_same_name_plan():
    import zipfile

    from bookclub import exchange, nameplan

    w = _fresh()
    review.save_name(w, "1", {"做法": "整句換掉", "已確認": True})
    review.save_name(w, "2", {"做法": "直接消音"})
    review.save_cut(w, {"start": 31.3, "end": 35.9})
    r = exchange.export_review(w, out=w.parent / "匯出.zip")
    assert r["未確認數"] == 5 and "ref.wav" in r["內含"]      # 3 學員段落＋名字 2＋重疊 1，名字 1 已確認
    with zipfile.ZipFile(r["檔案"]) as z:
        res = json.loads(z.read("覆核結果.json").decode("utf-8"))
        text = z.read("覆核結果.json").decode("utf-8")
        assert "給夥伴的說明.txt" in z.namelist()
    assert "小美" not in text and "阿明" not in text and "原文" not in text     # 不帶本名、不帶原始逐字稿
    assert res["影片"]["長度"] == 180.0 and res["名字處理"][0]["生成"]["文字"].startswith("剛剛Amy")
    assert res["刪除段落"][0]["id"] == "D001"

    src_plan = nameplan.make_plan(w)
    new = w.parent / "夥伴工作區"
    video = Path(json.loads((w / "分析結果.json").read_text(encoding="utf-8"))["video"])
    exchange.import_review(r["檔案"], new, video)
    assert (new / "audio.flac").exists() and (new / "參考音" / "ref.wav").exists()
    assert nameplan.make_plan(new) == src_plan
    assert nameplan.sentences_path(new).read_text(encoding="utf-8") == nameplan.sentences_path(w).read_text(encoding="utf-8")
    try:
        exchange.import_review(r["檔案"], new, video)                      # 不蓋掉已經匯入的工作區
        raise AssertionError
    except FileExistsError:
        pass


def test_import_rejects_wrong_video_length():
    import subprocess

    from bookclub import exchange

    w = _fresh()
    r = exchange.export_review(w, out=w.parent / "匯出.zip")
    short = w.parent / "短影片.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=5", str(short)], check=True)
    try:
        exchange.import_review(r["檔案"], w.parent / "新", short)
        raise AssertionError
    except ValueError as e:
        assert "長度對不上" in str(e)


def _run_all() -> int:
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, func in tests:
        try:
            func()
            print(f"✓ {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback

            traceback.print_exc()
            print(f"✗ {name}：{exc!r}")
    print(f"{len(tests) - failed}/{len(tests)} 通過")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
