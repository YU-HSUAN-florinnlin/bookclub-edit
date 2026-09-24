#!/bin/bash
# bookclub-edit 安裝腳本
#
# 用法：
#   bash install.sh              一般安裝（Intel Mac／Apple Silicon 都用這個）
#   （macOS 與 Linux／WSL2 Ubuntu 共用同一份腳本，會自動判斷）
#   bash install.sh --intel      在 Apple Silicon 上模擬 Intel 環境（開發測試用，
#                                 真的 Intel Mac 不需要加這個參數）
#   bash install.sh --no-skill   不建立 Claude Code 的 skill 捷徑
#   bash install.sh --check-only 只檢查電腦裡已經有什麼，不安裝任何東西
#   bash install.sh --force-download
#                                忽略偵測結果，該下載的照樣重新下載
#
# 可以重複執行：已經做過的步驟會自動跳過，不會重複下載、不會刪掉已有的設定。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

INTEL_MODE=0
NO_SKILL=0
CHECK_ONLY=0
FORCE_DOWNLOAD=0
for arg in "$@"; do
  case "$arg" in
    --intel) INTEL_MODE=1 ;;
    --no-skill) NO_SKILL=1 ;;
    --check-only) CHECK_ONLY=1 ;;
    --force-download) FORCE_DOWNLOAD=1 ;;
    *)
      echo "不認識的參數：$arg（可用參數：--intel、--no-skill、--check-only、--force-download）"
      exit 1
      ;;
  esac
done

# ── [0] 先檢查電腦裡已經有什麼 ───────────────────────────────
# 別人可能已經照其他說明裝過 CosyVoice（原始碼＋模型約 7GB）。先偵測一次，
# 已經有的就沿用、不重抓；偵測邏輯在 bookclub/detect.py，doctor 也用同一份。
# ── 作業系統偵測（macOS 與 Linux／WSL2 共用同一份腳本）──────────
OS_KIND="$(uname -s)"          # Darwin ／ Linux
IS_WSL=0
if [ "$OS_KIND" = "Linux" ] && grep -qi microsoft /proc/version 2>/dev/null; then
  IS_WSL=1
fi

SYS_PYTHON="$(command -v python3 || true)"
if [ -z "$SYS_PYTHON" ]; then
  echo "❌ 找不到 python3，請先安裝 Python 3 再跑這支腳本。"
  exit 1
fi

echo ""
"$SYS_PYTHON" bookclub/detect.py || true

if [ "$CHECK_ONLY" = "1" ]; then
  echo ""
  echo "（--check-only：只檢查、沒有安裝任何東西。要正式安裝請去掉這個參數。）"
  exit 0
fi

# 外部已有的 CosyVoice 原始碼／模型：記下路徑，後面的步驟會改成建立 symlink 沿用
EXTERNAL_COSYVOICE_SRC=""
EXTERNAL_COSYVOICE_MODEL=""
if [ "$FORCE_DOWNLOAD" = "0" ]; then
  EXTERNAL_COSYVOICE_SRC="$("$SYS_PYTHON" bookclub/detect.py --external-cosyvoice-source 2>/dev/null || true)"
  EXTERNAL_COSYVOICE_MODEL="$("$SYS_PYTHON" bookclub/detect.py --external-cosyvoice-model 2>/dev/null || true)"
fi

TOTAL_STEPS=11
STEP=0
step() {
  STEP=$((STEP + 1))
  echo ""
  echo "[$STEP/$TOTAL_STEPS] $1"
}

# ── [1/11] 系統檢查 ──────────────────────────────────────────
step "檢查系統（作業系統、晶片、記憶體、剩餘空間）"

NATIVE_ARCH="$(uname -m)"
if [ "$OS_KIND" = "Darwin" ]; then
  echo "作業系統：macOS $(sw_vers -productVersion 2>/dev/null || echo '（版本無法偵測）')"
  MEM_BYTES="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
  MEM_GB=$(( MEM_BYTES / 1024 / 1024 / 1024 ))
