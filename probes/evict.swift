import Foundation
// CORRECTED (2026-09-29): evict used to exit 0 even when every path printed FAILED, so a script could go on to
// measure a "dataless" read of a file that was never evicted. It now exits 1 if any path failed, and 2 (usage)
// with no path. The printed lines are unchanged.
if CommandLine.arguments.count < 2 { FileHandle.standardError.write("usage: evict <path>...\n".data(using: .utf8)!); exit(2) }
var failed = false
for p in CommandLine.arguments.dropFirst() {
  do { try FileManager.default.evictUbiquitousItem(at: URL(fileURLWithPath: p)); print("evicted \(p)") }
  catch { print("FAILED \(p): \(error)"); failed = true }
}
exit(failed ? 1 : 0)
