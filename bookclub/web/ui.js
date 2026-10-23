"use strict";
/* 1022-1（#263）：工具自己的確認／輸入／提示視窗，取代瀏覽器內建的 confirm()、prompt()、alert()。
 * 內建框標題是「localhost:8766 顯示」、長文擠成一團、按鈕只有「確定／取消」，老師看不懂要按哪一顆。
 *
 *   await uiConfirm({title, message, ok, cancel, danger})  → true（按了主要按鈕）／false（取消、✕、Esc）
 *     兩顆按鈕都是「要做的事」時加 dismiss: null → ✕、Esc 回 null（＝這次的修改不算），跟按取消鈕分開
 *   await uiPrompt({title, message, placeholder, ok, value}) → 打的字（去頭尾空白前的原樣）／null（取消）
 *   await uiAlert({title, message, ok, copy})               → 關掉時 resolve
 *     錯誤視窗加 copy（純文字）→ 多一顆「複製錯誤資訊」，按了複製到剪貼簿（10-23，#258）
 *
 * - 用原生 <dialog> + showModal()，樣式沿用第 3 步的 .rv-modal（style.css），深色模式跟著走。
 * - 訊息裡的換行（\n）變成一段一段；所有文字都用 textContent 放，不用 innerHTML 拼字串。
 * - 危險動作（danger: true，例如剪掉、刪掉、整段改）預設停在「取消」，其他停在主要按鈕。
 * - 同時叫好幾個視窗會排隊：前一個關掉才開下一個，不會疊兩層。
 * - 視窗掛在 <body> 底下（不在 #content 裡），第 3 步唯讀、已結案把 #content 裡的按鈕鎖住時，這裡的按鈕照樣按得到。
 * - 瀏覽器太舊沒有 <dialog> 時退回內建的框，至少流程不會卡住。 */

const uiQueue = [];
let uiBusy = false;
let uiSeq = 0;

function uiEnqueue(build) {
  return new Promise((resolve) => {
    uiQueue.push({ build, resolve });
    uiNext();
  });
}

function uiNext() {
  if (uiBusy || !uiQueue.length) return;
  uiBusy = true;
  const { build, resolve } = uiQueue.shift();
  let done = false;
  const finish = (v) => {
    if (done) return;
    done = true;
    uiBusy = false;
    resolve(v);
    Promise.resolve().then(uiNext);   // 下一個排隊的視窗（不用計時器：分頁在背景時會被延後）
  };
  try { build(finish); } catch (e) { finish(undefined); throw e; }
}

function uiParas(message) {   // 10-22 宇軒 B9：「• 」開頭的連續幾行變成項目清單
  const frag = document.createDocumentFragment();
  let ul = null;
  String(message == null ? "" : message).split(/\n/).forEach((line) => {
    if (!line.trim()) { ul = null; return; }   // 空行只是段落間距，不另外畫
    if (line.startsWith("• ")) {
      if (!ul) { ul = document.createElement("ul"); frag.appendChild(ul); }
      const li = document.createElement("li");
      li.textContent = line.slice(2);
      ul.appendChild(li);
      return;
    }
    ul = null;
    const p = document.createElement("p");
    p.textContent = line;
    frag.appendChild(p);
  });
  return frag;
}

function uiButton(text, cls) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = cls;
  b.textContent = text;
  return b;
}

