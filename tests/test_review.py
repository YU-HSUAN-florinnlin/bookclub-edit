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

# 名冊、敏感詞用暫存資料夾的假資料，不讀真的 ~/讀書會剪輯資料/
import os  # noqa: E402

_DATA = Path(tempfile.mkdtemp())
(_DATA / "名冊.csv").write_text("中文名,其他寫法,英文代號,聲線,性別\n小美,,Amy,,女\n阿明,,Tom,,男\n", encoding="utf-8")
os.environ["BOOKCLUB_DATA_DIR"] = str(_DATA)

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
    assert p["各類"]["刪除段落"] == {"已確認": 1, "總數": 3}          # 手動 1 筆＋建議 2 筆（還沒決定）


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
    stu2 = next(x for x in review.page_data(w)["項目"] if x["id"] == "T005")
    assert "Amy" in stu2["建議稿"] and stu2["換過的字"][0]["原字"] == "小美"      # 學員說到的本名自動換成代號
    from bookclub import turns
    turns.save_turn(w, "T005", {"校對稿": stu2["建議稿"], "已確認": True})     # 覆核時按「通過」
    r = exchange.export_review(w, out=w.parent / "匯出.zip")
    assert r["未確認數"] == 6 and "ref.wav" in r["內含"]      # 2 學員段落＋名字 1＋重疊 1＋建議刪除 2
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


def test_export_lists_places_with_real_names():
    from bookclub import exchange

    w = _fresh()
    orig = review.roster_words
    review.roster_words = lambda: ["學員1分享"]          # 假的「本名」：假資料學員 1 的句子都有這幾個字
    try:
        d = review.page_data(w)
        assert [x["含本名"] for x in d["項目"] if x["類型"] == "學員段落"] == [True, False, False]
        r = exchange.export_review(w, out=w.parent / "匯出.zip")
        assert r["還有本名的地方"][:1] == ["學員段落 T003"]
    finally:
        review.roster_words = orig


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


def test_suggest_overlap_rules():
    turns = [{"start": 0, "end": 30, "說話者": "老師"}, {"start": 30.2, "end": 50, "說話者": "學員2"},
             {"start": 50.3, "end": 90, "說話者": "老師"}]
    f = review.suggest_overlap
    assert f({"start": 10, "end": 10.4}, turns, None, {})["做法"] == "只留老師原聲學員消音"        # 老師講話中間附和
    r = f({"start": 29.5, "end": 30.3}, turns, "學員2", {})
    assert r["做法"] == "兩邊都重生成" and r["排法"] == "前後排開" and "老師收尾" in r["原因"]      # 交接
    assert "學員收尾" in f({"start": 49.8, "end": 50.5}, turns, "學員2", {})["原因"]
    assert f({"start": 29.5, "end": 30.3}, turns, "學員2", {"學員2": "保留原聲"})["做法"] == "不用改"
    assert f({"start": 40, "end": 40.5}, turns, "學員2", {})["做法"] == "只留學員"                  # 學員說話中老師回應
    assert f({"start": 95, "end": 95.5}, turns, None, {})["做法"] == "兩邊都重生成"                  # 判斷不出來


def test_replace_real_names():
    table = [{"寫法": "小美", "代號": "Amy"}, {"寫法": "美", "代號": "X"}, {"寫法": "王小美", "代號": "Amy"},
             {"寫法": "公司名", "代號": "某公司"}]
    text, ch = review.replace_real_names("我是王小美，在公司名上班，小美說", table)
    assert text == "我是Amy，在某公司上班，Amy說"                  # 長的先換、單字不換
    assert [c["原字"] for c in ch] == ["王小美", "公司名", "小美"] and text[ch[0]["位置"]:].startswith("Amy")


def test_prep_and_cut_suggestions_mark_items_not_needed():
    w = _fresh()
    d = review.page_data(w)
    assert d["開始前確認"] == {"刪除": False, "學員": False, "名字": False, "保留原聲": False}
    assert [x["id"] for x in d["刪除建議"]] == ["S1", "S2"]
    try:
        review.set_prep(w, "學員", True)             # 09-30：學員還沒選本名不能標完成
        raise AssertionError
    except ValueError as e:
        assert "還沒選本名" in str(e)
    review.decide_cut_suggestion(w, "S2", "刪除")
    review.decide_cut_suggestion(w, "S1", "不刪")
    dec = review.load_decisions(w)
    assert [c["建議id"] for c in dec["刪除段落"]] == ["S2"] and dec["刪除建議"]["S1"]["決定"] == "不刪"
    d = review.page_data(w)
    cuts = [x for x in d["項目"] if x["類型"] == "刪除段落"]
    assert len(cuts) == 2 and all(x["已確認"] for x in cuts)                   # 建議那一筆就代表刪除段落，不重複列
    review.decide_cut_suggestion(w, "S2", "不刪")
    assert review.load_decisions(w)["刪除段落"][0]["狀態"] == "還原"
    # 刪除 40–72 秒：學員1 的段落、69.6 秒的重疊都落在裡面 → 不用處理
    review.save_cut(w, {"start": 39.9, "end": 72.0})
    review.set_voice(w, "學員2", "保留原聲")
    d = review.page_data(w)
    skip = {x["id"]: x["不用處理"] for x in d["項目"] if x.get("不用處理")}
    assert skip.get("T003") == "落在確認刪除的段落裡" and "O69.60" in skip and "保留原聲" in skip.get("T005", "")
    assert all(x.get("建議") and x["建議"].get("原因") for x in d["項目"])       # 每一筆一開始就有建議＋原因
    stu = next(x for x in d["項目"] if x["類型"] == "學員段落")
    assert stu["建議稿"] == stu["校對稿"]


