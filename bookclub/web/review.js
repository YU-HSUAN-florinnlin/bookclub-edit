"use strict";

/* 第 3 步「覆核工作台」（09-26 改版：邊看影片邊逐筆通過）。
 * 版面：頂端一行（進度、花了多少時間、推算整支要多久、開始前 3 件事、設定、快捷鍵、匯出）
 *   → 上半部左欄：影片、時間列、整支時間軸（老師灰、學員綠、冥想導讀淡藍；名字、重疊、刪除標記）、圖例
 *     上半部右欄：「目前這一筆」卡片（內容、藍底建議＋原因、通過／改做法／上一筆／下一筆）
 *   → 下半部：篩選列＋一行一筆的清單。
 * 一進來先做「開始前 3 件事」（學員是誰、誰保留原聲、建議刪除段落），做完按「開始逐筆看」才進清單。
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
  "刪除段落": { cls: "cut", label: "刪除段落" },
  "局部消音": { cls: "mute", label: "局部消音" },
};
const RV_FILTERS = [["全部", "全部"], ["還沒確認", "還沒確認"], ["學員段落", "學員段落"], ["名字", "名字"],
  ["學員名字", "學員提到名字"], ["重疊", "重疊"], ["刪除段落", "建議刪除"]];
const rvIsName = (t) => t === "名字" || t === "學員名字";
const RV_STU_SHADES = ["#3f8f5a", "#6aae7f", "#2d6b43", "#8cc49d", "#4f9d6b", "#1f5434"];

const rv = {
  data: null, video: null, cur: null, filter: "全部", open: false, prepOpen: false, prepTab: "學員",
  lastT: 0, hold: null, lastActivity: Date.now(), timeTimer: null, splitOpen: null, stopAt: null,
  ed: { open: false, kind: "刪除段落", id: null, a: null, b: null, busy: false, result: null },
};

// 「新增修改」的類型（後端 review.MANUAL_KINDS）與對齊規則的說明（bookclub/align.py）
const RV_KINDS = [["刪除段落", "刪除段落"], ["局部消音", "局部消音"], ["學員發言", "漏抓的學員發言"],
  ["名字", "漏抓的「老師提到名字」"], ["重疊", "漏抓的重疊"]];
const RV_RULE_HINT = {
  "刪除段落": "按新增後，起點終點各自對齊附近 0.5 秒內的安靜處（不切在字中間）",
  "局部消音": "按新增後，起點終點各自對齊附近 0.5 秒內的安靜處",
  "學員發言": "按新增後，對齊句子的開頭、結尾（1 秒內）",
  "名字": "按新增後，對齊逐字稿裡字的時間，前後留一點停頓",
  "重疊": "按新增後，對齊句子的開頭、結尾（1 秒內）",
};
const RV_ITEM_KIND = { "學員段落": "學員發言", "名字": "名字", "重疊": "重疊", "刪除段落": "刪除段落", "局部消音": "局部消音" };

function rvKey(it) { return `${it["類型"]}:${it.id}`; }
function rvItem(key) { return rv.data["項目"].find((x) => rvKey(x) === key); }
function rvItems() { return rv.data["項目"]; }
function rvDone(it) { return !!(it["已確認"] || it["不用處理"]); }

function rvFmt(sec, digits = 0) {
  if (sec == null || isNaN(sec)) return "—";
  sec = Math.max(0, sec);
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
  return !!(p["學員"] && p["保留原聲"] && p["刪除"]);
}

// ---------------------------------------------------------------------------
// 進入點
// ---------------------------------------------------------------------------

async function renderReview() {
  contentEl.innerHTML = "<p>載入中…</p>";
  rv.data = await apiGet("/api/review");
  rv.prepOpen = !rvPrepDone();
  const v = rv.data["影片"];
  contentEl.classList.add("wide");
  contentEl.innerHTML = `
    <div class="rv2">
      <header class="rv-bar">
        <h1>3　覆核工作台</h1>
        <div class="rv-progress" id="rv-progress"></div>
        <div class="rv-bar-btns">
          <button class="ghost" id="rv-prep-btn">開始前 3 件事</button>
          <button class="ghost" id="rv-set-btn" aria-expanded="false">設定</button>
          <button class="ghost" id="rv-key-btn" aria-expanded="false">快捷鍵</button>
          <button class="ghost" id="rv-export">匯出覆核結果</button>
          <button class="primary" id="rv-go4" disabled>全部通過，開始 AI 修改</button>
        </div>
      </header>
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
    if (e.key !== "Enter") return;
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
}

function rvRenderMain() {
  document.getElementById("rv-prep-btn").classList.toggle("on", rv.prepOpen);
  if (rv.prepOpen) { rvRenderPrepSide(); rvRenderPrep(); return; }
  rvRenderCard();
  rvRenderList();
}

async function rvReload() {
  rv.data = await apiGet("/api/review");
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
    const cls = x["冥想導讀"] ? "calm" : x["說話者"] === "老師" ? "teacher" : "stu";
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
    <span class="gap"><i class="sw name"></i>名字</span><span><i class="sw ov"></i>重疊</span><span><i class="sw cut"></i>刪除</span>`;
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

function rvConfirmGo4() {
  if (rvLeft()) return;
  if (rv.video && !rv.video.paused) rv.video.pause();
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
      await apiPost("/api/execute/start", { start: null, end: null, methods: null });
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

function rvChip(it) {
  const t = RV_TYPE[it["類型"]] || { cls: "", label: it["類型"] };
  return `<span class="rv-chip ${t.cls}"><i></i>${esc(t.label)}</span>`;
}

function rvSuggestText(it) {
  const s = it["建議"] || {};
  if (it["類型"] === "重疊" && s["做法"] === "兩邊都重生成" && s["排法"]) return `${s["做法"]}、${s["排法"]}`;
  if (it["類型"] === "學員段落") return "逐字稿沒問題就通過";
  return s["做法"] || "—";
}

function rvChosen(it) {   // 現在會套用的做法：人改過的，或建議
  const t = it["類型"];
  if (t === "重疊") return it["做法"] || (it["建議"] || {})["做法"];
  if (rvIsName(t)) return it["做法"];
  if (t === "刪除段落") return it["來源"] === "建議" ? (it["決定"] || "刪除") : (it["狀態"] === "還原" ? "不刪" : "刪除");
  if (t === "局部消音") return it["方式"];
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
  const state = it["不用處理"] ? `<span class="rv-state skip">不用處理：${esc(it["不用處理"])}</span>`
    : it["已確認"] ? `<span class="rv-state ok">✓ 已通過</span>` : "";
  const sug = it["建議"] || {};
  const chosen = rvChosen(it);
  const changed = chosen && sug["做法"] && chosen !== sug["做法"] && it["類型"] !== "學員段落";
  box.innerHTML = `
    <article class="rv-card" data-key="${esc(rv.cur)}">
      <header>
        ${rvChip(it)}${it["人工新增"] ? `<span class="rv-tag">人工新增</span>` : ""}
        <span class="rv-when">${esc(rvFmt(it.start, 1))}–${esc(rvFmt(it.end, 1))}</span>
        <span class="rv-count">第 ${idx + 1}／${all.length} 筆</span>
      </header>
      <div class="rv-body">${rvBodyHtml(it)}${rvAlignLine(it)}</div>
      <div class="rv-sug">
        <p class="what">建議：${esc(rvSuggestText(it))}</p>
        <p class="why">${esc(sug["原因"] || "")}</p>
        ${changed ? `<p class="mine">改成：${esc(chosen)}</p>` : ""}
      </div>
      ${state ? `<div class="rv-statebox">${state}</div>` : ""}
      <div class="rv-actions">
        <button class="primary" id="rv-pass" title="已通過的再按一次會取消">${it["已確認"] ? "已通過" : "通過"}<kbd>Enter</kbd></button>
        <button class="ghost" id="rv-change" aria-expanded="${rv.open}">改做法<kbd>E</kbd></button>
        <button class="ghost" id="rv-retime" title="用「新增修改」面板改這一筆的起點終點">改時間</button>
        ${it["類型"] === "學員段落" ? `<button class="ghost" id="rv-isteacher" title="聲音辨識判錯：這一段其實是老師在講話">這段其實是老師</button>` : ""}
        <span class="spacer"></span>
        <button class="ghost" id="rv-prev" aria-label="上一筆">上一筆</button>
        <button class="ghost" id="rv-next" aria-label="下一筆">下一筆</button>
      </div>
      <div class="rv-more" id="rv-more" ${rv.open ? "" : "hidden"}>${rvMoreHtml(it)}</div>
    </article>`;
  document.getElementById("rv-pass").addEventListener("click", () => rvPass());
  document.getElementById("rv-change").addEventListener("click", rvToggleMore);
  document.getElementById("rv-retime").addEventListener("click", () => rvOpenEditor(it));
  const isT = document.getElementById("rv-isteacher");
  if (isT) isT.addEventListener("click", async () => {
    // 09-29 宇軒：聲音辨識把老師判成學員時，一鍵改回老師（只有一部分是老師：先用「改做法」裡的「從游標處切開」）
    if (!confirm(`把 ${rvFmt(it.start, 1)}–${rvFmt(it.end, 1)} 這一段改成老師？\n改了之後這段不會重念，也會補找這段裡老師提到的名字。\n只有一部分是老師的話，先按「改做法」→「從游標處切開」。`)) return;
    await rvSaveTurnText(it);
    const res = await apiPost("/api/turns/save", { id: it.id, "說話者": "老師" });
    if (res["補找到的老師名字"]) alert(`這段裡補找到 ${res["補找到的老師名字"]} 個老師提到的名字，已經加進清單。`);
    await rvReload();
  });
  document.getElementById("rv-prev").addEventListener("click", () => rvStep(-1));
  document.getElementById("rv-next").addEventListener("click", () => rvStep(1));
  rvBindBody(it);
  rvBindMore(it);
  rvRenderTimeline();
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
      ${it["含本名"] ? `<p class="rv-warnline">文字裡還有名冊上的本名，要換成代號。</p>` : ""}
      ${it["問老師"] ? `<p class="rv-note">已標「聽不清楚，問老師」${it["問老師備註"] ? "：" + esc(it["問老師備註"]) : ""}</p>` : ""}`;
  }
  if (t === "學員名字") {
    const w = it["整句"];
    return `<p class="rv-who">${esc(rvWho(it["學員"]))}（保留原聲）講到名字</p><p class="rv-quote">${it.sentence_html}</p>
      ${w ? `<p class="rv-note">選「換成代號」時，用${esc(rvWho(it["學員"]))}自己的聲音重念這句：${esc(w["換成代號"] || w["原文"])}${w["換成代號"] ? "" : "（句子裡找不到比對到的字，會退回直接消音）"}</p>` : ""}
      <p class="rv-meta">代號 ${esc(it["代號"] || "（沒有）")}${it["信心"] === "低" ? "　低信心，先聽清楚是不是名字" : ""}</p>`;
  }
  if (t === "名字") {
    const w = it["整句"];
    return `<p class="rv-quote">${it.sentence_html}</p>
      ${w ? `<p class="rv-note">整句換掉後：${esc(w["換成代號"] || w["原文"])}${w["換成代號"] ? "" : "（句子裡找不到比對到的字，要人處理）"}</p>` : ""}
      <p class="rv-meta">代號 ${esc(it["代號"] || "（沒有）")}${it["信心"] === "低" ? "　低信心，先聽清楚是不是名字" : ""}</p>`;
  }
  if (t === "重疊") {
    return `<dl class="rv-pair"><dt>老師</dt><dd>${esc(it["老師文字"] || "（聽不出來）")}</dd>
      <dt>${esc(rvWho(it["學員說話者"]))}</dt><dd>${esc(it["學員文字"] || "（聽不出來）")}</dd></dl>
      <p class="rv-meta">重疊 ${Number(it.length || it.end - it.start).toFixed(1)} 秒</p>`;
  }
  if (t === "刪除段落") {
    return `<p class="rv-quote">${esc(rvFmt(it.start, 1))} 到 ${esc(rvFmt(it.end, 1))}，共 ${(it.end - it.start).toFixed(1)} 秒，聲音畫面一起刪</p>
      <p class="rv-meta">${it["來源"] === "建議" ? `影片分析找到的（${esc(it["建議類型"] || "")}）` : "人手動加的"}</p>`;
  }
  return `<p class="rv-quote">${esc(rvFmt(it.start, 1))} 到 ${esc(rvFmt(it.end, 1))} 只消聲音、畫面保留（${esc(it["方式"] || "")}）</p>`;
}

function rvBindBody(it) {
  const ta = document.getElementById("rv-text");
  if (ta) ta.addEventListener("blur", () => rvSaveTurnText(it));
}

function rvRadios(name, options, current, cls) {
  return options.map((o) => `<label class="rv-radio"><input type="radio" name="${esc(name)}" class="${cls}" value="${esc(o)}" ${o === current ? "checked" : ""}> ${esc(o)}</label>`).join("");
}

function rvMoreHtml(it) {
  const t = it["類型"];
  const opts = rv.data["選項"];
  if (t === "學員段落") {
    const whoOpts = ["老師", ...rvStudents(), "新學員"].map((n) => `<option value="${esc(n)}" ${n === it["說話者"] ? "selected" : ""}>${esc(n === "新學員" ? "新的一位學員" : rvWho(n))}</option>`).join("");
    return `<div class="rv-field"><label>說話者 <select id="rv-who">${whoOpts}</select></label></div>
      <div class="rv-field rv-row">
        <button class="ghost" id="rv-merge">併進上一段</button>
        <button class="ghost" id="rv-split">從游標處切開</button>
      </div>
      <div class="rv-field"><label class="rv-check"><input type="checkbox" id="rv-ask" ${it["問老師"] ? "checked" : ""}> 聽不清楚，問老師</label>
        <input id="rv-asknote" placeholder="要問老師什麼" value="${esc(it["問老師備註"] || "")}" ${it["問老師"] ? "" : "hidden"}></div>
      ${it["原文"] !== it["校對稿"] ? `<details class="rv-orig"><summary>看原本轉出來的文字</summary>${esc(it["原文"])}</details>` : ""}`;
  }
  if (rvIsName(t)) {
    const tags = opts["名字標記"].map((g) => `<label class="rv-check"><input type="checkbox" class="rv-tag" value="${esc(g)}" ${it.tags.includes(g) ? "checked" : ""}> ${esc(g)}</label>`).join("");
    return `<div class="rv-field rv-choices">${rvRadios("rv-namehow", opts[t], it["做法"], "rv-namehow")}</div>
      <div class="rv-field rv-choices">${tags}</div>
      <div class="rv-field"><input id="rv-note" placeholder="備註（選填）" value="${esc(it.note || "")}"></div>`;
  }
  if (t === "重疊") {
    const how = rvChosen(it);
    const whoOpts = ['<option value="">（不知道是誰）</option>', ...rvStudents().map((n) => `<option value="${esc(n)}" ${n === it["學員說話者"] ? "selected" : ""}>${esc(rvWho(n))}</option>`)].join("");
    const ctx = (it["附近逐字稿"] || []).map((s) => `<p><b>${esc(s["說話者"])}</b> ${esc(s.text)}</p>`).join("");
    return `<div class="rv-field rv-choices">${rvRadios("rv-ovhow", opts["重疊"], how, "rv-ovhow")}</div>
      <p class="rv-warnline" id="rv-keepwarn" ${how === "不用改" ? "" : "hidden"}>「不用改」會保留原聲：只有同意保留原聲的學員才選這個。</p>
      <div class="rv-field rv-choices" id="rv-arr" ${how === "兩邊都重生成" ? "" : "hidden"}>兩邊都重生成時：${rvRadios("rv-ar", opts["重疊排法"], it["排法"] || (it["建議"] || {})["排法"] || "前後排開", "rv-ar")}</div>
      <div class="rv-field rv-two"><label>老師說的<textarea id="rv-tt" rows="2">${esc(it["老師文字"])}</textarea></label>
        <label>學員說的（<select id="rv-ovwho">${whoOpts}</select>）<textarea id="rv-st" rows="2">${esc(it["學員文字"])}</textarea></label></div>
      ${ctx ? `<details class="rv-orig"><summary>前後 3 秒的逐字稿</summary>${ctx}</details>` : ""}
      <div class="rv-field"><input id="rv-note" placeholder="備註（選填）" value="${esc(it["備註"] || "")}"></div>`;
  }
  if (t === "刪除段落" && it["來源"] === "建議") {
    return `<div class="rv-field rv-choices">${rvRadios("rv-cutdo", ["刪除", "不刪"], rvChosen(it), "rv-cutdo")}</div>
      <p class="rv-meta">要調整起訖：按上面的「改時間」（改完就算確認刪除）。</p>`;
  }
  const isCut = t === "刪除段落";
  const off = it["狀態"] === "還原";
  return `${isCut ? "" : `<div class="rv-field rv-choices">${rvRadios("rv-muteway", opts["消音"], it["方式"], "rv-muteway")}</div>`}
    <div class="rv-field"><button class="ghost" id="rv-toggle">${off ? (isCut ? "改回刪除" : "改回消音") : "還原（不處理）"}</button></div>
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
  const q = (id) => document.getElementById(id);
  const t = it["類型"];
  if (t === "學員段落") {
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
      try { await apiPost("/api/turns/split", { id: it.id, at }); } catch (err) { alert(err.message); return; }
      await rvReload();
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
      q("rv-keepwarn").hidden = el.value !== "不用改";
      q("rv-arr").hidden = el.value !== "兩邊都重生成";
      await rvSaveOverlap(it, { "做法": el.value });
    }));
    document.querySelectorAll(".rv-ar").forEach((el) => el.addEventListener("change", () => rvSaveOverlap(it, { "排法": el.value })));
    q("rv-tt").addEventListener("change", (e) => rvSaveOverlap(it, { "老師文字": e.target.value }));
    q("rv-st").addEventListener("change", (e) => rvSaveOverlap(it, { "學員文字": e.target.value }));
    q("rv-ovwho").addEventListener("change", (e) => rvSaveOverlap(it, { "學員說話者": e.target.value || null }));
    q("rv-note").addEventListener("change", (e) => rvSaveOverlap(it, { "備註": e.target.value }));
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

function rvRefreshSug() {   // 改了做法：只更新建議框下面的「改成」那一行，不重畫整張卡（展開的選項保持開著）
  const it = rvItem(rv.cur);
  const box = document.querySelector(".rv-sug");
  if (!it || !box) return;
  const chosen = rvChosen(it), sug = (it["建議"] || {})["做法"];
  let mine = box.querySelector(".mine");
  if (chosen && sug && chosen !== sug) {
    if (!mine) { mine = document.createElement("p"); mine.className = "mine"; box.appendChild(mine); }
    mine.textContent = `改成：${chosen}`;
  } else if (mine) mine.remove();
  rvRenderList();
}

async function rvPass() {
  const it = rv.cur && rvItem(rv.cur);
  if (!it) return;
  const t = it["類型"];
  const undo = !!it["已確認"];
  try {
    if (t === "學員段落") {
      const ta = document.getElementById("rv-text");
      const text = ta ? ta.value.trim() : it["校對稿"];
      await apiPost("/api/turns/save", { id: it.id, "校對稿": text, "已確認": !undo });
      it["校對稿"] = it["建議稿"] = text;
      it["換過的字"] = [];
    } else if (rvIsName(t)) {
      await apiPost(t === "名字" ? "/api/review/name" : "/api/review/stuname", { id: it.id, "做法": it["做法"], "已確認": !undo });
    } else if (t === "重疊") {
      const how = rvChosen(it);
      const ar = it["排法"] || (it["建議"] || {})["排法"] || "前後排開";
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
  if (t === "名字") return (it["整句"] && (it["整句"]["換成代號"] || it["整句"]["原文"])) || it["代號"] || "";
  if (t === "學員名字") return `${rvWho(it["學員"])}講到名字：${it["代號"] || ""}`;
  if (t === "重疊") return `老師：${it["老師文字"] || "—"}／${rvWho(it["學員說話者"])}：${it["學員文字"] || "—"}`;
  if (t === "刪除段落") return `${rvFmt(it.start)}–${rvFmt(it.end)}（${(it.end - it.start).toFixed(0)} 秒）${it["建議類型"] ? " " + it["建議類型"] : ""}`;
  return `${rvFmt(it.start)}–${rvFmt(it.end)} ${it["方式"] || ""}`;
}

function rvRenderList() {
  const lower = document.getElementById("rv-lower");
  const items = rvItems();
  const count = (f) => f === "全部" ? items.length : f === "還沒確認" ? items.filter((x) => !rvDone(x)).length
    : items.filter((x) => x["類型"] === f).length;
  const rows = rvVisible().map((it) => {
    const key = rvKey(it);
    const st = key === rv.cur ? `<span class="st now">目前</span>` : it["不用處理"] ? `<span class="st skip">不用處理</span>`
      : it["已確認"] ? `<span class="st ok">✓ 通過</span>` : `<span class="st">—</span>`;
    const chosen = rvChosen(it);
    const sug = it["類型"] === "學員段落" ? "通過" : chosen || rvSuggestText(it);
    return `<li class="${key === rv.cur ? "cur" : ""} ${rvDone(it) ? "done" : ""}" data-key="${esc(key)}" tabindex="-1">
      <span class="tm">${esc(rvFmt(it.start))}</span>
      <span class="ty">${rvChip(it)}</span>
      <span class="tx">${it["人工新增"] ? `<span class="rv-tag">人工新增</span>` : ""}${esc(rvPreview(it))}</span>
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
// 開始前 3 件事
// ---------------------------------------------------------------------------

const RV_PREP = [["學員", "① 學員是誰"], ["保留原聲", "② 誰保留原聲"], ["刪除", "③ 建議刪除段落"]];

function rvRenderPrepSide() {
  const p = rv.data["開始前確認"];
  const box = document.getElementById("rv-right");
  box.innerHTML = `<article class="rv-card rv-prepside">
      <header><span class="rv-chip"><i></i>開始前 3 件事</span></header>
      <p class="rv-meta">先把整體定下來，逐筆看的時候就不用再想：學員換成誰、誰不用重念、哪些段落整段刪掉。</p>
      <ol class="rv-prepsteps">${RV_PREP.map(([k, label]) => `<li class="${p[k] ? "ok" : ""} ${rv.prepTab === k ? "on" : ""}">
        <button class="linkish" data-tab="${esc(k)}">${esc(label)}</button><span>${p[k] ? "✓ 做完了" : "還沒做"}</span></li>`).join("")}</ol>
      <div class="rv-actions"><button class="primary" id="rv-start" ${rvPrepDone() ? "" : "disabled"}>開始逐筆看</button>
        ${rvPrepDone() ? "" : `<span class="rv-meta">3 件都做完才能開始</span>`}</div>
    </article>`;
  box.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { rv.prepTab = b.dataset.tab; rvRenderPrepSide(); rvRenderPrep(); }));
  document.getElementById("rv-start").addEventListener("click", () => {
    rv.prepOpen = false;
    if (!rv.cur || rvDone(rvItem(rv.cur) || {})) rv.cur = rvFirstPending();
    rvRenderMain();
    if (rv.cur) rvSelect(rv.cur);
  });
}

function rvRenderPrep() {
  const lower = document.getElementById("rv-lower");
  const k = rv.prepTab;
  const done = rv.data["開始前確認"][k];
  let body = "";
  if (k === "學員") body = rvPrepPeopleHtml();
  else if (k === "保留原聲") body = rvPrepVoiceHtml();
  else body = rvPrepCutHtml();
  const label = RV_PREP.find((x) => x[0] === k)[1];
  lower.innerHTML = `<section class="rv-prep">
      <h2>${esc(label)}</h2>${body}
      <div class="rv-actions"><button class="primary" id="rv-prepdone">${done ? "✓ 這一件做完了（再按一次改回還沒做）" : "這一件做完了"}</button></div>
    </section>`;
  document.getElementById("rv-prepdone").addEventListener("click", async () => {
    const r = await apiPost("/api/review/prep", { "項目": k, "完成": !done });
    rv.data["開始前確認"] = r["開始前確認"];
    if (!done) { const i = RV_PREP.findIndex((x) => x[0] === k); const next = RV_PREP.slice(i + 1).concat(RV_PREP).find((x) => !rv.data["開始前確認"][x[0]]); if (next) rv.prepTab = next[0]; }
    rvRenderPrepSide(); rvRenderPrep();
  });
  rvBindPrep(lower);
}

function rvSegsOf(who) { return rvItems().filter((x) => x["類型"] === "學員段落" && x["說話者"] === who); }

function rvPrepPeopleHtml() {
  // 09-29 宇軒：改成左右兩欄。左：聲紋分出來的每一位，試聽＋選本名（最後一個選項是老師）；右：每個本名在這支影片用哪個英文代號
  const people = Object.entries(rv.data["學員"] || {});
  if (!people.length) return `<p class="rv-meta">${rv.data["有段落"] ? "這支影片沒有學員段落。" : "還沒有段落分析結果：先跑完第 1 步影片分析。"}</p>`;
  const tp = rv.data["學員資料"] || {};
  const codes = rv.data["代號選項"] || [];
  const reals = tp["本名選項"] || [];
  const teacher = tp["老師名稱"] || "老師";
  const rosterCode = tp["名冊代號"] || {};
  const nameCode = tp["本名代號"] || {};
  const left = people.map(([n, p]) => {
    const others = people.filter(([m]) => m !== n).map(([m]) => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
    const realOpts = ['<option value="">（還沒指定）</option>']
      .concat(reals.map((r) => `<option value="${esc(r)}" ${p["本名"] === r ? "selected" : ""}>${esc(r)}</option>`))
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
      </div>
      <details class="rv-more-ctl"><summary>其他（合併、拆開）</summary><div class="ctl">
        ${others ? `<label>跟誰是同一人 <select class="rv-mergeto" data-person="${esc(n)}"><option value="">—</option>${others}</select></label>` : ""}
        <button class="ghost small rv-splitopen" data-person="${esc(n)}" title="一位其實是兩個人、或有幾段其實是老師">拆開／有幾段其實是老師</button>
      </div></details>${split}</li>`;
  }).join("");
  const chosen = [...new Set(people.map(([, p]) => p["本名"]).filter(Boolean))];
  const right = chosen.length ? chosen.map((r) => {
    const cur = nameCode[r] || rosterCode[r] || "";
    const who = people.filter(([, p]) => p["本名"] === r).map(([n]) => n).join("、");
    const opts = ['<option value="">（還沒指定）</option>'].concat(codes.map((c) => `<option ${cur === c ? "selected" : ""}>${esc(c)}</option>`)).join("");
    return `<li class="rv-person"><div class="nm"><b>${esc(r)}</b><span class="rv-meta">${esc(who)}</span></div>
      <div class="ctl"><label>英文代號 <select class="rv-namecode" data-real="${esc(r)}">${opts}</select></label>
      ${rosterCode[r] && cur !== rosterCode[r] ? `<span class="rv-meta">名冊上是 ${esc(rosterCode[r])}</span>` : ""}</div></li>`;
  }).join("") : `<li class="rv-meta">左邊選了本名之後，這裡會列出來。</li>`;
  return `<p class="rv-meta">左邊是聲紋分出來的「學員 1、2⋯⋯」：試聽後選他的本名；聲音其實是老師的，選「${esc(teacher)}」。右邊是每個本名在這支影片換成哪個英文代號（預設是名冊上的）。</p>
    <div class="rv-people2"><div><h4>聲紋分出來的人</h4><ul class="rv-people">${left}</ul></div>
      <div><h4>本名 → 這支影片的英文代號</h4><ul class="rv-people">${right}</ul></div></div>`;
}

function rvPrepVoiceHtml() {
  const people = Object.entries(rv.data["學員"] || {});
  if (!people.length) return `<p class="rv-meta">這支影片沒有學員。</p>`;
  const rows = people.map(([n, p]) => `<li class="rv-person"><div class="nm"><i class="dot" style="background:${rvStuColor(n)}"></i><b>${esc(rvWho(n))}</b></div>
      <div class="ctl"><button class="ghost small rv-sample" data-person="${esc(n)}">試聽</button>
      ${rvRadios(`voice-${n}`, rv.data["選項"]["聲音"], p["聲音"], "rv-voice").replaceAll('class="rv-voice"', `class="rv-voice" data-person="${esc(n)}"`)}</div></li>`).join("");
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
    const segs = rvSegsOf(b.dataset.person);
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
  root.querySelectorAll(".rv-namecode").forEach((el) => el.addEventListener("change", async () => {
    await apiPost("/api/turns/namecode", { "本名": el.dataset.real, "代號": el.value || null }); await reload();
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
  const note = ok[0] && ok[1] ? `對齊${it["對齊到"] || ""}` : !ok[0] && !ok[1] ? "沒對齊，保留你標的時間" : `${ok[0] ? "終點" : "起點"}沒對齊`;
  return `<p class="rv-meta">你標的 ${esc(rvFmt(m[0], 2))}–${esc(rvFmt(m[1], 2))} → ${esc(rvFmt(it.start, 2))}–${esc(rvFmt(it.end, 2))}（${esc(note)}）</p>`;
}

function rvOpenEditor(it) {
  const ed = rv.ed;
  ed.open = true;
  ed.result = null;
  if (it) { ed.kind = RV_ITEM_KIND[it["類型"]]; ed.id = it.id; ed.a = it.start; ed.b = it.end; ed.label = `${(RV_TYPE[it["類型"]] || {}).label || it["類型"]} ${rvFmt(it.start)}`; }
  else { ed.id = null; ed.label = null; }
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

function rvEdTimeRow(which, label) {
  const t = rv.ed[which];
  return `<div class="rv-edrow"><span class="rv-edlab">${label}</span>
      <input class="rv-t" id="rv-ed-${which}" value="${t != null ? esc(rvFmt(t, 2)) : ""}" placeholder="43:15.2" aria-label="${label}（例如 43:15.2）">
      <button class="ghost small" data-now="${which}" title="${which === "a" ? "快捷鍵 I" : "快捷鍵 O"}">用目前播放位置</button>
      <button class="ghost small" data-nudge="${which}" data-d="-0.1" aria-label="${label}往前 0.1 秒">−0.1</button>
      <button class="ghost small" data-nudge="${which}" data-d="0.1" aria-label="${label}往後 0.1 秒">＋0.1</button></div>`;
}

function rvRenderIO() {
  const el = document.getElementById("rv-io");
  if (!el) return;
  const ed = rv.ed;
  if (!ed.open) {
    el.innerHTML = `<button class="ghost" id="rv-ed-open">＋新增修改</button>
      <span class="rv-meta">刪除段落、局部消音、漏抓的學員發言／名字／重疊</span>`;
    document.getElementById("rv-ed-open").addEventListener("click", () => rvOpenEditor(null));
    return;
  }
  const kindSel = ed.id ? `<b>改時間：${esc(ed.label || "")}</b>`
    : `<label>類型 <select id="rv-ed-kind">${RV_KINDS.map(([k, l]) => `<option value="${esc(k)}" ${k === ed.kind ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
  let extra = "";
  if (!ed.id && ed.kind === "學員發言") {
    extra = `<label>是哪位學員 <select id="rv-ed-who">${[...rvStudents(), "新學員"].map((n) => `<option value="${esc(n)}" ${n === ed.who ? "selected" : ""}>${esc(n === "新學員" ? "新的一位學員" : rvWho(n))}</option>`).join("")}</select></label>`;
  } else if (ed.kind === "名字" && !ed.id) {
    const codes = rv.data["代號選項"] || [];
    extra = `<label>換成代號 <select id="rv-ed-code"><option value="">（選一個）</option>${codes.map((c) => `<option ${c === ed.code ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></label>
      <label>逐字稿裡寫成 <input id="rv-ed-word" size="6" placeholder="不填就用對齊到的字" value="${esc(ed.word || "")}"></label>`;
  } else if (ed.kind === "局部消音" && !ed.id) {
    extra = rvRadios("rv-ed-way", rv.data["選項"]["消音"], ed.way || rv.data["選項"]["消音"][0], "rv-ed-way");
  }
  const res = ed.result;
  const resHtml = !res ? "" : res.error ? `<p class="rv-warnline">${esc(res.error)}</p>`
    : `<p class="rv-edres">✓ ${res["新增"] ? "新增了" : "改好了"}。你標的 ${esc(rvFmt(res["標的起訖"][0], 2))}–${esc(rvFmt(res["標的起訖"][1], 2))}
       → 對齊後 <b>${esc(rvFmt(res.start, 3))}–${esc(rvFmt(res.end, 3))}</b>
       ${res["對齊"][0] && res["對齊"][1] ? `（對齊${esc(res["對齊到"])}）`
         : `<span class="rv-warn">（${!res["對齊"][0] && !res["對齊"][1] ? "沒對齊" : res["對齊"][0] ? "終點沒對齊" : "起點沒對齊"}：附近找不到${esc(res["對齊到"])}，保留你標的時間）</span>`}</p>`;
  el.innerHTML = `<section class="rv-editor" aria-label="新增修改">
      <div class="rv-edrow">${kindSel}<span class="spacer"></span><button class="ghost small" id="rv-ed-close">收起來</button></div>
      ${rvEdTimeRow("a", "起點")}
      ${rvEdTimeRow("b", "終點")}
      ${extra ? `<div class="rv-edrow">${extra}</div>` : ""}
      <p class="rv-meta">${esc(RV_RULE_HINT[ed.kind] || "")}</p>
      <div class="rv-edrow"><button class="ghost" id="rv-ed-play">試聽這段</button>
        <button class="primary" id="rv-ed-save">${ed.busy ? "對齊中…" : ed.id ? "儲存修改" : "新增"}</button>
        <span class="rv-meta" id="rv-ed-dur"></span></div>
      <div id="rv-ed-res">${resHtml}</div></section>`;
  const q = (id) => document.getElementById(id);
  q("rv-ed-close").addEventListener("click", rvCloseEditor);
  const kind = q("rv-ed-kind");
  if (kind) kind.addEventListener("change", () => { ed.kind = kind.value; ed.result = null; rvRenderIO(); });
  for (const w of ["a", "b"]) {
    const box = q(`rv-ed-${w}`);
    const take = () => {   // 只更新數值，不重畫面板（重畫會把正在失去焦點的輸入框拿掉）
      if (!box.value.trim()) { rvEdSet(w, null, false); return; }
      const t = rvParseTime(box.value);
      box.classList.toggle("bad", t == null);
      if (t != null) rvEdSet(w, t, false);
    };
    box.addEventListener("change", take);
    box.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); take(); } });
  }
  el.querySelectorAll("[data-now]").forEach((b) => b.addEventListener("click", () => rvEdSet(b.dataset.now, rv.video ? rv.video.currentTime : null)));
  el.querySelectorAll("[data-nudge]").forEach((b) => b.addEventListener("click", () => {
    const w = b.dataset.nudge;
    if (ed[w] == null) return;
    rvEdSet(w, Math.max(0, Math.round((ed[w] + Number(b.dataset.d)) * 100) / 100));
  }));
  const who = q("rv-ed-who"); if (who) who.addEventListener("change", () => { ed.who = who.value; });
  const code = q("rv-ed-code"); if (code) code.addEventListener("change", () => { ed.code = code.value; });
  const word = q("rv-ed-word"); if (word) word.addEventListener("change", () => { ed.word = word.value; });
  el.querySelectorAll(".rv-ed-way").forEach((r) => r.addEventListener("change", () => { ed.way = r.value; }));
  q("rv-ed-play").addEventListener("click", () => {
    if (!rv.video || !rvEdOk()) return;
    rv.stopAt = ed.b;
    rvSeek(ed.a, true);
  });
  q("rv-ed-save").addEventListener("click", rvEdSave);
  rvEdRefresh();
}

function rvEdOk() { const ed = rv.ed; return ed.a != null && ed.b != null && ed.b > ed.a; }

function rvEdRefresh() {   // 起點終點改了：更新輸入框、按鈕、長度，不重畫整個面板
  const ed = rv.ed, ok = rvEdOk();
  for (const w of ["a", "b"]) {
    const box = document.getElementById(`rv-ed-${w}`);
    if (box && document.activeElement !== box) box.value = ed[w] != null ? rvFmt(ed[w], 2) : "";
  }
  const play = document.getElementById("rv-ed-play"), save = document.getElementById("rv-ed-save");
  if (play) play.disabled = !ok;
  if (save) save.disabled = !ok || ed.busy;
  const dur = document.getElementById("rv-ed-dur");
  if (dur) dur.textContent = ok ? `共 ${(ed.b - ed.a).toFixed(1)} 秒` : "起點、終點都填好（終點晚於起點）才能新增";
}

function rvEdSet(which, t, fromButton = true) {
  const ed = rv.ed;
  if (t == null && fromButton) return;
  ed[which] = t;
  if (ed.result) { ed.result = null; const r = document.getElementById("rv-ed-res"); if (r) r.innerHTML = ""; }
  if (fromButton) { const box = document.getElementById(`rv-ed-${which}`); if (box) { box.value = rvFmt(t, 2); box.classList.remove("bad"); } }
  rvEdRefresh();
  rvRenderTimeline();
}

async function rvEdSave() {
  const ed = rv.ed;
  if (ed.a == null || ed.b == null || ed.b <= ed.a) return;
  const body = { "類型": ed.kind, start: ed.a, end: ed.b };
  if (ed.id) body.id = ed.id;
  else if (ed.kind === "學員發言") body["說話者"] = ed.who || rvStudents()[0] || "新學員";
  else if (ed.kind === "名字") { body["代號"] = ed.code || ""; if (ed.word) body["名字"] = ed.word; }
  else if (ed.kind === "局部消音") body["方式"] = ed.way || rv.data["選項"]["消音"][0];
  ed.busy = true;
  rvRenderIO();
  let r;
  try { r = await apiPost("/api/review/manual", body); } catch (err) {
    ed.busy = false; ed.result = { error: err.message }; rvRenderIO(); return;
  }
  ed.busy = false;
  const res = { ...r["對齊結果"], "新增": r["新增"] };
  // 新增完清空起點終點，可以接著標下一筆；結果留在面板上
  Object.assign(ed, { id: null, label: null, a: null, b: null, result: res, word: "", code: "" });   // 代號每筆重選，免得沿用上一筆
  await rvReload();
  const key = `${r["類型"]}:${r.id}`;
  if (rvItem(key)) { rv.filter = "全部"; rvRenderList(); rvSelect(key, { seek: false }); }
}

// ---------------------------------------------------------------------------
// 設定（平常收起來）：已自動跳過的重疊、學員聲音一鍵切換
// ---------------------------------------------------------------------------

function rvRenderSettings() {
  const el = document.getElementById("rv-settings");
  const list = rv.data["已自動跳過的重疊"] || [];
  el.innerHTML = `
    <section><h3>學員聲音一鍵全部切換</h3>
      <button class="ghost small rv-voice-all" data-v="重新生成">全部重新生成</button>
      <button class="ghost small rv-voice-all" data-v="保留原聲">全部保留原聲</button>
      <span class="rv-meta">保留原聲只給已經同意的學員</span></section>
    <section><h3>已自動跳過的重疊（${list.length} 處）</h3>
      <p class="rv-meta">兩位學員之間的重疊、老師講話時學員的短附和、不到 0.05 秒的邊界誤差，自動不處理。覺得要處理的按「救回」。</p>
      <ul class="rv-skips">${list.map((o) => `<li><span class="tm">${esc(rvFmt(o.start, 1))}</span> ${o.length.toFixed(2)} 秒　${esc(o["原因"] || "")}
        <button class="ghost small rv-segplay" data-t="${o.start - 2}">試聽</button> <button class="ghost small rv-rescue" data-id="${esc(o.id)}">救回</button></li>`).join("")}</ul></section>`;
  el.querySelectorAll(".rv-segplay").forEach((b) => b.addEventListener("click", () => rvSeek(Number(b.dataset.t), true)));
  el.querySelectorAll(".rv-rescue").forEach((b) => b.addEventListener("click", async () => {
    await apiPost("/api/review/overlap", { id: b.dataset.id, "救回": true }); await rvReload();
  }));
  el.querySelectorAll(".rv-voice-all").forEach((b) => b.addEventListener("click", async () => {
    await apiPost("/api/review/voice", { "學員": "全部", "聲音": b.dataset.v }); await rvReload();
  }));
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
    apiPost("/api/review/time", { "秒數": 30 }).then((r) => { rv.data["進度"]["已花秒數"] = r["覆核秒數"]; rvRecount(); }).catch(() => {});
  }, 30000);
}

// ---------------------------------------------------------------------------
// 快捷鍵
// ---------------------------------------------------------------------------

document.addEventListener("keydown", (e) => {
  if (currentRouteId() !== "step3" || !rv.data) return;
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
  else if ((k === "i" || k === "o") && rv.video) {   // I／O：把目前時間填進「新增修改」的起點／終點
    if (!rv.ed.open) rvOpenEditor(null);
    rvEdSet(k === "i" ? "a" : "b", rv.video.currentTime);
  }
  else if (k === "e" && !rv.prepOpen) { e.preventDefault(); rvToggleMore(); }
  else if (e.key === "ArrowDown" && !rv.prepOpen) { e.preventDefault(); rvStep(1); }
  else if (e.key === "ArrowUp" && !rv.prepOpen) { e.preventDefault(); rvStep(-1); }
});
