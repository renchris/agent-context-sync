// agentsync-ocr: on-device text recognition with Apple Vision (VNRecognizeTextRequest, accurate level).
//
// Built by agentsync (convert/ocr.py) with the Command Line Tools' swiftc.  No network and no model download:
// nothing leaves the Mac.  Every answer is one JSON document on stdout, keys sorted.
//
//   agentsync-ocr --version
//       {"engine","helper","revision"}
//   agentsync-ocr [--languages en-US,de-DE] [--tile PX] [--frames N] [--min-px PX] [--max-megapixels N]
//                 PATH...
//       {"results":[{"index","frame","frames","width","height","skipped","error",
//                    "lines":[{"text","confidence","x","y","w","h"}]}]}
//
// One result per frame read, in argument order: the first frame of each file, or its first N with --frames N
// (multi-page TIFF scans).  "index" is the file's position among the paths and "frames" its frame count.  No
// path and no system error text is ever printed on stdout.
//
// "width" and "height" are the UPRIGHT pixel size (after any EXIF rotation); line boxes are fractions of it
// (0...1) with the origin at the TOP-left.  Before a frame is decoded its stored pixel size is read:
//   - more than --max-megapixels, or a side longer than 32768 pixels: "error": "too large"
//   - a side shorter than --min-px (an icon or a bullet): "skipped": true, not an error
// The other errors are "no such file", "not an image", "unsupported image type" (raster types only),
// "no frames", "not readable" and "recognition failed" (Vision's own message goes to stderr).
//
// An image whose longer side exceeds 4/3 of --tile is also read in tiles of that size, each overlapping the
// next by a quarter, because Vision scales a large image down and loses small labels.  merge() says which
// reading of a line is kept.  Exit 0 whenever the JSON was written, 64 on a usage error.

import CoreGraphics
import Foundation
import ImageIO
import Vision

let helperVersion = "2.0.0"
let engineName = "apple-vision"

// ImageIO also opens PDF, SVG, Photoshop and OpenEXR files, and it goes by content, not by file name.
let rasterTypes: Set<String> = [
    "public.png", "public.jpeg", "public.tiff", "com.compuserve.gif", "com.microsoft.bmp", "public.heic",
    "public.heif", "org.webmproject.webp",
]

// With the megapixel limit this bounds the number of tiles (about 70): a strip a million pixels long and
// fifty high is few megapixels and hundreds of tiles.
let longestSide = 32_768

struct Line: Encodable {
    let text: String
    let confidence: Float
    let x: Double
    let y: Double
    let w: Double
    let h: Double
}

struct Item: Encodable {
    let index: Int
    let frame: Int
    let frames: Int
    var width = 0
    var height = 0
    var lines: [Line] = []
    var skipped = false
    var error: String?
}

struct Version: Encodable {
    let engine: String
    let helper: String
    let revision: Int
}

struct Output: Encodable {
    let results: [Item]
}

struct Options {
    var languages = ["en-US"]
    var tile = 0
    var frames = 1
    var minPx = 0
    var maxMegapixels = 50
}

func revision() -> Int {
    if #available(macOS 13.0, *) { return VNRecognizeTextRequestRevision3 }
    return VNRecognizeTextRequestRevision2
}

func complain(_ message: String) {
    FileHandle.standardError.write(Data("agentsync-ocr: \(message)\n".utf8))
}

func usage(_ message: String) -> Never {
    complain(message)
    exit(64)
}

