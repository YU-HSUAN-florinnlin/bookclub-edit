"""10-02 第五批：工作區整份複製到別的資料夾（或搬家、換一台電腦）之後，複本上的操作不能讀寫到原本的工作區。

工作區裡有幾個檔存了完整路徑（`分析結果.json`、`transcript/merged.json`、`參考音/挑選紀錄.json`、
`生成/老師紀錄.json`、`生成/老師/_嘗試快取.json`）。這裡建一個假工作區 → 複製到別處（大檔照實際做法換成連結）→
在複本上做會寫檔的操作 → 原本的資料夾一個位元都沒變。不載入模型（生成用假的）。

獨立可跑：.venv/bin/python tests/test_copied_workdir.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

_DATA = Path(tempfile.mkdtemp())
(_DATA / "名冊.csv").write_text("中文名,其他寫法,英文代號,聲線,性別\n小美,,Amy,,女\n阿明,,Tom,,男\n", encoding="utf-8")
os.environ["BOOKCLUB_DATA_DIR"] = str(_DATA)

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

import fake_workdir  # noqa: E402
from bookclub import execute, finalcheck, refpick, review, tts  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

SR = 24000
SENTS = [{"id": "A", "text": "甲乙丙丁", "slot_s": 6.0}, {"id": "B", "text": "戊己庚辛", "slot_s": 6.0}]


def _tree(root: Path) -> dict[str, str]:
    """資料夾裡每一個檔（含連結本身）→ 內容指紋。連結記「指到哪裡」，不跟進去。"""
    out = {}
    for dp, dns, fns in os.walk(root):
        for n in fns + [d for d in dns if (Path(dp) / d).is_symlink()]:
            p = Path(dp) / n
            rel = str(p.relative_to(root))
            if p.is_symlink():
                out[rel] = "連結→" + os.readlink(p)
            else:
                out[rel] = hashlib.sha1(p.read_bytes()).hexdigest() + f"|{p.stat().st_mtime_ns}"
    return out


def _hear(p) -> str:
    return "甲乙丙丁" if Path(p).name.startswith("A_") else "戊己庚辛"


def _synth(level: float):
    return lambda text, seed, speed: (np.full(int(4.0 * SR), level, dtype=np.float32), SR)


def _never(*a, **k):
    raise AssertionError("不該重新生成")


def _legacy(work: Path) -> None:
    """把老師紀錄、生成快取改成 10-01 以前的寫法（紀錄沒有參考音指紋、快取鍵是「完整路徑#指紋」），模擬正式工作區現在的樣子。"""
    od = tts.teacher_out_dir(work)
    ref = wd.ref_dir(work) / "ref.wav"
    fp = tts.ref_fingerprint(ref)
    cache = json.loads((od / tts.ATTEMPT_CACHE).read_text(encoding="utf-8"))
    (od / tts.ATTEMPT_CACHE).write_text(json.dumps({k.replace(f"參考音#{fp}", f"{ref}#{fp}"): v for k, v in cache.items()},
                                                   ensure_ascii=False), encoding="utf-8")
    log = json.loads(tts.teacher_log_path(work).read_text(encoding="utf-8"))
    log.pop("參考音指紋", None)
    tts.teacher_log_path(work).write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")


def _make_original(root: Path) -> Path:
    """假工作區（tests/fake_workdir.py）＋參考音候選＋老師兩句生成好的（舊寫法的紀錄與快取）＋一筆局部消音、總檢查按過看過。"""
    w = fake_workdir.make(root)
    rd = wd.ref_dir(w)
    shutil.copyfile(rd / "ref.wav", rd / "候選1.wav")
    sf.write(str(rd / "候選2.wav"), np.full(SR, 0.02, dtype=np.float32), SR)
    (rd / "挑選紀錄.json").write_text(json.dumps({"video": str(root / "假影片.mp4"), "workdir": str(w), "選定名次": 1},
                                             ensure_ascii=False), encoding="utf-8")
    (w / "句子.json").write_text(json.dumps(SENTS, ensure_ascii=False), encoding="utf-8")
    tts.generate_teacher(w, w / "句子.json", synth=_synth(0.01), hear=_hear, check_similarity=False, use_pauses=False,
                         log=lambda s: None)
    _legacy(w)
    review.manual_edit(w, {"類型": "局部消音", "start": 30.0, "end": 31.0})
    execute.ack_final(w, seen=True)
    return w


