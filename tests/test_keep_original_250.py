"""10-18 #250：③ 名冊上的人選「不用處理」「不是名字」一定照原聲留著（推翻 1016-3「名字照樣拿掉」）。

測的事：
- 三條路一致：老師句子不重念那一處、學員重念稿不換（也不拿掉）、保留原聲學員講到時預設照原聲（不消音、不生成）
- 名冊檔一個 byte 都不變；「不是名字」的名冊寫法不寫進共用排除清單
- 只影響這一列的人：人名清單把別人的暱稱併進來時，那個暱稱照舊換成那個人的英文名
- 第 5 步「需留意」列出這些照原聲留著的地方（文字類：只扣剪掉的，同一句被重念也列得到）

全部是合成資料（假名＋自編句子）。獨立可跑：.venv/bin/python tests/test_keep_original_250.py
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

from bookclub import epcodes, execute, finalcheck, nameplan, personnames, review, studentnames, students  # noqa: E402
from bookclub import turns as turns_mod  # noqa: E402
from bookclub import workdir as wd  # noqa: E402


def _setup(how: str = "不用處理"):
    """學員1 的段落提到小美、美美（學員重念）；學員2 保留原聲、講到美美（學員名字候選照 find 的格式給，不跑找名字）；
    老師句子本來就有小美（fake_workdir 的名字候選）。③ 小美選 how。回傳 (工作區, 還原 find 的函式, 學員名字候選 id)。"""
    w = T._make()
    tp = turns_mod.turns_path(w)
    td = json.loads(tp.read_text(encoding="utf-8"))
    t1 = next(x for x in td["段落"] if x["說話者"] == "學員1")
    t1["校對稿"] = "我覺得小美說得對，美美也是"
    t2 = next(x for x in td["段落"] if x["說話者"] == "學員2")
    tp.write_text(json.dumps(td, ensure_ascii=False), encoding="utf-8")
    dec = review.load_decisions(w)
    dec["學員聲音"] = {"學員2": "保留原聲"}
    review._save_decisions(w, dec)
    sp = json.loads((w / "說話者判斷.json").read_text(encoding="utf-8"))
    s2 = next(x for x in sp["sentences"] if x["id"] == t2["句子"][0])
    c = {"matched_text": "美美", "name": "美美", "canonical": "小美", "代號": "", "sentence_id": s2["id"], "sentence": s2["text"],
         "start": s2["start"] + 0.2, "end": s2["start"] + 0.6, "學員": "學員2", "位置": "句中", "信心": "高",
         "比對層級": "精確", "敏感詞": False}
    c["id"] = studentnames.cand_id(c)
    real = studentnames.find
    studentnames.find = lambda workdir, force=False: {"保留原聲學員": ["學員2"], "candidates": [dict(c)]}
    personnames.decide(w, "小美", how)
    personnames.wait_rescan(w)
    return w, (lambda: setattr(studentnames, "find", real)), c["id"]


def test_three_paths_keep_original():
    for how in ("不用處理", "不是名字"):
        (T._DATA / "名冊.csv").write_text(T._ROSTER, encoding="utf-8")   # _make 也寫同一份
        before = (T._DATA / "名冊.csv").read_bytes()
        w, undo, sid = _setup(how)
        try:
            # 老師句子：小美那一處不生成、不消音（照原聲）
            cands = wd.read_json(wd.names_path(w))["candidates"]
            idx = {k for k, x in enumerate(cands, start=1) if x.get("canonical") == "小美"}
            plan = nameplan.compute_plan(w)
            used = {i for g in plan["生成"] for i in g.get("候選", [])} | {m.get("候選") for m in plan.get("消音", [])}
            assert idx and not (idx & used), (how, idx, plan)
            # 學員重念：小美、美美照字念（不換、不拿掉）
            items, _ = students.build_items(w)
            hit = [it["text"] for it in items if "說得對" in it["text"]]
            assert hit and "小美" in hit[0] and "美美" in hit[0], (how, hit)
            assert not {"小美", "美美"} & {r["寫法"] for r in epcodes.replace_table(w)}
            # 保留原聲學員講到美美：預設照原聲（不消音、不生成），卡片寫明
            card = next(x for x in studentnames.items(w) if x["id"] == sid)
            assert card["照原聲留著"] and "不是名字" in card["tags"] and "照原聲留著" in card["建議"]["原因"], card
            sp = studentnames.plan(w)
            assert not sp["生成"] and not any(m["id"] == sid for m in sp["消音"]), sp
            # 人在學員名字卡上自己選了直接消音：照人選的
            wd.write_json(studentnames.decisions_path(w), {sid: {"做法": studentnames.MUTE, "tags": [], "note": ""}})
            assert any(m["id"] == sid for m in studentnames.plan(w)["消音"])
            # ③ 那列：灰字照原聲
            mei = next(u for u in personnames.mentioned(w) if u["名字"] == "小美")
            assert mei["名冊上的人照原聲"] and mei["名冊上的人"], mei
        finally:
            undo()
        assert (T._DATA / "名冊.csv").read_bytes() == before   # 名冊不動
        ex = T._DATA / "名字排除清單.csv"
        assert not ex.is_file() or "小美" not in ex.read_text(encoding="utf-8")   # 名冊上的寫法不寫共用排除清單


def test_other_persons_alias_still_replaced():
    """人名清單把阿明的暱稱「阿強」併到小美這一列 → 小美選不用處理，阿強照舊換成阿明的英文名（不跟著照原聲）。"""
    try:
        w = T._make()   # 會寫回預設名冊，所以名冊在這之後才換
        (T._DATA / "名冊.csv").write_text("中文名,其他寫法,性別,英文代號\n小美,美美,女,\n阿明,阿強,男,凱文\n", encoding="utf-8")
        pp = personnames.people_path(w)
        d = json.loads(pp.read_text(encoding="utf-8"))
        d["人名"] = [p for p in d["人名"] if p["名字"] != "阿強"]
        d["人名"].append({"id": "P050", "名字": "小美", "其他寫法": ["阿強"], "是誰": "其他人", "說明": "", "句子": [],
                        "次數": 1, "老師說": 1, "學員說": 0, "第一次": 76.3})
        pp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        personnames.request_rescan(w)   # 名冊換了：補找老師提到阿強的地方
        personnames.wait_rescan(w)
        personnames.decide(w, "小美", "不用處理")
        personnames.wait_rescan(w)
        keep = epcodes.keep_original_names(w)
        assert "小美" in keep["小美"] and "美美" in keep["小美"] and "阿強" not in keep["小美"], keep
        said, _ = review.replace_real_names("阿強跟小美說", epcodes.replace_table(w))
        assert said == "凱文跟小美說", said
        got = [(c["canonical"], c["代號"]) for c in wd.read_json(wd.names_path(w))["candidates"] if c.get("name") == "阿強"]
        assert got and all(x == ("阿明", "凱文") for x in got), got
    finally:
        (T._DATA / "名冊.csv").write_text(T._ROSTER, encoding="utf-8")


def test_step5_attention_lists_kept_names():
    w, undo, sid = _setup()
    try:
        rows = execute.kept_name_rows(w)
        snap = execute.skipped_snapshot(w)
    finally:
        undo()
    cards = {r["第3步"].split(":", 1)[0] for r in rows}
    assert cards == {"名字", "學員段落", "學員名字"}, rows
    assert all(r["類別"] == "照原聲名字" for r in rows)
    assert any(r["第3步"] == f"學員名字:{sid}" for r in rows)
    assert snap and snap["照原聲名字"] >= len(rows) and all(r in snap["列"] for r in rows), snap   # #246 沒英文名的也算在這一類
    assert "照原聲名字" in finalcheck.ATTENTION_TEXT_KINDS and any(k == "照原聲名字" for k, _n, _x in finalcheck.ATTENTION_KINDS)
    # 需留意：被重念蓋到的照樣列（文字類），剪掉的扣掉
    teacher = next(r for r in rows if r["第3步"].startswith("名字:"))
    stu = next(r for r in rows if r["第3步"].startswith("學員段落:"))
    a, b = teacher["start"], teacher["end"]
    log = {"片段": [{"src": [0.0, 1000.0], "freeze": 0.0}], "範圍": [0.0, 1000.0],
           "紀錄": [{"類型": "學員重念", "原片": [stu["start"], stu["end"]], "動到聲音": True},
                  {"類型": "名字", "原片": [a - 1, b + 1], "動到聲音": True}]
           + [{"類型": "刪除", "原片": [r["start"], r["end"]], "動到聲音": True} for r in rows if r["第3步"].startswith("學員名字:")]}
    got = finalcheck.place_attention(rows, log)
    keys = [x["鍵"] for x in got]
    assert teacher["key"] in keys and stu["key"] in keys, (keys, rows)
    assert not any(k.startswith("照原聲名字:學員名字:") for k in keys), keys   # 落在剪掉的地方：不列


def test_no_rows_when_nothing_kept():
    w = T._make()
    assert execute.kept_name_rows(w) == []
    personnames.decide(w, "阿德", "換成代號", "傑克")
    personnames.wait_rescan(w)
    assert execute.kept_name_rows(w) == []


def test_name_also_someone_elses_alias_privacy():
    """10-18 審查（隱私）：名冊 小美（本名、沒英文名）；阿明（Kevin，其他寫法有「小美」「阿強」）；人名清單把「阿強」併進小美這列，
    ③ 小美選不用處理 → 只有小美照原聲；阿強是阿明的，三條路都換成 Kevin（不能因為小美也是阿明的寫法，就把阿明算成自己）。"""
    try:
        w, undo, sid = _setup_alias_case()
        try:
            keep = epcodes.keep_original_names(w)
            assert keep.get("小美") == {"小美"}, keep
            said, _ = review.replace_real_names("阿強跟小美說", epcodes.replace_table(w))   # 學員重念
            assert said == "Kevin跟小美說", said
            cands = wd.read_json(wd.names_path(w))["candidates"]
            q = [c for c in cands if c.get("name") == "阿強"]
            assert q and all((c["canonical"], c["代號"]) == ("阿明", "Kevin") for c in q), q
            plan = nameplan.compute_plan(w)   # 老師句子
            assert any("Kevin" in g["text"] for g in plan["生成"]) and not any("阿強" in g["text"] for g in plan["生成"]), plan
            card = next(x for x in studentnames.items(w) if x["id"] == sid)   # 保留原聲學員：不照原聲，換成代號時念 Kevin
            assert not card.get("照原聲留著") and "不是名字" not in card["tags"], card
            wd.write_json(studentnames.decisions_path(w), {sid: {"做法": studentnames.CODE, "tags": [], "note": ""}})
            g = studentnames.plan(w)["生成"]
            assert g and "Kevin" in g[0]["text"] and "阿強" not in g[0]["text"], g
        finally:
            undo()
    finally:
        (T._DATA / "名冊.csv").write_text(T._ROSTER, encoding="utf-8")


def _setup_alias_case():
    w = T._make()
    (T._DATA / "名冊.csv").write_text("中文名,其他寫法,性別,英文代號\n小美,,女,\n阿明,小美、阿強,男,Kevin\n", encoding="utf-8")
    pp = personnames.people_path(w)
    d = json.loads(pp.read_text(encoding="utf-8"))
    d["人名"] = [p for p in d["人名"] if p["名字"] != "阿強"]
    d["人名"].append({"id": "P050", "名字": "小美", "其他寫法": ["阿強"], "是誰": "其他人", "說明": "", "句子": [],
                    "次數": 1, "老師說": 1, "學員說": 0, "第一次": 76.3})
    pp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    personnames.request_rescan(w)
    personnames.wait_rescan(w)
    td = json.loads(turns_mod.turns_path(w).read_text(encoding="utf-8"))
    t2 = next(x for x in td["段落"] if x["說話者"] == "學員2")
    dec = review.load_decisions(w)
    dec["學員聲音"] = {"學員2": "保留原聲"}
    review._save_decisions(w, dec)
    sp = json.loads((w / "說話者判斷.json").read_text(encoding="utf-8"))
    s2 = next(x for x in sp["sentences"] if x["id"] == t2["句子"][0])
    s2["text"] = "阿強說得對。"
    (w / "說話者判斷.json").write_text(json.dumps(sp, ensure_ascii=False), encoding="utf-8")
    c = {"matched_text": "阿強", "name": "阿強", "canonical": "阿明", "代號": "Kevin", "sentence_id": s2["id"], "sentence": s2["text"],
         "start": s2["start"] + 0.2, "end": s2["start"] + 0.6, "學員": "學員2", "位置": "句首", "信心": "高",
         "比對層級": "精確", "敏感詞": False}
    c["id"] = studentnames.cand_id(c)
    real = studentnames.find
    studentnames.find = lambda workdir, force=False: {"保留原聲學員": ["學員2"], "candidates": [dict(c)]}
    personnames.decide(w, "小美", "不用處理")
    personnames.wait_rescan(w)
    return w, (lambda: setattr(studentnames, "find", real)), c["id"]


def test_short_name_still_listed():
    """10-18 審查：照原聲名字只有名字那幾個字，0.25 秒也要列（不套 0.3 秒門檻）；別的類別照舊套。"""
    log = {"片段": [{"src": [0.0, 100.0], "freeze": 0.0}], "範圍": [0.0, 100.0], "紀錄": []}
    rows = [{"key": "照原聲名字:名字:1", "類別": "照原聲名字", "start": 10.0, "end": 10.25, "第3步": "名字:1"},
            {"key": "聲紋:S1", "類別": "聲紋", "start": 20.0, "end": 20.25, "第3步": None}]
    got = [x["鍵"] for x in finalcheck.place_attention(rows, log)]
    assert got == ["照原聲名字:名字:1"], got


def _nickname_case(coded: bool, alts: list[str]):
    """③ 一列「美美」（名冊：小美 其他寫法 美美），人名清單其他寫法 alts；老師句子「美美來，」、學員1 重念稿講到美美與小美、
    學員2 保留原聲講到美美。coded＝② 小美 → 艾瑪。③ 美美選不用處理。"""
    w = T._make()
    if coded:
        turns_mod.set_name_code(w, "小美", "艾瑪", add_roster=False)
    pp = personnames.people_path(w)
    d = json.loads(pp.read_text(encoding="utf-8"))
    d["人名"].append({"id": "P060", "名字": "美美", "其他寫法": alts, "是誰": "其他人", "說明": "", "句子": [],
                    "次數": 1, "老師說": 1, "學員說": 0, "第一次": 76.3})
    pp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    sp = json.loads((w / "說話者判斷.json").read_text(encoding="utf-8"))
    nd = wd.read_json(wd.names_path(w))
    c0 = nd["candidates"][0]
    next(x for x in sp["sentences"] if x["id"] == c0["sentence_id"])["text"] = "美美來，"
    c0.update({"name": "美美", "matched_text": "美美", "canonical": "小美", "sentence": "美美來，"})
    (w / "說話者判斷.json").write_text(json.dumps(sp, ensure_ascii=False), encoding="utf-8")
    wd.write_json(wd.names_path(w), nd)
    tp = turns_mod.turns_path(w)
    td = json.loads(tp.read_text(encoding="utf-8"))
    next(x for x in td["段落"] if x["說話者"] == "學員1")["校對稿"] = "我覺得美美說得對，小美也是"
    t2 = next(x for x in td["段落"] if x["說話者"] == "學員2")
    tp.write_text(json.dumps(td, ensure_ascii=False), encoding="utf-8")
    dec = review.load_decisions(w)
    dec["學員聲音"] = {"學員2": "保留原聲"}
    review._save_decisions(w, dec)
    s2 = next(x for x in sp["sentences"] if x["id"] == t2["句子"][0])
    sc = {"matched_text": "美美", "name": "美美", "canonical": "小美", "代號": "", "sentence_id": s2["id"], "sentence": s2["text"],
          "start": s2["start"] + 0.2, "end": s2["start"] + 0.6, "學員": "學員2", "位置": "句中", "信心": "高",
          "比對層級": "精確", "敏感詞": False}
    sc["id"] = studentnames.cand_id(sc)
    real = studentnames.find
    def fake_find(workdir, force=False):   # 跟真的 find 一樣過一次 epcodes.sync 的對齊（本名照這一集的決定、代號照代號表）
        canon = (epcodes.episode_spellings(workdir).get("美美") or ("小美",))[0]
        return {"保留原聲學員": ["學員2"], "candidates": [
            {**sc, "canonical": canon, "代號": epcodes.episode_codes(workdir).get(canon) or ""}]}

    studentnames.find = fake_find
    personnames.decide(w, "美美", "不用處理")
    personnames.wait_rescan(w)
    epcodes.sync(w)
    return w, (lambda: setattr(studentnames, "find", real)), sc["id"]


def _paths(w: Path, sid: str) -> dict:
    cands = wd.read_json(wd.names_path(w))["candidates"]
    idx = {k for k, x in enumerate(cands, start=1) if x.get("name") == "美美"}
    plan = nameplan.compute_plan(w)
    gen = [g for g in plan["生成"] if set(g.get("候選", [])) & idx]
    items, _ = students.build_items(w)
    stu = next(it["text"] for it in items if "說得對" in it["text"])
    card = next(x for x in studentnames.items(w) if x["id"] == sid)
    row = next(u for u in personnames.mentioned(w) if u["名字"] == "美美")
    return {"老師": gen, "學員重念": stu, "保留原聲": card, "③": row, "需留意": execute.kept_name_rows(w)}


def test_rule_b_nickname_with_or_without_english_name():
    """10-18 #250 題 1（宇軒選 B）：(a) 小美這一集有英文名，③ 暱稱美美選不用處理 → 三條路照樣換成艾瑪、③ 寫「照樣換成 艾瑪」、
    需留意不列；(b) 小美沒有英文名 → 照原聲。"""
    w, undo, sid = _nickname_case(True, [])
    try:
        got = _paths(w, sid)
    finally:
        undo()
    assert got["老師"] and all("艾瑪" in g["text"] and "美美" not in g["text"] for g in got["老師"]), got["老師"]
    assert got["學員重念"] == "我覺得艾瑪說得對，艾瑪也是", got["學員重念"]
    assert not got["保留原聲"].get("照原聲留著") and "不是名字" not in got["保留原聲"]["tags"], got["保留原聲"]
    assert got["③"]["照樣換成英文名"] == "艾瑪" and got["③"]["名冊上的英文名"] == "艾瑪" and not got["③"]["名冊上的人照原聲"], got["③"]
    assert not got["需留意"], got["需留意"]
    w, undo, sid = _nickname_case(False, [])
    try:
        got = _paths(w, sid)
    finally:
        undo()
    assert not got["老師"] and got["學員重念"] == "我覺得美美說得對，小美也是", got
    assert got["保留原聲"].get("照原聲留著"), got["保留原聲"]
    assert got["③"]["名冊上的人照原聲"] and not got["③"]["照樣換成英文名"], got["③"]
    assert {r["第3步"].split(":", 1)[0] for r in got["需留意"]} >= {"名字", "學員段落", "學員名字"}, got["需留意"]


def test_rule_a_real_name_merged_into_nickname_row():
    """題 2（宇軒選 A，維持）：人名清單把本名「小美」併進暱稱「美美」那列 → 有英文名的照換、沒有的跟著照原聲。"""
    for coded in (True, False):
        w, undo, sid = _nickname_case(coded, ["小美"])
        try:
            got = _paths(w, sid)
            keep = epcodes.keep_original_names(w)
        finally:
            undo()
        if coded:
            assert got["學員重念"] == "我覺得艾瑪說得對，艾瑪也是" and got["老師"], got
            assert not keep.get("美美"), keep
        else:
            assert got["學員重念"] == "我覺得美美說得對，小美也是" and not got["老師"], got
            assert keep.get("美美") >= {"美美", "小美"}, keep


def test_web_row_text_for_english_name():
    """③ 那列：有英文名的寫「這位這一集有英文名，照樣換成 艾瑪」，選單「不用處理（照樣換成 艾瑪）」；不寫「照原聲留著」。"""
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        print("（沒有 node，略過）")
        return
    web = REPO_ROOT / "bookclub" / "web"
    row = {"id": "P", "名字": "美美", "其他寫法": [], "是誰": "其他人", "說明": "", "名冊本名": "小美", "次數": 1, "老師說": 1, "學員說": 0,
           "第一次": 1, "刪除段落外次數": 1, "都在刪除段落": False, "② 已決定": False, "做法": "不用處理", "代號": None, "同一人": None,
           "自動配": False, "已決定": True, "名冊上的人": True, "名冊上的英文名": "艾瑪", "照樣換成英文名": "艾瑪", "名冊上的人照原聲": False}
    spec = {"前置": "", "情境": [{"名稱": "row", "程式": f"""
        rv.data = {{"提到的名字": [{json.dumps(row, ensure_ascii=False)}], "學員": {{}}, "這一集代號": {{}},
          "代號分組": {{"女": ["艾瑪"], "男": [], "這一集用到的其他代號": []}}, "代號對照": {{"代號給了": {{}}, "撞名": {{}}}}}};
        return rvNameRowHtml(rv.data["提到的名字"][0]);"""}]}
    d = Path(tempfile.mkdtemp())
    (d / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    files = ",".join(str(web / f) for f in ("cards.js", "review.js"))
    out = json.loads(subprocess.run([node, str(REPO_ROOT / "tests" / "web_harness.js"), files, str(d / "spec.json")],
                                    capture_output=True, text=True, check=True).stdout)["row"]
    assert "這位這一集有英文名，照樣換成 艾瑪" in out and "不用處理（照樣換成 艾瑪）" in out and "照原聲留著" not in out, out


def test_same_person_then_not_needed_keeps_original_even_with_english_name():
    """10-18 宇軒（審查題 1）：名冊「小美：美美、阿德」、小美這一集有英文名（艾瑪）。③ 阿德先選「同一人：小美」、後來改回
    「不用處理」→ 照原聲（老師明確說不是小美）；直接選「不用處理」（沒有先選同一人）→ 照樣換成艾瑪。"""
    for undo_same in (True, False):
        w = T._make()
        T._two(w)
        (T._DATA / "名冊.csv").write_text("中文名,其他寫法,性別\n小美,美美、阿德,女\n阿明,,男\n", encoding="utf-8")
        personnames.request_rescan(w)
        personnames.wait_rescan(w)
        if undo_same:
            personnames.decide(w, "阿德", "是上面的學員", same_as="小美")
            personnames.wait_rescan(w)
        personnames.decide(w, "阿德", "不用處理")
        personnames.wait_rescan(w)
        said, _ = review.replace_real_names("阿德說得對", epcodes.replace_table(w))
        ade = next(u for u in personnames.mentioned(w, chosen={"小美", "阿明"}) if u["名字"] == "阿德")
        if undo_same:
            assert said == "阿德說得對" and ade["名冊上的人照原聲"] and not ade["照樣換成英文名"], (said, ade)
        else:
            assert said == "艾瑪說得對" and ade["照樣換成英文名"] == "艾瑪", (said, ade)
    (T._DATA / "名冊.csv").write_text(T._ROSTER, encoding="utf-8")


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
