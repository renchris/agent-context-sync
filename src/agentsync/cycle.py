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
import hashlib
import logging
import os
import re
import secrets
import shutil
import socket
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentsync import __version__, curate, gitops, lints, materialise
from agentsync.arm_local import InboxArm, LocalArm
from agentsync.classifier import ClassifyContext, PassClassification, classify_content, classify_output
from agentsync.classifier import classify_pass as _classify_pass
from agentsync.config import BreakerConfig, Config, SourceConfig
from agentsync.convert import convert_file
from agentsync.convert.cache import ConverterCache
from agentsync.convert.canonical import canonical_hash
from agentsync.convert.registry import Registry
from agentsync.errors import (
    AgentSyncError,
    AuthRequiredError,
    BudgetExhaustedError,
    ConfigError,
    DatalessRefusedError,
    GraphThrottled,
    PublishError,
)
from agentsync.frontmatter import FrontmatterError, parse_frontmatter
from agentsync.graph.auth import MsalAuth, settings_from_config
from agentsync.graph.client import GraphClient, user_agent
from agentsync.graph.drive import DriveArm
from agentsync.graph.mail import MailArm
from agentsync.graph.teams import TeamsArm
from agentsync.manifest import ItemRow, Manifest, OutputRow, cursor_fingerprint
from agentsync.model import (
    ByteBudget,
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
from agentsync.ops.lock import SingleWriterLock, read_heartbeat, write_heartbeat
from agentsync.publish import Publisher, SourceStatus, render_tombstone

log = logging.getLogger(__name__)

_RUN_TRAILER = re.compile(r"^Agentsync-Run:\s*(\d+)\s*$", re.MULTILINE)
_SETTLED_PUBLISHED = Verdict.UNCHANGED  # a published row is in sync; CHANGED would be pending work again
_PRESENT = (RowState.LIVE, RowState.DATALESS, RowState.QUARANTINED, RowState.REFUSED)
_NEVER_TRIPS = BreakerConfig(fraction=1.0, floor=10**12, hold_days=0)


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
        if out.page_sha256 is not None and _sha256_file(path) != out.page_sha256:
            return False
    return True


def _page_provenance_matches(repo: Path, item: ItemRow, outputs: Sequence[OutputRow], graph: bool) -> bool:
    """True when every live page's frontmatter still names the item's current path (and eTag for Graph)."""
    for out in outputs:
        if out.status is OutputStatus.TOMBSTONE:
            continue
        try:
            data, _ = parse_frontmatter((repo / out.output_path).read_text(encoding="utf-8"))
        except (OSError, FrontmatterError, UnicodeDecodeError):
            return False
        if data.get("source_path") != item.rel_path:
            return False
        if graph and item.etag is not None and data.get("source_etag") not in (None, item.etag):
            return False
    return True


def _clear_staging(staging: Path) -> None:
    if staging.is_symlink():
        staging.unlink()
    elif staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    staging.chmod(0o700)


def _discard_staged(fetched: FetchResult, staging: Path) -> None:
    fetched.path.unlink(missing_ok=True)
    parent = fetched.path.parent
    if parent != staging and staging in parent.parents:
        with contextlib.suppress(OSError):
            parent.rmdir()


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


def _make_client(config: Config, sources: Sequence[SourceConfig]) -> tuple[GraphClient | None, str | None]:
    """A Graph client when a selected live Graph source exists and ``[graph] client_id`` is set."""
    if not any(s.kind.is_graph and s.is_live for s in sources):
        return None, None
    if not config.graph.client_id:
        return None, "Graph source configured but [graph] client_id is unset (IT app registration pending)"
    try:
        auth = MsalAuth(settings_from_config(config))
    except AgentSyncError as exc:
        return None, f"Graph auth unavailable: {exc}"
    client = GraphClient(
        auth, base_url=config.graph.base_url, user_agent=user_agent(config.graph.company, __version__)
    )
    return client, None


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
                        text = render_tombstone(
                            t, title=item.name, source_kind=src.kind.value, source_path=item.rel_path
                        )
                        _write_atomic(path, text)
            live = [o for o in outs if o.status is not OutputStatus.TOMBSTONE]
            if not live:
                continue
            if _pages_intact(repo, outs):
                if not _page_provenance_matches(repo, item, outs, graph):
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
    head_run = _head_run_id(repo) if head_tree is not None else None
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


@dataclass(slots=True)
class _SourceAcc:
    """Mutable per-source accumulator for one cycle (becomes a SourceReport)."""

    src: SourceConfig
    pass_kind: PassKind | None = None
    enumeration_complete: bool = False
    counts: Counter[Verdict] = field(default_factory=Counter)
    materialised_bytes: int = 0
    deferred: int = 0
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
        self.registry = Registry.default(config.convert)
        self.cache = ConverterCache(config.cache_dir)
        self.publisher = Publisher(config, manifest, clock=clock)
        self.staging = config.state_paths.staging
        self.run_id = 0
        self.changes: list[MirrorChange] = []
        self.ok_pages: set[str] = set()  # mirror pages written this cycle with real content (secret scan)
        self.findings: list[LintFinding] = []
        self.accs: dict[str, _SourceAcc] = {}
        self.shard_sources: set[str] = set()  # sources whose committed shard changed this cycle

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
        recovery = recover(self.config, self.manifest)
        if recovery is not RecoveryAction.NONE:
            log.warning("recovery: %s", recovery.value)
        self.run_id = self.manifest.begin_run(self.mode, host=socket.gethostname(), pid=os.getpid())
        commit_sha: str | None = None
        status = "ok"
        blocked = False
        try:
            fp_changed = set(self.manifest.sync_sources(self.config.sources))
            self.publisher.ensure_scaffold()
            _clear_staging(self.staging)
            arms = build_arms(self.config, self.manifest, self.client)
            for src in self.selected:
                self.lock.beat(f"source:{src.id}")
                self._run_source(src, arms.get(src.id), fp_changed)
            self.lock.beat("secrets")
            self._quarantine_secrets()
            self.lock.beat("curate")
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
            changed_paths = sorted(
                {c.path for c in self.changes} | {c.prev_path for c in self.changes if c.prev_path}
            )
            self.findings += lints.run_land_gate(self.repo, changed_paths)
            blocked = any(f.blocking for f in self.findings)
            if blocked:
                log.error("land gate blocked the commit: %d blocking finding(s)", self._blocking_count())
                self.manifest.discard_pending(self.run_id)
                status = "failed"
            else:
                self.lock.beat("commit")
                commit_sha = self._commit(statuses)
                self.manifest.promote_cursors(self.run_id, _iso(self.now()))
                if self.mode is CycleMode.RECONCILE:
                    removed = self.cache.gc(self.manifest.live_action_keys())
                    if removed:
                        log.info("converter cache: %d dead key(s) removed", removed)
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
        self.manifest.finish_run(self.run_id, status=status, commit_sha=report.commit_sha, counts=counts)
        try:
            self.publisher.write_state(report, self._statuses(self.config.sources))
        except (OSError, AgentSyncError) as exc:
            log.warning("STATE.md not written: %s", exc)

    # ---- commit -------------------------------------------------------------------------------------------
    def _commit(self, statuses: Sequence[SourceStatus]) -> str | None:
        if not gitops.has_changes(self.repo):
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
                scan, pc, _rows = self._scan_and_classify(src, arm, set())
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
            acc.auth_required = True
            acc.errors.append(f"auth REAUTH_REQUIRED: {_one_line(str(exc))}")
            self.manifest.stage_cursor(src.id, None, self.run_id)
            self.manifest.set_auth_state("REAUTH_REQUIRED", [src.id])
            self._record_error(src, acc, "REAUTH_REQUIRED")
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
    ) -> tuple[ScanResult, PassClassification, dict[str, ItemRow]]:
        acc = self._acc(src)
        cursor_row = self.manifest.get_cursor(src.id)
        current = cursor_row.current if cursor_row is not None else None
        full = (
            self.mode is CycleMode.RECONCILE
            or current is None
            or self.broke_stale
            or src.id in fp_changed
            or src.kind in (SourceKind.LOCAL, SourceKind.INBOX)
        )
        scan = arm.scan(current, full=full)
        acc.pass_kind = scan.pass_kind
        acc.enumeration_complete = scan.enumeration_complete
        acc.alarms += list(scan.alarms)
        rows = {r.stable_id: r for r in self.manifest.iter_items(src.id)}
        unseen = self.manifest.unseen_live(src.id, self.run_id)
        accepting = src.id in self.accept_deletions
        active = not accepting and self.manifest.breaker_active(src.id, _iso(self.now()))
        ctx = ClassifyContext(
            run_id=self.run_id, pass_kind=scan.pass_kind, written_at_ns=self.manifest.written_at_ns()
        )
        pc = _classify_pass(
            scan.items,
            rows,
            unseen,
            ctx,
            enumeration_complete=scan.enumeration_complete,
            live_rows=self.manifest.live_count(src.id),
            breaker=_NEVER_TRIPS if accepting else self.config.breaker,
            breaker_active=active,
        )
        for c in pc.verdicts:
            acc.counts[c.verdict] += 1
        if pc.deletion_candidates:
            acc.counts[Verdict.DELETION_CANDIDATE] += len(pc.deletion_candidates)
        acc.breaker_tripped = pc.breaker_tripped and bool(pc.deletion_candidates or active)
        return scan, pc, rows

    @staticmethod
    def _observed_state(item: SourceItem, row: ItemRow | None) -> RowState:
        """State to store for an observation (quarantine/refusal sticks until a re-conversion clears it)."""
        if row is not None and row.state in (RowState.QUARANTINED, RowState.REFUSED):
            return row.state
        return RowState.DATALESS if item.dataless else RowState.LIVE

    def _sync_source(self, src: SourceConfig, arm: SourceArm, acc: _SourceAcc, fp_changed: set[str]) -> None:
        scan, pc, rows = self._scan_and_classify(src, arm, fp_changed)
        items = {it.stable_id: it for it in scan.items}
        verdicts = {c.stable_id: c for c in pc.verdicts}
        rows_before: dict[str, ItemRow | None] = {sid: rows.get(sid) for sid in verdicts}
        moved: set[str] = set()
        # ---- one transaction: observations, safe-save rekeys, derived paths, pending cursor ---------------
        with self.manifest.transaction():
            for c in pc.verdicts:
                item = items[c.stable_id]
                row = rows_before[c.stable_id]
                if c.verdict is Verdict.DELETED:
                    if row is None:
                        continue  # a tombstone for an id we never had
                    state = row.state
                else:
                    state = self._observed_state(item, row)
                self.manifest.upsert_observed(item, run_id=self.run_id, verdict=c.verdict, state=state)
                if c.prev_path is not None or c.verdict is Verdict.METADATA_ONLY:
                    moved.add(c.stable_id)
            for new_id, old_id in pc.safe_saves:
                self.manifest.rekey(src.id, old_id, new_id)
            scope_root = getattr(arm, "scope_root", None)
            if src.kind is SourceKind.GRAPH_DRIVE and callable(scope_root):
                root_id = scope_root()
                for stable_id, _old, _new in self.manifest.rederive_paths(
                    src.id, root_id if isinstance(root_id, str) else None
                ):
                    moved.add(stable_id)
            self.manifest.stage_cursor(src.id, scan.new_cursor, self.run_id)
            acc.staged_cursor = scan.new_cursor is not None
            self.manifest.set_page_link(src.id, None)
            if scan.pass_kind is PassKind.FULL:
                self.manifest.set_enumeration_complete(src.id, scan.enumeration_complete, self.run_id)
            self.manifest.record_source_pass(
                self.run_id,
                src.id,
                pass_kind=scan.pass_kind,
                enumeration_complete=scan.enumeration_complete,
                cursor_reset=scan.cursor_reset,
                counts={k.value: v for k, v in acc.counts.items()},
            )
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
        for row in queue:
            if complete_full and row.last_seen_run < self.run_id:
                continue  # absent from a complete listing: a deletion candidate, not work
            self._process(src, arm, row, budget, acc)
        acc.materialised_bytes = budget.used
        # ---- renames / metadata-only rows that needed no bytes --------------------------------------------
        for stable_id in sorted(moved - queued):
            row = self.manifest.get_item(src.id, stable_id)
            if row is None or row.is_dir or row.state is RowState.TOMBSTONE:
                continue
            self._rewrite(row)
        # ---- removals -------------------------------------------------------------------------------------
        self._removals(src, pc, rows_before, acc)

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

    def _rewrite(self, row: ItemRow) -> None:
        outs = self.manifest.outputs_for(row.source_id, row.stable_id)
        if not any(o.status is not OutputStatus.TOMBSTONE for o in outs):
            return
        try:
            self.changes += self.publisher.rewrite_frontmatter(row, self.run_id)
        except PublishError as exc:
            log.warning("%s/%s: frontmatter rewrite failed (%s); queued for re-fetch", *_row_ids(row), exc)
            self.manifest.set_verdict(row.source_id, row.stable_id, Verdict.MAYBE_CHANGED)

    def _removals(
        self,
        src: SourceConfig,
        pc: PassClassification,
        rows_before: dict[str, ItemRow | None],
        acc: _SourceAcc,
    ) -> None:
        today = self.today()
        last_commit = gitops.head_sha(self.repo)
        removals: list[tuple[str, str]] = []
        for c in pc.verdicts:
            if c.verdict is not Verdict.DELETED or c.reason != "provider-tombstone":
                continue
            row = self.manifest.get_item(src.id, c.stable_id)
            if row is None or row.state is RowState.TOMBSTONE or rows_before.get(c.stable_id) is None:
                continue
            why = str(row.extra.get("removed") or row.extra.get("removed_reason") or "")
            reason = (
                "moved"
                if why.startswith("moved") or why in ("moved-out-of-scope", "excluded")
                else ("deleted-upstream")
            )
            removals.append((c.stable_id, reason))
        if pc.deletion_candidates:
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
                    "removed; if the deletion is real run `agentsync reconcile --source "
                    f"{src.id} --accept-deletions` or retire the source"
                )
            else:
                removals += [(sid, "deleted-upstream") for sid in pc.deletion_candidates]
                srow = self.manifest.get_source(src.id)
                if srow is not None and srow.breaker_tripped_at is not None:
                    self.manifest.clear_breaker(src.id)
        elif src.id in self.accept_deletions:
            srow = self.manifest.get_source(src.id)
            if srow is not None and srow.breaker_tripped_at is not None:
                self.manifest.clear_breaker(src.id)
        for stable_id, reason in sorted(set(removals)):
            self.changes += self.publisher.tombstone(
                src.id, stable_id, reason=reason, run_id=self.run_id, today=today, last_commit=last_commit
            )
        self.changes += self.publisher.reap(today)

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
    def _no_converter(self, row: ItemRow) -> ConversionResult | None:
        if self.registry.for_name(row.name) is not None:
            return None
        return convert_file(
            self.staging / "unused",
            name=row.name,
            content_sha256="",
            canonical_sha256="",
            registry=self.registry,
            cache=self.cache,
        )

    def _process(
        self, src: SourceConfig, arm: SourceArm, row: ItemRow, budget: ByteBudget, acc: _SourceAcc
    ) -> None:
        sid, stable = row.source_id, row.stable_id
        refused = self._no_converter(row)
        if refused is not None:  # no bytes are needed to refuse a type: never download it
            self._publish(src, row, refused, acc)
            return
        if not budget.can_afford(max(row.size or 0, 0)):
            self._defer(src, row, budget, acc)
            return
        try:
            fetched = arm.fetch(_item_from_row(row), self.staging, budget)
        except BudgetExhaustedError:
            self._defer(src, row, budget, acc)
            return
        except DatalessRefusedError as exc:
            self.manifest.set_verdict(sid, stable, Verdict.DEFERRED)
            acc.deferred += 1
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

    def _defer(self, src: SourceConfig, row: ItemRow, budget: ByteBudget, acc: _SourceAcc) -> None:
        self.manifest.set_verdict(row.source_id, row.stable_id, Verdict.DEFERRED)
        acc.deferred += 1
        acc.counts[Verdict.DEFERRED] += 1
        size = row.size or 0
        cap = self.budget_bytes if self.budget_bytes is not None else src.max_materialise_bytes
        if size > cap:
            acc.alarms.append(
                f"{row.rel_path}: {size} bytes exceeds the per-cycle budget ({cap}); raise "
                "max_materialise_bytes or run `agentsync materialise --budget BYTES PATH`"
            )
        del budget

    def _after_fetch(self, src: SourceConfig, row: ItemRow, fetched: FetchResult, acc: _SourceAcc) -> None:
        sid, stable = row.source_id, row.stable_id
        h1 = canonical_hash(fetched.path, suffix=_item_from_row(row).suffix)
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
        intact = _pages_intact(self.repo, outs)
        if c2.verdict is Verdict.TOUCHED_NOT_CHANGED and intact:
            acc.counts[Verdict.TOUCHED_NOT_CHANGED] += 1
            self._rewrite_if_moved(src, fresh, outs)
            self.manifest.set_verdict(sid, stable, Verdict.TOUCHED_NOT_CHANGED)
            return
        result = convert_file(
            fetched.path,
            name=row.name,
            content_sha256=fetched.content_sha256,
            canonical_sha256=h1.sha256,
            registry=self.registry,
            cache=self.cache,
        )
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
        duplicate = self._inbox_duplicate(src, h1.sha256)
        if duplicate is not None:
            result = dataclasses.replace(
                result, status=ConversionStatus.REFUSED, units=(), reason=f"duplicate-of {duplicate}"
            )
        c3 = classify_output(outs, result) if intact else Verdict.CHANGED
        if c3 is Verdict.OUTPUT_UNCHANGED:  # H2 early cutoff: bodies identical, the pages stay as they are
            acc.counts[Verdict.OUTPUT_UNCHANGED] += 1
            if any(o.action_key != result.action_key for o in outs if o.status is OutputStatus.OK):
                self.manifest.replace_outputs(
                    sid,
                    stable,
                    [
                        dataclasses.replace(o, action_key=result.action_key)
                        if o.status is OutputStatus.OK
                        else o
                        for o in outs
                    ],
                )
            self._rewrite_if_moved(src, fresh, outs)
            self.manifest.set_verdict(sid, stable, Verdict.OUTPUT_UNCHANGED)
            return
        acc.counts[Verdict.CHANGED] += 1
        self._publish(src, fresh, result, acc, quarantine_reason=None if duplicate is None else result.reason)

    def _inbox_duplicate(self, src: SourceConfig, canonical: str) -> str | None:
        if src.kind is not SourceKind.INBOX:
            return None
        for other in self.manifest.find_by_canonical(canonical):
            if other.source_id == src.id:
                continue
            try:
                kind = self.config.source(other.source_id).kind
            except ConfigError:
                continue
            if kind.is_graph and other.state in (RowState.LIVE, RowState.DATALESS):
                return other.source_id
        return None

    def _rewrite_if_moved(self, src: SourceConfig, row: ItemRow, outs: Sequence[OutputRow]) -> None:
        if not _page_provenance_matches(self.repo, row, outs, src.kind.is_graph):
            self._rewrite(row)

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
        pages = self.publisher.plan_pages(src, row, result)
        self.changes += self.publisher.write_pages(row, pages, self.run_id)
        if result.status is ConversionStatus.OK:
            self.ok_pages.update(p.output_path for p in pages)
            if row.state is not RowState.LIVE and row.state is not RowState.DATALESS:
                self.manifest.set_state(
                    sid, stable, RowState.DATALESS if row.dataless else RowState.LIVE, None
                )
            self.manifest.set_verdict(sid, stable, _SETTLED_PUBLISHED)
            return
        reason = _one_line(quarantine_reason or result.reason or result.status.value, 200)
        if result.status is ConversionStatus.REFUSED and quarantine_reason is None:
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

    # ---- secrets, curation, statuses ----------------------------------------------------------------------
    def _quarantine_secrets(self) -> None:
        pages = sorted(p for p in self.ok_pages if (self.repo / p).is_file())
        if not pages:
            return
        hits = lints.lint_secrets(self.repo, pages)
        self.findings += hits
        for stable_ids in _owners(self.manifest, [h.path for h in hits]):
            sid, stable = stable_ids
            row = self.manifest.get_item(sid, stable)
            if row is None:
                continue
            try:
                src = self.config.source(sid)
            except ConfigError:
                continue
            stub = ConversionResult(
                status=ConversionStatus.UNREADABLE,
                converter_id="",
                converter_version="",
                options_hash="",
                action_key="",
                content_sha256=row.content_sha256 or "",
                canonical_sha256=row.canonical_sha256 or "",
                units=(),
                reason="contains a credential",
            )
            self._publish(src, row, stub, self._acc(src), quarantine_reason="contains a credential")
            log.warning("%s: %s quarantined: the converted page contains a credential", sid, row.rel_path)

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


