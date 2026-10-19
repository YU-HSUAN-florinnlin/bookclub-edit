"use strict";

/* 第 5 步「成品檢查」（10-15 第5步2.0 第2段：改成用第 3 步的卡片，31 號設計 §6）。
 * 版面：頂端「5 成品檢查」＋「看哪一版」＋「輸出成品」，一句狀態另起一行在標題下面（10-15 要修改合併（1015-4）順手）
 *   → 左邊成品影片＋時間軸（時間軸上的點＝卡片，照狀態上色：還沒看／通過／要重做）＋「看過 P%」；
 *     右邊「目前這張卡」：第 3 步那張卡＋狀態標籤＋「原本 ▶」「現在 ▶」＋通過／要修改／上一張／下一張
 *     （10-15 要修改合併（1015-4）：「退回」「改」合成一顆「要修改」，見 fcFixHtml）
 *   → 下面：卡片清單（第 3 步的分頁；最右邊收「第 3 步選定不修改」「⚠️ 需留意」）。10-15 宇軒驗證回饋六：清單分兩個——
 *     上面「還要確認的」（這一版還沒看完的＋之前版本還沒看完的跨版本卡，照原片時間排）、下面「已通過的」；第 5 步自己多的那個分頁拿掉。
 *   「＋在這裡新增修改」「重做這幾張（N 張）」（有要重做的卡才出現）：10-15 宇軒驗證回饋，搬到左邊「看過 P%」那一行下面。
 * 卡片的內容、「要修改」帶出的修改控制區是第 3 步的卡片元件（cards.js），走第 3 步原本的 API；改完那張卡「已改，等重做」。
 * 「重做這幾張」：第 4 步開始執行、跳到第 4 步；跑完自動跳回第 5 步（31 號設計 §9 問題 2 定案）。
 * 10-15 宇軒驗證回饋二（1015-4 補充）：卡片行為盡量跟第 3 步一樣——標題列原片時間範圍（成品時間小字）、影片下「跳到」框、
 *   通過後按鈕「✓ 已通過（再按取消）」且不自動跳下一張；「裡面有老師的聲音」＝「其他」（整個修改選單）；要重做的卡可以再按
 *   「要修改」、帶入上次的原因與補充，改了再「確定」覆蓋成最新；「取消」改名「收起」（只收合、不撤回）。
 * 10-15 第5步2.0 第3段（版本，31 號設計 §6.5–6.7）：有兩版以上，頂端多「看哪一版」選單（fc.ver＝看哪一版，null＝最新）；
 *   舊版：卡片照那一版的判斷、那一版沒做的卡變淡；可以按通過（記在那一版）、可以輸出那一版；不能新增、不能重做（到最新版按）。
 *   10-15 宇軒驗證回饋六：「要修改」所有卡都能按（不論狀態、跨版本、看哪一版）。
 *   10-15 總檢查修正（1015-7）：原因記在哪由後端分流——最新版有它狀態列的卡（不論在哪一版畫面按）走最新版正常路（成品檢查退回）；
 *   其他（跨版本卡、沿用卡、舊版才有的卡）記在卡片自己的等重做（needs.mark_outside_redo），第 4 步「重做這幾張」也收它。
 *   數字（還要確認幾張、已通過幾張）、每張卡是不是「已通過的」「不能修改」用後端給的（卡片統計.還要確認／已通過的、卡片 已通過的／不能修改），
 *   沒有時前端自己算（過渡）。舊版畫面上最新版要重做的卡另寫小字「最新版：要重做（原因）」（卡片 最新版要重做）。
 *   最新版上面清單多了跨版本卡（範圍組裝時那一版範圍外、舊版還沒看完的卡，時間格下小字「第 N 版」）；重做過的卡多「上一版 ▶」。
 * 資料：GET /api/final（?版本=N 看舊版；卡片、狀態、第 3 步資料）；存檔：/api/final/card（通過／要修改的原因／取消，後端分流）、/api/final/edited、
 * /api/final/watched、/api/final/export（bookclub/finalcards.py、finalcheck.py）。依賴 app.js 的 apiGet／apiPost／esc／contentEl。 */

// 09-29 宇軒：2 倍速以下播過的才算「看過」（快轉看完不算有人完整看過）
const FC_MAX_RATE = 2;
const FC_AB_CONTEXT = 2;   // 試聽前後各多 2 秒（跟後端一樣）
// 10-15 宇軒驗證回饋：「原本 ▶」「現在 ▶」旁的滑動開關「前後多 2 秒」（iOS 樣式），預設關（只播修改的那一段）；開關記在瀏覽器
// 10-15 前後 2 秒開關統一（1015-5）：開關元件、狀態搬到 cards.js（cvPadHtml／cvPad／cvSetPad），第 3 步共用同一個鍵

let fcBackKey = null;   // 從第 3 步按「回第 5 步成品檢查」回來時，回到出發的那張卡（或「第 3 步選定不修改」那一列）
const fc = { ver: null, data: null, video: null, audio: null, cur: null, seen: [], seg: null, sentRanges: "", timer: null, lastT: 0,
  ab: null, span: null, ed: { open: false }, pend: [],
  // 10-15 要修改合併（1015-4）：「要修改」那一塊（open＝開著、card＝哪一張、pick＝哪裡出問題、extra＝補充）
  fix: { open: false, card: null, pick: null, extra: "", busy: false, err: "" }, fixEd: null };

function fcFmt(t, d = 1) { return typeof rvFmt === "function" ? rvFmt(t, d) : String(t); }
function fcSpan(r2) {
  return r2 && r2[0] != null ? `${fcFmt(r2[0])}${r2[1] != null && Math.abs(r2[1] - r2[0]) >= 0.05 ? "–" + fcFmt(r2[1]) : ""}` : "";
}
// 10-07 宇軒：畫面上的「學員3」換成「本名（代號）」（GET /api/final 的「學員顯示名」；只在畫面換，紀錄不寫本名）
function fcWho(text) {
  const names = (fc.data && fc.data["學員顯示名"]) || {};
  return String(text == null ? "" : text).replace(/學員\s?(\d+)(?!\d)/g, (m, n) => names[`學員${n}`] || m);
}
// （純函式）「2026-10-14T14:20:05」→「10/14 14:20」
function fcShortStamp(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(iso || ""));
  return m ? `${m[2]}/${m[3]} ${m[4]}:${m[5]}` : String(iso || "");
}

// ---------- 卡片的狀態（純函式） ----------

const FC_ST = { NOT_MADE: "還沒生成", TODO: "還沒看", OK: "通過", BACK: "退回", EDITED: "已改，等重做" };
const FC_TODO = [FC_ST.TODO, FC_ST.BACK, FC_ST.EDITED, FC_ST.NOT_MADE];   // 還沒看完的狀態
const FC_REDO = [FC_ST.BACK, FC_ST.EDITED, FC_ST.NOT_MADE];               // 「重做這幾張」算的
const FC_REDO_LABEL = "要重做";   // 10-15 要修改合併（1015-4）：退回、已改，等重做的標籤字

// 範圍外的卡（資料欄位 c["這一版沒處理到"]）給老師看的字：之前的版本通過過＝「已通過（沿用上一版）」、綠色系（cls ok，
// 10-15 宇軒驗證回饋三、四）；舊版畫面上之前沒通過過的＝「這一版沒做這張」
const FC_OUT_PASSED = "已通過（沿用上一版）", FC_OUT_PASSED_NOTE = "這版沒改到這裡，不用再看";
const FC_OUT_NONE = "這一版沒做這張", FC_OUT_NONE_NOTE = "這一版的成品沒有處理這一張";
// 卡片標籤：{text, cls, small: [小字]}（31 號設計 §6.3）
function fcCardLabel(c) {
  // 範圍外的卡（c["這一版沒處理到"]，開發者用範圍組裝時）：不能按通過；標籤見上面 FC_OUT_*（31 號設計 §6.6 修訂三～六）
  if (c["這一版沒處理到"]) {
    return c["上一版已通過"] === false ? { text: FC_OUT_NONE, cls: "made", small: [FC_OUT_NONE_NOTE] }
      : { text: FC_OUT_PASSED, cls: "ok", small: [FC_OUT_PASSED_NOTE] };
  }
  const small = [];
  if (c["狀態"] === FC_ST.OK && c["沿用自"]) small.push(`沿用第 ${c["沿用自"]} 版`);
  if (c["要人聽"] && c["狀態"] !== FC_ST.OK) small.push("請仔細聽");
  if (c["第幾版"]) small.push(`第 ${c["第幾版"]} 版`);
  if (c["舊紀錄"]) small.push("舊紀錄");
  if (c["狀態"] === FC_ST.BACK && (c["連帶自"] || []).length) small.push("連帶");   // 10-15 第2段審查修正（必修 1）
  if (c["狀態"] === FC_ST.EDITED) small.push(c["第3步改過"] ? "在第 3 步改過" : "已改");   // 10-15 第2段審查修正（第 6 條）
  const cls = { [FC_ST.NOT_MADE]: "made", [FC_ST.TODO]: "todo", [FC_ST.OK]: "ok", [FC_ST.BACK]: "back", [FC_ST.EDITED]: "edited" }[c["狀態"]] || "todo";
  // 10-15 要修改合併（1015-4）：退回、已改，等重做對老師一律寫「要重做」（已改的小字「已改」；原因在底下一行）
  const text = c["狀態"] === FC_ST.OK ? "通過 ✓" : [FC_ST.BACK, FC_ST.EDITED].includes(c["狀態"]) ? FC_REDO_LABEL : c["狀態"];
  return { text, cls, small };
}

// 退回原因那一行（選項＋補充）
function fcReasonText(why) {
  if (!why) return "";
  const opts = (why["選項"] || []).join("、");
  const extra = String(why["補充"] || "").trim();
  return opts && extra ? `${opts}：${extra}` : opts || extra;
}

// 10-15 第2段審查修正：退回沒選原因也沒補充（後端舊檔那一格寫的就是這一句）
const FC_NO_REASON = "退回（沒寫原因）";
// 退回那張卡底下那一行（純函式）：有原因＝「原因：…」；沒寫原因＝「要重做（沒寫原因）」（10-15 要修改合併（1015-4）：標籤字改「要重做」）
const FC_NO_REASON_SHOW = "要重做（沒寫原因）";
function fcWhyLine(c) {
  const why = fcReasonText(c["退回原因"]);
  return why && why !== FC_NO_REASON ? `原因：${why}` : FC_NO_REASON_SHOW;
}

// 10-15 第2段審查修正（必修 1）：連帶要重做的卡那一行（純函式）：「跟 #N 共用，那張還是要重做」
// 10-15 總檢查修正（1015-7）：給老師看的字一律「要重做」，不說「退回」
function fcSharedLine(c) {
  const from = c["連帶自"] || [];
  if (!from.length) return "";
  return `跟 ${from.map((x) => (x["編號"] ? `#${x["編號"]}` : "另一張卡")).join("、")} 共用，那張還是要重做`;
}

// 「重做這幾張」那顆按鈕（純函式）：{show, n}。10-15 第2段審查修正（必修 3）：舊檔模式（新紀錄不能用）按「改」記不到
// 「已改，等重做」，所以舊檔模式 AI 沒在跑時一律顯示（不寫張數）
function fcRedoCount(d) {
  const n = (d["卡片統計"] || {})["要重做"] || 0;
  if (n) return { show: true, n };
  return { show: d["卡片來源"] === "舊檔" && !d["AI執行中"], n: 0 };
}

// 10-15 總檢查修正（1015-7）：（純函式）還要確認幾張＝上面「還要確認的」清單（不分頁、不篩「只看要仔細聽的」）。
// 有後端的 卡片統計.還要確認 就用它（finalcards.summary，含跨版本卡）；沒有（後端還沒給、舊工作區）照前端兩個清單算
function fcLeftCount(d) {
  const st = (d && d["卡片統計"]) || {};
  if (typeof st["還要確認"] === "number") return st["還要確認"];
  return fcSplitLists(((d && d["卡片"]) || []).concat((d && d["跨版本卡片"]) || [])).todo.length;
}
// 10-15 總檢查修正（1015-7）：（純函式）舊版畫面上幾張要重做。審查建議 6：用後端的 卡片統計.要重做（needs.redo_cards 的張數，
// 跟最新版「重做這幾張（N 張）」同一個數）；沒有（後端還沒給）才照卡片算
function fcOldRedoCount(d) {
  const n = ((d && d["卡片統計"]) || {})["要重做"];
  if (typeof n === "number") return n;
  return ((d && d["卡片"]) || []).filter((c) => c["最新版要重做"] || [FC_ST.BACK, FC_ST.EDITED].includes(c["狀態"])).length;
}

// 頂端那一句（純函式，31 號設計 §6.8：只顯示一句，照優先順序選）。回傳 {text, cls, go}（go＝點了切到哪一頁）
// 10-15 第2段審查修正（必修 3）：d["舊檔改過"]＝舊檔模式這次畫面按「改」改過（記不到卡片上，畫面自己記著）→ 當作要重做
// 10-15 總檢查修正（1015-7）：「還有 N 張沒看」的 N＝還要確認幾張（fcLeftCount，含跨版本卡；以前只算這一版的還沒看，
// 最新版只有跨版本卡時頂端錯寫「全部看完了」）。看舊版（d["看的是舊版"]）：不提「重做這幾張」那幾句，有要重做的卡寫
// 「有 N 張要重做（到最新版按『重做這幾張』）」（N＝fcOldRedoCount；以前舊版硬把要重做設 0）
function fcStatusLine(d) {
  const st0 = d["卡片統計"] || {};
  const st = d["舊檔改過"] && !st0["要重做"] ? { ...st0, "要重做": 1 } : st0;
  const names = ((d["需留意"] || {})["列"] || []).filter((r) => r["名字"]).length;
  const run = (d["上次執行"] || {})["狀態"];
  const out = d["輸出成品"];
  const left = fcLeftCount(d);
  if (names) return { text: `⚠️ 這 ${names} 處會照原聲念出本名`, cls: "fc-st-name", go: "需留意" };
  if (d["看的是舊版"]) {
    const n = fcOldRedoCount(d);
    if (n) return { text: `有 ${n} 張要重做（到最新版按『重做這幾張』）`, cls: "fc-st-redo" };
  } else {
    if (d["AI執行中"]) return { text: "正在重做…", cls: "" };
    if (st["要重做"] && ["停止", "失敗", "中斷"].includes(run)) return { text: "上次重做沒做完：按『重做這幾張』繼續", cls: "fc-st-redo" };
    if (st["要重做"]) return { text: "改過的還沒重做：按右上角『重做這幾張』", cls: "fc-st-redo" };
  }
  if (left) return { text: `還有 ${left} 張沒看`, cls: "" };
  if (out && !d["輸出成品過期"]) return { text: `已輸出（${fcShortStamp(out["時間"])}）`, cls: "fc-st-done" };
  return { text: "全部看完了，可以輸出", cls: "fc-st-done" };
}

