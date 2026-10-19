# CLAUDE.md：給 AI 助理的開發規範

給在這個倉庫工作的 AI 助理（Claude Code、Codex 等）讀。一律用台灣繁體中文溝通與寫文件。
開發待辦與設計討論在維護者的私人工作區，不在本 repo；這裡只放跟程式本身有關的規則。

## 專案是什麼

- 讀書會 Zoom 錄影的隱私處理工具：學員聲音換成匿名聲線、說出口的名字換成代號，老師維持原音（`bookclub/__init__.py`）。
- 使用者是讀書會老師與協作夥伴，在本機網頁（`bookclub serve`，<http://localhost:8766>）照第 0～5 步操作。
- 平台：macOS（Apple Silicon、Intel）與 Windows 的 WSL2 Ubuntu；維護者在 macOS 開發。Windows 上有不少功能還沒實機驗過（見 `docs/之後要做.md`）。

## 目錄地圖

- `bookclub/`：主套件，指令入口 `cli.py`（`bookclub = "bookclub.cli:main"`）。工作區路徑一律問 `workdir.py`，不要自己組字串。
  - 第 1 步分析：`analyze.py`（串流程）、`transcribe.py`（Groq 轉文字）、`speakers.py`（認老師）、`overlap.py`（重疊、分辨說話者）、`names.py`（找名字）、`namespage.py`（名字覆核頁）、`turns.py`（段落分析）、`cutsuggest.py`（建議刪除段落）、`personnames.py`（整集人名）、`untranscribed.py`（有人聲沒字）、`turnvoice.py`（學員段落像老師）
  - 參考音與底噪：`refpick.py`（挑老師參考音）、`roomtone.py`（墊底噪）、`silentedge.py`（重念範圍前後沒聲音）
  - 第 3 步覆核：`review.py`（覆核工作台）、`proofread.py`（校對資料）、`align.py`（新增修改對齊時間）、`epcodes.py`／`codeswap.py`（這一集的代號）、`studentnames.py`（保留原聲學員講到名字）、`exchange.py`（覆核結果匯出入）
  - 第 4 步生成與組裝：`execute.py`（一次跑完）、`tts.py`（老師 AI 聲音）、`nameplan.py`（名字處理計畫）、`students.py`（學員匿名聲線）、`studentgen.py`（學員自己聲音念代號）、`fit.py`（放回時間格）、`pauses.py`（插入停頓）、`assemble.py`（組新聲音軌）、`render.py`（組影片）、`proclog.py`（處理紀錄）、`execcounts.py`（逐類統計）、`timemap.py`（原片↔成品時間）
  - 第 5 步成品檢查：`finalcheck.py`、`finalcards.py`（卡片）、`needs.py`（修改需求紀錄、`bookclub upgrade`）
  - 環境與共用：`config.py`、`profile.py`（第 0 步設定包）、`doctor.py`（健檢）、`detect.py`、`system_info.py`、`models.py`（模型下載）、`keepawake.py`（防睡眠）、`fileio.py`（選檔、Windows 檔案進出）、`csvfile.py`（Big5／UTF-8 CSV）、`safeview.py`（`bookclub inspect`）、`server.py`（網頁伺服器）、`_cosyvoice_compat.py`（CosyVoice 相容修補）
  - Swift 小工具（Mac 才用，伺服器啟動時編譯到 `~/.cache/bookclub/`）：`avconcat.swift`（AVFoundation 接片段）、`filepicker.swift`（常駐選檔視窗）
  - `web/`：網頁前端（`app.js`、`cards.js`、`review.js`、`finalcheck.js`、`fileio.js`、`index.html`、`style.css`）
- `tests/`：每支 `test_*.py` 都能獨立跑；`fake_workdir.py` 建全合成的假工作區；`_testtmp.py` 讓暫存檔集中、跑完自動清；`smoke/` 實際跑模型；`snapshots/`＋`web_harness.js` 是網頁快照測試。
- `docs/`：工作區格式、AI 查資料規則、套件鎖定、之後要做（見文末索引）。
- `skills/bookclub-edit/`：給使用者端 Claude Code 的 skill（`install.sh` 建捷徑到 `~/.claude/skills/`）。
- `profile.example/`：第 0 步設定範例（名冊、敏感詞、發音對照表、`settings.toml`），內容是虛構的。
- `prototype/`：流程樣品網頁；`prototype/audio/` 不進 git，用 `make_audio.sh` 重產。
- `spikes/`：早期實驗程式，**已列在 `.gitignore`**，只在維護者本機。
- `third_party/`：CosyVoice 原始碼（`install.sh` 用 git clone 放進來），**已列在 `.gitignore`**，不要改。
- `.venv/`：主要執行環境（uv 建的 Python 3.11）。`.venv-fw/`：只給 `spikes/nametest` 試 faster-whisper 用的另一個環境，主程式不用。兩者都不進 git。
- `install.sh`（安裝／重裝，macOS 與 Linux 共用）、`update-lock.sh`（開發者重產鎖定檔）、`啟動.command`（Mac 雙擊開網頁，用 `zsh -ic` 才讀得到 `~/.zshrc` 的 Groq 金鑰）。

