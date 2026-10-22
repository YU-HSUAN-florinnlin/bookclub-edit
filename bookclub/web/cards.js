"use strict";

/* 卡片元件（10-15 第5步2.0 第2段：從第 3 步 review.js 抽出來，第 3 步「覆核工作台」與第 5 步「成品檢查」共用）。
 * 一張卡的內容（rvBodyHtml）、「改做法」那一塊（rvMoreHtml）、卡片上的工具按鈕（改時間、不是名字、這段其實是老師、
 * 改成老師重念、刪掉這一筆：cvToolBtnsHtml）、各種存檔（第 3 步原本的 API）、起訖編輯器（te*）都在這裡。
 * 函式名稱沿用第 3 步的 rv* 前綴（其他程式、測試、文件都用這個名字找）。
 *
 * 元件不碰哪一頁的全域狀態，只透過「情境」（cvUse(情境) 設定；同一時間畫面上只有一頁）：
 *   mode        "第3步" | "第5步"
 *   data()      第 3 步的資料（GET /api/review 的那一份，或 GET /api/final 的「第3步」）：項目、學員、這一集代號、選項、代號選項…
 *   reload()    改完重新讀資料、重畫
 *   post(url, body, opts)  存檔（第 3 步＝apiPost；第 5 步另外記「這張卡改過了」）
 *   play(a, stop)          從原片 a 秒播到 stop 秒（第 3 步播原片影片；第 5 步播原聲）
 *   videoTime()            「用目前播放位置」的原片秒數（沒有影片＝null）
 *   seek(t)                跳到原片 t 秒
 *   select(key, opts)      換到某一張卡
 *   openEditor(it, [a,b])  打開「改時間」面板（[a,b]＝先填好的起訖，可省略）
 *   prefill(類型, a, b, 說話者, 說明)  打開「新增修改」面板、起訖先填好
 *   askOutside(before)     切段之後問「切在外面的幾秒是誰的聲音」（沒有＝不問）
 *   refreshSug()           做法改了，更新建議框
 *   moreOpen() / toggleMore()  「改做法」那一塊開著沒有／開關
 *   rerender()             重畫目前這張卡
 * 第 3 步的情境在 review.js（RV_CARD_CTX），第 5 步的在 finalcheck.js（FC_CARD_CTX）。
 * 第 3 步畫面與行為跟抽出來之前一模一樣：tests/test_cards_snapshot.py 用抽之前的快照鎖住。
 * 10-15 前後 2 秒開關統一（1015-5）：宇軒授權改第 3 步卡片畫面（播放按鈕改名「聽這一段」＋「前後多 2 秒」開關），快照照新畫面重產。
 * 依賴 app.js 的 apiPost／esc。 */

const cv = { ctx: null };
function cvUse(ctx) { cv.ctx = ctx; }
function cvData() { return cv.ctx.data(); }
function cvItems() { return cvData()["項目"]; }
function cvItem(key) { return cvItems().find((x) => rvKey(x) === key); }
function cvReload() { return cv.ctx.reload(); }
function cvPost(url, body, opts) { return cv.ctx.post(url, body, opts); }
function cvPlay(a, stop) { cv.ctx.play(a, stop); }
function cvVideoTime() { return cv.ctx.videoTime(); }
function cvSelect(key, opts) { cv.ctx.select(key, opts); }
function cvOpenEditor(it, rng) { cv.ctx.openEditor(it, rng); }
function cvPrefill(kind, a, b, who, note) { cv.ctx.prefill(kind, a, b, who, note); }
function cvAskOutside(before) { if (cv.ctx.askOutside) cv.ctx.askOutside(before); }
// 10-16 #225：第 5 步的「復原」——改之前記下原本的樣子（第 3 步沒有這個，什麼都不做）
function cvNoteUndo(u) { if (cv.ctx.noteUndo) cv.ctx.noteUndo(u); }
function cvTurnSnap(it) {
  const ta = document.getElementById("rv-text");
  return { id: it.id, who: it["說話者"], text: (it["已確認"] ? it["校對稿"] : it["建議稿"]) || "", ok: !!it["已確認"], typed: ta ? ta.value : null };
}
function cvRefreshSug() { if (cv.ctx.refreshSug) cv.ctx.refreshSug(); }

// ---------------------------------------------------------------------------
// 10-15 前後 2 秒開關統一（1015-5）：第 3 步、第 5 步所有「播卡片範圍」的按鈕共用同一顆「前後多 2 秒」滑動開關
// （以前只有第 5 步的「原本 ▶」「現在 ▶」有；第 3 步各按鈕各自寫死前後 0、0.3、0.5 秒）。
// 開關狀態記在瀏覽器、兩步共用（沿用第 5 步原本的鍵 fc-ab-pad，宇軒設過的不會不見）；預設關。
// 開著：前後各多 2 秒；關著：只播範圍本身；只有一個時間點（不到 0.05 秒）的照樣前後 2 秒（不然什麼都聽不到）。
// 一張卡只放一顆開關（卡片層級），面板裡的播放鈕（改做法、要修改、改時間面板的「聽這一段」）照同一個狀態。
// ---------------------------------------------------------------------------

const CV_PAD_KEY = "fc-ab-pad";
const CV_PAD_SEC = 2;
function cvPadOn() { try { return localStorage.getItem(CV_PAD_KEY) === "1"; } catch (e) { return false; } }
function cvPad() { return cvPadOn() ? CV_PAD_SEC : 0; }
function cvSetPad(on) {
  try { localStorage.setItem(CV_PAD_KEY, on ? "1" : "0"); } catch (e) { /* 存不了也沒關係 */ }
  document.querySelectorAll("[data-cv-pad]").forEach((x) => { x.checked = !!on; });   // 畫面上別的開關（例如跳出來的問題視窗）跟著換
}
// [a, b] 加上前後秒數（b 沒有＝只有一個時間點）
function cvPadRange(a, b, pad = cvPad()) {
  const end = b != null ? b : a;
  const k = end - a < 0.05 ? Math.max(pad, CV_PAD_SEC) : pad;
  return [Math.max(0, a - k), end + k];
}
// 滑動開關（iOS 樣式，樣式在 style.css 的 .fc-pad／.fc-switch）
function cvPadHtml(id) {
  return `<label class="fc-pad"><input type="checkbox" role="switch" id="${esc(id)}" data-cv-pad aria-label="前後多聽 2 秒" ${cvPadOn() ? "checked" : ""}><span class="fc-switch" aria-hidden="true"></span>前後多聽 2 秒</label>`;
}
function cvBindPad(id, root = null) {   // root：不在頁面上的元素（例如跳出來的視窗）
  const el = root ? root.querySelector(`#${id}`) : document.getElementById(id);
  if (el) el.addEventListener("change", () => cvSetPad(el.checked));
}
// 播卡片的一段範圍（照開關加前後）
function cvPlayRange(a, b) { const [x, y] = cvPadRange(a, b); cvPlay(x, y); }

// ---------------------------------------------------------------------------
// 卡片上的工具按鈕（第 3 步在「通過、改做法」後面；第 5 步收在「要修改」→「其他」／「裡面有老師的聲音」裡面，10-15 要修改合併（1015-4）、驗證回饋二）
// ---------------------------------------------------------------------------

