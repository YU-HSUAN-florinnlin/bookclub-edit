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
import json
import threading
from datetime import datetime
from pathlib import Path

from bookclub import align
from bookclub import workdir as wd

REVIEW_DIR_NAME = "覆核"
NAME_DECISIONS_FILE = "名字覆核決定.json"     # 跟 server.py、nameplan.py 一致

OVERLAP_HOWS = ("不用改", "兩邊都重生成", "只留老師", "只留老師原聲學員消音", "只留學員", "兩邊都不留")
OVERLAP_ARRANGE = ("前後排開", "照原位置疊著")
OVERLAP_LABEL = {"只留老師": "生成老師聲音", "只留學員": "生成學員聲音", "只留老師原聲學員消音": "消音",
                 "兩邊都重生成": "兩邊都重新生成"}   # 畫面上的名稱（跟 review.js 的 RV_HOW_LABEL 一致）
# 第 3 步顯示的選項（09-30 宇軒）：重疊幾乎都是零點幾秒的短回應，學員那邊本來就會跟著整段重念，
# 「兩邊都重生成」「兩邊都不留」不顯示（舊的決定選過的照樣認得）。畫面上的名稱見 review.js 的 RV_HOW_LABEL
OVERLAP_SHOWN = ("不用改", "只留老師", "只留學員", "兩邊都重生成", "只留老師原聲學員消音")   # 10-01：加回「兩邊都重生成」（只做照原本的時間）
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
                 ("刪除建議", {}), ("人工重疊", []), ("人工名字", []), ("已刪除", [])):
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
    """原片路徑：伺服器啟動時帶的 --video → `分析結果.json` → 逐字稿記錄的來源。
    10-02 第五批：存下來的路徑在別的工作區裡（工作區被複製）時，用這個工作區裡的那一份（`wd.find_video`）。"""
    return wd.find_video(Path(workdir), override)



# ---------------------------------------------------------------------------
# 每一筆的建議（09-26：使用者只要看、按通過）
# ---------------------------------------------------------------------------

SHORT_S = 3.0   # 09-29 宇軒：學員發言短於這個秒數，預設建議刪除這段（先試 3 秒）
FILLER_WORDS = ("謝謝老師", "老師好", "老師再見", "大家好", "謝謝", "再見", "拜拜", "晚安", "好的", "沒有",
                "好", "對", "嗯", "恩", "呵", "哈", "喔", "哦", "欸", "啊", "是", "ok", "OK")
_PUNCT = "，。、！？；：,.!?;: …～~﹝﹞()（）「」『』\n\t"


def is_filler(text: str) -> bool:
    """只有招呼、附和、笑聲（純函式）：去掉標點後，全部由 FILLER_WORDS 組成。空字串不算。"""
    t = "".join(ch for ch in (text or "") if ch not in _PUNCT)
    if not t:
        return False
    while t:
        w = next((w for w in FILLER_WORDS if t.startswith(w)), None)
        if not w:
            return False
        t = t[len(w):]
    return True


def is_minor_student(text: str, seconds: float) -> str | None:
    """不重要的學員短句：回傳原因，不是就 None。"""
    if seconds < SHORT_S:
        return f"只有 {seconds:.1f} 秒"
    if is_filler(text):
        return "只有招呼、附和或笑聲"
    return None


def suggest_overlap(o: dict, turns: list[dict], who: str | None, voices: dict, stu_text: str | None = None) -> dict:
    """重疊的預設建議（純函式，規則照 02 規格第三節的定案，減少人的決策）：
    1. 學員是「保留原聲」的人 → 不用改
    2. 一來一往交接（老師收尾、學員開口，或反過來；重疊離換人的地方 1.5 秒內）→ 不用改
       （09-30 宇軒：預設不消音，人確認時聽了覺得學員原聲明顯再選「生成老師聲音」；學員那邊本來就跟著整段重念）
    3. 學員在老師連續講話中間附和（整個重疊落在老師的段落裡）→ 不用改
       （09-30 宇軒聽過成品：消音會讓老師的話斷掉、干擾理解；要消的話在第 3 步逐筆改「只留老師原聲、學員消音」）
    4. 老師在學員說話中間短短回應（整個重疊落在學員的段落裡）→ 只留學員（09-26 加，待宇軒確認）
    5. 判斷不出來 → 不用改（09-30：預設不消音，請人聽過再決定）"""
    if who and voices.get(who) == "保留原聲":
        return {"做法": "不用改", "排法": None, "原因": f"{who} 保留原聲，重疊照原樣"}
    if stu_text is not None:   # 09-29 宇軒：學員只是附和（3 個字以內或聽不出字）→ 不用生成；09-30 改成也不消音
        bare = "".join(ch for ch in stu_text if ch not in _PUNCT)
        if len(bare) <= 3 or is_filler(bare):
            return {"做法": "不用改", "排法": None,
                    "原因": "學員只是附和（3 個字以內或只有嗯、對），照原樣留著：消音會讓老師的話斷掉"}
    ordered = sorted(turns, key=lambda t: t["start"])
    for a, b in zip(ordered, ordered[1:]):
        if (a["說話者"] == "老師") == (b["說話者"] == "老師"):
            continue
        edge = (a["end"] + b["start"]) / 2
        if o["start"] - HANDOVER_S <= edge <= o["end"] + HANDOVER_S:
            how = "老師收尾、學員開口" if a["說話者"] == "老師" else "學員收尾、老師開口"
            return {"做法": "不用改", "排法": None,
                    "原因": f"一來一往交接的地方（{how}）：學員那邊會跟著整段重念，老師這一小段照原樣。"
                            "聽得到學員原聲又覺得明顯，改選「生成老師聲音」"}
    inside = any(t["說話者"] == "老師" and t["start"] <= o["start"] and o["end"] <= t["end"] for t in ordered)
    if inside:
        return {"做法": "不用改", "排法": None,
                "原因": "學員在老師連續講話中間附和（學員那邊短），照原樣留著：消音會讓老師的話斷掉"}
    in_student = any(t["說話者"] != "老師" and t["start"] <= o["start"] and o["end"] <= t["end"] for t in ordered)
    if in_student:   # 09-26 加（待宇軒確認）：第一堂 9 筆都是學員分享中間老師短短回應
        return {"做法": "只留學員", "排法": None,
                "原因": "老師在學員說話中間短短回應（嗯、對），拿掉老師那一小段、學員照常重念"}
    return {"做法": "不用改", "排法": None, "原因": "判斷不出是附和還是交接，先照原樣留著：請聽一下再決定"}


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
    """`POST /api/review/prep`：開始前 4 件事（刪除／學員／名字／保留原聲）哪一件做完了。
    09-30：底下還有沒處理的項目不能標完成（說明原因）；④ 標完成時把每位學員目前的選擇寫下來，
    之後冒出新的學員（拆開、① 改成不刪）才認得出來。"""
    if key not in PREP_KEYS:
        raise ValueError(f"只能是：{'、'.join(PREP_KEYS)}")
    workdir = Path(workdir)
    if done:
        data = page_data(workdir)
        left = data["開始前待處理"].get(key) or []
        if left:
            raise ValueError(f"還不能標完成：{'；'.join(left)}")
        people = [n for n, p in data["學員"].items() if not p.get("都會刪掉")]
    with _lock:
        dec = load_decisions(workdir)
        dec["開始前確認"][key] = bool(done)
        if done and key == "保留原聲":
            for n in people:
                dec["學員聲音"].setdefault(n, "重新生成")
        _save_decisions(workdir, dec)
        return {"ok": True, "開始前確認": dec["開始前確認"]}


PEOPLE_MISSING = ("人名清單沒跑成功（第 1 步用 Claude 找這一集提到的人名）：到第 1 步按「重新分析（做完的會跳過）」，"
                  "或命令列 bookclub run people <工作區>，跑成功之後才能標完成")


def people_list_missing(workdir: Path) -> bool:
    """10-02 第七批（A3）：有逐字稿、卻沒有 `校對/人名清單.json`（Claude 那一步沒跑成功）。"""
    from bookclub import personnames

    return wd.merged_transcript_path(workdir).exists() and not personnames.people_path(workdir).exists()


def _safe(fn, default):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — 算不出來不要擋住工作台
        print(f"⚠️ 第 3 步資料有一項算不出來：{type(exc).__name__}")
        return default


def _code_table(workdir: Path) -> dict:
    """10-07：「代號對照」抽屜的資料（epcodes.code_table）；算不出來給空的，不擋第 3 步。"""
    from bookclub import epcodes

    return _safe(lambda: epcodes.code_table(workdir), {"對照": [], "代號給了": {}, "撞名": {}, "還沒選本名": [], "名單": {}})


def prep_pending(dec: dict, people: dict, suggestions: list[dict], mentioned: list[dict],
                 codes: dict[str, str], people_missing: bool = False) -> dict[str, list[str]]:
    """開始前 4 件事，每一件底下還沒處理的（純函式，09-30）：
    ① 建議刪除段落還沒選刪不刪 ② 學員還沒選本名、選了本名還沒代號 ③ 被提到的名字還沒決定
    ④ 學員還沒選保留原聲或重新生成（標完成後才冒出來的，例如拆開、① 改成不刪）。"""
    out: dict[str, list[str]] = {k: [] for k in PREP_KEYS}
    n = sum(1 for sg in suggestions if not dec["刪除建議"].get(sg["id"], {}).get("決定"))
    if n:
        out["刪除"].append(f"{n} 段建議刪除還沒選刪除或不刪")
    live = {name: p for name, p in people.items() if not p.get("都會刪掉")}
    no_real = [name for name, p in live.items() if not p.get("本名") and not p.get("本名未知")]
    if no_real:
        out["學員"].append(f"{'、'.join(no_real)} 還沒選本名")
    no_code = sorted({p["本名"] for p in live.values() if p.get("本名") and not (codes.get(p["本名"]) or p.get("代號"))})
    if no_code:
        out["學員"].append(f"{'、'.join(no_code)} 還沒選代號")
    if people_missing:
        out["名字"].append(PEOPLE_MISSING)
    un = [u for u in mentioned if not u.get("已決定") and not u.get("都在刪除段落")]
    if un:
        out["名字"].append(f"{len(un)} 個被提到的名字還沒決定")
    new = [name for name in live if name not in dec["學員聲音"]]
    if new and dec["開始前確認"].get("保留原聲"):
        out["保留原聲"].append(f"{'、'.join(new)} 還沒選保留原聲或重新生成")
    return {k: v for k, v in out.items() if v}


def _mark_all_cut(people: dict, turns: list[dict], dec: dict, suggestions: list[dict]) -> list[tuple[float, float]]:
    """每位學員標 `都會刪掉`（段落全部落在確認剪掉的範圍裡）；回傳會剪掉的範圍（含 ① 選了刪除的建議）。就地改 people。"""
    will_cut = [(c["start"], c["end"]) for c in dec["刪除段落"] if c.get("狀態") != "還原"] + \
        [(sg["start"], sg["end"]) for sg in suggestions if dec["刪除建議"].get(sg["id"], {}).get("決定") == "刪除"]
    for name, p in people.items():
        segs = [t for t in turns if t["說話者"] == name]
        p["都會刪掉"] = bool(segs) and all(_in_ranges(t["start"], t["end"], will_cut) for t in segs)
    return will_cut


def students_pending(workdir: str | Path) -> list[str]:
    """10-08 宇軒（流程簡化）：開始 AI 修改前唯一要擋的人工確認——「開始前 4 件事」② 學員是誰。
    每位出現的學員（不是全部在剪掉的段落裡）都選了本名或「本名未知」、選了本名的有代號；
    「不是學員」的改成老師就不在學員裡。回傳還沒做好的（白話，跟 prep_pending 的 ② 一樣），空的＝可以開始。
    沒有段落分析就回空的（那時 precheck 另外會擋）。理由：沒配代號的學員，AI 重念會念出本名。"""
    from bookclub import epcodes
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    tdata = turns_mod.page_data(workdir)
    if tdata.get("尚未準備"):
        return []
    turns = tdata.get("段落", [])
    people = {k: dict(p) for k, p in tdata.get("學員", {}).items() if p.get("段數")}
    dec = load_decisions(workdir)
    _mark_all_cut(people, turns, dec, load_cut_suggestions(workdir))
    return prep_pending(dec, people, [], [], epcodes.episode_codes(workdir)).get("學員", [])


def _auto_unprep(workdir: Path, pending: dict[str, list[str]]) -> list[str]:
    """讀資料時：已經標完成、底下又冒出沒處理的項目 → 那一件自動改回還沒做。回傳改回的項目。"""
    with _lock:
        dec = load_decisions(workdir)
        back = [k for k in PREP_KEYS if dec["開始前確認"].get(k) and pending.get(k)]
        if back:
            for k in back:
                dec["開始前確認"][k] = False
            _save_decisions(workdir, dec)
    return back


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


STACK = "照原位置疊著"   # 「兩邊都重生成」＋這個排法＝B 方案（10-01）：兩邊各自生成、放回原本的時間，疊到的地方混在一起


def is_stacked(how: str | None, arrange: str | None) -> bool:
    return how == "兩邊都重生成" and arrange == STACK


def overlap_sides(o: dict, d: dict, sents: list[dict], turns: list[dict] | None = None) -> dict:
    """B 方案兩邊各自的起訖（純函式）：人在卡片上改過的優先；沒改過的，老師那邊＝蓋到重疊的老師句子、
    學員那邊＝蓋到重疊的其他句子（前後各最多 3 秒），找不到就用重疊本身的起訖。"""
    near = _overlapping(sents, o["start"] - 0.3, o["end"] + 0.3)

    def span(rows: list[dict]) -> list[float]:
        if not rows:
            return [round(o["start"], 3), round(o["end"], 3)]
        return [round(max(min(r["start"] for r in rows), o["start"] - 3), 3),
                round(min(max(r["end"] for r in rows), o["end"] + 3), 3)]

    stu = span([s for s in near if s.get("label") != "老師"])
    for t in turns or []:   # 10-01：預設的學員那邊不伸進前後的學員段落（那裡會整段重念，兩筆會搶同一段時間）
        if t.get("說話者") not in (None, "老師") and not (t["start"] <= o["start"] and o["end"] <= t["end"]):
            if o["end"] <= t["start"] < stu[1]:
                stu[1] = round(t["start"], 3)
            if stu[0] < t["end"] <= o["start"]:
                stu[0] = round(t["end"], 3)
    return {"老師起訖": d.get("老師起訖") or span([s for s in near if s.get("label") == "老師"]),
            "學員起訖": d.get("學員起訖") or stu}


