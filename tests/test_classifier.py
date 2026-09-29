"""H0 -> H1 -> H2 classifier: every verdict transition, property-style invariants, multi-cycle scenarios."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TypeVar

import pytest

from agentsync.classifier import (
    ClassifyContext,
    PassClassification,
    breaker_trips,
    classify_content,
    classify_observed,
    classify_output,
    classify_pass,
    deletion_candidates,
    match_safe_saves,
)
from agentsync.config import BreakerConfig, SourceConfig
from agentsync.manifest import ItemRow, Manifest, OutputRow
from agentsync.model import (
    CanonicalHash,
    Classification,
    ConversionResult,
    ConversionStatus,
    CycleMode,
    OutputStatus,
    PassKind,
    RemoteHashes,
    RenderedUnit,
    RowState,
    SourceItem,
    SourceKind,
    UnitKind,
    Verdict,
)

T = TypeVar("T")

WRITTEN_AT = 10_000
OLD_MTIME = 5_000  # before the manifest's last write: not racy
CTX = ClassifyContext(run_id=7, pass_kind=PassKind.DELTA, written_at_ns=WRITTEN_AT)
FULL = ClassifyContext(run_id=7, pass_kind=PassKind.FULL, written_at_ns=WRITTEN_AT)
PHASE1 = {
    Verdict.CREATED,
    Verdict.UNCHANGED,
    Verdict.MAYBE_CHANGED,
    Verdict.METADATA_ONLY,
    Verdict.DATALESS,
    Verdict.DELETED,
}
PENDING = [Verdict.CREATED, Verdict.MAYBE_CHANGED, Verdict.CHANGED, Verdict.DEFERRED, Verdict.ERROR]
SETTLED = [
    None,
    Verdict.UNCHANGED,
    Verdict.METADATA_ONLY,
    Verdict.TOUCHED_NOT_CHANGED,
    Verdict.OUTPUT_UNCHANGED,
    Verdict.DATALESS,
    Verdict.QUARANTINED,
    Verdict.REFUSED,
]


def h(c: str) -> str:
    return c * 64


def local(stable_id: str = "vol:1", rel_path: str = "a/Report.docx", **kw: object) -> SourceItem:
    base: dict[str, object] = {
        "source_id": "src",
        "stable_id": stable_id,
        "rel_path": rel_path,
        "name": rel_path.rsplit("/", 1)[-1],
        "size": 100,
        "mtime_ns": OLD_MTIME,
        "ctime_ns": OLD_MTIME,
        "ino": 1,
        "mode": 0o100644,
        "gen_count": 3,
    }
    base.update(kw)
    return SourceItem(**base)  # type: ignore[arg-type]


def graph(stable_id: str = "01ABC", rel_path: str = "Finance/Budget.xlsx", **kw: object) -> SourceItem:
    base: dict[str, object] = {
        "source_id": "src",
        "stable_id": stable_id,
        "rel_path": rel_path,
        "name": rel_path.rsplit("/", 1)[-1],
        "size": 100,
        "mtime_ns": OLD_MTIME,
        "ctime_ns": OLD_MTIME,
        "etag": '"{E},1"',
        "ctag": '"c:{E},1"',
        "remote_hashes": RemoteHashes(quickxor="qx1"),
        "parent_id": "fin",
    }
    base.update(kw)
    return SourceItem(**base)  # type: ignore[arg-type]


def row_of(it: SourceItem, **kw: object) -> ItemRow:
    """The manifest row a previous observation of ``it`` left behind (published, settled)."""
    base: dict[str, object] = {
        "source_id": it.source_id,
        "stable_id": it.stable_id,
        "parent_id": it.parent_id,
        "name": it.name,
        "rel_path": it.rel_path,
        "prev_path": None,
        "is_dir": it.is_dir,
        "size": it.size,
        "mtime_ns": it.mtime_ns,
        "ctime_ns": it.ctime_ns,
        "created_ns": it.created_ns,
        "ino": it.ino,
        "mode": it.mode,
        "gen_count": it.gen_count,
        "dataless": it.dataless,
        "quickxor": it.remote_hashes.quickxor,
        "sha1_remote": it.remote_hashes.sha1,
        "sha256_remote": it.remote_hashes.sha256,
        "etag": it.etag,
        "ctag": it.ctag,
        "content_type": it.content_type,
        "content_sha256": h("c"),
        "canonical_sha256": h("1"),
        "canonical_method": "ooxml-parts@1",
        "canonical_parts": (("word/document.xml", h("d")),),
        "state": RowState.LIVE,
        "state_reason": None,
        "last_verdict": Verdict.UNCHANGED,
        "first_seen_run": 1,
        "last_seen_run": 6,
        "principal": None,
        "sensitivity_label": None,
    }
    base.update(kw)
    return ItemRow(**base)  # type: ignore[arg-type]


def verdict(it: SourceItem, row: ItemRow | None, ctx: ClassifyContext = CTX) -> tuple[Verdict, str]:
    c = classify_observed(it, row, ctx)
    return c.verdict, c.reason


# ---- phase 1: the transition table -------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    name: str
    before: SourceItem
    after: SourceItem
    expect: Verdict
    reason: str
    row_kw: tuple[tuple[str, object], ...] = ()


CASES = [
    # local (H0 + gen_count)
    Case("local untouched", local(), local(), Verdict.UNCHANGED, "stat-equal"),
    Case(
        "local content write",
        local(),
        local(size=101, mtime_ns=6_000, gen_count=4),
        Verdict.MAYBE_CHANGED,
        "stat-differs:size",
    ),
    Case(
        "local mtime restored older",
        local(),
        local(mtime_ns=OLD_MTIME - 1),
        Verdict.MAYBE_CHANGED,
        "stat-differs:mtime",
    ),
    Case(
        "local ctime only (xattr/chmod)",
        local(),
        local(ctime_ns=OLD_MTIME + 1),
        Verdict.MAYBE_CHANGED,
        "stat-differs:ctime",
    ),
    Case("local mode", local(), local(mode=0o100600), Verdict.MAYBE_CHANGED, "stat-differs:mode"),
    Case(
        "local same stat, gen moved", local(), local(gen_count=4), Verdict.MAYBE_CHANGED, "gen-count-differs"
    ),
    Case(
        "local same stat, gen decreased",
        local(),
        local(gen_count=2),
        Verdict.MAYBE_CHANGED,
        "gen-count-decreased",
    ),
    Case("local gen unknown now", local(), local(gen_count=None), Verdict.MAYBE_CHANGED, "gen-count-unknown"),
    Case(
        "local gen zero stored",
        local(gen_count=0),
        local(gen_count=0),
        Verdict.MAYBE_CHANGED,
        "gen-count-unknown",
    ),
    Case(
        "local racily clean",
        local(mtime_ns=WRITTEN_AT, ctime_ns=WRITTEN_AT),
        local(mtime_ns=WRITTEN_AT, ctime_ns=WRITTEN_AT),
        Verdict.MAYBE_CHANGED,
        "racily-clean",
    ),
    Case(
        "local mtime after write time",
        local(mtime_ns=WRITTEN_AT + 5, ctime_ns=1),
        local(mtime_ns=WRITTEN_AT + 5, ctime_ns=1),
        Verdict.MAYBE_CHANGED,
        "racily-clean",
    ),
    Case("local evicted to placeholder", local(), local(dataless=True), Verdict.DATALESS, "dataless"),
    Case(
        "local changed while dataless",
        local(),
        local(dataless=True, size=5, mtime_ns=1),
        Verdict.DATALESS,
        "dataless",
    ),
    Case(
        "local rename same stat",
        local(),
        local(rel_path="b/Report.docx"),
        Verdict.METADATA_ONLY,
        "stat-equal+renamed",
    ),
    Case(
        "local rename + edit",
        local(),
        local(rel_path="b/Report.docx", size=1),
        Verdict.MAYBE_CHANGED,
        "stat-differs:size",
    ),
    # graph drive (provider hash first)
    Case("graph untouched", graph(), graph(), Verdict.UNCHANGED, "quickxor-equal"),
    Case(
        "graph in-place overwrite",
        graph(),
        graph(remote_hashes=RemoteHashes(quickxor="qx2"), etag='"{E},2"', ctag='"c:{E},2"', size=101),
        Verdict.MAYBE_CHANGED,
        "quickxor-differs",
    ),
    Case(
        "graph hash differs but tags hold",
        graph(),
        graph(remote_hashes=RemoteHashes(quickxor="qx2")),
        Verdict.MAYBE_CHANGED,
        "quickxor-differs",
    ),
    Case(
        "graph label/rename: etag moves, hash holds",
        graph(),
        graph(etag='"{E},9"'),
        Verdict.METADATA_ONLY,
        "quickxor-equal+etag-moved",
    ),
    Case(
        "graph move to another folder",
        graph(),
        graph(rel_path="Archive/Budget.xlsx", parent_id="arc", etag='"{E},2"'),
        Verdict.METADATA_ONLY,
        "quickxor-equal+renamed+etag-moved",
    ),
    Case(
        "graph hash equal, stat noise ignored",
        graph(),
        graph(mtime_ns=1, size=1),
        Verdict.UNCHANGED,
        "quickxor-equal",
    ),
    Case(
        "graph sha1 only (personal)",
        graph(remote_hashes=RemoteHashes(sha1="s1")),
        graph(remote_hashes=RemoteHashes(sha1="s2")),
        Verdict.MAYBE_CHANGED,
        "sha1-differs",
    ),
    Case(
        "graph sha256 never decides",
        graph(remote_hashes=RemoteHashes(sha256="x"), ctag=None, etag=None),
        graph(remote_hashes=RemoteHashes(sha256="x"), ctag=None, etag=None),
        Verdict.MAYBE_CHANGED,
        "gen-count-unknown",
    ),
    Case(
        "graph hash absent now -> ctag decides",
        graph(),
        graph(remote_hashes=RemoteHashes()),
        Verdict.UNCHANGED,
        "ctag-equal",
    ),
    Case(
        "graph hash absent now, ctag moved",
        graph(),
        graph(remote_hashes=RemoteHashes(), ctag='"c:{E},2"'),
        Verdict.MAYBE_CHANGED,
        "ctag-differs",
    ),
    Case(
        "ODB: no hash, no ctag, etag moved, size/mtime hold",
        graph(remote_hashes=RemoteHashes(), ctag=None),
        graph(remote_hashes=RemoteHashes(), ctag=None, etag='"{E},2"'),
        Verdict.METADATA_ONLY,
        "etag-moved",
    ),
    Case(
        "ODB: no hash, no ctag, etag moved, size moved",
        graph(remote_hashes=RemoteHashes(), ctag=None),
        graph(remote_hashes=RemoteHashes(), ctag=None, etag='"{E},2"', size=7),
        Verdict.MAYBE_CHANGED,
        "etag-moved+size-or-mtime-differs",
    ),
    Case(
        "OneNote: no hash, no ctag, etag equal",
        graph(remote_hashes=RemoteHashes(), ctag=None),
        graph(remote_hashes=RemoteHashes(), ctag=None),
        Verdict.UNCHANGED,
        "etag-equal",
    ),
    Case(
        "no hash/ctag/etag at all",
        graph(remote_hashes=RemoteHashes(), ctag=None, etag=None),
        graph(remote_hashes=RemoteHashes(), ctag=None, etag=None),
        Verdict.MAYBE_CHANGED,
        "gen-count-unknown",
    ),
    Case(
        "zero-byte item: no hash ever",
        graph(remote_hashes=RemoteHashes(), size=0, ctag=None),
        graph(remote_hashes=RemoteHashes(), size=0, ctag=None, etag='"{E},2"', mtime_ns=9),
        Verdict.MAYBE_CHANGED,
        "etag-moved+size-or-mtime-differs",
    ),
    # row state / pending work
    Case(
        "tombstoned id reappears",
        local(),
        local(),
        Verdict.MAYBE_CHANGED,
        "reappeared",
        (("state", RowState.TOMBSTONE),),
    ),
    Case(
        "never materialised (content NULL)",
        local(),
        local(),
        Verdict.MAYBE_CHANGED,
        "never-materialised",
        (("content_sha256", None), ("canonical_sha256", None)),
    ),
    Case(
        "refused row, nothing moved",
        local(),
        local(),
        Verdict.UNCHANGED,
        "stat-equal",
        (("state", RowState.REFUSED), ("content_sha256", None)),
    ),
    Case(
        "quarantined row edited",
        local(),
        local(size=1),
        Verdict.MAYBE_CHANGED,
        "stat-differs:size",
        (("state", RowState.QUARANTINED),),
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_phase1_transition(case: Case) -> None:
    row = row_of(case.before, **dict(case.row_kw))
    c = classify_observed(case.after, row, CTX)
    assert (c.verdict, c.reason) == (case.expect, case.reason)
    assert (c.source_id, c.stable_id) == ("src", case.after.stable_id)
    moved = case.after.rel_path != case.before.rel_path
    assert (c.prev_path == case.before.rel_path) if moved else c.prev_path is None


def test_created_and_deleted_edges() -> None:
    assert verdict(local(), None) == (Verdict.CREATED, "new-id")
    assert verdict(local(dataless=True), None) == (Verdict.CREATED, "new-id")
    assert verdict(local(deleted=True), None) == (Verdict.DELETED, "deleted-unseen")
    assert verdict(graph(deleted=True, rel_path=""), row_of(graph())) == (
        Verdict.DELETED,
        "provider-tombstone",
    )
    tomb = row_of(graph(), state=RowState.TOMBSTONE)
    assert verdict(graph(deleted=True), tomb) == (Verdict.DELETED, "already-tombstoned")
    c = classify_observed(graph(deleted=True, rel_path="elsewhere"), row_of(graph()), CTX)
    assert c.prev_path is None  # a deleted record's path is not a rename


@pytest.mark.parametrize("last", PENDING, ids=str)
@pytest.mark.parametrize(
    "after",
    [local(), local(rel_path="z/moved.docx"), local(dataless=True)],
    ids=["same", "renamed", "dataless"],
)
def test_pending_work_is_never_silenced(last: Verdict, after: SourceItem) -> None:
    """A deferred/erroring/unpublished row stays pending even when H0 now looks settled."""
    c = classify_observed(after, row_of(local(), last_verdict=last), CTX)
    expected = Verdict.CREATED if last is Verdict.CREATED else Verdict.MAYBE_CHANGED
    assert (c.verdict, c.reason) == (expected, f"pending:{last}")


@pytest.mark.parametrize("last", SETTLED, ids=str)
def test_settled_rows_stay_quiet(last: Verdict | None) -> None:
    assert verdict(local(), row_of(local(), last_verdict=last)) == (Verdict.UNCHANGED, "stat-equal")


def test_dataless_without_content_stays_dataless() -> None:
    row = row_of(
        local(dataless=True),
        content_sha256=None,
        canonical_sha256=None,
        last_verdict=Verdict.DATALESS,
        state=RowState.DATALESS,
    )
    assert verdict(local(dataless=True), row) == (Verdict.DATALESS, "dataless")
    # hydrated later with the very same stat: never materialised, so it must be read
    assert verdict(local(), row) == (Verdict.MAYBE_CHANGED, "never-materialised")


@pytest.mark.parametrize(
    ("after", "expect", "reason"),
    [
        (graph(is_dir=True), Verdict.UNCHANGED, "dir"),
        (graph(is_dir=True, rel_path="Finance/Renamed"), Verdict.METADATA_ONLY, "renamed"),
        (graph(is_dir=True, name="Other"), Verdict.METADATA_ONLY, "moved"),
        (graph(is_dir=True, parent_id="x"), Verdict.METADATA_ONLY, "moved"),
        (
            graph(is_dir=True, ctag='"c:{E},99"', etag='"{E},99"', size=5, mtime_ns=1),
            Verdict.UNCHANGED,
            "dir",
        ),
        (graph(is_dir=True, dataless=True), Verdict.UNCHANGED, "dir"),
        (graph(is_dir=True, deleted=True), Verdict.DELETED, "provider-tombstone"),
    ],
)
def test_directories_only_carry_the_tree(after: SourceItem, expect: Verdict, reason: str) -> None:
    row = row_of(graph(is_dir=True), last_verdict=Verdict.CREATED, content_sha256=None)
    assert verdict(after, row) == (expect, reason)


def test_directory_reappears_and_is_created() -> None:
    d = graph("d1", "Finance", is_dir=True)
    assert verdict(d, None) == (Verdict.CREATED, "new-id")
    assert verdict(d, row_of(d, state=RowState.TOMBSTONE)) == (Verdict.METADATA_ONLY, "reappeared")


def test_racily_clean_guard_uses_written_at_boundary() -> None:
    it = local(mtime_ns=WRITTEN_AT - 1)
    assert verdict(it, row_of(it)) == (Verdict.UNCHANGED, "stat-equal")
    it = local(mtime_ns=WRITTEN_AT)
    assert verdict(it, row_of(it)) == (Verdict.MAYBE_CHANGED, "racily-clean")
    never_finished = ClassifyContext(run_id=1, pass_kind=PassKind.FULL, written_at_ns=0)
    assert verdict(local(), row_of(local()), never_finished) == (Verdict.MAYBE_CHANGED, "racily-clean")


# ---- phase 1: property-style invariants over random observations ------------------------------------------


def _random_pair(rng: random.Random) -> tuple[SourceItem, ItemRow | None]:
    def pick(*options: T) -> T:
        return rng.choice(options)

    base = (graph if rng.random() < 0.5 else local)()
    before = replace(
        base,
        size=pick(0, 100, 101),
        mtime_ns=pick(OLD_MTIME, OLD_MTIME + 1, WRITTEN_AT, WRITTEN_AT + 1),
        ctime_ns=pick(OLD_MTIME, OLD_MTIME + 1),
        ino=pick(None, 1, 2),
        mode=pick(None, 0o100644),
        gen_count=pick(None, 0, 3, 4),
        etag=pick(None, "e1", "e2"),
        ctag=pick(None, "c1", "c2"),
        remote_hashes=RemoteHashes(quickxor=pick(None, "q1", "q2"), sha1=pick(None, "s1", "s2")),
        is_dir=rng.random() < 0.1,
    )
    after = replace(
        before,
        rel_path=pick(before.rel_path, "moved/" + before.name),
        size=pick(before.size, 100, 101),
        mtime_ns=pick(before.mtime_ns, OLD_MTIME, WRITTEN_AT + 3),
        ctime_ns=pick(before.ctime_ns, OLD_MTIME + 7),
        gen_count=pick(before.gen_count, None, 0, 2, 5),
        etag=pick(before.etag, None, "e3"),
        ctag=pick(before.ctag, None, "c3"),
        remote_hashes=pick(before.remote_hashes, RemoteHashes(), RemoteHashes(quickxor="q9")),
        dataless=rng.random() < 0.15,
        deleted=rng.random() < 0.08,
    )
    if rng.random() < 0.1:
        return after, None
    row = row_of(
        before,
        last_verdict=pick(*SETTLED, *PENDING),
        state=pick(
            RowState.LIVE,
            RowState.LIVE,
            RowState.DATALESS,
            RowState.QUARANTINED,
            RowState.REFUSED,
            RowState.TOMBSTONE,
        ),
        content_sha256=pick(h("c"), h("c"), None),
    )
    return after, row


def _check_invariants(it: SourceItem, row: ItemRow | None, c: Classification) -> None:
    assert c.verdict in PHASE1
    assert (c.source_id, c.stable_id) == (it.source_id, it.stable_id)
    assert c.replaces_id is None and c.changed_parts == ()
    if it.deleted:
        assert c.verdict is Verdict.DELETED and c.prev_path is None
        return
    assert c.verdict is not Verdict.DELETED  # absence/tombstones never come from a live observation
    if row is None:
        assert c.verdict is Verdict.CREATED
        return
    assert c.verdict is not Verdict.CREATED or row.last_verdict is Verdict.CREATED
    moved = it.rel_path != row.rel_path
    assert (c.prev_path == row.rel_path) if moved else c.prev_path is None
    if it.is_dir:
        assert c.verdict in {Verdict.UNCHANGED, Verdict.METADATA_ONLY}
        return
    if row.state is RowState.TOMBSTONE:
        assert c.verdict is Verdict.MAYBE_CHANGED
        return
    if row.last_verdict in PENDING:
        assert c.verdict in {Verdict.CREATED, Verdict.MAYBE_CHANGED}  # never silenced
    if c.verdict is Verdict.DATALESS:
        assert it.dataless
    qx_new, qx_old = it.remote_hashes.quickxor, row.quickxor
    if not it.dataless and qx_new is not None and qx_old is not None and qx_new != qx_old:
        assert c.verdict in {Verdict.MAYBE_CHANGED, Verdict.CREATED}  # a moved provider hash is always read
    if c.verdict is Verdict.UNCHANGED:
        assert not moved
        assert not (it.etag and row.etag and it.etag != row.etag)
        hash_equal = (qx_new is not None and qx_new == qx_old) or (
            (qx_new is None or qx_old is None)
            and it.remote_hashes.sha1 is not None
            and it.remote_hashes.sha1 == row.sha1_remote
        )
        tag_equal = it.ctag is not None and it.ctag == row.ctag
        etag_equal = it.etag is not None and it.etag == row.etag and it.size == row.size
        stat_equal = (
            (it.size, it.mtime_ns, it.ctime_ns, it.ino, it.mode)
            == (row.size, row.mtime_ns, row.ctime_ns, row.ino, row.mode)
            and bool(it.gen_count)
            and it.gen_count == row.gen_count
            and row.mtime_ns is not None
            and row.mtime_ns < WRITTEN_AT
        )
        assert hash_equal or tag_equal or etag_equal or stat_equal


@pytest.mark.parametrize("seed", range(8))
def test_phase1_invariants_hold_for_random_observations(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(500):
        it, row = _random_pair(rng)
        c = classify_observed(it, row, CTX)
        _check_invariants(it, row, c)
        assert classify_observed(it, row, CTX) == c  # pure and deterministic


@pytest.mark.parametrize("seed", range(4))
def test_mtime_is_compared_for_inequality_only(seed: int) -> None:
    """Moving mtime forward or backward by the same amount yields the same verdict."""
    rng = random.Random(seed)
    for _ in range(200):
        delta = rng.randint(1, 3_000)
        before = local(mtime_ns=OLD_MTIME, gen_count=rng.choice([None, 3]))
        row = row_of(before)
        up = classify_observed(replace(before, mtime_ns=OLD_MTIME + delta), row, CTX)
        down = classify_observed(replace(before, mtime_ns=OLD_MTIME - delta), row, CTX)
        assert (
            (up.verdict, up.reason)
            == (down.verdict, down.reason)
            == (Verdict.MAYBE_CHANGED, "stat-differs:mtime")
        )


def test_identity_is_never_the_path() -> None:
    """Two ids swapping paths are two renames, not two edits."""
    a, b = local("vol:1", "x/A.docx", ino=1), local("vol:2", "x/B.docx", ino=2)
    ca = classify_observed(replace(a, rel_path="x/B.docx", name="B.docx"), row_of(a), CTX)
    cb = classify_observed(replace(b, rel_path="x/A.docx", name="A.docx"), row_of(b), CTX)
    assert (ca.verdict, ca.prev_path) == (Verdict.METADATA_ONLY, "x/A.docx")
    assert (cb.verdict, cb.prev_path) == (Verdict.METADATA_ONLY, "x/B.docx")


# ---- phase 2 (H1) ------------------------------------------------------------------------------------------


def test_phase2_office_noop_save_is_touched_not_changed() -> None:
    row = row_of(local())
    c = classify_content(row, CanonicalHash(h("1"), "ooxml-parts@1", (("word/document.xml", h("d")),)))
    assert (c.verdict, c.reason, c.changed_parts) == (Verdict.TOUCHED_NOT_CHANGED, "canonical-equal", ())


def test_phase2_changed_lists_differing_parts() -> None:
    row = row_of(local(), canonical_parts=(("a.xml", h("a")), ("b.xml", h("b")), ("gone.xml", h("9"))))
    new = CanonicalHash(
        h("2"), "ooxml-parts@1", (("a.xml", h("a")), ("b.xml", h("B".lower())), ("new.xml", h("n")))
    )
    c = classify_content(row, new)
    assert c.verdict is Verdict.CHANGED and c.reason == "canonical-differs"
    assert c.changed_parts == ("gone.xml", "new.xml")
    new2 = CanonicalHash(
        h("2"), "ooxml-parts@1", (("a.xml", h("0")), ("b.xml", h("b")), ("gone.xml", h("9")))
    )
    assert classify_content(row, new2).changed_parts == ("a.xml",)


@pytest.mark.parametrize(
    ("row_kw", "reason"),
    [
        ({"canonical_sha256": None, "last_verdict": Verdict.CREATED}, "no-previous-canonical"),
        ({"state": RowState.TOMBSTONE}, "reappeared"),
        ({"canonical_method": "bytes@1"}, "canonical-method-changed"),
    ],
)
def test_phase2_forced_changed(row_kw: dict[str, object], reason: str) -> None:
    c = classify_content(row_of(local(), **row_kw), CanonicalHash(h("1"), "ooxml-parts@1"))
    assert (c.verdict, c.reason) == (Verdict.CHANGED, reason)
    assert c.stable_id == "vol:1"


def test_phase2_without_row() -> None:
    c = classify_content(None, CanonicalHash(h("1"), "bytes@1"))
    assert c.verdict is Verdict.CHANGED and c.reason == "no-row"


@pytest.mark.parametrize("seed", range(4))
def test_phase2_property_equal_iff_touched(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(300):
        stored = rng.choice([h("1"), h("2"), None])
        new = rng.choice([h("1"), h("2")])
        method = rng.choice(["bytes@1", "ooxml-parts@1"])
        row = row_of(local(), canonical_sha256=stored, canonical_method=rng.choice([method, "bytes@1", None]))
        c = classify_content(row, CanonicalHash(new, method))
        same = stored == new and row.canonical_method in (None, method)
        assert (c.verdict is Verdict.TOUCHED_NOT_CHANGED) == same
        assert c.verdict in {Verdict.TOUCHED_NOT_CHANGED, Verdict.CHANGED}


# ---- phase 3 (H2 early cutoff) -----------------------------------------------------------------------------


def unit(uid: str, body: str) -> RenderedUnit:
    return RenderedUnit(
        unit_id=uid,
        kind=UnitKind.WHOLE,
        index=0,
        of=1,
        name="",
        file_stem="",
        title="t",
        summary="s",
        tokens_estimate=1,
        body=body,
        rendered_sha256=hashlib.sha256(body.encode()).hexdigest(),
    )


def result(*units: RenderedUnit, status: ConversionStatus = ConversionStatus.OK) -> ConversionResult:
    return ConversionResult(
        status=status,
        converter_id="pandoc-gfm",
        converter_version="3.9",
        options_hash="sha256:" + h("0"),
        action_key=h("k"),
        content_sha256=h("c"),
        canonical_sha256=h("2"),
        units=tuple(units),
    )


def out_row(u: RenderedUnit, status: OutputStatus = OutputStatus.OK, sha: str | None = None) -> OutputRow:
    return OutputRow(
        output_path=f"mirror/src/{u.unit_id}.md",
        source_id="src",
        stable_id="vol:1",
        unit_id=u.unit_id,
        action_key=h("k"),
        rendered_sha256=sha or u.rendered_sha256,
        page_sha256=h("p"),
        converter_id="pandoc-gfm",
        converter_version="3.9",
        options_hash="sha256:" + h("0"),
        status=status,
        built_run=1,
    )


def test_phase3_office_resave_h1_differs_h2_equal_cuts_off() -> None:
    """The brief's Office re-save: H1 differs (so phase 2 said CHANGED) but every rendered body is equal."""
    row = row_of(local())
    phase2 = classify_content(row, CanonicalHash(h("2"), "ooxml-parts@1", (("word/document.xml", h("e")),)))
    assert phase2.verdict is Verdict.CHANGED and phase2.changed_parts == ("word/document.xml",)
    idx, s1 = unit("index", "# Budget\n"), unit("sheet:1", "| a |\n")
    assert classify_output([out_row(idx), out_row(s1)], result(s1, idx)) is Verdict.OUTPUT_UNCHANGED


