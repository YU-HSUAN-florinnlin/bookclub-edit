"""bookclub/needs.py（修改需求紀錄，10-14 第5步2.0 第1段）的測試。

用 tests/fake_workdir.py 的合成資料（電子音、假逐字稿）＋手寫的處理紀錄與成品檢查，做 6 種舊工作區
（沒做過第 4 步／做過沒退回／有退回沒重做／重做中殘留／換過範圍／輸出過），不跑模型、不碰真的工作區。
獨立可跑：.venv/bin/python tests/test_needs.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import fake_workdir  # noqa: E402

_DATA = Path(tempfile.mkdtemp())
(_DATA / "名冊.csv").write_text("中文名,其他寫法,英文代號,聲線,性別\n小美,,Amy,,女\n阿明,,Tom,,男\n", encoding="utf-8")
os.environ["BOOKCLUB_DATA_DIR"] = str(_DATA)

from bookclub import finalcheck as fc  # noqa: E402
from bookclub import needs, proclog  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

T1, T2, T3 = "2026-10-12T10:00:00", "2026-10-12T12:00:00", "2026-10-12T14:00:00"
_ROOT = None


def _fresh() -> Path:
    """每個測試一份新的假工作區（影片只做一次，複製比較快）。"""
    global _ROOT
    if _ROOT is None:
        _ROOT = Path(tempfile.mkdtemp()) / "base"
        fake_workdir.make(_ROOT)
    d = Path(tempfile.mkdtemp())
    shutil.copytree(_ROOT, d / "base")
    w = d / "base" / "工作區"
    data = json.loads((w / "分析結果.json").read_text(encoding="utf-8"))
    data["video"] = str(d / "base" / "假影片.mp4")
    (w / "分析結果.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return w


def _rec(kind, a, b, keys, f=None, out=None, sound=True):
    o = out or [a - 3.6, b - 3.6]
    return {"類型": kind, "原片": [a, b], "成品": o, "動到聲音": sound, "要人聽": False, "檔案": f, "文字": None,
            "覆核項目": keys, "做了什麼": kind, **({"檔案指紋": f"fp-{f}"} if f else {})}


def _records():
    return [_rec("刪除", 0.0, 3.6, ["刪除段落:S1"], out=[0.0, 0.0]),
            _rec("學員重念", 40.0, 55.0, ["學員段落:T003"], "生成/學員/T003_01.wav"),
            _rec("學員重念", 55.0, 71.6, ["學員段落:T003"], "生成/學員/T003_02.wav"),
            _rec("名字整句換掉", 76.0, 80.0, ["名字:1"], "生成/老師/S001.wav"),
            _rec("學員重念", 92.0, 111.6, ["學員段落:T005"], "生成/學員/T005_01.wav"),
            _rec("停格", 99.0, 99.0, [], out=[95.4, 95.9])]   # 對不到卡片（覆核項目空、動到聲音）→ 待釐清


def _log(stamp=T1, tag="0-3", rng=(0.0, 180.0), recs=None) -> dict:
    return {"版本": 1, "來源": f"render video {tag}", "範圍": list(rng), "產生時間": stamp, "片段": None,
            "紀錄": recs if recs is not None else _records(),
            "未登記的變動": [{"原片": [130.0, 130.4], "長度秒": 0.4, "格數": 20}]}


def _prod(w: Path, name: str, stamp: str, after_s: float = 30.0) -> str:
    f = w / "輸出" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"fake-mp4")
    t = datetime.fromisoformat(stamp).timestamp() + after_s
    os.utime(f, (t, t))
    return f"輸出/{name}"


def _check(log: dict, results: dict | None = None, **extra) -> dict:
    by = {fc.record_key(r): r for r in log["紀錄"]}
    rows = {k: {"結果": v[0], "原因": v[1] if len(v) > 1 else "", "指紋": fc.content_print(by[k]), "更新時間": T1}
            for k, v in (results or {}).items()}
    return {"版本": 1, "處理紀錄產生時間": log["產生時間"], "指紋版本": 2, "處理紀錄快照": fc.snapshot(log), "逐筆": rows,
            "整片退回": [], "未登記確認": {}, "看過區段": [], "成品影片": None, **extra}


def _setup(w: Path, log: dict | None, check: dict | None) -> None:
    if log is not None:
        wd.write_json(proclog.log_path(w), log)   # wd 直接寫：不經 proclog._write（不建版本，這是「舊版工具」留下的）
    if check is not None:
        wd.write_json(fc.check_path(w), check)


def _sha(w: Path) -> dict[str, str]:
    out = {}
    for p in sorted(w.rglob("*")):
        if p.is_file() and needs.DIR_NAME not in p.relative_to(w).parts:
            out[str(p.relative_to(w))] = hashlib.sha1(p.read_bytes()).hexdigest()
    return out


def _quiet(fn, *a, **kw):
    with redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _card(w: Path, key: str) -> dict:
    return next(c for c in needs.card_rows(w) if c.get("第3步鍵") == key)


def _state(w: Path, card: str, v: int) -> str:
    return needs.card_state(needs.load(w), card, v)


# ---------- 6 種舊工作區 ----------

def test_upgrade_no_step4_yet():
    """沒做過第 4 步：只有卡片，沒有版本；舊檔一個位元都沒變。"""
    w = _fresh()
    before = _sha(w)
    rep = _quiet(needs.upgrade, w)
    assert rep["升級了"] and _sha(w) == before
    cards = needs.card_rows(w)
    kinds = sorted(c["類型"] for c in cards)
    assert kinds == sorted(["刪除段落", "刪除段落", "學員段落", "學員段落", "學員段落", "重疊", "名字", "名字"]), kinds
    assert [c["卡號"] for c in cards] == [f"K{i:04d}" for i in range(1, 9)]
    assert all(c["狀態"] == needs.ALIVE and c["來源"] == needs.SRC_AUTO for c in cards)
    assert needs.version_rows(w) == [] and rep["沒有處理紀錄"] and rep["版本"] is None
    assert (w / "修改需求" / "升級報告.md").is_file() and (w / "修改需求" / "升級報告.json").is_file()
    # 第 3 步決定是同步副本：學員段落帶校對稿、已確認
    t3 = _card(w, "學員段落:T003")
    assert "校對稿" in t3["第3步決定"] and t3["第3步決定"]["已確認"] is False and t3["原片"] == [40.0, 71.6]


def test_upgrade_done_no_returns():
    """做過第 4 步、全部通過：版本 1（升級前的成品）、卡片狀態通過、生成句對到卡片。"""
    w = _fresh()
    log = _log()
    prod = _prod(w, "成品_0-3_sw.mp4", T1)
    keys = {fc.record_key(r): ("通過",) for r in log["紀錄"]}
    _setup(w, log, _check(log, keys, 成品影片=prod, 未登記確認={"130.00": {"結果": "沒問題"}}))
    before = _sha(w)
    rep = _quiet(needs.upgrade, w)
    assert _sha(w) == before
    v = needs.version_rows(w)
    assert len(v) == 1 and v[0]["版本號"] == 1 and v[0]["備註"] == needs.NOTE_UPGRADE and v[0]["範圍"] == [0.0, 180.0]
    assert v[0]["標記"] == "0-3" and v[0]["成品檔"] == [prod] and v[0]["輸出做法"] == ["sw"]
    assert (w / v[0]["處理紀錄"]).is_file() and wd.read_json(w / v[0]["處理紀錄"]) == log   # 處理紀錄副本
    st = needs.load(w)
    t3, t5, n1 = (_card(w, k)["卡號"] for k in ("學員段落:T003", "學員段落:T005", "名字:1"))
    assert all(needs.card_state(st, c, 1) == needs.PASSED for c in (t3, t5, n1))
    assert needs.card_state(st, _card(w, "學員段落:T007")["卡號"], 1) == needs.NOT_MADE   # 沒處理紀錄 → 還沒生成
    assert not [c for c in st.cards.values() if c.get("等重做")]
    # 一張卡對到兩筆處理紀錄（學員段落切兩格）：成品時間取聯集
    assert st.states[(t3, 1)]["成品"] == [36.4, 68.0] and len(st.states[(t3, 1)]["紀錄鍵"]) == 2
    # 生成句表：T003 的兩格 → 同一張卡；老師 S001 → 名字:1
    g = {(r["句id"], r["角色"]): r for r in needs.gen_rows(w)}
    assert g[("T003_01", "學員")]["卡號"] == [t3] and g[("T003_02", "學員")]["卡號"] == [t3]
    assert g[("S001", "老師")]["卡號"] == [n1] and all(r["對應方式"] == "計畫" for r in g.values())
    # 沒登記的變動一張卡、確認沒問題 → 通過
    un = [c for c in st.cards.values() if c["類型"] == needs.UNLOGGED]
    assert len(un) == 1 and needs.card_state(st, un[0]["卡號"], 1) == needs.PASSED
    assert rep["成品狀態"].get(needs.PASSED) == 6   # 剪掉 S1、T003、名字 1、T005、停格（待釐清）、沒登記的變動


def test_upgrade_returned_not_redone():
    """有退回、沒重做：退回原因放「補充」（選項空的），那張卡等重做（起因退回）。"""
    w = _fresh()
    log = _log()
    _setup(w, log, _check(log, {"學員重念@40.00": ("通過",), "學員重念@55.00": ("退回重做", "第二句念錯"),
                                "名字整句換掉@76.00": ("通過",)}))
    _quiet(needs.upgrade, w)
    st = needs.load(w)
    t3 = _card(w, "學員段落:T003")["卡號"]
    row = st.states[(t3, 1)]
    assert row["狀態"] == needs.RETURNED and row["退回原因"] == {"選項": [], "補充": "第二句念錯"}
    assert st.cards[t3]["等重做"]["起因"] == needs.WHY_RETURNED
    assert needs.card_state(st, _card(w, "名字:1")["卡號"], 1) == needs.PASSED
    assert needs.card_state(st, _card(w, "學員段落:T005")["卡號"], 1) == needs.NOT_SEEN   # 有處理紀錄、沒判斷
    # 歷史只放原因字數，不放原因文字
    hist = (w / "修改需求" / "歷史.jsonl").read_text(encoding="utf-8")
    assert "第二句念錯" not in hist and "原因字數" in hist


def test_upgrade_leftover_redoing_counts_as_still_to_redo():
    """重做中殘留（bug 三）：不管是不是殘留，裡面每一項都當「還要重做」——那張卡通過了也一樣等重做。"""
    w = _fresh()
    log = _log()
    _setup(w, log, _check(log, {"名字整句換掉@76.00": ("通過",)},
                          重做中={"時間": "2026-10-12T11:00:00", "項目": [
                              {"鍵": "名字整句換掉@76.00", "覆核項目": ["名字:1"], "來源": "逐筆"}]},
                          重做過={"時間": "2026-10-11T09:00:00", "處理紀錄產生時間": "2026-10-11T08:00:00", "項目": [
                              {"鍵": "學員重念@40.00", "覆核項目": ["學員段落:T003"], "來源": "逐筆", "第幾版": 2}]}))
    rep = _quiet(needs.upgrade, w)
    st = needs.load(w)
    n1, t3 = _card(w, "名字:1")["卡號"], _card(w, "學員段落:T003")["卡號"]
    assert needs.card_state(st, n1, 1) == needs.PASSED and st.cards[n1]["等重做"] == {"起因": "退回", "時間": "2026-10-12T11:00:00"}
    assert n1 in rep["等重做"]
    hist = needs.read_history(w)
    assert any(h["卡號"] == t3 and h["動作"] == "重做過（升級時記）" and h["後"]["第幾版"] == 2 for h in hist)


def test_upgrade_changed_range_only_this_logs_products():
    """換過範圍：版本 1 只記這份處理紀錄做出的成品；別的範圍的舊成品不建版本、檔案照留。範圍外的卡＝還沒生成。"""
    w = _fresh()
    recs = [r for r in _records() if r["原片"][0] < 60]
    log = _log(T2, tag="0-1", rng=(0.0, 60.0), recs=recs)
    old = _prod(w, "成品_0-3_sw.mp4", T1)
    new = _prod(w, "成品_0-1_sw.mp4", T2)
    _setup(w, log, _check(log, {}, 成品影片=old))   # 第 5 步還開著舊範圍那支（10-13 以前的工作區）
    rep = _quiet(needs.upgrade, w)
    v = needs.version_rows(w)[0]
    assert v["成品檔"] == [new] and v["範圍"] == [0.0, 60.0] and v["標記"] == "0-1"
    assert rep["舊成品沒建版本"] == 1 and (w / old).is_file()
    st = needs.load(w)
    assert needs.card_state(st, _card(w, "學員段落:T003")["卡號"], 1) == needs.NOT_SEEN
    assert needs.card_state(st, _card(w, "學員段落:T007")["卡號"], 1) == needs.NOT_MADE


def test_upgrade_exported_and_whole_flags():
    """輸出過：輸出紀錄歸到版本 1。整片看的退回：對到一張卡 → 那張卡退回；對到 0 張 → 待釐清卡（升級時對不到）。"""
    w = _fresh()
    log = _log()
    prod = _prod(w, "成品_0-3_sw.mp4", T1)
    out = {"時間": "2026-10-12T13:00:00", "來源": prod, "檔案": "輸出/最終成品_0-3_sw_x.mp4", "檢查完才輸出": False,
           "看過百分比": 40, "沒通過筆數": 2, "處理紀錄產生時間": T1}
    _setup(w, log, _check(log, {}, 成品影片=prod, 輸出成品=out, 輸出紀錄=[out], 整片退回=[
        {"id": "R001", "成品秒": 50.0, "原片秒": 92.0, "覆核項目": ["學員段落:T005"], "原因": "聲音怪", "建立時間": T1},
        {"id": "R002", "成品秒": 120.0, "原片秒": 124.0, "覆核項目": [], "原因": "這裡怪", "建立時間": T1}]))
    rep = _quiet(needs.upgrade, w)
    v = needs.version_rows(w)[0]
    assert len(v["輸出紀錄"]) == 1 and v["輸出紀錄"][0]["檔案"] == out["檔案"]
    st = needs.load(w)
    assert needs.card_state(st, _card(w, "學員段落:T005")["卡號"], 1) == needs.RETURNED
    unclear = [c for c in st.cards.values() if c["類型"] == needs.UNCLEAR]
    whole = [c for c in unclear if (c.get("舊檔鍵") or {}).get("整片退回") == "R002"]
    rec = [c for c in unclear if (c.get("舊檔鍵") or {}).get("處理紀錄") == "停格@99.00"]
    assert len(whole) == 1 and whole[0]["來源"] == needs.SRC_UPGRADE and needs.card_state(st, whole[0]["卡號"], 1) == needs.RETURNED
    assert len(rec) == 1 and rec[0]["來源"] == needs.SRC_UPGRADE
    assert len(rep["對不到"]) == 2


# ---------- 只跑一次、試跑、備份、重跑一樣 ----------

def _strip(rows: list[dict]) -> list[dict]:
    drop = {"建立時間", "更新時間", "時間"}

    def clean(x):
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items() if k not in drop}
        if isinstance(x, list):
            return [clean(v) for v in x]
        return x
    return clean(rows)


def test_upgrade_only_once_dry_run_and_backup():
    w = _fresh()
    log = _log()
    _setup(w, log, _check(log, {"學員重念@55.00": ("退回重做", "念錯")}))
    rep = _quiet(needs.upgrade, w, dry_run=True)   # 試跑：什麼檔都不寫
    assert rep["試跑"] and not rep["升級了"] and not (w / "修改需求").exists()
    assert rep["卡片"]["總數"] == 10 and rep["版本"]["版本號"] == 1
    real = _quiet(needs.upgrade, w)
    assert real["升級了"] and real["卡片"] == rep["卡片"]
    bk = w / real["備份"]
    assert (bk / "覆核" / "成品檢查.json").is_file() and (bk / "校對" / "段落.json").is_file()
    assert (bk / "生成" / "處理紀錄.json").is_file() and real["備份檔數"] >= 3
    assert not list(bk.rglob("*.wav")) and not list(bk.rglob("*.mp4"))
    cards1 = needs.card_rows(w)
    again = _quiet(needs.upgrade, w)            # 只跑一次
    assert not again["升級了"] and needs.card_rows(w) == cards1
    assert _quiet(needs.ensure, w) is None and needs.card_rows(w) == cards1
    # 把 修改需求/ 改名再升級：結果一樣（時間欄位除外）
    states1 = needs.state_rows(w)
    gen1 = needs.gen_rows(w)
    (w / "修改需求").rename(w / "修改需求_停用")
    _quiet(needs.upgrade, w)
    assert _strip(needs.card_rows(w)) == _strip(cards1)
    assert _strip(needs.state_rows(w)) == _strip(states1) and _strip(needs.gen_rows(w)) == _strip(gen1)


def test_cli_upgrade_dry_run_prints_numbers_only():
    from bookclub import cli

    w = _fresh()
    log = _log()
    _setup(w, log, _check(log, {"學員重念@55.00": ("退回重做", "第二句念錯")}))
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert cli.main(["upgrade", str(w), "--dry-run"]) == 0
    text = buf.getvalue()
    assert "試跑" in text and "卡片 10 張" in text and "待釐清" in text and not (w / "修改需求").exists()
    assert "第二句念錯" not in text and "分享的第" not in text and "小美" not in text
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert cli.main(["upgrade", str(w)]) == 0
        assert cli.main(["upgrade", str(w)]) == 0
    assert "已寫入" in buf.getvalue() and "已經升級過了" in buf.getvalue()


# ---------- 沒升級過的工作區：讀取不壞、不建資料夾 ----------

def test_old_workdir_without_folder_reads_fine():
    w = _fresh()
    log = _log()
    _setup(w, log, _check(log, {}))
    assert needs.card_rows(w) == [] and needs.version_rows(w) == [] and needs.state_rows(w) == []
    assert needs.read_history(w) == [] and needs.load(w).latest_version() is None
    assert needs.sync_cards(w) is None and needs.mirror_final(w) is None and needs.record_gen_plan(w) is None
    assert needs.add_version(w, _log(T2)) is None
    needs.add_product(w, T1, "輸出/成品_0-3_sw.mp4")
    _quiet(fc.decide_record, w, "學員重念@40.00", "通過")   # 第 5 步照舊能用（雙寫不做）
    _quiet(proclog.write_log, w, _log(T2))                   # 組裝照舊能用（不建版本）
    assert not (w / "修改需求").exists()
    from bookclub import safeview

    out = safeview.run([str(w), "修改需求"])
    assert "還沒升級" in out[0] and not (w / "修改需求").exists()


# ---------- 雙寫：第 5 步判斷、第 4 步版本 ----------

def test_dual_write_step5_keeps_old_file_identical():
    """第 5 步按通過／退回：舊檔 `成品檢查.json` 跟沒升級的工作區一模一樣（讀的那一邊不變）；新紀錄跟著變。"""
    a, b = _fresh(), _fresh()
    log = _log()
    for w in (a, b):
        _setup(w, log, _check(log, {}))
    _quiet(needs.upgrade, a)
    for w in (a, b):
        _quiet(fc.decide_record, w, "學員重念@40.00", "通過")
        _quiet(fc.decide_record, w, "學員重念@55.00", "退回重做", "結尾怪")
        _quiet(fc.decide_unlogged, w, "130.00", "沒問題")
        _quiet(fc.add_whole_redo, w, 120.0, "這裡怪")
    ca, cb = wd.read_json(fc.check_path(a)), wd.read_json(fc.check_path(b))
    strip = lambda c: {k: v for k, v in c.items() if k not in ("整片退回",)}   # noqa: E731
    assert _strip(strip(ca)) == _strip(strip(cb))
    assert [x["id"] for x in ca["整片退回"]] == [x["id"] for x in cb["整片退回"]] == ["R001"]
    st = needs.load(a)
    t3 = _card(a, "學員段落:T003")["卡號"]
    assert needs.card_state(st, t3, 1) == needs.RETURNED and st.cards[t3]["等重做"]
    assert st.states[(t3, 1)]["退回原因"]["補充"] == "結尾怪"
    _quiet(fc.decide_record, a, "學員重念@55.00", "通過")   # 改成通過：兩格都通過 → 卡片通過、不用重做
    st = needs.load(a)
    assert needs.card_state(st, t3, 1) == needs.PASSED and not st.cards[t3]["等重做"]
    acts = [h["動作"] for h in needs.read_history(a) if h["卡號"] == t3]
    assert acts[-2:] == ["通過", "取消等重做"] and "退回" in acts
    # 只記看過區段：判斷沒變，不重算（歷史不加）
    n = len(needs.read_history(a))
    _quiet(fc.add_watched, a, [[0, 5.0]])
    assert len(needs.read_history(a)) == n


def test_new_version_on_assembly_carries_pass_and_marks_changed():
    """第 4 步組裝（寫處理紀錄）：建版本 2（範圍、以第 1 版為基礎、處理紀錄副本）；內容沒變、上一版通過 → 通過（沿用第 1 版）；
    生成檔換了 → 還沒看、列在「這一版做了哪幾張」；重做完（finish_redo）那張卡不再等重做。"""
    w = _fresh()
    log = _log()
    _setup(w, log, _check(log, {"學員重念@40.00": ("通過",), "學員重念@55.00": ("通過",),
                                "名字整句換掉@76.00": ("退回重做", "念錯")}))
    _quiet(needs.upgrade, w)
    n1, t3 = _card(w, "名字:1")["卡號"], _card(w, "學員段落:T003")["卡號"]
    _quiet(fc.prepare_redo, w)   # 第 4 步開始重做退回的：重做中
    assert needs.load(w).cards[n1]["等重做"]
    recs = _records()
    recs[3] = {**recs[3], "檔案指紋": "fp-新的"}   # 名字那一句重新生成了
    from datetime import timedelta

    later = (datetime.now() + timedelta(minutes=1)).isoformat(timespec="seconds")   # 組裝比按重做晚
    new = _log(later, recs=recs)
    _quiet(proclog.write_log, w, new)              # → proclog._write → needs.add_version
    _prod(w, "成品_0-3_sw.mp4", later)
    needs.add_product(w, later, "輸出/成品_0-3_sw.mp4")
    v = needs.version_rows(w)
    assert [x["版本號"] for x in v] == [1, 2] and v[1]["以哪一版為基礎"] == 1 and v[1]["範圍"] == [0.0, 180.0]
    assert v[1]["成品檔"] == ["輸出/成品_0-3_sw.mp4"] and (w / v[1]["處理紀錄"]).is_file()
    assert v[1]["這一版做了哪幾張"] == [n1]
    st = needs.load(w)
    assert needs.card_state(st, t3, 2) == needs.PASSED and st.states[(t3, 2)]["沿用自"] == 1
    assert needs.card_state(st, n1, 2) == needs.NOT_SEEN
    _quiet(fc.finish_redo, w)   # 組裝做完收尾（舊流程）：重做中拿掉 → 卡片不再等重做
    st = needs.load(w)
    assert not st.cards[n1]["等重做"] and needs.card_state(st, n1, 2) == needs.NOT_SEEN
    assert any(h["卡號"] == n1 and h["動作"] == "重做進第2版" for h in needs.read_history(w))
    # 舊版的判斷不被新版改到
    assert needs.card_state(st, n1, 1) == needs.RETURNED
    # 同一份處理紀錄再寫一次不會多一版
    _quiet(proclog.write_log, w, new)
    assert len(needs.version_rows(w)) == 2


def test_old_tool_log_becomes_version_on_open():
    """回滾到舊版工具組裝過（處理紀錄換新、版本表裡沒有）：打開時補一個版本，標「舊版工具組的」。"""
    w = _fresh()
    log = _log()
    _setup(w, log, _check(log, {}))
    _quiet(needs.upgrade, w)
    _setup(w, _log(T3), None)   # 舊工具直接寫處理紀錄（沒經過新程式）
    _quiet(needs.ensure, w)
    v = needs.version_rows(w)
    assert [x["版本號"] for x in v] == [1, 2] and v[1]["備註"] == needs.NOTE_OLD_TOOL and v[1]["時間"] == T3


# ---------- 第 3 步同步：切段、併段、改 id、還原 ----------

def _turns(w: Path) -> dict:
    return wd.read_json(w / "校對" / "段落.json")


def test_sync_cards_split_merge_rename():
    w = _fresh()
    _quiet(needs.upgrade, w)
    k3, k5, k7 = (_card(w, f"學員段落:{t}")["卡號"] for t in ("T003", "T005", "T007"))
    data = _turns(w)
    ts = {t["id"]: t for t in data["段落"]}
    # 切段：T003 後半變成新的 T009
    t3 = ts["T003"]
    half = len(t3["句子"]) // 2
    tail = {**t3, "id": "T009", "句子": t3["句子"][half:], "start": 56.0}
    t3["句子"] = t3["句子"][:half]
    t3["end"] = 55.6
    # 併段：T005 併進 T007（T007 拿到 T005 的句子；T005 不見了）
    ts["T007"]["句子"] = ts["T005"]["句子"] + ts["T007"]["句子"]
    ts["T007"]["start"] = ts["T005"]["start"]
    data["段落"] = [t for t in data["段落"] if t["id"] != "T005"]
    data["段落"].insert(3, tail)
    wd.write_json(w / "校對" / "段落.json", data)
    res = _quiet(needs.sync_cards, w)
    assert res["新增"] == 1 and res["併入"] == 1, res
    cards = {c["卡號"]: c for c in needs.card_rows(w)}
    assert cards[k3]["第3步鍵"] == "學員段落:T003" and cards[k3]["狀態"] == needs.ALIVE and cards[k3]["原片"][1] == 55.6
    assert cards[k5]["狀態"] == needs.MERGED and cards[k5]["指向"] == [k7]
    new = _card(w, "學員段落:T009")
    assert new["卡號"] == "K0009" and new["來源"] == needs.SRC_AUTO
    hist = needs.read_history(w)
    assert any(h["卡號"] == new["卡號"] and h["動作"] == "從別張拆出" and h["後"] == {"從": k3} for h in hist)
    assert any(h["卡號"] == k3 and h["動作"] == "改範圍" for h in hist)
    # 改 id（句子一樣）：同一張卡、改鍵；卡號不重用
    data = _turns(w)
    for t in data["段落"]:
        if t["id"] == "T009":
            t["id"] = "T099"
    wd.write_json(w / "校對" / "段落.json", data)
    _quiet(needs.sync_cards, w)
    assert _card(w, "學員段落:T099")["卡號"] == new["卡號"]
    assert any(h["卡號"] == new["卡號"] and h["動作"] == "改鍵" and h["後"] == "學員段落:T099" for h in needs.read_history(w))
    # 沒變就不寫
    n = len(needs.read_history(w))
    assert _quiet(needs.sync_cards, w) is None and len(needs.read_history(w)) == n


def test_sync_cards_restore_and_decision_history():
    """建議刪除選「不刪」→ 卡片刪掉（還原）；選回刪除 → 恢復有效。改第 3 步決定 → 歷史記欄位名（不記內容）。"""
    from bookclub import review

    w = _fresh()
    _quiet(needs.upgrade, w)
    s1 = _card(w, "刪除段落:S1")["卡號"]
    _quiet(review.decide_cut_suggestion, w, "S1", "不刪")
    _quiet(needs.sync_cards, w)
    assert needs.load(w).cards[s1]["狀態"] == needs.REMOVED
    _quiet(review.decide_cut_suggestion, w, "S1", "刪除")
    _quiet(needs.sync_cards, w)
    assert needs.load(w).cards[s1]["狀態"] == needs.ALIVE
    from bookclub import turns

    _quiet(turns.save_turn, w, "T003", {"校對稿": "改過的字。", "已確認": True})
    _quiet(needs.sync_cards, w)
    k3 = _card(w, "學員段落:T003")
    h = [x for x in needs.read_history(w) if x["卡號"] == k3["卡號"] and x["動作"] == "改了第3步決定"]
    assert h and "校對稿" in h[-1]["後"]["欄位"] and "已確認" in h[-1]["後"]["欄位"]
    assert "改過的字" not in (w / "修改需求" / "歷史.jsonl").read_text(encoding="utf-8")
    assert k3["第3步決定"]["校對稿"] == "改過的字。"


def test_sync_ignores_review_timer():
    from bookclub import review

    w = _fresh()
    _quiet(needs.upgrade, w)
    _quiet(review.add_time, w, 10)   # 先有覆核決定檔
    snap = needs.step3_snapshot(w)
    _quiet(review.add_time, w, 20)   # 第 3 步計時每 30 秒寫一次覆核決定：不算第 3 步改了
    assert needs.step3_snapshot(w) == snap


# ---------- 第 4 步：生成句 ----------

def test_record_gen_plan_on_execute_start():
    w = _fresh()
    _quiet(needs.upgrade, w)
    wd.write_json(needs.table_path(w, needs.T_GEN), {"表": "生成句", "列": []})   # 清掉升級時記的，看排計畫時會不會記
    res = _quiet(needs.record_gen_plan, w)
    assert res["對上"] >= 4 and not res["對不到"]
    g = {(r["句id"], r["角色"]): r["卡號"] for r in needs.gen_rows(w)}
    assert g[("T005_01", "學員")] == [_card(w, "學員段落:T005")["卡號"]]
    assert g[("S002", "老師")] == [_card(w, "名字:2")["卡號"]]


def test_student_numbers_count_in_whole_turn():
    """學員生成句的序號在整個段落裡數：範圍切到段落時，同一格的號碼跟整段一樣（以前從範圍開頭重新數）。"""
    from bookclub import students

    sents = [{"id": f"s{i}", "start": i * 4.0, "end": i * 4.0 + 3.6, "text": "字" * 10} for i in range(6)]
    full = students.plan_slots(sents)
    assert len(full) >= 2
    assert students.turn_numbers(full, sents, sents, []) == list(range(1, len(full) + 1))
    later = [s for s in sents if s["id"] in {x["id"] for x in full[-1]["句子"]}]   # 範圍只蓋到最後一格的句子
    part = students.plan_slots(later)
    assert students.turn_numbers(part, sents, later, []) == [len(full)]


# ---------- inspect、safe_id ----------

def test_inspect_needs_prints_ids_not_text():
    from bookclub import safeview

    w = _fresh()
    log = _log()
    _setup(w, log, _check(log, {"學員重念@55.00": ("退回重做", "第二句念錯")}))
    _quiet(needs.upgrade, w)
    before = {p: p.stat().st_mtime_ns for p in (w / "修改需求").rglob("*") if p.is_file()}
    text = "\n".join(safeview.run([str(w), "修改需求"]))
    assert "卡號=K0002" in text and "第3步鍵=學員段落:T003" in text and "狀態=退回" in text and "有原因=是" in text
    assert "第二句念錯" not in text and "分享的第" not in text and "小美" not in text and "阿明" not in text
    assert "動作=建立" in text and "動作=組裝進第1版" in text and "紀錄鍵=[學員重念@40.00, 學員重念@55.00]" in text
    assert {p: p.stat().st_mtime_ns for p in (w / "修改需求").rglob("*") if p.is_file()} == before
    only = "\n".join(safeview.run([str(w), "修改需求", "--id", "K0002"]))
    assert "卡號=K0002" in only and "卡號=K0003  類型" not in only
    assert safeview.safe_id("K0001") and safeview.safe_id("K1234")


# ---------- 伺服器打開時自動升級 ----------

def test_server_get_upgrades_once_and_not_while_running():
    from bookclub import server

    class Stub:
        def __init__(self, w, running):
            self.workdir, self._run = w, running

        def exec_running(self):
            return self._run

    w = _fresh()
    _quiet(server._needs_ensure, Stub(w, True))     # 第 4 步執行中：先不升級
    assert not needs.exists(w)
    _quiet(server._needs_ensure, Stub(w, False))
    assert needs.exists(w)
    stamp = needs.load(w).fmt["升級時間"]
    _quiet(server._needs_ensure, Stub(w, False))
    assert needs.load(w).fmt["升級時間"] == stamp


# ---------- 10-15 第1段審查修正 ----------

def test_whole_redo_cancel_recomputes_state():
    """必修 1：整片退回（對不到卡 → 待釐清卡退回）取消後，那張卡不再停在「退回」：卡片刪掉、這一版改回還沒看、不等重做。
    同一處再標一次 → 同一張卡恢復有效、又是退回。"""
    w = _fresh()
    log = _log()
    _setup(w, log, _check(log, {}))
    _quiet(needs.upgrade, w)
    r = _quiet(fc.add_whole_redo, w, 120.0, "這裡怪")["項目"]
    st = needs.load(w)
    k = next(c for c in st.cards.values() if (c.get("舊檔鍵") or {}).get("整片退回") == r["id"])["卡號"]
    assert needs.card_state(st, k, 1) == needs.RETURNED and st.cards[k]["等重做"]
    _quiet(fc.remove_whole_redo, w, r["id"])
    st = needs.load(w)
    assert needs.card_state(st, k, 1) == needs.NOT_SEEN, needs.card_state(st, k, 1)
    assert st.cards[k]["狀態"] == needs.REMOVED and not st.cards[k]["等重做"]
    assert st.states[(k, 1)]["退回原因"] is None
    acts = [h["動作"] for h in needs.read_history(w) if h["卡號"] == k]
    assert "改回還沒看" in acts and "刪掉" in acts and "取消等重做" in acts
    r2 = _quiet(fc.add_whole_redo, w, 120.0, "還是怪")["項目"]
    st = needs.load(w)
    k2 = next(c for c in st.cards.values() if (c.get("舊檔鍵") or {}).get("整片退回") is not None
              and c["狀態"] == needs.ALIVE)["卡號"]
    assert k2 == k and r2["id"] and needs.card_state(st, k, 1) == needs.RETURNED


def test_whole_redo_on_card_outside_version_cancel_back_to_not_made():
    """必修 1（另一種）：整片退回對到一張這一版沒處理到的卡 → 那張卡這一版退回；取消 → 回到「還沒生成」（拿掉那一列）。"""
    w = _fresh()
    log = _log()
    _setup(w, log, _check(log, {}))
    _quiet(needs.upgrade, w)
    k7 = _card(w, "學員段落:T007")["卡號"]
    index = needs._index(w)
    st = needs.load(w)
    v = st.version_at(T1)
    chk = fc.refresh(_check(log, {}, 整片退回=[{"id": "R009", "成品秒": 140.0, "原片秒": 143.6,
                                                 "覆核項目": ["學員段落:T007"], "原因": "怪", "建立時間": T1}]), log)
    needs.apply_check(st, v, chk, index, T2)
    assert needs.card_state(st, k7, 1) == needs.RETURNED and st.cards[k7]["等重做"]
    needs.apply_check(st, v, fc.refresh(_check(log, {}), log), index, T3)
    assert needs.card_state(st, k7, 1) == needs.NOT_MADE and (k7, 1) not in st.states and not st.cards[k7]["等重做"]
    assert st.cards[k7]["狀態"] == needs.ALIVE
    assert any(h["卡號"] == k7 and h["動作"] == "改回還沒生成" for h in st.history)


def test_upgrade_fails_midway_rerun_no_duplicate_history():
    """必修 2：升級寫到一半失敗 → 沒有半套 修改需求/（留在 修改需求.升級中/）；重跑先清掉它，歷史序號 1..N 不重複。"""
    w = _fresh()
    log = _log()
    _setup(w, log, _check(log, {"學員重念@55.00": ("退回重做", "念錯")}))
    real_write = needs._write

    def boom(path, data):
        if path.name == needs.FORMAT_FILE:
            raise OSError("寫不進去")
        real_write(path, data)

    needs._write = boom
    try:
        try:
            _quiet(needs.upgrade, w)
            raise AssertionError("應該要失敗")
        except OSError:
            pass
    finally:
        needs._write = real_write
    stage = w / needs.STAGING
    assert not (w / needs.DIR_NAME).exists() and not needs.exists(w)
    assert (stage / needs.HISTORY_FILE).is_file()   # 失敗的留在 .升級中（下次重跑先清）
    rep = _quiet(needs.upgrade, w)
    assert rep["升級了"] and needs.exists(w) and not stage.exists()
    nos = [h["序號"] for h in needs.read_history(w)]
    assert nos == list(range(1, len(nos) + 1)), nos
    assert len(list((w / needs.DIR_NAME).glob(f"{needs.BACKUP_PREFIX}*"))) == 1
    # 平常存檔：歷史寫了、格式.json 沒寫到（下一個歷史序號落後）→ load 跟歷史檔最後一條比、取大的
    fm = wd.read_json(w / needs.DIR_NAME / needs.FORMAT_FILE)
    fm["下一個歷史序號"] = 1
    wd.write_json(w / needs.DIR_NAME / needs.FORMAT_FILE, fm)
    _quiet(fc.decide_record, w, "學員重念@55.00", "通過")
    nos = [h["序號"] for h in needs.read_history(w)]
    assert len(nos) == len(set(nos)) and nos == sorted(nos)


def test_old_partial_folder_without_format_is_moved_aside():
    """舊版程式升級失敗留下的 修改需求/（沒有 格式.json）：升級時整個搬開（不刪），換上新的。"""
    w = _fresh()
    (w / needs.DIR_NAME).mkdir()
    (w / needs.DIR_NAME / needs.HISTORY_FILE).write_text('{"序號": 1}\n', encoding="utf-8")
    _quiet(needs.upgrade, w)
    assert needs.exists(w)
    aside = list(w.glob(f"{needs.DIR_NAME}_升級前_*"))
    assert len(aside) == 1 and (aside[0] / needs.HISTORY_FILE).is_file()
    assert [h["序號"] for h in needs.read_history(w)][:1] == [1] and len(needs.read_history(w)) > 1


def test_renumbered_student_slots_in_dry_run_and_report():
    """必修 3：上次組裝範圍切到學員段落 → dry-run 與升級報告印「會換號的學員句」格數；整支範圍＝0。"""
    from bookclub import cli

    w = _fresh()
    recs = [r for r in _records() if r["原片"][0] >= 55]
    log = _log(tag="1-3", rng=(60.0, 180.0), recs=recs)
    _setup(w, log, _check(log, {}))
    rep = _quiet(needs.upgrade, w, dry_run=True)
    assert rep["學員換號格數"] >= 1, rep["學員換號格數"]
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert cli.main(["upgrade", str(w), "--dry-run"]) == 0
    assert f"會換號的學員句：{rep['學員換號格數']} 格" in buf.getvalue() and "重新生成一次" in buf.getvalue()
    _quiet(needs.upgrade, w)
    md = (w / needs.DIR_NAME / needs.REPORT_MD).read_text(encoding="utf-8")
    assert "會換號的學員句" in md and "做過部分範圍的工作區，切到段落的那幾格下次會重新生成一次" in md
    w2 = _fresh()
    _setup(w2, _log(), _check(_log(), {}))
    assert _quiet(needs.upgrade, w2, dry_run=True)["學員換號格數"] == 0


def test_sync_error_recorded_and_shown_in_inspect():
    """建議：雙寫失敗記進 格式.json 的 同步錯誤，inspect 印出來；healthy() 變 False。"""
    from bookclub import safeview

    w = _fresh()
    log = _log()
    _setup(w, log, _check(log, {}))
    _quiet(needs.upgrade, w)
    assert needs.healthy(w) and "同步錯誤：沒有" in "\n".join(safeview.run([str(w), "修改需求"]))
    real = needs.mirror_final

    def boom(*a, **k):
        raise RuntimeError("小美的退回原因")   # 錯誤訊息不能被記、不能被印

    needs.mirror_final = boom
    try:
        _quiet(fc.decide_record, w, "學員重念@40.00", "通過")
        _quiet(fc.decide_record, w, "學員重念@55.00", "通過")
    finally:
        needs.mirror_final = real
    er = needs.sync_errors(w)
    assert er["次數"] == 2 and er["哪裡"] == "第5步存檔" and er["種類"] == "RuntimeError" and not needs.healthy(w)
    text = "\n".join(safeview.run([str(w), "修改需求"]))
    assert "同步錯誤 2 次" in text and "第5步存檔" in text and "RuntimeError" in text and "小美" not in text
    assert "小美" not in (w / needs.DIR_NAME / needs.FORMAT_FILE).read_text(encoding="utf-8")


def test_ensure_nonblocking_and_cooldown():
    """建議：GET 裡的升級——另一個請求正在做就跳過；別的程式拿著升級鎖也不等；失敗後冷卻期間不重試。"""
    w = _fresh()
    key = str(w)
    needs._busy.add(key)
    try:
        assert _quiet(needs.ensure, w) is None and not needs.exists(w)   # 另一個請求正在做：直接跳過
    finally:
        needs._busy.discard(key)
    with needs._stage_lock(w / needs.STAGING):   # 別的程式（bookclub upgrade）正在升級：不等
        try:
            _quiet(needs.upgrade, w, wait=False)
            raise AssertionError("應該拿不到鎖")
        except needs.Busy:
            pass
        assert _quiet(needs.ensure, w) is None and not needs.exists(w)
    assert key not in needs._failed_at   # 拿不到鎖不算失敗
    calls = []
    real = needs.upgrade

    def boom(*a, **k):
        calls.append(1)
        raise RuntimeError("壞了")

    needs.upgrade = boom
    try:
        _quiet(needs.ensure, w)
        _quiet(needs.ensure, w)
    finally:
        needs.upgrade = real
    assert calls == [1] and key in needs._failed_at   # 第二次在冷卻期間，不重試
    needs._failed_at.pop(key)
    _quiet(needs.ensure, w)
    assert needs.exists(w) and not (w / needs.STAGING).exists()


def test_card_print_ignores_non_sound_fields():
    """建議：只改「已確認」（聲音不變）→ 下一版這張卡不算「這一版做了」、沿用上一版的通過。"""
    from bookclub import turns

    w = _fresh()
    log = _log()
    _setup(w, log, _check(log, {fc.record_key(r): ("通過",) for r in log["紀錄"]}))
    _quiet(needs.upgrade, w)
    k3 = _card(w, "學員段落:T003")["卡號"]
    _quiet(turns.save_turn, w, "T003", {"已確認": True})
    _quiet(needs.sync_cards, w)
    assert any(h["卡號"] == k3 and h["動作"] == "改了第3步決定" for h in needs.read_history(w))   # 歷史照記
    _quiet(proclog.write_log, w, _log(T2))
    v = needs.version_rows(w)
    assert len(v) == 2 and k3 not in v[1]["這一版做了哪幾張"], v[1]["這一版做了哪幾張"]
    st = needs.load(w)
    assert needs.card_state(st, k3, 2) == needs.PASSED and st.states[(k3, 2)]["沿用自"] == 1


def test_catch_up_unchanged_writes_format_only():
    """建議：第 5 步只記看過區段（判斷沒變）→ 下次打開只寫 格式.json，表不重寫。"""
    w = _fresh()
    log = _log()
    prod = _prod(w, "成品_0-3_sw.mp4", T1)
    _setup(w, log, _check(log, {}, 成品影片=prod))
    _quiet(needs.upgrade, w)
    _quiet(needs.ensure, w)
    d = w / needs.DIR_NAME
    tables = {p.name: p.stat().st_mtime_ns for p in d.glob("*.json") if p.name != needs.FORMAT_FILE}
    fm_before = (d / needs.FORMAT_FILE).read_text(encoding="utf-8")
    _quiet(fc.add_watched, w, [[0, 5.0]], prod)
    _quiet(needs.ensure, w)
    assert {p.name: p.stat().st_mtime_ns for p in d.glob("*.json") if p.name != needs.FORMAT_FILE} == tables
    assert (d / needs.FORMAT_FILE).read_text(encoding="utf-8") != fm_before   # 檔案指紋有記


def test_sync_snapshot_taken_before_page():
    """建議：page 算好之後、同步之前第 3 步又被改（另一個請求 POST）→ 記的是算 page 之前的快照，下次會再同步，不漏。"""
    w = _fresh()
    _quiet(needs.upgrade, w)
    k3 = _card(w, "學員段落:T003")["卡號"]
    snap = needs.step3_snapshot(w)
    page = needs.compute_page(w)
    data = _turns(w)                               # 算完 page 之後第 3 步被改了
    for t in data["段落"]:
        if t["id"] == "T003":
            t["end"] = 70.0
    wd.write_json(w / "校對" / "段落.json", data)
    _quiet(needs.sync_cards, w, page, snap=snap)   # 用舊 page：這次還看不到改動
    assert needs.load(w).cards[k3]["原片"][1] == 71.6
    _quiet(needs.sync_cards, w)                    # 下次：快照不一樣 → 再同步
    assert needs.load(w).cards[k3]["原片"][1] == 70.0


def test_render_audio_log_does_not_make_version():
    """建議：render audio（開發者指令）寫的處理紀錄不建版本，打開工作區也不補。"""
    w = _fresh()
    log = _log()
    _setup(w, log, _check(log, {}))
    _quiet(needs.upgrade, w)
    saved = (proclog.records_from_edl, proclog.stamp_contents, proclog.check_files)
    proclog.records_from_edl = lambda edits: []
    proclog.stamp_contents = lambda workdir, recs: recs
    proclog.check_files = lambda a, b, recs: {"未登記的變動": []}
    try:
        _quiet(proclog.write_audio_log, w, [], w / "audio.flac", w / "生成" / "新聲音.wav")
    finally:
        proclog.records_from_edl, proclog.stamp_contents, proclog.check_files = saved
    assert proclog.load(w)["來源"] == "render audio"
    _quiet(needs.ensure, w)
    assert len(needs.version_rows(w)) == 1 and needs.add_version(w, proclog.load(w)) is None


def test_upgrade_never_writes_old_files_intercepted():
    """建議：用會讓 page_data 想存檔的假工作區（名字、重疊、名字決定沒有指紋要重新錨定），攔 open／os.replace／rename／mkdir，
    證明升級（試跑、真的升級、之後打開）只寫 修改需求/（和 修改需求.升級中/），舊檔一個都不寫。"""
    import builtins

    from bookclub import review

    def make() -> Path:
        w = _fresh()
        log = _log()
        _setup(w, log, _check(log, {}, 整片退回=[{"id": "R001", "成品秒": 120.0, "原片秒": 124.0, "覆核項目": [],
                                                  "原因": "怪", "建立時間": T1}]))
        wd.write_json(w / "名字覆核決定.json", {"1": {"做法": "直接消音"}, "2": {"做法": "直接消音"}})   # 沒有候選指紋
        return w

    probe = make()                                  # 先證明這個假工作區平常打開第 3 步會寫檔（不然攔截沒意義）
    before = _sha(probe)
    _quiet(review.page_data, probe)
    assert _sha(probe) != before, "假工作區要能讓 page_data 寫檔"

    w = make()
    before = _sha(w)
    allowed = (str(w / needs.DIR_NAME), str(w / needs.STAGING))
    bad: list[str] = []

    def check(p) -> None:
        s = os.path.abspath(os.fspath(p))
        if s.startswith(str(w)) and not s.startswith(allowed):
            bad.append(s[len(str(w)):])

    real = {"open": builtins.open, "io_open": io.open, "replace": os.replace, "rename": os.rename, "mkdir": os.mkdir}

    def fopen(file, mode="r", *a, **k):
        if isinstance(file, (str, bytes, os.PathLike)) and any(c in mode for c in "wax+"):
            check(file)
        return real["open"](file, mode, *a, **k)

    def fio_open(file, mode="r", *a, **k):
        if isinstance(file, (str, bytes, os.PathLike)) and any(c in mode for c in "wax+"):
            check(file)
        return real["io_open"](file, mode, *a, **k)

    def frep(src, dst, *a, **k):
        check(src)
        check(dst)
        return real["replace"](src, dst, *a, **k)

    def fren(src, dst, *a, **k):
        check(src)
        check(dst)
        return real["rename"](src, dst, *a, **k)

    def fmk(p, *a, **k):
        check(p)
        return real["mkdir"](p, *a, **k)

    builtins.open, io.open, os.replace, os.rename, os.mkdir = fopen, fio_open, frep, fren, fmk
    try:
        _quiet(needs.upgrade, w, dry_run=True)
        _quiet(needs.upgrade, w)
        _quiet(needs.ensure, w)
        _quiet(needs.sync_cards, w, force=True)
        _quiet(needs.mirror_final, w, force=True)
    finally:
        builtins.open, io.open, os.replace, os.rename, os.mkdir = (real["open"], real["io_open"], real["replace"],
                                                                     real["rename"], real["mkdir"])
    assert not bad, bad
    assert _sha(w) == before and needs.exists(w)
    assert any(c["類型"] == "名字" for c in needs.card_rows(w)) and any(c["類型"] == "重疊" for c in needs.card_rows(w))


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
