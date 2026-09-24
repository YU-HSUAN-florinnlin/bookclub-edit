"""工作區檔案路徑與共用小工具。

一支影片一個工作區資料夾，這支模組是「檔案放哪裡」這件事的唯一正本——
`bookclub/transcribe.py`、`speakers.py`、`overlap.py`、`names.py`、
`analyze.py`（串流程）都從這裡問路徑，不要自己組字串。檔案格式細節見
`docs/工作區格式.md`。

沿用 `bookclub/refpick.py` 已經在用的檔名（`audio.flac`、`chunks/`、
`transcript/`、`參考音/`），這樣 `bookclub run analyze` 呼叫 refpick 的
「挑參考音」時可以直接吃到轉文字步驟留下的快取，不必重跑一次抽音。
"""

from __future__ import annotations

import json
from pathlib import Path

REF_DIR_NAME = "參考音"          # 跟 bookclub/refpick.py 的 REF_DIR_NAME 保持一致
NAME_CANDIDATES_DIR_NAME = "名字候選"


def fmt_time(sec: float) -> str:
    """秒數轉 "HH:MM:SS"，只用於標時間，不涉及音訊或逐字稿內容。"""
    sec = max(0, round(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def ensure(workdir: str | Path) -> Path:
    """確保工作區資料夾存在，回傳 Path。"""
    workdir = Path(workdir).expanduser()
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


# ---------- 步驟 1：轉文字 ----------

def audio_path(workdir: Path) -> Path:
    """整支影片抽出來的音軌，16kHz 單聲道 flac。"""
    return workdir / "audio.flac"


def chunks_dir(workdir: Path) -> Path:
    """轉文字送去 Groq 的音訊分塊（依模組不同，檔名前綴不同，避免互相覆蓋）。"""
    return workdir / "chunks"


def transcript_dir(workdir: Path) -> Path:
    """Groq 逐塊辨識的原始回應＋合併後的逐字稿。"""
    return workdir / "transcript"


def merged_transcript_path(workdir: Path) -> Path:
    """合併好、時間已換算回整支影片的逐字稿（`bookclub/transcribe.py` 的最終輸出）。"""
    return transcript_dir(workdir) / "merged.json"


# ---------- 步驟 2：認老師 ----------

def speakers_path(workdir: Path) -> Path:
    """每句話的老師／學員判斷、分群資訊、老師聲紋中心。"""
    return workdir / "說話者判斷.json"


# ---------- 步驟 3：找重疊 ----------

def overlap_path(workdir: Path) -> Path:
    """重疊清單（含已自動跳過的）。"""
    return workdir / "重疊.json"


# ---------- 步驟 4：挑老師參考音（呼叫 bookclub/refpick.py） ----------

def ref_dir(workdir: Path, ref_dir_name: str = REF_DIR_NAME) -> Path:
    return workdir / ref_dir_name


# ---------- 步驟 5：找名字 ----------

def names_path(workdir: Path) -> Path:
    """名字候選清單：時間、名字、代號、位置、建議做法、切點。"""
    return workdir / "名字候選.json"


def name_candidates_dir(workdir: Path) -> Path:
    """每筆名字候選前後各 2 秒的小段音檔，供覆核試聽。"""
    return workdir / NAME_CANDIDATES_DIR_NAME


# ---------- 串流程彙整 ----------

def analysis_result_path(workdir: Path) -> Path:
    """`bookclub run analyze` 的彙整索引：各步驟耗時、統計數字。"""
    return workdir / "分析結果.json"


def report_path(workdir: Path) -> Path:
    """給人看的報告（只放統計與時間，不放逐字稿內容）。"""
    return workdir / "報告.md"


# ---------- 小工具：讀寫 JSON ----------

def read_json(path: Path, default=None):
    """檔案不存在就回傳 default（預設 None），存在就讀出來。"""
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    """統一存檔格式：不轉義中文、縮排 1 格，跟 refpick.py 的存檔方式一致。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
