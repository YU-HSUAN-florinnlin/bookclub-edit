"""bookclub/render.py 的單元測試（純函式）：片段、時間換算、只重做片段的規劃、標字時段合併、文字比對。

獨立可跑：.venv/bin/python tests/test_render.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import render


def test_pieces_cut_and_freeze():
    pl = render.pieces(100, 200, [(120, 130)], [{"at": 150, "dur": 1.0}])
    assert [p["src"] for p in pl] == [[100, 120], [130, 150], [150, 200]]
    assert [p["freeze"] for p in pl] == [0.0, 1.0, 0.0]
    assert render.output_length(pl) == 100 - 10 + 1


def test_to_output_time():
    pl = render.pieces(100, 200, [(120, 130)], [{"at": 150, "dur": 1.0}])
    assert render.to_output_time(110, pl) == 10
    assert render.to_output_time(125, pl) is None          # 刪掉了
    assert render.to_output_time(130, pl) == 20            # 剪點
    assert render.to_output_time(150, pl) == 40            # 停格前
    assert render.to_output_time(160, pl) == 51            # 停格後多 1 秒


def test_cut_outside_range_ignored():
    pl = render.pieces(100, 200, [(10, 20), (190, 250)], [])
    assert [p["src"] for p in pl] == [[100, 190]]


def test_clip_to_cuts_after_frame_snap():
    a, b = render.clip_to_cuts(2491.79, 2511.14, [(2328.88, 2491.8)])
    assert (a, b) == (2491.8, 2511.14)
    pl = render.pieces(2220, 3323, [(2328.88, 2491.8)], [])
    assert render.to_output_time(a, pl) is not None


def test_speedup_for_choice_c():
    assert render.speedup_for(9.0, 10.0) == 1.0              # 沒超過不用加快
    assert render.speedup_for(11.0, 10.0) == 1.1              # 超過 10%：加快 10% 剛好放得進去
    assert render.speedup_for(14.0, 10.0) == 1.15             # 超過很多：最多加快 15%，剩下停格


def test_snap_and_ceil_frames():
    assert render.snap(2328.93) == 2328.92
    assert render.ceil_frames(0.25) == 0.28


def test_smart_plan_copies_between_keyframes():
    pl = render.pieces(100, 200, [], [])
    keys = [96, 132, 168, 204]
    plan = render.smart_plan(pl, keys, None)
    assert plan[0] == {"src": [100, 132], "做法": "重做", "freeze": 0.0}   # 開頭不是關鍵畫面
    assert plan[1]["做法"] == "複製" and plan[1]["src"] == [132, 200]


def test_smart_plan_blur_and_freeze_reencode():
    pl = render.pieces(132, 240, [], [{"at": 240, "dur": 2.0}])
    plan = render.smart_plan(pl, [132, 168, 204, 240], [170, 180])
    kinds = [(p["src"], p["做法"], p["freeze"]) for p in plan]
    # 模糊那一段、後面接停格那一段都要重做，相鄰的合併成一段編碼
    assert kinds == [([132, 168], "複製", 0.0), ([168, 240], "重做", 2.0)]
    assert sum(p["src"][1] - p["src"][0] for p in plan) == 108


def test_merge_windows_combines_labels():
    wins = [{"start": 0, "end": 10, "label": "A", "caption": "x"}, {"start": 5, "end": 20, "label": "B", "caption": None}]
    m = render.merge_windows(wins)
    assert [(w["start"], w["end"], w["label"], w["caption"]) for w in m] == \
        [(0, 5, "A", "x"), (5, 10, "A｜B", "x"), (10, 20, "B", None)]


def test_diff_marks():
    a, b, n = render.diff_marks("我今天很開心。", "我今天很關心")
    assert a == "我今天很【開】心" and b == "我今天很【關】心" and n == 1
    assert render.diff_marks("一樣，", "一樣")[2] == 0


def test_onset_ignores_short_blip_and_lag_finds_shift():
    import numpy as np

    sr = render.SR
    x = np.zeros(sr * 4, dtype=np.float32)
    x[int(0.5 * sr):int(0.52 * sr)] = 0.5          # 20 毫秒的雜音不算開口
    t = np.arange(sr) / sr
    x[int(1.5 * sr):int(2.5 * sr)] = 0.3 * np.sin(2 * np.pi * 200 * t)
    assert abs(render.onset(x) - 1.5) < 0.02
    y = np.roll(x, int(0.3 * sr))
    assert abs(render.envelope_lag(x, y) - 0.3) < 0.02



def test_output_segments_stream_same_as_concat():
    # 09-30：組聲音改成一段一段寫檔；吐出來的段落接起來要等於成品長度，剪點淡出淡入、停格補的聲音照舊
    import tempfile

    import numpy as np
    import soundfile as sf

    a, SR = 100.0, render.SR
    t = np.arange(int(20 * SR)) / SR
    x = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    y = x.copy()
    plist = render.pieces(a, a + 20.0, [(105.0, 107.0)], [{"at": 112.0, "dur": 0.4, "edit": "E1"}])
    tails = {"E1": np.full(int(0.4 * SR), 0.1, dtype=np.float32)}
    segs = list(render.output_segments(x, y, plist, tails, a))
    whole = np.concatenate(segs)
    assert abs(len(whole) / SR - render.output_length(plist)) < 1e-3
    k = int(round((105.0 - a) * SR))                       # 剪點前 10 毫秒淡出到 0
    assert abs(whole[k - 1]) < 1e-3
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "新.wav"
        with sf.SoundFile(str(out), "w", SR, 1, subtype="PCM_16") as fw:
            for seg in render.output_segments(x, y, plist, tails, a):
                fw.write(np.clip(seg, -1, 1))
        got, _ = sf.read(str(out), dtype="float32")
    assert len(got) == len(whole) and np.max(np.abs(got - np.clip(whole, -1, 1))) < 1e-4


def test_build_decisions_mutes_overlaps():
    """09-30：重疊處除了「不用改」都要消音（或被換聲音蓋掉），成品不能留學員原聲；保留原聲的學員不動；救回的也算。"""
    import os
    import tempfile

    sys.path.insert(0, str(REPO_ROOT / "tests"))
    import fake_workdir
    from bookclub import review

    with tempfile.TemporaryDirectory() as root:
        old = os.environ.get("BOOKCLUB_DATA_DIR")
        os.environ["BOOKCLUB_DATA_DIR"] = str(Path(root) / "資料")
        try:
            w = fake_workdir.make(root)
            d = render.build_decisions(w, 0.0, 180.0)
            ov = [m for m in d["標記"] if m["類型"] == "重疊"]
            assert [m["id"] for m in ov] == ["O69.60"], ov   # 0 秒的那筆不算、第三筆自動跳過（兩位學員）
            assert d["重疊沒處理"] == [] and all(m["處理"] for m in ov)
            muted = [(e["重疊"], e["start"], e["end"]) for e in d["動作"] if e.get("重疊")]
            assert muted == [("O69.60", 69.6, 70.1)], muted
            assert not [f for f in d["停格"] if "重疊" in f.get("原因", "")]   # 前後排開還沒做，不再停格

            # 救回自動跳過的那筆 → 也要處理
            review.save_overlap(w, "O150.20", {"救回": True})
            d = render.build_decisions(w, 0.0, 180.0)
            assert "O150.20" in [e.get("重疊") for e in d["動作"]] and d["重疊沒處理"] == []

            # 選「生成老師聲音」：老師整句排進生成清單（跟名字同一句就併在一起）；找不到老師句子的記下來；
            # 還沒生成時照消音處理，不會留著原聲
            import json
            from bookclub import nameplan
            from bookclub import workdir as wdmod
            ovf = wdmod.overlap_path(w)
            data = json.loads(ovf.read_text(encoding="utf-8"))
            data["overlaps"].append({"start": 78.0, "end": 78.4, "length": 0.4, "已自動跳過": False, "原因": None, "區域": "區域0009",
                                     "speakers": [{"label": "A", "role": "不是老師", "sim": 0.1}, {"label": "B", "role": "老師", "sim": 0.8}]})
            ovf.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            review.save_overlap(w, "O78.00", {"做法": "只留老師"})
            review.save_overlap(w, "O69.60", {"做法": "只留老師"})      # 這一處在學員的話裡，找不到老師的句子
            plan = nameplan.compute_plan(w)
            assert [(g["id"], g["重疊項目"]) for g in plan["生成"] if g.get("重疊項目")] == [("S001", ["O78.00"])], plan["生成"]
            assert plan["重疊沒句子"] == ["O69.60"]
            d = render.build_decisions(w, 0.0, 180.0)
            assert {"O69.60", "O78.00"} <= {e.get("重疊") for e in d["動作"]} and d["重疊沒處理"] == []
            page = review.page_data(w)
            its = {i["id"]: i for i in page["項目"] if i["類型"] == "重疊"}
            assert its["O78.00"]["老師整句"]["原文"] and its["O69.60"]["老師整句"] is None
            assert "兩邊都不留" not in page["選項"]["重疊"]

            # 人選「不用改」→ 原樣保留、不消音
            review.save_overlap(w, "O69.60", {"做法": "不用改"})
            d = render.build_decisions(w, 0.0, 180.0)
            assert "O69.60" not in [e.get("重疊") for e in d["動作"]] and d["重疊沒處理"] == []
        finally:
            if old is None:
                os.environ.pop("BOOKCLUB_DATA_DIR", None)
            else:
                os.environ["BOOKCLUB_DATA_DIR"] = old


def test_build_decisions_ignores_outdated_student_records():
    """09-30：生成紀錄只增不減。段落改過之後：編號不存在的舊重念不用；時間格改過還沒重新生成的，那一格先消音。"""
    import os
    import tempfile

    sys.path.insert(0, str(REPO_ROOT / "tests"))
    import fake_workdir
    from bookclub import students
    from bookclub import workdir as wdmod

    with tempfile.TemporaryDirectory() as root:
        old = os.environ.get("BOOKCLUB_DATA_DIR")
        os.environ["BOOKCLUB_DATA_DIR"] = str(Path(root) / "資料")
        try:
            w = fake_workdir.make(root)
            items, _ = students.build_items(w)
            ok, moved = items[0], items[1]
            fitted = {"檔案": "生成/學員/x.wav", "放回做法": "補靜音", "差異比例": 0.0}
            rec = lambda it, slot: {"id": it["id"], "學員": it["學員"], "段落": it["段落"], "slot": slot, "text": it["text"],   # noqa: E731
                                    "聲線": "女", "放回時間格": fitted}
            wdmod.write_json(students.log_path(w), {"句子": [
                rec(ok, ok["slot"]),                                             # 對得上：照放
                rec(moved, [moved["slot"][0], moved["slot"][1] + 5.0]),          # 時間格改過、還沒重新生成
                {**rec(ok, [170.0, 175.0]), "id": "T999_01", "段落": "T999"}]})  # 段落已經不存在
            d = render.build_decisions(w, 0.0, 180.0)
            got = {e.get("id"): e["類型"] for e in d["動作"] if e.get("id") in (ok["id"], moved["id"], "T999_01")}
            assert got == {ok["id"]: "學員重念", moved["id"]: "局部消音"}, got
            m = next(e for e in d["動作"] if e.get("id") == moved["id"])
            assert abs(m["start"] - moved["slot"][0]) < 0.05 and m["end"] <= moved["slot"][1] + 0.05
            assert any("T999_01" in x for x in d["警告"]) and any(moved["id"] in x and "消音" in x for x in d["警告"])
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
