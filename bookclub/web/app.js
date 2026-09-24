"use strict";

/* 讀書會剪輯工具｜前端骨架。原生 HTML/CSS/JS，不用打包工具、不用 CDN 套件。
 * 左側步驟列固定 0～7 步；這一輪只有第 1、2、4 步是真的可用頁面，其餘顯示
 * 「還沒做」的說明頁，不假裝可用。路由用 URL hash（#step1、#step2…）。 */

const STEP_DEFS = [
  { id: "overview", num: null, title: "總覽", real: true },
  { id: "step0", num: 0, title: "一次性設定", real: false },
  { id: "step1", num: 1, title: "轉文字與分析", real: true },
  { id: "step2", num: 2, title: "聲音分群與參考音", real: true },
  { id: "step3", num: 3, title: "學員逐字稿校對", real: false },
  { id: "step4", num: 4, title: "標記覆核", real: true },
  { id: "step5", num: 5, title: "AI 執行", real: false },
  { id: "step6", num: 6, title: "成品逐筆覆核", real: false },
  { id: "step7", num: 7, title: "整片檢查", real: false },
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

async function renderSidebar() {
  let state = null;
  try {
    state = await apiGet("/api/state");
  } catch (e) {
    videoInfoEl.textContent = `讀取工作區狀態失敗：${e.message}`;
  }

  if (state) {
    const v = state.video || {};
    const name = v.name || "（還不知道影片路徑）";
    const dur = v.duration_hms ? `　長度 ${v.duration_hms}` : "";
    videoInfoEl.innerHTML = `${esc(name)}${esc(dur)}<br>${esc(state.workdir || "")}`;
  }

  const active = currentRouteId();
  stepsEl.innerHTML = STEP_DEFS.map((s) => {
    let badgeClass = "notyet";
    let badgeText = "·";
    if (s.real && state) {
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
  if (num === 1) return !!(sub["轉文字"] && sub["轉文字"].done);
  if (num === 2) return !!(sub["挑參考音"] && sub["挑參考音"].done);
  if (num === 4) return !!(sub["找名字"] && sub["找名字"].done);
  return false;
}

// ---------------------------------------------------------------------------
// 路由
// ---------------------------------------------------------------------------

async function render() {
  await renderSidebar();
  const id = currentRouteId();
  stopStatusPoll();

  try {
    if (id === "overview") return await renderOverview();
    if (id === "step1") return await renderStep1();
    if (id === "step2") return await renderStep2();
    if (id === "step4") return await renderStep4();
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
  contentEl.innerHTML = `
    <h1>${esc(def.num)}　${esc(def.title)}</h1>
    <div class="notyet-card">這一步還沒做，之後的階段才會做。</div>
  `;
}

// ---------------------------------------------------------------------------
// 總覽
// ---------------------------------------------------------------------------

async function renderOverview() {
  contentEl.innerHTML = "<p>載入中…</p>";
  const state = await apiGet("/api/state");
  const sub = state.substeps || {};
  const order = ["轉文字", "認老師", "找重疊", "挑參考音", "找名字"];

  const rows = order.map((k) => {
    const s = sub[k] || {};
    const statClass = s.done ? "done" : "";
    return `<tr>
      <td>${esc(k)}</td>
      <td><span class="badge ${statClass}">${s.done ? "已完成" : "未完成"}</span></td>
      <td>${esc(fmtElapsed(s.elapsed_s))}</td>
      <td>${esc(JSON.stringify(s["統計"] || {}))}</td>
    </tr>`;
  }).join("");

  contentEl.innerHTML = `
    <h1>總覽</h1>
    <div class="card">
      <table class="kv">
        <tr><td>影片</td><td>${esc(state.video.name || "（不知道）")}</td></tr>
        <tr><td>影片長度</td><td>${esc(state.video.duration_hms || "—")}</td></tr>
        <tr><td>工作區</td><td>${esc(state.workdir)}</td></tr>
        <tr><td>總耗時</td><td>${esc(fmtElapsed(state["總耗時_s"]))}</td></tr>
      </table>
    </div>
    <h2>各步驟狀態</h2>
    <div class="card">
      <table class="kv">
        <thead><tr><th style="text-align:left">子步驟</th><th style="text-align:left">狀態</th>
          <th style="text-align:left">耗時</th><th style="text-align:left">統計</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// 第 1 步：轉文字與分析
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
    ["轉文字", "1"], ["認老師", "2"], ["找重疊", "3"], ["挑參考音", "4"], ["找名字", "5"],
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
    <h1>1　轉文字與分析</h1>
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
// 第 2 步：聲音分群與參考音（只顯示第一名，換一段往下走）
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
      <h1>2　聲音分群與參考音</h1>
      <div class="notyet-card">還沒有參考音候選——先在第 1 步按「開始分析」跑完，或確認 bookclub run analyze 有正常結束。</div>
    `;
    return;
  }
  if (refsPointer >= list.length) refsPointer = list.length - 1;
  const c = list[refsPointer];

  contentEl.innerHTML = `
    <h1>2　聲音分群與參考音</h1>
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

// ---------------------------------------------------------------------------
// 第 4 步：名字覆核
// ---------------------------------------------------------------------------

async function renderStep4() {
  contentEl.innerHTML = "<p>載入中…</p>";
  const data = await apiGet("/api/names");
  renderStep4Body(data);
}

function renderStep4Body(data) {
  const candidates = data.candidates || [];
  const excluded = data["已自動排除"] || [];

  const excludedRows = excluded.map((e) => `<tr>
    <td>${esc(e.start != null ? e.start.toFixed(1) : "")}</td>
    <td>${esc(e.matched_text || "")}</td>
    <td>${esc(e["原因"] || "")}</td>
  </tr>`).join("") || '<tr><td colspan="3">（這次沒有筆被排除清單擋掉）</td></tr>';

  if (candidates.length === 0) {
    contentEl.innerHTML = `
      <h1>4　標記覆核</h1>
      <div class="notyet-card">還沒有名字候選——先在第 1 步用 --roster 跑過找名字。</div>
      <div class="excluded-box">
        <strong>已自動排除（${excluded.length} 筆）</strong>
        <table><tr><th>時間</th><th>抓到的字</th><th>原因</th></tr>${excludedRows}</table>
      </div>
    `;
    return;
  }

  const cardsHtml = candidates.map((c) => {
    const lowConf = c["信心"] === "低";
    const tags = (c["已標記"] && c["已標記"].tags) || [];
    const note = (c["已標記"] && c["已標記"].note) || "";
    const checked = (name) => (tags.includes(name) ? "checked" : "");

    const origPlayer = c["原音網址"]
      ? `<audio controls preload="none" src="${esc(c["原音網址"])}"></audio>`
      : `<div class="missing">（原音檔缺失）</div>`;
    const mutedPlayer = c["消音網址"]
      ? `<audio controls preload="none" src="${esc(c["消音網址"])}"></audio>`
      : `<div class="missing">（消音版缺失）</div>`;

    return `
    <div class="namecard ${lowConf ? "lowconf" : ""}" id="name-${esc(c.id)}">
      <div class="rowhead">
        <span class="idx">第 ${esc(c.id)} 筆</span>
        <span class="time">${esc(c["時間"])}</span>
        ${lowConf ? '<span class="badge error">低信心</span>' : ""}
      </div>
      <div class="sentence">${c.sentence_html}</div>
      <table class="meta">
        <tr><td>抓到的字</td><td>${esc(c.matched_text)}</td>
            <td>代號</td><td>${esc(c["代號"])}</td></tr>
        <tr><td>位置</td><td>${esc(c["位置"])}</td>
            <td>比對層級</td><td>${esc(c["比對層級"])}</td></tr>
        <tr><td>信心</td><td class="conf-${esc(c["信心"])}">${esc(c["信心"])}</td>
            <td>建議做法</td><td>${esc(c["建議做法"])}</td></tr>
        <tr><td>切點信心</td><td>${esc(c["切點信心"])}</td><td></td><td></td></tr>
      </table>
      <div class="players">
        <div><div class="playerlabel">原音</div>${origPlayer}</div>
        <div><div class="playerlabel">消音版（建議切點）</div>${mutedPlayer}</div>
      </div>
      <div class="checks">
        <label><input type="checkbox" class="mark-check" data-id="${esc(c.id)}" data-tag="不是名字" ${checked("不是名字")}> 不是名字</label>
        <label><input type="checkbox" class="mark-check" data-id="${esc(c.id)}" data-tag="是地名" ${checked("是地名")}> 是地名</label>
        <label><input type="checkbox" class="mark-check" data-id="${esc(c.id)}" data-tag="切點削到旁邊的字" ${checked("切點削到旁邊的字")}> 切點削到旁邊的字</label>
      </div>
      <div class="notefield">
        <input type="text" class="mark-note" data-id="${esc(c.id)}" placeholder="備註（選填）" value="${esc(note)}">
      </div>
      <span class="savedmark" data-saved-for="${esc(c.id)}"></span>
    </div>`;
  }).join("");

  contentEl.innerHTML = `
    <h1>4　標記覆核</h1>
    <div class="excluded-box">
      <strong>已自動排除（${excluded.length} 筆）</strong>
      <table><tr><th>時間</th><th>抓到的字</th><th>原因</th></tr>${excludedRows}</table>
    </div>
    ${cardsHtml}
  `;

  document.querySelectorAll(".mark-check").forEach((el) => {
    el.addEventListener("change", () => submitMark(el.dataset.id));
  });
  document.querySelectorAll(".mark-note").forEach((el) => {
    el.addEventListener("change", () => submitMark(el.dataset.id));
    el.addEventListener("blur", () => submitMark(el.dataset.id));
  });
}

async function submitMark(id) {
  const card = document.getElementById(`name-${id}`);
  if (!card) return;
  const tags = Array.from(card.querySelectorAll(`.mark-check[data-id="${id}"]:checked`)).map((el) => el.dataset.tag);
  const noteEl = card.querySelector(`.mark-note[data-id="${id}"]`);
  const note = noteEl ? noteEl.value : "";
  const savedEl = card.querySelector(`[data-saved-for="${id}"]`);
  try {
    const result = await apiPost("/api/names/mark", { id, tags, note });
    if (savedEl) {
      savedEl.textContent = result["已加入排除清單"] ? "已存檔（已加入排除清單）" : "已存檔";
      setTimeout(() => { if (savedEl) savedEl.textContent = ""; }, 2500);
    }
  } catch (e) {
    if (savedEl) savedEl.textContent = `存檔失敗：${e.message}`;
  }
}
