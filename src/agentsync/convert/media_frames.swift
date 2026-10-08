// agentsync-media: reads a recording on this Mac with AVFoundation (spec S1, S2, S4, S6 rule 8).
//
// Built by agentsync (convert/media.py, with convert/ocr.py's build) with the Command Line Tools' swiftc.  No
// network: nothing leaves the Mac.  Every answer is one JSON document on stdout, keys sorted.  A failure is
// exit 3 with "error: <message>" on stderr; no message holds a path.  tests/media_kit.py fakes this protocol.
//
//   agentsync-media --version
//       {"engine","helper"}
//   agentsync-media info FILE
//       {"duration_ms","width","height","picture","audio","created"}
//   agentsync-media scan FILE --out DIR [--step-ms 2000] [--first-tick K] [--max-ticks N]
//       {"grid":[320,180],"step_ms","file":"grids.bin","ticks":[{"index","ms"}]}
//   agentsync-media frames FILE --out DIR --ticks A,B [--crop X0,Y0,X1,Y1 | --crop-right F] [--step-ms 2000]
//       {"frames":[{"tick","ms","file","width","height"}]}
//   agentsync-media diff GRIDS --pairs A:B,... [--include R]... [--exclude R]... [--threshold 12]
//       {"pairs":[{"a","b","changed","cells"}]}
//   agentsync-media pills FILE --request REQ.json [--step-ms 2000]
//       {"pills":[{"tick","values"}]}
//   agentsync-media audio FILE --out DIR
//       {"file":"audio.pcm","sample_rate":16000,"channels":1,"samples"}
//
// Tick k is media time k x step from the picture track's first frame; a recording has floor(duration / step)
// + 1 ticks.  The frame of a tick is the one on display at that time (both tolerances zero; past the last
// frame, the last frame), upright.  "width" and "height" are upright pixels; "picture" is h264, hevc, vp9,
// av1 or the track's own four-character code, null without a picture track; "created" is the container's
// creation time in UTC, or null.
//
// scan writes ticks K to K + N - 1 (at most N of them, never past the end) to DIR/grids.bin, one 320x180 grid
// of box-averaged luma per tick (integer floor of 0.299 R + 0.587 G + 0.114 B over the cell's pixels).  The
// tick list keeps absolute indexes.  frames writes DIR/tHHMMSS.jpg per tick, named by media time, ImageIO
// JPEG at quality 0.7 with no metadata, native size; a crop keeps pixel columns floor(x0 w) to floor(x1 w) - 1
// and rows likewise.  diff compares grids at their positions in the grid file: a cell counts as changed when
// its luma differs by more than the threshold, under the mask (the included rectangles, the whole grid when
// none, less the excluded ones; a cell is in a rectangle when any part of it is).
//
// pills (spec S7) reads REQ, [{"tick": k, "boxes": [[x, y, w, h], ...]}] in fractions of the frame, origin
// top-left, and answers in its order with one value per box: the median of B - R over the box's pixels with
// R + G + B < 600 (the lower one of an even count; 0 when none is that dark).  A box covers pixel columns
// floor(x w) - 4 to ceil((x + w) w) + 3 and rows floor(y h) - 2 to ceil((y + h) h) + 1, clamped to the frame.
// audio (spec S8 rule 1) decodes the first sound track to DIR/audio.pcm, 16 kHz mono 16-bit little-endian
// PCM, read from the start and never by seeking; "samples" counts what it wrote.  It decodes to float at the
// track's own rate and resamples itself (Resampler), which adds no difference between runs; Apple's AAC decoder
// can still differ in the last bit on a long file, so audio.pcm may differ by 1 in a few samples per million.

import AVFoundation
import CoreGraphics
import Foundation
import ImageIO

let helperVersion = "1.1.0"
let engineName = "avfoundation"
let gridW = 320
let gridH = 180
let jpegQuality = 0.7
let pillWiden = (4, 2)  // pixels added left and right, above and below
let pillDark = 600  // a pixel counts when R + G + B is below this
let sampleRate = 16000

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data("error: \(message)\n".utf8))
    exit(3)
}

func emit(_ value: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .withoutEscapingSlashes])
    else { fail("cannot encode the answer") }
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

// ---------------------------------------------------------------------------------------------------------
// arguments: a command, then plain values and options in any order
// ---------------------------------------------------------------------------------------------------------

