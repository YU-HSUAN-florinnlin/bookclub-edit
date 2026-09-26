#!/bin/bash
# 雙擊這個檔案就會開啟讀書會剪輯工具的網頁。不指定工作區：在網頁「總覽」選影片、或切換到已有的專案
# （09-26 改；舊用法 .venv/bin/bookclub serve <工作區路徑> 照樣能用）。
cd "$(dirname "${BASH_SOURCE[0]}")"
.venv/bin/bookclub serve
