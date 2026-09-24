#!/bin/bash
# bookclub-edit 安裝腳本
#
# 用法：
#   bash install.sh              一般安裝（Intel Mac／Apple Silicon 都用這個）
#   bash install.sh --intel      在 Apple Silicon 上模擬 Intel 環境（開發測試用，
#                                 真的 Intel Mac 不需要加這個參數）
#   bash install.sh --no-skill   不建立 Claude Code 的 skill 捷徑
#
# 可以重複執行：已經做過的步驟會自動跳過，不會重複下載、不會刪掉已有的設定。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

INTEL_MODE=0
NO_SKILL=0
for arg in "$@"; do
  case "$arg" in
    --intel) INTEL_MODE=1 ;;
    --no-skill) NO_SKILL=1 ;;
    *)
      echo "不認識的參數：$arg（可用參數：--intel、--no-skill）"
      exit 1
      ;;
  esac
done

TOTAL_STEPS=11
STEP=0
step() {
  STEP=$((STEP + 1))
  echo ""
  echo "[$STEP/$TOTAL_STEPS] $1"
}

# ── [1/11] 系統檢查 ──────────────────────────────────────────
step "檢查系統（macOS 版本、晶片、記憶體、剩餘空間）"

NATIVE_ARCH="$(uname -m)"
echo "macOS 版本：$(sw_vers -productVersion)"
if [ "$NATIVE_ARCH" = "arm64" ]; then
  echo "晶片：arm64（Apple Silicon）"
else
  echo "晶片：x86_64（Intel）"
fi

MEM_GB=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
echo "記憶體：約 ${MEM_GB}GB"
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

if [ "$INTEL_MODE" = "1" ] && [ "$NATIVE_ARCH" = "arm64" ]; then
  PY_TARGET="cpython-3.11-macos-x86_64-none"
  TARGET_ARCH="x86_64"
  VENV_DIR=".venv-x86"
  echo "已加 --intel 參數：改用模擬的 Intel 環境（Rosetta 下的 x86_64 Python），只供開發測試用，虛擬環境放在 $VENV_DIR。"
else
  # 明確指定晶片：只寫「3.11」的話，電腦上如果已經有另一種晶片的 Python 3.11，uv 會直接拿來用
  if [ "$NATIVE_ARCH" = "arm64" ]; then
    PY_TARGET="cpython-3.11-macos-aarch64-none"
  else
    PY_TARGET="cpython-3.11-macos-x86_64-none"
  fi
  TARGET_ARCH="$NATIVE_ARCH"
  VENV_DIR=".venv"
fi

# ── [2/11] Homebrew、ffmpeg、uv ──────────────────────────────
step "檢查 Homebrew，並安裝 ffmpeg／uv"

if ! command -v brew >/dev/null 2>&1; then
  echo "❌ 沒有找到 Homebrew（macOS 的套件安裝工具）。"
  echo "   這一步需要輸入你的登入密碼，本腳本不會自動安裝，請自己動手："
  echo "   1. 打開 https://brew.sh，照畫面指示安裝"
  echo "   2. 安裝完成後，開一個新的終端機視窗"
  echo "   3. 重新執行「bash install.sh」"
  exit 1
fi
echo "Homebrew 已安裝"

for pkg in ffmpeg uv; do
  if brew list --versions "$pkg" >/dev/null 2>&1; then
    echo "  $pkg 已安裝，跳過"
  else
    echo "  安裝 $pkg..."
    brew install "$pkg"
  fi
done

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

if [ -d "$COSYVOICE_DIR/.git" ]; then
  CURRENT_SHA="$(git -C "$COSYVOICE_DIR" rev-parse HEAD 2>/dev/null || echo '')"
  if [ "$CURRENT_SHA" = "$COSYVOICE_SHA" ]; then
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
