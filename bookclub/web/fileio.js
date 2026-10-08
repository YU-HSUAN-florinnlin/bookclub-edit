"use strict";
/* 10-07 檔案進出：總覽的「選影片」（系統內建的選檔視窗）、Windows 的檔案複製進 Ubuntu 的進度、
 * 第 5 步「打開成品資料夾」「複製成品到 Windows 的下載資料夾」「片頭、片尾」（10-08 宇軒：片頭片尾從總覽搬到第 5 步輸出成品這一區）。後端在 bookclub/server.py（/api/pick…、/api/final/outputs…）
 * 與 bookclub/fileio.py。叫不起系統視窗（或按取消）時退回 app.js 的網頁資料夾瀏覽（renderPicker）。 */

// 10-08 宇軒：「先把片頭片尾的選取功能隱藏掉」。第 5 步的片頭片尾選檔先不顯示；程式與 API（/api/pick 的「片頭」「片尾」、
// /api/extras/clear、工作區設定.json）都留著，之後接上成品時把這個改回 true 就會出現。
const SHOW_EXTRAS = false;

const PK_WHAT = { "影片": "選影片", "片頭": "選片頭", "片尾": "選片尾" };
let pkState = null;            // 上一次 /api/picks
let pkProjects = [];           // 總覽的專案清單（判斷「已經有這支影片的專案」）
let pkPollTimer = null;
let pkFallbackFor = { "用途": "影片", "給": "新影片" };   // 網頁資料夾瀏覽現在是在幫誰選

function pkSysName(kind) { return kind === "wsl" ? "Windows" : kind === "mac" ? "Mac" : "電腦"; }

function pkProgressHtml(job) {
  if (!job) return "";
  if (job["狀態"] === "複製中" || job["狀態"] === "等待") {
    return `<div class="pk-copy"><progress max="100" value="${job["百分比"]}"></progress>
      <span>複製進 Ubuntu 中… <b>${job["百分比"]}%</b>（${job["已複製MB"]}／${job["大小MB"]} MB）</span>
      <button class="secondary pk-cancel" data-id="${esc(job.id)}">取消複製</button></div>`;
  }
  if (job["狀態"] === "失敗") return `<div class="pk-copy"><span class="badge error">複製失敗</span> ${esc(job["錯誤"] || "")}（重新選一次）</div>`;
  if (job["狀態"] === "取消") return `<div class="pk-copy muted">已取消複製。</div>`;
  if (job["狀態"] === "沿用") return `<div class="muted">Ubuntu 裡已經有同名、同大小的一份，直接用那一份（沒有再複製）。</div>`;
  return "";
}

function pkJob(id) { return ((pkState || {})["複製"] || []).find((j) => j.id === id) || null; }

function pkFileHtml(v) {
  if (!v) return `<span class="muted">還沒選</span>`;
  const job = v["複製"] ? pkJob(v["複製"]) : null;
  const where = v["路徑"] ? `<div class="muted pk-path-text">${esc(v["路徑"])}</div>` : "";
  const from = v["原本的位置"] ? `<div class="muted">原本在 Windows：${esc(v["原本的位置"])}${v["路徑"] ? "（已經複製進 Ubuntu，上面是複製過來的那一份）" : ""}</div>` : "";
  return `<b>${esc(v["檔名"] || "")}</b>${v["大小MB"] != null ? ` <span class="muted">${v["大小MB"]} MB</span>` : ""}${where}${from}${v["路徑"] ? "" : pkProgressHtml(job)}`;
}

// 總覽「選影片」卡片（app.js renderOverview 放進來）
function pickCardHtml() {
  return `
    <div class="card pick">
      <h2 style="margin-top:0">選影片</h2>
      <p class="muted">按「選影片」會跳出電腦內建的選檔視窗，選好按「開啟」，這裡只留下檔案的位置。按「開始分析」才會在影片旁邊建這支影片的工作資料夾（<code>影片檔名_剪輯工作區</code>）；同一支影片已經做到一半的，會接著做（做完的步驟自動跳過）。</p>
      <div id="pkState"><p class="muted">載入中…</p></div>
      <div id="pkFallback" hidden>
        <p class="hint" id="pkFallbackWhy"></p>
        <div id="picker"></div>
      </div>
    </div>`;
}

