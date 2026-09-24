"""試跑：聲音生成模型（CosyVoice3）能不能載入、生成一句大約 5 秒的聲音。

用固定的一句話（含虛構名字）生成，結果存到 tests/smoke/out/tts_out.wav，
可以打開聽聽看是不是正常長度的人聲（不是 0.04 秒的空白——這是修補相容性
問題之前踩過的坑，見 bookclub/_cosyvoice_compat.py 開頭的說明）。

獨立可跑：.venv/bin/python tests/smoke/smoke_tts.py
在模擬 Intel（Rosetta）環境下這一支明顯比較慢，抓 4–6 分鐘。
"""

from __future__ import annotations

import json
import platform
import resource
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

SAMPLE_TEXT = "小明，你剛剛分享的那一段，美華也有類似的經驗。"
# CosyVoice 零樣本聲音克隆需要一段參考音檔＋它的逐字稿；這裡沿用 CosyVoice
# 專案自帶的範例參考音，不是老師或學員的聲音。
PROMPT_TEXT = "You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。"


def main() -> int:
    payload: dict = {"machine": platform.machine(), "ok": False}
    try:
        t0 = time.time()
        import torch
        import soundfile as sf

        from bookclub._cosyvoice_compat import load_cosyvoice
        from bookclub.config import cosyvoice_repo_dir

        t1 = time.time()

        model = load_cosyvoice()
        t2 = time.time()

        prompt_wav = cosyvoice_repo_dir() / "asset" / "zero_shot_prompt.wav"
        if not prompt_wav.is_file():
            raise FileNotFoundError(f"找不到 CosyVoice 內建的參考音檔：{prompt_wav}")

        chunks = []
        for j in model.inference_zero_shot(SAMPLE_TEXT, PROMPT_TEXT, str(prompt_wav), stream=False):
            chunks.append(j["tts_speech"])
        t3 = time.time()

        wav = torch.cat(chunks, dim=1).squeeze(0).numpy()
        audio_s = len(wav) / model.sample_rate

        out_path = Path(__file__).resolve().parent / "out" / "tts_out.wav"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_path), wav, model.sample_rate)
        print(f"已輸出：{out_path}（{audio_s:.2f} 秒，取樣率 {model.sample_rate}）")

        payload.update(
            {
                "torch": torch.__version__,
                "import_s": round(t1 - t0, 2),
                "load_s": round(t2 - t1, 2),
                "run_s": round(t3 - t2, 2),
                "audio_s": round(audio_s, 2),
                "peak_rss_gb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9, 2),
                "ok": True,
            }
        )
    except Exception as exc:
        payload["error"] = str(exc)
        payload["peak_rss_gb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9, 2)

    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
