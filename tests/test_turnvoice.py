"""10-04 #127：學員段落的聲音多半是老師 → 第 3 步學員段落卡片提醒、第 4 步總檢查「請看一眼」每一段一列。
用 tests/fake_workdir.py 的合成資料，不載入模型、不連網。

獨立可跑：.venv/bin/python tests/test_turnvoice.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import io
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import fake_workdir  # noqa: E402

_DATA = Path(tempfile.mkdtemp())
(_DATA / "名冊.csv").write_text("中文名,其他寫法,英文代號,聲線,性別\n小美,,Amy,,女\n阿明,,Tom,,男\n", encoding="utf-8")
os.environ["BOOKCLUB_DATA_DIR"] = str(_DATA)

from bookclub import execute, review, turnvoice  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

_ROOT = None


def _fresh(teacher_in_t005: bool = True) -> Path:
    """假工作區；teacher_in_t005：學員2 的段落 T005（92–111.6 秒，5 句）裡 4 句聲紋改成老師。"""
    global _ROOT
    if _ROOT is None:
        _ROOT = Path(tempfile.mkdtemp()) / "base"
        fake_workdir.make(_ROOT)
    d = Path(tempfile.mkdtemp())
    shutil.copytree(_ROOT, d / "base")
    w = d / "base" / "工作區"
    data = wd.read_json(w / "分析結果.json")
    data["video"] = str(d / "base" / "假影片.mp4")
    wd.write_json(w / "分析結果.json", data)
    if teacher_in_t005:
        sp = wd.read_json(wd.speakers_path(w))
        turn = next(t for t in wd.read_json(w / "校對" / "段落.json")["段落"] if t["id"] == "T005")
        for s in sp["sentences"]:
            if s["id"] in turn["句子"][:4]:
                s["label"] = "老師"
        wd.write_json(wd.speakers_path(w), sp)
    return w


def _s(i: int, a: float, b: float, label: str, voice: str | None = None) -> dict:
    s = {"id": f"s{i}", "start": a, "end": b, "label": label}
    if voice:
        s["聲紋判斷"] = voice
    return s


def test_ratio_and_thresholds():
    # 10-04 實例的樣子：28 句裡 27 句老師
    sents = [_s(i, i * 3.0, i * 3.0 + 2.5, "老師" if i else "不是老師") for i in range(28)]
    v = turnvoice.turn_voice({"start": 0.0, "end": 90.0, "句子": [s["id"] for s in sents]}, sents)
    assert v["多半是老師"] and v["句數"] == 28 and v["老師句數"] == 27 and abs(v["比例"] - 27 / 28) < 1e-3, v
    # 剛好一半（老師 5 秒、不是老師 3 秒＋不確定 2 秒）→ 算；「太短」不算進分母
    sents = [_s(0, 0, 5, "老師"), _s(1, 5, 8, "不是老師"), _s(2, 8, 10, "不確定"), _s(3, 10, 30, "太短")]
    v = turnvoice.turn_voice({"start": 0, "end": 30}, sents)
    assert v["多半是老師"] and v["比例"] == 0.5 and v["句數"] == 3, v
    # 比例不到一半 → 不算
    sents[1] = _s(1, 5, 8.2, "不是老師")
    assert not turnvoice.turn_voice({"start": 0, "end": 30}, sents)["多半是老師"]
    # 全部是老師、但只有 4 秒 → 不算（太短的段落一兩句誤判不算）
    assert not turnvoice.turn_voice({"start": 0, "end": 4}, [_s(0, 0, 4, "老師")])["多半是老師"]
    # 有「聲紋判斷」就用它（冥想引導被文字改成老師的不算）；秒數夾在段落裡
    sents = [_s(0, 0, 10, "老師", voice="不是老師"), _s(1, 10, 30, "老師")]
    v = turnvoice.turn_voice({"start": 0, "end": 20}, sents)
    assert v["老師秒"] == 10.0 and v["計入秒"] == 20.0 and v["多半是老師"], v
    assert turnvoice.turn_voice({"start": 0, "end": 5}, [_s(0, 0, 5, "太短")]) is None
    assert "28 句裡 27 句" in turnvoice.warn_text({"句數": 28, "老師句數": 27, "老師秒": 75.1})


def test_card_has_warning():
    w = _fresh()
    cards = {x["id"]: x for x in review.page_data(w)["項目"] if x["類型"] == "學員段落"}
    v = cards["T005"]["聲紋多半是老師"]
    assert v["句數"] == 5 and v["老師句數"] == 4 and v["提醒"].startswith("這一段的聲音特徵多半是老師（5 句裡 4 句"), v
    assert "說話者改成老師" in v["提醒"]
    assert cards["T005"]["建議"]["做法"] == "通過"                     # 不擋通過
    assert "聲紋多半是老師" not in cards["T003"]


def test_final_check_row():
    w = _fresh()
    fc = execute.final_check(w)
    key = f"{execute.TEACHER_TURN_KEY}:T005"
    rows = [r for r in fc["請看一眼"] if str(r["key"]).startswith(execute.TEACHER_TURN_KEY)]
    assert [r["key"] for r in rows] == [key], rows
    r = rows[0]
    assert r["第3步"], r                                                # 可以點回第 3 步那張卡
    assert "5 句裡 4 句" in r["說明"] and "已經確認" not in r["說明"] and not r.get("可以按聽過")
    assert r["聲紋老師"]["多半是老師"]
    assert not any(str(x["key"]).startswith(execute.TEACHER_TURN_KEY) for x in fc["一定要處理"])
    # 已確認的段落也照列，說明寫明已確認
    data = wd.read_json(w / "校對" / "段落.json")
    next(t for t in data["段落"] if t["id"] == "T005")["已確認"] = True
    wd.write_json(w / "校對" / "段落.json", data)
    r = next(x for x in execute.final_check(w)["請看一眼"] if x["key"] == key)
    assert "已經確認過" in r["說明"], r["說明"]


def test_no_row_normally():
    w = _fresh(teacher_in_t005=False)
    fc = execute.final_check(w)
    assert not any(str(r["key"]).startswith(execute.TEACHER_TURN_KEY) for r in fc["請看一眼"])


def test_inspect_turns_prints_numbers_only():
    from bookclub import safeview

    w = _fresh()
    out = {}
    for args in (["段落", "--id", "T005", "--id", "T003"], ["總檢查"]):
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert safeview.main([str(w), *args]) == 0
        out[args[0]] = buf.getvalue()
    t5 = next(x for x in out["段落"].splitlines() if x.startswith("id=T005 "))
    assert "聲紋多半是老師=是" in t5 and "聲紋老師比例=0.800" in t5 and "聲紋老師句數=4/5" in t5, t5
    t3 = next(x for x in out["段落"].splitlines() if x.startswith("id=T003 "))
    assert "聲紋多半是老師=否" in t3 and "聲紋老師比例=0.000" in t3, t3
    assert "key=聲紋:學員段落是老師:T005" in out["總檢查"] and "聲紋老師.多半是老師=是" in out["總檢查"], out["總檢查"]
    allout = "".join(out.values())
    for bad in ("分享的第", "老師說的", "小美", "阿明", "聲音特徵", "這段其實是老師"):
        assert bad not in allout, bad


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
