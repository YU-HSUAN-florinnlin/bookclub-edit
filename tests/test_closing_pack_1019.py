"""批次 1019-1：結案打包兩個小改動。

- #252：選到的資料夾本身就叫「讀書會剪輯結案」時，不再多建一層同名資料夾（第二支影片結案時老師很可能直接點進上次那個）。
- #253：打包多帶工作區裡的小紀錄（第 3 步決定、名字、逐字時間、生成紀錄、參考音文字檔⋯），之後要能重新匯入；
  影片、聲音等大檔與超過上限的檔不帶、寫進打包清單「沒帶的檔」；打包清單第一段補版本那一行與「原始影片沒有打包」。

合成資料（假影片位元組、假名字），全部寫在系統暫存資料夾；不碰 ~/讀書會剪輯資料。
獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_closing_pack_1019.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from bookclub import closing  # noqa: E402
from bookclub import workdir as wd  # noqa: E402

DAY = time.strftime("%Y%m%d")
NODE = shutil.which("node")


def _mk(p: Path, data: bytes) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _workdir(root: Path, *, versions: int | None = 3, out_ver: int | None = 2) -> Path:
    """假工作區：輸出過第 out_ver 版（共 versions 版），加上 #253 要多帶的各種小紀錄、大的聲音檔。"""
    w = root / "第一堂_剪輯工作區"
    prod = _mk(w / "輸出" / "成品_v2_0-3_sw.mp4", os.urandom(200_000))
    final = _mk(w / "輸出" / "最終成品_v2_0-3_sw_20261019-101500.mp4", prod.read_bytes())
    _mk(w / "輸出" / "成品_v1_0-3_sw.mp4", os.urandom(40_000))           # 別版的成品：不帶
    _mk(w / "輸出" / "原聲_0-3.wav", os.urandom(10_000))                # 比對用聲音：不帶
    _mk(w / "輸出" / "處理標記_0-3.md", "標記".encode())
    _mk(w / "生成" / "處理紀錄.json", json.dumps({"產生時間": "2026-10-19T10:00:00", "片段": None}).encode())
    _mk(w / "生成" / "剪輯決策.json", b"{}")
    # 10-19 #253 要多帶的小紀錄
    _mk(w / "生成" / "老師紀錄.json", b'{"S001": {}}')
    _mk(w / "生成" / "學員紀錄.json", b"{}")
    _mk(w / "生成" / "名字處理計畫.json", b"{}")
    _mk(w / "生成" / "_嘗試快取.json", b"{}")                          # 生成用的快取：清空間會清，不算紀錄
    _mk(w / "生成" / "老師" / "S001.wav", os.urandom(2_000_000))         # 大的聲音檔：不帶
    _mk(w / "覆核" / "覆核決定.json", '{"決定": []}'.encode())
    _mk(w / "覆核" / "名字覆核決定.json", b"{}")
    _mk(w / "校對" / "人名決定.json", "{\"小安\": \"Amy\"}".encode())      # 假名字
    _mk(w / "校對" / "段落.json", b"[]")
    _mk(w / "學員名字候選" / "候選.json", b"[]")
    _mk(w / "transcript" / "chunk_0000.json", b'{"words": []}')
    _mk(w / "transcript" / "merged.srt", "1\r\n00:00:00,000 --> 00:00:01,000\r\n甲\r\n".encode())
    _mk(w / "參考音" / "候選.json", b"[]")
    _mk(w / "參考音" / "候選_01.txt", "參考音的文字".encode())
    _mk(w / "參考音" / "候選_01.wav", os.urandom(30_000))                # 參考音聲音：不帶
    _mk(w / "說話者判斷.json", b"{}")
    _mk(w / "名字覆核.html", "<html></html>".encode())
    _mk(w / "名字排除清單.csv", "名字\r\n".encode())
    _mk(w / "audio.flac", os.urandom(50_000))                             # 抽出來的音軌：不帶
    _mk(w / "覆核" / ".覆核決定.json.寫入中", b"x")                       # 點開頭的暫存：不帶
    _mk(w / "覆核" / "太大的紀錄.json", b"{" + b" " * 300_000 + b"}")   # 測試時把上限調到 100 KB：超過上限
    _mk(w / "報告.md", "報告".encode())
    wd.write_json(w / "分析結果.json", {"video": str(root / "第一堂.mp4")})
    wd.write_json(w / "transcript" / "merged.json", {"sentences": [{"id": "1", "start": 0.0, "end": 1.0, "text": "甲"}]})
    out = {"檔案": str(final.relative_to(w)), "來源": "輸出/成品_v2_0-3_sw.mp4", "處理紀錄產生時間": "2026-10-19T10:00:00"}
    if out_ver is not None:
        out["版本號"] = out_ver
    wd.write_json(w / "覆核" / "成品檢查.json", {"輸出成品": out})
    if versions:
        wd.write_json(w / "修改需求" / "格式.json", {"格式": 1})
        wd.write_json(w / "修改需求" / "版本.json", {"列": [{"版本號": n} for n in range(1, versions + 1)]})
    return w


