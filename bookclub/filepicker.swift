// 常駐的 Mac 選檔小程式（10-08）：網頁「選影片」叫的系統選檔視窗。
//
// 為什麼不每次叫 osascript：開發者的 Mac 實測，從網頁按下到視窗出現，每次叫 osascript 第一次 2.00 秒、之後 0.94～1.45 秒，
// 時間都花在建選檔視窗本身；改用這支常駐小程式（先預熱）後第一次 0.67 秒、之後 0.27～0.41 秒；預熱含第一次編譯約 5 秒。
// 所以伺服器啟動時就在背景把這支小程式開起來、先建一個選檔視窗預熱（不顯示），之後每次按「選影片」都用它開。
//
// 跟伺服器（bookclub/fileio.py 的 MacPicker）用一行一個 JSON 溝通：
//   啟動、預熱好 → 印 {"ready": true}
//   收到 {"title": "...", "exts": ["mp4", ...]} → 印 {"opening": true}（馬上要開視窗）→ 選好印 {"path": "..."}；取消印 {"cancel": true}
//   stdin 關掉（伺服器結束）→ 自己結束
// 編譯：fileio.swift_build 編到 ~/.cache/bookclub/filepicker-<原始碼雜湊前 8 碼>（有裝 Xcode 命令列工具才編）

import AppKit
import UniformTypeIdentifiers

let app = NSApplication.shared
app.setActivationPolicy(.accessory)   // 不在 Dock 出現圖示

func emit(_ obj: [String: Any]) {
    if let d = try? JSONSerialization.data(withJSONObject: obj), let s = String(data: d, encoding: .utf8) {
        print(s)
        fflush(stdout)
    }
}

func types(_ exts: [String]) -> [UTType] {
    var out: [UTType] = [.movie]
    for e in exts {
        if let t = UTType(filenameExtension: e) { out.append(t) }
    }
    return out
}

// 預熱：建一個選檔視窗但不顯示（第一次建要 2 秒多，建過一次之後同一支程式再建就快）
let warm = NSOpenPanel()
warm.allowedContentTypes = types(["mp4"])
_ = warm.directoryURL
warm.layoutIfNeeded()
emit(["ready": true])

// 10-08 修正：以前主執行緒在兩次之間卡在 readLine() 等下一個要求（sample 看得到主執行緒停在 __read_nocancel，CPU 0%，
// 不是忙迴圈），但選檔視窗關掉後這支小程式還是「最前面的程式」，macOS 看到最前面的程式不處理事件，
// 就整個畫面轉彩色圈圈，Edge、其他程式都點不動（宇軒按取消後遇到，重開機才好）。
// 現在：主執行緒一直跑事件迴圈（app.run()），另一條執行緒讀 stdin、把要求交給主執行緒；視窗關掉後馬上 hide，
// 把「最前面」還給原本的程式（瀏覽器）。
func handle(_ line: String) {
    guard let data = line.data(using: .utf8),
          let req = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
        emit(["error": "看不懂的要求"])
        return
    }
    let p = NSOpenPanel()
    p.message = req["title"] as? String ?? "選影片"
    p.allowedContentTypes = types(req["exts"] as? [String] ?? [])
    p.canChooseDirectories = false
    p.canChooseFiles = true
    p.allowsMultipleSelection = false
    // 1008-5：不設成浮在所有程式之上（.floating）；靠 activate 把視窗帶到前面，關掉後立刻 orderOut＋hide
    app.activate(ignoringOtherApps: true)
    // 量測用：設了環境變數 BOOKCLUB_PICKER_AUTOCANCEL=秒數，視窗開了那麼久自動取消（平常不設）
    if let s = ProcessInfo.processInfo.environment["BOOKCLUB_PICKER_AUTOCANCEL"], let sec = Double(s) {
        let t = Timer(timeInterval: sec, repeats: false) { _ in p.cancel(nil) }
        RunLoop.main.add(t, forMode: .modalPanel)
    }
    emit(["opening": true])
    let ok = p.runModal() == .OK
    let path = p.url?.path
    p.orderOut(nil)
    app.hide(nil)   // 選好、取消、按 Esc 都一樣：把最前面還給原本的程式
    if ok, let path = path {
        emit(["path": path])
    } else {
        emit(["cancel": true])
    }
}

DispatchQueue.global(qos: .userInitiated).async {
    while let line = readLine() {
        DispatchQueue.main.async { handle(line) }
    }
    DispatchQueue.main.async { exit(0) }   // stdin 關掉（伺服器結束）→ 自己結束
}
app.run()