SHRINK_TOL_S = 0.05   # 改過的起訖比原本抓到的小超過這麼多，才算人把範圍改小


def student_gen_slot(o: dict, d: dict, sents: list[dict], turns: list[dict] | None = None) -> dict:
    """重疊選「生成學員聲音」、又不在學員段落裡時，要換掉的時間格（純函式，10-01 第三批）：
    預設＝學員那一整句（`overlap_sides` 的學員那邊，不伸進前後的學員段落），至少包住重疊本身；
    這幾秒整段換成生成的學員聲音，裡面老師的聲音不保留。

    人改過的照人改的，不自動放大：
    - 卡片上改過「會換掉的範圍」（存在 `學員起訖`，跟兩邊都重新生成共用同一欄）→ 照填的
    - 以前用「改時間」改過重疊的起訖（`改過的起訖`），而且比原本抓到的小（任一端往內縮）→ 照改過的起訖
      （改大、或跟原本一樣＝只是把預設值存下來，照預設的學員整句，會包住改過的範圍）
    回傳 {start, end, 來源: 學員整句｜你改過的範圍｜你改小的重疊時間}。"""
    if d.get("學員起訖"):
        a, b = d["學員起訖"]
        return {"start": round(a, 3), "end": round(b, 3), "來源": "你改過的範圍"}
    orig = o.get("原本起訖")
    if orig and (o["start"] > orig[0] + SHRINK_TOL_S or o["end"] < orig[1] - SHRINK_TOL_S):
        return {"start": round(o["start"], 3), "end": round(o["end"], 3), "來源": "你改小的重疊時間"}
    a, b = overlap_sides(o, {}, sents, turns)["學員起訖"]
    return {"start": round(min(a, o["start"]), 3), "end": round(max(b, o["end"]), 3), "來源": "學員整句"}


def teacher_in_slot(a: float, b: float, sents: list[dict], turns: list[dict]) -> list[dict]:
    """[a, b] 裡老師的句子（純函式，10-01 第三批）：卡片上寫「這幾秒裡老師的話會不見」用。
    算老師的：逐句判成老師的；或落在老師的段落裡、逐句也沒判成「不是老師」的（判成不是老師的多半就是要重念的學員那一句，
    跟卡片上「老師說的」「學員說的」初稿同一個分法）。每句帶 `範圍外`（超出 [a, b] 的部分）。"""
    from bookclub import nameplan

    by_turn = nameplan.teacher_by_turns(turns)
    out = []
    for s in sorted(sents, key=lambda x: x["start"]):
        teacher = s.get("label") == "老師" or (by_turn(s) and s.get("label") != "不是老師")
        if min(s["end"], b) - max(s["start"], a) <= 0.05 or not teacher:
            continue
        outside = [[round(x, 3), round(y, 3)] for x, y in ((s["start"], a), (b, s["end"])) if y - x > 0.05]
        out.append({"start": s["start"], "end": s["end"], "text": s.get("text", ""), "範圍外": outside})
    return out


def overlap_choice(o: dict, d: dict, sents: list[dict], turns: list[dict], voices: dict) -> dict:
    """這一處重疊最後照哪個做法（組裝用）：人選的優先，沒選就照建議——跟覆核工作台顯示的是同一套算法。
    `d` 是這一筆的覆核決定。回傳 {做法, 排法, 學員}。"""
    defaults = _overlap_defaults(o, sents, turns)
    who = d.get("學員說話者", defaults["學員說話者"])
    sug = suggest_overlap(o, turns, who, voices, d.get("學員文字", defaults["學員文字"]))
    return {"做法": d.get("做法") or sug["做法"], "排法": d.get("排法") or sug.get("排法"), "學員": who}


# 10-04 #111：人在卡片上做過這些事的重疊，照樣出卡（不自動處理）。做法選「不用改」「只留學員」（第 3 步在學員段落裡的建議）
# 跟自動處理的結果一樣（學員那邊本來就整段重念），照樣自動處理；決定留在 覆核決定.json 裡不動，救回時卡片照原樣回來。
OVERLAP_SAME_AS_AUTO = ("不用改", "只留學員")
OVERLAP_HUMAN_KEYS = ("改過的起訖", "備註", "老師整句改稿")


def overlap_has_own_decision(d: dict) -> bool:
    """這一處重疊人自己做過會影響結果的決定（純函式）：選了別的做法、改過時間、寫了備註、改了老師整句。"""
    return bool((d.get("做法") and d["做法"] not in OVERLAP_SAME_AS_AUTO)
                or any(d.get(k) for k in OVERLAP_HUMAN_KEYS))


def mark_student_turn_overlaps(ov: dict, dec: dict, turns: list[dict], sents: list[dict],
                               kept: set | None = None) -> list[dict]:
    """10-04 #111（宇軒定做法 A）：落在會整段重念的學員段落裡、兩邊都沒有老師的重疊，在記憶體裡標成已自動跳過
    （`原因`＝固定說法、`自動處理`＝「學員段落」、`學員段落`＝那一段的 id），跟其他自動跳過的一樣：不出卡、
    不算進要處理的筆數、組裝與排生成計畫都不另外處理（學員那一段整段重念就蓋掉了），第 3 步「設定」可以救回。
    不寫檔（重疊.json、覆核決定.json 都不動）。判斷見 `overlap.student_turn_home`；人自己做過決定的不動
    （`overlap_has_own_decision`）。`kept`：保留原聲的學員，沒給就照覆核決定。回傳這次標上的那幾筆。"""
    from bookclub import overlap as overlap_mod

    if kept is None:
        kept = {k for k, v in (dec.get("學員聲音") or {}).items() if v == "保留原聲"}
    by_id = {s["id"]: s for s in sents if s.get("id") is not None}
    out = []
    for o in ov.get("overlaps", []):
        if o.get("已自動跳過"):
            continue
        if overlap_has_own_decision(dec["重疊"].get(overlap_id(o), {})):
            continue
        t = overlap_mod.student_turn_home(o, turns, by_id, kept)
        if t is None:
            continue
        o.update({"已自動跳過": True, "原因": overlap_mod.STUDENT_TURN_REASON,
                  "自動處理": overlap_mod.STUDENT_TURN_TAG, "學員段落": t["id"]})
        out.append(o)
    if out:
        ovs = ov.get("overlaps", [])
        ov["已自動跳過數"] = sum(1 for o in ovs if o.get("已自動跳過"))
        ov["要人決定數"] = len(ovs) - ov["已自動跳過數"]
    return out


def load_overlaps(workdir: Path, dec: dict, turns: list[dict], sents: list[dict] | None = None,
                  kept: set | None = None) -> dict:
    """讀 重疊.json（沒有就是空的）＋不用重跑的過濾規則（邊界誤差、#111 學員段落裡的），只在記憶體裡套，不改檔。"""
    from bookclub import overlap as overlap_mod

    ov = wd.read_json(wd.overlap_path(Path(workdir)), default=None) or {"overlaps": []}
    ov.setdefault("overlaps", [])
    overlap_mod.apply_simple_filters(ov)
    if turns:
        if sents is None:
            sents = (wd.read_json(wd.speakers_path(Path(workdir)), default={}) or {}).get("sentences", [])
        mark_student_turn_overlaps(ov, dec, turns, sents, kept)
    return ov


def student_turn_overlaps(workdir: Path, dec: dict, turns: list[dict]) -> list[dict]:
    """只讀：#111 自動處理（學員段落裡、兩邊都沒有老師）而且沒被救回的重疊 [{id, start, end, length, 學員段落}]，照時間排。
    開始前總檢查「請看一眼」用。"""
    ov = load_overlaps(Path(workdir), dec, turns)
    out = []
    for o in ov["overlaps"]:
        oid = overlap_id(o)
        if o.get("自動處理") == "學員段落" and not dec["重疊"].get(oid, {}).get("救回"):
            out.append({"id": oid, "start": o["start"], "end": o["end"], "length": o.get("length", o["end"] - o["start"]),
                        "學員段落": o.get("學員段落")})
    return sorted(out, key=lambda x: x["start"])


def overlap_choices(workdir: str | Path, voices: dict | None = None) -> list[dict]:
    """每一處要處理的重疊（自動跳過、沒救回的不算）最後照哪個做法：[{id, start, end, 做法, 學員, 老師整句改稿}]。
    組裝（`render.build_decisions`）與排老師生成計畫（`nameplan.compute_plan`）共用，兩邊看到的一定一樣。
    `voices`：學員聲音設定，沒給就讀覆核決定（測試「保留原聲的也照樣換」時傳 {}）。"""
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    dec = load_decisions(workdir)
    tdata = turns_mod.page_data(workdir)
    turns = tdata.get("段落", []) if not tdata.get("尚未準備") else []
    sents = (wd.read_json(wd.speakers_path(workdir), default={}) or {}).get("sentences", [])
    voices = dec["學員聲音"] if voices is None else voices
    # 10-04 #111：學員段落裡、兩邊都沒有老師的重疊自動處理（跟第 3 步不出卡是同一個判斷）
    ov = load_overlaps(workdir, dec, turns, sents, kept={k for k, v in voices.items() if v == "保留原聲"})
    out = []
    for o in effective_overlaps(workdir, ov.get("overlaps", []), dec):   # 含覆核時人工補的、改過時間的
        oid = overlap_id(o)
        d = dec["重疊"].get(oid, {})
        if o.get("已自動跳過") and not d.get("救回"):
            continue
        ch = overlap_choice(o, d, sents, turns, voices)
        defaults = _overlap_defaults(o, sents, turns)
        gs = student_gen_slot(o, d, sents, turns)   # 10-01 第三批：生成學員聲音（不在學員段落裡）換掉的範圍
        out.append({"id": oid, "start": o["start"], "end": o["end"], "做法": ch["做法"], "排法": ch["排法"],
                    "學員生成起訖": [gs["start"], gs["end"]], "學員生成來源": gs["來源"],
                    "學員": ch["學員"], "學員已選": bool(d.get("學員說話者")),   # 人在卡片上選的（不是猜的）
                    "老師整句改稿": d.get("老師整句改稿", ""), "已確認": bool(d.get("已確認")),
                    "老師文字": d.get("老師文字", defaults["老師文字"]) or "",
                    "學員文字": d.get("學員文字", defaults["學員文字"]) or "",
                    **overlap_sides(o, d, sents, turns)})
    return out


def coverers(workdir: Path, dec: dict, turns: list[dict]) -> list[dict]:
    """會把原聲換掉的範圍（10-01 7-4）：學員重念的時間格、老師重念的範圍、剪掉的片段。
    每一筆帶 `已通過`（蓋住別人的那一筆自己通過了，被蓋住的才算處理好）與畫面上的名稱。"""
    from bookclub import nameplan, students

    out = []
    tmap = {t["id"]: t for t in turns}
    try:
        items, _ = students.build_items(Path(workdir))
    except FileNotFoundError:
        items = []
    for it in items:
        a, b = it["slot"]
        if it.get("重疊"):
            out.append({"類型": "學員重念", "id": it["id"], "名稱": f"重疊 {wd.fmt_time(a)} 的學員那一句", "start": a, "end": b,
                        "已通過": bool(dec["重疊"].get(it["重疊"], {}).get("已確認")), "重疊": it["重疊"]})
        else:
            out.append({"類型": "學員段落", "id": it["段落"], "名稱": f"學員段落 {wd.fmt_time(tmap.get(it['段落'], it).get('start', a))}",
                        "start": a, "end": b, "已通過": bool(tmap.get(it["段落"], {}).get("已確認"))})
    try:
        plan = nameplan.compute_plan(Path(workdir))
    except Exception:  # noqa: BLE001 — 排不出老師的計畫（例如還沒有名字候選）：只看學員段落與剪掉的片段
        plan = {"生成": []}
    nd = effective_name_decisions(Path(workdir))   # 10-03 補修：一張卡通過，同一句同代號的每一處都算通過
    for g in plan["生成"]:
        ok = all((nd.get(str(c)) or {}).get("已確認") for c in g.get("候選", [])) \
            and all(dec["重疊"].get(o, {}).get("已確認") for o in g.get("重疊項目", []))
        out.append({"類型": "老師重念", "id": g["id"], "名稱": f"老師重念 {wd.fmt_time(g['slot'][0])}",
                    "start": g["slot"][0], "end": g["slot"][1], "已通過": ok, "重疊項目": g.get("重疊項目", [])})
    for c in dec["刪除段落"]:
        if c.get("狀態") != "還原":
            out.append({"類型": "剪掉的片段", "id": c["id"], "名稱": f"剪掉的片段 {wd.fmt_time(c['start'])}",
                        "start": c["start"], "end": c["end"], "已通過": True})
    return out


def overlap_cover(o: dict, oid: str, covs: list[dict]) -> tuple[dict | None, dict | None]:
    """這一處重疊被哪一筆涵蓋（純函式）：(已通過的那一筆, 還沒通過的那一筆)。"""
    from bookclub import assemble

    done = assemble.find_cover(o["start"], o["end"], [k for k in covs if k["已通過"]], oid)
    wait = None if done else assemble.find_cover(o["start"], o["end"], covs, oid)
    return done, wait


