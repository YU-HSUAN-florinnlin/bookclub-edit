"use strict";

/* 第 5 步「成品檢查」（10-15 第5步2.0 第2段：改成用第 3 步的卡片，31 號設計 §6）。
 * 版面：頂端「5 成品檢查」＋一句狀態＋「輸出成品」
 *   → 左邊成品影片＋時間軸（時間軸上的點＝卡片，照狀態上色：還沒看／通過／退回）＋「看過 P%」；
 *     右邊「目前這張卡」：第 3 步那張卡＋狀態標籤＋「原本 ▶」「現在 ▶」＋通過／退回／改
 *   → 下面：卡片清單（第 3 步的分頁＋「還沒看完」；最右邊收「第 3 步選定不修改」「⚠️ 需留意」）
 *     ＋「＋在這裡新增修改」「重做這幾張（N 張）」（有退回或改過才出現）。
 * 卡片的內容、「改」裡面的東西是第 3 步的卡片元件（cards.js），走第 3 步原本的 API；改完那張卡「已改，等重做」。
 * 「重做這幾張」：第 4 步開始執行、跳到第 4 步；跑完自動跳回第 5 步（31 號設計 §9 問題 2 定案）。
 * 10-15 第5步2.0 第3段（版本，31 號設計 §6.5–6.7）：有兩版以上，頂端多「看哪一版」選單（fc.ver＝看哪一版，null＝最新）；
 *   舊版：卡片照那一版的判斷、沒處理到的變淡；可以按通過（記在那一版）、可以輸出那一版；不能退回（10-15 第3段審查修正）、不能改、不能新增、不能重做。
 *   最新版下面多「上一版還沒看的（第 N 版）」那一區（範圍組裝時那一版範圍外、舊版還沒看完的卡）；重做過的卡多「上一版 ▶」。
 * 資料：GET /api/final（?版本=N 看舊版；卡片、狀態、第 3 步資料）；存檔：/api/final/card（通過／退回，回寫舊檔每一筆）、/api/final/edited、
 * /api/final/watched、/api/final/export（bookclub/finalcards.py、finalcheck.py）。依賴 app.js 的 apiGet／apiPost／esc／contentEl。 */

// 09-29 宇軒：2 倍速以下播過的才算「看過」（快轉看完不算有人完整看過）
const FC_MAX_RATE = 2;
const FC_AB_CONTEXT = 2;   // 試聽前後各多 2 秒（跟後端一樣）

let fcBackKey = null;   // 從第 3 步按「回第 5 步成品檢查」回來時，回到出發的那張卡（或「第 3 步選定不修改」那一列）
const fc = { ver: null, data: null, video: null, audio: null, cur: null, seen: [], seg: null, sentRanges: "", timer: null, lastT: 0,
  ab: null, span: null, redoOpen: false, editOpen: false, picks: [], ed: { open: false } };

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
const FC_TODO = [FC_ST.TODO, FC_ST.BACK, FC_ST.EDITED, FC_ST.NOT_MADE];   // 「還沒看完」那一頁
const FC_REDO = [FC_ST.BACK, FC_ST.EDITED, FC_ST.NOT_MADE];               // 「重做這幾張」算的

