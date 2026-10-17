"""10-17 #245：老師句子裡的名字沒有代號、被拿掉（換成空的）時，在句首、句尾留下的空白去掉。

以前「小安 來」→「 來」（開頭留空白）、「謝謝 小安」→「謝謝 」（結尾留空白）；現在句首、句尾的空白去掉
（含全形空白、tab、換行）。10-18 宇軒：句中的空白一律不動（「我跟 小安 說」→「我跟  說」照原樣），
所以兩邊不會接成名冊上另一個名字、英文代號也不會黏在一起。換成代號時空格照舊。

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
    assert nameplan.replace_name("小安\u3000\t來", _drop("小安")) == "來"   # 全形空白、tab 也去掉
    assert nameplan.replace_name(" \n小安 來", _drop("小安")) == "來"


def test_drop_in_middle_keeps_spaces():
    assert nameplan.replace_name("我跟 小安 說過了", _drop("小安")) == "我跟  說過了"   # 句中照原樣
    assert nameplan.replace_name("我跟小安說過了", _drop("小安")) == "我跟說過了"
    assert nameplan.replace_name("Hi 小安 Bob 你好", _drop("小安")) == "Hi  Bob 你好"


def test_drop_at_end():
    assert nameplan.replace_name("謝謝 小安 ", _drop("小安")) == "謝謝"
    assert nameplan.replace_name("謝謝\u3000小安\n", _drop("小安")) == "謝謝"


def test_middle_drop_does_not_join_or_glue():
    """句中不接起來：「王 小安 明」不會變成名冊上的「王明」；「A 小安 阿德 B」不會黏成「ATom」。"""
    assert nameplan.replace_name("王 小安 明", _drop("小安")) == "王  明"
    table = [{"寫法": "小安", "代號": "", "拿掉": True}, {"寫法": "王明", "代號": "Ben"}, {"寫法": "阿德", "代號": "Tom"},
             {"寫法": "阿明", "代號": "Ben"}, {"寫法": "小華", "代號": "", "拿掉": True}]
    for src, want in [("王 小安 明", "王  明"), ("A 小安 阿德 B", "A  Tom B"), ("阿德 小安 阿明", "Tom  Ben"),
                      ("王 小安 小華 明", "王   明")]:
        got, _ = review.replace_real_names(src, table)
        assert got == want and "王明" not in got and "ATom" not in got and "TomBen" not in got, (src, got)
    c = review._replace_each_at("A 小安 阿德 B", _drop("小安"), [{**_drop("阿德"), "代號": "Tom"}])
    assert c == ("A  Tom B", [2]), c


def test_code_keeps_spaces():
    c = {"matched_text": "小安", "name": "小安", "canonical": "小安", "代號": "Amy"}
    assert nameplan.replace_name("我跟 小安 說", c) == "我跟 Amy 說"
    assert nameplan.replace_name("小安 來", c) == "Amy 來"


def test_replace_name_at_positions():
    assert nameplan.replace_name_at("我跟 小安 說", _drop("小安")) == ("我跟  說", 3)
    assert nameplan.replace_name_at("小安 說", _drop("小安")) == ("說", 0)
    assert nameplan.replace_name_at("謝謝 小安", _drop("小安")) == ("謝謝", 2)
    assert nameplan.replace_name_at("我跟 小安 說", {**_drop("小安"), "代號": "Amy"}) == ("我跟 Amy 說", 3)


def test_real_names_table_drop():
    table = [{"寫法": "小安", "代號": "", "拿掉": True}, {"寫法": "阿德", "代號": "Tom"}]
    for src, want in [("小安 來", "來"), ("我跟 小安 說", "我跟  說"), ("謝謝 小安", "謝謝"), ("\t小安\u3000來", "來"),
                      ("Hi 小安 Bob", "Hi  Bob"), ("我跟 阿德 說", "我跟 Tom 說"), ("阿德 跟 小安 說", "Tom 跟  說")]:
        got, _ = review.replace_real_names(src, table)
        assert got == want, (src, got, want)
    got, ch = review.replace_real_names("我跟 阿德 和 小安 說", table)
    assert got == "我跟 Tom 和  說", got
    assert [(c["原字"], c["換成"], c["位置"]) for c in ch] == [("阿德", "Tom", 3), ("小安", "", 9)], ch
    assert got[ch[0]["位置"]:ch[0]["位置"] + 3] == "Tom"
    got, ch = review.replace_real_names("謝謝 小安 小安", table)   # 結尾連兩個：前面記的位置不超過字尾
    assert got == "謝謝" and [c["位置"] for c in ch] == [2, 2], (got, ch)
    got, ch = review.replace_real_names("小安 小安 來", table)
    assert got == "來" and [c["位置"] for c in ch] == [0, 0], (got, ch)


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


def test_compute_plan_text_has_no_edge_spaces():
    """`compute_plan` 出來的 `生成`：名字在句首、句尾被拿掉不留頭尾空白。"""
    w = _make(("小美 你好，", "我說過了，謝謝 阿明 "))
    plan = nameplan.compute_plan(w)
    gens = [g for g in plan["生成"] if str(g.get("id", "")).startswith("S")]
    assert gens, plan
    for g in gens:
        t = g["text"]
        assert t == t.strip(), repr(t)
        assert "小美" not in t and "阿明" not in t, t
    joined = "".join(g["text"] for g in gens)
    assert "謝謝" in joined, [g["text"] for g in gens]


# ---------- 10-17 #247：卡片標「（拿掉）」 ----------

def test_replace_each_at_drop_positions():
    """後端純函式：拿掉的位置（新文字裡的索引）；同一張卡的另一處換成代號，前面記的位置跟著移。"""
    got = review._replace_each_at("小安 跟 阿德 說", _drop("小安"), [{**_drop("阿德"), "代號": "Tom"}])
    assert got == ("跟 Tom 說", [0]), got
    got = review._replace_each_at("阿德 跟 小安 說", _drop("小安"), [{**_drop("阿德"), "代號": "Tom"}])
    assert got == ("Tom 跟  說", [6]), got
    got = review._replace_each_at("我跟 小安 和 小安 說", _drop("小安"), [_drop("小安")])
    assert got == ("我跟  和  說", [3, 6]), got
    got = review._replace_each_at("謝謝 小安 小安", _drop("小安"), [_drop("小安")])
    assert got == ("謝謝", [2, 2]), got
    assert review._replace_each_at("我跟 Amy 說", _drop("小安"), []) is None
    _t, ch = review.replace_real_names("我跟 小安 說", [{"寫法": "小安", "代號": "", "拿掉": True}])
    assert ch == [{"原字": "小安", "換成": "", "位置": 3}], ch   # 換成空的＝拿掉，畫面照這個標


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
    assert stu["建議稿"] == "我覺得  說得很好", stu["建議稿"]   # 句中的空白照原樣
    assert stu["換過的字"] == [{"原字": "小安", "換成": "", "位置": 4}], stu["換過的字"]
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
    assert "我覺得 <mark class=\"rv-drop\"" in got[keys[0]]["card"] and "（拿掉）</mark> 說得很好" in got[keys[0]]["card"]
    assert re.search(r"會念成</span>我跟<mark class=\"rv-drop\"[^>]*>（拿掉）</mark>說過了", got[keys[1]]["card"]), got[keys[1]]["card"]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
