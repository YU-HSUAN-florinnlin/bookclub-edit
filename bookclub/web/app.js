"use strict";
// 10-05 宇軒：「網頁第四部的『預估時間』先不要顯示」。只是網頁不顯示（後端照樣算、命令列照樣印），
// 要恢復把這個改回 true。
const SHOW_EXEC_ETA = false;

/* 讀書會剪輯工具｜前端骨架。原生 HTML/CSS/JS，不用打包工具、不用 CDN 套件。
 * 左側步驟列 1～5 步（1022-1 #267：第 0 步「初始化設定」收進左下角齒輪「工具設定」選單，網址照舊 #step0）（09-25 宇軒：原本第 3、4 步合成第 3 步「覆核工作台」；09-29：原本第 5 步逐筆覆核、第 6 步整片檢查
 * 合成第 5 步「成品檢查」），每步標 AI／人工，可以收合；
 * 還沒做的步驟顯示「還沒做」的說明頁，不假裝可用。路由用 URL hash（#step1、#step2…）。
 * 第 3 步覆核工作台在 review.js，第 5 步成品檢查在 finalcheck.js。 */

// who：這一步是誰做（09-29 宇軒：步驟列標「AI」或「人工」，一眼看出現在輪到誰）
const STEP_DEFS = [
  { id: "overview", num: null, title: "總覽", real: true },
  // nav: false＝不列在步驟列（1022-1 #267：收進左下角「工具設定」選單，見 TOOL_MENU），網址照舊能開
  { id: "step0", num: 0, title: "初始化設定", real: true, who: "人工", nav: false },
  { id: "step1", num: 1, title: "影片分析", real: true, who: "AI" },
  { id: "step2", num: 2, title: "挑選老師參考聲音片段", real: true, who: "人工" },
  { id: "step3", num: 3, title: "覆核工作台", real: true, who: "人工" },
  { id: "step4", num: 4, title: "AI 執行", real: true, who: "AI" },
  { id: "step5", num: 5, title: "成品檢查", real: true, who: "人工" },
];

const contentEl = document.getElementById("content");
const stepsEl = document.getElementById("steps");
const videoInfoEl = document.getElementById("videoinfo");

let statusPollTimer = null;

// ---------------------------------------------------------------------------
// API 小工具
// ---------------------------------------------------------------------------

