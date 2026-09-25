"""段落分析（第 1 步之後、第 3 步之前，電腦自動跑；宇軒 09-25 提議）。

讀書會的對話結構：老師講一段、學員分享一段，可能來回幾次，老師再點下一位
（或下一位學員自己舉手）。逐句校對 110 句太累，改成**一段一段**確認：

1. **看文字切段落**：整支逐字稿交給 Claude（`claude -p`）讀，依對話脈絡判斷
   每一段是老師還是學員、什麼時候換人、哪幾段是同一位學員、老師有沒有點名
2. **看聲音認人**：學員的每一段整段抽聲紋（比單句長，分群比較準），分群出
   「學員 1、學員 2⋯⋯」
3. **名字線索**：學員開口前，老師最後一句提到的名冊名字，當作那位學員代號的建議

產出 `校對/段落.json`，給網頁第 3 步：段落時間表 → 學員換成哪個英文名 → 逐段確認逐字稿。

可行性測試（`compare_with_voice`）：文字判斷「老師／學員」時**不給**聲音判斷
的結果，再跟聲紋的判斷比，看只看文字能對幾成。
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

import numpy as np

from bookclub import workdir as wd

CHUNK_LINES = 220        # 每次交給 Claude 的句數（約 8–10 分鐘）
OVERLAP_LINES = 20       # 前後塊重疊的句數，接縫處用前一塊的判斷
MAX_EMB_S = 30.0         # 每段最多取 30 秒抽聲紋
MIN_EMB_S = 1.5          # 短於這個秒數的段落不抽聲紋（歸到最像的學員，標「猜的」）
VOICE_DISTANCE_T = 0.5   # 段落層級聲紋分群門檻（比逐句的 0.6 嚴一點：整段的聲紋比較穩）


def turns_path(workdir: Path) -> Path:
    return workdir / "校對" / "段落.json"


# ---------- 文字判斷（Claude） ----------

PROMPT = """你在協助剪輯一支讀書會的錄影。老師帶領，學員輪流分享或提問；通常是老師講一段、學員講一段，可能來回幾次，老師再點下一位學員，或下一位學員自己接話。

下面是逐字稿，每行格式：「行號|時間|文字」。語音轉文字有錯字，也沒有標誰在說話。請只根據文字內容與對話脈絡，把逐字稿切成「同一個人連續說話」的段落。

判斷線索：老師會帶讀、講解、提問、點名（「Bella，妳要不要分享」「下一位」「謝謝某某」）、回應學員；學員會分享自己的經驗、回答老師、提問、說「謝謝老師」。一段學員分享結束後，老師通常會回應。

規則：
- 每一行都要屬於一個段落，段落照行號順序、不重疊、不遺漏
- 說話者只能是「老師」或「學員」；學員段落再給一個「學員編號」：同一位學員在前後段落（例如回答老師的追問）用同一個編號；看得出是新的一位學員就用新編號；不確定就用新編號
- 很短的附和（「嗯」「對」「好」）如果夾在老師的話中間，就併進老師的段落
- 老師點到名字時，把名字寫在「老師點名」