def covered_overlaps(workdir: Path, dec: dict, turns: list[dict]) -> list[dict]:
    """只讀：哪幾處重疊自動算處理好（被已通過的那一筆涵蓋）。開始前總檢查用（不寫任何檔）。"""
    ov = load_overlaps(Path(workdir), dec, turns)   # 10-04 #111：學員段落裡自動處理的另外列（student_turn_overlaps），這裡不算
    covs = coverers(Path(workdir), dec, turns)
    out = []
    for o in effective_overlaps(Path(workdir), ov.get("overlaps", []), dec):
        oid = overlap_id(o)
        if o.get("已自動跳過") and not dec["重疊"].get(oid, {}).get("救回"):
            continue
        done, _wait = overlap_cover(o, oid, covs)
        if done:
            out.append({"id": oid, "start": o["start"], "end": o["end"], "涵蓋": _cover_info(done)})
    return out


def item_index(workdir: str | Path, dec: dict | None = None, turns: list[dict] | None = None) -> dict[str, dict]:
    """覆核項目的鍵 → 畫面上看得到的名稱與第 3 步那一張卡片（10-01 1-5：總檢查、成品檢查不用內部編號）。只讀。

    鍵兩種寫法都收：處理紀錄的 `覆核項目`（`學員段落:T003`、`名字:2`、`重疊:O…`、`刪除段落:S1`／`D001`、
    `局部消音:M001`、`學員名字:…`）與第 3 步卡片的鍵（`類型:id`）。每一筆：
    {名稱, 類型, id, start, end, 第3步（卡片的鍵，第 3 步沒有這張卡片時是 None）, 改時間（「改時間」面板的類型，沒有就是 None）}；
    重疊另帶 `疊放`（兩邊都重新生成、照原本的時間），名字另帶 `老師整段`。"""
    from bookclub import overlap as overlap_mod
    from bookclub import studentnames
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    dec = dec if dec is not None else load_decisions(workdir)
    if turns is None:
        tdata = turns_mod.page_data(workdir)
        turns = tdata.get("段落", []) if not tdata.get("尚未準備") else []
    out: dict[str, dict] = {}

    def put(keys: list[str], kind: str, iid: str, name: str, a: float, b: float, card: str | None,
            retime: str | None, **extra) -> None:
        row = {"名稱": name, "類型": kind, "id": iid, "start": round(a, 3), "end": round(b, 3),
               "第3步": card, "改時間": retime, **extra}
        for k in keys:
            out.setdefault(k, row)

    for t in turns:
        key = f"學員段落:{t['id']}"
        if t.get("說話者") == "老師":
            if t.get("說話者是人改的"):
                put([f"改成老師:{t['id']}", key], "改成老師", t["id"], f"改成老師 {wd.fmt_time(t['start'])}",
                    t["start"], t["end"], f"改成老師:{t['id']}", "學員發言")
            continue
        put([key], "學員段落", t["id"], f"學員段落 {wd.fmt_time(t['start'])}", t["start"], t["end"], key, "學員發言")
    ov = load_overlaps(workdir, dec, turns)   # 10-04 #111：學員段落裡自動處理的沒有卡片（第3步＝None）
    try:   # 10-01 第三批：生成學員聲音（不在學員段落裡）換掉的範圍，第 5 步改範圍改的是這個
        gen = {c["id"]: c for c in overlap_choices(workdir)}
        slots = student_slots(workdir)
        gen = {k: c["學員生成起訖"] for k, c in gen.items()
               if not is_stacked(c["做法"], c.get("排法")) and overlap_student_gen(c, slots)}
    except Exception:  # noqa: BLE001 — 算不出來就照以前（改重疊本身的起訖）
        gen = {}
    for o in effective_overlaps(workdir, ov.get("overlaps", []), dec):
        oid = overlap_id(o)
        d = dec["重疊"].get(oid, {})
        shown = not o.get("已自動跳過") or d.get("救回")
        # 重疊卡片自己生成的學員那一句，處理紀錄寫成 `學員段落:<重疊 id>`
        put([f"重疊:{oid}", f"學員段落:{oid}"], "重疊", oid, f"重疊 {wd.fmt_time(o['start'])}", o["start"], o["end"],
            f"重疊:{oid}" if shown else None, "重疊" if shown else None, 疊放=is_stacked(d.get("做法"), d.get("排法")),
            學員生成=gen.get(oid))
    names = wd.read_json(wd.names_path(workdir), default=None) or {}
    ndec = wd.read_json(name_decisions_path(workdir), default={}) or {}
    ncands = effective_name_candidates(workdir, names.get("candidates", []), ndec)
    cards = card_of(ncands)   # 10-03 補修：併進別張卡的（同一處、同一句同代號），第 3 步那一張是主卡
    for i, c in enumerate(ncands, start=1):
        cid = str(c.get("id") or i)
        whole = bool(c.get("老師整段"))
        put([f"名字:{cid}"], "名字", cid, f"{'老師重念' if whole else '老師提到名字'} {wd.fmt_time(c['start'])}",
            c["start"], c["end"], f"名字:{cards.get(cid, cid)}", "名字", 老師整段=whole,
            **({"併進": cards[cid]} if cards.get(cid, cid) != cid else {}))
    for c in dec["刪除段落"]:
        card = f"刪除段落:{c.get('建議id') or c['id']}"
        put([card, f"刪除段落:{c['id']}"], "刪除段落", c.get("建議id") or c["id"], f"剪掉 {wd.fmt_time(c['start'])}",
            c["start"], c["end"], card, "刪除段落")
    for sg in load_cut_suggestions(workdir):
        put([f"刪除段落:{sg['id']}"], "刪除段落", sg["id"], f"剪掉 {wd.fmt_time(sg['start'])}", sg["start"], sg["end"],
            f"刪除段落:{sg['id']}", "刪除段落")
    for m in dec["局部消音"]:
        put([f"局部消音:{m['id']}"], "局部消音", m["id"], f"消音 {wd.fmt_time(m['start'])}", m["start"], m["end"],
            f"局部消音:{m['id']}", "局部消音")
    for c in (wd.read_json(studentnames.cands_path(workdir), default=None) or {}).get("candidates", []):
        put([f"學員名字:{c['id']}"], "學員名字", str(c["id"]), f"學員提到名字 {wd.fmt_time(c['start'])}", c["start"], c["end"],
            f"學員名字:{c['id']}", None)
    return out


def item_name(index: dict[str, dict], key: str) -> str:
    """覆核項目的鍵 → 畫面上的名稱；找不到（例如那一筆後來被刪掉）就只寫類型，不寫內部編號。"""
    if key in index:
        return index[key]["名稱"]
    kind = key.split(":", 1)[0]
    return {"名字": "老師提到名字", "刪除段落": "剪掉", "局部消音": "消音", "學員名字": "學員提到名字"}.get(kind, kind) \
        + "（第 3 步現在找不到這一筆）"


def _cover_info(k: dict) -> dict:
    return {"類型": k["類型"], "id": k["id"], "名稱": k["名稱"], "start": round(k["start"], 3), "end": round(k["end"], 3),
            "重疊項目": k.get("重疊項目") or []}


ALIGN_KEYS = ("標的起訖", "對齊", "對齊到")


def _align_info(d: dict) -> dict:
    """畫面「你標的 → 對齊後」要的欄位（有才帶）。"""
    return {k: d[k] for k in ALIGN_KEYS if k in d}


def name_fingerprint(c: dict) -> str:
    """名字候選的內容指紋：哪一句、幾秒、抓到哪幾個字。候選清單重算、順序變了，靠這個找回同一筆。"""
    return f"{c.get('sentence_id')}|{round(float(c.get('start', 0)), 1)}|{c.get('matched_text', '')}"


SAME_SPOT_TOL_S = 0.05   # 10-03 第八批（#12）：兩筆名字候選起訖都差不到這麼多秒、同一句 → 同一處
_LEVEL_RANK = {"精確": 0, "A1": 1, "A2": 2}


def same_spot_groups(candidates: list[dict], decisions: dict) -> dict[int, int]:
    """10-03 第八批（#12）：同一處（同一句、同一段時間）比中好幾個人的名字候選，合成一張（純函式）。

    名冊同一個寫法出現在兩列、或讀音相近，同一處會比中 2–4 個人、出 2–4 張卡；第一張整句換掉後，後面幾張
    在句子裡找不到比對到的字，按不到通過。每一組留最準的一筆當主卡（精確＞A1＞A2；聽到的字跟寫法一樣的優先；
    再來照順序），其他併進主卡（`也可能是`）。

    已經按過通過的決定不動：組裡已通過的優先當主卡；其他已通過的照舊自己一張，不併。敏感詞不併。
    candidates 用 `名字候選.json` 原本的起訖（不是改過的），回傳 {被併掉的順位（1 起算）: 主卡順位}。"""
    def confirmed(i: int) -> bool:
        return bool((decisions.get(str(i)) or {}).get("已確認"))

    pool = [(i, c) for i, c in enumerate(candidates, start=1)
            if not c.get("敏感詞") and c.get("sentence_id") is not None and "start" in c and "end" in c]
    pool.sort(key=lambda x: (str(x[1]["sentence_id"]), float(x[1]["start"]), x[0]))
    groups: list[list[tuple[int, dict]]] = []
    for i, c in pool:
        g = groups[-1] if groups else None
        if g and g[0][1]["sentence_id"] == c["sentence_id"] \
                and abs(float(g[0][1]["start"]) - float(c["start"])) <= SAME_SPOT_TOL_S \
                and abs(float(g[0][1]["end"]) - float(c["end"])) <= SAME_SPOT_TOL_S:
            g.append((i, c))
        else:
            groups.append([(i, c)])
    out: dict[int, int] = {}
    for g in groups:
        if len(g) < 2:
            continue
        rank = lambda x: (0 if confirmed(x[0]) else 1, _LEVEL_RANK.get(x[1].get("比對層級"), 3),   # noqa: E731
                          0 if x[1].get("matched_text") == x[1].get("name") else 1, x[0])
        main = min(g, key=rank)[0]
        for i, _c in g:
            if i != main and not confirmed(i):
                out[i] = main
    return out


CARD_SYNC_KEYS = ("做法", "tags", "已確認", "整句起訖", "改稿")   # 一張卡的決定套到這一句同代號每一處的欄位


def _decision_sig(d: dict) -> tuple:
    from bookclub import nameplan

    return (d.get("做法") or nameplan.WHOLE, tuple(sorted(d.get("tags") or [])), (d.get("改稿") or "").strip(),
            tuple(d.get("整句起訖") or ()))


def name_card_groups(cands: list[dict], decisions: dict) -> dict[int, dict]:
    """10-03 第八批補修（#12）：同一句（`sentence_id`）、同一個代號的名字候選合成一張卡（純函式）。

    `cands` 是自動抓到的候選（順位＝id，`同一處` 已經標好的不算；敏感詞、沒有代號的不併）。
    同一組裡時間疊在一起（重疊超過 SAME_SPOT_TOL_S）的算「同一處」（同一個名字比中好幾次），其他的是「這一句裡的另一處」。
    主卡＝最早那一處裡最準的一筆（精確＞A1＞A2；聽到的字跟寫法一樣的優先；再照順序；不看有沒有確認過，卡片編號才不會跳）。
    已確認的成員做法不一樣（做法、標記、改稿、重念範圍）→ `分開`（沿用各自的決定，直到人在這張卡上重新按通過）。
    回傳 {主卡順位: {"成員": [...], "處": [[順位...], ...]（每一處第一個是帶頭的）, "分開": bool, "帶頭": 決定的來源順位}}。"""
    def rank(x: tuple[int, dict]) -> tuple:
        return (_LEVEL_RANK.get(x[1].get("比對層級"), 3), 0 if x[1].get("matched_text") == x[1].get("name") else 1, x[0])

    def confirmed(i: int) -> bool:
        return bool((decisions.get(str(i)) or {}).get("已確認"))

    by: dict[tuple, list[tuple[int, dict]]] = {}
    for i, c in enumerate(cands, start=1):
        if c.get("同一處") or c.get("敏感詞") or c.get("人工新增") or c.get("sentence_id") is None \
                or not (c.get("代號") or "").strip() or "start" not in c or "end" not in c:
            continue
        by.setdefault((str(c["sentence_id"]), c["代號"].strip()), []).append((i, c))
    out: dict[int, dict] = {}
    for g in by.values():
        if len(g) < 2:
            continue
        g.sort(key=lambda x: (float(x[1]["start"]), x[0]))
        spots: list[dict] = []
        for i, c in g:
            s = spots[-1] if spots else None
            if s and float(c["start"]) < s["end"] - SAME_SPOT_TOL_S:
                s["成員"].append((i, c))
                s["end"] = max(s["end"], float(c["end"]))
            else:
                spots.append({"成員": [(i, c)], "end": float(c["end"])})
        groups = [[x[0] for x in sorted(s["成員"], key=rank)] for s in spots]
        main = groups[0][0]
        members = [i for i, _c in g]
        sigs = {_decision_sig(decisions.get(str(i)) or {}) for i in members if confirmed(i)}
        lead = main if confirmed(main) else next((i for i in members if confirmed(i)), main)
        out[main] = {"成員": members, "處": groups, "分開": len(sigs) > 1, "帶頭": lead}
    return out


def _mark_name_cards(out: list[dict], decisions: dict) -> None:
    """把 `name_card_groups` 的結果標在候選上（就地改 out 的自動候選）：
    每個成員 `同一張卡`＝主卡 id；主卡帶 `這一句的處`（每一處的起訖、編號）、`決定帶頭`，做法不一樣的帶 `分開決定過`；
    沒有分開決定過的，同一處裡帶頭以外的標 `同一處`（處理計畫照帶頭那一筆處理，不重複換）。"""
    auto = [c for c in out if "id" not in c]
    for main, g in name_card_groups(auto, decisions).items():
        key = str(main)
        for i in g["成員"]:
            out[i - 1] = {**out[i - 1], "同一張卡": key}
        if not g["分開"]:
            for spot in g["處"]:
                for i in spot[1:]:
                    out[i - 1] = {**out[i - 1], "同一處": str(spot[0])}
        spots = []
        for spot in g["處"]:
            cs = [out[i - 1] for i in spot]
            spots.append({"id": str(spot[0]), "候選": [str(i) for i in spot], "start": round(min(float(c["start"]) for c in cs), 3),
                          "end": round(max(float(c["end"]) for c in cs), 3)})
        extra: dict = {"這一句的處": spots, "同一張卡候選": [str(i) for i in g["成員"]], "決定帶頭": str(g["帶頭"])}
        if g["分開"]:
            from bookclub import nameplan

            extra["分開決定過"] = [{"id": str(i), "start": round(float(out[i - 1]["start"]), 3),
                                  "end": round(float(out[i - 1]["end"]), 3),
                                  "做法": (decisions.get(str(i)) or {}).get("做法") or nameplan.WHOLE,
                                  "tags": list((decisions.get(str(i)) or {}).get("tags") or []),
                                  "已確認": bool((decisions.get(str(i)) or {}).get("已確認"))} for i in g["成員"]]
        out[main - 1] = {**out[main - 1], **extra}


