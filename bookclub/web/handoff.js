"use strict";
/* 33 號設計（#257–#260）接力交接：第 3 步、第 5 步的「交接打包…」「匯入交接包…」，總覽的「匯入交接包…」與「清掉這集」。
 * 後端 /api/handoff/…（bookclub/server.py、handoff.py）。依賴 app.js 的 apiGet／apiPost／esc／render，fileio.js 的 clGB。
 * 畫面一律寫「雲端空間」。 */

const HO_HINT = {
  "第3步": "看完卡片、要交給夥伴做第 4、5 步時，按「交接打包…」存成 zip，再自己放上雲端空間。夥伴交回來的包，在第 5 步或總覽按「匯入交接包…」。",
  "第5步": "檢查、修改到接近完成，要交回老師時按「交接打包…」（不用先輸出成品）。收到對方的交接包，按「匯入交接包…」。",
};
let hoPollTimer = null;
let hoWatch = null;          // 這一頁按了哪一段的交接打包（做完要不要問清理）

function hoSize(n) { return typeof clGB === "function" ? clGB(n) : `${Math.round(Number(n || 0) / 1e6)} MB`; }

// 交接打包那一行的狀態（純函式）
function hoPackLine(job) {
  if (!job) return { running: false, text: "", kind: "" };
  const s = job["狀態"];
  if (s === "等待" || s === "存檔中") return { running: true, text: `正在打包…（第 ${job["第幾個"]}／${job["共幾個"]} 個檔）`, kind: "" };
  if (s === "核對中") return { running: true, text: "正在核對…", kind: "" };
  if (s === "已存好") return { running: false, text: job["說明"] || "已存好。", kind: "done" };
  if (s === "停止") return { running: false, text: job["錯誤"] || "停下來了。", kind: "" };
  return { running: false, text: job["錯誤"] || "沒有存好", kind: "error" };
}

async function hoBoxInit(el, stage) {
  if (!el) return;
  let d;
  try { d = await apiGet("/api/handoff/state"); } catch (e) { return; }
  if (!document.body.contains(el)) return;
  const job = d["打包"] && d["打包"]["哪一段"] === stage ? d["打包"] : null;
  const line = hoPackLine(job);
  const why = (d["可以打包"] || {})[stage];
  const gone = stage === "第5步" && d["已交出去"] ? `<p class="muted">這一集已經交出去了（${esc(String(d["已交出去"]).slice(5, 10))}）。不用再改的話，可以到總覽按「清掉這集」。</p>` : "";
  el.hidden = false;
  el.innerHTML = `<h3>交接</h3>
    <p class="hint">${esc(HO_HINT[stage])}</p>${gone}
    <div class="fo-row">
      ${line.running ? `<button class="secondary" id="ho-stop">停止</button>`
        : `<button class="secondary" id="ho-pack" ${why ? "disabled" : ""}>交接打包…</button>`}
      <button class="ghost" id="ho-imp" ${line.running ? "disabled" : ""}>匯入交接包…</button>
      ${stage === "第3步" && !line.running && d["最近一次"] ? `<button class="ghost" id="ho-clean" hidden>清掉暫存…</button>` : ""}
      ${why && !line.running ? `<span class="muted">${esc(why)}</span>` : ""}
      <span id="ho-msg" role="status">${line.text ? (line.kind ? `<span class="badge ${line.kind}">${line.kind === "done" ? "已存好" : "沒有存好"}</span> ` : "") + esc(line.text) : ""}</span>
    </div>
    <div id="ho-typed" hidden><p class="hint" id="ho-typed-why"></p>
      <input type="text" id="ho-path" size="50" placeholder="資料夾的完整路徑">
      <button class="secondary" id="ho-typed-go">開始打包</button> <button class="ghost" id="ho-typed-no">不打包了</button></div>`;
  const msg = el.querySelector("#ho-msg");
  const start = async (path) => {
    try { await apiPost("/api/handoff/pack", { "段": stage, "路徑": path }, { quiet: true }); }
    catch (e) { msg.innerHTML = `<span class="badge error">沒有開始</span> ${esc(e.message)}`; return; }
    hoWatch = stage;
    hoBoxInit(el, stage);
  };
  const typed = (txt) => {
    el.querySelector("#ho-typed").hidden = false;
    el.querySelector("#ho-typed-why").textContent = txt || "打要存交接包的資料夾路徑：";
    el.querySelector("#ho-typed-go").onclick = () => start(el.querySelector("#ho-path").value);
    el.querySelector("#ho-typed-no").onclick = () => { el.querySelector("#ho-typed").hidden = true; };
  };
  const pack = el.querySelector("#ho-pack");
  if (pack) pack.addEventListener("click", async () => {
    if (!d["系統視窗"]) { typed("系統的選資料夾視窗關掉了，打要存交接包的資料夾路徑："); return; }
    pack.disabled = true;
    let r;
    try { r = await apiPost("/api/handoff/pick-folder", {}, { quiet: true }); }
    catch (e) { msg.innerHTML = `<span class="badge error">打不開選資料夾視窗</span> ${esc(e.message)}`; pack.disabled = false; return; }
    pack.disabled = false;
    if (r["取消"]) return;                  // 按取消：什麼都不動
    if (r["退回網頁"]) { typed(r["說明"]); return; }
    start(r["路徑"]);
  });
  const stop = el.querySelector("#ho-stop");
  if (stop) stop.addEventListener("click", async () => {
    try { await apiPost("/api/handoff/cancel", {}, { quiet: true }); } catch (e) { /* 已經做完了 */ }
    hoBoxInit(el, stage);
  });
  el.querySelector("#ho-imp").addEventListener("click", () => hoOpenImport());
  const cl = el.querySelector("#ho-clean");
  if (cl && !(hoWatch === stage && job && job["狀態"] === "已存好")) {
    apiGet("/api/handoff/clean-plan").then((plan) => {
      if (!document.body.contains(cl) || !plan["合計"] || !plan["有交接包"]) return;
      cl.textContent = `清掉暫存（約 ${hoSize(plan["合計"])}）…`;
      cl.hidden = false;
      cl.addEventListener("click", () => hoAskClean(msg));
    }).catch(() => {});
  }
  clearTimeout(hoPollTimer);
  if (line.running) hoPollTimer = setTimeout(() => { if (document.body.contains(el)) hoBoxInit(el, stage); }, 1000);
  else if (hoWatch === stage && job && job["狀態"] === "已存好") {
    hoWatch = null;
    if (stage === "第3步") hoAskClean(msg);   // 段 1：老師交出去之後，建議清掉 Ubuntu 裡的原片複本和暫存
  } else if (hoWatch === stage && job) hoWatch = null;
}

