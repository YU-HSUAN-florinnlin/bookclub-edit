"""第 3 步「覆核工作台」的資料與存檔（09-25 宇軒定案：原本第 3、4 步合成一步）。

畫面（`bookclub/web/review.js`）：原片播放器 → 時間軸 → 整體設定（學員換成名冊上的誰、
重新生成／保留原聲）→ 待處理清單（學員段落、老師提到名字、重疊、刪除段落、局部消音，照時間排）。
老師的段落不逐段看，只出現在時間軸上。

決定存在哪裡（寫進 `docs/工作區格式.md`）：
- 學員段落、學員換成誰：沿用 `校對/段落.json`（`bookclub/turns.py` 的存檔函式）
- 老師提到名字：沿用 `名字覆核決定.json`，加 `做法`、`已確認` 欄位（`bookclub/nameplan.py` 讀）
- 重疊、刪除段落、局部消音、學員保留原聲、覆核花的時間：`覆核/覆核決定.json`（這支模組管）

純函式為主（不碰 socket），`bookclub/server.py` 只負責轉手。
"""

from __future__ import annotations

import html
import threading
from datetime import datetime
from pathlib import Path

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
    for k, v in (("重疊", {}), ("刪除段落", []), ("局部消音", []), ("學員聲音", {}), ("覆核秒數", 0.0)):
        data.setdefault(k, v)
    return data


def _save_decisions(workdir: Path, data: dict) -> None:
    wd.write_json(review_path(workdir), data)


def overlap_id(o: dict) -> str:
    """重疊的 id 用起點時間（重跑找重疊、筆數變了也對得上同一處）。"""
    return f"O{o['start']:.2f}"


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


def _names_items(workdir: Path, sents: list[dict]) -> list[dict]:
    from bookclub import nameplan
    from bookclub.namespage import _highlight_sentence

    result = wd.read_json(wd.names_path(workdir), default=None) or {}
    decisions = wd.read_json(name_decisions_path(workdir), default={}) or {}
    ordered = sorted(sents, key=lambda s: s["start"])
    pos = {s["id"]: k for k, s in enumerate(ordered)}
    items = []
    for i, c in enumerate(result.get("candidates", []), start=1):
        cid = str(i)
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
        items.append({
            "類型": "名字", "id": cid, "start": c["start"], "end": c["end"],
            "sentence_html": _highlight_sentence(c.get("sentence", ""), c.get("matched_text", ""), c.get("位置", "")),
            "整句": {"start": group[0]["start"], "end": group[-1]["end"], "原文": whole_text, "換成代號": replaced}
            if group else None,
            "matched_text": c.get("matched_text", ""), "代號": c.get("代號", ""), "位置": c.get("位置", ""),
            "信心": c.get("信心", ""), "比對層級": c.get("比對層級", ""), "切點信心": c.get("切點信心", ""),
            "建議做法": c.get("建議做法", ""), "敏感詞": bool(c.get("敏感詞")),
            "做法": d.get("做法") or nameplan.WHOLE, "tags": d.get("tags", []), "note": d.get("note", ""),
            "已確認": bool(d.get("已確認")),
        })
    return items


def roster_words() -> list[str]:
    """名冊上的本名與其他寫法（找「還沒換成代號的本名」用）。"""
    from bookclub import names
    from bookclub.config import data_dir

    return sorted({r["寫法"] for r in names.load_roster(data_dir() / "名冊.csv") if len(r["寫法"]) >= 2},
                  key=len, reverse=True)


def has_real_name(text: str, words: list[str]) -> bool:
    return any(w in (text or "") for w in words)


