"""Value types shared by every agentsync module (the vocabulary of docs/design/CONTRACTS.md)."""

from __future__ import annotations

import enum
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agentsync.errors import BudgetExhaustedError

TreeLookup = Callable[[str], tuple[str | None, str] | None]
"""Manifest lookup ``stable_id -> (parent_id, name)`` the Graph drive arm derives paths from (None =
unknown)."""

ExtraValue = str | int | float | bool | None
"""Allowed value type in ``SourceItem.extra`` (JSON scalars only, so it serialises deterministically)."""


class SourceKind(enum.StrEnum):
    """Which arm owns a source (design section 4.2)."""

    LOCAL = "local"  # arm B: a folder (typically a File Provider sync-client folder), T3 metadata walk
    INBOX = "inbox"  # arm C: manual drag-and-drop folder; max(created, modified), quiescence, dedup
    GRAPH_DRIVE = "graph_drive"  # arm A: OneDrive / SharePoint document library, one delta cursor per drive
    GRAPH_MAIL = "graph_mail"  # arm A: one mail folder, folder-scoped message delta
    GRAPH_TEAMS = "graph_teams"  # arm A: one Teams channel, channel message delta -> monthly rollups

    @property
    def is_graph(self) -> bool:
        """True for the three Graph-backed kinds."""
        return self in (SourceKind.GRAPH_DRIVE, SourceKind.GRAPH_MAIL, SourceKind.GRAPH_TEAMS)


class SourceState(enum.StrEnum):
    """Operator-declared lifecycle of a configured source (design section 4.2 Arm 0, 4.7 retirement)."""

    PAUSED = "paused"
    LIVE = "live"
    RETIRED = "retired"


class PassKind(enum.StrEnum):
    """The two pass kinds the classifier must never confuse (design section 4.3)."""

    DELTA = "delta"  # incremental: deletes only from explicit tombstones
    FULL = "full_enumeration"  # bootstrap / 410 resync / reconcile / T3 walk: absence is evidence if complete


class RowState(enum.StrEnum):
    """Manifest ``items.state`` (design section 4.3): a failure is a row, never a log line."""

    LIVE = "live"
    DATALESS = "dataless"
    TOMBSTONE = "tombstone"
    QUARANTINED = "quarantined"
    REFUSED = "refused"


class Verdict(enum.StrEnum):
    """Per-object classifier verdicts, exactly the states of design section 4.3.

    Phase 1 (zero bytes) yields CREATED, DATALESS, UNCHANGED, MAYBE_CHANGED, METADATA_ONLY, DELETED,
    DELETION_CANDIDATE.  Phase 2 (after ``materialise``) turns MAYBE_CHANGED/CREATED into
    TOUCHED_NOT_CHANGED or CHANGED.  Phase 3 (after conversion) turns CHANGED into OUTPUT_UNCHANGED when every
    unit's H2 equals the recorded one (ninja ``restat`` early cutoff).  A rename is not a verdict: it is
    ``Classification.prev_path`` and classification of content continues.
    """

    CREATED = "created"  # stable id never seen before in this source
    UNCHANGED = "unchanged"  # provider hash / cTag / stat tuple equal (and not racily clean)
    MAYBE_CHANGED = "maybe_changed"  # a zero-byte signal moved; needs H1 to decide
    METADATA_ONLY = "metadata_only"  # eTag moved but size/mtime/hash hold: re-map path, rewrite frontmatter
    DATALESS = "dataless"  # SF_DATALESS placeholder: never open, never hash, never read null hash as a diff
    TOUCHED_NOT_CHANGED = "touched_not_changed"  # bytes read, canonical hash (H1) equal: update H0 only
    CHANGED = "changed"  # H1 differs: convert (through the cache)
    OUTPUT_UNCHANGED = "output_unchanged"  # converted, but every unit's H2 equals the recorded one
    DELETED = "deleted"  # explicit tombstone (Graph ``deleted`` facet, confirmed mail removal)
    DELETION_CANDIDATE = "deletion_candidate"  # absent from a COMPLETE full enumeration; subject to breaker
    DEFERRED = "deferred"  # needed bytes but the per-cycle budget was exhausted; retried next cycle
    QUARANTINED = "quarantined"  # IRM/encrypted/password/pending/checked-out/secret-in-content/duplicate
    REFUSED = "refused"  # no converter for the type, or out of scope: stub page, never silent
    ERROR = "error"  # unexpected failure on this object; recorded on the row, cycle continues


