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

import functools
import threading
from pathlib import Path

from bookclub import workdir as wd

# 10-07 審查：挑代號到寫進去要在同一把鎖裡（② 連續快速選兩位時，兩個請求同時挑到同一個代號）。
# 自動配、② 右欄手動選代號（turns.set_name_code）、③ 人名決定（personnames.decide）都拿這把；可重入。
_auto_lock = threading.RLock()


def _locked(fn):
    @functools.wraps(fn)
    def wrap(*a, **k):
        with _auto_lock:
            return fn(*a, **k)
    return wrap


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
    dec = wd.read_json(personnames.decisions_path(workdir), default={}) or {}
    spell = episode_spellings(workdir, roster=roster, dec=dec)
    for name, d in dec.items():
        if d.get("做法") == "換成代號" and d.get("代號"):
            out[name if name in spell else canon.get(name, name)] = d["代號"]   # 1016-3 審查 A：這一集自己決定的寫法不改到名冊那個人
    tdata = wd.read_json(turns_mod.turns_path(workdir), default={}) or {}
    # 人名清單裡寫成同一個人的（例如「宜君」其他寫法「怡君」）：② 右欄選了其中一個，整組跟著換
    groups = [{p["名字"], *p["其他寫法"], *([p["名冊本名"]] if p.get("名冊本名") else [])}
              for p in (wd.read_json(personnames.people_path(workdir), default={}) or {}).get("人名", [])]
    for real, code in (tdata.get("本名代號") or {}).items():
        if code:
            me = canon.get(real, real)
            for g in [g for g in groups if real in g] or [{real}]:
                for n in g | {real}:
                    if canon.get(n, me) != me:   # 10-18 #246 修法 1：名冊上是別人的寫法不跟著套（人名清單把別人的暱稱併進來）
                        continue
                    out[canon.get(n, n)] = code
    for name, d in dec.items():
        if d.get("做法") == "是上面的學員" and out.get(d.get("同一人")):   # 同一個人：跟著那位的代號走
            out[name if name in spell else canon.get(name, name)] = out[d["同一人"]]
    # 1016-3 審查 A：名冊是多支影片共用的；這一集 ③ 自己決定的寫法（名冊上是別人的其他寫法）用這一集的決定
    for w, (target, how, _who) in spell.items():
        if how == "是上面的學員":
            if out.get(target):
                out[w] = out[target]
        elif how == "換成代號":
            if dec.get(target, {}).get("代號"):
                out[w] = dec[target]["代號"]
        else:   # 不用處理、不是名字：這一集不換
            out.pop(w, None)
    return out


OWN_KEY = "這一集不跟名冊"   # 舊欄位（e0bf2f0）：以前選過同一人、後來改成別的；照讀相容，新的決定改記 AT_KEY
AT_KEY = "決定時名冊對到"     # 人名決定：人親手選換成代號／不用處理／不是名字時，名冊把這個名字對到誰（沒有＝空字串、這一集自己＝名字本身）
SELF_HOWS = ("換成代號", "不用處理", "不是名字")


def roster_canon(roster: list[dict]) -> dict[str, str]:
    """{寫法: 本名}，本名優先（一個名字同時是某列本名、又是別列其他寫法時算那一列本人）。"""
    canon = {r["寫法"]: r["canonical"] for r in roster}
    canon.update({r["canonical"]: r["canonical"] for r in roster})
    return canon