let arguments = Array(CommandLine.arguments.dropFirst())
if arguments.contains("--version") {
    emit(["engine": engineName, "helper": helperVersion])
    exit(0)
}
let valued: Set<String> = [
    "--out", "--step-ms", "--first-tick", "--max-ticks", "--ticks", "--crop", "--crop-right", "--pairs",
    "--include", "--exclude", "--threshold", "--request",
]
var found: [String: [String]] = [:]
var plain: [String] = []
var rest = arguments.dropFirst().makeIterator()
while let argument = rest.next() {
    if valued.contains(argument) {
        guard let value = rest.next() else { fail("\(argument) needs a value") }
        found[argument, default: []].append(value)
    } else if argument.hasPrefix("--") {
        fail("unknown option \(argument)")
    } else {
        plain.append(argument)
    }
}

func whole(_ name: String, _ fallback: Int, atLeast minimum: Int) -> Int {
    guard let text = found[name]?.last else { return fallback }
    guard let n = Int(text), n >= minimum else { fail("\(name) needs a whole number >= \(minimum)") }
    return n
}

func fraction(_ text: Substring) -> Double {
    guard let v = Double(text), v.isFinite else { fail("a rectangle needs four numbers") }
    return v
}

func rect(_ text: String) -> (Double, Double, Double, Double) {
    let parts = text.split(separator: ",", omittingEmptySubsequences: false)
    guard parts.count == 4 else { fail("a rectangle needs four numbers") }
    return (fraction(parts[0]), fraction(parts[1]), fraction(parts[2]), fraction(parts[3]))
}

func onlyFile() -> URL {
    guard plain.count == 1 else { fail("one file is needed") }
    return URL(fileURLWithPath: plain[0])
}

func outFolder() -> URL {
    var isDir: ObjCBool = false
    guard let path = found["--out"]?.last, FileManager.default.fileExists(atPath: path, isDirectory: &isDir),
        isDir.boolValue
    else { fail("--out needs a folder") }
    return URL(fileURLWithPath: path, isDirectory: true)
}

// ---------------------------------------------------------------------------------------------------------
// the recording
// ---------------------------------------------------------------------------------------------------------

struct Recording {
    let asset: AVURLAsset
    let picture: AVAssetTrack?
    let durationMs: Int
}

func openRecording(_ url: URL) -> Recording {
    guard FileManager.default.isReadableFile(atPath: url.path) else { fail("cannot open the file") }
    let asset = AVURLAsset(url: url, options: [AVURLAssetPreferPreciseDurationAndTimingKey: true])
    // The latest end of any track, not asset.duration: that one follows the SDK the helper was linked with
    // (3,253,207 or 3,253,254 ms for one recording whose sound ends last; the tracks say 3,253,254 either way).
    var end = CMTime.zero
    for track in asset.tracks {
        let e = CMTimeRangeGetEnd(track.timeRange)
        if e.isNumeric && CMTimeCompare(e, end) > 0 { end = e }
    }
    let ms = max(0, Int(CMTimeConvertScale(end, timescale: 1000, method: .roundTowardZero).value))
    return Recording(asset: asset, picture: asset.tracks(withMediaType: .video).first, durationMs: ms)
}

func codec(_ track: AVAssetTrack) -> String {
    guard let first = track.formatDescriptions.first else { return "unknown" }
    let code = CMFormatDescriptionGetMediaSubType(first as! CMFormatDescription)
    let text = String(
        String.UnicodeScalarView([24, 16, 8, 0].compactMap { Unicode.Scalar(UInt8((code >> $0) & 0xff)) }))
    let known = ["avc1": "h264", "avc3": "h264", "hvc1": "hevc", "hev1": "hevc", "vp09": "vp9", "av01": "av1"]
    if let name = known[text] { return name }
    let token = text.lowercased().filter { $0.isASCII && ($0.isLetter || $0.isNumber) }
    return token.isEmpty ? "unknown" : token
}

func created(_ asset: AVAsset) -> Any {
    // An MP4 that never set its creation time says 1904 (the format's epoch): that is no time.
    guard let date = asset.creationDate?.dateValue, date.timeIntervalSince1970 > 0 else { return NSNull() }
    let format = DateFormatter()
    format.locale = Locale(identifier: "en_US_POSIX")
    format.timeZone = TimeZone(identifier: "UTC")
    format.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
    return format.string(from: date)
}

