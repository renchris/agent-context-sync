"""The H0/H1/H2 multi-state change classifier (design 4.3) — pure functions, no I/O (owner: manifest).

Phase 1 (``classify_observed`` / ``classify_pass``) decides from zero bytes; phase 2 (``classify_content``)
compares the canonical hash H1 after a budgeted fetch; phase 3 (``classify_output``) is the ninja-``restat``
early cutoff on every unit's rendered hash H2.  Reasons are short, machine-greppable strings.

Refinements over the literal rung list (each keeps the rung order and only ever errs towards reading bytes):

- An explicit provider tombstone is DELETED even for an id never seen (``deleted-unseen``), never CREATED.
- Content-equal plus a moved path or eTag is METADATA_ONLY (design section 5: "eTag moves, hash holds ->
  METADATA_ONLY"), so a rename/label sweep rewrites frontmatter without re-reading or re-converting.
- A row still carrying pending work (CREATED/MAYBE_CHANGED/CHANGED/DEFERRED/ERROR) is never downgraded to
  UNCHANGED/METADATA_ONLY/DATALESS by a later zero-byte signal: the verdict is carried forward
  (``pending:<verdict>``) — otherwise a budget-deferred change would be forgotten once H0 was re-stamped.
- A file row never materialised (no content hash) is MAYBE_CHANGED (``never-materialised``) once it is
  readable, and a tombstoned id that reappears is MAYBE_CHANGED (``reappeared``).
- When provider hash and cTag are absent, an equal eTag with equal size/mtime is UNCHANGED (``etag-equal``):
  the eTag covers the entire item (metadata + content).
"""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Protocol

from agentsync.config import BreakerConfig
from agentsync.manifest import ItemRow, OutputRow
from agentsync.model import (
    CanonicalHash,
    Classification,
    ConversionResult,
    ConversionStatus,
    ExtraValue,
    OutputStatus,
    PassKind,
    RowState,
    SourceItem,
    Verdict,
)

_log = logging.getLogger(__name__)

_PENDING: frozenset[Verdict] = frozenset(
    {Verdict.CREATED, Verdict.MAYBE_CHANGED, Verdict.CHANGED, Verdict.DEFERRED, Verdict.ERROR}
)
"""Row verdicts that mean the row has not been published yet (same set as ``Manifest.pending_work``)."""

_QUIET: frozenset[Verdict] = frozenset({Verdict.UNCHANGED, Verdict.METADATA_ONLY, Verdict.DATALESS})
"""Phase-1 outcomes that trigger no fetch; a pending row must never be silenced by one of them."""


class _H0Row(Protocol):
    """What the zero-byte content rungs read from a stored row (``ItemRow`` or the manifest's light
    observation tuple)."""

    @property
    def size(self) -> int | None: ...
    @property
    def mtime_ns(self) -> int | None: ...
    @property
    def ctime_ns(self) -> int | None: ...
    @property
    def ino(self) -> int | None: ...
    @property
    def mode(self) -> int | None: ...
    @property
    def gen_count(self) -> int | None: ...
    @property
    def quickxor(self) -> str | None: ...
    @property
    def sha1_remote(self) -> str | None: ...
    @property
    def etag(self) -> str | None: ...
    @property
    def ctag(self) -> str | None: ...


class _ObservedLike(_H0Row, Protocol):
    """Everything ``ClassifyContext.h0_unchanged`` compares: the columns ``upsert_observed`` would write."""

    @property
    def parent_id(self) -> str | None: ...
    @property
    def name(self) -> str: ...
    @property
    def rel_path(self) -> str: ...
    @property
    def is_dir(self) -> bool: ...
    @property
    def created_ns(self) -> int | None: ...
    @property
    def dataless(self) -> bool: ...
    @property
    def sha256_remote(self) -> str | None: ...
    @property
    def content_type(self) -> str | None: ...
    @property
    def content_sha256(self) -> str | None: ...
    @property
    def state(self) -> RowState: ...
    @property
    def last_verdict(self) -> Verdict | None: ...
    @property
    def extra(self) -> Mapping[str, ExtraValue]: ...


_FAST_STATES: frozenset[RowState] = frozenset({RowState.LIVE, RowState.QUARANTINED, RowState.REFUSED})
"""Row states an unchanged, non-dataless observation keeps as they are (``cycle._observed_state``)."""


