"""這一集的代號表（09-29 宇軒：不用每一集同一個學員都用同一個英文名，只要同一集一致）。

名冊（`~/讀書會剪輯資料/名冊.csv`）只負責認得出誰是誰（本名＋其他寫法）；09-29 宇軒決定拿掉英文代號欄，
每一集自己選代號（`CODE_POOL` 是給選單用的名單，10-02 起改成外國人名的中文寫法，也可以自己打）。這一集的代號照下面的先後蓋過去（後面的優先）：

1. 名冊上的英文代號（舊名冊還有這一欄才會用到，相容用）
2. 第 3 步「③ 名冊上沒有的名字」選「換成代號」的（`校對/人名決定.json`）
3. 第 3 步「② 學員是誰」右欄（`校對/段落.json` 的 `本名代號`）

老師講到的名字（`名字候選.json`）、保留原聲學員講到的名字（`學員名字候選.json`）、學員重念稿的名字替換
都照這張表；候選檔裡的 `代號` 由 `sync()` 跟這張表對齊。
"""

from __future__ import annotations

from pathlib import Path

from bookclub import workdir as wd


# 10-02 第七批（任務單 26 號）：代號一律用外國人名的中文寫法（聲音模型念英文名常念得怪、每次不一樣；
# 不用中文名，是要讓學員一聽就知道這是抽換過的）。宇軒 10-02 定稿，選單只顯示中文、分女男。
# 括號裡的英文只用來對應舊代號（`OLD_TO_NEW`，換代號時預先帶出）。
NEW_CODES_F = (("安娜", "Anna"), ("貝拉", "Bella"), ("克洛伊", "Chloe"), ("黛西", "Daisy"), ("艾瑪", "Emma"),
               ("費歐娜", "Fiona"), ("漢娜", "Hannah"), ("艾瑞絲", "Iris"), ("賈斯敏", "Jasmine"), ("潔西", "Jessie"),
               ("茱莉亞", "Julia"), ("凱特", "Kate"), ("蘿拉", "Laura"), ("露西", "Lucy"), ("米亞", "Mia"),
               ("妮娜", "Nina"), ("蘿絲", "Rose"), ("露比", "Ruby"))
NEW_CODES_M = (("傑克", "Jack"), ("湯姆", "Tom"), ("大衛", "David"), ("麥可", "Michael"), ("亨利", "Henry"),
               ("凱文", "Kevin"), ("安迪", "Andy"), ("馬克", "Mark"), ("里歐", "Leo"), ("山姆", "Sam"), ("保羅", "Paul"))
CODE_POOL_F = tuple(c for c, _ in NEW_CODES_F)
CODE_POOL_M = tuple(c for c, _ in NEW_CODES_M)
CODE_POOL = CODE_POOL_F + CODE_POOL_M
OLD_TO_NEW = {en: zh for zh, en in NEW_CODES_F + NEW_CODES_M}
# 10-02 之前選單上的英文名（換代號、總檢查找「要念的文字裡還有舊英文代號」用）
_OLD_POOL = (
    "Amy", "Anna", "Bella", "Chloe", "Claire", "Daisy", "Ella", "Emma", "Fiona", "Grace", "Hannah", "Iris", "Ivy",
    "Jasmine", "Joan", "Julia", "Kate", "Laura", "Lily", "Lucy", "Mia", "Nina", "Olivia", "Rose", "Ruby", "Sara",
    "Tina", "Vivian", "Wendy", "Zoe",
    "Adam", "Ben", "Chris", "Daniel", "David", "Eric", "Frank", "Henry", "Ian", "Jack", "Jason", "Kevin", "Leo",
    "Mark", "Max", "Nick", "Oscar", "Paul", "Ray", "Sam", "Tom", "Victor", "Will",
)
OLD_CODE_POOL = tuple(sorted(set(_OLD_POOL) | set(OLD_TO_NEW)))


