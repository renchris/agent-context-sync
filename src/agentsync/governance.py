"""Retention, purge, legal hold, history compaction, remote policy and offboarding (owner: governance).

C15 section 7 and requirements 36-42.  The docs repo is a plaintext copy of tenant data OUTSIDE Purview
retention, hold, eDiscovery and DLP, so this module owns every way that copy is bounded or destroyed:

* :func:`purge` removes the selected items' pages, sidecars, manifest rows, converter-cache entries, the
  piece store folder of a purged recording, tombstone data and log lines that name them, then rewrites
  docs-repo history with git plumbing only (``cat-file``, ``hash-object``, ``update-ref``), expires every
  reflog, runs ``gc --prune=now`` and verifies with ``cat-file --batch-check`` that no targeted blob
  survives.  Index-like generated files (``_manifest/*.jsonl``, ``CHANGELOG``, ``INDEX.md``,
  ``DEPENDS.tsv``, ``_sync/*``) are rewritten in every
  commit with the lines that name a purged item removed (a docs path that another item's live page holds in
  HEAD is not one of those names).  Surviving tombstones of OTHER items have their
  ``git show <sha>`` hints remapped to the rewritten commits so their recovery still works.
* :func:`compact_history` squashes history older than ``[governance] history_days`` into one root snapshot,
  then expires reflogs and prunes, with the same verification.
* Legal hold: ``[governance] hold = true`` (with ``hold_reason``) or a scoped hold set by the records/legal
  owner with :func:`set_hold` suspends purge and compaction and says why (:func:`hold_state_lines` for
  STATE.md).
* Remote policy: no remote by default; ``allow_remote = true`` needs ``remote_url_prefixes`` naming a
  tenant-owned host.  :func:`push_rewritten` never pushes otherwise.
* :func:`offboard` lists (dry run, the default) or removes every local copy: LaunchAgents, Keychain items,
  converter cache, logs, state dir, and with ``purge_data`` the docs repo and sources.toml.  Destructive only
  with ``confirm`` equal to the docs repo path.

Every destructive action appends one JSON line per item to ``<state_dir>/governance/audit.jsonl`` with the
source id, sha256 hashes of the stable id and paths, the time and the reason, never content.
"""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import errno
import hashlib
import json
import logging
import os
import posixpath
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import tomllib
import unicodedata
import urllib.parse
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC as _UTC
from datetime import datetime, timedelta
from pathlib import Path
from typing import IO as _IO
from typing import Any

from agentsync import gitops
from agentsync.config import SOURCE_ID_RE as _SOURCE_ID_RE
from agentsync.config import Config
from agentsync.convert.pieces import PieceStore
from agentsync.errors import AgentSyncError, ConfigError
from agentsync.manifest import Manifest, manifest_lost, migration_backups
from agentsync.model import SourceKind
from agentsync.ops import launchd
from agentsync.ops.lock import HOLD_RECORD_KEY as _HOLD_RECORD_KEY
from agentsync.ops.lock import SingleWriterLock, read_regular_text
from agentsync.paths import StatePaths, expand, glob_match, is_cloud_path, is_under
from agentsync.policy import POLICY_FILE_NAME
from agentsync.skill import AGENTSYNC_BIN
from agentsync.text import plural
from agentsync.tm_exclude import TM_EXCLUDE_XATTR
from agentsync.tm_exclude import set_exclusion as _set_tm_exclusion
from agentsync.tm_exclude import xattr_state as _xattr_state

log = logging.getLogger(__name__)

KEYCHAIN_SERVICE = "agentsync"
"""Keychain generic-password service every agentsync token cache item uses (= graph.auth.KEYCHAIN_SERVICE)."""

HOLD_ALL = "all"
"""Hold scope that covers every source (and therefore suspends compaction as well as every purge)."""

DEFAULT_HISTORY_DAYS = 30

_GOVERNANCE_KEYS = frozenset(
    {
        "history_days",
        "compaction_slack_days",
        "allow_remote",
        "remote_url_prefixes",
        "hold",
        "hold_reason",
        "hold_owner",
        "purge_on_upstream_delete",
        "archive",
    }
)
_SCRUBBED_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
    "GIT_COMMON_DIR",
)
_GIT_TIMEOUT_S = 3600.0
_SCRUB_MAX_BYTES = 64 * 1024 * 1024
_TREE_MODE = b"40000"
_GITLINK_MODE = b"160000"
_SIDECAR_SUFFIX = ".files"
_PAGE_TOPS = frozenset({"mirror", "archive"})  # docs-repo dirs whose pages carry an item's identity
_PURGED_MARK = "[purged]"
_PATH_CHARS = r"A-Za-z0-9._/\-"
_SIG_MARKERS = (b"-----BEGIN PGP SIGNATURE-----", b"-----BEGIN SSH SIGNATURE-----")
_DROP_HEADERS = frozenset({b"gpgsig", b"gpgsig-sha256", b"mergetag"})
_FM_KEYS = frozenset(
    {
        "source_id",
        "stable_id",
        "source_path",
        "status",
        "canonical_sha256",
        "rendered_sha256",
        "last_rendered_sha256",
    }
)
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_LOCK_LABEL = "governance"
_SECURITY = "/usr/bin/security"
_TMUTIL = "/usr/bin/tmutil"
_TCCUTIL = "/usr/bin/tccutil"

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]
"""Runs one argv and returns the completed process; never raises on a non-zero rc."""


class GovernanceError(AgentSyncError):
    """A purge, compaction, hold or offboarding step was refused or failed; the message says why."""


class GovernanceUnreadError(GovernanceError):
    """A governance file, or governance/ itself, that cannot be read as what it is, the person's: ``path``,
    ``role`` (the file named by what it is), ``detail`` and ``fix``, the words status's FAIL and every
    command's ``error: state:`` line print alike, never "check crashed" or a class name; fail closed, never
    the empty default, which would drop legal holds (B13 round 28 ruling 19)."""

    def __init__(self, path: Path, role: str, detail: str, fix: str) -> None:
        self.path, self.role, self.detail, self.fix = path, role, detail, fix
        super().__init__(f"{detail} (fix: {fix})")


class GovernanceNotAFileError(GovernanceUnreadError):
    """A governance file that is not a regular file (a folder, a fifo, a socket, a link to one or a loop),
    or governance/ that is not a folder (``role`` :data:`GOVERNANCE_DIR_ROLE`): the person's move aside,
    keeping a copy, in the words status prints (built by :func:`_not_a_file_words`;
    ``doctor.state_not_a_file`` is the finder), never "a fault in
    agentsync to report"; fail closed, never the empty default, which would drop legal holds (B13 round 26
    E43; ruling 13)."""

    def __init__(self, path: Path, kind: str, role: str) -> None:
        self.kind = kind
        super().__init__(path, role, *_not_a_file_words(path, kind, role))


class GovernanceDamagedError(GovernanceUnreadError):
    """A governance file that is a regular file but not one agentsync can read: damaged JSON, an empty or
    cut-short file, or valid JSON of the wrong shape (``[]`` or ``{"holds": "all"}`` at holds.json). The
    person's, in :func:`_damaged_words`, never "fix or remove it by hand": a hold list removed by hand read
    as no holds, and ``purge --queue`` erased a page under a hold on all (B13 round 28 I90)."""

    def __init__(self, path: Path, role: str, what: str) -> None:
        super().__init__(path, role, *_damaged_words(path, role, what))


class HoldActiveError(GovernanceError):
    """A legal/records hold covers the scope: purge and compaction are suspended until it is released."""


class PurgeReason(enum.StrEnum):
    """Why content is purged (C15 7: the four triggers plus an operator request)."""

    UPSTREAM_DELETED = "upstream-deleted"
    LABEL_ESCALATION = "label-escalation"
    DLP_REMEDIATION = "dlp-remediation"
    ERASURE_REQUEST = "erasure-request"
    OPERATOR = "operator"


# ---------------------------------------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GovernanceConfig:
    """The ``[governance]`` table of sources.toml (retention, remote policy, config-level legal hold)."""

    history_days: int = DEFAULT_HISTORY_DAYS
    compaction_slack_days: int = 7
    allow_remote: bool = False
    remote_url_prefixes: tuple[str, ...] = ()
    hold: bool = False
    hold_reason: str | None = None
    hold_owner: str | None = None
    purge_on_upstream_delete: bool = True
    archive: bool = False


ARCHIVE_KEEPS_HISTORY = "archive on: history is kept"
"""Why compaction is skipped (STATE.md retention line) or refused (``compact-history``) under ``archive``."""


def _bool(t: Mapping[str, Any], key: str, where: str, default: bool) -> bool:
    v = t.get(key, default)
    if not isinstance(v, bool):
        raise ConfigError(f"{where}.{key}: expected true/false, got {v!r}")
    return v


def _int(t: Mapping[str, Any], key: str, where: str, default: int, minimum: int) -> int:
    v = t.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
        raise ConfigError(f"{where}.{key}: expected an integer >= {minimum}, got {v!r}")
    return v


def _opt_str(t: Mapping[str, Any], key: str, where: str) -> str | None:
    v = t.get(key)
    if v is None:
        return None
    if not isinstance(v, str) or not v.strip():
        raise ConfigError(f"{where}.{key}: expected a non-empty string, got {v!r}")
    return v.strip()


def parse_governance(doc: Mapping[str, Any], *, where: str = "sources.toml") -> GovernanceConfig:
    """Validate the ``[governance]`` table of parsed sources.toml (absent = defaults); raises ConfigError."""
    raw = doc.get("governance", {})
    w = f"{where}: [governance]"
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{w}: must be a table")
    unknown = sorted(set(raw) - _GOVERNANCE_KEYS)
    if unknown:
        raise ConfigError(
            f"{w}: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(sorted(_GOVERNANCE_KEYS))}"
        )
    prefixes_raw = raw.get("remote_url_prefixes", [])
    if not isinstance(prefixes_raw, list) or not all(isinstance(p, str) and p.strip() for p in prefixes_raw):
        raise ConfigError(f"{w}.remote_url_prefixes: expected a list of non-empty strings")
    for prefix in prefixes_raw:
        parsed = _parse_remote(prefix)
        if parsed is None or (parsed.scheme != "file" and not parsed.host):
            raise ConfigError(
                f"{w}.remote_url_prefixes: {prefix!r} is not a remote URL (scheme://host/path, "
                "user@host:path or an absolute path)"
            )
        if parsed.userinfo and ":" in parsed.userinfo:
            raise ConfigError(f"{w}.remote_url_prefixes: {_redact_url(prefix)} carries a password")
    cfg = GovernanceConfig(
        history_days=_int(raw, "history_days", w, DEFAULT_HISTORY_DAYS, 1),
        compaction_slack_days=_int(raw, "compaction_slack_days", w, 7, 0),
        allow_remote=_bool(raw, "allow_remote", w, False),
        remote_url_prefixes=tuple(p.strip() for p in prefixes_raw),
        hold=_bool(raw, "hold", w, False),
        hold_reason=_opt_str(raw, "hold_reason", w),
        hold_owner=_opt_str(raw, "hold_owner", w),
        purge_on_upstream_delete=_bool(raw, "purge_on_upstream_delete", w, True),
        archive=_bool(raw, "archive", w, False),
    )
    if cfg.archive and raw.get("purge_on_upstream_delete") is True:
        raise ConfigError(
            f"{w}: archive = true keeps what a source deletes, purge_on_upstream_delete = true erases it; "
            "set one of them (archive alone queues no purge on an upstream delete)"
        )
    if cfg.archive:
        cfg = dataclasses.replace(cfg, purge_on_upstream_delete=False)
    if cfg.hold and not cfg.hold_reason:
        raise ConfigError(
            f"{w}: hold = true needs hold_reason (who asked, and why), so a refusal can say why"
        )
    if cfg.allow_remote and not cfg.remote_url_prefixes:
        raise ConfigError(
            f"{w}: allow_remote = true needs remote_url_prefixes naming the tenant-owned remote "
            '(e.g. ["https://dev.azure.com/contoso/"]); every clone is a copy no purge can reach'
        )
    return cfg