@dataclass(frozen=True, slots=True)
class ClassifyContext:
    """Inputs every phase-1 decision needs besides the item and its row."""

    run_id: int
    pass_kind: PassKind
    written_at_ns: (
        int  # manifest's last write time: racily-clean guard (row.mtime_ns >= this => MAYBE_CHANGED)
    )

    def h0_unchanged(self, item: SourceItem, row: _ObservedLike | None) -> bool:
        """The H0 fast path: True only when ``classify_observed`` would say UNCHANGED with no rename AND
        ``upsert_observed`` would rewrite nothing but ``last_seen_run``/``last_verdict``.

        Every column the upsert writes must already hold the observed value (so the row is stamped with
        ``Manifest.touch_observed`` and never decoded or rewritten), the row must carry no pending work, have
        content, keep its state, and the ordinary content rungs (provider hash / cTag / eTag / stat tuple +
        gen_count + racily-clean guard) must yield UNCHANGED.  Anything else returns False and takes the full
        path, so the fast path can only ever skip work the full path would not have done.
        """
        if row is None or item.deleted or item.is_dir or item.dataless or row.is_dir or row.dataless:
            return False
        if row.state not in _FAST_STATES or row.last_verdict in _PENDING:
            return False
        if row.content_sha256 is None and row.state is RowState.LIVE:
            return False  # never materialised
        if (
            item.rel_path != row.rel_path
            or item.name != row.name
            or item.size != row.size
            or item.mtime_ns != row.mtime_ns
            or item.ctime_ns != row.ctime_ns
            or item.ino != row.ino
            or item.mode != row.mode
            or item.gen_count != row.gen_count
            or item.created_ns != row.created_ns
            or item.parent_id != row.parent_id
            or item.etag != row.etag
            or item.ctag != row.ctag
            or item.content_type != row.content_type
            or item.remote_hashes.quickxor != row.quickxor
            or item.remote_hashes.sha1 != row.sha1_remote
            or item.remote_hashes.sha256 != row.sha256_remote
            or dict(item.extra) != dict(row.extra)
        ):
            return False
        verdict, _reason = _content_rungs(item, row, self)
        return verdict is Verdict.UNCHANGED


@dataclass(frozen=True, slots=True)
class PassClassification:
    """Everything phase 1 decided for one source pass."""

    verdicts: tuple[Classification, ...]  # one per observed item, ordered by stable_id
    safe_saves: tuple[tuple[str, str], ...]  # (new_id, old_id) rekeys to apply before phase 2
    deletion_candidates: tuple[str, ...]  # stable ids; empty unless FULL and enumeration_complete
    breaker_tripped: bool  # candidates > breaker threshold: NO removal may be applied this pass


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _provider_hash(item: SourceItem, row: _H0Row) -> tuple[str, bool] | None:
    """(kind, equal) for the first provider hash present on BOTH sides (quickxor, then sha1); else None.

    Provider hashes are comparable only to themselves.  ``sha256Hash`` is documented "isn't supported. Don't
    use" for drives, so it never decides.
    """
    pairs = (
        ("quickxor", item.remote_hashes.quickxor, row.quickxor),
        ("sha1", item.remote_hashes.sha1, row.sha1_remote),
    )
    for kind, observed, stored in pairs:
        if observed is not None and stored is not None:
            return kind, observed == stored
    return None


def _stat_difference(item: SourceItem, row: _H0Row) -> str | None:
    """Name of the first H0 stat field that differs (mtime compared for inequality only), else None."""
    fields = (
        ("size", item.size, row.size),
        ("mtime", item.mtime_ns, row.mtime_ns),
        ("ctime", item.ctime_ns, row.ctime_ns),
        ("ino", item.ino, row.ino),
        ("mode", item.mode, row.mode),
    )
    for name, observed, stored in fields:
        if observed != stored:
            return name
    return None


