"""10-20 總審查（批次 1020-1）：`敏感詞.csv` 替代詞留空的詞，老師句子、學員重念兩條路一致拿掉。

以前老師句子會拿掉（名字卡 `敏感詞` 沒有代號＝刻意拿掉），學員重念稿卻照字念（`review.replace_real_names`
只換有代號的列），第 5 步「需留意」還把它列成「這一集還沒有英文名、照原聲留著」。替代詞留空＝人決定刪，
符合「程式不自己把名字變成空的」的原則，所以 `epcodes.replace_table` 給這種列標 `拿掉`。

測的事：
- 老師句子：那一處不念（重念的句子裡沒有這個詞）
- 學員重念：要念的字裡拿掉、句首句尾不留空白（#245）；第 3 步建議寫「「某詞」拿掉了」、卡片標「（拿掉）」（#247）
- 第 5 步「需留意」不再把它列成沒英文名照原聲（`execute.no_code_rows`）
- 有替代詞的照舊換成替代詞

全部是合成資料（假名＋自編句子）。獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_sensitive_drop_1020.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_people_rescan as T  # noqa: E402 — 假工作區、假名冊（BOOKCLUB_DATA_DIR）

from bookclub import epcodes, execute, nameplan, review, students  # noqa: E402
from bookclub import turns as turns_mod  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

WORD = "秘密基地"
NODE = shutil.which("node")
WEB = REPO_ROOT / "bookclub" / "web"
HARNESS = REPO_ROOT / "tests" / "web_harness.js"


def _make() -> Path:
    (T._DATA / "敏感詞.csv").write_text(f"原詞,替代詞\n{WORD},\n老地方,某個地方\n", encoding="utf-8")
    w = T._make()
    # 老師那一句提到敏感詞（名字候選照找名字的格式給，不跑找名字）
    sp = json.loads((w / "說話者判斷.json").read_text(encoding="utf-8"))
    s = next(x for x in sp["sentences"] if x["id"] == "00_019")
    s["text"] = f"我們下次去{WORD}吧，"
    (w / "說話者判斷.json").write_text(json.dumps(sp, ensure_ascii=False), encoding="utf-8")
    m = json.loads((w / "transcript" / "merged.json").read_text(encoding="utf-8"))
    import fake_workdir

    m["words"] = fake_workdir._words(sp["sentences"])
    (w / "transcript" / "merged.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    names = wd.read_json(wd.names_path(w))
    c = dict(names["candidates"][0])
    c.update({"sentence": s["text"], "sentence_id": "00_019", "name": WORD, "canonical": WORD, "代號": "", "敏感詞": True,
              "matched_text": WORD, "位置": "句中", "建議做法": "整句換掉", "start": s["start"] + 1.0, "end": s["start"] + 1.6})
    names["candidates"] = [c]
    wd.write_json(wd.names_path(w), names)
    # 學員1 的段落提到敏感詞（句首）與有替代詞的那個詞
    tp = turns_mod.turns_path(w)
    td = json.loads(tp.read_text(encoding="utf-8"))
    t1 = next(x for x in td["段落"] if x["說話者"] == "學員1")
    t1["校對稿"] = f"{WORD} 是我們約好的老地方"
    tp.write_text(json.dumps(td, ensure_ascii=False), encoding="utf-8")
    return w


def _cleanup() -> None:
    (T._DATA / "敏感詞.csv").unlink(missing_ok=True)


def test_table_marks_empty_replacement_as_drop():
    w = _make()
    try:
        tab = {r["寫法"]: r for r in epcodes.replace_table(w)}
        assert tab[WORD].get("拿掉") is True and not tab[WORD]["代號"], tab[WORD]
        assert not tab["老地方"].get("拿掉") and tab["老地方"]["代號"] == "某個地方", tab["老地方"]
    finally:
        _cleanup()


def test_teacher_and_student_paths_both_drop():
    w = _make()
    try:
        plan = nameplan.compute_plan(w)
        used = {i for g in plan["生成"] for i in g.get("候選", [])} | {m.get("候選") for m in plan.get("消音", [])}
        assert 1 in used, plan   # 老師那一處有處理（不是照原聲）
        assert all(WORD not in g["text"] for g in plan["生成"]), plan["生成"]
        items, _ = students.build_items(w)
        hit = [it["text"] for it in items if it.get("段落") and "是我們" in it["text"]]
        assert hit and all(WORD not in it["text"] for it in items), [it["text"] for it in items]
        assert hit[0].startswith("是我們約好的"), hit   # 句首的空白去掉（#245）
        page = review.page_data(w)
        stu = [x for x in page["項目"] if x["類型"] == "學員段落" and "約好的" in (x.get("校對稿") or "")]
        assert stu and stu[0]["建議稿"] == "是我們約好的某個地方", stu[0]["建議稿"]   # 有替代詞的照換
        assert {"原字": WORD, "換成": "", "位置": 0} in stu[0]["換過的字"], stu[0]["換過的字"]
        assert f"「{WORD}」拿掉了" in stu[0]["建議"]["原因"], stu[0]["建議"]["原因"]
        # 第 5 步「需留意」：不再列成「沒英文名、照原聲」
        rows = execute.no_code_rows(w)
        assert not [r for r in rows if r["第3步"] == f"學員段落:{stu[0]['id']}"], rows
        return page, stu[0]
    finally:
        _cleanup()


def test_web_card_shows_drop_mark():
    if not NODE:
        print("（沒有 node，略過）")
        return
    page, stu = test_teacher_and_student_paths_both_drop()
    key = f"學員段落:{stu['id']}"
    skel = ["rv-right", "rv-lower", "rv-tl", "rv-progress", "rv-legend", "rv-io", "rv-export-msg", "rv-busy",
            "rv-prep-btn", "rv-go4", "rv-settings", "rv-codes", "rv-clock", "rv-head"]   # 跟 test_name_drop 一樣的頁面骨架
    pre = (f"H.skeleton = new Set({json.dumps(skel)});\n"
           f"const DATA = {json.dumps(page, ensure_ascii=False)};\n"
           "H.apiGetData['/api/review'] = DATA;\n"
           "function setup() { rv.data = H.clone(DATA); if (typeof rvUseCardCtx === 'function') rvUseCardCtx();\n"
           "  rv.video = H.makeEl('video', {id: 'rv-video'}, ''); rv.video.paused = true; }\n")
    scen = [{"名稱": key, "程式": f"""
          setup(); rv.cur = {json.dumps(key, ensure_ascii=False)}; rv.open = true; rvRenderCard();
          return document.getElementById("rv-right").innerHTML;"""}]
    d = Path(tempfile.mkdtemp())
    (d / "spec.json").write_text(json.dumps({"前置": pre, "情境": scen}, ensure_ascii=False), encoding="utf-8")
    files = ",".join(str(WEB / f) for f in ("cards.js", "review.js") if (WEB / f).is_file())
    r = subprocess.run([NODE, str(HARNESS), files, str(d / "spec.json")], capture_output=True, text=True, check=True)
    card = json.loads(r.stdout)[key]
    assert isinstance(card, str) and "（拿掉）" in card and f"「{WORD}」拿掉了" in card, card


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