@pytest.mark.parametrize(
    "name",
    [
        "body-changed",
        "unit-added",
        "unit-removed",
        "first-build",
        "not-ok",
        "prev-stub",
        "no-units",
        "dup-units",
    ],
)
def test_phase3_changed(name: str) -> None:
    idx, s1, s2 = unit("index", "# B\n"), unit("sheet:1", "a\n"), unit("sheet:2", "b\n")
    prev = [out_row(idx), out_row(s1)]
    cases = {
        "body-changed": (prev, result(idx, unit("sheet:1", "a changed\n"))),
        "unit-added": (prev, result(idx, s1, s2)),
        "unit-removed": (prev, result(idx)),
        "first-build": ([], result(idx, s1)),
        "not-ok": (prev, result(idx, s1, status=ConversionStatus.UNREADABLE)),
        "prev-stub": ([out_row(idx, OutputStatus.FAILED), out_row(s1)], result(idx, s1)),
        "no-units": (prev, result()),
        "dup-units": ([out_row(idx)], result(idx, idx)),
    }
    previous, res = cases[name]
    assert classify_output(previous, res) is Verdict.CHANGED


def test_phase3_ignores_tombstoned_units() -> None:
    idx, gone = unit("index", "# B\n"), unit("sheet:9", "old\n")
    prev = [out_row(idx), out_row(gone, OutputStatus.TOMBSTONE)]
    assert classify_output(prev, result(idx)) is Verdict.OUTPUT_UNCHANGED