class OutputStatus(enum.StrEnum):
    """Manifest ``outputs.status`` (design section 4.3 ``output`` table)."""

    OK = "ok"
    FAILED = "failed"
    SKIPPED_DATALESS = "skipped-dataless"
    QUARANTINED = "quarantined"
    REFUSED = "refused"
    TOMBSTONE = "tombstone"


class PageStatus(enum.StrEnum):
    """Mirror page frontmatter ``status`` (design section 4.4); read verbatim by the refresh queue."""

    CURRENT = "current"
    DELETED = "deleted"
    SUPERSEDED = "superseded"
    UNREADABLE = "unreadable"
    REFUSED = "refused"
    ARCHIVED = "archived"  # a docs/archive/ copy of a page whose source was deleted ([governance] archive)


class ConversionStatus(enum.StrEnum):
    """Outcome of one conversion."""

    OK = "ok"
    FAILED = "failed"  # converter raised / produced nothing: stub page, row status failed
    UNREADABLE = "unreadable"  # encrypted / password-protected / IRM bytes: UNREADABLE stub, quarantined row
    REFUSED = "refused"  # no converter registered for the type


class UnitKind(enum.StrEnum):
    """Addressable citation unit kinds (design section 4.4, 'one file per addressable citation unit')."""

    WHOLE = "whole"
    INDEX = "index"  # e.g. <Name>.xlsx.d/00-index.md, the workbook's grep-recall surface
    SHEET = "sheet"
    SUMMARY = "summary"  # streaming path for giant workbooks: schema + sample + CSV sidecar


class ChangeOp(enum.StrEnum):
    """CHANGELOG / commit-subject operation letters."""

    ADDED = "A"
    MODIFIED = "M"
    RENAMED = "R"
    DELETED = "D"


class CycleMode(enum.StrEnum):
    """How a cycle was invoked (one launchd job per mode, design section 4.7)."""

    POLL = "poll"  # delta where a cursor exists; local sources still walk (T3 is the full enumeration)
    RECONCILE = "reconcile"  # full enumeration for every source
    DRY_RUN = "dry_run"  # classify only: no materialise, no publish, no commit, cursors untouched


# ---------------------------------------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RemoteHashes:
    """Provider-side hashes as reported by Graph; each comparable ONLY to itself, never to a local sha256."""

    quickxor: str | None = None
    sha256: str | None = None  # documented "isn't supported. Don't use" for drives; carried if present
    sha1: str | None = None

    @property
    def empty(self) -> bool:
        """True when the provider supplied no hash at all."""
        return self.quickxor is None and self.sha256 is None and self.sha1 is None


@dataclass(frozen=True, slots=True)
class SourceItem:
    """One observed object from any arm, in one schema (design section 4.2 'one change-record schema').

    Identity is ``(source_id, stable_id)`` and is NEVER the path: local = ``f"{volume_uuid}:{inode}"``;
    graph drive = driveItem id; mail = message id; teams = ``f"{channel_id}:{yyyy-mm}"`` (monthly rollup).
    ``rel_path`` is a derived, mutable POSIX path relative to the source root, NFC-normalised.
    Times are integer nanoseconds since the epoch (UTC); for Graph items they come from
    ``lastModifiedDateTime``/``createdDateTime``.  ``extra`` holds arm-specific JSON scalars (sorted on
    export) and is excluded from hashing/equality of the dataclass.
    """

    source_id: str
    stable_id: str
    rel_path: str
    name: str
    size: int
    mtime_ns: int
    ctime_ns: int
    is_dir: bool = False
    dataless: bool = False
    gen_count: int | None = None  # ATTR_CMN_GEN_COUNT; None/0 = unknown => hash to confirm
    remote_hashes: RemoteHashes = field(default_factory=RemoteHashes)
    etag: str | None = None
    ctag: str | None = None
    deleted: bool = False  # explicit tombstone from the provider (never inferred from absence)
    parent_id: str | None = None  # id -> (parent, name) tree; paths are re-derived from it
    created_ns: int | None = None  # birthtime / createdDateTime; inbox arm keys on max(created, modified)
    ino: int | None = None  # H0 stat tuple (local only)
    mode: int | None = None  # H0 stat tuple (local only)
    content_type: str | None = None  # MIME type where the provider reports it
    extra: Mapping[str, ExtraValue] = field(default_factory=dict, hash=False, compare=False)

    @property
    def suffix(self) -> str:
        """Lower-case extension of ``name`` including the dot, e.g. ``.xlsx`` (``.teams.json`` kept whole)."""
        low = self.name.lower()
        if low.endswith(".teams.json"):
            return ".teams.json"
        dot = low.rfind(".")
        return low[dot:] if dot > 0 else ""


