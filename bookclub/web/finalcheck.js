"use strict";

/* 第 5 步「成品檢查」（09-29 宇軒：原本第 5 步逐筆覆核＋第 6 步整片檢查合成一步）。
 * 版面沿用第 3 步：最上面固定一行「沒列在時間軸上的地方＝原片沒動」→ 頂端（進度、看過比例、模式、送回 AI 重做、輸出成品）
 *   → 左邊成品影片＋時間軸（處理紀錄的每一筆都標出來、沒登記的變動標紅、看過的區段）；右邊「目前這一筆」或整片看的面板
 *   → 下面：沒登記的變動（要人確認）＋全部處理紀錄清單。
 * 逐筆看：處理前／處理後試聽、通過、退回重做（要寫原因）。整片看：記錄實際播放過的區段（video.played，取聯集），
 * 看到問題按一下就在目前時間建一筆退回重做。全部通過、整片看過 100% 才能按「輸出成品」。
 * 資料：GET /api/final；存檔：/api/final/*（bookclub/finalcheck.py）。依賴 app.js 的 apiGet／apiPost／esc／contentEl。 */

const FC_TYPE = {
  "學員重念": "stu", "名字整句換掉": "name", "名字消音": "name", "換聲音": "name", "消音": "name",
  "刪除": "cut", "停格": "frz", "模糊示範": "blur", "重疊": "ov", "名字要人處理": "name",
};

const fc = { data: null, video: null, audio: null, cur: null, mode: "逐筆", sentRanges: "", timer: null, redoOpen: false, lastT: 0 };

function fcFmt(t, d = 1) { return typeof rvFmt === "function" ? rvFmt(t, d) : String(t); }
function fcRecs() { return fc.data["紀錄"]; }
function fcRec(key) { return fcRecs().find((r) => r["鍵"] === key); }
function fcHasTime(r) { return r["成品"] && r["成品"][0] != null; }

