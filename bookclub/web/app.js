"use strict";

/* 讀書會剪輯工具｜前端骨架。原生 HTML/CSS/JS，不用打包工具、不用 CDN 套件。
 * 左側步驟列 0～6 步（09-25 宇軒：原本第 3、4 步合成第 3 步「覆核工作台」，後面往前）；
 * 還沒做的步驟顯示「還沒做」的說明頁，不假裝可用。路由用 URL hash（#step1、#step2…）。
 * 第 3 步覆核工作台在 review.js。 */

const STEP_DEFS = [
  { id: "overview", num: null, title: "總覽", real: true },
  { id: "step0", num: 0, title: "初始化設定", real: true },
  { id: "step1", num: 1, title: "影片分析", real: true },
  { id: "step2", num: 2, title: "挑選老師參考聲音片段", real: true },
  { id: "step3", num: 3, title: "覆核工作台", real: true },
  { id: "step4", num: 4, title: "AI 執行", real: false },
  { id: "step5", num: 5, title: "成品逐筆覆核", real: false },
  { id: "step6", num: 6, title: "整片檢查", real: false },
];

const contentEl = document.getElementById("content");
const stepsEl = document.getElementById("steps");
const videoInfoEl = document.getElementById("videoinfo");

let statusPollTimer = null;

// ---------------------------------------------------------------------------
// API 小工具
// ---------------------------------------------------------------------------

async function apiGet(path) {
  const res = await fetch(path);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${path} 失敗（${res.status}）`);
  return data;
}

async function apiPost(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${path} 失敗（${res.status}）`);
  return data;
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}

function fmtElapsed(s) {
  if (s == null) return "—";
  return `${s} 秒`;
}

// ---------------------------------------------------------------------------
// 側邊欄
// ---------------------------------------------------------------------------

function currentRouteId() {
  const h = (location.hash || "#overview").slice(1);
  return STEP_DEFS.some((s) => s.id === h) ? h : "overview";
}

let lastState = null;

async function renderSidebar() {
  let state = null;
  try {
    state = await apiGet("/api/state");
  } catch (e) {
    videoInfoEl.textContent = `讀取工作區狀態失敗：${e.message}`;
  }
  lastState = state;

  if (state && state["沒有專案"]) {
    videoInfoEl.textContent = "還沒選影片（到總覽選）";
  } else if (state) {
    const v = state.video || {};
    const name = v.name || "（還不知道影片路徑）";
    const dur = v.duration_hms ? `　長度 ${v.duration_hms}` : "";
    videoInfoEl.innerHTML = `${esc(name)}${esc(dur)}<br>${esc(state.workdir || "")}`;
  }

  const active = currentRouteId();
  stepsEl.innerHTML = STEP_DEFS.map((s) => {
    let badgeClass = "notyet";
    let badgeText = "·";
    if (s.real && state && !state["沒有專案"]) {
      const done = stepDoneFromState(s.num, state);
      badgeClass = done ? "done" : "";
      badgeText = done ? "✓" : "·";
    } else if (!s.real) {
      badgeClass = "notyet";
      badgeText = "…";
    }
    const label = s.num === null ? s.title : `${s.num}　${s.title}`;
    return `<li><a href="#${s.id}" class="${s.id === active ? "active" : ""}">
      <span class="step-badge ${badgeClass}">${badgeText}</span>${esc(label)}
    </a></li>`;
  }).join("");
}

function stepDoneFromState(num, state) {
  const sub = state.substeps || {};
  if (num === 0) return true;
  if (num === 1) return !!(sub["轉文字"] && sub["轉文字"].done);
  if (num === 2) return !!(sub["挑參考音"] && sub["挑參考音"].done);
  if (num === 3) return !!(state.proofread && state.proofread.done);
  return false;
}

// ---------------------------------------------------------------------------
// 路由
// ---------------------------------------------------------------------------

async function render() {
  await renderSidebar();
  const id = currentRouteId();
  stopStatusPoll();
  contentEl.classList.remove("wide");

  try {
    if (id === "overview") return await renderOverview();
    if (id !== "step0" && lastState && lastState["沒有專案"]) {
      contentEl.innerHTML = `<div class="notyet-card">還沒選影片。到 <a href="#overview">總覽</a> 選一支影片，或切換到已有的專案。</div>`;
      return;
    }
    if (id === "step1") return await renderStep1();
    if (id === "step2") return await renderStep2();
    if (id === "step3") return await renderReview();
    if (id === "step0") return await renderProfile();
    const def = STEP_DEFS.find((s) => s.id === id);
    return renderNotYet(def);
  } catch (e) {
    contentEl.innerHTML = `<div class="card"><strong>載入失敗：</strong>${esc(e.message)}</div>`;
  }
}

