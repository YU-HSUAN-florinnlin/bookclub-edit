"""bookclub/proofread.py 的單元測試（純函式，不載入聲紋模型）。

獨立可跑：.venv/bin/python tests/test_proofread.py
"""

from __future__ import annotations

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
