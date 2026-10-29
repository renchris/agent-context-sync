"""Preflight checks with a concrete fix per failure, run by ``agentsync status`` (owner: ops).

Every probe that touches the machine or another module is a private module-level function (``_git_path``,
``_run``, ``_volume_uuid``, ``_auth_status``, ...) so tests replace it with a fake; ``run_checks`` never
raises for a single check: a probe that crashes becomes a failed :class:`CheckResult` naming the exception
(:func:`unfinished`), and a program that does not answer in time becomes one that says the check ran out of
time. Every FAIL names a fix: scripts/install.sh tells its reader so.
"""

from __future__ import annotations

import contextlib
import ctypes
import enum
import errno
import functools
import json
import logging
import os
import plistlib
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

from agentsync import arm_local, gitops, lints, materialise
from agentsync.arm_local import LISTING_TIMEOUT_S as _ARM_LISTING_TIMEOUT_S
from agentsync.arm_local import CallTimedOutError, call_with_timeout, cloud_provider_root
from agentsync.config import RETIRE_ADVICE, Config, SourceConfig, canonical_source_root
from agentsync.convert import media, ocr, speech
from agentsync.cycle import (
    VANISHED_FIX,
    _holds_home,
    _sync_leaves,
    unowned_mirror_pages,
    vanished_add_back_tail,
    vanished_add_backs,
    vanished_detail,
    vanished_sources,
)
from agentsync.errors import AgentSyncError, ConfigError, ConversionError, ManifestOpenError
from agentsync.graph import auth as graph_auth
from agentsync.it_request import org_of
from agentsync.loop import BIN, INSTALL_SH, NO_NEXT_HINT_ENV, empty_cloud_folders_held
from agentsync.manifest import (
    LOST_MANIFEST,
    LOST_MANIFEST_EMPTY_UNREAD,
    LOST_MANIFEST_SHORT,
    LOST_MANIFEST_UNREAD,
    Manifest,
    ManifestBusyError,
    ManifestNotCurrentError,
    lost_words,
    manifest_lost,
    read_note,
)
from agentsync.model import CycleMode, SourceKind
from agentsync.ops import launchd
from agentsync.ops.lock import (
    STARTED_BY_LAUNCHD,
    STARTED_BY_OTHER,
    LockInfo,
    SingleWriterLock,
    read_heartbeat,
    read_regular_text,
)
from agentsync.paths import StatePaths, default_cache_dir, default_log_dir, expand, is_cloud_path
from agentsync.skill import AGENTSYNC_BIN, HELPER_RETRY_CLAUSE
from agentsync.text import plural

if TYPE_CHECKING:
    from agentsync.governance import GovernanceUnreadError

log = logging.getLogger(__name__)

_MIN_PYTHON = (3, 11)
_MIN_FREE_BYTES = 2 * 1024**3
_MAX_LOG_BYTES = 64 * 1024**2
_STALE_FACTOR = 3  # design 4.7 watcher rule: cursor/success age > 3 x cadence
# The heartbeat's consecutive_incomplete before we warn: passes not counted as listed. A Graph delta pass
# resets it while its own enumeration_complete stays false (B13 round 6 G53; round 7 H51).
_INCOMPLETE_RUNS = 3
_POLICY_NAMES = {0: "default", 1: "off", 2: "on"}
AGENT_STEP_PENDING_ENV = "AGENTSYNC_AGENT_STEP_PENDING"
"""Set to 1 by scripts/install.sh --confirm-install-agent for its doctor step: the LaunchAgents are installed
by its later agent step, so a launchd.* fix that is install-agent reads :data:`AGENT_STEP_NOTE` instead."""
AGENT_STEP_NOTE = "installed by the agent step below"
# A runnable fix names the binary by its full path (BIN), as the loop's waits do: ~/.local/bin is not on PATH
# by design (install.sh installs uv with UV_NO_MODIFY_PATH=1), so a bare `agentsync` is "command not found".
_AGENT_STEP_FIXES = (f"{BIN} install-agent", "launchctl bootstrap ")
# NO_NEXT_HINT_ENV (agentsync.loop, its one definition) is 1 under scripts/install.sh: its NEXT: line is
# the only instruction in its output, so the ad hoc launcher's Developer ID fix (an IT action, nothing the
# person or their agent does) reads ADHOC_IT_NOTE, with no ``fix:``.
ADHOC_IT_NOTE = "for IT: Developer ID build (docs/deploy/mdm)"
_XCODE_SELECT = "/usr/bin/xcode-select"
_DEVTOOLS_TIMEOUT_S = 5.0
# Only scripts/install.sh builds the OCR, media and speech helpers (convert/ocr.py, convert/media.py,
# convert/speech.py), so the ocr, media and speech texts name it and no agentsync command.  Without developer
# tools it builds nothing, so there the not-built line says what it waits for.  Every text names the
# installer by INSTALL_SH, the path the setup prompt clones it to: the prompt runs nothing from inside the
# checkout, so a bare scripts/install.sh is not found from where the agent or the person runs it (B13 round 17
# U30).
_OCR_NOT_BUILT = f"{INSTALL_SH} builds it"
_OCR_NOT_BUILT_NO_DEVTOOLS = (
    f"{INSTALL_SH} builds it once the Command Line Tools are installed (xcode-select --install)"
)
_OCR_DEVTOOLS_FIX = f"xcode-select --install, then run {INSTALL_SH} again"

# Also under NO_NEXT_HINT_ENV, when install.sh was not asked for the agent step: a launchd.* warn whose fix is
# install-agent or a bootstrap (LaunchAgents left by an earlier install) is the person's to refresh, so it
# reads this note, with no ``fix:``. A FAIL keeps its fix.
_AGENT_YOURS_NOTE = "background sync is yours to refresh, not a setup step"
# Also under NO_NEXT_HINT_ENV: a local folder's listing that stays incomplete is the person's to settle (an
# empty cloud folder, a folder without access), and install.sh prints the loop's line for it; one that no
# wait names (its stopping folders excluded since) reads the note too, since install's sync is the walk that
# lists it (B13 round 8 J12), and so does an inbox no wait names on status's path, since an inbox pass is
# always full and install's step-6 sync is that full pass (B13 round 9 K75). That status runs
# before install.sh's sync and reads the last walk's stored state, which the sync can clear (a file added, a
# folder no longer a File Provider domain): the note promises no line, it says where the answer is, true
# whether the wait stays, goes, or a FAIL skips the sync (B13 round 4 Q26, Q32). The answer can be a WAITING
# ON YOU line, the NEXT line (a listing held for a privacy prompt), install.sh's line that the sync cleared
# it, or nothing when a FAIL skips the sync, so it names no line type; no reader infers "cleared" from a
# missing line (B13 round 6 G33). No parentheses: the report's fix parser strips one level. "as of", not "held
# at", since the detail before it says "deletions held" (B13 round 13 O36: one word in two senses). It
# names that sync as one that may not run (B13 round 14 Q35: "the end of this run says whether this run's
# sync cleared it" stood in runs whose sync a FAIL or a busy lock skipped, and whose end said nothing). It
# names the condition install.sh's answer follows, a sync that exits 0 (``cleared_listings``, through
# ``loop_next``): a first sync that exits 78, 80 or another code ends the run with ``exit 1`` before that, and
# "if this run's sync runs" promised an answer those runs never gave (B13 round 15 S11). A run that ends
# before its closing status after its sync ran (those exits, or a failed --confirm-install-agent wait) says
# so per held listing instead, and names status (install.sh's ``unanswered_listings``, round 15 S64).
_LISTING_YOURS_NOTE = (
    "as of its last walk: if this run's sync succeeds, the end of this run says whether it cleared it"
)
# The note when status itself has a [FAIL] that stops install.sh's sync (any but the launcher's TCC_PENDING,
# install.sh's DOCTOR_BLOCKS rule): status swaps it in, since only it knows every check's result (B13 round 14
# Q10). No line type promised, no parentheses (the report's fix parser strips one level), and no claim of what
# releases the listing: "it stays held until a sync after that fix" tied it to a [FAIL] about another folder
# or pandoc, while an empty cloud folder held it, and said "held" a second time beside "deletions held"
# (B13 round 15 S28; round 13 O36). Status run on its own names what keeps it incomplete, by the full path.
_LISTING_SKIPPED_NOTE = (
    "as of its last walk: a [FAIL] line stops this run's sync, so this run does not say what keeps it "
    f"incomplete; {BIN} status names it"
)
# The same note in the setup report, whose run prints no WAITING ON YOU line at its end (``--report-only``
# prints none at all): the waits are on its Summary's Loop line. It promises no line, since the report's
# doctor calls a local listing yours by the source's kind, not by the loop's waits (B13 round 5 F12, F45).
LISTING_YOURS_REPORT_NOTE = (
    "as of its last walk: the Summary's Loop line says whether a WAITING ON YOU line still names it"
)
# The note on an incomplete listing when the state file cannot be read (a damaged manifest): no sync lists it
# again until it can, so the line points at no WAITING ON YOU line and gives no sync as its fix (B13 round 19
# W11: the report's note sent the reader to its Loop line, and the fix by hand to WAITING ON YOU lines, while
# the Loop line held only the state_dir.manifest FAIL's NEXT). It makes no claim about the loop's own read:
# damage only a sync reached FAILs state_dir.manifest from that sync's record while the loop reads the file
# (B13 round 20 X14, X46: "the loop cannot read the state file" beside a loop line read from it). A note, not
# a fix, with no parentheses, as the notes above.
LISTING_STATE_UNREADABLE_NOTE = (
    "as of its last walk: the state file cannot be read, so no sync lists it again until it can; the "
    "state_dir.manifest [FAIL] line says what to do first"
)
# The same note beside a manifest lost from a Mac that has synced (``manifest.manifest_lost``): the file is
# missing, not unreadable, and what lists the source again is the person's restore or their move of
# heartbeat.json aside (LOST_MANIFEST_FIX), never the file becoming readable (B13 round 24 B36: the note said
# "the state file cannot be read ... until it can"). No parentheses, as the notes above.
LISTING_STATE_LOST_NOTE = (
    "as of its last walk: the state file is missing, so no sync lists it again until the person restores it "
    "or confirms a new start; the state_dir.manifest [FAIL] line says what to do first"
)
# The same note beside a manifest lost because it is there but too short (manifest.LOST_MANIFEST_SHORT): never
# "is missing", and its fix is MANIFEST_FIX's, which moves heartbeat.json aside with it (B13 round 29 C128).
LISTING_STATE_SHORT_NOTE = (
    "as of its last walk: the state file is empty or too short to be a manifest, so no sync lists it again "
    "until the person restores it or moves it aside with heartbeat.json; the state_dir.manifest [FAIL] line "
    "says what to do first"
)
# The same note beside agentsync's governance folder that is not a folder (governance.governance_dir_error):
# every sync refuses over it, so no WAITING ON YOU line and no sync is its fix (B13 round 29 C130).
LISTING_STATE_GOVDIR_NOTE = (
    "as of its last walk: agentsync's governance folder is not a folder, so no sync lists it again until the "
    "person fixes it; the governance.hold [FAIL] line says what to do first"
)
# The same note for a state file this account cannot open (its mode, its owner): the state_dir lines, not
# state_dir.manifest, say why (B13 round 20 X15: W11 keyed only a damaged file, so the warn kept pointing at
# WAITING ON YOU lines a status that cannot read the state never prints).
LISTING_STATE_SHUT_NOTE = (
    "as of its last walk: this account cannot open the state file, so no sync lists it again until it can; "
    "the state_dir [FAIL] lines say what to do first"
)

# The same note for something other than a file where the state file belongs (a folder): not "cannot be
# read", beside a state_dir.manifest line that says "is a folder, not the state file" (B13 round 25 D23, lane
# RA's item, lane B's file; the report's words for its warn say the same).
LISTING_STATE_NOT_FILE_NOTE = (
    "as of its last walk: the state file is not a file, so no sync lists it again until the person fixes it; "
    "the state_dir.manifest [FAIL] line says what to do first"
)
# The same note for a side file of the state file (its -wal or -shm) that is not a file: no account's access,
# so not the shut note's "cannot open", but the person's move aside that state_dir.files names (B13 round 25
# D8: a folder at the -wal read "this account cannot open the state file").
LISTING_STATE_SIDE_TAIL = "the state_dir.files [FAIL] line says what to do first"
"""The last words of a one-side :func:`listing_state_side_note` (and of :data:`LISTING_STATE_SIDE_NOTE`), for
a reader that keys on the note; :data:`LISTING_STATE_SIDES_TAIL` ends a two-side one. Both start with ``the
state_dir.files [FAIL] line``."""

LISTING_STATE_SIDES_TAIL = "the state_dir.files [FAIL] lines say what to do first"
"""The last words of a :func:`listing_state_side_note` naming both sides, which sits under one state_dir.files
FAIL per side (B13 round 30 C322: "line says" above two such lines)."""

LISTING_STATE_SIDE_NOTE = (
    "as of its last walk: a side file of the state file is not a file, so no sync lists it again until the "
    f"person moves it aside; {LISTING_STATE_SIDE_TAIL}"
)
""":func:`listing_state_side_note` when no side is named."""


def sides_not_files_words(sides: Sequence[Path]) -> tuple[str, int]:
    """The words of the -wal and -shm of ``sides`` that are not files, each by the role its state_dir.files
    FAIL gives it (:data:`SIDE_ROLES`), the write-ahead log first, and how many: ``the state file's
    write-ahead log is not a file`` for one, ``... write-ahead log and shared-memory file are not files`` for
    both; ("", 0) for none. One place for the two-side words, which the listing note, status's cannot-read
    line and the setup report read (B13 round 29 C141)."""
    suffixes = [x for x in SIDE_ROLES if any(side_suffix(side) == x for side in sides)]
    if not suffixes:
        return "", 0
    if len(suffixes) == 1:
        return f"{SIDE_ROLES[suffixes[0]]} is not a file", 1
    return "the state file's write-ahead log and shared-memory file are not files", len(suffixes)


def listing_state_side_note(sides: Sequence[Path]) -> str:
    """The listing note beside a -wal or -shm that is not a file, naming each by the role its state_dir.files
    FAIL gives it (:data:`SIDE_ROLES`), the write-ahead log first: "a side file of the state file" said none
    while the FAIL lines beside it named them (B13 round 27 P16, P146). :data:`LISTING_STATE_SIDE_NOTE`
    with none named."""
    what, n = sides_not_files_words(sides)
    if not n:
        return LISTING_STATE_SIDE_NOTE
    them = "it" if n == 1 else "them"
    return (
        f"as of its last walk: {what}, so no sync lists it again until the person moves {them} aside; "
        f"{LISTING_STATE_SIDE_TAIL if n == 1 else LISTING_STATE_SIDES_TAIL}"
    )


def listing_state_governance_note(err: GovernanceUnreadError, check: str) -> str:
    """The listing note beside a governance file the sync's guard stops on (``state_not_a_file`` with
    ``damaged`` and ``lost``: the hold list, the purge queue, the suppression list, the audit trail, the hold
    record or the purge record), named by ``err``'s own role, never a WAITING ON YOU line: no sync runs until
    the person fixes it, and an empty-folder exclude, which that line asks for, clears nothing (B13 round 30
    C321). ``check`` is the FAIL that says what to do (``governance_check``). Each clause ends ``; ``, so the
    audit trail's reads as the report's fixed phrase. Never for a hold the hold list lost: the sync runs."""
    from agentsync import governance  # noqa: PLC0415 - governance imports this module lazily

    if isinstance(err, governance.GovernanceNotAFileError):
        said = "is not a file"
    elif isinstance(err, governance.GovernanceLostError):
        said = "has lost a purge the audit trail records"
    else:
        said = "cannot be read"
    return (
        f"as of its last walk: {err.role} {said}; no sync lists it again until the person fixes it; the "
        f"{check} [FAIL] line says what to do first"
    )


def state_unopenable(exc: BaseException | None, state_root: Path | None = None) -> bool:
    """Whether ``exc``, a read of the state, failed because this account cannot open the file (its mode, its
    owner, a read-only volume): a :class:`ManifestOpenError`, sqlite's CANTOPEN, PERM or READONLY, or an
    ``OSError`` with EACCES, EPERM or EROFS on a path in ``state_root``, the state folder (a docs repo the
    loop cannot search is no state file's; B13 round 20 X15's reviewer). Keyed on the read's own error, never
    on a check's name: state_dir.files also FAILs a file readable by others, which the loop reads normally."""
    if isinstance(exc, sqlite3.Error):
        # a side file that is not a file is no account's access: the person moves it aside (B13 round 25 D8)
        side = state_root is not None and side_not_a_file_at(Path(state_root) / "manifest.sqlite") is not None
        return cannot_open(exc) and not side
    if isinstance(exc, ManifestOpenError):
        return True
    if not isinstance(exc, OSError) or exc.errno not in (errno.EACCES, errno.EPERM, errno.EROFS):
        return False
    if state_root is None or not isinstance(exc.filename, str | bytes | os.PathLike):
        return False
    path = Path(os.path.realpath(os.fsdecode(exc.filename)))
    folder = Path(os.path.realpath(state_root))
    return path == folder or path.is_relative_to(folder)


LISTING_INCOMPLETE = "enumeration incomplete for "
"""How the heartbeat warn of a listing that stays incomplete starts its detail."""

# The bundled pandoc is an Intel (x86_64) program, in the Apple silicon wheel of pypandoc_binary too, so
# macOS runs it through Rosetta, and Rosetta translates a file it has not seen once, at its first start. A
# native arm64 copy of the same version, when the operator installed one, is what the converters and this
# check run instead, and it needs no Rosetta (convert.pandoc.resolved_pandoc, B13 round 14 Q60).
# `uv tool install --force` writes a new copy at every update. Measured 2026-10-07 on a 119 MB pandoc 3.9:
# 10 to 67 s for the first `--version` of a fresh copy (the longer times on a loaded Mac), under 1 s after on
# that Mac; but a later start took 110 s at a load of about 600 (B13 round 15 S17), so a busy Mac slows any
# start. scripts/install.sh therefore starts it once before it runs status (its "pandoc: ..." lines), outside
# this limit; a status run by hand right after an update, or on a busy Mac, can still meet a slow one. The
# reason is install.sh's ticker's, word for word (B13 round 16 T17, T77). A native or configured copy needs
# no Rosetta, so its first start is not slow; the busy Mac's half alone is its reason (B13 round 17 U104).
_PANDOC_TIMEOUT_S = 60.0
_PANDOC_SLOW_START = "a new copy's first start can take a minute, and any start can on a busy Mac"
_PANDOC_BUSY_START = "any start can take a minute on a busy Mac"
# The first words of the native copy's fix, which PERSONS_FIX_RE reads as the person's (B13 round 15 S47).
_NATIVE_COPY_FIX = "the native pandoc copy at"
# A Python older than 3.11 is a tool environment install.sh did not make (it asks uv for 3.11); its step 2
# makes a new one (B13 round 15 S29: `uv sync` syncs only the folder it runs in).
_PYTHON_FIX = f"run {INSTALL_SH} again (it installs agentsync with uv's Python 3.11)"
# The bundled pandoc's: install.sh's step 2 reinstalls when that pandoc is not the file pypandoc_binary's
# RECORD lists (its install_is_current), under the sync locks and the TLS setting, at uv.lock's versions; the
# bare `uv tool upgrade` it replaces had none of those and moved every dependency (B13 round 16 T54, T59, T60,
# T65). Its fix starts with that re-run, the agent's step, and names the [convert] edit, the person's, only as
# what to show them once the re-run has not cleared the line: a fix's first words say whose it is, here, in
# install.sh's persons_fails and in the setup prompt (B13 round 17 U13, U47, U79, U80).
_BUNDLED_PANDOC_FIX = (
    f"run {INSTALL_SH} again (it reinstalls agentsync when the bundled pandoc is missing or not the "
    "file pypandoc_binary installed)"
)
_PANDOC_PATH_THEIRS = "setting [convert] pandoc_path to an absolute pandoc is theirs"
_EBADARCH = getattr(errno, "EBADARCH", 86)  # "Bad CPU type in executable": an Intel program and no Rosetta

