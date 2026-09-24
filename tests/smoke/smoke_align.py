"""試跑：逐字對位模型（Qwen3 對齊）能不能載入、跑出結果。

對 tests/smoke/out/one.wav（用 make_sample.py 產生的假錄音，不是真的學員
錄音）跑一次，印出每個字對到的起訖時間，最後一行印 JSON 方便程式判讀。

獨立可跑：.venv/bin/python tests/smoke/smoke_align.py
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


def main() -> int:
    audio_path = Path(__file__).resolve().parent / "out" / "one.wav"
    payload: dict = {"machine": platform.machine(), "ok": False}

    if not audio_path.is_file():
        payload["error"] = f"找不到測試音檔：{audio_path}，先跑 tests/smoke/make_sample.py"
        print(json.dumps(payload, ensure_ascii=False))
        return 1

    try:
        t0 = time.time()
        import torch
        from qwen_asr import Qwen3ForcedAligner
        import soundfile as sf

        from bookclub.config import aligner_model_id

        t1 = time.time()

        model = Qwen3ForcedAligner.from_pretrained(aligner_model_id(), dtype=torch.float32, device_map="cpu")
        t2 = time.time()

        result = model.align(audio=str(audio_path), text=SAMPLE_TEXT, language="Chinese")
        t3 = time.time()

        for item in result[0]:
            print(f"{item.text}\t{item.start_time:.3f}\t{item.end_time:.3f}")

        payload.update(
            {
                "torch": torch.__version__,
                "import_s": round(t1 - t0, 2),
                "load_s": round(t2 - t1, 2),
                "run_s": round(t3 - t2, 2),
                "audio_s": round(sf.info(str(audio_path)).duration, 2),
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
