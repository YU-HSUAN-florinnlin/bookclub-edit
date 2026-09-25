"use strict";

/* 第 3 步「覆核工作台」（09-25 宇軒定案：原本第 3、4 步合成一步）。
 * 由上到下：原片播放器 → 時間軸（整支＋放大前後 60 秒）→ 整體設定 → 待處理清單。
 * 老師的段落不逐段看，只畫在時間軸上。資料：GET /api/review；存檔：/api/turns/*、/api/review/*。
 * 快捷鍵：空白鍵播放暫停、J／L 前後 5 秒、⌘／Ctrl＋Enter 確認並跳下一筆、I／O 標起訖、
 * Esc（在文字框裡）從頭播這一筆。依賴 app.js 的 apiGet／apiPost／esc／contentEl。 */

const RV_COLORS = ["#e07a5f", "#3d9970", "#8e6cc8", "#d4a017", "#2b8cbe", "#c2185b", "#6b8e23", "#ff7f0e",
  "#17becf", "#8c564b", "#bc80bd", "#b15928"];
const RV_ZOOM_S = 60;
const RV_FILTERS = ["全部", "學員段落", "名字", "重疊", "刪除段落", "局部消音", "還沒確認"];
const RV_FILTER_LABEL = { "名字": "老師提到名字" };

const rv = {
  data: null, video: null, inMark: null, outMark: null, filter: "全部", highlight: true, follow: false,
  autoplay: true, activeKey: null, nowKey: null, lastActivity: Date.now(), timeTimer: null, zoomTimer: 0,
  stopAt: null, dragging: false,
};

function rvKey(it) { return `${it["類型"]}:${it.id}`; }
function rvItem(key) { return rv.data["項目"].find((x) => rvKey(x) === key); }

function rvFmt(sec, digits = 0) {
  if (sec == null || isNaN(sec)) return "—";
  sec = Math.max(0, sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const ss = digits ? s.toFixed(digits).padStart(3 + digits, "0") : String(Math.floor(s)).padStart(2, "0");
  return `${h ? h + ":" + String(m).padStart(2, "0") : m}:${ss}`;
}

function rvParseTime(text) {
  const parts = String(text).trim().replace("：", ":").split(":");
  if (!parts.length || parts.length > 3 || parts.some((p) => p.trim() === "" || isNaN(Number(p)))) return null;
  return parts.reduce((acc, p) => acc * 60 + Number(p), 0);
}

function rvColor(who) {
  if (who === "老師") return "#b9b9b9";
  const names = Object.keys(rv.data["學員"] || {});
  const i = names.indexOf(who);
  return i >= 0 ? RV_COLORS[i % RV_COLORS.length] : "#999";
}

function rvWho(name) {
  if (!name) return "（不知道）";
  if (name === "老師") return "老師";
  const code = ((rv.data["學員"] || {})[name] || {})["代號"];
  return code ? `${name} → ${code}` : name;
}

// ---------------------------------------------------------------------------
// 進入點
// ---------------------------------------------------------------------------

async function renderReview() {
  contentEl.innerHTML = "<p>載入中…</p>";
  rv.data = await apiGet("/api/review");
  const v = rv.data["影片"];
  contentEl.innerHTML = `
    <h1>3　覆核工作台</h1>
    <div class="rv-top">
      <div class="rv-player">
        ${v["有影片"] ? `<video id="rv-video" controls preload="metadata" src="${esc(v["網址"])}"></video>`
          : `<div class="notyet-card">找不到原片（${esc(v["檔名"] || "沒有記錄影片路徑")}）。用 <code>bookclub serve &lt;工作區&gt; --video &lt;影片&gt;</code> 重開。</div>`}
        <div class="rv-ctrl">
          <span class="rv-clock" id="rv-clock">0:00</span>／${esc(rvFmt(v["長度"]))}
          <input id="rv-goto" class="rv-goto" placeholder="跳到：43:15 或 1:05:00，按 Enter">
          <select id="rv-rate" title="播放速度"><option value="1">1 倍</option><option value="1.25">1.25 倍</option><option value="1.5">1.5 倍</option><option value="2">2 倍</option></select>
          <span class="rv-io">起 <input id="rv-in" class="rv-iobox" placeholder="按 I 或輸入">　訖 <input id="rv-out" class="rv-iobox" placeholder="按 O 或輸入"></span>
          <button id="rv-add-cut" class="secondary" title="用 I／O 標的起訖新增一段刪除段落">＋刪除段落</button>
          <button id="rv-add-mute" class="secondary" title="用 I／O 標的起訖新增一段局部消音">＋局部消音</button>
          <button id="rv-size" class="secondary" title="影片縮小，清單多一點空間">影片縮小</button>
        </div>
        <div class="rv-keys">空白鍵 播放／暫停　J／L 前後 5 秒　I／O 標起訖　⌘／Ctrl＋Enter 確認並跳下一筆　Esc（在文字框裡）從頭播這一筆</div>
      </div>
      <div class="rv-tl" id="rv-tl" title="點一下或拖拉跳到那個時間"></div>
      <div class="rv-zoom" id="rv-zoom" title="放大：目前位置前後各 60 秒"></div>
      <div class="rv-legend" id="rv-legend"></div>
    </div>
    <details class="rv-sec" id="rv-settings" open><summary><h2>整體設定：學員換成名冊上的誰、重新生成或保留原聲</h2></summary><div id="rv-people"></div></details>
    <div id="rv-progress"></div>
    <div class="rv-filters" id="rv-filters"></div>
    <div class="rv-opts">
      <label><input type="checkbox" id="rv-hl" ${rv.highlight ? "checked" : ""}> 播放時自動標亮目前那一筆</label>
      <label><input type="checkbox" id="rv-follow" ${rv.follow ? "checked" : ""}> 標亮時清單跟著捲動</label>
      <label><input type="checkbox" id="rv-auto" ${rv.autoplay ? "checked" : ""}> 確認後自動播放下一筆</label>
    </div>
    <div id="rv-list"></div>
    <details class="rv-sec" id="rv-skipped"></details>
    <div class="card rv-export"><button id="rv-export">匯出覆核結果（給夥伴執行 AI 步驟）</button> <span id="rv-export-msg"></span></div>`;

  rv.video = document.getElementById("rv-video");
  if (rv.video) rvBindVideo();
  document.getElementById("rv-goto").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const t = rvParseTime(e.target.value);
    if (t == null) { e.target.classList.add("bad"); return; }
    e.target.classList.remove("bad");
    rvSeek(t);
  });
  document.getElementById("rv-rate").addEventListener("change", (e) => { if (rv.video) rv.video.playbackRate = Number(e.target.value); });
  rvBindIOBoxes();
  document.getElementById("rv-add-cut").addEventListener("click", () => rvAddRange("cut"));
  document.getElementById("rv-add-mute").addEventListener("click", () => rvAddRange("mute"));
  document.getElementById("rv-hl").addEventListener("change", (e) => { rv.highlight = e.target.checked; rvHighlightNow(true); });
  document.getElementById("rv-follow").addEventListener("change", (e) => { rv.follow = e.target.checked; });
  document.getElementById("rv-auto").addEventListener("change", (e) => { rv.autoplay = e.target.checked; });
  document.getElementById("rv-export").addEventListener("click", rvExport);
  document.getElementById("rv-size").addEventListener("click", (e) => {
    rv.small = !rv.small;
    try { localStorage.setItem("rv-small", rv.small ? "1" : ""); } catch (err) { /* 沒有 localStorage 也沒關係 */ }
    rvApplySize();
  });
  try { rv.small = localStorage.getItem("rv-small") === "1"; } catch (err) { rv.small = false; }
  rvApplySize();
  window.addEventListener("resize", rvTopHeight);
  rvBindTimeline(document.getElementById("rv-tl"), () => [0, rv.data["影片"]["長度"]]);
  rvBindTimeline(document.getElementById("rv-zoom"), () => rvZoomWindow());

  rvRenderAll();
  rvStartTimeTracking();
}

