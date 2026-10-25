#!/bin/bash
# backup-before-update.sh：README〈更新到新版本〉第 3 步「更新前備份（只備份紀錄，不備份大檔）」。
# 用法（Windows PowerShell）：
#   wsl -d Ubuntu -- bash -l "/home/<Ubuntu 使用者>/bookclub-edit/scripts/backup-before-update.sh"
#
# 做的事：
#   1. 先印出現在已經有的「_更新前備份_」資料夾（第 9 步清舊備份時要用，請記下來）。
#   2. ~/讀書會剪輯資料 → ~/讀書會剪輯資料_更新前備份_<日期時間>_只有紀錄（跳過 影片/）。
#   3. 專案清單.json 裡每個工作區旁邊建 <工作區>_更新前備份_<日期時間>_只有紀錄，
#      只複製 .json／.jsonl（含子資料夾，不含 輸出/），以及 修改需求/、校對/、覆核/、transcript/
#      四個資料夾（不含 wav、flac）。
# 只複製、不讀內容、不刪任何東西。同名備份已存在就停（不覆蓋）。

DATA="$HOME/讀書會剪輯資料"
[ -d "$DATA" ] || { echo "找不到 $DATA，停止。"; exit 1; }

echo "== 更新前已經有的備份（第 9 步會用到）=="
find "$HOME" -maxdepth 4 -name '*_更新前備份_*' -not -path '*/.cache/*' -prune -print 2>/dev/null
echo "== 開始備份 =="

export BC_STAMP="$(date +%Y%m%d-%H%M)"
python3 -I - <<'PY'
import json, os, shutil, sys, time

home = os.path.expanduser("~")
data = os.path.join(home, "讀書會剪輯資料")
suffix = f"_更新前備份_{os.environ['BC_STAMP']}_只有紀錄"

dst = data + suffix
if os.path.exists(dst):
    sys.exit(f"已存在，停止：{dst}")
SKIP = shutil.ignore_patterns("影片", "備份", "舊測試工作區", "*_更新前備份_*", "*.wav", "*.flac")
est = 0
for r, ds, fs in os.walk(data):
    ds[:] = [d for d in ds if d not in ("影片", "備份", "舊測試工作區") and "_更新前備份_" not in d]
    est += sum(os.path.getsize(os.path.join(r, f)) for f in fs if not f.endswith((".wav", ".flac")))
if est > 2 * 1024**3:
    sys.exit(f"資料夾紀錄約 {est / 1024**3:.1f}GB，比預期大很多，停止，請將錯誤訊息通知開發者。")
shutil.copytree(data, dst, ignore=SKIP)
print("資料備份完成：", dst)

lst = os.path.join(data, "專案清單.json")
if not os.path.exists(lst):
    print("找不到 專案清單.json：只備份了資料夾，沒有工作區備份。")
    sys.exit(0)
projects = json.load(open(lst, encoding="utf-8")).get("專案", [])

KEEP_DIRS = {"修改需求", "校對", "覆核", "transcript"}
SKIP_TOP = {"輸出"}
SKIP_EXT = (".wav", ".flac")
for ws in projects:
    if not os.path.isdir(ws):
        print("找不到工作區，略過：", ws)
        continue
    out = ws.rstrip("/") + suffix
    if os.path.exists(out):
        sys.exit(f"已存在，停止：{out}")
    n = 0
    for root, dirs, files in os.walk(ws):
        rel = os.path.relpath(root, ws)
        top = rel.split(os.sep)[0] if rel != "." else ""
        if top in SKIP_TOP:
            continue
        for f in files:
            if f.endswith(SKIP_EXT):
                continue
            keep = f.endswith((".json", ".jsonl")) or top in KEEP_DIRS
            if keep:
                target = os.path.join(out, rel, f) if rel != "." else os.path.join(out, f)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(os.path.join(root, f), target)
                n += 1
    os.makedirs(out, exist_ok=True)
    print(f"工作區備份完成（{n} 個檔）：", out)
PY
rc=$?
echo "== 備份後的更新前備份 =="
du -sh "$HOME"/讀書會剪輯資料_更新前備份_* 2>/dev/null
exit $rc
