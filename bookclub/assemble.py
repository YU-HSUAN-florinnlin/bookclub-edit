"""流程第 5 步（三）：把生成的聲音、消音放回原本的時間，組成一條新的聲音軌。

開發計畫 03 §2.5：所有「換聲音」動作都在原始時間軸上做，產出一條跟原片等長
的新聲音軌；刪除段落與停格留到最後一次套用（還沒做）。

- 聲音從**原片影片**重新抽（48kHz 單聲道，存成 `輸出/原聲音軌.wav`），不用分析用的 16kHz
  `audio.flac`；只讀寫有動到的段落，整支影片不放進記憶體
- 換聲音：生成檔（24kHz）轉成 48kHz、裁補成剛好等於時間格，音量對齊原本那一段，
  底下墊底噪、頭尾各 10 毫秒從底噪淡入／淡出到底噪，整格不留原片（10-03 第八批 #60；以前跟原片交叉淡入淡出）
- 消音：墊環境底噪——在前後 20 秒內找最安靜的 0.5 秒（沒人說話的地方），重複鋪滿
- 兩筆重疊時，時間長的（整句換掉）蓋過短的（名字消音）

產出（工作區 `輸出/`）：
- `新聲音軌.wav`：跟原片等長，可以直接交給剪輯軟體換掉原本的聲音
- `處理前後/`：每一筆前後各 2 秒的「處理前」「處理後」試聽檔，給第 5 步成品檢查
- `處理前後.html`：試聽頁（只放代號後的文字，不放本名）
- `生成/剪輯決策.json`：這次套用了哪些動作
"""

from __future__ import annotations

import html
import subprocess
from pathlib import Path

import numpy as np

from bookclub import workdir as wd

SR = 48000
FADE_S = 0.01
ROOM_SEARCH_S = 20.0
ROOM_WIN_S = 0.5
CONTEXT_S = 2.0
ACTIVE_DB = -40.0   # 算音量時只看比最大聲低不到 40 dB 的音框（講話的部分）
# 接縫做法版本（10-03 第八批 #23）：換聲音類頭尾怎麼接、停格點怎麼接改了就加 1。處理紀錄每一筆換聲音類都記這個號碼，
# 第 5 步的內容指紋含它——接縫改了，換聲音類的每一筆回到還沒看（宇軒 10-03 定：還是要聽一下）
# 1：頭尾各 10 毫秒跟原片交叉淡入淡出（09 月到 10-03）
# 2：（10-03 第八批 #60）換聲音類頭尾 10 毫秒從底噪淡入、淡出到底噪，整格不留原片；停格點前後兩截直接接上
SEAM_VERSION = 2
CUT_MARK_S = 0.05   # 聲音比時間格長、又沒停格：結尾被切掉超過這麼多秒，第 5 步標出來（更短的是取樣換算的零頭）


def out_dir(workdir: Path) -> Path:
    return workdir / "輸出"


def edl_path(workdir: Path) -> Path:
    return workdir / "生成" / "剪輯決策.json"


# ---------- 剪輯決策（純函式） ----------

def build_edl(plan: dict, teacher_log: dict | None) -> tuple[list[dict], list[str]]:
    """名字處理計畫＋老師生成紀錄 → 剪輯決策清單（依時間排序、已處理重疊）與警告。"""
    records = {r["id"]: r for r in (teacher_log or {}).get("句子", [])}
    edits, warnings = [], []
    for g in plan.get("生成", []):
        r = records.get(g["id"])
        if not r or not r.get("放回時間格"):
            warnings.append(f"{g['id']} 還沒生成，這次先不放")
            continue
        edits.append({
            "類型": "換聲音", "start": g["slot"][0], "end": g["slot"][1], "檔案": r["放回時間格"]["檔案"],
            "文字": g["text"], "候選": g["候選"], "要人聽": r.get("要人聽", False), "生成編號": g["id"],
            **({"重疊項目": g["重疊項目"]} if g.get("重疊項目") else {}),
            **({"疊放": True, "重疊": g["重疊項目"][0]} if g.get("疊放") else {}),
        })
    for m in plan.get("消音", []):
        edits.append({"類型": "消音", "start": m["start"], "end": m["end"], "候選": [m["候選"]]})

    # 重疊：長的優先（B 方案兩邊都生成的那一對不算搶，見 stackable）；10-02 第七批：短的只扣掉疊到的部分
    kept, w = resolve_overlaps(edits, label=lambda e: f"候選 {e['候選']}")
    return kept, warnings + w


def stackable(e: dict, k: dict) -> bool:
    """兩筆動作可以疊在一起放（10-01 B 方案）：同一處重疊、兩邊都標了疊放（學員一句＋老師一句，聲音相加）。"""
    return bool(e.get("疊放") and k.get("疊放") and e.get("重疊") and e.get("重疊") == k.get("重疊"))


MUTE_KINDS = ("消音", "名字消音", "局部消音", "學員名字消音", "學員空隙消音")   # 墊環境底噪的動作（其他是換聲音）
# 墊底噪時整段直接換成底噪、頭尾不跟原片交叉淡入淡出的（#102：空隙兩邊是重念的格子，交叉淡入會混進 10 毫秒學員原聲）
HARD_MUTE_KINDS = ("學員空隙消音",)
SWAP_KINDS = ("換聲音", "學員重念", "名字整句換掉")
# 10-02 第七批（A2）：換聲音被較長的那筆蓋過一部分時，沒蓋到的部分改成哪一種消音
SWAP_TO_MUTE = {"換聲音": "消音", "名字整句換掉": "名字消音", "學員重念": "局部消音", "學員名字換代號": "學員名字消音"}
NAME_LEFT_TOL_S = 0.05   # 名字還有超過這個秒數沒被任何動作蓋到 → 不輸出成品


def _as_mute(e: dict, s: float, t: float) -> dict:
    """換聲音那一筆沒被蓋到的 [s, t]，改成消音（只留編號、候選、學員，不帶生成檔）。"""
    kind = SWAP_TO_MUTE.get(e["類型"], "局部消音")
    out = {k: e[k] for k in ("id", "候選", "學員", "生成編號") if k in e}
    out.update({"類型": kind, "start": s, "end": t, "被蓋過改消音": e["類型"]})
    out.setdefault("候選", [])
    if kind == "局部消音":
        out.setdefault("id", e.get("生成編號") or e["類型"])
        out.update({"方式": "墊底噪", "霧化": False})
    return out


