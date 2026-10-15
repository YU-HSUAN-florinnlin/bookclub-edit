"use strict";

/* 第 3 步「覆核工作台」（09-26 改版：邊看影片邊逐筆通過）。
 * 版面：頂端一行（進度、花了多少時間、推算整支要多久、開始前 4 件事、設定、快捷鍵、匯出）
 *   → 上半部左欄：影片、時間列、整支時間軸（老師灰、學員綠、冥想導讀淡藍；名字、重疊、刪除標記）、圖例
 *     上半部右欄：「目前這一筆」卡片（內容、藍底建議＋原因、通過／改做法／上一筆／下一筆）
 *   → 下半部：篩選列＋一行一筆的清單。
 * 一進來先做「開始前 4 件事」（① 建議刪除段落、② 辨識學員聲音是誰、③ 辨識其他名稱如何替換、④ 誰保留原聲），做完按「開始逐筆看」才進清單。
 * 播放完全由人控制；影片播到下一筆的起點時，右欄自動換過去（焦點在文字框時不換）。
 * 「＋新增修改」面板（09-29）：選類型、起點終點（打時間／用目前播放位置／±0.1 秒）、試聽，按新增後後端對齊時間，
 *   畫面顯示「你標的 → 對齊後」；已經有的項目按「改時間」用同一個面板。
 * 快捷鍵：Enter 通過（不跳）、E 改做法、↑／↓ 上一筆／下一筆、空白鍵播放暫停、J／L 前後 5 秒、I／O 把目前時間填進起點／終點、
 * Esc（文字框裡）從頭播這一筆。資料：GET /api/review；存檔：/api/turns/*、/api/review/*。
 * 依賴 app.js 的 apiGet／apiPost／esc／contentEl／currentRouteId。 */

const RV_FILTERS = [["全部", "全部"], ["還沒確認", "還沒確認"], ["學員段落", "學員段落"], ["名字", "名字"],
  ["學員名字", "學員提到名字"], ["重疊", "重疊"], ["刪除段落", "剪掉"],
  ["局部消音", "消音"], ["改成老師", "改成老師"]];   // 10-01：以前沒有這兩類的篩選，各篩選加起來對不上「全部」

const rv = {
  data: null, video: null, cur: null, filter: "全部", open: false, prepOpen: false, prepTab: "刪除",
  lastT: 0, hold: null, lastActivity: Date.now(), timeTimer: null, splitOpen: null, stopAt: null,
  ed: { open: false, kind: "刪除段落", id: null, a: null, b: null, busy: false, result: null },
};

// ---------------------------------------------------------------------------
// 10-15 第5步2.0 第2段：卡片元件抽到 cards.js（第 5 步共用）。這是第 3 步的情境（說明見 cards.js 開頭）
// ---------------------------------------------------------------------------

const RV_CARD_CTX = {
  mode: "第3步",
  data: () => rv.data,
  reload: () => rvReload(),
  post: (url, body, opts) => apiPost(url, body, opts),
  play: (a, stop) => { rv.stopAt = stop; rvSeek(a, true); },
  videoTime: () => (rv.video ? rv.video.currentTime : null),
  seek: (t) => rvSeek(t),
  select: (key, opts) => { rv.filter = "全部"; rvRenderList(); rvSelect(key, opts); },
  openEditor: (it, rng) => {
    rvOpenEditor(it);
    if (rng) { rv.ed.a = rng[0]; rv.ed.b = rng[1]; rvRenderIO(); rvRenderTimeline(); }
  },
  prefill: (kind, a, b, who, note) => rvPrefill(kind, a, b, who, note),
  askOutside: (before) => rvAskNewOutside(before),
  refreshSug: () => rvRefreshSug(),
  moreOpen: () => rv.open,
  toggleMore: () => rvToggleMore(),
  rerender: () => rvRenderMain(),
};
function rvUseCardCtx() { cvUse(RV_CARD_CTX); }

function rvItem(key) { return rv.data["項目"].find((x) => rvKey(x) === key); }
function rvItems() { return rv.data["項目"]; }

function rvPrepDone() {
  const p = rv.data["開始前確認"] || {};
  return RV_PREP.every(([k]) => p[k]);
}

// 10-08 宇軒（流程簡化）：「開始前 4 件事」只有 ② 學員是誰真的要做（沒配代號的學員 AI 重念會念出本名）；
// ①③④ 沒做照預設（① 建議剪掉的不剪、③ 名冊上的人已自動換代號、不認得的名字留原聲、④ 全部重新生成）
function rvWhoLeft() { return (rv.data && rv.data["②還沒做"]) || []; }
function rvWhoDone() { return !rvWhoLeft().length; }

// ---------------------------------------------------------------------------
// 進入點
// ---------------------------------------------------------------------------

// 10-01 1-2、1-3、2-4：從第 4 步總檢查、第 5 步成品檢查跳過來改某一筆（帶著卡片、要改的起訖）
// go = { key: 卡片的鍵, openMore: 打開「改做法」, edit: {類型, id?, 名稱?, start, end, 說話者?}, note: 上面要顯示的一行, back: 回哪一步 }
let rvGoto = null;

function rvJump(go) {
  rvGoto = go;
  if (location.hash === "#step3") render(); else location.hash = "#step3";
}

function rvApplyGoto(go) {
  rv.prepOpen = false;
  rv.filter = "全部";
  if (go.key && rvItem(go.key)) { rv.cur = go.key; rv.open = !!go.openMore; }
  if (go.edit) {
    const e = go.edit;
    Object.assign(rv.ed, { open: true, result: null, kind: e["類型"], id: e.id || null, a: e.start, b: e.end,
      label: e["名稱"] || null, who: e["說話者"] || rv.ed.who, whole: !!e["老師整段"] });
  }
  rv.goNote = go.note ? { text: go.note, back: go.back || "step4", backKey: go.backKey || null } : null;
}

function rvGoNoteHtml() {
  const n = rv.goNote;
  if (!n) return "";
  const back = n.back === "step5" ? ["#step5", "回第 5 步成品檢查"] : ["#step4", "回第 4 步"];
  return `<p class="rv-gonote">${esc(n.text)}　<a href="${back[0]}" id="rv-goback">${back[1]}</a> <button class="ghost small" id="rv-gonote-x" aria-label="關掉這一行">×</button></p>`;
}

async function renderReview() {
  rvUseCardCtx();   // 10-15 第5步2.0 第2段：卡片元件用第 3 步的情境
  contentEl.innerHTML = "<p>載入中…</p>";
  rv.data = await apiGet("/api/review");
  // 10-07：進第 3 步時 ③ 名冊上的人自動配一次代號（後端記旗標，只做一次；GET 不寫檔，所以放在這裡 POST）
  if (!rv.data["代號開始前已配"] && !rv.data["AI執行中"]) {
    try {
      const r = await apiPost("/api/codes/initial", {});
      if (r["③"]) rv.data = await apiGet("/api/review");
    } catch (e) { /* 配不成不擋第 3 步，「幫還沒代號的自動配」照樣能按 */ }
  }
  rv.prepOpen = !rvWhoDone();   // 10-08：② 還沒做才先打開「開始前 4 件事」
  const go = rvGoto;
  rvGoto = null;
  rv.goNote = null;
  if (go) rvApplyGoto(go);
  const v = rv.data["影片"];
  contentEl.classList.add("wide");
  contentEl.innerHTML = `
    <div class="rv2">
      <header class="rv-bar">
        <h1>3　覆核工作台</h1>
        <div class="rv-progress" id="rv-progress"></div>
        <div class="rv-bar-btns">
          <button class="ghost" id="rv-prep-btn">開始前 4 件事</button>
          <button class="ghost" id="rv-set-btn" aria-expanded="false">設定</button>
          <button class="ghost" id="rv-key-btn" aria-expanded="false">快捷鍵</button>
          <button class="ghost" id="rv-code-btn" aria-expanded="false" title="本名和代號的對照（截圖或分享螢幕時會露出本名）">代號對照</button>
          <button class="ghost" id="rv-export" title="只含老師提到名字的部分，匯入後不能跑第 4、5 步；要交接整個專案請複製整個工作區資料夾">匯出覆核結果</button>
          <button class="primary" id="rv-go4" disabled>開始 AI 修改</button>
        </div>
      </header>
      <div class="rv-busy" id="rv-busy" role="status" hidden></div>
      <div class="rv-drawer" id="rv-keys" hidden>
        <dl class="rv-keylist">
          <dt>Enter</dt><dd>通過這一筆（影片照常播、不跳走；文字框裡 Shift＋Enter 換行）</dd>
          <dt>E</dt><dd>改做法（展開全部選項）</dd>
          <dt>↑ ↓</dt><dd>上一筆／下一筆（影片跳到那筆前 2 秒）</dd>
          <dt>空白鍵</dt><dd>播放／暫停</dd>
          <dt>J　L</dt><dd>往前／往後 5 秒</dd>
          <dt>I　O</dt><dd>把目前播放位置填進「新增修改」的起點／終點（面板沒開會自動打開）</dd>
          <dt>Esc</dt><dd>在文字框裡：從頭播這一筆</dd>
        </dl>
      </div>
      <div class="rv-drawer" id="rv-settings" hidden></div>
      <div class="rv-drawer" id="rv-codes" hidden></div>
      <div class="rv-export-msg" id="rv-export-msg"></div>
      <section class="rv-upper">
        <div class="rv-left">
          ${v["有影片"] ? `<video id="rv-video" controls preload="metadata" src="${esc(v["網址"])}"></video>`
            : `<div class="rv-novideo">找不到原片（${esc(v["檔名"] || "沒有記錄影片路徑")}）。用 <code>bookclub serve &lt;工作區&gt; --video &lt;影片&gt;</code> 重開。</div>`}
          <div class="rv-timebar">
            <span class="rv-clock"><b id="rv-clock">0:00</b> ／ ${esc(rvFmt(v["長度"]))}</span>
            <input id="rv-goto" class="rv-goto" placeholder="跳到 43:15" aria-label="跳到時間（例如 43:15，按 Enter）">
            <select id="rv-rate" aria-label="播放速度"><option value="1">1 倍速</option><option value="1.25">1.25 倍速</option><option value="1.5">1.5 倍速</option><option value="2">2 倍速</option></select>
          </div>
          <div class="rv-tl" id="rv-tl" title="點一下或拖拉，跳到那個時間"></div>
          <div class="rv-legend" id="rv-legend"></div>
          <div class="rv-io" id="rv-io"></div>
        </div>
        <div class="rv-right" id="rv-right"></div>
      </section>
      <section class="rv-lower" id="rv-lower"></section>
    </div>`;

  rv.video = document.getElementById("rv-video");
  if (rv.video) rvBindVideo();
  document.getElementById("rv-goto").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.isComposing) return;
    e.preventDefault();
    const t = rvParseTime(e.target.value);
    e.target.classList.toggle("bad", t == null);
    if (t != null) { rvSeek(t); e.target.blur(); }
  });
  document.getElementById("rv-rate").addEventListener("change", (e) => { if (rv.video) rv.video.playbackRate = Number(e.target.value); });
  document.getElementById("rv-prep-btn").addEventListener("click", () => { rv.prepOpen = !rv.prepOpen; rvRenderMain(); });
  document.getElementById("rv-set-btn").addEventListener("click", (e) => rvToggleDrawer("rv-settings", e.currentTarget));
  document.getElementById("rv-key-btn").addEventListener("click", (e) => rvToggleDrawer("rv-keys", e.currentTarget));
  document.getElementById("rv-code-btn").addEventListener("click", (e) => rvToggleDrawer("rv-codes", e.currentTarget));
  document.getElementById("rv-export").addEventListener("click", rvExport);
  document.getElementById("rv-go4").addEventListener("click", rvConfirmGo4);
  rvBindTimeline(document.getElementById("rv-tl"));
  window.addEventListener("resize", rvRenderTimeline);

  if (!rv.cur) rv.cur = rvFirstPending();
  rvRenderAll();
  rvStartTimeTracking();
  rvRenderGoNote();
  if (go) {
    if (go.key && rvItem(go.key)) { rvSelect(go.key); if (go.openMore && !rv.open) rvToggleMore(); }
    else if (go.edit) rvSeek(Math.max(0, go.edit.start - 2));
    const box = document.getElementById(go.edit ? "rv-io" : "rv-right");
    if (box) box.scrollIntoView({ block: "nearest" });
  }
}

function rvRenderGoNote() {
  const el = document.getElementById("rv-export-msg");
  if (!el) return;
  el.innerHTML = rvGoNoteHtml();
  const x = document.getElementById("rv-gonote-x");
  if (x) x.addEventListener("click", () => { rv.goNote = null; rvRenderGoNote(); });
  // 10-01 第三批 13：回第 4 步時捲到總檢查裡出發的那一列；回第 5 步時回到出發的那一筆
  const back = document.getElementById("rv-goback");
  const n = rv.goNote;
  if (back && n) back.addEventListener("click", () => {
    if (n.back === "step5") fcBackKey = n.backKey || fc.cur;
    else execBackRow = n.backKey || "";
  });
}

function rvToggleDrawer(id, btn) {
  const el = document.getElementById(id);
  el.hidden = !el.hidden;
  btn.setAttribute("aria-expanded", String(!el.hidden));
  if (id === "rv-settings" && !el.hidden) rvRenderSettings();
  if (id === "rv-codes" && !el.hidden) rvRenderCodeMap();
}

