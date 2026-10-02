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
import os
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
    # 先寫暫存檔再換上（09-29）：寫到一半被中斷時，原本的檔案還是完整的，不會留下半個 JSON
    tmp = path.with_name(f".{path.name}.寫入中")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# ---------- 存下來的完整路徑（10-02 第五批） ----------
#
# 有幾個檔存了完整路徑：`分析結果.json` 的 video／workdir、`transcript/merged.json` 的 source、
# `參考音/挑選紀錄.json` 的 video／workdir／重切紀錄[].原片、`生成/老師紀錄.json` 的參考音、
# `生成/老師/_嘗試快取.json`／`_停頓快取.json` 的鍵（10-01 以前的寫法），學員兩份紀錄的參考音。
# 工作區整份複製、搬到別的資料夾、換一台電腦匯入之後，這些路徑還指著原本的位置。規則：
# - 寫：一律寫「現在開啟的這個工作區」（所有寫檔都從 workdir 組路徑，不照存下來的路徑寫）；
#   要覆蓋的檔如果是指到別處的連結（複製工作區時大檔常用連結），先拿掉連結再寫（`unlink_if_link`）
# - 讀：存下來的路徑在別的工作區裡，換成這個工作區裡同一個位置的檔（`localize`），不去讀別的工作區
# - 原片在工作區外面（影片旁邊），照存的路徑讀；找不到時說清楚（`video_missing_message`）

WORKSPACE_DIRS = ("參考音", "生成", "輸出", "覆核", "校對", "transcript", "chunks", "web快取", NAME_CANDIDATES_DIR_NAME,
                  "重疊", "學員名字候選", "匯出")
WORKSPACE_SUFFIX = "_剪輯工作區"   # 跟 bookclub/server.py 的 PROJECT_SUFFIX 一致


def recorded_workdirs(workdir: Path) -> list[Path]:
    """`分析結果.json`、`參考音/挑選紀錄.json` 記的工作區位置（跟現在這個不一樣的才列）。"""
    workdir = Path(workdir)
    out: list[Path] = []
    for p in (analysis_result_path(workdir), ref_dir(workdir) / "挑選紀錄.json"):
        try:
            v = (read_json(p, default={}) or {}).get("workdir")
        except (OSError, ValueError):
            v = None
        if v and Path(v) != workdir and Path(v) not in out:
            out.append(Path(v))
    return out


def _inside(p: Path, root: Path) -> Path | None:
    """p 在 root 底下就回傳相對路徑，不在回 None（純路徑比較，不碰檔案）。"""
    try:
        return p.relative_to(root)
    except ValueError:
        return None


def foreign_rel(path: str | Path, workdir: Path, old_roots: list[Path] | None = None) -> Path | None:
    """存下來的路徑如果在「別的工作區」裡，回傳它在那個工作區裡的相對位置；不是就回 None。
    判斷：在 `分析結果.json`／`挑選紀錄.json` 記的舊工作區底下；或路徑中間有工作區固定的子資料夾
    （參考音、生成、輸出⋯），而且上一層的名字跟現在這個工作區一樣、或是「⋯_剪輯工作區」「工作區」。"""
    p = Path(path).expanduser()
    workdir = Path(workdir)
    if not p.is_absolute() or _inside(p, workdir) is not None:
        return None
    for root in old_roots if old_roots is not None else recorded_workdirs(workdir):
        rel = _inside(p, root)
        if rel is not None and rel.parts:
            return rel
    parts = p.parts
    for i in range(len(parts) - 1, 0, -1):
        up = parts[i - 1]
        if parts[i] in WORKSPACE_DIRS and (up == workdir.name or up.endswith(WORKSPACE_SUFFIX) or up == "工作區"):
            return Path(*parts[i:])
    return None


def localize(path: str | Path | None, workdir: Path, old_roots: list[Path] | None = None) -> Path | None:
    """存下來的完整路徑 → 這次要讀的檔：在別的工作區裡的換成這個工作區裡同一個位置（不管存不存在，
    不會退回去讀別的工作區）；相對路徑接在這個工作區底下；其他（原片、設定資料夾裡的聲線）照用。"""
    if not path:
        return None
    workdir = Path(workdir)
    p = Path(path).expanduser()
    if not p.is_absolute():
        return workdir / p
    rel = foreign_rel(p, workdir, old_roots)
    return workdir / rel if rel is not None else p


def same_stored_file(a: str | Path | None, b: str | Path | None) -> bool:
    """兩個存下來的路徑是不是「同一個工作區檔案」：完全一樣，或在工作區裡的位置一樣（例如都是 `參考音/ref.wav`，
    只是工作區被複製或搬了）。給沒有內容指紋的舊紀錄比對用。"""
    if not a or not b:
        return False
    if str(a) == str(b):
        return True

    def tail(x) -> tuple | None:
        parts = Path(x).parts
        for i in range(len(parts) - 1, 0, -1):
            if parts[i] in WORKSPACE_DIRS:
                return parts[i:]
        return None

    ta, tb = tail(a), tail(b)
    return ta is not None and ta == tb


def unlink_if_link(path: Path) -> None:
    """要覆蓋的檔如果是連結（複製工作區時大檔常用連結指回原本的工作區），先拿掉連結，
    這次寫的就是這個工作區自己的檔，不會順著連結寫到原本的工作區。"""
    path = Path(path)
    if path.is_symlink():
        path.unlink()


def video_candidates(workdir: Path) -> list[str]:
    """存下來的原片路徑：`分析結果.json` 的 video → 逐字稿記的 source。"""
    workdir = Path(workdir)
    out = []
    for p, k in ((analysis_result_path(workdir), "video"), (merged_transcript_path(workdir), "source")):
        try:
            v = (read_json(p, default={}) or {}).get(k)
        except (OSError, ValueError):
            v = None
        if v and v not in out:
            out.append(v)
    return out


def find_video(workdir: Path, override: str | Path | None = None) -> Path | None:
    """原片：給了 override 先用；再來是存下來的路徑。原片放在工作區裡（少見）而工作區被複製時，用這個工作區裡的那一份。"""
    for c in [override, *video_candidates(workdir)]:
        p = localize(c, workdir) if c else None
        if p is not None and p.is_file():
            return p
    return None


def video_missing_message(workdir: Path) -> str:
    """找不到原片時給人看的話：記的是哪個位置、怎麼處理。"""
    cands = video_candidates(workdir)
    where = "、".join(str(localize(c, workdir)) for c in cands) if cands else "（工作區裡沒有記原片的位置）"
    return (f"找不到原片影片。工作區記的位置：{where}。"
            "影片搬過、改過檔名，或這個工作區是從別的資料夾、別台電腦複製來的：把影片放回原本的位置，"
            "或用 --video 指定影片現在的位置")


def fingerprint(data) -> str:
    """任何可以轉成 JSON 的東西 → 短指紋（09-29 檢查 #7：衍生檔記下自己是從哪一份輸入算出來的）。"""
    import hashlib

    return hashlib.sha1(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
