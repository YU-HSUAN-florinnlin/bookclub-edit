"use strict";
/* 10-15 第5步2.0 第2段：網頁程式的 node 測試用「假的瀏覽器」（不開瀏覽器、不裝套件）。
 *
 * 用法（tests/test_cards_snapshot.py、tests/test_final_cards_web.py 呼叫）：
 *   node tests/web_harness.js <要載入的 js 檔，逗號分隔> <情境 json 檔>
 * 情境 json：{前置: "JS 程式（載入之後先跑）", 情境: [{名稱, 程式: "JS 程式（回傳要比的東西）"}]}
 * 每個情境各自一份全新的環境（重新載入那幾支 js），印出 {名稱: 結果} 的 JSON。
 *
 * 假 DOM 只做到測試要的：innerHTML 設下去時用簡單的方式拆出有 id／class／data-* 的元素（不建真的樹），
 * getElementById、querySelector(All)（只看最後一段的 #id、.class、[data-x]、[data-x="v"]、標籤名）、addEventListener（記下來，
 * `H.fireAll()` 依序觸發一次）。apiGet／apiPost／confirm／alert／prompt（1022-1 起還有 uiConfirm／uiAlert／uiPrompt，1022-2 起還有 uiToast）都記進 `H.log`，不連網路。 */

const fs = require("fs");
const vm = require("vm");
const path = require("path");

