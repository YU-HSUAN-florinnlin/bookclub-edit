"""環境健檢：`bookclub doctor`。

逐項檢查這台電腦裝得對不對、模型在不在、帳號有沒有登入，每項印一行
「✅ 項目：細節」或「❌ 項目：問題 → 修法：…」，最後印總結。

不是每個 ❌ 都要馬上處理——有些項目（Hugging Face 登入、GROQ 金鑰、
pyannote 模型）在剛安裝完的當下本來就還沒做，這是預期的。真正會讓這個
指令回傳失敗（非 0）的只有「必要項目」，也就是少了它工具就完全動不了的
那些；其他的印 ❌ 只是提醒，不影響「必要項目全過」的判斷。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bookclub import system_info

from bookclub.config import (
    aligner_model_id,
    cosyvoice_model_dir,
    data_dir,
    repo_root,
    settings_path,
)
from bookclub.models import (
    COSYVOICE3_FILES,
    HF_LOGIN_STEPS,
    PYANNOTE_CACHE_DIR,
    PYANNOTE_CONFIG_FILENAME,
    PYANNOTE_REPOS,
    _hf_cached,
)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""
    required: bool = True

    def line(self) -> str:
        if self.ok:
            return f"✅ {self.name}：{self.detail}"
        arrow = f" → 修法：{self.fix}" if self.fix else ""
        return f"❌ {self.name}：{self.detail}{arrow}"


# ── 系統資訊（純顯示，不影響總結）────────────────────────────


def is_wsl() -> bool:
    return system_info.is_wsl(system_info.read_system_file("/proc/version"))


def linux_distro_name() -> str:
    return system_info.parse_os_release(system_info.read_system_file("/etc/os-release"))


def linux_mem_gb() -> float:
    memory = system_info.parse_meminfo(system_info.read_system_file("/proc/meminfo"))
    return memory / 1024**3 if memory is not None else 0.0


def check_os_version() -> Check:
    """作業系統：macOS 顯示版本；Linux 顯示發行版，WSL2 另外標示。"""
    if system_info.detect_os() == "Darwin":
        return Check("作業系統", True, f"macOS {platform.mac_ver()[0] or '未知'}", required=False)
    detail = linux_distro_name()
    if is_wsl():
        detail += "（偵測到 WSL2，Windows 裡的 Linux）"
    return Check("作業系統", True, detail, required=False)


def check_chip() -> Check:
    machine = platform.machine()
    if system_info.detect_os() == "Darwin":
        label = {
            "arm64": "arm64（Apple Silicon）",
            "x86_64": "x86_64（Intel，或在 Apple Silicon 上以 Rosetta 模擬 Intel）",
        }.get(machine, machine)
    else:
        label = {"aarch64": "aarch64（ARM）", "x86_64": "x86_64"}.get(machine, machine)
    return Check("處理器架構", True, label, required=False)


def check_memory() -> Check:
    try:
        if system_info.detect_os() == "Darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
            mem_gb = int(out.stdout.strip()) / (1024**3)
        else:
            mem_gb = linux_mem_gb()
        if mem_gb <= 0:
            return Check("記憶體", True, "無法偵測", required=False)
        detail = f"約 {mem_gb:.0f}GB"
        if mem_gb < 8:
            detail += "（低於建議的 8GB，聲音生成等步驟可能較吃緊）"
        return Check("記憶體", True, detail, required=False)
    except Exception as exc:
        return Check("記憶體", True, f"無法偵測（{exc}）", required=False)


def check_disk_space() -> Check:
    try:
        usage = shutil.disk_usage(Path.home())
        avail_gb = usage.free / (1024**3)
        if avail_gb < 10:
            return Check(
                "剩餘空間", False, f"約 {avail_gb:.0f}GB",
                "處理一支 2 小時的影片，暫存檔與成品大約要 5–10GB。刪掉不用的檔案（例如工作區裡處理完的舊影片）騰出空間",
                required=False,
            )
        detail = f"約 {avail_gb:.0f}GB"
        if avail_gb < 30:
            detail += "（低於建議的 30GB，先留意）"
        return Check("剩餘空間", True, detail, required=False)
    except Exception as exc:
        return Check("剩餘空間", True, f"無法偵測（{exc}）", required=False)


# ── 基本工具 ─────────────────────────────────────────────


def check_ffmpeg() -> Check:
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return Check("ffmpeg／ffprobe", True, "已安裝")
    command = "sudo apt-get install ffmpeg" if system_info.detect_os() == "Linux" else "brew install ffmpeg"
    return Check("ffmpeg／ffprobe", False, "沒有找到", f"終端機執行「{command}」")


def check_python_version() -> Check:
    major, minor, micro = sys.version_info[:3]
    detail = f"{major}.{minor}.{micro}"
    if (major, minor) == (3, 11):
        return Check("Python 版本", True, detail)
    return Check("Python 版本", False, detail, "用 uv venv 重建虛擬環境時指定 --python 3.11")


# ── 核心套件 ─────────────────────────────────────────────


def check_torch() -> Check:
    try:
        import torch

        version = torch.__version__
        if version.startswith("2.2.2"):
            return Check("torch", True, f"{version}（{platform.machine()}）")
        return Check(
            "torch", False, f"{version}（預期 2.2.2）",
            "跑 uv pip install --python .venv/bin/python -r requirements.txt -c constraints.txt 重新對齊版本",
        )
    except Exception as exc:
        return Check("torch", False, f"無法匯入（{exc}）", "跑 install.sh 重新安裝套件")


def check_numpy() -> Check:
    try:
        import numpy

        version = numpy.__version__
        if int(version.split(".")[0]) < 2:
            return Check("numpy", True, version)
        return Check(
            "numpy", False, f"{version}（需要 <2）",
            "跑 uv pip install --python .venv/bin/python -r requirements.txt -c constraints.txt 重新對齊版本",
        )
    except Exception as exc:
        return Check("numpy", False, f"無法匯入（{exc}）", "跑 install.sh 重新安裝套件")


def check_import(name: str, module: str, fix: str) -> Check:
    try:
        __import__(module)
        return Check(name, True, "可以匯入")
    except Exception as exc:
        return Check(name, False, f"匯入失敗：{exc}", fix)


def check_cosyvoice_import() -> Check:
    try:
        from bookclub._cosyvoice_compat import setup

        setup()
        import cosyvoice.cli.cosyvoice  # noqa: F401

        return Check("可匯入 cosyvoice（聲音生成）", True, "可以匯入（已套用相容性修補）")
    except Exception as exc:
        return Check(
            "可匯入 cosyvoice（聲音生成）", False, f"匯入失敗：{exc}",
            "確認 third_party/CosyVoice 是否存在（install.sh 會自動 clone），或重新安裝套件",
        )


def check_transformers_version() -> Check:
    try:
        import transformers

        return Check("transformers 版本", True, transformers.__version__, required=False)
    except Exception as exc:
        return Check("transformers 版本", False, f"無法匯入（{exc}）", "跑 install.sh 重新安裝套件", required=False)


# ── 帳號與登入 ───────────────────────────────────────────


def check_groq_key() -> Check:
    if os.environ.get("GROQ_API_KEY"):
        return Check("GROQ_API_KEY", True, "目前終端機工作階段已載入", required=False)

    zshrc = Path.home() / ".zshrc"
    mentioned = False
    if zshrc.is_file():
        try:
            mentioned = "GROQ_API_KEY" in zshrc.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            mentioned = False

    if mentioned:
        return Check(
            "GROQ_API_KEY", False, "~/.zshrc 裡有設定，但這個終端機工作階段還沒載入",
            "開一個新的終端機視窗，或執行「source ~/.zshrc」後再試", required=False,
        )
    return Check(
        "GROQ_API_KEY", False, "還沒設定",
        "到 ~/.zshrc 加一行 export GROQ_API_KEY=你的金鑰（到 https://console.groq.com 申請）",
        required=False,
    )


def check_hf_login() -> Check:
    try:
        from huggingface_hub import get_token

        token = get_token()
    except Exception:
        token = None
    if token:
        return Check("Hugging Face 登入", True, "有", required=False)
    return Check(
        "Hugging Face 登入", False, "無",
        "① 登入 huggingface.co（沒帳號先免費註冊）② 打開 "
        "huggingface.co/pyannote/segmentation-3.0 與 .../pyannote/speaker-diarization-3.1 各按一次同意 "
        "③ Settings → Access Tokens 建一把 Read 金鑰，終端機執行 .venv/bin/hf auth login 貼上（金鑰不要貼給 AI）",
        required=False,
    )


def check_claude_cli() -> Check:
    local_bin = Path.home() / ".local" / "bin" / "claude"
    found = shutil.which("claude") or (str(local_bin) if local_bin.exists() else None)
    if found:
        return Check("claude 指令", True, found, required=False)
    return Check("claude 指令", False, "找不到", "確認 Claude Code 已安裝、且 ~/.local/bin 有在 PATH 裡", required=False)


def check_claude_responds(timeout_s: int = 30) -> Check:
    try:
        result = subprocess.run(["claude", "-p", "回覆 OK"], capture_output=True, text=True, timeout=timeout_s)
        if result.returncode == 0 and result.stdout.strip():
            return Check("claude 回應測試", True, result.stdout.strip()[:60], required=False)
        return Check(
            "claude 回應測試", False, f"退出碼 {result.returncode}：{result.stderr.strip()[:200]}",
            "確認 claude 指令已登入（直接跑 claude 看看能不能正常對話）", required=False,
        )
    except FileNotFoundError:
        return Check("claude 回應測試", False, "找不到 claude 指令", "確認 Claude Code 已安裝", required=False)
    except subprocess.TimeoutExpired:
        return Check("claude 回應測試", False, f"超過 {timeout_s} 秒沒回應", "稍後重試，或檢查網路", required=False)


# ── 模型檔案 ─────────────────────────────────────────────


def check_aligner_model() -> Check:
    repo_id = aligner_model_id()
    if _hf_cached(repo_id, "config.json"):
        return Check("逐字對位模型檔案", True, f"{repo_id} 已在本機快取")
    return Check("逐字對位模型檔案", False, f"{repo_id} 還沒下載", "跑「bookclub models download」")


def check_cosyvoice_model_files() -> Check:
    target = cosyvoice_model_dir()
    missing = [f for f in COSYVOICE3_FILES if not (target / f).is_file()]
    link = target / "llm.pt"
    link_ok = link.is_symlink() and os.readlink(link) == "llm.rl.pt" and (target / "llm.rl.pt").is_file()
    if not missing and link_ok:
        return Check("CosyVoice3 模型檔案", True, f"{target} 檔案齊全，llm.pt 連結正常")
    problems = []
    if missing:
        problems.append(f"缺 {len(missing)} 個檔案")
    if not link_ok:
        problems.append("llm.pt 連結不對或缺 llm.rl.pt")
    return Check("CosyVoice3 模型檔案", False, "、".join(problems), "跑「bookclub models download」")


def check_pyannote_models() -> Check:
    missing = [r for r in PYANNOTE_REPOS if not _hf_cached(r, PYANNOTE_CONFIG_FILENAME, PYANNOTE_CACHE_DIR)]
    if not missing:
        return Check("pyannote 模型檔案", True, "三組都在本機快取", required=False)
    return Check(
        "pyannote 模型檔案", False, f"{len(missing)}/{len(PYANNOTE_REPOS)} 組還沒下載",
        "先完成「Hugging Face 登入」那一項列出的三步驟，再跑「bookclub models download」",
        required=False,
    )


# ── 資料夾與設定 ─────────────────────────────────────────


def check_data_folder() -> Check:
    d = data_dir()
    if d.is_dir():
        return Check("老師資料夾", True, str(d))
    return Check("老師資料夾", False, f"{d} 不存在", "跑 install.sh（會從 profile.example/ 建立範本），或手動建立")


def check_settings_file() -> Check:
    p = settings_path()
    if p.is_file():
        return Check("settings.toml", True, str(p))
    return Check("settings.toml", False, f"{p} 不存在", "跑 install.sh，或從 profile.example/settings.toml 複製一份")


def check_roster_file() -> Check:
    p = data_dir() / "名冊.csv"
    if p.is_file():
        return Check("名冊.csv", True, str(p))
    return Check("名冊.csv", False, f"{p} 不存在", "跑 install.sh，或從 profile.example/名冊.csv 複製一份")


def run_checks(include_claude_call: bool) -> list[Check]:
    checks = [
        check_os_version(),
        check_chip(),
        check_memory(),
        check_disk_space(),
        check_ffmpeg(),
        check_python_version(),
        check_torch(),
        check_numpy(),
        check_import("可匯入 qwen_asr（逐字對位）", "qwen_asr", "跑 uv pip install --python .venv/bin/python --no-deps qwen-asr==0.0.6"),
        check_import("可匯入 pyannote.audio（分辨誰在說話）", "pyannote.audio", "跑 install.sh 重新安裝套件"),
        check_cosyvoice_import(),
        check_import("onnxruntime", "onnxruntime", "跑 install.sh 重新安裝套件"),
        check_transformers_version(),
        check_groq_key(),
        check_hf_login(),
        check_claude_cli(),
    ]
    if include_claude_call:
        checks.append(check_claude_responds())
    checks += [
        check_aligner_model(),
        check_cosyvoice_model_files(),
        check_pyannote_models(),
        check_data_folder(),
        check_settings_file(),
        check_roster_file(),
    ]
    return checks


def print_sources() -> None:
    """印出「現在用的模型與原始碼在哪裡」：是倉庫自己的，還是 symlink 沿用別處的。
    排錯時很常需要這個資訊（例如沿用了別人裝的版本，行為跟預期不同）。"""
    try:
        from bookclub import detect
    except Exception:  # detect 壞掉不該讓 doctor 整個掛掉
        return
    print()
    print("── 現在用的原始碼與模型 ──")
    for item in (detect.describe_current_cosyvoice_source(), detect.describe_current_cosyvoice_model()):
        print(item.line() if hasattr(item, "line") else f"{item.name}：{item.path}｜{item.detail}")


def print_report(checks: list[Check]) -> bool:
    for c in checks:
        print(c.line())
    print_sources()

    required_failed = [c for c in checks if c.required and not c.ok]
    print()
    if required_failed:
        names = "、".join(c.name for c in required_failed)
        print(f"總結：還有 {len(required_failed)} 項必要檢查沒過（{names}），先照上面的修法處理。")
    else:
        optional_failed = [c for c in checks if not c.required and not c.ok]
        if optional_failed:
            print("總結：必要項目全部通過 ✅。上面的 ❌ 是非必要項目（例如 Hugging Face 登入、GROQ 金鑰），之後要用到相關功能時再處理即可。")
        else:
            print("總結：全部通過 ✅")
    return not required_failed


# ── --smoke：實際跑一次三個模型 ────────────────────────────

SMOKE_SCRIPTS = [
    ("align", "smoke_align.py", 300),
    ("tts", "smoke_tts.py", 900),
    ("diarize", "smoke_diarize.py", 300),
]


def _ensure_samples() -> bool:
    out_dir = repo_root() / "tests" / "smoke" / "out"
    if (out_dir / "one.wav").is_file() and (out_dir / "two.wav").is_file():
        return True
    print("找不到測試用音檔，先產生（tests/smoke/make_sample.py，用 macOS 內建的 say）...")
    script = repo_root() / "tests" / "smoke" / "make_sample.py"
    result = subprocess.run([sys.executable, str(script)], cwd=str(repo_root()))
    return result.returncode == 0


def run_smoke() -> dict:
    print()
    print("開始跑試跑腳本（--smoke）：會實際載入三個模型、各處理一小段測試音檔，需要幾分鐘。")
    if not _ensure_samples():
        print("⚠️ 測試音檔產生失敗，略過 --smoke")
        return {"ok": False, "error": "make_sample 失敗"}

    smoke_dir = repo_root() / "tests" / "smoke"
    results: dict[str, dict] = {}
    rows = []
    for key, script_name, timeout_s in SMOKE_SCRIPTS:
        script = smoke_dir / script_name
        print(f"  跑 {script_name}（逾時 {timeout_s} 秒）...")
        t0 = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, str(script)],
                cwd=str(repo_root()),
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            elapsed = time.time() - t0
            parsed = None
            for line in reversed(proc.stdout.strip().splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        parsed = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    break
            ok = proc.returncode == 0 and parsed is not None and parsed.get("ok", True)
            results[key] = {
                "ok": ok,
                "wall_s": round(elapsed, 1),
                "returncode": proc.returncode,
                "parsed": parsed,
                "stderr_tail": "" if ok else proc.stderr[-2000:],
            }
            rows.append((key, ok, elapsed, parsed))
        except subprocess.TimeoutExpired:
            elapsed = time.time() - t0
            results[key] = {"ok": False, "wall_s": round(elapsed, 1), "error": f"超過 {timeout_s} 秒逾時"}
            rows.append((key, False, elapsed, None))

    print()
    print("耗時表：")
    print(f"  {'項目':<10}{'結果':<6}{'總耗時(秒)':<12}細節")
    for key, ok, elapsed, parsed in rows:
        mark = "✅" if ok else "❌"
        detail = ", ".join(f"{k}={v}" for k, v in parsed.items() if k != "machine") if parsed else ""
        print(f"  {key:<10}{mark:<6}{elapsed:<12.1f}{detail}")

    out_record = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "machine": platform.machine(),
        "results": results,
    }

    save_dir = data_dir() / "環境檢查"
    try:
        save_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = save_dir / f"smoke-{stamp}.json"
        out_path.write_text(json.dumps(out_record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n結果已存成：{out_path}（之後夥伴要回傳這份）")
    except Exception as exc:
        print(f"⚠️ 結果存檔失敗：{exc}")

    return out_record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bookclub doctor", description="檢查環境是否就緒")
    parser.add_argument("--smoke", action="store_true", help="額外跑三個試跑腳本（實際載入模型、處理測試音檔），需要幾分鐘")
    parser.add_argument("--claude", action="store_true", help="額外實際呼叫一次 claude -p 測試有沒有反應")
    args = parser.parse_args(argv)

    print(f"bookclub doctor — {datetime.now().astimezone().isoformat(timespec='seconds')}")
    print()
    checks = run_checks(include_claude_call=args.claude)
    ok = print_report(checks)

    if args.smoke:
        run_smoke()

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