// step5：第 5 步的字（10-15：學員段落的「這段其實是老師」寫成「裡面有老師的聲音」；這一段先做成整段改回老師原聲，
// 選哪幾個字是老師的（#226）之後做）
function cvToolBtnsHtml(it, step5 = false) {
  const teacher = step5 ? `<button class="ghost" id="rv-isteacher" title="這一段改回老師的原聲，不重念（整段）">裡面有老師的聲音</button>`
    : `<button class="ghost" id="rv-isteacher" title="聲音辨識判錯：這一段其實是老師在講話">這段其實是老師</button>`;
  return [
    RV_ITEM_KIND[it["類型"]] ? `<button class="ghost" id="rv-retime" title="用「新增修改」面板改這一筆的起點終點">改時間</button>` : "",
    it["類型"] === "名字" ? `<button class="${rvNotName(it) ? "primary" : "ghost"}" id="rv-notname" aria-pressed="${rvNotName(it)}" title="抓錯了，這裡其實沒有人名：照原音不改，這個寫法以後也不會再抓（已選的再按一次取消）">${rvNotName(it) ? "✓ " : ""}${it["老師整段"] ? "不用改，保留老師原聲" : "不是名字，不用改"}</button>` : "",
    it["類型"] === "學員段落" ? teacher : "",
    it["類型"] === "改成老師" ? `<button class="ghost" id="rv-tai" title="這一段照打的字老師重念（用老師的 AI 聲音，例如裡面有名字）">改成老師重念</button>` : "",
    it["可以刪"] ? `<button class="ghost" id="rv-del" title="人工新增的加錯了：整筆刪掉（會留一筆紀錄，在「設定」看得到）">刪掉這一筆</button>` : "",
  ].join("\n        ");
}

// 工具按鈕＋卡片狀態列裡「取消剪掉」「跳到那一筆」的動作
function cvBindTools(it) {
  const retime = document.getElementById("rv-retime");
  if (retime) retime.addEventListener("click", () => {
    // 10-01 第三批：重疊選生成學員聲音（自己生成）：要改的是「會換掉的範圍」，打開改做法裡的那一格
    if (it["類型"] === "重疊" && it["生成範圍"] && rvChosen(it) === "只留學員") {
      if (!cv.ctx.moreOpen()) cv.ctx.toggleMore();
      const box = document.getElementById("rv-genbox");
      if (box) { box.scrollIntoView({ block: "nearest" }); const f = box.querySelector("input"); if (f) f.focus({ preventScroll: true }); }
      return;
    }
    cvOpenEditor(it);
  });
  const notName = document.getElementById("rv-notname");
  if (notName) notName.addEventListener("click", async () => {
    const off = rvNotName(it);
    const tags = (it.tags || []).filter((g) => g !== "不是名字" && g !== "是地名").concat(off ? [] : ["不是名字"]);
    await rvSaveName(it, { tags, "已確認": !off });
    it["已確認"] = !off;
    cv.ctx.rerender();
  });
  const isT = document.getElementById("rv-isteacher");
  if (isT) isT.addEventListener("click", async () => {
    // 09-29 宇軒：聲音辨識把老師判成學員時，一鍵改回老師（只有一部分是老師：先用「改做法」裡的「從游標處切開」）
    // 10-15：第 5 步的按鈕叫「改」（不是「改做法」）；整段改回老師原聲，選字（#226）之後做
    // 10-15 要修改合併（1015-4）：第 5 步這顆在「要修改」→「其他」裡，切開也在同一塊
    const where = cv.ctx.mode === "第5步" ? "改按「從游標處切開」" : "先按「改做法」→「從游標處切開」";
    if (!(await uiConfirm({ title: `把 ${rvFmt(it.start, 1)}–${rvFmt(it.end, 1)} 這一段改成老師？`, danger: true, ok: "整段改成老師", cancel: "先不要",
      message: `改了之後這段不會重念，也會補找這段裡老師提到的名字。\n只有一部分是老師的話，${where}。` }))) return;
    cvNoteUndo({ kind: "teacher", ...cvTurnSnap(it) });
    await cvToTeacher(it);
  });
  const uncut = document.getElementById("rv-uncut");   // 10-01 2-4：「通過＝剪掉」產生的剪掉片段，在原本那一段取消
  if (uncut) uncut.addEventListener("click", async () => {
    if (!(await uiConfirm({ title: "取消剪掉？", message: "這一段的聲音和畫面留著，改成學員重念。", ok: "不剪了，改成學員重念", cancel: "照舊剪掉" }))) return;
    await cvPost("/api/review/cut", { id: it["剪掉的片段"], "狀態": "還原" });
    await cvPost("/api/turns/save", { id: it.id, "短句保留": true });
    await cvReload();
  });
  const goCover = document.getElementById("rv-gocover");
  if (goCover) goCover.addEventListener("click", () => {
    const c = it["涵蓋"];
    const hit = cvItems().find((x) => x.id === c.id || (x["類型"] === "名字" && `S${String(x.id).padStart(3, "0")}` === c.id)
      || (x["類型"] === "重疊" && (c["重疊項目"] || []).includes(x.id)))
      || cvItems().find((x) => x.start <= c.start + 0.05 && c.end - 0.05 <= x.end && x !== it);
    if (hit) cvSelect(rvKey(hit)); else cv.ctx.seek(c.start);
  });
  const tai = document.getElementById("rv-tai");   // 10-01 2-3：改成老師的段落 → 老師 AI 重念
  if (tai) tai.addEventListener("click", async () => {
    if (!(await uiConfirm({ title: `${rvFmt(it.start, 1)}–${rvFmt(it.end, 1)} 這一段改成老師重念？`, message: "用老師的 AI 聲音重念。新增後在那一筆卡片上確認要念的字。", ok: "改成老師重念", cancel: "先不要" }))) return;
    try { await cvPost("/api/review/manual", { "類型": "學員發言", start: it.start, end: it.end, "說話者": "老師" }); } catch (err) { uiAlert({ title: "沒有成功", message: err.message }); return; }
    await cvReload();
  });
  const del = document.getElementById("rv-del");
  if (del) del.addEventListener("click", () => rvDeleteManual(it));
}

// 學員段落整段改成老師（存檔、重新整理、補找到名字的提醒）。10-15 要修改合併（1015-4）：從按鈕抽出來，
// （10-15 宇軒驗證回饋二：第 5 步「裡面有老師的聲音」改成跟「其他」一樣帶出整個選單，改回老師走上面那顆按鈕，「確定」不再呼叫這裡）
async function cvToTeacher(it) {
  await rvSaveTurnText(it);
  const res = await cvPost("/api/turns/save", { id: it.id, "說話者": "老師" });
  if (res["補找到的老師名字"]) uiToast(`這段裡補找到 ${res["補找到的老師名字"]} 個老師提到的名字，已經加進清單。`);
  await cvReload();
  if (res["改成老師重念"]) {   // 09-30：沒有逐字稿句子的段落，沒辦法自動找名字 → 改成一筆「老師這一段用 AI 聲音重念」
    uiAlert({ title: "請確認要念的字", message: "這一段沒有逐字稿句子，沒辦法自動找名字：已經改成「老師這一段用 AI 聲音重念」，請確認要念的字。\n不用重念的話，按「不用改，保留老師原聲」。" });
    const key = `名字:${res["改成老師重念"]}`;
    if (cvItem(key)) cvSelect(key, { seek: false });
  }
  return res;
}