# ---- safe-saves, deletions, breaker ------------------------------------------------------------------------


def test_match_safe_saves_pairs_same_path_only() -> None:
    created = [
        local("vol:9", "a/Report.docx", ino=9),
        local("vol:8", "a/New.docx", ino=8),
        local("vol:7", "a/Dir", is_dir=True),
    ]
    missing = [
        row_of(local("vol:1", "a/Report.docx")),
        row_of(local("vol:2", "a/Other.docx")),
        row_of(local("vol:3", "a/Dir", is_dir=True)),
    ]
    assert match_safe_saves(created, missing) == [("vol:9", "vol:1")]


def test_match_safe_saves_prefers_most_recent_and_skips_ties() -> None:
    new = [local("vol:9", "a/R.docx")]
    old = [
        row_of(local("vol:1", "a/R.docx"), last_seen_run=3),
        row_of(local("vol:2", "a/R.docx"), last_seen_run=5),
    ]
    assert match_safe_saves(new, old) == [("vol:9", "vol:2")]
    tie = [
        row_of(local("vol:1", "a/R.docx"), last_seen_run=5),
        row_of(local("vol:2", "a/R.docx"), last_seen_run=5),
    ]
    assert match_safe_saves(new, tie) == []
    tomb = [row_of(local("vol:1", "a/R.docx"), state=RowState.TOMBSTONE)]
    assert match_safe_saves(new, tomb) == []
    assert match_safe_saves([local("vol:9", "a/R.docx", deleted=True)], old) == []


