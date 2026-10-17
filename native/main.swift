// Mac 清理助手 · 原生 AppKit 界面（真·Mac 窗口应用）
//
// 设计：UI 是原生的（NSWindow / NSTableView / NSButton），清理引擎仍复用技能
// macos-deep-optimize 的 scan/report/clean/judge 脚本 —— 规则只有一份，改技能就能生效。
// App 启动时把引擎（本地 HTTP，只监听 127.0.0.1）拉起来，退出时关掉。
//
// 安全不变：默认预览；执行要输入「确认清理」；逐项勾选走 --select-file（只做交集）。

import Cocoa
import WebKit

// MARK: - 引擎位置

func stateDir() -> String {
    let home = FileManager.default.homeDirectoryForCurrentUser.path
    let d = home + "/.local/state/mac-cleanup-app"
    try? FileManager.default.createDirectory(atPath: d, withIntermediateDirectories: true)
    return d
}

func skillDir() -> String {
    let home = FileManager.default.homeDirectoryForCurrentUser.path
    if let e = ProcessInfo.processInfo.environment["MAC_CLEAN_SKILL"], !e.isEmpty { return e }
    let live = home + "/.hermes/skills/devops/macos-deep-optimize/scripts"
    if FileManager.default.fileExists(atPath: live + "/scan.py") { return live }
    let bundled = Bundle.main.resourcePath.map { $0 + "/skill-scripts" } ?? ""
    return bundled
}

func pythonPath() -> String {
    for c in ["/usr/bin/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3"] {
        if FileManager.default.isExecutableFile(atPath: c) { return c }
    }
    return "/usr/bin/python3"
}

func freePort() -> Int {
    let fd = socket(AF_INET, SOCK_STREAM, 0)
    if fd < 0 { return 8765 }
    defer { close(fd) }
    var addr = sockaddr_in()
    addr.sin_family = sa_family_t(AF_INET)
    addr.sin_addr.s_addr = inet_addr("127.0.0.1")
    addr.sin_port = 0
    var len = socklen_t(MemoryLayout<sockaddr_in>.size)
    _ = withUnsafePointer(to: &addr) { p in
        p.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, len) }
    }
    _ = withUnsafeMutablePointer(to: &addr) { p in
        p.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(fd, $0, &len) }
    }
    return Int(UInt16(bigEndian: addr.sin_port))
}

func human(_ kb: Double) -> String {
    if kb >= 1_048_576 { return String(format: "%.2f GB", kb / 1_048_576) }
    if kb >= 1024 { return String(format: "%.0f MB", kb / 1024) }
    return String(format: "%.0f KB", kb)
}

func shortPath(_ p: String) -> String {
    let home = FileManager.default.homeDirectoryForCurrentUser.path
    return p.hasPrefix(home) ? "~" + p.dropFirst(home.count) : p
}

// MARK: - 数据模型

struct JevInfo {
    var tier = "", category = "", label = "", advice = "", recoverability = ""
    var safe = -1.0, confidence = -1.0

    init(_ d: [String: Any]) {
        tier = d["tier"] as? String ?? ""
        category = d["category"] as? String ?? ""
        label = d["category_label"] as? String ?? ""
        advice = d["advice"] as? String ?? ""
        recoverability = d["recoverability"] as? String ?? ""
        if let v = d["safe_to_delete"] as? Double { safe = v }
        if let v = d["safe_to_delete"] as? Int { safe = Double(v) }
        if let v = d["category_confidence"] as? Double { confidence = v }
        if let v = d["category_confidence"] as? Int { confidence = Double(v) }
    }
}

struct Item {
    var path = "", name = "", size = "", tier = "", note = ""
    var sizeKB = 0.0
    var selectable = false
    var ai: JevInfo?

    init(_ d: [String: Any]) {
        path = d["path"] as? String ?? ""
        name = d["name"] as? String ?? (path as NSString).lastPathComponent
        size = d["size"] as? String ?? ""
        tier = d["tier"] as? String ?? ""
        note = d["note"] as? String ?? ""
        selectable = d["selectable"] as? Bool ?? false
        if let v = d["size_kb"] as? Double { sizeKB = v }
        if let v = d["size_kb"] as? Int { sizeKB = Double(v) }
        if let a = d["ai"] as? [String: Any] { ai = JevInfo(a) }
    }
}

