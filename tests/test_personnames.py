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


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
