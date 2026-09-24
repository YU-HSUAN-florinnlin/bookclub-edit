"""Phase 0 試跑用的假錄音產生器。

用 macOS 內建的「唸出來」功能（`say` 指令）合成假的讀書會對話，純粹是給
smoke_align.py／smoke_tts.py／smoke_diarize.py 當測試素材——裡面的名字
（小明、美華）都是虛構的，不是真的學員，說話的也是電腦合成的語音，不是
真人錄音。

產生到 tests/smoke/out/（這個資料夾不進 git，見 .gitignore）：
  one.wav  單人一句、約 5 秒——給「逐字對位」測試用
  two.wav  兩人各一句、中間留 0.5 秒空白、最後疊出約 1 秒的重疊——給
           「分辨誰在說話」與「重疊偵測」測試用

獨立可跑：.venv/bin/python tests/smoke/make_sample.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent / "out"
SAMPLE_RATE = 16000

VOICE_ONE = "Meijia"
SENTENCE_ONE = "小明，你剛剛分享的那一段，美華也有類似的經驗。"
SENTENCE_TWO_B = "對啊，我那時候也是這樣想的，後來才慢慢調整過來。"

PREFERRED_SECOND_VOICES = ["Eddy (中文（台灣）)", "Sandy (中文（台灣）)", "Shelley (中文（台灣）)"]


def find_second_voice() -> str:
    """找一個台灣腔中文、且不是 Meijia 的系統語音。不同電腦裝的語音包可能
    不一樣，現場用 `say -v '?'` 查，不寫死成單一名字。"""
    result = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, check=True)
    available = []
    for line in result.stdout.splitlines():
        m = re.match(r"^(.*?)\s{2,}zh_TW\b", line)
        if m:
            name = m.group(1).strip()
            if name != VOICE_ONE:
                available.append(name)

    for name in PREFERRED_SECOND_VOICES:
        if name in available:
            return name
    if available:
        return available[0]
    raise RuntimeError(
        "找不到第二個台灣腔中文語音。macOS 系統設定 → 輔助使用 → 朗讀內容，"
        "可以看目前裝了哪些語音、或加裝新的。"
    )


def say_to_wav(voice: str, text: str, wav_path: Path) -> None:
    """呼叫 say 產生語音，轉成 16k 單聲道 wav（後面模型都吃這個格式）。"""
    aiff_path = wav_path.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-o", str(aiff_path), text], check=True)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(aiff_path), "-ar", str(SAMPLE_RATE), "-ac", "1", str(wav_path)],
        check=True,
        capture_output=True,
    )
    aiff_path.unlink(missing_ok=True)


def make_one() -> Path:
    path = OUT_DIR / "one.wav"
    say_to_wav(VOICE_ONE, SENTENCE_ONE, path)
    print(f"已產生：{path}")
    return path


def make_two() -> Path:
    import numpy as np
    import soundfile as sf

    a_path = OUT_DIR / "_two_a.wav"
    b_path = OUT_DIR / "_two_b.wav"
    say_to_wav(VOICE_ONE, SENTENCE_ONE, a_path)
    second_voice = find_second_voice()
    say_to_wav(second_voice, SENTENCE_TWO_B, b_path)

    audio_a, sr_a = sf.read(str(a_path), dtype="float32")
    audio_b, sr_b = sf.read(str(b_path), dtype="float32")
    if sr_a != SAMPLE_RATE or sr_b != SAMPLE_RATE:
        raise RuntimeError(f"轉檔後取樣率不對：{sr_a}／{sr_b}（預期 {SAMPLE_RATE}）")

    gap = np.zeros(int(0.5 * SAMPLE_RATE), dtype="float32")

    # 最後疊約 1 秒重疊：把兩段語音的「尾巴」直接相加，模擬兩人同時講話
    overlap_len = min(int(1.0 * SAMPLE_RATE), len(audio_a), len(audio_b))
    overlap = audio_a[-overlap_len:] + audio_b[-overlap_len:]
    peak = float(np.max(np.abs(overlap))) if overlap_len else 0.0
    if peak > 1.0:
        overlap = overlap / peak

    combined = np.concatenate([audio_a, gap, audio_b, overlap])

    path = OUT_DIR / "two.wav"
    sf.write(str(path), combined, SAMPLE_RATE)
    a_path.unlink(missing_ok=True)
    b_path.unlink(missing_ok=True)
    print(f"已產生：{path}（第二個語音：{second_voice}，總長 {len(combined) / SAMPLE_RATE:.1f} 秒）")
    return path


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        make_one()
        make_two()
    except Exception as exc:
        print(f"產生測試音檔失敗：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
