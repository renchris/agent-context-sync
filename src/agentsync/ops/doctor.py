"""Preflight checks with a concrete fix per failure, run by ``agentsync status`` (owner: ops).

Every probe that touches the machine or another module is a private module-level function (``_git_path``,
``_run``, ``_volume_uuid``, ``_auth_status``, ...) so tests replace it with a fake; ``run_checks`` never
raises for a single check: a probe that crashes becomes a failed :class:`CheckResult` naming the exception
(:func:`unfinished`), and a program that does not answer in time becomes one that says the check ran out of
time. Every FAIL names a fix: scripts/install.sh tells its reader so.
"""

from __future__ import annotations

import ctypes
import enum
import errno
import logging
import os
import plistlib
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentsync.arm_local import LISTING_TIMEOUT_S as _ARM_LISTING_TIMEOUT_S
from agentsync.arm_local import CallTimedOutError, call_with_timeout
from agentsync.config import Config, SourceConfig, canonical_source_root
from agentsync.cycle import _sync_leaves
from agentsync.errors import ConfigError
from agentsync.loop import NO_NEXT_HINT_ENV
from agentsync.model import SourceKind
from agentsync.ops import launchd
from agentsync.ops.lock import SingleWriterLock, read_heartbeat
from agentsync.paths import expand, is_cloud_path

log = logging.getLogger(__name__)

_MIN_PYTHON = (3, 11)
_MIN_FREE_BYTES = 2 * 1024**3
_MAX_LOG_BYTES = 64 * 1024**2
_STALE_FACTOR = 3  # design 4.7 watcher rule: cursor/success age > 3 x cadence
_INCOMPLETE_RUNS = 3  # consecutive passes with enumeration_complete false before we warn
_POLICY_NAMES = {0: "default", 1: "off", 2: "on"}
AGENT_STEP_PENDING_ENV = "AGENTSYNC_AGENT_STEP_PENDING"
"""Set to 1 by scripts/install.sh --confirm-install-agent for its doctor step: the LaunchAgents are installed
by its later agent step, so a launchd.* fix that is install-agent reads :data:`AGENT_STEP_NOTE` instead."""
AGENT_STEP_NOTE = "installed by the agent step below"
_AGENT_STEP_FIXES = ("agentsync install-agent", "launchctl bootstrap ")
# NO_NEXT_HINT_ENV (agentsync.loop, its one definition) is 1 under scripts/install.sh: its NEXT: line is
# the only instruction in its output, so the ad hoc launcher's Developer ID fix (an IT action, nothing the
# person or their agent does) reads ADHOC_IT_NOTE, with no ``fix:``.
ADHOC_IT_NOTE = "for IT: Developer ID build (docs/deploy/mdm)"
_XCODE_SELECT = "/usr/bin/xcode-select"
_DEVTOOLS_TIMEOUT_S = 5.0
# Only scripts/install.sh builds the OCR, media and speech helpers (convert/ocr.py, convert/media.py,
# convert/speech.py), so the ocr, media and speech texts name it and no agentsync command.  Without developer
# tools it builds nothing, so there the not-built line says what it waits for.
_OCR_NOT_BUILT = "scripts/install.sh builds it"
_OCR_NOT_BUILT_NO_DEVTOOLS = (
    "scripts/install.sh builds it once the Command Line Tools are installed (xcode-select --install)"
)
_OCR_DEVTOOLS_FIX = "xcode-select --install, then run scripts/install.sh again"

# Also under NO_NEXT_HINT_ENV, when install.sh was not asked for the agent step: a launchd.* warn whose fix is
# install-agent or a bootstrap (LaunchAgents left by an earlier install) is the person's to refresh, so it
# reads this note, with no ``fix:``. A FAIL keeps its fix.
_AGENT_YOURS_NOTE = "background sync is yours to refresh, not a setup step"
# Also under NO_NEXT_HINT_ENV: a local folder's listing that stays incomplete is the person's to settle (an
# empty cloud folder, a folder without access), and install.sh prints the loop's line for it.
_LISTING_YOURS_NOTE = "yours: see WAITING ON YOU"

# The bundled pandoc is an Intel (x86_64) program, in the Apple silicon wheel of pypandoc_binary too, so
# macOS runs it through Rosetta, and Rosetta translates a file it has not seen once, at its first start.
# `uv tool install --force` writes a new copy at every update. Measured 2026-10-07 on a 119 MB pandoc 3.9:
# 10 to 67 s for the first `--version` of a fresh copy (the longer times on a loaded Mac), under 1 s after.
# scripts/install.sh therefore starts it once before it runs status (its "pandoc: ..." lines), outside this
# limit; a status run by hand right after an update can still meet a cold one.
_PANDOC_TIMEOUT_S = 60.0
_PANDOC_SLOW_START = (
    "the first start of a newly installed pandoc can take a minute, and later ones take under a second"
)
_EBADARCH = getattr(errno, "EBADARCH", 86)  # "Bad CPU type in executable": an Intel program and no Rosetta

# A check that ran out of time has no answer: a slow program and one that never answers read the same from
# here, so the line does not say which it was.  _STILL_NO_ANSWER is what the fix names for a second such
# line when the check has no fix of its own for a program that does not work.
_OUT_OF_TIME = "the check ran out of time, so it could not say whether anything is wrong"
_STILL_NO_ANSWER = "report it (the program does not answer on this Mac, and no setup step clears that)"

# A check that crashed is a fault in agentsync, which nothing on this Mac clears. -v logs the traceback.
# An operating-system error is not that: the Mac's state causes it and can clear it (see _os_error).
_CRASH_FIX = (
    "agentsync status -v (prints the traceback: a check that crashes is a fault in agentsync to report, "
    "and no setup step clears it)"
)
_POLICY_FAULT_FIX = (
    "report this line: agentsync cannot use this process's download policy on this macOS, and no setup step "
    "clears that"
)


class Severity(enum.StrEnum):
    """How bad a failed check is."""

    ERROR = "error"  # sync will fail or be wrong
    WARN = "warn"  # sync works, something is degraded
    INFO = "info"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One check."""

    name: str
    ok: bool
    detail: str
    severity: Severity = Severity.ERROR
    fix: str | None = None  # the exact command or setting that fixes it
    note: str | None = None  # shown in brackets instead of a fix a caller has already scheduled


@dataclass(frozen=True, slots=True)
class _AuthProbe:
    """What doctor needs from graph.auth (no secrets, no network)."""

    signed_in: bool
    username: str | None
    cache_backend: str


# ---------------------------------------------------------------------------------------------------------
# probes (replaced by fakes in tests)
# ---------------------------------------------------------------------------------------------------------


def _python_version() -> tuple[int, int, int]:
    """The running interpreter's version."""
    v = sys.version_info
    return (v.major, v.minor, v.micro)


def _now() -> datetime:
    """Current UTC time (doctor output is diagnostic, never content)."""
    return datetime.now(UTC)


