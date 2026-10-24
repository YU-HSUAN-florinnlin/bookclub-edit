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
    assert m["哪一段"] == "第3步" and m["電腦"] and m["工具版本"] and m["匯出時間"]
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
    assert job.state == "要確認" and job.ask["重點"] == "你交接之後，這台又改過這一集" \
        and job.ask["改過類別"] == ["第 3 步的段落與校對稿"] and job.ask["工作區名稱"] == w.name
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
    assert job.state == "要確認" and job.ask["重點"] == "這個交接包已經匯入過了" and job.ask["建議不匯入"]


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
    # 宇軒 10-20：不再看 zip 還在不在（改由確認視窗請人確認已上傳）
    os.rename(z5, z5.with_name("搬走.zip"))
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


# ---------------------------------------------------------------------------
# 1020-2 審查修正
# ---------------------------------------------------------------------------

def test_m1_change_during_pack_is_a_change():
    """打包寫完 zip 後、記錄之前改了卡片：交接時的樣子用包裡的內容，所以算「又改過」，清掉這集擋下。"""
    w, video = _teacher()
    w2 = Path(_import(_pack(w, handoff.STAGE3), _download(video)).result["工作區"])
    _partner_stage5(w2)
    out = Path(tempfile.mkdtemp())

    def touch(_z):
        wd.write_json(w2 / "修改需求" / "卡片.json", {"列": [{"卡號": "K0001", "誰": "打包中又改"}]})
    job = handoff.PackJob(w2, out, handoff.STAGE5, fail_hook=touch)
    job.run()
    assert job.state == "已存好"
    assert handoff.changes_after_export(w2) == ["修改需求/卡片.json"]
    try:
        handoff.clean_episode(w2, kind="mac", data=_DATA)
        raise AssertionError("改過要擋")
    except handoff.HandoffError as e:
        assert "又改過" in str(e)
    assert w2.is_dir()


def test_m2_server_written_files_are_not_changes():
    """打包後打開第 3、5 步、看一段影片（伺服器自己寫的檔）不算動過；只比人做的判斷。"""
    w, video = _teacher()
    w2 = Path(_import(_pack(w, handoff.STAGE3), _download(video)).result["工作區"])
    _partner_stage5(w2)
    _pack(w2, handoff.STAGE5)
    wd.write_json(w2 / "修改需求" / "格式.json", {"同步": {"檔案": {"覆核/成品檢查.json": [1, 2]}}})
    wd.write_json(w2 / "修改需求" / "看過.json", {"列": [{"版本號": 1, "看過區段": [[0, 5]]}]})
    (w2 / "修改需求" / "歷史.jsonl").write_text('{"序號": 1}\n', encoding="utf-8")
    chk = json.loads((w2 / "覆核" / "成品檢查.json").read_text(encoding="utf-8"))
    chk.update({"看過區段": [[0, 5]], "成品長度": 180.0, "成品檔指紋": [1, 2]})
    wd.write_json(w2 / "覆核" / "成品檢查.json", chk)
    assert handoff.changes_after_export(w2) == []
    assert handoff.episode_clean_check(w2, kind="mac", data=_DATA) is None
    chk["逐筆"] = {"x": {"結果": "退回"}}   # 人按的退回：算改過
    wd.write_json(w2 / "覆核" / "成品檢查.json", chk)
    assert handoff.changes_after_export(w2) == ["覆核/成品檢查.json"]
    # 老師段 1 交出去時還沒有成品檢查，之後只打開第 5 步（建了空的）：不算改過
    t, _v = _teacher()
    _pack(t, handoff.STAGE3)
    wd.write_json(t / "覆核" / "成品檢查.json", {"版本": 1, "逐筆": {}, "整片退回": [], "看過區段": []})
    assert handoff.changes_after_export(t) == []


