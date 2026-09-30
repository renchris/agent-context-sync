"""SQLite manifest: items keyed on stable identity, outputs, tombstones, cursors, runs (owner: manifest).

Contract: docs/design/CONTRACTS.md section "manifest.py".  The schema below IS the contract; change it only
together with MANIFEST_SCHEMA_VERSION and a migration (``_MIGRATIONS``, applied by ``Manifest.migrate``).

Durability: the database runs in WAL mode with ``synchronous=FULL``; every public write is atomic on its own
(one statement, or an internal transaction/savepoint for multi-statement writes), and ``Manifest.transaction``
groups the per-pass snapshot (item upserts, safe-save rekeys, the pending cursor) into ONE commit.
Secrets: cursor values (delta links) are never logged; ``cursor_fingerprint`` is the only derived form shown.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import logging
import os
import sqlite3
import time
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import Any, NamedTuple

from agentsync.config import SourceConfig
from agentsync.errors import ConfigError, ManifestSchemaError
from agentsync.model import (
    ChangeOp,
    CycleMode,
    ExtraValue,
    MirrorChange,
    OutputStatus,
    PassKind,
    RowState,
    SourceItem,
    SourceKind,
    SourceState,
    TreeLookup,
    Verdict,
)

_log = logging.getLogger(__name__)

MANIFEST_SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,           -- manifest_schema_version | key_schema_version | tree_sha | written_at_ns
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
  source_id            TEXT PRIMARY KEY,
  kind                 TEXT NOT NULL,                  -- SourceKind value; immutable for an id
  config_state         TEXT NOT NULL,                  -- SourceState value as last configured
  config_fingerprint   TEXT NOT NULL,                  -- sha256 of scope keys (path/drive/folder/...)
  enumeration_complete INTEGER NOT NULL DEFAULT 0,     -- set only by a finished FULL pass of the scope
  baseline_complete    INTEGER NOT NULL DEFAULT 0,     -- first full enumeration done (bootstrap over)
  last_full_run_id     INTEGER,
  last_success_run_id  INTEGER,
  breaker_tripped_at   TEXT,                           -- UTC ISO-8601, NULL = not tripped
  breaker_until        TEXT,                           -- UTC ISO-8601
  breaker_candidates   INTEGER,
  auth_state           TEXT NOT NULL DEFAULT 'ok'      -- ok | REAUTH_REQUIRED
);

CREATE TABLE IF NOT EXISTS items (
  source_id          TEXT NOT NULL REFERENCES sources(source_id),
  stable_id          TEXT NOT NULL,                    -- NEVER the path
  parent_id          TEXT,
  name               TEXT NOT NULL,
  rel_path           TEXT NOT NULL,                    -- derived, NFC, POSIX, relative to the source root
  prev_path          TEXT,                             -- previous rel_path when the last change was a rename
  is_dir             INTEGER NOT NULL DEFAULT 0,
  size               INTEGER,
  mtime_ns           INTEGER,
  ctime_ns           INTEGER,
  created_ns         INTEGER,
  ino                INTEGER,
  mode               INTEGER,
  gen_count          INTEGER,
  dataless           INTEGER NOT NULL DEFAULT 0,
  quickxor           TEXT,                             -- provider hashes: comparable only to themselves
  sha1_remote        TEXT,
  sha256_remote      TEXT,
  etag               TEXT,
  ctag               TEXT,
  content_type       TEXT,
  content_sha256     TEXT,                             -- sha256 of fetched bytes; NULL = never materialised
  canonical_sha256   TEXT,                             -- H1
  canonical_method   TEXT,
  canonical_parts    TEXT,                             -- JSON [[part, sha256], ...] sorted, OOXML only
  state              TEXT NOT NULL CHECK (state IN ('live','dataless','tombstone','quarantined','refused')),
  state_reason       TEXT,
  last_verdict       TEXT,                             -- Verdict value; DEFERRED/ERROR rows are pending work
  first_seen_run     INTEGER NOT NULL,
  last_seen_run      INTEGER NOT NULL,
  principal          TEXT,
  sensitivity_label  TEXT,
  extra_json         TEXT NOT NULL DEFAULT '{}',       -- json.dumps(extra, sort_keys=True)
  PRIMARY KEY (source_id, stable_id)
);
CREATE INDEX IF NOT EXISTS items_by_path ON items(source_id, rel_path);
CREATE INDEX IF NOT EXISTS items_by_parent ON items(source_id, parent_id);
CREATE INDEX IF NOT EXISTS items_by_canonical ON items(canonical_sha256);
CREATE INDEX IF NOT EXISTS items_by_verdict ON items(source_id, last_verdict);

CREATE TABLE IF NOT EXISTS cursors (                   -- SECRET: never exported, never in docs/
  source_id       TEXT PRIMARY KEY REFERENCES sources(source_id),
  current         TEXT,                                -- last cursor whose change set is committed in git
  current_set_at  TEXT,                                -- UTC ISO-8601
  pending         TEXT,                                -- cursor from the running cycle; promoted after commit
  pending_run_id  INTEGER,
  page_link       TEXT                                 -- @odata.nextLink of an interrupted enumeration
);

CREATE TABLE IF NOT EXISTS cache (                     -- index of the on-disk write-once converter cache
  action_key        TEXT PRIMARY KEY,
  converter_id      TEXT NOT NULL,
  converter_version TEXT NOT NULL,
  options_hash      TEXT NOT NULL,
  canonical_sha256  TEXT NOT NULL,
  status            TEXT NOT NULL,                     -- ConversionStatus value
  unit_count        INTEGER NOT NULL,
  bytes             INTEGER NOT NULL,
  created_run       INTEGER NOT NULL,
  last_used_run     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS outputs (
  output_path       TEXT PRIMARY KEY,                  -- docs-repo-relative, e.g. mirror/<source>/a/b.docx.md
  source_id         TEXT NOT NULL,
  stable_id         TEXT NOT NULL,
  unit_id           TEXT NOT NULL,                     -- whole | index | sheet:<n> | summary
  action_key        TEXT,
  rendered_sha256   TEXT,                              -- H2 (body only)
  page_sha256       TEXT,                              -- sha256 of the whole file (frontmatter + body)
  converter_id      TEXT,
  converter_version TEXT,
  options_hash      TEXT,
  status            TEXT NOT NULL
                    CHECK (status IN ('ok','failed','skipped-dataless','quarantined','refused','tombstone')),
  built_run         INTEGER NOT NULL,
  UNIQUE (source_id, stable_id, unit_id)
);
CREATE INDEX IF NOT EXISTS outputs_by_item ON outputs(source_id, stable_id);
CREATE INDEX IF NOT EXISTS outputs_by_key ON outputs(action_key);

CREATE TABLE IF NOT EXISTS tombstones (
  output_path          TEXT PRIMARY KEY,
  source_id            TEXT NOT NULL,
  stable_id            TEXT NOT NULL,
  unit_id              TEXT NOT NULL,
  deleted_at           TEXT NOT NULL,                  -- UTC date YYYY-MM-DD of the tombstoning run
  deleted_run          INTEGER NOT NULL,
  last_rendered_sha256 TEXT,
  last_commit          TEXT,                           -- git sha whose tree holds the last live content
  reap_after           TEXT NOT NULL,                  -- UTC date; reaped (file removed) on/after this
  reason               TEXT NOT NULL                   -- deleted-upstream|unit-removed|moved|retired:<why>
);

CREATE TABLE IF NOT EXISTS runs (
  run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  mode         TEXT NOT NULL,                          -- CycleMode value
  started_at   TEXT NOT NULL,                          -- UTC ISO-8601 (run metadata, never in docs content)
  finished_at  TEXT,
  status       TEXT NOT NULL DEFAULT 'running',        -- running | ok | partial | failed | aborted
  commit_sha   TEXT,
  host         TEXT NOT NULL,
  pid          INTEGER NOT NULL,
  counts_json  TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS run_sources (
  run_id               INTEGER NOT NULL REFERENCES runs(run_id),
  source_id            TEXT NOT NULL,
  pass_kind            TEXT,                           -- PassKind value; NULL = skipped
  enumeration_complete INTEGER NOT NULL DEFAULT 0,
  cursor_reset         INTEGER NOT NULL DEFAULT 0,
  counts_json          TEXT NOT NULL DEFAULT '{}',
  skipped_reason       TEXT,
  error                TEXT,
  PRIMARY KEY (run_id, source_id)
);

CREATE TABLE IF NOT EXISTS depends (                   -- mirror of docs/DEPENDS.tsv for reverse lookups
  page       TEXT NOT NULL,                            -- docs-repo-relative topics/... path
  source     TEXT NOT NULL,                            -- docs-repo-relative mirror/... path
  pinned_sha TEXT NOT NULL,
  role       TEXT NOT NULL,
  PRIMARY KEY (page, source)
);
CREATE INDEX IF NOT EXISTS depends_by_source ON depends(source);
"""


@dataclass(frozen=True, slots=True)
class SourceRow:
    """One ``sources`` row."""

    source_id: str
    kind: SourceKind
    config_state: SourceState
    config_fingerprint: str
    enumeration_complete: bool
    baseline_complete: bool
    last_full_run_id: int | None
    last_success_run_id: int | None
    breaker_tripped_at: str | None
    breaker_until: str | None
    breaker_candidates: int | None
    auth_state: str


@dataclass(frozen=True, slots=True)
class ItemRow:
    """One ``items`` row."""

    source_id: str
    stable_id: str
    parent_id: str | None
    name: str
    rel_path: str
    prev_path: str | None
    is_dir: bool
    size: int | None
    mtime_ns: int | None
    ctime_ns: int | None
    created_ns: int | None
    ino: int | None
    mode: int | None
    gen_count: int | None
    dataless: bool
    quickxor: str | None
    sha1_remote: str | None
    sha256_remote: str | None
    etag: str | None
    ctag: str | None
    content_type: str | None
    content_sha256: str | None
    canonical_sha256: str | None
    canonical_method: str | None
    canonical_parts: tuple[tuple[str, str], ...]
    state: RowState
    state_reason: str | None
    last_verdict: Verdict | None
    first_seen_run: int
    last_seen_run: int
    principal: str | None
    sensitivity_label: str | None
    extra: Mapping[str, ExtraValue] = field(default_factory=dict, hash=False, compare=False)


@dataclass(frozen=True, slots=True)
class OutputRow:
    """One ``outputs`` row."""

    output_path: str
    source_id: str
    stable_id: str
    unit_id: str
    action_key: str | None
    rendered_sha256: str | None
    page_sha256: str | None
    converter_id: str | None
    converter_version: str | None
    options_hash: str | None
    status: OutputStatus
    built_run: int


@dataclass(frozen=True, slots=True)
class TombstoneRow:
    """One ``tombstones`` row."""

    output_path: str
    source_id: str
    stable_id: str
    unit_id: str
    deleted_at: str
    deleted_run: int
    last_rendered_sha256: str | None
    last_commit: str | None
    reap_after: str
    reason: str


@dataclass(frozen=True, slots=True)
class CursorRow:
    """One ``cursors`` row (secret: never logged, never exported; see ``cursor_fingerprint``)."""

    source_id: str
    current: str | None
    current_set_at: str | None
    pending: str | None
    pending_run_id: int | None
    page_link: str | None


@dataclass(frozen=True, slots=True)
class DependsRow:
    """One DEPENDS.tsv / ``depends`` row; paths are docs-repo-relative."""

    page: str
    source: str
    pinned_sha: str
    role: str


REDACTED_PREFIX = "[redacted: credential in name] "


