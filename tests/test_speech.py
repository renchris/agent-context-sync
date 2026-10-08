"""convert/speech.py, the speech engine's build, trust, probe, digests and protocol (spec S8, C3).

The protocol tests run the fake speech helper of ``tests/speech_kit.py``; the build tests use stub
xcode-select, xcrun, swift and git in the style of ``tests/test_media.py``.  Every word and every name is made
up (Contoso).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agentsync.config import ConvertConfig
from agentsync.convert import ocr, speech
from agentsync.convert.speech import Segment, SpeechEngine, SpeechError, Word
from speech_kit import FAKE_PIN, calls, fake_speech, pcm, place_models
from test_ocr import loose_umask, mode, script

CFG = ConvertConfig()
DAY = 24 * 3600
WORDS = [["Contoso", 1000, 1400], ["storage", 1500, 1900], ["budget", 9100, 9500], ["approved", 9600, 9990]]
SEGMENTS = [["S1", 900, 2000], ["S2", 9000, 10100]]
OLD = "1111111111111111111111111111111111111111"
NOT_PLACED = "the speech models are not placed: parakeet-tdt-0.6b-v3/ and speaker-diarization/"


@pytest.fixture(autouse=True)
def _speech_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests are about speech being on: the suite's off switch is set per test where it matters."""
    monkeypatch.delenv("AGENTSYNC_OCR", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")


def place_fake(cache: Path, script_: dict[str, Any] | None = None, **kw: Any) -> Path:
    """A fake speech helper where this agentsync's built one goes."""
    helper = ocr._helper_path(cache, speech.SPEECH)
    fake_speech(helper.parent, script_, **kw).replace(helper)
    return helper


def pin_to_placed(monkeypatch: pytest.MonkeyPatch, models: Path) -> None:
    """Pin the digests of the made-up model folders, as if they were FluidAudio's."""
    for name, files in speech.MODEL_FILES.items():
        digest = speech.folder_digest(models / name, files)
        assert digest is not None
        monkeypatch.setitem(speech.MODEL_DIGESTS, name, digest)


def engine_of(tmp_path: Path, script_: dict[str, Any] | None = None, **kw: Any) -> SpeechEngine:
    models = place_models(tmp_path / "models")
    return SpeechEngine(
        fake_speech(tmp_path / "bin", script_, **kw),
        models,
        name="paper-speech",
        helper_version="0.1.0",
        fluidaudio=FAKE_PIN,
        model_digest="ab" * 32,
    )


@pytest.fixture
def no_compiler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Developer tools that leave a trace when anything starts them; the test fails if anything did."""
    trace = tmp_path / "compiler-was-started"
    tool = tmp_path / "traced-tool"
    tool.write_text(f'#!/bin/sh\necho "$0 $*" >> "{trace}"\nexit 1\n')
    tool.chmod(0o700)
    monkeypatch.setattr(ocr, "_XCODE_SELECT", str(tool))
    monkeypatch.setattr(ocr, "_XCRUN", str(tool))
    yield trace
    assert not trace.exists(), trace.read_text()


SWIFT_OK = r"""case "$1" in
package) mkdir -p .build/checkouts/FluidAudio ;;
build)
  commit=$(sed -n 's/.*"\(.*\)".*/\1/p' Sources/agentsync-speech/Pin.swift)
  mkdir -p .build/release
  out=.build/release/agentsync-speech
  answer="{\"engine\": \"stub-speech\", \"fluidaudio\": \"$commit\", \"helper\": \"9.9.9\"}"
  { echo '#!/bin/sh'; echo "echo '$answer'"; } > $out
  chmod 755 $out ;;
