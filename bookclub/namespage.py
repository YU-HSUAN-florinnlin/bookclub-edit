"""流程第 1 步找名字之後的覆核頁：`bookclub/names.py` 算出候選之後，這支模組把
`名字候選.json` 轉成給人看的靜態覆核頁 `工作區/名字覆核.html`，取代原本手寫在
暫存資料夾的版本；同時把每筆候選的建議切點消音，另存一份「消音版」音檔供比較
試聽（放在 `名字候選/`，檔名加 `_消音`）。

隱私：候選逐字稿、matched_text 都是個資，只寫進 `名字覆核.html`（跟工作區其他
檔案一樣，留在本機給覆核用），不印在終端機、不寫進 report/summary。

用法：`bookclub.analyze.run_analyze` 找完名字後自動呼叫 `build_review_page`；
也可以獨立呼叫，讀 `workdir/名字候選.json` 重新產生頁面（例如排除清單更新後、
想重新看一次覆核頁，不用重跑找名字）。
"""

from __future__ import annotations

import html
import json
from pathlib import Path

import soundfile as sf

from bookclub.names import CANDIDATE_CLIP_PAD_S
from bookclub.workdir import name_candidates_dir, names_path, read_json

MUTE_SUFFIX = "_消音"


# ---------- 時間格式 ----------

def _fmt_hms1(t: float) -> str:
    """秒數轉 `h:mm:ss.s`（小時不補零，保留一位小數）——覆核頁顯示原片時間用，
    跟 `workdir.fmt_time` 的 `HH:MM:SS`（整數秒、給報告用）是不同用途，不共用。"""
    t = max(0.0, float(t))
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - h * 3600 - m * 60
    return f"{h}:{m:02d}:{s:04.1f}"


# ---------- 消音版音檔 ----------

def _mute_clip(clip_path: Path, clip_start: float, mute_start: float, mute_end: float) -> Path | None:
    """讀候選的原音小段（`候選音檔`，名字前後各 `CANDIDATE_CLIP_PAD_S` 秒），把
    `[mute_start, mute_end]`（原片絕對秒數，候選的建議切點）對應到這段小音檔裡的
    區間打成靜音，另存 `_消音` 版本供覆核頁比較試聽。已存在就不重切；原音檔不存在
    就回傳 None（覆核頁該筆的消音播放器改顯示「音檔缺失」）。"""
    if not clip_path.exists():
        return None
    out_path = clip_path.with_name(clip_path.stem + MUTE_SUFFIX + clip_path.suffix)
    if out_path.exists():
        return out_path
    x, sr = sf.read(str(clip_path), dtype="float32")
    i0 = int(round(max(0.0, mute_start - clip_start) * sr))
    i1 = int(round(max(0.0, mute_end - clip_start) * sr))
    i0 = min(max(i0, 0), len(x))
    i1 = min(max(i1, i0), len(x))
    muted = x.copy()
    muted[i0:i1] = 0.0
    sf.write(str(out_path), muted, sr)
    return out_path


# ---------- 逐字稿標色 ----------

def _highlight_sentence(sentence: str, matched_text: str, position: str) -> str:
    """把 `matched_text` 在 `sentence` 裡用 `<mark>` 標黃底，回傳已跳脫的 HTML。
    `matched_text` 來自 Groq 的逐字（words）時間軸，`sentence` 是 Groq 的整句
    （sentences）文字，兩邊偶爾字面對不上（見 docs/工作區格式.md「已知落差」）；
    精準比對不到時，退回照「位置」概略標色，不是完全不標。"""
    esc = html.escape
    sentence = sentence or ""
    if matched_text and matched_text in sentence:
        idx = sentence.find(matched_text)
        before, mid, after = sentence[:idx], sentence[idx:idx + len(matched_text)], sentence[idx + len(matched_text):]
        return f"{esc(before)}<mark>{esc(mid)}</mark>{esc(after)}"

    n = len(matched_text) if matched_text else 1
    n = max(1, min(n, len(sentence))) if sentence else 0
    if not sentence:
        return ""
    if position in ("句首", "句首句尾"):
        return f"<mark>{esc(sentence[:n])}</mark>{esc(sentence[n:])}"
    if position == "句尾":
        cut = max(0, len(sentence) - n)
        return f"{esc(sentence[:cut])}<mark>{esc(sentence[cut:])}</mark>"
    # 句中且精準比對不到：無法可靠定位確切字元，整句加註記標色，不亂標位置
    return f'<span class="approx" title="精準比對不到，位置僅供參考">{esc(sentence)}</span>'