function rvApplySize() {
  const top = document.querySelector(".rv-top");
  if (!top) return;
  top.classList.toggle("small", !!rv.small);
  document.getElementById("rv-size").textContent = rv.small ? "影片放大" : "影片縮小";
  rvTopHeight();
}

function rvTopHeight() {   // 卡片捲到畫面中間時不要被上方固定的影片區擋住
  const top = document.querySelector(".rv-top");
  if (top) document.documentElement.style.setProperty("--rv-top-h", `${top.offsetHeight}px`);
}

function rvRenderAll() {
  rvRenderTimeline();
  rvRenderZoom();
  rvRenderLegend();
  rvTopHeight();
  rvRenderPeople();
  rvRenderProgress();
  rvRenderFilters();
  rvRenderList();
  rvRenderSkipped();
}

async function rvReload() {
  const y = window.scrollY;
  rv.data = await apiGet("/api/review");
  rvRenderAll();
  window.scrollTo(0, y);
}

// ---------------------------------------------------------------------------
// 播放器
// ---------------------------------------------------------------------------

function rvBindVideo() {
  const v = rv.video;
  v.addEventListener("timeupdate", () => {
    document.getElementById("rv-clock").textContent = rvFmt(v.currentTime, 1);
    rvMovePlayhead();
    if (rv.stopAt != null && v.currentTime >= rv.stopAt) { v.pause(); rv.stopAt = null; }
    const now = Date.now();
    if (now - rv.zoomTimer > 250) { rv.zoomTimer = now; rvRenderZoom(); rvHighlightNow(false); }
  });
  v.addEventListener("seeked", () => { rvMovePlayhead(); rvRenderZoom(); rvHighlightNow(false); });
}

function rvSeek(t, play = false, stopAfter = null) {
  if (!rv.video) return;
  rv.video.currentTime = Math.max(0, Math.min(t, rv.data["影片"]["長度"] || t));
  rv.stopAt = stopAfter != null ? rv.video.currentTime + stopAfter : null;
  if (play) rv.video.play().catch(() => {});
  rvMovePlayhead();
  rvRenderZoom();
}

function rvPlayItem(it) {
  rv.activeKey = rvKey(it);
  rvMarkActive();
  rvSeek(Math.max(0, it.start - 2), true);
}

// ---------------------------------------------------------------------------
// 時間軸
// ---------------------------------------------------------------------------

function rvZoomWindow() {
  const t = rv.video ? rv.video.currentTime : 0;
  return [t - RV_ZOOM_S, t + RV_ZOOM_S];
}

function rvBindTimeline(el, rangeFn) {
  let range = null;   // 按下時固定範圍：放大軸拖拉時視窗不會跟著跑
  const seekAt = (e) => {
    const r = el.getBoundingClientRect();
    const [a, b] = range || rangeFn();
    const x = Math.min(Math.max(0, e.clientX - r.left), r.width);
    rvSeek(a + (x / r.width) * (b - a));
  };
  el.addEventListener("pointerdown", (e) => {
    range = rangeFn();
    rv.dragging = el;
    seekAt(e);
    try { el.setPointerCapture(e.pointerId); } catch (err) { /* 合成事件沒有真的指標 */ }
  });
  el.addEventListener("pointermove", (e) => { if (rv.dragging === el) seekAt(e); });
  const end = (e) => {
    rv.dragging = false;
    range = null;
    rvRenderZoom();
    try { el.releasePointerCapture(e.pointerId); } catch (err) { /* 同上 */ }
  };
  el.addEventListener("pointerup", end);
  el.addEventListener("pointercancel", end);
}

