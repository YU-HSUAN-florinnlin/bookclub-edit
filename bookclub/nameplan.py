"""流程第 5 步（二）：老師提到學員名字的地方，排出要怎麼處理（02 規格第三節）。

讀第 1 步找到的 `名字候選.json`，扣掉第 4 步覆核標成「不是名字」「是地名」的，
每一筆照做法分三種：

| 做法 | 這支程式產出 |
| --- | --- |
| 直接消音 | 剪輯決策：名字那一小段消音（組裝時墊環境底噪） |
| 整句換掉 | 生成清單：整句改寫成代號，交給老師 AI 聲音重念，放回整句的時間格 |
| 只換名字 | 生成清單：只念代號，放回名字那一小段的時間格 |

做法：覆核決定（第 3 步覆核工作台）裡有 `做法` 欄位就用覆核的，沒有就整句換掉（09-25 定案）。
整句換掉時照標點把 Groq 的半句擴成完整句子；同一句有好幾個名字時，合成一筆、一次換完。

產出（工作區）：
- `生成/名字句子.json`：給 `bookclub gen teacher` 的句子清單（只含代號，不含本名）
- `生成/名字處理計畫.json`：每筆候選對到哪一筆生成或消音，組裝時讀
"""

from __future__ import annotations

from pathlib import Path

from bookclub import workdir as wd

DECISIONS_FILE_NAME = "名字覆核決定.json"   # 跟 bookclub/server.py 一致
SKIP_TAGS = {"不是名字", "是地名"}
MUTE, WHOLE, NAME_ONLY = "直接消音", "整句換掉", "只換名字"


def plan_path(workdir: Path) -> Path:
    return workdir / "生成" / "名字處理計畫.json"


def sentences_path(workdir: Path) -> Path:
    return workdir / "生成" / "名字句子.json"


def replace_name(sentence: str, cand: dict) -> str | None:
    """把句子裡的名字換成代號。依序試：比對到的字、名冊寫法、本名；都找不到回傳 None。"""
    for key in ("matched_text", "name", "canonical"):
        word = (cand.get(key) or "").strip()
        if word and word in sentence:
            return sentence.replace(word, cand["代號"], 1)
    return None


END_PUNCT = "。！？!?﹖﹗…"          # 句子結束
CONT_PUNCT = "，,、；;：:﹐﹑"        # 句子還沒完（Groq 在這裡切開的是半句）
MAX_SENTENCE_S = 25.0               # 擴句的上限：整句不超過這麼長
MAX_JOIN_GAP_S = 0.8                # 前後兩段中間空白超過這個秒數，不接在一起