async function apiGet(path) {
  let res;
  try {
    res = await fetch(path);
  } catch (e) {
    const err = new Error("連不上網頁伺服器（可能被關掉了）");
    err.network = true;   // 10-03 第九批（#24）：輪詢分得出「伺服器沒了」跟「伺服器回錯誤」
    throw err;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${path} 失敗（${res.status}）`);
  return data;
}

// 存檔呼叫一律走 apiPost：失敗時畫面最上面出現紅色橫幅（09-30：以前伺服器重開、斷線時改的東西默默不見）。
// quiet：呼叫的地方自己會說明失敗原因、而且不是存檔的（例如「開始執行」已經在跑、計時）。
async function apiPost(path, body, { quiet = false } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  } catch (e) {
    const err = new Error("連不上網頁伺服器（可能被關掉了）");
    if (!quiet) { showSaveError(err.message); err.shown = true; }
    throw err;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `${path} 失敗（${res.status}）`);
    err.data = data;   // 10-02 第七批：呼叫端看得到其他欄位（例如「找不到claude」）
    // 1015-8 第 3 段：已結案的瘦身版——頂端那一句已經說了，不再跳「沒存到」的提示
    if (!quiet && !data["已結案"]) { showSaveError(err.message); err.shown = true; }
    throw err;
  }
  return data;
}

function showSaveError(reason) {
  const el = document.getElementById("saveError");
  if (!el) return;
  el.innerHTML = `<b>這次修改沒存到，請重新整理。</b> <span class="why">${esc(reason || "")}</span>
    <button id="saveErrorReload">重新整理</button> <button class="ghost" id="saveErrorClose" aria-label="關掉這個提示">×</button>`;
  el.hidden = false;
  document.getElementById("saveErrorReload").addEventListener("click", () => location.reload());
  document.getElementById("saveErrorClose").addEventListener("click", () => { el.hidden = true; });
}

// 沒被接住的錯誤（事件裡 await 失敗）也要讓人看得到，不能默默吞掉
window.addEventListener("unhandledrejection", (e) => {
  const err = e.reason || {};
  if (err.shown) return;
  showSaveError(err.message || String(err));
});

// 10-03 第九批（#24）：輪詢（第 1 步分析、第 4 步執行，每 2 秒一次）連續失敗＝伺服器多半已經關掉，
// 畫面最上面出現橫幅；以前完全靜默，畫面一直停在「執行中…」。偶爾失敗一次不跳（例如伺服器忙），
// 連續 CONN_FAIL_LIMIT 次（約 6 秒）連不上才跳；下一次輪詢成功就自動收掉。
// 只算「連不上」（fetch 本身失敗）；伺服器有回應、只是回錯誤（例如 500）不算。
const CONN_FAIL_LIMIT = 3;
let connFails = 0;

function connBanner() {
  let el = document.getElementById("connLost");
  if (!el) {
    el = document.createElement("div");
    el.id = "connLost";
    el.className = "save-error conn-lost";
    el.setAttribute("role", "alert");
    el.hidden = true;
    el.innerHTML = "<b>連不上伺服器，可能已經關掉了；請回終端機看，或重新雙擊啟動。</b>"
      + ' <span class="why">伺服器回來之後，這一條會自己消失。</span>';
    const anchor = document.getElementById("saveError");
    if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(el, anchor.nextSibling);
    else document.body.insertBefore(el, document.body.firstChild);
  }
  return el;
}

function pollOk() {
  connFails = 0;
  const el = document.getElementById("connLost");
  if (el) el.hidden = true;
}

function pollFailed(e) {
  if (!e || !e.network) return;   // 伺服器還在、只是回錯誤：不算斷線
  connFails += 1;
  if (connFails >= CONN_FAIL_LIMIT) connBanner().hidden = false;
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}

// 10-16 #225 宇軒（乙方案）：藏起來的說明用小問號。滑鼠滑過出現、點一下固定住、再點收起；觸控點一下開、再點收起。
// html 由呼叫端負責 esc；說明框用 fixed 定位，避免被外層 overflow 切掉
function helpTip(html, label) {
  return `<span class="help-tip"><button type="button" class="help-tip-btn" aria-label="${esc(label || "說明")}" aria-expanded="false">?</button><span class="help-tip-pop" role="tooltip" hidden>${html}</span></span>`;
}

function helpTipPlace(tip) {
  const btn = tip.querySelector(".help-tip-btn"), pop = tip.querySelector(".help-tip-pop");
  const b = btn.getBoundingClientRect();
  pop.style.left = "0px"; pop.style.top = "0px";
  const w = pop.offsetWidth, h = pop.offsetHeight, gap = 8;
  const left = Math.max(gap, Math.min(b.left - 12, window.innerWidth - w - gap));
  const below = b.bottom + 6;
  const top = below + h > window.innerHeight - gap && b.top - 6 - h > gap ? b.top - 6 - h : below;
  pop.style.left = `${left}px`; pop.style.top = `${top}px`;
}

function helpTipShow(tip, on) {
  const pop = tip.querySelector(".help-tip-pop");
  if (!pop) return;
  clearTimeout(tip._hideTimer);
  pop.hidden = !on;
  tip.querySelector(".help-tip-btn").setAttribute("aria-expanded", on ? "true" : "false");
  if (on) helpTipPlace(tip); else tip.classList.remove("pinned");
}

(function helpTipInit() {
  const tipOf = (el) => el && el.closest && el.closest(".help-tip");
  document.addEventListener("pointerover", (e) => {
    if (e.pointerType !== "mouse") return;
    const t = tipOf(e.target);
    if (t) helpTipShow(t, true);
  });
  document.addEventListener("pointerout", (e) => {
    if (e.pointerType !== "mouse") return;
    const t = tipOf(e.target);
    if (!t || t.classList.contains("pinned") || t.contains(e.relatedTarget)) return;
    t._hideTimer = setTimeout(() => helpTipShow(t, false), 200);   // 留一點時間讓滑鼠移到說明框上
  });
  document.addEventListener("click", (e) => {
    const t = tipOf(e.target);
    if (t && e.target.closest(".help-tip-btn")) {
      e.preventDefault(); e.stopPropagation();
      const pin = !t.classList.contains("pinned");
      t.classList.toggle("pinned", pin);
      helpTipShow(t, pin);
    }
    document.querySelectorAll(".help-tip.pinned").forEach((x) => { if (x !== t) helpTipShow(x, false); });   // 點別的地方收起
  }, true);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") document.querySelectorAll(".help-tip-pop:not([hidden])").forEach((p) => helpTipShow(p.closest(".help-tip"), false));
  });
  window.addEventListener("scroll", () => document.querySelectorAll(".help-tip-pop:not([hidden])").forEach((p) => helpTipPlace(p.closest(".help-tip"))), true);
})();

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

// 10-08（宇軒：兩個專案名稱一樣，切錯了看不出來）：頂端常駐「現在在：名稱（上一層）」＋正式／測試（純函式）
function pjNowHtml(lab) {
  if (!lab || !lab["顯示名稱"]) return "";
  return `<div class="pj-now"><b>現在在：${esc(lab["顯示名稱"])}</b> <span class="badge ${lab["標示"] === "測試" ? "" : "done"}">${esc(lab["標示"])}</span></div>`;
}

// 1015-8 第 3 段（32 號設計 §5.2）：已結案的瘦身版——每一步頂端一句（給老師看）；第 1–4 步所有按鈕灰掉、第 5 步只灰掉
// 聽／改／重做／輸出／拿檔（style.css 的 body.ws-closed、body.ws-closed-all）。伺服器那邊另外一層擋（409）。
function wsClosed() { return !!(lastState && lastState["已結案"]); }

function wsClosedBarHtml(st) {
  const s = st || lastState || {};
  if (!s["已結案"]) return "";
  const c = s["結案"] || {};
  const where = c["日期"] || c["位置顯示"] ? `（${[c["日期"], c["位置顯示"] ? `存到 ${c["位置顯示"]}` : ""].filter(Boolean).join(" ")}）` : "";
  return `<div class="ws-closed-bar" role="status">這支影片已結案${esc(where)}，這裡只能看紀錄。</div>`;
}

function wsClosedMark(id) {
  const on = wsClosed() && /^step[1-5]$/.test(id || "");
  document.body.classList.toggle("ws-closed", on);
  document.body.classList.toggle("ws-closed-all", on && id !== "step5");
  if (on && id !== "step5" && !contentEl.querySelector(".ws-closed-bar")) contentEl.insertAdjacentHTML("afterbegin", wsClosedBarHtml());
}

// 總覽：進行中的在上面、已結案的放最下面一組（純函式；順序照原本的）
function pjGroups(list) {
  return { "進行中": (list || []).filter((p) => !p["已結案"]), "已結案": (list || []).filter((p) => !!p["已結案"]) };
}

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
    videoInfoEl.innerHTML = `${pjNowHtml(state["專案標示"])}${esc(name)}${esc(dur)}<br>${esc(state.workdir || "")}`;
  }

  const active = currentRouteId();
  stepsEl.innerHTML = STEP_DEFS.filter((s) => s.nav !== false).map((s) => {
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
    const short = s.num === null ? "總" : String(s.num);
    const who = s.who ? `<span class="step-who ${s.who === "AI" ? "ai" : "human"}">${esc(s.who)}</span>` : "";
    return `<li><a href="#${s.id}" class="${s.id === active ? "active" : ""}" title="${esc(label)}${s.who ? `（${esc(s.who)}）` : ""}">
      <span class="step-badge ${badgeClass}">${badgeText}</span><span class="step-num ${badgeClass}">${esc(short)}${badgeClass === "done" ? "✓" : ""}</span><span class="step-label">${esc(label)}</span>${who}
    </a>${s.num === 3 && s.real && state && !state["沒有專案"] ? navSubHtml(state) : ""}</li>`;
  }).join("");
  navSubSync();
  toolMenuSync();
}

// 10-16 宇軒：第 3 步下面兩個子步驟，點了切到那一頁、看得出現在在哪裡；做完前面綠色打勾
const NAV_SUBS = [["prep", "3-1", "開始前 4 件事"], ["cards", "3-2", "逐筆確認"]];
let navSubWant = null;
let navSubLast = null;   // 最後一次在第 3 步算的打勾 {workdir, prep, cards}   // 從別的步驟點子步驟：進第 3 步時要打開哪一頁（review.js renderReview 讀）

function navSubHtml(state) {
  return `<ul class="substeps">${NAV_SUBS.map(([k, n, t]) => `<li><a href="#step3" data-sub="${k}" title="${esc(`${n} ${t}`)}">
    <span class="step-badge sub-badge" data-sub-badge="${k}">·</span><span class="step-label">${esc(n)}　${esc(t)}</span></a></li>`).join("")}</ul>`;
}

// 子步驟的「現在在哪一頁」與打勾：第 3 步的資料讀過就照它算，沒讀過照 /api/state
function navSubSync() {
  const st = lastState || {};
  const onStep3 = currentRouteId() === "step3";
  // 審查必修 3：只在第 3 步上才用第 3 步的資料（離開後、換專案後照 /api/state，不沿用舊資料）
  const rvd = onStep3 && typeof rv !== "undefined" && rv.data && document.getElementById("rv-prep-btn") ? rv : null;
  // 離開第 3 步：同一個專案沿用最後一次在第 3 步算的結果（/api/state 的 3-2 只看學員段落，會跟第 3 步裡不一樣）；換了專案才照 /api/state
  const last = !rvd && navSubLast && navSubLast.workdir === st.workdir ? navSubLast : null;
  const prepDone = rvd ? rvPrepDone() : last ? last.prep : !!(st["開始前"] && st["開始前"].done);
  const cardsDone = rvd ? rvItems().every((x) => rvDone(x)) : last ? last.cards : !!(st.proofread && st.proofread.done);
  if (rvd) navSubLast = { workdir: st.workdir, prep: prepDone, cards: cardsDone };
  const done = { prep: prepDone, cards: cardsDone };
  const cur = onStep3 && rvd ? (rv.prepOpen ? "prep" : "cards") : null;
  document.querySelectorAll(".substeps a[data-sub]").forEach((a) => {
    const k = a.dataset.sub;
    a.classList.toggle("active", k === cur);
    const b = a.querySelector("[data-sub-badge]");
    if (b) { b.classList.toggle("done", !!done[k]); b.textContent = done[k] ? "✓" : "·"; }
  });
  const main = document.querySelector('.steps a[href="#step3"]:not([data-sub])');
  if (main && cur) main.classList.remove("active");   // 亮子步驟就好，第 3 步本身不重複亮
  if (main && (rvd || last)) {   // 第 3 步本身：兩個子步驟都做完才打勾（第 3 步的資料算得出來才這樣算；沒有就照 /api/state）
    const all = prepDone && cardsDone;
    const b = main.querySelector(".step-badge"), n = main.querySelector(".step-num");
    if (b) { b.classList.toggle("done", all); b.textContent = all ? "✓" : "·"; }
    if (n) { n.classList.toggle("done", all); n.textContent = all ? "3✓" : "3"; }
  }
}

document.addEventListener("click", (e) => {
  const a = e.target.closest && e.target.closest(".substeps a[data-sub]");
  if (!a) return;
  e.preventDefault();
  const want = a.dataset.sub;
  if (currentRouteId() === "step3" && typeof rv !== "undefined" && rv.data && document.getElementById("rv-prep-btn")) {   // 審查建議 2：載入中不切
    rv.prepOpen = want === "prep";
    rvRenderMain();
    window.scrollTo(0, 0);
  } else {
    navSubWant = want;
    location.hash = "#step3";
  }
});

// 收合／展開（記在 localStorage；讀不到、私密視窗丟例外時一律當作展開）
const NAV_KEY = "nav-collapsed";

function navCollapsedSaved() {
  try { return localStorage.getItem(NAV_KEY) === "1"; } catch (e) { return false; }
}

function setNavCollapsed(on, save = true) {
  document.querySelector(".app").classList.toggle("nav-collapsed", on);
  const b = document.getElementById("navToggle");
  // 1022-1（#266）：按鈕是三條線的圖示（index.html 裡的 SVG），收合、展開都同一個，只換提示文字
  // 1022-2（#265）：說明改用馬上出現的提示框（ui.js data-tip），收合後只剩圖示，aria-label 留著給報讀軟體
  const label = on ? "展開步驟列" : "收合步驟列";
  b.dataset.tip = label;
  b.setAttribute("aria-label", label);
  b.setAttribute("aria-expanded", String(!on));
  if (save) {
    try { localStorage.setItem(NAV_KEY, on ? "1" : "0"); } catch (e) { /* 存不了也沒關係，只是下次不記得 */ }
  }
  window.dispatchEvent(new Event("resize"));   // 時間軸這類照寬度畫的東西重畫
}

setNavCollapsed(navCollapsedSaved(), false);
document.getElementById("navToggle").addEventListener("click", () => {
  setNavCollapsed(!document.querySelector(".app").classList.contains("nav-collapsed"));
});

// ---------------------------------------------------------------------------
// 1022-1（#267）：左下角齒輪「工具設定」選單（往上展開）。第 0 步「初始化設定」收在這裡，不列在步驟列。
// 第 3 步上方另有一顆「設定」（第 3 步自己的設定），所以這顆叫「工具設定」。之後要加項目：在 TOOL_MENU 加一列。
// ---------------------------------------------------------------------------

const TOOL_MENU = [
  { route: "step0", label: "初始化設定", hint: "名冊、敏感詞、發音對照表、匿名聲線；設定包匯出匯入" },
];

function toolMenuEls() {
  return { wrap: document.getElementById("toolMenuWrap"), btn: document.getElementById("toolMenuBtn"), menu: document.getElementById("toolMenu") };
}

function toolMenuOpen() { const { menu } = toolMenuEls(); return !!(menu && !menu.hidden); }

function toolMenuRender() {
  const { menu } = toolMenuEls();
  if (!menu) return;
  const cur = currentRouteId();
  menu.innerHTML = TOOL_MENU.map((m) => `<a role="menuitem" href="#${esc(m.route)}" class="tool-menu-item${m.route === cur ? " active" : ""}"${m.route === cur ? ' aria-current="page"' : ""}>
    <span class="tool-menu-label">${esc(m.label)}</span>${m.hint ? `<span class="tool-menu-hint">${esc(m.hint)}</span>` : ""}</a>`).join("");
}

// 選單用 fixed 定位貼在按鈕上方：步驟列收合後只有 64px 寬，放在裡面會被切掉
function toolMenuPlace() {
  const { btn, menu } = toolMenuEls();
  if (!btn || !menu) return;
  const r = btn.getBoundingClientRect();
  menu.style.left = `${Math.max(8, r.left)}px`;
  menu.style.bottom = `${Math.max(8, window.innerHeight - r.top + 6)}px`;
}

function toolMenuShow(on, { focus = false } = {}) {
  const { btn, menu } = toolMenuEls();
  if (!btn || !menu) return;
  if (on) { toolMenuRender(); menu.hidden = false; toolMenuPlace(); }
  else menu.hidden = true;
  btn.setAttribute("aria-expanded", String(on));
  btn.classList.toggle("open", on);
  if (on && focus) { const first = menu.querySelector('[role="menuitem"]'); if (first) first.focus(); }
}

// 在初始化設定頁時，齒輪按鈕亮「目前在這裡」
function toolMenuSync() {
  const { btn } = toolMenuEls();
  if (!btn) return;
  const here = TOOL_MENU.some((m) => m.route === currentRouteId());
  btn.classList.toggle("active", here);
  if (here) btn.setAttribute("aria-current", "page"); else btn.removeAttribute("aria-current");
  if (toolMenuOpen()) toolMenuRender();
}

(function toolMenuBind() {
  const { wrap, btn, menu } = toolMenuEls();
  if (!wrap || !btn || !menu) return;
  btn.addEventListener("click", (e) => {
    // 用鍵盤（Enter／空白鍵）打開時，焦點直接進第一項；滑鼠點開不搶焦點
    toolMenuShow(!toolMenuOpen(), { focus: e.detail === 0 });
  });
  btn.addEventListener("keydown", (e) => {
    if (e.key === "ArrowUp" || e.key === "ArrowDown") { e.preventDefault(); toolMenuShow(true, { focus: true }); }
  });
  menu.addEventListener("click", (e) => {
    if (e.target.closest && e.target.closest('[role="menuitem"]')) toolMenuShow(false);   // 照連結走 #step0，選單收起
  });
  menu.addEventListener("keydown", (e) => {
    const items = [...menu.querySelectorAll('[role="menuitem"]')];
    const i = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const n = items.length;
      if (n) items[(i + (e.key === "ArrowDown" ? 1 : -1) + n) % n].focus();
    } else if (e.key === "Tab" && ((!e.shiftKey && i === items.length - 1) || (e.shiftKey && i === 0))) {
      toolMenuShow(false);   // Tab 離開選單就收起
    }
  });
  document.addEventListener("click", (e) => {
    if (toolMenuOpen() && !wrap.contains(e.target)) toolMenuShow(false);   // 點選單外面收起
  });
  // 1022-1 審查修正：選單是 fixed 定位，側欄捲動時不會跟著動 → 捲動就收起
  const side = document.getElementById("sidebar");
  if (side) side.addEventListener("scroll", () => { if (toolMenuOpen()) toolMenuShow(false); }, { passive: true });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && toolMenuOpen() && !document.querySelector("dialog[open]")) {
      e.preventDefault();
      toolMenuShow(false);
      btn.focus();
    }
  });
  window.addEventListener("resize", () => { if (toolMenuOpen()) toolMenuPlace(); });
})();

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

// 10-01 走查：很快連點兩個步驟時，慢的那一頁（例如第 4 步）比較晚畫完，會蓋掉後點的那一頁
// （左邊亮第 2 步、右邊卻是第 4 步）。舊的那一次比新的晚畫完，就照現在的網址再畫一次。
let renderSeq = 0, renderDone = 0;

async function render() {
  const my = ++renderSeq;
  try {
    await renderRoute();
  } finally {
    if (my === renderSeq) renderDone = my;
    else if (renderDone === renderSeq) render();
  }
}

async function renderRoute() {
  await renderSidebar();
  const id = currentRouteId();
  if (id !== "step3") navSubWant = null;   // 審查建議 3：沒進到第 3 步就不留著
  stopStatusPoll();
  contentEl.classList.remove("wide");

  // 10-17 畫面測試：已結案的專案從第 1–4 步回總覽，這兩個 class 還在＝總覽的「切換」「選影片」全灰。每一頁畫之前先拿掉
  document.body.classList.remove("ws-closed", "ws-closed-all");
  try {
    if (id === "overview") return await renderOverview();
    if (id !== "step0" && lastState && lastState["沒有專案"]) {
      contentEl.innerHTML = `<div class="notyet-card">還沒選影片。到 <a href="#overview">總覽</a> 選一支影片，或切換到已有的專案。</div>`;
      return;
    }
    if (id === "step1") { await renderStep1(); return wsClosedMark(id); }
    if (id === "step2") { await renderStep2(); return wsClosedMark(id); }
    if (id === "step3") { await renderReview(); return wsClosedMark(id); }
    if (id === "step4") { await renderExecute(); return wsClosedMark(id); }
    if (id === "step5") { await renderFinal(); return wsClosedMark(id); }
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
  const notes = {};
  contentEl.innerHTML = `
    <h1>${esc(def.num)}　${esc(def.title)}</h1>
    <div class="notyet-card">${esc(notes[def.id] || "這一步還沒做，之後的階段才會做。")}</div>
  `;
}

// ---------------------------------------------------------------------------
// 第 4 步：AI 執行（09-29：一個按鈕依序跑老師名字 → 學員重念 → 保留原聲學員名字 → 組裝，做過的跳過）
// ---------------------------------------------------------------------------

let execPollTimer = null;
let execGoCheck = null;
let execBackRow = null;   // 10-01 第三批 13：從第 3 步按「回第 4 步總檢查」回來時，捲到出發的那一列（那一列處理好消失了就捲到總檢查開頭）   // 10-01 第三批 9：從第 3 步「全部通過，開始 AI 修改」帶過來時，捲到總檢查、上面寫一行說明

async function renderExecute() {
  contentEl.innerHTML = "<p>載入中…</p>";
  await renderExecuteBody();
}

// 10-01：「2026-10-01T03:16:01」→「10-01 03:16」（畫面上不出現程式的時間格式）
function fmtStamp(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(iso || ""));
  return m ? `${m[2]}-${m[3]} ${m[4]}:${m[5]}` : String(iso || "");
}

// 10-01 宇軒 7-5：按「開始執行」之前的總檢查（一定要處理／請看一眼），每一列可以跳過去聽
function fcTime(t) { t = Math.round(t * 10) / 10; const m = Math.floor(t / 60), s = t - m * 60;   // 10-01：先四捨五入（不會出現「:60.0」）
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${s.toFixed(1).padStart(4, "0")}`; }

let fcRowsCache = [];   // 總檢查每一列（按鈕用編號找回那一列）
let fcLookCache = [];   // 10-02 第五批：「請看一眼」畫面上的那幾列（按「我看過了」時一起送出，之後多了列就回到沒勾）

// 10-02 第四批：確認過的列（按了「照目前設定做」、第 3 步答了「老師的話，不用處理」）留在清單裡顯示成灰色；
// 「把確認好的收合起來」這個開關記在 localStorage（讀不到、私密視窗丟例外時一律當作不收合）
const FC_FOLD_KEY = "fc-fold-done";
function fcFoldDone() { try { return localStorage.getItem(FC_FOLD_KEY) === "1"; } catch (e) { return false; } }
function fcSetFoldDone(on) { try { localStorage.setItem(FC_FOLD_KEY, on ? "1" : "0"); } catch (e) { /* 存不了也沒關係，只是下次不記得 */ } }
// 切在段落外面的幾秒：在第 4 步也能改答案（跟第 3 步卡片上的選項一樣）
const FC_OUT = [["老師不用處理", "老師的話，不用處理"], ["老師重念", "老師的話，要用老師聲音重念"],
  ["還是學員", "還是學員的聲音"], ["好幾個人", "還有好幾個人的聲音"], ["", "先不回答"]];

// 10-05 #176（純函式）：「跳過去聽」只播這一列的起點到終點（不加前後緩衝，才聽得出有沒有混到別人的聲音）。
// 起訖不合理（沒有時間、終點不在起點之後）回傳 null，不放按鈕
function fcListenRange(start, end) {
  const a = Number(start), b = Number(end);
  if (start == null || end == null || !Number.isFinite(a) || !Number.isFinite(b) || b - a < 0.05) return null;
  return [Math.max(0, a), b];
}

function fcListenBtn(start, end, label) {
  const rg = fcListenRange(start, end);
  return rg ? `<button class="secondary small" data-fcplay="${rg[0]}|${rg[1]}" data-tip="從 ${esc(fcTime(rg[0]))} 播到 ${esc(fcTime(rg[1]))} 就停（前後不多播）">${esc(label)}</button>` : "";
}

// 10-05 #177（宇軒）：「一定要處理」每一列的「照目前設定做」＝按下去就照第 3 步的決定進行（純函式，回傳 HTML）。
// 原本「我聽過了」的三類也合成這一顆；旁邊一句這一類照目前設定做的後果。
// 不開放的類別（名字換不了代號、重疊缺東西）：放「回第 3 步補」，按了跳到第 3 步那一張卡片。
function fcKeepHtml(r, i) {
  if (r["可以按照目前設定做"]) {
    const on = !!r["已按照目前設定做"];
    return `<button class="${on ? "" : "secondary "}small fc-keep" data-fckeep="${esc(r.key)}" data-on="${on ? "0" : "1"}" aria-pressed="${on}">${on ? "✓ 照目前設定做・再按取消" : "照目前設定做"}</button>
    ${r["照目前設定做的後果"] ? `<div class="muted fc-keepwhy">${esc(r["照目前設定做的後果"])}</div>` : ""}`;
  }
  if (r["回第3步補"]) {   // 10-08：不再擋開始執行，紅字寫略過的後果
    return `<button class="small fc-back3" data-fcback="${i}">回第 3 步補</button>
    <div class="rv-warnline fc-keepwhy">${esc(r["照目前設定做不開放原因"] || "")}</div>`;
  }
  return `<div class="muted fc-keepwhy">${esc(r["照目前設定做不開放原因"] || "")}</div>`;
}

// 「回第 3 步補」要帶去哪裡（純函式）：找得到卡片就定位到那一張；找不到就到第 3 步，說明要找哪一類卡片
const FC_BACK3_CARD = { "名字換不了代號": "老師提到名字", "重疊缺東西": "重疊" };
function fcBack3Go(r) {
  const what = `${r["名稱"] ? `〈${r["名稱"]}〉` : ""}${r["去改"] || r["說明"] || ""}`;
  if (r["第3步"]) return { key: r["第3步"], openMore: true, note: `從開始前總檢查過來：${what}`, backKey: r.key };
  const card = FC_BACK3_CARD[r["類別"]] || "這一筆";
  return { key: null, note: `從開始前總檢查過來：在第 3 步找「${card}」的卡片${what ? `：${what}` : ""}`, backKey: r.key };
}

// 10-05 #178：「請看一眼」每一列的「我看過了」（純函式，回傳 HTML）
function fcLookBtnHtml(r) {
  const on = !!r["已看過"];
  return `<button class="${on ? "" : "secondary "}small fc-lookbtn" data-fclook="${esc(r.key)}" data-on="${on ? "0" : "1"}" aria-pressed="${on}">${on ? "✓ 看過了・再按取消" : "我看過了"}</button>`;
}

// 10-08 宇軒（流程簡化）：兩區預設收合成一行摘要、預設先略過（純函式）
function fcFoldHeads(fc) {
  const must = fc["一定要處理"] || [], look = fc["請看一眼"] || [];
  const mustLeft = must.filter((r) => !r["處理好"]).length, lookLeft = look.filter((r) => !r["已看過"]).length;
  return {
    must: must.length ? `⚠️ 一定要處理 ${mustLeft} 列${mustLeft ? "（預設先略過）" : "（都處理好了）"}${mustLeft < must.length ? `，已處理 ${must.length - mustLeft} 列` : ""}` : "⚠️ 一定要處理：沒有",
    look: look.length ? `👀 請看一眼 ${lookLeft} 處${lookLeft ? "（預設先略過）" : "（都看過了）"}${lookLeft < look.length ? `，已看過 ${look.length - lookLeft} 處` : ""}` : "👀 請看一眼：沒有",
    mustLeft, lookLeft,
  };
}
// 10-08 宇軒：整個「開始前總檢查」區塊預設收成一行（純函式）；展開狀態記在瀏覽器（讀不到就當作收合）
function fcWholeHead(fc) {
  const h = fcFoldHeads(fc);
  const n = (fc["一定要處理"] || []).length, k = (fc["請看一眼"] || []).length;
  if (!n && !k) return "開始前總檢查：沒有要處理、要看的";
  return `開始前總檢查：一定要處理 ${h.mustLeft} 列、請看一眼 ${h.lookLeft} 處${h.mustLeft || h.lookLeft ? "（預設先略過）" : "（都處理好了）"}`;
}
const FC_CHECK_OPEN_KEY = "fc-check-open";
function fcCheckOpen() { try { return localStorage.getItem(FC_CHECK_OPEN_KEY) === "1"; } catch (e) { return false; } }
function fcSetCheckOpen(on) { try { localStorage.setItem(FC_CHECK_OPEN_KEY, on ? "1" : "0"); } catch (e) { /* 存不了也沒關係 */ } }
let execOpen = {};   // 展開了哪一區（同一個分頁裡重畫時記得；重新整理網頁就回到收合）

function finalCheckHtml(fc) {
  fcRowsCache = [];
  const row = (r, isLook = false) => {
    const i = fcRowsCache.push(r) - 1;
    const paths = r["有學員聲音"] || [];
    const done = isLook ? !!r["已看過"] : !!r["處理好"];
    const isOut = String(r.key).startsWith("段落外:");
    const outNow = r["段落外答案"] || "";
    const outPick = isOut ? `<details class="fc-paths fc-outans"><summary>改答案</summary><p class="muted">這幾秒是誰的聲音？（跟第 3 步卡片上的問題同一題，改這裡兩邊一起改）</p>
      <ul>${FC_OUT.map(([v, label]) => `<li><button class="${outNow === v ? "" : "secondary "}small" data-fcout="${i}|${esc(v)}" aria-pressed="${outNow === v}">${esc(label)}</button></li>`).join("")}</ul></details>` : "";
    // 10-02 第六批：已經組裝過的話同時列成品時間（還沒有成品時只列原片）
    const outT = r["成品起訖"] ? `<div class="muted">成品 ${esc(fcTime(r["成品起訖"][0]))}–${esc(fcTime(r["成品起訖"][1]))}</div>` : "";
    const sh = r["縮小"];
    const pts = r["時間點"] || [];   // 10-04 #111：學員段落裡自動處理的重疊，每一處一個「聽」按鈕
    return `<tr data-fckey="${esc(r.key)}" class="${done ? "fc-done" : ""}${r["新的"] ? " fc-new" : ""}"><td class="nowrap">${outT ? "原片 " : ""}${esc(fcTime(r.start))}–${esc(fcTime(r.end))}${outT}${r["名稱"] ? `<div class="muted">${esc(r["名稱"])}</div>` : ""}${r["顯示編號"] ? `<div><span class="idtag" title="跟 AI 助手溝通用的編號">${esc(r["顯示編號"])}</span></div>` : ""}
      ${done ? `<div><span class="badge fc-donetag">${isLook ? "看過了" : "照目前設定做"}</span></div>` : ""}${r["新的"] ? `<div><span class="badge fc-newtag">新的</span></div>` : ""}
      ${r["照目前設定做後變了"] ? `<div><span class="badge fc-newtag">按了之後內容變了，要再確認一次</span></div>` : ""}</td>
    <td>${esc(r["說明"])}${r["去改"] ? `<div class="muted fc-todo">${done ? "" : "怎麼改："}${esc(r["去改"])}</div>` : ""}</td>
    <td class="fc-acts">${isLook ? fcLookBtnHtml(r) : ""}${pts.length ? "" : fcListenBtn(r.start, r.end, "跳過去聽")}
    ${pts.length ? `<div class="fc-points">${pts.map((p) => fcListenBtn(p.start, p.end, `聽 ${fcTime(p.start)}`)).join(" ")}</div>` : ""}
    ${sh && sh["可以縮"] ? `<button class="small" data-fcshrink="${i}">照建議縮小</button>` : ""}
    ${r["第3步"] && !r["回第3步補"] ? `<button class="secondary small" data-fcgo="${i}">去第 3 步改這一筆</button>` : ""}
    ${isLook ? "" : fcKeepHtml(r, i)}
    ${outPick}
    ${paths.length ? `<details class="fc-paths"><summary>有學員的聲音</summary><p class="muted">選一個，會帶著這段時間到第 3 步（起訖先填好，按「新增」或「儲存修改」才會存）：</p>
      <ul>${paths.map((p, j) => `<li><button class="secondary small" data-fcpath="${i}|${j}">${esc(p["文字"])}</button></li>`).join("")}</ul></details>` : ""}</td></tr>`;
  };
  const m = fc["摘要"] || {};
  const must = fc["一定要處理"] || [], look = fc["請看一眼"] || [];
  fcLookCache = look.map((r) => ({ key: r.key, start: r.start, end: r.end }));
  const mem = m["記憶體"] || null;
  const newN = fc["看過後新增"] || 0;
  const isDone = (r) => !!r["處理好"];   // 10-05 #177：按了「照目前設定做」（含以前的「我聽過了」）
  const doneN = must.filter(isDone).length;
  const fold = fcFoldDone() && doneN > 0;
  const shown = fold ? must.filter((r) => !isDone(r)) : must;
  const seenN = look.filter((r) => r["已看過"]).length;
  const hd = fcFoldHeads(fc);
  const openMust = !!execOpen["一定要處理"] || !!execGoCheck || !!execBackRow, openLook = !!execOpen["請看一眼"] || !!execBackRow;
  const openAll = fcCheckOpen() || !!execGoCheck || !!execBackRow;
  return `<details class="fc-whole" id="fcWhole" ${openAll ? "open" : ""}><summary><b id="fcCheckTitle">${esc(fcWholeHead(fc))}</b> <span class="fc-foldbtn">展開</span></summary>
    ${execGoCheck ? `<p class="hint" id="fcGoNote">${esc(execGoCheck)}</p>` : ""}
    <div class="card">
      <p class="muted">兩區預設都先略過：不用按任何東西就能「開始執行」，沒處理的照目前的設定做。做完到第 5 步，「需留意」那一頁列出成品裡還留著原聲、要聽一下的地方。想現在處理的話，展開來逐列處理。</p>
      <details class="fc-fold" data-fcfold="一定要處理" ${openMust ? "open" : ""}><summary><b>${esc(hd.must)}</b> <span class="fc-foldbtn">展開查看</span></summary>
      ${doneN ? `<label class="nowrap muted"><input type="checkbox" id="fcFold" ${fold ? "checked" : ""}> 把確認好的收合起來</label>` : ""}
      ${must.length ? `${shown.length ? `<table class="kv fc-check">${shown.map((r) => row(r)).join("")}</table>` : ""}
        ${fold ? `<p><button class="ghost small" id="fcUnfold">已確認 ${doneN} 列（點了展開）</button></p>` : ""}
        <p class="muted">要改的按「去第 3 步改這一筆」；改完回這一頁會重算。第 3 步已經決定好、照那樣做就可以的，按「照目前設定做」。確認過的列顯示成灰色，再按一次「照目前設定做」或改答案就會回到還要處理。紅字的兩類（名字換不了代號、重疊缺東西）不處理也能開始，紅字寫了略過的後果。</p>` : "<p class=\"muted\">沒有要處理的列。</p>"}
      </details>
      <details class="fc-fold" data-fcfold="請看一眼" ${openLook ? "open" : ""}><summary><b>${esc(hd.look)}</b> <span class="fc-foldbtn">展開查看</span></summary>
      <div class="fc-lookhead" id="fcLookHead">
        <p>已看過 <b id="fcSeenCount">${seenN}</b>／${look.length} 處</p>
        ${look.length ? `<button class="${fc["看過"] ? "" : "secondary "}small" id="fcSeenAll" data-on="${fc["看過"] ? "0" : "1"}" aria-pressed="${!!fc["看過"]}">${fc["看過"] ? "✓ 全部看過了・再按取消" : "全部看過了"}</button>` : ""}
      </div>
      ${newN && !fc["看過"] ? `<p class="hint" id="fcSeenReset">全部看過之後，「請看一眼」多了 ${newN} 列（標「新的」），看過之後在那一列按「我看過了」。</p>` : ""}
      ${look.length ? `<table class="kv fc-check fc-look">${look.map((r) => row(r, true)).join("")}</table>` : "<p class=\"muted\">沒有剪掉、消音、超過 10 秒的老師重念。</p>"}
      </details>
      <p class="muted">自動算處理好的（被別筆涵蓋）：${(m["自動算處理好"] || []).length} 筆
        ${(m["自動算處理好"] || []).length ? `<details><summary>展開</summary>${m["自動算處理好"].map((x) => `${esc(x["名稱"] || fcTime(x.start))} 由〈${esc(x["涵蓋"])}〉涵蓋`).join("<br>")}</details>` : ""}</p>
      <p class="muted">要生成約 ${Math.round((m["要生成秒數"] || 0) / 60)} 分鐘的聲音${SHOW_EXEC_ETA ? `，預估 ${((m["預估秒數"] || 0) / 3600).toFixed(1)} 小時（含組裝約 21 分鐘）` : ""}；硬碟可用 ${m["硬碟可用GB"]} GB。${esc(m["提醒"] || "")}</p>
      ${mem ? `<p class="${mem["偏滿"] ? "hint fc-mem-warn" : "muted"}" id="fcMem">${mem["偏滿"] ? "<b>記憶體偏滿：</b>" : ""}${esc(mem["說明"])}。${mem["偏滿"] ? esc(mem["怎麼處理"]) : ""}</p>` : ""}
      <audio id="fcAudio" preload="none"></audio>
    </div></details>`;
}

function bindFinalCheck(reload) {
  document.querySelectorAll("details[data-fcfold]").forEach((d) => d.addEventListener("toggle", () => { execOpen[d.dataset.fcfold] = d.open; }));
  const whole = document.getElementById("fcWhole");
  if (whole) whole.addEventListener("toggle", () => fcSetCheckOpen(whole.open));
  document.querySelectorAll("[data-fcplay]").forEach((b) => b.addEventListener("click", () => {
    const [a, e] = b.dataset.fcplay.split("|").map(Number);
    const au = document.getElementById("fcAudio");
    au.src = `/api/audio?start=${a.toFixed(2)}&end=${e.toFixed(2)}`;   // 10-05 #176：只播這一段（以前前後各多 1 秒）
    au.play().catch(() => {});
  }));
  // 10-01 1-2：直接跳到第 3 步那一張卡片（重疊打開「改做法」，看得到學員是誰的選單）
  document.querySelectorAll("[data-fcgo]").forEach((b) => b.addEventListener("click", () => {
    const r = fcRowsCache[Number(b.dataset.fcgo)];
    rvJump({ key: r["第3步"], openMore: r["第3步"].startsWith("重疊:"), note: `從開始前總檢查過來：${r["去改"] || r["說明"]}`, backKey: r.key });
  }));
  // 10-01 1-3：聽了有學員的聲音 → 帶著這段時間去第 3 步新增，或把旁邊那一筆的起訖改大
  document.querySelectorAll("[data-fcpath]").forEach((b) => b.addEventListener("click", () => {
    const [i, j] = b.dataset.fcpath.split("|").map(Number);
    const r = fcRowsCache[i], p = r["有學員聲音"][j];
    const edit = p["改時間"] || p["新增"];
    rvJump({ key: p["第3步"] || null, edit, note: `從開始前總檢查過來：${p["文字"]}（${fcTime(r.start)}–${fcTime(r.end)}）。下面的起訖已經填好，聽過沒問題按「${p["改時間"] ? "儲存修改" : "新增"}」`, backKey: r.key });
  }));
  // 10-02 第六批：老師重念範圍前後沒有人講話 →「照建議縮小」（改的是第 3 步同一個地方；不自動改）
  document.querySelectorAll("[data-fcshrink]").forEach((b) => b.addEventListener("click", async () => {
    const h = fcRowsCache[Number(b.dataset.fcshrink)]["縮小"];
    if (!(await uiConfirm({ title: `把重念範圍改成 ${fcTime(h["建議"][0])}–${fcTime(h["建議"][1])}（原片時間）？`, message: "這一句之後要重新生成。", ok: "照建議縮小", cancel: "先不要" }))) return;
    try { await apiPost("/api/review/shrink", { "鍵": h["鍵"] }); } catch (err) { uiErrorAlert({ title: "沒有成功", message: err.message }); return; }
    await reload();
  }));
  // 10-05 #177：「回第 3 步補」（名字換不了代號、重疊缺東西）
  document.querySelectorAll("[data-fcback]").forEach((b) => b.addEventListener("click", () => rvJump(fcBack3Go(fcRowsCache[Number(b.dataset.fcback)]))));
  // 10-02 第四批：切在段落外面的幾秒，在這裡直接改答案（存到第 3 步同一個地方）
  document.querySelectorAll("[data-fcout]").forEach((b) => b.addEventListener("click", async () => {
    const [i, v] = b.dataset.fcout.split("|");
    const r = fcRowsCache[Number(i)];
    const [a, e] = r["段落外起訖"] || [r.start, r.end];
    try { await apiPost("/api/review/outside", { "鍵": r.key, start: a, end: e, "答案": v || null }); }
    catch (err) { uiErrorAlert({ title: "沒有成功", message: err.message }); return; }
    await reload();
  }));
  const fold = document.getElementById("fcFold");
  if (fold) fold.addEventListener("change", async () => { fcSetFoldDone(fold.checked); await reload(); });
  const unfold = document.getElementById("fcUnfold");
  if (unfold) unfold.addEventListener("click", async () => { fcSetFoldDone(false); await reload(); });
  // 10-05 #177：「照目前設定做」／再按取消
  document.querySelectorAll("[data-fckeep]").forEach((b) => b.addEventListener("click", async () => {
    try { await apiPost("/api/execute/finalcheck", { key: b.dataset.fckeep, "照目前設定做": b.dataset.on === "1" }); }
    catch (err) { if (!err.shown) uiErrorAlert({ title: "沒有成功", message: err.message }); return; }   // 不開放的類別、列已經不在：伺服器回的原因
    await reload();
  }));
  // 10-05 #178：「請看一眼」每一列的「我看過了」、整區的「全部看過了」（以前是最下面的一個勾選）
  document.querySelectorAll("[data-fclook]").forEach((b) => b.addEventListener("click", async () => {
    try { await apiPost("/api/execute/finalcheck", { "看一眼": b.dataset.fclook, "看過": b.dataset.on === "1", "看過的列": fcLookCache }); }
    catch (err) { if (!err.shown) uiErrorAlert({ title: "沒有成功", message: err.message }); return; }
    await reload();
  }));
  const seenAll = document.getElementById("fcSeenAll");
  if (seenAll) seenAll.addEventListener("click", async () => {
    try { await apiPost("/api/execute/finalcheck", { "看過": seenAll.dataset.on === "1", "看過的列": fcLookCache }); }
    catch (err) { if (!err.shown) uiErrorAlert({ title: "沒有成功", message: err.message }); return; }
    await reload();
  });
}

async function renderExecuteBody() {
  const d = await apiGet("/api/execute");
  if (execRedoGoBack(d)) return;   // 10-15：從第 5 步「重做這幾張」來的，跑完了就回第 5 步
  let fc = null;
  try { if (!d.running) fc = await apiGet("/api/execute/finalcheck"); } catch (e) { fc = null; }
  const pre = d["前置檢查"];
  const prog = d["進度"] || {};
  const rows = execStepRows(d);
  const running = d.running;
  const redo = execRedoRows(d);   // 10-15 總檢查修正（1015-7）：讀後端的 要重做（needs.redo_cards），沒有時讀舊的 退回清單
  const execBlocked = running || !pre["可以開始"];   // 10-08 宇軒（流程簡化）：總檢查預設先略過，不擋
  // 10-15 第5步2.0 第3段（31 號設計 §5.3）：範圍「從／到」與「只重新組裝」只有開發者設定（settings.toml `developer = true`）才顯示；
  // 老師的畫面一律整支、照卡片重做（伺服器沒開開發者設定時收到範圍也不理）
  const dev = !!d["開發者"];
  const ra = dev ? d["只重新組裝"] : null;
  const autoBtn = pre["缺代號"] && !running;   // 1016-3：「按下面『幫還沒代號的自動配』」只在下面真的有這顆時才這樣寫
  // 10-20 總審查：說明改說「英文名」（跟 ③ 一致），沒有英文名的照原聲留著（#246），不再寫「都要有代號」
  const btn = execBtnState(running, prog, !!d["有成品"]);   // 10-08：按鈕文字照狀態   // 10-05 #97：沒有退回時也能只重新組裝（伺服器算好能不能按）
  contentEl.innerHTML = `
    <h1>4　AI 執行</h1>
    <p class="exec-now" id="execNow"><b>${esc(btn.status)}</b>${!running && pre["缺"].length ? ` <button type="button" class="secondary" id="execBlockJump">還缺 ${pre["缺"].length} 件事才能開始，看是哪些</button>` : ""}</p>
    <p class="muted">依序跑四步：老師提到名字 → 學員重念 → 保留原聲學員講到名字 → 組裝成品。每一步都可以中斷續跑，已經做過的跳過；做完到第 5 步「成品檢查」。</p>
    <div class="hint">從第 4 步直接開始（例如老師已經自己看完全片、挑好參考聲音）：<b>第 1 步轉文字還是要跑</b>（學員的話要照逐字稿重念，電腦自動）；
      <b>第 2 步的老師參考音也要選好</b>，會出現的名字要有英文名，沒有的照原聲留著（${autoBtn ? "要換的按下面「幫還沒代號的自動配」" : "要換的到第 3 步 ③ 按「幫還沒英文名的自動配」"}）。
      能省掉的是第 3 步逐筆覆核的人工：沒覆核的話，照第 1 步的建議做（名字的那一句老師重念、學員段落全部學員重念、建議剪掉的段落不剪）。
      第 3 步「開始前 4 件事」只有 ② 學員是誰一定要做（沒配代號的學員，AI 重念會念出本名）。</div>
    ${pre["提醒"].length ? `<p class="muted">提醒：${esc(pre["提醒"].join("；"))}</p>` : ""}
    ${autoBtn ? `<p><button class="secondary" id="btnAutoCode">幫還沒代號的自動配</button> <span class="muted">從代號名單配（外國人名的中文寫法），之後在第 3 步 ②③ 可以改</span></p>` : ""}
    <h2>要修改的項目</h2>
    <div class="card" id="execStats">${execStatsHtml(d)}</div>
    ${fc ? finalCheckHtml(fc) : ""}
    <h2>執行步驟${running ? "" : "（現在的狀態）"}</h2>
    <div class="card"><table class="kv exec">
      <thead><tr><th style="text-align:left">步驟</th><th style="text-align:left">狀態</th><th style="text-align:left">說明</th></tr></thead>
      <tbody id="execSteps">${rows}</tbody></table>
      ${prog["開始時間"] ? `<p class="muted small">上次執行：${esc(fmtStamp(prog["開始時間"]))} 開始${prog["結束時間"] ? `，${esc(fmtStamp(prog["結束時間"]))} 結束` : ""}；範圍 ${esc(fmtRange(prog["範圍"]))}</p>` : ""}
    </div>
    <div id="execTimeBox">${execTimeHtml(d["執行時間"])}</div>
    <div class="card">
      ${dev ? `<div class="exec-opts">
        <label>從 <input type="text" id="exStart" class="short" placeholder="0:00"></label>
        <label>到 <input type="text" id="exEnd" class="short" placeholder="${esc(d["影片長度"] ? fmtRange([0, d["影片長度"]]).split("–")[1] : "結尾")}"></label>
      </div>` : ""}
      <p class="muted">輸出方式：<span id="exMethodName">${esc(execMethodName(d, (d["預設輸出做法"] || ["sw"])[0]))}</span><span id="exMethodNote">${(d["預設輸出做法"] || ["sw"])[0] === "sw" ? "（Mac、Windows 都一樣）" : ""}</span></p>
      <details class="adv"><summary>進階設定</summary>
        <label>輸出方式 <select id="exMethod">${(d["輸出做法選項"] || [["sw", "標準輸出"]]).map(([m, label]) => `<option value="${m}" ${d["預設輸出做法"].includes(m) ? "selected" : ""}>${esc(label)}</option>`).join("")}</select></label>
        <p class="muted">一般用標準輸出就好。其他方式只有這台電腦支援時才會列出來。</p>
        ${ra && !redo.length && !running ? `<p class="exec-reasm"><button id="btnReassembleAll" class="secondary" ${execBlocked || !ra["可以"] ? "disabled" : ""}>只重新組裝（不重新生成）</button>
          ${!execBlocked && !ra["可以"] ? `<span class="muted" id="reasmWhy">${esc(ra["原因"] || "")}</span>` : ""}
          <span class="muted small" style="display:block">聲音都生成好了，只想用現在的程式把成品重新組裝一次（例如工具更新之後）。不會重新生成聲音${SHOW_EXEC_ETA ? `，整支約 ${Math.round((ra["預估秒數"] || 0) / 60)} 分鐘` : ""}。</span></p>` : ""}
      </details>
      ${!running && pre["缺"].length ? `<div class="exec-block" id="execBlockNear" role="alert"><b>還不能開始，要先處理這 ${pre["缺"].length} 件事：</b>${execBlockList(pre)}</div>` : ""}
      <button id="btnExec" ${execBlocked ? "disabled" : ""}${!running && !pre["可以開始"] ? ` title="要先處理上面列的事"` : ""}>${esc(btn.label)}</button>${btn.note ? ` <span class="muted small" id="execDoneNote">${esc(btn.note)}</span>` : ""}
      ${fc && !running && pre["可以開始"] ? `<span class="muted" id="execSkipNote">${esc(execSkipNote(fc))}</span>` : ""}
      ${running ? `<button id="btnStop" class="secondary" ${d["停止中"] ? "disabled" : ""}>${d["停止中"] ? "停止中…（等目前這一句生成完）" : "停止"}</button>` : ""}
      <span class="muted" id="execEta">${execEtaText(d)}</span>
      ${!running && fc && ((fc["摘要"] || {})["記憶體"] || {})["偏滿"] ? `<p class="hint fc-mem-warn" id="execMemWarn"><b>記憶體偏滿，按下去可能跑到一半就被停下來。</b>${esc(fc["摘要"]["記憶體"]["怎麼處理"])}</p>` : ""}
      ${!running ? `<p class="muted">跑之前先關掉瀏覽器其他分頁與用不到的程式：同時開著別的事，生成會慢三倍以上，記憶體不夠還可能中途停下來。</p>` : ""}
      ${running ? `<p class="muted">按「停止」會等目前這一句生成完才停，做好的都留著，下次按「繼續執行」接著做；組裝中按的話，要等組裝做完才停。</p>` : ""}
      ${!running && prog["停止"] ? `<p><span class="badge">已停止</span> ${prog["停止原因"] && !prog["停止原因"].startsWith("按了停止")
        ? esc(prog["停止原因"].replace("（swap）", "")) : "上次按了停止；按「繼續執行」會接著做（做好的不重做）。"}</p>` : ""}
      ${!running && prog["中斷"] ? `<p><span class="badge error">中斷</span> 上次跑到一半網頁伺服器被關掉了；按「繼續執行」會接著做（做好的不重做）。</p>` : ""}
      ${d.error ? `<p><span class="badge error">失敗</span> ${esc(d.error)}</p>` : ""}
      <div id="execAwake">${awakeLine(d)}</div>
      <div class="log" id="execLog">${(d.messages || []).map(esc).join("\n") || "（還沒有訊息）"}</div>
    </div>
    ${redo.length || d["重做中"] ? `<h2>第 5 步要重做的（${redo.length} 張）</h2>
      <div class="card"><p class="muted">按「開始執行」或下面這顆，會只重做這幾張：要重新生成的那幾句先清掉、重新生成（其他做好的不重做），最後重新組裝。
        要念的字和範圍都沒改的，會換一種念法重新生成，每按一次「要修改」換一次，不會回到用過的念法；上一版的聲音留著備份。
        做完到第 5 步，這幾張會回到「還沒看」、標「重做過」。${d["重做中"] ? "<b>上次重做還沒做完</b>，再按一次會接著做。" : ""}</p>
      <p><button id="btnRedo" ${execBlocked ? "disabled" : ""}>${redo.length ? `只重做這幾張（${redo.length} 張）` : "接著做上次沒做完的重做"}</button>
        ${dev ? `<button id="btnReassemble" class="secondary" ${execBlocked ? "disabled" : ""}>只重新組裝（不重新生成）</button>` : ""}
        ${execBlocked && !running ? `<span class="muted">要先能按「開始執行」（見上面）</span>` : ""}</p>
      ${dev ? `<p class="muted small">「只重新組裝」：生成的聲音不動（不重新生成、不換念法），只照現在的做法重新放回去、輸出新的成品；
        會多一個版本，舊的成品檔不動。只換了接起來的地方、聲音本身沒問題時用這顆；要重做的卡沒有真的重新生成，第 5 步照樣是「要重做」。</p>` : ""}
      <table class="kv">${redo.map((it) => `<tr><td>${esc(it.kind)}${it.name ? `　${esc(it.name)}` : ""}
        ${it.when ? `<div class="muted">${esc(it.when)}</div>` : ""}</td>
        <td>${esc(it.why)}${it.range ? `<div class="muted">${esc(it.range)}</div>` : ""}
          ${it.how ? `<div class="muted">按下去會：${esc(it.how)}</div>` : ""}</td></tr>`).join("")}</table></div>` : ""}`;
  bindFinalCheck(renderExecuteBody);
  // 1024-1（#274）：「到第 N 步」、最上面「還缺幾件事」
  document.querySelectorAll("[data-gostep]").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.gosub) navSubWant = b.dataset.gosub;   // 審查建議 3：② 學員是誰 → 直接打開第 3 步「開始前 4 件事」（review.js 讀 navSubWant）
    location.hash = `#step${b.dataset.gostep}`;
  }));
  const blockJump = document.getElementById("execBlockJump");
  if (blockJump) blockJump.addEventListener("click", () => {
    const el = document.getElementById("execBlockNear");
    if (el) { el.scrollIntoView({ block: "center" }); el.classList.add("exec-block-flash"); setTimeout(() => el.classList.remove("exec-block-flash"), 1500); }
  });
  if (execGoCheck || execBackRow) {
    const key = execBackRow;
    execGoCheck = null;
    execBackRow = null;
    const row = key ? [...document.querySelectorAll("tr[data-fckey]")].find((tr) => tr.dataset.fckey === key) : null;
    const t = document.getElementById("fcCheckTitle");
    if (row) { row.scrollIntoView({ block: "center" }); row.classList.add("fc-back"); }
    else if (t) t.scrollIntoView({ block: "start" });
  }
  const exMethod = document.getElementById("exMethod");   // 進階設定改了輸出方式：上面那一行跟著改
  if (exMethod) exMethod.addEventListener("change", () => {
    const nm = document.getElementById("exMethodName");
    if (nm) nm.textContent = execMethodName(d, exMethod.value);
    const note = document.getElementById("exMethodNote");
    if (note) note.textContent = exMethod.value === "sw" ? "（Mac、Windows 都一樣）" : "（進階設定改的）";
  });
  const redoBtn = document.getElementById("btnRedo");   // 10-01 第三批：只重做第 5 步要重做的（整支影片的範圍）
  if (redoBtn) redoBtn.addEventListener("click", async () => {
    if (!(await uiConfirm({ title: `只重做第 5 步要重做的 ${redo.length} 張，再重新組裝？`, ok: `重做這 ${redo.length} 張`, cancel: "先不要",
      message: "要重新生成的那幾句會先清掉（舊的聲音檔留著備份），其他做好的不重做。" }))) return;
    try { await apiPost("/api/execute/start", { start: null, end: null, methods: [document.getElementById("exMethod").value] }, { quiet: true }); }
    catch (e) { uiErrorAlert({ title: "無法開始", message: e.message, where: "第 4 步・開始執行" }); return; }
    await renderExecuteBody();
  });
  const reBtn = document.getElementById("btnReassemble");   // 10-03 第八批 #23：只重新組裝（整支影片的範圍）
  if (reBtn) reBtn.addEventListener("click", async () => {
    // 10-15 總檢查修正（1015-7）：只重新組裝不會重新生成，等重做的卡留著（後端 build_version 只清真的重做的）——不再說「回到還沒看」
    if (!(await uiConfirm({ title: "只重新組裝？", ok: "只重新組裝", cancel: "先不要",
      message: `生成的聲音不動、不重新生成，照現在的做法重新放回去。\n會多一個版本，舊的成品檔不動；第 5 步要重做的 ${redo.length} 張沒有重新生成，還是「要重做」。` }))) return;
    try { await apiPost("/api/execute/start", { start: null, end: null, methods: [document.getElementById("exMethod").value], "只重新組裝": true }, { quiet: true }); }
    catch (e) { uiErrorAlert({ title: "無法開始", message: e.message, where: "第 4 步・開始執行" }); return; }
    await renderExecuteBody();
  });
  const reAllBtn = document.getElementById("btnReassembleAll");   // 10-05 #97：沒有退回時的只重新組裝（整支影片的範圍）
  if (reAllBtn) reAllBtn.addEventListener("click", async () => {
    // 10-15 總檢查修正（1015-7）：0.2.2b1 起每次組裝都是一個新版本（舊的成品檔不蓋掉）、之前的通過沿用
    if (!(await uiConfirm({ title: "只重新組裝（不重新生成）？", ok: "只重新組裝", cancel: "先不要", message: [
      "・生成好的聲音都不動，不會重新生成。",
      "・會多一個版本，舊的成品檔不動；之前的通過沿用。",
      "・第 5 步：內容變了的（例如接縫、停格的做法改了）回到「還沒看」，那附近看過的部分也要重看。",
      `・${SHOW_EXEC_ETA ? `整支約 ${Math.round((ra["預估秒數"] || 0) / 60)} 分鐘，` : ""}跑的時候第 3 步不能修改。`].join("\n") }))) return;
    try { await apiPost("/api/execute/start", { start: null, end: null, methods: [document.getElementById("exMethod").value], "只重新組裝": true }, { quiet: true }); }
    catch (e) { uiErrorAlert({ title: "無法開始", message: e.message, where: "第 4 步・開始執行" }); return; }
    await renderExecuteBody();
  });
  document.getElementById("btnExec").addEventListener("click", async () => {
    const st = document.getElementById("exStart"), en = document.getElementById("exEnd");   // 開發者設定才有這兩格
    const body = { start: (st && st.value.trim()) || null, end: (en && en.value.trim()) || null,
      methods: [document.getElementById("exMethod").value] };
    try { await apiPost("/api/execute/start", body, { quiet: true }); } catch (e) { uiErrorAlert({ title: "無法開始", message: e.message, where: "第 4 步・開始執行" }); return; }
    await renderExecuteBody();
  });
  const auto = document.getElementById("btnAutoCode");   // 09-30：前置檢查缺代號時直接配
  if (auto) auto.addEventListener("click", async () => {
    auto.disabled = true;
    try {
      const r = await apiPost("/api/codes/auto", {});
      if (r["代號不夠"]) uiAlert({ title: "代號不夠用", message: "代號名單不夠用，剩下的請到第 3 步 ②③ 自己打新代號。" });
    } catch (e) { return; }
    await renderExecuteBody();
  });
  const stop = document.getElementById("btnStop");
  if (stop) stop.addEventListener("click", async () => {
    stop.disabled = true;
    stop.textContent = "停止中…（等目前這一句生成完）";
    try { await apiPost("/api/execute/stop", {}, { quiet: true }); } catch (e) { uiErrorAlert({ title: "停不了", message: e.message, where: "第 4 步・停止" }); }
  });
  if (running) startExecPoll();
}

