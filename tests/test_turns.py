"""bookclub/turns.py 的單元測試（純函式與存檔，不呼叫 Claude、不載入聲紋模型）。

獨立可跑：.venv/bin/python tests/test_turns.py
"""

from __future__ import annotations

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
        assert [t["id"] for t in d1["段落"]] == ["T001", "T001b", "T002"]
        assert d1["段落"][0]["校對稿"] == "我先說。" and d1["段落"][1]["start"] == 5.0
        turns.merge_turn(w, "T001b")
        turns.set_person_code(w, "學員2", "Laura")
        d2 = json.loads((w / "校對" / "段落.json").read_text(encoding="utf-8"))
        assert len(d2["段落"]) == 2 and d2["段落"][0]["end"] == 9 and d2["學員"]["學員2"]["代號"] == "Laura"
        p = turns.turns_progress(d2)
        assert p["學員段落數"] == 2 and p["已確認"] == 1


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
