"""One sync cycle, in the durability order of design 4.7 (owner: integrator).

Order: fail_closed -> lock -> open manifest -> recover -> sync_sources -> per live source: scan (pending
cursor) -> classify phase 1 -> persist observations + pending cursor (one transaction) -> materialise within
budget -> H1 -> convert via cache -> H2 cutoff -> plan/write pages -> tombstones (unless breaker) -> curate
(DEPENDS, banners) -> INDEX/CHANGELOG/QUARANTINE/shards -> land-gate lints -> ONE git commit if anything
changed -> tree_sha -> promote cursors -> heartbeat -> STATE.md -> release lock.

Invariants this module keeps (tests/test_cycle.py, tests/test_e2e.py):

- A cursor is promoted only after the git commit of the cycle that staged it (or, with nothing to commit,
  after every change it covered is either published or durable pending work in the manifest).  A failed
  source, a blocking lint or any cycle-level error discards the pending cursors of this run.
- A failure in one source is recorded on that source (report + ``run_sources`` + heartbeat); other sources
  continue, and pages already written for the failed source are consistent with the manifest.
- Row verdicts after publishing are settled (UNCHANGED / TOUCHED_NOT_CHANGED / OUTPUT_UNCHANGED /
  QUARANTINED / REFUSED); a row still pending (CREATED/MAYBE_CHANGED/CHANGED/DEFERRED/ERROR) is retried.
- An item whose pages are missing or do not hash to their ``outputs`` row is always re-published (this is
  also how ``recover`` re-publishes after a crash or a moved HEAD).
"""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import gc
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import socket
import stat
import struct
import threading
import time
import unicodedata
import zlib
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from agentsync import __version__, curate, gitops, governance, lints, materialise, net, skill
from agentsync import policy as content_policy
from agentsync.arm_local import (
    InboxArm,
    LocalArm,
    SettleBudget,
    WalkStats,
    cloud_provider_root,
    fold_conflict_suffix,
)
from agentsync.classifier import ClassifyContext, PassClassification, classify_content, classify_output
from agentsync.classifier import classify_pass as _classify_pass
from agentsync.config import BreakerConfig, Config, SourceConfig, canonical_source_root
from agentsync.convert import NO_CONVERTER_PREFIX, convert_file, media, ocr
from agentsync.convert import recording as recording_mod
from agentsync.convert.base import Converter
from agentsync.convert.cache import ConverterCache
from agentsync.convert.canonical import canonical_hash
from agentsync.convert.image import ImageConverter
from agentsync.convert.media import MediaEngine
from agentsync.convert.ocr import OcrEngine, OcrError, OcrImage
from agentsync.convert.pieces import PieceStore
from agentsync.convert.recording import RecordingConverter, RecordingNotFinished
from agentsync.convert.registry import SIDECAR_DIGEST_PREFIX, Registry, sidecar_digest_lines
from agentsync.errors import (
    AgentSyncError,
    AuthRequiredError,
    BudgetExhaustedError,
    ConfigError,
    DatalessRefusedError,
    GraphThrottled,
    LockHeldError,
    ProviderTimeoutError,
    PublishError,
    SidecarPathError,
)
from agentsync.frontmatter import FrontmatterError, parse_frontmatter
from agentsync.graph.auth import MsalAuth, settings_from_config
from agentsync.graph.client import GraphClient, user_agent
from agentsync.graph.drive import DriveArm
from agentsync.graph.errors import AuthBlockedError
from agentsync.graph.mail import MailArm
from agentsync.graph.teams import TeamsArm
from agentsync.manifest import (
    ItemRow,
    Manifest,
    OutputRow,
    cursor_fingerprint,
    migration_backups,
    redacted_path,
)
from agentsync.model import (
    ByteBudget,
    CanonicalHash,
    ChangeOp,
    ConversionResult,
    ConversionStatus,
    CycleMode,
    CycleReport,
    FetchResult,
    LintFinding,
    MirrorChange,
    OutputStatus,
    PassKind,
    RemoteHashes,
    RowState,
    ScanResult,
    SourceArm,
    SourceItem,
    SourceKind,
    SourceReport,
    SourceState,
    Verdict,
)
from agentsync.ops.launchd import rotate_logs
from agentsync.ops.lock import LockAcquisition, LockInfo, SingleWriterLock, read_heartbeat, write_heartbeat
from agentsync.paths import expand
from agentsync.policy import PolicyConfig
from agentsync.publish import (
    DELETED_UPSTREAM,
    Publisher,
    SourceStatus,
    archive_path,
    render_tombstone,
    sidecar_rel,
)

log = logging.getLogger(__name__)

_RUN_TRAILER = re.compile(r"^Agentsync-Run:\s*(\d+)\s*$", re.MULTILINE)
_SETTLED_PUBLISHED = Verdict.UNCHANGED  # a published row is in sync; CHANGED would be pending work again
_PRESENT = (RowState.LIVE, RowState.DATALESS, RowState.QUARANTINED, RowState.REFUSED)
_NEVER_TRIPS = BreakerConfig(fraction=1.0, floor=10**12, hold_days=0)
_WORK_BATCH = 256  # work-queue rows per manifest transaction (one commit + fsync per batch)
_LABEL_CAPABLE = (*content_policy.OOXML_SUFFIXES, ".pdf", ".eml", *ImageConverter.extensions)
"""Names whose content can carry a sensitivity label: re-screened when the effective [policy] changes. An
image's label cannot be read, so under a label rule an image is not converted at all (``Registry.default``);
the re-screen is what turns an image page published before the rule into the ``no converter`` stub."""
_OCR_BUDGET_S = 180.0
"""The seconds of on-device OCR one cycle may use before it starts no more: the images still waiting are
deferred like files past ``max_files``, so a folder of thousands of screenshots is read over many cycles and
never holds one cycle (or the installer's first sync) for an hour. The read that passes the budget finishes;
a page takes about 2 to 6 s, a 49-megapixel one about 26 s (``convert/ocr.py``). A document does not wait:
past the budget it is converted without OCR (``_Cycle._converting``)."""
_NO_CONVERTERS = Registry([])
_PAGE_CAP_MARK = "over the OCR page limit"
_PICTURE_CAP_MARK = "past the OCR picture limit"
"""What a converter's summary or stub reason says, in fixed wording, when a count limit of OCR left pages
or pictures of a document unread (``convert/pdf.py``, ``convert/image.py``).  The run record counts the
conversions that say so (``_Cycle._tally_ocr``)."""
_ocr_clock = time.monotonic
_OCR_CANARY = ".agentsync-ocr-canary.png"
"""The name, in the folder a read ran in, of the blank image the helper is handed after a read that failed
(``_CycleOcr``): it tells a file that sinks the helper from a helper that fails on everything."""
_OCR_CANARY_S = 30.0
_OCR_DOWN = (
    "on-device OCR stopped working in this sync (the helper fails on a blank image); files are converted "
    "without it and read again once it works: run scripts/install.sh again"
)
_REREAD_META = "reread:"
"""``reread:<source_id>``, for a local or inbox source: what the cycle looked for among the source's pages
and how far it got.  A JSON list of at most two records ``{"done": bool, "for": <sha256>, "tried": [<stable
id>, ...]}``: the one the last cycle wrote, then the last one written for another ``for``.  A record can
also hold ``"failed": {<stable id>: <count>}`` and ``"reading": <stable id>``.

``for`` is ``_Cycle._capabilities``: this agentsync's version, the suffixes that have a converter, the OCR
engine's identity and each converter's ``outdated_key``.  ``failed``: the files whose re-read failed, with
the number of cycles it failed in.  ``tried``: those it failed in ``_REREAD_ATTEMPTS`` cycles; they are not
read again for the same ``for``, and the second record keeps that true when an engine goes and comes back.
``reading``: the file being read when the record was stored, cleared when its read ends; a cycle that finds
one was killed in that read, and counts it as a failed one.  ``done`` counts in the first record only: no
file on this Mac was left to read again, so no cycle looks until one leaves such a file."""
_REREAD_BATCH = 20  # files asked for at a time, and read between two lock beats
_REREAD_ATTEMPTS = 2
"""The cycles in which one file's re-read may fail before the file is given up for what this install has.
One would make every passing fault final (a full disk, pandoc timing out under load); none would read a
file that cannot be converted in every cycle for ever."""
_REREAD_STREAK = 3
"""Re-reads that failed in a row, after which a cycle starts no more: what fails three files running is
more likely the Mac than the files, and each of them has used one of its attempts."""
_REREAD_BUDGET_S = 120.0
"""The seconds one cycle may spend reading files again before it starts no more (the read that passes it
finishes).  The rest wait for later cycles.  Reading again also stops once the cycle's OCR time is used up
(``_OCR_BUDGET_S``): past it a file would be converted without OCR, which is what it was read again for."""
_reread_clock = time.monotonic
_recording_clock = time.monotonic  # the staging of a recording, charged to the recording allowance
_POLICY_META = "policy_fingerprint"
_SCOPE_CHANGE_META = "scope_change:"
_SCOPE_CHANGE_REASON = "retired:scope-change"
_SCOPE_ROOT_META = "scope_root:"
"""``scope_root:<source_id>`` holds ``<run>:<path>`` for a local or inbox source: the first run under the
``path`` it has now (0: the path it had when this was first recorded)."""
_RESCREEN_META = "policy_rescreen_pending"
_CHECKPOINT_PENDING_META = "checkpoint_pending"  # KISS K06: the HEAD a held or failed checkpoint retries
_SEED_PAGE_NAMES = frozenset({"CLAUDE.md", "INDEX.md"})  # under topics/: scaffold files, never curated pages
_EMPTY_DIRS_META = "empty_cloud_dirs:"
"""Manifest meta ``empty_cloud_dirs:<source id>``: the zero-child cloud folders the source's last walk found,
a JSON list of paths relative to its root ("" when it found none, or could not walk). ``loop.next_step``
names them from here, so status and STATE.md never walk a source to explain why its listing is incomplete."""
HYDRATION_REFUSED = "hydration-refused"
"""``state_reason`` of a live/dataless row whose download the OS refused (EDEADLK) on its last attempt: a
later sync's budget never clears it, so ``loop`` makes it an operator wait. Cleared when the row is next
processed."""
RECORDING_WAITS = "recording-waits"
"""``state_reason`` of a local recording the recording pass has not finished reading: it waits for a
background sync or ``agentsync materialise <file>`` (spec S0 rule 6, ruling 4).  ``loop`` counts it in a
bucket of its own with the minutes read so far; it is never rule 3.  Cleared when its page is published."""
RECORDING_PROGRESS_META = "recording_progress:"
"""Manifest meta ``recording_progress:<source id>:<stable id>``: ``"<done_ms> <total_ms>"`` of a recording
that waits with part of it read (``RecordingNotFinished``), ``""`` once it is published or given up.  ``loop``
reads it for the "N of M minutes" note without running anything."""
_RECORDING_BUDGET_S = 180.0
"""The seconds of recording work one background cycle may spend (spec S0 rule 5): pieces of one recording at a
time, the piece in flight finishing.  Kept apart from the cycle's OCR time (``_OCR_BUDGET_S``), so the images
beside a recording keep theirs; recordings are read after every source, so nothing waits behind them."""
_RECORDING_META = "recording:"
"""``recording:<source_id>``: what the recording pass knows of the source's recordings across cycles.  A JSON
object of ``reading`` (the stable id being read when it was stored, cleared when the read ends: a cycle
that finds one was killed in that read, and counts it as a failed one), ``failed`` ({stable id: the cycles a
read of it was killed, timed out or failed in at one piece}), ``failed_at`` ({stable id: the media time read
before that piece, ms}), ``started`` ({stable id: the run that stored its first piece}, which orders the
recordings that have pieces), ``staged`` ({stable id: the canonical hash its unfinished read was staged
under, committed before the read, so its pieces are found when the row holds no hash or another one}) and
``fetch`` ({stable id: [failed downloads, the stat they failed at]})."""
_NEW_BYTES = frozenset({Verdict.CREATED, Verdict.CHANGED, Verdict.MAYBE_CHANGED})
"""The verdicts of a row whose bytes may not be the ones its pages were made from."""
_RECORDING_STOPPED = (
    "reading this recording stopped or failed in two syncs; run agentsync materialise on it from a terminal "
    "to read it"
)
"""The stub of a recording whose read was killed, timed out or failed in two cycles (spec O13).  Fixed
wording: it names no file.  Its stored pieces are kept, so ``agentsync materialise PATH`` resumes from
them."""
_MEDIA_DOWN = (
    "the media helper stopped working in this sync (it does not answer --version); recordings wait and are "
    "read once it works: run scripts/install.sh again"
)
_RECORDING_FETCHES = 1
"""Recordings downloaded per cycle (spec S0 rule 3, ruling 1): newest modification time first, only in the
recording pass of a reconcile job or ``agentsync materialise PATH``, so no document is held behind one."""
_RECORDING_MAX_BYTES = 4 * 1024**3
"""A larger online-only recording is not downloaded (about 9 hours at 466.6 MB an hour); it is counted with
the ones the OS refused (``HYDRATION_REFUSED``)."""
_RECORDING_SPARE_BYTES = 5 * 1024**3
"""A download starts only while the volume has twice the recording's size and this much more free: staging
copies the file once more (spec S0 rule 9)."""
_RECORDING_DEADLINE_S = 60.0
_RECORDING_FLOOR_BPS = 500_000
"""A download's deadline is ``_RECORDING_DEADLINE_S + size / _RECORDING_FLOOR_BPS`` seconds (a 4 Mbit/s
floor): past it the download is given up as a refusal.  The reconcile job's watchdog is far longer."""
_RECORDING_NOT_DOWNLOADED = (
    "{path}: an online-only recording could not be downloaded; in Finder choose Always Keep on This Device"
)
_TEXT_SIDECARS = (".txt", ".md", ".csv")
"""The sidecars the secret scan reads (spec S10 rule 2): a keyframe's bytes are never scanned as text."""


class RecoveryAction(enum.StrEnum):
    """What ``recover`` found and did before any source was touched."""

    NONE = "none"
    RESET_GENERATED = "reset_generated"  # tree_sha != HEAD^{tree}: died between publish and commit
    ADOPT_PENDING = "adopt_pending"  # commit landed but cursors not promoted: promote pending
    DISCARD_PENDING = "discard_pending"  # died before publish: pending cursors discarded


# ---------------------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------------------


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".agentsync-{secrets.token_hex(6)}.tmp"
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _one_line(text: str, limit: int = 300) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _scope_change_alarm(count: int) -> str:
    return (
        f"scope changed in sources.toml: {count} file(s) now outside it retired "
        "(not deleted upstream; no purge queued)"
    )


def _download_cost(src: SourceConfig, row: ItemRow) -> int:
    """The bytes fetching ``row`` costs the per-cycle materialise budget: a download only. A Graph item is
    always a download; a local or inbox file only when the manifest saw it dataless (online-only). An
    already-local file costs 0, so ``sync --materialise-budget 0`` still converts every local file
    (``materialise.materialise`` charges again from its own lstat: a file evicted since the scan is caught).
    """
    size = max(row.size or 0, 0)
    return size if src.kind.is_graph or row.dataless else 0


def _item_from_row(row: ItemRow) -> SourceItem:
    """Rebuild the SourceItem an arm's ``fetch`` needs from the manifest row (its latest observation)."""
    return SourceItem(
        source_id=row.source_id,
        stable_id=row.stable_id,
        rel_path=row.rel_path,
        name=row.name,
        size=row.size or 0,
        mtime_ns=row.mtime_ns or 0,
        ctime_ns=row.ctime_ns or 0,
        is_dir=row.is_dir,
        dataless=row.dataless,
        gen_count=row.gen_count,
        remote_hashes=RemoteHashes(quickxor=row.quickxor, sha256=row.sha256_remote, sha1=row.sha1_remote),
        etag=row.etag,
        ctag=row.ctag,
        parent_id=row.parent_id,
        created_ns=row.created_ns,
        ino=row.ino,
        mode=row.mode,
        content_type=row.content_type,
        extra=dict(row.extra),
    )


def _outdated_rule(conv: Converter) -> Callable[[str, str | None], bool] | None:
    """A converter's own answer to "is what version X made of a file worth reading the file again for?":
    its ``outdated(produced, reason)``, looked for behind the registry's guard.  None for a converter that
    has none, which is one that never asks for a re-read."""
    rule = getattr(getattr(conv, "inner", conv), "outdated", None)
    return rule if callable(rule) else None


def _replaced(conv: Converter) -> tuple[str, ...]:
    """The converter ids whose pages ``conv`` makes anew (its ``replaces``, looked for behind the registry's
    guard): a page one of them made of a file ``conv`` now claims is read again once."""
    replaces = getattr(getattr(conv, "inner", conv), "replaces", ())
    return tuple(replaces) if isinstance(replaces, tuple) else ()


@dataclass(slots=True)
class _Rereads:
    """One source's re-read record for what this cycle looks for, in memory (``_REREAD_META``)."""

    done: bool = False
    failed: dict[str, int] = field(default_factory=dict)  # stable id -> cycles its re-read failed in

    def tried(self) -> set[str]:
        """The files given up: their re-read failed in ``_REREAD_ATTEMPTS`` cycles."""
        return {stable for stable, count in self.failed.items() if count >= _REREAD_ATTEMPTS}


_RereadRecord = tuple[str, bool, dict[str, int], str | None]  # for, done, failed, reading
_REREAD_FOR_DIGITS = 8


def _reread_number(wanted: str) -> int:
    """The first ``_REREAD_FOR_DIGITS`` hex digits of a ``for`` digest as an integer: all a run record
    holds of it (``reread_for``), since a run record holds integers only.  Enough to tell two digests apart,
    and it names nothing."""
    return int(wanted[:_REREAD_FOR_DIGITS], 16)


def _reread_records(stored: str | None) -> list[_RereadRecord]:
    """The (for, done, failed, reading) records of a ``_REREAD_META`` value, newest first; none for a value
    that is not there or cannot be read.  ``failed`` holds the ``tried`` ids too, at ``_REREAD_ATTEMPTS``."""
    try:
        doc = json.loads(stored) if stored else None
    except ValueError:
        doc = None
    records: list[_RereadRecord] = []
    for record in doc if isinstance(doc, list) else ():
        if not isinstance(record, dict) or not isinstance(record.get("for"), str):
            continue
        failed: dict[str, int] = {}
        counts, tried, reading = record.get("failed"), record.get("tried"), record.get("reading")
        for stable, count in counts.items() if isinstance(counts, dict) else ():
            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                failed[str(stable)] = count
        for stable in tried if isinstance(tried, list) else ():
            if isinstance(stable, str):
                failed[stable] = max(failed.get(stable, 0), _REREAD_ATTEMPTS)
        records.append(
            (record["for"], record.get("done") is True, failed, reading if isinstance(reading, str) else None)
        )
    return records


def _reread_value(records: Sequence[_RereadRecord]) -> str:
    """The ``_REREAD_META`` value of ``records``.  ``failed`` and ``reading`` are written only when they
    hold something, so a source nothing failed in stores what it always did."""
    out: list[dict[str, object]] = []
    for wanted, done, failed, reading in records:
        doc: dict[str, object] = {
            "done": done,
            "for": wanted,
            "tried": sorted(stable for stable, count in failed.items() if count >= _REREAD_ATTEMPTS),
        }
        once = {stable: count for stable, count in sorted(failed.items()) if count < _REREAD_ATTEMPTS}
        if once:
            doc["failed"] = once
        if reading is not None:
            doc["reading"] = reading
        out.append(doc)
    return json.dumps(out, sort_keys=True)


@dataclass(slots=True)
class _Recordings:
    """One source's recording record (``_RECORDING_META``), in memory."""

    reading: str | None = None
    failed: dict[str, int] = field(default_factory=dict)
    failed_at: dict[str, int] = field(default_factory=dict)
    started: dict[str, int] = field(default_factory=dict)
    staged: dict[str, str] = field(default_factory=dict)
    fetch: dict[str, tuple[int, str]] = field(default_factory=dict)

    @classmethod
    def parse(cls, stored: str | None) -> _Recordings:
        """The record of a ``_RECORDING_META`` value; an empty one for a value that is not there or cannot be
        read."""
        try:
            doc = json.loads(stored) if stored else None
        except ValueError:
            doc = None
        record = cls()
        if not isinstance(doc, dict):
            return record
        reading = doc.get("reading")
        record.reading = reading if isinstance(reading, str) else None
        for name, target in (
            ("failed", record.failed),
            ("failed_at", record.failed_at),
            ("started", record.started),
        ):
            values = doc.get(name)
            for stable, count in values.items() if isinstance(values, dict) else ():
                if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                    target[str(stable)] = count
        staged = doc.get("staged")
        for stable, sha in staged.items() if isinstance(staged, dict) else ():
            if isinstance(sha, str) and sha:
                record.staged[str(stable)] = sha
        fetch = doc.get("fetch")
        for stable, entry in fetch.items() if isinstance(fetch, dict) else ():
            if (
                isinstance(entry, list)
                and len(entry) == 2
                and isinstance(entry[0], int)
                and isinstance(entry[1], str)
            ):
                record.fetch[str(stable)] = (entry[0], entry[1])
        return record

    def value(self) -> str:
        """The ``_RECORDING_META`` value; ``""`` for a record that holds nothing."""
        doc: dict[str, object] = {}
        if self.reading is not None:
            doc["reading"] = self.reading
        for name, values in (
            ("failed", self.failed),
            ("failed_at", self.failed_at),
            ("started", self.started),
            ("staged", self.staged),
        ):
            if values:
                doc[name] = dict(sorted(values.items()))
        if self.fetch:
            doc["fetch"] = {stable: list(entry) for stable, entry in sorted(self.fetch.items())}
        return json.dumps(doc, sort_keys=True) if doc else ""

    def fail(self, stable: str, at: int) -> int:
        """Count one failed read of ``stable`` at the piece after ``at`` ms of media time; a failure at
        another piece starts the count again (spec 4.1: two failures of the same piece).  The count."""
        same = stable in self.failed and self.failed_at.get(stable) == at
        self.failed[stable] = self.failed[stable] + 1 if same else 1
        self.failed_at[stable] = at
        return self.failed[stable]

    def forget(self, stable: str) -> None:
        """Clear the failure count of ``stable`` and its place in the reading order."""
        for values in (self.failed, self.failed_at, self.started):
            values.pop(stable, None)


_RecordingRun = Literal["none", "background", "named"]
"""Who reads recordings in a cycle (spec S0 rule 5): ``background``, a LaunchAgent's poll or reconcile job,
under ``_RECORDING_BUDGET_S``; ``named``, ``agentsync materialise PATH``, every recording it names to the end;
``none``, an interactive ``sync``, the operator verbs ``reconcile`` and ``accept-deletions`` (a tool's timeout
may end any of them) or a dry run."""


class _HelperDown(Exception):  # noqa: N818 - a signal: the media or OCR helper stopped working
    """Raised out of ``_after_fetch`` in the recording pass when a recording's read failed because a helper
    stopped working, before anything is published: the row waits as it was and the read is not counted."""


_STUB_STATES = {
    ConversionStatus.UNREADABLE: (RowState.QUARANTINED, OutputStatus.QUARANTINED),
    ConversionStatus.REFUSED: (RowState.REFUSED, OutputStatus.REFUSED),
}


def _no_converter_stub(row: ItemRow) -> bool:
    """True when ``row`` has the stub of a file no converter claimed.  It was made from the name alone, so
    the row may hold no hash of the file's bytes yet."""
    return row.state is RowState.REFUSED and (row.state_reason or "").startswith(NO_CONVERTER_PREFIX)


def _same_stub(outs: Sequence[OutputRow], row: ItemRow, result: ConversionResult) -> OutputStatus | None:
    """The status of the stub ``row`` already has when ``result`` says what it says (one page, the same
    reason), else None: the unreadable stub, or the ``no converter`` refusal an image gets once more when
    the engine failed on it again."""
    state, status = _STUB_STATES.get(result.status, (None, None))
    if result.status is ConversionStatus.REFUSED and not _no_converter_stub(row):
        return None
    live = [o for o in outs if o.status is not OutputStatus.TOMBSTONE]
    same = (
        status is not None
        and row.state is state
        and row.state_reason == _one_line(result.reason or result.status.value, 200)
        and len(live) == 1
        and live[0].status is status
    )
    return status if same else None


