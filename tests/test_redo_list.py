"""1015-8 #228（32 號設計 §7.4 情境 1–10）：「要重做的卡」改成問生成程式自己的判斷（`execcounts.stale_ids`），
再用生成句表反查卡片；所有畫面經 `needs.redo_list` 一個入口。

用 tests/test_needs.py 的假工作區（合成資料、電子音）＋手寫的處理紀錄與假的生成紀錄，不載入模型、不碰真的工作區。
情境 9 的「假生成程式」：照生成程式挑句子的同一個函式（`tts.needs_work`）挑要生成的句子，寫一筆新的紀錄與聲音檔。
獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_redo_list.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_needs as tn  # noqa: E402
import test_versions as tv  # noqa: E402

from bookclub import execcounts, execute, finalcards, needs, proclog, tts  # noqa: E402
from bookclub import finalcheck as fc  # noqa: E402
from bookclub import workdir as wd  # noqa: E402
from bookclub.config import data_dir  # noqa: E402

ROLE_LOG = {"老師": tts.teacher_log_path}


def _records() -> list[dict]:
    """test_needs 的處理紀錄，加上名字 2（老師重念 S002）那一筆。"""
    return tn._records() + [tn._rec("名字整句換掉", 119.778, 123.6, ["名字:2"], "生成/老師/S002.wav")]


def _write_gen(w: Path, shift: dict | None = None, skip: set | None = None, rng=None) -> None:
    """假的生成紀錄（照現在的計畫，三類都有；每句記放回時間格、檔案都在）——生成程式看來全部做好了。
    shift={句id: 秒}：那一句的紀錄時間格往後移（＝在範圍改之前生成的）；skip：這幾句不寫紀錄。"""
    shift, skip = shift or {}, skip or set()
    ref = wd.ref_dir(w) / "ref.wav"
    ctx = fc._redo_ctx(w)
    if rng is not None:   # 第 4 步照這個範圍跑：學員段落的時間格切在範圍邊界
        from bookclub import students

        ctx["學員"] = students.build_items(w, rng[0], rng[1])[0]
    for role, xs in ctx.items():
        lp, od = fc._role_paths(w, role)
        od.mkdir(parents=True, exist_ok=True)
        rel = od.relative_to(w)
        recs = []
        for x in xs:
            if x["id"] in skip:
                continue
            d = shift.get(x["id"], 0.0)
            recs.append({"id": x["id"], "text": x.get("text"), "slot": [round(x["slot"][0] + d, 3), x["slot"][1]],
                         "嘗試": [{"種子": 7}], "選定": 1, "第幾版": 1, "檔案": str(rel / f"{x['id']}.wav"),
                         "放回時間格": {"檔案": str(rel / f"{x['id']}_放回時間格.wav")}})
            (od / f"{x['id']}.wav").write_bytes(b"wav")
            (od / f"{x['id']}_放回時間格.wav").write_bytes(b"wav")
        data = {"句子": recs}
        if role == "老師":
            data.update({"參考音": str(ref), "參考音指紋": tts.ref_fingerprint(ref),
                         "參考音逐字稿": (wd.ref_dir(w) / "ref.txt").read_text(encoding="utf-8").strip()})
        wd.write_json(lp, data)


def _upgraded(shift: dict | None = None, results: dict | None = None) -> Path:
    """升級前做過第 4 步（版本 1＝升級前的成品，全部通過）：先寫生成紀錄（shift 的那幾句是範圍改之前生成的）再升級。"""
    w = tn._fresh()
    _write_gen(w, shift)
    log = tn._log(recs=_records())
    prod = tn._prod(w, "成品_0-3_sw.mp4", tn.T1)
    res = {fc.record_key(r): ("通過",) for r in log["紀錄"]}
    res.update(results or {})
    tn._setup(w, log, tn._check(log, res, 成品影片=prod, 未登記確認={"130.00": {"結果": "沒問題"}}))
    tn._quiet(needs.upgrade, w)
    return w


def _key(w: Path, key: str) -> str:
    return tn._card(w, key)["卡號"]


def _redo(w: Path) -> dict[str, str]:
    return {r["卡號"]: r["起因"] for r in needs.redo_list(w)}


def _old_way(w: Path) -> list[str]:
    """改之前的算法（只看卡片指紋與歷史，沒問生成程式）。"""
    st = needs.load(w)
    v = st.latest_version()
    hist = needs.read_history(w) if v is not None and v.get("其他卡指紋") is None else None
    return [r["卡號"] for r in needs.redo_cards(st, proclog.load(w), hist)]


def _cards_of(w: Path, sids: set) -> set[str]:
    st = needs.load(w)
    return {k for (sid, role), g in st.gen.items() if (role, sid) in sids for k in g.get("卡號") or []}


def test_1_k0003_range_changed_before_upgrade():
    """情境 1（K0003 實例）：名字卡 S002 做進版本 1；升級前範圍改了 0.1 秒（生成紀錄的時間格跟計畫差 0.1 秒）→
    以前的算法看不出來；redo_list 列它、起因「改了」；第 4 步「要做哪些」1 張、第 5 步「重做這幾張（1 張）」；
    跑完生成、組裝 → 清單空。"""
    w = _upgraded(shift={"S002": 0.1})
    k = _key(w, "名字:2")
    assert k not in _old_way(w)                          # #228 的 bug：卡片指紋看不出來
    assert _redo(w) == {k: needs.REDO_EDITED}
    assert [x["卡號"] for x in execute.redo_cards_view(w)] == [k]
    part = finalcards.build(w)
    assert part["統計"]["要重做"] == 1
    assert next(c for c in part["卡片"] if c.get("卡號") == k)["狀態"] == finalcards.EDITED   # 「已改，等重做」
    rows = {(r["類型"], r["做法"]): r for r in execcounts.counts(w)}
    t = rows[("老師提到名字", "老師重念（整句、名字換成代號）")]
    assert t["完成"] == t["總數"] - 1                    # 第 4 步逐類統計跟同一個判斷
    # 跑完生成（紀錄照現在的計畫）、組裝成新的一版 → 清單空
    _write_gen(w)
    recs = _records()
    recs[-1] = {**recs[-1], "檔案指紋": "fp-S002-第2版"}
    tv._assemble(w, tv._t(1), recs)
    assert _redo(w) == {} and execute.redo_cards_view(w) == []


def test_2_tiny_shift_not_listed_same_as_generator():
    """情境 2：範圍只差 0.03 秒 → 不列，生成程式也不重做（兩邊一致）。"""
    w = _upgraded(shift={"S002": 0.03})
    assert _redo(w) == {}
    rec = next(r for r in wd.read_json(tts.teacher_log_path(w))["句子"] if r["id"] == "S002")
    it = next(x for x in fc._redo_ctx(w)["老師"] if x["id"] == "S002")
    assert not tts.needs_work(rec, {**it, "生成用文字": it["text"]}, workdir=w)
    assert execute.names_done(w)[0] is True


def test_3_pron_table_change_lists_affected_cards():
    """情境 3：發音對照表改了一個字，影響的句子（至少兩句）→ 那幾句的卡列出，其他不列。"""
    w = _upgraded()
    assert _redo(w) == {}
    ctx = fc._redo_ctx(w)
    word = "分享"
    hit = {(role, x["id"]) for role, xs in ctx.items() for x in xs if word in (x.get("text") or "")}
    assert len(hit) >= 2, hit
    table = data_dir() / tts.PRON_TABLE_NAME
    table.write_text(f"原字,生成用,原因,建立日期\n{word},分想,測試,2026-10-16\n", encoding="utf-8")
    try:
        got = _redo(w)
        st = needs.load(w)
        made = {k for k in _cards_of(w, hit) if (k, 1) in st.states}   # 做進過成品的（T007 這一版沒處理，不算「改了」）
        assert set(got) == made and set(got.values()) == {needs.REDO_EDITED}, (got, hit)
        assert len(got) >= 2
    finally:
        table.unlink()
    assert _redo(w) == {}


def test_4_teacher_ref_changed_lists_all_name_cards():
    """情境 4：老師參考音換了 → 全部老師重念的名字卡列出（生成程式也是全部重做）。"""
    w = _upgraded()
    (wd.ref_dir(w) / "ref.txt").write_text("換過的參考音逐字稿。", encoding="utf-8")
    names = {_key(w, "名字:1"), _key(w, "名字:2")}
    got = _redo(w)
    assert set(got) == names and set(got.values()) == {needs.REDO_EDITED}
    assert execute.names_done(w)[0] is False
    assert finalcards.build(w)["統計"]["要重做"] == 2


def test_5_deleted_fit_file_lists_that_card():
    """情境 5：手動刪掉一句的「放回時間格」檔 → 那張卡列出。"""
    w = _upgraded()
    (w / "生成" / "學員" / "T005_01_放回時間格.wav").unlink()
    assert _redo(w) == {_key(w, "學員段落:T005"): needs.REDO_EDITED}


def test_6_cut_range_changed_before_upgrade():
    """情境 6：刪除段落升級前改了範圍（處理紀錄記的是 0–3.6，第 3 步現在是 0–3.0）→ 從處理紀錄的原片比出來，列出。
    沒改的另一段（S2，這一版沒處理紀錄）不列；只差 0.03 秒也不列。"""
    for end, listed in ((3.0, True), (3.63, False)):
        w = tn._fresh()
        p = w / "校對" / "刪除建議.json"
        data = wd.read_json(p)
        data["建議"][0]["end"] = end
        wd.write_json(p, data)
        _write_gen(w)
        log = tn._log(recs=_records())
        res = {fc.record_key(r): ("通過",) for r in log["紀錄"]}
        tn._setup(w, log, tn._check(log, res, 成品影片=tn._prod(w, "成品_0-3_sw.mp4", tn.T1)))
        tn._quiet(needs.upgrade, w)
        k = _key(w, "刪除段落:S1")
        assert tn._card(w, "刪除段落:S1")["原片"][1] == end
        assert _redo(w) == ({k: needs.REDO_EDITED} if listed else {}), (end, _redo(w))


def test_7_one_sentence_two_cards_both_listed():
    """情境 7：一句同時屬於兩張卡（同一句兩個名字）→ 兩張都列。"""
    w = _upgraded(shift={"S002": 0.2})
    k1, k2 = _key(w, "名字:1"), _key(w, "名字:2")
    st = needs.load(w)
    st.gen[("S002", "老師")]["卡號"] = [k2, k1]
    needs.save(w, st)
    assert _redo(w) == {k1: needs.REDO_EDITED, k2: needs.REDO_EDITED}


def test_8_closed_workdir_empty_no_errors():
    """情境 8：已結案（聲音檔都清了）→ 清單空的，第 4、5 步讀取不報錯。"""
    w = _upgraded(shift={"S002": 0.1})
    import shutil

    shutil.rmtree(w / "生成")                           # 聲音檔和生成紀錄都清掉了
    assert len(_redo(w)) >= 3                           # 還沒結案：每一句都會重新生成
    wd.write_json(w / "結案.json", {"狀態": "已結案"})
    assert needs.redo_list(w) == [] and execute.redo_cards_view(w) == []
    st = execute.status(w)
    assert st["要重做"] == []
    assert finalcards.build(w)["統計"]["要重做"] == 0
    assert fc.returned_by_card(w, fc._index(w)) == {}
    wd.write_json(w / "結案.json", {"狀態": "清理中"})   # 清理開始之後也算
    assert needs.redo_list(w) == []


def _fake_generate(w: Path) -> set:
    """假生成程式：每一類直接用生成程式挑句子的函式（`tts.needs_work`＋老師參考音整份換過就全部，跟 `tts.generate_teacher`
    一樣），不經 `execcounts._stale`；挑到的寫新紀錄。
    回傳實際重新生成的 {(角色, 句id)}。參考音照第 4 步「做過沒有」那樣比（`execute.names_done`／`students_done`／
    `stunames_done`），不經 execcounts。"""
    from bookclub import studentgen, students

    made: set = set()
    ctx = fc._redo_ctx(w)
    for role, xs in ctx.items():
        lp, od = fc._role_paths(w, role)
        data = wd.read_json(lp, default=None) or {}
        recs = {r["id"]: r for r in data.get("句子") or []}
        back = data.get("退回直接消音") or {}
        if role == "老師":
            table = tts.load_pron_table(w / tts.PRON_TABLE_NAME if (w / tts.PRON_TABLE_NAME).is_file() else None)
            changed = tts.teacher_ref_changed(w, data)
            ref_of = lambda x: None  # noqa: E731
        else:
            table = tts.load_pron_table()
            changed = False
            now = students.current_refs(w, xs) if role == "學員" and xs else {}
            ref_of = ((lambda x: now.get(x["學員"]) or wd.localize((recs.get(x["id"]) or {}).get("參考音"), w))
                      if role == "學員" else (lambda x: studentgen.current_ref(w, x["學員"], recs.get(x["id"]))))
        for x in xs:
            if x["id"] in back:
                continue
            it = {**x, "生成用文字": tts.apply_pron(x["text"], table)[0]}
            rec = recs.get(x["id"])
            if rec is not None and rec.get("text") is None:
                rec = {**rec, "text": x.get("text")}
            if changed or tts.needs_work(rec, it, ref_of(x), workdir=w):
                made.add((role, x["id"]))
                rel = od.relative_to(w)
                od.mkdir(parents=True, exist_ok=True)
                (od / f"{x['id']}.wav").write_bytes(b"new")
                (od / f"{x['id']}_放回時間格.wav").write_bytes(b"new")
                recs[x["id"]] = {"id": x["id"], "text": x.get("text"), "生成用文字": it["生成用文字"], "slot": x["slot"],
                                 "嘗試": [{"種子": 8}], "選定": 1, "第幾版": 2, "檔案": str(rel / f"{x['id']}.wav"),
                                 "放回時間格": {"檔案": str(rel / f"{x['id']}_放回時間格.wav")}}
        wd.write_json(lp, {**data, "句子": list(recs.values())})
    return made


def test_9_fake_generator_matches_redo_list():
    """情境 9（一致性）：名字 2 升級前改了範圍、T005 的放回時間格檔被刪、T003 在第 5 步退回（要修改）→
    第 4 步先清掉退回的句子（`prepare_redo`），假生成程式跑一次：實際重新生成的句子反查到的卡＝執行前 `redo_list` 裡
    （有生成句的）卡；起因「改了」的那幾張一定在裡面。"""
    w = _upgraded(shift={"S002": 0.1}, results={"學員重念@40.00": ("退回重做", "第一句念錯")})
    (w / "生成" / "學員" / "T005_01_放回時間格.wav").unlink()
    before = _redo(w)
    k2, t5, t3 = _key(w, "名字:2"), _key(w, "學員段落:T005"), _key(w, "學員段落:T003")
    assert before == {k2: needs.REDO_EDITED, t5: needs.REDO_EDITED, t3: needs.REDO_RETURNED}, before
    tn._quiet(fc.prepare_redo, w, log=lambda s: None)
    made = _fake_generate(w)
    with_gen = {k for g in needs.load(w).gen.values() for k in g.get("卡號") or []}
    want = {k for k, why in before.items() if why in (needs.REDO_EDITED, needs.REDO_RETURNED, needs.REDO_NEW)
            and k in with_gen}
    assert _cards_of(w, made) == want, (made, want)
    assert {k for k, why in before.items() if why == needs.REDO_EDITED} <= _cards_of(w, made)
    assert _fake_generate(w) == set()                    # 再跑一次：沒有要做的
    assert set(_redo(w)) == {t3}                         # 生成都做完了；退回的那張等組裝做進新的一版


def test_10_second_call_fast_with_cache():
    """情境 10（效能）：第一堂大小的假資料（約 150 張卡、400 句生成句、800 個聲音檔、2000 條歷史），
    `redo_list` 第二次呼叫（快取命中）< 50 毫秒。"""
    w = _upgraded(shift={"S002": 0.1})
    st = needs.load(w)
    now = needs._now()
    od = w / "生成" / "學員"
    for i in range(150):
        c = st.new_card({"類型": "學員段落", "第3步鍵": f"學員段落:X{i:03d}", "原片": [200.0 + i, 200.5 + i]}, now)
        for j in range(3):
            st.gen[(f"X{i:03d}_{j:02d}", "學員")] = {"句id": f"X{i:03d}_{j:02d}", "角色": "學員", "卡號": [c["卡號"]],
                                                    "第幾版": 1, "種子": 7, "對應方式": "計畫", "更新時間": now}
            (od / f"X{i:03d}_{j:02d}.wav").write_bytes(b"w")
            (od / f"X{i:03d}_{j:02d}_放回時間格.wav").write_bytes(b"w")
        st.states[(c["卡號"], 1)] = {"卡號": c["卡號"], "版本號": 1, "成品": None, "狀態": needs.PASSED, "退回原因": None,
                                     "沿用自": None, "紀錄鍵": [], "更新時間": now}
    for i in range(2000):
        st.log(now, "K0001", 3, "改了第3步決定", None, {"欄位": ["tags"]})
    needs.save(w, st)
    first = needs.redo_list(w)
    ms = 1e9
    for _ in range(3):   # 取最快的一次（整套測試平行跑時機器忙，單次會被拖慢）
        t0 = time.perf_counter()
        second = needs.redo_list(w)
        ms = min(ms, (time.perf_counter() - t0) * 1000)
    assert second == first and _key(w, "名字:2") in {r["卡號"] for r in second}
    print(f"  redo_list 第二次 {ms:.1f} 毫秒（第一次含排計畫）")
    assert ms < 50, ms
    # 輸入檔變了（刪了一個聲音檔）→ 不用舊的快取
    (od / "X000_00_放回時間格.wav").unlink()
    assert all(execcounts._stat_key(w) != v[0] for k, v in execcounts._STALE_CACHE.items() if k.startswith(f"{w}|"))


def test_unmark_edited_uses_same_list():
    """`needs.unmark_edited`（第 5 步「復原」）也照 redo_list 判斷：生成程式還會重做的卡，復原後照樣等重做。"""
    w = _upgraded(shift={"S002": 0.1})
    got = tn._quiet(needs.unmark_edited, w, "名字:2")
    assert got["清了"] is False   # 本來就沒有等重做（列出來是因為生成程式會重做）


def test_transcript_product_time_uses_exported_version_log():
    """逐字稿成品時間（closing.last_output_log）：輸出紀錄記的版本號 → 版本表那一版的處理紀錄副本（不是現在的處理紀錄）。"""
    from bookclub import closing

    w = _upgraded()
    chk = wd.read_json(fc.check_path(w))
    chk["輸出成品"] = {"版本號": 1, "處理紀錄產生時間": tn.T1}
    wd.write_json(fc.check_path(w), chk)
    n, log = closing.last_output_log(w)
    assert n == 1 and log["產生時間"] == tn.T1
    tv._assemble(w, tv._t(2), _records())                # 之後又組了第 2 版、還沒輸出：照樣用第 1 版
    n, log = closing.last_output_log(w)
    assert n == 1 and log["產生時間"] == tn.T1


def test_inspect_prints_stale_counts():
    """`bookclub inspect <工作區> 修改需求` 多印「生成程式會重新生成 N 句」「stale 但對不到卡的句子」（§7.3 safeview）。"""
    import io
    from contextlib import redirect_stdout

    from bookclub import safeview

    w = _upgraded(shift={"S002": 0.1})
    buf = io.StringIO()
    with redirect_stdout(buf):
        safeview.main([str(w), "修改需求"])
    out = buf.getvalue()
    assert "生成程式會重新生成 1 句（老師 1）；stale 但對不到卡的句子：0 句" in out
    assert "要重做的卡（needs.redo_list，第 4、5 步同一份）1 張：改了 1" in out


# ---------- 1015-8 審查修正 ----------

def test_fix_transcript_export_without_version_no_uses_version_at():
    """建議：輸出紀錄沒記版本號（舊紀錄）時，照處理紀錄產生時間找版本表那一版（`st.version_at`，跟
    `finalcards._export_info` 一樣），之後又組了新的一版也找得到。"""
    from bookclub import closing

    w = _upgraded()
    tv._assemble(w, tv._t(2), _records())
    chk = wd.read_json(fc.check_path(w))
    chk["輸出成品"] = {"處理紀錄產生時間": tn.T1}
    wd.write_json(fc.check_path(w), chk)
    n, log = closing.last_output_log(w)
    assert n == 1 and log["產生時間"] == tn.T1, n

def _gen_cards_in_v(w: Path, roles: set | None = None) -> set[str]:
    """生成句表裡有句子、而且做進過最新一版的卡（roles：只看這幾類）。"""
    st = needs.load(w)
    vn = max(st.versions)
    return {k for (sid, role), g in st.gen.items() if roles is None or role in roles
            for k in g.get("卡號") or [] if (k, vn) in st.states}


def test_fix1_cut_across_range_start_not_edited():
    """必修 1：部分範圍輸出（從 1.0 秒開始），刪除段落 0–3.6 跨過範圍開頭——組裝時剪點只篩不切，處理紀錄記 0–3.6；
    卡片那端切到範圍是 1.0–3.6。兩邊都切到範圍再比 → 不算改了。"""
    w = _upgraded()
    tv._assemble(w, tv._t(1), _records(), rng=(1.0, 180.0))
    assert needs.load(w).latest_version()["範圍"] == [1.0, 180.0]
    assert _redo(w) == {}


def test_fix2_missing_gen_logs_listed():
    """必修 2：生成紀錄不見了（整個 生成/ 被刪、或只刪老師紀錄）——生成程式會整類重做，那幾張卡要列出（以前整類跳過）。"""
    w = _upgraded()
    import shutil

    want_all = _gen_cards_in_v(w)
    want_t = _gen_cards_in_v(w, {"老師"})
    (w / "生成" / "老師紀錄.json").unlink()
    got = _redo(w)
    assert set(got) == want_t and len(want_t) == 2 and set(got.values()) == {needs.REDO_EDITED}, got
    assert execute.names_done(w)[0] is False
    shutil.rmtree(w / "生成")
    got = _redo(w)
    assert set(got) == want_all and len(want_all) == 4, (got, want_all)


def test_fix3_roster_change_busts_cache():
    """必修 3：快取熱了之後改名冊（學員句的文字會變：名冊上的名字才換成代號）→ redo_list、逐類統計馬上反映。"""
    w = _upgraded()
    assert _redo(w) == {}
    roster = data_dir() / "名冊.csv"
    old = roster.read_text(encoding="utf-8")
    before = {x["id"]: x["text"] for x in fc._redo_ctx(w)["學員"]}
    try:
        roster.write_text("中文名,其他寫法,英文代號,聲線,性別\n阿明,,Tom,,男\n", encoding="utf-8")   # 拿掉小美
        after = {x["id"]: x["text"] for x in fc._redo_ctx(w)["學員"]}
        changed = {i for i in before if before[i] != after.get(i)}
        assert changed, "名冊改了應該改到學員句的文字"
        got = _redo(w)
        assert set(got) == _cards_of(w, {("學員", i) for i in changed}) and got, got
        row = {(r["類型"], r["做法"]): r for r in execcounts.counts(w)}[("學員段落", "學員重念（用替代聲音）")]
        assert row["完成"] == row["總數"] - len(changed)
    finally:
        roster.write_text(old, encoding="utf-8")
    assert _redo(w) == {}


def test_fix_student_range_cut_by_version_range():
    """自述第 3 點：第 4 步只做 0–100 秒，學員段落 T005（92–111.6）的時間格切在 100 秒——照最新一版的範圍排計畫，
    不會一直顯示「改了」；用整支的計畫排就會（92–111.6 跟紀錄的 92–99.6 對不上）。"""
    w = _upgraded()
    _write_gen(w, rng=(0.0, 100.0))
    tv._assemble(w, tv._t(1), _records(), rng=(0.0, 100.0))
    assert _redo(w) == {}
    assert ("學員", "T005_01") in execcounts.stale_ids(w)            # 整支的計畫：對不上
    assert ("學員", "T005_01") not in execcounts.stale_ids(w, 0.0, 100.0)


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