def episode_spellings(workdir: str | Path, roster: list[dict] | None = None,
                      dec: dict | None = None) -> dict[str, tuple[str, str, str | None]]:
    """1016-3 審查 A：這一集 ③ 自己決定、跟名冊對法不一樣的寫法 {寫法: (這一集算誰, 做法, 名冊上對到的別人)}。
    名冊（`~/讀書會剪輯資料/名冊.csv`）多支影片共用，一支影片改決定**不改名冊**，改成這一集自己蓋過去：
    - 「同一人：某某」：這個名字（與人名清單上的其他寫法）算某某；名冊把它對到別人（或它自己一列）時蓋過去
    - 人親手選的不用處理／不是名字：照原聲留著的寫法（`kept_spellings`，10-18 #250）這一集不換——
      名冊上別的列把這個寫法對到別人時蓋過去（例如別支影片之後才把它加成某位學員的其他寫法）
    - 人親手選的換成代號：名冊**現在**對到的人跟決定當時記的不一樣（別支影片後來加的、以前選過同一人）→ 這一集用自己選的代號
    自動配的、10-17 以前沒有這兩個欄位的照舊跟名冊走。"""
    from bookclub import names, personnames
    from bookclub.config import data_dir

    workdir = Path(workdir)
    if roster is None:
        roster = names.load_roster(data_dir() / "名冊.csv")
    if dec is None:
        dec = wd.read_json(personnames.decisions_path(workdir), default={}) or {}
    canon = roster_canon(roster)
    every: dict[str, set[str]] = {}
    for r in roster:
        every.setdefault(r["寫法"], set()).add(r["canonical"])
        every.setdefault(r["canonical"], set()).add(r["canonical"])
    alts = {p["名字"]: p.get("其他寫法") or [] for p in (wd.read_json(personnames.people_path(workdir), default={}) or {}).get("人名", [])}
    out: dict[str, tuple[str, str, str | None]] = {}
    for name, d in dec.items():
        if not isinstance(d, dict):
            continue
        how = d.get("做法")
        if how == "是上面的學員":
            target = d.get("同一人")
            if not target:
                continue
            for w in [name, *alts.get(name, [])]:
                c = canon.get(w)
                if c and c != target:
                    out[w] = (target, how, c)
            continue
        if how not in SELF_HOWS or d.get("自動配"):
            continue
        ws = [name, *alts.get(name, [])]
        if how != "換成代號":
            # 10-18 #250（推翻 1016-3 的「名冊上有就照樣拿掉」）：不用處理／不是名字一律照原聲留著，
            # 只有名冊上是別人的寫法（人名清單把別人的暱稱併進來）照舊跟名冊走——一個寫法一個寫法看
            ws = spellings_of(name, alts.get(name, []), roster)
            kept = kept_spellings(name, d, ws, every)
            for w in ws:
                foreign = sorted(every.get(w, set()) - {w, name})
                if w in kept and foreign:
                    out[w] = (name, how, foreign[0])
            continue
        if d.get(OWN_KEY):
            base_of = lambda w: name  # noqa: E731
        elif AT_KEY in d:
            at = d.get(AT_KEY)
            base_of = (lambda w: at.get(w) or None) if isinstance(at, dict) else (lambda w: at or None)
        else:
            continue
        for w in ws:
            base = base_of(w)
            foreign = sorted(every.get(w, set()) - {w, name, *([base] if base else [])})
            if foreign:
                out[w] = (name, how, foreign[0])
    return out


def at_map(name: str, d: dict, spellings: list[str]) -> dict[str, str | None]:
    """`決定時名冊對到` 讀成 {寫法: 對到誰（空字串＝決定時名冊上沒有）}；沒記到的寫法是 None（不知道，當成名冊上有）。
    舊格式（只記名字本身的字串）：只有名字本身算數。"""
    at = d.get(AT_KEY)
    if isinstance(at, dict):
        return {w: at.get(w) for w in spellings}
    return {w: (at if (w == name and isinstance(at, str)) else None) for w in spellings}


def roster_owners(roster: list[dict]) -> dict[str, set[str]]:
    """{寫法: 名冊上對到的本名們}（本名自己也算一個寫法）。"""
    every: dict[str, set[str]] = {}
    for r in roster:
        every.setdefault(r["寫法"], set()).add(r["canonical"])
        every.setdefault(r["canonical"], set()).add(r["canonical"])
    return every


def spellings_of(name: str, alts: list[str], roster: list[dict]) -> list[str]:
    """③ 一列的名字涵蓋的寫法：名字本身、人名清單上的其他寫法、名冊上本名是這個名字的每一列寫法（10-18 #250）。"""
    return list(dict.fromkeys([name, *alts, *(r["寫法"] for r in roster if r["canonical"] == name)]))


