// 列出屏幕上的窗口（用来找到「Mac 清理助手」窗口的 id，然后单独截它）
//
//   swift tools/winlist.swift              # 列屏幕上的大窗口
//   swift tools/winlist.swift 清理         # 只列 owner/title 里含「清理」的（含其它 Space）
//
// 配合：screencapture -l <id> -x -o /tmp/appwin.png
import CoreGraphics
import Foundation

let filter = CommandLine.arguments.count > 1 ? CommandLine.arguments[1].lowercased() : ""
let list = (CGWindowListCopyWindowInfo(filter.isEmpty ? [.optionOnScreenOnly, .excludeDesktopElements] : [.optionAll],
                                       kCGNullWindowID) as? [[String: Any]]) ?? []
var shown = 0
for w in list {
    let owner = (w[kCGWindowOwnerName as String] as? String) ?? "?"
    let name = (w[kCGWindowName as String] as? String) ?? ""
    let layer = (w[kCGWindowLayer as String] as? Int) ?? -1
    let id = (w[kCGWindowNumber as String] as? Int) ?? 0
    let onscreen = (w[kCGWindowIsOnscreen as String] as? Bool) ?? false
    let b = (w[kCGWindowBounds as String] as? [String: Any]) ?? [:]
    let wd = (b["Width"] as? Double) ?? 0, ht = (b["Height"] as? Double) ?? 0
    let x = (b["X"] as? Double) ?? 0, y = (b["Y"] as? Double) ?? 0
    if filter.isEmpty {
        if layer != 0 || wd < 200 || ht < 200 { continue }
    } else if !owner.lowercased().contains(filter) && !name.lowercased().contains(filter) {
        continue
    }
    shown += 1
    print("id=\(id) owner=\(owner) title=\(name.isEmpty ? "(无标题)" : name) onscreen=\(onscreen) pos=(\(Int(x)),\(Int(y))) \(Int(wd))x\(Int(ht))")
}
print("—— 共 \(shown) 个 ——")