def card_decisions(cands: list[dict], decisions: dict) -> dict:
    """10-03 第八批補修（#12）：一張卡的決定套到這一句同代號的每一處（純函式，只在讀的時候算，不寫檔）。
    `cands` 是 `effective_name_candidates` 的結果。沒有分開決定過的組：每個成員的 CARD_SYNC_KEYS 換成帶頭那一筆的
    （帶頭＝主卡已確認就是主卡，不然是第一筆已確認的，都沒有就是主卡）；分開決定過的組照各自的決定。"""
    out = dict(decisions)
    for i, c in enumerate(cands, start=1):
        key = c.get("同一張卡")
        if not key or "id" in c:
            continue
        main = cands[int(key) - 1]
        if main.get("分開決定過"):
            continue
        lead, cid = main.get("決定帶頭") or key, str(i)
        if lead == cid:
            continue
        src = decisions.get(lead) or {}
        own = dict(decisions.get(cid) or {})
        for k in CARD_SYNC_KEYS:
            if k in src:
                own[k] = src[k]
            else:
                own.pop(k, None)
        out[cid] = own
    return out


def effective_name_decisions(workdir: str | Path) -> dict:
    """`名字覆核決定.json`＋一張卡的決定套到同一句同代號的每一處（`card_decisions`）。只讀。"""
    workdir = Path(workdir)
    decisions = wd.read_json(name_decisions_path(workdir), default={}) or {}
    cands = (wd.read_json(wd.names_path(workdir), default=None) or {}).get("candidates", [])
    if not cands:
        return decisions
    return card_decisions(effective_name_candidates(workdir, cands, decisions), decisions)


def card_of(cands: list[dict]) -> dict[str, str]:
    """每一筆名字候選 → 第 3 步顯示它的那一張卡的 id（同一處、同一句同代號併掉的指到主卡）。純函式。"""
    ids = [str(c.get("id") or i) for i, c in enumerate(cands, start=1)]
    by_id = dict(zip(ids, cands))
    out = {}
    for cid, c in zip(ids, cands):
        k, seen = cid, set()
        while k not in seen:
            seen.add(k)
            cc = by_id.get(k) or {}
            nxt = cc.get("同一處") or (cc.get("同一張卡") if cc.get("同一張卡") != k else None)
            if not nxt or nxt not in by_id:
                break
            k = nxt
        out[cid] = k
    return out


def anchor_name_decisions(workdir: str | Path) -> dict:
    """老師名字的覆核決定照「第幾筆」存（09-29 檢查 #6）：`名字候選.json` 整份重算、順序變了，決定會套到別筆。

    每筆決定記下候選的內容指紋；讀的時候指紋對不上，就搬到指紋相同的那一筆；找不到的收進 `_找不到的候選`，
    不套用、也不丟掉。舊的決定沒有指紋：用現在同一個編號的候選補上（還沒重算過，編號是對的）。
    回傳 {"搬動": n, "找不到": n}；沒變就不寫檔。"""
    workdir = Path(workdir)
    cands = (wd.read_json(wd.names_path(workdir), default=None) or {}).get("candidates", [])
    path = name_decisions_path(workdir)
    with _lock:
        decisions = wd.read_json(path, default=None)
        if not decisions or not cands:
            return {"搬動": 0, "找不到": 0}
        fps = {str(i): name_fingerprint(c) for i, c in enumerate(cands, start=1)}
        where = {fp: k for k, fp in fps.items()}
        out, lost, moved, changed = {}, dict(decisions.get("_找不到的候選") or {}), 0, False
        for k, d in decisions.items():
            if k == "_找不到的候選" or not k.isdigit() or not isinstance(d, dict):
                if k != "_找不到的候選":
                    out[k] = d            # 人工補的名字（NM001…）自己有穩定 id，不動
                continue
            fp = d.get("候選指紋")
            if fp is None:
                if k in fps:
                    d = {**d, "候選指紋": fps[k]}
                    changed = True
                out[k] = d
                continue
            if fps.get(k) == fp:
                out[k] = d
            elif fp in where:
                out[where[fp]] = d
                moved += 1
            else:
                lost[fp] = d
        # 之前找不到的，重算後又出現了：搬回來（那一筆還沒有新決定才搬）
        for fp in list(lost):
            if fp in where and where[fp] not in out:
                out[where[fp]] = lost.pop(fp)
                moved += 1
        if lost:
            out["_找不到的候選"] = lost
        if changed or moved or out != decisions:
            wd.write_json(path, out)
    return {"搬動": moved, "找不到": len(lost)}


def effective_name_candidates(workdir: Path, candidates: list[dict], decisions: dict) -> list[dict]:
    """名字候選＋人工補的名字（`人工名字`），改過時間的套上 `改過的起訖`。

    自動抓到的照舊用順位當 id（不帶 `id` 欄位，`nameplan.build_plan` 照舊編號）；人工補的帶 `id`（NM001…），
    這樣重跑找名字、候選變多也不會跟人工補的撞號。對齊過的時間已經留過停頓，建議緩衝歸零。
    `nameplan.compute_plan` 與覆核工作台共用，兩邊看到的名字一樣。"""
    out = []
    merged = same_spot_groups(candidates, decisions)   # 10-03 第八批（#12）：同一處只出一張卡
    for i, c in enumerate(candidates, start=1):
        d = decisions.get(str(i), {}) or {}
        if d.get("改過的起訖"):
            c = {**c, "start": d["改過的起訖"][0], "end": d["改過的起訖"][1], "建議緩衝秒數": 0.0, "改過時間": True}
        if i in merged:
            c = {**c, "同一處": str(merged[i])}
        out.append(c)
    for main in sorted(set(merged.values())):
        alts = [candidates[i - 1] for i in sorted(k for k, v in merged.items() if v == main)]
        c = out[main - 1]
        pick = ((decisions.get(str(main)) or {}).get("選的人") or "").strip()
        chosen = next((a for a in alts if pick and a.get("canonical") == pick and a.get("canonical") != c.get("canonical")), None)
        if chosen:   # 人在卡片上改用「也可能是」的另一位：名字、本名、代號換成那一位的（抓到的字、時間不變）
            alts = [candidates[main - 1]] + [a for a in alts if a is not chosen]
            c = {**c, **{k: chosen.get(k) for k in ("name", "canonical", "代號")}, "選的人": chosen.get("canonical")}
        seen, also = {c.get("canonical")}, []
        for a in alts:
            if a.get("canonical") in seen:
                continue
            seen.add(a.get("canonical"))
            also.append({"canonical": a.get("canonical"), "name": a.get("name"), "代號": a.get("代號", ""),
                         "比對層級": a.get("比對層級", "")})
        out[main - 1] = {**c, "也可能是": also, "同一處候選": [str(k) for k, v in sorted(merged.items()) if v == main]}
    _mark_name_cards(out, decisions)   # 10-03 第八批補修（#12）：同一句、同一個代號只出一張卡
    for m in load_decisions(Path(workdir))["人工名字"]:
        out.append({"id": m["id"], "start": m["start"], "end": m["end"], "sentence_id": m.get("sentence_id"),
                    "sentence": m.get("sentence", ""), "matched_text": m.get("matched_text", ""),
                    "name": m.get("matched_text", ""), "canonical": "", "代號": m.get("代號", ""), "敏感詞": False,
                    "位置": "", "比對層級": "人工", "信心": "", "建議做法": "", "切點信心": "", "建議緩衝秒數": 0.0,
                    "人工新增": True, **_align_info(m),
                    # 09-30：人工標的一段老師的話（不是一個名字）：整段照 `整段文字`（或卡片上改的字）用老師 AI 聲音重念
                    **({"老師整段": True, "整段文字": m.get("整段文字", "")} if m.get("老師整段") else {})})
    return out


def _names_items(workdir: Path, sents: list[dict]) -> list[dict]:
    from bookclub import nameplan
    from bookclub.namespage import _highlight_sentence

    result = wd.read_json(wd.names_path(workdir), default=None) or {}
    decisions = wd.read_json(name_decisions_path(workdir), default={}) or {}
    ordered = sorted(sents, key=lambda s: s["start"])
    pos = {s["id"]: k for k, s in enumerate(ordered)}
    items = []
    table = replace_table(workdir)
    merged = wd.read_json(wd.merged_transcript_path(workdir), default={}) or {}
    words = merged.get("words") or []
    extend = nameplan.extend_enabled(merged)   # 10-04 #62：新做法轉的工作區，重念範圍延伸到標點或停頓
    student_ranges = nameplan.student_name_ranges(workdir) if extend else []   # 延伸不能碰到別的名字
    cands = effective_name_candidates(workdir, result.get("candidates", []), decisions)
    raw_decisions, decisions = decisions, card_decisions(cands, decisions)   # 10-03 補修：一張卡的決定套到同一句同代號每一處
    blocked = set()
    if extend:   # 剪掉的片段跟 nameplan.compute_plan 同一個算法，卡片跟計畫的延伸判斷才會一樣
        cuts = [(x["start"], x["end"]) for x in load_decisions(workdir)["刪除段落"] if x.get("狀態") != "還原"]
        cut = {str(x.get("id") or k) for k, x in enumerate(cands, start=1) if _in_ranges(x["start"], x["end"], cuts)}
        blocked = nameplan.no_extend_groups(cands, decisions, ordered, cut=cut)
    for i, c in enumerate(cands, start=1):
        cid = str(c.get("id") or i)
        if c.get("同一處"):   # 10-03 第八批（#12）：同一處比中好幾個人，併進主卡（主卡寫「也可能是」）
            continue
        if c.get("同一張卡") and c["同一張卡"] != cid:   # 10-03 補修：同一句、同一個代號的另一處，併進主卡
            continue
        d = decisions.get(cid, {}) or {}
        mates = [cands[int(k) - 1] for k in c.get("同一張卡候選") or [] if k != cid and not cands[int(k) - 1].get("同一處")]
        group = nameplan.expand_sentence(ordered, pos[c["sentence_id"]]) if c.get("sentence_id") in pos else []
        if group and c.get("改過時間"):   # 跟 nameplan.build_plan 一樣：改時間納進來的句子一起重念
            group = [g for g in ordered if g["end"] > min(c["start"], group[0]["start"]) + 0.05
                     and g["start"] < max(c["end"], group[-1]["end"]) - 0.05
                     and (g in group or nameplan.ok_teacher(g))] or group
        whole_text = "".join(g["text"] for g in group)
        replaced = None
        if group:
            texts = {g["id"]: g["text"] for g in group}
            new = _replace_each(texts[c["sentence_id"]], c, mates)
            if new is not None:
                texts[c["sentence_id"]] = new
                replaced = "".join(texts[g["id"]] for g in group)
        sentence = c.get("sentence") or (ordered[pos[c["sentence_id"]]]["text"] if c.get("sentence_id") in pos else "")
        whole = None
        if group:   # 10-01：重念範圍跟 nameplan.build_plan 同一個算法（整句太長只重念名字那一小句、人改過的照人改的）
            ext = extend and group[0]["id"] not in blocked   # 問題 D：跟 nameplan.build_plan 同一個判斷
            ws = nameplan.whole_slot(c, d, group, words, nameplan.neighbors(ordered, group) if ext else None,
                                     nameplan.other_ranges(cands, c, student_ranges, ordered) if ext else ())
            if not ws["範圍"] and replaced is None and words and \
                    nameplan.replace_name(nameplan.range_words(words, ws["start"], ws["end"]), c) is not None:
                ws["範圍"] = "逐字"   # 跟 nameplan.build_plan 一樣：句子裡找不到，改用逐字時間的字（17 號 2-7）
            if ws["範圍"]:
                whole_text = nameplan.range_words(words, ws["start"], ws["end"])
                replaced = _replace_each(whole_text, c, mates)
            whole = {"start": ws["start"], "end": ws["end"], "原文": whole_text, "換成代號": replaced,
                     "改稿": d.get("改稿", ""), "範圍": ws["範圍"], "原本整句": ws["整句"]}
        if c.get("老師整段"):
            sentence = c.get("整段文字", "")
            whole = {"start": c["start"], "end": c["end"], "原文": sentence, "換成代號": sentence, "改稿": d.get("改稿", "")}
        if whole:   # 09-30：生成前還會再過一次名冊換代號（`nameplan.compute_plan`）；跟畫面上的字不一樣時讓人看得到
            shown = whole["改稿"] or whole["換成代號"] or ""
            said, _ch = replace_real_names(shown, table)
            whole["實際會念"] = said if shown and said != shown else ""
            src = nameplan.range_words(words, whole["start"], whole["end"]) if words else whole["原文"]
            whole["字數"] = [nameplan.say_count(shown), nameplan.say_count(src)]   # 要念的／這段時間逐字稿的
            whole["字太少"] = bool(shown) and nameplan.too_short(shown, src)
        items.append({
            "類型": "名字", "id": cid, "start": c["start"], "end": c["end"],
            "sentence_html": _highlight_sentence(sentence, c.get("matched_text", ""), c.get("位置", "")),
            "整句": whole, "老師整段": bool(c.get("老師整段")),
            "matched_text": c.get("matched_text", ""), "代號": c.get("代號", ""), "位置": c.get("位置", ""),
            "信心": c.get("信心", ""), "比對層級": c.get("比對層級", ""), "切點信心": c.get("切點信心", ""),
            "建議做法": c.get("建議做法", ""), "敏感詞": bool(c.get("敏感詞")),
            "做法": d.get("做法") or nameplan.WHOLE, "tags": d.get("tags", []), "note": d.get("note", ""),
            "已確認": bool(d.get("已確認")), "人工新增": bool(c.get("人工新增")), **_align_info(c), **_align_info(d),
            **({"也可能是": c["也可能是"], "選的人": c.get("選的人") or "", "本名": c.get("canonical", "")}
               if c.get("也可能是") else {}),
            # 10-03 補修：同一句、同一個代號的每一處（N>1 時卡片寫「這一句裡有 N 處」，每一處可以點去聽）
            **({"這一句的處": c["這一句的處"], "同一張卡候選": c.get("同一張卡候選", [])} if c.get("這一句的處") else {}),
        })
        if c.get("分開決定過"):   # 以前分開決定過、做法不一樣：沿用各自的決定，全部都通過才算這張卡通過
            items[-1]["分開決定過"] = c["分開決定過"]
            items[-1]["已確認"] = all(bool((raw_decisions.get(k) or {}).get("已確認")) for k in c.get("同一張卡候選", []))
    mark_name_covers(items, decisions)
    return items