def suggest_chinese(code: str | None) -> str | None:
    """舊英文代號 → 新名單上的中文寫法（不分大小寫）；舊名單上沒有對應的回 None。"""
    low = (code or "").strip().lower()
    return next((zh for en, zh in OLD_TO_NEW.items() if en.lower() == low), None)


def code_options(workdir: str | Path | None = None) -> list[str]:
    """代號選單：中文名單（女、男）＋這一集已經用到的（自己打過的、還沒換掉的英文代號也在）。"""
    used = set(episode_codes(workdir).values()) if workdir is not None else set()
    return list(CODE_POOL) + sorted(c for c in used - set(CODE_POOL) if c)


def code_groups(workdir: str | Path | None = None) -> dict[str, list[str]]:
    """代號選單分組（網頁用）：{女: [...], 男: [...], 這一集用到的其他代號: [...]}。"""
    used = set(episode_codes(workdir).values()) if workdir is not None else set()
    return {"女": list(CODE_POOL_F), "男": list(CODE_POOL_M),
            "這一集用到的其他代號": sorted(c for c in used - set(CODE_POOL) if c)}


def episode_codes(workdir: str | Path) -> dict[str, str]:
    """{本名（名冊中文名）: 這一集的代號}。"""
    from bookclub import names, personnames
    from bookclub import turns as turns_mod
    from bookclub.config import data_dir

    workdir = Path(workdir)
    roster = names.load_roster(data_dir() / "名冊.csv")
    out = {r["canonical"]: r["代號"] for r in roster if r.get("canonical") and r.get("代號")}
    canon = {r["寫法"]: r["canonical"] for r in roster}
    canon.update({r["canonical"]: r["canonical"] for r in roster})   # 本名優先：一個名字同時是某列本名、又是別列其他寫法時
    for name, d in (wd.read_json(personnames.decisions_path(workdir), default={}) or {}).items():
        if d.get("做法") == "換成代號" and d.get("代號"):
            out[canon.get(name, name)] = d["代號"]
    tdata = wd.read_json(turns_mod.turns_path(workdir), default={}) or {}
    # 人名清單裡寫成同一個人的（例如「宜君」其他寫法「怡君」）：② 右欄選了其中一個，整組跟著換
    groups = [{p["名字"], *p["其他寫法"], *([p["名冊本名"]] if p.get("名冊本名") else [])}
              for p in (wd.read_json(personnames.people_path(workdir), default={}) or {}).get("人名", [])]
    for real, code in (tdata.get("本名代號") or {}).items():
        if code:
            for g in [g for g in groups if real in g] or [{real}]:
                for n in g | {real}:
                    out[canon.get(n, n)] = code
    for name, d in (wd.read_json(personnames.decisions_path(workdir), default={}) or {}).items():
        if d.get("做法") == "是上面的學員" and out.get(d.get("同一人")):   # 同一個人：跟著那位的代號走
            out[canon.get(name, name)] = out[d["同一人"]]
    return out


def same_person_names(workdir: str | Path) -> set[str]:
    """③ 選了「是上面的學員」的本名（跟別人同代號是應該的，不算重複）。"""
    from bookclub import names, personnames
    from bookclub.config import data_dir

    roster = names.load_roster(data_dir() / "名冊.csv")
    canon = {r["寫法"]: r["canonical"] for r in roster}
    canon.update({r["canonical"]: r["canonical"] for r in roster})
    return {canon.get(n, n) for n, d in (wd.read_json(personnames.decisions_path(Path(workdir)), default={}) or {}).items()
            if d.get("做法") == "是上面的學員"}


def duplicates(workdir: str | Path, only: set[str] | None = None) -> dict[str, list[str]]:
    """同一集裡兩個以上本名用同一個代號：{代號: [本名…]}。`only`＝只看這一集有出現的本名。"""
    from bookclub import personnames

    by_code: dict[str, list[str]] = {}
    alias = same_person_names(workdir)
    for real, code in episode_codes(workdir).items():
        if (only is None or real in only) and real not in alias:
            by_code.setdefault(code, []).append(real)
    # 人名清單裡寫成同一個人的（例如「宜君」其他寫法「怡君」）不算重複
    groups = [{p["名字"], *p["其他寫法"], *([p["名冊本名"]] if p.get("名冊本名") else [])}
              for p in (wd.read_json(personnames.people_path(Path(workdir)), default={}) or {}).get("人名", [])]

    def distinct(rs: list[str]) -> bool:
        return not any(set(rs) <= g for g in groups)

    return {c: sorted(rs) for c, rs in by_code.items() if len(rs) > 1 and distinct(rs)}


