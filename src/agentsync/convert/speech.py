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

The build floor (C3): FluidAudio ``04e363c`` or later, the first build with the silence-aware FBank, is a
build-time check: the build refuses a resolved checkout that does not descend from it, leaving the reason
beside the helper.  The helper reports the commit compiled into it, and probe refuses any commit other than
:data:`FLUIDAUDIO_PIN`, an exact match (B13 round 19 W67).

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
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from agentsync.config import ConvertConfig
from agentsync.convert import media, ocr
from agentsync.convert.ocr import OcrError
from agentsync.skill import INSTALL_SH

log = logging.getLogger(__name__)

FLUIDAUDIO_FLOOR = "04e363c29d9a754022d602d6fe1468ab80a0f705"
"""The oldest FluidAudio commit the speech helper may be built from (spec C3)."""
FLUIDAUDIO_PIN = FLUIDAUDIO_FLOOR
"""The commit ``speech_helper/Package.swift`` builds; at or after :data:`FLUIDAUDIO_FLOOR`."""
NEMO_URL = (
    "https://github.com/FluidInference/text-processing-rs/releases/download/v0.3.1/"
    "NemoTextProcessing.xcframework.zip"
)
"""The one remote binaryTarget (NemoTextProcessing) FluidAudio's Package.swift and Package@swift-6.2.swift
name at :data:`FLUIDAUDIO_PIN`.  SwiftPM downloads it itself, ignoring the proxy variables, so behind a proxy
set only in the environment the build fetches it with curl first (:func:`_seed`)."""
NEMO_SHA256 = "5fa8c10d4ec26c1bb2413125f351a7222a4c68a23b74476680fbada7e26fc6aa"
"""The checksum those manifests pin for :data:`NEMO_URL` (SwiftPM's checksum is the zip's SHA-256)."""
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
_SWIFTPM = "swiftpm"  # SwiftPM's cache, configuration and security folders, beside the build tree
_BUILD_TIMEOUT_S = 1200.0  # cold builds took 279 to 451 s; OCR's 300 s was sized for one swiftc file
_VERSION_TIMEOUT_S = 5.0
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
# the installer by its path, as doctor's texts name it: the prompt never runs from inside the checkout, so a
# bare scripts/install.sh is not found (B13 round 18 V68, after round 17 U30). Probe's check is an exact match
# to the pin, so the refusal names the pin, never "or later" (B13 round 19 W67)
_PIN_REFUSAL = (
    f"the speech helper is not built from FluidAudio {FLUIDAUDIO_PIN[:7]}, the commit this agentsync pins; "
    f"{INSTALL_SH} rebuilds it"
)
BUILD_TIMED_OUT = "the speech helper's build ran out of time"
"""How the reason of a build that ran out of its :data:`_BUILD_TIMEOUT_S` starts, in ``<helper>.failed`` and
so in doctor's ``speech`` warn: doctor gives that warn its fix, and the setup report its own bucket, by these
words (B13 round 18 V5, V28). The time covers the FluidAudio fetch as well as the compile, so the words name
no cause."""
_TIMED_OUT = f"{BUILD_TIMED_OUT}; {INSTALL_SH} tries the build again on its next run"  # by its path (V68)
_clock = time.monotonic


class SpeechError(OcrError):
    """The speech helper could not be built, may not be run, failed, ran out of time, or answered with
    something that is not the expected JSON.  Its text holds no path but the installer's
    (``skill.INSTALL_SH``), which a build failure names as its fix (B13 round 19 W55)."""


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
    """The build behind a helper that answers ``--version``; raises SpeechError, with :data:`_PIN_REFUSAL`
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
                raise SpeechError(_PIN_REFUSAL)
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
        return "off", f"the speech helper is built; {unplaced}", None
    return state, detail, found


def probe(cfg: ConvertConfig, cache_dir: Path) -> tuple[str, str]:
    """(state, detail) for doctor and ``status``: ``ready``, ``off``, ``not-built`` or ``failed``.  Never
    compiles, never raises and takes no digest; ``off`` when the media helper is switched off or, with the
    helper built, a model folder is not placed (the detail then says the helper is built), ``failed`` with a
    fixed wording naming the pinned commit when the helper was built from another FluidAudio commit."""
    state, detail, _ = _resolve(cfg, cache_dir)
    return state, detail


def build_timed_out(cache_dir: Path) -> bool:
    """Whether the last build of the speech helper under ``cache_dir`` ran out of time: its marker's reason
    starts with :data:`BUILD_TIMED_OUT` (a marker an older build wrote, without the retry clause, too).
    Never raises."""
    try:
        return ocr._failure(ocr._helper_path(cache_dir, SPEECH), SPEECH).startswith(BUILD_TIMED_OUT)
    except Exception:  # a cache that cannot be looked at says nothing about the build
        return False


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


_PROXY_VARS = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
)
"""The shell's proxy variables, which the build's tools get and the helper never does: SwiftPM clones
FluidAudio with git, which reads them, as step 1's git does (setup prompt step 1 names HTTPS_PROXY)."""

