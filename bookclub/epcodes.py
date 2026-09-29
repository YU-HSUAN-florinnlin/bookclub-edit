"""這一集的代號表（09-29 宇軒：不用每一集同一個學員都用同一個英文名，只要同一集一致）。

名冊（`~/讀書會剪輯資料/名冊.csv`）只負責：認得出誰是誰（本名＋其他寫法），以及新的一集的預設代號。
這一集實際用哪個代號，照下面的先後蓋過去（後面的優先）：

1. 名冊上的英文代號（預設）
2. 第 3 步「③ 名冊上沒有的名字」選「換成代號」的（`校對/人名決定.json`）
3. 第 3 步「② 學員是誰」右欄（`校對/段落.json` 的 `本名代號`）

老師講到的名字（`名字候選.json`）、保留原聲學員講到的名字（`學員名字候選.json`）、學員重念稿的名字替換
都照這張表；候選檔裡的 `代號` 由 `sync()` 跟這張表對齊。
"""

from __future__ import annotations

from pathlib import Path

from bookclub import workdir as wd


def episode_codes(workdir: str | Path) -> dict[str, str]:
    """{本名（名冊中文名）: 這一集的代號}。"""
    from bookclub import names, personnames
    from bookclub import turns as turns_mod
    from bookclub.config import data_dir

    workdir = Path(workdir)
    roster = names.load_roster(data_dir() / "名冊.csv")
    out = {r["canonical"]: r["代號"] for r in roster if r.get("canonical") and r.get("代號")}
    canon = {r["寫法"]: r["canonical"] for r in roster}
    for name, d in (wd.read_json(personnames.decisions_path(workdir), default={}) or {}).items():
        if d.get("做法") == "換成代號" and d.get("代號"):
            out[canon.get(name, name)] = d["代號"]
    tdata = wd.read_json(turns_mod.turns_path(workdir), default={}) or {}
    for real, code in (tdata.get("本名代號") or {}).items():
        if code:
            out[canon.get(real, real)] = code
    for name, d in (wd.read_json(personnames.decisions_path(workdir), default={}) or {}).items():
        if d.get("做法") == "是上面的學員" and out.get(d.get("同一人")):   # 同一個人：跟著那位的代號走
            out[canon.get(name, name)] = out[d["同一人"]]
    return out


def same_person_names(workdir: str | Path) -> set[str]:
    """③ 選了「是上面的學員」的本名（跟別人同代號是應該的，不算重複）。"""
    from bookclub import names, personnames
    from bookclub.config import data_dir

    canon = {r["寫法"]: r["canonical"] for r in names.load_roster(data_dir() / "名冊.csv")}
    return {canon.get(n, n) for n, d in (wd.read_json(personnames.decisions_path(Path(workdir)), default={}) or {}).items()
            if d.get("做法") == "是上面的學員"}


def duplicates(workdir: str | Path, only: set[str] | None = None) -> dict[str, list[str]]:
    """同一集裡兩個以上本名用同一個代號：{代號: [本名…]}。`only`＝只看這一集有出現的本名。"""
    by_code: dict[str, list[str]] = {}
    alias = same_person_names(workdir)
    for real, code in episode_codes(workdir).items():
        if (only is None or real in only) and real not in alias:
            by_code.setdefault(code, []).append(real)
    return {c: sorted(rs) for c, rs in by_code.items() if len(rs) > 1}


def replace_table(workdir: str | Path | None = None) -> list[dict]:
    """學員重念稿的替換表：名冊寫法（代號換成這一集的）＋敏感詞。"""
    from bookclub import names
    from bookclub.config import data_dir

    rows = names.load_roster(data_dir() / "名冊.csv")
    if workdir is not None:
        codes = episode_codes(workdir)
        rows = [{**r, "代號": codes.get(r["canonical"], r["代號"])} for r in rows]
    return rows + names.load_sensitive_words(data_dir() / "敏感詞.csv")


def sync(workdir: str | Path) -> int:
    """名字候選檔的 `代號` 跟這一集的代號表對齊（敏感詞不動）。回傳改了幾筆；沒變就不寫檔。"""
    from bookclub import studentnames

    workdir = Path(workdir)
    codes = episode_codes(workdir)
    changed = 0
    for path in (wd.names_path(workdir), studentnames.cands_path(workdir)):
        data = wd.read_json(path, default=None)
        if not data or not data.get("candidates"):
            continue
        n = 0
        for c in data["candidates"]:
            if c.get("敏感詞"):
                continue
            code = codes.get(c.get("canonical") or "")
            if code and c.get("代號") != code:
                c["代號"] = code
                n += 1
        if n:
            wd.write_json(path, data)
            changed += n
    return changed
