// agentsync-speech: words and voices of a recording's sound, on this Mac, with FluidAudio (spec S8).
//
// Built by agentsync (convert/speech.py) with SwiftPM against one pinned FluidAudio commit; the build writes
// Pin.swift beside this file, which holds that commit.  No network: the models are read from the folder
// --models names, which the operator filled (parakeet-tdt-0.6b-v3/ and speaker-diarization/, FluidAudio's own
// folder names), and FluidAudio is held in its offline mode, so a missing file is a failure and never a
// download.  Every answer is one JSON document on stdout, keys sorted.  A failure is exit 3 with "error:
// <message>" on stderr; no message holds a path.  tests/speech_kit.py fakes this protocol.
//
//   agentsync-speech --version
//       {"engine","helper","fluidaudio"}
//   agentsync-speech words PCM --models DIR [--from MS --to MS]
//       {"words":[[text,start_ms,end_ms]]}
//   agentsync-speech voices PCM --models DIR --threshold 0.6
//       {"segments":[[speaker,start_ms,end_ms]]}
//
// PCM is raw 16 kHz mono signed 16-bit little-endian, what the media helper's audio verb writes.  words reads
// it whole with Parakeet TDT 0.6b v3 at FluidAudio's default settings, or only the clip --from to --to, whose
// times stay absolute; tokens are joined into words where a token starts with a space, as FluidAudio's own
// command line does.  voices runs the offline diarizer (community-1 segmentation, WeSpeaker, VBx) on the whole
// file at the given clustering threshold, never with a speaker count.  Times are integer milliseconds; no
// timings, confidences or embeddings are printed, and nothing is written to any file.

import CoreML
import FluidAudio
import Foundation

let helperVersion = "0.1.0"
let engineName = "fluidaudio"
let sampleRate = 16_000
let samplesPerMs = sampleRate / 1000
let asrFolder = "parakeet-tdt-0.6b-v3"
let diarizerFolder = "speaker-diarization"

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

func ms(_ seconds: Double) -> Int {
    max(0, Int((seconds * 1000).rounded()))
}

// ---------------------------------------------------------------------------------------------------------
// arguments: a command, then plain values and options in any order
// ---------------------------------------------------------------------------------------------------------

let arguments = Array(CommandLine.arguments.dropFirst())
if arguments.contains("--version") {
    emit(["engine": engineName, "helper": helperVersion, "fluidaudio": fluidAudioCommit])
    exit(0)
}
guard let command = arguments.first else { fail("no command") }

func parse(_ args: ArraySlice<String>) -> (options: [String: String], plain: [String]) {
    var options: [String: String] = [:]
    var plain: [String] = []
    var rest = args.makeIterator()
    while let arg = rest.next() {
        if arg.hasPrefix("--") {
            guard let value = rest.next() else { fail("\(arg) needs a value") }
            guard options[arg] == nil else { fail("\(arg) is given twice") }
            options[arg] = value
        } else {
            plain.append(arg)
        }
    }
    return (options, plain)
}

let (options, plain) = parse(arguments.dropFirst())

func known(_ names: Set<String>) {
    for name in options.keys where !names.contains(name) {
        fail("unknown option \(name)")
    }
}

func whole(_ name: String) -> Int? {
    guard let text = options[name] else { return nil }
    guard let value = Int(text), value >= 0 else { fail("\(name) is not a whole number of milliseconds") }
    return value
}

// ---------------------------------------------------------------------------------------------------------
// input: the PCM file and the model folders
// ---------------------------------------------------------------------------------------------------------

/// The samples of `path` from `fromMs` to `toMs` (the whole file when both are nil), as floats in [-1, 1).
func samples(_ path: String, fromMs: Int?, toMs: Int?) -> [Float] {
    guard let data = try? Data(contentsOf: URL(fileURLWithPath: path), options: .alwaysMapped) else {
        fail("cannot read the sound file")
    }
    guard data.count % 2 == 0 else { fail("the sound file is not 16-bit samples") }
    let count = data.count / 2
    let first = min(count, (fromMs ?? 0) * samplesPerMs)
    let last = min(count, toMs.map { $0 * samplesPerMs } ?? count)
    guard first <= last else { fail("--from is after --to") }
    return data.withUnsafeBytes { raw -> [Float] in
        let pcm = raw.bindMemory(to: Int16.self)
        return (first..<last).map { Float(Int16(littleEndian: pcm[$0])) / 32768 }
    }
}

func folder(_ name: String) -> URL {
    guard let models = options["--models"] else { fail("--models is required") }
    let url = URL(fileURLWithPath: models).appendingPathComponent(name, isDirectory: true)
    var isDir: ObjCBool = false
    guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDir), isDir.boolValue else {
        fail("the model folder \(name) is not there")
    }
    return url
}