async function renderFinal() {
  contentEl.innerHTML = "<p>載入中…</p>";
  fc.data = await apiGet("/api/final");
  contentEl.classList.add("wide");
  const d = fc.data;
  if (!d["有處理紀錄"] || !d["成品影片"]) {
    contentEl.innerHTML = `<h1>5　成品檢查</h1>
      <div class="notyet-card">${d["有處理紀錄"] ? "有處理紀錄，但找不到成品影片（工作區 輸出/成品_*）。" : "還沒有處理紀錄。"}
      先在第 4 步組裝：<code>bookclub render video &lt;工作區&gt; --start 0:00 --end &lt;結尾&gt;</code>（會寫 生成/處理紀錄.json 與 輸出/成品_*.mp4）。</div>`;
    return;
  }
  const prodSel = d["成品影片清單"].length > 1 ? `<label class="fc-prod">檢查哪一支 <select id="fc-prod">${d["成品影片清單"].map((p) =>
    `<option value="${esc(p)}" ${p === d["成品影片"] ? "selected" : ""}>${esc(p.replace("輸出/", ""))}</option>`).join("")}</select></label>` : "";
  contentEl.innerHTML = `
    <div class="rv2 fc">
      <div class="fc-pin" role="note">沒列在時間軸上的地方＝原片沒動</div>
      <header class="rv-bar">
        <h1>5　成品檢查</h1>
        <div class="rv-progress" id="fc-progress"></div>
        <div class="rv-bar-btns">
          <button class="ghost" id="fc-mode-逐筆">逐筆看</button>
          <button class="ghost" id="fc-mode-整片">整片看</button>
          <button class="ghost" id="fc-sendback"></button>
          <button class="primary" id="fc-export">輸出成品</button>
        </div>
      </header>
      <div class="rv-export-msg" id="fc-msg"></div>
      <section class="rv-upper">
        <div class="rv-left">
          ${prodSel}
          <video id="fc-video" controls preload="metadata" src="${esc(d["影片網址"])}?v=${encodeURIComponent(d["成品影片"])}"></video>
          <div class="rv-timebar"><span class="rv-clock"><b id="fc-clock">0:00</b> ／ ${esc(fcFmt(d["成品長度"], 0))}（成品時間）</span>
            <select id="fc-rate" aria-label="播放速度"><option value="1">1 倍速</option><option value="1.25">1.25 倍速</option><option value="1.5">1.5 倍速</option><option value="2">2 倍速</option></select></div>
          <div class="rv-tl fc-tl" id="fc-tl" title="點一下或拖拉，跳到那個時間"></div>
          <div class="rv-legend">
            <span><i class="sw stu"></i>學員重念</span><span><i class="sw name"></i>名字</span><span><i class="sw cut"></i>刪除點</span>
            <span><i class="sw frz"></i>停格</span><span><i class="sw blur"></i>模糊</span><span><i class="sw ov"></i>重疊</span>
            <span><i class="sw bad"></i>沒登記的變動</span><span class="gap"><i class="sw seen"></i>看過的地方</span></div>
          <p class="rv-meta">${esc(d["來源"] || "")}　處理紀錄 ${esc(d["處理紀錄產生時間"] || "")}</p>
        </div>
        <div class="rv-right" id="fc-right"></div>
      </section>
      <section class="rv-lower" id="fc-lower"></section>
      <audio id="fc-audio" preload="none"></audio>
    </div>`;
  fc.video = document.getElementById("fc-video");
  fc.audio = document.getElementById("fc-audio");
  if (!fc.cur || !fcRec(fc.cur)) fc.cur = (fcRecs().find((r) => !r["結果"]) || fcRecs()[0] || {})["鍵"] || null;
  for (const m of ["逐筆", "整片"]) document.getElementById(`fc-mode-${m}`).addEventListener("click", () => { fc.mode = m; fcRenderAll(); });
  document.getElementById("fc-sendback").addEventListener("click", fcSendBack);
  document.getElementById("fc-export").addEventListener("click", fcExport);
  document.getElementById("fc-rate").addEventListener("change", (e) => { fc.video.playbackRate = Number(e.target.value); });
  const prod = document.getElementById("fc-prod");
  if (prod) prod.addEventListener("change", async () => { await apiPost("/api/final/product", { "成品影片": prod.value }); fc.sentRanges = ""; renderFinal(); });
  fcBindVideo();
  fcBindTimeline(document.getElementById("fc-tl"));
  window.addEventListener("resize", fcRenderTimeline);
  fcRenderAll();
  fcStartWatchTracking();
}

async function fcReload() {
  fc.data = await apiGet("/api/final");
  fcRenderAll();
}

function fcRenderAll() {
  for (const m of ["逐筆", "整片"]) document.getElementById(`fc-mode-${m}`).classList.toggle("on", fc.mode === m);
  fcRenderTop();
  fcRenderTimeline();
  fcRenderRight();
  fcRenderLower();
}

// ---------- 頂端：進度、看過比例、送回、輸出 ----------

function fcRenderTop() {
  const st = fc.data["狀態"];
  const pct = Math.floor(st["看過比例"] * 100);
  const done = st["逐筆"]["通過"] + st["逐筆"]["退回"];
  document.getElementById("fc-progress").innerHTML = `
    <div class="bar" role="progressbar" aria-valuenow="${done}" aria-valuemin="0" aria-valuemax="${st["逐筆"]["總數"]}"><i style="width:${st["逐筆"]["總數"] ? (done / st["逐筆"]["總數"]) * 100 : 0}%"></i></div>
    <span>逐筆 <b>${st["逐筆"]["通過"]}</b>／${st["逐筆"]["總數"]} 通過${st["逐筆"]["退回"] ? `、${st["逐筆"]["退回"]} 退回` : ""}</span>
    <span>已經看過全片的 <b id="fc-pct">${pct}%</b></span>
    ${st["未登記"]["總數"] ? `<span>沒登記的變動 ${st["未登記"]["沒問題"]}／${st["未登記"]["總數"]} 確認</span>` : ""}`;
  const sb = document.getElementById("fc-sendback");
  sb.textContent = `送回 AI 重做（${st["退回數"]} 筆）`;
  sb.disabled = !st["退回數"];
  const ex = document.getElementById("fc-export");
  ex.disabled = !st["可以輸出"];
  ex.title = st["可以輸出"] ? "全部通過、整片看過 100%：可以輸出" : st["還不能輸出的原因"].join("；");
  const msg = document.getElementById("fc-msg");
  if (fc.data["輸出成品"]) msg.innerHTML = `<span class="badge done">已輸出</span> <code>${esc(fc.data["輸出成品"]["檔案"])}</code>（${esc(fc.data["輸出成品"]["時間"])}）`;
  else if (fc.data["送回AI重做"]) msg.innerHTML = `已送回 AI 重做 ${fc.data["送回AI重做"]["項目"].length} 筆（${esc(fc.data["送回AI重做"]["時間"])}）：第 4 步照清單重做，終端機 <code>bookclub redo list</code> 也看得到。`;
  else if (!st["可以輸出"]) msg.innerHTML = `<span class="rv-meta">還不能輸出：${esc(st["還不能輸出的原因"].join("；"))}（09-18 定：最後一定要有人完整看過整支）</span>`;
  else msg.innerHTML = "";
}