def test_localize_and_legacy_keys():
    old = Path("/舊的位置/2025-04-09 第一堂_剪輯工作區")
    new = Path("/新的位置/2025-04-09 第一堂_剪輯工作區")
    # 在別的工作區裡的 → 換成這個工作區裡同一個位置；原片、設定資料夾的聲線照用；相對路徑接在這個工作區底下
    assert wd.localize(old / "參考音" / "ref.wav", new, [old]) == new / "參考音" / "ref.wav"
    assert wd.localize(old / "參考音" / "ref.wav", new, []) == new / "參考音" / "ref.wav"       # 沒有記舊位置也認得出來
    assert wd.localize("/舊的位置/工作區/生成/老師/A.wav", new, []) == new / "生成" / "老師" / "A.wav"
    assert wd.localize("/舊的位置/2025-04-09 第一堂.mp4", new, [old]) == Path("/舊的位置/2025-04-09 第一堂.mp4")
    assert wd.localize("/Users/x/讀書會剪輯資料/聲線/女4.wav", new, [old]) == Path("/Users/x/讀書會剪輯資料/聲線/女4.wav")
    assert wd.localize(new / "參考音" / "ref.wav", new, [old]) == new / "參考音" / "ref.wav"
    assert wd.localize("生成/老師/A.wav", new) == new / "生成" / "老師" / "A.wav"
    assert wd.localize(None, new) is None
    # 沒有指紋的舊紀錄：工作區裡的位置一樣就算同一個檔
    assert wd.same_stored_file(old / "參考音" / "ref.wav", new / "參考音" / "ref.wav")
    assert not wd.same_stored_file(old / "參考音" / "ref.wav", new / "參考音" / "候選1.wav")
    # 生成快取的舊鍵（完整路徑#指紋）→ 新寫法（參考音#指紋），路徑在哪裡都一樣
    k = f"S016|1|42|1.0|甲乙|{old}/參考音/ref.wav#cb5c0059d453"
    assert tts.normalize_cache_key(k) == "S016|1|42|1.0|甲乙|參考音#cb5c0059d453"
    pk = f"{k}|原文|[1.0, 2.0]"
    assert tts.normalize_cache_key(pk) == "S016|1|42|1.0|甲乙|參考音#cb5c0059d453|原文|[1.0, 2.0]"
    assert tts.normalize_cache_key("S016|1|42|1.0|甲乙|參考音#cb5c0059d453") == "S016|1|42|1.0|甲乙|參考音#cb5c0059d453"


def test_copied_legacy_workdir_reuses_and_never_reads_original():
    """正式工作區現在的樣子（紀錄沒有指紋、快取鍵帶原本的完整路徑）複製到別處：老師的句子照樣沿用（不重新生成）；
    把原本的工作區搬走（換一台電腦的情況）也一樣。"""
    base = Path(tempfile.mkdtemp())
    w = _make_original(base / "原本")
    copy = base / "複本" / w.name
    shutil.copytree(w, copy)
    for kw in ({"phase": "生成"}, {}):
        r = tts.generate_teacher(copy, copy / "句子.json", synth=_never, hear=_hear, check_similarity=False,
                                 use_pauses=False, log=lambda s: None, **kw)
    assert [x["id"] for x in r["句子"]] == ["A", "B"]
    assert json.loads(tts.teacher_log_path(copy).read_text(encoding="utf-8"))["參考音"] == str(copy / "參考音" / "ref.wav")
    moved = base / "別台電腦" / w.name
    shutil.move(str(w), str(moved.parent.mkdir(parents=True) or moved))
    r = tts.generate_teacher(moved, moved / "句子.json", synth=_never, hear=_hear, check_similarity=False,
                             use_pauses=False, log=lambda s: None)
    assert len(r["句子"]) == 2
    # 紀錄記的完整路徑指到原本的工作區（已經不在了）：第 4 步「做過沒有」照樣判斷得出來，不會出錯
    assert execute.names_done(moved)[0] in (True, False)


