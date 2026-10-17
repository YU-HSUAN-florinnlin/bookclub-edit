#!/usr/bin/env bash
# 重新產生 requirements.lock（開發者用；老師、夥伴不用跑）。
#
# 什麼時候要跑：改了 requirements.txt 或 constraints.txt 之後。跑完把 requirements.lock 一起 commit，
# 發版說明要寫「這版要重跑 install.sh」（鎖定檔有變）。
#
# 做法：uv pip compile --universal 產一份 Mac（Apple Silicon／Intel）與 Linux（WSL2）共用的鎖定檔，
# 平台不同的套件用 「; 條件」 標在同一行。原本的 requirements.lock 會當成「偏好版本」，
# 沒必要變的套件維持原版本；要整批升級才加 --upgrade（例如 bash update-lock.sh --upgrade）。
#
# 不寫進鎖定檔的：nvidia-*、triton（只有 CUDA 版 torch 才要；install.sh 在沒有 NVIDIA 顯示卡的 Linux
# 先從 PyTorch 的 CPU 專用來源裝 torch，鎖定檔若列了這些，會在 WSL2 多下載約 2.8GB 用不到的東西）。
# qwen-asr 與 bookclub 本身也不在裡面（install.sh 另外用 --no-deps 裝）。
set -euo pipefail
cd "$(dirname "$0")"

# 只看這個時間以前已發布的版本（10-17 #241：鎖成 10-16 晚全新安裝會裝到的版本）。
# 之後新增套件、需要更新的版本時才往後調，調了要在 CHANGELOG 寫。
EXCLUDE_NEWER="2026-10-16T14:00:00Z"

NO_EMIT=(triton nvidia-cublas-cu12 nvidia-cuda-cupti-cu12 nvidia-cuda-nvrtc-cu12 nvidia-cuda-runtime-cu12
  nvidia-cudnn-cu12 nvidia-cufft-cu12 nvidia-curand-cu12 nvidia-cusolver-cu12 nvidia-cusparse-cu12
  nvidia-nccl-cu12 nvidia-nvjitlink-cu12 nvidia-nvtx-cu12)
NO_EMIT_ARGS=()
for p in "${NO_EMIT[@]}"; do NO_EMIT_ARGS+=(--no-emit-package "$p"); done

TMP="$(mktemp -d)"
trap '/bin/rm -rf "$TMP"' EXIT
[ -f requirements.lock ] && cp requirements.lock "$TMP/out.lock"

uv pip compile requirements.txt -c constraints.txt \
  --universal --python-version 3.11 --exclude-newer "$EXCLUDE_NEWER" \
  --no-header --annotation-style line "${NO_EMIT_ARGS[@]}" \
  -o "$TMP/out.lock" "$@"

{
  cat <<EOF
# requirements.lock：所有套件的確切版本（10-17 #241 起；install.sh 照這份裝）。
#
# 不要手改。改了 requirements.txt 或 constraints.txt 之後，開發者跑「bash update-lock.sh」重產，
# 一起 commit；鎖定檔有變的版本，更新時要重跑 install.sh。
# 產生方式：uv pip compile requirements.txt -c constraints.txt --universal --python-version 3.11
#   --exclude-newer $EXCLUDE_NEWER（只用這個時間以前發布的版本）
# Mac（Apple Silicon／Intel）與 Linux（WSL2）共用這一份，「; 條件」寫的是只在哪些環境裝。
# 沒列 nvidia-*、triton：沒有 NVIDIA 顯示卡的 Linux 由 install.sh 改裝只用處理器的 torch，用不到。
# 沒列 qwen-asr、bookclub：install.sh 另外用 --no-deps 裝。
EOF
  cat "$TMP/out.lock"
} > requirements.lock

echo "已更新 requirements.lock（$(grep -cE '^[a-z0-9]' requirements.lock) 個套件）"