// 第 3 步交接打包成功、核對過之後：建議清掉這一集的原片複本和暫存（33 號設計 §6，宇軒 10-20 定的字句）
async function hoAskClean(msg) {
  let plan;
  try { plan = await apiGet("/api/handoff/clean-plan"); } catch (e) { return; }
  if (!plan["合計"] || !plan["有交接包"]) return;
  const ok = await hoDialog("交接包已存好",
    [`交接包已存好。建議清掉這一集在 Ubuntu 裡的原片複本和暫存檔（約 ${hoSize(plan["合計"])}），之後匯入夥伴交回的包時，再選一次原片就好。判斷紀錄會留著。`],
    "清掉（建議）", "先留著");
  if (!ok) return;
  try {
    const r = await apiPost("/api/handoff/clean-copy", { "清單指紋": plan["清單指紋"] }, { quiet: true });
    if (msg) msg.insertAdjacentHTML("beforeend", ` <span class="muted">已清掉，空出 ${esc(hoSize(r["省下位元組"]))}。</span>`);
  } catch (e) { if (msg) msg.insertAdjacentHTML("beforeend", ` <span class="badge error">沒有清</span> ${esc(e.message)}`); }
}

// 兩顆按鈕的小視窗（回 true＝按了 yes）
function hoDialog(title, lines, yes, no) {
  return new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.className = "rv-confirm";
    dlg.setAttribute("aria-labelledby", "ho-dlg-title");
    dlg.innerHTML = `<h2 id="ho-dlg-title">${esc(title)}</h2>${lines.map((x) => `<p>${esc(x)}</p>`).join("")}
      <div class="rv-confirm-btns"><button class="ghost" id="ho-dlg-no">${esc(no)}</button><button class="primary" id="ho-dlg-yes">${esc(yes)}</button></div>`;
    document.body.appendChild(dlg);
    const done = (v) => { dlg.close(); dlg.remove(); resolve(v); };
    dlg.addEventListener("cancel", (e) => { e.preventDefault(); done(false); });
    dlg.querySelector("#ho-dlg-no").addEventListener("click", () => done(false));
    dlg.querySelector("#ho-dlg-yes").addEventListener("click", () => done(true));
    dlg.showModal();
  });
}

