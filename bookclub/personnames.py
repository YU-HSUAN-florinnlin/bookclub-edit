"""影片分析：找出這一集提到的所有人名（09-29 宇軒：老師自己檢查第一集時，太細的名字沒抓到，要盡可能全部抓出來）。

為什麼要有：第 1 步「找名字」（`names.py`）只比對**名冊上**的名字——名冊沒寫的名字（沒登記的學員、家人、朋友、
暱稱、英文名）完全不會被發現。第一堂實測：段落分析記下的「老師點名」有 5 個不同的名字，4 個不在名冊上。

做法（跟「建議刪除段落」同一套）：
1. 整支逐字稿交給另一個 Claude 呼叫（`claude -p`），每一行標出是老師還是學員說的，請它列出**所有**人名：
   本名、暱稱、英文名、姓氏加稱呼；同一個人的不同寫法合在一起；標出這個人是誰（學員、老師本人、書裡的人物或作者、
   其他人如家人朋友同事、不確定）
2. 對照名冊：名冊上有的標出本名與代號；沒有的列成「名冊上沒有的名字」，第 3 步讓人決定換成哪個代號或不用處理
3. 每個名字記下出現在哪幾句、老師說了幾次、學員說了幾次

結果存 `校對/人名清單.json`（存在就不再呼叫 Claude，`force` 重跑）。Claude 失敗就沒有清單，不擋其他步驟。
名字只寫進檔案，終端機只印筆數。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from bookclub import workdir as wd

KINDS = ("學員", "老師本人", "書中人物或作者", "其他人", "不確定")
NEED_CODE_KINDS = ("學員", "其他人", "不確定")   # 名冊上沒有、又是這幾種 → 第 3 步要人決定換成哪個代號

PROMPT = """你在協助剪輯一支讀書會的 Zoom 錄影，要把學員的個資去識別化。請找出逐字稿裡**所有**出現的人名，一個都不要漏。

要找的：
- 學員、老師的名字（全名、只叫名字、只叫姓、暱稱、疊字小名、英文名、「小○」「阿○」「○姐」「○哥」「○老師」這類稱呼）
- 學員提到的家人、朋友、同事、主管的名字
- 書裡的人物、作者、名人（也要列，標成「書中人物或作者」）
逐字稿是語音轉文字，名字常被轉成同音字或拆開，請依前後文判斷（例如老師點名「○○你要不要分享」）。
一般詞語（例如「大家」「同學」「老師」「媽媽」「朋友」）不是名字，不要列。

下面是逐字稿，每行格式：「行號|說話者|時間|文字」，說話者是「老師」或「學員」（聲音辨識的結果，可能有錯）。

{lines}

只輸出 JSON，不要其他文字，格式：
{{"人名": [{{"名字": "最常見的寫法", "其他寫法": ["逐字稿裡其他寫法"], "是誰": "學員|老師本人|書中人物或作者|其他人|不確定",
  "行號": [出現的行號], "說明": "一句話說明判斷依據"}}]}}
