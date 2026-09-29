"""保留原聲學員講到名字、選「換成代號」時：用這位學員自己的聲音重念名字所在的短句（09-29 宇軒定案）。

1. 參考音：從這位學員**其他乾淨的段落**挑一段——相鄰句子接起來（中間空白 ≤ 1.2 秒）、10～29 秒（老師參考音上限 29 秒，
   學員講的比較少，下限放寬到 10 秒）；碰到重疊、含名字候選的句子不要；評分照 `refpick.py` 的精神：說話密度、
   逐字稿信心、音量穩定。挑不到 → 這一筆退回直接消音，紀錄裡寫原因。參考音放工作區 `參考音/學員/<學員>/`，不進倉庫
2. 生成：`tts.run_generation`（老師、學員共用的三階段：種子 42、轉回文字檢查、插入停頓、放回時間格），`role="學員"`
3. 輸出：`生成/保留原聲學員/`、`生成/保留原聲學員紀錄.json`，跟 `生成/老師/`、`生成/學員/` 分開，不動它們的快取
4. 組裝（`assemble.add_student_name_edits` → `swap_edits`）：放回時間格、墊環境底噪；第 5 步標「學員聲音生成，音色可能有差」

`bookclub gen stunames <工作區>`。可以中斷續跑（每次生成記在 `_嘗試快取.json`）。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import numpy as np

from bookclub import workdir as wd

REF_MIN_S, REF_MAX_S = 10.0, 29.0
REF_MAX_GAP_S = 1.2
REF_MIN_CPS = 2.0      # 每秒至少 2 個字：太稀疏的段落大多是空白，當參考音學不到聲音
TAG = "保留原聲學員名字"


def out_dir(workdir: Path) -> Path:
    return Path(workdir) / "生成" / "保留原聲學員"


def log_path(workdir: Path) -> Path:
    return Path(workdir) / "生成" / "保留原聲學員紀錄.json"


def ref_dir(workdir: Path, who: str) -> Path:
    return Path(workdir) / "參考音" / "學員" / who


# ---------- 挑參考音（純函式＋讀音檔） ----------

def ref_windows(sents: list[dict], blocked: list[tuple[float, float]], min_s: float | None = None,
                max_s: float | None = None, max_gap: float | None = None) -> list[list[dict]]:
    """這位學員的句子（依時間）→ 可以當參考音的連續句子組（純函式）。碰到 blocked（重疊、名字）的句子斷開、不用。"""
    min_s = REF_MIN_S if min_s is None else min_s
    max_s = REF_MAX_S if max_s is None else max_s
    max_gap = REF_MAX_GAP_S if max_gap is None else max_gap
    def bad(s: dict) -> bool:
        return any(s["start"] < y and x < s["end"] for x, y in blocked)

    out = []
    ss = sorted(sents, key=lambda s: s["start"])
    for i in range(len(ss)):
        if bad(ss[i]):
            continue
        group = [ss[i]]
        for s in ss[i + 1:]:
            if bad(s) or s["start"] - group[-1]["end"] > max_gap or s["end"] - group[0]["start"] > max_s:
                break
            group.append(s)
        if group[-1]["end"] - group[0]["start"] >= min_s:
            out.append(group)
    return out


def score_window(group: list[dict], x: np.ndarray, sr: int) -> float:
    """說話密度（每秒字數，4 字封頂）0.3＋逐字稿信心 0.2＋音量穩定 0.5（講話音框的 dB 標準差越小越好）。"""
    dur = group[-1]["end"] - group[0]["start"]
    chars = sum(len([c for c in g.get("text", "") if c.isalnum()]) for g in group)
    density = min(1.0, chars / dur / 4.0) if dur > 0 else 0.0
    lp = np.mean([g.get("avg_logprob", -1.0) for g in group])
    conf = float(np.clip(1 + lp, 0, 1))
    f = max(1, int(sr * 0.02))
    n = len(x) // f
    if n < 10:
        return 0.0
    rms = np.sqrt(np.mean(x[: n * f].reshape(n, f).astype(np.float64) ** 2, axis=1)) + 1e-9
    db = 20 * np.log10(rms)
    active = db[db > db.max() - 30]
    stable = float(np.clip(1 - np.std(active) / 10, 0, 1)) if len(active) else 0.0
    return round(0.3 * density + 0.2 * conf + 0.5 * stable, 4)


def pick_ref(workdir: Path, who: str, turns: list[dict], sentences: dict, blocked: list[tuple[float, float]]) -> dict:
    """挑這位學員的參考音，存到 `參考音/學員/<學員>/ref.wav`、`ref.txt`。回傳 {ok, wav, text, 長度秒, 分數} 或 {ok: False, 原因}。"""
    import soundfile as sf

    d = ref_dir(workdir, who)
    owned = {sid for t in turns if t.get("說話者") == who for sid in t.get("句子", [])}
    meta = wd.read_json(d / "ref.json", default=None) or {}
    if (d / "ref.wav").is_file() and (d / "ref.txt").is_file() and meta.get("句子") and set(meta["句子"]) <= owned:
        # 沿用；段落改過（例如那幾句改成老師）就重挑
        return {"ok": True, "wav": d / "ref.wav", "text": (d / "ref.txt").read_text(encoding="utf-8").strip(), "沿用": True}
    sents = [sentences[sid] for sid in owned if sid in sentences]
    sents = [s for s in sents if s.get("label") != "老師"]

    def cps(g: list[dict]) -> float:
        dur = g[-1]["end"] - g[0]["start"]
        return sum(len([c for c in x.get("text", "") if c.isalnum()]) for x in g) / dur if dur else 0.0

    wins = [g for g in ref_windows(sents, blocked) if cps(g) >= REF_MIN_CPS]
    if not wins:
        return {"ok": False, "原因": f"參考音挑不到：{who} 找不到 {REF_MIN_S:.0f} 秒以上、每秒至少 {REF_MIN_CPS:.0f} 字、沒有重疊也沒有名字的連續段落"}
    best, best_score, best_x, sr = None, -1.0, None, 16000
    with sf.SoundFile(str(wd.audio_path(workdir))) as f:
        sr = f.samplerate
        for g in wins:
            a, b = max(0.0, g[0]["start"] - 0.1), g[-1]["end"] + 0.1
            f.seek(int(a * sr))
            x = f.read(int((b - a) * sr), dtype="float32")
            if x.ndim > 1:
                x = x.mean(axis=1)
            sc = score_window(g, x, sr)
            if sc > best_score:
                best, best_score, best_x = g, sc, x
    d.mkdir(parents=True, exist_ok=True)
    sf.write(str(d / "ref.wav"), best_x, sr)
    text = "".join(g["text"] for g in best).strip()
    (d / "ref.txt").write_text(text + "\n", encoding="utf-8")
    wd.write_json(d / "ref.json", {"學員": who, "句子": [g["id"] for g in best], "起訖": [best[0]["start"], best[-1]["end"]],
                                  "分數": best_score})
    return {"ok": True, "wav": d / "ref.wav", "text": text, "長度秒": round(len(best_x) / sr, 1), "分數": best_score,
            "候選組數": len(wins)}


# ---------- 生成 ----------

def generate(workdir: str | Path, *, synth_factory=None, hear=None, align=None, check_content: bool = True,
             use_pauses: bool = True, log: Callable[[str], None] = print) -> dict:
    """`bookclub gen stunames`：選了「換成代號」的保留原聲學員名字，用各自的聲音生成代號短句。"""
    from bookclub import studentnames, tts
    from bookclub import turns as turns_mod
    from bookclub.config import load_settings

    workdir = wd.ensure(workdir)
    sp = studentnames.plan(workdir)
    items = sp["生成"]
    lp = log_path(workdir)
    record = wd.read_json(lp, default=None) or {}
    done = {r["id"]: r for r in record.get("句子", [])}
    fallback = {}
    if not items:
        log(f"[{TAG}] 沒有選「換成代號」的學員名字，不用生成。")
        wd.write_json(lp, {**record, "句子": [], "退回直接消音": {}})
        return {"句子": [], "退回直接消音": {}}

    tdata = turns_mod.page_data(workdir)
    speakers = wd.read_json(wd.speakers_path(workdir), default={}) or {}
    sentences = {s["id"]: s for s in speakers.get("sentences", [])}
    ov = wd.read_json(wd.overlap_path(workdir), default={}) or {}
    blocked = [(o["start"], o["end"]) for o in ov.get("overlaps", [])]
    blocked += [(c["start"] - 0.5, c["end"] + 0.5) for c in studentnames.find(workdir).get("candidates", [])]
    table = tts.load_pron_table()
    tolerance = load_settings().thresholds.length_tolerance
    od = out_dir(workdir)
    od.mkdir(parents=True, exist_ok=True)
    refs = {}
    load_total = 0.0

    def save() -> None:
        sents = sorted((r for r in done.values() if r["id"] in {it["id"] for it in items}), key=lambda r: r["slot"][0])
        wd.write_json(lp, {"參考音": {k: {kk: str(vv) for kk, vv in v.items()} for k, v in refs.items()},
                           "句子": sents, "退回直接消音": fallback, "載入模型秒": round(load_total, 1)})

    for who in sorted({it["學員"] for it in items}):
        ref = pick_ref(workdir, who, tdata.get("段落", []), sentences, blocked)
        refs[who] = {k: v for k, v in ref.items() if k in ("wav", "長度秒", "分數", "原因", "沿用")}
        group = [it for it in items if it["學員"] == who]
        if not ref["ok"]:
            for it in group:
                fallback[it["id"]] = ref["原因"]
            log(f"[{TAG}] {who}：{ref['原因']}，{len(group)} 句退回直接消音")
            continue
        for it in group:
            it["slot_s"] = it["slot"][1] - it["slot"][0]
            it["生成用文字"], it["發音對照"] = tts.apply_pron(it["text"], table)
        todo = [it for it in group if not (it["id"] in done and done[it["id"]].get("參考音") == str(ref["wav"])
                                           and done[it["id"]]["text"] == it["text"])]
        log(f"[{TAG}] {who}：{len(group)} 句，要生成 {len(todo)} 句（參考音 {ref.get('長度秒', '沿用')} 秒）")
        if not todo:
            continue
        extra = {it["id"]: {"學員": who, "候選": it["候選"], "參考音": str(ref["wav"]), "角色": "學員"} for it in todo}

        def save_group() -> None:
            for sid, ex in extra.items():
                if sid in done:
                    done[sid].update(ex)
            save()

        synth = synth_factory(ref["wav"], ref["text"]) if synth_factory else None
        t = time.time()
        load_total += tts.run_generation(workdir, todo, od, Path(ref["wav"]), ref["text"], tolerance, save=save_group,
                                          done=done, role="學員", tag=TAG, check_content=check_content,
                                          check_similarity=False, use_pauses=use_pauses, synth=synth, hear=hear,
                                          align=align, log=log)
        log(f"[{TAG}] {who} 完成，{time.time() - t:.0f} 秒")
    save()
    return wd.read_json(lp)


def swap_edits(workdir: str | Path, sp: dict) -> list[dict]:
    """組裝用：選「換成代號」的每一筆——生成好了就放生成檔（學員名字換代號），
    還沒生成、參考音挑不到的退回直接消音（學員名字消音）。"""
    rec = wd.read_json(log_path(Path(workdir)), default=None) or {}
    done = {r["id"]: r for r in rec.get("句子", [])}
    out = []
    for g in sp.get("生成", []):
        r = done.get(g["id"])
        if r and r.get("放回時間格") and r.get("text") == g["text"]:
            tries = r.get("嘗試") or []
            k = (r.get("選定") or 1) - 1
            out.append({"類型": "學員名字換代號", "start": g["slot"][0], "end": g["slot"][1], "id": g["id"],
                        "學員": g["學員"], "候選": g["候選"], "檔案": r["放回時間格"]["檔案"],
                        "來源檔案": r["放回時間格"].get("來源檔案"), "放回做法": r["放回時間格"].get("放回做法"),
                        "差異比例": r["放回時間格"].get("差異比例"), "text": g["text"],
                        "生成用文字": r.get("生成用文字") or g["text"],
                        "轉回文字": tries[k].get("轉回文字") if 0 <= k < len(tries) else None,
                        "生成秒數": r["放回時間格"].get("長度秒"), "要人聽": True,
                        "備註": "學員聲音生成，音色可能有差"})
        else:
            why = (rec.get("退回直接消音") or {}).get(g["id"]) or "還沒生成"
            for m in g.get("消音備援", []):
                out.append({"類型": "學員名字消音", "start": m["start"], "end": m["end"], "id": m["id"], "學員": g["學員"],
                            "候選": [m["id"]], "備註": f"選了換成代號，但{why}，先直接消音"})
    return out