def _head_run_id(repo: Path) -> int | None:
    """The ``Agentsync-Run`` trailer of HEAD's commit message (None if HEAD is not an agentsync commit)."""
    try:
        if gitops.head_sha(repo) is None:
            return None
        message = gitops.run_git(repo, "log", "-1", "--format=%B", "HEAD").stdout
    except AgentSyncError:
        return None
    m = _RUN_TRAILER.search(message)
    return int(m.group(1)) if m else None


def _pages_intact(repo: Path, outputs: Sequence[OutputRow]) -> bool:
    """True when the item has live pages and every one exists and hashes to its ``outputs`` row."""
    live = [o for o in outputs if o.status is not OutputStatus.TOMBSTONE]
    if not live:
        return False
    for out in live:
        path = repo / out.output_path
        if path.is_symlink() or not path.is_file():
            return False
        try:
            data = path.read_bytes()
        except OSError:
            return False
        if out.page_sha256 is not None and hashlib.sha256(data).hexdigest() != out.page_sha256:
            return False
        if _SIDECAR_MARK in data and not _sidecars_intact(repo, out.output_path, data):
            return False
    return True


_SIDECAR_MARK = f"\n{SIDECAR_DIGEST_PREFIX}`".encode()


def _sidecars_intact(repo: Path, page_path: str, data: bytes) -> bool:
    """True when every sidecar the page body lists exists and hashes to the listed sha256."""
    for name, digest in sidecar_digest_lines(data.decode("utf-8", errors="replace")):
        side = repo / sidecar_rel(page_path, name)
        if side.is_symlink() or not side.is_file() or _sha256_file(side) != digest:
            return False
    return True


def _page_provenance_matches(
    repo: Path, item: ItemRow, outputs: Sequence[OutputRow], graph: bool, durable_id: str | None = None
) -> bool:
    """True when every live page's frontmatter still names the item's current path (and eTag for Graph) and,
    when ``durable_id`` is given, the item's durable key (``Manifest.durable_id``) as its ``stable_id``."""
    for out in outputs:
        if out.status is OutputStatus.TOMBSTONE:
            continue
        try:
            data, _ = parse_frontmatter((repo / out.output_path).read_text(encoding="utf-8"))
        except (OSError, FrontmatterError, UnicodeDecodeError):
            return False
        if data.get("source_path") not in (item.rel_path, redacted_path(item.rel_path)):
            return False
        if durable_id is not None and data.get("stable_id") != durable_id:
            return False
        if graph and item.etag is not None and data.get("source_etag") not in (None, item.etag):
            return False
    return True


@contextlib.contextmanager
def _gc_paused() -> Iterator[None]:
    """Pause the cyclic garbage collector for one allocation-heavy, cycle-free phase.

    A pass allocates several objects per file (the walk's items, the observation index, verdicts) and keeps
    them alive until the pass ends, so the generational collector's full collections re-traverse an ever
    larger live set: measured at 20k files, about 0.3 s of a no-op pass went to collections.  Nothing in the
    phase creates reference cycles that must be reclaimed before it ends; the previous state is restored.
    """
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()


def _clear_staging(staging: Path) -> None:
    if staging.is_symlink():
        staging.unlink()
    elif staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    staging.chmod(0o700)


_AGENT_TREES = ("_eval", "topics")  # docs-repo folders a coding agent writes, under its own umask
_OWN_WALK = 2000  # entries looked at below each tree that is made owner-only (status samples 500 a tree)
# How an own path is opened: never through a symlink, and never waiting (a FIFO opens at once).
_NO_LINK = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC

_Own = tuple[Path, str | Path, int | None]
"""A path :func:`_tighten_own_paths` makes owner-only, and how it is opened: the path as
``docs_repo.permissions`` names it, then its name in the folder that holds it and a descriptor on that
folder.  A folder the config names has no such folder: its path, and None."""


def _clear_group_other(path: str | Path, *, dir_fd: int | None = None) -> bool:
    """Clear ``path``'s group/other permission bits; True when they were set.  A symlink is left alone (its
    target may lie outside the docs repo), and so is anything that is not a regular file or a folder.  The
    mode is read again and changed through one descriptor opened without following a link: these are
    entries others can still write, so one swapped for a symlink after the first look is an error (ELOOP),
    never a chmod of its target.  ``O_NOFOLLOW`` covers the entry and not the folders above it, so an
    entry found by a walk is given as its name in the folder that holds it, with ``dir_fd`` a descriptor
    on that folder: no folder above it is looked up a second time."""

    def loose(mode: int) -> bool:
        return bool(mode & 0o077) and (stat.S_ISREG(mode) or stat.S_ISDIR(mode))

    if not loose(os.lstat(path, dir_fd=dir_fd).st_mode):
        return False
    fd = os.open(path, _NO_LINK, dir_fd=dir_fd)
    try:
        mode = os.fstat(fd).st_mode
        if not loose(mode):
            return False
        os.fchmod(fd, stat.S_IMODE(mode) & ~0o077)
    finally:
        os.close(fd)
    return True


def _open_folder(top: str | Path, below: Sequence[str] = (), *, dir_fd: int | None = None) -> int:
    """A descriptor on the folder ``below`` (names, outermost first) under ``top``, which is a path, or a
    name in the open folder ``dir_fd``.  Each step is opened from the one above it and none through a
    symlink, so what is reached is inside ``top`` whatever was renamed meanwhile.  Nor does it wait: a
    FIFO put where a folder was is an error, not a sync that hangs (``os.fwalk`` opens without
    ``O_NONBLOCK`` before Python 3.12)."""
    fd = os.open(top, _NO_LINK | os.O_DIRECTORY, dir_fd=dir_fd)
    for name in below:
        try:
            inner = os.open(name, _NO_LINK | os.O_DIRECTORY, dir_fd=fd)
        finally:
            os.close(fd)
        fd = inner
    return fd


def _listed(fd: int) -> tuple[list[str], list[str]]:
    """The names in the open folder ``fd``: its folders, then the rest, split and ordered as ``os.walk``
    does (the check walks with it), so a symlink to a folder counts as a folder here too."""
    dirs: list[str] = []
    rest: list[str] = []
    with os.scandir(fd) as entries:
        for entry in entries:
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False
            (dirs if is_dir else rest).append(entry.name)
    return dirs, rest


def _below(tree: Path, top: str | Path, dir_fd: int | None = None) -> Iterator[_Own]:
    """At most :data:`_OWN_WALK` entries below the folder ``tree``, in the order ``os.walk`` lists them.
    ``tree`` is opened once, as ``top``: its own path, or its name in the open folder ``dir_fd``.  Each
    entry comes with a descriptor on the folder that holds it, good until the next entry is asked for.
    One folder below is open at a time, reached again from the tree's descriptor (:func:`_open_folder`),
    so a deep tree takes no more descriptors than a flat one.  A tree or a folder that is a symlink, is
    gone or cannot be listed is passed over, as ``os.walk`` passes it."""
    try:
        root = _open_folder(top, dir_fd=dir_fd)
    except OSError:
        return
    pending: list[tuple[str, ...]] = [()]  # folders still to list, as names below the tree: the last is next
    seen = 0
    try:
        while pending:
            below = pending.pop()
            try:
                fd = _open_folder(".", below, dir_fd=root)
            except OSError:
                continue
            try:
                try:
                    dirs, rest = _listed(fd)
                except OSError:
                    continue
                folder = tree.joinpath(*below)
                for name in (*dirs, *rest):
                    if seen == _OWN_WALK:
                        return
                    seen += 1
                    yield folder / name, name, fd
            finally:
                os.close(fd)
            pending.extend((*below, name) for name in reversed(dirs))
    finally:
        os.close(root)


def _in_repo(repo: Path) -> Iterator[_Own]:
    """Each entry at the top of the docs repo, then what is below :data:`_AGENT_TREES`, all of it reached
    from one descriptor on the repo.  A docs repo that is not made yet, or is a symlink, has none."""
    try:
        fd = _open_folder(repo)
    except OSError:
        return
    try:
        with contextlib.suppress(OSError):
            dirs, rest = _listed(fd)
            for name in sorted((*dirs, *rest)):
                yield repo / name, name, fd
        for name in _AGENT_TREES:
            yield from _below(repo / name, name, fd)
    finally:
        os.close(fd)


def _holds_home(folder: Path) -> bool:
    """True when ``folder`` is the home folder or holds it.  A config may name any folder for its cache,
    its logs and its state, and that one is not agentsync's alone: neither a sync nor setup
    (``cli._owner_only_dirs``) makes it owner-only.  The file system says which folder a path is (the
    same device and inode), not its spelling: ``~/x/..``, a path through a symlink and, on a volume that
    takes a name in any case, another case are all the home folder, and the folder that is opened is the
    one that is changed.  The folders above it are those of its path as written and of where it really is
    (a home folder reached through a symlink).  A folder that is not there is neither."""

    def same(above: Path) -> bool:
        try:
            return folder.samefile(above)
        except OSError:
            return False

    home = Path.home()
    above = {home, *home.parents}
    with contextlib.suppress(OSError, RuntimeError):  # a home folder that does not resolve (a symlink loop)
        above.update(home.resolve().parents)
    return any(same(path) for path in above)


def _own_entries(config: Config) -> Iterator[_Own]:
    """:func:`_own_paths`, each with how it is opened (:data:`_Own`).  A folder the config names is opened
    by its path.  Everything inside one is opened by its name from a descriptor on the folder that holds
    it, so a folder swapped for a symlink after the walk listed it leads no change into the link's
    target: the walk does not enter it, and an entry it listed before is still found where it was."""
    repo, home = expand(config.docs_repo), Path.home()
    ctx = expand(config.config_path).parent
    own = [d for d in (expand(config.cache_dir), expand(config.log_dir)) if not _holds_home(d)]
    if ctx != home and ctx in repo.parents:
        yield ctx, ctx, None
    for folder in (repo, *own):
        yield folder, folder, None
    yield from _in_repo(repo)
    for folder in own:
        yield from _below(folder, folder)


def _own_paths(config: Config) -> Iterator[Path]:
    """The paths :func:`_tighten_own_paths` makes owner-only: agentsync's own, and all of them paths
    ``docs_repo.permissions`` looks at.  The agent-context folder (the config's folder when the docs repo
    is inside it and it is not the home folder, which is the check's rule), the docs repo and every entry
    at its top (the entry itself, not its contents), the cache folder and the log folder; then what is
    below :data:`_AGENT_TREES`, the cache folder and the log folder: at most :data:`_OWN_WALK` entries a
    tree, in the order the check walks them, so the walk is bounded and still reaches every entry the
    check samples.  ``mirror/`` and ``.git`` are not walked: the publisher writes pages 0600 and git
    writes under ``core.sharedRepository``, which ``gitops.ensure_repo`` sets.  A tree that is a symlink
    is not walked, nor is anything in a docs repo that is one, and a cache or log folder that is the home
    folder, or holds it, is not agentsync's alone: it is left out, however it is spelled
    (:func:`_holds_home`)."""
    return (path for path, _name, _dir_fd in _own_entries(config))


def _tighten_own_paths(config: Config) -> int:
    """Make agentsync's own paths owner-only (:func:`_own_paths`); return how many changed.

    agentsync writes under umask 077, but an agent's file tool runs under the agent's umask (usually 022),
    so the baseline draft left ``_eval/`` readable by group and other and the next ``status`` ended on a
    ``docs_repo.permissions`` FAIL the loop itself had caused; a cache folder or a log that something else
    made does the same.  Every non-dry cycle calls this, and so do ``init`` and ``add-source``
    (``cli._ensure_setup``), which ``install.sh`` runs before its status step: a setup run stopped on that
    FAIL before the sync that would have cleared it.  Only group and other bits are cleared, so no mode is
    widened, and no symlink is followed or changed, at an entry or at a folder above it inside the folders
    the config names (:func:`_own_entries`).  Modes are not content: git tracks only the executable bit,
    so this never dirties the tree.  A path that cannot be changed is skipped with one warning (the status
    check still reports it, with a chmod as its fix)."""
    changed, failed = 0, 0
    for _path, name, dir_fd in _own_entries(config):
        try:
            changed += _clear_group_other(name, dir_fd=dir_fd)
        except FileNotFoundError:  # not made yet (the log folder before the first job), or gone since
            continue
        except OSError:
            failed += 1
    if changed:
        log.info("cleared group/other access on %d of agentsync's own path(s)", changed)
    if failed:
        log.warning("%d path(s) could not be made owner-only (agentsync status names them)", failed)
    return changed


def _sync_leaves(config: Config, paths: Iterable[Path]) -> list[Path]:
    """Those of ``paths`` the next sync does not make owner-only: one outside :func:`_own_paths`, one that
    is neither a regular file nor a folder, one of another user's (only its owner may change a mode), or
    one its owner may not read (:func:`_clear_group_other` changes a mode through a descriptor, and
    opening one takes the owner's read bit; a chmod does not).  ``docs_repo.permissions`` words its fix by
    it: a sync when it clears them all, else the chmod.  Nothing is changed here."""
    own = set(_own_paths(config))

    def ours(path: Path) -> bool:
        try:
            st = path.lstat()
        except OSError:
            return False
        if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
            return False
        return st.st_uid == os.geteuid() and bool(st.st_mode & stat.S_IRUSR)

    return [p for p in paths if p not in own or not ours(p)]


def _charge(allowance: recording_mod.Allowance | None, started: float) -> None:
    """Add the time since ``started`` (on ``_recording_clock``) to the recording allowance, if any."""
    if allowance is not None:
        allowance.spent_s += _recording_clock() - started


def _discard_staged(fetched: FetchResult, staging: Path) -> None:
    fetched.path.unlink(missing_ok=True)
    parent = fetched.path.parent
    if parent != staging and staging in parent.parents:
        with contextlib.suppress(OSError):
            parent.rmdir()


class _DownloadDeadline(Exception):  # noqa: N818 - a signal: the download passed its deadline
    """A recording's download did not finish by its deadline (spec S0 rule 3)."""


def _free_bytes(path: Path) -> int:
    """Free bytes of the volume that holds ``path`` (or its nearest existing parent)."""
    while not path.exists() and path.parent != path:
        path = path.parent
    return shutil.disk_usage(path).free


def _fetch_by(deadline_s: float, fetch: Callable[[], FetchResult]) -> FetchResult:
    """Run ``fetch`` and wait at most ``deadline_s`` for it; past that raise :class:`_DownloadDeadline`.  The
    copy left running ends with the process (a daemon thread) or finishes into the staging folder, which the
    next cycle clears before it starts."""
    done: list[FetchResult | BaseException] = []

    def work() -> None:
        try:
            done.append(fetch())
        except BaseException as exc:  # handed to the waiting cycle
            done.append(exc)

    worker = threading.Thread(target=work, name="agentsync-recording-download", daemon=True)
    worker.start()
    worker.join(deadline_s)
    if not done:
        raise _DownloadDeadline
    if isinstance(done[0], BaseException):
        raise done[0]
    return done[0]


def _stat_key(row: ItemRow) -> str:
    """What a failed download of ``row`` is remembered against: its size, modification time and online-only
    flag.  A change to any of them makes it worth trying again."""
    return f"{row.size or 0}:{row.mtime_ns or 0}:{int(row.dataless)}"


# ---------------------------------------------------------------------------------------------------------
# arms, client, recovery
# ---------------------------------------------------------------------------------------------------------


def build_arms(
    config: Config,
    manifest: Manifest,
    client: GraphClient | None,
    *,
    persist_page_links: bool = True,
) -> dict[str, SourceArm]:
    """Instantiate one arm per live source (Graph kinds skipped with a reason when ``client`` is None)."""
    arms: dict[str, SourceArm] = {}
    for src in config.live_sources():
        if src.kind is SourceKind.LOCAL:
            arms[src.id] = LocalArm(src)
        elif src.kind is SourceKind.INBOX:
            arms[src.id] = InboxArm(src)
        elif client is None:
            log.info("source %s (%s): no Graph client this cycle; skipped", src.id, src.kind.value)
        elif src.kind is SourceKind.GRAPH_DRIVE:
            cursor = manifest.get_cursor(src.id)

            def save(link: str | None, sid: str = src.id) -> None:
                manifest.set_page_link(sid, link)

            arms[src.id] = DriveArm(
                client,
                src,
                manifest.tree_lookup(src.id),
                save_page_link=save if persist_page_links else None,
                resume_link=cursor.page_link if cursor is not None else None,
            )
        elif src.kind is SourceKind.GRAPH_MAIL:
            arms[src.id] = MailArm(client, src)
        elif src.kind is SourceKind.GRAPH_TEAMS:
            arms[src.id] = TeamsArm(client, src, config.state_paths.teams_store / src.id)
    return arms


NETWORK_POLICY_FAILED = "failed: "
"""Prefix of a client problem that FAILS the Graph sources (network policy), rather than skipping them."""

LISTING_HELD = "listing held: "
"""Prefix of the ``run_sources.skipped_reason`` of a local walk that timed out on a read macOS holds for an
Allow prompt (``ScanResult.listing_held``, field N8): the pass fetched nothing, and next-step says click
Allow."""


def _proxy_setting(config: Config) -> str | None:
    network = getattr(config, "network", None)
    value = getattr(network, "proxy", None) if network is not None else None
    return value if isinstance(value, str) else None


def _make_client(
    config: Config,
    sources: Sequence[SourceConfig],
    *,
    probe: Callable[[str, net.ProxySettings], net.Reachability] | None = None,
) -> tuple[GraphClient | None, str | None, MsalAuth | None]:
    """A Graph client when a selected live Graph source exists and ``[graph] client_id`` is set.

    The reachability gate (C15 section 9 item 34) runs first: one HTTPS HEAD to the Graph host through the
    resolved proxy with truststore TLS.  Offline -> the Graph sources are *skipped*; a certificate, proxy or
    PAC failure -> ``failed: network-policy (...)`` (the problem starts with :data:`NETWORK_POLICY_FAILED`),
    never skipped, because waiting does not fix it.
    """
    if not any(s.kind.is_graph and s.is_live for s in sources):
        return None, None, None
    if not config.graph.client_id:
        return (
            None,
            "Graph source configured but [graph] client_id is unset (IT app registration pending)",
            None,
        )
    try:
        auth = MsalAuth(settings_from_config(config))
    except AgentSyncError as exc:
        return None, f"Graph auth unavailable: {exc}", None
    proxy = net.resolve_proxy(_proxy_setting(config))
    reach = (probe or net.probe_reachability)(config.graph.base_url, proxy)
    if reach.failed:
        return None, f"{NETWORK_POLICY_FAILED}{reach.state} ({_one_line(reach.detail, 200)})", auth
    if reach.skipped:
        return None, f"offline: {_one_line(reach.detail, 200)}", auth
    client = GraphClient(
        auth,
        base_url=config.graph.base_url,
        user_agent=user_agent("agentsync", __version__),  # [graph] company is ignored (KISS K15)
        proxy=proxy,
    )
    return client, None, auth


def _republish_from_cache(
    config: Config,
    manifest: Manifest,
    publisher: Publisher,
    cache: ConverterCache,
    *,
    item: ItemRow,
    run_id: int,
) -> list[MirrorChange] | None:
    """Re-render an item's pages from its cached conversion; None when the cache cannot serve it."""
    outs = [o for o in manifest.outputs_for(item.source_id, item.stable_id) if o.status is OutputStatus.OK]
    keys = {o.action_key for o in outs if o.action_key}
    if len(keys) != 1:
        return None
    cached = cache.get(next(iter(keys)))
    if cached is None or cached.status not in (ConversionStatus.OK, ConversionStatus.UNREADABLE):
        return None
    try:
        source = config.source(item.source_id)
    except ConfigError:
        return None
    result = dataclasses.replace(cached, content_sha256=item.content_sha256 or cached.content_sha256)
    pages = publisher.plan_pages(source, item, result)
    return publisher.write_pages(item, pages, run_id)


def _repair_outputs(config: Config, manifest: Manifest, run_id: int) -> list[MirrorChange]:
    """Make ``docs/mirror`` match the manifest: re-publish damaged/missing pages, drop orphan files.

    Used after a crash or when HEAD moved under the manifest.  Items the cache cannot re-render are marked
    MAYBE_CHANGED so the next work queue fetches and re-publishes them (``_pages_intact`` forces it).
    """
    repo = config.docs_repo
    publisher = Publisher(config, manifest)
    cache = ConverterCache(config.cache_dir)
    changes: list[MirrorChange] = []
    owned: set[str] = set()
    for src in config.sources:
        for out in manifest.iter_outputs(source_id=src.id):
            owned.add(out.output_path)
        graph = src.kind.is_graph
        by_item: dict[str, list[OutputRow]] = {}
        for out in manifest.iter_outputs(source_id=src.id):
            by_item.setdefault(out.stable_id, []).append(out)
        for stable_id, outs in sorted(by_item.items()):
            item = manifest.get_item(src.id, stable_id)
            if item is None:
                continue
            for out in outs:  # tombstone pages are re-rendered from their tombstones row
                if out.status is OutputStatus.TOMBSTONE:
                    t = manifest.get_tombstone(out.output_path)
                    path = repo / out.output_path
                    if t is not None and (not path.is_file() or _sha256_file(path) != out.page_sha256):
                        hidden = manifest.is_redacted(src.id, stable_id)
                        text = render_tombstone(
                            t,
                            title=redacted_path(item.rel_path) if hidden else item.name,
                            source_kind=src.kind.value,
                            source_path=redacted_path(item.rel_path) if hidden else item.rel_path,
                            durable_id=manifest.durable_id(src.id, stable_id),
                            archived=t.reason == DELETED_UPSTREAM
                            and (repo / archive_path(out.output_path)).is_file(),
                        )
                        _write_atomic(path, text)
            live = [o for o in outs if o.status is not OutputStatus.TOMBSTONE]
            if not live:
                continue
            if _pages_intact(repo, outs):
                if not _page_provenance_matches(
                    repo, item, outs, graph, manifest.durable_id(src.id, stable_id)
                ):
                    try:
                        changes += publisher.rewrite_frontmatter(item, run_id)
                    except PublishError as exc:
                        log.warning("repair %s/%s: %s", src.id, stable_id, exc)
                        manifest.set_verdict(src.id, stable_id, Verdict.MAYBE_CHANGED)
                continue
            try:
                redone = _republish_from_cache(config, manifest, publisher, cache, item=item, run_id=run_id)
            except (AgentSyncError, OSError) as exc:
                log.warning("repair %s/%s from cache failed: %s", src.id, stable_id, exc)
                redone = None
            if redone is None:
                manifest.set_verdict(src.id, stable_id, Verdict.MAYBE_CHANGED)
                log.warning("repair %s/%s: pages damaged; queued for re-fetch", src.id, stable_id)
            else:
                changes += redone
    mirror = config.layout.mirror
    if mirror.is_dir():
        owned = {o.output_path for o in manifest.iter_outputs()}
        for page in sorted(mirror.rglob("*.md")):
            rel = page.relative_to(repo).as_posix()
            if rel == "mirror/CLAUDE.md" or ".files/" in rel or rel in owned:
                continue
            log.warning("repair: removing orphan mirror page %s", rel)
            page.unlink()
            sidecars = page.with_suffix(".files")
            if sidecars.is_dir() and not sidecars.is_symlink():
                shutil.rmtree(sidecars)
    return changes


