"""10-04 #119：找名字除了判成老師的句子，太短、不確定的句子、老師段落裡判成不是老師的句子也要掃；
學員段落裡的句子照舊不掃；舊工作區的名字候選補掃時，原本的候選編號不變、不重複。
用合成資料跟合成音訊，不載入模型、不連網。

獨立可跑：.venv/bin/python tests/test_names_scope.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_DATA = Path(tempfile.mkdtemp())
os.environ["BOOKCLUB_DATA_DIR"] = str(_DATA)

from bookclub import names as nm  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

SR = 16000
ROSTER = "中文名,其他寫法,英文代號,聲線,性別\n詩涵,,S01,女聲A,女\n"


def _tone(seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (0.2 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


def _words(text: str, t0: float) -> list[dict]:
    return [{"word": ch, "start": round(t0 + 0.3 * i, 3), "end": round(t0 + 0.3 * (i + 1), 3)} for i, ch in enumerate(text)]


# 每句 3 個字「詩涵好」（0.9 秒），句子之間空 1.1 秒。編號：001 老師、002 太短、003 不確定、004 不是老師（以上在老師段落）；
# 005 太短、006 不確定、007 不是老師（學員段落）；008 老師
SENTS = [("0000_001", "老師"), ("0000_002", "太短"), ("0000_003", "不確定"), ("0000_004", "不是老師"),
         ("0000_005", "太短"), ("0000_006", "不確定"), ("0000_007", "不是老師"), ("0000_008", "老師")]


def _make(tmp: Path, turns: list[dict] | None = None, text_turns: list[dict] | None = None):
    w = tmp / "工作區"
    w.mkdir()
    sf.write(str(wd.audio_path(w)), _tone(2.0 * len(SENTS) + 2.0), SR)
    sents, words = [], []
    for k, (sid, lab) in enumerate(SENTS):
        ws = _words("詩涵好", 1.0 + 2.0 * k)
        words += ws
        sents.append({"id": sid, "start": ws[0]["start"], "end": ws[-1]["end"], "text": "詩涵好", "label": lab})
    roster = _DATA / "名冊.csv"
    roster.write_text(ROSTER, encoding="utf-8")
    (w / "校對").mkdir()
    if turns is not None:
        wd.write_json(w / "校對" / "段落.json", {"段落": turns})
    if text_turns is not None:
        wd.write_json(w / "校對" / "段落_文字.json", {"段落": text_turns})
    wd.write_json(wd.speakers_path(w), {"sentences": sents})
    wd.merged_transcript_path(w).parent.mkdir(parents=True, exist_ok=True)
    wd.write_json(wd.merged_transcript_path(w), {"words": words})
    return w, sents, words, roster


TEACHER_IDS = ["0000_001", "0000_002", "0000_003", "0000_004"]
STUDENT_IDS = ["0000_005", "0000_006", "0000_007"]
TURNS = [{"id": "T001", "說話者": "老師", "句子": TEACHER_IDS},
         {"id": "T002", "說話者": "學員1", "句子": STUDENT_IDS},
         {"id": "T003", "說話者": "老師", "句子": ["0000_008"]}]


def _by_sid(res: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in res["candidates"]:
        out.setdefault(c["sentence_id"], []).append(c)
    return out


def test_short_sentence_scanned():
    """太短的句子裡的名字也要出候選（修之前沒有：只掃判成老師的句子）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        w, sents, words, roster = _make(tmp, turns=TURNS)
        got = _by_sid(nm.find_names(wd.audio_path(w), w, sents, words, roster))
        assert "0000_002" in got, sorted(got)
        c = got["0000_002"][0]
        assert c["來源"] == nm.SOURCE_SHORT == "太短句"
        assert c["比對層級"] == "精確" and c["信心"] == "高"   # 信心照比對層級，不因為來源降級
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unsure_sentence_in_teacher_turn_scanned():
    """第一堂 1:34:06.1 那種：老師段落開頭一句「不確定」的句子。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        w, sents, words, roster = _make(tmp, turns=TURNS)
        got = _by_sid(nm.find_names(wd.audio_path(w), w, sents, words, roster))
        assert "0000_003" in got, sorted(got)
        assert got["0000_003"][0]["來源"] == "不確定句"
        assert got["0000_003"][0]["信心"] == "高"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_not_teacher_sentence_in_teacher_turn_scanned():
    tmp = Path(tempfile.mkdtemp())
    try:
        w, sents, words, roster = _make(tmp, turns=TURNS)
        got = _by_sid(nm.find_names(wd.audio_path(w), w, sents, words, roster))
        assert "0000_004" in got, sorted(got)
        assert got["0000_004"][0]["來源"] == "老師段落裡的不是老師句"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_student_turn_sentences_not_scanned():
    """學員段落裡的句子（太短、不確定、不是老師都一樣）不多出老師的名字卡；判成老師的句子照舊、沒有來源欄位。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        w, sents, words, roster = _make(tmp, turns=TURNS)
        res = nm.find_names(wd.audio_path(w), w, sents, words, roster)
        got = _by_sid(res)
        assert not set(STUDENT_IDS) & set(got), sorted(got)
        assert "來源" not in got["0000_001"][0] and "來源" not in got["0000_008"][0]
        # 判成老師的句子排在前面（跟以前一樣的順序），放寬的排在後面
        assert [c["sentence_id"] for c in res["candidates"]] == ["0000_001", "0000_008", "0000_002", "0000_003", "0000_004"]
        assert res["掃描範圍"] == nm.SCAN_SCOPE
        assert res["統計"]["各來源筆數"] == {"太短句": 1, "不確定句": 1, "老師段落裡的不是老師句": 1}
        assert res["統計"]["總筆數"] == 5
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_text_turns_used_before_turns_exist():
    """分析第 6 步時 段落.json 還沒有：用 段落_文字.json（段落分析的文字那一半）判斷是不是學員段落。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        text_turns = [{"說話者": "老師", "句子": TEACHER_IDS}, {"說話者": "學員", "句子": STUDENT_IDS},
                      {"說話者": "老師", "句子": ["0000_008"]}]
        w, sents, words, roster = _make(tmp, text_turns=text_turns)
        got = _by_sid(nm.find_names(wd.audio_path(w), w, sents, words, roster))
        assert set(got) == {"0000_001", "0000_008", "0000_002", "0000_003", "0000_004"}, sorted(got)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_extra_scope_without_turns():
    """沒有段落資料：太短、不確定全掃；不是老師的不掃（不知道是不是老師段落）。"""
    sents = [{"id": sid, "label": lab} for sid, lab in SENTS]
    got = nm.extra_scope(sents, {})
    assert got == {"0000_002": "太短句", "0000_003": "不確定句", "0000_005": "太短句", "0000_006": "不確定句"}


def test_custom_select_not_widened():
    """給了 select（學員名字候選、段落改成老師後補找）照舊只掃選到的句子，不加來源。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        w, sents, words, roster = _make(tmp, turns=TURNS)
        res = nm.find_names(wd.audio_path(w), w, sents, words, roster, select=lambda s: s["id"] == "0000_007",
                            cache_path=tmp / "x.json", use_cache=False)
        assert [c["sentence_id"] for c in res["candidates"]] == ["0000_007"]
        assert "來源" not in res["candidates"][0] and "掃描範圍" not in res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _old_cache(w: Path, sents, words, roster) -> list[dict]:
    """舊版找的名字候選：只掃判成老師的句子（用 select 模擬），沒有「掃描範圍」，最後一筆是段落改成老師後補找的。"""
    res = nm.find_names(wd.audio_path(w), w, sents, words, roster, select=lambda s: s["label"] == "老師",
                        cache_path=wd.names_path(w), use_cache=False)
    patched = nm.find_names(wd.audio_path(w), w, sents, words, roster, select=lambda s: s["id"] == "0000_004",
                            cache_path=w / "補找暫存.json", use_cache=False)["candidates"]
    for c in patched:
        c["補找"] = "段落改成老師後補找"
    res["candidates"] += patched
    res.pop("掃描範圍", None)
    wd.write_json(wd.names_path(w), res)
    return res["candidates"]