// 「開始執行」旁邊一行（純函式）：總檢查預設略過幾列
function execSkipNote(fc) {
  const h = fcFoldHeads(fc);
  if (!h.mustLeft && !h.lookLeft) return "開始前總檢查都處理好了";
  return `開始前總檢查預設略過：一定要處理 ${h.mustLeft} 列、請看一眼 ${h.lookLeft} 處（照目前設定做；第 5 步「需留意」看得到）`;
}

// 10-08 宇軒（跑完之後按鈕還寫「開始執行」，老師以為哪裡出錯）：第 4 步按鈕文字與頂端一行狀態（純函式）
// 10-08 宇軒：第 4 步「執行時間」一塊（純函式）。t＝GET /api/execute 的「執行時間」（execute.run_times）
function execDur(s) {
  if (s == null) return "—";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return h ? `${h} 小時 ${m} 分` : m ? `${m} 分 ${x} 秒` : `${x} 秒`;
}
const EXEC_STOP_WORD = { "停止": "按了停止", "失敗": "失敗", "中斷": "伺服器被關掉" };
function execTimeHtml(t) {
  if (!t) return "";
  const steps = Object.entries(t["各步秒"] || {});
  const stops = t["中斷"] || [];
  return `<h2>執行時間</h2><div class="card" id="execTime">
    ${t["執行中"] ? `<p><b>已執行 ${esc(execDur(t["已執行秒"]))}</b>（這一次 ${esc(fmtStamp(t["開始"]))} 起算的這一輪還在跑）</p>` : ""}
    <table class="kv">
      <tr><td>總經過時間</td><td>${esc(execDur(t["總經過秒"]))}<span class="muted">（第一次按開始 ${esc(fmtStamp(t["開始"]))} → ${t["執行中"] ? "現在" : `最後一次結束 ${esc(fmtStamp(t["結束"]))}`}）</span></td></tr>
      <tr><td>實際執行時間</td><td>${esc(execDur(t["實際執行秒"]))}<span class="muted">（${t["執行次數"]} 次執行加起來，不含中間停下來的時間${t["算不出的次數"] ? `；有 ${t["算不出的次數"]} 次沒有記到結束時間，沒算進去` : ""}）</span></td></tr>
      <tr><td>中斷</td><td>${stops.length ? `${stops.length} 次：${stops.map((x) => `${esc(fmtStamp(x["時間"]))}（${esc(EXEC_STOP_WORD[x["結果"]] || x["結果"])}）`).join("、")}` : "沒有"}</td></tr>
      ${steps.length ? `<tr><td>各步</td><td>${steps.map(([k, v]) => `${esc(k)} ${esc(execDur(v))}`).join("；")}</td></tr>` : ""}
    </table>
    ${t["有歷史"] ? "" : `<p class="muted">舊版沒有記錄更早的執行：只算得到最後這一次。</p>`}
  </div>`;
}