// kind：confirm／prompt／alert。opts 見檔頭。cancelValue：取消、✕、Esc 時回傳的值
function uiOpen(kind, opts, finish) {
  const o = typeof opts === "string" ? { message: opts } : (opts || {});
  const cancelValue = kind === "confirm" ? false : kind === "prompt" ? null : undefined;
  const dismissValue = "dismiss" in o ? o.dismiss : cancelValue;   // ✕、Esc、被別的程式關掉時回傳的值
  if (typeof HTMLDialogElement === "undefined" || !document.createElement("dialog").showModal) {
    // 退回內建的框（舊瀏覽器）
    const text = [o.title, o.message].filter(Boolean).join("\n\n");
    if (kind === "confirm") return finish(window.confirm(text));
    if (kind === "prompt") return finish(window.prompt(text, o.value || ""));
    window.alert(text);
    return finish(undefined);
  }
  const n = ++uiSeq;
  const dlg = document.createElement("dialog");
  dlg.className = "rv-modal ui-dialog";
  dlg.setAttribute("aria-labelledby", `ui-dlg-h-${n}`);
  dlg.setAttribute("aria-describedby", `ui-dlg-b-${n}`);

  const head = document.createElement("div");
  head.className = "rv-modal-head";
  const h = document.createElement("h2");
  h.id = `ui-dlg-h-${n}`;
  h.textContent = o.title || (kind === "confirm" ? "要繼續嗎？" : kind === "prompt" ? "請輸入" : "提醒");
  const x = uiButton("✕", "ghost rv-modal-x");
  x.setAttribute("aria-label", "關閉");
  head.append(h, x);

  const body = document.createElement("div");
  body.className = "rv-modal-body";
  body.id = `ui-dlg-b-${n}`;
  body.appendChild(uiParas(o.message));
  let input = null;
  if (kind === "prompt") {
    input = document.createElement("input");
    input.type = "text";
    input.className = "ui-dialog-input";
    if (o.placeholder) input.placeholder = o.placeholder;
    if (o.value) input.value = o.value;
    input.setAttribute("aria-label", o.title || "輸入");
    body.appendChild(input);
  }

  const foot = document.createElement("div");
  foot.className = "rv-modal-foot";
  const okText = o.ok || (kind === "alert" ? "知道了" : "確定");
  const okBtn = uiButton(okText, `primary${o.danger ? " danger" : ""}`);
  let cancelBtn = null;
  if (kind !== "alert") {
    cancelBtn = uiButton(o.cancel || "取消", "ghost");
    foot.appendChild(cancelBtn);
  }
  if (kind === "alert" && o.copy) {   // 10-23（#258）：錯誤視窗「複製錯誤資訊」（文字由呼叫端組好、已去掉路徑）
    const cp = uiButton("複製錯誤資訊", "ghost");
    foot.appendChild(cp);
    cp.addEventListener("click", () => uiCopyText(String(o.copy), cp, body));
  }
  foot.appendChild(okBtn);
  dlg.append(head, body, foot);

  // 開視窗那一下的 Enter 可能還沒放開（例如第 3 步在文字框按 Enter「通過」跳出確認），
  // 那一下會落到視窗的按鈕上、直接替人按掉。放開按鍵或開了 0.3 秒之後才收按鍵（用時間比，不用計時器：分頁在背景時計時器會被延後）。
  const openedAt = Date.now();
  let keyUp = false;
  const armed = () => keyUp || Date.now() - openedAt >= 300;
  dlg.addEventListener("keyup", () => { keyUp = true; });

  const prevFocus = document.activeElement;
  let closed = false;
  const close = (v) => {
    if (closed) return;
    closed = true;
    try { dlg.close(); } catch (e) { /* 已經關了 */ }
    dlg.remove();
    if (prevFocus && typeof prevFocus.focus === "function" && document.body.contains(prevFocus)) {
      try { prevFocus.focus({ preventScroll: true }); } catch (e) { /* 焦點回不去也沒關係 */ }
    }
    finish(v);
  };
  const okValue = () => (kind === "confirm" ? true : kind === "prompt" ? input.value : undefined);

  okBtn.addEventListener("click", () => { if (armed()) close(okValue()); });
  if (cancelBtn) cancelBtn.addEventListener("click", () => { if (armed()) close(cancelValue); });
  x.addEventListener("click", () => close(dismissValue));
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(dismissValue); });   // Esc＝取消
  // 1022-1 審查修正：別的程式（例如第 3 步對 .rv-modal 綁的「點背景關掉」）直接 dlg.close() 時也要收尾，
  // 不然 finish 沒呼叫、uiBusy 一直是 true，之後的視窗全部排隊出不來。close() 本來就防重複
  dlg.addEventListener("close", () => close(dismissValue));
  // 1022-1 審查修正：Enter／空白鍵按住不放會連發，第二下落到主要按鈕上替人按掉 → 連發的那幾下不算
  dlg.addEventListener("keydown", (e) => {
    if (e.repeat && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); e.stopPropagation(); }
  }, true);
  if (input) {
    input.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" || e.isComposing || e.keyCode === 229) return;   // 中文輸入法選字的 Enter 不算
      e.preventDefault();
      if (armed()) close(okValue());
    });
  }

  document.body.appendChild(dlg);
  dlg.showModal();
  const first = input || (o.danger && cancelBtn ? cancelBtn : okBtn);
  first.focus();
}

