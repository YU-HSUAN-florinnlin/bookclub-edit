"""bookclub/turns.py 的單元測試（純函式與存檔，不呼叫 Claude、不載入聲紋模型）。

獨立可跑：.venv/bin/python tests/test_turns.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 10-01：這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from bookclub import turns


def test_normalize_turns_fills_gaps_and_overlaps():
    raw = [{"起": 0, "迄": 3, "說話者": "老師"}, {"起": 3, "迄": 5, "說話者": "學員"},
           {"起": 8, "迄": 9, "說話者": "老師"}, {"起": "x", "迄": 2}]
    out = turns.normalize_turns(raw, 0, 10)
    assert [(t["起"], t["迄"]) for t in out] == [(0, 3), (4, 7), (8, 10)]


def test_parse_json_from_chatty_output():
    assert turns.parse_json('好的，結果如下：\n```json\n{"段落": []}\n```')["段落"] == []


def _t(tid, start, end, text_role, voice_role, text_id=None, person=None):
    return {"id": tid, "start": start, "end": end, "文字判斷": text_role, "聲音判斷": voice_role,
            "文字學員編號": text_id, "學員": person}


def test_compare_with_voice_by_seconds():
    ts = [_t("a", 0, 60, "老師", "老師"), _t("b", 60, 90, "學員", "學員"), _t("c", 90, 100, "老師", "學員"),
          _t("d", 100, 105, "學員", "不確定")]
    c = turns.compare_with_voice(ts)
    assert c["一致比例"] == 0.9 and c["文字老師聲紋學員"] == 10 and c["聲紋不確定"] == 5
    calm = [{**_t("m", 0, 600, "老師", "學員"), "內容類型": "冥想引導"}, _t("b", 600, 700, "老師", "老師")]
    c2 = turns.compare_with_voice(calm)
    assert c2["一致比例"] == 1.0 and c2["冥想導讀不比對"] == 600


def test_same_person_agreement_within_chunk():
    ts = [_t("a", 0, 1, "學員", "學員", "C0-S1", "學員1"), _t("b", 1, 2, "學員", "學員", "C0-S1", "學員1"),
          _t("c", 2, 3, "學員", "學員", "C0-S2", "學員1"), _t("d", 3, 4, "學員", "學員", "C1-S1", "學員2")]
    r = turns.same_person_agreement(ts)
    assert r["同一人比對組數"] == 3 and abs(r["同一人判斷一致比例"] - 1 / 3) < 1e-3


def test_cluster_turns_orders_by_seconds():
    a, b = np.array([1.0, 0.0]), np.array([0.0, 1.0])
    assert turns.cluster_turns(np.stack([a, b, b]), [50.0, 10.0, 10.0]) == [0, 1, 1]


def test_save_merge_split_and_person():
    with tempfile.TemporaryDirectory() as d:
        w = Path(d)
        (w / "校對").mkdir()
        sents = [{"id": f"s{i}", "start": i * 5.0, "end": i * 5.0 + 4, "text": t, "label": "不是老師"}
                 for i, t in enumerate(["我先說。", "然後呢。", "換我了。"])]
        (w / "說話者判斷.json").write_text(json.dumps({"sentences": sents}, ensure_ascii=False), encoding="utf-8")
        data = {"段落": [
            {"id": "T001", "start": 0, "end": 9, "句子": ["s0", "s1"], "說話者": "學員1", "原文": "我先說。然後呢。",
             "校對稿": "我先說。然後呢。", "已確認": False, "校對秒數": None},
            {"id": "T002", "start": 10, "end": 14, "句子": ["s2"], "說話者": "學員1", "原文": "換我了。",
             "校對稿": "換我了。", "已確認": False, "校對秒數": None}],
            "學員": {"學員1": {"秒數": 13, "段數": 2, "點名線索": {}, "代號": None}}}
        (w / "校對" / "段落.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        r = turns.save_turn(w, "T002", {"說話者": "新學員", "已確認": True, "加秒數": 999})
        assert r["段落"]["說話者"] == "學員2" and r["段落"]["校對秒數"] == turns.MAX_COUNT_S
        turns.split_turn(w, "T001", 4)
        d1 = json.loads((w / "校對" / "段落.json").read_text(encoding="utf-8"))
        assert [t["id"] for t in d1["段落"]] == ["T001", "T001m1", "T002"]
        assert d1["段落"][0]["校對稿"] == "我先說。" and d1["段落"][1]["start"] == 5.0
        turns.merge_turn(w, "T001m1")
        turns.set_person_code(w, "學員2", "Laura")
        d2 = json.loads((w / "校對" / "段落.json").read_text(encoding="utf-8"))
        assert len(d2["段落"]) == 2 and d2["段落"][0]["end"] == 9 and d2["學員"]["學員2"]["代號"] == "Laura"
        p = turns.turns_progress(d2)
        assert p["學員段落數"] == 2 and p["已確認"] == 1


def test_turn_sentences_clip_and_map_pos():
    # 09-30：人把段落結尾提早（或切在句子裡面）時，句子的時間、文字照段落實際的範圍
    by_id = {"a": {"id": "a", "start": 0.0, "end": 4.0, "text": "我先說，"},
             "b": {"id": "b", "start": 4.0, "end": 34.0, "text": "我講完了。好啊謝謝小美。"}}
    t = {"start": 0.0, "end": 16.0, "句子": ["a", "b"], "句尾切點": {"b": 5}}
    ss = turns.turn_sentences(t, by_id)
    assert [(x["start"], x["end"], x["text"]) for x in ss] == [(0.0, 4.0, "我先說，"), (4.0, 16.0, "我講完了。")]
    assert by_id["b"]["end"] == 34.0 and by_id["b"]["text"].endswith("小美。")          # 原本的句子不動
    assert turns.turn_sentences({"start": 0.0, "end": 40.0, "句子": ["a", "b"]}, by_id)[1]["end"] == 34.0
    # 校對稿的游標位置 → 原文的位置（名字換成代號後字數不同）
    raw, edited = "謝謝小美，我講完了。", "謝謝Amy，我講完了。"
    assert turns.map_pos(raw, edited, edited.index("，") + 1) == raw.index("，") + 1
    assert turns.map_pos(raw, raw, 3) == 3 and turns.map_pos(raw, edited, len(edited)) == len(raw)
    # 句子裡第幾個字是幾秒：用逐字時間；沒有就照字數比例
    s = {"id": "b", "start": 4.0, "end": 34.0, "text": "我講完了。好啊謝謝小美。"}
    words = [{"word": c, "start": 4.0 + k, "end": 4.8 + k} for k, c in enumerate("我講完了")] + \
            [{"word": c, "start": 24.0 + k, "end": 24.8 + k} for k, c in enumerate("好啊謝謝小美")]
    cut, exact = turns.cut_time_in_sentence(s, 5, words)       # 「我講完了。」後面
    assert exact and 7.8 <= cut <= 24.0, cut                    # 落在「了」跟「好」中間的空檔
    cut2, exact2 = turns.cut_time_in_sentence(s, 5, [])
    assert not exact2 and 4.0 < cut2 < 34.0


def test_split_inside_a_sentence_and_teacher_item():
    """09-30 宇軒：學員的最後一句跟老師的話被轉成同一句 → 從游標處切開要切得了；
    後面那半句（沒有逐字稿句子）改成老師時，變成一筆「老師這一段用 AI 聲音重念」。"""
    import os

    from bookclub import nameplan, review, students

    old = os.environ.get("BOOKCLUB_DATA_DIR")
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as data_dir:
        os.environ["BOOKCLUB_DATA_DIR"] = data_dir
        (Path(data_dir) / "名冊.csv").write_text("中文名,其他寫法,性別\n小美,,女\n", encoding="utf-8")
        try:
            w = Path(d)
            (w / "校對").mkdir()
            (w / "transcript").mkdir()
            sents = [{"id": "s0", "start": 0.0, "end": 4.0, "text": "我先說，", "label": "不是老師"},
                     {"id": "s1", "start": 4.0, "end": 34.0, "text": "我講完了。好啊謝謝小美。", "label": "不確定"},
                     {"id": "s2", "start": 40.0, "end": 44.0, "text": "我們繼續。", "label": "老師"}]
            words = [{"word": c, "start": 0.2 + k * 0.8, "end": 0.9 + k * 0.8} for k, c in enumerate("我先說")] + \
                    [{"word": c, "start": 4.0 + k, "end": 4.8 + k} for k, c in enumerate("我講完了")] + \
                    [{"word": c, "start": 24.0 + k, "end": 24.8 + k} for k, c in enumerate("好啊謝謝小美")]
            (w / "說話者判斷.json").write_text(json.dumps({"sentences": sents}, ensure_ascii=False), encoding="utf-8")
            (w / "transcript" / "merged.json").write_text(json.dumps({"sentences": sents, "words": words}, ensure_ascii=False),
                                                          encoding="utf-8")
            raw = "我先說，我講完了。好啊謝謝小美。"
            data = {"段落": [
                {"id": "T001", "start": 0.0, "end": 34.0, "句子": ["s0", "s1"], "說話者": "學員1", "原文": raw,
                 "校對稿": raw.replace("小美", "Amy"), "已確認": True, "校對秒數": None},
                {"id": "T002", "start": 40.0, "end": 44.0, "句子": ["s2"], "說話者": "老師", "原文": "我們繼續。",
                 "校對稿": "我們繼續。", "已確認": False, "校對秒數": None}],
                "學員": {"學員1": {"秒數": 34, "段數": 1, "點名線索": {}, "代號": "Amy", "本名": "小美"}},
                "本名代號": {"小美": "Amy"}}
            (w / "校對" / "段落.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            load = lambda: json.loads((w / "校對" / "段落.json").read_text(encoding="utf-8"))   # noqa: E731

            # 游標放最後面／最前面：切不了，訊息講清楚
            for bad in (0, len(raw) + 5):
                try:
                    turns.split_turn(w, "T001", bad)
                    raise AssertionError("應該要擋下來")
                except ValueError as e:
                    assert "中間" in str(e)

            at = data["段落"][0]["校對稿"].index("好啊")            # 游標放在「好啊」前面＝切在最後一句裡面
            r = turns.split_turn(w, "T001", at)
            assert r["切在"].startswith("一句話裡面") and len(r["新段落"]) == 1
            d1 = load()
            a, b = d1["段落"][0], d1["段落"][1]
            assert a["id"] == "T001" and a["句子"] == ["s0", "s1"] and a["句尾切點"] == {"s1": 5}
            assert a["原文"] == "我先說，我講完了。" and a["校對稿"] == "我先說，我講完了。" and not a["已確認"]
            assert 7.8 <= a["end"] <= 24.0 and b["start"] == a["end"] and b["end"] == 34.0
            assert b["句子"] == [] and b["手動標記"] and b["原文"] == "好啊謝謝小美。" and b["校對稿"] == "好啊謝謝Amy。"
            assert b["說話者"] == "學員1" and len(d1["段落"]) == 3

            # 學員重念的時間格只到切點，文字只有學員自己的話
            items, _ = students.build_items(w)
            mine = [it for it in items if it["段落"] == "T001"]
            assert mine[-1]["slot"][1] == a["end"] and "好啊" not in "".join(it["text"] for it in mine)

            # 併回去：那一句又整句是這一段的
            turns.merge_turn(w, b["id"])
            d2 = load()
            assert "句尾切點" not in d2["段落"][0] and d2["段落"][0]["end"] == 34.0 and len(d2["段落"]) == 2

            # 再切一次，後面那半句改成老師 → 段落拿掉，變成一筆「老師整段」的項目，文字裡的本名換成代號
            d2["段落"][0]["校對稿"] = raw                        # 這次校對稿沒換代號
            (w / "校對" / "段落.json").write_text(json.dumps(d2, ensure_ascii=False), encoding="utf-8")
            r = turns.split_turn(w, "T001", raw.index("好啊"))
            tail = r["新段落"][0]
            res = turns.save_turn(w, tail, {"說話者": "老師"})
            assert res["改成老師重念"] == "NM001"
            assert [t["id"] for t in load()["段落"]] == ["T001", "T002"]
            m = review.load_decisions(w)["人工名字"][0]
            assert m["老師整段"] and m["整段文字"] == "好啊謝謝Amy。" and m["end"] == 34.0
            cands = review.effective_name_candidates(w, [], {})
            plan = nameplan.build_plan(cands, {}, {x["id"]: x for x in sents})
            assert [(g["id"], g["text"], g["slot"], g["候選"]) for g in plan["生成"]] == \
                [("SNM001", "好啊謝謝Amy。", [m["start"], 34.0], ["NM001"])]
            # 卡片上改了字就用改的；選直接消音、或字刪光 → 整段消音；標不用改 → 照原聲
            plan = nameplan.build_plan(cands, {"NM001": {"改稿": "好啊，謝謝 Amy。"}}, {})
            assert plan["生成"][0]["text"] == "好啊，謝謝 Amy。"
            plan = nameplan.build_plan(cands, {"NM001": {"做法": "直接消音"}}, {})
            assert not plan["生成"] and plan["消音"] == [{"候選": "NM001", "start": m["start"], "end": 34.0}]
            plan = nameplan.build_plan(cands, {"NM001": {"tags": ["不是名字"]}}, {})
            assert not plan["生成"] and not plan["消音"] and plan["略過"]
        finally:
            if old is None:
                os.environ.pop("BOOKCLUB_DATA_DIR", None)
            else:
                os.environ["BOOKCLUB_DATA_DIR"] = old


def test_chunk_ranges_and_parallel_stitch_match_sequential():
    assert turns.chunk_ranges(500, 220, 20) == [(0, 220), (200, 420), (400, 500)]
    sents = [{"start": i * 2.0, "end": i * 2.0 + 1, "text": f"第{i}句"} for i in range(500)]
    import threading, time as _t
    active, peak = [0], [0]
    lock = threading.Lock()

    def fake_call(prompt, model):
        with lock:
            active[0] += 1; peak[0] = max(peak[0], active[0])
        _t.sleep(0.2)
        ids = [int(line.split("|")[0]) for line in prompt.split("逐字稿：\n")[1].splitlines()]
        a, b = ids[0], ids[-1]
        mid = (a + b) // 2
        with lock:
            active[0] -= 1
        return json.dumps({"段落": [{"起": a, "迄": mid, "說話者": "老師"},
                                   {"起": mid + 1, "迄": b, "說話者": "學員", "學員編號": "S1"}]})

    out = turns.text_turns(sents, "sonnet", log=lambda *_: None, call=fake_call)
    assert peak[0] >= 2                                     # 真的同時送
    assert out[0]["起"] == 0 and out[-1]["迄"] == 499        # 頭尾完整
    assert all(out[i]["迄"] + 1 == out[i + 1]["起"] for i in range(len(out) - 1))   # 不重疊、不遺漏
    assert out[1]["學員編號"] == "C0-S1"


def _sent(i, label, dur=2.0):
    return {"id": f"s{i}", "start": i * 3.0, "end": i * 3.0 + dur, "text": f"第{i}句", "label": label}


def test_correct_labels_idempotent_and_reverts():
    sents = [_sent(0, "老師"), _sent(1, "不是老師"), _sent(2, "不確定"), _sent(3, "太短", 0.3), _sent(4, "不是老師")]
    text = {"段落": [{"起": 0, "迄": 3, "說話者": "老師", "內容類型": "冥想引導", "句子": ["s0", "s1", "s2", "s3"]},
                     {"起": 4, "迄": 4, "說話者": "學員", "內容類型": "學員分享", "句子": ["s4"]}]}
    c = turns.correct_labels(sents, text)
    assert c["改成老師句數"] == 3 and c["改成老師秒數"] == 4.3
    assert [s["label"] for s in sents] == ["老師", "老師", "老師", "老師", "不是老師"]
    assert sents[1]["聲紋判斷"] == "不是老師" and sents[1]["判斷依據"] == "文字：冥想引導"
    assert sents[4]["判斷依據"] == "聲紋" and c["冥想導讀區域"] == [[0.0, 9.3]]
    again = turns.correct_labels(sents, text)                       # 重複執行：結果一樣
    assert again["改成老師句數"] == 3 and [s["label"] for s in sents][1] == "老師"
    turns.correct_labels(sents, {"段落": []})                        # 文字結果換了：還原成聲紋判斷
    assert [s["label"] for s in sents] == ["老師", "不是老師", "不確定", "太短", "不是老師"]


def test_get_text_turns_caches_and_skips_claude():
    sents = [_sent(i, "老師") for i in range(5)]
    calls = [0]

    def fake_call(prompt, model):
        calls[0] += 1
        return json.dumps({"段落": [{"起": 0, "迄": 4, "說話者": "老師", "內容類型": "導讀"}]})

    with tempfile.TemporaryDirectory() as d:
        w = Path(d)
        a = turns.get_text_turns(w, sents, model="x", log=lambda *_: None, call=fake_call)
        assert a["段落"][0]["句子"] == [f"s{i}" for i in range(5)] and turns.text_turns_path(w).exists()
        b = turns.get_text_turns(w, sents, model="x", log=lambda *_: None, call=fake_call)
        assert calls[0] == 1 and b == a                                # 第二次讀快取，不再呼叫
        turns.get_text_turns(w, sents + [_sent(5, "老師")], model="x", log=lambda *_: None, call=fake_call)
        assert calls[0] == 2                                           # 句數對不上：重新判斷


def _chunk_ids(prompt):
    ids = [int(line.split("|")[0]) for line in prompt.split("逐字稿：\n")[1].splitlines()]
    return ids[0], ids[-1]


def test_empty_chunk_is_retried_then_fails():
    """10-03 第八批（#4）：某一塊回 {"段落": []}（格式對、內容空）算失敗：重送一次成功時段落完整；兩次都空 → 丟例外。"""
    sents = [{"start": i * 2.0, "end": i * 2.0 + 1, "text": f"第{i}句"} for i in range(500)]
    old_wait = turns.RETRY_WAITS_S
    turns.RETRY_WAITS_S = (0, 0, 0)
    try:
        for empty_times, ok in ((1, True), (4, False)):   # 10-03 第九批（#9）：重送 3 次（共送 4 次）都空才失敗
            seen = {}

            def fake_call(prompt, model):
                a, b = _chunk_ids(prompt)
                seen[a] = seen.get(a, 0) + 1
                if a == 200 and seen[a] <= empty_times:          # 第 2 塊回空
                    return json.dumps({"段落": []})
                return json.dumps({"段落": [{"起": a, "迄": b, "說話者": "老師"}]})

            if ok:
                out = turns.text_turns(sents, "x", log=lambda *_: None, call=fake_call)
                assert seen[200] == 2 and turns.turn_gaps(out, 500) == []
                assert out[0]["起"] == 0 and out[-1]["迄"] == 499
            else:
                try:
                    turns.text_turns(sents, "x", log=lambda *_: None, call=fake_call)
                    raise AssertionError("兩次都空還沒出錯")
                except ValueError as e:
                    assert "空" in str(e) and "200–419" in str(e)
    finally:
        turns.RETRY_WAITS_S = old_wait