// 10-09 宇軒定：開始執行 →（中斷／停止／失敗後）繼續執行 →（跑完有成品）更新成品
const EXEC_UPDATE_NOTE = "只補做第 3 步改過的部分，做過的不重做";
// 1024-1（#274）：「開始執行」按不下去時，逐條列出缺什麼；有對應步驟的附「到第 N 步」（第 0 步叫「初始化設定」）。
// 舊伺服器沒有「缺明細」時，照「缺」的原文列（沒有連結）
function execBlockList(pre) {
  const items = (pre["缺明細"] && pre["缺明細"].length) ? pre["缺明細"] : (pre["缺"] || []).map((t) => ({ "說明": t, "步驟": null }));
  const go = (n) => (n === 0 ? "到初始化設定" : `到第 ${n} 步`);
  return `<ul class="exec-block-list">${items.map((it) => `<li><span>${esc(it["說明"])}</span>${Number.isInteger(it["步驟"])
    ? ` <button type="button" class="secondary exec-go" data-gostep="${it["步驟"]}"${it["子步驟"] ? ` data-gosub="${esc(it["子步驟"])}"` : ""}>${go(it["步驟"])}</button>` : ""}${it["細節"]
    ? `<div class="muted small exec-block-more">${esc(it["細節"])}</div>` : ""}</li>`).join("")}</ul>`;
}