def kept_spellings(name: str, d: dict, spellings: list[str], every: dict[str, set[str]] | None = None) -> set[str]:
    """③ 人親手選了不用處理／不是名字、成品照原聲留著的寫法（10-18 #250，宇軒：選這兩個一定保留原本的聲音；
    推翻 1016-3「決定時名冊上就有的照樣拿掉」）。程式自動配的不算。
    照原聲的：`spellings`（見 `spellings_of`）裡，名冊上沒有、或名冊上就是這個人（這個名字本身，或名冊把這個名字對到的本名）的寫法。
    名冊上是別人的寫法不算（例如人名清單把別人的暱稱併進來，那個暱稱照舊換成那個人的英文名），
    除非決定當時名冊上還沒有它（`決定時名冊對到` 記成空的：之後別支影片才加進名冊的照舊照原聲），
    或是這一集選「同一人」時自己加進名冊的（`加進名冊的其他寫法`）。`every` 是 `roster_owners`，沒給就不看名冊（全部照原聲）。"""
    if not isinstance(d, dict) or d.get("做法") not in ("不用處理", "不是名字") or d.get("自動配"):
        return set()
    m = at_map(name, d, spellings)
    rec = set((d.get("加進名冊的其他寫法") or {}).get("寫法") or [])
    me = {name, *(every or {}).get(name, set())}   # 只看名字本身對到誰（不看人名清單其他寫法對到誰，免得別人的暱稱被算成自己）
    return {w for w in spellings if m.get(w) == "" or w in rec or not ((every or {}).get(w, set()) - me - {w})}


def keep_original_names(workdir: str | Path, dec: dict | None = None) -> dict[str, set[str]]:
    """{名字: 照原聲留著的寫法}（`kept_spellings`；第 4 步排計畫時，用這些寫法比對到、沒代號的名字候選略過；
    保留原聲學員講到這些寫法也預設略過，10-18 #250）。"""
    from bookclub import names, personnames
    from bookclub.config import data_dir

    workdir = Path(workdir)
    if dec is None:
        dec = wd.read_json(personnames.decisions_path(workdir), default={}) or {}
    roster = names.load_roster(data_dir() / "名冊.csv")
    every = roster_owners(roster)
    alts = {p["名字"]: p.get("其他寫法") or [] for p in (wd.read_json(personnames.people_path(workdir), default={}) or {}).get("人名", [])}
    out = {}
    for n, d in dec.items():
        k = kept_spellings(n, d, spellings_of(n, alts.get(n, []), roster), every)
        if k:
            out[n] = k
    return out


def is_kept_original(c: dict, keep: dict[str, set[str]]) -> bool:
    """這一筆名字候選照原聲留著（10-18 #250）：③ 選了不用處理／不是名字（`keep_original_names`）、比對到的寫法在裡面、
    這一集沒有代號、不是敏感詞。老師名字（`nameplan.compute_plan`）、保留原聲學員（`studentnames`）、第 5 步「需留意」同一個判斷。"""
    return (not c.get("敏感詞") and not (c.get("代號") or "").strip()
            and c.get("name") in keep.get(c.get("canonical"), ()))


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
        spell = episode_spellings(workdir, roster=rows)
        keep = set().union(*keep_original_names(workdir).values()) if workdir is not None else set()
        out = []
        for r in rows:
            if r["寫法"] in spell:   # 1016-3 審查 A：這一集自己決定的寫法
                target, how, _who = spell[r["寫法"]]
                if how in ("不用處理", "不是名字"):
                    continue   # 這一集不換
                out.append({**r, "canonical": target, "代號": codes.get(r["寫法"]) or codes.get(target) or ""})
            else:
                code = codes.get(r["canonical"], r["代號"])
                if not code and r["寫法"] in keep:   # 10-18 #250：③ 選了不用處理／不是名字，學員重念稿照原聲（不換、不拿掉）
                    continue
                out.append({**r, "代號": code})
        rows = out
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


ORIG_KEY = "名冊本名原本"   # 名字候選：這一集蓋過本名時記下原本照名冊的本名


