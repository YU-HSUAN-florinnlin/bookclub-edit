#!/bin/bash
# status.sh：只查看、不改任何東西。告訴你三件事：
#   1. 網頁（8766）有沒有開
#   2. 第 4 步有沒有在跑（更新前一定要確認，在跑就不能關網頁）
#   3. 網頁的程式編號（要關網頁時，請使用者在 Ubuntu 視窗打 kill <編號>）
# 用法（Windows PowerShell）：
#   wsl -d Ubuntu -- bash -l "/home/<Ubuntu 使用者>/bookclub-edit/scripts/status.sh"
# 結束碼：0＝網頁沒開，或開著而且第 4 步沒在跑（可以關網頁、可以更新）；
#         2＝第 4 步在跑，或查不出來（不要關網頁、不要更新，問使用者）。

PORT="${BOOKCLUB_PORT:-8766}"
BASE="http://localhost:${PORT}"

code="$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$BASE/" 2>/dev/null)"
if [ "$code" != "200" ]; then
  echo "網頁：沒開（${PORT} 沒有回應）。第 4 步不可能在跑，可以直接更新。"
  exit 0
fi
echo "網頁：開著（${PORT} 回 200）"

body="$(curl -s -m 5 "$BASE/api/run/status" 2>/dev/null)"
state="$(printf '%s' "$body" | python3 -I -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("查不出來"); sys.exit()
if "第4步執行中" in d:
    print("在跑" if d["第4步執行中"] else "沒在跑")
elif "沒有專案" in d:
    print("沒在跑")   # 還沒選影片，不可能在跑第 4 步
else:
    print("查不出來")
')"
echo "第 4 步：${state}"

echo "網頁的程式編號："
pids="$(pgrep -f 'bookclub.*serve')"
if [ -n "$pids" ]; then
  echo "$pids" | while read -r p; do echo "  $p"; done
  echo "要關網頁：請使用者自己在 Ubuntu 視窗打  kill <上面的編號>"
else
  echo "  找不到（可能是別的方式開的）"
fi

[ "$state" = "沒在跑" ] && exit 0
exit 2
