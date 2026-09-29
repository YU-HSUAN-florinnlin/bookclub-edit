"""第 3 步「覆核工作台」的資料與存檔（09-25 宇軒定案：原本第 3、4 步合成一步）。

畫面（`bookclub/web/review.js`）：原片播放器 → 時間軸 → 整體設定（學員換成名冊上的誰、
重新生成／保留原聲）→ 待處理清單（學員段落、老師提到名字、重疊、刪除段落、局部消音，照時間排）。
老師的段落不逐段看，只出現在時間軸上。

決定存在哪裡（寫進 `docs/工作區格式.md`）：
- 學員段落、學員換成誰：沿用 `校對/段落.json`（`bookclub/turns.py` 的存檔函式）
- 老師提到名字：沿用 `名字覆核決定.json`，加 `做法`、`已確認` 欄位（`bookclub/nameplan.py` 讀）
- 重疊、刪除段落、局部消音、學員保留原聲、覆核花的時間：`覆核/覆核決定.json`（這支模組管）
- 「新增修改」面板（09-29）人工補的名字、重疊：`覆核/覆核決定.json` 的 `人工名字`、`人工重疊`；
  時間照類型對齊（規則在 `bookclub/align.py`）；自動抓到的名字、重疊改時間記成 `改過的起訖`

純函式為主（不碰 socket），`bookclub/server.py` 只負責轉手。
"""

from __future__ import annotations

import html
import threading
from datetime import datetime
from pathlib import Path

from bookclub import align
from bookclub import workdir as wd

REVIEW_DIR_NAME = "覆核"
NAME_DECISIONS_FILE = "名字覆核決定.json"     # 跟 server.py、nameplan.py 一致

OVERLAP_HOWS = ("不用改", "兩邊都重生成", "只留老師", "只留老師原聲學員消音", "只留學員", "兩邊都不留")
OVERLAP_ARRANGE = ("前後排開", "照原位置疊著")
NAME_HOWS = ("整句換掉", "只換名字", "直接消音")
NAME_TAGS = ("不是名字", "是地名", "切點削到旁邊的字")
MUTE_WAYS = ("墊底噪", "霧化")
VOICE_CHOICES = ("重新生成", "保留原聲")
CALM_KINDS = ("冥想引導", "導讀")
MAX_TIME_STEP_S = 300.0      # 單次累加的覆核時間上限（離開座位不會灌水）
SNAP_SEARCH_S = 0.5          # 剪點往前後各找多遠的安靜處
PREP_KEYS = ("刪除", "學員", "名字", "保留原聲")   # 開始前 4 件事（09-26 宇軒選 A：先做完才進逐筆清單；09-29 加「名字」、刪除移到第一件）
HANDOVER_S = 1.5             # 重疊離「老師↔學員換人」的地方這麼近，算一來一往的交接
CUT_SUGGEST_FILE = "刪除建議.json"
MANUAL_KINDS = ("刪除段落", "局部消音", "學員發言", "名字", "重疊")   # 「新增修改」面板的五種類型

_lock = threading.Lock()


def review_path(workdir: Path) -> Path:
    return Path(workdir) / REVIEW_DIR_NAME / "覆核決定.json"


def name_decisions_path(workdir: Path) -> Path:
    return Path(workdir) / NAME_DECISIONS_FILE


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_decisions(workdir: Path) -> dict:
    data = wd.read_json(review_path(workdir), default=None) or {}
    data.setdefault("版本", 1)
    for k, v in (("重疊", {}), ("刪除段落", []), ("局部消音", []), ("學員聲音", {}), ("覆核秒數", 0.0),
                 ("刪除建議", {}), ("人工重疊", []), ("人工名字", [])):
        data.setdefault(k, v)
    data.setdefault("開始前確認", {})
    for k in PREP_KEYS:
        data["開始前確認"].setdefault(k, False)
    return data


def _save_decisions(workdir: Path, data: dict) -> None:
    wd.write_json(review_path(workdir), data)


def overlap_id(o: dict) -> str:
    """重疊的 id 用起點時間（重跑找重疊、筆數變了也對得上同一處）。人工補的、改過時間的重疊自己帶 id
    （改了起點也要對得上原本那一筆的決定）。"""
    return str(o["id"]) if o.get("id") else f"O{o['start']:.2f}"


def parse_time(text: str) -> float:
    """「43:15」「1:05:00」「95.5」→ 秒數（純函式）。格式不對丟 ValueError。"""
    parts = str(text).strip().replace("：", ":").split(":")
    if not parts or any(p.strip() == "" for p in parts) or len(parts) > 3:
        raise ValueError(f"看不懂的時間：{text}")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


# ---------------------------------------------------------------------------
# 影片
# ---------------------------------------------------------------------------

def video_path(workdir: Path, override: str | Path | None = None) -> Path | None:
    """原片路徑：伺服器啟動時帶的 --video → `分析結果.json` → 逐字稿記錄的來源。"""
    cands = [override]
    analysis = wd.read_json(wd.analysis_result_path(Path(workdir)), default={}) or {}
    cands.append(analysis.get("video"))
    merged = wd.read_json(wd.merged_transcript_path(Path(workdir)), default={}) or {}
    cands.append(merged.get("source"))
    for c in cands:
        if c and Path(c).expanduser().is_file():
            return Path(c).expanduser()
    return None



# ---------------------------------------------------------------------------
# 每一筆的建議（09-26：使用者只要看、按通過）
# ---------------------------------------------------------------------------

