"""10-18 #246 簡化版（宇軒）：每張老師名字卡在 ③ 都找得到；沒有英文名的照原聲留著，不再生成成空的。

測的事（三種情況）：
1. 人名清單把暱稱「美美」併到別人（② 的學員阿明）名下 → ③ 還是有小美自己的一列、看得到「卡片上寫成 美美」；
   美美沒有被算成阿明的英文名
2. 人名清單把「美美」標成老師本人、或人名清單根本沒抓到 → ③ 補上的小美那列看得到「卡片上寫成 美美」
3. 卡片沒有英文名 → 計畫裡不會變成空的（照原聲：不生成、不消音）、第 4 步只提醒（寫原片時間＋卡片類型）、
   第 5 步「需留意」列出；學員重念稿裡沒有英文名的名冊名字（照字念）也列出

全部是合成資料（假名＋自編句子）。獨立可跑：.venv/bin/python tests/test_no_code_246.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_people_rescan as T  # noqa: E402 — 假工作區、假名冊（小美：美美；阿明）

from bookclub import epcodes, execute, nameplan, personnames, review, students  # noqa: E402
from bookclub import turns as turns_mod  # noqa: E402
from bookclub import workdir as wd  # noqa: E402


def _card_says_meimei(w: Path) -> None:
    """老師句子「美美來，」：名字候選比對到美美、本名小美（名冊：小美 其他寫法 美美）。"""
    sp = json.loads((w / "說話者判斷.json").read_text(encoding="utf-8"))
    d = wd.read_json(wd.names_path(w))
    c = d["candidates"][0]
    s = next(x for x in sp["sentences"] if x["id"] == c["sentence_id"])
    s["text"] = "美美來，"
    c.update({"name": "美美", "matched_text": "美美", "canonical": "小美", "sentence": s["text"], "代號": ""})
    (w / "說話者判斷.json").write_text(json.dumps(sp, ensure_ascii=False), encoding="utf-8")
    wd.write_json(wd.names_path(w), d)


def _people(w: Path, rows: list[dict]) -> None:
    pp = personnames.people_path(w)
    d = json.loads(pp.read_text(encoding="utf-8"))
    d["人名"] = [{"id": f"P{k:03d}", "其他寫法": [], "說明": "", "句子": [], "次數": 1, "老師說": 1, "學員說": 0, "第一次": 10.0, **r}
                for k, r in enumerate(rows)]
    pp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def _row_of(w: Path, canon: str, chosen: set[str] | None = None) -> dict | None:
    return next((u for u in personnames.mentioned(w, chosen=chosen) if (u.get("名冊本名") or u["名字"]) == canon
                 and u["名字"] == canon), None)


def test_1_alias_merged_under_someone_else():
    w = T._make()
    _card_says_meimei(w)
    _people(w, [{"名字": "阿明", "其他寫法": ["美美"], "是誰": "學員"}])   # 人名清單把小美的暱稱併到阿明名下
    turns_mod.set_name_code(w, "阿明", "湯姆", add_roster=False)   # ② 阿明 → 湯姆
    epcodes.sync(w)
    assert epcodes.episode_codes(w).get("小美") != "湯姆", epcodes.episode_codes(w)   # 美美沒被算成阿明的英文名
    c = wd.read_json(wd.names_path(w))["candidates"][0]
    assert c["canonical"] == "小美" and c["代號"] != "湯姆", c
    row = _row_of(w, "小美", chosen={"阿明"})
    assert row and "美美" in row["卡片上寫成"], personnames.mentioned(w, chosen={"阿明"})
    # 同一組的其他寫法（名冊上是阿明自己的）照舊套阿明的英文名
    assert epcodes.episode_codes(w).get("阿明") == "湯姆"


def test_2_listed_as_teacher_or_missing():
    for rows in ([{"名字": "美美", "是誰": "老師本人"}], []):
        w = T._make()
        _card_says_meimei(w)
        _people(w, rows)
        epcodes.sync(w)
        row = _row_of(w, "小美")
        assert row and row["卡片上寫成"] == ["美美"], (rows, personnames.mentioned(w))
    # 讀音比對配到的加註
    d = wd.read_json(wd.names_path(w))
    d["candidates"][0]["比對層級"] = "A1"
    wd.write_json(wd.names_path(w), d)
    assert _row_of(w, "小美")["卡片上寫成"] == ["美美（讀音相近）"]


def test_3_no_code_keeps_original_and_is_listed():
    w = T._make()   # 老師句子「阿明…」：阿明在名冊上、這一集沒有英文名
    tp = turns_mod.turns_path(w)
    td = json.loads(tp.read_text(encoding="utf-8"))
    t1 = next(x for x in td["段落"] if x["說話者"] == "學員1")
    t1["校對稿"] = "我覺得阿明說得對"
    tp.write_text(json.dumps(td, ensure_ascii=False), encoding="utf-8")
    epcodes.sync(w)
    cands = wd.read_json(wd.names_path(w))["candidates"]
    idx = {k for k, c in enumerate(cands, start=1) if c.get("canonical") == "阿明" and not c.get("代號")}
    assert idx, cands
    plan = nameplan.compute_plan(w)
    used = {i for g in plan["生成"] for i in g.get("候選", [])} | {m.get("候選") for m in plan.get("消音", [])}
    assert not (idx & used), plan   # 不生成（不會變成空的）、不消音
    assert {x["候選"] for x in plan["略過"] if x["原因"] == nameplan.NO_CODE_SKIP} >= idx, plan["略過"]
    assert not any(not (g.get("text") or "").strip() for g in plan["生成"]), plan["生成"]
    # 第 4 步：只提醒（不擋），寫原片時間＋卡片類型
    pre = execute.precheck(w)
    note = next(n for n in pre["提醒"] if "還沒有英文名，照原聲留著" in n)
    first = min(cands[i - 1]["start"] for i in idx)
    assert f"{execute.fmt_card_time(first)}（老師提到名字）" in note and "（學員段落）" in note, note
    assert not any("英文名" in m for m in pre["缺"]), pre["缺"]
    # 第 5 步「需留意」：老師卡、學員重念（照字念的阿明）都列出
    rows = execute.no_code_rows(w)
    cards = {r["第3步"].split(":", 1)[0] for r in rows}
    assert cards == {"名字", "學員段落"} and all(r["類別"] == execute.KEPT_NAME_KIND for r in rows), rows
    snap = execute.skipped_snapshot(w)
    assert all(r in snap["列"] for r in rows), snap
    # 第 3 步名字卡：寫照原聲留著
    card = next(x for x in review.page_data(w)["項目"] if x["類型"] == "名字" and x["matched_text"] == "阿明")
    assert card.get("沒英文名照原聲"), card
    items, _ = students.build_items(w)
    assert any("阿明" in it["text"] for it in items)   # 學員重念稿照字念（交給第 5 步）


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
