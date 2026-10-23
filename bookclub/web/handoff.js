"use strict";
/* 33 號設計（#257–#260）接力交接：第 3 步、第 5 步的「交接打包…」「匯入交接包…」，總覽的「匯入交接包…」與「清掉這集」。
 * 後端 /api/handoff/…（bookclub/server.py、handoff.py）。依賴 app.js 的 apiGet／apiPost／esc／render，fileio.js 的 clGB，ui.js 的 uiConfirm／uiAlert（成功訊息一律用彈出視窗，不用浮動通知；宇軒 10-23）。
 * 畫面一律寫「雲端空間」。 */

const HO_HINT = {
  "第3步": "看完卡片、要交給夥伴做第 4、5 步時，按「交接打包…」存成 zip，再自己放上雲端空間。夥伴交回來的包，在第 5 步或總覽按「匯入交接包…」。",
  "第5步": "檢查、修改到接近完成，要交回老師時按「交接打包…」（不用先輸出成品）。收到對方的交接包，按「匯入交接包…」。",
};
let hoPollTimer = null;
let hoWatch = null;          // 這一頁按了哪一段的交接打包（做完要不要問清理）
let hoInfo = null;           // 最近一次 /api/handoff/state（錯誤資訊要用的工具版本、系統）

// 10-23（#258）：錯誤視窗的「複製錯誤資訊」——工具版本、系統、在哪裡、錯誤訊息、時間；路徑與檔名一律換成「（路徑略）」，
// 不放逐字稿、名字（錯誤訊息本來就不帶這些）
// 路徑可以有空格、中文、半形括號（「/Users/x/a b/第一集 (2).mp4」「C:\\Users\\…」）：從開頭的「/」「~/」「X:\\」一路遮到
// 引號、全形標點、換行或字串結尾為止，不留尾巴。路徑前面要是開頭、空白或引號（「Mac/Windows」這種字中間的斜線不算）
function hoScrub(text) {
  return String(text == null ? "" : text)
    .replace(/(^|[\s「『（("'：:])(?:[A-Za-z]:\\|~\/|\/)[^「」『』，。、；：（）"'\n]*/g, (m, pre) => `${pre}（路徑略）`)
    .replace(/[^\s「」『』，。、；：（）()"'／]+\.(?:mp4|mov|m4v|mkv|webm|avi|zip|wav|flac|json|csv)\b/gi, "（路徑略）");
}

function hoErrText(st, where, message, now) {
  const s = st || {};
  const sys = s["系統"] === "mac" ? "Mac" : s["系統"] === "wsl" ? "Windows" : "其他";
  const d = now || new Date();
  const p2 = (n) => String(n).padStart(2, "0");
  return ["讀書會剪輯工具：錯誤資訊", `工具版本：${s["工具版本"] || "不知道"}`, `系統：${sys}`, `在哪裡：${where}`,
    `錯誤訊息：${hoScrub(message)}`,
    `時間：${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`].join("\n");
}

async function hoErrAlert(title, message, where) {
  let st = hoInfo;
  if (!st) { try { st = await apiGet("/api/handoff/state"); } catch (e) { st = {}; } }
  await uiAlert({ title, message, copy: hoErrText(st, where, message) });
}

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
  hoInfo = d;
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
    // 宇軒 10-23：成功訊息一律彈出視窗。第 3 步：存好的那一句跟「建議清掉」放同一個視窗（沒有可清的才單獨說存好了）
    const saved = job["說明"] || "交接包已存好。";
    if (stage === "第3步") hoAskClean(msg, saved);   // 段 1：老師交出去之後，建議清掉 Ubuntu 裡的原片複本和暫存
    else uiAlert({ title: "交接包已存好", message: saved, ok: "好" });
  } else if (hoWatch === stage && job) {
    hoWatch = null;
    if (job["狀態"] === "失敗") hoErrAlert("交接包沒有存好", job["錯誤"] || "沒有存好", `第 ${stage === "第5步" ? 5 : 3} 步・交接打包`);
  }
}

// 第 3 步交接打包成功、核對過之後：建議清掉這一集的原片複本和暫存（33 號設計 §6，宇軒 10-20 定的字句）
async function hoAskClean(msg, saved) {
  let plan = null;
  try { plan = await apiGet("/api/handoff/clean-plan"); } catch (e) { plan = null; }
  if (!plan || !plan["合計"] || !plan["有交接包"]) {
    if (saved) await uiAlert({ title: "交接包已存好", message: saved, ok: "好" });
    return;
  }
  // 1022-1 起用工具自己的確認視窗（ui.js）；清掉是刪檔，danger
  const ok = await uiConfirm({ title: "交接包已存好", danger: true, ok: "清掉（建議）", cancel: "先留著",
    message: `${saved ? `${saved}\n` : ""}建議清掉這一集在 Ubuntu 裡的原片複本和暫存檔（約 ${hoSize(plan["合計"])}），之後匯入夥伴交回的包時，再選一次原片就好。判斷紀錄會留著。` });
  if (!ok) return;
  try {
    const r = await apiPost("/api/handoff/clean-copy", { "清單指紋": plan["清單指紋"] }, { quiet: true });
    await uiAlert({ title: "清掉了", message: `已清掉，空出 ${hoSize(r["省下位元組"])}。`, ok: "好" });
  } catch (e) { await hoErrAlert("沒有清掉", e.message, "第 3 步・清掉暫存"); }
}

// ---------------------------------------------------------------------------
// 匯入交接包：一個視窗兩格（① 選 zip ② 選原片）→ 核對 → 建好或換好 → 到該停的那一步
// ---------------------------------------------------------------------------

let hoImpTimer = null;
let hoImpRunning = false;    // 上一次畫的時候匯入是不是還在做（做到「失敗」那一下跳錯誤視窗，只跳一次）

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
  hoInfo = st;
  const v = hoImportView(st);
  if (hoImpRunning && v.js === "失敗") hoErrAlert("沒有匯入", (v.job && v.job["錯誤"]) || "沒有匯入", "匯入交接包");
  hoImpRunning = v.running;
  const s = (v.z || {})["摘要"] || {};
  const name = s["原片檔名"] || "原片";
  const zipHtml = v.z
    ? `<b>${esc(v.z["檔名"])}</b><div class="muted">${esc(s["哪一段"] === "第5步" ? "第 5 步交接（含成品）" : "第 3 步交接")}・${esc(s["電腦"] || "")}・${esc(String(s["匯出時間"] || "").replace("T", " ").slice(5, 16))}</div>`
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
    // 宇軒 10-22：粗體一句重點＋一行說明；改過的地方最多列 3 類白話，不列檔名
    const ask = v.job["要確認"] || {};
    const opts = ask["選項"] || [];
    const kinds = ask["改過類別"] || [];
    const kindLine = kinds.length ? `<p>改過的地方：${esc(kinds.slice(0, 3).join("、"))}${kinds.length > 3 ? `等 ${kinds.length} 類` : ""}</p>` : "";
    const yesLabel = ask["可以另建"] ? "繼續" : ask["建議不匯入"] ? "還是要匯入" : "繼續匯入";
    const btns = ask["只能不匯入"] ? `<button class="primary" id="ho-no">不匯入</button>`
      : ask["建議不匯入"] ? `<button class="ghost" id="ho-yes">${yesLabel}</button><button class="primary" id="ho-no">不匯入（建議）</button>`
      : `<button class="ghost" id="ho-no">不匯入</button><button class="primary" id="ho-yes">${yesLabel}</button>`;
    bottom = `<div class="ho-ask"><p><b>${esc(ask["重點"] || "")}</b></p><p>${esc(ask["說明"] || "")}</p>${kindLine}
      ${ask["只能不匯入"] ? "" : `<p class="muted">要換掉的工作區：${esc(ask["工作區名稱"] || "")}</p>`}
      ${opts.length ? `<ul class="ho-list">${opts.map((x, i) => `<li><label><input type="radio" name="ho-pick" value="${esc(x)}" ${i ? "" : "checked"}> ${esc(x)}</label></li>`).join("")}</ul>` : ""}
      ${ask["可以指定"] ? `<p><input type="text" id="ho-where" size="44" placeholder="原本那一份工作資料夾的完整位置（可以不填）"></p>` : ""}</div>
      <p id="ho-imp-msg" role="status"></p>
      <div class="rv-confirm-btns">${btns}</div>`;
  } else if (v.js === "完成") {
    // 宇軒 10-22：匯入成功只留「匯入好了」＋按鈕（有提醒才多一行）
    const r = v.job["結果"] || {};
    const go = r["去哪一步"] === "step5" ? `<button class="primary ho-go" data-go="step5">到第 5 步檢查成品</button>`
      : `<button class="ghost ho-go" data-go="step3">到第 3 步檢查修改需求</button><button class="primary ho-go" data-go="step4">到第 4 步做 AI 修改</button>`;
    dlg.innerHTML = `<h2 id="ho-import-title">匯入交接包</h2>
      <p><span class="badge done">匯入好了</span></p>
      ${(r["提醒"] || []).map((x) => `<p class="rv-warnline">${esc(x)}</p>`).join("")}
      <div class="rv-confirm-btns">${go}</div>`;
    dlg.querySelectorAll(".ho-go").forEach((b) => b.addEventListener("click", async () => {
      try { await apiPost("/api/handoff/import/reset", {}, { quiet: true }); } catch (e) { /* 已經清了 */ }
      dlg.close();
      const want = `#${b.dataset.go}`;
      if (location.hash === want && typeof render === "function") render(); else location.hash = want;
    }));
    clearTimeout(hoImpTimer);
    return;
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
    // 總審 T1、T2：選位置跟確認分開——選了（或打了）哪一份，先送去重新比對一次（不帶確認），有事會再問；
    // 已經是針對那一份在問的，才帶確認＋那一份
    const ask = (v.job && v.job["要確認"]) || {};
    const pick = dlg.querySelector('input[name="ho-pick"]:checked');
    const where = dlg.querySelector("#ho-where");
    let body = { "確認": true };
    if (pick) body = { "工作區": pick.value };
    else if (where && where.value.trim()) body = { "工作區": where.value.trim() };
    else if (ask["已指定"]) body = { "確認": true, "工作區": ask["工作區"] };
    try { await apiPost("/api/handoff/import/start", body, { quiet: true }); }
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
    const ok = await uiConfirm({ title: "清掉這集？", danger: true, ok: "清掉這集", cancel: "先留著",
      message: `「${p["顯示名稱"] || p["名稱"] || ""}」已經交出去了。清掉之後，這台的整個工作資料夾${h["含原片複本"] ? "和複製進 Ubuntu 的原片" : ""}都會移除（約 ${hoSize(h["大小"])}）。\n請確認交接包已經上傳到雲端空間。要回頭看可以重新匯入。` });
    if (!ok) return;
    btn.disabled = true;
    let r;
    try { r = await apiPost("/api/handoff/clean-episode", { "路徑": btn.dataset.path }, { quiet: true }); }
    catch (e) { btn.disabled = false; await hoErrAlert("沒有清掉", e.message, "總覽・清掉這集"); return; }
    await uiAlert({ title: "清掉了", message: `已清掉這集，空出 ${hoSize(r["省下位元組"])}。`, ok: "好" });
    if (typeof render === "function") render();
  }));
}