只輸出 JSON，不要其他文字，格式：
{"段落":[{"起":起始行號,"迄":結束行號,"說話者":"老師或學員","學員編號":"S1 或 null","換人依據":"一句話說明為什麼在這裡換人","老師點名":"名字或 null","信心":"高/中/低"}]}
"""


def format_lines(sentences: list[dict], offset: int = 0) -> str:
    return "\n".join(f"{offset + i}|{wd.fmt_time(s['start'])[3:] if s['start'] < 3600 else wd.fmt_time(s['start'])}|{s['text']}"
                     for i, s in enumerate(sentences))


def call_claude(prompt: str, model: str, timeout_s: int = 600) -> str:
    r = subprocess.run(["claude", "-p", "--model", model], input=prompt, capture_output=True, text=True,
                       timeout=timeout_s)
    if r.returncode != 0:
        raise RuntimeError(f"claude -p 失敗：{r.stderr.strip()[:300]}")
    return r.stdout


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("Claude 沒有回傳 JSON")
    return json.loads(m.group(0))


def normalize_turns(raw: list[dict], lo: int, hi: int) -> list[dict]:
    """把 Claude 回的段落整理成 [lo, hi] 行號之間不重疊、不遺漏的段落（純函式）。"""
    turns = sorted((t for t in raw if isinstance(t.get("起"), int) and isinstance(t.get("迄"), int)),
                   key=lambda t: t["起"])
    out, pos = [], lo
    for t in turns:
        a, b = max(t["起"], pos), min(t["迄"], hi)
        if b < a:
            continue
        if a > pos and out:          # 有漏掉的行：併進前一段
            out[-1]["迄"] = a - 1
        elif a > pos:
            a = pos
        out.append({**t, "起": a, "迄": b})
        pos = b + 1
        if pos > hi:
            break
    if out and pos <= hi:
        out[-1]["迄"] = hi
    return out


def text_turns(sentences: list[dict], model: str, log=print) -> list[dict]:
    """整支逐字稿分塊交給 Claude，接起來。學員編號加上塊的前綴（C0-S1），跨塊的同一人交給聲紋判斷。"""
    n = len(sentences)
    all_turns: list[dict] = []
    start = 0
    k = 0
    while start < n:
        end = min(n, start + CHUNK_LINES)
        t0 = time.time()
        raw = parse_json(call_claude(PROMPT + "\n逐字稿：\n" + format_lines(sentences[start:end], start), model))
        chunk = normalize_turns(raw.get("段落", []), start, end - 1)
        for t in chunk:
            if t.get("學員編號"):
                t["學員編號"] = f"C{k}-{t['學員編號']}"
        # 重疊區：前一塊已經判斷過的行，用前一塊的；這一塊從接縫之後開始
        if all_turns:
            seam = all_turns[-1]["迄"]
            chunk = [t for t in chunk if t["迄"] > seam]
            if chunk:
                chunk[0]["起"] = max(chunk[0]["起"], seam + 1)
        all_turns.extend(chunk)
        log(f"[段落] 第 {k + 1} 塊（{start}–{end - 1} 行）：{len(chunk)} 段，{time.time() - t0:.0f} 秒")
        if end >= n:
            break
        start = end - OVERLAP_LINES
        k += 1
    return all_turns


# ---------- 聲音認人 ----------

def cluster_turns(embs: np.ndarray, durs: list[float]) -> list[int]:
    """段落層級聲紋分群，回傳每段的群編號（依總秒數排，0 起算）（純函式）。"""
    from scipy.cluster.hierarchy import fcluster, linkage

    if len(embs) == 1:
        return [0]
    ids = fcluster(linkage(embs, method="average", metric="cosine"), t=VOICE_DISTANCE_T, criterion="distance")
    secs: dict[int, float] = {}
    for c, d in zip(ids, durs):
        secs[int(c)] = secs.get(int(c), 0.0) + d
    order = {c: i for i, c in enumerate(sorted(secs, key=lambda c: -secs[c]))}
    return [order[int(c)] for c in ids]


# ---------- 串起來 ----------

def build_turns(workdir: str | Path, *, model: str | None = None, log=print) -> dict:
    """`bookclub run turns`：文字切段落＋聲音認人＋名字線索，寫出 `校對/段落.json`。"""
    from bookclub import names
    from bookclub.config import data_dir, load_settings
    from bookclub.refpick import _l2norm, _load_embed_model, cosine

    workdir = Path(workdir).expanduser()
    speakers = wd.read_json(wd.speakers_path(workdir))
    if not speakers:
        raise FileNotFoundError(f"找不到 {wd.speakers_path(workdir)}，先跑 `bookclub run analyze`。")
    sents = speakers["sentences"]
    model = model or load_settings().claude_models.turns
    log(f"[段落] {len(sents)} 句，交給 Claude（{model}）只看文字切段落...")
    t0 = time.time()
    raw_turns = text_turns(sents, model, log)
    text_s = time.time() - t0

    roster = names.load_roster(data_dir() / "名冊.csv")
    turns = []
    for i, t in enumerate(raw_turns):
        ss = sents[t["起"]:t["迄"] + 1]
        turns.append({
            "id": f"T{i + 1:03d}", "start": ss[0]["start"], "end": ss[-1]["end"],
            "句子": [s["id"] for s in ss], "文字判斷": t["說話者"], "文字學員編號": t.get("學員編號"),
            "換人依據": t.get("換人依據"), "老師點名": t.get("老師點名"), "信心": t.get("信心"),
            "原文": "".join(s["text"] for s in ss),
            "聲音判斷": _voice_role(ss),
        })

    # 聲音認人：文字判斷為學員的段落，整段抽聲紋分群
    t1 = time.time()
    from pyannote.core import Segment

    inference = _load_embed_model()
    audio = str(wd.audio_path(workdir))
    center = np.array(speakers.get("cluster_info", {}).get("老師聲紋中心") or [])
    stu = [t for t in turns if t["文字判斷"] == "學員"]
    embs, durs, idx = [], [], []
    for k, t in enumerate(stu):
        segs = [s for s in sents if s["id"] in set(t["句子"]) and s["end"] - s["start"] >= 0.8]
        pieces, total = [], 0.0
        for s in segs:
            if total >= MAX_EMB_S:
                break
            pieces.append(_l2norm(inference.crop(audio, Segment(s["start"], s["end"])).reshape(-1)) * (s["end"] - s["start"]))
            total += s["end"] - s["start"]
        if total >= MIN_EMB_S:
            e = _l2norm(np.sum(pieces, axis=0))
            embs.append(e); durs.append(total); idx.append(k)
            if center.size:
                t["跟老師聲紋相似度"] = round(cosine(e, center), 3)
    groups = cluster_turns(np.stack(embs), durs) if embs else []
    for k, g in zip(idx, groups):
        stu[k]["學員"] = f"學員{g + 1}"
    for k, t in enumerate(stu):
        if "學員" not in t:
            t["學員"] = "學員?"
            t["學員是猜的"] = True
    voice_s = time.time() - t1

    # 名字線索：學員段落前一段是老師、老師點名了誰
    by_person: dict[str, dict] = {}
    for i, t in enumerate(turns):
        if t["文字判斷"] != "學員":
            t["說話者"] = "老師"
            continue
        t["說話者"] = t["學員"]
        p = by_person.setdefault(t["學員"], {"秒數": 0.0, "段數": 0, "點名線索": {}, "代號": None})
        p["秒數"] += t["end"] - t["start"]
        p["段數"] += 1
        prev = turns[i - 1] if i else None
        called = (prev or {}).get("老師點名") or t.get("老師點名")
        if called:
            code = next((r["代號"] for r in roster if r["寫法"] in called or called in r["寫法"]), None)
            key = code or called
            p["點名線索"][key] = p["點名線索"].get(key, 0) + 1
    for p in by_person.values():
        p["秒數"] = round(p["秒數"], 1)
        if p["點名線索"]:
            best = max(p["點名線索"].items(), key=lambda kv: kv[1])[0]
            p["建議代號"] = best if any(r["代號"] == best for r in roster) else None

    for t in turns:
        t.update({"校對稿": t["原文"], "已確認": False, "校對秒數": None})

    data = {
        "段落": turns,
        "學員": dict(sorted(by_person.items(), key=lambda kv: -kv[1]["秒數"])),
        "比對": {**compare_with_voice(turns), **same_person_agreement(turns)},
        "統計": {"段落數": len(turns), "學員段落數": len(stu), "學員人數": len(by_person),
                "文字判斷秒": round(text_s), "聲紋分群秒": round(voice_s), "模型": model},
    }
    wd.write_json(turns_path(workdir), data)
    c = data["比對"]
    log(f"[段落] 完成：{len(turns)} 段（學員 {len(stu)} 段、{len(by_person)} 位）；"
        f"只看文字判斷老師／學員，跟聲紋一致 {c['一致比例']:.0%}（以秒數計）→ {turns_path(workdir)}")
    return data


def _voice_role(ss: list[dict]) -> str:
    """這段的聲紋判斷：依秒數多數決（老師／學員／不確定）。"""
    secs = {"老師": 0.0, "學員": 0.0, "不確定": 0.0}
    for s in ss:
        k = "老師" if s["label"] == "老師" else "學員" if s["label"] == "不是老師" else "不確定"
        secs[k] += s["end"] - s["start"]
    return max(secs, key=secs.get)


def same_person_agreement(turns: list[dict]) -> dict:
    """同一塊裡的學員段落兩兩比：文字說「同一人」跟聲紋分群說「同一人」一致的比例（純函式）。"""
    stu = [t for t in turns if t["文字判斷"] == "學員" and t.get("文字學員編號") and not t.get("學員是猜的")]
    agree = total = 0
    for i in range(len(stu)):
        for j in range(i + 1, len(stu)):
            a, b = stu[i], stu[j]
            if a["文字學員編號"].split("-")[0] != b["文字學員編號"].split("-")[0]:
                continue
            total += 1
            agree += (a["文字學員編號"] == b["文字學員編號"]) == (a["學員"] == b["學員"])
    return {"同一人判斷一致比例": round(agree / total, 3) if total else None, "同一人比對組數": total}


def compare_with_voice(turns: list[dict]) -> dict:
    """文字判斷（老師／學員）跟聲紋判斷比，以秒數計算一致比例（純函式）。"""
    agree = total = 0.0
    kinds = {"文字老師聲紋學員": 0.0, "文字學員聲紋老師": 0.0, "聲紋不確定": 0.0}
    for t in turns:
        d = t["end"] - t["start"]
        v = t["聲音判斷"]
        if v == "不確定":
            kinds["聲紋不確定"] += d
            continue
        total += d
        if v == t["文字判斷"]:
            agree += d
        elif t["文字判斷"] == "老師":
            kinds["文字老師聲紋學員"] += d
        else:
            kinds["文字學員聲紋老師"] += d
    return {"一致比例": round(agree / total, 3) if total else 0.0, "比對秒數": round(total),
            **{k: round(v) for k, v in kinds.items()}}


# ---------- 網頁第 3 步：讀取與存檔 ----------

import threading

_lock = threading.Lock()
MAX_COUNT_S = 300.0   # 單次累加的校對時間上限（一段比一句長，放寬到 5 分鐘）


def _audio_url(start: float, end: float) -> str:
    from urllib.parse import urlencode

    return "/api/audio?" + urlencode({"start": f"{max(0, start - 0.15):.2f}", "end": f"{end + 0.15:.2f}"})


def turns_progress(data: dict) -> dict:
    """學員段落確認進度與推算（純函式）。"""
    stu = [t for t in data["段落"] if t["說話者"] != "老師"]
    done = [t for t in stu if t["已確認"]]
    spent = sum(t["校對秒數"] or 0.0 for t in done)
    audio = sum(t["end"] - t["start"] for t in done)
    total = sum(t["end"] - t["start"] for t in stu)
    ratio = spent / audio if audio else None
    return {
        "已確認": len(done), "學員段落數": len(stu), "已花秒數": round(spent),
        "每分鐘聲音要花分鐘": round(ratio, 1) if ratio else None,
        "學員聲音分鐘": round(total / 60, 1),
        "推算全部要花小時": round(total * ratio / 3600, 1) if ratio else None,
    }


def page_data(workdir: str | Path) -> dict:
    from bookclub import names
    from bookclub.config import data_dir

    workdir = Path(workdir)
    data = wd.read_json(turns_path(workdir))
    if not data:
        return {"尚未準備": True}
    for t in data["段落"]:
        t["音檔網址"] = _audio_url(t["start"], t["end"])
    for name, p in data["學員"].items():
        sample = next((t for t in data["段落"] if t["說話者"] == name and t["end"] - t["start"] >= 3), None) \
            or next((t for t in data["段落"] if t["說話者"] == name), None)
        p["試聽網址"] = _audio_url(sample["start"], min(sample["end"], sample["start"] + 12)) if sample else None
    data["代號選項"] = sorted({r["代號"] for r in names.load_roster(data_dir() / "名冊.csv") if r["代號"]})
    data["進度"] = turns_progress(data)
    return data


def _recount_people(data: dict) -> None:
    people = data["學員"]
    for p in people.values():
        p["秒數"], p["段數"] = 0.0, 0
    for t in data["段落"]:
        if t["說話者"] != "老師":
            p = people.setdefault(t["說話者"], {"秒數": 0.0, "段數": 0, "點名線索": {}, "代號": None})
            p["秒數"] = round(p["秒數"] + t["end"] - t["start"], 1)
            p["段數"] += 1
    for k in [k for k, p in people.items() if p["段數"] == 0 and not p.get("代號")]:
        del people[k]


def save_turn(workdir: str | Path, tid: str, fields: dict) -> dict:
    """`POST /api/turns/save`：改說話者、校對稿、確認；校對秒數累加。"""
    workdir = Path(workdir)
    with _lock:
        data = wd.read_json(turns_path(workdir))
        t = next((x for x in data["段落"] if x["id"] == tid), None)
        if t is None:
            raise KeyError(f"找不到這一段：{tid}")
        if "說話者" in fields:
            who = str(fields["說話者"])
            if who == "新學員":
                n = 1 + max([int(k[2:]) for k in data["學員"] if k[2:].isdigit()] or [0])
                who = f"學員{n}"
            t["說話者"] = who
            t["說話者是人改的"] = True
            _recount_people(data)
        if "校對稿" in fields:
            t["校對稿"] = str(fields["校對稿"]).strip()
        if "已確認" in fields:
            t["已確認"] = bool(fields["已確認"])
        if fields.get("加秒數"):
            t["校對秒數"] = round((t["校對秒數"] or 0.0) + min(float(fields["加秒數"]), MAX_COUNT_S), 1)
        wd.write_json(turns_path(workdir), data)
        return {"ok": True, "段落": t, "進度": turns_progress(data)}


def merge_turn(workdir: str | Path, tid: str) -> dict:
    """`POST /api/turns/merge`：這一段併進上一段（說話者用上一段的）。"""
    workdir = Path(workdir)
    with _lock:
        data = wd.read_json(turns_path(workdir))
        ts = data["段落"]
        i = next(k for k, x in enumerate(ts) if x["id"] == tid)
        if i == 0:
            raise ValueError("第一段沒有上一段可以合併")
        a, b = ts[i - 1], ts[i]
        a["end"] = b["end"]
        a["句子"] += b["句子"]
        a["原文"] += b["原文"]
        a["校對稿"] += b["校對稿"]
        a["已確認"] = False
        del ts[i]
        _recount_people(data)
        wd.write_json(turns_path(workdir), data)
        return {"ok": True}


def split_turn(workdir: str | Path, tid: str, at_char: int) -> dict:
    """`POST /api/turns/split`：在校對稿第 at_char 個字切成兩段（時間照句子邊界分；後段先沿用原本的說話者，換人在 ① 改）。"""
    workdir = Path(workdir)
    with _lock:
        data = wd.read_json(turns_path(workdir))
        speakers = wd.read_json(wd.speakers_path(workdir))
        sent = {s["id"]: s for s in speakers["sentences"]}
        ts = data["段落"]
        i = next(k for k, x in enumerate(ts) if x["id"] == tid)
        t = ts[i]
        # 找 at_char 落在哪一句：照原文逐句累計字數
        acc, cut = 0, None
        for k, sid in enumerate(t["句子"]):
            acc += len(sent[sid]["text"])
            if acc >= at_char:
                cut = k + 1
                break
        if not cut or cut >= len(t["句子"]):
            raise ValueError("切點要落在兩句之間（這一段只有一句，或切在最後一句）")
        first, second = t["句子"][:cut], t["句子"][cut:]
        new = {**t, "id": t["id"] + "b", "句子": second, "start": sent[second[0]]["start"],
               "原文": "".join(sent[s]["text"] for s in second), "已確認": False, "校對秒數": None,
               "說話者是人改的": True, "換人依據": "人工切開"}
        new["校對稿"] = t["校對稿"][at_char:].strip() or new["原文"]
        t.update({"句子": first, "end": sent[first[-1]]["end"], "原文": "".join(sent[s]["text"] for s in first),
                  "校對稿": t["校對稿"][:at_char].strip(), "已確認": False})
        ts.insert(i + 1, new)
        _recount_people(data)
        wd.write_json(turns_path(workdir), data)
        return {"ok": True}


def set_person_code(workdir: str | Path, person: str, code: str | None) -> dict:
    """`POST /api/turns/person`：學員 N 換成哪個英文名（名冊代號）。"""
    workdir = Path(workdir)
    with _lock:
        data = wd.read_json(turns_path(workdir))
        data["學員"].setdefault(person, {"秒數": 0.0, "段數": 0, "點名線索": {}})["代號"] = code or None
        wd.write_json(turns_path(workdir), data)
        return {"ok": True}