function pkRowHtml(purpose, v, target, note) {
  const has = !!v;
  // 10-08 宇軒：按鈕放在「影片」字樣右邊、路徑左邊（以前在最右邊，版面寬一點就跑出去看不到）
  return `<tr><td class="pk-what">${esc(purpose)}${note ? `<div class="muted">${esc(note)}</div>` : ""}</td>
    <td class="pk-btns"><button class="${purpose === "影片" ? "" : "secondary"} pk-pick" data-p="${purpose}" data-g="${target}">${has ? "換一支" : PK_WHAT[purpose]}</button>
      ${has && purpose !== "影片" ? `<button class="ghost pk-forget" data-p="${purpose}" data-g="${target}">不用了</button>` : ""}</td>
    <td class="pk-file">${pkFileHtml(v)}</td></tr>`;
}

function pkRenderState() {
  const box = document.getElementById("pkState");
  if (!box || !pkState) return;
  const sel = pkState["選了"] || {};
  const v = sel["影片"];
  let startHtml = "";
  if (v && v["路徑"]) {
    const dir = v["路徑"].replace(/\/[^/]*$/, "");
    const stem = (v["檔名"] || "").replace(/\.[^.]+$/, "");
    const target = `${dir}/${stem}_剪輯工作區`;
    const existing = (pkProjects || []).find((p) => p["路徑"] === target);
    startHtml = `<div class="pk-sel"><div class="muted">${existing ? `已經有這支影片的專案（${esc(existing["分析完成"] ? "分析完成" : "還沒分析完")}），按開始分析會接著做。` : `會在影片旁邊建立資料夾：${esc(stem)}_剪輯工作區/`}</div>
      <button id="pkStart">開始分析</button> <span id="pkMsg"></span></div>`;
  } else if (v) {
    startHtml = `<div class="pk-sel muted">影片複製進 Ubuntu 之後才能開始分析。</div>`;
  }
  box.innerHTML = `
    <table class="kv pk-table"><tbody>
      ${pkRowHtml("影片", v, "新影片")}
    </tbody></table>
    ${startHtml}`;
  pkBind(box);
  const st = document.getElementById("pkStart");
  if (st) st.addEventListener("click", startPicked);
}

// 第 5 步「輸出成品」這一區的片頭、片尾（10-08 宇軒：要最終輸出的時候才選；可以換、可以拿掉，輸出可以做很多次）
function feRenderExtras() {
  const box = document.getElementById("feExtras");
  if (!box || !pkState) return;
  if (!SHOW_EXTRAS) { box.innerHTML = ""; return; }
  const cur = pkState["目前的專案"];
  if (!cur) { box.innerHTML = ""; return; }
  if (cur["錯誤"]) { box.innerHTML = `<p class="badge error">${esc(cur["錯誤"])}</p>`; return; }
  // 每一格只看最後一個工作（含成功的），舊的「複製失敗」不會一直掛著
  const jobs = (pkState["複製"] || []).filter((j) => j["給"] === "目前");
  const row = (p) => {
    const v = cur[p];
    const job = jobs.filter((j) => j["用途"] === p).slice(-1)[0];
    const missing = v && v["檔案還在"] === false ? `<div class="badge error">找不到這個檔案了（搬走或刪掉了？），重新選一次</div>` : "";
    return `<tr><td class="pk-what">${p}<div class="muted">可以不選</div></td>
      <td class="pk-btns"><button class="secondary pk-pick" data-p="${p}" data-g="目前">${v ? "換一支" : PK_WHAT[p]}</button>
        ${v ? `<button class="ghost pk-clear" data-p="${p}">拿掉</button>` : ""}</td>
      <td class="pk-file">${v ? pkFileHtml(v) : `<span class="muted">沒有</span>`}${missing}${job && ["複製中", "等待", "失敗"].includes(job["狀態"]) ? pkProgressHtml(job) : ""}</td></tr>`;
  };
  box.innerHTML = `<h3 class="fe-title">片頭、片尾</h3>
    <p class="hint fe-note">這一版只先記錄選了哪一支（存在這個工作資料夾的 <code>工作區設定.json</code>），按「輸出成品」時<b>還不會接上</b>。選錯了可以換、可以拿掉；「輸出成品」可以按很多次，每次都會產生一支新的最終成品，舊的不會被蓋掉。</p>
    <table class="kv pk-table"><tbody>${row("片頭")}${row("片尾")}</tbody></table>`;
  pkBind(box);
}