// 卡片要的第 3 步那張卡（沒有＝待釐清、沒登記的變動，或第 3 步資料讀不到）
// 10-15 修正：不看卡片的 `有第3步卡片`——那個欄位只在整頁資料（GET /api/final，finalcards._step3_part）裡有，按通過／取消後
// fcDecide 用後端回傳的卡（finalcards.decide，沒有這個欄位）換掉舊卡，以前就變「原本：（找不到原句）」。前端別依賴它，
// 直接用 第3步鍵 在整頁資料的第 3 步項目裡找（找不到才是 null）
function fcItemOf(c) {
  if (!c || !c["第3步鍵"]) return null;
  return ((fc.data["第3步"] || {})["項目"] || []).find((x) => rvKey(x) === c["第3步鍵"]) || null;
}
const FC_KIND_NAME = { "待釐清": "不確定是哪一種", "沒登記的變動": "聲音變了但沒有紀錄" };
// 卡片類型的小標（第 3 步卡片用第 3 步的；其他兩種另外寫）
function fcChip(c, it) {
  if (it) return rvChip(it);
  return `<span class="rv-chip fc-ov"><i></i>${esc(FC_KIND_NAME[c["類型"]] || c["類型"])}</span>`;
}
// 清單一行的文字
function fcPreview(c, it) { return it ? rvPreview(it) : c["說明"] || ""; }

// ---------- 分頁（純函式）：沿用第 3 步的分頁名稱 ----------
// 10-15 宇軒驗證回饋六：第 5 步自己多的那個分頁拿掉（上面「還要確認的」清單就是它）；各分頁的卡＝這一版的卡＋之前版本還沒看完的卡
// （跨版本卡），兩個清單（還要確認的／已通過的）都照分頁篩

const FC_TAB_ALL = "全部", FC_TAB_KEEP = "不修改", FC_TAB_ATT = "需留意", FC_TAB_OTHER = "其他";
const FC_TAB_GONE = ["還沒看完"];   // 10-15 宇軒驗證回饋六：拿掉的分頁（記住的是它就回到「全部」）
const FC_TYPE_TABS = [["學員段落", "學員段落"], ["名字", "名字"], ["學員名字", "學員提到名字"], ["重疊", "重疊"], ["刪除段落", "剪掉"], ["局部消音", "消音"], ["改成老師", "改成老師"]];
function fcTabList(cards, keepN, att, cross) {
  const all = cards.concat(cross || []);
  const tabs = [{ id: FC_TAB_ALL, "名稱": "全部", cards: all }];
  for (const [t, name] of FC_TYPE_TABS) {
    const list = all.filter((c) => c["類型"] === t);
    if (list.length) tabs.push({ id: t, "名稱": name, cards: list });
  }
  const other = all.filter((c) => !FC_TYPE_TABS.some(([t]) => t === c["類型"]));
  if (other.length) tabs.push({ id: FC_TAB_OTHER, "名稱": "其他", cards: other });
  // 31 號設計 §6.1：「第 3 步選定不修改」「需留意」收到最右邊（給宇軒或夥伴用）
  if (keepN) tabs.push({ id: FC_TAB_KEEP, "名稱": "第 3 步選定不修改", cards: [], "筆數": keepN, "清單外": true });
  if (att) tabs.push({ id: FC_TAB_ATT, "名稱": "⚠️ 需留意", cards: [], "筆數": (att["列"] || []).length, "清單外": true });
  return tabs;
}
// 10-15 宇軒驗證回饋六：（純函式）這張卡算不算「已通過的」（下面那個清單）：這一版通過的＋已通過（沿用上一版）
// 10-15 總檢查修正（1015-7）：後端有給每張卡 已通過的（布林，finalcards）就用它；沒有時照前端算（過渡）
function fcIsPassed(c) {
  if (!c) return false;
  if (typeof c["已通過的"] === "boolean") return c["已通過的"];
  return !c["跨版本"] && (c["狀態"] === FC_ST.OK || (!!c["這一版沒處理到"] && c["上一版已通過"] !== false));
}
// （純函式）兩個清單：{todo: 還要確認的, passed: 已通過的}，各自照原片時間排（沒有原片時間的排最後）
function fcSplitLists(cards) {
  const at = (c) => (c["原片"] && c["原片"][0] != null ? c["原片"][0] : Infinity);
  const sort = (l) => l.map((c, i) => [c, i]).sort((a, b) => at(a[0]) - at(b[0]) || a[1] - b[1]).map((x) => x[0]);
  return { todo: sort(cards.filter((c) => !fcIsPassed(c))), passed: sort(cards.filter(fcIsPassed)) };
}
function fcPickTab(tabs, want) { return tabs.some((t) => t.id === want) ? want : FC_TAB_ALL; }
const FC_TAB_KEY = "fc-tab";
// 10-15 宇軒驗證回饋六：記住的是拿掉的那個分頁（FC_TAB_GONE）→ 回到「全部」
function fcTabGet() { let v; try { v = localStorage.getItem(FC_TAB_KEY) || FC_TAB_ALL; } catch (e) { v = FC_TAB_ALL; } return FC_TAB_GONE.includes(v) ? FC_TAB_ALL : v; }
function fcTabSet(id) { try { localStorage.setItem(FC_TAB_KEY, id); } catch (e) { /* 存不了也沒關係 */ } }
const FC_ONLY_KEY = "fc-only-look";
function fcOnlyLook() { try { return localStorage.getItem(FC_ONLY_KEY) === "1"; } catch (e) { return false; } }
function fcSetOnlyLook(on) { try { localStorage.setItem(FC_ONLY_KEY, on ? "1" : "0"); } catch (e) { /* 存不了也沒關係 */ } }

function fcCards() { return (fc.data && fc.data["卡片"]) || []; }
// 之前版本還沒看完的卡（跨版本卡，清單時間格下小字「第 N 版」）。10-15 總檢查修正（1015-7）：改名（舊名列在作廢清單）
function fcCrossCards() { return (fc.data && fc.data["跨版本卡片"]) || []; }
function fcCard(id) { return fcCards().find((c) => c.id === id) || fcCrossCards().find((c) => c.id === id); }
function fcTabs() { return fcTabList(fcCards(), (fc.data["不修改"] || []).length, fc.data["需留意"], fcCrossCards()); }
function fcTab() { return fcPickTab(fcTabs(), fcTabGet()); }
// 10-15 宇軒驗證回饋六：目前這一頁的兩個清單——上面「還要確認的」（開了「只看要仔細聽的」再篩一次）、下面「已通過的」
function fcLists() {
  const t = fcTabs().find((x) => x.id === fcTab());
  const sp = fcSplitLists(t && !t["清單外"] ? t.cards : fcCards().concat(fcCrossCards()));
  return { todo: fcOnlyLook() ? sp.todo.filter((c) => c["要人聽"]) : sp.todo, passed: sp.passed };
}
function fcShown() { return fcLists().todo; }
// 上一張／下一張走的順序：上面清單從頭到尾，接下面清單從頭到尾
function fcNavList() { const l = fcLists(); return l.todo.concat(l.passed); }

// （純函式）上一張／下一張：目前這張在清單裡就照順序（到頭／到尾停在原地）；不在（影片播到別頁的那張）就照成品時間找——
// 10-15 宇軒驗證回饋六：只拿這一版的卡比（跨版本卡的成品時間是舊版的）；清單照原片時間排、不一定照成品時間，所以找最近的那一張
// 10-15 總檢查修正（1015-7）：todoN＝上面清單有幾張（list 的前 todoN 張）。照時間找不到（影片在所有卡之後／之前）時，
// 往後退到「上面清單」最後一張（以前退到 list 最後一張＝下面清單的最後一張，一按就跳過整個還要確認的）、往前退到第一張
function fcStepPick(list, curId, t, dir, todoN = null) {
  if (!list.length) return null;
  const i = list.findIndex((x) => x.id === curId);
  if (i >= 0) return list[Math.max(0, Math.min(list.length - 1, i + dir))].id;
  const at = (x) => (!x["跨版本"] && x["成品"] && x["成品"][0] != null ? x["成品"][0] : null);
  let best = null;
  for (const x of list) {
    const a = at(x);
    if (a == null) continue;
    if (dir > 0 && a > t + 0.05 && (!best || a < at(best))) best = x;
    if (dir < 0 && a < t - 0.05 && (!best || a >= at(best))) best = x;
  }
  const lastTodo = todoN != null && todoN > 0 ? list[Math.min(todoN, list.length) - 1] : list[list.length - 1];
  return (best || (dir > 0 ? lastTodo : list[0])).id;
}

// ---------- 時間換算（純函式，跟後端 finalcheck.from_output_time／to_output_time 一樣） ----------