def recover(config: Config, manifest: Manifest) -> RecoveryAction:
    """Compare manifest tree_sha / pending cursors with git HEAD and repair (design 4.7 recovery).

    - HEAD's tree differs from ``tree_sha`` and HEAD carries the ``Agentsync-Run`` trailer of the run that
      staged the pending cursors (or of the run that crashed): the commit landed, so promote -> ADOPT_PENDING.
    - HEAD's tree differs for any other reason (a manual commit or reset, a lost ``set_tree_sha``): restore
      the generated paths to HEAD and re-publish every page the manifest owns -> RESET_GENERATED.
    - Otherwise pending cursors left by a dead run are discarded -> DISCARD_PENDING; after a crash (a runs
      row still 'running') the mirror is verified against the manifest either way.
    """
    repo = config.docs_repo
    runs = manifest.last_runs(1)
    crashed = runs[0][0] if runs and runs[0][2] == "running" else None
    pending = [
        c
        for s in config.sources
        if (c := manifest.get_cursor(s.id)) is not None and c.pending is not None and c.pending_run_id
    ]
    pending_runs = sorted({c.pending_run_id for c in pending if c.pending_run_id is not None})
    head_tree = gitops.head_tree_sha(repo)
    stored = manifest.tree_sha()
    now_iso = _iso(datetime.now(UTC))
    action = RecoveryAction.NONE
    repair = crashed is not None
    # The HEAD trailer only matters when there is a pending cursor or a crashed run to attribute it to.
    head_run = _head_run_id(repo) if head_tree is not None and (pending_runs or crashed) else None
    if head_run is not None and head_run in pending_runs:
        # The commit of the run that staged these cursors landed (crash after commit, before promotion).
        promoted = manifest.promote_cursors(head_run, now_iso)
        log.warning("recover: commit of run %d landed; promoted cursors of %s", head_run, promoted)
        for run in pending_runs:
            if run != head_run:
                manifest.discard_pending(run)
        if head_tree is not None and head_tree != stored:
            manifest.set_tree_sha(head_tree)
        action = RecoveryAction.ADOPT_PENDING
    elif head_tree is not None and head_tree != stored:
        for run in pending_runs:
            manifest.discard_pending(run)
        if head_run is not None and head_run == crashed:
            log.warning("recover: commit of crashed run %d landed; recording its tree", head_run)
            action = RecoveryAction.ADOPT_PENDING
        else:
            log.warning("recover: HEAD tree differs from the manifest's; resetting generated paths")
            gitops.restore_generated(repo)
            action = RecoveryAction.RESET_GENERATED
            repair = True
        manifest.set_tree_sha(head_tree)
    elif pending_runs:
        for run in pending_runs:
            manifest.discard_pending(run)
        log.warning("recover: discarded pending cursors of dead run(s) %s", pending_runs)
        action = RecoveryAction.DISCARD_PENDING
    if crashed is not None:
        landed = gitops.head_sha(repo) if head_run == crashed else None
        manifest.finish_run(crashed, status="aborted", commit_sha=landed, counts={})
    if repair:
        run_for_repair = crashed if crashed is not None else (runs[0][0] if runs else 0)
        changes = _repair_outputs(config, manifest, run_for_repair)
        log.warning("recover: verified mirror against the manifest (%d page change(s))", len(changes))
    return action


# ---------------------------------------------------------------------------------------------------------
# the cycle
# ---------------------------------------------------------------------------------------------------------


def _blank_png() -> bytes:
    """A white 64 x 64 PNG: an image any working helper answers for (it holds no text)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    side = 64
    head = chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 0, 0, 0, 0))
    pixels = chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff" * side) * side))
    return b"\x89PNG\r\n\x1a\n" + head + pixels + chunk(b"IEND", b"")


class _CycleOcr(OcrEngine):
    """The OCR engine of one cycle: the engine found on this Mac, adding up the seconds its helper ran. Every
    converter of the cycle reads through it, so ``spent_s`` is all the OCR the cycle did; a conversion the
    cache served never reaches it.

    A read that fails says nothing yet about whose failure it is.  So the helper is then handed a blank
    image (``_OCR_CANARY``): when it fails on that too (it may not be run, it was removed, it crashes on
    everything), the failure is no file's, ``down`` is set, and the helper is not run again in this cycle.
    The cycle converts without it from there on and counts no file's re-read against the file."""

    spent_s = 0.0
    down = False

    def read(
        self, images: Sequence[Path], *, work_dir: Path, budget_s: float, frames: int = 1
    ) -> list[tuple[OcrImage, ...]]:
        """As ``OcrEngine.read``; the seconds it took, whether or not it worked, are added to ``spent_s``.
        OcrError at once when the helper is ``down``."""
        if self.down:
            raise OcrError("the OCR helper is not run again in this cycle")
        start = _ocr_clock()
        try:
            return super().read(images, work_dir=work_dir, budget_s=budget_s, frames=frames)
        except OcrError:
            self.down = not self._reads_a_blank_image(work_dir)
            if self.down:
                log.warning("%s", _OCR_DOWN)
            raise
        finally:
            self.spent_s += _ocr_clock() - start

    def _reads_a_blank_image(self, work_dir: Path) -> bool:
        canary = work_dir / _OCR_CANARY
        try:
            canary.write_bytes(_blank_png())
            super().read([canary], work_dir=work_dir, budget_s=_OCR_CANARY_S)
        except (OcrError, OSError):
            return False
        finally:
            with contextlib.suppress(OSError):
                canary.unlink()
        return True


def _cycle_ocr(config: Config) -> _CycleOcr | None:
    """The engine one cycle converts with, or None: no helper is built, ``[convert] ocr = false`` or
    ``AGENTSYNC_OCR=0`` (``ocr.engine``, which never compiles and never raises)."""
    found = ocr.engine(config.convert, config.cache_dir)
    if found is None:
        return None
    return _CycleOcr(
        found.helper, name=found.name, revision=found.revision, helper_version=found.helper_version
    )


def _cycle_media(config: Config, engine: OcrEngine | None) -> MediaEngine | None:
    """The media helper one cycle reads recordings with, or None.  Looked for only beside an OCR ``engine``
    (spec S0 rule 1: a recording is read by both), through ``media.engine``, which never compiles and never
    raises; it answers None under ``[convert] recordings = false``."""
    if engine is None or not config.convert.recordings:
        return None
    return media.engine(config.convert, config.cache_dir)


def _cycle_registry(
    config: Config,
    policy: PolicyConfig,
    engine: OcrEngine | None,
    found: MediaEngine | None,
    pieces: PieceStore,
) -> Registry:
    """``Registry.default`` with the cycle's engines.  The media helper and the piece store go in only beside
    a media helper: without one the registry is the one a Mac without recordings has."""
    if found is None:
        return Registry.default(config.convert, policy=policy, ocr=engine)
    extra: dict[str, Any] = {"media": found, "pieces": pieces}
    return Registry.default(config.convert, policy=policy, ocr=engine, **extra)


@dataclass(slots=True)
class _SourceAcc:
    """Mutable per-source accumulator for one cycle (becomes a SourceReport)."""

    src: SourceConfig
    pass_kind: PassKind | None = None
    enumeration_complete: bool = False
    counts: Counter[Verdict] = field(default_factory=Counter)
    materialised_bytes: int = 0
    deferred: int = 0
    deferred_online_only: int = 0
    converted: int = 0
    breaker_tripped: bool = False
    staged_cursor: bool = False
    skipped_reason: str | None = None
    alarms: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    auth_required: bool = False
    failed: bool = False

    def report(self, cursor_advanced: bool) -> SourceReport:
        """Freeze into the model's SourceReport."""
        return SourceReport(
            source_id=self.src.id,
            kind=self.src.kind,
            pass_kind=self.pass_kind,
            enumeration_complete=self.enumeration_complete,
            counts=dict(sorted(self.counts.items(), key=lambda kv: kv[0].value)),
            materialised_bytes=self.materialised_bytes,
            deferred=self.deferred,
            breaker_tripped=self.breaker_tripped,
            cursor_advanced=cursor_advanced,
            skipped_reason=self.skipped_reason,
            alarms=tuple(self.alarms),
            errors=tuple(self.errors),
            converted=self.converted,
            deferred_online_only=self.deferred_online_only,
        )