def test_old_cache_supplement_keeps_ids_and_no_duplicates():
    """舊工作區重跑分析：原本的候選照原樣留在原本的位置（編號＝順位，覆核決定照舊對得上），
    放寬後新找到的加在最後面、帶來源；已經補找過的同一處不重複；再跑一次不再多。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        w, sents, words, roster = _make(tmp, turns=TURNS)
        old = _old_cache(w, sents, words, roster)
        assert [c["sentence_id"] for c in old] == ["0000_001", "0000_008", "0000_004"]
        decisions = {"1": {"做法": "直接消音", "已確認": True}, "3": {"做法": "只換名字", "已確認": True}}
        wd.write_json(w / "名字覆核決定.json", decisions)

        res = nm.find_names(wd.audio_path(w), w, sents, words, roster)
        cands = res["candidates"]
        assert cands[:3] == old                                     # 原本的三筆一字不差、位置不變
        assert [c["sentence_id"] for c in cands[3:]] == ["0000_002", "0000_003"]   # not_t 已經補找過，不重複
        assert all(c["補找"] == nm.SUPPLEMENT_TAG and c.get("來源") for c in cands[3:])
        saved = wd.read_json(wd.names_path(w))
        assert saved["掃描範圍"] == nm.SCAN_SCOPE and saved["統計"]["總筆數"] == 5
        assert saved["統計"]["放寬範圍補找數"] == 2
        assert wd.read_json(w / "名字覆核決定.json") == decisions      # 決定檔不動
        # 新候選的試聽音檔編號接在後面，不蓋掉原本的
        assert {Path(c["候選音檔"]).name[:4] for c in cands[3:]} == {"0003", "0004"}

        again = nm.find_names(wd.audio_path(w), w, sents, words, roster)
        assert again["candidates"] == cands
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_preview_supplement_writes_nothing():
    """`inspect 名字 --重算` 用的：算出來會多幾筆，但不寫名字候選.json、不寫試聽音檔。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        w, sents, words, roster = _make(tmp, turns=TURNS)
        _old_cache(w, sents, words, roster)
        before = wd.names_path(w).read_bytes()
        clips_before = sorted(p.name for p in wd.name_candidates_dir(w).iterdir())
        got = nm.preview_supplement(w)
        assert [c["sentence_id"] for c in got["candidates"]] == ["0000_002", "0000_003"]
        assert len(got["找到"]) == 3 and got["現有候選數"] == 3 and got["掃描範圍"] is None
        assert got["多掃句數"] == {"太短句": 1, "不確定句": 1, "老師段落裡的不是老師句": 1}
        assert wd.names_path(w).read_bytes() == before
        assert sorted(p.name for p in wd.name_candidates_dir(w).iterdir()) == clips_before
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_inspect_names_recompute_prints_no_text():
    """`bookclub inspect <工作區> 名字 --重算`：印時間、句子編號、來源、層級，不印名字與句子；不寫檔。"""
    from bookclub import safeview

    tmp = Path(tempfile.mkdtemp())
    try:
        w, sents, words, roster = _make(tmp, turns=TURNS)
        _old_cache(w, sents, words, roster)
        before = wd.names_path(w).read_bytes()
        out = "\n".join(safeview.run([str(w), "名字", "--重算"]))
        assert "詩涵" not in out and "S01" not in out, out
        assert "現有候選 3 筆" in out and "舊版（只掃判成老師的句子）" in out
        assert "現有候選沒有的 2 筆、2 處" in out
        assert "sentence_id=0000_002  來源=太短句  比對層級=精確  信心=高" in out, out
        assert "sentence_id=0000_003  來源=不確定句" in out
        only = "\n".join(safeview.run([str(w), "名字", "--重算", "--from", "0:04.5", "--to", "0:05.8"]))
        assert "sentence_id=0000_003" in only and "sentence_id=0000_002" not in only
        assert wd.names_path(w).read_bytes() == before
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"通過：{t.__name__}")
    print(f"\n共 {len(tests)} 個測試通過")
    shutil.rmtree(_DATA, ignore_errors=True)


if __name__ == "__main__":
    _run_all()
