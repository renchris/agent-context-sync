// agentsync-launcher: the LaunchAgent's Program (design C15 §3, requirements 17-21).
//
// Why it exists. Reads of ~/Library/CloudStorage/<domain> from a launchd job go through TCC's
// kTCCServiceFileProviderDomain, decided for the *responsible process*: the executable launchd starts.
// An Apple platform binary (/usr/bin/python3) gets EPERM with nothing to approve; an unapproved
// third-party binary raises a user prompt and open() blocks until someone answers it; an approved one
// reads normally. PPPC has no FileProviderDomain key, but a Full Disk Access grant (SystemPolicyAllFiles)
// on the responsible process is checked first and pre-empts it. So one small signed executable with a
// stable identifier is the TCC subject, and Python runs as its child (children inherit the responsible
// process). A grant or PPPC entry then names this launcher, never an interpreter that runs any script.
//
// What it does, in order:
//   1. --canary PATH (repeatable): before anything else, lstat PATH, then list one entry of a directory or
//      open-and-close any other file (no byte is read, materialisation is OFF so nothing hydrates, nothing
//      is written: the same TCC decision a reader gets, without its side effects). Each
//      canary runs on a worker thread with a --canary-timeout (default 10 s). A canary that has not
//      returned is logged "TCC_PENDING reason=canary" and the launcher exits 79 without starting the
//      child: an unanswered prompt must never read as an empty folder, nor hang the job forever.
//      EPERM/EACCES logs "TCC_DENIED" (the child still runs; it records the source as unreadable).
//   2. posix_spawn the program after "--" (absolute path, no PATH lookup) with the same environment,
//      signal mask cleared and the forwarded signals reset to their defaults.
//   3. A hard wall-clock watchdog (--timeout, default 14400 s, 0 = none; DispatchWallTime, so it counts
//      across sleep): on expiry log "TCC_PENDING reason=watchdog", SIGTERM the child, SIGKILL it after
//      --grace seconds, exit 79.
//   4. SIGTERM/SIGINT/SIGHUP/SIGUSR1/SIGUSR2 are forwarded to the child. The child's exit status is the
//      launcher's; a child killed by a forwardable signal re-raises that signal on the launcher.
//
// The child is PINNED (review deploy-ops-launcher-arbitrary-program-trampoline): a TCC / Full Disk Access
// grant on this launcher must never become a grant for any program a same-user process names after "--".
// The signed bundle's Info.plist (sealed by the code signature) carries AgentSyncAllowedProgram, the one
// interpreter install.sh built it for; the child must be exactly that path followed by childPrefix
// ("-I -X utf8 -m agentsync sync": isolated mode, so PYTHONPATH/PYTHONSTARTUP and the user site are ignored).
// A bundle without a pin runs nothing unless it was built with ALLOW_ANY_PROGRAM=1 (AgentSyncAllowAnyProgram:
// development and tests only). DYLD_* and PYTHON* are always removed from the child's environment.
// Trust boundary: the pinned interpreter's site-packages (the uv tool environment) are user-writable, so
// code there runs with this launcher's grants; approve the File Provider prompt rather than Full Disk Access.
//
// --canary-only runs step 1 and exits: 0 ok, 79 pending, 80 denied, 66 missing, 74 other I/O error.
// --self-responsible re-spawns the launcher with responsibility disclaimed (the private
// responsibility_spawnattrs_setdisclaim, looked up with dlsym), so a probe run from a terminal is judged
// by the launcher's own TCC grants instead of the terminal's; exit 81 when the call is unavailable.
//
// Exit codes: the child's own, or 64 usage, 66 canary missing (--canary-only), 71 spawn failed,
// 74 canary I/O error (--canary-only), 79 TCC_PENDING, 80 TCC_DENIED (--canary-only), 81 disclaim unavailable.

import Darwin
import Dispatch