def test_chunks_saved_and_only_failed_resent():
    """10-03 第九批（#9）：每一塊成功就存檔；有一塊重送完還是失敗 → 其他塊照樣存、整份丟 TurnChunksFailed；
    重跑只送沒成功的那一塊；整份成功後逐塊暫存清掉。重送照 RETRY_WAITS_S 漸進等待（假的 sleep）。"""
    sents = [{"id": f"s{i}", "start": i * 2.0, "end": i * 2.0 + 1, "text": f"第{i}句"} for i in range(500)]
    seen: dict = {}
    bad = {"on": True}

    def fake_call(prompt, model):
        a, b = _chunk_ids(prompt)
        seen[a] = seen.get(a, 0) + 1
        if a == 200 and bad["on"]:
            raise RuntimeError("claude -p 失敗：rate limit（假的）")
        return json.dumps({"段落": [{"起": a, "迄": b, "說話者": "學員", "學員編號": "S1"}]})

    slept: list = []
    with tempfile.TemporaryDirectory() as d:
        w = Path(d)
        cdir = turns.text_chunks_dir(w)
        try:
            turns.text_turns(sents, "x", log=lambda *_: None, call=fake_call, cache_dir=cdir, sleep=slept.append)
            raise AssertionError("第 2 塊一直失敗應該丟例外")
        except turns.TurnChunksFailed as e:
            assert "1／3 塊" in str(e) and "已存下" in str(e) and "rate limit" in str(e)
        assert slept == list(turns.RETRY_WAITS_S) == [5, 20, 60]          # 漸進等待，不是 5 秒一次
        assert seen == {0: 1, 200: 4, 400: 1}
        assert sorted(f.name for f in cdir.iterdir()) == ["00000-00219.json", "00400-00499.json"]
        saved = json.loads((cdir / "00000-00219.json").read_text(encoding="utf-8"))
        assert saved["段落"][0]["學員編號"] == "S1" and saved["句數"] == 500 and len(saved["指紋"]) == 40
        assert "第0句" not in json.dumps(saved, ensure_ascii=False).replace('"段落"', "")   # 不存原文，只存指紋
        # 重跑：只送第 2 塊
        seen.clear(); bad["on"] = False
        logs: list = []
        out = turns.get_text_turns(w, sents, model="x", log=logs.append, call=fake_call)
        assert seen == {200: 1}
        assert sum("沿用上次存下的結果" in x for x in logs) == 2
        assert out["段落"][0]["學員編號"] == "C0-S1" and out["段落"][-1]["學員編號"] == "C2-S1"   # 前綴不會加兩次
        assert turns.turn_gaps(out["段落"], 500) == []
        assert not cdir.exists() and turns.text_turns_path(w).exists()      # 整份存好，逐塊暫存清掉
        # 逐字稿改過（指紋不同）或模型不同：不沿用存下的塊
        cdir.mkdir(parents=True)
        bad["on"] = True
        try:
            turns.text_turns(sents, "x", log=lambda *_: None, call=fake_call, cache_dir=cdir, sleep=lambda s: None)
        except turns.TurnChunksFailed:
            pass
        seen.clear(); bad["on"] = False
        changed = [dict(x) for x in sents]
        changed[5]["text"] = "改過的字"
        turns.text_turns(changed, "x", log=lambda *_: None, call=fake_call, cache_dir=cdir)
        assert seen == {0: 1, 200: 1}                                        # 第 1 塊改過重送，第 3 塊沿用
        seen.clear()
        turns.text_turns(sents, "另一個模型", log=lambda *_: None, call=fake_call, cache_dir=cdir)
        assert seen == {0: 1, 200: 1, 400: 1}
        # 存下的塊有缺口（例如手動改壞）：不沿用
        f = cdir / "00400-00499.json"
        bad_data = json.loads(f.read_text(encoding="utf-8"))
        bad_data["段落"][0]["起"] = 410
        f.write_text(json.dumps(bad_data, ensure_ascii=False), encoding="utf-8")
        seen.clear()
        turns.text_turns(sents, "另一個模型", log=lambda *_: None, call=fake_call, cache_dir=cdir)
        assert seen == {400: 1}