class _Cycle:
    """State of one run_cycle call (one instance per cycle)."""

    def __init__(
        self,
        config: Config,
        manifest: Manifest,
        *,
        mode: CycleMode,
        selected: Sequence[SourceConfig],
        clock: Callable[[], datetime],
        lock: SingleWriterLock,
        broke_stale: bool,
        client: GraphClient | None,
        client_problem: str | None,
        budget_bytes: int | None,
        forced_paths: dict[str, set[str]],
        accept_deletions: frozenset[str],
        auth: MsalAuth | None = None,
        stale_backups: Sequence[Path] = (),
        settle_inbox: bool = False,
        recordings: _RecordingRun = "none",
    ) -> None:
        self.config = config
        self.manifest = manifest
        self.mode = mode
        self.selected = selected
        self.clock = clock
        self.lock = lock
        self.broke_stale = broke_stale
        self.client = client
        self.client_problem = client_problem
        self.budget_bytes = budget_bytes
        self.forced_paths = forced_paths
        self.accept_deletions = accept_deletions
        self.repo = config.docs_repo
        self.dry = mode is CycleMode.DRY_RUN
        self.auth = auth
        self.stale_backups = tuple(stale_backups)  # pre-v<N> manifest copies older than this cycle
        self.settle_inbox = settle_inbox  # interactive sync: inboxes wait for settling files (N4)
        # ~/Library/CloudStorage/<provider> folders a walk timed out in this cycle: every other source under
        # one would wait on the same privacy prompt, so they are not read either (field N8)
        self.held_clouds: set[Path] = set()
        # [policy] (sources.toml + policy.toml): a broken policy raises ConfigError (exit 78), never "allow"
        self.publisher = Publisher(config, manifest, clock=clock)
        # The OCR engine is looked for once per cycle, here, and never under a dry run: looking runs the
        # helper's --version. Without one every converter is what it was before OCR existed.
        self.ocr = None if self.dry else _cycle_ocr(config)
        # The media helper likewise (its --version runs), beside an OCR engine only; the piece store is a
        # folder of the cache, created when a piece is first stored.
        self.media = _cycle_media(config, self.ocr)
        self.pieces = PieceStore.under(config.cache_dir)
        self.registry = _cycle_registry(
            config, self.publisher.content_policy, self.ocr, self.media, self.pieces
        )
        self.cache = ConverterCache(config.cache_dir)
        self.gov = governance.load_governance(config.config_path)
        self.suppressions = governance.load_suppressions(config.state_paths.root)
        self.staging = config.state_paths.staging
        self.run_id = 0
        self.changes: list[MirrorChange] = []
        self._recorded = 0  # how many of self.changes are durable in run_changes
        self.ok_pages: set[str] = (
            set()
        )  # mirror pages + sidecars written this cycle with content (secret scan)
        self.stub_pages: set[str] = set()  # stub pages written this cycle (metadata only: name, path)
        self.sidecar_page: dict[str, str] = {}  # sidecar path -> its page (a hit there stubs the page)
        self.findings: list[LintFinding] = []
        self.accs: dict[str, _SourceAcc] = {}
        self.shard_sources: set[str] = set()  # sources whose committed shard changed this cycle
        self.retention_lines: list[str] = []  # STATE.md "## Retention" (compaction ran / held / failed)
        # KISS K06: the automatic checkpoint's outcome (CycleReport.checkpoint and its companions)
        self.checkpoint: str | None = None
        self.checkpoint_detail = ""
        self.checkpoint_blockers: tuple[LintFinding, ...] = ()
        self.snapshot_tag: str | None = None
        # files converted before a capability this install has, read again once (CONTRACTS.md 16.27)
        self._capability: str | None = None  # what a re-read looks for (``_capabilities``), worked out once
        self._rereads: dict[str, _Rereads] = {}  # per source: its record, read once and kept current
        self._reread_others: dict[str, set[str]] = {}  # per source: the ids its other record holds
        self._queue_targets: dict[str, list[tuple[str, str, str | None, str]]] = {}
        self._reread_streak = 0  # re-reads that failed in a row
        self._reread_s = 0.0  # seconds spent reading files again
        self._cannot_run: dict[int, bool] = {}  # per converter: its version cannot be read this cycle
        self._reread_n = 0  # files read again
        self._reread_kept = 0  # of those, the ones whose conversion failed: their page is as it was
        self._reread_left = 0  # files still to read again, over the sources this cycle looked at
        self._reread_looked = False  # a source's record was brought up to date (``_reread_source``)
        # what this run did, as counts for its run record (``_run_tally``, CONTRACTS.md 16.28): never a name
        self._tally: Counter[str] = Counter()
        # the recording pass (spec S0 rules 3 to 8): who reads, what the queues left it, what it knows
        self.recordings: _RecordingRun = "none" if self.dry else recordings
        self._recording_queue: dict[str, list[str]] = {}  # per source: the recordings its queue deferred
        self._recording_arms: dict[str, SourceArm] = {}  # per source whose queue ran: its arm
        self._recording_records: dict[str, _Recordings] = {}  # per source: its record, read once
        self._in_recording_pass = False  # ``_after_fetch`` converts with the whole registry, never past OCR
        self._media_down = False  # the media helper stopped answering --version in this cycle

    # ---- time ---------------------------------------------------------------------------------------------
    def now(self) -> datetime:
        """Current time (UTC) from the injected clock."""
        return self.clock().astimezone(UTC)

    def today(self) -> str:
        """UTC date for tombstones and the changelog."""
        return self.now().date().isoformat()

    # ---- top level ----------------------------------------------------------------------------------------
    def run(self) -> CycleReport:
        """Run the cycle body; the caller holds the lock."""
        if self.dry:
            return self._run_dry()
        gitops.ensure_repo(self.repo)
        last = self.manifest.last_runs(1)
        crashed = last[0][0] if last and last[0][2] == "running" else None
        recovery = recover(self.config, self.manifest)
        if recovery is not RecoveryAction.NONE:
            log.warning("recovery: %s", recovery.value)
        self._refuse_vanished_sources()
        self.run_id = self.manifest.begin_run(self.mode, host=socket.gethostname(), pid=os.getpid())
        if crashed is not None:
            self._carry_crashed_changes(crashed)
        commit_sha: str | None = None
        status = "ok"
        blocked = False
        try:
            fp_changed = set(self.manifest.sync_sources(self.config.sources))
            for sid in sorted(fp_changed):  # until one complete pass: absence = the operator's scope change
                self.manifest.set_meta(_SCOPE_CHANGE_META + sid, str(self.run_id))
            self._note_roots(fp_changed)
            self._check_policy_change()
            self.publisher.ensure_scaffold()
            _tighten_own_paths(self.config)  # another umask is not ours: no permissions FAIL of its making
            skill.write_skill(self.repo)  # outside the docs repo; a failure is only a warning
            _clear_staging(self.staging)
            arms = build_arms(self.config, self.manifest, self.client)
            settle = SettleBudget() if self.settle_inbox else None  # one wait bound for the whole sync
            for arm in arms.values():
                if isinstance(arm, InboxArm):
                    arm.settle = settle
            for src in self.selected:
                self.lock.beat(f"source:{src.id}")
                self._run_source(src, arms.get(src.id), fp_changed)
                self._flush_changes()
            self._recording_pass()  # after every source's queue and re-reads: nothing waits behind it
            self._flush_changes()
            if self._reread_n:  # one line a cycle, and a count: never a name
                log.info(
                    "%d file(s) converted before a capability this install has were read again; %d of "
                    "them could not be converted and keep the page they had",
                    self._reread_n,
                    self._reread_kept,
                )
            self.lock.beat("secrets")
            self._quarantine_secrets()
            self._flush_changes(rewrite=True)
            self.lock.beat("curate")
            pre_head, session_pages = self._session_topic_pages()  # before this run's banner rewrites
            self._curate()
            self.lock.beat("surface")
            statuses = self._statuses(self.config.sources)
            shards = self.publisher.write_manifest_shards([s.id for s in self.config.sources])
            self.shard_sources = {Path(p).stem for p in shards if (self.repo / p).is_file()}
            self.publisher.write_quarantine()
            self.publisher.write_index(statuses)
            if self.changes:
                self.publisher.append_changelog(self.run_id, self.today(), self.changes, self._report(None))
            self.lock.beat("land-gate")
            dirty = gitops.has_changes(self.repo)
            if dirty or self.mode is CycleMode.RECONCILE:
                changed_paths = sorted(
                    {c.path for c in self.changes} | {c.prev_path for c in self.changes if c.prev_path}
                )
                self.findings += lints.run_land_gate(
                    self.repo, changed_paths, known_secrets=self._cursor_secrets()
                )
            else:  # nothing would land: the gate guards a commit, and a clean tree makes none
                log.info("clean worktree: nothing to land; land gate skipped (RECONCILE always runs it)")
            blocked = any(f.blocking for f in self.findings)
            if blocked:
                log.error("land gate blocked the commit: %d blocking finding(s)", self._blocking_count())
                self.manifest.discard_pending(self.run_id)
                status = "failed"
            else:
                self.lock.beat("commit")
                commit_sha = self._commit(statuses, dirty=dirty)
                self._retag_published()
                self.manifest.clear_run_changes(self.run_id)
                self.manifest.promote_cursors(self.run_id, _iso(self.now()))
                self._drop_stale_backups()
                self._checkpoint(pre_head, session_pages)  # before compaction, which remaps its tags
                if self.mode is CycleMode.RECONCILE:
                    removed = self.cache.gc(self.manifest.live_action_keys())
                    if removed:
                        log.info("converter cache: %d dead key(s) removed", removed)
                    self._prune_pieces()
                    self.lock.beat("retention")
                    commit_sha = self._retention(commit_sha)
        except Exception as exc:  # a cycle-level failure: nothing is promoted, the report says why
            log.exception("cycle %d failed", self.run_id)
            self.manifest.discard_pending(self.run_id)
            self.findings.append(
                LintFinding("CYCLE", "-", f"cycle failed: {_one_line(str(exc) or repr(exc))}")
            )
            status = "failed"
            blocked = True
        if status == "ok" and any(a.failed or a.auth_required for a in self.accs.values()):
            status = "partial"
        report = self._report(commit_sha, promoted=not blocked)
        self._after(report, status, ok_cycle=not blocked)
        return report

    def _session_topic_pages(self) -> tuple[str | None, set[str]]:
        """KISS K06: the pre-run HEAD and the topic pages a session left dirty (uncommitted or untracked),
        taken before this run's curation step so its STALE/RETIRED banner rewrites are not in it; the
        scaffold's seeds (CLAUDE.md, INDEX.md) never are.  ``(None, set())`` before the first commit or on a
        git error: no checkpoint this run."""
        head = gitops.head_sha(self.repo)
        if head is None:
            return None, set()
        try:
            dirty = gitops.paths_changed_since(self.repo, head, ("topics",))
        except AgentSyncError as exc:
            log.warning("checkpoint: cannot list the session's topic pages: %s", exc)
            return None, set()
        pages = {p for p in dirty if p.endswith(".md") and p.rsplit("/", 1)[-1] not in _SEED_PAGE_NAMES}
        return head, pages

    def _checkpoint(self, pre_head: str | None, session_pages: set[str]) -> None:
        """KISS K06, the automatic checkpoint, after this cycle's commit landed: when the session wrote topic
        pages and ``curate.checkpoint_blockers`` is empty, move ``curated`` to the pre-run HEAD (the mirror
        state the session curated against; what this run brought in stays in the next curate-queue) and, with
        ``[governance] archive``, cut a snapshot tag at the new commit (it holds the session's pages).  Its
        own try/except: a tagging failure is a warning and never fails the landed cycle.

        A held or failed checkpoint is remembered (meta ``checkpoint_pending`` = its base), because that sync
        already committed the session's pages and no later run would see them dirty: every later cycle
        retries it, session pages or not, until it advances.  The refresh verdicts are scoped to topic pages
        changed since the BASE, the pending HEAD or else the pre-run HEAD, so a page from a held session stays
        checked and an older page a sync only bannered never holds it."""
        if pre_head is None:
            return
        pending = self._pending_checkpoint(pre_head)
        if session_pages:
            target = pre_head
        elif pending is not None:
            target = pending
        else:
            return
        base = pending if pending is not None else pre_head
        try:
            blockers = curate.checkpoint_blockers(self.repo, since=base)
            if blockers:
                self.checkpoint, self.checkpoint_blockers = "held", tuple(blockers)
                log.warning("checkpoint held: %d curation error(s)", len(blockers))
                self._set_pending_checkpoint(base)
                return
            gitops.tag_curated(self.repo, target)
        except Exception as exc:  # the commit already landed: the checkpoint is only a warning
            log.warning("checkpoint not recorded: %s", exc)
            self.checkpoint, self.checkpoint_detail = "failed", _one_line(str(exc) or repr(exc))
            self._set_pending_checkpoint(base)
            return
        self.checkpoint, self.checkpoint_detail = "advanced", target
        log.info("checkpoint advanced: curated at %s", target[:12])
        if pending is not None:
            self._set_pending_checkpoint("")
        if not self.gov.archive:
            return
        try:
            head = gitops.head_sha(self.repo)
            if head is not None:
                self.snapshot_tag = gitops.tag_snapshot(self.repo, head, self.now())
        except Exception as exc:  # e.g. a second snapshot in the same second; the checkpoint itself moved
            log.warning("archive snapshot tag not cut: %s", exc)

    def _pending_checkpoint(self, pre_head: str) -> str | None:
        """The HEAD a held or failed checkpoint would have tagged, or None.  A commit that history
        compaction or a purge rewrote away is no longer an ancestor: retry at the pre-run HEAD instead."""
        try:
            pending = self.manifest.get_meta(_CHECKPOINT_PENDING_META) or None
            if pending is None or gitops.is_ancestor(self.repo, pending, pre_head):
                return pending
        except Exception as exc:  # unreadable: retry at the pre-run HEAD rather than lose it
            log.warning("checkpoint: cannot read the pending checkpoint: %s", exc)
            return pre_head
        log.info("checkpoint: pending %s was rewritten; retrying at %s", pending[:12], pre_head[:12])
        return pre_head

    def _set_pending_checkpoint(self, sha: str) -> None:
        """Record (``sha``) or clear (``""``) the pending checkpoint; a failure is only a warning."""
        try:
            self.manifest.set_meta(_CHECKPOINT_PENDING_META, sha)
        except Exception as exc:
            log.warning("checkpoint: cannot record the pending checkpoint: %s", exc)

    def _drop_stale_backups(self) -> None:
        """Delete the pre-migration manifest copies that existed before this cycle: it committed on the
        migrated database, so the copy has done its job (a copy taken during this cycle waits one more)."""
        for path in self.stale_backups:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("cannot delete the pre-migration manifest copy %s: %s", path, exc)
            else:
                log.info("deleted the pre-migration manifest copy %s", path.name)

    def _retention(self, commit_sha: str | None) -> str | None:
        """Scheduled compaction (C15 section 9 item 39): in a RECONCILE, squash history older than
        ``[governance] history_days`` once it is due; a hold suspends it (STATE.md says which), and
        ``[governance] archive`` turns it off (history is kept)."""
        if self.gov.archive:
            return commit_sha
        try:
            due = governance.compaction_due(self.repo, self.gov, now=self.now())
        except AgentSyncError as exc:
            self.retention_lines.append(f"- compaction check failed: {_one_line(str(exc))}")
            return commit_sha
        if not due:
            return commit_sha
        holds = governance.blocking_holds(
            governance.active_holds(self.config.state_paths.root, self.gov), None
        )
        if holds:
            self.retention_lines += [
                f"- compaction due (history_days {self.gov.history_days}) but SUSPENDED by hold: "
                f"{h.describe()}"
                for h in holds
            ]
            return commit_sha
        try:
            rep = governance.compact_history(self.config, gov=self.gov, lock=False, now=self.now())
        except AgentSyncError as exc:
            log.error("retention compaction failed: %s", exc)
            self.retention_lines.append(f"- compaction FAILED: {_one_line(str(exc))}")
            return commit_sha
        if not rep.verified:
            self.retention_lines.append(
                f"- compaction NOT verified: {len(rep.survivors)} object(s) of the squashed history can "
                "still be read from the docs repo"
            )
        elif rep.squashed:
            self.retention_lines.append(
                f"- compacted {rep.squashed} commit(s) older than {rep.cutoff} (history_days "
                f"{self.gov.history_days})"
            )
            log.warning("retention: %d commit(s) squashed (cutoff %s)", rep.squashed, rep.cutoff)
        return gitops.head_sha(self.repo) if commit_sha is not None else commit_sha

    def _check_policy_change(self) -> None:
        """Re-screen label-capable files when the effective ``[policy]`` (sources.toml + policy.toml) moved.

        Tightening must refuse (and queue a purge of) files already published under the old rules;
        loosening must re-publish files refused under them.  The fingerprint lives in the manifest's meta
        table; a manifest without one re-screens once when label rules are active."""
        policy = self.publisher.content_policy
        current = policy.fingerprint()
        stored = self.manifest.get_meta(_POLICY_META)
        if stored == current:
            return
        if stored is not None or policy.labels_active:
            marked = self.manifest.mark_for_rescreen(_LABEL_CAPABLE)
            if marked:
                self.manifest.set_meta(_RESCREEN_META, current)
                log.warning("[policy] changed: %d label-capable file(s) queued for a re-screen", marked)
        self.manifest.set_meta(_POLICY_META, current)

    def _rescreen_lines(self) -> list[str]:
        """STATE.md lines while a [policy] re-screen is incomplete (cleared once nothing is left)."""
        if not self.manifest.get_meta(_RESCREEN_META):  # never set, or "" since the last re-screen finished
            return []
        left = self.manifest.pending_named(_LABEL_CAPABLE)
        if left == 0:
            self.manifest.set_meta(_RESCREEN_META, "")
            return []
        return [
            "## Content policy",
            "",
            f"- [policy] changed: {left} label-capable file(s) not yet re-screened (fetched and screened as "
            "the byte budget allows); until then their mirror pages reflect the previous policy",
            "",
        ]

    def _refuse_vanished_sources(self) -> None:
        """Retirement is an explicit verb, not an absence: a source whose [[source]] block was deleted while
        its pages are still mirrored would keep them ``status: current`` forever.  Refuse (exit 78) and name
        the fix, before anything is written."""
        configured = {s.id for s in self.config.sources}
        vanished = {
            sid: n for sid, n in self.manifest.present_counts().items() if sid not in configured and n
        }
        if vanished:
            names = ", ".join(f"{sid!r} ({n} file(s))" for sid, n in sorted(vanished.items()))
            raise ConfigError(
                f"source(s) {names} are mirrored but no longer in sources.toml; retirement is explicit: put "
                'the [[source]] block back with state = "retired" (its pages become tombstones in one '
                "labelled commit), or restore it"
            )

    def _retag_published(self) -> None:
        """Keep ``published`` on HEAD when HEAD is an agentsync commit: a crash between commit_cycle and
        tag_published (or a no-op cycle after it) must not leave readers and pushes on the previous tree."""
        head = gitops.head_sha(self.repo)
        if head is None or _head_run_id(self.repo) is None:
            return
        tagged = gitops.run_git(
            self.repo,
            "rev-parse",
            "--verify",
            "-q",
            f"refs/tags/{gitops.PUBLISHED_TAG}^{{commit}}",
            check=False,
        ).stdout.strip()
        if tagged != head:
            gitops.tag_published(self.repo, head)
            log.warning("published tag moved to HEAD %s (it lagged at %s)", head[:12], tagged[:12] or "none")

    def _carry_crashed_changes(self, crashed: int) -> None:
        """A crashed run's recorded changes whose pages are still uncommitted become part of this cycle's
        change set, so its commit subject, body and CHANGELOG describe them (design 4.7: never commit a
        dirty tree anonymously).  Changes of a run whose commit landed are simply forgotten."""
        carried = self.manifest.run_changes(crashed)
        if not carried:
            return
        dirty = _dirty_paths(
            self.repo, sorted({c.path for c in carried} | {c.prev_path for c in carried if c.prev_path})
        )
        keep = [c for c in carried if c.path in dirty or (c.prev_path is not None and c.prev_path in dirty)]
        if keep:
            log.warning(
                "recovery: %d change(s) of crashed run %d carried into run %d",
                len(keep),
                crashed,
                self.run_id,
            )
            self.changes = [*keep, *self.changes]
            self._flush_changes()
        self.manifest.clear_run_changes(crashed)

    def _flush_changes(self, *, rewrite: bool = False) -> None:
        """Record the changes produced since the last flush (all of them with ``rewrite``) in the manifest."""
        if self.dry:
            return
        if rewrite:
            self.manifest.record_run_changes(self.run_id, self.changes, replace=True)
        elif len(self.changes) > self._recorded:
            self.manifest.record_run_changes(self.run_id, self.changes[self._recorded :])
        self._recorded = len(self.changes)

    def _cursor_secrets(self) -> list[str]:
        """The live cursor values (and their token parameters): no committed file may contain one."""
        out: set[str] = set()
        for src in self.config.sources:
            row = self.manifest.get_cursor(src.id)
            if row is None:
                continue
            for value in (row.current, row.pending, row.page_link):
                if not value or value.startswith("hwm:"):
                    continue
                out.add(value)
                out.update(m.group(1) for m in re.finditer(r"token=([^&\s]{16,})", value))
        return sorted(out)

    def _blocking_count(self) -> int:
        return sum(1 for f in self.findings if f.blocking)

    def _report(self, commit_sha: str | None, *, promoted: bool = False) -> CycleReport:
        sources = tuple(
            acc.report(cursor_advanced=promoted and acc.staged_cursor and not acc.failed)
            for acc in self.accs.values()
        )
        auth_required = any(a.auth_required for a in self.accs.values())
        failed = self._blocking_count() > 0 or any(a.failed for a in self.accs.values())
        exit_code = 77 if auth_required else (1 if failed else 0)
        return CycleReport(
            run_id=self.run_id,
            mode=self.mode,
            sources=sources,
            changes=tuple(sorted(self.changes, key=lambda c: (c.path, c.op.value))),
            commit_sha=commit_sha,
            lint_findings=tuple(sorted(self.findings, key=lambda f: (not f.blocking, f.code, f.path))),
            broke_stale_lock=self.broke_stale,
            auth_required=auth_required,
            exit_code=exit_code,
            checkpoint=self.checkpoint,
            checkpoint_detail=self.checkpoint_detail,
            checkpoint_blockers=self.checkpoint_blockers,
            snapshot_tag=self.snapshot_tag,
        )

    def _after(self, report: CycleReport, status: str, *, ok_cycle: bool) -> None:
        """Step 12-13: last-success, heartbeats, finish_run, STATE.md (every cycle, failures included)."""
        now_iso = _iso(self.now())
        hb = self.config.state_paths.heartbeat
        for acc in self.accs.values():
            ok = ok_cycle and not acc.failed and not acc.auth_required and acc.pass_kind is not None
            if ok:
                self.manifest.set_last_success(acc.src.id, self.run_id)
            if acc.skipped_reason in ("paused", "retired"):
                continue
            row = self.manifest.get_source(acc.src.id)
            try:
                write_heartbeat(
                    hb,
                    acc.src.id,
                    run_id=self.run_id,
                    pass_kind=acc.pass_kind,
                    enumeration_complete=acc.enumeration_complete,
                    ok=ok,
                    auth_state=row.auth_state if row is not None else "ok",
                    now_iso=now_iso,
                )
            except (OSError, ValueError) as exc:
                log.warning("heartbeat for %s not written: %s", acc.src.id, exc)
        counts = Counter(c.op.value for c in self.changes)
        counts.update(self._run_tally())
        self.manifest.finish_run(self.run_id, status=status, commit_sha=report.commit_sha, counts=counts)
        try:
            self.publisher.write_state(report, self._statuses(self.config.sources))
            self._append_state(self._state_extras())
        except (OSError, AgentSyncError) as exc:
            log.warning("STATE.md not written: %s", exc)

    def _run_tally(self) -> Counter[str]:
        """What this run adds to its ``runs.counts_json`` beside the change counts (CONTRACTS.md 16.28):
        integers only, so the setup report can say later what OCR did and whether the same files are
        converted run after run.  ``converted`` is always there, so a record without it is one from before
        these counts; ``ocr_ms`` and ``ocr_budget_s`` are there whenever the cycle had an engine.  Any
        other key that would be 0 is left out.

        ``converted``: files converted (the sum of ``SourceReport.converted``); ``converted_failed``: of
        those, conversions that failed (counted in the run they failed in: the file keeps its stub and is
        not converted again until its bytes change); ``converted_seen``: of those, files whose own pages
        were made from the same bytes by an earlier run (``_converted_before``; a copy of a file is not
        one); ``converted_again``: of those, when the bytes were last converted in the run just before.
        ``reread`` and ``reread_kept``: files read again for what their converter has gained, and those
        of them that kept their page.  ``reread_left``: files on this Mac still to read again when the
        cycle stopped looking, over the sources it looked at (``_reread_source``); ``loop.next_step`` words
        its "sync again" note from it.
        ``ocr_ms``: milliseconds the helper ran; ``ocr_budget_s``: the cycle's OCR time; ``ocr_over``: 1
        when it was used up; ``ocr_down``: 1 when the helper stopped working; ``ocr_deferred``: files left
        for a later cycle's OCR (``_ocr_waits``); ``ocr_without_budget`` and ``ocr_without_down``: files
        converted without the engine for either reason; ``ocr_failed``: files the engine failed on (a
        helper failure, or the file's own time limit); ``ocr_page_cap`` and ``ocr_picture_cap``:
        conversions that say a count limit left pages or pictures unread.

        ``reread_for`` is the one key that is no count: what this run's re-read looked for
        (``_reread_number`` of ``_capabilities``), there when the run brought a source's re-read record
        up to date (``_reread_source``).  The report holds each source's stored record against the newest
        one, and so knows a record an earlier build or another engine left from one this build wrote."""
        tally = Counter({key: count for key, count in self._tally.items() if count})
        tally["converted"] = self._tally["converted"]
        if self._reread_n:
            tally["reread"] = self._reread_n
        if self._reread_kept:
            tally["reread_kept"] = self._reread_kept
        if self._reread_left:
            tally["reread_left"] = self._reread_left
        if self._reread_looked:
            tally["reread_for"] = _reread_number(self._capabilities())
        if self.ocr is not None:
            tally["ocr_ms"] = round(self.ocr.spent_s * 1000)
            tally["ocr_budget_s"] = round(_OCR_BUDGET_S)
            if self.ocr.spent_s >= _OCR_BUDGET_S:
                tally["ocr_over"] = 1
            if self.ocr.down:
                tally["ocr_down"] = 1
        return tally

    def _tally_ocr(self, result: ConversionResult, *, lacks: bool, plain: bool) -> None:
        """Count what OCR left unread of one conversion (``_run_tally``).  ``lacks``: the result is one
        this cycle's registry would read the file again for (``_lacks``); ``plain``: it was converted with
        the registry that has no engine (``_converting``)."""
        if self.ocr is None:
            return
        said = " ".join([result.reason or "", *(unit.summary for unit in result.units)])
        if _PAGE_CAP_MARK in said:
            self._tally["ocr_page_cap"] += 1
        if _PICTURE_CAP_MARK in said:
            self._tally["ocr_picture_cap"] += 1
        if not lacks:
            return
        if not plain:
            self._tally["ocr_failed"] += 1
        else:
            self._tally["ocr_without_down" if self.ocr.down else "ocr_without_budget"] += 1

    def _converted_before(self, outs: Sequence[OutputRow], key: str) -> int | None:
        """The run that last converted under ``key`` (the bytes and the converter), for a file whose own
        pages were made under it; None for any other file.  ``outs``: the file's output rows from before
        this conversion.

        ``Manifest.cache_last_used`` goes by the key alone.  Asked for every file, it called a second file
        with the bytes of one the run before had converted (a copy, a re-export, one attachment saved
        twice) a file converted again, and the setup report read that as a loop."""
        if not key or all(o.action_key != key for o in outs):
            return None
        return self.manifest.cache_last_used(key)

    def _tally_converted(self, result: ConversionResult, prior: int | None) -> None:
        """Count one conversion (``_run_tally``).  ``prior``: the run that last converted the file's bytes
        when its own pages were made from them (``_converted_before``), None otherwise."""
        self._tally["converted"] += 1
        if result.status is ConversionStatus.FAILED:
            self._tally["converted_failed"] += 1
        if prior is not None and prior < self.run_id:
            self._tally["converted_seen"] += 1
            if prior == self.run_id - 1:
                self._tally["converted_again"] += 1

    def _state_extras(self) -> list[str]:
        """STATE.md sections the publisher does not render: Graph sign-in and token source (C15 section 9
        item 4), the network gate, legal/records holds (item 40) and queued purges (item 38)."""
        lines: list[str] = []
        graph_live = [s.id for s in self.selected if s.kind.is_graph and s.is_live]
        if graph_live:
            lines += ["## Graph sign-in", ""]
            if self.auth is not None:
                try:
                    method = self.auth.status().sign_in_method or "unknown"
                except Exception as exc:  # the cache may be unreadable; STATE.md still says so
                    method = f"unknown ({type(exc).__name__})"
                lines.append(f"sign_in_method: {method}")
                lines.append(f"token_source: {self.auth.last_token_source or 'none this run'}")
                if self.auth.last_token_source not in (None, "broker", "cache") and method == "broker":
                    lines.append("alarm: the broker account's token did not come from the broker")
            lines.append(f"network: {self.client_problem or 'online'}")
            lines.append("")
        lines += self._rescreen_lines()
        retention = list(self.retention_lines)
        if not retention and self.gov.archive:
            retention.append(f"- {governance.ARCHIVE_KEEPS_HISTORY}")
        elif not retention:
            try:
                state, detail = governance.compaction_state(self.repo, self.gov, now=self.now())
            except AgentSyncError:
                state, detail = "ok", ""
            if state != "ok":
                retention.append(f"- {state}: {detail}")
        if retention:
            lines += ["## Retention", "", *retention, ""]
        lines += governance.hold_state_lines(self.config.state_paths.root, self.gov)
        queued = governance.pending_purges(self.config.state_paths.root)
        if queued:
            lines += [
                "## Queued purges",
                "",
                f"- {len(queued)} purge request(s) waiting; run `agentsync purge --queue` (rewrites history)",
                "",
            ]
        return lines

    def _append_state(self, lines: Sequence[str]) -> None:
        if not lines:
            return
        path = self.config.layout.state_md
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        _write_atomic(path, text.rstrip("\n") + "\n\n" + "\n".join(lines).rstrip("\n") + "\n")

    # ---- commit -------------------------------------------------------------------------------------------
    def _commit(self, statuses: Sequence[SourceStatus], *, dirty: bool | None = None) -> str | None:
        if not (gitops.has_changes(self.repo) if dirty is None else dirty):
            log.info("no content change: no commit")
            return None
        self.publisher.write_state_snapshot(statuses)
        source_ids = sorted({c.source_id for c in self.changes} | self.shard_sources)
        notes = [f"{a.src.id}: {alarm}" for a in self.accs.values() for alarm in a.alarms]
        body = gitops.commit_body(self.changes, run_id=self.run_id, mode=self.mode.value, notes=notes)
        sha = gitops.commit_cycle(self.repo, gitops.commit_subject(self.changes, source_ids), body)
        if sha is not None:
            tree = gitops.head_tree_sha(self.repo)
            if tree is not None:
                self.manifest.set_tree_sha(tree)
            gitops.tag_published(self.repo, sha)
        return sha

    # ---- dry run ------------------------------------------------------------------------------------------
    def _run_dry(self) -> CycleReport:
        runs = self.manifest.last_runs(1)
        self.run_id = (runs[0][0] if runs else 0) + 1
        arms = build_arms(self.config, self.manifest, self.client, persist_page_links=False)
        for src in self.selected:
            acc = self._acc(src)
            arm = arms.get(src.id)
            if not src.is_live or arm is None:
                acc.skipped_reason = src.state.value if not src.is_live else (self.client_problem or "no arm")
                continue
            try:
                with _gc_paused():
                    scan, pc, _rows, _fast, _restamp = self._scan_and_classify(src, arm, set())
            except AuthRequiredError as exc:
                acc.auth_required = True
                acc.errors.append(f"auth: {exc}")
                continue
            except Exception as exc:
                acc.failed = True
                acc.errors.append(_one_line(f"{type(exc).__name__}: {exc}"))
                continue
            del scan, pc
        return self._report(None)

    # ---- per source ---------------------------------------------------------------------------------------
    def _acc(self, src: SourceConfig) -> _SourceAcc:
        acc = self.accs.get(src.id)
        if acc is None:
            acc = self.accs[src.id] = _SourceAcc(src)
        return acc

    def _run_source(self, src: SourceConfig, arm: SourceArm | None, fp_changed: set[str]) -> None:
        acc = self._acc(src)
        if src.state is SourceState.PAUSED:
            acc.skipped_reason = "paused"
            self.manifest.record_source_pass(
                self.run_id, src.id, pass_kind=None, enumeration_complete=False, cursor_reset=False,
                counts={}, skipped_reason="paused",
            )  # fmt: skip
            return
        if src.state is SourceState.RETIRED:
            self._retire(src, acc)
            return
        if arm is None:
            acc.skipped_reason = self.client_problem or "no Graph client this cycle"
            acc.alarms.append(acc.skipped_reason)
            if acc.skipped_reason.startswith(NETWORK_POLICY_FAILED):  # TLS / proxy / PAC: failed, not skipped
                acc.failed = True
                acc.errors.append(acc.skipped_reason)
            self.manifest.record_source_pass(
                self.run_id, src.id, pass_kind=None, enumeration_complete=False, cursor_reset=False,
                counts={}, skipped_reason=acc.skipped_reason,
            )  # fmt: skip
            return
        try:
            self._sync_source(src, arm, acc, fp_changed)
            if src.kind.is_graph:
                row = self.manifest.get_source(src.id)
                if row is not None and row.auth_state != "ok":
                    self.manifest.set_auth_state("ok", [src.id])
        except AuthRequiredError as exc:
            log.error("source %s: sign-in required: %s", src.id, exc)
            state = "REAUTH_REQUIRED"
            if isinstance(exc, AuthBlockedError):  # C15 1.5: name the tenant decision, not just "sign in"
                state = f"REAUTH_REQUIRED ({exc.state}{', ' + exc.aadsts if exc.aadsts else ''})"
            acc.auth_required = True
            acc.errors.append(f"auth {state}: {_one_line(str(exc))}")
            self.manifest.stage_cursor(src.id, None, self.run_id)
            self.manifest.set_auth_state("REAUTH_REQUIRED", [src.id])  # the manifest keeps the coarse state
            self._record_error(src, acc, state)
        except GraphThrottled as exc:
            acc.failed = True
            acc.errors.append(f"throttled: retry after {exc.retry_after:.0f}s")
            self.manifest.stage_cursor(src.id, None, self.run_id)
            self._record_error(src, acc, "throttled")
        except Exception as exc:  # isolate: this source fails, the others continue
            log.exception("source %s failed", src.id)
            acc.failed = True
            acc.errors.append(_one_line(f"{type(exc).__name__}: {exc}"))
            self.manifest.stage_cursor(src.id, None, self.run_id)
            self._record_error(src, acc, _one_line(f"{type(exc).__name__}: {exc}"))

    def _record_error(self, src: SourceConfig, acc: _SourceAcc, error: str) -> None:
        try:
            self.manifest.record_source_pass(
                self.run_id,
                src.id,
                pass_kind=acc.pass_kind,
                enumeration_complete=acc.enumeration_complete,
                cursor_reset=False,
                counts={k.value: v for k, v in acc.counts.items()},
                error=error,
            )
        except Exception:  # the manifest itself may be the problem; the report still carries the error
            log.exception("run_sources row for %s not written", src.id)

    def _scan_and_classify(
        self, src: SourceConfig, arm: SourceArm, fp_changed: set[str]
    ) -> tuple[ScanResult, PassClassification, dict[str, ItemRow], frozenset[str], frozenset[str]]:
        """Scan, then phase 1 with the H0 fast path.

        Returns (scan, classification, rows read, ids proved unchanged by the fast path, the subset of those
        whose stored last_verdict is not already UNCHANGED).  The source's rows
        are read once as light observation tuples; an observation identical to its row
        (``ClassifyContext.h0_unchanged``) is classified UNCHANGED without decoding its ``ItemRow``, and full
        rows are read only for the other observations and for known ids the pass did not observe (the
        deletion/safe-save candidates ``unseen_live`` used to return).  The local walk reuses the same index
        to skip getattrlist for files whose stat tuple did not move.
        """
        acc = self._acc(src)
        cursor_row = self.manifest.get_cursor(src.id)
        current = cursor_row.current if cursor_row is not None else None
        srow = self.manifest.get_source(src.id)
        full = (
            self.mode is CycleMode.RECONCILE
            or current is None
            or self.broke_stale
            or src.id in fp_changed
            or src.kind in (SourceKind.LOCAL, SourceKind.INBOX)
            # a drive whose first complete enumeration never finished (e.g. a resumed bootstrap) is not
            # baselined: its cursor may skip items, so keep enumerating in full until one pass completes
            or (src.kind is SourceKind.GRAPH_DRIVE and srow is not None and not srow.baseline_complete)
        )
        index = self.manifest.observation_index(src.id)
        cloud = cloud_provider_root(arm.root) if isinstance(arm, LocalArm) else None
        if isinstance(arm, LocalArm) and cloud is not None and cloud in self.held_clouds:
            alarm = (
                f"not read this pass: a walk under {cloud} timed out, so macOS is most likely waiting for "
                "you to click Allow on a privacy prompt: click Allow, then re-run the sync "
                "(nothing is deleted)"
            )
            scan = ScanResult(
                source_id=src.id,
                pass_kind=PassKind.FULL,
                items=(),
                new_cursor=None,
                enumeration_complete=False,
                alarms=(alarm,),
                listing_held=True,
            )
        else:
            if isinstance(arm, LocalArm):
                arm.known_h0 = index
            try:
                scan = arm.scan(current, full=full)
            finally:
                if isinstance(arm, LocalArm):
                    arm.known_h0 = None
        if scan.listing_held and cloud is not None:
            self.held_clouds.add(cloud)
        acc.pass_kind = scan.pass_kind
        acc.enumeration_complete = scan.enumeration_complete
        acc.alarms += list(scan.alarms)
        if self.suppressions.items or self.suppressions.globs:  # purged items must never come back (C15 7)
            kept = tuple(
                it for it in scan.items if not self.suppressions.matches(src.id, it.stable_id, it.rel_path)
            )
            if len(kept) != len(scan.items):
                log.info("%s: %d purged item(s) suppressed", src.id, len(scan.items) - len(kept))
                scan = dataclasses.replace(scan, items=kept)
        ctx = ClassifyContext(
            run_id=self.run_id, pass_kind=scan.pass_kind, written_at_ns=self.manifest.written_at_ns()
        )
        latest: dict[str, SourceItem] = {}
        for it in scan.items:
            latest[it.stable_id] = it
        fast = [it for sid, it in latest.items() if ctx.h0_unchanged(it, index.get(sid))]
        fast_ids = frozenset(it.stable_id for it in fast)
        slow = [it for it in scan.items if it.stable_id not in fast_ids]
        missing = sorted(
            sid for sid, r in index.items() if sid not in latest and not r.is_dir and r.state in _PRESENT
        )
        rows = self.manifest.get_items(src.id, [*(it.stable_id for it in slow), *missing])
        unseen = [rows[sid] for sid in missing if sid in rows]
        accepting = src.id in self.accept_deletions
        active = not accepting and self.manifest.breaker_active(src.id, _iso(self.now()))
        pc = _classify_pass(
            slow,
            rows,
            unseen,
            ctx,
            enumeration_complete=scan.enumeration_complete,
            live_rows=self.manifest.live_count(src.id),
            breaker=_NEVER_TRIPS if accepting else self.config.breaker,
            breaker_active=active,
            unchanged=fast,
        )
        for c in pc.verdicts:
            acc.counts[c.verdict] += 1
        if pc.deletion_candidates:
            acc.counts[Verdict.DELETION_CANDIDATE] += len(pc.deletion_candidates)
        acc.breaker_tripped = pc.breaker_tripped and bool(pc.deletion_candidates or active)
        log.debug("%s: %d observation(s), %d via the H0 fast path", src.id, len(latest), len(fast_ids))
        restamp = frozenset(
            sid
            for sid in fast_ids
            if (r := index.get(sid)) is not None and r.last_verdict is not Verdict.UNCHANGED
        )
        return scan, pc, rows, fast_ids, restamp

    def _note_empty_dirs(self, source_id: str, stats: WalkStats | None) -> None:
        """Store this walk's zero-child cloud folders (:data:`_EMPTY_DIRS_META`), written only on a change."""
        key = _EMPTY_DIRS_META + source_id
        found = stats.empty_cloud_dirs if stats is not None else ()
        value = json.dumps(list(found), ensure_ascii=False) if found else ""
        if (self.manifest.get_meta(key) or "") != value:
            self.manifest.set_meta(key, value)

    @staticmethod
    def _observed_state(item: SourceItem, row: ItemRow | None) -> RowState:
        """State to store for an observation (quarantine/refusal sticks until a re-conversion clears it)."""
        if row is not None and row.state in (RowState.QUARANTINED, RowState.REFUSED):
            return row.state
        return RowState.DATALESS if item.dataless else RowState.LIVE

    def _sync_source(self, src: SourceConfig, arm: SourceArm, acc: _SourceAcc, fp_changed: set[str]) -> None:
        with _gc_paused():
            scan, pc, rows, fast_ids, restamp = self._scan_and_classify(src, arm, fp_changed)
            items = {it.stable_id: it for it in scan.items}
            slow_verdicts = (
                [c for c in pc.verdicts if c.stable_id not in fast_ids] if fast_ids else pc.verdicts
            )
            rows_before: dict[str, ItemRow | None] = {
                c.stable_id: rows.get(c.stable_id) for c in slow_verdicts
            }
            moved: set[str] = set()
            renamed: set[str] = set()  # the rows of ``moved`` whose path changed
            # ---- one transaction: observations, safe-save rekeys, derived paths, pending cursor -----------
            with self.manifest.transaction():
                self.manifest.touch_observed(src.id, fast_ids, run_id=self.run_id, verdict_changed=restamp)
                observations: list[tuple[SourceItem, Verdict, RowState]] = []
                for c in slow_verdicts:
                    item = items[c.stable_id]
                    row = rows_before[c.stable_id]
                    if c.verdict is Verdict.DELETED:
                        if row is None:
                            continue  # a tombstone for an id we never had
                        state = row.state
                    else:
                        state = self._observed_state(item, row)
                        if row is not None and row.dataless and not item.dataless and not item.is_dir:
                            # downloaded since the last pass: on this Mac now, so it can be read again
                            self._reread_reopen(src)
                    observations.append((item, c.verdict, state))
                    if c.prev_path is not None or c.verdict is Verdict.METADATA_ONLY:
                        moved.add(c.stable_id)
                    if c.prev_path is not None:
                        renamed.add(c.stable_id)
                self.manifest.upsert_observed_many(observations, run_id=self.run_id, existing=rows)
                for new_id, old_id in pc.safe_saves:
                    self.manifest.rekey(src.id, old_id, new_id)
                scope_root = getattr(arm, "scope_root", None)
                if src.kind is SourceKind.GRAPH_DRIVE and callable(scope_root):
                    root_id = scope_root()
                    for stable_id, _old, _new in self.manifest.rederive_paths(
                        src.id, root_id if isinstance(root_id, str) else None
                    ):
                        moved.add(stable_id)
                        renamed.add(stable_id)
                self.manifest.stage_cursor(src.id, scan.new_cursor, self.run_id)
                acc.staged_cursor = scan.new_cursor is not None
                self.manifest.set_page_link(src.id, None)
                if scan.pass_kind is PassKind.FULL:
                    self.manifest.set_enumeration_complete(src.id, scan.enumeration_complete, self.run_id)
                if isinstance(arm, LocalArm):
                    self._note_empty_dirs(src.id, arm.last_stats)
                self.manifest.record_source_pass(
                    self.run_id,
                    src.id,
                    pass_kind=scan.pass_kind,
                    enumeration_complete=scan.enumeration_complete,
                    cursor_reset=scan.cursor_reset,
                    counts={k.value: v for k, v in acc.counts.items()},
                    skipped_reason=LISTING_HELD + "click Allow on the macOS prompt"
                    if scan.listing_held
                    else None,
                )
        if scan.listing_held:
            # Every fetch would lstat and read under the root macOS is holding, and wait as the walk did: no
            # work queue, rewrites or removals this pass (the pending rows stay queued for the next one).
            return
        # ---- work queue: fetch -> H1 -> convert -> H2 -> publish ------------------------------------------
        self.lock.beat(f"work:{src.id}")
        queue = self.manifest.pending_work(src.id)
        forced = self._forced_ids(src)
        if forced is not None:
            for sid in sorted(forced - {r.stable_id for r in queue}):
                self.manifest.set_verdict(src.id, sid, Verdict.MAYBE_CHANGED)
            queue = [r for r in self.manifest.pending_work(src.id) if r.stable_id in forced]
        complete_full = scan.pass_kind is PassKind.FULL and scan.enumeration_complete
        budget = ByteBudget(
            self.budget_bytes if self.budget_bytes is not None else src.max_materialise_bytes, src.max_files
        )
        queued = {r.stable_id for r in queue}
        # The local, inbox and drive arms have a path scope (include/exclude) and answer for a row they did
        # not list this pass.  Mail and Teams have none: every queued row of theirs is work.
        in_scope: Callable[[str], bool] | None = getattr(arm, "in_scope", None)
        work = [
            r
            for r in queue
            if not (complete_full and r.last_seen_run < self.run_id)
            and (in_scope is None or in_scope(r.rel_path))
        ]
        # (a row absent from a complete listing is a deletion candidate, not work; a row the source's
        # include/exclude no longer covers is never work, and an incomplete pass does not prune it)
        for start in range(0, len(work), _WORK_BATCH):
            self._process_batch(src, arm, work[start : start + _WORK_BATCH], budget, acc)
        self._recording_arms[src.id] = arm  # the recording pass reads what the queue left it with this arm
        if isinstance(arm, LocalArm) and not self.forced_paths:
            # After the source's own work: new and changed files had the cycle's OCR time first.  Never for
            # a Graph source (every read there is a download) and never while ``materialise PATH`` names
            # the files to read.
            try:
                self._reread_source(src, arm, acc, queued)
            except Exception as exc:
                # Reading files again is extra work on a source that has synced: what stops it is reported
                # and never fails the source, whose renames and removals are still to come.
                log.exception("%s: reading files again stopped", src.id)
                acc.errors.append(f"reading files again stopped: {type(exc).__name__}")
        acc.materialised_bytes = budget.used
        # ---- renames / metadata-only rows that needed no bytes --------------------------------------------
        for stable_id in sorted(moved - queued):
            row = self.manifest.get_item(src.id, stable_id)
            if row is None or row.is_dir or row.state is RowState.TOMBSTONE:
                continue
            if stable_id in renamed and _stubbed_for_path(row):
                # The stub holds no content to move, and its cause was the old path: read the file again.
                self.manifest.set_verdict(src.id, stable_id, Verdict.MAYBE_CHANGED)
                continue
            self._rewrite(src, row, acc)
        # ---- removals -------------------------------------------------------------------------------------
        outside: list[str] = []
        if isinstance(arm, LocalArm) and not complete_full:
            # The walk stopped short, so a file it did not list is unknown, never gone.  A path the config no
            # longer covers is another matter: no walk lists it again, complete or not.  That holds for a
            # path under the root the source has now; a row last listed under an earlier ``path`` is unknown.
            replaced = {old for _new, old in pc.safe_saves}
            since = self._root_since(src)
            outside = sorted(
                sid
                for sid, row in rows.items()
                if sid not in items
                and sid not in replaced
                and not row.is_dir
                and row.state in _PRESENT
                and row.last_seen_run >= since
                and not arm.in_scope(row.rel_path)
            )
        self._removals(src, pc, rows_before, acc, items, complete_full=complete_full, out_of_scope=outside)

    def _note_roots(self, fp_changed: set[str]) -> None:
        """Record the first run of each local or inbox source under the ``path`` it has now
        (``_SCOPE_ROOT_META``).  A row keeps the ``rel_path`` of the root it was last listed under, so a row
        last seen before that run cannot be tested against the source's globs.

        A source seen here for the first time gets 0: its rows are under this root, unless sources.toml
        changed in this very run and may have moved it."""
        for src in self.config.sources:
            if src.kind not in (SourceKind.LOCAL, SourceKind.INBOX):
                continue
            key, root = _SCOPE_ROOT_META + src.id, str(src.path)
            stored = self.manifest.get_meta(key)
            if stored is not None and stored.partition(":")[2] == root:
                continue
            first = self.run_id if stored is not None or src.id in fp_changed else 0
            self.manifest.set_meta(key, f"{first}:{root}")

    def _root_since(self, src: SourceConfig) -> int:
        """The run ``_note_roots`` recorded for ``src`` (this run when there is no readable record)."""
        since = (self.manifest.get_meta(_SCOPE_ROOT_META + src.id) or "").partition(":")[0]
        return int(since) if since.isdigit() else self.run_id

    def _process_batch(
        self, src: SourceConfig, arm: SourceArm, rows: Sequence[ItemRow], budget: ByteBudget, acc: _SourceAcc
    ) -> None:
        """Process up to ``_WORK_BATCH`` rows with their manifest writes in ONE transaction.

        Every row used to commit (and fsync, ``synchronous=FULL``) each of its five-odd writes on its own; a
        batch commits once.  Durability is unchanged in kind: the rows finished before an exception are
        committed before it propagates (as they were one statement at a time), and a process death loses at
        most one batch of manifest writes, whose pages ``recover`` verifies against the manifest next cycle
        (the rows are still pending work there, so they are processed again).
        """
        if not rows:
            return
        self.lock.beat(f"work:{src.id}")
        failure: BaseException | None = None
        with self.manifest.transaction():
            for row in rows:
                try:
                    self._process(src, arm, row, budget, acc)
                except BaseException as exc:  # commit what finished, then re-raise outside the transaction
                    failure = exc
                    break
            self._flush_changes()  # the batch's changes are durable with its outputs rows
        if failure is not None:
            raise failure

    # ---- files converted before a capability existed: read again once ------------------------------------
    def _capabilities(self) -> str:
        """Digest of what a re-read looks for: this agentsync's version, the suffixes that have a converter,
        the OCR engine's identity and what each converter's ``outdated`` goes by besides (its
        ``outdated_key``: an emitter and its floor).  ``_REREAD_META`` stores it per source.  The version is
        in it so that an upgrade starts over: a file given up under one build is tried by the next."""
        if self._capability is None:
            keys = {
                (c.converter_id, str(getattr(getattr(c, "inner", c), "outdated_key", "")))
                for c in self.registry.converters()
            }
            doc = {
                "agentsync": __version__,
                "extensions": list(self.registry.extensions()),
                "ocr": self.ocr.identity if self.ocr is not None else "",
                "rules": sorted(list(k) for k in keys if k[1]),
            }
            self._capability = hashlib.sha256(json.dumps(doc, sort_keys=True).encode("utf-8")).hexdigest()
        return self._capability

    def _lacks(self, name: str, result: ConversionResult) -> bool:
        """True when ``result`` is one this cycle's registry would read ``name`` again for: the file was
        converted without something its converter has (past the OCR time, or after the engine failed), or by
        a converter the one claiming ``name`` now replaces.  For a file only the engine reads, that result is
        the ``no converter`` refusal."""
        conv = self.registry.for_name(name)
        if conv is None:
            return False
        if result.status is ConversionStatus.REFUSED:
            return (result.reason or "").startswith(NO_CONVERTER_PREFIX)
        if result.status not in (ConversionStatus.OK, ConversionStatus.UNREADABLE):
            return False
        if result.converter_id in _replaced(conv):
            return True
        if conv.converter_id != result.converter_id:
            return False
        rule = _outdated_rule(conv)
        reason = result.reason if result.status is ConversionStatus.UNREADABLE else None
        return rule is not None and rule(result.converter_version, reason)

    def _reread_targets(
        self, source_id: str, *, recordings: bool = False
    ) -> list[tuple[str, str, str | None, str]]:
        """What to look for among ``source_id``'s files (``Manifest.reread_candidates``): each (converter
        id, version, stub reason or None, suffix) a converter of this cycle's registry calls outdated or
        replaces (for that converter's suffixes only), and each ``no converter`` refusal of a suffix that has
        a converter now.

        The suffixes of the recording converter are left to the recording pass (spec S0 rule 4), which asks
        for them alone with ``recordings``."""
        targets: set[tuple[str, str, str | None, str]] = set()
        converters, extensions = self.registry.converters(), self.registry.extensions()
        for converter_id, version, reason in self.manifest.produced_by(source_id):
            if reason is not None and reason.startswith(NO_CONVERTER_PREFIX):
                suffix = reason[len(NO_CONVERTER_PREFIX) :]
                targets.update(
                    (converter_id, version, reason, ext)
                    for ext in extensions
                    if ext.endswith(suffix) or suffix.endswith(ext)
                )
                continue
            for conv in converters:
                rule = _outdated_rule(conv)
                outdated = conv.converter_id == converter_id and rule is not None and rule(version, reason)
                if outdated or converter_id in _replaced(conv):
                    targets.update((converter_id, version, reason, ext) for ext in conv.extensions)
        mine = [t for t in targets if (t[3] in self._recording_suffixes()) == recordings]
        return sorted(mine, key=lambda t: (t[0], t[1], t[2] or "", t[3]))

    def _reread_over(self) -> bool:
        """True once this cycle starts no more re-reads: their time is used up, the cycle's OCR time is,
        the helper stopped working, or ``_REREAD_STREAK`` of them failed in a row."""
        return self._reread_s >= _REREAD_BUDGET_S or self._reread_streak >= _REREAD_STREAK or self._ocr_over()

    def _ocr_over(self) -> bool:
        """True once this cycle reads nothing more with its engine: its OCR time (``_OCR_BUDGET_S``) is
        used up, or the helper stopped working (``_CycleOcr.down``)."""
        return self.ocr is not None and (self.ocr.spent_s >= _OCR_BUDGET_S or self.ocr.down)

    def _reread_load(self, source_id: str) -> _Rereads:
        """The source's record for this cycle's capabilities, read once a cycle and kept current here.

        A record that says a file was being read was left by a cycle that died in that read (a hang the
        launcher's watchdog ended, a crash in a native library, a kill that had nothing to do with it).
        That counts as one failed read of the file: without it the next cycle would start with the same
        file, and one that kills the process would stop every sync at that source."""
        state = self._rereads.get(source_id)
        if state is None:
            mine = self._capabilities()
            state, others = _Rereads(), set[str]()
            for n, (wanted, done, failed, reading) in enumerate(
                _reread_records(self.manifest.get_meta(_REREAD_META + source_id))
            ):
                if wanted != mine:
                    others.update(failed)
                    continue
                state = _Rereads(done and n == 0, dict(failed))
                if reading is not None:
                    state.done = False
                    state.failed[reading] = state.failed.get(reading, 0) + 1
            self._rereads[source_id], self._reread_others[source_id] = state, others
        return state

    def _save_reread(self, source_id: str, *, reading: str | None = None, forget: str | None = None) -> None:
        """Store the source's ``_REREAD_META`` value, when it is not the one stored: this cycle's record,
        then the newest one written for other capabilities.  ``reading``: the file about to be read.
        ``forget``: an id to take out of the other record as well."""
        key, mine, state = _REREAD_META + source_id, self._capabilities(), self._reread_load(source_id)
        stored = self.manifest.get_meta(key)
        records: list[_RereadRecord] = [(mine, state.done and reading is None, state.failed, reading)]
        for wanted, done, failed, _reading in _reread_records(stored):
            if wanted != mine:
                failed.pop(forget or "", None)
                records.append((wanted, done, failed, None))
                break
        value = _reread_value(records)
        if stored != value:
            self.manifest.set_meta(key, value)

    def _reread_reopen(self, src: SourceConfig) -> None:
        """``src`` has a file to read again that it did not have when its record said ``done``: a file
        converted just now without something its converter has, or one that is on this Mac again.  The
        record says so at once, so the look happens even when this cycle does not get to it (a
        ``materialise PATH`` run, a source that fails further on)."""
        if src.kind.is_graph:
            return
        state = self._reread_load(src.id)
        if state.done:
            state.done = False
            self._save_reread(src.id)

    def _reread_forget(self, src: SourceConfig, stable: str) -> None:
        """The work queue converted ``stable`` anew, so what its re-reads did says nothing about the file
        any more: its bytes are others.  Without this a file given up once stayed given up through every
        later change, and one converted without OCR after such a change was never read again."""
        if src.kind.is_graph:
            return
        state = self._reread_load(src.id)
        if stable in state.failed or stable in self._reread_others[src.id]:
            state.failed.pop(stable, None)
            self._reread_others[src.id].discard(stable)
            self._save_reread(src.id, forget=stable)

    def _reread_note(self, source_id: str, stable: str, outcome: bool | None) -> None:
        """Count what one re-read of ``stable`` came to (``_reread``): True clears what it failed before,
        False is one more failed cycle, None (not now) is neither.  A read the helper stopped working in
        was not the file's to lose, and is not counted."""
        state = self._reread_load(source_id)
        if outcome is True:
            state.failed.pop(stable, None)
            self._reread_streak = 0
        elif outcome is False and not (self.ocr is not None and self.ocr.down):
            state.failed[stable] = state.failed.get(stable, 0) + 1
            self._reread_streak += 1

    def _reread_source(self, src: SourceConfig, arm: LocalArm, acc: _SourceAcc, queued: set[str]) -> None:
        """Read again the files of ``src`` whose pages were made before something this cycle's registry has
        (CONTRACTS.md 16.27): a ``no converter`` stub of a type that has a converter now, a page or a ``no
        text layer`` stub written without OCR when there is an engine, a PDF page from before comments were
        kept, a page of the field build of OCR.

        Only a file this pass listed and left unchanged, in scope and on this Mac, is read, and ``queued``
        (the rows the pass's own work held) wait for the next pass.  No row is marked and nothing is
        downloaded: a file is read with its hashes in place, so whatever stops the cycle leaves it as it
        was.  A read either gives the file's pages a version that lacks nothing or is counted against the
        file; after ``_REREAD_ATTEMPTS`` cycles in which it failed, the file is among the source's
        ``tried`` and is not read again for what the registry has."""
        state = self._reread_load(src.id)
        self._reread_looked = True  # from here the source's record is one for this cycle's capabilities
        if state.done:
            return  # nothing was left to read again, and this cycle found nothing either (_reread_reopen)
        targets = self._reread_targets(src.id)
        kept, after, more = self._reread_kept, "", bool(targets)
        while more and not self._reread_over():
            rows = self.manifest.reread_candidates(
                src.id, targets, seen_run=self.run_id, skip=state.tried(), after=after, limit=_REREAD_BATCH
            )
            if not rows:
                break
            after = rows[-1].stable_id
            batch = [r for r in rows if r.stable_id not in queued and arm.in_scope(r.rel_path)]
            more = self._reread_batch(src, arm, batch, acc)
        if self._reread_kept > kept:
            acc.alarms.append(
                f"{self._reread_kept - kept} file(s) read again for what their converter has gained could "
                "not be converted; their pages are kept as they were"
            )
        # Done when no file on this Mac is left to read again: none the cycle's time ran out before, none
        # that is pending, none this pass did not list and none whose read failed fewer times than it may.
        left = self.manifest.reread_count(src.id, targets, skip=state.tried()) if targets else 0
        self._reread_left += left
        state.done = not left
        self._save_reread(src.id)

    def _reread_batch(
        self, src: SourceConfig, arm: LocalArm, rows: Sequence[ItemRow], acc: _SourceAcc
    ) -> bool:
        """Read ``rows`` again, each in a manifest transaction of its own.  False when the cycle stopped
        starting re-reads before the last of them.

        Before a row is read the record says so in a write of its own, which is committed on its own
        (``reading``); the row's transaction clears it and stores what the read came to.  So a read that
        raises is counted, and so is one the process died in (``_reread_load``)."""
        self.lock.beat(f"work:{src.id}")
        for row in rows:
            if self._reread_over():
                return False
            self._save_reread(src.id, reading=row.stable_id)
            failure: BaseException | None = None
            outcome: bool | None = False
            started = _reread_clock()
            with self.manifest.transaction():
                try:
                    outcome = self._reread(src, arm, row, acc)
                except BaseException as exc:  # commit what finished, then re-raise outside the transaction
                    failure = exc
                    # Its pages may be half written: as pending work the next pass reads it, checks them
                    # against the manifest and publishes them again when they do not match.
                    with contextlib.suppress(Exception):
                        self.manifest.set_verdict(row.source_id, row.stable_id, Verdict.MAYBE_CHANGED)
                finally:
                    self._reread_s += _reread_clock() - started
                self._reread_note(src.id, row.stable_id, outcome)
                self._flush_changes()
                self._save_reread(src.id)
            if failure is not None:
                raise failure
        return True

    def _runs(self, conv: Converter) -> bool:
        """False when ``conv`` cannot run at all in this cycle (its ``version()`` raises: pandoc is
        missing).  Asked once a cycle per converter; one WARNING names it."""
        if id(conv) not in self._cannot_run:
            try:
                conv.version()
            except Exception as exc:
                self._cannot_run[id(conv)] = True
                log.warning(
                    "%s cannot run (%s): no file is read again for it in this cycle",
                    conv.converter_id,
                    type(exc).__name__,
                )
            else:
                self._cannot_run[id(conv)] = False
        return not self._cannot_run[id(conv)]

    def _reread(self, src: SourceConfig, arm: LocalArm, row: ItemRow, acc: _SourceAcc) -> bool | None:
        """Read ``row`` again and convert it with this cycle's registry.

        True: done with, its pages come from a version that lacks nothing.  False: it still lacks what it
        was read for (its conversion failed and the page was kept, the engine failed on it again), which
        counts against the file.  None: not now, for a reason that is no failure of this file, so a later
        cycle asks again: its converter cannot run at all (pandoc is missing), the file has gone or been
        evicted since the walk listed it, or it could not be read (it never reached a converter).

        Nothing is downloaded: the fetch has a budget of no bytes, so a file evicted since the walk is left
        alone.  A file the work queue refuses unread (an excluded label, an inbox copy of a Graph file) is
        not read here either."""
        conv = self.registry.for_name(row.name)
        if (
            conv is None
            or self.publisher.policy_refusal(row) is not None
            or self._inbox_name_size_duplicate(src, row) is not None
        ):
            return False
        if not self._runs(conv):
            return None
        try:
            fetched = arm.fetch(_item_from_row(row), self.staging, ByteBudget(0, 1))
        except (BudgetExhaustedError, DatalessRefusedError, FileNotFoundError):
            return None  # evicted or gone since the walk: the next pass says what the file is now
        except Exception as exc:  # unreadable for now: the row and its page are as they were
            log.debug("a file could not be read again (%s); its page is as it was", type(exc).__name__)
            return None
        self._reread_n += 1
        try:
            result = self._after_fetch(src, row, fetched, acc, reread=True)
        finally:
            _discard_staged(fetched, self.staging)
        return result is not None and not self._lacks(row.name, result)

    def _outdated_in_queue(self, src: SourceConfig, row: ItemRow) -> bool:
        """True when the work queue holds ``row`` with the bytes its pages were made from and those pages
        are ones to read the file again for (``_reread_targets``).

        ``_reread_source`` reads only a file the pass listed unchanged.  A file the pass calls maybe-changed
        every time (a volume that reports no generation count) is in the work queue every cycle and never
        is one, so it would never gain what its converter has.  The queue has the file in hand: it is
        converted there.  Under the same limits: never for a Graph source or a ``materialise PATH`` run,
        not once the cycle starts no more re-reads, and not a file that was given up."""
        if src.kind.is_graph or self.forced_paths or self._reread_over():
            return False
        state = self._reread_load(src.id)
        if state.done or state.failed.get(row.stable_id, 0) >= _REREAD_ATTEMPTS:
            return False
        if src.id not in self._queue_targets:
            self._queue_targets[src.id] = self._reread_targets(src.id)
        targets = self._queue_targets[src.id]
        if not targets or not self.manifest.reread_left(src.id, targets, only=row.stable_id):
            return False
        conv = self.registry.for_name(row.name)  # asked last: whether it runs can start a process
        return conv is not None and self._runs(conv)

    # ---- recordings: a pass of their own (spec S0 rules 3 to 8, 4.1) -------------------------------------
    def _recording(self, name: str) -> bool:
        """True when this cycle's registry gives ``name`` to the recording converter."""
        conv = self.registry.for_name(name)
        return conv is not None and conv.converter_id == RecordingConverter.converter_id

    def _recording_suffixes(self) -> frozenset[str]:
        """The suffixes this cycle's registry gives the recording converter: none without a media helper."""
        return frozenset(
            ext
            for conv in self.registry.converters()
            if conv.converter_id == RecordingConverter.converter_id
            for ext in conv.extensions
        )

    def _recording_defer(self, src: SourceConfig, row: ItemRow, acc: _SourceAcc) -> None:
        """Leave a recording of a source's queue to the recording pass, unfetched (spec S0 rule 4).  It
        waits (``RECORDING_WAITS``), or keeps ``HYDRATION_REFUSED`` until its download is tried again.  An
        online-only recording that has the page it was given while it was on this Mac keeps it
        (``_keeps_page``), unless its bytes may be new: a version that waited for the recording pass (its
        reason says so) or one whose verdict says its bytes changed, which waits for the recording download
        (ruling R1).  A ``materialise PATH`` run makes what it names maybe-changed without new bytes."""
        new_bytes = row.state_reason in (RECORDING_WAITS, HYDRATION_REFUSED) or (
            row.last_verdict in _NEW_BYTES and not self.forced_paths
        )
        if row.dataless and not new_bytes and self._keeps_page(row, acc):
            return
        self.manifest.set_verdict(row.source_id, row.stable_id, Verdict.DEFERRED)
        acc.deferred += 1
        acc.counts[Verdict.DEFERRED] += 1
        waits = row.state in (RowState.LIVE, RowState.DATALESS) and row.state_reason is None
        if waits:  # never over a quarantine's or a refused download's own reason
            self.manifest.set_state(row.source_id, row.stable_id, row.state, RECORDING_WAITS)
        self._recording_queue.setdefault(src.id, []).append(row.stable_id)

    def _recording_load(self, source_id: str) -> _Recordings:
        """The source's recording record, read once a cycle and kept current here.  A ``reading`` mark in it
        was left by a cycle that died in that read (a watchdog, a crash, a shutdown): one failed read of that
        recording, stored at once (spec S0 rule 7)."""
        record = self._recording_records.get(source_id)
        if record is None:
            record = _Recordings.parse(self.manifest.get_meta(_RECORDING_META + source_id))
            self._recording_records[source_id] = record
            if record.reading is not None:
                record.fail(record.reading, self._read_to(record.staged.get(record.reading)))
                self._save_recording(source_id)
        return record

    def _read_to(self, sha: str | None) -> int:
        """The media time (ms) the piece store holds of the recording staged under ``sha``: where its read
        stopped."""
        progress = self.pieces.progress(sha) if sha else None
        return progress[0] if progress is not None else 0

    def _save_recording(self, source_id: str, *, reading: str | None = None) -> None:
        """Store the source's ``_RECORDING_META`` value when it is not the one stored.  ``reading``: the
        recording about to be read; outside a transaction the write is committed on its own."""
        record = self._recording_load(source_id)
        record.reading = reading
        key, value = _RECORDING_META + source_id, record.value()
        stored = self.manifest.get_meta(key)
        if (stored or "") != value:
            self.manifest.set_meta(key, value)

    def _set_progress(self, source_id: str, stable: str, value: str) -> None:
        """Store ``RECORDING_PROGRESS_META`` of one recording (``""``: nothing is read of it now)."""
        key = f"{RECORDING_PROGRESS_META}{source_id}:{stable}"
        stored = self.manifest.get_meta(key)
        if stored != value and (stored is not None or value):
            self.manifest.set_meta(key, value)

    def _recordings_down(self) -> bool:
        """True once no recording is staged in this cycle: the media helper stopped answering, or the OCR
        helper stopped working (``_CycleOcr.down``).  A recording is read by both."""
        return self._media_down or (self.ocr is not None and self.ocr.down)

    def _helper_failed(self, name: str, result: ConversionResult) -> bool:
        """True when ``result`` is the ``no converter`` refusal a recording gets after a MediaError (or an
        OcrError) and the error was a helper's, not the file's (spec S0 rule 8): the OCR helper is down, or
        the media helper does not answer ``--version`` (5 s), which marks it down for the cycle."""
        refused = result.status is ConversionStatus.REFUSED and (result.reason or "").startswith(
            NO_CONVERTER_PREFIX
        )
        if not refused or not self._recording(name):
            return False
        if not self._recordings_down() and self.media is not None and not self.media.alive():
            self._media_down = True
            log.warning("%s", _MEDIA_DOWN)
        return self._recordings_down()

    def _recording_pass(self) -> None:
        """Read recordings, once every source has had its queue and its re-reads (spec S0 rule 4).

        A recording with stored pieces first (the one started earliest first), then new and changed ones,
        then online-only ones, then the stubs a re-read would pick (a ``no converter`` stub from before
        recordings were read, a page the converter calls outdated), each group in source order, then stable
        id.  Only a run no tool timeout ends reads one (rule 5): a background cycle works pieces of one
        recording at a time until ``_RECORDING_BUDGET_S`` of recording work is used, the piece in flight
        finishing, and ``agentsync materialise PATH`` reads each recording it names to the end, one after the
        other.  An interactive ``sync`` reads none: its queues left them waiting.  No recording is staged
        once a helper stopped working (rule 8)."""
        if self.recordings == "none" or not self._recording_suffixes():
            return
        candidates = self._recording_candidates()
        if not candidates:
            return
        self.lock.beat("recordings")
        self._in_recording_pass = True
        try:
            with recording_mod.work_allowance(
                None if self.recordings == "named" else _RECORDING_BUDGET_S
            ) as allowance:
                fetches = 0
                for src, arm, row, stub in candidates:
                    if allowance.used_up or self._recordings_down():
                        break
                    online = row.dataless or src.kind.is_graph
                    if online and not self._may_download(src, row, fetches):
                        continue
                    fetches += online
                    try:
                        self._read_recording(src, arm, row, stub=stub, online=online, allowance=allowance)
                    except Exception as exc:  # one recording's trouble: reported, never the source's failure
                        log.exception("%s: reading a recording stopped", src.id)
                        self._acc(src).errors.append(f"reading a recording stopped: {type(exc).__name__}")
        finally:
            self._in_recording_pass = False

    def _recording_candidates(self) -> list[tuple[SourceConfig, SourceArm, ItemRow, bool]]:
        """(source, arm, row, stub path) of every recording the pass may read, in the order it reads them
        (``_recording_pass``).  The stub path is taken only for a local or inbox file the pass listed
        unchanged and in scope, on this Mac, and never in a ``materialise PATH`` run."""
        order = {src.id: n for n, src in enumerate(self.selected)}
        ranked: list[tuple[tuple[int, int, int, str], SourceConfig, SourceArm, ItemRow, bool]] = []
        for src in self.selected:
            arm = self._recording_arms.get(src.id)
            if arm is None:
                continue
            record = self._recording_load(src.id)
            queued = self.manifest.get_items(src.id, self._recording_queue.get(src.id, ()))
            picked = [(row, False) for row in queued.values()]
            targets = self._reread_targets(src.id, recordings=True) if not self.forced_paths else []
            after = ""
            while isinstance(arm, LocalArm) and targets:
                rows = self.manifest.reread_candidates(
                    src.id, targets, seen_run=self.run_id, after=after, limit=_WORK_BATCH
                )
                if not rows:
                    break
                after = rows[-1].stable_id
                picked += [(r, True) for r in rows if r.stable_id not in queued and arm.in_scope(r.rel_path)]
            for row, stub in picked:
                online = row.dataless or src.kind.is_graph
                if online and not self._recording_downloads():
                    continue  # it waits for a run that may download it (rule 3)
                sha = row.canonical_sha256
                if not online and sha and self.pieces.progress(sha) is not None:
                    key = (0, record.started.get(row.stable_id, self.run_id))
                elif online:
                    key = (2, -(row.mtime_ns or 0))  # newest first
                else:
                    key = (3 if stub else 1, 0)
                ranked.append(((*key, order[src.id], row.stable_id), src, arm, row, stub))
        ranked.sort(key=lambda r: r[0])
        return [(src, arm, row, stub) for _key, src, arm, row, stub in ranked]

    def _recording_downloads(self) -> bool:
        """True when this cycle's recording pass may download a recording (spec S0 rule 3, ruling 1): only a
        reconcile job or ``agentsync materialise PATH``; never a poll cycle or an interactive sync."""
        return self.recordings == "named" or (
            self.recordings == "background" and self.mode is CycleMode.RECONCILE
        )

    def _may_download(self, src: SourceConfig, row: ItemRow, fetches: int) -> bool:
        """True when the pass downloads ``row`` now (spec S0 rule 3).  Not past ``_RECORDING_FETCHES`` in a
        background cycle; never one over ``_RECORDING_MAX_BYTES`` (it is counted as one that could not be
        downloaded); not one whose download failed in ``_REREAD_ATTEMPTS`` cycles while its stat and
        online-only flag are as they were then, unless ``materialise PATH`` names it; and only while the
        volume keeps twice its size and ``_RECORDING_SPARE_BYTES`` free."""
        size = max(row.size or 0, 0)
        if size > _RECORDING_MAX_BYTES:
            self._download_refused(src, row, count=False)
            return False
        if self.recordings == "background" and fetches >= _RECORDING_FETCHES:
            return False
        tries, key = self._recording_load(src.id).fetch.get(row.stable_id, (0, ""))
        if not self.forced_paths and tries >= _REREAD_ATTEMPTS and key == _stat_key(row):
            return False
        if _free_bytes(self.staging) < 2 * size + _RECORDING_SPARE_BYTES:
            log.info("%s: a recording waits for free disk space before it is downloaded", src.id)
            return False
        return True

    def _download_refused(self, src: SourceConfig, row: ItemRow, *, count: bool = True) -> None:
        """The OS refused a recording's download (``errno 89``, a policy), it passed its deadline, or it is
        too large: ``HYDRATION_REFUSED``, never rule 3.  ``count``: one more failed download at this stat, so
        it is tried once more in a later reconcile, then only when the stat changes.  A ``materialise PATH``
        run that names it says what to do in Finder."""
        sid, stable = src.id, row.stable_id
        if count:
            record = self._recording_load(sid)
            tries, key = record.fetch.get(stable, (0, ""))
            record.fetch[stable] = (tries + 1 if key == _stat_key(row) else 1, _stat_key(row))
            self._save_recording(sid)
        fresh = self.manifest.get_item(sid, stable) or row
        if fresh.state in (RowState.LIVE, RowState.DATALESS) and fresh.state_reason != HYDRATION_REFUSED:
            self.manifest.set_state(sid, stable, fresh.state, HYDRATION_REFUSED)
        if self.forced_paths:
            self._acc(src).alarms.append(_RECORDING_NOT_DOWNLOADED.format(path=row.rel_path))

    def _read_recording(
        self,
        src: SourceConfig,
        arm: SourceArm,
        row: ItemRow,
        *,
        stub: bool,
        online: bool = False,
        allowance: recording_mod.Allowance | None = None,
    ) -> None:
        """Read one recording in a manifest transaction of its own (spec S0 rule 7).

        Before it is read the source's record says so in a write committed on its own (``reading``); the
        recording's transaction clears it and stores what the read came to.  So a read that raises is
        counted, and so is one the process died in (``_recording_load``).  ``stub``: the stub path, where a
        read that fails keeps the page or stub the recording has (``_after_fetch`` with ``reread``).
        ``online``: reading it is a download, under an allowance of its own (never the source's
        ``ByteBudget``) and a deadline; a refusal or the deadline makes it ``HYDRATION_REFUSED``.
        ``allowance``: the pass's recording allowance, charged for the staging copy and for the work of a
        Graph recording read to the end under an allowance of its own (spec S0 rule 5).

        The canonical hash of the staged bytes goes into the record (``staged``) in a write committed on its
        own, before the read: a kill rolls back the hash the read stores on the row, and the pieces it left
        are found by this one."""
        sid, stable, acc = src.id, row.stable_id, self._acc(src)
        record = self._recording_load(sid)
        if self.forced_paths:
            record.forget(stable)  # ``materialise PATH`` names it: its count starts again (O13)
        elif record.failed.get(stable, 0) >= _REREAD_ATTEMPTS:
            with self.manifest.transaction():
                self._recording_given_up(src, row, acc)
                self._flush_changes()
                self._save_recording(sid)
            return
        self._save_recording(sid, reading=stable)
        item = _item_from_row(row)
        started = _recording_clock()
        try:
            if online:
                deadline = _RECORDING_DEADLINE_S + max(row.size or 0, 0) / _RECORDING_FLOOR_BPS
                budget = ByteBudget(_RECORDING_MAX_BYTES, 1)
                fetched = _fetch_by(deadline, lambda: arm.fetch(item, self.staging, budget))
            else:
                fetched = arm.fetch(item, self.staging, ByteBudget(0, 1))
        except Exception as exc:  # gone, evicted or unreadable since the walk: the next pass says what it is
            self._save_recording(sid)
            if isinstance(exc, AuthRequiredError):
                raise
            refused = (DatalessRefusedError, ProviderTimeoutError, PermissionError, _DownloadDeadline)
            if online and isinstance(exc, refused):
                log.info("%s: an online-only recording could not be downloaded (%s)", sid, type(exc).__name__)
                self._download_refused(src, row)
            else:
                log.debug("a recording could not be read (%s); it waits", type(exc).__name__)
            return
        finally:
            _charge(allowance, started)
        if online:  # on this Mac now: what a refused download left on the row is void
            record.fetch.pop(stable, None)
            if row.state_reason == HYDRATION_REFUSED:
                self.manifest.set_state(sid, stable, row.state, None)
        started = _recording_clock()
        try:
            h1 = canonical_hash(fetched.path, suffix=item.suffix)
        except OSError as exc:  # gone or unreadable since it was staged: the next pass says what it is
            log.debug("a staged recording could not be hashed (%s); it waits", type(exc).__name__)
            _discard_staged(fetched, self.staging)
            self._save_recording(sid)
            return
        finally:
            _charge(allowance, started)
        record.staged[stable] = h1.sha256
        self._save_recording(sid, reading=stable)
        with self.manifest.transaction():
            try:
                # A Graph recording is read to the end in the run that downloaded it: nothing keeps its bytes
                # for a later cycle, which would download it again (one recording is under 900 s of work)
                whole: contextlib.AbstractContextManager[recording_mod.Allowance | None] = (
                    recording_mod.work_allowance(None) if src.kind.is_graph else contextlib.nullcontext()
                )
                with whole as inner:
                    try:
                        result = self._after_fetch(src, row, fetched, acc, reread=stub, h1=h1)
                    finally:
                        if inner is not None and allowance is not None:
                            allowance.spent_s += inner.spent_s
            except RecordingNotFinished as signal:
                self._recording_waits(src, row, acc, signal)
            except _HelperDown:  # no file's failure: the recording waits as it was, its read not counted
                if self._media_down and _MEDIA_DOWN not in acc.alarms:
                    acc.alarms.append(_MEDIA_DOWN)
                if self.pieces.progress(h1.sha256) is None:
                    record.staged.pop(stable, None)  # nothing was stored under it
            except Exception as exc:  # its pages may be half written: as pending work they are checked again
                log.warning("%s: a recording's read failed (%s)", sid, type(exc).__name__)
                acc.errors.append(_one_line(f"{row.rel_path}: {type(exc).__name__}: {exc}"))
                self.manifest.set_verdict(sid, stable, Verdict.MAYBE_CHANGED)
                self._recording_failed(src, row, acc)
            else:
                self._recording_read(src, row, acc, result, stub=stub)
            finally:
                _discard_staged(fetched, self.staging)
            self._flush_changes()
            self._save_recording(sid)

    def _recording_waits(
        self, src: SourceConfig, row: ItemRow, acc: _SourceAcc, signal: RecordingNotFinished
    ) -> None:
        """The read stopped with pieces left (spec 4.1): the recording waits, never failed (ruling 4), with
        how much of it is read in ``RECORDING_PROGRESS_META``.  A piece that passed its deadline is one failed
        read, and a second such cycle gives the recording up (O13)."""
        sid, stable = src.id, row.stable_id
        record = self._recording_load(sid)
        record.started.setdefault(stable, self.run_id)
        if signal.timed_out and self._recording_failed(src, row, acc, at=signal.done_ms):
            return
        fresh = self.manifest.get_item(sid, stable) or row
        if fresh.state in (RowState.LIVE, RowState.DATALESS) and fresh.state_reason is None:
            self.manifest.set_state(sid, stable, fresh.state, RECORDING_WAITS)
        self._set_progress(sid, stable, f"{signal.done_ms} {signal.total_ms}")

    def _recording_read(
        self, src: SourceConfig, row: ItemRow, acc: _SourceAcc, result: ConversionResult | None, *, stub: bool
    ) -> None:
        """Settle what one finished read came to.  A failed read (the converter failed, or the media helper
        failed on this file while it still answers) counts against the recording; a first read that came to
        no conversion (None: its content is a purged item's) settles nothing; any other read is done with:
        it waits no more, and its pieces go now that its page is in the converter cache (4.1)."""
        sid, stable = src.id, row.stable_id
        if stub:  # None: the read failed and the page or stub it had is kept
            failed = result is None or self._lacks(row.name, result)
        elif result is None:
            return
        else:
            failed = result.status is ConversionStatus.FAILED or self._lacks(row.name, result)
        if failed:
            self._recording_failed(src, row, acc)
            return
        record = self._recording_load(sid)
        record.forget(stable)
        staged = record.staged.pop(stable, None)
        fresh = self.manifest.get_item(sid, stable)
        if fresh is not None and fresh.state_reason == RECORDING_WAITS:
            self.manifest.set_state(sid, stable, fresh.state, None)
        self._set_progress(sid, stable, "")
        sha = (result.canonical_sha256 if result is not None else "") or (
            fresh.canonical_sha256 if fresh is not None else None
        )
        for done in {sha, staged}:
            if done:
                self.pieces.remove(done)

    def _recording_failed(
        self, src: SourceConfig, row: ItemRow, acc: _SourceAcc, *, at: int | None = None
    ) -> bool:
        """Count one failed read of a recording at the piece after ``at`` ms (by default where its stored
        pieces end); True when that was the second failure of that piece and it is given up."""
        record = self._recording_load(src.id)
        if at is None:
            at = self._read_to(record.staged.get(row.stable_id))
        if record.fail(row.stable_id, at) < _REREAD_ATTEMPTS:
            return False
        self._recording_given_up(src, row, acc)
        return True

    def _recording_given_up(self, src: SourceConfig, row: ItemRow, acc: _SourceAcc) -> None:
        """Settle a recording whose read stopped or failed in two cycles as the O13 stub, on the first-read
        path and the stub path alike.  Its stored pieces are kept, so ``agentsync materialise PATH`` resumes
        from them; the count starts again with the stub."""
        sid, stable = src.id, row.stable_id
        fresh = self.manifest.get_item(sid, stable) or row
        stub = self._stub(fresh, ConversionStatus.UNREADABLE, _RECORDING_STOPPED)
        self._publish(src, fresh, stub, acc, quarantine_reason=_RECORDING_STOPPED)
        self._recording_load(sid).forget(stable)
        self._set_progress(sid, stable, "")
        log.warning("%s: a recording whose read stopped in two syncs was given up", sid)

    def _prune_pieces(self) -> None:
        """Reconcile's clean-up of the piece store (spec 4.1): a recording's folder goes once no row waits on
        its hash.  A recording that waits, is read in part behind its stub, or was given up (O13) keeps its
        pieces, and so does the hash an unfinished read was staged under (``staged``: a kill rolls back the
        row's), while the row is there; the record forgets the others.  Never in a cycle without the
        recording converter, which cannot tell what is a recording."""
        if not self._recording_suffixes() or not self.pieces.pending():
            return
        keep: set[str] = set()
        for src in self.config.sources:
            record, present = self._recording_load(src.id), set[str]()
            for row in self.manifest.iter_items(src.id, states=_PRESENT):
                if not self._recording(row.name):
                    continue
                present.add(row.stable_id)
                staged, sha = record.staged.get(row.stable_id), row.canonical_sha256
                if staged:
                    keep.add(staged)
                if sha and (
                    row.state_reason in (RECORDING_WAITS, _RECORDING_STOPPED)
                    or _no_converter_stub(row)
                    or row.last_verdict is Verdict.DEFERRED
                ):
                    keep.add(sha)
            for stable in set(record.staged) - present:
                del record.staged[stable]
            self._save_recording(src.id)
        removed = self.pieces.prune(keep)
        if removed:
            log.info("piece store: %d recording folder(s) no row waits on removed", removed)

    def _forced_ids(self, src: SourceConfig) -> set[str] | None:
        """Stable ids named by ``agentsync materialise PATH`` for this source (None = no restriction)."""
        if not self.forced_paths:
            return None
        wanted = self.forced_paths.get(src.id, set())
        out: set[str] = set()
        for rel in sorted(wanted):
            row = self.manifest.item_by_path(src.id, rel)
            if row is None:
                self._acc(src).alarms.append(f"materialise: {rel} is not a known file of {src.id}")
            elif not row.is_dir:
                out.add(row.stable_id)
        return out

    def _rewrite(self, src: SourceConfig, row: ItemRow, acc: _SourceAcc) -> bool:
        """Re-render ``row``'s pages for the path and metadata it has now, bodies kept (a rename, a
        METADATA_ONLY change).

        False when the pages could not follow the file because a sidecar has no name that fits beside the
        new path: the item is then published as the stub ``_publish`` writes for such a page, and the caller
        must not mark the row unchanged.  Any other failure is logged and the row queued for a re-fetch."""
        outs = self.manifest.outputs_for(row.source_id, row.stable_id)
        if not any(o.status is not OutputStatus.TOMBSTONE for o in outs):
            return True
        try:
            self.changes += self.publisher.rewrite_frontmatter(row, self.run_id)
        except SidecarPathError as exc:
            self._publish_path_stub(src, row, acc, exc)
            return False
        except PublishError as exc:
            log.warning("%s/%s: frontmatter rewrite failed (%s); queued for re-fetch", *_row_ids(row), exc)
            self.manifest.set_verdict(row.source_id, row.stable_id, Verdict.MAYBE_CHANGED)
        return True

    def _removals(
        self,
        src: SourceConfig,
        pc: PassClassification,
        rows_before: dict[str, ItemRow | None],
        acc: _SourceAcc,
        scan_items: dict[str, SourceItem] | None = None,
        *,
        complete_full: bool = False,
        out_of_scope: Sequence[str] = (),
    ) -> None:
        """Apply the pass's removals.  ``out_of_scope``: present rows of a local or inbox source that an
        incomplete pass did not list and whose path fails the arm's ``in_scope``."""
        scan_items = scan_items or {}
        today = self.today()
        if src.kind in (SourceKind.LOCAL, SourceKind.INBOX):
            self.manifest.clear_absent_marks(src.id, self.run_id)
        scope_key = _SCOPE_CHANGE_META + src.id
        scope_changed = self.manifest.get_meta(scope_key) not in (None, "")
        removals: list[tuple[str, str]] = []
        for c in pc.verdicts:
            if c.verdict is not Verdict.DELETED or c.reason != "provider-tombstone":
                continue
            row = self.manifest.get_item(src.id, c.stable_id)
            if row is None or row.state is RowState.TOMBSTONE or rows_before.get(c.stable_id) is None:
                continue
            item = scan_items.get(c.stable_id)
            extra = {**dict(row.extra), **(dict(item.extra) if item is not None else {})}
            why = str(extra.get("removed") or extra.get("removed_reason") or "")
            reason = _removal_reason(why)
            removals.append((c.stable_id, reason))
            if row.is_dir:
                # A folder moved out of scope (or deleted) arrives as ONE delta record; its descendants
                # produce none.  They left with it: same reason, or they would stay live until a FULL pass
                # read their absence as an upstream deletion and queued purges (review correctness-folder).
                removals += [
                    (d.stable_id, reason)
                    for d in self.manifest.descendants(src.id, c.stable_id)
                    if d.state in _PRESENT and d.stable_id not in scan_items
                ]
        if out_of_scope:
            # An incomplete pass has no deletion candidates, and absence from it proves nothing.  These rows
            # are retired on the config alone, as the complete pass below retires them: same reason, exempt
            # from the breaker, no purge.  Left alone they would stay pending work for ever on a source whose
            # walk never completes.
            removals += [(sid, _SCOPE_CHANGE_REASON) for sid in out_of_scope]
            acc.alarms.append(_scope_change_alarm(len(out_of_scope)))
        if pc.deletion_candidates and scope_changed:
            # sources.toml narrowed this source (path/folder, include/exclude): the files still exist
            # upstream, the operator took them out of scope.  Retire their pages (exempt from the breaker, as
            # retirement is), never "deleted upstream", never a purge (review correctness-scope-change).
            removals += [(sid, _SCOPE_CHANGE_REASON) for sid in pc.deletion_candidates]
            acc.alarms.append(_scope_change_alarm(len(pc.deletion_candidates)))
        elif pc.deletion_candidates:
            if pc.breaker_tripped:
                now = self.now()
                if not self.manifest.breaker_active(src.id, _iso(now)):
                    until = _iso(now + timedelta(days=self.config.breaker.hold_days))
                    self.manifest.trip_breaker(
                        src.id, candidates=len(pc.deletion_candidates), tripped_at=_iso(now), until=until
                    )
                acc.breaker_tripped = True
                acc.alarms.append(
                    f"deletion breaker TRIPPED: {len(pc.deletion_candidates)} absent file(s) held, nothing "
                    f"removed; if the deletion is real run `agentsync accept-deletions {src.id}` or retire "
                    "the source"
                )
            else:
                confirmed = self._confirmed_absent(src, pc.deletion_candidates, rows_before, acc)
                removals += [(sid, "deleted-upstream") for sid in confirmed]
                srow = self.manifest.get_source(src.id)
                if srow is not None and srow.breaker_tripped_at is not None:
                    self.manifest.clear_breaker(src.id)
        elif src.id in self.accept_deletions:
            srow = self.manifest.get_source(src.id)
            if srow is not None and srow.breaker_tripped_at is not None:
                self.manifest.clear_breaker(src.id)
        if scope_changed and complete_full:
            self.manifest.set_meta(scope_key, "")
        last_commit = gitops.head_sha(self.repo) if removals else None
        for stable_id, reason in sorted(set(removals)):
            self.changes += self.publisher.tombstone(
                src.id,
                stable_id,
                reason=reason,
                run_id=self.run_id,
                today=today,
                last_commit=last_commit,
                archive=self.gov.archive,
            )
            if reason == "deleted-upstream" and self.gov.purge_on_upstream_delete:
                # C15 req 38: a confirmed deletion (explicit, or absent past the breaker) queues a purge; the
                # history rewrite itself runs only from `agentsync purge --queue` (operator-scheduled).
                governance.enqueue_purge(
                    self.config.state_paths.root,
                    governance.PurgeSelector(source_id=src.id, stable_id=stable_id),
                    governance.PurgeReason.UPSTREAM_DELETED,
                    now=self.now(),
                )
        self.changes += self.publisher.reap(today)

    def _confirmed_absent(
        self,
        src: SourceConfig,
        candidates: Sequence[str],
        rows_before: dict[str, ItemRow | None],
        acc: _SourceAcc,
    ) -> list[str]:
        """Local/inbox: a file must be absent from TWO complete passes before it is tombstoned (and its purge
        queued).  One walk can land inside an Office save sequence (the original renamed to an excluded
        ``~WRL*.tmp``, its replacement not yet in place); the next pass then pairs the new inode with the
        old row as a safe-save instead of splitting the document.  Graph sources delete on one pass (their
        ids are stable; absence there is not a save artefact)."""
        if src.kind not in (SourceKind.LOCAL, SourceKind.INBOX) or src.id in self.accept_deletions:
            return list(candidates)
        rows = self.manifest.get_items(src.id, candidates)
        confirmed: list[str] = []
        first: list[str] = []
        for sid in candidates:
            row = rows.get(sid) or rows_before.get(sid)
            seen_absent = row.extra.get("absent_since_run") if row is not None else None
            if isinstance(seen_absent, int) and seen_absent < self.run_id:
                confirmed.append(sid)
            else:
                first.append(sid)
        if first:
            self.manifest.mark_absent(src.id, first, self.run_id)
            acc.alarms.append(
                f"{len(first)} file(s) absent from this complete pass: removed only if still absent next pass"
            )
        return confirmed

    def _retire(self, src: SourceConfig, acc: _SourceAcc) -> None:
        """Retirement: tombstone the whole source in this (labelled) commit, exempt from the breaker."""
        acc.skipped_reason = "retired"
        reason = f"retired:{_one_line(src.retired_reason or 'retired', 80)}"
        today, last_commit = self.today(), gitops.head_sha(self.repo)
        for row in list(self.manifest.iter_items(src.id, states=_PRESENT)):
            if row.is_dir:
                continue
            self.changes += self.publisher.tombstone(
                src.id, row.stable_id, reason=reason, run_id=self.run_id, today=today, last_commit=last_commit
            )
        self.manifest.drop_cursor(src.id)
        self.changes += self.publisher.reap(today)
        self.manifest.record_source_pass(
            self.run_id, src.id, pass_kind=None, enumeration_complete=False, cursor_reset=False,
            counts={}, skipped_reason="retired",
        )  # fmt: skip

    # ---- one item -----------------------------------------------------------------------------------------
    def _ocr_image(self, row: ItemRow) -> bool:
        """True when ``row`` is an image this cycle's registry reads by OCR."""
        conv = self.registry.for_name(row.name)
        return conv is not None and conv.converter_id == ImageConverter.converter_id

    def _no_converter(self, src: SourceConfig, row: ItemRow) -> ConversionResult | None:
        """The refusal of a file nothing converts, decided from its name: no byte is read for it.

        An image OCR would read is refused the same way while reading it means a download (an online-only
        file, any Graph item). No image was ever downloaded, and hydrating a photo library is the operator's
        choice, never a side effect of OCR: an image that was never read keeps the stub it has without an
        engine, and one that was read while it was on this Mac keeps its page (``_keeps_page``). Only an
        image already on this Mac is read."""
        if self.registry.for_name(row.name) is not None and not (
            self._ocr_image(row) and (src.kind.is_graph or row.dataless)
        ):
            return None
        return convert_file(
            self.staging / "unused",
            name=row.name,
            content_sha256="",
            canonical_sha256="",
            registry=_NO_CONVERTERS,
            cache=self.cache,
        )

    def _reads_with_ocr(self, name: str) -> bool:
        """True when the converter this cycle's registry routes ``name`` to reads with the engine: an
        image, a PDF, a deck, a Word or OpenDocument file.  Such a converter has the OCR options."""
        conv = self.registry.for_name(name)
        return self.ocr is not None and conv is not None and "ocr_languages" in conv.options()

    def _ocr_waits(self, src: SourceConfig, row: ItemRow) -> bool:
        """True when ``row`` is left for a later cycle's OCR, before a byte of it is read.

        An image on this Mac waits once the cycle's OCR time is used (``_OCR_BUDGET_S``).  So does a Graph
        item whose converter reads with the engine, and also when the helper stopped working: converted
        now it would get the page without OCR, and nothing reads a Graph file again (a re-read never
        downloads), so that page would stay until the file's bytes change.  Waiting costs no download; the
        next cycle converts it with OCR.  A document on this Mac does not wait (``_converting``)."""
        if self.ocr is None or not self._ocr_over():
            return False
        if self._ocr_image(row):
            return self.ocr.spent_s >= _OCR_BUDGET_S
        return src.kind.is_graph and self._reads_with_ocr(row.name)

    def _keeps_page(self, row: ItemRow, acc: _SourceAcc) -> bool:
        """True when ``row`` is an image that is not read because reading it would be a download, and that
        has the page OCR made of it while it was on this Mac: the page is kept.

        The row is settled as the online-only file it is, and nothing is published.  Without this, whatever
        made such a row pending work (a ``materialise PATH`` run that names it, a repair) replaced its page
        with the ``no converter`` stub.  Under a label rule there is no image converter at all, and the
        stub is what the rule asks for."""
        if self.registry.for_name(row.name) is None:
            return False
        outs = self.manifest.outputs_for(row.source_id, row.stable_id)
        if not any(o.status is OutputStatus.OK for o in outs) or not _pages_intact(self.repo, outs):
            return False
        settled = Verdict.DATALESS if row.dataless else _SETTLED_PUBLISHED
        self.manifest.set_verdict(row.source_id, row.stable_id, settled)
        if self.forced_paths and not self._recording(row.name):
            acc.alarms.append(
                f"{row.rel_path}: an online-only image is not downloaded for OCR; its page is kept as it was"
            )
        return True

    def _converting(self) -> Registry:
        """The registry the next file is converted with. Once the cycle reads nothing more with its engine
        (``_ocr_over``: its OCR time is used, or the helper stopped working) it is the one without an engine
        (``Registry.without_ocr``): a document on this Mac that converts without OCR does not wait for the
        next cycle as an image does, and does not hold this one for its own share of helper time. It gets
        the page, the version and the action key of a Mac without an engine, which is how a later re-read
        can tell OCR has not read it. (A Graph document is not converted then: ``_ocr_waits``.)"""
        plain = self.registry.without_ocr
        if self._in_recording_pass:  # recordings have time of their own (``_RECORDING_BUDGET_S``)
            return self.registry
        return plain if plain is not None and self._ocr_over() else self.registry

    def _process(
        self, src: SourceConfig, arm: SourceArm, row: ItemRow, budget: ByteBudget, acc: _SourceAcc
    ) -> None:
        sid, stable = row.source_id, row.stable_id
        recording = self._recording(row.name)
        if row.state_reason == HYDRATION_REFUSED and not recording:
            self.manifest.set_state(sid, stable, row.state, None)  # re-decided below: only a new refusal
        refused = self._no_converter(src, row)
        if refused is not None:  # no bytes are needed to refuse a type: never download it
            if not self._keeps_page(row, acc):
                self._publish(src, row, refused, acc)
            return
        screening = self.publisher.policy_refusal(row)
        if screening is not None:  # an excluded item label: never download it (C15 section 9 item 27)
            self._publish(src, row, self._stub(row, ConversionStatus.REFUSED, screening.reason), acc)
            return
        duplicate = self._inbox_name_size_duplicate(src, row)
        if duplicate is not None:  # the Graph arm is authoritative: never read the drop's bytes
            stub = ConversionResult(
                status=ConversionStatus.REFUSED,
                converter_id="",
                converter_version="",
                options_hash="",
                action_key="",
                content_sha256=row.content_sha256 or "",
                canonical_sha256=row.canonical_sha256 or "",
                units=(),
                reason=duplicate,
            )
            log.info("%s: %s refused: %s", sid, row.rel_path, duplicate)
            self._publish(src, row, stub, acc, quarantine_reason=duplicate)
            return
        if recording:  # read in the recording pass, never in a source's queue (spec S0 rule 4)
            self._recording_defer(src, row, acc)
            return
        if not budget.can_afford(_download_cost(src, row)):
            self._defer(src, row, budget, acc)
            return
        if self._ocr_waits(src, row):  # it waits for OCR, not for a download: the next sync reads it
            self._tally["ocr_deferred"] += 1
            self._defer(src, row, budget, acc, online_only=False)
            return
        try:
            fetched = arm.fetch(_item_from_row(row), self.staging, budget)
        except BudgetExhaustedError:
            self._defer(src, row, budget, acc, online_only=True)  # the pre-check passed: bytes refused it
            return
        except DatalessRefusedError as exc:
            self.manifest.set_verdict(sid, stable, Verdict.DEFERRED)
            if row.state in (RowState.LIVE, RowState.DATALESS):  # never over a quarantine's own reason
                self.manifest.set_state(sid, stable, row.state, HYDRATION_REFUSED)
            acc.deferred += 1
            acc.deferred_online_only += 1
            acc.alarms.append(f"{row.rel_path}: hydration refused by the OS ({exc}); deferred")
            return
        except AuthRequiredError:
            raise
        except FileNotFoundError:
            self.manifest.set_verdict(sid, stable, Verdict.ERROR)
            acc.counts[Verdict.ERROR] += 1
            log.info("%s: %s vanished before it could be read; re-classified next cycle", sid, row.rel_path)
            return
        except Exception as exc:  # per-item failure: a row, never a crash of the source
            self.manifest.set_verdict(sid, stable, Verdict.ERROR)
            acc.counts[Verdict.ERROR] += 1
            acc.errors.append(_one_line(f"{row.rel_path}: {type(exc).__name__}: {exc}"))
            log.warning("%s: fetch of %s failed: %s", sid, row.rel_path, exc)
            return
        try:
            self._after_fetch(src, row, fetched, acc)
        finally:
            _discard_staged(fetched, self.staging)

    @staticmethod
    def _stub(row: ItemRow, status: ConversionStatus, reason: str) -> ConversionResult:
        """A converter-less result (no bytes read) carrying ``reason``."""
        return ConversionResult(
            status=status,
            converter_id="",
            converter_version="",
            options_hash="",
            action_key="",
            content_sha256=row.content_sha256 or "",
            canonical_sha256=row.canonical_sha256 or "",
            units=(),
            reason=reason,
        )

    def _defer(
        self,
        src: SourceConfig,
        row: ItemRow,
        budget: ByteBudget,
        acc: _SourceAcc,
        *,
        online_only: bool | None = None,
    ) -> None:
        """Leave ``row`` for a later run. It is online-only when reading it needs a download (the budget's
        byte side refused it); otherwise the file side (``max_files``) did. ``online_only`` None: decided here
        from the row; a fetch that raised BudgetExhaustedError after the pre-check passed (a file the manifest
        saw local that materialise found dataless) passes True."""
        self.manifest.set_verdict(row.source_id, row.stable_id, Verdict.DEFERRED)
        acc.deferred += 1
        acc.counts[Verdict.DEFERRED] += 1
        cost = _download_cost(src, row)
        if online_only is None:
            online_only = cost > 0 and budget.used + cost > budget.max_bytes
        if online_only:
            acc.deferred_online_only += 1
        size = max(row.size or 0, 0)
        cap = self.budget_bytes if self.budget_bytes is not None else src.max_materialise_bytes
        if online_only and size > cap:
            acc.alarms.append(
                f"{row.rel_path}: online-only, {size} bytes exceeds the per-cycle budget ({cap}); raise "
                "max_materialise_bytes or run `agentsync materialise --budget BYTES PATH`"
            )

    def _after_fetch(
        self,
        src: SourceConfig,
        row: ItemRow,
        fetched: FetchResult,
        acc: _SourceAcc,
        *,
        reread: bool = False,
        h1: CanonicalHash | None = None,
    ) -> ConversionResult | None:
        """H1, convert, H2, publish for one fetched file.  Returns the conversion its pages now stand for;
        None when none took the place of what they stood for before.

        ``reread``: the file is read again for what its converter has gained (``_reread_source``), so bytes
        that are the ones its pages were made from are converted all the same.  Such a re-read never costs
        the file its page: a conversion that fails leaves the page as it is, and one that gives the page or
        the stub it already has leaves it untouched and only moves its action key.  The same holds for a
        ``no converter`` stub, which was made without reading a byte: a failed read of the file leaves the
        stub, where it would otherwise become a failed conversion nothing reads again.

        ``h1``: the canonical hash of the staged bytes, when the caller has it (the recording pass)."""
        sid, stable = row.source_id, row.stable_id
        if h1 is None:
            h1 = canonical_hash(fetched.path, suffix=_item_from_row(row).suffix)
        if self.suppressions.matches_content(h1.sha256):
            self._suppress_purged_content(src, row, acc)
            return None
        c2 = classify_content(row, h1)
        if c2.changed_parts:
            log.info("%s/%s changed parts: %s", sid, row.rel_path, ", ".join(c2.changed_parts))
        self.manifest.set_content(
            sid,
            stable,
            content_sha256=fetched.content_sha256,
            canonical_sha256=h1.sha256,
            canonical_method=h1.method,
            canonical_parts=h1.parts,
        )
        fresh = self.manifest.get_item(sid, stable) or row
        outs = self.manifest.outputs_for(sid, stable)
        # The stub of a page with no room for its sidecar is never the last word on the bytes: its cause is
        # the path, so every read plans the item again at the path it has now.
        intact = _pages_intact(self.repo, outs) and not _stubbed_for_path(fresh)
        same = c2.verdict is Verdict.TOUCHED_NOT_CHANGED and intact
        if same and not reread and not self._reads_anyway(fresh):
            result = None
            if self._outdated_in_queue(src, fresh):
                # The same bytes, and pages from before something their converter has: read again here.
                started = _reread_clock()
                self._reread_n += 1
                result = self._after_fetch(src, row, fetched, acc, reread=True)
                self._reread_s += _reread_clock() - started
                self._reread_note(sid, stable, result is not None and not self._lacks(row.name, result))
                self._save_reread(sid)
                now = self.manifest.get_item(sid, stable)
                if now is None or now.last_verdict is not row.last_verdict:
                    return result  # published, or its pages came out the same: the row is settled
                fresh, outs = now, self.manifest.outputs_for(sid, stable)  # kept as it was: settle it below
            if self._rewrite_if_moved(src, fresh, outs, acc):
                acc.counts[Verdict.TOUCHED_NOT_CHANGED] += 1
                self.manifest.set_verdict(sid, stable, Verdict.TOUCHED_NOT_CHANGED)
            return result
        if not reread:
            self._reread_forget(src, stable)  # new bytes: what a re-read of the old ones did is void
        again = reread and same  # the bytes its pages were made from, converted once more
        # ...or bytes never read before, behind a stub made from the name: the stub is what there is to keep
        keeps = again or (reread and intact and _no_converter_stub(fresh))
        registry = self._converting()
        result = convert_file(
            fetched.path,
            name=row.name,
            content_sha256=fetched.content_sha256,
            canonical_sha256=h1.sha256,
            registry=registry,
            cache=self.cache,
        )
        if self._in_recording_pass and self._helper_failed(row.name, result):
            raise _HelperDown
        # the run that last converted this file from these bytes, asked before this one is recorded (the
        # run record): a copy of a file another run converted is no file converted again
        prior = self._converted_before(outs, result.action_key)
        if result.action_key and result.status in (ConversionStatus.OK, ConversionStatus.UNREADABLE):
            self.manifest.record_cache(
                result.action_key,
                converter_id=result.converter_id,
                converter_version=result.converter_version,
                options_hash=result.options_hash,
                canonical_sha256=result.canonical_sha256,
                status=result.status.value,
                unit_count=len(result.units),
                size=sum(len(u.body.encode("utf-8")) for u in result.units),
                run_id=self.run_id,
            )
        lacks = self._lacks(row.name, result)
        self._tally_ocr(result, lacks=lacks, plain=registry is not self.registry)
        if not reread and lacks:
            # Converted without something its converter has (past the OCR time, or the engine failed on
            # it): the source has a file to read again, so a later cycle looks.
            self._reread_reopen(src)
        duplicate = self._inbox_duplicate(src, h1.sha256)
        if duplicate is not None:
            result = dataclasses.replace(result, status=ConversionStatus.REFUSED, units=(), reason=duplicate)
        if self.ocr is not None and self.ocr.down and _OCR_DOWN not in acc.alarms:
            acc.alarms.append(_OCR_DOWN)
        if keeps and result.status is ConversionStatus.FAILED:
            # A re-read that fails keeps the page: nothing is published and no verdict moves.
            self._reread_kept += 1
            return None
        stub = _same_stub(outs, fresh, result) if keeps else None
        if stub is not None:  # the stub it has says what this conversion says
            self._move_key(sid, stable, outs, result.action_key, stub)
            return result
        if not again:
            acc.converted += 1
            self._tally_converted(result, prior)
        c3 = classify_output(outs, result) if intact else Verdict.CHANGED
        if c3 is Verdict.OUTPUT_UNCHANGED:  # H2 early cutoff: bodies identical, the pages stay as they are
            self._move_key(sid, stable, outs, result.action_key, OutputStatus.OK)
            if self._rewrite_if_moved(src, fresh, outs, acc):
                acc.counts[Verdict.OUTPUT_UNCHANGED] += 1
                self.manifest.set_verdict(sid, stable, Verdict.OUTPUT_UNCHANGED)
            return result
        acc.counts[Verdict.CHANGED] += 1
        self._publish(src, fresh, result, acc, quarantine_reason=None if duplicate is None else result.reason)
        return result

    def _reads_anyway(self, row: ItemRow) -> bool:
        """True when the recording pass converts ``row`` from the bytes its stored hash names all the same:
        the hash may be one a read stored without publishing (a recording that waits, one given up (O13), a
        ``no converter`` stub the stub path read in part), and ``materialise PATH`` reads what it names."""
        return self._in_recording_pass and (
            bool(self.forced_paths)
            or row.state_reason in (RECORDING_WAITS, _RECORDING_STOPPED)
            or _no_converter_stub(row)
        )

    def _move_key(
        self, sid: str, stable: str, outs: Sequence[OutputRow], key: str, status: OutputStatus
    ) -> None:
        """Give ``key`` to the item's output rows of ``status``, which stay as they are otherwise: the
        conversion under that key gave the pages they already describe."""
        if any(o.action_key != key for o in outs if o.status is status):
            self.manifest.replace_outputs(
                sid,
                stable,
                [dataclasses.replace(o, action_key=key) if o.status is status else o for o in outs],
            )

    def _suppress_purged_content(self, src: SourceConfig, row: ItemRow, acc: _SourceAcc) -> None:
        """The fetched bytes are content purged for erasure/DLP/label reasons (a copy, or a re-upload under
        a new id): never publish it.  The item's pages leave the tree, its manifest row is forgotten and its
        id joins the suppression list, so later scans drop it before any fetch."""
        for out in self.manifest.outputs_for(row.source_id, row.stable_id):
            if self.publisher.remove_output_page(out.output_path):
                self.changes.append(
                    MirrorChange(ChangeOp.DELETED, out.output_path, row.source_id, row.stable_id)
                )
        self.manifest.forget_item(row.source_id, row.stable_id)
        governance.suppress_items(self.config.state_paths.root, [(row.source_id, row.stable_id)])
        self.suppressions = governance.load_suppressions(self.config.state_paths.root)
        acc.alarms.append(f"an item matched purged content and was suppressed (never published): {src.id}")
        log.warning("%s: fetched content matches a purged item; suppressed", src.id)

    def _duplicate_reason(self, other: ItemRow) -> str:
        """``duplicate-of <source_id>`` plus the Graph row's mirror path, if any (design 4.2, arm C)."""
        pages = sorted(
            o.output_path
            for o in self.manifest.outputs_for(other.source_id, other.stable_id)
            if o.status is not OutputStatus.TOMBSTONE
        )
        return f"duplicate-of {other.source_id}" + (f" ({pages[0]})" if pages else "")

    def _is_live_graph_row(self, src: SourceConfig, other: ItemRow) -> bool:
        if other.source_id == src.id or other.state not in (RowState.LIVE, RowState.DATALESS):
            return False
        try:
            return self.config.source(other.source_id).kind.is_graph
        except ConfigError:
            return False

    def _inbox_duplicate(self, src: SourceConfig, canonical: str) -> str | None:
        """Arm precedence by canonical hash: the quarantine reason when a live Graph row has the same H1."""
        if src.kind is not SourceKind.INBOX:
            return None
        for other in self.manifest.find_by_canonical(canonical):
            if self._is_live_graph_row(src, other):
                return self._duplicate_reason(other)
        return None

    def _inbox_name_size_duplicate(self, src: SourceConfig, row: ItemRow) -> str | None:
        """Arm precedence by (normalised name, size), decided from zero bytes before any fetch.

        The drop's name is NFC-normalised with its conflict suffixes folded (``-<COMPUTERNAME>``, `` (1)``,
        `` - Copy``, Finder `` copy``) and case-folded; so is each live Graph row's name of exactly the same
        size.  A match refuses the drop (quarantined ``duplicate-of <source_id> (<mirror path>)``).
        """
        if src.kind is not SourceKind.INBOX or row.size is None:
            return None
        raw = row.extra.get("dedup_name")
        key = _dedup_key(raw if isinstance(raw, str) and raw else row.name)
        for other in self.manifest.find_by_size(row.size):
            if self._is_live_graph_row(src, other) and _dedup_key(other.name) == key:
                return self._duplicate_reason(other)
        return None

    def _rewrite_if_moved(
        self, src: SourceConfig, row: ItemRow, outs: Sequence[OutputRow], acc: _SourceAcc
    ) -> bool:
        """Bring the pages' frontmatter and paths up to ``row`` when they name another path, eTag or id.
        False when ``_rewrite`` settled the item as a stub instead (see there)."""
        durable = self.manifest.durable_id(row.source_id, row.stable_id)
        if _page_provenance_matches(self.repo, row, outs, src.kind.is_graph, durable):
            return True
        return self._rewrite(src, row, acc)

    def _publish_path_stub(
        self, src: SourceConfig, row: ItemRow, acc: _SourceAcc, exc: SidecarPathError
    ) -> None:
        """Settle ``row`` as an unreadable stub: its page has no room for the sidecar it needs.  One item's
        refusal: an uncaught error would fail the source at the same row every cycle, and an over-long
        sidecar would block the commit for every source."""
        log.warning("%s/%s quarantined: %s", *_row_ids(row), exc)
        stub = self._stub(row, ConversionStatus.UNREADABLE, _SIDECAR_PATH)
        self._publish(src, row, stub, acc, quarantine_reason=_SIDECAR_PATH)

    def _publish(
        self,
        src: SourceConfig,
        row: ItemRow,
        result: ConversionResult,
        acc: _SourceAcc,
        *,
        quarantine_reason: str | None = None,
    ) -> None:
        sid, stable = row.source_id, row.stable_id
        prior_ok = [o for o in self.manifest.outputs_for(sid, stable) if o.status is OutputStatus.OK]
        try:
            pages = self.publisher.plan_pages(src, row, result)
        except SidecarPathError as exc:
            self._publish_path_stub(src, row, acc, exc)
            return
        self.changes += self.publisher.write_pages(row, pages, self.run_id)
        if prior_ok and any(p.refusal for p in pages) and quarantine_reason is None:
            self._label_escalation(row, prior_ok)
        if result.status is ConversionStatus.OK:
            self.ok_pages.update(p.output_path for p in pages)
            for p in pages:  # sidecars carry the full text past the page cap: scan them too
                for rel, _data in p.sidecars:  # text only: a keyframe's bytes are never scanned (S10)
                    if rel.lower().endswith(_TEXT_SIDECARS):
                        self.ok_pages.add(rel)
                        self.sidecar_page[rel] = p.output_path
            if (
                row.state is not RowState.LIVE and row.state is not RowState.DATALESS
            ) or row.state_reason == RECORDING_WAITS:
                self.manifest.set_state(
                    sid, stable, RowState.DATALESS if row.dataless else RowState.LIVE, None
                )
            self.manifest.set_verdict(sid, stable, _SETTLED_PUBLISHED)
            return
        self.stub_pages.update(p.output_path for p in pages)
        reason = _one_line(quarantine_reason or result.reason or result.status.value, 200)
        refused = result.status is ConversionStatus.REFUSED or (
            result.status is ConversionStatus.UNREADABLE and content_policy.is_refusal_reason(result.reason)
        )
        if refused and quarantine_reason is None:
            self.manifest.set_state(sid, stable, RowState.REFUSED, reason)
            self.manifest.set_verdict(sid, stable, Verdict.REFUSED)
            acc.counts[Verdict.REFUSED] += 1
        elif result.status is ConversionStatus.FAILED:
            self.manifest.set_state(sid, stable, RowState.QUARANTINED, reason)
            self.manifest.set_verdict(sid, stable, Verdict.ERROR)  # not cached: retried next cycle
            acc.counts[Verdict.ERROR] += 1
            acc.errors.append(_one_line(f"{row.rel_path}: {reason}"))
        else:
            self.manifest.set_state(sid, stable, RowState.QUARANTINED, reason)
            self.manifest.set_verdict(sid, stable, Verdict.QUARANTINED)
            acc.counts[Verdict.QUARANTINED] += 1

    def _label_escalation(self, row: ItemRow, prior_ok: Sequence[OutputRow]) -> None:
        """A published item is now refused by the label policy (relabel, or a tightened [policy]): drop the
        plaintext conversions of its earlier content from the cache at once and queue a history purge
        (C15 section 9 item 38; the rewrite itself runs from ``agentsync purge --queue``)."""
        keys = {o.action_key for o in prior_ok if o.action_key}
        removed = self.cache.delete(keys)
        queued = governance.enqueue_purge(
            self.config.state_paths.root,
            governance.PurgeSelector(source_id=row.source_id, stable_id=row.stable_id),
            governance.PurgeReason.LABEL_ESCALATION,
            now=self.now(),
        )
        self._acc(self.config.source(row.source_id)).alarms.append(
            f"label escalation: a published file is now refused by the label policy; {removed} cached "
            f"conversion(s) deleted{', purge queued' if queued else ''} (run `agentsync purge --queue`)"
        )
        log.warning("%s: label escalation of %s; purge queued", row.source_id, row.stable_id)

    # ---- secrets, curation, statuses ----------------------------------------------------------------------
    def _quarantine_secrets(self) -> None:
        """Secret scan over every page and sidecar written this cycle (C15; design 4.7).

        A hit in content (a page or one of its ``.files/`` sidecars) re-publishes the item as a ``contains a
        credential`` stub (the sidecars go with the page).  The stubs are scanned again: a hit there comes
        from the item's metadata (its file name / mail subject), so the item's path is redacted everywhere
        it would be committed (stub, shard, QUARANTINE.tsv, CHANGELOG, commit body) and its page moves to a
        hashed name.  A credential that survives even that blocks the commit."""
        pages = sorted(p for p in self.ok_pages | self.stub_pages if (self.repo / p).is_file())
        if not pages:
            return
        hits = lints.lint_secrets(self.repo, pages)
        self.findings += hits
        hit_pages = sorted({self.sidecar_page.get(h.path, h.path) for h in hits})
        stubbed: list[tuple[str, str]] = []
        for sid, stable in _owners(self.manifest, hit_pages):
            pages_of = {o.output_path for o in self.manifest.outputs_for(sid, stable)}
            if pages_of and pages_of <= (self.stub_pages - self.ok_pages):
                self._redact(sid, stable)  # already a stub: the credential is in the name
            elif self._stub_credential(sid, stable):
                stubbed.append((sid, stable))
        stub_paths = sorted(
            o.output_path
            for sid, stable in stubbed
            for o in self.manifest.outputs_for(sid, stable)
            if (self.repo / o.output_path).is_file()
        )
        for sid, stable in _owners(
            self.manifest, [h.path for h in lints.lint_secrets(self.repo, stub_paths)]
        ):
            self._redact(sid, stable)
        redacted = sorted(
            o.output_path
            for c in self.changes
            if self.manifest.is_redacted(c.source_id, c.stable_id)
            for o in self.manifest.outputs_for(c.source_id, c.stable_id)
            if (self.repo / o.output_path).is_file()
        )
        for left in lints.lint_secrets(self.repo, sorted(set(redacted))):
            self.findings.append(
                LintFinding(
                    "SECRET", left.path, "a credential survives redaction; not committing", blocking=True
                )
            )

    def _stub_credential(self, sid: str, stable: str) -> bool:
        row = self.manifest.get_item(sid, stable)
        if row is None:
            return False
        try:
            src = self.config.source(sid)
        except ConfigError:
            return False
        stub = self._stub(row, ConversionStatus.UNREADABLE, _CREDENTIAL)
        self._publish(src, row, stub, self._acc(src), quarantine_reason=_CREDENTIAL)
        log.warning("%s/%s quarantined: the converted page contains a credential", sid, stable)
        return True

    def _redact(self, sid: str, stable: str) -> None:
        """The item's name/path carries a credential: redact it everywhere it would be committed."""
        if self.manifest.is_redacted(sid, stable):
            return
        self.manifest.set_redacted(sid, stable)
        old_paths = {o.output_path for o in self.manifest.outputs_for(sid, stable)}
        head_paths = {p for p in old_paths if _in_head(self.repo, p)}
        mine = [c for c in self.changes if (c.source_id, c.stable_id) == (sid, stable)]
        self.changes = [c for c in self.changes if (c.source_id, c.stable_id) != (sid, stable)]
        self._stub_credential(sid, stable)
        new = [c for c in self.changes if (c.source_id, c.stable_id) == (sid, stable)]
        self.changes = [c for c in self.changes if (c.source_id, c.stable_id) != (sid, stable)]
        for path in sorted({c.path for c in new} | {c.path for c in mine if c.path not in old_paths}):
            if self.manifest.output_by_path(path) is None:
                continue
            op = ChangeOp.MODIFIED if path in head_paths else ChangeOp.ADDED
            self.changes.append(MirrorChange(op, path, sid, stable))
        if head_paths - {c.path for c in self.changes}:
            # the committed page under the credential-bearing name is removed (named nowhere in CHANGELOG)
            self._acc(self.config.source(sid)).alarms.append(
                "a page named after a credential was removed from the tree; `agentsync purge` it from history"
            )
        log.warning("%s/%s: the name carries a credential; its path is redacted", sid, stable)

    def _curate(self) -> None:
        layout = self.config.layout
        rows, entity_rows, findings = curate.generate_depends(layout)
        self.findings += findings
        curate.write_depends(layout, rows)
        curate.write_by_entity(layout, entity_rows)
        self.manifest.replace_depends(rows)
        rc, verdicts = curate.refresh_queue(layout)
        if rc != 2:
            curate.apply_stale_banners(layout, verdicts, self.today())
        retired = {
            s.id: s.retired_reason or "retired" for s in self.config.sources if s.state is SourceState.RETIRED
        }
        curate.apply_retired_banners(layout, rows, retired)

    def _statuses(self, sources: Iterable[SourceConfig]) -> list[SourceStatus]:
        return source_statuses(self.config, self.manifest, now=self.now(), reports=self.accs)