func tickCount(_ r: Recording, _ step: Int) -> Int { r.durationMs / step + 1 }

struct Frames {
    let generator: AVAssetImageGenerator
    let start: CMTime
    let last: CMTime

    init(_ r: Recording) {
        guard let track = r.picture else { fail("the recording has no picture") }
        generator = AVAssetImageGenerator(asset: r.asset)
        generator.appliesPreferredTrackTransform = true
        generator.requestedTimeToleranceBefore = .zero
        generator.requestedTimeToleranceAfter = .zero
        let range = track.timeRange
        let scale = track.naturalTimeScale > 0 ? track.naturalTimeScale : 600
        start = range.start
        last = CMTimeSubtract(CMTimeRangeGetEnd(range), CMTime(value: 1, timescale: scale))
    }

    /// The frames on display ``ms`` after the first one, upright, in the order asked.  They are asked for
    /// together, so the decoder works ahead: on a busy Mac 150 ticks took 22 to 40 s this way against 59 to
    /// 89 s one at a time, with the same pixels.
    func at(_ ms: [Int]) -> [CGImage] {
        let times = ms.map { value -> NSValue in
            let time = CMTimeAdd(start, CMTime(value: CMTimeValue(value), timescale: 1000))
            return NSValue(time: CMTimeCompare(time, last) > 0 ? last : time)
        }
        var images = [CGImage?](repeating: nil, count: times.count)
        var answered = [Bool](repeating: false, count: times.count)
        var calls = 0
        let lock = NSLock()
        let done = DispatchSemaphore(value: 0)
        generator.generateCGImagesAsynchronously(forTimes: times) { requested, image, _, result, _ in
            lock.lock()
            let match = times.indices.first { !answered[$0] && CMTimeCompare(times[$0].timeValue, requested) == 0 }
            if let i = match {
                answered[i] = true
                images[i] = result == .succeeded ? image : nil
            }
            calls += 1
            let finished = calls == times.count
            lock.unlock()
            if finished { done.signal() }
        }
        if !times.isEmpty { done.wait() }
        var out: [CGImage] = []
        for image in images {
            guard let image = image else { fail("the picture cannot be decoded") }
            out.append(image)
        }
        return out
    }
}

let batch = 16  // frames asked for at once: 16 decoded 1080p frames are about 130 MB

/// ``body`` over ``image`` drawn as 8-bit RGBX in its own RGB colour space, the top row first.
func withPixels<T>(_ image: CGImage, _ body: (UnsafePointer<UInt8>, Int, Int) -> T) -> T {
    let w = image.width
    let h = image.height
    let space = image.colorSpace.flatMap { $0.model == .rgb ? $0 : nil } ?? CGColorSpaceCreateDeviceRGB()
    guard w > 0, h > 0,
        let context = CGContext(
            data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: space,
            bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue),
        let base = context.data
    else { fail("the picture cannot be decoded") }
    context.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
    return body(UnsafePointer(base.bindMemory(to: UInt8.self, capacity: w * h * 4)), w, h)
}

func grid(_ image: CGImage) -> Data { withPixels(image) { pixels, w, h in grid(pixels, w, h) } }

func grid(_ pixels: UnsafePointer<UInt8>, _ w: Int, _ h: Int) -> Data {
    var out = Data(count: gridW * gridH)
    out.withUnsafeMutableBytes { raw in
        let cells = raw.bindMemory(to: UInt8.self)
        for cy in 0..<gridH {
            let y0 = cy * h / gridH
            let y1 = max(y0 + 1, (cy + 1) * h / gridH)
            for cx in 0..<gridW {
                let x0 = cx * w / gridW
                let x1 = max(x0 + 1, (cx + 1) * w / gridW)
                var sum = 0
                for y in y0..<y1 {
                    var p = pixels + (y * w + x0) * 4
                    for _ in x0..<x1 {
                        sum += 299 * Int(p[0]) + 587 * Int(p[1]) + 114 * Int(p[2])
                        p += 4
                    }
                }
                cells[cy * gridW + cx] = UInt8(sum / (1000 * (y1 - y0) * (x1 - x0)))
            }
        }
    }
    return out
}