let launcherVersion = "1.0.0"
let exUsage: Int32 = 64
let exNoInput: Int32 = 66
let exOSErr: Int32 = 71
let exIOErr: Int32 = 74
let exitTCCPending: Int32 = 79
let exitTCCDenied: Int32 = 80
let exitDisclaimUnavailable: Int32 = 81
let disclaimedMarker = "AGENTSYNC_LAUNCHER_DISCLAIMED"
let childPrefix = ["-I", "-X", "utf8", "-m", "agentsync", "sync"]
let scrubbedPrefixes = ["DYLD_", "PYTHON"]
let forwarded: [Int32] = [SIGTERM, SIGINT, SIGHUP, SIGUSR1, SIGUSR2]

// MARK: logging (one line per event on stderr, which launchd sends to StandardErrorPath)

func timestamp() -> String {
    var now = time(nil)
    var parts = tm()
    gmtime_r(&now, &parts)
    var buf = [CChar](repeating: 0, count: 32)
    let n = strftime(&buf, buf.count, "%Y-%m-%dT%H:%M:%SZ", &parts)
    let bytes = buf[0..<n].map { UInt8(bitPattern: $0) }
    return String(decoding: bytes, as: UTF8.self)
}

func logLine(_ token: String, _ fields: String = "") {
    var line = "\(timestamp()) agentsync-launcher[\(getpid())]: \(token)"
    if !fields.isEmpty { line += " " + fields }
    line += "\n"
    _ = line.withCString { ptr in write(STDERR_FILENO, ptr, strlen(ptr)) }
}

func quoted(_ s: String) -> String {
    return "\"" + s.replacingAll("\\", with: "\\\\").replacingAll("\"", with: "\\\"") + "\""
}

extension String {
    func replacingAll(_ target: Character, with replacement: String) -> String {
        var out = ""
        for ch in self { if ch == target { out += replacement } else { out.append(ch) } }
        return out
    }
}

func errnoText(_ code: Int32) -> String {
    return String(cString: strerror(code))
}

// MARK: options

struct Options {
    var timeout: Double = 14400
    var grace: Double = 30
    var canaries: [String] = []
    var canaryTimeout: Double = 10
    var canaryOnly = false
    var selfResponsible = false
    var program: [String] = []
}

let usage = """
    usage: agentsync-launcher [--timeout S] [--grace S] [--canary PATH]... [--canary-timeout S]
                              [--self-responsible] -- /absolute/program [args...]
           agentsync-launcher [--self-responsible] --canary-only --canary PATH [--canary-timeout S]
           agentsync-launcher --version
    """

func die(_ message: String) -> Never {
    logLine("USAGE", message)
    FileHandleless.err(usage + "\n")
    exit(exUsage)
}

enum FileHandleless {
    static func out(_ s: String) { _ = s.withCString { write(STDOUT_FILENO, $0, strlen($0)) } }
    static func err(_ s: String) { _ = s.withCString { write(STDERR_FILENO, $0, strlen($0)) } }
}

func seconds(_ flag: String, _ value: String?) -> Double {
    guard let value = value, let v = Double(value), v.isFinite, v >= 0 else {
        die("\(flag) needs a number of seconds >= 0")
    }
    return v
}

func parse(_ args: [String]) -> Options {
    var o = Options()
    var i = 0
    while i < args.count {
        let a = args[i]
        let next: String? = i + 1 < args.count ? args[i + 1] : nil
        switch a {
        case "--":
            o.program = Array(args[(i + 1)...])
            i = args.count
            continue
        case "--timeout": o.timeout = seconds(a, next); i += 1
        case "--grace": o.grace = seconds(a, next); i += 1
        case "--canary-timeout":
            o.canaryTimeout = seconds(a, next)
            if o.canaryTimeout <= 0 { die("--canary-timeout must be > 0") }
            i += 1
        case "--canary":
            guard let p = next, p.hasPrefix("/") else { die("--canary needs an absolute path") }
            o.canaries.append(p); i += 1
        case "--canary-only": o.canaryOnly = true
        case "--self-responsible": o.selfResponsible = true
        case "--version":
            FileHandleless.out("agentsync-launcher \(launcherVersion)\n")
            exit(0)
        case "-h", "--help":
            FileHandleless.out(usage + "\n")
            exit(0)
        default:
            die("unknown option \(a)")
        }
        i += 1
    }
    if o.canaryOnly {
        if o.canaries.isEmpty { die("--canary-only needs at least one --canary") }
        if !o.program.isEmpty { die("--canary-only takes no program") }
    } else {
        guard let prog = o.program.first else { die("missing -- /absolute/program") }
        if !prog.hasPrefix("/") { die("program must be an absolute path (no PATH lookup): \(prog)") }
    }
    return o
}