def redacted_path(rel_path: str) -> str:
    """The form an item path that carries a credential takes in every committed file (a 12-hex digest)."""
    return REDACTED_PREFIX + hashlib.sha256(rel_path.encode("utf-8")).hexdigest()[:12]


def cursor_fingerprint(cursor: str | None) -> str:
    """Return the 12-hex sha256 prefix of a cursor for STATE.md (never the token itself); "-" for None."""
    if cursor is None:
        return "-"
    return hashlib.sha256(cursor.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------------------------------------
# private helpers and constants
# ---------------------------------------------------------------------------------------------------------

ALIASES_SQL = """
CREATE TABLE IF NOT EXISTS item_aliases (              -- retired stable ids (safe-save / re-identification)
  source_id  TEXT NOT NULL,
  alias_id   TEXT NOT NULL,                            -- an id the item carried before a rekey
  stable_id  TEXT NOT NULL,                            -- the item's current id
  origin     INTEGER NOT NULL DEFAULT 0,               -- 1 = the item's first id: its durable key
  PRIMARY KEY (source_id, alias_id)
);
CREATE INDEX IF NOT EXISTS item_aliases_by_item ON item_aliases(source_id, stable_id);
CREATE TABLE IF NOT EXISTS redacted_items (            -- items whose NAME/path carries a credential
  source_id  TEXT NOT NULL,
  stable_id  TEXT NOT NULL,
  PRIMARY KEY (source_id, stable_id)
);
CREATE TABLE IF NOT EXISTS run_changes (               -- a run's mirror changes, durable as they happen
  run_id     INTEGER NOT NULL,
  seq        INTEGER NOT NULL,
  op         TEXT NOT NULL,
  path       TEXT NOT NULL,
  source_id  TEXT NOT NULL,
  stable_id  TEXT NOT NULL,
  prev_path  TEXT,
  PRIMARY KEY (run_id, seq)
);
"""
"""Additive table (created on open, no schema-version bump; an older build ignores it).

``Manifest.rekey`` records every retired id here, so a purge by any id the item ever carried reaches every
version in history, and the durable key (``origin`` = 1, else the current id) is what pages and shards name:
a same-content safe-save or a volume-UUID change moves no committed byte (design 4.1 #2, contract 5.1)."""

_MIGRATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version     INTEGER PRIMARY KEY,
  description TEXT NOT NULL,
  applied_at  TEXT NOT NULL                          -- UTC ISO-8601 (state metadata, never in docs/)
);
"""


@dataclass(frozen=True, slots=True)
class _Migration:
    """One forward-only schema step; version N brings the schema from N-1 to N."""

    version: int
    description: str
    sql: str


_MIGRATIONS: tuple[_Migration, ...] = (_Migration(1, "initial schema (CONTRACTS.md section 5)", SCHEMA_SQL),)
"""Every schema step ever shipped, in order.  Append (never edit) and bump MANIFEST_SCHEMA_VERSION."""

_PENDING_VERDICTS: tuple[Verdict, ...] = (
    Verdict.CREATED,
    Verdict.MAYBE_CHANGED,
    Verdict.CHANGED,
    Verdict.DEFERRED,
    Verdict.ERROR,
)
"""Row verdicts that mean "observed but not yet published" (CONTRACTS.md section 5, pending work)."""

_PRESENT_STATES: tuple[RowState, ...] = (
    RowState.LIVE,
    RowState.DATALESS,
    RowState.QUARANTINED,
    RowState.REFUSED,
)
"""Row states of an object that exists upstream (everything except a tombstone)."""

_EXPORT_STATES: tuple[RowState, ...] = (*_PRESENT_STATES, RowState.TOMBSTONE)

_AUTH_STATES = frozenset({"ok", "REAUTH_REQUIRED"})
_REMOVAL_KEYS = ("removed", "removed_reason")
_RUN_FINAL_STATUSES = frozenset({"ok", "partial", "failed", "aborted"})

_ITEM_COLUMNS = (
    "source_id, stable_id, parent_id, name, rel_path, prev_path, is_dir, size, mtime_ns, ctime_ns, "
    "created_ns, "
    "ino, mode, gen_count, dataless, quickxor, sha1_remote, sha256_remote, etag, ctag, content_type, "
    "content_sha256, canonical_sha256, canonical_method, canonical_parts, state, state_reason, last_verdict, "
    "first_seen_run, last_seen_run, principal, sensitivity_label, extra_json"
)
_OUTPUT_COLUMNS = (
    "output_path, source_id, stable_id, unit_id, action_key, rendered_sha256, page_sha256, converter_id, "
    "converter_version, options_hash, status, built_run"
)
_TOMBSTONE_COLUMNS = (
    "output_path, source_id, stable_id, unit_id, deleted_at, deleted_run, last_rendered_sha256, last_commit, "
    "reap_after, reason"
)
_SOURCE_COLUMNS = (
    "source_id, kind, config_state, config_fingerprint, enumeration_complete, baseline_complete, "
    "last_full_run_id, last_success_run_id, breaker_tripped_at, breaker_until, breaker_candidates, auth_state"
)

_FOLD_SQL_FUNCTION = "agentsync_fold"
_RESET_HASHES_SQL = (
    "UPDATE items SET content_sha256 = NULL, canonical_sha256 = NULL, canonical_method = NULL, "
    "canonical_parts = NULL WHERE source_id = ? AND stable_id = ?"
)
_UPDATE_ITEM_SQL = (
    "UPDATE items SET parent_id = ?, name = ?, rel_path = ?, prev_path = ?, is_dir = ?, size = ?, "
    "mtime_ns = ?, ctime_ns = ?, ino = ?, mode = ?, gen_count = ?, created_ns = ?, dataless = ?, "
    "quickxor = ?, sha1_remote = ?, sha256_remote = ?, etag = ?, ctag = ?, content_type = ?, "
    "state = ?, state_reason = {reason}, last_verdict = ?, last_seen_run = ?, extra_json = ? "
    "WHERE source_id = ? AND stable_id = ?"
)
_INSERT_ITEM_SQL = (
    "INSERT INTO items (source_id, stable_id, parent_id, name, rel_path, prev_path, is_dir, "
    "size, mtime_ns, ctime_ns, created_ns, ino, mode, gen_count, dataless, quickxor, "
    "sha1_remote, sha256_remote, etag, ctag, content_type, state, last_verdict, first_seen_run, "
    "last_seen_run, extra_json) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
    "?, ?, ?, ?, ?, ?, ?, ?)"
)


def _key_schema_version() -> int:
    """The converter cache's KEY_SCHEMA_VERSION (imported lazily: a constant, not a call into convert)."""
    from agentsync.convert.cache import KEY_SCHEMA_VERSION  # noqa: PLC0415 - lazy: keep manifest import light

    return KEY_SCHEMA_VERSION


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _fold(text: str | None) -> str | None:
    """NFC + casefold key, the same notion of 'same path' APFS (case-insensitive) applies."""
    if text is None:
        return None
    return _nfc(_nfc(text).casefold())


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp; naive values are taken as UTC."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _check_date(value: str, what: str) -> None:
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{what} must be a UTC date YYYY-MM-DD, got {value!r}") from None
    if len(value) != 10:
        raise ValueError(f"{what} must be a UTC date YYYY-MM-DD, got {value!r}")


def _check_sha256(value: str, what: str) -> None:
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{what} must be 64 lowercase hex chars")


@functools.cache
def _encoder() -> json.JSONEncoder:
    """One deterministic encoder: ``json.dumps`` with non-default options builds a new one per call."""
    return json.JSONEncoder(sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _json_dumps(obj: object) -> str:
    return _encoder().encode(obj)


def _opt_bool(value: Any) -> bool:
    return bool(value)


_VERDICT_BY_VALUE: Mapping[str, Verdict] = MappingProxyType({v.value: v for v in Verdict})
_STATE_BY_VALUE: Mapping[str, RowState] = MappingProxyType({s.value: s for s in RowState})
_EMPTY_JSON = frozenset({"", "{}", "[]"})
_ID_CHUNK = (
    900  # stable ids per ``IN (...)`` query (SQLite's default host-parameter limit is 999 on old builds)
)


def _extra_from_json(raw: str | None) -> dict[str, ExtraValue]:
    if raw is None or raw in _EMPTY_JSON:
        return {}
    value: dict[str, ExtraValue] = json.loads(raw)
    return value


class _ObservedRow(NamedTuple):
    """The columns phase 1 compares an observation against (``Manifest.observation_index``).

    Field names match ``ItemRow`` so the classifier reads either; building one costs a fraction of an
    ``ItemRow`` (no JSON parts, no enum construction beyond two dict lookups), which is what lets a no-op
    pass over 100k rows skip the full row decode.
    """

    stable_id: str
    parent_id: str | None
    name: str
    rel_path: str
    is_dir: bool
    size: int | None
    mtime_ns: int | None
    ctime_ns: int | None
    created_ns: int | None
    ino: int | None
    mode: int | None
    gen_count: int | None
    dataless: bool
    quickxor: str | None
    sha1_remote: str | None
    sha256_remote: str | None
    etag: str | None
    ctag: str | None
    content_type: str | None
    content_sha256: str | None
    state: RowState
    last_verdict: Verdict | None
    extra: Mapping[str, ExtraValue]


def _scope_fingerprint(src: SourceConfig) -> str:
    """sha256 over every key that defines WHAT a source enumerates; a change invalidates cursor and listing.

    Beyond path/drive/folder/mailbox/team/channel this includes include/exclude globs (a widened scope must be
    re-enumerated or a delta cursor would never report the newly included items) and the principal (a
    delegated view is per principal; another principal's cursor is not ours).
    """
    scope: dict[str, object] = {
        "kind": src.kind.value,
        "path": str(src.path) if src.path is not None else None,
        "drive_id": src.drive_id,
        "site": src.site,
        "folder": src.folder,
        "mailbox": src.mailbox,
        "team_id": src.team_id,
        "channel_id": src.channel_id,
        "include": list(src.include),
        "exclude": list(src.exclude),
        "principal": src.principal,
    }
    return hashlib.sha256(_json_dumps(scope).encode("utf-8")).hexdigest()


def _item_from_row(r: sqlite3.Row) -> ItemRow:
    """Decode one ``SELECT {_ITEM_COLUMNS}`` row (positional: name lookups on sqlite3.Row are O(columns))."""
    (
        source_id,
        stable_id,
        parent_id,
        name,
        rel_path,
        prev_path,
        is_dir,
        size,
        mtime_ns,
        ctime_ns,
        created_ns,
        ino,
        mode,
        gen_count,
        dataless,
        quickxor,
        sha1_remote,
        sha256_remote,
        etag,
        ctag,
        content_type,
        content_sha256,
        canonical_sha256,
        canonical_method,
        canonical_parts,
        state,
        state_reason,
        last_verdict,
        first_seen_run,
        last_seen_run,
        principal,
        sensitivity_label,
        extra_json,
    ) = tuple(r)
    if canonical_parts is None or canonical_parts in _EMPTY_JSON:
        parts: tuple[tuple[str, str], ...] = ()
    else:
        parts = tuple((str(p[0]), str(p[1])) for p in json.loads(canonical_parts))
    return ItemRow(
        source_id,
        stable_id,
        parent_id,
        name,
        rel_path,
        prev_path,
        bool(is_dir),
        size,
        mtime_ns,
        ctime_ns,
        created_ns,
        ino,
        mode,
        gen_count,
        bool(dataless),
        quickxor,
        sha1_remote,
        sha256_remote,
        etag,
        ctag,
        content_type,
        content_sha256,
        canonical_sha256,
        canonical_method,
        parts,
        _STATE_BY_VALUE[state],
        state_reason,
        None if last_verdict is None else _VERDICT_BY_VALUE[last_verdict],
        first_seen_run,
        last_seen_run,
        principal,
        sensitivity_label,
        _extra_from_json(extra_json),
    )


_OBSERVED_COLUMNS = (
    "stable_id, parent_id, name, rel_path, is_dir, size, mtime_ns, ctime_ns, created_ns, ino, mode, "
    "gen_count, dataless, quickxor, sha1_remote, sha256_remote, etag, ctag, content_type, content_sha256, "
    "state, last_verdict, extra_json"
)


def _observed_from_tuple(t: tuple[Any, ...]) -> _ObservedRow:
    verdict = t[21]
    return _ObservedRow(
        t[0],
        t[1],
        t[2],
        t[3],
        bool(t[4]),
        t[5],
        t[6],
        t[7],
        t[8],
        t[9],
        t[10],
        t[11],
        bool(t[12]),
        t[13],
        t[14],
        t[15],
        t[16],
        t[17],
        t[18],
        t[19],
        _STATE_BY_VALUE[t[20]],
        None if verdict is None else _VERDICT_BY_VALUE[verdict],
        _extra_from_json(t[22]),
    )


def _output_from_row(r: sqlite3.Row) -> OutputRow:
    return OutputRow(
        output_path=r["output_path"],
        source_id=r["source_id"],
        stable_id=r["stable_id"],
        unit_id=r["unit_id"],
        action_key=r["action_key"],
        rendered_sha256=r["rendered_sha256"],
        page_sha256=r["page_sha256"],
        converter_id=r["converter_id"],
        converter_version=r["converter_version"],
        options_hash=r["options_hash"],
        status=OutputStatus(r["status"]),
        built_run=r["built_run"],
    )


def _tombstone_from_row(r: sqlite3.Row) -> TombstoneRow:
    return TombstoneRow(
        output_path=r["output_path"],
        source_id=r["source_id"],
        stable_id=r["stable_id"],
        unit_id=r["unit_id"],
        deleted_at=r["deleted_at"],
        deleted_run=r["deleted_run"],
        last_rendered_sha256=r["last_rendered_sha256"],
        last_commit=r["last_commit"],
        reap_after=r["reap_after"],
        reason=r["reason"],
    )


def _source_from_row(r: sqlite3.Row) -> SourceRow:
    return SourceRow(
        source_id=r["source_id"],
        kind=SourceKind(r["kind"]),
        config_state=SourceState(r["config_state"]),
        config_fingerprint=r["config_fingerprint"],
        enumeration_complete=_opt_bool(r["enumeration_complete"]),
        baseline_complete=_opt_bool(r["baseline_complete"]),
        last_full_run_id=r["last_full_run_id"],
        last_success_run_id=r["last_success_run_id"],
        breaker_tripped_at=r["breaker_tripped_at"],
        breaker_until=r["breaker_until"],
        breaker_candidates=r["breaker_candidates"],
        auth_state=r["auth_state"],
    )


def _connect(db_path: Path) -> sqlite3.Connection:
    """Create the db file 0600 if needed, connect in autocommit mode and apply the connection pragmas."""
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(db_path, os.O_RDWR | os.O_CREAT, 0o600)
    os.close(fd)
    db_path.chmod(0o600)
    conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=30.0)
    conn.row_factory = sqlite3.Row
    mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if str(mode).lower() != "wal":
        conn.close()
        raise ManifestSchemaError(f"{db_path}: could not enable WAL journal mode (got {mode!r})")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    # A pass reads the whole items table twice (observation index, shard export) and stamps every row: a
    # 64 MiB page cache (default 2 MiB) keeps a 100k-file manifest resident (memory only; durable as before).
    conn.execute("PRAGMA cache_size=-65536")
    conn.create_function(_FOLD_SQL_FUNCTION, 1, _fold, deterministic=True)
    return conn


