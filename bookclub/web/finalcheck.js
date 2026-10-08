"use strict";

/* 第 5 步「成品檢查」（09-29 宇軒：原本第 5 步逐筆覆核＋第 6 步整片檢查合成一步）。
 * 版面沿用第 3 步：最上面固定一行「沒列在時間軸上的地方＝原片沒動」→ 頂端（進度、看過比例、模式、送回 AI 重做、輸出成品）
 *   → 左邊成品影片＋時間軸（處理紀錄的每一筆都標出來、沒登記的變動標紅、看過的區段）；右邊「目前這一筆」或整片看的面板
 *   → 下面：沒登記的變動（要人確認）＋分頁（全部、還沒通過、每一種類型、第 3 步選定不修改、⚠️ 需留意；10-08 照第 3 步的分頁）。
 * 逐筆看：處理前／處理後試聽、通過、退回重做（要寫原因）。整片看：記錄實際播放過的區段（只算 2 倍速以下連續播的，取聯集），
 * 看到問題按一下就在目前時間建一筆退回重做。全部通過、整片看過 100% 就直接輸出；還沒檢查完按「輸出成品」會先問一次（10-08 宇軒放寬）。
 * 資料：GET /api/final；存檔：/api/final/*（bookclub/finalcheck.py）。依賴 app.js 的 apiGet／apiPost／esc／contentEl。 */

const FC_TYPE = {
  "學員重念": "stu", "名字整句換掉": "name", "名字消音": "name", "換聲音": "name", "消音": "name", "局部消音": "cut", "學員名字消音": "name", "學員名字換代號": "name",
  "刪除": "cut", "停格": "frz", "模糊示範": "blur", "重疊": "ov", "名字要人處理": "name",
  "學員空隙消音": "cut", "學員空隙保留原聲": "stu",   // 10-03 第八批補修 #102
};

// 10-01 走查：類型在畫面上的名稱跟第 3 步一致（存檔的值不變）
// 10-01 第三批 7：三頁統一四個名稱「老師重念」「學員重念」「剪掉」「消音」
const FC_LABEL = { "刪除": "剪掉（連畫面）", "局部消音": "消音（只拿掉聲音）", "名字整句換掉": "老師重念", "換聲音": "老師重念",
  "名字消音": "消音（名字）", "消音": "消音", "學員名字消音": "消音（學員講到名字）", "學員名字換代號": "學員重念（學員講到名字）",
  "學員空隙消音": "消音（學員段落兩格之間）", "學員空隙保留原聲": "保留原聲（學員段落兩格之間）" };
const fcKind = (t) => FC_LABEL[t] || t;
// 處理紀錄的來源（「render video 試看_1001」是指令名稱）→「成品 試看_1001」
const fcSourceName = (src) => String(src || "").replace(/^render video\s*/, "成品 ").replace(/^render audio$/, "只換聲音的版本");

// 09-29 宇軒：2 倍速以下播過的才算「看過」（快轉看完不算有人完整看過）
const FC_MAX_RATE = 2;

const fc = { span: null, seen: [], seg: null, data: null, video: null, audio: null, cur: null, mode: "逐筆", sentRanges: "", timer: null, redoOpen: false, lastT: 0, ab: null };
// 10-07 宇軒：畫面上的「學員3」換成「本名（代號）」（GET /api/final 的「學員顯示名」；只在畫面換，處理紀錄不寫本名）
function fcWho(text) {
  const names = (fc.data && fc.data["學員顯示名"]) || {};
  return String(text == null ? "" : text).replace(/學員\s?(\d+)(?!\d)/g, (m, n) => names[`學員${n}`] || m);
}
let fcBackKey = null;   // 10-01 第三批 13：從第 3 步按「回第 5 步成品檢查」回來時，回到出發的那一筆（影片跳到那裡、卡片捲進畫面）

function fcFmt(t, d = 1) { return typeof rvFmt === "function" ? rvFmt(t, d) : String(t); }
// 10-02 第六批：時間同時列原片與成品（「原片 42:59.8／成品 38:26.4」；成品時間是組裝時算好的，停格也算進去）
function fcSpan(r2) { return r2 && r2[0] != null ? `${fcFmt(r2[0])}${r2[1] != null && Math.abs(r2[1] - r2[0]) >= 0.05 ? "–" + fcFmt(r2[1]) : ""}` : ""; }
function fcBoth(r) {
  const o = fcSpan(r["原片"]), p = fcSpan(r["成品"]);
  return [o ? `原片 ${o}` : "", p ? `成品 ${p}` : (r["原片"] ? "成品裡沒有（剪掉了）" : "")].filter(Boolean).join("／") || "—";
}
// 10-02 第六批：「只看要人聽的」（生成檢查沒過、放不進時間格、標紅的）；開關記在 localStorage（讀不到就當作沒開）
const FC_ONLY_KEY = "fc-only-look";
function fcOnlyLook() { try { return localStorage.getItem(FC_ONLY_KEY) === "1"; } catch (e) { return false; } }
function fcSetOnlyLook(on) { try { localStorage.setItem(FC_ONLY_KEY, on ? "1" : "0"); } catch (e) { /* 存不了也沒關係 */ } }
// 10-08 宇軒：目前這一頁（分頁）列出來的處理紀錄；開了「只看要人聽的」再篩一次。「上一筆／下一筆」、通過後找下一筆都只在這裡面走
function fcShown() {
  const t = fcTabs().find((x) => x.id === fcTab());
  const recs = t && !t["清單外"] ? t.recs : fcRecs();
  return fcOnlyLook() ? recs.filter((r) => r["要人看"]) : recs;
}
function fcRecs() { return fc.data["紀錄"]; }
function fcRec(key) { return fcRecs().find((r) => r["鍵"] === key); }
function fcHasTime(r) { return r["成品"] && r["成品"][0] != null; }
// 10-04 #128：跟 AI 助手溝通用的編號（跟 `bookclub inspect 成品檢查` 的「編號」一樣：#12；有生成檔的加生成編號：#12 T034_13）。
// 小字灰色；缺欄位、長得不像編號就不顯示，不丟例外
function fcIdTag(r) {
  try {
    const n = r ? r["編號"] : null;
    const g = r && typeof r["生成編號"] === "string" && /^[A-Za-z]{1,4}\d+(?:m\d+)*(?:_\d+)?$/.test(r["生成編號"]) ? r["生成編號"] : "";
    const txt = [Number.isInteger(n) ? `#${n}` : "", g].filter(Boolean).join(" ");
    return txt ? `<span class="idtag" title="跟 AI 助手溝通用的編號">${esc(txt)}</span>` : "";
  } catch (e) { return ""; }
}