# A check that ran out of time has no answer: a slow program and one that never answers read the same from
# here, so the line does not say which it was.  _STILL_NO_ANSWER is what the fix names for a second such
# line when the check has no fix of its own for a program that does not work.
_OUT_OF_TIME = "the check ran out of time, so it could not say whether anything is wrong"
_STILL_NO_ANSWER = "report it: the program does not answer on this Mac, and no setup step clears that"

# A check that crashed is a fault in agentsync, which nothing on this Mac clears. -v logs the traceback.
# An operating-system error is not that: the Mac's state causes it and can clear it (see _os_error).
_CRASH_FIX = (
    f"run {BIN} status -v, which prints the traceback: a check that crashes is a fault in agentsync to "
    "report, and no setup step clears it"
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
    """The git the sync will use (gitops' resolver: on macOS the git /usr/bin/git's xcrun shim runs, so the
    doctor line names the Xcode or Command Line Tools path; for a stub resolver /usr/bin/git, else launchd
    PATH)."""
    try:
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


def _pandoc_route(config: Config) -> tuple[Path, str]:
    """The pandoc the converters will run and which one it is (``configured``, ``native`` or ``wheel``):
    [convert] pandoc_path as written, which this check's own not-absolute and not-executable lines judge,
    else ``convert.pandoc.resolved_pandoc``'s answer, the operator's native arm64 copy when there is one
    (B13 round 14 Q60).  A wheel binary that is missing is still named, for the check's missing line."""
    if config.convert.pandoc_path is not None:
        return config.convert.pandoc_path, "configured"
    from agentsync.convert import pandoc  # noqa: PLC0415 - lazy: optional at doctor import time

    try:
        return pandoc.resolved_pandoc(None)
    except ConversionError:  # the wheel's binary is not there: "missing or not executable"
        return pandoc._wheel_pandoc(), "wheel"


def _ocr_status(config: Config) -> tuple[str, str]:
    """(state, detail) of the on-device OCR helper, from ``convert.ocr.probe``: it looks and never compiles;
    the one program it may start is a built helper's ``--version``, which it gives 5 s."""
    return ocr.probe(config.convert, config.cache_dir)


def _media_status(config: Config) -> tuple[str, str]:
    """(state, detail) of the media helper, from ``convert.media.probe``: as :func:`_ocr_status`, it looks and
    never compiles; the one program it may start is a built helper's ``--version``, which it gives 5 s."""
    return media.probe(config.convert, config.cache_dir)


def _speech_status(config: Config) -> tuple[str, str]:
    """(state, detail) of the speech engine, from ``convert.speech.probe``: it looks, never compiles and
    takes no model digest; the one program it may start is a built helper's ``--version``, given 5 s."""
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
    return materialise.get_materialize_policy()


def _volume_uuid(path: Path) -> str:
    """Volume UUID of ``path`` (arm_local.volume_uuid)."""
    return arm_local.volume_uuid(path)


def _auth_status(config: Config) -> _AuthProbe:
    """Cached sign-in state and token-cache backend (graph.auth; no network)."""
    provider = graph_auth.MsalAuth(graph_auth.settings_from_config(config))
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
    return launchd.in_our_job(config.launchd_label_prefix)


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
    """``path`` or its closest ancestor this account can see. A folder above it that refuses a search makes
    ``exists()`` raise PermissionError on Python 3.11, which ended the disk check in a generic "report it"
    FAIL that took status's NEXT from state_dir's, the person's; the walk steps past it to the folder that
    refuses, whose ``statvfs`` needs a search only of the folders above it (B13 round 22 Z73, Z3, Z34). Read
    through a link first, where it can be, so a state folder that links to another volume is measured there
    (lane B's reviewer: the walk stopped at the link's own folder, on the wrong volume)."""
    p = Path(os.path.realpath(expand(path)))
    while p != p.parent:
        try:
            if p.exists():
                break
        except OSError:
            pass
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


# Special inside double quotes (``!`` too: history expansion in an interactive bash, zsh or tcsh).
_DOUBLE_QUOTE_SPECIAL = frozenset('!$`\\"')


def _shell_path(path: Path | str) -> str:
    """``path`` as one shell word for a fix: ``~/'<rest>'`` below the home folder (as HOME gives it, or
    resolved), ``~`` for the home folder itself, ``'<path>'`` anywhere else. Both setup reports redact the
    home folder to ``~``, and inside quotes (``'~/...'``) no shell expands it (CONTRACTS §16.39 N11). The
    quoted part is the raw path, so the report's redaction still finds every name in it. A path with a
    ``'`` is quoted by what it holds (§16.45 R78): ``~/"<rest>"`` (or ``"<path>"``) when it has none of
    ``!$`\\"``, still the raw path in one piece; else each ``'`` is spliced as ``'\\''``, which splits a name
    holding one for the redaction."""
    p = str(path)
    home = Path.home()
    forms = {str(home)}
    with contextlib.suppress(OSError, RuntimeError):  # a home folder that does not resolve (a symlink loop)
        forms.add(str(home.resolve()))
    for form in sorted(forms, key=len, reverse=True):
        h = form.rstrip("/")
        if not h:  # HOME=/ : every path would be "below" it
            continue
        if p == h:
            return "~"
        if p.startswith(h + "/"):
            return "~/" + _quoted(p[len(h) + 1 :])
    return _quoted(p)


def _quoted(text: str) -> str:
    """``text`` as one quoted shell word, by what it holds (:func:`_shell_path`)."""
    if "'" not in text:
        return f"'{text}'"
    if not _DOUBLE_QUOTE_SPECIAL.intersection(text):
        return f'"{text}"'
    return "'" + text.replace("'", "'\\''") + "'"


AGAIN_UNDER_INSTALL_SH = (
    f"run {INSTALL_SH} again without --report-only, since a --report-only run builds nothing; a plain run "
    "keeps the folders already synced"
)
"""How a fix under scripts/install.sh (:data:`NO_NEXT_HINT_ENV`) says to run it again: by what the command is,
--report-only with its own subject, so "builds nothing" never reads as the re-run's (B13 round 27 P109), and
no bracket inside the line's ``(fix: ...)``. It said ``run the same <install.sh> command again as it is (its
NEXT line names it)``, but a warn makes no NEXT line name install.sh, and the same command under
``--report-only``, which sets the variable too and writes this fix into the report, builds nothing (B13 round
19 W19, W48). ``--list-folders`` runs no doctor. No options are repeated: "the options of the run that
installed agentsync" named options the person never saw, and a plain run does the same job, since a run with
no --source-local keeps the config's folders (install.sh's header; its own re-run drops them too) and
background sync is the operator's flag, which a setup agent never passes (B13 round 20 X21). No bracket of its
own: every doctor line wraps its fix in ``(fix: ...)``, so ``(it keeps ...)`` and :data:`_STILL_NO_ANSWER`'s
``(the program ...)`` nested a bracket in that one, which round 20's X7 and X44 had taken out of such lines
(B13 round 21 Y20)."""


def _again_fix(fallback: str | None = None) -> str:
    """The fix for a check that ran out of time: run again as it is, and what to do when the same line
    comes back.  Under scripts/install.sh (:data:`NO_NEXT_HINT_ENV`) the run is install.sh's, named by
    :data:`AGAIN_UNDER_INSTALL_SH`; else ``agentsync status``.  A program that was only slow
    answers the next time.  One that never answers gives the same line at every run, so the fix does not
    end at "again": it names ``fallback`` (the check's own fix), else says to report the line."""
    if os.environ.get(NO_NEXT_HINT_ENV, "").strip() == "1":
        again = AGAIN_UNDER_INSTALL_SH
    else:
        again = f"run {BIN} status again as it is"
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
    that stays. No bracket of its own: the line wraps the fix in ``(fix: ...)`` (B13 round 22 Z7)."""
    reason = exc.strerror or str(exc) or type(exc).__name__
    stays = f"if the line stays, report it: {BIN} status -v prints where the check stopped"
    if isinstance(exc.filename, str | bytes | os.PathLike):
        path = os.fsdecode(exc.filename)
        fix = f"check that {path} is there and can be opened, then run again; {stays}"
        return _bad(name, f"could not read {path}: {reason}", severity, fix=fix)
    detail = f"stopped on a system error: {type(exc).__name__}: {reason}"
    return _bad(name, detail, severity, fix=f"run again; {stays}")


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
    return [_bad("python", f"{text}; agentsync needs >= 3.11", fix=_PYTHON_FIX)]


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
    first = detail[0] if detail and cp.returncode >= 0 else _exit_words(cp.returncode, cp.stderr)
    return [_bad("git", f"{git} --version failed: {first}", fix="xcode-select --install")]


def _exit_words(returncode: int, stderr: str = "") -> str:
    """``exit 2``; for a program a signal stopped (subprocess's negative code), the converters' phrase
    ``stopped by SIGKILL (signal 9)``, with the line its stderr says why only for a crash (``ocr._ended``;
    B13 round 3 R81)."""
    if returncode >= 0:
        return f"exit {returncode}"
    return ocr._ended(returncode, stderr.encode())


def _check_pandoc(config: Config) -> list[CheckResult]:
    """pandoc (configured or bundled) runs and reports a version.  One that does not answer within
    ``_PANDOC_TIMEOUT_S`` is most often starting for the first time (see that constant): the line says so
    and its fix is to run again, then this check's own fix if the line comes back (a pandoc that never
    answers reads the same, and running again does not clear that one).  One macOS will not start is a
    FAIL with this check's fix, and says when the reason is an Intel program on a Mac without Rosetta.

    The fix is by which pandoc it is (B13 round 15 S29, S47): a configured one is that path's, and the
    bundled one's is a re-run of scripts/install.sh, whose step 2 reinstalls the tool environment it made
    when that pandoc is not the file pypandoc_binary installed, with its sync locks, TLS setting and uv.lock's
    versions (B13 round 16 T54, T59, T60, T65; `uv sync` syncs only the folder it runs in). The operator's
    native copy is the person's to replace or remove (they installed it), so status and
    install.sh name it as theirs (``PERSONS_FIX_RE``); after a time-out the agent's re-run comes first.

    The bundled one's fix is the agent's re-run first, then the [convert] edit as the person's, shown to them
    if the line comes back (B13 round 17 U13, U47, U80). A configured path and a not-absolute one are a
    [convert] edit only, so their fixes are the person's (``PERSONS_FIX_RE``). After a time-out under
    install.sh, whose step 2 has already found the bundled file whole or reinstalled it, a second re-run
    repeats the first, so the line that comes back names the person's edit (U78)."""
    own = "set [convert] pandoc_path to an absolute pandoc"
    comes_back = f"if this line comes back after that, show the person this line: {_PANDOC_PATH_THEIRS}"
    fix = f"{_BUNDLED_PANDOC_FIX}; {comes_back}"
    try:
        pandoc, kind = _pandoc_route(config)
    except ImportError as exc:
        return [_bad("pandoc", f"pypandoc_binary is not importable: {exc}", fix=fix)]
    # The OK line says which copy ran; a FAIL line says so for the native copy, the one its fix is about.
    which = {"native": " (native arm64 copy)", "wheel": " (bundled with pypandoc_binary)"}.get(kind, "")
    failed = which if kind == "native" else ""
    again = fix  # what a time-out's fix names once the re-run gives the same line
    if kind == "wheel" and os.environ.get(NO_NEXT_HINT_ENV, "").strip() == "1":
        again = (
            f"show the person this line: {_PANDOC_PATH_THEIRS} (the bundled pandoc does not answer on this "
            "Mac, and install.sh found its file whole)"
        )
    if kind == "configured":  # the operator's path: the wheel's reinstall does not touch it
        fix = again = (
            f"check or replace the pandoc at [convert] pandoc_path ({_shell_path(pandoc)}), or remove that "
            "line to use the bundled pandoc or its native arm64 copy"
        )
    elif kind == "native":  # the operator's copy: uv does not touch it, and Rosetta is not what it needs
        copy = _shell_path(pandoc.parent.parent)
        runs = "the converters run it instead of the bundled pandoc"
        fix = f"{_NATIVE_COPY_FIX} {copy} is yours to replace or remove ({runs}), or {own}"
        again = f"show the person this line: {_NATIVE_COPY_FIX} {copy} is theirs to replace or remove"
        again += f" ({runs})"
    if not pandoc.is_absolute():
        return [
            _bad("pandoc", f"pandoc path {pandoc} is not absolute (launchd has no Homebrew PATH)", fix=own)
        ]
    if not os.access(pandoc, os.X_OK):
        return [_bad("pandoc", f"{pandoc} is missing or not executable", fix=fix)]
    try:
        cp = _run([str(pandoc), "--version"], timeout=_PANDOC_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        # A new copy's slow first start is Rosetta's translation of the bundled Intel binary, which no other
        # pandoc has; a busy Mac slows any start, of any copy (B13 round 15 S17, round 17 U104)
        why = _PANDOC_SLOW_START if kind == "wheel" else _PANDOC_BUSY_START
        return [_timed_out("pandoc", exc, what=f"{pandoc}{failed} --version", why=why, fallback=again)]
    except OSError as exc:
        detail = f"{pandoc}{failed} could not be started: {exc.strerror or exc}"
        if exc.errno == _EBADARCH and kind != "native":
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
        return [_ok("pandoc", f"{lines[0]} at {pandoc}{which}")]
    if cp.returncode < 0:  # a signal: the git line's colon form, no bracket inside one (B13 round 32 C661)
        said = f": {_exit_words(cp.returncode, cp.stderr)}"
    else:
        said = f" (exit {cp.returncode}): {cp.stderr.strip()[:200]}"
    return [_bad("pandoc", f"{pandoc}{failed} --version failed{said}", fix=fix)]


def _check_ocr(config: Config) -> list[CheckResult]:
    """On-device OCR, which is optional: ok when the helper is ready or OCR is switched off; a not-ok INFO
    line when the helper is not built (scripts/install.sh builds it; this check never compiles); WARN with
    the reason when the last build failed, or the helper is there and may not be run or does not answer,
    with a fix only when the developer tools are missing, the build ran out of time or a signal stopped it
    (:func:`_helper_again_fix`; B13 round 19 W49, round 25 D85).  Never a FAIL: a probe that crashes is a
    WARN too.

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
    fix = _OCR_DEVTOOLS_FIX if _devtools_missing() else _helper_again_fix(detail, "ocr")
    detail = _helper_detail(detail, fix)
    return [_bad("ocr", f"on-device OCR is not working: {detail}", Severity.WARN, fix=fix)]


def _check_media(config: Config) -> list[CheckResult]:
    """The media helper that reads recordings, optional as OCR is and checked the same way: ok when ready or
    switched off (``[convert] recordings = false`` among the switches); a not-ok INFO line when it is not
    built (scripts/install.sh builds it; this check never compiles); WARN with the reason when it failed,
    with a fix only when the developer tools are missing, the build ran out of time (W49) or a signal
    stopped it (B13 round 25 D85).  Never a FAIL."""
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
    fix = _OCR_DEVTOOLS_FIX if _devtools_missing() else _helper_again_fix(detail, "media")
    detail = _helper_detail(detail, fix)
    return [_bad("media", f"media helper: not working ({detail})", Severity.WARN, fix=fix)]


def _check_speech(config: Config) -> list[CheckResult]:
    """The speech engine (words and voices of a recording), optional as the media helper is and checked the
    same way, one ``speech: <state> (<detail>)`` line: ok when ready or off (whatever switches the media
    helper off, or a model folder the operator has not placed); a not-ok INFO line when the helper is not
    built; WARN with the reason when it failed, a helper built from a FluidAudio commit other than the
    pinned one among them, with a fix only for missing developer tools, a build that ran out of time or a
    signal stopped, a FluidAudio fetch that failed, or a fetch that needs a newer Swift than this Mac's (a
    Command Line Tools update; B13 round 26 E48, round 27 P53).  The probe takes no model digest and never
    compiles. Never a FAIL."""
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
    if _devtools_missing():
        fix: str | None = _OCR_DEVTOOLS_FIX
    else:
        fix = _helper_again_fix(detail, "speech") or _resolve_again_fix(detail)
    detail = _helper_detail(detail, fix)
    return [_bad("speech", f"speech: {state} ({detail})", Severity.WARN, fix=fix)]


_RETRY_CLAUSE = HELPER_RETRY_CLAUSE
"""How a helper's marker reason ends when its build ran out of time or a signal stopped it
(``convert.speech._TIMED_OUT``, ``convert.ocr``'s): doctor's line drops it when its fix says the re-run, so
the re-run is said once (B13 round 20 X20). The writers' own text, from ``skill``, so the four writers and
doctor share one constant: a reworded clause is dropped too (B13 round 21 Y54). Doctor already imports convert
through ``cycle``, so this is about one copy, not import safety (round 22 Z56)."""


def _helper_detail(detail: str, fix: str | None) -> str:
    """``detail`` without :data:`_RETRY_CLAUSE` when ``fix`` says the re-run (:func:`_helper_again_fix`'s, a
    time-out's or a signal's, or :func:`_resolve_again_fix`'s, whose reason has no such clause); an exact
    suffix, so an older marker without it, other trailing words, or a reason with the developer tools' fix is
    left whole (B13 round 20 X20, round 25 D85)."""
    if fix is None or fix == _OCR_DEVTOOLS_FIX:
        return detail
    return detail.removesuffix(_RETRY_CLAUSE)


def _helper_again_fix(detail: str, helper: str) -> str | None:
    """The fix of a helper build that ran out of time (``ocr``, ``media`` or ``speech``; its reason starts
    with the helper's own time-out words, :func:`helper_timed_out_words`): install.sh again, which builds it
    again; any other failed build gets no fix here, as its reason is the news (B13 round 18 V5, V28; the OCR
    and media helpers, B13 round 19 W49), but for a build step a signal stopped (its reason ends in
    :data:`_RETRY_CLAUSE`, ``ocr._signal_reason``'s), whose fix is the plain re-run, with no time limit and no
    "less busy" (B13 round 25 D85); a speech build whose FluidAudio fetch failed has
    :func:`_resolve_again_fix`'s re-run instead, which ``_check_speech`` tries next (B13 round 24 B42). The 20
    minutes of the speech build cover the FluidAudio fetch too, so its cause is the Mac and the network; the
    OCR and media builds fetch nothing, and stop after 5. The re-run is said once, and the minutes are a
    limit, not a promise (W20). Under install.sh the run is named as :func:`_again_fix` names it,
    :data:`AGAIN_UNDER_INSTALL_SH` (B13 round 19 W19, W48)."""
    if os.environ.get(NO_NEXT_HINT_ENV, "").strip() == "1":
        again = AGAIN_UNDER_INSTALL_SH
    else:
        again = f"run {INSTALL_SH} again"
    try:
        words, minutes = helper_timed_out_words(helper)
    except Exception:  # a runtime error in convert's names or attributes (a renamed constant), never a
        # convert that fails to import: that already fails doctor's import through cycle (B13 round 22 Z56)
        return None
    if not detail.startswith(words):
        return again if detail.endswith(_RETRY_CLAUSE) else None
    busy = "the Mac and the network are" if helper == "speech" else "the Mac is"
    stops = f"the build stops after {minutes} minutes, and if it stops again, run it when {busy} less busy"
    return f"{again}; {stops}"


def _resolve_again_fix(detail: str) -> str | None:
    """The fix of a speech build whose ``swift package resolve`` failed (its reason starts with
    ``speech.RESOLVE_FAILED``): install.sh again, named as :func:`_helper_again_fix` names it, as each run
    fetches FluidAudio again and a dropped connection is the common cause (a plain re-run built it in every
    home of the v32 rehearsal); and, as a blocked host fails every time, where to look when it fails again:
    both hosts of :data:`speech.FETCH_HOSTS`, as resolve downloads the library FluidAudio links from GitHub's
    release host, and a SwiftPM mirror for FluidAudio's own clone (B13 round 25 D53, D65). No time limit and
    no "less busy": this is no time-out (B13 round 24 B42). But for a resolve that needs a newer Swift than
    this Mac's (``speech.TOOLS_TOO_OLD``): a re-run builds with the same Swift and fails the same way, and the
    network is fine, so its fix is a Command Line Tools update first, and names no host (B13 round 26 E48)."""
    if not detail.startswith(speech.RESOLVE_FAILED):
        return None
    if os.environ.get(NO_NEXT_HINT_ENV, "").strip() == "1":
        again = AGAIN_UNDER_INSTALL_SH
    else:
        again = f"run {INSTALL_SH} again"
    too_old = speech.TOOLS_TOO_OLD_RE.search(detail)
    if too_old:
        return (
            f"update the Command Line Tools (or Xcode) in Software Update to a release with Swift "
            f"{too_old['needed']} or later (on a macOS too old for that release, update macOS first), then "
            f"{again}"
        )
    github, assets = speech.FETCH_HOSTS
    return (
        f"{again}; it fetches FluidAudio and the library it links from GitHub again, and if this line comes "
        f"back, check that this Mac reaches {github}, or the SwiftPM mirror it uses for FluidAudio, and "
        f"{assets} (behind a proxy: HTTPS_PROXY set in the shell)"
    )


def helper_timed_out_words(helper: str) -> tuple[str, int]:
    """The words a build of ``helper`` (``ocr``, ``media`` or ``speech``) that ran out of time starts its
    reason with, and its limit in minutes. The setup report keys its bucket on the same words, read from the
    helper's own marker (B13 round 19 W49)."""
    if helper == "speech":
        return speech.BUILD_TIMED_OUT, int(speech._BUILD_TIMEOUT_S // 60)
    kind = media.MEDIA if helper == "media" else ocr._OCR
    return f"{ocr.BUILD_TIMED_OUT} {kind.name}", int(ocr._BUILD_TIMEOUT_S // 60)


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
                _ok("docs_repo.git", f"{repo} does not exist yet; `{BIN} add-source <folder>` creates it")
            )
        else:
            out.append(
                _bad(
                    "docs_repo.git",
                    f"{repo} does not exist and {anchor} is not writable",
                    fix=f"mkdir -p {_shell_path(repo)}",
                )
            )
    elif not repo.is_dir():
        out.append(
            _bad(
                "docs_repo.git",
                f"{repo} exists but is not a directory",
                fix=f"move {_shell_path(repo)} aside",
            )
        )
    elif (repo / ".git").exists():
        is_git = True
        out.append(_ok("docs_repo.git", f"{repo} is a git repository"))
    else:
        out.append(
            _ok(
                "docs_repo.git",
                f"{repo} is not a git repo yet; `{BIN} add-source <folder>` runs git init",
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
                        f"{plural(len(links), 'tracked symlink')}: {shown}",
                        fix=(
                            f"git -C {_shell_path(repo)} rm --cached <path> for each, and replace them with "
                            "real files"
                        ),
                    )
                )
            else:
                out.append(_ok("docs_repo.symlinks", "no tracked symlinks"))
    else:
        out.append(_ok("docs_repo.symlinks", "not a symlink"))
    return out


def _persons_chown(root: Path) -> str:
    """The fix of a state folder or file another account owns (a first ``sudo agentsync sync``): a sudo, which
    the person runs, as the setup prompt's "no sudo" rule says, so ``is_persons_fix`` reads it as theirs and
    no NEXT hands it to the agent (B13 round 20 X74). No bracket of its own: the line wraps the fix in ``(fix:
    ...)`` (B13 round 22 Z49)."""
    return f"the person runs sudo chown -R {os.getuid()} {_shell_path(root)}; the agent runs no sudo"


def _search_fix(root: Path, *, folder: bool = True) -> str:
    """The fix of a state folder (or, ``folder`` False, a file) this account owns, whose mode already gives
    it access, that still refuses it: an ACL or a privacy setting, the person's, never a chmod of the agent's,
    which would change nothing, so its first words are "the person " (B13 round 21, the ownership rule: an
    ACL deny stays the person's, as install.sh's step 1 tail says; lane P measured a file's). Prose after
    "; ", no bracket of its own (B13 round 22 Z49). It names how to take the ACL off as well as how to see it,
    as :func:`_parent_fix` does (B13 round 23 A15): run by the owner, ``chmod -N`` leaves the mode, which
    already gives the owner access; where a privacy setting is the cause it changes nothing."""
    shown = _shell_path(root)
    if not folder:
        return (
            f"the person gives this account read and write access to the file {shown}; its owner and mode "
            f"are right, so an ACL or a privacy setting refuses it; ls -le {shown} shows the ACL, and chmod "
            f"-N {shown} removes it"
        )
    return (
        f"the person gives this account read, write and search access to the folder {shown}; its owner "
        f"and mode are right, so an ACL or a privacy setting refuses it; ls -led {shown} shows the ACL, and "
        f"chmod -N {shown} removes it"
    )


def _unsearchable_above(root: Path) -> Path | None:
    """The first folder above ``root``, from ``/`` down, that refuses this account a search; None when every
    one allows it (B13 round 22 Z72: its parent stood in, a folder that refuses nothing). Read through a link
    where it can be, so a state folder that is a symlink names the folder above its target that refuses (lane
    B's reviewer); a link inside a folder that refuses a search cannot be read and is walked as it is."""
    for above in reversed(Path(os.path.realpath(root)).parents):
        if not os.access(above, os.X_OK):
            return above
    return None


def _parent_fix(root: Path) -> str:
    """The fix of a state folder this account cannot reach (its ``stat`` refused): the person's (the round 21
    ownership rule). A folder above it that refuses a search is named, not the state folder (B13 round 21
    lane B's reviewer), with a ``chmod u+rwx`` of it when it is this account's own folder whose own mode
    refuses the search, which is the whole fix (B13 round 22 Z14; u+rwx, not 700, keeps the group and other
    bits a home folder may carry on purpose); none, as another account's, an ACL or a privacy setting
    refuses whatever its mode. With no folder above refusing, an ACL or a privacy setting on the folder itself
    refuses it, which neither its owner nor its mode can show, as ``ls -led`` is refused too (B13 round 22
    Z72)."""
    above = _unsearchable_above(root)
    if above is None:
        return (
            "the person gives this account read-attributes, read, write and search access to the folder "
            f"{_shell_path(root)}; every folder above it allows a search, so an ACL or a privacy setting on "
            f"the folder itself refuses it, and chmod -N {_shell_path(root)} removes its ACL"
        )
    fix = (
        f"the person gives this account search access to {_shell_path(above)}, a folder above "
        f"{_shell_path(root)} that refuses it one, then run the same command again"
    )
    try:
        st = above.lstat()  # every folder above it allows a search, so this one is seen
    except OSError:
        return fix
    if st.st_uid == os.getuid() and not st.st_mode & stat.S_IXUSR:
        mode = stat.S_IMODE(st.st_mode)
        own = f"it is this account's own folder at mode {mode:04o}"
        fix += f"; {own}, so chmod u+rwx {_shell_path(above)} gives it"
    return fix


def _read_only(path: Path) -> bool:
    """Whether ``path`` is on a volume mounted read-only (no write for anyone, whatever its mode)."""
    try:
        return bool(os.statvfs(path).f_flag & os.ST_RDONLY)
    except (OSError, AttributeError):
        return False


def read_only_fix(path: Path) -> str:
    """The fix of a state path on a read-only volume: the person's, as no chmod and no ACL change writes there
    (B13 round 21 lane B's reviewer: the ACL words named a cause it was not). No bracket of its own (B13
    round 22 Z49)."""
    return f"the person makes the volume that holds {_shell_path(path)} writable, as it is mounted read-only"


def state_folder_fix(root: Path) -> str:
    """The fix of a state folder this account cannot look inside, as state_dir's lines give it (B13 round 20
    X73: W65's "owner and mode 600" still refuses a folder a search), the first that applies: the person's
    fix naming the first folder above it that refuses a search (:func:`_parent_fix`), when it cannot be
    stat'ed; the person's chown when another account owns it; the person's fix for a read-only volume; the
    person's access (ACL) fix when its owner bits already give access and it still refuses; ``chmod 700``,
    the agent's, when its mode refuses it; else the person's access (ACL) fix (B13 round 22 Z52, Z64, Z78)."""
    try:
        st = root.stat()
    except PermissionError:  # a folder above it refuses a search: the person's, as an ACL is (round 21)
        return _parent_fix(root)
    except OSError:
        return f"make {_shell_path(root)} this account's folder at mode 700, then run again"
    if st.st_uid != os.getuid():
        return _persons_chown(root)
    if _read_only(root):  # before the mode: no chmod writes there (B13 round 22 Z78)
        return read_only_fix(root)
    # its mode already gives the owner access and it still refuses: an ACL, the person's, whatever its group
    # and other bits, which a chmod 700 would fix and still leave it refused (B13 round 22 Z64)
    if st.st_mode & 0o700 == 0o700 and not os.access(root, os.R_OK | os.W_OK | os.X_OK):
        return _search_fix(root)
    if stat.S_IMODE(st.st_mode) != 0o700:
        return f"chmod 700 {_shell_path(root)}"
    return _search_fix(root)


def state_path_fix(root: Path, path: Path) -> str:
    """The fix of ``path``, in the state folder ``root``, that this account was refused: the first of the
    folder, a folder below it, or the file itself that refuses it, with that one's fix (another account's:
    the person's chown of the folder; on a read-only volume the person's volume fix; this account's at a mode
    that refuses it: ``chmod 700`` a folder, ``chmod 600`` a file; this account's whose mode already gives it
    access: the person's access (ACL) fix, search for a folder, read and write for a file), else
    :func:`state_folder_fix`, which is where the fix of a folder above the state folder comes from (B13 round
    20 X73: a lock at 0000 in a healthy folder was given the folder's ACL fix; round 22 Z52)."""
    try:
        below = path.relative_to(root).parts
    except ValueError:
        return state_folder_fix(root)
    chain = [root] + [root.joinpath(*below[: i + 1]) for i in range(len(below))]
    for at in chain:
        try:
            st = at.lstat()
        except PermissionError:  # its folder refuses a search, and that folder's own check passed
            break
        except OSError:
            return state_folder_fix(root)
        if st.st_uid != os.getuid():
            return _persons_chown(root)
        mode = stat.S_IMODE(st.st_mode)
        if stat.S_ISDIR(st.st_mode):
            if not os.access(at, os.R_OK | os.W_OK | os.X_OK):
                if at == root:
                    return state_folder_fix(root)
                if _read_only(at):  # before the mode: no chmod writes there (B13 round 22 Z78)
                    return read_only_fix(at)
                # its mode already gives the owner access: an ACL, the person's (round 21, lane P's measure)
                if mode & 0o700 != 0o700:
                    return f"chmod 700 {_shell_path(at)}"
                return _search_fix(at)
        elif not os.access(at, os.R_OK | os.W_OK):
            if _read_only(at):  # (Z78)
                return read_only_fix(at)
            if mode & 0o600 != 0o600:
                return f"chmod 600 {_shell_path(at)}"
            return _search_fix(at, folder=False)
    return state_folder_fix(root)


def refused_state_path(config: Config, name: str, exc: OSError) -> CheckResult | None:
    """``name``'s FAIL for ``exc``, a refused open of a path in the state folder, by its cause: the path and
    the system's words, with the fix of the folder or file that refused it (:func:`state_path_fix`: this
    account's ``chmod 600`` or ``chmod 700``; another account's chown, a read-only volume, an ACL or a folder
    above that refuses a search, the person's; B13 round 22 Z52); None for any other
    error. The one shape of status's governance lines (``cli._refused_check``) and of the lock and heartbeat
    checks, so each says what ``sync``'s ``error: state:`` line says (B13 round 21 Y43: those two gave the
    generic "could not read ... report it" FAIL)."""
    root = expand(config.state_paths.root)
    if exc.filename is None or not state_unopenable(exc, root):
        return None
    path = Path(os.path.realpath(os.fsdecode(exc.filename)))
    detail = f"{path} cannot be opened: {exc.strerror or exc}"
    return _bad(name, detail, fix=state_path_fix(Path(os.path.realpath(root)), path))


def refused_state_files(config: Config) -> list[Path]:
    """The manifest, its -wal and its -shm, each there that this account cannot read and write: another
    account's, or one ``os.access`` refuses (its mode, an ACL, a read-only volume), state_dir.files' own test.
    One list for the CLI's error line and the setup report's (B13 round 22 Z80); a file it cannot see (a
    folder that refuses a search) is not listed: the state_dir lines say that."""
    db = expand(config.state_paths.db)
    out: list[Path] = []
    for p in (db, *manifest_sides(db)):
        try:
            st = p.lstat()
            if stat.S_ISLNK(st.st_mode):
                if p != db:
                    continue  # a side link is no file sqlite opens: its own FAIL (B13 round 26 E62)
                st = p.stat()  # a linked manifest by its target, as state_dir.files reads it (E63)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        if st.st_uid != os.getuid() or not os.access(p, os.R_OK | os.W_OK):
            out.append(p)
    return out


def refused_files_fix(config: Config, files: Sequence[Path]) -> str:
    """One fix for every file of ``files`` (:func:`refused_state_files`), as status's state_dir.files FAIL
    gives it: ``chmod 600`` of them all when each is this account's own at a mode that refuses it, so one
    chmod leaves none for the next run (B13 round 22 Z80's verifier: one file's fix was the trap of fixing one
    file a run); else the fix of the first that is not, another account's or one an ACL or a read-only volume
    refuses, the person's (:func:`state_path_fix`)."""
    for p in files:
        st = p.stat()  # a linked manifest by its target (B13 round 26 E63)
        if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o600 == 0o600 or _read_only(p):
            return own_path_fix(p)  # the target's own folder, never the state folder's (B13 round 28 I52)
    # each by its own path, which a chmod follows through a link, as status names it (I52)
    return "chmod 600 " + " ".join(_shell_path(expand(p)) for p in files)


def own_path_fix(path: Path) -> str:
    """:func:`state_path_fix` of a state file at its resolved path, judged against its resolved folder: a
    -wal or -shm beside a linked manifest's target lives in the target's folder, so its chmod or the person's
    fix names it there, never the state folder (B13 round 28 I52; install.sh and the setup report use it,
    with a fallback for an older build)."""
    resolved = Path(os.path.realpath(expand(path)))
    return state_path_fix(resolved.parent, resolved)


def _not_a_file(mode: int) -> str | None:
    """What an ``lstat`` mode is when it is neither a regular file nor a symlink (``a folder``, ``a socket``,
    ``a fifo``, ``not a regular file``), else None: a symlink is judged by what it points to
    (:func:`state_file_kind`)."""
    if stat.S_ISREG(mode) or stat.S_ISLNK(mode):
        return None
    if stat.S_ISDIR(mode):
        return "a folder"
    if stat.S_ISSOCK(mode):
        return "a socket"
    if stat.S_ISFIFO(mode):
        return "a fifo"
    return "not a regular file"


def state_file_kind(path: Path) -> str | None:
    """What is at ``path``, where a state file belongs, when it is not a file agentsync can open: a folder, a
    socket or a fifo (:func:`_not_a_file`), a symlink loop, or a symlink to a folder (or another non-file),
    read through the link as sqlite and the setup report read it (B13 round 23 A46: a link read as a file, so
    no state_dir.manifest line was printed while the report pointed at one, and a loop got an ACL FAIL and a
    link to a folder a ``chmod 600`` that would follow the link). A link through a file (ENOTDIR: a file where
    a folder on its way belongs) is ``a symlink to nothing``, as :func:`link_kind` and :func:`dangling_target`
    word it (B13 round 27 P132: it read as a file, so no guard saw it and a hold list behind it read as a
    fault to report). None for a regular file, a symlink to one, a symlink whose target is missing (no
    manifest yet; a governance file's judge is :func:`governance_file_kind`) or nothing there."""
    try:
        st = path.lstat()
    except OSError:
        return None
    if not stat.S_ISLNK(st.st_mode):
        return _not_a_file(st.st_mode)
    try:
        target = path.stat()
    except OSError as exc:
        return {errno.ELOOP: "a symlink loop", errno.ENOTDIR: "a symlink to nothing"}.get(exc.errno or 0)
    kind = _not_a_file(target.st_mode)
    return None if kind is None else f"a symlink to {kind}"


def governance_file_kind(path: Path) -> str | None:
    """:func:`state_file_kind` of a governance file, where a symlink whose target is missing is ``a symlink
    to nothing`` too, the person's (ruling 13): no first sync creates a hold list, and a dangling holds.json
    read as no holds, so ``purge --queue`` erased a page under a hold on all (B13 round 27 P131). None for a
    regular file, a link to one (a working setup never newly FAILs) or nothing there."""
    kind = state_file_kind(path)
    if kind is not None:
        return kind
    try:
        if not stat.S_ISLNK(path.lstat().st_mode):
            return None
    except OSError:  # nothing there, or a folder above it this account cannot search: its own lines
        return None
    try:
        path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return "a symlink to nothing"
    except OSError:
        return None
    return None


def governance_dir_kind(gov: Path) -> str | None:
    """What is at governance/ when it is not a folder agentsync can use, the person's: ``a symlink to
    nothing`` (the link's stat says ENOENT or ENOTDIR), ``a symlink loop`` (ELOOP), ``a file``, ``a fifo``,
    ``a socket`` (or ``not a folder``), at it or behind a link (``a symlink to a file``). None when nothing is
    there (a fresh install), for a folder, a link to one, or one this account cannot search, whose own
    permission lines speak. Its own judge, by lstat: :func:`state_file_kind` and :func:`governance_file_kind`
    call a folder not a file, which would FAIL every working install (B13 round 28 I88, I89)."""
    try:
        st = gov.lstat()
    except OSError:  # nothing there, or a folder above it this account cannot search: its own lines
        return None
    if stat.S_ISLNK(st.st_mode):
        try:
            st = gov.stat()
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                return "a symlink loop"
            if exc.errno in (errno.ENOENT, errno.ENOTDIR):
                return "a symlink to nothing"
            return None  # refused on its way (EACCES): its own lines
        kind = _not_a_folder(st.st_mode)
        return None if kind is None else f"a symlink to {kind}"
    return _not_a_folder(st.st_mode)


def _not_a_folder(mode: int) -> str | None:
    """What a stat mode is when it is not a folder (``a file``, ``a fifo``, ``a socket``, ``not a folder``),
    else None."""
    if stat.S_ISDIR(mode):
        return None
    if stat.S_ISREG(mode):
        return "a file"
    if stat.S_ISFIFO(mode):
        return "a fifo"
    if stat.S_ISSOCK(mode):
        return "a socket"
    return "not a folder"


def dangling_target(path: Path) -> Path | None:
    """Where ``path`` points when it is a symlink to nothing (its target is missing), else None. Such a link
    is no file this account is refused: an ACL line and its ``chmod -N`` were false of it, and the chmod
    fails on a link to nothing (B13 round 24 B64)."""
    try:
        if not stat.S_ISLNK(path.lstat().st_mode):
            return None
        path.stat()
        return None
    except (FileNotFoundError, NotADirectoryError):  # a link through a file is to nothing too (E45)
        pass
    except OSError:
        return None
    try:
        return path.parent / path.readlink()
    except OSError:  # gone in between
        return None


DANGLING_UNUSABLE = "whose folder does not exist or refuses a write"
"""Status's words for where a link to nothing points when no sync can create the file through it
(:func:`dangling_into_unusable`; B13 round 24 B64), after ``<path> is a symlink to <target>, ``: the setup
report keys a lost manifest's link words on them (B13 round 27 P33, P58)."""


def dangling_into_unusable(path: Path) -> Path | None:
    """Where ``path`` points when it is a symlink to nothing (:func:`dangling_target`) whose folder is not a
    folder this account can write and search, so no sync can create the file through it (B13 round 24 B64,
    round 25 D34); else None, a link into a usable folder included (the first sync creates the file it points
    at). Never raises. One test for status's state_dir lines, its manifest line and the setup report (B13
    round 26 E75, E41). The manifest's alone since round 26 E62: a -wal or -shm link of any kind is no
    file."""
    try:
        target = dangling_target(path)
        if target is None:
            return None
        folder = target.parent
        return None if folder.is_dir() and os.access(folder, os.W_OK | os.X_OK) else target
    except OSError:
        return None


def manifest_refused(db: Path) -> bool:
    """Whether a sync stops on what is at ``db`` while no manifest file is there to read: a link to nothing
    whose folder it cannot use (:func:`dangling_into_unusable`) or a link loop or a link to a non-file
    (:func:`state_file_kind`), so status never says the next sync starts a new manifest beside the FAIL it
    stops on (B13 round 26 E41)."""
    # os.path.exists, never Path.exists, which raises in a folder this account cannot search
    return not os.path.exists(db) and (  # noqa: PTH110
        state_file_kind(db) is not None or dangling_into_unusable(db) is not None
    )


def link_kind(path: Path) -> str | None:
    """What a symlink at ``path`` is, by the link's own lstat: :func:`state_file_kind`'s words for a link to
    a folder or another non-file and for a loop, else ``a symlink to nothing`` (its target missing, or a
    file where a folder on its way belongs) or ``a symlink to a file``; None when ``path`` is no symlink.
    For a state file no link may stand in for: the lock, which a sync opens with O_NOFOLLOW (a link there
    ended every sync on a traceback while status said ``free``), and a -wal or -shm, which sqlite never opens
    through a link (B13 round 26 E38, E62; ruling 13)."""
    try:
        if not stat.S_ISLNK(path.lstat().st_mode):
            return None
    except OSError:
        return None
    kind = state_file_kind(path)
    if kind is not None:
        return kind
    try:
        path.stat()
    except OSError:
        return "a symlink to nothing"
    return "a symlink to a file"


def own_kind(path: Path) -> str | None:
    """What is at ``path`` when it is not a regular file, a link read as itself (:func:`link_kind`): a
    folder, a socket, a fifo, or any symlink; None for a regular file or nothing there (B13 round 26 E38)."""
    try:
        mode = path.lstat().st_mode
    except OSError:
        return None
    return link_kind(path) if stat.S_ISLNK(mode) else _not_a_file(mode)


def _them(names: Sequence[str], one: str, many: str) -> str:
    """``one`` for a single file, ``many`` for more (B13 round 20 X17: "them: manifest.sqlite (0000)")."""
    return one if len(names) == 1 else many


def state_folder_shut(config: Config) -> bool:
    """Whether this account is refused a look inside the state folder (no search for it: a mode, another
    owner, an ACL or a sandbox), so the checks that read the files there cannot answer: they say not checked,
    and the state_dir lines say why (B13 round 20 X73: each was a generic FAIL to report, and governance's a
    crash). A missing folder, or one that is a file, is not that: state_dir says so, and those checks run."""
    sp = config.state_paths
    try:
        (expand(sp.root) / sp.db.name).lstat()
    except PermissionError:
        return True
    except OSError:
        return False
    return False


STATE_FOLDER_UNCHECKED = (
    "not checked: this account cannot look inside the state folder; the state_dir [FAIL] line says why"
)
"""The [info] line of a check that reads the state folder, when :func:`state_folder_shut` (B13 round 20
X73), naming the one state_dir check that FAILs it (:func:`state_folder_shut_line`): "the state_dir lines"
was false over one line (B13 round 27 P122)."""


def state_folder_shut_line(config: Config) -> str:
    """The one check whose FAIL says why this account cannot look inside the state folder:
    ``state_dir.files`` when the folder is this account's with its owner bits open (its owner and mode are
    right, so the files check's "cannot look inside" FAIL is the one printed: an ACL, a sandbox), else
    ``state_dir`` (another account's, a mode, a folder above it that refuses a search) (B13 round 27 P122:
    "the state_dir lines say why" over one line, and a fixed "state_dir" would point at an [ok] line)."""
    try:
        st = expand(config.state_paths.root).stat()
    except OSError:
        return "state_dir"
    own = st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) & 0o700 == 0o700
    return "state_dir.files" if own else "state_dir"


def state_folder_says_why(config: Config) -> str:
    """``the <check> [FAIL] line says why`` of :func:`state_folder_shut_line`."""
    return f"the {state_folder_shut_line(config)} [FAIL] line says why"


STATE_FOLDER_NOT_A_FOLDER = "not checked: the state folder is not a folder (see state_dir)"
"""The [info] line of a check that reads the state folder, when :func:`state_root_not_folder` (round 23
ruling 4): not :data:`STATE_FOLDER_UNCHECKED`, whose "cannot look inside" is false of a file."""


def _not_a_folder_at(root: Path) -> Path | None:
    """The first path, from ``/`` down to ``root``, that is there but is not a folder (a file, a symlink to
    one, a socket, a fifo, a symlink loop), read through links, so a symlink to a folder passes; None when
    each is a folder, or one is missing or refuses a look (state_dir's other lines say those)."""
    for path in (*reversed(root.parents), root):
        try:
            st = path.stat()
        except OSError as exc:
            return path if exc.errno == errno.ELOOP else None
        if not stat.S_ISDIR(st.st_mode):
            return path
    return None


def state_root_not_folder(config: Config) -> Path | None:
    """The state folder, or the first folder above it, that is not a folder (:func:`_not_a_folder_at`):
    the person's to move aside, one FAIL, state_dir's, while every check that reads the state folder says
    :data:`STATE_FOLDER_NOT_A_FOLDER` and nothing raises (B13 round 23 A7, A22, A33, A58; round 23 ruling 4:
    heartbeat FAILed with a generic "report it" fix and governance crashed on ``[Errno 20]``). Keyed on the
    stat of the state folder and the folders above it, never on any ENOTDIR, which would hide unrelated
    faults."""
    return _not_a_folder_at(expand(config.state_paths.root))


def state_folder_unchecked(config: Config) -> str | None:
    """The [info] words of a check that reads the state folder when it cannot:
    :data:`STATE_FOLDER_NOT_A_FOLDER` for a state folder (or a folder above it) that is not a folder,
    :data:`STATE_FOLDER_UNCHECKED` for one this account cannot look inside (:func:`state_folder_shut`); None
    when the checks can read it."""
    if state_root_not_folder(config) is not None:
        return STATE_FOLDER_NOT_A_FOLDER
    if not state_folder_shut(config):
        return None
    return STATE_FOLDER_UNCHECKED.replace("the state_dir [FAIL] line says why", state_folder_says_why(config))


def _not_a_folder_fail(root: Path, that: Path) -> CheckResult:
    """state_dir's FAIL for ``that``, the state folder or a folder above it, which is not a folder: the
    person's move aside, keeping a copy, since agentsync never writes a file there (round 23 ruling 4; B13
    round 23 A22, A33: ``move <root> aside`` did not start "the person ", so status's NEXT gave the agent the
    person's step)."""
    try:
        mode = that.stat().st_mode
        kind = (
            "a file"
            if stat.S_ISREG(mode)
            else "a socket"
            if stat.S_ISSOCK(mode)
            else "a fifo"
            if stat.S_ISFIFO(mode)
            else "a special file"
        )
    except OSError:
        kind = "a symlink loop"
    detail = f"{that} is {kind}, not a folder"
    if that != root:
        detail += f", and the state folder {root} sits under it"
    return _bad("state_dir", detail, fix=f"the person moves {_shell_path(that)} aside, keeping a copy")


def _in_shut_state_folder(config: Config, path: Path) -> str | None:
    """:func:`state_folder_unchecked`'s words when ``path`` is the state folder or lies in it: a ``log_dir``
    or ``cache_dir`` set there cannot be read either, and its check says not checked, not a generic FAIL whose
    agent's fix took status's NEXT (B13 round 22 Z60; round 23 ruling 4 for a state folder that is not a
    folder); None for a path outside it, or a state folder the checks can read."""
    root = expand(config.state_paths.root)
    return state_folder_unchecked(config) if path == root or path.is_relative_to(root) else None


def stopped(config: Config, name: str, exc: Exception) -> CheckResult:
    """The result of the check ``name`` that raised ``exc``: a path in the state folder this account was
    refused (a folder above it that refuses a search, a mode, an ACL) is that path's FAIL with its fix
    (:func:`refused_state_path`), so status's NEXT says whose it is; anything else :func:`unfinished`. The
    backstop of :func:`run_checks` and status's own checks (B13 round 22 Z60: a crash under a folder above the
    state folder gave the generic "report it" fix, read as the agent's, which took the NEXT from state_dir's
    person's fix)."""
    if isinstance(exc, OSError):
        refused = refused_state_path(config, name, exc)
        if refused is not None:
            log.info("doctor: check %s stopped on a refused state path", name, exc_info=exc)
            return refused
        shut = _behind_the_state_folders_refuser(config, exc)
        if shut is not None:
            log.info("doctor: check %s stopped under a folder that refuses a search", name, exc_info=exc)
            return _bad(name, shut, Severity.INFO)
    if _under_the_non_folder(config, exc):
        log.info("doctor: check %s stopped under a state folder that is not a folder", name, exc_info=exc)
        return _bad(name, STATE_FOLDER_NOT_A_FOLDER, Severity.INFO)
    return unfinished(name, exc)


def _under_the_non_folder(config: Config, exc: BaseException) -> bool:
    """Whether ``exc`` (or the error it wraps: a GovernanceError keeps its cause) is an ENOTDIR or ELOOP on a
    path under the state folder, or the folder above it, that is not a folder (:func:`state_root_not_folder`):
    one fault, state_dir's (round 23 ruling 4; B13 round 23 A58: heartbeat and governance gave report-it and
    check-crashed lines beside it)."""
    cause = exc if isinstance(exc, OSError) else exc.__cause__ or exc.__context__
    if not isinstance(cause, OSError) or cause.errno not in (errno.ENOTDIR, errno.ELOOP):
        return False
    that = state_root_not_folder(config)
    if that is None:
        return False
    if not isinstance(cause.filename, str | bytes | os.PathLike):
        return True  # no path to place it: the state folder that is not a folder explains it
    path = Path(os.fsdecode(cause.filename))
    real, real_that = Path(os.path.realpath(path)), Path(os.path.realpath(that))
    return path.is_relative_to(that) or real.is_relative_to(real_that)


def _behind_the_state_folders_refuser(config: Config, exc: OSError) -> str | None:
    """The not-checked words for ``exc``, an EACCES on a path under the folder above the state folder that
    refuses this account a search (``~/Library`` at 0000 holds the default cache and log folders too): one
    fault, the person's, which state_dir's FAIL names, so the cache and logs checks say not checked rather
    than a generic "report it" FAIL that took status's NEXT (round 22 ruling 2; lane B's reviewer). None for
    any other error."""
    if exc.errno not in (errno.EACCES, errno.EPERM) or not isinstance(
        exc.filename, str | bytes | os.PathLike
    ):
        return None
    above = _unsearchable_above(expand(config.state_paths.root))
    if above is None:
        return None
    path = Path(os.path.realpath(os.fsdecode(exc.filename)))
    if not path.is_relative_to(above):
        return None
    said = f"not checked: this account cannot search {above}, a folder above the state folder"
    return f"{said}; the state_dir [FAIL] line says why"  # its one line (P122)


SIDE_ROLES = {"-wal": "the state file's write-ahead log", "-shm": "the state file's shared-memory file"}
"""What each side file of the manifest is, in the words of its state_dir.files FAIL (B13 round 25 D6)."""


def manifest_sides(db: Path) -> tuple[Path, ...]:
    """The manifest's -wal and -shm where sqlite keeps them: beside ``db``, or, when ``db`` is a symlink,
    beside its target (a link to nothing: beside the file its first sync creates there); none for a link
    loop, whose own state_dir.manifest FAIL covers it. One helper for status, doctor, the error lines, the
    refused-file list and the setup report: beside a linked manifest a folder at the target's -shm passed
    status while every sync stopped on it, and a harmless leftover beside the link blocked curate (B13 round
    27 P42; ruling 18). Never raises."""
    try:
        linked = stat.S_ISLNK(db.lstat().st_mode)
    except OSError:
        linked = False
    if linked:
        try:
            db = Path(os.path.realpath(db, strict=True))
        except (FileNotFoundError, PermissionError):
            # a link to nothing, or into a folder this account cannot search: beside the target it names, so
            # the state_dir.files line names that folder (P42's reviewer)
            db = Path(os.path.realpath(db))
        except OSError:  # a loop, or a link through a file: the manifest's own FAIL
            return ()
    return tuple(db.with_name(db.name + suffix) for suffix in SIDE_ROLES)


def side_suffix(side: Path) -> str:
    """``-wal`` or ``-shm`` for a side :func:`manifest_sides` names, by its own name, else ""."""
    return next((suffix for suffix in SIDE_ROLES if side.name.endswith(suffix)), "")


def _not_a_file_detail(sp: StatePaths, p: Path, kind: str) -> str:
    """The detail of a state path that is not a file (:func:`state_file_kind`): the manifest's "is <kind>, not
    the state file"; a side file's, the lock's, the heartbeat's and the token cache's name the file by its
    role, "not a file: the state file's write-ahead log belongs there", since "not the state file" said the
    -shm was the manifest (B13 round 25 D6, D21: the sync's line, status's and the report's read the same
    words). The role follows a colon, not in brackets: the line's ``(fix: ...)`` came right after it, two
    brackets back to back (B13 round 26 E18); the report's readers key on ", not a file", either way."""
    if p == sp.db:
        return f"{expand(p)} is {kind}, not the state file"
    # by its suffix: beside a linked manifest the side is the target's, real.sqlite-shm (P42)
    suffix = side_suffix(p) if p not in (sp.lock, sp.heartbeat) else ""
    role = SIDE_ROLES.get(suffix) or {sp.lock: "the lock", sp.heartbeat: "the heartbeat"}.get(
        p, "the token cache"
    )
    return f"{expand(p)} is {kind}, not a file: {role} belongs there"


def _opens_first(path: Path, kind: str) -> str:
    """The step a folder at a state path needs before it can be copied or moved, when this account cannot
    read, write and search it: macOS renames a folder only with write on the folder itself, and copies it only
    with read and search (measured: a 0000 or 0500 folder refuses ``mv``, a 0300 one ``cp -R``). This
    account's own: a ``chmod 700``, reversible; another account's: the person's sudo chown. "" when the mode
    already gives access, or for a link, which moves aside whatever its target's mode (B13 round 25 D72)."""
    if kind != "a folder":
        return ""
    try:
        if os.access(path, os.R_OK | os.W_OK | os.X_OK):
            return ""
        own = path.lstat().st_uid == os.getuid()
    except OSError:
        return ""
    shown = _shell_path(path)
    if own:
        step = f"runs chmod 700 {shown} first, which is reversible"
    else:
        step = f"runs sudo chown -R {os.getuid()} {shown} first (the agent runs no sudo)"
    return f"{step}, as a folder shut to this account can be neither copied nor moved; then "


def _not_a_file_fix(sp: StatePaths, p: Path, kind: str) -> str:
    """The person's fix of a state path that is not a file: MANIFEST_FIX for the manifest, a move aside,
    keeping a copy, as install.sh's step 1 and step 2 say it, for a side file or the token cache (B13 round 25
    D22, ruling 12), each after the step that opens a folder shut to this account (:func:`_opens_first`)."""
    first = _opens_first(expand(p), kind)
    if p == sp.db:
        return "the person " + first + MANIFEST_FIX.removeprefix("the person ") if first else MANIFEST_FIX
    return f"the person {first}moves {_shell_path(expand(p))} aside, keeping a copy"


def side_not_a_file_at(db: Path) -> Path | None:
    """The manifest's -wal or -shm beside ``db`` that is not a file sqlite can open (the first): a folder, a
    socket, a fifo, a link to one or a loop (:func:`state_file_kind`), or a link to nothing whose folder is
    missing or refuses a write (:func:`dangling_target`), as state_dir.files FAILs it; else None. By the stat,
    never by sqlite's error, which says only CANTOPEN or READONLY (B13 round 25 D2, D8, D70). Beside a
    manifest that is no link, any link at a side is one: sqlite opens a side only where it is, never
    through a link, so a link to nothing in a writable folder or to a regular file stopped every sync on
    "unable to open database file" while status passed it (B13 round 26 E62); beside a manifest that is a
    link, sqlite keeps its sides beside the target, so those are judged and nothing beside the link is
    (:func:`manifest_sides`; B13 round 27 P42)."""
    found = sides_not_a_file_at(db)
    return found[0] if found else None


def sides_not_a_file_at(db: Path) -> list[Path]:
    """Every side :func:`side_not_a_file_at` judges not a file, the -wal first, beside a linked manifest's
    target (:func:`manifest_sides`; P42), so a note naming them names both (B13 round 27 P16)."""
    return [
        side
        for side in manifest_sides(db)
        if link_kind(side) is not None or state_file_kind(side) is not None
    ]


def side_not_a_file(config: Config) -> tuple[Path, CheckResult] | None:
    """That side (:func:`side_not_a_file_at`) and status's own state_dir.files FAIL of it, for the sync's,
    curate's and init's error line, which print it as status does (round 25 ruling 12); None when every side
    is a file or status's check raises (the error path never raises a second error)."""
    try:
        side = side_not_a_file_at(expand(config.state_paths.db))
        if side is None:
            return None
    except Exception:
        return None
    return _side_fail(config, side)


def _side_fail(config: Config, side: Path) -> tuple[Path, CheckResult] | None:
    """``side`` and status's own state_dir.files FAIL of it, or None when status names none or its check
    raises (the error path never raises a second error)."""
    try:
        results = _check_state_dir(config)
    except Exception:
        return None
    named = f"{side} is "
    found = (r for r in results if r.name == "state_dir.files" and not r.ok and r.detail.startswith(named))
    result = next(found, None)
    return None if result is None else (side, result)


def _openable_wal_fifo(side: Path) -> bool:
    """Whether ``side`` is a -wal fifo this account can read and write: sqlite removes it on close and the
    run goes on, so only it is left to the open (B13 round 27 P49). The access check opens nothing, so a fifo
    never blocks."""
    try:
        return (
            side.name.endswith("-wal")
            and stat.S_ISFIFO(side.lstat().st_mode)
            and os.access(side, os.R_OK | os.W_OK)
        )
    except OSError:
        return False


_GOVERNANCE_CHECKS = {
    "holds.json": "governance.hold",
    "purge-queue.json": "governance.purge_queue",
    "suppressed.json": "governance.suppressed",
    "audit.jsonl": "governance.audit",
    "purge-record.json": "governance.suppressed",  # the state folder's, beside the hold record (C381)
}
"""The status check that FAILs each governance file that is not a file (B13 round 26 E43)."""


def governance_check(path: Path) -> str:
    """The status check that FAILs the governance file at ``path`` (:data:`_GOVERNANCE_CHECKS`), or
    governance.hold for governance/ itself and any other path: one name for status's FAIL, its "cannot read
    the state" pointer and every command's line, so they never drift (B13 round 28 I25, I49: the pointer said
    "its governance [FAIL] line", a check that does not exist)."""
    return _GOVERNANCE_CHECKS.get(path.name, "governance.hold")


def state_not_a_file(
    config: Config, *, manifest: bool = True, damaged: bool = False, lost: bool = False, record: bool = True
) -> tuple[Path, CheckResult] | None:
    """The first state file that is not a regular file where one belongs, and status's own FAIL of it, judged
    by stat before anything opens it (so a fifo never blocks): a -wal or -shm (:func:`side_not_a_file`; a
    fifo at the -wal this account can read and write is left to the open, since sqlite removes it on close
    and the run goes on; one it cannot open stops here as a folder does: B13 round 27 P49), the manifest
    (a folder, a fifo, a socket, a loop, a link to nothing whose folder is missing: its state_dir.manifest
    FAIL), the lock (:func:`lock_not_a_file`), a folder at heartbeat.json (:func:`heartbeat_not_a_file`) and
    the governance files; None when none is, the state folder itself is not a folder (its own guard), or any
    check raises. Every command that opens one prints it as its ``error: state:`` line (B13 round 26 E22,
    E38, E39, E40, E43, E65; ruling 13). ``manifest=False`` skips the manifest and its sides, which a sync's
    own open already stops on, with its lost-manifest record (``kind=lost``). ``lost=True`` (the sync and the
    commands that publish as it does) adds a suppression list or a purge queue that lost a purge the audit
    trail records (``governance.governance_lost``; B13 round 29 C172); a hold or a purge is never stopped on
    it, so the person can run each purge again. ``record=False`` (a hold being set) passes a hold record that
    cannot be read, whose own fix sets each hold again first (B13 round 30 C382; ruling 34)."""
    try:
        sp = config.state_paths
        if state_root_not_folder(config) is not None:
            return None
        db = expand(sp.db)
        # every side, the -wal first, past an openable -wal fifo only (P49): a -shm folder beside one was
        # never judged, so init passed it and step 1's "stops at its config step" was false (B13 round 28 I16)
        for bad_side in sides_not_a_file_at(db) if manifest else []:
            if not _openable_wal_fifo(bad_side):
                found = _side_fail(config, bad_side)
                if found is not None:
                    return found
        if (
            manifest
            and os.path.lexists(db)
            # a link to nothing in a usable folder is a missing manifest, lost or a first start, never a
            # file that is not a file: hold and release are not refused over it (B13 round 27 P41)
            and (state_file_kind(db) is not None or dangling_into_unusable(db) is not None)
        ):
            named = f"{db} is "
            for r in _check_state_dir(config):
                if r.name == "state_dir.manifest" and not r.ok and r.detail.startswith(named):
                    return db, r
        lock = expand(sp.lock)
        kind = own_kind(lock)
        if kind is not None:
            return lock, lock_not_a_file(config, kind)
        folder = heartbeat_not_a_file(config)
        if folder is not None:
            return expand(sp.heartbeat), folder
        from agentsync import governance  # noqa: PLC0415 - governance imports this module lazily

        odd: governance.GovernanceUnreadError | None = governance.governance_not_a_file(
            expand(sp.root), record=record
        )
        if odd is None and damaged:  # the commands' guard: a damaged or wrong-shaped one too, read only once
            # each is a regular file (B13 round 28 I90); install.sh's probe asks for the kinds alone
            odd = governance.governance_damaged(expand(sp.root), record=record)
        if odd is None and lost:
            odd = governance.governance_lost(expand(sp.root))
        if odd is not None:
            return odd.path, _bad(governance_check(odd.path), odd.detail, fix=odd.fix)
    except Exception:  # the error path: never a second error over the first
        return None
    return None


def _no_manifest_yet(config: Config) -> str:
    """state_dir.files' ok line with no manifest: "the first sync" only over a mirror with no page; after the
    person's new start (heartbeat.json aside, LOST_MANIFEST_FIX) the pages earlier syncs wrote are there, and
    the next sync starts a new manifest over them (B13 round 25 D26: "created 0600 by the first sync" over 24
    mirror pages). One count, the repair's (``cycle.unowned_mirror_pages``)."""
    try:
        pages = len(unowned_mirror_pages(config))
    except OSError:
        pages = 0
    if not pages:
        return "no manifest yet (created 0600 by the first sync)"
    return f"no manifest yet (the next sync starts one, 0600, over {plural(pages, 'existing mirror page')})"


def _check_state_dir(config: Config) -> list[CheckResult]:
    """state_dir exists with mode 0700 (owned by this user); the db and token fallback are 0600."""
    sp = config.state_paths
    root = expand(sp.root)
    out: list[CheckResult] = []
    try:
        st = root.stat()
    except PermissionError as exc:  # a folder above it refuses a search: the person's (B13 round 21)
        out.append(_bad("state_dir", f"cannot reach {root}: {exc.strerror or exc}", fix=_parent_fix(root)))
        return out
    except FileNotFoundError:
        out.append(
            _bad(
                "state_dir",
                f"{root} does not exist; the first sync creates it",
                Severity.WARN,
                fix=f"mkdir -m 700 -p {_shell_path(root)}",
            )
        )
        return out
    except OSError as exc:  # a file where a folder above it belongs (ENOTDIR), a symlink loop (ELOOP): A58
        that = _not_a_folder_at(root) if exc.errno in (errno.ENOTDIR, errno.ELOOP) else None
        if that is None:  # the path changed in between, or another error: the backstop's words
            raise
        out.append(_not_a_folder_fail(root, that))
        return out
    if not stat.S_ISDIR(st.st_mode):
        out.append(_not_a_folder_fail(root, _not_a_folder_at(root) or root))
        return out
    mode = stat.S_IMODE(st.st_mode)
    if st.st_uid != os.getuid():
        out.append(
            _bad(
                "state_dir",
                f"{root} is owned by uid {st.st_uid}, not {os.getuid()}",
                fix=_persons_chown(root),
            )
        )
    elif mode != 0o700:
        out.append(
            _bad(
                "state_dir",
                f"{root} has mode {mode:04o}; cursors and tokens need 0700",
                fix=f"chmod 700 {_shell_path(root)}",
            )
        )
    else:
        out.append(_ok("state_dir", f"{root} (mode 0700)"))

    loose: list[str] = []
    present: list[str] = []
    modes: list[str] = []
    others: list[str] = []  # another account's (a sudo sync): only the whole folder's chown leaves none
    shut: list[str] = []  # this account's, with no read or no write for it (chmod 000, 0400)
    denied: list[Path] = []  # this account's at a mode that gives it access, or on a read-only volume
    nonfile: list[CheckResult] = []  # a folder, socket or fifo where a state file belongs
    lost_link = False  # a dangling manifest link over earlier passes: its own FAIL holds the lost fix (D71)
    other_link: Path | None = None  # another account's file reached through a link (E63)
    shut_paths: list[Path] = []  # the files of ``shut``, by their own path: a side may sit by a link's target
    loose_paths: list[Path] = []  # and of ``loose``
    sides = manifest_sides(expand(sp.db))  # where sqlite keeps them: beside a linked manifest's target (P42)
    unsearched: set[Path] = set()  # a target's folder outside the state folder that refuses a look inside
    for p in (sp.db, *sides, sp.token_cache_fallback):
        try:
            fst = p.lstat()
        except FileNotFoundError:
            continue
        except PermissionError as exc:
            if p in sides and Path(os.path.realpath(p.parent)) != Path(os.path.realpath(root)):
                # the linked manifest's folder, not the state folder: its own line, and the rest still read
                if p.parent not in unsearched:
                    unsearched.add(p.parent)
                    detail = f"cannot look inside {p.parent}: {exc.strerror or exc}"
                    nonfile.append(_bad("state_dir.files", detail, fix=state_folder_fix(p.parent)))
                continue
            # no search of the folder: no file in it can be judged (B13 round 20 X73)
            # a folder this account owns whose mode gives it access that still refuses it: an ACL, a sandbox;
            # at 0755 too, beside its chmod 700 line, which would leave it refused (B13 round 22 Z64)
            if st.st_uid == os.getuid() and mode & 0o700 == 0o700:
                out.append(
                    _bad(
                        "state_dir.files",
                        f"cannot look inside {root}: {exc.strerror or exc}",
                        fix=state_folder_fix(root),
                    )
                )
            return out  # else state_dir's owner or mode FAIL above is the answer, with its fix
        m = stat.S_IMODE(fst.st_mode)
        if p in sides and stat.S_ISLNK(fst.st_mode):
            # a -wal or -shm link: sqlite never opens a side through a link, so a link to nothing in a
            # writable folder or to a file stopped every sync while this line passed it, and a link's own
            # 0755 got a chmod 600 that follows the link and never clears (B13 round 26 E62, E46); beside a
            # manifest that is a link the sides are its target's, and nothing beside the link is judged (P42)
            link = link_kind(p) or "a symlink"
            nonfile.append(
                _bad("state_dir.files", _not_a_file_detail(sp, p, link), fix=_not_a_file_fix(sp, p, link))
            )
            continue
        target = dangling_target(p)
        if target is not None:  # by what a sync does through it, never an ACL (B13 round 24 B64)
            if dangling_into_unusable(p) is None:
                # the first sync creates the file it points at, as with no file there; over earlier passes
                # the lost manifest's FAIL below, whose moves work through the link
                continue
            name = "state_dir.manifest" if p == sp.db else "state_dir.files"
            detail = f"{expand(p)} is a symlink to {target}, {DANGLING_UNUSABLE}"
            fix = f"the person points the link at an existing file or removes it: rm {_shell_path(expand(p))}"
            if p == sp.db and manifest_lost(expand(sp.db)):
                # over earlier passes: one line naming both moves in order, since neither LOST_MANIFEST_FIX
                # move works through the link (a copy restored follows it into the missing folder, and with
                # heartbeat.json aside the sync stops on the link) and "rm" alone ends on the lost guard
                # (B13 round 25 D71); the generic lost line below is not printed beside it
                # the file's own fact in its own clause, as E19's line says it; every reader keys on the
                # ending (LOST_MANIFEST_WORDS) or on DANGLING_UNUSABLE, both kept (B13 round 28 I50)
                detail += f"; the manifest {lost_words(expand(sp.db)) or LOST_MANIFEST}"
                fix = (
                    f"the person removes the link first: rm {_shell_path(expand(p))}; then "
                    + LOST_MANIFEST_FIX.removeprefix("the person ")
                )
                fix = round28_first(expand(sp.root), fix)  # heartbeat.json's hold record first (C351)
                lost_link = True
            nonfile.append(_bad(name, detail, fix=fix))
            continue
        kind = state_file_kind(p)  # a link by what it points to (A46); a link to a file keeps its mode FAIL
        if kind is not None:
            # B13 round 21 Y47: a folder at 0700 read "[ok] manifest.sqlite (0700): this account's alone"
            # while every sync stopped on "cannot be opened: Is a directory", so status exited 0 with no
            # NEXT; judged before its owner and mode, and never probed
            name = "state_dir.manifest" if p == sp.db else "state_dir.files"
            nonfile.append(_bad(name, _not_a_file_detail(sp, p, kind), fix=_not_a_file_fix(sp, p, kind)))
            continue
        if stat.S_ISLNK(fst.st_mode):
            # a link to a file is judged by that file: a link's own mode (0755 under umask 022) means nothing
            # on macOS, and the chmod 600 its FAIL named follows the link, so the FAIL never cleared (B13
            # round 26 E63)
            try:
                fst = p.stat()
            except OSError:
                continue  # gone in between
            m = stat.S_IMODE(fst.st_mode)
        present.append(p.name)
        modes.append(f"{p.name} ({m:04o})")
        # B13 round 19 W64: a manifest this account cannot open read "are 0600" and no FAIL, while every
        # read of the state failed (sqlite's CANTOPEN, which state_dir.manifest does not judge)
        if fst.st_uid != os.getuid():
            others.append(f"{p.name} (owner uid {fst.st_uid}, mode {m:04o})")
            if p.is_symlink():  # the target may sit outside the state folder: its own fix (E63)
                other_link = p
        elif not os.access(p, os.R_OK | os.W_OK):
            # a read-only volume before the mode, as state_path_fix tests it: no chmod writes there (B13
            # round 22 Z78), so a 0400 file on one joins A85's one volume line (B13 round 24 B65)
            if m & 0o600 == 0o600 or _read_only(p):  # a chmod 600 would change nothing (B13 round 21)
                denied.append(expand(p))  # an ACL, or a read-only volume (its own words below)
            else:
                shut.append(f"{p.name} ({m:04o})")
                shut_paths.append(expand(p))
        elif m & 0o077:
            loose.append(f"{p.name} ({m:04o})")
            loose_paths.append(expand(p))
    out += nonfile
    # one state_dir.files line per side: a side that is not a file FAILs, and no ok line under the same name
    # says the rest are fine beside it (B13 round 25 D10: "[FAIL] ... -shm is a folder" then "[ok] ... are
    # 0600", one check counted twice); each FAIL keeps its own owner's fix, never merged
    files_failed = any(r.name == "state_dir.files" for r in nonfile)
    if others:
        out.append(
            _bad(
                "state_dir.files",
                f"owned by another account, so this one cannot open {_them(others, 'it', 'them')}: "
                f"{', '.join(others)}",
                fix=_persons_chown(root)
                if other_link is None
                else state_path_fix(Path(os.path.realpath(root)), Path(os.path.realpath(other_link))),
            )
        )
    elif shut:
        paths = " ".join(_shell_path(x) for x in shut_paths)  # a target's side by its own folder (P42)
        out.append(
            _bad(
                "state_dir.files",
                f"this account cannot read and write {_them(shut, 'it', 'them')}: {', '.join(shut)}",
                fix=f"chmod 600 {paths}",
            )
        )
    if not others:  # an ACL each its own line, the person's; a chmod above the agent's
        # every file on a read-only volume is one line with one fix, the volume's, as the chmod's line joins
        # its files (B13 round 23 A85: three lines gave the same fix three times)
        read_only = [p for p in denied if _read_only(p)]
        if read_only:
            named = ", ".join(f"{p.name} ({stat.S_IMODE(p.stat().st_mode):04o})" for p in read_only)
            detail = f"on a read-only volume, so this account cannot write {named}"
            out.append(_bad("state_dir.files", detail, fix=read_only_fix(read_only[0])))
        for p in denied:
            if p in read_only:
                continue
            m = stat.S_IMODE(p.stat().st_mode)  # a link's target's, as above (E63)
            detail = f"refused to this account by an ACL or a privacy setting: {p.name} ({m:04o})"
            out.append(_bad("state_dir.files", detail, fix=_search_fix(p, folder=False)))
    if others or shut or denied:
        pass  # the lines above are the answer, with their fixes
    elif loose:
        paths = " ".join(_shell_path(x) for x in loose_paths)
        out.append(
            _bad("state_dir.files", f"readable by others: {', '.join(loose)}", fix=f"chmod 600 {paths}")
        )
    elif files_failed:
        pass  # the side's FAIL above is the line (D10)
    elif present and len(modes) == sum(1 for x in modes if x.endswith("(0600)")):
        out.append(_ok("state_dir.files", f"{', '.join(present)} {_them(present, 'is', 'are')} 0600"))
    elif present:  # "are 0600" only when they are: 0700 is not (W64)
        out.append(_ok("state_dir.files", f"{', '.join(modes)}: this account's alone"))
    elif not nonfile and not manifest_lost(expand(sp.db)):
        out.append(_ok("state_dir.files", _no_manifest_yet(config)))
    if manifest_lost(expand(sp.db)) and not lost_link:  # whatever else is there: a stale -wal (A29)
        # a link to a file that does not exist, in a folder a sync can write, is named as one: "missing" read
        # false beside the manifest.sqlite ls lists (B13 round 26 E19); the detail still ends on
        # LOST_MANIFEST, which the listing note reads, and both moves of the fix work through such a link
        target = dangling_target(expand(sp.db))
        what = f"is a symlink to {target}, which does not exist; the manifest " if target is not None else ""
        lost = lost_words(expand(sp.db)) or LOST_MANIFEST  # an unread heartbeat says so (P111)
        fix = round28_first(expand(sp.root), lost_fix(lost))  # heartbeat.json's hold record first (C351)
        out.append(_bad("state_dir.manifest", f"{expand(sp.db)} {what}{lost}", fix=fix))
    # B13 round 20 X78: not over a manifest this account cannot write, which already FAILs: a read-only open
    # of a WAL database creates its -wal and -shm with the file's own mode (0400), so the one chmod the FAIL
    # named left two more to fix; after it, the next status probes and any side file is created 0600
    blocked = {n.split(" ")[0] for n in others + shut} | {p.name for p in denied}
    if sp.db.name in present and sp.db.name not in blocked:
        out += _check_manifest_readable(expand(sp.db))
    return out


# SQLite's result codes for a file that is no database (SQLITE_NOTADB) or a damaged one (SQLITE_CORRUPT); a
# locked, busy or unopenable one is not unreadable state, and a sync that holds it must not read as one.
MANIFEST_UNREADABLE_CODES = frozenset({26, 11})
MANIFEST_FIX = (
    "the person decides, with a copy of it kept: restore it from a copy they trust, or move it aside and "
    "heartbeat.json with it, so the next sync starts a new one, which converts every file again (online-only "
    "ones are downloaded again) and forgets the deletions, the deletion breaker's suspended removals and the "
    "archive it recorded; legal holds are kept, in governance/holds.json"
)
"""The person's fix for a manifest that is no database or a damaged one. "holds" said the manifest held the
legal holds, which live in governance/holds.json and survive a new manifest; what it loses is the deletion
breaker's state (B13 round 17 U73). heartbeat.json goes aside with it: no new manifest is started while the
heartbeat records earlier passes (``manifest.manifest_lost``, B13 round 23 A29), in install.sh's verb phrase
("move the folder aside and heartbeat.json with it"), since "with heartbeat.json beside it" read as where the
file is, not as a move (B13 round 25 D73)."""

LOST_MANIFEST_FIX = (
    "the person decides: restore manifest.sqlite from a copy they trust, or move heartbeat.json aside, "
    "keeping a copy, so the next sync starts a new manifest, which converts every file again (online-only "
    "ones are downloaded again) and forgets the deletions, the deletion breaker's suspended removals and the "
    "archive it recorded; legal holds are kept, in governance/holds.json"
)
"""The person's fix for a manifest lost from a Mac that has synced (``manifest.manifest_lost``): no sync
starts a new one over that history, so moving heartbeat.json aside is the person's confirmation (B13 round
23 A29: status said "no manifest yet" and "no sync has run", and the sync it pointed to quietly started over,
converting every file again and dropping the empty-folder alarm and the deletion hold)."""


LOST_UNREAD = (LOST_MANIFEST_UNREAD, LOST_MANIFEST_EMPTY_UNREAD)
"""The lost words of a manifest beside a heartbeat.json that cannot be read (P111; I92's twin)."""

HEARTBEAT_LOST_TAIL = (
    "no pass runs until the person decides on the lost manifest beside it (the state_dir.manifest [FAIL] "
    "line says how), and either of its moves replaces this file"
)
"""The heartbeat warn's tail beside a lost manifest it cannot rule earlier passes out for, never "the next
completed pass rewrites it": every sync refuses on the lost manifest until the person restores it or moves
heartbeat.json aside (B13 round 28 I62, I32)."""


def round28_first(state_dir: Path, fix: str) -> str:
    """``fix``, a move of heartbeat.json aside (:data:`LOST_MANIFEST_FIX`, :data:`MANIFEST_FIX`), led by one
    sync while heartbeat.json still keeps round 28's hold record (``governance.round28_pending``): the sync
    moves it to hold-record.json before it stops on the manifest, where the move aside alone took the record
    with it (B13 round 30 C351, C352; ruling 34). As the link's first step, so readers keyed on the ending
    still know the fix; the link's own "removes the link first" then drops its "first", which this step now
    is (B13 round 31 C543)."""
    from agentsync import governance  # noqa: PLC0415 - governance imports this module lazily

    try:
        pending = governance.round28_pending(state_dir)
    except Exception:  # the fix stands without it
        pending = False
    if not pending:
        return fix
    return (
        f"the person runs `{AGENTSYNC_BIN} sync` once first, which moves round 28's hold record from "
        "heartbeat.json to hold-record.json before it stops on the manifest; then "
        + fix.removeprefix("the person ").replace("removes the link first: ", "removes the link: ", 1)
    )


def lost_fix(lost: str) -> str:
    """The person's fix of a lost manifest by its words (``manifest.lost_words``): :data:`LOST_MANIFEST_FIX`,
    or for a manifest that is there but too short (``LOST_MANIFEST_SHORT``) the damaged manifest's
    :data:`MANIFEST_FIX`, which moves it aside with heartbeat.json: moving heartbeat.json alone left a 2 to
    99 byte file the next sync stops on as "file is not a database" (B13 round 28 I92)."""
    return MANIFEST_FIX if lost in LOST_MANIFEST_SHORT else LOST_MANIFEST_FIX


def unreadable_code(exc: sqlite3.Error) -> bool:
    """Whether ``exc`` is SQLITE_NOTADB or SQLITE_CORRUPT, an extended code (CORRUPT_VTAB, ...) included."""
    code = getattr(exc, "sqlite_errorcode", None)
    return isinstance(code, int) and code & 0xFF in MANIFEST_UNREADABLE_CODES


CONTENDED_CODES = frozenset({sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED})
"""SQLite's result codes for a database another connection holds now: no fault of the file or of the code."""
ENVIRONMENT_CODES = CONTENDED_CODES | {
    sqlite3.SQLITE_FULL,
    sqlite3.SQLITE_READONLY,
    sqlite3.SQLITE_IOERR,
    sqlite3.SQLITE_CANTOPEN,
    sqlite3.SQLITE_PERM,
}
"""The codes the Mac gives, not the code: held, a full disk, a read-only or failing volume, a file that will
not open or is not permitted. The CLI says them in one line; every other code keeps its traceback (B13 round
18 V59, an allowlist, so a code nobody listed still shows its stack)."""


def _code(exc: BaseException) -> int | None:
    code = getattr(exc, "sqlite_errorcode", None)
    return code & 0xFF if isinstance(code, int) else None


def contended(exc: BaseException) -> bool:
    """Whether ``exc`` is SQLITE_BUSY or SQLITE_LOCKED, an extended code (BUSY_SNAPSHOT, ...) included: the
    one test status's words and the setup report's make for a busy state file (B13 round 18 V60)."""
    return _code(exc) in CONTENDED_CODES


def environmental(exc: BaseException) -> bool:
    """Whether ``exc`` has one of :data:`ENVIRONMENT_CODES`, an extended code included (B13 round 18 V59)."""
    return _code(exc) in ENVIRONMENT_CODES


CANNOT_OPEN_CODES = frozenset({sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_PERM, sqlite3.SQLITE_READONLY})
"""SQLite's codes for a manifest this account cannot open or write: no damage, the file's owner or mode, or
its folder's (B13 round 19 W64). By name, once, for status's words and the setup report's: a hand-kept (8, 14,
23) held SQLITE_AUTH (23), which nothing here raises, for SQLITE_PERM (3; B13 round 20 X48)."""


def cannot_open(exc: BaseException) -> bool:
    """Whether ``exc`` has one of :data:`CANNOT_OPEN_CODES`, an extended code included (B13 round 20 X48)."""
    return _code(exc) in CANNOT_OPEN_CODES


def side_may_explain(exc: BaseException) -> bool:
    """Whether a sqlite ``exc`` is one a side file that is not a file gives: CANTOPEN or READONLY (a folder,
    a socket, a link to nothing), or IOERR (a fifo: IOERR_LOCK at the -shm, IOERR_WRITE at the -wal,
    measured by lane B's reviewer), so the stat (:func:`side_not_a_file_at`), never the code, decides (B13
    round 25 D2)."""
    return cannot_open(exc) or _code(exc) == sqlite3.SQLITE_IOERR


def _sql_name(name: object) -> str:
    """``name`` as a quoted SQL identifier, ``"`` doubled."""
    return '"' + str(name).replace('"', '""') + '"'


def manifest_unreadable(db: Path) -> sqlite3.DatabaseError | None:
    """The error when the manifest at ``db`` is no database or a damaged one, read through a read-only
    open that creates nothing (:func:`_probe_uri`; a plain ``mode=ro`` open of a WAL database made its -wal
    and -shm, with the file's own mode, so status does not probe a file this account cannot write: B13 round
    20 X78, and one setup report left both beside a clean manifest: round 31 C526) of its schema, which every
    open of the manifest reads first (a header alone can be intact over a damaged schema page), and of each
    b-tree's first entry: a table's row, ``SELECT * FROM "<table>" NOT INDEXED LIMIT 1``, one page per
    table, its root and the leaf the first row is on (B13 round 19 W41: damage under the cache or outputs
    table, which no status read touches, passed status while the sync failed; a plain ``SELECT 1``,
    ``rowid`` or ``count(*)`` is answered from an index and misses it), and an index's, ``SELECT 1 FROM
    "<table>" INDEXED BY "<index>" LIMIT 1``, as install.sh's step 1 reads them (B13 round 20 X50: damage
    under an index root passed status until a sync stopped on it). None when it reads, is missing, or is
    only locked, busy or not openable now. Damage deeper in a b-tree shows when a command reads it
    (``cli._state_readable``); status's own read of the loop's state is one such read, and its error FAILs
    through :func:`manifest_damaged` (B13 round 17 U64, U95), and a sync's is kept for status by
    :func:`record_unreadable`. A lost manifest (:func:`lost_words`: a 2 to 99 byte file beside earlier passes)
    is not probed: it is lost in every command, one state_dir.manifest FAIL and exit 1 with the lost words,
    never "file is not a database" and exit 65, and no -wal or -shm is made beside it (B13 round 29 C192,
    C203)."""
    if lost_words(db) is not None:
        return None
    real = Path(os.path.realpath(db))  # sqlite keeps the sides beside a link's target
    stamps: tuple[tuple[int, int] | None, ...] | None = None
    try:
        # a file this account cannot read and write is not opened: a read-only open would make its -wal and
        # -shm with its own mode (0400), and every caller, a sync's state check included, would leave them so
        # (B13 round 20 X78); state_dir.files FAILs it, and the damage check waits for the chmod
        if db.exists() and not os.access(db, os.R_OK | os.W_OK):
            return None
        uri = _probe_uri(db, real)
        if uri is None:
            return None
        if uri.endswith("&immutable=1"):
            stamps = _probe_stamps(real)
        conn = sqlite3.connect(uri, uri=True, timeout=0.5)
        try:
            trees = conn.execute(
                "SELECT type, name, tbl_name FROM sqlite_master"
                " WHERE rootpage > 0 AND type IN ('table', 'index')"
            ).fetchall()
            for kind, name, table in trees:
                quoted = _sql_name(name)
                read = (
                    f"SELECT * FROM {quoted} NOT INDEXED LIMIT 1"
                    if kind == "table"
                    else f"SELECT 1 FROM {_sql_name(table)} INDEXED BY {quoted} LIMIT 1"
                )
                try:
                    conn.execute(read).fetchone()
                except sqlite3.DatabaseError as exc:
                    if unreadable_code(exc) or environmental(exc):  # damaged; or held, so is every b-tree
                        raise
                    # a b-tree this read cannot take (a virtual table, an index a partial WHERE keeps the
                    # planner from using): the others are still read
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        if stamps is not None and _probe_stamps(real) != stamps:
            return None  # the file or its -wal changed under the immutable read: busy, never damage (C526)
        if unreadable_code(exc):
            return exc
    return None


def _probe_uri(db: Path, real: Path) -> str | None:
    """The read-only open :func:`manifest_unreadable` reads ``db`` (sides beside ``real``) through, one that
    creates no file beside it (B13 round 31 C526): with no -journal and no -wal or an empty one, and not both
    sides there, the file is the whole database, read ``immutable=1``, which takes no lock, so the caller
    stamps it before and after; with both sides there, a plain ``mode=ro``, which then makes nothing; with a
    -journal (an older build's rollback, which ``immutable=1`` would read as torn pages), a plain ``mode=ro``
    as before. A -wal with frames and no -shm, which any open makes, is not read: None."""

    def side(suffix: str) -> os.stat_result | None:
        try:
            return os.lstat(real.with_name(real.name + suffix))
        except OSError:
            return None

    plain = f"file:{quote(str(db))}?mode=ro"
    wal, shm, journal = side("-wal"), side("-shm"), side("-journal")
    if journal is not None or (wal is not None and shm is not None):
        return plain
    if wal is not None and not (stat.S_ISREG(wal.st_mode) and wal.st_size == 0):
        return None
    return plain + "&immutable=1"


def _probe_stamps(real: Path) -> tuple[tuple[int, int] | None, ...]:
    """(size, mtime_ns) of the manifest at ``real`` and of its -wal, None for one not there: what an immutable
    read is checked against (C526)."""

    def one(path: Path) -> tuple[int, int] | None:
        try:
            st = path.stat()
        except OSError:
            return None
        return st.st_size, st.st_mtime_ns

    return one(real), one(real.with_name(real.name + "-wal"))


_UNREADABLE_RECORD = "manifest-unreadable.json"
"""Beside the manifest: the error a command's read of it raised and exited 65 on, keyed to the file it read
(:func:`record_unreadable`)."""


def _manifest_key(db: Path) -> list[int]:
    st = db.stat()
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns]


def record_unreadable(db: Path, error: BaseException, command: str | None = None) -> None:
    """Keep ``error``, the SQLITE_NOTADB or SQLITE_CORRUPT a command's read of the manifest at ``db`` exited
    65 on, for status: damage deeper in a table than :func:`manifest_unreadable` reads shows only to the read
    that reaches it, so status said healthy after a sync stopped on it (B13 round 19 W41). Keyed to the
    file's device, inode, size and mtime, so a manifest moved aside, restored or written since drops it.
    ``command``: the subcommand whose read it was, which status names (B13 round 20 X54: it said "a sync" of
    a purge's read too). Best effort: a record that cannot be written leaves status as it was."""
    try:
        record = {"key": _manifest_key(db), "error": " ".join(str(error).split())[:300], "command": command}
        db.with_name(_UNREADABLE_RECORD).write_text(json.dumps(record), encoding="utf-8")
    except (OSError, ValueError):
        pass


def _recorded(db: Path) -> tuple[str, str | None] | None:
    """The error and the command :func:`record_unreadable` kept for the manifest at ``db`` while it is still
    the same file, else None; a record from an older build has no command."""
    try:
        record = json.loads(read_regular_text(db.with_name(_UNREADABLE_RECORD)) or "null")  # no fifo wait
        same = isinstance(record, dict) and record.get("key") == _manifest_key(db)
    except (OSError, ValueError):
        return None
    error = record.get("error") if same else None
    if not (isinstance(error, str) and error):
        return None
    command = record.get("command")
    return error, command if isinstance(command, str) and command else None


def recorded_unreadable(db: Path) -> str | None:
    """The error :func:`record_unreadable` kept for the manifest at ``db`` while it is still the same file,
    else None."""
    found = _recorded(db)
    return None if found is None else found[0]


def clear_stale_unreadable(db: Path) -> None:
    """Remove the record beside the manifest at ``db`` once it no longer matches the file (restored, moved
    aside, written since): a command that just read the manifest whole calls this, so the record's name
    does not say the manifest is unreadable beside one that reads (B13 round 20 X33). A record that still
    matches stays, as damage that command never read is still there. Never status's: status writes nothing,
    and a status that read an old record while a sync wrote a new one would remove the new one. Best
    effort."""
    record = db.with_name(_UNREADABLE_RECORD)
    try:
        if record.exists() and recorded_unreadable(db) is None:
            record.unlink(missing_ok=True)
    except OSError:
        pass


def _check_manifest_readable(db: Path) -> list[CheckResult]:
    """``state_dir.manifest``: a FAIL, the person's, when the manifest is no database or a damaged one. Every
    other check reads past it ("cannot read the state"), and every sync and add-source stops on it, so
    status exits 1 with the one check that says why (B13 round 16 T12). Nothing when its schema and each
    table's first row read: damage under a table that status's loop read hits FAILs through
    :func:`manifest_damaged`, and damage a sync stopped on, while the file is the same, through
    :func:`recorded_unreadable` (B13 round 19 W41)."""
    exc = manifest_unreadable(db)
    if exc is not None:
        return [manifest_damaged(db, exc)]
    found = _recorded(db)
    if found is None:
        return []
    recorded, command = found
    # the command that read it, never "a sync" for a purge's or curate's read (B13 round 20 X54)
    who = f"an agentsync {command}" if command else "an agentsync command"
    fix = round28_first(db.parent, MANIFEST_FIX)  # heartbeat.json's hold record first (C351)
    return [_bad("state_dir.manifest", f"{db} cannot be read: {recorded} ({who} read it)", fix=fix)]


def manifest_damaged(db: Path, exc: sqlite3.DatabaseError) -> CheckResult:
    """The ``state_dir.manifest`` FAIL for ``exc``, an error :func:`unreadable_code` accepts, read from the
    manifest at ``db``: by the schema probe, or by status's read of the loop's state, which reads a table the
    probe does not (B13 round 17 U64, U95: a damaged table page passed status with every check [ok])."""
    fix = round28_first(db.parent, MANIFEST_FIX)  # heartbeat.json's hold record first (C351)
    return _bad("state_dir.manifest", f"{db} cannot be read: {exc}", fix=fix)


# The first words of a [FAIL] fix that is the person's: a [[source]] path or sentinel, a folder or file to
# put back, a sign-in, or a folder to sync in its app (the source checks and _missing_source_fix write them);
# a deleted [[source]] block to put back (the config.vanished_sources check, cycle.VANISHED_FIX; B13 round
# 15 S7); a fix that starts "the person ": a [policy] or [governance] value (cli's POLICY_FIX and
# GOVERNANCE_FIX; round 16 T2, T9) or a state file that cannot be read (MANIFEST_FIX; T12); Rosetta to
# install, or the native pandoc copy they installed (the pandoc check; B13 round 15 S47, S53); a fix that
# starts with a [convert] pandoc_path edit, the configured or not-absolute pandoc's (round 17 U79, U80). The
# bundled pandoc's starts with the agent's install.sh re-run and stays the agent's: its first words decide.
# A re-run fails the same way and an edit would choose a folder for them. install.sh's persons_fails reads
# the same words from the [FAIL] lines with its own copy, since the installer runs the older installed build;
# a test pins the two equal (B13 round 14 Q1, Q9, Q33).
PERSONS_FIX_RE = re.compile(
    r"(fix path in \[\[source\]\]|point \[\[source\]\] |set path in the \[\[source\]\] |restore |sign in to "
    r"|/.* is (missing|empty) while (OneDrive-|another )|put the \[\[source\]\] block back "
    r"|the person |Rosetta is yours to install|the native pandoc copy at |set \[convert\] pandoc_path "
    r"|check or replace the pandoc at \[convert\] pandoc_path )"
)


# The kinds of fix PERSONS_FIX_RE reads, in the words of install.sh's PERSONS_FAILS: cli's rule 1 NEXT and
# the setup report's installer-output count name them; tests pin it to PERSONS_FAILS and to the report's own
# copy (B13 round 16 T5, T9; round 17 U11, U19, U45, U52, U86). "A state file that is damaged or another
# account's" since round 20 X26: one of this account's own that it cannot open is a chmod, the agent's, only
# when its own mode is why. Since round 22 (Z65, Z83, Z17, Z48; round 21's ownership rule, lane B's words) the
# kind also names a state file that is not a file (MANIFEST_FIX: a folder where the manifest belongs), the
# state folder, and the refusals no chmod fixes, each the person's: an ACL or a privacy setting (_search_fix),
# a folder above it that refuses a search (_parent_fix), a read-only volume (read_only_fix). Since round 29
# (ruling 27: C122, C197, C206) a hold the hold list lost, whose governance.hold FAIL's fix is the person's,
# and since round 30 (ruling 40: C311) a purge queue or suppression list that lost a purge the audit trail
# records, whose governance.purge_queue or governance.suppressed FAIL's is.
PERSONS_KINDS = (
    "a folder or file to put back or sync, a [[source]] path, a [[source]] block to put back, a [policy] or "
    "[governance] value, a docs-repo remote to remove or allow, a state file that is damaged, missing after "
    "earlier syncs, another account's or not a file, a hold the hold list lost, a purge queue or suppression "
    "list that lost a purge the audit trail records, a state folder that is not a folder, or a state file or "
    "folder that an ACL, a privacy setting, a folder above it or a read-only volume refuses, a sign-in, a "
    "[convert] pandoc_path, Rosetta to install or a native pandoc copy"
)


def is_persons_fix(fix: str | None) -> bool:
    """Whether a [FAIL]'s fix is the person's to make, not a step for the agent (``PERSONS_FIX_RE``)."""
    return bool(fix) and PERSONS_FIX_RE.match(fix or "") is not None


def _missing_source_fix(src: SourceConfig, root: Path) -> str:
    """The fix for a source whose folder does not exist, by what is missing (B13 round 11 M10): its provider
    folder (``~/Library/CloudStorage/<provider>``) gone with no other folder of that app beside it means the
    app is signed out, so the fix is the sign-in alone; with another folder of the app there (another
    account, a renamed organization) the sign-in is not clear-cut, so it keeps the path; a provider folder
    that is there means the person is signed in, so the subfolder is the path or a folder the app does not
    sync, unless it holds nothing but hidden entries, which is a stale one (signed out, or still setting up:
    B13 round 12 N61, the rule install.sh's list keys its sign-in exit on); a folder outside
    ~/Library/CloudStorage has no sync client to sign in to. A SharePoint library folder
    (``OneDrive-SharedLibraries-<org>``) missing beside its organization's ``OneDrive-<org>`` is a library not
    synced on this Mac, never a sign-in, since that account is signed in (B13 round 12 N53). The sign-in names
    the command to run after it (round 12 N56: "then re-run" named none). A library folder that is there but
    empty beside its organization's ``OneDrive-<org>`` is a library OneDrive has not synced or set up yet, no
    sign-in either (B13 round 13 O52: the empty-folder rule ran first). The provider folder is read within the
    listing's time bound, since a hidden Allow prompt holds that read too, and unread it keeps the path fix
    (B13 round 13 O47). No parenthesis: a FAIL line puts its fix in one. One rule for the listing, the volume
    and the launcher's probe."""
    fix_path = f'fix path in [[source]] id = "{src.id}"'
    if not is_cloud_path(root):
        return fix_path
    provider = cloud_provider_root(root)
    if provider is None:
        return f"{fix_path}, or sign in to the sync client"
    app = provider.name.split("-", 1)[0] or "the sync client"
    state = provider_state(provider)
    there = os.path.lexists(provider)
    if state == "library":
        org = org_of(provider.name)
        if there:
            return (
                f"{provider} is empty while OneDrive-{org} is there: sync the SharePoint library to this Mac "
                f"with Sync on its SharePoint page, or let OneDrive finish setting it up, or {fix_path}"
            )
        return (
            f"{provider} is missing while OneDrive-{org} is there: sync the SharePoint library to this Mac "
            f"with Sync on its SharePoint page, or {fix_path}"
        )
    if state == "signed-out" and there:  # a stale provider folder, as install.sh's list reads it (N61)
        return (
            f"sign in to {app} on this Mac or let it finish setting up, since {provider} is empty, "
            f"then run {BIN} status again"
        )
    # Renamed, moved or deleted there: names the put-back, as the wait does, after the path PERSONS_FIX_RE
    # keys on, so the fix stays the person's (B13 round 15 S4; round 16 T58)
    if state == "signed-in":
        return f"{fix_path}, or put the folder back under that name in {app}, or sync that folder in {app}"
    if state == "other-account":
        return (
            f"{provider} is missing while another {app} folder is there: sign in to {app} with the account "
            f"it belongs to, or {fix_path}"
        )
    return f"sign in to {app} on this Mac, since {provider} is missing, then run {BIN} status again"


ProviderState = Literal["signed-out", "other-account", "library", "signed-in"]


def provider_state(provider: Path, *, timeout: float | None = None) -> ProviderState:
    """What a ~/Library/CloudStorage provider folder (``provider``) says of its app's sign-in, by the rule
    install.sh's list keys its sign-in exit on (``signed_out``): ``signed-out`` for one gone with no other
    folder of its app beside it, or there but holding nothing but hidden entries (stale, or still setting up:
    B13 round 12 N61); ``other-account`` for one gone while another folder of its app is there;
    ``library`` for a ``OneDrive-SharedLibraries-<org>`` folder gone or empty beside its organization's
    ``OneDrive-<org>`` (a library not synced, never a sign-in: B13 round 12 N53, round 13 O52, checked
    before the empty-folder rule); ``signed-in`` otherwise. A folder that is unreadable, or whose listing
    does not answer within the listing's time bound (a hidden Allow prompt holds it, B13 round 13 O47), counts
    as signed in; the setup report passes a ``timeout`` within its own budget (None: the listing's bound, read
    at the call). One rule for doctor's fix and
    the report's sign-in stop bucket (B13 round 14 Q46, Q55)."""
    app = provider.name.split("-", 1)[0]
    org = org_of(provider.name) if provider.name.startswith("OneDrive-SharedLibraries-") else None
    signed_in_org = org is not None and os.path.lexists(provider.parent / f"OneDrive-{org}")
    if os.path.lexists(provider):
        shown: list[str] = ["?"]  # unreadable or held: not shown empty
        with contextlib.suppress(OSError, CallTimedOutError):
            shown = call_with_timeout(
                lambda: [p.name for p in provider.iterdir() if not p.name.startswith(".")],
                _LISTING_TIMEOUT_S if timeout is None else timeout,
                name="doctor-provider",
            )
        if shown:
            return "signed-in"
        return "library" if signed_in_org else "signed-out"
    if signed_in_org:
        return "library"
    others: list[str] = []
    with contextlib.suppress(OSError):
        others = [
            p.name
            for p in provider.parent.iterdir()
            if p.name != provider.name and p.name.split("-", 1)[0] == app
        ]
    return "other-account" if others else "signed-out"


def _check_local_source(config: Config, src: SourceConfig, image: Path) -> list[CheckResult]:
    """One local/inbox source: listable, sentinel present, volume UUID readable."""
    base = f"source.{src.id}"
    if not src.is_live:
        return [_ok(base, f"{src.state.value}: not checked")]
    if src.path is None:
        return [_bad(base, "no path configured", fix=f'set path in the [[source]] with id = "{src.id}"')]
    root = expand(src.path)
    cloud = is_cloud_path(root)

    # only once the listing has answered: the fix reads the provider folder, which a hidden Allow prompt
    # holds too (B13 round 13 O47: read first, it held status past the listing's bound); once, for the
    # listing and the volume
    @functools.cache
    def missing_fix() -> str:
        return _missing_source_fix(src, root)

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
                    fix=f"open {_shell_path(root)} in Finder once; if still empty: {_fda_fix(image)}"
                    if cloud
                    else None,
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
                # the command to run after, as the sign-in's (B13 round 12 N56)
                fix=f"click Allow on the macOS prompt, then run {BIN} status again; if no prompt shows: "
                f"{_fda_fix(image)}",
            )
        )
        return out
    except FileNotFoundError:
        out.append(_bad(f"{base}.listable", f"{root} does not exist", fix=missing_fix()))
    except NotADirectoryError:
        out.append(
            _bad(
                f"{base}.listable",
                f"{root} is not a directory",
                fix=f'point [[source]] id = "{src.id}" path at a folder',
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
            out.append(
                _bad(f"{base}.listable", f"{root}: permission denied", fix=f"chmod u+rx {_shell_path(root)}")
            )
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
                    fix=f'restore {target}, or change sentinel in [[source]] id = "{src.id}"',
                )
            )
        except PermissionError:
            out.append(_bad(f"{base}.sentinel", f"{target}: not permitted (TCC)", fix=_fda_fix(image)))
        except OSError as exc:
            # Any other answer (the path is a file, an I/O error, a sync app that stopped answering) is this
            # line's own: leaving the check with it cost the source its other lines and their fixes.
            out.append(_bad(f"{base}.sentinel", f"{target}: {exc.strerror or exc}", fix=look_fix))
    elif cloud:
        # add-source writes the sentinel as a commented recommendation, and the walk already holds back
        # deletions for a cloud folder it cannot list, or finds empty inside a File Provider domain (the
        # walk's own test, ``in_domain``: a missing provider folder counts as one): optional, so not a warn
        # to chase. agentsync is the subject: "a folder ... holds deletions" read as the folder holding them
        # (B13 round 5 F3), and an empty folder outside a domain holds nothing back (F50).
        provider = arm_local.cloud_provider_root(root)
        held = (
            "is empty or cannot be listed"
            if provider is None or arm_local._is_domain(provider)
            else ("cannot be listed")
        )
        out.append(
            _ok(
                f"{base}.sentinel",
                "no sentinel configured (optional: agentsync already holds back deletions under a cloud "
                f"folder that {held})",
            )
        )
    else:
        out.append(_ok(f"{base}.sentinel", "no sentinel configured"))

    # The value is the APFS volume's UUID (getattrlist ATTR_VOL_UUID of the mount point), for a File Provider
    # domain as for a plain folder: nothing in it is the domain's, so it is labelled as what it is.
    kind = "volume"
    if not listed and _is_missing(root):
        out.append(
            _bad(
                f"{base}.volume",
                f"{kind} UUID not checked: {root} is missing",
                fix=missing_fix(),
            )
        )
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
            # A cloud root always needs the signed launcher (launchd.launcher_required): with none found, the
            # LaunchAgents would run it, never this interpreter (B13 round 5 F2).
            subject = (
                (launchd.launcher_app(launcher) or launcher)
                if launcher is not None
                else "the signed launcher (agentsync-launcher)"
            )
            if not agents_wanted(config):
                # No LaunchAgent is installed or pending: none runs anything yet (F2).
                out.append(
                    _ok(
                        "tcc",
                        "cloud roots were listed with this terminal's privacy grants; background sync is not "
                        f"installed: if it is turned on, its LaunchAgents run {subject}, which needs its own "
                        "grants",
                    )
                )
            else:
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
        # the token cache is a file in the state folder: a folder this account cannot search says not
        # checked, as the lock's line does, and a cache file it cannot open gets that file's own fix, never
        # a logout, which stops on the same refusal (B13 round 21 Y42)
        root = expand(config.state_paths.root)
        if _under_the_non_folder(config, exc):  # the token cache under a state folder that is a file (A22)
            out.append(_bad("graph.auth", STATE_FOLDER_NOT_A_FOLDER, Severity.INFO))
            return out
        if isinstance(exc, OSError) and exc.filename is not None and state_unopenable(exc, root):
            unchecked = state_folder_unchecked(config)
            if unchecked is not None:
                out.append(_bad("graph.auth", unchecked, Severity.INFO))
            else:
                path = Path(os.path.realpath(os.fsdecode(exc.filename)))
                fix = state_path_fix(Path(os.path.realpath(root)), path)
                out.append(
                    _bad(
                        "graph.auth", f"cannot read the token cache: {exc.strerror or exc}", severity, fix=fix
                    )
                )
            return out
        out.append(
            _bad(
                "graph.auth",
                f"cannot read the token cache: {type(exc).__name__}: {exc}",
                severity,
                fix=f"{BIN} logout && {BIN} login",
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
                fix=f"run `{BIN} login` from a logged-in GUI session so the login Keychain is used",
            )
        )
    if status.signed_in:
        out.append(_ok("graph.signed_in", f"as {status.username or '<unknown user>'}"))
    else:
        needed = f" ({len(live_graph)} live Graph source(s): {', '.join(live_graph)})" if live_graph else ""
        out.append(_bad("graph.signed_in", f"no cached account{needed}", severity, fix=f"{BIN} login"))
    return out


def _check_disk(config: Config) -> list[CheckResult]:
    """At least 2 GiB free on the state and docs volumes. Measured at the nearest folder: a file where the
    folder or one above it belongs is on the same volume, so its free figure stands, but the line names the
    folder that holds it, never "free at" a path state_dir calls a file, not a folder (B13 round 24 B28;
    round 23 ruling 4 keeps disk.state measured)."""
    out: list[CheckResult] = []
    for name, path, judged in (
        ("disk.state", config.state_dir, "state_dir"),
        ("disk.docs", config.docs_repo, "docs_repo.git"),
    ):
        anchor = held = _nearest_existing(path)
        while not anchor.is_dir() and anchor != anchor.parent:
            anchor = anchor.parent
        free = _disk_free(anchor)
        if free >= _MIN_FREE_BYTES:
            at = f"{anchor}, the folder that holds {held}, which is not a folder (see {judged})"
            out.append(_ok(name, f"{_gib(free)} free at {at if held != anchor else anchor}"))
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
# Both name the installer, and the build script beside it, by the checkout path (B13 round 17 U30).
_LAUNCHER_FIX = (
    f"{INSTALL_SH} --confirm-install-agent (builds, signs and installs "
    "~/Applications/AgentSyncLauncher.app, then the LaunchAgents)"
)
_DEVELOPER_ID_FIX = (
    f"SIGN_IDENTITY='Developer ID Application: <Org> (<TEAMID>)' {Path(INSTALL_SH).parents[1]}/launcher/"
    f"build.sh, then {INSTALL_SH} --confirm-install-agent --launcher <built .app>"
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
                        f"click Allow on {prompt} while logged in, then run {BIN} status again "
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
                    fix=_missing_source_fix(src, root),
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
                    fix=f"{exe} --canary-only --canary {_shell_path(root)}",
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
            return _bad(name, f"{path} is not a readable plist", Severity.WARN, fix=f"{BIN} install-agent")
        return _bad(
            name, f"{spec.label} is not installed (no {path})", Severity.WARN, fix=f"{BIN} install-agent"
        )
    problems: list[tuple[Severity, str, str]] = []
    args = installed.get("ProgramArguments")
    program = args[0] if isinstance(args, list) and args and isinstance(args[0], str) else None
    if program is None or not os.access(program, os.X_OK):
        problems.append((Severity.ERROR, f"job program {program!r} is missing", f"{BIN} install-agent"))
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
                    f"{BIN} install-agent (from the environment agentsync now runs in)",
                )
            )
        elif "agentsync" in child and not _importable(interp):
            problems.append(
                (
                    Severity.ERROR,
                    f"job interpreter {interp} cannot import agentsync: every run fails",
                    f"{BIN} install-agent (from the environment agentsync now runs in)",
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
                    f"{BIN} install-agent" if wanted is not None and wanted[0] == pin else _LAUNCHER_FIX,
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
                f"{BIN} install-agent",
            )
        )
    if installed.get("MaterializeDatalessFiles") is not False:
        problems.append(
            (
                Severity.ERROR,
                "plist does not set MaterializeDatalessFiles=false (hydration not fail-closed)",
                f"{BIN} install-agent",
            )
        )
    expected = plistlib.loads(launchd.render_plist(spec))
    if dict(installed) != expected:
        diff = sorted(k for k in set(expected) | set(installed) if expected.get(k) != installed.get(k))
        problems.append(
            (
                Severity.WARN,
                f"installed plist differs from this config/interpreter ({', '.join(diff)})",
                f"{BIN} install-agent",
            )
        )
    loaded = _is_loaded(spec.label)
    if not loaded:
        problems.append(
            (
                Severity.WARN,
                "installed but not loaded",
                f"launchctl bootstrap gui/{os.getuid()} {_shell_path(path)}",
            )
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


def _check_gitleaks(config: Config) -> list[CheckResult]:
    """Whether a LaunchAgent run finds the gitleaks this process's PATH does (by the path a run from the
    person's shell records, launchd's PATH or the fixed places, ``lints.gitleaks_lookup``): WARN when it
    would not, so background syncs would scan with the builtin patterns alone; no line otherwise.  INFO
    with no fix when no LaunchAgent is installed (KISS K11a)."""
    found = shutil.which("gitleaks")
    if found is None or lints.gitleaks_lookup(path=launchd.LAUNCHD_PATH) is not None:
        return []
    result = _bad(
        "gitleaks",
        f"{found} is on this shell's PATH only: a background run would not find it and would scan for "
        "credentials with the builtin patterns alone",
        Severity.WARN,
        fix=f"{BIN} sync (a run from this shell records where gitleaks is)",
    )
    return [result if agents_wanted(config) else _optional(result)]


def _check_lock(config: Config) -> list[CheckResult]:
    """Who holds the single-writer lock, whether it is silent, and whether a crash left it stale."""
    path = config.state_paths.lock
    try:
        try:
            path.lstat()  # a link to nothing is there too; a refused search raises PermissionError, as before
        except FileNotFoundError:
            return [_ok("lock", "no lock file yet")]
        kind = own_kind(path)
        if kind is not None:
            # a folder, fifo, socket or any link: a sync opens the lock O_NOFOLLOW and flocks it, so each
            # ended on a traceback (EISDIR, ELOOP, EOPNOTSUPP) while this line said "free"; judged before
            # the probe, which opens a fifo fine, and before any read (B13 round 26 E38; ruling 13)
            return [lock_not_a_file(config, kind)]
        try:
            # the open a sync's acquire makes, less O_CREAT and the flock, so status creates and locks
            # nothing: a lock this account can read but not write passed, while every sync stopped on it
            # (B13 round 24 B58). Keyed on that open, not the owner: another account's writable lock runs
            os.close(os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW))
        except OSError as exc:
            if (refused := refused_state_path(config, "lock", exc)) is not None:
                return [refused]  # its mode, its owner or a read-only volume, as sync's error: state: line
        held = SingleWriterLock.is_held(path)
        body = SingleWriterLock.read_body(path).strip()
        desc = SingleWriterLock.describe(path)
    except PermissionError as exc:  # its mode or its owner, by its cause (B13 round 21 Y43)
        refused = refused_state_path(config, "lock", exc)
        if refused is None:
            raise
        return [refused]
    if held:
        info = LockInfo.parse(body)
        if info is None:
            # No cycle's record: an empty body (install.sh's reinstall, a probe, or a cycle before its body
            # write) or one that does not parse (a corrupt leftover under install.sh's lockf). A sidecar here
            # is a dead cycle's, so its pid and beat say nothing about this holder and none is read.
            return [_ok("lock", f"held by {desc}")]
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
        # only a sync mode's label is a cycle: exclude-empty and governance (a purge, uninstall's cleanup)
        # hold the same lock under their own label (B13 round 5 F71)
        running = "a cycle is running" if info.label in {m.value for m in CycleMode} else "not a cycle"
        return [_ok("lock", f"held by {desc} ({running})")]
    if body:
        return [
            _bad(
                "lock",
                f"free, but {desc} ended without releasing it; the next cycle breaks it and runs FULL passes",
                Severity.WARN,
                fix=_stale_lock_fix(config, path, body),
            )
        ]
    return [_ok("lock", "free")]


def _stale_lock_fix(config: Config, path: Path, body: str) -> str | None:
    """Where a dead holder's crash can be read, by the holder sidecar of the same pid: the background job's
    own err.log when it is there; nothing for a sync started in a terminal or by a command, whose output went
    to whatever started it; with no matching sidecar (an older build, a governance label), the log folder's
    err.logs only when there are any. Never a log that does not exist: after a killed interactive sync it
    sent the person to Library/Logs/agentsync/*.err.log, which was not there (B13 round 26 E12)."""
    info = LockInfo.parse(body)
    holder = SingleWriterLock.read_holder(path)
    same = info is not None and holder is not None and holder.get("pid") == info.pid
    started = holder.get("started_by") if same and holder is not None else None
    log_dir = expand(config.log_dir)
    if started == STARTED_BY_LAUNCHD and info is not None:
        # the config's prefix first, then the default a job installed before a prefix change still runs
        # under (launchd.started_by_our_job accepts both), so an old log never hides the current job's (B13
        # round 27 P50)
        for prefix in dict.fromkeys((config.launchd_label_prefix, launchd._DEFAULT_LABEL_PREFIX)):
            err = log_dir / f"{prefix}.{info.label}.err.log"
            if err.is_file():
                return f"read the crash in {err}"
        return None
    if started == STARTED_BY_OTHER:
        return None
    try:
        found = any(log_dir.glob("*.err.log"))
    except OSError:
        found = False
    return f"read the crash in {log_dir}/*.err.log" if found else None


def lock_not_a_file(config: Config, kind: str) -> CheckResult:
    """The lock's FAIL for something there that is not a file (``kind``, :func:`own_kind`'s words): the
    person's move aside, keeping a copy, after the step that opens a folder shut to this account, as the
    other state files say it (B13 round 26 E38; ruling 13)."""
    sp = config.state_paths
    return _bad("lock", _not_a_file_detail(sp, sp.lock, kind), fix=_not_a_file_fix(sp, sp.lock, kind))


def heartbeat_not_a_file(config: Config) -> CheckResult | None:
    """The heartbeat's FAIL for a real folder at heartbeat.json (by its own lstat), which no pass can write
    over (a rename of a file onto a folder fails EISDIR); None for anything a pass's rename replaces (a fifo,
    a socket, a link to a folder), whose warn's promise is true (B13 round 26 E39)."""
    sp = config.state_paths
    try:
        if not stat.S_ISDIR(expand(sp.heartbeat).lstat().st_mode):
            return None
    except OSError:
        return None
    return _bad(
        "heartbeat",
        f"{_not_a_file_detail(sp, sp.heartbeat, 'a folder')}; no pass can write over it",
        fix=_not_a_file_fix(sp, sp.heartbeat, "a folder"),
    )


def _check_shared_content(config: Config) -> list[CheckResult]:
    """An advisory, never a warn (§16.45 R144): live files that two non-inbox sources both hold, by content
    (the canonical hash), counted per pair. Neither copy is refused (only an inbox drop of a live Graph file
    is), since between two such sources no side is the real one; whether one source copies the other is the
    person's to settle. Nothing when no pair shares a file, before the first sync, or when the manifest
    cannot be read (the manifest checks report that). Over a state folder this account cannot search it says
    :data:`STATE_FOLDER_UNCHECKED`, as its siblings do, and only where it has two sources to compare; the
    look for the manifest is inside the ``try``, since on Python 3.11 ``Path.exists`` raises EACCES (B13 round
    21 Y11, Y26: a second, generic "could not read ... report it" FAIL beside state_dir's)."""
    db = config.state_paths.db
    ids = [s.id for s in config.live_sources() if s.kind is not SourceKind.INBOX]
    if len(ids) < 2:
        return []
    unchecked = state_folder_unchecked(config)
    if unchecked is not None:
        return [_bad("sources.shared_content", unchecked, Severity.INFO)]
    try:
        if not db.exists():
            return []
        with Manifest(db) as manifest:
            pairs = manifest.shared_canonical_counts(ids)
    except (ManifestNotCurrentError, ManifestBusyError) as exc:  # under manifest.read_only_reads (C560)
        return [_ok("sources.shared_content", read_note(exc))]
    except (OSError, sqlite3.Error, AgentSyncError):
        return []
    if not pairs:
        return []
    shown = "; ".join(
        f"{plural(n, 'file')} in {a} {'has' if n == 1 else 'have'} the same content as a file in {b}"
        for a, b, n in pairs
    )
    return [
        _ok(
            "sources.shared_content",
            f"{shown}: both are kept; if one source copies the other, give its [[source]] block in "
            f"sources.toml {RETIRE_ADVICE}",
        )
    ]


def _check_vanished_sources(config: Config) -> list[CheckResult]:
    """A source still mirrored whose [[source]] block is gone from sources.toml: every sync refuses the config
    (exit 78) for it, while status and doctor said nothing, so status's NEXT sent the agent to that sync and
    install.sh's step 2 ran into it (B13 round 15 S7). One [FAIL] in the sync's own words and fix
    (``cycle.vanished_sources``, by ``config.sources``: a block just set to retired is still there, and its
    retiring sync must run). Nothing before the first sync, or when the manifest cannot be read (the manifest
    checks report that)."""
    db = config.state_paths.db
    if not db.exists():
        return []
    try:
        with Manifest(db) as manifest:
            vanished = vanished_sources(config, manifest)
            back = _vanished_add_backs(config, manifest, vanished)
    except (ManifestNotCurrentError, ManifestBusyError) as exc:  # under manifest.read_only_reads (C560)
        return [_ok("config.vanished_sources", read_note(exc))]
    except (OSError, sqlite3.Error, AgentSyncError):
        return []
    if not vanished:
        return []
    return [
        _bad(
            "config.vanished_sources",
            f"{vanished_detail(vanished)}, so every sync refuses this config (exit 78)",
            Severity.ERROR,
            fix=vanished_fix(back, every=len(back) == len(vanished)),
        )
    ]


def _vanished_add_backs(config: Config, manifest: Manifest, vanished: Collection[str]) -> list[Path]:
    """``cycle.vanished_add_backs`` read through ``manifest``."""
    return vanished_add_backs(config, manifest.get_meta, vanished)


def vanished_fix(roots: Sequence[Path], *, every: bool) -> str:
    """Doctor's config.vanished_sources fix: :data:`cycle.VANISHED_FIX`, which starts with the person's words
    (``PERSONS_FIX_RE``), then, for each folder ``cycle.vanished_add_backs`` found, the command that adds it
    back under the same id (``cycle.vanished_add_back_tail``, the tail the sync's refusal and the setup report
    end with too; B13 round 17 U9, U25, U46, round 18 V7). ``every``: every vanished source has one."""
    return f"{VANISHED_FIX}{vanished_add_back_tail(roots, every=every)}"


def _check_heartbeat(
    config: Config,
    *,
    loop_checks: Collection[str] | None = None,
    loop_waits: Mapping[str, int] | None = None,
) -> list[CheckResult]:
    """Per live source: auth state, last success within 3 x cadence, enumeration completing.

    ``loop_checks`` (``loop.NextStep.checks``, which status passes) are the checks a WAITING ON YOU line
    explains: an incomplete listing points at that line only when its ``heartbeat.<id>`` is among them, and
    otherwise says to sync again. ``None`` (no loop state read): a local source points at the line. Under
    ``NO_NEXT_HINT_ENV`` (install.sh) every incomplete local source reads ``_LISTING_YOURS_NOTE`` with no
    fix: the run's own sync comes next (B13 round 8 J12). So does an inbox on status's path (``loop_checks``
    given), named by a wait or not, since an inbox pass is always full (B13 round 9 K75). Without
    ``loop_checks`` (the setup report), only an inbox held by empty cloud folders reads it; any other inbox
    keeps ``sync -v`` (B13 round 10 L49)."""
    path = config.state_paths.heartbeat
    try:
        beats: Mapping[str, Mapping[str, object]] = read_heartbeat(path)
    except ValueError as exc:
        folder = heartbeat_not_a_file(config)  # no pass replaces a folder: never the warn's promise (E39)
        if folder is None and lost_words(expand(config.state_paths.db)) in LOST_UNREAD:
            # this heartbeat is why the manifest reads lost: no pass runs until the person decides, so never
            # the promise of one; no fix of its own, so NEXT stays on the manifest's (B13 round 28 I62, I32)
            return [_bad("heartbeat", f"{exc}; {HEARTBEAT_LOST_TAIL}", Severity.WARN)]
        return [folder or _bad("heartbeat", f"{exc}; the next completed pass rewrites it", Severity.WARN)]
    except PermissionError as exc:  # its mode or its owner, by its cause (B13 round 21 Y43)
        # a 0000 folder refuses the open before any kind is read: judged by its lstat, the person's move
        # aside with its chmod first, never the chmod alone, after which the next sync still stops (P135)
        folder = heartbeat_not_a_file(config)
        if folder is not None:
            return [folder]
        refused = refused_state_path(config, "heartbeat", exc)
        if refused is None:
            raise
        return [refused]
    hb = expand(path)
    if hb.is_symlink() and not hb.exists() and lost_words(expand(config.state_paths.db)) in LOST_UNREAD:
        # a link to nothing reads as no record, but it is why the manifest reads lost: never "no completed
        # pass recorded yet" per source under a FAIL saying earlier passes cannot be ruled out; the damaged
        # file's warn, with no fix, so NEXT stays on the manifest's (B13 round 30 C310). Beside a good
        # manifest a pass's rename replaces the link, so it keeps the per-source lines.
        return [
            _bad(
                "heartbeat",
                f"{hb}: a link to a file that does not exist; {HEARTBEAT_LOST_TAIL}",
                Severity.WARN,
            )
        ]
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
                    fix=f"{BIN} login",
                )
            )
            continue
        limit = _STALE_FACTOR * max(src.cadence_s, config.poll_interval_s)
        last = entry.get("last_success_at")
        age = _age_s(last, now)
        incomplete = entry.get("consecutive_incomplete")
        if age is None:
            out.append(_bad(name, "no successful pass yet", Severity.WARN, fix=f"{BIN} sync -v"))
        elif age > limit:
            out.append(
                _bad(
                    name,
                    f"last success {last} ({age}s ago) > {_STALE_FACTOR} x cadence ({limit}s)",
                    Severity.WARN,
                    fix=f"{BIN} sync -v",
                )
            )
        elif isinstance(incomplete, int) and incomplete >= _INCOMPLETE_RUNS:
            # one bracket on the line, the fix's or the note's: what it holds follows a "; " (B13 round 11
            # M44's second half, built in round 12 N17, N34, N74; install.sh's cleared_listings finds the line
            # by "deletions held", with or without the bracket an older build printed)
            detail = f"{LISTING_INCOMPLETE}{incomplete} consecutive passes; {_held(src)}"
            # status passes the loop's own checks (R151): a wait the loop prints decides. Without them (the
            # setup report), an inbox held by empty cloud folders gets the loop's pointer too, read from the
            # manifest's stored state (B13 round 3 R53).
            if loop_checks is not None:
                yours = name in loop_checks
            else:
                yours = src.kind is SourceKind.LOCAL or (
                    src.kind is SourceKind.INBOX and _empty_folders_held(config, src)
                )
            # Under install.sh a local source no wait names gets the note too: its "sync; another full walk"
            # fix is the walk install's own sync runs next, and the note stays true if that sync is skipped
            # (lock busy, a FAIL) (B13 round 8 J12). So does an inbox on status's path (loop_checks given):
            # an inbox pass is always full, so install's step-6 sync is that walk too. The setup report
            # (no loop_checks) runs after that sync, so an inbox there keeps ``sync -v``, which names a
            # cause no plain sync clears (B13 round 9 K75).
            in_install = os.environ.get(NO_NEXT_HINT_ENV, "").strip() == "1"
            walked_next = src.kind is SourceKind.LOCAL or (
                src.kind is SourceKind.INBOX and loop_checks is not None
            )
            if in_install and (yours or walked_next):
                out.append(CheckResult(name, False, detail, Severity.WARN, note=_LISTING_YOURS_NOTE))
            else:
                waits = None if loop_waits is None else loop_waits.get(src.id)
                out.append(
                    _bad(name, detail, Severity.WARN, fix=_incomplete_fix(src, yours=yours, waits=waits))
                )
        else:
            detail = f"last success {last} ({age}s ago, {entry.get('pass_kind')})"
            # Below the threshold the line stays ok (one incomplete pass is normal for an inbox while a file
            # is still being written), but it says what the heartbeat holds: an incomplete pass sets
            # last_success_at too, and the loop may already print a WAITING ON YOU line about this source.
            if isinstance(incomplete, int) and incomplete > 0:
                passes = plural(incomplete, "pass", "passes")
                detail += f"; listing incomplete for the last {passes}, {_held(src)}, "
                detail += f"a warn after {_INCOMPLETE_RUNS}"
            out.append(_ok(name, detail))
    return out