else
  # Linux：發行版看 /etc/os-release、記憶體看 /proc/meminfo；取不到就說無法偵測，不中斷
  DISTRO="$( . /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-Linux}" || echo "Linux" )"
  if [ "$IS_WSL" = "1" ]; then
    echo "作業系統：$DISTRO（偵測到 WSL2，Windows 裡的 Linux）"
  else
    echo "作業系統：$DISTRO"
  fi
  MEM_KB="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
  MEM_GB=$(( MEM_KB / 1024 / 1024 ))
fi

if [ "$NATIVE_ARCH" = "arm64" ] || [ "$NATIVE_ARCH" = "aarch64" ]; then
  echo "晶片：$NATIVE_ARCH（ARM）"
else
  echo "晶片：$NATIVE_ARCH"
fi

if [ "$MEM_GB" -le 0 ]; then
  echo "記憶體：無法偵測，略過這項檢查"
  MEM_GB=8   # 偵測不到時不要因此中斷安裝，當成剛好達標
else
  echo "記憶體：約 ${MEM_GB}GB"
fi
if [ "$MEM_GB" -lt 8 ]; then
  echo "⚠️ 記憶體低於建議的 8GB，聲音生成等步驟可能會比較吃緊，仍繼續安裝。"
fi

AVAIL_KB=$(df -k "$HOME" | tail -1 | awk '{print $4}')
AVAIL_GB=$(( AVAIL_KB / 1024 / 1024 ))
echo "剩餘空間：約 ${AVAIL_GB}GB"
# 模型約 7GB、套件約 3GB；已經下載過模型的話（重跑安裝），需要的空間少很多
if [ -f "$HOME/.cache/bookclub/Fun-CosyVoice3-0.5B/llm.rl.pt" ]; then
  NEED_GB=5
else
  NEED_GB=15
fi
if [ "$AVAIL_GB" -lt "$NEED_GB" ]; then
  echo "❌ 剩餘空間只有約 ${AVAIL_GB}GB，這次安裝至少需要 ${NEED_GB}GB。"
  echo "   請先清出空間（例如清掉不用的影片、下載檔），再重跑這個腳本。"
  exit 1
elif [ "$AVAIL_GB" -lt 30 ]; then
  echo "⚠️ 剩餘空間約 ${AVAIL_GB}GB，低於建議的 30GB，之後可能會不夠用，仍繼續安裝。"
fi

if [ "$INTEL_MODE" = "1" ] && [ "$OS_KIND" != "Darwin" ]; then
  echo "❌ --intel 只在 macOS（Apple Silicon 上模擬 Intel）有意義，這台是 $OS_KIND。請去掉這個參數重跑。"
  exit 1
fi

if [ "$INTEL_MODE" = "1" ] && [ "$NATIVE_ARCH" = "arm64" ]; then
  PY_TARGET="cpython-3.11-macos-x86_64-none"
  TARGET_ARCH="x86_64"
  VENV_DIR=".venv-x86"
  echo "已加 --intel 參數：改用模擬的 Intel 環境（Rosetta 下的 x86_64 Python），只供開發測試用，虛擬環境放在 $VENV_DIR。"
else
  # 明確指定平台與晶片：只寫「3.11」的話，電腦上如果已經有另一種的 Python 3.11，uv 會直接拿來用
  if [ "$OS_KIND" = "Darwin" ]; then
    if [ "$NATIVE_ARCH" = "arm64" ]; then
      PY_TARGET="cpython-3.11-macos-aarch64-none"
    else
      PY_TARGET="cpython-3.11-macos-x86_64-none"
    fi
  elif [ "$NATIVE_ARCH" = "aarch64" ] || [ "$NATIVE_ARCH" = "arm64" ]; then
    PY_TARGET="cpython-3.11-linux-aarch64-gnu"
  else
    PY_TARGET="cpython-3.11-linux-x86_64-gnu"
  fi
  TARGET_ARCH="$NATIVE_ARCH"
  VENV_DIR=".venv"
fi

# ── [2/11] Homebrew、ffmpeg、uv ──────────────────────────────
step "安裝系統套件（ffmpeg、uv 等）"