// ---------------------------------------------------------------------------
// 匯入交接包：一個視窗兩格（① 選 zip ② 選原片）→ 核對 → 建好或換好 → 到該停的那一步
// ---------------------------------------------------------------------------

let hoImpTimer = null;

// 匯入工作那一段的文字（純函式）
function hoImportView(st) {
  const imp = (st && st["匯入"]) || {};
  const z = imp["交接包"];
  const v = imp["原片"];
  const cp = imp["複製"];
  const job = imp["工作"];
  const js = job && job["狀態"];
  const running = !!(job && ["等待", "核對中", "比對原片", "匯入中"].includes(js));
  const copying = !!(cp && ["等待", "複製中"].includes(cp["狀態"]));
  const ready = !!(z && v && v["路徑"]) && !running && !copying;
  let status = "";
  if (js === "核對中") status = `正在核對交接包…（第 ${job["第幾個"]}／${job["共幾個"]} 個檔）`;
  else if (js === "比對原片") status = "正在比對原片…";
  else if (js === "匯入中" || js === "等待") status = "正在匯入…";
  else if (js === "失敗") status = job["錯誤"] || "沒有匯入";
  return { z, v, cp, job, js, running, copying, ready, status };
}

function hoStepName(go) { return go === "step5" ? "第 5 步" : "第 4 步"; }

async function hoOpenImport() {
  let dlg = document.getElementById("ho-import");
  if (!dlg) {
    dlg = document.createElement("dialog");
    dlg.id = "ho-import";
    dlg.className = "rv-confirm ho-import";
    dlg.setAttribute("aria-labelledby", "ho-import-title");
    document.body.appendChild(dlg);
    dlg.addEventListener("close", () => { clearTimeout(hoImpTimer); });
  }
  if (!dlg.open) dlg.showModal();
  await hoRenderImport(dlg);
}

