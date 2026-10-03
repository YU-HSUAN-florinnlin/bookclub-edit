"""10-04 #111：重疊落在會整段重念的學員段落裡、兩邊都沒有老師 → 第 3 步不出卡、自動算處理好，
第 4 步開始前總檢查「請看一眼」列出筆數與每一處的時間。用 tests/fake_workdir.py 的合成資料，不載入模型、不連網。

獨立可跑：.venv/bin/python tests/test_overlap_student_turn.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import fake_workdir  # noqa: E402

_DATA = Path(tempfile.mkdtemp())
(_DATA / "名冊.csv").write_text("中文名,其他寫法,英文代號,聲線,性別\n小美,,Amy,,女\n阿明,,Tom,,男\n", encoding="utf-8")
os.environ["BOOKCLUB_DATA_DIR"] = str(_DATA)

from bookclub import overlap, review  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

_ROOT = None


def _fresh() -> Path:
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


def _ov(a: float, b: float, *roles: str) -> dict:
    return {"start": a, "end": b, "length": round(b - a, 3),
            "speakers": [{"label": chr(65 + i), "role": r, "sim": 0.1} for i, r in enumerate(roles)],
            "已自動跳過": False, "原因": None, "區域": "區域0009"}


# 假工作區：學員1 40–70、老師 70–90、學員2 90–110（見 fake_workdir.TURNS）
AUTO_A = _ov(50.2, 50.4, "不是老師", "不確定")        # 學員1 段落裡、沒有老師 → 自動處理
AUTO_B = _ov(50.9, 51.1, "不是老師", "不確定")        # 同一段裡內容一樣的另一處（#111 的實例：好幾張一樣的卡）
WITH_TEACHER = _ov(95.0, 95.3, "不是老師", "老師")     # 學員2 段落裡，但一邊是老師 → 照舊出卡
IN_TEACHER = _ov(75.0, 75.3, "不是老師", "不確定")     # 老師段落裡 → 照舊出卡
OWN_CHOICE = _ov(52.0, 52.3, "不是老師", "不確定")     # 人選了「生成老師聲音」→ 照舊出卡、決定不動
SAME_CHOICE = _ov(54.0, 54.3, "不是老師", "不確定")    # 人選了「生成學員聲音」並通過（結果一樣）→ 自動處理、決定不動


def _setup(w: Path) -> None:
    data = wd.read_json(wd.overlap_path(w))
    data["overlaps"] += [dict(o) for o in (AUTO_A, AUTO_B, WITH_TEACHER, IN_TEACHER, OWN_CHOICE, SAME_CHOICE)]
    data["overlaps"].sort(key=lambda o: o["start"])
    wd.write_json(wd.overlap_path(w), data)
    review.save_overlap(w, review.overlap_id(OWN_CHOICE), {"做法": "只留老師"})
    review.save_overlap(w, review.overlap_id(SAME_CHOICE), {"做法": "只留學員", "已確認": True})


def _cards(w: Path) -> dict[str, dict]:
    return {x["id"]: x for x in review.page_data(w)["項目"] if x["類型"] == "重疊"}


def test_student_turn_home_pure():
    turns = [{"id": "T1", "start": 0.0, "end": 10.0, "說話者": "學員1", "句子": ["s1", "s2"]},
             {"id": "T2", "start": 10.0, "end": 20.0, "說話者": "老師", "句子": ["s3"]},
             {"id": "T3", "start": 20.0, "end": 30.0, "說話者": "學員2", "句子": [], "校對稿": ""},
             {"id": "T4", "start": 30.0, "end": 40.0, "說話者": "學員3", "句子": [], "校對稿": "有字"}]
    by_id = {"s1": {"id": "s1", "start": 1.0, "end": 4.0, "text": "甲"}, "s2": {"id": "s2", "start": 4.2, "end": 8.0, "text": "乙"},
             "s3": {"id": "s3", "start": 10.0, "end": 20.0, "text": "丙"}}
    o = lambda a, b, *r: {"start": a, "end": b, "speakers": [{"role": x} for x in r]}  # noqa: E731
    assert overlap.student_turn_home(o(5.0, 5.3, "不是老師", "不確定"), turns, by_id, set())["id"] == "T1"
    assert overlap.student_turn_home(o(5.0, 5.3, "不是老師", "老師"), turns, by_id, set()) is None       # 有老師
    assert overlap.student_turn_home(o(5.0, 5.3, "不是老師", "不確定"), turns, by_id, {"學員1"}) is None   # 保留原聲
    assert overlap.student_turn_home(o(9.0, 9.3, "不是老師", "不確定"), turns, by_id, set()) is None      # 段落裡但在句子範圍外
    assert overlap.student_turn_home(o(9.9, 10.3, "不是老師", "不確定"), turns, by_id, set()) is None     # 跨到老師段落
    assert overlap.student_turn_home(o(15.0, 15.3, "不是老師", "不確定"), turns, by_id, set()) is None    # 老師段落
    assert overlap.student_turn_home({"start": 5.0, "end": 5.3, "speakers": []}, turns, by_id, set()) is None   # 人工補的沒有角色
    assert overlap.student_turn_home(o(25.0, 25.3, "不是老師"), turns, by_id, set()) is None              # 沒句子、校對稿空的不會生成
    assert overlap.student_turn_home(o(35.0, 35.3, "不是老師"), turns, by_id, set())["id"] == "T4"       # 手動標的段落照校對稿整段生成


def test_has_own_decision():
    assert not review.overlap_has_own_decision({})
    assert not review.overlap_has_own_decision({"做法": "不用改", "已確認": True})
    assert not review.overlap_has_own_decision({"做法": "只留學員", "已確認": True, "涵蓋於": {"id": "T1"}})
    assert review.overlap_has_own_decision({"做法": "只留老師"})
    assert review.overlap_has_own_decision({"改過的起訖": [1.0, 2.0]})
    assert review.overlap_has_own_decision({"備註": "這裡有老師"})


def test_step3_no_card_and_handled():
    w = _fresh()
    _setup(w)
    ov_file = wd.overlap_path(w).read_bytes()
    dec_before = wd.read_json(review.review_path(w))["重疊"]
    cards = _cards(w)
    auto = {review.overlap_id(o) for o in (AUTO_A, AUTO_B, SAME_CHOICE)}
    kept = {review.overlap_id(o) for o in (WITH_TEACHER, IN_TEACHER, OWN_CHOICE)}
    assert not auto & set(cards), set(cards)                    # 不出卡
    assert kept <= set(cards), set(cards)                        # 有老師、不在學員段落、人自己選了別的做法：照舊出卡
    d = review.page_data(w)
    skipped = {x["id"]: x for x in d["已自動跳過的重疊"]}
    assert auto <= set(skipped)
    assert all(skipped[i]["自動處理"] == "學員段落" and skipped[i]["原因"] == overlap.STUDENT_TURN_REASON for i in auto)
    # 組裝／排生成計畫看到的也一樣（不另外處理）
    assert not auto & {c["id"] for c in review.overlap_choices(w)}
    assert kept <= {c["id"] for c in review.overlap_choices(w)}
    # 沒有第 3 步卡片可以跳
    idx = review.item_index(w)
    assert all(idx[f"重疊:{i}"]["第3步"] is None for i in auto)
    assert all(idx[f"重疊:{i}"]["第3步"] == f"重疊:{i}" for i in kept)
    # 檔案沒被改寫：重疊.json 一個位元組都沒變；已存的決定（只留老師、只留學員＋已確認）照原樣
    assert wd.overlap_path(w).read_bytes() == ov_file
    dec_after = wd.read_json(review.review_path(w))["重疊"]
    for i in (review.overlap_id(OWN_CHOICE), review.overlap_id(SAME_CHOICE)):
        assert {k: v for k, v in dec_after[i].items() if k != "涵蓋於"} == \
            {k: v for k, v in dec_before[i].items() if k != "涵蓋於"}, i


def test_rescue_brings_card_back():
    w = _fresh()
    _setup(w)
    oid = review.overlap_id(AUTO_A)
    review.save_overlap(w, oid, {"救回": True})
    cards = _cards(w)
    assert oid in cards and cards[oid]["救回"]
    assert oid in {c["id"] for c in review.overlap_choices(w)}
    assert review.overlap_id(AUTO_B) not in cards                # 只有救回的那一處回來


def test_kept_voice_student_not_auto():
    w = _fresh()
    _setup(w)
    with review._lock:
        dec = review.load_decisions(w)
        dec["學員聲音"]["學員1"] = "保留原聲"
        review._save_decisions(w, dec)
    assert review.overlap_id(AUTO_A) in _cards(w)                # 學員1 保留原聲：不會整段重念 → 照舊出卡


def test_final_check_lists_auto_handled():
    from bookclub import execute

    w = _fresh()
    _setup(w)
    fc = execute.final_check(w)
    row = next(r for r in fc["請看一眼"] if r["key"] == execute.STUDENT_TURN_KEY)
    pts = row["時間點"]
    assert [p["id"] for p in pts] == [review.overlap_id(o) for o in (AUTO_A, AUTO_B, SAME_CHOICE)]
    assert pts[0]["start"] == AUTO_A["start"] and pts[0]["名稱"].startswith("學員段落 ")
    assert row["start"] == AUTO_A["start"] and row["end"] == SAME_CHOICE["end"]
    assert row["說明"].startswith("3 處重疊") and wd.fmt_time(AUTO_A["start"]) in row["說明"]
    assert "救回" in row["去改"] and not row.get("可以按聽過")           # 不擋第 4 步
    assert len(fc["摘要"]["學員段落裡自動處理"]) == 3
    # 一定要處理裡沒有這幾處
    auto = {review.overlap_id(o) for o in (AUTO_A, AUTO_B, SAME_CHOICE)}
    assert not any(any(i in r["key"] for i in auto) for r in fc["一定要處理"])
    # 救回一處 → 列表少一處
    review.save_overlap(w, review.overlap_id(AUTO_A), {"救回": True})
    row = next(r for r in execute.final_check(w)["請看一眼"] if r["key"] == execute.STUDENT_TURN_KEY)
    assert len(row["時間點"]) == 2


def test_inspect_shows_auto_flag():
    import io
    from contextlib import redirect_stdout

    from bookclub import safeview

    w = _fresh()
    _setup(w)
    before = wd.read_json(review.review_path(w))
    out = {}
    for topic in ("重疊", "總檢查"):
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert safeview.main([str(w), topic]) == 0
        out[topic] = buf.getvalue()
    assert "學員段落裡自動處理 3 處" in out["重疊"], out["重疊"]
    line = next(x for x in out["重疊"].splitlines() if x.startswith(f"id={review.overlap_id(AUTO_A)} "))
    assert "已自動跳過=是" in line and "自動處理=學員段落" in line, line
    line = next(x for x in out["重疊"].splitlines() if x.startswith(f"id={review.overlap_id(WITH_TEACHER)} "))
    assert "已自動跳過=否" in line and "自動處理" not in line, line
    assert "key=重疊:學員段落" in out["總檢查"] and "學員段落裡自動處理的重疊 3 處：" in out["總檢查"], out["總檢查"]
    assert wd.read_json(review.review_path(w)) == before        # inspect 不寫檔


def test_no_row_without_auto_overlaps():
    from bookclub import execute

    w = _fresh()   # 假工作區原本的重疊：一處老師與學員、兩處已自動跳過（兩位學員、邊界誤差）
    fc = execute.final_check(w)
    assert not any(r["key"] == execute.STUDENT_TURN_KEY for r in fc["請看一眼"])
    assert fc["摘要"]["學員段落裡自動處理"] == []
    assert not any(x.get("自動處理") for x in review.page_data(w)["已自動跳過的重疊"])


def _run_all() -> int:
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, func in tests:
        try:
            func()
            print(f"✓ {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback

            traceback.print_exc()
            print(f"✗ {name}：{exc!r}")
    print(f"{len(tests) - failed}/{len(tests)} 通過")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