def _run(argv: Sequence[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` with launchd's PATH, capturing text output; never raises on a non-zero exit. A program
    that does not end within ``timeout`` raises ``subprocess.TimeoutExpired``, which the check that asked
    turns into :func:`_timed_out` (and :func:`run_checks` does for one that gets past its check)."""
    env = {"PATH": launchd.LAUNCHD_PATH, "LC_ALL": "C", "HOME": str(Path.home()), "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(list(argv), capture_output=True, text=True, check=False, timeout=timeout, env=env)


def _git_path() -> Path:
    """The git the sync will use (gitops' resolver once merged; else /usr/bin/git, else launchd PATH)."""
    try:
        from agentsync import gitops  # noqa: PLC0415 - lazy: doctor must import even if gitops is broken

        return gitops.git_executable()
    except NotImplementedError:
        pass
    candidate = Path("/usr/bin/git")
    if os.access(candidate, os.X_OK):
        return candidate
    found = shutil.which("git", path=launchd.LAUNCHD_PATH)
    if found is None:
        raise FileNotFoundError("git not found in /usr/bin or on launchd's PATH")
    return Path(found)


def _pandoc_path(config: Config) -> Path:
    """The pandoc the converters will run: [convert] pandoc_path, else pypandoc_binary's bundled binary."""
    if config.convert.pandoc_path is not None:
        return config.convert.pandoc_path
    import pypandoc  # noqa: PLC0415 - lazy: optional at doctor import time

    return Path(pypandoc.__file__).parent / "files" / "pandoc"


def _ocr_status(config: Config) -> tuple[str, str]:
    """(state, detail) of the on-device OCR helper, from ``convert.ocr.probe``: it looks and never compiles;
    the one program it may start is a built helper's ``--version``, which it gives 5 s."""
    from agentsync.convert import ocr  # noqa: PLC0415 - lazy: doctor must import even if convert is broken

    return ocr.probe(config.convert, config.cache_dir)


def _media_status(config: Config) -> tuple[str, str]:
    """(state, detail) of the media helper, from ``convert.media.probe``: as :func:`_ocr_status`, it looks and
    never compiles; the one program it may start is a built helper's ``--version``, which it gives 5 s."""
    from agentsync.convert import media  # noqa: PLC0415 - lazy: doctor must import even if convert is broken

    return media.probe(config.convert, config.cache_dir)


def _speech_status(config: Config) -> tuple[str, str]:
    """(state, detail) of the speech engine, from ``convert.speech.probe``: it looks, never compiles and
    takes no model digest; the one program it may start is a built helper's ``--version``, given 5 s."""
    from agentsync.convert import speech  # noqa: PLC0415 - lazy: doctor must import even if convert is broken

    return speech.probe(config.convert, config.cache_dir)


def _devtools_missing() -> bool:
    """True when ``xcode-select -p`` names no folder: no Xcode and no Command Line Tools.  Asked by full
    path, never through the ``/usr/bin`` tool shims, which would open the install dialog."""
    try:
        cp = _run([_XCODE_SELECT, "-p"], timeout=_DEVTOOLS_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return False  # unknown: no fix is better than a wrong one
    return not (cp.returncode == 0 and cp.stdout.strip() and Path(cp.stdout.strip()).is_dir())


def _materialize_policy() -> int:
    """The process's materialise-dataless-files I/O policy (materialise.get_materialize_policy)."""
    from agentsync import materialise  # noqa: PLC0415

    return materialise.get_materialize_policy()


def _volume_uuid(path: Path) -> str:
    """Volume UUID of ``path`` (arm_local.volume_uuid)."""
    from agentsync import arm_local  # noqa: PLC0415

    return arm_local.volume_uuid(path)


def _auth_status(config: Config) -> _AuthProbe:
    """Cached sign-in state and token-cache backend (graph.auth; no network)."""
    from agentsync.graph import auth  # noqa: PLC0415

    provider = auth.MsalAuth(auth.settings_from_config(config))
    status = provider.status()
    return _AuthProbe(status.signed_in, status.username, status.cache_backend)


def _is_loaded(label: str) -> bool:
    """launchd.is_loaded with the real launchctl."""
    return launchd.is_loaded(label)


# How long doctor waits for a source root's first entry (arm_local.LISTING_TIMEOUT_S); tests shorten it.
_LISTING_TIMEOUT_S = _ARM_LISTING_TIMEOUT_S


def _first_entry(path: Path) -> str | None:
    """Name of the first directory entry of ``path`` (None when empty); lists metadata only, opens no file."""
    with os.scandir(path) as it:
        entry = next(it, None)
    return None if entry is None else entry.name


def _is_missing(path: Path) -> bool:
    """Whether ``path`` is known to be gone.  A read that fails any other way (an I/O error, a timeout) is
    not that answer: ``Path.exists`` raises it on Python 3.11 to 3.13 and reads it as "missing" from 3.14."""
    try:
        path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError:
        return False
    return False


def _disk_free(path: Path) -> int:
    """Free bytes available to this user on the volume holding ``path``."""
    return shutil.disk_usage(path).free


def _process_image() -> Path:
    """The executable image this process runs (what TCC grants attach to), via proc_pidpath."""
    if sys.platform == "darwin":
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            buf = ctypes.create_string_buffer(4096)
            n = libc.proc_pidpath(os.getpid(), buf, ctypes.c_uint32(len(buf)))
            if n > 0:
                return Path(buf.raw[:n].decode("utf-8", errors="replace"))
        except (OSError, AttributeError):
            pass
    return Path(os.path.realpath(sys.executable))


@dataclass(frozen=True, slots=True)
class _CodeSignature:
    """What ``codesign`` says about the launcher (no network, nothing written)."""

    valid: bool
    verify_detail: str  # codesign --verify output when invalid
    identifier: str | None
    team_id: str | None  # None when "not set" (ad hoc)
    adhoc: bool
    hardened_runtime: bool
    requirement: str | None  # the designated requirement (``codesign -d -r-``)


def _find_launcher() -> Path | None:
    """launchd.find_launcher (``$AGENTSYNC_LAUNCHER`` or ~/Applications/AgentSyncLauncher.app)."""
    return launchd.find_launcher()


def _codesign_info(path: Path) -> _CodeSignature:
    """Verify and describe the signature of ``path`` (an .app bundle or a Mach-O) with /usr/bin/codesign."""
    codesign = "/usr/bin/codesign"
    verify = _run([codesign, "--verify", "--strict", "--verbose=1", str(path)], timeout=60)
    info = _run([codesign, "-d", "--verbose=2", str(path)], timeout=60)
    req = _run([codesign, "-d", "-r-", str(path)], timeout=60)
    fields: dict[str, str] = {}
    for line in (info.stderr + info.stdout).splitlines():
        key, sep, value = line.partition("=")
        if sep and key and " " not in key:
            fields.setdefault(key, value.strip())
    requirement = None
    for line in (req.stdout + req.stderr).splitlines():
        text = line.lstrip("# ").strip()
        if text.startswith("designated => "):
            requirement = text[len("designated => ") :]
    cd_flags = next(
        (line for line in (info.stderr + info.stdout).splitlines() if line.startswith("CodeDirectory ")), ""
    )
    team = fields.get("TeamIdentifier")
    return _CodeSignature(
        valid=verify.returncode == 0,
        verify_detail=(verify.stderr or verify.stdout).strip().splitlines()[-1] if verify.returncode else "",
        identifier=fields.get("Identifier"),
        team_id=None if team in (None, "not set") else team,
        adhoc=fields.get("Signature") == "adhoc" or "adhoc" in cd_flags,
        hardened_runtime="runtime" in cd_flags,
        requirement=requirement,
    )


def _launcher_canary(launcher: Path, path: Path, timeout_s: float) -> tuple[int, str]:
    """Run the launcher's canary on ``path`` *as its own responsible process* (``--self-responsible``), so the
    answer is the launcher's TCC grant, not this terminal's.  Metadata-only: lstat + one directory entry or an
    open-and-close; nothing is read, hydrated or written.  Returns (exit code, last log line)."""
    argv = [
        str(launcher),
        "--self-responsible",
        "--canary-only",
        "--canary-timeout",
        str(timeout_s),
        "--canary",
        str(path),
    ]
    try:
        cp = _run(argv, timeout=timeout_s + 15)
    except subprocess.TimeoutExpired:
        return launchd.EXIT_TCC_PENDING, f"launcher did not return within {timeout_s + 15:.0f}s"
    lines = [ln for ln in (cp.stderr or "").splitlines() if ln.strip()]
    return cp.returncode, lines[-1].split("]: ", 1)[-1] if lines else ""


def _in_launchd_job(config: Config) -> bool:
    """True when doctor itself runs inside one of our LaunchAgents (so TCC results are authoritative)."""
    return os.environ.get("XPC_SERVICE_NAME", "").startswith(config.launchd_label_prefix + ".")


# ---------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------


def _fda_target(image: Path) -> Path:
    """The path to add under Full Disk Access: the enclosing .app bundle if there is one, else the binary."""
    for parent in image.parents:
        if parent.suffix == ".app":
            return parent
    return image


def _fda_fix(image: Path) -> str:
    """The exact Full Disk Access instruction for ``image``."""
    target = _fda_target(image)
    return (
        f"grant Full Disk Access to {target} (System Settings > Privacy & Security > Full Disk Access > '+', "
        "Cmd-Shift-G to paste the path; on a managed Mac: an MDM PPPC profile allowing SystemPolicyAllFiles "
        "for it)"
    )


def _nearest_existing(path: Path) -> Path:
    """``path`` or its closest existing ancestor."""
    p = expand(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def _gib(n: int) -> str:
    """Format a byte count in GiB with one decimal."""
    return f"{n / 1024**3:.1f} GiB"


def _age_s(iso: object, now: datetime) -> int | None:
    """Seconds since an ISO-8601 ``...Z`` timestamp; None if absent or malformed."""
    if not isinstance(iso, str):
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00") if iso.endswith("Z") else iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int((now - dt).total_seconds())


def _ok(name: str, detail: str, severity: Severity = Severity.INFO) -> CheckResult:
    """A passing check."""
    return CheckResult(name=name, ok=True, detail=detail, severity=severity)


def _bad(name: str, detail: str, severity: Severity = Severity.ERROR, fix: str | None = None) -> CheckResult:
    """A failing check."""
    return CheckResult(name=name, ok=False, detail=detail, severity=severity, fix=fix)


def _again_fix(fallback: str | None = None) -> str:
    """The fix for a check that ran out of time: run again as it is, and what to do when the same line
    comes back.  Under scripts/install.sh (:data:`NO_NEXT_HINT_ENV`) the run is the command its ``NEXT:``
    line names, which a setup agent may run; else ``agentsync status``.  A program that was only slow
    answers the next time.  One that never answers gives the same line at every run, so the fix does not
    end at "again": it names ``fallback`` (the check's own fix), else says to report the line."""
    if os.environ.get(NO_NEXT_HINT_ENV, "").strip() == "1":
        again = "run the same scripts/install.sh command again as it is (its NEXT line names it)"
    else:
        again = "agentsync status (run it again as it is)"
    return f"{again}; if this line comes back: {fallback or _STILL_NO_ANSWER}"


def _timed_out(
    name: str,
    exc: subprocess.TimeoutExpired,
    severity: Severity = Severity.ERROR,
    *,
    what: str | None = None,
    why: str | None = None,
    fallback: str | None = None,
) -> CheckResult:
    """A check whose program did not answer within its limit. That is not a crash and not a finding either
    way: the line says the check ran out of time, and its fix is to run the checks again and what to do if
    that changes nothing (:func:`_again_fix`).  ``what`` is the command as the line names it (default: the
    program's file name), ``why`` what is known about the wait, ``fallback`` the check's own fix for a
    program that does not work."""
    if what is None:
        argv = exc.cmd if isinstance(exc.cmd, list | tuple) else [exc.cmd]
        what = Path(str(argv[0])).name if argv else "the program"
    detail = f"{what} did not answer within {exc.timeout:.0f}s: {_OUT_OF_TIME}"
    return _bad(name, f"{detail} ({why})" if why else detail, severity, fix=_again_fix(fallback))


def _os_error(name: str, exc: OSError, severity: Severity) -> CheckResult:
    """A check that stopped on an operating-system error none of its own rules caught: a folder that is a
    file, an I/O error, a sync app or a network that stopped answering.  That is the system's answer, and
    the person or a later run often clears it, so it is not called a crash: the line names the path the
    error is about, when it has one, and its fix says to look there and run again, then to report a line
    that stays."""
    reason = exc.strerror or str(exc) or type(exc).__name__
    stays = "if the line stays, report it: agentsync status -v prints where the check stopped"
    if isinstance(exc.filename, str | bytes | os.PathLike):
        path = os.fsdecode(exc.filename)
        fix = f"check that {path} is there and can be opened, then run again ({stays})"
        return _bad(name, f"could not read {path}: {reason}", severity, fix=fix)
    detail = f"stopped on a system error: {type(exc).__name__}: {reason}"
    return _bad(name, detail, severity, fix=f"run again ({stays})")


def unfinished(name: str, exc: Exception, severity: Severity = Severity.ERROR) -> CheckResult:
    """The result of a check that raised instead of answering, so no line of ``status`` lacks what to do
    next: a ``subprocess.TimeoutExpired`` is :func:`_timed_out`; an ``OSError`` is :func:`_os_error`
    (``could not read <path>: <reason>``); anything else is ``check crashed: <type>: <message>`` with the
    fix that prints its traceback and says to report it.  The traceback is logged here, at info level
    (``-v``), so that both of those fixes hold for every caller."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return _timed_out(name, exc, severity)
    if isinstance(exc, OSError):
        log.info("doctor: check %s stopped on a system error", name, exc_info=exc)
        return _os_error(name, exc, severity)
    log.info("doctor: check %s crashed", name, exc_info=exc)
    return _bad(name, f"check crashed: {type(exc).__name__}: {exc}", severity, fix=_CRASH_FIX)


# ---------------------------------------------------------------------------------------------------------
# checks, in run order
# ---------------------------------------------------------------------------------------------------------


def _check_python(config: Config) -> list[CheckResult]:
    """Python >= 3.11."""
    v = _python_version()
    text = f"Python {v[0]}.{v[1]}.{v[2]} at {sys.executable}"
    if v[:2] >= _MIN_PYTHON:
        return [_ok("python", text)]
    return [_bad("python", f"{text}; agentsync needs >= 3.11", fix="uv python install 3.11 && uv sync")]


def _check_git(config: Config) -> list[CheckResult]:
    """git resolves to an absolute path and runs."""
    try:
        git = _git_path()
    except (OSError, ValueError) as exc:
        return [_bad("git", f"not found: {exc}", fix="xcode-select --install")]
    if not git.is_absolute():
        return [_bad("git", f"resolved to a relative path {git}", fix="install git at /usr/bin/git")]
    try:
        cp = _run([str(git), "--version"], timeout=30)
    except subprocess.TimeoutExpired as exc:
        return [_timed_out("git", exc, what=f"{git} --version")]
    except OSError as exc:
        return [
            _bad("git", f"{git} could not be started: {exc.strerror or exc}", fix="xcode-select --install")
        ]
    out = cp.stdout.strip()
    if cp.returncode == 0 and out.startswith("git version"):
        return [_ok("git", f"{out} at {git}")]
    detail = (cp.stderr or cp.stdout).strip().splitlines()
    first = detail[0] if detail else f"exit {cp.returncode}"
    return [_bad("git", f"{git} --version failed: {first}", fix="xcode-select --install")]


def _check_pandoc(config: Config) -> list[CheckResult]:
    """pandoc (configured or bundled) runs and reports a version.  One that does not answer within
    ``_PANDOC_TIMEOUT_S`` is most often starting for the first time (see that constant): the line says so
    and its fix is to run again, then this check's own fix if the line comes back (a pandoc that never
    answers reads the same, and running again does not clear that one).  One macOS will not start is a
    FAIL with this check's fix, and says when the reason is an Intel program on a Mac without Rosetta."""
    own = "set [convert] pandoc_path to an absolute pandoc"
    fix = f"uv sync (reinstalls pypandoc_binary) or {own}"
    try:
        pandoc = _pandoc_path(config)
    except ImportError as exc:
        return [_bad("pandoc", f"pypandoc_binary is not importable: {exc}", fix=fix)]
    if not pandoc.is_absolute():
        return [
            _bad("pandoc", f"pandoc path {pandoc} is not absolute (launchd has no Homebrew PATH)", fix=fix)
        ]
    if not os.access(pandoc, os.X_OK):
        return [_bad("pandoc", f"{pandoc} is missing or not executable", fix=fix)]
    try:
        cp = _run([str(pandoc), "--version"], timeout=_PANDOC_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        return [_timed_out("pandoc", exc, what=f"{pandoc} --version", why=_PANDOC_SLOW_START, fallback=fix)]
    except OSError as exc:
        detail = f"{pandoc} could not be started: {exc.strerror or exc}"
        if exc.errno == _EBADARCH:
            # Installing Rosetta changes the system, so the fix gives it to the person and not to a setup
            # agent, in the words of _AGENT_YOURS_NOTE. The command is whole: without --agree-to-license it
            # stops at a license question (`softwareupdate --help`: that flag is "without user interaction").
            detail += " (it is an Intel program, which an Apple silicon Mac runs only with Rosetta)"
            fix = (
                "Rosetta is yours to install, not a setup step (IT's on a managed Mac): softwareupdate "
                f"--install-rosetta --agree-to-license; or {own} built for this Mac"
            )
        return [_bad("pandoc", detail, fix=fix)]
    lines = cp.stdout.strip().splitlines()
    if cp.returncode == 0 and lines and lines[0].startswith("pandoc"):
        return [_ok("pandoc", f"{lines[0]} at {pandoc}")]
    return [
        _bad(
            "pandoc", f"{pandoc} --version failed (exit {cp.returncode}): {cp.stderr.strip()[:200]}", fix=fix
        )
    ]


def _check_ocr(config: Config) -> list[CheckResult]:
    """On-device OCR, which is optional: ok when the helper is ready or OCR is switched off; a not-ok INFO
    line when the helper is not built (scripts/install.sh builds it; this check never compiles); WARN with
    the reason when the last build failed, or the helper is there and may not be run or does not answer,
    with a fix only when the developer tools are missing.  Never a FAIL: a probe that crashes is a WARN
    too.

    scripts/install.sh builds nothing on a Mac without developer tools and leaves no failure to report, so
    the not-built line says there that the build waits for them.  It stays INFO with no fix."""
    try:
        state, detail = _ocr_status(config)
    except Exception as exc:
        log.debug("doctor: the OCR probe crashed", exc_info=True)
        return [_bad("ocr", f"on-device OCR could not be checked: {type(exc).__name__}", Severity.WARN)]
    if state == "ready":
        return [_ok("ocr", f"on-device OCR is ready: {detail}")]
    if state == "off":
        return [_ok("ocr", f"on-device OCR is off: {detail}")]
    if state == "not-built":
        how = _OCR_NOT_BUILT_NO_DEVTOOLS if _devtools_missing() else _OCR_NOT_BUILT
        return [_bad("ocr", f"{detail}; {how}", Severity.INFO)]
    fix = _OCR_DEVTOOLS_FIX if _devtools_missing() else None
    return [_bad("ocr", f"on-device OCR is not working: {detail}", Severity.WARN, fix=fix)]


def _check_media(config: Config) -> list[CheckResult]:
    """The media helper that reads recordings, optional as OCR is and checked the same way: ok when ready or
    switched off (``[convert] recordings = false`` among the switches); a not-ok INFO line when it is not
    built (scripts/install.sh builds it; this check never compiles); WARN with the reason when it failed,
    with a fix only when the developer tools are missing.  Never a FAIL."""
    try:
        state, detail = _media_status(config)
    except Exception as exc:
        log.debug("doctor: the media probe crashed", exc_info=True)
        return [_bad("media", f"media helper: could not be checked ({type(exc).__name__})", Severity.WARN)]
    if state in ("ready", "off"):
        return [_ok("media", f"media helper: {state} ({detail})")]
    if state == "not-built":
        how = _OCR_NOT_BUILT_NO_DEVTOOLS if _devtools_missing() else _OCR_NOT_BUILT
        return [_bad("media", f"{detail}; {how}", Severity.INFO)]
    fix = _OCR_DEVTOOLS_FIX if _devtools_missing() else None
    return [_bad("media", f"media helper: not working ({detail})", Severity.WARN, fix=fix)]


def _check_speech(config: Config) -> list[CheckResult]:
    """The speech engine (words and voices of a recording), optional as the media helper is and checked the
    same way, one ``speech: <state> (<detail>)`` line: ok when ready or off (whatever switches the media
    helper off, or a model folder the operator has not placed); a not-ok INFO line when the helper is not
    built; WARN with the reason when it failed, a FluidAudio build older than 04e363c among them.  The probe
    takes no model digest and never compiles.  Never a FAIL."""
    try:
        state, detail = _speech_status(config)
    except Exception as exc:
        log.debug("doctor: the speech probe crashed", exc_info=True)
        return [_bad("speech", f"speech: could not be checked ({type(exc).__name__})", Severity.WARN)]
    if state in ("ready", "off"):
        return [_ok("speech", f"speech: {state} ({detail})")]
    if state == "not-built":
        how = _OCR_NOT_BUILT_NO_DEVTOOLS if _devtools_missing() else _OCR_NOT_BUILT
        return [_bad("speech", f"speech: {state} ({detail}; {how})", Severity.INFO)]
    fix = _OCR_DEVTOOLS_FIX if _devtools_missing() else None
    return [_bad("speech", f"speech: {state} ({detail})", Severity.WARN, fix=fix)]


def _check_config(config: Config) -> list[CheckResult]:
    """Keys sources.toml still accepts but ignores (KISS K15): ``[graph] company`` gets one WARN naming the
    line to delete; nothing is printed when there is none."""
    line = config.graph_company_line
    if line is None:
        return []
    where = f"line {line} of" if line else "the [graph] company key in"
    return [
        _bad(
            "config.graph_company",
            "[graph] company is ignored: the User-Agent is always NONISV|agentsync|agentsync/<version>",
            Severity.WARN,
            fix=f"delete {where} {config.config_path}",
        )
    ]


def _check_docs_repo(config: Config) -> list[CheckResult]:
    """docs_repo outside CloudStorage, a git repo (or creatable), no symlinks."""
    repo = expand(config.docs_repo)
    out: list[CheckResult] = []
    real = Path(os.path.realpath(repo))
    if is_cloud_path(repo) or is_cloud_path(real):
        out.append(
            _bad(
                "docs_repo.location",
                f"{repo} (real path {real}) is inside ~/Library/CloudStorage; a git dir in a File Provider "
                "tree inherits every sync-client failure",
                fix="set [agentsync] docs_repo to a local path such as ~/agent-context/docs",
            )
        )
    else:
        out.append(_ok("docs_repo.location", f"{repo} is outside ~/Library/CloudStorage"))

    is_git = False
    if not repo.exists() and not repo.is_symlink():
        anchor = _nearest_existing(repo.parent)
        if anchor.is_dir() and os.access(anchor, os.W_OK | os.X_OK):
            out.append(
                _ok("docs_repo.git", f"{repo} does not exist yet; `agentsync add-source <folder>` creates it")
            )
        else:
            out.append(
                _bad(
                    "docs_repo.git",
                    f"{repo} does not exist and {anchor} is not writable",
                    fix=f"mkdir -p {repo}",
                )
            )
    elif not repo.is_dir():
        out.append(_bad("docs_repo.git", f"{repo} exists but is not a directory", fix=f"move {repo} aside"))
    elif (repo / ".git").exists():
        is_git = True
        out.append(_ok("docs_repo.git", f"{repo} is a git repository"))
    else:
        out.append(
            _ok(
                "docs_repo.git",
                f"{repo} is not a git repo yet; `agentsync add-source <folder>` runs git init",
            )
        )

    if repo.is_symlink():
        out.append(
            _bad(
                "docs_repo.symlinks",
                f"{repo} is a symlink to {repo.readlink()}",
                fix=f"set [agentsync] docs_repo to the real directory {real}",
            )
        )
    elif is_git:
        git = _git_path()
        try:
            cp = _run([str(git), "-C", str(repo), "ls-files", "-s", "-z"], timeout=120)
        except subprocess.TimeoutExpired as exc:
            # A warn, as a git that exits non-zero here is: the lines above stand and no sync is stopped.
            out.append(_timed_out("docs_repo.symlinks", exc, Severity.WARN, what="git ls-files"))
            return out
        if cp.returncode != 0:
            out.append(
                _bad("docs_repo.symlinks", f"git ls-files failed: {cp.stderr.strip()[:200]}", Severity.WARN)
            )
        else:
            links = sorted(
                rec.split("\t", 1)[1]
                for rec in cp.stdout.split("\0")
                if rec.startswith("120000 ") and "\t" in rec
            )
            if links:
                shown = ", ".join(links[:5]) + (f" (+{len(links) - 5} more)" if len(links) > 5 else "")
                out.append(
                    _bad(
                        "docs_repo.symlinks",
                        f"{len(links)} tracked symlink(s): {shown}",
                        fix=f"git -C {repo} rm --cached <path> for each, and replace them with real files",
                    )
                )
            else:
                out.append(_ok("docs_repo.symlinks", "no tracked symlinks"))
    else:
        out.append(_ok("docs_repo.symlinks", "not a symlink"))
    return out


def _check_state_dir(config: Config) -> list[CheckResult]:
    """state_dir exists with mode 0700 (owned by this user); the db and token fallback are 0600."""
    sp = config.state_paths
    root = expand(sp.root)
    out: list[CheckResult] = []
    try:
        st = root.stat()
    except FileNotFoundError:
        out.append(
            _bad(
                "state_dir",
                f"{root} does not exist; the first sync creates it",
                Severity.WARN,
                fix=f"mkdir -m 700 -p '{root}'",
            )
        )
        return out
    if not stat.S_ISDIR(st.st_mode):
        out.append(_bad("state_dir", f"{root} is not a directory", fix=f"move '{root}' aside"))
        return out
    mode = stat.S_IMODE(st.st_mode)
    if st.st_uid != os.getuid():
        out.append(
            _bad(
                "state_dir",
                f"{root} is owned by uid {st.st_uid}, not {os.getuid()}",
                fix=f"sudo chown -R {os.getuid()} '{root}'",
            )
        )
    elif mode != 0o700:
        out.append(
            _bad(
                "state_dir",
                f"{root} has mode {mode:04o}; cursors and tokens need 0700",
                fix=f"chmod 700 '{root}'",
            )
        )
    else:
        out.append(_ok("state_dir", f"{root} (mode 0700)"))

    loose: list[str] = []
    present: list[str] = []
    for p in (
        sp.db,
        sp.db.with_name(sp.db.name + "-wal"),
        sp.db.with_name(sp.db.name + "-shm"),
        sp.token_cache_fallback,
    ):
        try:
            m = stat.S_IMODE(p.lstat().st_mode)
        except FileNotFoundError:
            continue
        present.append(p.name)
        if m & 0o077:
            loose.append(f"{p.name} ({m:04o})")
    if loose:
        paths = " ".join(f"'{root / n.split(' ')[0]}'" for n in loose)
        out.append(
            _bad("state_dir.files", f"readable by others: {', '.join(loose)}", fix=f"chmod 600 {paths}")
        )
    elif present:
        out.append(_ok("state_dir.files", f"{', '.join(present)} are 0600"))
    else:
        out.append(_ok("state_dir.files", "no manifest yet (created 0600 by the first sync)"))
    return out


def _check_local_source(config: Config, src: SourceConfig, image: Path) -> list[CheckResult]:
    """One local/inbox source: listable, sentinel present, volume UUID readable."""
    base = f"source.{src.id}"
    if not src.is_live:
        return [_ok(base, f"{src.state.value}: not checked")]
    if src.path is None:
        return [_bad(base, "no path configured", fix=f"set path in the [[source]] with id = {src.id!r}")]
    root = expand(src.path)
    cloud = is_cloud_path(root)
    missing_fix = f"fix path in [[source]] id = {src.id!r}, or sign in to the sync client"
    # An error with no rule of its own (an I/O error, a sync app that stopped answering) is the person's to
    # look at, so its fix says where: for the listing, and for the sentinel read below.
    sync_app = " and that its sync app is running and signed in" if cloud else ""
    look_fix = f"check that {root} opens in Finder{sync_app}"
    out: list[CheckResult] = []
    listed = False
    try:
        first = call_with_timeout(lambda: _first_entry(root), _LISTING_TIMEOUT_S, name="doctor-listing")
        listed = True
        if first is None and src.kind is SourceKind.INBOX and not cloud:
            # every config has an inbox since KISS K05; empty is its normal state, not a finding. The hint
            # is for the folder config.ensure_inbox keeps: any other inbox source is one the person added.
            kept = canonical_source_root(expand(config.docs_repo).parent / "inbox")
            hint = " (drop files you save by hand here)" if src.path == kept else ""
            out.append(_ok(f"{base}.listable", f"{root} is empty{hint}"))
        elif first is None:
            why = (
                "a File Provider folder that has not been enumerated, or a TCC denial reading as empty"
                if cloud
                else "the folder is empty"
            )
            out.append(
                _bad(
                    f"{base}.listable",
                    f"{root} lists 0 entries ({why})",
                    Severity.WARN,
                    fix=f"open {root} in Finder once; if still empty: {_fda_fix(image)}" if cloud else None,
                )
            )
        else:
            out.append(_ok(f"{base}.listable", f"{root} is listable"))
    except CallTimedOutError:
        # A listing that never returns is macOS holding the read for an Allow prompt in this terminal: a FAIL
        # like EPERM (never "empty"), and the sentinel and volume reads below would wait on it too.
        out.append(
            _bad(
                f"{base}.listable",
                f"{root}: listing did not return within {_LISTING_TIMEOUT_S:.0f}s; macOS is most likely "
                "waiting for you to click Allow on a privacy prompt (it can sit behind other windows)",
                fix=f"click Allow on the macOS prompt, then re-run; if no prompt shows: {_fda_fix(image)}",
            )
        )
        return out
    except FileNotFoundError:
        out.append(_bad(f"{base}.listable", f"{root} does not exist", fix=missing_fix))
    except NotADirectoryError:
        out.append(
            _bad(
                f"{base}.listable",
                f"{root} is not a directory",
                fix=f"point [[source]] id = {src.id!r} path at a folder",
            )
        )
    except PermissionError as exc:
        if exc.errno == errno.EPERM:
            out.append(
                _bad(
                    f"{base}.listable",
                    f"{root}: Operation not permitted (macOS privacy/TCC)",
                    fix=_fda_fix(image),
                )
            )
        else:
            out.append(_bad(f"{base}.listable", f"{root}: permission denied", fix=f"chmod u+rx '{root}'"))
    except OSError as exc:
        out.append(_bad(f"{base}.listable", f"{root}: {exc.strerror or exc}", fix=look_fix))

    if src.sentinel:
        target = root / src.sentinel
        try:
            target.lstat()  # lstat never hydrates a dataless placeholder
            out.append(_ok(f"{base}.sentinel", f"{src.sentinel} present"))
        except FileNotFoundError:
            out.append(
                _bad(
                    f"{base}.sentinel",
                    f"{target} is missing: every pass would be incomplete (no deletions, no cursor)",
                    fix=f"restore {target}, or change sentinel in [[source]] id = {src.id!r}",
                )
            )
        except PermissionError:
            out.append(_bad(f"{base}.sentinel", f"{target}: not permitted (TCC)", fix=_fda_fix(image)))
        except OSError as exc:
            # Any other answer (the path is a file, an I/O error, a sync app that stopped answering) is this
            # line's own: leaving the check with it cost the source its other lines and their fixes.
            out.append(_bad(f"{base}.sentinel", f"{target}: {exc.strerror or exc}", fix=look_fix))
    elif cloud:
        # add-source writes the sentinel as a commented recommendation, and the walk already holds deletions
        # for a cloud folder it cannot list or finds empty: optional, so not a warn to chase.
        out.append(
            _ok(
                f"{base}.sentinel",
                "no sentinel configured (optional: a cloud folder that is empty or cannot be listed already "
                "holds deletions)",
            )
        )
    else:
        out.append(_ok(f"{base}.sentinel", "no sentinel configured"))

    kind = "File Provider root" if cloud else "volume"
    if not listed and _is_missing(root):
        out.append(_bad(f"{base}.volume", f"{kind} UUID not checked: {root} is missing", fix=missing_fix))
        return out
    try:
        uuid = _volume_uuid(root)
        out.append(_ok(f"{base}.volume", f"{kind} UUID {uuid}"))
    except NotImplementedError:
        out.append(
            _bad(
                f"{base}.volume",
                f"{kind} UUID not checked: arm_local.volume_uuid is not implemented",
                Severity.WARN,
            )
        )
    except OSError as exc:
        out.append(
            _bad(
                f"{base}.volume",
                f"{kind} UUID unreadable for {root}: {exc}; stable ids cannot be formed",
                fix="check the volume is mounted (diskutil info <mount>)",
            )
        )
    return out


def _check_sources(config: Config) -> list[CheckResult]:
    """Every local/inbox source in configuration order, plus the TCC context note for cloud roots."""
    image = _process_image()
    out: list[CheckResult] = []
    local = [s for s in config.sources if s.kind in (SourceKind.LOCAL, SourceKind.INBOX)]
    for src in local:
        try:
            out.extend(_check_local_source(config, src, image))
        except Exception as exc:
            out.append(unfinished(f"source.{src.id}", exc))
    if any(s.is_live and s.path is not None and is_cloud_path(s.path) for s in local):
        if _in_launchd_job(config):
            out.append(
                _ok("tcc", f"checked inside the LaunchAgent ({image}): results above are authoritative")
            )
        else:
            try:
                launcher = _find_launcher()
            except ConfigError:
                launcher = None
            subject = (launchd.launcher_app(launcher) or launcher) if launcher is not None else image
            out.append(
                _ok(
                    "tcc",
                    f"cloud roots were listed with this terminal's privacy grants; the LaunchAgent runs "
                    f"{subject} and needs its own (the launcher and tcc.* lines below report it)",
                )
            )
    return out


def _check_materialise(config: Config) -> list[CheckResult]:
    """The materialise-dataless-files I/O policy is readable (so fail-closed can be enforced)."""
    if sys.platform != "darwin":
        return [_ok("materialise.policy", f"not macOS ({sys.platform}): no dataless files")]
    try:
        p = _materialize_policy()
    except NotImplementedError:
        return [
            _bad(
                "materialise.policy",
                "not checked: materialise.get_materialize_policy is not implemented",
                Severity.WARN,
            )
        ]
    except OSError as exc:
        return [
            _bad(
                "materialise.policy",
                f"getiopolicy_np failed: {exc}; hydration cannot be fail-closed",
                fix=_POLICY_FAULT_FIX,
            )
        ]
    name = _POLICY_NAMES.get(p, f"unknown({p})")
    if p not in _POLICY_NAMES:
        return [_bad("materialise.policy", f"unexpected process policy {name}", fix=_POLICY_FAULT_FIX)]
    return [_ok("materialise.policy", f"process policy {name}; the cycle sets it off and opts in per read")]


def _check_graph(config: Config) -> list[CheckResult]:
    """Client id set, token cache backend, signed in (cache only, no network)."""
    live_graph = [s.id for s in config.live_sources() if s.kind.is_graph]
    if config.graph.client_id is None:
        return [_ok("graph.client_id", "not set: Graph sources are off; local sources work without IT")]
    out = [_ok("graph.client_id", f"{config.graph.client_id} (authority {config.graph.authority})")]
    severity = Severity.ERROR if live_graph else Severity.WARN
    try:
        status = _auth_status(config)
    except NotImplementedError:
        out.append(_bad("graph.auth", "not checked: graph.auth is not implemented", Severity.WARN))
        return out
    except ConfigError as exc:  # [graph] itself is wrong (e.g. a multi-tenant authority): not a cache fault
        out.append(
            _bad("graph.config", str(exc), severity, fix="edit [graph] in sources.toml (see the message)")
        )
        return out
    except Exception as exc:  # MSAL / Keychain errors are many; each is a diagnosis, not a crash
        out.append(
            _bad(
                "graph.auth",
                f"cannot read the token cache: {type(exc).__name__}: {exc}",
                severity,
                fix="agentsync logout && agentsync login",
            )
        )
        return out
    if status.cache_backend == "keychain":
        out.append(_ok("graph.token_cache", "login Keychain"))
    else:
        out.append(
            _bad(
                "graph.token_cache",
                f"{status.cache_backend}: the Keychain was unavailable",
                Severity.WARN,
                fix="run `agentsync login` from a logged-in GUI session so the login Keychain is used",
            )
        )
    if status.signed_in:
        out.append(_ok("graph.signed_in", f"as {status.username or '<unknown user>'}"))
    else:
        needed = f" ({len(live_graph)} live Graph source(s): {', '.join(live_graph)})" if live_graph else ""
        out.append(_bad("graph.signed_in", f"no cached account{needed}", severity, fix="agentsync login"))
    return out


def _check_disk(config: Config) -> list[CheckResult]:
    """At least 2 GiB free on the state and docs volumes."""
    out: list[CheckResult] = []
    for name, path in (("disk.state", config.state_dir), ("disk.docs", config.docs_repo)):
        anchor = _nearest_existing(path)
        free = _disk_free(anchor)
        if free >= _MIN_FREE_BYTES:
            out.append(_ok(name, f"{_gib(free)} free at {anchor}"))
        else:
            out.append(
                _bad(
                    name,
                    f"only {_gib(free)} free at {anchor} (need >= {_gib(_MIN_FREE_BYTES)})",
                    fix=f"free disk space on the volume holding {anchor}",
                )
            )
    return out


# Plain install.sh builds no launcher (KISS K11b): only --confirm-install-agent does, and it installs the
# LaunchAgents too.
_LAUNCHER_FIX = (
    "scripts/install.sh --confirm-install-agent (builds, signs and installs "
    "~/Applications/AgentSyncLauncher.app, then the LaunchAgents)"
)
_DEVELOPER_ID_FIX = (
    "SIGN_IDENTITY='Developer ID Application: <Org> (<TEAMID>)' launcher/build.sh, then "
    "scripts/install.sh --confirm-install-agent --launcher <built .app>"
)
_FP_CANARY_TIMEOUT_S = 15.0


_OPTIONAL_NOT_INSTALLED = "not installed (optional background sync; see docs/deploy)"
_OPTIONAL_NOTE = "background sync only, which is not installed; see docs/deploy"


def agents_wanted(config: Config) -> bool:
    """Whether doctor checks background sync as a requirement: a LaunchAgent plist exists, or install.sh's
    agent step is pending (KISS K11a). Otherwise the launcher, launchd, TCC canary and job-proxy checks
    report INFO only."""
    return os.environ.get(AGENT_STEP_PENDING_ENV, "").strip() == "1" or launchd.agents_installed(config)


def _optional(result: CheckResult) -> CheckResult:
    """``result`` as an INFO line with no fix: it concerns only the optional background sync (KISS K11a)."""
    if result.ok or (result.severity is Severity.INFO and result.fix is None):
        return result
    return CheckResult(result.name, False, result.detail, Severity.INFO, fix=None, note=_OPTIONAL_NOTE)


def _check_launcher(config: Config) -> list[CheckResult]:
    """The signed launcher the LaunchAgents run: present, signature valid, identifier, designated requirement
    (printed for the PPPC profile; WARN when it is an ad-hoc cdhash).  With no LaunchAgent installed
    (background sync is optional), every failed line is INFO with no fix."""
    out = _launcher_results(config)
    return out if agents_wanted(config) else [_optional(r) for r in out]


def _launcher_results(config: Config) -> list[CheckResult]:
    required = launchd.launcher_required(config)
    try:
        exe = _find_launcher()
    except ConfigError as exc:
        return [_bad("launcher", str(exc), fix=f"unset {launchd.LAUNCHER_ENV}, or {_LAUNCHER_FIX}")]
    if exe is None:
        if not agents_wanted(config):
            return [
                _bad("launcher", f"{launchd.LAUNCHER_EXECUTABLE} {_OPTIONAL_NOT_INSTALLED}", Severity.INFO)
            ]
        where = f"{launchd.LAUNCHER_EXECUTABLE} not found at {launchd.default_launcher_app()}"
        if not required:
            return [
                _ok(
                    "launcher",
                    f"{where}; not needed while no live source is under a TCC-protected folder (the "
                    "LaunchAgents run the interpreter directly)",
                )
            ]
        return [
            _bad(
                "launcher",
                f"{where}: live sources sit under TCC-protected folders, so the LaunchAgents cannot be "
                "installed without it",
                fix=_LAUNCHER_FIX,
            )
        ]
    target = launchd.launcher_app(exe) or exe
    out = [_ok("launcher", f"{target}")]
    try:
        sig = _codesign_info(target)
    except subprocess.TimeoutExpired as exc:
        out.append(_timed_out("launcher.signature", exc))
        return out
    if not sig.valid:
        out.append(
            _bad(
                "launcher.signature",
                f"codesign --verify failed: {sig.verify_detail or 'invalid signature'}",
                fix=_LAUNCHER_FIX,
            )
        )
    else:
        kind = "ad hoc" if sig.adhoc else f"TeamIdentifier {sig.team_id or '?'}"
        runtime = "hardened runtime" if sig.hardened_runtime else "NO hardened runtime"
        text = f"valid; identifier {sig.identifier}; {kind}; {runtime}"
        expected = launchd.launcher_identifier(exe)  # the bundle's own CFBundleIdentifier (BUNDLE_ID builds)
        if sig.identifier != expected:
            out.append(
                _bad(
                    "launcher.signature",
                    f"{text}; the bundle's Info.plist says {expected}",
                    Severity.WARN,
                    fix=_LAUNCHER_FIX,
                )
            )
        elif sig.adhoc:
            out.append(
                _bad(
                    "launcher.signature",
                    f"{text}: every rebuild is a new TCC subject and PPPC cannot pin it",
                    Severity.WARN,
                    fix=_DEVELOPER_ID_FIX,
                )
            )
        else:
            out.append(_ok("launcher.signature", text))
    req = sig.requirement or "<none>"
    if sig.requirement is None or req.startswith("cdhash") or sig.adhoc:
        out.append(
            _bad(
                "launcher.requirement",
                f"designated => {req} (ad hoc: a PPPC CodeRequirement cannot pin a cdhash)",
                Severity.WARN,
                fix=_DEVELOPER_ID_FIX,
            )
        )
    else:
        pinned = "certificate leaf[subject.OU]" in req
        out.append(
            _ok(
                "launcher.requirement",
                f"designated => {req}"
                + ("" if pinned else " (no certificate leaf[subject.OU]: not pinned to a TeamID)"),
            )
        )
    if os.environ.get(NO_NEXT_HINT_ENV, "").strip() == "1":
        out = [_for_it(r) for r in out]
    return out


def _for_it(result: CheckResult) -> CheckResult:
    """``result`` with the Developer ID rebuild fix (an IT/fleet signing action) worded as
    :data:`ADHOC_IT_NOTE`, with no ``fix:`` (under :data:`NO_NEXT_HINT_ENV`)."""
    if result.ok or result.fix != _DEVELOPER_ID_FIX:
        return result
    return CheckResult(result.name, result.ok, result.detail, result.severity, fix=None, note=ADHOC_IT_NOTE)


def _check_tcc_access(config: Config) -> list[CheckResult]:
    """Per TCC-protected live source: a timed, metadata-only read through the launcher, judged as the
    launcher's own responsible process (FDA or the File Provider grant).  Needs the launcher, and runs only
    when background sync is installed (KISS K11a): nothing else runs the launcher."""
    paths = launchd.canary_paths(config)
    if not paths:
        return []
    if not agents_wanted(config):
        return _tcc_canary_skipped(config)
    try:
        exe = _find_launcher()
    except ConfigError:
        exe = None
    if exe is None:
        return []  # _check_launcher already names the fix
    target = launchd.launcher_app(exe) or exe
    prompt = launchd.tcc_prompt_text(config) or "the privacy prompt naming agentsync-launcher"
    out: list[CheckResult] = []
    for src in config.live_sources():
        if src.path is None or src.kind not in (SourceKind.LOCAL, SourceKind.INBOX):
            continue
        root = expand(src.path)
        if not launchd.tcc_protected(root):
            continue
        name = f"tcc.{src.id}"
        rc, line = _launcher_canary(exe, root, _FP_CANARY_TIMEOUT_S)
        if rc == 0:
            out.append(_ok(name, f"{target} can read {root} as its own responsible process"))
        elif rc == launchd.EXIT_TCC_PENDING:
            out.append(
                _bad(
                    name,
                    f"TCC_PENDING: {root} did not answer within {_FP_CANARY_TIMEOUT_S:.0f}s; macOS is asking "
                    f"(or asked) {prompt}",
                    fix=(
                        f"click Allow on {prompt} while logged in, then run agentsync status again "
                        "(prefer this per-provider grant over Full Disk Access: see "
                        "launcher/Sources/main.swift, "
                        "trust boundary)"
                    ),
                )
            )
        elif rc == launchd.EXIT_TCC_DENIED:
            out.append(
                _bad(
                    name,
                    f"TCC_DENIED: {root}: Operation not permitted for {target}",
                    fix=(
                        "clear the earlier 'Don't Allow' with tccutil reset All "
                        f"{launchd.launcher_identifier(exe)} and approve the prompt ({_fda_target(target)} "
                        "runs only agentsync's pinned interpreter; Full Disk Access is broader than needed)"
                    ),
                )
            )
        elif rc == launchd.EXIT_CANARY_MISSING:
            out.append(
                _bad(
                    name,
                    f"{root} does not exist (sync client signed out or folder moved)",
                    fix=f"fix path in [[source]] id = {src.id!r}, or sign in to the sync client",
                )
            )
        elif rc == launchd.EXIT_DISCLAIM_UNAVAILABLE:
            out.append(
                _bad(
                    name,
                    "cannot probe as the launcher (responsibility_spawnattrs_setdisclaim unavailable)",
                    Severity.WARN,
                    fix=_fda_fix(target),
                )
            )
        else:
            out.append(
                _bad(
                    name,
                    f"launcher canary exited {rc}: {line or 'no output'}",
                    Severity.WARN,
                    fix=f"{exe} --canary-only --canary '{root}'",
                )
            )
    return out


def _job_child(args: Sequence[object]) -> list[str] | None:
    """The child argv after ``--`` when the job runs the launcher (None otherwise)."""
    if not args or not isinstance(args[0], str) or Path(args[0]).name != launchd.LAUNCHER_EXECUTABLE:
        return None
    rest = [a for a in args if isinstance(a, str)]
    if "--" not in rest:
        return None
    child = rest[rest.index("--") + 1 :]
    return child or None


def _importable(interpreter: str) -> bool:
    """True when ``interpreter -I -c 'import agentsync'`` succeeds (bounded, no network)."""
    try:
        cp = _run([interpreter, "-I", "-c", "import agentsync"], timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return cp.returncode == 0


def _check_launchd_job(spec: launchd.AgentSpec, suffix: str) -> CheckResult:
    """One LaunchAgent: installed, current, fail-closed, interpreter present, loaded."""
    name = f"launchd.{suffix}"
    path = launchd.plist_path(spec.label)
    installed = launchd._installed_plist(spec.label)  # ops-internal helper
    if installed is None:
        if path.exists():
            return _bad(name, f"{path} is not a readable plist", Severity.WARN, fix="agentsync install-agent")
        return _bad(
            name, f"{spec.label} is not installed (no {path})", Severity.WARN, fix="agentsync install-agent"
        )
    problems: list[tuple[Severity, str, str]] = []
    args = installed.get("ProgramArguments")
    program = args[0] if isinstance(args, list) and args and isinstance(args[0], str) else None
    if program is None or not os.access(program, os.X_OK):
        problems.append((Severity.ERROR, f"job program {program!r} is missing", "agentsync install-agent"))
    child = _job_child(args if isinstance(args, list) else [])
    if child is not None:
        # A launcher job execs the program after "--": a removed .venv / uv tool env means every run ends
        # SPAWN_FAILED 71 while ProgramArguments[0] (the launcher) looks fine (review deploy-ops).
        interp = child[0]
        if not os.access(interp, os.X_OK):
            problems.append(
                (
                    Severity.ERROR,
                    f"job interpreter {interp} is missing: every run exits 71 (SPAWN_FAILED)",
                    "agentsync install-agent (from the environment agentsync now runs in)",
                )
            )
        elif "agentsync" in child and not _importable(interp):
            problems.append(
                (
                    Severity.ERROR,
                    f"job interpreter {interp} cannot import agentsync: every run fails",
                    "agentsync install-agent (from the environment agentsync now runs in)",
                )
            )
        # The launcher takes its child's path as text and starts only the one it was built for, so a job
        # that names that interpreter by another path is refused on every run, with nothing else to show
        # for it (a build before `launchd.pinned_interpreter` wrote `python3` under a launcher pinned to
        # `python`).  A warn, not a FAIL: only background sync is down, and a FAIL stops install.sh.
        pin = launchd.launcher_pin(Path(program)) if program is not None else None
        if pin is not None and interp != pin:
            wanted = _job_child(list(spec.program_arguments))
            problems.append(
                (
                    Severity.WARN,
                    f"job interpreter {interp} is not the one its launcher starts ({pin}): every run exits "
                    "64 (PROGRAM_REFUSED)",
                    "agentsync install-agent" if wanted is not None and wanted[0] == pin else _LAUNCHER_FIX,
                )
            )
    expected_program = spec.program_arguments[0]
    if (
        program is not None
        and Path(expected_program).name == launchd.LAUNCHER_EXECUTABLE
        and Path(program).name != launchd.LAUNCHER_EXECUTABLE
    ):
        problems.append(
            (
                Severity.ERROR,
                f"the job runs {program} directly, not the signed launcher (TCC prompts would hang it)",
                "agentsync install-agent",
            )
        )
    if installed.get("MaterializeDatalessFiles") is not False:
        problems.append(
            (
                Severity.ERROR,
                "plist does not set MaterializeDatalessFiles=false (hydration not fail-closed)",
                "agentsync install-agent",
            )
        )
    expected = plistlib.loads(launchd.render_plist(spec))
    if dict(installed) != expected:
        diff = sorted(k for k in set(expected) | set(installed) if expected.get(k) != installed.get(k))
        problems.append(
            (
                Severity.WARN,
                f"installed plist differs from this config/interpreter ({', '.join(diff)})",
                "agentsync install-agent",
            )
        )
    loaded = _is_loaded(spec.label)
    if not loaded:
        problems.append(
            (Severity.WARN, "installed but not loaded", f"launchctl bootstrap gui/{os.getuid()} '{path}'")
        )
    if not problems:
        interval = installed.get("StartInterval")
        return _ok(name, f"{spec.label} loaded (StartInterval {interval} s)")
    rank = {Severity.ERROR: 0, Severity.WARN: 1, Severity.INFO: 2}
    problems.sort(key=lambda p: rank[p[0]])
    return _bad(
        name, f"{spec.label}: " + "; ".join(p[1] for p in problems), problems[0][0], fix=problems[0][2]
    )


def _check_launchd(config: Config) -> list[CheckResult]:
    """Both LaunchAgents (WARN when not installed or not loaded).  With no LaunchAgent plist and no pending
    agent step, background sync is optional (KISS K11a): one INFO line per job, no fix."""
    if not agents_wanted(config):
        return [
            _bad(
                f"launchd.{suffix}",
                f"{config.launchd_label_prefix}.{suffix} {_OPTIONAL_NOT_INSTALLED}",
                Severity.INFO,
            )
            for suffix in ("poll", "reconcile")
        ]
    out: list[CheckResult] = []
    builders: tuple[tuple[str, Callable[[Config], launchd.AgentSpec]], ...] = (
        ("poll", launchd.poll_spec),
        ("reconcile", launchd.reconcile_spec),
    )
    for suffix, build in builders:
        try:
            out.append(_check_launchd_job(build(config), suffix))
        except ConfigError as exc:
            out.append(_bad(f"launchd.{suffix}", str(exc), fix=_LAUNCHER_FIX))
        except Exception as exc:  # launchctl that does not answer in time is a TimeoutExpired here
            out.append(unfinished(f"launchd.{suffix}", exc, Severity.WARN))
    if os.environ.get(AGENT_STEP_PENDING_ENV, "").strip() == "1":
        out = [_agent_step_pending(r) for r in out]
    elif os.environ.get(NO_NEXT_HINT_ENV, "").strip() == "1":
        out = [_agent_yours(r) for r in out]
    return out


def _agent_step_pending(result: CheckResult) -> CheckResult:
    """``result`` with an install-agent (or bootstrap) fix replaced by :data:`AGENT_STEP_NOTE`. A FAIL keeps
    its fix: it stops install.sh before its agent step, so that step is not what clears it."""
    if result.ok or result.severity is Severity.ERROR:
        return result
    if result.fix is None or not result.fix.startswith(_AGENT_STEP_FIXES):
        return result
    return CheckResult(result.name, result.ok, result.detail, result.severity, fix=None, note=AGENT_STEP_NOTE)


def _agent_yours(result: CheckResult) -> CheckResult:
    """A launchd.* warn with an install-agent (or bootstrap) fix, worded as ``_AGENT_YOURS_NOTE`` with no
    ``fix:`` (under :data:`NO_NEXT_HINT_ENV` with no agent step pending)."""
    if result.ok or result.severity is not Severity.WARN:
        return result
    if result.fix is None or not result.fix.startswith(_AGENT_STEP_FIXES):
        return result
    return CheckResult(result.name, result.ok, result.detail, result.severity, note=_AGENT_YOURS_NOTE)


def _check_lock(config: Config) -> list[CheckResult]:
    """Who holds the single-writer lock, whether it is silent, and whether a crash left it stale."""
    path = config.state_paths.lock
    if not path.exists():
        return [_ok("lock", "no lock file yet")]
    held = SingleWriterLock.is_held(path)
    body = SingleWriterLock.read_body(path).strip()
    desc = SingleWriterLock.describe(path)
    if held:
        holder = SingleWriterLock.read_holder(path)
        age = _age_s(holder.get("last_beat_at"), _now()) if holder else None
        limit = max(2 * config.reconcile_interval_s, 3600)
        if age is not None and age > limit:
            pid = holder.get("pid") if holder else None
            return [
                _bad(
                    "lock",
                    f"held by {desc}; silent for {age}s (> {limit}s)",
                    Severity.WARN,
                    fix=f"if that process is hung: kill {pid}" if isinstance(pid, int) else None,
                )
            ]
        return [_ok("lock", f"held by {desc} (a cycle is running)")]
    if body:
        return [
            _bad(
                "lock",
                f"free, but {desc} ended without releasing it; the next cycle breaks it and runs FULL passes",
                Severity.WARN,
                fix=f"read the crash in {config.log_dir}/*.err.log",
            )
        ]
    return [_ok("lock", "free")]


def _check_heartbeat(config: Config) -> list[CheckResult]:
    """Per live source: auth state, last success within 3 x cadence, enumeration completing."""
    path = config.state_paths.heartbeat
    try:
        beats: Mapping[str, Mapping[str, object]] = read_heartbeat(path)
    except ValueError as exc:
        return [_bad("heartbeat", f"{exc}; the next completed pass rewrites it", Severity.WARN)]
    now = _now()
    out: list[CheckResult] = []
    for src in config.live_sources():
        name = f"heartbeat.{src.id}"
        entry = beats.get(src.id)
        if entry is None:
            out.append(_ok(name, "no completed pass recorded yet"))
            continue
        if entry.get("auth_state") == "REAUTH_REQUIRED":
            out.append(
                _bad(
                    name,
                    "auth: REAUTH_REQUIRED; no Graph cursor advances until you sign in again",
                    fix="agentsync login",
                )
            )
            continue
        limit = _STALE_FACTOR * max(src.cadence_s, config.poll_interval_s)
        last = entry.get("last_success_at")
        age = _age_s(last, now)
        incomplete = entry.get("consecutive_incomplete")
        if age is None:
            out.append(_bad(name, "no successful pass yet", Severity.WARN, fix="agentsync sync -v"))
        elif age > limit:
            out.append(
                _bad(
                    name,
                    f"last success {last} ({age}s ago) > {_STALE_FACTOR} x cadence ({limit}s)",
                    Severity.WARN,
                    fix="agentsync sync -v",
                )
            )
        elif isinstance(incomplete, int) and incomplete >= _INCOMPLETE_RUNS:
            detail = f"enumeration incomplete for {incomplete} consecutive passes (deletions held)"
            if src.kind is SourceKind.LOCAL and os.environ.get(NO_NEXT_HINT_ENV, "").strip() == "1":
                out.append(CheckResult(name, False, detail, Severity.WARN, note=_LISTING_YOURS_NOTE))
            else:
                out.append(_bad(name, detail, Severity.WARN, fix=_incomplete_fix(src)))
        else:
            out.append(_ok(name, f"last success {last} ({age}s ago, {entry.get('pass_kind')})"))
    return out


def _incomplete_fix(src: SourceConfig) -> str:
    """The fix for a source whose listing stays incomplete. A Graph pass resumes, and an inbox is incomplete
    while a file in it is still being written, so another sync clears those. A local folder's walk is
    always a full pass, so the same folder stops it again: the loop's WAITING ON YOU line, which status
    prints above the checks, says which case it is and what to do (it names the empty cloud folders)."""
    if src.kind is not SourceKind.LOCAL:
        return f"agentsync sync -v (a full pass that lists all of {src.id} clears this)"
    return (
        f"agentsync status (its WAITING ON YOU line about {src.id} says what stops the listing and what to "
        "do: another sync does not clear it)"
    )


def _check_logs(config: Config) -> list[CheckResult]:
    """launchd never rotates StandardOut/ErrorPath: warn before a log grows without bound."""
    log_dir = expand(config.log_dir)
    if not log_dir.is_dir():
        return [_ok("logs", f"{log_dir} does not exist yet")]
    files = sorted(p for p in log_dir.glob("*.log") if p.is_file())
    big = [(p, p.stat().st_size) for p in files if p.stat().st_size > _MAX_LOG_BYTES]
    if big:
        names = ", ".join(f"{p.name} ({s // 1024**2} MiB)" for p, s in big)
        return [
            _bad("logs", f"large logs: {names}", Severity.WARN, fix="; ".join(f": > '{p}'" for p, _ in big))
        ]
    return [_ok("logs", f"{len(files)} log file(s) in {log_dir}")]


_PERM_SAMPLE = 500  # files per tree whose mode is checked (a bounded walk, newest pages first is not needed)


def _group_other_readable(root: Path) -> list[Path]:
    """``root`` and up to _PERM_SAMPLE entries below it with any group/other permission bit set."""
    bad: list[Path] = []
    if not root.exists() or root.is_symlink():
        return bad
    if root.stat().st_mode & 0o077:
        bad.append(root)
    if not root.is_dir():
        return bad
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [*dirnames, *filenames]:
            path = Path(dirpath) / name
            try:
                mode = path.lstat().st_mode
            except OSError:
                continue
            if not stat.S_ISLNK(mode) and mode & 0o077:
                bad.append(path)
            seen += 1
            if seen >= _PERM_SAMPLE or len(bad) >= 20:
                return bad
    return bad


def _check_permissions(config: Config) -> list[CheckResult]:
    """The docs repo (worktree + .git), ~/agent-context, the cache and the logs are owner-only: they hold a
    plaintext copy of tenant data (audit critic-mirror-world-readable-tcc-downgrade).  Read-only: the fix
    names the sync that clears the paths found, or a chmod when a sync would leave one of them."""
    repo = expand(config.docs_repo)
    roots = [
        repo,
        repo / ".git" / "objects",
        repo / "mirror",
        expand(config.cache_dir),
        expand(config.log_dir),
    ]
    ctx = expand(config.config_path).parent
    if ctx != Path.home() and ctx in repo.parents:
        roots.insert(0, ctx)
    bad: list[Path] = []
    for root in roots:
        if root == ctx:
            if ctx.is_dir() and ctx.stat().st_mode & 0o077:
                bad.append(ctx)
            continue
        bad += _group_other_readable(root)
    if bad:
        shown = ", ".join(str(p) for p in bad[:4]) + (f" (+{len(bad) - 4} more)" if len(bad) > 4 else "")
        targets = " ".join(f"'{r}'" for r in roots if r.exists())
        # A sync makes agentsync's own paths owner-only, and a setup agent may run it; the chmod is the
        # person's, for a path a sync leaves (in mirror/ or .git, or another user's).
        fix = "agentsync sync (it makes these owner-only)"
        if _sync_leaves(config, bad):
            fix = f"chmod -R go-rwx {targets}; git -C '{repo}' config core.sharedRepository 0600"
        return [_bad("docs_repo.permissions", f"group/other can read tenant data: {shown}", fix=fix)]
    return [_ok("docs_repo.permissions", "docs repo, cache and logs are owner-only")]


_CHECKS: tuple[tuple[str, Callable[[Config], list[CheckResult]]], ...] = (
    ("python", _check_python),
    ("git", _check_git),
    ("pandoc", _check_pandoc),
    ("ocr", _check_ocr),
    ("media", _check_media),
    ("speech", _check_speech),
    ("config", _check_config),
    ("docs_repo", _check_docs_repo),
    ("permissions", _check_permissions),
    ("state_dir", _check_state_dir),
    ("sources", _check_sources),
    ("materialise", _check_materialise),
    ("graph", _check_graph),
    ("disk", _check_disk),
    ("launcher", _check_launcher),
    ("tcc", _check_tcc_access),
    ("launchd", _check_launchd),
    ("lock", _check_lock),
    ("heartbeat", _check_heartbeat),
    ("logs", _check_logs),
)


def _tcc_canary_skipped(config: Config) -> list[CheckResult]:
    """What the ``tcc`` group reports when the caller skips the canary: one info line, only when a canary
    would have run (a TCC-protected live source); worded apart for a Mac with no LaunchAgent installed
    (KISS K11a)."""
    if not launchd.canary_paths(config):
        return []
    if not agents_wanted(config):
        return [
            _ok("tcc.canary", "not run: it probes the launcher, which only the optional background sync runs")
        ]
    return [
        _ok(
            "tcc.canary",
            "not run: the last launcher run got past TCC (it runs again after a TCC_PENDING or TCC_DENIED "
            "launcher event, or when nothing has run since install)",
        )
    ]


def run_checks(config: Config, *, tcc_canary: bool = True) -> list[CheckResult]:
    """Run every check, in a fixed order, never raising for a single failed check.

    python >= 3.11; git absolute path; pandoc (configured or bundled) runs and reports a version; on-device
    OCR and the media helper each ready, off, not built (INFO) or failed (WARN), never built here and never a
    FAIL; docs_repo
    outside CloudStorage, a git repo (or creatable), no symlinks; state_dir exists with mode 0700 and the db
    0600; each local/inbox source root is listable (EPERM => "grant Full Disk Access to <interpreter>"),
    sentinel present, File Provider root (volume UUID readable); materialisation policy readable; graph:
    client id set, token cache backend (Keychain vs file), signed in (no network); disk free >= 2 GiB on state
    and docs volumes; launchd agents loaded (WARN if not; INFO when none is installed, KISS K11a).

    Ops extension (C15 §3): the signed launcher's presence, signature, identifier and designated requirement
    (WARN when ad hoc), and per TCC-protected source a timed metadata-only canary run *as the launcher*.
    ``tcc_canary=False`` (KISS K08a: ``status`` decides when it is due) replaces the canary with one
    ``tcc.canary`` info line.
    """
    results: list[CheckResult] = []
    for group, check in _CHECKS:
        run = _tcc_canary_skipped if group == "tcc" and not tcc_canary else check
        try:
            results.extend(run(config))
        except Exception as exc:
            results.append(unfinished(group, exc))
    return results


_TAGS = {Severity.ERROR: "FAIL", Severity.WARN: "warn", Severity.INFO: "info"}


def format_results(results: list[CheckResult]) -> str:
    """Render results as aligned text lines ``[ok|FAIL|warn] name — detail (fix: ...)`` (or ``(<note>)``)."""
    if not results:
        return ""
    width = max(len(r.name) for r in results)
    lines: list[str] = []
    for r in results:
        tag = "ok" if r.ok else _TAGS[r.severity]
        line = f"[{tag:<4}] {r.name:<{width}} — {r.detail}"
        if not r.ok and r.fix:
            line += f" (fix: {r.fix})"
        elif not r.ok and r.note:
            line += f" ({r.note})"
        lines.append(line)
    return "\n".join(lines)
