"""10-04 #117：有人聲但逐字稿沒有字（Groq 漏轉）→ 第 4 步開始前總檢查要人聽過。
區間計算是純函式；總檢查用 tests/fake_workdir.py 的合成資料（加上 VAD 安靜處、拿掉幾句的字），不載入模型、不連網。

獨立可跑：.venv/bin/python tests/test_untranscribed.py
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

from bookclub import execute, review, untranscribed  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

_ROOT = None


def _fresh(with_silence: bool = True) -> Path:
    """假工作區（3 分鐘，每句 4k–4k+3.6 秒）。with_silence：補上 VAD 安靜處（句子之間的 0.4 秒），並拿掉幾句的字：
    - 第 33、34 句（132–139.6，老師導讀）整句沒有字 → 一定要處理
    - 第 24、25 句（96–103.6，學員2 段落、會整段重念）沒有字 → 請看一眼（學員段落）
    - 第 6、7 句（24–31.6，老師講解）沒有字、但整段會剪掉 → 不列
    - 第 21 句（84–87.6，老師）後半句沒有字（約 1.8 秒）→ 請看一眼（較短）"""
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
    if with_silence:
        m = wd.read_json(wd.merged_transcript_path(w))
        sents = m["sentences"]
        m["silence_map"] = [{"start": a["end"], "end": b["start"]} for a, b in zip(sents, sents[1:])] + \
                           [{"start": sents[-1]["end"], "end": m["duration"]}]

        def drop(x: dict) -> bool:
            s = x["start"]
            return 132 <= s < 140 or 96 <= s < 104 or 24 <= s < 32 or 85.8 <= s < 87.6

        m["words"] = [x for x in m["words"] if not drop(x)]
        wd.write_json(wd.merged_transcript_path(w), m)
        dec = review.load_decisions(w)
        dec["刪除段落"].append({"id": "D901", "start": 23.8, "end": 31.8, "狀態": "刪除"})
        review._save_decisions(w, dec)
    return w


# ---------- 純函式 ----------

def test_long_word_counts_first_second_only():
    # 0–15 秒都是人聲；第二個字被 Groq 拉長到 14.5 秒，只算開頭 1 秒有蓋到
    words = [{"start": 0.0, "end": 0.5}, {"start": 0.5, "end": 15.0}]
    r = untranscribed.find_regions(15.0, [], words)
    assert len(r) == 1 and abs(r[0]["start"] - 1.5) < 1e-6 and abs(r[0]["end"] - 15.0) < 1e-6, r
    assert abs(r[0]["人聲秒"] - 13.5) < 1e-6 and r[0]["小段數"] == 1


def test_adjacent_pieces_merge_only_without_words_between():
    sil = [{"start": 5.0, "end": 6.0}, {"start": 19.8, "end": 20.0}]
    words = [{"start": 0.0, "end": 0.3}, {"start": 19.5, "end": 19.8}]
    r = untranscribed.find_regions(20.0, sil, words)
    assert len(r) == 1 and r[0]["小段數"] == 2, r                    # 中間只隔著安靜 → 併成一處
    assert abs(r[0]["start"] - 0.3) < 1e-6 and abs(r[0]["end"] - 19.5) < 1e-6
    assert abs(r[0]["人聲秒"] - (4.7 + 13.5)) < 1e-6                  # 安靜的那 1 秒不算人聲
    r = untranscribed.find_regions(20.0, sil, words + [{"start": 5.5, "end": 5.6}])
    assert len(r) == 2, r                                              # 中間有一個字 → 分開兩處


def test_many_short_pieces_add_up():
    # 10-04 審查 F：20 秒裡 10 小段人聲各 1.2 秒、一個字都沒有 → 加起來 12 秒，算一處
    sil = [{"start": k * 2.0 + 1.2, "end": k * 2.0 + 2.0} for k in range(10)]
    r = untranscribed.find_regions(20.0, sil, [])
    assert len(r) == 1 and r[0]["小段數"] == 10 and abs(r[0]["人聲秒"] - 12.0) < 1e-6, r
    assert r[0]["start"] == 0.0 and abs(r[0]["end"] - 19.2) < 1e-6
    # 單獨一段 1.4 秒不列；兩段 0.8 秒（中間沒有字）加起來 1.6 秒算一處
    assert untranscribed.find_regions(3.0, [{"start": 1.4, "end": 3.0}], []) == []
    r = untranscribed.find_regions(3.0, [{"start": 0.8, "end": 2.2}], [])
    assert len(r) == 1 and r[0]["小段數"] == 2 and abs(r[0]["人聲秒"] - 1.6) < 1e-6, r


def test_short_gaps_and_missing_data():
    words = [{"start": float(k), "end": k + 0.3} for k in range(10)]   # 每秒一個字，空隙 0.7 秒
    assert untranscribed.find_regions(10.0, [], words) == []
    assert untranscribed.find_regions(10.0, None, words) is None        # 沒有安靜處清單 → 不列
    assert untranscribed.find_regions(None, [], words) is None
    assert untranscribed.from_merged({"words": words, "duration": 10.0}) is None
    assert untranscribed.stats(None) is None
    assert "沒有安靜處資料" in untranscribed.summary_line(None, str)
    line = untranscribed.summary_line(untranscribed.find_regions(15.0, [], [{"start": 0.0, "end": 15.0}]),
                                      lambda t: f"{t:.1f}")
    assert line.startswith("[1/轉文字] 有人聲但沒有字：1 處、人聲合計 14.0 秒，其中 3 秒以上 1 處：1.0–15.0"), line


def test_classify_rules():
    reg = {"start": 10.0, "end": 20.0, "人聲區間": [[10.0, 14.0], [16.0, 20.0]]}
    assert untranscribed.classify(reg, [], [])[0] == "一定要處理"
    assert untranscribed.classify(reg, [(9.0, 21.0)], []) == (None, 0.0)              # 整個會剪掉
    assert untranscribed.classify(reg, [(9.0, 17.5)], [])[0] == "較短"                  # 剩 2.5 秒
    assert untranscribed.classify(reg, [], [(5.0, 25.0)]) == ("學員段落", 8.0)           # 整個在學員段落裡
    assert untranscribed.classify(reg, [], [(5.0, 18.0)])[0] == "較短"                  # 段落外面剩 2 秒
    # 10-04 審查 G：段落外面只有 1 秒（不到 1.5 秒）也不能算「學員段落」（那一截不會被重念蓋掉）
    assert untranscribed.classify(reg, [], [(5.0, 19.0)]) == ("較短", 8.0)
    assert untranscribed.classify(reg, [(19.0, 21.0)], [(5.0, 19.0)])[0] == "學員段落"  # 段落外那一截會剪掉 → 照算段落裡


def test_key_tolerance():
    heard = {"沒有字:100.0", "名字:3", "沒有字:彙總"}
    assert execute.untranscribed_key(100.8, heard) == "沒有字:100.0"   # 重算後移動不到 1 秒：沿用
    assert execute.untranscribed_key(101.2, heard) == "沒有字:101.2"   # 移動超過 1 秒：新的一列
    assert execute.untranscribed_key(50.04, set()) == "沒有字:50.0"


# ---------- 總檢查 ----------

def _rows(fc: dict, part: str) -> list[dict]:
    return [r for r in fc[part] if str(r["key"]).startswith("沒有字")]


def test_final_check_rows():
    w = _fresh()
    fc = execute.final_check(w)
    must = _rows(fc, "一定要處理")
    assert len(must) == 1, must
    r = must[0]
    # 頭尾會帶到前後句子裡字蓋不到的零點幾秒人聲（那段時間一樣一個字都沒有）
    assert 131.4 < r["start"] <= 132.0 and 139.5 < r["end"] <= 140.2, r
    assert r["key"] == f"沒有字:{r['start']:.1f}" and r["可以按聽過"] and not r["已按聽過"]
    assert "約 7.3 秒" in r["說明"] and "名字卡" in r["說明"] and "我聽過了" in r["說明"]
    look = _rows(fc, "請看一眼")
    assert [x["key"] for x in look] == [execute.UNTRANSCRIBED_LOOK_KEY], look
    pts = look[0]["時間點"]
    kinds = {p["類型"] for p in pts}
    assert kinds == {"較短", "學員段落"}, pts
    stu = next(p for p in pts if p["類型"] == "學員段落")
    assert abs(stu["start"] - 96.0) < 0.6 and stu["名稱"].startswith("學員段落 "), stu
    short = next(p for p in pts if p["類型"] == "較短")
    assert 85.0 < short["start"] < 86.0 and 1.5 <= short["秒"] < 3.0, short
    assert not any(20 <= p["start"] <= 32 for p in pts)                 # 會剪掉的不列
    assert "重念時會少念這幾秒" in look[0]["說明"] and "隱私不受影響" in look[0]["說明"]
    assert not look[0].get("可以按聽過")


def test_heard_persists_and_moves():
    w = _fresh()
    key = _rows(execute.final_check(w), "一定要處理")[0]["key"]
    execute.ack_final(w, key)
    r = _rows(execute.final_check(w), "一定要處理")[0]
    assert r["key"] == key and r["已按聽過"]
    # 之前按的鍵跟現在差 0.6 秒（重轉之後小幅移動）→ 照算
    dec = review.load_decisions(w)
    start = r["start"]
    dec["總檢查"]["聽過"] = [f"沒有字:{start + 0.6:.1f}"]
    review._save_decisions(w, dec)
    r = _rows(execute.final_check(w), "一定要處理")[0]
    assert r["已按聽過"] and r["key"] == f"沒有字:{start + 0.6:.1f}"
    execute.ack_final(w, r["key"], heard=False)                       # 取消也是同一個鍵
    assert not _rows(execute.final_check(w), "一定要處理")[0]["已按聽過"]
    # 差 1.5 秒 → 算新的一列
    dec = review.load_decisions(w)
    dec["總檢查"]["聽過"] = [f"沒有字:{start + 1.5:.1f}"]
    review._save_decisions(w, dec)
    assert not _rows(execute.final_check(w), "一定要處理")[0]["已按聽過"]


def test_kept_voice_turn_is_not_revoiced():
    w = _fresh()
    dec = review.load_decisions(w)
    dec["學員聲音"]["學員2"] = "保留原聲"                                 # 學員2 不重念 → 那裡的沒有字照樣要聽
    review._save_decisions(w, dec)
    must = _rows(execute.final_check(w), "一定要處理")
    assert any(abs(r["start"] - 96.0) < 0.6 for r in must), must


def test_no_rows_without_silence_map():
    w = _fresh(with_silence=False)
    fc = execute.final_check(w)
    assert not _rows(fc, "一定要處理") and not _rows(fc, "請看一眼")


def test_inspect_words_and_final_check_no_text():
    from bookclub import safeview

    w = _fresh()
    before = wd.read_json(review.review_path(w))
    out = {}
    for args in (["字", "--limit", "5"], ["字", "--from", "1:30", "--to", "1:45", "--毫秒"], ["總檢查"]):
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert safeview.main([str(w), *args]) == 0
        out[" ".join(args)] = buf.getvalue()
    allout = "".join(out.values())
    for bad in ("老師說的", "分享的第", "小美", "阿明", "聽一下", "名字卡"):   # 逐字稿、名字、說明文字都不印
        assert bad not in allout, bad
    words = out["字 --limit 5"]
    assert "有人聲但沒有字（整支）：4 處" in words, words   # 整支統計（含會剪掉的那一處）
    assert "沒有字 0:02:11.5" in words and "小段數=4" in words, words
    assert "⋯另外" in words                                             # 安靜處只列 5 處、其他提示用 --limit
    ms = out["字 --from 1:30 --to 1:45 --毫秒"]
    assert "範圍內 1 處" in ms and "沒有字 0:01:35.474–" in ms, ms             # --毫秒 有作用
    assert "#1  0:01:29.800–0:01:30.182" in ms, ms
    fc = out["總檢查"]
    assert "key=沒有字:131.5" in fc and "key=沒有字:彙總" in fc and "時間點 id=沒有字:" in fc and "類型=學員段落" in fc, fc
    assert wd.read_json(review.review_path(w)) == before                # inspect 不寫檔


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
