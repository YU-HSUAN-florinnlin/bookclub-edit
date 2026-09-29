"""bookclub/personnames.py（找出這一集提到的所有人名）的測試：Claude 用假的回覆，不真的呼叫。

獨立可跑：.venv/bin/python tests/test_personnames.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import fake_workdir  # noqa: E402

_DATA = Path(tempfile.mkdtemp())
(_DATA / "名冊.csv").write_text("中文名,其他寫法,英文代號,聲線,性別\n小美,美美,Amy,,女\n阿明,,Tom,,男\n", encoding="utf-8")
os.environ["BOOKCLUB_DATA_DIR"] = str(_DATA)

from bookclub import personnames  # noqa: E402


def _sents():
    return [{"id": "a", "start": 0.0, "end": 2.0, "text": "美美你要不要分享", "label": "老師"},
            {"id": "b", "start": 3.0, "end": 6.0, "text": "我跟阿強說過", "label": "不是老師"},
            {"id": "c", "start": 7.0, "end": 9.0, "text": "作者說", "label": "老師"}]


def test_format_lines_marks_speaker_and_index():
    lines = personnames.format_lines(_sents()).splitlines()
    assert lines[0] == "0|老師|0:00:00|美美你要不要分享" and lines[1].startswith("1|學員|")


def test_parse_reply_filters_bad_lines():
    reply = "前言 " + json.dumps({"人名": [
        {"名字": "美美", "其他寫法": ["美美", "小美"], "是誰": "學員", "行號": [0, 99], "說明": "老師點名"},
        {"名字": "阿強", "是誰": "奇怪", "行號": [1]},
        {"名字": "沒有行號", "是誰": "學員", "行號": []}]}, ensure_ascii=False)
    got = personnames.parse_reply(reply, 3)
    assert [p["名字"] for p in got] == ["美美", "阿強"]
    assert got[0]["行號"] == [0] and got[0]["其他寫法"] == ["小美"] and got[1]["是誰"] == "不確定"


def test_match_roster_and_summarize():
    from bookclub import names

    people = [{"名字": "美美", "其他寫法": [], "是誰": "學員", "行號": [0]},
              {"名字": "阿強", "其他寫法": [], "是誰": "其他人", "行號": [1]}]
    personnames.match_roster(people, names.load_roster(_DATA / "名冊.csv"))
    personnames.summarize(people, _sents())
    assert people[0]["名冊本名"] == "小美" and people[0]["名冊代號"] == "Amy" and people[1]["名冊本名"] is None
    assert people[0]["老師說"] == 1 and people[1]["學員說"] == 1 and people[1]["句子"] == ["b"]


def test_find_people_with_fake_claude_and_called_names():
    root = Path(tempfile.mkdtemp()) / "base"
    fake_workdir.make(root)
    w = root / "工作區"
    sents = json.loads((w / "說話者判斷.json").read_text(encoding="utf-8"))["sentences"]
    i = next(k for k, s in enumerate(sents) if "小美" in s["text"])

    def fake(prompt, model):
        assert "小美" in prompt
        return json.dumps({"人名": [{"名字": "小美", "是誰": "學員", "行號": [i]},
                                  {"名字": "阿強", "是誰": "其他人", "行號": [i, i + 1]},
                                  {"名字": "作者", "是誰": "書中人物或作者", "行號": [0]}]}, ensure_ascii=False)

    data = personnames.find_people(w, call=fake, log=lambda m: None)
    assert data["統計"] == {**data["統計"], "名字數": 3, "名冊上有": 1, "名冊上沒有": 2}
    assert personnames.people_path(w).is_file()
    called = personnames.called_names(w)
    assert [c["名字"] for c in called] == ["阿強", "小美"]          # 書中人物不列；依次數排序
    assert called[1]["名冊上有"] and not called[0]["名冊上有"]
    # 已經有結果就沿用，不再呼叫
    assert personnames.find_people(w, call=lambda p, m: 1 / 0, log=lambda m: None)["統計"]["名字數"] == 3


def test_unlisted_decide_adds_to_roster_and_rescans():
    import shutil

    from bookclub import names
    from bookclub import turns as turns_mod
    from bookclub import workdir as wd

    root = Path(tempfile.mkdtemp()) / "base"
    fake_workdir.make(root)
    w = root / "工作區"
    sents = json.loads((w / "說話者判斷.json").read_text(encoding="utf-8"))["sentences"]
    # 名冊只有小美、阿明；逐字稿裡老師提到的「Tom」對到阿明。這裡假裝 Claude 找到一個名冊上沒有的「阿強」（出現在老師的句子）
    i = next(k for k, s in enumerate(sents) if s.get("label") == "老師")
    sents[i]["text"] = "阿強你要不要說說看"
    words = json.loads((w / "transcript" / "merged.json").read_text(encoding="utf-8"))
    for x in words["words"]:
        if sents[i]["start"] - 0.05 <= x["start"] and x["end"] <= sents[i]["end"] + 0.05:
            x["word"] = ""
    ws = [x for x in words["words"] if sents[i]["start"] - 0.05 <= x["start"] and x["end"] <= sents[i]["end"] + 0.05]
    if ws:
        ws[0]["word"] = "阿強你要不要說說看"
    (w / "transcript" / "merged.json").write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    sp = json.loads((w / "說話者判斷.json").read_text(encoding="utf-8"))
    sp["sentences"] = sents
    (w / "說話者判斷.json").write_text(json.dumps(sp, ensure_ascii=False), encoding="utf-8")
    roster_backup = (_DATA / "名冊.csv").read_text(encoding="utf-8")
    try:
        personnames.find_people(w, call=lambda p, m: json.dumps({"人名": [
            {"名字": "阿強", "是誰": "學員", "行號": [i]}, {"名字": "某作者", "是誰": "書中人物或作者", "行號": [0]}]},
            ensure_ascii=False), log=lambda m: None)
        un = personnames.unlisted(w)
        assert [(u["名字"], u["做法"]) for u in un] == [("阿強", None), ("某作者", "不用處理")]
        n0 = len(wd.read_json(wd.names_path(w))["candidates"])
        res = personnames.decide(w, "阿強", "換成代號", "Kevin")
        assert res["加進名冊"] and any(r["寫法"] == "阿強" and r["代號"] == "Kevin" for r in names.load_roster(_DATA / "名冊.csv"))
        # 老師那句「阿強你要不要說說看」補找到了（假資料的名字候選是手寫的，其他名冊名字也可能一起補到）
        new = wd.read_json(wd.names_path(w))["candidates"][n0:]
        assert res["補找到的老師名字"] == len(new) and any(c["代號"] == "Kevin" and c["補找"] for c in new), res
        assert personnames.unlisted(w)[0]["做法"] == "換成代號" and personnames.unlisted(w)[0]["已決定"]
        # 已經在名冊上、再選一個新代號：名冊跟著改（09-29 宇軒：之前這樣按沒反應）
        res = personnames.decide(w, "阿強", "換成代號", "Kyle")
        assert not res["加進名冊"] and any(r["寫法"] == "阿強" and r["代號"] == "Kyle" for r in names.load_roster(_DATA / "名冊.csv"))
        # 「學員是誰」已經選成本名的不列；每次出現都在刪除段落裡的標起來
        assert [u["名字"] for u in personnames.unlisted(w, chosen={"阿強"})] == ["某作者"]
        u = next(x for x in personnames.unlisted(w, cuts=[(sents[i]["start"], sents[i]["end"])]) if x["名字"] == "阿強")
        assert u["都在刪除段落"] and u["刪除段落外次數"] == 0
        # 名冊上沒有的名字在右欄選代號：一樣加進名冊
        r = turns_mod.set_name_code(w, "阿華", "Iris")
        assert any(x["寫法"] == "阿華" for x in names.load_roster(_DATA / "名冊.csv")) and "補找到的老師名字" in r
    finally:
        (_DATA / "名冊.csv").write_text(roster_backup, encoding="utf-8")
        shutil.rmtree(root.parent, ignore_errors=True)


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
