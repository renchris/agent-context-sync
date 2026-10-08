// swift-tools-version: 6.0
// The on-device speech helper (convert/speech.py).  FluidAudio is pinned to one commit, at or after the
// diarizer build floor 04e363c; `python -I -m agentsync.convert.speech` checks the resolved checkout against
// that floor and writes Sources/agentsync-speech/Pin.swift, the commit `--version` reports, before it builds.
import PackageDescription

let package = Package(
    name: "agentsync-speech",
    platforms: [.macOS(.v14)],
    dependencies: [
        .package(
            url: "https://github.com/FluidInference/FluidAudio.git",
            revision: "04e363c29d9a754022d602d6fe1468ab80a0f705"
        )
    ],
    targets: [
        .executableTarget(
            name: "agentsync-speech",
            dependencies: [.product(name: "FluidAudio", package: "FluidAudio")],
            path: "Sources/agentsync-speech"
        )
    ],
    swiftLanguageModes: [.v5]
)
