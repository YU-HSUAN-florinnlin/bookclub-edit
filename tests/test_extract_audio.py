"""10-03 第八批（#11）：抽音中斷的殘檔不當快取。

用 ffmpeg 產生幾秒的假影片（彩色畫面＋正弦波），不載入模型、不打網路。

獨立可跑：.venv/bin/python tests/test_extract_audio.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import soundfile as sf

from bookclub import analyze, exchange, refpick
from bookclub import transcribe as tc

VIDEO_S = 4.0


def _make_video(path: Path, seconds: float = VIDEO_S) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"color=c=gray:s=64x64:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)


def _write_flac(path: Path, seconds: float) -> None:
    sf.write(str(path), np.zeros(int(seconds * 16000), dtype=np.float32), 16000, format="FLAC")


def test_partial_audio_is_reextracted_and_kept():
    """只有前半段的 audio.flac → 改名留著、重新抽；轉文字與挑參考音走同一支。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        video = root / "假影片.mp4"
        _make_video(video)
        w = root / "工作區"
        w.mkdir()
        _write_flac(w / "audio.flac", 1.5)
        logs = []
        out, took = refpick.ensure_audio(video, w, log=logs.append)
        assert out == w / "audio.flac" and took > 0
        assert abs(sf.info(str(out)).duration - VIDEO_S) < 0.5
        kept = list(w.glob("audio.殘檔-*.flac"))
        assert len(kept) == 1 and abs(sf.info(str(kept[0])).duration - 1.5) < 0.01
        assert logs and "殘檔" in logs[0] and "重新抽" in logs[0]
        assert not (w / refpick.AUDIO_TMP_NAME).exists()

        # 讀不開的殘檔（中斷時只寫了開頭幾個位元組）也重新抽
        (w / "audio.flac").write_bytes(b"fLaC\x00")
        refpick.ensure_audio(video, w, log=lambda *_: None)
        assert abs(sf.info(str(w / "audio.flac")).duration - VIDEO_S) < 0.5


def test_full_length_audio_is_reused():
    """長度對的 audio.flac → 沿用，不重抽（轉文字、挑參考音兩個入口都一樣）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        video = root / "假影片.mp4"
        _make_video(video)
        w = root / "工作區"
        w.mkdir()
        _write_flac(w / "audio.flac", refpick.video_audio_len_s(video))
        before = (w / "audio.flac").stat().st_mtime_ns
        for fn in (tc.extract_audio, refpick._step1_extract_audio, refpick.ensure_audio):
            out, took = fn(video, w)
            assert took == 0.0 and out.stat().st_mtime_ns == before
        assert not list(w.glob("audio.殘檔-*"))


def test_ffmpeg_failure_leaves_no_audio():
    """ffmpeg 中途失敗（寫了一半的暫存檔）→ 不留下 audio.flac，也不留暫存檔；下次重抽。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        video = root / "假影片.mp4"
        _make_video(video)
        w = root / "工作區"
        w.mkdir()
        real_run = refpick.subprocess.run

        def broken_run(cmd, *a, **k):
            if cmd and cmd[0] == "ffmpeg":
                Path(cmd[-1]).write_bytes(b"fLaC half")
                raise subprocess.CalledProcessError(255, cmd)
            return real_run(cmd, *a, **k)

        refpick.subprocess.run = broken_run
        try:
            try:
                tc.extract_audio(video, w)
                raise AssertionError("ffmpeg 失敗還沒出錯")
            except subprocess.CalledProcessError:
                pass
        finally:
            refpick.subprocess.run = real_run
        assert not (w / "audio.flac").exists() and not (w / refpick.AUDIO_TMP_NAME).exists()
        out, took = tc.extract_audio(video, w)
        assert took > 0 and abs(sf.info(str(out)).duration - VIDEO_S) < 0.5

        # 壞掉的影片（ffmpeg 真的失敗）：一樣不留 audio.flac
        bad = root / "壞掉.mp4"
        bad.write_bytes(b"not a video")
        w2 = root / "工作區2"
        try:
            refpick.ensure_audio(bad, w2)
            raise AssertionError("壞掉的影片還沒出錯")
        except subprocess.CalledProcessError:
            pass
        assert not (w2 / "audio.flac").exists() and not (w2 / refpick.AUDIO_TMP_NAME).exists()


def test_import_review_uses_shared_extract():
    """匯入覆核結果也走同一支（不再自己只看檔案在不在）。"""
    assert "ensure_audio" in Path(exchange.__file__).read_text(encoding="utf-8")


def test_transcript_tail_warning():
    """逐字稿最後一句離片尾超過 3 分鐘 → 「要注意」加一條（不擋）；差不多到片尾就不加。"""
    sents = [{"start": 0.0, "end": 10.0}, {"start": 20.0, "end": 600.0}]
    msg = analyze.transcript_tail_warning(sents, 600.0 + analyze.TRANSCRIPT_TAIL_WARN_S + 5)
    assert msg and "逐字稿只到" in msg and "audio.flac" in msg
    assert analyze.transcript_tail_warning(sents, 650.0) is None
    assert analyze.transcript_tail_warning(sents, None) is None
    assert analyze.transcript_tail_warning([], 1000.0)


def _run_all() -> int:
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, func in tests:
        try:
            func()
            print(f"✓ {name}")
        except Exception as exc:
            failed += 1
            print(f"✗ {name}：{exc!r}")
    print(f"{len(tests) - failed}/{len(tests)} 通過")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
