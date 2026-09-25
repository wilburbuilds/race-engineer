import Cocoa
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var backend: Process?
    var input: Pipe?
    var outputBuffer = Data()
    var quitting = false
    var ready = false
    var errorLog: FileHandle?
    let dataDir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Documents/Race Engineer", isDirectory: true)

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let item = NSMenuItem(); menu.addItem(item)
        let appMenu = NSMenu(); item.submenu = appMenu
        appMenu.addItem(withTitle: "About Race Engineer", action: #selector(about), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Open Recordings", action: #selector(recordings), keyEquivalent: "o")
        appMenu.addItem(withTitle: "Reload Dashboard", action: #selector(reloadDashboard), keyEquivalent: "r")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit and Save", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        NSApp.mainMenu = menu
        for i in appMenu.items where i.action != #selector(NSApplication.terminate(_:)) { i.target = self }
        let editItem = NSMenuItem(); menu.addItem(editItem); let edit = NSMenu(title: "Edit"); editItem.submenu = edit
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 850), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Race Engineer"
        window.minSize = NSSize(width: 850, height: 600)
        window.setFrameAutosaveName("RaceEngineerMain")
        window.center()
        web = WKWebView(); web.navigationDelegate = self
        window.contentView = web
        web.loadHTMLString("<body style='background:#0b0e14;color:#edeff3;font:20px -apple-system;padding:48px'><h1>Race Engineer</h1><p>Starting the recorder…</p></body>", baseURL: nil)
        window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        do { try startBackend() } catch { showError(error.localizedDescription) }
    }
    func startBackend() throws {
        try FileManager.default.createDirectory(at: dataDir.appendingPathComponent("sessions"), withIntermediateDirectories: true)
        let resources = Bundle.main.resourceURL!
        let logs = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/Race Engineer")
        try FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        let log = logs.appendingPathComponent("app.log")
        FileManager.default.createFile(atPath: log.path, contents: nil)
        errorLog = try FileHandle(forWritingTo: log)
        let p = Process(); backend = p
        p.executableURL = resources.appendingPathComponent("python/bin/python3.11")
        p.arguments = ["-I", "-B", "-u", resources.appendingPathComponent("backend/bootstrap.py").path, "--data-dir", dataDir.path]
        p.currentDirectoryURL = dataDir
        var env = ProcessInfo.processInfo.environment
        env.removeValue(forKey: "PYTHONHOME"); env.removeValue(forKey: "PYTHONPATH")
        env["PYTHONDONTWRITEBYTECODE"] = "1"; p.environment = env
        let stdinPipe = Pipe(); input = stdinPipe; p.standardInput = stdinPipe
        let stdoutPipe = Pipe(); p.standardOutput = stdoutPipe; p.standardError = errorLog
        stdoutPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let bytes = handle.availableData
            if bytes.isEmpty { handle.readabilityHandler = nil; return }
            DispatchQueue.main.async { self?.received(bytes) }
        }
        p.terminationHandler = { [weak self] process in
            DispatchQueue.main.async {
                guard let self = self else { return }
                if self.quitting {
                    if process.terminationStatus == 0 { NSApp.reply(toApplicationShouldTerminate: true) }
                    else { self.quitting = false; NSApp.reply(toApplicationShouldTerminate: false); self.showError("The recorder could not finish saving. Check ~/Library/Logs/Race Engineer/app.log before quitting.") }
                } else { self.showError("The recorder stopped. Check ~/Library/Logs/Race Engineer/app.log for details, then reopen the app.") }
            }
        }
        try p.run()
        DispatchQueue.main.asyncAfter(deadline: .now() + 15) { [weak self] in
            guard let self = self, !self.ready, !self.quitting, self.backend?.isRunning == true else { return }
            self.showError("The recorder is taking longer than expected to start. Check ~/Library/Logs/Race Engineer/app.log.")
        }
    }
    func received(_ bytes: Data) {
        outputBuffer.append(bytes)
        while let newline = outputBuffer.firstIndex(of: 10) {
            let line = outputBuffer.prefix(upTo: newline); outputBuffer.removeSubrange(...newline)
            if let object = try? JSONSerialization.jsonObject(with: line) as? [String: String], let value = object["url"], let url = URL(string: value) {
                ready = true; web.load(URLRequest(url: url))
            }
        }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let p = backend, p.isRunning else { return .terminateNow }
        if !quitting {
            quitting = true; window.title = "Race Engineer — Saving recording…"
            try? input?.fileHandleForWriting.write(contentsOf: Data("quit\n".utf8))
            try? input?.fileHandleForWriting.close()
        }
        return .terminateLater
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil); return true
    }
    @objc func recordings() { NSWorkspace.shared.open(dataDir.appendingPathComponent("sessions")) }
    @objc func reloadDashboard() { web.reload() }
    @objc func about() {
        let alert = NSAlert(); alert.messageText = "Race Engineer"; alert.informativeText = "Live F1 telemetry, driver comparisons, and automatic session recording.\n\nClosing the window saves and quits. Recordings are in Documents/Race Engineer/sessions.\n\nVoice is off in this edition."; alert.runModal()
    }
    func showError(_ message: String) { let alert = NSAlert(); alert.messageText = "Race Engineer"; alert.informativeText = message; alert.alertStyle = .warning; alert.runModal() }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.host == "localhost" || url.host == "127.0.0.1" || url.scheme == "about" { decisionHandler(.allow) }
        else { decisionHandler(.cancel); if ["http", "https"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) } }
    }
}
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