@dataclass(frozen=True, slots=True)
class ScanResult:
    """What one arm's ``scan`` returns for one source and one pass.

    ``new_cursor`` is the PENDING cursor (e.g. the final ``@odata.deltaLink``): the cycle stores it as
    pending and promotes it only after the git commit.  ``enumeration_complete`` is True only for a
    FULL pass that saw the whole scope (every page, canary present, no budget abort).
    """

    source_id: str
    pass_kind: PassKind
    items: tuple[SourceItem, ...]
    new_cursor: str | None
    enumeration_complete: bool
    cursor_reset: bool = False  # a 410 or a dropped bad cursor forced a re-enumeration this pass
    unknown_dirs: tuple[str, ...] = ()  # zero-child cloud dirs / EPERM dirs: 'unknown', never 'empty'
    alarms: tuple[str, ...] = ()  # operator-visible problems (bad cursor dropped, canary missing, ...)


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Bytes of one item, copied into the staging dir by ``SourceArm.fetch``."""

    stable_id: str
    path: Path  # staging copy; converters read ONLY this, never the live source path
    size: int
    content_sha256: str  # sha256 of exactly the bytes at ``path``


class SourceArm(Protocol):
    """The uniform interface every arm class implements; ``cycle.py`` depends only on this."""

    source_id: str
    kind: SourceKind

    def scan(self, cursor: str | None, *, full: bool) -> ScanResult:
        """Enumerate changes since ``cursor`` (or everything when ``full`` or cursor is None); zero file
        bytes."""
        ...

    def fetch(self, item: SourceItem, dest_dir: Path, budget: ByteBudget) -> FetchResult:
        """Copy one item's bytes into ``dest_dir`` charging ``budget``; raises BudgetExhaustedError first."""
        ...


class ByteBudget:
    """Mutable per-cycle, per-source byte/file budget; ``charge`` raises BudgetExhaustedError when exceeded.

    The byte side bounds downloads: callers charge a file's size only when reading it downloads it (a
    dataless, online-only local file, or any Graph item), and 0 bytes for an already-local file, which still
    counts one file against ``max_files``.

    Deliberately not a frozen dataclass: one instance lives for one source for one cycle.
    """

    __slots__ = ("files_used", "max_bytes", "max_files", "used")

    def __init__(self, max_bytes: int, max_files: int) -> None:
        self.max_bytes = max_bytes
        self.max_files = max_files
        self.used = 0
        self.files_used = 0

    def can_afford(self, size: int) -> bool:
        """True when one more file of ``size`` bytes fits in both budgets."""
        return self.used + size <= self.max_bytes and self.files_used + 1 <= self.max_files

    def charge(self, size: int) -> None:
        """Reserve one file of ``size`` bytes or raise BudgetExhaustedError without charging."""
        if not self.can_afford(size):
            raise BudgetExhaustedError(
                f"budget exhausted: {self.used}+{size} of {self.max_bytes} bytes, "
                f"{self.files_used}+1 of {self.max_files} files"
            )
        self.used += size
        self.files_used += 1

    @property
    def remaining_bytes(self) -> int:
        """Bytes still available this cycle."""
        return max(0, self.max_bytes - self.used)


# ---------------------------------------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CanonicalHash:
    """H1: canonical content hash plus the per-part digests it rolled up (so a CHANGED verdict can log
    parts)."""

    sha256: str
    method: str  # e.g. "ooxml-parts@1", "pdf-text@1", "bytes@1"
    parts: tuple[tuple[str, str], ...] = ()  # sorted (part name, sha256) for OOXML; () otherwise


@dataclass(frozen=True, slots=True)
class Classification:
    """The classifier's decision for one object."""

    source_id: str
    stable_id: str
    verdict: Verdict
    reason: str  # short machine-greppable reason, e.g. "quickxor-differs", "racily-clean", "canonical-equal"
    prev_path: str | None = None  # set when the object was renamed/moved (rename is reported, not churn)
    replaces_id: str | None = None  # safe-save: new FILEID at a path whose old id vanished in the same pass
    changed_parts: tuple[str, ...] = ()  # OOXML part names that differed (logged on every CHANGED)