def _content_rungs(item: SourceItem, row: _H0Row, ctx: ClassifyContext) -> tuple[Verdict, str]:
    """The zero-byte content rungs of design 4.3 for an existing, non-deleted file row."""
    if item.dataless:
        return Verdict.DATALESS, "dataless"
    provider = _provider_hash(item, row)
    if provider is not None:
        kind, equal = provider
        return (Verdict.UNCHANGED, f"{kind}-equal") if equal else (Verdict.MAYBE_CHANGED, f"{kind}-differs")
    if not item.remote_hashes.empty or row.quickxor is not None or row.sha1_remote is not None:
        _log.debug(
            "%s/%s: provider hash absent on one side; not read as unchanged", item.source_id, item.stable_id
        )
    if item.ctag is not None and row.ctag is not None:
        if item.ctag == row.ctag:
            return Verdict.UNCHANGED, "ctag-equal"
        return Verdict.MAYBE_CHANGED, "ctag-differs"
    size_mtime_hold = item.size == row.size and item.mtime_ns == row.mtime_ns
    if item.etag is not None and row.etag is not None:
        if item.etag != row.etag:
            if size_mtime_hold:
                return Verdict.METADATA_ONLY, "etag-moved"
            return Verdict.MAYBE_CHANGED, "etag-moved+size-or-mtime-differs"
        if size_mtime_hold:
            return Verdict.UNCHANGED, "etag-equal"
    differs = _stat_difference(item, row)
    if differs is not None:
        return Verdict.MAYBE_CHANGED, f"stat-differs:{differs}"
    observed_gen, stored_gen = item.gen_count, row.gen_count
    if not observed_gen or not stored_gen:
        return Verdict.MAYBE_CHANGED, "gen-count-unknown"
    if observed_gen < stored_gen:
        return Verdict.MAYBE_CHANGED, "gen-count-decreased"
    if observed_gen != stored_gen:
        return Verdict.MAYBE_CHANGED, "gen-count-differs"
    if row.mtime_ns is None or row.mtime_ns >= ctx.written_at_ns:
        return Verdict.MAYBE_CHANGED, "racily-clean"
    return Verdict.UNCHANGED, "stat-equal"


def _classify_dir(item: SourceItem, row: ItemRow, prev_path: str | None) -> Classification:
    """Directories only carry the id tree: CREATED / UNCHANGED / METADATA_ONLY / DELETED."""
    if row.state is RowState.TOMBSTONE:
        return Classification(item.source_id, item.stable_id, Verdict.METADATA_ONLY, "reappeared", prev_path)
    if prev_path is not None:
        return Classification(item.source_id, item.stable_id, Verdict.METADATA_ONLY, "renamed", prev_path)
    if item.parent_id != row.parent_id or _nfc(item.name) != row.name:
        return Classification(item.source_id, item.stable_id, Verdict.METADATA_ONLY, "moved")
    return Classification(item.source_id, item.stable_id, Verdict.UNCHANGED, "dir")


def classify_observed(item: SourceItem, row: ItemRow | None, ctx: ClassifyContext) -> Classification:
    """Phase 1 (zero bytes) for one observed object, in the exact rung order of design 4.3.

    new row -> CREATED; item.deleted -> DELETED; path moved -> prev_path set, continue; dataless -> DATALESS;
    quickXorHash both present -> differ ? MAYBE_CHANGED : UNCHANGED; hash absent on either side -> never
    UNCHANGED from hash, fall through (logged); cTag both present (files only) -> differ ? MAYBE_CHANGED :
    UNCHANGED; eTag moved but size/mtime hold -> METADATA_ONLY; stat tuple (size, mtime_ns, ctime_ns, ino,
    mode) and (ino, gen_count) equal -> racily-clean guard: row.mtime_ns >= ctx.written_at_ns ? MAYBE_CHANGED
    : UNCHANGED; gen_count None/0/decreased = unknown -> MAYBE_CHANGED; else MAYBE_CHANGED. mtime compared for
    inequality only. Directories: CREATED / UNCHANGED / METADATA_ONLY / DELETED only.
    """
    sid, iid = item.source_id, item.stable_id
    if item.deleted:
        if row is None:
            return Classification(sid, iid, Verdict.DELETED, "deleted-unseen")
        if row.state is RowState.TOMBSTONE:
            return Classification(sid, iid, Verdict.DELETED, "already-tombstoned")
        return Classification(sid, iid, Verdict.DELETED, "provider-tombstone")
    if row is None:
        return Classification(sid, iid, Verdict.CREATED, "new-id")
    rel_path = _nfc(item.rel_path)
    prev_path = row.rel_path if rel_path != row.rel_path else None
    if item.is_dir:
        return _classify_dir(item, row, prev_path)
    if row.state is RowState.TOMBSTONE:
        return Classification(sid, iid, Verdict.MAYBE_CHANGED, "reappeared", prev_path)

    verdict, reason = _content_rungs(item, row, ctx)
    if verdict is Verdict.UNCHANGED:
        moved: list[str] = []
        if prev_path is not None:
            moved.append("renamed")
        if item.etag is not None and row.etag is not None and item.etag != row.etag:
            moved.append("etag-moved")
        if moved:
            verdict, reason = Verdict.METADATA_ONLY, "+".join([reason, *moved])
    elif verdict is Verdict.METADATA_ONLY and prev_path is not None:
        reason = f"{reason}+renamed"

    if verdict in _QUIET:
        if row.last_verdict in _PENDING:
            carried = Verdict.CREATED if row.last_verdict is Verdict.CREATED else Verdict.MAYBE_CHANGED
            return Classification(sid, iid, carried, f"pending:{row.last_verdict}", prev_path)
        if (
            verdict is not Verdict.DATALESS
            and row.content_sha256 is None
            and row.state in (RowState.LIVE, RowState.DATALESS)
        ):
            return Classification(sid, iid, Verdict.MAYBE_CHANGED, "never-materialised", prev_path)
    return Classification(sid, iid, verdict, reason, prev_path)


