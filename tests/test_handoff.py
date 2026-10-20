"""33 號設計（#257–#260）接力交接：交接打包、設定快照、匯入交接包、清理。

全部是合成資料（tests/fake_workdir.py 的假工作區、虛構的名冊與聲線檔），寫在系統暫存資料夾；不碰 ~/讀書會剪輯資料。
不跑模型、不組裝影片（音軌用假的抽音函式、長度用假的量測）。
獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_handoff.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401

import json
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import fake_workdir  # noqa: E402

_DATA = Path(tempfile.mkdtemp()) / "老師資料"
_DATA.mkdir(parents=True)
os.environ["BOOKCLUB_DATA_DIR"] = str(_DATA)
ROSTER = ("中文名,其他寫法,英文代號,聲線,性別,Email\n"
          "小美,,Amy,,女,amy@example.com\n阿明,,Tom,,男,\n大雄,胖虎,Ken,,男,\n")
(_DATA / "名冊.csv").write_text(ROSTER, encoding="utf-8")
(_DATA / "敏感詞.csv").write_text("原詞,替代詞\n這本書,某本書\n某公司名,某公司\n", encoding="utf-8")
(_DATA / "發音對照表.csv").write_text("原字,生成用,原因,建立日期\n收穫,收貨,測試,2026-10-20\n愉快,魚快,測試,2026-10-20\n",
                                    encoding="utf-8")
_VOICES = _DATA / "聲線"
_VOICES.mkdir()
for _sex in ("男", "女"):
    (_VOICES / f"{_sex}聲_暫定.wav").write_bytes(f"RIFF-{_sex}".encode())
    (_VOICES / f"{_sex}聲_暫定.txt").write_text("假的", encoding="utf-8")

from bookclub import closing, epcodes, execute, handoff, tts  # noqa: E402
from bookclub import turns as turns_mod  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

PROBE = (lambda p: fake_workdir.DUR)   # 假的量長度（不叫 ffprobe）
STALE = "覆核或生成結果比成品新，要重新組裝"   # execute.render_done 看修改時間的那一句
_BASE: Path | None = None


def _no_audio(video, target):   # 假的抽音：寫一個小檔當 audio.flac
    (Path(target) / "audio.flac").write_bytes(b"fLaC-fake")


def _teacher() -> tuple[Path, Path]:
    """老師這台：影片＋旁邊的 `假影片_剪輯工作區/`（第 3 步做完：每位學員都選了「本名未知」）。回 (工作區, 影片)。"""
    global _BASE
    if _BASE is None:
        _BASE = Path(tempfile.mkdtemp()) / "base"
        fake_workdir.make(_BASE)
    root = Path(tempfile.mkdtemp()) / "老師"
    shutil.copytree(_BASE, root)
    w = root / "假影片_剪輯工作區"
    os.rename(root / "工作區", w)
    video = root / "假影片.mp4"
    data = json.loads((w / "分析結果.json").read_text(encoding="utf-8"))
    data.update({"video": str(video), "workdir": str(w)})
    (w / "分析結果.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for person in turns_mod.page_data(w)["學員"]:
        turns_mod.set_real_name(w, person, turns_mod.UNKNOWN_REAL)
    from bookclub import review

    review.add_time(w, 10)   # 覆核過（有覆核決定.json）
    return w, video


def _download(video: Path, name: str = "夥伴") -> Path:
    """另一台（另一個資料夾）從雲端空間下載的原片。"""
    d = Path(tempfile.mkdtemp()) / name / "下載"
    d.mkdir(parents=True)
    dst = d / video.name
    shutil.copy2(video, dst)
    return dst


def _pack(w: Path, stage: str, out: Path | None = None) -> Path:
    out = out or Path(tempfile.mkdtemp())
    job = handoff.PackJob(w, out, stage)
    job.run()
    assert job.state == "已存好", (job.state, job.error)
    assert job.to_dict()["說明"] == handoff.UPLOAD_LINE.format(job.dest.name)
    assert not [p for p in out.iterdir() if p.name.startswith(".")], "暫存檔要清掉"
    return job.dest


def _import(z: Path, video: Path, **kw) -> handoff.ImportJob:
    job = handoff.ImportJob(z, video, probe=PROBE, extract_audio=_no_audio, **kw)
    job.run()
    return job


def _partner_stage5(w2: Path) -> None:
    """夥伴做完第 4 步：成品、選定的生成聲音、輸出摘要；成品比所有輸入新。"""
    prod = w2 / "輸出" / "成品_v1_0-3_sw.mp4"
    prod.parent.mkdir(parents=True, exist_ok=True)
    prod.write_bytes(os.urandom(200_000))
    gen = w2 / "生成" / "老師" / "S001_放回時間格.wav"
    gen.parent.mkdir(parents=True, exist_ok=True)
    gen.write_bytes(os.urandom(5_000))
    (w2 / "生成" / "老師" / "S001_第2次.wav").write_bytes(os.urandom(5_000))   # 沒被紀錄指到的嘗試檔：不帶
    wd.write_json(w2 / "生成" / "老師紀錄.json", {"句子": [{"id": "1", "檔案": str(gen)}]})
    from bookclub import render, roomtone

    wd.write_json(w2 / "輸出" / "輸出摘要_0-3.json", {"輸出": {"sw": {"驗證": {"通過": True}}},
                                                   "底噪挑法": roomtone.METHOD, "組裝做法版本": render.ASSEMBLE_VERSION})
    wd.write_json(w2 / "覆核" / "成品檢查.json", {"成品影片": "輸出/成品_v1_0-3_sw.mp4"})
    (w2 / "修改需求").mkdir(exist_ok=True)
    wd.write_json(w2 / "修改需求" / "卡片.json", {"列": [{"卡號": "K0001", "誰": "夥伴"}]})
    later = time.time() + 60
    os.utime(prod, (later, later))


# ---------------------------------------------------------------------------

def test_stage3_pack_contents_and_snapshot():
    w, video = _teacher()
    (w / "web快取").mkdir()
    (w / "web快取" / "片段.wav").write_bytes(b"x")
    z = _pack(w, handoff.STAGE3)
    assert z.name.startswith("假影片_交接第3步_") and z.suffix == ".zip"
    with zipfile.ZipFile(z) as zf:
        names = set(zf.namelist())
        m = json.loads(zf.read(handoff.LIST_NAME))
        roster = zf.read("工作區/交接/設定快照/名冊.csv").decode("utf-8-sig")
        sens = zf.read("工作區/交接/設定快照/敏感詞.csv").decode("utf-8-sig")
        pron = zf.read("工作區/交接/設定快照/發音對照表.csv").decode("utf-8-sig")
    assert handoff.README_NAME in names
    assert "工作區/校對/段落.json" in names and "工作區/參考音/ref.wav" in names and "工作區/transcript/merged.json" in names
    assert not any(n.endswith((".mp4", "audio.flac")) or "web快取" in n for n in names), "不帶原片、音軌、快取"
    assert m["哪一段"] == "第3步" and m["主機"] and m["工具版本"] and m["匯出時間"]
    assert m["原片"]["檔名"] == "假影片.mp4" and m["原片"]["大小"] == video.stat().st_size and m["原片"]["長度"] == fake_workdir.DUR
    assert all(set(f) >= {"路徑", "大小", "sha256", "修改時間"} for f in m["檔案"])
    assert set(m["聲線"]) == {"男聲_暫定.wav", "女聲_暫定.wav"}
    # 設定快照：只帶這一集用到的列；名冊不帶聯絡資料欄
    assert "小美" in roster and "阿明" in roster and "大雄" not in roster and "Email" not in roster and "amy@" not in roster
    assert "這本書" in sens and "某公司名" not in sens
    assert "收穫" in pron and "愉快" not in pron
    assert m["設定快照"] == {"名冊": 2, "敏感詞": 1, "發音對照表": 1}
    e = handoff.last_export(w)
    assert e and e["哪一段"] == "第3步" and e["檔名"] == z.name and "校對/段落.json" in e["紀錄指紋"]
    assert not handoff.handed_over(w), "第 3 步不算已交出去"


def test_stage3_import_new_workdir_ready_for_step4():
    w, video = _teacher()
    z = _pack(w, handoff.STAGE3)
    v2 = _download(video)
    job = _import(z, v2)
    assert job.state == "完成", (job.state, job.error, job.ask)
    w2 = Path(job.result["工作區"])
    assert w2 == v2.parent / "假影片_剪輯工作區" and job.result["新建"] and job.result["去哪一步"] == "step4"
    assert job.result["聲線不同"] == []
    ana = json.loads((w2 / "分析結果.json").read_text(encoding="utf-8"))
    assert ana["video"] == str(v2), "原片路徑換成這台的"
    assert json.loads((w2 / "transcript" / "merged.json").read_text(encoding="utf-8"))["source"] == str(v2)
    assert (w2 / "audio.flac").is_file(), "新工作區從原片重抽音軌"
    pre = execute.precheck(w2)
    assert pre["可以開始"], pre["缺"]
    # 修改時間照交接清單還原
    m = handoff.read_manifest(z)
    for f in m["檔案"]:
        if f["路徑"] not in handoff.HISTORY_FILES:
            assert (w2 / f["路徑"]).stat().st_mtime_ns == f["修改時間"], f["路徑"]
    rec = handoff.read_record(w2)
    assert rec["匯入"][-1]["來源工作區"] == str(w) and rec["匯入"][-1]["哪一段"] == "第3步"
    assert w in wd.recorded_workdirs(w2)


def test_snapshot_used_instead_of_local_roster():
    w, video = _teacher()
    z = _pack(w, handoff.STAGE3)
    job = _import(z, _download(video))
    w2 = Path(job.result["工作區"])
    assert handoff.has_snapshot(w2)
    assert handoff.setting_path(w2, "名冊.csv") == handoff.snapshot_dir(w2) / "名冊.csv"
    assert handoff.setting_path(w, "名冊.csv") == _DATA / "名冊.csv", "沒有快照的舊工作區照舊讀本機"
    saved = {n: (_DATA / n).read_bytes() for n in handoff.SETTING_FILES}
    try:
        for n in handoff.SETTING_FILES:   # 這台的名冊、敏感詞、發音對照表都沒有這一集的人和詞
            (_DATA / n).write_text((_DATA / n).read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
        words = {r["寫法"] for r in epcodes.replace_table(w2)}
        assert {"小美", "阿明", "這本書"} <= words, "第 4 步最後一道保險讀快照"
        assert "小美" not in {r["寫法"] for r in epcodes.replace_table(w)}, "沒快照的照舊讀本機"
        assert dict(tts.load_pron_table(workdir=w2)).get("收穫") == "收貨"
        assert tts.load_pron_table(workdir=w) == []
        from bookclub import students

        assert students.roster_gender("阿明", w2) == "男" and students.roster_gender("阿明", w) is None
    finally:
        for n, b in saved.items():
            (_DATA / n).write_bytes(b)


def test_stage5_import_replaces_teacher_workdir_with_backup():
    w, video = _teacher()
    _pack(w, handoff.STAGE3)                      # 段 1：老師交出去
    w2 = Path(_import(_pack(w, handoff.STAGE3), _download(video)).result["工作區"])
    _partner_stage5(w2)
    z5 = _pack(w2, handoff.STAGE5)                # 段 2：夥伴交回
    assert handoff.handed_over(w2)
    with zipfile.ZipFile(z5) as zf:
        names = set(zf.namelist())
    assert "工作區/輸出/成品_v1_0-3_sw.mp4" in names and "工作區/生成/老師/S001_放回時間格.wav" in names
    assert "工作區/生成/老師/S001_第2次.wav" not in names, "沒被紀錄指到的嘗試檔不帶"
    (w / "web快取").mkdir(exist_ok=True)
    (w / "web快取" / "舊片段.wav").write_bytes(b"x")
    job = _import(z5, video)                      # 段 3：老師匯入（交接後沒動過 → 不用確認）
    assert job.state == "完成", (job.state, job.error, job.ask)
    r = job.result
    assert Path(r["工作區"]) == w and not r["新建"] and r["去哪一步"] == "step5" and r["備份"]
    bdir = w / r["備份"]
    assert bdir.name.startswith("交接前_") and (bdir / "校對" / "段落.json").is_file() and (bdir / "參考音" / "ref.wav").is_file()
    assert json.loads((w / "修改需求" / "卡片.json").read_text(encoding="utf-8"))["列"][0]["誰"] == "夥伴"
    assert (w / "輸出" / "成品_v1_0-3_sw.mp4").is_file() and (w / "生成" / "老師" / "S001_放回時間格.wav").is_file()
    assert not (w / "web快取").exists(), "換掉的舊快取直接丟掉"
    # 修改時間還原後不會判定要重新組裝
    _done, why = execute.render_done(w, "0-3", ["sw"])
    assert why != STALE and why != "還沒組裝", why   # 名字處理計畫之類的其他判斷不在這裡測
    newest_in = max(p.stat().st_mtime for p in execute.render_inputs(w))
    assert (w / "輸出" / "成品_v1_0-3_sw.mp4").stat().st_mtime >= newest_in
    # 對方紀錄裡的完整路徑（夥伴那台的工作區）讀的時候換成這裡的
    rec = json.loads((w / "生成" / "老師紀錄.json").read_text(encoding="utf-8"))
    assert wd.localize(rec["句子"][0]["檔案"], w) == w / "生成" / "老師" / "S001_放回時間格.wav"
    # 備份結案清空間時一起清
    assert closing.classify(f"{r['備份']}/校對/段落.json") == ("清", closing.CAT_TMP)
    assert closing.classify("備份/換代號前/段落.json")[0] == "留"


def test_mtime_restore_matters():
    """不還原修改時間的話（解壓縮的時間都是現在），輸入檔比成品新，會被判定要重新組裝。"""
    w, video = _teacher()
    w2 = Path(_import(_pack(w, handoff.STAGE3), _download(video)).result["工作區"])
    _partner_stage5(w2)
    for p in (w2 / "校對" / "段落.json",):
        future = time.time() + 120   # 解壓縮那一刻比成品新
        os.utime(p, (future, future))
    assert execute.render_done(w2, "0-3", ["sw"])[1] == STALE


def test_zip_missing_or_bad_file_stops():
    w, video = _teacher()
    z = _pack(w, handoff.STAGE3)
    v2 = _download(video)
    d = Path(tempfile.mkdtemp())
    # 少一個檔
    short = d / "少一個.zip"
    with zipfile.ZipFile(z) as src, zipfile.ZipFile(short, "w") as dst:
        for n in src.namelist():
            if n != "工作區/校對/段落.json":
                dst.writestr(n, src.read(n))
    job = _import(short, v2)
    assert job.state == "失敗" and job.error == handoff.NOT_READY
    assert not (v2.parent / "假影片_剪輯工作區").exists(), "核對沒過一個檔都不建"
    # 內容不對（指紋不符）
    bad = d / "改壞.zip"
    with zipfile.ZipFile(z) as src, zipfile.ZipFile(bad, "w") as dst:
        for n in src.namelist():
            data = src.read(n)
            dst.writestr(n, data + b" " if n == "工作區/校對/段落.json" else data)
    assert _import(bad, v2).error == handoff.NOT_READY
    # 截斷（還沒下載完）
    cut = d / "沒下載完.zip"
    cut.write_bytes(z.read_bytes()[: z.stat().st_size // 2])
    assert _import(cut, v2).error == handoff.NOT_READY
    # 不是交接包
    other = d / "別的.zip"
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("a.txt", "x")
    assert _import(other, v2).error == handoff.NOT_PACKAGE
    # 路徑跳出工作區的包不收
    evil = d / "怪路徑.zip"
    with zipfile.ZipFile(z) as src, zipfile.ZipFile(evil, "w") as dst:
        m = json.loads(src.read(handoff.LIST_NAME))
        m["檔案"][0]["路徑"] = "../外面.txt"
        for n in src.namelist():
            dst.writestr(n, json.dumps(m) if n == handoff.LIST_NAME else src.read(n))
    assert _import(evil, v2).state == "失敗"


def test_wrong_video_stops():
    w, video = _teacher()
    z = _pack(w, handoff.STAGE3)
    other = Path(tempfile.mkdtemp()) / "假影片.mp4"
    other.write_bytes(b"not the same video")
    job = _import(z, other)
    assert job.state == "失敗" and "不是同一支" in job.error and "假影片.mp4" in job.error
    v2 = _download(video)
    job = handoff.ImportJob(z, v2, probe=lambda p: fake_workdir.DUR + 30, extract_audio=_no_audio)
    job.run()
    assert job.state == "失敗" and "長度差" in job.error


def test_voice_difference_only_warns():
    w, video = _teacher()
    z = _pack(w, handoff.STAGE3)
    p = _VOICES / "女聲_暫定.wav"
    old = p.read_bytes()
    p.write_bytes(b"RIFF-different")
    try:
        job = _import(z, _download(video))
    finally:
        p.write_bytes(old)
    assert job.state == "完成"
    assert job.result["聲線不同"] == ["女聲_暫定.wav"] and any("聲線" in x for x in job.result["提醒"])


def test_teacher_changed_after_export_is_listed():
    w, video = _teacher()
    _pack(w, handoff.STAGE3)
    w2 = Path(_import(_pack(w, handoff.STAGE3), _download(video)).result["工作區"])
    _partner_stage5(w2)
    z5 = _pack(w2, handoff.STAGE5)
    # 只累加覆核計時：不算動過
    dec = w / "覆核" / "覆核決定.json"
    assert dec.is_file()
    data = json.loads(dec.read_text(encoding="utf-8"))
    data["覆核秒數"] = float(data.get("覆核秒數") or 0) + 30
    wd.write_json(dec, data)
    assert handoff.changes_after_export(w) == []
    # 真的改了第 3 步的檔 → 列出來、問要不要繼續
    seg = w / "校對" / "段落.json"
    sdata = json.loads(seg.read_text(encoding="utf-8"))
    sdata["段落"][0]["校對稿"] = "老師後來又改了這一句。"
    wd.write_json(seg, sdata)
    job = _import(z5, video)
    assert job.state == "要確認" and "校對/段落.json" in job.ask["改過"] and not job.ask["沒有交接紀錄"]
    assert json.loads(seg.read_text(encoding="utf-8"))["段落"][0]["校對稿"] == "老師後來又改了這一句。", "還沒確認不能動"
    job = _import(z5, video, confirm=True)
    assert job.state == "完成" and job.result["備份"]
    kept = json.loads((w / job.result["備份"] / "校對" / "段落.json").read_text(encoding="utf-8"))
    assert kept["段落"][0]["校對稿"] == "老師後來又改了這一句。", "老師改的那一份在備份裡"


def test_existing_without_record_asks():
    w, video = _teacher()
    z = _pack(w, handoff.STAGE3)
    w2 = Path(_import(z, _download(video)).result["工作區"])
    # 夥伴那台已經有這一集、沒交接打包過，又匯入一次 → 要確認（沒有交接紀錄）
    v2 = Path(json.loads((w2 / "分析結果.json").read_text(encoding="utf-8"))["video"])
    job = _import(z, v2)
    assert job.state == "要確認" and job.ask["沒有交接紀錄"]


def _wsl_setup(w: Path, video: Path) -> Path:
    """WSL 情境：原片是工具複製進 `<資料>/影片/` 的那一份，分析結果記它。"""
    imp = _DATA / "影片"
    imp.mkdir(exist_ok=True)
    copy = imp / f"{w.parent.name}_{video.name}"
    shutil.copy2(video, copy)
    data = json.loads((w / "分析結果.json").read_text(encoding="utf-8"))
    data["video"] = str(copy)
    (w / "分析結果.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return copy


def test_clean_after_stage3_pack():
    w, video = _teacher()
    (w / "名字候選").mkdir(exist_ok=True)
    (w / "名字候選" / "001.wav").write_bytes(b"clip")
    try:
        handoff.clean_copy(w, "x", kind="mac", data=_DATA)
        raise AssertionError("還沒交接打包不能清")
    except handoff.HandoffError:
        pass
    _pack(w, handoff.STAGE3)
    # Mac：原片是原檔，只清暫存
    plan = handoff.copy_clean_plan(w, kind="mac", data=_DATA)
    rels = {r["相對路徑"] for r in plan["檔案"]}
    assert "audio.flac" in rels and "名字候選/001.wav" in rels and not plan["含原片複本"]
    assert not any(r.startswith("參考音/") for r in rels), "參考音不清"
    try:
        handoff.clean_copy(w, "不對的指紋", kind="mac", data=_DATA)
        raise AssertionError("指紋不同要擋")
    except handoff.HandoffError:
        pass
    r = handoff.clean_copy(w, plan["清單指紋"], kind="mac", data=_DATA)
    assert r["省下位元組"] > 0 and not r["原片複本"]
    assert video.is_file(), "Mac 上的原片絕對不刪"
    assert not (w / "audio.flac").exists() and (w / "參考音" / "ref.wav").is_file() and (w / "校對" / "段落.json").is_file()
    # WSL：工具複製進 Ubuntu 的原片複本，沒有別的工作區在用 → 一起清
    w3, v3 = _teacher()
    copy = _wsl_setup(w3, v3)
    _pack(w3, handoff.STAGE3)
    plan = handoff.copy_clean_plan(w3, kind="wsl", data=_DATA)
    assert plan["含原片複本"]
    r = handoff.clean_copy(w3, plan["清單指紋"], kind="wsl", data=_DATA)
    assert r["原片複本"] and not copy.exists() and v3.is_file()


def test_clean_copy_keeps_shared_video():
    w, video = _teacher()
    copy = _wsl_setup(w, video)
    other, _v = _teacher()   # 另一個（還沒結案的）專案也用這一份
    data = json.loads((other / "分析結果.json").read_text(encoding="utf-8"))
    data["video"] = str(copy)
    (other / "分析結果.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    wd.write_json(_DATA / "專案清單.json", {"專案": [str(w), str(other)]})
    try:
        _pack(w, handoff.STAGE3)
        plan = handoff.copy_clean_plan(w, kind="wsl", data=_DATA)
        assert not plan["含原片複本"]
        handoff.clean_copy(w, plan["清單指紋"], kind="wsl", data=_DATA)
        assert copy.is_file()
    finally:
        (_DATA / "專案清單.json").unlink()


def test_clean_episode_after_handover():
    w, video = _teacher()
    w2 = Path(_import(_pack(w, handoff.STAGE3), _download(video)).result["工作區"])
    copy = _wsl_setup(w2, video)
    _partner_stage5(w2)
    try:
        handoff.clean_episode(w2, kind="wsl", data=_DATA)
        raise AssertionError("還沒交出去不能整集清")
    except handoff.HandoffError:
        pass
    out = Path(tempfile.mkdtemp())
    z5 = _pack(w2, handoff.STAGE5, out)
    info = handoff.episode_clean_info(w2, kind="wsl", data=_DATA)
    assert info and info["含原片複本"] and info["大小"] > 0
    # 交出去之後又改過 → 不清
    seg = w2 / "校對" / "段落.json"
    orig = seg.read_bytes()
    seg.write_bytes(orig + b" ")
    try:
        handoff.clean_episode(w2, kind="wsl", data=_DATA)
        raise AssertionError("改過要擋")
    except handoff.HandoffError as e:
        assert "又改過" in str(e)
    seg.write_bytes(orig)
    # 交接包搬走了 → 不清
    moved = z5.with_name("搬走.zip")
    os.rename(z5, moved)
    try:
        handoff.clean_episode(w2, kind="wsl", data=_DATA)
        raise AssertionError("找不到交接包要擋")
    except handoff.HandoffError as e:
        assert "找不到" in str(e)
    os.rename(moved, z5)
    r = handoff.clean_episode(w2, kind="wsl", data=_DATA)
    assert r["省下位元組"] > 0 and not w2.exists() and not copy.exists() and video.is_file()


def test_clean_episode_mac_keeps_video():
    w, video = _teacher()
    v2 = _download(video)
    w2 = Path(_import(_pack(w, handoff.STAGE3), v2).result["工作區"])
    _partner_stage5(w2)
    _pack(w2, handoff.STAGE5)
    handoff.clean_episode(w2, kind="mac", data=_DATA)
    assert not w2.exists() and v2.is_file(), "Mac 上的原片不碰"


def test_stage5_needs_product_and_pack_cancel():
    w, _video = _teacher()
    try:
        handoff.pack_items(w, handoff.STAGE5)
        raise AssertionError("沒有成品不能第 5 步交接打包")
    except handoff.HandoffError as e:
        assert "第 4 步" in str(e)
    out = Path(tempfile.mkdtemp())
    job = handoff.PackJob(w, out, handoff.STAGE3)
    job.cancel()
    job.run()
    assert job.state == "停止" and not list(out.iterdir())
    # 存過去壞掉 → 失敗、刪掉自己存的那一份
    job = handoff.PackJob(w, out, handoff.STAGE3, fail_hook=lambda p: p.write_bytes(p.read_bytes()[:-50]))
    job.run()
    assert job.state == "失敗" and not list(out.iterdir()) and handoff.last_export(w) is None
    try:
        handoff.check_location(w, w / "校對", 1)
        raise AssertionError("不能存在工作資料夾裡")
    except handoff.HandoffError:
        pass


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        fn()
        print(f"✓ {name}")
    print(f"全部 {len(tests)} 個通過")
