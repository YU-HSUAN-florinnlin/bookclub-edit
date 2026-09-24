#!/bin/bash
# 雙擊這個檔案就會開啟讀書會剪輯工具的網頁，自動挑最近修改過的工作區。
cd "$(dirname "${BASH_SOURCE[0]}")"
WORKDIR_ROOT="$HOME/讀書會剪輯資料/工作區"
LATEST=$(ls -td "$WORKDIR_ROOT"/*/ 2>/dev/null | head -1)
if [ -z "$LATEST" ]; then
  echo "找不到工作區（$WORKDIR_ROOT 底下還沒有資料夾）。"
  echo "請先用 bookclub run analyze 建立一個工作區，或用「.venv/bin/bookclub serve <工作區路徑>」指定。"
  read -p "按 Enter 關閉視窗..."
  exit 1
fi
echo "開啟工作區：$LATEST"
.venv/bin/bookclub serve "$LATEST"