/// The median of B - R over the dark enough pixels of ``box`` (x, y, w, h fractions), widened and clamped.
func pill(_ pixels: UnsafePointer<UInt8>, _ w: Int, _ h: Int, _ box: (Double, Double, Double, Double)) -> Int {
    func clamp(_ v: Double, _ top: Int) -> Int { Int(max(0, min(Double(top), v))) }
    let x0 = clamp((box.0 * Double(w)).rounded(.down) - Double(pillWiden.0), w)
    let x1 = clamp(((box.0 + box.2) * Double(w)).rounded(.up) + Double(pillWiden.0), w)
    let y0 = clamp((box.1 * Double(h)).rounded(.down) - Double(pillWiden.1), h)
    let y1 = clamp(((box.1 + box.3) * Double(h)).rounded(.up) + Double(pillWiden.1), h)
    var counts = [Int](repeating: 0, count: 511)  // B - R + 255
    var n = 0
    for y in y0..<max(y0, y1) {
        var p = pixels + (y * w + x0) * 4
        for _ in x0..<max(x0, x1) {
            let r = Int(p[0])
            let b = Int(p[2])
            if r + Int(p[1]) + b < pillDark {
                counts[b - r + 255] += 1
                n += 1
            }
            p += 4
        }
    }
    guard n > 0 else { return 0 }
    var seen = 0
    for (i, c) in counts.enumerated() {
        seen += c
        if seen > (n - 1) / 2 { return i - 255 }
    }
    return 0
}

func writeJPEG(_ image: CGImage, to url: URL) {
    let data = NSMutableData()
    guard let destination = CGImageDestinationCreateWithData(data as CFMutableData, "public.jpeg" as CFString, 1, nil)
    else { fail("cannot write a frame") }
    let options: [CFString: Any] = [kCGImageDestinationLossyCompressionQuality: jpegQuality]
    CGImageDestinationAddImage(destination, image, options as CFDictionary)
    guard CGImageDestinationFinalize(destination) else { fail("cannot write a frame") }
    do { try withoutMetadata(data as Data).write(to: url) } catch { fail("cannot write a frame") }
}

/// ``jpeg`` without the Exif (APP1) and Photoshop (APP13) segments ImageIO writes on its own.  The JFIF header
/// and the colour profile stay.
func withoutMetadata(_ jpeg: Data) -> Data {
    let b = [UInt8](jpeg)
    guard b.count > 2 else { return jpeg }
    var out = Data(b[0..<2])
    var i = 2
    while i + 4 <= b.count, b[i] == 0xFF, b[i + 1] != 0xDA {  // 0xDA: the image data follows
        let end = i + 2 + (Int(b[i + 2]) << 8 | Int(b[i + 3]))
        guard end <= b.count else { break }
        if b[i + 1] != 0xE1 && b[i + 1] != 0xED { out.append(contentsOf: b[i..<end]) }
        i = end
    }
    out.append(contentsOf: b[i...])
    return out
}

func hms(_ ms: Int) -> String {
    let s = ms / 1000
    return String(format: "%02d%02d%02d", s / 3600, s / 60 % 60, s % 60)
}

// ---------------------------------------------------------------------------------------------------------
// commands
// ---------------------------------------------------------------------------------------------------------

func info() {
    let r = openRecording(onlyFile())
    var answer: [String: Any] = [
        "duration_ms": r.durationMs, "width": 0, "height": 0, "picture": NSNull(),
        "audio": !r.asset.tracks(withMediaType: .audio).isEmpty, "created": created(r.asset),
    ]
    if let track = r.picture {
        let size = track.naturalSize.applying(track.preferredTransform)
        answer["width"] = Int(abs(size.width).rounded())
        answer["height"] = Int(abs(size.height).rounded())
        answer["picture"] = codec(track)
    }
    emit(answer)
}

func scan() {
    let r = openRecording(onlyFile())
    let out = outFolder()
    let step = whole("--step-ms", 2000, atLeast: 1)
    let first = whole("--first-tick", 0, atLeast: 0)
    let count = tickCount(r, step)
    guard first < count else { fail("tick \(first) is past the end") }
    let end = found["--max-ticks"] == nil ? count : min(count, first + whole("--max-ticks", 0, atLeast: 0))
    let frames = Frames(r)
    let file = out.appendingPathComponent("grids.bin")
    guard FileManager.default.createFile(atPath: file.path, contents: nil),
        let handle = try? FileHandle(forWritingTo: file)
    else { fail("cannot write the grids") }
    let ticks = Array(first..<max(first, end))
    for at in stride(from: 0, to: ticks.count, by: batch) {
        autoreleasepool {
            for image in frames.at(ticks[at..<min(ticks.count, at + batch)].map { $0 * step }) {
                do { try handle.write(contentsOf: grid(image)) } catch { fail("cannot write the grids") }
            }
        }
    }
    guard (try? handle.close()) != nil else { fail("cannot write the grids") }
    emit([
        "grid": [gridW, gridH], "step_ms": step, "file": "grids.bin",
        "ticks": ticks.map { ["index": $0, "ms": $0 * step] },
    ])
}

