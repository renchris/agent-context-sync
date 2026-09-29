// fswatch.swift <out-file> <path>... — file-level FSEvents, one line per event: epoch  flags  path
import Foundation
import CoreServices
// CORRECTED (2026-09-29): missing arguments, an out-file that cannot be created, and a failed
// FSEventStreamCreate used to crash on an index or a force-unwrap (Trace/BPT trap, exit 133); they now print
// a usage or error line and exit 2 or 1. 0x100000 used to be labelled "Cloned", but FSEvents.h defines it as
// ItemIsHardlink (ItemCloned is 0x400000); it is now "IsHardlink". The dropped/wrapped/mount/own-event flags
// the design's rescan logic depends on are now named too. New names are appended, so the name order for the
// flags this probe already logged (C14) is unchanged; the hex column always carried every bit.
func die(_ msg: String, _ code: Int32) -> Never { FileHandle.standardError.write((msg + "\n").data(using: .utf8)!); exit(code) }
let args = Array(CommandLine.arguments.dropFirst())
guard args.count >= 2 else { die("usage: fswatch <out-file> <path>...", 2) }
func openOut(_ path: String) -> FileHandle {
  if let h = FileHandle(forWritingAtPath: path) { return h }
  FileManager.default.createFile(atPath: path, contents: nil)
  guard let h = FileHandle(forWritingAtPath: path) else { die("fswatch: cannot open \(path) for writing", 1) }
  return h
}
let out = openOut(args[0])
out.seekToEndOfFile()
let names: [(UInt32,String)] = [(0x100,"Created"),(0x200,"Removed"),(0x400,"InodeMetaMod"),(0x800,"Renamed"),(0x1000,"Modified"),(0x2000,"FinderInfoMod"),(0x4000,"ChangeOwner"),(0x8000,"XattrMod"),(0x10000,"IsFile"),(0x20000,"IsDir"),(0x40000,"IsSymlink"),(0x1,"MustScanSubDirs"),(0x10,"HistoryDone"),(0x20,"RootChanged"),(0x100000,"IsHardlink"),
  (0x200000,"IsLastHardlink"),(0x400000,"Cloned"),(0x2,"UserDropped"),(0x4,"KernelDropped"),(0x8,"EventIdsWrapped"),(0x40,"Mount"),(0x80,"Unmount"),(0x80000,"OwnEvent")]
let cb: FSEventStreamCallback = { _, _, n, paths, flags, _ in
  let ps = unsafeBitCast(paths, to: NSArray.self) as! [String]
  for i in 0..<n { let f = flags[i]; let fs = names.filter { f & $0.0 != 0 }.map { $0.1 }.joined(separator: ",")
    out.write("\(Date().timeIntervalSince1970)\t0x\(String(f, radix: 16))\t\(fs)\t\(ps[i])\n".data(using: .utf8)!) } }
let s = FSEventStreamCreate(nil, cb, nil, Array(args.dropFirst()) as CFArray, FSEventStreamEventId(kFSEventStreamEventIdSinceNow), 0.2, UInt32(kFSEventStreamCreateFlagFileEvents | kFSEventStreamCreateFlagNoDefer | kFSEventStreamCreateFlagUseCFTypes))
guard let s else { die("fswatch: FSEventStreamCreate failed", 1) }
FSEventStreamSetDispatchQueue(s, DispatchQueue.main); guard FSEventStreamStart(s) else { die("fswatch: FSEventStreamStart failed", 1) }; dispatchMain()