def test_m3_teacher_downloaded_video_elsewhere_still_replaces_original():
    """老師段 3 選的是「下載」裡的原片（不在原本的位置）：照交接編號找到老師原本那份工作區，換掉＋備份。"""
    w, video = _teacher()
    z3 = _pack(w, handoff.STAGE3)
    w2 = Path(_import(z3, _download(video)).result["工作區"])
    _partner_stage5(w2)
    z5 = _pack(w2, handoff.STAGE5)
    elsewhere = _download(video, "老師下載")
    seg = w / "校對" / "段落.json"
    data = json.loads(seg.read_text(encoding="utf-8"))
    data["段落"][0]["校對稿"] = "交出去之後老師又改了。"
    wd.write_json(seg, data)
    cands = [w, w2]   # 同一台演練：兩份都在專案清單上
    job = _import(z5, elsewhere, candidates=cands)
    assert job.state == "要確認" and Path(job.ask["工作區"]) == w and "第 3 步的段落與校對稿" in job.ask["改過類別"]
    job = _import(z5, elsewhere, confirm=True, candidates=cands)
    assert job.state == "完成" and Path(job.result["工作區"]) == w and job.result["備份"]
    ana = json.loads((w / "分析結果.json").read_text(encoding="utf-8"))
    assert ana["video"] == str(elsewhere)
    assert not (elsewhere.parent / "假影片_剪輯工作區").exists(), "不另建一份"
    # 夥伴那台（清單上沒有老師那份）匯入第 3 步包：照原片旁邊建新的，工作區 ID 跟著包
    v3 = _download(video, "夥伴二")
    job = _import(z3, v3, candidates=[])
    assert job.state == "完成" and Path(job.result["工作區"]) == v3.parent / "假影片_剪輯工作區"
    assert handoff.read_record(Path(job.result["工作區"]))["工作區ID"] == handoff.read_record(w)["工作區ID"]


def test_m4_last_safety_net_uses_snapshot_and_local():
    w, video = _teacher()
    w2 = Path(_import(_pack(w, handoff.STAGE3), _download(video)).result["工作區"])
    assert "大雄" not in (handoff.snapshot_dir(w2) / "名冊.csv").read_text(encoding="utf-8-sig")
    words = {r["寫法"] for r in epcodes.replace_table(w2)}
    assert {"大雄", "胖虎", "小美", "某公司名", "這本書"} <= words, "快照＋本機合起來"
    from bookclub import students

    assert students.roster_gender("大雄", w2) == "男", "快照沒有的人查本機"
    saved = (_DATA / "名冊.csv").read_bytes()
    try:
        (_DATA / "名冊.csv").write_text("中文名,其他寫法,英文代號,聲線,性別\n", encoding="utf-8")
        assert "小美" in {r["寫法"] for r in epcodes.replace_table(w2)}, "本機沒有的，快照照樣有"
    finally:
        (_DATA / "名冊.csv").write_bytes(saved)


def test_s1_failure_midway_restores_teacher_workdir():
    w, video = _teacher()
    w2 = Path(_import(_pack(w, handoff.STAGE3), _download(video)).result["工作區"])
    _partner_stage5(w2)
    z5 = _pack(w2, handoff.STAGE5)
    before = {p.relative_to(w).as_posix(): p.read_bytes() for p in w.rglob("*") if p.is_file()}
    orig = handoff._move_in
    n = {"k": 0}

    def flaky(src, dst, root):
        n["k"] += 1
        if n["k"] == len(before) + 5:
            raise OSError(28, "No space left on device")
        return orig(src, dst, root)
    handoff._move_in = flaky
    try:
        job = _import(z5, video)
    finally:
        handoff._move_in = orig
    assert job.state == "失敗" and "空間不夠" in job.error
    after = {p.relative_to(w).as_posix(): p.read_bytes() for p in w.rglob("*") if p.is_file()}
    assert after == before, sorted(set(after) ^ set(before))
    assert not handoff.has_snapshot(w)


def test_s4_space_checked_before_import():
    w, video = _teacher()
    z = _pack(w, handoff.STAGE3)
    m = handoff.read_manifest(z)
    from collections import namedtuple

    U = namedtuple("U", "total used free")
    try:
        handoff.space_check(Path(tempfile.mkdtemp()) / "新的", m, usage=lambda p: U(10, 10, 1000))
        raise AssertionError("空間不夠要擋")
    except handoff.HandoffError as e:
        assert "空間不夠" in str(e) and "GB" in str(e)


