"""建一個假資料工作區：給覆核工作台（第 3 步）與匯出／匯入的測試、網頁手動測試用。

全部是合成資料（電子音、假逐字稿、假名字），不含任何真實學員內容。
    .venv/bin/python tests/fake_workdir.py <資料夾>        # 建好後 bookclub serve <資料夾>/工作區

內容（3 分鐘）：老師（冥想引導、導讀）、兩位學員輪流說話；老師提到兩次名字；三處重疊
（一處要人決定、一處兩位學員之間、一處 0 秒的邊界誤差）；參考音 ref.wav／ref.txt；發音對照表。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 16000
DUR = 180.0
SENT_S, GAP_S = 3.6, 0.4

# (起, 訖, 誰, 內容類型)
TURNS = [
    (0, 20, "老師", "冥想引導"), (20, 40, "老師", "講解"), (40, 70, "學員1", "學員分享"),
    (70, 90, "老師", "提問與回應"), (90, 110, "學員2", "學員分享"), (110, 140, "老師", "導讀"),
    (140, 160, "學員1", "學員分享"), (160, 180, "老師", "講解"),
]
FREQ = {"老師": 220.0, "學員1": 330.0, "學員2": 440.0}


def _who(t: float) -> tuple[str, str]:
    for a, b, w, k in TURNS:
        if a <= t < b:
            return w, k
    return "老師", "講解"


def _sentences() -> list[dict]:
    out = []
    for k in range(int(DUR // (SENT_S + GAP_S))):
        start = k * (SENT_S + GAP_S)
        who, kind = _who(start)
        if who == "老師":
            text = f"老師說的第{k}句，" if k % 3 else f"老師說的第{k}句。"
        else:
            text = f"這是{who}分享的第{k}句話。"
        label = "老師" if who == "老師" else "不是老師"
        if kind == "冥想引導" and k % 2:
            label = "不是老師"          # 聲紋在冥想引導時誤判（文字修正會改回老師）
        out.append({"id": f"00_{k:03d}", "start": round(start, 3), "end": round(start + SENT_S, 3), "text": text,
                    "avg_logprob": -0.2, "label": label, "sim": 0.8 if label == "老師" else 0.1})
    # 老師提到名字：第 19 句（76 秒）是半句，接下一句才完整
    out[19]["text"] = "剛剛小美分享得很好，"
    out[20]["text"] = "我們再多聽一點。"
    out[30]["text"] = "阿明上次也有提到這本書﹖"      # 120 秒，導讀中
    return out


def _audio(sents: list[dict]) -> np.ndarray:
    x = np.zeros(int(DUR * SR), dtype=np.float32)
    for s in sents:
        who, _ = _who(s["start"])
        a, b = int(s["start"] * SR), int(s["end"] * SR)
        t = np.arange(b - a) / SR
        x[a:b] = 0.15 * np.sin(2 * np.pi * FREQ[who] * t)
    x += 0.002 * np.random.default_rng(0).standard_normal(len(x)).astype(np.float32)
    return x


def make(root: str | Path) -> Path:
    root = Path(root)
    w = root / "工作區"
    for sub in ("transcript", "校對", "參考音", "名字候選"):
        (w / sub).mkdir(parents=True, exist_ok=True)
    sents = _sentences()
    audio = _audio(sents)
    sf.write(str(w / "audio.flac"), audio, SR)
    wav = root / "假聲音.wav"
    sf.write(str(wav), audio, SR)
    video = root / "假影片.mp4"
    if not video.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"testsrc=duration={int(DUR)}:size=640x360:rate=15", "-i", str(wav),
                        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(video)], check=True)

    merged_sents = [{k: s[k] for k in ("id", "start", "end", "text", "avg_logprob")} for s in sents]
    _write(w / "transcript" / "merged.json", {"sentences": merged_sents, "words": [], "duration": DUR,
                                              "source": str(video)})
    center = [0.0] * 8
    _write(w / "說話者判斷.json", {"sentences": sents, "cluster_info": {
        "分群數": 3, "各群秒數": {"1": 100.0}, "老師群編號": 1, "老師群佔可比對總秒數比例": 0.6, "老師聲紋中心": center}})
    _write(w / "分析結果.json", {"video": str(video), "workdir": str(w), "影片長度": DUR, "句數": len(sents),
                                "elapsed": {"1_轉文字": 1.0}})

    turns, people = [], {}
    for i, (a, b, who, kind) in enumerate(TURNS):
        ss = [s for s in sents if a <= s["start"] < b]
        text = "".join(s["text"] for s in ss)
        turns.append({"id": f"T{i + 1:03d}", "start": ss[0]["start"], "end": ss[-1]["end"], "句子": [s["id"] for s in ss],
                      "文字判斷": "老師" if who == "老師" else "學員", "文字學員編號": None, "換人依據": "假資料",
                      "老師點名": None, "信心": "高", "內容類型": kind, "原文": text, "聲音判斷": "老師" if who == "老師" else "學員",
                      "說話者": who, "校對稿": text, "已確認": False, "校對秒數": None})
        if who != "老師":
            p = people.setdefault(who, {"秒數": 0.0, "段數": 0, "點名線索": {}, "代號": None})
            p["秒數"] += ss[-1]["end"] - ss[0]["start"]
            p["段數"] += 1
    people["學員1"]["點名線索"] = {"Amy": 1}
    people["學員1"]["建議代號"] = "Amy"
    _write(w / "校對" / "段落.json", {"段落": turns, "學員": people, "比對": {}, "統計": {"段落數": len(turns)}})

    def cand(sent, word, code, start, end, how):
        return {"start": start, "end": end, "sentence": sent["text"], "sentence_id": sent["id"], "name": word,
                "canonical": word, "代號": code, "敏感詞": False, "matched_text": word, "比對層級": "精確", "信心": "高",
                "位置": "句首", "建議做法": how, "原因": "假資料", "切點信心": "雙邊乾淨", "建議緩衝秒數": 0.05,
                "候選音檔": ""}

    _write(w / "名字候選.json", {"candidates": [cand(sents[19], "小美", "Amy", 76.3, 76.9, "直接消音"),
                                              cand(sents[30], "阿明", "Tom", 120.0, 120.6, "整句換掉")],
                                "已自動排除": [], "排除清單": [], "統計": {"總筆數": 2}})
    ov = [
        {"start": 69.6, "end": 70.1, "length": 0.5, "speakers": [{"label": "A", "role": "不是老師", "sim": 0.1},
                                                                  {"label": "B", "role": "老師", "sim": 0.8}],
         "已自動跳過": False, "原因": None, "區域": "區域0000"},
        {"start": 100.0, "end": 100.0, "length": 0.0, "speakers": [{"label": "A", "role": "不是老師", "sim": 0.1},
                                                                    {"label": "B", "role": "不確定", "sim": 0.4}],
         "已自動跳過": False, "原因": None, "區域": "區域0001"},
        {"start": 150.2, "end": 150.6, "length": 0.4, "speakers": [{"label": "A", "role": "不是老師", "sim": 0.1},
                                                                    {"label": "C", "role": "不是老師", "sim": 0.2}],
         "已自動跳過": True, "原因": "兩位學員之間的重疊", "區域": "區域0002"},
    ]
    _write(w / "重疊.json", {"overlaps": ov, "重疊數": 3, "已自動跳過數": 1, "掃描區域數": 3, "掃描總秒數": 60.0})

    ref = w / "參考音"
    sf.write(str(ref / "ref.wav"), audio[int(20 * SR):int(40 * SR)], SR)
    (ref / "ref.txt").write_text("老師說的第5句，老師說的第6句。", encoding="utf-8")
    _write(ref / "挑選紀錄.json", {"候選數": 1, "選定名次": 1})
    return w


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    print(make(sys.argv[1] if len(sys.argv) > 1 else "假工作區"))