def _held(src: SourceConfig) -> str:
    """What an incomplete listing holds back, by the source's kind (B13 round 5 F69). A local or inbox
    source's only removals are absence-based, so its deletions wait for a complete listing; a Graph source's
    provider tombstones apply on any pass, and only a file missing from the listing is kept."""
    if src.kind.is_graph:
        return "files missing from the listing are kept while deletions Graph reports still apply"
    return "deletions held"


def _empty_folders_held(config: Config, src: SourceConfig) -> bool:
    """:func:`agentsync.loop.empty_cloud_folders_held` from the manifest's stored state (no folder is listed);
    False when there is no manifest or it cannot be read. The manifest is opened as status's ``next_step``
    opens it, so an older schema is migrated here as there (under ``manifest.read_only_reads``, the setup
    report's, it is not read: False; C560); only an inbox incomplete for ``_INCOMPLETE_RUNS`` passes opens
    it."""
    db = config.state_paths.db
    if not db.exists():
        return False
    try:
        with Manifest(db) as manifest:
            return empty_cloud_folders_held(manifest, src)
    except Exception:  # the check is extra: an unreadable manifest keeps the sync-again fix
        return False


def _incomplete_fix(src: SourceConfig, *, yours: bool | None = None, waits: int | None = None) -> str:
    """The fix for a source whose listing stays incomplete. ``yours`` (default: a local source): a WAITING ON
    YOU line explains it. A local folder's walk is always a full pass, so the same folder stops it again: each
    such line about the source says which case it is and what to do (they count the empty cloud folders,
    name ``exclude-empty``, and point at the docs repo's STATE.md, which keeps the sync's alarm line with the
    folder names; the alarm names ``sync -v`` only past its first five: B13 round 6 G13, G42; round 7 H12,
    H35, H51). No command and no "above": status prints the lines, and the setup
    report's Doctor section may have none above. Otherwise a Graph pass resumes, and an inbox is most often
    incomplete while a file in it is still being written, so another sync clears those; ``sync -v`` names any
    other cause. A local source with no such line either changed since its last walk (its stopping folders are
    excluded now) or had no full walk yet: the next full walk lists it (under install.sh it reads
    ``_LISTING_YOURS_NOTE`` instead, since install's own sync is that walk: B13 round 8 J12; on status's path
    an inbox does too, since an inbox pass is always full: round 9 K75).
    ``waits``: how many WAITING ON YOU lines name the source, when status knows
    (``loop.NextStep.wait_counts``): "the line" only for exactly one, as the sync's alarm says it (B13 round
    6 G44); unknown (the setup report, which may show none) and more than one keep "each", true for any
    count.
    No nested parentheses: the setup report's fix parser strips one level."""
    if yours is None:
        yours = src.kind is SourceKind.LOCAL
    if yours:
        which = "the WAITING ON YOU line" if waits == 1 else "each WAITING ON YOU line"
        return (
            f"{which} about {src.id} says what stops the listing and what to do: another sync does not "
            "clear it"
        )
    if src.kind is SourceKind.LOCAL:
        return f"{BIN} sync; another full walk lists it"
    # A Graph source keeps sync -v too (B13 round 6 G54): since F69 a delta resets the count, so the warn
    # takes three incomplete FULL passes in a row, and those come only with no cursor kept or a drive not
    # baselined, where a plain sync is itself FULL; otherwise a plain sync runs a delta, which clears the
    # count. reconcile is only an explicit FULL, and the loop says sync again for the same source.
    # prose after "; ", as the local fix's, never in a bracket: the line puts the fix in one (B13 round
    # 13 O51)
    return f"{BIN} sync -v; a full pass that lists all of {src.id} clears this"


