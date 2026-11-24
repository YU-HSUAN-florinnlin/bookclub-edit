"""Windows trial: forced alignment (Qwen3) + silero-vad on the generated wav. JSON line out.
Usage: python infer_align.py <wav>   (run from repo root)
"""
import json, sys, time, platform
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
TEXT = "今天天氣很好，我們一起散步。"
wav_path = sys.argv[1]
payload = {"ok": False, "machine": platform.machine(), "vad": None, "align": None}

try:
    import soundfile as sf, torch
    info = sf.info(wav_path)
    payload["audio_s"] = round(info.duration, 2)
except Exception as exc:
    payload["error"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(payload, ensure_ascii=False)); sys.exit(1)

try:
    t0 = time.time()
    from silero_vad import load_silero_vad, read_audio, get_speech_timestamps
    m = load_silero_vad()
    audio = read_audio(wav_path, sampling_rate=16000)
    ts = get_speech_timestamps(audio, m, sampling_rate=16000, return_seconds=True)
    payload["vad"] = {"ok": len(ts) > 0, "segments": ts, "secs": round(time.time() - t0, 1)}
except Exception as exc:
    payload["vad"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

try:
    t0 = time.time()
    from qwen_asr import Qwen3ForcedAligner
    from bookclub.config import aligner_model_id
    model = Qwen3ForcedAligner.from_pretrained(aligner_model_id(), dtype=torch.float32, device_map="cpu")
    t1 = time.time()
    res = model.align(audio=wav_path, text=TEXT, language="Chinese")
    t2 = time.time()
    items = [(i.text, round(i.start_time, 3), round(i.end_time, 3)) for i in res[0]]
    payload["align"] = {"ok": len(items) > 0, "load_s": round(t1 - t0, 1), "run_s": round(t2 - t1, 1),
                        "n_items": len(items), "first": items[:3], "last": items[-2:]}
except Exception as exc:
    import traceback; traceback.print_exc()
    payload["align"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

payload["ok"] = bool(payload["vad"] and payload["vad"].get("ok")) or bool(payload["align"] and payload["align"].get("ok"))
print(json.dumps(payload, ensure_ascii=False, default=str))
sys.exit(0 if payload["ok"] else 1)