// 「聽這一段」（以前叫「只播這一段」）「照建議縮小」
function cvBindPlayShrink(it) {
  const only = document.getElementById("rv-playonly");   // 10-04 #130：從卡片起點剛好開始播，播到終點停（10-15 1015-5：照開關加前後 2 秒）
  if (only) only.addEventListener("click", (e) => {
    e.stopPropagation();
    cvPlayRange(Number(only.dataset.s), Number(only.dataset.e));
  });
  const shrink = document.getElementById("rv-shrink");
  if (shrink) shrink.addEventListener("click", () => rvShrink(it));
}

// ---------------------------------------------------------------------------
// 以下從第 3 步 review.js 原封不動搬過來（只把全域的 rv.data／rvReload／apiPost／播放 換成情境）
// ---------------------------------------------------------------------------

const RV_TYPE = {
  "學員段落": { cls: "stu", label: "學員段落" },
  "名字": { cls: "name", label: "老師提到名字" },
  "學員名字": { cls: "name", label: "學員提到名字" },   // 09-29：保留原聲的學員自己講到名字
  "重疊": { cls: "ov", label: "重疊" },
  "刪除段落": { cls: "cut", label: "剪掉（連畫面）" },   // 10-01 宇軒：要看得出來連畫面都剪；第三批 7：三頁統一叫「剪掉」
  "局部消音": { cls: "mute", label: "消音（只拿掉聲音）" },
  "改成老師": { cls: "tfix", label: "改成老師（原聲）" },   // 10-01：人改成老師的段落照樣列出來
};

const rvIsName = (t) => t === "名字" || t === "學員名字";

const RV_STU_SHADES = ["#3f8f5a", "#6aae7f", "#2d6b43", "#8cc49d", "#4f9d6b", "#1f5434"];

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

// 10-04 #128：跟 AI 助手溝通用的內部編號（跟 `bookclub inspect` 的寫法一樣：T027、名字 35、重疊 O2754.10、剪掉 S3、消音 M001），
// 小字灰色顯示。只顯示長得像程式編號的（同 safeview.safe_id 的編號規則）；對不上、缺欄位就不顯示，不丟例外
const RV_ID_RE = /^(?:[A-Za-z]{0,4}\d+(?:[._m]\d+)*(?:_學員|_老師)?|\d+\.\d+)$/;

const RV_ID_LABEL = { "學員段落": "", "改成老師": "", "名字": "名字 ", "學員名字": "學員名字 ", "重疊": "重疊 ", "刪除段落": "剪掉 ", "局部消音": "消音 " };

function rvIdTag(it) {
  try {
    const id = it && it.id != null ? String(it.id) : "";
    const pre = it ? RV_ID_LABEL[it["類型"]] : undefined;
    if (pre === undefined || !RV_ID_RE.test(id)) return "";
    // 名字卡併了好幾筆（同一句同代號）：主卡編號顯示出來，其他幾筆放在滑鼠移上去的提示
    const mates = rvIsName(it["類型"]) && Array.isArray(it["同一張卡候選"])
      ? it["同一張卡候選"].map(String).filter((k) => k !== id && RV_ID_RE.test(k)) : [];
    const title = mates.length ? `同一張卡：${mates.join("、")}` : "跟 AI 助手溝通用的編號";
    return `<span class="idtag" title="${esc(title)}">${esc(pre + id)}</span>`;
  } catch (e) { return ""; }
}

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

function rvStudents() { return Object.keys(cvData()["學員"] || {}); }

function rvStuColor(who) {
  const i = rvStudents().indexOf(who);
  return RV_STU_SHADES[(i < 0 ? 0 : i) % RV_STU_SHADES.length];
}

// 10-07 宇軒：卡片上的「學員 N」改顯示「本名（代號）」（不加開關）；沒選本名的照舊；
// 兩位學員 N 選了同一個本名，後面加「・學員 N」分得出來（rvWhoHtml 用小字）
function rvWhoParts(name) {
  if (!name) return ["（不知道是誰）", ""];
  if (name === "老師") return ["老師", ""];
  const all = cvData()["學員"] || {};
  const p = all[name] || {};
  const real = p["本名"];
  const code = (real && (cvData()["這一集代號"] || {})[real]) || p["代號"];
  if (!real) return [code ? `${name}（${code}）` : name, ""];
  const twin = Object.entries(all).some(([n, q]) => n !== name && q["本名"] === real);
  return [`${real}（${code || "還沒代號"}）`, twin ? `・${name}` : ""];
}

function rvWho(name) { return rvWhoParts(name).join(""); }

