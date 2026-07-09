// outerloop control app — "Mission Control" redesign.
// A background (LSUIElement) status item. Clicking it opens a menu-bar POPOVER
// (the glance: needs-you list, fleet, token budget, kill switch); the popover's
// gear opens the full dark WINDOW (sidebar: Tasks / Settings / Setup). It holds no
// state the DB/launchd doesn't already own — it shells `launchctl`, tails claude
// transcripts, and talks to the hub's JSON API.
import Cocoa
import CryptoKit
import ServiceManagement

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
// pkg install = deploy.env exists. A brew install has no deploy.env: role/identity
// live in machine-local settings.json (written by `outerloop local` / this app) and
// the daemon is brew services' homebrew.mxcl.outerloop, not com.outerloop.* agents.
let isPkg = FileManager.default.fileExists(atPath: ENV_PATH)
let brewLabel = "homebrew.mxcl.outerloop"
let brewBin = ["/opt/homebrew/bin/brew", "/usr/local/bin/brew"]
    .first { FileManager.default.fileExists(atPath: $0) } ?? "brew"
let dashURL = env["DASH_URL"] ?? "http://127.0.0.1:8765"
let uid = getuid()
let home = NSHomeDirectory()
let dataDir = "\(home)/Library/Application Support/outerloop"
let killFile = "\(dataDir)/KILL"
let settingsFile = "\(dataDir)/settings.json"  // runtime config the worker reads (hub URL)
let workerPlist = "\(home)/Library/LaunchAgents/com.outerloop.worker.plist"
// pkg bakes the role into deploy.env; brew reads settings.json — same key `outerloop
// local role` writes. Computed (not a constant) so a first-run choice takes effect
// without relaunching; unset shows "hub" but the picker persists the real value.
var role: String { env["ROLE"] ?? readSetting("role") ?? "hub" }

// The worker's identity (worker name + token): the pkg bakes it into the worker's
// launchd plist; under brew it lives in settings.json (keys `worker` / `token`).
func workerEnv() -> [String: String] {
    guard let d = NSDictionary(contentsOfFile: workerPlist),
          let e = d["EnvironmentVariables"] as? [String: String] else { return [:] }
    return e
}
func myWorker() -> String { workerEnv()["OUTERLOOP_WORKER"] ?? readSetting("worker") ?? "" }
func myToken() -> String { workerEnv()["OUTERLOOP_WORKER_TOKEN"] ?? readSetting("token") ?? "" }

// A hub-side box: a pure hub or a combined hub+worker node (role=both). Both show the hub
// UI and run the hub agents; a combined node just also runs a co-located worker (handled by
// `outerloop service`, so it needs no extra agent under brew).
func isHubBox() -> Bool { role == "hub" || role == "both" }

// launchd agents this machine owns: pkg = com.outerloop.* by role (label == plist
// basename); brew = the single brew-services label.
let labels: [String] = !isPkg ? [brewLabel]
    : isHubBox()
    ? ["com.outerloop.hub", "com.outerloop.tunnel", "com.outerloop.worker"]
    : ["com.outerloop.worker"]

// --- Mission Control palette (docs/design_handoff_mission_control/README.md) ---
func hexColor(_ v: UInt32, _ a: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: CGFloat((v >> 16) & 0xff) / 255,
            green: CGFloat((v >> 8) & 0xff) / 255,
            blue: CGFloat(v & 0xff) / 255, alpha: a)
}
enum C {
    static let bg = hexColor(0x14171e)          // window background
    static let popover = hexColor(0x1c2029)     // popover surface
    static let sidebar = hexColor(0x161921, 0.85)
    static let deep = hexColor(0x101318)        // status block, inputs
    static let well = hexColor(0x12151b)
    static let term = hexColor(0x0b0d11)        // terminal
    static let tx = hexColor(0xe8eaf0)
    static let tx2 = hexColor(0x9aa2b1)
    static let tx3 = hexColor(0x5d6470)
    static let body = hexColor(0xc6ccd8)
    static let acc = hexColor(0x3ddc84)
    static let warn = hexColor(0xf5b843)
    static let bad = hexColor(0xf26d6d)
    static let info = hexColor(0x5eb1f7)
    static let violet = hexColor(0xa78bfa)
    static let hairline = NSColor.white.withAlphaComponent(0.07)
}
func monoFont(_ size: CGFloat, _ weight: NSFont.Weight = .regular) -> NSFont {
    NSFont.monospacedSystemFont(ofSize: size, weight: weight)
}
// Stamped into Info.plist by build-app.sh from outerloop/__init__.py ("?" on
// unbundled dev runs where Bundle.main has no plist).
let APP_VERSION = (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "?"

func label(_ s: String, _ font: NSFont, _ color: NSColor) -> NSTextField {
    let t = NSTextField(labelWithString: s)
    t.font = font; t.textColor = color; t.lineBreakMode = .byTruncatingTail
    return t
}
// The console's section voice: tiny, wide-tracked, uppercase.
func microlabel(_ s: String, _ color: NSColor = C.tx3) -> NSTextField {
    let t = NSTextField(labelWithString: "")
    t.attributedStringValue = NSAttributedString(
        string: s.uppercased(),
        attributes: [.font: NSFont.systemFont(ofSize: 10, weight: .semibold),
                     .foregroundColor: color, .kern: 1.2])
    return t
}
// Flat pill button: fill or hairline outline, rounded 7.
func flatButton(_ title: String, fill: NSColor?, text: NSColor, border: NSColor?,
                font: NSFont = .systemFont(ofSize: 12, weight: .semibold)) -> NSButton {
    let b = NSButton(title: title, target: nil, action: nil)
    b.isBordered = false; b.wantsLayer = true
    b.layer?.cornerRadius = 7
    if let f = fill { b.layer?.backgroundColor = f.cgColor }
    if let br = border { b.layer?.borderWidth = 1; b.layer?.borderColor = br.cgColor }
    b.attributedTitle = NSAttributedString(string: title,
        attributes: [.font: font, .foregroundColor: text])
    return b
}
final class FlippedView: NSView { override var isFlipped: Bool { true } }

// --- LAN pairing crypto — the Swift mirror of outerloop/pairing.py ----------
extension Data {
    init?(hexString: String) {
        guard hexString.count % 2 == 0 else { return nil }
        var d = Data(capacity: hexString.count / 2)
        var i = hexString.startIndex
        while i < hexString.endIndex {
            let next = hexString.index(i, offsetBy: 2)
            guard let b = UInt8(hexString[i..<next], radix: 16) else { return nil }
            d.append(b)
            i = next
        }
        self = d
    }
    var hexString: String { map { String(format: "%02x", $0) }.joined() }
}

let PAIR_ALPHABET = Array("23456789ABCDEFGHJKMNPQRSTUVWXYZ")  // no 0/O/1/I
func makePairCode() -> String { String((0..<6).map { _ in PAIR_ALPHABET.randomElement()! }) }

// PBKDF2-HMAC-SHA256, 100k rounds, dkLen 32 = a single block — spelled out with
// CryptoKit so we don't need CommonCrypto. Must match hashlib.pbkdf2_hmac exactly.
func pairKey(_ code: String, _ salt: Data) -> Data {
    let pw = SymmetricKey(data: Data(code.utf8))
    var block = salt
    block.append(contentsOf: [0, 0, 0, 1])
    var u = Data(HMAC<SHA256>.authenticationCode(for: block, using: pw))
    var out = u
    for _ in 1..<100_000 {
        u = Data(HMAC<SHA256>.authenticationCode(for: u, using: pw))
        for i in 0..<out.count { out[i] ^= u[i] }
    }
    return out
}

func pairCodeCheck(_ code: String, _ salt: Data) -> String {
    var d = salt
    d.append(contentsOf: Array(code.utf8))
    return Data(SHA256.hash(data: d)).hexString
}

func pairDecrypt(code: String, salt: Data, cipherHex: String, macHex: String) -> String? {
    guard let enc = Data(hexString: cipherHex) else { return nil }
    let k = pairKey(code, salt)
    let mac = Data(HMAC<SHA256>.authenticationCode(for: enc, using: SymmetricKey(data: k))).hexString
    guard mac == macHex else { return nil }
    var stream = Data()
    var counter: UInt32 = 0
    while stream.count < enc.count {
        var d = k
        withUnsafeBytes(of: counter.bigEndian) { d.append(contentsOf: $0) }
        stream.append(contentsOf: Data(SHA256.hash(data: d)))
        counter += 1
    }
    return String(bytes: zip(enc, stream).map { $0 ^ $1 }, encoding: .utf8)
}

// JSON HTTP to an arbitrary base URL (discovery talks to candidate hubs, which
// aren't hubBase() yet and need no bearer).
func httpJSON(_ method: String, _ urlStr: String, body: [String: Any]? = nil,
              _ done: @escaping ([String: Any]?) -> Void) {
    guard let url = URL(string: urlStr) else { done(nil); return }
    var req = URLRequest(url: url)
    req.httpMethod = method
    req.timeoutInterval = 5
    if let b = body {
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: b)
    }
    URLSession.shared.dataTask(with: req) { data, _, _ in
        let j = (data.flatMap { try? JSONSerialization.jsonObject(with: $0) }) as? [String: Any]
        DispatchQueue.main.async { done(j) }
    }.resume()
}

// Browse _outerloop._tcp for hubs on this LAN. ponytail: NetService is deprecated
// but is still the simplest zero-dependency resolve-to-host:port path; move to
// NWBrowser if Apple ever removes it.
final class HubDiscovery: NSObject, NetServiceBrowserDelegate, NetServiceDelegate {
    struct FoundHub { let name: String; let base: String; var detail: String }
    private let browser = NetServiceBrowser()
    private var services: [NetService] = []   // retained while resolving
    var hubs: [FoundHub] = []
    var onChange: (() -> Void)?

    func start() {
        browser.delegate = self
        browser.searchForServices(ofType: "_outerloop._tcp.", inDomain: "local.")
    }
    func stop() {
        browser.stop()
        services.removeAll()
        hubs.removeAll()
    }
    func netServiceBrowser(_ b: NetServiceBrowser, didFind svc: NetService, moreComing: Bool) {
        services.append(svc)
        svc.delegate = self
        svc.resolve(withTimeout: 5)
    }
    func netServiceBrowser(_ b: NetServiceBrowser, didRemove svc: NetService, moreComing: Bool) {
        services.removeAll { $0 === svc }
        hubs.removeAll { $0.name == svc.name }
        onChange?()
    }
    func netServiceDidResolveAddress(_ svc: NetService) {
        guard let host = svc.hostName else { return }
        let h = host.hasSuffix(".") ? String(host.dropLast()) : host
        let base = "http://\(h):\(svc.port)"
        guard !hubs.contains(where: { $0.base == base }) else { return }
        hubs.append(FoundHub(name: svc.name, base: base, detail: "\(h) :\(svc.port)"))
        onChange?()
        httpJSON("GET", base + "/api/pair/info") { j in
            guard let j = j, let i = self.hubs.firstIndex(where: { $0.base == base }) else { return }
            let v = (j["version"] as? String) ?? "?"
            let w = (j["workers"] as? Int) ?? 0
            self.hubs[i].detail = "\(h) :\(svc.port) · v\(v) · \(w) worker\(w == 1 ? "" : "s")"
            self.onChange?()
        }
    }
}

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