def test_s7_symlinked_subfolder_is_not_written_through():
    w, video = _teacher()
    z = _pack(w, handoff.STAGE3)
    v2 = _download(video)
    target = v2.parent / "假影片_剪輯工作區"
    outside = Path(tempfile.mkdtemp()) / "外面"
    outside.mkdir()
    target.mkdir()
    os.symlink(outside, target / "校對")
    job = _import(z, v2)
    assert job.state == "失敗" and "捷徑" in job.error
    assert not list(outside.iterdir()), "不能寫到工作區外"


def test_s3_teacher_own_workdir_has_no_clean_episode():
    w, video = _teacher()
    w2 = Path(_import(_pack(w, handoff.STAGE3), _download(video)).result["工作區"])
    _partner_stage5(w2)
    _pack(w2, handoff.STAGE5)
    assert handoff.episode_clean_info(w2, kind="mac", data=_DATA)
    # 老師自己（第 1 步建的）按了第 5 步交接打包：不出現「清掉這集」、也清不掉
    _partner_stage5(w)
    _pack(w, handoff.STAGE5)
    assert handoff.handed_over(w) and handoff.episode_clean_info(w, kind="mac", data=_DATA) is None
    try:
        handoff.clean_episode(w, kind="mac", data=_DATA)
        raise AssertionError("老師自己的工作區不能整集清")
    except handoff.HandoffError as e:
        assert "從頭做的" in str(e)
    assert w.is_dir()


def test_machine_label_stable_and_no_hostname():
    import socket

    a, b = handoff.machine_label(), handoff.machine_label()
    assert a == b and a.split("・")[0] in ("Mac", "Windows", "Linux") and len(a.split("・")[1]) == 4
    w, _v = _teacher()
    z = _pack(w, handoff.STAGE3)
    raw = zipfile.ZipFile(z).read(handoff.LIST_NAME).decode("utf-8")
    assert socket.gethostname() not in raw and json.loads(raw)["電腦"] == a


def _open5(w: Path) -> None:
    """不開伺服器，照 GET /api/final 的做法打開第 5 步（審查重現腳本同一套）。"""
    import types

    from bookclub import server as srv

    fake = types.SimpleNamespace(workdir=w, video=None, exec_running=lambda: False)
    page, snap = srv._final_step3(fake)
    srv._needs_ensure(fake, page, snap)
    try:
        srv._final_data(fake, page)
    except Exception:  # noqa: BLE001 — 假資料的成品量不到長度之類的，不影響這裡要看的「有沒有改檔」
        pass


def test_m4_roster_changes_on_teacher_machine():
    """宇軒 10-20：老師處理後面幾集時本機名冊會變。①匯入不看名冊內容 ②匯回後只打開第 5 步不重排、不判要重新生成
    ③合起來的名單只會多換，不會讓檢查丟錯。"""
    from bookclub import needs, nameplan

    saved = (_DATA / "名冊.csv").read_bytes()
    try:
        w, video = _teacher()
        z3 = _pack(w, handoff.STAGE3)
        # ① 夥伴那台名冊跟快照不同（少了阿明、多了別人）：匯入照常成功、不提醒名冊
        (_DATA / "名冊.csv").write_text("中文名,其他寫法,英文代號,聲線,性別\n小美,,Amy,,女\n路人甲,,Joe,,男\n", encoding="utf-8")
        job = _import(z3, _download(video))
        assert job.state == "完成" and not any("名冊" in x for x in job.result["提醒"])
        w2 = Path(job.result["工作區"])
        _partner_stage5(w2)
        wd.write_json(nameplan.plan_path(w2), nameplan.compute_plan(w2))   # 夥伴第 4 步排好的計畫
        _open5(w2)   # 夥伴打開過第 5 步（修改需求紀錄已升級，一般流程）
        z5 = _pack(w2, handoff.STAGE5)
        # 老師這台的名冊後來變大（處理後面幾集時加了人，其中一個寫法剛好出現在這一集老師講的話裡）
        (_DATA / "名冊.csv").write_text(saved.decode("utf-8") + "書本,這本書,Book,,男\n", encoding="utf-8")
        job = _import(z5, video)
        assert job.state == "完成" and not any("名冊" in x for x in job.result["提醒"])
        # ② 只打開第 5 步：名字處理計畫、生成紀錄、覆核決定都不重寫；要重做的清單不因名冊變大而多
        watch = [nameplan.plan_path(w), w / "生成" / "老師紀錄.json", w / "覆核" / "覆核決定.json", w / "校對" / "段落.json",
                 w / "修改需求" / "卡片.json"]
        before = {p: (p.read_bytes() if p.is_file() else None) for p in watch}
        redo0 = needs.redo_list(w) if needs.exists(w) else None
        _open5(w)
        _open5(w)
        after = {p: (p.read_bytes() if p.is_file() else None) for p in watch}
        assert after == before, [str(p.relative_to(w)) for p in watch if after[p] != before[p]]
        assert (needs.redo_list(w) if needs.exists(w) else None) == redo0
        # ③ 重跑第 4 步時才用「快照＋本機」：多換一個名字，不會丟錯
        plan = nameplan.compute_plan(w)
        assert "書本" in {r["canonical"] for r in epcodes.replace_table(w)}
        assert plan is not None
    finally:
        (_DATA / "名冊.csv").write_bytes(saved)