// 10-07 宇軒：「代號對照」抽屜——檢查後面內容的時候隨時叫得出來：上半 本名 → 代號 → 哪幾位學員 N → 來源；
// 下半 全部代號分女男，標已用（給了誰）／未用。資料是 GET /api/review 的「代號對照」（後端 epcodes.code_table，只讀）
// 10-15 宇軒驗證回饋（「視覺上太花、太繁複」）：本名後面的「也寫成⋯⋯」不顯示；「來源」整欄變灰；
// 已用的代號只寫給了誰（拿掉「已用」兩個字），未用的代號連框框一起變淡——最明顯的是已經配出去的名字
function rvRenderCodeMap() {
  const el = document.getElementById("rv-codes");
  if (!el) return;
  const t = rv.data["代號對照"] || {};
  const rows = t["對照"] || [];
  const stuNo = (n) => n.replace(/^學員(\d+)$/, "學員 $1");
  const body = rows.length ? rows.map((r) => `<tr>
      <td><b>${esc(r["本名"])}</b></td>
      <td>${r["代號"] ? `<b>${esc(r["代號"])}</b>` : '<span class="rv-warnline">還沒代號</span>'}${r["自動配未改過"] ? ' <span class="rv-tag" title="程式自動配的，人還沒改過">自動配</span>' : ""}</td>
      <td>${esc((r["學員"] || []).map(stuNo).join("、") || "—")}</td>
      <td class="rv-cm-src">${esc(r["來源"] || "—")}${r["性別"] ? `・${esc(r["性別"])}（${esc(r["性別依據"] || "")}）` : ""}</td></tr>`).join("")
    : '<tr><td colspan="4" class="rv-meta">還沒有人有代號：先在「開始前 4 件事」② 選學員是誰。</td></tr>';
  const noReal = t["還沒選本名"] || [];
  const pool = t["名單"] || {};
  const chips = (list) => list.map((e) => `<span class="rv-codechip${e["給了"].length ? " used" : " unused"}">${esc(e["代號"])}
      ${e["給了"].length ? `<span class="rv-cm-who">${esc(e["給了"].join("、"))}</span>` : '<span class="rv-meta">未用</span>'}${(e["撞名"] || []).length ? `<span class="rv-warnline">撞名：${esc(e["撞名"].join("、"))}</span>` : ""}</span>`).join("");
  el.innerHTML = `<p class="rv-warnline">截圖或分享螢幕時，這裡會露出本名。用完再按一次「代號對照」收起來。</p>
    <table class="rv-codemap"><thead><tr><th>本名</th><th>代號</th><th>學員</th><th class="rv-cm-src">來源</th></tr></thead><tbody>${body}</tbody></table>
    ${noReal.length ? `<p class="rv-meta">還沒選本名：${esc(noReal.map(stuNo).join("、"))}</p>` : ""}
    <h3>全部代號</h3>
    ${[["女", "女生名單"], ["男", "男生名單"], ["其他", "自己打的、舊的"]].filter(([k]) => (pool[k] || []).length)
      .map(([k, lab]) => `<div class="rv-codepool"><b>${lab}</b>${chips(pool[k])}</div>`).join("")}`;
}

function rvFirstPending() {
  const it = rvItems().find((x) => !rvDone(x)) || rvItems()[0];
  return it ? rvKey(it) : null;
}

function rvRenderAll() {
  rvRenderTimeline();
  rvRenderLegend();
  rvRenderProgress();
  rvRenderMain();
  rvRenderIO();
  rvApplyReadonly();
}

// ---------------------------------------------------------------------------
// 09-30：第 4 步 AI 執行中，第 3 步變唯讀（改了這次執行也用不到，還會跟正在跑的生成打架）
// 看、播放、篩選、上一筆下一筆照常；其他按鈕與輸入框鎖住，上方橫幅說明。後端也擋（409）。
// ---------------------------------------------------------------------------

const RV_RO_ALLOW = "#rv-prep-btn, #rv-set-btn, #rv-key-btn, #rv-code-btn, #rv-export, #rv-prev, #rv-next, .rv-filters button, [data-tab], .rv-segplay, .rv-sample, #rv-playonly, [data-te-play], [data-te-close], #rv-busy button";

function rvReadonly() { return !!(rv.data && rv.data["AI執行中"]); }

function rvApplyReadonly() {
  const ro = rvReadonly();
  const bar = document.getElementById("rv-busy");
  if (bar) {
    bar.hidden = !ro;
    bar.innerHTML = ro ? `AI 正在執行第 4 步：第 3 步現在只能看、不能改。要改的話到 <a href="#step4">第 4 步</a> 按「停止」，或等它跑完。
      <button class="ghost small" id="rv-busy-check">再檢查一次</button>` : "";
    const b = document.getElementById("rv-busy-check");
    if (b) b.addEventListener("click", rvReload);
  }
  const root = document.querySelector(".rv2");
  if (!root) return;
  root.classList.toggle("readonly", ro);
  root.querySelectorAll("#rv-right, #rv-lower, #rv-settings, #rv-io, .rv-bar-btns").forEach((box) => {
    box.querySelectorAll("button, input, select, textarea").forEach((el) => {
      if (el.matches(RV_RO_ALLOW)) return;
      if (ro) { if (!el.disabled) { el.disabled = true; el.dataset.ro = "1"; } }
      else if (el.dataset.ro) { el.disabled = false; delete el.dataset.ro; }
    });
  });
}

function rvRenderMain() {
  document.getElementById("rv-prep-btn").classList.toggle("on", rv.prepOpen);
  if (rv.prepOpen) { rvRenderPrepSide(); rvRenderPrep(); rvApplyReadonly(); return; }
  rvRenderCard();
  rvRenderList();
  rvApplyReadonly();
}

async function rvReload() {
  rv.data = await apiGet("/api/review");
  const back = rv.data["開始前自動改回"] || [];
  if (back.length) { rv.prepOpen = true; rv.prepTab = back[0]; }   // 09-30：冒出新項目、自動改回還沒做的，直接打開給人看
  if (rv.cur && !rvItem(rv.cur)) rv.cur = rvFirstPending();
  rvRenderAll();
  const s = document.getElementById("rv-settings");
  if (s && !s.hidden) rvRenderSettings();
  const c = document.getElementById("rv-codes");
  if (c && !c.hidden) rvRenderCodeMap();
}

// ---------------------------------------------------------------------------
// 播放器：播到下一筆的起點時自動換筆（焦點在文字框時不換）
// ---------------------------------------------------------------------------

function rvTyping() {
  const a = document.activeElement;
  return !!a && ["TEXTAREA", "INPUT", "SELECT"].includes(a.tagName) && !!a.closest(".rv-right, .rv-lower");
}

function rvBindVideo() {
  const v = rv.video;
  v.addEventListener("timeupdate", () => {
    document.getElementById("rv-clock").textContent = rvFmt(v.currentTime, 1);
    if (rv.stopAt != null && v.currentTime >= rv.stopAt) { v.pause(); rv.stopAt = null; }
    rvMovePlayhead();
    rvFollow(v.currentTime);
  });
  v.addEventListener("seeked", () => { rvMovePlayhead(); rvFollow(v.currentTime, true); });
}

function rvFollow(t, seeked = false) {
  const prev = rv.lastT;
  rv.lastT = t;
  if (rv.prepOpen || rvTyping()) return;
  if (rv.hold) {   // 按了上一筆／下一筆：影片從那筆前 2 秒開始播，播到那筆之前不要換走
    const it = rvItem(rv.hold);
    if (it && t >= it.start - 3 && t <= it.end + 0.5 && t < it.start) return;
    rv.hold = null;
  }
  const cand = rvItems().filter((x) => !x["不用處理"]);
  let hit = null;
  if (!seeked && t >= prev && t - prev < 2) {
    // 照常播放：剛好跨過某一筆的起點
    hit = cand.filter((x) => x.start > prev && x.start <= t).pop();
  } else {
    // 跳過去的：落在哪一筆裡面就換到那一筆（起點最晚的那筆）
    hit = cand.filter((x) => x.start - 0.2 <= t && t <= x.end + 0.2).pop();
  }
  if (hit && rvKey(hit) !== rv.cur) rvSelect(rvKey(hit), { seek: false, auto: true });
}

function rvSeek(t, play = false) {
  if (!rv.video) return;
  rv.video.currentTime = Math.max(0, Math.min(t, rv.data["影片"]["長度"] || t));
  if (play) rv.video.play().catch(() => {});
  rvMovePlayhead();
}

function rvSelect(key, { seek = true, auto = false } = {}) {
  if (!key || !rvItem(key)) return;
  rv.cur = key;
  rv.open = false;
  const it = rvItem(key);
  if (seek) { rv.hold = key; rvSeek(Math.max(0, it.start - 2)); }
  rvRenderCard();
  rvMarkListRow(true);
  rvApplyReadonly();
}

function rvStep(dir) {   // 上一筆／下一筆跳過「不用處理」的（目前這筆除外）
  const list = rvVisible().filter((x) => !x["不用處理"] || rvKey(x) === rv.cur);
  if (!list.length) return;
  const i = list.findIndex((x) => rvKey(x) === rv.cur);
  const next = list[Math.max(0, Math.min(list.length - 1, i < 0 ? 0 : i + dir))];
  rvSelect(rvKey(next));
}

// ---------------------------------------------------------------------------
// 時間軸：老師灰、學員綠（同一色系）、冥想導讀淡藍；標記：名字、重疊、刪除
// ---------------------------------------------------------------------------

function rvBindTimeline(el) {
  const seekAt = (e) => {
    const r = el.getBoundingClientRect();
    const x = Math.min(Math.max(0, e.clientX - r.left), r.width);
    rvSeek((x / r.width) * (rv.data["影片"]["長度"] || 0));
  };
  let down = false;
  el.addEventListener("pointerdown", (e) => {
    down = true;
    seekAt(e);
    try { el.setPointerCapture(e.pointerId); } catch (err) { /* 合成事件沒有真的指標 */ }
  });
  el.addEventListener("pointermove", (e) => { if (down) seekAt(e); });
  const end = (e) => {
    down = false;
    try { el.releasePointerCapture(e.pointerId); } catch (err) { /* 同上 */ }
  };
  el.addEventListener("pointerup", end);
  el.addEventListener("pointercancel", end);
}

function rvRenderTimeline() {
  const el = document.getElementById("rv-tl");
  if (!el || !rv.data) return;
  const dur = rv.data["影片"]["長度"] || 1;
  const pct = (t) => (t / dur) * 100;
  const bands = rv.data["色帶"].map((x) => {
    const cls = x["冥想導讀"] ? "calm" : x["人改成老師"] ? "teacher tfix" : x["說話者"] === "老師" ? "teacher" : "stu";
    const style = cls === "stu" ? `;background:${rvStuColor(x["說話者"])}` : "";
    return `<i class="b ${cls}" style="left:${pct(x.start)}%;width:${Math.max(0.12, pct(x.end - x.start))}%${style}"></i>`;
  }).join("");
  const marks = rvItems().map((it) => {
    const t = it["類型"];
    if (rvIsName(t)) return `<i class="m name" style="left:${pct(it.start)}%"></i>`;
    if (t === "重疊") return `<i class="m ov" style="left:${pct(it.start)}%"></i>`;
    if (t === "刪除段落" && it["狀態"] !== "還原") {
      return `<i class="m cut${it["來源"] === "建議" && !it["決定"] ? " pending" : ""}" style="left:${pct(it.start)}%;width:${Math.max(0.3, pct(it.end - it.start))}%"></i>`;
    }
    return "";
  }).join("");
  const io = rv.ed.open ? [rv.ed.a, rv.ed.b].map((t) => (t != null ? `<i class="io" style="left:${pct(t)}%"></i>` : "")).join("") : "";
  const cur = rv.cur && rvItem(rv.cur);
  const curMark = cur ? `<i class="cur" style="left:${pct(cur.start)}%;width:${Math.max(0.3, pct(cur.end - cur.start))}%"></i>` : "";
  el.innerHTML = `<div class="track">${bands}</div>${marks}${curMark}${io}<i class="head" id="rv-head"></i>`;
  rvMovePlayhead();
}

function rvMovePlayhead() {
  const head = document.getElementById("rv-head");
  if (head && rv.video) head.style.left = `${(rv.video.currentTime / (rv.data["影片"]["長度"] || 1)) * 100}%`;
}

function rvRenderLegend() {
  document.getElementById("rv-legend").innerHTML = `
    <span><i class="sw teacher"></i>老師</span><span><i class="sw stu"></i>學員</span><span><i class="sw calm"></i>冥想導讀</span>
    <span class="gap"><i class="sw name"></i>名字</span><span><i class="sw ov"></i>重疊</span><span><i class="sw cut"></i>剪掉（連畫面）</span><span><i class="sw teacher tfix"></i>改成老師</span>`;
}

// ---------------------------------------------------------------------------
// 頂端：進度
// ---------------------------------------------------------------------------

function rvRenderProgress() {
  const p = rv.data["進度"];
  const pct = p["總數"] ? Math.round((p["已確認"] / p["總數"]) * 100) : 0;
  const est = p["推算全部秒數"] != null ? rvHm(p["推算全部秒數"]) : "先通過幾筆";
  document.getElementById("rv-progress").innerHTML = `
    <div class="bar" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><i style="width:${pct}%"></i></div>
    <span><b>${p["已確認"]}</b>／${p["總數"]} 筆</span><span>花了 ${rvHm(p["已花秒數"])}</span><span>推算整支 ${est}</span>`;
  rvUpdateGo4();
}

// ---------------------------------------------------------------------------
// 開始 AI 修改 → 確認 → 跳第 4 步（09-29 宇軒；10-08 流程簡化：卡片不用全部通過，只看 ② 學員是誰）
// ---------------------------------------------------------------------------

function rvLeft() { return rvItems().filter((x) => !rvDone(x)).length; }