_CA_VARS = ("GIT_SSL_CAINFO", "GIT_SSL_CAPATH", "CURL_SSL_BACKEND")
"""The shell's CA variables the build's git reads (Xcode's git, libcurl 8.7.1 with LibreSSL): behind TLS
inspection they are how the shell's git trusts the inspecting CA, so SwiftPM's clone needs them too.
SSL_CERT_FILE, SSL_CERT_DIR and CURL_CA_BUNDLE are left out of swift and git because that git ignores them
(measured); only the seed's curl gets them (:data:`_CURL_CA_VARS`).  GIT_SSL_NO_VERIFY and DEVELOPER_DIR never
reach the build."""

_CURL_CA_VARS = ("CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR")
"""The shell's CA variables /usr/bin/curl 8.7.1 reads and Xcode's git ignores (both measured under env -i:
each pointed at a missing path changed curl's CAfile or CApath, and git still cloned): behind TLS inspection
they are how the shell's curl trusts the inspecting CA, so :func:`_seed`'s curl gets them, and nothing else
does."""


def _shell(keys: Sequence[str]) -> dict[str, str]:
    """The shell's non-empty values of ``keys``."""
    return {key: value for key in keys if (value := os.environ.get(key))}


def _build_env() -> dict[str, str]:
    """:func:`ocr._env` plus whichever of :data:`_PROXY_VARS` and :data:`_CA_VARS` the shell sets."""
    return {**ocr._env(), **_shell((*_PROXY_VARS, *_CA_VARS))}


_GIT_URL = NEMO_URL
"""The url whose settings :func:`_git_config` reads: the one the seed's curl fetches. A setting git scopes to
github.com, or to any path NEMO_URL is under (``[http "https://github.com/FluidInference"]``), counts. One
scoped to FluidAudio's own repository path routes the clone but not the seed, and one scoped to the seed's
repository routes the seed but not the clone: git matches by path prefix and takes the most specific match
(B13 round 5 F78, round 6 G63)."""
_GIT_SETTINGS = ("http.sslcainfo", "http.sslcapath", "http.sslbackend", "http.proxy")
"""The git settings the seed's curl follows, as ``git config`` names them.  http.sslVerify is never read:
verification is never turned off."""