function rvWhoHtml(name) {
  const [a, b] = rvWhoParts(name);
  return esc(a) + (b ? `<small class="rv-meta">${esc(b)}</small>` : "");
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
  if (it["類型"] === "學員段落") return s["做法"] === "刪除這段" ? "剪掉這段（聲音和畫面都拿掉，影片會變短）" : "學員重念";   // 10-16 #225：「（逐字稿沒問題就通過）」跟下面那行重複
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

// 10-04 #130（宇軒）：每一種卡片都有「只播這一段」：從卡片的 start 剛好開始播、播到 end 停（點卡片跳過去是從前 2 秒開始播）
// 名字卡播的是名字那一小段（重念範圍另有「只播重念範圍」）；重疊卡播的是重疊的那一段（兩邊各自的範圍在「改做法」裡「播這一段」）
// 10-15 前後 2 秒開關統一（1015-5）：改名「聽這一段」，旁邊放這張卡唯一的一顆「前後多 2 秒」開關（名字卡的開關在重念範圍那一列）
function rvPlayOnlyHtml(it) {
  const a = Number(it && it.start), b = Number(it && it.end);
  if (!it || it.start == null || it.end == null || !Number.isFinite(a) || !Number.isFinite(b) || b <= a) return "";
  // 10-16 #225 宇軒：名字卡的「聽這一段」＋開關也放標題列（跟其他卡一樣），播重念範圍；第 5 步照舊在重念範圍那一列
  const w = it["整句"];
  if (it["類型"] === "名字" && (!w || it["老師整段"] || (cv.ctx && cv.ctx.mode === "第5步"))) return "";
  const [s0, e0] = it["類型"] === "名字" ? [Number(w.start), Number(w.end)] : [a, b];
  return ` <button class="ghost small" id="rv-playonly" data-s="${s0}" data-e="${e0}" title="聽 ${esc(rvFmt(s0, 1))}–${esc(rvFmt(e0, 1))}（開了「前後多聽 2 秒」就前後各多 2 秒）">聽這一段</button>${cvPadHtml("rv-pad")}`;
}

// 10-02 第六批：老師重念範圍開頭或結尾有一段沒有人講話：提醒＋「照建議縮小」（不自動改；改了這一句要重新生成）
function rvEdgeHtml(it) {
  const h = it["前後沒聲音"];
  if (!h) return "";
  return `<div class="rv-note rv-edge" id="rv-edge"><p>${esc(h["說明"])}。</p>
    ${h["可以縮"] ? `<button class="ghost small" id="rv-shrink">照建議縮小</button> <span class="rv-meta">按了會把重念範圍改成建議的範圍；這一句之後要重新生成</span>`
      : `<p class="rv-meta">${esc(h["原因"] || "")}</p>`}</div>`;
}

async function rvShrink(it) {
  const h = it["前後沒聲音"];
  if (!(await uiConfirm({ title: `把重念範圍改成 ${rvFmt(h["建議"][0], 1)}–${rvFmt(h["建議"][1], 1)}？`, message: "這一句之後要重新生成。", ok: "照建議縮小", cancel: "先不要" }))) return;
  try { await cvPost("/api/review/shrink", { "鍵": h["鍵"] }); } catch (err) { uiAlert({ title: "沒有成功", message: err.message }); return; }
  await cvReload();
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
    const t = cvVideoTime(); if (t != null) save(b.dataset[`${prefix}now`], Math.round(t * 100) / 100);
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
    : w["範圍"] === "逐字" ? "照整句（句子文字裡找不到名字，改用逐字時間的字）"
    : w["範圍"] === "延伸" ? `句子斷在話中間，往前後延伸到標點或停頓（0.3 秒以上）；原本那一句 ${rvFmt(whole[0], 1)}–${rvFmt(whole[1], 1)}`
    : "照整句";
  // 10-15 前後 2 秒開關統一（1015-5）：「只播重念範圍」改「聽這一段」＋「前後多 2 秒」開關（第 5 步卡片上已經有一顆，這裡不再放）；
  // 「回到預設範圍」是重設起訖、不是播放：改成次要樣式，放在起訖欄下面
  return `<div class="rv-field rv-range"><div class="rv-edrow"><b>重念範圍</b><span class="rv-meta">${esc(tag)}</span>
      ${cv.ctx && cv.ctx.mode === "第5步" ? `<button class="ghost small" id="rv-playrange">聽這一段</button>` : ""}</div>
      ${rvTimeRows("rg", [["0", "起點", w.start], ["1", "終點", w.end]])}
      ${w["範圍"] === "人選" ? `<div class="rv-edrow"><button class="linkish rv-reset" id="rv-rangereset">回到預設範圍</button></div>` : ""}</div>`;
}

async function rvSaveRange(it, a, b) {
  const w = it["整句"];
  if (b - a < 0.3) { uiAlert({ title: "時間不對", message: "重念範圍的結束要晚於開始" }); return; }
  const body = { id: it.id, "整句起訖": [a, b] };
  // 1022-1（#263）：兩顆按鈕直接寫做法；關掉視窗（✕、Esc）＝保留你改的字（跟以前按「取消」一樣）
  if (w["改稿"] && await uiConfirm({ title: "重念範圍改了", ok: "照新範圍重排", cancel: "保留我改的字",
    message: "要念的句子要不要照新範圍的逐字稿重排？\n照新範圍重排：你改過的字會被蓋掉。\n保留我改的字：要念的句子不動。" })) body["改稿"] = "";
  try { await cvPost("/api/review/name", body); } catch (err) { uiAlert({ title: "沒有成功", message: err.message }); return; }
  await cvReload();
}

function rvSidesHtml(it) {   // 10-01 B 方案：兩邊各自的起訖＋兩列小時間條（上列老師、下列學員，只用來看相對位置）
  const T = it["老師起訖"], S = it["學員起訖"];
  const lo = Math.min(T[0], S[0]), hi = Math.max(T[1], S[1]), span = Math.max(0.1, hi - lo);
  const bar = (r) => `<i style="left:${((r[0] - lo) / span) * 100}%;width:${Math.max(1, ((r[1] - r[0]) / span) * 100)}%"></i>`;
  return `<div class="rv-field rv-sides" id="rv-sides">
      <div class="rv-minibar" aria-hidden="true"><div class="row t">${bar(T)}</div><div class="row s">${bar(S)}</div></div>
      <p class="rv-meta">上列老師、下列學員（${esc(rvFmt(lo, 1))}–${esc(rvFmt(hi, 1))}）。兩段各自生成、放回原本的時間，疊到的地方混在一起。
        <button class="ghost small" id="rv-sideplay">聽這一段</button></p>
      ${rvTimeRows("sd", [["老師起訖:0", "老師 起點", T[0]], ["老師起訖:1", "老師 終點", T[1]], ["學員起訖:0", "學員 起點", S[0]], ["學員起訖:1", "學員 終點", S[1]]])}</div>`;
}

const RV_OUT = [["老師不用處理", "老師的話，不用處理"], ["老師重念", "老師的話，要用老師聲音重念"],
  ["還是學員", "還是學員的聲音"], ["好幾個人", "還有好幾個人的聲音"]];

function rvOutQHtml(it, q, i) {
  const d = q.end - q.start;
  const opts = RV_OUT.map(([v, label]) => `<button class="${q["答案"] === v ? "primary" : "ghost"} small rv-outans" data-i="${i}" data-v="${esc(v)}" aria-pressed="${q["答案"] === v}">${esc(label)}</button>`).join(" ");
  const later = `<button class="${q["答案"] ? "ghost" : "primary"} small rv-outans" data-i="${i}" data-v="" aria-pressed="${!q["答案"]}">先不回答</button>`;
  const follow = q["答案"] === "老師重念"
    ? `<p class="rv-warnline">還沒有老師重念這幾秒的那一筆。<button class="ghost small rv-outgo" data-i="${i}" data-go="teacher">新增老師重念這幾秒（起訖已經填好）</button></p>`
    : q["答案"] === "還是學員"
      ? `<p class="rv-warnline">那就不該切在這裡：<button class="ghost small rv-outgo" data-i="${i}" data-go="back">把這一段的起訖改回去包住這幾秒</button>
          或 <button class="ghost small rv-outgo" data-i="${i}" data-go="student">另外新增一筆漏抓的發言</button></p>`
      : q["答案"] === "好幾個人"
        ? `<p class="rv-warnline">要再切開：一個人一段。<button class="ghost small rv-outgo" data-i="${i}" data-go="split">切出其中一段（帶去新增修改，起訖先填成這幾秒）</button>
            把起點或終點縮到只有一個人講話的那一段再新增；剩下沒處理的幾秒會再問一次。</p>`
        : q["答案"] === "老師不用處理" ? `<p class="rv-meta">照原聲留著；第 4 步總檢查會列成「已確認」（灰色），不用再處理，要改答案在那裡也能改。</p>`
          : `<p class="rv-meta">先不回答的話，第 4 步總檢查會列出這幾秒。</p>`;
  return `<div class="rv-field rv-outq"><p><b>切在段落外面的 ${esc(rvFmt(q.start, 1))}–${esc(rvFmt(q.end, 1))}（${d.toFixed(1)} 秒）是誰的聲音？</b>
      <button class="ghost small rv-outplay" data-i="${i}">聽這一段</button></p>
    <p class="rv-meta">${q["整句"] ? "這一段切短的時候，原本屬於這一段的一句整句落在段落外面" : "這一段的句子有一部分在段落外面"}，沒有任何處理蓋到：照現在的樣子會留著原聲。</p>
    <div class="rv-row">${opts} ${later}</div>${follow}</div>`;
}

async function rvAnswerOutside(it, q, v) {
  try { await cvPost("/api/review/outside", { "鍵": q["鍵"], start: q.start, end: q.end, "答案": v || null }); }
  catch (err) { uiAlert({ title: "沒有成功", message: err.message }); return false; }
  await cvReload();
  if (v === "老師重念") cvPrefill("學員發言", q.start, q.end, "老師", "下面的起訖已經填好、說話的人選了老師：聽過沒問題按「新增」，這幾秒就會用老師的聲音重念");
  if (v === "好幾個人") cvPrefill("學員發言", q.start, q.end, it["說話者"], "把起點或終點縮到只有一個人講話的那一段，選是誰說的，再按「新增」；剩下的幾秒會再問一次");
  return true;
}

function rvBindOutQ(it) {
  const qs = it["段落外"] || [];
  document.querySelectorAll(".rv-outans").forEach((b) => b.addEventListener("click", () => rvAnswerOutside(it, qs[Number(b.dataset.i)], b.dataset.v)));
  document.querySelectorAll(".rv-outplay").forEach((b) => b.addEventListener("click", () => {
    const q = qs[Number(b.dataset.i)]; cvPlayRange(q.start, q.end);   // 10-15 1015-5：以前寫死前後 0.3 秒，改照開關
  }));
  document.querySelectorAll(".rv-outgo").forEach((b) => b.addEventListener("click", () => {
    const q = qs[Number(b.dataset.i)];
    if (b.dataset.go === "teacher") cvPrefill("學員發言", q.start, q.end, "老師");
    else if (b.dataset.go === "student") cvPrefill("學員發言", q.start, q.end, it["說話者"]);
    else if (b.dataset.go === "split") cvPrefill("學員發言", q.start, q.end, it["說話者"], "把起點或終點縮到只有一個人講話的那一段，選是誰說的，再按「新增」；剩下的幾秒會再問一次");
    else cvOpenEditor(it, [Math.min(it.start, q.start), Math.max(it.end, q.end)]);
  }));
}

function rvOutKeys() {   // 現在畫面上所有「切在外面的幾秒」的鍵（切之前記下來，切完比對哪幾段是新冒出來的）
  return new Set(cvItems().flatMap((x) => (x["段落外"] || []).map((q) => `${q["鍵"]}|${q.start}|${q.end}`)));
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
  // 10-15 1015-5：「播這一段」改「聽這一段」；「回到預設」跟名字卡一樣改次要樣式、放在起訖欄下面
  return `<div class="rv-edrow"><b>會換掉的範圍</b><span class="rv-meta">${esc(RV_GEN_SRC[g["來源"]] || "")}</span>
      <button class="ghost small" id="rv-genplay">聽這一段</button></div>
    ${rvTimeRows("gs", [["0", "起點", g.start], ["1", "終點", g.end]])}
    ${g["來源"] !== "學員整句" ? `<div class="rv-edrow"><button class="linkish rv-reset" id="rv-genreset">回到預設（學員那一整句）</button></div>` : ""}
    <p class="rv-meta">照你填的時間存，不會自動對齊。這段時間整段換成學員的生成聲音，裡面老師的聲音不保留。</p>`;
}

// 10-17 #247：名字被拿掉（沒有代號、換成空的）的地方畫面上標「（拿掉）」——只在畫面上，不進要念的文字、不送後端
const RV_DROP_MARK = "（拿掉）";

function rvMarkChanges(text, changes) {
  // 標出自動換成代號的字（位置是新文字裡的索引）；換成空的（名字拿掉）標「（拿掉）」
  let out = "", i = 0;
  for (const c of changes || []) {
    out += esc(text.slice(i, c["位置"])) + (c["換成"]
      ? `<mark title="原本是「${esc(c["原字"])}」">${esc(c["換成"])}</mark>`
      : `<mark class="rv-drop" title="${c["原字"] ? `原本是「${esc(c["原字"])}」，` : ""}名字拿掉了">${RV_DROP_MARK}</mark>`);
    i = c["位置"] + c["換成"].length;
  }
  return out + esc(text.slice(i));
}

function rvMarkDrops(text, at) {
  // 10-17 #247：老師名字卡「會念成」：在名字被拿掉的位置（新文字裡的索引）插入「（拿掉）」標記，其他字照樣跳脫
  if (!(at || []).length) return esc(text);
  return rvMarkChanges(text, at.map((p) => ({ "位置": p, "原字": "", "換成": "" })));
}

function rvBodyHtml(it) {
  const t = it["類型"];
  if (t === "學員段落") {
    const text = it["已確認"] ? it["校對稿"] : it["建議稿"];
    const rows = Math.min(9, Math.max(3, Math.ceil((text || "").length / 34)));
    // 10-16 #225 宇軒：加「學員」「會念成」小標（跟名字卡一樣）；落在剪掉片段裡的不會重念，字照列、不能改
    const [whoA, whoB] = rvWhoParts(it["說話者"]);
    const whoM = /^(.*)（(.*)）$/.exec(whoA);
    const who = (whoM ? `${esc(whoM[1])}<span class="rv-meta">（代號 ${esc(whoM[2])}）</span>` : esc(whoA)) + (whoB ? `<small class="rv-meta">${esc(whoB)}</small>` : "");
    const say = rvInCut(it)
      ? `<p class="rv-quote rv-cuttext"><span class="rv-lab">逐字稿</span>${esc(text)}</p>`
      : `<div class="rv-edrow"><span class="rv-lab">會念成</span><span class="rv-meta">可以直接改在下面框內</span></div>
      <textarea id="rv-text" rows="${rows}" aria-label="學員重念要念的字（可以直接改）">${esc(text)}</textarea>`;
    return `<p class="rv-who"><span class="rv-lab">學員</span>${who}${it["手動標記"] ? "　（人工標記的段落）" : ""}</p>
      ${say}
      ${(it["換過的字"] || []).length && !it["已確認"] ? `<p class="rv-note">${(it["換過的字"]).some((c) => !c["換成"]) ? "自動換掉的地方" : "自動換成代號的地方"}：${rvMarkChanges(it["建議稿"], it["換過的字"])}</p>` : ""}
      ${it["代號改過"] ? `<p class="rv-warnline">代號改過，請再看一次（原本的代號：${esc(it["代號改過"])}，別人也在用，沒有自動換）。</p>` : ""}
      ${it["含本名"] ? `<p class="rv-warnline">文字裡還有名冊上的本名，要換成代號。</p>` : ""}
      ${it["聲紋多半是老師"] ? `<p class="rv-warnline">${esc(it["聲紋多半是老師"]["提醒"])}</p>` : ""}
      ${it["問老師"] ? `<p class="rv-note">已標「聽不清楚，問老師」${it["問老師備註"] ? "：" + esc(it["問老師備註"]) : ""}</p>` : ""}
      ${(it["段落外"] || []).map((q, i) => rvOutQHtml(it, q, i)).join("")}`;
  }
  if (t === "學員名字") {
    const w = it["整句"];
    return `<p class="rv-who">${rvWhoHtml(it["學員"])}（保留原聲）講到名字</p><p class="rv-quote">${it.sentence_html}</p>
      ${it["沒英文名照原聲"] ? `<p class="rv-note">${it["做法"] === "換成代號" ? "" : "選「換成代號」時："}照原聲留著：這個名字這一集還沒有英文名，到「開始前 4 件事」③ 選</p>`   // 10-18 複審：跟老師名字卡一樣，照實寫
        : w ? `<p class="rv-note">選「換成代號」時，用${esc(rvWho(it["學員"]))}自己的聲音重念這句：${esc(w["換成代號"] || w["原文"])}${w["換成代號"] ? "" : "（句子裡找不到比對到的字，會退回直接消音）"}</p>` : ""}
      <p class="rv-meta">代號 ${esc(it["代號"] || "（沒有）")}${it["信心"] === "低" ? "　低信心，先聽清楚是不是名字" : ""}</p>`;
  }
  if (t === "名字" && it["老師整段"]) {   // 09-30：人工標的一段老師的話，整段用老師 AI 聲音重念
    const w = it["整句"];
    return `<p class="rv-who">老師這一段（${esc(rvFmt(w.start, 1))}–${esc(rvFmt(w.end, 1))}）老師重念（用老師的 AI 聲音），原本的聲音整段換掉</p>
      <div class="rv-field"><label>要念的字（名字要寫成代號；範圍內老師講的話都要寫進來，學員的話不要寫）
        <textarea id="rv-namesay" rows="2" data-auto="${esc(w["換成代號"] || "")}">${esc(w["改稿"] || w["換成代號"] || "")}</textarea></label>
        <span class="rv-meta" id="rv-namesay-st">${w["改稿"] ? RV_ST_EDITED : w["換成代號"] ? "狀態：照逐字稿自動排的（名冊上的本名已換成代號），請對照聲音確認" : "逐字稿這段沒有字，請打上要念的字；空白的話整段會消音"}</span>
        ${w["實際會念"] ? `<p class="rv-warnline">文字裡還有名冊上的名字，生成時會自動${(w["實際會念拿掉"] || []).length ? "換成代號或拿掉" : "換成代號"}。實際會念：${rvMarkDrops(w["實際會念"], w["實際會念拿掉"])}</p>` : ""}</div>
      <p class="rv-meta">這一段不用重念、要保留老師原聲：按「不用改，保留老師原聲」。</p>`;
  }
  if (t === "名字") {
    // 10-16 #225 宇軒：決定放上面、細節收起來。原句 → 會念成 → 也可能是 → 「調整重念的字和範圍」（預設收起；有問題、改過、第 5 步自動打開）
    const w = it["整句"];
    const skip = rvNotName(it) || rvInCut(it);   // 不會重念：收合區整個不出現
    const say = w ? (w["改稿"] || w["換成代號"] || w["原文"]) : "";
    const problem = w && (!(w["改稿"] || w["換成代號"]) || w["字太少"] || w["實際會念"]);
    const step5 = cv.ctx && cv.ctx.mode === "第5步";
    const key = rvKey(it);
    const open = step5 || problem || (cv.nameFold || {})[key];
    const low = it["信心"] === "低" ? `<p class="rv-warnline">低信心，先聽清楚是不是名字</p>` : "";
    // 10-18 #246 修法 5：沒有英文名的卡照原聲留著（不重念、名字不會被拿掉）；人改了要念的字就照人改的
    const keepOrig = !skip && w && it["沒英文名照原聲"] && !w["改稿"] && rvChosen(it) !== "直接消音";
    const speak = keepOrig
      ? `<p class="rv-quote rv-say"><span class="rv-lab">會念成</span><span class="rv-warnline">照原聲留著：這個名字這一集還沒有英文名，到「開始前 4 件事」③ 選</span></p>`
      : !skip && w && rvChosen(it) === "整句換掉"
      ? `<p class="rv-quote rv-say"><span class="rv-lab">會念成</span>${w["改稿"] ? esc(say) : w["換成代號"] ? rvMarkDrops(say, w["拿掉的位置"]) : `<span class="rv-warnline">（還沒換成代號）</span>`}<span class="rv-meta">（代號 ${esc(it["代號"] || "（沒有）")}）</span></p>` : "";   // 審查建議 7：沒換成代號時不顯示含本名的原文
    const fold = w && !skip ? `<details class="rv-namefold" data-key="${esc(key)}" ${open ? "open" : ""}>
        <summary>調整重念的字和範圍${w["改稿"] || w["範圍"] === "人選" ? ` <span class="rv-tag">已改過</span>` : ""}</summary>
        ${rvRangeHtml(it)}
        <div class="rv-field"><div class="rv-edrow"><b>要念的句子</b>${helpTip("逐字稿漏了名字、名字講了兩次，直接在這裡改；重念範圍內老師講的話都要寫進來。")}</div>
          <textarea id="rv-namesay" rows="2" aria-label="老師重念要念的句子" data-auto="${esc(w["換成代號"] || "")}">${esc(say)}</textarea>
          <p class="rv-meta">原本逐字稿：${esc(w["原文"])}</p>
          <span class="rv-meta" id="rv-namesay-st">${w["改稿"] ? RV_ST_EDITED : w["換成代號"] ? "" : "句子裡找不到比對到的字，要人改（把名字改成代號才能通過）"}</span>
          ${w["字太少"] ? `<p class="rv-warnline">要念 ${w["字數"][0]} 個字，重念範圍的逐字稿有 ${w["字數"][1]} 個字：這一段其他的話會不見。請把重念範圍改小，或把話補齊（不然不能通過）。</p>` : ""}
          ${w["實際會念"] ? `<p class="rv-warnline">句子裡還有名冊上的名字，生成時會自動${(w["實際會念拿掉"] || []).length ? "換成代號或拿掉" : "換成代號"}。實際會念：${rvMarkDrops(w["實際會念"], w["實際會念拿掉"])}</p>` : ""}</div>
      </details>` : "";
    return `<p class="rv-quote"><span class="rv-lab">原句</span>${it.sentence_html}</p>
      ${speak}${low}
      ${rvAlsoHtml(it)}
      ${!w ? `<p class="rv-meta">代號 ${esc(it["代號"] || "（沒有）")}</p>` : ""}
      ${fold}
      ${rvSpotsHtml(it)}`;
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
  if (pr && w) pr.addEventListener("click", () => { cvPlayRange(w.start, w.end); });
  const rr = document.getElementById("rv-rangereset");
  if (rr) rr.addEventListener("click", async () => {
    await cvPost("/api/review/name", { id: it.id, "整句起訖": null }); await cvReload();
  });
  if (w && document.querySelector("[data-rg]")) {
    const cur = { 0: w.start, 1: w.end };
    rvBindTimeRows("rg", (k) => cur[k], (k, t) => { cur[k] = t; rvSaveRange(it, cur[0], cur[1]); });
  }
  const ta = document.getElementById("rv-text");
  if (ta) ta.addEventListener("blur", () => rvSaveTurnText(it));
  if (it["類型"] === "名字") { rvBindAlso(it); rvBindSpots(it); }
  document.querySelectorAll(".rv-namefold").forEach((d) => d.addEventListener("toggle", () => {
    cv.nameFold = cv.nameFold || {}; cv.nameFold[d.dataset.key] = d.open;
  }));
  const say = document.getElementById("rv-namesay");   // 09-29：名字整句的重念稿
  if (say) say.addEventListener("blur", async () => {
    const v = say.value.trim();
    const same = v === (say.dataset.auto || "").trim() || (!it["老師整段"] && v === ((it["整句"] || {})["原文"] || "").trim());
    const txt = same ? "" : v;   // 沒改（還是原文）不算人改過：原文裡可能還有本名
    if (txt === ((it["整句"] || {})["改稿"] || "")) return;
    await cvPost("/api/review/name", { id: it.id, "改稿": txt });
    if (it["整句"]) it["整句"]["改稿"] = txt;
    const st = document.getElementById("rv-namesay-st");
    if (st) st.textContent = txt ? RV_ST_EDITED : "";   // 10-16 #225：「照逐字稿自動排的」不顯示
  });
}

// 10-03 第八批補修（#12）：同一句、同一個代號的名字只出一張卡；每一處列出來（可以點去聽），決定套到每一處
function rvSpotsHtml(it) {
  const spots = it["這一句的處"] || [];
  if (spots.length < 2) return "";
  const btns = spots.map((s, i) => `<button class="ghost small rv-segplay" data-t="${s.start}" data-e="${s.end}">第 ${i + 1} 處 ${esc(rvFmt(s.start, 1))}–${esc(rvFmt(s.end, 1))}</button>`).join(" ");
  const split = it["分開決定過"];
  const note = split
    ? `<p class="rv-warnline">這一句的 ${spots.length} 處以前分開決定過，做法不一樣：${esc(split.map((x) => `${rvFmt(x.start, 1)} ${x.tags && x.tags.length ? x.tags.join("、") : rvHowLabel(x["做法"])}${x["已確認"] ? "（已通過）" : "（還沒通過）"}`).join("；"))}。
        現在照各自的決定處理；在這張卡上按「通過」，就全部改成這張卡的做法。</p>`
    : `<p class="rv-meta">這張卡的決定套用到每一處：整句換掉是整句念一次、每一處都換成代號；只換名字、直接消音是每一處各自處理。</p>`;
  // 10-15 總檢查修正（1015-7）：名字卡沒有 整句（沒有重念範圍那一列）時這張卡沒有「前後多 2 秒」開關，但「第 N 處」照開關播——
  // 這裡補一顆（第 3 步；第 5 步卡片上已經有一顆）。有 整句 的照舊放在重念範圍那一列，一張卡還是只有一顆
  const pad = !it["整句"] && !(cv.ctx && cv.ctx.mode === "第5步") ? cvPadHtml("rv-pad") : "";
  return `<p class="rv-meta">這一句裡有 ${spots.length} 處（同一個代號，合成這一張）：</p>
    <div class="rv-field rv-row">${btns}${pad}</div>${note}`;
}

function rvBindSpots(it) {
  document.querySelectorAll(".rv-segplay[data-e]").forEach((b) => b.addEventListener("click", () => {
    cvPlayRange(Number(b.dataset.t), Number(b.dataset.e));   // 10-15 1015-5：以前寫死前 0.5、後 0.3 秒，改照開關
  }));
}

// 10-03 第八批（#12）：同一處比中好幾個人（名冊同一個寫法在兩列、讀音相近），合成一張卡；另一位寫在這裡，代號由人選
function rvAlsoHtml(it) {
  const also = it["也可能是"] || [];
  if (!also.length) return "";
  const btns = also.map((a) => `<button class="ghost small rv-alsopick" data-who="${esc(a.canonical || "")}">改用 ${esc(a["代號"] || "（沒有代號）")}（${esc(a.canonical || "")}）</button>`).join(" ");
  const back = it["選的人"] ? ` <button class="ghost small rv-alsopick" data-who="">回到自動選的</button>` : "";
  return `<p class="rv-meta">也可能是：${esc(also.map((a) => `${a["代號"] || "（沒有代號）"}（${a.canonical || ""}）`).join("、"))}
    ——同一個地方比中好幾位，已合成這一張。現在用 ${esc(it["代號"] || "（沒有）")}（${esc(it["本名"] || "")}）${it["選的人"] ? "，是你選的" : "，是自動選的（比對最準的）"}；不對的話選另一位：</p>
    <div class="rv-field rv-row">${btns}${back}</div>`;
}

function rvBindAlso(it) {
  document.querySelectorAll(".rv-alsopick").forEach((b) => b.addEventListener("click", async () => {
    try { await cvPost("/api/review/name", { id: it.id, "選的人": b.dataset.who || "" }); } catch (err) { uiAlert({ title: "沒有成功", message: err.message }); return; }
    await cvReload();
  }));
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
  const opts = cvData()["選項"];
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

function rvBindMore(it) {
  const cutThis = document.getElementById("rv-cutthis");   // 09-29 宇軒：改做法裡直接刪掉這一段
  if (cutThis) cutThis.addEventListener("click", async () => {
    const [a, b] = rvCutRange(it);
    if (!(await uiConfirm({ title: `剪掉 ${rvFmt(a, 1)}–${rvFmt(b, 1)}？`, danger: true, ok: "剪掉這段", cancel: "先不要",
      message: "聲音和畫面都拿掉（影片會變短），這段就不用生成。\n只想拿掉聲音、畫面留著：用「新增修改」→ 消音。\n之後不剪了：在清單找這筆「剪掉片段」按「還原」。" }))) return;
    await cvPost("/api/review/cut", { start: a, end: b, "備註": `從「${it["類型"]}」這一筆刪除` });
    await cvReload();
  });
  const q = (id) => document.getElementById(id);
  const t = it["類型"];
  document.querySelectorAll(".rv-minor").forEach((el) => el.addEventListener("change", async () => {
    await cvPost("/api/turns/save", { id: it.id, "短句保留": el.value === "學員整句生成" });
    await cvReload();
  }));
  if (t === "改成老師") {
    q("rv-who").addEventListener("change", async (e) => {
      await cvPost("/api/turns/save", { id: it.id, "說話者": e.target.value }); await cvReload();
    });
  } else if (t === "學員段落") {
    q("rv-who").addEventListener("change", async (e) => {
      cvNoteUndo({ kind: "who", ...cvTurnSnap(it) });
      await rvSaveTurnText(it);
      await cvPost("/api/turns/save", { id: it.id, "說話者": e.target.value }); await cvReload();
    });
    q("rv-merge").addEventListener("click", async () => {
      await rvSaveTurnText(it);
      const before = rvOutKeys();
      try { await cvPost("/api/turns/merge", { id: it.id }); } catch (err) { uiAlert({ title: "沒有成功", message: err.message }); return; }
      await cvReload();
      cvAskOutside(before);
    });
    q("rv-split").addEventListener("mousedown", (e) => e.preventDefault());
    q("rv-split").addEventListener("click", async () => {
      const ta = q("rv-text");
      if (!ta) { uiAlert({ title: "不用切開", message: "這段落在剪掉的片段裡，不用切開" }); return; }   // 10-16 #225：剪掉的卡沒有輸入框
      const at = ta.selectionStart;
      if (!at || at >= ta.value.length) { uiAlert({ title: "先放好游標", message: "先在逐字稿裡把游標放在要切開的地方（換人的第一個字前面）" }); return; }
      const snap = cvTurnSnap(it);
      await rvSaveTurnText(it);
      const before = rvOutKeys();
      let sp;
      try { sp = await cvPost("/api/turns/split", { id: it.id, at }); } catch (err) { uiAlert({ title: "沒有成功", message: err.message }); return; }
      cvNoteUndo({ kind: "split", ...snap, made: (sp && sp["新段落"]) || [] });
      await cvReload();
      cvAskOutside(before);
      if (sp && String(sp["切在"] || "").startsWith("一句話裡面")) {   // 09-30：切在一句話裡面，切點是照逐字稿每個字的時間找的
        uiAlert({ title: "切好了，請聽一下前後", message: `切在${sp["切在"]}：切點 ${rvFmt(sp["切點"], 1)}。\n前後兩段請各聽一下開頭結尾，不準的話用「改時間」調整。後面那一段如果是老師講的，${cv.ctx.mode === "第5步" ? "到那一段按「整段改成老師」" : "按「這段其實是老師」"}。` });
      }
    });
    q("rv-ask").addEventListener("change", async (e) => {
      q("rv-asknote").hidden = !e.target.checked;
      const res = await cvPost("/api/turns/save", { id: it.id, "問老師": e.target.checked });
      it["問老師"] = res["段落"]["問老師"];
    });
    q("rv-asknote").addEventListener("change", (e) => { it["問老師備註"] = e.target.value; cvPost("/api/turns/save", { id: it.id, "問老師備註": e.target.value }); });
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
      if (el.value === "只留學員" || was === "只留學員") await cvReload();
    }));
    if (q("rv-genbox") && it["生成範圍"]) {
      const g = it["生成範圍"];
      const cur = { 0: g.start, 1: g.end };
      rvBindTimeRows("gs", (k) => cur[k], async (k, t) => {
        cur[k] = t;
        if (cur[1] - cur[0] < 0.1) { uiAlert({ title: "時間不對", message: "結束要晚於開始" }); return; }
        await rvSaveOverlap(it, { "學員起訖": [cur[0], cur[1]] });
        await cvReload();
      });
      q("rv-genplay")?.addEventListener("click", () => { cvPlayRange(g.start, g.end); });
      q("rv-genreset")?.addEventListener("click", async () => {
        if (!(await uiConfirm({ title: "回到預設範圍？", message: "會換掉的範圍回到預設（學員那一整句）。", ok: "回到預設範圍", cancel: "先不要" }))) return;
        await rvSaveOverlap(it, { "回到預設範圍": true });
        await cvReload();
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
        if (pair[1] - pair[0] < 0.1) { uiAlert({ title: "時間不對", message: "結束要晚於開始" }); return; }
        await rvSaveOverlap(it, { [key]: pair });
        it[key] = pair;
        q("rv-sidesbox").innerHTML = rvSidesHtml(it);
        rvBindMore(it);
      });
      q("rv-sideplay").addEventListener("click", () => {
        cvPlayRange(Math.min(it["老師起訖"][0], it["學員起訖"][0]), Math.max(it["老師起訖"][1], it["學員起訖"][1]));
      });
    }
    // 10-01 走查：被別筆涵蓋的重疊沒有這些欄位（以前這裡丟錯，時間軸不會跟著換到這一筆）
    q("rv-tt")?.addEventListener("change", (e) => rvSaveOverlap(it, { "老師文字": e.target.value }));
    q("rv-st")?.addEventListener("change", (e) => rvSaveOverlap(it, { "學員文字": e.target.value }));
    q("rv-ovwho")?.addEventListener("change", async (e) => { await rvSaveOverlap(it, { "學員說話者": e.target.value || null }); await cvReload(); });
    const guessBtn = q("rv-ovguess");
    if (guessBtn) guessBtn.addEventListener("click", async () => { await rvSaveOverlap(it, { "學員說話者": guessBtn.dataset.who }); await cvReload(); });
    q("rv-note")?.addEventListener("change", (e) => rvSaveOverlap(it, { "備註": e.target.value }));
  } else if (t === "刪除段落" && it["來源"] === "建議") {
    document.querySelectorAll(".rv-cutdo").forEach((el) => el.addEventListener("change", async () => {
      await cvPost("/api/review/cutsuggest", { id: it.id, "決定": el.value }); await cvReload();
    }));
  } else {
    const api = t === "刪除段落" ? "/api/review/cut" : "/api/review/mute";
    q("rv-toggle").addEventListener("click", async () => {
      const on = t === "刪除段落" ? "刪除" : "消音";
      await cvPost(api, { id: it.id, "狀態": it["狀態"] === "還原" ? on : "還原" }); await cvReload();
    });
    document.querySelectorAll(".rv-muteway").forEach((el) => el.addEventListener("change", () => cvPost(api, { id: it.id, "方式": el.value })));
    q("rv-note").addEventListener("change", (e) => cvPost(api, { id: it.id, "備註": e.target.value }));
  }
}

