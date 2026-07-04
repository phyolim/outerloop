// outerloop control app.
// A background (LSUIElement) status item. Clicking it opens a persistent window with
// two tabs: Tasks (live fleet view — terminate + watch CLI output for THIS machine's
// tasks) and Settings (hub URL / identity / relay). It holds no state the DB/launchd
// doesn't already own — it shells `launchctl`, tails claude transcripts, and talks to
// the hub's JSON API.
import Cocoa

// --- config baked by the installer -----------------------------------------
let ENV_PATH = "/usr/local/outerloop/deploy.env"

func loadEnv(_ path: String) -> [String: String] {
    guard let text = try? String(contentsOfFile: path, encoding: .utf8) else { return [:] }
    var out: [String: String] = [:]
    for line in text.split(separator: "\n") {
        let s = line.trimmingCharacters(in: .whitespaces)
        if s.isEmpty || s.hasPrefix("#") { continue }
        guard let eq = s.firstIndex(of: "=") else { continue }
        out[String(s[..<eq])] = String(s[s.index(after: eq)...])
    }
    return out
}

let env = loadEnv(ENV_PATH)
let role = env["ROLE"] ?? "worker"
let dashURL = env["DASH_URL"] ?? "http://127.0.0.1:8765"
let uid = getuid()
let home = NSHomeDirectory()
let dataDir = "\(home)/Library/Application Support/outerloop"
let killFile = "\(dataDir)/KILL"
let settingsFile = "\(dataDir)/settings.json"  // runtime config the worker reads (hub URL)
let workerPlist = "\(home)/Library/LaunchAgents/com.outerloop.worker.plist"

// The worker's identity (device name + token) is baked into its launchd plist by the
// installer. Returns the plist's EnvironmentVariables, or empty if unreadable.
func workerEnv() -> [String: String] {
    guard let d = NSDictionary(contentsOfFile: workerPlist),
          let e = d["EnvironmentVariables"] as? [String: String] else { return [:] }
    return e
}
func myDevice() -> String { workerEnv()["INBOX_DEVICE"] ?? "" }
func myToken() -> String { workerEnv()["INBOX_DEVICE_TOKEN"] ?? "" }

// launchd agents this machine owns, by role. Label == plist basename.
let labels: [String] = role == "hub"
    ? ["com.outerloop.hub", "com.outerloop.tunnel", "com.outerloop.worker"]
    : ["com.outerloop.worker"]

// --- shelling out -----------------------------------------------------------
@discardableResult
func run(_ launchPath: String, _ args: [String]) -> (code: Int32, out: String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: launchPath)
    p.arguments = args
    let pipe = Pipe()
    p.standardOutput = pipe
    p.standardError = pipe
    do { try p.run() } catch { return (-1, "") }
    p.waitUntilExit()
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    return (p.terminationStatus, String(data: data, encoding: .utf8) ?? "")
}

func isRunning(_ label: String) -> Bool {
    let r = run("/bin/launchctl", ["print", "gui/\(uid)/\(label)"])
    return r.code == 0 && r.out.contains("state = running")
}
func startAgent(_ label: String) {
    let plist = "\(home)/Library/LaunchAgents/\(label).plist"
    run("/bin/launchctl", ["bootstrap", "gui/\(uid)", plist])   // no-op if already loaded
    run("/bin/launchctl", ["kickstart", "-k", "gui/\(uid)/\(label)"])
}
func stopAgent(_ label: String) { run("/bin/launchctl", ["bootout", "gui/\(uid)/\(label)"]) }

var killEngaged: Bool { FileManager.default.fileExists(atPath: killFile) }
func toggleKill() {
    let fm = FileManager.default
    if killEngaged { try? fm.removeItem(atPath: killFile) } else { fm.createFile(atPath: killFile, contents: Data()) }
}

// --- runtime config (settings.json), so hub URL / relay change with no rebuild ---
func readSetting(_ key: String) -> String? {
    guard let data = FileManager.default.contents(atPath: settingsFile),
          let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let v = j[key] as? String, !v.isEmpty else { return nil }
    return v
}
func readHubURL() -> String? { readSetting("hub_url") }
func writeSettings(_ kv: [String: String]) {
    var obj: [String: Any] = [:]
    if let data = FileManager.default.contents(atPath: settingsFile),
       let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any] { obj = j }
    for (k, v) in kv { obj[k] = v }
    try? FileManager.default.createDirectory(atPath: dataDir, withIntermediateDirectories: true)
    if let out = try? JSONSerialization.data(withJSONObject: obj, options: [.prettyPrinted]) {
        try? out.write(to: URL(fileURLWithPath: settingsFile))
    }
}
func writeHubURL(_ url: String) { writeSettings(["hub_url": url]) }

// hub URL for links/API: the saved local setting, else the plist seed, else loopback.
func hubBase() -> String {
    if let u = readHubURL() { return u }
    let e = workerEnv()["INBOX_HUB"] ?? ""
    return e.isEmpty ? dashURL : e
}

// Rewrite the worker plist's EnvironmentVariables (name + token live here, not settings.json).
func setWorkerEnv(_ updates: [String: String]) -> Bool {
    guard let data = FileManager.default.contents(atPath: workerPlist),
          var plist = (try? PropertyListSerialization.propertyList(from: data, options: [], format: nil))
            as? [String: Any] else { return false }
    var env = (plist["EnvironmentVariables"] as? [String: Any]) ?? [:]
    for (k, v) in updates { env[k] = v }
    plist["EnvironmentVariables"] = env
    guard let out = try? PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
        else { return false }
    return (try? out.write(to: URL(fileURLWithPath: workerPlist))) != nil
}