// 「開始 AI 修改」按鈕的狀態（純函式）：只有 ② 還沒做、或 AI 正在跑（唯讀）時不能按；沒看完的卡片照目前的設定做
function rvGo4State(whoLeft, left, readonly) {
  if (readonly) return { disabled: true, title: "AI 正在執行第 4 步" };
  if (whoLeft.length) return { disabled: true, title: `先做完「開始前 4 件事」② 學員是誰：${whoLeft.join("；")}` };
  return { disabled: false, title: left ? `還有 ${left} 張卡片沒看，會照目前的設定做（不擋）` : "卡片都看過了，可以開始 AI 修改" };
}

function rvUpdateGo4() {
  const btn = document.getElementById("rv-go4");
  if (!btn) return;
  const st = rvGo4State(rvWhoLeft(), rvLeft(), !!rv.data["AI執行中"]);
  if (btn.dataset.ro && !st.disabled) return;   // 唯讀時由 rvApplyReadonly 管
  btn.disabled = st.disabled;
  btn.title = st.title;
}

// 開始對話框裡的說明（純函式）：沒看完的卡片幾張、開始前 4 件事 ①③④ 沒做的照預設
const RV_PREP_DEFAULT = { "刪除": "① 建議剪掉的段落不剪", "名字": "③ 名冊上的人已自動換代號，不認得的名字留原聲", "保留原聲": "④ 學員全部重新生成" };
function rvGo4Notes(left, prep) {
  const out = [];
  if (left) out.push(`還有 ${left} 張卡片沒看：照目前的設定做（卡片上的建議）。`);
  const skip = Object.keys(RV_PREP_DEFAULT).filter((k) => !(prep || {})[k]).map((k) => RV_PREP_DEFAULT[k]);
  if (skip.length) out.push(`開始前 4 件事沒做的照預設：${skip.join("；")}。`);
  return out;
}

// 10-08 宇軒：確認視窗字太多 → 只留一句，其他收進「詳細」
const RV_GO4_MAIN = "略過或照建議通過的項目，第 5 步都可以再檢查。要開始 AI 修改嗎？";

async function rvConfirmGo4() {
  if (!rvWhoDone()) { rv.prepOpen = true; rv.prepTab = "學員"; rvRenderMain(); return; }
  if (rv.video && !rv.video.paused) rv.video.pause();
  const notes = rvGo4Notes(rvLeft(), rv.data["開始前確認"]);
  const dlg = document.createElement("dialog");
  dlg.className = "rv-confirm";
  dlg.setAttribute("aria-labelledby", "rv-confirm-title");
  dlg.innerHTML = `
    <h2 id="rv-confirm-title">開始 AI 修改？</h2>
    <p id="rv-confirm-main">${esc(RV_GO4_MAIN)}</p>
    <details class="rv-confirm-more"><summary>詳細</summary>
      ${notes.map((x) => `<p>${esc(x)}</p>`).join("")}
      <p>開始之後，AI 會依序生成老師的名字句子、學員重念，最後組裝成品影片。<b>整支影片會花比較多時間</b>，
        跑的時候電腦不要睡眠；第 4 步看得到每一類做到幾筆。</p>
      <p class="muted">第 4 步的總檢查預設先略過；跑完之後到第 5 步「成品檢查」看結果，「需留意」那一頁列出成品裡還留著原聲、要聽一下的地方。中途有問題可以停下來，已經做好的不會重做；上一支成品在新的組裝做完之前都照樣留著。</p>
    </details>
    <p class="rv-confirm-err" id="rv-confirm-err" role="alert"></p>
    <div class="rv-confirm-btns">
      <button class="ghost" id="rv-confirm-back">回去檢查</button>
      <button class="primary" id="rv-confirm-go">開始修改</button>
    </div>`;
  document.body.appendChild(dlg);
  const close = () => { dlg.close(); dlg.remove(); };
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(); });   // Esc＝回去檢查
  dlg.querySelector("#rv-confirm-back").addEventListener("click", close);
  dlg.querySelector("#rv-confirm-go").addEventListener("click", async (e) => {
    const go = e.currentTarget;
    go.disabled = true; go.textContent = "開始中…";
    try {
      await apiPost("/api/execute/start", { start: null, end: null, methods: null }, { quiet: true });
    } catch (err) {
      if (!/已經有.*在跑/.test(err.message)) {       // 已經在跑就直接去第 4 步看進度
        dlg.querySelector("#rv-confirm-err").textContent = `還不能開始：${err.message}`;
        go.disabled = false; go.textContent = "開始修改";
        return;
      }
    }
    close();
    location.hash = "#step4";
  });
  dlg.showModal();
  dlg.querySelector("#rv-confirm-back").focus();   // 預設焦點放在「回去檢查」，按 Enter 不會誤觸開始
}

// ---------------------------------------------------------------------------
// 10-08 宇軒（流程簡化）：「全部照建議通過」（後端 review.pass_all）
// ---------------------------------------------------------------------------

function rvPassAllHtml() {
  const left = rvLeft();
  return `<button class="secondary" id="rv-passall" ${left ? "" : "disabled"} title="有建議、還沒確認的卡片一次照建議通過；建議剪掉、文字裡還有本名、名字換不了代號、人工新增的等等不會幫你按">全部照建議通過</button>`;
}

function rvBindPassAll(box) {
  const b = box && box.querySelector("#rv-passall");
  if (b) b.addEventListener("click", rvPassAll);
}

// 一鍵通過之後的摘要對話框內容（純函式）
function rvPassAllMsg(r) {
  const kinds = (r && r["依類型"]) || [];
  return { title: `通過了 ${(r && r["通過"]) || 0} 張`, left: (r && r["還要看"]) || 0,
    lines: kinds.map((k) => `${k["說明"]}：${k["筆數"]} 張`),
    note: (r && r["還要看"]) ? "剩下的不擋「開始 AI 修改」，沒看的照目前的設定做。清單上方的篩選選「還沒確認」看得到是哪幾張。" : "卡片都處理好了。" };
}

