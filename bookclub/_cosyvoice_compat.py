"""CosyVoice 相容性修補。

CosyVoice（聲音生成引擎）不是用 pip 裝的，原始碼放在 third_party/CosyVoice
（git clone 來的，固定版本，見 config.cosyvoice_repo_dir() 與 install.sh）。
之後的 tts.py 要用 CosyVoice 時，統一從這裡 `setup()` 或 `load_cosyvoice()`，
不要各自處理，理由如下——修補只有兩件事，但都會讓 CosyVoice 直接跑不出東西：

1. **假的 pyworld 模組**：CosyVoice 程式碼裡有 `import pyworld`，但 pyworld
   只有在「處理訓練資料」時才用得到，我們只做推論（拿現成模型生成聲音），
   用不到它，而且 Mac 沒有 pyworld 的安裝檔，直接裝會失敗。做法：塞一個
   空的假模組騙過 import，反正程式不會真的呼叫到裡面的函式。

2. **拿掉語言模型單步推論的注意力遮罩**：CosyVoice 的語言模型部分用的是
   舊版 transformers 的寫法。新版 transformers 看到「只有 1 格長」的注意力
   遮罩，會誤解成「這裡看不到前面說過的內容」，生成结果因此被腰斬成
   0.04 秒（幾乎是空的）。做法：換成不帶遮罩的版本——單句生成本來就不需要
   遮罩，前面的內容已經在 cache 裡了。

這兩個修補已經在 spikes/phase0/smoke_tts.py 驗證有效（生成長度回到正常的
3–6 秒，不是 0.04 秒），這裡只是把同一套做法包成函式給正式程式用。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

from bookclub.config import cosyvoice_model_dir, cosyvoice_repo_dir

_done = False


def setup() -> None:
    """把 CosyVoice 準備好可以 import、可以正常生成聲音。

    可以重複呼叫，只有第一次會真的動手（用一個模組層級的旗標記住），
    之後呼叫直接跳過，不會重複修補。
    """
    global _done
    if _done:
        return

    repo_dir = cosyvoice_repo_dir()
    if not repo_dir.is_dir():
        raise RuntimeError(
            f"找不到 CosyVoice 原始碼：{repo_dir}\n"
            "→ 修法：跑 install.sh 讓它自動 clone，或參考 README 手動取得。"
        )

    matcha_dir = repo_dir / "third_party" / "Matcha-TTS"
    new_entries = [str(repo_dir), str(matcha_dir)]
    sys.path[:0] = [p for p in new_entries if p not in sys.path]

    # 修補 1：假的 pyworld 模組（只有訓練資料處理會用到，推論用不到）
    sys.modules.setdefault("pyworld", types.ModuleType("pyworld"))

    # 修補 2：語言模型單步推論拿掉注意力遮罩
    from cosyvoice.llm import llm as _cv_llm

    def _forward_one_step(self, xs, masks, cache=None):
        outs = self.model(
            inputs_embeds=xs,
            output_hidden_states=True,
            return_dict=True,
            use_cache=True,
            past_key_values=cache,
        )
        return outs.hidden_states[-1], outs.past_key_values

    _cv_llm.Qwen2Encoder.forward_one_step = _forward_one_step

    _done = True


def load_cosyvoice(model_dir: str | Path | None = None):
    """載入 CosyVoice3 模型，回傳可以拿來生成聲音的物件。

    拿到物件後用 `model.inference_zero_shot(文字, 參考音逐字稿, 參考音路徑,
    stream=False)` 生成，取樣率是 `model.sample_rate`。用法範例見
    spikes/phase0/smoke_tts.py、tests/smoke/smoke_tts.py。
    """
    setup()
    from cosyvoice.cli.cosyvoice import AutoModel

    target = Path(model_dir).expanduser() if model_dir else cosyvoice_model_dir()
    if not target.is_dir():
        raise RuntimeError(
            f"找不到 CosyVoice3 模型資料夾：{target}\n"
            "→ 修法：跑「bookclub models download」下載模型。"
        )
    return AutoModel(model_dir=str(target))