// MARK: child pin (read from the bundle's sealed Info.plist)

/// The text of <bundle>/Contents/Info.plist when this executable is <bundle>/Contents/MacOS/<exe>.
func infoPlistText() -> String? {
    guard let exe = ownExecutablePath() else { return nil }
    guard let slash = exe.lastIndex(of: "/") else { return nil }
    let macos = String(exe[..<slash])
    guard macos.hasSuffix("/Contents/MacOS") else { return nil }
    let plist = String(macos.dropLast("/MacOS".count)) + "/Info.plist"
    guard let fp = fopen(plist, "r") else { return nil }
    defer { fclose(fp) }
    var bytes: [UInt8] = []
    var buf = [UInt8](repeating: 0, count: 4096)
    while true {
        let n = fread(&buf, 1, buf.count, fp)
        if n <= 0 { break }
        bytes += buf[0..<n]
        if bytes.count > 1 << 20 { return nil }
    }
    return String(decoding: bytes, as: UTF8.self)
}

func xmlUnescape(_ s: String) -> String {
    var out = s
    for (from, to) in [("&lt;", "<"), ("&gt;", ">"), ("&quot;", "\""), ("&apos;", "'"), ("&amp;", "&")] {
        out = out.replacingAllSubstrings(from, with: to)
    }
    return out
}

extension String {
    func replacingAllSubstrings(_ target: String, with replacement: String) -> String {
        guard !target.isEmpty else { return self }
        var out = ""
        var rest = Substring(self)
        while let r = rest.range(of: target) {
            out += rest[..<r.lowerBound] + replacement
            rest = rest[r.upperBound...]
        }
        return out + rest
    }
}

extension Substring {
    func range(of target: String) -> Range<Substring.Index>? {
        guard !target.isEmpty, target.count <= self.count else { return nil }
        var i = startIndex
        while i < endIndex {
            if self[i...].hasPrefix(target) {
                return i..<index(i, offsetBy: target.count)
            }
            i = index(after: i)
        }
        return nil
    }
}

/// The value after <key>KEY</key>: the <string> text, "true"/"false" for <true/>/<false/>, nil if absent.
func infoValue(_ text: String, _ key: String) -> String? {
    guard let k = Substring(text).range(of: "<key>\(key)</key>") else { return nil }
    let rest = text[k.upperBound...].drop(while: { $0 == " " || $0 == "\t" || $0 == "\n" || $0 == "\r" })
    if rest.hasPrefix("<true/>") { return "true" }
    if rest.hasPrefix("<false/>") { return "false" }
    if rest.hasPrefix("<string/>") { return "" }
    guard rest.hasPrefix("<string>") else { return nil }
    let body = rest.dropFirst("<string>".count)
    guard let end = body.range(of: "</string>") else { return nil }
    return xmlUnescape(String(body[..<end.lowerBound]))
}