def suggest_overlap(o: dict, turns: list[dict], who: str | None, voices: dict) -> dict:
    """重疊的預設建議（純函式，規則照 02 規格第三節的定案，減少人的決策）：
    1. 學員是「保留原聲」的人 → 不用改
    2. 一來一往交接（老師收尾、學員開口，或反過來；重疊離換人的地方 1.5 秒內）→ 兩邊都重生成、前後排開
    3. 學員在老師連續講話中間附和（整個重疊落在老師的段落裡）→ 只留老師原聲、學員消音
    4. 老師在學員說話中間短短回應（整個重疊落在學員的段落裡）→ 只留學員（09-26 加，待宇軒確認）
    5. 判斷不出來 → 兩邊都重生成、前後排開（最保險）"""
    if who and voices.get(who) == "保留原聲":
        return {"做法": "不用改", "排法": None, "原因": f"{who} 保留原聲，重疊照原樣"}
    ordered = sorted(turns, key=lambda t: t["start"])
    for a, b in zip(ordered, ordered[1:]):
        if (a["說話者"] == "老師") == (b["說話者"] == "老師"):
            continue
        edge = (a["end"] + b["start"]) / 2
        if o["start"] - HANDOVER_S <= edge <= o["end"] + HANDOVER_S:
            how = "老師收尾、學員開口" if a["說話者"] == "老師" else "學員收尾、老師開口"
            return {"做法": "兩邊都重生成", "排法": "前後排開", "原因": f"一來一往交接的地方（{how}），兩邊都重念、前後排開"}
    inside = any(t["說話者"] == "老師" and t["start"] <= o["start"] and o["end"] <= t["end"] for t in ordered)
    if inside:
        return {"做法": "只留老師原聲學員消音", "排法": None,
                "原因": "學員在老師連續講話中間附和（學員那邊短），留老師原聲、學員消音"}
    in_student = any(t["說話者"] != "老師" and t["start"] <= o["start"] and o["end"] <= t["end"] for t in ordered)
    if in_student:   # 09-26 加（待宇軒確認）：第一堂 9 筆都是學員分享中間老師短短回應
        return {"做法": "只留學員", "排法": None,
                "原因": "老師在學員說話中間短短回應（嗯、對），拿掉老師那一小段、學員照常重念"}
    return {"做法": "兩邊都重生成", "排法": "前後排開", "原因": "判斷不出是附和還是交接，先用最保險的做法"}


def replace_real_names(text: str, table: list[dict]) -> tuple[str, list[dict]]:
    """名冊上的本名、敏感詞換成代號／替代詞（純函式）。table：[{寫法, 代號}]，長的先換。
    回傳 (新文字, [{原字, 換成, 位置}])，位置是新文字裡的字元索引，給畫面標出換過的字。"""
    rows = sorted((r for r in table if r.get("代號") and len(r["寫法"]) >= 2), key=lambda r: -len(r["寫法"]))
    out, changes, i = [], [], 0
    text = text or ""
    pos = 0
    while i < len(text):
        hit = next((r for r in rows if text.startswith(r["寫法"], i)), None)
        if hit:
            out.append(hit["代號"])
            changes.append({"原字": hit["寫法"], "換成": hit["代號"], "位置": pos})
            pos += len(hit["代號"])
            i += len(hit["寫法"])
        else:
            out.append(text[i])
            pos += 1
            i += 1
    return "".join(out), changes


def replace_table(workdir: Path | None = None) -> list[dict]:
    """名冊寫法＋敏感詞；給 workdir 就用這一集的代號（`bookclub/epcodes.py`）。"""
    from bookclub import epcodes

    return epcodes.replace_table(workdir)


def cut_suggest_path(workdir: Path) -> Path:
    return Path(workdir) / "校對" / CUT_SUGGEST_FILE


def load_cut_suggestions(workdir: Path) -> list[dict]:
    """影片分析產生的建議刪除段落（`bookclub/cutsuggest.py`）；還沒跑就是空的。"""
    data = wd.read_json(cut_suggest_path(workdir), default=None) or {}
    return data.get("建議", [])


def _in_ranges(a: float, b: float, ranges: list[tuple[float, float]], pad: float = 0.3) -> bool:
    return any(x - pad <= a and b <= y + pad for x, y in ranges)


def set_prep(workdir: str | Path, key: str, done: bool) -> dict:
    """`POST /api/review/prep`：開始前 4 件事（刪除／學員／名字／保留原聲）哪一件做完了。"""
    if key not in PREP_KEYS:
        raise ValueError(f"只能是：{'、'.join(PREP_KEYS)}")
    workdir = Path(workdir)
    with _lock:
        dec = load_decisions(workdir)
        dec["開始前確認"][key] = bool(done)
        _save_decisions(workdir, dec)
        return {"ok": True, "開始前確認": dec["開始前確認"]}


def decide_cut_suggestion(workdir: str | Path, sid: str, choice: str) -> dict:
    """`POST /api/review/cutsuggest`：建議刪除的段落，確認刪除或不刪。
    刪除＝在 `刪除段落[]` 建（或改回）一筆帶 `建議id` 的段落；不刪＝那一筆改成還原。"""
    if choice not in ("刪除", "不刪"):
        raise ValueError("只能選：刪除、不刪")
    workdir = Path(workdir)
    sug = next((x for x in load_cut_suggestions(workdir) if x["id"] == sid), None)
    if sug is None:
        raise KeyError(f"找不到這筆建議：{sid}")
    with _lock:
        dec = load_decisions(workdir)
        dec["刪除建議"][sid] = {"決定": choice, "更新時間": _now()}
        _save_decisions(workdir, dec)
        existing = next((x for x in dec["刪除段落"] if x.get("建議id") == sid), None)
    if existing is None and choice == "刪除":
        return _upsert_range(workdir, "刪除段落", "D", {"start": sug["start"], "end": sug["end"], "狀態": "刪除",
                                                     "建議id": sid, "備註": sug.get("原因", "")}, snap=False)
    if existing is not None:
        return _upsert_range(workdir, "刪除段落", "D", {"id": existing["id"],
                                                     "狀態": "刪除" if choice == "刪除" else "還原"}, snap=False)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 組畫面資料
# ---------------------------------------------------------------------------

def _overlapping(sents: list[dict], a: float, b: float) -> list[dict]:
    return [s for s in sents if s["start"] < b and a < s["end"]]


def _overlap_defaults(o: dict, sents: list[dict], turns: list[dict]) -> dict:
    """重疊處兩邊各說了什麼的初稿：蓋到重疊時間的句子，老師句子給老師、其他給學員；
    學員是誰：蓋到這個時間的學員段落，沒有就找前後 10 秒內最近的學員段落。"""
    near = _overlapping(sents, o["start"] - 0.3, o["end"] + 0.3)
    teacher = "".join(s["text"] for s in near if s.get("label") == "老師")
    student = "".join(s["text"] for s in near if s.get("label") != "老師")
    stu_turns = [t for t in turns if t.get("說話者") and t["說話者"] != "老師"]
    who = next((t["說話者"] for t in stu_turns if t["start"] <= o["end"] and o["start"] <= t["end"]), None)
    if who is None and stu_turns:
        dist = lambda t: max(0.0, t["start"] - o["end"], o["start"] - t["end"])  # noqa: E731
        best = min(stu_turns, key=dist)
        who = best["說話者"] if dist(best) <= 10 else None
    context = [{"說話者": "老師" if s.get("label") == "老師" else "學員", "text": s["text"],
                "start": s["start"], "end": s["end"]}
               for s in _overlapping(sents, o["start"] - 3, o["end"] + 3)]
    return {"老師文字": teacher, "學員文字": student, "學員說話者": who, "附近逐字稿": context}


