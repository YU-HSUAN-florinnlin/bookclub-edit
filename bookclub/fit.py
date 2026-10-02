"""把生成的聲音放回原本的時間格（開發計畫 03 §2.6）。

時間格＝原片裡那句話佔的秒數。生成出來的聲音不會剛好一樣長，規則：

| 生成長度 vs 時間格 | 學員 | 老師 |
| --- | --- | --- |
| 比較短 | 後面補靜音 | 差距 ≤ 15% 補靜音；> 15% 標紅 |
| 長 ≤ 15% | ffmpeg `atempo` 微調語速 | 同左 |
| 長 > 15% | 標紅（預設用畫面停格延長，人可改成重生成或改文字） | 標紅，一律讓人決定（有嘴型，不自動停格） |

15% 來自 `~/讀書會剪輯資料/settings.toml` 的 `thresholds.length_tolerance`。
`plan_fit()` 是純函式（只做判斷），`apply_fit()` 才真的呼叫 ffmpeg 產生檔案。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

PAD = "補靜音"
TEMPO = "微調語速"
FLAG = "標紅"

DEFAULT_TOLERANCE = 0.15


def plan_fit(gen_s: float, slot_s: float, role: str, tolerance: float = DEFAULT_TOLERANCE) -> dict:
    """決定這句要怎麼放回時間格。

    role：`老師` 或 `學員`。回傳：
    - `做法`：補靜音／微調語速／標紅
    - `差異比例`：生成長度 ÷ 時間格 − 1（正數＝比較長）
    - `atempo`：微調語速時要加快的倍數（> 1），其他做法是 None
    - `原因`：標紅時給人看的說明，其他做法是 None
    """
    if slot_s <= 0:
        raise ValueError(f"時間格長度要大於 0，收到 {slot_s}")
    if gen_s <= 0:
        raise ValueError(f"生成長度要大於 0，收到 {gen_s}")

    diff = gen_s / slot_s - 1
    plan = {"做法": PAD, "差異比例": round(diff, 4), "atempo": None, "原因": None}

    if diff > tolerance:
        plan["做法"] = FLAG
        plan["原因"] = f"比原本長 {diff:.0%}，超過 {tolerance:.0%}"
        if role == "學員":
            plan["原因"] += "；預設用畫面停格延長，也可以重生成或改短文字"
        else:
            plan["原因"] += "；老師有嘴型，請決定要重生成、改文字或接受"
        return plan

    if diff > 0:
        plan["做法"] = TEMPO
        plan["atempo"] = round(gen_s / slot_s, 4)
        return plan

    if role != "學員" and -diff > tolerance:
        plan["做法"] = FLAG
        plan["原因"] = f"比原本短 {-diff:.0%}，超過 {tolerance:.0%}；老師有嘴型，請決定要重生成、改文字或接受"
    return plan


def fit_filter(plan: dict, slot_s: float) -> str:
    """plan 對應的 ffmpeg 音訊濾鏡字串。標紅的句子照補靜音處理產出試聽檔，
    讓人聽得到「如果直接放進去」會是什麼樣子（太長的部分會被切掉）。"""
    filters = []
    if plan["做法"] == TEMPO and plan["atempo"]:
        # atempo 單次只收 0.5–2.0；我們最多加快 15%，一次就夠
        filters.append(f"atempo={plan['atempo']:.4f}")
    filters.append(f"apad=whole_dur={slot_s:.3f}")
    filters.append(f"atrim=0:{slot_s:.3f}")
    return ",".join(filters)


def apply_fit(src: str | Path, dst: str | Path, slot_s: float, plan: dict) -> Path:
    """用 ffmpeg 產出剛好等於時間格長度的聲音檔。"""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink():   # 10-02 第五批：複製來的工作區裡是連結的話先拿掉，ffmpeg 才不會順著連結覆蓋原本工作區的檔
        dst.unlink()
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-af", fit_filter(plan, slot_s), str(dst)],
        check=True,
    )
    return dst