window.addEventListener("hashchange", render);
window.addEventListener("DOMContentLoaded", render);

function stopStatusPoll() {
  if (statusPollTimer) {
    clearInterval(statusPollTimer);
    statusPollTimer = null;
  }
}

// ---------------------------------------------------------------------------
// 還沒做的步驟
// ---------------------------------------------------------------------------

function renderNotYet(def) {
  const notes = {
    step4: "覆核工作台確認完、匯出覆核結果之後，交給夥伴的電腦執行：bookclub review import → bookclub gen names → bookclub render audio。網頁版還沒做。",
  };
  contentEl.innerHTML = `
    <h1>${esc(def.num)}　${esc(def.title)}</h1>
    <div class="notyet-card">${esc(notes[def.id] || "這一步還沒做，之後的階段才會做。")}</div>
  `;
}

// ---------------------------------------------------------------------------
// 第 0 步：初始化設定（跨專案共用；名冊只顯示代號與筆數，不顯示本名）
// ---------------------------------------------------------------------------

async function renderProfile() {
  contentEl.innerHTML = "<p>載入中…</p>";
  const d = await apiGet("/api/profile");
  const desc = {
    "名冊.csv": "學員本名、其他寫法、英文代號、性別。只要一份、不分期：同一支影片裡同一人同一代號就好",
    "敏感詞.csv": "公司名、地名等要換掉的詞，與替代詞",
    "名字排除清單.csv": "確認不是名字的詞（地名、疊字誤抓），之後自動不列入候選",
    "發音對照表.csv": "老師 AI 聲音念偏的詞，換成接近台灣口音的寫法",
  };
  const rows = Object.entries(d["檔案"]).map(([name, f]) => `<tr>
      <td><b>${esc(name.replace(".csv", ""))}</b><div class="muted">${esc(desc[name] || "")}</div>
        ${name === "名冊.csv" && (f["代號"] || []).length ? `<div class="muted">代號：${esc(f["代號"].join("、"))}${f["沒有代號的筆數"] ? `（另有 ${f["沒有代號的筆數"]} 筆還沒有代號）` : ""}</div>` : ""}</td>
      <td>${f["有檔案"] ? `${f["筆數"]} 筆` : "還沒有檔案"}</td>
      <td>${esc(f["最後修改"] || "—")}</td></tr>`).join("");
  const st = d["settings.toml"];
  contentEl.innerHTML = `
    <h1>0　初始化設定</h1>
    <p class="muted">這些設定所有影片共用，放在 <code>${esc(d["資料夾"])}</code>。AI 之後發現新的念偏詞、排除詞會照舊寫進這裡；要給協作夥伴，用下面的設定包。</p>
    <div class="card"><table class="kv prof">
      <thead><tr><th style="text-align:left">項目</th><th style="text-align:left">筆數</th><th style="text-align:left">最後修改</th></tr></thead>
      <tbody>${rows}
        <tr><td><b>settings.toml</b><div class="muted">伺服器埠號、Claude 模型、門檻值</div></td><td>${st["有檔案"] ? "有" : "沒有（用內建預設值）"}</td><td>${esc(st["最後修改"] || "—")}</td></tr>
        <tr><td><b>匿名聲線</b><div class="muted">學員重念用的 AI 聲音素材（可商用的開放授權）</div></td><td>${esc(d["匿名聲線"]["狀態"])}</td><td>—</td></tr>
      </tbody></table></div>
    <h2>設定包（給協作夥伴）</h2>
    <div class="card">
      <p>匯出：名冊、敏感詞、名字排除清單、發音對照表、settings.toml 打包成一個 zip。名冊含學員本名，只傳給協作夥伴。</p>
      <p><a href="/api/profile/export.zip"><button>匯出設定包</button></a></p>
      <p style="margin-top:18px">匯入：每個清單以第一欄當鑰匙，新的加進去、已經有的不動；同一鑰匙內容不同，保留這台電腦的並列出衝突。</p>
      <p><input type="file" id="profFile" accept=".zip"> <button id="profImport" class="secondary">匯入設定包</button></p>
      <div id="profMsg"></div>
    </div>`;
  document.getElementById("profImport").addEventListener("click", async () => {
    const f = document.getElementById("profFile").files[0];
    const msg = document.getElementById("profMsg");
    if (!f) { msg.innerHTML = `<p class="muted">先選設定包 zip。</p>`; return; }
    msg.textContent = "匯入中…";
    try {
      const res = await fetch("/api/profile/import", { method: "POST", headers: { "Content-Type": "application/zip" }, body: f });
      const r = await res.json();
      if (!res.ok) throw new Error(r.error || "匯入失敗");
      const lines = Object.entries(r["檔案"]).map(([name, v]) => `<li>${esc(name)}：新增 ${v["新增"]} 筆${v["衝突"].length
        ? `，衝突 ${v["衝突"].length} 筆（保留這台電腦的）：${esc(v["衝突"].map((c) => c["鑰匙"]).join("、"))}` : ""}</li>`).join("");
      msg.innerHTML = `<p><span class="badge done">匯入完成</span> 新增 ${r["新增"]} 筆、衝突 ${r["衝突數"]} 筆</p><ul>${lines}</ul>`;
      const keep = msg.innerHTML;
      await renderProfile();
      document.getElementById("profMsg").innerHTML = keep;
    } catch (e) { msg.innerHTML = `<span class="badge error">失敗</span> ${esc(e.message)}`; }
  });
}