function makeEnv(files, setup) {
  const H = { log: [], listeners: [], docListeners: [], els: {}, api: {}, apiGetData: {}, skeleton: new Set(), confirm: true };
  const ATTR_RE = /([\w:-]+)(?:\s*=\s*("([^"]*)"|'([^']*)'|[^\s>]+))?/g;
  const unesc = (s) => String(s).replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&");

  function classList(el) {
    return {
      add: (...c) => { const s = new Set(el.className.split(/\s+/).filter(Boolean)); c.forEach((x) => s.add(x)); el.className = [...s].join(" "); },
      remove: (...c) => { el.className = el.className.split(/\s+/).filter((x) => x && !c.includes(x)).join(" "); },
      toggle: (c, on) => { const has = el.className.split(/\s+/).includes(c); const want = on === undefined ? !has : !!on; if (want) el.classList.add(c); else el.classList.remove(c); return want; },
      contains: (c) => el.className.split(/\s+/).includes(c),
    };
  }

  function makeEl(tag, attrs, inner) {
    const el = {
      tagName: String(tag || "div").toUpperCase(), id: attrs.id || "", className: attrs.class || "", attrs,
      dataset: {}, style: {}, hidden: "hidden" in attrs, disabled: "disabled" in attrs, checked: "checked" in attrs,
      value: attrs.value != null ? unesc(attrs.value) : tag === "textarea" ? unesc(inner || "") : "",
      textContent: "", _html: "", _kids: [], selectionStart: 0, offsetTop: 0, offsetHeight: 10, scrollTop: 0, clientHeight: 100,
      isComposing: false,
    };
    el.classList = classList(el);
    for (const [k, v] of Object.entries(attrs)) {
      if (k.startsWith("data-")) el.dataset[k.slice(5).replace(/-([a-z])/g, (m, c) => c.toUpperCase())] = v == null ? "" : unesc(v);
    }
    el.addEventListener = (type, fn) => H.listeners.push({ el, type, fn });
    el.removeEventListener = () => {};
    el.setAttribute = (k, v) => { el.attrs[k] = String(v); };
    el.getAttribute = (k) => (k in el.attrs ? el.attrs[k] : null);
    el.querySelector = (sel) => queryAll(el._kids, sel)[0] || null;
    el.querySelectorAll = (sel) => queryAll(el._kids, sel);
    el.closest = () => null;
    el.matches = (sel) => sel.split(",").some((s) => matchOne(el, s.trim()));
    for (const fn of ["focus", "blur", "scrollIntoView", "remove", "showModal", "close", "play", "pause", "insertAdjacentElement", "setPointerCapture", "releasePointerCapture", "appendChild"]) {
      el[fn] = () => (fn === "play" ? Promise.resolve() : undefined);
    }
    el.getBoundingClientRect = () => ({ top: 0, left: 0, width: 100, height: 10, bottom: 10, right: 100 });
    Object.defineProperty(el, "innerHTML", {
      get: () => el._html,
      set: (v) => { el._html = String(v); el._kids = parse(el._html); },
    });
    return el;
  }

  function parse(html) {
    const out = [];
    const re = /<([a-zA-Z][\w-]*)((?:\s+[\w:-]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*\/?>/g;
    let m;
    while ((m = re.exec(html))) {
      const tag = m[1].toLowerCase();
      const attrs = {};
      let a;
      ATTR_RE.lastIndex = 0;
      while ((a = ATTR_RE.exec(m[2] || ""))) attrs[a[1]] = a[3] != null ? a[3] : a[4] != null ? a[4] : a[2] != null ? a[2] : null;
      let inner = "";
      if (tag === "textarea") { const end = html.indexOf("</textarea>", re.lastIndex); inner = end >= 0 ? html.slice(re.lastIndex, end) : ""; }
      const el = makeEl(tag, attrs, inner);
      out.push(el);
      if (el.id) H.els[el.id] = el;
    }
    return out;
  }

  function matchOne(el, sel) {
    const last = sel.trim().split(/\s+/).pop();
    const parts = last.match(/(^[a-zA-Z][\w-]*)|(#[\w一-鿿-]+)|(\.[\w-]+)|(\[[^\]]+\])|(:checked)/g) || [];
    for (const p of parts) {
      if (p.startsWith("#")) { if (el.id !== p.slice(1)) return false; }
      else if (p.startsWith(".")) { if (!el.classList.contains(p.slice(1))) return false; }
      else if (p === ":checked") { if (!el.checked) return false; }
      else if (p.startsWith("[")) {
        const mm = /^\[([\w:-]+)(?:="?([^"\]]*)"?)?\]$/.exec(p);
        if (!mm || !(mm[1] in el.attrs)) return false;
        if (mm[2] != null && unesc(el.attrs[mm[1]] || "") !== mm[2]) return false;
      } else if (el.tagName !== p.toUpperCase()) return false;
    }
    return parts.length > 0;
  }

  function queryAll(list, sel) {
    const sels = sel.split(",").map((s) => s.trim());
    return list.filter((el) => sels.some((s) => matchOne(el, s)));
  }

  const allEls = () => Object.values(H.els).concat(...Object.values(H.els).map((e) => e._kids));
  const document = {
    getElementById: (id) => H.els[id] || (H.skeleton.has(id) ? (H.els[id] = makeEl("div", { id }, "")) : null),
    querySelector: (sel) => (/^dialog/.test(sel) ? null : queryAll(uniq(allEls()), sel)[0] || null),
    querySelectorAll: (sel) => queryAll(uniq(allEls()), sel),
    createElement: (tag) => { const el = makeEl(tag, {}, ""); return el; },
    addEventListener: (type, fn) => H.docListeners.push({ type, fn }),   // 1017：document 的快捷鍵（keydown）
    body: { appendChild: () => {}, contains: () => true },
    documentElement: {},
    activeElement: null,
    hidden: false,
  };
  function uniq(list) { return [...new Set(list)]; }

  const uiText = (o) => (o && typeof o === "object" ? [o.title, o.message].filter(Boolean).join("\n") : String(o));
  const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/ /g, "&nbsp;");
  const clone = (x) => JSON.parse(JSON.stringify(x));
  const ctx = {
    console, JSON, Math, Number, String, Array, Object, Set, Map, Promise, Date, RegExp, Error, isNaN, parseFloat, parseInt, encodeURIComponent,
    document, H, esc, window: { addEventListener: () => {} },
    location: { hash: "" },
    localStorage: { getItem: () => null, setItem: () => {} }, sessionStorage: { getItem: () => null, setItem: () => {} },
    CSS: { escape: (s) => String(s) },
    MutationObserver: class { observe() {} },
    setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
    alert: (m) => H.log.push(["alert", String(m)]),
    confirm: (m) => { H.log.push(["confirm", String(m)]); return H.confirm; },
    prompt: (m) => { H.log.push(["prompt", String(m)]); return "測試原因"; },
    // 1022-1（#263）：工具自己的視窗（ui.js）不載進來，換成一樣記進 H.log 的假版本：
    // 「按主要按鈕／取消」照 H.confirm；記的文字是標題＋訊息（跟以前內建框那一整段一樣看得到）
    uiConfirm: async (o) => { H.log.push(["confirm", uiText(o)]); return H.confirm; },
    uiPrompt: async (o) => { H.log.push(["prompt", uiText(o)]); return "測試原因"; },
    uiAlert: async (o) => { H.log.push(["alert", uiText(o)]); },
    uiErrorAlert: async (o) => { H.log.push(["alert", uiText(o)]); },   // 10-23：錯誤視窗（多一顆複製錯誤資訊），記法跟 uiAlert 一樣
    // 1022-2（#264）：右下角浮動通知，記成 toast（跟要按「知道了」的 alert 分開）
    uiToast: (m) => { H.log.push(["toast", String(m)]); },
    apiGet: async (url) => { H.log.push(["GET", url]); const k = Object.keys(H.apiGetData).find((p) => url.startsWith(p)); return clone(k ? H.apiGetData[k] : {}); },
    apiPost: async (url, body) => { H.log.push(["POST", url, clone(body === undefined ? null : body)]); return clone(H.api[url] || H.api["*"] || {}); },
    fmtStamp: (iso) => { const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(iso || "")); return m ? `${m[2]}-${m[3]} ${m[4]}:${m[5]}` : String(iso || ""); },
    currentRouteId: () => H.route || "step3",
    render: () => H.log.push(["render"]),
    contentEl: null, fcBackKey: null, execBackRow: null, fileOutInit: () => {},
  };
  ctx.contentEl = makeEl("main", { id: "content" }, "");
  ctx.globalThis = ctx;
  H.makeEl = makeEl;
  H.clone = clone;
  H.fireAll = async (pick) => {
    const ls = H.listeners.slice();
    for (const l of ls) {
      if (pick && !pick(l)) continue;
      const ev = { type: l.type, target: l.el, currentTarget: l.el, key: "Enter", preventDefault() {}, stopPropagation() {}, isComposing: false };
      H.log.push(["觸發", l.el.id || l.el.className || l.el.tagName, l.type]);
      try { await l.fn(ev); } catch (e) { H.log.push(["錯誤", String(e && e.message)]); }
      if (H.after) H.after();
    }
  };
  H.listenerSig = () => H.listeners.map((l) => [l.el.id || "", l.el.className || "", l.el.tagName, l.type, JSON.stringify(l.el.dataset)]);
  vm.createContext(ctx);
  // 10-16 #225：問號說明 helpTip() 在 app.js（harness 不載 app.js），只抽這一個純函式進來
  const appSrc = fs.readFileSync(path.join(__dirname, "..", "bookclub", "web", "app.js"), "utf8");
  const tipAt = appSrc.indexOf("function helpTip(");
  vm.runInContext(appSrc.slice(tipAt, appSrc.indexOf("\n}\n", tipAt) + 3), ctx, { filename: "app.js:helpTip" });
  for (const f of files) vm.runInContext(fs.readFileSync(f, "utf8"), ctx, { filename: f });
  if (setup) vm.runInContext(setup, ctx);
  return ctx;
}

async function main() {
  const files = process.argv[2].split(",").filter(Boolean);
  const spec = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
  const out = {};
  for (const sc of spec["情境"]) {
    const ctx = makeEnv(files, spec["前置"] || "");
    try {
      out[sc["名稱"]] = await vm.runInContext(`(async () => { ${sc["程式"]} })()`, ctx);
    } catch (e) {
      out[sc["名稱"]] = { "錯誤": String(e && e.stack) };
    }
  }
  process.stdout.write(JSON.stringify(out));
}

if (require.main === module) main();
module.exports = { makeEnv };
