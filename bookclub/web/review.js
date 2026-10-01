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

const RV_TYPE = {
  "學員段落": { cls: "stu", label: "學員段落" },
  "名字": { cls: "name", label: "老師提到名字" },
  "學員名字": { cls: "name", label: "學員提到名字" },   // 09-29：保留原聲的學員自己講到名字
  "重疊": { cls: "ov", label: "重疊" },
  "刪除段落": { cls: "cut", label: "剪掉（連畫面）" },   // 10-01 宇軒：要看得出來連畫面都剪；第三批 7：三頁統一叫「剪掉」
  "局部消音": { cls: "mute", label: "消音（只拿掉聲音）" },
  "改成老師": { cls: "tfix", label: "改成老師（原聲）" },   // 10-01：人改成老師的段落照樣列出來
};
const RV_FILTERS = [["全部", "全部"], ["還沒確認", "還沒確認"], ["學員段落", "學員段落"], ["名字", "名字"],
  ["學員名字", "學員提到名字"], ["重疊", "重疊"], ["刪除段落", "剪掉"],
  ["局部消音", "消音"], ["改成老師", "改成老師"]];   // 10-01：以前沒有這兩類的篩選，各篩選加起來對不上「全部」
const rvIsName = (t) => t === "名字" || t === "學員名字";
const RV_STU_SHADES = ["#3f8f5a", "#6aae7f", "#2d6b43", "#8cc49d", "#4f9d6b", "#1f5434"];

const rv = {
  data: null, video: null, cur: null, filter: "全部", open: false, prepOpen: false, prepTab: "刪除",
  lastT: 0, hold: null, lastActivity: Date.now(), timeTimer: null, splitOpen: null, stopAt: null,
  ed: { open: false, kind: "刪除段落", id: null, a: null, b: null, busy: false, result: null },
};

// 「新增修改」的類型（後端 review.MANUAL_KINDS）與對齊規則的說明（bookclub/align.py）
const RV_KINDS = [["刪除段落", "剪掉（聲音和畫面都拿掉，影片會變短）"], ["局部消音", "消音（只拿掉聲音，畫面留著）"], ["學員發言", "漏抓的發言（學員，或老師要老師重念的話）"],
  ["名字", "漏抓的「老師提到名字」"], ["重疊", "漏抓的重疊"]];
const RV_RULE_HINT = {
  "刪除段落": "按新增後，起點終點各自對齊附近 0.5 秒內的安靜處（不切在字中間）",
  "局部消音": "按新增後，起點終點各自對齊附近 0.5 秒內的安靜處",
  "學員發言": "按新增後，對齊句子的開頭、結尾（1 秒內）。選「老師」＝這一段老師重念（用老師的 AI 聲音），新增後在卡片上確認要念的字",
  "名字": "按新增後，對齊逐字稿裡字的時間，前後留一點停頓",
  "重疊": "按新增後，對齊句子的開頭、結尾（1 秒內）",
};
// 人工標的老師整段（老師 AI 重念一整段）改時間：對齊句子邊界，不是對字（10-01）
// 10-01 走查：改既有那一筆（按鈕是「儲存修改」）時不寫「按新增後」，也不提新增才有的「選老師」
function rvRuleHint(kind, whole, editing) {
  if (whole) return "按儲存後，對齊句子的開頭、結尾（1 秒內）";
  const h = RV_RULE_HINT[kind] || "";
  return editing ? h.replace("按新增後", "按儲存後").replace(/。選「老師」.*$/, "") : h;
}
const RV_ITEM_KIND = { "改成老師": "學員發言", "學員段落": "學員發言", "名字": "名字", "重疊": "重疊", "刪除段落": "刪除段落", "局部消音": "局部消音" };

function rvKey(it) { return `${it["類型"]}:${it.id}`; }
function rvItem(key) { return rv.data["項目"].find((x) => rvKey(x) === key); }
function rvItems() { return rv.data["項目"]; }
// 09-29 宇軒：落在前面已確認刪除的段落裡＝前面核對過了，顯示成「通過」
function rvInCut(it) { return (it["不用處理"] || "").includes("剪掉"); }

// 09-29 宇軒：抓錯的名字（標了「不是名字」「是地名」）＝不用改
function rvNotName(it) { return (it.tags || []).some((g) => g === "不是名字" || g === "是地名"); }

function rvDone(it) { return !!(it["已確認"] || it["不用處理"] || it["涵蓋"]) && !it["還缺"]; }   // 10-01：被別筆涵蓋的自動算處理好；還缺東西的不算（1-1）

function rvFmt(sec, digits = 0) {
  if (sec == null || isNaN(sec)) return "—";
  sec = Math.max(0, sec);
  if (digits) sec = Math.round(sec * 10 ** digits) / 10 ** digits;   // 10-01 走查：59.96 秒以前顯示成「54:60.0」
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const ss = digits ? s.toFixed(digits).padStart(3 + digits, "0") : String(Math.floor(s)).padStart(2, "0");
  return `${h ? h + ":" + String(m).padStart(2, "0") : m}:${ss}`;
}

function rvParseTime(text) {
  const parts = String(text).trim().replace("：", ":").split(":");
  if (!parts.length || parts.length > 3 || parts.some((p) => p.trim() === "" || isNaN(Number(p)))) return null;
  return parts.reduce((acc, p) => acc * 60 + Number(p), 0);
}

function rvHm(sec) {
  if (sec == null) return "—";
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h ? `${h} 小時 ${m} 分` : `${m} 分`;
}

function rvStudents() { return Object.keys(rv.data["學員"] || {}); }

function rvStuColor(who) {
  const i = rvStudents().indexOf(who);
  return RV_STU_SHADES[(i < 0 ? 0 : i) % RV_STU_SHADES.length];
}

function rvWho(name) {
  if (!name) return "（不知道是誰）";
  if (name === "老師") return "老師";
  const code = ((rv.data["學員"] || {})[name] || {})["代號"];
  return code ? `${name}（${code}）` : name;
}

function rvPrepDone() {
  const p = rv.data["開始前確認"] || {};
  return RV_PREP.every(([k]) => p[k]);
}

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
  const back = n.back === "step5" ? ["#step5", "回第 5 步成品檢查"] : ["#step4", "回第 4 步總檢查"];
  return `<p class="rv-gonote">${esc(n.text)}　<a href="${back[0]}" id="rv-goback">${back[1]}</a> <button class="ghost small" id="rv-gonote-x" aria-label="關掉這一行">×</button></p>`;
}

