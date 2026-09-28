// 用 AVFoundation 把幾段 mp4 影片接起來（不重新編碼），加上一條聲音，輸出 mp4／mov。
// 各段編碼參數不同時，AVFoundation 會在同一軌裡保留多組參數（QuickTime 能播）。
// bookclub/render.py 在 macOS 上會自動編譯到 ~/.cache/bookclub/avconcat 再呼叫（只重做片段的輸出做法）。
// 用法：avconcat <片段清單.txt（一行一個 mp4）> <聲音.m4a> <輸出.mp4|.mov>
import AVFoundation
import Foundation

let args = CommandLine.arguments
guard args.count == 4 else { print("用法：avconcat <清單> <聲音> <輸出>"); exit(2) }
let paths = try! String(contentsOfFile: args[1], encoding: .utf8).split(separator: "\n").map(String.init).filter { !$0.isEmpty }
let comp = AVMutableComposition()
let vt = comp.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid)!
var cursor = CMTime.zero
for p in paths {
    let a = AVURLAsset(url: URL(fileURLWithPath: p))
    guard let t = a.tracks(withMediaType: .video).first else { print("沒有影像軌：\(p)"); exit(1) }
    let r = t.timeRange
    try! vt.insertTimeRange(r, of: t, at: cursor)
    cursor = CMTimeAdd(cursor, r.duration)
}
let aa = AVURLAsset(url: URL(fileURLWithPath: args[2]))
if let src = aa.tracks(withMediaType: .audio).first {
    let at = comp.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)!
    try! at.insertTimeRange(CMTimeRange(start: .zero, duration: min(src.timeRange.duration, cursor)), of: src, at: .zero)
}
let out = URL(fileURLWithPath: args[3])
try? FileManager.default.removeItem(at: out)
let ex = AVAssetExportSession(asset: comp, presetName: AVAssetExportPresetPassthrough)!
ex.outputURL = out
ex.outputFileType = out.pathExtension.lowercased() == "mov" ? .mov : .mp4
ex.shouldOptimizeForNetworkUse = true
let sem = DispatchSemaphore(value: 0)
ex.exportAsynchronously { sem.signal() }
sem.wait()
if ex.status == .completed {
    print("完成 \(CMTimeGetSeconds(cursor)) 秒")
} else {
    print("失敗：\(ex.error?.localizedDescription ?? "?")")
    exit(1)
}