func frames() {
    let r = openRecording(onlyFile())
    let out = outFolder()
    let step = whole("--step-ms", 2000, atLeast: 1)
    let count = tickCount(r, step)
    guard let list = found["--ticks"]?.last else { fail("--ticks needs a value") }
    var ticks: [Int] = []
    for text in list.split(separator: ",") {
        guard let k = Int(text) else { fail("--ticks needs whole numbers") }
        ticks.append(k)
    }
    var (x0, y0, x1, y1) = found["--crop"]?.last.map(rect) ?? (0.0, 0.0, 1.0, 1.0)
    if let right = found["--crop-right"]?.last { x1 = fraction(Substring(right)) }
    for k in ticks where !(0 <= k && k < count) { fail("tick \(k) is past the end") }
    let source = Frames(r)
    var made: [[String: Any]] = []
    for at in stride(from: 0, to: ticks.count, by: batch) {
        let some = Array(ticks[at..<min(ticks.count, at + batch)])
        autoreleasepool {
            for (k, image) in zip(some, source.at(some.map { $0 * step })) {
                let w = Double(image.width)
                let h = Double(image.height)
                let left = Int(max(0, min(w, (x0 * w).rounded(.down))))
                let top = Int(max(0, min(h, (y0 * h).rounded(.down))))
                let right = Int(max(0, min(w, (x1 * w).rounded(.down))))
                let bottom = Int(max(0, min(h, (y1 * h).rounded(.down))))
                guard left < right, top < bottom else { fail("the crop holds no pixels") }
                let full = left == 0 && top == 0 && right == image.width && bottom == image.height
                guard
                    let kept = full
                        ? image : image.cropping(to: CGRect(x: left, y: top, width: right - left, height: bottom - top))
                else { fail("the crop holds no pixels") }
                let name = "t\(hms(k * step)).jpg"
                writeJPEG(kept, to: out.appendingPathComponent(name))
                made.append(["tick": k, "ms": k * step, "file": name, "width": kept.width, "height": kept.height])
            }
        }
    }
    emit(["frames": made])
}

func cells(_ r: (Double, Double, Double, Double)) -> (Int, Int, Int, Int) {
    func clamp(_ v: Double, _ top: Int) -> Int { Int(max(0, min(Double(top), v))) }
    return (
        clamp((r.0 * Double(gridW)).rounded(.down), gridW), clamp((r.1 * Double(gridH)).rounded(.down), gridH),
        clamp((r.2 * Double(gridW)).rounded(.up), gridW), clamp((r.3 * Double(gridH)).rounded(.up), gridH)
    )
}

func diff() {
    guard plain.count == 1,
        let data = try? Data(contentsOf: URL(fileURLWithPath: plain[0]), options: .alwaysMapped)
    else { fail("cannot open the grid file") }
    let threshold = whole("--threshold", 12, atLeast: 0)
    let size = gridW * gridH
    var mask = [UInt8](repeating: found["--include"] == nil ? 1 : 0, count: size)
    for (value, key) in [(UInt8(1), "--include"), (UInt8(0), "--exclude")] {
        for text in found[key] ?? [] {
            let (cx0, cy0, cx1, cy1) = cells(rect(text))
            for y in cy0..<max(cy0, cy1) {
                for x in cx0..<max(cx0, cx1) { mask[y * gridW + x] = value }
            }
        }
    }
    let masked = mask.reduce(0) { $0 + Int($1) }
    guard let list = found["--pairs"]?.last else { fail("--pairs needs a value") }
    var pairs: [[String: Any]] = []
    for pair in list.split(separator: ",") {
        let ends = pair.split(separator: ":", omittingEmptySubsequences: false)
        guard ends.count == 2, let a = Int(ends[0]), let b = Int(ends[1]), a >= 0, b >= 0 else {
            fail("--pairs needs A:B pairs of whole numbers")
        }
        guard (max(a, b) + 1) * size <= data.count else { fail("tick past the end of the grid file") }
        let changed = data.withUnsafeBytes { raw -> Int in
            let g = raw.bindMemory(to: UInt8.self)
            var n = 0
            for i in 0..<size where mask[i] != 0 && abs(Int(g[a * size + i]) - Int(g[b * size + i])) > threshold {
                n += 1
            }
            return n
        }
        pairs.append(["a": a, "b": b, "changed": changed, "cells": masked])
    }
    emit(["pairs": pairs])
}