// ---------------------------------------------------------------------------
// 總覽
// ---------------------------------------------------------------------------

async function renderOverview() {
  contentEl.innerHTML = "<p>載入中…</p>";
  const [state, projects] = await Promise.all([apiGet("/api/state"), apiGet("/api/projects")]);
  const hasProject = !state["沒有專案"];
  let current = "";
  if (hasProject) {
    const sub = state.substeps || {};
    const order = ["轉文字", "認老師", "找重疊", "挑參考音", "找名字", "段落分析"];
    const done = order.filter((k) => (sub[k] || {}).done).length;
    current = `
      <h2>目前的專案</h2>
      <div class="card">
        <table class="kv">
          <tr><td>影片</td><td>${esc(state.video.name || "（不知道）")}</td></tr>
          <tr><td>影片長度</td><td>${esc(state.video.duration_hms || "—")}</td></tr>
          <tr><td>工作區</td><td>${esc(state.workdir)}</td></tr>
          <tr><td>影片分析</td><td>${done}／${order.length} 個子步驟完成${state["總耗時_s"] ? `，花了 ${esc(fmtElapsed(state["總耗時_s"]))}` : ""}（<a href="#step1">看進度</a>）</td></tr>
        </table>
      </div>`;
  }
  const list = projects["專案"] || [];
  const rows = list.map((p) => `<tr class="${p["路徑"] === projects["目前"] ? "cur" : ""}">
      <td><b>${esc(p["名稱"])}</b>${p["路徑"] === projects["目前"] ? "　（目前）" : ""}<div class="muted">${p["舊位置"] ? "舊位置（讀書會剪輯資料／工作區）" : esc(p["位置"])}</div></td>
      <td>${esc(p["長度"] || "—")}</td>
      <td>${p["分析完成"] ? "分析完成" : "還沒分析完"}${p["有覆核"] ? "、覆核中" : ""}</td>
      <td>${esc(p["修改時間"])}</td>
      <td>${p["路徑"] === projects["目前"] ? "" : `<button class="secondary pj-switch" data-path="${esc(p["路徑"])}">切換</button>`}</td></tr>`).join("");
  contentEl.innerHTML = `
    <h1>總覽</h1>
    ${projects["轉文字金鑰"] === false ? `<div class="hint">這個網頁伺服器讀不到 Groq 金鑰，新影片沒辦法轉文字（已經轉好文字的專案不受影響）。關掉這個伺服器，改用雙擊「啟動.command」重開。</div>` : ""}
    <div class="card pick">
      <h2 style="margin-top:0">選影片</h2>
      <p class="muted">選一支影片，按「開始分析」才會在影片旁邊建這支影片的工作資料夾（<code>影片檔名_剪輯工作區</code>）。同一支影片已經做到一半的，會接著做（做完的步驟自動跳過）。</p>
      <div id="pickerSel"></div>
      <div id="picker"></div>
    </div>
    ${current}
    <h2>已有的專案（${list.length}）</h2>
    <div class="card">${list.length ? `<table class="kv pj-list"><tbody>${rows}</tbody></table>` : `<p class="muted">還沒有專案。</p>`}</div>`;
  contentEl.querySelectorAll(".pj-switch").forEach((b) => b.addEventListener("click", async () => {
    try { await apiPost("/api/projects/switch", { "路徑": b.dataset.path }); } catch (e) { alert(e.message); return; }
    await render();
  }));
  let start = null;
  try { start = localStorage.getItem("pick-dir"); } catch (e) { /* 沒有 localStorage 也沒關係 */ }
  await renderPicker(start, list);
}

