#!/bin/bash
# serve.sh：在背景啟動 bookclub serve，並確認 http://localhost:8766 有回應。
# 用法（Windows PowerShell）：
#   wsl -d Ubuntu -- bash -l "/home/<Ubuntu 使用者>/bookclub-edit/scripts/serve.sh"
# -l（登入模式）會載入 ~/.profile 的環境設定（Groq 金鑰要放在這裡；bash 登入不會讀 ~/.zshrc）。
# 已經在跑就不再開第二份。結束碼：0＝網頁有回應，1＝沒開起來。

PORT=8766
URL="http://localhost:${PORT}/"
TOOL_DIR="${BOOKCLUB_TOOL_DIR:-$HOME/bookclub-edit}"
LOG="$HOME/讀書會剪輯資料/serve.log"

code() { curl -s -m 5 -o /dev/null -w '%{http_code}' "$URL" 2>/dev/null; }

if [ "$(code)" = "200" ]; then
  echo "網頁本來就開著（200），不重複啟動。"
  pgrep -af 'bookclub serve' | grep -v pgrep
  exit 0
fi

if [ ! -x "$TOOL_DIR/.venv/bin/bookclub" ]; then
  echo "找不到 $TOOL_DIR/.venv/bin/bookclub，停止。"
  exit 1
fi

if ! env | grep -q '^GROQ_API_KEY='; then
  echo "提醒：這個登入環境沒有 Groq 金鑰，網頁開得起來，但第 1 步的雲端轉文字會顯示讀不到金鑰。"
fi
mkdir -p "$(dirname "$LOG")"
cd "$TOOL_DIR" || exit 1
setsid nohup .venv/bin/bookclub serve >> "$LOG" 2>&1 < /dev/null &

# 啟動要幾秒，最多等 40 秒
for i in $(seq 1 20); do
  sleep 2
  if [ "$(code)" = "200" ]; then
    echo "網頁已開起來（200）。在背景跑，關掉對話也不會停。"
    pgrep -af 'bookclub serve' | grep -v pgrep
    exit 0
  fi
done

echo "等了 40 秒網頁還是沒有回應（http=$(code)）。啟動紀錄最後幾行："
tail -n 15 "$LOG"
exit 1
