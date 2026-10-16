"""第 4 步「AI 執行」網頁上的逐類統計：每一類要改幾筆、做完幾筆（09-29 宇軒）。

生成類（老師名字整句重念、學員重念、保留原聲學員換成代號）看生成紀錄：每生成完一句就寫進紀錄，
所以跑到一半重新整理也看得到數字往上跳。組裝類（消音、刪除）沒有逐筆紀錄，組裝做完才算完成。

只讀檔、不載入模型；讀不到的類別回「—」，不讓整頁失敗。
"""

from __future__ import annotations

import os
from pathlib import Path

from bookclub import workdir as wd


def _in_range(s: float, e: float, a: float | None, b: float | None) -> bool:
    return (a is None or e > a) and (b is None or s < b)


def _stale(items: list[dict], log: dict | None, back: dict | None = None, ref_of=None,
           all_stale: bool = False, workdir: str | Path | None = None) -> set[str]:
    """（1015-8 #228）生成程式會重新生成的句子 id：跟第 4 步「做過沒有」、生成程式挑要做的句子同一個判斷
    （`tts.record_stale`：文字、發音對照表、時間格、參考音；`tts.output_missing`：聲音檔不在；老師參考音整份換過）。
    back（保留原聲學員的「退回直接消音」）裡的不算。"""
    from bookclub import tts

    recs = {r["id"]: r for r in (log or {}).get("句子", [])}
    back = back or {}

    def ok(g: dict) -> bool:
        rec = recs.get(g["id"]) or {}
        if not rec.get("放回時間格") or all_stale:
            return False
        if workdir is not None and tts.output_missing(rec, workdir):
            return False
        if rec.get("text") is None:
            rec = {**rec, "text": g.get("text")}
        return not tts.record_stale(rec, g, ref_of(g) if ref_of else None)
    return {g["id"] for g in items if g["id"] not in back and not ok(g)}


def _gen_done(items: list[dict], log: dict | None, back: dict | None = None, ref_of=None,
              all_stale: bool = False, workdir: str | Path | None = None) -> int:
    """做完幾句。10-01：跟第 4 步「做過沒有」用同一個判斷（tts.record_stale：文字、發音對照表、時間格、參考音），
    以前只比文字，參考音換過或改了時間，這裡還寫做完、執行步驟卻說要重做。
    ref_of(g)：這一句現在的參考音檔（None＝不比）；all_stale：老師參考音整份換過，生成的都不算。
    workdir（10-04 #108）：給了的話，聲音檔不在的也不算做完（tts.output_missing，跟第 4 步「做過沒有」同一個判斷；
    只看檔案在不在、不讀內容，每 2 秒輪詢一集約 100 句沒問題）。
    1015-8 #228：判斷式抽成 `_stale`（`stale_ids` 也用它），完成＝總數 − 會重做的句數。"""
    return len(items) - len(_stale(items, log, back, ref_of, all_stale, workdir))


# ---------- 1015-8 #228：三類生成句各自的輸入（逐類統計與 `stale_ids` 共用） ----------

def _teacher_plan(workdir: Path) -> dict:
    from bookclub import nameplan

    if wd.read_json(wd.names_path(workdir), default=None):
        return nameplan.compute_plan(workdir)
    return wd.read_json(nameplan.plan_path(workdir), default=None) or {}


def _teacher_inputs(workdir: Path, plan: dict | None = None) -> dict:
    """老師（名字整句重念）：發音對照表跟生成程式用同一份（工作區裡有匯入的那份就用它，見 `execute.names_done`）。"""
    from bookclub import tts

    plan = _teacher_plan(workdir) if plan is None else plan
    own = workdir / tts.PRON_TABLE_NAME
    table = tts.load_pron_table(own if own.is_file() else None)
    gen = [{**g, "生成用文字": tts.apply_pron(g["text"], table)[0]} for g in plan.get("生成", [])]
    tlog = wd.read_json(tts.teacher_log_path(workdir), default=None)
    return {"items": gen, "log": tlog, "all_stale": tts.teacher_ref_changed(workdir, tlog)}


def _student_inputs(workdir: Path, a: float | None = None, b: float | None = None) -> dict:
    """學員段落：比「現在配到的聲線」（`students.current_refs`），沒配到的照紀錄記的（見 `execute.students_done`）。"""
    from bookclub import students, tts

    items, _ = students.build_items(workdir, a, b)
    table = tts.load_pron_table()
    items = [{**g, "生成用文字": tts.apply_pron(g["text"], table)[0]} for g in items]
    log = wd.read_json(students.log_path(workdir), default=None)
    now = students.current_refs(workdir, items) if items else {}
    recs = {r["id"]: r for r in (log or {}).get("句子", [])}

    def ref_of(g: dict):
        return now.get(g["學員"]) or wd.localize((recs.get(g["id"]) or {}).get("參考音"), workdir)
    return {"items": items, "log": log, "ref_of": ref_of, "now": now}