ALIGN_KEYS = ("標的起訖", "對齊", "對齊到")


def _align_info(d: dict) -> dict:
    """畫面「你標的 → 對齊後」要的欄位（有才帶）。"""
    return {k: d[k] for k in ALIGN_KEYS if k in d}


def effective_name_candidates(workdir: Path, candidates: list[dict], decisions: dict) -> list[dict]:
    """名字候選＋人工補的名字（`人工名字`），改過時間的套上 `改過的起訖`。

    自動抓到的照舊用順位當 id（不帶 `id` 欄位，`nameplan.build_plan` 照舊編號）；人工補的帶 `id`（NM001…），
    這樣重跑找名字、候選變多也不會跟人工補的撞號。對齊過的時間已經留過停頓，建議緩衝歸零。
    `nameplan.compute_plan` 與覆核工作台共用，兩邊看到的名字一樣。"""
    out = []
    for i, c in enumerate(candidates, start=1):
        d = decisions.get(str(i), {}) or {}
        if d.get("改過的起訖"):
            c = {**c, "start": d["改過的起訖"][0], "end": d["改過的起訖"][1], "建議緩衝秒數": 0.0, "改過時間": True}
        out.append(c)
    for m in load_decisions(Path(workdir))["人工名字"]:
        out.append({"id": m["id"], "start": m["start"], "end": m["end"], "sentence_id": m.get("sentence_id"),
                    "sentence": m.get("sentence", ""), "matched_text": m.get("matched_text", ""),
                    "name": m.get("matched_text", ""), "canonical": "", "代號": m.get("代號", ""), "敏感詞": False,
                    "位置": "", "比對層級": "人工", "信心": "", "建議做法": "", "切點信心": "", "建議緩衝秒數": 0.0,
                    "人工新增": True, **_align_info(m)})
    return out


def _names_items(workdir: Path, sents: list[dict]) -> list[dict]:
    from bookclub import nameplan
    from bookclub.namespage import _highlight_sentence

    result = wd.read_json(wd.names_path(workdir), default=None) or {}
    decisions = wd.read_json(name_decisions_path(workdir), default={}) or {}
    ordered = sorted(sents, key=lambda s: s["start"])
    pos = {s["id"]: k for k, s in enumerate(ordered)}
    items = []
    cands = effective_name_candidates(workdir, result.get("candidates", []), decisions)
    for i, c in enumerate(cands, start=1):
        cid = str(c.get("id") or i)
        d = decisions.get(cid, {}) or {}
        group = nameplan.expand_sentence(ordered, pos[c["sentence_id"]]) if c.get("sentence_id") in pos else []
        whole_text = "".join(g["text"] for g in group)
        replaced = None
        if group:
            texts = {g["id"]: g["text"] for g in group}
            new = nameplan.replace_name(texts[c["sentence_id"]], c)
            if new is not None:
                texts[c["sentence_id"]] = new
                replaced = "".join(texts[g["id"]] for g in group)
        sentence = c.get("sentence") or (ordered[pos[c["sentence_id"]]]["text"] if c.get("sentence_id") in pos else "")
        items.append({
            "類型": "名字", "id": cid, "start": c["start"], "end": c["end"],
            "sentence_html": _highlight_sentence(sentence, c.get("matched_text", ""), c.get("位置", "")),
            "整句": {"start": group[0]["start"], "end": group[-1]["end"], "原文": whole_text, "換成代號": replaced}
            if group else None,
            "matched_text": c.get("matched_text", ""), "代號": c.get("代號", ""), "位置": c.get("位置", ""),
            "信心": c.get("信心", ""), "比對層級": c.get("比對層級", ""), "切點信心": c.get("切點信心", ""),
            "建議做法": c.get("建議做法", ""), "敏感詞": bool(c.get("敏感詞")),
            "做法": d.get("做法") or nameplan.WHOLE, "tags": d.get("tags", []), "note": d.get("note", ""),
            "已確認": bool(d.get("已確認")), "人工新增": bool(c.get("人工新增")), **_align_info(c), **_align_info(d),
        })
    return items


def effective_overlaps(workdir: Path, overlaps: list[dict], dec: dict | None = None) -> list[dict]:
    """重疊清單＋人工補的重疊（`人工重疊`），改過時間的套上 `改過的起訖`（帶原本的 id，決定才對得上）。
    覆核工作台與組裝（`render.build_decisions`）共用。"""
    dec = dec if dec is not None else load_decisions(Path(workdir))
    out = []
    for o in overlaps:
        oid = overlap_id(o)
        d = dec["重疊"].get(oid, {})
        if d.get("改過的起訖"):
            a, b = d["改過的起訖"]
            o = {**o, "id": oid, "start": a, "end": b, "length": round(b - a, 3), "改過時間": True, **_align_info(d)}
        out.append(o)
    for m in dec["人工重疊"]:
        out.append({"id": m["id"], "start": m["start"], "end": m["end"], "length": round(m["end"] - m["start"], 3),
                    "speakers": [], "已自動跳過": False, "原因": None, "人工新增": True, **_align_info(m)})
    return sorted(out, key=lambda o: o["start"])


def roster_words() -> list[str]:
    """名冊上的本名與其他寫法（找「還沒換成代號的本名」用）。"""
    from bookclub import names
    from bookclub.config import data_dir

    return sorted({r["寫法"] for r in names.load_roster(data_dir() / "名冊.csv") if len(r["寫法"]) >= 2},
                  key=len, reverse=True)


def has_real_name(text: str, words: list[str]) -> bool:
    return any(w in (text or "") for w in words)


