"""1015-8 第 3 段（32 號設計 §4、§5、§9，以 §1a、§1b 為準）：清空間＋瘦身版＋總覽分組。#238 TDD 試點：先寫這支、確認全失敗，再實作。

- 「第一堂結構」的假工作區清完，剩下的檔案集合＝§2.2「留」的清單，一個不多一個不少；不認得的檔、捷徑、捷徑指到的檔都還在
- §4.3 再確認：沒打包、打包沒核對通過、`含成品` 不是 true、打包位置讀不到或大小不對、清單指紋不同、有東西在跑 → 一個檔都不刪
- §4.4 先寫清單→搬進 `.結案清理中/`→刪→寫已結案；搬到一半、刪到一半中斷 →「繼續清」做完結果一樣；`--restore` 搬回去
- §4.2 版本表 `影片還在` 改 false、`歷史.jsonl` 記一條「結案」
- §1a 問題 1 甲：WSL 版面（工作區在 `~/讀書會剪輯資料/影片/xxx_剪輯工作區/`）影片副本只算那一支、一起清；Mac 上影片不在清單裡
- 硬連結：合計用 (st_dev, st_ino) 去重（跟 `closing.dir_size(seen)` 一致）
- §5、§9.3 瘦身版：已結案後第 1–5 步每個 POST 都 409、每個 GET 不報 500、工作區檔案打開前後一模一樣
- 畫面（node）：框裡那一句、正在清、已結案、繼續清；總覽「已結案」分組

合成資料，全部寫在系統暫存資料夾（tests/_testtmp.py）；刪檔只發生在這裡的假工作區。不碰 ~/讀書會剪輯資料。
獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_closing_clean.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from bookclub import closing  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

NODE = shutil.which("node")
FIOJS = (REPO_ROOT / "bookclub" / "web" / "fileio.js").read_text(encoding="utf-8")
APPJS = (REPO_ROOT / "bookclub" / "web" / "app.js").read_text(encoding="utf-8")


def _mk(p: Path, data: bytes = b"x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _files(w: Path) -> set[str]:
    """工作區裡所有的檔（含捷徑本身，不跟捷徑進去），相對路徑。"""
    out = set()
    for root, dirs, files in os.walk(w, followlinks=False):
        for n in files + [d for d in dirs if os.path.islink(os.path.join(root, d))]:
            out.add(str(Path(root, n).relative_to(w)))
    return out


def _digest(w: Path) -> dict[str, str]:
    """每個檔的內容指紋＋修改時間（打開前後一模一樣用）。"""
    out = {}
    for rel in sorted(_files(w)):
        p = w / rel
        if p.is_symlink():
            out[rel] = "->" + os.readlink(p)
        else:
            out[rel] = hashlib.sha1(p.read_bytes()).hexdigest() + f"@{p.stat().st_mtime_ns}"
    return out


# ---------------------------------------------------------------------------
# 第一堂結構的假工作區（§2.2 每一列都有）
# ---------------------------------------------------------------------------

KEEP = {
    # 紀錄（transcript、修改需求、校對、覆核、匯出、備份）
    "transcript/merged.json", "transcript/raw/00.json",
    "修改需求/格式.json", "修改需求/版本.json", "修改需求/卡片.json", "修改需求/版本/v3/處理紀錄.json",
    "修改需求/升級前備份_20261014/成品檢查.json", "修改需求/歷史.jsonl", "修改需求/.寫入鎖",
    "校對/段落.json", "覆核/覆核決定.json", "覆核/成品檢查.json",
    "匯出/覆核結果_0-97.zip", "備份/舊設定.json",
    # 工作區最上層的紀錄
    "名字覆核決定.json", "學員名字覆核決定.json", "分析結果.json", "報告.md", "說話者判斷.json", "重疊.json",
    "名字候選.json", "名字覆核.html", "工作區設定.json",
    # 輸出/ 裡每一版改了哪裡
    "輸出/處理標記_0-97.md", "輸出/處理標記_0-97.html", "輸出/剪輯決策_0-97.json", "輸出/輸出摘要_0-97.json",
    "輸出/處理標記_v2_0-97.md",
    # 生成/ 的紀錄（.json 一律留，只有兩個快取例外）
    "生成/老師紀錄.json", "生成/學員紀錄.json", "生成/處理紀錄.json", "生成/剪輯決策.json", "生成/執行進度.json",
    "生成/名字處理計畫.json", "生成/學員/_重新生成版本.json", "生成/老師/重做前_20261015/說明.json",
    # 參考音/ 的紀錄
    "參考音/候選.json", "參考音/挑選紀錄.json", "參考音/底噪.json", "參考音/ref.txt",
    "參考音/舊候選備份_20261001/候選.json",
    # 快取資料夾裡的 .json 也留（留的贏）
    "web快取/索引.json",
}
UNKNOWN = {   # 不認得的：不動
    "老師的筆記.docx", "輸出/老師自己放的.mp4", "輸出/處理前後.html", "輸出/.DS_Store", "生成/老師/模型.npy",
    "段落.bak",
}
LINKS = {"捷徑.mp4", "web快取/捷徑.wav", "生成/捷徑資料夾"}   # 捷徑本身不動
CLEAN = {
    # 影片
    "輸出/成品_v1_0-97_sw.mp4", "輸出/成品_v2_0-97_sw.mp4", "輸出/成品_v3_0-97_sw.mp4", "輸出/成品_v3_0-97_標字版.mp4",
    "輸出/成品_0-97_sw.mp4", "輸出/最終成品_0-97_sw_20261016-101500.mp4", "輸出/最終成品_0-97_sw_20261015-090000.mp4",
    # 比對用聲音
    "輸出/原聲_0-97.wav", "輸出/新聲音_0-97.wav", "輸出/原聲音軌.wav", "輸出/新聲音軌.wav",
    "輸出/處理前後/001_前.wav", "輸出/處理前後/001_後.wav",
    # 輸出到一半
    "輸出/_輸出中_成品_v2_0-97_sw.mp4", "輸出/.最終成品_0-97_sw_20261016-111111.mp4.輸出中",
    # AI 生成的聲音
    "生成/老師/S001.wav", "生成/老師/S001_放回時間格.wav", "生成/學員/T003_01.wav",
    "生成/老師/重做前_20261015/S001_第1次.wav", "生成/老師/_嘗試快取.json", "生成/學員/_停頓快取.json",
    # 播放和分析用的暫存
    "web快取/1.00_2.00.wav", "web快取/成品檢查/前_001.wav", "audio.flac", "audio.殘檔-20261001-101010.flac",
    "chunks/chunk_0000.flac", "名字候選/0000_12.3s.wav", "名字候選/0000_12.3s_消音.wav",
    "重疊/區域_1.00_2.00.flac", "重疊/區域_1.00_2.00.rttm",
    "參考音/cand_01.wav", "參考音/底噪_48k.wav", "參考音/舊候選備份_20261001/cand_01.wav",
}


def _lesson1(root: Path, *, ws_parent: Path | None = None, video: Path | None = None) -> Path:
    """第一堂結構：輸出過第 3 版（最終成品是成品的硬連結）、修改需求、逐字稿、各種暫存。"""
    w = (ws_parent or root) / "2025-04-09 第一堂_剪輯工作區"
    for rel in KEEP | UNKNOWN:
        text = json.dumps({"留": rel}, ensure_ascii=False) if rel.endswith((".json", ".jsonl")) else f"留:{rel}"
        _mk(w / rel, (text + ("\n" if rel.endswith(".jsonl") else "")).encode())
    for rel in CLEAN:
        _mk(w / rel, os.urandom(2000 + 37 * len(rel)))
    # 最終成品（最後一次輸出）是第 3 版成品的硬連結：大小只算一次
    fin = w / "輸出/最終成品_0-97_sw_20261016-101500.mp4"
    fin.unlink()
    os.link(w / "輸出/成品_v3_0-97_sw.mp4", fin)
    # 捷徑：指到工作區外面的檔與資料夾（捷徑跟指到的都不能動）
    outside = root / "外面"
    _mk(outside / "真的.mp4", b"real")
    _mk(outside / "資料夾" / "S009.wav", b"wav")
    os.symlink(outside / "真的.mp4", w / "捷徑.mp4")
    os.symlink(outside / "真的.mp4", w / "web快取/捷徑.wav")
    os.symlink(outside / "資料夾", w / "生成/捷徑資料夾")
    vid = video or (root / "2025-04-09 第一堂.mp4")
    if not vid.exists():
        _mk(vid, os.urandom(5000))
    wd.write_json(w / "分析結果.json", {"video": str(vid), "影片長度": 5820.0})
    wd.write_json(w / "transcript/merged.json", {"sentences": [{"id": "1", "start": 0.0, "end": 1.0, "text": "甲"}]})
    wd.write_json(w / "覆核/成品檢查.json", {"輸出成品": {
        "檔案": "輸出/最終成品_0-97_sw_20261016-101500.mp4", "來源": "輸出/成品_v3_0-97_sw.mp4",
        "處理紀錄產生時間": "2026-10-16T10:00:00", "版本號": 3}})
    return w


def _pack(w: Path, loc: Path, **kw) -> closing.PackJob:
    loc.mkdir(parents=True, exist_ok=True)
    job = closing.PackJob(w, loc, **kw)
    job.run()
    assert job.state == "已存好", (job.state, job.error)
    return job


def _plan(w: Path, **kw) -> dict:
    return closing.clean_plan(w, kind=kw.pop("kind", "mac"), **kw)


# ---------------------------------------------------------------------------
# 清單（§4.1）
# ---------------------------------------------------------------------------

def test_plan_lists_exactly_the_clean_rows():
    with tempfile.TemporaryDirectory() as t:
        w = _lesson1(Path(t))
        before = _digest(w)
        plan = _plan(w)
        got = {r["相對路徑"] for r in plan["檔案"]}
        assert got == CLEAN, (got - CLEAN, CLEAN - got)
        assert _digest(w) == before                       # 算清單只讀
        assert all(r["分類"] in ("影片", "比對用聲音", "AI 生成的聲音", "暫存") for r in plan["檔案"])
        cats = {r["相對路徑"]: r["分類"] for r in plan["檔案"]}
        assert cats["輸出/成品_v3_0-97_sw.mp4"] == "影片" and cats["輸出/原聲音軌.wav"] == "比對用聲音"
        assert cats["生成/老師/_嘗試快取.json"] == "AI 生成的聲音" and cats["web快取/1.00_2.00.wav"] == "暫存"
        assert cats["輸出/_輸出中_成品_v2_0-97_sw.mp4"] == "暫存"
        # 合計：硬連結只算一次＝清完前後 dir_size（同一套去重）的差
        size_all = closing.dir_size(w, set())
        link = w / "輸出/成品_v3_0-97_sw.mp4"
        assert plan["合計"] == sum(r["大小"] for r in plan["檔案"]) - link.stat().st_size
        assert isinstance(plan["清單指紋"], str) and len(plan["清單指紋"]) == 40
        assert plan["清單指紋"] == _plan(w)["清單指紋"]    # 同一份工作區算兩次一樣
        assert size_all > plan["合計"]
        # 分類小計
        assert sum(c["大小"] for c in plan["分類"]) == plan["合計"]
        assert not plan["含影片副本"]


def test_classify_pure_keep_wins_unknown_untouched():
    c = closing.classify
    assert c("修改需求/版本/v3/成品_v3.mp4")[0] == "留"          # 修改需求/ 整個留
    assert c("transcript/a.wav")[0] == "留"
    assert c("web快取/索引.json")[0] == "留"                     # .json 留的贏
    assert c("生成/老師/_嘗試快取.json")[0] == "清"              # 兩個快取例外
    assert c("輸出/成品_v1_0-3_sw.mp4") == ("清", "影片")
    assert c("輸出/老師自己放的.mp4")[0] is None                 # 不認得
    assert c("輸出/子資料夾/成品_v1.mp4")[0] is None             # 只認 輸出/ 最上層
    assert c("audio.flac")[0] == "清" and c("audio.殘檔-1.flac")[0] == "清" and c("audio.mp3")[0] is None
    assert c("結案.json")[0] == "留"
    assert c(".結案清理中/輸出/成品_v1.mp4")[0] == "留"          # 清理中資料夾自己不在清單裡（不會清兩次）


def test_plan_changes_when_files_change():
    with tempfile.TemporaryDirectory() as t:
        w = _lesson1(Path(t))
        fp = _plan(w)["清單指紋"]
        _mk(w / "web快取/9.00_10.00.wav", b"new")
        assert _plan(w)["清單指紋"] != fp


# ---------------------------------------------------------------------------
# 清完剩下的＝留的清單（§9.1 第 2 道保險）
# ---------------------------------------------------------------------------

def test_clean_leaves_exactly_keep_set_links_and_unknown():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        w = _lesson1(root)
        _pack(w, root / "D槽")
        pack_before = _digest(root / "D槽")
        outside_before = _digest(root / "外面")
        plan = _plan(w)
        size_before = closing.dir_size(w, set())
        r = closing.clean(w, plan["清單指紋"], kind="mac")
        left = _files(w)
        assert left == KEEP | UNKNOWN | LINKS | {closing.RECORD_FILE}, (left - (KEEP | UNKNOWN | LINKS), (KEEP | UNKNOWN | LINKS) - left)
        assert not (w / closing.CLEAN_DIR).exists()
        assert _digest(root / "外面") == outside_before         # 捷徑指到的檔還在
        assert _digest(root / "D槽") == pack_before             # 打包的地方沒動
        assert (root / "2025-04-09 第一堂.mp4").is_file()        # Mac：影片是原檔，不碰
        rec = closing.read_record(w)
        assert rec["狀態"] == closing.CLOSED and closing.closed(w)
        cl = rec["清理"]
        assert cl["清單指紋"] == plan["清單指紋"] and cl["完成"] and cl["搬好"] and cl["開始"]
        assert cl["省下位元組"] == plan["合計"] == r["省下位元組"]
        assert size_before - closing.dir_size(w, set()) >= plan["合計"] - 10_000   # 結案.json 變大一點
        assert {x["相對路徑"] for x in cl["清單"]} == CLEAN
        # 清完的空資料夾收掉（只收空的）
        assert not (w / "chunks").exists() and not (w / "名字候選").exists() and not (w / "重疊").exists()
        assert (w / "web快取").is_dir()                            # 還有 索引.json 與捷徑


# ---------------------------------------------------------------------------
# §4.3 再確認：任何一道沒過就一個檔都不刪
# ---------------------------------------------------------------------------

def _refused(w: Path, fp: str, *, busy=None, want: str = "") -> None:
    before = _digest(w)
    rec_before = closing.read_record(w)
    try:
        closing.clean(w, fp, kind="mac", busy=busy)
    except closing.CleanError as e:
        assert want in str(e), str(e)
    else:
        raise AssertionError("應該拒絕")
    assert _digest(w) == before
    assert closing.read_record(w) == rec_before
    assert not (w / closing.CLEAN_DIR).exists()


def test_refuse_without_verified_pack_with_product():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        w = _lesson1(root)
        fp = _plan(w)["清單指紋"]
        _refused(w, fp, want="還沒存好")                                   # 沒打包
        job = _pack(w, root / "D槽")
        rec = closing.read_record(w)
        rec["打包"][-1]["核對"] = "還沒"
        wd.write_json(closing.record_path(w), rec)
        _refused(w, fp, want="還沒存好")                                   # 打包沒核對通過
        rec["打包"][-1]["核對"] = "通過"
        rec["打包"][-1]["含成品"] = False
        wd.write_json(closing.record_path(w), rec)
        _refused(w, fp, want="成品")                                      # 含成品不是 true
        rec["打包"][-1].pop("含成品")
        wd.write_json(closing.record_path(w), rec)
        _refused(w, fp, want="成品")                                      # 沒有這一欄也不行
        rec["打包"][-1]["含成品"] = True
        wd.write_json(closing.record_path(w), rec)
        # 打包位置讀不到（D 槽拔掉）
        folder = Path(job.folder)
        folder.rename(folder.with_name("拔掉了"))
        _refused(w, fp, want="找不到剛才存的地方")
        folder.with_name("拔掉了").rename(folder)
        # 打包裡一個檔大小不對
        f = next(p for p in folder.rglob("*.mp4"))
        data = f.read_bytes()
        f.write_bytes(data[:-1])
        _refused(w, fp, want="找不到剛才存的地方")
        f.write_bytes(data)
        # 清單指紋不一樣
        _refused(w, "0" * 40, want="清單變了")
        # 有東西在跑
        _refused(w, fp, busy=lambda: "第 4 步正在跑，跑完才能清。", want="第 4 步正在跑")
        # 都對了才清
        closing.clean(w, fp, kind="mac")
        assert closing.closed(w)


def test_refuse_when_only_records_pack_is_the_verified_one():
    """最近一次核對通過的是「只有紀錄」的那一份（理論上已結案才會有）→ 不算有成品。"""
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        w = _lesson1(root)
        _pack(w, root / "D槽", records_only=True)
        _refused(w, _plan(w)["清單指紋"], want="成品")


# ---------------------------------------------------------------------------
# §4.4 中斷續做、搬回去
# ---------------------------------------------------------------------------

class _Crash(Exception):
    pass


def _clean_ref(root: Path) -> tuple[set, dict]:
    """沒中斷的那一次：清完剩下的檔與 結案.json 的清理欄位（給中斷的比）。"""
    w = _lesson1(root)
    _pack(w, root / "D槽")
    closing.clean(w, _plan(w)["清單指紋"], kind="mac")
    return _files(w), closing.read_record(w)["清理"]


def test_interrupted_move_then_resume_same_result():
    with tempfile.TemporaryDirectory() as t:
        ref_files, ref_clean = _clean_ref(Path(t) / "一")
        root = Path(t) / "二"
        w = _lesson1(root)
        _pack(w, root / "D槽")
        n = {"搬": 0}

        def hook(stage, rel):
            if stage == "搬":
                n["搬"] += 1
                if n["搬"] == 7:
                    raise _Crash()

        try:
            closing.clean(w, _plan(w)["清單指紋"], kind="mac", hook=hook)
        except _Crash:
            pass
        rec = closing.read_record(w)
        assert rec["狀態"] == closing.CLEANING and closing.closed(w) and not rec["清理"].get("搬好")
        assert (w / closing.CLEAN_DIR).is_dir()
        assert closing.clean_resume(w)["省下位元組"] == ref_clean["省下位元組"]
        assert _files(w) == ref_files
        assert closing.read_record(w)["狀態"] == closing.CLOSED


def test_interrupted_delete_then_resume_same_result():
    with tempfile.TemporaryDirectory() as t:
        ref_files, ref_clean = _clean_ref(Path(t) / "一")
        root = Path(t) / "二"
        w = _lesson1(root)
        _pack(w, root / "D槽")

        def hook(stage, rel):
            if stage == "刪":   # 刪到一半：刪掉幾個，然後當掉
                for p in list((w / closing.CLEAN_DIR).rglob("*.wav"))[:5]:
                    p.unlink()
                raise _Crash()

        try:
            closing.clean(w, _plan(w)["清單指紋"], kind="mac", hook=hook)
        except _Crash:
            pass
        rec = closing.read_record(w)
        assert rec["狀態"] == closing.CLEANING and rec["清理"].get("搬好") and rec["清理"].get("刪除開始")
        # 刪已經開始：不給搬回去
        try:
            closing.restore(w)
        except closing.CleanError:
            pass
        else:
            raise AssertionError("刪開始後不能搬回去")
        got = closing.clean_resume(w)
        assert _files(w) == ref_files and got["省下位元組"] == ref_clean["省下位元組"]


def test_restore_before_delete_puts_everything_back():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        w = _lesson1(root)
        _pack(w, root / "D槽")
        before = {k: v for k, v in _digest(w).items() if k != closing.RECORD_FILE}

        def hook(stage, rel):
            if stage == "刪":
                raise _Crash()

        try:
            closing.clean(w, _plan(w)["清單指紋"], kind="mac", hook=hook)
        except _Crash:
            pass
        assert closing.closed(w)
        n = closing.restore(w)
        assert n == len(CLEAN)
        after = {k: v for k, v in _digest(w).items() if k != closing.RECORD_FILE}
        assert after == before
        rec = closing.read_record(w)
        assert rec["狀態"] == closing.PACKED and not closing.closed(w) and rec["清理"].get("搬回")


def test_reopen_renames_record():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        w = _lesson1(root)
        _pack(w, root / "D槽")
        closing.clean(w, _plan(w)["清單指紋"], kind="mac")
        new = closing.reopen(w)
        assert not closing.record_path(w).exists() and not closing.closed(w)
        assert new.name.startswith("結案_取消_") and new.parent == w and new.is_file()


def test_versions_marked_video_gone_and_history():
    import test_needs as tn

    from bookclub import needs

    w = tn._fresh()
    log = tn._log()
    tn._setup(w, log, tn._check(log, {}))
    tn._quiet(needs.upgrade, w)
    rel = tn._prod(w, "成品_0-3_sw.mp4", tn.T1)
    fin = w / "輸出" / "最終成品_0-3_sw_20261016-101500.mp4"
    shutil.copy2(w / rel, fin)
    chk = wd.read_json(w / "覆核/成品檢查.json")
    chk["輸出成品"] = {"檔案": str(fin.relative_to(w)), "來源": rel, "處理紀錄產生時間": log["產生時間"]}
    wd.write_json(w / "覆核/成品檢查.json", chk)
    assert needs.load(w).versions and all(v.get("影片還在") is not False for v in needs.load(w).versions.values())
    _pack(w, w.parent / "存這裡")
    closing.clean(w, _plan(w)["清單指紋"], kind="mac")
    st = needs.load(w)
    assert all(v["影片還在"] is False for v in st.versions.values())
    last = needs.read_history(w)[-1]
    assert last["動作"] == "結案" and "省下位元組" in (last["後"] or {})
    assert not (w / "生成" / "學員" / "T003.wav").exists() if (w / "生成").is_dir() else True


# ---------------------------------------------------------------------------
# §1a 問題 1 甲：WSL 的影片副本
# ---------------------------------------------------------------------------

def test_wsl_video_copy_only_that_one_cleaned():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        data = root / "讀書會剪輯資料"
        vids = data / "影片"
        vid = _mk(vids / "2025-04-09 第一堂.mp4", os.urandom(7000))
        other = _mk(vids / "第二堂.mp4", os.urandom(3000))                  # 別支影片的副本：不動
        w = _lesson1(root, ws_parent=vids, video=vid)
        mac = _plan(w, kind="mac", data=data)
        assert not mac["含影片副本"] and all(r["分類"] != "影片副本" for r in mac["檔案"])
        plan = _plan(w, kind="wsl", data=data)
        extra = [r for r in plan["檔案"] if r["分類"] == "影片副本"]
        assert len(extra) == 1 and extra[0]["路徑"] == str(vid) and extra[0]["大小"] == 7000 and plan["含影片副本"]
        assert plan["合計"] == mac["合計"] + 7000
        # 另一個沒結案的專案也用這支影片 → 不清
        w2 = vids / "重跑_剪輯工作區"
        wd.write_json(w2 / "分析結果.json", {"video": str(vid)})
        wd.write_json(data / "專案清單.json", {"專案": [str(w), str(w2)]})
        assert not _plan(w, kind="wsl", data=data)["含影片副本"]
        wd.write_json(data / "專案清單.json", {"專案": [str(w)]})
        # 影片是捷徑：不清
        _pack(w, root / "D槽")
        before = closing.dir_size(w, set()) + vid.stat().st_size
        closing.clean(w, plan["清單指紋"], kind="wsl", data=data)
        assert not vid.exists() and other.is_file()
        assert _files(w) == KEEP | UNKNOWN | LINKS | {closing.RECORD_FILE}
        assert closing.read_record(w)["清理"]["省下位元組"] == plan["合計"]
        assert before - closing.dir_size(w, set()) >= plan["合計"] - 10_000


def test_wsl_video_symlink_or_outside_import_dir_not_listed():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        data = root / "讀書會剪輯資料"
        real = _mk(root / "Windows" / "第一堂.mp4", os.urandom(100))
        (data / "影片").mkdir(parents=True)
        link = data / "影片" / "第一堂.mp4"
        os.symlink(real, link)
        w = _lesson1(root, ws_parent=data / "影片", video=link)
        assert not _plan(w, kind="wsl", data=data)["含影片副本"]
        w2 = _lesson1(root / "別處", video=real)                         # 影片不在匯入資料夾
        assert not _plan(w2, kind="wsl", data=data)["含影片副本"]


def test_wsl_interrupted_move_with_video_copy_resumes():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        data = root / "讀書會剪輯資料"
        vid = _mk(data / "影片" / "2025-04-09 第一堂.mp4", os.urandom(7000))
        w = _lesson1(root, ws_parent=data / "影片", video=vid)
        _pack(w, root / "D槽")
        plan = _plan(w, kind="wsl", data=data)

        def hook(stage, rel):
            if stage == "刪":
                raise _Crash()

        try:
            closing.clean(w, plan["清單指紋"], kind="wsl", data=data, hook=hook)
        except _Crash:
            pass
        assert not vid.exists() and (w / closing.CLEAN_DIR / closing.OUTSIDE_DIR / vid.name).is_file()
        closing.restore(w)
        assert vid.is_file()                                               # 搬回原位
        closing.clean(w, _plan(w, kind="wsl", data=data)["清單指紋"], kind="wsl", data=data)
        assert not vid.exists() and closing.closed(w)


# ---------------------------------------------------------------------------
# 伺服器：POST /api/close/clean、一顆鈕跑到底、瘦身版（§5、§9.3）
# ---------------------------------------------------------------------------

def _serve(w: Path):
    from bookclub import server as srv

    httpd = srv.BookclubServer(("127.0.0.1", 0), srv.Handler, workdir=w, video=None)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _call(base, path, body=None, method=None):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method=method or ("GET" if body is None else "POST"))
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            ctype = r.headers.get("Content-Type", "")
            return r.status, (json.loads(raw) if "json" in ctype else raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


def _wait(base, done=lambda st: True):
    for _ in range(400):
        st = _call(base, "/api/close/state")[1]
        job = st.get("工作")
        if job and job["狀態"] not in ("等待", "存檔中", "核對中", "清理中") and done(st):
            return st
        time.sleep(0.05)
    raise AssertionError(st)


def test_api_one_button_pack_then_clean():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        w = _lesson1(root)
        loc = root / "存這裡"
        loc.mkdir()
        httpd, base = _serve(w)
        try:
            st = _call(base, "/api/close/state")[1]
            assert st["暫存大小"] == _plan(w)["合計"] and st["清單指紋"] == _plan(w)["清單指紋"]
            assert st["含影片副本"] is False
            code, r = _call(base, "/api/close/pack", {"位置": "自己選", "路徑": str(loc), "清單指紋": st["清單指紋"]})
            assert code == 200, r
            st = _wait(base)
            assert st["工作"]["狀態"] == "已結案", st["工作"]
            assert st["狀態"] == closing.CLOSED and st["已結案"]
            assert _files(w) == KEEP | UNKNOWN | LINKS | {closing.RECORD_FILE}
            assert st["清理"]["省下位元組"] > 0 and "存這裡" in st["清理"]["成品存在"]
            assert _call(base, "/api/close/cancel", {})[0] == 400
            code, r = _call(base, "/api/close/clean", {"清單指紋": "x"})
            assert code == 400 and "已結案" in r["error"]
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_api_clean_refused_while_busy_and_no_fingerprint():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        w = _lesson1(root)
        _pack(w, root / "D槽")
        httpd, base = _serve(w)
        try:
            before = _digest(w)
            fp = _call(base, "/api/close/state")[1]["清單指紋"]
            code, r = _call(base, "/api/close/clean", {})
            assert code == 400                                               # 沒帶指紋
            httpd.exec_state["running"] = True
            code, r = _call(base, "/api/close/clean", {"清單指紋": fp})
            assert code == 400 and "第 4 步" in r["error"]
            httpd.exec_state["running"] = False
            httpd.final_exporting = 1                                        # 輸出成品正在複製（計數器）
            code, r = _call(base, "/api/close/clean", {"清單指紋": fp})
            assert code == 400 and "輸出" in r["error"]
            httpd.final_exporting = 0

            class _Job:
                purpose, running = "影片", True

            httpd.copy_jobs["假的"] = _Job()                                # 複製正在跑
            code, r = _call(base, "/api/close/clean", {"清單指紋": fp})
            assert code == 400 and "複製" in r["error"]
            httpd.copy_jobs.clear()
            assert _digest(w) == before
            code, r = _call(base, "/api/close/clean", {"清單指紋": fp})
            assert code == 200, r
            st = _wait(base)
            assert st["工作"]["狀態"] == "已結案" and closing.closed(w)
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_api_resume_after_server_restart():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        w = _lesson1(root)
        _pack(w, root / "D槽")

        def hook(stage, rel):
            if stage == "搬" and rel.endswith(".flac"):
                raise _Crash()

        try:
            closing.clean(w, _plan(w)["清單指紋"], kind="mac", hook=hook)
        except _Crash:
            pass
        httpd, base = _serve(w)                                              # 伺服器重開
        try:
            st = _call(base, "/api/close/state")[1]
            assert st["狀態"] == closing.CLEANING and st["可以繼續清"] and st["已結案"]
            code, r = _call(base, "/api/close/clean", {"繼續": True})
            assert code == 200, r
            st = _wait(base)
            assert st["狀態"] == closing.CLOSED
            assert _files(w) == KEEP | UNKNOWN | LINKS | {closing.RECORD_FILE}
        finally:
            httpd.shutdown()
            httpd.server_close()


def _closed_fresh() -> Path:
    """真的後端資料的假工作區（test_needs）：輸出過、打包、清完。"""
    import test_needs as tn

    from bookclub import needs

    w = tn._fresh()
    log = tn._log()
    tn._setup(w, log, tn._check(log, {"學員重念@40.00": ["通過"]}))
    tn._quiet(needs.upgrade, w)
    rel = tn._prod(w, "成品_0-3_sw.mp4", tn.T1)
    fin = w / "輸出" / "最終成品_0-3_sw_20261016-101500.mp4"
    shutil.copy2(w / rel, fin)
    chk = wd.read_json(w / "覆核/成品檢查.json")
    chk["輸出成品"] = {"檔案": str(fin.relative_to(w)), "來源": rel, "處理紀錄產生時間": log["產生時間"]}
    wd.write_json(w / "覆核/成品檢查.json", chk)
    with redirect_stdout(io.StringIO()):
        _pack(w, w.parent / "存這裡")
        closing.clean(w, _plan(w)["清單指紋"], kind="mac")
    return w


def _api_paths(kind: str) -> list[str]:
    src = (REPO_ROOT / "bookclub" / "server.py").read_text(encoding="utf-8")
    body = src.split("def _route_get_api")[1].split("def _handle_audio")[0] if kind == "GET" else \
        src.split("def _route_post_api")[1].split("# 對外入口")[0]
    paths = set(re.findall(r'path == "(/api/[^"]+)"', body))
    if kind == "POST":   # path.startswith("/api/turns/") 之類的分支：裡面的 path == 都已經列到
        paths |= {"/api/profile/import"}
    return sorted(paths)


POST_OK = ("/api/close/", "/api/projects/", "/api/profile/import", "/api/copy/cancel")


def test_closed_every_post_409_every_get_no_500_files_unchanged():
    w = _closed_fresh()
    before = _digest(w)
    httpd, base = _serve(w)
    try:
        posts = _api_paths("POST")
        assert len(posts) > 40, posts
        for p in posts:
            if p.startswith(POST_OK):
                continue
            body = {"給": "目前", "用途": "片頭"} if p.startswith("/api/pick") else {}
            code, r = _call(base, p, body)
            assert code == 409 and isinstance(r, dict) and r.get("已結案") and "已結案" in r["error"], (p, code, r)
        # 給新影片的選檔照樣能用（不寫這個工作區）
        code, r = _call(base, "/api/pick/forget", {"用途": "片頭"})
        assert code == 200, r
        q = {"/api/audio": "?start=1&end=2", "/api/final/vclip": "?版本=1&start=1&end=2",
             "/api/final/clip": "?which=前&key=x", "/api/final/goto": "?src=60", "/api/browse": ""}
        gets = _api_paths("GET")
        assert len(gets) > 20, gets
        for p in gets:
            code, r = _call(base, p + q.get(p, ""))
            assert code != 500, (p, code, r)
        code, d = _call(base, "/api/final")
        assert code == 200 and d["已結案"] and d.get("卡片") is not None
        code, s = _call(base, "/api/state")
        assert code == 200 and s["已結案"] and s["結案"]["位置顯示"]
        code, a = _call(base, "/api/audio?start=1&end=2")
        assert code == 409
        code, pj = _call(base, "/api/projects")
        assert code == 200
        # 切換回同一個專案（重新打開）也不補做任何事
        code, r = _call(base, "/api/projects/switch", {"路徑": str(w)})
        assert code in (200, 403)
        for p in ("/api/review", "/api/final", "/api/execute", "/api/roomtone", "/api/names", "/api/proofread", "/api/turns"):
            assert _call(base, p)[0] != 500
        assert _digest(w) == before, set(_digest(w).items()) ^ set(before.items())
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_closed_project_start_same_video_refused():
    w = _closed_fresh()
    httpd, base = _serve(w)
    try:
        video = Path(wd.read_json(w / "分析結果.json")["video"])
        _mk(video, b"fake")
        from bookclub import server as srv

        orig = srv.project_dir_for
        srv.project_dir_for = lambda v: w
        try:
            code, r = _call(base, "/api/projects/start", {"影片": str(video)})
        finally:
            srv.project_dir_for = orig
        assert code == 409 and r.get("已結案"), (code, r)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_closed_project_row_and_mark_interrupted_skipped():
    from bookclub import server as srv

    w = _closed_fresh()
    prog = w / "生成" / "執行進度.json"
    wd.write_json(prog, {"步驟": {"組裝": {"狀態": "進行中"}}})
    before = _digest(w)
    srv._mark_interrupted(w)
    assert _digest(w) == before
    row = srv._project_row(w, None)
    assert row["已結案"] and row["已結案"]["位置顯示"] and row["已結案"]["日期"]
    assert srv._project_row(w.parent, None)["已結案"] is None


def test_final_exporting_is_counter():
    """第 2 段審查建議 (a)：輸出成品同時兩個，第一個做完不能把「在輸出」清成沒在輸出。"""
    with tempfile.TemporaryDirectory() as t:
        w = _lesson1(Path(t))
        httpd, base = _serve(w)
        try:
            from bookclub import finalcheck as fc

            gate = threading.Event()
            started = []
            orig = fc.export_final

            def slow(w_, confirm=False):
                started.append(1)
                gate.wait(5)
                return {"ok": True}

            fc.export_final = slow
            try:
                th = [threading.Thread(target=_call, args=(base, "/api/final/export", {"確定": True})) for _ in range(2)]
                for x in th:
                    x.start()
                for _ in range(100):
                    if len(started) == 2:
                        break
                    time.sleep(0.02)
                assert httpd.final_exporting == 2
                gate.set()
                for x in th:
                    x.join()
                assert httpd.final_exporting == 0
            finally:
                fc.export_final = orig
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_pack_source_vanishes_before_size_read_is_source_changed():
    """第 2 段審查建議 (b)：`_one` 讀來源大小那段在 try 裡；檔案不在當「來源變了」（PackError，不是 FileNotFoundError）。"""
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        w = _lesson1(root)
        job = closing.PackJob(w, root)
        gone = w / "生成" / "不在了.json"
        try:
            job._one(root, {"相對路徑": "紀錄/不在了.json", "來源": gone})
        except closing.PackError as e:
            assert "不在了" in str(e) or "變了" in str(e)
        else:
            raise AssertionError("應該是 PackError")


# ---------------------------------------------------------------------------
# 開發者指令、inspect
# ---------------------------------------------------------------------------

def test_cli_dry_run_restore_reopen_inspect():
    from bookclub import cli

    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        w = _lesson1(root)
        before = _digest(w)
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert cli.main(["close", "--dry-run", str(w)]) == 0
        out = buf.getvalue()
        assert "輸出/成品_v1_0-97_sw.mp4" in out and "合計" in out and "清單指紋" in out
        assert "transcript/merged.json" not in out
        assert _digest(w) == before                                    # 只看不刪
        _pack(w, root / "D槽")

        def hook(stage, rel):
            if stage == "刪":
                raise _Crash()

        try:
            closing.clean(w, _plan(w)["清單指紋"], kind="mac", hook=hook)
        except _Crash:
            pass
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert cli.main(["inspect", str(w), "結案"]) == 0
        out = buf.getvalue()
        assert "清理中" in out and "核對 通過" in out and "第一堂" not in out   # 路徑、檔名不印
        with redirect_stdout(io.StringIO()):
            assert cli.main(["close", "--restore", str(w)]) == 0
        assert {k for k in _digest(w)} == {k for k in before} | {closing.RECORD_FILE}
        with redirect_stdout(io.StringIO()):
            assert cli.main(["close", "--reopen", str(w)]) == 0
        assert not closing.record_path(w).exists()


# ---------------------------------------------------------------------------
# 畫面（node）
# ---------------------------------------------------------------------------

def _fn(src: str, name: str) -> str:
    m = re.search(rf"^(async )?function {name}\(.*?^\}}\n", src, re.S | re.M)
    assert m, name
    return m.group(0)


def test_js_close_box_texts():
    if not NODE:
        print("（沒有 node，略過）")
        return
    code = _fn(FIOJS, "clGB") + _fn(FIOJS, "clView") + """
      const base = {"暫存大小": 2.6e9, "系統": "mac", "狀態": null, "工作": null, "可以繼續": false, "含影片副本": false};
      console.log(JSON.stringify([
        clView(base),
        clView({...base, "系統": "wsl", "含影片副本": true}),
        clView({...base, "工作": {"狀態": "清理中"}}),
        clView({...base, "狀態": "已結案", "只有紀錄": true, "清理": {"省下位元組": 2.6e9, "成品存在": "D:\\\\讀書會剪輯結案\\\\第一堂_20261016"}}),
        clView({...base, "狀態": "清理中", "可以繼續清": true}),
        clView({...base, "工作": {"狀態": "已存好", "清理錯誤": "清單變了，請重新看一次。"}, "狀態": "已打包", "核對通過的": {"資料夾": "D:\\\\x"}}),
      ]));"""
    v = json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True).stdout)
    assert v[0]["line"] == "會把成品影片、逐字稿、修改紀錄存到你選的地方，然後刪掉這裡的暫存檔（共 2.6 GB）。", v[0]
    assert v[1]["line"] == "會把成品影片、逐字稿、修改紀錄存到你選的地方，然後刪掉這裡的暫存檔（共 2.6 GB，含複製進來的那支原始影片）。"
    assert v[2]["running"] and v[2]["status"] == "正在清…" and not v[2]["stop"]          # 清理開始後不給停止
    assert v[3]["status"] == "已結案，空出 2.6 GB。成品存在：D:\\讀書會剪輯結案\\第一堂_20261016" and v[3]["reveal"]
    assert v[3]["btn"] == "再存一份紀錄…"
    assert v[4]["status"] == "上次清到一半。" and v[4]["cleanResume"] and not v[4]["resume"]
    assert "清單變了" in v[5]["status"] and v[5]["cleanBtn"]


def test_js_overview_groups_closed_last():
    if not NODE:
        print("（沒有 node，略過）")
        return
    code = _fn(APPJS, "pjGroups") + """
      const list = [{"名稱": "a", "已結案": null}, {"名稱": "b", "已結案": {"日期": "10-16", "位置顯示": "D:\\\\x"}},
                    {"名稱": "c", "已結案": null}];
      console.log(JSON.stringify(pjGroups(list)));"""
    g = json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True).stdout)
    assert [p["名稱"] for p in g["進行中"]] == ["a", "c"] and [p["名稱"] for p in g["已結案"]] == ["b"]
    assert "已結案（" in APPJS and "這支影片已結案" in APPJS


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            with redirect_stdout(io.StringIO()):
                fn()
            print(f"✓ {fn.__name__}")
        except (Exception, SystemExit) as e:  # noqa: BLE001 — TDD：全部跑完、列出哪幾條沒過
            failed += 1
            print(f"✗ {fn.__name__}：{type(e).__name__}：{str(e)[:160]}")
    print(f"{len(tests) - failed}/{len(tests)} 通過")
    sys.exit(1 if failed else 0)