async function renderFinal() {
  contentEl.innerHTML = "<p>載入中…</p>";
  fc.data = await apiGet("/api/final");
  contentEl.classList.add("wide");
  const d = fc.data;
  if (!d["有處理紀錄"] || !d["成品影片"]) {
    contentEl.innerHTML = fcPreviewHtml(d["有處理紀錄"] ? "有處理紀錄，但找不到成品影片（工作區 輸出/成品_*）。" : "還沒有成品。");
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
      <div class="fo-box" id="fc-fileout"></div>
      <section class="rv-upper">
        <div class="rv-left">
          ${prodSel}
          <video id="fc-video" controls preload="metadata" src="${esc(d["影片網址"])}?v=${encodeURIComponent(d["成品影片"])}"></video>
          <div class="rv-timebar"><span class="rv-clock"><b id="fc-clock">0:00</b> ／ ${esc(fcFmt(d["成品長度"], 0))}（成品時間）</span>
            <span class="nowrap"><input id="fc-goto" class="rv-goto" placeholder="跳到 38:26" aria-label="跳到時間（例如 38:26，按 Enter）">
              <select id="fc-goto-kind" aria-label="輸入的是成品時間還是原片時間"><option value="成品">成品時間</option><option value="原片">原片時間</option></select></span>
            <span class="rv-meta" id="fc-goto-msg" role="status"></span>
            <span class="rv-warnline" id="fc-ratewarn" hidden>超過 2 倍速播的不算看過</span>
            <select id="fc-rate" aria-label="播放速度"><option value="1">1 倍速</option><option value="1.25">1.25 倍速</option><option value="1.5">1.5 倍速</option><option value="2">2 倍速</option></select></div>
          <div class="rv-tl fc-tl" id="fc-tl" title="點一下或拖拉，跳到那個時間"></div>
          <div class="rv-legend">
            <span><i class="sw stu"></i>學員重念</span><span><i class="sw name"></i>老師重念、名字</span><span><i class="sw cut"></i>剪掉</span>
            <span><i class="sw frz"></i>停格</span><span><i class="sw blur"></i>模糊</span><span><i class="sw ov"></i>重疊</span>
            <span><i class="sw bad"></i>沒登記的變動</span><span class="gap"><i class="sw seen"></i>看過的地方</span></div>
          <p class="rv-meta">${esc(fcSourceName(d["來源"]))}　處理紀錄產生於 ${esc(fmtStamp(d["處理紀錄產生時間"]))}</p>
        </div>
        <div class="rv-right" id="fc-right"></div>
      </section>
      <section class="rv-lower" id="fc-lower"></section>
      <audio id="fc-audio" preload="none"></audio>
    </div>`;
  fc.video = document.getElementById("fc-video");
  Object.assign(fc, { seen: [], seg: null, sentRanges: "" });   // 這次打開頁面播的，後端再跟之前的取聯集
  fc.audio = document.getElementById("fc-audio");
  if (!fc.cur || !fcRec(fc.cur)) fc.cur = (fcRecs().find((r) => !r["結果"]) || fcRecs()[0] || {})["鍵"] || null;
  for (const m of ["逐筆", "整片"]) document.getElementById(`fc-mode-${m}`).addEventListener("click", () => { fc.mode = m; fcRenderAll(); });
  document.getElementById("fc-sendback").addEventListener("click", fcSendBack);
  document.getElementById("fc-export").addEventListener("click", fcExport);
  fileOutInit(document.getElementById("fc-fileout"));   // 10-07：打開成品資料夾、複製到 Windows 的下載資料夾（fileio.js）
  document.getElementById("fc-rate").addEventListener("change", (e) => { fc.video.playbackRate = Number(e.target.value); });
  document.getElementById("fc-goto").addEventListener("keydown", (e) => {   // 10-03 第八批 #64：照第 3 步的「跳到」框
    if (e.key !== "Enter" || e.isComposing) return;
    e.preventDefault();
    fcGoto(e.target, document.getElementById("fc-goto-kind").value);
  });
  fc.ab = null;
  fc.audio.addEventListener("ended", () => { if (fc.ab && fc.ab.which === "前") fcStopAB(); });
  const prod = document.getElementById("fc-prod");
  if (prod) prod.addEventListener("change", async () => { await apiPost("/api/final/product", { "成品影片": prod.value }); fc.sentRanges = ""; renderFinal(); });
  fcBindVideo();
  fcBindTimeline(document.getElementById("fc-tl"));
  window.addEventListener("resize", fcRenderTimeline);
  fcRenderAll();
  fcStartWatchTracking();
  if (fcBackKey) {
    const key = fcBackKey;
    fcBackKey = null;
    if (fcRec(key)) { fc.mode = "逐筆"; fcTabSet(fcTabOf(key)); fcRenderAll(); fcSelect(key); }   // 10-08：切到那一筆所在的頁
    else if ((fc.data["不修改"] || []).some((u) => u["鍵"] === key)) { fcTabSet(FC_TAB_KEEP); fcRenderLower(); }   // 從「不修改」那一列出發的
    const row = !fcRec(key) && document.querySelector(`#fc-ulist li[data-ukey="${CSS.escape(key)}"]`);
    if (row) { row.classList.add("cur"); row.scrollIntoView({ block: "center" }); }
    const card = row ? null : document.getElementById("fc-right");
    if (card) card.scrollIntoView({ block: "nearest" });
  }
}