// --- self-update -------------------------------------------------------------
// brew can't swap the bundle from a terminal without the App Management TCC
// grant, but an app replacing an app signed by the SAME team is never gated
// (the Sparkle model) — so the app updates itself and the cask is auto_updates.
let TEAM_ID = "QGB9U9HXX3"

func semverNewer(_ a: String, _ b: String) -> Bool {   // a > b, numeric per part
    let x = a.split(separator: ".").map { Int($0) ?? 0 }
    let y = b.split(separator: ".").map { Int($0) ?? 0 }
    for i in 0..<max(x.count, y.count) {
        let (p, q) = (i < x.count ? x[i] : 0, i < y.count ? y[i] : 0)
        if p != q { return p > q }
    }
    return false
}

// Download → verify (strict codesign + our TeamIdentifier) → swap own bundle →
// relaunch. Runs off-main; returns an error line, or never (terminates on success).
func selfUpdate(to ver: String) -> String? {
    let fm = FileManager.default
    let stage = NSTemporaryDirectory() + "outerloop-update-\(ver)"
    try? fm.removeItem(atPath: stage)
    try? fm.createDirectory(atPath: stage, withIntermediateDirectories: true)
    guard let u = URL(string: "https://github.com/phyolim/outerloop/releases/download/v\(ver)/Outerloop-\(ver).zip"),
          let data = try? Data(contentsOf: u) else { return "download failed" }
    let zip = stage + "/Outerloop.zip"
    guard (try? data.write(to: URL(fileURLWithPath: zip))) != nil,
          run("/usr/bin/ditto", ["-xk", zip, stage]).code == 0 else { return "unzip failed" }
    let newApp = stage + "/Outerloop.app"
    // Never install a bundle that isn't intact and ours — a corrupt or MITM'd
    // download must fail here, not become the running app.
    guard run("/usr/bin/codesign", ["--verify", "--strict", newApp]).code == 0,
          run("/usr/bin/codesign", ["-dv", newApp]).out.contains("TeamIdentifier=\(TEAM_ID)")
        else { return "signature check failed" }
    let live = Bundle.main.bundlePath
    let old = stage + "/replaced.app"
    do { try fm.moveItem(atPath: live, toPath: old) } catch { return "cannot move current app aside" }
    do { try fm.moveItem(atPath: newApp, toPath: live) } catch {
        try? fm.moveItem(atPath: old, toPath: live)     // roll back, keep running build
        return "cannot install new app"
    }
    let sh = Process()
    sh.executableURL = URL(fileURLWithPath: "/bin/sh")
    sh.arguments = ["-c", "sleep 1; /usr/bin/open \"\(live)\""]
    try? sh.run()
    DispatchQueue.main.async { NSApp.terminate(nil) }
    return nil
}

// Hands-off update: the hub reports its version on every /api/fleet. When it's
// newer than ours, pull the matching notarized build and swap in place — no click.
// selfUpdate verifies the signature and relaunches, so on success this never
// returns. `autoUpdating` is the single lock shared with the popover's manual
// Update button, so a background poll and a click can't stack downloads.
var autoUpdating = false
func maybeAutoUpdate(to hubVersion: String?) {
    guard let v = hubVersion, APP_VERSION != "?", semverNewer(v, APP_VERSION), !autoUpdating
        else { return }
    autoUpdating = true
    DispatchQueue.global().async {
        let err = selfUpdate(to: v)                 // relaunches on success
        DispatchQueue.main.async {
            autoUpdating = false
            if let err { NSLog("outerloop: auto-update to v\(v) failed: \(err)") }
        }
    }
}

func isRunning(_ label: String) -> Bool {
    let r = run("/bin/launchctl", ["print", "gui/\(uid)/\(label)"])
    return r.code == 0 && r.out.contains("state = running")
}
func startAgent(_ label: String) {
    if label == brewLabel { run(brewBin, ["services", "start", "outerloop"]); return }
    let plist = "\(home)/Library/LaunchAgents/\(label).plist"
    run("/bin/launchctl", ["bootstrap", "gui/\(uid)", plist])   // no-op if already loaded
    run("/bin/launchctl", ["kickstart", "-k", "gui/\(uid)/\(label)"])
}
func stopAgent(_ label: String) {
    if label == brewLabel { run(brewBin, ["services", "stop", "outerloop"]); return }
    run("/bin/launchctl", ["bootout", "gui/\(uid)/\(label)"])
}
// Restart whichever daemon runs the worker on this box (pkg agent or brew service).
func restartWorkerDaemon() {
    if isPkg {
        run("/bin/launchctl", ["kickstart", "-k", "gui/\(uid)/com.outerloop.worker"])
    } else {
        run(brewBin, ["services", "restart", "outerloop"])
    }
}

// --- login item: relaunch the GUI at login (macOS 13+; graceful no-op on 12) ---
func loginItemEnabled() -> Bool {
    if #available(macOS 13, *) { return SMAppService.mainApp.status == .enabled }
    return false
}
func setLoginItem(_ on: Bool) {
    if #available(macOS 13, *) {
        do { on ? try SMAppService.mainApp.register() : try SMAppService.mainApp.unregister() }
        catch { NSSound.beep() }
    }
}

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
    let e = workerEnv()["OUTERLOOP_HUB"] ?? ""
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

// Blank the worker token wherever it lives (the inverse of the pairing writes),
// so isUnpairedWorker flips true and the popover offers pairing again.
func clearWorkerToken() {
    if workerEnv()["OUTERLOOP_WORKER_TOKEN"] != nil { _ = setWorkerEnv(["OUTERLOOP_WORKER_TOKEN": ""]) }
    writeSettings(["token": ""])
}

// --- hub JSON API (bearer = this machine's worker token) --------------------
func apiGET(_ path: String, status statusDone: @escaping ([String: Any]?, Int) -> Void) {
    guard let url = URL(string: hubBase() + path) else { statusDone(nil, 0); return }
    var req = URLRequest(url: url); req.timeoutInterval = 5
    let tok = myToken(); if !tok.isEmpty { req.setValue("Bearer \(tok)", forHTTPHeaderField: "Authorization") }
    URLSession.shared.dataTask(with: req) { data, resp, _ in
        let j = (data.flatMap { try? JSONSerialization.jsonObject(with: $0) }) as? [String: Any]
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        DispatchQueue.main.async { statusDone(j, code) }
    }.resume()
}
func apiGET(_ path: String, _ done: @escaping ([String: Any]?) -> Void) {
    apiGET(path, status: { j, _ in done(j) })
}
func apiPOST(_ path: String, body: [String: Any] = [:], _ done: @escaping (Bool) -> Void) {
    guard let url = URL(string: hubBase() + path) else { done(false); return }
    var req = URLRequest(url: url); req.httpMethod = "POST"; req.timeoutInterval = 5
    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
    req.httpBody = (try? JSONSerialization.data(withJSONObject: body)) ?? Data("{}".utf8)
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
    a.informativeText = "Where this worker reaches the hub, e.g. http://hub.local:8765"
    a.addButton(withTitle: "Save"); a.addButton(withTitle: "Cancel")
    let f = NSTextField(frame: NSRect(x: 0, y: 0, width: 300, height: 24))
    f.stringValue = prefill
    f.placeholderString = "http://hub.local:8765"
    a.accessoryView = f
    NSApp.activate(ignoringOtherApps: true)
    guard a.runModal() == .alertFirstButtonReturn else { return nil }
    let v = f.stringValue.trimmingCharacters(in: .whitespaces)
    return v.isEmpty ? nil : v
}

// =====================================================================
// Tasks pane: live fleet task list over a terminal-style live transcript.
// Terminate + CLI output light up only for tasks running on THIS machine
// (the claude process + transcript are local).
// =====================================================================
let CONTENT_W: CGFloat = 620, WIN_H: CGFloat = 560

final class TasksPane: NSObject, NSTableViewDataSource, NSTableViewDelegate {
    let view = NSView(frame: NSRect(x: 0, y: 0, width: CONTENT_W, height: WIN_H))
    let table = NSTableView()
    let output = NSTextView()
    let terminateBtn = flatButton("Terminate", fill: nil, text: C.bad,
                                  border: C.bad.withAlphaComponent(0.3),
                                  font: .systemFont(ofSize: 11))
    let headerCounts = label("", monoFont(11), C.tx3)
    let outputLabel = label("", monoFont(10), C.acc)
    var tasks: [[String: Any]] = []
    var pollTimer: Timer?
    var tailTimer: Timer?
    var tailedSession: String?
    var tailedPath: String?

    override init() { super.init(); build() }

    func build() {
        view.wantsLayer = true

        // header strip: title + counts, Terminate/Refresh right
        let title = label("Tasks", .systemFont(ofSize: 14, weight: .semibold), C.tx)
        title.frame = NSRect(x: 16, y: WIN_H - 34, width: 60, height: 18)
        view.addSubview(title)
        headerCounts.frame = NSRect(x: 74, y: WIN_H - 32, width: 250, height: 15)
        view.addSubview(headerCounts)
        terminateBtn.target = self; terminateBtn.action = #selector(confirmTerminate)
        terminateBtn.frame = NSRect(x: CONTENT_W - 176, y: WIN_H - 37, width: 84, height: 24)
        terminateBtn.isEnabled = false
        view.addSubview(terminateBtn)
        let refresh = flatButton("Refresh", fill: nil, text: C.tx2,
                                 border: NSColor.white.withAlphaComponent(0.12),
                                 font: .systemFont(ofSize: 11))
        refresh.target = self; refresh.action = #selector(reload)
        refresh.frame = NSRect(x: CONTENT_W - 86, y: WIN_H - 37, width: 70, height: 24)
        view.addSubview(refresh)
        let hairline = NSView(frame: NSRect(x: 0, y: WIN_H - 44, width: CONTENT_W, height: 1))
        hairline.wantsLayer = true; hairline.layer?.backgroundColor = C.hairline.cgColor
        view.addSubview(hairline)

        // task rows
        let cols: [(String, CGFloat)] = [
            ("id", 36), ("title", 250), ("status", 76), ("sub_stage", 92), ("worker", 70)]
        for (id, w) in cols {
            let c = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(id))
            c.width = w
            table.addTableColumn(c)
        }
        table.dataSource = self; table.delegate = self
        table.headerView = nil
        table.backgroundColor = .clear
        table.usesAlternatingRowBackgroundColors = false
        table.allowsMultipleSelection = false
        table.rowHeight = 24
        table.gridStyleMask = []
        let ts = NSScrollView(frame: NSRect(x: 0, y: WIN_H - 44 - 178, width: CONTENT_W, height: 178))
        ts.hasVerticalScroller = true; ts.borderType = .noBorder
        ts.drawsBackground = false
        ts.documentView = table
        view.addSubview(ts)

        // LIVE OUTPUT label + session, then the terminal
        let live = microlabel("live output")
        live.frame = NSRect(x: 16, y: WIN_H - 246, width: 90, height: 14)
        view.addSubview(live)
        outputLabel.frame = NSRect(x: 110, y: WIN_H - 246, width: CONTENT_W - 126, height: 14)
        view.addSubview(outputLabel)