def _dedup_key(name: str) -> str:
    """Inbox dedup key of a file name: NFC, conflict suffixes folded, case-folded (APFS/NTFS sameness)."""
    return unicodedata.normalize("NFC", fold_conflict_suffix(name).casefold())


def _row_ids(row: ItemRow) -> tuple[str, str]:
    """(source_id, stable_id) of a row (log helper)."""
    return row.source_id, row.stable_id


_CREDENTIAL = "contains a credential"
_SIDECAR_PATH = "path too long for the full-content file this page needs (shorten a folder or file name)"


def _stubbed_for_path(row: ItemRow) -> bool:
    """True when ``row`` is settled as the stub ``_publish_path_stub`` writes.  Its bytes were never the
    problem, so a rename must bring it back to the work queue although nothing in the file changed."""
    return row.state is RowState.QUARANTINED and row.state_reason == _SIDECAR_PATH


def _removal_reason(why: str) -> str:
    """Tombstone reason for an explicit provider removal: a move out of the scope (``moved:<folder>``,
    ``moved-out-of-scope``, ``excluded``) is ``moved`` (the item still exists: no purge is queued); anything
    else is ``deleted-upstream``."""
    return "moved" if why.startswith("moved") or why == "excluded" else "deleted-upstream"


def _dirty_paths(repo: Path, paths: Sequence[str]) -> set[str]:
    """The subset of ``paths`` whose worktree state differs from HEAD (modified, added, deleted)."""
    if not paths:
        return set()
    try:
        out = gitops.run_git(
            repo,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--no-renames",
            "--",
            *(f":(literal){p}" for p in paths),
        ).stdout
    except AgentSyncError:
        return set(paths)
    return {rec[3:] for rec in out.split("\0") if len(rec) > 3}


