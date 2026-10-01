"""第 4 步逐類統計（`bookclub/execcounts.py`）：用假工作區算每一類的總數與完成數。

獨立可跑：.venv/bin/python tests/test_execcounts.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 10-01：這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

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
        assert rows[("學員段落", "學員重念（用替代聲音）")]["總數"] > 0


def test_generated_items_count_as_done():
    from bookclub import students

    with tempfile.TemporaryDirectory() as t:
        w = fake_workdir.make(t)
        items, _ = students.build_items(w)
        first = items[0]
        wd.write_json(students.log_path(w), {"句子": [{"id": first["id"], "text": first["text"], "放回時間格": "x.wav"}]})
        r = _rows(w)[("學員段落", "學員重念（用替代聲音）")]
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
        r = _rows(w)[("學員段落", "學員重念（用替代聲音）")]
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
        assert r[("剪掉", "聲音和畫面都拿掉，影片會變短")]["總數"] == 1
        assert r[("消音", "只拿掉聲音，畫面留著")]["總數"] == 1    # 範圍外的那筆不算
        assert r[("消音", "只拿掉聲音，畫面留著")]["完成"] == 0
        r = _rows(w, a=0.0, b=100.0, render_done=True)
        assert r[("消音", "只拿掉聲音，畫面留著")]["完成"] == 1


def test_broken_kind_does_not_break_others():
    with tempfile.TemporaryDirectory() as t:
        w = fake_workdir.make(t)
        (w / "覆核" ).mkdir(exist_ok=True)
        review.review_path(w).write_text("{壞掉", encoding="utf-8")
        rows = execcounts.counts(w)
        assert len(rows) == 7


def test_counts_follow_same_staleness_as_step4():
    """10-01 走查：逐類統計跟第 4 步「做過沒有」同一個判斷——參考音換過、時間格改了都不算做完。"""
    items = [{"id": "A", "text": "甲", "生成用文字": "甲", "slot": [1.0, 2.0]},
             {"id": "B", "text": "乙", "生成用文字": "乙", "slot": [3.0, 4.0]}]
    log = {"句子": [{"id": "A", "text": "甲", "slot": [1.0, 2.0], "放回時間格": {"檔案": "a.wav"}},
                    {"id": "B", "text": "乙", "slot": [3.0, 4.6], "放回時間格": {"檔案": "b.wav"}}]}
    assert execcounts._gen_done(items, log) == 1                  # B 的時間格改了
    assert execcounts._gen_done(items, log, all_stale=True) == 0  # 老師參考音整份換過
    cache = {"A|1|42|1.0|甲|參考音#新的": {"elapsed_s": 10.0}, "B|1|42|1.0|乙|/舊路徑/ref.wav#舊的": {"elapsed_s": 10.0}}
    assert execcounts.cache_progress(cache, items)[0] == 2
    assert execcounts.cache_progress(cache, items, "#新的")[0] == 1
    assert execcounts.cache_progress(cache, items, {"A": "#舊的", "B": "#舊的"})[0] == 1


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
