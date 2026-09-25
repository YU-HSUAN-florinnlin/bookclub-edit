"use strict";

/* 讀書會剪輯工具｜前端骨架。原生 HTML/CSS/JS，不用打包工具、不用 CDN 套件。
 * 左側步驟列固定 0～7 步；這一輪只有第 1、2、4 步是真的可用頁面，其餘顯示
 * 「還沒做」的說明頁，不假裝可用。路由用 URL hash（#step1、#step2…）。 */

const STEP_DEFS = [
  { id: "overview", num: null, title: "總覽", real: true },
  { id: "step0", num: 0, title: "一次性設定", real: false },
  { id: "step1", num: 1, title: "轉文字與分析", real: true },
  { id: "step2", num: 2, title: "聲音分群與參考音", real: true },
  { id: "step3", num: 3, title: "學員逐字稿校對", real: true },
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

  try {
    if (id === "overview") return await renderOverview();
    if (id === "step1") return await renderStep1();
    if (id === "step2") return await renderStep2();
    if (id === "step3") return await renderStep3();
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


// ---------------------------------------------------------------------------
// 第 3 步：學員逐字稿校對（段落版，09-25 宇軒改版）
// ---------------------------------------------------------------------------
// 三塊：①段落時間表：誰從幾分幾秒講到幾分幾秒（可改說話者、併進上一段）
//       ②學員換成哪個英文名　③學員段落逐段確認逐字稿（一大段一起改）
// 鍵盤：在文字框裡 ⌘／Ctrl＋Enter＝確認並跳下一段（自動播放），Esc＝從頭播這段。

const pr = {
  data: null, player: new Audio(), rate: 1, autoplay: true, onlyTodo: false, showTeacher: false,
  activeId: null, activeSince: null,
};

async function renderStep3() {
  contentEl.innerHTML = "<p>載入中…</p>";
  pr.data = await apiGet("/api/turns");
  if (pr.data["尚未準備"]) {
    contentEl.innerHTML = `<h1>3　學員逐字稿校對</h1>
      <div class="notyet-card">還沒做段落分析。在終端機執行：<br>
      <code>.venv/bin/bookclub run turns &lt;工作區&gt;</code><br>會用 Claude 讀逐字稿切段落、用聲紋認人，約 10 分鐘。</div>`;
    return;
  }
  renderStep3Body();
}

async function prReload() {
  const y = window.scrollY;
  pr.data = await apiGet("/api/turns");
  renderStep3Body();
  window.scrollTo(0, y);
}

function fmtHms(sec) {
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
  return `${h ? h + ":" : ""}${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function prFmt(sec) {
  if (sec == null) return "—";
  return `${Math.floor(sec / 60)}:${String(Math.round(sec % 60)).padStart(2, "0")}`;
}

function prProgressHtml(p) {
  const pct = p["學員段落數"] ? Math.round((p["已確認"] / p["學員段落數"]) * 100) : 0;
  const perMin = p["每分鐘聲音要花分鐘"] != null ? `${p["每分鐘聲音要花分鐘"]} 分鐘` : "—（先確認幾段）";
  const total = p["推算全部要花小時"] != null ? `約 ${p["推算全部要花小時"]} 小時` : "—";
  return `<div class="pr-progress"><div class="bar"><div style="width:${pct}%"></div></div>
    <div class="nums">學員段落已確認 <b>${p["已確認"]}</b>／${p["學員段落數"]} 段　花了 <b>${prFmt(p["已花秒數"])}</b>
    　每 1 分鐘學員聲音要花 <b>${perMin}</b>　學員共講 ${p["學員聲音分鐘"]} 分鐘，推算全部要 <b>${total}</b></div></div>`;
}

function prWho(name) {
  if (name === "老師") return "老師";
  const code = (pr.data["學員"][name] || {})["代號"];
  return code ? `${name} → ${code}` : name;
}

function renderStep3Body() {
  const d = pr.data;
  const turns = d["段落"];
  const people = Object.entries(d["學員"]);
  const codes = d["代號選項"] || [];
  const whoOpts = (cur) => ["老師", ...people.map(([n]) => n), "新學員"]
    .map((n) => `<option value="${esc(n)}" ${n === cur ? "selected" : ""}>${esc(n === "新學員" ? "＋新的學員" : prWho(n))}</option>`).join("");

  // ① 段落時間表
  const tlRows = turns.map((t, i) => {
    const teacher = t["說話者"] === "老師";
    const mismatch = (t["文字判斷"] === "老師") !== (t["聲音判斷"] === "老師") && t["聲音判斷"] !== "不確定";
    return `<tr class="${teacher ? "t-teacher" : "t-student"} ${!pr.showTeacher && teacher ? "t-hide" : ""}">
      <td>${i + 1}</td><td class="tm">${esc(fmtHms(t.start))}–${esc(fmtHms(t.end))}</td>
      <td>${prFmt(t.end - t.start)}</td>
      <td><select class="tl-who" data-id="${esc(t.id)}">${whoOpts(t["說話者"])}</select></td>
      <td><button class="pr-play" data-url="${esc(t["音檔網址"])}">▶</button></td>
      <td class="why">${esc(t["換人依據"] || "")}${mismatch ? ' <span class="badge warn">聲紋判斷不同</span>' : ""}${t["學員是猜的"] ? ' <span class="badge warn">太短，學員是猜的</span>' : ""}</td>
      <td>${i ? `<button class="tl-merge" data-id="${esc(t.id)}" title="併進上一段">↑ 併入上一段</button>` : ""}</td></tr>`;
  }).join("");

  // ② 學員換成哪個英文名
  const pRows = people.map(([n, p]) => {
    const sug = p["建議代號"];
    const opts = ['<option value="">（還沒指定）</option>']
      .concat(codes.map((c) => `<option ${p["代號"] === c ? "selected" : ""}>${esc(c)}</option>`)).join("");
    const clue = Object.entries(p["點名線索"] || {}).map(([k, v]) => `${esc(k)}×${v}`).join("、");
    return `<tr><td><b>${esc(n)}</b></td><td>${prFmt(p["秒數"])}</td><td>${p["段數"]} 段</td>
      <td>${p["試聽網址"] ? `<button class="pr-play" data-url="${esc(p["試聽網址"])}">▶ 聽一段</button>` : ""}</td>
      <td><select class="p-code" data-person="${esc(n)}">${opts}</select>
      ${sug && !p["代號"] ? `<button class="p-sug" data-person="${esc(n)}" data-code="${esc(sug)}">用建議：${esc(sug)}</button>` : ""}</td>
      <td class="why">${clue ? `老師點名：${clue}` : ""}</td></tr>`;
  }).join("");

  // ③ 逐段確認逐字稿
  const cards = turns.map((t, i) => {
    if (t["說話者"] === "老師") return "";
    const rows = Math.min(14, Math.max(3, Math.ceil(t["校對稿"].length / 38)));
    return `<div class="pr-row ${t["已確認"] ? "done" : ""}" id="pr-${esc(t.id)}" data-id="${esc(t.id)}">
      <div class="pr-head"><span class="idx">第 ${i + 1} 段</span>
        <b>${esc(prWho(t["說話者"]))}</b><span class="time">${esc(fmtHms(t.start))}–${esc(fmtHms(t.end))}（${prFmt(t.end - t.start)}）</span>
        <button class="pr-play" data-url="${esc(t["音檔網址"])}" data-id="${esc(t.id)}">▶ 播放整段</button>
        <button class="pr-split" data-id="${esc(t.id)}" title="在游標位置切成兩段">✂ 從游標處切開</button>
        <button class="pr-done" data-id="${esc(t.id)}">${t["已確認"] ? "✓ 已確認" : "確認"}</button></div>
      <textarea class="pr-text" rows="${rows}" data-id="${esc(t.id)}">${esc(t["校對稿"])}</textarea>
      ${t["原文"] !== t["校對稿"] ? `<details class="pr-orig"><summary>看原本轉出來的</summary>${esc(t["原文"])}</details>` : ""}
    </div>`;
  }).join("");

  const c = d["比對"] || {};
  contentEl.innerHTML = `
    <h1>3　學員逐字稿校對</h1>
    <p class="hint">電腦已經讀過逐字稿、切好段落，並用聲紋認出哪幾段是同一位學員。請依序確認三件事：①段落有沒有切對、是不是這個人 ②每位學員換成哪個英文名 ③逐段確認逐字稿（成品裡學員的聲音照這份文字重念，錯一個字就念錯一個字）。</p>
    <div id="pr-progress">${prProgressHtml(d["進度"])}</div>
    <div class="pr-tools">播放速度 <select id="pr-rate"><option value="1">1 倍</option><option value="1.25">1.25 倍</option><option value="1.5">1.5 倍</option></select>
      <label><input type="checkbox" id="pr-autoplay" ${pr.autoplay ? "checked" : ""}> 確認後自動播放下一段</label></div>

    <details class="pr-sec" open><summary><h2>① 段落：誰從幾分幾秒講到幾分幾秒</h2></summary>
      <p class="hint">共 ${turns.length} 段。只看文字判斷老師／學員，跟聲紋一致 ${Math.round((c["一致比例"] || 0) * 100)}%；標「聲紋判斷不同」的段落請優先聽。
      <label><input type="checkbox" id="tl-teacher" ${pr.showTeacher ? "checked" : ""}> 也顯示老師的段落</label></p>
      <table class="tl">${tlRows}</table></details>

    <details class="pr-sec" open><summary><h2>② 學員換成哪個英文名（${people.length} 位）</h2></summary>
      <table class="tl">${pRows}</table>
      <p class="hint">「學員 1、2⋯⋯」是聲紋分出來的；同一個人被分成兩位時，在 ① 把段落改成同一位即可。</p></details>

    <details class="pr-sec" open><summary><h2>③ 逐段確認逐字稿</h2></summary>
      <p class="hint">點進文字框開始計時；<kbd>⌘</kbd>／<kbd>Ctrl</kbd>＋<kbd>Enter</kbd> 確認並跳下一段，<kbd>Esc</kbd> 從頭播這段。發現其實是兩個人，把游標放在換人的地方按「✂ 從游標處切開」。
      <label><input type="checkbox" id="pr-onlytodo" ${pr.onlyTodo ? "checked" : ""}> 只顯示還沒確認的</label></p>
      <div id="pr-list">${cards}</div></details>`;

  document.getElementById("pr-rate").value = String(pr.rate);
  document.getElementById("pr-rate").addEventListener("change", (e) => { pr.rate = Number(e.target.value); pr.player.playbackRate = pr.rate; });
  document.getElementById("pr-autoplay").addEventListener("change", (e) => { pr.autoplay = e.target.checked; });
  document.getElementById("pr-onlytodo").addEventListener("change", (e) => { pr.onlyTodo = e.target.checked; prApplyFilter(); });
  document.getElementById("tl-teacher").addEventListener("change", (e) => {
    pr.showTeacher = e.target.checked;
    contentEl.querySelectorAll("tr.t-teacher").forEach((r) => r.classList.toggle("t-hide", !pr.showTeacher));
  });
  contentEl.querySelectorAll(".pr-play").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.id) prActivate(b.dataset.id);
    prPlay(b.dataset.url);
  }));
  contentEl.querySelectorAll(".tl-who").forEach((el) => el.addEventListener("change", async () => {
    await apiPost("/api/turns/save", { id: el.dataset.id, "說話者": el.value }); await prReload();
  }));
  contentEl.querySelectorAll(".tl-merge").forEach((b) => b.addEventListener("click", async () => {
    await apiPost("/api/turns/merge", { id: b.dataset.id }); await prReload();
  }));
  contentEl.querySelectorAll(".p-code").forEach((el) => el.addEventListener("change", async () => {
    await apiPost("/api/turns/person", { "學員": el.dataset.person, "代號": el.value || null }); await prReload();
  }));
  contentEl.querySelectorAll(".p-sug").forEach((b) => b.addEventListener("click", async () => {
    await apiPost("/api/turns/person", { "學員": b.dataset.person, "代號": b.dataset.code }); await prReload();
  }));
  contentEl.querySelectorAll(".pr-text").forEach((el) => {
    el.addEventListener("focus", () => prActivate(el.dataset.id));
    el.addEventListener("blur", () => prSave(el.dataset.id, { "校對稿": el.value }));
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); prComplete(el.dataset.id, true); }
      else if (e.key === "Escape") { e.preventDefault(); prPlayId(el.dataset.id); }
    });
  });
  contentEl.querySelectorAll(".pr-split").forEach((b) => b.addEventListener("mousedown", (e) => e.preventDefault()));
  contentEl.querySelectorAll(".pr-split").forEach((b) => b.addEventListener("click", async () => {
    const ta = document.querySelector(`.pr-text[data-id="${b.dataset.id}"]`);
    const at = ta.selectionStart;
    if (!at || at >= ta.value.length) { alert("先把游標放在要切開的地方（換人的第一個字前面）"); return; }
    await prSave(b.dataset.id, { "校對稿": ta.value });
    try { await apiPost("/api/turns/split", { id: b.dataset.id, at }); } catch (err) { alert(err.message); return; }
    await prReload();
  }));
  contentEl.querySelectorAll(".pr-done").forEach((b) => b.addEventListener("click", () => {
    const t = prItem(b.dataset.id);
    if (t["已確認"]) prSave(b.dataset.id, { "已確認": false }); else prComplete(b.dataset.id, false);
  }));
  prApplyFilter();
}

function prItem(id) { return pr.data["段落"].find((x) => x.id === id); }

function prPlay(url) { pr.player.src = url; pr.player.playbackRate = pr.rate; pr.player.play().catch(() => {}); }

function prPlayId(id) { const t = prItem(id); if (t) prPlay(t["音檔網址"]); }

function prActivate(id) {
  if (pr.activeId === id) return;
  prFlushTime();
  pr.activeId = id;
  pr.activeSince = Date.now();
}

function prFlushTime() {
  if (!pr.activeId || !pr.activeSince) return;
  const t = prItem(pr.activeId);
  if (t) t._pending = (t._pending || 0) + (Date.now() - pr.activeSince) / 1000;
  pr.activeSince = Date.now();
}

async function prSave(id, fields) {
  const t = prItem(id);
  if (!t) return;
  if (pr.activeId === id) prFlushTime();
  const body = { id, ...fields };
  if (t._pending) { body["加秒數"] = t._pending; t._pending = 0; }
  const onlyText = Object.keys(fields).length === 1 && "校對稿" in fields;
  if (onlyText && fields["校對稿"].trim() === t["校對稿"] && !body["加秒數"]) return;
  const res = await apiPost("/api/turns/save", body);
  Object.assign(t, res["段落"]);
  const row = document.getElementById(`pr-${id}`);
  if (row) {
    row.classList.toggle("done", !!t["已確認"]);
    row.querySelector(".pr-done").textContent = t["已確認"] ? "✓ 已確認" : "確認";
  }
  document.getElementById("pr-progress").innerHTML = prProgressHtml(res["進度"]);
}

async function prComplete(id, goNext) {
  const row = document.getElementById(`pr-${id}`);
  await prSave(id, { "校對稿": row.querySelector(".pr-text").value, "已確認": true });
  if (!goNext) return prApplyFilter();
  const rows = [...contentEl.querySelectorAll(".pr-row")].filter((r) => r.style.display !== "none");
  const idx = rows.indexOf(row);
  const next = rows.slice(idx + 1).find((r) => !r.classList.contains("done")) || rows[idx + 1];
  if (next) {
    const ta = next.querySelector(".pr-text");
    ta.focus();
    next.scrollIntoView({ block: "center", behavior: "smooth" });
    if (pr.autoplay) prPlayId(ta.dataset.id);
  }
  prApplyFilter();
}

function prApplyFilter() {
  contentEl.querySelectorAll(".pr-row").forEach((r) => {
    const t = prItem(r.dataset.id);
    r.style.display = pr.onlyTodo && t["已確認"] && !r.contains(document.activeElement) ? "none" : "";
  });
}