def _run(w: Path, loc: Path, **kw) -> closing.PackJob:
    job = closing.PackJob(w, loc, **kw)
    job.run()
    return job


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _small_limit():
    """把單檔上限調到 100 KB（不用真的寫 50 MB 的假檔）；回傳還原用的函式。"""
    old = closing.PACK_RECORD_MAX_BYTES
    closing.PACK_RECORD_MAX_BYTES = 100_000

    def undo():
        closing.PACK_RECORD_MAX_BYTES = old
    return undo


# ---------- #252 ----------

def test_252_normal_folder_gets_one_layer():
    with tempfile.TemporaryDirectory() as t:
        w, loc = _workdir(Path(t)), Path(t) / "下載"
        loc.mkdir()
        job = _run(w, loc)
        assert job.state == "已存好", job.error
        want = loc / closing.PACK_ROOT / f"第一堂_{DAY}"
        assert Path(job.folder) == want and want.is_dir()
        assert closing.read_record(w)["打包"][-1]["資料夾"] == str(want)
        assert closing.pack_base(loc) == loc / closing.PACK_ROOT


def test_252_picked_pack_root_itself_no_nesting_and_numbered():
    with tempfile.TemporaryDirectory() as t:
        w = _workdir(Path(t))
        root = Path(t) / "下載" / closing.PACK_ROOT          # 上次結案留下的「讀書會剪輯結案」，老師直接點進去
        _mk(root / f"別支影片_{DAY}" / "打包清單.txt", b"x")
        assert closing.pack_base(root) == root
        loc = closing.check_location(w, root, 1000)          # 空間檢查、位置檢查照樣過
        job = _run(w, loc)
        assert job.state == "已存好", job.error
        assert Path(job.folder) == root.resolve() / f"第一堂_{DAY}"
        assert not (root / closing.PACK_ROOT).exists()        # 沒有變成 讀書會剪輯結案/讀書會剪輯結案/
        rec = closing.read_record(w)
        assert rec["打包"][-1]["資料夾"] == str(root.resolve() / f"第一堂_{DAY}")
        assert closing.pack_state(w)["核對通過的"]["資料夾"] == str(root.resolve() / f"第一堂_{DAY}")
        # 同名影片資料夾已經在 → 照舊加「 (2)」
        job2 = _run(w, loc)
        assert job2.state == "已存好" and Path(job2.folder) == root.resolve() / f"第一堂_{DAY} (2)"
        assert sorted(p.name for p in root.iterdir()) == sorted([f"別支影片_{DAY}", f"第一堂_{DAY}", f"第一堂_{DAY} (2)"])


def test_252_location_checks_and_windows_display():
    with tempfile.TemporaryDirectory() as t:
        w = _workdir(Path(t))
        try:   # 工作區就在選到的「讀書會剪輯結案」裡面：照樣擋
            inner = Path(t) / closing.PACK_ROOT
            w2 = _workdir(inner)
            closing.check_location(w2, inner, 1000)
            raise AssertionError("工作區在打包位置裡應該擋下")
        except closing.PackError as e:
            assert "工作資料夾裡" in str(e), e
        assert closing.check_location(w, Path(t), 1000) == Path(t).resolve()
    assert closing.windows_display(f"/mnt/d/{closing.PACK_ROOT}/第一堂_{DAY}") == f"D:\\{closing.PACK_ROOT}\\第一堂_{DAY}"