// --- hub JSON API (bearer = this machine's device token) --------------------
func apiGET(_ path: String, _ done: @escaping ([String: Any]?) -> Void) {
    guard let url = URL(string: hubBase() + path) else { done(nil); return }
    var req = URLRequest(url: url); req.timeoutInterval = 5
    let tok = myToken(); if !tok.isEmpty { req.setValue("Bearer \(tok)", forHTTPHeaderField: "Authorization") }
    URLSession.shared.dataTask(with: req) { data, _, _ in
        let j = (data.flatMap { try? JSONSerialization.jsonObject(with: $0) }) as? [String: Any]
        DispatchQueue.main.async { done(j) }
    }.resume()
}
func apiPOST(_ path: String, _ done: @escaping (Bool) -> Void) {
    guard let url = URL(string: hubBase() + path) else { done(false); return }
    var req = URLRequest(url: url); req.httpMethod = "POST"; req.timeoutInterval = 5
    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
    req.httpBody = Data("{}".utf8)
    let tok = myToken(); if !tok.isEmpty { req.setValue("Bearer \(tok)", forHTTPHeaderField: "Authorization") }
    URLSession.shared.dataTask(with: req) { _, resp, _ in
        let ok = (resp as? HTTPURLResponse).map { (200..<300).contains($0.statusCode) } ?? false
        DispatchQueue.main.async { done(ok) }
    }.resume()
}

// Native prompt for the hub URL. Returns the trimmed entry, or nil on cancel/empty.
func promptHubURL(_ prefill: String) -> String? {
    let a = NSAlert()
    a.messageText = "Orchestrator hub URL"
    a.informativeText = "Where this worker reaches the hub, e.g. http://mini.local:8765"
    a.addButton(withTitle: "Save"); a.addButton(withTitle: "Cancel")
    let f = NSTextField(frame: NSRect(x: 0, y: 0, width: 300, height: 24))
    f.stringValue = prefill
    f.placeholderString = "http://mini.local:8765"
    a.accessoryView = f
    NSApp.activate(ignoringOtherApps: true)
    guard a.runModal() == .alertFirstButtonReturn else { return nil }
    let v = f.stringValue.trimmingCharacters(in: .whitespaces)
    return v.isEmpty ? nil : v
}

// =====================================================================
// Tasks tab: live fleet task list. Terminate + CLI output light up only
// for tasks running on THIS machine (the claude process + transcript are local).
// =====================================================================
final class TasksPane: NSObject, NSTableViewDataSource, NSTableViewDelegate {
    let view = NSView(frame: NSRect(x: 0, y: 0, width: 744, height: 488))
    let table = NSTableView()
    let output = NSTextView()
    let terminateBtn = NSButton(title: "Terminate", target: nil, action: nil)
    let outputLabel = NSTextField(labelWithString: "Output")
    var tasks: [[String: Any]] = []
    var pollTimer: Timer?
    var tailTimer: Timer?
    var tailedSession: String?
    var tailedPath: String?

    override init() { super.init(); build() }

    func build() {
        let cols: [(String, String, CGFloat)] = [
            ("id", "#", 40), ("title", "Title", 250), ("type", "Type", 66),
            ("status", "Status", 66), ("sub_stage", "Stage", 96), ("device", "Device", 96)]
        for (id, title, w) in cols {
            let c = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(id))
            c.title = title; c.width = w
            table.addTableColumn(c)
        }
        table.dataSource = self; table.delegate = self
        table.usesAlternatingRowBackgroundColors = true
        table.allowsMultipleSelection = false
        let ts = NSScrollView(frame: NSRect(x: 8, y: 262, width: 728, height: 218))
        ts.hasVerticalScroller = true; ts.borderType = .bezelBorder; ts.documentView = table
        view.addSubview(ts)

        terminateBtn.target = self; terminateBtn.action = #selector(confirmTerminate)
        terminateBtn.bezelStyle = .rounded
        terminateBtn.frame = NSRect(x: 8, y: 226, width: 110, height: 28)
        terminateBtn.isEnabled = false
        view.addSubview(terminateBtn)
        let refresh = NSButton(title: "Refresh", target: self, action: #selector(reload))
        refresh.bezelStyle = .rounded; refresh.frame = NSRect(x: 126, y: 226, width: 90, height: 28)
        view.addSubview(refresh)

        outputLabel.frame = NSRect(x: 8, y: 200, width: 728, height: 18)
        outputLabel.textColor = .secondaryLabelColor
        view.addSubview(outputLabel)

        let os = NSScrollView(frame: NSRect(x: 8, y: 8, width: 728, height: 186))
        os.hasVerticalScroller = true; os.borderType = .bezelBorder
        output.isEditable = false
        output.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        output.isVerticallyResizable = true; output.isHorizontallyResizable = false
        output.autoresizingMask = [.width]
        output.minSize = NSSize(width: 0, height: 0)
        output.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        output.textContainer?.containerSize = NSSize(width: 728, height: CGFloat.greatestFiniteMagnitude)
        output.textContainer?.widthTracksTextView = true
        os.documentView = output
        view.addSubview(os)
    }