def sync(workdir: str | Path) -> int:
    """名字候選檔的 `代號` 跟這一集的代號表對齊（敏感詞不動）。回傳改了幾筆；沒變就不寫檔。
    09-29：同時把已經寫進校對稿、改稿裡的舊代號換成新的（`propagate`）。"""
    from bookclub import studentnames

    from bookclub import names
    from bookclub.config import data_dir

    workdir = Path(workdir)
    codes = episode_codes(workdir)
    propagate(workdir, codes)
    roster = names.load_roster(data_dir() / "名冊.csv")
    spell = episode_spellings(workdir, roster=roster)
    canon = roster_canon(roster)   # 第二輪審查 4：還原時也本名優先，跟 episode_codes、episode_spellings 一致
    changed = 0
    for path in (wd.names_path(workdir), studentnames.cands_path(workdir)):
        data = wd.read_json(path, default=None)
        if not data or not data.get("candidates"):
            continue
        n = 0
        for c in data["candidates"]:
            if c.get("敏感詞"):
                continue
            # 1016-3 審查 A、B：用這一集自己決定的寫法比對到的，本名照這一集的決定（補找掃的途中改了決定也一樣）；
            # 決定改回跟名冊走的，本名照名冊
            w = c.get("name")
            if w in spell:
                if c.get("canonical") != spell[w][0]:
                    c.setdefault(ORIG_KEY, c.get("canonical"))
                    c["canonical"] = spell[w][0]
                    n += 1
            elif ORIG_KEY in c:
                c["canonical"] = canon.get(w, w)   # 第三輪審查 2：名冊已經沒有這個寫法就用寫法本身，不退回原本那個人
                c.pop(ORIG_KEY, None)
                n += 1
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


@_locked
def auto_assign(workdir: str | Path) -> dict:
    """「幫還沒代號的自動配」：② 選了本名的學員、③ 名冊上的人（沒選不用處理／不是名字）還沒代號的，
    配這一集還沒用過的代號。② 的寫進右欄，③ 的寫成「換成代號」決定。
    10-07：依性別從女生／男生名單配（名冊性別＞第 1 步聲音推測；沒把握的兩邊都可以）、跟名字撞的不配、
    配上的都算決定好，畫面標「自動配」（人改了就拿掉）。"""
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    codes = episode_codes(workdir)
    tdata = wd.read_json(turns_mod.turns_path(workdir), default={}) or {}
    two = sorted({p["本名"] for p in tdata.get("學員", {}).values() if p.get("本名") and not codes.get(p["本名"])})
    done2, short = [], False
    for real in two:
        code = pick_code(workdir, person_gender(workdir, real)[0], codes)
        if not code:
            short = True
            continue
        turns_mod.set_name_code(workdir, real, code, add_roster=False)   # 10-07 宇軒：自動配只記這一集，不動名冊
        _mark(workdir, real, code)
        done2.append(real)
        codes = episode_codes(workdir)
    done3 = _auto_three(workdir, codes)
    if done3:
        sync(workdir)
    from bookclub import personnames

    left3 = [u for u in personnames.mentioned(workdir) if u.get("名冊本名") and not u["② 已決定"] and not u["都在刪除段落"]
             and not u["已決定"] and u["做法"] == "換成代號" and not u.get("代號")]
    return {"ok": True, "②": len(done2), "③": len(done3), "代號不夠": short or bool(left3)}


# ---------- 10-07：選了學員是誰就自動配、依性別配、撞名檢查、「代號對照」抽屜 ----------
# 10-07 宇軒：自動配的代號寫入時就算決定好（老師會自己改要改的），畫面只標「自動配」讓人知道是程式配的，人一改就拿掉標記：
# ② 學員（右欄 `本名代號`）的標記記在 `校對/代號自動配.json` 的 `自動配`；③ 名冊上的人記在人名決定的 `自動配: true`。
# 同一個檔的 `開始前③已配`：進第 3 步時 ③ 名冊上的人只自動配一次，之後人改了不會被蓋回去。

AUTO_FILE = "代號自動配.json"


def auto_path(workdir: str | Path) -> Path:
    return Path(workdir) / "校對" / AUTO_FILE