def _git_config(git: str, *, cwd: Path, deadline: float) -> dict[str, str]:
    """git's own values of :data:`_GIT_SETTINGS` for :data:`_GIT_URL`, from the config files the build's
    ``git clone`` reads (the system, global and XDG ones, never an enclosing repository's: ``GIT_DIR`` is
    /dev/null for this one call, since a home folder that is itself a git repository would otherwise lend its
    ``.git/config`` to a call run under ``~/Library/Caches``, B13 round 11 M68): one
    ``git config --type=path --get-urlmatch http <url>``, so a url-scoped
    ``[http "https://github.com"]`` value counts and a ``~`` path is expanded (``--type=path`` changes no
    value that does not start with ``~``; measured with Xcode's git 2.50.1).  Nothing set (git exits 1), a
    git that does not run and the build's time running out all read as nothing set.  An empty http.proxy is
    kept as ``""``: it is git's way to turn a proxy off, and git then goes direct whatever the environment
    says (measured with Xcode's git 2.50.1: ls-remote with HTTPS_PROXY at a closed port succeeded)."""
    left = deadline - _clock()
    if left <= 0:
        return {}
    try:
        cp = subprocess.run(
            [git, "config", "--type=path", "--get-urlmatch", "http", _GIT_URL],
            cwd=cwd,
            env={**_build_env(), "GIT_DIR": os.devnull},  # only here: swift and git -C share _build_env
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=min(left, ocr._TOOL_TIMEOUT_S),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if cp.returncode != 0:
        return {}
    found: dict[str, str] = {}
    for line in cp.stdout.decode("utf-8", errors="replace").splitlines():
        key, _, value = line.partition(" ")
        key = key.lower()
        if key in _GIT_SETTINGS and (value.strip() or key == "http.proxy"):
            found[key] = value.strip()
    return found


def _curl_env(git_config: Mapping[str, str]) -> dict[str, str]:
    """:func:`_build_env` plus the shell's :data:`_CURL_CA_VARS`, for :func:`_seed`'s curl, which trusts and
    routes as step 1's git does, from the environment and from git's config files (``git_config``, from
    :func:`_git_config`), with git's precedence:

    * The bundle git trusts (GIT_SSL_CAINFO, else http.sslCAInfo; curl reads neither) becomes CURL_CA_BUNDLE,
      unless the shell names a bundle of curl's own (measured with /usr/bin/curl 8.7.1: CURL_CA_BUNDLE at an
      empty file fails with exit 77, at /etc/ssl/cert.pem it fetches).
    * The CA folder git trusts (GIT_SSL_CAPATH, else http.sslCAPath) becomes SSL_CERT_DIR, unless the shell
      sets one.  A CA folder adds to the bundle curl uses (measured with /usr/bin/curl 8.7.1 and LibreSSL: a
      server signed by a CA in a hashed folder fetched with SSL_CERT_DIR at that folder beside
      /etc/ssl/cert.pem, and exited 60 with GIT_SSL_CAPATH alone; github.com still fetched with SSL_CERT_DIR
      at that folder or at one that does not exist).  Under CURL_SSL_BACKEND=secure-transport curl ignores
      it, as before.
    * http.sslBackend becomes CURL_SSL_BACKEND unless the shell sets it.
    * git's http.proxy wins over the environment's proxy, as it does in git, so it becomes HTTPS_PROXY and
      https_proxy (curl reads the lower-case one first); in the environment, not on argv, where ``ps`` would
      show a proxy's credentials.  An empty one turns the environment's proxy off, as it does for git."""
    env = {**_build_env(), **_shell(_CURL_CA_VARS)}
    cainfo = env.get("GIT_SSL_CAINFO") or git_config.get("http.sslcainfo")
    if cainfo and "CURL_CA_BUNDLE" not in env and "SSL_CERT_FILE" not in env:
        env["CURL_CA_BUNDLE"] = cainfo
    capath = env.get("GIT_SSL_CAPATH") or git_config.get("http.sslcapath")
    if capath and "SSL_CERT_DIR" not in env:
        env["SSL_CERT_DIR"] = capath
    backend = git_config.get("http.sslbackend")
    if backend and "CURL_SSL_BACKEND" not in env:
        env["CURL_SSL_BACKEND"] = backend
    proxy = git_config.get("http.proxy")
    if proxy:
        env["HTTPS_PROXY"] = env["https_proxy"] = proxy
    elif proxy == "":
        for key in _PROXIES:
            env.pop(key, None)
    return env


def _step(
    argv: Sequence[str], *, cwd: Path, deadline: float, what: str, env: dict[str, str] | None = None
) -> str:
    """Run one build tool in the build tree within the build's time (in ``env``, else :func:`_build_env`);
    its stdout, or SpeechError."""
    left = deadline - _clock()
    if left <= 0:
        raise SpeechError(_TIMED_OUT)
    try:
        cp = ocr._run_build(argv, cwd=cwd, timeout=left, env=_build_env() if env is None else env)
    except subprocess.TimeoutExpired:
        raise SpeechError(_TIMED_OUT) from None
    except OSError as exc:
        raise SpeechError(f"{what} does not run: {ocr._strerror(exc)}") from None
    if cp.returncode < 0:  # a signal; only a crash's own stderr says why (stdout is progress)
        raise SpeechError(ocr._signal_reason(f"{what} was ", cp.returncode, cp.stderr))
    if cp.returncode != 0:
        raise SpeechError(f"{what} failed (exit {cp.returncode}): {ocr._plain(cp.stderr or cp.stdout)}")
    return cp.stdout.decode("utf-8", errors="replace").strip()


_CURL = "/usr/bin/curl"
_CURL_FLAGS = (
    *("--fail", "--silent", "--show-error", "--location", "--proto", "=https"),
    *("--connect-timeout", "30", "--speed-limit", "1024", "--speed-time", "60"),
)
"""HTTPS only, through redirects (GitHub serves releases from another host); a stall of a minute below
1 KB/s ends the fetch rather than the build's twenty minutes."""
_PROXIES = ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")
"""The variables that route an https url through a proxy, for curl and git alike: both ignore HTTP_PROXY for
an https url (measured with /usr/bin/curl 8.7.1: HTTP_PROXY at a closed port still fetched directly), and
NO_PROXY names none."""
_SEED_PREFIX = ".seed-"


def _artifact_name(url: str) -> str:
    """SwiftPM's file name for a downloaded binary artifact in ``<cache-path>/artifacts``: the URL with
    every character that is not a letter or digit made ``_`` (SwiftPM's internal layout, measured on Swift
    6.2.4)."""
    return re.sub(r"[^A-Za-z0-9]", "_", url)


def _seed(artifacts: Path, *, git: str, cwd: Path, deadline: float) -> None:
    """Behind a proxy set in the environment or in git's config (``git``, the build's), put FluidAudio's
    binary artifact where SwiftPM looks before it downloads (it then prints "Fetched ... from cache"): curl,
    which trusts and routes as git does (:func:`_curl_env`), fetches
    :data:`NEMO_URL` to a temporary file, and only a file whose SHA-256 is :data:`NEMO_SHA256` is renamed into
    place.  Any failure removes the temporary file and leaves SwiftPM to download it as before; SwiftPM checks
    the checksum again either way.  A run stopped mid-fetch by SIGTERM or SIGKILL never reaches the
    ``finally``, so every run first removes the temporary files earlier runs left."""
    with contextlib.suppress(OSError):
        for stale in artifacts.glob(f"{_SEED_PREFIX}*"):
            with contextlib.suppress(OSError):
                stale.unlink()
    final = artifacts / _artifact_name(NEMO_URL)
    if final.exists():
        return
    git_config = _git_config(git, cwd=cwd, deadline=deadline)
    proxy = git_config.get("http.proxy")
    if proxy == "" or (proxy is None and not _shell(_PROXIES)):
        return  # no proxy, or git's empty http.proxy: git goes direct, so there is nothing to route
    try:
        artifacts.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=_SEED_PREFIX, dir=artifacts)
    except OSError:
        return
    os.close(fd)
    temporary = Path(name)
    try:
        _step(
            [_CURL, *_CURL_FLAGS, "--output", str(temporary), NEMO_URL],
            cwd=cwd,
            deadline=deadline,
            what="curl",
            env=_curl_env(git_config),
        )
        if _file_sha(temporary) != NEMO_SHA256:
            log.info("the NemoTextProcessing artifact fetched through the proxy has the wrong checksum")
            return
        temporary.replace(final)
    except (SpeechError, OSError):
        log.info("NemoTextProcessing could not be fetched through the proxy; SwiftPM downloads it itself")
    finally:
        temporary.unlink(missing_ok=True)


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