func pills() {
    let r = openRecording(onlyFile())
    let step = whole("--step-ms", 2000, atLeast: 1)
    let count = tickCount(r, step)
    guard let path = found["--request"]?.last, let data = FileManager.default.contents(atPath: path),
        let items = (try? JSONSerialization.jsonObject(with: data)) as? [[String: Any]]
    else { fail("--request needs a JSON list") }
    var ticks: [Int] = []
    var boxes: [[(Double, Double, Double, Double)]] = []
    for item in items {
        guard let k = item["tick"] as? Int, let list = item["boxes"] as? [[Double]],
            list.allSatisfy({ $0.count == 4 && $0.allSatisfy(\.isFinite) })
        else { fail("--request needs a tick and boxes of four numbers per item") }
        guard 0 <= k && k < count else { fail("tick \(k) is past the end") }
        ticks.append(k)
        boxes.append(list.map { ($0[0], $0[1], $0[2], $0[3]) })
    }
    let source = Frames(r)
    var answer: [[String: Any]] = []
    for at in stride(from: 0, to: ticks.count, by: batch) {
        let some = Array(at..<min(ticks.count, at + batch))
        autoreleasepool {
            for (i, image) in zip(some, source.at(some.map { ticks[$0] * step })) {
                let values = withPixels(image) { pixels, w, h in boxes[i].map { pill(pixels, w, h, $0) } }
                answer.append(["tick": ticks[i], "values": values])
            }
        }
    }
    emit(["pills": answer])
}

/// Mono samples at ``rate`` in, 16 kHz 16-bit samples out: a windowed-sinc low-pass (Blackman window, 16 zero
/// crossings a side, cut at 0.47 of the lower rate) evaluated at each output time in Double, in a fixed order,
/// so the chunks the samples arrive in change nothing.  AVFoundation's own converter is not used: under load it
/// gave a second byte stream in 1 of 200 runs (float) and 2 of about 260 (16-bit), where its float decode at
/// the track's own rate and channel count gave one in 232 of 232.
final class Resampler {
    let up: Int
    let down: Int
    let half: Int
    let taps: [Double]  // up phases of 2 half + 1 taps, each phase summing to 1
    var input: [Double] = []
    var base = 0  // the index of input[0] in the whole track
    var total = 0
    var next = 0

    init(rate: Int) {
        func gcd(_ a: Int, _ b: Int) -> Int { b == 0 ? a : gcd(b, a % b) }
        let g = gcd(rate, sampleRate)
        let up = sampleRate / g
        let fc = 0.47 * Double(min(rate, sampleRate)) / Double(rate)  // cycles per input sample
        let half = Int((8 / fc).rounded(.up))
        let reach = Double(half + 1)
        var table: [Double] = []
        table.reserveCapacity(up * (2 * half + 1))
        for phase in 0..<up {
            let row = (-half...half).map { j -> Double in
                let t = Double(j) - Double(phase) / Double(up)
                let sinc = t == 0 ? 1 : sin(2 * Double.pi * fc * t) / (2 * Double.pi * fc * t)
                return sinc * (0.42 + 0.5 * cos(Double.pi * t / reach) + 0.08 * cos(2 * Double.pi * t / reach))
            }
            let sum = row.reduce(0, +)
            table.append(contentsOf: row.map { $0 / sum })
        }
        self.up = up
        self.down = rate / g
        self.half = half
        self.taps = table
    }