// 複製純文字：剪貼簿可以用就複製、按鈕暫時變「已複製」；不行就把文字放進可全選的框，讓人自己複製
function uiCopyText(text, btn, box) {
  const label = "複製錯誤資訊";
  const ok = () => {
    btn.textContent = "已複製";
    setTimeout(() => { btn.textContent = label; }, 2000);
  };
  const fallback = () => {
    let ta = box.querySelector ? box.querySelector("textarea.ui-copy-box") : null;
    if (!ta) {
      ta = document.createElement("textarea");
      ta.className = "ui-copy-box";
      ta.readOnly = true;
      ta.rows = 7;
      ta.setAttribute("aria-label", "錯誤資訊（全選後複製）");
      box.appendChild(ta);
    }
    ta.value = text;
    try { ta.focus(); ta.select(); } catch (e) { /* 選不起來也沒關係，字在框裡 */ }
    btn.textContent = "請全選框裡的字複製";
  };
  try {
    const cb = typeof navigator !== "undefined" && navigator.clipboard;
    if (cb && cb.writeText) cb.writeText(text).then(ok, fallback); else fallback();
  } catch (e) { fallback(); }
}

// 10-23（#258）：錯誤視窗的「複製錯誤資訊」（原本在 handoff.js，10-23 搬來共用）——工具版本、系統、在哪裡、錯誤訊息、時間；路徑與檔名一律換成「（路徑略）」，
// 不放逐字稿、名字（錯誤訊息本來就不帶這些）
// 路徑可以有空格、中文、半形括號（「/Users/x/a b/第一集 (2).mp4」「C:\\Users\\…」）：從開頭的「/」「~/」「X:\\」一路遮到
// 引號、全形標點、換行或字串結尾為止，不留尾巴。路徑前面要是開頭、空白或引號（「Mac/Windows」這種字中間的斜線不算）
function uiScrub(text) {
  return String(text == null ? "" : text)
    .replace(/(^|[\s「『（("'：:])(?:[A-Za-z]:\\|~\/|\/)[^「」『』，。、；：（）"'\n]*/g, (m, pre) => `${pre}（路徑略）`)
    .replace(/「[^「」\n]*\.(?:mp4|mov|m4v|mkv|webm|avi|zip|wav|flac|json|csv)」/gi, "「（路徑略）」")   // 引號裡的檔名（可以有空格）
    .replace(/[^\s「」『』，。、；：（）()"'／]+\.(?:mp4|mov|m4v|mkv|webm|avi|zip|wav|flac|json|csv)\b/gi, "（路徑略）");
}

function uiErrorText(st, where, message, now) {
  const s = st || {};
  const sys = s["系統"] === "mac" ? "Mac" : s["系統"] === "wsl" ? "Windows" : "其他";
  const d = now || new Date();
  const p2 = (n) => String(n).padStart(2, "0");
  return ["讀書會剪輯工具：錯誤資訊", `工具版本：${s["工具版本"] || "不知道"}`, `系統：${sys}`, `在哪裡：${where}`,
    `錯誤訊息：${uiScrub(message)}`,
    `時間：${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`].join("\n");
}


// 工具版本、系統（第一次用時問伺服器，之後記著）
let uiToolInfoCache = null;
async function uiToolInfo() {
  if (uiToolInfoCache) return uiToolInfoCache;
  try { const r = await fetch("/api/tool-info"); uiToolInfoCache = await r.json(); } catch (e) { uiToolInfoCache = {}; }
  return uiToolInfoCache || {};
}

// 現在在哪一頁（錯誤資訊的「在哪裡」沒給時用）
const UI_PLACES = { step0: "初始化設定", step1: "第 1 步", step2: "第 2 步", step3: "第 3 步", step4: "第 4 步", step5: "第 5 步" };
function uiWhereNow() {
  const h = (typeof location !== "undefined" && location.hash || "").replace(/^#/, "").split(/[?/]/)[0];
  return UI_PLACES[h] || "總覽";
}

// 錯誤視窗：跟 uiAlert 一樣，多一顆「複製錯誤資訊」。where＝哪個功能（例如「第 5 步・輸出成品」），沒給就寫現在這一頁
async function uiErrorAlert(opts) {
  const o = typeof opts === "string" ? { message: opts } : (opts || {});
  const info = await uiToolInfo();
  return uiAlert({ ...o, copy: uiErrorText(info, o.where || uiWhereNow(), o.message) });
}

function uiConfirm(opts) { return uiEnqueue((finish) => uiOpen("confirm", opts, finish)); }
function uiPrompt(opts) { return uiEnqueue((finish) => uiOpen("prompt", opts, finish)); }
function uiAlert(opts) { return uiEnqueue((finish) => uiOpen("alert", opts, finish)); }

/* 1022-2（#264）：成功／完成的通知改成右下角的浮動通知（Toast），不用再按「知道了」。
 *
 *   uiToast(訊息)   → 不用 await；畫面右下角跳一則，停 UI_TOAST_MS 後自己淡出
 *
 * - 只給「做好了」這類訊息用；錯誤、或要人接著動手的話（例如「剩下的請自己打一個」）一律用 uiAlert，
 *   會自己消失的通知容易錯過。
 * - 滑鼠移到通知上、或通知裡的 ✕ 有焦點時暫停倒數；移開後重新算一次完整的秒數。
 * - 同時多則往上疊，最多 UI_TOAST_MAX 則，再多就把最舊的收掉。
 * - 位置在右下角（左下角是「工具設定」齒輪，不擋它）；不搶焦點；容器 role="status" aria-live="polite"，報讀軟體會念。
 * - 系統設定「減少動態效果」時不做淡入淡出。 */

const UI_TOAST_MS = 5000;   // 每則停留多久（毫秒），宇軒 10-22 定 5 秒
const UI_TOAST_MAX = 3;     // 最多同時幾則
const UI_TOAST_FADE_MS = 220;

function uiReducedMotion() {
  try { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) { return false; }
}

let uiToastEl = null;

// 1022-2 審查修正：用 showModal() 開的視窗在最上層（top layer），掛在 <body> 的通知會被它和灰色背景蓋住。
// 有視窗開著時，把通知容器搬進最上面那個開著的視窗裡（通知是 position: fixed，在視窗裡一樣貼畫面右下角）；
// 視窗關了搬回 <body>。視窗開關由下面的 MutationObserver（看 open 屬性）觸發，新增通知時也再對一次。
function uiToastHost() {
  const open = document.querySelectorAll("dialog[open]");
  return open.length ? open[open.length - 1] : document.body;
}

function uiToastBox() {
  if (!uiToastEl) {
    uiToastEl = document.getElementById("ui-toasts") || document.createElement("div");
    uiToastEl.id = "ui-toasts";
    uiToastEl.className = "ui-toasts";
    uiToastEl.setAttribute("role", "status");
    uiToastEl.setAttribute("aria-live", "polite");
  }
  const host = uiToastHost();
  if (uiToastEl.parentNode !== host) host.appendChild(uiToastEl);
  return uiToastEl;
}

// 頁面載入就先建好容器（報讀軟體要先看到 live region，第一則通知才會被念出來）
if (document.body) uiToastBox();
if (typeof MutationObserver !== "undefined" && document.body) {
  new MutationObserver(() => { if (uiToastEl) uiToastBox(); })
    .observe(document.body, { subtree: true, attributes: true, attributeFilter: ["open"] });
}

function uiToastClose(el) {
  if (!el || el.dataset.leaving) return;
  el.dataset.leaving = "1";
  clearTimeout(el._uiTimer);
  el.classList.remove("show");
  const remove = () => el.remove();
  if (uiReducedMotion()) remove(); else setTimeout(remove, UI_TOAST_FADE_MS);
}

function uiToast(message) {
  const box = uiToastBox();
  const el = document.createElement("div");
  el.className = "ui-toast";
  const body = document.createElement("div");
  body.className = "ui-toast-body";
  body.appendChild(uiParas(message));
  const x = uiButton("✕", "ui-toast-x");
  x.setAttribute("aria-label", "關閉通知");
  el.append(body, x);

  let hover = false, focus = false;
  const start = () => { clearTimeout(el._uiTimer); el._uiTimer = setTimeout(() => uiToastClose(el), UI_TOAST_MS); };
  const pause = () => clearTimeout(el._uiTimer);
  el.addEventListener("mouseenter", () => { hover = true; pause(); });
  el.addEventListener("mouseleave", () => { hover = false; if (!focus) start(); });
  el.addEventListener("focusin", () => { focus = true; pause(); });
  el.addEventListener("focusout", (e) => {
    if (e.relatedTarget && el.contains(e.relatedTarget)) return;
    focus = false;
    if (!hover) start();
  });
  x.addEventListener("click", () => uiToastClose(el));

  box.appendChild(el);   // 新的放最下面（最靠近角落），舊的往上推
  const live = [...box.children].filter((c) => !c.dataset.leaving);
  live.slice(0, Math.max(0, live.length - UI_TOAST_MAX)).forEach(uiToastClose);
  void el.offsetWidth;   // 先排版一次，淡入的 transition 才會跑
  el.classList.add("show");
  start();
  return el;
}

/* 1022-2（#265）：重要按鈕的說明改成馬上出現的提示框（Tooltip），取代要停一秒才出現、字又小的 title。
 *
 *   <button data-tip="說明文字">…</button>
 *
 * - document 上掛一次事件（事件委派），卡片重畫、動態產生的按鈕不用各自綁。
 * - 滑鼠移上去 UI_TIP_DELAY_MS 後出現、移開就收；用鍵盤（Tab）移到按鈕上也會出現；按 Esc、按下按鈕、捲動都會收。
 * - 預設放在按鈕上方，上面放不下就放下方；左右不超出畫面。
 * - 出現時按鈕加 aria-describedby 指到提示框，收起來就拿掉。
 * - 按鈕本身停用（disabled）時的「為什麼不能按」仍用 title：停用的按鈕在部分瀏覽器不觸發滑鼠事件。 */

const UI_TIP_DELAY_MS = 150;
let uiTipEl = null;
let uiTipFor = null;
let uiTipTimer = 0;
let uiTipPending = null;   // 正在等 UI_TIP_DELAY_MS 的那顆（滑過按鈕裡的 <kbd>、圖示時不重新計時）

function uiTipTarget(node) {
  const el = node && node.nodeType === 1 ? node : node && node.parentElement;
  const t = el && el.closest ? el.closest("[data-tip]") : null;
  return t && t.dataset.tip ? t : null;
}

// 1022-2 審查修正：卡片重畫後原本那顆按鈕已經不在畫面上，提示框不能留著（字還是上一張卡的）
function uiTipStale() {
  if (uiTipFor && !uiTipFor.isConnected) uiTipHide();
}

function uiTipShow(t) {
  clearTimeout(uiTipTimer);
  uiTipPending = null;
  uiTipStale();
  if (!t || !t.isConnected) return;
  if (!uiTipEl) {
    uiTipEl = document.createElement("div");
    uiTipEl.id = "ui-tip";
    uiTipEl.className = "ui-tip";
    uiTipEl.setAttribute("role", "tooltip");
    uiTipEl.hidden = true;
    document.body.appendChild(uiTipEl);
  }
  if (uiTipFor && uiTipFor !== t) uiTipUnlink(uiTipFor);
  uiTipFor = t;
  uiTipEl.textContent = t.dataset.tip;
  uiTipEl.hidden = false;
  uiTipEl.classList.remove("below");
  uiTipEl.style.left = "0px";
  uiTipEl.style.top = "0px";
  const r = t.getBoundingClientRect();
  const w = uiTipEl.offsetWidth, h = uiTipEl.offsetHeight;
  const gap = 8, edge = 8;
  let top = r.top - h - gap;
  if (top < edge) { top = r.bottom + gap; uiTipEl.classList.add("below"); }   // 上面放不下就放下面
  top = Math.min(top, window.innerHeight - h - edge);
  let left = r.left + r.width / 2 - w / 2;
  left = Math.max(edge, Math.min(left, window.innerWidth - w - edge));
  uiTipEl.style.left = `${Math.round(left)}px`;
  uiTipEl.style.top = `${Math.round(Math.max(edge, top))}px`;
  const ids = (t.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
  if (!ids.includes("ui-tip")) t.setAttribute("aria-describedby", ids.concat("ui-tip").join(" "));
}

function uiTipUnlink(t) {
  const ids = (t.getAttribute("aria-describedby") || "").split(/\s+/).filter((x) => x && x !== "ui-tip");
  if (ids.length) t.setAttribute("aria-describedby", ids.join(" ")); else t.removeAttribute("aria-describedby");
}

function uiTipHide() {
  clearTimeout(uiTipTimer);
  uiTipPending = null;
  if (uiTipFor) uiTipUnlink(uiTipFor);
  uiTipFor = null;
  if (uiTipEl) uiTipEl.hidden = true;
}

function uiTipLater(t) {
  if (uiTipPending === t) return;
  clearTimeout(uiTipTimer);
  uiTipPending = t;
  uiTipTimer = setTimeout(() => uiTipShow(t), UI_TIP_DELAY_MS);
}

document.addEventListener("mouseover", (e) => {
  uiTipStale();
  const t = uiTipTarget(e.target);
  if (t && t === uiTipFor) return;
  if (!t) { if (uiTipFor || uiTipPending) uiTipHide(); return; }
  uiTipLater(t);
});
document.addEventListener("mouseout", (e) => {
  const t = uiTipTarget(e.target);
  if (!t) return;
  if (e.relatedTarget && t.contains(e.relatedTarget)) return;   // 還在同一顆按鈕裡（移到裡面的 <kbd>、圖示）
  if (document.activeElement === t && uiTipFor === t && t.matches(":focus-visible")) return;   // 鍵盤焦點還在
  uiTipHide();
});
document.addEventListener("focusin", (e) => {
  const t = uiTipTarget(e.target);
  if (!t) return;
  let kb = true;
  try { kb = t.matches(":focus-visible"); } catch (err) { /* 舊瀏覽器沒有 :focus-visible，照樣顯示 */ }
  if (kb) uiTipShow(t);   // 滑鼠點下去的焦點不另外顯示（點了就收）
});
document.addEventListener("focusout", (e) => { if (uiTipTarget(e.target) === uiTipFor) uiTipHide(); });
// 按任何鍵都收起（快捷鍵換卡、Enter 通過、Esc⋯）；Tab 例外：Tab 移到下一顆按鈕時由 focusin 顯示那一顆的說明
document.addEventListener("keydown", (e) => { if (e.key !== "Tab" && (uiTipFor || uiTipPending)) uiTipHide(); }, true);
document.addEventListener("pointerdown", () => { if (uiTipFor || uiTipPending) uiTipHide(); }, true);
window.addEventListener("scroll", () => { if (uiTipFor) uiTipHide(); }, true);
window.addEventListener("resize", () => { if (uiTipFor) uiTipHide(); });