def _load_auto(workdir: Path) -> dict:
    d = wd.read_json(auto_path(workdir), default=None) or {}
    d.setdefault("自動配", {})
    return d


def _save_auto(workdir: Path, d: dict) -> None:
    if auto_path(workdir).parent.is_dir():
        wd.write_json(auto_path(workdir), d)


def _people_groups(workdir: Path) -> list[set[str]]:
    from bookclub import personnames

    from bookclub import names
    from bookclub.config import data_dir

    spell = episode_spellings(workdir)   # 1016-3：這一集自己決定的寫法不跟名冊那個人同一組
    canon = roster_canon(names.load_roster(data_dir() / "名冊.csv"))
    out = []
    for p in (wd.read_json(personnames.people_path(workdir), default={}) or {}).get("人名", []):
        g = {p["名字"], *p["其他寫法"], *([p["名冊本名"]] if p.get("名冊本名") and p["名字"] not in spell else [])}
        me = canon.get(p["名字"]) or p.get("名冊本名") or p["名字"]
        out.append({w for w in g if canon.get(w, me) == me})   # 10-18 #246 修法 1：名冊上是別人的寫法不算同一組
    return out


def name_spellings(workdir: str | Path) -> dict[str, str]:
    """不能拿來當代號的名字 {寫法: 是誰}：名冊上所有人的本名與其他寫法、第 1 步人名清單抓到的名字與其他寫法、
    ③ 人名決定的名字、名字候選比對到的字（`matched_text`，敏感詞除外）。
    代號跟其中一個寫法一樣，成品裡分不出是代號還是真的有人叫這個名字。"""
    from bookclub import names, personnames
    from bookclub.config import data_dir

    workdir = Path(workdir)
    out: dict[str, str] = {}
    for r in names.load_roster(data_dir() / "名冊.csv"):
        out.setdefault(r["寫法"], r["canonical"])
    for g in _people_groups(workdir):
        owner = next((x for x in sorted(g) if x in out), None) or sorted(g)[0]
        for w in g:
            out.setdefault(w, out.get(owner, owner))
    for name in (wd.read_json(personnames.decisions_path(workdir), default={}) or {}):
        out.setdefault(name, out.get(name, name))
    for c in (wd.read_json(wd.names_path(workdir), default={}) or {}).get("candidates", []):
        t = (c.get("matched_text") or "").strip()
        if t and not c.get("敏感詞"):
            out.setdefault(t, c.get("canonical") or t)
    return {k: v for k, v in out.items() if k}


def clashes(workdir: str | Path) -> dict[str, list[str]]:
    """代號名單（＋這一集用到的）裡跟名字撞的：{代號: [是誰…]}（網頁手動選到時警告）。"""
    sp = name_spellings(workdir)
    pool = set(CODE_POOL) | {c for c in episode_codes(workdir).values() if c}
    out: dict[str, set[str]] = {}
    for c in pool:
        if c in sp:
            out.setdefault(c, set()).add(sp[c])
    return {c: sorted(v) for c, v in out.items()}


def person_gender(workdir: str | Path, real: str | None) -> tuple[str | None, str]:
    """配代號用的性別：名冊有性別欄照名冊；沒有的話看第 1 步估的聲音（選了這個本名的學員 N 的基頻）。
    沒把握（估不出來、離 165 Hz 太近、兩位學員 N 推測不一樣）回 (None, 原因)：兩邊名單都可以配。"""
    from bookclub import students
    from bookclub import turns as turns_mod

    if not real:
        return None, "沒有本名"
    g = students.roster_gender(real)
    if g:
        return g, "名冊性別"
    tdata = wd.read_json(turns_mod.turns_path(Path(workdir)), default={}) or {}
    pitch = students.load_voice_table(Path(workdir)).get("音高") or {}

    def fresh(n: str, p: dict) -> dict | None:
        """10-07 審查：音高是照學員 N 編號存的；合併、拆開、改說話者之後這位的總秒數跟估的時候差超過 10%
        （可能已經是別人了），當作沒有推測。舊檔沒記秒數的照用。"""
        e = pitch.get(n)
        then, now = (e or {}).get("秒數"), p.get("秒數")
        if e and then and now is not None and abs(float(now) - float(then)) > 0.1 * float(then):
            return None
        return e

    guesses = [students.pitch_gender(fresh(n, p)) for n, p in (tdata.get("學員") or {}).items() if p.get("本名") == real]
    sure = {(g, c) for g, c in guesses if g}
    if len({g for g, _ in sure}) == 1:
        g, conf = sorted(sure)[0]
        return g, f"聲音推測（信心{conf}）"
    if len({g for g, _ in sure}) > 1:
        return None, "聲音推測不一致"
    return None, "聲音不確定" if guesses else "沒有聲音可以判斷"