def _replace_each(text: str, c: dict, mates: list[dict]) -> str | None:
    """名字換成代號，同一張卡的其他幾處（同一句同代號）也一起換（找不到的那一處跳過）；主卡自己的找不到回傳 None。"""
    from bookclub import nameplan

    new = nameplan.replace_name(text, c)
    if new is None:
        return None
    for m in mates:
        new = nameplan.replace_name(new, m) or new
    return new


def mark_name_covers(items: list[dict], decisions: dict) -> None:
    """10-03 第八批（#12）：還沒通過的名字卡，整個落在另一張已通過、整句換掉的卡的重念範圍裡
    → 帶 `涵蓋`（已由那一張處理，不用再按）。那一張改做法或退回（取消通過）時，下次讀就不再帶，回到要處理。
    `nameplan.build_plan` 用同一個規則把這一筆併進那一句（不列「要人處理」）。就地改 items。"""
    from bookclub import nameplan

    def plain_whole(it: dict) -> bool:
        d = decisions.get(str(it["id"]), {}) or {}
        return (d.get("做法") or nameplan.WHOLE) == nameplan.WHOLE and not (set(d.get("tags", [])) & nameplan.SKIP_TAGS)

    done = [k for k in items if k["類型"] == "名字" and k.get("已確認") and k.get("整句") and plain_whole(k)]
    for it in items:
        if it["類型"] != "名字" or it.get("已確認") or it.get("老師整段") or not plain_whole(it):
            continue
        k = next((k for k in done if k is not it and k["整句"]["start"] - SAME_SPOT_TOL_S <= it["start"]
                  and it["end"] <= k["整句"]["end"] + SAME_SPOT_TOL_S), None)
        if k:
            it["涵蓋"] = {"類型": "名字", "id": k["id"], "名稱": f"老師提到名字 {wd.fmt_time(k['start'])}",
                         "start": round(k["整句"]["start"], 3), "end": round(k["整句"]["end"], 3), "重疊項目": []}


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
            # 10-01 第三批：留著原本抓到的起訖（判斷人是把範圍改小還是改大，見 student_gen_slot）
            o = {**o, "id": oid, "start": a, "end": b, "length": round(b - a, 3), "改過時間": True,
                 "原本起訖": [o["start"], o["end"]], **_align_info(d)}
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
    anchor_name_decisions(workdir)   # 09-29：名字候選重算過，決定跟著內容走，不照編號錯位
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
              "冥想導讀": t.get("內容類型") in CALM_KINDS, "id": t["id"],
              "人改成老師": t["說話者"] == "老師" and bool(t.get("說話者是人改的"))} for t in turns]
    if not bands and sents:   # 還沒有段落分析：用逐句判斷畫
        bands = [{"start": s["start"], "end": s["end"], "說話者": "老師" if s.get("label") == "老師" else "學員?",
                  "冥想導讀": False, "id": s["id"]} for s in sents]

    # 刪除段落：建議（第 1 步影片分析產生）＋人手動加的
    suggestions = load_cut_suggestions(workdir)
    cut_ranges = [(c["start"], c["end"]) for c in dec["刪除段落"] if c.get("狀態") != "還原"]

    def skip_reason(a: float, b: float) -> str | None:
        return "落在剪掉的片段裡（聲音和畫面都拿掉）" if _in_ranges(a, b, cut_ranges) else None

    # 09-29 宇軒：只在確認刪除的段落（結尾道別等）裡講話的學員，不用判斷是誰（① 建議刪除段落選了「刪除」才算）
    will_cut = _mark_all_cut(people, turns, dec, suggestions)

    words = roster_words()
    table = replace_table(workdir)
    items: list[dict] = []
    cut_from = {c["來源段落"]: c for c in dec["刪除段落"] if c.get("來源段落") and c.get("狀態") != "還原"}
    from bookclub import turnvoice

    sent_by_id = {s.get("id"): s for s in sents}
    for k, t in enumerate(turns):
        if t["說話者"] == "老師":
            if t.get("說話者是人改的"):   # 10-01 2-3：人改成老師的段落照樣列出來（看得到、改得回來），不擋進度
                items.append({"類型": "改成老師", "id": t["id"], "序": k + 1, "start": t["start"], "end": t["end"],
                              "原文": t.get("原文", ""), "校對稿": t.get("校對稿", ""), "說話者": "老師",
                              "已確認": False, "人工新增": bool(t.get("人工新增")), **_align_info(t),
                              "建議": {"做法": "保留老師原聲", "原因": "你把這一段改成老師：照原聲留著。要改回學員、改時間、"
                                                            "或改成老師重念，在「改做法」裡選"},
                              "不用處理": "改成老師（原聲），不用處理"})
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
                      "代號改過": t.get("代號改過"),   # 09-30：epcodes.propagate 改了代號、改回還沒確認的
                      "建議": {"做法": "通過", "原因": why},
                      "短句保留": bool(t.get("短句保留")),
                      "不用處理": "保留原聲，不用校對逐字稿" if keep else skip_reason(t["start"], t["end"])})
        tv = turnvoice.turn_voice(t, sents, sent_by_id)   # 10-04 #127：聲音多半是老師 → 卡片上提醒（不擋通過）
        if tv and tv["多半是老師"]:
            items[-1]["聲紋多半是老師"] = {**tv, "提醒": turnvoice.warn_text(tv)}
        if t["id"] in cut_from:   # 10-01 2-4：從這一段「通過＝剪掉」產生的剪掉片段，卡片上可以取消
            items[-1]["剪掉的片段"] = cut_from[t["id"]]["id"]
        # 10-01 2-4：人剛新增、剛切出來的段落不建議剪掉（人是特地標的）
        by_hand = t.get("人工新增") or t.get("手動標記") or "人工" in str(t.get("換人依據") or "")
        minor = None if (keep or items[-1]["不用處理"] or t.get("短句保留") or by_hand) \
            else is_minor_student(draft, t["end"] - t["start"])
        if minor:   # 09-29 宇軒：不重要的短句預設剪掉（生成聲音反而花時間）；按通過＝剪掉這段
            items[-1]["建議"] = {"做法": "刪除這段", "原因": f"不重要的短句（{minor}）：預設剪掉（聲音和畫面都拿掉，影片會變短）、"
                                                   "省生成時間；要留下在「改做法」選「學員整句生成」"}
    for it in _names_items(workdir, sents):
        cut_hint = f"；第 1 步切點分析建議：{it['建議做法']}" if it.get("建議做法") and it["建議做法"] != nameplan_whole() else ""
        it["建議"] = {"做法": nameplan_whole(), "原因": "預設整句老師重念、名字換成代號" + cut_hint}
        if it.get("老師整段"):
            it["建議"]["原因"] = "人工標的老師的話：這一段照上面的字老師重念"
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
    covs, cover_seen, cover_gone = None, {}, {}
    if ov is not None:
        from bookclub import nameplan

        ordered_sents = sorted(sents, key=lambda s: s["start"])
        is_teacher = nameplan.teacher_by_turns(turns)
        # 10-01 1-1：選了要生成、但還缺東西（例如學員是誰還沒人選）的，卡片上直接寫「還缺」；跟總檢查同一個判斷
        slots = student_slots(workdir)
        choices = {c["id"]: c for c in overlap_choices(workdir)}
        lacking = {k: overlap_gen_problem(c, slots) for k, c in choices.items()}
        overlap_mod.apply_simple_filters(ov)   # 只在記憶體裡套，不改檔
        # 10-04 #111：學員段落裡、兩邊都沒有老師的重疊不出卡（收到「已自動跳過的重疊」，可以救回）；只在記憶體裡標
        mark_student_turn_overlaps(ov, dec, turns, sents)
        for o in effective_overlaps(workdir, ov.get("overlaps", []), dec):
            oid = overlap_id(o)
            d = dec["重疊"].get(oid, {})
            base = {"id": oid, "start": o["start"], "end": o["end"], "length": o["length"],
                    "角色": [s["role"] for s in o.get("speakers", [])]}
            if o.get("已自動跳過") and not d.get("救回"):
                skipped.append({**base, "原因": o.get("原因"), "自動處理": o.get("自動處理"), "學員段落": o.get("學員段落")})
                continue
            defaults = _overlap_defaults(o, sents, turns)
            who = d.get("學員說話者", defaults["學員說話者"])
            if covs is None:
                covs = coverers(workdir, dec, turns)
            cov, cov_wait = overlap_cover(o, oid, covs)
            if cov:
                cover_seen[oid] = _cover_info(cov)
            elif d.get("涵蓋於"):
                cover_gone[oid] = d["涵蓋於"]
            grp = nameplan.overlap_sentence(o, ordered_sents, is_teacher)   # 選「生成老師聲音」時要重念的老師整句
            items.append({"類型": "重疊", **base, **defaults,
                          "老師整句": {"start": grp[0]["start"], "end": grp[-1]["end"], "原文": "".join(g["text"] for g in grp),
                                   "改稿": d.get("老師整句改稿", "")} if grp else None,
                          "老師文字": d.get("老師文字", defaults["老師文字"]),
                          "學員文字": d.get("學員文字", defaults["學員文字"]),
                          "學員說話者": who,
                          "做法": d.get("做法"), "排法": d.get("排法", OVERLAP_ARRANGE[0]),
                          "備註": d.get("備註", ""), "已確認": bool(d.get("已確認")), "救回": bool(d.get("救回")),
                          "人工新增": bool(o.get("人工新增")), **_align_info(o),
                          "建議": suggest_overlap(o, turns, who, voices, d.get("學員文字", defaults["學員文字"])),
                          "不用處理": skip_reason(o["start"], o["end"]),
                          # 10-01 7-4：整個落在別筆（已通過的）換聲音的範圍裡 → 自動算處理好
                          "涵蓋": cover_seen.get(oid), "涵蓋待通過": _cover_info(cov_wait) if cov_wait else None,
                          "涵蓋消失": cover_gone.get(oid),
                          "學員已選": bool(d.get("學員說話者")),
                          # 10-01 1-1：還沒人選時，選單不直接顯示猜的那一位（看起來像選好了、其實沒存），旁邊寫「程式猜是」
                          "學員猜的": None if d.get("學員說話者") else defaults["學員說話者"],
                          "還缺": lacking.get(oid),
                          # 10-01 第三批：選生成學員聲音、不在學員段落裡 → 會換掉哪幾秒、裡面老師的話會怎樣
                          "生成範圍": gen_range_info(choices.get(oid), slots, sents, turns, covs, oid),
                          **overlap_sides(o, d, sents, turns)})
    if cover_seen or cover_gone:
        _remember_cover(workdir, cover_seen, cover_gone)
    linked = {c["建議id"]: c for c in dec["刪除段落"] if c.get("建議id")}
    # 10-01 第三批：還原的剪掉、消音（建議刪除選了不剪的也算）不列在清單、不算筆數，收到「設定」的「已還原的」，可以救回
    restored: list[dict] = []
    for sg in suggestions:
        d = dec["刪除建議"].get(sg["id"], {})
        c = linked.get(sg["id"], {})     # 確認刪除後改過時間的，照改過的
        row = {"類型": "刪除段落", "id": sg["id"], "start": c.get("start", sg["start"]),
               "end": c.get("end", sg["end"]), "來源": "建議", **_align_info(c),
               "建議類型": sg.get("類型"), "決定": d.get("決定"), "已確認": bool(d.get("決定")),
               "狀態": "還原" if d.get("決定") == "不刪" else "刪除",
               "建議": {"做法": "刪除", "原因": f"{sg.get('類型', '')}：{sg.get('原因', '')}".strip("：")},
               "不用處理": None}
        (restored if d.get("決定") == "不刪" else items).append(row)
    for c in dec["刪除段落"]:
        if c.get("建議id"):
            continue
        row = {"類型": "刪除段落", **c, "來源": "手動", "已確認": True, "人工新增": True, "可以刪": True,
               "建議": {"做法": "刪除" if c.get("狀態") != "還原" else "還原", "原因": "人手動加的"},
               "不用處理": None}
        (restored if c.get("狀態") == "還原" else items).append(row)
    for m in dec["局部消音"]:
        row = {"類型": "局部消音", **m, "已確認": True, "人工新增": True, "可以刪": True,
               "建議": {"做法": m.get("方式", MUTE_WAYS[0]), "原因": "人手動加的"}, "不用處理": None}
        (restored if m.get("狀態") == "還原" else items).append(row)
    for it in items:   # 10-01 第三批：人工新增的漏抓重疊、老師提到名字、老師這一段 AI 重念，加錯了可以直接刪
        if it["類型"] in ("重疊", "名字") and it.get("人工新增"):
            it["可以刪"] = True
    try:   # 10-01 第三批 5：第 5 步退回的，卡片上寫「第 5 步退回：原因」（重做完就消失）
        from bookclub import finalcheck

        back = finalcheck.returned_by_card(workdir, item_index(workdir, dec, turns))
    except Exception:  # noqa: BLE001 — 讀不到成品檢查不擋第 3 步
        back = {}
    for it in items:
        if back.get(f"{it['類型']}:{it['id']}"):
            it["第5步退回"] = back[f"{it['類型']}:{it['id']}"]
    try:   # 10-02 第六批：老師重念範圍前後沒有人講話 → 卡片上提醒＋「照建議縮小」（不自動改）
        from bookclub import silentedge

        edge = silentedge.hints(workdir)
    except Exception:  # noqa: BLE001 — 算不出來不擋第 3 步
        edge = {}
    for it in items:
        if edge.get(f"{it['類型']}:{it['id']}"):
            it["前後沒聲音"] = edge[f"{it['類型']}:{it['id']}"]
    try:   # 10-01 第三批 14：學員段落切短後，句子切在外面、沒被處理蓋到的幾秒：卡片上問是誰的聲音
        from bookclub import execute

        outside = execute.outside_questions(workdir)
    except Exception:  # noqa: BLE001 — 算不出來不擋第 3 步（第 4 步總檢查照樣會列）
        outside = {}
    for it in items:
        if it["類型"] == "學員段落" and outside.get(it["id"]) and not it.get("不用處理"):
            it["段落外"] = outside[it["id"]]
    items.sort(key=lambda x: (x["start"], x["類型"]))
    restored.sort(key=lambda x: x["start"])

    # 09-30：每位要重念的學員用哪個聲線（依男女輪流配；網頁載入不估基頻，性別不知道的寫「開始生成時自動配」）
    try:
        from bookclub import students as students_mod

        live = {n: {**p, "第一次": min((t["start"] for t in turns if t["說話者"] == n), default=0.0)}
                for n, p in people.items() if not p.get("都會刪掉") and p.get("聲音") != "保留原聲"}
        vp = students_mod.voice_page(workdir, live)
        for n, v in vp["每位"].items():
            people[n]["聲線"] = {**{k: v.get(k) for k in ("名稱", "性別", "依據", "人選的")},
                                 "自動配原本": (v.get("自動配原本") or {}).get("名稱")}
        voice_opts = vp["選項"]
    except Exception as exc:  # noqa: BLE001 — 聲線配不出來不要擋住工作台
        print(f"⚠️ 學員聲線配不出來：{exc}")
        voice_opts = {}

    mentioned = _unlisted_names(workdir, {p["本名"] for p in tdata.get("學員", {}).values() if p.get("本名")}, will_cut)
    ep_codes = epcodes.episode_codes(workdir)
    missing_people = people_list_missing(workdir)
    pending = prep_pending(dec, people, suggestions, mentioned, ep_codes, missing_people)
    reverted = _auto_unprep(workdir, pending)
    for k in reverted:
        dec["開始前確認"][k] = False

    return {
        "workdir": str(workdir),
        "影片": {"有影片": video_path(workdir, video) is not None, "網址": "/api/video",
                 "檔名": (video_path(workdir, video) or Path("")).name, "長度": duration},
        "有段落": has_turns,
        "色帶": bands,
        "學員": people,
        "代號選項": tdata.get("代號選項", []),
        "代號分組": tdata.get("代號分組") or {},   # 10-02 第七批：選單分女男、只顯示中文
        "英文代號換中文": _english_codes(workdir),   # 10-02 第七批：這一集還在用的英文代號（有的話 ② 上面顯示「換成中文」）
        "聲線選項": voice_opts,
        "學員資料": {k: tdata.get(k) for k in ("本名選項", "名冊代號", "老師名稱", "本名代號", "這一集的名字")},   # 09-29「學員是誰」兩欄
        "代號重複": _dup_codes(workdir, tdata),
        "這一集代號": ep_codes,   # 09-29：名冊拿掉代號欄，② ③ 顯示用這張
        "代號對照": _code_table(workdir),   # 10-07：頂端「代號對照」抽屜、選單標「已給某某」、撞名警告
        "代號自動配未改過": _safe(lambda: epcodes.auto_unchanged(workdir), {}),
        "代號開始前已配": bool(_safe(lambda: epcodes._load_auto(workdir).get("開始前③已配"), True)),
        "還沒代號": epcodes.missing(workdir),
        "提到的名字": mentioned,
        "名冊重複寫法": _roster_dups(),   # 10-03 第八批（#12）：③ 上面提醒「名冊裡同一個寫法出現在兩列」
        "項目": items,
        "已還原": restored,                         # 10-01 第三批：還原的剪掉、消音（不在清單、不算筆數）
        "已刪除": list(dec.get("已刪除") or []),     # 10-01 第三批：刪掉的人工新增項目（留紀錄）
        "已自動跳過的重疊": skipped,
        "刪除建議": [{**sg, "決定": dec["刪除建議"].get(sg["id"], {}).get("決定")} for sg in suggestions],
        "開始前確認": dec["開始前確認"],
        "開始前待處理": pending,          # 09-30：每一件底下還沒處理的（有的話不能標完成）
        # 10-08 宇軒（流程簡化）：開始 AI 修改只看 ② 學員是誰（①③④ 沒做照預設）；卡片沒看完不擋
        "②還沒做": pending.get("學員") or [],
        "人名清單沒跑成功": missing_people,   # 10-02 第七批（A3）
        "開始前自動改回": reverted,       # 09-30：這次讀資料時因為冒出新項目、自動改回還沒做的
        "選項": {"重疊": OVERLAP_SHOWN, "重疊排法": OVERLAP_ARRANGE, "名字": NAME_HOWS, "名字標記": NAME_TAGS,
                 "學員名字": list(studentnames.HOWS),
                 "消音": MUTE_WAYS, "聲音": VOICE_CHOICES},
        "進度": progress(items, dec, duration),
    }