EDGE_TRIM_TOL_S = 0.1   # 10-04 #61 補修：換聲音類只在頭或尾跟別筆疊到這麼多秒以內 → 修齊邊界、整筆照做（不改成消音）


def _ref_id(e: dict) -> str:
    return str(e.get("id") or e.get("生成編號") or e["類型"])


def resolve_overlaps(edits: list[dict], kept: list[dict] | None = None, *, longest_first: bool = True,
                     label=None, details: list[dict] | None = None) -> tuple[list[dict], list[str]]:
    """兩筆動作疊到時怎麼辦（純函式，10-02 第七批 A2）。以前疊到一點點，短的那筆整筆丟掉，名字會留在成品。

    - `kept` 是已經排好、優先的動作；`edits` 一筆一筆加進去（longest_first：長的先加）。
    - 疊到的（`stackable` 的那一對不算）：消音類只扣掉疊到的部分，剩下的照做；換聲音類不能只放一半，
      沒被蓋到的部分改成消音（`_as_mute`）。整筆都被蓋到的不重複處理。
    - 10-04 #61 補修：換聲音類只有頭或尾跟別筆疊到 EDGE_TRIM_TOL_S 秒以內（例如沿用的舊格跟新切的鄰格差 0.03 秒、
      兩個段落的起訖差 0.004 秒）→ 把這一筆的頭／尾修齊到那一筆的邊界、整筆照樣換聲音，不再整格變底噪。
      疊到的那一點點由那一筆處理（換聲音或消音），不會留原聲。疊到的比這多、或疊在中間，照舊改成消音。
    `details`：給一個清單就把每一筆警告的明細（警告類型、id、起訖、蓋過的、疊到秒、處理）加進去（`inspect 剪輯決策` 印）。
    回傳（依時間排序的動作、警告）。"""
    label = label or (lambda e: _ref_id(e))
    out, warnings = list(kept or []), []
    todo = sorted(edits, key=lambda e: -(e["end"] - e["start"])) if longest_first else list(edits)

    def note(kind: str, e: dict, hits: list[dict], how: str) -> None:
        if details is None:
            return
        spans = [[max(e["start"], k["start"]), min(e["end"], k["end"])] for k in hits]
        details.append({"警告類型": kind, "id": _ref_id(e), "類型": e["類型"], "start": e["start"], "end": e["end"],
                        "蓋過的": [_ref_id(k) for k in hits], "疊到的範圍": spans[0] if len(spans) == 1 else None,
                        "疊到秒": round(sum(b - a for a, b in spans), 3), "處理": how})

    for e in todo:
        hits = [k for k in out if e["start"] < k["end"] and k["start"] < e["end"] and not stackable(e, k)]
        if not hits:
            out.append(e)
            continue
        block = [(k["start"], k["end"]) for k in hits]
        parts = subtract(e["start"], e["end"], block)
        if not parts:
            warnings.append(f"{label(e)} 整筆落在別筆的範圍裡，跟著那一筆處理")
            note("整筆落在別筆裡", e, hits, "跟著那一筆")
            continue
        left = sum(t - s for s, t in parts)
        covered = (e["end"] - e["start"]) - left
        swap = e["類型"] in SWAP_KINDS or e["類型"] in SWAP_TO_MUTE
        if swap and len(parts) == 1 and covered <= EDGE_TRIM_TOL_S + 1e-9:
            s, t = parts[0]
            out.append({**e, "start": s, "end": t, "邊界修齊秒": round(covered, 3)})
            warnings.append(f"{label(e)} 跟別筆邊界疊到 {covered:.3f} 秒，頭尾修齊到那一筆，整筆照做")
            note("跟別筆邊界差一點、修齊", e, hits, "修齊照做")
            continue
        if swap:
            out += [_as_mute(e, s, t) for s, t in parts]
            warnings.append(f"{label(e)} 跟別筆重疊，疊到的部分以那一筆為準，沒蓋到的 {left:.2f} 秒改成消音")
            note("跟別筆重疊、以那一筆為準", e, hits, "改成消音")
        else:
            out += [{**e, "start": s, "end": t} for s, t in parts]
            warnings.append(f"{label(e)} 跟別筆重疊，疊到的部分以那一筆為準，其餘 {left:.2f} 秒照做")
            note("跟別筆重疊、以那一筆為準", e, hits, "其餘照做")
    out.sort(key=lambda e: e["start"])
    return out, warnings


def names_left(ranges: list[dict], edits: list[dict], cuts: list[tuple[float, float]] = (),
               a: float = 0.0, b: float = 1e12, tol: float = NAME_LEFT_TOL_S) -> list[dict]:
    """名字還有沒有地方沒被任何動作蓋到（純函式，10-02 第七批 A2 最後一道檢查）。
    `ranges`：[{候選, start, end}]（名字處理計畫裡每一筆的範圍）；換聲音、消音、剪掉都算蓋到。
    回傳沒蓋到超過 tol 秒的：[{候選, start, end, 沒處理秒, 沒處理的範圍}]。"""
    cover = [(e["start"], e["end"]) for e in edits] + list(cuts)
    out = []
    for r in ranges:
        x, y = max(r["start"], a), min(r["end"], b)
        if y <= x:
            continue
        rest = subtract(x, y, cover)
        sec = sum(t - s for s, t in rest)
        if sec > tol:
            out.append({**r, "沒處理秒": round(sec, 3), "沒處理的範圍": [[round(s, 3), round(t, 3)] for s, t in rest]})
    return out


def plan_name_ranges(plan: dict, stu_plan: dict | None = None) -> list[dict]:
    """名字處理計畫（老師）與保留原聲學員講到名字的計畫 → 每一筆名字要被處理的範圍。"""
    out = [{"候選": m["候選"], "start": m["start"], "end": m["end"]} for m in plan.get("消音", [])]
    out += [{"候選": g.get("候選"), "start": g["slot"][0], "end": g["slot"][1], "生成編號": g.get("id")}
            for g in plan.get("生成", [])]
    for m in (stu_plan or {}).get("消音", []):
        out.append({"候選": m["id"], "start": m["start"], "end": m["end"], "學員": m.get("學員")})
    for g in (stu_plan or {}).get("生成", []):
        out.append({"候選": g.get("候選"), "start": g["slot"][0], "end": g["slot"][1], "生成編號": g.get("id"),
                    "學員": g.get("學員")})
    return out


