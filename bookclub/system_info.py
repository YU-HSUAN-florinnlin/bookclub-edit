"""安裝前也能執行的跨平台資訊工具；只依賴標準函式庫。"""
from __future__ import annotations

import argparse
import platform
import re
from pathlib import Path


def detect_os() -> str:
    return platform.system()


def read_system_file(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def parse_os_release(text: str) -> str:
    """讀取資料而非執行 shell；支援引號及 os-release 的跳脫字元。"""
    values = {}
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if not sep or key not in {"PRETTY_NAME", "NAME", "VERSION"}:
            continue
        value = value.strip()
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                continue
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                value = re.sub(r'\\([\\"$`])', r'\1', value)
        values[key] = value
    return values.get("PRETTY_NAME") or " ".join(
        values.get(key, "") for key in ("NAME", "VERSION")
    ).strip() or "無法偵測"


def parse_meminfo(text: str) -> int | None:
    """回傳 MemTotal 的 bytes；缺漏或格式錯誤時回傳 None。"""
    match = re.search(r"^MemTotal:\s+(\d+)\s+kB\s*$", text, re.MULTILINE)
    if not match or int(match[1]) <= 0:
        return None
    return int(match[1]) * 1024


def is_wsl(version: str) -> bool:
    return "microsoft" in version.lower()


def python_target(system: str, machine: str, intel: bool = False) -> str:
    if intel and system != "Darwin":
        raise ValueError("--intel 只適用於 macOS，Linux／WSL2 不支援這個參數")
    if system not in {"Darwin", "Linux"}:
        raise ValueError(f"不支援的作業系統：{system}")
    arch = "aarch64" if machine in {"arm64", "aarch64"} else machine
    if intel:
        arch = "x86_64"
    if arch not in {"aarch64", "x86_64"}:
        raise ValueError(f"不支援的處理器架構：{machine}")
    suffix = "macos-" + arch + "-none" if system == "Darwin" else "linux-" + arch + "-gnu"
    return "cpython-3.11-" + suffix


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["os", "memory", "wsl", "target"])
    parser.add_argument("args", nargs="*")
    args = parser.parse_args()
    if args.action == "os":
        print(parse_os_release(read_system_file("/etc/os-release")))
    elif args.action == "memory":
        memory = parse_meminfo(read_system_file("/proc/meminfo"))
        print(memory // 1024**3 if memory is not None else "")
    elif args.action == "wsl":
        print("1" if is_wsl(read_system_file("/proc/version")) else "0")
    else:
        try:
            print(python_target(args.args[0], args.args[1], args.args[2] == "1"))
        except ValueError as exc:
            parser.exit(1, f"❌ {exc}\n")


if __name__ == "__main__":
    main()