def test_252_js_pick_hint():
    if not NODE:
        print("（沒有 node，略過）")
        return
    import re

    src = (REPO_ROOT / "bookclub" / "web" / "fileio.js").read_text(encoding="utf-8")
    fns = "".join(re.search(rf"^function {n}\(.*?^\}}\n", src, re.S | re.M).group(0) for n in ("clGB", "clView"))
    code = fns + """
      const base = {"暫存大小": 2.6e9, "系統": "wsl", "狀態": null, "工作": null, "可以繼續": false};
      console.log(JSON.stringify([
        clView(base),
        clView({...base, "狀態": "已打包", "核對通過的": {"資料夾": "D:\\\\x"}}),
        clView({...base, "工作": {"狀態": "存檔中", "第幾個": 2, "共幾個": 9}}),
        clView({...base, "狀態": "已結案", "只有紀錄": true}),
        clView({...base, "狀態": "清理中"}),
      ]));"""
    v = json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True).stdout)
    hint = "選放結案資料的地方（例如下載、D 槽）；選上次的「讀書會剪輯結案」也可以。"
    assert v[0]["pickHint"] == hint and v[1]["pickHint"] == hint
    assert not v[2]["pickHint"] and not v[3]["pickHint"] and not v[4]["pickHint"]
    assert "${v.pickHint ?" in src
    server = (REPO_ROOT / "bookclub" / "server.py").read_text(encoding="utf-8")
    assert "選上次的「讀書會剪輯結案」也可以\")" in server   # 選資料夾視窗上的說明


# ---------- #253 ----------

EXTRA = sorted([
    "紀錄/生成/老師紀錄.json", "紀錄/生成/學員紀錄.json", "紀錄/生成/名字處理計畫.json",
    "紀錄/覆核/覆核決定.json", "紀錄/覆核/名字覆核決定.json", "紀錄/校對/人名決定.json", "紀錄/校對/段落.json",
    "紀錄/學員名字候選/候選.json", "紀錄/transcript/chunk_0000.json", "紀錄/transcript/merged.srt",
    "紀錄/transcript/merged.json", "紀錄/參考音/候選.json", "紀錄/參考音/候選_01.txt", "紀錄/說話者判斷.json",
    "紀錄/名字覆核.html", "紀錄/名字排除清單.csv"])


