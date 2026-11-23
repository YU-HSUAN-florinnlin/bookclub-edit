"""Windows native install feasibility trial (not a real port).

Runs every step, never stops on failure, and writes a summary of what worked
and what did not (with the last lines of each error) to trial-out/.
Stdlib only. Run from the repository root with: python .github/windows-trial/run_trial.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / "trial-out"
LOGS = OUT / "logs"
LOGS.mkdir(parents=True, exist_ok=True)

COSYVOICE_SHA = "074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc"
VENV_PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
VENV_BIN = str(ROOT / ".venv" / "Scripts")

# (display name, lock-file package name)
KEY_PACKAGES = [
    ("torch 2.2.2", "torch"),
    ("torchaudio 2.2.2", "torchaudio"),
    ("openai-whisper (source build)", "openai-whisper"),
    ("dynet38 (for nagisa)", "dynet38"),
    ("kaldifst (for wetext)", "kaldifst"),
    ("onnxruntime", "onnxruntime"),
    ("pyannote.audio", "pyannote-audio"),
]

results: list[dict] = []
_counter = 0


def tail(text: str, n: int = 15) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


def run(name: str, cmd, timeout: int = 1800, env_extra: dict | None = None,
        cwd: Path | None = None, group: str = "step") -> bool:
    """Run one command, record result, never raise."""
    global _counter
    _counter += 1
    env = dict(os.environ)
    env["PYTHONUTF8"] = env.get("PYTHONUTF8", "1")
    env["PATH"] = VENV_BIN + os.pathsep + env["PATH"]
    if env_extra:
        env.update(env_extra)
    for k in [k for k, v in env.items() if v is None]:
        del env[k]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=str(cwd or ROOT), env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
        out = (p.stdout or "") + "\n" + (p.stderr or "")
        ok = p.returncode == 0
        code = p.returncode
    except subprocess.TimeoutExpired as e:
        out = f"TIMEOUT after {timeout}s\n{e.stdout or ''}\n{e.stderr or ''}"
        ok, code = False, "timeout"
    except Exception as e:  # e.g. executable not found
        out, ok, code = f"{type(e).__name__}: {e}", False, "exception"
    dt = time.time() - t0
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:60]
    (LOGS / f"{_counter:03d}_{safe}.log").write_text(out, encoding="utf-8", errors="replace")
    results.append({"group": group, "name": name, "ok": ok, "code": code,
                    "secs": round(dt), "tail": "" if ok else tail(out)})
    print(f"[{'OK' if ok else 'FAIL'}] {name} ({code}, {dt:.0f}s)", flush=True)
    if not ok:
        print(tail(out, 8), flush=True)
    return ok


def uv_install(name: str, args: list[str], group: str = "pkg", timeout: int = 1800) -> bool:
    return run(name, ["uv", "pip", "install", "--python", VENV_PY, *args], timeout=timeout, group=group)


def parse_lock() -> dict[str, str]:
    """name -> requirement line (comment stripped, env markers kept)."""
    reqs: dict[str, str] = {}
    for raw in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines():
        line = raw.split("  #")[0].strip()
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[=<>!~; ]", line, 1)[0].lower().replace("_", "-")
        reqs.setdefault(name, "")
        reqs[name] += line + "\n"
    return reqs


def main() -> None:
    # 1. environment info
    run("python version", [sys.executable, "-VV"], group="env")
    run("uv version", ["uv", "--version"], group="env")
    run("ffmpeg version", ["ffmpeg", "-version"], group="env")
    run("git version", ["git", "--version"], group="env")
    run("sample non-ASCII path write", [sys.executable, "-c",
        "import pathlib,tempfile;p=pathlib.Path(tempfile.mkdtemp())/'中文 路徑';p.mkdir();(p/'測試.txt').write_text('ok',encoding='utf-8');print(p)"],
        group="env")

    # 2. venv
    if not run("uv venv (Python 3.11)", ["uv", "venv", ".venv", "--python", "3.11"], group="venv"):
        write_summary()
        return
    run("venv python arch", [VENV_PY, "-c",
        "import platform,sys;print(sys.version);print(platform.machine(),platform.platform())"], group="venv")

    # 3. torch / torchaudio: CPU wheels (same idea as install.sh on Linux without NVIDIA)
    uv_install("torch==2.2.2 (cpu index)", ["--index-url", "https://download.pytorch.org/whl/cpu",
               "torch==2.2.2", "torchaudio==2.2.2"], group="key")
    # fall back to PyPI if the CPU index has no Windows wheel for it
    if not any(r["name"].startswith("torch==2.2.2 (cpu") and r["ok"] for r in results):
        uv_install("torch==2.2.2 (PyPI fallback)", ["torch==2.2.2", "torchaudio==2.2.2"], group="key")

    # 4. key packages one by one (--no-deps so each result is independent)
    lock = parse_lock()
    key_names = {"torch", "torchaudio", "openai-whisper", "dynet38", "kaldifst", "onnxruntime", "pyannote-audio"}
    tmp = OUT / "tmp-req"
    tmp.mkdir(exist_ok=True)
    for disp, pkg in KEY_PACKAGES:
        if pkg in ("torch", "torchaudio"):
            continue
        line = lock.get(pkg)
        if not line:
            results.append({"group": "key", "name": f"{disp} (alone)", "ok": False, "code": "not in lock",
                            "secs": 0, "tail": f"{pkg} not found in requirements.lock"})
            continue
        f = tmp / f"{pkg}.txt"
        f.write_text(line, encoding="utf-8")
        extra = ["--build-constraint", "build-constraints.txt"] if pkg == "openai-whisper" else []
        uv_install(f"{disp} (alone, --no-deps)", ["--no-deps", "-r", str(f), *extra], group="key")

    # 5. full lock: bulk first, per-package fallback so one failure doesn't hide the rest
    bulk_ok = uv_install("requirements.lock (bulk, with build-constraints)",
                         ["-r", "requirements.lock", "--build-constraint", "build-constraints.txt"],
                         group="lock", timeout=3000)
    if not bulk_ok:
        print("Bulk install failed; falling back to per-package install", flush=True)
        for pkg, line in lock.items():
            if pkg in key_names and pkg not in ("torch", "torchaudio"):
                pass  # still retry: bulk failure may have rolled everything back
            f = tmp / f"lock_{pkg}.txt"
            f.write_text(line, encoding="utf-8")
            extra = ["--build-constraint", "build-constraints.txt"]
            uv_install(f"lock: {pkg}", ["--no-deps", "-r", str(f), *extra], group="lockpkg", timeout=900)

    # 6. qwen-asr, bookclub itself (same as install.sh steps 5-6)
    uv_install("qwen-asr==0.0.6 (--no-deps)", ["--no-deps", "qwen-asr==0.0.6"], group="pkg")
    uv_install("bookclub (-e . --no-deps)", ["--no-deps", "-e", "."], group="pkg")

    # 7. CosyVoice source (same pinned commit as install.sh)
    cv = ROOT / "third_party" / "CosyVoice"
    cv.parent.mkdir(exist_ok=True)
    run("CosyVoice git init", ["git", "init", str(cv)], group="cosyvoice")
    run("CosyVoice remote add", ["git", "-C", str(cv), "remote", "add", "origin",
        "https://github.com/FunAudioLLM/CosyVoice.git"], group="cosyvoice")
    run("CosyVoice fetch pinned commit", ["git", "-C", str(cv), "fetch", "--depth", "1", "origin", COSYVOICE_SHA],
        group="cosyvoice")
    run("CosyVoice checkout", ["git", "-C", str(cv), "checkout", COSYVOICE_SHA], group="cosyvoice")
    run("CosyVoice submodules", ["git", "-C", str(cv), "submodule", "update", "--init", "--recursive", "--depth", "1"],
        group="cosyvoice")

    # 8. import checks (no models downloaded)
    imports = [
        ("torch", "import torch;print(torch.__version__, torch.cuda.is_available())"),
        ("torchaudio", "import torchaudio;print(torchaudio.__version__)"),
        ("whisper (openai-whisper)", "import whisper;print(whisper.__file__)"),
        ("nagisa (dynet38)", "import nagisa;print(nagisa.tagging('これはテストです'))"),
        ("kaldifst", "import kaldifst;print(kaldifst.__file__)"),
        ("wetext", "import wetext;print(wetext.__file__)"),
        ("onnxruntime", "import onnxruntime;print(onnxruntime.__version__, onnxruntime.get_available_providers())"),
        ("pyannote.audio", "import pyannote.audio;print(pyannote.audio.__version__)"),
        ("silero_vad", "import silero_vad;print('ok')"),
        ("transformers", "import transformers;print(transformers.__version__)"),
        ("qwen_asr", "import qwen_asr;print('ok')"),
        ("soundfile", "import soundfile;print(soundfile.__version__)"),
        ("librosa", "import librosa;print(librosa.__version__)"),
    ]
    for disp, code in imports:
        run(f"import {disp}", [VENV_PY, "-c", code], group="import", timeout=600)
    cv_code = (
        "import sys;from pathlib import Path;"
        "r=Path('third_party/CosyVoice').resolve();"
        "sys.path.insert(0,str(r));sys.path.insert(0,str(r/'third_party'/'Matcha-TTS'));"
        "import cosyvoice;from cosyvoice.cli.cosyvoice import AutoModel;print('cosyvoice import ok')"
    )
    run("import CosyVoice (cosyvoice.cli.cosyvoice)", [VENV_PY, "-c", cv_code], group="import", timeout=600)
    cv_code2 = ("import sys;from pathlib import Path;"
                "r=Path('third_party/CosyVoice').resolve();"
                "sys.path.insert(0,str(r));sys.path.insert(0,str(r/'third_party'/'Matcha-TTS'));"
                "from cosyvoice.cli.cosyvoice import CosyVoice3;print('CosyVoice3 import ok')")
    run("import CosyVoice3 class", [VENV_PY, "-c", cv_code2], group="import", timeout=600)

    # 9. bookclub import / CLI
    run("python -c import bookclub", [VENV_PY, "-c", "import bookclub;print(bookclub.__file__)"], group="cli")
    run("bookclub --help", [str(Path(VENV_BIN) / "bookclub.exe"), "--help"], group="cli")
    run("bookclub doctor (informational)", [str(Path(VENV_BIN) / "bookclub.exe"), "doctor"], group="cli", timeout=600)

    # 10. unit tests: each file separately, never stop
    tests = sorted(p for p in (ROOT / "tests").glob("test_*.py"))
    for p in tests:
        run(f"test {p.name} [PYTHONUTF8=1]", [VENV_PY, str(p.relative_to(ROOT))],
            env_extra={"PYTHONUTF8": "1"}, group="test_utf8", timeout=300)
    core = ["test_detect.py", "test_fileio.py", "test_names.py", "test_keepawake.py",
            "test_assemble.py", "test_lockfile.py", "test_execute.py", "test_extract_audio.py"]
    for name in core:
        p = ROOT / "tests" / name
        if p.exists():
            run(f"test {name} [PYTHONUTF8 unset]", [VENV_PY, str(p.relative_to(ROOT))],
                env_extra={"PYTHONUTF8": None, "PYTHONIOENCODING": None}, group="test_noutf8", timeout=300)

    write_summary()


def write_summary() -> None:
    def table(group_filter, title):
        rows = [r for r in results if group_filter(r)]
        if not rows:
            return ""
        s = f"\n### {title}\n\n| Result | Step | Code | Secs |\n|---|---|---|---|\n"
        for r in rows:
            s += f"| {'PASS' if r['ok'] else 'FAIL'} | {r['name']} | {r['code']} | {r['secs']} |\n"
        return s

    md = "# Windows native trial summary\n"
    npass = sum(r["ok"] for r in results)
    md += f"\nTotal steps: {len(results)}, passed: {npass}, failed: {len(results) - npass}\n"

    md += "\n## Key packages\n\n| Package | Alone (--no-deps) | Import |\n|---|---|---|\n"
    imp = {r["name"]: r["ok"] for r in results if r["group"] == "import"}
    def mark(v):
        return "n/a" if v is None else ("PASS" if v else "FAIL")
    key_map = [
        ("torch 2.2.2", "torch==2.2.2", "import torch"),
        ("torchaudio 2.2.2", "torch==2.2.2", "import torchaudio"),
        ("openai-whisper", "openai-whisper", "import whisper (openai-whisper)"),
        ("dynet38 / nagisa", "dynet38", "import nagisa (dynet38)"),
        ("kaldifst / wetext", "kaldifst", "import kaldifst"),
        ("onnxruntime", "onnxruntime", "import onnxruntime"),
        ("pyannote.audio", "pyannote.audio", "import pyannote.audio"),
        ("CosyVoice", "CosyVoice checkout", "import CosyVoice (cosyvoice.cli.cosyvoice)"),
    ]
    for disp, inst_key, imp_key in key_map:
        inst = next((r["ok"] for r in results if r["name"].startswith(inst_key)
                     or r["name"].startswith(inst_key.replace("pyannote.audio", "pyannote.audio ("))), None)
        md += f"| {disp} | {mark(inst)} | {mark(imp.get(imp_key))} |\n"

    for grp, title in [("env", "Environment"), ("venv", "venv"), ("key", "Key package installs"),
                       ("lock", "Lock file (bulk)"), ("pkg", "Other installs"),
                       ("cosyvoice", "CosyVoice source"), ("import", "Imports"), ("cli", "CLI")]:
        md += table(lambda r, g=grp: r["group"] == g, title)
    lp = [r for r in results if r["group"] == "lockpkg"]
    if lp:
        bad = [r for r in lp if not r["ok"]]
        md += f"\n### Lock file per-package fallback\n\n{len(lp) - len(bad)} passed, {len(bad)} failed.\n"
    for grp, title in [("test_utf8", "Unit tests, PYTHONUTF8=1"), ("test_noutf8", "Unit tests, PYTHONUTF8 unset (core subset)")]:
        rows = [r for r in results if r["group"] == grp]
        if rows:
            bad = [r for r in rows if not r["ok"]]
            md += f"\n### {title}\n\n{len(rows) - len(bad)} passed, {len(bad)} failed.\n"
            if bad:
                md += "\nFailed: " + ", ".join(r["name"].split()[1] for r in bad) + "\n"

    md += "\n## Failure details (last lines)\n"
    for r in results:
        if not r["ok"]:
            md += f"\n<details><summary>{r['name']} (code {r['code']})</summary>\n\n```\n{r['tail'][:3000]}\n```\n</details>\n"

    (OUT / "summary.md").write_text(md, encoding="utf-8")
    import json
    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(md)
    print(md[:4000])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # always leave a summary behind
        results.append({"group": "env", "name": "trial script crashed", "ok": False, "code": "exception",
                        "secs": 0, "tail": f"{type(e).__name__}: {e}"})
        write_summary()