# ---------------------------------------------------------------------------
# 1020-2 二審修正
# ---------------------------------------------------------------------------

def _edit_seg(w: Path, text: str) -> None:
    seg = w / "校對" / "段落.json"
    data = json.loads(seg.read_text(encoding="utf-8"))
    data["段落"][0]["校對稿"] = text
    wd.write_json(seg, data)


def _seg0(w: Path) -> str:
    return json.loads((w / "校對" / "段落.json").read_text(encoding="utf-8"))["段落"][0]["校對稿"]


def test_n1_package_from_older_export_asks():
    """老師交接兩次（A1、A2），夥伴照較舊的 A1 做完交回：要確認、說明這包是比較舊的那次；比對基準是 A1。"""
    w, video = _teacher()
    a1 = _pack(w, handoff.STAGE3)
    _edit_seg(w, "A1 之後老師改的。")
    _pack(w, handoff.STAGE3)   # A2
    w2 = Path(_import(a1, _download(video)).result["工作區"])
    _partner_stage5(w2)
    z5 = _pack(w2, handoff.STAGE5)
    job = _import(z5, video, candidates=[w])
    assert job.state == "要確認" and job.ask["重點"] == "這包是照你比較早交出去的那一份做的", job.ask
    assert "第 3 步的段落與校對稿" in job.ask["改過類別"], "跟 A1 比：A1 之後改的那句要列出來"
    assert _seg0(w) == "A1 之後老師改的。", "還沒確認不能動"
    job = _import(z5, video, confirm=True, candidates=[w])
    assert job.state == "完成"
    assert json.loads((w / job.result["備份"] / "校對" / "段落.json").read_text(encoding="utf-8"))["段落"][0]["校對稿"] \
        == "A1 之後老師改的。"
    # 照最新那包（A2）做的、之後沒動：不用問
    w3, v3 = _teacher()
    b1 = _pack(w3, handoff.STAGE3)
    w4 = Path(_import(b1, _download(v3)).result["工作區"])
    _partner_stage5(w4)
    assert _import(_pack(w4, handoff.STAGE5), v3, candidates=[w3]).state == "完成"


def test_n2_older_than_last_import_says_so():
    w, video = _teacher()
    w2 = Path(_import(_pack(w, handoff.STAGE3), _download(video)).result["工作區"])
    _partner_stage5(w2)
    b1 = _pack(w2, handoff.STAGE5)
    time.sleep(1.1)
    _edit_seg(w2, "夥伴第二包。")
    b2 = _pack(w2, handoff.STAGE5)
    assert _import(b2, video, candidates=[w]).state == "完成"
    job = _import(b1, video, candidates=[w])
    assert job.state == "要確認" and job.ask["重點"] == "這是比較舊的交接包" and job.ask["建議不匯入"], job.ask
    assert "已經匯入過比較新的版本" in job.ask["說明"] and job.ask["改過類別"] == []