let pickedVideo = null;

async function renderPicker(path, projectList) {
  const box = document.getElementById("picker");
  let d;
  try {
    d = await apiGet(`/api/browse${path ? `?path=${encodeURIComponent(path)}` : ""}`);
  } catch (e) {
    if (path) return renderPicker(null, projectList);
    box.innerHTML = `<p class="badge error">${esc(e.message)}</p>`;
    return;
  }
  try { localStorage.setItem("pick-dir", d["路徑"]); } catch (e) { /* 同上 */ }
  const rel = d["路徑"] === d["家目錄"] ? "家目錄" : d["路徑"].replace(d["家目錄"] + "/", "");
  box.innerHTML = `
    <div class="pk-path"><b>${esc(rel)}</b>${d["上一層"] ? ` <button class="secondary pk-up">回上一層</button>` : ""}</div>
    <ul class="pk-list">
      ${d["資料夾"].map((n) => `<li><button class="linkish pk-dir" data-name="${esc(n)}">📁 ${esc(n)}</button></li>`).join("")}
      ${d["影片"].map((v) => `<li><button class="linkish pk-video" data-name="${esc(v["名稱"])}">🎬 ${esc(v["名稱"])}</button> <span class="muted">${v["大小MB"]} MB</span></li>`).join("")}
      ${!d["資料夾"].length && !d["影片"].length ? `<li class="muted">這個資料夾沒有子資料夾或影片。</li>` : ""}
    </ul>`;
  const up = box.querySelector(".pk-up");
  if (up) up.addEventListener("click", () => renderPicker(d["上一層"], projectList));
  box.querySelectorAll(".pk-dir").forEach((b) => b.addEventListener("click", () => renderPicker(`${d["路徑"]}/${b.dataset.name}`, projectList)));
  box.querySelectorAll(".pk-video").forEach((b) => b.addEventListener("click", () => {
    pickedVideo = `${d["路徑"]}/${b.dataset.name}`;
    const stem = b.dataset.name.replace(/\.[^.]+$/, "");
    const target = `${d["路徑"]}/${stem}_剪輯工作區`;
    const existing = (projectList || []).find((p) => p["路徑"] === target);
    document.getElementById("pickerSel").innerHTML = `
      <div class="pk-sel"><div>選了：<b>${esc(b.dataset.name)}</b></div>
        <div class="muted">${existing ? `已經有這支影片的專案（${esc(existing["分析完成"] ? "分析完成" : "還沒分析完")}），按開始分析會接著做。` : `會在影片旁邊建立資料夾：${esc(stem)}_剪輯工作區/`}</div>
        <button id="pkStart">開始分析</button> <span id="pkMsg"></span></div>`;
    document.getElementById("pkStart").addEventListener("click", startPicked);
  }));
}

async function startPicked() {
  const msg = document.getElementById("pkMsg");
  msg.textContent = "開始中…";
  try {
    const r = await apiPost("/api/projects/start", { "影片": pickedVideo });
    msg.textContent = r["接著做"] ? "接著做，已經完成的步驟會跳過" : "已建立專案";
  } catch (e) { msg.innerHTML = `<span class="badge error">失敗</span> ${esc(e.message)}`; return; }
  location.hash = "#step1";
}

// ---------------------------------------------------------------------------
// 第 1 步：影片分析
// ---------------------------------------------------------------------------

async function renderStep1() {
  contentEl.innerHTML = "<p>載入中…</p>";
  await renderStep1Body();
}

