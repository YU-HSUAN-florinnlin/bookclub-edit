"""試跑：分辨誰在說話模型（pyannote）能不能載入、跑出結果。

對 tests/smoke/out/two.wav（兩人、中間留白、最後疊約 1 秒重疊，用
make_sample.py 產生的假錄音）跑 speaker-diarization-3.1 分辨是誰在說話，
另外用 segmentation-3.0 抓重疊區間，印出各自的區段。

pyannote 是需要先在 Hugging Face 網站同意條款的「受限模型」。這台如果還沒
登入或還沒同意，這支會直接失敗並印出原因——在 Phase 0 這是預期會發生的
情況，不代表程式寫錯，照 README「Hugging Face 三步驟」設定好再重跑就好。

獨立可跑：.venv/bin/python tests/smoke/smoke_diarize.py
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


def main() -> int:
    audio_path = Path(__file__).resolve().parent / "out" / "two.wav"
    payload: dict = {"machine": platform.machine(), "ok": False}

    if not audio_path.is_file():
        payload["error"] = f"找不到測試音檔：{audio_path}，先跑 tests/smoke/make_sample.py"
        print(json.dumps(payload, ensure_ascii=False))
        return 1

    try:
        t0 = time.time()
        import torch
        import soundfile as sf
        from huggingface_hub import get_token
        from pyannote.audio import Model, Pipeline
        from pyannote.audio.pipelines import OverlappedSpeechDetection

        t1 = time.time()

        token = get_token()
        if not token:
            raise RuntimeError("還沒有 Hugging Face 登入，pyannote 是受限模型下載不了，見 README「Hugging Face 三步驟」")

        diarization_pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=token)
        segmentation_model = Model.from_pretrained("pyannote/segmentation-3.0", use_auth_token=token)
        t2 = time.time()

        diarization = diarization_pipeline(str(audio_path))
        print("── 說話者分群 ──")
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            print(f"{speaker}\t{turn.start:.3f}\t{turn.end:.3f}")

        overlap_pipeline = OverlappedSpeechDetection(segmentation=segmentation_model)
        overlap_pipeline.instantiate({"min_duration_on": 0.0, "min_duration_off": 0.0})
        overlap = overlap_pipeline(str(audio_path))
        print("── 重疊區間 ──")
        for segment in overlap.get_timeline():
            print(f"重疊\t{segment.start:.3f}\t{segment.end:.3f}")
        t3 = time.time()

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