# ---------------------------------------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RenderedUnit:
    """One addressable output unit of one conversion (a whole document, one sheet, the workbook index).

    ``body`` is the markdown BELOW the frontmatter; ``rendered_sha256`` (H2) = sha256(body.encode("utf-8")).
    ``file_stem`` is the unit's output name inside a ``<Name>.<ext>.d/`` directory (e.g. ``01-q3-budget``);
    it is "" for a WHOLE unit, which is written as ``<Name>.<ext>.md`` / ``<name>.md``.
    """

    unit_id: str  # "whole" | "index" | "sheet:<n>" | "summary"
    kind: UnitKind
    index: int  # 0 for whole/index
    of: int  # total number of units in this conversion
    name: str  # sheet name etc.; "" for whole
    file_stem: str
    title: str
    summary: str  # converter-derived (title, headings, used range) — never model output
    tokens_estimate: int
    body: str
    rendered_sha256: str
    sidecars: tuple[tuple[str, bytes], ...] = ()  # (relative name, bytes), e.g. CSV for giant sheets


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """The result of converting one fetched source file (cached write-once by ``action_key``)."""

    status: ConversionStatus
    converter_id: str
    converter_version: str
    options_hash: str
    action_key: str
    content_sha256: str
    canonical_sha256: str
    units: tuple[RenderedUnit, ...]
    reason: str | None = None  # for non-OK statuses: human-readable cause, goes into the stub page
    from_cache: bool = False


# ---------------------------------------------------------------------------------------------------------
# Publication and reporting
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MirrorChange:
    """One path-level change in docs/mirror for CHANGELOG and the commit subject."""

    op: ChangeOp
    path: str  # docs-repo-relative, e.g. "mirror/finance/fy26-budget.xlsx.d/01-q3.md"
    source_id: str
    stable_id: str
    prev_path: str | None = None


@dataclass(frozen=True, slots=True)
class LintFinding:
    """One land-gate lint result; any finding with ``blocking=True`` stops the commit."""

    code: str  # e.g. "SYMLINK", "FRONTMATTER", "NONDETERMINISTIC", "SECRET", "PATH", "INDEX-BUDGET"
    path: str
    message: str
    blocking: bool = True


@dataclass(frozen=True, slots=True)
class SourceReport:
    """Per-source outcome of one cycle."""

    source_id: str
    kind: SourceKind
    pass_kind: PassKind | None  # None when the source was skipped (network down, paused, lock, error)
    enumeration_complete: bool
    counts: Mapping[Verdict, int] = field(default_factory=dict, hash=False)
    materialised_bytes: int = 0  # bytes downloaded (online-only files and Graph items), not local reads
    deferred: int = 0  # items left for a later run (budget or refused hydration)
    breaker_tripped: bool = False
    cursor_advanced: bool = False
    skipped_reason: str | None = None
    alarms: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    converted: int = 0  # files whose bytes were read and converted this run (pages changed or not)
    deferred_online_only: int = 0  # of ``deferred``: online-only files (a download the budget did not allow)


@dataclass(frozen=True, slots=True)
class CycleReport:
    """Everything one ``run_cycle`` did; the CLI prints it and STATE.md is rendered from it."""

    run_id: int
    mode: CycleMode
    sources: tuple[SourceReport, ...]
    changes: tuple[MirrorChange, ...]
    commit_sha: str | None  # None when nothing content-changing happened (no commit by design)
    lint_findings: tuple[LintFinding, ...] = ()
    broke_stale_lock: bool = False
    auth_required: bool = False
    exit_code: int = 0
    # KISS K06, the automatic checkpoint: "advanced" | "held" | "failed"; None without session pages
    checkpoint: str | None = None
    checkpoint_detail: str = ""  # advanced: the curated sha; failed: why (the cycle itself still landed)
    checkpoint_blockers: tuple[LintFinding, ...] = ()  # held: what holds it (curate.checkpoint_blockers)
    snapshot_tag: str | None = None  # [governance] archive: the snapshot/<UTC> tag cut at the new commit


TEAMS_MONTH_SCHEMA = "agentsync.teams-month/1"
"""``schema`` value of the per-channel-per-month JSON the Teams arm writes and the teams converter reads.

Shape (keys sorted on write, messages sorted by (created, id)):
``{"schema", "team_id", "team_name", "channel_id", "channel_name", "month": "YYYY-MM", "messages": [
{"id", "reply_to_id": str|null, "created": ISO-8601 UTC, "last_modified": ISO-8601 UTC, "from": display name,
"subject": str|null, "body_html": str, "deleted": bool, "attachments": [{"name", "content_url"}]}]}``.
"""