    /// The output samples ``samples`` completes; with ``final``, the rest, the track's end padded with silence.
    func push(_ samples: [Double], final: Bool = false) -> [Int16] {
        input.append(contentsOf: samples)
        total += samples.count
        var out: [Int16] = []
        let width = 2 * half + 1
        input.withUnsafeBufferPointer { x in
            taps.withUnsafeBufferPointer { h in
                while true {
                    let at = next * down
                    let i = at / up
                    if final ? at >= total * up : i + half >= base + x.count { break }
                    let row = (at % up) * width
                    let first = i - half - base
                    var sum = 0.0
                    for j in 0..<width where first + j >= 0 && first + j < x.count {
                        sum += h[row + j] * x[first + j]
                    }
                    out.append(Int16(max(-32768, min(32767, (sum * 32768).rounded()))))
                    next += 1
                }
            }
        }
        let keep = next * down / up - half  // no later output reaches before this sample
        if keep - base > 1 << 16 {
            input.removeFirst(keep - base)
            base = keep
        }
        return out
    }
}

func audio() {
    let r = openRecording(onlyFile())
    let out = outFolder()
    guard let track = r.asset.tracks(withMediaType: .audio).first else { fail("the recording has no sound") }
    guard let reader = try? AVAssetReader(asset: r.asset) else { fail("the sound cannot be decoded") }
    let output = AVAssetReaderTrackOutput(
        track: track,
        outputSettings: [
            AVFormatIDKey: kAudioFormatLinearPCM, AVLinearPCMBitDepthKey: 32, AVLinearPCMIsFloatKey: true,
            AVLinearPCMIsBigEndianKey: false, AVLinearPCMIsNonInterleaved: false,
        ])
    output.alwaysCopiesSampleData = false
    guard reader.canAdd(output) else { fail("the sound cannot be decoded") }
    reader.add(output)
    let file = out.appendingPathComponent("audio.pcm")
    guard FileManager.default.createFile(atPath: file.path, contents: nil),
        let handle = try? FileHandle(forWritingTo: file)
    else { fail("cannot write the sound") }
    func write(_ samples: [Int16]) {
        let data = samples.map { $0.littleEndian }.withUnsafeBytes { Data($0) }
        do { try handle.write(contentsOf: data) } catch { fail("cannot write the sound") }
    }
    guard reader.startReading() else { fail("the sound cannot be decoded") }
    var resampler: Resampler?
    var shape: (Int, Int)?  // rate, channels
    var written = 0
    while let buffer = output.copyNextSampleBuffer() {
        guard let block = CMSampleBufferGetDataBuffer(buffer) else { continue }
        guard let format = CMSampleBufferGetFormatDescription(buffer),
            let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(format)?.pointee,
            asbd.mSampleRate >= 1, asbd.mSampleRate == asbd.mSampleRate.rounded(), asbd.mChannelsPerFrame > 0
        else { fail("the sound cannot be decoded") }
        let now = (Int(asbd.mSampleRate), Int(asbd.mChannelsPerFrame))
        if shape == nil {
            shape = now
            resampler = Resampler(rate: now.0)
        }
        guard let (_, channels) = shape, now == shape!, let resampler = resampler else {
            fail("the sound changes its rate or channels")
        }
        let length = CMBlockBufferGetDataLength(block)
        var floats = [Float](repeating: 0, count: length / 4)
        let status = floats.withUnsafeMutableBytes { raw in
            CMBlockBufferCopyDataBytes(block, atOffset: 0, dataLength: length / 4 * 4, destination: raw.baseAddress!)
        }
        guard status == kCMBlockBufferNoErr, length % (4 * channels) == 0 else { fail("the sound cannot be decoded") }
        let mono = stride(from: 0, to: floats.count, by: channels).map { at -> Double in
            var sum = 0.0
            for c in 0..<channels { sum += Double(floats[at + c]) }
            return sum / Double(channels)
        }
        let made = resampler.push(mono)
        write(made)
        written += made.count
    }
    guard reader.status == .completed else { fail("the sound cannot be decoded") }
    if let resampler = resampler {
        let made = resampler.push([], final: true)
        write(made)
        written += made.count
    }
    guard (try? handle.close()) != nil else { fail("cannot write the sound") }
    emit(["file": "audio.pcm", "sample_rate": sampleRate, "channels": 1, "samples": written])
}

switch arguments.first ?? "" {
case "info": info()
case "scan": scan()
case "frames": frames()
case "diff": diff()
case "pills": pills()
case "audio": audio()
default: fail("unknown command")
}
exit(0)