# ---------- 主流程 ----------

def build_review_page(workdir: Path, names_result: dict | None = None) -> Path:
    """產生 `workdir/名字覆核.html`。`names_result` 不給就讀 `workdir/名字候選.json`。
    回傳寫出的檔案路徑。"""
    workdir = Path(workdir)
    if names_result is None:
        names_result = read_json(names_path(workdir), default=None)
        if names_result is None:
            raise FileNotFoundError(f"{names_path(workdir)} 不存在，請先跑找名字（bookclub run analyze ... --roster ...）")

    candidates = names_result.get("candidates", [])
    excluded = names_result.get("已自動排除", [])
    exclusion_rows = names_result.get("排除清單", [])
    stats = names_result.get("統計", {})

    clip_dir = name_candidates_dir(workdir)

    rows_data = []  # 給 JS「複製回報」用的精簡資料
    rows_html_parts = []
    low_conf_anchors = []

    for i, c in enumerate(candidates, start=1):
        time_label = _fmt_hms1(c.get("start", 0.0))
        matched_text = c.get("matched_text", "")
        sentence = c.get("sentence", "")
        position = c.get("位置", "")
        confidence = c.get("信心", "")
        level = c.get("比對層級", "")
        source = "敏感詞" if c.get("敏感詞") else "名冊"
        code = c.get("代號", "")
        action = c.get("建議做法", "")
        cut_confidence = c.get("切點信心", "")

        clip_start = max(0.0, c.get("start", 0.0) - CANDIDATE_CLIP_PAD_S)
        orig_rel = c.get("候選音檔", "")
        orig_path = workdir / orig_rel if orig_rel else None
        muted_rel = None
        if orig_path is not None and orig_path.exists():
            muted_path = _mute_clip(orig_path, clip_start, c.get("start", 0.0), c.get("end", 0.0))
            if muted_path is not None:
                muted_rel = str(muted_path.relative_to(workdir))

        row_classes = ["row"]
        low_conf = confidence == "低"
        if low_conf:
            row_classes.append("lowconf")
            low_conf_anchors.append(i)

        highlighted = _highlight_sentence(sentence, matched_text, position)

        orig_player = (
            f'<audio controls preload="none" src="{html.escape(orig_rel)}"></audio>'
            if orig_rel else '<div class="missing">（原音檔缺失）</div>'
        )
        muted_player = (
            f'<audio controls preload="none" src="{html.escape(muted_rel)}"></audio>'
            if muted_rel else '<div class="missing">（消音版缺失）</div>'
        )

        rows_html_parts.append(f"""
<div class="{' '.join(row_classes)}" id="row-{i}">
  <div class="rowhead">
    <span class="idx">第 {i} 筆</span>
    <span class="time">{html.escape(time_label)}</span>
    {'<span class="badge lowconf-badge">低信心</span>' if low_conf else ''}
  </div>
  <div class="sentence">{highlighted}</div>
  <table class="meta">
    <tr><td>抓到的字</td><td>{html.escape(matched_text)}</td>
        <td>代號</td><td>{html.escape(code)}</td></tr>
    <tr><td>位置</td><td>{html.escape(position)}</td>
        <td>比對層級</td><td>{html.escape(level)}</td></tr>
    <tr><td>信心</td><td class="conf-{html.escape(confidence)}">{html.escape(confidence)}</td>
        <td>來源</td><td>{html.escape(source)}</td></tr>
    <tr><td>建議做法</td><td>{html.escape(action)}</td>
        <td>切點信心</td><td>{html.escape(cut_confidence)}</td></tr>
  </table>
  <div class="players">
    <div><div class="playerlabel">原音</div>{orig_player}</div>
    <div><div class="playerlabel">消音版（建議切點）</div>{muted_player}</div>
  </div>
  <div class="checks">
    <label><input type="checkbox" id="c{i}-notname"> 不是名字</label>
    <label><input type="checkbox" id="c{i}-place"> 是地名</label>
    <label><input type="checkbox" id="c{i}-cutoff"> 切點削到旁邊的字</label>
  </div>
</div>""")

        rows_data.append({"idx": i, "time": time_label, "text": matched_text})

    excluded_rows_html = "".join(
        f"<tr><td>{_fmt_hms1(e.get('start', 0.0))}</td>"
        f"<td>{html.escape(e.get('matched_text', ''))}</td>"
        f"<td>{html.escape(e.get('原因', ''))}</td></tr>"
        for e in excluded
    ) or '<tr><td colspan="3">（這次沒有筆被排除清單擋掉）</td></tr>'

    exclusion_list_html = "".join(
        f"<li>「{html.escape(r.get('詞', ''))}」— {html.escape(r.get('原因', ''))}"
        f"（{html.escape(r.get('建立日期', ''))}）</li>"
        for r in exclusion_rows
    ) or "<li>（目前沒有排除清單，或清單檔案不存在）</li>"

    low_conf_html = "".join(f'<a href="#row-{i}">第 {i} 筆</a>' for i in low_conf_anchors) or "（沒有低信心候選）"

    total = stats.get("總筆數", len(candidates))
    excluded_count = stats.get("已自動排除數", len(excluded))
    conf_dist = stats.get("信心分布", {})
    action_dist = stats.get("各建議做法筆數", {})
    cut_dist = stats.get("切點信心分布", {})

    payload = json.dumps(rows_data, ensure_ascii=False)

    page = f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>找名字覆核</title>