// ---------- 影片、時間軸 ----------

function fcBindVideo() {
  const v = fc.video;
  v.addEventListener("timeupdate", () => {
    document.getElementById("fc-clock").textContent = fcFmt(v.currentTime, 1);
    const head = document.getElementById("fc-head");
    if (head) head.style.left = `${(v.currentTime / (fc.data["成品長度"] || 1)) * 100}%`;
    fcFollow(v.currentTime);
  });
  v.addEventListener("pause", fcSendWatched);
  v.addEventListener("ended", fcSendWatched);
}

function fcFollow(t) {   // 逐筆看：影片照常播、跨過某一筆的起點，右邊換到那一筆（正在寫原因時不換）
  const prev = fc.lastT;
  fc.lastT = t;
  if (fc.mode !== "逐筆" || fc.redoOpen || t < prev || t - prev > 2) return;
  const hit = fcRecs().filter((r) => fcHasTime(r) && r["成品"][0] > prev && r["成品"][0] <= t).pop();
  if (hit && hit["鍵"] !== fc.cur) { fc.cur = hit["鍵"]; fcRenderRight(); fcRenderTimeline(); fcMarkRow(); }
}

function fcSeek(t, play = false) {
  fc.video.currentTime = Math.max(0, Math.min(t, fc.data["成品長度"] || t));
  if (play) fc.video.play().catch(() => {});
}

function fcBindTimeline(el) {
  const seekAt = (e) => {
    const r = el.getBoundingClientRect();
    fcSeek((Math.min(Math.max(0, e.clientX - r.left), r.width) / r.width) * (fc.data["成品長度"] || 0));
  };
  let down = false;
  el.addEventListener("pointerdown", (e) => { down = true; seekAt(e); try { el.setPointerCapture(e.pointerId); } catch (err) { /* 合成事件 */ } });
  el.addEventListener("pointermove", (e) => { if (down) seekAt(e); });
  el.addEventListener("pointerup", () => { down = false; });
  el.addEventListener("pointercancel", () => { down = false; });
}

function fcRenderTimeline() {
  const el = document.getElementById("fc-tl");
  if (!el || !fc.data) return;
  const dur = fc.data["成品長度"] || 1;
  const pct = (t) => (t / dur) * 100;
  const marks = fcRecs().filter(fcHasTime).map((r) => {
    const [s, e] = r["成品"];
    const cls = FC_TYPE[r["類型"]] || "ov";
    const w = e != null && e > s ? Math.max(0.25, pct(e - s)) : 0;
    const st = r["結果"] === "通過" ? " ok" : r["結果"] === "退回重做" ? " redo" : "";
    return `<i class="fm ${cls}${st}${w ? "" : " pt"}" style="left:${pct(s)}%;${w ? `width:${w}%` : ""}" title="${esc(r["類型"])} ${esc(fcFmt(s))}"></i>`;
  }).join("");
  const bad = fc.data["未登記的變動"].filter((u) => u["成品秒"] != null)
    .map((u) => `<i class="fm bad pt" style="left:${pct(u["成品秒"])}%" title="沒登記的變動"></i>`).join("");
  const flags = fc.data["整片退回"].map((x) => `<i class="fm redo pt" style="left:${pct(x["成品秒"])}%" title="整片看時退回"></i>`).join("");
  const seen = fc.data["看過區段"].map(([s, e]) => `<i class="seen" style="left:${pct(s)}%;width:${pct(e - s)}%"></i>`).join("");
  const cur = fc.cur && fcRec(fc.cur);
  const curMark = cur && fcHasTime(cur) ? `<i class="cur" style="left:${pct(cur["成品"][0])}%;width:${Math.max(0.3, pct((cur["成品"][1] || cur["成品"][0]) - cur["成品"][0]))}%"></i>` : "";
  el.innerHTML = `<div class="track"></div>${marks}${bad}${flags}${curMark}<div class="seenbar">${seen}</div><i class="head" id="fc-head"></i>`;
}