func emit<T: Encodable>(_ value: T) {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
    guard let data = try? encoder.encode(value) else {
        complain("cannot encode the result")
        exit(70)
    }
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

// ---------------------------------------------------------------------------------------------------------
// one pass of Vision
// ---------------------------------------------------------------------------------------------------------

struct Word {
    let text: String
    let x0: Double
    let x1: Double
}

/// One line as one pass read it, in pixels of the upright image with the origin at the top-left.
struct Read {
    var text: String
    var confidence: Float
    var x0: Double
    var y0: Double
    var x1: Double
    var y1: Double
    var leftY: Double  // the line's centre at x0 and at x1: the box of a tilted line is taller than its text
    var rightY: Double
    var lineHeight: Double
    var words: [Word]  // left to right, or empty when the line does not run that way (it is never stitched)
    var row = 0
    var col = 0
    var cutLeft = false  // ends beside an interior tile edge: the line may go on in the next tile
    var cutRight = false
    var open = false  // an end is still cut after stitching: this never beats a whole-image line

    var width: Double { x1 - x0 }

    func centre(at x: Double) -> Double {
        width < 1 ? (leftY + rightY) / 2 : leftY + (rightY - leftY) * (x - x0) / width
    }
}

/// The lines Vision finds in `image`, a piece of the upright image whose top-left corner is at (ox, oy).
func recognise(_ image: CGImage, ox: Double, oy: Double, languages: [String]) throws -> [Read] {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.revision = revision()
    if !languages.isEmpty { request.recognitionLanguages = languages }
    request.minimumTextHeight = 0
    try VNImageRequestHandler(cgImage: image, orientation: .up, options: [:]).perform([request])
    let sw = Double(image.width)
    let sh = Double(image.height)
    // Vision's points are fractions of the piece with the origin at the BOTTOM-left.
    func pixel(_ p: CGPoint) -> (x: Double, y: Double) {
        (ox + Double(p.x) * sw, oy + (1 - Double(p.y)) * sh)
    }
    var out: [Read] = []
    for observation in request.results ?? [] {
        guard let best = observation.topCandidates(1).first else { continue }
        let string = best.string
        let text = string.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty { continue }
        let box = observation.boundingBox
        let x0 = ox + Double(box.minX) * sw
        let x1 = ox + Double(box.maxX) * sw
        let y0 = oy + (1 - Double(box.maxY)) * sh
        let y1 = oy + (1 - Double(box.minY)) * sh
        let (tl, bl) = (pixel(observation.topLeft), pixel(observation.bottomLeft))
        let (tr, br) = (pixel(observation.topRight), pixel(observation.bottomRight))
        let ends = [((tl.x + bl.x) / 2, (tl.y + bl.y) / 2), ((tr.x + br.x) / 2, (tr.y + br.y) / 2)]
            .sorted { $0.0 < $1.0 }
        var lineHeight = (hypot(tl.x - bl.x, tl.y - bl.y) + hypot(tr.x - br.x, tr.y - br.y)) / 2
        var (leftY, rightY) = (ends[0].1, ends[1].1)
        if lineHeight < 1 || lineHeight > y1 - y0 + 1 {
            (lineHeight, leftY, rightY) = (y1 - y0, (y0 + y1) / 2, (y0 + y1) / 2)
        }
        var words: [Word] = []
        let perCharacter = (x1 - x0) / Double(max(string.count, 1))
        for piece in string.split(separator: " ") {
            // By character position when Vision has no box for the word.
            let before = string.distance(from: string.startIndex, to: piece.startIndex)
            var w0 = x0 + perCharacter * Double(before)
            var w1 = w0 + perCharacter * Double(piece.count)
            if let quad = try? best.boundingBox(for: piece.startIndex..<piece.endIndex) {
                w0 = ox + Double(quad.boundingBox.minX) * sw
                w1 = ox + Double(quad.boundingBox.maxX) * sw
            }
            words.append(Word(text: String(piece), x0: w0, x1: w1))
        }
        if !zip(words, words.dropFirst()).allSatisfy({ $0.x0 <= $1.x0 }) { words = [] }
        out.append(
            Read(
                text: text, confidence: best.confidence.isFinite ? best.confidence : 0, x0: x0, y0: y0,
                x1: x1, y1: y1, leftY: leftY, rightY: rightY, lineHeight: lineHeight, words: words))
    }
    return out
}

// ---------------------------------------------------------------------------------------------------------
// tiles: which reading of a line is kept
// ---------------------------------------------------------------------------------------------------------

/// Where tiles start along one side.
func starts(_ length: Int, _ tile: Int) -> [Int] {
    var out = [0]
    while out[out.count - 1] + tile < length { out.append(out[out.count - 1] + tile * 3 / 4) }
    return out
}

/// True when two readings are the same line: on one row, and most of the shorter lies along the other.
///
/// The row test follows the line's centre, not its box, so the stacked lines of a tilted page stay apart.
/// Two tile readings sit within half the smaller line height.  `loosely` is for a whole-image reading: it
/// sees small text scaled down and reports a box up to three times too tall, a few pixels off.  It allows
/// half the larger height, but at most three quarters of the smaller one: the next row of tightly set
/// text is about one line height away.
func sameLine(_ a: Read, _ b: Read, loosely: Bool = false) -> Bool {
    let lo = max(a.x0, b.x0)
    let hi = min(a.x1, b.x1)
    guard hi > lo, hi - lo > 0.5 * min(a.width, b.width) else { return false }
    let (smaller, larger) = (min(a.lineHeight, b.lineHeight), max(a.lineHeight, b.lineHeight))
    let reach = loosely ? min(0.5 * larger, 0.75 * smaller) : 0.5 * smaller
    return abs(a.centre(at: (lo + hi) / 2) - b.centre(at: (lo + hi) / 2)) < reach
}

func inReadingOrder(_ a: Read, _ b: Read) -> Bool { (a.y0, a.x0, a.text) < (b.y0, b.x0, b.text) }

/// Longest first, so the copy of a line that shows most of it is the one kept.
func withoutCopies(_ reads: [Read]) -> [Read] {
    let longestFirst = reads.sorted {
        $0.width != $1.width ? $0.width > $1.width : inReadingOrder($0, $1)
    }
    var kept: [Read] = []
    for read in longestFirst where !kept.contains(where: { sameLine($0, read) }) { kept.append(read) }
    return kept
}

/// One reading from the pieces of a line that neighbouring tiles each saw part of.
///
/// The tiles share a band a quarter of a tile wide.  A word at a tile edge may be cut, so the last word of
/// the left piece and the first of the right piece are never used: each is taken from the other piece, which
/// sees it whole.  The seam sits mid-band, moved so that it falls between two words.
func joined(_ parts: [Read], columns: [(x: Double, width: Double)]) -> Read {
    var out = parts[0]
    out.open = parts[0].cutLeft || parts[parts.count - 1].cutRight
    guard parts.count > 1 else { return out }
    var words: [Word] = []
    var carried: [Word] = []
    var from = -Double.infinity
    for (n, part) in parts.enumerated() {
        var to = Double.infinity
        var carry: [Word] = []
        if n + 1 < parts.count {
            let other = parts[n + 1]
            let band = (lo: columns[other.col].x, hi: columns[part.col].x + columns[part.col].width)
            let upper = part.words.last?.x0 ?? band.hi
            let lower = other.words.first?.x1 ?? band.lo
            to = min(max((band.lo + band.hi) / 2, lower), upper)
            if lower > upper {
                // One word wider than the band is cut in both pieces.  Keep the right piece's copy.
                out.open = true
                carry = Array(other.words.prefix(1))
            } else if let word = part.words.dropLast().first(where: { $0.x0 < to && to < $0.x1 }) {
                to = min(word.x1, upper)
            }
        }
        words += carried
        words += part.words.filter { ($0.x0 + $0.x1) / 2 >= from && ($0.x0 + $0.x1) / 2 < to }
        (carried, from) = (carry, to)
        out.x0 = min(out.x0, part.x0)
        out.y0 = min(out.y0, part.y0)
        out.x1 = max(out.x1, part.x1)
        out.y1 = max(out.y1, part.y1)
        out.confidence = min(out.confidence, part.confidence)
        out.lineHeight = max(out.lineHeight, part.lineHeight)
    }
    out.rightY = parts[parts.count - 1].rightY
    out.words = words
    out.text = words.map { $0.text }.joined(separator: " ")
    if words.isEmpty {  // nothing certain on either side of a seam: keep what was read, as a piece
        out.open = true
        out.text = parts.map { $0.text }.joined(separator: " ")
    }
    return out
}

/// Tile readings with each line's pieces joined across the tile edges of its row of tiles.
func stitched(_ reads: [Read], columns: [(x: Double, width: Double)]) -> [Read] {
    let order = reads.indices.sorted { inReadingOrder(reads[$0], reads[$1]) }
    var next: [Int: Int] = [:]
    var claimed: Set<Int> = []
    for i in order where reads[i].cutRight {
        let left = reads[i]
        let midBand = (columns[left.col + 1].x + columns[left.col].x + columns[left.col].width) / 2
        var best: (index: Int, distance: Double)?
        for j in order where !claimed.contains(j) && reads[j].cutLeft {
            let right = reads[j]
            guard right.row == left.row, right.col == left.col + 1 else { continue }
            // Two pieces of one line both fill the band.  Two lines on one row, one ending in the band and
            // one starting in it, do not: the left one would start after the right one, or end before it.
            let slack = min(left.lineHeight, right.lineHeight)
            guard left.x0 <= right.x0 + slack, right.x1 >= left.x1 - slack else { continue }
            let distance = abs(left.centre(at: midBand) - right.centre(at: midBand))
            guard distance < 0.5 * slack else { continue }
            if distance < (best?.distance ?? .infinity) { best = (j, distance) }
        }
        if let best = best {
            next[i] = best.index
            claimed.insert(best.index)
        }
    }
    var out: [Read] = []
    for i in order where !claimed.contains(i) {
        var parts = [reads[i]]
        var at = i
        while let following = next[at] {
            parts.append(reads[following])
            at = following
        }
        out.append(joined(parts, columns: columns))
    }
    return out
}

/// How much text the readings hold: characters, spaces left out.
func letters(_ reads: [Read]) -> Int {
    reads.reduce(0) { $0 + $1.text.filter { $0 != " " }.count }
}

/// The lines of an image read once whole and once in tiles.
///
/// A tile sees small text at full size, so a line a tile holds from end to end is the better reading.  A
/// line that runs off a tile is only a piece of it.  The rules:
///   - a tile reading with a cut end (`open`) never replaces anything; it is kept only where no other
///     reading has that line;
///   - a whole-image line is dropped only when the complete tile readings of that line hold at least 90%
///     as many characters.  The measure is text, not width: the whole-image box of small text is too wide
///     or too narrow by a character or two, and a scaled-down misreading drops letters, it does not add them;
///   - otherwise the whole-image line has more of the line, so it is kept, and the tile readings that are
///     surely copies of it go.  A doubtful one stays: a line read twice is better than a line lost.
func merge(whole: [Read], tiles: [Read]) -> [Read] {
    var complete = withoutCopies(tiles.filter { !$0.open })
    var wholeLines: [Read] = []
    for line in whole.sorted(by: inReadingOrder) {
        let same = complete.filter { sameLine($0, line, loosely: true) }
        if Double(letters(same)) >= 0.9 * Double(letters([line])) { continue }
        complete = complete.filter { !sameLine($0, line) }
        wholeLines.append(line)
    }
    let pieces = withoutCopies(tiles.filter { $0.open }).filter { piece in
        !complete.contains(where: { sameLine($0, piece) })
            && !wholeLines.contains(where: { sameLine($0, piece, loosely: true) })
    }
    return wholeLines + complete + pieces
}

/// The tile readings of an upright image, each line's pieces already joined.
func readTiles(_ image: CGImage, tile: Int, languages: [String]) throws -> [Read] {
    let xs = starts(image.width, tile)
    let ys = starts(image.height, tile)
    var reads: [Read] = []
    for (row, y) in ys.enumerated() {
        for (col, x) in xs.enumerated() {
            let (wide, high) = (min(tile, image.width - x), min(tile, image.height - y))
            guard let piece = image.cropping(to: CGRect(x: x, y: y, width: wide, height: high))
            else { continue }
            let found = try autoreleasepool {
                try recognise(piece, ox: Double(x), oy: Double(y), languages: languages)
            }
            for var read in found {
                // A line cut by the top or bottom edge of a tile is whole in the row of tiles above or below
                // (a line taller than the overlap is large enough for the whole-image pass).
                let nearRow = max(2, 0.25 * read.lineHeight)
                if row > 0 && read.y0 - Double(y) <= nearRow { continue }
                if row + 1 < ys.count && Double(y + high) - read.y1 <= nearRow { continue }
                // Vision may drop a cut glyph and the space before it, so "beside the edge" is wide.
                let nearColumn = min(max(8, 1.5 * read.lineHeight), Double(tile) / 8)
                let nearLeft = col > 0 && read.x0 - Double(x) <= nearColumn
                let nearRight = col + 1 < xs.count && Double(x + wide) - read.x1 <= nearColumn
                (read.row, read.col) = (row, col)
                if read.words.isEmpty {
                    read.open = nearLeft || nearRight
                } else {
                    (read.cutLeft, read.cutRight) = (nearLeft, nearRight)
                }
                reads.append(read)
            }
        }
    }
    let columns = xs.map { (x: Double($0), width: Double(min(tile, image.width - $0))) }
    return stitched(reads.filter { !$0.open }, columns: columns) + reads.filter { $0.open }
}

// ---------------------------------------------------------------------------------------------------------
// one frame, one file
// ---------------------------------------------------------------------------------------------------------

/// The frame decoded upright (EXIF rotation applied by ImageIO) on white, as plain 8-bit RGB.
func upright(_ source: CGImageSource, _ index: Int, longerSide: Int) -> CGImage? {
    let options: [CFString: Any] = [
        kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceCreateThumbnailWithTransform: true,
        kCGImageSourceThumbnailMaxPixelSize: longerSide,  // the full size: nothing is scaled
        kCGImageSourceShouldCacheImmediately: true,
    ]
    guard let turned = CGImageSourceCreateThumbnailAtIndex(source, index, options as CFDictionary),
        let space = CGColorSpace(name: CGColorSpace.sRGB),
        let context = CGContext(
            data: nil, width: turned.width, height: turned.height, bitsPerComponent: 8, bytesPerRow: 0,
            space: space, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
    else { return nil }
    let all = CGRect(x: 0, y: 0, width: turned.width, height: turned.height)
    context.setFillColor(red: 1, green: 1, blue: 1, alpha: 1)  // dark text on a transparent background
    context.fill(all)
    context.interpolationQuality = .none
    context.draw(turned, in: all)
    return context.makeImage()
}

func readFrame(_ source: CGImageSource, _ blank: Item, _ options: Options) -> Item {
    var item = blank
    let properties = CGImageSourceCopyPropertiesAtIndex(source, item.frame, nil) as? [CFString: Any]
    let storedWidth = (properties?[kCGImagePropertyPixelWidth] as? NSNumber)?.intValue ?? 0
    let storedHeight = (properties?[kCGImagePropertyPixelHeight] as? NSNumber)?.intValue ?? 0
    let orientation = (properties?[kCGImagePropertyOrientation] as? NSNumber)?.intValue ?? 1
    guard storedWidth > 0, storedHeight > 0 else {
        item.error = "not readable"
        return item
    }
    let turned = (5...8).contains(orientation)  // EXIF 5-8: the stored rows are the upright columns
    item.width = turned ? storedHeight : storedWidth
    item.height = turned ? storedWidth : storedHeight
    // Both checks come before any pixel is decoded: a small file can declare a very large image.
    let megapixels = Double(storedWidth) * Double(storedHeight) / 1_000_000
    if megapixels > Double(options.maxMegapixels) || max(storedWidth, storedHeight) > longestSide {
        item.error = "too large"
        return item
    }
    if min(storedWidth, storedHeight) < options.minPx {
        item.skipped = true
        return item
    }
    guard let image = upright(source, item.frame, longerSide: max(storedWidth, storedHeight)) else {
        item.error = "not readable"
        return item
    }
    (item.width, item.height) = (image.width, image.height)
    var reads: [Read]
    do {
        reads = try recognise(image, ox: 0, oy: 0, languages: options.languages)
        if options.tile > 0 && max(image.width, image.height) > options.tile * 4 / 3 {
            let tiles = try readTiles(image, tile: options.tile, languages: options.languages)
            reads = merge(whole: reads, tiles: tiles)
        }
    } catch {
        complain("image \(item.index) frame \(item.frame): recognition failed: \(error.localizedDescription)")
        item.error = "recognition failed"
        return item
    }
    let (fullWidth, fullHeight) = (Double(image.width), Double(image.height))
    item.lines = reads.sorted(by: inReadingOrder).map {
        Line(
            text: $0.text, confidence: $0.confidence, x: $0.x0 / fullWidth, y: $0.y0 / fullHeight,
            w: ($0.x1 - $0.x0) / fullWidth, h: ($0.y1 - $0.y0) / fullHeight)
    }
    return item
}

func readFile(_ path: String, index: Int, _ options: Options) -> [Item] {
    func refused(_ why: String) -> [Item] { [Item(index: index, frame: 0, frames: 0, error: why)] }
    guard FileManager.default.isReadableFile(atPath: path) else { return refused("no such file") }
    guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
        let type = CGImageSourceGetType(source) as String?
    else { return refused("not an image") }
    guard rasterTypes.contains(type) else { return refused("unsupported image type") }
    let frames = CGImageSourceGetCount(source)
    guard frames > 0 else { return refused("no frames") }
    return (0..<min(frames, options.frames)).map { frame in
        autoreleasepool { readFrame(source, Item(index: index, frame: frame, frames: frames), options) }
    }
}

// ---------------------------------------------------------------------------------------------------------
// arguments
// ---------------------------------------------------------------------------------------------------------

var options = Options()
var paths: [String] = []
var wantVersion = false
var arguments = Array(CommandLine.arguments.dropFirst())

func number(_ name: String, atLeast minimum: Int) -> Int {
    guard !arguments.isEmpty, let n = Int(arguments.removeFirst()), n >= minimum else {
        usage("\(name) needs a whole number >= \(minimum)")
    }
    return n
}

while !arguments.isEmpty {
    let argument = arguments.removeFirst()
    switch argument {
    case "--version": wantVersion = true
    case "--languages":
        guard !arguments.isEmpty else { usage("--languages needs a value") }
        options.languages = arguments.removeFirst().split(separator: ",").map { String($0) }
    case "--tile": options.tile = number("--tile", atLeast: 0)
    case "--frames": options.frames = number("--frames", atLeast: 1)
    case "--min-px": options.minPx = number("--min-px", atLeast: 0)
    case "--max-megapixels": options.maxMegapixels = number("--max-megapixels", atLeast: 1)
    case "--":
        paths += arguments
        arguments = []
    default:
        if argument.hasPrefix("--") { usage("unknown option \(argument)") }
        paths.append(argument)
    }
}
if options.tile > 0 && options.tile < 256 { usage("--tile needs 0 (no tiles) or at least 256 pixels") }
if wantVersion {
    emit(Version(engine: engineName, helper: helperVersion, revision: revision()))
    exit(0)
}
if paths.isEmpty { usage("no image paths") }
emit(Output(results: paths.enumerated().flatMap { readFile($0.element, index: $0.offset, options) }))