def pick_code(workdir: str | Path, gender: str | None, codes: dict[str, str] | None = None,
              skip: set[str] | None = None) -> str | None:
    """從名單挑一個這一集還沒用、也不跟任何名字撞的代號。性別不知道的兩邊名單都可以；對應的名單用完回 None。"""
    codes = episode_codes(workdir) if codes is None else codes
    bad = set(codes.values()) | set(skip or ()) | set(name_spellings(workdir))
    pool = CODE_POOL_F if gender == "女" else CODE_POOL_M if gender == "男" else CODE_POOL
    return next((c for c in pool if c not in bad), None)


def _mark(workdir: Path, real: str, code: str) -> None:
    d = _load_auto(workdir)
    d["自動配"][real] = code
    _save_auto(workdir, d)


def clear_auto(workdir: str | Path, real: str) -> None:
    """人改了代號：拿掉「自動配」標記（② 右欄的標記、③ 這個人的人名決定）。"""
    from bookclub import personnames

    workdir = Path(workdir)
    d = _load_auto(workdir)
    if d["自動配"].pop(real, None) is not None:
        _save_auto(workdir, d)
    dp = personnames.decisions_path(workdir)
    dec = wd.read_json(dp, default=None)
    if dec:
        group = next((g for g in _people_groups(workdir) if real in g), {real}) | {real}
        hit = [k for k, v in dec.items() if isinstance(v, dict) and v.get("自動配") and (k in group or v.get("名冊本名") in group)]
        for k in hit:
            dec[k].pop("自動配", None)
        if hit:
            wd.write_json(dp, dec)


def auto_unchanged(workdir: str | Path) -> dict[str, str]:
    """自動配、人沒改過的代號 {本名: 代號}（代號後來被改掉的不算）。"""
    from bookclub import names, personnames
    from bookclub.config import data_dir

    workdir = Path(workdir)
    codes = episode_codes(workdir)
    out = {r: c for r, c in _load_auto(workdir)["自動配"].items() if c and codes.get(r) == c}
    canon = {r["寫法"]: r["canonical"] for r in names.load_roster(data_dir() / "名冊.csv")}
    for name, d in (wd.read_json(personnames.decisions_path(workdir), default={}) or {}).items():
        k = canon.get(name, name)
        if isinstance(d, dict) and d.get("自動配") and d.get("代號") and codes.get(k) == d["代號"]:
            out[k] = d["代號"]
    return out


@_locked
def auto_for_student(workdir: str | Path, real: str | None) -> str | None:
    """② 左欄選了學員 N 的本名之後：這個本名這一集還沒代號，就依性別挑一個配上（算決定好，標「自動配」）。"""
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    codes = episode_codes(workdir)
    if not real or codes.get(real):
        return None
    g, _why = person_gender(workdir, real)
    code = pick_code(workdir, g, codes)
    if not code or code in set(episode_codes(workdir).values()):   # 寫入前再確認一次還沒被用（同一把鎖裡）
        return None
    turns_mod.set_name_code(workdir, real, code, add_roster=False)   # 10-07 宇軒：自動配只記這一集，不動名冊
    _mark(workdir, real, code)
    return code


