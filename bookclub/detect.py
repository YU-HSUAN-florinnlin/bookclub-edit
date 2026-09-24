"""偵測電腦裡已經有哪些工具／原始碼／模型，安裝前後都能用。

**設計理由**：合作夥伴的電腦上可能已經照別份說明文件裝過 CosyVoice（原始碼＋
模型權重約 7GB），如果 `install.sh` 只檢查這個倉庫自己的位置（`third_party/
CosyVoice`、`~/.cache/bookclub/...`），會重新下載、重新 clone，浪費時間與空間。
這支模組負責「電腦裡到底有沒有」，`install.sh`（安裝前）與 `bookclub doctor`
（安裝後）共用同一份邏輯，不要各寫一份。

**唯一的限制**：這個模組本身不能依賴任何要另外 `pip install` 的套件（不 import
torch、huggingface_hub 等），也不能在模組最上層 import `bookclub.config`／
`bookclub.models`——因為 `install.sh` 會在虛擬環境還不存在時，用「系統 python3」
執行這支檔案，而系統 python3 版本不保證 ≥3.11（`bookclub.config` 用到只有
3.11 才有的 `tomllib`）。所有跟 bookclub 其他模組有關的 import 一律寫在函式
內部、包 try/except，讀不到就退回這裡自己複製的預設值。

單獨執行：
    python3 bookclub/detect.py                  # 人看的文字報告
    python3 bookclub/detect.py --json            # 給程式讀的 JSON
    python3 bookclub/detect.py --shell           # 文字報告印到 stderr，
                                                  # DETECT_* 變數印到 stdout，
                                                  # 給 install.sh eval 用
    python3 bookclub/detect.py --verify-cosyvoice-model <路徑>
                                                  # 檢查某資料夾的 CosyVoice3
                                                  # 模型檔案齊不齊全、大小合不合理，
                                                  # 印一行說明、回傳碼 0/1
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ── 跟 bookclub/config.py、bookclub/models.py 的預設值保持一致（見上方模組
# 說明：這裡不能直接 import 那兩個模組，怕系統 python3 版本太舊） ──────────
DEFAULT_COSYVOICE_MODEL_DIR = "~/.cache/bookclub/Fun-CosyVoice3-0.5B"
COSYVOICE_SHA_TESTED = "074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc"  # install.sh 裡也有一份，兩邊改要一起改

# CosyVoice3 推論用得到的關鍵檔案：llm.pt／llm.rl.pt 兩者有一個就算數（新舊
# 版本 CosyVoice 的檔名可能不同），其餘四個都要有。數字是「合理大小」的下限，
# 抓得比實際檔案小很多，只用來抓「檔案是空的／下載到一半」這種情形。
COSYVOICE_LLM_CANDIDATES = ["llm.pt", "llm.rl.pt"]
COSYVOICE_OTHER_KEY_FILES: dict[str, int] = {
    "flow.pt": 100_000_000,
    "hift.pt": 10_000_000,
    "campplus.onnx": 5_000_000,
    "speech_tokenizer_v3.onnx": 100_000_000,
}
COSYVOICE_LLM_MIN_SIZE = 200_000_000

PYANNOTE_REPOS = [
    "pyannote/segmentation-3.0",
    "pyannote/speaker-diarization-3.1",
    "pyannote/wespeaker-voxceleb-resnet34-LM",
]
PYANNOTE_CONFIG_FILENAME = "config.yaml"

# 掃 $HOME 找 CosyVoice／conda 環境時，這些資料夾跳過不進去（系統資料夾、
# 常見的巨大快取／套件庫，進去只會拖慢速度，也不可能是使用者自己放的東西）。
_SCAN_SKIP_NAMES = {
    "Library", "Applications", "Pictures", "Movies", "Music",
    "node_modules", ".cache", ".npm", ".cargo", ".rustup", ".docker",
    ".orbstack", ".Trash", ".Spotlight-V100", ".fseventsd", ".DocumentRevisions-V100",
    "Photos Library.photoslibrary", "不給AI",
}


@dataclass
class DetectedItem:
    """一項偵測結果：一種工具、一份原始碼、一份模型檔案……"""

    name: str
    found: bool
    path: str = ""
    detail: str = ""
    is_default: bool = False  # 是不是「倉庫自己的／預設位置」，resolve_* 用來排序
    commit: str = ""


# ── 共用小工具 ───────────────────────────────────────────────


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _run(cmd: list[str], timeout: float = 5) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (out.stdout or out.stderr or "").strip()
    except Exception:
        return None


def _first_line(s: str | None) -> str:
    if not s:
        return ""
    lines = s.splitlines()
    return lines[0].strip() if lines else ""


def _fmt_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024
    return f"{size:.1f}TB"


def _git_commit(path: Path, timeout: float = 5) -> str | None:
    if not (path / ".git").exists():
        return None
    out = _run(["git", "-C", str(path), "rev-parse", "HEAD"], timeout=timeout)
    if out and len(out) >= 7 and all(c in "0123456789abcdef" for c in out.lower()):
        return out
    return None


def _cosyvoice_model_dir_from_settings() -> Path:
    """嘗試讀 `~/讀書會剪輯資料/settings.toml` 裡覆寫的模型路徑；讀不到（檔案
    不存在、系統 python3 沒有 tomllib、格式不對……）一律退回內建預設值，不
    當掉。安裝前這個檔案本來就還沒建立，是預期情形。"""
    try:
        data_dir_override = os.environ.get("BOOKCLUB_DATA_DIR")
        data_dir = Path(data_dir_override).expanduser() if data_dir_override else Path.home() / "讀書會剪輯資料"
        settings_file = data_dir / "settings.toml"
        if settings_file.is_file():
            import tomllib  # Python 3.11+ 才有；讀不到就落到下面的 except

            with open(settings_file, "rb") as f:
                raw = tomllib.load(f)
            override = raw.get("paths", {}).get("cosyvoice_model_dir")
            if override:
                return Path(override).expanduser()
    except Exception:
        pass
    return Path(DEFAULT_COSYVOICE_MODEL_DIR).expanduser()


def _should_skip_dir(name: str) -> bool:
    return name.startswith(".") or name in _SCAN_SKIP_NAMES


def _scan_home_for_name(keyword: str, max_depth: int = 2) -> list[Path]:
    """在 `$HOME` 底下找資料夾名稱含 `keyword`（不分大小寫）的目錄，最多往下
    `max_depth` 層。刻意不用 os.walk 遞迴整棵樹（$HOME 底下常有巨大資料夾），
    只逐層列目錄名稱，遇到存取被拒或其他錯誤就跳過那個資料夾。"""
    home = Path.home()
    keyword_lower = keyword.lower()
    found: list[Path] = []
    try:
        depth1 = [p for p in home.iterdir() if p.is_dir() and not _should_skip_dir(p.name)]
    except OSError:
        return found

    for d1 in depth1:
        if keyword_lower in d1.name.lower():
            found.append(d1)
            continue  # 已經是候選，不用再往下找它裡面的東西
        if max_depth < 2:
            continue
        try:
            for d2 in d1.iterdir():
                if d2.is_dir() and not _should_skip_dir(d2.name) and keyword_lower in d2.name.lower():
                    found.append(d2)
        except OSError:
            continue
    return found


def _hf_cache_root() -> Path:
    override = os.environ.get("HF_HOME")
    if override:
        return Path(override).expanduser() / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _hf_latest_snapshot(cache_root: Path, repo_dirname: str) -> Path | None:
    repo_dir = cache_root / repo_dirname
    snaps = repo_dir / "snapshots"
    if not snaps.is_dir():
        return None
    try:
        revs = [r for r in snaps.iterdir() if r.is_dir()]
    except OSError:
        return None
    if not revs:
        return None
    revs.sort(key=lambda r: r.stat().st_mtime if r.exists() else 0, reverse=True)
    return revs[0]


# ── 驗證：一個資料夾裡的 CosyVoice3 模型檔案是否齊全、大小合理 ──────────────


def verify_cosyvoice_model_dir(path: Path) -> tuple[bool, str]:
    """檢查依據：關鍵檔案存在且大小合理，不是只看資料夾在不在。`llm.pt` 或
    `llm.rl.pt` 有一個就算數，其餘四個都要有。檔案可以是真的檔案，也可以是
    連結（例如 `llm.pt -> llm.rl.pt`，或整個資料夾是連結到別處）——`Path.stat()`
    會跟著連結走，量到的是實際檔案大小。"""
    if not path.is_dir():
        return False, f"{path} 不存在"

    def _size_of(name: str) -> int | None:
        f = path / name
        try:
            if not f.exists():  # exists() 會跟著連結走，斷掉的連結回傳 False
                return None
            return f.stat().st_size
        except OSError:
            return None

    llm_ok_name = None
    llm_size = 0
    for name in COSYVOICE_LLM_CANDIDATES:
        size = _size_of(name)
        if size is not None and size >= COSYVOICE_LLM_MIN_SIZE:
            llm_ok_name, llm_size = name, size
            break

    missing = []
    too_small = []
    for name, min_size in COSYVOICE_OTHER_KEY_FILES.items():
        size = _size_of(name)
        if size is None:
            missing.append(name)
        elif size < min_size:
            too_small.append(f"{name}（只有 {_fmt_size(size)}）")

    problems = []
    if llm_ok_name is None:
        problems.append("缺 llm.pt／llm.rl.pt（或檔案太小，可能下載到一半）")
    if missing:
        problems.append("缺：" + "、".join(missing))
    if too_small:
        problems.append("檔案大小異常：" + "、".join(too_small))

    if problems:
        return False, "；".join(problems)

    detail = f"{llm_ok_name}（{_fmt_size(llm_size)}）、flow.pt、hift.pt、campplus.onnx、speech_tokenizer_v3.onnx 都在，大小合理"
    return True, detail


# ── 各分類偵測 ───────────────────────────────────────────────


def _tool_item(name: str, cmd: str, version_args: list[str]) -> DetectedItem:
    path = shutil.which(cmd)
    if not path:
        return DetectedItem(name, False, detail="沒有找到")
    detail = _first_line(_run([cmd, *version_args])) or "版本未知"
    return DetectedItem(name, True, path=path, detail=detail)


def _detect_conda() -> DetectedItem:
    conda_exe = shutil.which("conda")
    if not conda_exe:
        for cand in (
            Path.home() / "miniforge3" / "bin" / "conda",
            Path.home() / "miniconda3" / "bin" / "conda",
            Path.home() / "anaconda3" / "bin" / "conda",
        ):
            if cand.is_file():
                conda_exe = str(cand)
                break
    if not conda_exe:
        return DetectedItem("conda／miniforge", False, detail="沒有找到")
    ver = _first_line(_run([conda_exe, "--version"])) or "版本未知"
    return DetectedItem("conda／miniforge", True, path=conda_exe, detail=ver)


def detect_system_tools() -> list[DetectedItem]:
    items = [
        _tool_item("ffmpeg", "ffmpeg", ["-version"]),
        _tool_item("ffprobe", "ffprobe", ["-version"]),
        _tool_item("sox", "sox", ["--version"]),
        _tool_item("git", "git", ["--version"]),
        _tool_item("uv", "uv", ["--version"]),
        _detect_conda(),
    ]
    py_path = shutil.which("python3") or sys.executable
    items.append(
        DetectedItem(
            "python3（系統）", True, path=py_path,
            detail=f"{platform.python_version()}（{platform.machine()}）",
        )
    )
    return items


def detect_cosyvoice_sources() -> list[DetectedItem]:
    """倉庫自己的 `third_party/CosyVoice` 排第一個（不論有沒有找到，都會列出
    一行），接著是 `~/CosyVoice`、`~/cosyvoice`，最後是 `$HOME` 底下深度 2 層
    內名稱含 CosyVoice 的資料夾。同一個實際路徑只列一次。"""
    repo_dir = _repo_root() / "third_party" / "CosyVoice"

    candidates: list[tuple[Path, bool]] = [(repo_dir, True)]  # (路徑, 是否為倉庫自己的位置)
    seen = {repo_dir.resolve()} if repo_dir.exists() else set()

    def _add(p: Path) -> None:
        key = p.resolve() if p.exists() else p
        if key in seen:
            return
        seen.add(key)
        candidates.append((p, False))

    for extra in (Path.home() / "CosyVoice", Path.home() / "cosyvoice"):
        _add(extra)
    for found in _scan_home_for_name("cosyvoice", max_depth=2):
        _add(found)

    items = []
    for p, is_default in candidates:
        label = "CosyVoice 原始碼" + ("（倉庫內建位置）" if is_default else "（外部）")
        if not p.is_dir():
            items.append(DetectedItem(label, False, path=str(p), detail="不存在"))
            continue
        commit = _git_commit(p)
        where = "倉庫自己 clone 進來的" if is_default else "外部位置，可沿用"
        detail = f"{where}，commit {commit[:12]}" if commit else f"{where}，不是 git 倉庫或看不到 commit"
        items.append(DetectedItem(label, True, path=str(p.resolve()), detail=detail, is_default=is_default, commit=commit or ""))
    return items


def detect_cosyvoice_models() -> list[DetectedItem]:
    """候選來源依序：settings.toml／預設的模型路徑（倉庫預設位置）→ Hugging
    Face 快取（`models--FunAudioLLM--*`）→ 前面找到的 CosyVoice 原始碼底下
    的 `pretrained_models/*`。每個候選都用 `verify_cosyvoice_model_dir` 驗證，
    「找到」的意思是「檔案齊全、大小合理」，不是只看資料夾存不存在。"""
    default_dir = _cosyvoice_model_dir_from_settings()
    candidates: list[tuple[Path, bool, str]] = [(default_dir, True, "倉庫預設／settings.toml 指定的位置")]
    seen = {default_dir.resolve()} if default_dir.exists() else set()

    def _add(p: Path, note: str) -> None:
        key = p.resolve() if p.exists() else p
        if key in seen:
            return
        seen.add(key)
        candidates.append((p, False, note))

    for snap in sorted(_hf_cache_root().glob("models--FunAudioLLM--*")):
        latest = _hf_latest_snapshot(_hf_cache_root(), snap.name)
        if latest:
            _add(latest, "Hugging Face 快取")

    for src_item in detect_cosyvoice_sources():
        if not src_item.found:
            continue
        pm = Path(src_item.path) / "pretrained_models"
        if not pm.is_dir():
            continue
        try:
            children = sorted(c for c in pm.iterdir() if c.is_dir())
        except OSError:
            children = []
        for child in children:
            _add(child, f"{src_item.path} 底下的 pretrained_models")

    items = []
    for p, is_default, note in candidates:
        ok, detail = verify_cosyvoice_model_dir(p)
        label = "CosyVoice3 模型權重" + ("（倉庫預設位置）" if is_default else f"（{note}）")
        items.append(DetectedItem(label, ok, path=str(p), detail=detail, is_default=is_default))
    return items


def detect_pyannote_models() -> list[DetectedItem]:
    cache_dir = Path(os.getenv("PYANNOTE_CACHE", str(Path.home() / ".cache" / "torch" / "pyannote")))
    items = []
    for repo in PYANNOTE_REPOS:
        dirname = "models--" + repo.replace("/", "--")
        found_path = _hf_latest_snapshot(cache_dir, dirname)
        if found_path is not None and (found_path / PYANNOTE_CONFIG_FILENAME).is_file():
            items.append(DetectedItem(f"pyannote：{repo}", True, path=str(found_path), detail="已在本機快取"))
        else:
            items.append(DetectedItem(f"pyannote：{repo}", False, detail="還沒下載"))
    return items


def detect_silero_vad() -> DetectedItem:
    """silero-vad 是 pip 套件，模型檔案內建在套件裡（不是另外下載），所以只
    要確認「目前這個 python 有沒有裝這個套件」就等於「有沒有這個模型」。"""
    try:
        import importlib.metadata as importlib_metadata

        version = importlib_metadata.version("silero-vad")
    except Exception:
        return DetectedItem("Silero VAD", False, detail="目前這個 python 環境還沒裝 silero-vad（會隨 requirements.txt 一起安裝）")

    try:
        import silero_vad

        pkg_dir = str(Path(silero_vad.__file__).resolve().parent)
    except Exception:
        pkg_dir = ""
    return DetectedItem("Silero VAD", True, path=pkg_dir, detail=f"pip 套件 silero-vad {version}（模型檔案內建在套件裡）")


def _list_conda_envs(conda_exe: str) -> list[tuple[str, str]]:
    out = _run([conda_exe, "env", "list", "--json"], timeout=10)
    if not out:
        return []
    try:
        data = json.loads(out)
    except Exception:
        return []
    envs = []
    for p in data.get("envs", []):
        envs.append((Path(p).name, p))
    return envs


def detect_python_envs() -> list[DetectedItem]:
    """只報告，不自動使用——要不要沿用既有的 Python 環境是使用者的決定。"""
    items = []
    venv_dir = _repo_root() / ".venv"
    venv_py = venv_dir / "bin" / "python"
    if venv_py.is_file():
        ver = _run([str(venv_py), "-c", "import sys,platform;print(f'{sys.version_info[0]}.{sys.version_info[1]} {platform.machine()}')"])
        items.append(DetectedItem("倉庫虛擬環境 .venv", True, path=str(venv_dir), detail=(ver or "").strip() or "存在"))
    else:
        items.append(DetectedItem("倉庫虛擬環境 .venv", False, detail="還沒建立"))

    conda_item = _detect_conda()
    if conda_item.found:
        for name, env_path in _list_conda_envs(conda_item.path):
            flag = "；名稱含 cosyvoice，可能跟聲音生成有關" if "cosyvoice" in name.lower() else ""
            items.append(DetectedItem(f"conda 環境：{name}", True, path=env_path, detail=f"只回報、不自動使用{flag}"))
    return items


# ── 目前實際在用的是哪裡（給 `bookclub doctor` 用，跟著 config.py 的解析走）─


def describe_current_cosyvoice_source() -> DetectedItem:
    p = _repo_root() / "third_party" / "CosyVoice"
    if not p.exists():
        return DetectedItem("CosyVoice 原始碼（目前使用）", False, detail=f"{p} 不存在")
    resolved = p.resolve()
    commit = _git_commit(p)
    if p.is_symlink():
        detail = f"沿用外部：{resolved}"
    else:
        detail = "倉庫自己 clone 進來的"
    if commit:
        detail += f"，commit {commit[:12]}"
        if commit != COSYVOICE_SHA_TESTED:
            detail += f"（跟測過的 {COSYVOICE_SHA_TESTED[:12]} 不同，供排錯參考）"
    return DetectedItem("CosyVoice 原始碼（目前使用）", True, path=str(resolved), detail=detail)


def describe_current_cosyvoice_model() -> DetectedItem:
    try:
        from bookclub.config import cosyvoice_model_dir

        p = cosyvoice_model_dir()
    except Exception:
        p = _cosyvoice_model_dir_from_settings()
    ok, detail = verify_cosyvoice_model_dir(p)
    where = f"沿用外部：{p.resolve()}｜" if p.is_symlink() else ""
    return DetectedItem("CosyVoice 模型（目前使用）", ok, path=str(p), detail=f"{where}{detail}")


# ── 給 install.sh 用：建議沿用哪一個候選 ────────────────────────────


def resolve_cosyvoice_source() -> DetectedItem | None:
    found = [i for i in detect_cosyvoice_sources() if i.found]
    return found[0] if found else None


def resolve_cosyvoice_model() -> DetectedItem | None:
    found = [i for i in detect_cosyvoice_models() if i.found]
    return found[0] if found else None


def _is_inside_repo(path: str | None) -> bool:
    """路徑在這個倉庫底下嗎（倉庫自己的東西不算「外部已有」，不需要 symlink）。"""
    if not path:
        return False
    try:
        Path(path).resolve().relative_to(_repo_root().resolve())
        return True
    except (ValueError, OSError):
        return False


def external_cosyvoice_source() -> str | None:
    """電腦裡「倉庫以外」已經有的 CosyVoice 原始碼，沒有就回傳 None。"""
    for item in detect_cosyvoice_sources():
        if item.found and not _is_inside_repo(item.path):
            return item.path
    return None


def external_cosyvoice_model() -> str | None:
    """電腦裡「倉庫預設模型位置以外」已經有的 CosyVoice3 模型權重，沒有就回傳 None。"""
    default = Path(DEFAULT_COSYVOICE_MODEL_DIR).expanduser().resolve()
    for item in detect_cosyvoice_models():
        if not item.found or not item.path:
            continue
        try:
            if Path(item.path).resolve() == default:
                continue
        except OSError:
            continue
        return item.path
    return None


# ── 彙整與輸出 ───────────────────────────────────────────────


def full_report() -> "OrderedDict[str, list[DetectedItem]]":
    sections: "OrderedDict[str, list[DetectedItem]]" = OrderedDict()
    sections["系統工具"] = detect_system_tools()
    sections["CosyVoice 原始碼"] = detect_cosyvoice_sources()
    sections["CosyVoice3 模型權重"] = detect_cosyvoice_models()
    sections["pyannote 聲紋／分辨說話者模型"] = detect_pyannote_models()
    sections["Silero VAD"] = [detect_silero_vad()]
    sections["Python 環境"] = detect_python_envs()
    return sections


def format_report_text(sections: "OrderedDict[str, list[DetectedItem]]" | None = None) -> str:
    sections = sections if sections is not None else full_report()
    lines = ["電腦裡已經有的環境（偵測結果，已經有的會沿用、不重複安裝）：", ""]
    for title, items in sections.items():
        lines.append(f"── {title} ──")
        for it in items:
            mark = "✅ 已找到" if it.found else "⬜ 沒有，會安裝／下載"
            loc = f"（{it.path}）" if it.path else ""
            detail = f"：{it.detail}" if it.detail else ""
            lines.append(f"{mark}　{it.name}{loc}{detail}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _report_as_dict() -> dict:
    sections = full_report()
    return {title: [asdict(it) for it in items] for title, items in sections.items()}


def _sh_quote(s: str) -> str:
    return "'" + (s or "").replace("'", "'\\''") + "'"


def format_report_shell() -> str:
    src = resolve_cosyvoice_source()
    model = resolve_cosyvoice_model()
    lines = [
        f"DETECT_COSYVOICE_SRC_FOUND={1 if src else 0}",
        f"DETECT_COSYVOICE_SRC_IS_DEFAULT={1 if (src and src.is_default) else 0}",
        f"DETECT_COSYVOICE_SRC_PATH={_sh_quote(src.path if src else '')}",
        f"DETECT_COSYVOICE_SRC_COMMIT={_sh_quote((src.commit if src else '') or '')}",
        f"DETECT_COSYVOICE_MODEL_FOUND={1 if model else 0}",
        f"DETECT_COSYVOICE_MODEL_IS_DEFAULT={1 if (model and model.is_default) else 0}",
        f"DETECT_COSYVOICE_MODEL_PATH={_sh_quote(model.path if model else '')}",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bookclub detect", description="偵測電腦裡已經有的工具與模型")
    parser.add_argument("--json", action="store_true", help="印出 JSON 格式的偵測報告")
    parser.add_argument("--shell", action="store_true", help="文字報告印到 stderr，DETECT_* 變數印到 stdout（給 install.sh eval 用）")
    parser.add_argument("--verify-cosyvoice-model", metavar="PATH", help="檢查指定資料夾的 CosyVoice3 模型檔案齊不齊全、大小合不合理")
    parser.add_argument("--external-cosyvoice-source", action="store_true", help="印出倉庫以外已有的 CosyVoice 原始碼路徑（沒有就不印）")
    parser.add_argument("--external-cosyvoice-model", action="store_true", help="印出預設位置以外已有的 CosyVoice3 模型路徑（沒有就不印）")
    args = parser.parse_args(argv)

    if args.external_cosyvoice_source:
        path = external_cosyvoice_source()
        if path:
            print(path)
        return 0

    if args.external_cosyvoice_model:
        path = external_cosyvoice_model()
        if path:
            print(path)
        return 0

    if args.verify_cosyvoice_model:
        ok, detail = verify_cosyvoice_model_dir(Path(args.verify_cosyvoice_model).expanduser())
        print(detail)
        return 0 if ok else 1

    if args.json:
        print(json.dumps(_report_as_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.shell:
        sys.stderr.write(format_report_text())
        sys.stdout.write(format_report_shell())
        return 0

    sys.stdout.write(format_report_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