<style>
  body {{ font-family: -apple-system, "PingFang TC", sans-serif; max-width: 720px;
         margin: 24px auto; padding: 0 16px 80px; line-height: 1.6; color: #222; }}
  h1 {{ font-size: 20px; }}
  h2 {{ font-size: 16px; margin-top: 28px; }}
  .summary {{ background: #f3f3f3; border-radius: 8px; padding: 12px 14px; font-size: 14px; }}
  .summary table {{ width: 100%; border-collapse: collapse; margin-top: 6px; }}
  .summary td {{ padding: 2px 6px; font-size: 13px; }}
  .excluded {{ background: #fff6e0; border: 1px solid #e8d9a8; border-radius: 8px;
               padding: 10px 14px; margin-top: 12px; font-size: 14px; }}
  .excluded table {{ width: 100%; border-collapse: collapse; margin-top: 6px; font-size: 13px; }}
  .excluded td, .excluded th {{ border-bottom: 1px solid #e8d9a8; padding: 4px 6px; text-align: left; }}
  .lowconf-jump {{ font-size: 13px; margin: 8px 0 20px; }}
  .lowconf-jump a {{ margin-right: 10px; }}
  .row {{ border: 1px solid #ddd; border-radius: 8px; padding: 12px 14px; margin: 14px 0; }}
  .row.lowconf {{ border-color: #d98c00; background: #fffaf0; }}
  .rowhead {{ display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }}
  .idx {{ font-weight: bold; }}
  .time {{ color: #555; font-size: 13px; }}
  .badge {{ font-size: 12px; padding: 2px 8px; border-radius: 10px; }}
  .lowconf-badge {{ background: #d98c00; color: #fff; }}
  .sentence {{ font-size: 16px; margin: 6px 0 10px; }}
  .sentence mark {{ background: #ffe066; padding: 0 2px; border-radius: 3px; }}
  .sentence .approx {{ background: #ffe6e6; padding: 0 2px; border-radius: 3px; }}
  table.meta {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 10px; }}
  table.meta td {{ padding: 3px 6px; border-bottom: 1px solid #eee; }}
  table.meta td:nth-child(1), table.meta td:nth-child(3) {{ color: #888; width: 18%; white-space: nowrap; }}
  .conf-低 {{ color: #b30000; font-weight: bold; }}
  .conf-中 {{ color: #a06a00; }}
  .conf-高 {{ color: #2a7a2a; }}
  .players {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; }}
  .players > div {{ flex: 1 1 260px; min-width: 220px; }}
  .playerlabel {{ font-size: 12px; color: #666; margin-bottom: 2px; }}
  audio {{ width: 100%; }}
  .missing {{ font-size: 12px; color: #999; }}
  .checks {{ display: flex; gap: 16px; flex-wrap: wrap; font-size: 14px; }}
  .footer {{ position: sticky; bottom: 0; background: #fff; border-top: 1px solid #ddd;
             padding: 12px 0; margin-top: 24px; }}
  button {{ font-size: 15px; padding: 10px 14px; border-radius: 6px; border: 1px solid #999;
            background: #333; color: #fff; }}
  pre#reportPreview {{ white-space: pre-wrap; background: #f7f7f7; border-radius: 6px;
                        padding: 10px; font-size: 13px; margin-top: 10px; min-height: 1.6em; }}
</style>
</head>
<body>
<h1>找名字覆核</h1>

<div class="summary">
  <div>候選總筆數：{total}　已自動排除：{excluded_count} 筆</div>
  <table>
    <tr><td>信心分布</td><td>{html.escape(json.dumps(conf_dist, ensure_ascii=False))}</td></tr>
    <tr><td>建議做法分布</td><td>{html.escape(json.dumps(action_dist, ensure_ascii=False))}</td></tr>
    <tr><td>切點信心分布</td><td>{html.escape(json.dumps(cut_dist, ensure_ascii=False))}</td></tr>
  </table>
</div>

<div class="excluded">
  <strong>已自動排除（{excluded_count} 筆）</strong>
  <table>
    <tr><th>時間</th><th>抓到的字</th><th>原因</th></tr>
    {excluded_rows_html}
  </table>
  <div style="margin-top:8px;"><strong>目前排除清單內容：</strong></div>
  <ul>{exclusion_list_html}</ul>
</div>

<h2>低信心候選（建議優先看）</h2>
<div class="lowconf-jump">{low_conf_html}</div>

<h2>逐筆候選</h2>
{''.join(rows_html_parts) if rows_html_parts else '<p>（沒有候選）</p>'}

<div class="footer">
  <button id="copyBtn">複製回報</button>
  <pre id="reportPreview"></pre>
</div>

<script>
const ROWS = {payload};
function labelsFor(idx) {{
  const labels = [];
  if (document.getElementById(`c${{idx}}-notname`).checked) labels.push('不是名字');
  if (document.getElementById(`c${{idx}}-place`).checked) labels.push('是地名');
  if (document.getElementById(`c${{idx}}-cutoff`).checked) labels.push('切點削到旁邊的字');
  return labels;
}}
document.getElementById('copyBtn').addEventListener('click', async () => {{
  const lines = [];
  for (const r of ROWS) {{
    const labels = labelsFor(r.idx);
    if (labels.length) lines.push(`${{r.idx}} ${{r.time}} ${{r.text}}：${{labels.join('、')}}`);
  }}
  const text = lines.length ? lines.join('\\n') : '（沒有勾選任何項目）';
  document.getElementById('reportPreview').textContent = text;
  try {{
    await navigator.clipboard.writeText(text);
    alert('已複製回報內容');
  }} catch (e) {{
    alert('複製失敗，請手動選取下面的文字複製');
  }}
}});
</script>
</body>
</html>
"""

    out_path = workdir / "名字覆核.html"
    out_path.write_text(page, encoding="utf-8")
    return out_path