        let os = NSScrollView(frame: NSRect(x: 16, y: 14, width: CONTENT_W - 32, height: WIN_H - 268))
        os.hasVerticalScroller = true; os.borderType = .noBorder
        os.wantsLayer = true
        os.layer?.backgroundColor = C.term.cgColor
        os.layer?.cornerRadius = 9
        os.layer?.borderWidth = 1
        os.layer?.borderColor = NSColor.white.withAlphaComponent(0.06).cgColor
        os.drawsBackground = false
        output.isEditable = false
        output.drawsBackground = false
        output.font = monoFont(11)
        output.textColor = C.body
        output.textContainerInset = NSSize(width: 10, height: 9)
        output.isVerticallyResizable = true; output.isHorizontallyResizable = false
        output.autoresizingMask = [.width]
        output.minSize = NSSize(width: 0, height: 0)
        output.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        output.textContainer?.containerSize = NSSize(width: CONTENT_W - 32, height: CGFloat.greatestFiniteMagnitude)
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
                self.headerCounts.stringValue = "hub unreachable (\(hubBase()))"
                self.outputLabel.stringValue = ""
                self.terminateBtn.isEnabled = false; return
            }
            let keep = self.selectedId()
            self.tasks = arr
            let running = arr.filter { ($0["running"] as? Bool) ?? false }.count
            self.headerCounts.stringValue = "\(arr.count) in the loop · \(running) running"
            self.table.reloadData()
            if let keep = keep, let i = arr.firstIndex(where: { ($0["id"] as? Int) == keep }) {
                self.table.selectRowIndexes([i], byExtendingSelection: false)
            }
            self.updateForSelection()
        }
    }

    // --- NSTableView view-based data source ---
    func numberOfRows(in tableView: NSTableView) -> Int { tasks.count }
    func cellAttrs(_ row: Int, _ key: String) -> (String, NSFont, NSColor) {
        guard row < tasks.count else { return ("", monoFont(10), C.tx3) }
        let task = tasks[row]
        switch key {
        case "id":
            return ((task["id"] as? Int).map(String.init) ?? "", monoFont(11), C.tx3)
        case "title":
            return ((task["title"] as? String) ?? "", .systemFont(ofSize: 12), C.tx)
        case "status":
            let s = (task["status"] as? String) ?? ""
            let color: NSColor = s == "active" ? C.acc : s == "blocked" ? C.warn
                : s == "failed" ? C.bad : C.tx3
            return ("● \(s)", monoFont(10, .semibold), color)
        case "sub_stage":
            return ((task["sub_stage"] as? String) ?? "", monoFont(10), C.tx2)
        case "worker":
            return ((task["worker"] as? String) ?? "—", monoFont(10), C.tx3)
        default:
            return ((task[key] as? String) ?? "", .systemFont(ofSize: 12), C.tx)
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
        let (text, font, color) = cellAttrs(row, col.identifier.rawValue)
        field.stringValue = text; field.font = font; field.textColor = color
        return field
    }
    // the local running row gets a faint green tint
    func tableView(_ t: NSTableView, rowViewForRow row: Int) -> NSTableRowView? {
        let v = TintRowView()
        if row < tasks.count, isLocal(tasks[row]), (tasks[row]["running"] as? Bool) ?? false {
            v.tint = C.acc.withAlphaComponent(0.05)
        }
        return v
    }
    func tableViewSelectionDidChange(_ notification: Notification) { updateForSelection() }

    func isLocal(_ task: [String: Any]) -> Bool {
        let dev = task["worker"] as? String
        return dev != nil && dev == myWorker() && !myWorker().isEmpty
    }

    func updateForSelection() {
        let row = table.selectedRow
        guard row >= 0, row < tasks.count else {
            terminateBtn.isEnabled = false; outputLabel.stringValue = ""; setTail(nil); return
        }
        let task = tasks[row]
        let running = (task["running"] as? Bool) ?? ((task["worker"] as? String) != nil)
        let local = isLocal(task)
        terminateBtn.isEnabled = local && running
        let session = task["session_id"] as? String
        if local, let s = session {
            outputLabel.textColor = C.acc
            outputLabel.stringValue = "session \(s.prefix(8)) · this machine"
            setTail(s)
        } else {
            setTail(nil); output.string = ""
            outputLabel.textColor = C.tx3
            if let dev = task["worker"] as? String {
                outputLabel.stringValue = "running on \(dev) — live output is only on that machine"
            } else {
                outputLabel.stringValue = "not running"
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
    // Colored sigils, per the design: ▸ assistant (blue), ⚙ tool (amber), » user (violet).
    func termLine(_ sigil: String, _ sigilColor: NSColor, _ text: String, _ textColor: NSColor) -> NSAttributedString {
        let para = NSMutableParagraphStyle(); para.lineHeightMultiple = 1.25
        let s = NSMutableAttributedString(string: sigil, attributes:
            [.font: monoFont(11), .foregroundColor: sigilColor, .paragraphStyle: para])
        s.append(NSAttributedString(string: " \(text)\n", attributes:
            [.font: monoFont(11), .foregroundColor: textColor, .paragraphStyle: para]))
        return s
    }
    func renderTail(_ session: String) {
        if tailedPath == nil { tailedPath = transcriptPath(session) }
        guard let path = tailedPath, let text = try? String(contentsOfFile: path, encoding: .utf8) else {
            if output.string.isEmpty { output.string = "(waiting for transcript…)" }
            return
        }
        let out = NSMutableAttributedString()
        for raw in text.split(separator: "\n") {
            guard let d = raw.data(using: .utf8),
                  let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
                  let msg = o["message"] as? [String: Any] else { continue }
            let isUser = (o["type"] as? String) == "user"
            if let s = msg["content"] as? String {
                out.append(isUser ? termLine("»", C.violet, s, C.body)
                                  : termLine("▸", C.info, s, C.body))
            } else if let arr = msg["content"] as? [[String: Any]] {
                for part in arr {
                    switch part["type"] as? String {
                    case "text":
                        if let t = part["text"] as? String { out.append(termLine("▸", C.info, t, C.body)) }
                    case "tool_use":
                        if let n = part["name"] as? String { out.append(termLine("  ⚙", C.warn, n, C.warn)) }
                    default: break
                    }
                }
            }
        }
        // keep only the tail (mirror of the old 500-line cap), then swap if changed
        if out.string != output.attributedString().string {
            output.textStorage?.setAttributedString(out)
            output.scrollToEndOfDocument(nil)
        }
    }
}

final class TintRowView: NSTableRowView {
    var tint: NSColor?
    override func drawBackground(in dirtyRect: NSRect) {
        (tint ?? .clear).setFill()
        dirtyRect.fill()
        if isSelected {
            NSColor.white.withAlphaComponent(0.06).setFill()
            bounds.fill()
        }
    }
}

// =====================================================================
// Settings tab: hub URL / identity (worker) or relay + loopback identity (hub),
// plus the service controls (start/stop, open at login).
// =====================================================================
final class SettingsPane: NSObject {
    let view = NSView(frame: NSRect(x: 0, y: 0, width: CONTENT_W, height: WIN_H))
    let hubField = NSTextField()
    let workerField = NSTextField()
    let tokenField = NSTextField()
    let capsValue = NSTextField(labelWithString: "")
    let vpsField = NSTextField()
    let userField = NSTextField()
    let keyField = NSTextField()
    let loginItem = NSButton(checkboxWithTitle: "Open at Login", target: nil, action: nil)

    // Native form geometry: a right-aligned label column + one aligned field column with
    // a steady row rhythm, instead of per-row magic frames that never quite lined up.
    let M: CGFloat = 20            // outer margin
    let labelW: CGFloat = 96       // right-aligned label column (M ..< fieldX)
    let fieldX: CGFloat = 128      // fields / controls start here
    let fieldW: CGFloat = 330
    let rowH: CGFloat = 22
    let rowStep: CGFloat = 34      // baseline-to-baseline between rows

    func header(_ c: NSView, _ y: CGFloat, _ s: String) {
        let t = microlabel(s)
        t.frame = NSRect(x: M, y: y, width: fieldX + fieldW - M, height: 16)
        c.addSubview(t)
    }
    func formLabel(_ s: String, _ y: CGFloat) -> NSTextField {
        let t = label(s, .systemFont(ofSize: 12), C.tx2)
        t.alignment = .right
        t.frame = NSRect(x: M, y: y + 3, width: labelW, height: 17)   // +3 vertically centers on a 22pt field
        return t
    }
    @discardableResult
    func row(_ c: NSView, _ y: CGFloat, _ label: String, _ field: NSTextField,
             width: CGFloat = 0, placeholder: String = "") -> NSTextField {
        c.addSubview(formLabel(label, y))
        field.frame = NSRect(x: fieldX, y: y, width: width > 0 ? width : fieldW, height: rowH)
        if !placeholder.isEmpty { field.placeholderString = placeholder }
        c.addSubview(field); return field
    }
    func note(_ c: NSView, _ y: CGFloat, _ s: String) {
        let n = NSTextField(wrappingLabelWithString: s)   // multi-line, sizes to fit — no manual wrapping
        n.frame = NSRect(x: fieldX, y: y, width: fieldW, height: 34)
        n.textColor = C.tx3; n.font = .systemFont(ofSize: 11)
        c.addSubview(n)
    }
    func button(_ title: String, _ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ sel: Selector) -> NSButton {
        let b = flatButton(title, fill: NSColor.white.withAlphaComponent(0.08), text: C.tx, border: nil,
                           font: .systemFont(ofSize: 12, weight: .medium))
        b.target = self; b.action = sel; b.frame = NSRect(x: x, y: y, width: w, height: 26)
        return b
    }

    func build() {
        let c = view
        let t = label("Settings", .systemFont(ofSize: 14, weight: .semibold), C.tx)
        t.frame = NSRect(x: M, y: WIN_H - 34, width: 200, height: 18); c.addSubview(t)
        let sub = label("Identity and relay for this machine. Capabilities are hub-owned — edit them in Fleet.",
                        .systemFont(ofSize: 12), C.tx3)
        sub.frame = NSRect(x: M, y: WIN_H - 54, width: CONTENT_W - 2 * M, height: 16); c.addSubview(sub)
        if isHubBox() { buildHub(c) } else { buildWorker(c) }
        buildService(c)
        present()
    }

    func buildHub(_ c: NSView) {
        var y: CGFloat = WIN_H - 90
        header(c, y, "this machine's worker"); y -= 32
        row(c, y, "Worker", workerField, placeholder: "worker name (e.g. hub)"); y -= rowStep
        row(c, y, "Token", tokenField, placeholder: "paste the token from Fleet"); y -= 30
        note(c, y - 4, role == "both"
             ? "This is a hub + worker node — the co-located worker + token are provisioned "
               + "automatically on start. Override the name/token here only if you want to."
             : "The hub runs its own loopback worker — pair it like any worker: "
             + "Fleet → \u{201C}Pair a new worker\u{201D}, then paste name + token here."); y -= 48
        c.addSubview(button("Save worker & token", fieldX, y, 160, #selector(saveIdentity)))
        c.addSubview(button("Open Dashboard", fieldX + 172, y, 136, #selector(openDashboard)))
        y -= 52
        if isPkg {   // relay/tunnel is a pkg-managed agent — hidden on a brew install
            header(c, y, "relay — remote access (reverse ssh tunnel)"); y -= 32
            row(c, y, "VPS host", vpsField, placeholder: "1.2.3.4.sslip.io  (empty = off)"); y -= rowStep
            row(c, y, "Tunnel user", userField, placeholder: "tunnel"); y -= rowStep
            row(c, y, "SSH key", keyField, placeholder: "~/.ssh/id_ed25519"); y -= 30
            note(c, y - 4, "Generate the keypair and authorize its .pub on the VPS "
                 + "(deploy/relay/vps-setup.sh) first — this only sets where + which key."); y -= 46
            c.addSubview(button("Save & reconnect", fieldX, y, 144, #selector(saveTunnel)))
        }
    }

    func buildWorker(_ c: NSView) {
        var y: CGFloat = WIN_H - 90
        header(c, y, "worker settings"); y -= 32
        row(c, y, "Hub URL", hubField, width: 250, placeholder: "http://hub.local:8765")
        c.addSubview(button("Save", fieldX + 260, y - 2, 62, #selector(saveHub))); y -= rowStep
        row(c, y, "Worker", workerField, placeholder: "worker name (e.g. mbp)"); y -= rowStep
        row(c, y, "Token", tokenField, placeholder: "paste the token from Fleet"); y -= 32
        c.addSubview(button("Save worker & token", fieldX, y, 160, #selector(saveIdentity))); y -= 34
        c.addSubview(formLabel("Capabilities", y))
        capsValue.frame = NSRect(x: fieldX, y: y + 1, width: fieldW, height: 18)
        capsValue.font = monoFont(11); capsValue.textColor = C.tx2
        capsValue.lineBreakMode = .byTruncatingTail; c.addSubview(capsValue); y -= 30
        note(c, y - 4, "Get Worker + Token from the hub's Fleet page \u{2192} \u{201C}Pair a new worker\u{201D}. "
             + "Capabilities are hub-owned — set them in Fleet."); y -= 48
        c.addSubview(button("Edit in Fleet \u{2192}", fieldX, y, 128, #selector(openFleet)))
        c.addSubview(button("Open Dashboard", fieldX + 140, y, 136, #selector(openDashboard)))
    }

    // service controls: start/stop the launchd agents + open-at-login (the old footer's job).
    // Pinned near the window bottom, below the pkg-hub relay section's last button.
    func buildService(_ c: NSView) {
        var y: CGFloat = 62
        header(c, y, "service"); y -= 36
        let start = flatButton(isHubBox() ? "Start hub" : "Start worker",
                               fill: C.acc, text: hexColor(0x0d0f13), border: nil,
                               font: .systemFont(ofSize: 12, weight: .semibold))
        start.target = self; start.action = #selector(startAll)
        start.frame = NSRect(x: fieldX, y: y, width: 110, height: 26); c.addSubview(start)
        let stop = flatButton(isHubBox() ? "Stop hub" : "Stop worker",
                              fill: nil, text: C.tx2, border: NSColor.white.withAlphaComponent(0.12),
                              font: .systemFont(ofSize: 12))
        stop.target = self; stop.action = #selector(stopAll)
        stop.frame = NSRect(x: fieldX + 122, y: y, width: 110, height: 26); c.addSubview(stop)

        loginItem.target = self; loginItem.action = #selector(toggleLoginItem)
        loginItem.frame = NSRect(x: fieldX + 250, y: y + 3, width: 150, height: 20)
        if #available(macOS 13, *) { loginItem.state = loginItemEnabled() ? .on : .off }
        else { loginItem.isHidden = true }
        c.addSubview(loginItem)
    }

    func present() {
        if isHubBox() {
            vpsField.stringValue = readSetting("vps_host") ?? (env["VPS_HOST"] ?? "")
            userField.stringValue = readSetting("tunnel_user") ?? (env["TUNNEL_USER"] ?? "tunnel")
            keyField.stringValue = readSetting("ssh_key") ?? (env["SSH_KEY"] ?? "")
            workerField.stringValue = myWorker()
            tokenField.stringValue = myToken()
        } else {
            hubField.stringValue = readHubURL() ?? (workerEnv()["OUTERLOOP_HUB"] ?? "")
            workerField.stringValue = myWorker()
            tokenField.stringValue = myToken()
            capsValue.stringValue = "loading…"
            fetchCaps()
        }
    }

    func fetchCaps() {
        let name = myWorker()
        guard !name.isEmpty else { capsValue.stringValue = "(pair this worker first)"; return }
        apiGET("/api/fleet") { j in
            var text = "(open Fleet to view)"
            if let devs = j?["workers"] as? [[String: Any]] {
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
        restartWorkerDaemon()
        fetchCaps()
    }
    @objc func saveIdentity() {
        let name = workerField.stringValue.trimmingCharacters(in: .whitespaces)
        let tok = tokenField.stringValue.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty, !tok.isEmpty else { return }
        if isPkg {
            guard setWorkerEnv(["OUTERLOOP_WORKER": name, "OUTERLOOP_WORKER_TOKEN": tok]) else {
                capsValue.stringValue = "(couldn't write worker config)"; return
            }
            run("/bin/launchctl", ["bootout", "gui/\(uid)/com.outerloop.worker"])
            run("/bin/launchctl", ["bootstrap", "gui/\(uid)", workerPlist])
        } else {
            writeSettings(["worker": name, "token": tok])   // same keys `outerloop local` writes
            restartWorkerDaemon()
        }
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
    @objc func startAll() { NotificationCenter.default.post(name: .olStartAll, object: nil) }
    @objc func stopAll() { NotificationCenter.default.post(name: .olStopAll, object: nil) }
    @objc func toggleLoginItem() { setLoginItem(loginItem.state == .on); loginItem.state = loginItemEnabled() ? .on : .off }
}

extension Notification.Name {
    static let olStartAll = Notification.Name("olStartAll")
    static let olStopAll = Notification.Name("olStopAll")
}

// =====================================================================
// Setup tab: live prerequisite checklist. Mirrors deploy/mac/scripts/preflight.sh
// (the authoritative real-mode gate: python3, git+identity, gh+auth, claude+creds)
// but as a GUI so the operator sees green/amber without opening a terminal, and can
// fix the two "identity" gaps in place: set git user.name/email, and launch the
// gh/claude interactive logins in Terminal. Probes run off the main thread through a
// LOGIN shell (zsh -lc) so PATH matches the user's interactive env — where Homebrew
// and the self-installed claude live — which the GUI's own PATH doesn't include.
// AWS is intentionally absent: the orchestrator never calls it (config.py shells only
// git/gh/claude); "aws" appears only in the optional VPS-relay docs.
// =====================================================================
final class SetupPane: NSObject {
    let view = NSView(frame: NSRect(x: 0, y: 0, width: CONTENT_W, height: WIN_H))
    let gitName = NSTextField()
    let gitEmail = NSTextField()
    var status: [String: NSTextField] = [:]
    var detail: [String: NSTextField] = [:]
    // shared column geometry: dot | name | value/detail, aligned across every row
    let dotX: CGFloat = 20, nameX: CGFloat = 44, nameW: CGFloat = 140, valX: CGFloat = 190, rowStep: CGFloat = 30

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
        let t = label("Setup", .systemFont(ofSize: 14, weight: .semibold), C.tx)
        t.frame = NSRect(x: dotX, y: WIN_H - 34, width: 300, height: 18)
        view.addSubview(t)
        let sub = label("Real mode shells git, gh and claude — all must be present and logged in.",
                        .systemFont(ofSize: 12), C.tx3)
        sub.frame = NSRect(x: dotX, y: WIN_H - 54, width: CONTENT_W - 2 * dotX, height: 16)
        view.addSubview(sub)

        var y: CGFloat = WIN_H - 92
        statusRow(y, "python", "Python ≥ 3.9"); y -= rowStep
        statusRow(y, "git", "git"); y -= rowStep

        // git identity — editable in place (the one prereq the operator sets, not installs)
        view.addSubview(statusDot("gitid", NSRect(x: dotX, y: y + 3, width: 16, height: 18)))
        let gl = label("Git identity", .systemFont(ofSize: 13, weight: .medium), C.tx)
        gl.frame = NSRect(x: nameX, y: y + 3, width: nameW, height: 18); view.addSubview(gl)
        gitName.frame = NSRect(x: valX, y: y, width: 140, height: 22); gitName.placeholderString = "user.name"
        view.addSubview(gitName)
        gitEmail.frame = NSRect(x: valX + 148, y: y, width: 170, height: 22); gitEmail.placeholderString = "user.email"
        view.addSubview(gitEmail)
        let save = NSButton(title: "Save", target: self, action: #selector(saveGitId))
        save.bezelStyle = .rounded; save.frame = NSRect(x: valX + 326, y: y - 3, width: 62, height: 28); view.addSubview(save)
        y -= rowStep

        statusRow(y, "gh", "GitHub CLI (gh)"); y -= rowStep
        actionRow(y, "ghauth", "GitHub login", "Log in…", #selector(loginGh)); y -= rowStep
        statusRow(y, "claude", "Claude CLI"); y -= rowStep
        actionRow(y, "claudeauth", "Claude login", "Log in…", #selector(loginClaude)); y -= rowStep + 8

        let recheck = flatButton("Re-check", fill: NSColor.white.withAlphaComponent(0.08), text: C.tx, border: nil,
                                 font: .systemFont(ofSize: 12, weight: .medium))
        recheck.target = self; recheck.action = #selector(recheckNow)
        recheck.frame = NSRect(x: dotX, y: y, width: 92, height: 26)
        view.addSubview(recheck)

        let note = NSTextField(wrappingLabelWithString:
            "Real mode shells git, gh and claude — all must be present and logged in. "
            + "AWS CLI is not required (the orchestrator never calls it; it appears only in the "
            + "optional VPS-relay docs). On a hub, the relay/SSH key is set on the Settings tab.")
        note.frame = NSRect(x: dotX, y: y - 56, width: CONTENT_W - 2 * dotX, height: 48)
        note.textColor = C.tx3; note.font = NSFont.systemFont(ofSize: 11)
        view.addSubview(note)
    }

    func statusDot(_ id: String, _ f: NSRect) -> NSTextField {
        let d = label("…", monoFont(12), C.tx3); d.frame = f
        status[id] = d; return d
    }
    func statusRow(_ y: CGFloat, _ id: String, _ name: String) {
        view.addSubview(statusDot(id, NSRect(x: dotX, y: y, width: 16, height: 18)))
        let l = label(name, .systemFont(ofSize: 13, weight: .medium), C.tx)
        l.frame = NSRect(x: nameX, y: y, width: nameW, height: 18); view.addSubview(l)
        let d = label("", monoFont(11), C.tx3)
        d.frame = NSRect(x: valX, y: y, width: CONTENT_W - valX - 116, height: 18)
        detail[id] = d; view.addSubview(d)
    }
    func actionRow(_ y: CGFloat, _ id: String, _ name: String, _ btn: String, _ sel: Selector) {
        statusRow(y, id, name)
        let b = flatButton(btn, fill: nil, text: C.tx2, border: NSColor.white.withAlphaComponent(0.12),
                           font: .systemFont(ofSize: 11))
        b.target = self; b.action = sel
        b.frame = NSRect(x: CONTENT_W - 108, y: y - 3, width: 88, height: 24); view.addSubview(b)
    }

    func set(_ id: String, _ state: String, _ text: String) {
        if let d = status[id] {
            switch state {
            case "ok":   d.stringValue = "●"; d.textColor = C.acc
            case "bad":  d.stringValue = "○"; d.textColor = C.bad
            case "warn": d.stringValue = "!"; d.textColor = C.warn
            default:     d.stringValue = "…"; d.textColor = C.tx3
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

    // Same resolution order the postinstall uses to bake OUTERLOOP_CLAUDE_BIN.
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

    @objc func recheckNow() { refresh() }
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
// The menu-bar popover — the glance: needs-you list, fleet, budget, kill switch.
// Rebuilt from /api/fleet + /api/decisions each time it opens.
// =====================================================================
final class PopoverPane: NSViewController {
    let W: CGFloat = 330
    var killSwitchOn = false
    var openWindow: (() -> Void)?
    var openSettings: (() -> Void)?
    // NSPopover ignores preferredContentSize changes once shown; the async
    // re-render (skeleton → live data) must push the new height to the popover
    // itself or the grown content clips at the top (header + gear vanish).
    weak var popover: NSPopover?

    // worker-side pairing (Pairing Flow steps 1–4)
    struct PairSession {
        let hub: HubDiscovery.FoundHub
        let requestId: String
        let code: String
        let salt: Data
        let expiresAt: Date
        let workerName: String
    }
    enum PairPhase {
        case idle, waiting(PairSession), done(String), failed(String)
        var isDone: Bool { if case .done = self { return true }; return false }
    }
    var pairPhase: PairPhase = .idle
    var pairNote: String?   // why we're (re-)pairing, e.g. token revoked by the hub
    var updateVersion: String?          // hub is advertising a newer build
    var updating = false
    var updateError: String?
    let discovery = HubDiscovery()
    var pairPoll: Timer?
    var pairTick: Timer?
    var pairReqs: [[String: Any]] = []   // hub side: pending requests from /api/fleet

    // An unpaired worker gets the pairing flow instead of the fleet glance.
    var isUnpairedWorker: Bool { role == "worker" && myToken().isEmpty }

    override func loadView() {
        view = FlippedView(frame: NSRect(x: 0, y: 0, width: W, height: 220))
    }
    // Paint the design's #1c2029 over the stock popover material. The layer goes
    // on the popover's frame view (contentView.superview) so the arrow and
    // rounded chrome are tinted too, not just the content rect.
    private let popoverBG = NSView()
    override func viewDidAppear() {
        super.viewDidAppear()
        guard popoverBG.superview == nil, let frame = view.window?.contentView?.superview else { return }
        popoverBG.wantsLayer = true
        popoverBG.layer?.backgroundColor = C.popover.cgColor
        popoverBG.frame = frame.bounds
        popoverBG.autoresizingMask = [.width, .height]
        frame.addSubview(popoverBG, positioned: .below, relativeTo: frame.subviews.first)
    }
    override func viewWillAppear() {
        super.viewWillAppear()
        if isUnpairedWorker, !(pairPhase.isDone) {
            discovery.onChange = { [weak self] in self?.renderPairing() }
            discovery.start()
            renderPairing()
            return
        }
        render(fleet: nil, decisions: nil)   // instant skeleton, then live data
        refresh()                            // update detection rides fleet["version"] in render()
    }

    @objc func installUpdate() {
        guard let uv = updateVersion, !updating, !autoUpdating else { return }
        updating = true; autoUpdating = true; updateError = nil
        refresh()
        DispatchQueue.global().async {
            let err = selfUpdate(to: uv)    // on success the app relaunches; no return
            DispatchQueue.main.async {
                self.updating = false; autoUpdating = false
                self.updateError = err
                self.refresh()
            }
        }
    }
    override func viewDidDisappear() {
        super.viewDidDisappear()
        // keep browsing/polling only while a pairing attempt is actually in flight
        if case .waiting = pairPhase {} else {
            discovery.stop()
            pairTick?.invalidate()
        }
    }

    func refresh() {
        apiGET("/api/fleet", status: { fleet, code in
            // A 401 while we hold a token means the hub deleted this worker:
            // drop the dead token and fall into the existing pairing flow.
            if code == 401, role == "worker", !myToken().isEmpty {
                self.tokenRevoked()
                return
            }
            apiGET("/api/decisions") { decisions in
                self.render(fleet: fleet, decisions: decisions)
            }
        })
    }

    func tokenRevoked() {
        clearWorkerToken()
        pairNote = "hub no longer recognizes this worker — pair again"
        pairPhase = .idle
        discovery.onChange = { [weak self] in self?.renderPairing() }
        discovery.start()
        renderPairing()
    }

    func render(fleet: [String: Any]?, decisions: [String: Any]?) {
        view.subviews.forEach { $0.removeFromSuperview() }
        var y: CGFloat = 0
        let inset: CGFloat = 15

        // header: pulsing dot, wordmark, role, busy count, gear → full window
        let workers = (fleet?["workers"] as? [[String: Any]]) ?? []
        let busy = workers.filter { $0["current_ticket"] != nil && !($0["current_ticket"] is NSNull) }.count
        // The hub advertises its version on every fleet poll; a newer one arms the
        // footer's Update button. The background poller installs it hands-off, but
        // this keeps the popover honest if the user opens it mid-cycle. ("?" == an
        // unbundled dev run, which can't self-update — never arm it there.)
        if APP_VERSION != "?", let hv = fleet?["version"] as? String, semverNewer(hv, APP_VERSION) {
            updateVersion = hv
        }
        let dot = label("●", .systemFont(ofSize: 10), fleet == nil ? C.tx3 : C.acc)
        dot.frame = NSRect(x: inset, y: 15, width: 12, height: 14); view.addSubview(dot)
        let mark = NSTextField(labelWithString: "")
        let m = NSMutableAttributedString(string: "outer", attributes: [.font: monoFont(13, .semibold), .foregroundColor: C.tx])
        m.append(NSAttributedString(string: "loop", attributes: [.font: monoFont(13, .semibold), .foregroundColor: C.acc]))
        mark.attributedStringValue = m; mark.isBordered = false; mark.isEditable = false; mark.drawsBackground = false
        mark.frame = NSRect(x: inset + 16, y: 13, width: 80, height: 17); view.addSubview(mark)
        let roleL = label(role, monoFont(11), C.tx3)
        roleL.frame = NSRect(x: inset + 98, y: 14, width: 90, height: 14); view.addSubview(roleL)
        let busyL = label(fleet == nil ? "…" : "\(busy) busy", monoFont(11), C.tx2)
        busyL.alignment = .right
        busyL.frame = NSRect(x: W - 118, y: 14, width: 70, height: 14); view.addSubview(busyL)
        let gear = flatButton("⚙", fill: nil, text: C.tx2, border: nil, font: .systemFont(ofSize: 13))
        gear.target = self; gear.action = #selector(openFullWindow)
        gear.frame = NSRect(x: W - 40, y: 10, width: 26, height: 22); view.addSubview(gear)
        y = 41
        addHairline(y); y += 10

        // NEEDS YOU
        let needs = (decisions?["decisions"] as? [[String: Any]]) ?? []
        if !needs.isEmpty {
            let h = microlabel("needs you · \(needs.count)", C.warn)
            h.frame = NSRect(x: inset, y: y, width: 200, height: 14); view.addSubview(h)
            y += 20
            for n in needs.prefix(4) {
                let kind = (n["kind"] as? String) ?? ""
                let (icon, iconColor): (String, NSColor) =
                    kind == "question" ? ("?", C.info) : kind == "error" ? ("!", C.bad) : ("⇡", C.warn)
                let cta = kind == "question" ? "Reply" : kind == "error" ? "Retry" : "Review"
                let box = label(icon, monoFont(11, .bold), iconColor)
                box.alignment = .center
                box.wantsLayer = true
                box.layer?.backgroundColor = iconColor.withAlphaComponent(0.14).cgColor
                box.layer?.cornerRadius = 6
                box.frame = NSRect(x: inset, y: y, width: 22, height: 22); view.addSubview(box)
                let tid = (n["ticket_id"] as? Int) ?? 0
                let t = NSTextField(labelWithString: "")
                let at = NSMutableAttributedString(string: "#\(tid) ", attributes: [.font: monoFont(10), .foregroundColor: C.tx3])
                at.append(NSAttributedString(string: (n["title"] as? String) ?? "",
                                             attributes: [.font: NSFont.systemFont(ofSize: 12), .foregroundColor: C.body]))
                t.attributedStringValue = at; t.lineBreakMode = .byTruncatingTail
                t.frame = NSRect(x: inset + 31, y: y + 3, width: W - 31 - 78 - 2 * inset, height: 16); view.addSubview(t)
                let b = flatButton(cta, fill: C.acc.withAlphaComponent(0.14), text: C.acc, border: nil,
                                   font: .systemFont(ofSize: 11, weight: .semibold))
                b.target = self; b.action = #selector(openTicket(_:)); b.tag = tid
                b.frame = NSRect(x: W - inset - 62, y: y, width: 62, height: 21); view.addSubview(b)
                y += 30
            }
            y += 4
        }

        // FLEET
        let fh = microlabel("fleet")
        fh.frame = NSRect(x: inset, y: y, width: 100, height: 14); view.addSubview(fh)
        y += 19
        if workers.isEmpty {
            let none = label(fleet == nil ? "hub unreachable — \(hubBase())" : "no workers paired yet",
                             monoFont(11), C.tx3)
            none.frame = NSRect(x: inset, y: y, width: W - 2 * inset, height: 14); view.addSubview(none)
            y += 20
        }
        for w in workers.prefix(6) {
            let status = (w["status"] as? String) ?? "offline"
            let online = (w["online"] as? Bool) ?? false
            let state = status == "online" ? (online ? "online" : "offline") : status
            let color: NSColor = state == "online" ? C.acc : state == "paused" ? C.warn
                : state == "draining" ? C.info : C.tx3
            let d = label("●", monoFont(10), color)
            d.frame = NSRect(x: inset, y: y + 1, width: 12, height: 12); view.addSubview(d)
            let name = label((w["name"] as? String) ?? "?", monoFont(12), C.tx)
            name.frame = NSRect(x: inset + 17, y: y, width: 60, height: 15); view.addSubview(name)
            var detail: String
            if let t = w["current_ticket"] as? Int { detail = "running #\(t)" }
            else if state == "offline", let ago = w["seconds_ago"] as? Int { detail = "offline · seen \(agoText(ago))" }
            else {
                let caps = ((w["capabilities"] as? [String]) ?? []).joined(separator: " ")
                detail = "\(state == "online" ? "idle" : state)\(caps.isEmpty ? "" : " · \(caps)")"
            }
            let dl = label(detail, monoFont(11), C.tx3)
            dl.frame = NSRect(x: inset + 82, y: y + 1, width: W - 82 - 2 * inset, height: 14); view.addSubview(dl)
            y += 22
        }
        y += 6

        // pending pairing requests: a compact amber row each — pairing works from
        // the menu bar without opening the dashboard. Hub-central, so only the hub's
        // popover offers to confirm (a paired worker's popover just shows the fleet).
        pairReqs = isHubBox() ? ((fleet?["pairing"] as? [[String: Any]]) ?? []) : []
        for (i, p) in pairReqs.prefix(3).enumerated() {
            let row = NSView(frame: NSRect(x: inset, y: y, width: W - 2 * inset, height: 38))
            row.wantsLayer = true
            row.layer?.backgroundColor = C.warn.withAlphaComponent(0.05).cgColor
            row.layer?.cornerRadius = 9
            row.layer?.borderWidth = 1
            row.layer?.borderColor = C.warn.withAlphaComponent(0.3).cgColor
            let box = label("◈", monoFont(11, .bold), C.warn)
            box.alignment = .center
            box.wantsLayer = true
            box.layer?.backgroundColor = C.warn.withAlphaComponent(0.14).cgColor
            box.layer?.cornerRadius = 6
            box.frame = NSRect(x: 8, y: 8, width: 22, height: 22); row.addSubview(box)
            let t = NSTextField(labelWithString: "")
            let at = NSMutableAttributedString(string: (p["name"] as? String) ?? "?",
                attributes: [.font: monoFont(12, .semibold), .foregroundColor: C.tx])
            at.append(NSAttributedString(string: " wants to join",
                attributes: [.font: NSFont.systemFont(ofSize: 12), .foregroundColor: C.body]))
            t.attributedStringValue = at; t.lineBreakMode = .byTruncatingTail
            t.frame = NSRect(x: 38, y: 11, width: row.frame.width - 38 - 92, height: 16); row.addSubview(t)
            let b = flatButton("Enter code…", fill: C.warn.withAlphaComponent(0.14), text: C.warn, border: nil,
                               font: .systemFont(ofSize: 11, weight: .semibold))
            b.target = self; b.action = #selector(enterPairCode(_:)); b.tag = i
            b.frame = NSRect(x: row.frame.width - 90, y: 8, width: 82, height: 22); row.addSubview(b)
            view.addSubview(row)
            y += 47
        }

        // token budget mini-panel
        let spend = fleet?["spend"] as? [String: Any]
        let spent = (spend?["spent"] as? Int) ?? 0
        let cap = (spend?["cap"] as? Int) ?? 0
        let pct = cap > 0 ? min(1, CGFloat(spent) / CGFloat(cap)) : 0
        let panel = NSView(frame: NSRect(x: inset, y: y, width: W - 2 * inset, height: 44))
        panel.wantsLayer = true
        panel.layer?.backgroundColor = C.well.cgColor
        panel.layer?.cornerRadius = 9
        panel.layer?.borderWidth = 1
        panel.layer?.borderColor = NSColor.white.withAlphaComponent(0.06).cgColor
        let bl = microlabel("tokens 24h")
        bl.frame = NSRect(x: 11, y: 24, width: 100, height: 12); panel.addSubview(bl)
        let bv = label(cap > 0 ? "\(fmtTok(spent)) / \(fmtTok(cap))" : "—", monoFont(10), C.tx2)
        bv.alignment = .right
        bv.frame = NSRect(x: panel.frame.width - 121, y: 24, width: 110, height: 12); panel.addSubview(bv)
        let track = NSView(frame: NSRect(x: 11, y: 12, width: panel.frame.width - 22, height: 4))
        track.wantsLayer = true
        track.layer?.backgroundColor = NSColor.white.withAlphaComponent(0.08).cgColor
        track.layer?.cornerRadius = 2
        let fill = NSView(frame: NSRect(x: 0, y: 0, width: track.frame.width * pct, height: 4))
        fill.wantsLayer = true
        fill.layer?.backgroundColor = (pct >= 1 ? C.bad : C.acc).cgColor
        fill.layer?.cornerRadius = 2
        track.addSubview(fill); panel.addSubview(track)
        view.addSubview(panel)
        y += 54

        // footer: Open Dashboard · Pause all · kill switch
        killSwitchOn = (fleet?["kill_switch"] as? Bool) ?? false
        let open = flatButton("Open Dashboard", fill: NSColor.white.withAlphaComponent(0.08), text: C.tx, border: nil,
                              font: .systemFont(ofSize: 12, weight: .semibold))
        open.target = self; open.action = #selector(openDashboard)
        open.frame = NSRect(x: inset, y: y, width: W - 2 * inset, height: 26); view.addSubview(open)
        y += 32
        // Two pause scopes on their own row so each label states what it stops:
        // "Pause this Mac" drops a local KILL file (halts only this box's loop);
        // "Pause fleet" is the hub-wide kill switch (no worker claims new work).
        let half = (W - 2 * inset - 8) / 2
        let pause = flatButton(killEngaged ? "Resume this Mac" : "Pause this Mac", fill: nil, text: C.tx2,
                               border: NSColor.white.withAlphaComponent(0.12), font: .systemFont(ofSize: 12))
        pause.toolTip = killEngaged ? "This Mac is paused. Click to resume its loop."
                                    : "Pause this Mac only — stops this machine's loop. Other workers keep running."
        pause.target = self; pause.action = #selector(pauseAll)
        pause.frame = NSRect(x: inset, y: y, width: half, height: 26); view.addSubview(pause)
        let kill = flatButton(killSwitchOn ? "◍ Resume fleet" : "◍ Pause fleet",
                              fill: killSwitchOn ? C.bad.withAlphaComponent(0.15) : nil, text: C.bad,
                              border: C.bad.withAlphaComponent(killSwitchOn ? 0.6 : 0.3),
                              font: .systemFont(ofSize: 12))
        kill.toolTip = killSwitchOn ? "Kill switch is ON — no worker in the fleet claims new work. Click to resume."
                                    : "Kill switch: stop the whole fleet from claiming new work"
        kill.target = self; kill.action = #selector(toggleKillSwitch)
        kill.frame = NSRect(x: inset + half + 8, y: y, width: half, height: 26); view.addSubview(kill)
        y += 34

        // version footer: this app's build (hub version lives in the dashboard header),
        // plus the self-update affordance when the hub is advertising a newer build.
        let verL = label("v\(APP_VERSION)", monoFont(10), C.tx3)
        verL.frame = NSRect(x: inset, y: y + 3, width: 120, height: 13)
        view.addSubview(verL)
        if updating || autoUpdating {
            let u = label("updating…", monoFont(10), C.tx3)
            u.alignment = .right
            u.frame = NSRect(x: W - inset - 140, y: y + 3, width: 140, height: 13); view.addSubview(u)
        } else if let e = updateError {
            let u = label("update failed: \(e)", monoFont(10), C.bad)
            u.alignment = .right; u.lineBreakMode = .byTruncatingTail
            u.frame = NSRect(x: W - inset - 220, y: y + 3, width: 220, height: 13); view.addSubview(u)
        } else if let uv = updateVersion {
            let b = flatButton("Update to v\(uv)", fill: C.acc.withAlphaComponent(0.14), text: C.acc,
                               border: nil, font: .systemFont(ofSize: 11, weight: .semibold))
            b.target = self; b.action = #selector(installUpdate)
            b.frame = NSRect(x: W - inset - 110, y: y, width: 110, height: 20); view.addSubview(b)
        }
        y += 24

        view.setFrameSize(NSSize(width: W, height: y))
        preferredContentSize = NSSize(width: W, height: y)
        popover?.contentSize = NSSize(width: W, height: y)
    }

    func addHairline(_ y: CGFloat) {
        let h = NSView(frame: NSRect(x: 0, y: y, width: W, height: 1))
        h.wantsLayer = true; h.layer?.backgroundColor = C.hairline.cgColor
        view.addSubview(h)
    }
    func fmtTok(_ n: Int) -> String {
        n >= 1_000_000 ? String(format: "%.1fM", Double(n) / 1_000_000)
            : n >= 1_000 ? "\(n / 1_000)k" : "\(n)"
    }
    func agoText(_ sec: Int) -> String {
        sec < 60 ? "\(sec)s ago" : sec < 3600 ? "\(sec / 60)m ago" : "\(sec / 3600)h ago"
    }

    @objc func openTicket(_ sender: NSButton) {
        if let u = URL(string: hubBase() + "/ticket/\(sender.tag)") { NSWorkspace.shared.open(u) }
    }
    @objc func openDashboard() { if let u = URL(string: hubBase()) { NSWorkspace.shared.open(u) } }
    @objc func openFullWindow() { openWindow?() }
    @objc func pauseAll() { toggleKill(); refresh() }
    @objc func toggleKillSwitch() {
        apiPOST("/api/kill", body: ["on": !killSwitchOn]) { _ in self.refresh() }
    }

    // --- hub side: type the code a joining worker is displaying ---------------
    @objc func enterPairCode(_ sender: NSButton) {
        guard sender.tag < pairReqs.count else { return }
        promptPairCode(pairReqs[sender.tag])
    }
    func promptPairCode(_ p: [String: Any], error: String? = nil) {
        let name = (p["name"] as? String) ?? "worker"
        let a = NSAlert()
        a.messageText = "Pair \(name)"
        a.informativeText = (error.map { $0 + "\n\n" } ?? "")
            + "Type the 6-character code shown on that machine."
        a.addButton(withTitle: "Pair"); a.addButton(withTitle: "Cancel")
        let f = NSTextField(frame: NSRect(x: 0, y: 0, width: 220, height: 24))
        f.placeholderString = "e.g. 7KF-P2M"
        a.accessoryView = f
        NSApp.activate(ignoringOtherApps: true)
        guard a.runModal() == .alertFirstButtonReturn else { return }
        apiPOST("/api/pair-confirm",
                body: ["request_id": (p["request_id"] as? String) ?? "", "code": f.stringValue]) { ok in
            if ok { self.refresh() }
            else { self.promptPairCode(p, error: "That code didn't match (or the request expired).") }
        }
    }

    // --- worker side: discover → request → display code → poll → store token --
    func renderPairing() {
        view.subviews.forEach { $0.removeFromSuperview() }
        var y: CGFloat = 0
        let inset: CGFloat = 15

        let dot = label("●", .systemFont(ofSize: 10), C.acc)
        dot.frame = NSRect(x: inset, y: 15, width: 12, height: 14); view.addSubview(dot)
        let mark = NSTextField(labelWithString: "")
        let m = NSMutableAttributedString(string: "outer", attributes: [.font: monoFont(13, .semibold), .foregroundColor: C.tx])
        m.append(NSAttributedString(string: "loop", attributes: [.font: monoFont(13, .semibold), .foregroundColor: C.acc]))
        mark.attributedStringValue = m; mark.isBordered = false; mark.isEditable = false; mark.drawsBackground = false
        mark.frame = NSRect(x: inset + 16, y: 13, width: 80, height: 17); view.addSubview(mark)
        let roleL = label("worker · unpaired", monoFont(11), C.tx3)
        roleL.frame = NSRect(x: inset + 98, y: 14, width: 130, height: 14); view.addSubview(roleL)
        let gear = flatButton("⚙", fill: nil, text: C.tx2, border: nil, font: .systemFont(ofSize: 13))
        gear.target = self; gear.action = #selector(openFullWindow)
        gear.frame = NSRect(x: W - 40, y: 10, width: 26, height: 22); view.addSubview(gear)
        y = 41
        addHairline(y); y += 12

        switch pairPhase {
        case .idle:
            let h = microlabel("pair this mac", C.warn)
            h.frame = NSRect(x: inset, y: y, width: 200, height: 14); view.addSubview(h)
            y += 21
            if let n = pairNote {
                let w = label(n, monoFont(11), C.warn)
                w.frame = NSRect(x: inset, y: y, width: W - 2 * inset, height: 14); view.addSubview(w)
                y += 20
            }
            if discovery.hubs.isEmpty {
                let s = label("searching for hubs on this network…", monoFont(11), C.tx3)
                s.frame = NSRect(x: inset, y: y, width: W - 2 * inset, height: 14); view.addSubview(s)
                y += 24
            }
            for (i, hub) in discovery.hubs.enumerated() {
                let d = label("●", monoFont(10), C.acc)
                d.frame = NSRect(x: inset, y: y + 4, width: 12, height: 12); view.addSubview(d)
                let n = label(hub.name, monoFont(12, .semibold), C.tx)
                n.frame = NSRect(x: inset + 17, y: y + 3, width: 70, height: 15); view.addSubview(n)
                let dl = label(hub.detail, monoFont(10), C.tx3)
                dl.frame = NSRect(x: inset + 90, y: y + 4, width: W - 90 - 72 - 2 * inset, height: 13)
                view.addSubview(dl)
                let b = flatButton("Join", fill: C.acc.withAlphaComponent(0.14), text: C.acc, border: nil,
                                   font: .systemFont(ofSize: 11, weight: .semibold))
                b.target = self; b.action = #selector(joinHub(_:)); b.tag = i
                b.frame = NSRect(x: W - inset - 52, y: y, width: 52, height: 22); view.addSubview(b)
                y += 30
            }
            y += 6

        case .waiting(let s):
            let t = NSTextField(labelWithString: "")
            let at = NSMutableAttributedString(string: "Enter this code on ",
                attributes: [.font: NSFont.systemFont(ofSize: 12), .foregroundColor: C.body])
            at.append(NSAttributedString(string: s.hub.name,
                attributes: [.font: monoFont(12, .semibold), .foregroundColor: C.tx]))
            at.append(NSAttributedString(string: " → Fleet",
                attributes: [.font: NSFont.systemFont(ofSize: 12), .foregroundColor: C.body]))
            t.attributedStringValue = at
            t.frame = NSRect(x: inset, y: y, width: W - 2 * inset, height: 16); view.addSubview(t)
            y += 26
            // 6 green-bordered cells, grouped 3–3 with a dash
            let cellW: CGFloat = 34, cellH: CGFloat = 44, gap: CGFloat = 6, dashW: CGFloat = 14
            let total = 6 * cellW + 5 * gap + dashW
            var x = (W - total) / 2
            for (i, ch) in s.code.enumerated() {
                if i == 3 {
                    let dash = label("–", monoFont(18), C.tx3)
                    dash.alignment = .center
                    dash.frame = NSRect(x: x, y: y + 11, width: dashW, height: 22)
                    view.addSubview(dash)
                    x += dashW + gap
                }
                let cell = label(String(ch), monoFont(22, .bold), C.acc)
                cell.alignment = .center
                cell.wantsLayer = true
                cell.layer?.backgroundColor = C.well.cgColor
                cell.layer?.cornerRadius = 7
                cell.layer?.borderWidth = 1
                cell.layer?.borderColor = C.acc.withAlphaComponent(0.5).cgColor
                cell.frame = NSRect(x: x, y: y, width: cellW, height: cellH)
                view.addSubview(cell)
                x += cellW + gap
            }
            y += cellH + 12
            let left = max(0, Int(s.expiresAt.timeIntervalSinceNow))
            let cd = label(left > 0 ? String(format: "expires in %d:%02d", left / 60, left % 60)
                                    : "expired", monoFont(11), C.warn)
            cd.alignment = .center
            cd.frame = NSRect(x: inset, y: y, width: W - 2 * inset, height: 14); view.addSubview(cd)
            y += 24
            let cancel = flatButton("Cancel", fill: nil, text: C.tx2,
                                    border: NSColor.white.withAlphaComponent(0.12),
                                    font: .systemFont(ofSize: 12))
            cancel.target = self; cancel.action = #selector(cancelPairing)
            cancel.frame = NSRect(x: (W - 90) / 2, y: y, width: 90, height: 26); view.addSubview(cancel)
            y += 34

        case .done(let name):
            let ok = label("● paired as \(name)", monoFont(12, .semibold), C.acc)
            ok.frame = NSRect(x: inset, y: y, width: W - 2 * inset, height: 16); view.addSubview(ok)
            y += 22
            let sub = label("worker daemon restarting — it appears in Fleet on its next heartbeat",
                            .systemFont(ofSize: 11), C.tx3)
            sub.frame = NSRect(x: inset, y: y, width: W - 2 * inset, height: 15); view.addSubview(sub)
            y += 26
            let open = flatButton("Open Dashboard", fill: NSColor.white.withAlphaComponent(0.08),
                                  text: C.tx, border: nil,
                                  font: .systemFont(ofSize: 12, weight: .semibold))
            open.target = self; open.action = #selector(openDashboard)
            open.frame = NSRect(x: inset, y: y, width: 150, height: 26); view.addSubview(open)
            y += 36

        case .failed(let msg):
            let e = label(msg, .systemFont(ofSize: 12), C.bad)
            e.frame = NSRect(x: inset, y: y, width: W - 2 * inset, height: 16); view.addSubview(e)
            y += 26
            let retry = flatButton("Try again", fill: NSColor.white.withAlphaComponent(0.08),
                                   text: C.tx, border: nil,
                                   font: .systemFont(ofSize: 12, weight: .semibold))
            retry.target = self; retry.action = #selector(retryPairing)
            retry.frame = NSRect(x: inset, y: y, width: 100, height: 26); view.addSubview(retry)
            y += 36
        }

        // the manual flow stays for hubs this browse can't see (other subnets, relay)
        if case .waiting = pairPhase {} else {
            addHairline(y); y += 9
            let manual = flatButton("Enter a hub URL manually →", fill: nil, text: C.info, border: nil,
                                    font: .systemFont(ofSize: 11))
            manual.target = self; manual.action = #selector(manualSetup)
            manual.frame = NSRect(x: inset - 6, y: y, width: 190, height: 20); view.addSubview(manual)
            y += 30
        }

        view.setFrameSize(NSSize(width: W, height: y))
        preferredContentSize = NSSize(width: W, height: y)
        popover?.contentSize = NSSize(width: W, height: y)
    }

    @objc func joinHub(_ sender: NSButton) {
        guard sender.tag < discovery.hubs.count, case .idle = pairPhase else { return }
        let hub = discovery.hubs[sender.tag]
        let code = makePairCode()
        let salt = Data((0..<32).map { _ in UInt8.random(in: 0...255) })
        let name = (ProcessInfo.processInfo.hostName.split(separator: ".").first.map(String.init) ?? "mac")
            .lowercased()
        let osv = ProcessInfo.processInfo.operatingSystemVersion
        let info = "macOS \(osv.majorVersion).\(osv.minorVersion) · \(machineArch())"
        httpJSON("POST", hub.base + "/api/pair/request", body: [
            "name": name, "host_info": info,
            "salt": salt.hexString, "code_check": pairCodeCheck(code, salt),
        ]) { j in
            guard let rid = j?["request_id"] as? String else {
                self.pairPhase = .failed((j?["error"] as? String) ?? "hub refused the pairing request")
                self.renderPairing()
                return
            }
            let ttl = (j?["expires_in"] as? Int) ?? 120
            self.pairPhase = .waiting(PairSession(
                hub: hub, requestId: rid, code: code, salt: salt,
                expiresAt: Date().addingTimeInterval(TimeInterval(ttl)), workerName: name))
            self.startPairPoll()
            self.renderPairing()
        }
    }

    func machineArch() -> String {
        var uts = utsname()
        uname(&uts)
        return withUnsafePointer(to: &uts.machine) {
            $0.withMemoryRebound(to: CChar.self, capacity: 1) { String(cString: $0) }
        }
    }

    func startPairPoll() {
        pairPoll?.invalidate()
        pairPoll = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { _ in self.pollPair() }
        pairTick?.invalidate()
        pairTick = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in
            // countdown redraw, only while visible
            if case .waiting = self.pairPhase, self.view.window != nil { self.renderPairing() }
        }
    }

    func pollPair() {
        guard case .waiting(let s) = pairPhase else { pairPoll?.invalidate(); return }
        httpJSON("GET", s.hub.base + "/api/pair/status/\(s.requestId)") { j in
            guard case .waiting = self.pairPhase else { return }
            let state = (j?["state"] as? String) ?? "pending"
            if state == "confirmed" {
                guard let enc = j?["token_enc"] as? String, let mac = j?["mac"] as? String else {
                    self.failPairing("token delivery malformed — try again")
                    return
                }
                // pairKey is 100k PBKDF2 rounds — decrypt off the main thread so the
                // popover doesn't hitch, then hop back to finish.
                DispatchQueue.global(qos: .userInitiated).async {
                    let token = pairDecrypt(code: s.code, salt: s.salt, cipherHex: enc, macHex: mac)
                    DispatchQueue.main.async {
                        guard case .waiting = self.pairPhase else { return }
                        if let token = token { self.finishPairing(s, token: token) }
                        else { self.failPairing("token verification failed — try again") }
                    }
                }
            } else if state == "expired" || Date() > s.expiresAt {
                self.failPairing("request expired — try again")
            }
        }
    }

    func failPairing(_ msg: String) {
        pairPoll?.invalidate()
        pairPhase = .failed(msg)
        if view.window != nil { renderPairing() }
    }

    func finishPairing(_ s: PairSession, token: String) {
        pairPoll?.invalidate()
        pairTick?.invalidate()
        writeHubURL(s.hub.base)
        if isPkg {
            _ = setWorkerEnv(["OUTERLOOP_WORKER": s.workerName, "OUTERLOOP_WORKER_TOKEN": token,
                              "OUTERLOOP_HUB": s.hub.base])
            run("/bin/launchctl", ["bootout", "gui/\(uid)/com.outerloop.worker"])
            run("/bin/launchctl", ["bootstrap", "gui/\(uid)", workerPlist])
        } else {
            writeSettings(["worker": s.workerName, "token": token])  // same keys `outerloop local` writes
            restartWorkerDaemon()
        }
        pairPhase = .done(s.workerName)
        discovery.stop()
        if view.window != nil { renderPairing() }
    }

    @objc func cancelPairing() {
        pairPoll?.invalidate()
        pairTick?.invalidate()
        pairPhase = .idle
        renderPairing()
    }
    @objc func retryPairing() {
        pairPhase = .idle
        discovery.stop()   // a failed attempt may have left the browser running
        discovery.start()
        renderPairing()
    }
    @objc func manualSetup() { openSettings?() }
}

// =====================================================================
// The app: status item → popover (the glance) → full window (sidebar:
// Tasks / Settings / Setup, with the launchd status block pinned bottom-left).
// =====================================================================
final class Controller: NSObject, NSWindowDelegate {
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    let popover = NSPopover()
    let popoverPane = PopoverPane()
    let window: NSWindow
    let tasksPane = TasksPane()
    let settingsPane = SettingsPane()
    let setupPane = SetupPane()
    let statusBlock = NSTextField(labelWithString: "")
    var navButtons: [NSButton] = []
    var panes: [NSView] = []

    override init() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 800, height: WIN_H),
                          styleMask: [.titled, .closable, .miniaturizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        super.init()
        window.title = "outerloop"
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.appearance = NSAppearance(named: .darkAqua)
        window.backgroundColor = C.bg
        window.isMovableByWindowBackground = true
        window.delegate = self
        window.isReleasedWhenClosed = false
        buildWindow()

        popover.behavior = .transient
        popover.appearance = NSAppearance(named: .darkAqua)
        popover.contentViewController = popoverPane
        popoverPane.popover = popover
        popoverPane.openWindow = { [weak self] in
            self?.popover.performClose(nil)
            self?.showWindow()
        }
        popoverPane.openSettings = { [weak self] in
            self?.popover.performClose(nil)
            self?.showWindow()
            self?.showPane(1)
        }

        item.button?.target = self
        item.button?.action = #selector(togglePopover)
        refreshIcon()
        Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { _ in self.refreshIcon(); self.refreshStatusBlock() }
        // Hands-off auto-update: ask the hub its version and swap ourselves when it's
        // ahead. 15s after launch (let the network/hub settle), then every 10 min.
        DispatchQueue.main.asyncAfter(deadline: .now() + 15) { [weak self] in self?.autoUpdatePoll() }
        Timer.scheduledTimer(withTimeInterval: 600, repeats: true) { _ in self.autoUpdatePoll() }
        NotificationCenter.default.addObserver(self, selector: #selector(startAll), name: .olStartAll, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(stopAll), name: .olStopAll, object: nil)
        DispatchQueue.main.async { [weak self] in self?.configureHubIfNeeded() }
    }

    func buildWindow() {
        guard let c = window.contentView else { return }
        c.wantsLayer = true

        // sidebar
        let side = NSView(frame: NSRect(x: 0, y: 0, width: 180, height: WIN_H))
        side.wantsLayer = true
        side.layer?.backgroundColor = C.sidebar.cgColor
        c.addSubview(side)
        let border = NSView(frame: NSRect(x: 179, y: 0, width: 1, height: WIN_H))
        border.wantsLayer = true
        border.layer?.backgroundColor = NSColor.white.withAlphaComponent(0.06).cgColor
        c.addSubview(border)

        settingsPane.build()
        panes = [tasksPane.view, settingsPane.view, setupPane.view]
        var y: CGFloat = WIN_H - 78
        for i in 0..<3 {
            let b = NSButton(title: "", target: self, action: #selector(selectTab(_:)))
            b.tag = i
            b.isBordered = false; b.wantsLayer = true
            b.layer?.cornerRadius = 7
            b.attributedTitle = navTitle(i, active: false)
            b.frame = NSRect(x: 8, y: y, width: 164, height: 28)
            side.addSubview(b)
            navButtons.append(b)
            y -= 31
        }

        // launchd status block pinned to the sidebar's bottom (the old footer's job)
        let block = NSView(frame: NSRect(x: 8, y: 12, width: 164, height: 54))
        block.wantsLayer = true
        block.layer?.backgroundColor = C.deep.cgColor
        block.layer?.cornerRadius = 8
        block.layer?.borderWidth = 1
        block.layer?.borderColor = NSColor.white.withAlphaComponent(0.06).cgColor
        statusBlock.frame = NSRect(x: 10, y: 8, width: 144, height: 38)
        statusBlock.maximumNumberOfLines = 2
        statusBlock.cell?.wraps = true
        block.addSubview(statusBlock)
        side.addSubview(block)

        // app version, tucked above the status block
        let verL = label("v\(APP_VERSION)", monoFont(10), C.tx3)
        verL.frame = NSRect(x: 18, y: 72, width: 144, height: 13)
        side.addSubview(verL)

        for p in panes {
            p.setFrameOrigin(NSPoint(x: 180, y: 0))
            p.isHidden = true
            c.addSubview(p)
        }
        showPane(0)
        refreshStatusBlock()
    }

    // NSButton centers attributed titles regardless of `alignment` — bake a
    // left-aligned paragraph style into the title instead.
    func navTitle(_ i: Int, active: Bool) -> NSAttributedString {
        let (glyph, name) = [("▤", "Tasks"), ("⚙", "Settings"), ("✓", "Setup")][i]
        let para = NSMutableParagraphStyle(); para.alignment = .left
        let at = NSMutableAttributedString(string: "  \(glyph)  ", attributes:
            [.font: monoFont(11), .foregroundColor: active ? C.acc : C.tx3, .paragraphStyle: para])
        at.append(NSAttributedString(string: name, attributes:
            [.font: NSFont.systemFont(ofSize: 13), .foregroundColor: active ? C.tx : C.tx2,
             .paragraphStyle: para]))
        return at
    }

    func showPane(_ i: Int) {
        for (j, p) in panes.enumerated() { p.isHidden = j != i }
        for (j, b) in navButtons.enumerated() {
            let active = j == i
            b.layer?.backgroundColor = active ? C.acc.withAlphaComponent(0.08).cgColor : NSColor.clear.cgColor
            b.attributedTitle = navTitle(j, active: active)
        }
        if i == 0 { tasksPane.start() } else { tasksPane.stop() }
        if i == 2 { setupPane.refresh() }
    }
    @objc func selectTab(_ sender: NSButton) { showPane(sender.tag) }

    func refreshStatusBlock() {
        // Attributed so each dot shows state color (green/gray), like the menu-bar icon.
        let dim: [NSAttributedString.Key: Any] = [.font: monoFont(10), .foregroundColor: C.tx3]
        let s = NSMutableAttributedString()
        for (i, label) in labels.enumerated() {
            let running = isRunning(label)
            s.append(NSAttributedString(string: running ? "●" : "○",
                attributes: [.font: monoFont(10), .foregroundColor: running ? C.acc : C.tx3]))
            let name = label == brewLabel ? "service"
                : label.replacingOccurrences(of: "com.outerloop.", with: "")
            s.append(NSAttributedString(string: " \(name)" + (i < labels.count - 1 ? "  " : ""), attributes: dim))
        }
        s.append(NSAttributedString(string: "\nrole \(role)", attributes: dim))
        if role == "worker" {
            s.append(NSAttributedString(string: " · \(readHubURL() ?? "(hub not set)")", attributes: dim))
        }
        statusBlock.attributedStringValue = s
    }

    func autoUpdatePoll() {
        apiGET("/api/fleet") { j in maybeAutoUpdate(to: j?["version"] as? String) }
    }

    func refreshIcon() {
        let up = labels.allSatisfy { isRunning($0) }
        let dot = killEngaged ? "◍" : (up ? "●" : "○")
        let color: NSColor = killEngaged ? .systemRed : (up ? .systemGreen : .systemGray)
        item.button?.attributedTitle = NSAttributedString(
            string: dot, attributes: [.foregroundColor: color, .font: NSFont.systemFont(ofSize: 14)])
        item.button?.toolTip = "outerloop (\(role))"
    }

    @objc func togglePopover() {
        if popover.isShown { popover.performClose(nil); return }
        guard let btn = item.button else { return }
        popover.show(relativeTo: btn.bounds, of: btn, preferredEdge: .minY)
    }

    func showWindow() {
        if !window.isVisible { window.center() }
        NSApp.setActivationPolicy(.regular)   // window open → show Dock icon + app-switcher entry
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        tasksPane.start()          // begin polling when shown (idempotent)
        refreshStatusBlock()
    }
    // Stop polling and drop back to menu-bar-only (no Dock icon) when the window is hidden.
    func windowWillClose(_ notification: Notification) {
        tasksPane.stop()
        NSApp.setActivationPolicy(.accessory)
    }

    func configureHubIfNeeded() {   // first-run: worker with no hub set
        guard role == "worker", readHubURL() == nil else { return }
        if let url = promptHubURL("") { writeHubURL(url); restartWorkerDaemon() }
    }
    func workerConfigMissing() -> Bool {
        return (readHubURL() ?? workerEnv()["OUTERLOOP_HUB"] ?? "").isEmpty
            || myWorker().isEmpty || myToken().isEmpty
    }
    @objc func startAll() {
        if role == "worker", workerConfigMissing() { showWindow(); showPane(1); return }
        labels.forEach(startAgent); refreshIcon(); refreshStatusBlock()
    }
    @objc func stopAll() { labels.forEach(stopAgent); refreshIcon(); refreshStatusBlock() }
}

// A standard main menu so the usual editing key-equivalents (⌘C/⌘V/⌘X/⌘A/⌘Z) reach
// the first-responder text field. Without an Edit menu AppKit has nowhere to route
// them, so copy/paste silently does nothing in every field. The menu bar itself only
// shows while the window is key (the app is otherwise an accessory).
func installMainMenu() {
    let main = NSMenu()

    let appItem = NSMenuItem(); main.addItem(appItem)
    let appMenu = NSMenu(); appItem.submenu = appMenu
    appMenu.addItem(withTitle: "About outerloop",
                    action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
    appMenu.addItem(.separator())
    appMenu.addItem(withTitle: "Hide outerloop", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
    appMenu.addItem(withTitle: "Quit outerloop", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

    let editItem = NSMenuItem(); main.addItem(editItem)
    let edit = NSMenu(title: "Edit"); editItem.submenu = edit
    edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
    let redo = edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
    redo.keyEquivalentModifierMask = [.command, .shift]
    edit.addItem(.separator())
    edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
    edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
    edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
    edit.addItem(withTitle: "Delete", action: #selector(NSText.delete(_:)), keyEquivalent: "")
    edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")

    NSApp.mainMenu = main
}

// Brew first run: no role chosen on this box yet. Ask once — a GUI stand-in for
// `outerloop local role …` — and persist it. Picking Hub finishes the whole setup with
// no terminal step: it mints the dashboard password HERE (the daemon would otherwise
// generate one and announce it only in the brew-services log, locking the user out of
// their own board) and (re)starts the service so the choice takes effect even if the
// daemon was already running as the unset-role loopback default.
func firstRunRolePicker() {
    guard !isPkg, env["ROLE"] == nil, readSetting("role") == nil else { return }
    // The CLI shim installed by the formula, next to brew itself.
    let outerloopBin = URL(fileURLWithPath: brewBin)
        .deletingLastPathComponent().appendingPathComponent("outerloop").path
    let a = NSAlert()
    a.messageText = "Set up this Mac"
    a.informativeText = "Run this machine as the fleet Hub — it serves your other Macs over "
        + "the LAN in real mode, with auth on and a dashboard password. \u{201C}Hub + Worker\u{201D} "
        + "also does work on this Mac itself. Or join an existing hub as a Worker."
    a.addButton(withTitle: "Hub")            // .alertFirstButtonReturn
    a.addButton(withTitle: "Hub + Worker")   // .alertSecondButtonReturn
    a.addButton(withTitle: "Worker")         // .alertThirdButtonReturn
    a.addButton(withTitle: "Decide Later")   // 4th → leave unset → stays FAKE-safe loopback
    NSApp.activate(ignoringOtherApps: true)
    let resp = a.runModal()
    switch resp {
    case .alertFirstButtonReturn, .alertSecondButtonReturn:
        // Hub and Hub+Worker are the same hardened hub setup; the latter also runs a
        // co-located worker (role=both), which `outerloop service` provisions on start.
        let both = (resp == .alertSecondButtonReturn)
        writeSettings(["role": both ? "both" : "hub"])
        let pw = String(UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(16))
        run(outerloopBin, ["config", "ui_token", pw])   // set BEFORE the daemon can generate one
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(pw, forType: .string)
        let done = NSAlert()
        done.messageText = both ? "This Mac is a hub + worker" : "This Mac is the hub"
        done.informativeText = "Dashboard password (copied to your clipboard):\n\n\(pw)\n\n"
            + (both ? "It runs the hub and also does work itself. " : "")
            + "Board: http://\(ProcessInfo.processInfo.hostName):8765 — other Macs join from "
            + "its Fleet page. View the password anytime with `outerloop status`; change it "
            + "with `outerloop config ui_token`."
        done.addButton(withTitle: both ? "Start Node" : "Start Hub")
        done.runModal()
        run(brewBin, ["services", "restart", "outerloop"])   // restart = start if stopped
    case .alertThirdButtonReturn:
        writeSettings(["role": "worker"])   // Controller then prompts for the hub URL
    default: break
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)   // menu-bar only, no Dock icon
installMainMenu()
// Default open-at-login on first launch only; a later manual un-check sticks.
if #available(macOS 13, *), !UserDefaults.standard.bool(forKey: "didDefaultLoginItem") {
    if SMAppService.mainApp.status == .notRegistered { setLoginItem(true) }
    UserDefaults.standard.set(true, forKey: "didDefaultLoginItem")
}
firstRunRolePicker()                   // choose hub/worker before the window builds
let controller = Controller()
app.run()