function execBtnState(running, prog, hasProduct) {
  prog = prog || {};
  const when = prog["開始時間"] ? fmtStamp(prog["開始時間"]) : "";
  const end = prog["結束時間"] ? fmtStamp(prog["結束時間"]) : when;
  const assembled = ((prog["步驟"] || {})["組裝"] || {})["狀態"] === "做完";
  const old = hasProduct ? "第 5 步看的是上一支完整的成品（沒有被動到）" : "第 5 步還沒有成品";
  if (running) return { label: "執行中…", note: "", status: `現在：第 4 步執行中（${when} 開始）。${hasProduct ? "做完之前，第 5 步看的還是上一支成品" : "做完才會有成品"}` };
  if (prog["中斷"]) return { label: "繼續執行", note: "", status: `現在：上次執行（${when} 開始）跑到一半網頁伺服器被關掉。${old}` };
  if (prog["停止"]) return { label: "繼續執行", note: "", status: `現在：上次執行（${when} 開始）跑到一半被停止。${old}` };
  if (prog["錯誤"]) return { label: "繼續執行", note: "", status: `現在：上次執行（${when} 開始）沒有跑完（失敗）。${old}` };
  if (prog["開始時間"] && prog["結束時間"] && (assembled || hasProduct))
    return { label: "更新成品", note: EXEC_UPDATE_NOTE, status: `現在：已完成（${end}），到第 5 步檢查成品；改了第 3 步按「更新成品」` };
  if (hasProduct) return { label: "更新成品", note: EXEC_UPDATE_NOTE, status: "現在：有成品，到第 5 步檢查；改了第 3 步按「更新成品」" };
  return { label: "開始執行", note: "", status: "現在：還沒執行過，第 5 步還沒有成品" };
}

