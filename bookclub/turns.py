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
PARALLEL_CALLS = 8       # 同時送給 Claude 的塊數（每塊各自獨立判斷；09-26 從 4 改 8：第一堂 8 塊一次送完，不用等第二輪）
MAX_EMB_S = 30.0         # 每段最多取 30 秒抽聲紋
MIN_EMB_S = 1.5          # 短於這個秒數的段落不抽聲紋（歸到最像的學員，標「猜的」）
VOICE_DISTANCE_T = 0.5   # 段落層級聲紋分群門檻（比逐句的 0.6 嚴一點：整段的聲紋比較穩）


def turns_path(workdir: Path) -> Path:
    return workdir / "校對" / "段落.json"


def text_turns_path(workdir: Path) -> Path:
    """段落分析的文字那一半（Claude 的判斷）快取：存在就不再呼叫 Claude。"""
    return workdir / "校對" / "段落_文字.json"


# ---------- 文字判斷（Claude） ----------

PROMPT = """你在協助剪輯一支讀書會的錄影。老師帶領，學員輪流分享或提問；通常是老師講一段、學員講一段，可能來回幾次，老師再點下一位學員，或下一位學員自己接話。

下面是逐字稿，每行格式：「行號|時間|文字」。語音轉文字有錯字，也沒有標誰在說話。請只根據文字內容與對話脈絡，把逐字稿切成「同一個人連續說話」的段落。

判斷線索：老師會帶讀、講解、提問、點名（「Bella，妳要不要分享」「下一位」「謝謝某某」）、回應學員；學員會分享自己的經驗、回答老師、提問、說「謝謝老師」。一段學員分享結束後，老師通常會回應。

特別注意：**老師帶冥想引導**（例如「吸氣⋯⋯吐氣」「把注意力帶回呼吸」「感覺身體」、有很多停頓的引導語）或**帶讀、導讀書本內容**時，整段都是老師，不是學員——中間不要切出學員段落，除非文字明確顯示有學員開口（例如老師問「有人想分享嗎」之後有人回應）。

規則：
- 每一行都要屬於一個段落，段落照行號順序、不重疊、不遺漏
- 說話者只能是「老師」或「學員」；學員段落再給一個「學員編號」：同一位學員在前後段落（例如回答老師的追問）用同一個編號；看得出是新的一位學員就用新編號；不確定就用新編號
- 很短的附和（「嗯」「對」「好」）如果夾在老師的話中間，就併進老師的段落
- 老師點到名字時，把名字寫在「老師點名」
- 每一段標「內容類型」：冥想引導、導讀、講解、提問與回應、學員分享、其他

只輸出 JSON，不要其他文字，格式：
{"段落":[{"起":起始行號,"迄":結束行號,"說話者":"老師或學員","學員編號":"S1 或 null","內容類型":"冥想引導／導讀／講解／提問與回應／學員分享／其他","換人依據":"一句話說明為什麼在這裡換人","老師點名":"名字或 null","信心":"高/中/低"}]}
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


def chunk_ranges(n: int, size: int = CHUNK_LINES, overlap: int = OVERLAP_LINES) -> list[tuple[int, int]]:
    """把 n 行切成 [(start, end), ...]（end 不含），相鄰兩塊重疊 overlap 行（純函式）。"""
    out, start = [], 0
    while start < n:
        end = min(n, start + size)
        out.append((start, end))
        if end >= n:
            break
        start = end - overlap
    return out


def stitch_chunks(chunks: list[list[dict]]) -> list[dict]:
    """依順序把每一塊的段落接起來：重疊區用前一塊的判斷，這一塊從接縫之後開始（純函式）。"""
    all_turns: list[dict] = []
    for chunk in chunks:
        if all_turns:
            seam = all_turns[-1]["迄"]
            chunk = [dict(t) for t in chunk if t["迄"] > seam]
            if chunk:
                chunk[0]["起"] = max(chunk[0]["起"], seam + 1)
        all_turns.extend(chunk)
    return all_turns


def text_turns(sentences: list[dict], model: str, log=print, workers: int = PARALLEL_CALLS,
               call=None) -> list[dict]:
    """整支逐字稿分塊交給 Claude，**同時送出**（預設 4 塊一起），全部回來再依順序接起來。

    每一塊各自獨立判斷，所以可以平行；訂閱方案限制同時呼叫數時會自動排隊，最慢就是一塊一塊跑。
    學員編號加上塊的前綴（C0-S1），跨塊的同一人交給聲紋判斷。call 可以換成假的（測試用）。
    """
    from concurrent.futures import ThreadPoolExecutor

    call = call or call_claude
    ranges = chunk_ranges(len(sentences))

    def one(k: int) -> list[dict]:
        start, end = ranges[k]
        t0 = time.time()
        for attempt in (1, 2):   # Claude 偶爾回傳格式壞掉的 JSON、或同時送太多被拒，重送一次
            try:
                raw = parse_json(call(PROMPT + "\n逐字稿：\n" + format_lines(sentences[start:end], start), model))
                break
            except (ValueError, RuntimeError, subprocess.TimeoutExpired):
                if attempt == 2:
                    raise
                time.sleep(5)
        chunk = normalize_turns(raw.get("段落", []), start, end - 1)
        for t in chunk:
            if t.get("學員編號"):
                t["學員編號"] = f"C{k}-{t['學員編號']}"
        log(f"[段落] 第 {k + 1}／{len(ranges)} 塊（{start}–{end - 1} 行）：{len(chunk)} 段，{time.time() - t0:.0f} 秒")
        return chunk

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        chunks = list(pool.map(one, range(len(ranges))))
    return stitch_chunks(chunks)


def get_text_turns(workdir: str | Path, sentences: list[dict], *, model: str | None = None, log=print,
                   call=None) -> dict:
    """段落分析的文字那一半：`校對/段落_文字.json` 存在（而且句數對得上）就直接用，不然呼叫 Claude 再存檔。

    回傳 {"段落": [{起, 迄, 說話者, 學員編號, 內容類型, …, 句子: [句子 id]}], "句數", "模型", "文字判斷秒"}。
    sentences 只用到 id／start／text，轉文字的句子或認老師之後的句子都可以（兩者順序、id 一樣）。"""
    workdir = Path(workdir).expanduser()
    path = text_turns_path(workdir)
    cached = wd.read_json(path)
    if cached and cached.get("句數") == len(sentences):
        log(f"[段落] 已有 {path.name}（{len(cached['段落'])} 段），不再呼叫 Claude")
        return cached
    if model is None:
        from bookclub.config import load_settings

        model = load_settings().claude_models.turns
    log(f"[段落] {len(sentences)} 句，交給 Claude（{model}）只看文字切段落...")
    t0 = time.time()
    raw = text_turns(sentences, model, log, call=call)
    for t in raw:
        t["句子"] = [s["id"] for s in sentences[t["起"]:t["迄"] + 1]]
    data = {"段落": raw, "句數": len(sentences), "模型": model, "文字判斷秒": round(time.time() - t0)}
    wd.write_json(path, data)
    return data


# ---------- 文字修正聲紋判斷（09-25 宇軒：冥想引導、導讀整段算老師） ----------

def calm_turns(text: dict) -> list[dict]:
    """冥想引導、導讀的段落（聲紋在這兩種段落不可靠）。"""
    return [t for t in text.get("段落", []) if t.get("內容類型") in VOICE_UNRELIABLE]


def correct_labels(sentences: list[dict], text: dict) -> dict:
    """落在冥想引導、導讀段落裡、聲紋判成非老師的句子改成老師（就地修改，純函式）。

    原本的聲紋判斷存在 `聲紋判斷`，第一次修正時記下、之後不動，所以重複執行結果一樣；
    文字結果改了（例如重跑段落分析），沒被新結果涵蓋的句子會還原成聲紋判斷。
    每句加 `判斷依據`：「聲紋」或「文字：冥想引導／導讀」。回傳統計與冥想導讀的時間區域。"""
    kind_of: dict[str, str] = {}
    for t in calm_turns(text):
        for sid in t.get("句子", []):
            kind_of[sid] = t["內容類型"]
    n = secs = 0.0
    for s in sentences:
        voice = s.setdefault("聲紋判斷", s.get("label"))
        kind = kind_of.get(s.get("id"))
        if kind and voice != "老師":
            s["label"] = "老師"
            s["判斷依據"] = f"文字：{kind}"
            n += 1
            secs += s["end"] - s["start"]
        else:
            s["label"] = voice
            s["判斷依據"] = "聲紋"
    by_id = {s["id"]: s for s in sentences}
    regions = []
    for t in calm_turns(text):
        ss = [by_id[i] for i in t.get("句子", []) if i in by_id]
        if ss:
            regions.append([round(ss[0]["start"], 3), round(ss[-1]["end"], 3)])
    return {"改成老師句數": int(n), "改成老師秒數": round(secs, 1), "冥想導讀段數": len(regions),
            "冥想導讀秒數": round(sum(e - s for s, e in regions), 1), "冥想導讀區域": regions}


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

def build_turns(workdir: str | Path, *, model: str | None = None, roster_path: str | Path | None = None,
                log=print, text: dict | None = None) -> dict:
    """`bookclub run turns`：文字切段落（有 `段落_文字.json` 就沿用）＋聲音認人＋名字線索，寫出 `校對/段落.json`。

    `bookclub run analyze` 會先把文字那一半跑完（跟認老師同時跑），這裡只補聲音認人（約 13 秒）。"""
    from bookclub import names
    from bookclub.config import data_dir
    from bookclub.refpick import _l2norm, _load_embed_model, cosine

    workdir = Path(workdir).expanduser()
    speakers = wd.read_json(wd.speakers_path(workdir))
    if not speakers:
        raise FileNotFoundError(f"找不到 {wd.speakers_path(workdir)}，先跑 `bookclub run analyze`。")
    sents = speakers["sentences"]
    text = text or get_text_turns(workdir, sents, model=model, log=log)
    raw_turns = text["段落"]
    model = text.get("模型") or model
    text_s = text.get("文字判斷秒") or 0

    roster = names.load_roster(Path(roster_path).expanduser() if roster_path else data_dir() / "名冊.csv")
    turns = []
    for i, t in enumerate(raw_turns):
        ss = sents[t["起"]:t["迄"] + 1]
        turns.append({
            "id": f"T{i + 1:03d}", "start": ss[0]["start"], "end": ss[-1]["end"],
            "句子": [s["id"] for s in ss], "文字判斷": t["說話者"], "文字學員編號": t.get("學員編號"),
            "換人依據": t.get("換人依據"), "老師點名": t.get("老師點名"), "信心": t.get("信心"),
            "內容類型": t.get("內容類型"),
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
    """這段的聲紋判斷：依秒數多數決（老師／學員／不確定）。用原本的聲紋判斷，不用文字修正過的。"""
    secs = {"老師": 0.0, "學員": 0.0, "不確定": 0.0}
    for s in ss:
        lab = s.get("聲紋判斷", s["label"])
        k = "老師" if lab == "老師" else "學員" if lab == "不是老師" else "不確定"
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


VOICE_UNRELIABLE = ("冥想引導", "導讀")   # 09-25 宇軒試聽：老師帶冥想、導讀時語氣沉穩、音質不同，聲紋會誤判成學員


def compare_with_voice(turns: list[dict]) -> dict:
    """文字判斷（老師／學員）跟聲紋判斷比，以秒數計算一致比例（純函式）。

    冥想引導、導讀的段落不列入比對：聲紋在這兩種段落不可靠（09-25 第一堂 17 段聲紋判成學員、
    文字判成老師的地方，宇軒聽過全部是老師）。"""
    agree = total = 0.0
    kinds = {"文字老師聲紋學員": 0.0, "文字學員聲紋老師": 0.0, "聲紋不確定": 0.0, "冥想導讀不比對": 0.0}
    for t in turns:
        d = t["end"] - t["start"]
        if t.get("內容類型") in VOICE_UNRELIABLE:
            kinds["冥想導讀不比對"] += d
            continue
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
    roster = names.load_roster(data_dir() / "名冊.csv")
    from bookclub import epcodes as _ep   # 09-29：名冊拿掉代號欄，選單用常用英文名＋這一集用過的

    data["代號選項"] = _ep.code_options(workdir)
    # 09-29「學員是誰」改兩欄：左邊選本名（最後一個選項是老師），右邊每個本名在這支影片用哪個英文代號
    data["本名選項"] = sorted({r["canonical"] for r in roster if r.get("canonical")})
    data["名冊代號"] = {r["canonical"]: r["代號"] for r in roster if r.get("canonical") and r["代號"]}
    from bookclub.config import load_settings

    data["老師名稱"] = load_settings().teacher.name
    data.setdefault("本名代號", {})
    from bookclub import personnames   # 09-29：這一集被叫到的名字（第 1 步人名清單），本名選單排最前面

    data["這一集的名字"] = personnames.called_names(workdir)
    from bookclub import epcodes   # 09-29：學員 N 的代號一律照這一集的代號表（右欄＞③＞名冊）

    codes = epcodes.episode_codes(workdir)
    for p in data["學員"].values():
        if p.get("本名") and codes.get(p["本名"]):
            p["代號"] = codes[p["本名"]]
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
        if "問老師" in fields:          # 聽不清楚，問老師（覆核工作台）
            t["問老師"] = bool(fields["問老師"])
        if "問老師備註" in fields:
            t["問老師備註"] = str(fields["問老師備註"])
        if fields.get("加秒數"):
            t["校對秒數"] = round((t["校對秒數"] or 0.0) + min(float(fields["加秒數"]), MAX_COUNT_S), 1)
        wd.write_json(turns_path(workdir), data)
    added = 0
    if fields.get("說話者") == "老師":
        # 那段其實是老師在講話：補找這段裡老師提到的名字（第 1 步只掃判成老師的句子，會漏掉）
        from bookclub import names

        try:
            added = names.append_candidates(workdir, t.get("句子", []))
        except Exception as exc:   # 補找失敗不要擋住改說話者
            print(f"⚠️ 改成老師後補找名字失敗：{exc}")
    return {"ok": True, "段落": t, "進度": turns_progress(data), "補找到的老師名字": added}


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
    """`POST /api/turns/split`：在校對稿第 at_char 個字切成兩段（時間照句子邊界分；後段先沿用原本的說話者，換人在 ② 改）。"""
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


UNKNOWN_REAL = "__unknown"   # 「學員是誰」左欄：不知道是誰（照樣換聲音）


def set_real_name(workdir: str | Path, person: str, real: str | None) -> dict:
    """`POST /api/turns/realname`：學員 N 的本名（09-29 宇軒：「學員是誰」左欄）。
    選老師（`settings.toml` 的 `[teacher] name`，預設「老師」）＝這一位的段落全部改成老師，並補找老師提到的名字。
    其他本名：記下本名，英文代號用這支影片右欄設過的；沒設過就用名冊上的代號。"""
    from bookclub import names
    from bookclub.config import data_dir, load_settings

    workdir = Path(workdir)
    teacher = load_settings().teacher.name
    if real == UNKNOWN_REAL:   # 09-29 宇軒：聽得出不是其他人、但不知道本名：照樣換聲音，不用本名
        with _lock:
            data = wd.read_json(turns_path(workdir))
            p = data["學員"].setdefault(person, {"秒數": 0.0, "段數": 0, "點名線索": {}})
            p["本名"], p["本名未知"], p["代號"] = None, True, None   # 自動配的代號是名冊上別人的，清掉
            wd.write_json(turns_path(workdir), data)
        return {"ok": True, "學員": person, "本名": None, "本名未知": True, "代號": None}
    if real and real in (teacher, "老師"):
        ids = [t["id"] for t in wd.read_json(turns_path(workdir))["段落"] if t["說話者"] == person]
        if not ids:
            raise KeyError(f"{person} 沒有段落")
        return {**reassign_turns(workdir, ids, "老師"), "改成老師": True}
    roster = {r["canonical"]: r["代號"] for r in names.load_roster(data_dir() / "名冊.csv") if r.get("canonical")}
    # 人名清單裡的寫法對到名冊本名（例如選了逐字稿裡的暱稱）
    from bookclub import personnames

    for p0 in (wd.read_json(personnames.people_path(workdir), default=None) or {}).get("人名", []):
        if real and p0.get("名冊本名") and real in (p0["名字"], *p0["其他寫法"]):
            real = p0["名冊本名"]
    with _lock:
        data = wd.read_json(turns_path(workdir))
        p = data["學員"].setdefault(person, {"秒數": 0.0, "段數": 0, "點名線索": {}})
        p["本名"] = real or None
        p.pop("本名未知", None)
        if real:
            from bookclub import epcodes

            p["代號"] = epcodes.episode_codes(workdir).get(real) or roster.get(real) or p.get("代號")
        wd.write_json(turns_path(workdir), data)
        return {"ok": True, "學員": person, "本名": p["本名"], "代號": p.get("代號")}


def set_name_code(workdir: str | Path, real: str, code: str | None) -> dict:
    """`POST /api/turns/namecode`：這個本名在這支影片用哪個英文代號（右欄）；同一個本名的學員 N 一起改。
    本名不在名冊上（第 1 步人名清單抓到的）：加進名冊，並補找老師提到這個名字的地方。"""
    from bookclub import names, personnames
    from bookclub.config import data_dir

    workdir = Path(workdir)
    added = 0
    if code and not any(r["canonical"] == real for r in names.load_roster(data_dir() / "名冊.csv")):
        alts = next((p["其他寫法"] for p in (wd.read_json(personnames.people_path(workdir), default=None) or {}).get("人名", [])
                     if p["名字"] == real), [])
        if personnames.add_to_roster(real, alts, code):
            added = personnames.rescan_names(workdir)
    with _lock:
        data = wd.read_json(turns_path(workdir))
        data.setdefault("本名代號", {})[real] = code or None
        n = 0
        for p in data["學員"].values():
            if p.get("本名") == real:
                p["代號"] = code or None
                n += 1
        wd.write_json(turns_path(workdir), data)
    from bookclub import epcodes

    epcodes.sync(workdir)   # 老師講到這個名字、保留原聲學員講到的，一起換成這個代號
    return {"ok": True, "本名": real, "代號": code, "改了幾位": n, "補找到的老師名字": added}


# ---------- 09-26：開始前確認「學員是誰」、漏抓的學員發言 ----------

def _new_student_name(data: dict) -> str:
    used = set(data["學員"]) | {t["說話者"] for t in data["段落"]}
    n = 1 + max([int(k[2:]) for k in used if k.startswith("學員") and k[2:].isdigit()] or [0])
    return f"學員{n}"


def _unique_id(data: dict, base: str) -> str:
    ids = {t["id"] for t in data["段落"]}
    k = 1
    while f"{base}m{k}" in ids:
        k += 1
    return f"{base}m{k}"


def merge_person(workdir: str | Path, src: str, dst: str) -> dict:
    """`POST /api/turns/merge_person`：兩位其實是同一人（例如學員 3 和 7），src 的段落全部改成 dst。"""
    if src == dst:
        raise ValueError("要合併的兩位是同一位")
    workdir = Path(workdir)
    with _lock:
        data = wd.read_json(turns_path(workdir))
        if dst not in data["學員"]:
            raise KeyError(f"找不到 {dst}")
        n = 0
        for t in data["段落"]:
            if t["說話者"] == src:
                t["說話者"], t["說話者是人改的"] = dst, True
                n += 1
        old = data["學員"].pop(src, None) or {}
        if not data["學員"][dst].get("代號") and old.get("代號"):
            data["學員"][dst]["代號"] = old["代號"]
        _recount_people(data)
        wd.write_json(turns_path(workdir), data)
        return {"ok": True, "改了幾段": n}


def reassign_turns(workdir: str | Path, ids: list[str], who: str) -> dict:
    """`POST /api/turns/reassign`：幾段一起改說話者（把一位拆成兩位：選幾段改成「新學員」；
    09-29 加：聲音辨識把老師判成學員時，選幾段改成「老師」，並補找這幾段裡老師提到的名字）。"""
    workdir = Path(workdir)
    with _lock:
        data = wd.read_json(turns_path(workdir))
        if who == "新學員":
            who = _new_student_name(data)
            data["學員"][who] = {"秒數": 0.0, "段數": 0, "點名線索": {}, "代號": None}
        want = set(ids)
        hit = [t for t in data["段落"] if t["id"] in want]
        if not hit:
            raise KeyError("找不到要改的段落")
        for t in hit:
            t["說話者"], t["說話者是人改的"] = who, True
        _recount_people(data)
        wd.write_json(turns_path(workdir), data)
    added = 0
    if who == "老師":
        from bookclub import names

        try:
            added = names.append_candidates(workdir, [sid for t in hit for sid in t.get("句子", [])])
        except Exception as exc:   # 補找失敗不要擋住改說話者
            print(f"⚠️ 改成老師後補找名字失敗：{exc}")
    return {"ok": True, "說話者": who, "改了幾段": len(hit), "補找到的老師名字": added}


def mark_student(workdir: str | Path, start: float, end: float, who: str) -> dict:
    """`POST /api/turns/mark_student`：用 I／O 標一段改成學員段落（段落分析漏抓、判成老師的學員發言）。

    句子中點落在起訖裡的句子切出來成學員段落，原本的段落在句子邊界切開；一句都沒有（例如只有 2 秒、
    被併進老師的長句）就照標的起訖建一段「手動標記」的段落，逐字稿先放蓋到的句子當初稿。"""
    if end <= start:
        raise ValueError("結束時間要晚於開始時間")
    workdir = Path(workdir)
    with _lock:
        data = wd.read_json(turns_path(workdir))
        speakers = wd.read_json(wd.speakers_path(workdir)) or {}
        sent = {s["id"]: s for s in speakers.get("sentences", [])}
        if who == "新學員":
            who = _new_student_name(data)
        data["學員"].setdefault(who, {"秒數": 0.0, "段數": 0, "點名線索": {}, "代號": None})
        inside = {sid for sid, s in sent.items() if start <= (s["start"] + s["end"]) / 2 <= end}
        mark = {"說話者": who, "文字判斷": "學員", "說話者是人改的": True, "換人依據": "人工標記（I／O）",
                "內容類型": "學員分享", "已確認": False, "校對秒數": None}
        out, made = [], []
        for t in data["段落"]:
            ids = t["句子"]
            if not any(i in inside for i in ids):
                out.append(t)
                continue
            runs: list[tuple[bool, list[str]]] = []
            for i in ids:
                if runs and runs[-1][0] == (i in inside):
                    runs[-1][1].append(i)
                else:
                    runs.append((i in inside, [i]))
            for k, (isin, run) in enumerate(runs):
                part = {**t, "句子": run, "start": sent[run[0]]["start"], "end": sent[run[-1]]["end"],
                        "原文": "".join(sent[s]["text"] for s in run)}
                part["校對稿"] = t["校對稿"] if len(runs) == 1 else part["原文"]
                if k:
                    part["id"] = _unique_id({"段落": out + [t]}, t["id"])
                if isin:
                    part.update(mark)
                    made.append(part)
                out.append(part)
        if not made:
            near = [s for s in sorted(sent.values(), key=lambda s: s["start"]) if s["start"] < end and start < s["end"]]
            n = 1 + sum(1 for t in out if t["id"].startswith("U"))
            text = "".join(s["text"] for s in near)
            part = {"id": f"U{n:03d}", "start": round(start, 3), "end": round(end, 3), "句子": [], "原文": text,
                    "校對稿": text, "聲音判斷": "不確定", "信心": None, "老師點名": None, "文字學員編號": None,
                    "手動標記": True, **mark}
            out.append(part)
            made.append(part)
        # 同一次標出來、前後相連的學員段落併成一段
        made_ids = {id(t) for t in made}
        merged: list[dict] = []
        for t in sorted(out, key=lambda x: x["start"]):
            if merged and id(t) in made_ids and id(merged[-1]) in made_ids:
                a = merged[-1]
                a.update({"end": t["end"], "句子": a["句子"] + t["句子"], "原文": a["原文"] + t["原文"],
                          "校對稿": a["校對稿"] + t["校對稿"]})
                continue
            merged.append(t)
        data["段落"] = merged
        _recount_people(data)
        wd.write_json(turns_path(workdir), data)
        return {"ok": True, "說話者": who, "段落": [t["id"] for t in merged if id(t) in made_ids]}
