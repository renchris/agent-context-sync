"""The on-device speech engine (``speech_helper/``, a SwiftPM package over FluidAudio): build, trust, probe
and run (spec S8, C3).

Built only by ``python -I -m agentsync.convert.speech`` from ``scripts/install.sh``, trusted, probed and
pruned by the OCR helper's functions (``convert/ocr.py``, which take the :data:`SPEECH` description), never by
a sync, ``status`` or doctor.  It lives in ``<cache_dir>/speech/``: the helper, the SwiftPM build tree
(``build/``), both owner-only, and the two model folders the operator places there by hand,
``parakeet-tdt-0.6b-v3/`` and ``speaker-diarization/`` (FluidAudio's own names).  agentsync never downloads a
model: the helper loads them from that folder with FluidAudio held offline, and fails (exit 3) when a file is
missing.  The protocol is pinned by ``tests/speech_kit.py``: ``--version``, ``words``, ``voices``.

Speech is off until both folders are placed and each matches its pinned digest.  :func:`probe` only looks
(the folders are there, the helper answers ``--version`` from a verified FluidAudio commit) and never takes a
digest; :func:`engine` is the one place the digests are taken, once per cycle, and a mismatch is no engine.

The build floor (C3): FluidAudio ``04e363c`` or later, the first build with the silence-aware FBank.  The
build refuses a resolved checkout that does not descend from it, leaving the reason beside the helper; the
helper reports the commit compiled into it, and probe refuses one that is not :data:`FLUIDAUDIO_PIN`.

Answers are checked strictly: integer milliseconds, start at or before end, no key and no field beyond the
protocol's.  A voice embedding never leaves the helper; an answer that carries one is a :class:`SpeechError`.

Switches: everything that switches the media helper off (``[convert] recordings = false``, ``[convert] ocr =
false``, ``AGENTSYNC_OCR=0``, no macOS).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from agentsync.config import ConvertConfig
from agentsync.convert import media, ocr
from agentsync.convert.ocr import OcrError

log = logging.getLogger(__name__)

FLUIDAUDIO_FLOOR = "04e363c29d9a754022d602d6fe1468ab80a0f705"
"""The oldest FluidAudio commit the speech helper may be built from (spec C3)."""
FLUIDAUDIO_PIN = FLUIDAUDIO_FLOOR
"""The commit ``speech_helper/Package.swift`` builds; at or after :data:`FLUIDAUDIO_FLOOR`."""
THRESHOLD = 0.6
"""The diarizer's clustering threshold (community-1's own); never a forced speaker count."""
DIARIZER_REVISION = 1
"""``-d`` in :attr:`SpeechEngine.identity`.  Bump it when the diarizer's settings (the threshold) change."""
ASR_MODELS = "parakeet-tdt-0.6b-v3"
DIARIZER_MODELS = "speaker-diarization"
MODEL_FILES: dict[str, tuple[str, ...]] = {
    ASR_MODELS: (
        "CtcHead.mlmodelc",  # not shipped; FluidAudio loads it when present, so it would change the digest
        "Decoder.mlmodelc",
        "Encoder.mlmodelc",
        "JointDecisionv3.mlmodelc",
        "Preprocessor.mlmodelc",
        "parakeet_vocab.json",
    ),
    DIARIZER_MODELS: (
        "Embedding.mlmodelc",
        "FBank.mlmodelc",
        "PldaRho.mlmodelc",
        "Segmentation.mlmodelc",
        "plda-parameters.json",
    ),
}
"""What the helper loads from each model folder: the files the digest covers."""
MODEL_DIGESTS: dict[str, str] = {
    ASR_MODELS: "91f662e91bade07a57c249ff4ed449cb744c89e5a5e85ced688213bbcf48f0dd",  # 21 files, 483,105,645 B
    DIARIZER_MODELS: "825a52af4635c7aea2b652dbdaef03c4c1126b85f38e4d8271162504b276efcc",  # 21 files, 21.6 MB
}
"""The pinned digest of each folder (:func:`folder_digest`), from FluidAudio's own copies."""