function rvMarkers() {
  const out = [];
  for (const it of rv.data["項目"]) {
    const t = it["類型"];
    if (t === "名字") out.push({ cls: "m-name", start: it.start, end: it.end, title: `名字 ${rvFmt(it.start)}` });
    else if (t === "重疊") out.push({ cls: "m-ov", start: it.start, end: it.end, title: `重疊 ${rvFmt(it.start)}` });
    else if (t === "刪除段落" && it["狀態"] !== "還原") out.push({ cls: "m-cut", start: it.start, end: it.end, title: `刪除 ${rvFmt(it.start)}–${rvFmt(it.end)}` });
    else if (t === "局部消音" && it["狀態"] !== "還原") out.push({ cls: "m-mute", start: it.start, end: it.end, title: `消音 ${rvFmt(it.start)}–${rvFmt(it.end)}` });
  }
  return out;
}

function rvStripHtml(a, b, withTicks) {
  const span = b - a || 1;
  const pct = (t) => ((t - a) / span) * 100;
  const bands = rv.data["色帶"].filter((x) => x.end > a && x.start < b).map((x) => {
    const l = Math.max(0, pct(x.start)), w = Math.max(0.15, Math.min(100, pct(x.end)) - l);
    const color = x["冥想導讀"] ? "#bfe3ff" : rvColor(x["說話者"]);
    return `<div class="rv-band" style="left:${l}%;width:${w}%;background:${color}" title="${esc(rvWho(x["說話者"]))}${x["冥想導讀"] ? "（冥想導讀）" : ""} ${rvFmt(x.start)}–${rvFmt(x.end)}"></div>`;
  }).join("");
  const marks = rvMarkers().filter((m) => m.end >= a && m.start <= b).map((m) => {
    const l = Math.max(0, pct(m.start)), w = Math.max(0, Math.min(100, pct(m.end)) - l);
    return `<div class="rv-mark ${m.cls}" style="left:${l}%;width:${w}%" title="${esc(m.title)}"></div>`;
  }).join("");
  let ticks = "";
  if (withTicks) {
    const step = 10;
    for (let t = Math.ceil(a / step) * step; t <= b; t += step) {
      if (t < 0) continue;
      ticks += `<div class="rv-tick" style="left:${pct(t)}%"><span>${rvFmt(t)}</span></div>`;
    }
  }
  const io = [rv.inMark, rv.outMark].map((t, k) => (t != null && t >= a && t <= b)
    ? `<div class="rv-iomark" style="left:${pct(t)}%" title="${k ? "訖" : "起"}"></div>` : "").join("");
  return `${bands}${marks}${ticks}${io}<div class="rv-head"></div>`;
}

function rvRenderTimeline() {
  document.getElementById("rv-tl").innerHTML = rvStripHtml(0, rv.data["影片"]["長度"] || 1, false);
  rvMovePlayhead();
}

function rvRenderZoom() {
  const el = document.getElementById("rv-zoom");
  if (!el || rv.dragging === el) return;   // 拖拉放大軸時先不重畫，放開再畫
  const [a, b] = rvZoomWindow();
  el.innerHTML = rvStripHtml(a, b, true);
  const head = el.querySelector(".rv-head");
  if (head) head.style.left = "50%";
}

function rvMovePlayhead() {
  const el = document.getElementById("rv-tl");
  if (!el || !rv.video) return;
  const head = el.querySelector(".rv-head");
  if (head) head.style.left = `${(rv.video.currentTime / (rv.data["影片"]["長度"] || 1)) * 100}%`;
}

function rvRenderLegend() {
  const people = Object.keys(rv.data["學員"] || {});
  document.getElementById("rv-legend").innerHTML = [
    `<span><i style="background:#b9b9b9"></i>老師</span>`, `<span><i style="background:#bfe3ff"></i>冥想導讀</span>`,
    ...people.map((p) => `<span><i style="background:${rvColor(p)}"></i>${esc(rvWho(p))}</span>`),
    `<span><i class="m-name"></i>名字</span>`, `<span><i class="m-ov"></i>重疊</span>`,
    `<span><i class="m-cut"></i>刪除段落</span>`, `<span><i class="m-mute"></i>局部消音</span>`,
  ].join("");
}

// ---------------------------------------------------------------------------
// 整體設定
// ---------------------------------------------------------------------------