def page_data(workdir: str | Path, video: str | Path | None = None) -> dict:
    """`GET /api/review`：覆核工作台一次要的全部資料。"""
    from bookclub import overlap as overlap_mod
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    speakers = wd.read_json(wd.speakers_path(workdir), default={}) or {}
    sents = speakers.get("sentences", [])
    merged = wd.read_json(wd.merged_transcript_path(workdir), default={}) or {}
    analysis = wd.read_json(wd.analysis_result_path(workdir), default={}) or {}
    duration = analysis.get("影片長度") or merged.get("duration") or (sents[-1]["end"] if sents else 0)

    tdata = turns_mod.page_data(workdir)
    has_turns = not tdata.get("尚未準備")
    turns = tdata.get("段落", []) if has_turns else []
    people = tdata.get("學員", {}) if has_turns else {}
    dec = load_decisions(workdir)
    for name, p in people.items():
        p["聲音"] = dec["學員聲音"].get(name, "重新生成")

    # 時間軸色帶：老師（灰）、每位學員、冥想導讀（淡藍）
    bands = [{"start": t["start"], "end": t["end"], "說話者": t["說話者"],
              "冥想導讀": t.get("內容類型") in CALM_KINDS, "id": t["id"]} for t in turns]
    if not bands and sents:   # 還沒有段落分析：用逐句判斷畫
        bands = [{"start": s["start"], "end": s["end"], "說話者": "老師" if s.get("label") == "老師" else "學員?",
                  "冥想導讀": False, "id": s["id"]} for s in sents]

    words = roster_words()
    items: list[dict] = []
    for k, t in enumerate(turns):
        if t["說話者"] == "老師":
            continue
        items.append({"類型": "學員段落", "id": t["id"], "序": k + 1, "start": t["start"], "end": t["end"],
                      "說話者": t["說話者"], "校對稿": t["校對稿"], "原文": t["原文"], "已確認": t["已確認"],
                      "問老師": bool(t.get("問老師")), "問老師備註": t.get("問老師備註", ""),
                      "內容類型": t.get("內容類型"), "換人依據": t.get("換人依據"),
                      "學員是猜的": bool(t.get("學員是猜的")), "含本名": has_real_name(t["校對稿"], words)})
    items += _names_items(workdir, sents)

    ov = wd.read_json(wd.overlap_path(workdir), default=None)
    skipped = []
    if ov is not None:
        overlap_mod.apply_simple_filters(ov)   # 只在記憶體裡套，不改檔
        for o in ov.get("overlaps", []):
            oid = overlap_id(o)
            d = dec["重疊"].get(oid, {})
            base = {"id": oid, "start": o["start"], "end": o["end"], "length": o["length"],
                    "角色": [s["role"] for s in o.get("speakers", [])]}
            if o.get("已自動跳過") and not d.get("救回"):
                skipped.append({**base, "原因": o.get("原因")})
                continue
            defaults = _overlap_defaults(o, sents, turns)
            items.append({"類型": "重疊", **base, **defaults,
                          "老師文字": d.get("老師文字", defaults["老師文字"]),
                          "學員文字": d.get("學員文字", defaults["學員文字"]),
                          "學員說話者": d.get("學員說話者", defaults["學員說話者"]),
                          "做法": d.get("做法"), "排法": d.get("排法", OVERLAP_ARRANGE[0]),
                          "備註": d.get("備註", ""), "已確認": bool(d.get("已確認")), "救回": bool(d.get("救回"))})
    for c in dec["刪除段落"]:
        items.append({"類型": "刪除段落", **c, "已確認": True})
    for m in dec["局部消音"]:
        items.append({"類型": "局部消音", **m, "已確認": True})
    items.sort(key=lambda x: (x["start"], x["類型"]))

    return {
        "workdir": str(workdir),
        "影片": {"有影片": video_path(workdir, video) is not None, "網址": "/api/video",
                 "檔名": (video_path(workdir, video) or Path("")).name, "長度": duration},
        "有段落": has_turns,
        "色帶": bands,
        "學員": people,
        "代號選項": tdata.get("代號選項", []),
        "項目": items,
        "已自動跳過的重疊": skipped,
        "選項": {"重疊": OVERLAP_HOWS, "重疊排法": OVERLAP_ARRANGE, "名字": NAME_HOWS, "名字標記": NAME_TAGS,
                 "消音": MUTE_WAYS, "聲音": VOICE_CHOICES},
        "進度": progress(items, dec, duration),
    }


def progress(items: list[dict], dec: dict, duration: float) -> dict:
    """已確認／總數、覆核花的時間、推算整支要多久（純函式）。"""
    total = len(items)
    done = sum(1 for x in items if x.get("已確認"))
    spent = float(dec.get("覆核秒數") or 0.0)
    est = spent / done * total if done else None
    by_type: dict[str, list[int]] = {}
    for x in items:
        c = by_type.setdefault(x["類型"], [0, 0])
        c[1] += 1
        c[0] += bool(x.get("已確認"))
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
        idx = int(cid) - 1
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