// 還沒有成品時：照正式版面擺一份霧化的樣子（假資料、按不下去），讓人先知道之後要做什麼（09-29 宇軒）
function fcPreviewHtml(why) {
  const rows = [["0:42", "學員重念", "學員 A 的段落"], ["3:15", "名字", "老師提到名字，整句重念"], ["7:08", "剪掉的地方", "開頭空白"],
    ["12:30", "停格", "重念比原本長，補長"], ["18:02", "名字", "直接消音"], ["25:47", "學員重念", "學員 B 的段落"]];
  const blocks = [[3, 5, "stu"], [9, 1, "name"], [16, 0.6, "cut"], [27, 3, "frz"], [38, 1, "name"], [51, 6, "stu"], [70, 1, "name"], [83, 4, "stu"]];
  return `
    <div class="fc-preview-wrap">
      <div class="rv2 fc fc-preview" inert aria-hidden="true">
        <div class="fc-pin">沒列在時間軸上的地方＝原片沒動</div>
        <header class="rv-bar">
          <h1>5　成品檢查</h1>
          <div class="rv-progress"><div class="bar"><i style="width:35%"></i></div><span>逐筆 <b>7</b>／20 通過</span><span>看過 35%</span></div>
          <div class="rv-bar-btns"><button class="ghost on">逐筆看</button><button class="ghost">整片看</button>
            <button class="ghost">送回 AI 重做（1 筆）</button><button class="primary">輸出成品</button></div>
        </header>
        <section class="rv-upper">
          <div class="rv-left">
            <div class="fc-preview-video"></div>
            <div class="rv-timebar"><span class="rv-clock"><b>3:15</b> ／ 1:38:00（成品時間）</span></div>
            <div class="rv-tl fc-tl fc-preview-tl">${blocks.map(([l, w, c]) => `<i class="sw ${c}" style="left:${l}%;width:${w}%"></i>`).join("")}</div>
            <div class="rv-legend"><span><i class="sw stu"></i>學員重念</span><span><i class="sw name"></i>名字</span><span><i class="sw cut"></i>剪掉的地方</span>
              <span><i class="sw frz"></i>停格</span><span class="gap"><i class="sw seen"></i>看過的地方</span></div>
          </div>
          <div class="rv-right"><div class="fc-preview-card">
            <p><b>3:15　名字</b>　老師提到名字，整句重念</p>
            <p class="muted">處理前 ▶　處理後 ▶</p>
            <p><button class="primary">通過</button> <button class="ghost">退回重做</button></p></div></div>
        </section>
        <section class="rv-lower"><table class="kv">${rows.map(([t, k, x]) => `<tr><td>${t}</td><td>${k}</td><td>${x}</td><td>—</td></tr>`).join("")}</table></section>
      </div>
      <div class="fc-preview-note" role="status">
        <h2>${esc(why)}</h2>
        <p>第 4 步 AI 修改跑完之後，這一頁會變成<b>成品檢查</b>：逐筆聽處理前後、按通過或退回重做，再把整片看完（看過 100%）再輸出成品（沒看完也能輸出，會先問一次）。</p>
        <p class="muted">後面霧霧的是之後的樣子，現在還不能按。</p>
        <p><a class="btn" href="#step4">到第 4 步 AI 執行</a></p>
      </div>
    </div>`;
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
    ${st["未登記"]["總數"] ? `<span>沒登記的變動 ${st["未登記"]["沒問題"]}／${st["未登記"]["總數"]} 確認</span>` : ""}
    ${(fc.data["需留意"] || {})["總數"] ? `<span>需留意 ${fc.data["需留意"]["總數"]} 處${fc.data["需留意"]["名字"] ? `<b class="fc-export-names">（名字 ${fc.data["需留意"]["名字"]} 處）</b>` : ""}</span>` : ""}`;
  const sb = document.getElementById("fc-sendback");
  sb.textContent = `送回 AI 重做（${st["退回數"]} 筆）`;
  sb.disabled = !st["退回數"];
  const ex = document.getElementById("fc-export");
  ex.disabled = !fc.data["有處理紀錄"] || !fc.data["成品影片"];   // 10-08 宇軒放寬：沒檢查完也能按，按下去先確認
  ex.title = st["可以輸出"] ? "全部通過、整片看過 100%：可以輸出" : `還沒檢查完（${st["還不能輸出的原因"].join("；")}），按下去會先問一次`;
  const msg = document.getElementById("fc-msg");
  const early = fcExportedEarly(fc.data["輸出成品"]);
  if (fc.data["輸出成品"]) msg.innerHTML = `<span class="badge done">已輸出</span> <code>${esc(fc.data["輸出成品"]["檔案"])}</code>（${esc(fmtStamp(fc.data["輸出成品"]["時間"]))}）${early ? `<span class="rv-warnline">　${esc(early)}</span>` : ""}<span class="muted">　要重新輸出可以再按一次，會產生新的一支、舊的不會被蓋掉；成品在下面的按鈕拿</span>`;
  else if (fc.data["重做中"]) msg.innerHTML = `第 4 步正在重做退回的 ${fc.data["重做中"]["項目"].length} 筆（或上次沒做完）：做完會重新組裝，這幾筆回到「還沒看」。`;
  else if (fc.data["送回AI重做"]) msg.innerHTML = `已送回 AI 重做 ${fc.data["送回AI重做"]["項目"].length} 筆（${esc(fmtStamp(fc.data["送回AI重做"]["時間"]))}）：到 <a href="#step4">第 4 步</a> 按「只重做退回的這幾筆」。`;
  else if (fc.data["重做過"]) msg.innerHTML = `上一次重做了 ${fc.data["重做過"]["項目"].length} 筆（${esc(fmtStamp(fc.data["重做過"]["時間"]))}）：清單上標「重做過」的要重新看。`;
  else if (!st["可以輸出"]) msg.innerHTML = `<span class="rv-meta">還沒檢查完：${esc(st["還不能輸出的原因"].join("；"))}（還是可以輸出，按下去會先問一次）</span>`;
  else msg.innerHTML = "";
}

// ---------- 影片、時間軸 ----------

function fcBindVideo() {
  const v = fc.video;
  v.addEventListener("timeupdate", () => {
    const clock = document.getElementById("fc-clock");
    if (!clock) return;   // 已經換到別的步驟（影片還在送事件）
    clock.textContent = fcFmt(v.currentTime, 1);
    fcAbTick(v.currentTime);
    fcSpanTick(v.currentTime);   // 10-08：「不修改」那一列按「跳過去聽」只播那一段
    fcMoveHead();
    fcFollow(v.currentTime);
    fcTrack();
  });
  const stop = () => {   // 暫停、播完、要跳走、換速度：先把播到這裡的接上再斷開
    const t = v.currentTime;
    if (fc.seg && t >= fc.seg[1] && t - fc.seg[1] < 1.0) fc.seg[1] = t;
    fcEndSeg();
  };
  v.addEventListener("pause", () => { stop(); fcSendWatched(); });
  v.addEventListener("ended", () => { stop(); fcSendWatched(); });
  v.addEventListener("seeking", stop);
  v.addEventListener("seeked", () => { const c = document.getElementById("fc-clock"); if (c) c.textContent = fcFmt(v.currentTime, 1); fcMoveHead(); });
  v.addEventListener("ratechange", () => {
    fcEndSeg();
    const w = document.getElementById("fc-ratewarn");
    if (w) w.hidden = v.playbackRate <= FC_MAX_RATE;
  });
}

function fcFollow(t) {   // 逐筆看：影片照常播、跨過某一筆的起點，右邊換到那一筆（正在寫原因時不換）
  const prev = fc.lastT;
  fc.lastT = t;
  if (fc.mode !== "逐筆" || fc.redoOpen || fc.ab || t < prev || t - prev > 2) return;   // 試聽中不換（前後多播的 2 秒會碰到隔壁那筆）
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
    return `<i class="fm ${cls}${st}${w ? "" : " pt"}" style="left:${pct(s)}%;${w ? `width:${w}%` : ""}" title="${esc(fcKind(r["類型"]))} ${esc(fcFmt(s))}"></i>`;
  }).join("");
  const bad = fc.data["未登記的變動"].filter((u) => u["成品秒"] != null)
    .map((u) => `<i class="fm bad pt" style="left:${pct(u["成品秒"])}%" title="沒登記的變動"></i>`).join("");
  const flags = fc.data["整片退回"].map((x) => `<i class="fm redo pt" style="left:${pct(x["成品秒"])}%" title="整片看時退回"></i>`).join("");
  const seen = fc.data["看過區段"].map(([s, e]) => `<i class="seen" style="left:${pct(s)}%;width:${pct(e - s)}%"></i>`).join("");
  const cur = fc.cur && fcRec(fc.cur);
  const curMark = cur && fcHasTime(cur) ? `<i class="cur" style="left:${pct(cur["成品"][0])}%;width:${Math.max(0.3, pct((cur["成品"][1] || cur["成品"][0]) - cur["成品"][0]))}%"></i>` : "";
  // 10-01 2-2：重畫時紅線放在目前時間（以前重畫後紅線沒有位置，要等下一次播放進度更新才回來，暫停時就停在 0:00）
  el.innerHTML = `<div class="track"></div>${marks}${bad}${flags}${curMark}<div class="seenbar">${seen}</div><i class="head" id="fc-head" style="left:${pct(fc.video ? fc.video.currentTime : 0)}%"></i>`;
}

function fcMoveHead() {
  const head = document.getElementById("fc-head");
  if (head && fc.video) head.style.left = `${(fc.video.currentTime / (fc.data["成品長度"] || 1)) * 100}%`;
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
  const done = r["已改範圍"];
  const state = r["結果"] === "通過" ? `<span class="rv-state ok">✓ 通過</span>`
    : r["結果"] === "退回重做" ? `<span class="rv-warnline">退回重做：${esc(r["原因"])}</span>${done && done["改成"]
      ? `<br><span class="rv-meta">範圍改了：${done["原本"] ? `${esc(fcFmt(done["原本"][0]))}–${esc(fcFmt(done["原本"][1]))} → ` : ""}${esc(fcFmt(done["改成"][0]))}–${esc(fcFmt(done["改成"][1]))}（原片時間，第 3 步〈${esc(fcWho(done["名稱"] || ""))}〉看得到）。第 4 步按「開始執行」會${esc(done["重做"] || "重新組裝")}</span>` : ""}` : "";
  box.innerHTML = `<article class="rv-card">
      <header><span class="rv-chip fc-${FC_TYPE[r["類型"]] || "ov"}"><i></i>${esc(fcKind(r["類型"]))}</span>
        <span class="rv-when">${esc(fcBoth(r))}</span>${fcIdTag(r)}
        <span class="rv-count">第 ${idx + 1}／${all.length} 筆</span></header>
      <div class="rv-body">
        <p>${esc(fcWho(r["做了什麼"]))}</p>
        ${r["文字"] ? `<p class="rv-note">念的稿子：${esc(r["文字"])}</p>` : ""}
        ${(r["覆核名稱"] || []).length ? `<p class="rv-meta">第 3 步：${esc(fcWho(r["覆核名稱"].join("、")))}</p>` : ""}
        ${r["要人聽"] ? `<p class="rv-warnline">生成檢查沒過或放不進時間格：仔細聽</p>` : ""}
        ${fcEdgeHtml(r)}
      </div>
      ${canAB ? `<div class="rv-row fc-ab"><span class="rv-meta">試聽（前後各多 2 秒）</span>
        <button class="ghost small" data-ab="前">處理前</button><button class="ghost small" data-ab="後" ${p && p[0] != null ? "" : "disabled"}>處理後</button>
        <button class="ghost small" id="fc-ab-stop">停</button></div>` : ""}
      ${state ? `<div class="rv-statebox">${state}</div>` : ""}
      ${fcRedoneHtml(r)}
      <div class="rv-actions">
        <button class="${r["結果"] === "通過" ? "primary on" : r["結果"] ? "ghost" : "primary"}" id="fc-pass" aria-pressed="${r["結果"] === "通過"}">${r["結果"] === "通過" ? "✓ 已通過（再按取消）" : "通過"}</button>
        <button class="ghost${r["結果"] === "退回重做" ? " on" : ""}" id="fc-redo" aria-pressed="${r["結果"] === "退回重做"}" aria-expanded="${fc.redoOpen}">${r["結果"] === "退回重做" ? "✓ 已退回重做（再按可改）" : "退回重做"}</button>
        <span class="spacer"></span>
        <button class="ghost" id="fc-prev">上一筆</button><button class="ghost" id="fc-next">下一筆</button>
      </div>
      <div class="rv-more" id="fc-redobox" ${fc.redoOpen ? "" : "hidden"}>
        <label>退回的原因（AI 重做時照這個改）<textarea id="fc-reason" rows="2" placeholder="例如：第二句念錯字「課」念成「顆」；停格太久">${esc(r["結果"] === "退回重做" ? r["原因"] : "")}</textarea></label>
        <div class="rv-row"><button class="primary small" id="fc-redo-go">確定退回</button><button class="ghost small" id="fc-redo-cancel">取消</button>
          ${r["結果"] === "退回重做" ? `<button class="ghost small" id="fc-redo-undo">取消退回（改回還沒看）</button>` : ""}</div>
        ${fcRetimeHtml(r)}
      </div></article>`;
  box.querySelectorAll("[data-ab]").forEach((b) => b.addEventListener("click", () => fcPlayAB(r, b.dataset.ab)));
  const stop = document.getElementById("fc-ab-stop"); if (stop) stop.addEventListener("click", () => { fc.audio.pause(); fcStopAB(); });
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
  const undo = document.getElementById("fc-redo-undo");
  if (undo) undo.addEventListener("click", () => fcDecide(r, null));
  fcBindRetime(r);
  const shrink = document.getElementById("fc-shrink");
  if (shrink) shrink.addEventListener("click", () => fcShrink(r));
}

// 10-02 第六批：老師重念範圍前後沒有人講話：提醒＋「照建議縮小」（改的是第 3 步同一個地方，這一筆標成退回重做）
function fcEdgeHtml(r) {
  const h = r["前後沒聲音"];
  if (!h) return "";
  return `<div class="rv-note fc-edge" id="fc-edge"><p>${esc(h["說明"])}（原片時間）。</p>
    ${h["可以縮"] ? `<button class="ghost small" id="fc-shrink">照建議縮小</button> <span class="rv-meta">按了會改第 3 步這一筆的重念範圍，這一筆標成退回重做（第 4 步重新生成這一句）</span>`
      : `<p class="rv-meta">${esc(h["原因"] || "")}</p>`}</div>`;
}

async function fcShrink(r) {
  const h = r["前後沒聲音"];
  if (!confirm(`把重念範圍改成 ${fcFmt(h["建議"][0])}–${fcFmt(h["建議"][1])}（原片時間）？\n這一筆會標成退回重做，第 4 步按「開始執行」會重新生成這一句、再重新組裝。`)) return;
  try { await apiPost("/api/review/shrink", { "鍵": h["鍵"], "第5步鍵": r["鍵"] }); } catch (e) { alert(e.message); return; }
  fc.redoOpen = false;
  await fcReload();
}

// 10-01 第三批：第 4 步「只重做退回的」重做過的這一筆：看得出是重做過的新版本、上一次退回的原因
function fcRedoneHtml(r) {
  const x = r["重做過"];
  if (!x) return "";
  // 10-02 第四批：文字和範圍沒改的，重做時換一種念法重新生成；看得出現在是第幾版（上一版的聲音留著備份）
  const ver = x["第幾版"] ? `第 ${x["第幾版"]} 版` : "";
  const what = x["做法"] === "重新生成"
    ? (x["新版本"] === false ? "重新生成過，但這一筆的聲音跟上一版一樣"
      : x["換一種念法"] ? `這是換一種念法重新生成的${ver || "新版本"}（文字和範圍沒改，上一版的聲音留著備份）`
        : `這一版是照改過的內容重新生成的${ver ? `（${ver}）` : "新聲音"}`)
    : x["只重新組裝"] ? "生成的聲音沒動，只重新組裝過"   // 10-03 第八批 #23：第 4 步「只重新組裝」
      : "這一筆沒有聲音要重新生成，只重新組裝過";
  return `<div class="rv-statebox"><span class="rv-tag">${esc(x["標籤"] || "重做過")}${ver && x["做法"] === "重新生成" ? `・${esc(ver)}` : ""}</span> <span class="rv-meta">${esc(fmtStamp(x["時間"]))}：${esc(what)}。
    上一次退回的原因：${esc(x["原因"] || "（沒寫）")}</span></div>`;
}

// ---------- 10-01 2-4：退回重做時直接調整這一筆的時間範圍（用第 3 步同一套起訖編輯器，存到第 3 步同一個地方） ----------

function fcSrcTime(t) {   // 成品時間 → 原片時間（跟後端 finalcheck.from_output_time 一樣；落在停格裡算停格那一點）
  const plist = fc.data["片段"];
  if (!plist || !plist.length) return t;
  let acc = 0;
  for (const p of plist) {
    const [s, e] = p.src;
    if (t <= acc + (e - s)) return s + Math.max(0, t - acc);
    acc += e - s;
    if (t <= acc + p.freeze) return e;
    acc += p.freeze;
  }
  return plist[plist.length - 1].src[1];
}

function fcRetimeHtml(r) {
  const t = r["改範圍"] || {};
  const go = t["第3步"] ? `<button class="ghost small" id="fc-go3">去第 3 步〈${esc(fcWho(t["名稱"] || "這一筆"))}〉</button>` : "";
  if (!t["可以"]) {
    return `<div class="rv-field fc-retime"><b>調整時間範圍</b>
      <p class="rv-meta">這一筆不能在這裡改範圍：${esc(t["原因"] || "")}。下一步：${esc(t["下一步"] || "")}</p>${go}</div>`;
  }
  return `<div class="rv-field fc-retime"><b>調整時間範圍</b>（原片時間）
      <p class="rv-meta">時間點抓得不準，在這裡直接改起點終點：存到第 3 步〈${esc(fcWho(t["名稱"]))}〉同一個地方，這一筆自動標成退回重做，第 4 步按「開始執行」會${esc(t["重做"])}（其他做好的不重做）。</p>
      <div id="fc-retime"></div>${go}</div>`;
}

// 10-01 第三批 11：第 5 步改學員段落照填的時間、不對齊（以前對齊句子邊界，+0.1 秒這種小調整會被縮回）
function fcRawTime(t) { return t["方式"] === "改時間" && t["類型"] === "學員發言"; }

// 面板上的提醒跟實際行為一致（哪幾種對齊、哪幾種照填的時間，見回報的表）
function fcRetimeHint(t) {
  if (t["方式"] === "重念範圍") return "照你填的時間存，不會自動對齊；要包住名字，切點要自己聽準";
  if (t["方式"] === "學員起訖") return "照你填的時間存，不會自動對齊；這段時間整段換成學員的生成聲音（裡面老師的聲音不保留），切點要自己聽準";
  if (fcRawTime(t)) return "照你填的時間存，不會像第 3 步那樣自動對齊到句子的開頭、結尾，切點要自己聽準";
  return `${rvRuleHint(t["類型"], t["老師整段"], true)}（跟第 3 步一樣）`;
}

function fcBindRetime(r) {
  const t = r["改範圍"] || {};
  const go = document.getElementById("fc-go3");
  if (go) go.addEventListener("click", () => rvJump({ key: t["第3步"], back: "step5", backKey: r["鍵"],
    note: `從第 5 步成品檢查過來：${t["可以"] ? "改這一筆" : t["下一步"] || ""}` }));
  if (!t["可以"] || !document.getElementById("fc-retime")) return;
  const range = t["方式"] === "重念範圍";
  const gen = t["方式"] === "學員起訖";   // 10-01 第三批：重疊的「生成學員聲音」會換掉的範圍
  const raw = fcRawTime(t);              // 照填的時間、不對齊（見 fcRawTime）
  const before = [t.start, t.end];
  const ctx = {
    host: "fc-retime",
    st: { kind: t["類型"], id: t.id, a: t.start, b: t.end, busy: false, result: fc.retimeResult || null },
    o: {
      aria: "調整時間範圍",
      head: () => `<span class="rv-meta">〈${esc(fcWho(t["名稱"]))}〉${range ? "的重念範圍" : gen ? "會換成學員生成聲音的範圍" : ""}</span>`,
      hint: () => fcRetimeHint(t),
      now: () => (fc.video ? Math.round(fcSrcTime(fc.video.currentTime) * 100) / 100 : null),
      nowLabel: "用影片目前位置（換成原片時間）",
      play: (a, b) => { fc.video.pause(); fc.audio.src = `/api/audio?start=${a.toFixed(2)}&end=${b.toFixed(2)}`; fc.audio.play().catch(() => {}); },
      api: range ? "/api/review/name" : gen ? "/api/review/overlap" : "/api/review/manual",
      body: (st) => (range ? { id: t.id, "整句起訖": [st.a, st.b] } : gen ? { id: t.id, "學員起訖": [st.a, st.b] }
        : { "類型": t["類型"], id: t.id, start: st.a, end: st.b, ...(raw ? { "不對齊": true } : {}) }),
      toResult: (res, st) => (range || gen ? { "不對齊": true, start: st.a, end: st.b } : { ...res["對齊結果"], "新增": false, ...(raw ? { "不對齊": true } : {}) }),
      saved: async (res, out) => {
        if (Math.abs(out.start - before[0]) < 0.01 && Math.abs(out.end - before[1]) < 0.01) {
          // 10-01 走查：對齊之後跟原本一樣，以前還是寫「範圍改了：A → A」、標成要重做
          fc.retimeResult = { error: `對齊之後還是原本的 ${fcFmt(before[0])}–${fcFmt(before[1])}，範圍沒有改；要改的話把起點或終點多移一點` };
          fc.redoOpen = true;
          fcRenderRight();
          fc.retimeResult = null;
          return;
        }
        const box = document.getElementById("fc-reason");
        const typed = box ? box.value.trim() : "";
        const why = (typed && !typed.startsWith("時間範圍改成") ? typed : "") || `時間範圍改成 ${fcFmt(out.start)}–${fcFmt(out.end)}（原本 ${fcFmt(before[0])}–${fcFmt(before[1])}）`;
        await apiPost("/api/final/item", { "鍵": r["鍵"], "結果": "退回重做", "原因": why,
          "改範圍": { "名稱": t["名稱"], "原本": before, "改成": [out.start, out.end], "第3步": t["第3步"], "重做": t["重做"] } });
        fc.retimeResult = out;
        fc.redoOpen = true;
        await fcReload();
        fc.retimeResult = null;
      },
      saveLabel: () => "儲存修改",
    },
  };
  teRender(ctx);
}

// 10-03 第八批 #63：試聽怎麼播（純函式，tests/test_finalcheck_web.py 用 node 跑）。前後各多 2 秒（跟後端 clip 一樣）。
// 處理後：影片本身從那一筆起點前 2 秒播到終點後 2 秒（聲音就是成品）；
// 處理前：播原聲（後端 clip），影片從同一筆的成品起點前 2 秒同步播、靜音（剪掉的那種成品裡沒有，影片不動）。
const FC_AB_CONTEXT = 2;
function fcAbPlan(r, which) {
  const p = r["成品"], o = r["原片"];
  const has = p && p[0] != null;
  const from = has ? Math.max(0, p[0] - FC_AB_CONTEXT) : null;
  if (which === "後") {
    if (!has) return null;
    const end = p[1] != null ? p[1] : p[0];
    return { video: { from, to: end + FC_AB_CONTEXT, muted: false }, audio: null };
  }
  const url = `/api/final/clip?which=${encodeURIComponent("前")}&key=${encodeURIComponent(r["鍵"])}`;
  const len = o && o[1] != null ? o[1] - o[0] + 2 * FC_AB_CONTEXT : null;
  return { video: has ? { from, to: len != null ? from + len : null, muted: true } : null, audio: url };
}

function fcPlayAB(r, which) {
  const plan = fcAbPlan(r, which);
  if (!plan) return;
  fcStopAB();
  fc.audio.pause();
  const v = fc.video;
  fc.ab = { which, from: plan.video ? plan.video.from : null, to: plan.video ? plan.video.to : null, muted: v.muted };   // 記住原本有沒有靜音，播完還原
  if (plan.video) {
    v.muted = plan.video.muted;
    fcSeek(plan.video.from);
    v.play().catch(() => {});
  } else v.pause();
  if (plan.audio) { fc.audio.src = plan.audio; fc.audio.play().catch(() => {}); }
  document.querySelectorAll("[data-ab]").forEach((b) => b.classList.toggle("on", b.dataset.ab === which));
}

// 試聽結束（按停、原聲播完、影片播到終點後 2 秒）：原聲停、影片停下、靜音還原。pause=false：影片照播（自己拖走了）
function fcStopAB(pause = true) {
  const ab = fc.ab;
  if (!ab) return;
  fc.ab = null;
  fc.audio.pause();
  if (pause) fc.video.pause();
  fc.video.muted = ab.muted;
  document.querySelectorAll("[data-ab]").forEach((b) => b.classList.remove("on"));
}

// 每次 timeupdate：播到試聽終點就停；自己拖到別的地方（範圍外超過 1 秒）就不算試聽了，原聲停、靜音還原、影片照播
function fcAbTick(t) {
  const ab = fc.ab;
  if (!ab || ab.from == null) return;
  if (t < ab.from - 1 || (ab.to != null && t >= ab.to + 1)) fcStopAB(false);
  else if (ab.to != null && t >= ab.to) fcStopAB();
}

// 10-03 第八批 #64：「跳到」框。成品時間直接跳；原片時間請後端換算（finalcheck.goto_output_time，跟頁面其他地方同一支）
async function fcGoto(box, kind) {
  const msg = document.getElementById("fc-goto-msg");
  const t = rvParseTime(box.value);
  box.classList.toggle("bad", t == null);
  if (msg) msg.textContent = "";
  if (t == null) return;
  if (kind !== "原片") { fcSeek(t); box.blur(); return; }
  try {
    const r = await apiGet(`/api/final/goto?src=${t}`);
    if (r["成品秒"] == null) { if (msg) msg.textContent = r["說明"]; return; }
    fcSeek(r["成品秒"]);
    if (msg) msg.textContent = r["說明"] ? `${r["說明"]}（成品 ${fcFmt(r["成品秒"])}）` : `原片 ${fcFmt(t)}＝成品 ${fcFmt(r["成品秒"])}`;
    box.blur();
  } catch (e) { if (msg) msg.textContent = e.message; }
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
  const all = fcShown().length ? fcShown() : fcRecs();   // 10-02 第六批：開了「只看要人聽的」就只在那幾筆之間換
  const k = fcStepPick(all, fc.cur, fc.video ? fc.video.currentTime : 0, dir);
  if (k) fcSelect(k);
}

// 10-08（純函式）：上一筆／下一筆要選哪一筆。目前這一筆在清單裡：照順序前後一筆（到頭就停在頭）；
// 不在清單裡（影片跟著播到別頁的那一筆）：「下一筆」選成品時間在 t 之後的第一筆、「上一筆」選 t 之前的最後一筆；
// 找不到（後面、前面都沒有了）就選最後一筆／第一筆。沒有成品時間的（成品裡剪掉、名字沒處理）照清單位置不參加時間比較
function fcStepPick(list, curKey, t, dir) {
  if (!list.length) return null;
  const i = list.findIndex((x) => x["鍵"] === curKey);
  if (i >= 0) return list[Math.max(0, Math.min(list.length - 1, i + dir))]["鍵"];
  const at = (x) => (x["成品"] && x["成品"][0] != null ? x["成品"][0] : null);
  if (dir > 0) {
    const n = list.find((x) => at(x) != null && at(x) > t + 0.05);
    return (n || list[list.length - 1])["鍵"];
  }
  const p = list.filter((x) => at(x) != null && at(x) < t - 0.05).pop();
  return (p || list[0])["鍵"];
}

// 10-08（純函式）：這一頁都通過了、別頁還有沒通過的 → 回傳還沒通過的筆數（顯示「到『還沒通過』」）；不用顯示回傳 0。
// 「全部」「還沒通過」兩頁不顯示（前者都通過＝全部都通過；後者本來就是那一頁）
function fcTabDoneLeft(tabId, tabRecs, allRecs) {
  if (tabId === FC_TAB_ALL || tabId === FC_TAB_TODO || tabId === FC_TAB_KEEP || tabId === FC_TAB_ATT || !tabRecs.length) return 0;
  if (tabRecs.some((r) => r["結果"] !== "通過")) return 0;
  return allRecs.filter((r) => r["結果"] !== "通過").length;
}

// 10-08：影片跟著播到別頁的那一筆時不自動切頁；分頁列下面寫一行「目前這一筆在『某某』頁」（可以點），那一頁的按鈕亮一個小點
function fcRenderCurHint() {
  const box = document.getElementById("fc-curhint");
  if (!box || !fc.data) return;
  document.querySelectorAll(".fc-tabs button.fc-dot").forEach((b) => b.classList.remove("fc-dot"));
  const tabs = fcTabs(), tab = fcPickTab(tabs, fcTabGet());
  const here = tabs.find((x) => x.id === tab);
  const has = here && here.recs.some((r) => r["鍵"] === fc.cur);
  const other = fc.cur && fcRec(fc.cur) && !has ? tabs.find((x) => x.id.startsWith("組:") && x.recs.some((r) => r["鍵"] === fc.cur)) : null;
  if (!other) { box.innerHTML = ""; return; }
  const btn = document.querySelector(`.fc-tabs button[data-tab="${CSS.escape(other.id)}"]`);
  if (btn) btn.classList.add("fc-dot");
  box.innerHTML = `<button class="linkish" id="fc-curhint-go">目前這一筆在「${esc(other["名稱"])}」頁</button>`;
  document.getElementById("fc-curhint-go").addEventListener("click", () => { fcTabSet(other.id); fcRenderLower(); });
}

// 10-03 第八批 #65（純函式）：目前這一筆之後第一筆還沒看的；後面都看了才繞回前面找；全部看了回傳 null。
// only：只在這幾個鍵裡找（「只看要人聽的」），null＝全部。
function fcNextUnseen(recs, curKey, only = null) {
  const ok = (x) => !x["結果"] && x["鍵"] !== curKey && (!only || only.includes(x["鍵"]));
  const i = recs.findIndex((x) => x["鍵"] === curKey);
  const hit = recs.slice(i + 1).find(ok) || recs.slice(0, Math.max(0, i)).find(ok);
  return hit ? hit["鍵"] : null;
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
    // 10-03 第八批 #65：從目前這一筆往後找（以前從頭找，前面跳過沒看的會一直被拉回去）；開了「只看要人聽的」只在那幾筆裡找
    // 10-08：只在目前這一頁（分頁）裡找；在「全部」又沒開篩選就是全部
    const next = fcNextUnseen(fcRecs(), r["鍵"], fcOnlyLook() || fcTab() !== FC_TAB_ALL ? fcShown().map((x) => x["鍵"]) : null);
    if (next) fc.cur = next;
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
      <p class="rv-meta">看過 ${esc(fcFmt(st["看過秒數"], 0))}／${esc(fcFmt(st["成品長度"], 0))}。只算用 2 倍速以下真的播過的地方（拖過去跳過的、超過 2 倍速快轉的都不算）。看到 100% 才算檢查完；沒看完也能輸出，按「輸出成品」會先問一次。</p>
      <div class="rv-field"><label>看到問題：寫一句原因，按下去就在目前時間建一筆退回重做
        <input id="fc-flag-why" placeholder="例如：這裡聲音突然變小"></label></div>
      <div class="rv-actions"><button class="primary" id="fc-flag">這裡有問題（退回重做）</button></div>
      ${flags.length ? `<ul class="fc-flags">${flags.map((x) => `<li><button class="linkish" data-t="${x["成品秒"]}">${esc(fcFmt(x["成品秒"]))}</button>
        ${esc(x["原因"])}${(x["覆核名稱"] || []).length ? `<span class="rv-meta">（${esc(fcWho(x["覆核名稱"].join("、")))}）</span>` : ""}
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

// ---------- 下面：沒登記的變動＋分頁（全部、還沒通過、每一種類型、第 3 步選定不修改） ----------

// 10-08 宇軒：下方照第 3 步的樣子用分頁切換類型。分頁列：全部 → 還沒通過 → 每一組（1008-1 的組，有筆數的才列）
// → 第 3 步選定不修改（有才列）。目前在哪一頁記在瀏覽器（localStorage，讀不到就回「全部」）
const FC_TAB_KEY = "fc-tab";
const FC_TAB_ALL = "全部", FC_TAB_TODO = "還沒通過", FC_TAB_KEEP = "不修改", FC_TAB_ATT = "需留意";

// 「有修改的」照類型分組（純函式）。groups＝GET /api/final 的「修改類型」（順序、名稱）；
// 紀錄沒有「組」（舊的後端）或組不在清單裡的歸「其他」。回傳 [{組, 名稱, 說明, recs, 通過, 要人看}]，沒有紀錄的組不列。
function fcGroupRecs(recs, groups) {
  const defs = (groups && groups.length ? groups : [{ "組": "其他", "名稱": "處理紀錄", "說明": "" }]).slice();
  if (!defs.some((g) => g["組"] === "其他")) defs.push({ "組": "其他", "名稱": "其他", "說明": "" });
  const known = new Set(defs.map((g) => g["組"]));
  const by = new Map(defs.map((g) => [g["組"], []]));
  for (const r of recs) by.get(known.has(r["組"]) ? r["組"] : "其他").push(r);
  return defs.filter((g) => by.get(g["組"]).length).map((g) => {
    const list = by.get(g["組"]);
    return { ...g, recs: list, "通過": list.filter((r) => r["結果"] === "通過").length, "要人看": list.filter((r) => r["要人看"]).length };
  });
}

// 「第 3 步有卡片但選定不修改」照子類分組（純函式）。kinds＝「不修改類型」（順序、名稱、怎麼改）
function fcGroupUnchanged(rows, kinds) {
  const defs = (kinds || []).slice();
  const by = new Map(defs.map((k) => [k["子類"], []]));
  for (const r of rows || []) {
    if (!by.has(r["子類"])) { defs.push({ "子類": r["子類"], "名稱": r["子類"], "去改": "" }); by.set(r["子類"], []); }
    by.get(r["子類"]).push(r);
  }
  return defs.filter((k) => by.get(k["子類"]).length).map((k) => ({ ...k, rows: by.get(k["子類"]) }));
}

// 分頁列（純函式）：[{id, 名稱, 說明, recs（這一頁的處理紀錄，不管「只看要人聽的」）, 通過, 要人看, 筆數}]。
// 「還沒通過」＝還沒按通過的（目前這一筆按了也先留著，跟第 3 步「還沒確認」一樣，不會一按就從清單消失）
// 10-08 宇軒（流程簡化）：最後一頁「⚠️ 需留意」（att＝GET /api/final 的「需留意」；舊的後端沒有就不列）
function fcTabList(recs, groups, unchanged, curKey, att) {
  const pack = (id, name, note, list) => ({ id, "名稱": name, "說明": note, recs: list, "筆數": list.length,
    "通過": list.filter((r) => r["結果"] === "通過").length, "要人看": list.filter((r) => r["要人看"]).length });
  const tabs = [pack(FC_TAB_ALL, "全部", "", recs),
    pack(FC_TAB_TODO, "還沒通過", "", recs.filter((r) => r["結果"] !== "通過" || r["鍵"] === curKey))];
  for (const g of fcGroupRecs(recs, groups)) tabs.push(pack(`組:${g["組"]}`, g["名稱"], g["說明"], g.recs));
  if ((unchanged || []).length) tabs.push({ id: FC_TAB_KEEP, "名稱": "第 3 步選定不修改", "說明": "", recs: [], "筆數": unchanged.length, "通過": 0, "要人看": 0, "不修改": true, "清單外": true });
  if (att) tabs.push({ id: FC_TAB_ATT, "名稱": "⚠️ 需留意", "說明": "第 4 步總檢查略過的、成品裡還留著原聲的地方（不擋輸出）", recs: [],
    "筆數": (att["列"] || []).length, "名字": (att["列"] || []).filter((r) => r["名字"]).length, "通過": 0, "要人看": 0, "需留意": true, "清單外": true });
  return tabs;
}
// 目前在哪一頁：記住的那一頁現在沒有了（例如那一類都沒了）就回「全部」（純函式）
function fcPickTab(tabs, want) { return tabs.some((t) => t.id === want) ? want : FC_TAB_ALL; }
function fcTabGet() { try { return localStorage.getItem(FC_TAB_KEY) || FC_TAB_ALL; } catch (e) { return FC_TAB_ALL; } }
function fcTabSet(id) { try { localStorage.setItem(FC_TAB_KEY, id); } catch (e) { /* 存不了也沒關係 */ } }
function fcTabs() { return fcTabList(fcRecs(), fc.data["修改類型"], fc.data["不修改"], fc.cur, fc.data["需留意"]); }
function fcTab() { return fcPickTab(fcTabs(), fcTabGet()); }
// 一筆處理紀錄在哪一頁：目前這一頁有它就留著，不然去它那一組（從第 3 步回來、時間軸點到別類的）
function fcTabOf(key) {
  const tabs = fcTabs(), cur = fcTab();
  const has = (t) => t.recs.some((r) => r["鍵"] === key);
  const here = tabs.find((t) => t.id === cur);
  if (here && has(here)) return cur;
  const g = tabs.find((t) => t.id.startsWith("組:") && has(t));
  return g ? g.id : FC_TAB_ALL;
}

function fcRecRow(r) {
  return `<li data-key="${esc(r["鍵"])}" class="${r["鍵"] === fc.cur ? "cur" : ""} ${r["結果"] ? "done" : ""}">
      <span class="tm">${r["原片"] ? `原片 ${esc(fcFmt(r["原片"][0]))}` : "—"}<br><small>${fcHasTime(r) ? `成品 ${esc(fcFmt(r["成品"][0]))}` : r["原片"] ? "成品裡沒有" : ""}</small></span>
      <span class="ty"><span class="rv-chip fc-${FC_TYPE[r["類型"]] || "ov"}"><i></i>${esc(fcKind(r["類型"]))}</span></span>
      <span class="tx">${fcIdTag(r)}${r["重做過"] ? `<span class="rv-tag">${esc(r["重做過"]["標籤"] || "重做過")}${r["重做過"]["第幾版"] && r["重做過"]["做法"] === "重新生成" ? `・第 ${r["重做過"]["第幾版"]} 版` : ""}</span>` : ""}${esc(fcWho(r["做了什麼"]))}</span>
      <span class="sg">${esc(fcWho((r["覆核名稱"] || []).join("、")))}</span>
      <span class="st ${r["結果"] === "通過" ? "ok" : ""}">${r["結果"] === "通過" ? "✓ 通過" : r["結果"] === "退回重做" ? "退回" : "—"}</span></li>`;
}

function fcUnchangedRow(u, i) {
  const out = u["成品"] && u["成品"][0] != null;
  const more = u["處數"] > 1 ? `（共 ${u["處數"]} 處，時間是第一處）` : "";
  const cut = u["剩下秒"] != null && u["原片"] && u["剩下秒"] < u["原片"][1] - u["原片"][0] - 0.05
    ? `<span class="rv-meta">其中 ${esc(fcFmt(u["剩下秒"], 1))} 秒照原聲（其餘被別筆修改、剪掉）</span>` : "";
  return `<li data-ukey="${esc(u["鍵"])}" class="fc-un-row">
      <span class="tm">原片 ${esc(fcFmt(u["原片"][0]))}<br><small>${out ? `成品 ${esc(fcSpan(u["成品"]))}` : ""}</small></span>
      <span class="tx">${esc(fcWho(u["名稱"]))}${u["學員"] ? `（${esc(fcWho(u["學員"]))}）` : ""}${esc(more)} ${cut}</span>
      <span class="rv-row">${out ? `<button class="ghost small fc-u-play" data-i="${i}">跳過去聽</button>` : ""}
        <button class="ghost small fc-u-go" data-i="${i}">回第 3 步改</button></span></li>`;
}

// 分頁按鈕上的徽章（10-08 宇軒：只寫「完成 x／n」，要人聽幾筆不寫在上面——「只看要人聽的」篩選在清單裡）：
// 處理紀錄的頁寫「完成 x／n」；「還沒通過」只寫筆數；不修改、需留意兩頁不用按通過，只寫幾處
function fcTabBadge(t) {
  if (t["清單外"]) return `<span>${t["筆數"]}</span>`;
  if (t.id === FC_TAB_TODO) return `<span>${t["筆數"] - t["通過"]}</span>`;   // 跟第 3 步「還沒確認」一樣只寫筆數
  return `<span>完成 ${t["通過"]}／${t["筆數"]}</span>`;
}

// ---------- 10-08 宇軒（流程簡化）：「⚠️ 需留意」——第 4 步總檢查預設略過、成品裡還留著原聲的地方 ----------
// 名字換不了代號紅字置頂（會念出本名）；其他依類別分組。每一列：時間（原片／成品）、卡片名稱、還留著幾秒、「跳過去聽」「回第 3 步改」

const FC_ATT_MAX_PLAY = 4;   // 剩下好幾段時，最多放幾顆「跳過去聽」
function fcAttentionRow(r, i) {
  const segs = r["成品段"] || [];
  const plays = segs.slice(0, FC_ATT_MAX_PLAY).map((sg, j) => `<button class="ghost small fc-a-play" data-i="${i}" data-s="${j}">跳過去聽${segs.length > 1 ? ` ${esc(fcFmt(sg[0]))}` : ""}</button>`).join("");
  const name = r["卡片名稱"] ? `〈${esc(fcWho(r["卡片名稱"]))}〉` : "";
  const part = r["原片"] && r["剩下秒"] < r["原片"][1] - r["原片"][0] - 0.05 ? `（其餘被別筆處理蓋到）` : "";
  return `<li class="${r["名字"] ? "fc-att-name" : ""}" data-akey="${esc(r["鍵"] || "")}">
      <span class="tm">原片 ${esc(fcSpan(r["原片"]))}<br><small>成品 ${esc(fcSpan(r["成品"]))}</small></span>
      <span class="tx">${name}還留著 ${esc(fcFmt(r["剩下秒"], 1))} 秒原聲${part}${segs.length > 1 ? `，${segs.length} 段` : ""}</span>
      <span class="rv-row">${plays}<button class="ghost small fc-a-go" data-i="${i}">回第 3 步改</button></span></li>`;
}

// 需留意這一頁（純函式，flat 收集每一列給按鈕用）
function fcAttentionHtml(att, kinds, flat) {
  att = att || {};
  const rows = att["列"] || [];
  if (!att["有紀錄"]) return `<p class="rv-meta">這支成品是舊版工具組裝的，當時沒有記下第 4 步略過了哪幾列。重新組裝一次（第 4 步「只重新組裝」）之後這裡才會列。</p>`;
  const head = `<p class="rv-warnline">這一頁會顯示學員本名，截圖或分享螢幕時請注意。</p>
    <p class="rv-meta">第 4 步開始執行時「開始前總檢查」沒處理（預設略過）的地方，扣掉被學員重念、名字換掉、剪掉、消音蓋到的部分，只列成品裡還留著原聲的。不用按通過、不擋輸出；想確認就按「跳過去聽」，要改就「回第 3 步改」，改完要重新組裝。</p>
    ${att["第4步沒處理"] != null ? `<p class="rv-meta">這次執行時總檢查有 ${att["第4步沒處理"]} 列沒處理（含請看一眼），其中 ${rows.length} 處成品裡還留著原聲。</p>` : ""}`;
  if (!rows.length) return `${head}<p>沒有要留意的地方。</p>`;
  const defs = (kinds || []).slice();
  for (const r of rows) if (!defs.some((k) => k["類別"] === r["類別"])) defs.push({ "類別": r["類別"], "名稱": r["類別"], "說明": "" });
  const put = (r) => { flat.push(r); return fcAttentionRow(r, flat.length - 1); };
  const names = rows.filter((r) => r["名字"]);
  const red = names.length ? `<section class="fc-att-red"><h3>⚠️ 這 ${names.length} 處會照原聲念出本名</h3>
      <p class="rv-meta">程式在句子裡找不到名字、換不了代號。要換掉：回第 3 步把要重念的句子改好（名字寫成代號），或改成直接消音，再重新組裝。</p>
      <ul class="fc-attlist">${names.map(put).join("")}</ul></section>` : "";
  const groups = defs.filter((k) => k["類別"] !== "名字換不了代號").map((k) => {
    const list = rows.filter((r) => !r["名字"] && r["類別"] === k["類別"]);
    return list.length ? `<h3 class="fc-sub">${esc(k["名稱"])}　${list.length} 處<span class="rv-meta">${esc(k["說明"] || "")}</span></h3><ul class="fc-attlist">${list.map(put).join("")}</ul>` : "";
  }).join("");
  return `${head}${red}${groups}`;
}

// 「回第 3 步改」帶去哪裡（純函式）：有卡片就定位到那一張；沒有卡片（聲紋、沒有字）帶著這段時間到「新增修改」
function fcAttentionGo(r) {
  const kind = r["類別"] || "";
  const note = `從第 5 步「需留意」過來：${kind}（原片 ${fcSpan(r["原片"])}）；改完要回第 4 步重新組裝`;
  if (r["第3步"]) return { key: r["第3步"], openMore: String(r["第3步"]).startsWith("重疊:"), back: "step5", backKey: r["鍵"], note };
  const a = (r["剩下"] && r["剩下"][0]) || r["原片"];
  return { key: null, back: "step5", backKey: r["鍵"], note,
    edit: { "類型": kind === "沒有字" ? "名字" : "學員發言", start: a[0], end: a[1] } };
}

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
  const tabs = fcTabs();
  const tab = fcPickTab(tabs, fcTabGet());
  const t = tabs.find((x) => x.id === tab);
  const only = fcOnlyLook();
  const nav = `<nav class="rv-filters fc-tabs" aria-label="類型">${tabs.map((x) =>
    `<button class="${x.id === tab ? "on" : ""}" data-tab="${esc(x.id)}" title="${esc(x["說明"] || "")}">${esc(x["名稱"])} ${fcTabBadge(x)}</button>`).join("")}</nav>`;
  const flat = [];
  let body;
  if (t["需留意"]) {
    body = fcAttentionHtml(fc.data["需留意"], fc.data["需留意類型"], flat);
  } else if (t["不修改"]) {
    const uRows = fc.data["不修改"] || [];
    body = `<p class="rv-meta">第 3 步看過、決定照原聲留著的地方。不用按通過、不擋輸出；想確認就按「跳過去聽」（只播那一段），要改就「回第 3 步改」，改完要重新組裝。</p>
      <p class="rv-warnline">這一頁會顯示學員本名，截圖或分享螢幕時請注意。</p>
      <div class="fc-ugroups" id="fc-ulist">${fcGroupUnchanged(uRows, fc.data["不修改類型"]).map((k) => {
        const lis = k.rows.map((u) => { flat.push(u); return fcUnchangedRow(u, flat.length - 1); }).join("");
        return `<h3 class="fc-sub">${esc(k["名稱"])}　${k.rows.length} 筆<span class="rv-meta">${esc(k["去改"] || "")}</span></h3><ul class="fc-ulist">${lis}</ul>`;
      }).join("")}</div>`;
  } else {
    const lookN = t.recs.filter((r) => r["要人看"]).length;
    const shown = fcShown();
    body = `<div class="rv-row fc-filter"><label class="nowrap"><input type="checkbox" id="fc-only" ${only ? "checked" : ""}> 只看要人聽的（${lookN} 筆）</label>
      <span class="rv-meta">${t["說明"] ? `${esc(t["說明"])}。` : ""}要人聽＝生成檢查沒過、放不進時間格（標紅）、名字沒有自動處理的。${only ? `現在列 ${shown.length}／${t.recs.length} 筆。` : ""}</span></div>
      <ol class="rv-list" id="fc-list">${shown.map(fcRecRow).join("") || `<li class="empty">${only ? "這一頁沒有要人聽的。" : "這一頁沒有處理紀錄。"}</li>`}</ol>`;
  }
  const left = t["清單外"] ? 0 : fcTabDoneLeft(tab, t.recs, fcRecs());
  const done = left ? `<p class="fc-tabdone"><button class="linkish" id="fc-go-todo">這一頁都通過了，還有 ${left} 筆沒通過（到「還沒通過」）</button></p>` : "";
  lower.innerHTML = `${unHtml}${nav}<div class="fc-curhint rv-meta" id="fc-curhint" role="status"></div>${done}${body}`;
  const goTodo = document.getElementById("fc-go-todo");
  if (goTodo) goTodo.addEventListener("click", () => { fcTabSet(FC_TAB_TODO); fcRenderLower(); });
  lower.querySelectorAll(".fc-tabs button").forEach((b) => b.addEventListener("click", () => { fcTabSet(b.dataset.tab); fcRenderLower(); }));
  const onlyBox = document.getElementById("fc-only");
  if (onlyBox) onlyBox.addEventListener("change", () => { fcSetOnlyLook(onlyBox.checked); fcRenderLower(); });
  lower.querySelectorAll("#fc-list li[data-key]").forEach((li) => li.addEventListener("click", () => { fc.mode = "逐筆"; fcRenderAll(); fcSelect(li.dataset.key); }));
  lower.querySelectorAll(".fc-u-play").forEach((b) => b.addEventListener("click", () => { const u = flat[Number(b.dataset.i)]; fcPlaySpan(u["成品"][0], u["成品"][1]); }));
  lower.querySelectorAll(".fc-u-go").forEach((b) => b.addEventListener("click", () => {
    const u = flat[Number(b.dataset.i)];
    const kind = (fc.data["不修改類型"] || []).find((k) => k["子類"] === u["子類"]) || {};
    rvJump({ key: u["第3步"] || null, back: "step5", backKey: u["鍵"],
      note: `從第 5 步成品檢查過來：〈${fcWho(u["名稱"])}〉選定不修改。${kind["去改"] || ""}；改完要回第 4 步重新組裝` });
  }));
  lower.querySelectorAll(".fc-a-play").forEach((b) => b.addEventListener("click", () => { const u = flat[Number(b.dataset.i)]; const sg = u["成品段"][Number(b.dataset.s)]; fcPlaySpan(sg[0], sg[1]); }));
  lower.querySelectorAll(".fc-a-go").forEach((b) => b.addEventListener("click", () => rvJump(fcAttentionGo(flat[Number(b.dataset.i)]))));
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
  fcRenderCurHint();
  const list = document.getElementById("fc-list");
  if (!list) return;
  list.querySelectorAll("li.cur").forEach((li) => li.classList.remove("cur"));
  const li = fc.cur && list.querySelector(`li[data-key="${CSS.escape(fc.cur)}"]`);
  if (!li) return;
  li.classList.add("cur");
  const top = li.getBoundingClientRect().top - list.getBoundingClientRect().top + list.scrollTop;
  if (top < list.scrollTop || top + li.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = Math.max(0, top - list.clientHeight / 3);
}

// 10-08：「跳過去聽」只播那一段（成品時間 a～b），播到 b 自動停；中途自己跳走就不管
const FC_SPAN_WAIT_MS = 10000;   // 10-08：按了之後影片一直沒跳到那一段（載入太慢等），等超過 10 秒就放掉
function fcPlaySpan(a, b) {
  if (fc.ab) fcStopAB(false);
  const span = { a: Number(a), b: Math.max(Number(a) + 0.3, Number(b)), armed: false, since: Date.now() };
  fc.span = span;
  fcSeek(span.a, true);
  setTimeout(() => { if (fc.span === span && !span.armed) fc.span = null; }, FC_SPAN_WAIT_MS + 50);
}
// 純函式：目前時間 t 對「只播那一段」的處理 → "wait"（還沒跳到）、"play"、"stop"（播到結尾，暫停）、"drop"（人跳走了）
function fcSpanStep(span, t, now) {
  if (!span) return "drop";
  if (!span.armed) {
    if (t >= span.a - 0.3 && t <= span.b) return "play";
    return now != null && span.since != null && now - span.since > FC_SPAN_WAIT_MS ? "drop" : "wait";   // 等太久：放掉
  }
  if (t < span.a - 0.5 || t > span.b + 1) return "drop";
  if (t >= span.b) return "stop";
  return "play";
}
function fcSpanTick(t) {
  if (!fc.span) return;
  const s = fcSpanStep(fc.span, t, Date.now());
  if (s === "play") fc.span.armed = true;
  else if (s === "stop") { fc.span = null; fc.video.pause(); }
  else if (s === "drop") fc.span = null;
}

// ---------- 看過比例：自己記播過的區段（2 倍速以下、連續播的才算），送給後端取聯集 ----------
// 不用 video.played：它連 16 倍速快轉的也算進去。

function fcTrack() {   // 每次 timeupdate：這一小段是正常播過去的就接上，跳過、快轉、暫停就斷開
  const v = fc.video;
  const t = v.currentTime;
  const ok = !v.paused && !v.seeking && v.playbackRate <= FC_MAX_RATE && !(fc.ab && fc.ab.which === "前");   // 試聽處理前時影片是靜音的，不算看過
  if (ok && fc.seg && t >= fc.seg[1] && t - fc.seg[1] < 1.0) { fc.seg[1] = t; return; }
  fcEndSeg();
  if (ok) fc.seg = [t, t];
}

function fcEndSeg() {
  if (fc.seg && fc.seg[1] > fc.seg[0]) fc.seen.push([Number(fc.seg[0].toFixed(3)), Number(fc.seg[1].toFixed(3))]);
  fc.seg = null;
}

function fcPlayedRanges() {
  const out = fc.seen.slice();
  if (fc.seg && fc.seg[1] > fc.seg[0]) out.push([Number(fc.seg[0].toFixed(3)), Number(fc.seg[1].toFixed(3))]);
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
  if (!confirm(`把 ${n} 筆退回的送回 AI 重做？\n到第 4 步按「只重做退回的這幾筆」（或「開始執行」），會只重做這幾筆、再重新組裝。`)) return;
  try { await apiPost("/api/final/sendback", {}); } catch (e) { alert(e.message); return; }
  await fcReload();
}

// 10-08 宇軒放寬輸出：沒看完、沒全部通過也可以輸出，按下去先問一次（都達標就直接輸出不問）。
// 後端回「要確認」＋「還差」；按「照目前的狀態輸出」才帶 確定 再送一次
async function fcExport() {
  let r;
  try { r = await apiPost("/api/final/export", {}); } catch (e) { alert(e.message); return; }
  if (r["要確認"]) {
    let win = false;
    try { win = !!(await apiGet("/api/final/outputs"))["可以複製到Windows"]; } catch (e) { /* 讀不到就不提 Windows */ }
    if (!(await fcConfirmExport(r["還差"], win))) return;
    try { r = await apiPost("/api/final/export", { "確定": true }); } catch (e) { alert(e.message); return; }
  }
  await fcReload();
  fileOutInit(document.getElementById("fc-fileout"));   // 「打開成品資料夾」「複製到 Windows」改指向剛輸出的這一支
}

// 確認視窗的文字（純函式）：還差什麼一句一句寫、最後問要不要照目前的狀態輸出；Windows（WSL）多提醒要再按複製那一顆。
// 10-08 審查：有「擋下」的（程式處理不了的名字沒按通過、名字／重疊退回沒重做、標字版）只說要先做什麼、不給輸出（block）；
// 其他退回重做還沒重做的放最前面紅字（redoHead），底下列每一筆的時間與原因（redo，只顯示）；第 4 步正在重做的寫在 warn
function fcExportAsk(gaps, win) {
  const g = gaps || {};
  const why = g["說明"] || [], block = g["擋下"] || [], redo = g["退回清單"] || [], warn = g["提醒"] || [];
  if (block.length) {
    return { blocked: true, title: "還不能輸出", body: `${block.join("；")}。`, note: "處理好之後再按一次「輸出成品」。",
      redoHead: "", redo: [], warn: [], names: [], att: "", win: "" };
  }
  // 10-08 宇軒（流程簡化）：名字不再擋，紅字列出（可以確認輸出）；「需留意」多一句
  return { blocked: false, title: why.length ? "還沒檢查完，確定要輸出嗎？" : "確定要輸出嗎？",
    names: g["名字提醒"] || [], att: g["需留意說明"] || "",
    redoHead: redo.length ? `有 ${redo.length} 筆退回重做還沒重做，輸出的是重做之前的樣子` : "",
    redo: redo.map((x) => ({ t: x["成品秒"] != null ? x["成品秒"] : x["原片秒"], kind: x["成品秒"] != null ? "成品" : "原片", why: x["原因"] || "" })),
    warn,
    body: `${why.length ? why.join("、") + "。" : ""}確定要以目前的狀態輸出嗎？`,
    note: "輸出的是現在檢查的這一支成品（照目前的樣子），會另外存一支新的，舊的不會被蓋掉。之後要重新輸出可以再按一次。",
    win: win ? "成品在 Ubuntu 裡：輸出之後，再按「複製成品到 Windows 的下載資料夾」，才拿得到。" : "" };
}

function fcConfirmExport(gaps, win) {
  return new Promise((resolve) => {
    const a = fcExportAsk(gaps, win);
    const dlg = document.createElement("dialog");
    dlg.className = "rv-confirm";
    dlg.setAttribute("aria-labelledby", "fc-export-title");
    dlg.innerHTML = `<h2 id="fc-export-title">${esc(a.title)}</h2>
      ${(a.names || []).map((x) => `<p class="fc-export-names"><b>${esc(x)}</b></p>`).join("")}
      ${a.redoHead ? `<p class="rv-warnline"><b>${esc(a.redoHead)}</b></p>` : ""}
      ${a.att ? `<p class="rv-warnline" id="fc-export-att">${esc(a.att)}（第 5 步「⚠️ 需留意」那一頁）</p>` : ""}
      ${a.warn.map((x) => `<p class="rv-warnline">${esc(x)}</p>`).join("")}
      <p>${esc(a.body)}</p>
      ${a.redo.length ? `<ul class="fc-export-redo">${a.redo.map((x) => `<li>${esc(x.kind)} ${esc(x.t != null ? fcFmt(x.t) : "—")}　${esc(x.why)}</li>`).join("")}</ul>` : ""}
      <p class="muted">${esc(a.note)}</p>${a.win ? `<p><b>${esc(a.win)}</b></p>` : ""}
      <div class="rv-confirm-btns">${a.blocked ? `<button class="primary" id="fc-export-no">知道了</button>`
        : `<button class="ghost" id="fc-export-no">回去繼續檢查</button><button class="primary" id="fc-export-yes">照目前的狀態輸出</button>`}</div>`;
    document.body.appendChild(dlg);
    const done = (v) => { dlg.close(); dlg.remove(); resolve(v); };
    dlg.addEventListener("cancel", (e) => { e.preventDefault(); done(false); });   // Esc＝回去繼續檢查
    dlg.querySelector("#fc-export-no").addEventListener("click", () => done(false));
    const yes = dlg.querySelector("#fc-export-yes");
    if (yes) yes.addEventListener("click", () => done(true));
    dlg.showModal();
  });
}

// 輸出紀錄當時沒檢查完的提示（純函式）：「這次輸出時還沒看完（看過 73%）、還有 12 筆沒通過」；檢查完才輸出的、舊紀錄沒這幾欄的回空字串
function fcExportedEarly(rec) {
  if (!rec || rec["檢查完才輸出"] !== false) return "";
  const parts = [];
  if (rec["看過百分比"] != null && rec["看過百分比"] < 100) parts.push(`還沒看完（看過 ${rec["看過百分比"]}%）`);
  if (rec["沒通過筆數"]) parts.push(`還有 ${rec["沒通過筆數"]} 筆沒通過`);
  if (rec["沒確認變動筆數"]) parts.push(`還有 ${rec["沒確認變動筆數"]} 處沒登記的變動沒確認`);
  return parts.length ? `這次輸出時${parts.join("、")}` : "這次輸出時還沒檢查完";
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