def page_data(workdir: str | Path, video: str | Path | None = None) -> dict:
    """`GET /api/review`：覆核工作台一次要的全部資料。每一筆都帶 `建議`（做法＋一行原因），
    落在確認刪除範圍裡、或保留原聲學員的學員段落帶 `不用處理`（原因）。"""
    from bookclub import overlap as overlap_mod
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    speakers = wd.read_json(wd.speakers_path(workdir), default={}) or {}
    sents = speakers.get("sentences", [])
    merged = wd.read_json(wd.merged_transcript_path(workdir), default={}) or {}
    analysis = wd.read_json(wd.analysis_result_path(workdir), default={}) or {}
    duration = analysis.get("影片長度") or merged.get("duration") or (sents[-1]["end"] if sents else 0)

    from bookclub import epcodes

    epcodes.sync(workdir)   # 09-29：名字候選的代號跟這一集的代號表對齊（沒變就不寫檔）
    tdata = turns_mod.page_data(workdir)
    has_turns = not tdata.get("尚未準備")
    turns = tdata.get("段落", []) if has_turns else []
    # 09-29 宇軒：段落都被併走／改成老師的學員（0 段）點試聽沒反應，不列
    people = {k: p for k, p in (tdata.get("學員", {}) if has_turns else {}).items() if p.get("段數")}
    dec = load_decisions(workdir)
    voices = {name: dec["學員聲音"].get(name, "重新生成") for name in people}
    for name, p in people.items():
        p["聲音"] = voices[name]

    # 時間軸色帶：老師（灰）、學員、冥想導讀（淡藍）
    bands = [{"start": t["start"], "end": t["end"], "說話者": t["說話者"],
              "冥想導讀": t.get("內容類型") in CALM_KINDS, "id": t["id"]} for t in turns]
    if not bands and sents:   # 還沒有段落分析：用逐句判斷畫
        bands = [{"start": s["start"], "end": s["end"], "說話者": "老師" if s.get("label") == "老師" else "學員?",
                  "冥想導讀": False, "id": s["id"]} for s in sents]

    # 刪除段落：建議（第 1 步影片分析產生）＋人手動加的
    suggestions = load_cut_suggestions(workdir)
    cut_ranges = [(c["start"], c["end"]) for c in dec["刪除段落"] if c.get("狀態") != "還原"]

    def skip_reason(a: float, b: float) -> str | None:
        return "落在確認刪除的段落裡" if _in_ranges(a, b, cut_ranges) else None

    # 09-29 宇軒：只在確認刪除的段落（結尾道別等）裡講話的學員，不用判斷是誰（① 建議刪除段落選了「刪除」才算）
    will_cut = cut_ranges + [(sg["start"], sg["end"]) for sg in suggestions
                             if dec["刪除建議"].get(sg["id"], {}).get("決定") == "刪除"]
    for name, p in people.items():
        segs = [t for t in turns if t["說話者"] == name]
        p["都會刪掉"] = bool(segs) and all(_in_ranges(t["start"], t["end"], will_cut) for t in segs)

    words = roster_words()
    table = replace_table(workdir)
    items: list[dict] = []
    for k, t in enumerate(turns):
        if t["說話者"] == "老師":
            continue
        draft, changes = (t["校對稿"], []) if t["已確認"] else replace_real_names(t["校對稿"], table)
        keep = voices.get(t["說話者"]) == "保留原聲"
        why = "逐字稿看過、沒有錯字就通過（成品會照這份文字重念）"
        if changes:
            why = f"已把名冊上的名字換成代號：{'、'.join(sorted({c['換成'] for c in changes}))}；" + why
        if t.get("學員是猜的"):
            why = "這段太短、學員是猜的，聽一下是誰；" + why
        items.append({"類型": "學員段落", "id": t["id"], "序": k + 1, "start": t["start"], "end": t["end"],
                      "說話者": t["說話者"], "校對稿": t["校對稿"], "建議稿": draft, "換過的字": changes,
                      "原文": t["原文"], "已確認": t["已確認"],
                      "問老師": bool(t.get("問老師")), "問老師備註": t.get("問老師備註", ""),
                      "內容類型": t.get("內容類型"), "換人依據": t.get("換人依據"), "手動標記": bool(t.get("手動標記")),
                      "學員是猜的": bool(t.get("學員是猜的")), "含本名": has_real_name(draft, words),
                      "人工新增": bool(t.get("人工新增")), **_align_info(t),
                      "建議": {"做法": "通過", "原因": why},
                      "不用處理": "保留原聲，不用校對逐字稿" if keep else skip_reason(t["start"], t["end"])})
    for it in _names_items(workdir, sents):
        cut_hint = f"；第 1 步切點分析建議：{it['建議做法']}" if it.get("建議做法") and it["建議做法"] != nameplan_whole() else ""
        it["建議"] = {"做法": nameplan_whole(), "原因": "預設整句用老師 AI 聲音重念、名字換成代號（09-25 定案）" + cut_hint}
        it["不用處理"] = skip_reason(it["start"], it["end"])
        items.append(it)
    # 09-29：保留原聲的學員自己講到名字（設成保留原聲才會列；改回重新生成就不列）
    from bookclub import studentnames

    try:
        items.extend(studentnames.items(workdir, cut_ranges))
    except Exception as exc:   # 找名字失敗不要擋住整個工作台
        print(f"⚠️ 保留原聲學員的名字找不到：{exc}")

    ov = wd.read_json(wd.overlap_path(workdir), default=None)
    if ov is None and dec["人工重疊"]:
        ov = {"overlaps": []}
    skipped = []
    if ov is not None:
        overlap_mod.apply_simple_filters(ov)   # 只在記憶體裡套，不改檔
        for o in effective_overlaps(workdir, ov.get("overlaps", []), dec):
            oid = overlap_id(o)
            d = dec["重疊"].get(oid, {})
            base = {"id": oid, "start": o["start"], "end": o["end"], "length": o["length"],
                    "角色": [s["role"] for s in o.get("speakers", [])]}
            if o.get("已自動跳過") and not d.get("救回"):
                skipped.append({**base, "原因": o.get("原因")})
                continue
            defaults = _overlap_defaults(o, sents, turns)
            who = d.get("學員說話者", defaults["學員說話者"])
            items.append({"類型": "重疊", **base, **defaults,
                          "老師文字": d.get("老師文字", defaults["老師文字"]),
                          "學員文字": d.get("學員文字", defaults["學員文字"]),
                          "學員說話者": who,
                          "做法": d.get("做法"), "排法": d.get("排法", OVERLAP_ARRANGE[0]),
                          "備註": d.get("備註", ""), "已確認": bool(d.get("已確認")), "救回": bool(d.get("救回")),
                          "人工新增": bool(o.get("人工新增")), **_align_info(o),
                          "建議": suggest_overlap(o, turns, who, voices),
                          "不用處理": skip_reason(o["start"], o["end"])})
    linked = {c["建議id"]: c for c in dec["刪除段落"] if c.get("建議id")}
    for sg in suggestions:
        d = dec["刪除建議"].get(sg["id"], {})
        c = linked.get(sg["id"], {})     # 確認刪除後改過時間的，照改過的
        items.append({"類型": "刪除段落", "id": sg["id"], "start": c.get("start", sg["start"]),
                      "end": c.get("end", sg["end"]), "來源": "建議", **_align_info(c),
                      "建議類型": sg.get("類型"), "決定": d.get("決定"), "已確認": bool(d.get("決定")),
                      "狀態": "還原" if d.get("決定") == "不刪" else "刪除",
                      "建議": {"做法": "刪除", "原因": f"{sg.get('類型', '')}：{sg.get('原因', '')}".strip("：")},
                      "不用處理": None})
    for c in dec["刪除段落"]:
        if c.get("建議id"):
            continue
        items.append({"類型": "刪除段落", **c, "來源": "手動", "已確認": True, "人工新增": True,
                      "建議": {"做法": "刪除" if c.get("狀態") != "還原" else "還原", "原因": "人手動加的"},
                      "不用處理": None})
    for m in dec["局部消音"]:
        items.append({"類型": "局部消音", **m, "已確認": True, "人工新增": True,
                      "建議": {"做法": m.get("方式", MUTE_WAYS[0]), "原因": "人手動加的"}, "不用處理": None})
    items.sort(key=lambda x: (x["start"], x["類型"]))

    return {
        "workdir": str(workdir),
        "影片": {"有影片": video_path(workdir, video) is not None, "網址": "/api/video",
                 "檔名": (video_path(workdir, video) or Path("")).name, "長度": duration},
        "有段落": has_turns,
        "色帶": bands,
        "學員": people,
        "代號選項": tdata.get("代號選項", []),
        "學員資料": {k: tdata.get(k) for k in ("本名選項", "名冊代號", "老師名稱", "本名代號", "這一集的名字")},   # 09-29「學員是誰」兩欄
        "代號重複": _dup_codes(workdir, tdata),
        "名冊上沒有的名字": _unlisted_names(workdir, {p["本名"] for p in tdata.get("學員", {}).values() if p.get("本名")},
                                           will_cut),
        "項目": items,
        "已自動跳過的重疊": skipped,
        "刪除建議": [{**sg, "決定": dec["刪除建議"].get(sg["id"], {}).get("決定")} for sg in suggestions],
        "開始前確認": dec["開始前確認"],
        "選項": {"重疊": OVERLAP_HOWS, "重疊排法": OVERLAP_ARRANGE, "名字": NAME_HOWS, "名字標記": NAME_TAGS,
                 "學員名字": list(studentnames.HOWS),
                 "消音": MUTE_WAYS, "聲音": VOICE_CHOICES},
        "進度": progress(items, dec, duration),
    }