def load_governance(config_path: Path) -> GovernanceConfig:
    """Read ``[governance]`` from sources.toml at ``config_path``; a missing file gives the defaults."""
    path = expand(config_path)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return GovernanceConfig()
    try:
        doc = tomllib.loads(data.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: not valid TOML: {exc}") from None
    return parse_governance(doc, where=str(path))


# ---------------------------------------------------------------------------------------------------------
# small file helpers (state dir: governance/)
# ---------------------------------------------------------------------------------------------------------


def governance_dir(state_dir: Path) -> Path:
    """Return ``<state_dir>/governance`` (holds, audit trail, purge queue, suppression list)."""
    return expand(state_dir) / "governance"


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(_UTC)).astimezone(_UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(_UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


GOVERNANCE_DIR_ROLE = "agentsync's governance folder"
"""The role of governance/ itself in :class:`GovernanceNotAFileError` (B13 round 28 I88, I89)."""


def governance_dir_error(state_dir: Path) -> GovernanceNotAFileError | None:
    """governance/ that is not a folder agentsync can use, as the error its readers and writers raise: a
    link to nothing, a symlink loop, a link through a file, or a file, a fifo or a socket at it or behind a
    link (``doctor.governance_dir_kind``); None for nothing there (a fresh install), a folder, a link to one,
    or a folder this account cannot search (its own permission lines). Judged before any file under it is
    read: a governance/ link to a gone volume read as no holds, and ``purge docs=`` erased a page under a hold
    on all, then raised FileExistsError with no audit line (B13 round 28 I88); a loop, a file or a fifo there
    read as "check crashed" and named a different file under it in each command (I89)."""
    from agentsync.ops import doctor  # noqa: PLC0415 - doctor imports this module (through cycle)

    gov = governance_dir(state_dir)
    kind = doctor.governance_dir_kind(gov)
    return None if kind is None else GovernanceNotAFileError(gov, kind, GOVERNANCE_DIR_ROLE)


def _refuse_bad_dir(folder: Path) -> None:
    """Raise :func:`governance_dir_error` of the governance/ folder ``folder`` when it is not one."""
    from agentsync.ops import doctor  # noqa: PLC0415 - doctor imports this module (through cycle)

    kind = doctor.governance_dir_kind(folder)
    if kind is not None:
        raise GovernanceNotAFileError(folder, kind, GOVERNANCE_DIR_ROLE)


def _mkdir_governance(folder: Path) -> None:
    """Create governance/ (0700) when it is not there; one that is not a folder raises its not-a-file error
    first, and a mkdir over a link to nothing (FileExistsError) or under a file (NotADirectoryError) the same
    error, never a traceback (B13 round 28 I88)."""
    _refuse_bad_dir(folder)
    try:
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    except (FileExistsError, NotADirectoryError):
        _refuse_bad_dir(folder)
        raise


def _write_target(path: Path, role: str) -> Path:
    """Where a write of ``path`` lands: the file a working link points to, so the link and its target stay in
    step (B13 round 28 I94: a release renamed a local file over a linked holds.json, and the target kept the
    hold on all), else ``path``. A link to nothing or a loop is its not-a-file error (every writer reads
    first, so only a race reaches this)."""
    if not path.is_symlink():
        return path
    try:
        return Path(path).resolve(strict=True)
    except (OSError, RuntimeError):
        from agentsync.ops import doctor  # noqa: PLC0415 - doctor imports this module (through cycle)

        raise GovernanceNotAFileError(
            path, doctor.governance_file_kind(path) or "a symlink to nothing", role
        ) from None


def _atomic_write_json(path: Path, obj: object, role: str = "a governance file") -> None:
    """Write ``obj`` to ``path`` by a temporary file renamed over it, fsynced, mode 0600: through a working
    link into its target's folder (:func:`_write_target`), never over the link; a target folder this account
    cannot write is a GovernanceError naming the target, never a traceback (B13 round 28 I94)."""
    _mkdir_governance(path.parent)
    dest = _write_target(path, role)
    data = (json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    try:
        fd, name = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".tmp")
    except PermissionError as exc:
        if dest == path:  # the state folder's own: its handlers name the chmod
            raise
        raise GovernanceError(f"{dest} cannot be written: {exc.strerror or exc}") from exc
    tmp = Path(name)
    try:
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        tmp.replace(dest)
    except PermissionError as exc:
        tmp.unlink(missing_ok=True)
        if dest == path:
            raise
        raise GovernanceError(f"{dest} cannot be replaced: {exc.strerror or exc}") from exc
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    with contextlib.suppress(OSError):
        dir_fd = os.open(dest.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def _read_json(path: Path, default: Any, role: str = "a governance file", *, again: bool = True) -> Any:
    """The JSON at ``path``, ``default`` only when nothing is there (a fresh install). Anything at the path
    that is not a regular file is GovernanceNotAFileError, the person's, judged by the path's own lstat
    (``doctor.governance_file_kind``): a symlink to nothing, never the empty default, which read a hold list
    behind a dangling link as no holds (B13 round 27 P131); a loop or a link through a file at the file
    itself, never "unreadable", the words of a fault to report (P132, P133); a folder this account cannot
    open, never its chmod alone, after which the move aside still stops (P135). governance/ itself that is
    not a folder is judged first, by its own lstat, never the empty default under a link to nothing nor
    "unreadable" under a loop (B13 round 28 I88, I89). Text that is not JSON (damaged, empty, cut short) is
    GovernanceDamagedError, never "fix or remove it by hand" (I90). A regular file that appears between the
    failed open and the look at what is there is read once more, never called a link to nothing (I107)."""
    from agentsync.ops import doctor  # noqa: PLC0415 - doctor imports this module (through cycle)

    try:  # never a plain read: a fifo at holds.json hung status and every hold (B13 round 25 D46)
        text = read_regular_text(path)
        if text is None:
            _refuse_bad_dir(path.parent)
            if os.path.lexists(path):
                kind = doctor.governance_file_kind(path)
                if kind is not None:
                    raise GovernanceNotAFileError(path, kind, role)
                if again:  # something readable appeared: a hold written meanwhile (I107)
                    return _read_json(path, default, role, again=False)
                raise GovernanceError(f"{path} kept changing while it was read; run the command again")
            return default
        if not text.strip():
            raise GovernanceDamagedError(path, role, "the file is empty")
        return json.loads(text)
    except ValueError as exc:
        if not isinstance(exc, json.JSONDecodeError | UnicodeDecodeError):  # not a file: moved aside (E43)
            raise GovernanceNotAFileError(
                path, doctor.governance_file_kind(path) or "not a regular file", role
            ) from None
        raise GovernanceDamagedError(path, role, f"it is not JSON ({exc})") from None
    except (
        PermissionError
    ) as exc:  # its owner or mode, not its contents (B13 round 20 X73): kept as the cause
        kind = doctor.state_file_kind(path)
        if kind is not None:  # a 0000 folder: the person's move aside, its chmod first (P135)
            raise GovernanceNotAFileError(path, kind, role) from None
        raise GovernanceError(f"{path} cannot be opened: {exc.strerror or exc}") from exc
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            _refuse_bad_dir(path.parent)  # the folder above, by its own lstat (I89)
        kind = doctor.state_file_kind(path) if exc.errno in (errno.ELOOP, errno.ENOTDIR) else None
        if kind is not None:  # at the file itself; above it, the folder's own (P133)
            raise GovernanceNotAFileError(path, kind, role) from None
        raise GovernanceError(f"{path} cannot be read: {exc.strerror or exc}") from exc


def _damaged_words(path: Path, role: str, what: str) -> tuple[str, str]:
    """The detail and the person's fix of a governance file that is a regular file agentsync cannot read
    (``what``: why). Never "remove it" and never a bare move aside: a hold list moved aside or removed read as
    no holds and its page was erased (B13 round 28 I90), so the hold list's fix restores it, or keeps a copy
    and sets each hold again before any purge; the audit trail's record keeps them in force meanwhile."""
    from agentsync.ops import doctor  # noqa: PLC0415 - doctor imports this module (through cycle)

    shown = doctor._shell_path(path)
    detail = f"{path} is not {role} agentsync can read: {what}"
    if role == "the hold list":
        fix = (
            f"the person (the records or legal owner) restores {shown} from a copy they trust; or moves it "
            f"aside, keeping a copy, and before any purge sets each hold it held again with "
            f"`{AGENTSYNC_BIN} hold <scope> --reason <why> --owner <name>` (until then the holds the audit "
            "trail records stay in force)"
        )
    else:
        fix = f"the person restores {shown} from a copy they trust, or moves it aside, keeping a copy"
    return detail, fix


def audit_path(state_dir: Path) -> Path:
    """Return the append-only audit trail ``<state_dir>/governance/audit.jsonl`` (no content, ever)."""
    return governance_dir(state_dir) / "audit.jsonl"


@contextlib.contextmanager
def _audit_appender(state_dir: Path) -> Iterator[Callable[[Mapping[str, object]], None]]:
    """Open the audit trail for append (mode 0600) and yield a writer of one JSON line (sorted keys, fsynced)
    on that open file.  A change that must leave an audit line opens it first, then writes its own file, then
    the line: an audit trail this account cannot open raises before anything changed (B13 round 23 A79: a
    hold was released, or set, and the purge queue written, with no audit line, and the command exited 1)."""
    path = audit_path(state_dir)
    _mkdir_governance(path.parent)  # a governance/ that is not a folder: its error, no traceback (I88)
    fd = _open_audit(path)

    def write(record: Mapping[str, object]) -> None:
        os.write(fd, (json.dumps(dict(record), sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
        os.fsync(fd)

    try:
        yield write
    finally:
        os.close(fd)


def _not_a_file_words(path: Path, kind: str, role: str) -> tuple[str, str]:
    """The detail and the person's fix of something at a governance file that is not a file (``kind``, in
    ``doctor.state_file_kind``'s words; ``role`` names the file: ``the audit trail``, ``the hold list``,
    ``the purge queue``, ``the suppression list``): status's FAIL and a command's error line say the same,
    the file named by what it is, never "not the state file" (the manifest's words), and the move aside
    keeping a copy, as install.sh and state_dir.files say it (B13 round 25 D22, D41; round 26 E43)."""
    from agentsync.ops import doctor  # noqa: PLC0415 - doctor imports this module (through cycle)

    shown = doctor._shell_path(path)
    if role == GOVERNANCE_DIR_ROLE:
        # never a bare move aside: the hold list and the audit trail live in it, and a governance/ moved
        # aside read as no holds, so a page under a hold on all was erased (B13 round 28 I89)
        detail = f"{path} is {kind}, not a folder: {role} belongs there"
        return detail, (
            "the person puts the real governance folder (the one holding holds.json and audit.jsonl) back "
            f"at {shown}, from wherever it was moved or from a copy; only where none was ever kept, moves "
            f"{shown} aside, keeping a copy (a hold set earlier stays in force from heartbeat.json's record)"
        )
    detail = f"{path} is {kind}, not a file: {role} belongs there"  # no bracket before "(fix:" (E18)
    # a folder shut to this account takes its chmod (or the person's chown) first: macOS refuses its move
    # (B13 round 27 P135, as the lock's and the heartbeat's fixes already said)
    first = doctor._opens_first(path, kind)
    fix = f"the person {first}moves {shown} aside, keeping a copy"
    if role == "the hold list":  # then the list itself back, so following it never leaves no holds (I87)
        fix += ", then puts the hold list back from a copy they trust"
    return detail, fix


def audit_not_a_file(path: Path, kind: str) -> tuple[str, str]:
    """:func:`_not_a_file_words` of the audit trail (B13 round 25 D41)."""
    return _not_a_file_words(path, kind, "the audit trail")


GOVERNANCE_ROLES = {
    "holds.json": "the hold list",
    "purge-queue.json": "the purge queue",
    "suppressed.json": "the suppression list",
    "audit.jsonl": "the audit trail",
}
"""What each governance file is, in the words of its not-a-file FAIL (B13 round 26 E43)."""


def governance_not_a_file(state_dir: Path) -> GovernanceNotAFileError | None:
    """The first governance file that is not a regular file, by its stat (``doctor.state_file_kind``, which
    opens nothing, so a fifo never blocks), as the error its readers raise; None when each is a file, a link
    to one or missing (B13 round 26 E43; ruling 13). governance/ itself first (:func:`governance_dir_error`),
    so every command refuses over it in its own words, init included (B13 round 28 I88, I89)."""
    from agentsync.ops import doctor  # noqa: PLC0415 - doctor imports this module (through cycle)

    folder = governance_dir_error(state_dir)
    if folder is not None:
        return folder
    for name, role in GOVERNANCE_ROLES.items():
        path = governance_dir(state_dir) / name
        kind = doctor.governance_file_kind(path)  # a link to nothing too (B13 round 27 P131)
        if kind is not None:
            return GovernanceNotAFileError(path, kind, role)
    return None


def governance_damaged(state_dir: Path) -> GovernanceDamagedError | None:
    """The first governance file that is a regular file agentsync cannot read (damaged JSON, empty, or the
    wrong shape: :class:`GovernanceDamagedError`), the hold list first; None when each reads, or another
    error stops the read (its own line says that). Read only after :func:`governance_not_a_file` passed, so
    each is a regular file and a fifo never blocks: sync's guard refuses over it in status's words, never a
    traceback and never an exit 0 (B13 round 28 I90)."""
    for read in (_state_holds, pending_purges, load_suppressions):
        try:
            read(state_dir)
        except GovernanceDamagedError as exc:
            return exc
        except Exception:  # another error: its own handlers
            continue
    return None


def _not_a_file_error(path: Path, mode: int | None = None) -> GovernanceError | None:
    """The error of a hold, its release or a queued purge over an audit trail that is not a file, by what
    status's check reads there (``doctor.state_file_kind``: a folder, a socket, a fifo, a link to one or a
    loop), or, from the open file's own mode (``mode``), a special file reached through a link; None for a
    regular file or a link to one, which appends as before (B13 round 25 D41)."""
    from agentsync.ops import doctor  # noqa: PLC0415 - doctor imports this module (through cycle)

    kind = doctor.governance_file_kind(path)
    if kind is None and mode is not None:
        kind = doctor._not_a_file(mode)
    if kind is None:
        return None
    detail, fix = audit_not_a_file(path, kind)
    return GovernanceError(f"{detail} (fix: {fix})")


def _open_audit(path: Path) -> int:
    """The audit trail opened for append, never waiting: O_NONBLOCK, so a fifo where it belongs that nothing
    reads fails at once (ENXIO) rather than holding a hold, its release and a queued purge forever (B13 round
    24 B63). Anything there that is not a regular file (a folder: EISDIR; a socket: EOPNOTSUPP on macOS,
    ENXIO on Linux; a symlink loop: ELOOP) is refused with status's words before a line is written, whatever
    the open's errno, judged by what is there (B13 round 25 D41: only the fifo's ENXIO was, and a folder or a
    socket ended on a traceback). The flag changes nothing for a regular file."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NONBLOCK | os.O_CLOEXEC
    refused = _not_a_file_error(
        path
    )  # first: O_CREAT follows a link to nothing and creates its target (P131)
    if refused is not None:
        raise refused
    try:
        fd = os.open(path, flags, 0o600)
    except OSError:
        refused = _not_a_file_error(path)
        if refused is None:  # a read-only volume, a mode: their own handling
            raise
        raise refused from None
    mode = os.fstat(fd).st_mode
    if not stat.S_ISREG(mode):
        os.close(fd)
        raise _not_a_file_error(path, mode) or GovernanceError(f"{path} is not a regular file")
    return fd


def sticky_blocker(state_dir: Path) -> Path | None:
    """The first file a hold, its release or a queued purge replaces (holds.json, the purge queue, the
    suppression list) that another account owns in a governance/ folder with the sticky bit set that another
    account owns too, where no rename replaces it (EPERM) though the folder takes a write; else None. The
    sticky folder's own owner may replace any file in it, and whose folder it is decides nothing else:
    another account's folder this account can write works (B13 round 25 D45). Through a link, as a hold
    writes. A working link is judged where the write renames, in its target's folder, by the target's owner
    (B13 round 28 I94: a write now lands in the target)."""
    for path in (_holds_file(state_dir), _queue_file(state_dir), _suppress_file(state_dir)):
        try:
            dest = path.resolve(strict=True) if path.is_symlink() else path
            st = dest.parent.stat()
            if not st.st_mode & stat.S_ISVTX or st.st_uid == os.getuid():
                continue
            if dest.lstat().st_uid != os.getuid():
                return path
        except (OSError, RuntimeError):
            continue
    return None


def append_audit(state_dir: Path, record: Mapping[str, object]) -> None:
    """Append one JSON line (sorted keys) to the audit trail, mode 0600, fsynced."""
    with _audit_appender(state_dir) as write:
        write(record)


def read_audit(state_dir: Path) -> list[dict[str, Any]]:
    """Return every audit record, oldest first."""
    try:
        text = audit_path(state_dir).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ---------------------------------------------------------------------------------------------------------
# legal / records holds
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Hold:
    """One active hold: ``scope`` is ``all`` or a source id; ``origin`` is ``config`` or ``state``."""

    scope: str
    reason: str
    owner: str
    set_at: str
    origin: str

    def describe(self) -> str:
        """One line naming the scope, why, who and since when."""
        when = f" since {self.set_at}" if self.set_at else ""
        return f"hold {self.scope} ({self.origin}): {self.reason} — set by {self.owner}{when}"


def _holds_file(state_dir: Path) -> Path:
    return governance_dir(state_dir) / "holds.json"


def _check_scope(scope: str) -> str:
    s = scope.strip()
    if s != HOLD_ALL and not _SOURCE_ID_RE.match(s):
        raise GovernanceError(f"hold scope must be {HOLD_ALL!r} or a source id, got {scope!r}")
    return s


_HOLD_KEYS = ("scope", "reason", "owner", "set_at")


def _state_holds(state_dir: Path) -> list[Hold]:
    """The holds holds.json lists. One of the wrong shape (``[]``, ``null``, ``{"holds": "all"}``,
    ``{"holds": ["all"]}``) is GovernanceDamagedError, never an AttributeError traceback out of every command
    and never no holds (B13 round 28 I90); holds.json has had this one shape since it was added."""
    path = _holds_file(state_dir)
    data = _read_json(path, {"holds": []}, "the hold list")
    # an object with no "holds" is no holds, as the queue's: the audit trail keeps any it lost (I87)
    entries = data.get("holds", []) if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise GovernanceDamagedError(path, "the hold list", 'it is not an object with a "holds" list')
    out: list[Hold] = []
    for h in entries:
        if not isinstance(h, dict) or not all(isinstance(h.get(k), str) for k in _HOLD_KEYS):
            raise GovernanceDamagedError(path, "the hold list", f"an entry is not a hold: {h!r}"[:200])
        try:
            scope = _check_scope(h["scope"])
        except GovernanceError:
            raise GovernanceDamagedError(
                path, "the hold list", f"an entry's scope is {h['scope']!r}"
            ) from None
        out.append(Hold(scope, h["reason"], h["owner"], h["set_at"], "state"))
    return out


HOLD_RECORD_KEY = _HOLD_RECORD_KEY
"""heartbeat.json's record of the state hold scopes, a list beside the per-source entries (whose readers
take only objects), kept outside governance/ so a governance/ moved aside never lifts a hold (B13 round 28
I89)."""


def _heartbeat_file(state_dir: Path) -> Path:
    return expand(state_dir) / "heartbeat.json"


def recorded_hold_scopes(state_dir: Path) -> frozenset[str]:
    """The scopes heartbeat.json's record says were held (:data:`HOLD_RECORD_KEY`); empty when there is no
    record or it cannot be read (no record to keep: today's answer)."""
    try:
        text = read_regular_text(_heartbeat_file(state_dir))
        data = json.loads(text) if text else None
    except (OSError, ValueError):
        return frozenset()
    scopes = data.get(HOLD_RECORD_KEY) if isinstance(data, dict) else None
    if not isinstance(scopes, list):
        return frozenset()
    return frozenset(s for s in scopes if isinstance(s, str))


def record_holds(state_dir: Path, scopes: Iterable[str]) -> None:
    """Write ``scopes`` as heartbeat.json's hold record, into an existing regular heartbeat only (a new one
    would read as an earlier pass to the lost-manifest guard), keeping every source's entry; best effort: a
    heartbeat that cannot be read or written keeps the audit trail as the record."""
    path = _heartbeat_file(state_dir)
    try:
        text = read_regular_text(path)
        data = json.loads(text) if text else None
        if not isinstance(data, dict):
            return
        data[HOLD_RECORD_KEY] = sorted(set(scopes))
        body = (json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, body)
            os.fsync(fd)
        finally:
            os.close(fd)
        Path(name).replace(path)
    except (OSError, ValueError) as exc:
        log.debug("governance: hold record not written to %s: %s", path, exc)


def _audit_events(state_dir: Path) -> list[dict[str, Any]]:
    """The audit trail's records, oldest first, for the hold replay: a line that does not parse is skipped,
    and a trail that cannot be read is none (its own governance.audit line says why)."""
    try:
        text = read_regular_text(audit_path(state_dir))
    except (OSError, ValueError):
        return []
    out: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def audit_holds(state_dir: Path) -> tuple[dict[str, dict[str, Any]], frozenset[str]]:
    """The audit trail replayed per scope: the scopes whose last record is ``hold-set`` (each with that
    record) and those whose last is ``hold-released``. Only :func:`set_hold` and :func:`release_hold` write
    holds.json, each with its audit line in the same step, so the replay is the record of the holds in force
    (B13 round 28 I87)."""
    last: dict[str, dict[str, Any]] = {}
    for rec in _audit_events(state_dir):
        if rec.get("action") in ("hold-set", "hold-released") and isinstance(rec.get("scope"), str):
            last[rec["scope"]] = rec
    held = {s: r for s, r in last.items() if r["action"] == "hold-set"}
    released = frozenset(s for s, r in last.items() if r["action"] == "hold-released")
    return held, released


def lost_holds(state_dir: Path, listed: Iterable[Hold] | None = None) -> list[Hold]:
    """The holds the audit trail records as set and not released, or heartbeat.json's record names with no
    release, that holds.json (``listed``, read here when None) lacks: missing, rewritten as no holds, or
    with governance/ moved aside. Each is in force (``origin`` ``audit``), so purge, ``purge --queue`` and
    compaction refuse it and status shows it: following status's fix for an unreadable hold list never
    leaves a page under a hold unprotected (B13 round 28 I87, I89; ruling 19)."""
    have = {h.scope for h in (_state_holds(state_dir) if listed is None else listed)}
    held, released = audit_holds(state_dir)
    out = [
        Hold(
            scope,
            LOST_HOLD_REASON,
            str(rec.get("owner") or "the records owner"),
            str(rec.get("at") or ""),
            "audit",
        )
        for scope, rec in sorted(held.items())
        if scope not in have
    ]
    out += [
        Hold(scope, LOST_HOLD_REASON, "the records owner", "", "audit")
        for scope in sorted(recorded_hold_scopes(state_dir) - have - released - set(held))
    ]
    return out


LOST_HOLD_REASON = "lost from the hold list"
"""The reason of a hold :func:`lost_holds` keeps in force (B13 round 28 I87)."""


def lost_hold_words(state_dir: Path, hold: Hold) -> tuple[str, str]:
    """governance.hold's FAIL of a lost hold (:func:`lost_holds`): the detail and the person's fix, which
    puts the hold list back, sets the hold again or releases it, never a purge first."""
    path = _holds_file(state_dir)
    by = f" set by {hold.owner}" + (f" at {hold.set_at}" if hold.set_at else "")
    detail = (
        f"{path} no longer lists the hold on {hold.scope}{by} (audit.jsonl, heartbeat.json), and no release "
        "is recorded; purge and compaction stay suspended"
    )
    fix = (
        f"the person puts the hold list back from the copy kept, or the records owner runs `{AGENTSYNC_BIN} "
        f"hold {hold.scope} --reason <why> --owner <name>` again, or releases it with `{AGENTSYNC_BIN} hold "
        f"{hold.scope} --release --owner <name>`"
    )
    return detail, fix


def set_hold(state_dir: Path, scope: str, *, reason: str, owner: str, now: datetime | None = None) -> Hold:
    """Record a hold (records/legal owner only) that suspends purge and compaction for ``scope``."""
    s = _check_scope(scope)
    if not reason.strip() or not owner.strip():
        raise GovernanceError("a hold needs a reason and the name of the records or legal owner setting it")
    at = _iso(_now(now))
    holds = [h for h in _state_holds(state_dir) if h.scope != s]
    hold = Hold(s, " ".join(reason.split()), " ".join(owner.split()), at, "state")
    holds.append(hold)
    with _audit_appender(state_dir) as audit:
        _atomic_write_json(
            _holds_file(state_dir),
            {
                "holds": [
                    {"scope": h.scope, "reason": h.reason, "owner": h.owner, "set_at": h.set_at}
                    for h in sorted(holds, key=lambda h: h.scope)
                ]
            },
        )
        audit({"action": "hold-set", "at": at, "scope": s, "owner": hold.owner})
    record_holds(state_dir, (h.scope for h in holds))  # outside governance/ too (I89)
    log.info("governance: %s", hold.describe())  # the CLI prints it once, on stdout (B13 round 28 I47)
    return hold


def release_hold(state_dir: Path, scope: str, *, owner: str, now: datetime | None = None) -> bool:
    """Release the state hold on ``scope``; True if one existed ; config holds live in sources.toml. A hold
    holds.json lost but the audit trail or heartbeat.json's record keeps (:func:`lost_holds`) is released
    too, with its own audit line, so a deliberate release never needs a set first (B13 round 28 I87)."""
    s = _check_scope(scope)
    holds = _state_holds(state_dir)
    keep = [h for h in holds if h.scope != s]
    if len(keep) == len(holds) and not any(h.scope == s for h in lost_holds(state_dir, holds)):
        return False
    at = _iso(_now(now))
    with _audit_appender(state_dir) as audit:  # a release removes protection: never one with no record
        _atomic_write_json(
            _holds_file(state_dir),
            {
                "holds": [
                    {"scope": h.scope, "reason": h.reason, "owner": h.owner, "set_at": h.set_at} for h in keep
                ]
            },
        )
        audit({"action": "hold-released", "at": at, "scope": s, "owner": " ".join(owner.split())})
    record_holds(state_dir, (h.scope for h in keep))
    log.info("governance: hold %s released by %s", s, owner)  # the audit trail records it (I47)
    return True


def config_holds(gov: GovernanceConfig) -> tuple[Hold, ...]:
    """The config-level hold (scope ``all``) when sources.toml sets one: read from ``[governance]`` alone, so
    status shows it over a state folder it cannot open (B13 round 21 Y44: the shut folder's not-checked line
    hid it)."""
    if not gov.hold:
        return ()
    return (Hold(HOLD_ALL, gov.hold_reason or "hold = true", gov.hold_owner or "sources.toml", "", "config"),)


def state_holds(state_dir: Path) -> tuple[Hold, ...]:
    """The holds recorded in the state folder (``governance/holds.json``), by scope."""
    return tuple(sorted(_state_holds(state_dir), key=lambda h: h.scope))


def hold_list_unread(state_dir: Path) -> bool:
    """Whether the state's hold list cannot be read (not a file, a link to nothing, refused, damaged, or
    governance/ not a folder) or has lost a hold the audit trail keeps (:func:`lost_holds`): status and the
    loop invite no queued purge over it, since status FAILs it and every purge refuses until the person puts
    it back (B13 round 27 P131: a dangling holds.json read as no holds, and the invited run erased a page
    under a hold on all; round 28 I87: the same once the person followed the fix)."""
    try:
        return bool(lost_holds(state_dir))
    except Exception:  # not a file, refused, or the wrong shape: no read, so no hold list to trust
        return True


def active_holds(state_dir: Path, gov: GovernanceConfig) -> tuple[Hold, ...]:
    """Every hold in force: the config-level one (scope ``all``) first, then state holds by scope, then the
    holds the audit trail keeps that holds.json lost (:func:`lost_holds`; B13 round 28 I87)."""
    listed = state_holds(state_dir)
    return (*config_holds(gov), *listed, *lost_holds(state_dir, listed))


def blocking_holds(holds: Sequence[Hold], source_ids: Iterable[str] | None) -> list[Hold]:
    """Holds that block an action on ``source_ids`` (None = a repo-wide action: every hold blocks)."""
    if source_ids is None:
        return list(holds)
    ids = set(source_ids)
    return [h for h in holds if h.scope == HOLD_ALL or h.scope in ids]


def _refuse_if_held(holds: Sequence[Hold], what: str) -> None:
    if holds:
        why = "; ".join(h.describe() for h in holds)
        raise HoldActiveError(
            f"{what} suspended by legal/records hold: {why}. Release it first (records owner)."
        )


def hold_state_lines(state_dir: Path, gov: GovernanceConfig) -> list[str]:
    """Markdown lines for STATE.md naming every active hold (empty when none)."""
    holds = active_holds(state_dir, gov)
    if not holds:
        return []
    lines = ["## Legal / records holds", ""]
    lines += [
        f"- HOLD `{h.scope}`: {h.reason} (set by {h.owner}"
        f"{', ' + h.set_at if h.set_at else ''}, {h.origin}); purge and compaction suspended"
        for h in holds
    ]
    return [*lines, ""]


# ---------------------------------------------------------------------------------------------------------
# selectors, suppression list, purge queue
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PurgeSelector:
    """What to purge: exactly one of ``stable_id``, ``path_glob`` (source rel_path) or ``docs_glob``
    (docs-repo-relative path), optionally narrowed to one ``source_id``."""

    source_id: str | None = None
    stable_id: str | None = None
    path_glob: str | None = None
    docs_glob: str | None = None

    def __post_init__(self) -> None:
        """Validate: exactly one selector field, and a well-formed source id."""
        n = sum(v is not None and v != "" for v in (self.stable_id, self.path_glob, self.docs_glob))
        if n != 1:
            raise GovernanceError(
                "a purge selector needs exactly one of stable id, source-path glob, docs glob"
            )
        if self.source_id is not None and not _SOURCE_ID_RE.match(self.source_id):
            raise GovernanceError(f"not a source id: {self.source_id!r}")

    @classmethod
    def parse(cls, text: str, *, source_id: str | None = None) -> PurgeSelector:
        """Parse ``id=<stable_id>``, ``path=<glob>`` or ``docs=<glob>``; bare text with ``*?[/`` is a path
        glob,
        otherwise a stable id."""
        t = text.strip()
        for prefix, key in (("id=", "stable_id"), ("path=", "path_glob"), ("docs=", "docs_glob")):
            if t.startswith(prefix):
                return cls(source_id=source_id, **{key: t[len(prefix) :]})
        if any(c in t for c in "*?[/"):
            return cls(source_id=source_id, path_glob=t)
        return cls(source_id=source_id, stable_id=t)

    def to_json(self) -> dict[str, str]:
        """The non-empty fields (for the queue file)."""
        return {k: v for k, v in dataclasses.asdict(self).items() if v}

    def kind(self) -> str:
        """``stable-id`` | ``path-glob`` | ``docs-glob``."""
        if self.stable_id:
            return "stable-id"
        return "path-glob" if self.path_glob else "docs-glob"

    def fingerprint(self) -> str:
        """sha256 of the selector (what the audit trail records instead of the selector text)."""
        return _sha(json.dumps(self.to_json(), sort_keys=True))

    def matches_item(self, source_id: str, stable_id: str, rel_path: str) -> bool:
        """True when a manifest item / page identity is selected (``docs_glob`` never matches here)."""
        if self.source_id is not None and source_id != self.source_id:
            return False
        if self.stable_id:
            return stable_id == self.stable_id
        if self.path_glob:
            return glob_match(rel_path, self.path_glob)
        return False


@dataclass(frozen=True, slots=True)
class Suppressions:
    """Items and source-path globs that were purged for a reason other than upstream deletion.

    The cycle must skip them (never re-fetch, never re-publish): :meth:`matches` per observed item."""

    items: frozenset[tuple[str, str]] = frozenset()
    globs: tuple[tuple[str, str], ...] = ()
    # Extensions (additive): the purged items' exact source paths (NFC + casefold), so a new id at the same
    # path (an Office safe-save, a re-upload) stays suppressed, and their canonical hashes (H1), so a copy
    # of the erased content under another name is never published either.
    paths: frozenset[tuple[str, str]] = frozenset()
    canonical: frozenset[str] = frozenset()

    def matches(self, source_id: str, stable_id: str, rel_path: str) -> bool:
        """True when this item must not be synced again."""
        if (source_id, stable_id) in self.items:
            return True
        if self.paths and (source_id, _path_key(rel_path)) in self.paths:
            return True
        return any((not sid or sid == source_id) and glob_match(rel_path, g) for sid, g in self.globs)

    def matches_content(self, canonical_sha256: str | None) -> bool:
        """True when fetched content hashes (H1) to purged content (never for the empty file)."""
        return (
            bool(canonical_sha256)
            and canonical_sha256 != _EMPTY_SHA256
            and canonical_sha256 in self.canonical
        )


def _path_key(rel_path: str) -> str:
    return unicodedata.normalize("NFC", rel_path).casefold()


def _suppress_file(state_dir: Path) -> Path:
    return governance_dir(state_dir) / "suppressed.json"


def load_suppressions(state_dir: Path) -> Suppressions:
    """Load the suppression list (empty when none). One of the wrong shape is GovernanceDamagedError, never a
    traceback (B13 round 28 I90)."""
    path = _suppress_file(state_dir)
    data = _read_json(path, {}, "the suppression list")
    pairs_ok = isinstance(data, dict) and all(
        isinstance(data.get(k, []), list)
        and all(isinstance(x, list) and len(x) == 2 for x in data.get(k, []))
        for k in ("items", "globs", "paths")
    )
    if not pairs_ok or not isinstance(data.get("canonical", []), list):
        raise GovernanceDamagedError(
            path, "the suppression list", "its lists are not of the shape agentsync writes"
        )
    items = frozenset((str(a), str(b)) for a, b in data.get("items", []))
    globs = tuple((str(a), str(b)) for a, b in data.get("globs", []))
    paths = frozenset((str(a), _path_key(str(b))) for a, b in data.get("paths", []))
    canonical = frozenset(str(h) for h in data.get("canonical", []))
    return Suppressions(items, globs, paths, canonical)


def _add_suppressions(
    state_dir: Path,
    items: Iterable[tuple[str, str]],
    globs: Iterable[tuple[str, str]],
    *,
    paths: Iterable[tuple[str, str]] = (),
    canonical: Iterable[str] = (),
) -> None:
    cur = load_suppressions(state_dir)
    all_items = sorted(cur.items | set(items))
    all_globs = sorted(set(cur.globs) | set(globs))
    all_paths = sorted(cur.paths | {(sid, _path_key(rel)) for sid, rel in paths if rel})
    all_canonical = sorted(cur.canonical | {h for h in canonical if h and h != _EMPTY_SHA256})
    _atomic_write_json(
        _suppress_file(state_dir),
        {
            "canonical": all_canonical,
            "globs": [list(g) for g in all_globs],
            "items": [list(i) for i in all_items],
            "paths": [list(x) for x in all_paths],
        },
    )


def suppress_items(state_dir: Path, items: Iterable[tuple[str, str]]) -> None:
    """Add ``(source_id, stable_id)`` pairs to the suppression list (content the cycle found to be purged)."""
    _add_suppressions(state_dir, items, ())


@dataclass(frozen=True, slots=True)
class QueuedPurge:
    """One pending purge request.

    ``aliases`` are the item's retired ids when it was queued (its ``item_aliases`` rows), kept beside the
    selector so a purge run after the manifest that linked them was replaced still finds the pages history
    keeps under an older id (B13 round 28 I93, I109).  ``found_nothing`` records that the queue's run found
    nothing its id reaches (:func:`run_purge_queue`), so status says why it stays queued without reading
    history; it is no part of the request, so an entry compares equal with or without it."""

    selector: PurgeSelector
    reason: PurgeReason
    enqueued_at: str
    aliases: tuple[str, ...] = ()
    found_nothing: bool = field(default=False, compare=False)


def _queue_file(state_dir: Path) -> Path:
    return governance_dir(state_dir) / "purge-queue.json"


def pending_purges(state_dir: Path) -> list[QueuedPurge]:
    """Queued purges, oldest first. One of the wrong shape is GovernanceDamagedError, never a traceback (B13
    round 28 I90).  ``aliases`` and ``found_nothing`` sit beside ``selector``, never in it, and are read with
    a default: an older build runs ``PurgeSelector(**selector)`` and drops them when it rewrites the queue,
    which is today's behavior again (B13 round 28 I93, I109)."""
    path = _queue_file(state_dir)
    data = _read_json(path, {"queue": []}, "the purge queue")
    # an object with no "queue" is an empty queue: reading too few purges erases nothing (the hold list, whose
    # reading too few holds would, stays strict)
    entries = data.get("queue", []) if isinstance(data, dict) else ([] if data == [] else None)
    if not isinstance(entries, list):
        raise GovernanceDamagedError(path, "the purge queue", 'it is not an object with a "queue" list')
    out: list[QueuedPurge] = []
    for q in entries:
        try:
            selector = PurgeSelector(**q["selector"])  # a q that is no object raises TypeError here
            aliases = q.get("aliases", [])
            found_nothing = q.get("found_nothing", False)
            if not isinstance(aliases, list) or not all(isinstance(a, str) and a for a in aliases):
                raise TypeError("aliases is not a list of ids")
            if not isinstance(found_nothing, bool):
                raise TypeError("found_nothing is not true or false")
            out.append(
                QueuedPurge(
                    selector,
                    PurgeReason(q["reason"]),
                    str(q["enqueued_at"]),
                    tuple(aliases),
                    found_nothing,
                )
            )
        except (TypeError, KeyError, ValueError, GovernanceError):
            raise GovernanceDamagedError(
                path, "the purge queue", f"an entry is not a queued purge: {q!r}"[:200]
            ) from None
    return out


def _queue_entry(q: QueuedPurge) -> dict[str, object]:
    """One queue entry as written: ``aliases`` and ``found_nothing`` only when set, beside the selector."""
    out: dict[str, object] = {
        "selector": q.selector.to_json(),
        "reason": q.reason.value,
        "enqueued_at": q.enqueued_at,
    }
    if q.aliases:
        out["aliases"] = list(q.aliases)
    if q.found_nothing:
        out["found_nothing"] = True
    return out


def _write_queue(state_dir: Path, queue: Sequence[QueuedPurge]) -> None:
    _atomic_write_json(_queue_file(state_dir), {"queue": [_queue_entry(q) for q in queue]})


def enqueue_purge(
    state_dir: Path,
    selector: PurgeSelector,
    reason: PurgeReason,
    *,
    aliases: Iterable[str] = (),
    now: datetime | None = None,
) -> bool:
    """Queue a purge (confirmed upstream deletion past the breaker, label escalation, DLP, erasure); False if
    an identical request is already queued.  ``aliases`` are the item's retired ids (the manifest's
    ``item_aliases`` rows whose current id is the queued one), stored beside the selector, never a path: a
    new start or a restored older manifest loses the rows that link them (B13 round 28 I93, I109)."""
    queue = pending_purges(state_dir)
    if any(q.selector == selector and q.reason is reason for q in queue):
        return False
    retired = tuple(sorted({a for a in aliases if a and a != selector.stable_id}))
    queue.append(QueuedPurge(selector, reason, _iso(_now(now)), retired))
    with _audit_appender(state_dir) as audit:  # a refused audit trail queues nothing (A79)
        _write_queue(state_dir, queue)
        audit(
            {
                "action": "purge-enqueued",
                "at": _iso(_now(now)),
                "reason": reason.value,
                "selector_kind": selector.kind(),
                "selector_sha256": selector.fingerprint(),
                "source_id": selector.source_id,
            }
        )
    return True


def _covers(selector: PurgeSelector, report: PurgeReport, entry: QueuedPurge) -> bool:
    """True when the verified purge of ``selector`` (``report``) already did what the queued ``entry`` asks:
    the entry names one item by source and stable id, the purge erased that id or one of the entry's stored
    aliases (so a hand ``purge docs=<page>`` of an old-id page takes it off the queue; B13 round 28 I109) and
    was not narrowed to another source, and it suppressed the item if the entry's purge would have (every
    reason but ``upstream-deleted`` suppresses)."""
    sel = entry.selector
    if not (sel.stable_id and sel.source_id) or not any(
        (sel.source_id, i) in report.items for i in (sel.stable_id, *entry.aliases)
    ):
        return False
    if selector.source_id not in (None, sel.source_id):
        # a docs glob narrowed to another source targets the matched page only, not the item's other pages
        return False
    return report.reason is not PurgeReason.UPSTREAM_DELETED or entry.reason is PurgeReason.UPSTREAM_DELETED


def _drop_covered(state_dir: Path, selector: PurgeSelector, report: PurgeReport) -> None:
    """Take off the queue every entry a verified purge already did (:func:`_covers`).  A queue file this build
    cannot read or write is logged and left as it is: the purge itself is done and its report stands."""
    try:
        queue = pending_purges(state_dir)
        kept = [q for q in queue if not _covers(selector, report, q)]
        if len(kept) != len(queue):
            _write_queue(state_dir, kept)
    except Exception as exc:  # the purge already verified: the queue is only tidied
        log.warning("governance: purge queue not updated after a verified purge: %s", exc)


# ---------------------------------------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------------------------------------


def _git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _SCRUBBED_ENV and not k.startswith("GIT_CONFIG")}
    env.update(
        {
            "LC_ALL": "C",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ADVICE": "0",
            "GIT_EDITOR": ":",
            "GIT_PAGER": "cat",
        }
    )
    return env


def _git(
    repo: Path, *args: str, input_bytes: bytes | None = None, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    argv = [str(gitops.git_executable()), "-C", str(repo), *args]
    try:
        proc = subprocess.run(
            argv,
            input=input_bytes,
            stdin=None if input_bytes is not None else subprocess.DEVNULL,
            capture_output=True,
            env=_git_env(),
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise GovernanceError(f"git {' '.join(args[:2])}: {exc}") from None
    if check and proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise GovernanceError(f"git {' '.join(args[:3])} failed (rc {proc.returncode}): {err}")
    return proc


def _git_text(repo: Path, *args: str, check: bool = True) -> str:
    return _git(repo, *args, check=check).stdout.decode("utf-8", "surrogateescape")


class _CatFile:
    """One long-running ``git cat-file --batch`` reader."""

    def __init__(self, repo: Path) -> None:
        self._proc = subprocess.Popen(
            [str(gitops.git_executable()), "-C", str(repo), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_git_env(),
        )
        self._memo: dict[str, tuple[str, bytes]] = {}

    def _pipes(self) -> tuple[_IO[bytes], _IO[bytes]]:
        stdin, stdout = self._proc.stdin, self._proc.stdout
        if stdin is None or stdout is None:
            raise GovernanceError("git cat-file --batch has no pipes")
        return stdin, stdout

    def read(self, sha: str) -> tuple[str, bytes]:
        """Return (type, content) of one object; raises GovernanceError when missing."""
        stdin, stdout = self._pipes()
        stdin.write(sha.encode("ascii") + b"\n")
        stdin.flush()
        header = stdout.readline().decode("ascii", "replace").split()
        if len(header) != 3:
            raise GovernanceError(f"git object {sha} is missing ({' '.join(header)})")
        size = int(header[2])
        data = stdout.read(size)
        stdout.read(1)
        return header[1], data

    def close(self) -> None:
        """Stop the reader."""
        with contextlib.suppress(OSError):
            stdin, _ = self._pipes()
            stdin.close()
        try:
            self._proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        if self._proc.stdout is not None:
            self._proc.stdout.close()


def _hash_len(repo: Path) -> int:
    fmt = _git_text(repo, "rev-parse", "--show-object-format", check=False).strip() or "sha1"
    return 32 if fmt == "sha256" else 20


def _write_object(repo: Path, kind: str, data: bytes) -> str:
    proc = _git(repo, "hash-object", "-t", kind, "-w", "--no-filters", "--stdin", input_bytes=data)
    return proc.stdout.decode("ascii").strip()


def _parse_tree(data: bytes, hlen: int) -> list[tuple[bytes, bytes, str]]:
    out: list[tuple[bytes, bytes, str]] = []
    i = 0
    while i < len(data):
        sp = data.index(b" ", i)
        nul = data.index(b"\0", sp)
        out.append((data[i:sp], data[sp + 1 : nul], data[nul + 1 : nul + 1 + hlen].hex()))
        i = nul + 1 + hlen
    return out


def _serialize_tree(entries: Sequence[tuple[bytes, bytes, str]]) -> bytes:
    return b"".join(mode + b" " + name + b"\0" + bytes.fromhex(sha) for mode, name, sha in entries)


def _split_commit(raw: bytes) -> tuple[str, list[str], list[bytes], bytes]:
    """(tree, parents, other header lines minus signatures, message)."""
    head, _, msg = raw.partition(b"\n\n")
    tree = ""
    parents: list[str] = []
    other: list[bytes] = []
    skipping = False
    for line in head.split(b"\n"):
        if line.startswith(b" "):
            if not skipping:
                other.append(line)
            continue
        key, _, value = line.partition(b" ")
        skipping = key in _DROP_HEADERS
        if key == b"tree":
            tree = value.decode("ascii")
        elif key == b"parent":
            parents.append(value.decode("ascii"))
        elif not skipping:
            other.append(line)
    return tree, parents, other, msg


def _join_commit(tree: str, parents: Sequence[str], other: Sequence[bytes], msg: bytes) -> bytes:
    lines = [b"tree " + tree.encode("ascii"), *(b"parent " + p.encode("ascii") for p in parents), *other]
    return b"\n".join(lines) + b"\n\n" + msg


def _commit_graph(repo: Path, extra_tips: Sequence[str]) -> list[tuple[str, list[str], int]]:
    """Every commit reachable from refs (+ worktree HEADs), parents first: (sha, parents, committer time)."""
    out = _git_text(
        repo,
        "rev-list",
        "--all",
        *extra_tips,
        "--topo-order",
        "--reverse",
        "--parents",
        "--timestamp",
        check=False,
    )
    graph: list[tuple[str, list[str], int]] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            graph.append((parts[1], parts[2:], int(parts[0])))
    return graph


@dataclass(frozen=True, slots=True)
class _Worktree:
    path: Path
    head: str | None
    branch: str | None


def _worktrees(repo: Path) -> list[_Worktree]:
    out = _git_text(repo, "worktree", "list", "--porcelain")
    trees: list[_Worktree] = []
    for block in out.strip().split("\n\n"):
        fields: dict[str, str] = {}
        bare = False
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            fields[key] = value
            bare = bare or key == "bare"
        if "worktree" in fields and not bare:
            trees.append(
                _Worktree(Path(fields["worktree"]), fields.get("HEAD") or None, fields.get("branch"))
            )
    return trees


def _refs(repo: Path, *, symrefs: bool = False) -> list[tuple[str, str, str]]:
    """(sha, type, name) of every ref; symbolic refs only when ``symrefs`` (then ONLY them)."""
    out = _git_text(repo, "for-each-ref", "--format=%(objectname)%00%(objecttype)%00%(symref)%00%(refname)")
    refs: list[tuple[str, str, str]] = []
    for line in out.splitlines():
        sha, kind, target, name = line.split("\0", 3)
        if bool(target) == symrefs:
            refs.append((sha, kind, name))
    return refs


def _preflight(repo: Path) -> list[_Worktree]:
    """Refuse a repo whose objects could survive gc (alternates, .keep packs) or with uncommitted work."""
    git_common = Path(_git_text(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    if (git_common / "objects" / "info" / "alternates").exists():
        raise GovernanceError(f"{repo}: uses objects/info/alternates; a purge cannot reach the shared store")
    packs = git_common / "objects" / "pack"
    if packs.is_dir() and any(p.suffix == ".keep" for p in packs.iterdir()):
        raise GovernanceError(f"{repo}: has .keep packs that gc never prunes; remove them first")
    trees = _worktrees(repo)
    for wt in trees:
        if not wt.path.is_dir():
            continue
        dirty = _git_text(wt.path, "status", "--porcelain=v1", "--untracked-files=all")
        if dirty.strip():
            raise GovernanceError(
                f"{wt.path}: uncommitted changes; run a sync cycle (its recovery restores generated paths) "
                "or commit/discard them first, so nothing unpurged is left in the index"
            )
    return trees


def _needle_regex(needles: Iterable[str]) -> re.Pattern[str] | None:
    uniq = sorted({n for n in needles if n}, key=lambda n: (-len(n), n))
    if not uniq:
        return None
    body = "|".join(re.escape(n) for n in uniq)
    return re.compile(f"(?<![{_PATH_CHARS}])(?:{body})(?![{_PATH_CHARS}])")


def _page_identity(data: bytes) -> dict[str, str] | None:
    """Frontmatter keys that identify a mirror page (hand parser matching frontmatter.render_frontmatter:
    bare scalars, or JSON-quoted free text); None when the blob is not a mirror page."""
    if not data.startswith(b"---\n"):
        return None
    end = data.find(b"\n---\n", 3)
    if end == -1:
        end = data.find(b"\n---", 3)
        if end == -1:
            return None
    out: dict[str, str] = {}
    for line in data[4:end].decode("utf-8", "replace").split("\n"):
        key, sep, value = line.partition(": ")
        if not sep or key not in _FM_KEYS:
            continue
        v = value.strip()
        if v.startswith('"'):
            with contextlib.suppress(ValueError):
                v = str(json.loads(v))
        out[key] = v
    return out if "source_id" in out and "stable_id" in out else None


# ---------------------------------------------------------------------------------------------------------
# the history rewriter
# ---------------------------------------------------------------------------------------------------------


_OLD_QUOTED_SPAN = re.compile(r"(?<!`)`([^`]+)`(?!`)")
"""A single-backtick code span, the only form ``publish._quoted`` wrote before B13 round 7 H37."""


class _Scrubber:
    """Line-level removal of every mention of a purged item from index-like text."""

    def __init__(
        self,
        docs_paths: Iterable[str],
        ids: Iterable[tuple[str, str]],
        rel_paths: Iterable[tuple[str, str]],
        *,
        standalone_rel_paths: bool,
        spare_ids: Iterable[tuple[str, str]] = (),
        live_paths: Collection[str] = (),
    ) -> None:
        self._ids = set(ids)
        self._spare = set(spare_ids)  # the live items now at a taken path: their own JSON lines stay
        docs_paths = list(docs_paths)
        self._paths = _needle_regex([*docs_paths, *(sid for _, sid in self._ids)])
        # B13 round 8 J50: an index line written before H37 quoted a path with an apostrophe for each
        # backtick (the old _quoted); that form is matched only inside such a quoted span, and never where it
        # is another item's live page (``live_paths``), so the lines of a file named with apostrophes stay.
        self._legacy = _needle_regex(
            v for p in docs_paths if "`" in p and (v := p.replace("`", "'")) not in live_paths
        )
        self._pairs: dict[str, re.Pattern[str]] = {}
        by_source: dict[str, set[str]] = {}
        for source_id, rel in rel_paths:
            # and as STATE.md quotes it (publish._quoted: whitespace runs collapsed, no unprintable character)
            shown = "".join(c for c in " ".join(rel.split()) if c.isprintable())
            by_source.setdefault(source_id, set()).update({rel, shown})
        for source_id, rels in by_source.items():
            rx = _needle_regex(rels)
            if rx is not None:
                self._pairs[source_id] = rx
        self._source_rx = {s: _needle_regex([s]) for s in self._pairs}
        self._standalone = (
            _needle_regex({r for rels in by_source.values() for r in rels}) if standalone_rel_paths else None
        )

    def line_hit(self, line: str) -> bool:
        """True when ``line`` names a purged item."""
        if self._spare and line.startswith("{"):
            with contextlib.suppress(ValueError):
                obj = json.loads(line)
                if (
                    isinstance(obj, dict)
                    and (str(obj.get("source_id")), str(obj.get("stable_id"))) in self._spare
                ):
                    return False
        if self._paths is not None and self._paths.search(line):
            return True
        if self._standalone is not None and self._standalone.search(line):
            return True
        if (
            self._legacy is not None
            and not line.startswith("{")
            and any(self._legacy.search(span) for span in _OLD_QUOTED_SPAN.findall(line))
        ):
            return True
        for source_id, rx in self._pairs.items():
            srx = self._source_rx[source_id]
            if srx is not None and srx.search(line) and rx.search(line):
                return True
        if line.startswith("{") and self._ids:
            with contextlib.suppress(ValueError):
                obj = json.loads(line)
                if (
                    isinstance(obj, dict)
                    and (str(obj.get("source_id")), str(obj.get("stable_id"))) in self._ids
                ):
                    return True
        return False

    def own_line_hit(self, source_id: str, line: str) -> bool:
        """True when ``line``, one of ``source_id``'s own with no source id on it (a carried STATE.md line),
        names a purged item: by a docs path or id, or by a rel path of that source only (B13 round 8 J46)."""
        if self._paths is not None and self._paths.search(line):
            return True
        rx = self._pairs.get(source_id)
        return rx is not None and rx.search(line) is not None

    def scrub(self, text: str) -> tuple[str, int]:
        """Return (text without the hit lines, number of lines removed)."""
        lines = text.split("\n")
        keep = [ln for ln in lines if not self.line_hit(ln)]
        return "\n".join(keep), len(lines) - len(keep)


@dataclass
class _Plan:
    """What pass 1 found in history."""

    ids: set[tuple[str, str]] = field(default_factory=set)
    target_blobs: set[str] = field(default_factory=set)
    dropped_paths: set[str] = field(default_factory=set)
    rel_paths: set[tuple[str, str]] = field(default_factory=set)
    canonical: set[str] = field(default_factory=set)
    rendered: set[str] = field(default_factory=set)
    explicit_seen: dict[str, set[str]] = field(default_factory=dict)  # docs path -> blobs ever there
    kept_at_explicit: set[tuple[str, str]] = field(default_factory=set)  # (path, blob) of another item


class _Rewriter:
    """Pass 1 (discover what to drop) and pass 2 (rewrite trees, blobs and commits) over one repo."""

    def __init__(
        self,
        repo: Path,
        cat: _CatFile,
        hlen: int,
        *,
        selector: PurgeSelector | None,
        explicit_paths: set[str],
        plan: _Plan,
        others: set[tuple[str, str]] | None = None,
    ) -> None:
        self.repo = repo
        self.cat = cat
        self.hlen = hlen
        self.selector = selector
        self.explicit = explicit_paths
        self.plan = plan
        self.others = others or set()
        self.scrubber: _Scrubber | None = None
        self.sha_remap: dict[str, str] = {}
        self._identity: dict[str, dict[str, str] | None] = {}
        self._tree_memo: dict[tuple[str, str], str | None] = {}
        self._blob_memo: dict[tuple[str, str], str] = {}
        self._walked: set[tuple[str, str]] = set()
        self.replaced_blobs: set[str] = set()
        self._hex_re = re.compile(rf"\b[0-9a-f]{{{hlen * 2}}}\b")

    # -- decisions -------------------------------------------------------------------------------------------

    def identity(self, sha: str) -> dict[str, str] | None:
        if sha not in self._identity:
            kind, data = self.cat.read(sha)
            self._identity[sha] = _page_identity(data) if kind == "blob" else None
        return self._identity[sha]

    def _is_target(self, path: str, sha: str) -> bool:
        sel = self.selector
        if sel is None:
            return False
        if sel.docs_glob and glob_match(path, sel.docs_glob):
            return True
        top = path.split("/", 1)[0]
        if top not in _PAGE_TOPS or not path.endswith(".md"):
            return False
        if sel.source_id is not None and not path.startswith(f"{top}/{sel.source_id}/"):
            return path in self.explicit and self.identity(sha) is None
        ident = self.identity(sha)
        if path in self.explicit:
            self.plan.explicit_seen.setdefault(path, set()).add(sha)
        if ident is None:
            return path in self.explicit
        key = (ident["source_id"], ident["stable_id"])
        if key in self.plan.ids or sel.matches_item(key[0], key[1], ident.get("source_path", "")):
            return True
        if (
            path in self.explicit
            and key not in self.others
            and (key[0], ident.get("source_path", "")) in self.plan.rel_paths
        ):
            # The same page path of the same source file under an id the manifest no longer knows: the item
            # before a safe-save rekey recorded without an alias (a manifest older than item_aliases).
            return True
        if path in self.explicit:
            self.plan.kept_at_explicit.add((path, sha))
        return False

    def _record_target(self, path: str, sha: str) -> None:
        self.plan.target_blobs.add(sha)
        self.plan.dropped_paths.add(path)
        ident = self.identity(sha) if path.endswith(".md") else None
        if ident is None:
            return
        self.plan.ids.add((ident["source_id"], ident["stable_id"]))
        if ident.get("source_path"):
            self.plan.rel_paths.add((ident["source_id"], ident["source_path"]))
        if ident.get("canonical_sha256"):
            self.plan.canonical.add(ident["canonical_sha256"])
        for key in ("rendered_sha256", "last_rendered_sha256"):
            if ident.get(key):
                self.plan.rendered.add(ident[key])

    def _drop_decisions(self, entries: Sequence[tuple[bytes, bytes, str]], prefix: str) -> set[bytes]:
        """Names in one directory to drop: target blobs, then the ``.files`` sidecar dirs of dropped pages."""
        dropped: set[bytes] = set()
        stems: set[bytes] = set()
        for mode, name, sha in entries:
            if mode in (_TREE_MODE, _GITLINK_MODE):
                continue
            path = prefix + name.decode("utf-8", "surrogateescape")
            if self._is_target(path, sha):
                dropped.add(name)
                self._record_target(path, sha)
                if name.endswith(b".md"):
                    stems.add(name[:-3])
        # a page this tree keeps (another item's, not targeted): its sidecar dir is that item's too
        kept_pages = {
            n[:-3] for m, n, _s in entries if m not in (_TREE_MODE, _GITLINK_MODE) and n.endswith(b".md")
        }
        kept_pages -= stems
        for mode, name, _sha in entries:
            if mode != _TREE_MODE or not name.endswith(_SIDECAR_SUFFIX.encode()):
                continue
            stem = name[: -len(_SIDECAR_SUFFIX)]
            page = prefix + stem.decode("utf-8", "surrogateescape") + ".md"
            if stem in stems or (page in self.explicit and stem not in kept_pages):
                dropped.add(name)
                self.plan.dropped_paths.add(prefix + name.decode("utf-8", "surrogateescape") + "/")
        return dropped

    # -- pass 1 ---------------------------------------------------------------------------------------------

    def walk(self, tree: str, prefix: str = "") -> None:
        key = (tree, prefix)
        if key in self._walked:
            return
        self._walked.add(key)
        _, data = self.cat.read(tree)
        entries = _parse_tree(data, self.hlen)
        dropped = self._drop_decisions(entries, prefix)
        for mode, name, sha in entries:
            if name in dropped:
                if mode == _TREE_MODE:
                    self._collect_subtree(sha, prefix + name.decode("utf-8", "surrogateescape") + "/")
                continue
            if mode == _TREE_MODE:
                self.walk(sha, prefix + name.decode("utf-8", "surrogateescape") + "/")

    def _collect_subtree(self, tree: str, prefix: str) -> None:
        """Every blob under a dropped sidecar dir is a target blob."""
        _, data = self.cat.read(tree)
        for mode, name, sha in _parse_tree(data, self.hlen):
            path = prefix + name.decode("utf-8", "surrogateescape")
            if mode == _TREE_MODE:
                self._collect_subtree(sha, path + "/")
            elif mode != _GITLINK_MODE:
                self.plan.target_blobs.add(sha)
                self.plan.dropped_paths.add(path)

    # -- pass 2 ---------------------------------------------------------------------------------------------

    def _transform_blob(self, path: str, sha: str) -> str:
        if path.startswith("topics/"):
            return sha
        kind = "mirror" if path.split("/", 1)[0] in _PAGE_TOPS else "index"
        memo_key = (kind, sha)
        if memo_key in self._blob_memo:
            return self._blob_memo[memo_key]
        new = sha
        if kind == "mirror":
            if self.sha_remap and path.endswith(".md"):
                _, data = self.cat.read(sha)
                if b"\nstatus: deleted\n" in data or b"\nstatus: archived\n" in data:
                    text = data.decode("utf-8", "surrogateescape")
                    out = self._hex_re.sub(lambda m: self.sha_remap.get(m.group(0), m.group(0)), text)
                    if out != text:
                        new = _write_object(self.repo, "blob", out.encode("utf-8", "surrogateescape"))
        elif self.scrubber is not None:
            _, data = self.cat.read(sha)
            if len(data) <= _SCRUB_MAX_BYTES and b"\0" not in data:
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    text = None
                if text is not None:
                    out, removed = self.scrubber.scrub(text)
                    if removed:
                        new = _write_object(self.repo, "blob", out.encode("utf-8"))
                        self.replaced_blobs.add(sha)
        self._blob_memo[memo_key] = new
        return new

    def rewrite_tree(self, tree: str, prefix: str = "") -> str | None:
        """Return the rewritten tree sha (None when every entry was dropped)."""
        key = (tree, prefix)
        if key in self._tree_memo:
            return self._tree_memo[key]
        _, data = self.cat.read(tree)
        entries = _parse_tree(data, self.hlen)
        dropped = self._drop_decisions(entries, prefix)
        out: list[tuple[bytes, bytes, str]] = []
        changed = bool(dropped)
        for mode, name, sha in entries:
            if name in dropped:
                continue
            path = prefix + name.decode("utf-8", "surrogateescape")
            if mode == _TREE_MODE:
                sub = self.rewrite_tree(sha, path + "/")
                if sub is None:
                    changed = True
                    continue
                changed = changed or sub != sha
                out.append((mode, name, sub))
            elif mode == _GITLINK_MODE:
                out.append((mode, name, sha))
            else:
                new = self._transform_blob(path, sha)
                changed = changed or new != sha
                out.append((mode, name, new))
        result: str | None
        if not changed:
            result = tree
        elif not out:
            result = None
        else:
            result = _write_object(self.repo, "tree", _serialize_tree(out))
        self._tree_memo[key] = result
        return result

    def root_tree(self, tree: str) -> str:
        new = self.rewrite_tree(tree)
        return new if new is not None else _write_object(self.repo, "tree", b"")


def _rewrite_tag(repo: Path, cat: _CatFile, sha: str, cmap: Mapping[str, str], memo: dict[str, str]) -> str:
    """Rewrite an annotated tag (chain) onto the mapped commit; signatures are dropped."""
    if sha in memo:
        return memo[sha]
    _, raw = cat.read(sha)
    head, _, msg = raw.partition(b"\n\n")
    lines = head.split(b"\n")
    target = lines[0].split(b" ", 1)[1].decode("ascii")
    kind = lines[1].split(b" ", 1)[1].decode("ascii") if len(lines) > 1 else "commit"
    new_target = _rewrite_tag(repo, cat, target, cmap, memo) if kind == "tag" else cmap.get(target, target)
    if new_target == target:
        memo[sha] = sha
        return sha
    for marker in _SIG_MARKERS:
        cut = msg.find(marker)
        if cut != -1:
            msg = msg[:cut]
    lines[0] = b"object " + new_target.encode("ascii")
    new = _write_object(repo, "tag", b"\n".join(lines) + b"\n\n" + msg)
    memo[sha] = new
    return new


@dataclass(frozen=True, slots=True)
class _RewriteResult:
    commit_map: dict[str, str]
    old_refs: dict[str, str]
    new_refs: dict[str, str]
    deleted_refs: tuple[str, ...]
    replaced_blobs: frozenset[str]


def _apply_rewrite(
    repo: Path,
    cat: _CatFile,
    rw: _Rewriter,
    graph: Sequence[tuple[str, list[str], int]],
    worktrees: Sequence[_Worktree],
    *,
    squash: set[str] | None = None,
    squash_base: str | None = None,
    squash_message: bytes = b"",
) -> _RewriteResult:
    """Pass 2: rewrite every commit parents-first, then move every ref atomically and reset each worktree."""
    cmap: dict[str, str] = {}
    new_root: str | None = None
    if squash and squash_base is not None:
        _, raw = cat.read(squash_base)
        tree, _parents, other, _msg = _split_commit(raw)
        new_root = _write_object(repo, "commit", _join_commit(rw.root_tree(tree), [], other, squash_message))
        for sha in squash:
            cmap[sha] = new_root
    for sha, parents, _ts in graph:
        if squash and sha in squash:
            continue
        _, raw = cat.read(sha)
        tree, _p, other, msg = _split_commit(raw)
        new_tree = rw.root_tree(tree)
        new_parents: list[str] = []
        for p in parents:
            mp = cmap.get(p, p)
            if mp not in new_parents:
                new_parents.append(mp)
        if new_tree == tree and new_parents == parents:
            cmap[sha] = sha
        else:
            cmap[sha] = _write_object(repo, "commit", _join_commit(new_tree, new_parents, other, msg))
        if cmap[sha] != sha:
            rw.sha_remap[sha] = cmap[sha]

    old_refs: dict[str, str] = {}
    new_refs: dict[str, str] = {}
    deleted: list[str] = []
    tag_memo: dict[str, str] = {}
    commands: list[str] = []
    for _sha, _kind, name in _refs(repo, symrefs=True):
        if name.startswith("refs/remotes/"):
            commands += ["option no-deref", f"delete {name}"]
    for sha, kind, name in _refs(repo):
        if name.startswith("refs/remotes/"):
            deleted.append(name)
            commands.append(f"delete {name} {sha}")
            old_refs[name] = sha
            continue
        if kind == "commit":
            new = cmap.get(sha, sha)
        elif kind == "tag":
            new = _rewrite_tag(repo, cat, sha, cmap, tag_memo)
        else:
            continue
        old_refs[name] = sha
        if new != sha:
            new_refs[name] = new
            commands.append(f"update {name} {new} {sha}")
    if commands:
        _git(repo, "update-ref", "--stdin", input_bytes=("\n".join(commands) + "\n").encode("utf-8"))
    for wt in worktrees:
        if wt.branch is None and wt.head and cmap.get(wt.head, wt.head) != wt.head and wt.path.is_dir():
            _git(wt.path, "update-ref", "--no-deref", "HEAD", cmap[wt.head], wt.head)
    git_dir = Path(_git_text(repo, "rev-parse", "--path-format=absolute", "--git-dir").strip())
    for name in ("ORIG_HEAD", "FETCH_HEAD", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REBASE_HEAD"):
        with contextlib.suppress(FileNotFoundError):
            (git_dir / name).unlink()
    return _RewriteResult(cmap, old_refs, new_refs, tuple(deleted), frozenset(rw.replaced_blobs))


def _owned_ignored(rel: str) -> bool:
    """An ignored file agentsync itself regenerates in the docs repo (publish.GITIGNORE)."""
    return rel == "_sync/STATE.md" or rel.startswith("_manifest/cache/")


def _reset_worktrees(
    worktrees: Sequence[_Worktree],
    scrubber: _Scrubber | None,
    *,
    owned: _Scrubber | None = None,
    state_sources: Collection[str] = (),
) -> list[str]:
    """Scrub ignored text files, then ``reset --hard`` each worktree onto its (rewritten) HEAD.

    agentsync's own ignored files take ``owned`` when given (B13 round 8 J48): STATE.md names a source's file
    by its rel path under a ``## <id>`` heading, so the line holds no source id for the paired scrubber to
    match. Every other ignored file (a user's own) keeps the paired scrubber, so a generic rel path cuts no
    line there. STATE.md goes section by section (B13 round 9 K51): under the ``## <id>`` of a source in
    ``state_sources`` a bare rel path counts only as that source's own, as in its carried lines, so another
    source's line naming the same rel path stays."""
    touched: list[str] = []
    for wt in worktrees:
        if not wt.path.is_dir():
            continue
        if scrubber is not None:
            ignored = _git_text(wt.path, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
            for rel in sorted(p for p in ignored.split("\0") if p):
                use = owned if owned is not None and _owned_ignored(rel) else scrubber
                scrub: Callable[[str], tuple[str, int]] = use.scrub
                if owned is not None and rel == "_sync/STATE.md":
                    scrub = _state_md_scrub(scrubber, owned, frozenset(state_sources))
                if _scrub_file(wt.path / rel, scrub):
                    touched.append(str(wt.path / rel))
        if _git(wt.path, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0:
            _git(wt.path, "reset", "--hard", "-q")
    return touched


def _expire_and_prune(repo: Path) -> None:
    _git(repo, "reflog", "expire", "--expire=now", "--expire-unreachable=now", "--all")
    _git(
        repo,
        "-c",
        "gc.autoDetach=false",
        "-c",
        "gc.cruftPacks=false",
        "-c",
        "gc.reflogExpire=now",
        "-c",
        "gc.reflogExpireUnreachable=now",
        "-c",
        "gc.pruneExpire=now",
        "gc",
        "--prune=now",
        "--quiet",
    )


def _in_head(repo: Path, path: str) -> bool:
    """True when ``HEAD:<path>`` exists."""
    return _git(repo, "cat-file", "-e", f"HEAD:{path}", check=False).returncode == 0


def _taken_paths(
    rw: _Rewriter, heads: Iterable[str], paths: Iterable[str]
) -> dict[str, set[tuple[str, str] | None]]:
    """The docs paths a worktree HEAD holds with a blob pass 1 did not target, each with the identities of the
    pages there, one per HEAD that differs (None for a page without one): another item's live page now at a
    path the purged item once had.  The rewrite keeps that blob, so the purge keeps its manifest rows and its
    index lines too, on every worktree's HEAD."""
    wanted = sorted({p for p in paths if p and not p.endswith("/")})
    out: dict[str, set[tuple[str, str] | None]] = {}
    if not wanted:
        return out
    trees: dict[str, dict[str, tuple[bytes, str]]] = {}

    def entries(tree: str) -> dict[str, tuple[bytes, str]]:
        if tree not in trees:
            _, data = rw.cat.read(tree)
            trees[tree] = {
                name.decode("utf-8", "surrogateescape"): (mode, sha)
                for mode, name, sha in _parse_tree(data, rw.hlen)
            }
        return trees[tree]

    for head in heads:
        _, raw = rw.cat.read(head)
        root = _split_commit(raw)[0]
        for path in wanted:
            sha: str | None = root
            mode = _TREE_MODE
            for part in path.split("/"):
                if sha is None or mode != _TREE_MODE:
                    sha = None
                    break
                mode, sha = entries(sha).get(part, (b"", None))
            if sha is None or mode in (_TREE_MODE, _GITLINK_MODE) or sha in rw.plan.target_blobs:
                continue
            ident = rw.identity(sha)
            key = (ident["source_id"], ident["stable_id"]) if ident else None
            if key is not None and key in rw.plan.ids:
                continue
            out.setdefault(path, set()).add(key)
    return out


def surviving_objects(repo: Path, shas: Iterable[str]) -> list[str]:
    """Return the objects of ``shas`` that ``git cat-file`` can still read (empty = all gone)."""
    wanted = sorted(set(shas))
    if not wanted:
        return []
    proc = _git(repo, "cat-file", "--batch-check", input_bytes=("\n".join(wanted) + "\n").encode("ascii"))
    out = proc.stdout.decode("ascii", "replace")
    return sorted(line.split()[0] for line in out.splitlines() if line and not line.endswith(" missing"))


def unreachable_objects(repo: Path) -> list[str]:
    """``git fsck --unreachable --no-reflogs`` object ids (should be empty after a purge or compaction)."""
    out = _git_text(repo, "fsck", "--unreachable", "--no-reflogs", "--no-progress", check=False)
    return sorted(
        line.split()[2]
        for line in out.splitlines()
        if line.startswith("unreachable ") and len(line.split()) >= 3
    )


def _reachable_blobs_at(repo: Path, shas: set[str]) -> dict[str, str]:
    """Where (one path) each of ``shas`` is still reachable, for a verification failure message."""
    out = _git_text(repo, "rev-list", "--objects", "--all", check=False)
    where: dict[str, str] = {}
    for line in out.splitlines():
        sha, _, path = line.partition(" ")
        if sha in shas and sha not in where:
            where[sha] = path
    return where


# ---------------------------------------------------------------------------------------------------------
# remote policy
# ---------------------------------------------------------------------------------------------------------


def _redact_url(url: str) -> str:
    return re.sub(r"(://)[^/@]+@", r"\1***@", url)


def _url_is_cloud_path(url: str) -> bool:
    if "://" in url and not url.startswith("file://"):
        return False
    local = url.removeprefix("file://")
    if ":" in local.split("/")[0]:
        return False
    return is_cloud_path(Path(local))


@dataclass(frozen=True, slots=True)
class _RemoteUrl:
    scheme: str  # https | http | ssh | git | file
    userinfo: str
    host: str  # lower-cased, with any port
    path: str  # normalised POSIX path, leading "/"


def _parse_remote(url: str) -> _RemoteUrl | None:
    """Parse a git remote URL: ``scheme://[user@]host[:port]/path``, scp-style ``[user@]host:path``, or a
    local path / ``file://`` URL.  None when it is none of those."""
    text = url.strip()
    if not text or any(c.isspace() or ord(c) < 32 for c in text):
        return None
    if "://" in text:
        parts = urllib.parse.urlsplit(text)
        scheme = parts.scheme.lower()
        if scheme == "file":
            return _RemoteUrl("file", "", "", posixpath.normpath("/" + parts.path.lstrip("/")))
        if scheme not in ("https", "http", "ssh", "git") or not parts.hostname:
            return None
        userinfo = parts.netloc.rpartition("@")[0] if "@" in parts.netloc else ""
        host = parts.hostname.lower() + (f":{parts.port}" if parts.port else "")
        path = posixpath.normpath("/" + parts.path.lstrip("/")) if parts.path.strip("/") else "/"
        return _RemoteUrl(scheme, userinfo, host, path)
    if text.startswith("/"):
        return _RemoteUrl("file", "", "", posixpath.normpath(text))
    head, sep, rest = text.partition(":")
    if sep and "/" not in head and head:
        userinfo, _, host = head.rpartition("@")
        if not host:
            return None
        path = posixpath.normpath("/" + rest.lstrip("/")) if rest.strip("/") else "/"
        return _RemoteUrl("ssh", userinfo, host.lower(), path)
    return None


def _remote_matches(url: str, prefix: str) -> bool:
    """Scheme, userinfo and host must be equal; the path must equal the prefix path or continue it at a
    ``/`` boundary (``https://git.contoso.com`` never allows ``git.contoso.com.attacker.net``, and
    ``https://github.com/contoso`` never allows ``github.com/contoso-personal``)."""
    u, p = _parse_remote(url), _parse_remote(prefix)
    if u is None or p is None or u.scheme != p.scheme or u.host != p.host or u.userinfo != p.userinfo:
        return False
    if p.path in ("/", u.path):
        return True
    base = p.path if p.path.endswith("/") else p.path + "/"
    return u.path.startswith(base)


def remote_allowed(url: str, gov: GovernanceConfig) -> bool:
    """True when ``allow_remote`` is on and ``url`` lies under a configured tenant-owned prefix: same scheme,
    userinfo and host, and a path at or below the prefix's at a ``/`` boundary (never a string prefix)."""
    if not gov.allow_remote or _url_is_cloud_path(url):
        return False
    return any(_remote_matches(url, p) for p in gov.remote_url_prefixes)


def remote_policy_findings(repo: Path, gov: GovernanceConfig) -> list[str]:
    """Blocking findings for every configured remote the policy does not allow (empty = compliant)."""
    findings: list[str] = []
    names = _git_text(repo, "remote").split()
    for name in sorted(names):
        url = _git_text(repo, "remote", "get-url", name, check=False).strip()
        shown = _redact_url(url)
        # Whose the fix is, in the finding itself: init's stderr and status's [FAIL] line print it, and an
        # imperative "run git ... remote remove" there read as the agent's step (B13 round 17 U2, U16)
        if not gov.allow_remote:
            findings.append(
                f"remote {name} ({shown}) exists but [governance] allow_remote = false: every clone flattens "
                f"the writer's ACLs and no purge can reach it; the person removes it (git -C {repo} remote "
                f"remove {name}) or allows it in [governance], not the agent"
            )
        elif not remote_allowed(url, gov):
            findings.append(
                f"remote {name} ({shown}) is not under a tenant-owned prefix in [governance] "
                "remote_url_prefixes (or is inside ~/Library/CloudStorage); the person removes it or names "
                "its tenant-owned prefix there, not the agent"
            )
    return findings


def push_rewritten(
    repo: Path, gov: GovernanceConfig, old_refs: Mapping[str, str], *, remote: str = "origin"
) -> str:
    """Force-push rewritten branches/tags with a lease on their pre-rewrite values, only when the policy
    allows
    the remote; returns ``disabled`` | ``no-remote`` | ``refused: …`` | ``pushed N refs to <remote>``
    (``1 ref`` for one; B13 round 18 V90)."""
    if not gov.allow_remote:
        return "disabled"
    names = _git_text(repo, "remote").split()
    if remote not in names:
        return "no-remote"
    url = _git_text(repo, "remote", "get-url", remote).strip()
    if not remote_allowed(url, gov):
        return f"refused: remote {remote} ({_redact_url(url)}) is not tenant-owned per remote_url_prefixes"
    args: list[str] = []
    refspecs: list[str] = []
    current = {name: sha for sha, _kind, name in _refs(repo)}
    for ref in sorted(current):
        if not ref.startswith(("refs/heads/", "refs/tags/")):
            continue
        tracking = f"refs/remotes/{remote}/{ref.removeprefix('refs/heads/')}"
        lease = old_refs.get(tracking) if ref.startswith("refs/heads/") else None
        args.append(f"--force-with-lease={ref}:{lease}" if lease else f"--force-with-lease={ref}")
        refspecs.append(f"+{current[ref]}:{ref}")
    if not refspecs:
        return "refused: nothing to push"
    _git(repo, "push", "--porcelain", "--quiet", *args, remote, *refspecs)
    log.warning(
        "governance: force-pushed %d ref(s) to %s; the host still keeps unreachable objects until ITS gc "
        "(GitHub/Azure DevOps: ask the admin) and every existing clone keeps the old history",
        len(refspecs),
        remote,
    )
    return f"pushed {plural(len(refspecs), 'ref')} to {remote}"


# ---------------------------------------------------------------------------------------------------------
# purge
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PurgeReport:
    """What a purge found and did; ``verified`` is True only when no targeted object survives."""

    selector: str
    reason: PurgeReason
    dry_run: bool
    items: tuple[tuple[str, str], ...]
    docs_paths: tuple[str, ...]
    commits_rewritten: int = 0
    blobs_targeted: int = 0
    survivors: tuple[str, ...] = ()
    unreachable_left: int = 0
    cache_entries_removed: int = 0
    manifest_rows_removed: Mapping[str, int] = field(default_factory=dict)
    files_scrubbed: tuple[str, ...] = ()
    citing_pages: tuple[str, ...] = ()
    remote: str = "disabled"
    notes: tuple[str, ...] = ()
    paths_left: tuple[str, ...] = ()  # extension: purged items' page paths still in HEAD (or never targeted)
    taken_paths: tuple[str, ...] = ()  # extension: targeted docs paths another item's live page holds in HEAD
    not_run: str = ""  # extension: why the queue did not run this entry (it stays queued); "" when it ran

    @property
    def verified(self) -> bool:
        """True when this was a real run and nothing targeted remains readable in the repo."""
        return (
            not self.dry_run
            and not self.not_run
            and not self.survivors
            and self.unreachable_left == 0
            and not self.paths_left
        )


@dataclass(frozen=True, slots=True)
class _ManifestTargets:
    items: list[tuple[str, str, str, bool]]  # source_id, stable_id, rel_path, is_dir
    docs_paths: set[str]
    action_keys: set[str]
    canonical: set[str]
    rendered: set[str]
    teams_months: list[tuple[str, str]]
    aliases: set[tuple[str, str]] = field(default_factory=set)  # retired ids of the selected items
    # every id (and retired id) of a file the manifest lists that is not selected: never another item's
    # earlier id for the rewriter's same-path rule
    others: set[tuple[str, str]] = field(default_factory=set)


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


@dataclass(frozen=True, slots=True)
class _ManifestRead:
    """One read of the manifest tables a purge resolves its selector against (:func:`_read_manifest`)."""

    rows: list[sqlite3.Row]
    outputs: list[sqlite3.Row]
    tombs: list[sqlite3.Row]
    alias_to_current: dict[tuple[str, str], str]  # retired id -> current id
    aliases_of: dict[tuple[str, str], set[str]]  # current id -> retired ids


def _read_manifest(conn: sqlite3.Connection) -> _ManifestRead:
    rows = conn.execute(
        "SELECT i.source_id, i.stable_id, i.rel_path, i.is_dir, i.canonical_sha256, s.kind, i.state "
        "FROM items i LEFT JOIN sources s ON s.source_id = i.source_id ORDER BY i.source_id, i.stable_id"
    ).fetchall()
    outputs = conn.execute(
        "SELECT output_path, source_id, stable_id, action_key, rendered_sha256 FROM outputs"
    ).fetchall()
    tombs = conn.execute(
        "SELECT output_path, source_id, stable_id, last_rendered_sha256 FROM tombstones"
    ).fetchall()
    # item_aliases: every id an item carried before a safe-save rekey (current id <- retired ids)
    alias_to_current: dict[tuple[str, str], str] = {}
    aliases_of: dict[tuple[str, str], set[str]] = {}
    has_aliases = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'item_aliases'"
    ).fetchone()
    if has_aliases is not None:
        for a in conn.execute("SELECT source_id, alias_id, stable_id FROM item_aliases").fetchall():
            alias_to_current[(a["source_id"], a["alias_id"])] = a["stable_id"]
            aliases_of.setdefault((a["source_id"], a["stable_id"]), set()).add(a["alias_id"])
    return _ManifestRead(rows, outputs, tombs, alias_to_current, aliases_of)


def _resolve_manifest(db: Path, selector: PurgeSelector, ids: set[tuple[str, str]]) -> _ManifestTargets:
    """Read-only: the manifest rows the selector (or already-known ids) names."""
    if not db.exists():
        return _ManifestTargets([], set(), set(), set(), set(), [])
    Manifest(db).close()  # schema/version check; raises ManifestSchemaError on a mismatch
    conn = _connect(db)
    try:
        return _resolve_read(conn, _read_manifest(conn), selector, ids)
    finally:
        conn.close()


def _resolve_read(
    conn: sqlite3.Connection, m: _ManifestRead, selector: PurgeSelector, ids: set[tuple[str, str]]
) -> _ManifestTargets:
    """:func:`_resolve_manifest` over a read already made (``conn`` serves the cache lookup)."""
    rows, outputs, tombs = m.rows, m.outputs, m.tombs
    alias_to_current, aliases_of = m.alias_to_current, m.aliases_of
    items: list[tuple[str, str, str, bool]] = []
    selected: set[tuple[str, str]] = set(ids)
    selected |= {(s, alias_to_current[(s, i)]) for s, i in ids if (s, i) in alias_to_current}
    if selector.stable_id:
        for (s, alias), current in alias_to_current.items():
            if alias == selector.stable_id and selector.source_id in (None, s):
                selected.add((s, current))
    if selector.docs_glob:
        for r in [*outputs, *tombs]:
            if glob_match(r["output_path"], selector.docs_glob) and (
                selector.source_id is None or r["source_id"] == selector.source_id
            ):
                selected.add((r["source_id"], r["stable_id"]))
    canonical: set[str] = set()
    teams: list[tuple[str, str]] = []
    for r in rows:
        key = (r["source_id"], r["stable_id"])
        hit = key in selected or (
            not r["is_dir"]
            and any(
                selector.matches_item(r["source_id"], sid, r["rel_path"])
                for sid in (r["stable_id"], *sorted(aliases_of.get(key, ())))
            )
        )
        dir_hit = (
            bool(r["is_dir"])
            and selector.path_glob is not None
            and selector.matches_item(r["source_id"], r["stable_id"], r["rel_path"])
        )
        if not hit and not dir_hit:
            continue
        items.append((r["source_id"], r["stable_id"], r["rel_path"], bool(r["is_dir"])))
        if hit:
            selected.add(key)
        if r["canonical_sha256"]:
            canonical.add(r["canonical_sha256"])
        month = r["stable_id"].rsplit(":", 1)[-1]
        if r["kind"] == "graph_teams" and re.fullmatch(r"\d{4}-\d{2}", month):
            teams.append((r["source_id"], month))
    docs_paths: set[str] = set()
    keys: set[str] = set()
    rendered: set[str] = set()
    for r in outputs:
        if (r["source_id"], r["stable_id"]) in selected:
            docs_paths.add(r["output_path"])
            if r["action_key"]:
                keys.add(r["action_key"])
            if r["rendered_sha256"]:
                rendered.add(r["rendered_sha256"])
    for r in tombs:
        if (r["source_id"], r["stable_id"]) in selected:
            docs_paths.add(r["output_path"])
            if r["last_rendered_sha256"]:
                rendered.add(r["last_rendered_sha256"])
    if canonical:
        marks = ",".join("?" * len(canonical))
        for r in conn.execute(
            f"SELECT action_key FROM cache WHERE canonical_sha256 IN ({marks})",
            sorted(canonical),
        ):
            keys.add(r["action_key"])
    aliases = {(s, a) for (s, i) in selected for a in aliases_of.get((s, i), ())}
    others = {
        k
        for r in rows
        if r["state"] != "tombstone" and (r["source_id"], r["stable_id"]) not in selected
        for k in [
            (r["source_id"], r["stable_id"]),
            *((r["source_id"], a) for a in aliases_of.get((r["source_id"], r["stable_id"]), ())),
        ]
    }
    return _ManifestTargets(items, docs_paths, keys, canonical, rendered, teams, aliases, others)


def _manifest_apply(
    db: Path,
    targets: Sequence[tuple[str, str, str, bool]],
    docs_paths: set[str],
    *,
    action_keys: set[str],
    scrubber: _Scrubber,
    commit_map: Mapping[str, str],
    squashed: set[str],
    tree_sha: str | None,
    keep_owners: Collection[tuple[str, str]] = frozenset(),
    line_scrubber: _Scrubber | None = None,
) -> dict[str, int]:
    """Delete the purged rows (secure_delete, checkpoint, VACUUM) and remap commit shas; return row counts.

    An outputs row at one of ``docs_paths`` whose owner is in ``keep_owners`` (a file the manifest lists that
    the purge did not select) stays: its page may be missing from HEAD after a crash between the manifest
    write and the commit, and the next cycle's repair republishes it from that row.

    With ``line_scrubber`` (a purge's, B13 round 8 J46), each alarm and error line STATE.md carries for a
    source a run did not select (meta ``state_md_lines``) that it hits becomes ``[purged]``, in this
    transaction so the old page is overwritten and VACUUMed; a value that cannot be read is deleted."""
    counts = {
        "items": 0,
        "outputs": 0,
        "tombstones": 0,
        "depends": 0,
        "cache": 0,
        "run_sources": 0,
        "meta": 0,
    }
    if not db.exists():
        return counts
    conn = _connect(db)
    try:
        conn.execute("PRAGMA secure_delete=ON")
        has_aliases = (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'item_aliases'"
            ).fetchone()
            is not None
        )
        conn.execute("BEGIN IMMEDIATE")
        try:
            files = [(s, i) for s, i, _r, d in targets if not d]
            for source_id, stable_id in files:
                for table in ("outputs", "tombstones"):
                    counts[table] += conn.execute(
                        f"DELETE FROM {table} WHERE source_id = ? AND stable_id = ?",
                        (source_id, stable_id),
                    ).rowcount
                counts["items"] += conn.execute(
                    "DELETE FROM items WHERE source_id = ? AND stable_id = ? AND is_dir = 0",
                    (source_id, stable_id),
                ).rowcount
                if has_aliases:
                    conn.execute(
                        "DELETE FROM item_aliases WHERE source_id = ? AND (stable_id = ? OR alias_id = ?)",
                        (source_id, stable_id, stable_id),
                    )
            for path in sorted(docs_paths):
                for r in conn.execute(
                    "SELECT source_id, stable_id FROM outputs WHERE output_path = ?", (path,)
                ).fetchall():
                    if (r["source_id"], r["stable_id"]) not in keep_owners:
                        counts["outputs"] += conn.execute(
                            "DELETE FROM outputs WHERE output_path = ? AND source_id = ? AND stable_id = ?",
                            (path, r["source_id"], r["stable_id"]),
                        ).rowcount
                counts["tombstones"] += conn.execute(
                    "DELETE FROM tombstones WHERE output_path = ?", (path,)
                ).rowcount
                counts["depends"] += conn.execute("DELETE FROM depends WHERE source = ?", (path,)).rowcount
            for key in sorted(action_keys):
                counts["cache"] += conn.execute("DELETE FROM cache WHERE action_key = ?", (key,)).rowcount
            dirs = sorted(((s, i, r) for s, i, r, d in targets if d), key=lambda t: -t[2].count("/"))
            for source_id, stable_id, _rel in dirs:
                child = conn.execute(
                    "SELECT 1 FROM items WHERE source_id = ? AND parent_id = ? LIMIT 1",
                    (source_id, stable_id),
                ).fetchone()
                if child is None:
                    counts["items"] += conn.execute(
                        "DELETE FROM items WHERE source_id = ? AND stable_id = ?", (source_id, stable_id)
                    ).rowcount
            for r in conn.execute(
                "SELECT run_id, source_id, error, skipped_reason FROM run_sources "
                "WHERE error IS NOT NULL OR skipped_reason IS NOT NULL"
            ).fetchall():
                err = _PURGED_MARK if r["error"] and scrubber.line_hit(r["error"]) else r["error"]
                why = (
                    _PURGED_MARK
                    if r["skipped_reason"] and scrubber.line_hit(r["skipped_reason"])
                    else r["skipped_reason"]
                )
                if err != r["error"] or why != r["skipped_reason"]:
                    conn.execute(
                        "UPDATE run_sources SET error = ?, skipped_reason = ? "
                        "WHERE run_id = ? AND source_id = ?",
                        (err, why, r["run_id"], r["source_id"]),
                    )
                    counts["run_sources"] += 1
            if line_scrubber is not None:
                counts["meta"] += _scrub_carried_lines(conn, line_scrubber)
            for r in conn.execute(
                "SELECT output_path, last_commit FROM tombstones WHERE last_commit IS NOT NULL"
            ):
                old = r["last_commit"]
                new = None if old in squashed else commit_map.get(old, old)
                if new != old:
                    conn.execute(
                        "UPDATE tombstones SET last_commit = ? WHERE output_path = ?", (new, r["output_path"])
                    )
            for old, new in commit_map.items():
                if old != new:
                    conn.execute("UPDATE runs SET commit_sha = ? WHERE commit_sha = ?", (new, old))
            if tree_sha is not None and conn.execute("SELECT 1 FROM meta WHERE key = 'tree_sha'").fetchone():
                conn.execute("UPDATE meta SET value = ? WHERE key = 'tree_sha'", (tree_sha,))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return counts


def _cache_entries(cache_root: Path, keys: set[str], rendered: set[str]) -> list[Path]:
    """Cache entry dirs to delete: by action key, or holding a unit whose body hash is a purged H2.  The
    piece store (``recordings/``) is not a shard: :func:`_piece_victims` covers it."""
    out: set[Path] = set()
    if not cache_root.is_dir():
        return []
    pieces = PieceStore.under(cache_root).root
    for shard in sorted(cache_root.iterdir()):
        if not shard.is_dir() or shard.is_symlink() or shard == pieces:
            continue
        if shard.name.startswith("tmp-"):
            out.add(shard)
            continue
        for entry in sorted(shard.iterdir()):
            if entry.name in keys:
                out.add(entry)
                continue
            if not rendered:
                continue
            try:
                meta = json.loads((entry / "result.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            units = meta.get("units", []) if isinstance(meta, dict) else []
            if any(isinstance(u, dict) and u.get("body_sha256") in rendered for u in units):
                out.add(entry)
    return sorted(out)


def _piece_victims(cache_root: Path, canonical: set[str]) -> list[str]:
    """The recordings in the piece store (spec 4.1) whose canonical hash is a purged item's."""
    return sorted(set(PieceStore.under(cache_root).pending()) & canonical)


def _staged_hashes(db: Path, items: Iterable[tuple[str, str, str, bool]]) -> set[str]:
    """The canonical hashes the purged recordings' unfinished reads were staged under (``staged`` in the
    manifest meta ``recording:<source id>``): a first read killed in its transaction leaves its pieces under
    a hash its row does not hold."""
    files = [(s, i) for s, i, _r, d in items if not d]
    if not files or not db.exists():
        return set()
    conn = _connect(db)
    try:
        out: set[str] = set()
        for source_id in sorted({s for s, _i in files}):
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (f"recording:{source_id}",)).fetchone()
            try:
                doc = json.loads(row["value"]) if row is not None and row["value"] else {}
            except ValueError:
                doc = {}
            staged = doc.get("staged") if isinstance(doc, dict) else None
            if isinstance(staged, dict):
                out |= {str(staged[i]) for s, i in files if s == source_id and staged.get(i)}
        return out
    finally:
        conn.close()


_CARRIED_LINES_META = "state_md_lines"  # cycle._STATE_LINES_META: {source id: [run id, alarms, errors]}


def _scrub_carried_lines(conn: sqlite3.Connection, scrubber: _Scrubber) -> int:
    """Mark each carried STATE.md line ``scrubber`` hits for its own source as ``[purged]`` (the decoded
    strings, since JSON escapes quotes and non-ASCII); another source's alarm stays. A value that is not the
    stored shape is deleted, an entry that is not and names the item is emptied. 1 if the key changed."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (_CARRIED_LINES_META,)).fetchone()
    if row is None:
        return 0
    try:
        loaded = json.loads(row[0])
    except ValueError:
        loaded = None
    if not isinstance(loaded, dict):
        conn.execute("DELETE FROM meta WHERE key = ?", (_CARRIED_LINES_META,))
        return 1
    changed = False
    for sid, entry in loaded.items():
        if isinstance(entry, list) and len(entry) == 3 and all(isinstance(e, list) for e in entry[1:]):
            new = [
                entry[0],
                *(
                    [
                        _PURGED_MARK if isinstance(x, str) and scrubber.own_line_hit(sid, x) else x
                        for x in lines
                    ]
                    for lines in entry[1:]
                ),
            ]
            changed = changed or new != entry
            loaded[sid] = new
        elif any(scrubber.own_line_hit(sid, x) for x in _strings(entry)):
            loaded[sid] = None  # not the shape a cycle reads; it carries nothing, and now names nothing
            changed = True
    if not changed:
        return 0
    conn.execute("UPDATE meta SET value = ? WHERE key = ?", (json.dumps(loaded), _CARRIED_LINES_META))
    return 1


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list | dict):
        for v in value.values() if isinstance(value, dict) else value:
            yield from _strings(v)


def _state_md_scrub(
    paired: _Scrubber, log: _Scrubber, sources: frozenset[str]
) -> Callable[[str], tuple[str, int]]:
    """STATE.md's scrub, section by section (B13 round 9 K51): under the ``## <id>`` of a configured source a
    line goes when the paired scrubber hits it or it names a purged file of that source by its bare rel path
    (``own_line_hit``, as its carried lines are scrubbed); under any other heading ``log`` decides alone."""

    def scrub(text: str) -> tuple[str, int]:
        lines = text.split("\n")
        keep: list[str] = []
        section: str | None = None
        for line in lines:
            if line.startswith("## "):
                section = line[3:]
            if section in sources:
                hit = paired.line_hit(line) or log.own_line_hit(section, line)
            else:
                hit = log.line_hit(line)
            if not hit:
                keep.append(line)
        return "\n".join(keep), len(lines) - len(keep)

    return scrub


def _scrub_file(path: Path, scrub: Callable[[str], tuple[str, int]]) -> bool:
    """Remove hit lines from one text file in place (0600 tmp + rename); True if changed. ``scrub`` is a
    scrubber's :meth:`_Scrubber.scrub`, or STATE.md's (:func:`_state_md_scrub`)."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _SCRUB_MAX_BYTES:
            return False
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    out, removed = scrub(text)
    if not removed:
        return False
    tmp = path.with_name(f".{path.name}.scrub.tmp")
    tmp.write_text(out, encoding="utf-8")
    tmp.chmod(path.stat().st_mode & 0o777)
    tmp.replace(path)
    return True


def _scrub_logs(dirs: Iterable[Path], scrubber: _Scrubber) -> tuple[list[str], list[str]]:
    """Scrub every regular text file under the log dirs; returns (scrubbed, unscrubbable binary files)."""
    scrubbed: list[str] = []
    skipped: list[str] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file() or p.is_symlink():
                continue
            if _scrub_file(p, scrubber.scrub):
                scrubbed.append(str(p))
            elif p.suffix in {".gz", ".bz2", ".zip", ".xz"}:
                skipped.append(str(p))
    return scrubbed, skipped


def _rmtree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _topics_citing(repo: Path, db: Path, docs_paths: set[str]) -> list[str]:
    """Curated pages that still cite a purged page (DEPENDS rows, or its file name in the page text): a human
    must rewrite them, because their prose may be derived from the purged content."""
    if not docs_paths:
        return []
    out: set[str] = set()
    if db.exists():
        conn = _connect(db)
        try:
            for path in sorted(docs_paths):
                out |= {r["page"] for r in conn.execute("SELECT page FROM depends WHERE source = ?", (path,))}
        finally:
            conn.close()
    topics = repo / "topics"
    names = sorted({p.rstrip("/").rsplit("/", 1)[-1] for p in docs_paths}, key=lambda n: (-len(n), n))
    if topics.is_dir() and names:
        body = "|".join(re.escape(n) for n in names)
        rx = re.compile(rf"(?<![A-Za-z0-9._\-])(?:{body})(?![A-Za-z0-9._/\-])")
        for p in sorted(topics.rglob("*.md")):
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if rx.search(text):
                out.add(p.relative_to(repo).as_posix())
    return sorted(out)


@contextlib.contextmanager
def _maybe_lock(state_paths: StatePaths, lock: bool) -> Iterator[None]:
    if not lock:
        yield
        return
    with SingleWriterLock(state_paths.lock, _LOCK_LABEL):
        yield


def purge(
    config: Config,
    selector: PurgeSelector,
    *,
    reason: PurgeReason,
    gov: GovernanceConfig | None = None,
    dry_run: bool = False,
    push: bool = False,
    lock: bool = True,
    now: datetime | None = None,
    ids: Iterable[tuple[str, str]] = (),
) -> PurgeReport:
    """Remove the selected items from the docs repo's whole history, the manifest (and any pre-migration
    ``<db>.pre-v*`` copy), the converter cache, the Teams store, staging and logs; expire reflogs, prune, and
    verify no targeted blob survives.

    Refused (HoldActiveError) while a hold covers an affected source.  ``dry_run`` reports without writing.
    ``push`` force-pushes the rewritten refs only when ``allow_remote`` and a tenant-owned remote exist.
    ``ids`` (source, stable id) are targeted beside the selector: a queued entry's stored aliases, so the
    history walk finds pages kept under an id no manifest links any more (B13 round 28 I93, I109).
    The returned report's ``verified`` must be True; the CLI exits non-zero otherwise.
    """
    gov = gov or load_governance(config.config_path)
    repo = expand(config.docs_repo)
    sp = config.state_paths
    _refuse_bad_dir(governance_dir(sp.root))  # before the rewrite, dry run too (B13 round 28 I88)
    if not dry_run and governance_dir(sp.root).is_dir():
        # the audit trail opened first, as a hold opens it (A79): one that cannot be opened stops the purge
        # before anything is erased, never a rewrite left with no audit line (I88). Only in a governance/
        # that is there: never one made up while the real folder is away, which would stand in its way
        with _audit_appender(sp.root):
            pass
    with _maybe_lock(sp, lock and not dry_run):
        report = _purge_locked(
            config,
            repo,
            sp,
            selector=selector,
            reason=reason,
            gov=gov,
            dry_run=dry_run,
            push=push,
            now=now,
            ids=set(ids),
        )
        if report.verified:  # never on a dry run
            _drop_covered(sp.root, selector, report)
        return report


def _purge_locked(
    config: Config,
    repo: Path,
    sp: StatePaths,
    *,
    selector: PurgeSelector,
    reason: PurgeReason,
    gov: GovernanceConfig,
    dry_run: bool,
    push: bool,
    now: datetime | None,
    ids: set[tuple[str, str]] | None = None,
) -> PurgeReport:
    has_repo = (repo / ".git").exists()
    seed = set(ids or ())  # a queued entry's stored aliases (B13 round 28 I93, I109)
    mt = _resolve_manifest(sp.db, selector, seed)
    plan = _Plan(ids={(s, i) for s, i, _r, d in mt.items if not d} | mt.aliases | seed)
    plan.rel_paths |= {(s, r) for s, _i, r, d in mt.items if not d}
    worktrees: list[_Worktree] = []
    taken: dict[str, set[tuple[str, str] | None]] = {}
    legacy_live: set[str] = set()  # B13 round 8 J50: an old-quoted form of a purged path that is a live page
    graph: list[tuple[str, list[str], int]] = []
    hlen = 20
    cat: _CatFile | None = None
    try:
        if has_repo:
            worktrees = _worktrees(repo) if dry_run else _preflight(repo)
            hlen = _hash_len(repo)
            graph = _commit_graph(repo, [w.head for w in worktrees if w.head])
            cat = _CatFile(repo)
            # pass 1 to a fixpoint: a glob match reveals an id whose other (renamed) paths are targets too,
            # and that id's manifest rows make its page paths explicit for the next walk, as pass 2 sees them
            for _ in range(4):
                before = (len(plan.ids), len(plan.target_blobs), len(mt.docs_paths))
                rw = _Rewriter(
                    repo,
                    cat,
                    hlen,
                    selector=selector,
                    explicit_paths=mt.docs_paths,
                    plan=plan,
                    others=mt.others,
                )
                for _sha, _parents, _ts in graph:
                    _, raw = cat.read(_sha)
                    rw.walk(_split_commit(raw)[0])
                if plan.ids - {(s, i) for s, i, _r, d in mt.items if not d} - mt.aliases:
                    mt = _resolve_manifest(sp.db, selector, plan.ids)
                    plan.ids |= {(s, i) for s, i, _r, d in mt.items if not d} | mt.aliases
                if (len(plan.ids), len(plan.target_blobs), len(mt.docs_paths)) == before:
                    # pass 1 decided what pass 2 will: a blob it did not target at a docs path stays in HEAD
                    taken = _taken_paths(
                        rw, [w.head for w in worktrees if w.head], mt.docs_paths | plan.dropped_paths
                    )
                    legacy_live = set(
                        _taken_paths(
                            rw,
                            [w.head for w in worktrees if w.head],
                            {p.replace("`", "'") for p in mt.docs_paths | plan.dropped_paths if "`" in p},
                        )
                    )
                    break
        affected_sources = sorted(
            {s for s, _ in plan.ids} | ({selector.source_id} if selector.source_id else set())
        )
        holds = blocking_holds(active_holds(sp.root, gov), affected_sources or None)
        _refuse_if_held(holds, "purge")
        docs_paths = sorted(mt.docs_paths | plan.dropped_paths)
        rel_paths = plan.rel_paths | {(s, r) for s, _i, r, _d in mt.items}
        scrubber = _Scrubber(
            docs_paths, plan.ids, rel_paths, standalone_rel_paths=False, live_paths=legacy_live
        )
        # The history's index files keep the lines of a live page at a taken path: by its path, and its own
        # manifest line by its id.  Logs, run errors and ignored files are scrubbed by every path; logs,
        # agentsync's own ignored files (STATE.md) and STATE.md's carried lines also by a bare rel path.
        index_scrubber = _Scrubber(
            [p for p in docs_paths if p not in taken],
            plan.ids,
            rel_paths,
            standalone_rel_paths=False,
            spare_ids={k for ks in taken.values() for k in ks if k is not None},
            live_paths=legacy_live,
        )
        log_scrubber = _Scrubber(
            docs_paths, plan.ids, rel_paths, standalone_rel_paths=True, live_paths=legacy_live
        )
        cache_victims = _cache_entries(expand(config.cache_dir), mt.action_keys, mt.rendered | plan.rendered)
        piece_victims = _piece_victims(
            expand(config.cache_dir), mt.canonical | plan.canonical | _staged_hashes(sp.db, mt.items)
        )
        citing = _topics_citing(repo, sp.db, set(docs_paths))
        items = tuple(sorted(plan.ids))
        if dry_run:
            # the scrubber's needles are the docs paths, the ids and the source paths: with none of them and
            # no blob, no commit changes
            if plan.target_blobs or docs_paths or plan.ids or rel_paths:
                note = f"up to {plural(len(graph), 'commit')} would be rewritten"
            else:
                note = "nothing is targeted: no commit would be rewritten"
            return PurgeReport(
                selector=selector.kind(),
                reason=reason,
                dry_run=True,
                items=items,
                docs_paths=tuple(docs_paths),
                blobs_targeted=len(plan.target_blobs),
                cache_entries_removed=len(cache_victims) + len(piece_victims),
                citing_pages=tuple(citing),
                remote="dry-run",
                notes=(note,),
                taken_paths=tuple(sorted(taken)),
            )
        at = _iso(_now(now))
        notes: list[str] = []
        paths_left: list[str] = []
        result: _RewriteResult | None = None
        survivors: list[str] = []
        unreachable: list[str] = []
        head_tree: str | None = None
        if has_repo and cat is not None and graph:
            rw = _Rewriter(
                repo, cat, hlen, selector=selector, explicit_paths=mt.docs_paths, plan=plan, others=mt.others
            )
            rw.scrubber = index_scrubber
            result = _apply_rewrite(repo, cat, rw, graph, worktrees)
            cat.close()
            cat = None
            touched = _reset_worktrees(
                worktrees, scrubber, owned=log_scrubber, state_sources={s.id for s in config.sources}
            )
            notes += [f"scrubbed ignored file {t}" for t in touched]
            _expire_and_prune(repo)
            targets = plan.target_blobs | result.replaced_blobs
            survivors = surviving_objects(repo, targets)
            unreachable = unreachable_objects(repo)
            head_tree = gitops.head_tree_sha(repo)
            if survivors:
                where = _reachable_blobs_at(repo, set(survivors))
                notes += [
                    f"blob {s[:12]} still reachable at {where.get(s, '(unreferenced)')}" for s in survivors
                ]
            # Belt and braces: a page the manifest attributes to a purged item must be gone from HEAD, and
            # an item whose page paths held blobs in history cannot be "verified" with nothing targeted.
            paths_left = [p for p in sorted(mt.docs_paths) if _in_head(repo, p)]
            notes += [f"{p} still exists in HEAD" for p in paths_left]
            seen = {p for p, blobs in plan.explicit_seen.items() if blobs}
            if seen and not plan.target_blobs:
                paths_left += sorted(seen - set(paths_left))
                notes.append("the item's page paths hold history blobs but none was targeted")
            notes += [
                f"kept blob {b[:12]} at {p}: it names another item (not purged)"
                for p, b in sorted(plan.kept_at_explicit)
                if b not in plan.target_blobs
            ]
            if result.deleted_refs:
                notes.append(
                    f"deleted {plural(len(result.deleted_refs), 'remote-tracking ref')}; "
                    "remotes are NOT purged"
                )
        cmap = result.commit_map if result else {}
        rows = _manifest_apply(
            sp.db,
            mt.items,
            set(docs_paths) - set(taken),
            action_keys=set(mt.action_keys),
            scrubber=scrubber,
            commit_map=cmap,
            squashed=set(),
            tree_sha=head_tree,
            keep_owners=mt.others - plan.ids,
            line_scrubber=log_scrubber,
        )
        for entry in cache_victims:
            _rmtree(entry)
        store = PieceStore.under(expand(config.cache_dir))
        for canonical_sha in piece_victims:
            store.remove(canonical_sha)
        for backup in migration_backups(sp.db):  # a pre-migration copy still holds the purged rows
            backup.unlink(missing_ok=True)
            notes.append(f"deleted the pre-migration manifest copy {backup.name}")
        for source_id, month in mt.teams_months:
            with contextlib.suppress(FileNotFoundError):
                (sp.teams_store / source_id / f"{month}.json").unlink()
        if sp.staging.is_dir():
            for child in sp.staging.iterdir():
                _rmtree(child)
        scrubbed, unscrubbable = _scrub_logs([expand(config.log_dir), sp.logs], log_scrubber)
        notes += [f"cannot scrub compressed log {p}: delete it" for p in unscrubbable]
        remote = "disabled"
        if has_repo:
            names = _git_text(repo, "remote").split()
            if push and result is not None:
                remote = push_rewritten(repo, gov, result.old_refs)
            elif names:
                remote = f"not pushed: remote(s) {', '.join(sorted(names))} still hold the purged history"
            else:
                remote = "no-remote"
        if reason is not PurgeReason.UPSTREAM_DELETED:
            # Erasure / DLP / label escalation / operator: the content must not come back while it exists
            # upstream -- by id (every alias), by exact source path (a safe-save gets a new id at the same
            # path) and by canonical hash (a copy under another name).
            globs = [(selector.source_id or "", selector.path_glob)] if selector.path_glob else []
            _add_suppressions(
                sp.root,
                plan.ids,
                globs,
                paths=sorted({(s, r) for s, _i, r, d in mt.items if not d} | plan.rel_paths),
                canonical=sorted(mt.canonical | plan.canonical),
            )
        report = PurgeReport(
            selector=selector.kind(),
            reason=reason,
            dry_run=False,
            items=items,
            docs_paths=tuple(docs_paths),
            commits_rewritten=sum(1 for o, n in cmap.items() if o != n),
            blobs_targeted=len(plan.target_blobs),
            survivors=tuple(survivors),
            unreachable_left=len(unreachable),
            paths_left=tuple(paths_left),
            cache_entries_removed=len(cache_victims) + len(piece_victims),
            manifest_rows_removed=rows,
            files_scrubbed=tuple(scrubbed),
            citing_pages=tuple(citing),
            remote=remote,
            notes=tuple(notes),
            taken_paths=tuple(sorted(taken)),
        )
        _audit_purge(
            sp.root,
            at=at,
            selector=selector,
            reason=reason,
            items=mt.items,
            plan=plan,
            docs_paths=docs_paths,
            report=report,
        )
        if not report.verified:
            log.error(
                "governance: purge NOT verified: %d surviving object(s)", len(survivors) + len(unreachable)
            )
        return report
    finally:
        if cat is not None:
            cat.close()


def _audit_purge(
    state_dir: Path,
    *,
    at: str,
    selector: PurgeSelector,
    reason: PurgeReason,
    items: Sequence[tuple[str, str, str, bool]],
    plan: _Plan,
    docs_paths: Sequence[str],
    report: PurgeReport,
) -> None:
    rel_by_id = {(s, i): r for s, i, r, _d in items}
    for source_id, stable_id in sorted(plan.ids):
        rel = rel_by_id.get((source_id, stable_id))
        append_audit(
            state_dir,
            {
                "action": "purge",
                "at": at,
                "path_sha256": _sha(rel) if rel is not None else None,
                "reason": reason.value,
                "source_id": source_id,
                "stable_id_sha256": _sha(stable_id),
            },
        )
    append_audit(
        state_dir,
        {
            "action": "purge-summary",
            "at": at,
            "blobs": report.blobs_targeted,
            "commits_rewritten": report.commits_rewritten,
            "docs_path_sha256": sorted(_sha(p) for p in docs_paths),
            "reason": reason.value,
            "selector_kind": selector.kind(),
            "selector_sha256": selector.fingerprint(),
            "verified": report.verified,
        },
    )


_LISTED_STATES = ("live", "dataless")
"""Row states of a file the source still has (``RowState.LIVE``, ``RowState.DATALESS``)."""


KEPT_REASONS = (PurgeReason.UPSTREAM_DELETED, PurgeReason.LABEL_ESCALATION)
"""The reasons :func:`_kept_why` judges: the cycle queues both for a file that may be listed again later."""


def unjudged_note(check: str = "state_dir.manifest") -> str:
    """Why the queue keeps an ``upstream-deleted`` or ``label-escalation`` entry while the manifest cannot be
    read (:func:`_unjudged`): run blind, it erased a current page and its history (B13 round 26 E25; ruling
    14). ``check`` is the status line that FAILs the state file there (:func:`_unjudged_check`): a 0000
    manifest's is state_dir.files, never a state_dir.manifest line status does not print (B13 round 27 P24,
    P46; ruling 17)."""
    return (
        "not judged: agentsync's state file is missing or unreadable, so nothing says whether its file is "
        f"listed or published again; it stays queued until the {check} [FAIL] line is fixed"
    )


UNJUDGED_NOTE = unjudged_note()
""":func:`unjudged_note` of a manifest lost, not a file or damaged (its state_dir.manifest FAIL)."""

UNJUDGED_PURGES = (
    "queued purges wait on agentsync's state file: the state_dir.manifest [FAIL] line says what to do first; "
    f"`{AGENTSYNC_BIN} purge --queue` refuses until then"
)
"""The note after a count of two or more :func:`unjudged_note` entries in the loop, status, doctor and
STATE.md (:func:`unjudged_purges`): no wait and no invitation to ``purge --queue``, which refuses over a lost,
misplaced or unreadable manifest (ruling 14; B13 round 27 P24), and never :data:`KEPT_ESCALATIONS`' words,
which claim a label policy no read confirmed."""


def unjudged_purges(n: int, check: str = "state_dir.manifest") -> str:
    """``n`` :func:`unjudged_note` entries, counted with agreement ("1 queued purge waits", never "1 queued
    purge(s) wait": B13 round 27 P118), naming ``check``, the FAIL that is there (P24, P46)."""
    if n == 1:
        return (
            f"1 queued purge waits on agentsync's state file: the {check} [FAIL] line says what to do first; "
            f"`{AGENTSYNC_BIN} purge --queue` refuses until then"
        )
    return f"{n} {UNJUDGED_PURGES}".replace("state_dir.manifest", check, 1)


NEW_START_NOTE = (
    "not judged yet: no sync has built the manifest, so nothing says whether its file is listed or published "
    "again; it stays queued until the next sync builds the manifest"
)
"""Why the queue keeps an ``upstream-deleted`` or ``label-escalation`` entry with no manifest there and none
lost: the person's new start (heartbeat.json moved aside), since only a sync with a manifest queues these, so
an earlier manifest judged it. Run before the next sync, a purge erased a page whose file was still listed
(B13 round 27 P40; ruling 17)."""


def new_start_purges(n: int) -> str:
    """``n`` :data:`NEW_START_NOTE` entries: no wait and no invitation, the next sync first (P40)."""
    waits, them = ("waits", "it") if n == 1 else ("wait", "them")
    return (
        f"{plural(n, 'queued purge')} {waits} for the next sync to build the manifest (a new start): "
        f"`{AGENTSYNC_BIN} purge --queue` leaves {them} queued until then; run `{AGENTSYNC_BIN} sync` first"
    )


UNRESOLVED_HEAD = "kept until its id is found: "
"""The first words of :func:`unresolved_note`, by which :func:`kept_lines` and the CLI count it."""


def unresolved_note(q: QueuedPurge) -> str:
    """Why the queue keeps an ``upstream-deleted`` or ``label-escalation`` entry when the current manifest
    holds nothing for its source, id and stored aliases (no row, page, tombstone or alias): queued before a
    new start replaced the manifest that named it, or found by the queue's own run to reach nothing in the
    docs history (:func:`run_purge_queue`).  Run blind, it targeted nothing, read VERIFIED and dropped its
    only record, while the page's history stayed under the id the lost manifest's alias had linked (B13 round
    27 P112; ruling 17).  ``purge --queue`` looks for its id in history and runs it once it finds one (B13
    round 28 I93).  By hand, a purge by the page's own id keeps another file's live page at that path; a
    purge by the path erases every page ever there, a restored file's live page too, so it is the last
    resort."""
    where = f"mirror/{q.selector.source_id}/" if q.selector.source_id else "mirror/"
    return (
        f"{UNRESOLVED_HEAD}nothing in the manifest names it (a new start replaced the one that recorded its "
        "file's page, or an older copy was put back), so `purge --queue` keeps it until it finds its id in "
        "the docs history; if none is found it stays queued. To erase the page by hand, find it with "
        f"`git log --all --name-only -- {where}` in the docs repo and run "
        f"`{AGENTSYNC_BIN} purge id=<its id> --source <its source>` with the id in the page's front matter, "
        f"which keeps another file's live page at that path; `{AGENTSYNC_BIN} purge docs=<page path>` is "
        "the last resort, for a page that id run targets nothing of"
    )


def unresolved_purges(n: int) -> str:
    """``n`` :func:`unresolved_note` entries, counted with agreement (P112, P118; B13 round 28 I93)."""
    is_, its, it, stays = ("is", "its", "it", "stays") if n == 1 else ("are", "their", "they", "stay")
    return (
        f"{plural(n, 'queued purge')} {is_} kept until `{AGENTSYNC_BIN} purge --queue` finds {its} id in the "
        f"docs history; if none is found {it} {stays} queued (nothing in the manifest names {it} after a new "
        f"start or a restored copy); `{AGENTSYNC_BIN} purge id=<its id> --source <its source>` erases a page "
        "by hand"
    )


def _unjudged_check(db: Path) -> str:
    """The status line that FAILs a manifest no queued purge can be judged over: state_dir.files for a
    regular file this account cannot read and write (its mode), else state_dir.manifest (lost, not a file,
    damaged) (B13 round 27 P24: the words named a state_dir.manifest line status never printed)."""
    try:
        st = db.stat()
    except OSError:
        return "state_dir.manifest"
    if stat.S_ISREG(st.st_mode) and not os.access(db, os.R_OK | os.W_OK):
        return "state_dir.files"
    from agentsync.ops import doctor  # noqa: PLC0415 - doctor imports this module (through cycle)

    if doctor.side_not_a_file_at(db) is not None:  # a -wal or -shm that is not a file: its files line
        return "state_dir.files"
    return "state_dir.manifest"


def _unjudged(db: Path) -> bool:
    """Whether no queued ``upstream-deleted`` or ``label-escalation`` entry can be judged because the manifest
    is lost from a Mac that has synced (``manifest.manifest_lost``), or something other than a regular file
    is there (a folder, a fifo, a socket, a link loop or through a file, a link to nothing whose folder no
    sync can use), judged by stat so nothing opens a fifo. A plain missing manifest with no heartbeat record,
    or a link to nothing in a folder the next sync creates the file through, is the person's new start
    (:data:`NEW_START_NOTE`; B13 round 26 E25, round 27 P40, P46)."""
    if manifest_lost(db):
        return True
    try:
        st = db.stat()
    except FileNotFoundError:
        if not os.path.lexists(db):
            return False
        from agentsync.ops import doctor  # noqa: PLC0415 - doctor imports this module (through cycle)

        return doctor.dangling_into_unusable(db) is not None
    except OSError:  # a loop, a link through a file, a folder above it this account cannot search
        return True
    return not stat.S_ISREG(st.st_mode)


def _first_run_started(conn: sqlite3.Connection) -> str | None:
    """``started_at`` of this manifest's first run, or None when it records none."""
    row = conn.execute("SELECT MIN(started_at) FROM runs").fetchone()
    return None if row is None or row[0] is None else str(row[0])


def _queued_before(q: QueuedPurge, started: str | None) -> bool:
    """Whether ``q`` was queued before ``started`` (a manifest's first run), both UTC ISO-8601; False when
    either is missing or does not parse."""
    if not started or not q.enqueued_at:
        return False
    try:
        queued = datetime.fromisoformat(q.enqueued_at.replace("Z", "+00:00"))
        first = datetime.fromisoformat(started.replace("Z", "+00:00"))
    except ValueError:
        return False
    # a stamp with no zone (hand-edited, another writer) is UTC, as every stamp agentsync writes is: comparing
    # a naive one with an aware one raised TypeError through status and the loop (P112's reviewer)
    queued, first = (t if t.tzinfo else t.replace(tzinfo=_UTC) for t in (queued, first))
    return queued < first


def _unresolved(conn: sqlite3.Connection, m: _ManifestRead, q: QueuedPurge, started: str | None) -> bool:
    """Whether ``q``, an entry the cycle queues (a source + id selector), was queued before this manifest's
    first run, or the queue's last run found nothing its id reaches (``found_nothing``), and the manifest
    holds nothing for it or its stored aliases: no row, page, tombstone or alias (:func:`_resolve_read`), so
    no manifest read says what a run targets (B13 round 27 P112). An entry queued by this manifest's own runs
    always has its row or tombstone here. Read-only and no git: status and the loop judge by it, and only
    :func:`run_purge_queue` looks for the id in history (B13 round 28 I93, I109)."""
    sel = q.selector
    if q.reason not in KEPT_REASONS or not (sel.source_id and sel.stable_id) or sel.docs_glob:
        return False
    if not (q.found_nothing or _queued_before(q, started)):
        return False
    mt = _resolve_read(conn, m, sel, _stored_ids(q))
    return not mt.items and not mt.docs_paths and not mt.aliases


def _stored_ids(q: QueuedPurge) -> set[tuple[str, str]]:
    """``q``'s stored aliases as (source, id), the ids a purge of it targets beside its selector."""
    sid = q.selector.source_id
    return {(sid, a) for a in q.aliases} if sid else set()


def _kept_queued(db: Path, q: QueuedPurge) -> str:
    """Why the queue must not run ``q`` now, or "" (:func:`_kept_why` over a fresh read of the manifest);
    :func:`unjudged_note` when the manifest is lost, not a file or unreadable, :data:`NEW_START_NOTE` when
    there is none (the new start), :func:`unresolved_note` when the manifest that replaced the one it was
    queued under holds nothing for it, never "", which ran the entry blind (B13 round 26 E25; round 27 P40,
    P112)."""
    if q.reason not in KEPT_REASONS:
        return ""
    db = expand(db)
    if _unjudged(db):
        return unjudged_note(_unjudged_check(db))
    if not os.path.exists(db):  # noqa: PTH110 - never Path.exists, which raises in a shut folder
        return NEW_START_NOTE
    try:
        Manifest(db).close()  # schema/version check; raises ManifestSchemaError on a mismatch
        conn = _connect(db)
        try:
            if _first_run_started(conn) is None:
                # a manifest no sync has run over (add-source, init or a dry run made it after the new start)
                # is the new start's, never one that holds no such file (B13 round 28 I61)
                return NEW_START_NOTE
            return _kept_why(conn, _read_manifest(conn), q)
        finally:
            conn.close()
    except (AgentSyncError, sqlite3.Error):  # its cause is said once per run (_unjudged_cause; P25)
        return unjudged_note(_unjudged_check(db))


def _unjudged_cause(db: Path) -> str:
    """Why no queued purge could be judged, in words, the file by its name: never a selector's repr or the
    full path under the home, which a WARNING per entry printed twice over (B13 round 27 P25)."""
    name = db.name
    try:
        st = db.stat()
    except FileNotFoundError:
        return f"{name} in the state folder is missing"
    except OSError as exc:
        return f"{name} in the state folder cannot be reached ({(exc.strerror or 'refused').lower()})"
    if not stat.S_ISREG(st.st_mode):
        return f"{name} in the state folder is not a regular file"
    try:
        Manifest(db).close()
    except (AgentSyncError, sqlite3.Error) as exc:
        cause = exc.__cause__ if isinstance(exc.__cause__, OSError) else exc
        if isinstance(cause, OSError) and cause.strerror:
            return f"{name} in the state folder cannot be opened ({cause.strerror.lower()})"
        if isinstance(exc, sqlite3.Error):
            return f"{name} in the state folder cannot be read ({exc})"
        return f"{name} in the state folder cannot be read"
    return f"{name} in the state folder cannot be read"


def _kept_why(
    conn: sqlite3.Connection,
    m: _ManifestRead,
    q: QueuedPurge,
    state: Mapping[tuple[str, str], str] | None = None,
) -> str:
    """Why the queue must not run ``q`` now, or "".

    An ``upstream-deleted`` entry is queued as its row becomes a tombstone, and a file that came back is
    listed again: under the queued id, or under a later one the queued id is an alias of (a re-key), which a
    purge follows.  Run, the entry would erase that file's page and history.  A ``label-escalation`` entry is
    queued as its row becomes refused, and a file the label policy no longer refuses (the rule was loosened,
    or the label taken off) is published again, its row live or online-only (B13 round 6 G30).  Run, the
    entry would erase that current page and suppress the file, so it never syncs again.  A row that is
    quarantined or refused does not stop either: after a file comes back refused, the queued purge is the
    only thing that erases its earlier text from history, so a rule added back makes the entry runnable
    again.  DLP, erasure and operator entries erase live content on purpose and are never judged here.
    ``state`` (each row's state) saves a caller that judges many entries a pass over the rows per entry."""
    if q.reason not in KEPT_REASONS:
        return ""
    if state is None:
        state = {(r["source_id"], r["stable_id"]): r["state"] for r in m.rows}
    # a row under a stored alias is the same file: a restored older manifest lists it under its retired id,
    # and a run seeded with that id would erase its page (B13 round 28 I109)
    mt = _resolve_read(conn, m, q.selector, _stored_ids(q))
    listed = [(s, i) for s, i, _r, is_dir in mt.items if not is_dir and state.get((s, i)) in _LISTED_STATES]
    note = _kept_note(q, listed)
    if not note and _unresolved(conn, m, q, _first_run_started(conn)):  # B13 round 27 P112
        note = unresolved_note(q)
    return note


def _kept_listed(
    s: str,
    sid: str,
    info: Mapping[tuple[str, str], tuple[str, bool]],
    alias_to_current: Mapping[tuple[str, str], str],
    aliases: Sequence[str] = (),
) -> list[tuple[str, str]]:
    """The listed files a ``source_id`` + ``stable_id`` selector (no ``docs_glob``) names, by lookup: the rows
    :func:`_resolve_read` selects for that shape are the queued id and the id it is an alias of (a re-key),
    and the same for each of the entry's stored ``aliases`` (B13 round 28 I109), and nothing else.  ``info``
    maps each row to (state, is_dir)."""
    cands: list[tuple[str, str]] = []
    for i in (sid, *aliases):
        for k in ((s, i), (s, alias_to_current.get((s, i), i))):
            if k not in cands:
                cands.append(k)
    return [k for k in cands if k in info and not info[k][1] and info[k][0] in _LISTED_STATES]


def _kept_note(q: QueuedPurge, listed: Sequence[tuple[str, str]]) -> str:
    """:func:`_kept_why`'s note for the listed files ``q`` names, or "" when none is listed."""
    if not listed:
        return ""
    sid = q.selector.stable_id
    earlier = sum(1 for _s, i in listed if i in q.aliases and i != sid)  # a restored copy's row (I109)
    later = sum(1 for _s, i in listed if i != sid and i not in q.aliases) if sid else 0
    via = "".join(
        f", {n} of them under {when} id" for n, when in ((later, "a later"), (earlier, "an earlier")) if n
    )
    rows = plural(len(listed), "row")
    if q.reason is PurgeReason.LABEL_ESCALATION:
        return (
            f"stays queued: the label policy no longer refuses the file and its page is current again "
            f"({rows}{via}); running it would erase that page and stop the file syncing. "
            "`purge --queue` skips it while the file is published, and runs it if the policy refuses the "
            f"file again; `{AGENTSYNC_BIN} purge path=<its path> --source <its source>` erases it anyway"
        )
    return (
        f"stays queued: the manifest lists the file as live or online-only ({rows}{via}); "
        "an upstream deletion is not run against a listed file; a later `purge --queue` runs it once the "
        "file is gone"
    )


_SAID_UNJUDGED: set[tuple[str, str]] = set()
"""The (manifest path, cause) pairs :func:`partition_purges` warned of in this process (B13 round 28 I43)."""


def partition_purges(
    state_paths: StatePaths, queue: Sequence[QueuedPurge] | None = None
) -> tuple[list[QueuedPurge], list[tuple[QueuedPurge, str]]]:
    """The queued purges (``queue``, default the queue file) a ``purge --queue`` would run, and the ones it
    keeps queued with why: an upstream deletion of a file the manifest lists again, or a label escalation of a
    file the policy publishes again (:func:`_kept_why`, the rule the run itself applies).  Read-only, the
    manifest is read once, and an entry the cycle queues (a source + id selector) is judged by lookup, so the
    cost is rows + entries, not their product.  A manifest that is lost, not a file or unreadable keeps those
    entries with :data:`UNJUDGED_NOTE`, never runnable (B13 round 26 E25: run blind, one erased a current
    page and its history); and with the manifest lost ``purge`` refuses outright, so every other entry is kept
    with that note too and nothing invites the run (ruling 14)."""
    entries = pending_purges(state_paths.root) if queue is None else list(queue)
    db = expand(state_paths.db)
    if entries and manifest_lost(db):
        return [], [(q, UNJUDGED_NOTE) for q in entries]
    if all(q.reason not in KEPT_REASONS for q in entries):
        return entries, []
    if _unjudged(db):
        return _unjudged_split(entries, unjudged_note(_unjudged_check(db)))
    if not os.path.exists(db):  # noqa: PTH110 - the new start: judged by the next sync's manifest (P40)
        return _unjudged_split(entries, NEW_START_NOTE)
    try:
        Manifest(db).close()
        conn = _connect(db)
        try:
            started = _first_run_started(conn)
            if started is None:  # no sync has run over it: the new start's (B13 round 28 I61)
                return _unjudged_split(entries, NEW_START_NOTE)
            m = _read_manifest(conn)
            # One pass over the rows for every entry: the cycle queues one source + id selector per confirmed
            # deletion, so a moved folder fills the queue, and status, doctor, the loop and STATE.md read it.
            info = {(r["source_id"], r["stable_id"]): (r["state"], bool(r["is_dir"])) for r in m.rows}
            state = {k: st for k, (st, _d) in info.items()}
            whys: list[str] = []
            for q in entries:
                sel = q.selector
                if q.reason in KEPT_REASONS and sel.source_id and sel.stable_id and not sel.docs_glob:
                    why = _kept_note(
                        q, _kept_listed(sel.source_id, sel.stable_id, info, m.alias_to_current, q.aliases)
                    )
                    if not why and not any((sel.source_id, i) in info for i in (sel.stable_id, *q.aliases)):
                        # no row under its id or a stored alias (a row: resolvable; P112, I109)
                        why = unresolved_note(q) if _unresolved(conn, m, q, started) else ""
                    whys.append(why)
                else:
                    whys.append(_kept_why(conn, m, q, state))
        finally:
            conn.close()
    except (AgentSyncError, sqlite3.Error):
        cause = _unjudged_cause(db)
        key = (str(db), cause)
        # once per manifest and cause in a process: status reads the queue in four places, and printed the
        # same timestamped WARNING four times before its NEXT (B13 round 28 I43); the rest at DEBUG
        (log.debug if key in _SAID_UNJUDGED else log.warning)(
            "governance: cannot tell which queued purges stay queued: %s", cause
        )
        _SAID_UNJUDGED.add(key)
        return _unjudged_split(entries, unjudged_note(_unjudged_check(db)))
    return [q for q, why in zip(entries, whys, strict=True) if not why], [
        (q, why) for q, why in zip(entries, whys, strict=True) if why
    ]


KEPT_PURGES = (
    "upstream deletions stay queued while their file is listed again; once the file is gone, they wait for "
    f"`{AGENTSYNC_BIN} purge --queue` like any queued purge"
)
"""The note after a count of two or more kept upstream deletions (:func:`partition_purges`) in the loop,
status, doctor and STATE.md (:func:`kept_purges`): no ``purge --queue`` clears them while their file is
listed, so they are no wait until their file is gone.  Nothing runs them on its own: the history rewrite runs
only from ``agentsync purge --queue``."""


def kept_purges(n: int) -> str:
    """``n`` kept upstream deletions, counted with agreement (B13 round 27 P118: "1 upstream deletion(s)
    stay queued")."""
    if n == 1:
        return (
            "1 upstream deletion stays queued while its file is listed again; once the file is gone, it "
            f"waits for `{AGENTSYNC_BIN} purge --queue` like any queued purge"
        )
    return f"{n} {KEPT_PURGES}"


KEPT_ESCALATIONS = (
    "label-escalation purges stay queued while the label policy lets their file publish again: "
    f"`{AGENTSYNC_BIN} purge --queue` skips them, since running one would erase a current page; if the "
    "policy refuses the file again, they wait like any queued purge"
)
""":data:`KEPT_PURGES` for kept label-escalation entries (B13 round 6 G30): the file is published again, not
gone, so the upstream wording would tell the reader the wrong thing."""


def kept_escalations(n: int) -> str:
    """``n`` kept label escalations, counted with agreement (P118)."""
    if n == 1:
        return (
            "1 label-escalation purge stays queued while the label policy lets its file publish again: "
            f"`{AGENTSYNC_BIN} purge --queue` skips it, since running it would erase a current page; if the "
            "policy refuses the file again, it waits like any queued purge"
        )
    return f"{n} {KEPT_ESCALATIONS}"


def _unjudged_split(
    entries: Sequence[QueuedPurge], note: str = UNJUDGED_NOTE
) -> tuple[list[QueuedPurge], list[tuple[QueuedPurge, str]]]:
    """:func:`partition_purges` when the manifest cannot be read, or is not there yet: DLP, erasure and
    operator entries stay runnable, the ones it judges are kept with ``note`` (B13 round 26 E25)."""
    return [q for q in entries if q.reason not in KEPT_REASONS], [
        (q, note) for q in entries if q.reason in KEPT_REASONS
    ]


_UNJUDGED_CHECK = re.compile(r"until the (\S+) \[FAIL\] line is fixed$")


def held_purges(
    state_dir: Path, gov: GovernanceConfig, queued: Sequence[QueuedPurge]
) -> tuple[list[QueuedPurge], list[tuple[QueuedPurge, list[Hold]]]]:
    """``queued`` (the runnable entries of :func:`partition_purges`) split by the holds in force: the ones a
    ``purge --queue`` would run, and the ones a hold suspends, each with its holds. A hold on ``all`` holds
    every entry; an entry for one source, the holds on it; a sourceless ``docs=`` or ``path=`` entry under a
    hold on one source only stays runnable, as ``purge`` judges it by the items it resolves (B13 round 28
    I91: under a hold on all, status, the sync and the warn invited a ``purge --queue`` that refused, preview
    included). Raises what :func:`active_holds` raises; the caller's hold-list-unread branch comes first."""
    holds = active_holds(state_dir, gov)
    runnable: list[QueuedPurge] = []
    held: list[tuple[QueuedPurge, list[Hold]]] = []
    for q in queued:
        sid = q.selector.source_id
        blocking = blocking_holds(holds, [sid] if sid else [])
        if blocking:
            held.append((q, blocking))
        else:
            runnable.append(q)
    return runnable, held


def held_words(held: Sequence[tuple[QueuedPurge, list[Hold]]]) -> str:
    """The note for the entries a hold suspends (:func:`held_purges`): they wait on the hold, and run once
    its records owner releases it, with that release's command; a note, never a WAITING ON YOU, since the
    next move is the records owner's, and never a ``purge --queue`` (B13 round 28 I91)."""
    scopes = sorted({h.scope for _q, hs in held for h in hs if h.origin != "config"})
    config = any(h.origin == "config" for _q, hs in held for h in hs)
    how = [f"`{AGENTSYNC_BIN} hold {s} --release --owner <name>`" for s in scopes]
    if config:
        how.append("hold = false in sources.toml's [governance]")
    n = len(held)
    verb = "waits" if n == 1 else "wait"
    them = "it runs" if n == 1 else "they run"
    return (
        f"{plural(n, 'queued purge')} {verb} on the hold above; {them} once its records owner releases it "
        f"({' or '.join(how)})"
    )


PURGE_REASON_WORDS = {
    PurgeReason.UPSTREAM_DELETED: "a confirmed upstream deletion",
    PurgeReason.LABEL_ESCALATION: "a label escalation",
    PurgeReason.DLP_REMEDIATION: "a published credential",
    PurgeReason.ERASURE_REQUEST: "an erasure request",
    PurgeReason.OPERATOR: "an operator's request",
}
"""What each queued reason is, in the purge queue's warn (B13 round 28 I91)."""


def reason_counts(queued: Sequence[QueuedPurge]) -> str:
    """One count per reason actually queued, e.g. ``1 dlp-remediation: a published credential``, never a
    fixed list that misnamed a queued DLP purge (B13 round 28 I91)."""
    counts: dict[PurgeReason, int] = {}
    for q in queued:
        counts[q.reason] = counts.get(q.reason, 0) + 1
    return ", ".join(f"{n} {r.value}: {PURGE_REASON_WORDS[r]}" for r, n in sorted(counts.items()))


def kept_lines(kept: Sequence[tuple[QueuedPurge, str]]) -> list[str]:
    """The kept entries of :func:`partition_purges` as one counted line per kind: the upstream deletions
    (:func:`kept_purges`) apart from the label escalations (:func:`kept_escalations`), the ones no manifest
    read judged (:func:`unjudged_purges`, naming the FAIL their notes name), the new start's
    (:func:`new_start_purges`) and the unresolvable ones (:func:`unresolved_purges`) apart from all (B13 round
    26 E25; round 27 P40, P112, P118)."""
    unjudged = [why for _q, why in kept if _UNJUDGED_CHECK.search(why) and why.startswith("not judged: ")]
    new_start = sum(1 for _q, why in kept if why == NEW_START_NOTE)
    unresolved = sum(1 for _q, why in kept if why.startswith(UNRESOLVED_HEAD))
    judged = [
        (q, why)
        for q, why in kept
        if why not in unjudged and why != NEW_START_NOTE and not why.startswith(UNRESOLVED_HEAD)
    ]
    escalations = sum(1 for q, _why in judged if q.reason is PurgeReason.LABEL_ESCALATION)
    upstream = len(judged) - escalations
    match = _UNJUDGED_CHECK.search(unjudged[0]) if unjudged else None
    check = match.group(1) if match else "state_dir.manifest"
    return [
        *([kept_purges(upstream)] if upstream else []),
        *([kept_escalations(escalations)] if escalations else []),
        *([unjudged_purges(len(unjudged), check)] if unjudged else []),
        *([new_start_purges(new_start)] if new_start else []),
        *([unresolved_purges(unresolved)] if unresolved else []),
    ]


def _probed(q: QueuedPurge) -> bool:
    """Whether the queue's run resolves ``q`` dry before it runs it: an entry the cycle queues for a file that
    may come back (an ``upstream-deleted`` or ``label-escalation`` source + id selector), whose manifest row a
    new start or a restored older copy may have lost (B13 round 28 I93, I109)."""
    sel = q.selector
    return q.reason in KEPT_REASONS and bool(sel.source_id and sel.stable_id) and not sel.docs_glob


def _targets_nothing(rep: PurgeReport, seed: set[tuple[str, str]]) -> bool:
    """Whether a dry run of a queued entry found nothing to erase: no blob, no docs path and no item beyond
    the stored aliases it was seeded with.  Run, it read VERIFIED having erased nothing and dropped its only
    record (B13 round 27 P112), so it stays queued with :func:`unresolved_note` instead (B13 round 28 I93)."""
    return rep.blobs_targeted == 0 and not rep.docs_paths and set(rep.items) <= seed


def _erased_before(state_dir: Path, q: QueuedPurge) -> bool:
    """Whether an earlier purge already erased ``q``'s item: its id or a stored alias is on the suppression
    list, which every purge but an upstream deletion's fills with each id it erased.  Its run targets nothing
    because nothing is left, as when an operator purge of the same item ran while ``q`` stayed queued: it runs
    and leaves the queue, never kept as one whose id is not found (B13 round 28 I93)."""
    sid = q.selector.source_id
    items = load_suppressions(state_dir).items
    return bool(sid) and any((sid, i) in items for i in (q.selector.stable_id, *q.aliases))


_HELD_WHY = "the purge failed; the log has the error"
"""What a queued entry's report says when its purge raised a :class:`GovernanceError` that is not a hold: a
git failure's text can name a path or an id, and the report is printed, so that text stays in the log."""


def run_purge_queue(
    config: Config,
    *,
    gov: GovernanceConfig | None = None,
    dry_run: bool = False,
    lock: bool = True,
    now: datetime | None = None,
) -> list[PurgeReport]:
    """Run every queued purge; a held or failing one stays queued (logged), the rest are dequeued.

    ``dry_run`` reports each queued purge without writing anything, and leaves the queue as it is.  The
    queue file is written after every purge that verified, so a run that stops part-way leaves no entry it
    has already purged; the entry in flight stays in the file.  An entry a verified purge of this run already
    did (:func:`_covers`) is not run again: it leaves the queue with a ``purge-covered`` audit line and a note
    on the covering report; a dry run notes it the same way and does not preview it on its own.  An entry
    kept queued (a listed file's upstream deletion, a published file's label escalation, a hold, a failure)
    gets a report with ``not_run`` set.  An upstream deletion or label escalation by source + id is resolved
    dry first, seeded with its stored aliases: one that targets no item, docs path or blob, and whose item no
    earlier purge erased, stays queued with :func:`unresolved_note` and its ``found_nothing`` mark, never
    VERIFIED having erased nothing; this is the only judgment that reads the docs history (B13 round 28 I93,
    I109).
    """
    gov = gov or load_governance(config.config_path)
    sp = config.state_paths
    reports: list[PurgeReport] = []
    with _maybe_lock(sp, lock):
        remaining: list[QueuedPurge] = []
        stays: list[QueuedPurge] = []  # reported "stays queued": a later purge's cover must not drop them
        covered: set[int] = (
            set()
        )  # indexes in ``queue`` of the later entries a verified purge of this run did
        queue = pending_purges(sp.root)
        unjudged = 0  # entries no manifest read could judge: said once, with the cause
        new_start = 0  # the new start's entries, and those kept until their id is found: one counted
        unresolved = 0  # WARNING per kind after the loop, as P25 (B13 round 28 I65)

        def not_run(q: QueuedPurge, why: str) -> None:
            reports.append(
                PurgeReport(
                    selector=q.selector.kind(),
                    reason=q.reason,
                    dry_run=dry_run,
                    items=(),
                    docs_paths=(),
                    remote="not run",
                    notes=(why,),
                    not_run=why,
                )
            )
            remaining.append(q)
            stays.append(q)

        for n, q in enumerate(queue):
            if n in covered:
                # an earlier purge of this run did it (or, in a preview, would): audited and noted on that
                # report.  Skipped before the kept check, which a preview would judge on a manifest it left as
                # it is: the run's covering purge has already erased the row that check reads
                continue
            why = _kept_queued(sp.db, q)
            probe = _probed(q) and (not why or why.startswith(UNRESOLVED_HEAD))
            if why and not probe:
                if why.startswith("not judged: "):
                    unjudged += 1  # one WARNING for the run, with its cause, after the loop (P25)
                elif why == NEW_START_NOTE:
                    new_start += 1  # one counted WARNING after the loop (P40; I65)
                elif why.startswith(UNRESOLVED_HEAD):
                    unresolved += 1  # one counted WARNING after the loop (P112; I65)
                else:
                    log.warning("governance: %s", why)  # a judged note names its own rows: said per entry
                not_run(q, why)
                continue
            seed = _stored_ids(q)
            try:
                dry: PurgeReport | None = None
                if probe:
                    # B13 round 28 I93, I109: what the entry's id and stored aliases reach, before it runs
                    dry = purge(
                        config,
                        q.selector,
                        reason=q.reason,
                        gov=gov,
                        dry_run=True,
                        lock=False,
                        ids=seed,
                        now=now,
                    )
                    if _targets_nothing(dry, seed) and not _erased_before(sp.root, q):
                        unresolved += 1  # one counted WARNING after the loop (P112; I65)
                        not_run(dataclasses.replace(q, found_nothing=True), unresolved_note(q))
                        continue
                rep = (
                    dry
                    if dry is not None and dry_run
                    else purge(
                        config,
                        q.selector,
                        reason=q.reason,
                        gov=gov,
                        dry_run=dry_run,
                        lock=False,
                        now=now,
                        ids=seed,
                    )
                )
            except GovernanceError as exc:
                log.warning("governance: queued purge kept: %s", exc)
                not_run(q, f"stays queued: {exc if isinstance(exc, HoldActiveError) else _HELD_WHY}")
                continue
            reports.append(rep)
            if dry_run:
                # a preview folds a later entry this purge covers into its report, as the run does, so it
                # counts what the run would run; each previewed purge is presumed to verify, so the run's
                # "earlier entry" half (an earlier purge that failed) has nothing to fold here
                later = [
                    i
                    for i in range(n + 1, len(queue))
                    if i not in covered and _covers(q.selector, rep, queue[i])
                ]
                rep = dataclasses.replace(
                    rep,
                    notes=(
                        *rep.notes,
                        *(
                            f"would also do the queued {queue[i].reason.value} purge of the same item: the "
                            "run takes it off the queue"
                            for i in later
                        ),
                    ),
                )
                reports[-1] = rep
                covered.update(later)
                remaining.append(q)
            elif not rep.verified:
                remaining.append(q)
            else:
                # purge() took off the file every entry it covered: record each one as done (an erasure
                # request keeps a record that it was fulfilled), except an entry this run already reported as
                # staying queued: it stays, and the next run clears it
                did = [e for e in remaining if not any(e is k for k in stays) and _covers(q.selector, rep, e)]
                later = [
                    i
                    for i in range(n + 1, len(queue))
                    if i not in covered and _covers(q.selector, rep, queue[i])
                ]
                for e in [*did, *(queue[i] for i in later)]:
                    log.info(
                        "governance: queued %s purge covered by an earlier purge in this run", e.reason.value
                    )
                    append_audit(
                        sp.root,
                        {
                            "action": "purge-covered",
                            "at": _iso(_now(now)),
                            "covered_by_sha256": q.selector.fingerprint(),
                            "reason": e.reason.value,
                            "selector_sha256": e.selector.fingerprint(),
                        },
                    )
                    rep = dataclasses.replace(
                        rep,
                        notes=(
                            *rep.notes,
                            f"also did the queued {e.reason.value} purge of the same item: "
                            "taken off the queue",
                        ),
                    )
                reports[-1] = rep
                covered.update(later)
                remaining = [e for e in remaining if not any(e is d for d in did)]
                _write_queue(
                    sp.root, [*remaining, *(queue[i] for i in range(n + 1, len(queue)) if i not in covered)]
                )
        if not dry_run:
            _write_queue(sp.root, remaining)
        if new_start:
            log.warning("governance: %s", new_start_purges(new_start))
        if unresolved:
            log.warning("governance: %s", unresolved_purges(unresolved))
        if unjudged:
            log.warning(
                "governance: cannot judge %s: %s",
                plural(unjudged, "queued purge"),
                _unjudged_cause(expand(sp.db)),
            )
    return reports


# ---------------------------------------------------------------------------------------------------------
# history compaction
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompactionReport:
    """Result of one compaction: how many commits were squashed into the new root, and verification."""

    cutoff: str
    dry_run: bool
    squashed: int
    kept: int
    new_root: str | None
    survivors: tuple[str, ...] = ()
    unreachable_left: int = 0
    dangling_recovery_hints: int = 0
    note: str = ""

    @property
    def verified(self) -> bool:
        """True when a real compaction left no dropped object readable (a no-op is trivially verified)."""
        return not self.dry_run and not self.survivors and self.unreachable_left == 0


def _squash_set(graph: Sequence[tuple[str, list[str], int]], cutoff_ts: int) -> set[str]:
    parents = {sha: ps for sha, ps, _ in graph}
    stack = [sha for sha, _ps, ts in graph if ts < cutoff_ts]
    out: set[str] = set()
    while stack:
        c = stack.pop()
        if c in out:
            continue
        out.add(c)
        stack.extend(parents.get(c, []))
    return out


def compaction_due(repo: Path, gov: GovernanceConfig, *, now: datetime | None = None) -> bool:
    """True when some commit (other than a lone root) is older than history_days + compaction_slack_days, so a
    scheduled compaction should run (the slack keeps history shas stable between weekly-ish runs)."""
    repo = expand(repo)
    if not (repo / ".git").exists():
        return False
    graph = _commit_graph(repo, [])
    cutoff = int((_now(now) - timedelta(days=gov.history_days + gov.compaction_slack_days)).timestamp())
    old = _squash_set(graph, cutoff)
    return len(old) > 1 or any(ps for sha, ps, _ in graph if sha in old)


def compaction_state(repo: Path, gov: GovernanceConfig, *, now: datetime | None = None) -> tuple[str, str]:
    """``("ok" | "due" | "overdue", one line)`` for doctor / status / STATE.md (C15 section 9 item 39).

    ``due``: history holds a commit older than ``history_days + compaction_slack_days`` (the next RECONCILE
    compacts it); ``overdue``: older than ``history_days + 2 x compaction_slack_days`` (the scheduled run did
    not happen, or a hold / failure keeps blocking it)."""
    if gov.archive:
        return "ok", ARCHIVE_KEEPS_HISTORY
    span = gov.history_days + gov.compaction_slack_days
    if not compaction_due(repo, gov, now=now):
        return "ok", f"no commit older than {plural(span, 'day')} (history_days {gov.history_days} + slack)"
    late = dataclasses.replace(gov, compaction_slack_days=2 * gov.compaction_slack_days)
    if compaction_due(repo, late, now=now):
        return (
            "overdue",
            f"history holds commits older than "
            f"{plural(gov.history_days + 2 * gov.compaction_slack_days, 'day')}: "
            "retention compaction has not run",
        )
    return "due", f"history holds commits older than {plural(span, 'day')}; the next RECONCILE compacts them"


def compact_history(
    config: Config,
    keep_days: int | None = None,
    *,
    gov: GovernanceConfig | None = None,
    dry_run: bool = False,
    lock: bool = True,
    now: datetime | None = None,
) -> CompactionReport:
    """Squash every commit older than ``keep_days`` (default ``[governance] history_days``) into one root
    snapshot of the newest such commit, replay newer commits on it, expire reflogs, prune, and verify that no
    dropped object survives.  Refused while any hold is active, and for ``keep_days`` below 1."""
    gov = gov or load_governance(config.config_path)
    if gov.archive:
        raise GovernanceError(f"history compaction refused: {ARCHIVE_KEEPS_HISTORY} ([governance] archive)")
    days = gov.history_days if keep_days is None else keep_days
    if days < 1:  # KISS K13b: 0 would squash every commit, the session's own included
        raise GovernanceError(f"keep_days must be >= 1, got {days}")
    repo = expand(config.docs_repo)
    sp = config.state_paths
    _refuse_if_held(active_holds(sp.root, gov), "history compaction")
    cutoff_dt = _now(now) - timedelta(days=days)
    cutoff = _iso(cutoff_dt)
    if not (repo / ".git").exists():
        return CompactionReport(cutoff, dry_run, 0, 0, None, note="no docs repo")
    with _maybe_lock(sp, lock and not dry_run):
        worktrees = _worktrees(repo) if dry_run else _preflight(repo)
        graph = _commit_graph(repo, [w.head for w in worktrees if w.head])
        squash = _squash_set(graph, int(cutoff_dt.timestamp()))
        kept = len(graph) - len(squash)
        if not squash:
            return CompactionReport(cutoff, dry_run, 0, kept, None, note="nothing older than the cutoff")
        order = [sha for sha, _ps, _ts in graph if sha in squash]
        parents = {sha: ps for sha, ps, _ in graph}
        head = gitops.head_sha(repo)
        head_anc: set[str] = set()
        if head:
            stack = [head]
            while stack:
                c = stack.pop()
                if c not in head_anc:
                    head_anc.add(c)
                    stack.extend(parents.get(c, []))
        tips = [s for s in order if s in head_anc] or order
        base = tips[-1]
        if len(squash) == 1 and not parents.get(base):
            return CompactionReport(
                cutoff, dry_run, 0, kept, None, note="history already starts at the cutoff"
            )
        if dry_run:
            return CompactionReport(
                cutoff, True, len(squash), kept, None, note=f"would squash into {base[:12]}"
            )
        cat = _CatFile(repo)
        try:
            hlen = _hash_len(repo)
            before = {
                line.split()[0] for line in _git_text(repo, "rev-list", "--objects", "--all").splitlines()
            }
            rw = _Rewriter(repo, cat, hlen, selector=None, explicit_paths=set(), plan=_Plan())
            message = (
                f"agentsync: history before {cutoff} compacted\n\n"
                f"{plural(len(squash), 'commit')} older than {plural(days, 'day')} "
                f"{'was' if len(squash) == 1 else 'were'} squashed into this snapshot "
                "([governance] history_days); their content is no longer recoverable from this repo.\n\n"
                f"Agentsync-Compacted: {len(squash)}\nAgentsync-Cutoff: {cutoff}\n"
            ).encode()
            result = _apply_rewrite(
                repo, cat, rw, graph, worktrees, squash=squash, squash_base=base, squash_message=message
            )
        finally:
            cat.close()
        _reset_worktrees(worktrees, None)
        _expire_and_prune(repo)
        after = {line.split()[0] for line in _git_text(repo, "rev-list", "--objects", "--all").splitlines()}
        survivors = surviving_objects(repo, before - after)
        unreachable = unreachable_objects(repo)
        dangling = 0
        if sp.db.exists():
            conn = _connect(sp.db)
            try:
                marks = ",".join("?" * len(squash))
                dangling = conn.execute(
                    f"SELECT COUNT(*) FROM tombstones WHERE last_commit IN ({marks})",
                    sorted(squash),
                ).fetchone()[0]
            finally:
                conn.close()
            _manifest_apply(
                sp.db,
                [],
                set(),
                action_keys=set(),
                scrubber=_Scrubber([], [], [], standalone_rel_paths=False),
                commit_map=result.commit_map,
                squashed=squash,
                tree_sha=gitops.head_tree_sha(repo),
            )
        new_root = result.commit_map[base]
        report = CompactionReport(
            cutoff, False, len(squash), kept, new_root, tuple(survivors), len(unreachable), dangling
        )
        append_audit(
            sp.root,
            {
                "action": "compact",
                "at": _iso(_now(now)),
                "cutoff": cutoff,
                "kept": kept,
                "squashed": len(squash),
                "verified": report.verified,
            },
        )
        return report


# ---------------------------------------------------------------------------------------------------------
# Time Machine exclusions (C15 req 42)
# ---------------------------------------------------------------------------------------------------------


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), capture_output=True, text=True, check=False, timeout=120)


def time_machine_exclusions(config: Config) -> tuple[Path, ...]:
    """Paths that hold tenant content a purge must reach and must not be backed up: mirror/, archive/, the
    docs repo's ``.git`` (every version of every mirror page: a purge cannot reach a backup), the converter
    cache, the SQLite manifest, its ``-wal``/``-shm`` siblings and any pre-migration ``<db>.pre-v*`` copy (the
    same rows and cursors; KISS K12), the Teams store and staging (curated topics/ stay backed up as worktree
    files).

    A folder above the state folder that refuses this account a search makes the backups' lookup raise
    PermissionError: none is listed, and status's not-checked line covers the state folder's paths. Only
    here: purge and the cycle still stop on it, so no pre-migration copy that holds purged rows is passed
    over unseen (B13 round 22 Z46, Z9: the whole governance check became one generic FAIL, the config hold
    and governance.remote with it)."""
    sp = config.state_paths
    try:
        backups = migration_backups(sp.db)
    except PermissionError:
        backups = []
    return (
        expand(config.docs_repo) / "mirror",
        expand(config.docs_repo) / "archive",
        expand(config.docs_repo) / ".git",
        expand(config.cache_dir),
        sp.db,
        sp.db.with_name(sp.db.name + "-wal"),
        sp.db.with_name(sp.db.name + "-shm"),
        sp.teams_store,
        sp.staging,
        *backups,
    )


_TM_TRANSIENT = ("-wal", "-shm")


def time_machine_status(
    config: Config, *, runner: Runner | None = None
) -> list[tuple[Path, bool | str | None]]:
    """(path, excluded?) per exclusion path; None when the path does not exist, and the system's words (a
    str) when this account cannot read whether it is excluded: a file it cannot open, a folder it cannot look
    inside. Never False for that: the exclusion was there, and the sync the warn named could not fix a mode
    (B13 round 20 X12, X27).

    Read with getxattr(2); an injected ``runner`` (tests) is asked ``xattr -p`` instead."""
    out: list[tuple[Path, bool | str | None]] = []
    for p in time_machine_exclusions(config):
        try:
            if not p.exists():
                out.append((p, None))
                continue
        except PermissionError as exc:  # inside a folder this account cannot look in
            out.append((p, exc.strerror or str(exc)))
            continue
        if runner is None:
            out.append((p, _xattr_state(p, TM_EXCLUDE_XATTR)))
            continue
        cp = runner(["/usr/bin/xattr", "-p", TM_EXCLUDE_XATTR, str(p)])
        out.append((p, cp.returncode == 0))
    return out


def ensure_time_machine_exclusions(config: Config, *, runner: Runner | None = None) -> list[str]:
    """Apply the exclusions to every existing, durable path that lacks one (C15 req 42 outside
    ``install-agent``: ``init``, the first ``sync``); an empty list when nothing needed doing.

    Skipped off macOS and when ``AGENTSYNC_TM_EXCLUDE=0``; ``-wal``/``-shm`` come and go with connections, so
    they are applied when present but never re-checked (a ``tmutil`` call costs seconds). A path this account
    cannot read is not missing: no setxattr or ``tmutil`` at every cycle for it (B13 round 20 X12)."""
    if os.environ.get("AGENTSYNC_TM_EXCLUDE", "1") == "0" or not Path(_TMUTIL).exists():
        return []
    run = runner or _run
    missing = [
        p
        for p, excluded in time_machine_status(config, runner=runner)
        if excluded is False and not p.name.endswith(_TM_TRANSIENT)
    ]
    if runner is None:
        missing = [p for p in missing if not _set_tm_exclusion(p)]
    if not missing:
        return []
    cp = run([_TMUTIL, "addexclusion", *map(str, missing)])
    if cp.returncode == 0:
        return [f"excluded {p}" for p in missing]
    return [f"failed {p}: {cp.stderr.strip()}" for p in missing]


def apply_time_machine_exclusions(config: Config, *, runner: Runner | None = None) -> list[str]:
    """``tmutil addexclusion`` (sticky, no admin) on every path in ONE call, creating missing dirs.

    Returns one line per path: ``excluded <p>`` | ``pending <p>`` (a file not created yet) |
    ``failed <p>: <stderr>``; a failed batch is retried per path so the failure names its path.  Measured on
    macOS 15.7: ~11 s per path, so callers run it once at install, never per cycle.
    """
    run = runner or _run
    lines: dict[Path, str] = {}
    todo: list[Path] = []
    for p in time_machine_exclusions(config):
        if not p.exists():
            if p.suffix:
                lines[p] = f"pending {p}"
                continue
            p.mkdir(parents=True, exist_ok=True, mode=0o700)
        todo.append(p)
    if todo:
        cp = run([_TMUTIL, "addexclusion", *map(str, todo)])
        if cp.returncode == 0:
            lines.update({p: f"excluded {p}" for p in todo})
        else:
            for p in todo:
                one = run([_TMUTIL, "addexclusion", str(p)])
                lines[p] = f"excluded {p}" if one.returncode == 0 else f"failed {p}: {one.stderr.strip()}"
    return [lines[p] for p in time_machine_exclusions(config)]


# ---------------------------------------------------------------------------------------------------------
# offboarding / uninstall
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OffboardLocation:
    """One place agentsync keeps data: ``kind`` (launch-agent | keychain-item | cache-dir | log-dir |
    docs-repo | config-file | state-dir), where, whether it exists, and what offboarding does with it."""

    kind: str
    location: str
    exists: bool
    action: str


@dataclass(frozen=True, slots=True)
class OffboardReport:
    """The exact list of locations, what was removed (empty on a dry run), errors and manual follow-ups."""

    dry_run: bool
    locations: tuple[OffboardLocation, ...]
    removed: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    manual_steps: tuple[str, ...] = ()


def _keychain_accounts(service: str, keychain: Path | None, run: Runner) -> list[str]:
    """Accounts of every generic-password item with exactly this service (attributes only, no secrets)."""
    argv = [_SECURITY, "dump-keychain"] + ([str(keychain)] if keychain else [])
    cp = run(argv)
    if cp.returncode != 0:
        return []
    accounts: set[str] = set()
    for block in cp.stdout.split("keychain: ")[1:]:
        if 'class: "genp"' not in block:
            continue
        svce = re.search(r'"svce"<blob>=(?:0x[0-9A-F]+\s+)?"((?:[^"\\]|\\.)*)"', block)
        if svce is None or svce.group(1) != service:
            continue
        acct = re.search(r'"acct"<blob>=(?:0x[0-9A-F]+\s+)?"((?:[^"\\]|\\.)*)"', block)
        accounts.add(acct.group(1) if acct else "")
    return sorted(accounts)


def _launch_labels(config: Config) -> list[str]:
    return [f"{config.launchd_label_prefix}.poll", f"{config.launchd_label_prefix}.reconcile"]


def _plist_path(label: str) -> Path:
    from agentsync.ops import launchd  # noqa: PLC0415 - keep plistlib/launchctl out of import time

    return launchd.plist_path(label)


def offboard_plan(
    config: Config,
    *,
    purge_data: bool = False,
    keychain_service: str = KEYCHAIN_SERVICE,
    keychain: Path | None = None,
    runner: Runner | None = None,
) -> tuple[OffboardLocation, ...]:
    """The exact, ordered list of locations :func:`offboard` handles (read-only)."""
    run = runner or _run
    locs: list[OffboardLocation] = []
    for label in _launch_labels(config):
        p = _plist_path(label)
        locs.append(
            OffboardLocation("launch-agent", str(p), p.exists(), f"launchctl bootout {label} + remove plist")
        )
    app = _launcher_bundle()
    if app is not None and app.exists():
        # The TCC-approved launcher left behind is a standing grant (review deploy-ops-offboard).
        locs.append(OffboardLocation("launcher-app", str(app), True, "remove (after the LaunchAgents)"))
        ident = launchd.launcher_identifier(app)
        locs.append(OffboardLocation("tcc-grant", f"tcc:{ident}", True, f"tccutil reset All {ident}"))
    for acct in _keychain_accounts(keychain_service, keychain, run):
        locs.append(
            OffboardLocation(
                "keychain-item", f"keychain:{keychain_service}/{acct}", True, "delete-generic-password"
            )
        )
    for kind, p in (("cache-dir", config.cache_dir), ("log-dir", config.log_dir)):
        e = expand(p)
        locs.append(OffboardLocation(kind, str(e), e.exists(), "remove"))
    keep = "keep (purge_data=false): hand to the retention owner"
    docs = expand(config.docs_repo)
    locs.append(OffboardLocation("docs-repo", str(docs), docs.exists(), "remove" if purge_data else keep))
    cfg_path = expand(config.config_path)
    locs.append(
        OffboardLocation("config-file", str(cfg_path), cfg_path.exists(), "remove" if purge_data else keep)
    )
    policy_file = cfg_path.parent / POLICY_FILE_NAME
    if policy_file.exists():
        locs.append(OffboardLocation("config-file", str(policy_file), True, "remove" if purge_data else keep))
    ctx = cfg_path.parent
    for src in config.sources:
        if src.kind is SourceKind.INBOX and src.path is not None and is_under(expand(src.path), ctx):
            inbox = expand(src.path)
            locs.append(
                OffboardLocation("inbox-dir", str(inbox), inbox.exists(), "remove" if purge_data else keep)
            )
    state = expand(config.state_dir)
    locs.append(OffboardLocation("state-dir", str(state), state.exists(), "remove"))
    for kind, path in _uv_tool_paths():
        if path.exists() or path.is_symlink():
            locs.append(OffboardLocation(kind, str(path), True, "remove (uv tool uninstall agentsync)"))
    return tuple(locs)


def _launcher_bundle() -> Path | None:
    """The installed launcher bundle ($AGENTSYNC_LAUNCHER or ~/Applications/AgentSyncLauncher.app)."""
    raw = os.environ.get(launchd.LAUNCHER_ENV, "").strip()
    if raw.lower() == "none":
        return None
    if raw:
        exe = Path(raw).expanduser()
        return launchd.launcher_app(exe) or (exe if exe.suffix == ".app" else None)
    return launchd.default_launcher_app()


def _uv_tool_paths() -> list[tuple[str, Path]]:
    """Where ``uv tool install agentsync`` put the program: its environment, then the bin shim."""
    tool_dir = Path(os.environ.get("UV_TOOL_DIR") or Path.home() / ".local" / "share" / "uv" / "tools")
    bin_dir = Path(os.environ.get("UV_TOOL_BIN_DIR") or Path.home() / ".local" / "bin")
    return [("uv-tool-env", expand(tool_dir) / "agentsync"), ("uv-tool-shim", expand(bin_dir) / "agentsync")]


def _safe_to_remove(path: Path) -> str | None:
    """Why ``path`` must never be removed wholesale (None = fine)."""
    p = expand(path)
    home = Path.home()
    if is_cloud_path(p):
        return "inside ~/Library/CloudStorage"
    if len(p.parts) < 3 or p in (home, *home.parents):
        return "too close to / or $HOME"
    return None


def offboard(
    config: Config,
    *,
    purge_data: bool = False,
    dry_run: bool = True,
    confirm: str | None = None,
    gov: GovernanceConfig | None = None,
    keychain_service: str = KEYCHAIN_SERVICE,
    keychain: Path | None = None,
    runner: Runner | None = None,
    launchd_uninstall: Callable[[str], bool] | None = None,
) -> OffboardReport:
    """Uninstall: list (dry run, the default) or remove every local copy agentsync made.

    Destructive only with ``dry_run=False`` AND ``confirm`` equal to the docs repo path; refused while any
    hold is active.  Order: LaunchAgents, Keychain items, cache, logs, (docs repo, sources.toml when
    ``purge_data``), state dir last (it holds the lock, taken first).  Idempotent.
    """
    gov = gov or load_governance(config.config_path)
    run = runner or _run
    locs = offboard_plan(
        config, purge_data=purge_data, keychain_service=keychain_service, keychain=keychain, runner=run
    )
    sp = config.state_paths
    holds = active_holds(sp.root, gov)
    manual = [
        "Revoke the refresh tokens: ask IT to revoke sessions for your account, or sign out everywhere at "
        "https://myaccount.microsoft.com (deleting the Keychain item does not revoke them).",
        "Time Machine snapshots taken before the exclusions may hold copies: check `tmutil listbackups`; "
        "deleting a backup needs an administrator.",
        "Clones, remotes and backups made from the docs repo are copies no local step can reach.",
        f"Export {audit_path(sp.root)} to the retention owner before the state dir is removed.",
        "Remove agentsync-launcher from System Settings > Privacy & Security > Full Disk Access / Files and "
        "Folders if it is listed; on a managed Mac ask IT to withdraw any PPPC profile naming it.",
    ]
    if not purge_data:
        manual.append(f"The docs repo {expand(config.docs_repo)} is kept: hand it to the retention owner.")
    if holds:
        manual.insert(0, "BLOCKED: " + "; ".join(h.describe() for h in holds))
    if dry_run:
        return OffboardReport(True, locs, manual_steps=tuple(manual))
    expected = str(expand(config.docs_repo))
    if confirm != expected:
        raise GovernanceError(f"offboarding removes data: pass confirm={expected!r} (the docs repo path)")
    _refuse_if_held(holds, "offboarding")
    uninstall = launchd_uninstall or _default_launchd_uninstall
    removed: list[str] = []
    errors: list[str] = []
    for loc in locs:
        if loc.kind != "launch-agent":
            continue
        label = Path(loc.location).name.removesuffix(".plist")
        try:
            if uninstall(label):
                removed.append(loc.location)
        except OSError as exc:
            errors.append(f"{label}: {exc}")
    lock = SingleWriterLock(sp.lock, _LOCK_LABEL) if sp.root.is_dir() else None
    if lock is not None:
        lock.acquire()
    try:
        for loc in locs:
            if loc.kind == "launcher-app":
                p = Path(loc.location)
                why = _safe_to_remove(p)
                if why:
                    errors.append(f"{p}: not removed ({why})")
                elif p.exists():
                    _rmtree(p)
                    removed.append(str(p))
            elif loc.kind == "tcc-grant":
                ident = loc.location.split(":", 1)[1]
                cp = run([_TCCUTIL, "reset", "All", ident])
                if cp.returncode == 0:
                    removed.append(loc.location)
                else:
                    errors.append(f"{loc.location}: tccutil rc {cp.returncode}: {cp.stderr.strip()}")
            elif loc.kind == "keychain-item":
                acct = loc.location.split("/", 1)[1]
                argv = [_SECURITY, "delete-generic-password", "-s", keychain_service, "-a", acct]
                argv += [str(keychain)] if keychain else []
                cp = run(argv)
                while cp.returncode == 0:  # duplicates: delete until "not found" (44)
                    if loc.location not in removed:
                        removed.append(loc.location)
                    cp = run(argv)
                if cp.returncode != 44:
                    errors.append(f"{loc.location}: security rc {cp.returncode}: {cp.stderr.strip()}")
            elif loc.kind in ("cache-dir", "log-dir", "docs-repo", "config-file", "inbox-dir", "state-dir"):
                if not loc.action.startswith("remove"):
                    continue
                p = Path(loc.location)
                why = _safe_to_remove(p) if loc.kind != "config-file" else None
                if why:
                    errors.append(f"{p}: not removed ({why})")
                    continue
                if loc.kind == "docs-repo" and p.exists() and not (p / ".git").exists():
                    errors.append(f"{p}: not removed (not a git repo; refusing to guess)")
                    continue
                if p.exists() or p.is_symlink():
                    _rmtree(p)
                    removed.append(str(p))
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                lock.release()
    for loc in locs:  # the program itself goes last (this process may be running from it)
        if loc.kind in ("uv-tool-env", "uv-tool-shim"):
            p = Path(loc.location)
            why = _safe_to_remove(p)
            if why:
                errors.append(f"{p}: not removed ({why})")
            elif p.is_symlink() or p.is_file():
                p.unlink()
                removed.append(str(p))
            elif p.exists():
                _rmtree(p)
                removed.append(str(p))
    log.warning("governance: offboarded; removed %d location(s), %d error(s)", len(removed), len(errors))
    return OffboardReport(False, locs, tuple(removed), tuple(errors), tuple(manual))


def _default_launchd_uninstall(label: str) -> bool:
    from agentsync.ops import launchd  # noqa: PLC0415 - only needed on a real uninstall

    return launchd.uninstall(label)