def _remember_cover(workdir: Path, seen: dict, gone: dict) -> None:
    """記下每一處重疊現在被哪一筆涵蓋（下次讀資料時，那一筆改了時間、做法、被還原 → 看得出「原本涵蓋、現在沒有了」）。
    沒變就不寫檔。涵蓋消失的這一次就清掉記錄（提醒只出現一次；那一筆已經變回還沒確認）。"""
    with _lock:
        dec = load_decisions(workdir)
        changed = False
        for oid, info in seen.items():
            d = dec["重疊"].setdefault(oid, {})
            if d.get("涵蓋於") != info:
                d["涵蓋於"], changed = info, True
        for oid in gone:
            if dec["重疊"].get(oid, {}).pop("涵蓋於", None) is not None:
                changed = True
        if changed:
            _save_decisions(workdir, dec)


def _roster_dups() -> list[dict]:
    """名冊裡同一個寫法出現在兩列以上（列號，標題列算第 1 列）；讀不到就當沒有。"""
    from bookclub import names
    from bookclub.config import data_dir

    try:
        return names.roster_duplicate_spellings(data_dir() / "名冊.csv")
    except Exception:  # noqa: BLE001 — 提醒算不出來不擋工作台
        return []


def _dup_codes(workdir: Path, tdata: dict) -> dict:
    """這一集有出現的人（選成本名的學員＋老師講到的名字）裡，兩個以上用同一個代號的。"""
    from bookclub import epcodes

    here = {p["本名"] for p in tdata.get("學員", {}).values() if p.get("本名")}
    here |= {c.get("canonical") for c in (wd.read_json(wd.names_path(workdir), default={}) or {}).get("candidates", [])
             if c.get("canonical") and not c.get("敏感詞")}
    return epcodes.duplicates(workdir, here)


def _english_codes(workdir: Path) -> list[dict]:
    from bookclub import codeswap

    try:
        return codeswap.plan(workdir)["英文代號"]
    except Exception as exc:  # noqa: BLE001 — 列不出來不要擋住工作台
        print(f"⚠️ 英文代號列不出來：{exc}")
        return []


def _unlisted_names(workdir: Path, chosen: set[str] | None = None, cuts: list | None = None) -> list[dict]:
    from bookclub import personnames

    try:
        return personnames.mentioned(workdir, chosen, cuts)
    except Exception as exc:  # noqa: BLE001 — 人名清單壞掉不要擋住工作台
        print(f"⚠️ 人名清單讀不到：{exc}")
        return []


def nameplan_whole() -> str:
    from bookclub import nameplan

    return nameplan.WHOLE


def progress(items: list[dict], dec: dict, duration: float) -> dict:
    """已確認／總數、覆核花的時間、推算整支要多久（純函式）。"""
    total = len(items)
    done = sum(1 for x in items if (x.get("已確認") or x.get("不用處理") or x.get("涵蓋")) and not x.get("還缺"))
    spent = float(dec.get("覆核秒數") or 0.0)
    passed = sum(1 for x in items if x.get("已確認") and not x.get("不用處理"))
    need = sum(1 for x in items if not x.get("不用處理"))
    est = spent / passed * need if passed else None
    by_type: dict[str, list[int]] = {}
    for x in items:
        c = by_type.setdefault(x["類型"], [0, 0])
        c[1] += 1
        c[0] += bool((x.get("已確認") or x.get("不用處理") or x.get("涵蓋")) and not x.get("還缺"))   # 10-01 走查：跟總數同一個算法
    return {"已確認": done, "總數": total, "已花秒數": round(spent), "推算全部秒數": round(est) if est else None,
            "各類": {k: {"已確認": v[0], "總數": v[1]} for k, v in by_type.items()}}


# ---------------------------------------------------------------------------
# 10-08 宇軒（流程簡化）：第 3 步「全部照建議通過」
# ---------------------------------------------------------------------------

# 一鍵通過不幫忙按、要人自己看的原因（鍵 → 摘要裡的白話）。順序＝摘要裡列的順序
BULK_SKIP = (
    ("名字換不了代號", "名字換不了代號（不處理的話成品會照原聲念出本名）"),
    ("還有本名", "要念的文字裡還有本名"),
    ("代號改過", "代號改過，請再看一次"),
    ("重疊缺資料", "重疊還缺東西（學員是誰或文字）"),
    ("字太少", "要念的字太少（這一段其他的話會不見）"),
    ("學員是猜的", "學員是程式猜的（這段太短，聽一下是誰）"),
    ("建議剪掉", "建議剪掉這段（會連畫面一起剪掉）"),
    ("沒有建議", "沒有建議的做法"),
    ("分開決定過", "同一句的名字以前分開決定過"),
    ("人工新增", "人工新增的"),
    ("第5步退回", "第 5 步退回的"),
)
BULK_SKIP_TEXT = dict(BULK_SKIP)


def item_done(it: dict) -> bool:
    """跟網頁 rvDone 同一個判斷：已確認、不用處理、被別筆涵蓋，而且沒有還缺東西。"""
    return bool(it.get("已確認") or it.get("不用處理") or it.get("涵蓋")) and not it.get("還缺")


def bulk_skip_reason(it: dict, stuck: set[str], words: list[str]) -> str | None:
    """這一張卡片能不能一鍵照建議通過（純函式）。回傳不能的原因鍵（BULK_SKIP），可以就回傳 None。
    stuck＝名字換不了代號的候選編號（nameplan.compute_plan 的 要人處理）；words＝名冊上的本名寫法。"""
    t = it.get("類型")
    if it.get("第5步退回"):
        return "第5步退回"
    if it.get("人工新增") or it.get("手動標記") or it.get("老師整段"):
        return "人工新增"
    if it.get("代號改過"):
        return "代號改過"
    if it.get("還缺"):
        return "重疊缺資料"
    sug = (it.get("建議") or {}).get("做法")
    if t == "學員段落":
        if sug == "刪除這段" and not it.get("短句保留"):
            return "建議剪掉"
        if it.get("含本名") or has_real_name(it.get("建議稿") or "", words):
            return "還有本名"
        if it.get("學員是猜的"):   # 10-08 審查：猜錯的話用錯的人的聲線、代號
            return "學員是猜的"
        return None
    if t == "名字":
        ids = {str(it.get("id"))} | {str(k) for k in it.get("同一張卡候選") or []}
        if ids & stuck:
            return "名字換不了代號"
        if it.get("分開決定過"):
            return "分開決定過"
        whole = it.get("整句")
        if it.get("做法") != "直接消音":
            if not whole:
                return "名字換不了代號"
            if whole.get("字太少"):
                return "字太少"
            said = whole.get("實際會念") or whole.get("改稿") or whole.get("換成代號") or ""
            if has_real_name(said, words):
                return "還有本名"
        return None
    if t == "學員名字":
        return None if it.get("做法") else "沒有建議"
    if t == "重疊":
        return None if (it.get("做法") or sug) else "沒有建議"
    if t == "刪除段落":
        return "建議剪掉"
    return "沒有建議"


def bulk_pass_split(items: list[dict], stuck: set[str], words: list[str]) -> tuple[list[dict], list[tuple[dict, str]]]:
    """（純函式）還沒處理好的卡片分成：一鍵可以通過的、要人自己看的（附原因鍵）。處理好的不列。"""
    ok, left = [], []
    for it in items:
        if item_done(it):
            continue
        why = bulk_skip_reason(it, stuck, words)
        (left.append((it, why)) if why else ok.append(it))
    return ok, left


def bulk_summary(passed: int, left: list[tuple[dict, str]]) -> dict:
    """一鍵通過之後跳出來的摘要（純函式）：{通過, 還要看, 依類型: [{原因, 說明, 筆數}], 說明}。"""
    count: dict[str, int] = {}
    for _it, why in left:
        count[why] = count.get(why, 0) + 1
    kinds = [{"原因": k, "說明": text, "筆數": count[k]} for k, text in BULK_SKIP if count.get(k)]
    text = f"通過了 {passed} 張"
    if left:
        text += f"，還有 {len(left)} 張要看：" + "；".join(f"{x['說明']} {x['筆數']} 張" for x in kinds)
        text += "。這些不擋「開始 AI 修改」，沒看的照目前的設定做"
    return {"通過": passed, "還要看": len(left), "依類型": kinds, "說明": text}