def test_stitch_gap_detected():
    """10-03 第八批（#4）：接起來有缺口 → 丟例外，寫哪幾行、原片幾分。"""
    sents = [{"start": i * 60.0, "end": i * 60.0 + 30, "text": "x"} for i in range(10)]
    out = turns.stitch_chunks([[{"起": 0, "迄": 3}], [], [{"起": 7, "迄": 9}]])
    assert turns.turn_gaps(out, 10) == [(4, 6)]
    try:
        turns.check_turns_cover(out, sents)
        raise AssertionError("有缺口沒抓到")
    except ValueError as e:
        assert "第 4–6 行" in str(e) and "00:04:00" in str(e) and "00:06:30" in str(e)
    turns.check_turns_cover([{"起": 0, "迄": 9}], sents)


def test_get_text_turns_does_not_reuse_cache_with_gap():
    """10-03 第八批（#4）：`段落_文字.json` 有缺口 → 不沿用，重新送。"""
    sents = [_sent(i, "老師") for i in range(5)]
    calls = [0]

    def fake_call(prompt, model):
        calls[0] += 1
        return json.dumps({"段落": [{"起": 0, "迄": 4, "說話者": "老師", "內容類型": "導讀"}]})

    with tempfile.TemporaryDirectory() as d:
        w = Path(d)
        p = turns.text_turns_path(w)
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"段落": [{"起": 0, "迄": 1, "句子": ["s0", "s1"]}], "句數": 5}), encoding="utf-8")
        a = turns.get_text_turns(w, sents, model="x", log=lambda *_: None, call=fake_call)
        assert calls[0] == 1 and a["段落"][0]["迄"] == 4
        assert json.loads(p.read_text(encoding="utf-8"))["段落"][0]["迄"] == 4