const EXEC_STEP_NAMES = [["老師名字", "老師提到名字：老師重念（名字換成代號）"], ["學員重念", "學員段落：學員重念（用替代聲音）"],
  ["保留原聲學員名字", "保留原聲的學員講到名字：選了換成代號的，用他自己的聲音重念"], ["組裝", "換聲音＋剪掉＋停格，輸出成品影片"]];

// 10-01 第三批 8：輸出方式的名稱（「硬體編碼」這類說法收進進階設定）
function execMethodName(d, m) { return ((d["輸出做法選項"] || []).find((x) => x[0] === m) || [m, "標準輸出"])[1]; }

function execStepRows(d) {
  const steps = (d["進度"] || {})["步驟"] || {};
  const now = d["現在狀態"];   // 10-01 第三批 4：沒在執行時顯示現在的狀態（跟上面的統計同一個判斷），不是上一次執行的結果
  if (!d.running && now) return EXEC_STEP_NAMES.map(([k, desc]) => {
    const x = now[k] || {};
    const st = x["做好了"] == null ? "讀不到" : x["做好了"] ? "做好了" : "還沒做";
    const last = (steps[k] || {})["狀態"];
    return `<tr><td><b>${esc(k)}</b><div class="muted">${esc(desc)}</div></td>
      <td><span class="badge ${x["做好了"] ? "done" : ""}">${esc(st)}</span>${last ? `<div class="muted small">上次執行：${esc(last)}</div>` : ""}</td>
      <td class="muted">${esc(x["說明"] || "")}</td></tr>`;
  }).join("");
  const badge = (st) => ({ "做完": "done", "跳過": "done", "進行中": "running", "失敗": "error", "中斷": "error" }[st] || "");
  return EXEC_STEP_NAMES.map(([k, desc]) => {
    const st = (steps[k] || {})["狀態"] || "還沒跑";
    return `<tr><td><b>${esc(k)}</b><div class="muted">${esc(desc)}</div></td>
      <td><span class="badge ${badge(st)}">${esc(st)}</span></td><td class="muted">${esc((steps[k] || {})["訊息"] || "")}</td></tr>`;
  }).join("");
}

// 09-30：預估剩餘時間（從每一句生成花的時間推算，只算生成類；組裝另外算）
function execEtaText(d) {
  const s = d["預估剩餘秒數"];
  if (!d.running) return "";
  if (s != null && s <= 0) return "生成都做完了，接著組裝";
  if (!SHOW_EXEC_ETA) return "";   // 10-05 宇軒：預估時間先不顯示
  if (s == null) return "預估剩餘時間：第一句生成完才算得出來";
  const m = Math.round(s / 60);
  return `預估生成還要約 ${m >= 60 ? `${Math.floor(m / 60)} 小時 ${m % 60} 分` : `${Math.max(1, m)} 分`}（不含組裝）`;
}

// 逐類統計：每一類要改幾筆、做完幾筆（09-29 宇軒）。生成類邊跑邊跳；消音、刪除在組裝做完才算完成
function execStatsHtml(d) {
  const rows = d["統計"] || [];
  if (!rows.length) return `<p class="muted">還算不出來（第 1 步影片分析要先跑完）</p>`;
  const ok = rows.filter((r) => r["總數"] != null);
  const total = ok.reduce((n, r) => n + r["總數"], 0), done = ok.reduce((n, r) => n + r["完成"], 0);
  const pct = (a, b) => (b ? Math.round((a / b) * 100) : 100);
  const body = rows.map((r) => {
    if (r["總數"] == null) return `<tr><td><b>${esc(r["類型"])}</b><div class="muted">${esc(r["做法"])}</div></td><td class="num muted" colspan="2">讀不到</td></tr>`;
    const p = pct(r["完成"], r["總數"]);
    const made = r["已生成"] != null && r["已生成"] > r["完成"] ? `<div class="muted">已生成 ${r["已生成"]}／${r["總數"]} 句</div>` : "";
    return `<tr class="${r["總數"] ? "" : "zero"}"><td><b>${esc(r["類型"])}</b><div class="muted">${esc(r["做法"])}${r["階段"] === "組裝" ? "（組裝時處理）" : ""}</div>${r["另外"] ? `<div class="muted">${esc(r["另外"])}</div>` : ""}</td>
      <td class="num"><b>${r["完成"]}</b>／${r["總數"]}${made}</td>
      <td>${r["總數"] ? `<span class="bar" role="progressbar" aria-valuenow="${p}" aria-valuemin="0" aria-valuemax="100" aria-label="${esc(r["類型"])} ${esc(r["做法"])}"><i style="width:${p}%"></i></span>` : `<span class="muted">沒有</span>`}</td></tr>`;
  }).join("");
  return `<p class="exec-total">全部 <b>${done}</b>／${total} 筆完成（${pct(done, total)}%）</p>
    <table class="kv exec-stats"><thead><tr><th style="text-align:left">類型</th><th style="text-align:right">完成／總數</th><th></th></tr></thead>
    <tbody>${body}</tbody></table>`;
}

// 10-15 總檢查修正（1015-7）：（純函式）第 4 步「第 5 步要重做的（N 張）」那一區每一列：{kind, name, when, why, range, how}。
// 後端有 要重做（needs.redo_cards，每筆 {卡號, 類型, 原片: [s, e], 原因: {選項, 補充}}）就用它——N 跟第 5 步「重做這幾張（N 張）」
// 同一份清單；沒有（後端還沒給）時照舊讀 退回清單（{類型, 覆核名稱, 原片, 原因（字串）, 改範圍, 說明}）
function execRedoRows(d) {
  const why = (w) => {
    if (!w || typeof w !== "object") return String(w || "");
    const opts = (w["選項"] || []).join("、"), extra = String(w["補充"] || "").trim();
    return opts && extra ? `${opts}：${extra}` : opts || extra || "（沒寫原因）";
  };
  if (Array.isArray(d["要重做"])) {
    return d["要重做"].map((it) => {
      const m = /^K0*(\d+)$/.exec(String(it["卡號"] || ""));
      return { kind: it["類型"] || "", name: m ? `#${m[1]}` : String(it["卡號"] || ""), when: Array.isArray(it["原片"]) ? `原片 ${fmtRange(it["原片"])}` : "",
        why: why(it["原因"]), range: execRangeText(it["改範圍"]), how: it["說明"] || "" };
    });
  }
  return (d["退回清單"] || []).map((it) => ({ kind: it["類型"] || "", name: (it["覆核名稱"] || []).join("、") || "—",
    when: Array.isArray(it["原片"]) ? `原片 ${fmtRange(it["原片"])}` : "", why: why(it["原因"]),
    range: execRangeText(it["改範圍"]), how: it["說明"] || "" }));
}
// 第 4 步清單「範圍改了」那一行（審查建議 8：要重做、舊的退回清單共用）
function execRangeText(r) {
  return r ? `範圍改了${r["原本"] ? `：${fmtRange(r["原本"])} → ${fmtRange(r["改成"])}` : ""}（存在第 3 步〈${r["名稱"] || ""}〉）` : "";
}

function fmtRange(r) {
  if (!r) return "—";
  const f = (t) => { t = Math.round(t); const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
    return `${h ? h + ":" + String(m).padStart(2, "0") : m}:${String(s).padStart(2, "0")}`; };
  return `${f(r[0])}–${f(r[1])}`;
}

// 10-07 #172：第 1、4 步跑的時候固定顯示防睡眠開成沒有（伺服器的「防睡眠」欄位；執行訊息會被洗掉，這行不會）
function awakeLine(s) {
  const t = s && s["防睡眠"];
  if (!t || !(s.running || t.startsWith("沒開成"))) return "";
  return `<p><span class="badge ${t.startsWith("沒開成") ? "error" : ""}">防睡眠</span> ${esc(t)}</p>`;
}

// 10-15 第5步2.0 第2段：第 5 步按「重做這幾張」跳過來的，這一次執行好好跑完就自動跳回第 5 步（純函式）。
// since＝那一次執行的開始時間（伺服器 /api/execute/start 回傳的 started_at，秒）。10-15 第2段審查修正（建議）：
// 以前比瀏覽器時間（WSL 時鐘會差），改成比伺服器時間：started_at 是同一次（或之後）才算
// 回傳 "回第5步"（跑完了）、"留下"（停止、失敗、中斷：停在第 4 步照現有提示）、null（還在跑、或不是從第 5 步來的）
function execRedoReturn(d, since) {
  if (!since || !d || d.running) return null;
  if (!d.started_at || d.started_at < since - 0.001) return null;   // 不是這一次（更早的那一次）
  const prog = d["進度"] || {};
  if (d.error || d.stopped || prog["停止"] || prog["錯誤"] || prog["中斷"]) return "留下";
  return d.finished_at ? "回第5步" : null;
}

function execRedoGoBack(d) {
  const r = execRedoReturn(d, typeof fcRedoBackGet === "function" ? fcRedoBackGet() : null);
  if (!r) return false;
  fcRedoBackClear();
  if (r !== "回第5步") return false;
  location.hash = "#step5";
  return true;
}

