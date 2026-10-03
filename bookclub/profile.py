"""第 0 步「初始化設定」：跨專案共用的設定，與給夥伴的「設定包」匯出／匯入（09-26 加）。

共用設定都在 `~/讀書會剪輯資料/`（`config.data_dir()`）：名冊、敏感詞、名字排除清單、發音對照表四個 CSV，
`settings.toml`，匿名聲線（`聲線/`）。**名冊只要一份、不分期**：同一支影片裡同一人同一代號就好，
不同影片之間代號可以指不同人（09-26 宇軒）。

設定包是一個 zip（四個 CSV＋`settings.toml`＋第 4 步會用到的匿名聲線＋說明）。匯入用**同一條合併規則**：每個 CSV 以第一欄當鑰匙，
新的加進去、已經有的不動；同一鑰匙內容不同，保留本機的並列出衝突。`settings.toml` 本機沒有才放進去，
不一樣就列成衝突。10-03 第九批：範本的範例列匯出不帶、匯入略過（#34）；本機表頭沒有的欄位不匯入但列出來（#34）；
名冊同一個寫法出現在兩列，匯入結果與第 0 步提醒列號（#96）。匿名聲線（10-03 加，#7）同一條規則：本機沒有才放，內容一樣略過，內容不同保留本機並列成衝突。AI 之後發現新的念偏詞、排除詞照舊寫進 `~/讀書會剪輯資料/` 的 CSV，夥伴匯入新的設定包就更新。

隱私：名冊含學員本名。畫面與回報只顯示代號與筆數，衝突清單裡的名冊也用代號標示，不顯示本名。
"""

from __future__ import annotations

import csv
import io
import tomllib
import zipfile
from datetime import datetime
from pathlib import Path

from bookclub.config import data_dir

CSV_FILES = ("名冊.csv", "敏感詞.csv", "名字排除清單.csv", "發音對照表.csv")
SETTINGS_FILE = "settings.toml"
VOICE_DIR = "聲線"
AUDIO_EXTS = (".wav", ".flac", ".mp3", ".m4a")
EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "profile.example"
EXAMPLE_FILES = ("名冊.csv", "敏感詞.csv")   # 發音對照表的「愉快」是真的念偏詞，不算範例列
README = """讀書會剪輯工具｜設定包

內容：名冊、敏感詞、名字排除清單、發音對照表（CSV）與 settings.toml；
      第 4 步學員重念用的匿名聲線（聲線/ 資料夾，每個聲音一個 .wav 加同名逐字稿 .txt）。
匯入：在網頁「0 初始化設定」按「匯入設定包」，或在終端機跑
    .venv/bin/bookclub profile import <這個 zip>
合併規則：每個 CSV 以第一欄當鑰匙，新的加進去、已經有的不動；同一鑰匙內容不同，保留你電腦上的並列出衝突。
匿名聲線也是同一條規則：你電腦上沒有才放進去，已經有、內容一樣略過，內容不同保留你電腦上的並列出衝突。
範本裡示範用的範例列（範例學員、範例公司名稱這類）不會打包，也不會匯入。
你電腦上的表頭沒有的欄位不會匯入，匯入結果會列出是哪個檔的哪幾欄。
名冊含學員本名，這個檔案只傳給協作夥伴，不要公開。
"""


def _read_csv(data: bytes | str) -> tuple[list[str], list[dict]]:
    if isinstance(data, bytes):   # 10-03 #35：Excel 另存的 Big5 也讀得了
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp950")
    else:
        text = data.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    rows = [{k: (v or "").strip() for k, v in r.items() if k is not None} for r in reader]
    return list(reader.fieldnames or []), [r for r in rows if any(r.values())]


def example_keys(name: str) -> set[str]:
    """10-03 第九批（#34）：範本（`profile.example/`）裡示範用的假資料列——install.sh 把範本複製到
    `~/讀書會剪輯資料/`，名冊有「範例學員」「範例學員二」、敏感詞有「範例公司名稱」「範例地名」。
    回傳這些列第一欄的值；匯出不帶、匯入略過、第 0 步提醒刪掉。只認名冊與敏感詞。"""
    p = EXAMPLE_DIR / name
    if name not in EXAMPLE_FILES or not p.is_file():
        return set()
    header, rows = _read_csv(p.read_bytes())
    return {r.get(header[0], "") for r in rows if header and r.get(header[0])}


def _is_example(name: str, header: list[str], row: dict, keys: set[str] | None = None) -> bool:
    keys = example_keys(name) if keys is None else keys
    return bool(header) and row.get(header[0], "") in keys