def test_n3_original_not_found_asks_before_creating():
    w, video = _teacher()
    w2 = Path(_import(_pack(w, handoff.STAGE3), _download(video)).result["工作區"])
    _partner_stage5(w2)
    z5 = _pack(w2, handoff.STAGE5)
    elsewhere = _download(video, "老師下載")
    job = _import(z5, elsewhere, candidates=[])      # 專案清單裡找不到老師那一份
    assert job.state == "要確認" and job.ask["可以另建"] and job.ask["可以指定"]
    assert not (elsewhere.parent / "假影片_剪輯工作區").exists(), "沒問之前不另建"
    job = _import(z5, elsewhere, confirm=True, target=w)   # 人指定原本那一份
    assert job.state == "完成" and Path(job.result["工作區"]) == w and job.result["備份"]


def test_n5_two_copies_let_user_choose():
    w, video = _teacher()
    w2 = Path(_import(_pack(w, handoff.STAGE3), _download(video)).result["工作區"])
    _partner_stage5(w2)
    z5 = _pack(w2, handoff.STAGE5)
    copy = w.parent.parent / "副本" / w.name
    shutil.copytree(w, copy)
    job = _import(z5, video, candidates=[copy, w])
    assert job.state == "要確認" and job.ask["選項"] == [str(copy), str(w)] and job.ask["工作區名稱"] == w.name
    job = _import(z5, video, confirm=True, candidates=[copy, w], target=w)
    assert job.state == "完成" and Path(job.result["工作區"]) == w
    assert not (copy / handoff.BACKUP_DIR).exists(), "沒選的那一份不動"


def test_n6_same_machine_original_video_refused():
    w, video = _teacher()
    z = _pack(w, handoff.STAGE3)
    job = _import(z, video, candidates=[])   # 清單上沒有、但原片旁邊就是交出它的那份
    assert job.state == "失敗" and job.error == "這個交接包是這台交出去的，不用在這台匯入。"


# ---------------------------------------------------------------------------
# 1020-2 三審修正
# ---------------------------------------------------------------------------

def test_reimport_after_import_no_false_alarm():
    """D3：匯回第 5 步包之後，再匯入同一包只說「已經匯入過了」；夥伴做的內容不算老師改的。"""
    w, video = _teacher()
    w2 = Path(_import(_pack(w, handoff.STAGE3), _download(video)).result["工作區"])
    _partner_stage5(w2)
    z5 = _pack(w2, handoff.STAGE5)
    dl = _download(video, "老師下載")
    assert _import(z5, dl, candidates=[w]).state == "完成"
    job = _import(z5, dl, candidates=[w])
    assert job.state == "要確認" and job.ask["重點"] == "這個交接包已經匯入過了" and job.ask["改過類別"] == []
    # 夥伴再交一包（比較新），老師這邊匯入後沒動 → 不用問
    time.sleep(1.1)
    _edit_seg(w2, "夥伴又改了一句。")
    z6 = _pack(w2, handoff.STAGE5)
    j6 = _import(z6, dl, candidates=[w])
    assert j6.state == "完成", (j6.state, j6.ask, j6.error)
    # 匯入之後老師自己改 → 才算改過
    _edit_seg(w, "老師匯入後自己改的。")
    time.sleep(1.1)
    _edit_seg(w2, "夥伴第三包。")
    z7 = _pack(w2, handoff.STAGE5)
    job = _import(z7, dl, candidates=[w])
    assert job.state == "要確認" and job.ask["重點"] == "你交接之後，這台又改過這一集"


def test_auto_codes_file_not_compared():
    w, video = _teacher()
    _pack(w, handoff.STAGE3)
    wd.write_json(w / "校對" / "代號自動配.json", {"開始前③已配": True, "自動配": {}})
    assert handoff.changes_after_export(w) == []