async function hoRenderImport(dlg) {
  let st;
  try { st = await apiGet("/api/handoff/state"); } catch (e) {
    dlg.innerHTML = `<h2 id="ho-import-title">匯入交接包</h2><p class="badge error">${esc(e.message)}</p>
      <div class="rv-confirm-btns"><button class="primary" id="ho-x">關閉</button></div>`;
    dlg.querySelector("#ho-x").onclick = () => dlg.close();
    return;
  }
  if (!dlg.open) return;
  const v = hoImportView(st);
  const s = (v.z || {})["摘要"] || {};
  const name = s["原片檔名"] || "原片";
  const zipHtml = v.z
    ? `<b>${esc(v.z["檔名"])}</b><div class="muted">${esc(s["哪一段"] === "第5步" ? "第 5 步交接（含成品）" : "第 3 步交接")}・${esc(s["電腦"] || "")}・${esc(String(s["匯出時間"] || "").replace("T", " ").slice(0, 16))}</div>`
    : `<span class="muted">還沒選</span>`;
  let vidHtml = `<span class="muted">先選交接包</span>`;
  if (v.z) {
    vidHtml = `<div>請選從雲端空間下載的「<b>${esc(name)}</b>」</div>`;
    if (v.v) vidHtml += `<div><b>${esc(v.v["檔名"] || "")}</b></div>`;
    if (v.copying) vidHtml += `<div class="pk-copy"><progress max="100" value="${v.cp["百分比"]}"></progress> 複製進 Ubuntu 中… <b>${v.cp["百分比"]}%</b></div>`;
    else if (v.cp && v.cp["狀態"] === "失敗") vidHtml += `<div><span class="badge error">複製失敗</span> ${esc(v.cp["錯誤"] || "")}（重新選一次）</div>`;
  }
  let bottom = "";
  if (v.js === "要確認") {
    const ask = v.job["要確認"] || {};
    const lines = ask["沒有交接紀錄"]
      ? ["這台已經有這一集，但沒有交接打包過的紀錄，看不出來交出去之後有沒有改過。"]
      : [`這台的這一集在交接打包之後又改過（${ask["共幾個"]} 個檔）：`];
    bottom = `<div class="ho-ask">${lines.map((x) => `<p>${esc(x)}</p>`).join("")}
      ${(ask["改過"] || []).length ? `<ul class="ho-list">${ask["改過"].map((x) => `<li>${esc(x)}</li>`).join("")}${ask["共幾個"] > ask["改過"].length ? `<li>⋯</li>` : ""}</ul>` : ""}
      <p>繼續的話，會先把這台的紀錄備份到「備份/交接前_日期」，再換成包裡的。</p></div>
      <div class="rv-confirm-btns"><button class="ghost" id="ho-no">不匯入</button><button class="primary" id="ho-yes">繼續匯入</button></div>`;
  } else if (v.js === "完成") {
    const r = v.job["結果"] || {};
    bottom = `<p><span class="badge done">匯入好了</span> ${r["新建"] ? "已經建好這一集。" : "已經換成包裡的內容，原本的紀錄備份在「" + esc(r["備份"] || "") + "」。"}</p>
      ${(r["提醒"] || []).map((x) => `<p class="rv-warnline">${esc(x)}</p>`).join("")}
      <p class="muted">下載的交接包（zip）可以刪了。</p>
      <div class="rv-confirm-btns"><button class="primary" id="ho-go">到${esc(hoStepName(r["去哪一步"]))}</button></div>`;
  } else {
    bottom = `<p id="ho-imp-msg" role="status">${v.status ? (v.js === "失敗" ? `<span class="badge error">沒有匯入</span> ` : "") + esc(v.status) : ""}</p>
      <div class="rv-confirm-btns"><button class="ghost" id="ho-cancel" ${v.running ? "disabled" : ""}>取消</button>
        <button class="primary" id="ho-start" ${v.ready ? "" : "disabled"}>開始匯入</button></div>`;
  }
  dlg.innerHTML = `<h2 id="ho-import-title">匯入交接包</h2>
    <p class="hint">不用自己解壓縮，在這裡選 zip 就好。</p>
    <table class="kv pk-table"><tbody>
      <tr><td class="pk-what">① 交接包</td><td class="pk-btns"><button class="secondary" id="ho-zip" ${v.running ? "disabled" : ""}>${v.z ? "換一個" : "選交接包（zip）"}</button></td><td class="pk-file">${zipHtml}</td></tr>
      <tr><td class="pk-what">② 原片</td><td class="pk-btns">${v.z ? `<button class="secondary" id="ho-vid" ${v.running || v.copying ? "disabled" : ""}>${v.v ? "換一支" : "選原片"}</button>` : ""}</td><td class="pk-file">${vidHtml}</td></tr>
    </tbody></table>
    <div id="ho-typed2" hidden><p class="hint" id="ho-typed2-why"></p>
      <input type="text" id="ho-path2" size="44" placeholder="完整的路徑"> <button class="secondary" id="ho-typed2-go">用這個</button></div>
    ${bottom}`;
  const say = (html) => { const m = dlg.querySelector("#ho-imp-msg"); if (m) m.innerHTML = html; };
  const typed = (why, url) => {
    dlg.querySelector("#ho-typed2").hidden = false;
    dlg.querySelector("#ho-typed2-why").textContent = why;
    dlg.querySelector("#ho-typed2-go").onclick = async () => {
      try { await apiPost(url, { "路徑": dlg.querySelector("#ho-path2").value }, { quiet: true }); }
      catch (e) { say(`<span class="badge error">不行</span> ${esc(e.message)}`); return; }
      hoRenderImport(dlg);
    };
  };
  const zb = dlg.querySelector("#ho-zip");
  if (zb) zb.addEventListener("click", async () => {
    zb.disabled = true;
    let r;
    try { r = await apiPost("/api/handoff/import/zip", {}, { quiet: true }); }
    catch (e) { zb.disabled = false; say(`<span class="badge error">不能用</span> ${esc(e.message)}`); return; }
    if (r["退回網頁"]) { typed(r["說明"], "/api/handoff/import/zip"); zb.disabled = false; return; }
    hoRenderImport(dlg);
  });
  const vb = dlg.querySelector("#ho-vid");
  if (vb) vb.addEventListener("click", async () => {
    vb.disabled = true;
    vb.textContent = "選檔視窗開著…";
    let r;
    try { r = await apiPost("/api/pick", { "用途": "交接原片" }, { quiet: true }); }
    catch (e) { say(`<span class="badge error">不能用</span> ${esc(e.message)}`); hoRenderImport(dlg); return; }
    if (r["退回網頁"] && !r["取消"]) { typed(`${r["說明"] || ""} 打原片的完整路徑：`, "/api/handoff/import/video"); vb.disabled = false; return; }
    hoRenderImport(dlg);
  });
  const sb = dlg.querySelector("#ho-start");
  if (sb) sb.addEventListener("click", async () => {
    sb.disabled = true;
    try { await apiPost("/api/handoff/import/start", {}, { quiet: true }); }
    catch (e) { say(`<span class="badge error">沒有開始</span> ${esc(e.message)}`); sb.disabled = false; return; }
    hoRenderImport(dlg);
  });
  const cb = dlg.querySelector("#ho-cancel");
  if (cb) cb.addEventListener("click", async () => {
    try { await apiPost("/api/handoff/import/reset", {}, { quiet: true }); } catch (e) { /* 沒東西可取消 */ }
    dlg.close();
  });
  const yes = dlg.querySelector("#ho-yes");
  if (yes) yes.addEventListener("click", async () => {
    yes.disabled = true;
    try { await apiPost("/api/handoff/import/start", { "確認": true }, { quiet: true }); }
    catch (e) {
      yes.disabled = false;
      yes.closest(".rv-confirm-btns").insertAdjacentHTML("beforebegin", `<p class="rv-warnline">${esc(e.message)}</p>`);
      return;
    }
    hoRenderImport(dlg);
  });
  const no = dlg.querySelector("#ho-no");
  if (no) no.addEventListener("click", async () => {
    try { await apiPost("/api/handoff/import/reset", {}, { quiet: true }); } catch (e) { /* 沒東西可取消 */ }
    dlg.close();
  });
  const go = dlg.querySelector("#ho-go");
  if (go) go.addEventListener("click", async () => {
    const r = (v.job && v.job["結果"]) || {};
    try { await apiPost("/api/handoff/import/reset", {}, { quiet: true }); } catch (e) { /* 已經清了 */ }
    dlg.close();
    const want = `#${r["去哪一步"] || "step4"}`;
    if (location.hash === want && typeof render === "function") render(); else location.hash = want;
  });
  clearTimeout(hoImpTimer);
  if (v.running || v.copying) hoImpTimer = setTimeout(() => { if (dlg.open) hoRenderImport(dlg); }, 1000);
}