def roster_duplicate_rows(path: Path) -> list[list[int]]:
    """10-03 第九批（#96）：名冊裡同一個寫法出現在兩列以上 → [[列號…], …]（標題列算第 1 列）。
    呼叫 `names.roster_duplicate_spellings`（第 3 步 ③ 同一個算法）；第 0 步與匯入只給列號，不給寫法（本名）。"""
    from bookclub.names import roster_duplicate_spellings

    seen, out = set(), []
    for d in roster_duplicate_spellings(Path(path)):
        k = tuple(d["列"])
        if k not in seen:
            seen.add(k)
            out.append(d["列"])
    return out


def duplicate_rows_text(groups: list[list[int]]) -> str:
    """「名冊裡同一個寫法出現在兩列：第 2、5 列；第 3、7 列（標題列算第 1 列）…」；沒有就空字串。"""
    if not groups:
        return ""
    where = "；".join(f"第 {'、'.join(str(n) for n in g)} 列" for g in groups)
    return (f"名冊裡同一個寫法出現在兩列以上：{where}（標題列算第 1 列）。同一處名字會同時比中這幾列的人；"
            "是同一人的話到名冊把重複的那一列刪掉或合併，不是同一人就留著，第 3 步卡片上再選是哪一位。")


def _mtime(p: Path) -> str | None:
    return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M") if p.exists() else None


def summary(root: Path | None = None) -> dict:
    """`GET /api/profile`：目前的共用設定（筆數、最後修改時間）。名冊只給代號，不給本名。"""
    root = Path(root or data_dir())
    files = {}
    for name in CSV_FILES:
        p = root / name
        info = {"有檔案": p.exists(), "筆數": 0, "最後修改": _mtime(p)}
        if p.exists():
            header, rows = _read_csv(p.read_bytes())
            info["筆數"] = len(rows)
            keys = example_keys(name)
            info["範例列"] = sum(1 for r in rows if _is_example(name, header, r, keys))   # #34
            if name == "名冊.csv":
                info["重複寫法列"] = roster_duplicate_rows(p)   # #96：只給列號
                info["代號"] = sorted({r.get("英文代號", "") for r in rows if r.get("英文代號")})
                info["沒有代號的筆數"] = sum(1 for r in rows if not r.get("英文代號"))
        files[name] = info
    # 10-01：數第 4 步真的會用到的聲線（候選資料夾裡男 N、女 N，跳過不用的），以前只數最外層的兩個暫定檔
    from bookclub.students import voice_pool

    pool = voice_pool(root / VOICE_DIR) if (root / VOICE_DIR).is_dir() else {}
    voices = [f for fs in pool.values() for f in fs]
    detail = "、".join(f"{g}聲 {len(fs)} 個" for g, fs in pool.items() if fs)
    return {"資料夾": str(root), "檔案": files,
            "settings.toml": {"有檔案": (root / SETTINGS_FILE).exists(), "最後修改": _mtime(root / SETTINGS_FILE)},
            "匿名聲線": {"數量": len(voices), "狀態": f"{len(voices)} 個（{detail}）" if voices else "還沒做"}}


def export_voices(root: Path) -> dict[str, list[Path]]:
    """10-03（#7）：設定包要帶的匿名聲線＝第 4 步 `students.voice_pool` 真的會用的那些
    （候選資料夾裡沒被跳過的男 N、女 N；某個性別沒有候選時才是暫定聲線）。回傳 {男: [wav], 女: [wav]}。"""
    from bookclub.students import voice_pool

    base = root / VOICE_DIR
    return voice_pool(base) if base.is_dir() else {"男": [], "女": []}


def voice_count_text(counts: dict[str, int]) -> str:
    """「匿名聲線：男 N 個、女 N 個」；一個都沒有時明講。"""
    if not any(counts.values()):
        return "匿名聲線：設定包裡沒有（第 4 步學員重念需要，請跟給你設定包的人要含聲線的新版，或自己放進聲線資料夾）"
    return f"匿名聲線：男 {counts.get('男', 0)} 個、女 {counts.get('女', 0)} 個"