async function renderReview() {
  contentEl.innerHTML = "<p>載入中…</p>";
  rv.data = await apiGet("/api/review");
  rv.prepOpen = !rvPrepDone();
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
          <button class="ghost" id="rv-export">匯出覆核結果</button>
          <button class="primary" id="rv-go4" disabled>全部通過，開始 AI 修改</button>
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

const RV_RO_ALLOW = "#rv-prep-btn, #rv-set-btn, #rv-key-btn, #rv-export, #rv-prev, #rv-next, .rv-filters button, [data-tab], .rv-segplay, .rv-sample, [data-te-play], [data-te-close], #rv-busy button";

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
// 全部通過 → 確認 → 跳第 4 步開始 AI 修改（09-29 宇軒）
// ---------------------------------------------------------------------------

function rvLeft() { return rvItems().filter((x) => !rvDone(x)).length; }

function rvUpdateGo4() {
  const btn = document.getElementById("rv-go4");
  if (!btn) return;
  const left = rvLeft();
  btn.disabled = left > 0;
  btn.title = left ? `還有 ${left} 筆沒通過，全部通過後才能開始` : "全部通過了，可以開始 AI 修改";
}

async function rvConfirmGo4() {
  if (rvLeft()) return;
  if (rv.video && !rv.video.paused) rv.video.pause();
  // 10-01 第三批 9：第 4 步的開始前總檢查還沒處理完 → 直接帶去那裡（以前只跳對話框寫「還不能開始」）
  try {
    const fc = await apiGet("/api/execute/finalcheck");
    if (!fc["可以開始"] || !fc["看過"]) { execGoCheck = "從第 3 步過來：開始 AI 修改之前，先把下面的開始前總檢查處理完。"; location.hash = "#step4"; return; }
  } catch (e) { /* 讀不到總檢查：照舊跳對話框，開始時後端會擋 */ }
  const dlg = document.createElement("dialog");
  dlg.className = "rv-confirm";
  dlg.setAttribute("aria-labelledby", "rv-confirm-title");
  dlg.innerHTML = `
    <h2 id="rv-confirm-title">全部都檢查好了嗎？</h2>
    <p>開始之後，AI 會依序生成老師的名字句子、學員重念，最後組裝成品影片。<b>整支影片會花比較多時間</b>，
      跑的時候電腦不要睡眠；第 4 步看得到每一類做到幾筆。</p>
    <p class="muted">跑完之後到第 5 步「成品檢查」看結果；中途有問題可以停下來，已經做好的不會重做。</p>
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
      if (/總檢查/.test(err.message)) { close(); execGoCheck = `從第 3 步過來：${err.message}`; location.hash = "#step4"; return; }
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

// 09-29 宇軒：做法的顯示名稱（存檔的值不變）
// 10-01 第三批 7：三頁統一四個名稱「老師重念」「學員重念」「剪掉」「消音」；8：「霧化」寫成「聲音霧化」
const RV_HOW_LABEL = { "整句換掉": "老師重念", "學員整句生成": "學員重念", "霧化": "聲音霧化",
  // 重疊（09-30 宇軒）：畫面上用這幾個名稱，存檔的值不變
  "只留老師": "生成老師聲音", "只留學員": "生成學員聲音", "只留老師原聲學員消音": "消音",
  "兩邊都重生成": "兩邊都重新生成（照原本的時間）",
  // 10-01 宇軒：剪掉＝聲音和畫面都拿掉；消音＝只拿掉聲音
  "刪除這段": "剪掉這段（連畫面）", "刪除": "剪掉（連畫面）", "不刪": "不剪" };
const rvHowLabel = (h) => RV_HOW_LABEL[h] || h;

function rvChip(it) {
  const t = RV_TYPE[it["類型"]] || { cls: "", label: it["類型"] };
  return `<span class="rv-chip ${t.cls}"><i></i>${esc(t.label)}</span>`;
}

function rvSuggestText(it) {
  const s = it["建議"] || {};
  if (it["類型"] === "學員段落") return s["做法"] === "刪除這段" ? "剪掉這段（聲音和畫面都拿掉，影片會變短）" : "學員重念（逐字稿沒問題就通過）";
  if (it["類型"] === "改成老師") return "保留老師原聲";
  return rvHowLabel(s["做法"]) || "—";
}

function rvChosen(it) {   // 現在會套用的做法：人改過的，或建議
  const t = it["類型"];
  if (t === "重疊") return it["做法"] || (it["建議"] || {})["做法"];
  if (rvIsName(t)) return rvNotName(it) ? "不是名字，不用改" : it["做法"];
  if (t === "刪除段落") return it["來源"] === "建議" ? (it["決定"] || "刪除") : (it["狀態"] === "還原" ? "不刪" : "刪除");
  if (t === "局部消音") return it["狀態"] === "還原" ? "不消音" : it["方式"];   // 10-01：還原的以前清單還寫「墊底噪」
  return null;
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
    : rvInCut(it) ? `<span class="rv-state ok">✓ 通過：這段落在剪掉的片段裡（聲音和畫面都拿掉）${it["剪掉的片段"] ? "" : "（要救回：把那一筆剪掉片段改成不剪／還原）"}</span>${it["剪掉的片段"] ? ` <button class="ghost small" id="rv-uncut">取消剪掉（還原這一段）</button>` : ""}`
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
  const coverNote = back + lack + (it["涵蓋消失"] ? `<p class="rv-warnline">原本由〈${esc(it["涵蓋消失"]["名稱"] || it["涵蓋消失"].id)}〉涵蓋，現在沒有了（那一筆改了時間、做法或被還原），請重新看。</p>`
    : it["涵蓋待通過"] ? `<p class="rv-note">這一處整個落在〈${esc(it["涵蓋待通過"]["名稱"])}〉裡；那一筆通過之後，這一處就自動算處理好。</p>` : "");
  box.innerHTML = `
    <article class="rv-card" data-key="${esc(rv.cur)}">
      <header>
        ${rvChip(it)}${it["人工新增"] ? `<span class="rv-tag">人工新增</span>` : ""}${it["代號改過"] ? `<span class="rv-tag warn">代號改過，請再看一次</span>` : ""}
        <span class="rv-when">${esc(when)}</span>
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
        ${RV_ITEM_KIND[it["類型"]] ? `<button class="ghost" id="rv-retime" title="用「新增修改」面板改這一筆的起點終點">改時間</button>` : ""}
        ${it["類型"] === "名字" ? `<button class="${rvNotName(it) ? "primary" : "ghost"}" id="rv-notname" aria-pressed="${rvNotName(it)}" title="抓錯了，這裡其實沒有人名：照原音不改，這個寫法以後也不會再抓（已選的再按一次取消）">${rvNotName(it) ? "✓ " : ""}${it["老師整段"] ? "不用改，保留老師原聲" : "不是名字，不用改"}</button>` : ""}
        ${it["類型"] === "學員段落" ? `<button class="ghost" id="rv-isteacher" title="聲音辨識判錯：這一段其實是老師在講話">這段其實是老師</button>` : ""}
        ${it["類型"] === "改成老師" ? `<button class="ghost" id="rv-tai" title="這一段照打的字老師重念（用老師的 AI 聲音，例如裡面有名字）">改成老師重念</button>` : ""}
        ${it["可以刪"] ? `<button class="ghost" id="rv-del" title="人工新增的加錯了：整筆刪掉（會留一筆紀錄，在「設定」看得到）">刪掉這一筆</button>` : ""}
        <span class="spacer"></span>
        <button class="ghost" id="rv-prev" aria-label="上一筆">上一筆</button>
        <button class="ghost" id="rv-next" aria-label="下一筆">下一筆</button>
      </div>
      <div class="rv-more" id="rv-more" ${rv.open ? "" : "hidden"}>${rvMoreHtml(it)}</div>
    </article>`;
  document.getElementById("rv-pass").addEventListener("click", () => rvPass());
  document.getElementById("rv-change").addEventListener("click", rvToggleMore);
  const retime = document.getElementById("rv-retime");
  if (retime) retime.addEventListener("click", () => {
    // 10-01 第三批：重疊選生成學員聲音（自己生成）：要改的是「會換掉的範圍」，打開改做法裡的那一格
    if (it["類型"] === "重疊" && it["生成範圍"] && rvChosen(it) === "只留學員") {
      if (!rv.open) rvToggleMore();
      const box = document.getElementById("rv-genbox");
      if (box) { box.scrollIntoView({ block: "nearest" }); const f = box.querySelector("input"); if (f) f.focus({ preventScroll: true }); }
      return;
    }
    rvOpenEditor(it);
  });
  const notName = document.getElementById("rv-notname");
  if (notName) notName.addEventListener("click", async () => {
    const off = rvNotName(it);
    const tags = (it.tags || []).filter((g) => g !== "不是名字" && g !== "是地名").concat(off ? [] : ["不是名字"]);
    await rvSaveName(it, { tags, "已確認": !off });
    it["已確認"] = !off;
    rvRenderMain();
  });
  const isT = document.getElementById("rv-isteacher");
  if (isT) isT.addEventListener("click", async () => {
    // 09-29 宇軒：聲音辨識把老師判成學員時，一鍵改回老師（只有一部分是老師：先用「改做法」裡的「從游標處切開」）
    if (!confirm(`把 ${rvFmt(it.start, 1)}–${rvFmt(it.end, 1)} 這一段改成老師？\n改了之後這段不會重念，也會補找這段裡老師提到的名字。\n只有一部分是老師的話，先按「改做法」→「從游標處切開」。`)) return;
    await rvSaveTurnText(it);
    const res = await apiPost("/api/turns/save", { id: it.id, "說話者": "老師" });
    if (res["補找到的老師名字"]) alert(`這段裡補找到 ${res["補找到的老師名字"]} 個老師提到的名字，已經加進清單。`);
    await rvReload();
    if (res["改成老師重念"]) {   // 09-30：沒有逐字稿句子的段落，沒辦法自動找名字 → 改成一筆「老師這一段用 AI 聲音重念」
      alert("這一段沒有逐字稿句子，沒辦法自動找名字：已經改成「老師這一段用 AI 聲音重念」，請確認要念的字。\n不用重念的話，按「不用改，保留老師原聲」。");
      const key = `名字:${res["改成老師重念"]}`;
      if (rvItem(key)) { rv.filter = "全部"; rvRenderList(); rvSelect(key, { seek: false }); }
    }
  });
  const uncut = document.getElementById("rv-uncut");   // 10-01 2-4：「通過＝剪掉」產生的剪掉片段，在原本那一段取消
  if (uncut) uncut.addEventListener("click", async () => {
    if (!confirm("取消剪掉：這一段的聲音和畫面留著，改成學員重念？")) return;
    await apiPost("/api/review/cut", { id: it["剪掉的片段"], "狀態": "還原" });
    await apiPost("/api/turns/save", { id: it.id, "短句保留": true });
    await rvReload();
  });
  const goCover = document.getElementById("rv-gocover");
  if (goCover) goCover.addEventListener("click", () => {
    const c = it["涵蓋"];
    const hit = rvItems().find((x) => x.id === c.id || (x["類型"] === "名字" && `S${String(x.id).padStart(3, "0")}` === c.id)
      || (x["類型"] === "重疊" && (c["重疊項目"] || []).includes(x.id)))
      || rvItems().find((x) => x.start <= c.start + 0.05 && c.end - 0.05 <= x.end && x !== it);
    if (hit) { rv.filter = "全部"; rvRenderList(); rvSelect(rvKey(hit)); } else rvSeek(c.start);
  });
  const tai = document.getElementById("rv-tai");   // 10-01 2-3：改成老師的段落 → 老師 AI 重念
  if (tai) tai.addEventListener("click", async () => {
    if (!confirm(`${rvFmt(it.start, 1)}–${rvFmt(it.end, 1)} 這一段改成老師重念（用老師的 AI 聲音）？新增後在那一筆卡片上確認要念的字。`)) return;
    try { await apiPost("/api/review/manual", { "類型": "學員發言", start: it.start, end: it.end, "說話者": "老師" }); } catch (err) { alert(err.message); return; }
    await rvReload();
  });
  const del = document.getElementById("rv-del");
  if (del) del.addEventListener("click", () => rvDeleteManual(it));
  document.getElementById("rv-prev").addEventListener("click", () => rvStep(-1));
  document.getElementById("rv-next").addEventListener("click", () => rvStep(1));
  rvBindBody(it);
  rvBindMore(it);
  rvRenderTimeline();
}

// 10-01 宇軒：逐字稿框下面的狀態（「人改過」看不懂）
const RV_ST_EDITED = "狀態：要念的句子你改過了（已存）";
const RV_ST_AUTO = "狀態：照逐字稿自動排的";

function rvPassCuts(it) {   // 這一筆按通過會剪掉（聲音和畫面）
  return it["類型"] === "學員段落" && !it["已確認"] && !rvInCut(it) && (it["建議"] || {})["做法"] === "刪除這段" && !it["短句保留"];
}

function rvTimeRows(prefix, pairs) {   // 起訖欄位＋用目前播放位置＋±0.1（沿用新增修改面板的樣子）
  return pairs.map(([key, label, t]) => `<div class="rv-edrow"><span class="rv-edlab">${esc(label)}</span>
      <input class="rv-t" data-${prefix}="${esc(key)}" value="${esc(rvFmt(t, 2))}" aria-label="${esc(label)}（例如 43:15.2）">
      <button class="ghost small" data-${prefix}now="${esc(key)}">用目前播放位置</button>
      <button class="ghost small" data-${prefix}nudge="${esc(key)}" data-d="-0.1" aria-label="${esc(label)}往前 0.1 秒">−0.1</button>
      <button class="ghost small" data-${prefix}nudge="${esc(key)}" data-d="0.1" aria-label="${esc(label)}往後 0.1 秒">＋0.1</button></div>`).join("");
}

function rvBindTimeRows(prefix, get, save) {   // get(key) → 秒；save(key, 秒)
  document.querySelectorAll(`[data-${prefix}]`).forEach((box) => box.addEventListener("change", () => {
    const t = rvParseTime(box.value);
    box.classList.toggle("bad", t == null);
    if (t != null) save(box.dataset[prefix], t);
  }));
  document.querySelectorAll(`[data-${prefix}now]`).forEach((b) => b.addEventListener("click", () => {
    if (rv.video) save(b.dataset[`${prefix}now`], Math.round(rv.video.currentTime * 100) / 100);
  }));
  document.querySelectorAll(`[data-${prefix}nudge]`).forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset[`${prefix}nudge`];
    save(k, Math.max(0, Math.round((get(k) + Number(b.dataset.d)) * 100) / 100));
  }));
}

function rvRangeHtml(it) {   // 10-01 宇軒 7-1：名字的重念範圍（可以改）
  const w = it["整句"];
  const whole = w["原本整句"] || [w.start, w.end];
  const tag = w["範圍"] === "人選" ? "你改過的範圍" : w["範圍"] === "自動"
    ? `整句 ${(whole[1] - whole[0]).toFixed(1)} 秒太長，自動縮成名字所在的那一小句（照逐字稿的空白切）`
    : w["範圍"] === "逐字" ? "照整句（句子文字裡找不到名字，改用逐字時間的字）" : "照整句";
  return `<div class="rv-field rv-range"><div class="rv-edrow"><b>重念範圍</b><span class="rv-meta">${esc(tag)}</span>
      <button class="ghost small" id="rv-playrange">只播重念範圍</button>
      ${w["範圍"] === "人選" ? `<button class="ghost small" id="rv-rangereset">回到預設範圍</button>` : ""}</div>
      ${rvTimeRows("rg", [["0", "起點", w.start], ["1", "終點", w.end]])}</div>`;
}

async function rvSaveRange(it, a, b) {
  const w = it["整句"];
  if (b - a < 0.3) { alert("重念範圍的結束要晚於開始"); return; }
  const body = { id: it.id, "整句起訖": [a, b] };
  if (w["改稿"] && confirm("重念範圍改了。要念的句子要不要照新範圍的逐字稿重排？\n按「確定」重排（你改過的字會被蓋掉）；按「取消」保留你改的字。")) body["改稿"] = "";
  try { await apiPost("/api/review/name", body); } catch (err) { alert(err.message); return; }
  await rvReload();
}

function rvSidesHtml(it) {   // 10-01 B 方案：兩邊各自的起訖＋兩列小時間條（上列老師、下列學員，只用來看相對位置）
  const T = it["老師起訖"], S = it["學員起訖"];
  const lo = Math.min(T[0], S[0]), hi = Math.max(T[1], S[1]), span = Math.max(0.1, hi - lo);
  const bar = (r) => `<i style="left:${((r[0] - lo) / span) * 100}%;width:${Math.max(1, ((r[1] - r[0]) / span) * 100)}%"></i>`;
  return `<div class="rv-field rv-sides" id="rv-sides">
      <div class="rv-minibar" aria-hidden="true"><div class="row t">${bar(T)}</div><div class="row s">${bar(S)}</div></div>
      <p class="rv-meta">上列老師、下列學員（${esc(rvFmt(lo, 1))}–${esc(rvFmt(hi, 1))}）。兩段各自生成、放回原本的時間，疊到的地方混在一起。
        <button class="ghost small" id="rv-sideplay">播這一段</button></p>
      ${rvTimeRows("sd", [["老師起訖:0", "老師 起點", T[0]], ["老師起訖:1", "老師 終點", T[1]], ["學員起訖:0", "學員 起點", S[0]], ["學員起訖:1", "學員 終點", S[1]]])}</div>`;
}

// 10-01 第三批：重疊選「生成學員聲音」、不在學員段落裡：寫清楚會換掉哪幾秒、這幾秒裡老師的話會怎樣
const RV_GEN_SRC = { "學員整句": "學員那一整句", "你改過的範圍": "你改過的範圍", "你改小的重疊時間": "照你之前改小的重疊時間" };

function rvGenNoteHtml(it) {
  const g = it["生成範圍"];
  if (!g) return "";
  const who = it["學員已選"] ? rvWho(it["學員說話者"]) : "選的那位學員";
  const short = (s) => (s.length > 40 ? `${s.slice(0, 40)}⋯` : s);
  let html = `<p class="rv-note rv-gennote"><b>會換掉 ${esc(rvFmt(g.start, 1))}–${esc(rvFmt(g.end, 1))}</b>（${(g.end - g.start).toFixed(1)} 秒，${esc(RV_GEN_SRC[g["來源"]] || "")}）：
    這幾秒整段換成${esc(who)}的生成聲音，<b>裡面老師的聲音都不保留</b>。</p>`;
  const ts = g["老師"] || [];
  if (!ts.length) {
    html += `<p class="rv-meta">這幾秒裡沒有整句是老師的話；重疊那一小段老師的聲音${it["老師文字"] ? `（${esc(short(it["老師文字"]))}）` : ""}會跟著不見。</p>`;
  }
  for (const s of ts) {
    const outs = s["範圍外"] || [];
    html += outs.length
      ? `<p class="rv-warnline">老師 ${esc(rvFmt(s.start, 1))}–${esc(rvFmt(s.end, 1))} 這一句（${esc(short(s.text || ""))}）只有一部分在範圍裡：範圍裡的會不見，
          範圍外的 ${outs.map((x) => `${esc(rvFmt(x[0], 1))}–${esc(rvFmt(x[1], 1))}`).join("、")} 照原聲留著，會聽到老師的話從中間斷掉或接上。
          要整句都換掉：把範圍改大，或改選「生成老師聲音」。</p>`
      : `<p class="rv-meta">老師 ${esc(rvFmt(s.start, 1))}–${esc(rvFmt(s.end, 1))} 這一句整句在範圍裡，會不見（${esc(short(s.text || ""))}）。</p>`;
  }
  if ((g["疊到"] || []).length) html += `<p class="rv-warnline">範圍跟${g["疊到"].map((n) => `〈${esc(n)}〉`).join("、")}疊到：組裝時比較長的那一筆優先，另一筆會被蓋掉。</p>`;
  return html + `<p class="rv-meta">要改範圍：按「改時間」。</p>`;
}

function rvGenBoxHtml(it) {
  const g = it["生成範圍"];
  if (!g) return "";
  return `<div class="rv-edrow"><b>會換掉的範圍</b><span class="rv-meta">${esc(RV_GEN_SRC[g["來源"]] || "")}</span>
      <button class="ghost small" id="rv-genplay">播這一段</button>
      ${g["來源"] !== "學員整句" ? `<button class="ghost small" id="rv-genreset">回到預設（學員那一整句）</button>` : ""}</div>
    ${rvTimeRows("gs", [["0", "起點", g.start], ["1", "終點", g.end]])}
    <p class="rv-meta">照你填的時間存，不會自動對齊。這段時間整段換成學員的生成聲音，裡面老師的聲音不保留。</p>`;
}

function rvMarkChanges(text, changes) {
  // 標出自動換成代號的字（位置是新文字裡的索引）
  let out = "", i = 0;
  for (const c of changes || []) {
    out += esc(text.slice(i, c["位置"])) + `<mark title="原本是「${esc(c["原字"])}」">${esc(c["換成"])}</mark>`;
    i = c["位置"] + c["換成"].length;
  }
  return out + esc(text.slice(i));
}

function rvBodyHtml(it) {
  const t = it["類型"];
  if (t === "學員段落") {
    const text = it["已確認"] ? it["校對稿"] : it["建議稿"];
    const rows = Math.min(9, Math.max(3, Math.ceil((text || "").length / 34)));
    return `<p class="rv-who">${esc(rvWho(it["說話者"]))}${it["手動標記"] ? "　（人工標記的段落）" : ""}</p>
      <textarea id="rv-text" rows="${rows}" aria-label="逐字稿（可以直接改）">${esc(text)}</textarea>
      ${(it["換過的字"] || []).length && !it["已確認"] ? `<p class="rv-note">自動換成代號的地方：${rvMarkChanges(it["建議稿"], it["換過的字"])}</p>` : ""}
      ${it["代號改過"] ? `<p class="rv-warnline">代號改過，請再看一次（原本的代號：${esc(it["代號改過"])}，別人也在用，沒有自動換）。</p>` : ""}
      ${it["含本名"] ? `<p class="rv-warnline">文字裡還有名冊上的本名，要換成代號。</p>` : ""}
      ${it["問老師"] ? `<p class="rv-note">已標「聽不清楚，問老師」${it["問老師備註"] ? "：" + esc(it["問老師備註"]) : ""}</p>` : ""}`;
  }
  if (t === "學員名字") {
    const w = it["整句"];
    return `<p class="rv-who">${esc(rvWho(it["學員"]))}（保留原聲）講到名字</p><p class="rv-quote">${it.sentence_html}</p>
      ${w ? `<p class="rv-note">選「換成代號」時，用${esc(rvWho(it["學員"]))}自己的聲音重念這句：${esc(w["換成代號"] || w["原文"])}${w["換成代號"] ? "" : "（句子裡找不到比對到的字，會退回直接消音）"}</p>` : ""}
      <p class="rv-meta">代號 ${esc(it["代號"] || "（沒有）")}${it["信心"] === "低" ? "　低信心，先聽清楚是不是名字" : ""}</p>`;
  }
  if (t === "名字" && it["老師整段"]) {   // 09-30：人工標的一段老師的話，整段用老師 AI 聲音重念
    const w = it["整句"];
    return `<p class="rv-who">老師這一段（${esc(rvFmt(w.start, 1))}–${esc(rvFmt(w.end, 1))}）老師重念（用老師的 AI 聲音），原本的聲音整段換掉</p>
      <div class="rv-field"><label>要念的字（名字要寫成代號；範圍內老師講的話都要寫進來，學員的話不要寫）
        <textarea id="rv-namesay" rows="2" data-auto="${esc(w["換成代號"] || "")}">${esc(w["改稿"] || w["換成代號"] || "")}</textarea></label>
        <span class="rv-meta" id="rv-namesay-st">${w["改稿"] ? RV_ST_EDITED : w["換成代號"] ? "狀態：照逐字稿自動排的（名冊上的本名已換成代號），請對照聲音確認" : "逐字稿這段沒有字，請打上要念的字；空白的話整段會消音"}</span>
        ${w["實際會念"] ? `<p class="rv-warnline">文字裡還有名冊上的名字，生成時會自動換成代號。實際會念：${esc(w["實際會念"])}</p>` : ""}</div>
      <p class="rv-meta">這一段不用重念、要保留老師原聲：按「不用改，保留老師原聲」。</p>`;
  }
  if (t === "名字") {
    const w = it["整句"];
    return `<p class="rv-quote">${it.sentence_html}</p>
      ${w ? `${rvRangeHtml(it)}
        <p class="rv-note">重念範圍（${esc(rvFmt(w.start, 1))}–${esc(rvFmt(w.end, 1))}，${(w.end - w.start).toFixed(1)} 秒）的逐字稿：${esc(w["原文"])}</p>
        <div class="rv-field"><label>老師重念要念的句子（逐字稿漏了名字、名字講了兩次，直接在這裡改；重念範圍內老師講的話都要寫進來）
          <textarea id="rv-namesay" rows="2" data-auto="${esc(w["換成代號"] || "")}">${esc(w["改稿"] || w["換成代號"] || w["原文"])}</textarea></label>
          <span class="rv-meta" id="rv-namesay-st">${w["改稿"] ? RV_ST_EDITED : w["換成代號"] ? RV_ST_AUTO : "句子裡找不到比對到的字，要人改（把名字改成代號才能通過）"}</span>
          ${w["字太少"] ? `<p class="rv-warnline">要念 ${w["字數"][0]} 個字，重念範圍的逐字稿有 ${w["字數"][1]} 個字：這一段其他的話會不見。請把重念範圍改小，或把話補齊（不然不能通過）。</p>` : ""}
          ${w["實際會念"] ? `<p class="rv-warnline">句子裡還有名冊上的名字，生成時會自動換成代號。實際會念：${esc(w["實際會念"])}</p>` : ""}</div>` : ""}
      <p class="rv-meta">代號 ${esc(it["代號"] || "（沒有）")}${it["信心"] === "低" ? "　低信心，先聽清楚是不是名字" : ""}</p>`;
  }
  if (t === "改成老師") {
    return `<p class="rv-who">這一段你改成老師了：照老師原聲留著，不重念、不用處理</p>
      <p class="rv-quote">${esc(it["校對稿"] || it["原文"] || "（沒有逐字稿）")}</p>
      <p class="rv-meta">切點選得不好：按「改時間」；其實是學員：在「改做法」選是哪一位學員；裡面有名字要換：按「改成老師重念」。</p>`;
  }
  if (t === "重疊") {
    const who = it["學員已選"] ? rvWho(it["學員說話者"]) : "學員（還沒選是誰）";   // 10-01 1-1：沒人選過不顯示猜的
    return `<dl class="rv-pair"><dt>老師</dt><dd>${esc(it["老師文字"] || "（聽不出來）")}</dd>
      <dt>${esc(who)}</dt><dd>${esc(it["學員文字"] || "（聽不出來）")}</dd></dl>
      <p class="rv-meta">重疊 ${Number(it.length || it.end - it.start).toFixed(1)} 秒</p>
      ${rvChosen(it) === "只留學員" && !it["涵蓋"] ? rvGenNoteHtml(it) : ""}`;
  }
  if (t === "刪除段落") {
    return `<p class="rv-quote">${esc(rvFmt(it.start, 1))} 到 ${esc(rvFmt(it.end, 1))}，共 ${(it.end - it.start).toFixed(1)} 秒剪掉：聲音和畫面都拿掉，影片會變短（只想拿掉聲音，改用消音）</p>
      <p class="rv-meta">${it["來源"] === "建議" ? `影片分析找到的（${esc(it["建議類型"] || "")}）` : "人手動加的"}</p>`;
  }
  return `<p class="rv-quote">${esc(rvFmt(it.start, 1))} 到 ${esc(rvFmt(it.end, 1))} 消音：只拿掉聲音，畫面留著（${esc(rvHowLabel(it["方式"] || ""))}）</p>`;
}

function rvBindBody(it) {
  const w = it["整句"];
  const pr = document.getElementById("rv-playrange");
  if (pr && w) pr.addEventListener("click", () => { rv.stopAt = w.end; rvSeek(w.start, true); });
  const rr = document.getElementById("rv-rangereset");
  if (rr) rr.addEventListener("click", async () => {
    await apiPost("/api/review/name", { id: it.id, "整句起訖": null }); await rvReload();
  });
  if (w && document.querySelector("[data-rg]")) {
    const cur = { 0: w.start, 1: w.end };
    rvBindTimeRows("rg", (k) => cur[k], (k, t) => { cur[k] = t; rvSaveRange(it, cur[0], cur[1]); });
  }
  const ta = document.getElementById("rv-text");
  if (ta) ta.addEventListener("blur", () => rvSaveTurnText(it));
  const say = document.getElementById("rv-namesay");   // 09-29：名字整句的重念稿
  if (say) say.addEventListener("blur", async () => {
    const v = say.value.trim();
    const same = v === (say.dataset.auto || "").trim() || (!it["老師整段"] && v === ((it["整句"] || {})["原文"] || "").trim());
    const txt = same ? "" : v;   // 沒改（還是原文）不算人改過：原文裡可能還有本名
    if (txt === ((it["整句"] || {})["改稿"] || "")) return;
    await apiPost("/api/review/name", { id: it.id, "改稿": txt });
    if (it["整句"]) it["整句"]["改稿"] = txt;
    const st = document.getElementById("rv-namesay-st");
    if (st) st.textContent = txt ? RV_ST_EDITED : RV_ST_AUTO;
  });
}

function rvRadios(name, options, current, cls) {
  return options.map((o) => `<label class="rv-radio"><input type="radio" name="${esc(name)}" class="${cls}" value="${esc(o)}" ${o === current ? "checked" : ""}> ${esc(rvHowLabel(o))}</label>`).join("");
}

function rvCutRange(it) {   // 「刪除這段」刪的範圍：名字＝整句，其他＝這一筆
  const w = it["整句"];
  return w ? [w.start, w.end] : [it.start, it.end];
}

function rvCutThisHtml(it) {
  if (it["不用處理"]) return "";
  const [a, b] = rvCutRange(it);
  return `<div class="rv-field rv-row"><button class="ghost" id="rv-cutthis">剪掉這段（${esc(rvFmt(a, 1))}–${esc(rvFmt(b, 1))}，聲音和畫面都拿掉，影片會變短）</button></div>`;
}

function rvMoreHtml(it) {
  const t = it["類型"];
  const opts = rv.data["選項"];
  if (t === "學員段落") {
    const whoOpts = ["老師", ...rvStudents(), "新學員"].map((n) => `<option value="${esc(n)}" ${n === it["說話者"] ? "selected" : ""}>${esc(n === "新學員" ? "新的一位學員" : rvWho(n))}</option>`).join("");
    const minor = (it["建議"] || {})["做法"] === "刪除這段" || it["短句保留"];
    const cur = it["短句保留"] || (it["建議"] || {})["做法"] !== "刪除這段" ? "學員整句生成" : "刪除這段";
    return `${minor ? `<div class="rv-field rv-choices">${rvRadios("rv-minor", ["學員整句生成", "刪除這段"], cur, "rv-minor")}
        <span class="rv-meta">不重要的短句預設剪掉（聲音和畫面都拿掉，影片會變短）；選「剪掉這段（連畫面）」後按通過才會真的剪</span></div>` : ""}
      <div class="rv-field"><label>說話者 <select id="rv-who">${whoOpts}</select></label></div>
      <div class="rv-field rv-row">
        <button class="ghost" id="rv-merge">併進上一段</button>
        <button class="ghost" id="rv-split">從游標處切開</button>
      </div>
      <div class="rv-field"><label class="rv-check"><input type="checkbox" id="rv-ask" ${it["問老師"] ? "checked" : ""}> 聽不清楚，問老師</label>
        <input id="rv-asknote" placeholder="要問老師什麼" value="${esc(it["問老師備註"] || "")}" ${it["問老師"] ? "" : "hidden"}></div>
      ${it["原文"] !== it["校對稿"] ? `<details class="rv-orig"><summary>看原本轉出來的文字</summary>${esc(it["原文"])}</details>` : ""}
      ${rvCutThisHtml(it)}`;
  }
  if (t === "改成老師") {   // 10-01 2-3：改回學員（選哪一位）
    const whoOpts = ["老師", ...rvStudents(), "新學員"].map((nm) => `<option value="${esc(nm)}" ${nm === "老師" ? "selected" : ""}>${esc(nm === "新學員" ? "新的一位學員" : rvWho(nm))}</option>`).join("");
    return `<div class="rv-field"><label>說話者 <select id="rv-who">${whoOpts}</select></label>
      <span class="rv-meta">改回學員：這一段變回待確認的學員段落（學員重念）</span></div>`;
  }
  if (t === "名字" && it["老師整段"]) {   // 老師整段：只有「整段重念」或「整段消音」
    return `<div class="rv-field rv-choices">${rvRadios("rv-namehow", opts[t].filter((h) => h !== "只換名字"), it["做法"], "rv-namehow")}</div>
      <div class="rv-field"><input id="rv-note" placeholder="備註（選填）" value="${esc(it.note || "")}"></div>
      ${rvCutThisHtml(it)}`;
  }
  if (rvIsName(t)) {
    const tags = opts["名字標記"].map((g) => `<label class="rv-check"><input type="checkbox" class="rv-tag" value="${esc(g)}" ${it.tags.includes(g) ? "checked" : ""}> ${esc(g)}</label>`).join("");
    return `<div class="rv-field rv-choices">${rvRadios("rv-namehow", opts[t], it["做法"], "rv-namehow")}</div>
      <div class="rv-field rv-choices">${tags}</div>
      <div class="rv-field"><input id="rv-note" placeholder="備註（選填）" value="${esc(it.note || "")}"></div>
      ${rvCutThisHtml(it)}`;
  }
  if (t === "重疊" && it["涵蓋"]) {   // 10-01 7-4：被別筆涵蓋的，做法選項收起來
    return `<p class="rv-meta">這一處整個落在〈${esc(it["涵蓋"]["名稱"])}〉（${esc(rvFmt(it["涵蓋"].start, 1))}–${esc(rvFmt(it["涵蓋"].end, 1))}）裡，會跟著那一筆整段換掉，選什麼都不影響結果。
      那一筆改了時間、做法或被還原，這一處會自動變回還沒確認。</p>`;
  }
  if (t === "重疊") {
    const how = rvChosen(it);
    // 10-01 1-1：還沒人選時選單停在「（請選）」，程式猜的那一位寫在旁邊（以前直接顯示成選好的樣子，其實沒存）
    const picked = it["學員已選"] ? it["學員說話者"] : "";
    const whoOpts = [`<option value="" ${picked ? "" : "selected"}>（請選）</option>`, ...rvStudents().map((n) => `<option value="${esc(n)}" ${n === picked ? "selected" : ""}>${esc(rvWho(n))}</option>`)].join("");
    const guess = it["學員猜的"] && it["學員猜的"] !== "老師"
      ? `<span class="rv-meta rv-guess">程式猜是 ${esc(rvWho(it["學員猜的"]))}</span> <button class="ghost small" id="rv-ovguess" data-who="${esc(it["學員猜的"])}">就是這一位</button>` : "";
    const ctx = (it["附近逐字稿"] || []).map((s) => `<p><b>${esc(s["說話者"])}</b> ${esc(s.text)}</p>`).join("");
    const hows = opts["重疊"].includes(how) ? opts["重疊"] : [...opts["重疊"], how];   // 舊的決定選過、現在不顯示的做法照樣列出來
    const w = it["老師整句"];
    return `<div class="rv-field rv-choices">${rvRadios("rv-ovhow", hows, how, "rv-ovhow")}</div>
      <p class="rv-warnline" id="rv-keepwarn" ${how === "不用改" ? "" : "hidden"}>「不用改」會把這一小段的學員原聲留在成品：學員只是短短附和（嗯、對），或這位學員同意保留原聲才選這個。</p>
      <p class="rv-meta">不用改＝照原樣。生成老師聲音＝老師這一整句老師重念，學員疊在上面的聲音跟著拿掉。生成學員聲音＝重疊落在學員段落裡時跟著整段重念；不在學員段落裡時，照下面「學員說的」、用選的那一位學員的聲線生成（一定要選學員是誰），換掉學員那一整句的時間，裡面老師的聲音不保留。兩邊都重新生成（照原本的時間）＝學員那句學員重念（用替代聲音）、老師那句老師重念，各自放回原本的時間，疊到的地方混在一起。消音＝這一小段靜音，老師的聲音跟著斷零點幾秒。</p>
      <div class="rv-field" id="rv-tsay-box" ${how === "只留老師" ? "" : "hidden"}>${w
        ? `<label>老師整句（${esc(rvFmt(w.start, 1))}–${esc(rvFmt(w.end, 1))}），會用老師的 AI 聲音重念這一句。重疊的地方逐字稿常常混到學員的話，請對照聲音改好：
            <textarea id="rv-tsay" rows="2">${esc(w["改稿"] || w["原文"])}</textarea></label>
            <span class="rv-meta" id="rv-tsay-st">${w["改稿"] ? RV_ST_EDITED : RV_ST_AUTO}</span>`
        : `<p class="rv-warnline">找不到這一處老師的句子，沒辦法重念；選了會照消音處理。</p>`}</div>
      <div class="rv-field" id="rv-genbox" ${how === "只留學員" && it["生成範圍"] ? "" : "hidden"}>${rvGenBoxHtml(it)}</div>
      <div id="rv-sidesbox" ${how === "兩邊都重生成" ? "" : "hidden"}>${rvSidesHtml(it)}</div>
      <div class="rv-field rv-two"><label>老師說的<textarea id="rv-tt" rows="2">${esc(it["老師文字"])}</textarea></label>
        <label>學員說的（學員是誰 <select id="rv-ovwho">${whoOpts}</select>${guess}）<textarea id="rv-st" rows="2">${esc(it["學員文字"])}</textarea></label></div>
      ${ctx ? `<details class="rv-orig"><summary>前後 3 秒的逐字稿</summary>${ctx}</details>` : ""}
      <div class="rv-field"><input id="rv-note" placeholder="備註（選填）" value="${esc(it["備註"] || "")}"></div>`;
  }
  if (t === "刪除段落" && it["來源"] === "建議") {
    return `<div class="rv-field rv-choices">${rvRadios("rv-cutdo", ["刪除", "不刪"], rvChosen(it), "rv-cutdo")}</div>
      <p class="rv-meta">剪掉＝聲音和畫面都拿掉，影片會變短。要調整起訖：按上面的「改時間」（改完就算確認剪掉）。</p>`;
  }
  const isCut = t === "刪除段落";
  const off = it["狀態"] === "還原";
  return `${isCut ? "" : `<div class="rv-field rv-choices">${rvRadios("rv-muteway", opts["消音"], it["方式"], "rv-muteway")}</div>`}
    <div class="rv-field"><button class="ghost" id="rv-toggle">${off ? (isCut ? "改回剪掉" : "改回消音") : isCut ? "還原（不剪）" : "還原（不消音）"}</button>
      <span class="rv-meta">還原的會從清單收起來，放到「設定」→「已還原的」，要的話從那裡救回</span></div>
    <div class="rv-field"><input id="rv-note" placeholder="備註（選填）" value="${esc(it["備註"] || "")}"></div>`;
}

function rvToggleMore() {
  rv.open = !rv.open;
  const m = document.getElementById("rv-more");
  if (m) m.hidden = !rv.open;
  const b = document.getElementById("rv-change");
  if (b) b.setAttribute("aria-expanded", String(rv.open));
  if (rv.open && m) { const f = m.querySelector("input, select, textarea"); if (f) f.focus({ preventScroll: true }); }
}

function rvBindMore(it) {
  const cutThis = document.getElementById("rv-cutthis");   // 09-29 宇軒：改做法裡直接刪掉這一段
  if (cutThis) cutThis.addEventListener("click", async () => {
    const [a, b] = rvCutRange(it);
    if (!confirm(`剪掉 ${rvFmt(a, 1)}–${rvFmt(b, 1)}？聲音和畫面都拿掉（影片會變短），這段就不用生成。\n只想拿掉聲音、畫面留著：用「新增修改」→ 消音。\n之後不剪了：在清單找這筆「剪掉片段」按「還原」。`)) return;
    await apiPost("/api/review/cut", { start: a, end: b, "備註": `從「${it["類型"]}」這一筆刪除` });
    await rvReload();
  });
  const q = (id) => document.getElementById(id);
  const t = it["類型"];
  document.querySelectorAll(".rv-minor").forEach((el) => el.addEventListener("change", async () => {
    await apiPost("/api/turns/save", { id: it.id, "短句保留": el.value === "學員整句生成" });
    await rvReload();
  }));
  if (t === "改成老師") {
    q("rv-who").addEventListener("change", async (e) => {
      await apiPost("/api/turns/save", { id: it.id, "說話者": e.target.value }); await rvReload();
    });
  } else if (t === "學員段落") {
    q("rv-who").addEventListener("change", async (e) => {
      await rvSaveTurnText(it);
      await apiPost("/api/turns/save", { id: it.id, "說話者": e.target.value }); await rvReload();
    });
    q("rv-merge").addEventListener("click", async () => {
      await rvSaveTurnText(it);
      try { await apiPost("/api/turns/merge", { id: it.id }); } catch (err) { alert(err.message); return; }
      await rvReload();
    });
    q("rv-split").addEventListener("mousedown", (e) => e.preventDefault());
    q("rv-split").addEventListener("click", async () => {
      const ta = q("rv-text");
      const at = ta.selectionStart;
      if (!at || at >= ta.value.length) { alert("先在逐字稿裡把游標放在要切開的地方（換人的第一個字前面）"); return; }
      await rvSaveTurnText(it);
      let sp;
      try { sp = await apiPost("/api/turns/split", { id: it.id, at }); } catch (err) { alert(err.message); return; }
      await rvReload();
      if (sp && String(sp["切在"] || "").startsWith("一句話裡面")) {   // 09-30：切在一句話裡面，切點是照逐字稿每個字的時間找的
        alert(`切在${sp["切在"]}：切點 ${rvFmt(sp["切點"], 1)}。\n前後兩段請各聽一下開頭結尾，不準的話用「改時間」調整。後面那一段如果是老師講的，按「這段其實是老師」。`);
      }
    });
    q("rv-ask").addEventListener("change", async (e) => {
      q("rv-asknote").hidden = !e.target.checked;
      const res = await apiPost("/api/turns/save", { id: it.id, "問老師": e.target.checked });
      it["問老師"] = res["段落"]["問老師"];
    });
    q("rv-asknote").addEventListener("change", (e) => { it["問老師備註"] = e.target.value; apiPost("/api/turns/save", { id: it.id, "問老師備註": e.target.value }); });
  } else if (rvIsName(t)) {
    document.querySelectorAll(".rv-namehow").forEach((el) => el.addEventListener("change", () => rvSaveName(it, { "做法": el.value })));
    document.querySelectorAll(".rv-tag").forEach((el) => el.addEventListener("change", () => rvSaveName(it, {
      tags: [...document.querySelectorAll(".rv-tag:checked")].map((x) => x.value) })));
    q("rv-note").addEventListener("change", (e) => rvSaveName(it, { note: e.target.value }));
  } else if (t === "重疊") {
    document.querySelectorAll(".rv-ovhow").forEach((el) => el.addEventListener("change", async () => {
      const was = rvChosen(it);
      q("rv-keepwarn").hidden = el.value !== "不用改";
      q("rv-sidesbox").hidden = el.value !== "兩邊都重生成";
      q("rv-tsay-box").hidden = el.value !== "只留老師";
      // 10-01：兩邊都重新生成只做「照原本的時間」（前後排開不做）
      await rvSaveOverlap(it, el.value === "兩邊都重生成" ? { "做法": el.value, "排法": "照原位置疊著" } : { "做法": el.value });
      // 10-01 第三批：選了（或取消）生成學員聲音，會換掉的範圍要後端重算
      if (el.value === "只留學員" || was === "只留學員") await rvReload();
    }));
    if (q("rv-genbox") && it["生成範圍"]) {
      const g = it["生成範圍"];
      const cur = { 0: g.start, 1: g.end };
      rvBindTimeRows("gs", (k) => cur[k], async (k, t) => {
        cur[k] = t;
        if (cur[1] - cur[0] < 0.1) { alert("結束要晚於開始"); return; }
        await rvSaveOverlap(it, { "學員起訖": [cur[0], cur[1]] });
        await rvReload();
      });
      q("rv-genplay")?.addEventListener("click", () => { rv.stopAt = g.end; rvSeek(g.start, true); });
      q("rv-genreset")?.addEventListener("click", async () => {
        if (!confirm("會換掉的範圍回到預設（學員那一整句）？")) return;
        await rvSaveOverlap(it, { "回到預設範圍": true });
        await rvReload();
      });
    }
    const tsay = q("rv-tsay");   // 09-30：選「生成老師聲音」時要重念的老師整句
    if (tsay) tsay.addEventListener("change", async () => {
      const txt = tsay.value.trim() === it["老師整句"]["原文"] ? "" : tsay.value.trim();
      await rvSaveOverlap(it, { "老師整句改稿": txt });
      it["老師整句"]["改稿"] = txt;
      q("rv-tsay-st").textContent = txt ? RV_ST_EDITED : RV_ST_AUTO;
    });
    if (q("rv-sides")) {
      const get = (k) => { const [key, i] = k.split(":"); return it[key][Number(i)]; };
      rvBindTimeRows("sd", get, async (k, t) => {
        const [key, i] = k.split(":");
        const pair = [...it[key]]; pair[Number(i)] = t;
        if (pair[1] - pair[0] < 0.1) { alert("結束要晚於開始"); return; }
        await rvSaveOverlap(it, { [key]: pair });
        it[key] = pair;
        q("rv-sidesbox").innerHTML = rvSidesHtml(it);
        rvBindMore(it);
      });
      q("rv-sideplay").addEventListener("click", () => {
        rv.stopAt = Math.max(it["老師起訖"][1], it["學員起訖"][1]);
        rvSeek(Math.min(it["老師起訖"][0], it["學員起訖"][0]), true);
      });
    }
    // 10-01 走查：被別筆涵蓋的重疊沒有這些欄位（以前這裡丟錯，時間軸不會跟著換到這一筆）
    q("rv-tt")?.addEventListener("change", (e) => rvSaveOverlap(it, { "老師文字": e.target.value }));
    q("rv-st")?.addEventListener("change", (e) => rvSaveOverlap(it, { "學員文字": e.target.value }));
    q("rv-ovwho")?.addEventListener("change", async (e) => { await rvSaveOverlap(it, { "學員說話者": e.target.value || null }); await rvReload(); });
    const guessBtn = q("rv-ovguess");
    if (guessBtn) guessBtn.addEventListener("click", async () => { await rvSaveOverlap(it, { "學員說話者": guessBtn.dataset.who }); await rvReload(); });
    q("rv-note")?.addEventListener("change", (e) => rvSaveOverlap(it, { "備註": e.target.value }));
  } else if (t === "刪除段落" && it["來源"] === "建議") {
    document.querySelectorAll(".rv-cutdo").forEach((el) => el.addEventListener("change", async () => {
      await apiPost("/api/review/cutsuggest", { id: it.id, "決定": el.value }); await rvReload();
    }));
  } else {
    const api = t === "刪除段落" ? "/api/review/cut" : "/api/review/mute";
    q("rv-toggle").addEventListener("click", async () => {
      const on = t === "刪除段落" ? "刪除" : "消音";
      await apiPost(api, { id: it.id, "狀態": it["狀態"] === "還原" ? on : "還原" }); await rvReload();
    });
    document.querySelectorAll(".rv-muteway").forEach((el) => el.addEventListener("change", () => apiPost(api, { id: it.id, "方式": el.value })));
    q("rv-note").addEventListener("change", (e) => apiPost(api, { id: it.id, "備註": e.target.value }));
  }
}

// ---------------------------------------------------------------------------
// 存檔
// ---------------------------------------------------------------------------

async function rvSaveTurnText(it) {
  const ta = document.getElementById("rv-text");
  if (!ta || it["類型"] !== "學員段落") return;
  const v = ta.value.trim();
  const base = (it["已確認"] ? it["校對稿"] : it["建議稿"]) || "";
  if (v === base.trim()) return;
  const res = await apiPost("/api/turns/save", { id: it.id, "校對稿": v });
  it["校對稿"] = it["建議稿"] = res["段落"]["校對稿"];
  it["換過的字"] = [];
}

async function rvSaveName(it, fields) {
  const res = await apiPost(it["類型"] === "學員名字" ? "/api/review/stuname" : "/api/review/name", { id: it.id, ...fields });
  Object.assign(it, { "做法": res["決定"]["做法"] || it["做法"], tags: res["決定"].tags || [], note: res["決定"].note || "" });
  rvRefreshSug();
}

async function rvSaveOverlap(it, fields) {
  try {
    const res = await apiPost("/api/review/overlap", { id: it.id, ...fields });
    Object.assign(it, res["決定"]);
    rvRefreshSug();
  } catch (err) { alert(err.message); }
}

// 10-01 第三批：人工新增的（剪掉、消音、漏抓的重疊、漏抓的名字、老師這一段 AI 重念）加錯了可以刪；刪掉的留一筆紀錄
function rvDeleteName(it) {
  const t = it["類型"];
  const label = t === "名字" && it["老師整段"] ? "老師重念（人工標的一段）" : t === "名字" ? "漏抓的老師提到名字" : t === "重疊" ? "漏抓的重疊"
    : (RV_TYPE[t] || {}).label || t;
  return `${label} ${rvFmt(it.start, 1)}–${rvFmt(it.end, 1)}`;
}

async function rvDeleteManual(it) {
  if (!confirm(`刪掉這一筆人工新增的「${rvDeleteName(it)}」？\n刪掉之後清單裡不會再有它，成品也不會照它處理；會留一筆紀錄（在「設定」→「刪掉的紀錄」看得到），但不能救回，要的話重新新增。`)) return;
  try { await apiPost("/api/review/delete", { "類型": it["類型"], id: it.id }); } catch (err) { alert(err.message); return; }
  await rvReload();
}

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

function rvPreview(it) {
  const t = it["類型"];
  if (t === "學員段落") return `${rvWho(it["說話者"])}：${(it["已確認"] ? it["校對稿"] : it["建議稿"]) || ""}`;
  if (t === "改成老師") return `老師（原聲）：${it["校對稿"] || it["原文"] || ""}`;
  if (t === "名字" && it["老師整段"]) return `老師重念：${(it["整句"] || {})["改稿"] || (it["整句"] || {})["換成代號"] || "（還沒有字）"}`;
  if (t === "名字") return (it["整句"] && (it["整句"]["換成代號"] || it["整句"]["原文"])) || it["代號"] || "";
  if (t === "學員名字") return `${rvWho(it["學員"])}講到名字：${it["代號"] || ""}`;
  if (t === "重疊") return `老師：${it["老師文字"] || "—"}／${rvWho(it["學員說話者"])}：${it["學員文字"] || "—"}`;
  // 10-01 走查：不到 10 秒的寫到小數一位（以前 0.7 秒的消音寫成「1:05:10–1:05:10」）
  const d = it.end - it.start, dg = d < 10 ? 1 : 0;
  if (t === "刪除段落") return `${rvFmt(it.start, dg)}–${rvFmt(it.end, dg)}（${d.toFixed(dg)} 秒）${it["建議類型"] ? " " + it["建議類型"] : ""}`;
  return `${rvFmt(it.start, dg)}–${rvFmt(it.end, dg)}（${d.toFixed(dg)} 秒）${it["方式"] ? " " + rvHowLabel(it["方式"]) : ""}`;
}

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
      <span class="tx">${it["第5步退回"] ? `<span class="rv-tag warn" title="${esc(it["第5步退回"].join("；"))}">第 5 步退回</span>` : ""}${it["人工新增"] ? `<span class="rv-tag">人工新增</span>` : ""}${it["代號改過"] ? `<span class="rv-tag warn">代號改過，請再看一次</span>` : ""}${esc(rvPreview(it))}</span>
      <span class="sg">${esc(sug)}</span>
      ${st}</li>`;
  }).join("");
  lower.innerHTML = `
    <nav class="rv-filters" aria-label="篩選">${RV_FILTERS.map(([f, label]) =>
      `<button class="${rv.filter === f ? "on" : ""}" data-f="${esc(f)}">${esc(label)} <span>${count(f)}</span></button>`).join("")}</nav>
    <ol class="rv-list" id="rv-list">${rows || `<li class="empty">這個篩選沒有項目。</li>`}</ol>
    ${(rv.data["已自動跳過的重疊"] || []).length ? `<p class="rv-foot">另外有 ${rv.data["已自動跳過的重疊"].length} 處重疊自動跳過（兩位學員之間、短附和、邊界誤差），在「設定」裡可以救回。</p>` : ""}`;
  lower.querySelectorAll(".rv-filters button").forEach((b) => b.addEventListener("click", () => { rv.filter = b.dataset.f; rvRenderList(); }));
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
        <button class="linkish" data-tab="${esc(k)}">${esc(label)}</button><span>${p[k] ? "✓ 做完了" : "還沒做"}</span>
        ${!p[k] && rvPrepLeft(k).length ? `<span class="rv-meta">${esc(rvPrepLeft(k).join("；"))}</span>` : ""}</li>`).join("")}</ol>
      <div class="rv-actions"><button class="primary" id="rv-start" ${rvPrepDone() ? "" : "disabled"}>開始逐筆看</button>
        ${rvPrepDone() ? "" : `<span class="rv-meta">4 件都做完才能開始</span>`}</div>
    </article>`;
  box.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { rv.prepTab = b.dataset.tab; rvRenderPrepSide(); rvRenderPrep(); }));
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
  // 09-29 宇軒：改成左右兩欄。左：照聲音特徵分出來的每一位，試聽＋選本名（最後一個選項是老師）；右：每個本名在這支影片用哪個英文代號
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
    const opts = ['<option value="">（還沒指定）</option>'].concat(codes.map((c) => `<option ${cur === c ? "selected" : ""}>${esc(c)}</option>`))
      .concat(cur && !codes.includes(cur) ? [`<option selected>${esc(cur)}</option>`] : [])
      .concat(['<option value="__new">新的代號…</option>']).join("");
    const notInRoster = !reals.includes(r);
    return `<li class="rv-person"><div class="nm"><b>${esc(r)}</b><span class="rv-meta">${esc(who)}${notInRoster ? "・名冊上沒有，選了代號會加進名冊" : ""}</span></div>
      <div class="ctl"><label>英文代號 <select class="rv-namecode" data-real="${esc(r)}">${opts}</select></label>
</div></li>`;
  }).join("") : `<li class="rv-meta">左邊選了本名之後，這裡會列出來。</li>`;
  return `${rvDupHtml()}<p class="rv-meta">左邊是照聲音特徵分出來的「學員 1、2⋯⋯」：試聽後選他的本名（最上面是這一集被叫到的名字）；聲音其實是老師的，選「${esc(teacher)}」；聽得出是另一個人、但不知道本名的，選「不知道是誰」（照樣換聲音，不用代號）。右邊是每個本名在這支影片換成哪個英文代號（只影響這一集，老師講到他的名字、學員稿子裡的名字都會照這裡換）。</p>${rvAutoHtml()}
    <div class="rv-people2"><div><h4>照聲音特徵分出來的人</h4><ul class="rv-people">${left}</ul></div>
      <div><h4>本名 → 這支影片的英文代號</h4><ul class="rv-people">${right}</ul></div></div>`;
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

