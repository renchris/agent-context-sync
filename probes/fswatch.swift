// fswatch.swift <out-file> <path>... — file-level FSEvents, one line per event: epoch  flags  path
import Foundation
import CoreServices
let args = Array(CommandLine.arguments.dropFirst()); let out = FileHandle(forWritingAtPath: args[0]) ?? { FileManager.default.createFile(atPath: args[0], contents: nil); return FileHandle(forWritingAtPath: args[0])! }()
out.seekToEndOfFile()
let names: [(UInt32,String)] = [(0x100,"Created"),(0x200,"Removed"),(0x400,"InodeMetaMod"),(0x800,"Renamed"),(0x1000,"Modified"),(0x2000,"FinderInfoMod"),(0x4000,"ChangeOwner"),(0x8000,"XattrMod"),(0x10000,"IsFile"),(0x20000,"IsDir"),(0x40000,"IsSymlink"),(0x1,"MustScanSubDirs"),(0x10,"HistoryDone"),(0x20,"RootChanged"),(0x100000,"Cloned")]
let cb: FSEventStreamCallback = { _, _, n, paths, flags, _ in
  let ps = unsafeBitCast(paths, to: NSArray.self) as! [String]
  for i in 0..<n { let f = flags[i]; let fs = names.filter { f & $0.0 != 0 }.map { $0.1 }.joined(separator: ",")
    out.write("\(Date().timeIntervalSince1970)\t0x\(String(f, radix: 16))\t\(fs)\t\(ps[i])\n".data(using: .utf8)!) } }
let s = FSEventStreamCreate(nil, cb, nil, Array(args.dropFirst()) as CFArray, FSEventStreamEventId(kFSEventStreamEventIdSinceNow), 0.2, UInt32(kFSEventStreamCreateFlagFileEvents | kFSEventStreamCreateFlagNoDefer | kFSEventStreamCreateFlagUseCFTypes))!
FSEventStreamSetDispatchQueue(s, DispatchQueue.main); FSEventStreamStart(s); dispatchMain()