def export_profile(out: str | Path | None = None, root: Path | None = None) -> dict:
    """打包設定包 zip，回傳 {檔案, 內含, 匿名聲線}。預設存在 `~/讀書會剪輯資料/設定包/設定包_<時間>.zip`。
    10-03（#7）：第 4 步會用到的匿名聲線（各帶同名 .txt）放進 zip 的 `聲線/`，路徑照本機；跳過不用的不打包。"""
    root = Path(root or data_dir())
    out = Path(out).expanduser() if out else root / "設定包" / f"設定包_{datetime.now():%Y%m%d-%H%M%S}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    inside = []
    pool = export_voices(root)
    counts = {g: len(fs) for g, fs in pool.items()}
    voice_names = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        skipped_examples = 0
        for name in (*CSV_FILES, SETTINGS_FILE):
            p = root / name
            if not p.exists():
                continue
            keys = example_keys(name)
            if keys:   # 10-03 第九批（#34）：範本的範例列不帶出去
                header, rows = _read_csv(p.read_bytes())
                keep = [r for r in rows if not _is_example(name, header, r, keys)]
                if len(keep) != len(rows):
                    skipped_examples += len(rows) - len(keep)
                    buf = io.StringIO()
                    w = csv.DictWriter(buf, fieldnames=header, extrasaction="ignore", lineterminator="\n")
                    w.writeheader()
                    w.writerows(keep)
                    z.writestr(name, buf.getvalue())
                    inside.append(name)
                    continue
            z.write(p, name)
            inside.append(name)
        for g in ("男", "女"):
            for wav in pool.get(g, []):
                for f in (wav, wav.with_suffix(".txt")):
                    z.write(f, f.relative_to(root).as_posix())
                voice_names.append(wav.stem)
        listing = f"\n{voice_count_text(counts)}" + (f"（{'、'.join(voice_names)}）" if voice_names else "") + "\n"
        z.writestr("說明.txt", README + listing)
    if voice_names:
        inside.append(f"{VOICE_DIR}（{'、'.join(voice_names)}）")
    print(f"[設定包] 已匯出：{out}（{'、'.join(inside)}）")
    print(f"[設定包] {voice_count_text(counts)}")
    if skipped_examples:
        print(f"[設定包] 範本的範例列（範例學員、範例公司名稱這類示範資料）{skipped_examples} 筆沒有放進去")
    return {"檔案": str(out), "內含": inside, "匿名聲線": counts, "略過範例列": skipped_examples}


def merge_rows(local_header: list[str], local: list[dict], incoming: list[dict], label_col: str | None = None) -> dict:
    """同一條合併規則（純函式）：第一欄當鑰匙；新的加進去、已經有的不動；內容不同列成衝突。
    回傳 {新增: [列], 衝突: [{鑰匙, 本機, 匯入}]}。label_col：衝突要顯示的欄（名冊用代號，不顯示本名）。"""
    if not local_header:
        return {"新增": incoming, "衝突": []}
    key = local_header[0]
    have = {r.get(key, ""): r for r in local}
    added, conflicts = [], []
    for r in incoming:
        k = r.get(key, "")
        if not k:
            continue
        if k not in have:
            added.append({h: r.get(h, "") for h in local_header})
            have[k] = r
            continue
        mine = have[k]
        if any((r.get(h, "") or "") != (mine.get(h, "") or "") for h in local_header if h in r):
            show = (lambda row: {label_col: row.get(label_col, "")}) if label_col else (lambda row: row)
            conflicts.append({"鑰匙": (mine.get(label_col) or "（沒有代號）") if label_col else k,
                              "本機": show(mine), "匯入": show(r)})
    return {"新增": added, "衝突": conflicts}


def _append_rows(path: Path, header: list[str], rows: list[dict], new_file: bool) -> None:
    if new_file:
        path.write_text("", encoding="utf-8")
    else:
        raw = path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            path.write_bytes(raw + b"\n")
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerows(rows)


def import_profile(zip_path: str | Path, root: Path | None = None) -> dict:
    """匯入設定包：照合併規則併進本機的 CSV；回傳每個檔案的新增筆數與衝突（名冊用代號標示）。"""
    root = Path(root or data_dir())
    root.mkdir(parents=True, exist_ok=True)
    report: dict[str, dict] = {}
    with zipfile.ZipFile(Path(zip_path).expanduser()) as z:
        names = set(z.namelist())
        if not names & {*CSV_FILES, SETTINGS_FILE}:
            raise ValueError("這個 zip 不是設定包（裡面沒有名冊、敏感詞、排除清單、發音對照表或 settings.toml）")
        for name in CSV_FILES:
            if name not in names:
                continue
            in_header, incoming = _read_csv(z.read(name))
            keys = example_keys(name)   # 10-03 第九批（#34）：舊版設定包可能帶著範本的範例列，不當真的名字收進來
            examples = [r for r in incoming if _is_example(name, in_header, r, keys)]
            incoming = [r for r in incoming if not _is_example(name, in_header, r, keys)]
            p = root / name
            local_header, local = _read_csv(p.read_bytes()) if p.exists() else ([], [])
            header = local_header or in_header
            r = merge_rows(local_header, local, incoming, label_col="英文代號" if name == "名冊.csv" else None)
            if r["新增"]:
                _append_rows(p, header, r["新增"], new_file=not p.exists())
            report[name] = {"新增": len(r["新增"]), "衝突": r["衝突"], "略過範例列": len(examples),
                            "沒有匯入的欄位": _dropped_columns(name, local_header, in_header, incoming)}
            if name == "名冊.csv":   # 10-03 第九批（#96）
                report[name]["重複寫法列"] = roster_duplicate_rows(p)
        if SETTINGS_FILE in names:
            p = root / SETTINGS_FILE
            data = z.read(SETTINGS_FILE)
            tomllib.loads(data.decode("utf-8"))          # 格式不對就丟錯，不寫進去
            if not p.exists():
                p.write_bytes(data)
                report[SETTINGS_FILE] = {"新增": 1, "衝突": []}
            elif p.read_bytes() != data:
                report[SETTINGS_FILE] = {"新增": 0, "衝突": [{"鑰匙": SETTINGS_FILE, "本機": "保留", "匯入": "內容不同"}]}
            else:
                report[SETTINGS_FILE] = {"新增": 0, "衝突": []}
        voices = _import_voices(z, root)
    added = sum(v["新增"] for v in report.values())
    conflicts = sum(len(v["衝突"]) for v in report.values()) + len(voices["衝突"])
    notes = import_notes(report)
    print(f"[設定包] 匯入完成：新增 {added} 筆、衝突 {conflicts} 筆（衝突保留本機的）")
    for line in notes:
        print(f"[設定包] {line}")
    print(f"[設定包] {voices['說明']}")
    return {"檔案": report, "新增": added, "衝突數": conflicts, "匿名聲線": voices, "提醒": notes}