def test_253_small_records_packed_big_files_not():
    undo = _small_limit()
    try:
        with tempfile.TemporaryDirectory() as t:
            w, loc = _workdir(Path(t)), Path(t) / "下載"
            loc.mkdir()
            plan_before = closing.clean_plan(w, kind="mac")
            job = _run(w, loc)
            assert job.state == "已存好", job.error
            out = Path(job.folder)
            names = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
            for rel in EXTRA:
                assert rel in names, rel
                assert (out / rel).read_bytes() == (w / rel.removeprefix("紀錄/")).read_bytes(), rel
            # 原本就有的照舊（不重複帶：成品檢查只在 紀錄/成品檢查.json，分析結果只在 紀錄/分析結果.json）
            for rel in ("紀錄/成品檢查.json", "紀錄/分析結果.json", "紀錄/報告.md", "紀錄/剪輯決策.json",
                        "紀錄/處理紀錄.json", "紀錄/修改需求/版本.json", "紀錄/處理標記_0-3.md"):
                assert rel in names, rel
            for no in ("紀錄/覆核/成品檢查.json", "紀錄/生成/剪輯決策.json", "紀錄/生成/處理紀錄.json"):
                assert no not in names, no
            # 不帶：影片、聲音、快取、點開頭的暫存、超過上限的、結案.json、其他版本的成品
            for p in names:
                assert not p.endswith((".wav", ".flac")), p
                assert "_嘗試快取" not in p and "寫入中" not in p and "太大的紀錄" not in p and closing.RECORD_FILE not in p, p
            assert [p for p in names if p.endswith(".mp4")] == ["最終成品_v2_0-3_sw_20261019-101500.mp4"]
            # 核對全過：結案.json 每個檔的大小、SHA-256 跟打包裡的一樣
            e = closing.read_record(w)["打包"][-1]
            assert e["核對"] == "通過" and len(e["檔案"]) == len(names)
            for f in e["檔案"]:
                assert _sha(out / f["相對路徑"]) == f["sha256"] and (out / f["相對路徑"]).stat().st_size == f["大小"]
            src = {f["相對路徑"]: f["來源"] for f in e["檔案"]}
            assert src["紀錄/覆核/覆核決定.json"] == "覆核/覆核決定.json"
            # 打包清單：第一段版本那一行＋原始影片那一句；沒帶的檔
            lst = (out / "打包清單.txt").read_bytes().decode("utf-8").split("\r\n")
            assert lst[:5] == [*closing.PRIVACY_LINES, "最後輸出的是第 2 版（共 3 版）。",
                               "卡片：做進成品 0 張（人工確認過 0、還沒確認 0）。",
                               "原始影片沒有打包，請另外保留。"], lst[:6]   # 這個假工作區沒有卡片、生成紀錄沒指到聲音
            assert lst[5] == "" and lst[6].startswith("共 ")
            i = next(k for k, s in enumerate(lst) if s.startswith("沒帶的檔"))
            skipped = lst[i + 2:]
            assert any(s.startswith("覆核/太大的紀錄.json：") and "太大沒帶" in s for s in skipped), skipped
            gen = next(s for s in skipped if s.startswith("生成/："))
            assert ".wav" in gen and ".json" in gen, gen          # 大的聲音檔＋要清的快取 .json
            gen_n = int(gen.split("：")[1].split(" 個")[0])
            assert gen_n == 2, gen                                # S001.wav＋_嘗試快取.json
            assert any(s.startswith("參考音/：1 個（.wav）") for s in skipped), skipped
            assert any(s.startswith("輸出/：") and ".mp4" in s and ".wav" in s for s in skipped), skipped
            assert any(s.startswith("工作區最上層：1 個（.flac）") for s in skipped), skipped
            # 清空間的計畫不變（新帶走的只是複製，工作區原本就留這些檔）
            assert closing.clean_plan(w, kind="mac") == plan_before
            for rel in EXTRA:
                assert closing.classify(rel.removeprefix("紀錄/"))[0] != "清", rel
    finally:
        undo()


def test_253_version_line_unknown():
    with tempfile.TemporaryDirectory() as t:
        w = _workdir(Path(t), versions=None)        # 沒升級過（沒有修改需求/版本表），輸出紀錄有版本號：只寫第 N 版
        assert closing.pack_version_line(w) == "最後輸出的是第 2 版。"
    with tempfile.TemporaryDirectory() as t:
        w = _workdir(Path(t), versions=None, out_ver=None)   # 版本號也沒有
        assert closing.pack_version_line(w) == "版本：查不到"
    with tempfile.TemporaryDirectory() as t:
        w = _workdir(Path(t), versions=4, out_ver=4)
        assert closing.pack_version_line(w) == "最後輸出的是第 4 版（共 4 版）。"
    assert closing.pack_list_text([]).split("\r\n")[:3] == [*closing.PRIVACY_LINES, ""]   # 沒給版本＝照舊


def test_253_records_only_after_closed_also_has_records():
    with tempfile.TemporaryDirectory() as t:
        w, loc = _workdir(Path(t)), Path(t) / "下載"
        loc.mkdir()
        assert _run(w, loc).state == "已存好"
        rec = closing.read_record(w)
        rec["狀態"] = closing.CLOSED
        wd.write_json(closing.record_path(w), rec)
        job = _run(w, loc, records_only=True)
        out = Path(job.folder)
        assert job.state == "已存好", job.error
        assert not list(out.glob("*.mp4")) and (out / "紀錄" / "覆核" / "覆核決定.json").is_file()
        assert (out / "紀錄" / "transcript" / "merged.json").is_file()