// 卡片標籤：{text, cls, small: [小字]}（31 號設計 §6.3）
function fcCardLabel(c) {
  // 10-15 第5步2.0 第3段（§6.6）：這一版沒處理到（開發者用範圍組裝時範圍外的卡）——變淡、不能按通過
  if (c["這一版沒處理到"]) return { text: "這一版沒處理到", cls: "made", small: [] };
  const small = [];
  if (c["狀態"] === FC_ST.OK && c["沿用自"]) small.push(`沿用第 ${c["沿用自"]} 版`);
  if (c["要人聽"] && c["狀態"] !== FC_ST.OK) small.push("請仔細聽");
  if (c["第幾版"]) small.push(`第 ${c["第幾版"]} 版`);
  if (c["舊紀錄"]) small.push("舊紀錄");
  if (c["狀態"] === FC_ST.BACK && (c["連帶自"] || []).length) small.push("連帶");   // 10-15 第2段審查修正（必修 1）
  if (c["狀態"] === FC_ST.EDITED && c["第3步改過"]) small.push("在第 3 步改過");     // 10-15 第2段審查修正（第 6 條）
  const cls = { [FC_ST.NOT_MADE]: "made", [FC_ST.TODO]: "todo", [FC_ST.OK]: "ok", [FC_ST.BACK]: "back", [FC_ST.EDITED]: "edited" }[c["狀態"]] || "todo";
  return { text: c["狀態"] === FC_ST.OK ? "通過 ✓" : c["狀態"], cls, small };
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
// 退回那張卡底下那一行（純函式）：有原因＝「原因：…」；沒寫原因＝「退回（沒寫原因）」
function fcWhyLine(c) {
  const why = fcReasonText(c["退回原因"]);
  return why && why !== FC_NO_REASON ? `原因：${why}` : FC_NO_REASON;
}

// 10-15 第2段審查修正（必修 1）：連帶退回的卡那一行（純函式）：「跟 #N 共用，那張還是退回」
function fcSharedLine(c) {
  const from = c["連帶自"] || [];
  if (!from.length) return "";
  return `跟 ${from.map((x) => (x["編號"] ? `#${x["編號"]}` : "另一張卡")).join("、")} 共用，那張還是退回`;
}

// 「重做這幾張」那顆按鈕（純函式）：{show, n}。10-15 第2段審查修正（必修 3）：舊檔模式（新紀錄不能用）按「改」記不到
// 「已改，等重做」，所以舊檔模式 AI 沒在跑時一律顯示（不寫張數）
function fcRedoCount(d) {
  const n = (d["卡片統計"] || {})["要重做"] || 0;
  if (n) return { show: true, n };
  return { show: d["卡片來源"] === "舊檔" && !d["AI執行中"], n: 0 };
}

// 頂端那一句（純函式，31 號設計 §6.8：只顯示一句，照優先順序選）。回傳 {text, cls, go}（go＝點了切到哪一頁）
// 10-15 第2段審查修正（必修 3）：d["舊檔改過"]＝舊檔模式這次畫面按「改」改過（記不到卡片上，畫面自己記著）→ 當作要重做
function fcStatusLine(d) {
  const st0 = d["卡片統計"] || {};
  const st = d["舊檔改過"] && !st0["要重做"] ? { ...st0, "要重做": 1 } : st0;
  const names = ((d["需留意"] || {})["列"] || []).filter((r) => r["名字"]).length;
  const run = (d["上次執行"] || {})["狀態"];
  const out = d["輸出成品"];
  if (names) return { text: `⚠️ 這 ${names} 處會照原聲念出本名`, cls: "fc-st-name", go: "需留意" };
  if (d["AI執行中"]) return { text: "正在重做…", cls: "" };
  if (st["要重做"] && ["停止", "失敗", "中斷"].includes(run)) return { text: "上次重做沒做完：按『重做這幾張』繼續", cls: "fc-st-redo" };
  if (st["要重做"]) return { text: "改過的還沒重做：按下面『重做這幾張』", cls: "fc-st-redo" };
  if (st["還沒看"]) return { text: `還有 ${st["還沒看"]} 張沒看`, cls: "" };
  if (out && !d["輸出成品過期"]) return { text: `已輸出（${fcShortStamp(out["時間"])}）`, cls: "fc-st-done" };
  return { text: "全部看完了，可以輸出", cls: "fc-st-done" };
}

// 卡片要的第 3 步那張卡（沒有＝待釐清、沒登記的變動，或第 3 步資料讀不到）
function fcItemOf(c) {
  if (!c || !c["第3步鍵"] || !c["有第3步卡片"]) return null;
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

// ---------- 分頁（純函式）：沿用第 3 步的分頁名稱，第 5 步加「還沒看完」 ----------

const FC_TAB_ALL = "全部", FC_TAB_TODO = "還沒看完", FC_TAB_KEEP = "不修改", FC_TAB_ATT = "需留意", FC_TAB_OTHER = "其他";
const FC_TYPE_TABS = [["學員段落", "學員段落"], ["名字", "名字"], ["學員名字", "學員提到名字"], ["重疊", "重疊"], ["刪除段落", "剪掉"], ["局部消音", "消音"], ["改成老師", "改成老師"]];
function fcTabList(cards, keepN, att) {
  const tabs = [{ id: FC_TAB_ALL, "名稱": "全部", cards }, { id: FC_TAB_TODO, "名稱": "還沒看完", cards: cards.filter((c) => FC_TODO.includes(c["狀態"]) && !c["這一版沒處理到"]) }];
  for (const [t, name] of FC_TYPE_TABS) {
    const list = cards.filter((c) => c["類型"] === t);
    if (list.length) tabs.push({ id: t, "名稱": name, cards: list });
  }
  const other = cards.filter((c) => !FC_TYPE_TABS.some(([t]) => t === c["類型"]));
  if (other.length) tabs.push({ id: FC_TAB_OTHER, "名稱": "其他", cards: other });
  // 31 號設計 §6.1：「第 3 步選定不修改」「需留意」收到最右邊（給宇軒或夥伴用）
  if (keepN) tabs.push({ id: FC_TAB_KEEP, "名稱": "第 3 步選定不修改", cards: [], "筆數": keepN, "清單外": true });
  if (att) tabs.push({ id: FC_TAB_ATT, "名稱": "⚠️ 需留意", cards: [], "筆數": (att["列"] || []).length, "清單外": true });
  return tabs;
}
function fcPickTab(tabs, want) { return tabs.some((t) => t.id === want) ? want : FC_TAB_ALL; }
const FC_TAB_KEY = "fc-tab";
function fcTabGet() { try { return localStorage.getItem(FC_TAB_KEY) || FC_TAB_ALL; } catch (e) { return FC_TAB_ALL; } }
function fcTabSet(id) { try { localStorage.setItem(FC_TAB_KEY, id); } catch (e) { /* 存不了也沒關係 */ } }
const FC_ONLY_KEY = "fc-only-look";
function fcOnlyLook() { try { return localStorage.getItem(FC_ONLY_KEY) === "1"; } catch (e) { return false; } }
function fcSetOnlyLook(on) { try { localStorage.setItem(FC_ONLY_KEY, on ? "1" : "0"); } catch (e) { /* 存不了也沒關係 */ } }

function fcCards() { return (fc.data && fc.data["卡片"]) || []; }
function fcCross() { return (fc.data && fc.data["跨版本卡片"]) || []; }   // 上一版還沒看的（第 N 版）
function fcCard(id) { return fcCards().find((c) => c.id === id) || fcCross().find((c) => c.id === id); }
function fcTabs() { return fcTabList(fcCards(), (fc.data["不修改"] || []).length, fc.data["需留意"]); }
function fcTab() { return fcPickTab(fcTabs(), fcTabGet()); }
// 目前這一頁列出來的卡片（開了「只看要仔細聽的」再篩一次）。上一張／下一張、通過後找下一張只在這裡面走
function fcShown() {
  const t = fcTabs().find((x) => x.id === fcTab());
  const list = t && !t["清單外"] ? t.cards : fcCards();
  return fcOnlyLook() ? list.filter((c) => c["要人聽"]) : list;
}

// （純函式）上一張／下一張：目前這張在清單裡就照順序；不在（影片播到別頁的那張）就照成品時間找
function fcStepPick(list, curId, t, dir) {
  if (!list.length) return null;
  const i = list.findIndex((x) => x.id === curId);
  if (i >= 0) return list[Math.max(0, Math.min(list.length - 1, i + dir))].id;
  const at = (x) => (x["成品"] && x["成品"][0] != null ? x["成品"][0] : null);
  if (dir > 0) {
    const n = list.find((x) => at(x) != null && at(x) > t + 0.05);
    return (n || list[list.length - 1]).id;
  }
  const p = list.filter((x) => at(x) != null && at(x) < t - 0.05).pop();
  return (p || list[0]).id;
}

// （純函式）目前這張之後第一張還沒看的；後面都看了才繞回前面找；都看了回傳 null
function fcNextUnseen(cards, curId) {
  const ok = (x) => x["狀態"] === FC_ST.TODO && x.id !== curId;
  const i = cards.findIndex((x) => x.id === curId);
  const hit = cards.slice(i + 1).find(ok) || cards.slice(0, Math.max(0, i)).find(ok);
  return hit ? hit.id : null;
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
// 10-15 第5步2.0 第3段：「上一版」＝上一個有這張卡的版本的成品那一段聲音（/api/final/vclip）；上一版還沒看的卡（跨版本）的
// 「現在」也是播那一版的成品聲音（畫面上的影片是這一版的，時間對不上），「原本」只放原聲
function fcVclip(ver, p) {
  const a = Math.max(0, p[0] - FC_AB_CONTEXT), b = (p[1] != null ? p[1] : p[0]) + FC_AB_CONTEXT;
  return `/api/final/vclip?版本=${ver}&start=${a.toFixed(2)}&end=${b.toFixed(2)}`;
}
function fcCardAbPlan(c, which) {
  const p = c["成品"], o = c["原片"];
  if (which === "上一版") {
    const u = c["上一版"];
    return u && u["成品"] && u["成品"][0] != null ? { video: null, audio: fcVclip(u["版本號"], u["成品"]) } : null;
  }
  if (c["跨版本"] && which === "現在") return p && p[0] != null ? { video: null, audio: fcVclip(c["版本號"], p) } : null;
  const has = p && p[0] != null && !c["跨版本"];
  const from = has ? Math.max(0, p[0] - FC_AB_CONTEXT) : null;
  if (which === "現在") {
    if (!has) return null;
    const end = p[1] != null ? p[1] : p[0];
    return { video: { from, to: end + FC_AB_CONTEXT, muted: false }, audio: null };
  }
  if (!o || o[0] == null) return null;
  const a = Math.max(0, o[0] - FC_AB_CONTEXT), b = (o[1] != null ? o[1] : o[0]) + FC_AB_CONTEXT;
  return { video: has ? { from, to: from + (b - a), muted: true } : null, audio: `/api/audio?start=${a.toFixed(2)}&end=${b.toFixed(2)}` };
}

// ---------------------------------------------------------------------------
// 卡片元件的情境（第 5 步；說明見 cards.js 開頭）
// ---------------------------------------------------------------------------

const FC_CARD_CTX = {
  mode: "第5步",
  data: () => (fc.data && fc.data["第3步"]) || {},
  reload: () => fcReload(),
  post: (url, body, opts) => fcStep3Post(url, body, opts),
  play: (a, stop) => fcPlaySource(a, stop),
  videoTime: () => (fc.video ? Math.round(fcSrcTime(fc.video.currentTime) * 100) / 100 : null),
  seek: (t) => { const o = fcOutTime(t); if (o != null) fcSeek(Math.max(0, o - 2)); },
  select: (key) => { const c = fcCards().find((x) => x["第3步鍵"] === key); if (c) fcSelect(c.id); },
  openEditor: (it, rng) => fcOpenEditor(it, rng),
  prefill: (kind, a, b, who, note) => fcOpenEditor(null, [a, b], { kind, who, note }),
  askOutside: null,
  refreshSug: () => {},
  moreOpen: () => fc.editOpen,
  toggleMore: () => fcToggleEdit(),
  rerender: () => fcRenderRight(),
};

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
  const adding = fc.ed.open && !fc.ed.id && url === "/api/review/manual";
  const c = fcCard(fc.cur);
  if (!adding && c && c["第3步鍵"] && !String(url).startsWith("/api/final/") && !fcNoteOnly(body)) await fcMarkEdited(c);
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
  fc.data["卡片統計"] = { ...(fc.data["卡片統計"] || {}), "全部": cards.length,
    "還沒看": cards.filter((c) => c["狀態"] === FC_ST.TODO).length, "要重做": cards.filter((c) => FC_REDO.includes(c["狀態"])).length };
}

// ---------------------------------------------------------------------------
// 進入點
// ---------------------------------------------------------------------------

// ---------- 版本（10-15 第5步2.0 第3段，31 號設計 §6.5） ----------

function fcUrl() { return fc.ver ? `/api/final?版本=${encodeURIComponent(fc.ver)}` : "/api/final"; }
function fcOld() { return !!(fc.data && fc.data["看的是舊版"]); }
// 10-15 第3段審查修正（必修 1，宇軒定前暫定）：舊版、上一版還沒看的那一區只能通過——退回在那裡第 4 步看不到、會無聲失效
// （31 號設計 §5.1 按卡片重做做完再開放）。純函式：卡片、是不是在看舊版
function fcCanJudge(c) {
  return !!c && ![FC_ST.NOT_MADE, FC_ST.EDITED].includes(c["狀態"]) && !c["這一版沒處理到"];
}
function fcCanReturn(c, old) {
  return fcCanJudge(c) && !c["跨版本"] && !old;
}
// 選單每一項的字（純函式，§6.8）：「第 N 版　10/14 14:20（最新）」；開發者設定開著才多寫範圍標記
function fcVersionText(v, dev) {
  return `第 ${v["版本號"]} 版　${fcShortStamp(v["時間"])}${v["最新"] ? "（最新）" : ""}${dev && v["標記"] ? `　${v["標記"]}` : ""}`;
}
// 「看哪一版」選單（純函式）：兩版以上才出現；開發者在舊版上多一顆「以這一版為基礎再組裝」
function fcVersionMenuHtml(d) {
  const list = d["版本清單"] || [];
  if (list.length < 2) return "";
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

async function renderFinal() {
  cvUse(FC_CARD_CTX);
  contentEl.innerHTML = "<p>載入中…</p>";
  try { fc.data = await apiGet(fcUrl()); }
  catch (e) { if (!fc.ver) throw e; fc.ver = null; fc.data = await apiGet(fcUrl()); }   // 那一版不在了：回最新
  contentEl.classList.add("wide");
  const d = fc.data;
  if (!d["看的是舊版"] && (!d["有處理紀錄"] || !d["成品影片"])) {   // 31 號設計 §6.8：一句話＋連結（霧化預覽拿掉）
    contentEl.innerHTML = `<div class="rv2 fc"><h1>5　成品檢查</h1>
      <p class="fc-none">還沒有成品，請先到 <a href="#step4">第 4 步</a></p>${fcWhereHtml(d["專案標示"])}</div>`;
    return;
  }
  contentEl.innerHTML = `
    <div class="rv2 fc">
      <header class="rv-bar">
        <h1>5　成品檢查</h1>
        <div class="fc-vers">${fcVersionMenuHtml(d)}</div>
        <div class="fc-status" id="fc-status" role="status"></div>
        <div class="rv-bar-btns"><button class="primary" id="fc-export" ${d["影片清掉了"] ? "disabled" : ""}>輸出成品</button></div>
      </header>
      <div class="fo-box" id="fc-fileout"></div>
      <section class="rv-upper">
        <div class="rv-left">
          ${d["影片清掉了"] ? `<p class="rv-warnline">這一版的影片清掉了</p>` : ""}
          <video id="fc-video" controls preload="metadata" ${d["影片網址"] ? `src="${esc(d["影片網址"])}${String(d["影片網址"]).includes("?") ? "&" : "?"}v=${encodeURIComponent(d["成品影片"] + "|" + (d["成品版本"] || ""))}"` : ""}></video>
          <div class="rv-timebar"><span class="rv-clock"><b id="fc-clock">0:00</b> ／ ${esc(fcFmt(d["成品長度"], 0))}</span>
            <select id="fc-rate" aria-label="播放速度"><option value="1">1 倍速</option><option value="1.25">1.25 倍速</option><option value="1.5">1.5 倍速</option><option value="2">2 倍速</option></select></div>
          <div class="rv-tl fc-tl" id="fc-tl" title="點一下或拖拉，跳到那個時間"></div>
          <div class="rv-legend"><span><i class="sw fc-todo"></i>還沒看</span><span><i class="sw fc-ok"></i>通過</span><span><i class="sw fc-back"></i>退回</span></div>
          <p class="rv-meta fc-seenpct">看過 <b id="fc-pct">0</b>%</p>
        </div>
        <div class="rv-right" id="fc-right"></div>
      </section>
      <section class="rv-lower" id="fc-lower"></section>
      <audio id="fc-audio" preload="none"></audio>
    </div>`;
  fc.video = document.getElementById("fc-video");
  fc.audio = document.getElementById("fc-audio");
  Object.assign(fc, { seen: [], seg: null, sentRanges: "", ab: null, redoOpen: false, editOpen: false, ed: { open: false },
    legacyEdited: false, nextData: null });
  if (!fc.cur || !fcCard(fc.cur)) fc.cur = (fcCards().find((c) => c["狀態"] === FC_ST.TODO) || fcCards()[0] || {}).id || null;
  document.getElementById("fc-export").addEventListener("click", fcExport);
  fcBindVersionMenu();
  fileOutInit(document.getElementById("fc-fileout"));   // 10-07：打開成品資料夾、複製到 Windows
  document.getElementById("fc-rate").addEventListener("change", (e) => { fc.video.playbackRate = Number(e.target.value); });
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
  fc.data = pre && !fc.ver && Date.now() - pre.at < 5000 ? pre.data : await apiGet(fcUrl());
  if (fc.cur && !fcCard(fc.cur)) fc.cur = (fcCards().find((c) => c["狀態"] === FC_ST.TODO) || fcCards()[0] || {}).id || null;
  fcRenderAll();
}

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
  // 10-15 第5步2.0 第3段：看舊版時不提「重做」（舊版上不能重做；退回的算沒看完）
  const st0 = fc.data["卡片統計"] || {};
  const view = fcOld() ? { ...fc.data, "AI執行中": false, "卡片統計": { ...st0, "要重做": 0, "還沒看": (st0["還沒看"] || 0) + (st0["退回"] || 0) } } : fc.data;
  const s = fcStatusLine({ ...view, "舊檔改過": !!fc.legacyEdited && fc.data["卡片來源"] === "舊檔" });
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

function fcFollow(t) {   // 影片照常播、跨過某張卡的起點，右邊換到那張（改、退回開著、試聽中不換）
  const prev = fc.lastT;
  fc.lastT = t;
  if (fc.redoOpen || fc.editOpen || fc.ab || t < prev || t - prev > 2) return;
  const hit = fcCards().filter((c) => c["成品"] && c["成品"][0] != null && c["成品"][0] > prev && c["成品"][0] <= t).pop();
  if (hit && hit.id !== fc.cur) { fc.cur = hit.id; fcRenderRight(); fcRenderTimeline(); fcMarkRow(); }
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

function fcStateBoxHtml(c) {
  const lab = fcCardLabel(c);
  const back = c["狀態"] === FC_ST.BACK;
  const shared = back ? fcSharedLine(c) : "";
  return `<span class="fc-tag fc-tag-${lab.cls}">${esc(lab.text)}</span>${lab.small.map((x) => ` <span class="rv-meta">${esc(x)}</span>`).join("")}
    ${back ? `<p class="rv-warnline fc-why">${esc(fcWhyLine(c))}</p>` : ""}${shared ? `<p class="rv-warnline fc-shared">${esc(shared)}</p>` : ""}`;
}

function fcRenderStateBox() {
  const box = document.getElementById("fc-statebox");
  const c = fc.cur && fcCard(fc.cur);
  if (box && c) box.innerHTML = fcStateBoxHtml(c);
}

function fcRenderRight() {
  const box = document.getElementById("fc-right");
  if (!box) return;
  const c = fc.cur && fcCard(fc.cur);
  if (!c) { box.innerHTML = `<div class="rv-card empty"><p>這一版沒有任何修改</p></div>`; return; }
  const it = fcItemOf(c);
  const list = c["跨版本"] ? fcCross() : fcShown().length ? fcShown() : fcCards();
  const idx = list.findIndex((x) => x.id === c.id);
  const busy = !!fc.data["AI執行中"];
  const canJudge = fcCanJudge(c);
  const canReturn = fcCanReturn(c, fcOld());   // 10-15 第3段審查修正：舊版、跨版本卡退回按鈕灰的
  const canEdit = it && !fcOld();   // 10-15 第5步2.0 第3段：舊版上不能改（改的是第 3 步現在的檔，回最新一版改）
  const abPrev = fcCardAbPlan(c, "上一版");
  const back = c["狀態"] === FC_ST.BACK;
  const opts = fc.data["退回原因選項"] || [];
  const picked = back ? ((c["退回原因"] || {})["選項"] || []) : fc.picks;
  const abNow = fcCardAbPlan(c, "現在"), abOrig = fcCardAbPlan(c, "原本");
  box.innerHTML = `<article class="rv-card fc-card" data-id="${esc(c.id)}">
      <header>${fcChip(c, it)}${c["編號"] ? `<span class="idtag" title="跟 AI 助手溝通用的編號">#${esc(String(c["編號"]))}</span>` : ""}
        <span class="rv-count">第 ${idx + 1}／${list.length} 張</span></header>
      <div class="rv-statebox" id="fc-statebox">${fcStateBoxHtml(c)}</div>
      ${fc.editOpen ? "" : `<p class="fc-preview">${esc(fcWho(fcPreview(c, it)))}</p>`}
      <div class="rv-row fc-ab">
        <button class="ghost small" data-ab="原本" ${abOrig ? "" : "disabled"}>原本 ▶</button>
        <button class="ghost small" data-ab="現在" ${abNow ? "" : "disabled"}>現在 ▶</button>${abPrev ? `
        <button class="ghost small" data-ab="上一版">上一版 ▶</button>` : ""}</div>
      <div class="rv-actions">
        <button class="${c["狀態"] === FC_ST.OK ? "primary on" : "primary"}" id="fc-pass" aria-pressed="${c["狀態"] === FC_ST.OK}" ${canJudge ? "" : "disabled"}>通過<kbd>Enter</kbd></button>
        <button class="ghost${back ? " on" : ""}" id="fc-redo" aria-pressed="${back}" aria-expanded="${fc.redoOpen}" ${canReturn ? "" : `disabled title="要退回請回到最新一版"`}>退回</button>${back && !canReturn ? `
        <button class="ghost small" id="fc-back-undo">取消退回</button>` : ""}
        ${canEdit ? `<button class="ghost${fc.editOpen ? " on" : ""}" id="fc-edit" aria-expanded="${fc.editOpen}" ${busy ? "disabled title=\"正在重做，做完才能改\"" : ""}>改<kbd>E</kbd></button>` : ""}
        <span class="spacer"></span>
        <button class="ghost" id="fc-prev">上一張</button><button class="ghost" id="fc-next">下一張</button>
      </div>
      <div class="rv-more" id="fc-redobox" ${fc.redoOpen && canReturn ? "" : "hidden"}>
        ${c["可重新生成"] ? "" : `<p class="rv-warnline">這一張重做只會重新放一次，聲音不會變；要改內容請按『改』</p>`}
        <p><b>為什麼退回？</b></p>
        <div class="rv-field rv-choices">${opts.map((o) => `<label class="rv-check"><input type="checkbox" class="fc-why-opt" value="${esc(o)}" ${picked.includes(o) ? "checked" : ""}> ${esc(o)}</label>`).join("")}</div>
        <label class="rv-field">補充（可不填）<input id="fc-why-extra" value="${esc(back ? (c["退回原因"] || {})["補充"] || "" : "")}"></label>
        <div class="rv-row"><button class="primary small" id="fc-redo-go">確定退回</button><button class="ghost small" id="fc-redo-cancel">取消</button>
          ${back ? `<button class="ghost small" id="fc-redo-undo">取消退回</button>` : ""}</div>
      </div>
      ${canEdit && fc.editOpen ? `<div class="fc-editbox" id="fc-editbox">
        <div class="rv-body">${rvEdgeHtml(it)}${rvBodyHtml(it)}${rvAlignLine(it)}</div>
        <div class="rv-actions">${cvToolBtnsHtml(it, true)}</div>
        <div class="rv-more" id="rv-more">${rvMoreHtml(it)}</div>
      </div>` : ""}
    </article>`;
  box.querySelectorAll("[data-ab]").forEach((b) => b.addEventListener("click", () => fcPlayAB(c, b.dataset.ab)));
  document.getElementById("fc-pass").addEventListener("click", () => fcDecide(c, c["狀態"] === FC_ST.OK ? null : FC_ST.OK));
  document.getElementById("fc-redo").addEventListener("click", () => fcToggleRedo());
  const edit = document.getElementById("fc-edit");
  if (edit) edit.addEventListener("click", () => fcToggleEdit());
  document.getElementById("fc-redo-cancel").addEventListener("click", () => { fc.redoOpen = false; fcRenderRight(); });
  document.getElementById("fc-redo-go").addEventListener("click", () => {
    const sel = [...document.querySelectorAll(".fc-why-opt:checked")].map((x) => x.value);
    fcDecide(c, FC_ST.BACK, sel, document.getElementById("fc-why-extra").value);
  });
  const undo = document.getElementById("fc-redo-undo");
  if (undo) undo.addEventListener("click", () => fcDecide(c, null));
  const undo2 = document.getElementById("fc-back-undo");   // 10-15 第3段審查修正：舊版上以前退回過的，退回框打不開，取消放外面
  if (undo2) undo2.addEventListener("click", () => fcDecide(c, null));
  document.getElementById("fc-prev").addEventListener("click", () => fcStep(-1));
  document.getElementById("fc-next").addEventListener("click", () => fcStep(1));
  if (canEdit && fc.editOpen) {   // 「改」裡面是第 3 步的卡片元件（第 3 步原本的 API）
    cvBindTools(it);
    cvBindPlayShrink(it);
    rvBindBody(it);
    rvBindOutQ(it);
    rvBindMore(it);
  }
}

function fcToggleRedo() {
  const c = fc.cur && fcCard(fc.cur);
  if (!fc.redoOpen && !fcCanReturn(c, fcOld())) return;   // 10-15 第3段審查修正：舊版、跨版本卡不開退回
  fc.redoOpen = !fc.redoOpen;
  if (fc.redoOpen) fc.editOpen = false;
  fcRenderRight();
}

function fcToggleEdit() {
  const c = fc.cur && fcCard(fc.cur);
  if (!c || !fcItemOf(c) || fc.data["AI執行中"] || fcOld()) return;
  fc.editOpen = !fc.editOpen;
  if (fc.editOpen) fc.redoOpen = false;
  fcRenderRight();
  const box = document.getElementById("fc-editbox");
  if (box) box.scrollIntoView({ block: "nearest" });
}

async function fcDecide(c, result, picks = [], extra = "") {
  if (result === FC_ST.BACK && !fcCanReturn(c, fcOld())) return;   // 10-15 第3段審查修正：任何路徑（按鈕、Enter）都不送舊版退回
  fc.nextData = null;   // 10-15 第2段審查二修正：判斷之後不能再用之前留的整頁資料
  let res;
  // 10-15 第5步2.0 第3段：在哪一版上按的（舊版、上一版還沒看的那一區記在那一版；整頁重新整理）
  const ver = c["跨版本"] || fcOld() ? c["版本號"] : null;
  try {
    res = await apiPost("/api/final/card", { id: c.id, "結果": result, "選項": picks, "補充": extra, ...(ver ? { "版本": ver } : {}) });
  } catch (e) { alert(e.message); return; }
  fc.redoOpen = false;
  fc.picks = [];
  if (ver) {
    const list = c["跨版本"] ? fcCross() : fcCards();
    const next = result === FC_ST.OK ? fcNextUnseen(list, c.id) : null;
    await fcReload();
    if (next && fcCard(next)) fcSelect(next, false);
    return;
  }
  const i = fcCards().findIndex((x) => x.id === c.id);
  if (res["卡片"] && i >= 0) fc.data["卡片"][i] = res["卡片"];
  else if (i >= 0) fc.data["卡片"].splice(i, 1);   // 整片退回的待釐清卡通過＝拿掉
  // 10-15 第2段審查修正（必修 1）：共用處理紀錄的別張卡也換成新的（連帶退回、或跟著通過）
  (res["共用卡片"] || []).forEach((m) => { const j = fcCards().findIndex((x) => x.id === m.id); if (j >= 0) fc.data["卡片"][j] = m; });
  fc.data["卡片統計"] = res["統計"] || fc.data["卡片統計"];
  if (res["狀態"]) fc.data["狀態"] = res["狀態"];
  const skipped = (res["跳過"] || []).length > 0;   // 通過了，可是共用的那一段別張卡還退回著：留在這張，讓老師看到那一行
  if ((result === FC_ST.OK && !skipped) || !res["卡片"]) {   // 通過了就換下一張還沒看的（影片不跳，照常播）
    const next = fcNextUnseen(fcShown(), c.id) || fcNextUnseen(fcCards(), c.id);
    if (next) fc.cur = next;
    else if (!res["卡片"]) fc.cur = (fcCards()[0] || {}).id || null;
  }
  fcRenderTop(); fcRenderRight(); fcRenderTimeline(); fcRenderLower();
}

function fcSelect(id, seek = true) {
  fc.cur = id;
  fc.redoOpen = false;
  fc.editOpen = false;
  const c = fcCard(id);
  if (seek && c && c["成品"] && c["成品"][0] != null) fcSeek(Math.max(0, c["成品"][0] - 2));
  fcRenderRight();
  fcRenderTimeline();
  fcMarkRow();
}

function fcStep(dir) {
  const k = fcStepPick(fcShown().length ? fcShown() : fcCards(), fc.cur, fc.video ? fc.video.currentTime : 0, dir);
  if (k) fcSelect(k);
}

// ---------- 試聽 ----------

function fcPlayAB(c, which) {
  const plan = fcCardAbPlan(c, which);
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
    hint: (ed) => (ed.kind === FC_UNSURE ? "先記在起點這個時間、標成退回；之後再決定怎麼改" : rvRuleHint(ed.kind, ed.whole, !!ed.id)),
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
  return `<li data-id="${esc(c.id)}" class="${c.id === fc.cur ? "cur" : ""} ${c["狀態"] === FC_ST.OK ? "done" : ""}${c["這一版沒處理到"] ? " fc-faded" : ""}">
      <span class="tm">${c["原片"] && c["原片"][0] != null ? esc(fcFmt(c["原片"][0], 0)) : "—"}</span>
      <span class="ty">${fcChip(c, it)}</span>
      <span class="tx">${c["編號"] ? `<span class="idtag">#${esc(String(c["編號"]))}</span>` : ""}${esc(fcWho(fcPreview(c, it)))}</span>
      <span class="st fc-tag fc-tag-${lab.cls}">${esc(lab.text)}</span></li>`;
}

function fcRenderLower() {
  const lower = document.getElementById("fc-lower");
  if (!lower || !fc.data) return;
  const tabs = fcTabs();
  const tab = fcPickTab(tabs, fcTabGet());
  const t = tabs.find((x) => x.id === tab);
  const badge = (x) => (x["清單外"] ? x["筆數"] : x.id === FC_TAB_TODO ? x.cards.length : `${x.cards.filter((c) => c["狀態"] === FC_ST.OK).length}／${x.cards.length}`);
  const nav = `<nav class="rv-filters fc-tabs" aria-label="類型">${tabs.map((x) =>
    `<button class="${x.id === tab ? "on" : ""}${x["清單外"] ? " fc-tab-far" : ""}" data-tab="${esc(x.id)}">${esc(x["名稱"])} <span>${esc(String(badge(x)))}</span></button>`).join("")}</nav>`;
  const flat = [];
  let body;
  if (t.id === FC_TAB_ATT) body = fcAttentionHtml(fc.data["需留意"], fc.data["需留意類型"], flat);
  else if (t.id === FC_TAB_KEEP) body = fcKeepHtml(flat);
  else {
    const shown = fcShown();
    const lookN = t.cards.filter((c) => c["要人聽"]).length;
    const done = tab !== FC_TAB_ALL && tab !== FC_TAB_TODO && t.cards.length && !t.cards.some((c) => FC_TODO.includes(c["狀態"]));
    body = `${lookN ? `<label class="nowrap fc-only"><input type="checkbox" id="fc-only" ${fcOnlyLook() ? "checked" : ""}> 只看要仔細聽的（${lookN} 張）</label>` : ""}
      ${done ? `<p class="fc-tabdone rv-meta">這一頁看完了</p>` : ""}
      <ol class="rv-list" id="fc-list">${shown.map(fcRowHtml).join("") || `<li class="empty">${fcCards().length ? "這一頁沒有卡片" : "這一版沒有任何修改"}</li>`}</ol>
      ${fcCrossHtml(fcCross())}`;
  }
  const rc = fcRedoCount(fc.data);
  const n = rc.n;
  const busy = !!fc.data["AI執行中"];
  const bar = fcOld() ? "" : `<div class="rv-row fc-bottom">
      <button class="ghost" id="fc-add" ${busy ? "disabled title=\"正在重做，做完才能改\"" : ""}>＋在這裡新增修改</button>
      ${rc.show ? `<button class="primary" id="fc-redoall" ${busy ? "disabled" : ""}>${n ? `重做這幾張（${n} 張）` : "重做這幾張"}</button>` : ""}</div>
    <div class="rv-io" id="fc-io"></div>`;
  lower.innerHTML = `${nav}${body}${bar}`;
  lower.querySelectorAll(".fc-tabs button").forEach((b) => b.addEventListener("click", () => { fcTabSet(b.dataset.tab); fcRenderLower(); }));
  const only = document.getElementById("fc-only");
  if (only) only.addEventListener("change", () => { fcSetOnlyLook(only.checked); fcRenderLower(); });
  lower.querySelectorAll("#fc-list li[data-id], .fc-cross li[data-id]").forEach((li) => li.addEventListener("click", () => fcSelect(li.dataset.id, !fcCard(li.dataset.id)["跨版本"])));
  lower.querySelectorAll(".fc-u-play").forEach((b) => b.addEventListener("click", () => { const u = flat[Number(b.dataset.i)]; fcPlaySpan(u["成品"][0], u["成品"][1]); }));
  lower.querySelectorAll(".fc-u-go").forEach((b) => b.addEventListener("click", () => {
    const u = flat[Number(b.dataset.i)];
    const kind = (fc.data["不修改類型"] || []).find((k) => k["子類"] === u["子類"]) || {};
    rvJump({ key: u["第3步"] || null, back: "step5", backKey: u["鍵"],
      note: `從第 5 步成品檢查過來：〈${fcWho(u["名稱"])}〉選定不修改。${kind["去改"] || ""}；改完要回第 4 步重新組裝` });
  }));
  lower.querySelectorAll(".fc-a-play").forEach((b) => b.addEventListener("click", () => { const u = flat[Number(b.dataset.i)]; const sg = u["成品段"][Number(b.dataset.s)]; fcPlaySpan(sg[0], sg[1]); }));
  lower.querySelectorAll(".fc-a-go").forEach((b) => b.addEventListener("click", () => rvJump(fcAttentionGo(flat[Number(b.dataset.i)]))));
  const add = document.getElementById("fc-add");
  if (add) add.addEventListener("click", () => fcOpenEditor(null));
  const redo = document.getElementById("fc-redoall");
  if (redo) redo.addEventListener("click", () => fcRedo(redo));
  if (fc.ed.open) fcRenderIO();
  fcMarkRow();
}

// 「上一版還沒看的（第 N 版）」那一區（純函式，31 號設計 §6.5、#221）：照版本分組；沒有就是空字串
function fcCrossHtml(cross) {
  if (!cross || !cross.length) return "";
  const vers = [...new Set(cross.map((c) => c["版本號"]))];
  return vers.map((n) => `<h3 class="fc-sub">上一版還沒看的（第 ${esc(String(n))} 版）</h3>
    <ol class="rv-list fc-cross">${cross.filter((c) => c["版本號"] === n).map(fcRowHtml).join("")}</ol>`).join("");
}

function fcMarkRow() {
  const list = document.getElementById("fc-list");
  if (!list) return;
  list.querySelectorAll("li.cur").forEach((li) => li.classList.remove("cur"));
  const li = fc.cur && list.querySelector(`li[data-id="${CSS.escape(fc.cur)}"]`);
  if (!li) return;
  li.classList.add("cur");
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

function fcKeepHtml(flat) {
  const uRows = fc.data["不修改"] || [];
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
    <p class="rv-meta">第 4 步開始執行時「開始前總檢查」沒處理（預設略過）的地方，扣掉被學員重念、名字換掉、剪掉、消音蓋到的部分，只列成品裡還留著原聲的。不用按通過、不擋輸出；想確認就按「跳過去聽」，要改就「回第 3 步改」，改完要重新組裝。</p>
    ${att["名冊外沒找"] ? `<p class="fc-export-names"><b>⚠️ 名冊外的名字這次沒有找（人名清單沒跑成功）：成品裡可能有沒換掉的名字。</b>到第 1 步按「重新分析（做完的會跳過）」補跑，再回第 3 步 ③ 決定、重新組裝。</p>` : ""}
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
  if (block.length) return { blocked: true, title: "還不能輸出", names: [], details: [`${block.join("；")}。處理好之後再按一次「輸出成品」。`] };
  const details = [...(g["說明"] || [])];
  if (redo.length) details.push(`有 ${redo.length} 筆退回重做還沒重做，輸出的是重做之前的樣子：` + redo.map((x) => `${x["成品秒"] != null ? "成品" : "原片"} ${x["成品秒"] != null ? fcFmt(x["成品秒"]) : x["原片秒"] != null ? fcFmt(x["原片秒"]) : "—"}${x["原因"] ? " " + x["原因"] : ""}`).join("；"));
  if (g["需留意說明"]) details.push(`${g["需留意說明"]}（「⚠️ 需留意」那一頁）`);
  details.push(...(g["提醒"] || []));
  details.push("輸出的是現在這一支成品，會另外存一支新的，舊的不會被蓋掉。");
  if (win) details.push("成品在 Ubuntu 裡：輸出之後，再按「複製成品到 Windows 的下載資料夾」，才拿得到。");
  return { blocked: false, title: left ? `還有 ${left} 張沒看完，確定要輸出嗎？` : "確定要輸出嗎？", names: g["名字提醒"] || [], details };
}

function fcConfirmExport(gaps, win) {
  return new Promise((resolve) => {
    const st = fc.data["卡片統計"] || {};
    const a = fcExportAsk(gaps, win, (st["還沒看"] || 0) + (st["要重做"] || 0));
    const dlg = document.createElement("dialog");
    dlg.className = "rv-confirm";
    dlg.setAttribute("aria-labelledby", "fc-export-title");
    dlg.innerHTML = `<h2 id="fc-export-title">${esc(a.title)}</h2>
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

// ---------- 快捷鍵（照第 3 步：Enter 通過、E 改；空白鍵播放；↑↓ 上一張下一張；J／L 前後 5 秒） ----------

document.addEventListener("keydown", (e) => {
  if (currentRouteId() !== "step5" || !fc.video) return;
  if (e.isComposing || e.keyCode === 229) return;
  if (document.querySelector("dialog[open]")) return;
  if (["TEXTAREA", "INPUT", "SELECT"].includes(e.target.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
  const k = e.key.toLowerCase();
  const c = fc.cur && fcCard(fc.cur);
  if (e.key === " ") { e.preventDefault(); fc.video.paused ? fc.video.play().catch(() => {}) : fc.video.pause(); }
  else if (e.key === "Enter" && e.target.tagName !== "BUTTON" && fcCanJudge(c)) {   // Enter＝通過（舊版也可以）；退回一律走按鈕
    e.preventDefault(); fcDecide(c, c["狀態"] === FC_ST.OK ? null : FC_ST.OK);
  }
  else if (k === "e") { e.preventDefault(); fcToggleEdit(); }
  else if (k === "j") fcSeek(fc.video.currentTime - 5);
  else if (k === "l") fcSeek(fc.video.currentTime + 5);
  else if (e.key === "ArrowDown") { e.preventDefault(); fcStep(1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); fcStep(-1); }
});