async function renderStep1Body() {
  const [state, status] = await Promise.all([
    apiGet("/api/state"),
    apiGet("/api/run/status").catch(() => null),
  ]);
  const sub = state.substeps || {};
  const order = [
    ["轉文字", "1"], ["認老師", "2"], ["找重疊", "3"], ["挑參考音", "4"], ["找名字", "5"], ["段落分析", "6"],
  ];
  const rows = order.map(([k]) => {
    const s = sub[k] || {};
    return `<tr>
      <td>${esc(k)}</td>
      <td><span class="badge ${s.done ? "done" : ""}">${s.done ? "已完成" : "未完成"}</span></td>
      <td>${esc(fmtElapsed(s.elapsed_s))}</td>
    </tr>`;
  }).join("");

  const running = status && status.running;
  const errorMsg = status && status.error;
  const messages = (status && status.messages) || [];

  contentEl.innerHTML = `
    <h1>1　影片分析</h1>
    <div class="card">
      <table class="kv">
        <thead><tr><th style="text-align:left">子步驟</th><th style="text-align:left">狀態</th>
          <th style="text-align:left">耗時</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="card">
      <button id="btnAnalyze" ${running ? "disabled" : ""}>${running ? "分析執行中…" : "開始分析"}</button>
      ${errorMsg ? `<p><span class="badge error">失敗</span> ${esc(errorMsg)}</p>` : ""}
      <div class="log" id="runLog">${messages.map(esc).join("\n") || "（還沒有訊息）"}</div>
    </div>
  `;

  document.getElementById("btnAnalyze").addEventListener("click", startAnalyze);

  if (running) startStatusPoll();
}

async function startAnalyze() {
  try {
    await apiPost("/api/run/analyze", {});
  } catch (e) {
    alert(`無法開始分析：${e.message}`);
    return;
  }
  await renderStep1Body();
}

function startStatusPoll() {
  stopStatusPoll();
  statusPollTimer = setInterval(async () => {
    if (currentRouteId() !== "step1") {
      stopStatusPoll();
      return;
    }
    try {
      const status = await apiGet("/api/run/status");
      const logEl = document.getElementById("runLog");
      if (logEl) logEl.textContent = (status.messages || []).join("\n") || "（還沒有訊息）";
      if (!status.running) {
        stopStatusPoll();
        await renderStep1Body();
        await renderSidebar();
      }
    } catch (e) {
      // 輪詢失敗不中斷，下一次再試
    }
  }, 2000);
}

// ---------------------------------------------------------------------------
// 第 2 步：挑選老師參考聲音片段（只顯示第一名，換一段往下走）
// ---------------------------------------------------------------------------

let refsCache = null;
let refsPointer = 0;

async function renderStep2() {
  contentEl.innerHTML = "<p>載入中…</p>";
  refsCache = await apiGet("/api/refs");
  refsPointer = 0;
  renderStep2Body();
}

function renderStep2Body() {
  const list = (refsCache && refsCache.candidates) || [];
  if (list.length === 0) {
    contentEl.innerHTML = `
      <h1>2　挑選老師參考聲音片段</h1>
      <div class="notyet-card">還沒有參考音候選——先在第 1 步按「開始分析」跑完，或確認 bookclub run analyze 有正常結束。</div>
    `;
    return;
  }
  if (refsPointer >= list.length) refsPointer = list.length - 1;
  const c = list[refsPointer];

  contentEl.innerHTML = `
    <h1>2　挑選老師參考聲音片段</h1>
    <div class="hint">參考音裡如果有雜音、笑聲、咳嗽，或別人的回應（例如「嗯」「對」），都不適合，請按「換一段」。</div>
    <div class="card">
      <p>第 ${refsPointer + 1} 段／共 ${list.length} 段　｜　原片時間：${esc(c["原片時間"])}　｜　長度：${esc(c["長度秒"])} 秒</p>
      ${c["音檔網址"] ? `<audio controls preload="none" src="${esc(c["音檔網址"])}"></audio>` : `<div class="namecard missing">（音檔缺失）</div>`}
      <p style="margin-top:12px;">逐字稿（可以直接修改）：</p>
      <textarea id="refText" rows="4">${esc(c.transcript)}</textarea>
      <div style="margin-top:14px; display:flex; gap:10px; flex-wrap:wrap;">
        <button id="btnNext" class="secondary" ${refsPointer >= list.length - 1 ? "disabled" : ""}>這段有雜音、笑聲或別人的聲音，換一段</button>
        <button id="btnUse">用這段</button>
      </div>
      <p id="refMsg"></p>
    </div>
  `;

  document.getElementById("btnNext").addEventListener("click", () => {
    if (refsPointer < list.length - 1) {
      refsPointer += 1;
      renderStep2Body();
    }
  });

  document.getElementById("btnUse").addEventListener("click", async () => {
    const msgEl = document.getElementById("refMsg");
    const text = document.getElementById("refText").value;
    try {
      await apiPost("/api/refs/use", { rank: c.rank, transcript: text });
      msgEl.innerHTML = `<span class="badge done">已存檔</span> 已存成 ref.wav／ref.txt（第 ${c.rank} 名）`;
      await renderSidebar();
    } catch (e) {
      msgEl.innerHTML = `<span class="badge error">失敗</span> ${esc(e.message)}`;
    }
  });
}
