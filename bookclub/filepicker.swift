// 常駐的 Mac 選檔小程式（10-08）：網頁「選影片」叫的系統選檔視窗。
//
// 為什麼不每次叫 osascript：開發者的 Mac 實測，osascript 每次從啟動到選檔視窗真的出現在畫面上要 1.2～1.4 秒
// （第一次 2～3 秒），時間都花在建選檔視窗本身；同一支程式第二次以後開只要約 0.55 秒。
// 所以伺服器啟動時就在背景把這支小程式開起來、先建一個選檔視窗預熱（不顯示），之後每次按「選影片」都用它開。
//
// 跟伺服器（bookclub/fileio.py 的 MacPicker）用一行一個 JSON 溝通：
//   啟動、預熱好 → 印 {"ready": true}
//   收到 {"title": "...", "exts": ["mp4", ...]} → 印 {"opening": true}（馬上要開視窗）→ 選好印 {"path": "..."}；取消印 {"cancel": true}
//   stdin 關掉（伺服器結束）→ 自己結束
// 編譯：swiftc -O -o ~/.cache/bookclub/filepicker bookclub/filepicker.swift（fileio.py 會自己編，原始碼比較新才重編）

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

while let line = readLine() {
    guard let data = line.data(using: .utf8),
          let req = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
        emit(["error": "看不懂的要求"])
        continue
    }
    let p = NSOpenPanel()
    p.message = req["title"] as? String ?? "選影片"
    p.allowedContentTypes = types(req["exts"] as? [String] ?? [])
    p.canChooseDirectories = false
    p.canChooseFiles = true
    p.allowsMultipleSelection = false
    p.level = .floating   // 浮在瀏覽器上面，不會躲在後面
    app.activate(ignoringOtherApps: true)
    // 量測用：設了環境變數 BOOKCLUB_PICKER_AUTOCANCEL=秒數，視窗開了那麼久自動取消（平常不設）
    if let s = ProcessInfo.processInfo.environment["BOOKCLUB_PICKER_AUTOCANCEL"], let sec = Double(s) {
        let t = Timer(timeInterval: sec, repeats: false) { _ in p.cancel(nil) }
        RunLoop.main.add(t, forMode: .modalPanel)
    }
    emit(["opening": true])
    if p.runModal() == .OK, let u = p.url {
        emit(["path": u.path])
    } else {
        emit(["cancel": true])
    }
}