@pytest.mark.parametrize("seed", range(4))
def test_match_safe_saves_property_one_to_one(seed: int) -> None:
    rng = random.Random(seed)
    paths = [f"d/{n}.docx" for n in range(6)]
    for _ in range(100):
        created = [local(f"new:{i}", rng.choice(paths)) for i in range(rng.randint(0, 5))]
        missing = [
            row_of(local(f"old:{i}", rng.choice(paths)), last_seen_run=rng.randint(1, 4))
            for i in range(rng.randint(0, 5))
        ]
        pairs = match_safe_saves(created, missing)
        assert pairs == sorted(pairs)
        news, olds = [p[0] for p in pairs], [p[1] for p in pairs]
        assert len(set(news)) == len(news) and len(set(olds)) == len(olds)
        path_of_new = {c.stable_id: c.rel_path for c in created}
        path_of_old = {r.stable_id: r.rel_path for r in missing}
        assert all(path_of_new[n] == path_of_old[o] for n, o in pairs)


def test_deletion_candidates_only_from_a_complete_full_pass() -> None:
    rows = [
        row_of(local("b")),
        row_of(local("a")),
        row_of(local("d", is_dir=True)),
        row_of(local("t"), state=RowState.TOMBSTONE),
    ]
    assert deletion_candidates(rows, pass_kind=PassKind.DELTA, enumeration_complete=True) == []
    assert deletion_candidates(rows, pass_kind=PassKind.FULL, enumeration_complete=False) == []
    got = deletion_candidates(rows, pass_kind=PassKind.FULL, enumeration_complete=True)
    assert [r.stable_id for r in got] == ["a", "b"]