## 開發環境

- Python 限 3.11（`pyproject.toml`：`>=3.11,<3.12`）。環境由 `bash install.sh` 建；只檢查不裝：`bash install.sh --check-only`。
- 相依套件不寫在 `pyproject.toml`，分三份（細節見 `docs/套件版本鎖定.md`）：
  - `requirements.txt`：直接用到的套件，開發者手改
  - `constraints.txt`：Intel Mac 裝得到的版本上限；兩種晶片用同一組，**不要為 Apple Silicon 放寬**
  - `requirements.lock`：**不要手改**。改了上面兩份之後跑 `bash update-lock.sh`（整批升級才加 `--upgrade`），一起 commit
  - `build-constraints.txt`：openai-whisper 建置用的 setuptools 上限，跟鎖定檔無關
- `numpy==1.26.4`、`torch==2.2.2` 是刻意固定的；新套件版本若要求 NumPy 2 就裝不起來（pyarrow 26 的前例）。
- 鎖定檔有變的版本，CHANGELOG 要寫「這版要重跑 install.sh」。`bookclub doctor` 會提醒已裝版本跟鎖定檔不一致。

## 跑測試

- 單支：`.venv/bin/python tests/test_xxx.py`（各檔開頭的「獨立可跑」那行）；少數檔寫 `PYTHONPATH=. .venv/bin/python tests/test_xxx.py`（例如 `test_csvfile.py`、`test_codes_auto.py`）。沒有用 pytest。
- 全部跑一輪：`for f in tests/test_*.py; do PYTHONPATH=. .venv/bin/python "$f" || echo "失敗：$f"; done`（2026-10-19 實測 62 支全過，約 1 分鐘）
- 單元／網頁測試：用合成資料，不跑模型、不碰真的工作區、不寫 `~/讀書會剪輯資料`。新的測試檔在 `from __future__` 下第一個 import `_testtmp`。
- 快照測試 `tests/test_cards_snapshot.py`：用 node 的假瀏覽器比對第 3 步卡片的 HTML、按鈕事件、送出的 API。只有**維護者同意刻意改第 3 步畫面**時才重產（`--update`），並在該檔開頭記下改了什麼。`test_final_cards_web.py`、`test_web.py` 也用到 node。
- smoke 測試（`tests/smoke/`）會**真的載入三個模型**跑一小段假錄音，要幾分鐘到十幾分鐘：`.venv/bin/bookclub doctor --smoke`（只在 Mac 上跑，WSL2 跳過）。AI 不要自己跑，除非被明確要求。
- 互動行為（點按鈕、存檔）測試抓不到：用 `.venv/bin/python tests/fake_workdir.py <資料夾>` 建假工作區、`bookclub serve <資料夾>/工作區` 讓人實際點過。

## 資料邊界（最重要）

- 工作區格式見 `docs/工作區格式.md`；每支影片一個工作區，建在影片旁邊（`<影片檔名>_剪輯工作區/`）。
- 設定與資料在 `~/讀書會剪輯資料/`、模型快取在 `~/.cache/bookclub/` 等處，都不在倉庫裡，也不能進 git。
- **任何學員姓名、名冊、Email、電話、影片、音檔、逐字稿都不能進 repo**，commit 訊息、CHANGELOG、測試資料也一樣。測試一律用虛構名字與合成聲音。
- `.gitignore` 已擋 `*.wav`、`*.aiff`、`tests/smoke/out/`、`prototype/audio/`；**不要用 `git add -f` 繞過**。commit 前看一次 `git status`，有不該出現的檔就停下來問。
- 查工作區資料**只能用 `.venv/bin/bookclub inspect <工作區> <主題>`**（規則見 `docs/給AI助手-查工作區資料.md`），不要 `cat`、`Read`、`jq` 或自己寫 Python 讀工作區 JSON。inspect 沒有的欄位，在 `safeview.py` 白名單加數字、編號、固定說法，或先問使用者。
- 原片↔成品時間只用 `inspect … 換算` 或 `render.to_output_time`，不要自己扣剪掉的片段。
- 用瀏覽器工具驗畫面時，只查要驗的元素，不要整頁抓文字（畫面上有逐字稿與名字）。

