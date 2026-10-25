#!/bin/bash
# list-old-backups.sh：README〈更新到新版本〉第 9 步的「只列出、不刪」檢查。
# 用法（Windows PowerShell）：
#   wsl -d Ubuntu -- bash -l "/home/<Ubuntu 使用者>/bookclub-edit/scripts/list-old-backups.sh" [這次剛做的備份時間，例如 20261025-1503]
# 列出家目錄與各工作區旁名字含 _更新前備份_ 的資料夾、大小，以及舊版工具資料夾（bookclub-edit-舊版*）。
# 給了「這次剛做的備份時間」，名字含這個時間的標示「保留」，其餘標示「候選」。只列，不刪任何東西。

KEEP="$1"
DATA="$HOME/讀書會剪輯資料"
echo "== 更新前備份 =="
{
  find "$HOME" -maxdepth 1 -name '*_更新前備份_*' 2>/dev/null
  if [ -f "$DATA/專案清單.json" ]; then
    python3 -I - "$DATA/專案清單.json" <<'PY'
import glob, json, sys
for ws in json.load(open(sys.argv[1], encoding="utf-8")).get("專案", []):
    for p in glob.glob(ws.rstrip("/") + "_更新前備份_*"):
        print(p)
PY
  fi
} | sort -u | while IFS= read -r d; do
  [ -n "$d" ] || continue
  tag="候選"
  if [ -n "$KEEP" ]; then case "$d" in *"$KEEP"*) tag="保留";; esac; fi
  echo "[$tag] $(du -sh "$d" 2>/dev/null | cut -f1)  $d"
done
echo "== 舊版工具資料夾 =="
n=0
for d in "$HOME"/bookclub-edit-舊版*; do
  [ -d "$d" ] || continue
  n=1
  echo "$(du -sh "$d" | cut -f1)  $d"
done
[ "$n" = 0 ] && echo "（沒有）"
exit 0