@_locked
def release_auto(workdir: str | Path, real: str | None) -> bool:
    """② 改選了別的本名：原本那個本名的代號是自動配、人沒改過、又沒有別的學員 N 選它 → 收回來（名單上可以再配）。"""
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    if not real:
        return False
    mark = _load_auto(workdir)["自動配"].get(real)
    tdata = wd.read_json(turns_mod.turns_path(workdir), default={}) or {}
    if not mark or (tdata.get("本名代號") or {}).get(real) != mark:
        return False
    if any(p.get("本名") == real for p in (tdata.get("學員") or {}).values()):
        return False
    turns_mod.set_name_code(workdir, real, None, add_roster=False)   # 10-07 宇軒：自動配只記這一集，不動名冊
    return True


def _auto_three(workdir: Path, codes: dict[str, str]) -> list[str]:
    """③ 名冊上的人（沒人決定過、② 沒選、不是都在刪除段落、還沒代號）依名冊性別配代號，寫成「換成代號」＋`自動配`。"""
    from datetime import datetime

    from bookclub import personnames, students

    done: list[str] = []
    dp = personnames.decisions_path(workdir)
    for u in personnames.mentioned(workdir):
        canon = u.get("名冊本名")
        if not canon or u["② 已決定"] or u["都在刪除段落"] or u["已決定"] or codes.get(canon) or u["做法"] != "換成代號":
            continue
        code = pick_code(workdir, students.roster_gender(canon), codes)
        if not code:
            break
        dec = wd.read_json(dp, default={}) or {}
        dec[u["名字"]] = {"做法": "換成代號", "代號": code, "同一人": None, "自動配": True,
                         "更新時間": datetime.now().isoformat(timespec="seconds")}
        wd.write_json(dp, dec)
        codes = episode_codes(workdir)
        done.append(u["名字"])
    return done


@_locked
def auto_initial(workdir: str | Path) -> dict:
    """進第 3 步（`POST /api/codes/initial`，或第 1 步分析結束）：③ 名冊上的人自動配一次代號。
    做過就不再做（`開始前③已配`），人後來改的不會被蓋回去。人名清單還沒有（第 1 步沒跑完）時先不做、不記旗標。"""
    from bookclub import personnames

    workdir = Path(workdir)
    a = _load_auto(workdir)
    if a.get("開始前③已配"):
        return {"ok": True, "已配過": True, "③": 0}
    if wd.read_json(personnames.people_path(workdir), default=None) is None \
            and not (wd.read_json(wd.names_path(workdir), default={}) or {}).get("candidates"):
        return {"ok": True, "還不能配": True, "③": 0}
    done = _auto_three(workdir, episode_codes(workdir))
    a = _load_auto(workdir)
    a["開始前③已配"] = True
    _save_auto(workdir, a)
    if done:
        sync(workdir)
    return {"ok": True, "③": len(done)}


