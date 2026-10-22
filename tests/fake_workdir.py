"""建一個假資料工作區：給覆核工作台（第 3 步）與匯出／匯入的測試、網頁手動測試用。

全部是合成資料（電子音、假逐字稿、假名字），不含任何真實學員內容。
    .venv/bin/python tests/fake_workdir.py <資料夾>        # 建好後 bookclub serve <資料夾>/工作區

內容（3 分鐘）：老師（冥想引導、導讀）、三位學員說話；老師提到兩次名字；三處重疊
（一處要人決定、一處兩位學員之間、一處 0 秒的邊界誤差）；參考音 ref.wav／ref.txt；發音對照表。
09-26 加：一段學員發言藏在老師段落裡（164–168 秒，段落分析判成老師）、兩位學員被分成同一人
（學員1 的第二段其實是另一個人的聲音）、建議刪除 2 段（`校對/刪除建議.json`）。
09-29 加：逐字時間（`merged.json` 的 `words`，每個字平均排在句子裡）。

1022-2 加 `--with-step5`：再補假的生成紀錄、處理紀錄、成品影片（剪掉開頭 3.6 秒的假影片），第 5 步有卡片可以點：
    .venv/bin/python tests/fake_workdir.py <資料夾> --with-step5
    BOOKCLUB_DATA_DIR=<資料夾>/假資料 .venv/bin/bookclub serve <資料夾>/工作區 --port 8801 --no-open
`<資料夾>/假資料/` 是虛構的名冊（小美→Amy、阿明→Tom），開服務時用 BOOKCLUB_DATA_DIR 指過去，不讀真的 ~/讀書會剪輯資料。
做法照 tests/test_needs.py 的 `gen_logs`、`_log`、`_prod`（舊版工具留下的處理紀錄＋成品檢查 → `needs.upgrade` 建第 1 版）。
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
    (140, 160, "學員3", "學員分享"), (160, 180, "老師", "講解"),
]
FREQ = {"老師": 220.0, "學員1": 330.0, "學員2": 440.0, "學員3": 550.0}
HIDDEN = (164, 168, "學員2")     # 藏在老師段落裡的學員發言（聲音是學員，段落分析判成老師）
CLUSTERED_AS = {"學員3": "學員1"}  # 聲紋分群錯：學員3 被分成學員1


def _voice(t: float) -> str:
    if HIDDEN[0] <= t < HIDDEN[1]:
        return HIDDEN[2]
    return _who(t)[0]


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
        if HIDDEN[0] <= start < HIDDEN[1]:
            out.append({"id": f"00_{k:03d}", "start": round(start, 3), "end": round(start + SENT_S, 3),
                        "text": "這是藏在老師段落裡的學員發言。", "avg_logprob": -0.2, "label": "不是老師", "sim": 0.1})
            continue
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
    out[23]["text"] = "我跟小美一樣，也覺得很有收穫。"   # 92 秒，學員2 說到名冊上的名字（覆核時自動換成代號）
    return out


def _words(sents: list[dict]) -> list[dict]:
    """逐字時間（09-29 加，「新增修改」的名字對齊到字用）：每句的字（不含標點）平均排在句子裡，
    頭尾各留 0.1 秒。"""
    out = []
    for s in sents:
        chars = [c for c in s["text"] if c.isalnum()]
        step = (s["end"] - s["start"] - 0.2) / max(1, len(chars))
        for k, c in enumerate(chars):
            a = s["start"] + 0.1 + k * step
            out.append({"word": c, "start": round(a, 3), "end": round(a + step * 0.9, 3)})
    return out


def _audio(sents: list[dict]) -> np.ndarray:
    x = np.zeros(int(DUR * SR), dtype=np.float32)
    for s in sents:
        who = _voice(s["start"])
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
    _write(w / "transcript" / "merged.json", {"sentences": merged_sents, "words": _words(sents), "duration": DUR,
                                              "source": str(video)})
    center = [0.0] * 8
    _write(w / "說話者判斷.json", {"sentences": sents, "cluster_info": {
        "分群數": 3, "各群秒數": {"1": 100.0}, "老師群編號": 1, "老師群佔可比對總秒數比例": 0.6, "老師聲紋中心": center}})
    _write(w / "分析結果.json", {"video": str(video), "workdir": str(w), "影片長度": DUR, "句數": len(sents),
                                "elapsed": {"1_轉文字": 1.0}})

    turns, people = [], {}
    for i, (a, b, who, kind) in enumerate(TURNS):
        ss = [s for s in sents if a <= s["start"] < b]
        who = CLUSTERED_AS.get(who, who)
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
    # 10-02 第七批：第 1 步人名清單（Claude）跑成功的樣子（沒有這個檔＝沒跑成功，第 3 步 ③ 不能標完成、第 4 步擋住）
    _write(w / "校對" / "人名清單.json", {"人名": [], "模型": "假的", "句數": len(sents),
                                        "統計": {"名字數": 0, "名冊上有": 0, "名冊上沒有": 0}})

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

    _write(w / "校對" / "刪除建議.json", {"建議": [
        {"id": "S1", "類型": "開頭空白", "start": 0.0, "end": 3.6, "原因": "假資料：開頭到第一句話之前沒人說話",
         "剪點對齊安靜處": [True, True]},
        {"id": "S2", "類型": "結尾道別", "start": 172.0, "end": 180.0, "原因": "假資料：最後幾句是道別",
         "剪點對齊安靜處": [True, True]},
    ], "模型": "假資料", "耗時": {}})

    ref = w / "參考音"
    sf.write(str(ref / "ref.wav"), audio[int(20 * SR):int(40 * SR)], SR)
    (ref / "ref.txt").write_text("老師說的第5句，老師說的第6句。", encoding="utf-8")
    _write(ref / "挑選紀錄.json", {"候選數": 1, "選定名次": 1})
    return w


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


FAKE_ROSTER = "中文名,其他寫法,英文代號,聲線,性別\n小美,,Amy,,女\n阿明,,Tom,,男\n"


def _rec(kind, a, b, keys, f=None, out=None, sound=True):
    o = out or [round(a - 3.6, 3), round(b - 3.6, 3)]   # 開頭 0–3.6 秒剪掉了
    return {"類型": kind, "原片": [a, b], "成品": o, "動到聲音": sound, "要人聽": False, "檔案": f, "文字": None,
            "覆核項目": keys, "做了什麼": kind, **({"檔案指紋": f"fp-{f}"} if f else {})}


def add_step5(root: str | Path) -> Path:
    """在 `make()` 建好的假工作區補第 5 步要的東西（全部合成、不跑模型）：
    生成紀錄（每句假的 wav 檔，內容只是幾個位元組）、處理紀錄（剪掉開頭、學員重念 3 格、老師名字整句換掉、對不到卡片的停格、
    一處沒登記的變動）、成品檢查、成品影片 `輸出/成品_0-3_sw.mp4`（假影片剪掉開頭 3.6 秒），最後 `needs.upgrade` 建第 1 版。
    要先設好 BOOKCLUB_DATA_DIR（名冊影響老師名字的計畫）。"""
    from datetime import datetime, timedelta

    from bookclub import finalcheck as fc
    from bookclub import needs, proclog
    from bookclub import workdir as wd

    root = Path(root)
    w = root / "工作區"
    # 生成紀錄：照現在排出來的計畫，生成程式看來全部做好了（tests/test_needs.py gen_logs）
    for role, xs in fc._redo_ctx(w).items():
        if not xs:
            continue
        lp, od = fc._role_paths(w, role)
        od.mkdir(parents=True, exist_ok=True)
        rel = od.relative_to(w)
        wd.write_json(lp, {"句子": [{"id": x["id"], "text": x.get("text"), "slot": x["slot"], "嘗試": [{"種子": 7}],
                                    "選定": 1, "第幾版": 1,
                                    "放回時間格": {"檔案": str(rel / f"{x['id']}_放回時間格.wav")}} for x in xs]})
        for x in xs:
            (od / f"{x['id']}.wav").write_bytes(b"wav")
            (od / f"{x['id']}_放回時間格.wav").write_bytes(b"wav")
    stamp = (datetime.now() - timedelta(minutes=2)).isoformat(timespec="seconds")
    recs = [_rec("刪除", 0.0, 3.6, ["刪除段落:S1"], out=[0.0, 0.0]),
            _rec("學員重念", 40.0, 55.0, ["學員段落:T003"], "生成/學員/T003_01.wav"),
            _rec("學員重念", 55.0, 71.6, ["學員段落:T003"], "生成/學員/T003_02.wav"),
            _rec("名字整句換掉", 76.0, 80.0, ["名字:1"], "生成/老師/S001.wav"),
            _rec("學員重念", 92.0, 111.6, ["學員段落:T005"], "生成/學員/T005_01.wav"),
            _rec("停格", 99.0, 99.0, [], out=[95.4, 95.9])]   # 對不到卡片 → 第 5 步「待釐清」
    log = {"版本": 1, "來源": "render video 0-3", "範圍": [0.0, DUR], "產生時間": stamp, "片段": None, "紀錄": recs,
           "未登記的變動": [{"原片": [130.0, 130.4], "長度秒": 0.4, "格數": 20}]}
    wd.write_json(proclog.log_path(w), log)   # 直接寫（像舊版工具留下的），下面 upgrade 才建版本
    wd.write_json(fc.check_path(w), {"版本": 1, "處理紀錄產生時間": stamp, "指紋版本": 2, "處理紀錄快照": fc.snapshot(log),
                                     "逐筆": {}, "整片退回": [], "未登記確認": {}, "看過區段": [], "成品影片": None})
    prod = w / "輸出" / "成品_0-3_sw.mp4"
    prod.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "3.6", "-i", str(root / "假影片.mp4"),
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(prod)], check=True)
    needs.upgrade(w)
    return w


if __name__ == "__main__":
    import os

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = Path(args[0] if args else "假工作區")
    if "--with-step5" in sys.argv:
        data = root / "假資料"
        data.mkdir(parents=True, exist_ok=True)
        if not (data / "名冊.csv").exists():
            (data / "名冊.csv").write_text(FAKE_ROSTER, encoding="utf-8")
        ex = Path(__file__).resolve().parent.parent / "profile.example" / "settings.toml"
        if ex.exists() and not (data / "settings.toml").exists():
            (data / "settings.toml").write_bytes(ex.read_bytes())   # 範例設定（虛構），免得每次印「找不到 settings.toml」
        os.environ["BOOKCLUB_DATA_DIR"] = str(data.resolve())
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        w = make(root)
        add_step5(root)
        print(w)
        print(f"開網頁：BOOKCLUB_DATA_DIR={data.resolve()} .venv/bin/bookclub serve {w.resolve()} --port 8801 --no-open")
    else:
        print(make(root))