if [ "$OS_KIND" != "Darwin" ]; then
  # ── Linux（含 WSL2 Ubuntu）：系統套件走 apt-get，uv 走官方安裝指令 ──
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "❌ 這台 Linux 沒有 apt-get，本腳本只自動處理 Debian／Ubuntu 系統。"
    echo "   請自行安裝下列套件後重跑：ffmpeg sox git build-essential curl unzip"
    exit 1
  fi
  APT_PKGS="ffmpeg sox git build-essential curl unzip"
  MISSING=""
  for pkg in $APT_PKGS; do
    dpkg -s "$pkg" >/dev/null 2>&1 || MISSING="$MISSING $pkg"
  done
  if [ -n "$MISSING" ]; then
    echo "要安裝的系統套件：$MISSING"
    echo "⚠️ 接下來會用系統管理員權限安裝，畫面上可能跳出密碼提示，請輸入你的登入密碼。"
    ADMIN_CMD=""
    if [ "$(id -u)" != "0" ]; then ADMIN_CMD="$(command -v sudo || true)"; fi
    $ADMIN_CMD apt-get update
    # shellcheck disable=SC2086
    $ADMIN_CMD apt-get install -y $MISSING
  else
    echo "系統套件都已安裝，跳過"
  fi

  if ! command -v uv >/dev/null 2>&1; then
    echo "安裝 uv（Python 套件管理工具；apt 沒有，用官方安裝指令）..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if ! command -v uv >/dev/null 2>&1; then
      echo "❌ 裝完還是找不到 uv。請開一個新的終端機（或先跑 source ~/.bashrc）再重跑這個腳本。"
      exit 1
    fi
  else
    echo "uv 已安裝，跳過"
  fi
elif ! command -v brew >/dev/null 2>&1; then
  echo "❌ 沒有找到 Homebrew（macOS 的套件安裝工具）。"
  echo "   這一步需要輸入你的登入密碼，本腳本不會自動安裝，請自己動手："
  echo "   1. 打開 https://brew.sh，照畫面指示安裝"
  echo "   2. 安裝完成後，開一個新的終端機視窗"
  echo "   3. 重新執行「bash install.sh」"
  exit 1
else
  echo "Homebrew 已安裝"
  for pkg in ffmpeg uv; do
    if brew list --versions "$pkg" >/dev/null 2>&1; then
      echo "  $pkg 已安裝，跳過"
    else
      echo "  安裝 $pkg..."
      brew install "$pkg"
    fi
  done
fi

# ── [3/11] Python 虛擬環境 ────────────────────────────────────
step "準備 Python 虛擬環境（$VENV_DIR）"

uv python install "$PY_TARGET"

if [ -d "$VENV_DIR" ]; then
  EXISTING_PY="$VENV_DIR/bin/python"
  if [ -x "$EXISTING_PY" ]; then
    EXISTING_ARCH="$("$EXISTING_PY" -c 'import platform; print(platform.machine())' 2>/dev/null || echo '?')"
    EXISTING_VER="$("$EXISTING_PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo '?')"
    if [ "$EXISTING_ARCH" = "$TARGET_ARCH" ] && [ "$EXISTING_VER" = "3.11" ]; then
      echo "沿用既有的 $VENV_DIR（Python $EXISTING_VER，$EXISTING_ARCH）"
    else
      echo "❌ $VENV_DIR 已存在，但版本或晶片跟這次要裝的不一樣。"
      echo "   現有：Python $EXISTING_VER／$EXISTING_ARCH　需要：Python 3.11／$TARGET_ARCH"
      echo "   本腳本不會自動刪除既有環境，避免不小心弄丟東西。"
      echo "   請自行確認 $VENV_DIR 內容後，手動改名或移除，再重跑這個腳本。"
      exit 1
    fi
  else
    echo "❌ $VENV_DIR 資料夾存在，但裡面沒有可執行的 python，請手動檢查這個資料夾後再處理。"
    exit 1
  fi