    func start() {
        pollTimer?.invalidate()            // idempotent: reopening must not stack timers
        reload()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { _ in self.reload() }
    }
    func stop() {
        pollTimer?.invalidate(); pollTimer = nil
        tailTimer?.invalidate(); tailTimer = nil; tailedSession = nil; tailedPath = nil
    }

    func selectedId() -> Int? {
        let r = table.selectedRow
        return (r >= 0 && r < tasks.count) ? (tasks[r]["id"] as? Int) : nil
    }

    @objc func reload() {
        apiGET("/api/tasks") { j in
            guard let arr = j?["tasks"] as? [[String: Any]] else {
                self.tasks = []; self.table.reloadData()
                self.outputLabel.stringValue = "Output — hub unreachable (\(hubBase()))"
                self.terminateBtn.isEnabled = false; return
            }
            let keep = self.selectedId()
            self.tasks = arr
            self.table.reloadData()
            if let keep = keep, let i = arr.firstIndex(where: { ($0["id"] as? Int) == keep }) {
                self.table.selectRowIndexes([i], byExtendingSelection: false)
            }
            self.updateForSelection()
        }
    }

    // --- NSTableView view-based data source ---
    func numberOfRows(in tableView: NSTableView) -> Int { tasks.count }
    func cellText(_ row: Int, _ key: String) -> String {
        guard row < tasks.count else { return "" }
        let task = tasks[row]
        switch key {
        case "id": return (task["id"] as? Int).map(String.init) ?? ""
        case "device": return (task["device"] as? String) ?? "—"
        default: return (task[key] as? String) ?? ""
        }
    }
    func tableView(_ t: NSTableView, viewFor col: NSTableColumn?, row: Int) -> NSView? {
        guard let col = col else { return nil }
        let field = (t.makeView(withIdentifier: col.identifier, owner: self) as? NSTextField)
            ?? {
                let f = NSTextField(labelWithString: "")
                f.identifier = col.identifier
                f.lineBreakMode = .byTruncatingTail
                return f
            }()
        field.stringValue = cellText(row, col.identifier.rawValue)
        return field
    }
    func tableViewSelectionDidChange(_ notification: Notification) { updateForSelection() }

    func isLocal(_ task: [String: Any]) -> Bool {
        let dev = task["device"] as? String
        return dev != nil && dev == myDevice() && !myDevice().isEmpty
    }

    func updateForSelection() {
        let row = table.selectedRow
        guard row >= 0, row < tasks.count else {
            terminateBtn.isEnabled = false; outputLabel.stringValue = "Output"; setTail(nil); return
        }
        let task = tasks[row]
        let running = (task["running"] as? Bool) ?? ((task["device"] as? String) != nil)
        let local = isLocal(task)
        terminateBtn.isEnabled = local && running
        let session = task["session_id"] as? String
        if local, let s = session {
            outputLabel.stringValue = "Output — session \(s.prefix(8)) (live, this machine)"
            setTail(s)
        } else {
            setTail(nil); output.string = ""
            if let dev = task["device"] as? String {
                outputLabel.stringValue = "Output — running on \(dev); live output is only on that machine"
            } else {
                outputLabel.stringValue = "Output — not running"
            }
        }
    }

    // --- terminate: hub parks the ticket, then SIGKILL the local claude process ---
    @objc func confirmTerminate() {
        let row = table.selectedRow
        guard row >= 0, row < tasks.count else { return }
        let task = tasks[row]
        guard let id = task["id"] as? Int, isLocal(task) else { return }
        let a = NSAlert()
        a.messageText = "Terminate task #\(id)?"
        a.informativeText = "Kills the running agent on this machine and parks the ticket. "
            + "You can revive it later from the dashboard's Parked page."
        a.addButton(withTitle: "Terminate"); a.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        guard a.runModal() == .alertFirstButtonReturn else { return }
        let session = task["session_id"] as? String
        apiPOST("/api/tasks/\(id)/terminate") { _ in
            // ponytail: match the claude child by its session-id in argv. Unique uuid, so a
            // broad pkill -f is safe; tighten to pgid if a collision ever shows up.
            if let s = session, !s.isEmpty { run("/usr/bin/pkill", ["-9", "-f", s]) }
            self.reload()
        }
    }

    // --- CLI output: tail claude's own session transcript (jsonl) ---
    func setTail(_ session: String?) {
        if session == tailedSession { return }
        tailTimer?.invalidate(); tailTimer = nil
        tailedSession = session; tailedPath = nil
        guard let s = session else { return }
        renderTail(s)
        tailTimer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { _ in self.renderTail(s) }
    }
    func transcriptPath(_ session: String) -> String? {
        let r = run("/usr/bin/find", ["\(home)/.claude/projects", "-name", "\(session).jsonl"])
        let p = r.out.split(separator: "\n").first.map { String($0).trimmingCharacters(in: .whitespaces) }
        return (p?.isEmpty == false) ? p : nil
    }
    func renderTail(_ session: String) {
        if tailedPath == nil { tailedPath = transcriptPath(session) }
        guard let path = tailedPath, let text = try? String(contentsOfFile: path, encoding: .utf8) else {
            if output.string.isEmpty { output.string = "(waiting for transcript…)" }
            return
        }
        var lines: [String] = []
        for raw in text.split(separator: "\n") {
            guard let d = raw.data(using: .utf8),
                  let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
                  let msg = o["message"] as? [String: Any] else { continue }
            let who = ((o["type"] as? String) == "user") ? "»" : "assistant"
            if let s = msg["content"] as? String {
                lines.append("\(who == "»" ? "»" : "▸") \(s)")
            } else if let arr = msg["content"] as? [[String: Any]] {
                for part in arr {
                    switch part["type"] as? String {
                    case "text": if let t = part["text"] as? String { lines.append("▸ \(t)") }
                    case "tool_use":
                        if let n = part["name"] as? String { lines.append("  ⚙ \(n)") }
                    default: break
                    }
                }
            }
        }
        let text2 = lines.suffix(500).joined(separator: "\n")
        if output.string != text2 {
            output.string = text2
            output.scrollToEndOfDocument(nil)
        }
    }
}