async function rvSaveTurnText(it) {
  const ta = document.getElementById("rv-text");
  if (!ta || it["類型"] !== "學員段落") return;
  const v = ta.value.trim();
  const base = (it["已確認"] ? it["校對稿"] : it["建議稿"]) || "";
  if (v === base.trim()) return;
  const res = await cvPost("/api/turns/save", { id: it.id, "校對稿": v });
  it["校對稿"] = it["建議稿"] = res["段落"]["校對稿"];
  it["換過的字"] = [];
}

async function rvSaveName(it, fields) {
  const res = await cvPost(it["類型"] === "學員名字" ? "/api/review/stuname" : "/api/review/name", { id: it.id, ...fields });
  Object.assign(it, { "做法": res["決定"]["做法"] || it["做法"], tags: res["決定"].tags || [], note: res["決定"].note || "" });
  cvRefreshSug();
}

async function rvSaveOverlap(it, fields) {
  try {
    const res = await cvPost("/api/review/overlap", { id: it.id, ...fields });
    Object.assign(it, res["決定"]);
    cvRefreshSug();
  } catch (err) { uiAlert({ title: "沒有成功", message: err.message }); }
}

// 10-01 第三批：人工新增的（剪掉、消音、漏抓的重疊、漏抓的名字、老師這一段 AI 重念）加錯了可以刪；刪掉的留一筆紀錄
function rvDeleteName(it) {
  const t = it["類型"];
  const label = t === "名字" && it["老師整段"] ? "老師重念（人工標的一段）" : t === "名字" ? "漏抓的老師提到名字" : t === "重疊" ? "漏抓的重疊"
    : (RV_TYPE[t] || {}).label || t;
  return `${label} ${rvFmt(it.start, 1)}–${rvFmt(it.end, 1)}`;
}