/// Refuse (exit 64) any child the bundle does not allow: see the header comment.
func enforcePin(_ program: [String]) {
    let text = infoPlistText()
    let pinned = text.flatMap { infoValue($0, "AgentSyncAllowedProgram") } ?? ""
    if !pinned.isEmpty {
        let wanted = [pinned] + childPrefix
        if program.count >= wanted.count && Array(program[0..<wanted.count]) == wanted { return }
        logLine(
            "PROGRAM_REFUSED",
            "program=\(quoted(program.first ?? "")) allowed=\(quoted(wanted.joined(separator: " ")))"
        )
        exit(exUsage)
    }
    if text.flatMap({ infoValue($0, "AgentSyncAllowAnyProgram") }) == "true" {
        return  // a development build (build.sh warned when it made it); never grant it TCC access
    }
    logLine(
        "PROGRAM_REFUSED",
        "program=\(quoted(program.first ?? "")) reason=\(quoted("this launcher has no pinned program; rebuild it with launcher/build.sh ALLOWED_PROGRAM=<python> (scripts/install.sh does)"))"
    )
    exit(exUsage)
}

func childEnvironment(_ env: [String]) -> [String] {
    return env.filter { entry in !scrubbedPrefixes.contains(where: { entry.hasPrefix($0) }) }
}

// MARK: canary

enum CanaryResult {
    case ok, missing(Int32), denied(Int32), ioError(Int32), pending
}

/// lstat the path, then list one entry of a directory or open (and close, unread) anything else.
/// No byte of content is read and nothing is written; this thread's dataless-file materialisation policy is
/// set OFF first, so even a File Provider placeholder is never downloaded by a canary.
func probe(_ path: String) -> Int32 {
    _ = setiopolicy_np(IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES, IOPOL_SCOPE_THREAD,
                       IOPOL_MATERIALIZE_DATALESS_FILES_OFF)
    var st = stat()
    if lstat(path, &st) != 0 { return errno }
    let kind = st.st_mode & S_IFMT
    if kind == S_IFDIR {
        guard let dir = opendir(path) else { return errno }
        errno = 0
        _ = readdir(dir)
        let e = errno
        closedir(dir)
        return e
    }
    if kind == S_IFLNK { return 0 }
    let fd = open(path, O_RDONLY | O_NOCTTY | O_CLOEXEC)
    if fd < 0 { return errno }
    close(fd)
    return 0
}

final class Box<T>: @unchecked Sendable {
    var value: T
    init(_ value: T) { self.value = value }
}

func runCanary(_ path: String, timeout: Double) -> CanaryResult {
    let done = DispatchSemaphore(value: 0)
    let result = Box<Int32>(-1)
    DispatchQueue.global(qos: .utility).async {
        result.value = probe(path)
        done.signal()
    }
    if done.wait(wallTimeout: .now() + timeout) == .timedOut { return .pending }
    let e = result.value
    switch e {
    case 0: return .ok
    case EPERM, EACCES: return .denied(e)
    case ENOENT, ENOTDIR: return .missing(e)
    default: return .ioError(e)
    }
}

/// Run every canary; returns the exit code for --canary-only (0 when all passed).
func runCanaries(_ o: Options) -> Int32 {
    var denied = false, missing = false, ioError = false
    for path in o.canaries {
        switch runCanary(path, timeout: o.canaryTimeout) {
        case .ok:
            logLine("CANARY_OK", "path=\(quoted(path))")
        case .pending:
            logLine(
                "TCC_PENDING",
                "reason=canary path=\(quoted(path)) timeout_s=\(o.canaryTimeout) "
                    + "hint=\(quoted("approve the prompt '“agentsync-launcher” wants to access files managed by …', or grant Full Disk Access to this launcher"))"
            )
            return exitTCCPending
        case .denied(let e):
            denied = true
            logLine("TCC_DENIED", "path=\(quoted(path)) errno=\(e) error=\(quoted(errnoText(e)))")
        case .missing(let e):
            missing = true
            logLine("CANARY_MISSING", "path=\(quoted(path)) errno=\(e) error=\(quoted(errnoText(e)))")
        case .ioError(let e):
            ioError = true
            logLine("CANARY_ERROR", "path=\(quoted(path)) errno=\(e) error=\(quoted(errnoText(e)))")
        }
    }
    if denied { return exitTCCDenied }
    if missing { return exNoInput }
    if ioError { return exIOErr }
    return 0
}