def classify_content(row: ItemRow | None, canonical: CanonicalHash) -> Classification:
    """Phase 2, after materialise: canonical (H1) equal to row's -> TOUCHED_NOT_CHANGED, else CHANGED.

    On CHANGED, ``changed_parts`` lists the OOXML parts whose digests differ (logged by the caller). A row
    with no previous canonical hash (CREATED) is CHANGED.

    ``row`` None (a caller that skipped ``upsert_observed``) yields CHANGED with empty ids: the signature
    carries no identity for that case.  A different canonical ``method`` is CHANGED (digests of two methods
    are not comparable), and a tombstoned row is CHANGED (``reappeared``: its pages must be rebuilt).
    """
    if row is None:
        return Classification("", "", Verdict.CHANGED, "no-row")
    sid, iid = row.source_id, row.stable_id
    if row.canonical_sha256 is None:
        return Classification(sid, iid, Verdict.CHANGED, "no-previous-canonical", row.prev_path)
    if row.state is RowState.TOMBSTONE:
        return Classification(sid, iid, Verdict.CHANGED, "reappeared", row.prev_path)
    if row.canonical_method is not None and row.canonical_method != canonical.method:
        return Classification(sid, iid, Verdict.CHANGED, "canonical-method-changed")
    if row.canonical_sha256 == canonical.sha256:
        return Classification(sid, iid, Verdict.TOUCHED_NOT_CHANGED, "canonical-equal")
    before = dict(row.canonical_parts)
    after = dict(canonical.parts)
    changed = tuple(
        sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    )
    return Classification(sid, iid, Verdict.CHANGED, "canonical-differs", changed_parts=changed)


def classify_output(previous: Sequence[OutputRow], result: ConversionResult) -> Verdict:
    """Phase 3, H2 early cutoff: OUTPUT_UNCHANGED iff the unit set and every unit's rendered_sha256 match.

    Only an OK conversion can cut off, and only against previous outputs that were all OK (a stub page from a
    failed/unreadable run must be replaced).  Tombstoned output rows of removed units are ignored.
    """
    if result.status is not ConversionStatus.OK or not result.units:
        return Verdict.CHANGED
    before = {o.unit_id: o for o in previous if o.status is not OutputStatus.TOMBSTONE}
    if not before or any(o.status is not OutputStatus.OK for o in before.values()):
        return Verdict.CHANGED
    after = {u.unit_id: u.rendered_sha256 for u in result.units}
    if len(after) != len(result.units) or after.keys() != before.keys():
        return Verdict.CHANGED
    if any(before[unit].rendered_sha256 != digest for unit, digest in after.items()):
        return Verdict.CHANGED
    return Verdict.OUTPUT_UNCHANGED


def match_safe_saves(created: Sequence[SourceItem], missing: Sequence[ItemRow]) -> list[tuple[str, str]]:
    """Pair a CREATED file with a vanished row at the SAME rel_path (Office safe-save: new FILEID).

    Only meaningful in a FULL pass.  Returns (new_id, old_id) sorted by new_id; each id used at most once.

    Directories, provider-deleted items and tombstoned rows never pair.  When several vanished rows share the
    path, the one seen most recently wins; a tie is ambiguous and pairs nothing (it stays a deletion
    candidate and the new id stays CREATED).  Paths compare exactly after NFC.
    """
    by_path: dict[str, list[ItemRow]] = {}
    for row in missing:
        if row.is_dir or row.state is RowState.TOMBSTONE:
            continue
        by_path.setdefault(_nfc(row.rel_path), []).append(row)
    new_by_path: dict[str, list[SourceItem]] = {}
    for item in created:
        if item.is_dir or item.deleted:
            continue
        new_by_path.setdefault(_nfc(item.rel_path), []).append(item)
    pairs: list[tuple[str, str]] = []
    for path in sorted(new_by_path.keys() & by_path.keys()):
        news = new_by_path[path]
        if len({i.stable_id for i in news}) != 1:
            continue  # two new ids at one path in one listing: not a safe-save we can attribute
        olds = sorted(by_path[path], key=lambda r: (-r.last_seen_run, r.stable_id))
        if len(olds) > 1 and olds[0].last_seen_run == olds[1].last_seen_run:
            continue
        if olds[0].stable_id == news[0].stable_id:
            continue
        pairs.append((news[0].stable_id, olds[0].stable_id))
    pairs.sort()
    return pairs