def pass_all(workdir: str | Path) -> dict:
    """`POST /api/review/passall`：把有建議、還沒確認的卡片一次照建議通過（不幫忙按的見 bulk_skip_reason）。
    每一類各寫一次檔；通過之後再檢查一次（名字換不了代號、重疊還缺東西），有的話改回沒通過、算進要人看。"""
    from bookclub import nameplan
    from bookclub import studentnames
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    data = page_data(workdir)
    words = roster_words()
    try:
        stuck = {str(m["候選"]) for m in nameplan.compute_plan(workdir)["要人處理"]}
    except Exception:  # noqa: BLE001 — 排不出計畫：名字卡都不幫忙按
        stuck = {str(it["id"]) for it in data["項目"] if it["類型"] == "名字"}
    ok, left = bulk_pass_split(data["項目"], stuck, words)
    by_type: dict[str, list[dict]] = {}
    for it in ok:
        by_type.setdefault(it["類型"], []).append(it)

    if by_type.get("學員段落"):
        with turns_mod._lock:
            tdata = wd.read_json(turns_mod.turns_path(workdir))
            want = {it["id"]: it for it in by_type["學員段落"]}
            for t in tdata["段落"]:
                it = want.get(t["id"])
                if it is not None:
                    t["校對稿"] = str(it.get("建議稿") or t.get("校對稿") or "").strip()
                    t["已確認"] = True
                    t.pop("代號改過", None)
            wd.write_json(turns_mod.turns_path(workdir), tdata)

    name_ids: list[str] = []
    if by_type.get("名字"):
        cands = (wd.read_json(wd.names_path(workdir), default=None) or {}).get("candidates", [])
        with _lock:
            decisions = wd.read_json(name_decisions_path(workdir), default={}) or {}
            eff = {str(c.get("id") or i): c for i, c in enumerate(
                effective_name_candidates(workdir, cands, decisions), start=1)} if cands else {}
            for it in by_type["名字"]:
                cid = str(it["id"])
                me = eff.get(cid) or {}
                mates = [str(k) for k in me.get("同一張卡候選") or [] if str(k) != cid] if me.get("同一張卡") == cid else []
                src = decisions.get(str(me.get("決定帶頭") or cid)) or {}
                base = {k: json.loads(json.dumps(src[k], ensure_ascii=False)) for k in CARD_SYNC_KEYS if k in src}
                base.update({"做法": it.get("做法") or nameplan.WHOLE, "已確認": True})
                for k in [cid] + mates:
                    dk = decisions.setdefault(k, {"tags": [], "note": ""})
                    for key in CARD_SYNC_KEYS:
                        if key in base:
                            dk[key] = json.loads(json.dumps(base[key], ensure_ascii=False))
                    if k.isdigit() and 1 <= int(k) <= len(cands):
                        dk["候選指紋"] = name_fingerprint(cands[int(k) - 1])
                    dk["更新時間"] = _now()
                    name_ids.append(k)
            wd.write_json(name_decisions_path(workdir), decisions)

    if by_type.get("學員名字"):
        with _lock:
            sdec = wd.read_json(studentnames.decisions_path(workdir), default={}) or {}
            for it in by_type["學員名字"]:
                d = sdec.setdefault(str(it["id"]), {"tags": [], "note": ""})
                d["做法"] = it.get("做法") or studentnames.MUTE
                d["已確認"] = True
                d["更新時間"] = _now()
            wd.write_json(studentnames.decisions_path(workdir), sdec)

    if by_type.get("重疊"):
        with _lock:
            dec = load_decisions(workdir)
            for it in by_type["重疊"]:
                how = it.get("做法") or (it.get("建議") or {}).get("做法")
                d = dec["重疊"].setdefault(str(it["id"]), {})
                d["做法"] = how
                d["排法"] = "照原位置疊著" if how == "兩邊都重生成" else (it.get("排法") or (it.get("建議") or {}).get("排法") or OVERLAP_ARRANGE[0])
                d["已確認"] = True
                d["更新時間"] = _now()
            _save_decisions(workdir, dec)

    # 通過之後再檢查一次：名字換不了代號、重疊還缺東西 → 改回沒通過（跟逐筆按通過時擋下來的一樣）
    back: list[tuple[dict, str]] = []
    if name_ids:
        try:
            now_stuck = {str(m["候選"]) for m in nameplan.compute_plan(workdir)["要人處理"]}
        except Exception:  # noqa: BLE001
            now_stuck = set()
        bad = [it for it in by_type["名字"] if ({str(it["id"])} | {str(k) for k in it.get("同一張卡候選") or []}) & now_stuck]
        if bad:
            ids = set()
            for it in bad:
                ids |= {str(it["id"])} | {str(k) for k in it.get("同一張卡候選") or []}
            with _lock:
                decisions = wd.read_json(name_decisions_path(workdir), default={}) or {}
                for k in ids & set(name_ids):
                    decisions.setdefault(k, {"tags": [], "note": ""})["已確認"] = False
                wd.write_json(name_decisions_path(workdir), decisions)
            back += [(it, "名字換不了代號") for it in bad]
    if by_type.get("重疊"):
        slots = student_slots(workdir)
        choices = {c["id"]: c for c in overlap_choices(workdir)}
        bad = [it for it in by_type["重疊"] if choices.get(str(it["id"])) and overlap_gen_problem(choices[str(it["id"])], slots)]
        if bad:
            with _lock:
                dec = load_decisions(workdir)
                for it in bad:
                    dec["重疊"].setdefault(str(it["id"]), {})["已確認"] = False
                _save_decisions(workdir, dec)
            back += [(it, "重疊缺資料") for it in bad]
    return {"ok": True, **bulk_summary(len(ok) - len(back), left + back)}


# ---------------------------------------------------------------------------
# 存檔
# ---------------------------------------------------------------------------

def save_name(workdir: str | Path, cid: str, fields: dict) -> dict:
    """`POST /api/review/name`：做法、標記（不是名字／是地名／切點削到旁邊的字）、備註、已確認。
    標「不是名字」「是地名」時，跟舊的名字覆核頁一樣把抓到的字加進排除清單。

    10-03 補修（#12）：同一句、同一個代號合成一張卡的，決定（CARD_SYNC_KEYS）照樣每筆候選各存一份，
    一起寫進這一句同代號的每一筆（`同一張卡候選`）。以前分開決定過、做法不一樣的：只改主卡，
    等人在這張卡上按通過時才全部統一成主卡的決定。`選的人` 只改主卡（代號變了，下次讀會重新分組）。"""
    workdir = Path(workdir)
    cid = str(cid)
    anchor_name_decisions(workdir)
    cands = (wd.read_json(wd.names_path(workdir), default=None) or {}).get("candidates", [])
    eff = effective_name_candidates(workdir, cands, wd.read_json(name_decisions_path(workdir), default={}) or {}) \
        if cands else []
    me = next((c for i, c in enumerate(eff, start=1) if str(c.get("id") or i) == cid), None) or {}
    mates = [k for k in me.get("同一張卡候選") or [] if k != cid] if me.get("同一張卡") == cid else []
    split = bool(me.get("分開決定過"))
    sync = bool(mates) and (not split or bool(fields.get("已確認")))
    lead = me.get("決定帶頭") or cid
    with _lock:
        decisions = wd.read_json(name_decisions_path(workdir), default={}) or {}
        d = decisions.setdefault(cid, {"tags": [], "note": ""})
        if sync and not split and lead != cid:   # 卡片顯示的是帶頭那一筆（已確認的）的決定：先對齊，再套這次改的
            src = decisions.get(lead) or {}
            for k in CARD_SYNC_KEYS:
                if k in src:
                    d[k] = src[k]
                else:
                    d.pop(k, None)
        if cid.isdigit() and 1 <= int(cid) <= len(cands):
            d["候選指紋"] = name_fingerprint(cands[int(cid) - 1])
        if "做法" in fields:
            if fields["做法"] not in NAME_HOWS:
                raise ValueError(f"名字的做法只能是：{'、'.join(NAME_HOWS)}")
            d["做法"] = fields["做法"]
        was_not_name = {k: any(t in ("是地名", "不是名字") for t in (decisions.get(k) or {}).get("tags", []))
                        for k in [cid] + (mates if sync else [])}
        if "tags" in fields:
            d["tags"] = [t for t in fields["tags"] if t in NAME_TAGS]
        if "note" in fields:
            d["note"] = str(fields["note"])
        if "已確認" in fields:
            d["已確認"] = bool(fields["已確認"])
        if "整句起訖" in fields:   # 10-01：名字卡片上改重念範圍（空＝照預設）
            v = fields["整句起訖"]
            if v:
                a, b = float(v[0]), float(v[1])
                if b - a < 0.3:
                    raise ValueError("重念範圍的結束要晚於開始")
                d["整句起訖"] = [round(a, 3), round(b, 3)]
            else:
                d.pop("整句起訖", None)
        if "選的人" in fields:   # 10-03 第八批（#12）：同一處比中好幾個人，人選是哪一位（空白＝回到自動選的）
            who = str(fields["選的人"] or "").strip()
            if who:
                d["選的人"] = who
            else:
                d.pop("選的人", None)
        if "改稿" in fields:   # 09-29：要重念的句子人直接改（空白＝回到自動換好的）
            txt = str(fields["改稿"] or "").strip()
            if txt:
                d["改稿"] = txt
            else:
                d.pop("改稿", None)
        d["更新時間"] = _now()
        synced = [cid]
        if sync:   # 10-03 補修：同一句同代號的每一筆都存同一份決定（各自帶自己的候選指紋）
            for k in mates:
                dm = decisions.setdefault(k, {"tags": [], "note": ""})
                for key in CARD_SYNC_KEYS:
                    if key in d:
                        dm[key] = json.loads(json.dumps(d[key], ensure_ascii=False))
                    else:
                        dm.pop(key, None)
                if k.isdigit() and 1 <= int(k) <= len(cands):
                    dm["候選指紋"] = name_fingerprint(cands[int(k) - 1])
                dm["更新時間"] = d["更新時間"]
                synced.append(k)
        wd.write_json(name_decisions_path(workdir), decisions)

    def undo(key: str, value=None) -> None:
        with _lock:
            dd = wd.read_json(name_decisions_path(workdir), default={}) or {}
            for k in synced:
                if key == "已確認":
                    dd.setdefault(k, {"tags": [], "note": ""})["已確認"] = False
                else:
                    dd.get(k, {}).pop(key, None)
            wd.write_json(name_decisions_path(workdir), dd)

    if fields.get("整句起訖"):
        now = effective_name_candidates(workdir, cands, wd.read_json(name_decisions_path(workdir), default={}) or {})
        a, b = d["整句起訖"]
        for c in [c for i, c in enumerate(now, start=1) if str(c.get("id") or i) in synced and not c.get("同一處")]:
            if not (a <= c["start"] + 0.05 and c["end"] - 0.05 <= b):
                undo("整句起訖")
                many = "（這一句同一個名字的每一處都要包住）" if len(synced) > 1 else ""
                raise ValueError(f"重念範圍要包住名字（{wd.fmt_time(c['start'])}–{wd.fmt_time(c['end'])}）{many}")
    if fields.get("已確認"):
        # 09-30：按了通過，但這一筆其實處理不了（句子裡找不到名字、換不了代號）→ 成品會照原聲念出名字。擋下來
        from bookclub import nameplan

        plan = nameplan.compute_plan(workdir)
        stuck = next((m for m in plan["要人處理"] if str(m["候選"]) in synced), None)
        if not stuck:   # 10-01：要念的字比那段時間逐字稿少太多 → 這一段其他的話會不見
            for g in [g for g in plan["生成"] if set(synced) & {str(x) for x in g.get("候選", [])}]:
                if nameplan.too_short(g["text"], words_text(workdir, *g["slot"])):
                    stuck = {"原因": f"要念的字（{nameplan.say_count(g['text'])} 字）不到重念範圍逐字稿"
                                     f"（{nameplan.say_count(words_text(workdir, *g['slot']))} 字）的一半，這一段其他的話會不見。"
                                     "請把重念範圍改小，或把話補齊"}
                    break
        if stuck:
            undo("已確認")
            raise ValueError(f"這一筆還不能通過：{stuck['原因']}。先在卡片上把「老師 AI 聲音要重念的句子」改好（名字寫成代號），"
                             "或在「改做法」選直接消音。不處理的話，成品會照原聲念出名字。")
    added = removed = False
    for k in synced:   # 10-03 補修：同一張卡的每一筆各自把抓到的字加進／拿出排除清單
        dk = (wd.read_json(name_decisions_path(workdir), default={}) or {}).get(k) or {}
        is_not_name = any(t in ("是地名", "不是名字") for t in dk.get("tags", []))
        if "tags" not in fields or is_not_name == was_not_name.get(k, False):
            continue
        from bookclub.server import _append_exclusion, _remove_exclusion

        idx = int(k) - 1 if k.isdigit() else -1     # 人工補的名字（NM001…）不在候選清單裡
        term = cands[idx].get("matched_text") if 0 <= idx < len(cands) else None
        a_k = r_k = False
        if is_not_name and term:
            a_k = _append_exclusion(term, "、".join(t for t in dk["tags"] if t in ("是地名", "不是名字")))
        elif not is_not_name:   # 09-30：取消「不是名字」，連排除清單一起拿掉（不然以後這個寫法永遠抓不到）
            r_k = _remove_exclusion(dk.get("排除的詞") or term or "")
        with _lock:
            decisions = wd.read_json(name_decisions_path(workdir), default={}) or {}
            dd = decisions.setdefault(k, dk)
            if a_k:
                dd["排除的詞"] = term
            elif r_k or not is_not_name:
                dd.pop("排除的詞", None)
            wd.write_json(name_decisions_path(workdir), decisions)
        if k == cid:
            d = dd
        added, removed = added or a_k, removed or r_k
    return {"ok": True, "id": cid, "決定": d, "已加入排除清單": added, "已從排除清單拿掉": removed,
            **({"一起存的": synced[1:]} if len(synced) > 1 else {})}