// ---------- 右邊 ----------

function fcRenderRight() {
  if (fc.mode === "整片") return fcRenderWhole();
  const box = document.getElementById("fc-right");
  const r = fc.cur && fcRec(fc.cur);
  if (!r) { box.innerHTML = `<div class="rv-card empty"><p>處理紀錄是空的：這支成品跟原片一樣。</p></div>`; return; }
  const all = fcRecs();
  const idx = all.findIndex((x) => x["鍵"] === fc.cur);
  const o = r["原片"], p = r["成品"];
  const canAB = !!o && r["類型"] !== "名字要人處理";
  const state = r["結果"] === "通過" ? `<span class="rv-state ok">✓ 通過</span>`
    : r["結果"] === "退回重做" ? `<span class="rv-warnline">退回重做：${esc(r["原因"])}</span>` : "";
  box.innerHTML = `<article class="rv-card">
      <header><span class="rv-chip fc-${FC_TYPE[r["類型"]] || "ov"}"><i></i>${esc(r["類型"])}</span>
        <span class="rv-when">${p && p[0] != null ? `成品 ${esc(fcFmt(p[0]))}${p[1] != null && p[1] !== p[0] ? "–" + esc(fcFmt(p[1])) : ""}` : "成品裡沒有（刪掉了）"}</span>
        <span class="rv-count">第 ${idx + 1}／${all.length} 筆</span></header>
      <div class="rv-body">
        <p>${esc(r["做了什麼"])}</p>
        ${r["文字"] ? `<p class="rv-note">念的稿子：${esc(r["文字"])}</p>` : ""}
        <p class="rv-meta">原片 ${o ? `${esc(fcFmt(o[0]))}${o[1] !== o[0] ? "–" + esc(fcFmt(o[1])) : ""}` : "—"}
          ${(r["覆核項目"] || []).length ? `　第 3 步：${esc(r["覆核項目"].join("、"))}` : ""}${r["檔案"] ? `　檔案：${esc(r["檔案"])}` : ""}</p>
        ${r["要人聽"] ? `<p class="rv-warnline">生成檢查沒過或放不進時間格：仔細聽</p>` : ""}
      </div>
      ${canAB ? `<div class="rv-row fc-ab"><span class="rv-meta">試聽（前後各多 2 秒）</span>
        <button class="ghost small" data-ab="前">處理前</button><button class="ghost small" data-ab="後" ${p && p[0] != null ? "" : "disabled"}>處理後</button>
        <button class="ghost small" id="fc-ab-stop">停</button></div>` : ""}
      ${state ? `<div class="rv-statebox">${state}</div>` : ""}
      <div class="rv-actions">
        <button class="primary" id="fc-pass">${r["結果"] === "通過" ? "已通過（再按取消）" : "通過"}</button>
        <button class="ghost" id="fc-redo" aria-expanded="${fc.redoOpen}">退回重做</button>
        <span class="spacer"></span>
        <button class="ghost" id="fc-prev">上一筆</button><button class="ghost" id="fc-next">下一筆</button>
      </div>
      <div class="rv-more" id="fc-redobox" ${fc.redoOpen ? "" : "hidden"}>
        <label>退回的原因（AI 重做時照這個改）<textarea id="fc-reason" rows="2" placeholder="例如：第二句念錯字「課」念成「顆」；停格太久">${esc(r["結果"] === "退回重做" ? r["原因"] : "")}</textarea></label>
        <div class="rv-row"><button class="primary small" id="fc-redo-go">確定退回</button><button class="ghost small" id="fc-redo-cancel">取消</button></div>
      </div></article>`;
  box.querySelectorAll("[data-ab]").forEach((b) => b.addEventListener("click", () => fcPlayAB(r, b.dataset.ab)));
  const stop = document.getElementById("fc-ab-stop"); if (stop) stop.addEventListener("click", () => fc.audio.pause());
  document.getElementById("fc-pass").addEventListener("click", () => fcDecide(r, r["結果"] === "通過" ? null : "通過"));
  document.getElementById("fc-redo").addEventListener("click", () => { fc.redoOpen = !fc.redoOpen; fcRenderRight(); if (fc.redoOpen) document.getElementById("fc-reason").focus(); });
  document.getElementById("fc-redo-cancel").addEventListener("click", () => { fc.redoOpen = false; fcRenderRight(); });
  document.getElementById("fc-redo-go").addEventListener("click", () => {
    const why = document.getElementById("fc-reason").value.trim();
    if (!why) { alert("退回重做要寫原因"); return; }
    fcDecide(r, "退回重做", why);
  });
  document.getElementById("fc-prev").addEventListener("click", () => fcStep(-1));
  document.getElementById("fc-next").addEventListener("click", () => fcStep(1));
}