def _dup_codes(workdir: Path, tdata: dict) -> dict:
    """這一集有出現的人（選成本名的學員＋老師講到的名字）裡，兩個以上用同一個代號的。"""
    from bookclub import epcodes

    here = {p["本名"] for p in tdata.get("學員", {}).values() if p.get("本名")}
    here |= {c.get("canonical") for c in (wd.read_json(wd.names_path(workdir), default={}) or {}).get("candidates", [])
             if c.get("canonical") and not c.get("敏感詞")}
    return epcodes.duplicates(workdir, here)


def _unlisted_names(workdir: Path, chosen: set[str] | None = None, cuts: list | None = None) -> list[dict]:
    from bookclub import personnames

    try:
        return personnames.unlisted(workdir, chosen, cuts)
    except Exception as exc:  # noqa: BLE001 — 人名清單壞掉不要擋住工作台
        print(f"⚠️ 人名清單讀不到：{exc}")
        return []


def nameplan_whole() -> str:
    from bookclub import nameplan

    return nameplan.WHOLE


def progress(items: list[dict], dec: dict, duration: float) -> dict:
    """已確認／總數、覆核花的時間、推算整支要多久（純函式）。"""
    total = len(items)
    done = sum(1 for x in items if x.get("已確認") or x.get("不用處理"))
    spent = float(dec.get("覆核秒數") or 0.0)
    passed = sum(1 for x in items if x.get("已確認") and not x.get("不用處理"))
    need = sum(1 for x in items if not x.get("不用處理"))
    est = spent / passed * need if passed else None
    by_type: dict[str, list[int]] = {}
    for x in items:
        c = by_type.setdefault(x["類型"], [0, 0])
        c[1] += 1
        c[0] += bool(x.get("已確認") or x.get("不用處理"))
    return {"已確認": done, "總數": total, "已花秒數": round(spent), "推算全部秒數": round(est) if est else None,
            "各類": {k: {"已確認": v[0], "總數": v[1]} for k, v in by_type.items()}}


# ---------------------------------------------------------------------------
# 存檔
# ---------------------------------------------------------------------------

def save_name(workdir: str | Path, cid: str, fields: dict) -> dict:
    """`POST /api/review/name`：做法、標記（不是名字／是地名／切點削到旁邊的字）、備註、已確認。
    標「不是名字」「是地名」時，跟舊的名字覆核頁一樣把抓到的字加進排除清單。"""
    workdir = Path(workdir)
    cid = str(cid)
    with _lock:
        decisions = wd.read_json(name_decisions_path(workdir), default={}) or {}
        d = decisions.setdefault(cid, {"tags": [], "note": ""})
        if "做法" in fields:
            if fields["做法"] not in NAME_HOWS:
                raise ValueError(f"名字的做法只能是：{'、'.join(NAME_HOWS)}")
            d["做法"] = fields["做法"]
        if "tags" in fields:
            d["tags"] = [t for t in fields["tags"] if t in NAME_TAGS]
        if "note" in fields:
            d["note"] = str(fields["note"])
        if "已確認" in fields:
            d["已確認"] = bool(fields["已確認"])
        d["更新時間"] = _now()
        wd.write_json(name_decisions_path(workdir), decisions)
    added = False
    if any(t in ("是地名", "不是名字") for t in d.get("tags", [])) and "tags" in fields:
        from bookclub.server import _append_exclusion

        cands = (wd.read_json(wd.names_path(workdir), default={}) or {}).get("candidates", [])
        idx = int(cid) - 1 if cid.isdigit() else -1     # 人工補的名字（NM001…）不在候選清單裡
        if 0 <= idx < len(cands) and cands[idx].get("matched_text"):
            added = _append_exclusion(cands[idx]["matched_text"],
                                      "、".join(t for t in d["tags"] if t in ("是地名", "不是名字")))
    return {"ok": True, "id": cid, "決定": d, "已加入排除清單": added}