def _stuname_inputs(workdir: Path, plan: dict | None = None) -> dict:
    """保留原聲學員提到名字（換成代號）：退回直接消音的不算（見 `execute.stunames_done`）。"""
    from bookclub import studentgen, studentnames, tts

    plan = studentnames.plan(workdir) if plan is None else plan
    rec = wd.read_json(studentgen.log_path(workdir), default=None) or {}
    table = tts.load_pron_table()
    gen = [{**g, "生成用文字": tts.apply_pron(g["text"], table)[0]} for g in plan["生成"]]
    recs = {r["id"]: r for r in rec.get("句子", [])}
    return {"items": gen, "log": rec, "back": rec.get("退回直接消音"),
            "ref_of": lambda g: studentgen.current_ref(workdir, g["學員"], recs.get(g["id"]))}


def _role_stale(inp: dict, workdir: Path) -> set[str]:
    return _stale(inp["items"], inp.get("log"), inp.get("back"), inp.get("ref_of"), inp.get("all_stale", False), workdir)


# 角色名稱跟 `needs` 生成句表一樣（老師、學員、保留原聲學員）
ROLES = ("老師", "學員", "保留原聲學員")
_STALE_CACHE: dict[str, tuple] = {}


def _stat_key(workdir: Path) -> tuple:
    """`stale_ids` 的快取鍵（§7.2 第 6 點）：會影響判斷的輸入檔的修改時間＋大小——工作區最上層的檔（第 3 步決定、
    名字候選、重疊⋯）、`校對/`、`覆核/`、`transcript/`、`參考音/` 底下的檔、`生成/` 底下的 .json 與每個資料夾
    （聲音檔被刪、被換掉，資料夾的修改時間會變；`output_missing` 只看檔案在不在）、發音對照表、學員聲線庫。只 stat，不讀內容。"""
    from bookclub import students, tts

    out: list = []

    def add(p: Path) -> None:
        try:
            s = p.stat()
            out.append((str(p), s.st_mtime_ns, s.st_size))
        except OSError:
            out.append((str(p), None, None))

    try:
        with os.scandir(workdir) as it:
            for e in it:
                if e.is_file(follow_symlinks=False):
                    add(Path(e.path))
    except OSError:
        pass
    for sub in ("校對", "覆核", "transcript", "參考音"):
        for root, _dirs, files in os.walk(workdir / sub):
            for f in files:
                add(Path(root) / f)
    for root, _dirs, files in os.walk(workdir / "生成"):
        add(Path(root))
        for f in files:
            if f.endswith((".json", ".jsonl")):
                add(Path(root) / f)
    add(tts.pron_table_path())
    try:
        vd = students.voice_dir()
        add(vd)
        for f in sorted(vd.glob("*")) if vd.is_dir() else []:
            add(f)
    except Exception:  # noqa: BLE001 — 聲線庫讀不到：只是少一項快取鍵
        pass
    return tuple(out)


def stale_ids(workdir: str | Path, a: float | None = None, b: float | None = None, *,
              cache: bool = True) -> set[tuple[str, str]]:
    """（1015-8 #228，32 號設計 §7.2）生成程式「會重新生成」的句子：{(角色, 句id)}。三類各用自己現在的計畫
    （`nameplan.compute_plan`、`students.build_items`、`studentnames.plan`）、自己的紀錄與參考音，判斷式就是
    `_stale`（`tts.record_stale`＋`tts.output_missing`＋老師參考音整份換過）——第 4 步逐類統計（`counts`）也是這一個。
    某一類排不出計畫（還沒有段落分析、沒有名字候選⋯）就當那一類沒有要重做的。只讀檔、不載入模型。
    a、b：學員段落照這個範圍排（`needs.redo_list` 給最新一版的範圍——第 4 步照那個範圍跑，範圍切到的段落時間格才對得上）。
    輸入檔都沒變就用記憶體裡上一次算的（`_stat_key`）。"""
    workdir = Path(workdir)
    ck = f"{workdir}|{a}|{b}"
    key = _stat_key(workdir) if cache else None
    got = _STALE_CACHE.get(ck) if cache else None
    if got is not None and got[0] == key:
        return set(got[1])
    out: set[tuple[str, str]] = set()
    for role, fn in (("老師", lambda: _teacher_inputs(workdir)), ("學員", lambda: _student_inputs(workdir, a, b)),
                     ("保留原聲學員", lambda: _stuname_inputs(workdir))):
        try:
            inp = fn()
        except Exception:  # noqa: BLE001 — 這一類排不出計畫：生成程式那一類也不會做
            continue
        if inp.get("log") is None or not (inp.get("log") or {}).get("句子"):
            # 這一類從來沒生成過（沒有生成紀錄）：判斷不了哪一張「做進成品之後又變了」，交給卡片指紋那幾條
            continue
        out |= {(role, i) for i in _role_stale(inp, workdir)}
    if cache:
        _STALE_CACHE[ck] = (key, frozenset(out))
    return out