def _row_ids(row: ItemRow) -> tuple[str, str]:
    """(source_id, stable_id) of a row (log helper)."""
    return row.source_id, row.stable_id


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
        counts: Counter[str] = Counter()
        deferred = 0
        for item in manifest.iter_items(src.id):
            if item.is_dir:
                continue
            counts[item.state.value] += 1
            if item.last_verdict is Verdict.DEFERRED:
                deferred += 1
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
        path = Path(os.path.abspath(Path(raw).expanduser()))  # noqa: PTH100 - lexical, never resolves symlinks
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


def run_cycle(
    config: Config,
    *,
    mode: CycleMode,
    only: Sequence[str] = (),
    now: Callable[[], datetime] | None = None,
    client: GraphClient | None = None,
    budget_bytes: int | None = None,
    materialise_paths: Sequence[Path] = (),
    accept_deletions: Sequence[str] = (),
) -> CycleReport:
    """Run one cycle under the single-writer lock; returns the report (never raises for per-source failures).

    Raises LockHeldError (CLI exit 75), ConfigError, ManifestSchemaError.  AuthRequiredError is caught:
    no cursor advances, STATE.md/heartbeat record ``auth: REAUTH_REQUIRED``, report.auth_required=True.
    DRY_RUN classifies only: no materialise, no writes under docs/, no commit, no cursor change.

    Extensions (keyword-only): ``client`` injects a GraphClient (tests; default built from ``[graph]``);
    ``budget_bytes`` overrides every source's per-cycle materialise budget; ``materialise_paths`` restricts
    the work queue to those files (``agentsync materialise``); ``accept_deletions`` lists sources whose
    breaker is cleared and whose absence-based removals apply this cycle (operator-asserted deletion).
    """
    clock = now or (lambda: datetime.now(UTC))
    try:
        materialise.fail_closed()
    except OSError as exc:  # not macOS: there is no dataless state to protect
        log.debug("fail_closed unavailable: %s", exc)
    selected = _selected(config, only)
    forced = _map_paths(config, materialise_paths) if materialise_paths else {}
    if forced:
        selected = [s for s in selected if s.id in forced]
    lock = SingleWriterLock(config.state_paths.lock, mode.value)
    acquisition = lock.acquire()
    own_client = client is None
    graph_client, problem = (client, None) if client is not None else _make_client(config, selected)
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
            )
            return cycle.run()
    finally:
        if own_client and graph_client is not None:
            graph_client.close()
        lock.release()