_SHARED_CONFIG = ("Library/org.swift.swiftpm/configuration", ".swiftpm/configuration")
"""Where SwiftPM keeps the person's shared configuration, under HOME, in the order it reads them."""
_SHARED_FILES = ("mirrors.json", "registries.json")


def _shared_config(config: Path) -> None:
    """Copy the person's shared ``mirrors.json`` and ``registries.json`` into the build's ``--config-path``
    folder, every build: that flag replaces the shared configuration folder outright (measured on Swift
    6.2.4: a mirror there resolved under its own --config-path and was "not found" under another), so a Mac
    that reaches GitHub only through a SwiftPM mirror or registry could not build.  A file gone from the
    shared folder is gone from the copy.  The folder is found from HOME, so a rehearsal that sets HOME never
    reads the real ~/Library.  The copy is owner-only, since a mirror's url can carry a token; one that
    cannot be read or written is skipped."""
    with contextlib.suppress(OSError):
        config.mkdir(mode=0o700, exist_ok=True)
    for name in _SHARED_FILES:
        copy = config / name
        with contextlib.suppress(OSError):
            copy.unlink(missing_ok=True)
            for folder in _SHARED_CONFIG:
                source = Path.home() / folder / name
                if source.is_file():
                    shutil.copyfile(source, copy)
                    copy.chmod(0o600)
                    break


