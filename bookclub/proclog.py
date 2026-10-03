"""第 4 步「AI 處理紀錄」＋「沒登記的變動」檢查（09-29 宇軒）。

為什麼要有：第 5 步成品檢查只看時間軸上標出來的地方，最上面寫「沒列在時間軸上的地方＝原片沒動」。
這句話要成立，就要有一份完整的紀錄，再用聲音自己驗證一次——程式哪裡多動了、少記了，都抓得到。

**處理紀錄**（工作區 `生成/處理紀錄.json`）：第 4 步每個動作一筆——原片起訖、成品起訖（刪除、停格之後）、
做了什麼、對應第 3 步哪一筆（`覆核項目`，例如 `學員段落:T003`、`名字:2`、`刪除段落:S1`）、用了哪個檔案。
資料來自 `render.build_decisions()` 排好的動作（跟 `build_marks()` 同一份），這支只負責整理，不改組裝邏輯。

**沒登記的變動**：原片聲音和新聲音（都在原片時間軸上、刪除與停格之前）每 20 毫秒比一次，找出
「有變動、但不在任何一筆會動到聲音的紀錄範圍內（前後留 0.1 秒）」的地方，列成 `未登記的變動[]`，第 5 步要人確認。
- 「有變動」：這 20 毫秒兩邊相減的音量超過 −60 dBFS，而且比原片那 20 毫秒的音量低不到 26 dB
  （同一個檔案抄過去的地方逐點相同，差是 0；只看絕對值會把很小聲的地方的小改動漏掉，只看相對值會被底噪騙）
- `render video` 的新聲音是刪除、停格之後的成品時間軸，先照片段對照表放回原片時間軸（刪掉的地方用原片補，
  刪除本身有紀錄）再比
- `render audio`（只處理名字、跟原片等長）直接比，整支影片分段讀，不整條放進記憶體

純函式為主：`build_records`、`to_source_timeline`、`changed_frames`、`find_unlogged` 不讀檔，單元測試直接餵合成聲音。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np

from bookclub import workdir as wd
from bookclub.assemble import GAP_KEEP_KIND, GAP_KIND, gap_keep_text, gap_mute_text

FRAME_S = 0.02            # 每 20 毫秒比一次
PAD_S = 0.1               # 紀錄範圍前後各留 0.1 秒（接縫淡入淡出、剪點淡出淡入會碰到旁邊一點點）
ABS_DB = -60.0            # 相減的音量低於這個 → 當作沒變（PCM16 來回的誤差遠低於這個）
REL_DB = -26.0            # 相減的音量比原片低超過這麼多 → 當作沒變
MERGE_GAP_S = 0.2         # 兩處變動中間隔不到 0.2 秒，併成一處（人看的時候是同一件事）

# 會動到聲音的紀錄類型（檢查只拿這些當「有登記」）；模糊是畫面、重疊標記只是說明，不算
AUDIO_KINDS = ("學員重念", "名字整句換掉", "名字消音", "局部消音", "學員名字消音", "學員名字換代號", "刪除", "停格",
               "換聲音", "消音", "學員空隙消音")
# 10-03 第八批 #23：換聲音類（生成的聲音放進原片）＋停格（重念的後半截）：接縫做法改了，這幾類的成品聲音跟著變
SEAM_KINDS = ("學員重念", "名字整句換掉", "換聲音", "學員名字換代號", "停格")


def log_path(workdir: Path) -> Path:
    return Path(workdir) / "生成" / "處理紀錄.json"


# ---------- 紀錄（純函式） ----------

def _ot(t: float, plist: list[dict] | None) -> float | None:
    if plist is None:           # render audio：沒有刪除、停格，成品時間＝原片時間
        return t
    from bookclub.render import to_output_time

    return to_output_time(t, plist)


def _r(x: float | None) -> float | None:
    return None if x is None else round(float(x), 3)


def build_records(d: dict, plist: list[dict] | None, links: dict | None = None) -> list[dict]:
    """`render.build_decisions()` 的結果＋片段對照表 → 處理紀錄每一筆（依原片時間排序、編號從 1 起）。

    links：對回第 3 步覆核項目用的對照（`collect_links` 讀工作區產生）——
    `段落`：{學員重念的生成 id: 段落 id}、`刪除`：[[起, 訖, 項目]]、`重疊`：[[起, 訖, 項目]]。"""
    links = links or {}
    seg = links.get("段落", {})
    recs: list[dict] = []

    def near(pairs: list, s: float, e: float, tol: float = 0.05) -> list[str]:
        return [k for a, b, k in pairs if abs(a - s) <= tol and abs(b - e) <= tol]

    for e in d.get("動作", []):
        kind, s, t = e["類型"], e["start"], e["end"]
        if e.get("併入前一格"):   # #102：很短的空隙照樣墊底噪，不另外列（前一格紀錄的前後 0.1 秒已經涵蓋）
            continue
        rec = {"類型": kind, "原片": [_r(s), _r(t)], "成品": [_r(_ot(s, plist)), _r(_ot(t, plist))],
               "動到聲音": True, "要人聽": bool(e.get("要人聽")), "檔案": None, "文字": e.get("text"), "覆核項目": []}
        if kind == "學員重念":
            tid = seg.get(e["id"]) or str(e["id"]).rsplit("_", 1)[0]
            rec["覆核項目"] = [f"學員段落:{tid}"]
            rec["做了什麼"] = f"{e.get('學員') or '學員'} 用{e.get('聲線') or ''}聲 AI 重念"
            rec["檔案"] = e.get("來源檔案") if e.get("停格秒") or e.get("加快", 1.0) > 1.0 else e.get("檔案")
            if e.get("加快", 1.0) > 1.0:
                rec["做了什麼"] += f"；比時間格長，加快 {e['加快'] - 1:.0%}"
            if e.get("停格秒"):
                rec["做了什麼"] += f"；結尾停格 {e['停格秒']:.2f} 秒"
                rec["停格秒"] = _r(e["停格秒"])   # 10-03 第八批 #23：內容指紋用（不用從「做了什麼」拆）
                if rec["成品"][1] is not None:
                    rec["成品"][1] = _r(rec["成品"][1] + e["停格秒"])
        elif kind == "名字整句換掉":
            rec["覆核項目"] = [f"名字:{c}" for c in e.get("候選", [])] + [f"重疊:{x}" for x in e.get("重疊項目") or []]
            rec["做了什麼"] = "老師提到名字：整句用老師 AI 聲音重念、名字換成代號" if e.get("候選") \
                else "聲音重疊：老師整句用 AI 聲音重念，學員疊在上面的聲音跟著拿掉"
            rec["檔案"] = e.get("檔案")
        elif kind == "名字消音":
            rec["覆核項目"] = [f"名字:{c}" for c in e.get("候選", [])]
            rec["做了什麼"] = "老師提到名字：名字消音（墊環境底噪）"
        elif kind == "局部消音" and e.get("重疊"):
            rec["覆核項目"] = [f"重疊:{e['重疊']}"]
            rec["做了什麼"] = mute_text(e)
        elif kind == "局部消音":
            rec["覆核項目"] = [f"局部消音:{e['id']}"]
            rec["做了什麼"] = mute_text(e)
        elif kind in ("學員名字消音", "學員名字換代號"):
            rec["覆核項目"] = [f"學員名字:{c}" for c in e.get("候選", [])]
            rec["做了什麼"] = student_name_text(e)
            rec["檔案"] = e.get("檔案")
        elif kind == GAP_KIND:   # 10-03 第八批補修 #102
            rec["覆核項目"] = [f"學員段落:{e['段落']}"]
            rec["做了什麼"] = gap_mute_text(e)
            rec["空隙秒"] = _r(e.get("空隙秒"))
        else:
            rec["做了什麼"] = kind
        mark_cut(rec, e)
        recs.append(rec)

    # 10-03 第八批 #61、#104：切在講話中、內容問題（漏了一串字／結尾、開頭可能少念了字）寫進「做了什麼」並標要人聽
    for rec, e in zip(recs, d.get("動作", [])):
        if e.get("內容問題"):
            rec["做了什麼"] += f"；{e['內容問題']}"
            rec["內容問題"] = e["內容問題"]
            rec["要人聽"] = True
        if e.get("切在講話中"):
            pts = [float(x) for x in e["切在講話中"]]
            rec["做了什麼"] += "；切在講話中（找不到停頓）"
            rec["切在講話中"] = [_r(x) for x in pts]
            rec["要人聽"] = True

    for x, y in d.get("刪除", []):
        t = _ot(x, plist) if plist is None or _ot(x, plist) is not None else _ot(y, plist)
        recs.append({"類型": "刪除", "原片": [_r(x), _r(y)], "成品": [_r(t), _r(t)], "動到聲音": True, "要人聽": False,
                     "檔案": None, "文字": None, "覆核項目": near(links.get("刪除", []), x, y),
                     "做了什麼": f"刪除 {y - x:.1f} 秒（聲音畫面一起刪）"})

    by_edit = {e.get("id"): e for e in d.get("動作", [])}
    for f in d.get("停格", []):
        t = _ot(f["at"], plist)
        items = []
        if f.get("edit") in by_edit:
            items = [f"學員段落:{seg.get(f['edit']) or str(f['edit']).rsplit('_', 1)[0]}"]
        elif not f.get("示範"):
            items = [k for a, b, k in links.get("重疊", []) if abs(b - f["at"]) <= 0.05 or a <= f["at"] <= b]
        recs.append({"類型": "停格", "原片": [_r(f["at"]), _r(f["at"])], "成品": [_r(t), _r(t + f["dur"]) if t is not None else None],
                     "動到聲音": True, "要人聽": False, "檔案": None, "文字": None, "覆核項目": items,
                     "停格秒": _r(f["dur"]), "做了什麼": f"停格 {f['dur']:.2f} 秒：{f.get('原因', '')}"})

    if d.get("模糊"):
        s, e = d["模糊"]
        recs.append({"類型": "模糊示範", "原片": [_r(s), _r(e)], "成品": [_r(_ot(s, plist)), _r(_ot(e, plist))],
                     "動到聲音": False, "要人聽": False, "檔案": None, "文字": None, "覆核項目": [],
                     "做了什麼": "畫面右上四分之一模糊（示範）"})

    for m in d.get("標記", []):
        if m["類型"] == "重疊":
            recs.append({"類型": "重疊", "原片": [_r(m["start"]), _r(m["end"])],
                         "成品": [_r(_ot(m["start"], plist)), _r(_ot(m["end"], plist))], "動到聲音": False,
                         "要人聽": False, "檔案": None, "文字": None,
                         "覆核項目": near(links.get("重疊", []), m["start"], m["end"]),
                         "做了什麼": f"重疊（{m.get('做法', '')}）：{m.get('處理', '')}"})
        elif m["類型"] == GAP_KEEP_KIND:   # #102：空隙裡有老師的話／別的處理，原片沒動，要人聽
            recs.append({"類型": GAP_KEEP_KIND, "原片": [_r(m["start"]), _r(m["end"])],
                         "成品": [_r(_ot(m["start"], plist)), _r(_ot(m["end"], plist))], "動到聲音": False,
                         "要人聽": True, "檔案": None, "文字": None, "覆核項目": [f"學員段落:{m['段落']}"],
                         "空隙秒": _r(m.get("空隙秒")), "保留原因": m.get("保留原因"), "做了什麼": gap_keep_text(m)})
        elif m["類型"] == "名字要人處理":
            recs.append({"類型": "名字要人處理", "原片": None, "成品": None, "動到聲音": False, "要人聽": True,
                         "檔案": None, "文字": None, "覆核項目": [f"名字:{m['候選']}"],
                         "做了什麼": f"沒有自動處理：{m.get('原因', '')}（原片沒動）"})

    recs.sort(key=lambda r: (r["原片"][0] if r.get("原片") else 1e12, r["類型"]))
    for i, r in enumerate(recs, start=1):
        r["編號"] = i
    return recs


def mark_cut(rec: dict, e: dict) -> None:
    """10-03 第八批 #60：聲音比時間格長又沒停格、結尾被切掉的（組裝時記在動作的 `結尾切掉秒`）：
    「做了什麼」加一句、要人聽。"""
    if e.get("結尾切掉秒"):
        rec["結尾切掉秒"] = round(float(e["結尾切掉秒"]), 2)
        rec["做了什麼"] = (rec.get("做了什麼") or "") + f"；聲音比時間格長，結尾被切掉 {rec['結尾切掉秒']:.2f} 秒"
        rec["要人聽"] = True


def student_name_text(e: dict) -> str:
    who = e.get("學員") or "學員"
    if e["類型"] == "學員名字消音":
        return f"{who}（保留原聲）講到名字：名字消音（墊環境底噪）"
    return f"{who}（保留原聲）講到名字：用{who}自己的聲音重念這句、名字換成代號——學員聲音生成，音色可能有差"


def mute_text(e: dict) -> str:
    if e.get("重疊"):
        return f"聲音重疊處消音（墊環境底噪，老師的聲音跟著靜音；第 3 步選的做法：{e.get('做法') or '照建議'}）"
    return f"第 3 步標的局部消音 {e['id']}（墊環境底噪" + ("；選的是霧化，霧化還沒做，先墊底噪）" if e.get("霧化") else "）")


def records_from_edl(edits: list[dict]) -> list[dict]:
    """`render audio`（`assemble.build_edl` 的剪輯決策＋局部消音，沒有刪除停格）→ 處理紀錄。"""
    recs = []
    for e in edits:
        swap = e["類型"] == "換聲音"
        local = e["類型"] == "局部消音"
        stu = e["類型"] in ("學員名字消音", "學員名字換代號")
        recs.append({"類型": e["類型"], "原片": [_r(e["start"]), _r(e["end"])], "成品": [_r(e["start"]), _r(e["end"])],
                     "動到聲音": True, "要人聽": bool(e.get("要人聽")),
                     "檔案": e.get("檔案") if swap or e["類型"] == "學員名字換代號" else None,
                     "文字": e.get("文字") or e.get("text"),
                     "覆核項目": [f"局部消音:{e['id']}"] if local else [f"學員名字:{c}" for c in e.get("候選", [])] if stu
                     else [f"名字:{c}" for c in e.get("候選", [])],
                     "做了什麼": mute_text(e) if local else student_name_text(e) if stu
                     else "老師提到名字：用老師 AI 聲音重念、名字換成代號" if swap
                     else "老師提到名字：名字消音（墊環境底噪）"})
        mark_cut(recs[-1], e)
    for i, r in enumerate(recs, start=1):
        r["編號"] = i
    return recs


def audio_spans(records: list[dict]) -> list[tuple[float, float]]:
    """會動到聲音的紀錄範圍（原片時間）。"""
    return [(r["原片"][0], r["原片"][1]) for r in records
            if r.get("動到聲音") and r.get("原片") and r["類型"] in AUDIO_KINDS]


# ---------- 沒登記的變動（純函式） ----------

def to_source_timeline(new: np.ndarray, plist: list[dict], a: float, sr: int, orig: np.ndarray) -> np.ndarray:
    """成品時間軸（刪除、停格之後）的新聲音 → 原片時間軸：每個片段放回原本的位置，停格補的那段丟掉，
    刪掉的地方用原片補（刪除本身有紀錄，不是沒登記的變動）。"""
    out = orig.astype(np.float32).copy()
    acc = 0
    for p in plist:
        s = int(round((p["src"][0] - a) * sr))
        e = int(round((p["src"][1] - a) * sr))
        n = e - s
        chunk = new[acc:acc + n]
        out[s:s + len(chunk)] = chunk
        acc += n + int(round(p["freeze"] * sr))
    return out


def _frame_rms(x: np.ndarray, f: int) -> np.ndarray:
    n = len(x) // f
    if n == 0:
        return np.zeros(0)
    return np.sqrt(np.mean(x[: n * f].astype(np.float64).reshape(n, f) ** 2, axis=1))


def changed_frames(orig: np.ndarray, new: np.ndarray, sr: int, frame_s: float = FRAME_S) -> np.ndarray:
    """每 20 毫秒一格：這一格有沒有變（見檔頭的判斷規則）。長度不一樣時只比到短的那邊。"""
    n = min(len(orig), len(new))
    f = max(1, int(round(sr * frame_s)))
    o, x = orig[:n].astype(np.float64), new[:n].astype(np.float64)
    diff = _frame_rms(x - o, f)
    ref = _frame_rms(o, f)
    return (diff > 10 ** (ABS_DB / 20)) & (diff > ref * 10 ** (REL_DB / 20))


def find_unlogged(flags: np.ndarray, spans: list[tuple[float, float]], *, offset: float = 0.0,
                  frame_s: float = FRAME_S, pad: float = PAD_S, merge_gap: float = MERGE_GAP_S) -> list[dict]:
    """有變動的格子扣掉紀錄範圍（前後各留 pad）→ 一處一筆 {原片:[起,訖], 長度秒, 格數}（原片絕對秒數）。"""
    t0 = offset + np.arange(len(flags)) * frame_s          # 每一格的起點
    covered = np.zeros(len(flags), dtype=bool)
    for s, e in spans:
        covered |= (t0 + frame_s > s - pad) & (t0 < e + pad)
    idx = np.flatnonzero(flags & ~covered)
    out: list[dict] = []
    for k in idx:
        s, e = float(t0[k]), float(t0[k] + frame_s)
        if out and s - out[-1]["原片"][1] <= merge_gap + 1e-9:
            out[-1]["原片"][1] = e
            out[-1]["格數"] += 1
        else:
            out.append({"原片": [s, e], "格數": 1})
    for u in out:
        u["原片"] = [round(u["原片"][0], 3), round(u["原片"][1], 3)]
        u["長度秒"] = round(u["原片"][1] - u["原片"][0], 3)
    return out


def check_arrays(orig: np.ndarray, new_src: np.ndarray, sr: int, records: list[dict], offset: float = 0.0) -> dict:
    flags = changed_frames(orig, new_src, sr)
    found = find_unlogged(flags, audio_spans(records), offset=offset)
    return {"未登記的變動": found, "檢查": {"每格秒": FRAME_S, "前後留秒": PAD_S, "比了幾格": int(len(flags)),
                                     "有變動的格數": int(flags.sum()), "未登記處數": len(found)}}


def check_files(orig_path: Path, new_path: Path, records: list[dict], block_s: float = 60.0) -> dict:
    """兩個跟原片等長的聲音檔（`render audio` 的原聲音軌／新聲音軌）分段比，不整條讀進記憶體。"""
    import soundfile as sf

    parts = []
    with sf.SoundFile(str(orig_path)) as fo, sf.SoundFile(str(new_path)) as fn:
        sr = fo.samplerate
        f = int(round(sr * FRAME_S))
        block = max(f, int(block_s * sr) // f * f)
        while True:
            o = fo.read(block, dtype="float32")
            x = fn.read(block, dtype="float32")
            if len(o) == 0 or len(x) == 0:
                break
            if o.ndim > 1:
                o = o.mean(axis=1)
            if x.ndim > 1:
                x = x.mean(axis=1)
            parts.append(changed_frames(o, x, sr))
    flags = np.concatenate(parts) if parts else np.zeros(0, dtype=bool)
    found = find_unlogged(flags, audio_spans(records))
    return {"未登記的變動": found, "檢查": {"每格秒": FRAME_S, "前後留秒": PAD_S, "比了幾格": int(len(flags)),
                                     "有變動的格數": int(flags.sum()), "未登記處數": len(found)}}


def check_render_files(orig_path: Path, new_path: Path, plist: list[dict], a: float, records: list[dict],
                       block_s: float = 60.0) -> dict:
    """`render video` 的原聲（原片時間軸）與新聲音（成品時間軸，刪除、停格之後）分段比（09-30）。
    結果跟 `check_arrays(orig, to_source_timeline(new, …))` 一樣，但一次只讀一塊（預設 60 秒），
    也不轉 float64 整條相減——整支 98 分鐘以前要十幾 GB 記憶體。"""
    import soundfile as sf

    parts = []
    with sf.SoundFile(str(orig_path)) as fo, sf.SoundFile(str(new_path)) as fn:
        sr = fo.samplerate
        f = int(round(sr * FRAME_S))
        block = max(f, int(block_s * sr) // f * f)      # 每塊是整數格，跟整條一起算的格子對得上
        maps, acc = [], 0                                 # 每個片段：原片 [s, e) ← 新聲音從 acc 開始
        for p in plist:
            s = int(round((p["src"][0] - a) * sr))
            e = int(round((p["src"][1] - a) * sr))
            maps.append((s, e, acc))
            acc += (e - s) + int(round(p["freeze"] * sr))
        pos = 0
        while True:
            o = fo.read(block, dtype="float32")
            if len(o) == 0:
                break
            if o.ndim > 1:
                o = o.mean(axis=1)
            x = o.copy()
            for s, e, off in maps:
                lo, hi = max(s, pos), min(e, pos + len(o))
                if lo >= hi:
                    continue
                fn.seek(min(off + (lo - s), fn.frames))
                r = fn.read(hi - lo, dtype="float32")
                if r.ndim > 1:
                    r = r.mean(axis=1)
                x[lo - pos:lo - pos + len(r)] = r
            parts.append(changed_frames(o, x, sr))
            pos += len(o)
    flags = np.concatenate(parts) if parts else np.zeros(0, dtype=bool)
    found = find_unlogged(flags, audio_spans(records), offset=a)
    return {"未登記的變動": found, "檢查": {"每格秒": FRAME_S, "前後留秒": PAD_S, "比了幾格": int(len(flags)),
                                     "有變動的格數": int(flags.sum()), "未登記處數": len(found)}}


# ---------- 讀寫工作區 ----------

def file_print(path: Path) -> str | None:
    """生成檔的內容指紋（sha1 前 16 碼）：同一個路徑換了新檔也認得出來。讀不到回 None。"""
    import hashlib

    try:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()[:16]
    except OSError:
        return None


def stamp_contents(workdir: Path, recs: list[dict]) -> list[dict]:
    """10-03 第八批 #23：每一筆補內容指紋要的兩樣（第 5 步 `finalcheck.content_print` 用）——
    `檔案指紋`（用了生成檔的才有）、`接縫做法版本`（換聲音類與停格，`assemble.SEAM_VERSION`）。直接改傳進來的。"""
    from bookclub.assemble import SEAM_VERSION

    cache: dict[str, str | None] = {}
    for r in recs:
        f = r.get("檔案")
        if f:
            if f not in cache:
                cache[f] = file_print(Path(workdir) / f)
            if cache[f]:
                r["檔案指紋"] = cache[f]
        if r.get("類型") in SEAM_KINDS:
            r["接縫做法版本"] = SEAM_VERSION
    return recs


def collect_links(workdir: Path, d: dict) -> dict:
    """對回第 3 步覆核項目要的對照表（讀學員紀錄、覆核決定、重疊）。"""
    from bookclub import overlap as overlap_mod
    from bookclub import render, review, students

    workdir = Path(workdir)
    st = wd.read_json(students.log_path(workdir), default=None) or {}
    seg = {r["id"]: r.get("段落") for r in st.get("句子", []) if r.get("段落")}
    dec = review.load_decisions(workdir)
    cuts = [[render.snap(c["start"]), render.snap(c["end"]), f"刪除段落:{c.get('建議id') or c['id']}"]
            for c in dec["刪除段落"] if c.get("狀態") != "還原"]
    ov = wd.read_json(wd.overlap_path(workdir), default=None) or {"overlaps": []}
    overlap_mod.apply_simple_filters(ov)
    ovs = [[o["start"], o["end"], f"重疊:{review.overlap_id(o)}"]
           for o in review.effective_overlaps(workdir, ov.get("overlaps", []), dec)]
    return {"段落": seg, "刪除": cuts, "重疊": ovs}


def _write(workdir: Path, data: dict) -> dict:
    wd.write_json(log_path(workdir), data)
    n = len(data["未登記的變動"])
    print(f"[處理紀錄] {len(data['紀錄'])} 筆 → {log_path(workdir)}；"
          + (f"⚠️ 有 {n} 處聲音變了但沒有紀錄，第 5 步要人確認" if n else "沒有未登記的變動"))
    return data


def write_render_log(workdir: str | Path, d: dict, plist: list[dict], orig_path: Path, new_path: Path,
                     tag: str) -> dict:
    """`render video` 組完聲音之後呼叫（一行）：寫 `生成/處理紀錄.json`。"""
    workdir = Path(workdir)
    recs = stamp_contents(workdir, build_records(d, plist, collect_links(workdir, d)))
    a = d["範圍"][0]
    chk = check_render_files(orig_path, new_path, plist, a, recs)   # 09-30：分段讀、分段比，不整條讀進來
    return _write(workdir, {"版本": 1, "來源": f"render video {tag}", "範圍": d["範圍"],
                            "產生時間": datetime.now().isoformat(timespec="seconds"),
                            "原聲": str(Path(orig_path).relative_to(workdir)), "新聲音": str(Path(new_path).relative_to(workdir)),
                            "片段": plist, "紀錄": recs, **chk})


def write_audio_log(workdir: str | Path, edits: list[dict], orig_path: Path, new_path: Path) -> dict:
    """`render audio` 組完新聲音軌之後呼叫（一行）：寫 `生成/處理紀錄.json`。"""
    workdir = Path(workdir)
    recs = stamp_contents(workdir, records_from_edl(edits))
    chk = check_files(orig_path, new_path, recs)
    return _write(workdir, {"版本": 1, "來源": "render audio", "範圍": None,
                            "產生時間": datetime.now().isoformat(timespec="seconds"),
                            "原聲": str(Path(orig_path).relative_to(workdir)), "新聲音": str(Path(new_path).relative_to(workdir)),
                            "片段": None, "紀錄": recs, **chk})


def load(workdir: str | Path) -> dict | None:
    return wd.read_json(log_path(Path(workdir)), default=None)