def test_merge_split_and_mark_student():
    from bookclub import turns

    w = _fresh()
    turns.merge_person(w, "學員2", "學員1")
    data = json.loads((w / "校對" / "段落.json").read_text(encoding="utf-8"))
    assert set(data["學員"]) == {"學員1"} and data["學員"]["學員1"]["段數"] == 3
    r = turns.reassign_turns(w, ["T007"], "新學員")
    assert r["說話者"] == "學員2"
    data = json.loads((w / "校對" / "段落.json").read_text(encoding="utf-8"))
    assert {t["id"]: t["說話者"] for t in data["段落"]}["T007"] == "學員2" and data["學員"]["學員2"]["段數"] == 1
    # 藏在老師段落（160–180）裡的學員發言：164–168 秒
    r = turns.mark_student(w, 163.9, 167.7, "新學員")
    data = json.loads((w / "校對" / "段落.json").read_text(encoding="utf-8"))
    ts = data["段落"]
    new = next(t for t in ts if t["id"] in r["段落"])
    assert new["說話者"] == "學員3" and new["start"] == 164.0 and new["end"] == 167.6 and new["原文"].startswith("這是藏在")
    assert [t["start"] for t in ts] == sorted(t["start"] for t in ts) and len({t["id"] for t in ts}) == len(ts)
    teacher = [t for t in ts if t["說話者"] == "老師" and t["start"] >= 160]
    assert len(teacher) == 2 and teacher[1]["id"] != "T008"                     # 老師段落在前後切開
    # 標的範圍裡一句都沒有（2 秒的漏抓）→ 手動標記段落
    r = turns.mark_student(w, 103.0, 104.5, "學員2")
    data = json.loads((w / "校對" / "段落.json").read_text(encoding="utf-8"))
    man = next(t for t in data["段落"] if t["id"] == r["段落"][0])
    assert man["手動標記"] and man["句子"] == [] and man["start"] == 103.0
    try:
        turns.merge_person(w, "學員1", "學員1")
        raise AssertionError
    except ValueError:
        pass


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


def test_minor_student_and_filler_overlap_rules():
    """09-29 宇軒：不重要的短句（< 3 秒或只有招呼附和笑聲）預設建議刪除；重疊學員只是附和 → 只留老師原聲。"""
    assert review.is_filler("謝謝老師～") and review.is_filler("呵呵呵") and review.is_filler("好，對。")
    assert not review.is_filler("我覺得很好") and not review.is_filler("")
    assert review.is_minor_student("今天分享很多東西", 2.0) == "只有 2.0 秒"
    assert review.is_minor_student("老師再見", 5.0) == "只有招呼、附和或笑聲"
    assert review.is_minor_student("我覺得今天很有收穫", 6.0) is None
    s = review.suggest_overlap({"start": 1.0, "end": 1.3}, [], None, {}, "嗯")
    assert s["做法"] == "只留老師原聲學員消音"
    s = review.suggest_overlap({"start": 1.0, "end": 1.3}, [], None, {}, "我想問一下這個練習")
    assert s["做法"] == "兩邊都重生成"


def test_name_decisions_follow_candidate_content():
    # 09-29 檢查 #6：名字候選整份重算、順序變了，覆核決定跟著內容走，不照編號錯位
    from bookclub import workdir as wd

    w = _fresh()
    data = wd.read_json(wd.names_path(w))
    assert len(data["candidates"]) >= 2
    review.save_name(w, "1", {"tags": ["不是名字"]})
    dp = review.name_decisions_path(w)
    fp1 = wd.read_json(dp)["1"]["候選指紋"]
    data["candidates"] = list(reversed(data["candidates"]))      # 重算後順序變了
    wd.write_json(wd.names_path(w), data)
    assert review.anchor_name_decisions(w)["搬動"] == 1
    dec = wd.read_json(dp)
    k = str(len(data["candidates"]))
    assert dec[k]["候選指紋"] == fp1 and "不是名字" in dec[k]["tags"] and "1" not in dec
    data["candidates"] = data["candidates"][:-1]                  # 那一筆重算後不見了：收起來、不套到別筆
    wd.write_json(wd.names_path(w), data)
    assert review.anchor_name_decisions(w)["找不到"] == 1
    dec = wd.read_json(dp)
    assert fp1 in dec["_找不到的候選"] and k not in dec