def subtract(a: float, b: float, blockers: list[tuple[float, float]], min_len: float = 0.01) -> list[tuple[float, float]]:
    """[a, b] 扣掉 blockers 蓋到的部分，回傳剩下的小段（純函式；短於 min_len 秒的零頭不要）。"""
    parts = [(a, b)]
    for x, y in sorted(blockers):
        nxt = []
        for s, e in parts:
            if y <= s or x >= e:
                nxt.append((s, e))
                continue
            if s < x:
                nxt.append((s, x))
            if y < e:
                nxt.append((y, e))
        parts = nxt
    return [(s, e) for s, e in parts if e - s >= min_len]


# ---------- 學員段落裡兩格之間的空隙（10-03 第八批補修 #102） ----------
# 起因：學員重念把一個學員段落切成好幾格，每格是「第一句開始～最後一句結束」；格子之間句子不相連就有空隙，
# 以前沒有任何動作蓋到、組裝時播原片——逐字稿的句子結束時間比實際早時，學員原聲的尾巴留在成品裡（T034）。
# 宇軒定（B）：同一個學員段落裡相鄰兩格之間的空隙整段墊底噪；空隙裡有老師的話或別的處理時保留原聲、標要人聽。
# 段落最前面、最後面（第一格之前、最後一格之後）不動。
GAP_KIND = "學員空隙消音"            # 墊底噪（MUTE_KINDS 之一），處理紀錄對到 學員段落:<id>
GAP_KEEP_KIND = "學員空隙保留原聲"    # 標記：空隙裡有老師的話／別的處理，原片沒動、要人聽
GAP_RECORD_MIN_S = 0.05   # 墊底噪的空隙短於這個秒數：照樣墊，但處理紀錄不另外列一筆（前一格紀錄的前後 0.1 秒已經涵蓋）
GAP_TEACHER_TOL_S = 0.05  # 老師的句子跟空隙重疊超過這個秒數才算「空隙裡有老師的話」（逐字稿起訖的誤差不算）
GAP_MIN_S = 0.001         # 比這短的空隙（取樣換算的零頭）不處理


def student_gaps(chunks: list[dict], a: float = 0.0, b: float = 1e12) -> list[dict]:
    """學員重念的每一格 → 同一個學員段落裡相鄰兩格之間的空隙（純函式）。
    `chunks`：[{id, 段落, slot}]；回傳 [{段落, start, end, 前一格, 後一格}]（依時間排序、切到 [a, b] 裡）。
    段落最前面、最後面不算空隙。"""
    by: dict[str, list[dict]] = {}
    for c in chunks:
        by.setdefault(c["段落"], []).append(c)
    out = []
    for tid, cs in by.items():
        cs = sorted(cs, key=lambda c: c["slot"][0])
        for p, n in zip(cs, cs[1:]):
            s, t = max(p["slot"][1], a), min(n["slot"][0], b)
            if t - s > GAP_MIN_S:
                out.append({"段落": tid, "start": s, "end": t, "前一格": p["id"], "後一格": n["id"]})
    out.sort(key=lambda g: g["start"])
    return out


def _own(e: dict, tid: str) -> bool:
    """這一筆動作是不是這個學員段落自己的格子（學員重念、被蓋過改消音、時間格改過先消音、刪光的格子都帶格子的 id）。"""
    return str(e.get("id") or "").startswith(f"{tid}_") and e["類型"] in ("學員重念", "局部消音")


def gap_edits(gaps: list[dict], edits: list[dict], teacher: list[tuple[float, float]] = (),
              cuts: list[tuple[float, float]] = (), busy: list[tuple[float, float]] = ()) -> tuple[list[dict], list[dict]]:
    """空隙要怎麼處理（純函式，#102）。回傳（要加的墊底噪動作、保留原聲的標記）。

    - 剪掉的部分、這個段落自己的格子蓋到的部分不用處理
    - 剩下的部分有老師的句子（`teacher`，重疊超過 GAP_TEACHER_TOL_S）、或別的動作（名字、重疊、局部消音）、
      或 `busy`（例如選了「不用改」的重疊）碰到 → 整個空隙保留原片，加一筆 GAP_KEEP_KIND 標記（要人聽）
    - 不然整段墊底噪（GAP_KIND）；短於 GAP_RECORD_MIN_S 的帶 `併入前一格`（處理紀錄不另外列）"""
    new, marks = [], []
    for g in gaps:
        tid = g["段落"]
        own = [(e["start"], e["end"]) for e in edits if _own(e, tid)]
        free = [p for s, t in subtract(g["start"], g["end"], list(cuts), GAP_MIN_S)
                for p in subtract(s, t, own, GAP_MIN_S)]
        if not free:
            continue

        def hits(spans, tol: float = 0.0) -> list[tuple[float, float]]:
            return [(x, y) for x, y in spans
                    if any(min(y, t) - max(x, s) > tol for s, t in free)]

        others = hits([(e["start"], e["end"]) for e in edits if not _own(e, tid)])
        tea = hits(list(teacher), GAP_TEACHER_TOL_S)
        bz = hits(list(busy))
        if tea or others or bz:
            left = [p for s, t in free for p in subtract(s, t, others, GAP_MIN_S)]
            sec = sum(t - s for s, t in left)
            if sec >= GAP_RECORD_MIN_S:
                marks.append({"類型": GAP_KEEP_KIND, "start": round(left[0][0], 3), "end": round(left[-1][1], 3),
                              "段落": tid, "前一格": g["前一格"], "後一格": g["後一格"], "空隙秒": round(sec, 3),
                              "保留原因": "老師的話" if tea else "別的處理"})
            continue
        for s, t in free:
            sec = round(t - s, 3)
            new.append({"類型": GAP_KIND, "start": s, "end": t, "id": f"空隙:{g['前一格']}", "段落": tid,
                        "前一格": g["前一格"], "後一格": g["後一格"], "候選": [], "空隙秒": sec,
                        **({"併入前一格": True} if sec < GAP_RECORD_MIN_S else {})})
    return new, marks


SWAP_GAP_MAX_S = 0.1   # 10-05 #134（宇軒第三點）：兩筆換聲音之間只隔這麼短（跟 EDGE_TRIM_TOL_S 同一個量級）→ 墊底噪