function fcSrcTime(t, plist) {   // 成品時間 → 原片時間（落在停格裡算停格那一點）
  if (plist === undefined) plist = fc.data && fc.data["片段"];
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
function fcOutTime(t, plist) {   // 原片時間 → 成品時間（剪掉的地方回 null）
  if (plist === undefined) plist = fc.data && fc.data["片段"];
  if (!plist || !plist.length) return t;
  let acc = 0;
  for (const p of plist) {
    const [s, e] = p.src;
    if (t >= s - 1e-6 && t <= e + 1e-6) return acc + Math.max(0, t - s);
    acc += e - s + p.freeze;
  }
  return null;
}

// （純函式）卡片試聽怎麼播：現在＝成品影片本身前後多 2 秒；原本＝原片聲音前後多 2 秒（影片同步靜音播，成品裡沒有的不動）
// 10-15 第5步2.0 第3段：「上一版」＝上一個有這張卡的版本的成品那一段聲音（/api/final/vclip）；跨版本卡的
// 「現在」也是播那一版的成品聲音（畫面上的影片是這一版的，時間對不上），「原本」只放原聲
// 10-15 宇軒驗證回饋：pad＝前後多播幾秒（開關開著＝FC_AB_CONTEXT，關著＝0；不給＝FC_AB_CONTEXT，跟以前一樣）。
// 只有一個時間點（剪掉、停格的接點，沒有長度）的照樣前後各 2 秒，不然什麼都聽不到
// 10-15 1015-5：跟 cards.js 的 cvPadRange 同一條規則（只有時間點的照樣 2 秒）；這裡留著給純函式測試單獨載入
function fcPadOf(span, pad) {
  return span[1] == null || span[1] - span[0] < 0.05 ? Math.max(pad, FC_AB_CONTEXT) : pad;
}
function fcVclip(ver, p, pad = FC_AB_CONTEXT) {
  const k = fcPadOf(p, pad);
  const a = Math.max(0, p[0] - k), b = (p[1] != null ? p[1] : p[0]) + k;
  return `/api/final/vclip?版本=${ver}&start=${a.toFixed(2)}&end=${b.toFixed(2)}`;
}
function fcCardAbPlan(c, which, pad = FC_AB_CONTEXT) {
  const p = c["成品"], o = c["原片"];
  if (which === "上一版") {
    const u = c["上一版"];
    return u && u["成品"] && u["成品"][0] != null ? { video: null, audio: fcVclip(u["版本號"], u["成品"], pad) } : null;
  }
  if (c["跨版本"] && which === "現在") return p && p[0] != null ? { video: null, audio: fcVclip(c["版本號"], p, pad) } : null;
  const has = p && p[0] != null && !c["跨版本"];
  if (which === "現在") {
    if (!has) return null;
    const k = fcPadOf(p, pad);
    const end = p[1] != null ? p[1] : p[0];
    return { video: { from: Math.max(0, p[0] - k), to: end + k, muted: false }, audio: null };
  }
  if (!o || o[0] == null) return null;
  const k = fcPadOf(o, pad);
  const a = Math.max(0, o[0] - k), b = (o[1] != null ? o[1] : o[0]) + k;
  const from = has ? Math.max(0, p[0] - k) : null;
  return { video: has ? { from, to: from + (b - a), muted: true } : null, audio: `/api/audio?start=${a.toFixed(2)}&end=${b.toFixed(2)}` };
}

// ---------------------------------------------------------------------------
// 卡片元件的情境（第 5 步；說明見 cards.js 開頭）
// ---------------------------------------------------------------------------

const FC_CARD_CTX = {
  mode: "第5步",
  data: () => (fc.data && fc.data["第3步"]) || {},
  reload: () => fcReload(),
  post: (url, body, opts) => fcTrackPost(fcStep3Post(url, body, opts)),   // 10-15 要修改合併（1015-4）：「確定」先等存完
  play: (a, stop) => fcPlaySource(a, stop),
  videoTime: () => (fc.video ? Math.round(fcSrcTime(fc.video.currentTime) * 100) / 100 : null),
  seek: (t) => { const o = fcOutTime(t); if (o != null) fcSeek(Math.max(0, o - 2)); },
  select: (key) => { const c = fcCards().find((x) => x["第3步鍵"] === key); if (c) fcSelect(c.id); },
  openEditor: (it, rng) => fcOpenEditor(it, rng),
  prefill: (kind, a, b, who, note) => fcOpenEditor(null, [a, b], { kind, who, note }),
  askOutside: null,
  refreshSug: () => {},
  // 10-15 要修改合併（1015-4）：第 3 步的「改做法」那一塊＝「要修改」選「其他」帶出的整個選單
  moreOpen: () => fc.fix.open && FC_FIX_FULL.includes(fc.fix.pick),   // 10-15 宇軒驗證回饋二：選「裡面有老師的聲音」也算
  toggleMore: () => fcOpenFix(FC_FIX_FULL.includes(fc.fix.pick) ? fc.fix.pick : FC_FIX_OTHER),
  rerender: () => fcRenderRight(),
  noteUndo: (u) => { fc.undo = { ...u, card: fc.cur, ver: (fc.data || {})["目前版本"] }; },   // 10-16 #225：「復原」記下原本的樣子（離開這張卡就不見）
};

// 10-16 #225 宇軒：第 5 步按錯了可以「復原」——第 3 步改回按之前的樣子，再拿掉「已改，等重做」（內容跟這一版一樣才拿）
const FC_UNDO_LABEL = { teacher: "把這段改成老師", split: "切開這一段", who: "改說話者" };
function fcUndoHtml(c) {
  const u = fc.undo;
  // 換了卡（點清單、影片播過去自動換、換版本…）或換了版本（重做出新的一版）：「復原」就不見（審查建議 2）
  if (u && (u.card !== c.id || u.ver !== (fc.data || {})["目前版本"])) fc.undo = null;
  if (!fc.undo) return "";
  return `<div class="rv-warnline fc-undo">已${esc(FC_UNDO_LABEL[u.kind] || "改了")}。按錯了？<button class="ghost small" id="fc-undo">復原</button></div>`;
}
async function fcUndo() {
  const u = fc.undo;
  if (!u) return;
  const btn = document.getElementById("fc-undo");
  if (btn) { btn.disabled = true; btn.textContent = "復原中…"; }
  try {
    // 切開：照新段落原本的順序一段一段併回（倒著併會留下「句尾切點」，審查必修 1）
    if (u.kind === "split") for (const id of (u.made || [])) await apiPost("/api/turns/merge", { id });
    // 切開沒動說話者：不送（送了會記成「說話者是人改的」）
    await apiPost("/api/turns/save", { id: u.id, "校對稿": u.text, "已確認": u.ok, ...(u.kind === "split" ? {} : { "說話者": u.who }) });
    const r = await apiPost("/api/final/unedited", { "鍵": `學員段落:${u.id}` }, { quiet: true });
    if (r && r["頁面"]) fc.nextData = { data: r["頁面"], at: Date.now() };
    fc.undo = null;
    fcFixReset();
    await fcReload();
  } catch (e) {
    alert(`復原沒有成功：${e.message}\n可以到「要修改」→「其他」把說話者改回去。`);
    if (btn) { btn.disabled = false; btn.textContent = "復原"; }
  }
}

// 10-15 第2段審查修正（建議）：只改備註、不影響聲音的欄位（問老師、問老師備註、消音的備註），存了不標「已改，等重做」
const FC_NOTE_FIELDS = ["問老師", "問老師備註", "備註"];
function fcNoteOnly(body) {
  const ks = Object.keys(body || {}).filter((k) => !["id", "鍵"].includes(k));
  return ks.length > 0 && ks.every((k) => FC_NOTE_FIELDS.includes(k));
}

// 第 5 步按「改」改第 3 步的東西：照第 3 步原本的 API 存，存好之後那張卡記「已改，等重做」（新增的另外記，見 fcEditorCtx）
async function fcStep3Post(url, body, opts) {
  fc.nextData = null;   // 這次存檔之前留的整頁資料不能再用
  const r = await apiPost(url, body, opts);
  // 10-15 要修改合併（1015-4）：帶 id 的是改範圍（「要修改」裡的改範圍），不是新增
  const adding = fc.ed.open && !fc.ed.id && url === "/api/review/manual" && !(body && body.id);
  const c = fcCard(fc.cur);
  if (!adding && c && c["第3步鍵"] && !String(url).startsWith("/api/final/") && !fcNoteOnly(body)) {
    // 10-16 #225：「裡面有老師的聲音」從重疊卡帶出它所在的學員段落、改的是那一段 → 標那一段的卡
    const host = fc.fix.open && fc.fix.pick === FC_FIX_TEACHER ? fcTeacherHost(fcItemOf(c)) : null;
    await fcMarkEdited(c, host ? rvKey(host) : null);
  }
  return r;
}

async function fcMarkEdited(c, key = null, isNew = false) {
  try {
    const r = await apiPost("/api/final/edited", { "鍵": key || c["第3步鍵"], "新增": isNew }, { quiet: true });
    // 10-15 第2段審查修正（建議，效能）：伺服器連同整頁資料一起回傳；接著的重新整理（fcReload）直接用，不再 GET 一次
    if (r["頁面"]) fc.nextData = { data: r["頁面"], at: Date.now() };
    // 10-15 第2段審查二修正：舊檔模式就記「這次畫面改過」（不只看 記了）
    if (fc.data && fc.data["卡片來源"] === "舊檔") { fc.legacyEdited = true; fcRenderTop(); fcRenderLower(); }
    if (r["記了"] && c && !key) {
      c["狀態"] = FC_ST.EDITED;
      if (typeof c["已通過的"] === "boolean") c["已通過的"] = false;   // 10-15 總檢查修正（1015-7）：改了就搬到上面清單
      fcRecountLocal();
      fcRenderStateBox();
      fcRenderTop();
      fcRenderLower();
      fcRenderTimeline();
    }
  } catch (e) { /* 記不到不擋存檔；重新整理後照新紀錄顯示 */ }
}

function fcRecountLocal() {
  const cards = fcCards();
  // 審查建議 4：「要重做」不自己算（會把沿用卡算進去、漏跨版本卡）——保留後端的（needs.redo_cards 張數）；
  // 這次改出了要重做的卡、後端數還是 0 時至少算 1 張（頂端才寫得出「改過的還沒重做」），重新整理後又是後端的
  const prev = (fc.data["卡片統計"] || {})["要重做"] || 0;
  const any = cards.some((c) => [FC_ST.BACK, FC_ST.EDITED].includes(c["狀態"]));
  fc.data["卡片統計"] = { ...(fc.data["卡片統計"] || {}), "全部": cards.length,
    "還沒看": cards.filter((c) => c["狀態"] === FC_ST.TODO).length, "要重做": Math.max(prev, any ? 1 : 0) };
  // 10-15 總檢查修正（1015-7）：後端算的「還要確認」「已通過的」已經不準，拿掉讓前端照兩個清單算（重新整理後又是後端的）
  delete fc.data["卡片統計"]["還要確認"];
  delete fc.data["卡片統計"]["已通過的"];
}

// ---------------------------------------------------------------------------
// 進入點
// ---------------------------------------------------------------------------

// ---------- 版本（10-15 第5步2.0 第3段，31 號設計 §6.5） ----------

function fcUrl() { return fc.ver ? `/api/final?版本=${encodeURIComponent(fc.ver)}` : "/api/final"; }
function fcOld() { return !!(fc.data && fc.data["看的是舊版"]); }
// （純函式）能不能按通過／取消通過：還沒生成、已改等重做、範圍外（沿用上一版）的卡不能。
// 10-15 宇軒驗證回饋六：「要修改」另外判斷（fcCanFix），所有卡都能按
// 10-16 #225 宇軒：「通過」變灰時，旁邊問號寫原因
function fcPassOffText(c) {
  if (c && c["這一版沒處理到"]) return "這張是上一版通過的，這一版沒改到這裡，不用再判斷。";
  if (c && c["狀態"] === FC_ST.EDITED) return `這張改過了，要按「重做這幾張」做出新的一版，才能看、才能通過。${fc.undo && fc.undo.card === c.id ? "按錯了：在卡片上方按「復原」。" : ""}`;
  return "這張還沒做進成品，按「重做這幾張」做出新的一版後才能看。";
}
function fcCanJudge(c) {
  return !!c && ![FC_ST.NOT_MADE, FC_ST.EDITED].includes(c["狀態"]) && !c["這一版沒處理到"];
}
// 選單每一項的字（純函式，§6.8）：「第 N 版　10/14 14:20（最新）」；開發者設定開著才多寫範圍標記
function fcVersionText(v, dev) {
  return `第 ${v["版本號"]} 版　${fcShortStamp(v["時間"])}${v["最新"] ? "（最新）" : ""}${dev && v["標記"] ? `　${v["標記"]}` : ""}`;
}
// 「看哪一版」選單（純函式）：開發者在舊版上多一顆「以這一版為基礎再組裝」
// 10-15 宇軒驗證回饋：只有 1 版時也顯示選單（裡面只有一項）；一版都沒有才不出現
function fcVersionMenuHtml(d) {
  const list = d["版本清單"] || [];
  if (!list.length) return "";
  const dev = !!d["開發者"];
  const opts = list.map((v) => `<option value="${esc(String(v["版本號"]))}" ${v["版本號"] === d["目前版本"] ? "selected" : ""}>${esc(fcVersionText(v, dev))}</option>`).join("");
  const base = dev && d["看的是舊版"] ? ` <button class="ghost small" id="fc-rebase">以這一版為基礎再組裝</button>` : "";
  return `<label class="fc-ver">看哪一版 <select id="fc-ver" aria-label="看哪一版">${opts}</select></label>${base}`;
}
function fcBindVersionMenu() {
  const sel = document.getElementById("fc-ver");
  if (sel) sel.addEventListener("change", () => {
    const n = Number(sel.value);
    const v = (fc.data["版本清單"] || []).find((x) => x["版本號"] === n);
    fc.ver = v && !v["最新"] ? n : null;
    fc.cur = null;
    renderFinal();
  });
  const rb = document.getElementById("fc-rebase");
  if (rb) rb.addEventListener("click", () => fcRebase(rb));
}
// 開發者：以舊版為基礎再組裝——照那一版的範圍開始第 4 步，新的一版的「沿用」先跟那一版比；跑完一樣跳回第 5 步
async function fcRebase(btn) {
  const r = fc.data["範圍"] || [null, null];
  btn.disabled = true;
  let started = null;
  try {
    started = (await apiPost("/api/execute/start", { start: r[0], end: r[1], methods: null, "基礎版本": fc.data["目前版本"] }, { quiet: true }))["started_at"];
  } catch (e) { alert(`還不能開始：${e.message}`); btn.disabled = false; return; }
  fcRedoBackSet(started);
  fc.ver = null;
  location.hash = "#step4";
}

// 10-15 宇軒驗證回饋三：整頁資料缺欄位時補預設值（舊版頁面、舊工作區少給的清單一律當空的；需留意沒有＝null，不出那一頁）
const FC_LIST_FIELDS = ["卡片", "跨版本卡片", "不修改", "不修改類型", "需留意類型", "看過區段", "版本清單", "退回原因選項", "片段"];
function fcFillDefaults(d) {
  d = d && typeof d === "object" ? d : {};
  for (const k of FC_LIST_FIELDS) if (!Array.isArray(d[k])) d[k] = [];
  if (!d["需留意"] || typeof d["需留意"] !== "object") d["需留意"] = null;
  else if (!Array.isArray(d["需留意"]["列"])) d["需留意"] = { ...d["需留意"], "列": [] };
  if (!d["卡片統計"] || typeof d["卡片統計"] !== "object") d["卡片統計"] = {};
  if (!d["狀態"] || typeof d["狀態"] !== "object") d["狀態"] = {};
  return d;
}

async function renderFinal() {
  cvUse(FC_CARD_CTX);
  contentEl.innerHTML = "<p>載入中…</p>";
  try { fc.data = fcFillDefaults(await apiGet(fcUrl())); }
  catch (e) { if (!fc.ver) throw e; fc.ver = null; fc.data = fcFillDefaults(await apiGet(fcUrl())); }   // 那一版不在了：回最新
  contentEl.classList.add("wide");
  const d = fc.data;
  // 1015-8 第 3 段（32 號設計 §5.2）：已結案＝瘦身版——卡片照樣列（都從 修改需求/ 讀），影片那一塊換一行灰字，
  // 聽／改／重做／輸出／拿檔灰掉（style.css 的 body.ws-closed）；頂端一句「這支影片已結案（…），這裡只能看紀錄」
  const closed = !!d["已結案"];
  if (!closed && !d["看的是舊版"] && (!d["有處理紀錄"] || !d["成品影片"])) {   // 31 號設計 §6.8：一句話＋連結（霧化預覽拿掉）
    contentEl.innerHTML = `<div class="rv2 fc"><h1>5　成品檢查</h1>
      <p class="fc-none">還沒有成品，請先到 <a href="#step4">第 4 步</a></p>${fcWhereHtml(d["專案標示"])}</div>`;
    return;
  }
  contentEl.innerHTML = `
    <div class="rv2 fc">
      <header class="rv-bar">
        <h1>5　成品檢查</h1>
        <div class="fc-vers">${fcVersionMenuHtml(d)}</div>
        <div class="rv-bar-btns"><span id="fc-redo-slot"></span><button class="ghost" id="fc-export" ${d["影片清掉了"] ? "disabled" : ""}>輸出成品</button></div>
      </header>
      ${closed && typeof wsClosedBarHtml === "function" ? wsClosedBarHtml(d) : ""}
      <div class="fc-status" id="fc-status" role="status"></div>
      <div class="fo-box" id="fc-fileout"></div>
      <section class="rv-upper">
        <div class="rv-left">
          ${closed ? `<p class="muted fc-gone">影片已經清掉了。</p>` : d["影片清掉了"] ? `<p class="rv-warnline">這一版的影片清掉了</p>` : ""}
          <video id="fc-video" controls preload="metadata" ${closed ? "hidden" : ""} ${d["影片網址"] && !closed ? `src="${esc(d["影片網址"])}${String(d["影片網址"]).includes("?") ? "&" : "?"}v=${encodeURIComponent(d["成品影片"] + "|" + (d["成品版本"] || ""))}"` : ""}></video>
          <div class="rv-timebar"><span class="rv-clock"><b id="fc-clock">0:00</b> ／ ${esc(fcFmt(d["成品長度"], 0))}</span>
            <input id="fc-goto" class="rv-goto" placeholder="跳到 43:15" aria-label="跳到原片時間（例如 43:15，按 Enter）">
            <select id="fc-rate" aria-label="播放速度"><option value="1">1 倍速</option><option value="1.25">1.25 倍速</option><option value="1.5">1.5 倍速</option><option value="2">2 倍速</option></select></div>
          <div class="rv-tl fc-tl" id="fc-tl" title="點一下或拖拉，跳到那個時間"></div>
          <div class="rv-legend"><span><i class="sw fc-todo"></i>還沒看</span><span><i class="sw fc-ok"></i>通過</span><span><i class="sw fc-back"></i>要重做</span></div>
          <p class="rv-meta fc-seenpct">看過 <b id="fc-pct">0</b>%</p>
          <div class="fc-actbar" id="fc-actbar"></div>
          <div class="rv-io" id="fc-io"></div>
        </div>
        <div class="rv-right" id="fc-right"></div>
      </section>
      <section class="rv-lower" id="fc-lower"></section>
      <audio id="fc-audio" preload="none"></audio>
    </div>`;
  fc.video = document.getElementById("fc-video");
  fc.audio = document.getElementById("fc-audio");
  Object.assign(fc, { seen: [], seg: null, sentRanges: "", ab: null, ed: { open: false }, pend: [], fixEd: null,
    legacyEdited: false, nextData: null });
  fcFixReset();
  if (!fc.cur || !fcCard(fc.cur)) fc.cur = fcFirstId();
  document.getElementById("fc-export").addEventListener("click", fcExport);
  fcBindVersionMenu();
  fileOutInit(document.getElementById("fc-fileout"));   // 10-07：打開成品資料夾、複製到 Windows
  document.getElementById("fc-rate").addEventListener("change", (e) => { fc.video.playbackRate = Number(e.target.value); });
  // 10-15 宇軒驗證回饋二：「跳到」框加回來（照第 3 步：打原片時間按 Enter 跳過去；打錯框變紅）
  document.getElementById("fc-goto").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.isComposing) return;
    e.preventDefault();
    const src = rvParseTime(e.target.value);
    const t = src == null ? null : fcGotoOut(src);
    e.target.classList.toggle("bad", t == null);
    if (t != null) { fcSeek(t); e.target.blur(); }
  });
  fc.audio.addEventListener("ended", () => { if (fc.ab && fc.ab.which === "原本") fcStopAB(); });
  fcBindVideo();
  fcBindTimeline(document.getElementById("fc-tl"));
  window.addEventListener("resize", fcRenderTimeline);
  fcRenderAll();
  fcStartWatchTracking();
  if (fcBackKey) {
    const key = fcBackKey;
    fcBackKey = null;
    const c = fcCards().find((x) => x.id === key || x["第3步鍵"] === key);
    if (c) fcSelect(c.id);
    else if ((fc.data["不修改"] || []).some((u) => u["鍵"] === key)) { fcTabSet(FC_TAB_KEEP); fcRenderLower(); }
  }
}

