"""第 4 步逐類統計（`bookclub/execcounts.py`）：用假工作區算每一類的總數與完成數。

獨立可跑：.venv/bin/python tests/test_execcounts.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from bookclub import execcounts, review  # noqa: E402
from bookclub import workdir as wd  # noqa: E402
import fake_workdir  # noqa: E402


def _rows(w, **kw):
    return {(r["類型"], r["做法"]): r for r in execcounts.counts(w, **kw)}


def test_every_kind_listed_and_nothing_done_yet():
    with tempfile.TemporaryDirectory() as t:
        w = fake_workdir.make(t)
        rows = _rows(w)
        assert len(rows) == 7
        assert all(r["總數"] is not None for r in rows.values()), rows
        assert all(r["完成"] == 0 for r in rows.values())
        assert rows[("學員段落", "匿名聲線重念")]["總數"] > 0


def test_generated_items_count_as_done():
    from bookclub import students

    with tempfile.TemporaryDirectory() as t:
        w = fake_workdir.make(t)
        items, _ = students.build_items(w)
        first = items[0]
        wd.write_json(students.log_path(w), {"句子": [{"id": first["id"], "text": first["text"], "放回時間格": "x.wav"}]})
        r = _rows(w)[("學員段落", "匿名聲線重念")]
        assert r["完成"] == 1 and r["總數"] == len(items)


def test_cache_counts_generated_before_log_and_eta():
    # 09-30：生成紀錄整組跑完才寫；進度改看 _嘗試快取.json（每生成一次就寫），並推算剩餘時間
    from bookclub import students, tts

    with tempfile.TemporaryDirectory() as t:
        w = fake_workdir.make(t)
        items, _ = students.build_items(w)
        assert len(items) >= 2
        a, b = items[0], items[1]
        cache = {f"{a['id']}|1|42|1.0|{a['text']}|ref.wav#x": {"elapsed_s": 100.0},
                 f"{a['id']}|2|1|1.0|{a['text']}|ref.wav#x": {"elapsed_s": 20.0},      # 同一句第二次：還是算一句
                 f"{b['id']}|1|42|1.0|舊的文字|ref.wav#x": {"elapsed_s": 50.0},          # 文字對不上（舊的）不算
                 f"不在範圍|1|42|1.0|x|ref.wav#x": {"elapsed_s": 50.0}}
        (students.out_dir(w)).mkdir(parents=True, exist_ok=True)
        wd.write_json(students.out_dir(w) / tts.ATTEMPT_CACHE, cache)
        r = _rows(w)[("學員段落", "匿名聲線重念")]
        assert r["完成"] == 0 and r["已生成"] == 1
        assert r["預估剩餘秒數"] == (len(items) - 1) * 120
        assert execcounts.remaining_seconds(list(_rows(w).values())) >= r["預估剩餘秒數"]
        assert execcounts.cache_progress({}, items) == (0, None)
        # 09-30 夜間實點發現：一句都還沒生成時不能說「生成都做完了」
        rows = [{"已生成": 0, "總數": 4, "預估剩餘秒數": None}, {"已生成": 0, "總數": 0, "預估剩餘秒數": 0}]
        assert execcounts.remaining_seconds(rows) is None
        assert execcounts.remaining_seconds([{"已生成": 4, "總數": 4, "預估剩餘秒數": 0}]) == 0


def test_render_stage_rows_done_only_after_render():
    with tempfile.TemporaryDirectory() as t:
        w = fake_workdir.make(t)
        dec = review.load_decisions(w)
        dec["刪除段落"].append({"id": "C901", "start": 10.0, "end": 12.0})
        dec["局部消音"].append({"id": "M901", "start": 20.0, "end": 21.0})
        dec["局部消音"].append({"id": "M902", "start": 150.0, "end": 151.0})
        wd.write_json(review.review_path(w), dec)
        r = _rows(w, a=0.0, b=100.0)
        assert r[("刪除段落", "剪掉（聲音＋畫面）")]["總數"] == 1
        assert r[("局部消音", "只消聲音、畫面保留")]["總數"] == 1    # 範圍外的那筆不算
        assert r[("局部消音", "只消聲音、畫面保留")]["完成"] == 0
        r = _rows(w, a=0.0, b=100.0, render_done=True)
        assert r[("局部消音", "只消聲音、畫面保留")]["完成"] == 1


def test_broken_kind_does_not_break_others():
    with tempfile.TemporaryDirectory() as t:
        w = fake_workdir.make(t)
        (w / "覆核" ).mkdir(exist_ok=True)
        review.review_path(w).write_text("{壞掉", encoding="utf-8")
        rows = execcounts.counts(w)
        assert len(rows) == 7


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