_PACKAGE = "speech_helper"
_TARGET = "agentsync-speech"
_BUILD_TREE = "build"
_BUILD_TIMEOUT_S = 1200.0  # cold builds took 279 to 451 s; OCR's 300 s was sized for one swiftc file
_VERSION_TIMEOUT_S = 5.0
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_FLOOR_REFUSAL = (
    "the speech helper is not built from FluidAudio 04e363c or later; scripts/install.sh rebuilds it"
)
_clock = time.monotonic


class SpeechError(OcrError):
    """The speech helper could not be built, may not be run, failed, ran out of time, or answered with
    something that is not the expected JSON.  Its text never holds a path."""


def _package(name: str) -> bytes:
    return resources.files("agentsync.convert").joinpath(_PACKAGE, name).read_bytes()


def _sources() -> dict[str, bytes]:
    """The packaged SwiftPM package, by its path inside the package."""
    return {name: _package(name) for name in ("Package.swift", f"Sources/{_TARGET}/main.swift")}


SPEECH = ocr.Helper(
    "speech helper",
    "speech",
    _TARGET,
    lambda: b"\0".join(_sources().values()),
    SpeechError,
    "the speech helper needs macOS",
)
"""What the shared build, trust and run functions of ``convert/ocr.py`` need to know about this helper."""


@dataclass(frozen=True, slots=True)
class Word:
    """One recognised word and its span in the recording, in milliseconds."""

    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class Segment:
    """One stretch the diarizer gave to one voice; ``speaker`` is the helper's opaque label."""

    speaker: str
    start_ms: int
    end_ms: int


def _bad() -> SpeechError:
    return SpeechError("the speech helper's answer is not the expected JSON")


def _doc(stdout: bytes, key: str) -> list[object]:
    """The one list an answer holds under ``key``; any other key is refused."""
    try:
        doc = json.loads(stdout)
    except ValueError:  # also bytes that are not UTF-8
        raise _bad() from None
    found = doc.get(key) if isinstance(doc, dict) and set(doc) == {key} else None
    if not isinstance(found, list):
        raise _bad()
    return found