function startExecPoll() {
  if (execPollTimer) clearInterval(execPollTimer);
  execPollTimer = setInterval(async () => {
    if (currentRouteId() !== "step4") { clearInterval(execPollTimer); execPollTimer = null; return; }
    try {
      const d = await apiGet("/api/execute");
      pollOk();
      const el = document.getElementById("execLog");
      if (el) { el.textContent = (d.messages || []).join("\n") || "（還沒有訊息）"; el.scrollTop = el.scrollHeight; }
      const st = document.getElementById("execStats");
      if (st) st.innerHTML = execStatsHtml(d);
      const sp = document.getElementById("execSteps");
      if (sp) sp.innerHTML = execStepRows(d);
      const eta = document.getElementById("execEta");
      if (eta) eta.textContent = execEtaText(d);
      const aw = document.getElementById("execAwake");
      if (aw) aw.innerHTML = awakeLine(d);
      const tb = document.getElementById("execTimeBox");   // 10-08：執行中「已執行 x 分」跟著更新
      if (tb) tb.innerHTML = execTimeHtml(d["執行時間"]);
      if (!d.running) { clearInterval(execPollTimer); execPollTimer = null; if (execRedoGoBack(d)) return; await renderExecuteBody(); }
    } catch (e) { pollFailed(e); /* 輪詢失敗，下一次再試；連續幾次連不上就出橫幅 */ }
  }, 2000);
}

// ---------------------------------------------------------------------------
// 第 0 步：初始化設定（跨專案共用；名冊只顯示代號與筆數，不顯示本名）
// ---------------------------------------------------------------------------

async function renderProfile() {
  contentEl.innerHTML = "<p>載入中…</p>";
  const d = await apiGet("/api/profile");
  const desc = {
    "名冊.csv": "學員本名、其他寫法（認得出誰是誰）。只要一份、不分期；代號每一集在第 3 步自己選",
    "敏感詞.csv": "公司名、地名等要換掉的詞，與替代詞",
    "名字排除清單.csv": "確認不是名字的詞（地名、疊字誤抓），之後自動不列入候選",
    "發音對照表.csv": "老師 AI 聲音念偏的詞，換成接近台灣口音的寫法",
  };
  const rows = Object.entries(d["檔案"]).map(([name, f]) => `<tr>
      <td><b>${esc(name.replace(".csv", ""))}</b><div class="muted">${esc(desc[name] || "")}</div>
        ${name === "名冊.csv" && (f["代號"] || []).length ? `<div class="muted">代號：${esc(f["代號"].join("、"))}${f["沒有代號的筆數"] ? `（另有 ${f["沒有代號的筆數"]} 筆還沒有代號）` : ""}</div>` : ""}
        ${f["範例列"] ? `<div class="muted">裡面還有範本的範例列 ${f["範例列"]} 筆（範例學員、範例公司名稱這類示範資料），用不到可以從檔案刪掉；匯出設定包不會帶</div>` : ""}
        ${(f["重複寫法列"] || []).length ? `<div class="rv-warnline">同一個寫法出現在兩列以上：${esc(f["重複寫法列"].map((g) => `第 ${g.join("、")} 列`).join("；"))}（標題列算第 1 列）。同一處名字會同時比中這幾列的人；是同一人的話到名冊把重複的那一列刪掉或合併，不是同一人就留著，第 3 步卡片上再選是哪一位。</div>` : ""}</td>
      <td>${f["有檔案"] ? `${f["筆數"]} 筆` : "還沒有檔案"}</td>
      <td>${esc(f["最後修改"] || "—")}</td></tr>`).join("");
  const st = d["settings.toml"];
  contentEl.innerHTML = `
    <h1>初始化設定</h1>
    <p class="muted">這些設定所有影片共用，放在 <code>${esc(d["資料夾"])}</code>。AI 之後發現新的念偏詞、排除詞會照舊寫進這裡；要給協作夥伴，用下面的設定包。</p>
    <div class="card"><table class="kv prof">
      <thead><tr><th style="text-align:left">項目</th><th style="text-align:left">筆數</th><th style="text-align:left">最後修改</th></tr></thead>
      <tbody>${rows}
        <tr><td><b>替代聲音</b><div class="muted">學員重念用的 AI 聲音素材（可商用的開放授權）</div></td><td>${esc(d["匿名聲線"]["狀態"])}</td><td>—</td></tr>
      </tbody></table>
      <details class="adv"><summary>進階設定</summary>
        <p class="muted">程式本身的設定（網頁開在哪個位址、用哪個 AI 模型、各種判斷的標準），平常不用改。
          ${st["有檔案"] ? `有自己的設定檔（最後修改 ${esc(st["最後修改"] || "—")}）` : "沒有自己的設定檔，用內建的預設值"}；要改請找 Claude。</p>
      </details></div>
    <h2>設定包（給協作夥伴）</h2>
    <div class="card">
      <p>匯出：名冊、敏感詞、名字排除清單、發音對照表、進階設定，與第 4 步學員重念用的替代聲音，打包成一個 zip。名冊含學員本名，只傳給協作夥伴。</p>
      <p><a href="/api/profile/export.zip"><button>匯出設定包</button></a></p>
      <p style="margin-top:18px">匯入：每個清單以第一欄當鑰匙，新的加進去、已經有的不動；同一鑰匙內容不同，保留這台電腦的並列出衝突。這台電腦表頭沒有的欄位不會匯入，匯入結果會列出來；範本的範例列不匯入。</p>
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
      const voice = r["匿名聲線"] ? `<li>${esc(r["匿名聲線"]["說明"])}</li>` : "";   // 10-03（#7）
      const notes = (r["提醒"] || []).map((n) => `<li>${esc(n)}</li>`).join("");   // 10-03 第九批（#34、#96）：沒匯入的欄位、範例列、名冊重複寫法
      msg.innerHTML = `<p><span class="badge done">匯入完成</span> 新增 ${r["新增"]} 筆、衝突 ${r["衝突數"]} 筆</p><ul>${lines}${notes}${voice}</ul>`;
      const keep = msg.innerHTML;
      await renderProfile();
      document.getElementById("profMsg").innerHTML = keep;
    } catch (e) { msg.innerHTML = `<span class="badge error">失敗</span> ${esc(e.message)}`; }
  });
}

// ---------------------------------------------------------------------------
// 總覽
// ---------------------------------------------------------------------------
// 10-07 宇軒：選影片改用系統內建的選檔視窗（fileio.js），頁面上只留檔案路徑。網頁上的資料夾瀏覽（renderPicker）
// 只在叫不起系統視窗（WSL 的 interop 關掉、沒有桌面）或按了取消時才出現。

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
          <tr><td>工作區</td><td>${pjNowHtml(state["專案標示"])}${esc(state.workdir)}</td></tr>
          <tr><td>影片分析</td><td>${done}／${order.length} 個子步驟完成${state["總耗時_s"] ? `，花了 ${esc(fmtElapsed(state["總耗時_s"]))}` : ""}（<a href="#step1">看進度</a>）</td></tr>
        </table>
      </div>`;
  }
  const list = projects["專案"] || [];
  // 10-04 #129：每一列小字印完整路徑（以前只印上一層），資料夾名稱一樣的專案分得出來
  // 1015-8 第 3 段（§5.3）：已結案的放到最下面一個收合的「已結案（N 支）」，名稱旁徽章、底下灰字「10-16 存到 D:\…」
  const rowHtml = (p) => {
    const c = p["已結案"];
    return `<tr class="${p["路徑"] === projects["目前"] ? "cur" : ""}">
      <td><b>${esc(p["顯示名稱"] || p["名稱"])}</b> <span class="badge ${p["標示"] === "測試" ? "" : "done"}">${esc(p["標示"] || "")}</span>${c ? ` <span class="badge">已結案</span>` : ""}${p["路徑"] === projects["目前"] ? "　（目前）" : ""}${!c && typeof hoRowHtml === "function" ? hoRowHtml(p) : ""}
        ${c ? `<div class="muted">${esc([c["日期"], c["位置顯示"] ? `存到 ${c["位置顯示"]}` : ""].filter(Boolean).join(" "))}</div>` : ""}
        <div class="muted">${p["舊位置"] ? "舊位置（讀書會剪輯資料／工作區）：" : ""}${esc(p["路徑"])}</div></td>
      <td>${esc(p["長度"] || "—")}</td>
      <td>${c ? "已結案，只能看紀錄" : `${p["分析完成"] ? "分析完成" : "還沒分析完"}${p["有覆核"] ? "、覆核中" : ""}`}</td>
      <td>${esc(p["修改時間"])}</td>
      <td>${p["路徑"] === projects["目前"] ? "" : `<button class="secondary pj-switch" data-path="${esc(p["路徑"])}">切換</button>`}</td></tr>`;
  };
  const groups = pjGroups(list);
  const rows = groups["進行中"].map(rowHtml).join("");
  const closedRows = groups["已結案"].map(rowHtml).join("");
  contentEl.innerHTML = `
    <h1>總覽</h1>
    ${projects["轉文字金鑰"] === false ? `<div class="hint">${esc(projects["轉文字金鑰說明"] || "這個網頁伺服器讀不到 Groq 金鑰，新影片沒辦法轉文字。關掉這個伺服器，改用雙擊「啟動.command」重開。")}（已經轉好文字的專案不受影響）</div>` : ""}
    ${pickCardHtml()}
    ${typeof hoOverviewCardHtml === "function" ? hoOverviewCardHtml() : ""}
    ${current}
    <h2>已有的專案（${list.length}）</h2>
    <div class="card">${groups["進行中"].length ? `<table class="kv pj-list"><tbody>${rows}</tbody></table>` : `<p class="muted">${list.length ? "沒有進行中的專案。" : "還沒有專案。"}</p>`}
      ${closedRows ? `<details class="pj-closed"><summary>已結案（${groups["已結案"].length} 支）</summary><table class="kv pj-list"><tbody>${closedRows}</tbody></table></details>` : ""}</div>`;
  contentEl.querySelectorAll(".pj-switch").forEach((b) => b.addEventListener("click", async () => {
    const p = list.find((x) => x["路徑"] === b.dataset.path) || {};
    // 10-08：切換前說清楚要切去哪一個（名稱一樣的專案分得出來）
    if (!(await uiConfirm({ title: `切換到「${p["顯示名稱"] || p["名稱"] || ""}」（${p["標示"] || ""}）？`, ok: "切換", cancel: "先不要",
      message: `${b.dataset.path}\n之後第 3、4、5 步看的、改的都是這個工作區。` }))) return;
    try { await apiPost("/api/projects/switch", { "路徑": b.dataset.path }); } catch (e) { uiErrorAlert({ title: "沒有切換", message: e.message, where: "總覽・切換專案" }); return; }
    await render();
  }));
  if (typeof hoBindOverview === "function") hoBindOverview(contentEl, list);   // 33 號設計：匯入交接包、清掉這集（handoff.js）
  await bindPickCard(list);   // 10-07：系統內建的選檔視窗（fileio.js）；叫不起來才顯示下面的網頁資料夾瀏覽
}

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
  box.querySelectorAll(".pk-video").forEach((b) => b.addEventListener("click", () => pickBrowsed(`${d["路徑"]}/${b.dataset.name}`)));
}