def test_not_name_toggle_updates_exclusion_list():
    # 09-30：名字卡「不是名字，不用改」取消時，也要從全域排除清單拿掉（以前只加不拿）
    from bookclub import names
    from bookclub import workdir as wd

    w = _fresh()
    ex = _DATA / "名字排除清單.csv"
    term = wd.read_json(wd.names_path(w))["candidates"][0]["matched_text"]
    ex.write_text("詞,原因,建立日期\n別的詞,不是名字,2026-09-01\n", encoding="utf-8")
    r = review.save_name(w, "1", {"tags": ["不是名字"], "已確認": True})
    assert r["已加入排除清單"] and r["決定"]["排除的詞"] == term
    assert [x["詞"] for x in names.load_exclusion_list(ex)] == ["別的詞", term]
    r = review.save_name(w, "1", {"tags": [], "已確認": False})
    assert r["已從排除清單拿掉"] and "排除的詞" not in r["決定"]
    assert [x["詞"] for x in names.load_exclusion_list(ex)] == ["別的詞"]          # 別人加的不動
    assert review.save_name(w, "1", {"tags": []})["已從排除清單拿掉"] is False     # 本來就沒標：不動清單
    ex.unlink()


def test_prep_items_block_and_auto_revert():
    # 09-30：開始前 4 件事——底下有沒處理的不能標完成；標完成後冒出新項目自動改回還沒做
    from bookclub import turns

    w = _fresh()
    d = review.page_data(w)
    live = [n for n, p in d["學員"].items() if not p.get("都會刪掉")]
    assert "學員" in d["開始前待處理"] and "刪除" in d["開始前待處理"]
    # ① 兩筆建議都選了 → 可以標完成
    review.decide_cut_suggestion(w, "S1", "不刪")
    review.decide_cut_suggestion(w, "S2", "不刪")
    review.set_prep(w, "刪除", True)
    # ② 每位選本名、代號
    reals = ["小美", "阿明"] + [f"路人{i}" for i in range(len(live))]
    for n, real in zip(live, reals):
        turns.set_real_name(w, n, real)
        turns.set_name_code(w, real, {"小美": "Amy", "阿明": "Tom"}.get(real, f"Code{real[-1]}"))
    review.set_prep(w, "學員", True)
    # ④ 標完成時記下每位學員目前的選擇
    review.set_prep(w, "保留原聲", True)
    dec = review.load_decisions(w)
    assert set(dec["學員聲音"]) >= set(live) and dec["開始前確認"]["保留原聲"]
    # 拆出一位新學員 → ② 沒本名、④ 沒選 → 兩件都自動改回還沒做
    first = next(x for x in d["項目"] if x["類型"] == "學員段落" and x["說話者"] == live[0])
    turns.reassign_turns(w, [first["id"]], "新學員")
    d = review.page_data(w)
    assert set(d["開始前自動改回"]) == {"學員", "保留原聲"}
    assert not d["開始前確認"]["學員"] and not d["開始前確認"]["保留原聲"] and d["開始前確認"]["刪除"]
    assert review.page_data(w)["開始前自動改回"] == []              # 改回一次就好，不會每次都報
    try:
        review.set_prep(w, "保留原聲", True)      # ④ 沒有卡：新學員預設重新生成，標完成時寫下來
    except ValueError:
        raise AssertionError("④ 不該被擋")


def test_code_changed_flag_shown_until_passed():
    # 09-30：epcodes.propagate 改回還沒確認的段落帶「代號改過」，卡片提醒；人按通過就拿掉
    from bookclub import turns
    from bookclub import workdir as wd

    w = _fresh()
    tp = turns.turns_path(w)
    data = wd.read_json(tp)
    tid = next(t["id"] for t in data["段落"] if t["說話者"] != "老師")
    t = next(t for t in data["段落"] if t["id"] == tid)
    t["代號改過"] = "Grace"
    t["已確認"] = False
    wd.write_json(tp, data)
    it = next(x for x in review.page_data(w)["項目"] if x["類型"] == "學員段落" and x["id"] == tid)
    assert it["代號改過"] == "Grace"
    turns.save_turn(w, tid, {"已確認": True})
    it = next(x for x in review.page_data(w)["項目"] if x["類型"] == "學員段落" and x["id"] == tid)
    assert not it["代號改過"]


if __name__ == "__main__":
    sys.exit(_run_all())