def swap_gap_edits(edits: list[dict], teacher: list[tuple[float, float]] = (), cuts: list[tuple[float, float]] = (),
                   busy: list[tuple[float, float]] = (), max_s: float = SWAP_GAP_MAX_S) -> list[dict]:
    """兩筆換聲音動作（學員重念、名字整句換掉、學員名字換代號；不同段落或不同類型）之間只隔很短的空隙
    （GAP_MIN_S < 空隙 ≤ max_s）時，那一小段以前播原片（可能是學員原聲的尾巴）→ 墊底噪（純函式，10-05 #134）。
    同一個學員段落相鄰兩格的空隙 #102 已經處理（`gap_edits`），這裡碰到別的動作就跳過、不重複。
    不處理：空隙裡有別的動作、剪掉的部分、`busy`（例如選了「不用改」的重疊）、老師的句子（重疊超過 GAP_TEACHER_TOL_S），
    或是疊放的動作。回傳要加的墊底噪動作（類型 GAP_KIND、`跨筆`＝True）。"""
    kinds = ("學員重念", "名字整句換掉", "學員名字換代號", "換聲音")
    sw = sorted([e for e in edits if e["類型"] in kinds and not e.get("疊放")], key=lambda e: (e["start"], e["end"]))
    out = []
    for p_, n_ in zip(sw, sw[1:]):
        g0, g1 = p_["end"], n_["start"]
        if not (GAP_MIN_S < g1 - g0 <= max_s + 1e-9):
            continue
        if any(e["start"] < g1 - GAP_MIN_S and e["end"] > g0 + GAP_MIN_S for e in edits if e is not p_ and e is not n_):
            continue
        if any(x < g1 and y > g0 for x, y in list(cuts) + list(busy)):
            continue
        if any(min(y, g1) - max(x, g0) > GAP_TEACHER_TOL_S for x, y in teacher):
            continue
        sec = round(g1 - g0, 3)
        tid = next((e.get("段落") or str(e["id"]).rsplit("_", 1)[0] for e in (p_, n_) if e["類型"] == "學員重念"), None)
        out.append({"類型": GAP_KIND, "start": g0, "end": g1, "id": f"空隙:{_ref_id(p_)}", "段落": tid,
                    "前一格": _ref_id(p_), "後一格": _ref_id(n_), "候選": [], "空隙秒": sec, "跨筆": True,
                    **({"併入前一格": True} if sec < GAP_RECORD_MIN_S else {})})
    return out


def gap_keep_text(m: dict) -> str:
    """保留原聲那一筆的說明（處理紀錄「做了什麼」、標記清單共用）。"""
    sec = float(m.get("空隙秒") or 0.0)
    if m.get("保留原因") == "老師的話":
        return f"學員段落中間有老師的話，這 {sec:.2f} 秒保留原聲，請聽有沒有學員的聲音"
    return f"學員段落中間有別的處理（名字、重疊或消音），其餘 {sec:.2f} 秒保留原聲，請聽有沒有學員的聲音"


def gap_mute_text(e: dict) -> str:
    if e.get("跨筆"):   # 10-05 #134
        return f"兩筆換聲音之間的空隙 {float(e.get('空隙秒') or (e['end'] - e['start'])):.2f} 秒墊底噪（不留原聲）"
    return f"學員段落裡兩格之間的空隙 {float(e.get('空隙秒') or (e['end'] - e['start'])):.2f} 秒墊底噪（不留原聲）"


def local_mutes(dec: dict) -> list[dict]:
    """第 3 步標的局部消音（`覆核決定.json` 的 `局部消音[]`，狀態不是「還原」的）。"""
    return [m for m in dec.get("局部消音", []) if m.get("狀態") != "還原" and m.get("end", 0) > m.get("start", 0)]


def add_local_mutes(edits: list[dict], mutes: list[dict], cuts: list[tuple[float, float]] = (),
                    details: list[dict] | None = None) -> tuple[list[dict], list[str]]:
    """把局部消音加進剪輯決策（純函式，09-29 宇軒：局部消音保留，標了就要真的消）。

    - 跟刪除段落重疊的部分不用消（已經剪掉）
    - 跟既有的動作（換聲音、名字消音）重疊的部分以既有那筆為準，列在警告裡
    - 霧化還沒做，先一樣墊底噪（`霧化` 欄位記下來，處理紀錄註明）
    回傳（加好、依時間排序的剪輯決策、警告）。"""
    out, warnings = list(edits), []
    taken = [(e["start"], e["end"]) for e in edits]
    for m in mutes:
        ov = m.get("重疊")   # 重疊處的消音（`overlap_mutes`）：被學員重念蓋到是正常的，不列警告
        free = subtract(m["start"], m["end"], list(cuts))
        if not free:
            if not ov:
                warnings.append(f"局部消音 {m['id']} 整段落在刪除段落裡，不用消")
                if details is not None:
                    details.append({"警告類型": "局部消音落在剪掉的地方", "id": m["id"], "類型": "局部消音",
                                    "start": m["start"], "end": m["end"]})
            continue
        parts = [p for s, e in free for p in subtract(s, e, taken)]
        if not ov and (len(parts) != len(free) or sum(e - s for s, e in parts) < sum(e - s for s, e in free) - 0.01):
            warnings.append(f"局部消音 {m['id']} 跟換聲音或名字的處理重疊，重疊的地方以那一筆為準")
            if details is not None:
                hits = [k for k in edits if k["start"] < m["end"] and m["start"] < k["end"]]
                details.append({"警告類型": "局部消音跟別的處理重疊", "id": m["id"], "類型": "局部消音",
                                "start": m["start"], "end": m["end"], "蓋過的": [_ref_id(k) for k in hits],
                                "疊到秒": round(sum(min(k["end"], m["end"]) - max(k["start"], m["start"]) for k in hits), 3),
                                "處理": "其餘照做"})
        for s, e in parts:
            out.append({"類型": "局部消音", "start": s, "end": e, "id": m["id"], "方式": m.get("方式", "墊底噪"),
                        "霧化": m.get("方式") == "霧化", "候選": [],
                        **({"重疊": ov, "做法": m.get("做法")} if ov else {}),
                        **({"重疊缺資料": m["重疊缺資料"], "要人聽": True} if m.get("重疊缺資料") else {})})
    out.sort(key=lambda e: e["start"])
    return out, warnings


OVERLAP_KEEP = "不用改"            # 重疊的做法裡，只有這個會把原聲留在成品
OVERLAP_NOT_BUILT = ("兩邊都重生成", "兩邊都不留")   # 還沒做的做法，先消音（第 3 步也不顯示了）
OVERLAP_LEFT_TOL_S = 0.05          # 重疊處沒蓋到的原聲在這個秒數以內不算漏（剪點對齊畫面格的誤差）