def replace_table(workdir: str | Path | None = None) -> list[dict]:
    """學員重念稿的替換表：名冊寫法（代號換成這一集的）＋敏感詞。"""
    from bookclub import names
    from bookclub.config import data_dir

    rows = names.load_roster(data_dir() / "名冊.csv")
    if workdir is not None:
        codes = episode_codes(workdir)
        rows = [{**r, "代號": codes.get(r["canonical"], r["代號"])} for r in rows]
    return rows + names.load_sensitive_words(data_dir() / "敏感詞.csv")


SNAPSHOT_NAME = "代號表紀錄.json"   # 放在 校對/：上一次對齊時的代號表，拿來看哪個本名的代號改了


def snapshot_path(workdir: str | Path) -> Path:
    return Path(workdir) / "校對" / SNAPSHOT_NAME


def _code_pattern(code: str):
    """英文代號前後不能接英文字母（Ann 不會比對到 Anna 裡）。"""
    import re

    pat = re.escape(code)
    return re.compile(rf"(?<![A-Za-z]){pat}(?![A-Za-z])" if code.isascii() else pat)


def has_code(text: str, code: str) -> bool:
    return bool(text and code and _code_pattern(code).search(text))


def replace_code(text: str, old: str, new: str) -> str:
    """文字裡的舊代號換成新代號。"""
    if not text or not old:
        return text
    return _code_pattern(old).sub(lambda _m: new, text)


def propagate(workdir: str | Path, codes: dict[str, str] | None = None) -> dict:
    """代號改了（09-29）：已經寫進文字的舊代號跟著換——學員段落的校對稿（通過時存的是換好代號的稿子）、
    老師名字那一句人改過的「改稿」。

    舊代號如果還有別人在用（同一集不同人共用一個代號），分不出文字裡的是誰，不自動換：
    含舊代號的學員段落改回「還沒確認」，讓人再看一次。第一次呼叫只記下代號表。"""
    from bookclub import review
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    now = codes if codes is not None else episode_codes(workdir)
    before = wd.read_json(snapshot_path(workdir), default=None)
    result = {"換": {}, "要再看": []}
    if before is None or before == now:
        if before is None and snapshot_path(workdir).parent.is_dir():
            wd.write_json(snapshot_path(workdir), now)
        return result
    moves = [(old, now[real]) for real, old in before.items() if old and now.get(real) and now[real] != old]
    olds = [o for o, _ in moves]
    still = set(now.values())
    swap = {o: n for o, n in moves if o not in still and olds.count(o) == 1}
    unsure = {o for o in olds if o not in swap}
    result["換"] = swap

    if swap or unsure:
        tp = turns_mod.turns_path(workdir)
        with turns_mod._lock:
            data = wd.read_json(tp, default=None)
            if data:
                for t in data.get("段落", []):
                    txt = t.get("校對稿") or ""
                    for o, n in swap.items():
                        txt = replace_code(txt, o, n)
                    if txt != (t.get("校對稿") or ""):
                        t["校對稿"] = txt
                    if t.get("已確認") and any(has_code(txt, o) for o in unsure):
                        t["已確認"] = False
                        t["代號改過"] = "、".join(sorted(unsure))
                        result["要再看"].append(t["id"])
                wd.write_json(tp, data)
        if swap:
            dp = review.name_decisions_path(workdir)
            with review._lock:
                dec = wd.read_json(dp, default=None)
                if dec:
                    n_changed = 0
                    for d in dec.values():
                        if isinstance(d, dict) and d.get("改稿"):
                            txt = d["改稿"]
                            for o, n in swap.items():
                                txt = replace_code(txt, o, n)
                            if txt != d["改稿"]:
                                d["改稿"] = txt
                                n_changed += 1
                    if n_changed:
                        wd.write_json(dp, dec)
    wd.write_json(snapshot_path(workdir), now)
    return result