def deletion_candidates(
    unseen: Sequence[ItemRow], *, pass_kind: PassKind, enumeration_complete: bool
) -> list[ItemRow]:
    """Absence is evidence only inside a complete FULL pass; returns [] for DELTA or incomplete passes."""
    if pass_kind is not PassKind.FULL or not enumeration_complete:
        return []
    rows = [r for r in unseen if not r.is_dir and r.state is not RowState.TOMBSTONE]
    return sorted(rows, key=lambda r: r.stable_id)


def breaker_trips(candidates: int, live_rows: int, breaker: BreakerConfig) -> bool:
    """True when candidates > max(breaker.fraction * live_rows, breaker.floor) (per-scope, floored).

    Computed in exact rational arithmetic (``Fraction(str(fraction))``) so 0.29 x 100 is 29, not 28.99...
    """
    limit = max(Fraction(str(breaker.fraction)) * live_rows, Fraction(breaker.floor))
    return candidates > limit


def classify_pass(
    items: Sequence[SourceItem],
    rows: dict[str, ItemRow],
    unseen: Sequence[ItemRow],
    ctx: ClassifyContext,
    *,
    enumeration_complete: bool,
    live_rows: int,
    breaker: BreakerConfig,
    breaker_active: bool,
    unchanged: Sequence[SourceItem] = (),
) -> PassClassification:
    """Run phase 1 over a whole pass: per-item verdicts, safe-save pairing, deletion candidates, breaker.

    ``rows`` maps stable_id -> existing row for this source.  A breaker that is already active, or trips
    now, yields ``breaker_tripped=True`` and the caller applies NO absence-based removal.

    An id listed twice in one pass (Graph delta may repeat an item) is classified from its LAST record.
    ``unseen`` may be computed before or after this run's upserts: rows observed in this pass are ignored.
    A paired safe-save's new id becomes MAYBE_CHANGED (``safe-save``, ``replaces_id`` set) so phase 2 compares
    H1 against the old row's hash once ``Manifest.rekey`` has moved it (the Office no-op save stops there).

    ``unchanged`` (keyword, optional) lists observations the caller already proved unchanged with
    ``ClassifyContext.h0_unchanged`` and left out of ``items``/``rows``: each gets UNCHANGED (``h0-equal``),
    counts as observed (never missing, never a safe-save partner) and needs no row.
    """
    latest: dict[str, SourceItem] = {}
    for item in items:
        latest[item.stable_id] = item
    ordered = [latest[sid] for sid in sorted(latest)]
    verdicts = {it.stable_id: classify_observed(it, rows.get(it.stable_id), ctx) for it in ordered}
    fast = {it.stable_id for it in unchanged if it.stable_id not in latest}
    for it in unchanged:
        if it.stable_id in fast:
            verdicts[it.stable_id] = Classification(it.source_id, it.stable_id, Verdict.UNCHANGED, "h0-equal")

    missing = [r for r in unseen if r.stable_id not in latest and r.stable_id not in fast]
    safe_saves: list[tuple[str, str]] = []
    # Any FULL pass pairs, complete or not: the new id was listed AT the path, so that directory was walked
    # and the old id is provably not there any more (an unrelated unreadable folder, or a File Provider
    # tree's empty cloud folder, must not split a document into [DELETED UPSTREAM] + a disambiguated twin).
    if ctx.pass_kind is PassKind.FULL:
        created = [it for it in ordered if verdicts[it.stable_id].verdict is Verdict.CREATED]
        safe_saves = match_safe_saves(created, missing)
        for new_id, old_id in safe_saves:
            verdicts[new_id] = replace(
                verdicts[new_id], verdict=Verdict.MAYBE_CHANGED, reason="safe-save", replaces_id=old_id
            )
    replaced = {old for _new, old in safe_saves}
    candidates = deletion_candidates(
        [r for r in missing if r.stable_id not in replaced],
        pass_kind=ctx.pass_kind,
        enumeration_complete=enumeration_complete,
    )
    tripped = breaker_active or (bool(candidates) and breaker_trips(len(candidates), live_rows, breaker))
    if tripped and candidates:
        _log.warning(
            "deletion breaker: %d candidates against %d live rows; no absence-based removal this pass",
            len(candidates),
            live_rows,
        )
    return PassClassification(
        verdicts=tuple(verdicts[sid] for sid in sorted(verdicts)),
        safe_saves=tuple(safe_saves),
        deletion_candidates=tuple(r.stable_id for r in candidates),
        breaker_tripped=tripped,
    )