// MARK: spawn and supervise

typealias DisclaimFn = @convention(c) (UnsafeMutablePointer<posix_spawnattr_t?>, Int32) -> Int32

func disclaimFunction() -> DisclaimFn? {
    let rtldDefault = UnsafeMutableRawPointer(bitPattern: -2)
    guard let sym = dlsym(rtldDefault, "responsibility_spawnattrs_setdisclaim") else { return nil }
    return unsafeBitCast(sym, to: DisclaimFn.self)
}

func ownExecutablePath() -> String? {
    var size: UInt32 = 0
    _ = _NSGetExecutablePath(nil, &size)
    var buf = [CChar](repeating: 0, count: Int(size) + 1)
    guard _NSGetExecutablePath(&buf, &size) == 0 else { return nil }
    var resolved = [CChar](repeating: 0, count: Int(PATH_MAX) + 1)
    guard realpath(buf, &resolved) != nil else { return String(cString: buf) }
    return String(cString: resolved)
}

func cStrings(_ items: [String]) -> [UnsafeMutablePointer<CChar>?] {
    return items.map { strdup($0) } + [nil]
}

func currentEnvironment() -> [String] {
    var out: [String] = []
    var p = environ
    while let entry = p.pointee {
        out.append(String(cString: entry))
        p += 1
    }
    return out
}

final class Supervisor: @unchecked Sendable {
    let queue = DispatchQueue(label: "com.agentsync.launcher.supervisor")
    var child: pid_t = 0
    var timedOut = false
    var sources: [DispatchSourceProtocol] = []

    func installSignalForwarding() {
        for sig in forwarded {
            signal(sig, SIG_IGN)  // delivered to the dispatch source instead; reset to default in the child
            let src = DispatchSource.makeSignalSource(signal: sig, queue: queue)
            src.setEventHandler { [unowned self] in
                if self.child > 0 {
                    logLine("FORWARD", "signal=\(sig) pid=\(self.child)")
                    kill(self.child, sig)
                }
            }
            src.resume()
            sources.append(src)
        }
    }

    func armWatchdog(timeout: Double, grace: Double) {
        guard timeout > 0 else { return }
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(wallDeadline: .now() + timeout)
        timer.setEventHandler { [unowned self] in
            self.timedOut = true
            logLine(
                "TCC_PENDING",
                "reason=watchdog pid=\(self.child) timeout_s=\(timeout) "
                    + "hint=\(quoted("the child exceeded its wall-clock limit: an unanswered TCC prompt blocks open(); so does a hung cycle"))"
            )
            kill(self.child, SIGTERM)
            let killer = DispatchSource.makeTimerSource(queue: self.queue)
            killer.schedule(wallDeadline: .now() + grace)
            killer.setEventHandler { [unowned self] in
                logLine("WATCHDOG_KILL", "pid=\(self.child) grace_s=\(grace)")
                kill(self.child, SIGKILL)
            }
            killer.resume()
            self.sources.append(killer)
        }
        timer.resume()
        sources.append(timer)
    }