def _dropped_columns(name: str, local_header: list[str], in_header: list[str], incoming: list[dict]) -> dict | None:
    """10-03 第九批（#34）：設定包裡有、這台電腦的表頭沒有的欄位（合併只照這台的表頭寫，這幾欄的內容不會進來）。
    回傳 {欄位: [...], 原因: 文字}；沒有就 None。本機還沒有這個檔（照設定包的表頭新建）不會丟欄位。"""
    if not local_header:
        return None
    cols = [h for h in in_header if h and h not in local_header]
    if not cols:
        return None
    filled = [h for h in cols if any(r.get(h) for r in incoming)]
    if name == "名冊.csv" and cols == ["英文代號"]:
        why = "這台電腦的名冊沒有「英文代號」欄（新版的代號每一集在第 3 步選），設定包裡的代號沒有匯入"
    else:
        why = (f"這台電腦的 {name} 表頭沒有這幾欄，匯入只照這台的表頭寫"
               + ("（這幾欄在設定包裡是空的）" if not filled else "，這幾欄的內容沒有進來；要用這幾欄，請跟給你設定包的人拿原本的檔案對照"))
    return {"欄位": cols, "原因": why}


def import_notes(report: dict) -> list[str]:
    """匯入結果要人知道的事（每點一句）：沒有匯入的欄位、略過的範例列、名冊重複寫法。"""
    notes = []
    for name, v in report.items():
        d = v.get("沒有匯入的欄位")
        if d:
            notes.append(f"{name}：{'、'.join(d['欄位'])} 欄沒有匯入——{d['原因']}")
        if v.get("略過範例列"):
            notes.append(f"{name}：範本的範例列 {v['略過範例列']} 筆（示範用的假資料）沒有匯入")
    dup = duplicate_rows_text((report.get("名冊.csv") or {}).get("重複寫法列") or [])
    if dup:
        notes.append(dup)
    return notes


VOICE_EXTS = (".wav", ".txt")


def _import_voices(z: zipfile.ZipFile, root: Path) -> dict:
    """10-03（#7）：設定包裡 `聲線/` 底下的檔放到本機同樣的位置。本機沒有才放；已經有、內容一樣略過；
    內容不同保留本機並列成衝突（跟 CSV 同一條規則）。只收 .wav／.txt，路徑不能跑出聲線資料夾。
    回傳 {男, 女（設定包裡的聲線數）, 新增, 略過（檔案數）, 衝突: [相對路徑], 說明}。"""
    import re

    base = (root / VOICE_DIR).resolve()
    counts = {"男": 0, "女": 0}
    added = skipped = 0
    conflicts: list[str] = []
    for name in sorted(z.namelist()):
        if not name.startswith(VOICE_DIR + "/") or name.endswith("/"):
            continue
        rel = Path(name)
        if rel.is_absolute() or ".." in rel.parts or rel.suffix.lower() not in VOICE_EXTS:
            continue
        dest = root / rel
        if base not in dest.resolve().parents:
            continue
        if rel.suffix.lower() == ".wav":
            m = re.match(r"(男|女)", rel.stem)
            if m:
                counts[m.group(1)] += 1
        data = z.read(name)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            added += 1
        elif dest.read_bytes() == data:
            skipped += 1
        else:
            conflicts.append(rel.as_posix())
    text = voice_count_text(counts)
    if conflicts:
        text += f"；{len(conflicts)} 個檔跟這台電腦的內容不同，保留這台的：{'、'.join(conflicts)}"
    return {**counts, "新增": added, "略過": skipped, "衝突": conflicts, "說明": text}
