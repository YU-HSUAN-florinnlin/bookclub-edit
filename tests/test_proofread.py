"""bookclub/proofread.py 的單元測試（純函式，不載入聲紋模型）。

獨立可跑：.venv/bin/python tests/test_proofread.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 10-01：這支測試建的暫存資料夾跑完自己清（要在 tempfile 之前）

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from bookclub import proofread


def test_replace_terms_longest_first():
    terms = [{"寫法": "小美", "代號": "Amy"}, {"寫法": "小美美", "代號": "Mia"}, {"寫法": "鳳山", "代號": "南部"}]
    text, done = proofread.replace_terms("小美美跟小美都在鳳山", terms)
    assert text == "Mia跟Amy都在南部" and len(done) == 3


def test_sound_alike_hint_not_replaced():
    roster = [{"寫法": "芬芬", "代號": "Laura"}]
    hints = proofread.sound_alike_hints("我跟紛紛說過", roster)
    assert hints and hints[0]["可能是"] == "Laura"
    assert proofread.sound_alike_hints("我跟Laura說過", roster) == []


def test_cluster_voices_orders_by_seconds():
    a, b = np.array([1.0, 0.0]), np.array([0.0, 1.0])
    embs = np.stack([a, b, a, a])
    assert proofread.cluster_voices(embs, [1.0, 9.0, 1.0, 1.0]) == ["B", "A", "B", "B"]


def test_save_item_accumulates_time_and_progress():
    import json, tempfile
    with tempfile.TemporaryDirectory() as d:
        w = Path(d)
        (w / "校對").mkdir()
        items = [{"id": f"s{i}", "start": i * 10.0, "end": i * 10.0 + 6.0, "校對稿": "原句", "原文": "原句",
                  "聲音": "A", "聲音是猜的": True, "已校對": False, "校對秒數": None} for i in range(2)]
        (w / "校對" / "校對稿.json").write_text(json.dumps({"句子": items, "聲音": {"A": {"秒數": 12, "學員": None}}},
                                                        ensure_ascii=False), encoding="utf-8")
        (w / "說話者判斷.json").write_text(json.dumps({"sentences": [
            {"start": 0, "end": 600, "label": "不是老師"}, {"start": 600, "end": 900, "label": "老師"}]}), encoding="utf-8")
        proofread.save_item(w, "s0", {"加秒數": 10})
        r = proofread.save_item(w, "s0", {"校對稿": "改過的句子", "已校對": True, "加秒數": 500, "聲音": "B"})
        it = r["句子"]
        assert it["校對秒數"] == 10 + proofread.MAX_COUNT_S and it["聲音"] == "B" and not it["聲音是猜的"]
        p = r["進度"]
        assert p["已校對"] == 1 and p["改過字的句數"] == 1 and p["整支學員聲音分鐘"] == 10.0
        # 6 秒聲音花 190 秒 → 每分鐘聲音約 31.7 分鐘；整支 600 秒學員聲音 → 約 5.3 小時
        assert abs(p["推算整支要花小時"] - 5.3) < 0.1
        proofread.set_voice(w, "A", "Laura")
        assert json.loads((w / "校對" / "校對稿.json").read_text(encoding="utf-8"))["聲音"]["A"]["學員"] == "Laura"


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