def code_table(workdir: str | Path) -> dict:
    """「代號對照」抽屜（只讀不寫）：
    上半 `對照`：每個人一列（同一人的幾種寫法合成一列）→ 代號、哪幾位學員 N、來源、是不是自動配（人沒改過）、性別；
    下半 `名單`：女／男／其他，每個代號「給了誰」或空著、跟哪個名字撞名；`代號給了`：{代號: [本名…]}（選單標「已給某某」）。"""
    from bookclub import names, personnames
    from bookclub import turns as turns_mod
    from bookclub.config import data_dir

    workdir = Path(workdir)
    codes = episode_codes(workdir)
    tdata = wd.read_json(turns_mod.turns_path(workdir), default={}) or {}
    stu = tdata.get("學員") or {}
    roster = names.load_roster(data_dir() / "名冊.csv")
    dec = wd.read_json(personnames.decisions_path(workdir), default={}) or {}
    two = tdata.get("本名代號") or {}
    pending = auto_unchanged(workdir)

    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    keys = set(codes) | {p["本名"] for p in stu.values() if p.get("本名")}
    for k in keys:
        find(k)
    spell = episode_spellings(workdir, roster=roster, dec=dec)
    for r in roster:
        if r["寫法"] in spell:   # 1016-3：這一集自己決定的寫法跟這一集算的那個人同一組
            union(r["寫法"], spell[r["寫法"]][0])
        else:
            union(r["寫法"], r["canonical"])
    for g in _people_groups(workdir):
        g = sorted(g)
        for x in g[1:]:
            union(x, g[0])
    for name, d in dec.items():
        if isinstance(d, dict) and d.get("做法") == "是上面的學員" and d.get("同一人"):
            union(name, d["同一人"])
    groups: dict[str, list[str]] = {}
    for k in sorted(keys):
        groups.setdefault(find(k), []).append(k)
    spell: dict[str, set[str]] = {}   # 每組所有寫法（名冊其他寫法、人名清單其他寫法），顯示「也寫成」
    for x in list(parent):
        if find(x) in groups:
            spell.setdefault(find(x), set()).add(x)
    reals = {p["本名"] for p in stu.values() if p.get("本名")}
    roster_canon = {r["canonical"] for r in roster}

    rows = []
    for root_, members in groups.items():
        main = next((m for m in members if m in reals), None) or next((m for m in members if m in roster_canon), members[0])
        cs = sorted({codes[m] for m in members if codes.get(m)})
        who = sorted((n for n, p in stu.items() if p.get("本名") in members),
                     key=lambda n: int(n[2:]) if n[2:].isdigit() else 0)
        if any(two.get(m) for m in members):
            src = "② 學員是誰"
        elif any(isinstance(dec.get(m), dict) and dec[m].get("做法") == "換成代號" for m in members) \
                or any(isinstance(d, dict) and d.get("做法") == "換成代號" and find(n) == find(main) for n, d in dec.items()):
            src = "③ 換成代號"
        elif any(isinstance(d, dict) and d.get("做法") == "是上面的學員" and find(n) == find(main) for n, d in dec.items()):
            src = "③ 是上面的學員"
        elif cs:
            src = "名冊上的舊代號"
        else:
            src = ""
        g, why = person_gender(workdir, main)
        rows.append({"本名": main, "其他寫法": sorted(spell.get(root_, set(members)) - {main}), "代號": "、".join(cs), "學員": who,
                     "來源": src, "自動配未改過": any(pending.get(m) for m in members), "性別": g, "性別依據": why})
    rows.sort(key=lambda r: (not r["學員"], int(r["學員"][0][2:]) if r["學員"] and r["學員"][0][2:].isdigit() else 0, r["本名"]))
    given: dict[str, list[str]] = {}
    for r in rows:
        for c in [x for x in r["代號"].split("、") if x]:
            given.setdefault(c, []).append(r["本名"])
    clash = clashes(workdir)

    def entry(c: str) -> dict:
        return {"代號": c, "給了": given.get(c, []), "撞名": clash.get(c, [])}

    return {"對照": rows, "代號給了": given, "撞名": clash,
            "還沒選本名": [n for n, p in stu.items() if not p.get("本名") and not p.get("本名未知") and not p.get("都會刪掉")],
            "名單": {"女": [entry(c) for c in CODE_POOL_F], "男": [entry(c) for c in CODE_POOL_M],
                     "其他": [entry(c) for c in sorted(given) if c not in CODE_POOL]}}


def student_labels(workdir: str | Path) -> dict[str, str]:
    """10-07 宇軒：畫面上的「學員 N」改顯示「本名（代號）」：{學員N: 本名（代號）}；沒選本名的不列（照舊顯示學員 N）。
    兩位學員 N 選了同一個本名，後面加「・學員N」分得出來。只給畫面用，不寫進任何紀錄檔。"""
    from bookclub import turns as turns_mod

    workdir = Path(workdir)
    stu = (wd.read_json(turns_mod.turns_path(workdir), default={}) or {}).get("學員") or {}
    codes = episode_codes(workdir)
    count: dict[str, int] = {}
    for p in stu.values():
        if p.get("本名"):
            count[p["本名"]] = count.get(p["本名"], 0) + 1
    out = {}
    for n, p in stu.items():
        real = p.get("本名")
        if not real:
            continue
        code = codes.get(real) or p.get("代號")
        out[n] = f"{real}（{code}）" if code else real
        if count[real] > 1:
            out[n] += f"・{n}"
    return out
