"""Retention, purge, legal hold, history compaction, remote policy and offboarding (owner: governance).

C15 section 7 and requirements 36-42.  The docs repo is a plaintext copy of tenant data OUTSIDE Purview
retention, hold, eDiscovery and DLP, so this module owns every way that copy is bounded or destroyed:

* :func:`purge` removes the selected items' pages, sidecars, manifest rows, converter-cache entries, the
  piece store folder of a purged recording, tombstone data and log lines that name them, then rewrites
  docs-repo history with git plumbing only (``cat-file``, ``hash-object``, ``update-ref``), expires every
  reflog, runs ``gc --prune=now`` and verifies with ``cat-file --batch-check`` that no targeted blob
  survives.  Index-like generated files (``_manifest/*.jsonl``, ``CHANGELOG``, ``INDEX.md``,
  ``DEPENDS.tsv``, ``_sync/*``) are rewritten in every
  commit with the lines that name a purged item removed.  Surviving tombstones of OTHER items have their
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
import hashlib
import json
import logging
import os
import posixpath
import re
import shutil
import sqlite3
import subprocess
import tomllib
import unicodedata
import urllib.parse
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
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
from agentsync.manifest import Manifest, migration_backups
from agentsync.model import SourceKind
from agentsync.ops import launchd
from agentsync.ops.lock import SingleWriterLock
from agentsync.paths import StatePaths, expand, glob_match, is_cloud_path, is_under
from agentsync.policy import POLICY_FILE_NAME
from agentsync.tm_exclude import TM_EXCLUDE_XATTR
from agentsync.tm_exclude import has_xattr as _has_xattr
from agentsync.tm_exclude import set_exclusion as _set_tm_exclusion

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


def _atomic_write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = (json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    tmp.replace(path)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        raise GovernanceError(f"{path}: unreadable ({exc}); fix or remove it by hand") from None


def audit_path(state_dir: Path) -> Path:
    """Return the append-only audit trail ``<state_dir>/governance/audit.jsonl`` (no content, ever)."""
    return governance_dir(state_dir) / "audit.jsonl"


def append_audit(state_dir: Path, record: Mapping[str, object]) -> None:
    """Append one JSON line (sorted keys) to the audit trail, mode 0600, fsynced."""
    path = audit_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    line = (json.dumps(dict(record), sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)


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


def _state_holds(state_dir: Path) -> list[Hold]:
    data = _read_json(_holds_file(state_dir), {"holds": []})
    out: list[Hold] = []
    for h in data.get("holds", []):
        out.append(Hold(str(h["scope"]), str(h["reason"]), str(h["owner"]), str(h["set_at"]), "state"))
    return out


def set_hold(state_dir: Path, scope: str, *, reason: str, owner: str, now: datetime | None = None) -> Hold:
    """Record a hold (records/legal owner only) that suspends purge and compaction for ``scope``."""
    s = _check_scope(scope)
    if not reason.strip() or not owner.strip():
        raise GovernanceError("a hold needs a reason and the name of the records or legal owner setting it")
    at = _iso(_now(now))
    holds = [h for h in _state_holds(state_dir) if h.scope != s]
    hold = Hold(s, " ".join(reason.split()), " ".join(owner.split()), at, "state")
    holds.append(hold)
    _atomic_write_json(
        _holds_file(state_dir),
        {
            "holds": [
                {"scope": h.scope, "reason": h.reason, "owner": h.owner, "set_at": h.set_at}
                for h in sorted(holds, key=lambda h: h.scope)
            ]
        },
    )
    append_audit(state_dir, {"action": "hold-set", "at": at, "scope": s, "owner": hold.owner})
    log.warning("governance: %s", hold.describe())
    return hold


def release_hold(state_dir: Path, scope: str, *, owner: str, now: datetime | None = None) -> bool:
    """Release the state hold on ``scope``; True if one existed ; config holds live in sources.toml."""
    s = _check_scope(scope)
    holds = _state_holds(state_dir)
    keep = [h for h in holds if h.scope != s]
    if len(keep) == len(holds):
        return False
    _atomic_write_json(
        _holds_file(state_dir),
        {
            "holds": [
                {"scope": h.scope, "reason": h.reason, "owner": h.owner, "set_at": h.set_at} for h in keep
            ]
        },
    )
    at = _iso(_now(now))
    append_audit(
        state_dir, {"action": "hold-released", "at": at, "scope": s, "owner": " ".join(owner.split())}
    )
    log.warning("governance: hold %s released by %s", s, owner)
    return True


def active_holds(state_dir: Path, gov: GovernanceConfig) -> tuple[Hold, ...]:
    """Every hold in force: the config-level one (scope ``all``) first, then state holds by scope."""
    out: list[Hold] = []
    if gov.hold:
        out.append(
            Hold(HOLD_ALL, gov.hold_reason or "hold = true", gov.hold_owner or "sources.toml", "", "config")
        )
    out += sorted(_state_holds(state_dir), key=lambda h: h.scope)
    return tuple(out)


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
    """Load the suppression list (empty when none)."""
    data = _read_json(_suppress_file(state_dir), {})
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
    """One pending purge request."""

    selector: PurgeSelector
    reason: PurgeReason
    enqueued_at: str


def _queue_file(state_dir: Path) -> Path:
    return governance_dir(state_dir) / "purge-queue.json"


def pending_purges(state_dir: Path) -> list[QueuedPurge]:
    """Queued purges, oldest first."""
    data = _read_json(_queue_file(state_dir), {"queue": []})
    return [
        QueuedPurge(PurgeSelector(**q["selector"]), PurgeReason(q["reason"]), str(q["enqueued_at"]))
        for q in data.get("queue", [])
    ]


def _write_queue(state_dir: Path, queue: Sequence[QueuedPurge]) -> None:
    _atomic_write_json(
        _queue_file(state_dir),
        {
            "queue": [
                {"selector": q.selector.to_json(), "reason": q.reason.value, "enqueued_at": q.enqueued_at}
                for q in queue
            ]
        },
    )


def enqueue_purge(
    state_dir: Path, selector: PurgeSelector, reason: PurgeReason, *, now: datetime | None = None
) -> bool:
    """Queue a purge (confirmed upstream deletion past the breaker, label escalation, DLP, erasure); False if
    an identical request is already queued."""
    queue = pending_purges(state_dir)
    if any(q.selector == selector and q.reason is reason for q in queue):
        return False
    queue.append(QueuedPurge(selector, reason, _iso(_now(now))))
    _write_queue(state_dir, queue)
    append_audit(
        state_dir,
        {
            "action": "purge-enqueued",
            "at": _iso(_now(now)),
            "reason": reason.value,
            "selector_kind": selector.kind(),
            "selector_sha256": selector.fingerprint(),
            "source_id": selector.source_id,
        },
    )
    return True


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


class _Scrubber:
    """Line-level removal of every mention of a purged item from index-like text."""

    def __init__(
        self,
        docs_paths: Iterable[str],
        ids: Iterable[tuple[str, str]],
        rel_paths: Iterable[tuple[str, str]],
        *,
        standalone_rel_paths: bool,
    ) -> None:
        self._ids = set(ids)
        self._paths = _needle_regex([*docs_paths, *(sid for _, sid in self._ids)])
        self._pairs: dict[str, re.Pattern[str]] = {}
        by_source: dict[str, set[str]] = {}
        for source_id, rel in rel_paths:
            by_source.setdefault(source_id, set()).add(rel)
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
        if self._paths is not None and self._paths.search(line):
            return True
        if self._standalone is not None and self._standalone.search(line):
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
    ) -> None:
        self.repo = repo
        self.cat = cat
        self.hlen = hlen
        self.selector = selector
        self.explicit = explicit_paths
        self.plan = plan
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
        if path in self.explicit and (key[0], ident.get("source_path", "")) in self.plan.rel_paths:
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
        for mode, name, _sha in entries:
            if mode != _TREE_MODE or not name.endswith(_SIDECAR_SUFFIX.encode()):
                continue
            stem = name[: -len(_SIDECAR_SUFFIX)]
            page = prefix + stem.decode("utf-8", "surrogateescape") + ".md"
            if stem in stems or page in self.explicit:
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


def _reset_worktrees(worktrees: Sequence[_Worktree], scrubber: _Scrubber | None) -> list[str]:
    """Scrub ignored text files, then ``reset --hard`` each worktree onto its (rewritten) HEAD."""
    touched: list[str] = []
    for wt in worktrees:
        if not wt.path.is_dir():
            continue
        if scrubber is not None:
            ignored = _git_text(wt.path, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
            for rel in sorted(p for p in ignored.split("\0") if p):
                if _scrub_file(wt.path / rel, scrubber):
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
        if not gov.allow_remote:
            findings.append(
                f"remote {name} ({shown}) exists but [governance] allow_remote = false: every clone flattens "
                f"the writer's ACLs and no purge can reach it; run `git -C {repo} remote remove {name}`"
            )
        elif not remote_allowed(url, gov):
            findings.append(
                f"remote {name} ({shown}) is not under a tenant-owned prefix in [governance] "
                "remote_url_prefixes (or is inside ~/Library/CloudStorage)"
            )
    return findings


def push_rewritten(
    repo: Path, gov: GovernanceConfig, old_refs: Mapping[str, str], *, remote: str = "origin"
) -> str:
    """Force-push rewritten branches/tags with a lease on their pre-rewrite values, only when the policy
    allows
    the remote; returns ``disabled`` | ``no-remote`` | ``refused: …`` | ``pushed N ref(s) to <remote>``."""
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
    return f"pushed {len(refspecs)} ref(s) to {remote}"


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

    @property
    def verified(self) -> bool:
        """True when this was a real run and nothing targeted remains readable in the repo."""
        return not self.dry_run and not self.survivors and self.unreachable_left == 0 and not self.paths_left


@dataclass(frozen=True, slots=True)
class _ManifestTargets:
    items: list[tuple[str, str, str, bool]]  # source_id, stable_id, rel_path, is_dir
    docs_paths: set[str]
    action_keys: set[str]
    canonical: set[str]
    rendered: set[str]
    teams_months: list[tuple[str, str]]
    aliases: set[tuple[str, str]] = field(default_factory=set)  # retired ids of the selected items


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _resolve_manifest(db: Path, selector: PurgeSelector, ids: set[tuple[str, str]]) -> _ManifestTargets:
    """Read-only: the manifest rows the selector (or already-known ids) names."""
    empty = _ManifestTargets([], set(), set(), set(), set(), [])
    if not db.exists():
        return empty
    Manifest(db).close()  # schema/version check; raises ManifestSchemaError on a mismatch
    conn = _connect(db)
    try:
        items: list[tuple[str, str, str, bool]] = []
        rows = conn.execute(
            "SELECT i.source_id, i.stable_id, i.rel_path, i.is_dir, i.canonical_sha256, s.kind "
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
        return _ManifestTargets(items, docs_paths, keys, canonical, rendered, teams, aliases)
    finally:
        conn.close()


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
) -> dict[str, int]:
    """Delete the purged rows (secure_delete, checkpoint, VACUUM) and remap commit shas; return row counts."""
    counts = {"items": 0, "outputs": 0, "tombstones": 0, "depends": 0, "cache": 0, "run_sources": 0}
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
                for table in ("outputs", "tombstones"):
                    counts[table] += conn.execute(
                        f"DELETE FROM {table} WHERE output_path = ?",
                        (path,),
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


def _scrub_file(path: Path, scrubber: _Scrubber) -> bool:
    """Remove hit lines from one text file in place (0600 tmp + rename); True if changed."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _SCRUB_MAX_BYTES:
            return False
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    out, removed = scrubber.scrub(text)
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
            if _scrub_file(p, scrubber):
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
) -> PurgeReport:
    """Remove the selected items from the docs repo's whole history, the manifest (and any pre-migration
    ``<db>.pre-v*`` copy), the converter cache, the Teams store, staging and logs; expire reflogs, prune, and
    verify no targeted blob survives.

    Refused (HoldActiveError) while a hold covers an affected source.  ``dry_run`` reports without writing.
    ``push`` force-pushes the rewritten refs only when ``allow_remote`` and a tenant-owned remote exist.
    The returned report's ``verified`` must be True; the CLI exits non-zero otherwise.
    """
    gov = gov or load_governance(config.config_path)
    repo = expand(config.docs_repo)
    sp = config.state_paths
    with _maybe_lock(sp, lock and not dry_run):
        return _purge_locked(
            config, repo, sp, selector=selector, reason=reason, gov=gov, dry_run=dry_run, push=push, now=now
        )


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
) -> PurgeReport:
    has_repo = (repo / ".git").exists()
    mt = _resolve_manifest(sp.db, selector, set())
    plan = _Plan(ids={(s, i) for s, i, _r, d in mt.items if not d} | mt.aliases)
    plan.rel_paths |= {(s, r) for s, _i, r, d in mt.items if not d}
    worktrees: list[_Worktree] = []
    graph: list[tuple[str, list[str], int]] = []
    hlen = 20
    cat: _CatFile | None = None
    try:
        if has_repo:
            worktrees = _worktrees(repo) if dry_run else _preflight(repo)
            hlen = _hash_len(repo)
            graph = _commit_graph(repo, [w.head for w in worktrees if w.head])
            cat = _CatFile(repo)
            # pass 1 to a fixpoint: a glob match reveals an id whose other (renamed) paths are targets too
            for _ in range(4):
                before = (len(plan.ids), len(plan.target_blobs))
                rw = _Rewriter(repo, cat, hlen, selector=selector, explicit_paths=mt.docs_paths, plan=plan)
                for _sha, _parents, _ts in graph:
                    _, raw = cat.read(_sha)
                    rw.walk(_split_commit(raw)[0])
                if (len(plan.ids), len(plan.target_blobs)) == before:
                    break
            if plan.ids - {(s, i) for s, i, _r, d in mt.items if not d} - mt.aliases:
                mt = _resolve_manifest(sp.db, selector, plan.ids)
                plan.ids |= {(s, i) for s, i, _r, d in mt.items if not d} | mt.aliases
        affected_sources = sorted(
            {s for s, _ in plan.ids} | ({selector.source_id} if selector.source_id else set())
        )
        holds = blocking_holds(active_holds(sp.root, gov), affected_sources or None)
        _refuse_if_held(holds, "purge")
        docs_paths = sorted(mt.docs_paths | plan.dropped_paths)
        rel_paths = plan.rel_paths | {(s, r) for s, _i, r, _d in mt.items}
        scrubber = _Scrubber(docs_paths, plan.ids, rel_paths, standalone_rel_paths=False)
        log_scrubber = _Scrubber(docs_paths, plan.ids, rel_paths, standalone_rel_paths=True)
        cache_victims = _cache_entries(expand(config.cache_dir), mt.action_keys, mt.rendered | plan.rendered)
        piece_victims = _piece_victims(expand(config.cache_dir), mt.canonical | plan.canonical)
        citing = _topics_citing(repo, sp.db, set(docs_paths))
        items = tuple(sorted(plan.ids))
        if dry_run:
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
                notes=(f"{len(graph)} commit(s) would be rewritten",),
            )
        at = _iso(_now(now))
        notes: list[str] = []
        paths_left: list[str] = []
        result: _RewriteResult | None = None
        survivors: list[str] = []
        unreachable: list[str] = []
        head_tree: str | None = None
        if has_repo and cat is not None and graph:
            rw = _Rewriter(repo, cat, hlen, selector=selector, explicit_paths=mt.docs_paths, plan=plan)
            rw.scrubber = scrubber
            result = _apply_rewrite(repo, cat, rw, graph, worktrees)
            cat.close()
            cat = None
            touched = _reset_worktrees(worktrees, scrubber)
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
                    f"deleted {len(result.deleted_refs)} remote-tracking ref(s); remotes are NOT purged"
                )
        cmap = result.commit_map if result else {}
        rows = _manifest_apply(
            sp.db,
            mt.items,
            set(docs_paths),
            action_keys=set(mt.action_keys),
            scrubber=scrubber,
            commit_map=cmap,
            squashed=set(),
            tree_sha=head_tree,
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


def run_purge_queue(
    config: Config,
    *,
    gov: GovernanceConfig | None = None,
    dry_run: bool = False,
    lock: bool = True,
    now: datetime | None = None,
) -> list[PurgeReport]:
    """Run every queued purge; a held or failing one stays queued (logged), the rest are dequeued.

    ``dry_run`` reports each queued purge without writing anything, and leaves the queue as it is.
    """
    gov = gov or load_governance(config.config_path)
    sp = config.state_paths
    reports: list[PurgeReport] = []
    with _maybe_lock(sp, lock):
        remaining: list[QueuedPurge] = []
        for q in pending_purges(sp.root):
            try:
                rep = purge(
                    config, q.selector, reason=q.reason, gov=gov, dry_run=dry_run, lock=False, now=now
                )
            except GovernanceError as exc:
                log.warning("governance: queued purge kept: %s", exc)
                remaining.append(q)
                continue
            reports.append(rep)
            if not rep.verified:
                remaining.append(q)
        if not dry_run:
            _write_queue(sp.root, remaining)
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
        return "ok", f"no commit older than {span} day(s) (history_days {gov.history_days} + slack)"
    late = dataclasses.replace(gov, compaction_slack_days=2 * gov.compaction_slack_days)
    if compaction_due(repo, late, now=now):
        return (
            "overdue",
            f"history holds commits older than {gov.history_days + 2 * gov.compaction_slack_days} day(s): "
            "retention compaction has not run",
        )
    return "due", f"history holds commits older than {span} day(s); the next RECONCILE compacts them"


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
                f"{len(squash)} commit(s) older than {days} day(s) were squashed into this snapshot "
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
    files)."""
    sp = config.state_paths
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
        *migration_backups(sp.db),
    )


_TM_TRANSIENT = ("-wal", "-shm")


def time_machine_status(config: Config, *, runner: Runner | None = None) -> list[tuple[Path, bool | None]]:
    """(path, excluded?) per exclusion path; None when the path does not exist.

    Read with getxattr(2); an injected ``runner`` (tests) is asked ``xattr -p`` instead."""
    out: list[tuple[Path, bool | None]] = []
    for p in time_machine_exclusions(config):
        if not p.exists():
            out.append((p, None))
            continue
        if runner is None:
            out.append((p, _has_xattr(p, TM_EXCLUDE_XATTR)))
            continue
        cp = runner(["/usr/bin/xattr", "-p", TM_EXCLUDE_XATTR, str(p)])
        out.append((p, cp.returncode == 0))
    return out


def ensure_time_machine_exclusions(config: Config, *, runner: Runner | None = None) -> list[str]:
    """Apply the exclusions to every existing, durable path that lacks one (C15 req 42 outside
    ``install-agent``: ``init``, the first ``sync``); an empty list when nothing needed doing.

    Skipped off macOS and when ``AGENTSYNC_TM_EXCLUDE=0``; ``-wal``/``-shm`` come and go with connections, so
    they are applied when present but never re-checked (a ``tmutil`` call costs seconds)."""
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