def _ms(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _bad()
    return value


def _span(item: object) -> tuple[str, int, int]:
    """``[label, start_ms, end_ms]``: three fields, whole non-negative times, start at or before end."""
    if not isinstance(item, list) or len(item) != 3:
        raise _bad()
    label, start, end = item
    if not isinstance(label, str) or not label:
        raise _bad()
    start, end = _ms(start), _ms(end)
    if start > end:
        raise _bad()
    return label, start, end


@dataclass(frozen=True, slots=True)
class _Built:
    """A built helper that answered ``--version`` from the verified FluidAudio commit."""

    helper: Path
    name: str
    helper_version: str
    fluidaudio: str

    @property
    def description(self) -> str:
        return f"{self.name}, helper {self.helper_version}, FluidAudio {self.fluidaudio[:7]}"


class SpeechEngine:
    """A built speech helper and the model folder whose digests matched."""

    def __init__(
        self,
        helper: Path,
        models: Path,
        *,
        name: str,
        helper_version: str,
        fluidaudio: str,
        model_digest: str,
    ) -> None:
        """Bind ``helper`` and ``models``; the rest is what ``--version`` printed and the models' digest."""
        self.helper = helper
        self.models = models
        self.name = name
        self.helper_version = helper_version
        self.fluidaudio = fluidaudio
        self.model_digest = model_digest

    @property
    def identity(self) -> str:
        """Producer identity for the recording converter's version (spec section 4):
        ``asr-parakeet-<12 hex of model digest>-f<7 hex commit>-h<helper version>-d<diarizer revision>``.
        The helper's own version is in it: the helper joins tokens into words and rounds times, so a rebuilt
        helper may change a page."""
        return (
            f"asr-parakeet-{self.model_digest[:12]}-f{self.fluidaudio[:7]}-h{self.helper_version}"
            f"-d{DIARIZER_REVISION}"
        )

    @property
    def description(self) -> str:
        """One line for logs."""
        return f"{self.name}, helper {self.helper_version}, FluidAudio {self.fluidaudio[:7]}"

    def _run(self, args: Sequence[str], *, timeout: float) -> bytes:
        return ocr._run_helper(self.helper, args, timeout=timeout, kind=SPEECH)

    def alive(self) -> bool:
        """True when the helper still answers ``--version`` within 5 s as the same build.  Never raises."""
        try:
            again = _open(self.helper)
        except OcrError:
            return False
        return (again.name, again.helper_version, again.fluidaudio) == (
            self.name,
            self.helper_version,
            self.fluidaudio,
        )

    def words(self, pcm: Path, *, timeout: float, clip: tuple[int, int] | None = None) -> tuple[Word, ...]:
        """``words PCM --models DIR [--from MS --to MS]``: the words of 16 kHz mono s16le ``pcm``, or of the
        clip ``clip`` only (spec S8 rule 5), whose times stay absolute; raises SpeechError."""
        args = ["words", str(pcm.absolute()), "--models", str(self.models.absolute())]
        if clip is not None:
            start, end = clip
            if start < 0 or start > end:
                raise ValueError(f"not a clip: {clip}")
            args += ["--from", str(start), "--to", str(end)]
        found = []
        for item in _doc(self._run(args, timeout=timeout), "words"):
            text, start_ms, end_ms = _span(item)
            if clip is not None and start_ms < clip[0]:
                raise _bad()
            found.append(Word(text, start_ms, end_ms))
        return tuple(found)

    def voices(self, pcm: Path, *, timeout: float) -> tuple[Segment, ...]:
        """``voices PCM --models DIR --threshold 0.6``: the diarizer's segments of the whole of ``pcm``;
        raises SpeechError."""
        args = ["voices", str(pcm.absolute()), "--models", str(self.models.absolute())]
        args += ["--threshold", repr(THRESHOLD)]
        found = []
        for item in _doc(self._run(args, timeout=timeout), "segments"):
            speaker, start_ms, end_ms = _span(item)
            if not ocr._TOKEN_RE.fullmatch(speaker):
                raise _bad()
            found.append(Segment(speaker, start_ms, end_ms))
        return tuple(found)


def _open(helper: Path) -> _Built:
    """The build behind a helper that answers ``--version``; raises SpeechError, with :data:`_FLOOR_REFUSAL`
    when the FluidAudio commit compiled into it is not the one a build verified."""
    answer = ocr._run_helper(helper, ["--version"], timeout=_VERSION_TIMEOUT_S, kind=SPEECH)
    try:
        doc = json.loads(answer)
    except ValueError:
        doc = None
    if isinstance(doc, dict) and set(doc) == {"engine", "helper", "fluidaudio"}:
        name, version, commit = doc["engine"], doc["helper"], doc["fluidaudio"]
        if (
            isinstance(name, str)
            and isinstance(version, str)
            and isinstance(commit, str)
            and ocr._TOKEN_RE.fullmatch(name)
            and ocr._TOKEN_RE.fullmatch(version)
            and _COMMIT_RE.fullmatch(commit)
        ):  # the commit goes into the recording converter's version, so into page front matter
            if commit != FLUIDAUDIO_PIN:
                raise SpeechError(_FLOOR_REFUSAL)
            return _Built(helper, name, version, commit)
    raise SpeechError("the speech helper's --version answer is not the expected JSON")


# ---------------------------------------------------------------------------------------------------------
# the model folders
# ---------------------------------------------------------------------------------------------------------


def _file_sha(path: Path) -> str:
    """The SHA-256 of a regular file, opened without following a symlink; raises OSError."""
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise OSError(0, "not a regular file")
        return hashlib.file_digest(fh, "sha256").hexdigest()


def folder_digest(folder: Path, files: Sequence[str]) -> str | None:
    """SHA-256 over the sorted lines ``<relative path> <sha256 of file>`` (each ending in a newline) of every
    file in or under the entries ``files`` of ``folder``; an entry that is not there adds nothing.  None when
    a file cannot be read, or a symlink or anything but a file or folder is found."""
    lines = []
    try:
        for name in files:
            top = folder / name
            try:
                st = top.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(st.st_mode):
                lines.append(f"{name} {_file_sha(top)}")
                continue
            if not stat.S_ISDIR(st.st_mode):
                return None
            for root, dirs, names in os.walk(top):
                for d in dirs:
                    if not stat.S_ISDIR(Path(root, d).lstat().st_mode):
                        return None  # a symlinked folder, which os.walk would not enter
                for n in names:
                    path = Path(root, n)
                    lines.append(f"{path.relative_to(folder).as_posix()} {_file_sha(path)}")
    except OSError:
        return None
    return hashlib.sha256("".join(f"{ln}\n" for ln in sorted(lines)).encode()).hexdigest()


def _model_digest() -> str:
    """The digest of both folders together, from their pinned digests: what the identity names."""
    text = "".join(f"{name} {MODEL_DIGESTS[name]}\n" for name in sorted(MODEL_DIGESTS))
    return hashlib.sha256(text.encode()).hexdigest()


def _unplaced(models: Path) -> str:
    """Why speech is off for want of models, or "": a model folder that is not there."""
    missing = [f"{name}/" for name in MODEL_FILES if not (models / name).is_dir()]
    return f"the speech models are not placed: {' and '.join(missing)}" if missing else ""


# ---------------------------------------------------------------------------------------------------------
# probe and engine
# ---------------------------------------------------------------------------------------------------------


def _switched_off(cfg: ConvertConfig) -> str:
    """Why speech is off whatever is built, or "": whatever switches the media helper off."""
    return media._switched_off(cfg)


def _resolve(cfg: ConvertConfig, cache_dir: Path) -> tuple[str, str, _Built | None]:
    off = _switched_off(cfg)
    if off:
        return "off", off, None
    state, detail, found = ocr._look(cache_dir, SPEECH, _open)
    if found is None:
        return state, detail, None
    unplaced = _unplaced(found.helper.parent)
    if unplaced:
        return "off", unplaced, None
    return state, detail, found


def probe(cfg: ConvertConfig, cache_dir: Path) -> tuple[str, str]:
    """(state, detail) for doctor and ``status``: ``ready``, ``off``, ``not-built`` or ``failed``.  Never
    compiles, never raises and takes no digest; ``off`` when the media helper is switched off or a model
    folder is not placed, ``failed`` with a fixed wording naming ``04e363c`` when the helper was built from
    another FluidAudio commit."""
    state, detail, _ = _resolve(cfg, cache_dir)
    return state, detail


def engine(cfg: ConvertConfig, cache_dir: Path) -> SpeechEngine | None:
    """The engine a cycle should use, or None (logged): :func:`probe` is not ``ready``, or a model folder
    does not match its pinned digest.  The one place the digests are taken.  Never compiles, never raises.
    The helper it hands out gets a new modification time, its last use, as the media helper's does."""
    state, detail, found = _resolve(cfg, cache_dir)
    if found is None:
        log.info("speech is %s: %s", state, detail)
        return None
    models = found.helper.parent
    for name, files in MODEL_FILES.items():
        if folder_digest(models / name, files) != MODEL_DIGESTS[name]:
            log.warning("speech is off: the %s model folder does not match its pinned digest", name)
            return None
    with contextlib.suppress(OSError):
        os.utime(found.helper)
    return SpeechEngine(
        found.helper,
        models,
        name=found.name,
        helper_version=found.helper_version,
        fluidaudio=found.fluidaudio,
        model_digest=_model_digest(),
    )


# ---------------------------------------------------------------------------------------------------------
# building the helper (scripts/install.sh only)
# ---------------------------------------------------------------------------------------------------------


def _step(argv: Sequence[str], *, cwd: Path, deadline: float, what: str) -> str:
    """Run one build tool in the build tree within the build's time; its stdout, or SpeechError."""
    left = deadline - _clock()
    if left <= 0:
        raise SpeechError("the speech helper's build ran out of time")
    try:
        cp = subprocess.run(
            list(argv),
            cwd=cwd,
            env=ocr._env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=left,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise SpeechError("the speech helper's build ran out of time") from None
    except OSError as exc:
        raise SpeechError(f"{what} does not run: {ocr._strerror(exc)}") from None
    if cp.returncode != 0:
        raise SpeechError(f"{what} failed (exit {cp.returncode}): {ocr._plain(cp.stderr or cp.stdout)}")
    return cp.stdout.decode("utf-8", errors="replace").strip()


def _owner_only(tree: Path) -> None:
    """Clear group and other bits on everything in ``tree`` (``docs_repo.permissions`` walks the cache);
    symlinks are neither followed nor changed."""
    with contextlib.suppress(OSError):
        for root, dirs, files in os.walk(tree):
            for name in (*dirs, *files):
                with contextlib.suppress(OSError):
                    st = Path(root, name).lstat()
                    if not stat.S_ISLNK(st.st_mode) and st.st_mode & 0o077:
                        Path(root, name).chmod(stat.S_IMODE(st.st_mode) & 0o700)
        tree.chmod(0o700)


def _compile(helper: Path, kind: ocr.Helper = SPEECH) -> None:
    """Build the packaged SwiftPM package into ``helper``: resolve, refuse a FluidAudio checkout older than
    the floor, write ``Pin.swift``, build release.  The tree ``<folder>/build`` is kept (a rebuild is then
    incremental) and owner-only.  Raises SpeechError or OSError."""
    developer = ocr._tool([ocr._XCODE_SELECT, "-p"])
    if not developer or not Path(developer).is_dir():
        raise SpeechError("no Xcode or Command Line Tools (xcode-select -p names no folder)")
    tools = {}
    for tool in ("swift", "git"):
        found = ocr._tool([ocr._XCRUN, "--find", tool])
        if not found or not Path(found).is_file() or not os.access(found, os.X_OK):
            raise SpeechError(f"no {tool} in the developer tools")
        tools[tool] = found
    swift, git = tools["swift"], tools["git"]
    deadline = _clock() + _BUILD_TIMEOUT_S
    tree = helper.parent / _BUILD_TREE
    tree.mkdir(mode=0o700, exist_ok=True)
    try:
        for name, data in _sources().items():
            (tree / name).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            (tree / name).write_bytes(data)
        pin = tree / "Sources" / _TARGET / "Pin.swift"
        pin.unlink(missing_ok=True)
        log.info("building the on-device %s (FluidAudio from github.com; up to 20 minutes)", kind.name)
        _step([swift, "package", "resolve"], cwd=tree, deadline=deadline, what="swift package resolve")
        checkout = tree / ".build" / "checkouts" / "FluidAudio"
        resolved = _step(
            [git, "-C", str(checkout), "rev-parse", "HEAD"], cwd=tree, deadline=deadline, what="git"
        )
        if not _COMMIT_RE.fullmatch(resolved):
            raise SpeechError("the resolved FluidAudio checkout names no commit")
        try:
            _step(
                [git, "-C", str(checkout), "merge-base", "--is-ancestor", FLUIDAUDIO_FLOOR, resolved],
                cwd=tree,
                deadline=deadline,
                what="git merge-base",
            )
        except SpeechError as exc:
            if "ran out of time" in str(exc):
                raise
            raise SpeechError(
                f"FluidAudio {resolved[:7]} does not descend from the build floor 04e363c; "
                "the speech helper is not built"
            ) from None
        pin.write_text(f'let fluidAudioCommit = "{resolved}"\n', encoding="utf-8")
        _step(
            [swift, "build", "-c", "release", "--product", _TARGET],
            cwd=tree,
            deadline=deadline,
            what="swift build",
        )
        built = tree / ".build" / "release" / _TARGET
        if not built.is_file():
            raise SpeechError("swift build did not build the speech helper")
        staged = helper.with_name(f".build-{helper.name}")
        shutil.copyfile(built, staged)
        staged.chmod(0o700)
        staged.replace(helper)
    finally:
        _owner_only(tree)


def build(cache_dir: Path) -> Path:
    """Build the helper into ``<cache_dir>/speech`` (``scripts/install.sh`` only) unless a working one is
    there; raises SpeechError, whose reason is also left beside the helper for :func:`probe`."""
    return ocr._build(cache_dir, SPEECH, _open, _compile)


def _main() -> int:
    """``python -m agentsync.convert.speech``: what scripts/install.sh runs to build the helper.  Prints one
    ``speech: ...`` line; exit 0 when ready or off, 1 when it is not built."""
    return ocr._entry(SPEECH, _switched_off, build, probe, label="speech")


if __name__ == "__main__":
    sys.exit(_main())