function pkBind(box) {
  box.querySelectorAll(".pk-pick").forEach((b) => b.addEventListener("click", () => pkPick(b.dataset.p, b.dataset.g, b)));
  box.querySelectorAll(".pk-forget").forEach((b) => b.addEventListener("click", async () => {
    await apiPost("/api/pick/forget", { "用途": b.dataset.p });
    await pkRefresh();
  }));
  box.querySelectorAll(".pk-clear").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`拿掉這個專案的${b.dataset.p}？（只是不用它，影片檔本身不會刪）`)) return;
    await apiPost("/api/extras/clear", { "用途": b.dataset.p });
    await pkRefresh();
  }));
  box.querySelectorAll(".pk-cancel").forEach((b) => b.addEventListener("click", async () => {
    await apiPost("/api/copy/cancel", { id: b.dataset.id });
    await pkRefresh();
  }));
}

async function pkRefresh() {
  try { pkState = await apiGet("/api/picks"); } catch (e) {
    const box = document.getElementById("pkState");
    if (box) box.innerHTML = `<p class="badge error">${esc(e.message)}</p>`;
    return;
  }
  pkRenderState();
  feRenderExtras();
  const copying = (pkState["複製"] || []).some((j) => j["狀態"] === "複製中" || j["狀態"] === "等待");
  clearTimeout(pkPollTimer);
  const shown = () => document.getElementById("pkState") || document.getElementById("feExtras");
  if (copying) pkPollTimer = setTimeout(() => { if (shown()) pkRefresh(); }, 1000);
}

async function bindPickCard(projectList) {
  pkProjects = projectList || [];
  await pkRefresh();
}

