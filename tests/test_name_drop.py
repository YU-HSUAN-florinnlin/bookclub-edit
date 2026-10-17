"""10-17 #245：老師句子裡的名字沒有代號、被拿掉（換成空的）時，旁邊多出來的空格一起清掉。

以前「小安 來」→「 來」（開頭留空白）、「我跟 小安 說」→「我跟  說」（中間多兩個空格）；
現在句首句尾的空格去掉、句中直接接起來，兩邊都是英文字母或數字才留一個空格。換成代號時空格照舊。

全部是合成資料（假名字＋自編句子）。獨立可跑：.venv/bin/python tests/test_name_drop.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from bookclub import nameplan, review  # noqa: E402
from bookclub import workdir as wd  # noqa: E402


def _drop(word: str) -> dict:
    return {"matched_text": word, "name": word, "canonical": word, "代號": ""}


def test_drop_at_start():
    assert nameplan.replace_name("小安 來，我們開始", _drop("小安")) == "來，我們開始"


def test_drop_in_middle():
    assert nameplan.replace_name("我跟 小安 說過了", _drop("小安")) == "我跟說過了"
    assert nameplan.replace_name("我跟小安說過了", _drop("小安")) == "我跟說過了"


def test_drop_at_end():
    assert nameplan.replace_name("謝謝 小安 ", _drop("小安")) == "謝謝"


def test_drop_between_english_keeps_one_space():
    assert nameplan.replace_name("Hi 小安 Bob 你好", _drop("小安")) == "Hi Bob 你好"
    assert nameplan.replace_name("我跟 小安 Bob 說", _drop("小安")) == "我跟Bob 說"   # 一邊是中文：直接接起來


def test_code_keeps_spaces():
    c = {"matched_text": "小安", "name": "小安", "canonical": "小安", "代號": "Amy"}
    assert nameplan.replace_name("我跟 小安 說", c) == "我跟 Amy 說"
    assert nameplan.replace_name("小安 來", c) == "Amy 來"


def test_replace_name_at_positions():
    new, at, end = nameplan.replace_name_at("我跟 小安 說", _drop("小安"))
    assert (new, at, end) == ("我跟說", 2, 6), (new, at, end)
    new, at, end = nameplan.replace_name_at("我跟 小安 說", {**_drop("小安"), "代號": "Amy"})
    assert (new, at, end) == ("我跟 Amy 說", 3, 5)


def test_real_names_table_drop():
    table = [{"寫法": "小安", "代號": "", "拿掉": True}, {"寫法": "阿德", "代號": "Tom"}]
    for src, want in [("小安 來", "來"), ("我跟 小安 說", "我跟說"), ("謝謝 小安", "謝謝"),
                      ("Hi 小安 Bob", "Hi Bob"), ("我跟 阿德 說", "我跟 Tom 說"), ("阿德 跟 小安 說", "Tom 跟說")]:
        got, _ = review.replace_real_names(src, table)
        assert got == want, (src, got, want)
    got, ch = review.replace_real_names("我跟 阿德 和 小安 說", table)
    assert got == "我跟 Tom 和說", got
    assert [(c["原字"], c["換成"], c["位置"]) for c in ch] == [("阿德", "Tom", 3), ("小安", "", 8)], ch
    assert got[ch[0]["位置"]:ch[0]["位置"] + 3] == "Tom"


def _make() -> Path:
    import fake_workdir

    w = fake_workdir.make(Path(tempfile.mkdtemp()) / "base")
    sp = json.loads((w / "說話者判斷.json").read_text(encoding="utf-8"))
    sents = sp["sentences"]
    by = {s["id"]: s for s in sents}
    d = wd.read_json(wd.names_path(w))
    texts = {}
    for c, text in zip(d["candidates"], ["小美 你好，", "我跟 阿明 說過了。"]):
        by[c["sentence_id"]]["text"] = text
        texts[c["sentence_id"]] = text
        c.update({"代號": "", "sentence": text, "建議做法": "整句換掉"})
    (w / "說話者判斷.json").write_text(json.dumps(sp, ensure_ascii=False), encoding="utf-8")
    m = json.loads((w / "transcript" / "merged.json").read_text(encoding="utf-8"))
    for s in m["sentences"]:
        s["text"] = texts.get(s["id"], s["text"])
    m["words"] = fake_workdir._words(sents)
    (w / "transcript" / "merged.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    wd.write_json(wd.names_path(w), d)
    return w


def test_compute_plan_text_has_no_extra_spaces():
    w = _make()
    plan = nameplan.compute_plan(w)
    gens = [g for g in plan["生成"] if str(g.get("id", "")).startswith("S")]
    assert gens, plan
    for g in gens:
        t = g["text"]
        assert t == t.strip() and not re.search(r"\s\s", t), repr(t)
        assert "小美" not in t and "阿明" not in t, t
    joined = "".join(g["text"] for g in gens)
    assert "我跟說過了" in joined, [g["text"] for g in gens]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