def test_own_stage3_package_on_same_machine():
    """三審 4a：老師在交出它的同一台匯入自己的第 3 步包（原片選下載的那份）→ 停下來說不用匯入，不另建。"""
    w, video = _teacher()
    z = _pack(w, handoff.STAGE3)
    dl = _download(video, "老師下載")
    job = _import(z, dl, candidates=[w])
    assert job.state == "要確認" and job.ask["重點"] == "這是這台自己交出去的交接包" and job.ask["只能不匯入"]
    assert not (dl.parent / "假影片_剪輯工作區").exists()


def test_case_insensitive_paths_match():
    """三審 4b：Mac 不分大小寫，系統選檔回來的路徑大小寫跟專案清單不同也算同一個。"""
    base = Path(tempfile.mkdtemp()) / "Case"
    d = base / "AbC_剪輯工作區"
    d.mkdir(parents=True)
    upper = base / "ABC_剪輯工作區"
    if not upper.exists():
        print("（這個檔案系統分大小寫，跳過）")
        return
    assert wd.same_path(d, upper) and not wd.same_path(d, base / "別的")
    from bookclub import server as srv

    saved = srv.registry_path().read_bytes() if srv.registry_path().is_file() else None
    try:
        srv.register_project(d)
        srv.register_project(upper)
        assert [str(x) for x in srv.registered_projects()].count(str(d)) == 1
        assert str(upper) not in [str(x) for x in srv.registered_projects()]
        assert srv.known_project(upper)
    finally:
        if saved is None:
            srv.registry_path().unlink(missing_ok=True)
        else:
            srv.registry_path().write_bytes(saved)
    # 交接：選的原片路徑大小寫跟清單不同，原片旁邊就是清單上那一份 → 用清單記的寫法
    w, video = _teacher()
    z = _pack(w, handoff.STAGE3)
    v2 = _download(video, "Partner")
    shouted = Path(str(v2.parent).replace("Partner", "PARTNER")) / v2.name
    assert shouted.exists()
    j1 = _import(z, v2, candidates=[])
    w2 = Path(j1.result["工作區"])
    _partner_stage5(w2)
    time.sleep(1.1)
    z5 = _pack(w2, handoff.STAGE5)
    targets, how = handoff.find_targets(handoff.read_manifest(z5), shouted, [w2])
    assert targets == [w2] and how == "原片旁邊"


def test_change_kinds_plain_words():
    assert handoff.change_kinds(["校對/段落.json", "生成/老師紀錄.json（新的）", "修改需求/卡片.json",
                                 "覆核/成品檢查.json"]) == ["第 3 步的段落與校對稿", "第 4 步的 AI 修改紀錄",
                                                         "第 5 步的通過／退回", "第 5 步的修改需求卡片"]


def test_summary_shows_only_system():
    w, _v = _teacher()
    z = _pack(w, handoff.STAGE3)
    s = handoff.summary(handoff.read_manifest(z))
    assert s["電腦"] in ("Mac", "Windows", "Linux")


def test_t1_typed_location_of_another_episode_refused():
    """總審 T1：「找不到原本那份」時打了另一集的位置 → 停，不換掉那一集。"""
    w, video = _teacher()
    w2 = Path(_import(_pack(w, handoff.STAGE3), _download(video)).result["工作區"])
    _partner_stage5(w2)
    z5 = _pack(w2, handoff.STAGE5)
    other, _ov = _teacher()
    _pack(other, handoff.STAGE3)   # 另一集（另一個工作區 ID）
    before = (other / "校對" / "段落.json").read_bytes()
    elsewhere = _download(video, "老師下載")
    job = _import(z5, elsewhere, target=other)
    assert job.state == "失敗" and "不是同一集的工作區" in job.error
    job = _import(z5, elsewhere, confirm=True, target=other)   # 帶了確認也一樣擋
    assert job.state == "失敗" and (other / "校對" / "段落.json").read_bytes() == before
    assert not (other / handoff.BACKUP_DIR).exists()