@pytest.mark.parametrize(
    ("candidates", "live", "trips"),
    [
        (25, 40, False),
        (26, 40, True),
        (3, 40, False),
        (25, 125, False),
        (26, 125, True),
        (40, 200, False),
        (41, 200, True),
        (0, 0, False),
        (26, 0, True),
    ],
)
def test_breaker_trips_scoped_and_floored(candidates: int, live: int, trips: bool) -> None:
    assert breaker_trips(candidates, live, BreakerConfig()) is trips


def test_breaker_uses_exact_arithmetic() -> None:
    cfg = BreakerConfig(fraction=0.29, floor=0)
    assert not breaker_trips(29, 100, cfg)  # float 0.29*100 = 28.999...; exact is 29
    assert breaker_trips(30, 100, cfg)


@pytest.mark.parametrize("seed", range(4))
def test_breaker_property_matches_definition(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(500):
        cfg = BreakerConfig(fraction=rng.choice([0.0, 0.1, 0.2, 0.5]), floor=rng.randint(0, 30))
        live, cand = rng.randint(0, 400), rng.randint(0, 120)
        assert breaker_trips(cand, live, cfg) == (
            cand * 10 > max(round(cfg.fraction * 10) * live, cfg.floor * 10)
        )


# ---- classify_pass -----------------------------------------------------------------------------------------


def run_pass(
    items: list[SourceItem],
    rows: list[ItemRow],
    ctx: ClassifyContext = FULL,
    *,
    complete: bool = True,
    live: int | None = None,
    active: bool = False,
) -> PassClassification:
    by_id = {r.stable_id: r for r in rows}
    return classify_pass(
        items,
        by_id,
        rows,
        ctx,
        enumeration_complete=complete,
        live_rows=len(rows) if live is None else live,
        breaker=BreakerConfig(),
        breaker_active=active,
    )


def test_pass_office_safe_save_is_continuity_not_delete_create() -> None:
    old = local("vol:1", "a/Report.docx", ino=1)
    other = local("vol:5", "a/Keep.txt", ino=5)
    new = local("vol:2", "a/Report.docx", ino=2, mtime_ns=OLD_MTIME + 9)
    res = run_pass([new, other], [row_of(old), row_of(other)])
    assert res.safe_saves == (("vol:2", "vol:1"),)
    assert res.deletion_candidates == ()
    by_id = {c.stable_id: c for c in res.verdicts}
    assert (by_id["vol:2"].verdict, by_id["vol:2"].reason, by_id["vol:2"].replaces_id) == (
        Verdict.MAYBE_CHANGED,
        "safe-save",
        "vol:1",
    )
    assert by_id["vol:5"].verdict is Verdict.UNCHANGED
    assert not res.breaker_tripped


def test_pass_safe_save_needs_a_complete_full_pass() -> None:
    old, new = local("vol:1", "a/R.docx"), local("vol:2", "a/R.docx", ino=2)
    for ctx, complete in ((CTX, True), (FULL, False)):
        res = run_pass([new], [row_of(old)], ctx, complete=complete)
        assert res.safe_saves == () and res.deletion_candidates == ()
        assert res.verdicts[0].verdict is Verdict.CREATED


def test_pass_deletions_only_from_complete_listing_and_breaker() -> None:
    rows = [row_of(local(f"vol:{i}", f"f{i}.txt", ino=i)) for i in range(100)]
    seen = [local(f"vol:{i}", f"f{i}.txt", ino=i) for i in range(70)]
    delta = run_pass(seen, rows, CTX)
    assert delta.deletion_candidates == () and not delta.breaker_tripped
    partial = run_pass(seen, rows, FULL, complete=False)
    assert partial.deletion_candidates == ()
    full = run_pass(seen, rows, FULL)
    assert len(full.deletion_candidates) == 30 and full.breaker_tripped  # 30 > max(20, 25)
    small = run_pass(seen + [local(f"vol:{i}", f"f{i}.txt", ino=i) for i in range(70, 80)], rows, FULL)
    assert len(small.deletion_candidates) == 20 and not small.breaker_tripped
    held = run_pass(
        seen + [local(f"vol:{i}", f"f{i}.txt", ino=i) for i in range(70, 80)], rows, FULL, active=True
    )
    assert held.breaker_tripped and len(held.deletion_candidates) == 20


def test_pass_first_incremental_poll_after_bootstrap_cannot_delete() -> None:
    """Failure mode 22: a delta pass sees only changes; absence must not count."""
    rows = [row_of(local(f"vol:{i}", f"f{i}.txt", ino=i)) for i in range(50)]
    res = run_pass([local("vol:1", "f1.txt", ino=1, size=5)], rows, CTX)
    assert res.deletion_candidates == () and not res.breaker_tripped
    assert [c.verdict for c in res.verdicts] == [Verdict.MAYBE_CHANGED]


def test_pass_dedupes_repeated_ids_keeping_the_last_record() -> None:
    first = graph("01", "x.docx", remote_hashes=RemoteHashes(quickxor="q1"))
    last = graph("01", "x.docx", remote_hashes=RemoteHashes(quickxor="q2"))
    res = run_pass([first, last, graph("00", "y.docx")], [row_of(first)], CTX)
    assert [c.stable_id for c in res.verdicts] == ["00", "01"]
    assert res.verdicts[1].reason == "quickxor-differs"


def test_pass_unseen_may_already_include_observed_rows() -> None:
    a = local("vol:1", "a.txt")
    res = run_pass([a], [row_of(a)], FULL)
    assert res.deletion_candidates == ()


@pytest.mark.parametrize("seed", range(6))
def test_pass_properties(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(60):
        n = rng.randint(0, 60)
        rows = [row_of(local(f"id:{i:03}", f"p/{i % 40}.docx", ino=i)) for i in range(n)]
        observed = [local(f"id:{i:03}", f"p/{i % 40}.docx", ino=i) for i in range(n) if rng.random() < 0.7]
        observed += [
            local(f"new:{i}", f"p/{rng.randint(0, 50)}.docx", ino=1000 + i) for i in range(rng.randint(0, 5))
        ]
        ctx = rng.choice([CTX, FULL])
        complete = rng.random() < 0.7
        res = run_pass(observed, rows, ctx, complete=complete, active=rng.random() < 0.1)
        ids = [c.stable_id for c in res.verdicts]
        assert ids == sorted(set(ids)) == sorted({o.stable_id for o in observed})
        if ctx.pass_kind is PassKind.DELTA or not complete:
            assert res.deletion_candidates == () and res.safe_saves == ()
        seen = {o.stable_id for o in observed}
        replaced = {old for _, old in res.safe_saves}
        assert not (set(res.deletion_candidates) & (seen | replaced))
        assert list(res.deletion_candidates) == sorted(res.deletion_candidates)
        if res.deletion_candidates and not res.breaker_tripped:
            assert len(res.deletion_candidates) <= max(0.2 * len(rows), 25)


# ---- multi-cycle scenarios against the real manifest -------------------------------------------------------


class Harness:
    """A minimal stand-in for the cycle's phase-1 persistence + phase-2/3 bookkeeping (no publish)."""

    def __init__(self, tmp: Path) -> None:
        self.m = Manifest(tmp / "manifest.sqlite")
        self.m.sync_sources([SourceConfig(id="src", kind=SourceKind.LOCAL, path=tmp / "src")])
        self.outputs: dict[str, list[RenderedUnit]] = {}

    def scan(
        self, items: list[SourceItem], *, full: bool = True, complete: bool = True
    ) -> PassClassification:
        run = self.m.begin_run(CycleMode.RECONCILE if full else CycleMode.POLL, host="h", pid=1)
        ctx = ClassifyContext(run, PassKind.FULL if full else PassKind.DELTA, self.m.written_at_ns())
        rows = {r.stable_id: r for r in self.m.iter_items("src")}
        res = classify_pass(
            items,
            rows,
            self.m.unseen_live("src", run),
            ctx,
            enumeration_complete=complete,
            live_rows=self.m.live_count("src"),
            breaker=BreakerConfig(),
            breaker_active=self.m.breaker_active("src", "2026-09-29T00:00:00Z"),
        )
        by_id = {i.stable_id: i for i in items}
        with self.m.transaction():
            for c in res.verdicts:
                it = by_id[c.stable_id]
                row = rows.get(c.stable_id)
                if c.verdict is Verdict.DELETED:
                    if row is None:
                        continue
                    state = RowState.TOMBSTONE
                elif it.dataless and c.verdict is Verdict.DATALESS:
                    state = RowState.DATALESS
                elif row is not None and row.state in (RowState.QUARANTINED, RowState.REFUSED):
                    state = row.state
                else:
                    state = RowState.LIVE
                self.m.upsert_observed(it, run_id=run, verdict=c.verdict, state=state)
            for new_id, old_id in res.safe_saves:
                self.m.rekey("src", old_id, new_id)
            self.m.stage_cursor("src", None, run)
            if full and complete:
                self.m.set_enumeration_complete("src", True, run)
        if not res.breaker_tripped:
            for sid in res.deletion_candidates:
                self.m.set_state("src", sid, RowState.TOMBSTONE, "deleted-upstream")
                self.m.set_verdict("src", sid, Verdict.DELETED)
                # what Publisher.tombstone does to the item's outputs: the page body is now a stub
                stubs = [replace(o, status=OutputStatus.TOMBSTONE) for o in self.m.outputs_for("src", sid)]
                self.m.replace_outputs("src", sid, stubs)
        self.run = run
        return res

    def process(
        self, content: dict[str, tuple[str, list[RenderedUnit]]], budget: int = 1_000
    ) -> dict[str, Verdict]:
        """Fetch + H1 + convert + H2 for pending rows; ``content`` maps stable_id -> (H1, units)."""
        outcome: dict[str, Verdict] = {}
        for row in self.m.pending_work("src"):
            if budget <= 0:
                self.m.set_verdict("src", row.stable_id, Verdict.DEFERRED)
                outcome[row.stable_id] = Verdict.DEFERRED
                continue
            budget -= 1
            h1, units = content[row.stable_id]
            phase2 = classify_content(row, CanonicalHash(h1, "ooxml-parts@1"))
            self.m.set_content(
                "src",
                row.stable_id,
                content_sha256=h1,
                canonical_sha256=h1,
                canonical_method="ooxml-parts@1",
                canonical_parts=[],
            )
            final = phase2.verdict
            if final is Verdict.CHANGED:
                prev = self.m.outputs_for("src", row.stable_id)
                final = classify_output(prev, result(*units))
                self.m.replace_outputs(
                    "src",
                    row.stable_id,
                    [
                        OutputRow(
                            f"mirror/src/{row.stable_id}/{u.unit_id}.md",
                            "src",
                            row.stable_id,
                            u.unit_id,
                            None,
                            u.rendered_sha256,
                            None,
                            None,
                            None,
                            None,
                            OutputStatus.OK,
                            self.run,
                        )
                        for u in units
                    ],
                )
            # published: settle the row so it leaves pending_work
            self.m.set_verdict("src", row.stable_id, Verdict.UNCHANGED if final is Verdict.CHANGED else final)
            outcome[row.stable_id] = final
        self.m.finish_run(self.run, status="ok", commit_sha=None, counts={})
        return outcome


@pytest.fixture
def hx(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def old_local(stable_id: str, rel: str, ino: int, **kw: object) -> SourceItem:
    return local(stable_id, rel, ino=ino, **{"mtime_ns": 1, "ctime_ns": 1, **kw})


def test_scenario_create_noop_edit_office_resave(hx: Harness) -> None:
    doc = old_local("vol:1", "a/Plan.docx", 1)
    body = [unit("whole", "# Plan\n")]
    assert hx.scan([doc]).verdicts[0].verdict is Verdict.CREATED
    assert hx.process({"vol:1": (h("1"), body)}) == {"vol:1": Verdict.CHANGED}
    # no-op cycle: zero bytes read
    res = hx.scan([doc])
    assert res.verdicts[0].verdict is Verdict.UNCHANGED
    assert hx.process({}) == {}
    # Office no-op save in place: stat moves, H1 equal -> TOUCHED_NOT_CHANGED, no conversion
    touched = replace(doc, mtime_ns=2, ctime_ns=2, gen_count=4)
    assert hx.scan([touched]).verdicts[0].verdict is Verdict.MAYBE_CHANGED
    assert hx.process({"vol:1": (h("1"), body)}) == {"vol:1": Verdict.TOUCHED_NOT_CHANGED}
    # Office re-save with different container bytes: H1 differs, H2 equal -> OUTPUT_UNCHANGED
    resaved = replace(touched, mtime_ns=3, ctime_ns=3, gen_count=5, size=101)
    assert hx.scan([resaved]).verdicts[0].verdict is Verdict.MAYBE_CHANGED
    assert hx.process({"vol:1": (h("2"), body)}) == {"vol:1": Verdict.OUTPUT_UNCHANGED}
    # a real edit
    edited = replace(resaved, mtime_ns=4, ctime_ns=4, gen_count=6)
    hx.scan([edited])
    assert hx.process({"vol:1": (h("3"), [unit("whole", "# Plan v2\n")])}) == {"vol:1": Verdict.CHANGED}
    assert hx.m.pending_work("src") == []


def test_scenario_office_safe_save_new_inode_keeps_outputs(hx: Harness) -> None:
    doc = old_local("vol:1", "a/Budget.xlsx", 1)
    units = [unit("index", "# Budget\n"), unit("sheet:1", "| q3 |\n")]
    hx.scan([doc])
    hx.process({"vol:1": (h("1"), units)})
    safe_saved = old_local("vol:2", "a/Budget.xlsx", 2, mtime_ns=5)
    res = hx.scan([safe_saved])
    assert res.safe_saves == (("vol:2", "vol:1"),) and res.deletion_candidates == ()
    assert hx.m.get_item("src", "vol:1") is None
    assert [o.unit_id for o in hx.m.outputs_for("src", "vol:2")] == ["index", "sheet:1"]
    assert hx.process({"vol:2": (h("1"), units)}) == {"vol:2": Verdict.TOUCHED_NOT_CHANGED}
    row = hx.m.get_item("src", "vol:2")
    assert row is not None and row.first_seen_run == 1 and row.state is RowState.LIVE


def test_scenario_rename_by_stable_id_reads_zero_bytes(hx: Harness) -> None:
    doc = graph("01A", "Finance/Budget.xlsx")
    hx.scan([doc])
    hx.process({"01A": (h("1"), [unit("whole", "x\n")])})
    moved = replace(doc, rel_path="Archive/2026/Budget.xlsx", parent_id="arc", etag='"{E},2"')
    res = hx.scan([moved], full=False)
    c = res.verdicts[0]
    assert (c.verdict, c.prev_path) == (Verdict.METADATA_ONLY, "Finance/Budget.xlsx")
    assert hx.process({}) == {}  # nothing to fetch
    row = hx.m.get_item("src", "01A")
    assert (
        row is not None
        and row.rel_path == "Archive/2026/Budget.xlsx"
        and row.prev_path == "Finance/Budget.xlsx"
    )
    assert [o.stable_id for o in hx.m.outputs_for("src", "01A")] == ["01A"]


def test_scenario_deletion_needs_a_complete_listing_then_tombstones_and_restores(hx: Harness) -> None:
    files = [old_local(f"vol:{i}", f"f{i}.txt", i) for i in range(30)]
    hx.scan(files)
    hx.process({f.stable_id: (h("1"), [unit("whole", f.stable_id)]) for f in files})
    survivors = files[1:]
    hx.scan(survivors, full=False)  # delta pass: absence is not evidence
    hx.process({})
    hx.scan(survivors, full=True, complete=False)  # interrupted walk: still not evidence
    hx.process({})
    row = hx.m.get_item("src", "vol:0")
    assert row is not None and row.state is RowState.LIVE
    res = hx.scan(survivors)  # complete FULL pass
    hx.process({})
    assert res.deletion_candidates == ("vol:0",) and not res.breaker_tripped
    row = hx.m.get_item("src", "vol:0")
    assert row is not None and row.state is RowState.TOMBSTONE
    # the same id reappears (restored from the recycle bin): re-read and rebuilt
    res = hx.scan(files)
    assert {c.stable_id: c.reason for c in res.verdicts}["vol:0"] == "reappeared"
    assert hx.process({"vol:0": (h("1"), [unit("whole", "vol:0")])}) == {"vol:0": Verdict.CHANGED}


def test_scenario_explicit_delta_tombstone(hx: Harness) -> None:
    doc = graph("01A", "x.docx")
    hx.scan([doc])
    hx.process({"01A": (h("1"), [unit("whole", "x")])})
    res = hx.scan([replace(doc, deleted=True, rel_path="", size=0)], full=False)
    assert res.verdicts[0].verdict is Verdict.DELETED
    row = hx.m.get_item("src", "01A")
    assert row is not None and row.state is RowState.TOMBSTONE and row.rel_path == "x.docx"
    res = hx.scan([replace(doc, deleted=True)], full=False)
    assert res.verdicts[0].reason == "already-tombstoned"


def test_scenario_mass_absence_trips_breaker_and_keeps_everything(hx: Harness) -> None:
    files = [old_local(f"vol:{i:03}", f"f{i}.txt", i) for i in range(200)]
    hx.scan(files)
    hx.process({f.stable_id: (h("1"), [unit("whole", f.stable_id)]) for f in files})
    res = hx.scan(files[:100])  # an unmounted half looks like a mass delete
    hx.process({})
    assert res.breaker_tripped and len(res.deletion_candidates) == 100
    assert hx.m.live_count("src") == 200


def test_scenario_budget_deferral_is_durable_across_cycles(hx: Harness) -> None:
    files = [old_local(f"vol:{i}", f"f{i}.txt", i) for i in range(3)]
    hx.scan(files)
    first = hx.process({f.stable_id: (h("1"), [unit("whole", f.stable_id)]) for f in files}, budget=1)
    assert sorted(first.values()) == sorted([Verdict.CHANGED, Verdict.DEFERRED, Verdict.DEFERRED])
    res = hx.scan(files, full=False)  # nothing moved on disk; the deferred rows must not be forgotten
    assert (
        sorted(c.reason for c in res.verdicts if c.reason.startswith("pending")) == ["pending:deferred"] * 2
    )
    second = hx.process({f.stable_id: (h("1"), [unit("whole", f.stable_id)]) for f in files})
    assert set(second.values()) == {Verdict.CHANGED}
    assert hx.m.pending_work("src") == []


def test_scenario_dataless_change_is_seen_at_hydration(hx: Harness) -> None:
    doc = old_local("vol:1", "a/Notes.docx", 1, gen_count=3)
    hx.scan([doc])
    hx.process({"vol:1": (h("1"), [unit("whole", "v1")])})
    # evicted, and meanwhile edited elsewhere: the placeholder carries the new stat
    placeholder = replace(doc, dataless=True, size=222, mtime_ns=8, gen_count=9)
    assert hx.scan([placeholder]).verdicts[0].verdict is Verdict.DATALESS
    assert hx.process({}) == {}
    hydrated = replace(placeholder, dataless=False)
    res = hx.scan([hydrated])
    assert (res.verdicts[0].verdict, res.verdicts[0].reason) == (Verdict.MAYBE_CHANGED, "stat-differs:size")
    assert hx.process({"vol:1": (h("2"), [unit("whole", "v2")])}) == {"vol:1": Verdict.CHANGED}


def test_scenario_never_materialised_dataless_is_pending_work(hx: Harness) -> None:
    ghost = old_local("vol:1", "a/Cloud.pdf", 1, dataless=True)
    assert hx.scan([ghost]).verdicts[0].verdict is Verdict.CREATED
    assert [r.stable_id for r in hx.m.pending_work("src")] == ["vol:1"]
    res = hx.scan([ghost])  # the cycle died before phase 2: the CREATED row is carried, not dropped
    assert (res.verdicts[0].verdict, res.verdicts[0].reason) == (Verdict.CREATED, "pending:created")
    hx.process({}, budget=0)
    res = hx.scan([ghost])  # budget deferred it: still pending, never silenced to DATALESS
    assert (res.verdicts[0].verdict, res.verdicts[0].reason) == (Verdict.MAYBE_CHANGED, "pending:deferred")
    assert [r.stable_id for r in hx.m.pending_work("src")] == ["vol:1"]
    assert hx.process({"vol:1": (h("1"), [unit("whole", "pdf")])}) == {"vol:1": Verdict.CHANGED}
    assert hx.m.pending_work("src") == []