def test_253_small_text_record_changed_retried():
    """新帶的 .md／.txt 存的時候被改了：跟 .json 一樣重讀重存（最多 3 次）。"""
    with tempfile.TemporaryDirectory() as t:
        w, loc = _workdir(Path(t)), Path(t) / "下載"
        loc.mkdir()
        target = w / "參考音" / "候選_01.txt"
        job = closing.PackJob(w, loc)
        orig = job._copy
        hits = []

        def copy(src, data, tmp):
            got = orig(src, data, tmp)
            if src == target and not hits:
                hits.append(1)
                target.write_bytes("改過的文字".encode())   # 複製完、比對前被改了一次
            return got
        job._copy = copy
        job.run()
        assert job.state == "已存好", job.error
        assert hits and (Path(job.folder) / "紀錄" / "參考音" / "候選_01.txt").read_bytes() == "改過的文字".encode()


def test_253_pack_items_no_duplicates_and_server_size():
    with tempfile.TemporaryDirectory() as t:
        w = _workdir(Path(t))
        items = closing.pack_items(w)
        rels = [it["相對路徑"] for it in items]
        assert len(rels) == len(set(rels)), rels
        srcs = [it["來源"] for it in items if it.get("來源") is not None]
        assert len(srcs) == len(set(srcs))
        st = closing.pack_state(w)
        assert st["打包大小"] == closing.pack_size(items) and st["檔數"] == len(items) + 1


# ---------- #253 做法 A：選定的生成聲音、卡片統計（宇軒 10-19 追加） ----------

def _voices(w: Path) -> dict:
    """三種生成紀錄各一句：選定檔、放回時間格檔、來源檔；另有沒被指到的嘗試 wav 與 `_嘗試快取.json`。回傳 {相對路徑: 位元組}。"""
    files = {}

    def wav(rel: str, n: int) -> str:
        _mk(w / rel, os.urandom(n))
        files[rel] = n
        return rel
    t = {"id": "S001", "檔案": str(w / wav("生成/老師/S001.wav", 40_000)),       # 完整路徑（localize 解析）
         "放回時間格": {"檔案": wav("生成/老師/S001_放回時間格.wav", 41_000), "來源檔案": "生成/老師/S001.wav"}}
    _mk(w / "生成" / "老師" / "S001_2.wav", os.urandom(30_000))                 # 沒被指到的嘗試檔：不帶
    s = {"id": "T001", "檔案": wav("生成/學員/T001.wav", 50_000),
         "放回時間格": {"檔案": wav("生成/學員/T001_放回時間格.wav", 50_500), "來源檔案": wav("生成/學員/T001_來源.wav", 60_000)}}
    k = {"id": "K001", "檔案": wav("生成/保留原聲學員/K001.wav", 20_000),
         "放回時間格": {"檔案": wav("生成/保留原聲學員/K001_放回時間格.wav", 21_000),
                        "來源檔案": "/別的電腦/舊工作區/生成/保留原聲學員/不在.wav"}}   # 工作區外、不在：略過
    wd.write_json(w / "生成" / "老師紀錄.json", {"句子": [t]})
    wd.write_json(w / "生成" / "學員紀錄.json", {"句子": [s]})
    wd.write_json(w / "生成" / "保留原聲學員紀錄.json", {"句子": [k]})
    _mk(w / "生成" / "學員" / "_嘗試快取.json", b"{}")
    return files