function fcPlayAB(r, which) {
  fc.video.pause();
  fc.audio.src = `/api/final/clip?which=${encodeURIComponent(which)}&key=${encodeURIComponent(r["鍵"])}`;
  fc.audio.play().catch(() => {});
  document.querySelectorAll("[data-ab]").forEach((b) => b.classList.toggle("on", b.dataset.ab === which));
}

function fcSelect(key, seek = true) {
  fc.cur = key;
  fc.redoOpen = false;
  const r = fcRec(key);
  if (seek && r && fcHasTime(r)) fcSeek(Math.max(0, r["成品"][0] - 2));
  fcRenderRight();
  fcRenderTimeline();
  fcMarkRow();
}

function fcStep(dir) {
  const all = fcRecs();
  const i = all.findIndex((x) => x["鍵"] === fc.cur);
  const n = all[Math.max(0, Math.min(all.length - 1, (i < 0 ? 0 : i) + dir))];
  if (n) fcSelect(n["鍵"]);
}

async function fcDecide(r, result, reason = "") {
  try {
    const res = await apiPost("/api/final/item", { "鍵": r["鍵"], "結果": result, "原因": reason });
    r["結果"] = result;
    r["原因"] = reason;
    fc.data["狀態"] = res["狀態"];
  } catch (e) { alert(e.message); return; }
  fc.redoOpen = false;
  if (result === "通過") {   // 通過了就換下一筆還沒看的（影片不跳，照常播）
    const next = fcRecs().find((x) => !x["結果"]);
    if (next) fc.cur = next["鍵"];
  }
  fcRenderTop(); fcRenderRight(); fcRenderTimeline(); fcRenderLower();
}