function rvRenderPeople() {
  const people = Object.entries(rv.data["學員"] || {});
  const codes = rv.data["代號選項"] || [];
  if (!people.length) {
    document.getElementById("rv-people").innerHTML = `<p class="hint">${rv.data["有段落"] ? "這支影片沒有學員段落。" : "還沒有段落分析結果：在終端機跑 <code>bookclub run turns &lt;工作區&gt;</code>。"}</p>`;
    return;
  }
  const rows = people.map(([n, p]) => {
    const opts = ['<option value="">（還沒指定）</option>']
      .concat(codes.map((c) => `<option ${p["代號"] === c ? "selected" : ""}>${esc(c)}</option>`)).join("");
    const clue = Object.entries(p["點名線索"] || {}).map(([k, v]) => `${esc(k)}×${v}`).join("、");
    const sug = p["建議代號"];
    const voice = rv.data["選項"]["聲音"].map((c) => `<label><input type="radio" name="voice-${esc(n)}" class="rv-voice" data-person="${esc(n)}" value="${esc(c)}" ${p["聲音"] === c ? "checked" : ""}> ${esc(c)}</label>`).join(" ");
    return `<tr><td><i class="rv-dot" style="background:${rvColor(n)}"></i><b>${esc(n)}</b></td>
      <td>${esc(rvFmt(p["秒數"]))}・${p["段數"]} 段</td>
      <td><button class="rv-sample secondary" data-person="${esc(n)}">▶ 試聽</button></td>
      <td><select class="rv-code" data-person="${esc(n)}">${opts}</select>
        ${sug && !p["代號"] ? `<button class="rv-sug secondary" data-person="${esc(n)}" data-code="${esc(sug)}">用老師點名建議：${esc(sug)}</button>` : ""}</td>
      <td class="why">${clue ? `老師點名：${clue}` : ""}</td>
      <td class="rv-voicecell">${voice}</td></tr>`;
  }).join("");
  document.getElementById("rv-people").innerHTML = `
    <p class="hint">「學員 1、2⋯⋯」是聲紋分出來的。選名冊上的哪一位（顯示英文代號），老師提到這位學員名字時換成的代號就會一致。同一人被分成兩位時，在清單裡把段落的說話者改成同一位。</p>
    <table class="tl rv-people">${rows}</table>
    <p>一鍵全部切換：<button class="rv-voice-all secondary" data-v="重新生成">全部重新生成</button>
      <button class="rv-voice-all secondary" data-v="保留原聲">全部保留原聲</button>
      <span class="hint">（保留原聲只給已經同意的學員）</span></p>`;
  const root = document.getElementById("rv-people");
  root.querySelectorAll(".rv-code").forEach((el) => el.addEventListener("change", async () => {
    await apiPost("/api/turns/person", { "學員": el.dataset.person, "代號": el.value || null }); await rvReload();
  }));
  root.querySelectorAll(".rv-sug").forEach((b) => b.addEventListener("click", async () => {
    await apiPost("/api/turns/person", { "學員": b.dataset.person, "代號": b.dataset.code }); await rvReload();
  }));
  root.querySelectorAll(".rv-voice").forEach((el) => el.addEventListener("change", async () => {
    await apiPost("/api/review/voice", { "學員": el.dataset.person, "聲音": el.value }); await rvReload();
  }));
  root.querySelectorAll(".rv-voice-all").forEach((b) => b.addEventListener("click", async () => {
    await apiPost("/api/review/voice", { "學員": "全部", "聲音": b.dataset.v }); await rvReload();
  }));
  root.querySelectorAll(".rv-sample").forEach((b) => b.addEventListener("click", () => {
    const seg = rv.data["項目"].find((x) => x["類型"] === "學員段落" && x["說話者"] === b.dataset.person && x.end - x.start >= 3)
      || rv.data["項目"].find((x) => x["類型"] === "學員段落" && x["說話者"] === b.dataset.person);
    if (seg) rvSeek(seg.start, true, 12);
  }));
}

// ---------------------------------------------------------------------------
// 進度、篩選
// ---------------------------------------------------------------------------

function rvRenderProgress() {
  const p = rv.data["進度"];
  const pct = p["總數"] ? Math.round((p["已確認"] / p["總數"]) * 100) : 0;
  const est = p["推算全部秒數"] != null ? rvHm(p["推算全部秒數"]) : "—（先確認幾筆）";
  document.getElementById("rv-progress").innerHTML = `<div class="pr-progress"><div class="bar"><div style="width:${pct}%"></div></div>
    <div class="nums">已確認 <b id="rv-done">${p["已確認"]}</b>／${p["總數"]} 筆　覆核花了 <b>${rvHm(p["已花秒數"])}</b>　推算整支要 <b>${est}</b></div></div>`;
}

function rvHm(sec) {
  if (sec == null) return "—";
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h ? `${h} 小時 ${m} 分` : `${m} 分`;
}

function rvRenderFilters() {
  const items = rv.data["項目"];
  const count = (f) => f === "全部" ? items.length : f === "還沒確認" ? items.filter((x) => !x["已確認"]).length
    : items.filter((x) => x["類型"] === f).length;
  document.getElementById("rv-filters").innerHTML = RV_FILTERS.map((f) =>
    `<button class="rv-filter ${rv.filter === f ? "on" : ""}" data-f="${esc(f)}">${esc(RV_FILTER_LABEL[f] || f)}（${count(f)}）</button>`).join("");
  document.querySelectorAll(".rv-filter").forEach((b) => b.addEventListener("click", () => {
    rv.filter = b.dataset.f; rvRenderFilters(); rvApplyFilter();
  }));
}

function rvApplyFilter() {
  document.querySelectorAll(".rv-card").forEach((c) => {
    const it = rvItem(c.dataset.key);
    const show = !it ? false : rv.filter === "全部" ? true : rv.filter === "還沒確認" ? (!it["已確認"] || c.contains(document.activeElement))
      : it["類型"] === rv.filter;
    c.style.display = show ? "" : "none";
  });
}

// ---------------------------------------------------------------------------
// 待處理清單
// ---------------------------------------------------------------------------

function rvRenderList() {
  const items = rv.data["項目"];
  if (!items.length) {
    document.getElementById("rv-list").innerHTML = `<div class="notyet-card">沒有要處理的項目。</div>`;
    return;
  }
  document.getElementById("rv-list").innerHTML = items.map(rvCardHtml).join("");
  document.querySelectorAll(".rv-card").forEach(rvBindCard);
  rvApplyFilter();
  rvMarkActive();
}

function rvHead(it, label, extra = "") {
  return `<div class="rv-chead"><span class="rv-type t-${esc(it["類型"])}">${esc(label)}</span>
    <span class="time">${esc(rvFmt(it.start, 1))}–${esc(rvFmt(it.end, 1))}（${(it.end - it.start).toFixed(1)} 秒）</span>
    <button class="rv-play" title="影片跳到這一筆前 2 秒播放">▶</button>${extra}
    <span class="rv-state">${it["已確認"] ? "✓ 已確認" : ""}</span></div>`;
}

function rvRadios(name, options, current, cls) {
  return options.map((o) => `<label class="rv-radio"><input type="radio" name="${esc(name)}" class="${cls}" value="${esc(o)}" ${o === current ? "checked" : ""}> ${esc(o)}</label>`).join("");
}