def test_253A_selected_voices_packed_clean_plan_same():
    with tempfile.TemporaryDirectory() as t:
        w, loc = _workdir(Path(t)), Path(t) / "下載"
        loc.mkdir()
        before_items = closing.pack_items(w)
        voices = _voices(w)
        plan_before = closing.clean_plan(w, kind="mac")
        items = closing.pack_items(w)
        got = sorted(it["相對路徑"] for it in items if it.get("生成聲音"))
        assert got == sorted(f"紀錄/{r}" for r in voices), got
        # 打包需要的空間把這些聲音算進去（伺服器空間檢查用的 pack_size）
        assert closing.pack_size(items) >= closing.pack_size(before_items) + sum(voices.values())
        assert closing.pack_state(w)["打包大小"] == closing.pack_size(items)
        job = _run(w, loc)
        assert job.state == "已存好", job.error
        out = Path(job.folder)
        for r in voices:
            assert (out / "紀錄" / r).read_bytes() == (w / r).read_bytes(), r
        assert not (out / "紀錄" / "生成" / "老師" / "S001_2.wav").exists()
        assert not (out / "紀錄" / "生成" / "學員" / "_嘗試快取.json").exists()
        e = closing.read_record(w)["打包"][-1]
        rows = {f["相對路徑"]: f for f in e["檔案"]}
        for r in voices:
            f = rows[f"紀錄/{r}"]
            assert f["來源"] == r and f["sha256"] == _sha(out / "紀錄" / r) and f["大小"] == voices[r]
        lst = (out / "打包清單.txt").read_bytes().decode("utf-8").split("\r\n")
        assert lst[4:6] == ["原始影片沒有打包，請另外保留。", "生成好的聲音已經存在紀錄裡，之後匯入只要重做改到的地方。"], lst[:7]
        gen = next(x for x in lst if x.startswith("生成/："))   # 沒被指到的嘗試檔、快取照資料夾合成一行
        assert ".wav" in gen and ".json" in gen, gen
        # 清空間不變：生成聲音照舊在工作區清掉
        plan = closing.clean_plan(w, kind="mac")
        assert plan == plan_before
        cleaned = {r["相對路徑"] for r in plan["檔案"]}
        assert all(r in cleaned for r in voices)
        # 清空間前的核對（看打包的成品、每個檔在、大小對）照樣過、照樣清得掉
        res = closing.clean(w, plan["清單指紋"], kind="mac")
        assert res["省下位元組"] == plan["合計"] and closing.closed(w)
        assert all(not (w / r).exists() for r in voices) and all((out / "紀錄" / r).is_file() for r in voices)


def _versions_case():
    """test_versions 的三版假工作區（第 1 版整支、第 2 版只做 0–75 秒），最後輸出第 2 版。"""
    import test_versions as tv
    from bookclub import finalcheck as fc

    w = tv._three_versions()
    chk = wd.read_json(fc.check_path(w))
    chk["輸出成品"] = {"檔案": "輸出/成品_v2_0-1_sw.mp4", "版本號": 2}
    wd.write_json(fc.check_path(w), chk)
    return w


def test_253A_card_stats_same_as_step5():
    """審查必修：用第 5 步同一套分類。這個假工作區：第 2 版做進去 3 張（T003 通過、刪除段落 S1 與停格還沒看）；
    名字:1 範圍外、第 1 版通過，T005 與第 1 版的停格範圍外、第 1 版還沒看＝沿用上一版（10-20 宇軒定甲：拆開狀態）；
    名字:2、T007、重疊、刪除段落 S2 哪一版都沒處理＝第 3 步選定不修改。"""
    from bookclub import needs, review
    from bookclub import turns as turns_mod

    w = _versions_case()
    assert closing.card_stats_line(w) == ("卡片：做進成品 3 張（人工確認過 1、還沒確認 2）；"
                                          "沿用上一版 3 張（人工確認過 1、還沒確認 2）；第 3 步選定不修改 4 張。")
    # 第 5 步新增、還沒生成的卡 → 還沒處理多 1
    from bookclub import finalcards

    m = review.manual_edit(w, {"類型": "局部消音", "start": 30.0, "end": 31.0, "方式": review.MUTE_WAYS[0]})
    assert finalcards.edited(w, f"局部消音:{m['id']}", new=True)["記了"]
    assert closing.card_stats_line(w) == ("卡片：做進成品 3 張（人工確認過 1、還沒確認 2）；"
                                          "沿用上一版 3 張（人工確認過 1、還沒確認 2）；還沒處理 1 張；第 3 步選定不修改 4 張。")
    # 第 3 步改了、等重做（T003 原本通過）→ 從人工確認過移到還沒處理
    turns_mod.save_turn(w, "T003", {"校對稿": "第三步改過的字"})
    needs.sync_cards(w)
    assert closing.card_stats_line(w) == ("卡片：做進成品 2 張（人工確認過 0、還沒確認 2）；"
                                          "沿用上一版 3 張（人工確認過 1、還沒確認 2）；還沒處理 2 張；第 3 步選定不修改 4 張。")


