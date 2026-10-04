"""10-04 #127：學員段落的聲音有不少是老師（三成以上；純函式，不載入模型）。
欄位名稱 `多半是老師` 沿用（API／inspect 已經在用），意思是「達到門檻」。

為什麼要有：段落分析有時把老師的講話標成學員段落（10-04 實例：一段 89 秒標成「學員2」，裡面 28 句有 27 句
聲紋判斷是老師）。人如果沒聽出來就按通過，老師的話會被當成學員、用替代聲音重念。
這裡用每一句的聲紋判斷（`說話者判斷.json`，有 `聲紋判斷` 就用它——冥想引導、導讀段落裡被文字改成老師的不算）
算比例，在第 3 步學員段落卡片與第 4 步總檢查提醒。不擋通過。
"""

from __future__ import annotations

# 10-04 宇軒定 0.3（原本推定 0.5）：多報一張卡的成本低，漏報的後果是老師的話被當成學員重念。
# 第一堂實例：T027 是 89 秒、28 句裡 27 句老師；T025 比例 0.336（0.5 會漏掉）；其他學員段落最高約 0.03。之後看誤報再調
TEACHER_RATIO = 0.3      # 老師的句子秒數 ÷（老師＋不是老師＋不確定的句子秒數，「太短」不算）≥ 這個比例（三成）
TEACHER_MIN_S = 5.0      # 而且老師的句子合計至少這麼多秒（太短的段落一兩句誤判不算）
COUNTED = ("老師", "不是老師", "不確定")


def _label(s: dict) -> str | None:
    return s.get("聲紋判斷", s.get("label"))


def turn_voice(turn: dict, sents: list[dict], by_id: dict | None = None) -> dict | None:
    """一個段落裡聲紋判成老師的比例（純函式）。句子照段落的 `句子` 編號找（沒有編號、或一個都對不到，就用中點落在段落裡的句子），
    秒數夾在段落起訖裡面。回傳 {句數, 老師句數, 老師秒, 計入秒, 比例, 多半是老師}；沒有可以算的句子回傳 None。"""
    by_id = by_id if by_id is not None else {s.get("id"): s for s in sents}
    a, b = float(turn["start"]), float(turn["end"])
    ss = [by_id[i] for i in turn.get("句子") or [] if i in by_id]
    if not ss:   # 沒有編號、或編號一個都對不到（10-04 審查 H）→ 用中點落在段落裡的句子
        ss = [s for s in sents if a <= (float(s["start"]) + float(s["end"])) / 2 <= b]
    n = n_t = 0
    t_s = all_s = 0.0
    for s in ss:
        lab = _label(s)
        if lab not in COUNTED:
            continue
        d = min(float(s["end"]), b) - max(float(s["start"]), a)
        if d <= 0:
            continue
        n += 1
        all_s += d
        if lab == "老師":
            n_t += 1
            t_s += d
    if not n or all_s <= 0:
        return None
    ratio = t_s / all_s
    return {"句數": n, "老師句數": n_t, "老師秒": round(t_s, 1), "計入秒": round(all_s, 1), "比例": round(ratio, 3),
            "多半是老師": ratio >= TEACHER_RATIO and t_s >= TEACHER_MIN_S}


def warn_text(v: dict) -> str:
    """第 3 步卡片上的提醒（只有數字）。"""
    return (f"這一段的聲音特徵有不少像老師（{v['句數']} 句裡 {v['老師句數']} 句、約 {v['老師秒']:.0f} 秒）。"
            "請聽一下：是老師在講話就把說話者改成老師")
