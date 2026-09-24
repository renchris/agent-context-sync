import Foundation
for p in CommandLine.arguments.dropFirst() {
  do { try FileManager.default.evictUbiquitousItem(at: URL(fileURLWithPath: p)); print("evicted \(p)") }
  catch { print("FAILED \(p): \(error)") }
}
