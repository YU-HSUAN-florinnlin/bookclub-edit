# 變更紀錄

版本號用 `主.次.修`，主版本 0 代表還在開發、介面與檔案格式可能改變。

## 0.1.2（2026-09-24）

- **安裝腳本改成跨平台**：同一份 `install.sh` 在 macOS 與 Linux（含 Windows 的 WSL2 Ubuntu）都能跑。自動判斷作業系統，WSL2 會標示出來；macOS 走 Homebrew、Linux 走 apt-get（ffmpeg、sox、git、build-essential、curl、unzip），`uv` 在 Linux 用官方安裝指令
- Python 版本目標依平台與晶片組出（macOS arm64／x86_64、Linux x86_64／aarch64）
- `--intel` 只在 macOS 有效，在 Linux 上會明確報錯
- `bookclub doctor` 的「作業系統／晶片／記憶體」三項改成跨平台，Linux 讀 `/etc/os-release` 與 `/proc/meminfo`，WSL2 另外標示
- 新增 `tests/test_platform.py`（17 個測試，用假的系統檔內容驗 Linux 分支），全倉庫 138 個
- **尚未在真的 WSL2 上實跑驗證**

## 0.1.1（2026-09-24）

- **安裝前先檢查電腦裡已經有什麼**：新增 `bookclub/detect.py`，偵測系統工具、CosyVoice 原始碼與模型權重、pyannote 模型、Silero VAD、現成的 Python 環境。`install.sh` 與 `bookclub doctor` 共用同一份邏輯
- `install.sh` 新增 `--check-only`（只檢查不安裝）與 `--force-download`（忽略偵測、重新下載）
- **已經有的沿用、不重抓**：電腦裡別處已有的 CosyVoice 原始碼與模型權重，改成建立 symlink 沿用（原位置不動）。沿用模型前會驗證關鍵檔案齊全與大小合理，驗不過才自己下載
- `bookclub doctor` 最後會印出「現在用的原始碼與模型在哪裡」，分得出是倉庫自己的還是沿用外部
- 新增 `tests/test_detect.py`（10 個測試）

## 0.1.0（2026-09-24）

第一個給合作夥伴測試的版本。

**做得到**

- `bookclub run analyze`：一個指令跑完流程第 1 步——抽音、Groq 轉文字、聲紋分群認出老師、只在有學員的區域找聲音重疊、挑老師參考音、找學員姓名候選。第一堂（98 分鐘）在 MacBook Air M1 上實測 7.9 分鐘
- `bookclub ref pick`／`ref use`：自動挑老師參考音。不需要老師另外錄音；排除有別人聲音的區域；切頭尾、壓縮停頓到 29 秒以內；候選不足時把 2–3 段短的拼接起來
- `bookclub serve`：本機網頁，第 1 步進度、第 2 步參考音確認、第 4 步姓名標記覆核三頁可以實際操作
- `bookclub doctor`：環境健檢

**還沒做**

- 學員逐字稿校對（第 3 步）、AI 聲音生成與時間格處理（第 5 步）、成品逐筆覆核（第 6 步）、整片檢查與輸出（第 7 步）
- 第 2 步的聲音編號指認、第 4 步的聲音重疊與刪除段落介面
- 安裝腳本只在 macOS（Apple Silicon）測過，WSL2 尚未驗證
