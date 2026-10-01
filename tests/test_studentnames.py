"""bookclub/studentnames.py（保留原聲的學員自己講到名字）的測試：用 tests/fake_workdir.py 的合成資料
（假名字「小美」→ 代號 Amy，學員2 在 92 秒說到），不碰真的影片、不呼叫 Claude／Groq。

獨立可跑：.venv/bin/python tests/test_studentnames.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 10-01：這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

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

from bookclub import review, studentnames  # noqa: E402

_ROOT = None


def _fresh() -> Path:
    global _ROOT
    if _ROOT is None:
        _ROOT = Path(tempfile.mkdtemp()) / "base"
        fake_workdir.make(_ROOT)
    d = Path(tempfile.mkdtemp())
    shutil.copytree(_ROOT, d / "base")
    return d / "base" / "工作區"


def _stu_names(w: Path) -> list[dict]:
    return [x for x in review.page_data(w)["項目"] if x["類型"] == "學員名字"]


def test_no_kept_student_no_items():
    w = _fresh()
    assert _stu_names(w) == []


def test_kept_student_name_found_default_mute_and_gone_after_switch():
    w = _fresh()
    review.set_voice(w, "學員2", "保留原聲")
    items = _stu_names(w)
    assert len(items) == 1, items
    it = items[0]
    assert it["學員"] == "學員2" and it["代號"] == "Amy" and it["做法"] == studentnames.MUTE
    assert 90 <= it["start"] < it["end"] <= 95
    assert it["整句"]["換成代號"] and "Amy" in it["整句"]["換成代號"] and "小美" not in it["整句"]["換成代號"]
    # 老師的名字候選不受影響（還是原本那份）
    assert all(x["類型"] != "名字" or x["id"].isdigit() or x["id"].startswith("N") for x in review.page_data(w)["項目"])
    p = studentnames.plan(w)
    assert len(p["消音"]) == 1 and not p["生成"]
    # 改成重新生成：不再列
    review.set_voice(w, "學員2", "重新生成")
    assert _stu_names(w) == []
    assert studentnames.plan(w) == {"消音": [], "生成": [], "要人處理": []}


def test_choose_code_makes_generation_item_and_skip_tag():
    w = _fresh()
    review.set_voice(w, "學員2", "保留原聲")
    it = _stu_names(w)[0]
    studentnames.save(w, it["id"], {"做法": studentnames.CODE})
    p = studentnames.plan(w)
    assert not p["消音"] and len(p["生成"]) == 1
    g = p["生成"][0]
    assert g["學員"] == "學員2" and "Amy" in g["text"] and g["slot"][0] <= it["start"] and it["end"] <= g["slot"][1]
    studentnames.save(w, it["id"], {"tags": ["不是名字"]})
    assert studentnames.plan(w) == {"消音": [], "生成": [], "要人處理": []}


def test_ref_windows_rules():
    from bookclub import studentgen

    S = lambda i, a, b: {"id": f"s{i}", "start": a, "end": b, "text": "一句話"}  # noqa: E731
    sents = [S(0, 0, 5), S(1, 5.5, 11), S(2, 11.3, 16), S(3, 20, 26)]      # s2 跟 s3 中間空 4 秒
    wins = studentgen.ref_windows(sents, blocked=[])
    assert [[g["id"] for g in w] for w in wins] == [["s0", "s1", "s2"], ["s1", "s2"]] or \
        [[g["id"] for g in w] for w in wins][0] == ["s0", "s1", "s2"]
    assert studentgen.ref_windows(sents, blocked=[(6, 7)]) == []            # 名字卡在中間：剩下都不到 10 秒
    assert all(w[-1]["end"] - w[0]["start"] <= studentgen.REF_MAX_S for w in wins)


def _fake_synth_factory(ref_wav, ref_text):
    import numpy as np

    def synth(text, seed, speed):
        sr = 24000
        n = int(sr * max(1.0, len(text) * 0.25 / speed))
        t = np.arange(n) / sr
        return (0.2 * np.sin(2 * np.pi * 180 * t)).astype(np.float32), sr
    return synth


def test_generate_with_own_voice_and_swap_edit():
    from bookclub import assemble, studentgen

    w = _fresh()
    review.set_voice(w, "學員2", "保留原聲")
    it = _stu_names(w)[0]
    studentnames.save(w, it["id"], {"做法": studentnames.CODE})
    sp = studentnames.plan(w)
    # 還沒生成：組裝時退回直接消音
    e0 = studentgen.swap_edits(w, sp)
    assert [e["類型"] for e in e0] == ["學員名字消音"] and "還沒生成" in e0[0]["備註"]
    res = studentgen.generate(w, synth_factory=_fake_synth_factory, hear=lambda p: sp["生成"][0]["text"],
                              use_pauses=False, log=lambda m: None)
    assert (w / "參考音" / "學員" / "學員2" / "ref.wav").is_file(), res
    assert len(res["句子"]) == 1 and res["句子"][0]["放回時間格"]["檔案"].startswith("生成/保留原聲學員/")
    assert not (w / "生成" / "學員紀錄.json").exists() and not (w / "生成" / "老師紀錄.json").exists()
    e1 = studentgen.swap_edits(w, sp)
    assert [e["類型"] for e in e1] == ["學員名字換代號"] and "Amy" in e1[0]["text"]
    edits, warns = assemble.add_student_name_edits([], w)
    assert [e["類型"] for e in edits] == ["學員名字換代號"]


def test_no_ref_falls_back_to_mute():
    from bookclub import studentgen, workdir as wd

    w = _fresh()
    review.set_voice(w, "學員2", "保留原聲")
    it = _stu_names(w)[0]
    studentnames.save(w, it["id"], {"做法": studentnames.CODE})
    orig = studentgen.REF_MIN_S
    studentgen.REF_MIN_S = 999.0          # 讓它挑不到參考音
    try:
        res = studentgen.generate(w, synth_factory=_fake_synth_factory, use_pauses=False, check_content=False,
                                  log=lambda m: None)
    finally:
        studentgen.REF_MIN_S = orig
    assert res["退回直接消音"] and not res["句子"]
    e = studentgen.swap_edits(w, studentnames.plan(w))
    assert [x["類型"] for x in e] == ["學員名字消音"] and "找不到" in e[0]["備註"]
    assert wd.read_json(studentgen.log_path(w))


def test_mark_turn_as_teacher_rescans_teacher_names():
    """09-29：學員段落其實是老師 → 改成老師：這段不再重念、補找這段裡老師提到的名字（加在最後，舊編號不變）。"""
    from bookclub import students
    from bookclub import turns as turns_mod
    from bookclub import workdir as wd

    w = _fresh()
    before = wd.read_json(wd.names_path(w))["candidates"]
    turn = next(t for t in turns_mod.page_data(w)["段落"] if t["說話者"] == "學員2" and t["start"] <= 92.5 < t["end"])
    res = turns_mod.save_turn(w, turn["id"], {"說話者": "老師"})
    assert res["補找到的老師名字"] == 1
    after = wd.read_json(wd.names_path(w))["candidates"]
    assert after[:len(before)] == before and after[-1]["代號"] == "Amy" and after[-1]["補找"]
    items, _ = students.build_items(w)
    assert all(it["段落"] != turn["id"] for it in items)
    # 保留原聲時列的「學員提到名字」：那句改成老師就不再列
    review.set_voice(w, "學員2", "保留原聲")
    assert _stu_names(w) == []
    # 再改一次不會重複補
    turns_mod.save_turn(w, turn["id"], {"說話者": "老師"})
    assert len(wd.read_json(wd.names_path(w))["candidates"]) == len(after)


def test_reassign_turns_to_teacher_from_people_section():
    """「學員是誰」勾幾段改成老師：學員那一位整個是老師的話從學員名單消失，並補找老師名字。"""
    from bookclub import turns as turns_mod
    from bookclub import workdir as wd

    w = _fresh()
    ids = [t["id"] for t in turns_mod.page_data(w)["段落"] if t["說話者"] == "學員2"]
    n0 = len(wd.read_json(wd.names_path(w))["candidates"])
    res = turns_mod.reassign_turns(w, ids, "老師")
    assert res["改了幾段"] == len(ids) and res["補找到的老師名字"] == 1
    assert "學員2" not in turns_mod.page_data(w)["學員"]
    assert len(wd.read_json(wd.names_path(w))["candidates"]) == n0 + 1


def test_real_name_and_code_columns():
    """「學員是誰」兩欄：左邊選本名（預設帶名冊代號）、右邊改這支影片的代號；選老師＝整位改成老師。"""
    from bookclub import turns as turns_mod

    w = _fresh()
    d = turns_mod.page_data(w)
    assert "小美" in d["本名選項"] and d["名冊代號"]["小美"] == "Amy" and d["老師名稱"]
    r = turns_mod.set_real_name(w, "學員1", "小美")
    assert r["代號"] == "Amy"
    turns_mod.set_name_code(w, "小美", "Tom")
    assert turns_mod.page_data(w)["學員"]["學員1"]["代號"] == "Tom"
    r = turns_mod.set_real_name(w, "學員2", d["老師名稱"])
    assert r["改成老師"] and "學員2" not in turns_mod.page_data(w)["學員"]


def test_current_ref_uses_this_workdir_ref():
    """10-01：第 4 步「做過沒有」比的是這個工作區裡的參考音（複製到別處時紀錄記的是舊路徑）。"""
    from bookclub import studentgen, tts

    with tempfile.TemporaryDirectory() as d:
        old = Path(d) / "舊" / "參考音" / "學員" / "學員1" / "ref.wav"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"RIFFxx")
        new_w = Path(d) / "新"
        rec = {"參考音": str(old), "參考音指紋": tts.ref_fingerprint(old)}
        assert studentgen.current_ref(new_w, "學員1", rec) == str(old)     # 這裡還沒挑過：照紀錄
        cur = studentgen.ref_dir(new_w, "學員1") / "ref.wav"
        cur.parent.mkdir(parents=True)
        cur.write_bytes(b"RIFFxx")
        old.unlink()                                                      # 舊工作區刪掉了
        assert studentgen.current_ref(new_w, "學員1", rec) == str(cur)
        assert tts.same_ref_file(rec, studentgen.current_ref(new_w, "學員1", rec))


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