def _user_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(r[0]) for r in rows}


def _read_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row[0])


def _write_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _stored_version(conn: sqlite3.Connection, key: str, db_path: Path) -> int:
    raw = _read_meta(conn, key)
    if raw is None:
        raise ManifestSchemaError(f"{db_path}: meta.{key} is missing; not a usable agentsync manifest")
    try:
        return int(raw)
    except ValueError:
        raise ManifestSchemaError(f"{db_path}: meta.{key} is not an integer: {raw!r}") from None


def _apply_migrations(
    conn: sqlite3.Connection, migrations: Sequence[_Migration], from_version: int, to_version: int
) -> list[int]:
    """Apply steps (from_version, to_version] in the caller's transaction; record each; return versions."""
    conn.execute(_MIGRATIONS_TABLE_SQL.strip())
    by_version = {m.version: m for m in migrations}
    applied: list[int] = []
    for version in range(from_version + 1, to_version + 1):
        step = by_version.get(version)
        if step is None:
            raise ManifestSchemaError(f"no migration step to schema version {version}")
        for statement in _split_sql(step.sql):
            conn.execute(statement)
        conn.execute(
            "INSERT OR REPLACE INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
            (version, step.description, _utc_now_iso()),
        )
        applied.append(version)
    return applied


def _split_sql(script: str) -> list[str]:
    """Split a DDL script into complete statements (``executescript`` would COMMIT our transaction)."""
    out: list[str] = []
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                out.append(buf.strip())
            buf = ""
    if buf.strip():
        raise ManifestSchemaError(f"incomplete SQL statement in migration: {buf.strip()[:60]!r}")
    return out


# ---------------------------------------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------------------------------------