def save_overlap(workdir: str | Path, oid: str, fields: dict) -> dict:
    """`POST /api/review/overlap`：做法、排法（兩邊都重生成時）、兩邊文字、學員是誰、備註、已確認、救回。"""
    workdir = Path(workdir)
    with _lock:
        dec = load_decisions(workdir)
        d = dec["重疊"].setdefault(str(oid), {})
        if "做法" in fields:
            if fields["做法"] not in OVERLAP_HOWS:
                raise ValueError(f"重疊的做法只能是：{'、'.join(OVERLAP_HOWS)}")
            d["做法"] = fields["做法"]
        if "排法" in fields:
            if fields["排法"] not in OVERLAP_ARRANGE:
                raise ValueError(f"排法只能是：{'、'.join(OVERLAP_ARRANGE)}")
            d["排法"] = fields["排法"]
        for k in ("老師文字", "學員文字", "學員說話者", "備註"):
            if k in fields:
                d[k] = str(fields[k]) if fields[k] is not None else None
        for k in ("已確認", "救回"):
            if k in fields:
                d[k] = bool(fields[k])
        if d.get("已確認") and not d.get("做法"):
            raise ValueError("先選這一處要怎麼處理，再確認")
        d["更新時間"] = _now()
        _save_decisions(workdir, dec)
        return {"ok": True, "id": oid, "決定": d}