async function rvPassAll() {
  if (rvReadonly()) return;
  const b = document.getElementById("rv-passall");
  if (b) { b.disabled = true; b.textContent = "通過中…"; }
  let r;
  try { r = await apiPost("/api/review/passall", {}); }
  catch (err) { alert(err.message); if (b) { b.disabled = false; b.textContent = "全部照建議通過"; } return; }
  await rvReload();
  const m = rvPassAllMsg(r);
  const dlg = document.createElement("dialog");
  dlg.className = "rv-confirm";
  dlg.setAttribute("aria-labelledby", "rv-passall-title");
  dlg.innerHTML = `<h2 id="rv-passall-title">${esc(m.title)}${m.left ? `，還有 ${m.left} 張要看` : ""}</h2>
    ${m.lines.length ? `<ul>${m.lines.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
    <p class="muted">${esc(m.note)}</p>
    <div class="rv-confirm-btns"><button class="primary" id="rv-passall-ok">知道了</button></div>`;
  document.body.appendChild(dlg);
  const close = () => { dlg.close(); dlg.remove(); };
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
  dlg.querySelector("#rv-passall-ok").addEventListener("click", close);
  dlg.showModal();
}

function rvRecount() {
  const items = rvItems();
  const p = rv.data["進度"];
  p["已確認"] = items.filter(rvDone).length;
  const passed = items.filter((x) => x["已確認"] && !x["不用處理"]).length;
  const need = items.filter((x) => !x["不用處理"]).length;
  p["推算全部秒數"] = passed && p["已花秒數"] ? Math.round((p["已花秒數"] / passed) * need) : null;
  rvRenderProgress();
}

// ---------------------------------------------------------------------------
// 右欄「目前這一筆」
// ---------------------------------------------------------------------------

function rvVisible() {
  const f = rv.filter;
  return rvItems().filter((x) => f === "全部" ? true : f === "還沒確認" ? !rvDone(x) || rvKey(x) === rv.cur : x["類型"] === f);
}

function rvRenderCard() {
  const box = document.getElementById("rv-right");
  const it = rv.cur && rvItem(rv.cur);
  if (!it) {
    box.innerHTML = `<div class="rv-card empty"><p>沒有要處理的項目。</p></div>`;
    return;
  }
  const all = rvItems();
  const idx = all.findIndex((x) => rvKey(x) === rv.cur);
  const state = it["類型"] === "名字" && it["老師整段"] && rvNotName(it) ? `<span class="rv-state ok">✓ 不用改：這一段保留老師原聲（再按一次取消）</span>`
    : it["類型"] === "名字" && rvNotName(it) ? `<span class="rv-state ok">✓ 不是名字，不用改：抓錯了、這裡沒有人名，照原音保留（這個寫法以後不會再被抓成名字；再按一次取消）</span>`
    : rvInCut(it) ? `<span class="rv-state ok">✓ 通過：這段落在剪掉的片段裡（聲音和畫面都拿掉）${it["剪掉的片段"] ? "" : "（要救回：到蓋住這裡的那一筆「剪掉」，在「改做法」按「還原（不剪）」）"}</span>${it["剪掉的片段"] ? ` <button class="ghost small" id="rv-uncut">取消剪掉（還原這一段）</button>` : ""}`
    : it["涵蓋"] ? `<span class="rv-state ok">✓ 已由〈${esc(it["涵蓋"]["名稱"])}〉涵蓋：這一處會跟著那一筆整段換掉，不用另外選</span> <button class="ghost small" id="rv-gocover">跳到那一筆</button>`
    : it["不用處理"] ? `<span class="rv-state skip">不用處理：${esc(it["不用處理"])}</span>`
    : it["已確認"] && it["還缺"] ? `<span class="rv-warnline">按過通過，但還缺東西（見上面）</span>`
    : it["已確認"] ? `<span class="rv-state ok">✓ 已通過</span>` : "";
  const sug = it["建議"] || {};
  const chosen = rvChosen(it);
  const changed = chosen && sug["做法"] && chosen !== sug["做法"] && it["類型"] !== "學員段落";
  const w0 = it["整句"];
  const when = it["類型"] === "名字" && w0 && !it["老師整段"]   // 10-01：名字卡片寫重念範圍（不再只寫名字那 0.3 秒）
    ? `重念範圍 ${rvFmt(w0.start, 1)}–${rvFmt(w0.end, 1)}（名字在 ${rvFmt(it.start, 1)}）` : `${rvFmt(it.start, 1)}–${rvFmt(it.end, 1)}`;
  const lack = it["還缺"] ? `<p class="rv-warnline rv-lack">還缺：${esc(it["還缺"])}。${it["已確認"] ? "（之前按過通過，但補好之前第 4 步不能開始）" : ""}按「改做法」補好。</p>` : "";
  // 10-01 第三批 5：第 5 步退回的，卡片上寫原因（重做完就消失）
  const back = it["第5步退回"] ? `<p class="rv-warnline rv-back5">第 5 步退回：${esc(it["第5步退回"].join("；"))}</p>` : "";
  const coverNote = back + lack + rvEdgeHtml(it) + (it["涵蓋消失"] ? `<p class="rv-warnline">原本由〈${esc(it["涵蓋消失"]["名稱"] || it["涵蓋消失"].id)}〉涵蓋，現在沒有了（那一筆改了時間、做法或被還原），請重新看。</p>`
    : it["涵蓋待通過"] ? `<p class="rv-note">這一處整個落在〈${esc(it["涵蓋待通過"]["名稱"])}〉裡；那一筆通過之後，這一處就自動算處理好。</p>` : "");
  box.innerHTML = `
    <article class="rv-card" data-key="${esc(rv.cur)}">
      <header>
        ${rvChip(it)}${it["人工新增"] ? `<span class="rv-tag">人工新增</span>` : ""}${it["代號改過"] ? `<span class="rv-tag warn">代號改過，請再看一次</span>` : ""}
        <span class="rv-when">${esc(when)}</span>${rvPlayOnlyHtml(it)}${rvIdTag(it)}
        <span class="rv-count">第 ${idx + 1}／${all.length} 筆</span>
      </header>
      <div class="rv-body">${coverNote}${rvBodyHtml(it)}${rvAlignLine(it)}</div>
      <div class="rv-sug">
        <p class="what">建議：${esc(rvSuggestText(it))}</p>
        <p class="why">${esc(sug["原因"] || "")}</p>
        ${changed ? `<p class="mine">改成：${esc(rvHowLabel(chosen))}</p>` : ""}
      </div>
      ${state ? `<div class="rv-statebox">${state}</div>` : ""}
      <div class="rv-actions">
        <button class="primary" id="rv-pass" title="已通過的再按一次會取消">${rvInCut(it) || it["涵蓋"] ? "✓ 已處理" : it["類型"] === "改成老師" ? "不用處理" : it["已確認"] ? "✓ 已通過（再按取消）" : rvPassCuts(it) ? "通過（這一段會整段剪掉，畫面也剪）" : "通過"}<kbd>Enter</kbd></button>
        <button class="ghost" id="rv-change" aria-expanded="${rv.open}">改做法<kbd>E</kbd></button>
        ${cvToolBtnsHtml(it)}
        <span class="spacer"></span>
        <button class="ghost" id="rv-prev" aria-label="上一筆">上一筆</button>
        <button class="ghost" id="rv-next" aria-label="下一筆">下一筆</button>
      </div>
      <div class="rv-more" id="rv-more" ${rv.open ? "" : "hidden"}>${rvMoreHtml(it)}</div>
    </article>`;
  document.getElementById("rv-pass").addEventListener("click", () => rvPass());
  document.getElementById("rv-change").addEventListener("click", rvToggleMore);
  cvBindTools(it);   // 10-15 第5步2.0 第2段：工具按鈕的動作在 cards.js（第 5 步共用）
  document.getElementById("rv-prev").addEventListener("click", () => rvStep(-1));
  document.getElementById("rv-next").addEventListener("click", () => rvStep(1));
  cvBindPlayShrink(it);
  cvBindPad("rv-pad");   // 10-15 前後 2 秒開關統一（1015-5）：這張卡唯一的一顆（標題列或名字卡的重念範圍那一列）
  rvBindBody(it);
  rvBindOutQ(it);
  rvBindMore(it);
  rvRenderTimeline();
}

// ---------------------------------------------------------------------------
// 10-01 第三批 14：學員段落切短之後，句子切在段落外面、沒被處理蓋到的那幾秒：「是誰的聲音？」
// 切的當下跳出來問（rvAskNewOutside），卡片上也一直看得到、可以改；答案存在覆核決定的「段落外答案」
// ---------------------------------------------------------------------------

function rvPrefill(kind, a, b, who, note) {   // 帶去「新增修改」面板，起訖先填好（還沒存，要按新增）
  Object.assign(rv.ed, { open: true, id: null, label: null, kind, a: Math.round(a * 100) / 100, b: Math.round(b * 100) / 100,
    who: who || rv.ed.who, result: note ? { error: note } : null, whole: false });
  rvRenderIO();
  rvRenderTimeline();
  const box = document.getElementById("rv-io");
  if (box) box.scrollIntoView({ block: "nearest" });
}

// 切的當下就問：切完重新讀資料後，新冒出來、還沒回答的那幾秒跳出來問（一次一段）
function rvAskNewOutside(before) {
  const fresh = [];
  for (const it of rvItems()) for (const q of it["段落外"] || []) if (!q["答案"] && !before.has(`${q["鍵"]}|${q.start}|${q.end}`)) fresh.push([it, q]);
  if (!fresh.length) return;
  const [it, q] = fresh[0];
  const dlg = document.createElement("dialog");
  dlg.className = "rv-confirm";
  dlg.setAttribute("aria-labelledby", "rv-out-title");
  dlg.innerHTML = `<h2 id="rv-out-title">切在外面的這幾秒是誰的聲音？</h2>
    <p>〈${esc(rvWho(it["說話者"]))}〉這一段：${q["整句"] ? "原本屬於這一段的一句" : "原本那一句"}有 ${esc(rvFmt(q.start, 1))}–${esc(rvFmt(q.end, 1))}（${(q.end - q.start).toFixed(1)} 秒）落在段落外面，沒有任何處理蓋到。</p>
    <p><button class="ghost small" id="rv-out-play">聽這一段</button>${cvPadHtml("rv-out-pad")}</p>
    <div class="rv-out-btns">${RV_OUT.map(([v, label]) => `<button class="ghost" data-v="${esc(v)}">${esc(label)}</button>`).join("")}
      <button class="ghost" data-v="">先不回答</button></div>
    <p class="rv-meta">先不回答的話，第 4 步總檢查會列出這幾秒；卡片上也可以之後再回答。</p>`;
  document.body.appendChild(dlg);
  const close = () => { dlg.close(); dlg.remove(); };
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
  // 10-15 前後 2 秒開關統一（1015-5）：以前寫死前後 0.3 秒；改照開關（視窗蓋住卡片，所以這裡也放一顆，跟卡片上那顆同一個狀態）
  dlg.querySelector("#rv-out-play").addEventListener("click", () => { const [a, b] = cvPadRange(q.start, q.end); rv.stopAt = b; rvSeek(a, true); });
  cvBindPad("rv-out-pad", dlg);
  dlg.querySelectorAll("[data-v]").forEach((b) => b.addEventListener("click", async () => {
    close();
    const key = rvKey(it);
    if (b.dataset.v) await rvAnswerOutside(it, q, b.dataset.v);
    if (rvItem(key)) { rv.cur = key; rvRenderCard(); rvMarkListRow(true); }
    rvAskNewOutside(new Set([...before, `${q["鍵"]}|${q.start}|${q.end}`]));   // 下一段
  }));
  dlg.showModal();
}

function rvToggleMore() {
  rv.open = !rv.open;
  const m = document.getElementById("rv-more");
  if (m) m.hidden = !rv.open;
  const b = document.getElementById("rv-change");
  if (b) b.setAttribute("aria-expanded", String(rv.open));
  if (rv.open && m) { const f = m.querySelector("input, select, textarea"); if (f) f.focus({ preventScroll: true }); }
}

// ---------------------------------------------------------------------------
// 存檔
// ---------------------------------------------------------------------------

function rvRefreshSug() {   // 改了做法：只更新建議框下面的「改成」那一行，不重畫整張卡（展開的選項保持開著）
  const it = rvItem(rv.cur);
  const box = document.querySelector(".rv-sug");
  if (!it || !box) return;
  const chosen = rvChosen(it), sug = (it["建議"] || {})["做法"];
  let mine = box.querySelector(".mine");
  if (chosen && sug && chosen !== sug) {
    if (!mine) { mine = document.createElement("p"); mine.className = "mine"; box.appendChild(mine); }
    mine.textContent = `改成：${rvHowLabel(chosen)}`;
  } else if (mine) mine.remove();
  rvRenderList();
}

async function rvPass() {
  const it = rv.cur && rvItem(rv.cur);
  if (!it || rvReadonly()) return;
  const t = it["類型"];
  const undo = !!it["已確認"];
  try {
    if (rvInCut(it) || it["涵蓋"] || t === "改成老師") return;   // 不用按：已經處理好
    if (rvPassCuts(it)) {
      // 09-29 宇軒：不重要的短句，通過＝剪掉這段（聲音和畫面都拿掉，不用生成）；10-01：先確認、記下是哪一段剪的（可以取消）
      if (!confirm(`按通過會把 ${rvFmt(it.start, 1)}–${rvFmt(it.end, 1)} 整段剪掉（聲音和畫面都拿掉，影片會變短）。\n要留下這段：按「取消」，在「改做法」選「學員重念」。`)) return;
      await apiPost("/api/review/cut", { start: it.start, end: it.end, "備註": "不重要的短句（通過＝剪掉）", "來源段落": it.id });
      await rvReload();
      return;
    }
    if (t === "學員段落") {
      const ta = document.getElementById("rv-text");
      const text = ta ? ta.value.trim() : it["校對稿"];
      await apiPost("/api/turns/save", { id: it.id, "校對稿": text, "已確認": !undo });
      it["校對稿"] = it["建議稿"] = text;
      it["換過的字"] = [];
    } else if (rvIsName(t)) {
      const body = { id: it.id, "做法": it["做法"], "已確認": !undo };
      const say = document.getElementById("rv-namesay");   // 09-30：要重念的句子跟通過一起存（不然剛改的字還沒存到就先檢查了）
      if (t === "名字" && say && !undo) {
        const v = say.value.trim();
        const same = v === (say.dataset.auto || "").trim() || v === ((it["整句"] || {})["原文"] || "").trim();
        body["改稿"] = same ? "" : v;   // 沒改（還是原文）不算人改過：原文裡可能還有本名
        if (it["整句"]) it["整句"]["改稿"] = body["改稿"];
      }
      await apiPost(t === "名字" ? "/api/review/name" : "/api/review/stuname", body);
    } else if (t === "重疊") {
      const how = rvChosen(it);
      const ar = how === "兩邊都重生成" ? "照原位置疊著" : (it["排法"] || (it["建議"] || {})["排法"] || "前後排開");
      await apiPost("/api/review/overlap", undo ? { id: it.id, "已確認": false }
        : { id: it.id, "做法": how, "排法": ar, "已確認": true });
      it["做法"] = how;
    } else if (t === "刪除段落" && it["來源"] === "建議") {
      if (undo) { alert("建議刪除的段落用「改做法」選刪除或不刪"); return; }
      await apiPost("/api/review/cutsuggest", { id: it.id, "決定": rvChosen(it) });
      await rvReload();
      return;
    } else {
      return;   // 手動加的刪除段落、局部消音本來就算確認過
    }
  } catch (err) { alert(err.message); return; }
  it["已確認"] = !undo;
  rv.lastActivity = Date.now();
  if (document.activeElement && document.activeElement.blur) document.activeElement.blur();   // 通過＝打完字了，之後播到下一筆會自動換
  rvRecount();
  rvRenderCard();
  rvRenderList();
}

// ---------------------------------------------------------------------------
// 下半部：篩選列＋清單
// ---------------------------------------------------------------------------

function rvRenderList() {
  const lower = document.getElementById("rv-lower");
  const items = rvItems();
  const count = (f) => f === "全部" ? items.length : f === "還沒確認" ? items.filter((x) => !rvDone(x)).length
    : items.filter((x) => x["類型"] === f).length;
  const rows = rvVisible().map((it) => {
    const key = rvKey(it);
    const st = key === rv.cur ? `<span class="st now">目前</span>` : rvInCut(it) ? `<span class="st ok" title="落在剪掉的片段裡">✓ 通過</span>`
      : it["涵蓋"] ? `<span class="st ok" title="已由〈${esc(it["涵蓋"]["名稱"])}〉涵蓋">✓ 已涵蓋</span>`
      : it["涵蓋消失"] ? `<span class="st warn" title="原本由〈${esc(it["涵蓋消失"]["名稱"] || "")}〉涵蓋，現在沒有了">請重新看</span>`
      : it["還缺"] ? `<span class="st warn" title="${esc(it["還缺"])}">還缺</span>`
      : it["不用處理"] ? `<span class="st skip">不用處理</span>`
      : it["已確認"] ? `<span class="st ok">✓ 通過</span>` : `<span class="st">—</span>`;
    const chosen = rvChosen(it);
    const sug = rvInCut(it) ? "已剪掉（連畫面）" : it["涵蓋"] ? `跟著〈${it["涵蓋"]["名稱"]}〉換掉` : it["類型"] === "學員段落" ? ((it["建議"] || {})["做法"] === "刪除這段" && !it["短句保留"] ? "剪掉這段（連畫面）" : "學員重念")
      : rvHowLabel(chosen) || rvSuggestText(it);
    return `<li class="${key === rv.cur ? "cur" : ""} ${rvDone(it) ? "done" : ""}" data-key="${esc(key)}" tabindex="-1">
      <span class="tm">${esc(rvFmt(it.start))}</span>
      <span class="ty">${rvChip(it)}</span>
      <span class="tx">${rvIdTag(it)}${(it["段落外"] || []).some((q) => !q["答案"]) ? `<span class="rv-tag warn">段落外面有幾秒還沒回答</span>` : ""}${it["第5步退回"] ? `<span class="rv-tag warn" title="${esc(it["第5步退回"].join("；"))}">第 5 步退回</span>` : ""}${it["人工新增"] ? `<span class="rv-tag">人工新增</span>` : ""}${it["代號改過"] ? `<span class="rv-tag warn">代號改過，請再看一次</span>` : ""}${esc(rvPreview(it))}</span>
      <span class="sg">${esc(sug)}</span>
      ${st}</li>`;
  }).join("");
  lower.innerHTML = `
    <div class="rv-row rv-passbar"><button class="ghost small" id="rv-tostart" title="開始前 4 件事">開始前 4 件事${rvWhoDone() ? "" : "（② 還沒做）"}</button>${rvPassAllHtml()}
      <span class="rv-meta">還沒確認 ${rvLeft()} 張；不用全部看完也能「開始 AI 修改」，沒看的照建議做</span></div>
    <nav class="rv-filters" aria-label="篩選">${RV_FILTERS.map(([f, label]) =>
      `<button class="${rv.filter === f ? "on" : ""}" data-f="${esc(f)}">${esc(label)} <span>${count(f)}</span></button>`).join("")}</nav>
    <ol class="rv-list" id="rv-list">${rows || `<li class="empty">這個篩選沒有項目。</li>`}</ol>
    ${(rv.data["已自動跳過的重疊"] || []).length ? `<p class="rv-foot">另外有 ${rv.data["已自動跳過的重疊"].length} 處重疊自動跳過（兩位學員之間、短附和、邊界誤差、學員段落裡沒有老師的），在「設定」裡可以救回。</p>` : ""}`;
  lower.querySelectorAll(".rv-filters button").forEach((b) => b.addEventListener("click", () => { rv.filter = b.dataset.f; rvRenderList(); }));
  rvBindPassAll(lower);
  const toStart = document.getElementById("rv-tostart");
  if (toStart) toStart.addEventListener("click", () => { rv.prepOpen = true; rvRenderMain(); });
  lower.querySelectorAll(".rv-list li[data-key]").forEach((li) => li.addEventListener("click", () => rvSelect(li.dataset.key)));
  rvMarkListRow(true);
}

function rvMarkListRow(scroll) {
  const list = document.getElementById("rv-list");
  if (!list) return;
  list.querySelectorAll("li.cur").forEach((li) => {
    li.classList.remove("cur");
    const st = li.querySelector(".st.now");
    if (st) { const it = rvItem(li.dataset.key); st.className = "st"; st.textContent = it && it["不用處理"] ? "不用處理" : it && it["已確認"] ? "✓ 通過" : "—"; if (it && it["已確認"]) st.classList.add("ok"); }
  });
  const li = rv.cur && list.querySelector(`li[data-key="${CSS.escape(rv.cur)}"]`);
  if (!li) return;
  li.classList.add("cur");
  const st = li.querySelector(".st");
  if (st) { st.className = "st now"; st.textContent = "目前"; }
  if (scroll) {   // 只捲清單自己，不捲整頁
    const top = li.offsetTop - list.offsetTop, bottom = top + li.offsetHeight;
    if (top < list.scrollTop || bottom > list.scrollTop + list.clientHeight) list.scrollTop = Math.max(0, top - list.clientHeight / 3);
  }
}

// ---------------------------------------------------------------------------
// 開始前 4 件事
// ---------------------------------------------------------------------------

const RV_PREP = [["刪除", "① 建議刪除段落"], ["學員", "② 辨識學員聲音是誰"], ["名字", "③ 辨識其他名稱如何替換"], ["保留原聲", "④ 誰保留原聲"]];   // 09-29 宇軒：刪除先做，學員是誰才看得出誰只在要刪的段落裡

function rvRenderPrepSide() {
  const p = rv.data["開始前確認"];
  const box = document.getElementById("rv-right");
  box.innerHTML = `<article class="rv-card rv-prepside">
      <header><span class="rv-chip"><i></i>開始前 4 件事</span></header>
      <p class="rv-meta">先把整體定下來，逐筆看的時候就不用再想：哪些段落整段刪掉、學員換成誰、其他人名怎麼處理、誰不用重念。</p>
      ${rvRevertedHtml()}
      <ol class="rv-prepsteps">${RV_PREP.map(([k, label]) => `<li class="${p[k] ? "ok" : ""} ${rv.prepTab === k ? "on" : ""}">
        <button class="linkish" data-tab="${esc(k)}">${esc(label)}</button><span>${p[k] ? "✓ 做完了" : k === "學員" ? "還沒做（要做）" : "還沒做（照預設，不擋）"}</span>
        ${!p[k] && rvPrepLeft(k).length ? `<span class="rv-meta">${esc(rvPrepLeft(k).join("；"))}</span>` : ""}</li>`).join("")}</ol>
      <div class="rv-actions"><button class="primary" id="rv-start" ${rvWhoDone() ? "" : "disabled"}>開始逐筆看</button>
        ${rvPassAllHtml()}
        ${rvWhoDone() ? `<span class="rv-meta">${rvPrepDone() ? "" : "①③④ 沒做的照預設（不擋）"}</span>` : `<span class="rv-meta">② 學員是誰做完才能開始；①③④ 沒做的照預設</span>`}</div>
    </article>`;
  box.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { rv.prepTab = b.dataset.tab; rvRenderPrepSide(); rvRenderPrep(); }));
  rvBindPassAll(box);
  document.getElementById("rv-start").addEventListener("click", () => {
    rv.prepOpen = false;
    if (!rv.cur || rvDone(rvItem(rv.cur) || {})) rv.cur = rvFirstPending();
    rvRenderMain();
    if (rv.cur) rvSelect(rv.cur);
  });
  rvApplyReadonly();
}

// 09-30：每一件底下還沒處理的（後端 review.prep_pending）；有的話不能標完成
function rvPrepLeft(k) { return ((rv.data["開始前待處理"] || {})[k]) || []; }

function rvRevertedHtml() {   // 讀資料時冒出新項目、自動改回還沒做的（只提醒這一次）
  const back = rv.data["開始前自動改回"] || [];
  if (!back.length) return "";
  const labels = back.map((k) => (RV_PREP.find((x) => x[0] === k) || [k, k])[1]);
  return `<p class="rv-warnline">${esc(labels.join("、"))} 底下冒出新的項目（例如 ① 改成不刪、拆開學員），自動改回「還沒做」，再看一次。</p>`;
}

function rvRenderPrep() {
  const lower = document.getElementById("rv-lower");
  const k = rv.prepTab;
  const done = rv.data["開始前確認"][k];
  let body = "";
  if (k === "學員") body = rvPrepPeopleHtml();
  else if (k === "名字") body = rvPrepNamesHtml();
  else if (k === "保留原聲") body = rvPrepVoiceHtml();
  else body = rvPrepCutHtml();
  const label = RV_PREP.find((x) => x[0] === k)[1];
  lower.innerHTML = `<section class="rv-prep">
      <h2>${esc(label)}</h2>${body}
      <div class="rv-actions"><button class="primary" id="rv-prepdone" ${!done && rvPrepLeft(k).length ? "disabled" : ""}>${done ? "✓ 這一件做完了（再按一次改回還沒做）" : "這一件做完了"}</button>
        ${!done && rvPrepLeft(k).length ? `<span class="rv-warnline" id="rv-prepwhy">還不能標完成：${esc(rvPrepLeft(k).join("；"))}</span>` : `<span class="rv-warnline" id="rv-prepwhy"></span>`}</div>
    </section>`;
  document.getElementById("rv-prepdone").addEventListener("click", async () => {
    let r;
    try { r = await apiPost("/api/review/prep", { "項目": k, "完成": !done }, { quiet: true }); }
    catch (err) { document.getElementById("rv-prepwhy").textContent = err.message; if (!/還不能標完成/.test(err.message)) showSaveError(err.message); return; }
    rv.data["開始前確認"] = r["開始前確認"];
    if (!done) { const i = RV_PREP.findIndex((x) => x[0] === k); const next = RV_PREP.slice(i + 1).concat(RV_PREP).find((x) => !rv.data["開始前確認"][x[0]]); if (next) rv.prepTab = next[0]; }
    rvRenderPrepSide(); rvRenderPrep();
  });
  rvBindPrep(lower);
  rvApplyReadonly();
}

function rvSegsOf(who) { return rvItems().filter((x) => x["類型"] === "學員段落" && x["說話者"] === who); }

function rvPrepPeopleHtml() {
  // 09-29 宇軒：改成左右兩欄。左：照聲音特徵分出來的每一位，試聽＋選本名（最後一個選項是老師）；右：每個本名在這支影片用哪個代號
  const people = Object.entries(rv.data["學員"] || {});
  if (!people.length) return `<p class="rv-meta">${rv.data["有段落"] ? "這支影片沒有學員段落。" : "還沒有段落分析結果：先跑完第 1 步影片分析。"}</p>`;
  const tp = rv.data["學員資料"] || {};
  const codes = rv.data["代號選項"] || [];
  const reals = tp["本名選項"] || [];
  const teacher = tp["老師名稱"] || "老師";
  const rosterCode = tp["名冊代號"] || {};
  const nameCode = tp["本名代號"] || {};
  // 09-29 宇軒：只在結尾道別這類要刪的段落裡講話的，收到最下面，不用判斷
  const cutOnly = people.filter(([, p]) => p["都會刪掉"]);
  const left = people.filter(([, p]) => !p["都會刪掉"]).map(([n, p]) => {
    const others = people.filter(([m]) => m !== n).map(([m]) => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
    // 09-29：這一集被叫到的名字（第 1 步人名清單，依次數）排最前面；名冊上其他人放後面；最後是老師
    const called = tp["這一集的名字"] || [];
    const calledSet = new Set(called.map((c) => c["名字"]));
    const opt = (r, label) => `<option value="${esc(r)}" ${p["本名"] === r ? "selected" : ""}>${esc(label || r)}</option>`;
    const realOpts = ['<option value="">（還沒指定）</option>']
      .concat(called.length ? [`<optgroup label="這一集被叫到的名字">${called.map((c) => opt(c["名字"], `${c["名字"]}（${c["次數"]} 次${c["名冊上有"] ? "" : "・名冊上沒有"}）`)).join("")}</optgroup>`] : [])
      .concat([`<optgroup label="${called.length ? "名冊上其他人" : "名冊"}">${reals.filter((r) => !calledSet.has(r)).map((r) => opt(r)).join("")}</optgroup>`])
      .concat([`<option value="__unknown" ${p["本名未知"] ? "selected" : ""}>不知道是誰（照樣換聲音）</option>`])   // 09-29 宇軒
      .concat([`<option value="${esc(teacher)}">${esc(teacher)}（這位其實是老師）</option>`]).join("");
    const segs = rvSegsOf(n);
    const split = rv.splitOpen === n ? `<div class="rv-splitbox">
        <p class="rv-meta">勾選其實是另一個人的段落，按「改成新的一位學員」；其實是老師在講話的，按「勾的改成老師」（全部勾＝這一位整個都是老師）。</p>
        <ul>${segs.map((sg) => `<li><label class="rv-check"><input type="checkbox" class="rv-splitck" value="${esc(sg.id)}">
          <span class="tm">${esc(rvFmt(sg.start))}</span> ${esc((sg["建議稿"] || "").slice(0, 36))}</label>
          <button class="ghost small rv-segplay" data-t="${sg.start}">試聽</button></li>`).join("")}</ul>
        <button class="primary small" id="rv-splitgo" data-person="${esc(n)}">改成新的一位學員</button>
        <button class="ghost small" id="rv-toteacher" data-person="${esc(n)}">勾的改成老師</button>
        <button class="ghost small" id="rv-splitcancel">取消</button></div>` : "";
    return `<li class="rv-person">
      <div class="nm"><i class="dot" style="background:${rvStuColor(n)}"></i><b>${esc(n)}</b><span class="rv-meta">${esc(rvFmt(p["秒數"]))}，${p["段數"]} 段</span></div>
      <div class="ctl">
        <button class="ghost small rv-sample" data-person="${esc(n)}">試聽</button>
        <label>本名 <select class="rv-real" data-person="${esc(n)}">${realOpts}</select></label>
        ${rvVoiceSelect(n, p)}
      </div>
      <details class="rv-more-ctl"><summary>其他（合併、拆開）</summary><div class="ctl">
        ${others ? `<label>跟誰是同一人 <select class="rv-mergeto" data-person="${esc(n)}"><option value="">—</option>${others}</select></label>` : ""}
        <button class="ghost small rv-splitopen" data-person="${esc(n)}" title="一位其實是兩個人、或有幾段其實是老師">拆開／有幾段其實是老師</button>
      </div></details>${split}</li>`;
  }).join("") + (cutOnly.length ? `<li class="rv-person"><details><summary class="rv-meta">只在要刪的段落裡講話（${cutOnly.length} 位：${esc(cutOnly.map(([n]) => n).join("、"))}），不用判斷</summary>
      <p class="rv-meta">例如結尾跟老師說再見。這幾段會整段刪掉；如果在「① 建議刪除段落」改成不刪，他們會回到上面。</p>
      <div class="ctl">${cutOnly.map(([n]) => `<button class="ghost small rv-sample" data-person="${esc(n)}">試聽 ${esc(n)}</button>`).join("")}</div></details></li>` : "");
  const chosen = [...new Set(people.map(([, p]) => p["本名"]).filter(Boolean))];
  const right = chosen.length ? chosen.map((r) => {
    const cur = (rv.data["這一集代號"] || {})[r] || nameCode[r] || "";
    const who = people.filter(([, p]) => p["本名"] === r).map(([n]) => n).join("、");
    const opts = rvCodeOpts(cur, "（還沒指定）") + '<option value="__new">新的代號…</option>';
    const notInRoster = !reals.includes(r);
    const auto = cur && (rv.data["代號自動配未改過"] || {})[r] === cur;   // 10-07：選了本名當場自動配的（算決定好，人一改就拿掉標示）
    return `<li class="rv-person"><div class="nm"><b>${esc(r)}</b><span class="rv-meta">${esc(who)}${notInRoster ? "・名冊上沒有，選了代號會加進名冊" : ""}</span></div>
      <div class="ctl"><label>代號 <select class="rv-namecode" data-real="${esc(r)}">${opts}</select></label>
        ${auto ? `<span class="rv-tag" title="程式自動配的，不用改就不用動">自動配</span>` : ""}
</div></li>`;
  }).join("") : `<li class="rv-meta">左邊選了本名之後，這裡會列出來。</li>`;
  return `${rvDupHtml()}<p class="rv-meta">左邊是照聲音特徵分出來的「學員 1、2⋯⋯」：試聽後選他的本名（最上面是這一集被叫到的名字）；聲音其實是老師的，選「${esc(teacher)}」；聽得出是另一個人、但不知道本名的，選「不知道是誰」（照樣換聲音，不用代號）。右邊是每個本名在這支影片換成哪個代號（只影響這一集，老師講到他的名字、學員稿子裡的名字都會照這裡換）。</p>${rvEnglishCodesHtml()}${rvAutoHtml()}
    <div class="rv-people2"><div><h4>照聲音特徵分出來的人</h4><ul class="rv-people">${left}</ul></div>
      <div><h4>本名 → 這支影片的代號</h4><ul class="rv-people">${right}</ul></div></div>`;
}

// 09-30 宇軒：學員聲線依男女自動輪流（男 1、男 2⋯／女 1、女 2⋯，女 5 不用），每位不同；這裡顯示、可以改
function rvVoiceSelect(n, p) {
  if (p["聲音"] === "保留原聲") return `<span class="rv-meta">保留原聲，不用聲線</span>`;
  const v = p["聲線"] || {};
  const opts = rv.data["聲線選項"] || {};
  const all = [...(opts["男"] || []), ...(opts["女"] || [])];
  if (!all.length) return "";
  const cur = v["名稱"] || "";
  const auto = cur ? (v["人選的"] ? `改回自動配${v["自動配原本"] ? `（${v["自動配原本"]}）` : ""}` : `自動配（${cur}）`) : "開始生成時自動配";
  const og = (g) => (opts[g] || []).length ? `<optgroup label="${g}聲">${opts[g].map((x) => `<option value="${esc(x)}" ${v["人選的"] && x === cur ? "selected" : ""}>${esc(x)}</option>`).join("")}</optgroup>` : "";
  return `<label title="${esc(v["依據"] || "")}">聲線 <select class="rv-voicepick" data-person="${esc(n)}">
    <option value="" ${v["人選的"] ? "" : "selected"}>${esc(auto)}</option>${og("男")}${og("女")}</select></label>`;
}

// 10-02 第七批：代號選單分女男、只顯示中文（後端 epcodes.code_groups）；這一集用到的其他代號放最後
// 10-07：已經給別人的標「（已給某某）」，跟名冊或人名清單上的名字一樣的標「（撞名）」
function rvCodeOpts(cur, first) {
  const g = rv.data["代號分組"] || {};
  const flat = rv.data["代號選項"] || [];
  const map = rv.data["代號對照"] || {};
  const given = map["代號給了"] || {};
  const clash = map["撞名"] || {};
  const note = (c) => c === cur ? "" : (given[c] || []).length ? `（已給${given[c].join("、")}）` : clash[c] ? "（撞名）" : "";
  const opt = (c) => `<option value="${esc(c)}" ${cur === c ? "selected" : ""}>${esc(c + note(c))}</option>`;
  const groups = g["女"] ? [["女", g["女"]], ["男", g["男"] || []], ["這一集用到的其他代號", g["這一集用到的其他代號"] || []]]
    : [["代號", flat]];
  const all = groups.flatMap(([, cs]) => cs);
  return [`<option value="">${esc(first)}</option>`]
    .concat(groups.filter(([, cs]) => cs.length).map(([lab, cs]) => `<optgroup label="${esc(lab)}">${cs.map(opt).join("")}</optgroup>`))
    .concat(cur && !all.includes(cur) ? [`<option value="${esc(cur)}" selected>${esc(cur)}</option>`] : [])
    .join("");
}

// 10-02 第七批：這一集還在用英文代號 → 「把這一集的英文代號換成中文」（後端 codeswap）
function rvEnglishCodesHtml() {
  const rows = rv.data["英文代號換中文"] || [];
  if (!rows.length) return "";
  const g = rv.data["代號分組"] || {};
  const pick = (r) => `<select class="rv-cvt" data-old="${esc(r["舊"])}"><option value="">（選一個）</option>
    <optgroup label="女">${(g["女"] || []).map((c) => `<option ${c === r["建議"] ? "selected" : ""}>${esc(c)}</option>`).join("")}</optgroup>
    <optgroup label="男">${(g["男"] || []).map((c) => `<option ${c === r["建議"] ? "selected" : ""}>${esc(c)}</option>`).join("")}</optgroup></select>`;
  return `<div class="rv-person" id="rv-cvtbox"><p class="rv-warnline">這一集還在用英文代號（聲音模型念英文名常念得怪）。可以一次換成中文寫法：存代號的地方和要念的文字一起換，換之前自動備份；換過的句子到第 4 步會重新生成。</p>
    <ul>${rows.map((r) => `<li><b>${esc(r["舊"])}</b> → ${pick(r)} <span class="rv-meta">${r["存代號的地方"]} 格、要念的文字 ${r["要念的文字"]} 處${r["建議"] ? "" : "・舊名單沒有對應，要自己選"}</span></li>`).join("")}</ul>
    <button class="primary small" id="rv-cvtgo">把這一集的英文代號換成中文</button></div>`;
}

function rvAutoHtml() {
  // 09-29：名冊拿掉代號欄後，每一集要自己選代號；還沒選的一鍵配（10-07：依性別、不撞名，之後可以改）
  const lack = rv.data["還沒代號"] || [];
  return `<div class="rv-actions">${lack.length ? `<span class="rv-warnline">還有 ${lack.length} 個名字這一集沒有代號</span>` : ""}
    <button class="ghost small" id="rv-autocode">幫還沒代號的自動配</button></div>`;
}

// 10-07：手動選到跟名字撞的代號（名冊上有人、或這一集被叫到的人叫這個名字）→ 先問一次
function rvCodeClashOk(code) {
  const who = ((rv.data["代號對照"] || {})["撞名"] || {})[code];
  if (!who || !who.length) return true;
  return confirm(`「${code}」跟名字撞名（${who.join("、")} 也叫這個）。成品裡會分不出是代號還是真的在叫這個人。\n還是要用嗎？`);
}

function rvDupHtml() {
  // 09-29：這一集的代號表（右欄＞③ 換成代號＞名冊預設）裡，兩個人用同一個代號
  const dup = Object.entries(rv.data["代號重複"] || {});
  if (!dup.length) return "";
  return `<p class="rv-warnline">同一集有人用了同一個代號，成品裡會分不出是誰：${dup.map(([c, rs]) => `${esc(c)}（${esc(rs.join("、"))}）`).join("；")}。如果其實是同一個人（轉錯字、暱稱），不用改，或在「③」選「是上面的學員」合併；不同人的話，學員在「② 辨識學員聲音是誰」右欄改，其他人在「③ 辨識其他名稱如何替換」改。</p>`;
}

// 10-03 第八批（#12）：名冊裡同一個寫法出現在兩列 → 同一處名字會比中兩個人；讓人決定是不是同一人（改名冊）
function rvRosterDupHtml() {
  const dups = rv.data["名冊重複寫法"] || [];
  if (!dups.length) return "";
  return `<p class="rv-warnline" id="rvRosterDup">名冊裡同一個寫法出現在兩列：${esc(dups.map((d) => `「${d["寫法"]}」第 ${d["列"].join("、")} 列`).join("；"))}（標題列算第 1 列）。
    同一處名字會同時比中這幾列的人，卡片合成一張、寫「也可能是」。是同一人的話到名冊把重複的那一列刪掉或合併；不是同一人，卡片上自己選是哪一位。</p>`;
}

function rvPrepNamesHtml() {
  // 09-29 宇軒：③「辨識其他名稱如何替換」——這一集被提到的每個人名都列（名冊上有沒有都一樣）。
  // ② 已經選成本名的只顯示結果；每次出現都在確認刪除段落裡的收起來
  const codes = rv.data["代號選項"] || [];
  const all = rv.data["提到的名字"] || [];
  const fromTwo = all.filter((u) => u["② 已決定"] && !u["都在刪除段落"]);
  const un = all.filter((u) => !u["② 已決定"] && !u["都在刪除段落"]);
  const cutOnly = all.filter((u) => u["都在刪除段落"]);
  const hows = ["換成代號", "是上面的學員", "不是名字", "不用處理"];
  const tp = rv.data["學員資料"] || {};
  const reals = [...new Set(Object.values(rv.data["學員"] || {}).map((p) => p["本名"]).filter(Boolean))];
  const codeOf = (r) => (rv.data["這一集代號"] || {})[r] || "";
  const cntOf = (u) => u["刪除段落外次數"] !== u["次數"] ? `${u["刪除段落外次數"]} 次（另 ${u["次數"] - u["刪除段落外次數"]} 次在刪除段落裡）` : `${u["次數"]} 次`;
  const who = (u) => `${esc(u["是誰"])}${u["名冊本名"] && u["名冊本名"] !== u["名字"] ? `・名冊上是 ${esc(u["名冊本名"])}` : u["名冊本名"] ? "・名冊上有" : "・名冊上沒有"}`;
  const unRows = un.map((u) => {
    const codeOpts = rvCodeOpts(u["代號"] || "", "選代號") + '<option value="__new">新的代號…</option>';
    const sameOpts = ['<option value="">選是哪一位</option>'].concat(reals.map((r) =>
      `<option value="${esc(r)}" ${u["同一人"] === r ? "selected" : ""}>${esc(r)}（${esc(codeOf(r) || "還沒選代號")}）</option>`)).join("");
    const result = u["做法"] === "換成代號" && u["代號"] ? `→ <b>${esc(u["代號"])}</b>` : u["做法"] === "是上面的學員" && u["同一人"] ? `→ 同 ${esc(u["同一人"])}（<b>${esc(u["代號"] || "還沒選代號")}</b>）` : "";
    const confirmBtn = !u["已決定"] && u["做法"] && (u["做法"] !== "換成代號" || u["代號"])
      ? `<button class="ghost small rv-unok" data-name="${esc(u["名字"])}" data-how="${esc(u["做法"])}" data-code="${esc(u["代號"] || "")}">照建議確認</button>` : "";
    return `<li class="rv-person"><div class="nm"><b>${esc(u["名字"])}</b> ${result}<span class="rv-meta">${who(u)}・${cntOf(u)}（老師 ${u["老師說"]}、學員 ${u["學員說"]}）${u["其他寫法"].length ? "・也寫成 " + esc(u["其他寫法"].join("、")) : ""}</span></div>
      <div class="ctl"><button class="ghost small rv-segplay" data-t="${u["第一次"]}">試聽第一次出現</button>
        ${hows.map((h) => `<label class="rv-check"><input type="radio" name="un-${esc(u.id)}" class="rv-unhow" data-name="${esc(u["名字"])}" value="${h}" ${u["做法"] === h ? "checked" : ""}> ${h}</label>`).join("")}
        <select class="rv-uncode" data-name="${esc(u["名字"])}" ${u["做法"] === "換成代號" ? "" : "hidden"}>${codeOpts}</select>
        <select class="rv-unsame" data-name="${esc(u["名字"])}" ${u["做法"] === "是上面的學員" ? "" : "hidden"}>${sameOpts}</select>
        ${u["已決定"] ? (u["自動配"] ? '<span class="rv-tag" title="程式自動配的，不用改就不用動">自動配</span>' : "") : u["做法"] ? `<span class="rv-meta">（建議，還沒確認）</span>${confirmBtn}` : '<span class="rv-warnline">還沒決定</span>'}</div>
      ${u["說明"] ? `<p class="rv-meta">${esc(u["說明"])}</p>` : ""}</li>`;
  }).join("");
  const twoBlock = fromTwo.length ? `<li class="rv-person"><details><summary class="rv-meta">② 已經定好的學員（${fromTwo.length} 個）：${fromTwo.map((u) => `${esc(u["名字"])} → ${esc(u["代號"] || "還沒選代號")}`).join("、")}</summary>
      <p class="rv-meta">被提到時一樣換成這個代號；要改代號回「② 辨識學員聲音是誰」右欄。</p></details></li>` : "";
  const cutBlock = cutOnly.length ? `<li class="rv-person"><details><summary class="rv-meta">只出現在確認刪除的段落裡（${cutOnly.length} 個：${esc(cutOnly.map((u) => u["名字"]).join("、"))}），不用處理</summary>
      <p class="rv-meta">這些段落會整段刪掉。如果在「① 建議刪除段落」改成不刪，會回到上面。</p></details></li>` : "";
  // 10-02 第七批（A3）：Claude 那一步沒跑成功，名冊上沒有的名字這裡看不到
  const peopleWarn = rv.data["人名清單沒跑成功"] ? `<p class="rv-warnline" id="rvPeopleMissing">人名清單沒跑成功（第 1 步用 Claude 找這一集提到的人名）：名冊上沒有的名字這裡看不到，成品可能照原聲念出來。到第 1 步按「重新分析（做完的會跳過）」，或命令列 bookclub run people &lt;工作區&gt;；跑成功之前 ③ 不能標完成。</p>` : "";
  if (!all.length) return peopleWarn || `<p class="rv-meta">這一集沒有找到被提到的人名（第 1 步人名清單還沒跑，或真的沒有）。</p>`;
  return `${peopleWarn}${rvRosterDupHtml()}${rvDupHtml()}${rvAutoHtml()}<p class="rv-meta">這一集被提到的所有人名（老師或學員講到的），每個決定被提到時換成什麼：
    名冊上的人預設「換成代號」，代號每一集自己選（或按上面自動配）；其實是 ② 某位學員（轉錯字、暱稱）選「是上面的學員」；家人、朋友、沒登記的人選「換成代號」；書中人物、公眾人物選「不用處理」；抓錯的選「不是名字」。</p>
    <p class="rv-meta">${un.length} 個要看，${un.filter((u) => !u["已決定"]).length} 個還沒確認。</p>
    <ul class="rv-people">${unRows}${twoBlock}${cutBlock}</ul>`;
}

function rvPrepVoiceHtml() {
  // 09-29 宇軒：逐一列 ② 辨識好的學員（本名 → 代號）＋處理方式；只在刪除段落裡講話的不列
  const people = Object.entries(rv.data["學員"] || {}).filter(([, p]) => !p["都會刪掉"]);
  if (!people.length) return `<p class="rv-meta">這支影片沒有要處理的學員。</p>`;
  const rows = people.map(([n, p]) => {
    const name = p["本名"] ? `${esc(p["本名"])} → <b>${esc(p["代號"] || "還沒選代號")}</b>` : p["本名未知"] ? "不知道是誰" : '<span class="rv-warnline">② 還沒選本名</span>';
    const what = p["聲音"] === "保留原聲" ? "保留原聲：原本的聲音不動，講到的名字照 ③ 換" : `重新生成：用匿名聲音重念${p["代號"] ? `，稱呼換成 ${esc(p["代號"])}` : ""}`;
    return `<li class="rv-person"><div class="nm"><i class="dot" style="background:${rvStuColor(n)}"></i><b>${esc(n)}</b>　${name}<span class="rv-meta">${esc(rvFmt(p["秒數"]))}，${p["段數"]} 段</span></div>
      <div class="ctl"><button class="ghost small rv-sample" data-person="${esc(n)}">試聽</button>
      ${rvRadios(`voice-${n}`, rv.data["選項"]["聲音"], p["聲音"], "rv-voice").replaceAll('class="rv-voice"', `class="rv-voice" data-person="${esc(n)}"`)}
      <span class="rv-meta">${what}</span></div></li>`;
  }).join("");
  return `<p class="rv-warnline">只有同意的學員才保留原聲。保留原聲的學員，段落不用校對逐字稿（不重念），清單會自動標「不用處理」。</p>
    <ul class="rv-people">${rows}</ul>`;
}

function rvPrepCutHtml() {
  const list = rv.data["刪除建議"] || [];
  if (!list.length) return `<p class="rv-meta">沒有建議（影片分析還沒找建議刪除的段落，或這支影片沒有）。要刪的段落之後可以用 I／O 標起訖再新增。</p>`;
  return `<p class="rv-meta">影片分析找到可能要整段刪掉的地方（開頭空白、結尾道別、念聊天區留言、小組討論前後、技術問題）。聽一下，確認刪除或不刪。</p>
    <ul class="rv-cuts">${list.map((s) => `<li>
      <span class="tm">${esc(rvFmt(s.start))}–${esc(rvFmt(s.end))}</span>
      <span class="ty"><b>${esc(s["類型"] || "")}</b> ${esc(s["原因"] || "")}</span>
      <span class="ctl"><button class="ghost small rv-segplay" data-t="${s.start}">試聽</button>
        ${rvRadios(`cut-${s.id}`, ["刪除", "不刪"], s["決定"], "rv-cutpick").replaceAll('class="rv-cutpick"', `class="rv-cutpick" data-id="${esc(s.id)}"`)}</span></li>`).join("")}</ul>`;
}

function rvBindPrep(root) {
  const play = (t) => { rvSeek(Number(t), true); };
  root.querySelectorAll(".rv-segplay").forEach((b) => b.addEventListener("click", () => play(b.dataset.t)));
  root.querySelectorAll(".rv-sample").forEach((b) => b.addEventListener("click", () => {
    let segs = rvSegsOf(b.dataset.person);
    if (!segs.length) segs = (rv.data["色帶"] || []).filter((x) => x["說話者"] === b.dataset.person);   // 落在刪除段落裡、不在清單上的
    const seg = segs.find((x) => x.end - x.start >= 3) || segs[0];
    if (seg) play(seg.start);
  }));
  const reload = async () => { await rvReload(); };
  root.querySelectorAll(".rv-real").forEach((el) => el.addEventListener("change", async () => {
    const tname = (rv.data["學員資料"] || {})["老師名稱"] || "老師";
    if (el.value === tname && !confirm(`${el.dataset.person} 其實是${tname}？\n這一位的段落會全部改成老師（不重念），並補找裡面老師提到的名字。\n只有幾段是老師的話，改用下面「其他」→「拆開／有幾段其實是老師」。`)) { el.value = ""; return; }
    const res = await apiPost("/api/turns/realname", { "學員": el.dataset.person, "本名": el.value || null });
    if (res["補找到的老師名字"]) alert(`補找到 ${res["補找到的老師名字"]} 個老師提到的名字，已經加進清單。`);
    await reload();
  }));
  const askCode = (el) => {   // 「新的代號…」：自己打一個代號
    let v = el.value;
    if (v === "__new") {
      v = (prompt("輸入新的代號（外國人名的中文寫法，例如 葛蕾絲）") || "").trim();
      if (!v) { el.value = ""; return undefined; }
    }
    if (!v) return null;
    if (!rvCodeClashOk(v)) { el.value = ""; return undefined; }
    return v;
  };
  root.querySelectorAll(".rv-namecode").forEach((el) => el.addEventListener("change", async () => {
    const code = askCode(el);
    if (code === undefined) return;
    const res = await apiPost("/api/turns/namecode", { "本名": el.dataset.real, "代號": code });
    if (res["補找到的老師名字"]) alert(`加進名冊了，補找到 ${res["補找到的老師名字"]} 處老師提到這個名字，已經加進清單。`);
    await reload();
  }));
  root.querySelectorAll(".rv-unhow").forEach((el) => el.addEventListener("change", async () => {
    const sel = root.querySelector(`.rv-uncode[data-name="${CSS.escape(el.dataset.name)}"]`);
    const same = root.querySelector(`.rv-unsame[data-name="${CSS.escape(el.dataset.name)}"]`);
    if (sel) sel.hidden = el.value !== "換成代號";
    if (same) same.hidden = el.value !== "是上面的學員";
    if (el.value === "換成代號" || el.value === "是上面的學員") return;   // 選了代號／哪一位才存
    await apiPost("/api/people/decide", { "名字": el.dataset.name, "做法": el.value }); await reload();
  }));
  const cvt = root.querySelector("#rv-cvtgo");
  if (cvt) cvt.addEventListener("click", async () => {   // 10-02 第七批：把這一集的英文代號換成中文
    const map = {};
    root.querySelectorAll(".rv-cvt").forEach((el) => { map[el.dataset.old] = el.value; });
    const lack = Object.entries(map).filter(([, v]) => !v).map(([k]) => k);
    if (lack.length) { alert(`還沒選：${lack.join("、")}`); return; }
    const used = Object.values(map);
    if (new Set(used).size !== used.length) { alert("同一集不能兩個人換成同一個代號"); return; }
    if (!confirm(`要換成：${Object.entries(map).map(([o, n]) => `${o} → ${n}`).join("、")}？\n換之前會自動備份；換過的句子到第 4 步會重新生成。`)) return;
    try {
      const r = await apiPost("/api/codes/convert", { "對照": map });
      alert(`換好了：${Object.entries(r["每個代號換了幾處"]).map(([o, n]) => `${o} ${n} 處`).join("、")}。備份在工作區的 ${r["備份"]}`
        + (r["還剩"] && r["還剩"].length ? `\n還有沒換到的：${r["還剩"].join("、")}` : ""));
    } catch (e) { if (!e.shown) alert(e.message || e); }
    await reload();
  });
  const auto = root.querySelector("#rv-autocode");
  if (auto) auto.addEventListener("click", async () => {
    try {
      const r = await apiPost("/api/codes/auto", {});
      alert(`配好了：② ${r["②"]} 位、③ ${r["③"]} 個。${r["代號不夠"] ? "名單上的代號不夠用，剩下的請自己打新代號。" : "不喜歡的直接改。"}`);
    } catch (e) { alert(e.message || e); }
    await reload();
  });
  root.querySelectorAll(".rv-unok").forEach((b) => b.addEventListener("click", async () => {
    try {
      await apiPost("/api/people/decide", { "名字": b.dataset.name, "做法": b.dataset.how, "代號": b.dataset.code || null });
    } catch (e) { alert(e.message || e); }
    await reload();
  }));
  root.querySelectorAll(".rv-unsame").forEach((el) => el.addEventListener("change", async () => {
    if (!el.value) return;
    try {
      const res = await apiPost("/api/people/decide", { "名字": el.dataset.name, "做法": "是上面的學員", "同一人": el.value });
      if (res["補找到的老師名字"]) alert(`補找到 ${res["補找到的老師名字"]} 處老師提到這個名字，已經加進清單。`);
    } catch (e) { alert(e.message || e); }
    await reload();
  }));
  root.querySelectorAll(".rv-uncode").forEach((el) => el.addEventListener("change", async () => {
    const code = askCode(el);
    if (!code) return;
    const res = await apiPost("/api/people/decide", { "名字": el.dataset.name, "做法": "換成代號", "代號": code });
    if (res["補找到的老師名字"]) alert(`加進名冊了，補找到 ${res["補找到的老師名字"]} 處老師提到這個名字，已經加進清單。`);
    await reload();
  }));
  root.querySelectorAll(".rv-code").forEach((el) => el.addEventListener("change", async () => {
    await apiPost("/api/turns/person", { "學員": el.dataset.person, "代號": el.value || null }); await reload();
  }));
  root.querySelectorAll(".rv-sug").forEach((b) => b.addEventListener("click", async () => {
    await apiPost("/api/turns/person", { "學員": b.dataset.person, "代號": b.dataset.code }); await reload();
  }));
  root.querySelectorAll(".rv-mergeto").forEach((el) => el.addEventListener("change", async () => {
    if (!el.value) return;
    if (!confirm(`把 ${el.dataset.person} 的段落全部併進 ${el.value}？`)) { el.value = ""; return; }
    await apiPost("/api/turns/merge_person", { "從": el.dataset.person, "併進": el.value }); await reload();
  }));
  root.querySelectorAll(".rv-splitopen").forEach((b) => b.addEventListener("click", () => { rv.splitOpen = b.dataset.person; rvRenderPrep(); }));
  const cancel = root.querySelector("#rv-splitcancel");
  if (cancel) cancel.addEventListener("click", () => { rv.splitOpen = null; rvRenderPrep(); });
  const go = root.querySelector("#rv-splitgo");
  if (go) go.addEventListener("click", async () => {
    const ids = [...root.querySelectorAll(".rv-splitck:checked")].map((x) => x.value);
    if (!ids.length) { alert("先勾要改成新學員的段落"); return; }
    await apiPost("/api/turns/reassign", { ids, "說話者": "新學員" });
    rv.splitOpen = null;
    await reload();
  });
  const toT = root.querySelector("#rv-toteacher");
  if (toT) toT.addEventListener("click", async () => {
    // 09-29 宇軒：聲音辨識把老師判成學員（例如「學員2」有一大段其實是老師在講話）
    const ids = [...root.querySelectorAll(".rv-splitck:checked")].map((x) => x.value);
    if (!ids.length) { alert("先勾其實是老師在講話的段落"); return; }
    if (!confirm(`把勾的 ${ids.length} 段改成老師？\n改了之後這幾段不會重念，也會補找裡面老師提到的名字。`)) return;
    const res = await apiPost("/api/turns/reassign", { ids, "說話者": "老師" });
    if (res["補找到的老師名字"]) alert(`補找到 ${res["補找到的老師名字"]} 個老師提到的名字，已經加進清單。`);
    rv.splitOpen = null;
    await reload();
  });
  root.querySelectorAll(".rv-voicepick").forEach((el) => el.addEventListener("change", async () => {
    await apiPost("/api/students/voice", { "學員": el.dataset.person, "聲線": el.value || null }); await reload();
  }));
  root.querySelectorAll(".rv-voice").forEach((el) => el.addEventListener("change", async () => {
    await apiPost("/api/review/voice", { "學員": el.dataset.person, "聲音": el.value }); await reload();
  }));
  root.querySelectorAll(".rv-cutpick").forEach((el) => el.addEventListener("change", async () => {
    await apiPost("/api/review/cutsuggest", { id: el.dataset.id, "決定": el.value }); await reload();
  }));
}

// ---------------------------------------------------------------------------
// 「＋新增修改」面板（09-29 宇軒：取代不直覺的 I／O 標起訖）
// ---------------------------------------------------------------------------

function rvOpenEditor(it) {
  const ed = rv.ed;
  ed.open = true;
  ed.result = null;
  if (it) { ed.kind = RV_ITEM_KIND[it["類型"]]; ed.id = it.id; ed.a = it.start; ed.b = it.end; ed.label = `${(RV_TYPE[it["類型"]] || {}).label || it["類型"]} ${rvFmt(it.start)}`; ed.whole = !!it["老師整段"]; }
  else { ed.id = null; ed.label = null; ed.kind = null; ed.whole = false; }   // 10-01：新增時預設不選類型（要先選才能新增）
  rvRenderIO();
  rvRenderTimeline();
  const box = document.getElementById("rv-io");
  if (box) box.scrollIntoView({ block: "nearest" });
}

function rvCloseEditor() {
  Object.assign(rv.ed, { open: false, id: null, a: null, b: null, result: null, label: null });
  rvRenderIO();
  rvRenderTimeline();
}

// ---------------------------------------------------------------------------
// 起訖編輯器（10-01 2-4：第 3 步「新增修改／改時間」與第 5 步「退回重做時改範圍」共用這一套）
// ctx = { host: 放面板的元素 id, st: 狀態 {kind, id, a, b, busy, result, label…}, o: 選項 }，o 裡：
//   head(st) 第一行、extra(st) 額外欄位、bindHead(el, st, ctx)、hint(st) 對齊規則說明、
//   now() 「用目前播放位置」的原片秒數（沒給就不顯示這顆）、nowLabel、play(a, b) 試聽、
//   body(st) 要送的內容（回傳 null＝不能送，自己在 st.result 寫原因）、api（預設 /api/review/manual）、
//   toResult(r, st) 後端回傳 → 結果列、saved(r, res) 存好之後、close() 收起來（沒給就沒有這顆）、
//   saveLabel(st)、onChange() 起訖改了（例如重畫時間軸）
// ---------------------------------------------------------------------------

// 第 3 步的「新增修改／改時間」面板（用上面那一套）
const rvEdCtx = {
  host: "rv-io", st: rv.ed, o: {
    aria: "新增修改",
    head: (ed) => ed.id ? `<b>改時間：${esc(ed.label || "")}</b>`
      : `<label>新增操作： <select data-te-kind><option value="" ${ed.kind ? "" : "selected"}>（先選要新增什麼）</option>${RV_KINDS.map(([k, l]) => `<option value="${esc(k)}" ${k === ed.kind ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`,
    bindHead: (el, ed) => {
      const kind = el.querySelector("[data-te-kind]");
      if (kind) kind.addEventListener("change", () => { ed.kind = kind.value || null; ed.result = null; rvRenderIO(); });
      const who = el.querySelector("#rv-ed-who"); if (who) who.addEventListener("change", () => { ed.who = who.value; });
      const code = el.querySelector("#rv-ed-code"); if (code) code.addEventListener("change", () => { if (!rvCodeClashOk(code.value)) code.value = ""; ed.code = code.value; });
      const word = el.querySelector("#rv-ed-word"); if (word) word.addEventListener("change", () => { ed.word = word.value; });
      el.querySelectorAll(".rv-ed-way").forEach((r) => r.addEventListener("change", () => { ed.way = r.value; }));
    },
    extra: (ed) => {
      if (!ed.id && ed.kind === "學員發言") {
        // 09-30 宇軒：聲音來源也可以選老師（學員講完老師接一句「謝謝〔名字〕」這種）→ 用老師的 AI 聲音重念
        return `<label>是誰說的 <select id="rv-ed-who">${[...rvStudents(), "新學員", "老師"].map((n) => `<option value="${esc(n)}" ${n === ed.who ? "selected" : ""}>${esc(n === "新學員" ? "新的一位學員" : n === "老師" ? "老師（這一段用老師的 AI 聲音重念）" : rvWho(n))}</option>`).join("")}</select></label>`;
      }
      if (ed.kind === "名字" && !ed.id) {
        const codes = rv.data["代號選項"] || [];
        return `<label>換成代號 <select id="rv-ed-code">${rvCodeOpts(ed.code || "", "（選一個）")}</select></label>
          <label>逐字稿裡寫成 <input id="rv-ed-word" size="6" placeholder="不填就用對齊到的字" value="${esc(ed.word || "")}"></label>`;
      }
      if (ed.kind === "局部消音" && !ed.id) return rvRadios("rv-ed-way", rv.data["選項"]["消音"], ed.way || rv.data["選項"]["消音"][0], "rv-ed-way");
      return "";
    },
    hint: (ed) => rvRuleHint(ed.kind, ed.whole, !!ed.id),
    now: () => (rv.video ? rv.video.currentTime : null),
    play: (a, b) => { if (!rv.video) return; rv.stopAt = b; rvSeek(a, true); },
    body: (ed) => {
      if (!ed.kind) { ed.result = { error: "先在「新增操作：」選要新增什麼" }; return null; }
      const body = { "類型": ed.kind, start: ed.a, end: ed.b };
      if (ed.id) body.id = ed.id;
      else if (ed.kind === "學員發言") body["說話者"] = ed.who || rvStudents()[0] || "新學員";
      else if (ed.kind === "名字") { body["代號"] = ed.code || ""; if (ed.word) body["名字"] = ed.word; }
      else if (ed.kind === "局部消音") body["方式"] = ed.way || rv.data["選項"]["消音"][0];
      return body;
    },
    saved: async (r, res) => {
      // 新增完清空起點終點，可以接著標下一筆；結果留在面板上
      const before = rvOutKeys();   // 10-01 第三批 14：改時間把段落切短 → 切在外面的幾秒當下就問
      Object.assign(rv.ed, { id: null, label: null, a: null, b: null, result: res, word: "", code: "" });   // 代號每筆重選，免得沿用上一筆
      await rvReload();
      rvAskNewOutside(before);
      const key = `${r["類型"]}:${r.id}`;
      if (rvItem(key)) { rv.filter = "全部"; rvRenderList(); rvSelect(key, { seek: false }); }
    },
    close: () => rvCloseEditor(),
    saveLabel: (ed) => (ed.id ? "儲存修改" : "新增"),
    onChange: () => rvRenderTimeline(),
  },
};

function rvRenderIO() {
  const el = document.getElementById("rv-io");
  if (!el) return;
  if (!rv.ed.open) {
    el.innerHTML = `<button class="ghost" id="rv-ed-open">＋新增修改</button>
      <span class="rv-meta">剪掉（連畫面）、消音（只拿掉聲音）、漏抓的發言（學員或老師）／名字／重疊</span>`;
    document.getElementById("rv-ed-open").addEventListener("click", () => rvOpenEditor(null));
    return;
  }
  teRender(rvEdCtx);
  rvApplyReadonly();
}

function rvEdSet(which, t) { teSet(rvEdCtx, which, t); }

// ---------------------------------------------------------------------------
// 設定（平常收起來）：已自動跳過的重疊、學員聲音一鍵切換
// ---------------------------------------------------------------------------

function rvRenderSettings() {
  const el = document.getElementById("rv-settings");
  const list = rv.data["已自動跳過的重疊"] || [];
  // 10-01 第三批：還原的剪掉、消音收在這裡（不在清單、不算筆數），可以救回；人工新增的也可以從這裡刪
  const back = rv.data["已還原"] || [];
  const gone = rv.data["已刪除"] || [];
  const backName = (x) => `${x["類型"] === "刪除段落" ? "剪掉" : "消音"}${x["來源"] === "建議" ? `（影片分析建議的${x["建議類型"] ? "：" + x["建議類型"] : ""}）` : "（人工新增）"}`;
  const dg = (x) => (x.end - x.start < 10 ? 1 : 0);
  el.innerHTML = `
    <section><h3>已還原的（${back.length} 筆）</h3>
      <p class="rv-meta">還原的剪掉、消音不會處理，也不算在清單的筆數裡。要再處理按「救回」，回到清單。</p>
      ${back.length ? `<ul class="rv-skips">${back.map((x, i) => `<li><span class="tm">${esc(rvFmt(x.start, dg(x)))}–${esc(rvFmt(x.end, dg(x)))}</span>
        ${esc(backName(x))}　${(x.end - x.start).toFixed(1)} 秒
        <button class="ghost small rv-segplay" data-t="${x.start}">試聽</button> <button class="ghost small rv-unrestore" data-i="${i}">救回</button>
        ${x["可以刪"] ? `<button class="ghost small rv-backdel" data-i="${i}">刪掉</button>` : ""}</li>`).join("")}</ul>` : `<p class="rv-meta">沒有。</p>`}</section>
    <section><h3>刪掉的紀錄（${gone.length} 筆）</h3>
      <p class="rv-meta">人工新增、後來按「刪掉」的。只是紀錄，不會處理。</p>
      ${gone.length ? `<ul class="rv-skips">${gone.map((x) => `<li>${esc(x["名稱"] || x["類型"])}（${esc(rvFmt(x.start, 1))}–${esc(rvFmt(x.end, 1))}）
        <span class="rv-meta">${esc(fmtStamp(x["刪除時間"]))} 刪的${x["誰"] ? `，${esc(x["誰"])}` : ""}</span></li>`).join("")}</ul>` : `<p class="rv-meta">沒有。</p>`}</section>
    <section><h3>學員聲音一鍵全部切換</h3>
      <button class="ghost small rv-voice-all" data-v="重新生成">全部重新生成</button>
      <button class="ghost small rv-voice-all" data-v="保留原聲">全部保留原聲</button>
      <span class="rv-meta">保留原聲只給已經同意的學員</span></section>
    <section><h3>已自動跳過的重疊（${list.length} 處）</h3>
      <p class="rv-meta">兩位學員之間的重疊、老師講話時學員的短附和、不到 0.05 秒的邊界誤差、落在會整段重念的學員段落裡而且兩邊都沒有老師（跟著那一段整段重念），自動不處理。覺得要處理的（例如聽到其實是老師插話）按「救回」。</p>
      <ul class="rv-skips">${list.map((o) => `<li><span class="tm">${esc(rvFmt(o.start, 1))}</span> ${o.length.toFixed(2)} 秒　${esc(o["原因"] || "")}
        <button class="ghost small rv-segplay" data-t="${o.start - 2}">試聽</button> <button class="ghost small rv-rescue" data-id="${esc(o.id)}">救回</button></li>`).join("")}</ul></section>`;
  el.querySelectorAll(".rv-segplay").forEach((b) => b.addEventListener("click", () => rvSeek(Number(b.dataset.t), true)));
  el.querySelectorAll(".rv-unrestore").forEach((b) => b.addEventListener("click", async () => {
    const x = back[Number(b.dataset.i)];
    if (x["來源"] === "建議") await apiPost("/api/review/cutsuggest", { id: x.id, "決定": "刪除" });
    else await apiPost(x["類型"] === "刪除段落" ? "/api/review/cut" : "/api/review/mute", { id: x.id, "狀態": x["類型"] === "刪除段落" ? "刪除" : "消音" });
    await rvReload();
  }));
  el.querySelectorAll(".rv-backdel").forEach((b) => b.addEventListener("click", () => rvDeleteManual(back[Number(b.dataset.i)])));
  el.querySelectorAll(".rv-rescue").forEach((b) => b.addEventListener("click", async () => {
    await apiPost("/api/review/overlap", { id: b.dataset.id, "救回": true }); await rvReload();
  }));
  el.querySelectorAll(".rv-voice-all").forEach((b) => b.addEventListener("click", async () => {
    // 09-30：一鍵生效太危險（保留原聲＝學員原本的聲音留在成品），先確認；取消不變
    const n = Object.values(rv.data["學員"] || {}).filter((p) => !p["都會刪掉"] && p["聲音"] !== b.dataset.v).length;
    if (!n) { alert(`每位學員都已經是「${b.dataset.v}」了。`); return; }
    const msg = b.dataset.v === "保留原聲"
      ? `會讓 ${n} 位學員保留原聲、不換聲音。\n只有同意保留原聲的學員才可以這樣做。確定嗎？`
      : `會讓 ${n} 位學員改成重新生成（用匿名聲音重念）。確定嗎？`;
    if (!confirm(msg)) return;
    await apiPost("/api/review/voice", { "學員": "全部", "聲音": b.dataset.v }); await rvReload();
  }));
  rvApplyReadonly();
}

// ---------------------------------------------------------------------------
// 匯出、計時
// ---------------------------------------------------------------------------

async function rvExport() {
  const msg = document.getElementById("rv-export-msg");
  msg.textContent = "匯出中…";
  try {
    const r = await apiPost("/api/review/export", {});
    const warn = (r["未確認數"] ? `還有 ${r["未確認數"]} 筆沒通過。` : "")
      + ((r["還有本名的地方"] || []).length ? `${r["還有本名的地方"].length} 處還有本名：${esc(r["還有本名的地方"].slice(0, 5).join("、"))}。` : "")
      + ((r["缺參考音"] || []).length ? "還沒選定老師參考音（第 2 步）。" : "");
    msg.innerHTML = `${warn ? `<span class="rv-warnline">${warn}</span> ` : ""}已匯出：<code>${esc(r["檔案"])}</code>（<a href="/api/review/export.zip">下載</a>）。只含老師提到名字的部分，匯入後不能跑第 4、5 步；要交接整個專案請複製整個工作區資料夾。`;
  } catch (err) { msg.innerHTML = `<span class="rv-warnline">匯出失敗：${esc(err.message)}</span>`; }
}

function rvStartTimeTracking() {
  if (rv.timeTimer) clearInterval(rv.timeTimer);
  rv.lastActivity = Date.now();
  rv.timeTimer = setInterval(() => {
    if (currentRouteId() !== "step3") { clearInterval(rv.timeTimer); rv.timeTimer = null; return; }
    const playing = rv.video && !rv.video.paused;
    if (document.hidden || (!playing && Date.now() - rv.lastActivity > 60000)) return;
    apiPost("/api/review/time", { "秒數": 30 }, { quiet: true }).then((r) => { rv.data["進度"]["已花秒數"] = r["覆核秒數"]; rvRecount(); }).catch(() => {});
    if (rvReadonly()) apiGet("/api/execute").then((d) => { if (!d.running) rvReload(); }).catch(() => {});   // 執行完就解鎖
  }, 30000);
}

// ---------------------------------------------------------------------------
// 快捷鍵
// ---------------------------------------------------------------------------

document.addEventListener("keydown", (e) => {
  if (currentRouteId() !== "step3" || !rv.data) return;
  // 09-30 宇軒：中文輸入法選字按的 Enter 不算快捷鍵（以前會被當成「通過」，卡片重畫後游標跑掉，再按 ↓ 就跳到下一筆）
  if (e.isComposing || e.keyCode === 229) return;
  if (document.querySelector("dialog[open]")) return;   // 確認視窗開著時，快捷鍵不作用
  rv.lastActivity = Date.now();
  const tag = e.target.tagName;
  const inField = ["TEXTAREA", "INPUT", "SELECT"].includes(tag);
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "Escape" && inField) {
    const it = rvItem(rv.cur);
    if (it && e.target.closest(".rv-right")) { e.preventDefault(); rv.hold = rv.cur; rvSeek(Math.max(0, it.start - 2), true); }
    return;
  }
  if (e.key === "Enter" && !e.shiftKey && !rv.prepOpen) {
    // 逐字稿文字框裡 Enter 也是通過（Shift＋Enter 換行）；其他輸入框（跳到時間、起訖）各自處理
    if (tag === "TEXTAREA" ? e.target.id === "rv-text" : !inField && tag !== "BUTTON") { e.preventDefault(); rvPass(); }
    return;
  }
  if (inField) return;
  const k = e.key.toLowerCase();
  if (e.key === " ") { e.preventDefault(); if (rv.video) rv.video.paused ? rv.video.play().catch(() => {}) : rv.video.pause(); }
  else if (k === "j" && rv.video) { rvSeek(rv.video.currentTime - 5); }
  else if (k === "l" && rv.video) { rvSeek(rv.video.currentTime + 5); }
  else if ((k === "i" || k === "o") && rv.video && !rvReadonly()) {   // I／O：把目前時間填進「新增修改」的起點／終點
    if (!rv.ed.open) rvOpenEditor(null);
    rvEdSet(k === "i" ? "a" : "b", rv.video.currentTime);
  }
  else if (k === "e" && !rv.prepOpen) { e.preventDefault(); rvToggleMore(); }
  else if (e.key === "ArrowDown" && !rv.prepOpen) { e.preventDefault(); rvStep(1); }
  else if (e.key === "ArrowUp" && !rv.prepOpen) { e.preventDefault(); rvStep(-1); }
});

// 10-03 第八批 #66：要念的文字裡有英文時提醒（happy 念成 heavy）。只提醒、不擋；改不改由人決定。
const RV_EN_IDS = ["rv-text", "rv-namesay", "rv-tsay", "rv-tt", "rv-st"];
function rvHasEnglish(text) { return /[A-Za-z]{2,}/.test(text || ""); }
function rvEnglishHint(el) {
  if (!el || !RV_EN_IDS.includes(el.id)) return;
  const id = el.id + "-en";
  let tip = document.getElementById(id);
  if (!rvHasEnglish(el.value)) { if (tip) tip.remove(); return; }
  if (!tip) {
    tip = document.createElement("div");
    tip.id = id;
    tip.className = "rv-meta";
    tip.textContent = "這段有英文。AI 念英文容易念錯；不是專有名詞的話，建議改成中文再生成。";
    el.insertAdjacentElement("afterend", tip);
  }
}
if (typeof document !== "undefined") {
  document.addEventListener("input", (e) => rvEnglishHint(e.target));
  let rvEnTimer = null;
  new MutationObserver(() => {
    clearTimeout(rvEnTimer);
    rvEnTimer = setTimeout(() => RV_EN_IDS.forEach((i) => rvEnglishHint(document.getElementById(i))), 80);
  }).observe(document.documentElement, { childList: true, subtree: true });
}