class Manifest:
    """The SQLite working store.  One instance per cycle; not thread-safe; single writer by the ops lock."""

    def __init__(self, db_path: Path) -> None:
        """Open (creating with mode 0600, WAL, synchronous=FULL, foreign_keys=ON) and migrate-check the db.

        Raises ManifestSchemaError when ``meta.manifest_schema_version`` (or the converter cache's
        ``key_schema_version``) differs from this build; never silently re-derives.
        """
        self._path = db_path
        self._in_tx = False
        self._savepoint_seq = 0
        # fold(output_path) -> output paths: output_by_path's case/NFC-insensitive lookup without a full-table
        # scan per miss (every new page misses).  Built lazily, updated by replace_outputs, dropped on any
        # rollback; hits are re-read from the table, so a superset is harmless.
        self._fold_paths: dict[str, set[str]] | None = None
        self._conn: sqlite3.Connection | None = _connect(db_path)
        try:
            self._initialise_or_check()
        except BaseException:
            self._conn.close()
            self._conn = None
            raise

    # ---- lifecycle --------------------------------------------------------------------------------------
    @property
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            raise sqlite3.ProgrammingError("manifest is closed")
        return self._conn

    def _initialise_or_check(self) -> None:
        conn = self._db
        tables = _user_tables(conn)
        if not tables:
            conn.execute("BEGIN IMMEDIATE")
            try:
                applied = _apply_migrations(conn, _MIGRATIONS, 0, MANIFEST_SCHEMA_VERSION)
                _write_meta(conn, "manifest_schema_version", str(MANIFEST_SCHEMA_VERSION))
                _write_meta(conn, "key_schema_version", str(_key_schema_version()))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            self._ensure_aliases()
            _log.info("created manifest %s at schema version %s", self._path, applied[-1] if applied else 0)
            return
        if "meta" not in tables:
            raise ManifestSchemaError(
                f"{self._path}: has tables but no meta table; not an agentsync manifest"
            )
        stored = _stored_version(conn, "manifest_schema_version", self._path)
        if stored != MANIFEST_SCHEMA_VERSION:
            hint = (
                "run `agentsync migrate` (Manifest.migrate)"
                if stored < MANIFEST_SCHEMA_VERSION
                else "it was written by a newer agentsync; upgrade this install"
            )
            raise ManifestSchemaError(
                f"{self._path}: manifest schema version {stored}, this build needs "
                f"{MANIFEST_SCHEMA_VERSION}; {hint}"
            )
        key_stored = _stored_version(conn, "key_schema_version", self._path)
        key_build = _key_schema_version()
        if key_stored != key_build:
            raise ManifestSchemaError(
                f"{self._path}: converter key schema version {key_stored}, this build uses {key_build}; "
                "run `agentsync migrate` (Manifest.migrate) to re-index the converter cache"
            )
        if not {"item_aliases", "redacted_items", "run_changes"} <= tables:
            self._ensure_aliases()
        if "schema_migrations" not in tables:
            # A version-1 manifest created before the migrations ledger existed: record its baseline.
            conn.execute(_MIGRATIONS_TABLE_SQL.strip())
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
                (stored, "baseline (ledger backfilled)", _utc_now_iso()),
            )

    def _ensure_aliases(self) -> None:
        for statement in _split_sql(ALIASES_SQL):
            self._db.execute(statement)

    @classmethod
    def migrate(cls, db_path: Path) -> list[int]:
        """Bring an older manifest to this build's schema in ONE transaction; return the versions applied.

        Also re-indexes the converter cache when ``key_schema_version`` changed (the ``cache`` table is
        cleared: every old action key is unreachable under the new composition).  Raises ManifestSchemaError
        for a manifest newer than this build.  A missing/empty db is created at the current version.
        """
        conn = _connect(db_path)
        try:
            tables = _user_tables(conn)
            if not tables:
                conn.close()
                cls(db_path).close()
                return list(range(1, MANIFEST_SCHEMA_VERSION + 1))
            if "meta" not in tables:
                raise ManifestSchemaError(f"{db_path}: not an agentsync manifest (no meta table)")
            stored = _stored_version(conn, "manifest_schema_version", db_path)
            if stored > MANIFEST_SCHEMA_VERSION:
                raise ManifestSchemaError(
                    f"{db_path}: schema version {stored} is newer than this build ({MANIFEST_SCHEMA_VERSION})"
                )
            conn.execute("BEGIN IMMEDIATE")
            try:
                applied = _apply_migrations(conn, _MIGRATIONS, stored, MANIFEST_SCHEMA_VERSION)
                _write_meta(conn, "manifest_schema_version", str(MANIFEST_SCHEMA_VERSION))
                key_build = _key_schema_version()
                key_raw = _read_meta(conn, "key_schema_version")
                if key_raw != str(key_build):
                    conn.execute("DELETE FROM cache")
                    _write_meta(conn, "key_schema_version", str(key_build))
                    _log.warning("converter key schema %s -> %s: cache index cleared", key_raw, key_build)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            if applied:
                _log.info("migrated manifest %s: applied schema versions %s", db_path, applied)
            return applied
        finally:
            with contextlib.suppress(sqlite3.ProgrammingError):
                conn.close()

    def close(self) -> None:
        """Close the connection (idempotent)."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            self._in_tx = False

    def __enter__(self) -> Manifest:
        """Return self."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close."""
        self.close()

    def transaction(self) -> AbstractContextManager[None]:
        """BEGIN IMMEDIATE ... COMMIT (ROLLBACK on exception).  Not re-entrant: nesting raises
        RuntimeError."""
        return self._transaction()

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[None]:
        conn = self._db
        if self._in_tx or conn.in_transaction:
            raise RuntimeError("Manifest.transaction is not re-entrant")
        conn.execute("BEGIN IMMEDIATE")
        self._in_tx = True
        try:
            yield
        except BaseException:
            self._in_tx = False
            self._fold_paths = None
            with contextlib.suppress(sqlite3.OperationalError):  # already rolled back by sqlite itself
                conn.execute("ROLLBACK")
            raise
        self._in_tx = False
        conn.execute("COMMIT")

    @contextlib.contextmanager
    def _atomic(self) -> Iterator[None]:
        """Group a multi-statement write: a savepoint inside a caller transaction, else its own one."""
        conn = self._db
        if conn.in_transaction:
            self._savepoint_seq += 1
            name = f"agentsync_sp_{self._savepoint_seq}"
            conn.execute(f"SAVEPOINT {name}")
            try:
                yield
            except BaseException:
                self._fold_paths = None
                conn.execute(f"ROLLBACK TO {name}")
                conn.execute(f"RELEASE {name}")
                raise
            conn.execute(f"RELEASE {name}")
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._fold_paths = None
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    # ---- meta -------------------------------------------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        """Return a meta value or None."""
        return _read_meta(self._db, key)

    def set_meta(self, key: str, value: str) -> None:
        """Upsert a meta value."""
        _write_meta(self._db, key, value)

    def written_at_ns(self) -> int:
        """Return meta.written_at_ns (0 if unset): the racily-clean guard's reference time."""
        raw = self.get_meta("written_at_ns")
        if raw is None:
            return 0
        try:
            return int(raw)
        except ValueError:
            _log.warning("meta.written_at_ns is not an integer; treating as unset (every stat is racy)")
            return 0

    def applied_migrations(self) -> list[tuple[int, str]]:
        """Return (version, description) of every schema step recorded in ``schema_migrations``."""
        rows = self._db.execute(
            "SELECT version, description FROM schema_migrations ORDER BY version"
        ).fetchall()
        return [(int(r[0]), str(r[1])) for r in rows]

    # ---- runs -------------------------------------------------------------------------------------------
    def begin_run(self, mode: CycleMode, *, host: str, pid: int) -> int:
        """Insert a ``runs`` row with status 'running' and return its run_id (monotonic)."""
        cur = self._db.execute(
            "INSERT INTO runs (mode, started_at, status, host, pid) VALUES (?, ?, 'running', ?, ?)",
            (CycleMode(mode).value, _utc_now_iso(), host, pid),
        )
        run_id = cur.lastrowid
        if run_id is None:  # pragma: no cover - sqlite always sets it for an INSERT
            raise RuntimeError("runs insert returned no rowid")
        return int(run_id)

    def record_source_pass(
        self,
        run_id: int,
        source_id: str,
        *,
        pass_kind: PassKind | None,
        enumeration_complete: bool,
        cursor_reset: bool,
        counts: Mapping[str, int],
        skipped_reason: str | None = None,
        error: str | None = None,
    ) -> None:
        """Upsert the ``run_sources`` row for one source in one run."""
        self._db.execute(
            "INSERT INTO run_sources (run_id, source_id, pass_kind, enumeration_complete, cursor_reset, "
            "counts_json, skipped_reason, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(run_id, source_id) DO UPDATE SET pass_kind = excluded.pass_kind, "
            "enumeration_complete = excluded.enumeration_complete, cursor_reset = excluded.cursor_reset, "
            "counts_json = excluded.counts_json, skipped_reason = excluded.skipped_reason, "
            "error = excluded.error",
            (
                run_id,
                source_id,
                None if pass_kind is None else PassKind(pass_kind).value,
                int(enumeration_complete),
                int(cursor_reset),
                _json_dumps({str(k): int(v) for k, v in counts.items()}),
                skipped_reason,
                error,
            ),
        )

    def finish_run(
        self, run_id: int, *, status: str, commit_sha: str | None, counts: Mapping[str, int]
    ) -> None:
        """Close a run row; also sets meta.written_at_ns to the current time_ns."""
        if status not in _RUN_FINAL_STATUSES:
            raise ValueError(f"run status must be one of {sorted(_RUN_FINAL_STATUSES)}, got {status!r}")
        with self._atomic():
            cur = self._db.execute(
                "UPDATE runs SET status = ?, finished_at = ?, commit_sha = ?, counts_json = ? "
                "WHERE run_id = ?",
                (
                    status,
                    _utc_now_iso(),
                    commit_sha,
                    _json_dumps({str(k): int(v) for k, v in counts.items()}),
                    run_id,
                ),
            )
            if cur.rowcount != 1:
                raise KeyError(f"no run {run_id}")
            _write_meta(self._db, "written_at_ns", str(time.time_ns()))

    def last_runs(self, limit: int = 10) -> list[tuple[int, str, str, str | None]]:
        """Return (run_id, mode, status, commit_sha) newest first."""
        rows = self._db.execute(
            "SELECT run_id, mode, status, commit_sha FROM runs ORDER BY run_id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [(int(r[0]), str(r[1]), str(r[2]), r[3]) for r in rows]

    # ---- sources ----------------------------------------------------------------------------------------
    def sync_sources(self, configured: Sequence[SourceConfig]) -> list[str]:
        """Upsert one ``sources`` row per configured source; return ids whose scope fingerprint changed.

        A changed fingerprint (path/drive/folder/mailbox/team/channel) clears ``enumeration_complete``
        and drops the cursor.  A kind change for an existing id raises ConfigError.
        """
        seen: set[str] = set()
        for src in configured:
            if src.id in seen:
                raise ConfigError(f"duplicate source id {src.id!r}")
            seen.add(src.id)
        changed: list[str] = []
        with self._atomic():
            for src in configured:
                fingerprint = _scope_fingerprint(src)
                existing = self.get_source(src.id)
                if existing is None:
                    self._db.execute(
                        "INSERT INTO sources (source_id, kind, config_state, config_fingerprint) "
                        "VALUES (?, ?, ?, ?)",
                        (src.id, src.kind.value, src.state.value, fingerprint),
                    )
                    continue
                if existing.kind is not src.kind:
                    raise ConfigError(
                        f"source {src.id!r} was {existing.kind.value!r} and is now {src.kind.value!r}; a "
                        "source's kind is immutable (retire it and add a new id)"
                    )
                if existing.config_fingerprint != fingerprint:
                    self._db.execute(
                        "UPDATE sources SET config_state = ?, config_fingerprint = ?, "
                        "enumeration_complete = 0, baseline_complete = 0 WHERE source_id = ?",
                        (src.state.value, fingerprint, src.id),
                    )
                    self._db.execute("DELETE FROM cursors WHERE source_id = ?", (src.id,))
                    _log.info("source %s: scope changed; cursor dropped, full enumeration required", src.id)
                    changed.append(src.id)
                elif existing.config_state is not src.state:
                    self._db.execute(
                        "UPDATE sources SET config_state = ? WHERE source_id = ?", (src.state.value, src.id)
                    )
        return sorted(changed)

    def get_source(self, source_id: str) -> SourceRow | None:
        """Return the ``sources`` row or None."""
        r = self._db.execute(
            f"SELECT {_SOURCE_COLUMNS} FROM sources WHERE source_id = ?", (source_id,)
        ).fetchone()
        return None if r is None else _source_from_row(r)

    def _update_source(self, sql: str, params: Sequence[object], source_id: str) -> None:
        cur = self._db.execute(sql, (*params, source_id))
        if cur.rowcount != 1:
            raise KeyError(f"no source {source_id!r}")

    def set_enumeration_complete(self, source_id: str, complete: bool, run_id: int) -> None:
        """Set the flag; True also sets baseline_complete and last_full_run_id."""
        if complete:
            self._update_source(
                "UPDATE sources SET enumeration_complete = 1, baseline_complete = 1, last_full_run_id = ? "
                "WHERE source_id = ?",
                (run_id,),
                source_id,
            )
        else:
            self._update_source(
                "UPDATE sources SET enumeration_complete = 0 WHERE source_id = ?", (), source_id
            )

    def set_last_success(self, source_id: str, run_id: int) -> None:
        """Record the last run in which this source's pass completed without error."""
        self._update_source(
            "UPDATE sources SET last_success_run_id = ? WHERE source_id = ?", (run_id,), source_id
        )

    def trip_breaker(self, source_id: str, *, candidates: int, tripped_at: str, until: str) -> None:
        """Record a tripped deletion breaker for the scope (removals suspended until ``until``)."""
        _parse_iso(tripped_at)
        _parse_iso(until)
        self._update_source(
            "UPDATE sources SET breaker_tripped_at = ?, breaker_until = ?, breaker_candidates = ? "
            "WHERE source_id = ?",
            (tripped_at, until, candidates),
            source_id,
        )
        _log.warning(
            "source %s: deletion breaker tripped (%d candidates) until %s", source_id, candidates, until
        )

    def clear_breaker(self, source_id: str) -> None:
        """Clear the breaker (operator action, or ``until`` passed with candidates under threshold)."""
        self._update_source(
            "UPDATE sources SET breaker_tripped_at = NULL, breaker_until = NULL, breaker_candidates = NULL "
            "WHERE source_id = ?",
            (),
            source_id,
        )

    def breaker_active(self, source_id: str, now_iso: str) -> bool:
        """True when the breaker is tripped and ``now_iso`` < breaker_until."""
        src = self.get_source(source_id)
        if src is None or src.breaker_tripped_at is None:
            return False
        if src.breaker_until is None:
            return True  # tripped without an expiry: held until an operator clears it
        return _parse_iso(now_iso) < _parse_iso(src.breaker_until)

    def set_auth_state(self, state: str, source_ids: Iterable[str]) -> None:
        """Set auth_state (``ok`` | ``REAUTH_REQUIRED``) on the given sources."""
        if state not in _AUTH_STATES:
            raise ValueError(f"auth_state must be one of {sorted(_AUTH_STATES)}, got {state!r}")
        ids = sorted(set(source_ids))
        with self._atomic():
            for sid in ids:
                self._update_source("UPDATE sources SET auth_state = ? WHERE source_id = ?", (state,), sid)

    # ---- items ------------------------------------------------------------------------------------------
    def get_item(self, source_id: str, stable_id: str) -> ItemRow | None:
        """Return the row or None."""
        r = self._db.execute(
            f"SELECT {_ITEM_COLUMNS} FROM items WHERE source_id = ? AND stable_id = ?", (source_id, stable_id)
        ).fetchone()
        return None if r is None else _item_from_row(r)

    def item_by_path(self, source_id: str, rel_path: str) -> ItemRow | None:
        """Return the non-tombstone row currently at ``rel_path`` (NFC, case-sensitive) or None."""
        r = self._db.execute(
            f"SELECT {_ITEM_COLUMNS} FROM items WHERE source_id = ? AND rel_path = ? "
            "AND state != 'tombstone' ORDER BY last_seen_run DESC, stable_id ASC LIMIT 1",
            (source_id, _nfc(rel_path)),
        ).fetchone()
        return None if r is None else _item_from_row(r)

    def iter_items(self, source_id: str, *, states: Iterable[RowState] | None = None) -> Iterator[ItemRow]:
        """Yield rows of one source ordered by stable_id, optionally filtered by state."""
        sql = f"SELECT {_ITEM_COLUMNS} FROM items WHERE source_id = ?"
        params: list[object] = [source_id]
        if states is not None:
            wanted = sorted({RowState(s).value for s in states})
            if not wanted:
                return
            sql += f" AND state IN ({', '.join('?' for _ in wanted)})"
            params.extend(wanted)
        sql += " ORDER BY stable_id"
        rows = self._db.execute(sql, params).fetchall()
        for r in rows:
            yield _item_from_row(r)

    def get_items(self, source_id: str, stable_ids: Iterable[str]) -> dict[str, ItemRow]:
        """Rows of one source for the given ids (absent ids are simply missing), in batched queries."""
        ids = sorted(set(stable_ids))
        out: dict[str, ItemRow] = {}
        for start in range(0, len(ids), _ID_CHUNK):
            chunk = ids[start : start + _ID_CHUNK]
            rows = self._db.execute(
                f"SELECT {_ITEM_COLUMNS} FROM items WHERE source_id = ? "
                f"AND stable_id IN ({', '.join('?' for _ in chunk)})",
                (source_id, *chunk),
            ).fetchall()
            for r in rows:
                row = _item_from_row(r)
                out[row.stable_id] = row
        return out

    def observation_index(self, source_id: str) -> dict[str, _ObservedRow]:
        """Every row of one source (tombstones and directories included) as the light tuple phase 1 compares.

        One query and no per-row JSON/enum decoding beyond two dict lookups: the H0 fast path of a pass
        (``ClassifyContext.h0_unchanged``, the local walk's getattrlist skip) reads this instead of decoding
        a full ``ItemRow`` per file.  Also the source of "which known ids were not observed" for a pass.
        """
        cur = self._db.cursor()
        cur.row_factory = None  # plain tuples: no sqlite3.Row wrapper per row
        rows = cur.execute(
            f"SELECT {_OBSERVED_COLUMNS} FROM items WHERE source_id = ?", (source_id,)
        ).fetchall()
        return {str(t[0]): _observed_from_tuple(t) for t in rows}

    def state_counts(self, source_id: str) -> tuple[dict[RowState, int], int]:
        """(file rows per state, file rows whose last_verdict is DEFERRED) of one source (STATE.md)."""
        by_state: dict[RowState, int] = {}
        deferred = 0
        for state, count, n_deferred in self._db.execute(
            "SELECT state, COUNT(*), SUM(last_verdict = ?) FROM items WHERE source_id = ? AND is_dir = 0 "
            "GROUP BY state ORDER BY state",
            (Verdict.DEFERRED.value, source_id),
        ).fetchall():
            by_state[_STATE_BY_VALUE[state]] = int(count)
            deferred += int(n_deferred or 0)
        return by_state, deferred

    def find_by_size(self, size: int) -> list[ItemRow]:
        """Live/dataless file rows (any source) of exactly ``size`` bytes: the inbox (name, size) dedup."""
        rows = self._db.execute(
            f"SELECT {_ITEM_COLUMNS} FROM items WHERE size = ? AND is_dir = 0 "
            "AND state IN ('live', 'dataless') ORDER BY source_id, stable_id",
            (size,),
        ).fetchall()
        return [_item_from_row(r) for r in rows]

    def live_count(self, source_id: str) -> int:
        """Count rows in state live|dataless (the breaker denominator), files only."""
        r = self._db.execute(
            "SELECT COUNT(*) FROM items WHERE source_id = ? AND is_dir = 0 AND state IN ('live', 'dataless')",
            (source_id,),
        ).fetchone()
        return int(r[0])

    def tree_lookup(self, source_id: str) -> TreeLookup:
        """Return a callable ``stable_id -> (parent_id, name)`` over this source's rows (None if unknown)."""

        def lookup(stable_id: str) -> tuple[str | None, str] | None:
            r = self._db.execute(
                "SELECT parent_id, name FROM items WHERE source_id = ? AND stable_id = ?",
                (source_id, stable_id),
            ).fetchone()
            return None if r is None else (r[0], str(r[1]))

        return lookup

    def upsert_observed(self, item: SourceItem, *, run_id: int, verdict: Verdict, state: RowState) -> None:
        """Insert or update a row from an observation: all H0/provider fields, last_verdict, last_seen_run.

        Content hashes are NOT touched here (see ``set_content``).  A rel_path change stores prev_path.
        Two refinements keep the phase-1 signals honest: an explicit provider tombstone (``item.deleted``)
        updates only verdict/state/last_seen_run (a deleted record carries no trustworthy path or stat), and a
        DATALESS observation of an existing row keeps the H0 stat fields of the last materialised
        observation, so a change that arrived while the file was a placeholder is still seen at hydration.
        One exception to "content hashes untouched": a tombstoned row observed live again (reappearance)
        forgets its content/canonical hashes, so phase 2 says CHANGED and its pages are rebuilt instead of
        staying tombstone stubs (the conversion itself is still a converter-cache hit).
        """
        self._apply_upsert(
            self._upsert_plan(item, run_id, verdict, state, self.get_item(item.source_id, item.stable_id))
        )

    def upsert_observed_many(
        self,
        observations: Sequence[tuple[SourceItem, Verdict, RowState]],
        *,
        run_id: int,
        existing: Mapping[str, ItemRow] | None = None,
    ) -> None:
        """``upsert_observed`` for a whole pass, batched: one ``executemany`` per statement shape.

        ``existing`` maps stable_id -> the row as read before this call (ids of one source); ids absent from
        it are looked up in one batched query.  Same result as calling ``upsert_observed`` in order: an id
        observed twice is applied record by record.
        """
        if not observations:
            return
        by_source: dict[str, set[str]] = {}
        for item, _v, _s in observations:
            by_source.setdefault(item.source_id, set()).add(item.stable_id)
        rows: dict[tuple[str, str], ItemRow] = {}
        for sid, ids in by_source.items():
            known = {} if existing is None else existing
            missing = sorted(i for i in ids if i not in known)
            for stable_id, row in self.get_items(sid, missing).items():
                rows[(sid, stable_id)] = row
            for stable_id in ids:
                if stable_id in known:
                    rows[(sid, stable_id)] = known[stable_id]
        with self._atomic():
            batches: dict[str, list[tuple[object, ...]]] = {}
            done: set[tuple[str, str]] = set()
            for item, verdict, state in observations:
                key = (item.source_id, item.stable_id)
                if key in done:  # observed twice: apply what is batched so far, then re-read the row
                    self._flush(batches)
                    fresh = self.get_item(*key)
                    self._apply_upsert(self._upsert_plan(item, run_id, verdict, state, fresh))
                    continue
                done.add(key)
                for sql, params in self._upsert_plan(item, run_id, verdict, state, rows.get(key)):
                    batches.setdefault(sql, []).append(params)
            self._flush(batches)

    def _flush(self, batches: dict[str, list[tuple[object, ...]]]) -> None:
        # Statement shapes are independent (one row each), but the reappearance reset must precede the
        # UPDATE of the same row: sort so the hash-reset statement runs first.
        for sql in sorted(batches, key=lambda q: (not q.startswith(_RESET_HASHES_SQL), q)):
            self._db.executemany(sql, batches[sql])
        batches.clear()

    def _apply_upsert(self, plan: Sequence[tuple[str, tuple[object, ...]]]) -> None:
        for sql, params in plan:
            self._db.execute(sql, params)

    @staticmethod
    def _upsert_plan(
        item: SourceItem, run_id: int, verdict: Verdict, state: RowState, existing: ItemRow | None
    ) -> list[tuple[str, tuple[object, ...]]]:
        """The statements ``upsert_observed`` runs for one observation (pure: no I/O)."""
        verdict = Verdict(verdict)
        state = RowState(state)
        rel_path = _nfc(item.rel_path)
        name = _nfc(item.name)
        extra_json = _json_dumps(dict(item.extra))
        if existing is None:
            return [
                (
                    _INSERT_ITEM_SQL,
                    (
                        item.source_id,
                        item.stable_id,
                        item.parent_id,
                        name,
                        rel_path,
                        int(item.is_dir),
                        item.size,
                        item.mtime_ns,
                        item.ctime_ns,
                        item.created_ns,
                        item.ino,
                        item.mode,
                        item.gen_count,
                        int(item.dataless),
                        item.remote_hashes.quickxor,
                        item.remote_hashes.sha1,
                        item.remote_hashes.sha256,
                        item.etag,
                        item.ctag,
                        item.content_type,
                        state.value,
                        verdict.value,
                        run_id,
                        run_id,
                        extra_json,
                    ),
                )
            ]
        reason_sql = "state_reason" if existing.state is state else "NULL"
        if item.deleted:
            # A provider tombstone carries no trustworthy path or stat, but it does carry WHY the item left
            # the scope (extra "removed" / "removed_reason": deleted, moved:<folder>, moved-out-of-scope,
            # excluded).  Keep that on the row: the cycle's removal step tells a move (tombstone "moved", no
            # purge) from a real upstream deletion (review correctness-removal-reason-dropped).
            why = {k: item.extra[k] for k in _REMOVAL_KEYS if k in item.extra}
            if why:
                merged = _json_dumps({**dict(existing.extra), **why})
                return [
                    (
                        f"UPDATE items SET state = ?, state_reason = {reason_sql}, last_verdict = ?, "
                        "last_seen_run = ?, extra_json = ? WHERE source_id = ? AND stable_id = ?",
                        (state.value, verdict.value, run_id, merged, item.source_id, item.stable_id),
                    )
                ]
            return [
                (
                    f"UPDATE items SET state = ?, state_reason = {reason_sql}, last_verdict = ?, "
                    "last_seen_run = ? WHERE source_id = ? AND stable_id = ?",
                    (state.value, verdict.value, run_id, item.source_id, item.stable_id),
                )
            ]
        plan: list[tuple[str, tuple[object, ...]]] = []
        prev_path = existing.rel_path if rel_path != existing.rel_path else existing.prev_path
        keep_h0 = verdict is Verdict.DATALESS
        h0: tuple[int | None, ...] = (
            (
                existing.size,
                existing.mtime_ns,
                existing.ctime_ns,
                existing.ino,
                existing.mode,
                existing.gen_count,
            )
            if keep_h0
            else (item.size, item.mtime_ns, item.ctime_ns, item.ino, item.mode, item.gen_count)
        )
        created_ns = existing.created_ns if keep_h0 and item.created_ns is None else item.created_ns
        if existing.state is RowState.TOMBSTONE and state is not RowState.TOMBSTONE:
            plan.append((_RESET_HASHES_SQL, (item.source_id, item.stable_id)))
        plan.append(
            (
                _UPDATE_ITEM_SQL.format(reason=reason_sql),
                (
                    item.parent_id,
                    name,
                    rel_path,
                    prev_path,
                    int(item.is_dir),
                    *h0,
                    created_ns,
                    int(item.dataless),
                    item.remote_hashes.quickxor,
                    item.remote_hashes.sha1,
                    item.remote_hashes.sha256,
                    item.etag,
                    item.ctag,
                    item.content_type,
                    state.value,
                    verdict.value,
                    run_id,
                    extra_json,
                    item.source_id,
                    item.stable_id,
                ),
            )
        )
        return plan

    def touch_observed(
        self,
        source_id: str,
        stable_ids: Iterable[str],
        *,
        run_id: int,
        verdict_changed: Iterable[str] | None = None,
    ) -> int:
        """H0 fast path: stamp rows seen unchanged this pass (last_seen_run, last_verdict=unchanged).

        For observations the classifier proved identical to their row (``ClassifyContext.h0_unchanged``):
        ``upsert_observed`` would rewrite every column with the value it already holds, so only the two
        per-pass columns change.  One statement for the whole pass; returns the number of rows stamped.
        ``verdict_changed`` (optional) names the subset whose stored last_verdict is not already UNCHANGED:
        only those get the verdict written (last_verdict is indexed, so skipping it for the rest saves index
        writes); None writes it for every row.
        """
        ids = sorted(set(stable_ids))
        if not ids:
            return 0
        if verdict_changed is None:
            cur = self._db.execute(
                "UPDATE items SET last_seen_run = ?, last_verdict = ? WHERE source_id = ? "
                "AND stable_id IN (SELECT value FROM json_each(?))",
                (run_id, Verdict.UNCHANGED.value, source_id, _json_dumps(ids)),
            )
            return int(cur.rowcount)
        with self._atomic():
            cur = self._db.execute(
                "UPDATE items SET last_seen_run = ? WHERE source_id = ? "
                "AND stable_id IN (SELECT value FROM json_each(?))",
                (run_id, source_id, _json_dumps(ids)),
            )
            restamp = sorted(set(verdict_changed) & set(ids))
            if restamp:
                self._db.execute(
                    "UPDATE items SET last_verdict = ? WHERE source_id = ? "
                    "AND stable_id IN (SELECT value FROM json_each(?))",
                    (Verdict.UNCHANGED.value, source_id, _json_dumps(restamp)),
                )
        return int(cur.rowcount)

    def _update_item(self, sql: str, params: Sequence[object], source_id: str, stable_id: str) -> None:
        cur = self._db.execute(sql, (*params, source_id, stable_id))
        if cur.rowcount != 1:
            raise KeyError(f"no item {source_id}/{stable_id}")

    def set_content(
        self,
        source_id: str,
        stable_id: str,
        *,
        content_sha256: str,
        canonical_sha256: str,
        canonical_method: str,
        canonical_parts: Sequence[tuple[str, str]],
    ) -> None:
        """Record H1 inputs after a successful fetch."""
        _check_sha256(content_sha256, "content_sha256")
        _check_sha256(canonical_sha256, "canonical_sha256")
        parts = sorted((str(p), str(h)) for p, h in canonical_parts)
        self._update_item(
            "UPDATE items SET content_sha256 = ?, canonical_sha256 = ?, canonical_method = ?, "
            "canonical_parts = ? WHERE source_id = ? AND stable_id = ?",
            (content_sha256, canonical_sha256, canonical_method, _json_dumps([list(p) for p in parts])),
            source_id,
            stable_id,
        )

    def set_verdict(self, source_id: str, stable_id: str, verdict: Verdict) -> None:
        """Update last_verdict only (phase 2/3 outcomes, DEFERRED, ERROR)."""
        self._update_item(
            "UPDATE items SET last_verdict = ? WHERE source_id = ? AND stable_id = ?",
            (Verdict(verdict).value,),
            source_id,
            stable_id,
        )

    def set_state(self, source_id: str, stable_id: str, state: RowState, reason: str | None) -> None:
        """Set a row's state (quarantine, refuse, tombstone, restore to live)."""
        self._update_item(
            "UPDATE items SET state = ?, state_reason = ? WHERE source_id = ? AND stable_id = ?",
            (RowState(state).value, reason),
            source_id,
            stable_id,
        )

    def pending_work(self, source_id: str) -> list[ItemRow]:
        """Rows whose last_verdict is CREATED/MAYBE_CHANGED/CHANGED/DEFERRED/ERROR (not yet published).

        This is what lets a delta cursor advance safely when a budget deferred work: the work is durable here.
        Also returned: DATALESS rows that were never materialised (``content_sha256`` NULL), the contract's
        "DATALESS with no content yet -> fetch within budget".  Tombstones and directories never are.
        """
        verdicts = [v.value for v in _PENDING_VERDICTS]
        rows = self._db.execute(
            f"SELECT {_ITEM_COLUMNS} FROM items WHERE source_id = ? AND is_dir = 0 AND state != 'tombstone' "
            f"AND (last_verdict IN ({', '.join('?' for _ in verdicts)}) "
            "OR (last_verdict = 'dataless' AND content_sha256 IS NULL)) ORDER BY stable_id",
            (source_id, *verdicts),
        ).fetchall()
        return [_item_from_row(r) for r in rows]

    def unseen_live(self, source_id: str, run_id: int) -> list[ItemRow]:
        """Live/dataless file rows whose last_seen_run < run_id, ordered by stable_id (deletion
        candidates).

        Quarantined and refused rows are included too: they exist upstream exactly like live rows (their
        page is a stub), so their absence from a complete listing is the same evidence of deletion.
        """
        states = [s.value for s in _PRESENT_STATES]
        rows = self._db.execute(
            f"SELECT {_ITEM_COLUMNS} FROM items WHERE source_id = ? AND is_dir = 0 AND last_seen_run < ? "
            f"AND state IN ({', '.join('?' for _ in states)}) ORDER BY stable_id",
            (source_id, run_id, *states),
        ).fetchall()
        return [_item_from_row(r) for r in rows]

    def rekey(self, source_id: str, old_id: str, new_id: str) -> None:
        """Safe-save continuity: move the old row's content hashes and outputs to the new stable id, drop
        old.

        Works whether or not the new id's row was already upserted.  The old row's first_seen_run, principal,
        sensitivity label and a quarantined/refused state (with its reason) carry over; the observation fields
        of the new row win.  Tombstone rows follow the id too.
        """
        if old_id == new_id:
            return
        with self._atomic():
            old = self.get_item(source_id, old_id)
            if old is None:
                raise KeyError(f"no item {source_id}/{old_id} to rekey")
            self._record_alias(source_id, old_id, new_id)
            self._db.execute(
                "UPDATE OR IGNORE redacted_items SET stable_id = ? WHERE source_id = ? AND stable_id = ?",
                (new_id, source_id, old_id),
            )
            new = self.get_item(source_id, new_id)
            if new is None:
                self._db.execute(
                    "UPDATE items SET stable_id = ? WHERE source_id = ? AND stable_id = ?",
                    (new_id, source_id, old_id),
                )
            else:
                carried_state = (
                    old.state if old.state in (RowState.QUARANTINED, RowState.REFUSED) else new.state
                )
                carried_reason = old.state_reason if carried_state is old.state else new.state_reason
                prev_path = old.rel_path if old.rel_path != new.rel_path else new.prev_path
                self._db.execute(
                    "UPDATE items SET content_sha256 = ?, canonical_sha256 = ?, canonical_method = ?, "
                    "canonical_parts = ?, first_seen_run = ?, principal = COALESCE(principal, ?), "
                    "sensitivity_label = COALESCE(sensitivity_label, ?), state = ?, state_reason = ?, "
                    "prev_path = ? WHERE source_id = ? AND stable_id = ?",
                    (
                        old.content_sha256,
                        old.canonical_sha256,
                        old.canonical_method,
                        _json_dumps([list(p) for p in old.canonical_parts]),
                        min(old.first_seen_run, new.first_seen_run),
                        old.principal,
                        old.sensitivity_label,
                        carried_state.value,
                        carried_reason,
                        prev_path,
                        source_id,
                        new_id,
                    ),
                )
                stray = self._db.execute(
                    "DELETE FROM outputs WHERE source_id = ? AND stable_id = ?", (source_id, new_id)
                ).rowcount
                if stray:
                    _log.warning("rekey %s: dropped %d output rows of the new id", source_id, stray)
                self._db.execute(
                    "DELETE FROM items WHERE source_id = ? AND stable_id = ?", (source_id, old_id)
                )
            self._db.execute(
                "UPDATE outputs SET stable_id = ? WHERE source_id = ? AND stable_id = ?",
                (new_id, source_id, old_id),
            )
            self._db.execute(
                "UPDATE tombstones SET stable_id = ? WHERE source_id = ? AND stable_id = ?",
                (new_id, source_id, old_id),
            )
            self._db.execute(
                "UPDATE items SET parent_id = ? WHERE source_id = ? AND parent_id = ?",
                (new_id, source_id, old_id),
            )

    def _record_alias(self, source_id: str, old_id: str, new_id: str) -> None:
        """Retire ``old_id`` into ``item_aliases`` (the chain follows the item; the first id stays origin)."""
        origin = self._db.execute(
            "SELECT 1 FROM item_aliases WHERE source_id = ? AND stable_id = ? AND origin = 1",
            (source_id, old_id),
        ).fetchone()
        self._db.execute(
            "UPDATE item_aliases SET stable_id = ? WHERE source_id = ? AND stable_id = ?",
            (new_id, source_id, old_id),
        )
        self._db.execute(
            "INSERT OR REPLACE INTO item_aliases (source_id, alias_id, stable_id, origin) "
            "VALUES (?, ?, ?, ?)",
            (source_id, old_id, new_id, 0 if origin is not None else 1),
        )

    def durable_id(self, source_id: str, stable_id: str) -> str:
        """The item's durable key: its first stable id (kept across safe-save rekeys), else ``stable_id``.

        Pages (frontmatter ``stable_id``) and shards name items by this key, so a rekey moves no committed
        byte; ``aliases_of`` / ``resolve_alias`` map between it and the current id."""
        r = self._db.execute(
            "SELECT alias_id FROM item_aliases WHERE source_id = ? AND stable_id = ? AND origin = 1",
            (source_id, stable_id),
        ).fetchone()
        return str(r[0]) if r is not None else stable_id

    def aliases_of(self, source_id: str, stable_id: str) -> list[str]:
        """Every retired id of the item whose current id is ``stable_id`` (sorted; empty = never rekeyed)."""
        return sorted(
            str(r[0])
            for r in self._db.execute(
                "SELECT alias_id FROM item_aliases WHERE source_id = ? AND stable_id = ?",
                (source_id, stable_id),
            ).fetchall()
        )

    def resolve_alias(self, source_id: str, any_id: str) -> str | None:
        """The current id of the item that once carried ``any_id`` (None when it is no retired id)."""
        r = self._db.execute(
            "SELECT stable_id FROM item_aliases WHERE source_id = ? AND alias_id = ?", (source_id, any_id)
        ).fetchone()
        return str(r[0]) if r is not None else None

    def mark_for_rescreen(self, suffixes: Sequence[str]) -> int:
        """Queue every published or policy-refused file named with one of ``suffixes`` for a re-screen.

        A ``[policy]`` change must re-decide label-capable files already in the mirror: their content
        hashes are forgotten (phase 2 then says CHANGED, so the converter runs again under the new policy
        and the label screen decides) and their verdict becomes MAYBE_CHANGED.  Returns the row count."""
        like = " OR ".join("lower(name) LIKE ?" for _ in suffixes) or "0"
        cur = self._db.execute(
            "UPDATE items SET content_sha256 = NULL, canonical_sha256 = NULL, canonical_method = NULL, "
            "canonical_parts = NULL, last_verdict = ? WHERE is_dir = 0 "
            "AND (state IN ('live', 'dataless') OR (state = 'refused' AND state_reason LIKE 'refused: %')) "
            f"AND ({like})",
            (Verdict.MAYBE_CHANGED.value, *(f"%{s.lower()}" for s in suffixes)),
        )
        return int(cur.rowcount)

    def pending_named(self, suffixes: Sequence[str]) -> int:
        """How many rows named with one of ``suffixes`` are pending work (the re-screen backlog)."""
        like = " OR ".join("lower(name) LIKE ?" for _ in suffixes) or "0"
        pending = [v.value for v in _PENDING_VERDICTS]
        r = self._db.execute(
            f"SELECT COUNT(*) FROM items WHERE is_dir = 0 AND state != 'tombstone' AND ({like}) "
            f"AND last_verdict IN ({', '.join('?' for _ in pending)})",
            (*(f"%{s.lower()}" for s in suffixes), *pending),
        ).fetchone()
        return int(r[0])

    def set_redacted(self, source_id: str, stable_id: str) -> None:
        """Record that the item's name/path carries a credential: pages, shards and QUARANTINE.tsv show a
        redacted, hashed form of its path from now on (``redacted_path``)."""
        self._db.execute(
            "INSERT OR IGNORE INTO redacted_items (source_id, stable_id) VALUES (?, ?)",
            (source_id, stable_id),
        )

    def is_redacted(self, source_id: str, stable_id: str) -> bool:
        """True when the item's path must never be written in clear (see ``set_redacted``)."""
        r = self._db.execute(
            "SELECT 1 FROM redacted_items WHERE source_id = ? AND stable_id = ?", (source_id, stable_id)
        ).fetchone()
        return r is not None

    def redacted_ids(self, source_id: str) -> set[str]:
        """Stable ids of one source whose paths are redacted."""
        return {
            str(r[0])
            for r in self._db.execute(
                "SELECT stable_id FROM redacted_items WHERE source_id = ?", (source_id,)
            ).fetchall()
        }

    def record_run_changes(
        self, run_id: int, changes: Sequence[MirrorChange], *, replace: bool = False
    ) -> None:
        """Append (or with ``replace``, rewrite) the mirror changes of ``run_id``, durably and in the
        caller's transaction: a crash before the commit leaves them for the next cycle to describe (its
        subject, body and CHANGELOG), instead of a bare ``sync: 0a 0m 0r 0d`` (design 4.7)."""
        with self._atomic():
            if replace:
                self._db.execute("DELETE FROM run_changes WHERE run_id = ?", (run_id,))
            r = self._db.execute(
                "SELECT COALESCE(MAX(seq), -1) FROM run_changes WHERE run_id = ?", (run_id,)
            ).fetchone()
            start = int(r[0]) + 1
            self._db.executemany(
                "INSERT INTO run_changes (run_id, seq, op, path, source_id, stable_id, prev_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (run_id, start + n, c.op.value, c.path, c.source_id, c.stable_id, c.prev_path)
                    for n, c in enumerate(changes)
                ],
            )

    def run_changes(self, run_id: int) -> list[MirrorChange]:
        """The mirror changes recorded for ``run_id``, in production order."""
        return [
            MirrorChange(ChangeOp(r[0]), str(r[1]), str(r[2]), str(r[3]), r[4])
            for r in self._db.execute(
                "SELECT op, path, source_id, stable_id, prev_path FROM run_changes WHERE run_id = ? "
                "ORDER BY seq",
                (run_id,),
            ).fetchall()
        ]

    def clear_run_changes(self, up_to_run: int) -> None:
        """Forget the recorded changes of ``up_to_run`` and every earlier run (their commit landed)."""
        self._db.execute("DELETE FROM run_changes WHERE run_id <= ?", (up_to_run,))

    def mark_absent(self, source_id: str, stable_ids: Iterable[str], run_id: int) -> None:
        """Record the first complete pass that did not list these rows (``extra.absent_since_run``).

        The next observation of a row rewrites ``extra_json`` and so clears the mark; a row still absent in
        a later complete pass is a real deletion candidate (local/inbox two-pass rule, see the cycle)."""
        ids = sorted(set(stable_ids))
        for start in range(0, len(ids), _ID_CHUNK):
            chunk = ids[start : start + _ID_CHUNK]
            self._db.execute(
                "UPDATE items SET extra_json = json_set(extra_json, '$.absent_since_run', ?) "
                f"WHERE source_id = ? AND stable_id IN ({', '.join('?' for _ in chunk)}) "
                "AND json_extract(extra_json, '$.absent_since_run') IS NULL",
                (run_id, source_id, *chunk),
            )

    def present_counts(self) -> dict[str, int]:
        """source_id -> number of non-directory rows that exist upstream (every source the manifest knows)."""
        states = [st.value for st in _PRESENT_STATES]
        return {
            str(r[0]): int(r[1])
            for r in self._db.execute(
                "SELECT source_id, COUNT(*) FROM items WHERE is_dir = 0 "
                f"AND state IN ({', '.join('?' for _ in states)}) GROUP BY source_id ORDER BY source_id",
                states,
            ).fetchall()
        }

    def clear_absent_marks(self, source_id: str, run_id: int) -> int:
        """Drop ``extra.absent_since_run`` from rows observed in ``run_id`` (the H0 fast path does not
        rewrite ``extra_json``, so a file that came back unchanged would otherwise keep its old mark)."""
        probe = self._db.execute(
            "SELECT 1 FROM items WHERE source_id = ? AND extra_json LIKE '%absent_since_run%' LIMIT 1",
            (source_id,),
        ).fetchone()
        if probe is None:
            return 0
        cur = self._db.execute(
            "UPDATE items SET extra_json = json_remove(extra_json, '$.absent_since_run') "
            "WHERE source_id = ? AND last_seen_run = ? "
            "AND json_extract(extra_json, '$.absent_since_run') IS NOT NULL",
            (source_id, run_id),
        )
        return int(cur.rowcount)

    def descendants(self, source_id: str, dir_id: str) -> list[ItemRow]:
        """Every row below the directory ``dir_id`` (by parent_id, breadth-first; tombstones included)."""
        out: list[ItemRow] = []
        queue = [dir_id]
        seen = {dir_id}
        while queue:
            parent = queue.pop(0)
            rows = self._db.execute(
                f"SELECT {_ITEM_COLUMNS} FROM items WHERE source_id = ? AND parent_id = ? ORDER BY stable_id",
                (source_id, parent),
            ).fetchall()
            for r in rows:
                row = _item_from_row(r)
                if row.stable_id in seen:
                    continue
                seen.add(row.stable_id)
                out.append(row)
                if row.is_dir:
                    queue.append(row.stable_id)
        return out

    def forget_item(self, source_id: str, stable_id: str) -> None:
        """Delete every manifest trace of one item (row, outputs, tombstones, aliases); pages are the
        caller's."""
        with self._atomic():
            self._db.execute(
                "DELETE FROM redacted_items WHERE source_id = ? AND stable_id = ?", (source_id, stable_id)
            )
            for table in ("outputs", "tombstones"):
                self._db.execute(
                    f"DELETE FROM {table} WHERE source_id = ? AND stable_id = ?", (source_id, stable_id)
                )
            self._db.execute(
                "DELETE FROM item_aliases WHERE source_id = ? AND stable_id = ?", (source_id, stable_id)
            )
            self._db.execute(
                "DELETE FROM items WHERE source_id = ? AND stable_id = ?", (source_id, stable_id)
            )
            self._fold_paths = None

    def rederive_paths(self, source_id: str, root_id: str | None) -> list[tuple[str, str, str]]:
        """Recompute rel_path from the id->(parent, name) tree; return (stable_id, old, new) that changed.

        ``root_id`` is the scope root (drive folder id) or None for the drive root.  O(changed subtree).

        Rules: the scope root itself (``root_id``, or with ``root_id`` None a directory row with no parent and
        rel_path "") is "" and keeps its row; a row whose parent is the scope root or None sits at the top
        level (path = name).  Rows whose chain reaches an unknown id, or a cycle, are left untouched (logged).
        One read of the source's (id, parent, name, path) columns, writes only for rows whose path moved; a
        changed row gets prev_path = its old path.  Tombstone rows are used for the tree but never rewritten.
        """
        rows = self._db.execute(
            "SELECT stable_id, parent_id, name, rel_path, is_dir, state FROM items WHERE source_id = ?",
            (source_id,),
        ).fetchall()
        node: dict[str, tuple[str | None, str, str, bool, str]] = {
            str(r[0]): (r[1], _nfc(str(r[2])), str(r[3]), bool(r[4]), str(r[5])) for r in rows
        }

        def is_root(sid: str) -> bool:
            if root_id is not None:
                return sid == root_id
            parent, _name, rel, is_dir, _state = node[sid]
            return parent is None and is_dir and rel == ""

        memo: dict[str, str | None] = {}

        def derive(sid: str) -> str | None:
            chain: list[str] = []
            cur = sid
            base: str | None
            while True:
                if cur in memo:
                    base = memo[cur]
                    break
                if is_root(cur):
                    memo[cur] = ""
                    base = ""
                    break
                chain.append(cur)
                parent = node[cur][0]
                if parent is None:
                    base = "" if root_id is None else None  # outside a sub-folder scope: not ours to derive
                    break
                if root_id is not None and parent == root_id:
                    base = ""
                    break
                if parent not in node:
                    base = None
                    break
                if parent in chain:
                    _log.warning(
                        "rederive_paths %s: parent cycle at %s; paths left untouched", source_id, parent
                    )
                    base = None
                    break
                cur = parent
            for member in reversed(chain):
                if base is not None:
                    name = node[member][1]
                    base = name if base == "" else f"{base}/{name}"
                memo[member] = base
            return memo[sid]

        changes: list[tuple[str, str, str]] = []
        for sid in sorted(node):
            if is_root(sid):
                continue
            new_path = derive(sid)
            _parent, _name, old_path, _is_dir, state = node[sid]
            if new_path is None:
                continue
            if new_path != old_path and state != RowState.TOMBSTONE.value:
                changes.append((sid, old_path, new_path))
        if changes:
            with self._atomic():
                for sid, old_path, new_path in changes:
                    self._db.execute(
                        "UPDATE items SET rel_path = ?, prev_path = ? WHERE source_id = ? AND stable_id = ?",
                        (new_path, old_path, source_id, sid),
                    )
        return changes

    def find_by_canonical(self, canonical_sha256: str) -> list[ItemRow]:
        """Rows (any source, live) with this canonical hash — inbox duplicate / alias-of detection."""
        rows = self._db.execute(
            f"SELECT {_ITEM_COLUMNS} FROM items WHERE canonical_sha256 = ? AND is_dir = 0 "
            "AND state IN ('live', 'dataless') ORDER BY source_id, stable_id",
            (canonical_sha256,),
        ).fetchall()
        return [_item_from_row(r) for r in rows]

    # ---- outputs ----------------------------------------------------------------------------------------
    def outputs_for(self, source_id: str, stable_id: str) -> list[OutputRow]:
        """Output rows of one item ordered by unit_id."""
        rows = self._db.execute(
            f"SELECT {_OUTPUT_COLUMNS} FROM outputs WHERE source_id = ? AND stable_id = ? ORDER BY unit_id",
            (source_id, stable_id),
        ).fetchall()
        return [_output_from_row(r) for r in rows]

    def output_by_path(self, output_path: str) -> OutputRow | None:
        """Return the output row owning ``output_path`` (case-insensitive + NFC match) or None."""
        exact = self._db.execute(
            f"SELECT {_OUTPUT_COLUMNS} FROM outputs WHERE output_path = ?", (output_path,)
        ).fetchone()
        if exact is not None:
            return _output_from_row(exact)
        for path in sorted(self._fold_index().get(_fold(output_path) or "", ())):
            r = self._db.execute(
                f"SELECT {_OUTPUT_COLUMNS} FROM outputs WHERE output_path = ?", (path,)
            ).fetchone()
            if r is not None:
                return _output_from_row(r)
        return None

    def _fold_index(self) -> dict[str, set[str]]:
        if self._fold_paths is None:
            index: dict[str, set[str]] = {}
            for (path,) in self._db.execute("SELECT output_path FROM outputs").fetchall():
                index.setdefault(_fold(str(path)) or "", set()).add(str(path))
            self._fold_paths = index
        return self._fold_paths

    def replace_outputs(self, source_id: str, stable_id: str, rows: Sequence[OutputRow]) -> list[OutputRow]:
        """Replace an item's output rows; return the previous rows whose unit_id is no longer present."""
        units: set[str] = set()
        for row in rows:
            if row.source_id != source_id or row.stable_id != stable_id:
                raise ValueError(f"output {row.output_path} belongs to {row.source_id}/{row.stable_id}")
            if row.unit_id in units:
                raise ValueError(f"duplicate unit_id {row.unit_id!r} for {source_id}/{stable_id}")
            units.add(row.unit_id)
        with self._atomic():
            previous = self.outputs_for(source_id, stable_id)
            self._db.execute(
                "DELETE FROM outputs WHERE source_id = ? AND stable_id = ?", (source_id, stable_id)
            )
            for row in sorted(rows, key=lambda o: o.unit_id):
                self._db.execute(
                    f"INSERT INTO outputs ({_OUTPUT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row.output_path,
                        row.source_id,
                        row.stable_id,
                        row.unit_id,
                        row.action_key,
                        row.rendered_sha256,
                        row.page_sha256,
                        row.converter_id,
                        row.converter_version,
                        row.options_hash,
                        OutputStatus(row.status).value,
                        row.built_run,
                    ),
                )
        if self._fold_paths is not None:
            for p in previous:
                paths = self._fold_paths.get(_fold(p.output_path) or "")
                if paths is not None:
                    paths.discard(p.output_path)
            for row in rows:
                self._fold_paths.setdefault(_fold(row.output_path) or "", set()).add(row.output_path)
        return [p for p in previous if p.unit_id not in units]

    def live_action_keys(self) -> set[str]:
        """Every action_key referenced by a non-tombstone output (converter-cache GC roots)."""
        rows = self._db.execute(
            "SELECT DISTINCT action_key FROM outputs WHERE action_key IS NOT NULL AND status != 'tombstone'"
        ).fetchall()
        return {str(r[0]) for r in rows}

    def iter_outputs(self, *, source_id: str | None = None) -> Iterator[OutputRow]:
        """Yield output rows ordered by output_path."""
        if source_id is None:
            rows = self._db.execute(f"SELECT {_OUTPUT_COLUMNS} FROM outputs ORDER BY output_path").fetchall()
        else:
            rows = self._db.execute(
                f"SELECT {_OUTPUT_COLUMNS} FROM outputs WHERE source_id = ? ORDER BY output_path",
                (source_id,),
            ).fetchall()
        for r in rows:
            yield _output_from_row(r)

    # ---- converter cache index --------------------------------------------------------------------------
    def record_cache(
        self,
        action_key: str,
        *,
        converter_id: str,
        converter_version: str,
        options_hash: str,
        canonical_sha256: str,
        status: str,
        unit_count: int,
        size: int,
        run_id: int,
    ) -> None:
        """Insert or touch (last_used_run) a cache index row."""
        self._db.execute(
            "INSERT INTO cache (action_key, converter_id, converter_version, options_hash, canonical_sha256, "
            "status, unit_count, bytes, created_run, last_used_run) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(action_key) DO UPDATE SET "
            "last_used_run = MAX(last_used_run, excluded.last_used_run)",
            (
                action_key,
                converter_id,
                converter_version,
                options_hash,
                canonical_sha256,
                status,
                unit_count,
                size,
                run_id,
                run_id,
            ),
        )

    # ---- tombstones -------------------------------------------------------------------------------------
    def add_tombstone(self, row: TombstoneRow) -> None:
        """Insert or replace a tombstone row."""
        _check_date(row.deleted_at, "deleted_at")
        _check_date(row.reap_after, "reap_after")
        self._db.execute(
            f"INSERT OR REPLACE INTO tombstones ({_TOMBSTONE_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.output_path,
                row.source_id,
                row.stable_id,
                row.unit_id,
                row.deleted_at,
                row.deleted_run,
                row.last_rendered_sha256,
                row.last_commit,
                row.reap_after,
                row.reason,
            ),
        )

    def get_tombstone(self, output_path: str) -> TombstoneRow | None:
        """Return the tombstone at ``output_path`` or None."""
        r = self._db.execute(
            f"SELECT {_TOMBSTONE_COLUMNS} FROM tombstones WHERE output_path = ?", (output_path,)
        ).fetchone()
        return None if r is None else _tombstone_from_row(r)

    def remove_tombstone(self, output_path: str) -> None:
        """Delete a tombstone row (restore on reappearance, or after reaping)."""
        self._db.execute("DELETE FROM tombstones WHERE output_path = ?", (output_path,))

    def tombstones_due(self, today: str) -> list[TombstoneRow]:
        """Tombstones whose reap_after <= today (UTC date), ordered by output_path."""
        _check_date(today, "today")
        rows = self._db.execute(
            f"SELECT {_TOMBSTONE_COLUMNS} FROM tombstones WHERE reap_after <= ? ORDER BY output_path",
            (today,),
        ).fetchall()
        return [_tombstone_from_row(r) for r in rows]

    def iter_tombstones(self) -> Iterator[TombstoneRow]:
        """Yield all tombstones ordered by output_path."""
        rows = self._db.execute(
            f"SELECT {_TOMBSTONE_COLUMNS} FROM tombstones ORDER BY output_path"
        ).fetchall()
        for r in rows:
            yield _tombstone_from_row(r)

    # ---- cursors ----------------------------------------------------------------------------------------
    def get_cursor(self, source_id: str) -> CursorRow | None:
        """Return the cursor row or None."""
        r = self._db.execute(
            "SELECT source_id, current, current_set_at, pending, pending_run_id, page_link FROM cursors "
            "WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        if r is None:
            return None
        return CursorRow(
            source_id=r["source_id"],
            current=r["current"],
            current_set_at=r["current_set_at"],
            pending=r["pending"],
            pending_run_id=r["pending_run_id"],
            page_link=r["page_link"],
        )

    def stage_cursor(self, source_id: str, pending: str | None, run_id: int) -> None:
        """Store a pending cursor (same transaction as the item snapshot).  ``current`` is untouched.

        ``pending`` None (an arm without cursors, e.g. the local walk) clears any pending value instead, so
        promotion can never overwrite ``current`` with nothing.
        """
        pending_run = None if pending is None else run_id
        self._db.execute(
            "INSERT INTO cursors (source_id, pending, pending_run_id) VALUES (?, ?, ?) "
            "ON CONFLICT(source_id) DO UPDATE SET pending = excluded.pending, "
            "pending_run_id = excluded.pending_run_id",
            (source_id, pending, pending_run),
        )

    def promote_cursors(self, run_id: int, now_iso: str) -> list[str]:
        """After the git commit: pending -> current for every cursor staged by ``run_id``; return source
        ids."""
        _parse_iso(now_iso)
        with self._atomic():
            rows = self._db.execute(
                "SELECT source_id FROM cursors WHERE pending_run_id = ? AND pending IS NOT NULL "
                "ORDER BY source_id",
                (run_id,),
            ).fetchall()
            ids = [str(r[0]) for r in rows]
            self._db.execute(
                "UPDATE cursors SET current = pending, current_set_at = ?, pending = NULL, "
                "pending_run_id = NULL WHERE pending_run_id = ? AND pending IS NOT NULL",
                (now_iso, run_id),
            )
        return ids

    def discard_pending(self, run_id: int) -> None:
        """Forget pending cursors of a failed run (the next cycle re-reads from ``current``)."""
        self._db.execute(
            "UPDATE cursors SET pending = NULL, pending_run_id = NULL WHERE pending_run_id = ?", (run_id,)
        )

    def drop_cursor(self, source_id: str) -> None:
        """Delete current+pending+page_link (400 bad cursor, scope change, retirement)."""
        self._db.execute("DELETE FROM cursors WHERE source_id = ?", (source_id,))

    def set_page_link(self, source_id: str, link: str | None) -> None:
        """Persist the in-progress enumeration's nextLink so a crash resumes instead of restarting."""
        self._db.execute(
            "INSERT INTO cursors (source_id, page_link) VALUES (?, ?) "
            "ON CONFLICT(source_id) DO UPDATE SET page_link = excluded.page_link",
            (source_id, link),
        )

    # ---- depends ----------------------------------------------------------------------------------------
    def replace_depends(self, rows: Sequence[DependsRow]) -> None:
        """Replace the whole depends table (it is regenerated from topics/ frontmatter every cycle).

        A duplicate (page, source) keeps the first row in (page, source, pinned_sha, role) order (logged).
        """
        unique: dict[tuple[str, str], DependsRow] = {}
        for row in sorted(rows, key=lambda d: (d.page, d.source, d.pinned_sha, d.role)):
            key = (row.page, row.source)
            if key in unique:
                _log.warning("depends: duplicate row for page %s source %s; keeping the first", *key)
                continue
            unique[key] = row
        with self._atomic():
            self._db.execute("DELETE FROM depends")
            self._db.executemany(
                "INSERT INTO depends (page, source, pinned_sha, role) VALUES (?, ?, ?, ?)",
                [(d.page, d.source, d.pinned_sha, d.role) for d in unique.values()],
            )

    def pages_citing(self, source_path: str) -> list[str]:
        """Curated pages (docs-repo-relative) that cite ``source_path``, sorted."""
        rows = self._db.execute(
            "SELECT DISTINCT page FROM depends WHERE source = ? ORDER BY page", (source_path,)
        ).fetchall()
        return [str(r[0]) for r in rows]

    # ---- export / recovery ------------------------------------------------------------------------------
    def export_shard(self, source_id: str) -> list[str]:
        """Return the deterministic NDJSON lines of ``_manifest/<source_id>.jsonl`` (see CONTRACTS.md).

        One JSON line (no trailing newline) per non-directory item in state live/dataless/quarantined/
        refused/tombstone, sorted by stable_id; the publisher joins them with LF plus a trailing LF.
        """
        states = [s.value for s in _EXPORT_STATES]
        # Each line is a fixed template of JSON-quoted fields: byte-for-byte ``json.dumps(obj, sort_keys=True,
        # ensure_ascii=False, separators=(",", ":"))`` (keys already in sorted order) without a dict per row.
        # SQLite's json_quote does the quoting (it escapes exactly as the stdlib encoder does, NULL -> null);
        # tests/test_manifest.py checks the equality against json.dumps, control characters included.
        # 'dataless' is this Mac's File Provider cache state (the OS evicts files under storage pressure), not
        # a fact about the source: exported as 'live' so an eviction commits nothing.
        cur = self._db.cursor()
        cur.row_factory = None
        # The exported stable_id is the durable key (the item's first id, kept across safe-save rekeys), so
        # a same-content safe-save or a volume-UUID change exports the same line (contract 5.1: no volatile
        # field); the page frontmatter names the same key.
        items = cur.execute(
            "SELECT i.stable_id, '],\"rel_path\":' || json_quote(i.rel_path) || ',\"source_id\":' "
            "|| json_quote(i.source_id) || ',\"stable_id\":' "
            "|| json_quote(COALESCE(a.alias_id, i.stable_id)) "
            "|| ',\"state\":' "
            "|| json_quote(CASE i.state WHEN 'dataless' THEN 'live' ELSE i.state END) "
            "|| ',\"state_reason\":' || json_quote(i.state_reason) || '}' "
            "FROM items i LEFT JOIN item_aliases a "
            "ON a.source_id = i.source_id AND a.stable_id = i.stable_id "
            "AND a.origin = 1 WHERE i.source_id = ? AND i.is_dir = 0 "
            f"AND i.state IN ({', '.join('?' for _ in states)}) "
            "ORDER BY COALESCE(a.alias_id, i.stable_id), i.stable_id",
            (source_id, *states),
        ).fetchall()
        outs: dict[str, list[str]] = {}
        for sid, obj in cur.execute(
            "SELECT stable_id, '{\"path\":' || json_quote(output_path) || ',\"rendered_sha256\":' "
            "|| json_quote(rendered_sha256) || ',\"unit_id\":' || json_quote(unit_id) || '}' "
            "FROM outputs WHERE source_id = ? ORDER BY stable_id, unit_id",
            (source_id,),
        ).fetchall():
            outs.setdefault(sid, []).append(obj)
        head = '{"outputs":['
        lines = [head + ",".join(outs.get(sid, ())) + tail for sid, tail in items]
        redacted = self.redacted_ids(source_id)
        if redacted:
            for n, (sid, _tail) in enumerate(items):
                if sid in redacted:
                    obj = json.loads(lines[n])
                    obj["rel_path"] = redacted_path(str(obj["rel_path"]))
                    lines[n] = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return lines

    def tree_sha(self) -> str | None:
        """Return meta.tree_sha: the git tree the manifest last published."""
        return self.get_meta("tree_sha")

    def set_tree_sha(self, tree_sha: str) -> None:
        """Record the git tree sha of the commit that published the current manifest state."""
        self.set_meta("tree_sha", tree_sha)