else
  uv venv "$VENV_DIR" --python "$PY_TARGET"
  NEW_ARCH="$("$VENV_DIR/bin/python" -c 'import platform; print(platform.machine())')"
  if [ "$NEW_ARCH" != "$TARGET_ARCH" ]; then
    echo "❌ 新建的 $VENV_DIR 是 $NEW_ARCH，但這台需要 $TARGET_ARCH。請把 $VENV_DIR 資料夾改名或移到垃圾桶後重跑。"
    exit 1
  fi
  echo "已建立 $VENV_DIR（Python 3.11，$NEW_ARCH）"
fi

PYBIN="$VENV_DIR/bin/python"

# ── [4/11] 安裝 Python 套件 ───────────────────────────────────
step "安裝 Python 套件（第一次安裝耗時較久，請耐心等候）"

uv pip install --python "$PYBIN" -r requirements.txt -c constraints.txt --build-constraint build-constraints.txt

# ── [5/11] 逐字對位工具（不裝用不到的相依）───────────────────
step "安裝逐字對位工具（qwen-asr）"

uv pip install --python "$PYBIN" --no-deps qwen-asr==0.0.6

# ── [6/11] bookclub 指令 ──────────────────────────────────────
step "安裝 bookclub 指令"

uv pip install --python "$PYBIN" --no-deps -e .

# ── [7/11] CosyVoice 原始碼 ───────────────────────────────────
step "取得聲音生成引擎原始碼（CosyVoice）"

COSYVOICE_DIR="third_party/CosyVoice"
COSYVOICE_SHA="074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc"

if [ ! -e "$COSYVOICE_DIR" ] && [ -n "$EXTERNAL_COSYVOICE_SRC" ] && [ -d "$EXTERNAL_COSYVOICE_SRC" ]; then
  # 電腦裡本來就有一份 CosyVoice：建 symlink 沿用，不重新 clone（原本的位置不動）
  EXT_SHA="$(git -C "$EXTERNAL_COSYVOICE_SRC" rev-parse --short HEAD 2>/dev/null || echo '不是 git 倉庫')"
  mkdir -p "$(dirname "$COSYVOICE_DIR")"
  ln -s "$EXTERNAL_COSYVOICE_SRC" "$COSYVOICE_DIR"
  echo "沿用你電腦裡原本的 CosyVoice：$EXTERNAL_COSYVOICE_SRC（commit $EXT_SHA）"
  echo "（我們測過的版本是 ${COSYVOICE_SHA:0:7}；版本不同時先跑跑看，有問題再用 --force-download 重裝）"
elif [ -d "$COSYVOICE_DIR/.git" ] || [ -L "$COSYVOICE_DIR" ]; then
  CURRENT_SHA="$(git -C "$COSYVOICE_DIR" rev-parse HEAD 2>/dev/null || echo '')"
  if [ -L "$COSYVOICE_DIR" ]; then
    echo "CosyVoice 原始碼已經是 symlink，指向 $(readlink "$COSYVOICE_DIR")，跳過"
  elif [ "$CURRENT_SHA" = "$COSYVOICE_SHA" ]; then
    echo "CosyVoice 原始碼已存在且版本正確，跳過"
  else
    echo "❌ $COSYVOICE_DIR 已存在，但版本不是預期的 $COSYVOICE_SHA（目前是 $CURRENT_SHA）。"
    echo "   本腳本不會自動覆蓋，請自行確認這個資料夾後再處理。"
    exit 1
  fi
else
  echo "CosyVoice 是聲音生成引擎，原始碼約數十 MB，用 git 只抓固定版本（不抓完整歷史）"
  git init "$COSYVOICE_DIR"
  git -C "$COSYVOICE_DIR" remote add origin https://github.com/FunAudioLLM/CosyVoice.git
  git -C "$COSYVOICE_DIR" fetch --depth 1 origin "$COSYVOICE_SHA"
  git -C "$COSYVOICE_DIR" checkout "$COSYVOICE_SHA"
  git -C "$COSYVOICE_DIR" submodule update --init --recursive --depth 1
  echo "CosyVoice 原始碼取得完成"