function rvCardHtml(it) {
  const key = rvKey(it);
  const t = it["類型"];
  const base = `class="rv-card ${it["已確認"] ? "done" : ""} c-${esc(t)}" data-key="${esc(key)}" tabindex="-1"`;
  if (t === "學員段落") {
    const people = Object.keys(rv.data["學員"] || {});
    const whoOpts = ["老師", ...people, "新學員"].map((n) => `<option value="${esc(n)}" ${n === it["說話者"] ? "selected" : ""}>${esc(n === "新學員" ? "＋新的學員" : rvWho(n))}</option>`).join("");
    const rows = Math.min(12, Math.max(3, Math.ceil((it["校對稿"] || "").length / 40)));
    return `<div ${base}>${rvHead(it, `學員段落 ${it["序"]}`, `
      <select class="rv-who" title="改說話者">${whoOpts}</select>
      <button class="rv-merge secondary" title="這一段併進上一段">↑ 併入上一段</button>
      <button class="rv-split secondary" title="在文字框的游標位置切成兩段">✂ 從游標處切開</button>
      <label class="rv-ask"><input type="checkbox" class="rv-askbox" ${it["問老師"] ? "checked" : ""}> 聽不清楚，問老師</label>
      <button class="rv-confirm">${it["已確認"] ? "取消確認" : "確認"}</button>`)}
      ${it["學員是猜的"] ? '<div class="hint">這段太短，學員是猜的，請聽一下是誰。</div>' : ""}
      ${it["含本名"] ? '<div class="rv-warn">文字裡有名冊上的本名，記得換成英文代號（成品會照這份文字重念）。</div>' : ""}
      <textarea class="rv-text" rows="${rows}">${esc(it["校對稿"])}</textarea>
      <input class="rv-asknote" placeholder="要問老師什麼（例如：這裡聽不清楚是「覺察」還是「覺得」）" value="${esc(it["問老師備註"] || "")}" style="${it["問老師"] ? "" : "display:none"}">
      ${it["原文"] !== it["校對稿"] ? `<details class="pr-orig"><summary>看原本轉出來的</summary>${esc(it["原文"])}</details>` : ""}</div>`;
  }
  if (t === "名字") {
    const w = it["整句"];
    const tags = rv.data["選項"]["名字標記"].map((g) => `<label><input type="checkbox" class="rv-tag" value="${esc(g)}" ${it.tags.includes(g) ? "checked" : ""}> ${esc(g)}</label>`).join(" ");
    return `<div ${base}>${rvHead(it, "老師提到名字", ` <span class="badge">${esc(it["代號"] || "（沒有代號）")}</span>
      ${it["信心"] === "低" ? '<span class="badge error">低信心</span>' : ""}
      <button class="rv-confirm">${it["已確認"] ? "取消確認" : "確認"}</button>`)}
      <div class="sentence">${it.sentence_html}</div>
      ${w ? `<div class="rv-whole">整句（照標點擴成完整句子，${esc(rvFmt(w.start, 1))}–${esc(rvFmt(w.end, 1))}）：${esc(w["換成代號"] || w["原文"])}${w["換成代號"] ? "" : ' <span class="badge warn">句子裡找不到比對到的字，要人處理</span>'}</div>` : ""}
      <div class="rv-choices">${rvRadios(`name-${it.id}`, rv.data["選項"]["名字"], it["做法"], "rv-how")}
        <span class="hint">第 1 步建議：${esc(it["建議做法"] || "—")}（${esc(it["位置"])}、切點${esc(it["切點信心"])}）</span></div>
      <div class="checks">${tags}</div>
      <input class="rv-note" placeholder="備註（選填）" value="${esc(it.note)}"></div>`;
  }
  if (t === "重疊") {
    const people = Object.keys(rv.data["學員"] || {});
    const whoOpts = ['<option value="">（不知道）</option>', ...people.map((n) => `<option value="${esc(n)}" ${n === it["學員說話者"] ? "selected" : ""}>${esc(rvWho(n))}</option>`)].join("");
    const ctx = (it["附近逐字稿"] || []).map((s) => `<div><b>${esc(s["說話者"])}</b>（${esc(rvFmt(s.start, 1))}）：${esc(s.text)}</div>`).join("");
    return `<div ${base}>${rvHead(it, "重疊", ` <span class="hint">聲紋：${esc((it["角色"] || []).join("＋"))}</span>
      <button class="rv-confirm">${it["已確認"] ? "取消確認" : "儲存這一筆"}</button>`)}
      <div class="rv-ctx">${ctx || '<span class="hint">（附近沒有逐字稿）</span>'}</div>
      <div class="rv-choices">${rvRadios(`ov-${it.id}`, rv.data["選項"]["重疊"], it["做法"], "rv-ovhow")}</div>
      <div class="rv-warn" style="${it["做法"] === "不用改" ? "" : "display:none"}">「不用改」會保留原聲：只有同意保留原聲的學員才選這個。</div>
      <div class="rv-arrange" style="${it["做法"] === "兩邊都重生成" ? "" : "display:none"}">兩邊都重生成時：${rvRadios(`ar-${it.id}`, rv.data["選項"]["重疊排法"], it["排法"], "rv-ar")}</div>
      <div class="rv-2col"><label>老師說的<textarea class="rv-tt" rows="2">${esc(it["老師文字"])}</textarea></label>
        <label>學員說的（<select class="rv-ovwho">${whoOpts}</select>）<textarea class="rv-st" rows="2">${esc(it["學員文字"])}</textarea></label></div>
      <input class="rv-note" placeholder="備註（選填）" value="${esc(it["備註"])}"></div>`;
  }
  const isCut = t === "刪除段落";
  const off = isCut ? it["狀態"] === "還原" : it["狀態"] === "還原";
  const snapped = it["標的起訖"] && (Math.abs(it["標的起訖"][0] - it.start) > 0.01 || Math.abs(it["標的起訖"][1] - it.end) > 0.01)
    ? `<span class="hint">剪點已對齊安靜處（原本標 ${esc(rvFmt(it["標的起訖"][0], 2))}–${esc(rvFmt(it["標的起訖"][1], 2))}）</span>` : "";
  return `<div ${base}>${rvHead(it, isCut ? `刪除段落 ${it.id}` : `局部消音 ${it.id}`, `
      <button class="rv-toggle secondary">${off ? (isCut ? "改回刪除" : "改回消音") : "還原（不處理）"}</button>`)}
    <div class="rv-range">起 <input class="rv-a" value="${esc(rvFmt(it.start, 2))}"> 訖 <input class="rv-b" value="${esc(rvFmt(it.end, 2))}">
      <button class="rv-setio secondary" title="用目前 I／O 標的起訖">用目前的起訖</button>
      <button class="rv-saverange secondary">更新時間</button> ${snapped}</div>
    ${isCut ? `<div class="hint">${off ? "已還原：這段不刪。" : "聲音、畫面一起刪。"}</div>`
      : `<div class="rv-choices">${rvRadios(`mu-${it.id}`, rv.data["選項"]["消音"], it["方式"], "rv-muteway")}</div>`}
    <input class="rv-note" placeholder="備註（選填）" value="${esc(it["備註"] || "")}"></div>`;
}

