"""第 0 步「初始化設定」：跨專案共用的設定，與給夥伴的「設定包」匯出／匯入（09-26 加）。

共用設定都在 `~/讀書會剪輯資料/`（`config.data_dir()`）：名冊、敏感詞、名字排除清單、發音對照表四個 CSV，
`settings.toml`，匿名聲線（`聲線/`）。**名冊只要一份、不分期**：同一支影片裡同一人同一代號就好，
不同影片之間代號可以指不同人（09-26 宇軒）。

設定包是一個 zip（四個 CSV＋`settings.toml`＋說明）。匯入用**同一條合併規則**：每個 CSV 以第一欄當鑰匙，
新的加進去、已經有的不動；同一鑰匙內容不同，保留本機的並列出衝突。`settings.toml` 本機沒有才放進去，
不一樣就列成衝突。AI 之後發現新的念偏詞、排除詞照舊寫進 `~/讀書會剪輯資料/` 的 CSV，夥伴匯入新的設定包就更新。

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
README = """讀書會剪輯工具｜設定包

內容：名冊、敏感詞、名字排除清單、發音對照表（CSV）與 settings.toml。
匯入：在網頁「0 初始化設定」按「匯入設定包」，或在終端機跑
    .venv/bin/bookclub profile import <這個 zip>
合併規則：每個 CSV 以第一欄當鑰匙，新的加進去、已經有的不動；同一鑰匙內容不同，保留你電腦上的並列出衝突。
名冊含學員本名，這個檔案只傳給協作夥伴，不要公開。
"""


def _read_csv(data: bytes | str) -> tuple[list[str], list[dict]]:
    text = data.decode("utf-8-sig") if isinstance(data, bytes) else data.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    rows = [{k: (v or "").strip() for k, v in r.items() if k is not None} for r in reader]
    return list(reader.fieldnames or []), [r for r in rows if any(r.values())]


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
            if name == "名冊.csv":
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


def export_profile(out: str | Path | None = None, root: Path | None = None) -> dict:
    """打包設定包 zip，回傳 {檔案, 內含}。預設存在 `~/讀書會剪輯資料/設定包/設定包_<時間>.zip`。"""
    root = Path(root or data_dir())
    out = Path(out).expanduser() if out else root / "設定包" / f"設定包_{datetime.now():%Y%m%d-%H%M%S}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    inside = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in (*CSV_FILES, SETTINGS_FILE):
            p = root / name
            if p.exists():
                z.write(p, name)
                inside.append(name)
        z.writestr("說明.txt", README)
    print(f"[設定包] 已匯出：{out}（{'、'.join(inside)}）")
    return {"檔案": str(out), "內含": inside}


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
            p = root / name
            local_header, local = _read_csv(p.read_bytes()) if p.exists() else ([], [])
            header = local_header or in_header
            r = merge_rows(local_header, local, incoming, label_col="英文代號" if name == "名冊.csv" else None)
            if r["新增"]:
                _append_rows(p, header, r["新增"], new_file=not p.exists())
            report[name] = {"新增": len(r["新增"]), "衝突": r["衝突"]}
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
    added = sum(v["新增"] for v in report.values())
    conflicts = sum(len(v["衝突"]) for v in report.values())
    print(f"[設定包] 匯入完成：新增 {added} 筆、衝突 {conflicts} 筆（衝突保留本機的）")
    return {"檔案": report, "新增": added, "衝突數": conflicts}
