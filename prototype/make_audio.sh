#!/bin/bash
# 流程樣品網頁用的假聲音：全部用 macOS 系統語音生成，名字都是虛構的。
# 只用在樣品網頁，不代表 AI 生成聲音的品質，也不用在成品。
set -euo pipefail
cd "$(dirname "$0")/audio"
T=$(mktemp -d)
TW="(中文（台灣）)"
v() { case "$1" in Meijia) echo "Meijia";; *) echo "$1 $TW";; esac; }
mk() {  # mk 輸出名 聲音 語速 文字 [音量] [音高倍率]
  local out=$1 voice=$2 rate=$3 text=$4 vol=${5:-1.0} pitch=${6:-1.0}
  say -v "$(v "$voice")" -r "$rate" -o "$T/$out.aiff" "$text"
  ffmpeg -loglevel error -y -i "$T/$out.aiff" -af "asetrate=22050*$pitch,aresample=22050,atempo=1/$pitch,volume=$vol" -ac 1 -ar 22050 -b:a 48k "$out.mp3"
}
mix() {  # mix 輸出名 第一段 第二段 第二段延遲毫秒
  ffmpeg -loglevel error -y -i "$2.mp3" -i "$3.mp3" -filter_complex "[1]adelay=$4|$4[b];[0][b]amix=inputs=2:duration=longest:normalize=0" -ac 1 -ar 22050 -b:a 48k "$1.mp3"
}
seq2() {  # seq2 輸出名 第一段 第二段 間隔秒
  ffmpeg -loglevel error -y -i "$2.mp3" -f lavfi -t "$4" -i anullsrc=r=22050:cl=mono -i "$3.mp3" -filter_complex "[0][1][2]concat=n=3:v=0:a=1" -ac 1 -ar 22050 -b:a 48k "$1.mp3"
}
# 原音（處理前）
mk s01 Eddy 185 "好，那我們來聽聽看大家這週的練習。美華，妳要不要先分享？"
mk s02 Meijia 190 "好。這週我試著在生氣的時候先停一下，大概停三次呼吸，再決定要不要說話。"
mk s03 Eddy 185 "嗯，停三次呼吸，很好。"
mk s04 Meijia 190 "可是有一次在公司開會，我還是直接回嘴了。後來才發現，我根本沒有聽完同事的話。"
mk s05t Eddy 185 "所以後來妳怎麼做？"
mk s05s Reed 190 "我也有這種經驗。"
mix s05 s05t s05s 600
mk s06 Eddy 185 "小明你也有？那你接著說。"
mk s07 Reed 190 "對，我覺得最難的是，當下根本不知道自己在生氣，都是事後才發現。"
mk s09 Eddy 185 "這就是書裡說的，我們常常以為自己是對的。佩君，妳剛剛好像也想說什麼？"
mk s10 Shelley 190 "對，美華剛剛講的，讓我想到我媽媽。她常說我講話太快，我以前都覺得是她想太多。"
mk s11 Eddy 185 "大家等一下，我這邊網路好像斷了，我重新連一下。"
mk s12 Eddy 185 "上週志豪也提到類似的事。"
mk s13 Grandma 180 "喂？聽得到嗎？" 0.35
# 處理後
mk p01n Eddy 185 "好，那我們來聽聽看大家這週的練習。Bella，妳要不要先分享？"
mk p01s Eddy 200 "好，那我們來聽聽看大家這週的練習。Bella，妳要不要先分享？" 1.0 1.03
mk p02 Sandy 190 "好。這週我試著在生氣的時候先停一下，大概停三次呼吸，再決定要不要說話。"
mk p04 Sandy 190 "可是有一次在公司開會，我還是直接回嘴了。後來才發現，我根本沒有聽完同事的話。"
mk p05t Eddy 185 "所以後來妳怎麼做？"
mk p05c Grandpa 185 "我也有這種經驗。"
seq2 p05a p05t p05c 0.3
mix p05b p05t p05c 600
mk p06 Eddy 185 "Alex你也有？那你接著說。"
mk p07 Grandpa 185 "對，我覺得最難的是，當下根本不知道自己在生氣，都是事後才發現。"
mk p09 Eddy 185 "這就是書裡說的，我們常常以為自己是對的。Daisy，妳剛剛好像也想說什麼？"
mk p10 Flo 190 "對，Bella剛剛講的，讓我想到我媽媽。她常說我講話太快，我以前都覺得是她想太多。"
mk p12 Eddy 185 "上週Chris也提到類似的事。"
# 老師聲音試聽（同一句、三種參考音；樣品用語速與音高假裝差異）
mk tA Eddy 175 "我們練習的不是不生氣，而是在生氣的時候，還記得回到呼吸。"
mk tB Eddy 200 "我們練習的不是不生氣，而是在生氣的時候，還記得回到呼吸。" 1.0 1.04
mk tC Eddy 185 "我們練習的不是不生氣，而是在生氣的時候，還記得回到呼吸。" 1.0 0.96
# 匿名聲線庫
for pair in "vFA Sandy" "vFB Flo" "vFC Grandma" "vMA Grandpa" "vMB Rocko"; do
  set -- $pair; mk "$1" "$2" 185 "大家好，這是匿名聲線的試聽。這個聲音不屬於任何一位學員。"
done
# 成品片段（第 7 步整片檢查用）：刪除段落 s11 不放進來
LIST="p01n s03 p02 p04 p05a p06 p07 p09 p10 p12"
ffmpeg -loglevel error -y -f lavfi -t 0.4 -i anullsrc=r=22050:cl=mono -ac 1 -ar 22050 -b:a 48k gap.mp3
: > "$T/list.txt"; for c in $LIST; do echo "file '$PWD/$c.mp3'" >> "$T/list.txt"; echo "file '$PWD/gap.mp3'" >> "$T/list.txt"; done
ffmpeg -loglevel error -y -f concat -safe 0 -i "$T/list.txt" -ac 1 -ar 22050 -b:a 48k final.mp3
/bin/rm -f s05t.mp3 s05s.mp3 p05t.mp3
# 每段長度（秒），給網頁標時間點用
echo "{"; first=1; for f in *.mp3; do d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f"); [ $first = 1 ] && first=0 || echo ","; printf '"%s": %.2f' "${f%.mp3}" "$d"; done; echo; echo "}"