function rvBindCard(card) {
  const key = card.dataset.key;
  const it = rvItem(key);
  const q = (sel) => card.querySelector(sel);
  card.addEventListener("focusin", () => { rv.activeKey = key; rvMarkActive(); });
  card.addEventListener("click", () => { rv.activeKey = key; rvMarkActive(); });
  q(".rv-play").addEventListener("click", (e) => { e.stopPropagation(); rvPlayItem(it); });
  const confirmBtn = q(".rv-confirm");
  if (confirmBtn) confirmBtn.addEventListener("click", () => it["已確認"] ? rvUnconfirm(key) : rvConfirm(key, false));
  const t = it["類型"];

  if (t === "學員段落") {
    q(".rv-who").addEventListener("change", async (e) => {
      await rvSaveTurnText(key);
      await apiPost("/api/turns/save", { id: it.id, "說話者": e.target.value }); await rvReload();
    });
    q(".rv-merge").addEventListener("click", async () => {
      await rvSaveTurnText(key);
      try { await apiPost("/api/turns/merge", { id: it.id }); } catch (err) { alert(err.message); return; }
      await rvReload();
    });
    q(".rv-split").addEventListener("mousedown", (e) => e.preventDefault());
    q(".rv-split").addEventListener("click", async () => {
      const ta = q(".rv-text");
      const at = ta.selectionStart;
      if (!at || at >= ta.value.length) { alert("先把游標放在要切開的地方（換人的第一個字前面）"); return; }
      await rvSaveTurnText(key);
      try { await apiPost("/api/turns/split", { id: it.id, at }); } catch (err) { alert(err.message); return; }
      await rvReload();
    });
    q(".rv-text").addEventListener("blur", () => rvSaveTurnText(key));
    q(".rv-askbox").addEventListener("change", async (e) => {
      q(".rv-asknote").style.display = e.target.checked ? "" : "none";
      const res = await apiPost("/api/turns/save", { id: it.id, "問老師": e.target.checked });
      it["問老師"] = res["段落"]["問老師"];
    });
    q(".rv-asknote").addEventListener("change", (e) => apiPost("/api/turns/save", { id: it.id, "問老師備註": e.target.value }));
  } else if (t === "名字") {
    card.querySelectorAll(".rv-how").forEach((el) => el.addEventListener("change", () => rvSaveName(key, { "做法": el.value })));
    card.querySelectorAll(".rv-tag").forEach((el) => el.addEventListener("change", () => rvSaveName(key, {
      tags: [...card.querySelectorAll(".rv-tag:checked")].map((x) => x.value) })));
    q(".rv-note").addEventListener("change", (e) => rvSaveName(key, { note: e.target.value }));
  } else if (t === "重疊") {
    card.querySelectorAll(".rv-ovhow").forEach((el) => el.addEventListener("change", () => {
      q(".rv-warn").style.display = el.value === "不用改" ? "" : "none";
      q(".rv-arrange").style.display = el.value === "兩邊都重生成" ? "" : "none";
      rvSaveOverlap(key, { "做法": el.value });
    }));
    card.querySelectorAll(".rv-ar").forEach((el) => el.addEventListener("change", () => rvSaveOverlap(key, { "排法": el.value })));
    q(".rv-tt").addEventListener("change", (e) => rvSaveOverlap(key, { "老師文字": e.target.value }));
    q(".rv-st").addEventListener("change", (e) => rvSaveOverlap(key, { "學員文字": e.target.value }));
    q(".rv-ovwho").addEventListener("change", (e) => rvSaveOverlap(key, { "學員說話者": e.target.value || null }));
    q(".rv-note").addEventListener("change", (e) => rvSaveOverlap(key, { "備註": e.target.value }));
  } else {
    const api = t === "刪除段落" ? "/api/review/cut" : "/api/review/mute";
    q(".rv-toggle").addEventListener("click", async () => {
      const on = t === "刪除段落" ? "刪除" : "消音";
      await apiPost(api, { id: it.id, "狀態": it["狀態"] === "還原" ? on : "還原" }); await rvReload();
    });
    q(".rv-setio").addEventListener("click", () => {
      if (rv.inMark != null) q(".rv-a").value = rvFmt(rv.inMark, 2);
      if (rv.outMark != null) q(".rv-b").value = rvFmt(rv.outMark, 2);
    });
    q(".rv-saverange").addEventListener("click", async () => {
      const a = rvParseTime(q(".rv-a").value), b = rvParseTime(q(".rv-b").value);
      if (a == null || b == null || b <= a) { alert("起訖時間格式不對，或訖早於起"); return; }
      try { await apiPost(api, { id: it.id, start: a, end: b }); } catch (err) { alert(err.message); return; }
      await rvReload();
    });
    card.querySelectorAll(".rv-muteway").forEach((el) => el.addEventListener("change", () => apiPost(api, { id: it.id, "方式": el.value })));
    q(".rv-note").addEventListener("change", (e) => apiPost(api, { id: it.id, "備註": e.target.value }));
  }
}

function rvCard(key) { return document.querySelector(`.rv-card[data-key="${CSS.escape(key)}"]`); }