def test_copy_then_write_leaves_original_untouched():
    """建工作區 → 複製到別的資料夾（大檔照實際做法換成指回原本的連結）→ 在複本上做會寫檔的操作 → 原本的資料夾一個位元都沒變。"""
    base = Path(tempfile.mkdtemp())
    w = _make_original(base / "原本")
    copy = base / "複本" / w.name
    shutil.copytree(w, copy)
    # 實際複製時大的影音檔用連結：音軌、參考音、生成的聲音檔都換成指回原本工作區的連結
    links = [Path("audio.flac"), Path("參考音") / "ref.wav", Path("參考音") / "ref.txt", Path("參考音") / "候選2.wav",
             *[p.relative_to(w) for p in tts.teacher_out_dir(w).glob("*.wav")]]
    for rel in links:
        (copy / rel).unlink()
        (copy / rel).symlink_to(w / rel)
    before = _tree(w)

    # 1. 第 2 步換參考音（換音檔、存逐字稿）
    refpick.finalize_reference(copy, 2, "新的參考音逐字稿", replace_audio=True)
    assert not (copy / "參考音" / "ref.wav").is_symlink() and not (copy / "參考音" / "ref.txt").is_symlink()
    # 2. 第 4 步重新生成老師的句子（參考音換了，全部重新生成；聲音檔原本是連結）
    tts.generate_teacher(copy, copy / "句子.json", synth=_synth(0.05), hear=_hear, check_similarity=False,
                         use_pauses=False, log=lambda s: None)
    log = json.loads(tts.teacher_log_path(copy).read_text(encoding="utf-8"))
    assert log["參考音"] == str(copy / "參考音" / "ref.wav") and log["參考音指紋"]
    assert not any(p.is_symlink() for p in tts.teacher_out_dir(copy).glob("A*.wav"))
    # 3. 第 3、4 步：新增消音、總檢查按「我看過了」、清掉一句等著重做
    review.manual_edit(copy, {"類型": "局部消音", "start": 40.0, "end": 41.0})
    execute.ack_final(copy, seen=True)
    finalcheck.clear_generated(copy, "老師", ["A"], "2026-10-02T17:00:00")
    # 4. 讀原片：複本記的位置照用（原片在工作區外面）；原片放在工作區裡的少見情況，用複本裡的那一份
    assert review.video_path(copy) == base / "原本" / "假影片.mp4"
    inside = w / "原片.mp4"
    inside.write_bytes(b"fake")
    before[str(inside.relative_to(w))] = _tree(w)[str(inside.relative_to(w))]
    an = json.loads((copy / "分析結果.json").read_text(encoding="utf-8"))
    (copy / "分析結果.json").write_text(json.dumps({**an, "video": str(inside)}, ensure_ascii=False), encoding="utf-8")
    merged = json.loads((copy / "transcript" / "merged.json").read_text(encoding="utf-8"))
    (copy / "transcript" / "merged.json").write_text(json.dumps({**merged, "source": str(inside)}, ensure_ascii=False),
                                                     encoding="utf-8")
    assert review.video_path(copy) is None                       # 複本裡沒有那一份：不去讀原本工作區裡的
    assert str(copy / "原片.mp4") in wd.video_missing_message(copy)
    (copy / "原片.mp4").write_bytes(b"fake")
    assert review.video_path(copy) == copy / "原片.mp4"

    assert _tree(w) == before                                    # 原本的資料夾一個位元都沒變


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"✓ {t.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")


if __name__ == "__main__":
    _run_all()