def _in_head(repo: Path, path: str) -> bool:
    try:
        return gitops.run_git(repo, "cat-file", "-e", f"HEAD:{path}", check=False).returncode == 0
    except AgentSyncError:
        return False


def _owners(manifest: Manifest, paths: Sequence[str]) -> list[tuple[str, str]]:
    owners: set[tuple[str, str]] = set()
    for path in paths:
        out = manifest.output_by_path(path)
        if out is not None:
            owners.add((out.source_id, out.stable_id))
    return sorted(owners)


def source_statuses(
    config: Config,
    manifest: Manifest,
    *,
    now: datetime,
    reports: dict[str, _SourceAcc] | None = None,
) -> list[SourceStatus]:
    """One SourceStatus per configured source (STATE.md / INDEX.md / ``agentsync status``)."""
    heartbeat: dict[str, dict[str, object]] = {}
    try:
        heartbeat = {k: dict(v) for k, v in read_heartbeat(config.state_paths.heartbeat).items()}
    except (OSError, ValueError) as exc:
        log.warning("heartbeat unreadable: %s", exc)
    out: list[SourceStatus] = []
    for src in config.sources:
        row = manifest.get_source(src.id)
        cursor = manifest.get_cursor(src.id)
        set_at = _parse_iso(cursor.current_set_at) if cursor is not None else None
        age = int((now - set_at).total_seconds()) if set_at is not None else None
        by_state, deferred = manifest.state_counts(src.id)
        counts: Counter[str] = Counter({state.value: n for state, n in by_state.items()})
        acc = (reports or {}).get(src.id)
        breaker = "ok"
        if row is not None and row.breaker_tripped_at is not None:
            breaker = (
                f"TRIPPED until {row.breaker_until or 'cleared'} ({row.breaker_candidates or 0} candidates)"
            )
        auth = "ok"
        hb = heartbeat.get(src.id, {})
        if row is not None and row.auth_state != "ok":
            auth = f"{row.auth_state} {hb.get('last_attempt_at', '')}".strip()
        last_success = hb.get("last_success_at")
        out.append(
            SourceStatus(
                source_id=src.id,
                kind=src.kind,
                state=src.state.value,
                pass_kind=acc.pass_kind if acc is not None else None,
                enumeration_complete=bool(row.enumeration_complete) if row is not None else False,
                baseline_complete=bool(row.baseline_complete) if row is not None else False,
                cursor_age_s=age,
                cursor_fingerprint=cursor_fingerprint(cursor.current if cursor is not None else None),
                cadence_s=src.cadence_s,
                live=counts[RowState.LIVE.value] + counts[RowState.DATALESS.value],
                dataless=counts[RowState.DATALESS.value],
                quarantined=counts[RowState.QUARANTINED.value] + counts[RowState.REFUSED.value],
                deferred=deferred,
                breaker=breaker,
                auth=auth,
                last_success=str(last_success) if isinstance(last_success, str) else None,
            )
        )
    return out


