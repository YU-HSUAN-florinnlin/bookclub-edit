---
name: bookclub-edit
description: 啟動讀書會影片隱私處理工具的網頁。觸發時機：使用者說「剪讀書會」「處理第 N 週影片」「讀書會影片隱私處理」，或給一個影片路徑說要處理。這是對話入口（thin skill），實際流程在本機網頁上進行。0.1.1 版：轉文字與分析、挑老師參考音、名字覆核可用；聲音生成與影片輸出還沒做。
---

# bookclub-edit Skill

## 用途

工作夥伴對 Claude Code 說「幫我處理這支讀書會影片」時的對話入口。這個 skill 只負責
**啟動本機網頁伺服器**並帶入影片路徑，實際的分辨誰在說話、逐字稿校對、標記覆核、
聲音生成全部在瀏覽器裡完成——不在對話裡進行。

完整設計見這個工具開發時參考的規格文件（不在這個公開倉庫裡，是內部文件）。

## 目前進度：0.1.1

做得到：`bookclub run analyze`（抽音、轉文字、認出老師、找聲音重疊、挑老師參考音、找名字候選）、
`bookclub ref pick`／`ref use`、`bookclub serve`（本機網頁，第 1、2、4 步可操作）、`bookclub doctor`。

還沒做：學員逐字稿校對、AI 聲音生成與時間格處理、成品逐筆覆核、整片輸出。網頁上這些步驟
會顯示「這一步還沒做」。

## 觸發時機

- 夥伴說「幫我剪讀書會」「處理第 N 週影片」「讀書會影片隱私處理」
- 夥伴提供一個影片路徑，說要處理（換聲音、換名字）
- 夥伴說環境好像有問題、貼出 `bookclub doctor` 的錯誤訊息

## 執行流程

1. **環境還沒裝好時**：**先跑 `bash install.sh --check-only`**，看電腦裡已經有哪些工具與
   模型（別處已經裝過的 CosyVoice 與模型會被認出來、安裝時直接沿用，不重抓約 7GB）。
   再跑 `bash install.sh` 安裝缺的部分，或 `bash install.sh --no-skill` 如果不想重建這個
   捷徑；想忽略偵測全部重抓用 `--force-download`。裝完會自動跑一次
   `bookclub doctor`，把結果念給夥伴聽、必要項目沒過的話照畫面上的「修法」處理。
2. **確認環境沒問題**：跑 `.venv/bin/bookclub doctor`，必要項目全過才繼續。常見還沒
   處理的項目（這是預期的，不代表壞掉）：
   - Hugging Face 還沒登入 → 照畫面上的三步驟說明處理
   - GROQ_API_KEY 還沒設定 → 到 `~/.zshrc` 加一行，開新終端機視窗
3. **確認影片路徑**：如果訊息裡沒有明確路徑，詢問影片在哪裡。
4. **跑分析，再開網頁**：
   ```bash
   cd ~/Downloads/bookclub-edit
   .venv/bin/bookclub run analyze "<影片路徑>" ~/讀書會剪輯資料/工作區/<名稱> --roster ~/讀書會剪輯資料/名冊.csv
   ```
   ```bash
   .venv/bin/bookclub serve ~/讀書會剪輯資料/工作區/<名稱>
   ```
   網頁預設在 <http://localhost:8766>；第 1 步看進度、第 2 步確認老師參考音、第 4 步
   逐筆覆核名字候選。其餘步驟還沒做。
5. **出問題時**：請夥伴貼 `bookclub doctor` 的完整輸出，照畫面上每一項的「修法」處理；
   處理不了的，把輸出貼給 Claude Code 討論。

## 環境健檢（bookclub doctor）

```bash
cd ~/Downloads/bookclub-edit
.venv/bin/bookclub doctor            # 一般檢查（最後會印出現在用的原始碼與模型在哪裡）
.venv/bin/bookclub doctor --smoke    # 加碼：實際跑三個模型各處理一小段測試音檔（幾分鐘）
.venv/bin/bookclub doctor --claude   # 加碼：實際呼叫一次 claude -p 測試有沒有反應
```

## 隱私提醒

老師的名冊、聲音樣本、影片放在 `~/讀書會剪輯資料/`，不在這個倉庫裡，Claude Code
討論時不要把名冊內容、學員逐字稿細節貼進對話（跟主要工作環境的個資分級規則一致：
🟡 聯絡資料、🔴 學員諮詢內容不給 AI 原文）。
