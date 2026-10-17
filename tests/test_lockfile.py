"""套件鎖定版本（10-17，批次 1017-4，待辦 #241）。

10-16 晚老師從頭重建執行環境，requirements.txt 沒寫版本的 pyarrow 裝到 26.0.0（要 NumPy 2），
工具固定 NumPy 1.26.4 → 第 4 步一載入 CosyVoice（cosyvoice/dataset/processor.py import pyarrow）就失敗；
當時 doctor 只 import cosyvoice.cli.cosyvoice，健檢全過。這裡檢查：
- 有 requirements.lock、版本對（pyarrow 25.0.1、numpy 1.26.4）、沒列 CUDA 套件、requirements.txt 的每個套件都在裡面
- install.sh 照鎖定檔裝
- doctor 會 import 設定檔用到的模組，載入失敗時講白話；已裝版本跟鎖定檔不同時列出來

不跑模型、不碰真的環境：CosyVoice 用假模組模擬。
獨立可跑：.venv/bin/python tests/test_lockfile.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401

import importlib.abc
import importlib.machinery
import re
import sys
import tempfile
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import doctor as dr  # noqa: E402

LOCK = REPO_ROOT / "requirements.lock"


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _lock_pins() -> dict[str, list[str]]:
    pins: dict[str, list[str]] = {}
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"([A-Za-z0-9_.\-]+)==([^\s;]+)", line)
        assert m, f"鎖定檔有一行不是「名稱==版本」：{line}"
        pins.setdefault(_norm(m.group(1)), []).append(m.group(2))
    return pins


# ── 鎖定檔 ───────────────────────────────────────────────


def test_lockfile_exists_and_pins_known_good_versions():
    assert LOCK.is_file(), "缺 requirements.lock"
    pins = _lock_pins()
    assert pins["pyarrow"] == ["25.0.1"], pins.get("pyarrow")
    assert pins["numpy"] == ["1.26.4"], pins.get("numpy")
    assert pins["torch"] == ["2.2.2"] and pins["torchaudio"] == ["2.2.2"]


def test_lockfile_has_no_cuda_packages():
    """沒有 NVIDIA 顯示卡的 Linux 由 install.sh 改裝只用處理器的 torch；鎖定檔列了 nvidia-*／triton 就會又裝回來。"""
    bad = [n for n in _lock_pins() if n.startswith("nvidia-") or n == "triton"]
    assert not bad, bad


def test_lockfile_covers_every_requirement():
    pins = _lock_pins()
    for line in (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name = re.match(r"[A-Za-z0-9_.\-]+", line).group(0)
        assert _norm(name) in pins, f"requirements.txt 的 {name} 沒在鎖定檔裡（改了 requirements.txt 要跑 bash update-lock.sh）"


def test_install_sh_uses_lockfile():
    text = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    installs = [l for l in text.splitlines() if re.match(r"\s*uv pip install\b.*\s-r\s", l)]
    assert installs, "install.sh 找不到 uv pip install -r"
    assert all("-r requirements.lock" in l for l in installs), installs
    # 不能改用 sync：會把 --no-deps 另外裝的 qwen-asr、-e . 的 bookclub 移掉
    assert "uv pip sync" not in text
    assert "--no-deps qwen-asr" in text and "--no-deps -e ." in text


# ── doctor：CosyVoice 設定檔用到的模組都要載入得了 ───────────


_FAKE_TOP = {"cosyvoice", "matcha"}


class _Boom(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """假的 import：指定的模組一載入就丟出 pyarrow 那種錯誤；其他 cosyvoice.*、matcha.* 給空模組。"""

    def __init__(self, broken: str, message: str):
        self.broken = broken
        self.message = message

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in _FAKE_TOP:
            return importlib.machinery.ModuleSpec(fullname, self, is_package=True)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        if module.__name__ == self.broken:
            raise ImportError(self.message)
        module.__path__ = []


def _fake(k: str) -> bool:
    return k.split(".", 1)[0] in _FAKE_TOP


def _with_fake_cosyvoice(broken: str, message: str, fn):
    saved = {k: v for k, v in sys.modules.items() if _fake(k)}
    for k in saved:
        del sys.modules[k]
    finder = _Boom(broken, message)
    sys.meta_path.insert(0, finder)
    import bookclub._cosyvoice_compat as compat

    orig_setup = compat.setup
    compat.setup = lambda: None
    try:
        return fn()
    finally:
        compat.setup = orig_setup
        sys.meta_path.remove(finder)
        for k in [k for k in sys.modules if _fake(k)]:
            del sys.modules[k]
        sys.modules.update(saved)


def test_doctor_cosyvoice_catches_dataset_processor_failure():
    """10-16 那次：cosyvoice.cli.cosyvoice 載入得了，cosyvoice.dataset.processor 因為 pyarrow 載入失敗。"""
    msg = "pyarrow requires NumPy 2.0 or newer, found 1.26.4"
    chk = _with_fake_cosyvoice("cosyvoice.dataset.processor", msg, dr.check_cosyvoice_import)
    assert not chk.ok, chk.line()
    assert chk.required
    assert "聲音生成引擎載入失敗" in chk.detail, chk.detail
    assert "pyarrow" in chk.detail and "NumPy 2" in chk.detail, chk.detail
    assert "install.sh" in chk.fix, chk.fix


def test_doctor_cosyvoice_ok_when_all_modules_load():
    chk = _with_fake_cosyvoice("（沒有壞的）", "", dr.check_cosyvoice_import)
    assert chk.ok, chk.line()


def test_cosyvoice_modules_read_from_model_yaml():
    """設定檔裡 !name:／!new: 引用到的模組都要列進去（取到最後一個點之前）；沒有設定檔時至少有 dataset.processor。"""
    d = Path(tempfile.mkdtemp())
    (d / "cosyvoice3.yaml").write_text(
        "a: !name:cosyvoice.dataset.processor.parquet_opener\n"
        "b: !new:cosyvoice.llm.llm.CosyVoice3LM\n"
        "c: !name:matcha.utils.audio.mel_spectrogram\n"
        "d: !new:omegaconf.DictConfig\n",
        encoding="utf-8",
    )
    mods = dr.cosyvoice_modules(d / "cosyvoice3.yaml")
    assert mods[0] == "cosyvoice.cli.cosyvoice", mods
    for m in ("cosyvoice.dataset.processor", "cosyvoice.llm.llm", "matcha.utils.audio", "omegaconf"):
        assert m in mods, (m, mods)
    fallback = dr.cosyvoice_modules(d / "沒有這個檔.yaml")
    assert "cosyvoice.cli.cosyvoice" in fallback and "cosyvoice.dataset.processor" in fallback


# ── doctor：已裝版本跟鎖定檔不一樣 ─────────────────────────


def test_lock_mismatch_lists_differences():
    lock = Path(tempfile.mkdtemp()) / "requirements.lock"
    lock.write_text(
        "# 註解\n"
        "pyarrow==25.0.1          # via -r requirements.txt\n"
        "numpy==1.26.4\n"
        "torch==2.2.2\n"
        "colorama==0.4.6 ; sys_platform == 'win32'\n"
        "missing-pkg==1.0\n",
        encoding="utf-8",
    )
    installed = {"pyarrow": "26.0.0", "numpy": "1.26.4", "torch": "2.2.2+cpu"}
    diffs = dr.lock_mismatches(lock, installed)
    assert ("pyarrow", "25.0.1", "26.0.0") in diffs, diffs
    assert ("missing-pkg", "1.0", None) in diffs, diffs
    names = [d[0] for d in diffs]
    assert "numpy" not in names and "torch" not in names, diffs     # +cpu 只差本機標記，算一樣
    assert "colorama" not in names, diffs                           # 這台用不到的不算

    chk = dr.check_lock_versions(lock, installed)
    assert not chk.ok and not chk.required, chk.line()
    assert "pyarrow 26.0.0（鎖定 25.0.1）" in chk.detail, chk.detail
    assert "install.sh" in chk.fix

    ok = dr.check_lock_versions(lock, {**installed, "pyarrow": "25.0.1", "missing-pkg": "1.0"})
    assert ok.ok, ok.line()


def test_lock_check_in_doctor_run():
    names = [c.name for c in dr.run_checks(include_claude_call=False)]
    assert "套件版本跟鎖定檔一致" in names, names


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"通過：{t.__name__}")
    print(f"\n共 {len(tests)} 個測試通過")


if __name__ == "__main__":
    _run_all()