def test_t2_chosen_copy_still_compared():
    """總審 T2：好幾份讓人選，選了交接後改過的那份 → 先再問一次（交接之後又改過），確認才換。"""
    w, video = _teacher()
    w2 = Path(_import(_pack(w, handoff.STAGE3), _download(video)).result["工作區"])
    _partner_stage5(w2)
    z5 = _pack(w2, handoff.STAGE5)
    copy = w.parent.parent / "副本" / w.name
    shutil.copytree(w, copy)
    _edit_seg(w, "交出去之後老師又改了。")
    job = _import(z5, video, candidates=[copy, w])
    assert job.state == "要確認" and job.ask["選項"]
    job = _import(z5, video, candidates=[copy, w], target=w)   # 選了 w（不帶確認）
    assert job.state == "要確認" and job.ask["重點"] == "你交接之後，這台又改過這一集" and job.ask["已指定"]
    assert _seg0(w) == "交出去之後老師又改了。"
    job = _import(z5, video, confirm=True, candidates=[copy, w], target=w)
    assert job.state == "完成" and Path(job.result["工作區"]) == w


def test_user_messages_no_personal_name():
    """宇軒 10-23：給使用者看的錯誤訊息不寫「宇軒」，叫人回報的一律「請將錯誤訊息通知開發者。」。"""
    import ast

    tree = ast.parse((REPO_ROOT / "bookclub" / "handoff.py").read_text(encoding="utf-8"))
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.Module)) and n.body
            and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    strs = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]
    assert not [x for x in strs if "宇軒" in x], [x for x in strs if "宇軒" in x]
    assert not [x for x in strs if "終端機最後幾行" in x or "找開發者" in x or "給開發者" in x]
    assert sum("請將錯誤訊息通知開發者。" in x for x in strs) >= 6