function rvMarkActive() {
  document.querySelectorAll(".rv-card.active").forEach((c) => c.classList.remove("active"));
  const c = rv.activeKey && rvCard(rv.activeKey);
  if (c) c.classList.add("active");
}

function rvHighlightNow(force) {
  document.querySelectorAll(".rv-card.now").forEach((c) => { if (!rv.highlight) c.classList.remove("now"); });
  if (!rv.highlight || !rv.video) return;
  const t = rv.video.currentTime;
  const hit = rv.data["項目"].find((x) => x.start - 0.2 <= t && t <= x.end + 0.2);
  const key = hit ? rvKey(hit) : null;
  if (key === rv.nowKey && !force) return;
  rv.nowKey = key;
  document.querySelectorAll(".rv-card.now").forEach((c) => c.classList.remove("now"));
  const c = key && rvCard(key);
  if (c) {
    c.classList.add("now");
    if (rv.follow && !c.contains(document.activeElement)) c.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

function rvSetState(key, done) {
  const it = rvItem(key);
  it["已確認"] = done;
  const c = rvCard(key);
  if (c) {
    c.classList.toggle("done", done);
    c.querySelector(".rv-state").textContent = done ? "✓ 已確認" : "";
    const b = c.querySelector(".rv-confirm");
    if (b) b.textContent = done ? "取消確認" : it["類型"] === "重疊" ? "儲存這一筆" : "確認";
  }
  const items = rv.data["項目"];
  const p = rv.data["進度"];
  p["已確認"] = items.filter((x) => x["已確認"]).length;
  p["推算全部秒數"] = p["已確認"] && p["已花秒數"] ? Math.round((p["已花秒數"] / p["已確認"]) * items.length) : null;
  rvRenderProgress();
  rvRenderFilters();
}

async function rvSaveTurnText(key) {
  const it = rvItem(key), c = rvCard(key);
  if (!it || !c) return;
  const v = c.querySelector(".rv-text").value.trim();
  if (v === (it["校對稿"] || "").trim()) return;
  const res = await apiPost("/api/turns/save", { id: it.id, "校對稿": v });
  it["校對稿"] = res["段落"]["校對稿"];
}

async function rvSaveName(key, fields) {
  const it = rvItem(key);
  const res = await apiPost("/api/review/name", { id: it.id, ...fields });
  Object.assign(it, { "做法": res["決定"]["做法"] || it["做法"], tags: res["決定"].tags || [], note: res["決定"].note || "" });
  rvFlash(key, res["已加入排除清單"] ? "已存檔（已加入排除清單）" : "已存檔");
}

async function rvSaveOverlap(key, fields) {
  const it = rvItem(key);
  try {
    const res = await apiPost("/api/review/overlap", { id: it.id, ...fields });
    Object.assign(it, res["決定"]);
    rvFlash(key, "已存檔");
  } catch (err) { alert(err.message); }
}

function rvFlash(key, text) {
  const c = rvCard(key);
  if (!c) return;
  const s = c.querySelector(".rv-state");
  const old = rvItem(key)["已確認"] ? "✓ 已確認" : "";
  s.textContent = text;
  setTimeout(() => { s.textContent = rvItem(key) && rvItem(key)["已確認"] ? "✓ 已確認" : old; }, 1500);
}

async function rvConfirm(key, goNext) {
  const it = rvItem(key), c = rvCard(key);
  const t = it["類型"];
  try {
    if (t === "學員段落") {
      await apiPost("/api/turns/save", { id: it.id, "校對稿": c.querySelector(".rv-text").value, "已確認": true });
      it["校對稿"] = c.querySelector(".rv-text").value.trim();
    } else if (t === "名字") {
      const how = c.querySelector(".rv-how:checked");
      await apiPost("/api/review/name", { id: it.id, "做法": how ? how.value : it["做法"], "已確認": true });
    } else if (t === "重疊") {
      const how = c.querySelector(".rv-ovhow:checked");
      if (!how) { alert("先選這一處要怎麼處理"); return; }
      const ar = c.querySelector(".rv-ar:checked");
      await apiPost("/api/review/overlap", {
        id: it.id, "做法": how.value, "排法": ar ? ar.value : "前後排開", "老師文字": c.querySelector(".rv-tt").value,
        "學員文字": c.querySelector(".rv-st").value, "學員說話者": c.querySelector(".rv-ovwho").value || null,
        "備註": c.querySelector(".rv-note").value, "已確認": true });
      it["做法"] = how.value;
    }
  } catch (err) { alert(err.message); return; }
  if (t !== "刪除段落" && t !== "局部消音") rvSetState(key, true);
  rv.lastActivity = Date.now();
  if (goNext) rvGoNext(key);
  else rvApplyFilter();
}

async function rvUnconfirm(key) {
  const it = rvItem(key);
  if (it["類型"] === "學員段落") await apiPost("/api/turns/save", { id: it.id, "已確認": false });
  else if (it["類型"] === "名字") await apiPost("/api/review/name", { id: it.id, "已確認": false });
  else if (it["類型"] === "重疊") await apiPost("/api/review/overlap", { id: it.id, "已確認": false });
  rvSetState(key, false);
}

function rvGoNext(key) {
  const cards = [...document.querySelectorAll(".rv-card")].filter((c) => c.style.display !== "none");
  const idx = cards.findIndex((c) => c.dataset.key === key);
  const next = cards.slice(idx + 1).find((c) => !c.classList.contains("done")) || cards[idx + 1];
  rvApplyFilter();
  if (!next) return;
  rv.activeKey = next.dataset.key;
  rvMarkActive();
  const field = next.querySelector("textarea") || next;
  field.focus({ preventScroll: true });
  next.scrollIntoView({ block: "start", behavior: "smooth" });
  if (rv.autoplay) rvPlayItem(rvItem(next.dataset.key));
}

// ---------------------------------------------------------------------------
// 刪除段落／局部消音：用 I／O 新增
// ---------------------------------------------------------------------------

async function rvAddRange(kind) {
  if (rv.inMark == null || rv.outMark == null || rv.outMark <= rv.inMark) {
    alert("先在影片上按 I 標起點、按 O 標終點，或直接在「起」「訖」欄輸入時間（終點要在起點之後）");
    return;
  }
  const api = kind === "cut" ? "/api/review/cut" : "/api/review/mute";
  try { await apiPost(api, { start: rv.inMark, end: rv.outMark }); } catch (err) { alert(err.message); return; }
  rv.inMark = rv.outMark = null;
  rvShowIO();
  await rvReload();
}

function rvShowIO() {
  document.getElementById("rv-in").value = rv.inMark != null ? rvFmt(rv.inMark, 2) : "";
  document.getElementById("rv-out").value = rv.outMark != null ? rvFmt(rv.outMark, 2) : "";
  rvRenderZoom();
}

function rvBindIOBoxes() {   // 起訖也可以手動輸入（例如 43:15.5），按 Enter 或離開欄位就記下
  for (const [id, k] of [["rv-in", "inMark"], ["rv-out", "outMark"]]) {
    const el = document.getElementById(id);
    const take = () => {
      if (!el.value.trim()) { rv[k] = null; el.classList.remove("bad"); return; }
      const t = rvParseTime(el.value);
      el.classList.toggle("bad", t == null);
      if (t != null) { rv[k] = t; rvRenderZoom(); }
    };
    el.addEventListener("change", take);
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); take(); } });
  }
}

