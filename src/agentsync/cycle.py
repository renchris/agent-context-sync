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
import logging
import os
import re
import secrets
import shutil
import socket
import stat
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentsync import __version__, curate, gitops, governance, lints, materialise, net, skill
from agentsync import policy as content_policy
from agentsync.arm_local import InboxArm, LocalArm, SettleBudget, cloud_provider_root, fold_conflict_suffix
from agentsync.classifier import ClassifyContext, PassClassification, classify_content, classify_output
from agentsync.classifier import classify_pass as _classify_pass
from agentsync.config import BreakerConfig, Config, SourceConfig, canonical_source_root
from agentsync.convert import convert_file
from agentsync.convert.cache import ConverterCache
from agentsync.convert.canonical import canonical_hash
from agentsync.convert.registry import SIDECAR_DIGEST_PREFIX, Registry, sidecar_digest_lines
from agentsync.errors import (
    AgentSyncError,
    AuthRequiredError,
    BudgetExhaustedError,
    ConfigError,
    DatalessRefusedError,
    GraphThrottled,
    LockHeldError,
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
_LABEL_CAPABLE = (*content_policy.OOXML_SUFFIXES, ".pdf", ".eml")
"""Names whose content can carry a sensitivity label: re-screened when the effective [policy] changes."""
_POLICY_META = "policy_fingerprint"
_SCOPE_CHANGE_META = "scope_change:"
_SCOPE_CHANGE_REASON = "retired:scope-change"
_SCOPE_ROOT_META = "scope_root:"
"""``scope_root:<source_id>`` holds ``<run>:<path>`` for a local or inbox source: the first run under the
``path`` it has now (0: the path it had when this was first recorded)."""
_RESCREEN_META = "policy_rescreen_pending"
_CHECKPOINT_PENDING_META = "checkpoint_pending"  # KISS K06: the HEAD a held or failed checkpoint retries
_SEED_PAGE_NAMES = frozenset({"CLAUDE.md", "INDEX.md"})  # under topics/: scaffold files, never curated pages
HYDRATION_REFUSED = "hydration-refused"
"""``state_reason`` of a live/dataless row whose download the OS refused (EDEADLK) on its last attempt: a
later sync's budget never clears it, so ``loop`` makes it an operator wait. Cleared when the row is next
processed."""


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


def _clear_group_other(path: Path) -> bool:
    """Clear ``path``'s group/other permission bits; True when they were set.  A symlink is left alone (its
    target may lie outside the docs repo), and so is anything that is not a regular file or a folder."""
    mode = path.lstat().st_mode
    if not mode & 0o077 or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        return False
    path.chmod(stat.S_IMODE(mode) & ~0o077)
    return True


def _tighten_agent_writes(repo: Path) -> int:
    """Make owner-only what a coding agent writes into the docs repo; return how many paths changed.

    agentsync writes under umask 077, but an agent's file tool runs under the agent's umask (usually 022),
    so the baseline draft left ``_eval/`` readable by group and other and the next ``status`` ended on a
    ``docs_repo.permissions`` FAIL the loop itself had caused.  Covered: every entry at the top of the docs
    repo (the entry itself, not its contents) and everything below :data:`_AGENT_TREES`.  ``mirror/`` and
    ``.git`` are not walked: the publisher writes pages 0600 and git writes under
    ``core.sharedRepository``.  No symlink is followed or changed.  Modes are not content: git tracks only
    the executable bit, so this never dirties the tree.  A path that cannot be changed is skipped with one
    warning (the doctor check still reports it)."""
    changed, failed = 0, 0

    def tighten(path: Path) -> None:
        nonlocal changed, failed
        try:
            changed += _clear_group_other(path)
        except OSError:
            failed += 1

    try:
        top = sorted(repo.iterdir())
    except OSError:
        return 0
    for entry in top:
        tighten(entry)
    for name in _AGENT_TREES:
        tree = repo / name
        if tree.is_symlink() or not tree.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(tree, followlinks=False):
            for child in (*dirnames, *filenames):
                tighten(Path(dirpath) / child)
    if changed:
        log.info("docs repo: cleared group/other access on %d path(s) an agent wrote", changed)
    if failed:
        log.warning(
            "docs repo: %d path(s) could not be made owner-only (agentsync doctor names them)", failed
        )
    return changed


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
        self.registry = Registry.default(config.convert, policy=self.publisher.content_policy)
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
            _tighten_agent_writes(
                self.repo
            )  # an agent's umask is not ours: no permissions FAIL of its making
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
        self.manifest.finish_run(self.run_id, status=status, commit_sha=report.commit_sha, counts=counts)
        try:
            self.publisher.write_state(report, self._statuses(self.config.sources))
            self._append_state(self._state_extras())
        except (OSError, AgentSyncError) as exc:
            log.warning("STATE.md not written: %s", exc)

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
        if row.state_reason == HYDRATION_REFUSED:  # re-decided below: only a new refusal sets it again
            self.manifest.set_state(sid, stable, row.state, None)
        refused = self._no_converter(row)
        if refused is not None:  # no bytes are needed to refuse a type: never download it
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
        if not budget.can_afford(_download_cost(src, row)):
            self._defer(src, row, budget, acc)
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

    def _after_fetch(self, src: SourceConfig, row: ItemRow, fetched: FetchResult, acc: _SourceAcc) -> None:
        sid, stable = row.source_id, row.stable_id
        h1 = canonical_hash(fetched.path, suffix=_item_from_row(row).suffix)
        if self.suppressions.matches_content(h1.sha256):
            self._suppress_purged_content(src, row, acc)
            return
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
        if c2.verdict is Verdict.TOUCHED_NOT_CHANGED and intact:
            if self._rewrite_if_moved(src, fresh, outs, acc):
                acc.counts[Verdict.TOUCHED_NOT_CHANGED] += 1
                self.manifest.set_verdict(sid, stable, Verdict.TOUCHED_NOT_CHANGED)
            return
        acc.converted += 1
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
            result = dataclasses.replace(result, status=ConversionStatus.REFUSED, units=(), reason=duplicate)
        c3 = classify_output(outs, result) if intact else Verdict.CHANGED
        if c3 is Verdict.OUTPUT_UNCHANGED:  # H2 early cutoff: bodies identical, the pages stay as they are
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
            if self._rewrite_if_moved(src, fresh, outs, acc):
                acc.counts[Verdict.OUTPUT_UNCHANGED] += 1
                self.manifest.set_verdict(sid, stable, Verdict.OUTPUT_UNCHANGED)
            return
        acc.counts[Verdict.CHANGED] += 1
        self._publish(src, fresh, result, acc, quarantine_reason=None if duplicate is None else result.reason)

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
                for rel, _data in p.sidecars:
                    self.ok_pages.add(rel)
                    self.sidecar_page[rel] = p.output_path
            if row.state is not RowState.LIVE and row.state is not RowState.DATALESS:
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
    breaker is cleared and whose absence-based removals apply this cycle (operator-asserted deletion).
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
            )
            return cycle.run()
    finally:
        if own_client and graph_client is not None:
            graph_client.close()
        lock.release()