def _selected(config: Config, only: Sequence[str]) -> list[SourceConfig]:
    if not only:
        return list(config.sources)
    wanted = set(only)
    unknown = sorted(wanted - {s.id for s in config.sources})
    if unknown:
        raise ConfigError(f"unknown source id(s): {', '.join(unknown)}")
    return [s for s in config.sources if s.id in wanted]


def _map_paths(config: Config, paths: Sequence[Path]) -> dict[str, set[str]]:
    """Map ``agentsync materialise`` paths to (source id -> rel paths) of local/inbox sources."""
    out: dict[str, set[str]] = {}
    for raw in paths:
        # source roots are canonical (config resolves symlinks outside CloudStorage): so is the argument
        path = canonical_source_root(Path(os.path.abspath(Path(raw).expanduser())))  # noqa: PTH100
        for src in config.sources:
            if src.path is None or src.kind not in (SourceKind.LOCAL, SourceKind.INBOX):
                continue
            root = Path(os.path.abspath(src.path))  # noqa: PTH100 - lexical, as configured
            if path == root or root in path.parents:
                rel = path.relative_to(root).as_posix()
                out.setdefault(src.id, set()).add(unicodedata.normalize("NFC", rel))
                break
        else:
            raise ConfigError(f"{raw}: not inside any configured local or inbox source")
    return out