// ---------------------------------------------------------------------------
// 總覽：「匯入交接包…」（新增一集旁邊）、已交出去的那一集「清掉這集」
// ---------------------------------------------------------------------------

function hoOverviewCardHtml() {
  return `<div class="card ho-card"><h2 style="margin-top:0">匯入交接包</h2>
    <p class="muted">收到別人交接的 zip（從雲端空間下載）時按這裡；不用自己解壓縮，原片也在同一個視窗選。</p>
    <button class="secondary" id="hoImportBtn">匯入交接包…</button></div>`;
}

// 專案清單那一列的「已交出去」徽章與「清掉這集」（純函式；p 是 /api/projects 的一列）
function hoRowHtml(p) {
  const h = p["交接"];
  if (!h) return "";
  return ` <span class="badge">已交出去</span> <button class="ghost ho-clean" data-path="${esc(p["路徑"])}">清掉這集（${esc(hoSize(h["大小"]))}）</button>`;
}

function hoBindOverview(root, list) {
  const b = root.querySelector("#hoImportBtn");
  if (b) b.addEventListener("click", () => hoOpenImport());
  root.querySelectorAll(".ho-clean").forEach((btn) => btn.addEventListener("click", async () => {
    const p = (list || []).find((x) => x["路徑"] === btn.dataset.path) || {};
    const h = p["交接"] || {};
    const ok = await hoDialog("清掉這集？", [
      `「${p["顯示名稱"] || p["名稱"] || ""}」已經交出去了。清掉之後，這台的整個工作資料夾${h["含原片複本"] ? "和複製進 Ubuntu 的原片" : ""}都會移除（約 ${hoSize(h["大小"])}）。`,
      "請確認交接包已經上傳到雲端空間。要回頭看可以重新匯入。"], "清掉這集", "先留著");
    if (!ok) return;
    btn.disabled = true;
    try { await apiPost("/api/handoff/clean-episode", { "路徑": btn.dataset.path }, { quiet: true }); }
    catch (e) { btn.disabled = false; alert(e.message); return; }
    if (typeof render === "function") render();
  }));
}