// 現在在哪個工作區（純函式；名稱一樣的專案分得出來；只在沒有成品時顯示）
function fcWhereHtml(lab) {
  return lab && lab["顯示名稱"] ? `<p class="rv-meta fc-where">現在在：<b>${esc(lab["顯示名稱"])}</b>（${esc(lab["標示"] || "")}）。要看別的專案，到 <a href="#overview">總覽</a> 切換。</p>` : "";
}

async function fcReload() {
  const pre = fc.nextData;
  fc.nextData = null;
  // 10-15 第2段審查修正（建議，效能）：剛存檔、/api/final/edited 回傳的整頁資料（幾秒內）直接用
  fc.data = fcFillDefaults(pre && !fc.ver && Date.now() - pre.at < 5000 ? pre.data : await apiGet(fcUrl()));
  if (fc.cur && !fcCard(fc.cur)) fc.cur = fcFirstId();
  fcRenderAll();
}

// 10-15 總檢查修正（1015-7）：一開啟、切版本、目前這張不見了，預設黃底＝上一張／下一張走的順序的第一張（上面「還要確認的」第一張；
// 上面空了就是「已通過的」第一張）。以前是「這一版第一張還沒看的」，最新版只有跨版本卡、沿用卡時落在「已通過的」第一張
function fcFirstId() { return (fcNavList()[0] || fcCards()[0] || {}).id || null; }

function fcRenderAll() {
  fcRenderTop();
  fcRenderTimeline();
  fcRenderRight();
  fcRenderLower();
}

// ---------- 頂端：一句狀態、輸出 ----------

function fcRenderTop() {
  const box = document.getElementById("fc-status");
  if (!box) return;
  // 10-15 總檢查修正（1015-7）：舊版不再把要重做硬設 0（fcStatusLine 看 看的是舊版 自己換句子）；張數用後端的「還要確認」
  const s = fcStatusLine({ ...fc.data, "舊檔改過": !!fc.legacyEdited && fc.data["卡片來源"] === "舊檔" });
  // §4.4 修訂：新紀錄不能用時退回讀舊檔，提示一句（小字，不搶頂端那一句）。
  // 10-15 第2段審查修正（暫定，等宇軒）：只在開發者設定開著時顯示（老師看不懂、也不用做什麼）
  const old = fc.data["卡片來源"] === "舊檔" && fc.data["開發者"] ? `<span class="rv-meta fc-oldsrc">（先照舊的紀錄顯示）</span>` : "";
  box.innerHTML = s.go ? `<button class="linkish ${s.cls}" id="fc-status-go">${esc(s.text)}</button>${old}` : `<span class="${s.cls}">${esc(s.text)}</span>${old}`;
  const go = document.getElementById("fc-status-go");
  if (go) go.addEventListener("click", () => { fcTabSet(FC_TAB_ATT); fcRenderLower(); document.getElementById("fc-lower").scrollIntoView({ block: "start" }); });
  const pct = document.getElementById("fc-pct");
  if (pct) pct.textContent = String(Math.floor(((fc.data["狀態"] || {})["看過比例"] || 0) * 100));
  const ex = document.getElementById("fc-export");
  if (ex) ex.disabled = !fc.data["有處理紀錄"] || !fc.data["成品影片"];   // 那一版的影片清掉了：不能輸出
}

// ---------- 影片、時間軸 ----------

function fcBindVideo() {
  const v = fc.video;
  v.addEventListener("timeupdate", () => {
    const clock = document.getElementById("fc-clock");
    if (!clock) return;   // 已經換到別的步驟
    clock.textContent = fcFmt(v.currentTime, 1);
    fcAbTick(v.currentTime);
    fcSpanTick(v.currentTime);
    fcMoveHead();
    fcFollow(v.currentTime);
    fcTrack();
  });
  const stop = () => {
    const t = v.currentTime;
    if (fc.seg && t >= fc.seg[1] && t - fc.seg[1] < 1.0) fc.seg[1] = t;
    fcEndSeg();
  };
  v.addEventListener("pause", () => { stop(); fcSendWatched(); });
  v.addEventListener("ended", () => { stop(); fcSendWatched(); });
  v.addEventListener("seeking", stop);
  v.addEventListener("seeked", () => { const c = document.getElementById("fc-clock"); if (c) c.textContent = fcFmt(v.currentTime, 1); fcMoveHead(); });
  v.addEventListener("ratechange", () => fcEndSeg());
}

function fcFollow(t) {   // 影片照常播、跨過某張卡的起點，右邊換到那張（「要修改」開著、試聽中不換）
  const prev = fc.lastT;
  fc.lastT = t;
  if (fc.fix.open || fc.ab || t < prev || t - prev > 2) return;
  const hit = fcCards().filter((c) => c["成品"] && c["成品"][0] != null && c["成品"][0] > prev && c["成品"][0] <= t).pop();
  if (hit && hit.id !== fc.cur) { fc.cur = hit.id; fcRenderRight(); fcRenderTimeline(); fcMarkRow(); }
}

