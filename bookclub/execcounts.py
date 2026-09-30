"""第 4 步「AI 執行」網頁上的逐類統計：每一類要改幾筆、做完幾筆（09-29 宇軒）。

生成類（老師名字整句重念、學員重念、保留原聲學員換成代號）看生成紀錄：每生成完一句就寫進紀錄，
所以跑到一半重新整理也看得到數字往上跳。組裝類（消音、刪除）沒有逐筆紀錄，組裝做完才算完成。

只讀檔、不載入模型；讀不到的類別回「—」，不讓整頁失敗。
"""

from __future__ import annotations

from pathlib import Path

from bookclub import workdir as wd


def _in_range(s: float, e: float, a: float | None, b: float | None) -> bool:
    return (a is None or e > a) and (b is None or s < b)


def _gen_done(items: list[dict], log: dict | None, back: dict | None = None) -> int:
    recs = {r["id"]: r for r in (log or {}).get("句子", [])}
    back = back or {}
    return sum(1 for g in items if g["id"] in back
               or ((recs.get(g["id"]) or {}).get("放回時間格") and recs[g["id"]].get("text") in (None, g["text"])))


def cache_progress(cache: dict | None, items: list[dict]) -> tuple[int, float | None]:
    """從 `_嘗試快取.json` 算這批句子「已經生成過至少一次」的句數，與平均每句花的秒數（09-30）。
    生成紀錄要整組跑完才寫，快取是每生成一次就寫，跑到一半也看得到進度。
    快取鍵：`id|第幾次|種子|語速|生成用文字|參考音#指紋`；同一 id、文字對得上（原文或換過發音的）才算。"""
    want = {g["id"]: {g.get("text"), g.get("生成用文字")} - {None} for g in items}
    seen: dict[str, float] = {}
    for key, att in (cache or {}).items():
        parts = key.split("|", 4)
        if len(parts) < 5 or parts[0] not in want:
            continue
        text = parts[4].rsplit("|", 1)[0]
        if want[parts[0]] and text not in want[parts[0]]:
            continue
        seen[parts[0]] = seen.get(parts[0], 0.0) + float((att or {}).get("elapsed_s") or 0.0)
    avg = sum(seen.values()) / len(seen) if seen else None
    return len(seen), avg


def counts(workdir: str | Path, a: float | None = None, b: float | None = None,
           render_done: bool = False) -> list[dict]:
    """回傳 [{類型, 做法, 總數, 完成, 階段}]；生成類另外帶 `已生成`（從快取算，跑到一半就會跳）與
    `預估剩餘秒數`。總數 0 的也列（讓人知道這類沒東西）；讀失敗的總數給 None。"""
    workdir = Path(workdir)
    rows: list[dict] = []

    def add(kind: str, how: str, stage: str, fn) -> None:
        extra = {}
        try:
            got = fn()
            total, done = got[0], got[1]
            if len(got) > 2:   # 生成類：(總數, 完成, 已生成, 平均每句秒數)
                made, avg = got[2], got[3]
                made = max(made, done)
                extra = {"已生成": made, "預估剩餘秒數": round((total - made) * avg) if avg and total > made else
                         (0 if total <= made else None)}
        except Exception:  # noqa: BLE001 — 某一類讀不到不影響其他類
            total, done = None, None
        rows.append({"類型": kind, "做法": how, "階段": stage, "總數": total, "完成": done, **extra})

    def cached(od: Path, items: list[dict]) -> tuple[int, float | None]:
        from bookclub import tts

        return cache_progress(wd.read_json(od / tts.ATTEMPT_CACHE, default=None), items)

    def teacher():
        from bookclub import nameplan

        if wd.read_json(wd.names_path(workdir), default=None):
            plan = nameplan.compute_plan(workdir)
        else:
            plan = wd.read_json(nameplan.plan_path(workdir), default=None) or {}
        return plan

    plan_cache: dict = {}

    def tplan():
        if "p" not in plan_cache:
            plan_cache["p"] = teacher()
        return plan_cache["p"]

    def t_gen():
        from bookclub import tts

        gen = tplan().get("生成", [])
        table = tts.load_pron_table()
        gen = [{**g, "生成用文字": tts.apply_pron(g["text"], table)[0]} for g in gen]
        return (len(gen), _gen_done(gen, wd.read_json(tts.teacher_log_path(workdir), default=None)),
                *cached(tts.teacher_out_dir(workdir), gen))

    def t_mute():
        n = len(tplan().get("消音", []))
        return n, n if render_done else 0

    def stu():
        from bookclub import students

        from bookclub import tts

        items, _ = students.build_items(workdir, a, b)
        table = tts.load_pron_table()
        items = [{**g, "生成用文字": tts.apply_pron(g["text"], table)[0]} for g in items]
        return (len(items), _gen_done(items, wd.read_json(students.log_path(workdir), default=None)),
                *cached(students.out_dir(workdir), items))

    sp_cache: dict = {}

    def splan():
        from bookclub import studentnames

        if "p" not in sp_cache:
            sp_cache["p"] = studentnames.plan(workdir)
        return sp_cache["p"]

    def sn_gen():
        from bookclub import studentgen

        from bookclub import tts

        gen = splan()["生成"]
        rec = wd.read_json(studentgen.log_path(workdir), default=None) or {}
        table = tts.load_pron_table()
        gen = [{**g, "生成用文字": tts.apply_pron(g["text"], table)[0]} for g in gen]
        return (len(gen), _gen_done(gen, rec, rec.get("退回直接消音")), *cached(studentgen.out_dir(workdir), gen))

    def sn_mute():
        n = len(splan()["消音"])
        return n, n if render_done else 0

    def ranged(key: str):
        from bookclub import review

        dec = review.load_decisions(workdir)
        n = sum(1 for c in dec[key] if c.get("狀態") != "還原" and _in_range(c["start"], c["end"], a, b))
        return n, n if render_done else 0

    add("老師提到名字", "老師 AI 聲音整句重念", "生成", t_gen)
    add("老師提到名字", "直接消音", "組裝", t_mute)
    add("學員段落", "匿名聲線重念", "生成", stu)
    add("保留原聲學員提到名字", "用學員自己的聲音換成代號", "生成", sn_gen)
    add("保留原聲學員提到名字", "直接消音", "組裝", sn_mute)
    add("刪除段落", "剪掉片段：聲音和畫面都拿掉，影片會變短", "組裝", lambda: ranged("刪除段落"))
    add("局部消音", "消音：只拿掉聲音，畫面留著", "組裝", lambda: ranged("局部消音"))
    return rows


def remaining_seconds(rows: list[dict]) -> int | None:
    """生成類預估還要多久（秒）。還沒有任何一句生成過、算不出平均的類別不算；全部都算不出來回 None。"""
    gen = [r for r in rows if "已生成" in r and r.get("總數")]
    left = [r for r in gen if r["總數"] > r["已生成"]]
    if not left:
        return 0 if gen else None
    vals = [r["預估剩餘秒數"] for r in left if r.get("預估剩餘秒數") is not None]
    return sum(vals) if vals else None   # 還有要生成的、但一句都還沒生成完：算不出來