def _mark_workdir(d: str, turns_list: list[dict], n_sents: int = 8) -> Path:
    w = Path(d)
    (w / "校對").mkdir(exist_ok=True)
    sents = [{"id": f"s{i}", "start": i * 5.0, "end": i * 5.0 + 4, "text": f"第{i}句。", "label": "老師"}
             for i in range(n_sents)]
    (w / "說話者判斷.json").write_text(json.dumps({"sentences": sents}, ensure_ascii=False), encoding="utf-8")
    full = []
    for tid, ids, who in turns_list:
        idx = [int(x[1:]) for x in ids]
        full.append({"id": tid, "start": idx[0] * 5.0, "end": idx[-1] * 5.0 + 4, "句子": ids, "說話者": who,
                     "原文": "".join(f"第{i}句。" for i in idx), "校對稿": "".join(f"第{i}句。" for i in idx),
                     "已確認": False, "校對秒數": None})
    data = {"段落": full, "學員": {}}
    (w / "校對" / "段落.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return w


def _ids(w: Path) -> list[str]:
    return [t["id"] for t in json.loads((w / "校對" / "段落.json").read_text(encoding="utf-8"))["段落"]]


def test_mark_student_ids_never_repeat():
    """10-03 第八批（#13）：先切開再標、連標兩段沒有句子的短段落、刪一段再標 → 編號都不重複。"""
    with tempfile.TemporaryDirectory() as d:
        # 先切開（T001 → T001、T001m1）再標 T001 中間那一句：切出來的新編號不能跟後面的 T001m1 撞
        w = _mark_workdir(d, [("T001", ["s0", "s1", "s2"], "老師"), ("T001m1", ["s3"], "老師"),
                              ("T002", ["s4", "s5"], "老師")])
        turns.mark_student(w, 5.0, 9.0, "新學員")
        ids = _ids(w)
        assert len(ids) == len(set(ids)) and len(ids) == 5, ids

    with tempfile.TemporaryDirectory() as d:
        # 連標兩段沒有句子的短段落（標的範圍沒有蓋到任何句子的中點）
        w = _mark_workdir(d, [("T001", [f"s{i}" for i in range(8)], "老師")])
        a = turns.mark_student(w, 4.1, 4.6, "新學員")["段落"]
        b = turns.mark_student(w, 9.1, 9.6, "新學員")["段落"]
        assert a == ["U001"] and b == ["U002"]
        # 刪掉 U001 再標：不能再出一個 U002（以前用「有幾段 U＋1」）；刪掉的號碼也不再用
        data = json.loads((w / "校對" / "段落.json").read_text(encoding="utf-8"))
        data["段落"] = [t for t in data["段落"] if t["id"] != "U001"]
        (w / "校對" / "段落.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        c = turns.mark_student(w, 14.1, 14.6, "新學員")["段落"]
        assert c == ["U003"], c
        data = json.loads((w / "校對" / "段落.json").read_text(encoding="utf-8"))
        data["段落"] = [t for t in data["段落"] if t["id"] != "U003"]
        (w / "校對" / "段落.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        assert turns.mark_student(w, 19.1, 19.6, "新學員")["段落"] == ["U004"]
        ids = _ids(w)
        assert len(ids) == len(set(ids)), ids


def test_duplicate_ids_in_old_file_are_repaired():
    """10-03 第八批（#13）：讀到有重複編號的舊檔會修好；生成紀錄已經用舊編號的那一段保留原編號。"""
    with tempfile.TemporaryDirectory() as d:
        w = _mark_workdir(d, [("T001", ["s0"], "學員1"), ("T001m1", ["s1"], "學員1"), ("T001m1", ["s2"], "學員1"),
                              ("U002", ["s3"], "學員1"), ("U002", ["s4"], "學員1")])
        data = json.loads((w / "校對" / "段落.json").read_text(encoding="utf-8"))
        for t in data["段落"]:
            if t["id"] == "U002":
                t["句子"] = []
        (w / "校對" / "段落.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        gen = w / "生成"
        gen.mkdir()
        # 生成紀錄：T001m1 用在第二段（s2，10–14 秒）；U002 用在第一段（s3，15–19 秒）
        (gen / "學員紀錄.json").write_text(json.dumps({"句子": [
            {"id": "T001m1_01", "段落": "T001m1", "slot": [10.0, 14.0]},
            {"id": "U002_01", "段落": "U002", "slot": [15.0, 19.0]}]}, ensure_ascii=False), encoding="utf-8")
        changed = turns.repair_turn_ids(w, log=lambda *_: None)
        got = json.loads((w / "校對" / "段落.json").read_text(encoding="utf-8"))["段落"]
        ids = [t["id"] for t in got]
        assert len(ids) == len(set(ids)), ids
        by_start = {t["start"]: t["id"] for t in got}
        assert by_start[10.0] == "T001m1" and by_start[5.0] == "T001m2"      # 有生成紀錄的保留原編號
        assert by_start[15.0] == "U002" and by_start[20.0] == "U003"
        assert sorted(changed) == [("T001m1", "T001m2"), ("U002", "U003")]
        assert turns.repair_turn_ids(w, log=lambda *_: None) == []            # 修好之後不再改


def test_voice_role_uses_original_voice_label():
    ss = [{**_sent(0, "老師"), "聲紋判斷": "不是老師"}, _sent(1, "不是老師")]
    assert turns._voice_role(ss) == "學員"


def _run_all() -> int:
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, func in tests:
        try:
            func()
            print(f"✓ {name}")
        except Exception as exc:
            failed += 1
            print(f"✗ {name}：{exc!r}")
    print(f"{len(tests) - failed}/{len(tests)} 通過")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