function rvAutoHtml() {
  // 09-29：名冊拿掉代號欄後，每一集要自己選代號；還沒選的一鍵配常用英文名（之後可以改）
  const lack = rv.data["還沒代號"] || [];
  return `<div class="rv-actions">${lack.length ? `<span class="rv-warnline">還有 ${lack.length} 個名字這一集沒有英文代號</span>` : ""}
    <button class="ghost small" id="rv-autocode">幫還沒代號的自動配</button></div>`;
}

function rvDupHtml() {
  // 09-29：這一集的代號表（右欄＞③ 換成代號＞名冊預設）裡，兩個人用同一個代號
  const dup = Object.entries(rv.data["代號重複"] || {});
  if (!dup.length) return "";
  return `<p class="rv-warnline">同一集有人用了同一個代號，成品裡會分不出是誰：${dup.map(([c, rs]) => `${esc(c)}（${esc(rs.join("、"))}）`).join("；")}。如果其實是同一個人（轉錯字、暱稱），不用改，或在「③」選「是上面的學員」合併；不同人的話，學員在「② 辨識學員聲音是誰」右欄改，其他人在「③ 辨識其他名稱如何替換」改。</p>`;
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
    const codeOpts = ['<option value="">選代號</option>'].concat(codes.map((c) => `<option ${u["代號"] === c ? "selected" : ""}>${esc(c)}</option>`))
      .concat(u["代號"] && !codes.includes(u["代號"]) ? [`<option selected>${esc(u["代號"])}</option>`] : [])
      .concat(['<option value="__new">新的代號…</option>']).join("");
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
        ${u["已決定"] ? "" : u["做法"] ? `<span class="rv-meta">（建議，還沒確認）</span>${confirmBtn}` : '<span class="rv-warnline">還沒決定</span>'}</div>
      ${u["說明"] ? `<p class="rv-meta">${esc(u["說明"])}</p>` : ""}</li>`;
  }).join("");
  const twoBlock = fromTwo.length ? `<li class="rv-person"><details><summary class="rv-meta">② 已經定好的學員（${fromTwo.length} 個）：${fromTwo.map((u) => `${esc(u["名字"])} → ${esc(u["代號"] || "還沒選代號")}`).join("、")}</summary>
      <p class="rv-meta">被提到時一樣換成這個代號；要改代號回「② 辨識學員聲音是誰」右欄。</p></details></li>` : "";
  const cutBlock = cutOnly.length ? `<li class="rv-person"><details><summary class="rv-meta">只出現在確認刪除的段落裡（${cutOnly.length} 個：${esc(cutOnly.map((u) => u["名字"]).join("、"))}），不用處理</summary>
      <p class="rv-meta">這些段落會整段刪掉。如果在「① 建議刪除段落」改成不刪，會回到上面。</p></details></li>` : "";
  if (!all.length) return `<p class="rv-meta">這一集沒有找到被提到的人名（第 1 步人名清單還沒跑，或真的沒有）。</p>`;
  return `${rvDupHtml()}${rvAutoHtml()}<p class="rv-meta">這一集被提到的所有人名（老師或學員講到的），每個決定被提到時換成什麼：
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
  const askCode = (el) => {   // 「新的代號…」：自己打一個英文代號
    if (el.value !== "__new") return el.value || null;
    const v = (prompt("輸入新的英文代號（例如 Grace）") || "").trim();
    if (!v) { el.value = ""; return undefined; }
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
  const auto = root.querySelector("#rv-autocode");
  if (auto) auto.addEventListener("click", async () => {
    try {
      const r = await apiPost("/api/codes/auto", {});
      alert(`配好了：② ${r["②"]} 位、③ ${r["③"]} 個。${r["代號不夠"] ? "常用英文名不夠用，剩下的請自己打新代號。" : "不喜歡的直接改。"}`);
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

function rvAlignLine(it) {   // 「你標的 → 對齊後」（人工新增、改過時間的才有）
  const m = it["標的起訖"];
  if (!m) return "";
  const ok = it["對齊"] || [];
  const note = it["對齊到"] === "照填的時間" ? "照你填的時間，沒有自動對齊"   // 10-01 第三批：第 5 步改的
    : ok[0] && ok[1] ? `對齊${it["對齊到"] || ""}` : !ok[0] && !ok[1] ? "沒對齊，保留你標的時間" : `${ok[0] ? "終點" : "起點"}沒對齊`;
  return `<p class="rv-meta">你標的 ${esc(rvFmt(m[0], 2))}–${esc(rvFmt(m[1], 2))} → ${esc(rvFmt(it.start, 2))}–${esc(rvFmt(it.end, 2))}（${esc(note)}）</p>`;
}

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

function teTimeRow(ctx, which, label) {
  const t = ctx.st[which];
  return `<div class="rv-edrow"><span class="rv-edlab">${label}</span>
      <input class="rv-t" data-te-t="${which}" value="${t != null ? esc(rvFmt(t, 2)) : ""}" placeholder="43:15.2" aria-label="${label}（例如 43:15.2）">
      ${ctx.o.now ? `<button class="ghost small" data-te-now="${which}" title="${which === "a" ? "快捷鍵 I" : "快捷鍵 O"}">${esc(ctx.o.nowLabel || "用目前播放位置")}</button>` : ""}
      <button class="ghost small" data-te-nudge="${which}" data-d="-0.1" aria-label="${label}往前 0.1 秒">−0.1</button>
      <button class="ghost small" data-te-nudge="${which}" data-d="0.1" aria-label="${label}往後 0.1 秒">＋0.1</button></div>`;
}

function teResultHtml(res) {
  if (!res) return "";
  if (res.error) return `<p class="rv-warnline">${esc(res.error)}</p>`;
  if (res["不對齊"]) return `<p class="rv-edres">✓ 改好了：<b>${esc(rvFmt(res.start, 2))}–${esc(rvFmt(res.end, 2))}</b>（照你填的時間）</p>`;
  return `<p class="rv-edres">✓ ${res["新增"] ? "新增了" : "改好了"}。你標的 ${esc(rvFmt(res["標的起訖"][0], 2))}–${esc(rvFmt(res["標的起訖"][1], 2))}
       → 對齊後 <b>${esc(rvFmt(res.start, 3))}–${esc(rvFmt(res.end, 3))}</b>
       ${res["對齊"][0] && res["對齊"][1] ? `（對齊${esc(res["對齊到"])}）`
         : `<span class="rv-warn">（${!res["對齊"][0] && !res["對齊"][1] ? "沒對齊" : res["對齊"][0] ? "終點沒對齊" : "起點沒對齊"}：附近找不到${esc(res["對齊到"])}，保留你標的時間）</span>`}</p>`;
}

function teRender(ctx) {
  const el = document.getElementById(ctx.host);
  if (!el) return;
  const { st, o } = ctx;
  const extra = o.extra ? o.extra(st) : "";
  el.innerHTML = `<section class="rv-editor" aria-label="${esc(o.aria || "改起點終點")}">
      <div class="rv-edrow">${o.head(st)}<span class="spacer"></span>${o.close ? `<button class="ghost small" data-te-close>收起來</button>` : ""}</div>
      ${teTimeRow(ctx, "a", "起點")}
      ${teTimeRow(ctx, "b", "終點")}
      ${extra ? `<div class="rv-edrow">${extra}</div>` : ""}
      <p class="rv-meta">${esc(o.hint ? o.hint(st) : "")}</p>
      <div class="rv-edrow"><button class="ghost" data-te-play>試聽這段</button>
        <button class="primary" data-te-save>${st.busy ? "對齊中…" : o.saveLabel(st)}</button>
        <span class="rv-meta" data-te-dur></span></div>
      <div data-te-res>${teResultHtml(st.result)}</div></section>`;
  const q = (sel) => el.querySelector(sel);
  if (o.close) q("[data-te-close]").addEventListener("click", o.close);
  if (o.bindHead) o.bindHead(el, st, ctx);
  for (const w of ["a", "b"]) {
    const box = q(`[data-te-t="${w}"]`);
    const take = () => {   // 只更新數值，不重畫面板（重畫會把正在失去焦點的輸入框拿掉）
      if (!box.value.trim()) { teSet(ctx, w, null, false); return; }
      const t = rvParseTime(box.value);
      box.classList.toggle("bad", t == null);
      if (t != null) teSet(ctx, w, t, false);
    };
    box.addEventListener("change", take);
    box.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); e.stopPropagation(); take(); } });
  }
  el.querySelectorAll("[data-te-now]").forEach((b) => b.addEventListener("click", () => teSet(ctx, b.dataset.teNow, o.now())));
  el.querySelectorAll("[data-te-nudge]").forEach((b) => b.addEventListener("click", () => {
    const w = b.dataset.teNudge;
    if (st[w] == null) return;
    teSet(ctx, w, Math.max(0, Math.round((st[w] + Number(b.dataset.d)) * 100) / 100));
  }));
  q("[data-te-play]").addEventListener("click", () => { if (teOk(st)) o.play(st.a, st.b); });
  q("[data-te-save]").addEventListener("click", () => teSave(ctx));
  teRefresh(ctx);
}

function teOk(st) { return st.a != null && st.b != null && st.b > st.a; }

function teRefresh(ctx) {   // 起點終點改了：更新輸入框、按鈕、長度，不重畫整個面板
  const el = document.getElementById(ctx.host);
  if (!el) return;
  const st = ctx.st, ok = teOk(st);
  for (const w of ["a", "b"]) {
    const box = el.querySelector(`[data-te-t="${w}"]`);
    if (box && document.activeElement !== box) box.value = st[w] != null ? rvFmt(st[w], 2) : "";
  }
  const play = el.querySelector("[data-te-play]"), save = el.querySelector("[data-te-save]");
  if (play) play.disabled = !ok;
  if (save) save.disabled = !ok || st.busy;
  const dur = el.querySelector("[data-te-dur]");
  if (dur) dur.textContent = ok ? `共 ${(st.b - st.a).toFixed(1)} 秒` : "起點、終點都填好（終點晚於起點）才能存";
}

function teSet(ctx, which, t, fromButton = true) {
  const st = ctx.st;
  if (t == null && fromButton) return;
  st[which] = t;
  const el = document.getElementById(ctx.host);
  if (st.result) { st.result = null; const r = el && el.querySelector("[data-te-res]"); if (r) r.innerHTML = ""; }
  if (fromButton && el) { const box = el.querySelector(`[data-te-t="${which}"]`); if (box) { box.value = rvFmt(t, 2); box.classList.remove("bad"); } }
  teRefresh(ctx);
  if (ctx.o.onChange) ctx.o.onChange();
}

async function teSave(ctx) {
  const { st, o } = ctx;
  if (!teOk(st)) return;
  const body = o.body(st);
  if (!body) { teRender(ctx); return; }
  st.busy = true;
  teRender(ctx);
  let r;
  try { r = await apiPost(o.api || "/api/review/manual", body); } catch (err) {
    st.busy = false; st.result = { error: err.message }; teRender(ctx); return;
  }
  st.busy = false;
  const res = o.toResult ? o.toResult(r, st) : { ...r["對齊結果"], "新增": r["新增"] };
  await o.saved(r, res);
}

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
      const code = el.querySelector("#rv-ed-code"); if (code) code.addEventListener("change", () => { ed.code = code.value; });
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
        return `<label>換成代號 <select id="rv-ed-code"><option value="">（選一個）</option>${codes.map((c) => `<option ${c === ed.code ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></label>
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
      Object.assign(rv.ed, { id: null, label: null, a: null, b: null, result: res, word: "", code: "" });   // 代號每筆重選，免得沿用上一筆
      await rvReload();
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
      <p class="rv-meta">兩位學員之間的重疊、老師講話時學員的短附和、不到 0.05 秒的邊界誤差，自動不處理。覺得要處理的按「救回」。</p>
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
    msg.innerHTML = `${warn ? `<span class="rv-warnline">${warn}</span> ` : ""}已匯出：<code>${esc(r["檔案"])}</code>（<a href="/api/review/export.zip">下載</a>）`;
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
