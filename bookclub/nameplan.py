"""流程第 5 步（二）：老師提到學員名字的地方，排出要怎麼處理（02 規格第三節）。

讀第 1 步找到的 `名字候選.json`，扣掉第 4 步覆核標成「不是名字」「是地名」的，
每一筆照做法分三種：

| 做法 | 這支程式產出 |
| --- | --- |
| 直接消音 | 剪輯決策：名字那一小段消音（組裝時墊環境底噪） |
| 整句換掉 | 生成清單：整句改寫成代號，交給老師 AI 聲音重念，放回整句的時間格 |
| 只換名字 | 生成清單：只念代號，放回名字那一小段的時間格 |

做法預設用第 1 步的「建議做法」，覆核決定裡有 `做法` 欄位就用覆核的。
同一句有好幾個名字要整句換掉時，合成一筆、一次換完。

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


def build_plan(candidates: list[dict], decisions: dict, sentences: dict[str, dict]) -> dict:
    """純函式：候選＋覆核決定＋句子（id → {start, end, text}）→ 處理計畫。

    回傳 {"生成": [...句子清單...], "消音": [...], "略過": [...], "要人處理": [...]}，
    每一筆都帶 `候選` 編號（1 起算，跟覆核決定的 id 一致）。
    """
    gen, mutes, skipped, manual = [], [], [], []
    whole: dict[str, dict] = {}   # sentence_id → 生成項目（同一句合併）

    for i, c in enumerate(candidates, start=1):
        d = decisions.get(str(i), {}) or {}
        tags = set(d.get("tags", []))
        if tags & SKIP_TAGS:
            skipped.append({"候選": i, "原因": "、".join(sorted(tags & SKIP_TAGS))})
            continue
        how = d.get("做法") or c.get("建議做法") or WHOLE
        pad = float(c.get("建議緩衝秒數") or 0.0)

        if how == MUTE:
            mutes.append({"候選": i, "start": round(c["start"] - pad, 3), "end": round(c["end"] + pad, 3)})
            continue

        if how == NAME_ONLY:
            gen.append({"id": f"N{i:03d}", "text": c["代號"], "slot": [c["start"] - pad, c["end"] + pad], "候選": [i]})
            continue

        sid = c.get("sentence_id")
        sent = sentences.get(sid)
        if not sent:
            manual.append({"候選": i, "原因": f"找不到所在的句子（{sid}）"})
            continue
        item = whole.get(sid)
        base = item["text"] if item else sent["text"]
        new = replace_name(base, c)
        if new is None:
            manual.append({"候選": i, "原因": "句子裡找不到比對到的字，無法自動換成代號"})
            continue
        if item:
            item["text"] = new
            item["候選"].append(i)
        else:
            whole[sid] = {"id": f"S{i:03d}", "text": new, "slot": [sent["start"], sent["end"]], "候選": [i]}

    gen.extend(whole.values())
    gen.sort(key=lambda g: g["slot"][0])
    return {"生成": gen, "消音": mutes, "略過": skipped, "要人處理": manual}


def make_plan(workdir: str | Path, only: list[int] | None = None) -> dict:
    """`bookclub gen names` 的第一步：排出計畫、寫出句子清單。only 給了就只處理那幾筆候選（測試用）。"""
    workdir = Path(workdir).expanduser()
    names = wd.read_json(wd.names_path(workdir))
    if not names:
        raise FileNotFoundError(f"找不到 {wd.names_path(workdir)}，先跑 `bookclub run analyze`。")
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
    wd.write_json(sentences_path(workdir), [{k: g[k] for k in ("id", "text", "slot")} for g in plan["生成"]])
    wd.write_json(plan_path(workdir), plan)
    print(f"[名字處理] 候選 {len(candidates)} 筆"
          + (f"（這次只處理 {len(only)} 筆）" if only else "")
          + f"：生成 {len(plan['生成'])} 段、消音 {len(plan['消音'])} 段、略過 {len(plan['略過'])} 筆、"
          f"要人處理 {len(plan['要人處理'])} 筆")
    return plan
