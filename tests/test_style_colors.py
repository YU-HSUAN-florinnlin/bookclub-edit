"""bookclub/web/style.css 的顏色規則（1022-3 #269）。

- 深色模式只有一個 @media (prefers-color-scheme: dark) 區塊，裡面只覆寫 :root 的變數
- 按鈕、選項、輸入框這類可以按的東西不寫死顏色，一律用變數（白名單列在 HEX_OK）
- 用到的 var(--x) 都要在 :root 定義（沒有備用值的）
- 淺色幾個主要變數維持原本的值（深色整理不能動到淺色）

獨立可跑：PYTHONPATH=. .venv/bin/python tests/test_style_colors.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401 — 統一的暫存資料夾規則（這支沒有建暫存檔）

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "bookclub" / "web" / "style.css").read_text(encoding="utf-8")
CSS_NC = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(|(?<![-\w])(?:white|black)\b")   # 1022-3 審查：rgb()/hsl()/顏色名也算寫死

# 可以按的東西：選擇器有這些字就不准寫死十六進位顏色
CLICKABLE = re.compile(r"(?:^|[\s.>+~,(])(?:button|ghost|[\w-]*chip|input|select|textarea|summary|tool-btn|nav-toggle|fc-more|fc-foldbtn|ui-toast-x|help-tip-btn)(?![\w-])")   # 1022-3 審查：只比整個名稱，不比字串片段；summary（點了會展開）也算
# 白名單：紅色橫幅上的白色按鈕（底色固定是紅的，深淺色都一樣）
HEX_OK = {".save-error button", ".save-error button.ghost"}


def _blocks(text: str):
    """回傳 [(前導字串, 內容)]，只拆最外層的 { }。"""
    out, depth, start, head_start = [], 0, 0, 0
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                head = text[head_start:i].strip()
                start = i + 1
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                out.append((head, text[start:i]))
                head_start = i + 1
    assert depth == 0, "style.css 的大括號沒有成對"
    return out


def _rules(text: str):
    """攤平成 [(選擇器, 宣告)]，@media 裡的也攤開（選擇器前加 @media 字樣）。"""
    res = []
    for head, body in _blocks(text):
        if head.startswith("@media") or head.startswith("@supports"):
            for h2, b2 in _blocks(body):
                res.append((f"{head} {h2}", b2))
        elif head.startswith("@keyframes"):
            continue
        else:
            res.append((head, body))
    return res


def _root_vars(body: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", body)}


def test_one_dark_block_only_vars():
    darks = [m.start() for m in re.finditer(r"@media\s*\(prefers-color-scheme:\s*dark\)", CSS_NC)]
    assert len(darks) == 1, f"深色區塊應該只有一個，找到 {len(darks)} 個"
    dark = [(h, b) for h, b in _blocks(CSS_NC) if "prefers-color-scheme" in h]
    assert len(dark) == 1
    inner = _blocks(dark[0][1])
    assert [h for h, _ in inner] == [":root"], f"深色區塊只能有 :root，現在有 {[h for h, _ in inner]}"
    for decl in filter(None, (d.strip() for d in inner[0][1].split(";"))):
        prop = decl.split(":", 1)[0].strip()
        assert prop.startswith("--") or prop == "color-scheme", f"深色區塊只覆寫變數：{decl}"


def test_clickable_rules_use_vars():
    bad = []
    for sel, body in _rules(CSS_NC):
        if sel.startswith(":root") or "prefers-color-scheme" in sel:
            continue
        if not CLICKABLE.search(sel):
            continue
        if sel in HEX_OK:
            continue
        if HEX.search(body):
            bad.append(f"{sel} → {HEX.findall(body)}")
    assert not bad, "按鈕、選項、輸入框不要寫死顏色，改用 :root 的變數：\n" + "\n".join(bad)


def test_vars_defined():
    root = [b for h, b in _blocks(CSS_NC) if h == ":root"]
    assert len(root) == 1, "最上面應該只有一個 :root"
    defined = set(_root_vars(root[0]))
    dark = [b for h, b in _blocks(CSS_NC) if "prefers-color-scheme" in h][0]
    dark_vars = set(_root_vars(dark))
    assert dark_vars <= defined, f"深色有、淺色沒有的變數：{sorted(dark_vars - defined)}"
    # 區域變數（在別的規則裡自己定義的，例如 .fc 的 --fc-*）也算
    local = {m.group(1) for m in re.finditer(r"(--[\w-]+)\s*:", CSS_NC)}
    missing = sorted({m.group(1) for m in re.finditer(r"var\((--[\w-]+)\s*\)", CSS_NC)} - defined - local)
    assert not missing, f"用到但沒定義的變數：{missing}"


def test_light_values_kept():
    root = [b for h, b in _blocks(CSS_NC) if h == ":root"][0]
    v = _root_vars(root)
    keep = {"--fg": "#222", "--muted": "#666", "--border": "#ddd", "--bg": "#fff", "--bg-soft": "#f5f5f5",
            "--page-bg": "#f5f5f5", "--card-bg": "#fff", "--accent": "#2a6f2a", "--btn-bg": "#333", "--btn-fg": "#fff",
            "--btn-border": "#999", "--btn2-bg": "#fff", "--btn2-fg": "#333",
            "--rv-line": "#e6e6e6", "--pop-bg": "#fff", "--dialog-border": "transparent",
            # 10-22 宇軒 #270：淺色可以按的外框太淡，刻意加深（原 ghost #e6e6e6、chip #c9c9c9、輸入框 #ddd、
            # btn2 #999、ctl #ccc），統一中灰 #8a8a8a（對白底 3.45、對 #f5f5f5 3.17），滑過 #6e6e6e
            "--ghost-border": "#8a8a8a", "--ghost-border-hover": "#6e6e6e", "--btn2-border": "#8a8a8a",
            "--chip-border": "#8a8a8a", "--input-border": "#8a8a8a", "--ctl-border": "#8a8a8a"}
    diff = {k: (v.get(k), want) for k, want in keep.items() if v.get(k) != want}
    assert not diff, f"淺色變數被改了（現在, 原本）：{diff}"


def _lum(h: str) -> float:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    ch = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    ch = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in ch]
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]


def _contrast(a: str, b: str) -> float:
    x, y = sorted((_lum(a), _lum(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


def test_light_clickable_borders_contrast():
    """10-22 宇軒 #270：淺色可以按的外框對白卡片、整頁底色都要 ≥ 3:1（WCAG 1.4.11）。"""
    root = [b for h, b in _blocks(CSS_NC) if h == ":root"][0]
    v = _root_vars(root)
    bad = []
    for k in ("--ghost-border", "--ghost-border-hover", "--btn2-border", "--btn2-border-hover",
              "--chip-border", "--chip-border-hover", "--input-border", "--ctl-border"):
        for bg in (v["--card-bg"], v["--page-bg"]):
            c = _contrast(v[k], bg)
            if c < 3:
                bad.append(f"{k} {v[k]} 對 {bg} 只有 {c:.2f}")
    assert not bad, "\n".join(bad)


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