esac"""


def git_answering(folder: Path, resolved: str = FAKE_PIN, ancestor: int = 0) -> None:
    script(
        folder / "git",
        f'case "$3" in rev-parse) echo {resolved} ;; merge-base) exit {ancestor} ;; esac',
    )


@pytest.fixture
def tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stub developer tools: ``swift package resolve`` makes the FluidAudio checkout, ``git`` says it is the
    pin and descends from the floor, and ``swift build`` writes a helper that answers ``--version`` with the
    commit ``Pin.swift`` holds.  Each logs its arguments in ``<tool>.calls``."""
    folder = tmp_path / "tools"
    (folder / "Developer").mkdir(parents=True)
    script(folder / "xcode-select", f'echo "{folder}/Developer"')
    script(folder / "xcrun", f'echo "{folder}/$2"')
    script(folder / "swift", SWIFT_OK)
    git_answering(folder)
    monkeypatch.setattr(ocr, "_XCODE_SELECT", str(folder / "xcode-select"))
    monkeypatch.setattr(ocr, "_XCRUN", str(folder / "xcrun"))
    return folder


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "agent-context" / "sources.toml"
    path.parent.mkdir()
    monkeypatch.setenv("AGENTSYNC_CONFIG", str(path))
    return path


def lines_of(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


# ---------------------------------------------------------------------------------------------------------
# build, trust, probe
# ---------------------------------------------------------------------------------------------------------


def test_the_build_resolves_checks_the_floor_pins_the_commit_and_leaves_everything_owner_only(
    tools: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    with loose_umask():
        helper = speech.build(cache)
    folder = cache / "speech"
    assert helper == ocr._helper_path(cache, speech.SPEECH) and helper.parent == folder
    assert helper.name.startswith("agentsync-speech-")
    assert lines_of(tools / "swift.calls") == [
        "package resolve",
        "build -c release --product agentsync-speech",
    ]
    checkout = folder / "build" / ".build" / "checkouts" / "FluidAudio"
    assert lines_of(tools / "git.calls") == [
        f"-C {checkout} rev-parse HEAD",
        f"-C {checkout} merge-base --is-ancestor {speech.FLUIDAUDIO_FLOOR} {FAKE_PIN}",
    ]
    tree = folder / "build"
    assert (tree / "Package.swift").read_bytes() == speech._sources()["Package.swift"]
    assert b'revision: "04e363c29d9a754022d602d6fe1468ab80a0f705"' in (tree / "Package.swift").read_bytes()
    pin = tree / "Sources" / "agentsync-speech" / "Pin.swift"
    assert pin.read_text() == f'let fluidAudioCommit = "{FAKE_PIN}"\n'
    loose = [p for p in (cache, folder, *folder.rglob("*")) if not p.is_symlink() and mode(p) & 0o077]
    assert loose == [], "the helper, its folder and the SwiftPM build tree are owner-only"
    assert mode(helper) == 0o700

    assert speech.probe(CFG, cache) == ("off", NOT_PLACED)
    place_models(folder)
    assert speech.probe(CFG, cache) == ("ready", "stub-speech, helper 9.9.9, FluidAudio 04e363c")
    assert speech.build(cache) == helper and len(lines_of(tools / "swift.calls")) == 2, (
        "a working one is kept"
    )
    helper.chmod(0o722)  # the check is made before every run, not once
    assert speech.probe(CFG, cache) == (
        "failed",
        "the speech helper or its folder can be written by other users",
    )


def test_a_build_that_fails_leaves_its_reason_and_names_no_path(tools: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    home = Path.home()
    script(
        tools / "swift", f'echo "error: cannot clone {home}/x/FluidAudio: network is unreachable" >&2\nexit 1'
    )
    reason = "swift package resolve failed (exit 1): error: cannot clone <path>"
    with pytest.raises(SpeechError) as error:
        speech.build(cache)
    assert str(error.value) == reason
    assert speech.probe(CFG, cache) == ("failed", reason)

    script(tools / "xcrun", "exit 1")
    with pytest.raises(SpeechError, match=r"^no swift in the developer tools$"):
        speech.build(cache)


def test_the_build_gets_twenty_minutes_of_its_own(
    tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert speech._BUILD_TIMEOUT_S == 1200.0 and ocr._BUILD_TIMEOUT_S == 300.0
    now = [0.0]

    def clock() -> float:
        now[0] += 700.0  # each step past the first finds less of the build's time left
        return now[0]

    monkeypatch.setattr(speech, "_clock", clock)
    with pytest.raises(SpeechError, match=r"^the speech helper's build ran out of time$"):
        speech.build(tmp_path / "cache")


def test_probe_and_status_never_compile_and_take_no_digest(
    tmp_path: Path, no_compiler: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    assert speech.probe(CFG, cache) == ("not-built", "the speech helper is not built")
    assert speech.engine(CFG, cache) is None
    assert not cache.exists(), "looking writes nothing"

    helper = place_fake(cache)
    assert speech.probe(CFG, cache) == ("off", NOT_PLACED)
    place_models(helper.parent)

    def no_digest(*args: object) -> None:
        pytest.fail("probe took a digest")

    monkeypatch.setattr(speech, "folder_digest", no_digest)
    assert speech.probe(CFG, cache) == ("ready", "paper-speech, helper 0.1.0, FluidAudio 04e363c")
    assert [c["args"] for c in calls(helper)] == [["--version"], ["--version"]]
    for cfg, detail in (
        (replace(CFG, recordings=False), "[convert] recordings = false"),
        (replace(CFG, ocr=False), "[convert] ocr = false"),
    ):
        assert speech.probe(cfg, cache) == ("off", detail)
        assert speech.engine(cfg, cache) is None
    monkeypatch.setenv("AGENTSYNC_OCR", "0")
    assert speech.probe(CFG, cache) == ("off", "AGENTSYNC_OCR=0")
    monkeypatch.delenv("AGENTSYNC_OCR")
    monkeypatch.setattr(sys, "platform", "linux")
    assert speech.probe(CFG, cache) == ("off", "on-device OCR needs macOS")
    with pytest.raises(SpeechError, match=r"^the speech helper needs macOS$"):
        speech.build(cache)
    assert len(calls(helper)) == 2, "a switched-off helper is not even asked for its version"

    monkeypatch.setattr(sys, "platform", "darwin")
    helper.unlink()
    ocr._marker(helper).write_text("")
    assert speech.probe(CFG, cache) == ("failed", "the last build of the speech helper failed")


def test_the_module_entry_prints_one_speech_line(
    tools: Path, tmp_path: Path, config_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = tmp_path / "cache"
    config_file.write_text(f'[agentsync]\ncache_dir = "{cache}"\n[convert]\nrecordings = false\n')
    assert speech._main() == 0
    assert capsys.readouterr().out == "speech: off ([convert] recordings = false)\n"
    assert not (tools / "swift.calls").exists() and not (cache / "speech").exists()

    config_file.write_text(f'[agentsync]\ncache_dir = "{cache}"\n')
    assert speech._main() == 0
    assert capsys.readouterr().out == f"speech: off ({NOT_PLACED})\n"
    place_models(cache / "speech")
    assert speech._main() == 0
    assert capsys.readouterr().out == "speech: ready (stub-speech, helper 9.9.9, FluidAudio 04e363c)\n"
    script(tools / "xcode-select", "exit 2")
    ocr._helper_path(cache, speech.SPEECH).unlink()
    assert speech._main() == 1
    assert capsys.readouterr().out == (
        "speech: not built (no Xcode or Command Line Tools (xcode-select -p names no folder))\n"
    )


# ---------------------------------------------------------------------------------------------------------
# the digests
# ---------------------------------------------------------------------------------------------------------


def test_the_engine_takes_the_digests_once_and_names_them_in_its_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    helper = place_fake(cache)
    models = place_models(helper.parent)
    pin_to_placed(monkeypatch, models)
    os.utime(helper, (time.time() - 30 * DAY, time.time() - 30 * DAY))
    found = speech.engine(CFG, cache)
    assert found is not None and found.helper == helper and found.models == models
    assert re.fullmatch(r"asr-parakeet-[0-9a-f]{12}-f04e363c-h0\.1\.0-d1", found.identity), found.identity
    assert found.identity == f"asr-parakeet-{speech._model_digest()[:12]}-f04e363c-h0.1.0-d1"
    assert helper.stat().st_mtime > time.time() - DAY, "a helper handed to a cycle is stamped as used"
    assert found.alive()


def test_a_folder_that_does_not_match_its_pinned_digest_gives_no_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    cache = tmp_path / "cache"
    helper = place_fake(cache)
    models = place_models(helper.parent)
    assert speech.probe(CFG, cache)[0] == "ready", "probe takes no digest"
    with caplog.at_level(logging.WARNING, logger="agentsync.convert.speech"):
        assert speech.engine(CFG, cache) is None
    assert "the parakeet-tdt-0.6b-v3 model folder does not match its pinned digest" in caplog.text
    assert str(tmp_path) not in caplog.text

    pin_to_placed(monkeypatch, models)
    assert speech.engine(CFG, cache) is not None
    (models / "speaker-diarization" / "plda-parameters.json").write_text('{"tensors": {}}\n')
    assert speech.engine(CFG, cache) is None, "one changed file"
    pin_to_placed(monkeypatch, models)
    (models / "parakeet-tdt-0.6b-v3" / "CtcHead.mlmodelc").mkdir()
    (models / "parakeet-tdt-0.6b-v3" / "CtcHead.mlmodelc" / "model.mil").write_text("x")
    assert speech.engine(CFG, cache) is None, "a file FluidAudio would also load"
    (models / "parakeet-tdt-0.6b-v3" / "CtcHead.mlmodelc" / "model.mil").unlink()
    (models / "parakeet-tdt-0.6b-v3" / "CtcHead.mlmodelc" / "model.mil").symlink_to(models / "x")
    assert (
        speech.folder_digest(models / "parakeet-tdt-0.6b-v3", speech.MODEL_FILES["parakeet-tdt-0.6b-v3"])
        is None
    )


def test_the_pinned_digests_cover_what_the_helper_loads() -> None:
    assert speech.MODEL_FILES == {
        "parakeet-tdt-0.6b-v3": (
            "CtcHead.mlmodelc",
            "Decoder.mlmodelc",
            "Encoder.mlmodelc",
            "JointDecisionv3.mlmodelc",
            "Preprocessor.mlmodelc",
            "parakeet_vocab.json",
        ),
        "speaker-diarization": (
            "Embedding.mlmodelc",
            "FBank.mlmodelc",
            "PldaRho.mlmodelc",
            "Segmentation.mlmodelc",
            "plda-parameters.json",
        ),
    }
    assert all(re.fullmatch(r"[0-9a-f]{64}", d) for d in speech.MODEL_DIGESTS.values())
    source = speech._sources()["Sources/agentsync-speech/main.swift"].decode()
    for name in ("asrFolder", "diarizerFolder"):
        assert re.search(rf'let {name} = "(parakeet-tdt-0\.6b-v3|speaker-diarization)"', source)
    assert "ModelHub.offlineMode = true" in source and "AsrModels.loadLocal" in source


# ---------------------------------------------------------------------------------------------------------
# the protocol
# ---------------------------------------------------------------------------------------------------------


def test_words_and_voices_read_the_whole_file_or_a_clip_with_absolute_times(tmp_path: Path) -> None:
    eng = engine_of(tmp_path, {"words": WORDS, "segments": SEGMENTS})
    audio = pcm(tmp_path / "work" / "audio.pcm")
    assert eng.words(audio, timeout=60) == tuple(Word(*w) for w in WORDS)
    assert eng.words(audio, timeout=60, clip=(9000, 10000)) == (
        Word("budget", 9100, 9500),
        Word("approved", 9600, 9990),
    )
    assert eng.voices(audio, timeout=60) == (Segment("S1", 900, 2000), Segment("S2", 9000, 10100))
    models = str(eng.models.absolute())
    assert [c["args"] for c in calls(eng.helper)] == [
        ["words", str(audio.absolute()), "--models", models],
        ["words", str(audio.absolute()), "--models", models, "--from", "9000", "--to", "10000"],
        ["voices", str(audio.absolute()), "--models", models, "--threshold", "0.6"],
    ]
    with pytest.raises(ValueError, match="not a clip"):
        eng.words(audio, timeout=60, clip=(5, 4))


GOOD_WORD = ["Contoso", 1000, 1400]
GOOD_SEGMENT = ["S1", 900, 2000]
BAD_REPLIES: dict[str, tuple[str, str]] = {
    "words that are not JSON": ("words", "not json"),
    "words that are a list": ("words", "[]"),
    "words under another key": ("voices", json.dumps({"words": [GOOD_SEGMENT]})),
    "words with timings": ("words", json.dumps({"words": [GOOD_WORD], "processingTimeSeconds": 1.5})),
    "a word with a confidence": ("words", json.dumps({"words": [[*GOOD_WORD, 0.98]]})),
    "a word in seconds": ("words", json.dumps({"words": [["Contoso", 1.0, 1.4]]})),
    "a word ending before it starts": ("words", json.dumps({"words": [["Contoso", 1400, 1000]]})),
    "a word at a negative time": ("words", json.dumps({"words": [["Contoso", -1, 1000]]})),
    "a word whose time is true": ("words", json.dumps({"words": [["Contoso", True, 1000]]})),
    "an empty word": ("words", json.dumps({"words": [["", 1000, 1400]]})),
    "segments with embeddings": (
        "voices",
        json.dumps({"segments": [GOOD_SEGMENT], "embeddings": [[0.1, 0.2]]}),
    ),
    "a segment with its embedding": ("voices", json.dumps({"segments": [[*GOOD_SEGMENT, [0.1, 0.2]]]})),
    "a speaker holding a path": ("voices", json.dumps({"segments": [["../S 1", 900, 2000]]})),
    "segments that are not a list": ("voices", json.dumps({"segments": {"S1": [900, 2000]}})),
}


@pytest.mark.parametrize("answer", BAD_REPLIES.values(), ids=BAD_REPLIES.keys())
def test_an_answer_that_is_not_the_expected_json_is_a_speech_error_without_a_path(
    tmp_path: Path, answer: tuple[str, str]
) -> None:
    command, text = answer
    eng = engine_of(tmp_path, reply={command: text})
    audio = pcm(tmp_path / "work" / "audio.pcm")
    with pytest.raises(SpeechError) as error:
        eng.words(audio, timeout=60) if command == "words" else eng.voices(audio, timeout=60)
    assert str(error.value) == "the speech helper's answer is not the expected JSON"
    assert str(tmp_path) not in str(error.value)


def test_a_word_before_the_clip_is_refused(tmp_path: Path) -> None:
    eng = engine_of(tmp_path, reply={"words": json.dumps({"words": [GOOD_WORD]})})
    with pytest.raises(SpeechError, match="not the expected JSON"):
        eng.words(pcm(tmp_path / "a.pcm"), timeout=60, clip=(5000, 9000))


def test_a_helper_failure_and_a_missing_model_are_speech_errors_without_a_path(tmp_path: Path) -> None:
    eng = engine_of(tmp_path, fail={"voices": "voice separation failed"})
    audio = pcm(tmp_path / "work" / "audio.pcm")
    with pytest.raises(SpeechError, match=r"^the speech helper exited 3: error: voice separation failed$"):
        eng.voices(audio, timeout=60)
    (eng.models / "parakeet-tdt-0.6b-v3" / "parakeet_vocab.json").unlink()
    (eng.models / "parakeet-tdt-0.6b-v3").rmdir()
    with pytest.raises(SpeechError) as error:
        eng.words(audio, timeout=60)
    assert (
        str(error.value)
        == "the speech helper exited 3: error: the model folder parakeet-tdt-0.6b-v3 is not there"
    )


@pytest.mark.parametrize(
    "version",
    [
        '{"engine": "paper-speech", "helper": "0.1.0"}',
        f'{{"engine": "paper-speech", "helper": "0.1.0", "fluidaudio": "{FAKE_PIN[:7]}"}}',
        f'{{"engine": "a b", "helper": "0.1.0", "fluidaudio": "{FAKE_PIN}"}}',
        f'{{"engine": "x", "helper": "0.1.0", "fluidaudio": "{FAKE_PIN}", "embedding": [0.1]}}',
    ],
)
def test_a_version_answer_that_is_not_the_expected_json_is_failed(tmp_path: Path, version: str) -> None:
    cache = tmp_path / "cache"
    place_models(place_fake(cache, version=version).parent)
    assert speech.probe(CFG, cache) == (
        "failed",
        "the speech helper's --version answer is not the expected JSON",
    )
