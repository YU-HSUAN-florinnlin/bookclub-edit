"""保留原聲的學員自己講到名字（09-29 宇軒定案，02 規格「保留原聲的學員自己講到名字」）。

學員在第 3 步設成「保留原聲」時，他的話不會重念，講到的名字（自己的、別的同學的）與敏感詞要另外處理：

| 做法 | 第 4 步 |
| --- | --- |
| 直接消音（預設） | 名字那一小段墊環境底噪 |
| 換成代號 | 用這位學員自己的聲音當參考音，重新生成名字所在的短句（起訖照標點與停頓，不斷在句子中間），名字換成代號（`bookclub/studentgen.py`） |

設成「重新生成」的學員不用另外處理（整段照校對稿重念，名字在校對時就換掉）。

找名字沿用 `names.find_names`（比對方法、排除清單、切點精修都一樣），只是改掃保留原聲學員的句子。
檔案（工作區）：
- `學員名字候選.json`：候選（格式同 `名字候選.json`，每筆多 `學員`、`id`），另記 `保留原聲學員`——設定改了就重算
- `學員名字候選/`：試聽小段
- `學員名字覆核決定.json`：{候選 id: {做法, tags, note, 已確認}}；候選 id＝`句子id@開始秒`，重算後不會對錯

名字、句子內容只寫進檔案，不印在終端機。
"""

from __future__ import annotations

from pathlib import Path

from bookclub import workdir as wd

CANDS_FILE = "學員名字候選.json"
CLIP_DIR = "學員名字候選"
DECISIONS_FILE = "學員名字覆核決定.json"
MUTE, CODE = "直接消音", "換成代號"
HOWS = (MUTE, CODE)
SKIP_TAGS = {"不是名字", "是地名"}


def cands_path(workdir: Path) -> Path:
    return Path(workdir) / CANDS_FILE


def decisions_path(workdir: Path) -> Path:
    return Path(workdir) / DECISIONS_FILE


def kept_students(dec: dict) -> list[str]:
    """第 3 步設成保留原聲的學員。"""
    return sorted(k for k, v in (dec.get("學員聲音") or {}).items() if v == "保留原聲")


def sentence_owner(turns: list[dict], kept: list[str]) -> dict[str, str]:
    """句子 id → 保留原聲的學員（只列保留原聲學員的段落裡的句子）。"""
    keep = set(kept)
    return {sid: t["說話者"] for t in turns if t.get("說話者") in keep for sid in t.get("句子", [])}


def cand_id(c: dict) -> str:
    return f"{c.get('sentence_id')}@{c['start']:.2f}"


def find(workdir: str | Path, force: bool = False) -> dict:
    """找保留原聲學員講到的名字；保留原聲的學員跟上次一樣就沿用。沒有保留原聲的學員回傳空結果。"""
    from bookclub import names, review
    from bookclub import turns as turns_mod
    from bookclub.config import data_dir

    import hashlib

    workdir = Path(workdir)
    kept = kept_students(review.load_decisions(workdir))
    tdata = turns_mod.page_data(workdir)
    owner0 = sentence_owner(tdata.get("段落", []), kept) if not tdata.get("尚未準備") else {}
    sig = hashlib.sha1(repr(sorted(owner0.items())).encode()).hexdigest()[:12]   # 段落改了（例如改成老師）也要重算
    cached = wd.read_json(cands_path(workdir), default=None)
    if cached is not None and cached.get("保留原聲學員") == kept and cached.get("段落指紋") == sig and not force:
        return cached
    empty = {"保留原聲學員": kept, "段落指紋": sig, "candidates": [], "統計": {"總筆數": 0}}
    speakers = wd.read_json(wd.speakers_path(workdir), default={}) or {}
    merged = wd.read_json(wd.merged_transcript_path(workdir), default={}) or {}
    roster = data_dir() / "名冊.csv"
    if not kept or tdata.get("尚未準備") or not roster.is_file() or not wd.audio_path(workdir).is_file():
        wd.write_json(cands_path(workdir), empty)
        return empty
    owner = sentence_owner(tdata["段落"], kept)
    sensitive = data_dir() / "敏感詞.csv"
    res = names.find_names(
        wd.audio_path(workdir), workdir, speakers.get("sentences", []), merged.get("words", []), roster,
        sensitive if sensitive.is_file() else None,
        select=lambda s: s.get("id") in owner, cache_path=cands_path(workdir), clip_dir=workdir / CLIP_DIR,
        use_cache=False, annotate=lambda s: {"學員": owner.get(s.get("id"))})
    for c in res["candidates"]:
        c["id"] = cand_id(c)
    res["保留原聲學員"] = kept
    res["段落指紋"] = sig
    wd.write_json(cands_path(workdir), res)
    return res


def _ordered_sentences(workdir: Path) -> list[dict]:
    speakers = wd.read_json(wd.speakers_path(workdir), default={}) or {}
    return sorted(speakers.get("sentences", []), key=lambda s: s["start"])


def whole_sentence(ordered: list[dict], pos: dict, c: dict, owner: dict) -> dict | None:
    """名字所在的短句（照標點擴成完整句子，只接同一位學員的句子），名字換成代號。純函式。"""
    from bookclub import nameplan

    sid = c.get("sentence_id")
    if sid not in pos:
        return None
    who = owner.get(sid)
    group = nameplan.expand_sentence(ordered, pos[sid], same=lambda s: owner.get(s.get("id")) == who)
    texts = {g["id"]: g["text"] for g in group}
    new = nameplan.replace_name(texts[sid], c)
    return {"start": group[0]["start"], "end": group[-1]["end"], "原文": "".join(g["text"] for g in group),
            "換成代號": ("".join(new if g["id"] == sid else texts[g["id"]] for g in group)) if new is not None else None,
            "句子": [g["id"] for g in group]}