def snap_to_quiet(workdir: Path, t: float, search_s: float = SNAP_SEARCH_S) -> tuple[float, bool]:
    """剪點對齊到附近安靜處（沿用找名字切點的做法：`names._refine_cut`）。只讀剪點附近幾秒的聲音。"""
    import soundfile as sf

    from bookclub.names import PAUSE_CONTEXT_S, _find_pause_point

    audio = wd.audio_path(Path(workdir))
    if not audio.exists():
        return round(t, 3), False
    with sf.SoundFile(str(audio)) as f:
        sr = f.samplerate
        w0 = max(0.0, t - 2.0)
        f.seek(int(w0 * sr))
        x = f.read(int(4.0 * sr), dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    if len(x) == 0:
        return round(t, 3), False
    # 人手按 I／O 會慢零點幾秒，搜尋範圍比找名字切點（0.3 秒）寬
    c = t - w0
    idx, clean = _find_pause_point(x, sr, int(c * sr), int((c - search_s) * sr), int((c + search_s) * sr),
                                   int((c - PAUSE_CONTEXT_S) * sr), int((c + PAUSE_CONTEXT_S) * sr))
    return round(idx / sr + w0, 3), clean


def _upsert_range(workdir: Path, key: str, prefix: str, fields: dict, *, snap: bool) -> dict:
    with _lock:
        dec = load_decisions(workdir)
        lst = dec[key]
        item = next((x for x in lst if x["id"] == fields.get("id")), None) if fields.get("id") else None
        if item is None:
            n = 1 + max([int(x["id"][1:]) for x in lst if x["id"][1:].isdigit()] or [0])
            item = {"id": f"{prefix}{n:03d}", "建立時間": _now()}
            lst.append(item)
        if "start" in fields or "end" in fields:
            a = float(fields.get("start", item.get("start", 0.0)))
            b = float(fields.get("end", item.get("end", 0.0)))
            if b <= a:
                raise ValueError("結束時間要晚於開始時間")
            item["標的起訖"] = [round(a, 3), round(b, 3)]
            if snap:
                (a, ca), (b, cb) = snap_to_quiet(workdir, a), snap_to_quiet(workdir, b)
                item["剪點對齊安靜處"] = [ca, cb]
            item["start"], item["end"] = round(a, 3), round(b, 3)
        for k, v in fields.items():
            if k in ("id", "start", "end"):
                continue
            item[k] = v
        item["更新時間"] = _now()
        lst.sort(key=lambda x: x.get("start", 0))
        _save_decisions(workdir, dec)
        return {"ok": True, "項目": item}


def save_cut(workdir: str | Path, fields: dict) -> dict:
    """`POST /api/review/cut`：新增或修改一段刪除段落（剪點自動對齊附近安靜處）；狀態＝刪除／還原。"""
    if "狀態" in fields and fields["狀態"] not in ("刪除", "還原"):
        raise ValueError("刪除段落的狀態只能是：刪除、還原")
    f = {"狀態": "刪除", **{k: v for k, v in fields.items() if k in ("id", "start", "end", "狀態", "備註")}} \
        if not fields.get("id") else {k: v for k, v in fields.items() if k in ("id", "start", "end", "狀態", "備註")}
    return _upsert_range(Path(workdir), "刪除段落", "D", f, snap=True)


def save_mute(workdir: str | Path, fields: dict) -> dict:
    """`POST /api/review/mute`：新增或修改一段局部消音（只消聲音、畫面保留）；方式＝墊底噪／霧化。"""
    if "方式" in fields and fields["方式"] not in MUTE_WAYS:
        raise ValueError(f"消音方式只能是：{'、'.join(MUTE_WAYS)}")
    f = {k: v for k, v in fields.items() if k in ("id", "start", "end", "方式", "備註", "狀態")}
    if not fields.get("id"):
        f = {"方式": MUTE_WAYS[0], "狀態": "消音", **f}
    return _upsert_range(Path(workdir), "局部消音", "M", f, snap=False)


# ---------------------------------------------------------------------------
# 「新增修改」面板（09-29）：人標起訖 → 照類型對齊 → 新增或改時間
# ---------------------------------------------------------------------------

def _as_time(v) -> float:
    """面板送來的時間：數字（秒）或「43:15.2」這種文字（沿用 parse_time）。"""
    return float(v) if isinstance(v, (int, float)) else parse_time(str(v))


def align_range(workdir: Path, kind: str, a: float, b: float) -> dict:
    """照類型對齊（規則見 `bookclub/align.py`）。讀檔在這裡，對齊本身是純函式。"""
    rule = align.RULES[kind]
    if rule == align.QUIET:
        return align.align_quiet(a, b, lambda t: snap_to_quiet(workdir, t))
    merged = wd.read_json(wd.merged_transcript_path(workdir), default={}) or {}
    if rule == align.WORDS:
        return align.align_words(a, b, merged.get("words") or [])
    sents = merged.get("sentences") or (wd.read_json(wd.speakers_path(workdir), default={}) or {}).get("sentences", [])
    return align.align_sentences(a, b, sents)


def manual_edit(workdir: str | Path, fields: dict) -> dict:
    """`POST /api/review/manual`：新增一筆（沒帶 id）或改已經有的那一筆的時間（帶 id）。

    fields：`類型`（MANUAL_KINDS）、`start`、`end`（秒數或「43:15.2」）、`id`（改時間時）；
    學員發言另有 `說話者`，名字另有 `代號`（新增必填）、`名字`（逐字稿裡寫成什麼，不給就用對齊到的字），
    局部消音另有 `方式`。回傳對齊結果（你標的 → 對齊後、兩端各有沒有對到）。"""
    workdir = Path(workdir)
    kind = str(fields.get("類型", ""))
    if kind not in MANUAL_KINDS:
        raise ValueError(f"類型只能是：{'、'.join(MANUAL_KINDS)}")
    a, b = _as_time(fields["start"]), _as_time(fields["end"])
    if b <= a:
        raise ValueError("終點要晚於起點")
    iid = str(fields["id"]) if fields.get("id") else None
    al = align_range(workdir, kind, a, b)
    info = {"標的起訖": al["標的起訖"], "對齊": al["對齊"], "對齊到": al["對齊到"]}
    if kind == "刪除段落":
        new_id = _manual_cut(workdir, iid, al, info)
    elif kind == "局部消音":
        new_id = _manual_mute(workdir, iid, al, info, fields)
    elif kind == "重疊":
        new_id = _manual_overlap(workdir, iid, al, info)
    elif kind == "名字":
        new_id = _manual_name(workdir, iid, al, info, fields)
    else:
        new_id = _manual_student(workdir, iid, al, info, fields)
    return {"ok": True, "類型": "學員段落" if kind == "學員發言" else kind, "id": new_id, "新增": iid is None,
            "對齊結果": {k: al[k] for k in ("start", "end", "標的起訖", "對齊", "對齊到")}}


def _manual_cut(workdir: Path, iid: str | None, al: dict, info: dict) -> str:
    info = {**info, "剪點對齊安靜處": al["對齊"]}      # 舊欄位名，組裝與匯出照舊讀得到
    if iid and not iid.startswith("D"):
        # 影片分析建議的（S1…）：改時間＝確認刪除、用新的起訖
        sug = next((x for x in load_cut_suggestions(workdir) if x["id"] == iid), None)
        if sug is None:
            raise KeyError(f"找不到這筆建議：{iid}")
        with _lock:
            dec = load_decisions(workdir)
            dec["刪除建議"][iid] = {"決定": "刪除", "更新時間": _now()}
            _save_decisions(workdir, dec)
            existing = next((x for x in dec["刪除段落"] if x.get("建議id") == iid), None)
        base = {"id": existing["id"]} if existing else {"建議id": iid, "備註": sug.get("原因", "")}
        _upsert_range(workdir, "刪除段落", "D", {**base, "start": al["start"], "end": al["end"], "狀態": "刪除", **info},
                      snap=False)
        return iid
    f = {"start": al["start"], "end": al["end"], **info}
    f.update({"id": iid} if iid else {"狀態": "刪除", "來源": "人工新增"})
    return _upsert_range(workdir, "刪除段落", "D", f, snap=False)["項目"]["id"]


def _manual_mute(workdir: Path, iid: str | None, al: dict, info: dict, fields: dict) -> str:
    f = {"start": al["start"], "end": al["end"], **info}
    if iid:
        f["id"] = iid
    else:
        way = fields.get("方式") or MUTE_WAYS[0]
        if way not in MUTE_WAYS:
            raise ValueError(f"消音方式只能是：{'、'.join(MUTE_WAYS)}")
        f.update({"方式": way, "狀態": "消音", "來源": "人工新增"})
    return _upsert_range(workdir, "局部消音", "M", f, snap=False)["項目"]["id"]


def _next_id(lst: list[dict], prefix: str) -> str:
    n = 1 + max([int(x["id"][len(prefix):]) for x in lst if x["id"][len(prefix):].isdigit()] or [0])
    return f"{prefix}{n:03d}"


def _manual_overlap(workdir: Path, iid: str | None, al: dict, info: dict) -> str:
    with _lock:
        dec = load_decisions(workdir)
        mine = next((x for x in dec["人工重疊"] if x["id"] == iid), None) if iid else None
        if mine is not None:
            mine.update({"start": al["start"], "end": al["end"], **info, "更新時間": _now()})
        elif iid:     # 自動抓到的重疊改時間：記在決定裡，原本的 重疊.json 不動
            d = dec["重疊"].setdefault(iid, {})
            d.update({"改過的起訖": [al["start"], al["end"]], **info, "更新時間": _now()})
        else:
            iid = _next_id(dec["人工重疊"], "OM")
            dec["人工重疊"].append({"id": iid, "start": al["start"], "end": al["end"], "來源": "人工新增", **info,
                                  "建立時間": _now(), "更新時間": _now()})
            dec["人工重疊"].sort(key=lambda x: x["start"])
        _save_decisions(workdir, dec)
    return iid


def _manual_name(workdir: Path, iid: str | None, al: dict, info: dict, fields: dict) -> str:
    sents = (wd.read_json(wd.speakers_path(workdir), default={}) or {}).get("sentences", [])
    sent = align.sentence_at((al["start"] + al["end"]) / 2, sents)
    word = str(fields.get("名字") or "").strip() or al["文字"]
    with _lock:
        dec = load_decisions(workdir)
        mine = next((x for x in dec["人工名字"] if x["id"] == iid), None) if iid else None
        if iid and mine is None:   # 自動抓到的名字改時間：記在名字覆核決定裡
            decisions = wd.read_json(name_decisions_path(workdir), default={}) or {}
            d = decisions.setdefault(iid, {"tags": [], "note": ""})
            d.update({"改過的起訖": [al["start"], al["end"]], **info, "更新時間": _now()})
            wd.write_json(name_decisions_path(workdir), decisions)
            return iid
        if mine is None:
            code = str(fields.get("代號") or "").strip()
            if not code:
                raise ValueError("選這個名字要換成哪個代號")
            iid = _next_id(dec["人工名字"], "NM")
            mine = {"id": iid, "代號": code, "來源": "人工新增", "建立時間": _now()}
            dec["人工名字"].append(mine)
        elif fields.get("代號"):
            mine["代號"] = str(fields["代號"]).strip()
        mine.update({"start": al["start"], "end": al["end"], "matched_text": word,
                     "sentence_id": sent["id"] if sent else None, "sentence": sent["text"] if sent else "",
                     **info, "更新時間": _now()})
        dec["人工名字"].sort(key=lambda x: x["start"])
        _save_decisions(workdir, dec)
    return iid


def _manual_student(workdir: Path, iid: str | None, al: dict, info: dict, fields: dict) -> str:
    from bookclub import turns as turns_mod

    if iid:
        retime_turn(workdir, iid, al["start"], al["end"])
        ids = [iid]
    else:
        ids = turns_mod.mark_student(workdir, al["start"], al["end"], str(fields.get("說話者") or "新學員"))["段落"]
    with turns_mod._lock:
        data = wd.read_json(turns_mod.turns_path(workdir))
        for t in data["段落"]:
            if t["id"] in ids:
                t.update(info)
                if not iid:
                    t["人工新增"] = True
        wd.write_json(turns_mod.turns_path(workdir), data)
    return ids[0] if ids else ""


def retime_turn(workdir: str | Path, tid: str, start: float, end: float) -> dict:
    """段落改起訖（純邏輯在 `retime_turns`）。"""
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    with turns_mod._lock:
        data = wd.read_json(turns_mod.turns_path(workdir))
        speakers = wd.read_json(wd.speakers_path(workdir)) or {}
        sent = {s["id"]: s for s in speakers.get("sentences", [])}
        data["段落"] = retime_turns(data["段落"], tid, start, end, sent)
        turns_mod._recount_people(data)
        wd.write_json(turns_mod.turns_path(workdir), data)
    return {"ok": True}


def retime_turns(turns: list[dict], tid: str, start: float, end: float, sent: dict[str, dict]) -> list[dict]:
    """一段改起訖（純函式）：中點落在新範圍裡的句子歸這一段（從別段拿過來，別段拿空了就刪掉）；
    這一段原本的句子落到範圍外的，還給時間上相鄰的那一段（前面的給前一段、後面的給後一段；沒有相鄰的
    就自己成一段老師段落）。句子變了的段落重算原文；校對稿沒改過就跟著換，改過的保留、標成還沒確認。"""
    import copy

    ts = sorted(copy.deepcopy(turns), key=lambda t: t["start"])
    k = next((i for i, t in enumerate(ts) if t["id"] == tid), None)
    if k is None:
        raise KeyError(f"找不到這一段：{tid}")
    me = ts[k]
    inside = {sid for sid, s in sent.items() if start <= (s["start"] + s["end"]) / 2 <= end}
    old = [i for i in me["句子"] if i in sent]
    before = [i for i in old if i not in inside and sent[i]["start"] < start]
    after = [i for i in old if i not in inside and sent[i]["start"] >= start]
    changed = {id(me)}
    for t in ts:
        if t is not me and any(i in inside for i in t["句子"]):
            t["句子"] = [i for i in t["句子"] if i not in inside]
            changed.add(id(t))
    me["句子"] = sorted(inside, key=lambda i: sent[i]["start"])
    prev = ts[k - 1] if k > 0 else None
    nxt = ts[k + 1] if k + 1 < len(ts) else None
    for give, to, at_end in ((before, prev, True), (after, nxt, False)):
        if not give:
            continue
        if to is None or not to["句子"]:
            to = {"id": f"{me['id']}r{len(ts)}", "start": 0.0, "end": 0.0, "句子": [], "說話者": "老師",
                  "文字判斷": "老師", "聲音判斷": "老師", "換人依據": "人工改時間後剩下的句子", "老師點名": None,
                  "信心": None, "內容類型": "其他", "文字學員編號": None, "原文": "", "校對稿": "",
                  "已確認": False, "校對秒數": None}
            ts.append(to)
        to["句子"] = (to["句子"] + give) if at_end else (give + to["句子"])
        changed.add(id(to))
    out = []
    for t in ts:
        if id(t) in changed:
            ids = [i for i in t["句子"] if i in sent]
            if not ids and t is not me:
                continue          # 句子全被拿走的段落刪掉
            text = "".join(sent[i]["text"] for i in ids)
            if text != t.get("原文", ""):
                if t.get("校對稿", "") == t.get("原文", ""):
                    t["校對稿"] = text
                t["已確認"] = False
            t["原文"] = text
            if t is me:
                t["start"], t["end"] = round(start, 3), round(end, 3)
            elif ids:
                t["start"], t["end"] = sent[ids[0]]["start"], sent[ids[-1]]["end"]
        out.append(t)
    return sorted(out, key=lambda t: t["start"])


def set_voice(workdir: str | Path, person: str | None, choice: str) -> dict:
    """`POST /api/review/voice`：學員重新生成或保留原聲；person 給 None／「全部」時一鍵全部切換。"""
    if choice not in VOICE_CHOICES:
        raise ValueError(f"只能選：{'、'.join(VOICE_CHOICES)}")
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    with _lock:
        dec = load_decisions(workdir)
        if person in (None, "", "全部"):
            people = (wd.read_json(turns_mod.turns_path(workdir), default={}) or {}).get("學員", {})
            for p in people:
                dec["學員聲音"][p] = choice
        else:
            dec["學員聲音"][person] = choice
        _save_decisions(workdir, dec)
        return {"ok": True, "學員聲音": dec["學員聲音"]}


def add_time(workdir: str | Path, seconds: float) -> dict:
    """`POST /api/review/time`：累加覆核花的時間（單次最多 5 分鐘）。"""
    workdir = Path(workdir)
    with _lock:
        dec = load_decisions(workdir)
        dec["覆核秒數"] = round(float(dec.get("覆核秒數") or 0.0) + max(0.0, min(float(seconds), MAX_TIME_STEP_S)), 1)
        _save_decisions(workdir, dec)
        return {"ok": True, "覆核秒數": dec["覆核秒數"]}


def esc(s: str) -> str:
    return html.escape(s or "")
