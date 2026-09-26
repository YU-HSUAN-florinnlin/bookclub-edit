"""bookclub/profile.py（初始化設定、設定包）的測試：全部用暫存資料夾，不碰真的 ~/讀書會剪輯資料/。

獨立可跑：.venv/bin/python tests/test_profile.py
"""

from __future__ import annotations

import csv
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import profile  # noqa: E402

ROSTER = "中文名,其他寫法,英文代號,聲線,性別\n王小美,小美,Amy,,女\n林阿明,,Tom,,男\n"


def _dir(files: dict[str, str]) -> Path:
    d = Path(tempfile.mkdtemp())
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    return d


def _rows(p: Path) -> list[dict]:
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def test_summary_shows_codes_not_names():
    d = _dir({"名冊.csv": ROSTER, "敏感詞.csv": "原詞,替代詞\n某公司,一家公司\n"})
    s = profile.summary(d)
    assert s["檔案"]["名冊.csv"]["筆數"] == 2 and s["檔案"]["名冊.csv"]["代號"] == ["Amy", "Tom"]
    assert "王小美" not in str(s) and "小美" not in str(s)
    assert s["檔案"]["發音對照表.csv"]["有檔案"] is False and s["匿名聲線"]["狀態"] == "還沒做"


def test_export_then_import_merges_and_lists_conflicts():
    a = _dir({"名冊.csv": ROSTER, "敏感詞.csv": "原詞,替代詞\n某公司,一家公司\n",
              "發音對照表.csv": "原字,生成用,原因,建立日期\n愉快,魚快,念偏,2026-09-25\n",
              "settings.toml": "[server]\nport = 8766\n"})
    out = profile.export_profile(a / "包.zip", root=a)
    with zipfile.ZipFile(out["檔案"]) as z:
        assert set(z.namelist()) == {"名冊.csv", "敏感詞.csv", "發音對照表.csv", "settings.toml", "說明.txt"}
    # 夥伴的電腦：名冊有一筆代號不同（衝突）、少一筆（新增）、敏感詞多一筆本機的；沒有結尾換行
    b = _dir({"名冊.csv": "中文名,其他寫法,英文代號,聲線,性別\n王小美,小美,Amelia,,女",
              "敏感詞.csv": "原詞,替代詞\n別家,另一家\n", "settings.toml": "[server]\nport = 9000\n"})
    r = profile.import_profile(out["檔案"], root=b)
    f = r["檔案"]
    assert f["名冊.csv"]["新增"] == 1 and len(f["名冊.csv"]["衝突"]) == 1
    c = f["名冊.csv"]["衝突"][0]
    assert c["鑰匙"] == "Amelia" and "王小美" not in str(c)                # 衝突用代號標示，不顯示本名
    roster = _rows(b / "名冊.csv")
    assert [x["英文代號"] for x in roster] == ["Amelia", "Tom"]              # 本機的保留、新的加在後面
    assert [x["原詞"] for x in _rows(b / "敏感詞.csv")] == ["別家", "某公司"]
    assert _rows(b / "發音對照表.csv")[0]["生成用"] == "魚快"                # 本機沒有的檔案整份放進來
    assert f["settings.toml"]["衝突"] and "9000" in (b / "settings.toml").read_text()
    # 再匯入一次：沒有新增，衝突一樣
    r2 = profile.import_profile(out["檔案"], root=b)
    assert r2["新增"] == 0 and r2["衝突數"] == r["衝突數"]


def test_import_rejects_non_profile_zip():
    d = Path(tempfile.mkdtemp())
    z = d / "x.zip"
    with zipfile.ZipFile(z, "w") as zz:
        zz.writestr("別的.txt", "x")
    try:
        profile.import_profile(z, root=d)
        raise AssertionError
    except ValueError:
        pass


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