def test_error_kinds_unexpected_vs_fixable():
    """10-23（宇軒）：使用者自己可以改正的（選錯原片、不是交接包）不算意外；zip 壞掉、空間不夠、捷徑算意外（跳錯誤視窗）。"""
    w, video = _teacher()
    z = _pack(w, handoff.STAGE3)
    other = Path(tempfile.mkdtemp()) / "假影片.mp4"
    other.write_bytes(b"not the same video")
    j = _import(z, other)
    assert j.state == "失敗" and not j.unexpected and j.to_dict()["意外"] is False
    cut = Path(tempfile.mkdtemp()) / "沒下載完.zip"
    cut.write_bytes(z.read_bytes()[: z.stat().st_size // 2])
    j = _import(cut, _download(video))
    assert j.state == "失敗" and j.unexpected and j.error == handoff.NOT_READY
    try:
        handoff.space_check(Path(tempfile.mkdtemp()), handoff.read_manifest(z),
                            usage=lambda p: __import__("collections").namedtuple("U", "total used free")(10, 10, 100))
        raise AssertionError("空間不夠要擋")
    except handoff.HandoffProblem:
        pass
    job = handoff.PackJob(w, Path(tempfile.mkdtemp()), handoff.STAGE3, fail_hook=lambda p: p.write_bytes(b"x"))
    job.run()
    assert job.state == "失敗" and job.to_dict()["意外"] is True


def _open3(w: Path) -> None:
    """不開伺服器，照第 3 步網頁會叫的 API 打開第 3 步（會寫代號表紀錄、名字候選、學員名字候選⋯）。"""
    from bookclub import proofread, review
    from bookclub.server import build_names

    turns_mod.page_data(w)
    review.page_data(w)
    build_names(w)
    proofread.page_data(w)


def _zip_texts(z: Path) -> dict[str, str]:
    """包裡每個檔的內容（文字；JSON 的 \\u 轉義也展開），掃名字用。"""
    out = {}
    with zipfile.ZipFile(z) as zf:
        for n in zf.namelist():
            t = zf.read(n).decode("utf-8", errors="ignore")
            try:
                t += "\n" + json.dumps(json.loads(t), ensure_ascii=False)
            except ValueError:
                pass
            out[n] = t
    return out


def test_272_codes_record_does_not_carry_whole_roster():
    """#272：名冊有英文代號欄、6 個人只有 2 個出現在這一集。先打開第 3 步（寫出記了整份名冊代號的代號表紀錄）再交接打包：
    快照名冊只有這一集的人、整個包（含代號表紀錄、第 5 步的包）都不含其他人的名字與代號；匯入後第 4 步前置檢查通過、
    打開第 3 步不當成「代號改了」。"""
    others = ("王小花", "花花", "陳大同", "林志遠", "張雅婷", "Lily", "Ben", "Max", "Ivy", "大雄", "胖虎", "Ken")
    saved = (_DATA / "名冊.csv").read_bytes()
    try:
        (_DATA / "名冊.csv").write_text(ROSTER + "王小花,花花,Lily,,女,\n陳大同,,Ben,,男,\n林志遠,,Max,,男,\n張雅婷,,Ivy,,女,\n",
                                       encoding="utf-8")
        w, video = _teacher()
        _open3(w)
        full = json.loads(epcodes.snapshot_path(w).read_text(encoding="utf-8"))
        assert {"王小花", "張雅婷", "大雄"} <= set(full), "前提：打開第 3 步後代號表紀錄記了整份名冊"
        corpus = handoff._corpus(w)
        assert not any(x in corpus for x in ("王小花", "陳大同", "張雅婷")), "判斷用到誰不看代號表紀錄"
        mt = epcodes.snapshot_path(w).stat().st_mtime_ns
        z = _pack(w, handoff.STAGE3)
        assert epcodes.snapshot_path(w).read_text(encoding="utf-8") and \
            json.loads(epcodes.snapshot_path(w).read_text(encoding="utf-8")) == full, "老師這台的檔不動"
        texts = _zip_texts(z)
        roster = texts["工作區/交接/設定快照/名冊.csv"]
        assert "小美" in roster and "阿明" in roster
        leaks = {n: [x for x in others if x in t] for n, t in texts.items() if any(x in t for x in others)}
        assert not leaks, leaks
        rec = json.loads(texts["工作區/校對/代號表紀錄.json"].split("\n{")[0])
        assert rec == {"小美": "Amy", "阿明": "Tom"}, rec
        m = handoff.read_manifest(z)
        assert next(f for f in m["檔案"] if f["路徑"] == "校對/代號表紀錄.json")["修改時間"] == mt, "修改時間照原檔"

        v2 = _download(video)
        job = _import(z, v2)
        assert job.state == "完成", (job.state, job.error, job.ask)
        w2 = Path(job.result["工作區"])
        assert epcodes.snapshot_path(w2).stat().st_mtime_ns == mt
        pre = execute.precheck(w2)
        assert pre["可以開始"], pre["缺"]
        assert epcodes.episode_codes(w2) == {"小美": "Amy", "阿明": "Tom"}
        assert epcodes.propagate(w2) == {"換": {}, "要再看": []}
        before = epcodes.snapshot_path(w2).read_bytes()
        _open3(w2)
        assert epcodes.snapshot_path(w2).read_bytes() == before, "匯入後打開第 3 步不當成代號改了"

        _partner_stage5(w2)
        z5 = _pack(w2, handoff.STAGE5)
        leaks = {n: [x for x in others if x in t] for n, t in _zip_texts(z5).items() if any(x in t for x in others)}
        assert not leaks, leaks

        # 審查 B：代號表紀錄讀好之後、寫進包之前原檔被改了（例如第 3 步正在對齊代號）→ 停，不存
        real = handoff._codes_record

        def racing(wk, roster_csv, corpus=None):
            out = real(wk, roster_csv, corpus)
            time.sleep(0.01)
            wd.write_json(epcodes.snapshot_path(wk), {**full, "小美": "Anna"})
            return out
        handoff._codes_record = racing
        try:
            job = handoff.PackJob(w, Path(tempfile.mkdtemp()), handoff.STAGE3)
            job.run()
        finally:
            handoff._codes_record = real
        assert job.state == "失敗" and "檔案變了" in (job.error or ""), (job.state, job.error)
    finally:
        (_DATA / "名冊.csv").write_bytes(saved)


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        fn()
        print(f"✓ {name}")
    print(f"全部 {len(tests)} 個通過")