// 10-15 宇軒驗證回饋二：（純函式）「跳到」框打的原片時間 → 成品時間。那裡在成品裡剪掉了就跳到下一段留下來的開頭；
// 比成品最後一段還晚回 null（框變紅）
function fcGotoOut(t, plist) {
  if (plist === undefined) plist = fc.data && fc.data["片段"];
  if (t == null || t < 0) return null;
  const o = fcOutTime(t, plist);
  if (o != null || !plist || !plist.length) return o;
  let acc = 0;
  for (const p of plist) {
    if (p.src[0] >= t) return acc;
    acc += p.src[1] - p.src[0] + p.freeze;
  }
  return null;
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

// 時間軸上的點：卡片，照狀態上色（還沒看／通過／退回；已改、還沒生成算退回那一色）
function fcRenderTimeline() {
  const el = document.getElementById("fc-tl");
  if (!el || !fc.data) return;
  const dur = fc.data["成品長度"] || 1;
  const pct = (t) => (t / dur) * 100;
  const marks = fcCards().filter((c) => c["成品"] && c["成品"][0] != null).map((c) => {
    const [s, e] = c["成品"];
    const st = fcCardLabel(c).cls;
    const cls = st === "ok" ? "fc-ok" : st === "todo" ? "fc-todo" : "fc-back";
    const w = e != null && e > s ? Math.max(0.25, pct(e - s)) : 0;
    return `<i class="fm ${cls}${w ? "" : " pt"}" style="left:${pct(s)}%;${w ? `width:${w}%` : ""}"></i>`;
  }).join("");
  const seen = (fc.data["看過區段"] || []).map(([s, e]) => `<i class="seen" style="left:${pct(s)}%;width:${pct(e - s)}%"></i>`).join("");
  const cur = fc.cur && fcCard(fc.cur);
  const curMark = cur && !cur["跨版本"] && cur["成品"] && cur["成品"][0] != null ? `<i class="cur" style="left:${pct(cur["成品"][0])}%;width:${Math.max(0.3, pct((cur["成品"][1] || cur["成品"][0]) - cur["成品"][0]))}%"></i>` : "";
  el.innerHTML = `<div class="track"></div>${marks}${curMark}<div class="seenbar">${seen}</div><i class="head" id="fc-head" style="left:${pct(fc.video ? fc.video.currentTime : 0)}%"></i>`;
}

function fcMoveHead() {
  const head = document.getElementById("fc-head");
  if (head && fc.video) head.style.left = `${(fc.video.currentTime / (fc.data["成品長度"] || 1)) * 100}%`;
}

// ---------- 右邊：目前這張卡 ----------

// 10-15 總檢查修正（1015-7）：（純函式）舊版畫面上、最新版要重做的卡底下那一行小字：「最新版：要重做（原因）」；沒有＝空字串。
// 那一版自己的標籤照那一版（後端 version_page 不再把它蓋成要重做，卡片多 最新版要重做: {選項, 補充}）
function fcLatestRedoLine(c) {
  const w = c && c["最新版要重做"];
  if (!w) return "";
  const why = fcReasonText(w);
  return why && why !== FC_NO_REASON ? `最新版：要重做（${why}）` : "最新版：要重做";
}

function fcStateBoxHtml(c) {
  const lab = fcCardLabel(c);
  const back = c["狀態"] === FC_ST.BACK;
  const shared = back ? fcSharedLine(c) : "";
  // 10-15 要修改合併（1015-4）：已改的卡有記原因（要修改先記原因再改）也列原因那一行
  const why = back || (c["狀態"] === FC_ST.EDITED && c["退回原因"]);
  const latest = fcLatestRedoLine(c);
  return `<span class="fc-tag fc-tag-${lab.cls}"${c["這一版沒處理到"] ? ` title="${esc(lab.small.join("；"))}"` : ""}>${esc(lab.text)}</span>${lab.small.map((x) => ` <span class="rv-meta">${esc(x)}</span>`).join("")}
    ${why ? `<p class="rv-warnline fc-why">${esc(fcWhyLine(c))}</p>` : ""}${shared ? `<p class="rv-warnline fc-shared">${esc(shared)}</p>` : ""}${latest ? `<p class="rv-meta fc-latest-redo">${esc(latest)}</p>` : ""}`;
}

function fcRenderStateBox() {
  const box = document.getElementById("fc-statebox");
  const c = fc.cur && fcCard(fc.cur);
  if (box && c) box.innerHTML = fcStateBoxHtml(c);
}

// 10-15 宇軒驗證回饋：AI 重新生成的卡（學員重念、名字整句換掉、換聲音、學員名字換代號），卡片上列兩行——
// 「原本：」校對稿的原句、「改成：」要念的字（處理紀錄的「文字」＝標字版的「餵給模型的文字」）；沒改字時兩行一樣也照列。
// 原句（純函式）：第 3 步那張卡的原文（學員段落、改成老師）／整句原文（名字、學員名字）／老師整句原文或兩邊的字（重疊）
function fcOrigText(it) {
  if (!it) return "";
  const t = it["類型"];
  if (t === "學員段落" || t === "改成老師") return it["原文"] || it["校對稿"] || "";
  if (t === "名字" || t === "學員名字") return (it["整句"] || {})["原文"] || "";
  if (t === "重疊") return ((it["老師整句"] || {})["原文"]) || [it["老師文字"], it["學員文字"]].filter((x) => x).join("／");
  return "";
}
// 卡片上的字（純函式）：有要念的字＝兩行；沒有＝照舊一行摘要
function fcCardTextHtml(c, it) {
  const said = (c["要念的字"] || []).join("　");
  if (!said) return `<p class="fc-cardtext">${esc(fcWho(fcPreview(c, it)))}</p>`;
  const orig = fcOrigText(it);
  // 10-15 宇軒驗證回饋：超過 2 行各自收合只顯示前 2 行，右邊「展開 ▾」（超過才出現，見 fcClampSetup）；「原本」「改成」各自開關
  const line = (k, lab, body) => `<div class="fc-line"><p class="fc-clamp" data-fc-line="${k}"><span class="fc-lab">${lab}</span>${body}</p>`
    + `<button type="button" class="fc-more" data-fc-more="${k}" aria-expanded="false" hidden>展開 ▾</button></div>`;
  return `<div class="fc-cardtext fc-lines">
        ${line("orig", "原本：", orig ? esc(fcWho(orig)) : '<span class="rv-meta">（找不到原句）</span>')}
        ${line("said", "改成：", esc(fcWho(said)))}</div>`;
}
// 10-15 宇軒驗證回饋：畫完卡片後量一次——字超過 2 行（scrollHeight > clientHeight）才露出「展開 ▾」；
// 按了整段打開、字改「收起 ▴」，再按收回。重畫卡片時一律回到收合。
function fcClampSetup(box) {
  if (!box) return;
  box.querySelectorAll(".fc-clamp").forEach((p) => {
    const btn = box.querySelector(`[data-fc-more="${p.dataset.fcLine}"]`);
    if (!btn) return;
    const over = p.scrollHeight > p.clientHeight + 1;
    btn.hidden = !over;
    if (!over) return;
    btn.addEventListener("click", () => {
      const open = !p.classList.contains("open");
      p.classList.toggle("open", open);
      btn.textContent = open ? "收起 ▴" : "展開 ▾";
      btn.setAttribute("aria-expanded", String(open));
    });
  });
}

// 10-15 宇軒驗證回饋二：（純函式）卡片標題的時間範圍，照第 3 步卡片標題的寫法（review.js rvRenderCard 的 when：原片時間、
// 名字卡寫重念範圍）；沒有第 3 步那張卡的照卡片的原片時間。成品時間另外小字（fcOutWhen）
function fcWhenText(c, it) {
  if (it) {
    const w0 = it["整句"];
    return it["類型"] === "名字" && w0 && !it["老師整段"]
      ? `重念範圍 ${rvFmt(w0.start, 1)}–${rvFmt(w0.end, 1)}（名字在 ${rvFmt(it.start, 1)}）` : `${rvFmt(it.start, 1)}–${rvFmt(it.end, 1)}`;
  }
  return fcSpan(c && c["原片"]);
}
function fcOutWhen(c) {
  const p = c && c["成品"];
  return p && p[0] != null && !c["跨版本"] ? `成品 ${fcSpan(p)}` : "";
}

function fcRenderRight() {
  const box = document.getElementById("fc-right");
  if (!box) return;
  const c = fc.cur && fcCard(fc.cur);
  if (!c) { box.innerHTML = `<div class="rv-card empty"><p>這一版沒有任何修改</p></div>`; return; }
  if (fc.fix.open && fc.fix.card !== c.id) fcFixReset();   // 換了一張卡（重新整理後那張不在了）：收起「要修改」
  const it = fcItemOf(c);
  // 10-15 宇軒驗證回饋二：這張可能已不在這一頁——照全部算第幾張
  // 10-15 宇軒驗證回饋六：照上一張／下一張走的順序（上面清單接下面清單）算
  const nav = fcNavList();
  const list = nav.some((x) => x.id === c.id) ? nav : fcCards().concat(fcCrossCards());
  const idx = list.findIndex((x) => x.id === c.id);
  const canJudge = fcCanJudge(c);
  const canFix = fcCanFix(c, fcOld());   // 10-15 宇軒驗證回饋六：所有卡都能按（10-15 總檢查修正（1015-7）：刪掉／併入的卡除外）
  const abPrev = fcCardAbPlan(c, "上一版");
  const back = fcCanUndo(c, fcOld());   // 10-15 總檢查修正（1015-7）：等重做那條路的卡也有「取消要重做」
  const abNow = fcCardAbPlan(c, "現在"), abOrig = fcCardAbPlan(c, "原本");
  const open = fc.fix.open && canFix;
  // 選了「念錯字」「其他」：下面列出第 3 步卡片的字（可以改），上面那兩行就不重複列
  const bodyShown = open && [FC_FIX_TEXT, ...FC_FIX_FULL].includes(fc.fix.pick) && fcFixCanEdit(it);
  const when = fcWhenText(c, it), outWhen = fcOutWhen(c);
  const passed = c["狀態"] === FC_ST.OK;
  box.innerHTML = `<article class="rv-card fc-card" data-id="${esc(c.id)}">
      <header>${fcChip(c, it)}${when ? `<span class="rv-when">${esc(when)}</span>` : ""}${outWhen ? `<span class="rv-meta fc-outwhen">${esc(outWhen)}</span>` : ""}${c["編號"] ? `<span class="idtag" title="跟 AI 助手溝通用的編號">#${esc(String(c["編號"]))}</span>` : ""}
        <span class="rv-count">第 ${idx + 1}／${list.length} 張</span></header>
      ${fcUndoHtml(c)}
      <div class="rv-statebox" id="fc-statebox">${fcStateBoxHtml(c)}</div>
      ${bodyShown ? "" : fcCardTextHtml(c, it)}
      <div class="rv-row fc-ab">
        <button class="ghost small" data-ab="原本" ${abOrig ? "" : "disabled"}>原本 ▶</button>
        <button class="ghost small" data-ab="現在" ${abNow ? "" : "disabled"}>現在 ▶</button>${abPrev ? `
        <button class="ghost small" data-ab="上一版">上一版 ▶</button>` : ""}
        ${cvPadHtml("fc-pad")}</div>
      <div class="rv-actions">
        <button class="${passed ? "primary on" : "primary"}" id="fc-pass" title="已通過的再按一次會取消" aria-pressed="${passed}" ${canJudge ? "" : "disabled"}>${passed ? "✓ 已通過（再按取消）" : "通過"}<kbd>Enter</kbd></button>${canJudge ? "" : helpTip(fcPassOffText(c), "為什麼不能按通過")}
        <button class="ghost${open ? " on" : ""}" id="fc-fix" aria-expanded="${open}" ${canFix ? "" : `disabled title="${esc(fcFixOff(c, fcOld()))}"`}>要修改<kbd>E</kbd></button>${back ? `
        <button class="ghost small" id="fc-back-undo">取消要重做</button>` : ""}
        <span class="spacer"></span>
        <button class="ghost" id="fc-prev">上一張</button><button class="ghost" id="fc-next">下一張</button>
      </div>
      ${open ? `<div class="rv-more fc-fixbox" id="fc-fixbox">${fcFixHtml(c, it)}</div>` : ""}
    </article>`;
  box.querySelectorAll("[data-ab]").forEach((b) => b.addEventListener("click", () => fcPlayAB(c, b.dataset.ab)));
  cvBindPad("fc-pad");   // 10-15 1015-5：這張卡唯一的一顆；「要修改」裡的「聽這一段」也照它
  document.getElementById("fc-pass").addEventListener("click", () => fcDecide(c, c["狀態"] === FC_ST.OK ? null : FC_ST.OK));
  document.getElementById("fc-fix").addEventListener("click", () => fcToggleFix());
  const undoBtn = document.getElementById("fc-undo");
  if (undoBtn) undoBtn.addEventListener("click", fcUndo);
  const undo = document.getElementById("fc-back-undo");   // 10-15 要修改合併（1015-4）：要重做的卡的「取消要重做」放外面
  if (undo) undo.addEventListener("click", () => fcDecide(c, null, [], "", true));   // 10-15 修正：明說是取消要重做（後端沒有要重做就擋，不會變成取消通過）
  document.getElementById("fc-prev").addEventListener("click", () => fcStep(-1));
  document.getElementById("fc-next").addEventListener("click", () => fcStep(1));
  if (open) fcFixBind(c, it);
  fcClampSetup(box);
}

// ---------- 10-15 要修改合併（1015-4）：「退回」「改」合成一顆「要修改」 ----------
// 按「要修改」→「哪裡出問題？」單選（念錯字／聲音不像、不自然／長度或停頓怪／裡面有老師的聲音／其他）→ 選了直接帶出對應的修改控制區
// （不另外按儲存）→「確定」：記原因（/api/final/card 退回＋選項＋補充）＋套用修改（第 3 步原本的 API，fcStep3Post 記「已改，等重做」）。
// 「只想換一種念法」就是「聲音不像、不自然」那條（只記原因、等重做）。

const FC_FIX_TEXT = "念錯字", FC_FIX_VOICE = "聲音不像、不自然", FC_FIX_RANGE = "長度或停頓怪", FC_FIX_TEACHER = "裡面有老師的聲音", FC_FIX_OTHER = "其他";
const FC_FIX_OPTS = [FC_FIX_TEXT, FC_FIX_VOICE, FC_FIX_RANGE, FC_FIX_TEACHER, FC_FIX_OTHER];
// 10-15 宇軒驗證回饋二：「裡面有老師的聲音」跟「其他」一樣帶出整個修改選單（第 3 步的「改做法」＋工具按鈕）＋補充
const FC_FIX_FULL = [FC_FIX_TEACHER, FC_FIX_OTHER];
const FC_FIX_NO_VOICE = "這一張重做只會重新放一次，聲音不會變；要改內容請選下面其他項";
function fcFixReset() { fc.fix = { open: false, card: null, pick: null, extra: "", busy: false, err: "" }; }

// （純函式）這張卡可不可以按「要修改」
// 10-15 宇軒驗證回饋四、六：不論狀態、跨版本、看哪一版，有卡就能按（old 不再用，留著參數不改呼叫的地方）
// 10-15 總檢查修正（1015-7）：刪掉／併入的卡不能按（沒有可以重做的；後端 decide 也擋）。後端給 不能修改（布林）就照它；
// 沒給時看 刪掉了（過渡）
function fcNoFix(c) {
  return !!c && (typeof c["不能修改"] === "boolean" ? c["不能修改"] : !!c["刪掉了"]);
}
function fcCanFix(c, old) {
  return !!c && !fcNoFix(c);
}
// （純函式）「要修改」停用時滑過的字：沒卡時「先選一張卡」；刪掉／併入的卡照後端給的 不能修改原因
function fcFixOff(c, old) {
  if (!c) return "先選一張卡";
  return c["不能修改原因"] || "這張卡已刪掉，沒有可以重做的";
}
// 10-15 總檢查修正（1015-7）：（純函式）這張卡外面要不要放「取消要重做」（按了送 /api/final/card 結果 null）：
// 審查修正 3c：看後端的 c["可取消要重做"]（舊版頁、跨版本卡＝那一版是退回；最新版頁＝最新版退回、或卡片的等重做有原因——
// 不論是不是 範圍外改了：只重新組裝之後變成最新版自己的卡、第 3 步選定不修改的舊卡也撤得掉）；沒有這個欄位照舊判斷。
// 刪掉／併入的不放
function fcCanUndo(c, old) {
  if (!c || fcNoFix(c)) return false;
  if (typeof c["可取消要重做"] === "boolean") return c["可取消要重做"];
  if (c["狀態"] === FC_ST.BACK) return true;
  return !old && c["狀態"] === FC_ST.EDITED && !!c["範圍外改了"] && !!c["退回原因"];
}
// 10-15 宇軒驗證回饋六：（純函式）記原因時帶的「版本」——跨版本卡、看舊版時的任何卡帶那張卡的版本號；最新版這一版的卡不帶（正常路）。
// 10-15 總檢查修正（1015-7）：帶了版本由後端分流（最新版有它狀態列的卡照樣走最新版正常路，其他記在卡片的等重做）
function fcFixVer(c, old) {
  return c && (c["跨版本"] || old) && c["版本號"] != null ? c["版本號"] : null;
}
// （純函式）「哪裡出問題？」列哪幾項：沒有聲音可以重新生成的卡（剪掉、消音、還沒生成…）不列「聲音不像、不自然」
function fcFixOptions(c, opts) {
  const list = (opts && opts.length ? opts : FC_FIX_OPTS).filter((o) => FC_FIX_OPTS.includes(o));
  return c && c["可重新生成"] ? list : list.filter((o) => o !== FC_FIX_VOICE);
}
// （純函式）確定時要不要記原因（送 /api/final/card 退回）：還沒生成的卡（新增的、這一版之後才出現的）成品裡沒有，記不了
// 已改的卡要還在這一版成品裡（有對到處理紀錄）才記得了（後端 finalcards.decide 同一條規則）
// 10-15 宇軒驗證回饋四：範圍外的卡（c["這一版沒處理到"]、或已經按過「要修改」的 c["範圍外改了"]）原因記在卡片的等重做（後端分流）
// 10-15 宇軒驗證回饋六：跨版本卡、看舊版時的任何卡也是（old＝在看舊版）
function fcFixRecords(c, old) {
  if (c && (c["這一版沒處理到"] || c["範圍外改了"] || c["跨版本"] || old)) return true;
  return !!c && c["狀態"] !== FC_ST.NOT_MADE && !(c["狀態"] === FC_ST.EDITED && !(c["紀錄鍵"] || []).length);
}
function fcFixCanEdit(it) { return !!it && !(fc.data && fc.data["AI執行中"]); }

// 10-15 宇軒驗證回饋二：（純函式）已經是「要重做」的卡再按「要修改」，帶入上次記的原因：{pick, extra}
// （改過的字、範圍本來就存進第 3 步的資料，控制區照現在的值畫；這裡只補「哪裡出問題」與補充）
// 10-15 總檢查修正（1015-7）：舊版畫面上最新版要重做的卡（c["最新版要重做"]），預填它的原因
function fcFixPrefill(c, opts) {
  const why = c && c["最新版要重做"] ? c["最新版要重做"] : c && [FC_ST.BACK, FC_ST.EDITED].includes(c["狀態"]) ? c["退回原因"] : null;
  if (!why) return { pick: null, extra: "" };
  const pick = (why["選項"] || []).find((o) => (opts || FC_FIX_OPTS).includes(o)) || null;
  return { pick, extra: String(why["補充"] || "") };
}

function fcToggleFix() {
  const c = fc.cur && fcCard(fc.cur);
  if (!fc.fix.open && !fcCanFix(c, fcOld())) return;
  if (fc.fix.open) fcFixReset();
  else {
    // 10-15 宇軒驗證回饋二：改完可以持續調整——要重做的卡再按「要修改」照樣打開、帶入上次的選項與補充；
    // 改範圍的編輯器從現在（上次改好）的起訖重新開始
    const pre = fcFixPrefill(c, fcFixOptions(c, fc.data["退回原因選項"]));
    fc.fixEd = null;
    fc.fix = { open: true, card: c.id, pick: pre.pick, extra: pre.extra, busy: false, err: "" };
  }
  fcRenderRight();
  const box = document.getElementById("fc-fixbox");
  if (box) box.scrollIntoView({ block: "nearest" });
}
// 「改」裡的東西要打開「其他」那一塊時（例如重疊卡按「改時間」）
function fcOpenFix(pick) {
  const c = fc.cur && fcCard(fc.cur);
  if (!fcCanFix(c, fcOld())) return;
  fc.fix = { open: true, card: c.id, pick, extra: fc.fix.extra || "", busy: false, err: "" };
  fcRenderRight();
}

// 沒有可以直接改的東西時那一行
function fcFixCantHtml(it) {
  if (it && fc.data["AI執行中"]) return `<p class="rv-meta">正在重做，做完才能改；按「確定」先記下原因</p>`;
  return `<p class="rv-meta">這一張沒有可以直接改的地方；按「確定」會記下原因、等重做</p>`;
}

// 選了哪一項之後帶出的修改控制區（每一項一塊）
function fcFixBranchHtml(c, it, pick) {
  const can = fcFixCanEdit(it);
  if (pick === FC_FIX_VOICE) return `<p>會換一種念法重新生成</p>`;
  if (pick === FC_FIX_TEXT) {
    if (!can) return fcFixCantHtml(it);
    const body = rvBodyHtml(it);
    const hasBox = /id="rv-(text|namesay)"/.test(body);
    return `<div class="rv-body">${body}</div>${hasBox ? `<p class="rv-meta">改好要念的字，再按「確定」</p>`
      : `<p class="rv-meta">這一種卡沒有字可以改；按「確定」會記下原因、等重做</p>`}`;
  }
  if (pick === FC_FIX_RANGE) {
    if (!can) return fcFixCantHtml(it);
    if (!RV_ITEM_KIND[it["類型"]]) return `<p class="rv-meta">這一種卡不能改範圍；按「確定」會記下原因、等重做</p>`;
    return `${rvEdgeHtml(it)}<div id="fc-fix-range"></div><p class="rv-meta">改好起點、終點，再按「確定」</p>`;
  }
  // 10-15 宇軒驗證回饋二：「裡面有老師的聲音」不再只做「學員段落整段改老師」，跟「其他」一樣帶出整個第 3 步的「改」選單
  // （整段是老師：勾「這段其實是老師」；只有一部分：「從游標處切開」再勾）
  if (!can) return fcFixCantHtml(it);   // 裡面有老師的聲音、其他：整個第 3 步的「改」選單
  if (pick === FC_FIX_TEACHER) return fcTeacherHtml(it);
  return `<div class="rv-body">${rvEdgeHtml(it)}${rvBodyHtml(it)}${rvAlignLine(it)}</div>
      <div class="rv-actions">${cvToolBtnsHtml(it, true)}</div>
      <div class="rv-more" id="rv-more">${rvMoreHtml(it)}</div>`;
}

// 10-16 #225 宇軒：「裡面有老師的聲音」照卡片類型給做法（四個選項每種卡都留）；說明收進問號
const FC_T_TIP_WHOLE = "整段都是老師在講：按這個，這一段改回老師原聲，不重念。";
const FC_T_TIP_SPLIT = "<ul><li>在上面的字裡，把游標放在老師開始講的第一個字前面</li><li>按「從游標處切開」</li><li>切開後，到老師講的那一段按「整段改成老師」</li></ul>";
const FC_T_LEAD = {
  "名字": ["這張本來就是老師的聲音", "<ul><li>重念的 AI 聲音不像老師 → 改選「聲音不像、不自然」</li><li>範圍切到別人的話或多切了 → 用下面的重念範圍調整</li><li>其實是學員在講 → 用「改時間」或改選「其他」</li></ul>"],
  "重疊": ["老師的話被處理掉了？", "重疊是老師和學員同時講話。聽到老師的話不見或被學員蓋過：在下面改做法（例如「生成老師聲音」），或用「改時間」調整範圍。"],
  "刪除段落": ["剪到老師的話？", "用「改時間」把範圍縮小，或在下面按「還原（不剪）」。"],
  "局部消音": ["消掉老師的話？", "用「改時間」把範圍縮小，或在下面按「還原（不消音）」。"],
};
// 重疊卡落在學員段落裡：問題其實在那一段學員段落（第 3 步「跳到那一筆」同一套找法）
function fcTeacherHost(it) {
  if (!it || it["類型"] !== "重疊" || !it["涵蓋"]) return null;
  const c = it["涵蓋"];
  if (c["類型"] && c["類型"] !== "學員段落") return null;   // 涵蓋的可能是名字整句或另一筆重疊（審查建議 3）
  const stu = cvItems().filter((x) => x["類型"] === "學員段落");
  return stu.find((x) => x.id === c.id) || stu.find((x) => x.start <= c.start + 0.05 && c.end - 0.05 <= x.end) || null;
}
function fcTeacherSubject(it) { return fcTeacherHost(it) || it; }
function fcTeacherHtml(it) {
  const host = fcTeacherHost(it);
  const s = host || it;
  if (s["類型"] === "學員段落" && !rvInCut(s)) {
    const more = rvMoreHtml(s).replace('<button class="ghost" id="rv-split">從游標處切開</button>', "");
    const tools = cvToolBtnsHtml(s, true).replace(/<button class="ghost" id="rv-isteacher"[^>]*>[^<]*<\/button>/, "");
    return `${host ? `<p class="rv-meta">這一處在〈學員段落 ${esc(rvFmt(host.start))}〉裡，下面改的是那一段</p>` : ""}
      <div class="rv-body">${rvBodyHtml(s)}</div>
      <div class="rv-actions fc-tacts"><button class="ghost" id="rv-isteacher">整段改成老師</button>${helpTip(FC_T_TIP_WHOLE)}
        <button class="ghost" id="rv-split">從游標處切開</button>${helpTip(FC_T_TIP_SPLIT)}</div>
      <details class="fc-other"><summary>其他做法</summary>
        <div class="rv-actions">${tools}</div>
        <div class="rv-more" id="rv-more">${more}</div></details>`;
  }
  const lead = FC_T_LEAD[s["類型"]];
  return `${lead ? `<div class="fc-tlead">${esc(lead[0])}${helpTip(lead[1])}</div>` : ""}
    <div class="rv-body">${rvEdgeHtml(s)}${rvBodyHtml(s)}${rvAlignLine(s)}</div>
      <div class="rv-actions">${cvToolBtnsHtml(s, true)}</div>
      <div class="rv-more" id="rv-more">${rvMoreHtml(s)}</div>`;
}

function fcFixHtml(c, it) {
  const f = fc.fix;
  const opts = fcFixOptions(c, fc.data["退回原因選項"]);
  if (!f.pick) {
    return `<p><b>哪裡出問題？</b></p>
      ${c["可重新生成"] ? "" : `<p class="rv-warnline">${esc(FC_FIX_NO_VOICE)}</p>`}
      <div class="rv-field rv-choices fc-fixopts">${opts.map((o) => `<label class="rv-radio"><input type="radio" name="fc-fix-opt" class="fc-fix-opt" value="${esc(o)}"> ${esc(o)}</label>`).join("")}</div>
      <div class="rv-row"><button class="ghost small" id="fc-fix-cancel">收起</button></div>`;
  }
  return `<p class="fc-fixpick">哪裡出問題：<b>${esc(f.pick)}</b> <button class="ghost small" id="fc-fix-repick">改選</button></p>
      ${fcFixBranchHtml(c, it, f.pick)}
      <label class="rv-field">補充（可不填）<input id="fc-fix-extra" value="${esc(f.extra || "")}"></label>
      ${f.err ? `<p class="rv-warnline">${esc(f.err)}</p>` : ""}
      ${fcFixRecords(c, fcOld()) ? "" : `<p class="rv-meta">這一張還沒做進成品，按「確定」只會套用修改</p>`}
      <div class="rv-row"><button class="primary small" id="fc-fix-go" ${f.busy ? "disabled" : ""}>${f.busy ? "存檔中…" : "確定"}</button>
        <button class="ghost small" id="fc-fix-cancel">收起</button></div>`;
}

function fcFixBind(c, it) {
  const f = fc.fix;
  document.querySelectorAll(".fc-fix-opt").forEach((r) => r.addEventListener("change", () => {
    f.pick = r.value; f.err = "";
    if (f.pick === FC_FIX_RANGE && it) fcFixRangeInit(it);
    fcRenderRight();
  }));
  // 10-15 宇軒驗證回饋二：「取消」改名「收起」——只收合，不撤回（已經存進去的改字、改範圍照留；原因要「確定」才記）
  const cancel = document.getElementById("fc-fix-cancel");
  if (cancel) cancel.addEventListener("click", () => { fcFixReset(); fcRenderRight(); });
  const repick = document.getElementById("fc-fix-repick");
  if (repick) repick.addEventListener("click", () => { f.pick = null; f.err = ""; fcRenderRight(); });
  const extra = document.getElementById("fc-fix-extra");
  if (extra) extra.addEventListener("input", () => { f.extra = extra.value; });
  const go = document.getElementById("fc-fix-go");
  if (go) go.addEventListener("click", () => fcFixConfirm(c, it));
  if (!f.pick || !fcFixCanEdit(it)) return;
  // 帶出的控制區：第 3 步卡片元件原本的綁定（存檔走 FC_CARD_CTX.post＝fcStep3Post）
  if (f.pick === FC_FIX_TEXT) { rvBindBody(it); rvBindOutQ(it); }
  else if (f.pick === FC_FIX_RANGE && RV_ITEM_KIND[it["類型"]]) {
    if (!fc.fixEd || fc.fixEd.id !== it.id) fcFixRangeInit(it);
    cvBindPlayShrink(it);
    teRender(FC_FIX_RANGE_CTX);
  } else if (FC_FIX_FULL.includes(f.pick)) {
    const s = f.pick === FC_FIX_TEACHER ? fcTeacherSubject(it) : it;   // 10-16 #225：重疊落在學員段落裡，改的是那一段
    cvBindTools(s);
    cvBindPlayShrink(s);
    rvBindBody(s);
    rvBindOutQ(s);
    rvBindMore(s);
  }
}

// 「長度或停頓怪」的改範圍：第 3 步的起訖編輯器（teRender），放在卡片裡；自己的「儲存修改」藏起來，存檔跟著「確定」
function fcFixRangeInit(it) {
  fc.fixEd = { kind: RV_ITEM_KIND[it["類型"]], id: it.id, a: it.start, b: it.end, a0: it.start, b0: it.end,
    whole: !!it["老師整段"], result: null, busy: false, label: `${(RV_TYPE[it["類型"]] || {}).label || it["類型"]} ${rvFmt(it.start)}` };
}
const FC_FIX_RANGE_CTX = {
  host: "fc-fix-range", get st() { return fc.fixEd; }, o: {
    aria: "改範圍",
    head: (ed) => `<b>改範圍：${esc(ed.label || "")}</b>`,
    hint: (ed) => rvRuleHint(ed.kind, ed.whole, true),
    now: () => FC_CARD_CTX.videoTime(),
    nowLabel: "用影片目前位置",
    play: (a, b) => fcPlaySource(a, b),
    api: "/api/review/manual",
    body: (ed) => ({ "類型": ed.kind, start: ed.a, end: ed.b, id: ed.id }),
    toResult: (r) => ({ ...r["對齊結果"], "新增": false }),
    saved: async (r, res) => { fc.fixEd.result = res; fc.fixEd.saved = true; },
    saveLabel: () => "儲存修改",
  },
};

// 存檔中的第 3 步存檔（例如改字框失去焦點時存的）：按「確定」先等它們存完
function fcTrackPost(p) {
  fc.pend.push(p);
  const done = () => { fc.pend = fc.pend.filter((x) => x !== p); };
  p.then(done, done);
  return p;
}

// 「確定」：等改字存完 → 記原因（退回＋選項＋補充）→ 套用這一項的修改（改範圍、改回老師原聲）→ 重新整理，卡片收合成「要重做」
async function fcFixConfirm(c, it) {
  const f = fc.fix;
  if (!f.pick || f.busy) return;
  const ed = f.pick === FC_FIX_RANGE && fcFixCanEdit(it) && RV_ITEM_KIND[it["類型"]] ? fc.fixEd : null;
  const moved = ed && (ed.a !== ed.a0 || ed.b !== ed.b0);
  if (moved && !teOk(ed)) { f.err = "起點、終點都填好（終點晚於起點）才能存"; fcRenderRight(); return; }
  f.busy = true; f.err = "";
  fcRenderRight();
  try {
    await Promise.allSettled(fc.pend.slice());
    if (f.pick === FC_FIX_TEXT && fcFixCanEdit(it)) await rvSaveTurnText(it);   // 改字框還沒存（沒失去焦點）就存
    if (fcFixRecords(c, fcOld())) {
      fc.nextData = null;   // 記原因之後不能再用之前留的整頁資料
      const ver = fcFixVer(c, fcOld());   // 10-15 宇軒驗證回饋六：跨版本卡、舊版的卡帶版本 → 後端記在卡片的等重做
      await apiPost("/api/final/card", { id: c.id, "結果": FC_ST.BACK, "選項": [f.pick], "補充": f.extra || "", ...(ver != null ? { "版本": ver } : {}) });
    }
    if (moved) {
      await teSave(FC_FIX_RANGE_CTX);
      if (ed.result && ed.result.error) throw new Error(ed.result.error);
    }
    // 10-15 宇軒驗證回饋二：「裡面有老師的聲音」的修改在選單裡當場存（跟「其他」一樣），「確定」只記原因
    fcFixReset();
    await fcReload();
  } catch (e) {
    if (fc.fix.open) { f.busy = false; f.err = e.message; fcRenderRight(); } else alert(e.message);
  }
}

async function fcDecide(c, result, picks = [], extra = "", cancelRedo = false) {
  fc.nextData = null;   // 10-15 第2段審查二修正：判斷之後不能再用之前留的整頁資料
  let res;
  // 10-15 第5步2.0 第3段：在哪一版上按的（舊版、跨版本卡記在那一版；整頁重新整理）
  const ver = c["跨版本"] || fcOld() ? c["版本號"] : null;
  try {
    res = await apiPost("/api/final/card", { id: c.id, "結果": result, "選項": picks, "補充": extra, ...(ver ? { "版本": ver } : {}),
      ...(cancelRedo ? { "取消要重做": true } : {}) });
  } catch (e) { alert(e.message); return; }
  fcFixReset();
  // 10-15 宇軒驗證回饋二：通過後留在這張（跟第 3 步一樣不自動跳下一張）。10-15 總檢查修正（1015-7）：等重做那條路的卡
  // （c["範圍外改了"]）取消要重做之後可能搬回跨版本卡、或回到「已通過（沿用上一版）」→ 整頁重新整理
  // 審查修正 3c：取消的是卡片等重做的原因（已改、等重做的卡）→ 卡片可能回到沿用、跨版本卡，也整頁重新整理
  if (ver || c["範圍外改了"] || (result === null && c["狀態"] === FC_ST.EDITED)) { await fcReload(); return; }
  const i = fcCards().findIndex((x) => x.id === c.id);
  if (res["卡片"] && i >= 0) fc.data["卡片"][i] = res["卡片"];
  else if (i >= 0) fc.data["卡片"].splice(i, 1);   // 整片退回的待釐清卡通過＝拿掉
  // 10-15 第2段審查修正（必修 1）：共用處理紀錄的別張卡也換成新的（連帶退回、或跟著通過）
  (res["共用卡片"] || []).forEach((m) => { const j = fcCards().findIndex((x) => x.id === m.id); if (j >= 0) fc.data["卡片"][j] = m; });
  fc.data["卡片統計"] = res["統計"] || fc.data["卡片統計"];
  if (res["狀態"]) fc.data["狀態"] = res["狀態"];
  // 10-15 宇軒驗證回饋二：通過後留在這張、按鈕變「✓ 已通過（再按取消）」（第 3 步 rvPass 也不跳）；下一張自己按或影片播過去自動換。
  // 只有這張卡不在了（整片退回的待釐清卡通過＝拿掉）才換到第一張
  if (!res["卡片"]) fc.cur = fcFirstId();   // 10-15 總檢查修正（1015-7）：上面清單第一張
  fcRenderTop(); fcRenderRight(); fcRenderTimeline(); fcRenderLower();
}

function fcSelect(id, seek = true) {
  if (fc.undo && fc.undo.card !== id) fc.undo = null;   // 10-16 #225：離開這張卡，「復原」就不見
  fc.cur = id;
  fcFixReset();
  const c = fcCard(id);
  if (seek && c && c["成品"] && c["成品"][0] != null) fcSeek(Math.max(0, c["成品"][0] - 2));
  fcRenderRight();
  fcRenderTimeline();
  fcMarkRow();
}

// 10-15 宇軒驗證回饋六：走上面清單接下面清單（fcNavList）；到頭／到尾停在原地；跨版本卡影片不跳（成品時間是舊版的，跟點清單一樣）
// 10-15 總檢查修正（1015-7）：照時間找不到時往後退到上面清單最後一張（fcStepPick 的 todoN）
function fcStep(dir) {
  const k = fcStepPick(fcNavList(), fc.cur, fc.video ? fc.video.currentTime : 0, dir, fcLists().todo.length);
  if (k && k !== fc.cur) fcSelect(k, !(fcCard(k) || {})["跨版本"]);
}

// ---------- 試聽 ----------

function fcPlayAB(c, which) {
  const plan = fcCardAbPlan(c, which, cvPad());
  if (!plan) return;
  fcStopAB();
  fc.audio.pause();
  const v = fc.video;
  fc.ab = { which, from: plan.video ? plan.video.from : null, to: plan.video ? plan.video.to : null, muted: v.muted };
  if (plan.video) {
    v.muted = plan.video.muted;
    fcSeek(plan.video.from);
    v.play().catch(() => {});
  } else v.pause();
  if (plan.audio) { fc.audio.src = plan.audio; fc.audio.play().catch(() => {}); }
  document.querySelectorAll("[data-ab]").forEach((b) => b.classList.toggle("on", b.dataset.ab === which));
}

function fcStopAB(pause = true) {
  const ab = fc.ab;
  if (!ab) return;
  fc.ab = null;
  fc.audio.pause();
  if (pause) fc.video.pause();
  fc.video.muted = ab.muted;
  document.querySelectorAll("[data-ab]").forEach((b) => b.classList.remove("on"));
}

function fcAbTick(t) {
  const ab = fc.ab;
  if (!ab || ab.from == null) return;
  if (t < ab.from - 1 || (ab.to != null && t >= ab.to + 1)) fcStopAB(false);
  else if (ab.to != null && t >= ab.to) fcStopAB();
}

// 「改」裡面的試聽（原片時間）：播原聲那一段（第 5 步的影片是成品，原片時間對不上）
function fcPlaySource(a, stop) {
  if (stop == null || !(stop > a)) return;
  fcStopAB(false);
  if (fc.video) fc.video.pause();
  fc.audio.src = `/api/audio?start=${Math.max(0, a).toFixed(2)}&end=${stop.toFixed(2)}`;
  fc.audio.play().catch(() => {});
}

// ---------- 「改時間」「＋在這裡新增修改」（第 3 步的起訖編輯器 teRender） ----------

const FC_UNSURE = "不確定";
function fcOpenEditor(it, rng, pre = {}) {
  const ed = fc.ed;
  Object.assign(ed, { open: true, result: pre.note ? { error: pre.note } : null, busy: false, extraNote: "" });
  if (it) {
    Object.assign(ed, { kind: RV_ITEM_KIND[it["類型"]], id: it.id, a: it.start, b: it.end, whole: !!it["老師整段"],
      label: `${(RV_TYPE[it["類型"]] || {}).label || it["類型"]} ${rvFmt(it.start)}` });
  } else {
    Object.assign(ed, { kind: pre.kind || null, id: null, label: null, whole: false, who: pre.who || ed.who,
      a: fc.video ? Math.round(fcSrcTime(fc.video.currentTime) * 100) / 100 : null, b: null });
  }
  if (rng) { ed.a = rng[0]; ed.b = rng[1]; }
  fcRenderIO();
  const box = document.getElementById("fc-io");
  if (box) box.scrollIntoView({ block: "nearest" });
}

const FC_ED_CTX = {
  host: "fc-io", get st() { return fc.ed; }, o: {
    aria: "新增修改",
    head: (ed) => ed.id ? `<b>改時間：${esc(ed.label || "")}</b>`
      : `<label>新增： <select data-te-kind><option value="" ${ed.kind ? "" : "selected"}>（先選要新增什麼）</option>${RV_KINDS.concat([[FC_UNSURE, "我不確定是哪一種，先記下來"]]).map(([k, l]) => `<option value="${esc(k)}" ${k === ed.kind ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`,
    bindHead: (el, ed) => {
      const kind = el.querySelector("[data-te-kind]");
      if (kind) kind.addEventListener("change", () => { ed.kind = kind.value || null; ed.result = null; fcRenderIO(); });
      const who = el.querySelector("#fc-ed-who"); if (who) who.addEventListener("change", () => { ed.who = who.value; });
      const code = el.querySelector("#fc-ed-code"); if (code) code.addEventListener("change", () => { ed.code = code.value; });
      const note = el.querySelector("#fc-ed-note"); if (note) note.addEventListener("change", () => { ed.extraNote = note.value; });
      el.querySelectorAll(".fc-ed-way").forEach((r) => r.addEventListener("change", () => { ed.way = r.value; }));
    },
    extra: (ed) => {
      if (ed.id) return "";
      const d = FC_CARD_CTX.data();
      if (ed.kind === "學員發言") {
        return `<label>是誰說的 <select id="fc-ed-who">${[...rvStudents(), "新學員", "老師"].map((n) => `<option value="${esc(n)}" ${n === ed.who ? "selected" : ""}>${esc(n === "新學員" ? "新的一位學員" : n === "老師" ? "老師（這一段用老師的 AI 聲音重念）" : rvWho(n))}</option>`).join("")}</select></label>`;
      }
      if (ed.kind === "名字") {
        return `<label>換成代號 <select id="fc-ed-code"><option value="">（選一個）</option>${(d["代號選項"] || []).map((c) => `<option value="${esc(c)}" ${c === ed.code ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></label>`;
      }
      if (ed.kind === "局部消音") return rvRadios("fc-ed-way", (d["選項"] || {})["消音"] || [], ed.way || ((d["選項"] || {})["消音"] || [])[0], "fc-ed-way");
      if (ed.kind === FC_UNSURE) return `<label>補充（可不填）<input id="fc-ed-note" value="${esc(ed.extraNote || "")}"></label>`;
      return "";
    },
    // 10-15 總檢查修正（1015-7）：給老師看的字「標成退回」→「標成要重做」
    hint: (ed) => (ed.kind === FC_UNSURE ? "先記在起點這個時間、標成要重做；之後再決定怎麼改" : rvRuleHint(ed.kind, ed.whole, !!ed.id)),
    now: () => FC_CARD_CTX.videoTime(),
    nowLabel: "用影片目前位置",
    play: (a, b) => fcPlaySource(a, b),
    get api() { return fc.ed.kind === FC_UNSURE ? "/api/final/flag" : "/api/review/manual"; },
    body: (ed) => {
      if (!ed.kind) { ed.result = { error: "先在「新增：」選要新增什麼" }; return null; }
      if (ed.kind === FC_UNSURE) {
        const t = fcOutTime(ed.a);
        if (t == null) { ed.result = { error: "這個時間在成品裡剪掉了，換一個時間" }; return null; }
        return { "成品秒": t, "原因": `不確定是哪一種${ed.extraNote ? "：" + ed.extraNote : ""}` };
      }
      const d = FC_CARD_CTX.data();
      const body = { "類型": ed.kind, start: ed.a, end: ed.b };
      if (ed.id) body.id = ed.id;
      else if (ed.kind === "學員發言") body["說話者"] = ed.who || rvStudents()[0] || "新學員";
      else if (ed.kind === "名字") body["代號"] = ed.code || "";
      else if (ed.kind === "局部消音") body["方式"] = ed.way || ((d["選項"] || {})["消音"] || [])[0];
      return body;
    },
    toResult: (r, st) => (st.kind === FC_UNSURE ? { "不對齊": true, start: st.a, end: st.b != null ? st.b : st.a }
      : { ...r["對齊結果"], "新增": !st.id }),
    saved: async (r, res) => {
      const ed = fc.ed;
      const adding = !ed.id && ed.kind !== FC_UNSURE;
      const key = adding && r["類型"] && r.id != null ? `${r["類型"]}:${r.id}` : null;
      if (key) await fcMarkEdited(null, key, true);   // 新增的卡：來源「第5步新增」、等重做
      Object.assign(ed, { open: false, id: null, a: null, b: null, result: res });
      await fcReload();
      const c = key ? fcCards().find((x) => x["第3步鍵"] === key) : null;
      if (c) fcSelect(c.id, false);
    },
    close: () => { fc.ed.open = false; fcRenderIO(); },
    saveLabel: (ed) => (ed.id ? "儲存修改" : ed.kind === FC_UNSURE ? "記下來" : "新增"),
  },
};
// teSave 用 cvPost（第 5 步情境會記「已改」）：新增時由 saved 另外記新的那張卡
function fcRenderIO() {
  const el = document.getElementById("fc-io");
  if (!el) return;
  if (!fc.ed.open) { el.innerHTML = ""; return; }
  teRender(FC_ED_CTX);
}

// ---------- 下面：分頁＋清單＋「＋在這裡新增修改」「重做這幾張」 ----------

function fcRowHtml(c) {
  const it = fcItemOf(c);
  const lab = fcCardLabel(c);
  // 10-15 宇軒驗證回饋四：「已通過（沿用上一版）」的列跟通過的一樣（done、綠色標籤）；「這一版沒做這張」照舊整列變淡
  // 10-15 宇軒驗證回饋五：「已通過（沿用上一版）」標籤留綠色，其他文字一樣刷淡（fc-faded-ok），讓注意力放在下面還沒確認的卡
  const outOk = c["這一版沒處理到"] && lab.cls === "ok";
  return `<li data-id="${esc(c.id)}" class="${c.id === fc.cur ? "cur" : ""} ${c["狀態"] === FC_ST.OK || outOk ? "done" : ""}${c["這一版沒處理到"] ? (outOk ? " fc-faded-ok" : " fc-faded") : ""}">
      <span class="tm">${c["原片"] && c["原片"][0] != null ? esc(fcFmt(c["原片"][0], 0)) : "—"}${c["跨版本"] ? `<small class="fc-vtag">第 ${esc(String(c["版本號"]))} 版</small>` : ""}</span>
      <span class="ty">${fcChip(c, it)}</span>
      <span class="tx">${c["編號"] ? `<span class="idtag">#${esc(String(c["編號"]))}</span>` : ""}${esc(fcWho(fcPreview(c, it)))}</span>
      <span class="st fc-tag fc-tag-${lab.cls}"${c["這一版沒處理到"] ? ` title="${esc(lab.small.join("；"))}"` : ""}>${esc(lab.text)}</span></li>`;
}

function fcRenderLower() {
  const lower = document.getElementById("fc-lower");
  if (!lower || !fc.data) return;
  const tabs = fcTabs();
  const want = fcTabGet();
  const tab = fcPickTab(tabs, want);
  const t = tabs.find((x) => x.id === tab);
  // 10-15 宇軒驗證回饋三：停在「第 3 步選定不修改」「需留意」再切到沒有那一頁的版本（舊版沒有）→ 說一句、先顯示全部（不改記住的分頁）
  const gone = want !== tab && [FC_TAB_KEEP, FC_TAB_ATT].includes(want)
    ? `<p class="rv-meta fc-tabgone">這一版沒有「${esc(want === FC_TAB_KEEP ? "第 3 步選定不修改" : "需留意")}」這一頁，先顯示全部。</p>` : "";
  // 10-15 宇軒驗證回饋六：分頁數字＝已通過的（下面清單那幾張）／全部（含跨版本卡）
  const badge = (x) => (x["清單外"] ? x["筆數"] : `${x.cards.filter(fcIsPassed).length}／${x.cards.length}`);
  const nav = `<nav class="rv-filters fc-tabs" aria-label="類型">${tabs.map((x) =>
    `<button class="${x.id === tab ? "on" : ""}${x["清單外"] ? " fc-tab-far" : ""}" data-tab="${esc(x.id)}">${esc(x["名稱"])} <span>${esc(String(badge(x)))}</span></button>`).join("")}</nav>`;
  const flat = [];
  let body;
  if (t.id === FC_TAB_ATT) body = fcAttentionHtml(fc.data["需留意"], fc.data["需留意類型"], flat);
  else if (t.id === FC_TAB_KEEP) body = fcKeepTabHtml(flat);
  else {
    // 10-15 宇軒驗證回饋六：兩個清單互換——上面「還要確認的」（這一版還沒看完的＋之前版本還沒看完的，照原片時間排在一起）、
    // 下面「已通過的」（這一版通過的＋已通過（沿用上一版）；沒有就不顯示）。「只看要仔細聽的」只篩上面
    const sp = fcSplitLists(t.cards);
    const shown = fcShown();
    const lookN = sp.todo.filter((c) => c["要人聽"]).length;
    const done = !sp.todo.length && sp.passed.length;
    // 10-15 總檢查修正（1015-7）：「只看要仔細聽的」開著時勾選框一律顯示（以前沒有要仔細聽的卡就整個不見、關不掉，清單看起來是空的）；
    // 篩完是空的寫一句、勾選框留著可以關
    const only = fcOnlyLook();
    const empty = sp.todo.length ? "沒有要仔細聽的卡片（取消勾選就看得到全部）" : fcCards().length || fcCrossCards().length ? "這一頁沒有卡片" : "這一版沒有任何修改";
    body = `<h3 class="fc-sub">還要確認的</h3>
      ${lookN || only ? `<label class="nowrap fc-only"><input type="checkbox" id="fc-only" ${only ? "checked" : ""}> 只看要仔細聽的（${lookN} 張）</label>` : ""}
      ${done ? `<p class="fc-tabdone rv-meta">這一頁看完了</p>` : ""}
      <ol class="rv-list" id="fc-list">${shown.map(fcRowHtml).join("") || (done ? "" : `<li class="empty">${empty}</li>`)}</ol>
      ${fcPassedHtml(sp.passed)}`;
  }
  lower.innerHTML = `${nav}${gone}${body}`;
  lower.querySelectorAll(".fc-tabs button").forEach((b) => b.addEventListener("click", () => { fcTabSet(b.dataset.tab); fcRenderLower(); }));
  const only = document.getElementById("fc-only");
  if (only) only.addEventListener("change", () => { fcSetOnlyLook(only.checked); fcRenderLower(); });
  lower.querySelectorAll("#fc-list li[data-id], .fc-passed li[data-id]").forEach((li) => li.addEventListener("click", () => fcSelect(li.dataset.id, !fcCard(li.dataset.id)["跨版本"])));
  lower.querySelectorAll(".fc-u-play").forEach((b) => b.addEventListener("click", () => { const u = flat[Number(b.dataset.i)]; fcPlaySpan(u["成品"][0], u["成品"][1]); }));
  lower.querySelectorAll(".fc-u-go").forEach((b) => b.addEventListener("click", () => {
    const u = flat[Number(b.dataset.i)];
    const kind = (fc.data["不修改類型"] || []).find((k) => k["子類"] === u["子類"]) || {};
    rvJump({ key: u["第3步"] || null, back: "step5", backKey: u["鍵"],
      note: `從第 5 步成品檢查過來：〈${fcWho(u["名稱"])}〉選定不修改。${kind["去改"] || ""}；改完要回第 4 步重新組裝` });
  }));
  lower.querySelectorAll(".fc-a-play").forEach((b) => b.addEventListener("click", () => { const u = flat[Number(b.dataset.i)]; const sg = u["成品段"][Number(b.dataset.s)]; fcPlaySpan(sg[0], sg[1]); }));
  lower.querySelectorAll(".fc-a-go").forEach((b) => b.addEventListener("click", () => rvJump(fcAttentionGo(flat[Number(b.dataset.i)]))));
  fcRenderActBar();
  fcMarkRow();
}

// 10-15 宇軒驗證回饋：「＋在這裡新增修改」「重做這幾張」搬到時間軸下面、「看過 P%」那一行之下（以前在清單底下）；10-16 重做這幾張再搬到右上角；
// 新增修改的面板（fc-io）跟著放在按鈕下面。舊版上不出現（不能新增、不能重做）
function fcRenderActBar() {
  const bar = document.getElementById("fc-actbar");
  if (!bar || !fc.data) return;
  const rc = fcRedoCount(fc.data);
  const n = rc.n;
  const busy = !!fc.data["AI執行中"];
  bar.innerHTML = fcOld() ? "" : `<div class="rv-row fc-bottom">
      <button class="ghost" id="fc-add" ${busy ? "disabled title=\"正在重做，做完才能改\"" : ""}>＋在這裡新增修改</button></div>`;
  // 10-16 宇軒：「重做這幾張」搬到右上角「輸出成品」左邊（輸出成品改一般按鈕外觀）
  const slot = document.getElementById("fc-redo-slot");
  // 10-16 宇軒：沒有要重做的卡也照樣顯示，變灰按不下去（看舊版、正在重做也是）
  const off = fcOld() ? "看舊版時不能重做，要回最新那一版" : busy ? "正在重做" : !rc.show ? "沒有要重做的卡" : "";
  if (slot) slot.innerHTML = `<button class="primary" id="fc-redoall" ${off ? `disabled title="${esc(off)}"` : ""}>${n && rc.show ? `重做這幾張（${n} 張）` : "重做這幾張"}</button>`;
  const add = document.getElementById("fc-add");
  if (add) add.addEventListener("click", () => fcOpenEditor(null));
  const redo = document.getElementById("fc-redoall");
  if (redo) redo.addEventListener("click", () => fcRedo(redo));
  if (fc.ed.open) fcRenderIO();
}

// 10-15 宇軒驗證回饋六：下面「已通過的」那一區（跨版本卡在上面清單）；沒有就是空字串
function fcPassedHtml(passed) {
  if (!passed || !passed.length) return "";
  return `<h3 class="fc-sub">已通過的</h3>
    <ol class="rv-list fc-passed">${passed.map(fcRowHtml).join("")}</ol>`;
}

// 10-15 宇軒驗證回饋六：黃底兩個清單都找、都標；捲動用那張卡所在的清單
function fcMarkRow() {
  const lower = document.getElementById("fc-lower");
  if (!lower) return;
  lower.querySelectorAll("#fc-list li.cur, .fc-passed li.cur").forEach((li) => li.classList.remove("cur"));
  const sel = fc.cur && `li[data-id="${CSS.escape(fc.cur)}"]`;
  const li = sel && lower.querySelector(`#fc-list ${sel}, .fc-passed ${sel}`);
  if (!li) return;
  li.classList.add("cur");
  const list = li.closest("ol") || li.parentElement;
  if (!list) return;
  const top = li.getBoundingClientRect().top - list.getBoundingClientRect().top + list.scrollTop;
  if (top < list.scrollTop || top + li.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = Math.max(0, top - list.clientHeight / 3);
}

// 「重做這幾張」（31 號設計 §9 問題 2 定案）：第 4 步開始執行、跳到第 4 步；跑完自動跳回第 5 步（app.js execRedoReturn）
async function fcRedo(btn) {
  btn.disabled = true;
  let started = null;
  try {
    started = (await apiPost("/api/execute/start", { start: null, end: null, methods: null }, { quiet: true }))["started_at"];
  } catch (e) {
    if (!/已經有.*在跑/.test(e.message)) { alert(`還不能開始：${e.message}`); btn.disabled = false; return; }
    // 10-15 第2段審查二修正：已經在跑的是 AI 執行（running）才記那一次的開始時間；在跑的是分析時 /api/execute 的 started_at
    // 是上一次執行的，記了到第 4 步會馬上彈回第 5 步
    try { const ex = await apiGet("/api/execute"); started = ex.running ? ex["started_at"] : null; } catch (e2) { started = null; }
  }
  fcRedoBackSet(started);
  location.hash = "#step4";
}
// 從第 5 步按「重做這幾張」開始的那一次執行（伺服器記的 started_at，秒；第 4 步跑完看這個決定要不要跳回來）。
// 10-15 第2段審查修正（建議）：以前記瀏覽器的時間、跟伺服器時間比（WSL 時鐘會差），改成記伺服器回傳的開始時間。
// 網頁重新整理（同一個分頁）還記得；拿不到開始時間就不記（照常停在第 4 步）
let fcRedoBack = null;
function fcRedoBackSet(started) {
  fcRedoBack = typeof started === "number" ? started : null;
  try { if (fcRedoBack) sessionStorage.setItem("fc-redo-back", String(fcRedoBack)); else sessionStorage.removeItem("fc-redo-back"); } catch (e) { /* 存不了也沒關係 */ }
}
function fcRedoBackGet() {
  if (fcRedoBack) return fcRedoBack;
  try { return Number(sessionStorage.getItem("fc-redo-back")) || null; } catch (e) { return null; }
}
function fcRedoBackClear() { fcRedoBack = null; try { sessionStorage.removeItem("fc-redo-back"); } catch (e) { /* 存不了也沒關係 */ } }

// ---------- 「第 3 步選定不修改」（最右邊那一頁；給宇軒或夥伴用，本名警告一定要留） ----------

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

// 10-15 宇軒驗證回饋三：以前叫 fcKeepHtml，跟 app.js 第 4 步「開始前總檢查」的 fcKeepHtml(r, i) 同名——
// 網頁的 js 共用同一個全域，finalcheck.js 最後載入、把第 4 步那一支蓋掉，第 4 步畫「一定要處理」時把那一列（物件）當成 flat
// 傳進來 → 「flat.push is not a function」、整頁「載入失敗」。改名分開（tests/test_final_cards_web.py 檢查全域不重名）
function fcKeepTabHtml(flat) {
  const uRows = Array.isArray(fc.data["不修改"]) ? fc.data["不修改"] : [];
  return `<p class="rv-meta">第 3 步看過、決定照原聲留著的地方。不用按通過、不擋輸出；想確認就按「跳過去聽」（只播那一段），要改就「回第 3 步改」，改完要重新組裝。</p>
    <p class="rv-warnline">這一頁會顯示學員本名，截圖或分享螢幕時請注意。</p>
    <div class="fc-ugroups" id="fc-ulist">${fcGroupUnchanged(uRows, fc.data["不修改類型"]).map((k) => {
      const lis = k.rows.map((u) => { flat.push(u); return fcUnchangedRow(u, flat.length - 1); }).join("");
      return `<h3 class="fc-sub">${esc(k["名稱"])}　${k.rows.length} 筆<span class="rv-meta">${esc(k["去改"] || "")}</span></h3><ul class="fc-ulist">${lis}</ul>`;
    }).join("")}</div>`;
}

// ---------- 「⚠️ 需留意」（最右邊那一頁）——第 4 步總檢查預設略過、成品裡還留著原聲的地方 ----------

const FC_ATT_MAX_PLAY = 4;   // 剩下好幾段時，最多放幾顆「跳過去聽」
function fcAttentionRow(r, i) {
  const segs = r["成品段"] || [];
  const plays = segs.slice(0, FC_ATT_MAX_PLAY).map((sg, j) => `<button class="ghost small fc-a-play" data-i="${i}" data-s="${j}">跳過去聽${segs.length > 1 ? ` ${esc(fcFmt(sg[0]))}` : ""}</button>`).join("");
  const name = r["卡片名稱"] ? `〈${esc(fcWho(r["卡片名稱"]))}〉` : "";
  const part = r["原片"] && r["剩下秒"] < r["原片"][1] - r["原片"][0] - 0.05 ? `（其餘被別筆處理蓋到）` : "";
  return `<li class="${r["名字"] ? "fc-att-name" : ""}" data-akey="${esc(r["鍵"] || "")}">
      <span class="tm">原片 ${esc(fcSpan(r["原片"]))}<br><small>成品 ${esc(fcSpan(r["成品"]))}</small></span>
      <span class="tx">${name}還留著 ${esc(String(Math.round(Number(r["剩下秒"]) * 10) / 10))} 秒原聲${part}${segs.length > 1 ? `，${segs.length} 段` : ""}</span>
      <span class="rv-row">${plays}<button class="ghost small fc-a-go" data-i="${i}">回第 3 步改</button></span></li>`;
}

// 需留意這一頁（純函式，flat 收集每一列給按鈕用）
function fcAttentionHtml(att, kinds, flat) {
  att = att || {};
  const rows = att["列"] || [];
  if (!att["有紀錄"]) return `<p class="rv-meta">這支成品是舊版工具組裝的，當時沒有記下第 4 步略過了哪幾列。重新組裝一次（第 4 步「只重新組裝」）之後這裡才會列。</p>`;
  const head = `<p class="rv-warnline">這一頁會顯示學員本名，截圖或分享螢幕時請注意。</p>
    <p class="rv-meta">目前成品裡還留著原聲的部分，不會擋輸出，但請多留意此部分；想確認就按「跳過去聽」，要改就「回第 3 步改」，改完要重新組裝。</p>
    ${att["名冊外沒找"] ? `<p class="fc-export-names"><b>⚠️ 名冊外的名字這次沒有找（人名清單沒跑成功）：成品裡可能有沒換掉的名字。</b>到第 1 步按「重新分析（做完的會跳過）」補跑，再回第 3 步 ③ 決定、重新組裝。</p>` : ""}
    ${/* 10-19 #234：字跟 finalcheck.py 的 RESCAN_UNFINISHED 一樣，改字兩處一起改 */ ""}
    ${att["補找沒做完"] ? `<p class="fc-export-names" id="fcRescanUnfinished"><b>⚠️ 這次補找沒做完：剛在 ③ 選的名字，老師講到的其他地方可能沒換掉，請聽一次。</b></p>` : ""}
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

// 10-08：「跳過去聽」只播那一段（成品時間 a～b），播到 b 自動停；中途自己跳走就不管
const FC_SPAN_WAIT_MS = 10000;   // 按了之後影片一直沒跳到那一段（載入太慢等），等超過 10 秒就放掉
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
    return now != null && span.since != null && now - span.since > FC_SPAN_WAIT_MS ? "drop" : "wait";
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

function fcTrack() {
  const v = fc.video;
  const t = v.currentTime;
  const ok = !v.paused && !v.seeking && v.playbackRate <= FC_MAX_RATE && !(fc.ab && fc.ab.which === "原本");   // 試聽原本時影片是靜音的，不算看過
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
    const r = await apiPost("/api/final/watched", { "區段": ranges, "成品影片": fc.data["成品影片"], ...(fcOld() ? { "版本": fc.data["目前版本"] } : {}) });
    fc.sentRanges = sig;
    fc.data["看過區段"] = r["看過區段"];
    fc.data["狀態"] = r["狀態"];
    fcRenderTop();
    fcRenderTimeline();
  } catch (e) { /* 下一次再送 */ }
}

function fcStartWatchTracking() {
  if (fc.timer) clearInterval(fc.timer);
  fc.timer = setInterval(() => {
    if (currentRouteId() !== "step5") { clearInterval(fc.timer); fc.timer = null; return; }
    if (fc.video && !fc.video.paused) fcSendWatched();
  }, 5000);
}

// ---------- 輸出 ----------

// 10-08 宇軒放寬輸出：沒看完也可以輸出，按下去先問一次（都達標就直接輸出不問）
async function fcExport() {
  fc.nextData = null;   // 10-15 第2段審查二修正：輸出之後的重新整理不能用之前留的整頁資料
  let r;
  const ver = fcOld() ? { "版本": fc.data["目前版本"] } : {};   // 10-15 第5步2.0 第3段（§6.7）：輸出選中的那一版
  try { r = await apiPost("/api/final/export", ver); } catch (e) { alert(e.message); return; }
  if (r["要確認"]) {
    let win = false;
    try { win = !!(await apiGet("/api/final/outputs"))["可以複製到Windows"]; } catch (e) { /* 讀不到就不提 Windows */ }
    if (!(await fcConfirmExport(r["還差"], win))) return;
    try { r = await apiPost("/api/final/export", { "確定": true, ...ver }); } catch (e) { alert(e.message); return; }
  }
  await fcReload();
  fileOutInit(document.getElementById("fc-fileout"));
}

// 確認視窗的文字（純函式，31 號設計 §6.8）：一句「還有 N 張沒看完，確定要輸出嗎？」；本名相關紅字照留；
// 其他細節（看過幾 %、退回還沒重做的每一筆、需留意、第 4 步正在重做、Windows 要再按複製）收進「詳細」。
// 擋下的（標字版、舊成品）只說要先做什麼、不給輸出。left＝還沒看完的張數
function fcExportAsk(gaps, win, left = 0) {
  const g = gaps || {};
  const block = g["擋下"] || [], redo = g["退回清單"] || [];
  // 10-15 宇軒定案：要輸出的不是最新版本 → 最上面一行提醒（後端照版本表給的句子；不擋，照常能輸出）
  const ver = g["不是最新版本"] || "";
  if (block.length) return { blocked: true, ver, title: "還不能輸出", names: [], details: [`${block.join("；")}。處理好之後再按一次「輸出成品」。`] };
  const details = [...(g["說明"] || [])];
  // 10-15 總檢查修正（1015-7）：給老師看的字「退回重做還沒重做」→「要重做還沒重做」（資料欄位 退回清單 照舊）
  if (redo.length) details.push(`有 ${redo.length} 筆要重做還沒重做，輸出的是重做之前的樣子：` + redo.map((x) => `${x["成品秒"] != null ? "成品" : "原片"} ${x["成品秒"] != null ? fcFmt(x["成品秒"]) : x["原片秒"] != null ? fcFmt(x["原片秒"]) : "—"}${x["原因"] ? " " + x["原因"] : ""}`).join("；"));
  if (g["需留意說明"]) details.push(`${g["需留意說明"]}（「⚠️ 需留意」那一頁）`);
  details.push(...(g["提醒"] || []));
  details.push("輸出的是現在這一支成品，會另外存一支新的，舊的不會被蓋掉。");
  if (win) details.push("成品在 Ubuntu 裡：輸出之後，再按「複製成品到 Windows 的下載資料夾」，才拿得到。");
  return { blocked: false, ver, title: left ? `還有 ${left} 張沒看完，確定要輸出嗎？` : "確定要輸出嗎？", names: g["名字提醒"] || [], details };
}

function fcConfirmExport(gaps, win) {
  return new Promise((resolve) => {
    // 10-15 總檢查修正（1015-7）：「還有 N 張」＝還要確認幾張（後端 卡片統計.還要確認，含跨版本卡；以前是還沒看＋要重做，漏了跨版本卡）
    const a = fcExportAsk(gaps, win, fcLeftCount(fc.data));
    const dlg = document.createElement("dialog");
    dlg.className = "rv-confirm";
    dlg.setAttribute("aria-labelledby", "fc-export-title");
    dlg.innerHTML = `${a.ver ? `<p class="fc-export-names fc-export-ver"><b>${esc(a.ver)}</b></p>` : ""}<h2 id="fc-export-title">${esc(a.title)}</h2>
      ${(a.names || []).map((x) => `<p class="fc-export-names"><b>${esc(x)}</b></p>`).join("")}
      ${a.details.length ? `<details class="rv-confirm-more"><summary>詳細</summary>${a.details.map((x) => `<p>${esc(x)}</p>`).join("")}</details>` : ""}
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

// ---------- 快捷鍵（照第 3 步：Enter 通過、E 要修改；空白鍵播放；↑↓ 上一張下一張；J／L 前後 5 秒） ----------

document.addEventListener("keydown", (e) => {
  if (currentRouteId() !== "step5" || !fc.video) return;
  if (e.isComposing || e.keyCode === 229) return;
  if (document.querySelector("dialog[open]")) return;
  if (["TEXTAREA", "INPUT", "SELECT"].includes(e.target.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
  // 10-17 畫面測試：已結案只能看紀錄——通過、要修改、播放、跳秒都不做（以前 Enter 照樣送出、409 再跳對話框）；↑↓ 換卡片只是看，照樣可以
  if (fc.data && fc.data["已結案"] && e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
  const k = e.key.toLowerCase();
  const c = fc.cur && fcCard(fc.cur);
  if (e.key === " ") { e.preventDefault(); fc.video.paused ? fc.video.play().catch(() => {}) : fc.video.pause(); }
  else if (e.key === "Enter" && e.target.tagName !== "BUTTON" && fcCanJudge(c)) {   // Enter＝通過（舊版也可以）；要修改一律走按鈕（E）
    e.preventDefault(); fcDecide(c, c["狀態"] === FC_ST.OK ? null : FC_ST.OK);
  }
  else if (k === "e") { e.preventDefault(); fcToggleFix(); }   // 10-15 要修改合併（1015-4）：E＝開「要修改」
  else if (k === "j") fcSeek(fc.video.currentTime - 5);
  else if (k === "l") fcSeek(fc.video.currentTime + 5);
  else if (e.key === "ArrowDown") { e.preventDefault(); fcStep(1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); fcStep(-1); }
});