function pkShowFallback(why, purpose, target) {
  pkFallbackFor = { "用途": purpose, "給": target };
  const fb = document.getElementById("pkFallback");
  if (!fb) return;
  fb.hidden = false;
  const label = purpose === "影片" ? "影片" : purpose;
  document.getElementById("pkFallbackWhy").textContent = `${why}（現在在幫「${label}」選）`;
  let start = null;
  try { start = localStorage.getItem("pick-dir"); } catch (e) { /* 沒有 localStorage 也沒關係 */ }
  renderPicker(start, pkProjects);
  fb.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

async function pkPick(purpose, target, btn) {
  const old = btn.textContent;
  btn.disabled = true;
  // 10-08：按下去馬上換字，讓人知道有反應；過 1.5 秒還沒選好才提醒視窗可能躲在後面
  btn.textContent = "正在打開選檔視窗…";
  const later = setTimeout(() => { btn.textContent = "選檔視窗開著…（沒看到的話，看一下瀏覽器後面或工作列）"; }, 1500);
  let r;
  try {
    r = await apiPost("/api/pick", { "用途": purpose, "給": target }, { quiet: true });
  } catch (e) {
    clearTimeout(later);
    btn.disabled = false; btn.textContent = old;
    alert(e.message);
    return;
  }
  clearTimeout(later);
  btn.disabled = false; btn.textContent = old;
  if (r["退回網頁"]) { pkShowFallback(r["說明"], purpose, target); return; }
  const fb = document.getElementById("pkFallback");
  if (fb) fb.hidden = true;
  await pkRefresh();
}

// app.js renderPicker 的網頁資料夾瀏覽點了一支影片
async function pickBrowsed(path) {
  try {
    await apiPost("/api/pick/browsed", { ...pkFallbackFor, "路徑": path }, { quiet: true });
  } catch (e) { alert(e.message); return; }
  const fb = document.getElementById("pkFallback");
  if (fb) fb.hidden = true;   // 選好了就收起來（要再選就再按一次按鈕）
  await pkRefresh();
}

// ---------------------------------------------------------------------------
// 第 5 步：打開成品資料夾、複製成品到 Windows 的下載資料夾、片頭片尾（finalcheck.js 只放一個容器、呼叫 fileOutInit）
// ---------------------------------------------------------------------------

let foPollTimer = null;

async function fileOutInit(el) {
  if (!el) return;
  if (!el.querySelector("#foMain")) {   // 第一次：排好三塊（按鈕列每秒重畫時，片頭片尾與資料夾瀏覽不會被洗掉）
    el.innerHTML = `<div id="foMain"></div><div id="feExtras"></div>
      <div id="pkFallback" hidden><p class="hint" id="pkFallbackWhy"></p><div id="picker"></div></div>`;
    if (SHOW_EXTRAS) pkRefresh();
  }
  const main = el.querySelector("#foMain");
  let d;
  try { d = await apiGet("/api/final/outputs"); } catch (e) { main.innerHTML = ""; return; }
  if (!document.body.contains(el)) return;
  const openName = d["系統"] === "wsl" ? "在檔案總管打開成品資料夾" : d["系統"] === "mac" ? "在 Finder 打開成品資料夾" : "打開成品資料夾";
  const job = (d["複製"] || [])[0];
  const running = job && (job["狀態"] === "複製中" || job["狀態"] === "等待");
  let jobHtml = "";
  if (job) {
    if (running) jobHtml = `<progress max="100" value="${job["百分比"]}"></progress> 複製到 Windows 的下載資料夾… <b>${job["百分比"]}%</b>（${job["已複製MB"]}／${job["大小MB"]} MB）`;
    else if (job["狀態"] === "完成") jobHtml = `<span class="badge done">已複製</span> Windows 的下載資料夾：<code>${esc(job["檔名"])}</code>`;
    else if (job["狀態"] === "失敗") jobHtml = `<span class="badge error">複製失敗</span> ${esc(job["錯誤"] || "")}`;
  }
  main.innerHTML = `<div class="fo-row">
      <button class="secondary" id="fo-open" ${d["成品"] ? "" : "disabled"}>${openName}</button>
      ${d["可以複製到Windows"] ? `<button class="secondary" id="fo-win" ${d["成品"] && !running ? "" : "disabled"}>複製成品到 Windows 的下載資料夾</button>` : ""}
      ${d["成品"] ? `<span class="muted">${esc(d["成品是"] || "")}：${esc(d["成品"])}</span>` : ""}
      <span id="fo-msg" role="status">${jobHtml}</span></div>`;
  const msg = main.querySelector("#fo-msg");
  main.querySelector("#fo-open").addEventListener("click", async () => {
    try { const r = await apiPost("/api/final/reveal", {}, { quiet: true }); msg.textContent = r["說明"] || ""; }
    catch (e) { msg.innerHTML = `<span class="badge error">打不開</span> ${esc(e.message)}`; }
  });
  const win = main.querySelector("#fo-win");
  if (win) win.addEventListener("click", async () => {
    win.disabled = true;
    try { await apiPost("/api/final/to_windows", {}, { quiet: true }); }
    catch (e) { msg.innerHTML = `<span class="badge error">沒有複製</span> ${esc(e.message)}`; win.disabled = false; return; }
    fileOutInit(el);
  });
  clearTimeout(foPollTimer);
  if (running) foPollTimer = setTimeout(() => { if (document.body.contains(el)) fileOutInit(el); }, 1000);
}