    /// Spawn ``argv`` and wait; returns the raw wait status (never returns on spawn failure).
    func run(_ argv: [String], env: [String], disclaim: DisclaimFn?, timeout: Double, grace: Double) -> Int32 {
        installSignalForwarding()
        var attr: posix_spawnattr_t? = nil
        posix_spawnattr_init(&attr)
        defer { posix_spawnattr_destroy(&attr) }
        var empty = sigset_t()
        sigemptyset(&empty)
        var defaults = sigset_t()
        sigemptyset(&defaults)
        for sig in forwarded { sigaddset(&defaults, sig) }
        sigaddset(&defaults, SIGPIPE)
        posix_spawnattr_setsigmask(&attr, &empty)
        posix_spawnattr_setsigdefault(&attr, &defaults)
        posix_spawnattr_setflags(&attr, Int16(POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF))
        if let disclaim = disclaim {
            let rc = disclaim(&attr, 1)
            if rc != 0 {
                logLine("DISCLAIM_FAILED", "rc=\(rc)")
                exit(exitDisclaimUnavailable)
            }
        }
        let cargv = cStrings(argv)
        let cenv = cStrings(env)
        var pid: pid_t = 0
        let rc: Int32 = queue.sync {
            let r = posix_spawn(&pid, argv[0], nil, &attr, cargv, cenv)
            if r == 0 { self.child = pid }
            return r
        }
        if rc != 0 {
            logLine("SPAWN_FAILED", "program=\(quoted(argv[0])) errno=\(rc) error=\(quoted(errnoText(rc)))")
            exit(exOSErr)
        }
        armWatchdog(timeout: timeout, grace: grace)
        var status: Int32 = 0
        while waitpid(pid, &status, 0) < 0 {
            if errno != EINTR {
                logLine("WAIT_FAILED", "pid=\(pid) errno=\(errno)")
                exit(exOSErr)
            }
        }
        return status
    }
}

/// Exit the way the child did (or 79 after a watchdog kill).
func finish(_ status: Int32, timedOut: Bool) -> Never {
    let termsig = status & 0x7f
    if timedOut {
        logLine("CHILD_EXIT", "status=\(status) after=watchdog exit=\(exitTCCPending)")
        exit(exitTCCPending)
    }
    if termsig == 0 {
        exit((status >> 8) & 0xff)
    }
    if termsig != 0x7f {
        logLine("CHILD_SIGNALED", "signal=\(termsig)")
        if forwarded.contains(termsig) || termsig == SIGKILL || termsig == SIGPIPE || termsig == SIGALRM {
            signal(termsig, SIG_DFL)
            var set = sigset_t()
            sigemptyset(&set)
            sigaddset(&set, termsig)
            sigprocmask(SIG_UNBLOCK, &set, nil)
            kill(getpid(), termsig)
        }
        exit(128 + termsig)
    }
    exit(exOSErr)
}

// MARK: main

let options = parse(Array(CommandLine.arguments.dropFirst()))
let alreadyDisclaimed = getenv(disclaimedMarker) != nil

if options.selfResponsible && !alreadyDisclaimed {
    guard let disclaim = disclaimFunction() else {
        logLine("DISCLAIM_UNAVAILABLE", "symbol=responsibility_spawnattrs_setdisclaim")
        exit(exitDisclaimUnavailable)
    }
    guard let me = ownExecutablePath() else {
        logLine("SPAWN_FAILED", "error=\(quoted("cannot resolve own executable path"))")
        exit(exOSErr)
    }
    let env = currentEnvironment().filter { !$0.hasPrefix(disclaimedMarker + "=") } + [disclaimedMarker + "=1"]
    let args = [me] + Array(CommandLine.arguments.dropFirst())
    let canaryBudget = options.canaryTimeout * Double(options.canaries.count)
    let outerTimeout = options.timeout > 0 ? options.timeout + options.grace + canaryBudget + 10 : 0
    let sup = Supervisor()
    let status = sup.run(args, env: env, disclaim: disclaim, timeout: outerTimeout, grace: options.grace)
    finish(status, timedOut: sup.queue.sync { sup.timedOut })
}

if !options.canaryOnly {
    enforcePin(options.program)  // before any canary: a refused child never touches a protected path
}
let canaryCode = runCanaries(options)
if options.canaryOnly {
    exit(canaryCode)
}
if canaryCode == exitTCCPending {
    exit(exitTCCPending)
}
let env = childEnvironment(currentEnvironment().filter { !$0.hasPrefix(disclaimedMarker + "=") })
let supervisor = Supervisor()
let status = supervisor.run(options.program, env: env, disclaim: nil, timeout: options.timeout, grace: options.grace)
finish(status, timedOut: supervisor.queue.sync { supervisor.timedOut })
