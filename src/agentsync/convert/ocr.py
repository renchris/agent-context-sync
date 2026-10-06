"""On-device OCR: Apple Vision through a small Swift helper (owner: convert).

The engine is ``VNRecognizeTextRequest`` at the accurate level.  Nothing leaves the Mac: no network, no model
download, no third-party service.  The helper's source ships in this package (``vision_ocr.swift``).

Who builds it: only ``scripts/install.sh``, by running ``python -I -m agentsync.convert.ocr``.  :func:`build`
compiles the source with the Command Line Tools' ``swiftc`` into ``<cache_dir>/ocr/``, mode 0700 in a 0700
folder, under a name that is the digest of the source and the build flags.  Nothing else compiles:
:func:`probe` and :func:`engine` only look, so ``status``, a dry run and the LaunchAgent never start a
compiler.  A build that fails leaves its reason beside where the helper goes (``<helper>.failed``).

Before every run the helper must be a regular file this user owns, in a folder this user owns, neither
writable by group or other.  It runs in one fixed environment and is handed file paths after ``--``.  No
error or reason text from this module holds a path: it says "the OCR helper".

Switches: ``[convert] ocr = false``, and ``AGENTSYNC_OCR=0`` (or ``off``) for the test suite.

:func:`text_lines` turns the helper's boxes into reading order: a recursive XY cut (the widest whitespace
gap first) separates columns, paragraphs and diagram labels; short cells that line up in rows (tables,
diagram rows) are read row by row, joined with `` | ``.
"""

from __future__ import annotations

import contextlib
import hashlib
import itertools
import json
import logging
import math
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from agentsync.config import ConvertConfig, load_config
from agentsync.errors import ConfigError, ConversionError
from agentsync.paths import default_cache_dir, default_config_path, expand

log = logging.getLogger(__name__)

LANGUAGES: tuple[str, ...] = ("en-US",)
"""The languages the helper reads.  A constant, not a config key."""
MAX_PAGES = 100
"""The most pages of one document, or frames of one multi-page image, that are read."""
MAX_MEGAPIXELS = 50
"""An image with more pixels is refused before it is decoded (``error`` "too large").  Fifty covers a 48 MP
phone photo and a 600 dpi A4 scan.  Measured on an Apple silicon Mac: a 49 MP page full of text takes about
26 s and 950 MB at its peak; a 300 dpi letter page (8 MP) about 6 s and 340 MB."""

_TILE_PX = 1536  # the helper also reads an image longer than 4/3 of this in overlapping tiles of this size
_MIN_PX = 48  # an image with a shorter side is an icon or a bullet: skipped, not read
_LAYOUT_REVISION = 1
"""Part of :attr:`OcrEngine.identity`.  Bump it when the reading order, the noise rule or an argument the
helper is run with (languages, tile, minimum size, megapixel limit) changes: pages already converted were
laid out by the old rules."""
_NOISE_MAX_CHARS = 2  # a line of one or two characters read with low confidence is an icon or a texture
_NOISE_CONFIDENCE = 0.35
_BATCH = 16  # images per helper run
_VERSION_TIMEOUT_S = 5.0
_TOOL_TIMEOUT_S = 60.0
_BUILD_TIMEOUT_S = 300.0
_PRUNE_AFTER_S = 7 * 24 * 3600.0
_MIN_MACOS = "12.0"
_HELPER_SOURCE = "vision_ocr.swift"
_ENV_SWITCH = "AGENTSYNC_OCR"
_XCODE_SELECT = "/usr/bin/xcode-select"
_XCRUN = "/usr/bin/xcrun"
_ERRORS = frozenset(
    {"not an image", "unsupported image type", "no frames", "too large", "not readable", "recognition failed"}
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")
_PATH_RE = re.compile(
    r"(?P<quote>['\"])~?/.*(?P=quote)"  # a quoted path: through the last such quote on the line
    r"|(?<![\w.<])~?/\S.*?(?=:\d+:\d+: |$)"  # an unquoted one: to a compiler's line:column, else to the end
)
_clock = time.monotonic


class OcrError(ConversionError):
    """The OCR helper could not be built, may not be run, failed or ran out of time."""


@dataclass(frozen=True, slots=True)
class OcrLine:
    """One recognised line; the box is in fractions of the image with the origin at the top-left."""

    text: str
    confidence: float
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True, slots=True)
class OcrImage:
    """What the helper read from one frame of one image.

    ``width`` and ``height`` are upright pixels (after any EXIF rotation); ``frames`` is how many frames the
    file has.  ``skipped`` is an image too small to hold text.  ``error`` is one of "not an image",
    "unsupported image type", "no frames", "too large", "not readable" (all five are facts about the bytes)
    and "recognition failed" (Vision gave up on this frame).  It is fixed wording, never a system message.
    """

    width: int
    height: int
    frames: int
    frame: int
    lines: tuple[OcrLine, ...]
    error: str | None = None
    skipped: bool = False