def overlap_mutes(overlaps: list[dict]) -> list[dict]:
    """重疊處要消音的範圍（純函式，09-30）：兩個聲音混在同一段錄音裡，學員原聲拿不掉，
    所以除了「不用改」，不管選哪個做法，重疊那一小段一律消音（老師跟著靜音零點幾秒）。
    被學員重念、名字處理蓋到的部分由 `add_local_mutes` 讓給那一筆。
    `overlaps`：[{id, start, end, 做法}]。"""
    return [{"id": f"重疊{o['id']}", "start": o["start"], "end": o["end"], "方式": "墊底噪",
             "重疊": o["id"], "做法": o["做法"]}
            for o in overlaps if o["做法"] != OVERLAP_KEEP and o["end"] > o["start"]]


COVER_TOL_S = 0.05   # 涵蓋的判斷容許的誤差（剪點對齊畫面格、四捨五入）


def find_cover(a: float, b: float, coverers: list[dict], oid: str | None = None) -> dict | None:
    """[a, b] 整個落在哪一筆「會把原聲換掉」的範圍裡（純函式，10-01 宇軒 7-4）：學員重念的時間格、
    老師重念的範圍、剪掉的片段。這一處重疊自己產生的那一筆（`重疊項目`／`重疊` 是 oid）不算。
    `coverers`：[{類型, id, start, end, ...}]；回傳最短的那一筆，沒有就 None。覆核工作台與組裝共用。"""
    hits = [k for k in coverers
            if k["start"] - COVER_TOL_S <= a and b <= k["end"] + COVER_TOL_S
            and not (oid and (oid in (k.get("重疊項目") or []) or k.get("重疊") == oid))]
    return min(hits, key=lambda k: k["end"] - k["start"]) if hits else None


def overlap_outcome(o: dict, edits: list[dict], cuts: list[tuple[float, float]] = ()) -> dict:
    """這一處重疊在剪輯決策裡實際怎麼了（純函式）：{處理: 一句話, 沒處理秒: 還留著原聲的秒數, 涵蓋: 那一筆}。
    `edits` 是排好的全部動作（換聲音、消音都會把那段原聲拿掉）。
    10-01：整個落在別筆換聲音的範圍裡（`find_cover`）→ 跟著那一筆換掉，不管這一處選了什麼。"""
    swaps = [e for e in edits if e["類型"] in SWAP_KINDS]
    cov = find_cover(o["start"], o["end"], [{**e, "id": e.get("id") or e.get("生成編號") or e["類型"]} for e in swaps],
                     o["id"])
    if cov:
        return {"處理": f"整段落在 {cov['id']} 的範圍裡，跟著換掉", "沒處理秒": 0.0,
                "涵蓋": {"類型": cov["類型"], "id": cov["id"], "start": cov["start"], "end": cov["end"]}}
    if o["做法"] == OVERLAP_KEEP:
        return {"處理": "照原樣，沒有動（不用改）", "沒處理秒": 0.0}
    free = subtract(o["start"], o["end"], list(cuts))
    if not free:
        return {"處理": "落在刪除段落裡，已經剪掉", "沒處理秒": 0.0}
    muted = sum(min(e["end"], y) - max(e["start"], x) for x, y in free for e in edits
                if e.get("重疊") == o["id"] and e["類型"] in MUTE_KINDS and e["start"] < y and x < e["end"])   # B 方案的兩句也帶 重疊，不算消音
    others = [e for e in edits if e.get("重疊") != o["id"]]
    left = sum(e - s for x, y in free for s, e in subtract(x, y, [(k["start"], k["end"]) for k in edits]))
    by = "、".join(dict.fromkeys(str(e.get("id") or e["類型"]) for e in others
                                if any(e["start"] < y and x < e["end"] for x, y in free)))
    if left > OVERLAP_LEFT_TOL_S:
        return {"處理": f"還有 {left:.2f} 秒原聲沒處理", "沒處理秒": round(left, 3)}
    if muted < 0.01:
        mine = [e for e in edits if e.get("疊放") and e.get("重疊") == o["id"]]
        if len(mine) >= 2:
            return {"處理": f"兩邊都重新生成、照原本的時間疊著（{'、'.join(str(e.get('id')) for e in mine)}）", "沒處理秒": 0.0}
        return {"處理": f"在 {by} 換聲音時一起換掉", "沒處理秒": 0.0}
    text = f"消音 {muted:.2f} 秒（墊環境底噪，老師的聲音跟著靜音）"
    if by:
        text += f"，其餘在 {by} 換聲音時一起換掉"
    if o["做法"] in OVERLAP_NOT_BUILT:
        text += f"；「{o['做法']}」還沒做，先消音"
    elif o["做法"] == "只留老師":
        text += "；選的是生成老師聲音，但老師這一句還沒生成或找不到句子，先消音"
    return {"處理": text, "沒處理秒": 0.0}


def student_name_edits(sp: dict) -> list[dict]:
    """保留原聲學員講到名字（`studentnames.plan()`）→ 剪輯決策：直接消音的墊底噪、換成代號的放生成檔（有生成紀錄才放）。"""
    out = [{"類型": "學員名字消音", "start": m["start"], "end": m["end"], "id": m["id"], "學員": m["學員"], "候選": [m["id"]]}
           for m in sp.get("消音", [])]
    return out


def add_student_name_edits(edits: list[dict], workdir: Path, a: float = 0.0, b: float = 1e12,
                           cuts: list[tuple[float, float]] = (), details: list[dict] | None = None) -> tuple[list[dict], list[str]]:
    """把保留原聲學員講到名字的處理加進剪輯決策；跟既有動作重疊時以既有那筆為準（例如測試時學員整段重念）。"""
    from bookclub import studentgen, studentnames

    sp = studentnames.plan(workdir)
    new = [e for e in student_name_edits(sp) + studentgen.swap_edits(workdir, sp) if e["start"] < b and a < e["end"]]
    new = [e for e in new if not any(x <= e["start"] and e["end"] <= y for x, y in cuts)]
    # 10-02 第七批（A2）：跟既有動作疊到時以既有那筆為準，但只扣掉疊到的部分（以前整筆丟掉，名字會留著）
    return resolve_overlaps(new, edits, longest_first=False, label=lambda e: f"{e['類型']} {e['id']}", details=details)


# ---------- 聲音處理（純函式） ----------

def _frame_rms(x: np.ndarray, frame: int) -> np.ndarray:
    n = len(x) // frame
    if n == 0:
        return np.array([np.sqrt(np.mean(x.astype(np.float64) ** 2))]) if len(x) else np.zeros(1)
    return np.sqrt(np.mean(x[: n * frame].reshape(n, frame).astype(np.float64) ** 2, axis=1))