def sync(workdir: str | Path) -> int:
    """名字候選檔的 `代號` 跟這一集的代號表對齊（敏感詞不動）。回傳改了幾筆；沒變就不寫檔。
    09-29：同時把已經寫進校對稿、改稿裡的舊代號換成新的（`propagate`）。"""
    from bookclub import studentnames

    workdir = Path(workdir)
    codes = episode_codes(workdir)
    propagate(workdir, codes)
    changed = 0
    for path in (wd.names_path(workdir), studentnames.cands_path(workdir)):
        data = wd.read_json(path, default=None)
        if not data or not data.get("candidates"):
            continue
        n = 0
        for c in data["candidates"]:
            if c.get("敏感詞"):
                continue
            code = codes.get(c.get("canonical") or "") or ""   # 這一集還沒選代號＝空的（第 4 步開始前會擋）
            if c.get("代號") != code:
                c["代號"] = code
                n += 1
        if n:
            wd.write_json(path, data)
            changed += n
    return changed


def missing(workdir: str | Path) -> list[str]:
    """第 4 步開始前：這一集還沒有代號、又會用到的本名（要重念的學員、老師講到的名冊名字）。"""
    from bookclub import personnames, review
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    codes = episode_codes(workdir)
    dec = review.load_decisions(workdir)
    tdata = wd.read_json(turns_mod.turns_path(workdir), default={}) or {}
    cuts = [(c["start"], c["end"]) for c in dec["刪除段落"] if c.get("狀態") != "還原"]
    out = set()
    for name, p in tdata.get("學員", {}).items():
        if p.get("本名") and p.get("段數") and not codes.get(p["本名"]):
            segs = [t for t in tdata.get("段落", []) if t["說話者"] == name]
            if not all(review._in_ranges(t["start"], t["end"], cuts) for t in segs):
                out.add(p["本名"])
    skip = {n for n, d in (wd.read_json(personnames.decisions_path(workdir), default={}) or {}).items()
            if d.get("做法") in ("不用處理", "不是名字")}
    for c in (wd.read_json(wd.names_path(workdir), default={}) or {}).get("candidates", []):
        k = c.get("canonical")
        if k and not c.get("敏感詞") and not codes.get(k) and k not in skip \
                and not review._in_ranges(c["start"], c["end"], cuts):
            out.add(k)
    return sorted(out)


def auto_assign(workdir: str | Path) -> dict:
    """「幫還沒代號的自動配」：② 選了本名的學員、③ 名冊上的人（沒選不用處理／不是名字）還沒代號的，
    依序配 `CODE_POOL` 裡這一集還沒用過的代號。② 的寫進右欄，③ 的寫成「換成代號」決定。"""
    from bookclub import personnames
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    codes = episode_codes(workdir)
    free = [c for c in CODE_POOL if c not in set(codes.values())]
    tdata = wd.read_json(turns_mod.turns_path(workdir), default={}) or {}
    two = sorted({p["本名"] for p in tdata.get("學員", {}).values() if p.get("本名") and not codes.get(p["本名"])})
    done2, done3 = [], []
    for real in two:
        if not free:
            break
        turns_mod.set_name_code(workdir, real, free.pop(0))
        done2.append(real)
    codes = episode_codes(workdir)
    for u in personnames.mentioned(workdir):
        if not free:
            break
        canon = u.get("名冊本名") or u["名字"]
        if u["② 已決定"] or u["都在刪除段落"] or codes.get(canon) or u["做法"] != "換成代號":
            continue
        personnames.decide(workdir, u["名字"], "換成代號", free.pop(0))
        done3.append(u["名字"])
        codes = episode_codes(workdir)
    return {"ok": True, "②": len(done2), "③": len(done3), "代號不夠": not free and bool(two or done3)}
