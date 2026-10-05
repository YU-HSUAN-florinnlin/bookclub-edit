"""10-04 #110：讀不到 Groq 金鑰時，第 4 步開始前擋下；照跑的話每一句記成「內容檢查沒做成」、標要人聽。"""
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from bookclub import tts
from bookclub.execute import execute_key_problem
sys.path.insert(0, str(Path(__file__).parent))
from test_tts import SR, FakeSynth  # noqa: E402


def test_key_problem_blocks_generation_only():
    assert execute_key_problem(env={}) and "--allow-no-key" in execute_key_problem(env={})
    assert execute_key_problem(env={"GROQ_API_KEY": "gsk_假的"}) is None
    assert execute_key_problem(env={}, allow=True) is None
    assert execute_key_problem(env={}, reassemble_only=True) is None
    assert execute_key_problem(env={}, only_steps=["組裝"]) is None
    assert execute_key_problem(env={}, only_steps=["學員重念", "組裝"])


def test_key_problem_message_per_platform():
    """Mac 只講 ~/.zshrc／啟動.command；Linux／WSL2 只講 ~/.profile（~/.bashrc 讀不到），不叫人雙擊啟動.command。"""
    from bookclub import server as sv

    mac = execute_key_problem(env={}, system="Darwin")
    linux = execute_key_problem(env={}, system="Linux")
    assert "啟動.command" in mac and "~/.zshrc" in mac and "~/.profile" not in mac
    assert "啟動.command" not in linux and "~/.profile" in linux and "~/.bashrc" in linux and "~/.zshrc" not in linux
    assert "--allow-no-key" in mac and "--allow-no-key" in linux
    # 跟網頁伺服器同一套說法（怎麼補金鑰那一句共用）
    assert sv.groq_key_howto("Linux", cli=True) in linux and sv.groq_key_howto("Darwin", cli=True) in mac
    # 網頁第 4 步被擋下的那一句：WSL2 不提啟動.command，不說「沒辦法轉文字」
    web_linux = sv.groq_key_execute_message("Linux")
    assert "啟動.command" not in web_linux and "~/.profile" in web_linux and "轉文字" not in web_linux
    assert "Ctrl+C" in web_linux and "--allow-no-key" not in web_linux
    assert "啟動.command" in sv.groq_key_execute_message("Darwin")


def test_no_key_marks_check_not_done_then_rechecks_with_key():
    old = os.environ.pop("GROQ_API_KEY", None)
    try:
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            ref = work / "參考音"
            ref.mkdir()
            sf.write(str(ref / "ref.wav"), np.zeros(SR, dtype=np.float32), SR)
            (ref / "ref.txt").write_text("參考音逐字稿", encoding="utf-8")
            sentences = {"A": "這個是我們今天課程的重點之一。", "B": "我們下週見。"}
            sp = work / "句子.json"
            sp.write_text(json.dumps([{"id": k, "text": v} for k, v in sentences.items()], ensure_ascii=False),
                          encoding="utf-8")

            synth = FakeSynth()
            log = tts.generate_teacher(work, sp, synth=synth, check_similarity=False, log=lambda s: None)
            assert [r["要人聽"] for r in log["句子"]] == [True, True] and len(synth.calls) == 2
            assert all(r["嘗試"][0]["內容檢查沒做成"] for r in log["句子"])

            # 還是沒有金鑰再跑一次：沿用聲音，維持要人聽
            synth2 = FakeSynth()
            log = tts.generate_teacher(work, sp, synth=synth2, check_similarity=False, redo=True, log=lambda s: None)
            assert synth2.calls == [] and [r["要人聽"] for r in log["句子"]] == [True, True]

            # 讀得到金鑰了（這裡直接給 hear）：聲音沿用，只補做檢查
            synth3 = FakeSynth()
            log = tts.generate_teacher(work, sp, synth=synth3, check_similarity=False, redo=True, log=lambda s: None,
                                       hear=lambda p: sentences[Path(p).name.split("_")[0]])
            assert synth3.calls == [] and [r["要人聽"] for r in log["句子"]] == [False, False]
    finally:
        if old is not None:
            os.environ["GROQ_API_KEY"] = old


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