// =====================================================================
// Settings tab: hub URL / identity (worker) or relay + loopback identity (hub).
// Same content the old Settings window had, reparented into a tab.
// =====================================================================
final class SettingsPane: NSObject {
    let hubField = NSTextField()
    let deviceField = NSTextField()
    let tokenField = NSTextField()
    let capsValue = NSTextField(labelWithString: "")
    let vpsField = NSTextField()
    let userField = NSTextField()
    let keyField = NSTextField()

    func label(_ s: String, _ f: NSRect, bold: Bool = false) -> NSTextField {
        let t = NSTextField(labelWithString: s)
        t.frame = f
        if bold { t.font = NSFont.boldSystemFont(ofSize: 13) }
        return t
    }
    func button(_ title: String, _ f: NSRect, _ sel: Selector) -> NSButton {
        let b = NSButton(title: title, target: self, action: sel)
        b.frame = f; b.bezelStyle = .rounded
        return b
    }

    func build(into c: NSView) {
        if role == "hub" { buildHub(c) } else { buildWorker(c) }
        present()
    }

    func buildHub(_ c: NSView) {
        c.addSubview(label("Relay — remote access (reverse SSH tunnel)",
                           NSRect(x: 20, y: 452, width: 440, height: 22), bold: true))
        c.addSubview(label("VPS host:", NSRect(x: 20, y: 418, width: 95, height: 20)))
        vpsField.frame = NSRect(x: 120, y: 415, width: 340, height: 24)
        vpsField.placeholderString = "1.2.3.4.sslip.io  (empty = tunnel off)"
        c.addSubview(vpsField)
        c.addSubview(label("Tunnel user:", NSRect(x: 20, y: 384, width: 95, height: 20)))
        userField.frame = NSRect(x: 120, y: 381, width: 340, height: 24)
        userField.placeholderString = "tunnel"
        c.addSubview(userField)
        c.addSubview(label("SSH key:", NSRect(x: 20, y: 350, width: 95, height: 20)))
        keyField.frame = NSRect(x: 120, y: 347, width: 340, height: 24)
        keyField.placeholderString = "~/.ssh/id_ed25519  (path to the private key)"
        c.addSubview(keyField)
        let rnote = label("Generate the keypair and authorize its .pub on the VPS "
                          + "(deploy/relay/vps-setup.sh) first — this only sets where + which key.",
                          NSRect(x: 20, y: 314, width: 440, height: 30))
        rnote.textColor = .secondaryLabelColor; rnote.font = NSFont.systemFont(ofSize: 11)
        rnote.maximumNumberOfLines = 2; rnote.lineBreakMode = .byWordWrapping
        c.addSubview(rnote)
        c.addSubview(button("Save & reconnect", NSRect(x: 20, y: 278, width: 200, height: 28), #selector(saveTunnel)))

        c.addSubview(label("This machine's worker", NSRect(x: 20, y: 228, width: 440, height: 22), bold: true))
        c.addSubview(label("Device:", NSRect(x: 20, y: 194, width: 95, height: 20)))
        deviceField.frame = NSRect(x: 120, y: 191, width: 340, height: 24)
        deviceField.placeholderString = "device name (e.g. hub)"
        c.addSubview(deviceField)
        c.addSubview(label("Token:", NSRect(x: 20, y: 160, width: 95, height: 20)))
        tokenField.frame = NSRect(x: 120, y: 157, width: 340, height: 24)
        tokenField.placeholderString = "paste the token from Fleet"
        c.addSubview(tokenField)
        let wnote = label("The hub runs its own loopback worker. Pair it like any device: "
                          + "Fleet → \"Pair a new device\", then paste name + token here.",
                          NSRect(x: 20, y: 124, width: 440, height: 30))
        wnote.textColor = .secondaryLabelColor; wnote.font = NSFont.systemFont(ofSize: 11)
        wnote.maximumNumberOfLines = 2; wnote.lineBreakMode = .byWordWrapping
        c.addSubview(wnote)
        c.addSubview(button("Save device & token", NSRect(x: 20, y: 84, width: 200, height: 28), #selector(saveIdentity)))
        c.addSubview(button("Open Dashboard", NSRect(x: 260, y: 84, width: 200, height: 28), #selector(openDashboard)))
    }

    func buildWorker(_ c: NSView) {
        c.addSubview(label("Worker settings", NSRect(x: 20, y: 452, width: 420, height: 22), bold: true))
        c.addSubview(label("Hub URL:", NSRect(x: 20, y: 416, width: 85, height: 20)))
        hubField.frame = NSRect(x: 110, y: 413, width: 250, height: 24)
        hubField.placeholderString = "http://mini.local:8765"
        c.addSubview(hubField)
        c.addSubview(button("Save", NSRect(x: 368, y: 411, width: 72, height: 28), #selector(saveHub)))

        c.addSubview(label("Device:", NSRect(x: 20, y: 374, width: 85, height: 20)))
        deviceField.frame = NSRect(x: 110, y: 371, width: 330, height: 24)
        deviceField.placeholderString = "device name (e.g. mbp)"
        c.addSubview(deviceField)
        c.addSubview(label("Token:", NSRect(x: 20, y: 340, width: 85, height: 20)))
        tokenField.frame = NSRect(x: 110, y: 337, width: 330, height: 24)
        tokenField.placeholderString = "paste the token from Fleet"
        c.addSubview(tokenField)
        c.addSubview(button("Save device & token", NSRect(x: 110, y: 301, width: 200, height: 28), #selector(saveIdentity)))

        c.addSubview(label("Capabilities:", NSRect(x: 20, y: 268, width: 90, height: 20)))
        capsValue.frame = NSRect(x: 110, y: 268, width: 330, height: 20)
        c.addSubview(capsValue)

        let note = label("Get Device + Token from the hub's Fleet page → \"Pair a new device\". "
                         + "Capabilities are hub-owned — set them in Fleet.",
                         NSRect(x: 20, y: 228, width: 420, height: 34))
        note.textColor = .secondaryLabelColor; note.font = NSFont.systemFont(ofSize: 11)
        note.maximumNumberOfLines = 2; note.lineBreakMode = .byWordWrapping
        c.addSubview(note)

        c.addSubview(button("Edit in Fleet →", NSRect(x: 20, y: 186, width: 200, height: 28), #selector(openFleet)))
        c.addSubview(button("Open Dashboard", NSRect(x: 240, y: 186, width: 200, height: 28), #selector(openDashboard)))
    }

    func present() {
        if role == "hub" {
            vpsField.stringValue = readSetting("vps_host") ?? (env["VPS_HOST"] ?? "")
            userField.stringValue = readSetting("tunnel_user") ?? (env["TUNNEL_USER"] ?? "tunnel")
            keyField.stringValue = readSetting("ssh_key") ?? (env["SSH_KEY"] ?? "")
            let e = workerEnv()
            deviceField.stringValue = e["INBOX_DEVICE"] ?? ""
            tokenField.stringValue = e["INBOX_DEVICE_TOKEN"] ?? ""
        } else {
            let e = workerEnv()
            hubField.stringValue = readHubURL() ?? (e["INBOX_HUB"] ?? "")
            deviceField.stringValue = e["INBOX_DEVICE"] ?? ""
            tokenField.stringValue = e["INBOX_DEVICE_TOKEN"] ?? ""
            capsValue.stringValue = "loading…"
            fetchCaps()
        }
    }

    func fetchCaps() {
        let name = myDevice()
        guard !name.isEmpty else { capsValue.stringValue = "(pair this device first)"; return }
        apiGET("/api/fleet") { j in
            var text = "(open Fleet to view)"
            if let devs = j?["devices"] as? [[String: Any]] {
                for d in devs where (d["name"] as? String) == name {
                    let caps = (d["capabilities"] as? [String]) ?? []
                    text = caps.isEmpty ? "(none set — click Edit in Fleet)" : caps.joined(separator: ", ")
                }
            }
            self.capsValue.stringValue = text
        }
    }

    @objc func saveHub() {
        let v = hubField.stringValue.trimmingCharacters(in: .whitespaces)
        guard !v.isEmpty else { return }
        writeHubURL(v)
        run("/bin/launchctl", ["kickstart", "-k", "gui/\(uid)/com.outerloop.worker"])
        fetchCaps()
    }
    @objc func saveIdentity() {
        let name = deviceField.stringValue.trimmingCharacters(in: .whitespaces)
        let tok = tokenField.stringValue.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty, !tok.isEmpty else { return }
        guard setWorkerEnv(["INBOX_DEVICE": name, "INBOX_DEVICE_TOKEN": tok]) else {
            capsValue.stringValue = "(couldn't write worker config)"; return
        }
        run("/bin/launchctl", ["bootout", "gui/\(uid)/com.outerloop.worker"])
        run("/bin/launchctl", ["bootstrap", "gui/\(uid)", workerPlist])
        capsValue.stringValue = "loading…"
        fetchCaps()
    }
    @objc func saveTunnel() {
        writeSettings(["vps_host": vpsField.stringValue.trimmingCharacters(in: .whitespaces),
                       "tunnel_user": userField.stringValue.trimmingCharacters(in: .whitespaces),
                       "ssh_key": keyField.stringValue.trimmingCharacters(in: .whitespaces)])
        run("/bin/launchctl", ["kickstart", "-k", "gui/\(uid)/com.outerloop.tunnel"])
    }
    @objc func openFleet() { if let u = URL(string: hubBase() + "/fleet") { NSWorkspace.shared.open(u) } }
    @objc func openDashboard() { if let u = URL(string: hubBase()) { NSWorkspace.shared.open(u) } }
}

// =====================================================================
// Setup tab: live prerequisite checklist. Mirrors deploy/mac/scripts/preflight.sh
// (the authoritative real-mode gate: python3, git+identity, gh+auth, claude+creds)
// but as a GUI so the operator sees green/red without opening a terminal, and can
// fix the two "identity" gaps in place: set git user.name/email, and launch the
// gh/claude interactive logins in Terminal. Probes run off the main thread through a
// LOGIN shell (zsh -lc) so PATH matches the user's interactive env — where Homebrew
// and the self-installed claude live — which the GUI's own PATH doesn't include.
// AWS is intentionally absent: the orchestrator never calls it (config.py shells only
// git/gh/claude); "aws" appears only in the optional VPS-relay docs.
// =====================================================================
final class SetupPane: NSObject {
    let view = NSView(frame: NSRect(x: 0, y: 0, width: 744, height: 488))
    let gitName = NSTextField()
    let gitEmail = NSTextField()
    var status: [String: NSTextField] = [:]
    var detail: [String: NSTextField] = [:]

    override init() { super.init(); build() }

    // A login shell so gh/claude/git resolve exactly as they do in the user's terminal.
    func sh(_ cmd: String) -> (code: Int32, out: String) { run("/bin/zsh", ["-lc", cmd]) }
    func firstLine(_ s: String) -> String {
        (s.split(separator: "\n").first.map(String.init) ?? "").trimmingCharacters(in: .whitespaces)
    }
    func which(_ tool: String) -> String {
        let r = sh("command -v \(tool)"); return r.code == 0 ? firstLine(r.out) : ""
    }

    func build() {
        let t = NSTextField(labelWithString: "Prerequisites")
        t.font = NSFont.boldSystemFont(ofSize: 13); t.frame = NSRect(x: 20, y: 458, width: 300, height: 22)
        view.addSubview(t)

        statusRow(430, "python", "Python ≥ 3.9")
        statusRow(404, "git", "git")
        // git identity: editable in place (the one prereq the operator sets, not installs).
        let dot = statusDot("gitid", NSRect(x: 20, y: 370, width: 18, height: 20))
        view.addSubview(dot)
        let gl = NSTextField(labelWithString: "Git identity"); gl.font = NSFont.systemFont(ofSize: 13, weight: .medium)
        gl.frame = NSRect(x: 44, y: 370, width: 96, height: 20); view.addSubview(gl)
        gitName.frame = NSRect(x: 150, y: 367, width: 150, height: 24); gitName.placeholderString = "user.name"
        view.addSubview(gitName)
        gitEmail.frame = NSRect(x: 308, y: 367, width: 200, height: 24); gitEmail.placeholderString = "user.email"
        view.addSubview(gitEmail)
        let save = NSButton(title: "Save", target: self, action: #selector(saveGitId))
        save.bezelStyle = .rounded; save.frame = NSRect(x: 516, y: 365, width: 70, height: 28); view.addSubview(save)

        statusRow(334, "gh", "GitHub CLI (gh)")
        actionRow(308, "ghauth", "GitHub login", "Log in…", #selector(loginGh))
        statusRow(278, "claude", "Claude CLI")
        actionRow(252, "claudeauth", "Claude login", "Log in…", #selector(loginClaude))

        let recheck = NSButton(title: "Re-check", target: self, action: #selector(recheck))
        recheck.bezelStyle = .rounded; recheck.frame = NSRect(x: 20, y: 206, width: 100, height: 28)
        view.addSubview(recheck)

        let note = NSTextField(labelWithString:
            "Real mode shells git, gh and claude — all must be present and logged in. "
            + "AWS CLI is not required (the orchestrator never calls it; it appears only in the "
            + "optional VPS-relay docs). On a hub, the relay/SSH key is set on the Settings tab.")
        note.frame = NSRect(x: 20, y: 150, width: 700, height: 48)
        note.textColor = .secondaryLabelColor; note.font = NSFont.systemFont(ofSize: 11)
        note.maximumNumberOfLines = 3; note.lineBreakMode = .byWordWrapping
        view.addSubview(note)
    }

    func statusDot(_ id: String, _ f: NSRect) -> NSTextField {
        let d = NSTextField(labelWithString: "…"); d.frame = f; d.textColor = .secondaryLabelColor
        status[id] = d; return d
    }
    func statusRow(_ y: CGFloat, _ id: String, _ name: String) {
        view.addSubview(statusDot(id, NSRect(x: 20, y: y, width: 18, height: 20)))
        let l = NSTextField(labelWithString: name); l.font = NSFont.systemFont(ofSize: 13, weight: .medium)
        l.frame = NSRect(x: 44, y: y, width: 150, height: 20); view.addSubview(l)
        let d = NSTextField(labelWithString: ""); d.frame = NSRect(x: 200, y: y, width: 330, height: 20)
        d.textColor = .secondaryLabelColor; d.lineBreakMode = .byTruncatingTail
        detail[id] = d; view.addSubview(d)
    }
    func actionRow(_ y: CGFloat, _ id: String, _ name: String, _ btn: String, _ sel: Selector) {
        statusRow(y, id, name)
        let b = NSButton(title: btn, target: self, action: sel)
        b.bezelStyle = .rounded; b.frame = NSRect(x: 540, y: y - 4, width: 120, height: 28); view.addSubview(b)
    }

    func set(_ id: String, _ state: String, _ text: String) {
        if let d = status[id] {
            switch state {
            case "ok":   d.stringValue = "●"; d.textColor = .systemGreen
            case "bad":  d.stringValue = "○"; d.textColor = .systemRed
            case "warn": d.stringValue = "!"; d.textColor = .systemOrange
            default:     d.stringValue = "…"; d.textColor = .secondaryLabelColor
            }
        }
        detail[id]?.stringValue = text
    }

    func refresh() {
        for id in ["python", "git", "gitid", "gh", "ghauth", "claude", "claudeauth"] { set(id, "pending", "checking…") }
        DispatchQueue.global().async {
            // python ≥ 3.9
            let pv = self.sh("python3 --version"); let ver = self.firstLine(pv.out)
            let comps = ver.replacingOccurrences(of: "Python", with: "").trimmingCharacters(in: .whitespaces).split(separator: ".")
            let pyOK = pv.code == 0 && comps.count >= 2 && (Int(comps[0]) ?? 0) == 3 && (Int(comps[1]) ?? 0) >= 9
            DispatchQueue.main.async {
                self.set("python", pyOK ? "ok" : "bad", pyOK ? ver : "not found — install Xcode CLT or brew install python")
            }
            // git + commit identity
            let git = self.which("git")
            let name = self.firstLine(self.sh("git config --global user.name").out)
            let email = self.firstLine(self.sh("git config --global user.email").out)
            DispatchQueue.main.async {
                self.set("git", git.isEmpty ? "bad" : "ok", git.isEmpty ? "not found — install Xcode CLT" : git)
                self.set("gitid", (!name.isEmpty && !email.isEmpty) ? "ok" : "bad", "")
                if self.gitName.stringValue.isEmpty { self.gitName.stringValue = name }
                if self.gitEmail.stringValue.isEmpty { self.gitEmail.stringValue = email }
            }
            // gh + auth
            let gh = self.which("gh")
            let ghAuth = gh.isEmpty ? false : (self.sh("gh auth status").code == 0)
            DispatchQueue.main.async {
                self.set("gh", gh.isEmpty ? "bad" : "ok", gh.isEmpty ? "not found — brew install gh" : gh)
                self.set("ghauth", ghAuth ? "ok" : "bad", ghAuth ? "authenticated" : "not logged in — click Log in…")
            }
            // claude + credentials
            let claude = self.resolveClaude()
            let fm = FileManager.default
            let creds = fm.fileExists(atPath: "\(home)/.claude/.credentials.json") || fm.fileExists(atPath: "\(home)/.claude.json")
            DispatchQueue.main.async {
                self.set("claude", claude.isEmpty ? "bad" : "ok", claude.isEmpty ? "not found — install the Claude Code CLI" : claude)
                self.set("claudeauth", creds ? "ok" : "warn", creds ? "credentials present" : "no credentials — click Log in…")
            }
        }
    }

    // Same resolution order the postinstall uses to bake INBOX_CLAUDE_BIN.
    func resolveClaude() -> String {
        let w = which("claude"); if !w.isEmpty { return w }
        for c in ["\(home)/.local/bin/claude", "\(home)/.claude/local/claude", "\(home)/.npm-global/bin/claude",
                  "\(home)/.bun/bin/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude"] {
            if FileManager.default.isExecutableFile(atPath: c) { return c }
        }
        return ""
    }

    func openTerminal(_ cmd: String) {
        let script = "tell application \"Terminal\"\nactivate\ndo script \"\(cmd)\"\nend tell"
        run("/usr/bin/osascript", ["-e", script])
    }

    @objc func recheck() { refresh() }
    @objc func loginGh() { openTerminal("gh auth login") }
    @objc func loginClaude() { openTerminal("claude") }
    @objc func saveGitId() {
        let name = gitName.stringValue.trimmingCharacters(in: .whitespaces)
        let email = gitEmail.stringValue.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty, !email.isEmpty else { return }
        let git = which("git"); let bin = git.isEmpty ? "/usr/bin/git" : git
        run(bin, ["config", "--global", "user.name", name])
        run(bin, ["config", "--global", "user.email", email])
        refresh()
    }
}

// =====================================================================
// The app: status item -> persistent window with Tasks + Settings tabs,
// plus a footer of launchd controls.
// =====================================================================
final class Controller: NSObject, NSWindowDelegate {
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    let window: NSWindow
    let tabs = NSTabView(frame: NSRect(x: 10, y: 48, width: 760, height: 488))
    let seg = NSSegmentedControl()
    let tasksPane = TasksPane()
    let settingsPane = SettingsPane()
    let setupPane = SetupPane()
    let statusLabel = NSTextField(labelWithString: "")
    var killItem: NSButton?

    override init() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 780, height: 572),
                          styleMask: [.titled, .closable, .miniaturizable],
                          backing: .buffered, defer: false)
        super.init()
        window.title = "outerloop"
        window.delegate = self
        window.isReleasedWhenClosed = false
        buildWindow()

        item.button?.target = self
        item.button?.action = #selector(togglePanel)
        refreshIcon()
        Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { _ in self.refreshIcon(); self.refreshFooter() }
        DispatchQueue.main.async { [weak self] in self?.configureHubIfNeeded() }
    }

    func buildWindow() {
        guard let c = window.contentView else { return }

        let tasksItem = NSTabViewItem(identifier: "tasks")
        tasksItem.label = "Tasks"; tasksItem.view = tasksPane.view
        let setContainer = NSView(frame: NSRect(x: 0, y: 0, width: 744, height: 488))
        settingsPane.build(into: setContainer)
        let setItem = NSTabViewItem(identifier: "settings")
        setItem.label = "Settings"; setItem.view = setContainer
        let setupItem = NSTabViewItem(identifier: "setup")
        setupItem.label = "Setup"; setupItem.view = setupPane.view
        tabs.tabViewType = .noTabsNoBorder   // Activity-Monitor style: no top strip clipping the panes
        tabs.addTabViewItem(tasksItem)       // 0
        tabs.addTabViewItem(setItem)         // 1 — startAll() jumps here on missing config
        tabs.addTabViewItem(setupItem)       // 2
        c.addSubview(tabs)

        // Activity-Monitor-style switcher: a centered segmented control above the content.
        seg.segmentStyle = .texturedRounded; seg.trackingMode = .selectOne
        seg.segmentCount = 3
        seg.setLabel("Tasks", forSegment: 0); seg.setLabel("Settings", forSegment: 1)
        seg.setLabel("Setup", forSegment: 2)
        seg.setWidth(100, forSegment: 0); seg.setWidth(100, forSegment: 1); seg.setWidth(100, forSegment: 2)
        seg.selectedSegment = 0
        seg.target = self; seg.action = #selector(selectTab)
        seg.frame = NSRect(x: (780 - 300) / 2, y: 540, width: 300, height: 24)
        c.addSubview(seg)

        // footer: role + agent status, and launchd controls
        statusLabel.frame = NSRect(x: 14, y: 14, width: 380, height: 20)
        statusLabel.textColor = .secondaryLabelColor
        c.addSubview(statusLabel)
        let quit = NSButton(title: "Quit", target: self, action: #selector(quit))
        quit.bezelStyle = .rounded; quit.frame = NSRect(x: 700, y: 10, width: 70, height: 28)
        c.addSubview(quit)
        let stop = NSButton(title: role == "hub" ? "Stop hub" : "Stop worker", target: self, action: #selector(stopAll))
        stop.bezelStyle = .rounded; stop.frame = NSRect(x: 588, y: 10, width: 108, height: 28)
        c.addSubview(stop)
        let startB = NSButton(title: role == "hub" ? "Start hub" : "Start worker", target: self, action: #selector(startAll))
        startB.bezelStyle = .rounded; startB.frame = NSRect(x: 476, y: 10, width: 108, height: 28)
        c.addSubview(startB)
        if role == "hub" {
            let k = NSButton(title: killEngaged ? "Resume all" : "Pause all", target: self, action: #selector(kill))
            k.bezelStyle = .rounded; k.frame = NSRect(x: 388, y: 10, width: 84, height: 28)
            c.addSubview(k); killItem = k
        }
        refreshFooter()
    }

    func refreshFooter() {
        let dots = labels.map { "\(isRunning($0) ? "●" : "○") \($0.replacingOccurrences(of: "com.outerloop.", with: ""))" }
        var s = "\(role)  —  " + dots.joined(separator: "   ")
        if role == "worker" { s += "   ·   hub: \(readHubURL() ?? "(not set)")" }
        statusLabel.stringValue = s
        killItem?.title = killEngaged ? "Resume all" : "Pause all"
    }

    func refreshIcon() {
        let up = labels.allSatisfy { isRunning($0) }
        let dot = killEngaged ? "◍" : (up ? "●" : "○")
        let color: NSColor = killEngaged ? .systemRed : (up ? .systemGreen : .systemGray)
        item.button?.attributedTitle = NSAttributedString(
            string: dot, attributes: [.foregroundColor: color, .font: NSFont.systemFont(ofSize: 14)])
        item.button?.toolTip = "outerloop (\(role))"
    }

    @objc func selectTab(_ s: NSSegmentedControl) { tabs.selectTabViewItem(at: s.selectedSegment) }

    @objc func togglePanel() {
        if !window.isVisible { window.center() }
        NSApp.setActivationPolicy(.regular)   // window open → show Dock icon + app-switcher entry
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        tasksPane.start()          // begin polling when shown (idempotent)
        setupPane.refresh()        // re-probe prereqs each time the window opens
        refreshFooter()
    }
    // Stop polling and drop back to menu-bar-only (no Dock icon) when the window is hidden.
    func windowWillClose(_ notification: Notification) {
        tasksPane.stop()
        NSApp.setActivationPolicy(.accessory)
    }

    func configureHubIfNeeded() {   // first-run: worker with no hub set
        guard role == "worker", readHubURL() == nil else { return }
        if let url = promptHubURL("") { writeHubURL(url); run("/bin/launchctl", ["kickstart", "-k", "gui/\(uid)/com.outerloop.worker"]) }
    }
    func workerConfigMissing() -> Bool {
        let e = workerEnv()
        return (readHubURL() ?? e["INBOX_HUB"] ?? "").isEmpty
            || (e["INBOX_DEVICE"] ?? "").isEmpty
            || (e["INBOX_DEVICE_TOKEN"] ?? "").isEmpty
    }
    @objc func startAll() {
        if role == "worker", workerConfigMissing() { seg.selectedSegment = 1; tabs.selectTabViewItem(at: 1); return }
        labels.forEach(startAgent); refreshIcon(); refreshFooter()
    }
    @objc func stopAll() { labels.forEach(stopAgent); refreshIcon(); refreshFooter() }
    @objc func kill() { toggleKill(); refreshIcon(); refreshFooter() }
    @objc func quit() { NSApplication.shared.terminate(nil) }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)   // menu-bar only, no Dock icon
let controller = Controller()
app.run()
