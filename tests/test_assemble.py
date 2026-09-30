"""bookclub/nameplan.py、bookclub/assemble.py 的單元測試：用合成資料，不碰真的影片、
不載入模型。

獨立可跑（不需要 pytest）：.venv/bin/python tests/test_assemble.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import soundfile as sf

from bookclub import assemble, nameplan

SR = assemble.SR


def _cand(start, end, how, sid="s1", matched="小美", code="Amy", **kw):
    return {"start": start, "end": end, "建議做法": how, "sentence_id": sid, "matched_text": matched,
            "name": matched, "canonical": matched, "代號": code, "建議緩衝秒數": 0.05, **kw}


SENTS = {
    "s1": {"id": "s1", "start": 10.0, "end": 14.0, "text": "小美，妳剛剛說的阿明也有提到。"},
    "s2": {"id": "s2", "start": 20.0, "end": 22.0, "text": "好，謝謝大家。"},
}


# ---------- 處理計畫 ----------

def test_plan_three_ways_and_skip():
    cands = [
        _cand(10.0, 10.5, "整句換掉"),
        _cand(11.8, 12.2, "整句換掉", matched="阿明", code="Tom"),   # 同一句 → 合併
        _cand(21.0, 21.4, "直接消音", sid="s2", matched="謝謝", code="X"),
        _cand(30.0, 30.4, "只換名字", sid="s9", matched="小華", code="Lily"),
        _cand(40.0, 40.4, "整句換掉", sid="s1"),                    # 標成不是名字 → 略過
    ]
    plan = nameplan.build_plan(cands, {"3": {"做法": "直接消音"}, "4": {"做法": "只換名字"},
                                       "5": {"tags": ["不是名字"]}}, SENTS)
    whole = [g for g in plan["生成"] if g["id"].startswith("S")]
    assert len(whole) == 1 and whole[0]["text"] == "Amy，妳剛剛說的Tom也有提到。" and whole[0]["候選"] == [1, 2]
    assert whole[0]["slot"] == [10.0, 14.0]
    only = [g for g in plan["生成"] if g["id"].startswith("N")]
    assert only[0]["text"] == "Lily" and abs(only[0]["slot"][0] - 29.95) < 1e-6
    assert plan["消音"] == [{"候選": 3, "start": 20.95, "end": 21.45}]
    assert plan["略過"] == [{"候選": 5, "原因": "不是名字"}]


def test_plan_decision_overrides_and_manual():
    cands = [_cand(10.0, 10.5, "整句換掉", matched="找不到的字", name="", canonical="")]
    plan = nameplan.build_plan(cands, {}, SENTS)
    assert plan["要人處理"][0]["候選"] == 1 and not plan["生成"]
    plan = nameplan.build_plan([_cand(10.0, 10.5, "整句換掉")], {"1": {"tags": [], "做法": "直接消音"}}, SENTS)
    assert plan["消音"] and not plan["生成"]


def test_plan_retime_extends_and_manual_text():
    """09-29 宇軒：逐字稿漏了第二次叫名字 → 改時間把後面納進來、人直接改要重念的句子。"""
    sents = {
        "a": {"id": "a", "start": 0.0, "end": 2.0, "text": "我待會兒想請那一個", "label": "老師"},
        "b": {"id": "b", "start": 2.0, "end": 4.0, "text": "小美分享一下", "label": "老師"},
        "c": {"id": "c", "start": 4.0, "end": 6.0, "text": "很開心", "label": "老師"},
    }
    c = _cand(0.5, 5.0, "整句換掉", sid="b", 改過時間=True)
    plan = nameplan.build_plan([c], {}, sents)
    g = plan["生成"][0]
    assert g["slot"] == [0.0, 6.0] and g["句子"] == ["a", "b", "c"] and g["text"] == "我待會兒想請那一個Amy分享一下很開心"
    plan = nameplan.build_plan([c], {"1": {"改稿": "我待會兒想請Amy，Amy分享一下，很開心"}}, sents)
    g = plan["生成"][0]
    assert g["text"] == "我待會兒想請Amy，Amy分享一下，很開心" and g["改稿"] and g["slot"] == [0.0, 6.0]
    # 沒改時間：照原本整句
    plan = nameplan.build_plan([_cand(2.2, 2.6, "整句換掉", sid="b")], {}, sents)
    assert plan["生成"][0]["slot"] == [2.0, 4.0]


def test_plan_defaults_to_whole_sentence_and_expands_half_sentences():
    sents = {
        "a": {"id": "a", "start": 0.0, "end": 2.0, "text": "上次我們講到，", "label": "老師"},
        "b": {"id": "b", "start": 2.1, "end": 4.0, "text": "小美說她很緊張，", "label": "老師"},
        "c": {"id": "c", "start": 4.0, "end": 6.0, "text": "後來就好了﹖", "label": "老師"},
        "d": {"id": "d", "start": 6.2, "end": 8.0, "text": "我們繼續，", "label": "老師"},
        "e": {"id": "e", "start": 8.0, "end": 9.0, "text": "謝謝，", "label": "不是老師"},
    }
    plan = nameplan.build_plan([_cand(2.2, 2.6, "直接消音", sid="b")], {}, sents)   # 建議做法只是參考
    g = plan["生成"][0]
    assert g["text"] == "上次我們講到，Amy說她很緊張，後來就好了﹖" and g["slot"] == [0.0, 6.0]
    assert g["句子"] == ["a", "b", "c"]
    group = nameplan.expand_sentence(sorted(sents.values(), key=lambda x: x["start"]), 3)
    assert [x["id"] for x in group] == ["d"]                     # 下一段是學員：不接


def test_plan_only_writes_no_real_names_to_sentence_list():
    with tempfile.TemporaryDirectory() as d:
        w = Path(d)
        (w / "名字候選.json").write_text(json.dumps({"candidates": [_cand(10.0, 10.5, "整句換掉")]}, ensure_ascii=False), encoding="utf-8")
        (w / "說話者判斷.json").write_text(json.dumps({"sentences": list(SENTS.values())}, ensure_ascii=False), encoding="utf-8")
        nameplan.make_plan(w)
        text = nameplan.sentences_path(w).read_text(encoding="utf-8")
        assert "Amy" in text and "小美" not in text


# ---------- 剪輯決策 ----------

def test_build_edl_longer_wins_and_missing_generation_warns():
    plan = {
        "生成": [{"id": "S001", "text": "Amy…", "slot": [10.0, 14.0], "候選": [1]},
                 {"id": "S009", "text": "…", "slot": [50.0, 52.0], "候選": [9]}],
        "消音": [{"候選": 2, "start": 11.0, "end": 11.5}, {"候選": 3, "start": 21.0, "end": 21.4}],
    }
    log = {"句子": [{"id": "S001", "放回時間格": {"檔案": "生成/老師/S001_放回時間格.wav"}, "要人聽": False}]}
    edits, warns = assemble.build_edl(plan, log)
    assert [(e["類型"], e["start"]) for e in edits] == [("換聲音", 10.0), ("消音", 21.0)]
    assert any("S009" in w for w in warns) and any("[2]" in w for w in warns)


# ---------- 聲音處理 ----------

def _tone(sec, amp=0.3, f=220):
    t = np.arange(int(sec * SR)) / SR
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)


def test_apply_edits_replace_and_mute():
    # 0–5 秒講話、5–6 秒很小聲（底噪）、6–10 秒講話
    x = np.concatenate([_tone(5.0), _tone(1.0, amp=0.001), _tone(4.0)])
    edits = [
        {"類型": "換聲音", "start": 1.0, "end": 3.0},
        {"類型": "消音", "start": 7.0, "end": 8.0},
    ]
    clip = _tone(1.5, amp=0.05, f=440)   # 比時間格短、比較小聲
    y = assemble.apply_edits(x, edits, {0: clip})
    assert len(y) == len(x)
    mid = y[int(1.5 * SR):int(2.0 * SR)]
    assert abs(assemble.active_rms(mid) - assemble.active_rms(x[SR:3 * SR])) < 0.02   # 音量對齊
    assert np.abs(y[int(2.6 * SR):int(2.98 * SR)]).max() < 1e-6   # 生成比較短 → 後面補空白
    muted = y[int(7.05 * SR):int(7.95 * SR)]
    assert np.abs(muted).max() < 0.01                                 # 消音處換成底噪
    assert np.array_equal(y[: int(0.99 * SR)], x[: int(0.99 * SR)])   # 沒動到的地方不變


def test_render_audio_end_to_end():
    if not shutil.which("ffmpeg"):
        return
    with tempfile.TemporaryDirectory() as d:
        w = Path(d)
        video = w / "原片.wav"
        sf.write(str(video), np.concatenate([_tone(12.0), _tone(1.0, amp=0.001), _tone(10.0)]), SR)
        (w / "生成" / "老師").mkdir(parents=True)
        sf.write(str(w / "生成" / "老師" / "S001_放回時間格.wav"), _tone(4.0, amp=0.1, f=330)[: 4 * 24000], 24000)
        plan = {"生成": [{"id": "S001", "text": "Amy，妳好。", "slot": [2.0, 6.0], "候選": [1]}],
                "消音": [{"候選": 2, "start": 15.0, "end": 15.5}], "要人處理": []}
        (w / "生成" / "名字處理計畫.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        (w / "生成" / "老師紀錄.json").write_text(json.dumps({"句子": [
            {"id": "S001", "放回時間格": {"檔案": "生成/老師/S001_放回時間格.wav"}, "要人聽": True}]}, ensure_ascii=False),
            encoding="utf-8")
        s = assemble.render_audio(w, video=video)
        new, sr = sf.read(str(w / "輸出" / "新聲音軌.wav"))
        assert sr == SR and abs(len(new) / SR - 23.0) < 0.01
        assert s["換聲音"] == 1 and s["消音"] == 1 and s["要人聽"] == 1
        assert len(list((w / "輸出" / "處理前後").glob("*.wav"))) == 4
        page = (w / "輸出" / "處理前後.html").read_text(encoding="utf-8")
        assert "Amy" in page and "要人聽" in page


def test_subtract_and_add_local_mutes():
    assert assemble.subtract(0, 10, [(2, 3), (5, 6)]) == [(0, 2), (3, 5), (6, 10)]
    assert assemble.subtract(0, 10, [(0, 10)]) == []
    edits = [{"類型": "換聲音", "start": 4.0, "end": 6.0, "候選": [1]}]
    mutes = [{"id": "M001", "start": 3.0, "end": 8.0, "方式": "墊底噪"},      # 中間被換聲音蓋到
             {"id": "M002", "start": 20.0, "end": 22.0, "方式": "霧化"},       # 整段在刪除段落裡
             {"id": "M003", "start": 29.0, "end": 31.0, "方式": "霧化"}]       # 一半在刪除段落裡
    out, warns = assemble.add_local_mutes(edits, mutes, cuts=[(19.0, 30.0)])
    got = [(e["id"] if "id" in e else e["類型"], e["start"], e["end"]) for e in out]
    assert got == [("M001", 3.0, 4.0), ("換聲音", 4.0, 6.0), ("M001", 6.0, 8.0), ("M003", 30.0, 31.0)], got
    assert any("M001" in w for w in warns) and any("M002" in w for w in warns)
    assert [e["霧化"] for e in out if e.get("id") == "M003"] == [True]


def test_overlap_teacher_items_in_plan():
    """09-30：重疊選「生成老師聲音」→ 老師那一整句排進生成清單；同一句已經因為名字要重念就併在一起；找不到句子的記下來。"""
    sents = {
        "s1": {"id": "s1", "start": 10.0, "end": 14.0, "text": "小美，妳剛剛說的阿明也有提到。", "label": "老師"},
        "s2": {"id": "s2", "start": 20.0, "end": 22.0, "text": "今天我們先從呼吸開始，", "label": "老師"},
        "s3": {"id": "s3", "start": 22.2, "end": 25.0, "text": "慢慢把注意力放回來。", "label": "老師"},
        "s4": {"id": "s4", "start": 40.0, "end": 43.0, "text": "我這週練習的時候很常分心。", "label": "學員"},
    }
    ordered = sorted(sents.values(), key=lambda x: x["start"])
    grp = nameplan.overlap_sentence({"start": 23.0, "end": 23.5}, ordered)
    assert [g["id"] for g in grp] == ["s2", "s3"]                       # 半句照標點擴成整句
    assert nameplan.overlap_sentence({"start": 41.0, "end": 41.4}, ordered) is None   # 這裡只有學員的話
    # 重疊的地方逐句標籤常常判成「不是老師」：照段落判斷（整段是老師在講）就找得到
    mixed = [dict(s, label="不是老師") if s["id"] == "s3" else s for s in ordered]
    assert nameplan.overlap_sentence({"start": 23.0, "end": 23.5}, mixed) is None   # 只看逐句標籤會找不到
    by_turn = nameplan.teacher_by_turns([{"start": 0.0, "end": 30.0, "說話者": "老師"}, {"start": 39.0, "end": 45.0, "說話者": "學員1"}])
    assert [g["id"] for g in nameplan.overlap_sentence({"start": 23.0, "end": 23.5}, mixed, by_turn)] == ["s2", "s3"]
    assert nameplan.overlap_sentence({"start": 41.0, "end": 41.4}, mixed, by_turn) is None
    plan = nameplan.build_plan([_cand(10.0, 10.5, "整句換掉")], {}, sents)
    picks = [{"id": "O12.00", "start": 12.0, "end": 12.4, "老師整句改稿": ""},          # 跟名字同一句 → 併在一起
             {"id": "O23.00", "start": 23.0, "end": 23.5, "老師整句改稿": "今天先從呼吸開始，慢慢把注意力放回來。"},
             {"id": "O41.00", "start": 41.0, "end": 41.4, "老師整句改稿": ""}]          # 找不到老師的句子
    nameplan.add_overlap_items(plan, picks, sents)
    gen = {g["id"]: g for g in plan["生成"]}
    assert len(gen) == 2 and plan["重疊沒句子"] == ["O41.00"]
    assert gen["S001"]["重疊項目"] == ["O12.00"] and gen["S001"]["text"].startswith("Amy")   # 文字用名字那一筆的
    v = gen["V0002300"]
    assert v["slot"] == [20.0, 25.0] and v["候選"] == [] and v["重疊項目"] == ["O23.00"]
    assert v["text"] == "今天先從呼吸開始，慢慢把注意力放回來。" and v["改稿"] is True
    # 生成好之後：剪輯決策帶著重疊項目；重疊消音讓給這一筆，算成處理好了
    tlog = {"句子": [{"id": "V0002300", "放回時間格": {"檔案": "生成/老師/V0002300.wav"}}]}
    edl, _ = assemble.build_edl(plan, tlog)
    assert [e.get("重疊項目") for e in edl] == [["O23.00"]]
    o = {"id": "O23.00", "start": 23.0, "end": 23.5, "做法": "只留老師"}
    out, _ = assemble.add_local_mutes(edl, assemble.overlap_mutes([o]), [])
    assert not [e for e in out if e.get("重疊")]
    res = assemble.overlap_outcome(o, out, [])
    assert res["沒處理秒"] == 0 and "換聲音時一起換掉" in res["處理"]
    # 還沒生成：照消音處理，而且說清楚為什麼
    out, _ = assemble.add_local_mutes([], assemble.overlap_mutes([o]), [])
    assert "還沒生成" in assemble.overlap_outcome(o, out, [])["處理"]


def test_overlap_mutes_and_outcome():
    """09-30：重疊處除了「不用改」一律消音；被換聲音蓋到的讓給那一筆；還留著原聲的算得出來。"""
    ovs = [{"id": "O10.00", "start": 10.0, "end": 10.5, "做法": "只留老師原聲學員消音"},   # 老師段落裡的附和 → 消音
           {"id": "O20.00", "start": 20.0, "end": 20.4, "做法": "只留學員"},                # 整個在學員重念裡 → 不另外消
           {"id": "O30.00", "start": 29.8, "end": 30.3, "做法": "兩邊都重生成"},            # 一半在學員重念裡 → 剩下的消音
           {"id": "O40.00", "start": 40.0, "end": 40.5, "做法": "不用改"},                  # 保留原聲 → 不動
           {"id": "O50.00", "start": 50.0, "end": 50.5, "做法": "只留老師"}]                # 整個在刪除段落裡
    edits = [{"類型": "學員重念", "id": "T001_1", "start": 19.0, "end": 22.0},
             {"類型": "學員重念", "id": "T002_1", "start": 30.0, "end": 33.0}]
    cuts = [(49.0, 52.0)]
    mutes = assemble.overlap_mutes(ovs)
    assert [m["重疊"] for m in mutes] == ["O10.00", "O20.00", "O30.00", "O50.00"]
    out, warns = assemble.add_local_mutes(edits, mutes, cuts)
    assert warns == []   # 被學員重念蓋到、落在刪除段落裡都是正常的，不吵
    got = [(e["重疊"], round(e["start"], 2), round(e["end"], 2)) for e in out if e.get("重疊")]
    assert got == [("O10.00", 10.0, 10.5), ("O30.00", 29.8, 30.0)], got
    res = {o["id"]: assemble.overlap_outcome(o, out, cuts) for o in ovs}
    assert all(r["沒處理秒"] == 0 for r in res.values()), res
    assert "消音 0.50 秒" in res["O10.00"]["處理"]
    assert "T001_1" in res["O20.00"]["處理"] and "消音" not in res["O20.00"]["處理"]
    assert "消音 0.20 秒" in res["O30.00"]["處理"] and "T002_1" in res["O30.00"]["處理"] and "還沒做" in res["O30.00"]["處理"]
    assert "不用改" in res["O40.00"]["處理"] and "刪除" in res["O50.00"]["處理"]
    # 消音沒加進去（例如之後有人改壞）→ 算得出還留著原聲
    bad = assemble.overlap_outcome(ovs[0], edits, cuts)
    assert bad["沒處理秒"] == 0.5 and "沒處理" in bad["處理"]
    assert assemble.overlap_outcome(ovs[2], edits, cuts)["沒處理秒"] == 0.2


def test_render_audio_applies_local_mute():
    """09-29：第 3 步標的局部消音，組裝時真的消掉；長度不變；處理紀錄有這一筆。"""
    if not shutil.which("ffmpeg"):
        return
    from bookclub import proclog

    with tempfile.TemporaryDirectory() as d:
        w = Path(d)
        video = w / "原片.wav"
        sf.write(str(video), np.concatenate([_tone(10.0), _tone(1.0, amp=0.001), _tone(10.0)]), SR)
        (w / "生成").mkdir(parents=True)
        (w / "生成" / "名字處理計畫.json").write_text(json.dumps({"生成": [], "消音": [], "要人處理": []}), encoding="utf-8")
        (w / "覆核").mkdir()
        (w / "覆核" / "覆核決定.json").write_text(json.dumps({"局部消音": [
            {"id": "M001", "start": 15.0, "end": 17.0, "方式": "墊底噪", "狀態": "消音"},
            {"id": "M002", "start": 3.0, "end": 4.0, "方式": "墊底噪", "狀態": "還原"}]}, ensure_ascii=False), encoding="utf-8")
        s = assemble.render_audio(w, video=video)
        new, _ = sf.read(str(w / "輸出" / "新聲音軌.wav"))
        orig, _ = sf.read(str(w / "輸出" / "原聲音軌.wav"))
        assert abs(len(new) - len(orig)) == 0
        rms = lambda x: float(np.sqrt(np.mean(x ** 2)))  # noqa: E731
        assert rms(new[int(15.2 * SR):int(16.8 * SR)]) < rms(orig[int(15.2 * SR):int(16.8 * SR)]) * 0.1
        assert rms(new[int(3.2 * SR):int(3.8 * SR)]) > rms(orig[int(3.2 * SR):int(3.8 * SR)]) * 0.9   # 還原的不動
        assert s["局部消音"] == 1
        recs = proclog.load(w)["紀錄"]
        mute = [r for r in recs if r["類型"] == "局部消音"]
        assert len(mute) == 1 and mute[0]["覆核項目"] == ["局部消音:M001"]
        assert not proclog.load(w)["未登記的變動"]


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
