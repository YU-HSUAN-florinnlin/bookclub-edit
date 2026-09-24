"""設定檔與資料夾路徑。

這個工具倉庫本身（bookclub-edit/）只放程式；老師的實際資料（名冊、聲音樣本、
影片、設定）放在另一個資料夾 `~/讀書會剪輯資料/`，不進 git 倉庫，理由是那些
是私人資料，這個倉庫之後會公開。兩邊分開放的細節見 README。

這個檔案負責回答「資料放在哪」「門檻設多少」這類問題，其他程式要用設定時都
從這裡問，不要自己組路徑或寫死數字。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DEFAULT_COSYVOICE_MODEL_DIR = "~/.cache/bookclub/Fun-CosyVoice3-0.5B"
DEFAULT_ALIGNER_MODEL_ID = "Qwen/Qwen3-ForcedAligner-0.6B"
DEFAULT_SERVER_PORT = 8766


def repo_root() -> Path:
    """這個工具倉庫本身的根目錄（bookclub-edit/ 所在位置）。"""
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """老師專案資料放的地方。

    預設是 `~/讀書會剪輯資料`；可以用環境變數 `BOOKCLUB_DATA_DIR` 換位置
    （例如測試時指到別的資料夾，不動到真正的老師資料）。
    """
    override = os.environ.get("BOOKCLUB_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "讀書會剪輯資料"


@dataclass
class ClaudeModels:
    """呼叫 `claude -p` 時用哪個模型。寫在設定檔裡，程式不寫死，之後要換模型
    只改 settings.toml 就好。"""

    punctuation: str = "sonnet"  # 標點還原：規則清楚、量大，用快速便宜的模型
    naming: str = "opus"  # 名字候選判斷：需要判斷語意，用能力較強的模型


@dataclass
class Thresholds:
    """幾個影響覆核與自動判斷的門檻值。"""

    length_tolerance: float = 0.15  # 生成的聲音比原本時間格長多少比例以內，用微調語速處理
    echo_overlap_max_s: float = 0.5  # 重疊在這秒數以內、且是「嗯、對、好」這類附和詞才自動過濾
    name_match_relaxed: bool = True  # 名字比對要不要放寬（同音字也算候選）


@dataclass
class Paths:
    """模型放在哪裡。"""

    cosyvoice_model_dir: str = DEFAULT_COSYVOICE_MODEL_DIR
    aligner_model_id: str = DEFAULT_ALIGNER_MODEL_ID


@dataclass
class Overlap:
    """找重疊（步驟「分辨說話者」）的參數。"""

    scan_pad_s: float = 3.0  # 只在學員／不確定的句子前後各留這麼多秒的範圍跑 pyannote，對應 02 規格的 overlap_scan_pad_s


@dataclass
class Settings:
    server_port: int = DEFAULT_SERVER_PORT
    claude_models: ClaudeModels = field(default_factory=ClaudeModels)
    thresholds: Thresholds = field(default_factory=Thresholds)
    paths: Paths = field(default_factory=Paths)
    overlap: Overlap = field(default_factory=Overlap)


def settings_path() -> Path:
    return data_dir() / "settings.toml"


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """讀 `~/讀書會剪輯資料/settings.toml`。

    檔案不存在時不會噴錯誤——印一行提示、回傳內建預設值，讓 `bookclub doctor`
    這類指令在還沒安裝好資料夾的情況下也能跑（會告訴使用者少了什麼，而不是
    直接當掉）。同一次執行只會讀一次（用 lru_cache 記住結果），重複呼叫不會
    重複印提示，也不會一直重讀檔案。
    """
    path = settings_path()
    if not path.is_file():
        print(f"提示：找不到 {path}，先用內建預設值。跑過 install.sh、或從 profile.example/ 複製一份 settings.toml 就會有這個檔案。")
        return Settings()

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    server_raw = raw.get("server", {})
    claude_raw = raw.get("claude_models", {})
    thresholds_raw = raw.get("thresholds", {})
    paths_raw = raw.get("paths", {})
    overlap_raw = raw.get("overlap", {})

    return Settings(
        server_port=int(server_raw.get("port", DEFAULT_SERVER_PORT)),
        claude_models=ClaudeModels(
            punctuation=claude_raw.get("punctuation", "sonnet"),
            naming=claude_raw.get("naming", "opus"),
        ),
        thresholds=Thresholds(
            length_tolerance=float(thresholds_raw.get("length_tolerance", 0.15)),
            echo_overlap_max_s=float(thresholds_raw.get("echo_overlap_max_s", 0.5)),
            name_match_relaxed=bool(thresholds_raw.get("name_match_relaxed", True)),
        ),
        paths=Paths(
            cosyvoice_model_dir=paths_raw.get("cosyvoice_model_dir", DEFAULT_COSYVOICE_MODEL_DIR),
            aligner_model_id=paths_raw.get("aligner_model_id", DEFAULT_ALIGNER_MODEL_ID),
        ),
        overlap=Overlap(
            scan_pad_s=float(overlap_raw.get("scan_pad_s", 3.0)),
        ),
    )


def overlap_scan_pad_s() -> float:
    """只在學員／不確定的句子前後各留幾秒的範圍跑 pyannote（找重疊、分辨說話者）。"""
    return load_settings().overlap.scan_pad_s


def cosyvoice_repo_dir() -> Path:
    """CosyVoice 原始碼的位置。這套是用 git clone 放進來的，不是 pip 套件，
    見 third_party/CosyVoice 與 install.sh 裡取碼的那一段。"""
    return repo_root() / "third_party" / "CosyVoice"


def cosyvoice_model_dir() -> Path:
    """CosyVoice3 模型檔案的位置（從 settings.toml 讀，讀不到就用預設值）。"""
    return Path(load_settings().paths.cosyvoice_model_dir).expanduser()


def aligner_model_id() -> str:
    """逐字對位模型在 Hugging Face 上的名稱。"""
    return load_settings().paths.aligner_model_id