## commit／分支／tag 慣例（從 git 紀錄整理）

- 功能分支名：10-15 起用 `月日-序號-主題`，例如 `1017-4-鎖定版本`、`1016-1-UI文字`（更早的分支沒有序號，例如 `1004-切點對齊`，不用改名）；完成後合回 `main`，合併 commit 寫「併入 <分支名> <摘要>（#編號）」。
- commit 訊息：`<範圍或版本號>：<摘要>（#待辦編號）`，例如「0.2.2b5：版本號、CHANGELOG（#244）」「#244 審查修正：…」「讀書會剪輯 1017-4：全部套件鎖定版本（#241）」。待辦編號指的是維護者私人工作區的編號，沒有就不寫。
- 版本 tag：`v0.2.2-beta5` 這種格式，使用者靠它換版。
- `穩定-MMDD-推送前`（同一天第二次加 `b`，例如 `穩定-1017b-推送前`）：推上 GitHub 前在 `main` 打的還原點；另有 `穩定-MMDD-合併前`。
- 推上 GitHub 前要走維護者的發版檢查清單（不在本 repo）。**AI 不自己 push、不自己打 tag**，除非被明確要求。

## 版本號與 CHANGELOG

- 版本號 PEP 440 格式（`0.2.2b5`），升版時三處一起改：`pyproject.toml` 的 `version`、`bookclub/__init__.py` 的 `__version__`、`CHANGELOG.md` 最上面那一節的標題。
- CHANGELOG 的寫法：開發中就在最上面維護一節標題叫「未發布」，每併一個分支就把變化寫進去；發版時把這節標題改成日期＋版本（見下一條格式），不是發版當天才補寫一整節。
- CHANGELOG 標題：`## 0.2.2b5（2026-10-17，測試版 beta5）`；內文條列，先寫使用者看得到的變化，技術細節放後面，句尾附（待辦 #編號）；最後一條寫「從上一版更新要不要跑 install.sh」。
- `docs/之後要做.md` 的項目做完就搬進 CHANGELOG。

## 使用者怎麼更新

- 0.2.2b4 起：在原本的工具資料夾用 `git fetch --tags`、`git checkout <版本 tag>` 換版，**不再改名、重新下載**（README〈更新到新版本〉）。
- 套件檔（`requirements.lock`、`requirements.txt`、`constraints.txt`、`build-constraints.txt`、`install.sh`、`pyproject.toml` 版本行以外）有變才要重跑 `bash install.sh`。
- 改了工作區檔案格式時，CHANGELOG 要寫退回上一版讀不讀得懂；舊工作區由 `bookclub upgrade` 自動升級（`--dry-run` 只看不寫）。

## AI 工作守則

- 不跑模型（smoke、`gen`、`run execute`、`ref pick` 等）、不組裝影片（`render`），除非被明確要求；要跑先說清楚會花多久。
- 改程式後跑相關的 `tests/test_*.py`；改了 `web/` 跑網頁測試；改流程或指令時同步改 README、`docs/工作區格式.md`、`skills/bookclub-edit/SKILL.md`。
- 不改 `third_party/`；CosyVoice 相容問題改 `bookclub/_cosyvoice_compat.py`。
- 不碰使用者資料夾（`~/讀書會剪輯資料/`、任何 `_剪輯工作區/`）；只在 `fake_workdir.py` 建的假工作區或暫存資料夾測試。
- 給使用者看的文字（網頁、README、終端機訊息）用白話，不用專有名詞；終端機與 `報告.md` 只印統計數字，不印逐字稿內容。
- 不寫入 `/Users/<名字>/…` 這類絕對路徑，一律用 `~` 或相對於工具資料夾的路徑。
- 不 commit、不 push，除非被明確要求。

## 相關文件

- `README.md`：§3 準備什麼（含 WSL2 檔案進出、防睡眠）、§4 安裝與〈更新到新版本〉、§5 怎麼用（每一步注意事項）、§6 產出在哪、§8 隱私與授權（哪些資料送 Groq、Claude）
- `docs/工作區格式.md`：工作區每個檔的格式與誰寫
- `docs/給AI助手-查工作區資料.md`：`bookclub inspect` 主題與規則
- `docs/套件版本鎖定.md`：三份套件檔分工、重產鎖定檔
- `docs/之後要做.md`：還沒做的事、Windows 待實機驗證清單、移植到 Windows 要注意的地方
- `skills/bookclub-edit/SKILL.md`：使用者端 Claude Code 怎麼啟動網頁、`bookclub doctor` 用法
- `CHANGELOG.md`：每一版的變動
