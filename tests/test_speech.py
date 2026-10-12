"""convert/speech.py, the speech engine's build, trust, probe, digests and protocol (spec S8, C3).

The protocol tests run the fake speech helper of ``tests/speech_kit.py``; the build tests use stub
xcode-select, xcrun, swift and git in the style of ``tests/test_media.py``.  Every word and every name is made
up (Contoso).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
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
from test_ocr import compiler_pid, compiling, gone, loose_umask, mode, script

CFG = ConvertConfig()
DAY = 24 * 3600
WORDS = [["Contoso", 1000, 1400], ["storage", 1500, 1900], ["budget", 9100, 9500], ["approved", 9600, 9990]]
SEGMENTS = [["S1", 900, 2000], ["S2", 9000, 10100]]
OLD = "1111111111111111111111111111111111111111"
NOT_PLACED = (
    "the speech helper is built; the speech models are not placed: parakeet-tdt-0.6b-v3/ and "
    "speaker-diarization/"
)


@pytest.fixture(autouse=True)
def _speech_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests are about speech being on: the suite's off switch is set per test where it matters."""
    monkeypatch.delenv("AGENTSYNC_OCR", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    for key in (*speech._PROXY_VARS, *speech._CA_VARS, *speech._CURL_CA_VARS):  # a developer's proxy or CA
        monkeypatch.delenv(key, raising=False)


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
    spm = f"--cache-path {folder}/swiftpm/cache --config-path {folder}/swiftpm/config "
    spm += f"--security-path {folder}/swiftpm/security"
    assert lines_of(tools / "swift.calls") == [
        f"package {spm} resolve",
        f"build {spm} -c release --product agentsync-speech",
    ]
    checkout = folder / "build" / ".build" / "checkouts" / "FluidAudio"
    assert lines_of(tools / "git.calls") == [
        "config --type=path --get-urlmatch http https://github.com",  # the seed: no proxy in git's config
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


def test_a_build_step_killed_by_a_signal_says_so_and_is_tried_again(tools: Path, tmp_path: Path) -> None:
    """A step a signal stopped (subprocess's negative return code) is not "exit -9" with whatever the
    compiler printed last, and a killed floor check is not the floor refusal."""
    cache = tmp_path / "cache"
    later = "scripts/install.sh tries the build again on its next run"
    script(
        tools / "swift",
        'case "$1" in package) mkdir -p .build/checkouts/FluidAudio ;; '
        'build) echo "Building for production..."; echo "error: an earlier line" >&2; kill -KILL $$ ;; esac',
    )
    with pytest.raises(SpeechError) as error:
        speech.build(cache)
    assert str(error.value) == f"swift build was stopped by SIGKILL (signal 9); {later}"
    assert speech.probe(CFG, cache) == ("failed", str(error.value))

    script(tools / "git", f'case "$3" in rev-parse) echo {FAKE_PIN} ;; merge-base) kill -TERM $$ ;; esac')
    with pytest.raises(SpeechError) as error:
        speech.build(cache)
    assert str(error.value) == f"git merge-base was stopped by SIGTERM (signal 15); {later}"
    script(  # a crash whose own stderr quotes an exit is still a crash: only the reason's start refuses
        tools / "git",
        f'case "$3" in rev-parse) echo {FAKE_PIN} ;; '
        'merge-base) echo "error: it failed (exit 1)" >&2; kill -SEGV $$ ;; esac',
    )
    with pytest.raises(SpeechError) as error:
        speech.build(cache)
    assert (
        str(error.value)
        == f"git merge-base was stopped by SIGSEGV (signal 11): error: it failed (exit 1); {later}"
    )
    script(tools / "git", f'case "$3" in rev-parse) echo {FAKE_PIN}; chmod -x "$0" ;; esac')
    with pytest.raises(SpeechError, match=r"^git merge-base does not run: Permission denied$"):
        speech.build(cache)  # a check that could not run is not the floor refusal either

    script(tools / "swift", SWIFT_OK)
    git_answering(tools)
    assert speech.build(cache).is_file()
    assert not ocr._marker(ocr._helper_path(cache, speech.SPEECH)).exists()


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


def test_a_speech_build_out_of_time_leaves_no_compiler_running(
    tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pidfile = tmp_path / "compiler.pid"
    script(
        tools / "swift",
        'case "$1" in package) mkdir -p .build/checkouts/FluidAudio ;; build)\n'
        + compiling(pidfile)
        + ";; esac",
    )
    monkeypatch.setattr(speech, "_BUILD_TIMEOUT_S", 2.0)
    with pytest.raises(SpeechError, match=r"^the speech helper's build ran out of time$"):
        speech.build(tmp_path / "cache")
    assert gone(compiler_pid(pidfile)), "swift build's compiler outlived the build's time limit"


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
    """The installer's one speech line: off when switched off (nothing built), and after a build with the
    models not placed it says the helper is built (B13 H13: the B11 and B12 rehearsals read minutes of
    building end in a bare ``speech: off`` and could not tell the build had succeeded)."""
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


SHELL_PROXY = {
    "HTTPS_PROXY": "http://proxy.contoso.example:8080",
    "https_proxy": "http://proxy.contoso.example:8080",
    "ALL_PROXY": "http://proxy.contoso.example:8080",
    "NO_PROXY": "intranet.contoso.example",
}


def test_the_build_gives_swiftpm_the_shells_proxy_and_the_helper_never_gets_it(
    tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setup prompt step 1 says a corporate proxy may need HTTPS_PROXY: SwiftPM clones FluidAudio with git,
    which reads it from the environment.  The build's tools get the shell's proxy variables and nothing else
    of the shell; the helper keeps the fixed environment."""
    for key, value in SHELL_PROXY.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("CONTOSO_TOKEN", "not for a compiler")
    helper_logs_env = SWIFT_OK.replace(
        "  chmod 755 $out", "  echo 'env > \"$0.env\"' >> $out\n  chmod 755 $out"
    )
    script(tools / "swift", 'env > "$0.$1.env"\n' + helper_logs_env)
    cache = tmp_path / "cache"
    helper = speech.build(cache)
    for step in ("package", "build"):
        seen = dict(ln.split("=", 1) for ln in lines_of(tools / f"swift.{step}.env"))
        assert {k: seen.get(k) for k in SHELL_PROXY} == SHELL_PROXY, step
        assert "CONTOSO_TOKEN" not in seen and seen["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin", step
    place_models(cache / "speech")
    assert speech.probe(CFG, cache)[0] == "ready"
    names = {ln.split("=", 1)[0] for ln in lines_of(Path(f"{helper}.env"))}
    assert "PATH" in names and not set(SHELL_PROXY) & names


SHELL_CA = {
    "GIT_SSL_CAINFO": "/Users/contoso/certs/contoso-inspection.pem",
    "GIT_SSL_CAPATH": "/Users/contoso/certs",
    "CURL_SSL_BACKEND": "secure-transport",
}
IGNORED_CA = {
    "SSL_CERT_FILE": "/Users/contoso/certs/contoso-inspection.pem",
    "SSL_CERT_DIR": "/Users/contoso/certs",
    "CURL_CA_BUNDLE": "/Users/contoso/certs/contoso-inspection.pem",
    "GIT_SSL_NO_VERIFY": "1",
    "DEVELOPER_DIR": "/Applications/Contoso.app/Contents/Developer",
}


def test_the_build_gives_git_the_shells_ca_and_never_turns_verification_off(
    tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R84: behind TLS inspection the shell's git may trust the inspecting CA only through GIT_SSL_CAINFO,
    GIT_SSL_CAPATH or CURL_SSL_BACKEND (Xcode's git reads those three and ignores SSL_CERT_* and
    CURL_CA_BUNDLE, measured), so SwiftPM's clone of FluidAudio gets them too.  GIT_SSL_NO_VERIFY and
    DEVELOPER_DIR never reach the build, an empty value is not passed, and the helpers keep ocr._env()."""
    plain = ocr._env()
    for key, value in {**SHELL_CA, **IGNORED_CA}.items():
        monkeypatch.setenv(key, value)
    assert ocr._env() == plain, "the OCR, media and speech helpers never see a CA variable"
    assert speech._build_env() == {**plain, **SHELL_CA}

    monkeypatch.setenv("GIT_SSL_CAPATH", "")
    assert "GIT_SSL_CAPATH" not in speech._build_env(), "an empty value is not passed"

    script(tools / "swift", 'env > "$0.$1.env"\n' + SWIFT_OK)
    helper = speech.build(tmp_path / "cache")
    for step in ("package", "build"):
        seen = dict(ln.split("=", 1) for ln in lines_of(tools / f"swift.{step}.env"))
        assert seen.get("GIT_SSL_CAINFO") == SHELL_CA["GIT_SSL_CAINFO"], step
        assert seen.get("CURL_SSL_BACKEND") == "secure-transport", step
        assert not set(IGNORED_CA) & set(seen) and "GIT_SSL_CAPATH" not in seen, step
    git_seen = dict(ln.split("=", 1) for ln in lines_of(tools / "git.env"))
    assert git_seen.get("GIT_SSL_CAINFO") == SHELL_CA["GIT_SSL_CAINFO"]
    assert not set(IGNORED_CA) & set(git_seen)
    assert helper.is_file()


SPM_FLAGS = ("--cache-path", "--config-path", "--security-path")


def test_swiftpm_keeps_its_cache_config_and_security_beside_the_build_tree(
    tools: Path, tmp_path: Path
) -> None:
    """R90: on Darwin SwiftPM finds its user-level folders through Foundation, which ignores HOME, so without
    these flags the build wrote the real ~/Library/Caches/org.swift.swiftpm and ~/Library/org.swift.swiftpm.
    Both steps get the same three paths (build reuses resolve's state), all under one owner-only folder beside
    the build tree, made owner-only again when the build fails."""
    cache = tmp_path / "cache"
    spm = cache / "speech" / "swiftpm"
    with loose_umask():
        speech.build(cache)
    calls = [line.split() for line in lines_of(tools / "swift.calls")]
    assert [c[0] for c in calls] == ["package", "build"]
    flags = []
    for call in calls:
        seen = {flag: call[call.index(flag) + 1] for flag in SPM_FLAGS if flag in call}
        assert set(seen) == set(SPM_FLAGS), call
        flags.append(seen)
    assert (
        flags[0]
        == flags[1]
        == {
            "--cache-path": f"{spm}/cache",
            "--config-path": f"{spm}/config",
            "--security-path": f"{spm}/security",
        }
    )
    assert mode(spm) == 0o700

    (tools / "swift.calls").unlink()
    script(
        tools / "swift",
        f'mkdir -p "{spm}/cache/manifests"; echo x > "{spm}/cache/manifests/m.db"\n'
        'echo "error: the network is down" >&2; exit 1',
    )
    ocr._helper_path(cache, speech.SPEECH).unlink()
    with loose_umask(), pytest.raises(SpeechError):
        speech.build(cache)
    loose = [p for p in (spm, *spm.rglob("*")) if mode(p) & 0o077]
    assert loose == [], "a failed build leaves SwiftPM's folder owner-only too"


FLUIDAUDIO_NEMO = {
    "04e363c29d9a754022d602d6fe1468ab80a0f705": (
        "https://github.com/FluidInference/text-processing-rs/releases/download/v0.3.1/"
        "NemoTextProcessing.xcframework.zip",
        "5fa8c10d4ec26c1bb2413125f351a7222a4c68a23b74476680fbada7e26fc6aa",
    ),
}
"""The NemoTextProcessing binaryTarget's url and checksum in FluidAudio's Package.swift and
Package@swift-6.2.swift at each commit speech_helper/Package.swift may pin, copied from those two files
(``git show <commit>:Package.swift`` in a FluidAudio clone; both agree at 04e363c).  A new pin is a KeyError
in test_the_seeded_artifact_is_the_one_the_pinned_fluidaudio_names until someone re-reads them."""
NEMO_CACHE_NAME = (
    "https___github_com_FluidInference_text_processing_rs_releases_download_"
    "v0_3_1_NemoTextProcessing_xcframework_zip"
)
"""SwiftPM's file name for the artifact in ``<cache-path>/artifacts`` (Swift 6.2.4, measured)."""
PAYLOAD = b"made-up Contoso xcframework bytes\n"
FAKE_CURL = """while [ $# -gt 0 ]; do [ "$1" = "--output" ] && out="$2"; shift; done
printf 'made-up Contoso xcframework bytes\\n' > "$out"
[ -f "$0.exit" ] && exit "$(cat "$0.exit")"
exit 0"""


def test_the_seeded_artifact_is_the_one_the_pinned_fluidaudio_names() -> None:
    url, checksum = FLUIDAUDIO_NEMO[speech.FLUIDAUDIO_PIN]
    assert (url, checksum) == (speech.NEMO_URL, speech.NEMO_SHA256)
    assert speech._artifact_name(speech.NEMO_URL) == NEMO_CACHE_NAME


@pytest.fixture
def curl(tools: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake curl that writes PAYLOAD to its --output and exits with the number in ``curl.exit`` (0 when
    there is none); the pinned checksum is the payload's, so a match seeds the cache.  The fake swift records
    the artifacts folder at resolve."""
    monkeypatch.setattr(speech, "_CURL", str(script(tools / "curl", FAKE_CURL)))
    monkeypatch.setattr(speech, "NEMO_SHA256", hashlib.sha256(PAYLOAD).hexdigest())
    script(
        tools / "swift",
        'if [ "$1" = package ]; then ls -a "$3/artifacts" > "$0.seen" 2>/dev/null; fi\n' + SWIFT_OK,
    )
    return tools / "curl"


def test_behind_a_proxy_the_build_seeds_swiftpms_cache_with_the_checked_artifact(
    curl: Path, tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R83: SwiftPM downloads FluidAudio's binary artifact itself and ignores the proxy variables, so a Mac
    whose only way out is a proxy in the environment could not build.  curl reads them, so the build fetches
    the pinned artifact through the proxy, checks its checksum and puts it where SwiftPM looks first."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.contoso.example:8080")
    cache = tmp_path / "cache"
    speech.build(cache)
    artifacts = cache / "speech" / "swiftpm" / "cache" / "artifacts"
    [call] = lines_of(curl.with_name("curl.calls"))
    flags = "--fail --silent --show-error --location --proto =https --connect-timeout 30 --speed-limit 1024 "
    flags += "--speed-time 60 --output"
    assert re.fullmatch(rf"{flags} {re.escape(str(artifacts))}/\.seed-\S+ {re.escape(speech.NEMO_URL)}", call)
    assert dict(ln.split("=", 1) for ln in lines_of(curl.with_name("curl.env")))["HTTPS_PROXY"] == (
        "http://proxy.contoso.example:8080"
    ), "curl runs in the build's environment, so it reads the proxy"
    assert [p.name for p in artifacts.iterdir()] == [NEMO_CACHE_NAME], "no temporary file is left"
    assert (artifacts / NEMO_CACHE_NAME).read_bytes() == PAYLOAD
    assert NEMO_CACHE_NAME in lines_of(tools / "swift.seen"), "seeded before resolve"
    assert mode(artifacts / NEMO_CACHE_NAME) & 0o077 == 0

    curl.with_name("curl.calls").unlink()
    ocr._helper_path(cache, speech.SPEECH).unlink()
    speech.build(cache)
    assert not curl.with_name("curl.calls").exists(), "a seeded artifact is not fetched again"


@pytest.mark.parametrize("curl_exit", ["0", "22"])
def test_a_wrong_or_failed_fetch_leaves_nothing_and_swiftpm_downloads_as_before(
    curl: Path, tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, curl_exit: str
) -> None:
    """A checksum mismatch (exit 0, wrong bytes) or a curl failure deletes the temporary file and the build
    goes on as it did before: SwiftPM downloads the artifact itself and checks it again."""
    monkeypatch.setenv("https_proxy", "http://proxy.contoso.example:8080")
    curl.with_name("curl.exit").write_text(curl_exit)
    if curl_exit == "0":
        monkeypatch.setattr(speech, "NEMO_SHA256", "0" * 64)
    cache = tmp_path / "cache"
    assert speech.build(cache).is_file()
    assert len(lines_of(curl.with_name("curl.calls"))) == 1
    artifacts = cache / "speech" / "swiftpm" / "cache" / "artifacts"
    assert not artifacts.exists() or list(artifacts.iterdir()) == []
    assert lines_of(tools / "swift.calls")[0].startswith("package ")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("NO_PROXY", "intranet.contoso.example"),
        ("HTTP_PROXY", "http://proxy.contoso.example:8080"),
        ("http_proxy", "http://proxy.contoso.example:8080"),
    ],
)
def test_without_a_proxy_the_build_fetches_nothing_itself(
    curl: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str, value: str
) -> None:
    """No variable that routes an https url through a proxy: SwiftPM downloads the artifact as before.
    NO_PROXY alone names no proxy, and curl and git both ignore HTTP_PROXY for an https url (measured with
    /usr/bin/curl 8.7.1: HTTP_PROXY at a closed port still fetched https://github.com directly), so a shell
    with only HTTP_PROXY would get a direct fetch of the artifact before SwiftPM's own."""
    monkeypatch.setenv("HTTP_PROXY", "")
    monkeypatch.setenv(key, value)
    assert speech.build(tmp_path / "cache").is_file()
    assert not curl.with_name("curl.calls").exists()


CURL_CA = {
    "CURL_CA_BUNDLE": "/Users/contoso/certs/contoso-inspection.pem",
    "SSL_CERT_FILE": "/Users/contoso/certs/contoso-inspection.pem",
    "SSL_CERT_DIR": "/Users/contoso/certs",
}


def test_the_seeds_curl_trusts_the_shells_ca_and_swiftpm_and_git_never_see_curls_variables(
    curl: Path, tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R84 x R83: /usr/bin/curl 8.7.1 reads CURL_CA_BUNDLE, SSL_CERT_FILE and SSL_CERT_DIR (measured under
    env -i: each pointed at a missing path changed its CAfile or CApath), so behind TLS inspection a shell
    that trusts the inspecting CA through them fetches with curl, and the seed's curl must too or it fails
    verification and SwiftPM's direct download fails after it.  Xcode's git ignores them, so swift and git
    still do not get them; an empty value is not passed, and verification is never turned off."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.contoso.example:8080")
    for key, value in {**CURL_CA, **SHELL_CA, "GIT_SSL_NO_VERIFY": "1"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("SSL_CERT_DIR", "")
    monkeypatch.delenv("GIT_SSL_CAPATH")  # else it is curl's SSL_CERT_DIR (B13 Q79)
    cache = tmp_path / "cache"
    assert speech.build(cache).is_file()
    seen = dict(ln.split("=", 1) for ln in lines_of(curl.with_name("curl.env")))
    passed = {key: seen.get(key) for key in CURL_CA}
    assert passed == {**CURL_CA, "SSL_CERT_DIR": None}, "curl gets the shell's non-empty CA bundle"
    assert seen.get("GIT_SSL_CAINFO") == SHELL_CA["GIT_SSL_CAINFO"] and "GIT_SSL_NO_VERIFY" not in seen
    artifacts = cache / "speech" / "swiftpm" / "cache" / "artifacts"
    assert (artifacts / NEMO_CACHE_NAME).read_bytes() == PAYLOAD
    for tool in ("swift", "git"):
        tool_seen = {ln.split("=", 1)[0] for ln in lines_of(tools / f"{tool}.env")}
        assert not set(CURL_CA) & tool_seen, tool


@pytest.mark.parametrize(
    ("own", "bundle"),
    [
        ({}, SHELL_CA["GIT_SSL_CAINFO"]),
        ({"CURL_CA_BUNDLE": "/Users/contoso/certs/curl.pem"}, "/Users/contoso/certs/curl.pem"),
        ({"SSL_CERT_FILE": "/Users/contoso/certs/openssl.pem"}, None),
    ],
)
def test_a_ca_only_git_trusts_is_the_seeds_curl_bundle_unless_curl_has_its_own(
    curl: Path,
    tools: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    own: dict[str, str],
    bundle: str | None,
) -> None:
    """R84 x R83: a shell behind TLS inspection that trusts the inspecting CA through git alone
    (GIT_SSL_CAINFO, which curl ignores) clones FluidAudio but would fetch the seed with curl's own bundle and
    fail verification.  curl gets that file as CURL_CA_BUNDLE, unless the shell names a bundle of curl's own;
    swift and git never see CURL_CA_BUNDLE."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.contoso.example:8080")
    monkeypatch.setenv("GIT_SSL_CAINFO", SHELL_CA["GIT_SSL_CAINFO"])
    for key, value in own.items():
        monkeypatch.setenv(key, value)
    assert speech.build(tmp_path / "cache").is_file()
    seen = dict(ln.split("=", 1) for ln in lines_of(curl.with_name("curl.env")))
    assert seen.get("CURL_CA_BUNDLE") == bundle
    for tool in ("swift", "git"):
        tool_seen = {ln.split("=", 1)[0] for ln in lines_of(tools / f"{tool}.env")}
        assert "CURL_CA_BUNDLE" not in tool_seen, tool


@pytest.mark.parametrize(
    ("own", "folder"),
    [
        ({}, "/Users/contoso/certs"),
        ({"SSL_CERT_DIR": "/Users/contoso/curl-certs"}, "/Users/contoso/curl-certs"),
    ],
)
def test_a_ca_folder_only_git_trusts_is_the_seeds_curl_folder_unless_curl_has_its_own(
    curl: Path,
    tools: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    own: dict[str, str],
    folder: str,
) -> None:
    """B13 Q79: curl ignores GIT_SSL_CAPATH (measured, exit 60), so a shell that trusts the inspecting CA
    through a hashed CA folder given only to git cloned FluidAudio and failed the seed's verification.  curl
    gets that folder as SSL_CERT_DIR, which adds to its bundle, unless the shell sets one; swift and git
    never see SSL_CERT_DIR."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.contoso.example:8080")
    monkeypatch.setenv("GIT_SSL_CAPATH", "/Users/contoso/certs")
    for key, value in own.items():
        monkeypatch.setenv(key, value)
    assert speech.build(tmp_path / "cache").is_file()
    seen = dict(ln.split("=", 1) for ln in lines_of(curl.with_name("curl.env")))
    assert seen.get("SSL_CERT_DIR") == folder
    for tool in ("swift", "git"):
        tool_seen = {ln.split("=", 1)[0] for ln in lines_of(tools / f"{tool}.env")}
        assert "SSL_CERT_DIR" not in tool_seen, tool


def real_git_config(tools: Path) -> None:
    """The stub git answers ``config`` as the real git does, from HOME's .gitconfig."""
    real = shutil.which("git")
    assert real is not None
    answer = f'case "$3" in rev-parse) echo {FAKE_PIN} ;; merge-base) exit 0 ;; esac'  # as git_answering
    script(tools / "git", f'if [ "$1" = config ]; then exec {real} "$@"; fi\n{answer}')


def test_the_seed_trusts_and_routes_as_gits_own_config_does(
    curl: Path, tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 Q52: git reads its CA, CA folder, TLS backend and proxy from its config files as well as the
    environment, and SwiftPM's git clones with them; the seed's curl reads none of them.  So a proxy kept only
    in ~/.gitconfig fetched nothing, and a CA kept only there failed the seed's verification.  The seed now
    reads git's settings for github.com (url-scoped ones count, ``~`` expanded) and gives them to curl with
    git's precedence: the config's proxy wins over the environment's, in curl's environment, never argv."""
    home = Path.home()
    (home / ".gitconfig").write_text(
        "[http]\n\tsslCAInfo = ~/contoso-ca.pem\n\tsslCAPath = ~/contoso-certs\n\tsslBackend = openssl\n"
        '[http "https://github.com"]\n\tproxy = http://seed:secret@proxy.contoso.example:3128\n'
        '[http "https://intranet.contoso.example"]\n\tproxy = http://wrong.contoso.example:1\n'
    )
    real_git_config(tools)
    monkeypatch.setenv("https_proxy", "http://env-proxy.contoso.example:8080")
    cache = tmp_path / "cache"
    assert speech.build(cache).is_file()
    seen = dict(ln.split("=", 1) for ln in lines_of(curl.with_name("curl.env")))
    proxy = "http://seed:secret@proxy.contoso.example:3128"
    assert (seen.get("HTTPS_PROXY"), seen.get("https_proxy")) == (proxy, proxy), "git's config wins"
    assert seen.get("CURL_CA_BUNDLE") == f"{home}/contoso-ca.pem"
    assert seen.get("SSL_CERT_DIR") == f"{home}/contoso-certs"
    assert seen.get("CURL_SSL_BACKEND") == "openssl"
    [call] = lines_of(curl.with_name("curl.calls"))
    assert "secret" not in call, "the proxy's credentials are not on argv"
    artifacts = cache / "speech" / "swiftpm" / "cache" / "artifacts"
    assert (artifacts / NEMO_CACHE_NAME).read_bytes() == PAYLOAD


def test_a_proxy_only_in_gits_config_still_seeds_and_the_shells_ca_wins_over_the_configs(
    curl: Path, tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 Q52: with no proxy in the environment the seed used to fetch nothing; a proxy url-scoped to
    github.com in git's config is enough now.  The shell's GIT_SSL_CAINFO wins over http.sslCAInfo and the
    shell's CURL_SSL_BACKEND over http.sslBackend, as in git."""
    (Path.home() / ".gitconfig").write_text(
        "[http]\n\tsslCAInfo = ~/contoso-ca.pem\n\tsslBackend = openssl\n"
        '[http "https://github.com"]\n\tproxy = proxy.contoso.example:3128\n'
    )
    real_git_config(tools)
    monkeypatch.setenv("GIT_SSL_CAINFO", SHELL_CA["GIT_SSL_CAINFO"])
    monkeypatch.setenv("CURL_SSL_BACKEND", "secure-transport")
    assert speech.build(tmp_path / "cache").is_file()
    seen = dict(ln.split("=", 1) for ln in lines_of(curl.with_name("curl.env")))
    assert seen.get("HTTPS_PROXY") == "proxy.contoso.example:3128"
    assert seen.get("CURL_CA_BUNDLE") == SHELL_CA["GIT_SSL_CAINFO"]
    assert seen.get("CURL_SSL_BACKEND") == "secure-transport"
    assert "SSL_CERT_DIR" not in seen


def test_without_a_proxy_in_the_shell_or_gits_config_nothing_is_fetched(
    curl: Path, tools: Path, tmp_path: Path
) -> None:
    """A proxy git keeps for another host only is no proxy for github.com."""
    (Path.home() / ".gitconfig").write_text('[http "https://intranet.contoso.example"]\n\tproxy = p:1\n')
    real_git_config(tools)
    assert speech.build(tmp_path / "cache").is_file()
    assert not curl.with_name("curl.calls").exists()


@pytest.mark.parametrize("state", ["fetches", "no proxy", "already seeded"])
def test_a_fetch_cut_off_mid_way_leaves_no_temporary_file_past_the_next_build(
    curl: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    """R83: install.sh stopped by SIGTERM or SIGKILL mid-fetch never runs the seed's ``finally``, so a partial
    ``.seed-*`` file of up to 87 MB stays in the artifacts folder.  The next build removes every one of them
    first, whether or not it fetches, so interrupted runs do not pile them up."""
    if state != "no proxy":
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.contoso.example:8080")
    cache = tmp_path / "cache"
    artifacts = cache / "speech" / "swiftpm" / "cache" / "artifacts"
    artifacts.mkdir(mode=0o700, parents=True)
    for name in (".seed-k3j2h1ab", ".seed-p9q8r7cd"):
        (artifacts / name).write_bytes(b"partial made-up Contoso bytes")
    if state == "already seeded":
        (artifacts / NEMO_CACHE_NAME).write_bytes(PAYLOAD)
    assert speech.build(cache).is_file()
    assert sorted(artifacts.glob(".seed-*")) == []
    assert curl.with_name("curl.calls").exists() == (state == "fetches")