func model(_ directory: URL, _ name: String, units: MLComputeUnits) -> MLModel {
    let url = directory.appendingPathComponent(name)
    guard FileManager.default.fileExists(atPath: url.path) else { fail("the model \(name) is not there") }
    let configuration = MLModelConfiguration()
    configuration.computeUnits = units
    configuration.allowLowPrecisionAccumulationOnGPU = true
    guard let loaded = try? MLModel(contentsOf: url, configuration: configuration) else {
        fail("the model \(name) does not load")
    }
    return loaded
}

/// The PLDA psi vector of plda-parameters.json, read as OfflineDiarizerModels.load reads it.
func pldaPsi(_ directory: URL) -> [Double] {
    let url = directory.appendingPathComponent(ModelNames.OfflineDiarizer.pldaParameters)
    guard
        let data = try? Data(contentsOf: url),
        let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
        let tensors = root["tensors"] as? [String: Any],
        let psi = tensors["psi"] as? [String: Any],
        let base64 = psi["data_base64"] as? String,
        let decoded = Data(base64Encoded: base64, options: [.ignoreUnknownCharacters]),
        decoded.count >= MemoryLayout<Float>.size
    else { fail("the PLDA parameters do not load") }
    var floats = [Float](repeating: 0, count: decoded.count / MemoryLayout<Float>.size)
    _ = floats.withUnsafeMutableBytes { decoded.copyBytes(to: $0) }
    return floats.map { Double($0) }
}

// ---------------------------------------------------------------------------------------------------------
// the two commands
// ---------------------------------------------------------------------------------------------------------

func words(_ path: String) async {
    known(["--models", "--from", "--to"])
    let fromMs = whole("--from")
    let toMs = whole("--to")
    guard (fromMs == nil) == (toMs == nil) else { fail("--from and --to go together") }
    let directory = folder(asrFolder)
    let audio = samples(path, fromMs: fromMs, toMs: toMs)
    let offset = fromMs ?? 0
    guard audio.count >= ASRConstants.minimumRequiredSamples(forSampleRate: sampleRate) else {
        emit(["words": [Any]()])  // too short to hold a word
        return
    }
    let version = AsrModelVersion.v3
    let result: ASRResult
    do {
        let models = try AsrModels.loadLocal(from: directory, version: version)
        let manager = AsrManager(
            config: ASRConfig(tdtConfig: TdtConfig(blankId: version.blankId), encoderHiddenSize: version.encoderHiddenSize)
        )
        try await manager.loadModels(models)
        var state = TdtDecoderState.make(decoderLayers: await manager.decoderLayerCount)
        result = try await manager.transcribe(audio, decoderState: &state)
    } catch {
        fail("speech recognition failed")
    }
    var out: [[Any]] = []
    var text = ""
    var start = 0.0
    var end = 0.0
    func close() {
        if !text.isEmpty { out.append([text, offset + ms(start), offset + max(ms(start), ms(end))]) }
        text = ""
    }
    for timing in result.tokenTimings ?? [] {
        let token = timing.token
        if token.hasPrefix(" ") || token.hasPrefix("\n") || token.hasPrefix("\t") {
            close()
            text = token.trimmingCharacters(in: .whitespacesAndNewlines)
            start = timing.startTime
        } else {
            if text.isEmpty { start = timing.startTime }
            text += token
        }
        end = timing.endTime
    }
    close()
    emit(["words": out])
}

func voices(_ path: String) async {
    known(["--models", "--threshold"])
    guard let text = options["--threshold"], let threshold = Double(text), threshold > 0, threshold <= 2 else {
        fail("--threshold is required, above 0 and at most 2")
    }
    let directory = folder(diarizerFolder)
    let audio = samples(path, fromMs: nil, toMs: nil)
    let names = ModelNames.OfflineDiarizer.self
    let models = OfflineDiarizerModels(  // the compute units OfflineDiarizerModels.load gives each model
        segmentationModel: model(directory, names.segmentationFile, units: .all),
        fbankModel: model(directory, names.fbankFile, units: .cpuOnly),
        embeddingModel: model(directory, names.embeddingFile, units: .all),
        pldaRhoModel: model(directory, names.pldaRhoFile, units: .all),
        pldaPsi: pldaPsi(directory),
        compilationDuration: 0
    )
    var config = OfflineDiarizerConfig.default
    config.clusteringThreshold = threshold
    let result: DiarizationResult
    do {
        let manager = OfflineDiarizerManager(config: config)
        manager.initialize(models: models)
        result = try await manager.process(audio: audio)
    } catch {
        fail("voice separation failed")
    }
    // Only the speaker and the span leave: each segment's embedding stays in this process and dies with it.
    let out: [[Any]] = result.segments.map { segment in
        let start = ms(Double(segment.startTimeSeconds))
        return [segment.speakerId, start, max(start, ms(Double(segment.endTimeSeconds)))]
    }
    emit(["segments": out])
}

AppLogger.mirrorsToConsole = false  // stderr carries the one error line, nothing else
ModelHub.offlineMode = true  // a model that is not in the folder fails; FluidAudio never fetches one
guard plain.count == 1 else { fail("\(command) takes one sound file") }
switch command {
case "words": await words(plain[0])
case "voices": await voices(plain[0])
default: fail("unknown command \(command)")
}