def active_rms(x: np.ndarray, sr: int = SR) -> float:
    """講話部分的音量：只算不太安靜的音框。"""
    rms = _frame_rms(x, max(1, int(sr * 0.02)))
    if rms.max() <= 0:
        return 0.0
    loud = rms[20 * np.log10(rms / rms.max() + 1e-12) > ACTIVE_DB]
    return float(np.sqrt(np.mean(loud ** 2))) if len(loud) else 0.0


def match_loudness(clip: np.ndarray, ref: np.ndarray, sr: int = SR, max_gain: float = 8.0) -> np.ndarray:
    """把 clip 的講話音量調到跟 ref 一樣（最多放大 8 倍，避免把底噪放大）。"""
    a, b = active_rms(clip, sr), active_rms(ref, sr)
    if a <= 0 or b <= 0:
        return clip
    return (clip * min(b / a, max_gain)).astype(np.float32)


def fit_length(clip: np.ndarray, n: int) -> np.ndarray:
    if len(clip) >= n:
        return clip[:n]
    return np.concatenate([clip, np.zeros(n - len(clip), dtype=np.float32)])


def voice_over_room(clip: np.ndarray, room: np.ndarray | None, n: int, freeze_n: int = 0,
                    sr: int = SR) -> tuple[np.ndarray, np.ndarray | None, float]:
    """換聲音類（學員重念、名字整句換掉、保留原聲學員名字）放進時間格的聲音（10-03 第八批 #60，純函式）。

    以前用 `splice` 頭尾各 10 毫秒跟原片交叉淡入淡出：接縫切在學員還在講話的地方，就混進 10 毫秒學員原聲（T034 雜音）。
    現在：生成的聲音頭尾各 10 毫秒**從底噪淡入、淡出到底噪**，底下整段墊 room（底噪），整格不留原片。
    - n：時間格多長；freeze_n：停格補長多長（0＝沒有停格）。回傳（時間格那一截 head、停格那一截 tail 或 None、結尾被切掉幾秒）
    - 有停格：head 結尾不淡出、tail 開頭不淡入，head＋tail＝原本連續的聲音，只在整句最後淡出
    - 聲音比時間格（＋停格）長：多的切掉，結尾一樣淡出；切掉的秒數回傳給第 5 步標出來
    room 是 None 時底下是全靜音。"""
    total = n + max(0, freeze_n)
    voice = fit_length(clip.astype(np.float32), total).copy()
    cut = max(0, len(clip) - total) / sr
    f = min(int(sr * FADE_S), total // 2)
    if f > 0:
        voice[:f] *= np.linspace(0, 1, f, dtype=np.float32)
        voice[-f:] *= np.linspace(1, 0, f, dtype=np.float32)
    if room is not None:
        voice = voice + fit_length(room.astype(np.float32), total)
    head = voice[:n]
    tail = voice[n:] if freeze_n > 0 else None
    return head, tail, round(cut, 3)


# 10-04 #134：停格點（畫面格）跟格子結尾錯開時怎麼接（`render.build_decisions` 判斷、`render.build_audio` 照做、
# 處理紀錄與 `inspect 剪輯決策` 印這幾個固定說法）
FREEZE_ALIGNED = "對齊"
FREEZE_EARLY = "停格點比結尾早：畫面先停，聲音連續播完，播完才墊底噪"
FREEZE_LATE_JOIN = "停格點比結尾晚：聲音連續播過停格點並播完，之後墊底噪（佔用下一筆開頭這一小段）"
FREEZE_LATE_KEEP = "停格點比結尾晚太多：維持原樣，聲音前後淡出淡入"
FREEZE_OFFSET_MAX_S = 0.04   # 錯開超過這麼多不處理（照舊）；正常是 snap 造成的 ≤0.02 秒
FREEZE_JOIN_MAX_S = 0.02     # 停格點比結尾晚：聲音佔用下一筆開頭最多這麼多秒（宇軒 10-05：不能蓋掉別人超過這 10–20 毫秒）


def freeze_offset_split(clip: np.ndarray, room: np.ndarray | None, n: int, freeze_n: int, off: int, join: bool = True,
                        sr: int = SR) -> dict:
    """有停格的換聲音格，停格點跟格子結尾錯開 off 個取樣點時，這一格的聲音怎麼切（10-04 #134，純函式）。

    成品的順序是：原片時間軸 y 播到停格點 → 停格那一段（tail）→ y 從停格點接著播。停格點是畫面格（`render.snap`），
    格子結尾是取樣點，以前 head 照格子結尾放、停格點卻在畫面格上，就會：
    - 停格點早（off < 0）：講話在停格點被切斷、停格那一段從格子結尾的內容接著念（中間 10–20 毫秒先跳過），
      停格結束後那 10–20 毫秒的聲音才像碎片一樣冒出來。
    - 停格點晚（off > 0）：格子結尾到停格點這一小段放的是下一筆的開頭（講話中途插進 10–20 毫秒的底噪），再接停格。
    現在照**成品播放的順序**把這一格整條連續的聲音（`voice_over_room` 的做法：頭尾從底噪淡入、淡出到底噪）依序排進去，
    不丟任何聲音、中間沒有斷點：
    - off < 0：y[格子開頭, 停格點] → 停格那一段 → y[停格點, 格子結尾]，三截依序接成整條聲音；
      停格點後面那 10–20 毫秒放的是這一格最後的一小段（整句最後淡出到底噪的地方），不是碎片。
    - off > 0 且 join（錯開 ≤ FREEZE_JOIN_MAX_S）：格子結尾～停格點這一小段（本來是下一筆的開頭）放這一格的聲音接著念，
      停格那一段接著念，整條聲音播完後剩下的停格補長是底噪；`resume` 是停格結束、從停格點接回下一筆（或原片）時，
      前 10 毫秒從這一格的底噪交叉淡入，不留硬切。
    - off > 0 但不 join（錯開太多，不該發生）：下一筆的範圍不動；這一格的聲音在格子結尾前 10 毫秒淡出到底噪、
      停格那一段開頭 10 毫秒從底噪淡入。
    回傳 {head: 放進 y[s, s+n]（off>0 且 join 時長 n+off，會蓋到下一筆的開頭）, tail: 停格那一段, resume: 停格結束接回 y
    時前幾個取樣點要跟它交叉淡入的底噪（或 None）, cut: 結尾被切掉幾秒}。"""
    total = n + max(0, freeze_n)
    voice = fit_length(clip.astype(np.float32), total).copy()
    cut = max(0, len(clip) - total) / sr
    f = min(int(sr * FADE_S), total // 2)
    if f > 0:
        voice[:f] *= np.linspace(0, 1, f, dtype=np.float32)
        voice[-f:] *= np.linspace(1, 0, f, dtype=np.float32)
    extra = max(0, off) + f
    rm = fit_length(room.astype(np.float32), total + extra) if room is not None else np.zeros(total + extra, np.float32)
    if off < 0:
        k = n + off   # 停格點在這一格裡的位置
        whole = voice + rm[:total]
        head = np.concatenate([whole[:k], whole[k + freeze_n:]])
        return {"head": head, "tail": whole[k:k + freeze_n], "resume": None, "cut": round(cut, 3)}
    if off > 0 and join:
        v = np.concatenate([voice, np.zeros(off, np.float32)])   # 整條聲音念完，停格補長最後 off 個取樣點只剩底噪
        whole = v + rm[:total + off]
        return {"head": whole[:n + off], "tail": whole[n + off:], "resume": rm[total + off:total + off + f].copy(),
                "cut": round(cut, 3)}
    if off > 0:
        g = min(f, n // 2, freeze_n // 2)
        if g > 0:
            voice[n - g:n] *= np.linspace(1, 0, g, dtype=np.float32)
            voice[n:n + g] *= np.linspace(0, 1, g, dtype=np.float32)
        whole = voice + rm[:total]
        return {"head": whole[:n], "tail": whole[n:], "resume": None, "cut": round(cut, 3)}
    whole = voice + rm[:total]
    return {"head": whole[:n], "tail": whole[n:], "resume": None, "cut": round(cut, 3)}


def splice(y: np.ndarray, s: int, clip: np.ndarray, sr: int = SR) -> None:
    """把 clip 放進 y[s:s+len(clip)]，頭尾各 10 毫秒跟原本的聲音交叉淡入淡出。
    10-03 第八批 #60 起只給消音類（墊底噪）用；換聲音類改用 `voice_over_room`（不跟原片交疊）。"""
    n = len(clip)
    f = min(int(sr * FADE_S), n // 2)
    seg = clip.astype(np.float32).copy()
    if f > 0:
        ramp = np.linspace(0, 1, f, dtype=np.float32)
        seg[:f] = y[s:s + f] * (1 - ramp) + seg[:f] * ramp
        seg[-f:] = seg[-f:] * (1 - ramp) + y[s + n - f:s + n] * ramp
    y[s:s + n] = seg


def room_tone(x: np.ndarray, s: int, e: int, n: int, sr: int = SR, avoid: list[tuple[int, int]] = (), bed=None) -> np.ndarray:
    """[s, e) 要墊的底噪，n 個取樣點。10-02 第六批第五件改挑法（見 `bookclub/roomtone.py`）：前後 20 秒內
    夠安靜（低於上限）的連續片段，處理過的範圍裡的也可以；附近沒有就用全片底噪（bed）。
    `avoid` 是舊挑法用的（避開處理過的範圍），現在不用，留著參數讓舊的呼叫照樣能跑。"""
    from bookclub import roomtone

    if bed is not None:
        return bed.take(x, s, e, n, sr)
    return roomtone.pick(x, s, e, n, sr)[0]


def render_edit(window: np.ndarray, w0: int, edit: dict, clip: np.ndarray | None,
                spans: list[tuple[int, int]], sr: int = SR, bed=None) -> np.ndarray:
    """處理一筆：window 是原聲從第 w0 個取樣點開始的一段（涵蓋這筆前後 20 秒），
    回傳這筆時間範圍 [s, t) 處理後的聲音。spans 是所有筆的範圍（找底噪時避開）。"""
    s, t = int(edit["start"] * sr) - w0, int(edit["end"] * sr) - w0
    s, t = max(0, s), min(len(window), t)
    y = window.astype(np.float32).copy()
    if t <= s:
        return y[s:t]
    if edit["類型"] not in MUTE_KINDS:
        # 10-03 第八批 #60：換聲音從底噪淡入、淡出到底噪，整格不留原片（不再跟原片交叉淡入淡出）
        loud = match_loudness(fit_length(clip, max(len(clip), t - s)), window[s:t], sr)
        room = room_tone(window, s, t, t - s, sr, bed=bed)
        new, _tail, cut = voice_over_room(loud, room, t - s, 0, sr)
        if cut >= CUT_MARK_S:
            edit["結尾切掉秒"] = round(cut, 2)
        y[s:t] = new
        return y[s:t]
    local = [(a - w0, b - w0) for a, b in spans if (a - w0, b - w0) != (s, t)]
    new = room_tone(window, s, t, t - s, sr, avoid=local, bed=bed)
    if edit["類型"] in HARD_MUTE_KINDS:
        y[s:t] = new
    else:
        splice(y, s, new, sr)
    return y[s:t]


def apply_edits(x: np.ndarray, edits: list[dict], clips: dict[int, np.ndarray], sr: int = SR, bed=None) -> np.ndarray:
    """整條聲音一次處理（測試用；正式組裝走 render_audio 逐段讀寫，不整條放進記憶體）。"""
    y = x.astype(np.float32).copy()
    spans = [(int(e["start"] * sr), int(e["end"] * sr)) for e in edits]
    for i, e in enumerate(edits):
        s = max(0, spans[i][0])
        seg = render_edit(x, 0, e, clips.get(i), spans, sr, bed=bed)
        y[s:s + len(seg)] = seg
    return y


# ---------- 讀寫檔案 ----------

def _read_audio(path: Path) -> np.ndarray:
    """短檔（生成的聲音）用 ffmpeg 讀成 48kHz 單聲道 float32。"""
    cmd = ["ffmpeg", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def _read_range(path: Path, a: int, b: int) -> np.ndarray:
    import soundfile as sf

    with sf.SoundFile(str(path)) as f:
        a = max(0, a)
        f.seek(a)
        return f.read(max(0, min(b, f.frames) - a), dtype="float32")


def _write_wav(path: Path, x: np.ndarray) -> None:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    wd.unlink_if_link(path)   # 10-02 第五批：複製來的工作區裡是連結的話，寫成自己的檔（不順著連結寫回原本的工作區）
    sf.write(str(path), np.clip(x, -1, 1), SR, subtype="PCM_16")


def render_audio(workdir: str | Path, video: str | Path | None = None) -> dict:
    """`bookclub render audio`：讀名字處理計畫與老師生成紀錄，組出新聲音軌與處理前後試聽。

    整支影片的聲音不整條放進記憶體（98 分鐘約 1GB）：先用 ffmpeg 抽一份 48kHz
    原聲檔，再一段段抄到新聲音軌，碰到要處理的地方才讀前後 20 秒來處理。
    """
    import soundfile as sf

    from bookclub import nameplan, tts

    workdir = Path(workdir).expanduser()
    plan = wd.read_json(nameplan.plan_path(workdir))
    if not plan:
        raise FileNotFoundError(f"找不到 {nameplan.plan_path(workdir)}，先跑 `bookclub gen names`。")
    teacher_log = wd.read_json(tts.teacher_log_path(workdir))
    edits, warnings = build_edl(plan, teacher_log)
    from bookclub import review

    edits, w2 = add_local_mutes(edits, local_mutes(review.load_decisions(workdir)))   # 聲音軌跟原片等長，刪除段落不套用
    warnings += w2
    edits, w3 = add_student_name_edits(edits, workdir)
    warnings += w3
    for w in warnings:
        print(f"⚠️ {w}")

    if video is None:   # 10-02 第五批：存下來的路徑照「現在這個工作區」解讀（見 wd.find_video）
        video = wd.find_video(workdir)
        if video is None:
            raise FileNotFoundError(wd.video_missing_message(workdir))
    if not Path(video).is_file():
        raise FileNotFoundError(f"找不到原片影片：{video}\n→ 用 --video 指定影片路徑。")

    out = out_dir(workdir)
    out.mkdir(parents=True, exist_ok=True)
    orig_path, new_path = out / "原聲音軌.wav", out / "新聲音軌.wav"
    if not orig_path.is_file():
        print(f"[組裝] 從原片抽出 48kHz 聲音：{Path(video).name}")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(SR),
                        "-c:a", "pcm_s16le", str(orig_path)], check=True)
    spans = [(max(0, int(e["start"] * SR)), int(e["end"] * SR)) for e in edits]
    from bookclub import roomtone

    bed = roomtone.bed_for(workdir, video, SR)   # 10-02 第六批：夠安靜才用、附近沒有用全片底噪
    pad = int((ROOM_SEARCH_S + ROOM_WIN_S) * SR)
    block = SR * 30
    with sf.SoundFile(str(orig_path)) as src, \
            sf.SoundFile(str(new_path), "w", samplerate=SR, channels=1, subtype="PCM_16") as dst:
        total = src.frames
        pos = 0

        def copy_until(end: int) -> None:
            nonlocal pos
            src.seek(pos)
            while pos < end:
                n = min(block, end - pos)
                dst.write(src.read(n, dtype="float32"))
                pos += n

        for i, e in enumerate(edits):
            s0, t0 = min(spans[i][0], total), min(spans[i][1], total)
            copy_until(s0)
            w0 = max(0, s0 - pad)
            window = _read_range(orig_path, w0, t0 + pad)
            clip = _read_audio(workdir / e["檔案"]) if e["類型"] not in MUTE_KINDS else None
            seg = render_edit(window, w0, e, clip, spans, bed=bed)
            dst.write(np.clip(seg, -1, 1))
            pos = s0 + len(seg)
        copy_until(total)

    ab = out / "處理前後"
    rows = []
    for n, e in enumerate(edits, start=1):
        a, b = int((e["start"] - CONTEXT_S) * SR), int((e["end"] + CONTEXT_S) * SR)
        tag = f"{n:03d}_{wd.fmt_time(e['start']).replace(':', '')}"
        _write_wav(ab / f"{tag}_前.wav", _read_range(orig_path, a, b))
        _write_wav(ab / f"{tag}_後.wav", _read_range(new_path, a, b))
        rows.append({**e, "編號": n, "前": f"處理前後/{tag}_前.wav", "後": f"處理前後/{tag}_後.wav"})
    (out / "處理前後.html").write_text(_ab_page(rows), encoding="utf-8")

    summary = {
        "原片": str(video), "長度秒": round(total / SR, 2),
        "換聲音": sum(1 for e in edits if e["類型"] == "換聲音"),
        "消音": sum(1 for e in edits if e["類型"] == "消音"),
        "局部消音": sum(1 for e in edits if e["類型"] == "局部消音"),
        "要人聽": sum(1 for e in edits if e.get("要人聽")),
        "要人處理": len(plan.get("要人處理", [])),
        "警告": warnings,
        "剪輯決策": edits,
        "底噪挑法": roomtone.METHOD,
    }
    wd.write_json(edl_path(workdir), summary)
    from bookclub import proclog   # 09-29：AI 處理紀錄＋沒登記的變動檢查（生成/處理紀錄.json，第 5 步讀）
    proclog.write_audio_log(workdir, edits, orig_path, new_path)
    print(f"[組裝] 完成：換聲音 {summary['換聲音']} 段、消音 {summary['消音']} 段、局部消音 {summary['局部消音']} 段；"
          f"新聲音軌 {summary['長度秒'] / 60:.1f} 分鐘 → {new_path}")
    return summary


def _ab_page(rows: list[dict]) -> str:
    items = []
    for r in rows:
        what = (f"換成老師 AI 聲音：{html.escape(r['文字'])}" if r["類型"] == "換聲音"
                else f"局部消音 {r['id']}（墊環境底噪{'；霧化還沒做' if r.get('霧化') else ''}）" if r["類型"] == "局部消音"
                else "名字消音（墊環境底噪）")
        flag = '<span class="flag">要人聽</span>' if r.get("要人聽") else ""
        items.append(
            f'<section><b>#{r["編號"]}　{wd.fmt_time(r["start"])}–{wd.fmt_time(r["end"])}</b>　'
            f'<small>{r["類型"]}・候選 {"、".join(map(str, r["候選"]))}</small> {flag}'
            f'<p>{what}</p><p>處理前<audio controls preload="none" src="{r["前"]}"></audio></p>'
            f'<p>處理後<audio controls preload="none" src="{r["後"]}"></audio></p></section>'
        )
    return (
        '<!doctype html><html lang="zh-Hant"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>處理前後試聽</title>'
        "<style>body{font-family:-apple-system,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;"
        "line-height:1.6}section{border:1px solid #ddd;border-radius:8px;padding:10px 16px;margin:12px 0}"
        "audio{width:100%}small{color:#666}.flag{color:#b00;font-size:.9em}</style>"
        f"<h1>處理前後試聽（{len(rows)} 筆）</h1>"
        "<p><small>每筆前後各多 2 秒。這頁含逐字稿（已換成代號），不要外傳。</small></p>"
        + "".join(items) + "</html>"
    )