def _env() -> dict[str, str]:
    """The one environment xcode-select, xcrun, swiftc and the helper run in.  Nothing is inherited but HOME
    and TMPDIR, so DEVELOPER_DIR cannot send the developer-tools check and the tools to different places."""
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"}
    for key in ("HOME", "TMPDIR"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def _plain(text: str | bytes) -> str:
    """The line of a tool's output that says what went wrong, with every file path taken out.

    A path may hold spaces, so nothing says where an unquoted one ends: the rest of the line goes with it,
    but for what follows a compiler's ``:line:column:``.  A quoted one goes through its closing quote."""
    s = text.decode("utf-8", errors="replace") if isinstance(text, bytes) else text
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    line = next((ln for ln in lines if "error:" in ln), lines[0] if lines else "no message")
    line = line.replace(str(Path.home()), "~")
    return _PATH_RE.sub(lambda m: f"{m['quote'] or ''}<path>{m['quote'] or ''}", line)[:200]


def _strerror(exc: OSError) -> str:
    """The operating system's words for ``exc`` without the path ``str(exc)`` would add."""
    return exc.strerror or type(exc).__name__


# ---------------------------------------------------------------------------------------------------------
# running the helper
# ---------------------------------------------------------------------------------------------------------


def _untrusted(helper: Path) -> str | None:
    """Why ``helper`` may not be run, or None.  This does not stop another process of the same user (nothing
    here can); it stops a helper that anyone else could have replaced."""
    try:
        st = helper.lstat()
        folder = helper.parent.lstat()
    except OSError as exc:
        return f"the OCR helper cannot be checked: {_strerror(exc)}"
    uid = os.geteuid()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != uid:
        return "the OCR helper is not a regular file this user owns"
    if not stat.S_ISDIR(folder.st_mode) or folder.st_uid != uid:
        return "the OCR helper's folder is not a folder this user owns"
    if (st.st_mode | folder.st_mode) & 0o022:
        return "the OCR helper or its folder can be written by other users"
    if not st.st_mode & 0o100:
        return "the OCR helper is not executable"
    return None


def _run_helper(helper: Path, args: Sequence[str], *, timeout: float, cwd: Path | None = None) -> bytes:
    """The helper's stdout for ``args``; raises OcrError."""
    refusal = _untrusted(helper)
    if refusal is not None:
        raise OcrError(refusal)
    try:  # no new session: the helper stays in the LaunchAgent's process group, which launchd cleans up
        cp = subprocess.run(
            [str(helper), *args],
            cwd=cwd,
            env=_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:  # its text holds the command line, so the helper's path
        raise OcrError("the OCR helper ran out of time") from None
    except OSError as exc:
        raise OcrError(f"the OCR helper does not run: {_strerror(exc)}") from None
    if cp.stderr:
        log.debug("OCR helper: %s", _plain(cp.stderr))
    if cp.returncode != 0:
        raise OcrError(f"the OCR helper exited {cp.returncode}: {_plain(cp.stderr)}")
    return cp.stdout


def _bad() -> OcrError:
    return OcrError("the OCR helper's answer is not the expected JSON")


def _whole(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _bad()
    return value


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _bad()
    if abs(value) > 1e9 or math.isnan(value):  # Python's JSON reader accepts Infinity and NaN
        raise _bad()
    return float(value)


def _image(result: dict[str, object]) -> OcrImage:
    raw = result.get("lines", [])
    if not isinstance(raw, list):
        raise _bad()
    lines = []
    for ln in raw:
        if not isinstance(ln, dict) or not isinstance(ln.get("text"), str):
            raise _bad()
        lines.append(
            OcrLine(ln["text"], *(_number(ln.get(key)) for key in ("confidence", "x", "y", "w", "h")))
        )
    error, skipped = result.get("error"), result.get("skipped", False)
    if not isinstance(skipped, bool) or not (error is None or isinstance(error, str)):
        raise _bad()
    if error == "no such file":  # the caller's path, not the image's bytes: never a settled result
        raise OcrError("the OCR helper could not open an image it was given")
    if error is not None and error not in _ERRORS:
        raise _bad()
    return OcrImage(
        width=_whole(result.get("width")),
        height=_whole(result.get("height")),
        frames=_whole(result.get("frames")),
        frame=_whole(result.get("frame")),
        lines=tuple(lines),
        error=error,
        skipped=skipped,
    )


def _parse(stdout: bytes, count: int) -> list[tuple[OcrImage, ...]]:
    """The helper's results for ``count`` images: every image present, in order, its frames from 0 up."""
    try:
        doc = json.loads(stdout)
    except ValueError:
        raise _bad() from None
    results = doc.get("results") if isinstance(doc, dict) else None
    if not isinstance(results, list):
        raise _bad()
    out: list[list[OcrImage]] = []
    for result in results:
        if not isinstance(result, dict):
            raise _bad()
        index, image = _whole(result.get("index")), _image(result)
        if image.frame == 0 and index == len(out):
            out.append([image])
        elif out and index == len(out) - 1 and image.frame == len(out[-1]):
            out[-1].append(image)
        else:
            raise _bad()
    if len(out) != count:
        raise _bad()
    return [tuple(frames) for frames in out]


class OcrEngine:
    """A built helper that answered ``--version``."""

    def __init__(self, helper: Path, *, name: str, revision: int, helper_version: str) -> None:
        """Bind ``helper``; the rest is what its ``--version`` printed."""
        self.helper = helper
        self.name = name
        self.revision = revision
        self.helper_version = helper_version

    @property
    def identity(self) -> str:
        """Producer identity for converter versions: engine, its revision, the helper's version and the layout
        revision.  The same on every Mac that runs the same engine and agentsync (no macOS build in it)."""
        return f"ocr-{self.name}-r{self.revision}-h{self.helper_version}-l{_LAYOUT_REVISION}"

    @property
    def description(self) -> str:
        """One line for doctor."""
        return f"{self.name} revision {self.revision}, helper {self.helper_version}"

    def read(
        self, images: Sequence[Path], *, work_dir: Path, budget_s: float, frames: int = 1
    ) -> list[tuple[OcrImage, ...]]:
        """OCR ``images``: one tuple per image, in order, holding its first ``frames`` frames (one, unless the
        caller asks for the pages of a multi-page TIFF; at most MAX_PAGES).

        The helper runs in ``work_dir``, which the caller owns (the cycle's staging folder), on a few images
        at a time.  The whole call takes at most ``budget_s`` seconds: past that OcrError is raised and
        nothing is returned.  OcrError also means the helper could not be run or its answer was not valid.
        A file the helper cannot read is not an exception: its OcrImage carries ``error`` or ``skipped``.
        """
        deadline = _clock() + budget_s
        out: list[tuple[OcrImage, ...]] = []
        for start in range(0, len(images), _BATCH):
            batch = images[start : start + _BATCH]
            left = deadline - _clock()
            if left <= 0:
                raise OcrError("the OCR helper ran out of time")
            args = [
                "--languages",
                ",".join(LANGUAGES),
                "--tile",
                str(_TILE_PX),
                "--frames",
                str(max(1, min(frames, MAX_PAGES))),
                "--min-px",
                str(_MIN_PX),
                "--max-megapixels",
                str(MAX_MEGAPIXELS),
                "--",
                *(str(p.absolute()) for p in batch),
            ]
            out += _parse(_run_helper(self.helper, args, timeout=left, cwd=work_dir), len(batch))
        return out


# ---------------------------------------------------------------------------------------------------------
# finding the helper
# ---------------------------------------------------------------------------------------------------------


def _source() -> bytes:
    return resources.files("agentsync.convert").joinpath(_HELPER_SOURCE).read_bytes()


def _build_flags() -> list[str]:
    return ["-O", "-swift-version", "5", "-target", f"{platform.machine()}-apple-macos{_MIN_MACOS}"]


def _helper_path(cache_dir: Path) -> Path:
    """Where this agentsync's helper is once built: a new source or new flags give a new name."""
    digest = hashlib.sha256(_source() + b"\0" + "\0".join(_build_flags()).encode()).hexdigest()[:16]
    return expand(cache_dir) / "ocr" / f"agentsync-ocr-{digest}"


def _marker(helper: Path) -> Path:
    return helper.with_name(helper.name + ".failed")


def _failure(helper: Path) -> str:
    """Why the last build failed, from the marker beside where the helper goes; "" when there is none."""
    try:
        with os.fdopen(os.open(_marker(helper), os.O_RDONLY | os.O_NOFOLLOW), "rb") as fh:
            text = fh.read(4096)
    except OSError:
        return ""
    return _plain(text) if text.strip() else "the last build of the OCR helper failed"


def _open(helper: Path) -> OcrEngine:
    """The engine for a helper that answers ``--version``; raises OcrError."""
    answer = _run_helper(helper, ["--version"], timeout=_VERSION_TIMEOUT_S)
    try:
        doc = json.loads(answer)
    except ValueError:
        doc = None
    if isinstance(doc, dict):
        name, revision, version = doc.get("engine"), doc.get("revision"), doc.get("helper")
        if (
            isinstance(name, str)
            and isinstance(version, str)
            and isinstance(revision, int)
            and not isinstance(revision, bool)
            and _TOKEN_RE.fullmatch(name)
            and _TOKEN_RE.fullmatch(version)
        ):  # the three go into converter versions, so into page front matter
            return OcrEngine(helper, name=name, revision=revision, helper_version=version)
    raise OcrError("the OCR helper's --version answer is not the expected JSON")


def _switched_off(cfg: ConvertConfig) -> str:
    """Why OCR is off whatever is built, or ""."""
    switch = os.environ.get(_ENV_SWITCH, "").strip().lower()
    if switch in ("0", "off"):
        return f"{_ENV_SWITCH}={switch}"
    if not cfg.ocr:
        return "[convert] ocr = false"
    if sys.platform != "darwin":
        return "on-device OCR needs macOS"
    return ""


def _resolve(cfg: ConvertConfig, cache_dir: Path) -> tuple[str, str, OcrEngine | None]:
    off = _switched_off(cfg)
    if off:
        return "off", off, None
    try:
        helper = _helper_path(cache_dir)
        try:
            helper.lstat()
        except FileNotFoundError:
            built = False
        else:
            built = True
    except OSError as exc:  # the packaged source cannot be read, or the cache folder is not a folder
        return "failed", f"the OCR helper cannot be looked up: {_strerror(exc)}", None
    if built:
        refusal = _untrusted(helper)
        if refusal is not None:
            return "failed", refusal, None
        try:
            found = _open(helper)
        except OcrError as exc:  # built, and it does not answer: OCR is broken, which is more than not built
            return "failed", _failure(helper) or str(exc), None
        return "ready", found.description, found
    failure = _failure(helper)  # the reason of a build that failed is the news, when there is one
    return ("failed", failure, None) if failure else ("not-built", "the OCR helper is not built", None)


def probe(cfg: ConvertConfig, cache_dir: Path) -> tuple[str, str]:
    """(state, detail) for doctor.  Never compiles and never raises.

    - ``ready``: the helper is built and answered; detail describes the engine.
    - ``off``: switched off (``[convert] ocr = false`` or ``AGENTSYNC_OCR=0``), or not macOS.
    - ``not-built``: no helper for this agentsync yet.  ``scripts/install.sh`` builds it.
    - ``failed``: the last build failed (detail is its reason), the helper may not be run, or the helper is
      there and does not answer ``--version`` (detail says why; the next build replaces it).
    """
    state, detail, _ = _resolve(cfg, cache_dir)
    return state, detail


def engine(cfg: ConvertConfig, cache_dir: Path) -> OcrEngine | None:
    """The engine a cycle should use, or None when :func:`probe` is not ``ready`` (logged).  Never compiles
    and never raises: without OCR every converter behaves as it did before OCR existed.

    The helper it hands out gets a new modification time: that is its last use, which is what a later build
    goes by when it removes old helpers (:func:`_prune`).  :func:`probe` only looks and stamps nothing."""
    state, detail, found = _resolve(cfg, cache_dir)
    if found is None:
        log.info("on-device OCR is %s: %s", state, detail)
    else:
        with contextlib.suppress(OSError):
            os.utime(found.helper)
    return found


# ---------------------------------------------------------------------------------------------------------
# building the helper (scripts/install.sh only)
# ---------------------------------------------------------------------------------------------------------


def _tool(argv: Sequence[str]) -> str:
    """What xcode-select or xcrun printed, "" when it failed."""
    try:
        cp = subprocess.run(
            argv,
            env=_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_TOOL_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return cp.stdout.decode("utf-8", errors="replace").strip() if cp.returncode == 0 else ""


def _own_folder(folder: Path) -> None:
    """Create ``<cache_dir>/ocr`` owner-only, or tighten it: it holds a program this process will run."""
    folder.parent.parent.mkdir(parents=True, exist_ok=True)
    for path in (folder.parent, folder):
        path.mkdir(mode=0o700, exist_ok=True)
    st = folder.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
        raise OcrError("the OCR helper's folder is not a folder this user owns")
    folder.chmod(0o700)


def _compile(helper: Path) -> None:
    """Compile the packaged source to ``helper``; raises OcrError or OSError."""
    # xcode-select first: without developer tools the /usr/bin/xcrun shim opens the install dialog.
    developer = _tool([_XCODE_SELECT, "-p"])
    if not developer or not Path(developer).is_dir():
        raise OcrError("no Xcode or Command Line Tools (xcode-select -p names no folder)")
    swiftc = _tool([_XCRUN, "--find", "swiftc"])
    if not swiftc or not Path(swiftc).is_file() or not os.access(swiftc, os.X_OK):
        raise OcrError("no swiftc in the developer tools")
    sdk = _tool([_XCRUN, "--sdk", "macosx", "--show-sdk-path"])
    if not sdk or not Path(sdk).is_dir():
        raise OcrError("no macOS SDK in the developer tools")
    with tempfile.TemporaryDirectory(dir=helper.parent, prefix=".build-", ignore_cleanup_errors=True) as tmp:
        built = Path(tmp) / "agentsync-ocr"
        (Path(tmp) / "main.swift").write_bytes(_source())
        log.info("building the on-device OCR helper")
        try:  # relative names, so a compiler message names main.swift and not the cache folder
            cp = subprocess.run(
                [swiftc, *_build_flags(), "-sdk", sdk, "-o", built.name, "main.swift"],
                cwd=tmp,
                env=_env(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=_BUILD_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise OcrError("swiftc ran out of time building the OCR helper") from None
        if cp.returncode != 0 or not built.is_file():
            raise OcrError(f"swiftc did not build the OCR helper (exit {cp.returncode}): {_plain(cp.stderr)}")
        built.chmod(0o700)
        built.replace(helper)


def _prune(helper: Path) -> None:
    """Remove other helpers, failure markers and abandoned build folders a week after their last use, and
    make the younger ones owner-only.  Nothing is removed at build time just for being old-versioned: a cycle
    that started before an upgrade may still be running its helper.  The last use is the modification time,
    which :func:`engine` renews each time it hands a helper to a cycle; a build's own time would be weeks
    old on the day of an upgrade."""
    cutoff = time.time() - _PRUNE_AFTER_S
    with contextlib.suppress(OSError):
        for entry in sorted(helper.parent.iterdir()):
            if entry == helper or not entry.name.startswith(("agentsync-ocr-", ".build-")):
                continue
            with contextlib.suppress(OSError):
                st = entry.lstat()
                if st.st_mtime >= cutoff:
                    if not stat.S_ISLNK(st.st_mode) and st.st_mode & 0o077:
                        entry.chmod(stat.S_IMODE(st.st_mode) & 0o700)
                elif stat.S_ISDIR(st.st_mode):
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink()


def _tidy(helper: Path) -> None:
    """Make ``helper``'s folder owner-only and :func:`_prune` it, when the folder is there and this user's.
    It creates nothing, so it also runs when nothing is built: ``docs_repo.permissions`` walks ``cache_dir``
    and FAILs on what an earlier build left readable by others."""
    with contextlib.suppress(OSError):
        st = helper.parent.lstat()
        if stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid():
            helper.parent.chmod(0o700)
            _prune(helper)


def build(cache_dir: Path) -> Path:
    """Build the helper under ``<cache_dir>/ocr`` unless a working one is already there; return its path.

    Only ``python -m agentsync.convert.ocr`` calls this (scripts/install.sh).  Raises OcrError with the
    reason; the same reason is left in ``<helper>.failed`` for :func:`probe`, and the next build that works
    removes it.  Whether or not the build works, what earlier builds left in the folder is tidied.
    """
    helper: Path | None = None
    try:
        if sys.platform != "darwin":
            raise OcrError("on-device OCR needs macOS")
        helper = _helper_path(cache_dir)
        _own_folder(helper.parent)
        try:
            _open(helper)
        except OcrError:  # none yet, or one that no longer runs
            _compile(helper)
            _open(helper)
    except (OSError, OcrError) as exc:
        reason = (
            str(exc) if isinstance(exc, OcrError) else f"the OCR helper could not be built: {_strerror(exc)}"
        )
        if helper is not None:
            with contextlib.suppress(OSError):
                fd = os.open(_marker(helper), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(reason + "\n")
        raise OcrError(reason) from None
    finally:
        if helper is not None:
            _tidy(helper)
    with contextlib.suppress(OSError):
        _marker(helper).unlink(missing_ok=True)
    return helper


def _main() -> int:
    """``python -m agentsync.convert.ocr``: what scripts/install.sh runs to build the helper.  It is not an
    agentsync command.  Prints one line; exit 0 when OCR is ready or switched off, 1 when it is not built.
    Switched off, it builds nothing and still tidies the folder an earlier build left."""
    os.umask(0o077)
    cfg, cache_dir = ConvertConfig(), default_cache_dir()
    try:
        if default_config_path().exists():  # before the first add-source there is no config: the defaults
            config = load_config()
            cfg, cache_dir = config.convert, config.cache_dir
    except (ConfigError, OSError):
        sys.stdout.write("OCR helper: not built (the config cannot be read)\n")
        return 1
    off = _switched_off(cfg)
    if off:
        with contextlib.suppress(OSError):  # the packaged source, which names the helper, cannot be read
            _tidy(_helper_path(cache_dir))
        sys.stdout.write(f"OCR helper: off ({off})\n")
        return 0
    try:
        build(cache_dir)
    except OcrError as exc:
        sys.stdout.write(f"OCR helper: not built ({exc})\n")
        return 1
    state, detail = probe(cfg, cache_dir)
    sys.stdout.write(f"OCR helper: {state} ({detail})\n")
    return 0 if state == "ready" else 1


# ---------------------------------------------------------------------------------------------------------
# reading order
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Box:
    text: str
    left: float
    top: float
    right: float
    bottom: float

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2


def _median(values: Sequence[float]) -> float:
    s = sorted(values)
    return s[len(s) // 2] if s else 0.0


def _lo(b: _Box, axis: str) -> float:
    return b.left if axis == "x" else b.top


def _hi(b: _Box, axis: str) -> float:
    return b.right if axis == "x" else b.bottom


def _groups(boxes: Sequence[_Box], axis: str, min_gap: float) -> list[list[_Box]]:
    """Split ``boxes`` at every whitespace gap >= ``min_gap`` along ``axis`` ("x" or "y"), in axis order."""
    ordered = sorted(boxes, key=lambda b: (_lo(b, axis), _hi(b, axis), b.text))
    groups: list[list[_Box]] = []
    end = float("-inf")
    for b in ordered:
        if groups and _lo(b, axis) - end < min_gap:
            groups[-1].append(b)
            end = max(end, _hi(b, axis))
        else:
            groups.append([b])
            end = _hi(b, axis)
    return groups


def _split_widest(boxes: Sequence[_Box], axis: str) -> tuple[float, list[_Box], list[_Box]]:
    """(gap, before, after) at the widest whitespace gap along ``axis`` (first on ties); gap 0 when none."""
    groups = _groups(boxes, axis, 0.0)
    if len(groups) < 2:
        return 0.0, list(boxes), []
    gaps = [
        min(_lo(b, axis) for b in groups[i]) - max(_hi(b, axis) for b in groups[i - 1])
        for i in range(1, len(groups))
    ]
    best = max(range(len(gaps)), key=lambda i: (gaps[i], -i)) + 1
    return gaps[best - 1], [b for g in groups[:best] for b in g], [b for g in groups[best:] for b in g]


def _over(a: _Box, b: _Box) -> bool:
    """True when one box sits over the other: they share more than a quarter of the narrower one's width."""
    shared = min(a.right, b.right) - max(a.left, b.left)
    return shared > 0.25 * min(a.right - a.left, b.right - b.left)


def _rows(boxes: Sequence[_Box]) -> list[list[_Box]]:
    """Boxes grouped into rows, top to bottom, each row left to right.

    A box joins the row whose first box has the nearest centre, when the two centres are within half the
    smaller of the two heights and the box sits over no box already in that row.  The lines of a tilted scan
    have boxes that overlap from top to bottom, so "inside the row's band" would chain a whole page into one
    row; they sit over each other, so each stays its own row.  A tall label beside a table joins the one
    row it is centred on.
    """
    rows: list[list[_Box]] = []
    for b in sorted(boxes, key=lambda b: (b.top, b.left, b.bottom, b.right, b.text)):
        best: list[_Box] | None = None
        nearest = float("inf")
        for row in rows:
            distance = abs(b.cy - row[0].cy)
            if distance > 0.5 * min(b.height, row[0].height) or distance >= nearest:
                continue
            if not any(_over(b, member) for member in row):
                best, nearest = row, distance
        if best is None:
            rows.append([b])
        else:
            best.append(b)
    rows.sort(key=lambda row: (row[0].cy, row[0].left, row[0].text))
    return [sorted(row, key=lambda b: (b.left, b.top, b.text)) for row in rows]


def _aligned_cells(columns: Sequence[Sequence[_Box]], mh: float) -> bool:
    """True when side-by-side groups are table cells / diagram labels rather than columns of prose: short
    texts whose rows line up across the groups."""
    boxes = [b for col in columns for b in col]
    if _median([len(b.text) for b in boxes]) >= 25:
        return False
    first, rest = columns[0], [b for col in columns[1:] for b in col]
    if not first or not rest:
        return False
    aligned = sum(1 for a in first if any(abs(a.cy - b.cy) <= 0.5 * mh for b in rest))
    return aligned >= 0.6 * len(first)


_MAX_DEPTH = 400  # one Python frame per level: far inside the interpreter's limit, deep enough for a page


def _cut(boxes: list[_Box], mh: float, depth: int = 0) -> list[list[_Box]]:
    """Recursive XY cut, one split per level at the widest whitespace gap (columns need 1.5 line heights of
    white, paragraphs 1).  Side-by-side groups of short, row-aligned texts are cut into rows only.  Past
    _MAX_DEPTH the boxes left are one block: nothing is dropped."""
    if len(boxes) < 2 or depth > _MAX_DEPTH:
        return [boxes]
    thr_x, thr_y = 1.5 * mh, 1.0 * mh
    gap_x, left, right = _split_widest(boxes, "x")
    gap_y, above, below = _split_widest(boxes, "y")
    if max(gap_x / thr_x, gap_y / thr_y) < 1.0:
        return [boxes]
    if gap_x / thr_x > gap_y / thr_y:
        if _aligned_cells([left, right], mh):
            return _groups(boxes, "y", thr_y)
        return _cut(left, mh, depth + 1) + _cut(right, mh, depth + 1)
    return _cut(above, mh, depth + 1) + _cut(below, mh, depth + 1)


def text_lines(image: OcrImage) -> list[str]:
    """Recognised text in reading order: one string per row, "" between blocks (no markdown escaping).

    A line of one or two characters read with confidence under 0.35 is dropped as noise."""
    width = image.width or 1000
    height = image.height or 1000
    boxes = []
    for ln in image.lines:
        text = " ".join(ln.text.split())
        if text and not (len(text) <= _NOISE_MAX_CHARS and ln.confidence < _NOISE_CONFIDENCE):
            boxes.append(
                _Box(text, ln.x * width, ln.y * height, (ln.x + ln.w) * width, (ln.y + ln.h) * height)
            )
    if not boxes:
        return []
    mh = max(_median([b.height for b in boxes]), 1.0)
    out: list[str] = []
    for block in _cut(boxes, mh):
        if out:
            out.append("")
        for row in _rows(block):
            parts = [row[0].text]
            for prev, cur in itertools.pairwise(row):
                parts.append((" | " if cur.left - prev.right > 1.0 * mh else " ") + cur.text)
            out.append("".join(parts))
    return out


if __name__ == "__main__":
    sys.exit(_main())
