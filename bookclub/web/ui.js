"use strict";
/* 1022-1（#263）：工具自己的確認／輸入／提示視窗，取代瀏覽器內建的 confirm()、prompt()、alert()。
 * 內建框標題是「localhost:8766 顯示」、長文擠成一團、按鈕只有「確定／取消」，老師看不懂要按哪一顆。
 *
 *   await uiConfirm({title, message, ok, cancel, danger})  → true（按了主要按鈕）／false（取消、✕、Esc）
 *   await uiPrompt({title, message, placeholder, ok, value}) → 打的字（去頭尾空白前的原樣）／null（取消）
 *   await uiAlert({title, message, ok})                     → 關掉時 resolve
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

function uiParas(message) {
  const frag = document.createDocumentFragment();
  String(message == null ? "" : message).split(/\n/).forEach((line) => {
    if (!line.trim()) return;   // 空行只是段落間距，不另外畫
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
  x.addEventListener("click", () => close(cancelValue));
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(cancelValue); });   // Esc＝取消
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

function uiConfirm(opts) { return uiEnqueue((finish) => uiOpen("confirm", opts, finish)); }
function uiPrompt(opts) { return uiEnqueue((finish) => uiOpen("prompt", opts, finish)); }
function uiAlert(opts) { return uiEnqueue((finish) => uiOpen("alert", opts, finish)); }