def test_253A_card_stats_carry_all_passed_and_returned():
    """沿用上一版全部確認過：只寫張數；有退回的拆出退回。"""
    from bookclub import finalcards, needs

    w = _versions_case()
    cross = finalcards.build(w)["跨版本卡片"]
    assert len(cross) == 2
    for c in cross:
        finalcards.decide(w, c["id"], needs.PASSED, version=1)
    assert closing.card_stats_line(w) == "卡片：做進成品 3 張（人工確認過 1、還沒確認 2）；沿用上一版 3 張；第 3 步選定不修改 4 張。"
    # 前一版的狀態列是退回、還沒排重做（例如舊工具留下的）→ 沿用上一版拆出退回
    st = needs.load(w)
    st.states[(cross[0]["卡號"], 1)]["狀態"] = needs.RETURNED
    needs.save(w, st)
    assert closing.card_stats_line(w) == ("卡片：做進成品 3 張（人工確認過 1、還沒確認 2）；"
                                          "沿用上一版 3 張（人工確認過 2、退回 1）；第 3 步選定不修改 4 張。")
    # 在第 5 步按退回（要重做）→ 排進重做＝還沒處理（跟第 5 步「重做這幾張」一致）
    finalcards.decide(w, cross[1]["id"], needs.RETURNED, ["念錯字"], version=1)
    assert closing.card_stats_line(w) == ("卡片：做進成品 3 張（人工確認過 1、還沒確認 2）；"
                                          "沿用上一版 2 張（人工確認過 1、退回 1）；還沒處理 1 張；第 3 步選定不修改 4 張。")


def test_253A_card_stats_old_version_new_card_not_kept():
    """審查建議 3：最後輸出的是舊版（走 version_page）時，那一版之後才新增、還沒生成的卡算還沒處理，不算選定不修改。"""
    from bookclub import finalcards, review
    from bookclub import finalcheck as fc

    w = _versions_case()
    chk = wd.read_json(fc.check_path(w))
    chk["輸出成品"]["版本號"] = 1
    wd.write_json(fc.check_path(w), chk)
    before = closing.card_stats_line(w)
    m = review.manual_edit(w, {"類型": "局部消音", "start": 30.0, "end": 31.0, "方式": review.MUTE_WAYS[0]})
    assert finalcards.edited(w, f"局部消音:{m['id']}", new=True)["記了"]
    after = closing.card_stats_line(w)
    assert "還沒處理 1 張" in after and after.endswith("第 3 步選定不修改 4 張。"), (before, after)


def test_253A_card_stats_old_version_and_unknown():
    w = _versions_case()
    from bookclub import finalcheck as fc

    chk = wd.read_json(fc.check_path(w))
    chk["輸出成品"]["版本號"] = 1        # 最後輸出的是第 1 版（不是現在處理紀錄那一版）→ 照第 1 版那一頁
    wd.write_json(fc.check_path(w), chk)
    line = closing.card_stats_line(w)
    assert line == "卡片：做進成品 6 張（人工確認過 2、還沒確認 4）；第 3 步選定不修改 4 張。", line   # 第 1 版整支：沒有沿用的
    with tempfile.TemporaryDirectory() as t:
        assert closing.card_stats_line(_workdir(Path(t), versions=None)) == "卡片：查不到"   # 沒有修改需求
    with tempfile.TemporaryDirectory() as t:
        assert closing.card_stats_line(_workdir(Path(t), out_ver=7)) == "卡片：查不到"       # 版本表沒有那一版
    lst = closing.pack_list_text([], version="版本：查不到", cards="卡片：查不到").split("\r\n")
    assert lst[:5] == [*closing.PRIVACY_LINES, "版本：查不到", "卡片：查不到", "原始影片沒有打包，請另外保留。"]


def _voice_log(w: Path, voices: dict, *, prints: bool = True) -> None:
    """最後輸出那一版的處理紀錄：每個選定、放回時間格的檔一筆，記 `檔案指紋`（proclog.stamp_contents 同一個算法）。"""
    from bookclub import proclog

    recs = [{"類型": "名字整句換掉", "檔案": r} for r in voices if "_來源" not in r]
    if prints:
        proclog.stamp_contents(w, recs)
    wd.write_json(w / "生成" / "處理紀錄.json", {"產生時間": "2026-10-19T10:00:00", "片段": None, "紀錄": recs})


