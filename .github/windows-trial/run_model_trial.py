"""Windows native MODEL trial (feasibility only): download models, run CPU inference.
Assumes run_trial.py (TRIAL_INSTALL_ONLY=1) already built .venv. Never stops on failure.
Output: model-out/ (summary.md, results.json, logs/, tts_out.wav, demo.mp4).
Run from the repository root: python .github/windows-trial/run_model_trial.py
"""
from __future__ import annotations

import json, os, shutil, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_trial as rt  # noqa: E402  (reuse run(); its trial-out/ dir is created but unused here)

ROOT = rt.ROOT
OUT = ROOT / "model-out"
LOGS = OUT / "logs"
LOGS.mkdir(parents=True, exist_ok=True)
rt.LOGS = LOGS
VPY = rt.VENV_PY
BK = str(Path(rt.VENV_BIN) / "bookclub.exe")
WAV = OUT / "tts_out.wav"
info: dict = {}


def last_json(path_glob: str) -> dict:
    for p in sorted(LOGS.glob(path_glob), reverse=True):
        for line in reversed(p.read_text(encoding="utf-8", errors="replace").splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    pass
    return {}


def py(name, code, group, timeout=900):
    return rt.run(name, [VPY, "-c", code], group=group, timeout=timeout)


def main() -> None:
    du = shutil.disk_usage("C:\\")
    info["disk_free_gb_start"] = round(du.free / 1e9, 1)
    rt.run("disk free (start)", ["powershell", "-c", "Get-PSDrive -PSProvider FileSystem | Format-Table Name,Used,Free"], group="env")
    rt.run("symlink support test", [sys.executable, "-c",
           "import os,tempfile,pathlib;d=pathlib.Path(tempfile.mkdtemp());(d/'a').write_text('x');os.symlink('a',d/'b');print('symlink ok')"], group="env")

    # models (pyannote skipped: needs Hugging Face token)
    rt.run("download aligner (Qwen3 ForcedAligner)", [VPY, "-c", "from bookclub.models import download_aligner;download_aligner()"],
           group="download", timeout=3600)
    rt.run("download CosyVoice3 (models.py)", [VPY, "-c", "from bookclub.models import download_cosyvoice3;download_cosyvoice3()"],
           group="download", timeout=5400)
    # models.py makes llm.pt a symlink; fall back to a copy if Windows refuses
    fix = ("from bookclub.config import cosyvoice_model_dir;import shutil;d=cosyvoice_model_dir();l=d/'llm.pt'\n"
           "print('exists',l.exists(),'symlink',l.is_symlink())\n"
           "if not l.exists():\n"
           "    shutil.copyfile(d/'llm.rl.pt',l);print('copied llm.rl.pt -> llm.pt (symlink fallback)')\n"
           "print(sorted(p.name for p in d.iterdir()))")
    py("llm.pt link check / copy fallback", fix, "download")
    py("download wetext resources", "from bookclub.models import download_wetext;download_wetext()", "download", 300)
    rt.run("disk free (after download)", ["powershell", "-c", "Get-PSDrive C | Format-Table Name,Used,Free"], group="env")

    # inference
    rt.run("CosyVoice3 CPU: load + synthesize one sentence", [VPY, str(HERE / "infer_tts.py"), str(WAV)],
           group="infer", timeout=3600)
    info["tts"] = last_json("*CosyVoice3_CPU*.log")
    if WAV.is_file():
        rt.run("silero-vad + forced alignment on generated wav", [VPY, str(HERE / "infer_align.py"), str(WAV)],
               group="infer", timeout=2400)
        info["align"] = last_json("*silero-vad*.log")

        # ffmpeg: 5 s synthetic video + generated wav -> mp4
        mp4 = OUT / "demo.mp4"
        rt.run("ffmpeg: synthetic 5s video + generated wav -> mp4", ["ffmpeg", "-y", "-f", "lavfi", "-i",
               "testsrc=size=640x360:rate=25:duration=5", "-i", str(WAV), "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-shortest", str(mp4)], group="infer", timeout=300)
        rt.run("ffprobe demo.mp4", ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name",
               "-of", "default=nw=1", str(mp4)], group="infer", timeout=60)

        # smoke scripts: samples need one.wav/two.wav (normally made with macOS `say`)
        sm = ROOT / "tests" / "smoke" / "out"
        sm.mkdir(parents=True, exist_ok=True)
        rt.run("make 16k one.wav from generated wav", ["ffmpeg", "-y", "-i", str(WAV), "-ar", "16000", "-ac", "1", str(sm / "one.wav")], group="smoke", timeout=60)
    rt.run("bookclub doctor --smoke (no samples: how does Windows behave?)", [BK, "doctor", "--smoke"], group="smoke", timeout=1500)
    if (ROOT / "tests" / "smoke" / "out" / "one.wav").is_file():
        shutil.copyfile(ROOT / "tests" / "smoke" / "out" / "one.wav", ROOT / "tests" / "smoke" / "out" / "two.wav")
        rt.run("bookclub doctor --smoke (with one.wav/two.wav provided)", [BK, "doctor", "--smoke"], group="smoke", timeout=2400)
    rt.run("smoke_align.py directly", [VPY, "tests/smoke/smoke_align.py"], group="smoke", timeout=900)
    rt.run("smoke_tts.py directly", [VPY, "tests/smoke/smoke_tts.py"], group="smoke", timeout=1800)
    write_summary()


def write_summary() -> None:
    res = rt.results
    md = "# Windows native model trial summary\n\n"
    md += f"Disk free at start: {info.get('disk_free_gb_start')} GB\n\n"
    t = info.get("tts") or {}
    md += "## Headline\n\n"
    md += f"- CosyVoice3 CPU synth: {'OK' if t.get('ok') else 'FAIL'} " \
          f"(load {t.get('load_s')}s, generate {t.get('run_s')}s, audio {t.get('audio_s')}s @ {t.get('sample_rate')} Hz, rms {t.get('rms')}, non-silent {t.get('non_silent')})\n"
    a = info.get("align") or {}
    md += f"- silero-vad: {a.get('vad')}\n- forced alignment: {a.get('align')}\n"
    md += "\n## All steps\n\n| Result | Step | Code | Secs |\n|---|---|---|---|\n"
    for r in res:
        md += f"| {'PASS' if r['ok'] else 'FAIL'} | {r['name']} | {r['code']} | {r['secs']} |\n"
    md += "\n## Failure details (last lines)\n"
    for r in res:
        if not r["ok"]:
            md += f"\n<details><summary>{r['name']} (code {r['code']})</summary>\n\n```\n{r['tail'][:3000]}\n```\n</details>\n"
    (OUT / "summary.md").write_text(md, encoding="utf-8")
    (OUT / "results.json").write_text(json.dumps({"info": info, "steps": res}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    ss = os.environ.get("GITHUB_STEP_SUMMARY")
    if ss:
        with open(ss, "a", encoding="utf-8") as f:
            f.write(md)
    print(md[:4000])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        rt.results.append({"group": "env", "name": "model trial crashed", "ok": False, "code": "exception", "secs": 0, "tail": f"{type(e).__name__}: {e}"})
        write_summary()