def _check_logs(config: Config) -> list[CheckResult]:
    """launchd never rotates StandardOut/ErrorPath: warn before a log grows without bound."""
    log_dir = expand(config.log_dir)
    unchecked = _in_shut_state_folder(config, log_dir)
    if unchecked is not None:  # its state_dir FAIL is the answer (B13 round 22 Z60)
        return [_bad("logs", unchecked, Severity.INFO)]
    if not log_dir.is_dir():
        return [_ok("logs", f"{log_dir} does not exist yet")]
    files = sorted(p for p in log_dir.glob("*.log") if p.is_file())
    big = [(p, p.stat().st_size) for p in files if p.stat().st_size > _MAX_LOG_BYTES]
    if big:
        names = ", ".join(f"{p.name} ({s // 1024**2} MiB)" for p, s in big)
        return [
            _bad(
                "logs",
                f"large logs: {names}",
                Severity.WARN,
                fix="; ".join(f": > {_shell_path(p)}" for p, _ in big),
            )
        ]
    return [_ok("logs", f"{plural(len(files), 'log file')} in {log_dir}")]


_PERM_SAMPLE = 500  # files per tree whose mode is checked (a bounded walk, newest pages first is not needed)


def _group_other_readable(root: Path, own_roots: frozenset[Path] = frozenset()) -> list[Path]:
    """``root`` and up to _PERM_SAMPLE entries below it with any group/other permission bit set.  A folder
    in ``own_roots`` is walked as a root of its own, so it is neither counted nor entered here: in a docs
    repo ``.git/objects`` and ``mirror/`` would spend the sample before an agent's folders are reached
    (CONTRACTS §16.39 N30)."""
    bad: list[Path] = []
    if not root.exists() or root.is_symlink():
        return bad
    if root.stat().st_mode & 0o077:
        bad.append(root)
    if not root.is_dir():
        return bad
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if Path(dirpath) / d not in own_roots]
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
    names the sync that clears the paths found, or a chmod when a sync would leave one of them.

    A cache or log folder that is the home folder or holds it (the sync's own rule, ``cycle._holds_home``) is
    not agentsync's alone: it is neither walked nor named in a chmod, and gets a FAIL of its own,
    ``config.<key>``, whose fix is to give the key a folder of its own (§16.45 R79)."""
    repo = expand(config.docs_repo)
    out: list[CheckResult] = []
    roots = [repo, repo / ".git" / "objects", repo / "mirror"]
    for key, folder, default in (
        ("cache_dir", expand(config.cache_dir), default_cache_dir()),
        ("log_dir", expand(config.log_dir), default_log_dir()),
    ):
        if _in_shut_state_folder(config, folder) is not None:  # no walk enters it; state_dir says why (Z60)
            continue
        if not _holds_home(folder):
            roots.append(folder)
            continue
        out.append(
            _bad(
                f"config.{key}",
                f"{key} is the home folder or a folder that holds it, so agentsync leaves its modes alone",
                fix=(
                    f"set {key} in {_shell_path(config.config_path)} to a folder of its own, such as "
                    f"{_shell_path(default)}"
                ),
            )
        )
    ctx = expand(config.config_path).parent
    if ctx != Path.home() and ctx in repo.parents:
        roots.insert(0, ctx)
    bad: list[Path] = []
    for root in roots:
        if root == ctx:
            if ctx.is_dir() and ctx.stat().st_mode & 0o077:
                bad.append(ctx)
            continue
        # The context folder has no walk of its own (only its mode is read above), so a walk that holds it
        # enters it: left out, a cache or log folder above it would hide everything beside the docs repo.
        bad += _group_other_readable(root, frozenset(r for r in roots if r != ctx and root in r.parents))
    if bad:
        shown = ", ".join(str(p) for p in bad[:4]) + (f" (+{len(bad) - 4} more)" if len(bad) > 4 else "")
        targets = " ".join(_shell_path(r) for r in roots if r.exists())
        # A sync makes agentsync's own paths owner-only, and a setup agent may run it; the chmod is the
        # person's, for a path a sync leaves (in mirror/ or .git, or another user's).
        fix: str | None = f"run {BIN} sync, which makes these owner-only"
        if _sync_leaves(config, bad):
            fix = (
                f"chmod -R go-rwx {targets}; git -C {_shell_path(repo)} config core.sharedRepository 0600"
                if targets
                else None
            )
        return [*out, _bad("docs_repo.permissions", f"group/other can read tenant data: {shown}", fix=fix)]
    return [*out, _ok("docs_repo.permissions", "docs repo, cache and logs are owner-only")]


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
    ("sources.shared_content", _check_shared_content),
    ("config.vanished_sources", _check_vanished_sources),
    ("materialise", _check_materialise),
    ("graph", _check_graph),
    ("disk", _check_disk),
    ("launcher", _check_launcher),
    ("tcc", _check_tcc_access),
    ("launchd", _check_launchd),
    ("gitleaks", _check_gitleaks),
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


def run_checks(
    config: Config,
    *,
    tcc_canary: bool = True,
    loop_checks: Collection[str] | None = None,
    loop_waits: Mapping[str, int] | None = None,
) -> list[CheckResult]:
    """Run every check, in a fixed order, never raising for a single failed check.

    python >= 3.11; git absolute path; pandoc (configured or bundled) runs and reports a version; on-device
    OCR and the media helper each ready, off, not built (INFO) or failed (WARN), never built here and never a
    FAIL; docs_repo
    outside CloudStorage, a git repo (or creatable), no symlinks; state_dir exists with mode 0700 and the db
    0600; each local/inbox source root is listable (EPERM => "grant Full Disk Access to <interpreter>"),
    sentinel present, volume UUID readable; materialisation policy readable; graph:
    client id set, token cache backend (Keychain vs file), signed in (no network); disk free >= 2 GiB on state
    and docs volumes; launchd agents loaded (WARN if not; INFO when none is installed, KISS K11a).

    Ops extension (C15 §3): the signed launcher's presence, signature, identifier and designated requirement
    (WARN when ad hoc), and per TCC-protected source a timed metadata-only canary run *as the launcher*.
    ``tcc_canary=False`` (KISS K08a: ``status`` decides when it is due) replaces the canary with one
    ``tcc.canary`` info line. ``loop_checks`` (``loop.NextStep.checks``; ``None``: not read) go to the
    heartbeat check, which points an incomplete listing at WAITING ON YOU only when the loop has that wait,
    with ``loop_waits`` (``loop.NextStep.wait_counts``) saying how many lines.
    """
    results: list[CheckResult] = []
    unchecked = state_folder_unchecked(config)  # shut (X73), or not a folder (round 23 ruling 4)
    for group, check in _CHECKS:
        saw_group(group)  # the setup report names the check a doctor it cut off was in (Y22)
        if unchecked is not None and group in _STATE_FOLDER_GROUPS:  # each would stop on the folder (X73)
            results.append(_bad(group, unchecked, Severity.INFO))
            continue
        run = _tcc_canary_skipped if group == "tcc" and not tcc_canary else check
        if group == "heartbeat" and loop_checks is not None:
            run = functools.partial(_check_heartbeat, loop_checks=loop_checks, loop_waits=loop_waits)
        try:
            results.extend(run(config))
        except Exception as exc:
            results.append(stopped(config, group, exc))
    return results


_group_holder = threading.local()
"""Per thread, ``seen``: told each check's group as :func:`run_checks` starts it, while :func:`groups_seen`
holds it (B13 round 21 Y22: a doctor the setup report cut off named no check, so a hung one read the same as
a busy Mac). Per thread, as the hook sets and reads it in the doctor's own thread (B13 round 26 E37: a module
global let another report's abandoned doctor thread reach this one's callback, or leave its own held)."""


def saw_group(group: str) -> None:
    """Tell :func:`groups_seen`'s callback, if this thread holds one, that the check ``group`` starts (never
    raises)."""
    seen = getattr(_group_holder, "seen", None)
    if seen is not None:
        with contextlib.suppress(Exception):
            seen(group)


@contextlib.contextmanager
def groups_seen(seen: Callable[[str], None]) -> Iterator[None]:
    """:func:`run_checks` tells ``seen`` each check's group before it runs it, inside the ``with``, in this
    thread."""
    before = getattr(_group_holder, "seen", None)
    _group_holder.seen = seen
    try:
        yield
    finally:
        _group_holder.seen = before


_STATE_FOLDER_GROUPS = frozenset({"config.vanished_sources", "lock", "heartbeat"})
"""The checks that read files in the state folder (the manifest, the lock, the heartbeat): over a folder this
account cannot search each says :data:`STATE_FOLDER_UNCHECKED` (B13 round 20 X73), and over one that is not a
folder :data:`STATE_FOLDER_NOT_A_FOLDER` (round 23 ruling 4). Three more read it and say
the same line from inside their own check, so the rest of each still runs: graph's ``graph.auth`` (the token
cache; graph.client_id and graph.config do not read the folder), ``sources.shared_content`` (only with two
sources to compare) and status's governance lines (B13 round 21 Y11, Y26, Y42, Y44, Y51)."""

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
