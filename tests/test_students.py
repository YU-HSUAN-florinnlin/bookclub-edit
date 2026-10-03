"""bookclub/students.py 的單元測試：切段規則、範圍過濾、刪除範圍排除、男女聲判斷。不載入模型。

獨立可跑：.venv/bin/python tests/test_students.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 10-01：這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import json
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


def test_chunks_cut_only_at_real_pauses_not_punctuation():
    # 10-03 #61：句子之間停頓 0.4 秒（≥ 0.3）→ 8 秒以上就切；標點不管
    sents = [S(i, i * 3.0, i * 3.0 + 2.6, "沒有標點") for i in range(10)]
    slots = students.plan_slots(sents)
    for c in slots[:-1]:
        a, b = c["slot"]
        assert 8 <= b - a <= 25, (a, b)
        assert not c["切在講話中"]
    assert sum(len(c["句子"]) for c in slots) == 10


def test_punctuation_alone_does_not_cut():
    # 每句都有句號、但句子之間沒有停頓（0.1 秒）→ 不在 8 秒切，一直接到 25 秒上限
    sents = [S(i, i * 3.0, i * 3.0 + 2.9, "一句話。") for i in range(12)]
    slots = students.plan_slots(sents)
    a, b = slots[0]["slot"]
    assert b - a > 20, (a, b)
    assert b - a <= 25 + 0.1
    assert slots[0]["切在講話中"], "找不到停頓、切在講話中要標出來"


def test_adjacent_slots_share_cut_point_in_middle_of_pause():
    # 10-03 下午：相鄰兩格頭尾相接，切點在停頓正中間
    sents = [S(0, 0, 4.0, "甲"), S(1, 4.1, 8.5, "乙"), S(2, 9.5, 13, "丙"), S(3, 13.1, 18, "丁")]
    slots = students.plan_slots(sents)
    assert len(slots) == 2
    assert slots[0]["slot"][1] == slots[1]["slot"][0] == 9.0   # 8.5–9.5 的正中間
    assert slots[0]["slot"][0] == 0 and slots[1]["slot"][1] == 18


def test_no_pause_found_falls_back_to_quietest_and_flags():
    # 都沒有停頓：超過 25 秒前切在「最安靜」（這裡＝句子間隔最大）的交界，標切在講話中
    sents = [S(0, 0, 9, "a"), S(1, 9.05, 18, "b"), S(2, 18.25, 24, "c"), S(3, 24.05, 30, "d")]
    slots = students.plan_slots(sents)
    assert [len(c["句子"]) for c in slots] == [2, 2]
    assert slots[0]["slot"][1] == slots[1]["slot"][0] == 18.125
    assert slots[0]["切在講話中"] == [18.125] and slots[1]["切在講話中"] == [18.125]


def test_earlier_short_pause_used_before_cutting_mid_speech():
    # 8 秒前有一個停頓、之後一直講到超過 25 秒：切在那個停頓（格子短一點也不要切在講話中）
    sents = [S(0, 0, 5, "a"), S(1, 5.5, 15, "b"), S(2, 15.05, 24, "c"), S(3, 24.05, 33, "d")]
    slots = students.plan_slots(sents)
    assert slots[0]["slot"] == [0, 5.25] and not slots[0]["切在講話中"]


def test_short_tail_merges_into_previous():
    sents = [S(0, 0, 9, "第一句。"), S(1, 9.5, 12, "尾巴。")]
    chunks = students.plan_chunks(sents)
    assert len(chunks) == 1 and _spans(chunks) == [(0, 12)]


def test_single_short_turn_kept():
    chunks = students.plan_chunks([S(0, 100, 107, "短短一句")])
    assert _spans(chunks) == [(100, 107)]


def test_quiet_map_finds_pause_from_audio_not_timestamps():
    # 原片量音量：逐字稿說句子之間沒有間隔（4.0 接 4.0），但原片 3.8–4.4 真的安靜 → 切在 4.1
    fs = 0.02
    db = np.full(int(20 / fs), -25.0)
    db[int(3.8 / fs):int(4.4 / fs)] = -80.0
    db[int(10.0 / fs):int(10.2 / fs)] = -80.0   # 0.2 秒：不夠長，不算停頓
    qm = students.QuietMap(db, 0.0)
    b = qm.boundary(S(0, 0, 4.0, "a"), S(1, 4.0, 8, "b"))
    assert b["停頓"] and abs(b["切點"] - 4.1) < 0.02, b
    b2 = qm.boundary(S(0, 5, 10.1, "a"), S(1, 10.1, 15, "b"))
    assert not b2["停頓"] and 9.8 <= b2["切點"] <= 10.4   # 沒有停頓：退回最安靜的一點
    # 逐字稿有間隔、原片卻在講話（間隔是轉文字的誤差）→ 不算停頓
    b3 = qm.boundary(S(0, 12, 13, "a"), S(1, 14, 15, "b"))
    assert not b3["停頓"]


def test_quiet_map_slots_all_cut_in_quiet():
    fs = 0.02
    db = np.full(int(60 / fs), -22.0)
    pauses = [(8.9, 9.4), (17.3, 17.8), (29.2, 29.9)]
    for a, b in pauses:
        db[int(a / fs):int(b / fs)] = -75.0
    sents = [S(0, 0, 9, "a"), S(1, 9.3, 17.4, "b"), S(2, 17.7, 29.3, "c"), S(3, 29.8, 40, "d")]
    slots = students.plan_slots(sents, boundary=students.QuietMap(db, 0.0).boundary)
    cuts = [c["slot"][1] for c in slots[:-1]]
    assert cuts == [round((a + b) / 2, 3) for a, b in pauses], cuts
    for c, d in zip(slots, slots[1:]):
        assert c["slot"][1] == d["slot"][0]


def test_cut_range_excluded_and_splits():
    sents = [S(i, i * 3.0, i * 3.0 + 2.5, "一句話。") for i in range(10)]
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


def test_voices_compare_by_name_and_reset_restores_auto():
    """10-01 走查：設定資料夾換了位置（紀錄裡是舊路徑）不重新配、不跟別人撞；改了又改回自動配，回到原本那一個。"""
    import json
    import os
    import tempfile

    old_env = os.environ.get("BOOKCLUB_DATA_DIR")
    data = Path(tempfile.mkdtemp())
    w = Path(tempfile.mkdtemp()) / "工作區"
    w.mkdir()
    os.environ["BOOKCLUB_DATA_DIR"] = str(data)
    try:
        d = data / "聲線" / "候選_0928"
        d.mkdir(parents=True)
        for name in ("女1", "女2", "女3", "女4", "女6"):
            (d / f"{name}.wav").write_bytes(name.encode())
            (d / f"{name}.txt").write_text("稿", encoding="utf-8")
        gone = "/別台電腦/讀書會剪輯資料/聲線/候選_0928"
        (w / "生成").mkdir()
        table = {"版本": 2, "學員": {"甲": {"檔案": f"{gone}/女1.wav", "名稱": "女1", "性別": "女", "人選的": False},
                          "乙": {"檔案": f"{gone}/女4.wav", "名稱": "女4", "性別": "女", "人選的": False}}}
        students.voices_path(w).write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
        people = {"學員1": {"本名": "甲", "第一次": 1.0}, "學員2": {"本名": "乙", "第一次": 2.0},
                  "學員3": {"本名": "丙", "第一次": 3.0, "性別": "女"}}
        (w / "校對").mkdir()
        (w / "校對" / "段落.json").write_text(json.dumps({"學員": people, "段落": []}, ensure_ascii=False), encoding="utf-8")
        got = students.assign_voices(w, ["學員1", "學員2"], {}, people=people, estimate=False, log=lambda s: None)
        assert got["學員1"]["名稱"] == "女1" and got["學員2"]["名稱"] == "女4"        # 同一個聲線、換了位置
        assert Path(got["學員2"]["檔案"]).parent == d
        students.set_voice_choice(w, "學員2", "女6")
        assert students.assign_voices(w, ["學員1", "學員2"], {}, people=people, estimate=False,
                                      log=lambda s: None)["學員2"]["名稱"] == "女6"
        students.set_voice_choice(w, "學員2", None)                                   # 改回自動配
        got = students.assign_voices(w, ["學員1", "學員2"], {}, people=people, estimate=False, log=lambda s: None)
        assert got["學員2"]["名稱"] == "女4" and not got["學員2"]["人選的"]
    finally:
        if old_env is None:
            os.environ.pop("BOOKCLUB_DATA_DIR", None)
        else:
            os.environ["BOOKCLUB_DATA_DIR"] = old_env


def test_build_items_slots_follow_audio_and_touch_within_turn():
    # 10-03 #61：假工作區（句子之間原片安靜 0.4 秒）：同一段落的格子頭尾相接、切點在安靜處；老師段落不產生格子
    import tempfile

    sys.path.insert(0, str(REPO_ROOT / "tests"))
    import fake_workdir

    with tempfile.TemporaryDirectory() as t:
        w = fake_workdir.make(t)
        items, _ = students.build_items(w)
        sents = {s["id"]: s for s in json.loads((w / "說話者判斷.json").read_text(encoding="utf-8"))["sentences"]}
        by_turn: dict = {}
        for it in items:
            if it.get("句子"):
                by_turn.setdefault(it["段落"], []).append(it)
        assert by_turn and all(k in ("T003", "T005", "T007") for k in by_turn)   # 只有學員段落
        for its in by_turn.values():
            its.sort(key=lambda x: x["slot"][0])
            for a, b in zip(its, its[1:]):
                assert a["slot"][1] == b["slot"][0], (a["slot"], b["slot"])
                cut = a["slot"][1]
                last, first = sents[a["句子"][-1]], sents[b["句子"][0]]
                assert last["end"] <= cut <= first["start"], (last["end"], cut, first["start"])
                assert "切在講話中" not in a
            assert its[0]["slot"][0] == sents[its[0]["句子"][0]]["start"]
            assert its[-1]["slot"][1] == sents[its[-1]["句子"][-1]]["end"]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
