"""下載／確認三個 AI 模型都在該在的位置。

`bookclub models download` 會呼叫這裡的 download_all()。已經下載好的檔案不會
重抓（本機空間有限，也不想浪費時間），所以這個指令可以放心重跑。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from bookclub.config import aligner_model_id, cosyvoice_model_dir

COSYVOICE3_REPO = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"

# 只下載推論用得到的檔案。完整倉庫約 9.75GB（含訓練用的額外檔案），
# 這份清單約 5.1GB。CosyVoice-BlankEN 是空白音框模型，零樣本聲音克隆會用到。
COSYVOICE3_FILES = [
    "cosyvoice3.yaml",
    "config.json",
    "configuration.json",
    "llm.rl.pt",
    "flow.pt",
    "hift.pt",
    "campplus.onnx",
    "speech_tokenizer_v3.onnx",
    "CosyVoice-BlankEN/config.json",
    "CosyVoice-BlankEN/generation_config.json",
    "CosyVoice-BlankEN/merges.txt",
    "CosyVoice-BlankEN/model.safetensors",
    "CosyVoice-BlankEN/tokenizer_config.json",
    "CosyVoice-BlankEN/vocab.json",
]

# pyannote 是需要先在網站點同意條款的「受限模型」，光有帳號不夠，還要
# 個別到每個模型頁面點過「同意」，這裡用 config.yaml 判斷是否已經下載
# （pyannote 的模型與流程設定檔都叫這個名字，不用連網也能查本機快取）。
PYANNOTE_REPOS = [
    "pyannote/segmentation-3.0",
    "pyannote/speaker-diarization-3.1",
    "pyannote/wespeaker-voxceleb-resnet34-LM",
]
PYANNOTE_CONFIG_FILENAME = "config.yaml"
# pyannote 3.x 不用 Hugging Face 的預設快取，而是自己的資料夾（跟 pyannote/audio/core/model.py 的 CACHE_DIR 一致）
PYANNOTE_CACHE_DIR = os.getenv("PYANNOTE_CACHE", os.path.expanduser("~/.cache/torch/pyannote"))

HF_LOGIN_STEPS = """Hugging Face 三步驟（pyannote 是「受限模型」，需要帳號同意條款才能下載）：
  ① 登入 huggingface.co（沒有帳號的話先免費註冊一個）
  ② 打開以下兩個頁面，各自把表單填一填、按「同意」：
       https://huggingface.co/pyannote/segmentation-3.0
       https://huggingface.co/pyannote/speaker-diarization-3.1
  ③ 到 Settings → Access Tokens 建一把 Read 權限的金鑰，然後在終端機執行：
       ~/Downloads/bookclub-edit/.venv/bin/hf auth login
     貼上金鑰（金鑰不要貼給 AI、也不要寫進任何檔案）"""


def _hf_cached(repo_id: str, filename: str, cache_dir: str | None = None) -> bool:
    """檔案是不是已經在本機的 Hugging Face 快取裡，純讀本機、不連網。"""
    from huggingface_hub import hf_hub_download

    try:
        hf_hub_download(repo_id=repo_id, filename=filename, local_files_only=True, cache_dir=cache_dir)
        return True
    except Exception:
        return False


def download_aligner() -> None:
    """Qwen3 對齊模型（逐字對位用）。"""
    repo_id = aligner_model_id()
    if _hf_cached(repo_id, "config.json"):
        print(f"[逐字對位] {repo_id} 已在本機快取，跳過下載")
        return

    print(f"[逐字對位] 下載 {repo_id}...")
    from huggingface_hub import snapshot_download

    snapshot_download(repo_id=repo_id)
    print("[逐字對位] 下載完成")


def download_cosyvoice3() -> None:
    """CosyVoice3 聲音生成模型。只下載推論用得到的檔案清單，並建立
    `llm.pt -> llm.rl.pt` 連結（RL 版念錯字比較少；載入程式固定讀 llm.pt
    這個檔名，換版本只要改連結指向）。"""
    target = cosyvoice_model_dir()
    target.mkdir(parents=True, exist_ok=True)

    missing = [f for f in COSYVOICE3_FILES if not (target / f).is_file()]
    if missing:
        print(f"[聲音生成] CosyVoice3 缺少 {len(missing)} 個檔案，開始下載（全部約 5.1GB）...")
        from huggingface_hub import hf_hub_download

        for f in missing:
            hf_hub_download(repo_id=COSYVOICE3_REPO, filename=f, local_dir=str(target))
        print("[聲音生成] CosyVoice3 檔案下載完成")
    else:
        print("[聲音生成] CosyVoice3 檔案已存在，跳過下載")

    link = target / "llm.pt"
    if link.is_symlink():
        if os.readlink(link) == "llm.rl.pt":
            print("[聲音生成] llm.pt 連結已存在，跳過")
        else:
            print(f"⚠️ [聲音生成] {link} 是連結，但指向的不是 llm.rl.pt，請自行確認要不要改")
    elif link.exists():
        print(f"⚠️ [聲音生成] {link} 已存在但不是連結（可能是誤放的檔案），請自行確認")
    else:
        link.symlink_to("llm.rl.pt")
        print("[聲音生成] 已建立連結 llm.pt -> llm.rl.pt")


def download_pyannote() -> None:
    """pyannote 說話者分群模型。這三個是受限模型，沒有 Hugging Face 登入
    帳號同意條款就下載不了，這裡偵測到沒登入時不會報錯，只會印步驟說明
    然後跳過（Phase 0 這台機器目前就是這個狀態，是預期中的情形）。"""
    from huggingface_hub import get_token

    token = get_token()
    if not token:
        print("[分辨誰在說話] 還沒有 Hugging Face 登入，略過下載，之後要用時再回來做以下步驟：")
        print(HF_LOGIN_STEPS)
        return

    from huggingface_hub import snapshot_download

    for repo_id in PYANNOTE_REPOS:
        if _hf_cached(repo_id, PYANNOTE_CONFIG_FILENAME, PYANNOTE_CACHE_DIR):
            print(f"[分辨誰在說話] {repo_id} 已在本機快取，跳過下載")
            continue
        print(f"[分辨誰在說話] 下載 {repo_id}...")
        try:
            snapshot_download(repo_id=repo_id, token=token, cache_dir=PYANNOTE_CACHE_DIR)
            print(f"[分辨誰在說話] {repo_id} 下載完成")
        except Exception as exc:
            print(f"⚠️ [分辨誰在說話] {repo_id} 下載失敗：{exc}")
            print("   常見原因：還沒在網頁上點過該模型頁面的「同意」。" + "\n" + HF_LOGIN_STEPS)


def download_wetext() -> None:
    """wetext 的語言資源（數字唸法等文字正規化規則）會在第一次使用時自動
    從 modelscope.cn 下載。這裡主動觸發一次，失敗只警告、不擋安裝——
    modelscope 目前常見「操作過於頻繁」限流，CosyVoice 沒有這個資源一樣
    能生成聲音，只是數字、單位的唸法會比較陽春。"""
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            import wetext

            wetext.Normalizer(lang="zh")
            print("[聲音生成] wetext 語言資源已就緒")
            return
        except Exception as exc:
            if attempt < max_attempts:
                wait_s = attempt * 5
                print(f"⚠️ [聲音生成] wetext 資源下載失敗（第 {attempt} 次，{wait_s} 秒後重試）：{exc}")
                time.sleep(wait_s)
            else:
                print(f"⚠️ [聲音生成] wetext 資源下載失敗（已重試 {max_attempts} 次，先略過不影響安裝）：{exc}")
                print("   之後可重跑「bookclub models download」再試一次。")


def download_all() -> None:
    print("開始檢查／下載模型...")
    download_aligner()
    download_cosyvoice3()
    download_pyannote()
    download_wetext()
    print("模型檢查完成。跑「bookclub doctor」確認整體環境狀態。")