function fcRenderWhole() {
  const box = document.getElementById("fc-right");
  const st = fc.data["狀態"];
  const pct = Math.floor(st["看過比例"] * 100);
  const flags = fc.data["整片退回"];
  box.innerHTML = `<article class="rv-card">
      <header><span class="rv-chip"><i></i>整片看</span></header>
      <p class="fc-big">已經看過全片的 <b>${pct}%</b></p>
      <div class="fc-seen" aria-hidden="true">${fc.data["看過區段"].map(([s, e]) => `<i style="left:${(s / (st["成品長度"] || 1)) * 100}%;width:${((e - s) / (st["成品長度"] || 1)) * 100}%"></i>`).join("")}</div>
      <p class="rv-meta">看過 ${esc(fcFmt(st["看過秒數"], 0))}／${esc(fcFmt(st["成品長度"], 0))}。只算真的播過的地方（拖過去跳過的不算），看到 100% 才能輸出。</p>
      <div class="rv-field"><label>看到問題：寫一句原因，按下去就在目前時間建一筆退回重做
        <input id="fc-flag-why" placeholder="例如：這裡聲音突然變小"></label></div>
      <div class="rv-actions"><button class="primary" id="fc-flag">這裡有問題（退回重做）</button></div>
      ${flags.length ? `<ul class="fc-flags">${flags.map((x) => `<li><button class="linkish" data-t="${x["成品秒"]}">${esc(fcFmt(x["成品秒"]))}</button>
        ${esc(x["原因"])}${(x["覆核項目"] || []).length ? `<span class="rv-meta">（${esc(x["覆核項目"].join("、"))}）</span>` : ""}
        <button class="ghost small" data-rm="${esc(x.id)}">刪掉</button></li>`).join("")}</ul>` : ""}
    </article>`;
  document.getElementById("fc-flag").addEventListener("click", async () => {
    const why = document.getElementById("fc-flag-why").value.trim();
    if (!why) { alert("先寫一句原因"); return; }
    const t = fc.video.currentTime;
    try { await apiPost("/api/final/flag", { "成品秒": t, "原因": why }); } catch (e) { alert(e.message); return; }
    await fcReload();
  });
  box.querySelectorAll("[data-t]").forEach((b) => b.addEventListener("click", () => fcSeek(Number(b.dataset.t) - 2, true)));
  box.querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", async () => { await apiPost("/api/final/unflag", { id: b.dataset.rm }); await fcReload(); }));
}

// ---------- 下面：沒登記的變動、全部清單 ----------

function fcRenderLower() {
  const lower = document.getElementById("fc-lower");
  const un = fc.data["未登記的變動"];
  const unHtml = un.length ? `<section class="fc-unlogged">
      <h2>沒登記的變動（${un.length} 處，要人確認）</h2>
      <p class="rv-meta">聲音變了、但處理紀錄裡沒有這一筆。聽一下：是正常的就按「沒問題」，不該變的按「退回重做」。</p>
      <ul>${un.map((u) => `<li data-key="${esc(u["鍵"])}">
        <span class="tm">原片 ${esc(fcFmt(u["原片"][0]))}–${esc(fcFmt(u["原片"][1]))}</span>
        <span>${u["成品秒"] != null ? `<button class="ghost small fc-un-play" data-t="${u["成品秒"]}">到成品 ${esc(fcFmt(u["成品秒"]))} 聽</button>` : "（成品裡刪掉了）"}</span>
        <span>${u["結果"] === "沒問題" ? `<span class="rv-state ok">✓ 沒問題</span>` : u["結果"] === "退回重做" ? `<span class="rv-warnline">退回：${esc(u["原因"])}</span>` : ""}</span>
        <span class="rv-row"><button class="ghost small fc-un" data-r="沒問題">沒問題</button><button class="ghost small fc-un" data-r="退回重做">退回重做</button></span>
      </li>`).join("")}</ul></section>` : "";
  const rows = fcRecs().map((r) => `<li data-key="${esc(r["鍵"])}" class="${r["鍵"] === fc.cur ? "cur" : ""} ${r["結果"] ? "done" : ""}">
      <span class="tm">${fcHasTime(r) ? esc(fcFmt(r["成品"][0])) : "—"}</span>
      <span class="ty"><span class="rv-chip fc-${FC_TYPE[r["類型"]] || "ov"}"><i></i>${esc(r["類型"])}</span></span>
      <span class="tx">${esc(r["做了什麼"])}</span>
      <span class="sg">${esc((r["覆核項目"] || []).join("、"))}</span>
      <span class="st ${r["結果"] === "通過" ? "ok" : ""}">${r["結果"] === "通過" ? "✓ 通過" : r["結果"] === "退回重做" ? "退回" : "—"}</span></li>`).join("");
  lower.innerHTML = `${unHtml}<h2 class="fc-h2">處理紀錄（${fcRecs().length} 筆，成品時間）</h2>
    <ol class="rv-list" id="fc-list">${rows || `<li class="empty">沒有處理紀錄。</li>`}</ol>`;
  lower.querySelectorAll("#fc-list li[data-key]").forEach((li) => li.addEventListener("click", () => { fc.mode = "逐筆"; fcRenderAll(); fcSelect(li.dataset.key); }));
  lower.querySelectorAll(".fc-un-play").forEach((b) => b.addEventListener("click", () => fcSeek(Number(b.dataset.t) - 2, true)));
  lower.querySelectorAll(".fc-un").forEach((b) => b.addEventListener("click", async () => {
    const key = b.closest("li").dataset.key;
    let why = "";
    if (b.dataset.r === "退回重做") { why = prompt("退回的原因（AI 重做時照這個改）") || ""; if (!why.trim()) return; }
    try { await apiPost("/api/final/unlogged", { "鍵": key, "結果": b.dataset.r, "原因": why }); } catch (e) { alert(e.message); return; }
    await fcReload();
  }));
  fcMarkRow();
}

