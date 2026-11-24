"""Windows trial: load CosyVoice3 on CPU and synthesize one self-written sentence.
Prints a JSON line. Usage: python infer_tts.py <out.wav>   (run from repo root)
"""
import json, sys, time, platform
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
TEXT = "今天天氣很好，我們一起散步。"
# CosyVoice's bundled example prompt (synthetic demo clip, not a real participant recording)
PROMPT_TEXT = "You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。"
payload = {"ok": False, "machine": platform.machine()}
try:
    t0 = time.time()
    import numpy as np, torch, soundfile as sf
    from bookclub._cosyvoice_compat import load_cosyvoice
    from bookclub.config import cosyvoice_repo_dir
    t1 = time.time()
    model = load_cosyvoice()
    t2 = time.time()
    prompt = cosyvoice_repo_dir() / "asset" / "zero_shot_prompt.wav"
    chunks = [j["tts_speech"] for j in model.inference_zero_shot(TEXT, PROMPT_TEXT, str(prompt), stream=False)]
    t3 = time.time()
    wav = torch.cat(chunks, dim=1).squeeze(0).numpy()
    out = Path(sys.argv[1]); out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), wav, model.sample_rate)
    payload.update(torch=torch.__version__, import_s=round(t1 - t0, 1), load_s=round(t2 - t1, 1),
                   run_s=round(t3 - t2, 1), audio_s=round(len(wav) / model.sample_rate, 2),
                   sample_rate=model.sample_rate, peak=round(float(np.abs(wav).max()), 4),
                   rms=round(float(np.sqrt((wav ** 2).mean())), 4))
    payload["non_silent"] = payload["rms"] > 0.005
    payload["ok"] = payload["audio_s"] > 0.5 and payload["non_silent"]
except Exception as exc:
    import traceback
    payload["error"] = f"{type(exc).__name__}: {exc}"
    traceback.print_exc()
print(json.dumps(payload, ensure_ascii=False))
sys.exit(0 if payload["ok"] else 1)