"""


def people_path(workdir: Path) -> Path:
    return Path(workdir) / "校對" / "人名清單.json"


def format_lines(sentences: list[dict]) -> str:
    """逐字稿 → 給 Claude 看的行（純函式）。說話者照聲音辨識的 label（改過段落的以段落為準，由呼叫端處理）。"""
    out = []
    for i, s in enumerate(sentences):
        who = "老師" if s.get("label") == "老師" else "學員"
        m, sec = divmod(int(s["start"]), 60)
        h, m = divmod(m, 60)
        out.append(f"{i}|{who}|{h}:{m:02d}:{sec:02d}|{s['text']}")
    return "\n".join(out)


def parse_reply(text: str, n_lines: int) -> list[dict]:
    """Claude 的回覆 → 人名清單（純函式）。行號超出範圍的丟掉，沒有行號的名字不要。"""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("Claude 的回覆裡找不到 JSON")
    raw = json.loads(m.group(0)).get("人名", [])
    out = []
    for p in raw:
        name = str(p.get("名字") or "").strip()
        lines = sorted({int(x) for x in p.get("行號", []) if str(x).lstrip("-").isdigit() and 0 <= int(x) < n_lines})
        if not name or not lines:
            continue
        kind = p.get("是誰") if p.get("是誰") in KINDS else "不確定"
        alts = [a.strip() for a in p.get("其他寫法", []) if isinstance(a, str) and a.strip() and a.strip() != name]
        out.append({"名字": name, "其他寫法": sorted(set(alts)), "是誰": kind, "行號": lines,
                    "說明": str(p.get("說明") or "")[:120]})
    return out


def match_roster(people: list[dict], roster: list[dict]) -> None:
    """對照名冊（就地加 `名冊本名`、`名冊代號`；純函式）。名字或任何一個其他寫法等於名冊上的寫法就算。"""
    lookup = {r["寫法"]: r for r in roster}
    for p in people:
        hit = next((lookup[w] for w in [p["名字"], *p["其他寫法"]] if w in lookup), None)
        p["名冊本名"] = hit["canonical"] if hit else None
        p["名冊代號"] = hit["代號"] if hit else None


def summarize(people: list[dict], sentences: list[dict]) -> None:
    """每個名字：句子 id、第一次出現的時間、老師說幾次、學員說幾次（就地加；純函式）。"""
    for p in people:
        ss = [sentences[i] for i in p["行號"]]
        p["句子"] = [s["id"] for s in ss]
        p["第一次"] = round(min(s["start"] for s in ss), 2)
        p["次數"] = len(ss)
        p["老師說"] = sum(1 for s in ss if s.get("label") == "老師")
        p["學員說"] = p["次數"] - p["老師說"]
    people.sort(key=lambda p: (-p["次數"], p["第一次"]))


def find_people(workdir: str | Path, *, model: str | None = None, log=print, force: bool = False,
                call=None) -> dict:
    """找這一集提到的所有人名，寫 `校對/人名清單.json`。已經有就直接讀（`force` 重跑）。call 可以注入假的（測試用）。"""
    from bookclub import names
    from bookclub.config import data_dir, load_settings

    workdir = Path(workdir).expanduser()
    path = people_path(workdir)
    cached = wd.read_json(path, default=None)
    if cached is not None and not force:
        log(f"[人名清單] 沿用 {path.name}（{len(cached.get('人名', []))} 個名字）")
        return cached
    speakers = wd.read_json(wd.speakers_path(workdir), default={}) or {}
    sentences = speakers.get("sentences") or (wd.read_json(wd.merged_transcript_path(workdir), default={}) or {}).get("sentences", [])
    if not sentences:
        log("[人名清單] 沒有逐字稿，跳過")
        return {"人名": [], "跳過": "沒有逐字稿"}
    if call is None:
        from bookclub.turns import call_claude as call
    model = model or load_settings().claude_models.turns
    t0 = time.time()
    people = None
    for attempt in range(2):   # 失敗重送一次
        try:
            people = parse_reply(call(PROMPT.format(lines=format_lines(sentences)), model), len(sentences))
            break
        except Exception as exc:  # noqa: BLE001
            if attempt:
                raise
            log(f"[人名清單] Claude 第一次失敗，重送：{exc}")
    roster_file = data_dir() / "名冊.csv"
    roster = names.load_roster(roster_file) if roster_file.is_file() else []
    match_roster(people, roster)
    summarize(people, sentences)
    for k, p in enumerate(people, start=1):
        p["id"] = f"P{k:03d}"
    data = {"人名": people, "模型": model, "句數": len(sentences), "耗時秒": round(time.time() - t0, 1),
            "統計": {"名字數": len(people), "名冊上有": sum(1 for p in people if p["名冊本名"]),
                   "名冊上沒有": sum(1 for p in people if not p["名冊本名"]),
                   "各類": {k: sum(1 for p in people if p["是誰"] == k) for k in KINDS}}}
    wd.write_json(path, data)
    st = data["統計"]
    log(f"[人名清單] 完成：{st['名字數']} 個名字（名冊上有 {st['名冊上有']}、沒有 {st['名冊上沒有']}），"
        f"Claude {data['耗時秒']:.0f} 秒 → {path}")
    return data


def called_names(workdir: str | Path) -> list[dict]:
    """第 3 步「學員是誰」本名選單用：這一集被叫到的名字（學員、不確定、其他人），依次數排序。
    名冊上有的用名冊本名（同一人合併次數）。"""
    from bookclub import names
    from bookclub.config import data_dir

    data = wd.read_json(people_path(Path(workdir)), default=None) or {}
    rf = data_dir() / "名冊.csv"
    match_roster(data.get("人名", []), names.load_roster(rf) if rf.is_file() else [])   # 對照最新的名冊（第 3 步可能剛加過）
    merged: dict[str, dict] = {}
    for p in data.get("人名", []):
        if p["是誰"] in ("老師本人", "書中人物或作者"):
            continue
        key = p.get("名冊本名") or p["名字"]
        m = merged.setdefault(key, {"名字": key, "次數": 0, "名冊上有": bool(p.get("名冊本名")), "id": p["id"]})
        m["次數"] += p["次數"]
    return sorted(merged.values(), key=lambda x: -x["次數"])


# ---------- 第 3 步：名冊上沒有的名字 ----------

DECISIONS_FILE = "人名決定.json"          # 校對/人名決定.json：{名字: {做法, 代號, 更新時間}}
HOWS = ("換成代號", "是上面的學員", "不是名字", "不用處理")   # 不用處理：書中人物、公眾人物這類不用去識別化的；是上面的學員：09-29 宇軒（例如逐字稿轉錯字）


def decisions_path(workdir: Path) -> Path:
    return Path(workdir) / "校對" / DECISIONS_FILE


def add_to_roster(name: str, alts: list[str], code: str) -> bool:
    """名冊上沒有的名字加進 `~/讀書會剪輯資料/名冊.csv`（中文名、其他寫法、英文代號）。已經有這個名字就不重寫。"""
    import csv

    from bookclub import names
    from bookclub.config import data_dir

    path = data_dir() / "名冊.csv"
    if any(r["寫法"] == name for r in names.load_roster(path)):
        return False
    new_file = not path.exists()
    if not new_file:
        with open(path, "rb") as f:
            tail = f.read()[-1:]
    with open(path, "a", encoding="utf-8", newline="") as f:
        if not new_file and tail not in (b"\n", b""):
            f.write("\n")
        w = csv.writer(f)
        if new_file:
            w.writerow(["中文名", "其他寫法", "英文代號", "聲線", "性別"])
        w.writerow([name, "、".join(a for a in alts if a != name), code, "", ""])
    return True


def _edit_roster(name: str, change) -> bool:
    """名冊上中文名是 `name` 的那一列交給 `change(row)` 改（其他欄位、順序、BOM 照舊）。"""
    import csv
    import io

    from bookclub.config import data_dir

    path = data_dir() / "名冊.csv"
    if not path.is_file():
        return False
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline="")))
    if not rows:
        return False
    fields = list(rows[0].keys())
    hit = False
    for r in rows:
        if (r.get("中文名") or "").strip() == name:
            change(r)
            hit = True
    if not hit:
        return False
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + buf.getvalue().encode("utf-8"))
    return True


def set_roster_code(name: str, code: str) -> bool:
    """名冊上這個中文名的英文代號改成 `code`。"""
    return _edit_roster(name, lambda r: r.__setitem__("英文代號", code))


def add_roster_alias(real: str, aliases: list[str]) -> bool:
    """名冊上 `real` 那一列的「其他寫法」補上 `aliases`（例如逐字稿轉錯的字）。"""
    def change(r):
        have = [x for x in (r.get("其他寫法") or "").replace(",", "、").split("、") if x.strip()]
        r["其他寫法"] = "、".join(have + [a for a in aliases if a and a != real and a not in have])
    return _edit_roster(real, change)


def rescan_names(workdir: Path) -> int:
    """名冊加了新名字之後，在老師說的句子裡補找（加在名字候選最後，舊編號不變）；保留原聲學員那邊下次打開自動重算。"""
    from bookclub import names
    from bookclub import turns as turns_mod

    speakers = wd.read_json(wd.speakers_path(workdir), default={}) or {}
    tdata = turns_mod.page_data(workdir)
    teacher_ids = {sid for t in tdata.get("段落", []) if t.get("說話者") == "老師" for sid in t.get("句子", [])}
    teacher_ids |= {s["id"] for s in speakers.get("sentences", []) if s.get("label") == "老師"}
    n = names.append_candidates(workdir, sorted(teacher_ids))
    from bookclub import epcodes

    epcodes.sync(workdir)   # 補找到的用名冊代號，換成這一集的
    return n


def unlisted(workdir: str | Path, chosen: set[str] | None = None,
             cuts: list[tuple[float, float]] | None = None) -> list[dict]:
    """第 3 步「這一集提到、名冊上沒有的名字」：名冊上沒有、又不是老師本人的每個名字＋人的決定。
    09-29 宇軒：`chosen`＝「學員是誰」左欄已經選成本名的（代號在右欄定，這裡不再列）；
    `cuts`＝確認刪除的段落，每次出現都在裡面的標「都在刪除段落」（收起來、不用處理）。"""
    workdir = Path(workdir)
    data = wd.read_json(people_path(workdir), default=None) or {}
    dec = wd.read_json(decisions_path(workdir), default={}) or {}
    chosen = chosen or set()
    cuts = cuts or []
    times = {}
    if cuts:
        speakers = wd.read_json(wd.speakers_path(workdir), default={}) or {}
        times = {s["id"]: (s["start"], s["end"]) for s in speakers.get("sentences", [])}

    def in_cut(sid: str) -> bool:
        t = times.get(sid)
        return bool(t) and any(a - 0.3 <= t[0] and t[1] <= b + 0.3 for a, b in cuts)

    out = []
    for p in data.get("人名", []):
        if p.get("名冊本名") or p["是誰"] == "老師本人":
            continue
        if {p["名字"], *p["其他寫法"]} & chosen:
            continue
        d = dec.get(p["名字"], {})
        default = "不用處理" if p["是誰"] == "書中人物或作者" else None
        left = [sid for sid in p.get("句子", []) if not in_cut(sid)]
        out.append({"id": p["id"], "名字": p["名字"], "其他寫法": p["其他寫法"], "是誰": p["是誰"], "說明": p.get("說明", ""),
                    "次數": p["次數"], "老師說": p["老師說"], "學員說": p["學員說"], "第一次": p["第一次"],
                    "刪除段落外次數": len(left) if p.get("句子") else p["次數"],
                    "都在刪除段落": bool(cuts) and bool(p.get("句子")) and not left,
                    "做法": d.get("做法") or default, "代號": d.get("代號"), "同一人": d.get("同一人"),
                    "已決定": bool(d.get("做法"))})
    return out


def decide(workdir: str | Path, name: str, how: str, code: str | None = None, same_as: str | None = None) -> dict:
    """`POST /api/people/decide`：名冊上沒有的名字怎麼處理。換成代號＝加進名冊＋補找老師提到的地方。"""
    from datetime import datetime

    workdir = Path(workdir)
    if how not in HOWS:
        raise ValueError(f"只能選：{'、'.join(HOWS)}")
    data = wd.read_json(people_path(workdir), default=None) or {}
    p = next((x for x in data.get("人名", []) if x["名字"] == name), None)
    if p is None:
        raise KeyError(f"人名清單裡沒有：{name}")
    added = rescanned = 0
    if how == "是上面的學員":   # 09-29 宇軒：其實是「學員是誰」選過的某位（轉錯字、暱稱）→ 用那位的代號，不用再選一次
        from bookclub import names
        from bookclub import turns as turns_mod
        from bookclub.config import data_dir

        if not same_as:
            raise ValueError("要選是上面哪一位學員")
        from bookclub import epcodes

        roster = {r["canonical"]: r["代號"] for r in names.load_roster(data_dir() / "名冊.csv") if r.get("canonical")}
        code = epcodes.episode_codes(workdir).get(same_as)
        if not code:
            raise ValueError(f"「{same_as}」還沒有英文代號：先在「學員是誰」右欄幫他選")
        if same_as in roster:
            add_roster_alias(same_as, [name, *p["其他寫法"]])
        else:
            added = int(add_to_roster(name, p["其他寫法"], code))
        rescanned = rescan_names(workdir)
    elif how == "換成代號":
        code = (code or "").strip()
        if not code:
            raise ValueError("換成代號要選一個英文代號")
        added = int(add_to_roster(name, p["其他寫法"], code))   # 已經在名冊上：不改名冊，這一集的代號記在決定裡（epcodes）
        rescanned = rescan_names(workdir)
    elif how == "不是名字":
        from bookclub.server import _append_exclusion

        for w in [name, *p["其他寫法"]]:
            _append_exclusion(w, "人名清單：宇軒判斷不是名字")
    dec = wd.read_json(decisions_path(workdir), default={}) or {}
    dec[name] = {"做法": how, "代號": code if how in ("換成代號", "是上面的學員") else None,
                 "同一人": same_as if how == "是上面的學員" else None, "更新時間": datetime.now().isoformat(timespec="seconds")}
    wd.write_json(decisions_path(workdir), dec)
    from bookclub import epcodes

    epcodes.sync(workdir)
    return {"ok": True, "名字": name, "做法": how, "加進名冊": bool(added), "補找到的老師名字": rescanned}