def _compile(helper: Path, kind: ocr.Helper = SPEECH) -> None:
    """Build the packaged SwiftPM package into ``helper``: resolve, refuse a FluidAudio checkout older than
    the floor, write ``Pin.swift``, build release.  The tree ``<folder>/build`` is kept (a rebuild is then
    incremental) and owner-only, and so is ``<folder>/swiftpm``: both steps get its ``cache``, ``config``
    and ``security`` folders, since SwiftPM on Darwin finds its user-level folders through Foundation, which
    ignores HOME, and would otherwise write the real ``~/Library``.  The flags do not keep SwiftPM off the
    real ``~/.swiftpm`` links, which both steps re-create whatever the flags say (CONTRACTS §16.102 N18), so
    the two swift steps alone also get ``CFFIXED_USER_HOME``, Foundation's home, set to ``swiftpm/home``:
    the links and the ``<home>_.swiftpm.lock`` in ``$TMPDIR`` are then the build's own.  SwiftPM finds its
    default netrc through that home too, so the person's ``~/.netrc``, when there is one, is passed as
    ``--netrc-file``.  git and the seed's curl read HOME and keep it (B13 round 13 O69).  Raises
    SpeechError or OSError."""
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
    spm = helper.parent / _SWIFTPM
    try:
        spm.mkdir(mode=0o700, exist_ok=True)
        _shared_config(spm / "config")
        places = [
            arg for what in ("cache", "config", "security") for arg in (f"--{what}-path", str(spm / what))
        ]
        home = spm / "home"
        home.mkdir(mode=0o700, exist_ok=True)
        netrc = Path.home() / ".netrc"
        if netrc.is_file():
            places += ["--netrc-file", str(netrc)]
        spm_env = {**_build_env(), "CFFIXED_USER_HOME": str(home)}
        for name, data in _sources().items():
            (tree / name).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            (tree / name).write_bytes(data)
        pin = tree / "Sources" / _TARGET / "Pin.swift"
        pin.unlink(missing_ok=True)
        log.info("building the on-device %s (FluidAudio from github.com; up to 20 minutes)", kind.name)
        _seed(spm / "cache" / "artifacts", git=git, cwd=tree, deadline=deadline)
        _step(
            [swift, "package", *places, "resolve"],
            cwd=tree,
            deadline=deadline,
            what="swift package resolve",
            env=spm_env,
        )
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
            if not str(exc).startswith("git merge-base failed (exit "):  # only the check's own answer refuses
                raise
            raise SpeechError(
                f"FluidAudio {resolved[:7]} does not descend from the build floor 04e363c; "
                "the speech helper is not built"
            ) from None
        pin.write_text(f'let fluidAudioCommit = "{resolved}"\n', encoding="utf-8")
        _step(
            [swift, "build", *places, "-c", "release", "--product", _TARGET],
            cwd=tree,
            deadline=deadline,
            what="swift build",
            env=spm_env,
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
        _owner_only(spm)


def build(cache_dir: Path) -> Path:
    """Build the helper into ``<cache_dir>/speech`` (``scripts/install.sh`` only) unless a working one is
    there; raises SpeechError, whose reason is also left beside the helper for :func:`probe`."""
    return ocr._build(cache_dir, SPEECH, _open, _compile)


def _main() -> int:
    """``python -m agentsync.convert.speech``: what scripts/install.sh runs to build the helper.  Prints one
    ``speech: ...`` line; exit 0 when ready or off, 1 when it is not built or is built and fails its probe
    (the line says which), 78 when the config cannot be read."""
    return ocr._entry(SPEECH, _switched_off, build, probe, label="speech")


if __name__ == "__main__":
    sys.exit(_main())
