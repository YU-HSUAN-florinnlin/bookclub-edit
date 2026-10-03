"""bookclub/render.py 的單元測試（純函式）：片段、時間換算、只重做片段的規劃、標字時段合併、文字比對。

獨立可跑：.venv/bin/python tests/test_render.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 10-01：這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

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


def test_freeze_point_continuous_in_output():
    """10-03 第八批 #60：學員重念比時間格長、結尾停格——成品裡停格點前後兩截接起來＝原本連續的聲音（沒有缺口、
    head 結尾不淡出、tail 開頭不淡入），只在整句最後淡出到底噪。照 build_audio 的放法（assemble.voice_over_room）。"""
    import numpy as np

    from bookclub import assemble

    a, SR = 0.0, render.SR
    x = (0.5 * np.sin(2 * np.pi * 200 * np.arange(int(4 * SR)) / SR)).astype(np.float32)   # 原片：學員講話
    s, t, fz = int(1.0 * SR), int(2.0 * SR), int(0.4 * SR)
    clip = (0.3 * np.sin(2 * np.pi * 330 * np.arange(t - s + fz) / SR)).astype(np.float32)
    room = np.full(t - s + fz, 0.001, dtype=np.float32)
    head, tail, cut = assemble.voice_over_room(clip, room, t - s, fz, SR)
    y = x.copy()
    y[s:t] = head
    plist = render.pieces(a, 4.0, [], [{"at": 2.0, "dur": 0.4, "edit": "E1"}])
    whole = np.concatenate(list(render.output_segments(x, y, plist, {"E1": tail}, a)))
    f = int(SR * assemble.FADE_S)
    got = whole[s:t + fz]
    want = clip + room
    assert cut == 0.0 and np.allclose(got[f:-f], want[f:-f], atol=1e-7)   # 停格點（t）前後逐點跟原本的聲音一樣
    assert abs(got[-1] - 0.001) < 1e-6 and abs(got[0] - 0.001) < 1e-6      # 頭尾淡到底噪
    assert np.array_equal(whole[t + fz:], x[t:])                           # 停格之後接回原片


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


def test_build_decisions_name_mute_touching_student_slot_and_uncovered_name_blocks():
    """10-02 第七批（A2）：老師在學員開口前叫名字、選直接消音（前後各 0.05 秒緩衝）→ 跟學員時間格疊 0.05 秒，
    以前整筆消音不做。現在沒疊到的部分照樣消音；名字還有地方沒蓋到（老師句子還沒生成）→ 不輸出成品。"""
    import json
    import os
    import tempfile

    sys.path.insert(0, str(REPO_ROOT / "tests"))
    import fake_workdir
    from bookclub import nameplan, students
    from bookclub import workdir as wdmod

    with tempfile.TemporaryDirectory() as root:
        old = os.environ.get("BOOKCLUB_DATA_DIR")
        data = Path(root) / "資料"
        data.mkdir()
        (data / "名冊.csv").write_text("中文名,其他寫法,英文代號,聲線,性別\n小美,,Amy,,女\n阿明,,Tom,,男\n", encoding="utf-8")
        os.environ["BOOKCLUB_DATA_DIR"] = str(data)
        try:
            w = fake_workdir.make(Path(root) / "base")
            items, _ = students.build_items(w)
            first = sorted(items, key=lambda it: it["slot"][0])[0]
            s0 = first["slot"][0]
            recs = [{"id": it["id"], "段落": it["段落"], "學員": it["學員"], "text": it["text"], "slot": it["slot"],
                     "嘗試": [], "選定": 1,
                     "放回時間格": {"檔案": f"生成/學員/{it['id']}_放回時間格.wav", "放回做法": "補靜音", "差異比例": 0.0}}
                    for it in items]
            wdmod.write_json(students.log_path(w), {"句子": recs, "學員聲線": {}})
            names = wdmod.read_json(wdmod.names_path(w))
            c = names["candidates"][0]
            c["start"], c["end"] = round(s0 - 0.6, 3), round(s0, 3)      # 名字剛好在學員開口前講完
            wdmod.write_json(wdmod.names_path(w), names)
            (w / nameplan.DECISIONS_FILE_NAME).write_text(json.dumps(
                {"1": {"做法": nameplan.MUTE}, "2": {"tags": ["不是名字"]}}, ensure_ascii=False), encoding="utf-8")
            plan = nameplan.make_plan(w)
            m = plan["消音"][0]
            assert m["end"] > s0   # 緩衝讓消音跟學員時間格疊到
            d = render.build_decisions(w, 0.0, 180.0)
            got = [(e["start"], e["end"]) for e in d["動作"] if e["類型"] == "名字消音"]
            assert got == [(m["start"], s0)], got
            assert d["名字沒處理"] == []

            # 另一筆整句重念、老師句子還沒生成 → 名字沒被蓋到，組裝擋下
            (w / nameplan.DECISIONS_FILE_NAME).write_text(json.dumps(
                {"1": {"tags": ["不是名字"]}}, ensure_ascii=False), encoding="utf-8")
            plan = nameplan.make_plan(w)
            assert plan["生成"] and not plan["消音"]
            d = render.build_decisions(w, 0.0, 180.0)
            assert [x["候選"] for x in d["名字沒處理"]] == [plan["生成"][0]["候選"]]
            try:
                render.render_video(w, 0.0, 180.0)
                raise AssertionError("名字沒蓋到還輸出了")
            except RuntimeError as e:
                assert "名字還有地方沒有消音或換掉" in str(e) and "沒處理" in str(e)
        finally:
            if old is None:
                os.environ.pop("BOOKCLUB_DATA_DIR", None)
            else:
                os.environ["BOOKCLUB_DATA_DIR"] = old


def _fake_with_student_records(root: Path) -> tuple[Path, list[dict]]:
    """假工作區＋每一格學員重念都有生成紀錄（放回時間格）。"""
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    import fake_workdir
    from bookclub import students
    from bookclub import workdir as wdmod

    w = fake_workdir.make(root)
    items, _ = students.build_items(w)
    recs = [{"id": it["id"], "段落": it["段落"], "學員": it["學員"], "text": it["text"], "slot": it["slot"], "聲線": "女",
             "嘗試": [], "選定": 1,
             "放回時間格": {"檔案": f"生成/學員/{it['id']}_放回時間格.wav", "放回做法": "補靜音", "差異比例": 0.0}}
            for it in items]
    wdmod.write_json(students.log_path(w), {"句子": recs, "學員聲線": {}})
    return w, [it for it in items if not it.get("重疊")]


def _expected_gaps(items: list[dict], skip: set = frozenset()) -> list[tuple]:
    by: dict[str, list] = {}
    for it in items:
        if it["學員"] not in skip:
            by.setdefault(it["段落"], []).append(it["slot"])
    out = []
    for tid, slots in by.items():
        slots.sort()
        out += [(tid, round(p[1], 3), round(n[0], 3)) for p, n in zip(slots, slots[1:]) if n[0] - p[1] > 0.001]
    return sorted(out, key=lambda g: g[1])


def test_build_decisions_mutes_gaps_between_student_chunks():
    """10-03 第八批補修 #102：同一個學員段落裡兩格之間的空隙墊底噪、進處理紀錄（對到學員段落）；
    段落頭尾、不同段落之間不動；空隙裡有老師的句子 → 保留原聲、處理紀錄要人聽；保留原聲的學員不動。"""
    import os
    import tempfile

    from bookclub import assemble, finalcheck, proclog, review
    from bookclub import workdir as wdmod

    with tempfile.TemporaryDirectory() as root:
        old = os.environ.get("BOOKCLUB_DATA_DIR")
        os.environ["BOOKCLUB_DATA_DIR"] = str(Path(root) / "資料")
        try:
            w, items = _fake_with_student_records(Path(root))
            want = _expected_gaps(items)
            assert want == [("T003", 51.6, 52.0)], want   # 假資料：學員1 的 T003 切成兩格；T005、T007 各一格；段落之間不算
            d = render.build_decisions(w, 0.0, 180.0)
            gaps = [e for e in d["動作"] if e["類型"] == assemble.GAP_KIND]
            assert [(e["段落"], round(e["start"], 3), round(e["end"], 3)) for e in gaps] == want, gaps
            # 段落頭尾不動：每一筆空隙兩邊都是同一個段落的格子
            first_last = {t: (min(i["slot"][0] for i in items if i["段落"] == t), max(i["slot"][1] for i in items if i["段落"] == t))
                          for t in {i["段落"] for i in items}}
            assert all(first_last[e["段落"]][0] < e["start"] and e["end"] < first_last[e["段落"]][1] for e in gaps)
            recs = proclog.build_records(d, render.pieces(0.0, 180.0, d["刪除"], d["停格"]))
            gr = [r for r in recs if r["類型"] == assemble.GAP_KIND]
            assert len(gr) == len(gaps) and all(r["動到聲音"] and not r["要人聽"] for r in gr)
            assert gr[0]["覆核項目"] == [f"學員段落:{gaps[0]['段落']}"]
            assert gr[0]["做了什麼"] == f"學員段落裡兩格之間的空隙 {gaps[0]['end'] - gaps[0]['start']:.2f} 秒墊底噪（不留原聲）"
            assert (gr[0]["原片"][0], gr[0]["原片"][1]) in proclog.audio_spans(recs)
            # 第 5 步退回這一筆：沒有要重新生成的（只重新組裝）
            ctx = {"學員": [{"id": i["id"], "段落": i["段落"], "slot": i["slot"]} for i in items], "老師": [], "保留原聲學員": []}
            assert finalcheck.redo_units(gr[0], ctx) == []
            assert not finalcheck.retime_target(gr[0], {})["可以"]

            # 空隙裡有老師的話（說話者判斷裡 label 是老師的句子跟空隙重疊）→ 保留原片、處理紀錄要人聽
            g0 = gaps[0]
            sp = wdmod.read_json(wdmod.speakers_path(w))
            sp["sentences"].append({"id": "99_000", "start": g0["start"] + 0.05, "end": g0["end"] - 0.05, "text": "嗯。",
                                    "label": "老師", "sim": 0.9})
            wdmod.write_json(wdmod.speakers_path(w), sp)
            d = render.build_decisions(w, 0.0, 180.0)
            gaps2 = [e for e in d["動作"] if e["類型"] == assemble.GAP_KIND]
            assert [(e["start"], e["end"]) for e in gaps2] == [(e["start"], e["end"]) for e in gaps[1:]]
            keep = [m for m in d["標記"] if m["類型"] == assemble.GAP_KEEP_KIND]
            assert [(m["段落"], m["start"], m["保留原因"]) for m in keep] == [(g0["段落"], round(g0["start"], 3), "老師的話")]
            recs = proclog.build_records(d, render.pieces(0.0, 180.0, d["刪除"], d["停格"]))
            kr = [r for r in recs if r["類型"] == assemble.GAP_KEEP_KIND]
            assert len(kr) == 1 and kr[0]["要人聽"] and not kr[0]["動到聲音"] and kr[0]["覆核項目"] == [f"學員段落:{g0['段落']}"]
            assert kr[0]["做了什麼"].startswith("學員段落中間有老師的話，這 ") and kr[0]["做了什麼"].endswith("秒保留原聲，請聽有沒有學員的聲音")
            assert finalcheck.redo_units(kr[0], ctx) == []
            marks = render.build_marks(d, render.pieces(0.0, 180.0, d["刪除"], d["停格"]))
            assert any(r["類型"] == assemble.GAP_KEEP_KIND and r["要人聽"] for r in marks)

            # 保留原聲的學員：他的段落一筆空隙都不處理，也不標；別的學員保留原聲不影響這一段
            sp["sentences"].pop()
            wdmod.write_json(wdmod.speakers_path(w), sp)
            review.set_voice(w, "學員2", "保留原聲")
            d = render.build_decisions(w, 0.0, 180.0)
            got = [(e["段落"], round(e["start"], 3), round(e["end"], 3)) for e in d["動作"] if e["類型"] == assemble.GAP_KIND]
            assert got == want, got
            review.set_voice(w, "學員1", "保留原聲")
            d = render.build_decisions(w, 0.0, 180.0)
            assert not [e for e in d["動作"] if e["類型"] == assemble.GAP_KIND]
            assert not [m for m in d["標記"] if m["類型"] == assemble.GAP_KEEP_KIND]
        finally:
            if old is None:
                os.environ.pop("BOOKCLUB_DATA_DIR", None)
            else:
                os.environ["BOOKCLUB_DATA_DIR"] = old


def test_build_audio_gap_has_only_room_tone_and_no_unlogged_change():
    """#102 組聲音（render.build_audio，假資料、不讀真的影片）：兩格之間的空隙成品裡只有底噪、不含原片；
    空隙有進處理紀錄，沒有「沒登記的變動」。空隙裡有老師的話時原片逐點保留。"""
    import tempfile

    import numpy as np
    import soundfile as sf

    from bookclub import assemble, proclog

    sr = SR = render.SR
    with tempfile.TemporaryDirectory() as root:
        w = Path(root) / "工作區"
        out = w / "輸出"
        (w / "生成" / "學員").mkdir(parents=True)
        out.mkdir()
        rng = np.random.default_rng(3)
        x = (0.001 * rng.standard_normal(SR * 12)).astype(np.float32)
        t = np.arange(SR * 5) / SR
        x[SR:SR * 6] += (0.3 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)      # 1–6 秒學員講話（含 3.0–3.5 的空隙）
        tone = (0.1 * np.sin(2 * np.pi * 500 * np.arange(int(1.9 * SR)) / SR)).astype(np.float32)
        for k in ("T003_01", "T003_02"):
            sf.write(str(w / "生成" / "學員" / f"{k}.wav"), tone, SR, subtype="PCM_16")

        def run(tag: str, with_gap: bool) -> tuple[np.ndarray, np.ndarray, dict]:
            sf.write(str(out / f"原聲_{tag}.wav"), x, SR, subtype="PCM_16")
            acts = [{"類型": "學員重念", "id": k, "start": s, "end": e, "檔案": f"生成/學員/{k}.wav", "學員": "學員1", "聲線": "女",
                     "text": "假", "生成用文字": "假"} for k, s, e in (("T003_01", 1.0, 3.0), ("T003_02", 3.5, 5.5))]
            marks = []
            if with_gap:
                new, marks = assemble.gap_edits(assemble.student_gaps(
                    [{"id": a["id"], "段落": "T003", "slot": [a["start"], a["end"]]} for a in acts]), acts)
            else:
                new, marks = assemble.gap_edits(assemble.student_gaps(
                    [{"id": a["id"], "段落": "T003", "slot": [a["start"], a["end"]]} for a in acts]), acts, teacher=[(3.0, 3.5)])
            d = {"範圍": [0.0, 12.0], "動作": sorted(acts + new, key=lambda e: e["start"]), "刪除": [], "停格": [], "模糊": None,
                 "標記": marks}
            res = render.build_audio(w, Path(root) / "沒有影片.mp4", d, out, tag)
            o, _ = sf.read(str(res["原聲"]), dtype="int16")
            n, _ = sf.read(str(res["新聲音"]), dtype="int16")
            recs = proclog.build_records(d, res["片段"])
            chk = proclog.check_arrays(o.astype(np.float32) / 32768, n.astype(np.float32) / 32768, sr, recs)
            return o, n, {"紀錄": recs, **chk}

        s, e = int(3.0 * sr), int(3.5 * sr)
        o, n, log = run("gap", True)
        assert np.max(np.abs(n[s:e].astype(np.float32) / 32768)) < 0.02          # 空隙裡原片 0.3 的聲音一點都沒留
        assert np.array_equal(n[:sr], o[:sr]) and np.array_equal(n[6 * sr:], o[6 * sr:])   # 段落外沒動
        assert [r["類型"] for r in log["紀錄"]] == ["學員重念", assemble.GAP_KIND, "學員重念"]
        assert log["未登記的變動"] == [], log["未登記的變動"]

        o, n, log = run("keep", False)
        assert np.array_equal(n[s:e], o[s:e])                                      # 老師的話：原片逐點保留
        kinds = [(r["類型"], r["要人聽"]) for r in log["紀錄"]]
        assert (assemble.GAP_KEEP_KIND, True) in kinds and assemble.GAP_KIND not in [k for k, _ in kinds]
        assert log["未登記的變動"] == [], log["未登記的變動"]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
