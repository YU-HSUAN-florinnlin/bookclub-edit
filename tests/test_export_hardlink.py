"""#79（1017-2）：輸出成品不再多複製一份——最終成品是成品的硬連結，做不到（跨磁碟、檔案系統不支援）才複製。

- 第 5 步「輸出成品」（`finalcheck.export_final`）與選單輸出舊版（`finalcards.export_version`）：輸出後兩個名字是同一個檔
  （st_ino 相同、st_nlink 2）；`os.link` 失敗退回複製，結果照舊（內容、修改時間一樣，過期判斷照舊）
- 重新組裝（寫暫存檔再換名）：成品換成新檔，最終成品留著輸出當時的內容，`export_stale` 照樣說「成品換新了」
- 工作區裡的成品是指回原工作區的捷徑：連到捷徑指到的那支
- `bookclub disk` 與結案框「共 X GB」（`clean_plan` 合計）同一份只算一次
獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_export_hardlink.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import errno
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from bookclub import closing, finalcards  # noqa: E402
from bookclub import finalcheck as fc  # noqa: E402
from bookclub import fileio  # noqa: E402
from bookclub import workdir as wd  # noqa: E402


def _ffmpeg() -> bool:
    if shutil.which("ffmpeg"):
        return True
    print("（沒有 ffmpeg，跳過）")
    return False


def _same(a: Path, b: Path) -> bool:
    sa, sb = a.stat(), b.stat()
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


@contextmanager
def _no_link():
    """模擬跨磁碟／檔案系統不支援硬連結：os.link 一律丟 EXDEV。"""
    real = os.link

    def fail(*_a, **_k):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    os.link = fail
    try:
        yield
    finally:
        os.link = real


def _export_final(w: Path) -> dict:
    fc.page_data(w)
    r = fc.export_final(w, confirm=True)
    assert r["ok"], r
    return r


def _reassemble(prod: Path) -> None:
    """照 render.py 的做法：寫 `_輸出中_…` 暫存檔，完整了才 replace 成正式檔名（新的 inode）。"""
    tmp = prod.with_name(f"_輸出中_{prod.name}")
    tmp.write_bytes(prod.read_bytes() + b"-new")
    tmp.replace(prod)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def test_link_or_copy_helper():
    with tempfile.TemporaryDirectory() as t:
        src = Path(t) / "a.mp4"
        src.write_bytes(b"x" * 1000)
        os.utime(src, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
        dst = Path(t) / "b.mp4"
        assert fileio.link_or_copy(src, dst) is True
        assert _same(src, dst) and src.stat().st_nlink == 2
        dst2 = Path(t) / "c.mp4"
        with _no_link():
            assert fileio.link_or_copy(src, dst2) is False
        assert not _same(src, dst2) and dst2.read_bytes() == src.read_bytes()
        assert dst2.stat().st_mtime_ns == src.stat().st_mtime_ns   # copy2：修改時間保留（舊紀錄的過期判斷靠這個）
        # 目的地已經在（例如上次中斷留下的暫存檔）：先拿掉那個名字再連結，不會失敗
        stale = Path(t) / "d.mp4"
        stale.write_bytes(b"half")
        assert fileio.link_or_copy(src, stale) is True and _same(src, stale)
        # 捷徑：連到捷徑指到的那支（複製來的工作區）
        ln = Path(t) / "捷徑.mp4"
        os.symlink(src, ln)
        dst3 = Path(t) / "e.mp4"
        assert fileio.link_or_copy(ln, dst3) is True
        assert not dst3.is_symlink() and _same(src, dst3)


def test_link_or_copy_does_not_overwrite_existing_dst_in_place():
    """審查（1017-2）：目的地已經在、而且是另一支成品的硬連結（例如上次中斷留下的暫存檔）——不能原地覆寫改到那支成品。"""
    with tempfile.TemporaryDirectory() as t:
        v1, v2 = Path(t) / "成品_v1.mp4", Path(t) / "成品_v2.mp4"
        v1.write_bytes(b"v1" * 500)
        v2.write_bytes(b"v2" * 700)
        tmp = Path(t) / ".最終成品.mp4.輸出中"
        os.link(v1, tmp)                                      # 預留的暫存檔連到成品_v1
        assert fileio.link_or_copy(v2, tmp) is True
        assert v1.read_bytes() == b"v1" * 500 and _same(v2, tmp) and v1.stat().st_nlink == 1
        os.link(v1, tmp.with_name("x"))
        tmp2 = tmp.with_name("x")
        with _no_link():                                      # 退回複製那條路也一樣
            assert fileio.link_or_copy(v2, tmp2) is False
        assert v1.read_bytes() == b"v1" * 500 and tmp2.read_bytes() == v2.read_bytes() and v1.stat().st_nlink == 1
        # 同一個檔（同一個路徑）：照舊丟 SameFileError，不會先把它刪掉
        try:
            fileio.link_or_copy(v1, v1)
            raise AssertionError("同一個檔應該丟 SameFileError")
        except shutil.SameFileError:
            pass
        assert v1.read_bytes() == b"v1" * 500


# ---------------------------------------------------------------------------
# 第 5 步「輸出成品」
# ---------------------------------------------------------------------------

def test_export_final_is_hardlink_and_not_stale():
    if not _ffmpeg():
        return
    import test_finalcheck as T

    w = T._workdir()
    prod = w / "輸出" / "成品_0-0_sw.mp4"
    r = _export_final(w)
    fin = w / r["檔案"]
    assert fin.is_file() and not fin.is_symlink()
    assert _same(prod, fin) and prod.stat().st_nlink == 2, (prod.stat().st_nlink, fin.stat().st_nlink)
    assert not list((w / "輸出").glob(".*.輸出中"))                     # 暫存檔換名了
    check = wd.read_json(fc.check_path(w))
    assert check["輸出成品"]["成品檔指紋"] == fc.product_print(prod)
    assert fc.export_stale(w, check) is None                            # 沒過期，拿檔按鈕拿最終成品
    # 再按兩次：同一份資料三個名字，不多佔空間
    r2, r3 = _export_final(w), _export_final(w)
    assert len({r["檔案"], r2["檔案"], r3["檔案"]}) == 3
    assert prod.stat().st_nlink == 4 and all(_same(prod, w / x["檔案"]) for x in (r2, r3))
    assert len(wd.read_json(fc.check_path(w))["輸出紀錄"]) == 3


def test_reassemble_does_not_touch_exported_final():
    """安全前提：組裝寫成品是「寫暫存再換名」，最終成品留著輸出當時的內容；過期判斷照樣說「成品換新了」。"""
    if not _ffmpeg():
        return
    import test_finalcheck as T

    w = T._workdir()
    prod = w / "輸出" / "成品_0-0_sw.mp4"
    before = prod.read_bytes()
    fin = w / _export_final(w)["檔案"]
    _reassemble(prod)
    assert fin.read_bytes() == before and prod.read_bytes() != before
    assert prod.stat().st_nlink == 1 and fin.stat().st_nlink == 1
    stale = fc.export_stale(w, wd.read_json(fc.check_path(w)))
    assert stale and stale["原因"] == fc.STALE_NEWER, stale
    # 舊紀錄（沒有成品檔指紋）那條路：直接比兩個檔，一樣判斷得出過期
    check = wd.read_json(fc.check_path(w))
    check["輸出成品"].pop("成品檔指紋")
    assert (fc.export_stale(w, check) or {}).get("原因") == fc.STALE_NEWER


def test_export_final_falls_back_to_copy():
    if not _ffmpeg():
        return
    import test_finalcheck as T

    w = T._workdir()
    prod = w / "輸出" / "成品_0-0_sw.mp4"
    with _no_link():
        r = _export_final(w)
    fin = w / r["檔案"]
    assert not _same(prod, fin) and prod.stat().st_nlink == 1
    assert fin.read_bytes() == prod.read_bytes() and fin.stat().st_mtime_ns == prod.stat().st_mtime_ns
    check = wd.read_json(fc.check_path(w))
    assert fc.export_stale(w, check) is None
    check["輸出成品"].pop("成品檔指紋")
    assert fc.export_stale(w, check) is None                            # 舊紀錄那條路：複製的修改時間保留，算同一份


def test_label_version_never_exported():
    """標字版是 ffmpeg 直接寫正式檔名（原地改寫），所以它絕不能被連成最終成品：兩支輸出程式都擋（不改，確認）。"""
    import test_needs as tn

    blocks = fc.export_blocks({"輸出": {}}, {}, "輸出/成品_v1_0-3_標字版.mp4", False)
    assert blocks["擋下"], blocks
    w = tn._fresh()
    tn._prod(w, "成品_v1_0-3_標字版.mp4", tn.T1)
    from bookclub import needs

    assert needs.version_video(w, {"成品檔": ["輸出/成品_v1_0-3_標字版.mp4"]}) is None


# ---------------------------------------------------------------------------
# 選單輸出舊版（finalcards.export_version）
# ---------------------------------------------------------------------------

def test_export_version_is_hardlink_and_falls_back():
    import test_versions as tv

    w = tv._three_versions()
    prod = w / "輸出" / "成品_v1_0-3_sw.mp4"
    r = finalcards.export_version(w, 1, confirm=True)
    fin = w / r["檔案"]
    assert r["ok"] and _same(prod, fin) and prod.stat().st_nlink == 2
    assert r["成品檔指紋"] == fc.product_print(prod)
    with _no_link():
        r2 = finalcards.export_version(w, 1, confirm=True)
    fin2 = w / r2["檔案"]
    assert r2["ok"] and not _same(prod, fin2) and fin2.read_bytes() == prod.read_bytes()
    assert prod.stat().st_nlink == 2


# ---------------------------------------------------------------------------
# 空間合計：同一份只算一次
# ---------------------------------------------------------------------------

def _unique_bytes(root: Path) -> int:
    files = {}
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink():
            st = p.stat()
            files[(st.st_dev, st.st_ino)] = st.st_size
    return sum(files.values())


def test_disk_and_clean_total_count_once():
    if not _ffmpeg():
        return
    import test_finalcheck as T

    w = T._workdir()
    prod = w / "輸出" / "成品_0-0_sw.mp4"
    _export_final(w)
    _export_final(w)
    size = prod.stat().st_size
    assert prod.stat().st_nlink == 3
    naive = sum(p.stat().st_size for p in w.rglob("*") if p.is_file())
    # bookclub disk：工作區那一行、合計都是實際佔用（同一份三個名字只算一次）
    with tempfile.TemporaryDirectory() as t:
        home = Path(t) / "家"
        data = home / "讀書會剪輯資料"
        data.mkdir(parents=True)
        wd.write_json(data / "專案清單.json", {"專案": [str(w)]})
        lines = closing.disk_report(home, data, wsl=False)
        real = _unique_bytes(w)
        assert real == naive - 2 * size
        line = next(x for x in lines if "工作區（1 支）" in x)
        assert closing.human(real) in line, (line, real, naive)
        total = next(x for x in lines if x.startswith("合計"))
        assert closing.human(real) in total, total
    # 結案打包框「共 X GB」＝clean_plan 合計：成品＋兩支最終成品都要清，只空出一份
    plan = closing.clean_plan(w, kind="mac")
    names = {r["相對路徑"] for r in plan["檔案"]}
    assert "輸出/成品_0-0_sw.mp4" in names and sum(1 for n in names if n.startswith("輸出/最終成品_")) == 2
    vids = [r for r in plan["檔案"] if r["相對路徑"].startswith(("輸出/成品_", "輸出/最終成品_"))]
    assert sum(r["大小"] for r in vids) == 3 * size
    others = sum(r["大小"] for r in plan["檔案"] if r not in vids)
    assert plan["合計"] == size + others, (plan["合計"], size, others)
    assert closing.clean_estimate(w) == plan["合計"]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
