#!/bin/bash
# update-to.sh [版本標籤]：README〈更新到新版本〉第 1、4、5、6 步（檢查、記檢查碼、切版本、比對套件）。
# 用法（Windows PowerShell）：
#   wsl -d Ubuntu -- bash -l "/home/<Ubuntu 使用者>/bookclub-edit/scripts/update-to.sh" v0.2.4-beta4
#
# 不做：關網頁（第 2 步）、備份（第 3 步，用 backup-before-update.sh）、install.sh（要輸入密碼，請使用者自己打）、
#       開網頁（第 7 步，用 serve.sh）、健檢（第 8 步）、清舊東西（第 9 步，用 list-old-backups.sh 先列）。
# 先關網頁、備份完成，才執行這個。
#
# 結束碼：0＝已切到新版，套件沒變（不用 install.sh）；
#         10＝已切到新版，套件有變（要請使用者自己跑 bash install.sh）；
#         1＝出錯，沒有切版本。

# 沒給標籤＝用 GitHub main 上 STABLE 檔寫的「穩定版」。
# 第一次從沒有 scripts/ 的舊版更新時，可以不切版本，直接從新標籤取出來跑：
#   git -C ~/bookclub-edit show v0.2.4-beta4:scripts/update-to.sh > ~/update-to.sh
TAG="$1"
TOOL_DIR="${BOOKCLUB_TOOL_DIR:-$HOME/bookclub-edit}"
cd "$TOOL_DIR" || { echo "找不到 $TOOL_DIR"; exit 1; }
if [ -z "$TAG" ]; then
  git fetch origin main 2>&1 | tail -2
  TAG="$(git show origin/main:STABLE 2>/dev/null | head -1 | tr -d '[:space:]')"
  [ -n "$TAG" ] || { echo "GitHub main 上找不到 STABLE 檔，請給版本標籤，例如 update-to.sh v0.2.4-beta4"; exit 1; }
  echo "穩定版（STABLE）：$TAG"
fi

[ -d .git ] || { echo "$TOOL_DIR 沒有 .git：不是用 git 下載的，請停下來通知開發者。"; exit 1; }

if curl -s -m 5 -o /dev/null "http://localhost:${BOOKCLUB_PORT:-8766}/"; then
  echo "網頁還開著（8766 有回應）。請先照 README 第 2 步關掉網頁再更新。"
  exit 1
fi

echo "== 目前版本 =="
git describe --tags --always
git status --short | head -20

git fetch --tags 2>&1 | tail -5
git rev-parse -q --verify "refs/tags/$TAG" > /dev/null || { echo "找不到標籤 $TAG。現有的新標籤："; git tag --sort=-v:refname | head -5; exit 1; }

PKG="requirements.lock requirements.txt constraints.txt build-constraints.txt install.sh"
SUM_OLD="$(mktemp)"; PYP_OLD="$(mktemp)"
for f in $PKG; do [ -f "$f" ] && sha256sum "$f"; done > "$SUM_OLD"
grep -v '^version' pyproject.toml > "$PYP_OLD"

echo "== 切到 $TAG =="
git checkout "$TAG" 2>&1 | tail -3
if [ "$(git describe --tags --exact-match 2>/dev/null)" != "$TAG" ]; then
  echo "沒有切到 $TAG（可能有本機改過的檔案擋住）。不要用 reset／stash／checkout -- 丟掉，請看是哪些檔。"
  git status --short | head -20
  exit 1
fi

echo "== 套件檔比對 =="
changed=0
for f in $PKG; do
  if [ -f "$f" ]; then
    new="$(sha256sum "$f" | cut -d' ' -f1)"
    old="$(grep -F " $f" "$SUM_OLD" | cut -d' ' -f1)"
    if [ "$new" = "$old" ]; then echo "沒變：$f"; else echo "有變（或舊版沒有）：$f"; changed=1; fi
  fi
done
if grep -v '^version' pyproject.toml | diff -q - "$PYP_OLD" > /dev/null; then
  echo "沒變：pyproject.toml（版本號以外）"
else
  echo "有變：pyproject.toml（版本號以外）"; changed=1
fi

echo "== 新版本 =="
git describe --tags
grep -m1 version bookclub/__init__.py
rm -f "$SUM_OLD" "$PYP_OLD"

if [ "$changed" = 1 ]; then
  echo "結果：套件有變 → 請使用者自己在 Ubuntu 視窗打 cd ~/bookclub-edit 和 bash install.sh（要輸入 Ubuntu 密碼）。"
  exit 10
fi
echo "結果：套件沒變 → 不用跑 install.sh。"
exit 0
