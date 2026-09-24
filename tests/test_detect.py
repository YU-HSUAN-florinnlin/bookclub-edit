"""bookclub/detect.py 的單元測試：只驗判斷邏輯，不下載任何東西、不碰真的模型。

獨立可跑：.venv/bin/python tests/test_detect.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import detect as dt


def _make_model_dir(root: Path, *, llm_name: str = "llm.pt", shrink: str | None = None) -> Path:
    """做一個假的模型資料夾：檔案大小用稀疏檔寫到門檻以上，不會真的佔空間。"""
    root.mkdir(parents=True, exist_ok=True)
    sizes = {llm_name: dt.COSYVOICE_LLM_MIN_SIZE + 1}
    sizes.update({k: v + 1 for k, v in dt.COSYVOICE_OTHER_KEY_FILES.items()})
    for name, size in sizes.items():
        if shrink == name:
            size = 10  # 故意做成「下載到一半」的小檔
        with open(root / name, "wb") as f:
            f.truncate(size)
    return root


def test_verify_model_dir_ok():
    tmp = Path(tempfile.mkdtemp())
    try:
        ok, detail = dt.verify_cosyvoice_model_dir(_make_model_dir(tmp / "模型"))
        assert ok, detail
        assert "llm.pt" in detail
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_verify_model_dir_accepts_rl_variant():
    tmp = Path(tempfile.mkdtemp())
    try:
        ok, _ = dt.verify_cosyvoice_model_dir(_make_model_dir(tmp / "模型", llm_name="llm.rl.pt"))
        assert ok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_verify_model_dir_missing_file():
    tmp = Path(tempfile.mkdtemp())
    try:
        d = _make_model_dir(tmp / "模型")
        (d / "flow.pt").unlink()
        ok, detail = dt.verify_cosyvoice_model_dir(d)
        assert not ok and "flow.pt" in detail
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_verify_model_dir_truncated_file():
    """檔案在、但大小明顯不對（下載到一半）也要擋下來。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        d = _make_model_dir(tmp / "模型", shrink="hift.pt")
        ok, detail = dt.verify_cosyvoice_model_dir(d)
        assert not ok and "hift.pt" in detail
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_verify_model_dir_not_exist():
    ok, detail = dt.verify_cosyvoice_model_dir(Path("/不存在的資料夾/模型"))
    assert not ok and detail


def test_is_inside_repo():
    assert dt._is_inside_repo(str(REPO_ROOT / "third_party" / "CosyVoice"))
    assert not dt._is_inside_repo(str(Path.home() / "CosyVoice"))
    assert not dt._is_inside_repo(None)
    assert not dt._is_inside_repo("")


def test_external_source_skips_repo_copy():
    """倉庫自己的 CosyVoice 不算「外部已有」，不該建 symlink 指向自己。"""
    inside = dt.DetectedItem("倉庫內建", True, path=str(REPO_ROOT / "third_party" / "CosyVoice"))
    outside = dt.DetectedItem("外部", True, path=str(Path.home() / "CosyVoice"))
    orig = dt.detect_cosyvoice_sources
    try:
        dt.detect_cosyvoice_sources = lambda: [inside, outside]  # type: ignore[assignment]
        assert dt.external_cosyvoice_source() == outside.path
        dt.detect_cosyvoice_sources = lambda: [inside]  # type: ignore[assignment]
        assert dt.external_cosyvoice_source() is None
    finally:
        dt.detect_cosyvoice_sources = orig  # type: ignore[assignment]


def test_external_model_skips_default_dir():
    default = str(Path(dt.DEFAULT_COSYVOICE_MODEL_DIR).expanduser())
    orig = dt.detect_cosyvoice_models
    try:
        dt.detect_cosyvoice_models = lambda: [dt.DetectedItem("預設位置", True, path=default)]  # type: ignore[assignment]
        assert dt.external_cosyvoice_model() is None
        elsewhere = str(Path.home() / "CosyVoice" / "pretrained_models" / "Fun-CosyVoice3-0.5B")
        dt.detect_cosyvoice_models = lambda: [  # type: ignore[assignment]
            dt.DetectedItem("預設位置", False, path=default),
            dt.DetectedItem("外部", True, path=elsewhere),
        ]
        assert dt.external_cosyvoice_model() == elsewhere
    finally:
        dt.detect_cosyvoice_models = orig  # type: ignore[assignment]


def test_report_sections_present():
    sections = dt.full_report()
    for key in ("系統工具", "CosyVoice 原始碼", "CosyVoice3 模型權重", "pyannote 聲紋／分辨說話者模型", "Python 環境"):
        assert key in sections
    text = dt.format_report_text(sections)
    assert "系統工具" in text and text.strip()


def test_cli_verify_returns_exit_code():
    tmp = Path(tempfile.mkdtemp())
    try:
        assert dt.main(["--verify-cosyvoice-model", str(_make_model_dir(tmp / "模型"))]) == 0
        assert dt.main(["--verify-cosyvoice-model", str(tmp / "沒有這個")]) == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"通過：{t.__name__}")
    print(f"\n共 {len(tests)} 個測試通過")


if __name__ == "__main__":
    _run_all()
