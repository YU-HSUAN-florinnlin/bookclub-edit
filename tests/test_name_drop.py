"""10-17 #245：老師句子裡的名字沒有代號、被拿掉（換成空的）時，旁邊多出來的空格一起清掉。

以前「小安 來」→「 來」（開頭留空白）、「我跟 小安 說」→「我跟  說」（中間多兩個空格）；
現在句首句尾的空格去掉、句中直接接起來，兩邊都是英文字母或數字才留一個空格。換成代號時空格照舊。

10-17 #247：第 3 步卡片上名字被拿掉的地方標「（拿掉）」（只在畫面上，不進要念的文字、不送後端）；
學員段落建議那一行寫「「小安」拿掉了」，不再出現「換成代號：；」的空冒號。

全部是合成資料（假名字＋自編句子）。獨立可跑：.venv/bin/python tests/test_name_drop.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import re
import shutil
import subprocess
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


def _make(says=("小美 你好，", "我跟 阿明 說過了。")) -> Path:
    import fake_workdir

    w = fake_workdir.make(Path(tempfile.mkdtemp()) / "base")
    sp = json.loads((w / "說話者判斷.json").read_text(encoding="utf-8"))
    sents = sp["sentences"]
    by = {s["id"]: s for s in sents}
    d = wd.read_json(wd.names_path(w))
    texts = {}
    for c, text in zip(d["candidates"], says):
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


# ---------- 10-17 #247：卡片標「（拿掉）」 ----------

def test_replace_each_at_drop_positions():
    """後端純函式：拿掉的位置（新文字裡的索引）；同一張卡的另一處換成代號，前面記的位置跟著移。"""
    got = review._replace_each_at("小安 跟 阿德 說", _drop("小安"), [{**_drop("阿德"), "代號": "Tom"}])
    assert got == ("跟 Tom 說", [0]), got
    got = review._replace_each_at("阿德 跟 小安 說", _drop("小安"), [{**_drop("阿德"), "代號": "Tom"}])
    assert got == ("Tom 跟說", [5]), got
    got = review._replace_each_at("我跟 小安 和 小安 說", _drop("小安"), [_drop("小安")])
    assert got == ("我跟和說", [2, 3]), got
    assert review._replace_each_at("我跟 Amy 說", _drop("小安"), []) is None
    _t, ch = review.replace_real_names("我跟 小安 說", [{"寫法": "小安", "代號": "", "拿掉": True}])
    assert ch == [{"原字": "小安", "換成": "", "位置": 2}], ch   # 換成空的＝拿掉，畫面照這個標


_TABLE = [{"寫法": "小安", "代號": "", "拿掉": True}, {"寫法": "阿德", "代號": "Tom"}]


def _page(only_drop: bool) -> dict:
    """假工作區：老師句子「我跟 阿明 說過了，小安 也是。」（阿明沒有代號）、學員段落提到小安（名冊那一道拿掉）。"""
    w = _make(("小美 你好，", "我跟 阿明 說過了，小安 也是。"))
    tp = w / "校對" / "段落.json"
    td = json.loads(tp.read_text(encoding="utf-8"))
    t = next(x for x in td["段落"] if x["說話者"] != "老師")
    t["校對稿"] = "我覺得 小安 說得很好" if only_drop else "我覺得 小安 跟 阿德 說得很好"
    tp.write_text(json.dumps(td, ensure_ascii=False), encoding="utf-8")
    real = review.replace_table
    review.replace_table = lambda workdir=None: _TABLE
    try:
        return review.page_data(w)
    finally:
        review.replace_table = real


def test_backend_card_fields():
    data = _page(True)
    stu = next(x for x in data["項目"] if x["類型"] == "學員段落" and "小安" in x["校對稿"])
    assert stu["建議稿"] == "我覺得說得很好", stu["建議稿"]
    assert stu["換過的字"] == [{"原字": "小安", "換成": "", "位置": 3}], stu["換過的字"]
    why = stu["建議"]["原因"]
    assert why.startswith("「小安」拿掉了；") and "：；" not in why and "代號：" not in why, why
    both = next(x for x in _page(False)["項目"] if x["類型"] == "學員段落" and "小安" in x["校對稿"])
    assert both["建議"]["原因"].startswith("已把名冊上的名字換成代號：Tom；「小安」拿掉了；"), both["建議"]["原因"]
    name = next(x for x in data["項目"] if x["類型"] == "名字" and x["matched_text"] == "阿明")
    w = name["整句"]
    say, at = w["換成代號"], w["拿掉的位置"]   # 重念範圍用逐字時間的字（沒有空格、標點）
    assert say.startswith("我跟說過了") and "阿明" not in say and "（拿掉）" not in say, w
    assert at == [2], w
    assert "小安" not in w["實際會念"] and "（拿掉）" not in w["實際會念"] and w["實際會念拿掉"], w
    return data


NODE = shutil.which("node")
WEB = REPO_ROOT / "bookclub" / "web"
HARNESS = REPO_ROOT / "tests" / "web_harness.js"


def test_web_card_shows_drop_mark_only_on_screen():
    """網頁：卡片 HTML 有「（拿掉）」；要念的字（輸入框、送出去的改稿）沒有「（拿掉）」；建議行沒有空冒號。"""
    if not NODE:
        print("（沒有 node，略過）")
        return
    data = test_backend_card_fields()
    stu = next(x for x in data["項目"] if x["類型"] == "學員段落" and "小安" in x["校對稿"])
    name = next(x for x in data["項目"] if x["類型"] == "名字" and x["matched_text"] == "阿明")
    keys = [f"學員段落:{stu['id']}", f"名字:{name['id']}"]
    skel = ["rv-right", "rv-lower", "rv-tl", "rv-progress", "rv-legend", "rv-io", "rv-export-msg", "rv-busy",
            "rv-prep-btn", "rv-go4", "rv-settings", "rv-codes", "rv-clock", "rv-head"]   # 跟 test_cards_snapshot 一樣的頁面骨架
    pre = (f"H.skeleton = new Set({json.dumps(skel)});\n"
           f"const DATA = {json.dumps(data, ensure_ascii=False)};\n"
           "H.apiGetData['/api/review'] = DATA;\n"
           "H.api['*'] = {'段落': {'校對稿': 'x', '問老師': false}, '決定': {'做法': '整句換掉', 'tags': [], 'note': ''}};\n"
           "function setup() { rv.data = H.clone(DATA); if (typeof rvUseCardCtx === 'function') rvUseCardCtx();\n"
           "  rv.video = H.makeEl('video', {id: 'rv-video'}, ''); rv.video.paused = true; }\n")
    scen = []
    for k in keys:
        scen.append({"名稱": k, "程式": f"""
          setup(); rv.cur = {json.dumps(k, ensure_ascii=False)}; rv.open = true; rvRenderCard();
          const card = document.getElementById("rv-right").innerHTML;
          // 輸入框沒改就離開：不送；改一個字再離開：送出去的字照輸入框（沒有「（拿掉）」）
          const ta = document.getElementById("rv-text") || document.getElementById("rv-namesay");
          ta.value = ta.value + "啊";
          for (const l of H.listeners.filter((x) => x.el === ta && x.type === "blur")) await l.fn({{target: ta}});
          return {{card, posts: H.log.filter((x) => x[0] === "POST" || x[0] === "apiPost")}};"""})
    d = Path(tempfile.mkdtemp())
    (d / "spec.json").write_text(json.dumps({"前置": pre, "情境": scen}, ensure_ascii=False), encoding="utf-8")
    files = ",".join(str(WEB / f) for f in ("cards.js", "review.js") if (WEB / f).is_file())
    r = subprocess.run([NODE, str(HARNESS), files, str(d / "spec.json")], capture_output=True, text=True, check=True)
    got = json.loads(r.stdout)
    for k in keys:
        v = got[k]
        assert isinstance(v, dict) and "錯誤" not in v, (k, v)
        card = v["card"]
        assert "（拿掉）" in card, (k, card)
        for ta in re.findall(r"<textarea[^>]*>(.*?)</textarea>", card, re.S):
            assert "（拿掉）" not in ta, (k, ta)
        assert "：；" not in card and "代號：；" not in card, (k, card)
        sent = json.dumps(v["posts"], ensure_ascii=False)
        assert v["posts"] and "（拿掉）" not in sent, (k, v["posts"])
    assert "「小安」拿掉了" in got[keys[0]]["card"], got[keys[0]]["card"]
    assert re.search(r"會念成</span>我跟<mark class=\"rv-drop\"[^>]*>（拿掉）</mark>說過了", got[keys[1]]["card"]), got[keys[1]]["card"]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