def items(workdir: str | Path, cut_ranges: list[tuple[float, float]] = ()) -> list[dict]:
    """第 3 步清單要顯示的「學員提到名字」（只列目前還是保留原聲的學員）。"""
    from bookclub import review
    from bookclub import turns as turns_mod
    from bookclub.namespage import _highlight_sentence

    workdir = Path(workdir)
    res = find(workdir)
    kept = set(res.get("保留原聲學員", []))
    if not res.get("candidates"):
        return []
    decisions = wd.read_json(decisions_path(workdir), default={}) or {}
    tdata = turns_mod.page_data(workdir)
    owner = sentence_owner(tdata.get("段落", []), sorted(kept))
    ordered = _ordered_sentences(workdir)
    pos = {s["id"]: k for k, s in enumerate(ordered)}
    out = []
    for c in res["candidates"]:
        if c.get("學員") not in kept or owner.get(c.get("sentence_id")) != c.get("學員"):
            continue
        d = decisions.get(c["id"], {}) or {}
        out.append({
            "類型": "學員名字", "id": c["id"], "start": c["start"], "end": c["end"], "學員": c.get("學員"),
            "sentence_html": _highlight_sentence(c.get("sentence", ""), c.get("matched_text", ""), c.get("位置", "")),
            "整句": whole_sentence(ordered, pos, c, owner),
            "matched_text": c.get("matched_text", ""), "代號": c.get("代號", ""), "信心": c.get("信心", ""),
            "比對層級": c.get("比對層級", ""), "敏感詞": bool(c.get("敏感詞")),
            "做法": d.get("做法") or MUTE, "tags": d.get("tags", []), "note": d.get("note", ""),
            "已確認": bool(d.get("已確認")),
            "建議": {"做法": MUTE, "原因": f"{c.get('學員')} 保留原聲，講到名字預設直接消音（09-29 定案）；也可以選換成代號，用他自己的聲音生成"},
            "不用處理": "落在確認刪除的段落裡" if review._in_ranges(c["start"], c["end"], list(cut_ranges)) else None,
        })
    return out


def save(workdir: str | Path, cid: str, fields: dict) -> dict:
    """`POST /api/review/stuname`：做法（直接消音／換成代號）、標記（不是名字／是地名）、備註、已確認。"""
    from bookclub.review import NAME_TAGS, _lock, _now

    workdir = Path(workdir)
    with _lock:
        decisions = wd.read_json(decisions_path(workdir), default={}) or {}
        d = decisions.setdefault(str(cid), {"tags": [], "note": ""})
        if "做法" in fields:
            if fields["做法"] not in HOWS:
                raise ValueError(f"學員提到名字的做法只能是：{'、'.join(HOWS)}")
            d["做法"] = fields["做法"]
        if "tags" in fields:
            d["tags"] = [t for t in fields["tags"] if t in NAME_TAGS]
        if "note" in fields:
            d["note"] = str(fields["note"])
        if "已確認" in fields:
            d["已確認"] = bool(fields["已確認"])
        d["更新時間"] = _now()
        wd.write_json(decisions_path(workdir), decisions)
    return {"ok": True, "id": str(cid), "決定": d}


def plan(workdir: str | Path) -> dict:
    """第 4 步要做的：{"消音": [{id, start, end, 學員}], "生成": [{id, 學員, text, 原文, slot, 候選}], "要人處理": [...]}。
    標「不是名字」「是地名」的略過；換成代號但句子裡找不到比對到的字 → 退回直接消音，列在要人處理。"""
    from bookclub import names as names_mod

    workdir = Path(workdir)
    out = {"消音": [], "生成": [], "要人處理": []}
    for it in items(workdir):
        if set(it["tags"]) & SKIP_TAGS:
            continue
        pad = names_mod.DEFAULT_BUFFER_S
        mute = {"id": it["id"], "start": round(it["start"] - pad, 3), "end": round(it["end"] + pad, 3), "學員": it["學員"]}
        if it["做法"] == CODE:
            w = it["整句"]
            if w and w.get("換成代號"):
                out["生成"].append({"id": "SS" + it["id"].split("@")[0].replace("-", "_"), "學員": it["學員"],
                                    "text": w["換成代號"], "原文": w["原文"], "slot": [w["start"], w["end"]],
                                    "候選": [it["id"]], "_cands": [it], "消音備援": [mute]})
                continue
            out["要人處理"].append({"候選": it["id"], "原因": "句子裡找不到比對到的字，換不成代號，先直接消音"})
        out["消音"].append(mute)
    # 同一句裡有兩個名字：合成一筆生成
    from bookclub import nameplan

    merged: dict[tuple, dict] = {}
    for g in out["生成"]:
        k = tuple(g["slot"])
        if k in merged:
            merged[k]["候選"] += g["候選"]
            merged[k]["_cands"] += g["_cands"]
            merged[k]["消音備援"] += g["消音備援"]
        else:
            merged[k] = g
    for g in merged.values():
        if len(g["_cands"]) > 1:   # 從原句重新換，每個名字都換成代號
            text = g["原文"]
            for c in g["_cands"]:
                text = nameplan.replace_name(text, c) or text
            g["text"] = text
        g.pop("_cands")
    out["生成"] = list(merged.values())
    return out
