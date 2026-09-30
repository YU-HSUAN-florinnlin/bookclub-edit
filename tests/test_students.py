"""bookclub/students.py 的單元測試：切段規則、範圍過濾、刪除範圍排除、男女聲判斷。不載入模型。

獨立可跑：.venv/bin/python tests/test_students.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from bookclub import students


def S(i: int, a: float, b: float, text: str) -> dict:
    return {"id": f"s{i}", "start": a, "end": b, "text": text}


def _spans(chunks):
    return [(c[0]["start"], c[-1]["end"]) for c in chunks]


def test_chunks_join_to_8_20_seconds_at_punctuation():
    sents = [S(i, i * 3.0, i * 3.0 + 2.9, "一句話，") for i in range(10)]   # 每句 3 秒、都以逗號結尾
    chunks = students.plan_chunks(sents)
    for a, b in _spans(chunks):
        assert 8 <= b - a <= 20, (a, b)
    assert sum(len(c) for c in chunks) == 10


def test_no_break_without_punctuation_until_max():
    sents = [S(i, i * 3.0, i * 3.0 + 2.9, "沒有標點") for i in range(10)]
    chunks = students.plan_chunks(sents)
    # 不能在句中斷：一直接到超過 20 秒才硬斷
    assert len(chunks[0]) >= 7


def test_long_gap_allows_break():
    sents = [S(0, 0, 4.9, "沒有標點"), S(1, 5, 9.9, "沒有標點"), S(2, 12, 16, "沒有標點"), S(3, 16.1, 20, "結尾。")]
    chunks = students.plan_chunks(sents)
    assert _spans(chunks)[0] == (0, 9.9)   # 9.9 秒之後停頓 2.1 秒，可以斷


def test_short_tail_merges_into_previous():
    sents = [S(0, 0, 9, "第一句。"), S(1, 9, 12, "尾巴。")]
    chunks = students.plan_chunks(sents)
    assert len(chunks) == 1 and _spans(chunks) == [(0, 12)]


def test_single_short_turn_kept():
    chunks = students.plan_chunks([S(0, 100, 107, "短短一句")])
    assert _spans(chunks) == [(100, 107)]


def test_cut_range_excluded_and_splits():
    sents = [S(i, i * 3.0, i * 3.0 + 2.9, "一句話。") for i in range(10)]
    chunks = students.plan_chunks(sents, cut_ranges=[(8.5, 15.5)])   # 蓋掉 s3、s4（中點 10.45、13.45）
    ids = [s["id"] for c in chunks for s in c]
    assert "s3" not in ids and "s4" not in ids
    # 刪除範圍前後不接在一起
    assert all(not ({"s2", "s5"} <= {s["id"] for s in c}) for c in chunks)


def test_clip_slot_stays_out_of_cut():
    assert students.clip_slot(2491.6, 2511.1, [(2328.9, 2491.8)]) == (2491.8, 2511.1)
    assert students.clip_slot(10, 20, [(19, 30)]) == (10, 19)
    assert students.clip_slot(10, 20, [(30, 40)]) == (10, 20)


def test_in_ranges_uses_midpoint():
    assert students.in_ranges(9, 11, [(10, 20)])
    assert not students.in_ranges(5, 11, [(10, 20)])


def test_in_window_filters_by_midpoint():
    sents = [S(0, 2210, 2225, "a"), S(1, 2215, 2228, "b"), S(2, 3320, 3330, "c"), S(3, 3318, 3322, "d")]
    kept = [s["id"] for s in students.in_window(sents, 2220, 3323)]
    assert kept == ["s1", "s3"]
    assert len(students.in_window(sents, None, None)) == 4


def test_split_edited_follows_proofread_text():
    # 09-29：重念照校對稿念；改過的字分回原本那一句，刪光的句子變空字串
    sents = [S(1, 0, 2, "我叫小美，"), S(2, 2, 4, "我在台北上班。"), S(3, 4, 6, "今天很開心。")]
    t = {"原文": "".join(s["text"] for s in sents), "校對稿": "我叫Amy，我在某公司上班。今天很開心！"}
    assert students.split_edited(t, sents) == {"s1": "我叫Amy，", "s2": "我在某公司上班。", "s3": "今天很開心！"}
    t["校對稿"] = "我叫小美，今天很開心。"
    assert students.split_edited(t, sents)["s2"] == ""


def test_split_edited_gives_up_when_text_not_found():
    sents = [S(1, 0, 2, "甲乙丙")]
    assert students.split_edited({"原文": "完全不同", "校對稿": "改過"}, sents) is None


def test_median_f0_male_female():
    sr = 16000
    t = np.arange(sr * 3) / sr
    low = 0.3 * np.sin(2 * np.pi * 120 * t) + 0.1 * np.sin(2 * np.pi * 240 * t)
    high = 0.3 * np.sin(2 * np.pi * 220 * t) + 0.1 * np.sin(2 * np.pi * 440 * t)
    assert students.median_f0(low.astype(np.float32), sr) < students.MALE_F0_HZ
    assert students.median_f0(high.astype(np.float32), sr) > students.MALE_F0_HZ




def test_voices_rotate_by_gender_and_key_by_real_name():
    # 09-30 宇軒：男生依序男 1、男 2⋯，女生女 1、女 2⋯（女 5 不用）；同一集每位不同；鍵用本名；可以改
    import json
    import os
    import tempfile

    data = Path(tempfile.mkdtemp())
    w = Path(tempfile.mkdtemp()) / "工作區"
    w.mkdir()
    old = os.environ.get("BOOKCLUB_DATA_DIR")
    os.environ["BOOKCLUB_DATA_DIR"] = str(data)
    try:
        d = data / "聲線" / "候選_0928"
        d.mkdir(parents=True)
        for name in ("男1", "男2", "男10", "女1", "女5", "女6"):
            (d / f"{name}.wav").write_bytes(b"x")
            (d / f"{name}.txt").write_text("參考音逐字稿", encoding="utf-8")
        (data / "名冊.csv").write_text("中文名,其他寫法,性別\n王小明,小明,男\n李小華,,女\n陳大同,,男\n林美美,,女\n",
                                      encoding="utf-8")
        pool = students.voice_pool()
        assert [f.stem for f in pool["男"]] == ["男1", "男2", "男10"] and [f.stem for f in pool["女"]] == ["女1", "女6"]
        assert students.roster_gender("小明") == "男" and students.roster_gender("不在名冊") is None
        people = {"學員1": {"本名": "王小明", "第一次": 10.0}, "學員2": {"本名": "李小華", "第一次": 20.0},
                  "學員3": {"本名": "陳大同", "第一次": 30.0}, "學員4": {"本名": "林美美", "第一次": 40.0},
                  "學員5": {"第一次": 50.0}}
        order = ["學員1", "學員2", "學員3", "學員4", "學員5"]
        got = students.assign_voices(w, order, {}, people=people, estimate=False, log=lambda s: None)
        assert [got[n]["名稱"] for n in order[:4]] == ["男1", "女1", "男2", "女6"]
        assert got["學員5"]["檔案"] is None                                    # 性別不知道、不估：先不配
        saved = json.loads(students.voices_path(w).read_text(encoding="utf-8"))
        assert set(saved["學員"]) == {"王小明", "李小華", "陳大同", "林美美"}   # 鍵是本名
        # 合併／拆開後學員編號變了（王小明變成學員7）：照本名沿用同一個聲線
        people2 = {"學員7": {"本名": "王小明", "第一次": 5.0}, **{k: v for k, v in people.items() if k != "學員1"}}
        got2 = students.assign_voices(w, ["學員7", "學員2"], {}, people=people2, estimate=False, log=lambda s: None)
        assert got2["學員7"]["名稱"] == "男1" and got2["學員2"]["名稱"] == "女1"
        # 人改聲線：選男 10；空白＝回到自動配
        people_file = {"學員": {k: {kk: vv for kk, vv in v.items() if kk != "第一次"} for k, v in people.items()},
                       "段落": [{"說話者": k, "start": v["第一次"], "end": v["第一次"] + 1} for k, v in people.items()]}
        (w / "校對").mkdir()
        (w / "校對" / "段落.json").write_text(json.dumps(people_file, ensure_ascii=False), encoding="utf-8")
        students.set_voice_choice(w, "學員3", "男10")
        got3 = students.assign_voices(w, order[:4], {}, people=people, estimate=False, log=lambda s: None)
        assert got3["學員3"]["名稱"] == "男10" and got3["學員3"]["人選的"]
        students.set_voice_choice(w, "學員3", None)
        got4 = students.assign_voices(w, order[:4], {}, people=people, estimate=False, log=lambda s: None)
        assert got4["學員3"]["名稱"] == "男2" and not got4["學員3"]["人選的"]
        try:
            students.set_voice_choice(w, "學員3", "女5")                      # 不用的聲線選不到
            raise AssertionError
        except ValueError:
            pass
    finally:
        if old is None:
            os.environ.pop("BOOKCLUB_DATA_DIR")
        else:
            os.environ["BOOKCLUB_DATA_DIR"] = old


def test_pitch_estimated_in_step1_used_without_estimating():
    """09-30：第 1 步先估好的音高，網頁載入（estimate=False）就能配聲線；沒估過的照舊等開始生成。"""
    import os
    import tempfile

    data = Path(tempfile.mkdtemp())
    w = Path(tempfile.mkdtemp()) / "工作區"
    (w / "生成").mkdir(parents=True)
    old = os.environ.get("BOOKCLUB_DATA_DIR")
    os.environ["BOOKCLUB_DATA_DIR"] = str(data)
    try:
        d = data / "聲線" / "候選_0928"
        d.mkdir(parents=True)
        for name in ("男1", "女1"):
            (d / f"{name}.wav").write_bytes(b"x")
            (d / f"{name}.txt").write_text("參考音逐字稿", encoding="utf-8")
        students.wd.write_json(students.voices_path(w), {"版本": 2, "學員": {}, "音高": {"學員1": {"hz": 120}, "學員2": {"hz": 210},
                                                                          "學員3": {"hz": None}}})
        people = {f"學員{i}": {"第一次": i * 10.0} for i in (1, 2, 3, 4)}
        got = students.assign_voices(w, list(people), {}, people=people, estimate=False, log=lambda s: None)
        assert got["學員1"]["名稱"] == "男1" and got["學員2"]["名稱"] == "女1", got
        assert got["學員3"]["性別"] == "女" and "估不出來" in got["學員3"]["依據"]      # 估過但估不出來 → 先用女聲
        assert got["學員4"]["檔案"] is None and got["學員4"]["依據"] == "還沒判斷"     # 沒估過 → 開始生成時才估
    finally:
        if old is None:
            os.environ.pop("BOOKCLUB_DATA_DIR", None)
        else:
            os.environ["BOOKCLUB_DATA_DIR"] = old


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
