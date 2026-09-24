"""bookclub/overlap.py 的單元測試：只測 02 規格的兩條自動過濾規則跟輔助函式，
不碰 pyannote 模型、不碰真的音檔。用一個小型 stub 頂替 pyannote 的 Annotation
（`_decide_skip`／`_teacher_turn_contains` 只用得到 `label_timeline(label)`）。

獨立可跑：.venv/bin/python tests/test_overlap.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bookclub import overlap as ov


@dataclass
class _Turn:
    start: float
    end: float


class _StubAnn:
    """只提供 `_decide_skip`／`_teacher_turn_contains` 需要的 label_timeline(label)。"""

    def __init__(self, turns_by_label: dict[str, list[_Turn]]):
        self._turns = turns_by_label

    def label_timeline(self, label: str):
        return self._turns.get(label, [])


# ---------- 規則 1：兩位學員之間的重疊 → 跳過 ----------

def test_two_students_overlap_is_skipped():
    ann = _StubAnn({})
    roles = {"SPEAKER_00": ("不是老師", 0.1), "SPEAKER_01": ("不是老師", 0.15)}
    skipped, reason = ov._decide_skip(ann, roles, ["SPEAKER_00", "SPEAKER_01"], 10.0, 10.3, echo_max_s=0.5)
    assert skipped is True
    assert reason == "兩位學員之間的重疊"


def test_teacher_and_student_overlap_not_skipped_by_rule1():
    ann = _StubAnn({"老師": [_Turn(0.0, 100.0)]})
    roles = {"老師": ("老師", 0.9), "學員": ("不是老師", 0.1)}
    # 落在老師連續發言區間內、且短於門檻 → 規則 2 會跳過，這裡先確認不是被規則 1 誤判
    skipped, reason = ov._decide_skip(ann, roles, ["老師", "學員"], 10.0, 10.3, echo_max_s=0.5)
    assert skipped is True
    assert reason == "老師講話時學員短附和"


# ---------- 規則 2：老師講話時學員的短附和（< echo_max_s 秒，落在老師連續發言區間內） ----------

def test_short_interjection_inside_teacher_turn_is_skipped():
    ann = _StubAnn({"老師": [_Turn(5.0, 20.0)]})
    roles = {"老師": ("老師", 0.9), "學員": ("不是老師", 0.05)}
    skipped, reason = ov._decide_skip(ann, roles, ["老師", "學員"], 10.0, 10.3, echo_max_s=0.5)
    assert skipped is True
    assert reason == "老師講話時學員短附和"


def test_interjection_too_long_is_not_skipped():
    """附和長度 >= echo_max_s：不符合規則 2，保留給人決定。"""
    ann = _StubAnn({"老師": [_Turn(5.0, 20.0)]})
    roles = {"老師": ("老師", 0.9), "學員": ("不是老師", 0.05)}
    skipped, reason = ov._decide_skip(ann, roles, ["老師", "學員"], 10.0, 10.6, echo_max_s=0.5)
    assert skipped is False
    assert reason is None


def test_interjection_outside_teacher_turn_is_not_skipped():
    """老師的發言區間沒有涵蓋整個重疊（例如老師的話在重疊當下剛好結束），不符合
    規則 2「落在老師說話區間內部」，保留給人決定。"""
    ann = _StubAnn({"老師": [_Turn(5.0, 10.1)]})  # 老師 10.1 秒就停了，重疊到 10.3
    roles = {"老師": ("老師", 0.9), "學員": ("不是老師", 0.05)}
    skipped, reason = ov._decide_skip(ann, roles, ["老師", "學員"], 10.0, 10.3, echo_max_s=0.5)
    assert skipped is False
    assert reason is None


def test_unknown_role_never_auto_skipped():
    """有一方相似度落在「不確定」區間（既不像老師也不確定是學員）：兩條規則都不
    自動套用，保留給人決定，避免誤殺。"""
    ann = _StubAnn({"老師": [_Turn(0.0, 100.0)]})
    roles = {"老師": ("老師", 0.9), "?": ("不確定", 0.45)}
    skipped, reason = ov._decide_skip(ann, roles, ["老師", "?"], 10.0, 10.2, echo_max_s=0.5)
    assert skipped is False
    assert reason is None

    roles2 = {"?": ("不確定", 0.45), "??": ("不是老師", 0.1)}
    skipped2, reason2 = ov._decide_skip(ann, roles2, ["?", "??"], 10.0, 10.2, echo_max_s=0.5)
    assert skipped2 is False
    assert reason2 is None


def test_two_way_exchange_kept_for_review():
    """一來一往的問答（老師講完換學員接著講一段不短的話，不是附和）：兩條規則都
    不適用，保留給人決定——這才是覆核清單真正要處理的重疊。"""
    ann = _StubAnn({"老師": [_Turn(0.0, 10.0)]})
    roles = {"老師": ("老師", 0.9), "學員": ("不是老師", 0.1)}
    skipped, reason = ov._decide_skip(ann, roles, ["老師", "學員"], 9.5, 12.0, echo_max_s=0.5)
    assert skipped is False
    assert reason is None


# ---------- _teacher_turn_contains ----------

def test_teacher_turn_contains_true_and_false():
    ann = _StubAnn({"老師": [_Turn(5.0, 20.0), _Turn(30.0, 40.0)]})
    assert ov._teacher_turn_contains(ann, "老師", 10.0, 10.3) is True
    assert ov._teacher_turn_contains(ann, "老師", 25.0, 25.3) is False
    # 邊界誤差容忍（eps 預設 0.05）
    assert ov._teacher_turn_contains(ann, "老師", 4.98, 20.02) is True


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"通過：{t.__name__}")
    print(f"\n共 {len(tests)} 個測試通過")


if __name__ == "__main__":
    _run_all()