async function startPicked() {
  const msg = document.getElementById("pkMsg");
  msg.textContent = "開始中…";
  const body = { "用選的": true };   // 10-07：用伺服器記著的那一支（系統選檔視窗選的；WSL2 是複製進 Ubuntu 的那一份）
  try {
    let r;
    try {
      r = await apiPost("/api/projects/start", body, { quiet: true });
    } catch (e) {
      // 10-02 第七批（B3）：找不到 claude 先說，確定要開始再送一次
      if (!(e.data && e.data["找不到claude"]) || !(await uiConfirm({ title: "還是要開始分析嗎？", message: e.message, ok: "還是開始分析", cancel: "先不要" }))) throw e;
      r = await apiPost("/api/projects/start", { ...body, "沒有claude也開始": true });
    }
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
  const allDone = order.every(([k]) => (sub[k] || {}).done);   // 10-01 第三批 10：分析做完了，按鈕改「重新分析（做完的會跳過）」
  const errorMsg = status && status.error;
  const messages = (status && status.messages) || [];
  // 10-02 第七批（A3＋B3）：Claude 那幾步沒跑成功、找不到 claude，畫面上說清楚
  const failed = state["Claude沒跑成功"] || [];
  const failedSteps = new Set(failed.map((f) => f["步驟"]));
  const otherNotes = (state["要注意"] || []).filter((m) => ![...failedSteps].some((k) => m.startsWith(`${k}（Claude）`)));
  const claudeCard = (failed.length || otherNotes.length || state["找得到claude"] === false) ? `
    <div class="card" id="step1Warn">
      ${state["找得到claude"] === false ? `<p class="rv-warnline" id="noClaude">找不到 claude 指令（PATH 和 ~/.local/bin 都沒有）：段落分析、建議刪除段落、人名清單會沒跑成功，第 3 步沒有學員段落。先裝好 Claude Code、登入一次（終端機跑 bookclub doctor 可以檢查），再按開始分析。</p>` : ""}
      ${failed.length ? `<p><span class="badge error">沒跑成功</span> 上次分析有 ${failed.length} 個用 Claude 的步驟沒跑成功：</p>
        <ul id="claudeFailed">${failed.map((f) => `<li><b>${esc(f["步驟"])}</b>：${esc(f["原因"])}<br><span class="muted">重跑：${esc(f["怎麼重跑"])}</span></li>`).join("")}</ul>` : ""}
      ${otherNotes.length ? `<p>要注意：</p><ul>${otherNotes.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : ""}
    </div>` : "";

  contentEl.innerHTML = `
    <h1>1　影片分析</h1>
    <div class="card">
      <table class="kv">
        <thead><tr><th style="text-align:left">子步驟</th><th style="text-align:left">狀態</th>
          <th style="text-align:left">耗時</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    ${claudeCard}
    <div class="card">
      <button id="btnAnalyze" ${running ? "disabled" : ""}>${running ? "分析執行中…" : allDone ? "重新分析（做完的會跳過）" : "開始分析"}</button>
      ${!running && allDone ? `<span class="muted">每一步都做完了；再按一次只會補做沒做完的，做完的直接沿用</span>` : ""}
      ${errorMsg ? `<p><span class="badge error">失敗</span> ${esc(errorMsg)}</p>` : ""}
      <div id="runAwake">${awakeLine(status)}</div>
      <div class="log" id="runLog">${messages.map(esc).join("\n") || "（還沒有訊息）"}</div>
    </div>
  `;

  document.getElementById("btnAnalyze").addEventListener("click", startAnalyze);

  if (running) startStatusPoll();
}

async function startAnalyze() {
  try {
    await apiPost("/api/run/analyze", {}, { quiet: true });
  } catch (e) {
    // 10-02 第七批（B3）：找不到 claude 先說，確定要開始再送一次
    if (e.data && e.data["找不到claude"] && await uiConfirm({ title: "還是要開始分析嗎？", message: e.message, ok: "還是開始分析", cancel: "先不要" })) {
      try {
        await apiPost("/api/run/analyze", { "沒有claude也開始": true }, { quiet: true });
      } catch (e2) {
        uiErrorAlert({ title: "無法開始分析", message: e2.message, where: "第 1 步・開始分析" });
        return;
      }
    } else {
      uiErrorAlert({ title: "無法開始分析", message: e.message, where: "第 1 步・開始分析" });
      return;
    }
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
      pollOk();
      const logEl = document.getElementById("runLog");
      if (logEl) logEl.textContent = (status.messages || []).join("\n") || "（還沒有訊息）";
      const aw = document.getElementById("runAwake");
      if (aw) aw.innerHTML = awakeLine(status);
      if (!status.running) {
        stopStatusPoll();
        await renderStep1Body();
        await renderSidebar();
      }
    } catch (e) {
      pollFailed(e);   // 輪詢失敗不中斷，下一次再試；連續幾次連不上就出橫幅（#24）
    }
  }, 2000);
}

// ---------------------------------------------------------------------------
// 第 2 步：挑選老師參考聲音片段
// 09-30 宇軒：要能來回切換著聽、互相比對 → 上一個／下一個（切過去自動播放）、每一個候選一顆按鈕直接跳、
// 隨時看得到目前選定的是第幾個；打開時停在已經選定的那一個；改到一半的逐字稿切走再回來還在
// ---------------------------------------------------------------------------

let refsCache = null;
let refsPointer = 0;
let refsDrafts = {};   // 名次 → 改到一半、還沒按「用這段」的逐字稿

async function renderStep2() {
  contentEl.innerHTML = "<p>載入中…</p>";
  refsCache = await apiGet("/api/refs");
  refsDrafts = {};
  const list = (refsCache && refsCache.candidates) || [];
  const chosen = list.findIndex((c) => c.rank === refsCache["已選定名次"]);
  refsPointer = chosen >= 0 ? chosen : 0;
  renderStep2Body();
}

function refsGo(k, play) {
  const list = (refsCache && refsCache.candidates) || [];
  if (k < 0 || k >= list.length || k === refsPointer) return;
  const ta = document.getElementById("refText");
  if (ta) refsDrafts[list[refsPointer].rank] = ta.value;
  refsPointer = k;
  renderStep2Body(play);
}

function renderStep2Body(play = false) {
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
  const chosenRank = refsCache["已選定名次"];
  const chosenAt = list.findIndex((x) => x.rank === chosenRank);
  const isChosen = c.rank === chosenRank;
  const text = refsDrafts[c.rank] !== undefined ? refsDrafts[c.rank] : c.transcript;
  const chips = list.map((x, k) => `<button class="ref-chip${k === refsPointer ? " on" : ""}${x.rank === chosenRank ? " chosen" : ""}" data-k="${k}"
      title="${esc(x["原片時間"])}">${x.rank === chosenRank ? "✓ " : ""}第 ${k + 1} 個</button>`).join("");

  contentEl.innerHTML = `
    <h1>2　挑選老師參考聲音片段</h1>
    <div class="hint">參考音裡如果有雜音、笑聲、咳嗽，或別人的回應（例如「嗯」「對」），都不適合。可以來回切換著聽，比較哪一個最像老師；鍵盤 ← → 也可以切換。</div>
    <div class="card">
      <p><b>目前選定：${chosenAt >= 0 ? `第 ${chosenAt + 1} 個` : "還沒選"}</b></p>
      <div class="ref-chips">${chips}</div>
      <p>正在聽：第 ${refsPointer + 1} 個／共 ${list.length} 個${isChosen ? `　<span class="badge done">目前選定的</span>` : ""}　｜　原片時間：${esc(c["原片時間"])}　｜　長度：${esc(c["長度秒"])} 秒</p>
      ${c["音檔網址"] ? `<audio id="refAudio" controls preload="auto" src="${esc(c["音檔網址"])}"></audio>` : `<div class="namecard missing">（音檔缺失）</div>`}
      <div style="margin-top:10px; display:flex; gap:10px; flex-wrap:wrap;">
        <button id="btnPrev" class="secondary" ${refsPointer <= 0 ? "disabled" : ""}>◀ 聽上一個</button>
        <button id="btnNext" class="secondary" ${refsPointer >= list.length - 1 ? "disabled" : ""}>聽下一個 ▶</button>
      </div>
      <p style="margin-top:12px;">逐字稿（可以直接修改；要跟聲音一字不差）：</p>
      <textarea id="refText" rows="4">${esc(text)}</textarea>
      ${refAudioNote(c, isChosen)}
      <div style="margin-top:14px; display:flex; gap:10px; flex-wrap:wrap;">
        <button id="btnUse">${isChosen ? "存逐字稿（音檔不換）" : "改用這一個"}</button>
        ${isChosen && c["跟現在用的音檔一樣"] === false ? `<button id="btnSwapAudio" class="secondary">換成這個候選的新音檔</button>` : ""}
      </div>
      <p id="refMsg"></p>
    </div>
    <div class="card" id="roomCard"><p class="muted">全片底噪載入中…</p></div>
  `;
  renderRoomCard();

  document.getElementById("btnPrev").addEventListener("click", () => refsGo(refsPointer - 1, true));
  document.getElementById("btnNext").addEventListener("click", () => refsGo(refsPointer + 1, true));
  contentEl.querySelectorAll(".ref-chip").forEach((el) => el.addEventListener("click", () => refsGo(Number(el.dataset.k), true)));
  const audio = document.getElementById("refAudio");
  if (play && audio) audio.play().catch(() => {});   // 切換過來的直接播，方便比對

  // 10-01：「存逐字稿」只存逐字稿；換音檔只在「改用這一個」「換成這個候選的新音檔」（老師的句子會全部重新生成）
  const save = async (swapAudio) => {
    const msgEl = document.getElementById("refMsg");
    const text = document.getElementById("refText").value;
    if (swapAudio && chosenRank != null &&
        !(await uiConfirm({ title: `要${isChosen ? "換成這個候選的新音檔" : `改用第 ${refsPointer + 1} 個`}嗎？`, message: "換了音檔之後，已經生成好的老師句子都要重新生成。",
          ok: isChosen ? "換成新音檔" : `改用第 ${refsPointer + 1} 個`, cancel: "先不要" }))) return;
    try {
      const r = await apiPost("/api/refs/use", { rank: c.rank, transcript: text, 換音檔: swapAudio });
      refsCache = await apiGet("/api/refs");
      delete refsDrafts[c.rank];
      renderStep2Body();
      document.getElementById("refMsg").innerHTML = r["換了音檔"]
        ? `<span class="badge done">已存檔</span> 目前選定第 ${refsPointer + 1} 個：逐字稿和音檔都換成這一個`
        : `<span class="badge done">已存檔</span> 只存了逐字稿，音檔沒有換`;
      await renderSidebar();
    } catch (e) {
      msgEl.innerHTML = `<span class="badge error">失敗</span> ${esc(e.message)}`;
    }
  };
  document.getElementById("btnUse").addEventListener("click", () => save(!isChosen));
  const swapBtn = document.getElementById("btnSwapAudio");
  if (swapBtn) swapBtn.addEventListener("click", () => save(true));
}

// 10-02 第六批第五件：全片底噪（消音、補空白的地方附近找不到夠安靜的聲音時，墊這一段）。宇軒的說法照原話寫
async function renderRoomCard() {
  const box = document.getElementById("roomCard");
  if (!box) return;
  let r;
  try { r = await apiGet("/api/roomtone"); } catch (e) { box.innerHTML = `<p class="muted">全片底噪讀不到：${esc(e.message)}</p>`; return; }
  if (!document.getElementById("roomCard")) return;
  const n = (r["候選"] || []).length;
  if (r["沒有"] || r.start == null) {
    box.innerHTML = `<h2>全片底噪</h2><p class="muted">${r["沒有"] ? "還沒有整支影片的聲音（第 1 步分析跑完才挑得出來）。" : "這支影片找不到夠安靜的片段：消音的地方附近找不到安靜的聲音時，會墊全靜音。"}</p>`;
    return;
  }
  const a = Math.max(0, r.start), b = r.end;
  box.innerHTML = `<h2>全片底噪</h2>
    <p>消音、補空白的地方，附近找不到夠安靜的聲音時，會墊這一段（從整支影片自動挑最安靜的地方）。</p>
    <p class="hint"><b>播放、聆聽，如果聽到任何聲音，它就不能當作底噪。</b></p>
    <p>原片時間：${esc(fcTime(a))}–${esc(fcTime(b))}（${(b - a).toFixed(1)} 秒）｜第 ${(r["選第幾個"] || 0) + 1} 段／共 ${n} 段　
      ${r["已確認"] ? `<span class="badge done">已確認</span>` : `<span class="badge">還沒確認</span>`}</p>
    <audio id="roomAudio" controls preload="none" src="/api/audio?start=${a.toFixed(2)}&end=${b.toFixed(2)}"></audio>
    <p class="muted">很小聲是正常的（本來就該幾乎聽不到），可以把音量開大一點聽。</p>
    <div style="display:flex; gap:10px; flex-wrap:wrap;">
      <button id="roomOk" ${r["已確認"] ? "disabled" : ""}>這段可以</button>
      <button id="roomNext" class="secondary" ${n < 2 ? "disabled" : ""}>換一段</button>
    </div>
    <p class="muted">還沒確認也可以先往下做：照樣用這一段（它本來就夠安靜），第 4 步開始前的總檢查會提醒你回來聽。</p>`;
  const act = async (v) => {
    try { await apiPost("/api/roomtone", { "動作": v }); } catch (e) { uiErrorAlert({ title: "沒有成功", message: e.message }); return; }
    await renderRoomCard();
    if (v === "換一段") { const au = document.getElementById("roomAudio"); if (au) au.play().catch(() => {}); }
  };
  document.getElementById("roomOk").addEventListener("click", () => act("確認"));
  document.getElementById("roomNext").addEventListener("click", () => act("換一段"));
}

function refRateName(rate) {   // 10-01：不用「48kHz」這種說法
  if (!rate) return "";
  return rate >= 44100 ? "新版（保留高音）" : "舊版（高音比較少）";
}

function refAudioNote(c, isChosen) {
  // 10-01：看得出「現在用的音檔」跟「這個候選的音檔」是不是同一份（候選重切過、選定的還是舊的）
  if (!isChosen) return "";
  const same = c["跟現在用的音檔一樣"];
  if (same === true) return `<p class="hint">現在用的音檔就是這個候選的音檔${c["取樣率"] ? `，是${esc(refRateName(c["取樣率"]))}` : ""}。</p>`;
  if (same === false) {
    return `<div class="notyet-card" style="margin-top:10px;">現在用的音檔是${esc(refRateName(refsCache["現在用的音檔取樣率"]) || "舊的")}，這個候選的音檔是${esc(refRateName(c["取樣率"]) || "重切過的")}，兩個不是同一份。
      按「存逐字稿（音檔不換）」只存逐字稿；要改用這個候選的音檔，按「換成這個候選的新音檔」，換了之後老師的句子都要重新生成。</div>`;
  }
  return "";
}

document.addEventListener("keydown", (e) => {   // 第 2 步：← → 切換候選（在打字時不搶）
  if (!document.getElementById("refText") || !document.querySelector(".ref-chips")) return;
  if (document.querySelector("dialog[open]")) return;   // 1022-1 審查修正：視窗開著時方向鍵不穿透到底下切換候選
  if (e.target && ["TEXTAREA", "INPUT", "SELECT"].includes(e.target.tagName)) return;
  if (e.key === "ArrowLeft") { e.preventDefault(); refsGo(refsPointer - 1, true); }
  if (e.key === "ArrowRight") { e.preventDefault(); refsGo(refsPointer + 1, true); }
});