def test_253A_voices_same_version_or_newer_or_unknown():
    for case in ("同一版", "又生成過", "舊紀錄"):
        with tempfile.TemporaryDirectory() as t:
            w, loc = _workdir(Path(t)), Path(t) / "下載"
            loc.mkdir()
            voices = _voices(w)
            _voice_log(w, voices, prints=case != "舊紀錄")
            if case == "又生成過":
                (w / "生成" / "老師" / "S001_放回時間格.wav").write_bytes(os.urandom(41_000))   # 輸出之後又生成過
            job = _run(w, loc)
            assert job.state == "已存好", job.error
            lst = (Path(job.folder) / "打包清單.txt").read_bytes().decode("utf-8").split("\r\n")
            e = closing.read_record(w)["打包"][-1]
            assert lst[5] == closing.VOICE_LINE, lst[:8]
            if case == "同一版":
                assert lst[6] == "" and e["聲音跟成品同一版"] is True and e["聲音不一樣檔數"] == 0
            elif case == "又生成過":
                assert lst[6] == "生成聲音是之後改過的狀態，比打包的成品新（有 1 個聲音檔不一樣）。", lst[:8]
                assert e["聲音跟成品同一版"] is False and e["聲音不一樣檔數"] == 1
            else:
                assert lst[6] == "生成聲音跟打包的成品是不是同一版：查不到。" and e["聲音跟成品同一版"] is None


def test_253A_voice_match_bad_format_unknown():
    """10-20 審查建議 2：處理紀錄格式不對（紀錄不是清單、檔案不是字串、同一個檔兩個指紋）→ 查不到，打包照樣存好。"""
    with tempfile.TemporaryDirectory() as t:
        w, loc = _workdir(Path(t)), Path(t) / "下載"
        loc.mkdir()
        _voices(w)
        for bad in ("壞掉", [{"檔案": {"x": 1}, "檔案指紋": "a"}],
                    [{"檔案": "生成/老師/S001.wav", "檔案指紋": "a"}, {"檔案": "生成/老師/S001.wav", "檔案指紋": "b"}]):
            wd.write_json(w / "生成" / "處理紀錄.json", {"產生時間": "2026-10-19T10:00:00", "片段": None, "紀錄": bad})
            assert closing.voice_match(w) == (None, 0), bad
        job = _run(w, loc)
        assert job.state == "已存好", job.error
        assert closing.read_record(w)["打包"][-1]["聲音跟成品同一版"] is None


def test_253_review_cache_dirs_staging_skipped_settings_kept():
    """0b7a6dd 審查：快取資料夾（web快取/、chunks/、名字候選/、重疊/）與 修改需求.升級中/ 的小檔不帶；工作區設定.json 照帶。"""
    with tempfile.TemporaryDirectory() as t:
        w = _workdir(Path(t))
        for rel in ("web快取/索引.json", "chunks/00.json", "名字候選/x.json", "重疊/區域.json", "修改需求.升級中/卡片.json"):
            _mk(w / rel, b"{}")
        _mk(w / "工作區設定.json", b'{"intro": null}')
        _mk(w / "參考音" / "大寫.TXT", "x".encode())
        rels = {it["相對路徑"] for it in closing.pack_items(w)}
        for rel in ("web快取/索引.json", "chunks/00.json", "名字候選/x.json", "重疊/區域.json", "修改需求.升級中/卡片.json"):
            assert f"紀錄/{rel}" not in rels, rel
        assert "紀錄/工作區設定.json" in rels and "紀錄/參考音/大寫.TXT" in rels
        assert ".TXT".lower() in closing.SMALL_RECORD_EXTS


if __name__ == "__main__":
    fns = [(k, v) for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print(f"✓ {name}")
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"✗ {name}：{type(e).__name__}：{e}")
    print(f"{len(fns) - bad}/{len(fns)} 通過")
    sys.exit(1 if bad else 0)
