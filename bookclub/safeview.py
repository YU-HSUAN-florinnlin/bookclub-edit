"""安全查詢指令 `bookclub inspect <工作區> <主題>`（10-02 第六批）。

為什麼要有：AI 助手查工作區狀態時，自己讀 JSON 沒濾乾淨，兩天內四次把逐字稿、名字、退回原因印到工具輸出
（其中一次含學員身心狀況的字眼）。靠提醒擋不住，改成從工具下手：這支指令只印時間、編號、數字、狀態。

做法是「白名單」：
- 數字、是／否：照印（時間欄位印成 0:42:59.8）
- 字串只有三種會印出值：
  1. 欄位在 `ENUM_KEYS`，而且值在 `VOCAB`（程式自己定的固定說法，例如 通過、補靜音、整句換掉）
  2. 欄位在 `ID_KEYS`，而且值長得像編號（T003、00_012、名字:3、學員重念@10.00⋯⋯，見 `safe_id`）
  3. 欄位在 `SPEAKER_KEYS`，而且值是「老師」或「學員N」這種代號（是本名的話照樣遮掉）
  另外 `STAMP_KEYS` 的值是「2026-10-02T18:34:00」這種時間戳才印
- 其他字串一律只印 `<文字 87 字>`／`<空>`，不印內容；不認得的新欄位也一樣（不是用黑名單濾已知的文字欄位）
- 清單只印數字清單與編號清單，其他印 `<清單 N 筆>`；巢狀的物件印 `<N 個欄位>`，不展開（鍵可能是本名）

這支指令不寫任何檔：算總檢查、前後沒聲音時會用到跟網頁一樣的函式，那幾個函式偶爾會順手整理存檔，
這裡執行期間把 `workdir.write_json` 換成不寫的版本。
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from bookclub import timemap
from bookclub import workdir as wd

# ---------- 白名單 ----------

VOCAB = {
    # 處理類型（處理紀錄、剪輯決策、第 3 步卡片）
    "學員重念", "名字整句換掉", "名字消音", "局部消音", "學員名字消音", "學員名字換代號", "刪除", "停格", "換聲音", "消音",
    "模糊示範", "重疊", "名字要人處理", "整片看時標的", "沒登記的變動", "學員段落", "名字", "刪除段落", "改成老師", "學員名字",
    # 建議刪除的類型
    "開頭空白", "結尾道別", "直播互動", "小組討論前後", "技術問題",
    # 做法、排法、方式、聲音
    "不用改", "兩邊都重生成", "只留老師", "只留老師原聲學員消音", "只留學員", "兩邊都不留", "生成老師聲音", "生成學員聲音",
    "兩邊都重新生成", "前後排開", "照原位置疊著", "整句換掉", "只換名字", "直接消音", "換成代號", "墊底噪", "霧化",
    "重新生成", "保留原聲", "不是名字", "是地名", "切點削到旁邊的字",
    # 狀態、結果
    "等待", "進行中", "做完", "跳過", "略過", "失敗", "中斷", "停止", "還原", "通過", "退回重做", "沒問題", "不刪",
    # 放回時間格
    "補靜音", "微調語速", "標紅", "插入停頓", "改語速重生成", "拉長",
    # 內容類型、說話者判斷、信心
    "冥想引導", "導讀", "講解", "提問與回應", "學員分享", "其他", "老師", "學員", "不是老師", "不確定", "太短",
    "高", "中", "低", "人工", "精確", "A1", "A2", "句首", "句中", "句尾", "句首句尾", "雙邊乾淨", "單邊乾淨", "都不乾淨",
    "男", "女", "建議稿", "校對稿", "逐筆", "整片看", "人工新增", "建議", "手動", "聲紋", "文字：冥想引導", "文字：導讀",
    "安靜處", "逐字稿的字", "句子邊界", "照填的時間", "逐字",
    # 段落外的答案
    "老師不用處理", "老師重念", "還是學員", "好幾個人",
    # 總檢查的列
    "段落是老師", "學員段落是老師", "彙總", "較短",   # 10-04 #127、#117
    "段落外", "聲紋", "沒有字", "英文代號", "字太少", "名字換不了代號", "重疊缺東西",   # 10-05 #177：一定要處理的類別
    # 前後沒聲音（第六批）
    "重念範圍", "老師整段", "老師起訖",
    # 全片底噪（第六批第五件）
    "確認",
    # 10-04 #134：停格點跟格子結尾錯開時怎麼接（assemble.FREEZE_*）
    "對齊", "停格點比結尾早：畫面先停，聲音連續播完，播完才墊底噪", "停格點比結尾晚：聲音連續播過停格點並播完，之後墊底噪（佔用下一筆開頭這一小段）",
    "停格點比結尾晚太多：維持原樣，聲音前後淡出淡入", "錯開太多，照舊",
    # 要念的文字放在哪個欄位（第七批「代號」主題）
    "改稿", "整段文字", "老師文字", "學員文字", "老師整句改稿",
    # 學員段落裡兩格之間的空隙（第八批補修 #102）
    "學員空隙消音", "學員空隙保留原聲", "老師的話", "別的處理",
    # 生成的內容問題（10-03 第八批 #61、#104）
    "漏了一串字", "結尾可能少念了字", "開頭可能少念了字",
    # 要人聽的原因類別（10-04 #61 補修：`inspect 生成` 的 要人聽原因，見 `listen_reasons`）
    "內容沒通過", "念對沒有的檢查沒做成", "放回時間格差太多", "切在講話中", "其他原因",
    # 剪輯決策的警告類型（10-04 #61 補修，見 `WARN_KINDS`）與重疊怎麼處理
    "段落改過、舊的重念不用", "時間格改過、還沒重新生成", "跟別筆重疊、被較長的蓋過", "跟別筆重疊、以那一筆為準",
    "跟別筆邊界差一點、修齊", "整筆落在別筆裡", "局部消音跟別的處理重疊", "局部消音落在剪掉的地方", "還沒生成",
    "改成消音", "修齊照做", "其餘照做", "跟著那一筆",
    # 名字候選從哪一種句子找到的、補找的原因（10-04 #119）
    "太短句", "不確定句", "老師段落裡的不是老師句", "找名字範圍放寬後補找", "段落改成老師後補找",
}
ENUM_KEYS = {"狀態", "類型", "做法", "排法", "放回做法", "版本", "建議做法", "結果", "方式", "內容類型", "信心", "label", "role",
             "文字判斷", "聲音判斷", "比對層級", "位置", "切點信心", "聲線", "角色", "文字來源", "來源", "判斷依據", "建議類型",
             "決定", "對齊到", "答案", "段落外答案", "tags", "聲音", "改法", "範圍類型", "保留原因", "內容問題",
             "自動處理",   # 10-04 #111：重疊自動處理（值是「學員段落」）
             "類別",   # 10-05 #177：總檢查「一定要處理」的類別
             # 10-04 #61 補修：被蓋過改消音（值是原本的類型）、要人聽原因、警告的類型與處理
             "被蓋過改消音", "要人聽原因", "警告類型", "處理",
             "補找",   # 10-04 #119：名字候選是怎麼補找到的
             "錯開處理", "停格錯開處理"}   # 10-04 #134：停格點跟結尾錯開時怎麼接
ID_KEYS = {"id", "鍵", "key", "段落", "sentence_id", "區域", "候選", "覆核項目", "句子", "重疊項目", "生成編號", "建議id",
           "來源段落", "edit", "第3步", "生成", "聽過", "編號", "前一格", "後一格",
           # 10-03 第八批補修（#12）：名字候選併進哪一張卡（同一處、同一句同代號）
           "同一處", "同一張卡", "決定帶頭", "同一處候選", "同一張卡候選", "併進",
           # 10-04 #111：學員段落裡自動處理的重疊落在哪一段
           "學員段落",
           # 10-04 #61 補修：警告裡蓋過這一筆的動作
           "蓋過的", "碰到邊的"}
SPEAKER_KEYS = {"說話者", "學員", "學員說話者", "文字學員編號", "學員猜的"}
# 10-02 第七批：代號（艾瑪、Emma 這類）不是個資，但值只有在新舊代號名單裡才印（自己打的、其他字照樣遮）
CODE_KEYS = {"代號", "建議代號", "舊", "建議", "新"}
STAMP_KEYS = {"更新時間", "建立時間", "時間", "產生時間", "開始", "結束", "看過時間", "處理紀錄產生時間", "開始時間", "結束時間",
              "重做時間"}
TIME_KEYS = {"start", "end", "slot", "原片", "成品", "at", "標的起訖", "改過的起訖", "整句起訖", "老師起訖", "學員起訖",
             "原本起訖", "src", "成品秒", "原片秒", "範圍", "句子外面", "段落外起訖", "建議", "原本", "改成", "前後",
             "疊到的範圍", "現在的slot"}

ID_PREFIXES = VOCAB | {"段落外", "剪掉", "名字消音", "長句", "字太少", "聲紋", "前後沒聲音", "學員段落", "名字", "重疊",
                       "刪除段落", "局部消音", "改成老師", "學員名字", "底噪", "空隙",
                       "沒有字"}   # 10-04 #117
_ID_BODY = r"(?:[A-Za-z]{0,4}\d+(?:[._m]\d+)*(?:_學員|_老師)?|\d+\.\d+)"   # O5602.14_學員：重疊卡片自己生成的那一句
_ID_RE = re.compile(rf"^{_ID_BODY}$")
_SPK_RE = re.compile(r"^(老師|學員\d+|學員\?)$")
_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T[\d:.]+)?$")
_VOICE_RE = re.compile(r"^(男|女)\d{1,2}$")


def safe_id(s: str) -> bool:
    """編號長得像編號才印：T003、00_012、V0542232、O69.60、NM001、12；或「前綴:編號」「類型@秒數」，
    前綴只能是程式自己的固定說法（ID_PREFIXES），中間可以有好幾段（段落外:T003:123.4）。"""
    if not isinstance(s, str) or not s or len(s) > 40:
        return False
    if _ID_RE.match(s):
        return True
    parts = re.split(r"[:@#]", s)
    if len(parts) < 2 or parts[0] not in ID_PREFIXES:
        return False
    return all(_ID_RE.match(p) or p in VOCAB for p in parts[1:])


def mask(s) -> str:
    s = str(s)
    return "<空>" if not s.strip() else f"<文字 {len(s)} 字>"


_MS = {"on": False}   # 10-04 #61 補修：`--毫秒` 時時間印到小數 3 位（看毫秒級的重疊）


def t3(sec: float | None) -> str:
    """秒數 → 「0:52:52.912」（到千分之一秒）。"""
    if sec is None:
        return "—"
    sec = round(max(0.0, float(sec)), 3)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:06.3f}"


def tfmt(sec: float | None) -> str:
    return t3(sec) if _MS["on"] else timemap.t1(sec)


@contextmanager
def millis(on: bool = True):
    old = _MS["on"]
    _MS["on"] = on
    try:
        yield
    finally:
        _MS["on"] = old


def _num(v: float) -> str:
    return str(v) if isinstance(v, int) else f"{v:.3f}".rstrip("0").rstrip(".")


def known_code(v: str) -> bool:
    """新名單（中文寫法）或舊英文名單上的代號（不分大小寫）。"""
    from bookclub import epcodes

    return v in epcodes.CODE_POOL or v.strip().lower() in {c.lower() for c in epcodes.OLD_CODE_POOL}


def val(key: str, v) -> str:
    """一個欄位的值 → 可以印的字（白名單，見檔案開頭）。"""
    k = key.split(".")[-1]
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, (int, float)):
        return tfmt(v) if k in TIME_KEYS else _num(v)
    if isinstance(v, str):
        if k in ENUM_KEYS and v in VOCAB:
            return v
        if k in ENUM_KEYS and k == "聲線" and _VOICE_RE.match(v):
            return v
        if k in ID_KEYS and safe_id(v):
            return v
        if k in SPEAKER_KEYS and _SPK_RE.match(v):
            return v
        if k in STAMP_KEYS and _STAMP_RE.match(v):
            return v
        if k in CODE_KEYS and known_code(v):
            return v
        return mask(v)
    if isinstance(v, (list, tuple)):
        if not v:
            return "[]"
        if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
            if len(v) > 6:
                return f"<數字 {len(v)} 個>"
            if k in TIME_KEYS and len(v) == 2:
                return f"{tfmt(v[0])}–{tfmt(v[1])}"
            return "[" + ", ".join(val(k, x) for x in v) + "]"
        if all(isinstance(x, str) for x in v) and k in (ID_KEYS | ENUM_KEYS | SPEAKER_KEYS | CODE_KEYS):
            shown = [val(k, x) for x in v[:10]]
            return "[" + ", ".join(shown) + (f", ⋯共 {len(v)} 個" if len(v) > 10 else "") + "]"
        return f"<清單 {len(v)} 筆>"
    if isinstance(v, dict):
        return f"<{len(v)} 個欄位>"
    return mask(v)


def flat(d: dict, prefix: str) -> dict:
    """巢狀物件的第一層攤開成「prefix.欄位」（決定、放回時間格這種固定格式的物件）。"""
    return {f"{prefix}.{k}": v for k, v in (d or {}).items()} if isinstance(d, dict) else {prefix: d}


def fmt_row(row: dict, first: tuple = ()) -> str:
    keys = [k for k in first if k in row] + [k for k in row if k not in first]
    return "  ".join(f"{k}={val(k, row[k])}" for k in keys)


# ---------- 篩選 ----------

def _row_range(row: dict) -> tuple[float, float] | None:
    for a, b in (("start", "end"),):
        if isinstance(row.get(a), (int, float)) and isinstance(row.get(b), (int, float)):
            return float(row[a]), float(row[b])
    for k in ("slot", "原片", "範圍"):
        v = row.get(k)
        if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
            return float(v[0]), float(v[1])
    if isinstance(row.get("at"), (int, float)):
        return float(row["at"]), float(row["at"])
    return None


class Filter:
    def __init__(self, a: float | None = None, b: float | None = None, ids: list[str] | None = None):
        self.a, self.b, self.ids = a, b, [x for x in (ids or []) if x]

    def ok(self, row: dict) -> bool:
        if self.ids:
            got = {str(row.get(k)) for k in ("id", "鍵", "key", "段落", "生成編號") if row.get(k) is not None}
            got |= {str(x) for x in row.get("覆核項目") or []}
            if not any(i == g or g.endswith(":" + i) or g.startswith(i) for i in self.ids for g in got):
                return False
        if self.a is None and self.b is None:
            return True
        r = _row_range(row)
        if r is None:
            return False
        lo = self.a if self.a is not None else -1e18
        hi = self.b if self.b is not None else 1e18
        return r[0] <= hi and lo <= r[1]


# ---------- 不寫檔 ----------

@contextmanager
def read_only():
    """這支指令執行期間 `workdir.write_json` 不寫檔（算總檢查時呼叫的函式偶爾會順手整理存檔）。"""
    orig = wd.write_json
    wd.write_json = lambda path, data: None
    try:
        yield
    finally:
        wd.write_json = orig


# ---------- 主題 ----------

def _read(path: Path):
    return wd.read_json(path, default=None)


def topic_files(w: Path, f: Filter, out: list[str]) -> None:
    """工作區有哪些資料檔（只列檔名、大小、修改時間）。"""
    import datetime

    names = ["transcript/merged.json", "說話者判斷.json", "重疊.json", "名字候選.json", "名字覆核決定.json", "分析結果.json",
             "校對/段落.json", "校對/刪除建議.json", "覆核/覆核決定.json", "覆核/成品檢查.json", "生成/名字處理計畫.json",
             "生成/老師紀錄.json", "生成/學員紀錄.json", "生成/保留原聲學員紀錄.json", "生成/處理紀錄.json",
             "生成/執行進度.json", "生成/子程式紀錄.jsonl", "生成/剪輯決策.json", "參考音/底噪.json"]
    for n in names:
        p = w / n
        if p.is_file():
            st = p.stat()
            out.append(f"{n}  {st.st_size} 位元組  改於 {datetime.datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M:%S}")
        else:
            out.append(f"{n}  沒有")
    outd = w / "輸出"
    if outd.is_dir():
        for p in sorted(outd.glob("剪輯決策_*.json")):
            out.append(f"輸出/{p.name}  {p.stat().st_size} 位元組")


def topic_turns(w: Path, f: Filter, out: list[str]) -> None:
    data = _read(w / "校對" / "段落.json") or {}
    rows = [t for t in data.get("段落", []) if f.ok(t)]
    out.append(f"段落 {len(data.get('段落', []))} 段（符合條件 {len(rows)} 段）")
    # 10-04 #127：學員段落聲紋多半是老師（只印是／否與數字）
    from bookclub import turnvoice

    sents = (_read(wd.speakers_path(w)) or {}).get("sentences", [])
    by_id = {s.get("id"): s for s in sents}
    for t in rows:
        line = fmt_row(t, ("id", "start", "end", "說話者", "內容類型", "已確認"))
        if t.get("說話者") not in (None, "老師"):
            v = turnvoice.turn_voice(t, sents, by_id)
            line += (f"  聲紋多半是老師={val('', bool(v['多半是老師']))}  聲紋老師比例={v['比例']:.3f}"
                     f"  聲紋老師句數={v['老師句數']}/{v['句數']}  聲紋老師秒={v['老師秒']:.1f}/{v['計入秒']:.1f}"
                     if v else "  聲紋多半是老師=—（沒有可以算的句子）")
        out.append(line)
    people = data.get("學員") or {}
    if people and not f.ids and f.a is None and f.b is None:
        out.append("學員：")
        for k, p in people.items():
            name = k if _SPK_RE.match(str(k)) else mask(k)
            out.append(f"  {name}  " + fmt_row(p if isinstance(p, dict) else {"值": p}, ("秒數", "段數")))


def topic_sentences(w: Path, f: Filter, out: list[str]) -> None:
    data = _read(wd.speakers_path(w)) or {}
    sents = data.get("sentences", [])
    rows = [s for s in sents if f.ok(s)]
    out.append(f"句子 {len(sents)} 句（符合條件 {len(rows)} 句）；各判斷句數：" +
               "、".join(f"{val('label', k)} {n}" for k, n in Counter(s.get("label") for s in sents).items()))
    for s in rows:
        out.append(fmt_row(s, ("id", "start", "end", "label", "sim")))


def topic_words(w: Path, f: Filter, out: list[str], limit: int) -> None:
    """逐字稿的字：只印時間，不印字。10-04：時間照 `--毫秒`；安靜處與清單都照 `--limit`、只列範圍內的。"""
    merged = _read(wd.merged_transcript_path(w)) or {}
    words = merged.get("words") or []
    rows = [x for x in words if f.ok({"start": x.get("start"), "end": x.get("end")})]
    out.append(f"字 {len(words)} 個（符合條件 {len(rows)} 個）")

    def more(n: int) -> str:
        return f"⋯另外 {n - limit} 處沒列（用 --limit 調）" if n > limit else ""

    # 10-04 #105：轉文字時被挖掉的靜音（只有時間），用來查「一個字橫跨被挖掉的靜音」
    sil = [s for s in (merged.get("silence_map") or []) if f.ok({"start": s.get("start"), "end": s.get("end")})]
    if sil:
        out.append(f"VAD 判定的安靜處（挖停頓的做法轉的就是被挖掉的地方）{len(sil)} 處：" + "、".join(
            f"{tfmt(s['start'])}–{tfmt(s['end'])}（{s['end'] - s['start']:.1f} 秒）" for s in sil[:limit]) + more(len(sil)))
    # 10-04 #62：整個落在安靜超過 1 秒的地方的字（可能是 Groq 自己編的；只印數字與時間）
    from bookclub.transcribe import QUIET_WORD_MIN_S, quiet_word_stats

    qs = quiet_word_stats(words, merged.get("silence_map") or [])
    if merged.get("轉文字做法"):
        out.append(f"轉文字做法：{merged['轉文字做法'] if merged['轉文字做法'] in ('不挖停頓', '保留一秒停頓') else '<其他>'}")
    pos = [p for p in qs["位置"] if f.ok({"start": p[0], "end": p[1]})]
    out.append(f"落在安靜超過 {QUIET_WORD_MIN_S:.0f} 秒的地方的字（整支）：{qs['字數']} 個、{qs['處數']} 處"
               + (f"；範圍內 {len(pos)} 處：" + "、".join(f"{tfmt(a)}–{tfmt(b)}（{n} 個）" for a, b, n in pos[:limit]) + more(len(pos))
                  if pos else ""))
    # 10-04 #117：有人聲但沒有字（Groq 漏轉）：整支統計＋範圍內每一處（時間、人聲秒數、小段數）
    from bookclub import untranscribed

    regions = untranscribed.from_merged(merged)
    st = untranscribed.stats(regions)
    if st is None:
        out.append("有人聲但沒有字：沒有安靜處資料，不統計")
    else:
        shown = [r for r in regions if f.ok(r)]
        out.append(f"有人聲但沒有字（整支）：{st['處數']} 處、人聲合計 {st['人聲秒']:.1f} 秒、"
                   f"{untranscribed.MUST_VOICE_S:.0f} 秒以上 {st['三秒以上']} 處；範圍內 {len(shown)} 處")
        for r in shown[:limit]:
            out.append(f"  沒有字 {tfmt(r['start'])}–{tfmt(r['end'])}  人聲={r['人聲秒']:.1f} 秒  小段數={r['小段數']}")
        if len(shown) > limit:
            out.append(more(len(shown)))
    if rows:
        out.append(f"第一個字從 {tfmt(rows[0]['start'])} 開始，最後一個字到 {tfmt(rows[-1]['end'])}")
        gaps = [(rows[i]["end"], rows[i + 1]["start"]) for i in range(len(rows) - 1) if rows[i + 1]["start"] - rows[i]["end"] >= 0.5]
        if gaps:
            out.append("字跟字之間空超過 0.5 秒的地方：" + "、".join(f"{tfmt(a)}–{tfmt(b)}（{b - a:.1f} 秒）" for a, b in gaps[:limit])
                       + more(len(gaps)))
    for i, x in enumerate(rows[:limit], 1):
        out.append(f"#{i}  {tfmt(x['start'])}–{tfmt(x['end'])}  <字 {len(str(x.get('word', '')))} 個字元>")
    if len(rows) > limit:
        out.append(f"⋯另外 {len(rows) - limit} 個沒列（用 --limit 調）")


def topic_overlaps(w: Path, f: Filter, out: list[str]) -> None:
    from bookclub import review

    from bookclub import turns as turns_mod

    dec = review.load_decisions(w)
    # 10-04 #111：跟第 3 步一樣套上不用重跑的過濾（邊界誤差、學員段落裡兩邊都沒有老師），`自動處理` 欄位看得出是哪一種
    tdata = turns_mod.page_data(w)
    turns = tdata.get("段落", []) if not tdata.get("尚未準備") else []
    ov = review.load_overlaps(w, dec, turns)
    rows = review.effective_overlaps(w, ov.get("overlaps", []), dec)
    n_auto = sum(1 for o in rows if o.get("自動處理") and not dec["重疊"].get(review.overlap_id(o), {}).get("救回"))
    out.append(f"重疊 {len(rows)} 處（含人工補的）；學員段落裡自動處理 {n_auto} 處")
    for o in rows:
        oid = review.overlap_id(o)
        row = {"id": oid, **{k: v for k, v in o.items() if k not in ("id", "speakers")},
               "角色": [s.get("role") for s in o.get("speakers", [])], **flat(dec["重疊"].get(oid, {}), "決定")}
        if f.ok(row):
            out.append(fmt_row(row, ("id", "start", "end", "length", "已自動跳過", "自動處理", "決定.做法", "決定.已確認")))


def _name_rows(w: Path) -> list[dict]:
    from bookclub import review

    cands = (_read(wd.names_path(w)) or {}).get("candidates", [])
    decisions = _read(review.name_decisions_path(w)) or {}
    rows = []
    for i, c in enumerate(review.effective_name_candidates(w, cands, decisions), start=1):
        cid = str(c.get("id") or i)
        row = {"id": cid, **{k: v for k, v in c.items() if k != "id"}, **flat(decisions.get(cid) or {}, "決定")}
        rows.append(row)
    return rows


def topic_names(w: Path, f: Filter, out: list[str], recompute: bool = False) -> None:
    if recompute:
        _names_recompute(w, f, out)
        return
    rows = _name_rows(w)
    out.append(f"名字候選 {len(rows)} 筆（含人工補的）")
    for row in rows:
        if f.ok(row):
            out.append(fmt_row(row, ("id", "start", "end", "同一處", "同一張卡", "決定.做法", "決定.已確認", "決定.tags",
                                     "老師整段", "建議做法")))
    plan = _read(w / "生成" / "名字處理計畫.json")
    if plan:
        out.append(f"名字處理計畫（上次排的）：生成 {len(plan.get('生成', []))} 段、消音 {len(plan.get('消音', []))} 段、"
                   f"略過 {len(plan.get('略過', []))} 筆、要人處理 {len(plan.get('要人處理', []))} 筆")
        for g in plan.get("生成", []):
            row = {"生成編號": g.get("id"), **{k: v for k, v in g.items() if k != "id"}}
            if f.ok(row):
                out.append("  生成 " + fmt_row(row, ("生成編號", "slot", "候選", "重疊項目", "疊放")))


def _spots(cands: list[dict]) -> list[tuple[float, float]]:
    """候選的時間範圍有重疊的併成一處（同一處好幾個名冊寫法，第 3 步併成一張卡）。"""
    out: list[list[float]] = []
    for c in sorted(cands, key=lambda c: c["start"]):
        if out and c["start"] <= out[-1][1]:
            out[-1][1] = max(out[-1][1], c["end"])
        else:
            out.append([c["start"], c["end"]])
    return [(a, b) for a, b in out]


def _names_recompute(w: Path, f: Filter, out: list[str]) -> None:
    """10-04 #119：`名字 --重算`——用現在的程式在放寬的句子（太短、不確定、老師段落裡的不是老師句）重找名字，
    不寫檔（`names.preview_supplement`），列出現有候選沒有的：時間、句子編號、來源、比對層級、信心。"""
    from bookclub import names

    r = names.preview_supplement(w)
    out.append("（--重算：用現在的程式在太短、不確定的句子與老師段落裡的不是老師句重找名字；不寫檔）")
    if r.get("沒辦法重算"):
        out.append("沒有名冊或音檔，沒辦法重算")
        return
    scope = "放寬後" if r.get("掃描範圍") == names.SCAN_SCOPE else "舊版（只掃判成老師的句子）"
    out.append(f"現有候選 {r['現有候選數']} 筆；名字候選.json 的掃描範圍：{scope}")
    out.append("多掃的句子：" + ("、".join(f"{val('來源', k)} {n} 句" for k, n in r["多掃句數"].items()) or "沒有"))
    new = r["candidates"]
    out.append(f"這些句子裡找到 {len(r['找到'])} 筆（現有候選已經有的 {len(r['找到']) - len(new)} 筆；"
               f"命中排除清單 {len(r['已自動排除'])} 筆）")
    by_src = Counter(c.get("來源") for c in new)
    spots = _spots(new)
    out.append(f"現有候選沒有的 {len(new)} 筆、{len(spots)} 處（重疊的算一處）："
               + ("、".join(f"{val('來源', k)} {n} 筆" for k, n in by_src.items()) or "沒有"))
    for c in new:
        row = {k: c.get(k) for k in ("start", "end", "sentence_id", "來源", "比對層級", "信心", "敏感詞", "位置",
                                     "建議做法", "切點信心")}
        if f.ok(row):
            out.append("  新增 " + fmt_row(row, ("start", "end", "sentence_id", "來源", "比對層級", "信心")))


def topic_cuts(w: Path, f: Filter, out: list[str]) -> None:
    from bookclub import review

    dec = review.load_decisions(w)
    out.append(f"局部消音 {len(dec['局部消音'])} 筆")
    for m in dec["局部消音"]:
        if f.ok(m):
            out.append(fmt_row(m, ("id", "start", "end", "方式", "狀態")))
    out.append(f"剪掉（刪除段落） {len(dec['刪除段落'])} 筆")
    for c in dec["刪除段落"]:
        if f.ok(c):
            out.append(fmt_row(c, ("id", "start", "end", "狀態", "建議id")))
    sug = (_read(w / "校對" / "刪除建議.json") or {}).get("建議", [])
    out.append(f"建議剪掉 {len(sug)} 筆")
    for s in sug:
        row = {**s, **flat(dec["刪除建議"].get(s.get("id"), {}), "決定")}
        if f.ok(row):
            out.append(fmt_row(row, ("id", "start", "end", "類型", "決定.決定")))


def topic_final_check(w: Path, f: Filter, out: list[str]) -> None:
    from bookclub import execute

    fc = execute.final_check(w)
    m = timemap.load(w)
    out.append(f"一定要處理 {len(fc['一定要處理'])} 列（還要處理 {fc['還要處理']}、已確認 {fc['已確認']}、"
               f"按了照目前設定做 {fc.get('已按照目前設定做', 0)}）；"
               f"請看一眼 {len(fc['請看一眼'])} 列（已看過 {fc.get('已看過列數', 0)}）；可以開始={val('', fc['可以開始'])}；"
               f"我看過了={val('', fc['看過'])}；看過後新增 {fc['看過後新增']} 列")
    for part in ("一定要處理", "請看一眼"):
        for r in fc[part]:
            row = {k: v for k, v in r.items() if k not in ("有學員聲音", "聲紋老師", "顯示編號", "照目前設定做不開放原因", "照目前設定做的後果", "依據")}   # 顯示編號＝key 或第3步，不重印
            if isinstance(r.get("聲紋老師"), dict):   # 10-04 #127：只有數字與是／否
                row.update(flat({k: v for k, v in r["聲紋老師"].items() if k != "提醒"}, "聲紋老師"))
            if f.ok(row):
                both = timemap.both(r["start"], r["end"], m) if m else None
                out.append(f"[{part}] " + fmt_row(row, ("key", "類別", "start", "end", "處理好", "已按照目前設定做", "已按聽過", "可以按照目前設定做", "照目前設定做後變了", "回第3步補", "已看過", "新的", "第3步"))
                           + (f"  （{both}）" if both else "") + (f"  有學員聲音的路 {len(r['有學員聲音'])} 條" if r.get("有學員聲音") else ""))
                for p in r.get("時間點") or []:   # 10-04 #117：合成一列的每一處（編號、時間）
                    out.append("    時間點 " + fmt_row({k: p.get(k) for k in ("id", "start", "end", "類型", "秒") if k in p}))
    s = fc.get("摘要") or {}
    out.append(f"摘要：自動算處理好 {len(s.get('自動算處理好') or [])} 筆、要生成 {s.get('要生成秒數')} 秒、預估 {s.get('預估秒數')} 秒、"
               f"硬碟可用 {s.get('硬碟可用GB')} GB")
    auto = s.get("學員段落裡自動處理") or []   # 10-04 #111
    out.append(f"學員段落裡自動處理的重疊 {len(auto)} 處" + ("：" + "、".join(
        f"{x['id']} {timemap.t1(x['start'])}–{timemap.t1(x['end'])}" for x in auto if safe_id(x["id"])) if auto else ""))


def _gen_logs(w: Path) -> dict[str, Path]:
    from bookclub import studentgen, students, tts

    return {"老師": tts.teacher_log_path(w), "學員": students.log_path(w), "保留原聲": studentgen.log_path(w)}


def _student_items_now(w: Path) -> dict | None:
    """現在的段落排出來的學員格子（`students.build_items`，跟組裝用的同一份；不載入模型、不寫檔）。排不出來回傳 None。"""
    from bookclub import students

    try:
        items, _ = students.build_items(w)
    except Exception:  # noqa: BLE001 — 匯入的工作區沒有段落分析等：只是少印這幾欄
        return None
    return {it["id"]: it for it in items}


def listen_reasons(r: dict) -> list[str]:
    """這一句為什麼要人聽（固定說法，從紀錄的數字、是否推出來；不讀任何文字欄位）：
    內容沒通過（選定那一次念的跟要念的差太多）、念對沒有的檢查沒做成、內容問題（漏字、頭尾少念）、
    放回時間格差太多（每一種放回做法都標紅）、切在講話中；都不是就印「其他原因」。"""
    out = []
    tries = r.get("嘗試") or []
    k = (r.get("選定") or 1) - 1
    chosen = tries[k] if 0 <= k < len(tries) and isinstance(tries[k], dict) else {}
    if chosen.get("內容檢查沒做成"):
        out.append("念對沒有的檢查沒做成")
    elif chosen.get("內容通過") is False:
        out.append("內容沒通過")
    if r.get("內容問題"):
        out.append(r["內容問題"] if r["內容問題"] in VOCAB else "其他原因")
    if (r.get("放回時間格") or {}).get("放回做法") == "標紅":
        out.append("放回時間格差太多")
    if r.get("切在講話中"):
        out.append("切在講話中")
    return list(dict.fromkeys(out)) or ["其他原因"]


def topic_generation(w: Path, f: Filter, out: list[str], who: str | None) -> None:
    from bookclub import studentgen, students, tts

    dirs = {"老師": tts.teacher_out_dir(w), "學員": students.out_dir(w), "保留原聲": studentgen.out_dir(w)}
    for role, path in _gen_logs(w).items():
        if who and who != role:
            continue
        data = _read(path)
        if not data:
            out.append(f"【{role}】沒有生成紀錄")
            continue
        recs = data.get("句子", [])
        st = data.get("統計") or {}
        out.append(f"【{role}】{len(recs)} 句；統計：" + "  ".join(f"{k}={val(k, v)}" for k, v in st.items()))
        versions = _read(dirs[role] / tts.REDO_VERSIONS) or {}
        now_by = _student_items_now(w) if role == "學員" else None
        for r in recs:
            row = {"id": r.get("id"), **{k: v for k, v in r.items() if k not in ("id", "嘗試", "放回時間格", "候選做法", "以前的版本",
                                                                               students.MID_SPEECH)},
                   **flat(r.get("放回時間格") or {}, "放回時間格")}
            # 10-04 #61 補修：學員每一格都印「切在講話中」是不是（以前紀錄沒這個欄位就不印）、要人聽的原因類別、聲音檔在不在
            pts = r.get(students.MID_SPEECH) or []
            if role == "學員":
                row["切在講話中"] = bool(pts)
            if r.get("要人聽"):
                row["要人聽原因"] = listen_reasons(r)
            row["聲音檔在"] = not tts.output_missing(r, w)
            if now_by is not None:   # 現在的段落排出來的這一格（組裝用這個時間格）：跟紀錄差多少、現在切在講話中嗎
                cur = now_by.get(r.get("id"))
                row["現在還在"] = cur is not None
                if cur is not None and r.get("slot"):
                    row["現在的slot"] = cur["slot"]
                    row["跟現在差秒"] = round(max(abs(cur["slot"][0] - r["slot"][0]), abs(cur["slot"][1] - r["slot"][1])), 3)
                    row["現在切在講話中"] = bool(cur.get(students.MID_SPEECH))
            if not f.ok(row):
                continue
            out.append(fmt_row(row, ("id", "段落", "學員", "slot", "slot_s", "選定", "要人聽", "要人聽原因", "切在講話中",
                                     "聲音檔在", "現在還在", "現在的slot", "跟現在差秒", "現在切在講話中", "建議做法", "放回時間格.放回做法", "放回時間格.差異比例", "第幾版", "內容問題"))
                       + (f"  切點={'、'.join(tfmt(x) for x in pts if isinstance(x, (int, float)))}" if pts else ""))
            for a in r.get("嘗試") or []:
                out.append("    嘗試 " + fmt_row(a, ("第幾次", "種子", "語速", "長度秒", "插入停頓後長度秒", "內容相似度", "內容通過",
                                                    "內容問題", "漏字數", "長度通過", "聲紋相似度", "耗時秒")))
            for v in r.get("以前的版本") or []:
                out.append("    以前的版本 " + fmt_row({k: x for k, x in v.items() if k != "嘗試"}, ("第幾版", "種子", "試過的種子", "選定")))
            ent = versions.get(str(r.get("id"))) if isinstance(versions, dict) else None
            if isinstance(ent, dict):
                out.append(f"    重新生成版本：避開的念法（種子）{val('種子', ent.get('避開') or [])}、以前的版本 {len(ent.get('以前的版本') or [])} 個")


_PART_LABEL_RE = re.compile(r"^(老師聲音|學員聲音|保留原聲學員名字|組裝成品)(\s(男|女)\d{1,2}|\s學員\d+)?(：(生成|插入停頓|收尾|停頓)"
                            r"(（(\d+ 段|\d+ 個聲線一起|\d+ 位一起)）)?)?$")


def topic_parts(w: Path, f: Filter, out: list[str]) -> None:
    from bookclub import execute

    p = execute.part_log_path(w)
    if not p.is_file():
        out.append("沒有子程式紀錄")
        return
    lines = [x for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    out.append(f"子程式紀錄 {len(lines)} 支")
    for i, line in enumerate(lines, 1):
        try:
            rec = json.loads(line)
        except ValueError:
            out.append(f"#{i}  （這一行讀不懂）")
            continue
        label = rec.get("名稱")
        shown = label if isinstance(label, str) and _PART_LABEL_RE.match(label) else mask(label or "")
        row = {k: v for k, v in rec.items() if k not in ("名稱", "參數")}
        row["參數"] = f"<{len(rec.get('參數') or [])} 個>"
        out.append(f"#{i}  名稱={shown}  " + "  ".join(f"{k}={v if k == '參數' else val(k, rec[k])}" for k, v in row.items()))


def topic_finalcheck(w: Path, f: Filter, out: list[str], only_flagged: bool = False) -> None:
    from bookclub import finalcheck, proclog

    log = proclog.load(w)
    if not log:
        out.append("沒有處理紀錄（第 4 步還沒組裝）")
        return
    check = finalcheck.refresh(finalcheck.load_check(w), log)
    st = finalcheck.status(log, check)
    out.append(f"處理紀錄 {val('產生時間', log.get('產生時間'))}，範圍 {val('範圍', log.get('範圍'))}，"
               f"片段 {len(log.get('片段') or [])} 段；成品影片 {mask(check.get('成品影片') or '')}")
    out.append(f"逐筆 通過 {st['逐筆']['通過']}／退回 {st['逐筆']['退回']}／共 {st['逐筆']['總數']}；沒登記的變動 "
               f"{st['未登記']['沒問題']}／{st['未登記']['總數']} 確認；看過 {st['看過比例'] * 100:.1f}%；可以輸出={val('', st['可以輸出'])}")
    n_flag = 0
    for r in log.get("紀錄", []):
        d = check["逐筆"].get(finalcheck.record_key(r), {})
        flagged = bool(r.get("要人聽"))
        n_flag += flagged
        if only_flagged and not flagged:
            continue
        row = {"鍵": finalcheck.record_key(r), "編號": r.get("編號"), "類型": r.get("類型"), "原片": r.get("原片"),
               "成品": r.get("成品"), "覆核項目": r.get("覆核項目"), "要人聽": r.get("要人聽"), "動到聲音": r.get("動到聲音"),
               "結果": d.get("結果"), "有原因": bool(d.get("原因")),
               "改範圍.改成": (d.get("改範圍") or {}).get("改成")}
        extra = {k: v for k, v in r.items() if k not in row and k not in ("做了什麼", "文字", "檔案")}
        row.update(extra)
        if f.ok(row):
            out.append(fmt_row(row, ("編號", "鍵", "類型", "原片", "成品", "結果", "有原因", "要人聽")))
    out.append(f"要人聽的 {n_flag} 筆")
    for u in log.get("未登記的變動", []):
        d = check["未登記確認"].get(finalcheck.unlogged_key(u), {})
        row = {"鍵": finalcheck.unlogged_key(u), "原片": u.get("原片"), "長度秒": u.get("長度秒"), "結果": d.get("結果"),
               "有原因": bool(d.get("原因"))}
        if f.ok(row):
            out.append("沒登記的變動 " + fmt_row(row))
    for x in check.get("整片退回", []):
        row = {"id": x.get("id"), "成品秒": x.get("成品秒"), "原片秒": x.get("原片秒"), "覆核項目": x.get("覆核項目"),
               "有原因": bool(x.get("原因"))}
        out.append("整片退回 " + fmt_row(row))


WARN_KINDS = (("段落改過、舊的重念不用", ("舊的重念不用", "這一筆舊的重念不用")), ("時間格改過、還沒重新生成", ("時間格改過",)),
              ("跟別筆重疊、被較長的蓋過", ("被較長的那筆蓋過",)), ("跟別筆邊界差一點、修齊", ("邊界疊到",)),
              ("局部消音跟別的處理重疊", ("重疊的地方以那一筆為準",)),
              ("跟別筆重疊、以那一筆為準", ("以那一筆為準",)), ("整筆落在別筆裡", ("整筆落在別筆的範圍裡",)),
              ("還沒生成", ("還沒生成",)), ("局部消音落在剪掉的地方", ("整段落在刪除段落裡",)))
_WARN_CAND_RE = re.compile(r"^候選 \[([^\]]*)\]")


def warn_kind(text: str) -> str:
    for name, keys in WARN_KINDS:
        if any(k in str(text) for k in keys):
            return name
    return "其他"


def topic_decisions(w: Path, f: Filter, out: list[str], tag: str | None, recompute: bool = False) -> None:
    outd = w / "輸出"
    files = sorted(outd.glob("剪輯決策_*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if outd.is_dir() else []
    if tag:
        path = outd / f"剪輯決策_{tag}.json"
    elif files:
        path = files[0]
    else:
        path = w / "生成" / "剪輯決策.json"
    out.append("有的剪輯決策：" + ("、".join(p.stem.removeprefix("剪輯決策_") for p in files) or "（輸出/ 底下沒有）"))
    d = _read(path)
    if not d:
        out.append(f"{path.name} 沒有")
        return
    out.append(f"讀的是 {path.relative_to(w)}；範圍 {val('範圍', d.get('範圍'))}")
    if recompute:
        # 10-04 #61 補修：用現在的程式、現在的工作區資料重新排一次（跟組裝同一個 `render.build_decisions`），
        # 不組裝、不寫檔、不載入模型——看「只重新組裝」會排成什麼樣子
        from bookclub import render

        rng = d.get("範圍") or [0.0, 1e9]
        d = render.build_decisions(w, float(rng[0]), float(rng[1]))
        d["片段"] = render.pieces(float(rng[0]), float(rng[1]), d["刪除"], d["停格"])   # 10-04 #134：重算也印成品長度
        out.append("（--重算：以下是用現在的程式重新排的剪輯決策，還沒組裝；停格、片段以實際組裝為準）")
    acts = d.get("動作") or d.get("edits") or []
    out.append("動作 " + str(len(acts)) + " 筆：" + "、".join(f"{val('類型', k)} {n}" for k, n in Counter(a.get("類型") for a in acts).items()))
    for a in acts:
        row = {k: v for k, v in a.items() if k not in ("text", "生成用文字", "轉回文字", "檔案", "來源檔案")}
        if f.ok(row):
            out.append("  動作 " + fmt_row(row, ("類型", "id", "start", "end", "放回做法", "要人聽", "加快", "停格秒", "停格錯開秒",
                                                  "停格錯開處理", "開頭讓出秒", "結尾多佔原片秒", "空隙秒", "跨筆",
                                                  "疊放")))
    cuts = d.get("刪除") or []
    out.append(f"剪掉 {len(cuts)} 段：" + "、".join(val("範圍", c) for c in cuts[:40]))
    fz = d.get("停格") or []
    out.append(f"停格 {len(fz)} 個：" + "、".join(f"{tfmt(x.get('at'))}（{_num(x.get('dur', 0))} 秒）" for x in fz[:40]))
    for x in fz:   # 10-04 #134：每個停格跟那一格結尾錯開多少、組裝怎麼接（只有數字與固定說法）
        row = {k: x[k] for k in ("at", "dur", "edit", "錯開秒", "錯開處理") if k in x}
        if f.ok(row):
            out.append("  停格 " + fmt_row(row, ("at", "edit", "dur", "錯開秒", "錯開處理")))
    marks = d.get("標記") or []
    out.append("標記 " + str(len(marks)) + " 筆：" + "、".join(f"{val('類型', k)} {n}" for k, n in Counter(m.get("類型") for m in marks).items()))
    for m in marks:
        row = {k: v for k, v in m.items() if k not in ("原因",)}
        if f.ok(row):
            out.append("  標記 " + fmt_row(row, ("類型", "id", "start", "end", "做法")))
    warns = d.get("警告") or []
    out.append("警告 " + str(len(warns)) + " 筆：" + ("、".join(f"{k} {n}" for k, n in Counter(warn_kind(x) for x in warns).items()) or "沒有"))
    _warn_rows(d, acts, f, out)
    plist = d.get("片段")
    if plist:
        from bookclub.render import output_length

        out.append(f"片段 {len(plist)} 段、成品長度 {tfmt(output_length(plist))}、停格合計 {sum(p.get('freeze', 0) for p in plist):.2f} 秒")


def warn_id(text: str) -> str | None:
    """舊的剪輯決策只存警告的文字：開頭的編號（`T038_10 跟別筆重疊⋯`、`T038_10：時間格改過⋯`、`局部消音 M001 ⋯`、
    `學員名字消音 SN001 ⋯`、`候選 [3]`）長得像編號才取出來；不像就回傳 None（不印）。"""
    text = str(text)
    m = _WARN_CAND_RE.match(text)
    if m:
        got = m.group(1).split(",")[0].strip().strip("'\"")
        return got if safe_id(got) else None
    toks = re.split(r"[\s：]+", text, maxsplit=2)
    if toks and safe_id(toks[0]):
        return toks[0]
    if len(toks) >= 2 and toks[0] in VOCAB and safe_id(toks[1]):
        return toks[1]
    return None


def _warn_rows(d: dict, acts: list[dict], f: Filter, out: list[str]) -> None:
    """每一筆警告：類型、涉及的動作 id、起訖（印到小數 3 位）。不印警告的文字。
    10-04 起組裝會存 `警告明細`（類型、id、起訖、蓋過的、疊到秒）；舊的剪輯決策沒有，就從警告開頭的編號
    找剪輯決策裡同編號的動作，跟它疊到的別筆動作從時間算出來（標「推算」）。"""
    details = d.get("警告明細")
    with millis():
        no_filter = f.a is None and f.b is None and not f.ids
        if isinstance(details, list) and details:
            for i, x in enumerate(details, 1):
                if not isinstance(x, dict):
                    continue
                row = {k: v for k, v in x.items() if k != "文字"}
                if no_filter or f.ok(row):
                    out.append(f"  警告 #{i} " + fmt_row(row, ("警告類型", "id", "start", "end", "蓋過的", "疊到的範圍", "疊到秒",
                                                             "處理")))
            return
        for i, text in enumerate(d.get("警告") or [], 1):
            wid = warn_id(text)
            mine = [a for a in acts if wid and str(a.get("id") or a.get("生成編號") or "") == wid]
            row = {"警告類型": warn_kind(text), "id": wid}
            if mine:
                row["start"], row["end"] = min(a["start"] for a in mine), max(a["end"] for a in mine)
                hits = []
                for a in acts:
                    if a in mine:
                        continue
                    x, y = max(a["start"], row["start"]), min(a["end"], row["end"])
                    if y > x:
                        hits.append((a, x, y))
                # 被蓋過改消音的那一筆，蓋過它的通常只是碰到邊（頭尾相接）——也列出碰到邊 0.2 秒以內的
                near = [a for a in acts if a not in mine and (abs(a["end"] - row["start"]) < 0.2 or abs(a["start"] - row["end"]) < 0.2)]
                row["蓋過的"] = [str(a.get("id") or a.get("生成編號") or a.get("類型")) for a, _x, _y in hits] or None
                row["疊到秒"] = round(sum(y - x for _a, x, y in hits), 3)
                row["碰到邊的"] = [str(a.get("id") or a.get("生成編號") or a.get("類型")) for a in near] or None
                row["碰到邊的起訖"] = [[a["start"], a["end"]] for a in near] or None
            if no_filter or f.ok(row):
                line = fmt_row({k: v for k, v in row.items() if k != "碰到邊的起訖"}, ("警告類型", "id", "start", "end", "蓋過的", "疊到秒"))
                if row.get("碰到邊的起訖"):
                    line += "  碰到邊的起訖=" + "、".join(f"{t3(a)}–{t3(b)}" for a, b in row["碰到邊的起訖"])
                out.append(f"  警告 #{i}（推算：舊檔沒有警告明細）" + line)


def topic_convert(w: Path, out: list[str], src: list[float], dst: list[float], tag: str | None) -> None:
    m = timemap.load(w, tag)
    if not m:
        out.append("沒有片段表（第 4 步還沒組裝，或找不到那一份剪輯決策）：還沒有成品時間")
        return
    plist = m.get("片段")
    out.append(f"用的片段表：{m['來源']}；範圍 {val('範圍', m.get('範圍'))}；"
               + ("只換聲音，成品時間＝原片時間" if plist is None else f"{len(plist)} 段、停格合計 {sum(p.get('freeze', 0) for p in plist):.2f} 秒"))
    for t in src:
        o = timemap.to_output(t, m)
        out.append(f"原片 {timemap.t1(t)} → 成品 {timemap.t1(o) if o is not None else '（剪掉了或不在範圍裡）'}")
    for t in dst:
        s = timemap.to_source(t, m)
        out.append(f"成品 {timemap.t1(t)} → 原片 {timemap.t1(s)}")


def topic_silent_edges(w: Path, f: Filter, out: list[str]) -> None:
    from bookclub import silentedge

    hints = silentedge.hints(w)
    out.append(f"老師重念範圍前後沒有人講話（超過 {silentedge.EDGE_SILENT_S} 秒）：{len(hints)} 筆")
    for key, h in hints.items():
        row = {"鍵": key, "生成編號": h["生成編號"], "slot": h["範圍"], "前面沒聲音秒": h["前"], "後面沒聲音秒": h["後"],
               "建議": h["建議"], "可以縮": h["可以縮"], "改法": h.get("改法")}
        if f.ok(row):
            out.append(fmt_row(row))


def topic_room(w: Path, f: Filter, out: list[str]) -> None:
    from bookclub import roomtone

    info = roomtone.load_info(w)
    if not info:
        out.append("還沒挑全片底噪（第 2 步或第 4 步會自動挑）")
        return
    row = {k: v for k, v in info.items() if k != "候選"}
    out.append("全片底噪 " + fmt_row(row, ("start", "end", "dBFS", "已確認")))
    for i, c in enumerate(info.get("候選") or [], 1):
        out.append(f"  候選 {i} " + fmt_row(c, ("start", "end", "dBFS")))


def topic_codes(w: Path, f: Filter, out: list[str]) -> None:
    """這一集用到的代號（10-02 第七批）：每個代號幾個人用、還在用的英文代號與建議（不印本名）。"""
    from collections import Counter as _C

    from bookclub import codeswap, epcodes

    used = _C(epcodes.episode_codes(w).values())
    out.append(f"這一集的代號表：{len(used)} 個代號")
    for code, n in sorted(used.items(), key=lambda kv: str(kv[0])):
        out.append("  " + fmt_row({"代號": code, "人數": n}, ("代號", "人數")))
    # 10-07：自動配、人沒改過的代號（只印個數與代號，不印本名）；進第 3 步的一次性自動配做過沒
    pend = epcodes.auto_unchanged(w)
    out.append(f"自動配（未改過）的代號 {len(pend)} 個：{'、'.join(sorted(c for c in pend.values() if c in epcodes.CODE_POOL)) or '—'}"
               f"；開始前 ③ 自動配過：{'是' if epcodes._load_auto(w).get('開始前③已配') else '否'}")
    rows = codeswap.plan(w)["英文代號"]
    out.append(f"還在用的英文代號 {len(rows)} 個（換代號：bookclub codes convert <工作區> --map 舊=新）")
    for r in rows:
        out.append("  " + fmt_row(r, ("舊", "建議", "存代號的地方", "要念的文字")))
    left = codeswap.leftover_texts(w)
    out.append(f"要念的文字裡還有舊英文代號：{len(left)} 處")
    for x in left:
        out.append("  " + fmt_row({"鍵": x["卡片"], "類型": x["欄位"], "代號": x["代號"]}, ("鍵", "類型")))


TOPICS = {
    "檔案": "工作區有哪些資料檔、大小、修改時間",
    "段落": "學員／老師段落（校對/段落.json）：編號、起訖、說話者代號、內容類型、已確認",
    "句子": "說話者判斷的每一句：編號、起訖、判斷（老師／不是老師⋯）、聲紋分數",
    "字": "逐字稿的字：只印每個字的時間（不印字），以及字跟字之間空超過 0.5 秒的地方",
    "重疊": "重疊（含人工補的）與第 3 步的決定（做法、已確認）",
    "名字": "名字候選與名字決定（做法、已確認、標記），以及上次排的名字處理計畫；--重算 在太短、不確定的句子與老師段落裡的"
            "不是老師句重找一次（不寫檔），列出會新增的候選時間點",
    "消音": "局部消音、剪掉的片段、建議剪掉（第 3 步的決定）",
    "總檢查": "第 4 步開始前總檢查的每一列（鍵、起訖、類別、已按聽過、已按照目前設定做、已看過、新的）；有成品時附成品時間",
    "生成": "生成紀錄（老師／學員／保留原聲）：每一句的時間格、選定、放回做法、要人聽原因、切在講話中、聲音檔在不在、"
            "跟現在的時間格差多少；每次嘗試的長度、語速、分數、種子、第幾版",
    "子程式": "第 4 步每一支子程式：秒數、結束碼、記憶體高峰",
    "成品檢查": "第 5 步：處理紀錄每一筆的通過／退回狀態（不印原因文字）、沒登記的變動、整片退回",
    "剪輯決策": "組裝排出的動作、剪掉、停格、標記、每一筆警告（類型、涉及的 id、起訖到毫秒）；--重算 用現在的程式重新排一次",
    "換算": "原片時間 ↔ 成品時間（--原片 42:59.8 或 --成品 38:26.4，可以給好幾個）",
    "前後沒聲音": "老師重念範圍開頭或結尾有一段沒有字（第六批）：建議範圍、能不能一鍵縮小",
    "底噪": "全片底噪（第六批第五件）：挑到哪一段、音量、確認了沒有、候選",
    "代號": "這一集用到的代號（每個幾個人用，不印本名）、還在用的英文代號與建議的中文、要念的文字裡還有舊英文代號的地方",
}
ALIASES = {"files": "檔案", "turns": "段落", "sentences": "句子", "words": "字", "overlaps": "重疊", "names": "名字",
           "mutes": "消音", "cuts": "消音", "剪掉": "消音", "finalcheck": "總檢查", "gen": "生成", "parts": "子程式",
           "final": "成品檢查", "第5步": "成品檢查", "decisions": "剪輯決策", "convert": "換算", "time": "換算",
           "edges": "前後沒聲音", "room": "底噪", "codes": "代號"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bookclub inspect", description="安全查詢工作區（只印時間、編號、數字、狀態）")
    p.add_argument("workdir", help="工作區路徑")
    p.add_argument("topic", nargs="?", help="主題：" + "、".join(TOPICS))
    p.add_argument("--from", dest="a", help="原片時間從（例如 42:00）")
    p.add_argument("--to", dest="b", help="原片時間到（例如 44:00）")
    p.add_argument("--id", action="append", help="只看這個編號（可以給好幾次；T003、名字:3、S003⋯）")
    p.add_argument("--who", choices=["老師", "學員", "保留原聲"], help="生成：只看哪一種")
    p.add_argument("--tag", help="剪輯決策／換算：用 輸出/剪輯決策_<tag>.json（不給用最新的處理紀錄）")
    p.add_argument("--原片", dest="src", action="append", default=[], help="換算：原片時間")
    p.add_argument("--成品", dest="dst", action="append", default=[], help="換算：成品時間")
    p.add_argument("--要人聽", dest="flagged", action="store_true", help="成品檢查：只列要人聽的")
    p.add_argument("--limit", type=int, default=300, help="字：最多列幾個（預設 300）")
    p.add_argument("--毫秒", dest="ms", action="store_true", help="時間印到小數 3 位（看毫秒級的重疊）")
    p.add_argument("--重算", dest="recompute", action="store_true",
                   help="剪輯決策：用現在的程式重新排一次（不組裝、不寫檔），範圍跟那一份剪輯決策一樣；"
                        "名字：在太短、不確定的句子與老師段落裡的不是老師句重找名字（不寫檔），列出會新增的候選")
    return p


def run(argv: list[str]) -> list[str]:
    from bookclub.review import parse_time

    args = build_parser().parse_args(argv)
    w = Path(args.workdir).expanduser()
    out: list[str] = []
    topic = ALIASES.get(args.topic or "", args.topic)
    if not topic or topic not in TOPICS:
        out.append(("看不懂的主題：" + mask(args.topic) + "。" if args.topic else "") + "可以查的主題：")
        out += [f"  {k}：{v}" for k, v in TOPICS.items()]
        return out
    if not w.is_dir():
        return [f"找不到工作區資料夾（{mask(str(w))}）"]
    f = Filter(parse_time(args.a) if args.a else None, parse_time(args.b) if args.b else None, args.id)
    with read_only(), millis(args.ms):
        if topic == "檔案":
            topic_files(w, f, out)
        elif topic == "段落":
            topic_turns(w, f, out)
        elif topic == "句子":
            topic_sentences(w, f, out)
        elif topic == "字":
            topic_words(w, f, out, args.limit)
        elif topic == "重疊":
            topic_overlaps(w, f, out)
        elif topic == "名字":
            topic_names(w, f, out, args.recompute)
        elif topic == "消音":
            topic_cuts(w, f, out)
        elif topic == "總檢查":
            topic_final_check(w, f, out)
        elif topic == "生成":
            topic_generation(w, f, out, args.who)
        elif topic == "子程式":
            topic_parts(w, f, out)
        elif topic == "成品檢查":
            topic_finalcheck(w, f, out, args.flagged)
        elif topic == "剪輯決策":
            topic_decisions(w, f, out, args.tag, args.recompute)
        elif topic == "換算":
            topic_convert(w, out, [parse_time(x) for x in args.src], [parse_time(x) for x in args.dst], args.tag)
        elif topic == "前後沒聲音":
            topic_silent_edges(w, f, out)
        elif topic == "底噪":
            topic_room(w, f, out)
        elif topic == "代號":
            topic_codes(w, f, out)
    return out


def main(argv: list[str]) -> int:
    import io
    import sys
    from contextlib import redirect_stdout

    buf = io.StringIO()
    try:
        with redirect_stdout(buf):   # 呼叫到的函式自己印的東西（可能含文字）不放出去
            lines = run(argv)
    except Exception as e:  # noqa: BLE001 — 錯誤訊息可能帶到資料內容，只印錯誤種類
        print(f"查詢失敗：{type(e).__name__}（錯誤訊息不印，可能含資料內容）")
        return 1
    sys.stdout.write("\n".join(lines) + "\n")
    return 0