def save_overlap(workdir: str | Path, oid: str, fields: dict) -> dict:
    """`POST /api/review/overlap`：做法、排法（兩邊都重生成時）、兩邊文字、老師整句改稿、學員是誰、備註、已確認、救回。"""
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
        if "老師整句改稿" in fields:   # 09-30：選「生成老師聲音」時要重念的句子人直接改（空白＝照逐字稿）
            txt = str(fields["老師整句改稿"] or "").strip()
            if txt:
                d["老師整句改稿"] = txt
            else:
                d.pop("老師整句改稿", None)
        for k in ("老師起訖", "學員起訖"):   # 10-01 B 方案：兩邊各自的起訖（空＝照預設）
            if k in fields:
                v = fields[k]
                if v:
                    a, b = float(v[0]), float(v[1])
                    if b <= a:
                        raise ValueError(f"{k}：結束要晚於開始")
                    d[k] = [round(a, 3), round(b, 3)]
                else:
                    d.pop(k, None)
        if fields.get("回到預設範圍"):   # 10-01 第三批：生成學員聲音會換掉的範圍回到預設（學員那一整句）
            for k in ("學員起訖", "改過的起訖", *ALIGN_KEYS):
                d.pop(k, None)
        for k in ("已確認", "救回"):
            if k in fields:
                d[k] = bool(fields[k])
        if d.get("已確認") and not d.get("做法"):
            raise ValueError("先選這一處要怎麼處理，再確認")
        d["更新時間"] = _now()
        _save_decisions(workdir, dec)
    if d.get("已確認"):
        # 10-01：要生成學員聲音（或兩邊都生成），得知道念什麼、用誰的聲線；缺的話不能通過
        o = next((x for x in overlap_choices(workdir) if x["id"] == str(oid)), None)
        why = overlap_gen_problem(o, student_slots(workdir)) if o else None
        if why:
            with _lock:
                dec = load_decisions(workdir)
                dec["重疊"].setdefault(str(oid), {})["已確認"] = False
                _save_decisions(workdir, dec)
            raise ValueError(f"這一處還不能通過：{why}")
    return {"ok": True, "id": oid, "決定": d}


def student_slots(workdir: Path) -> list[tuple[float, float]]:
    """學員段落重念的時間格（不含重疊卡片自己產生的那幾格）；沒有段落分析回傳空的。"""
    from bookclub import students

    try:
        items, _ = students.build_items(Path(workdir))
    except FileNotFoundError:
        return []
    return [tuple(it["slot"]) for it in items if not it.get("重疊")]


def overlap_student_gen(o: dict, slots: list[tuple[float, float]]) -> bool:
    """這一處重疊要不要自己生成學員那一句（純函式，10-01）：兩邊都重生成（照原位置疊著）一定要；
    生成學員聲音而且整個不在任何學員重念的時間格裡（在的話跟著那一格整段換掉）才要。"""
    if is_stacked(o["做法"], o.get("排法")):
        return True
    return o["做法"] == "只留學員" and not any(a < o["end"] and o["start"] < b for a, b in slots)


def gen_range_info(c: dict | None, slots: list[tuple[float, float]], sents: list[dict], turns: list[dict],
                   covs: list[dict] | None, oid: str) -> dict | None:
    """卡片上「生成學員聲音會換掉哪幾秒」（10-01 第三批）：只有選了生成學員聲音、又不在學員段落裡（要自己生成）才有。
    {start, end, 來源, 老師（範圍裡老師的句子，見 teacher_in_slot）, 疊到（範圍裡別筆換聲音、剪掉的名稱）}。"""
    if not c or is_stacked(c["做法"], c.get("排法")) or not overlap_student_gen(c, slots):
        return None
    a, b = c["學員生成起訖"]
    hit = [k["名稱"] for k in (covs or []) if k["start"] < b - 0.05 and a + 0.05 < k["end"] and k.get("重疊") != oid]
    return {"start": a, "end": b, "來源": c.get("學員生成來源"), "老師": teacher_in_slot(a, b, sents, turns), "疊到": hit}


def overlap_gen_problem(o: dict, slots: list[tuple[float, float]]) -> str | None:
    """選了要生成、但缺東西的原因（純函式）；沒問題回傳 None。"""
    miss = []
    if overlap_student_gen(o, slots):
        if not (o.get("學員文字") or "").strip():
            miss.append("「學員說的」是空的")
        if not o.get("學員已選") or o.get("學員") in (None, "", "老師"):   # 要人選過，不用猜的
            miss.append("還沒選學員是誰（要知道用哪一位的聲線）")
    if is_stacked(o["做法"], o.get("排法")) and not (o.get("老師文字") or "").strip():
        miss.append("「老師說的」是空的")
    return "、".join(miss) + "：要生成聲音，得知道念什麼、用誰的聲線" if miss else None


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
    keys = ("id", "start", "end", "狀態", "備註", "來源段落")   # 10-01：來源段落＝從哪一段學員段落「通過＝剪掉」來的
    f = {"狀態": "刪除", **{k: v for k, v in fields.items() if k in keys}} \
        if not fields.get("id") else {k: v for k, v in fields.items() if k in keys}
    return _upsert_range(Path(workdir), "刪除段落", "D", f, snap=True)


DELETABLE = {"刪除段落": "剪掉", "局部消音": "消音", "重疊": "漏抓的重疊", "名字": "漏抓的老師提到名字"}   # 人工新增、可以直接刪的


def delete_manual(workdir: str | Path, kind: str, iid: str, who: str | None = None) -> dict:
    """`POST /api/review/delete`：刪掉一筆人工新增的項目（10-01 第三批：加錯了以前只能還原、永遠留著）。

    能刪的（`DELETABLE`）：人工新增的剪掉片段（刪除段落裡沒有 `建議id` 的）、消音（局部消音）、漏抓的重疊（人工重疊）、
    漏抓的老師提到名字與「老師這一段用 AI 聲音重念」（人工名字）。影片分析建議的剪掉片段、自動抓到的重疊與名字不能刪
    （用不剪、不用改處理）；人工標的學員段落也不在這裡刪（會動到段落怎麼切，用「這段其實是老師」「併進上一段」）。
    刪掉的整筆（含卡片上的決定）收進 `已刪除`：{類型, id, 名稱, start, end, 刪除時間, 誰（這台電腦的使用者名稱）, 內容, 決定}，之後查得到。"""
    import getpass

    if kind not in DELETABLE:
        raise ValueError(f"這一種不能刪：{kind}")
    workdir = Path(workdir)
    iid = str(iid)
    try:
        who = who or getpass.getuser()
    except Exception:  # noqa: BLE001 — 拿不到使用者名稱不擋
        who = who or ""
    with _lock:
        dec = load_decisions(workdir)
        lst = {"刪除段落": dec["刪除段落"], "局部消音": dec["局部消音"], "重疊": dec["人工重疊"], "名字": dec["人工名字"]}[kind]
        item = next((x for x in lst if str(x.get("id")) == iid), None)
        if item is None:
            raise KeyError(f"找不到這一筆：{iid}")
        if kind == "刪除段落" and item.get("建議id"):
            raise ValueError("影片分析建議的剪掉片段不能刪：不剪的話選「不剪」")
        lst.remove(item)
        extra = {}
        if kind == "重疊":
            extra = dec["重疊"].pop(iid, None) or {}
        log = {"類型": kind, "id": iid, "名稱": f"{DELETABLE[kind]} {wd.fmt_time(item.get('start', 0.0))}",
               "start": item.get("start"), "end": item.get("end"), "刪除時間": _now(), "誰": who, "內容": item,
               **({"決定": extra} if extra else {})}
        dec.setdefault("已刪除", []).append(log)
        _save_decisions(workdir, dec)
    if kind == "名字":   # 卡片上的決定（做法、改稿、已確認）在名字覆核決定裡，一起收進紀錄
        with _lock:
            nd = wd.read_json(name_decisions_path(workdir), default={}) or {}
            gone = nd.pop(iid, None)
            if gone is not None:
                wd.write_json(name_decisions_path(workdir), nd)
                dec = load_decisions(workdir)
                dec["已刪除"][-1]["決定"] = gone
                _save_decisions(workdir, dec)
    return {"ok": True, "已刪除": log}


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
    rule = kind
    if kind == "名字" and iid and any(x["id"] == iid and x.get("老師整段") for x in load_decisions(workdir)["人工名字"]):
        rule = "學員發言"   # 10-01：人工標的老師整段（一整段老師的話）改時間對齊句子邊界，跟新增時一樣；對字會縮回名字附近
    if fields.get("不對齊") and iid:
        # 10-01 第三批 11：第 5 步改學員段落的範圍照填的時間（以前對齊句子邊界，+0.1 秒這種小調整會被縮回）
        al = {"start": round(a, 3), "end": round(b, 3), "標的起訖": [round(a, 3), round(b, 3)], "對齊": [False, False],
              "對齊到": "照填的時間", "文字": ""}
    else:
        al = align_range(workdir, rule, a, b)
    info = {"標的起訖": al["標的起訖"], "對齊": al["對齊"], "對齊到": al["對齊到"]}
    if kind == "刪除段落":
        new_id = _manual_cut(workdir, iid, al, info)
    elif kind == "局部消音":
        new_id = _manual_mute(workdir, iid, al, info, fields)
    elif kind == "重疊":
        new_id = _manual_overlap(workdir, iid, al, info)
    elif kind == "名字":
        new_id = _manual_name(workdir, iid, al, info, fields)
    elif not iid and str(fields.get("說話者") or "") == "老師":
        # 09-30 宇軒：這一段其實是老師在講（例如學員講完，老師接一句「謝謝〔名字〕」）→ 用老師的 AI 聲音重念
        from bookclub import turns as turns_mod

        tdata = wd.read_json(turns_mod.turns_path(workdir), default={}) or {}
        hit = next((t for t in tdata.get("段落", []) if t.get("說話者") != "老師"
                    and min(t["end"], al["end"]) - max(t["start"], al["start"]) > 0.2), None)
        if hit:   # 跟學員段落疊在一起的話，組裝時學員重念會蓋掉這一筆，先擋下來
            raise ValueError(f"這段時間跟 {hit['說話者']} 的段落（{wd.fmt_time(hit['start'])}–{wd.fmt_time(hit['end'])}）疊在一起。"
                             "先把那一段的結尾改早（改時間），或在那一段用「從游標處切開」把老師的話切出來，再按「這段其實是老師」。")
        new_id, kind = add_teacher_item(workdir, al["start"], al["end"], info=info), "名字"
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


def words_text(workdir: Path, start: float, end: float) -> str:
    """這段時間裡逐字稿的字（照每個字的時間挑，中點落在範圍裡的）。沒有逐字時間就回傳空字串。"""
    merged = wd.read_json(wd.merged_transcript_path(workdir), default={}) or {}
    return "".join(w.get("word", "") for w in merged.get("words") or [] if start <= (w["start"] + w["end"]) / 2 <= end).strip()


def add_teacher_item(workdir: str | Path, start: float, end: float, text: str | None = None, info: dict | None = None) -> str:
    """加一筆「老師這一段用 AI 聲音重念」（記在 `人工名字`，帶 `老師整段`；組裝時整段照打的字重念、原聲換掉）。
    `text` 沒給就用這段時間逐字稿的字當初稿；名冊上的本名、敏感詞先換成代號。回傳這一筆的 id（NM001…）。"""
    workdir = Path(workdir)
    raw = (text if text is not None else words_text(workdir, start, end)).strip()
    draft, _ = replace_real_names(raw, replace_table(workdir))
    with _lock:
        dec = load_decisions(workdir)
        iid = _next_id(dec["人工名字"], "NM")
        dec["人工名字"].append({"id": iid, "start": round(float(start), 3), "end": round(float(end), 3), "代號": "",
                              "matched_text": "", "sentence_id": None, "sentence": "", "老師整段": True, "整段文字": draft,
                              "來源": "人工新增", **(info or {}), "建立時間": _now(), "更新時間": _now()})
        dec["人工名字"].sort(key=lambda x: x["start"])
        _save_decisions(workdir, dec)
    return iid


def _manual_name(workdir: Path, iid: str | None, al: dict, info: dict, fields: dict) -> str:
    if iid:   # 09-30：人工標的老師整段改時間：只改起訖，不去對名字
        with _lock:
            dec = load_decisions(workdir)
            mine = next((x for x in dec["人工名字"] if x["id"] == iid and x.get("老師整段")), None)
            if mine is not None:
                mine.update({"start": al["start"], "end": al["end"], **info, "更新時間": _now()})
                dec["人工名字"].sort(key=lambda x: x["start"])
                _save_decisions(workdir, dec)
                return iid
    sents = (wd.read_json(wd.speakers_path(workdir), default={}) or {}).get("sentences", [])
    sent = align.sentence_at((al["start"] + al["end"]) / 2, sents)
    word = str(fields.get("名字") or "").strip() or al["文字"]
    with _lock:
        dec = load_decisions(workdir)
        mine = next((x for x in dec["人工名字"] if x["id"] == iid), None) if iid else None
        if iid and mine is None:   # 自動抓到的名字改時間：記在名字覆核決定裡
            decisions = wd.read_json(name_decisions_path(workdir), default={}) or {}
            d = decisions.setdefault(iid, {"tags": [], "note": ""})
            cands = (wd.read_json(wd.names_path(workdir), default=None) or {}).get("candidates", [])
            if str(iid).isdigit() and 1 <= int(iid) <= len(cands):
                d["候選指紋"] = name_fingerprint(cands[int(iid) - 1])
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
        if me.get("說話者") not in (None, "老師") and to.get("說話者") == "老師":
            # 10-02 第三批 14：學員段落切短、整句被還給老師段落的，記在這一段（第 3 步當下問「切在外面的這幾秒是誰的聲音」）
            me["切到外面"] = sorted(set(me.get("切到外面") or []) | set(give), key=lambda i: sent[i]["start"] if i in sent else 0.0)
    if me.get("切到外面"):   # 改回去包住的，不再算切到外面
        left = [i for i in me["切到外面"] if i not in inside]
        if left:
            me["切到外面"] = left
        else:
            me.pop("切到外面")
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