INTERACTIVE_LOCK_WAIT_S = 600.0
"""How long a mode-None (interactive) run waits for another cycle's lock before LockHeldError (exit 75)."""
_LOCK_RETRY_S = 0.5
_LOCK_PROGRESS_S = 30.0


def _interactive_mode(config: Config, now: datetime) -> CycleMode:
    """The mode of a run that named none: RECONCILE once the last full run (``last_full_run_started``) is at
    least ``config.reconcile_interval_s`` old, else POLL.  Chosen before the lock, so its label is truthful;
    opening the manifest here migrates it like any other open.

    A live lock holder labelled ``reconcile`` (the LaunchAgent's full pass, still recorded as ``running``, so
    ``last_full_run_started`` cannot see it) makes this run a POLL: it waits for that pass and then runs the
    cheap cycle instead of a second full reconcile straight after it."""
    db = config.state_paths.db
    if not db.is_file():
        return CycleMode.POLL
    lock_path = config.state_paths.lock
    if SingleWriterLock.is_held(lock_path):
        holder = LockInfo.parse(SingleWriterLock.read_body(lock_path))
        if holder is not None and holder.label == CycleMode.RECONCILE.value:
            return CycleMode.POLL
    with Manifest(db) as manifest:
        raw = manifest.last_full_run_started()
    try:
        started = datetime.fromisoformat(raw) if raw else None
    except ValueError:
        started = None
    if started is None:
        return CycleMode.POLL
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    age = (now.astimezone(UTC) - started).total_seconds()
    return CycleMode.RECONCILE if age >= config.reconcile_interval_s else CycleMode.POLL


def _acquire_waiting(lock: SingleWriterLock, wait_s: float) -> LockAcquisition:
    """Take the lock, retrying while another cycle holds it; progress is logged every 30 s.  After ``wait_s``
    the last LockHeldError is raised (naming how long this run waited)."""
    start = time.monotonic()
    noted = -_LOCK_PROGRESS_S
    while True:
        try:
            return lock.acquire()
        except LockHeldError as exc:
            waited = time.monotonic() - start
            if waited >= wait_s:
                raise LockHeldError(f"{exc}; waited {int(waited)}s") from None
            if waited - noted >= _LOCK_PROGRESS_S:
                noted = waited
                log.warning(
                    "another sync is running (%s); waiting for it to finish (%ds of at most %ds)",
                    exc,
                    int(waited),
                    int(wait_s),
                )
            time.sleep(min(_LOCK_RETRY_S, max(0.0, wait_s - waited)))


def run_cycle(
    config: Config,
    *,
    mode: CycleMode | None,
    only: Sequence[str] = (),
    now: Callable[[], datetime] | None = None,
    client: GraphClient | None = None,
    budget_bytes: int | None = None,
    materialise_paths: Sequence[Path] = (),
    accept_deletions: Sequence[str] = (),
    lock_wait_s: float = INTERACTIVE_LOCK_WAIT_S,
    wait_for_lock: bool | None = None,
    recordings: _RecordingRun | None = None,
) -> CycleReport:
    """Run one cycle under the single-writer lock; returns the report (never raises for per-source failures).

    ``mode=None`` is an interactive run (``agentsync sync`` without ``--mode``): its mode is
    ``_interactive_mode`` (RECONCILE when one is due, else POLL) and it waits up to ``lock_wait_s`` for a
    running cycle's lock.  An explicit mode (launchd) tries the lock once, unless ``wait_for_lock`` is True
    (an operator verb with a fixed mode, such as ``accept-deletions``: launchd never retries it).  An
    interactive run also lets each inbox wait once for files still settling (``InboxArm.settle``, at most
    ``INBOX_SETTLE_MAX_S`` for the whole sync), so a file an exporter just renamed into place converts in this
    sync, not the next.

    Raises LockHeldError (CLI exit 75), ConfigError, ManifestSchemaError.  AuthRequiredError is caught:
    no cursor advances, STATE.md/heartbeat record ``auth: REAUTH_REQUIRED``, report.auth_required=True.
    DRY_RUN classifies only: no materialise, no writes under docs/, no commit, no cursor change.

    Extensions (keyword-only): ``client`` injects a GraphClient (tests; default built from ``[graph]``);
    ``budget_bytes`` overrides every source's per-cycle materialise budget (download bytes: only online-only
    files and Graph items are charged, so 0 still converts every local file); ``materialise_paths`` restricts
    the work queue to those files (``agentsync materialise``); ``accept_deletions`` lists sources whose
    breaker is cleared and whose absence-based removals apply this cycle (operator-asserted deletion);
    ``recordings`` says who reads recordings (spec S0 rule 5): None derives it, ``named`` for
    ``materialise_paths``, ``none`` for an interactive run, else ``background``.  An operator verb with a mode
    of its own (``reconcile``, ``accept-deletions``) passes ``none``: a tool's timeout can end it.
    """
    clock = now or (lambda: datetime.now(UTC))
    stale_backups = migration_backups(config.state_paths.db)  # before any open: one taken now waits a cycle
    try:
        materialise.fail_closed()
    except OSError as exc:  # not macOS: there is no dataless state to protect
        log.debug("fail_closed unavailable: %s", exc)
    selected = _selected(config, only)
    forced = _map_paths(config, materialise_paths) if materialise_paths else {}
    if forced:
        selected = [s for s in selected if s.id in forced]
    interactive = mode is None if wait_for_lock is None else wait_for_lock
    settle_inbox = mode is None  # an atomic re-export converts within the same sync (field N4)
    # Who reads recordings (spec S0 rule 5): a run no tool timeout ends.  ``materialise PATH`` reads the ones
    # it names; a run with a mode of its own that does not wait for the lock is a LaunchAgent's job.
    if recordings is None:
        recordings = "named" if forced else "none" if interactive else "background"
    if mode is None:
        mode = _interactive_mode(config, clock())
    lock = SingleWriterLock(config.state_paths.lock, mode.value)
    acquisition = _acquire_waiting(lock, lock_wait_s) if interactive else lock.acquire()
    if mode is not CycleMode.DRY_RUN:
        rotate_logs(config.log_dir)  # launchd never rotates the job logs (bounded: 3 x 8 MiB per job)
    own_client = client is None
    auth: MsalAuth | None = None
    graph_client: GraphClient | None = client
    problem: str | None = None
    if client is None:  # DRY_RUN too: classifying still reads Graph metadata behind the same gate
        graph_client, problem, auth = _make_client(config, selected)
    try:
        config.state_paths.root.mkdir(parents=True, exist_ok=True)
        with Manifest(config.state_paths.db) as manifest:
            cycle = _Cycle(
                config,
                manifest,
                mode=mode,
                selected=selected,
                clock=clock,
                lock=lock,
                broke_stale=acquisition.broke_stale,
                client=graph_client,
                client_problem=problem,
                budget_bytes=budget_bytes,
                forced_paths=forced,
                accept_deletions=frozenset(accept_deletions),
                auth=auth,
                stale_backups=stale_backups,
                settle_inbox=settle_inbox,
                recordings=recordings,
            )
            return cycle.run()
    finally:
        if own_client and graph_client is not None:
            graph_client.close()
        lock.release()