async function rvDeleteManual(it) {
  if (!(await uiConfirm({ title: `刪掉這一筆人工新增的「${rvDeleteName(it)}」？`, danger: true, ok: "刪掉這一筆", cancel: "先不要",
    message: "刪掉之後清單裡不會再有它，成品也不會照它處理；會留一筆紀錄（在「設定」→「刪掉的紀錄」看得到），但不能救回，要的話重新新增。" }))) return;
  try { await cvPost("/api/review/delete", { "類型": it["類型"], id: it.id }); } catch (err) { uiAlert({ title: "沒有成功", message: err.message }); return; }
  await cvReload();
}

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

function rvAlignLine(it) {   // 「你標的 → 對齊後」（人工新增、改過時間的才有）
  const m = it["標的起訖"];
  if (!m || !cvData()["開發者"]) return "";   // 10-16 #225 宇軒（甲）：只在開發者設定顯示，老師的畫面不出現
  const ok = it["對齊"] || [];
  const note = it["對齊到"] === "照填的時間" ? "照你填的時間，沒有自動對齊"   // 10-01 第三批：第 5 步改的
    : ok[0] && ok[1] ? `對齊${it["對齊到"] || ""}` : !ok[0] && !ok[1] ? "沒對齊，保留你標的時間" : `${ok[0] ? "終點" : "起點"}沒對齊`;
  return `<p class="rv-meta">你標的 ${esc(rvFmt(m[0], 2))}–${esc(rvFmt(m[1], 2))} → ${esc(rvFmt(it.start, 2))}–${esc(rvFmt(it.end, 2))}（${esc(note)}）</p>`;
}

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
      <div class="rv-edrow"><button class="ghost" data-te-play>聽這一段</button>
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
  // 10-15 前後 2 秒開關統一（1015-5）：改時間面板的「聽這一段」照卡片上的開關加前後
  q("[data-te-play]").addEventListener("click", () => { if (teOk(st)) o.play(...cvPadRange(st.a, st.b)); });
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
  try { r = await cvPost(o.api || "/api/review/manual", body); } catch (err) {
    st.busy = false; st.result = { error: err.message }; teRender(ctx); return;
  }
  st.busy = false;
  const res = o.toResult ? o.toResult(r, st) : { ...r["對齊結果"], "新增": r["新增"] };
  await o.saved(r, res);
}