def _ends_open(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and t[-1] in CONT_PUNCT


def expand_sentence(ordered: list[dict], idx: int) -> list[dict]:
    """把 Groq 切的半句擴成完整句子（純函式，09-25 宇軒：句子起訖照標點，不斷在句子中間）。

    往前：前一段以逗號類結尾（句子還沒完）、也是老師、中間空白不超過 MAX_JOIN_GAP_S → 接上；
    往後：這一段以逗號類結尾 → 接下一段。以句號、問號、刪節號或沒有標點結尾的地方當成句子邊界。
    總長不超過 MAX_SENTENCE_S。回傳依時間排序的句子清單（至少含原本那一句）。"""
    group = [ordered[idx]]
    lo, hi = idx, idx

    def ok(s: dict) -> bool:
        return s.get("label", "老師") == "老師"

    while lo > 0:
        prev = ordered[lo - 1]
        if not (_ends_open(prev["text"]) and ok(prev) and group[0]["start"] - prev["end"] <= MAX_JOIN_GAP_S
                and group[-1]["end"] - prev["start"] <= MAX_SENTENCE_S):
            break
        lo -= 1
        group.insert(0, prev)
    while hi + 1 < len(ordered):
        nxt = ordered[hi + 1]
        if not (_ends_open(group[-1]["text"]) and ok(nxt) and nxt["start"] - group[-1]["end"] <= MAX_JOIN_GAP_S
                and nxt["end"] - group[0]["start"] <= MAX_SENTENCE_S):
            break
        hi += 1
        group.append(nxt)
    return group


def build_plan(candidates: list[dict], decisions: dict, sentences: dict[str, dict],
               default_how: str = WHOLE) -> dict:
    """純函式：候選＋覆核決定＋句子（id → {start, end, text}）→ 處理計畫。

    做法：覆核決定的 `做法` 優先；沒有就用 default_how（09-25 宇軒定案：預設整句換掉；
    第 1 步的「建議做法」只當成覆核時的參考）。整句換掉時，Groq 的半句照標點擴成完整句子
    （`expand_sentence`），同一個完整句子裡的名字合成一筆、一次換完。

    回傳 {"生成": [...句子清單...], "消音": [...], "略過": [...], "要人處理": [...]}，
    每一筆都帶 `候選` 編號（1 起算，跟覆核決定的 id 一致）。
    """
    gen, mutes, skipped, manual = [], [], [], []
    whole: dict[str, dict] = {}   # 完整句子第一段的 id → 生成項目（同一句合併）
    ordered = sorted(sentences.values(), key=lambda x: x["start"])
    pos = {x["id"]: k for k, x in enumerate(ordered)}

    for i, c in enumerate(candidates, start=1):
        d = decisions.get(str(i), {}) or {}
        tags = set(d.get("tags", []))
        if tags & SKIP_TAGS:
            skipped.append({"候選": i, "原因": "、".join(sorted(tags & SKIP_TAGS))})
            continue
        how = d.get("做法") or default_how
        pad = float(c.get("建議緩衝秒數") or 0.0)

        if how == MUTE:
            mutes.append({"候選": i, "start": round(c["start"] - pad, 3), "end": round(c["end"] + pad, 3)})
            continue

        if how == NAME_ONLY:
            gen.append({"id": f"N{i:03d}", "text": c["代號"], "slot": [c["start"] - pad, c["end"] + pad], "候選": [i]})
            continue

        sid = c.get("sentence_id")
        if sid not in pos:
            manual.append({"候選": i, "原因": f"找不到所在的句子（{sid}）"})
            continue
        group = expand_sentence(ordered, pos[sid])
        key = group[0]["id"]
        item = whole.get(key)
        # 名字只在原本那一段裡換（避免換到前後段同音的字），再接成整句
        texts = item["_texts"] if item else {g["id"]: g["text"] for g in group}
        new = replace_name(texts[sid], c)
        if new is None:
            manual.append({"候選": i, "原因": "句子裡找不到比對到的字，無法自動換成代號"})
            continue
        texts[sid] = new
        full = "".join(texts[g["id"]] for g in group)
        if item:
            item["text"] = full
            item["候選"].append(i)
        else:
            whole[key] = {"id": f"S{i:03d}", "text": full, "slot": [group[0]["start"], group[-1]["end"]],
                          "候選": [i], "句子": [g["id"] for g in group], "_texts": texts}

    for item in whole.values():
        item.pop("_texts", None)
    gen.extend(whole.values())
    gen.sort(key=lambda g: g["slot"][0])
    return {"生成": gen, "消音": mutes, "略過": skipped, "要人處理": manual}


IMPORTED_PATH = Path("覆核") / "覆核結果.json"   # `bookclub review import` 放進來的覆核結果


def make_plan(workdir: str | Path, only: list[int] | None = None) -> dict:
    """`bookclub gen names` 的第一步：排出計畫、寫出句子清單。only 給了就只處理那幾筆候選（測試用）。

    匯入的工作區（夥伴的電腦，`bookclub review import` 建的）沒有名字候選與逐字稿，改用覆核結果裡
    匯出時排好的計畫（只含代號，不含本名）。"""
    workdir = Path(workdir).expanduser()
    names = wd.read_json(wd.names_path(workdir))
    if not names:
        exported = wd.read_json(workdir / IMPORTED_PATH)
        if exported and exported.get("名字處理計畫") is not None:
            return _write_plan(workdir, exported["名字處理計畫"], note="（用匯入的覆核結果）")
        raise FileNotFoundError(f"找不到 {wd.names_path(workdir)}，先跑 `bookclub run analyze`。")
    plan = compute_plan(workdir, names, only=only)
    return _write_plan(workdir, plan, n_cands=len(names.get("candidates", [])), only=only)


def compute_plan(workdir: Path, names: dict | None = None, only: list[int] | None = None) -> dict:
    """排出處理計畫（不寫檔）：`make_plan` 與匯出覆核結果共用，兩邊排出來的計畫一定一樣。"""
    workdir = Path(workdir).expanduser()
    names = names if names is not None else (wd.read_json(wd.names_path(workdir)) or {})
    speakers = wd.read_json(wd.speakers_path(workdir)) or {}
    sentences = {s["id"]: s for s in speakers.get("sentences", [])}
    decisions = wd.read_json(workdir / DECISIONS_FILE_NAME, default={}) or {}
    candidates = names.get("candidates", [])
    if only:
        keep = set(only)
        decisions = dict(decisions)
        for i in range(1, len(candidates) + 1):
            if i not in keep:
                decisions[str(i)] = {"tags": ["不是名字"]}  # 只在計算時略過，不寫回覆核決定
    plan = build_plan(candidates, decisions, sentences)
    if only:
        plan["略過"] = [s for s in plan["略過"] if s["候選"] in set(only)]
        plan["只處理"] = sorted(set(only))
    return plan


def _write_plan(workdir: Path, plan: dict, n_cands: int | None = None, only: list[int] | None = None,
                note: str = "") -> dict:
    wd.write_json(sentences_path(workdir), [{k: g[k] for k in ("id", "text", "slot")} for g in plan["生成"]])
    wd.write_json(plan_path(workdir), plan)
    print(f"[名字處理] {note}" + (f"候選 {n_cands} 筆" if n_cands is not None else "")
          + (f"（這次只處理 {len(only)} 筆）" if only else "")
          + f"：生成 {len(plan['生成'])} 段、消音 {len(plan['消音'])} 段、略過 {len(plan['略過'])} 筆、"
          f"要人處理 {len(plan['要人處理'])} 筆")
    return plan