// ---------------------------------------------------------------------------
// 已自動跳過的重疊、匯出、計時
// ---------------------------------------------------------------------------

function rvRenderSkipped() {
  const list = rv.data["已自動跳過的重疊"] || [];
  const el = document.getElementById("rv-skipped");
  el.innerHTML = `<summary><h2>已自動跳過的重疊（${list.length} 處）</h2></summary>
    <p class="hint">兩位學員之間的重疊、老師講話時學員的短附和、不到 0.05 秒的邊界誤差，自動不處理。覺得要處理的按「救回」。</p>
    <table class="tl">${list.map((o) => `<tr><td>${esc(rvFmt(o.start, 1))}</td><td>${o.length.toFixed(2)} 秒</td><td>${esc(o["原因"] || "")}</td>
      <td><button class="rv-skipplay secondary" data-t="${o.start}">▶</button> <button class="rv-rescue secondary" data-id="${esc(o.id)}">救回</button></td></tr>`).join("")}</table>`;
  el.querySelectorAll(".rv-skipplay").forEach((b) => b.addEventListener("click", () => rvSeek(Number(b.dataset.t) - 2, true)));
  el.querySelectorAll(".rv-rescue").forEach((b) => b.addEventListener("click", async () => {
    await apiPost("/api/review/overlap", { id: b.dataset.id, "救回": true }); await rvReload();
  }));
}

async function rvExport() {
  const msg = document.getElementById("rv-export-msg");
  msg.textContent = "匯出中…";
  try {
    const r = await apiPost("/api/review/export", {});
    const warn = (r["未確認數"] ? `<span class="badge warn">還有 ${r["未確認數"]} 筆沒確認</span> ` : "")
      + ((r["還有本名的地方"] || []).length ? `<span class="badge warn">${r["還有本名的地方"].length} 處還有本名：${esc(r["還有本名的地方"].slice(0, 5).join("、"))}</span> ` : "")
      + ((r["缺參考音"] || []).length ? `<span class="badge warn">還沒選定老師參考音（第 2 步）</span> ` : "");
    msg.innerHTML = `${warn}已匯出：<code>${esc(r["檔案"])}</code>（<a href="/api/review/export.zip">下載</a>）`;
  } catch (err) { msg.innerHTML = `<span class="badge error">失敗</span> ${esc(err.message)}`; }
}

function rvStartTimeTracking() {
  if (rv.timeTimer) clearInterval(rv.timeTimer);
  rv.lastActivity = Date.now();
  rv.timeTimer = setInterval(() => {
    if (currentRouteId() !== "step3") { clearInterval(rv.timeTimer); rv.timeTimer = null; return; }
    const playing = rv.video && !rv.video.paused;
    if (document.hidden || (!playing && Date.now() - rv.lastActivity > 60000)) return;
    apiPost("/api/review/time", { "秒數": 30 }).then((r) => { rv.data["進度"]["已花秒數"] = r["覆核秒數"]; }).catch(() => {});
  }, 30000);
}

// ---------------------------------------------------------------------------
// 快捷鍵
// ---------------------------------------------------------------------------

document.addEventListener("keydown", (e) => {
  if (currentRouteId() !== "step3" || !rv.data) return;
  rv.lastActivity = Date.now();
  const inField = ["TEXTAREA", "INPUT", "SELECT"].includes(e.target.tagName);
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
    const card = e.target.closest ? e.target.closest(".rv-card") : null;
    const key = card ? card.dataset.key : rv.activeKey;
    if (key) { e.preventDefault(); rvConfirm(key, true); }
    return;
  }
  if (e.key === "Escape" && inField) {
    const card = e.target.closest(".rv-card");
    if (card) { e.preventDefault(); rvPlayItem(rvItem(card.dataset.key)); }
    return;
  }
  if (inField || e.metaKey || e.ctrlKey || e.altKey || !rv.video) return;
  const k = e.key.toLowerCase();
  if (e.key === " ") { e.preventDefault(); rv.video.paused ? rv.video.play().catch(() => {}) : rv.video.pause(); }
  else if (k === "j") { rvSeek(rv.video.currentTime - 5); }
  else if (k === "l") { rvSeek(rv.video.currentTime + 5); }
  else if (k === "i") { rv.inMark = rv.video.currentTime; rvShowIO(); }
  else if (k === "o") { rv.outMark = rv.video.currentTime; rvShowIO(); }
});
