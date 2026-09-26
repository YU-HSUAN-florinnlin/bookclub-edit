"""bookclub/cutsuggest.py（建議刪除段落）的測試：只測純函式與快取，不呼叫 Claude。

獨立可跑：.venv/bin/python tests/test_cutsuggest.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import cutsuggest  # noqa: E402


def test_format_lines_marks_long_gaps():
    s = [{"start": 20.0, "end": 22.0, "text": "大家好"}, {"start": 23.0, "end": 25.0, "text": "開始囉"},
         {"start": 60.0, "end": 61.0, "text": "回來了"}]
    out = cutsuggest.format_lines(s, 100.0).splitlines()
    assert out[0] == "——空白 20 秒——" and out[1].startswith("0:00:20.0-0:00:22.0|大家好")
    assert out[3] == "——空白 35 秒——" and out[-1] == "——空白 39 秒（到影片結尾）——"


def test_parse_reply_rules():
    reply = """好的，結果如下：
{"建議":[
 {"類型":"開頭空白","起":"0:00:05","迄":"0:01:11.0","原因":"還沒開始"},
 {"類型":"直播互動","起":"0:21:06","迄":"0:21:40","原因":"念留言"},
 {"類型":"直播互動","起":"0:21:40.5","迄":"0:22:14","原因":"續"},
 {"類型":"亂寫","起":"0:30:00","迄":"0:31:00","原因":"x"},
 {"類型":"技術問題","起":"0:40:00","迄":"0:40:01","原因":"太短"},
 {"類型":"技術問題","起":"壞掉","迄":"0:40:01","原因":"看不懂"},
 {"類型":"結尾道別","起":"1:37:30","迄":"1:37:40","原因":"再見"}]}"""
    items = cutsuggest.parse_reply(reply, 5864.0)
    assert [x["類型"] for x in items] == ["開頭空白", "直播互動", "結尾道別"]
    assert items[0]["start"] == 0.0 and items[0]["end"] == 71.0                  # 開頭空白從 0 開始
    assert items[1]["start"] == 1266.0 and items[1]["end"] == 1334.0             # 同類型相連的合併
    assert items[2]["start"] == 5850.0 and items[2]["end"] == 5864.0             # 結尾道別刪到結尾
    try:
        cutsuggest.parse_reply("沒有 JSON", 100)
        raise AssertionError
    except ValueError:
        pass


def test_cached_result_is_reused():
    w = Path(tempfile.mkdtemp())
    (w / "校對").mkdir()
    data = {"建議": [{"id": "S1", "類型": "開頭空白", "start": 0, "end": 10, "原因": "x"}]}
    (w / "校對" / "刪除建議.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert cutsuggest.suggest_cuts(w, [], duration=100, log=lambda *_: None) == data     # 有快取就不叫 Claude


def _run_all() -> int:
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, func in tests:
        try:
            func()
            print(f"✓ {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback

            traceback.print_exc()
            print(f"✗ {name}：{exc!r}")
    print(f"{len(tests) - failed}/{len(tests)} 通過")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
