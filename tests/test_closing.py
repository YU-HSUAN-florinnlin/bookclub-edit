"""1015-8（32 號設計 #222 第 1 段）：`bookclub/closing.py` 的逐字稿匯出（§6、§1a 問題 3、§1b 第 2 點）、
`closed`、`bookclub disk`（§4.5、§1b 第 1 點，只讀）。

全部用合成資料（假句子、假資料夾），不碰真的工作區、不碰真的家目錄。
獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_closing.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import closing  # noqa: E402
from bookclub import workdir as wd  # noqa: E402


def _s(a, b, text, i=0):
    return {"id": f"00_{i:03d}", "start": a, "end": b, "text": text}


def test_srt_time_rounding_and_hours():
    assert closing.srt_time(0) == "00:00:00,000"
    assert closing.srt_time(59.9995) == "00:01:00,000"      # 毫秒四捨五入進位到分
    assert closing.srt_time(59.9994) == "00:00:59,999"
    assert closing.srt_time(3.12) == "00:00:03,120"
    assert closing.srt_time(3723.4567) == "01:02:03,457"     # 超過 1 小時
    assert closing.srt_time(36000.0) == "10:00:00,000"


def test_srt_format_skip_empty_crlf_and_fix_end():
    sents = [_s(7.0, 9.0, "第二句", 2), _s(0.0, 2.5, "  第一句  ", 1), _s(3.0, 3.0, "結束跟開始一樣", 3),
             _s(4.0, 5.0, "   ", 4), _s(5.0, 6.0, "", 5)]
    srt = closing.transcript_srt(sents)
    assert "\r\n" in srt and "\n" not in srt.replace("\r\n", "")
    blocks = srt.split("\r\n\r\n")
    assert blocks[-1] == ""
    assert blocks[:3] == ["1\r\n00:00:00,000 --> 00:00:02,500\r\n第一句",          # 照 start 排、去頭尾空白
                          "2\r\n00:00:03,000 --> 00:00:03,500\r\n結束跟開始一樣",   # end <= start 補 0.5 秒
                          "3\r\n00:00:07,000 --> 00:00:09,000\r\n第二句"]
    assert len(blocks) == 4                                                      # 空句略過
    assert closing.transcript_srt([]) == ""


def test_txt_format():
    txt = closing.transcript_txt([_s(0.0, 1.0, "開始", 1), _s(5825.4, 5830.0, "很後面", 2), _s(65.0, 66.0, " ", 3)])
    lines = txt.split("\r\n")
    assert lines[0] == closing.TXT_HEAD and lines[1] == ""
    assert lines[2] == "[00:00] 開始" and lines[3] == "[97:05] 很後面" and lines[4] == ""
    assert len(lines) == 5 and txt.endswith("\r\n")


def test_product_time_cut_freeze_and_intro():
    # 範圍 0–100：剪掉 10–20、在 50 秒停格 2 秒
    plist = [{"src": [0.0, 10.0], "freeze": 0.0}, {"src": [20.0, 50.0], "freeze": 2.0}, {"src": [50.0, 100.0], "freeze": 0.0}]
    assert closing.product_span(1.0, 2.0, plist) == (1.0, 2.0)
    assert closing.product_span(12.0, 18.0, plist) is None                 # 整句剪掉：不寫
    assert closing.product_span(8.0, 22.0, plist) == (8.0, 12.0)           # 跨剪點：留下來的頭尾
    assert closing.product_span(60.0, 61.0, plist) == (52.0, 53.0)         # 停格之後往後 2 秒
    assert closing.product_span(1.0, 2.0, None) == (1.0, 2.0)              # 沒有片段表：時間不變
    sents = [_s(1.0, 2.0, "留著", 1), _s(12.0, 18.0, "剪掉的", 2), _s(60.0, 61.0, "停格之後", 3)]
    srt = closing.transcript_product_srt(sents, plist)
    assert "剪掉的" not in srt and srt.startswith("1\r\n00:00:01,000 --> 00:00:02,000\r\n留著")
    assert "2\r\n00:00:52,000 --> 00:00:53,000\r\n停格之後" in srt
    # §1b 第 2 點：片頭 10 秒 → 每句往後 10 秒
    srt10 = closing.transcript_product_srt(sents, plist, intro_s=10.0)
    assert srt10.startswith("1\r\n00:00:11,000 --> 00:00:12,000\r\n留著")
    assert "2\r\n00:01:02,000 --> 00:01:03,000\r\n停格之後" in srt10
    # 範圍不是從 0 開始（組裝 37:00–55:23 那種）：成品時間從 0 算
    assert closing.product_span(2230.0, 2231.0, [{"src": [2220.0, 3323.0], "freeze": 0.0}]) == (10.0, 11.0)


def _workdir(root: Path) -> Path:
    w = root / "影片_剪輯工作區"
    wd.write_json(w / "transcript" / "merged.json", {"sentences": [_s(0.0, 2.0, "甲", 1), _s(12.0, 14.0, "乙", 2),
                                                                     _s(30.0, 31.0, "丙", 3)]})
    return w


def test_export_writes_three_files_without_overwrite():
    from bookclub import finalcheck, proclog

    with tempfile.TemporaryDirectory() as t:
        w = _workdir(Path(t))
        out = Path(t) / "匯出"
        got = closing.export_transcripts(w, out)
        assert sorted(Path(f).name for f in got["檔案"]) == ["逐字稿.srt", "逐字稿.txt"] and got["成品時間"] is None
        assert got["說明"] and got["句數"] == 3
        raw = (out / "逐字稿.srt").read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw and b"\r\r\n" not in raw   # UTF-8 不加 BOM、不重複轉換行
        # 輸出過成品（沒升級過的工作區：照處理紀錄產生時間對）→ 多一份成品時間的
        log = {"產生時間": "2026-10-16T10:00:00", "片段": [{"src": [0.0, 10.0], "freeze": 0.0},
                                                          {"src": [20.0, 40.0], "freeze": 0.0}], "紀錄": []}
        wd.write_json(proclog.log_path(w), log)
        wd.write_json(finalcheck.check_path(w), {"輸出成品": {"處理紀錄產生時間": log["產生時間"], "版本號": None}})
        got = closing.export_transcripts(w, out, intro_s=10.0)
        names = sorted(Path(f).name for f in got["檔案"])
        assert names == ["逐字稿 (2).srt", "逐字稿 (2).txt", "逐字稿_成品時間.srt"], names   # 同名不蓋掉
        prod = (out / "逐字稿_成品時間.srt").read_text(encoding="utf-8")
        assert "乙" not in prod and "00:00:10,000 --> 00:00:12,000" in prod and "00:00:30,000 --> 00:00:31,000" in prod
        # 不能存進工作區裡
        try:
            closing.export_transcripts(w, w / "裡面")
            raise AssertionError("應該擋下")
        except ValueError as e:
            assert "工作資料夾裡" in str(e)


def test_cli_close_transcript():
    from bookclub import cli

    with tempfile.TemporaryDirectory() as t:
        w = _workdir(Path(t))
        assert cli.main(["close", "--transcript", str(w), str(Path(t) / "出")]) == 0
        assert (Path(t) / "出" / "逐字稿.srt").is_file()
        assert cli.main(["close", "--transcript", str(Path(t) / "沒有"), str(Path(t) / "出2")]) == 1


def test_closed_only_from_record_file():
    with tempfile.TemporaryDirectory() as t:
        w = Path(t)
        assert not closing.closed(w)
        for s, want in ((closing.PACKING, False), (closing.PACKED, False), (closing.CLEANING, True), (closing.CLOSED, True)):
            wd.write_json(w / closing.RECORD_FILE, {"狀態": s})
            assert closing.closed(w) is want, s
        (w / closing.RECORD_FILE).write_text("{壞掉", encoding="utf-8")
        assert not closing.closed(w)


def _mk(p: Path, size: int) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)


def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(root.rglob("*"))}


def test_disk_report_lists_and_never_deletes():
    with tempfile.TemporaryDirectory() as t:
        home, data = Path(t) / "家", Path(t) / "家" / "讀書會剪輯資料"
        vids = home / "影片們"
        w1, w2 = vids / "第一堂_剪輯工作區", vids / "第二堂_剪輯工作區"
        _mk(w1 / "輸出" / "成品.mp4", 3_000_000)
        _mk(w2 / "transcript" / "merged.json", 6_000)
        wd.write_json(w2 / closing.RECORD_FILE, {"狀態": closing.CLOSED})
        wd.write_json(data / "專案清單.json", {"專案": [str(w1), str(w2), str(vids / "不見了_剪輯工作區")]})
        _mk(data / "影片" / "第一堂.mp4", 2_000_000)
        _mk(home / ".cache" / "bookclub" / "模型.bin", 5_000_000)
        _mk(home / "bookclub-edit-舊版0.2.1" / "a.py", 1_000_000)
        _mk(home / "bookclub-edit-舊版0.2.2b1" / "b.py", 1_500_000)
        _mk(home / "讀書會剪輯資料_更新前備份_1016" / "c.json", 400_000)
        _mk(vids / "第一堂_剪輯工作區_更新前備份_1016" / "d.json", 100_000)
        os.symlink(w1 / "輸出" / "成品.mp4", home / ".cache" / "bookclub" / "捷徑.mp4")   # 捷徑不算大小
        before = _tree(home)
        text = "\n".join(closing.disk_report(home, data, wsl=False))
        assert _tree(home) == before                                  # 只讀：一個檔都沒動
        assert "工作區（2 支）" in text and "第一堂_剪輯工作區" in text and "進行中" in text and "已結案" in text
        assert "~/讀書會剪輯資料/影片/" in text and "2 MB" in text
        assert "~/.cache/bookclub" in text and "5 MB" in text
        assert "~/.cache/uv" not in text and "~/.cache/huggingface" not in text   # 沒有的資料夾不印
        assert "舊版工具資料夾（2 份）" in text and "2 MB" in text and "下次更新會自動刪" in text
        assert "更新前備份（2 份）" in text and "500 KB" in text
        assert "虛擬硬碟" not in text                                  # 不是 WSL 不印


def test_disk_report_empty_and_wsl_without_vhdx():
    with tempfile.TemporaryDirectory() as t:
        home = Path(t)
        lines = closing.disk_report(home, home / "讀書會剪輯資料", wsl=True, mnt_c=home / "沒有C槽")
        assert lines[0] == "讀書會剪輯：佔用空間" and "沒有找到" in lines[1] and len(lines) == 2
        # WSL、找得到 ext4.vhdx
        c = home / "c"
        _mk(c / "Users" / "老師" / "AppData" / "Local" / "Packages" / "Ubuntu" / "LocalState" / "ext4.vhdx", 7_000_000)
        lines = closing.disk_report(home, home / "讀書會剪輯資料", wsl=True, mnt_c=c)
        assert any("ext4.vhdx" in x and "7 MB" in x and "C 槽還剩" in x for x in lines)
        assert any("縮小虛擬硬碟" in x for x in lines)


def test_disk_report_wsl_layout_and_hardlinks_not_double_counted():
    """審查必修 4：WSL 上工作區在 `影片/第一堂_剪輯工作區/` 底下——「影片副本」只算 `影片/` 最上層的影片檔；
    硬連結（同一個檔兩個名字，例如更新前備份用 cp -l）整份報告只算一次；合計＝實際佔用。"""
    with tempfile.TemporaryDirectory() as t:
        home = Path(t) / "家"
        data = home / "讀書會剪輯資料"
        vids = data / "影片"
        w = vids / "第一堂_剪輯工作區"
        _mk(vids / "第一堂.mp4", 2_000_000)
        _mk(vids / "說明.txt", 1_000)                                  # 不是影片：不算進影片副本
        _mk(w / "輸出" / "成品.mp4", 3_000_000)
        _mk(w / "生成" / "a.wav", 500_000)
        wd.write_json(data / "專案清單.json", {"專案": [str(w)]})
        bak = vids / "第一堂_剪輯工作區_更新前備份_1016"
        (bak / "輸出").mkdir(parents=True)
        os.link(w / "輸出" / "成品.mp4", bak / "輸出" / "成品.mp4")       # 硬連結：不佔新空間
        _mk(bak / "b.json", 100_000)
        lines = closing.disk_report(home, data, wsl=False)
        text = "\n".join(lines)
        wsz = closing.dir_size(w)
        assert f"工作區（1 支）" in text and closing.human(wsz) in text
        vline = next(x for x in lines if "影片/" in x and "影片副本" in x)
        assert "2 MB" in vline, vline                                    # 只有最上層那支影片，不含工作區
        bline = next(x for x in lines if "更新前備份" in x)
        assert "100 KB" in bline, bline                                  # 硬連結那支已經算在工作區
        real = sum(os.lstat(p).st_size for p in {(os.lstat(f).st_dev, os.lstat(f).st_ino): f
                                                  for f in map(str, home.rglob("*")) if os.path.isfile(f)}.values())
        total = next(x for x in lines if x.startswith("合計"))
        assert closing.human(real - 1_000 - os.path.getsize(data / "專案清單.json")) in total, (total, real)


def test_cli_disk_runs():
    import io
    from contextlib import redirect_stdout

    from bookclub import cli

    buf = io.StringIO()
    old = os.environ.get("HOME")
    with tempfile.TemporaryDirectory() as t:
        os.environ["HOME"] = t   # 不去量真的家目錄
        try:
            with redirect_stdout(buf):
                assert cli.main(["disk"]) == 0
        finally:
            if old is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old
    assert buf.getvalue().startswith("讀書會剪輯：佔用空間")


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