def cache_progress(cache: dict | None, items: list[dict], ref_suffix: str | dict | None = None) -> tuple[int, float | None]:
    """從 `_嘗試快取.json` 算這批句子「已經生成過至少一次」的句數，與平均每句花的秒數（09-30）。
    生成紀錄要整組跑完才寫，快取是每生成一次就寫，跑到一半也看得到進度。
    快取鍵：`id|第幾次|種子|語速|生成用文字|參考音#指紋`；同一 id、文字對得上（原文或換過發音的）才算。"""
    want = {g["id"]: {g.get("text"), g.get("生成用文字")} - {None} for g in items}
    seen: dict[str, float] = {}
    for key, att in (cache or {}).items():
        parts = key.split("|", 4)
        if len(parts) < 5 or parts[0] not in want:
            continue
        text, ref = (parts[4].rsplit("|", 1) + [""])[:2]
        if want[parts[0]] and text not in want[parts[0]]:
            continue
        want_ref = ref_suffix.get(parts[0]) if isinstance(ref_suffix, dict) else ref_suffix
        if want_ref and not ref.endswith(want_ref):   # 10-01：換過參考音（聲線）之前生成的不算
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

    def cached(od: Path, items: list[dict], ref_suffix: str | dict | None = None) -> tuple[int, float | None]:
        from bookclub import tts

        return cache_progress(wd.read_json(od / tts.ATTEMPT_CACHE, default=None), items, ref_suffix)

    plan_cache: dict = {}

    def tplan():
        if "p" not in plan_cache:
            plan_cache["p"] = _teacher_plan(workdir)
        return plan_cache["p"]

    def t_gen():
        from bookclub import tts

        inp = _teacher_inputs(workdir, tplan())
        gen = inp["items"]
        fp = tts.ref_fingerprint(wd.ref_dir(workdir) / "ref.wav")
        return (len(gen), len(gen) - len(_role_stale(inp, workdir)),
                *cached(tts.teacher_out_dir(workdir), gen, f"#{fp}" if fp else None))

    def t_mute():
        n = len(tplan().get("消音", []))
        return n, n if render_done else 0

    def stu():
        from bookclub import students, tts

        inp = _student_inputs(workdir, a, b)
        items = inp["items"]
        fps = {who: tts.ref_fingerprint(f) for who, f in inp["now"].items() if f}
        suffix = {g["id"]: f"#{fps[g['學員']]}" for g in items if fps.get(g["學員"])}
        return (len(items), len(items) - len(_role_stale(inp, workdir)),
                *cached(students.out_dir(workdir), items, suffix))

    sp_cache: dict = {}

    def splan():
        from bookclub import studentnames

        if "p" not in sp_cache:
            sp_cache["p"] = studentnames.plan(workdir)
        return sp_cache["p"]

    def sn_gen():
        from bookclub import studentgen

        inp = _stuname_inputs(workdir, splan())
        gen = inp["items"]
        return (len(gen), len(gen) - len(_role_stale(inp, workdir)), *cached(studentgen.out_dir(workdir), gen))

    def sn_mute():
        n = len(splan()["消音"])
        return n, n if render_done else 0

    def ranged(key: str):
        from bookclub import review

        dec = review.load_decisions(workdir)
        n = sum(1 for c in dec[key] if c.get("狀態") != "還原" and _in_range(c["start"], c["end"], a, b))
        return n, n if render_done else 0

    add("老師提到名字", "老師重念（整句、名字換成代號）", "生成", t_gen)   # 10-01 第三批 7：統一叫法
    try:   # 10-01 第三批 6：落在剪掉的片段裡的名字不生成、不算；寫出來，第 3 步的「已剪掉」對得起來
        from bookclub import nameplan

        n_cut = sum(1 for x in tplan().get("略過", []) if x.get("原因") == nameplan.CUT_SKIP)
        if n_cut and rows and rows[-1]["類型"] == "老師提到名字":
            rows[-1]["另外"] = f"另外 {n_cut} 個名字落在剪掉的片段裡，不生成、不算"
    except Exception:  # noqa: BLE001
        pass
    add("老師提到名字", "消音（直接消掉名字）", "組裝", t_mute)
    add("學員段落", "學員重念（用替代聲音）", "生成", stu)
    add("保留原聲學員提到名字", "學員重念（用他自己的聲音，名字換成代號）", "生成", sn_gen)
    add("保留原聲學員提到名字", "消音（直接消掉名字）", "組裝", sn_mute)
    add("剪掉", "聲音和畫面都拿掉，影片會變短", "組裝", lambda: ranged("刪除段落"))
    add("消音", "只拿掉聲音，畫面留著", "組裝", lambda: ranged("局部消音"))
    return rows


def remaining_seconds(rows: list[dict]) -> int | None:
    """生成類預估還要多久（秒）。還沒有任何一句生成過、算不出平均的類別不算；全部都算不出來回 None。"""
    gen = [r for r in rows if "已生成" in r and r.get("總數")]
    left = [r for r in gen if r["總數"] > r["已生成"]]
    if not left:
        return 0 if gen else None
    vals = [r["預估剩餘秒數"] for r in left if r.get("預估剩餘秒數") is not None]
    return sum(vals) if vals else None   # 還有要生成的、但一句都還沒生成完：算不出來