function fcMarkRow() {
  const list = document.getElementById("fc-list");
  if (!list) return;
  list.querySelectorAll("li.cur").forEach((li) => li.classList.remove("cur"));
  const li = fc.cur && list.querySelector(`li[data-key="${CSS.escape(fc.cur)}"]`);
  if (!li) return;
  li.classList.add("cur");
  const top = li.offsetTop - list.offsetTop;
  if (top < list.scrollTop || top + li.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = Math.max(0, top - list.clientHeight / 3);
}

// ---------- 看過比例：送 video.played（實際播過的區段）給後端取聯集 ----------

function fcPlayedRanges() {
  const p = fc.video && fc.video.played;
  const out = [];
  if (!p) return out;
  for (let i = 0; i < p.length; i++) out.push([Number(p.start(i).toFixed(3)), Number(p.end(i).toFixed(3))]);
  return out;
}

async function fcSendWatched() {
  const ranges = fcPlayedRanges();
  const sig = JSON.stringify(ranges);
  if (!ranges.length || sig === fc.sentRanges) return;
  try {
    const r = await apiPost("/api/final/watched", { "區段": ranges, "成品影片": fc.data["成品影片"] });
    fc.sentRanges = sig;
    fc.data["看過區段"] = r["看過區段"];
    fc.data["狀態"] = r["狀態"];
    fcRenderTop();
    fcRenderTimeline();
    if (fc.mode === "整片" && document.activeElement && document.activeElement.id !== "fc-flag-why") fcRenderWhole();
  } catch (e) { /* 下一次再送 */ }
}

function fcStartWatchTracking() {
  if (fc.timer) clearInterval(fc.timer);
  fc.timer = setInterval(() => {
    if (currentRouteId() !== "step5") { clearInterval(fc.timer); fc.timer = null; return; }
    if (fc.video && !fc.video.paused) fcSendWatched();
  }, 5000);
}

// ---------- 送回、輸出 ----------

async function fcSendBack() {
  const n = fc.data["狀態"]["退回數"];
  if (!confirm(`把 ${n} 筆退回的送回 AI 重做？第 4 步會照這份清單只重做這幾筆。`)) return;
  try { await apiPost("/api/final/sendback", {}); } catch (e) { alert(e.message); return; }
  await fcReload();
}

async function fcExport() {
  try {
    const r = await apiPost("/api/final/export", {});
    document.getElementById("fc-msg").innerHTML = `<span class="badge done">已輸出</span> <code>${esc(r["檔案"])}</code>`;
    await fcReload();
  } catch (e) { alert(e.message); }
}

document.addEventListener("keydown", (e) => {
  if (currentRouteId() !== "step5" || !fc.video) return;
  if (["TEXTAREA", "INPUT", "SELECT"].includes(e.target.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (e.key === " ") { e.preventDefault(); fc.video.paused ? fc.video.play().catch(() => {}) : fc.video.pause(); }
  else if (k === "j") fcSeek(fc.video.currentTime - 5);
  else if (k === "l") fcSeek(fc.video.currentTime + 5);
  else if (e.key === "ArrowDown" && fc.mode === "逐筆") { e.preventDefault(); fcStep(1); }
  else if (e.key === "ArrowUp" && fc.mode === "逐筆") { e.preventDefault(); fcStep(-1); }
});