enum Row {
    case header(String)
    case item(Item)
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate, NSTableViewDataSource, NSTableViewDelegate, WKUIDelegate {

    var window: NSWindow!
    var webView: WKWebView?
    var engine: Process?
    var port = 0
    var engineReady = false

    // UI
    let titleLabel = NSTextField(labelWithString: "🧹 Mac 清理助手")
    let diskLabel = NSTextField(labelWithString: "读取磁盘…")
    let diskBar = NSProgressIndicator()
    let pctLabel = NSTextField(labelWithString: "")
    let scanBtn = NSButton(title: "开始体检", target: nil, action: nil)
    let reportBtn = NSButton(title: "打开完整报告", target: nil, action: nil)
    let webBtn = NSButton(title: "浏览器版界面", target: nil, action: nil)
    let wlBtn = NSButton(title: "保护名单", target: nil, action: nil)
    let histBtn = NSButton(title: "操作历史", target: nil, action: nil)
    let statusLabel = NSTextField(labelWithString: "正在启动引擎…")
    let tierLabel = NSTextField(labelWithString: "")
    let selLabel = NSTextField(labelWithString: "")
    let aiStatus = NSTextField(labelWithString: "检查中…")
    let keyField = NSSecureTextField(string: "")
    let saveKeyBtn = NSButton(title: "保存 key", target: nil, action: nil)
    let topField = NSTextField(string: "15")
    let onlyUnknown = NSButton(checkboxWithTitle: "只判断规则表认不出的", target: nil, action: nil)
    let judgeBtn = NSButton(title: "运行 AI 判断", target: nil, action: nil)
    let previewBtn = NSButton(title: "预览选中的 0 项", target: nil, action: nil)
    let applyBtn = NSButton(title: "执行清理（移到废纸篓）", target: nil, action: nil)
    let confirmField = NSTextField(string: "")
    let trashBtn = NSButton(title: "清空废纸篓", target: nil, action: nil)
    let trashField = NSTextField(string: "")
    let tableView = NSTableView()
    let logView = NSTextView()

    // 状态
    var rows: [Row] = []
    var selected = Set<String>()
    var touched = false
    var busyPhase = "idle"
    var elapsed = 0.0
    var lastKey = ""

    // MARK: 生命周期

    func applicationDidFinishLaunching(_ note: Notification) {
        port = freePort()
        if CommandLine.arguments.contains("--selftest") {     // 无界面自检：用 App 自己的调用路径验一遍
            startEngine()
            selfTest()
            return
        }
        buildMenu()
        // 默认：窗口里就是这个界面本身（同一份 HTML，手机/浏览器打开也是它）
        // 加 --classic 则回退到 AppKit 原生控件版界面
        let classic = CommandLine.arguments.contains("--classic")
        classic ? buildWindow() : buildWebWindow()
        startEngine()
        if classic {
            Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { [weak self] _ in self?.poll() }
            logLine("界面已就绪（AppKit 版）；引擎端口 \(port)")
        } else {
            waitForEngine(try: 0)
            logLine("窗口已就绪，等待引擎；端口 \(port)")
        }
    }

    // MARK: Web 版窗口（默认）

    func buildWebWindow() {
        let cfg = WKWebViewConfiguration()
        cfg.defaultWebpagePreferences.allowsContentJavaScript = true
        let wv = WKWebView(frame: NSRect(x: 0, y: 0, width: 1280, height: 900), configuration: cfg)
        wv.uiDelegate = self
        wv.autoresizingMask = [.width, .height]
        webView = wv
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 900),
                         styleMask: [.titled, .closable, .miniaturizable, .resizable],
                         backing: .buffered, defer: false)
        w.title = "Mac 清理助手"
        w.minSize = NSSize(width: 900, height: 620)
        w.center()
        w.setFrameAutosaveName("MacCleanupWeb")
        w.contentView = wv
        window = w
        splash("正在启动本机引擎…")
        w.makeKeyAndOrderFront(nil)
        w.orderFrontRegardless()
        NSApp.activate(ignoringOtherApps: true)
    }

    func splash(_ text: String) {
        let html = """
        <!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><style>
        body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#100D0A;
             color:#F4E7CF;font:14px -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}
        .b{text-align:center}.t{font-size:19px;font-weight:600;letter-spacing:-.02em;margin-bottom:9px}
        .s{color:#B3A288;font-size:13px}
        </style></head><body><div class="b"><div class="t">Mac 清理助手</div><div class="s">\(text)</div></div></body></html>
        """
        webView?.loadHTMLString(html, baseURL: nil)
    }

    func waitForEngine(try n: Int) {
        api("GET", "/api/state", nil, timeout: 8) { [weak self] s in
            guard let self = self else { return }
            if s["phase"] != nil {
                self.webView?.load(URLRequest(url: URL(string: "http://127.0.0.1:\(self.port)/")!))
                self.logLine("界面已载入：http://127.0.0.1:\(self.port)/")
            } else if n < 40 {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { self.waitForEngine(try: n + 1) }
            } else {
                self.splash("引擎没能起来，看日志：\(stateDir())/engine.log")
                self.logLine("❌ 引擎启动超时")
            }
        }
    }

    // 页面里的 window.open（完整报告 / 在浏览器打开）交给系统浏览器
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let u = navigationAction.request.url { NSWorkspace.shared.open(u) }
        return nil
    }

    /// 走界面同一条代码路径（api 调用 + 请求体），把结果打到标准输出。
    func selfTest() {
        var pass = 0, fail = 0
        func check(_ name: String, _ ok: Bool, _ detail: String = "") {
            print("\(ok ? "✅" : "❌") \(name)\(detail.isEmpty ? "" : "  — " + detail)")
            if ok { pass += 1 } else { fail += 1 }
        }
        var ready = false
        for _ in 0..<40 {
            if apiSync("GET", "/api/state", nil, timeout: 5)["phase"] != nil { ready = true; break }
            usleep(250_000)
        }
        check("引擎起来并响应 /api/state", ready)
        guard ready else { print("—— 自检结束：引擎没起来 ——"); NSApp.terminate(nil); return }

        let st = apiSync("GET", "/api/state", nil, timeout: 15)
        let items = ((st["items"] as? [[String: Any]]) ?? []).map { Item($0) }
        let selectable = items.filter { $0.selectable }
        check("拿到清单", !items.isEmpty, "\(items.count) 项，其中可勾选 \(selectable.count)")
        if let d = st["disk"] as? [String: Any] {
            check("磁盘信息", (d["free_gb"] as? Double) != nil, "可用 \(d["free_gb"] ?? "?") GB / 共 \(d["total_gb"] ?? "?") GB")
        }
        check("AI 判断结果已并入清单", items.contains { $0.ai != nil }, "带 🤖 的 \(items.filter { $0.ai != nil }.count) 项")

        let sel = Array(selectable.prefix(2)).map { $0.path }
        check("能选中两个绿灯项", sel.count == 2, sel.map { shortPath($0) }.joined(separator: " , "))
        let pv = apiSync("POST", "/api/clean", ["mode": "preview", "paths": sel], timeout: 900)
        let out = (pv["output"] as? String) ?? ""
        check("勾选 2 项 → 预览只处理这 2 项", pv["ok"] as? Bool == true && out.contains("选中 2 项"),
              out.split(separator: "\n").first.map(String.init) ?? "")

        let bad = apiSync("POST", "/api/clean", ["mode": "apply", "confirm": "删了吧", "paths": sel], timeout: 60)
        check("确认文本不对 → 后端拒绝执行", bad["ok"] as? Bool != true, (bad["error"] as? String) ?? "")
        let empty = apiSync("POST", "/api/clean", ["mode": "preview", "paths": [String]()], timeout: 60)
        check("空勾选 → 被拒绝", empty["ok"] as? Bool != true, (empty["error"] as? String) ?? "")
        let wl = apiSync("GET", "/api/whitelist", nil, timeout: 60)
        check("保护名单可读", (wl["output"] as? String)?.isEmpty == false)
        let hj = apiSync("GET", "/api/history", nil, timeout: 60)
        check("操作历史可读", (hj["output"] as? String)?.isEmpty == false)

        print("—— 自检结果：通过 \(pass) 项，失败 \(fail) 项 ——")
        _ = apiSync("POST", "/api/quit", [:], timeout: 5)
        if let e = engine, e.isRunning { e.terminate() }
        NSApp.terminate(nil)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ note: Notification) {
        // 退出时顺手把引擎收掉，不留孤儿进程
        _ = apiSync("POST", "/api/quit", [:], timeout: 3)
        if let p = engine, p.isRunning { p.terminate() }
    }

    // MARK: 菜单

    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "关于 Mac 清理助手", action: #selector(about), keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "在浏览器中打开界面", action: #selector(openWeb), keyEquivalent: "b")
        appMenu.addItem(withTitle: "重新载入界面", action: #selector(reloadUI), keyEquivalent: "r")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "退出", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        NSApp.mainMenu = main
    }

    @objc func about() {
        let a = NSAlert()
        a.messageText = "Mac 清理助手"
        a.informativeText = """
        原生 Mac 界面 + 技能 macos-deep-optimize 的清理规则。

        · 扫描只读，不改动文件
        · 默认只预览；执行要输入「确认清理」
        · 逐项勾选只做交集，不会越过安全白名单
        · AI 判断（TypeSafe/Jev）会把目录路径、大小、修改时间发给外部接口，可不用
        """
        a.runModal()
    }

    @objc func openWeb() { NSWorkspace.shared.open(URL(string: "http://127.0.0.1:\(port)/")!) }

    @objc func reloadUI() {
        webView?.load(URLRequest(url: URL(string: "http://127.0.0.1:\(port)/")!))
    }

    // MARK: 引擎

    func startEngine() {
        let logPath = stateDir() + "/engine.log"
        FileManager.default.createFile(atPath: logPath, contents: nil)
        let fh = FileHandle(forWritingAtPath: logPath)
        let task = Process()
        task.executableURL = URL(fileURLWithPath: pythonPath())
        var env = ProcessInfo.processInfo.environment
        env["MAC_CLEAN_OPEN"] = "0"                      // 由本界面负责显示，不弹浏览器
        env["MAC_CLEAN_PORT"] = "\(port)"
        env["MAC_CLEAN_SKILL"] = skillDir()
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin:" + (env["PATH"] ?? "")
        task.environment = env
        let serverPath = (Bundle.main.resourcePath ?? "") + "/server.py"
        task.arguments = [serverPath]
        task.standardOutput = fh
        task.standardError = fh
        do { try task.run(); engine = task; logLine("引擎已启动：\(serverPath)") }
        catch { logLine("❌ 引擎启动失败：\(error.localizedDescription)") }
    }

    // MARK: HTTP

    func api(_ method: String, _ path: String, _ body: [String: Any]?, timeout: TimeInterval = 1200,
             done: @escaping ([String: Any]) -> Void) {
        guard let url = URL(string: "http://127.0.0.1:\(port)\(path)") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.timeoutInterval = timeout
        if let b = body {
            req.httpBody = try? JSONSerialization.data(withJSONObject: b)
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        URLSession.shared.dataTask(with: req) { data, _, err in
            var obj: [String: Any] = [:]
            if let d = data, let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any] { obj = j }
            if !obj.isEmpty { obj["_httpOK"] = (err == nil) }
            else { obj = ["ok": false, "error": err?.localizedDescription ?? "无响应"] }
            DispatchQueue.main.async { done(obj) }
        }.resume()
    }

    @discardableResult
    func apiSync(_ method: String, _ path: String, _ body: [String: Any]?, timeout: TimeInterval) -> [String: Any] {
        guard let url = URL(string: "http://127.0.0.1:\(port)\(path)") else { return [:] }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.timeoutInterval = timeout
        if let b = body {
            req.httpBody = try? JSONSerialization.data(withJSONObject: b)
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let sem = DispatchSemaphore(value: 0)
        var out: [String: Any] = [:]
        URLSession.shared.dataTask(with: req) { data, _, _ in
            if let d = data, let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any] { out = j }
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + timeout)
        return out
    }

    func logLine(_ s: String) {
        let ts = DateFormatter.localizedString(from: Date(), dateStyle: .none, timeStyle: .medium)
        logView.string += "[\(ts)] \(s)\n"
        logView.scrollToEndOfDocument(nil)
    }

    // MARK: 轮询

    func poll() {
        if !engineReady, let e = engine, !e.isRunning {
            logLine("⚠️ 引擎进程已退出（日志：\(stateDir())/engine.log）")
        }
        api("GET", "/api/state", nil, timeout: 30) { [weak self] s in
            guard let self = self else { return }
            guard s["phase"] != nil else { return }
            self.engineReady = true
            self.render(state: s)
        }
    }

    func render(state s: [String: Any]) {
        if let d = s["disk"] as? [String: Any] {
            let free = d["free_gb"] as? Double ?? 0
            let used = d["used_gb"] as? Double ?? 0
            let total = d["total_gb"] as? Double ?? 0
            let pct = d["pct"] as? Double ?? 0
            diskLabel.stringValue = String(format: "可用 %.1f GB · 已用 %.1f / 共 %.1f GB", free, used, total)
            diskBar.doubleValue = pct
            pctLabel.stringValue = String(format: "占用 %.0f%%", pct)
        }
        busyPhase = s["phase"] as? String ?? "idle"
        elapsed = s["elapsed"] as? Double ?? 0
        let busy = (busyPhase == "scanning" || busyPhase == "cleaninig" || busyPhase == "cleaning" || busyPhase == "judging")
        scanBtn.isEnabled = !busy && engineReady
        scanBtn.title = busyPhase == "scanning" ? String(format: "扫描中… %.0fs", elapsed) : "开始体检"
        judgeBtn.isEnabled = !busy && (s["has_scan"] as? Bool ?? false) && engineReady
        judgeBtn.title = busyPhase == "judging" ? String(format: "判断中… %.0fs", elapsed) : "运行 AI 判断"
        let hasScan = s["has_scan"] as? Bool ?? false
        reportBtn.isEnabled = hasScan
        previewBtn.isEnabled = hasScan
        applyBtn.isEnabled = hasScan
        if let err = s["error"] as? String, !err.isEmpty { statusLabel.stringValue = "❌ " + err }
        else if let n = s["notice"] as? String, !n.isEmpty { statusLabel.stringValue = n }
        else if busy { statusLabel.stringValue = "工作中…" }
        else if hasScan { statusLabel.stringValue = "上次扫描：\((s["scan_at"] as? String) ?? "?")" }
        else { statusLabel.stringValue = "还没扫描。点「开始体检」（只读，1-3 分钟）" }
        if let t = s["tiers"] as? [String: Any], !t.isEmpty {
            let names = [("green", "🟢 可安全清理"), ("yellow", "🟡 需你判断"), ("red", "🔴 只走系统设置")]
            var parts: [String] = []
            for (k, label) in names {
                if let m = t[k] as? [String: Any], let mb = m["mb"] as? Double {
                    parts.append("\(label) \(human(mb * 1024))")
                }
            }
            tierLabel.stringValue = parts.joined(separator: "   ·   ")
        }
        if let j = s["judged"] as? [String: Any], let c = j["count"] as? Int {
            aiStatus.stringValue = "✅ key 已配置 · 上次判断 \(c) 项（\((j["model"] as? String) ?? "")）"
        }

        // 清单
        if let arr = s["items"] as? [[String: Any]] {
            let items = arr.map { Item($0) }
            let key = items.map { $0.path + ($0.ai != nil ? "1" : "0") }.joined(separator: "|")
            if key != lastKey {
                lastKey = key
                buildRows(items)
                if !touched { selected = Set(items.filter { $0.selectable }.map { $0.path }) }
                tableView.reloadData()
                updateSel()
            }
        }
    }

    func buildRows(_ items: [Item]) {
        let green = items.filter { $0.tier == "green" }
        let yellow = items.filter { $0.tier == "yellow" }
        let unknown = items.filter { $0.tier == "unknown" }
        rows = []
        let gkb = green.reduce(0.0) { $0 + $1.sizeKB }
        let ykb = yellow.reduce(0.0) { $0 + $1.sizeKB }
        if !green.isEmpty { rows.append(.header("🟢 可安全清理（\(green.count) 项 · \(human(gkb))）—— 纯缓存/可再生，勾选后可直接执行（🤖 徽章只是参考）")) }
        rows += green.map { Row.item($0) }
        if !yellow.isEmpty { rows.append(.header("🟡 需你判断（\(yellow.count) 项 · \(human(ykb))）—— 只有标「可自动清理」的纯缓存子目录能勾")) }
        rows += yellow.map { Row.item($0) }
        if !unknown.isEmpty { rows.append(.header("🤔 规则表认不出 · AI 判断（\(unknown.count) 项，仅供参考，不能自动清理）")) }
        rows += unknown.map { Row.item($0) }
    }

    func updateSel() {
        var kb = 0.0, n = 0, cap = 0
        for r in rows { if case .item(let it) = r { if it.selectable { cap += 1 }; if selected.contains(it.path) { kb += it.sizeKB; n += 1 } } }
        selLabel.stringValue = "已选 \(n) / 可选 \(cap) 项 · \(human(kb))"
        previewBtn.title = "预览选中的 \(n) 项"
    }

    // MARK: 表格

    func numberOfRows(in tableView: NSTableView) -> Int { rows.count }

    func tableView(_ tableView: NSTableView, heightOfRow row: Int) -> CGFloat {
        switch rows[row] {
        case .header: return 26
        case .item(let it): return it.note.isEmpty ? 38 : 52
        }
    }

    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        let ident = tableColumn?.identifier.rawValue ?? "main"
        let cell = NSView()
        switch rows[row] {
        case .header(let text):
            // 分组标题只在主列画一次（每列都画会叠字）
            guard ident == "main" else { return cell }
            let l = NSTextField(labelWithString: text)
            l.font = NSFont.boldSystemFont(ofSize: 12)
            l.textColor = .secondaryLabelColor
            l.translatesAutoresizingMaskIntoConstraints = false
            cell.addSubview(l)
            NSLayoutConstraint.activate([l.leadingAnchor.constraint(equalTo: cell.leadingAnchor, constant: 4),
                                         l.centerYAnchor.constraint(equalTo: cell.centerYAnchor)])
            return cell
        case .item(let it):
            if ident == "check" {
                let cb = NSButton(checkboxWithTitle: "", target: self, action: #selector(toggleItem(_:)))
                cb.tag = row
                cb.state = selected.contains(it.path) ? .on : .off
                cb.isEnabled = it.selectable
                cb.translatesAutoresizingMaskIntoConstraints = false
                cell.addSubview(cb)
                NSLayoutConstraint.activate([cb.leadingAnchor.constraint(equalTo: cell.leadingAnchor, constant: 4),
                                             cb.topAnchor.constraint(equalTo: cell.topAnchor, constant: 2)])
                return cell
            }
            if ident == "size" {
                let l = NSTextField(labelWithString: it.size)
                l.font = NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .regular)
                l.textColor = it.selectable ? .labelColor : .secondaryLabelColor
                l.translatesAutoresizingMaskIntoConstraints = false
                cell.addSubview(l)
                NSLayoutConstraint.activate([l.leadingAnchor.constraint(equalTo: cell.leadingAnchor, constant: 4),
                                             l.topAnchor.constraint(equalTo: cell.topAnchor, constant: 2)])
                return cell
            }
            // 主列：名称 + 徽章 / 路径 / 备注
            let a = NSMutableAttributedString()
            let nameColor: NSColor = it.selectable ? .labelColor : .secondaryLabelColor
            a.append(NSAttributedString(string: it.name.isEmpty ? (it.path as NSString).lastPathComponent : it.name,
                                        attributes: [.font: NSFont.boldSystemFont(ofSize: 12.5), .foregroundColor: nameColor]))
            if !it.selectable {
                a.append(NSAttributedString(string: "  不可自动清理",
                                            attributes: [.font: NSFont.systemFont(ofSize: 10.5), .foregroundColor: NSColor.systemOrange]))
            }
            if let ai = it.ai {
                let p = ai.safe < 0 ? "?" : String(format: "%.0f%%", ai.safe * 100)
                let rec = ai.recoverability.isEmpty ? "?" : ai.recoverability
                let tag: String = ai.tier == "green" ? "建议删" : (ai.tier == "red" ? "别删" : "拿不准")
                a.append(NSAttributedString(string: "  🤖 \(tag) · 可删概率 \(p) · 恢复 \(rec)",
                                            attributes: [.font: NSFont.systemFont(ofSize: 10.5), .foregroundColor: NSColor.systemPurple]))
            } else if it.tier != "unknown" {
                a.append(NSAttributedString(string: "  🤖 未判断",
                                            attributes: [.font: NSFont.systemFont(ofSize: 10.5), .foregroundColor: NSColor.tertiaryLabelColor]))
            }
            a.append(NSAttributedString(string: "\n" + shortPath(it.path),
                                        attributes: [.font: NSFont.monospacedSystemFont(ofSize: 10.5, weight: .regular),
                                                     .foregroundColor: NSColor.tertiaryLabelColor]))
            if !it.note.isEmpty {
                a.append(NSAttributedString(string: "\n" + it.note,
                                            attributes: [.font: NSFont.systemFont(ofSize: 10.5),
                                                         .foregroundColor: NSColor.secondaryLabelColor]))
            }
            let l = NSTextField(labelWithAttributedString: a)
            l.maximumNumberOfLines = 3
            l.lineBreakMode = .byTruncatingTail
            l.translatesAutoresizingMaskIntoConstraints = false
            cell.addSubview(l)
            NSLayoutConstraint.activate([l.leadingAnchor.constraint(equalTo: cell.leadingAnchor, constant: 2),
                                         l.trailingAnchor.constraint(lessThanOrEqualTo: cell.trailingAnchor, constant: -2),
                                         l.topAnchor.constraint(equalTo: cell.topAnchor, constant: 1)])
            return cell
        }
    }

    @objc func toggleItem(_ sender: NSButton) {
        guard case .item(let it) = rows[sender.tag] else { return }
        touched = true
        if sender.state == .on { selected.insert(it.path) } else { selected.remove(it.path) }
        updateSel()
    }

    // MARK: 窗口

    func buildWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1060, height: 880),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Mac 清理助手"
        window.center()
        window.setFrameAutosaveName("MacCleanupMain")

        let root = NSStackView()
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 8
        root.translatesAutoresizingMaskIntoConstraints = false
        window.contentView!.addSubview(root)
        let cv = window.contentView!
        NSLayoutConstraint.activate([
            root.leadingAnchor.constraint(equalTo: cv.leadingAnchor, constant: 16),
            root.trailingAnchor.constraint(equalTo: cv.trailingAnchor, constant: -16),
            root.topAnchor.constraint(equalTo: cv.topAnchor, constant: 14),
            root.bottomAnchor.constraint(equalTo: cv.bottomAnchor, constant: -14),
        ])

        // 标题 + 磁盘
        titleLabel.font = NSFont.boldSystemFont(ofSize: 18)
        diskLabel.font = NSFont.systemFont(ofSize: 12)
        diskLabel.textColor = .secondaryLabelColor
        pctLabel.font = NSFont.systemFont(ofSize: 12)
        pctLabel.textColor = .secondaryLabelColor
        diskBar.style = .bar
        diskBar.isIndeterminate = false
        diskBar.minValue = 0
        diskBar.maxValue = 100
        diskBar.controlSize = .small
        diskBar.translatesAutoresizingMaskIntoConstraints = false
        diskBar.widthAnchor.constraint(equalToConstant: 180).isActive = true

        let head = NSStackView(views: [titleLabel, diskBar, pctLabel, diskLabel])
        head.orientation = .horizontal
        head.spacing = 12
        root.addArrangedSubview(head)

        // 第一排按钮
        for b in [scanBtn, reportBtn, webBtn, wlBtn, histBtn] { b.bezelStyle = .rounded }
        scanBtn.keyEquivalent = "\r"
        scanBtn.target = self; scanBtn.action = #selector(doScan)
        reportBtn.target = self; reportBtn.action = #selector(doReport)
        webBtn.target = self; webBtn.action = #selector(openWeb)
        wlBtn.target = self; wlBtn.action = #selector(doWhitelist)
        histBtn.target = self; histBtn.action = #selector(doHistory)
        let row1 = NSStackView(views: [scanBtn, reportBtn, webBtn, wlBtn, histBtn])
        row1.orientation = .horizontal
        row1.spacing = 8
        root.addArrangedSubview(row1)

        statusLabel.font = NSFont.systemFont(ofSize: 12.5)
        statusLabel.textColor = .secondaryLabelColor
        root.addArrangedSubview(statusLabel)

        // 快捷选择
        selLabel.font = NSFont.systemFont(ofSize: 12)
        selLabel.textColor = .secondaryLabelColor
        let quick = ["全选": #selector(pickAll), "全不选": #selector(pickNone),
                     "只选 ≥100 MB": #selector(pickBig), "只选缓存类": #selector(pickCache),
                     "反选": #selector(pickInvert)]
        var qbtns: [NSView] = []
        for (t, act) in quick {
            let b = NSButton(title: t, target: self, action: act)
            b.bezelStyle = .rounded
            b.controlSize = .small
            qbtns.append(b)
        }
        qbtns.append(selLabel)
        let rowQuick = NSStackView(views: qbtns)
        rowQuick.orientation = .horizontal
        rowQuick.spacing = 8
        root.addArrangedSubview(rowQuick)

        // AI 行
        aiStatus.font = NSFont.systemFont(ofSize: 12)
        aiStatus.textColor = .secondaryLabelColor
        keyField.placeholderString = "TypeSafe/Jev key"
        keyField.translatesAutoresizingMaskIntoConstraints = false
        keyField.widthAnchor.constraint(equalToConstant: 200).isActive = true
        saveKeyBtn.bezelStyle = .rounded; saveKeyBtn.controlSize = .small
        saveKeyBtn.target = self; saveKeyBtn.action = #selector(saveKey)
        topField.translatesAutoresizingMaskIntoConstraints = false
        topField.widthAnchor.constraint(equalToConstant: 50).isActive = true
        onlyUnknown.state = .on
        judgeBtn.bezelStyle = .rounded; judgeBtn.controlSize = .small
        judgeBtn.target = self; judgeBtn.action = #selector(doJudge)
        let topLbl = NSTextField(labelWithString: "最大的")
        topLbl.font = NSFont.systemFont(ofSize: 12)
        let rowAI = NSStackView(views: [judgeBtn, topLbl, topField, onlyUnknown, aiStatus, keyField, saveKeyBtn])
        rowAI.orientation = .horizontal
        rowAI.spacing = 8
        root.addArrangedSubview(rowAI)

        // 清理行
        previewBtn.bezelStyle = .rounded
        previewBtn.target = self; previewBtn.action = #selector(doPreview)
        confirmField.placeholderString = "输入：确认清理"
        confirmField.translatesAutoresizingMaskIntoConstraints = false
        confirmField.widthAnchor.constraint(equalToConstant: 160).isActive = true
        applyBtn.bezelStyle = .rounded
        applyBtn.target = self; applyBtn.action = #selector(doApply)
        trashField.placeholderString = "输入：清空废纸篓"
        trashField.translatesAutoresizingMaskIntoConstraints = false
        trashField.widthAnchor.constraint(equalToConstant: 160).isActive = true
        trashBtn.bezelStyle = .rounded
        trashBtn.target = self; trashBtn.action = #selector(doTrash)
        let rowClean = NSStackView(views: [previewBtn, applyBtn, confirmField, trashBtn, trashField])
        rowClean.orientation = .horizontal
        rowClean.spacing = 8
        root.addArrangedSubview(rowClean)

        tierLabel.font = NSFont.systemFont(ofSize: 11.5)
        tierLabel.textColor = .tertiaryLabelColor
        tierLabel.lineBreakMode = .byTruncatingTail
        root.addArrangedSubview(tierLabel)

        // 表格
        let col1 = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("check")), col2 = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("size")), col3 = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("main"))
        col1.width = 30; col1.minWidth = 30; col1.maxWidth = 30
        col2.width = 92; col2.minWidth = 80
        col3.width = 860; col3.minWidth = 300
        for c in [col1, col2, col3] { tableView.addTableColumn(c) }
        tableView.dataSource = self
        tableView.delegate = self
        tableView.usesAlternatingRowBackgroundColors = true
        tableView.headerView = nil
        tableView.rowHeight = 40
        tableView.style = .inset
        let scroll = NSScrollView()
        scroll.documentView = tableView
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        scroll.translatesAutoresizingMaskIntoConstraints = false
        root.addArrangedSubview(scroll)
        scroll.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 360).isActive = true

        // 日志
        logView.isEditable = false
        logView.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        logView.textColor = .secondaryLabelColor
        let logScroll = NSScrollView()
        logScroll.documentView = logView
        logScroll.hasVerticalScroller = true
        logScroll.borderType = .bezelBorder
        logScroll.translatesAutoresizingMaskIntoConstraints = false
        root.addArrangedSubview(logScroll)
        logScroll.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        logScroll.heightAnchor.constraint(equalToConstant: 110).isActive = true

        window.makeKeyAndOrderFront(nil)
        window.orderFrontRegardless()          // 即使别的 App 挡在前面也把窗口抬出来
        NSApp.activate(ignoringOtherApps: true)
    }

    // 再点图标/点 Dock 图标时，把窗口重新带到前面（而不是"没反应"）
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { window.makeKeyAndOrderFront(nil) }
        window.orderFrontRegardless()
        NSApp.activate(ignoringOtherApps: true)
        return true
    }

    // MARK: 动作

    @objc func doScan() {
        logLine("▶️ 开始体检（只读扫描）")
        api("POST", "/api/scan", [:], timeout: 60) { [weak self] r in
            if r["ok"] as? Bool != true { self?.logLine("❌ 扫描未启动：\(r["error"] as? String ?? "")") }
        }
    }

    @objc func doReport() {
        api("POST", "/api/report", [:], timeout: 300) { [weak self] r in
            guard let self = self else { return }
            if r["ok"] as? Bool == true {
                self.logLine("✅ 报告已生成，用浏览器打开完整报告")
                NSWorkspace.shared.open(URL(string: "http://127.0.0.1:\(self.port)/report.html")!)
            } else { self.logLine("❌ 报告失败：\(r["error"] as? String ?? "")") }
        }
    }

    func setSel(_ f: (Item) -> Bool) {
        touched = true
        selected = Set(rows.compactMap { if case .item(let it) = $0, f(it) { return it.path } else { return nil } })
        tableView.reloadData()
        updateSel()
    }

    @objc func pickAll() { setSel { $0.selectable } }
    @objc func pickNone() { setSel { _ in false } }
    @objc func pickBig() { setSel { $0.selectable && $0.sizeKB >= 102400 } }
    @objc func pickCache() {
        setSel { it in
            guard it.selectable else { return false }
            let p = it.path
            return p.contains("/Caches/") || p.contains("/.cache/") || p.contains("/node_modules/")
                || p.contains("DerivedData") || p.contains("CoreSimulator") || p.hasSuffix("/Logs")
        }
    }
    @objc func pickInvert() {
        touched = true
        var inv = Set<String>()
        for r in rows { if case .item(let it) = r, it.selectable, !selected.contains(it.path) { inv.insert(it.path) } }
        selected = inv
        tableView.reloadData()
        updateSel()
    }

    @objc func saveKey() {
        let k = keyField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !k.isEmpty else { logLine("❌ key 不能为空"); return }
        api("POST", "/api/typesafe", ["key": k], timeout: 30) { [weak self] r in
            guard let self = self else { return }
            if r["ok"] as? Bool == true { self.keyField.stringValue = ""; self.aiStatus.stringValue = "✅ key 已保存"; self.logLine("✅ TypeSafe key 已保存") }
            else { self.logLine("❌ 保存失败：\(r["error"] as? String ?? "")") }
        }
    }

    @objc func doJudge() {
        let top = Int(topField.stringValue) ?? 15
        logLine("▶️ 运行 Jev AI 判断（最大的 \(top) 个\(onlyUnknown.state == .on ? "，仅未知" : "")）")
        statusLabel.stringValue = "AI 判断中…（每个目录 3 个问题、5 个一批）"
        api("POST", "/api/judge", ["top": top, "only_unknown": onlyUnknown.state == .on], timeout: 1200) { [weak self] r in
            guard let self = self else { return }
            if r["ok"] as? Bool == true {
                self.touched = false
                self.logLine("✅ AI 判断完成（清单已加 🤖 标记）")
                if let o = r["output"] as? String { self.logLine(o.suffix(600).trimmingCharacters(in: .whitespacesAndNewlines)) }
                self.lastKey = ""
            } else {
                self.logLine("❌ AI 判断失败：\(r["error"] as? String ?? "")")
                if let o = r["output"] as? String { self.logLine(o.suffix(400).trimmingCharacters(in: .whitespacesAndNewlines)) }
            }
        }
    }

    @objc func doPreview() {
        let paths = Array(selected)
        guard !paths.isEmpty else { logLine("❌ 没勾选任何项"); return }
        logLine("▶️ 预览选中的 \(paths.count) 项（什么都不删）")
        api("POST", "/api/clean", ["mode": "preview", "paths": paths], timeout: 900) { [weak self] r in
            guard let self = self else { return }
            if let o = r["output"] as? String { self.logLine(o) }
            self.logLine(r["ok"] as? Bool == true ? "✅ 预览完成（什么都没删）" : "❌ 预览失败：\(r["error"] as? String ?? "")")
        }
    }

    @objc func doApply() {
        let paths = Array(selected)
        guard !paths.isEmpty else { logLine("❌ 没勾选任何项"); return }
        guard confirmField.stringValue.trimmingCharacters(in: .whitespaces) == "确认清理" else {
            logLine("❌ 请先在输入框里输入：确认清理"); return
        }
        let a = NSAlert()
        a.messageText = "执行清理？"
        a.informativeText = "将把勾选的 \(paths.count) 项移到废纸篓（可逆，之后可从废纸篓恢复）。"
        a.addButton(withTitle: "执行")
        a.addButton(withTitle: "取消")
        guard a.runModal() == .alertFirstButtonReturn else { logLine("已取消"); return }
        logLine("▶️ 执行清理：\(paths.count) 项 → 废纸篓")
        api("POST", "/api/clean", ["mode": "apply", "confirm": "确认清理", "paths": paths], timeout: 1800) { [weak self] r in
            guard let self = self else { return }
            if let o = r["output"] as? String { self.logLine(o) }
            let b = r["free_before_gb"] as? Double ?? 0, af = r["free_after_gb"] as? Double ?? 0
            self.logLine(String(format: "可用空间 %.1f GB → %.1f GB（清空废纸篓后才真正释放）", b, af))
            self.confirmField.stringValue = ""
            self.touched = false
        }
    }

    @objc func doTrash() {
        guard trashField.stringValue.trimmingCharacters(in: .whitespaces) == "清空废纸篓" else {
            logLine("❌ 请先在输入框里输入：清空废纸篓"); return
        }
        let a = NSAlert()
        a.messageText = "清空废纸篓？"
        a.informativeText = "这一步不可逆：废纸篓里的文件会被永久删除。"
        a.addButton(withTitle: "清空")
        a.addButton(withTitle: "取消")
        guard a.runModal() == .alertFirstButtonReturn else { logLine("已取消"); return }
        logLine("▶️ 清空废纸篓…")
        api("POST", "/api/trash", ["confirm": "清空废纸篓"], timeout: 600) { [weak self] r in
            guard let self = self else { return }
            if r["ok"] as? Bool == true { self.logLine("✅ 废纸篓已清空，当前可用 \(r["free_gb"] as? Double ?? 0) GB") }
            else { self.logLine("❌ 清空失败：\(r["error"] as? String ?? "")") }
            self.trashField.stringValue = ""
        }
    }

    @objc func doWhitelist() {
        api("GET", "/api/whitelist", nil, timeout: 60) { [weak self] r in
            self?.logLine("—— 保护名单 ——\n\((r["output"] as? String) ?? "(空)")")
        }
    }

    @objc func doHistory() {
        api("GET", "/api/history", nil, timeout: 60) { [weak self] r in
            self?.logLine("—— 操作历史 ——\n\((r["output"] as? String) ?? "(空)")")
        }
    }
}

// MARK: - 入口

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