fi

# ── [8/11] 下載模型 ───────────────────────────────────────────
step "下載／確認 AI 模型（已下載的不會重抓）"

MODEL_DIR_DEFAULT="$HOME/.cache/bookclub/Fun-CosyVoice3-0.5B"
if [ ! -e "$MODEL_DIR_DEFAULT" ] && [ -n "$EXTERNAL_COSYVOICE_MODEL" ] && [ -d "$EXTERNAL_COSYVOICE_MODEL" ]; then
  # 沿用電腦裡原本的模型權重前，先驗證關鍵檔案齊全、大小合理
  if "$SYS_PYTHON" bookclub/detect.py --verify-cosyvoice-model "$EXTERNAL_COSYVOICE_MODEL"; then
    mkdir -p "$(dirname "$MODEL_DIR_DEFAULT")"
    ln -s "$EXTERNAL_COSYVOICE_MODEL" "$MODEL_DIR_DEFAULT"
    echo "沿用你電腦裡原本的 CosyVoice3 模型：$EXTERNAL_COSYVOICE_MODEL（約 5GB，省下重抓）"
  else
    echo "找到的模型資料夾檢查沒過（見上一行原因），改成自己下載"
  fi
fi

"$VENV_DIR/bin/bookclub" models download

# ── [9/11] 老師專案資料夾 ─────────────────────────────────────
step "建立老師專案資料夾（~/讀書會剪輯資料）"

DATA_DIR="$HOME/讀書會剪輯資料"
mkdir -p "$DATA_DIR"

if [ -d "profile.example" ]; then
  while IFS= read -r -d '' f; do
    rel="${f#profile.example/}"
    dest="$DATA_DIR/$rel"
    mkdir -p "$(dirname "$dest")"
    if [ -e "$dest" ]; then
      echo "  已存在，不覆蓋：$rel"
    else
      cp "$f" "$dest"
      echo "  建立：$rel"
    fi
  done < <(find profile.example -type f -print0)
fi

for sub in 老師聲音 聲線 影片 工作區; do
  mkdir -p "$DATA_DIR/$sub"
done
echo "資料夾就緒：$DATA_DIR"

# ── [10/11] Claude Code skill 捷徑 ────────────────────────────
step "建立 Claude Code skill 捷徑"

if [ "$NO_SKILL" = "1" ]; then
  echo "已加 --no-skill，跳過"
elif [ -d "$HOME/.claude/skills" ]; then
  SKILL_LINK="$HOME/.claude/skills/bookclub-edit"
  SKILL_SRC="$SCRIPT_DIR/skills/bookclub-edit"
  if [ -L "$SKILL_LINK" ] && [ "$(readlink "$SKILL_LINK")" = "$SKILL_SRC" ]; then
    echo "捷徑已存在，跳過"
  elif [ -e "$SKILL_LINK" ]; then
    echo "⚠️ $SKILL_LINK 已經存在，但不是指到這個倉庫，跳過建立（請自行確認後處理）"
  else
    ln -s "$SKILL_SRC" "$SKILL_LINK"
    echo "已建立：$SKILL_LINK -> $SKILL_SRC"
  fi
else
  echo "沒有找到 ~/.claude/skills，跳過（可能還沒安裝 Claude Code，或裝在別的地方）"
fi

# ── [11/11] 環境健檢 ───────────────────────────────────────────
step "執行環境健檢（bookclub doctor）"

set +e
"$VENV_DIR/bin/bookclub" doctor
DOCTOR_EXIT=$?
set -e

echo ""
if [ "$DOCTOR_EXIT" -eq 0 ]; then
  echo "安裝完成，必要項目都通過。"
else
  echo "安裝流程跑完了，但上面 doctor 的必要項目還有沒過的，照修法處理後重跑「$VENV_DIR/bin/bookclub doctor」確認。"
fi
echo "接下來可以雙擊「啟動.command」，或執行「$VENV_DIR/bin/bookclub serve」開啟網頁。"
