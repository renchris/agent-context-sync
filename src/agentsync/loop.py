"""The loop's next step, worked out from disk (KISS K01; owner: integrator).

:func:`next_step` reads the docs repo, the manifest, the skill copies and the governance queue (never the
network, never a write) and returns the one step an agent takes next, plus the operator waits. The first unmet
rule wins:

1. a status FAIL (a missing docs repo, a missing or stale skill copy, a Graph sign-in; then any fix the caller
   passes) names its fix;
2. no live source other than the inbox: ask which folders, then ``add-source``;
3. a source listing is INCOMPLETE, or files already on this Mac are not converted yet: sync again (online-only
   files waiting for a download budget are a note, never this rule);
4. no curated page and no ``_eval/questions.md``: draft the baseline questions;
5. ``_eval`` is still a draft: stop, the operator confirms;
6. no curated page and no ``_eval/results-*-before.md``: run the 'before' baseline in a fresh session;
7. checkpoint blockers: fix the pages ``curate`` lists;
8. :data:`AFTER_BASELINE_PAGES` or more curated pages and no after-results: run the 'after' baseline;
9. the curation queue is not empty: curate up to :data:`ROWS_PER_SESSION` rows, sync, session done;
10. nothing to do: session done.

The text is fixed wording plus counts and source ids, never a mirror path or file name (a page name is
third-party content). ``sync`` (without ``--mode``) and ``status`` print :meth:`NextStep.lines`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agentsync import curate, gitops, governance, skill
from agentsync.config import Config, SourceConfig
from agentsync.cycle import _CHECKPOINT_PENDING_META, _SEED_PAGE_NAMES
from agentsync.errors import AgentSyncError
from agentsync.manifest import Manifest
from agentsync.model import LintFinding, RowState, SourceKind, Verdict
from agentsync.paths import expand

_log = logging.getLogger(__name__)

NO_NEXT_HINT_ENV = "AGENTSYNC_NO_NEXT_HINT"
"""Set to 1 (scripts/install.sh exports it) and ``sync`` prints no NEXT / WAITING ON YOU lines, so the
installer's own NEXT: is the only instruction in its output; ``ops.doctor`` words its IT-only fix for it."""

NEXT_PREFIX = "NEXT: "
WAIT_PREFIX = "WAITING ON YOU: "
NOTE_PREFIX = "note: "
BIN = skill.AGENTSYNC_BIN
INSTALL_SH = "~/src/agent-context-sync/scripts/install.sh"
"""Where the README's setup prompt clones the checkout; ``--list-folders`` lists the candidate folders."""
ROWS_PER_SESSION = 10
"""Rule 9's session bound: curate at most this many queue rows, then sync and end the session."""
AFTER_BASELINE_PAGES = 20
"""Rule 8: the 'after' baseline is due once this many curated pages exist."""

_EVAL_DIR = "_eval"
_BASELINE = 'the agentsync-docs skill\'s "Baseline questions" section'
_STATUS_LINE = re.compile(r"^\s*status:\s*(\S+)\s*$")
_STATUS_LINES_READ = 10  # the skill puts ``status:`` on the first line; a frontmatter fence may precede it
_UNCONVERTED = frozenset({Verdict.CREATED, Verdict.MAYBE_CHANGED, Verdict.CHANGED, Verdict.DEFERRED})


@dataclass(frozen=True, slots=True)
class NextStep:
    """The agent's next step, the operator's waits and non-blocking notes (each without its prefix)."""

    step: str
    waits: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    rule: int = 10  # which rule set ``step`` (1-10), for tests and the setup report

    def lines(self) -> list[str]:
        """``NEXT: <step>``, then one ``WAITING ON YOU: …`` line per wait, then one ``note: …`` per note."""
        return (
            [NEXT_PREFIX + self.step]
            + [WAIT_PREFIX + w for w in self.waits]
            + [NOTE_PREFIX + n for n in self.notes]
        )


@dataclass(slots=True)
class _Files:
    """Per-source counts of files the manifest has not published yet."""

    local: dict[str, int] = field(default_factory=dict)  # already on this Mac: the next sync converts them
    online: dict[str, int] = field(default_factory=dict)  # online-only, within the download budget
    over: dict[str, int] = field(default_factory=dict)  # online-only and larger than the source's budget


def _ids(counts: dict[str, int]) -> str:
    return ", ".join(sorted(counts))


def _eval_status(path: Path) -> str | None:
    """The ``status:`` value near the top of ``path`` (lower-cased); None when the file is missing,
    "" when it has no status line."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for _ in range(_STATUS_LINES_READ):
                line = fh.readline()
                if not line:
                    break
                m = _STATUS_LINE.match(line)
                if m:
                    return m.group(1).strip("'\"").lower()
    except FileNotFoundError:
        return None
    except OSError as exc:
        _log.warning("loop: cannot read %s: %s", path, exc)
    return ""


def _skill_current(docs_repo: Path) -> bool:
    """Every skill copy exists and holds this build's text (a sync writes them; a failed write leaves them
    missing or stale)."""
    text = skill.skill_text(docs_repo)
    for path in skill.skill_paths():
        try:
            if path.read_text(encoding="utf-8") != text:
                return False
        except (OSError, UnicodeDecodeError):
            return False
    return True


def _unpublished(manifest: Manifest, sources: Sequence[SourceConfig]) -> _Files:
    """Count each source's files that are not converted yet, split into local, online-only and over-budget
    online-only. An item that failed (``error``) is retried by every sync and is not counted: it would make
    rule 3 loop."""
    out = _Files()
    for src in sources:
        for row in manifest.iter_items(src.id, states=(RowState.LIVE, RowState.DATALESS)):
            if row.is_dir:
                continue
            pending = row.last_verdict in _UNCONVERTED or (
                row.last_verdict is Verdict.DATALESS and row.content_sha256 is None
            )
            if not pending:
                continue
            online = src.kind.is_graph or row.dataless or row.state is RowState.DATALESS
            if not online:
                bucket = out.local
            elif max(row.size or 0, 0) > src.max_materialise_bytes:
                bucket = out.over
            else:
                bucket = out.online
            bucket[src.id] = bucket.get(src.id, 0) + 1
    return out


def _checkpoint_base(docs: Path, head: str, pending: str | None) -> str:
    """The base the next sync checks the checkpoint from: the held checkpoint's base (manifest meta
    ``checkpoint_pending``) while it is still an ancestor of HEAD, else HEAD (history compaction or a purge
    rewrote the pending commit away, or nothing is pending: only uncommitted topic pages count)."""
    return pending if pending and gitops.is_ancestor(docs, pending, head) else head


def _checkpoint_blockers(docs: Path, pending: str | None) -> int:
    """Rule 7, as the sync sees it: what holds a held checkpoint (manifest meta ``checkpoint_pending``, the
    base it retries from), else what would hold the topic pages this session left uncommitted; 0 when neither
    exists, since a sync with no session pages and nothing pending checks nothing."""
    head = gitops.head_sha(docs)
    if head is None:
        return 0
    if not pending:
        dirty = gitops.paths_changed_since(docs, head, ("topics",))
        if not any(p.endswith(".md") and p.rsplit("/", 1)[-1] not in _SEED_PAGE_NAMES for p in dirty):
            return 0
    return len(curate.checkpoint_blockers(docs, since=_checkpoint_base(docs, head, pending)))


def checkpoint_findings(config: Config) -> list[LintFinding]:
    """Every checkpoint blocker from the base rule 7 and the next sync use (``curate`` prints them): a page
    changed since that base is checked for STALE pins and missing sources, every page for the curation lints
    and UNLISTED. Before the first commit every page counts."""
    docs = expand(config.docs_repo)
    head = gitops.head_sha(docs)
    if head is None:
        return curate.checkpoint_blockers(docs)
    pending: str | None = None
    if config.state_paths.db.exists():
        with Manifest(config.state_paths.db) as manifest:
            pending = manifest.get_meta(_CHECKPOINT_PENDING_META) or None
    return curate.checkpoint_blockers(docs, since=_checkpoint_base(docs, head, pending))


def curation_held(config: Config) -> bool:
    """The baseline hold: no curated page yet and no ``_eval/results-*-before.md``, so ``curate`` lists no
    rows (rules 4-6 come first)."""
    has_before = any((expand(config.docs_repo) / _EVAL_DIR).glob("results-*-before.md"))
    return not curate.iter_topic_pages(config.layout) and not has_before


def _queue_rows(config: Config) -> int:
    """Rule 9: refresh-queue rows (STALE and the other verdicts) plus mirror pages no curated page cites."""
    layout = config.layout
    rc, verdicts = curate.refresh_queue(layout)
    rows, _entities, _findings = curate.generate_depends(layout)  # live: pages written since the last sync
    uncovered = curate.uncovered_mirror_pages(layout, rows)
    return (len(verdicts) if rc != 2 else 0) + len(uncovered)


def next_step(config: Config, *, fixes: Sequence[str] = ()) -> NextStep:
    """The first unmet rule of the loop (module docstring) and the operator's waits, from disk only.

    ``fixes`` are the fixes of status FAILs the caller already ran (rule 1, after the disk checks here)."""
    docs = expand(config.docs_repo)
    waits: list[str] = []
    notes: list[str] = []

    def done(step: str, rule: int) -> NextStep:
        return NextStep(step, tuple(waits), tuple(notes), rule)

    queued = governance.pending_purges(config.state_paths.root)
    if queued:
        waits.append(f"{len(queued)} queued purge(s): run `{BIN} purge --queue`")

    # Rule 1: status FAILs.
    if not (docs / ".git").exists():
        return done(f"the docs repo does not exist yet: run `{BIN} init`", 1)
    live = config.live_sources()
    db = config.state_paths.db
    files = _Files()
    incomplete: list[str] = []
    signin: list[str] = []
    pending: str | None = None
    if db.exists():
        with Manifest(db) as manifest:
            for src in live:
                row = manifest.get_source(src.id)
                if row is None or not row.enumeration_complete:
                    incomplete.append(src.id)
                if row is not None and row.breaker_tripped_at is not None:
                    waits.append(
                        f"the deletion breaker tripped on {src.id} ({row.breaker_candidates or 0} file(s) "
                        f"gone): if they really were deleted, run `{BIN} accept-deletions {src.id}`"
                    )
                if src.kind.is_graph and row is not None and row.auth_state != "ok":
                    signin.append(src.id)
            files = _unpublished(manifest, live)
            pending = manifest.get_meta(_CHECKPOINT_PENDING_META) or None
    else:
        incomplete = [s.id for s in live]
    if files.over:
        waits.append(
            f"{sum(files.over.values())} online-only file(s) in {_ids(files.over)} are larger than the "
            f"per-run download budget: run `{BIN} materialise --budget BYTES` with BYTES above their size, "
            "or raise that source's max_materialise_bytes"
        )
    if files.online:
        notes.append(
            f"{sum(files.online.values())} online-only file(s) in {_ids(files.online)} wait for a later "
            "sync's download budget; they do not block the next step"
        )
    eval_dir = docs / _EVAL_DIR
    questions = _eval_status(eval_dir / "questions.md")
    answers = _eval_status(eval_dir / "answers.md")
    confirmed = questions == "confirmed" and answers == "confirmed"
    if questions is not None and not confirmed:
        waits.append(
            "the baseline questions are a draft: keep about 10 in _eval/questions.md, correct the answers in "
            "_eval/answers.md, and change both files to status: confirmed"
        )
    if not _skill_current(docs):
        return done(
            f"the agentsync-docs skill is missing or out of date: run `{BIN} sync` (it writes the skill; "
            "if this line is still here after that sync, the skills folder cannot be written: tell the "
            "operator)",
            1,
        )
    if signin:
        return done(f"sign-in required for {', '.join(signin)}: run `{BIN} graph login`", 1)
    if fixes:
        return done(fixes[0], 1)

    # Rule 2: something to sync.
    if not any(s.kind is not SourceKind.INBOX for s in live):
        return done(
            f"no folder is synced yet: ask the operator which folders to sync (`{INSTALL_SH} --list-folders` "
            f'lists them), then run `{BIN} add-source "<folder>"` for each, then `{BIN} sync`',
            2,
        )

    # Rule 3: the sync has not caught up with what is on this Mac.
    if incomplete or files.local:
        behind = sorted(set(incomplete) | set(files.local))
        return done(
            f"{len(behind)} source(s) not fully listed or converted yet ({', '.join(behind)}): "
            f"run `{BIN} sync` again",
            3,
        )

    pages = curate.iter_topic_pages(config.layout)
    # Rules 4-6: the baseline comes before the first curated page.
    if not pages and questions is None:
        return done(
            f"draft the baseline questions: follow step 1 (Draft) of {_BASELINE}, then run `{BIN} sync`",
            4,
        )
    if questions is not None and not confirmed:
        return done(
            "stop: the operator confirms the baseline questions (WAITING ON YOU below); session done", 5
        )
    has_before = any(eval_dir.glob("results-*-before.md"))
    if not pages and not has_before:
        return done(
            "run the 'before' baseline in a session that did not draft the questions (start a new one if "
            f"this one did): follow step 2 (Run) of {_BASELINE}, then run `{BIN} sync`",
            6,
        )

    # Rule 7: what holds the checkpoint.
    blockers = _checkpoint_blockers(docs, pending) if pages else 0
    if blockers:
        return done(
            f"{blockers} curation error(s) hold the checkpoint: run `{BIN} curate`, fix every ERROR "
            f"it lists, then run `{BIN} sync`",
            7,
        )

    # Rule 8: the 'after' baseline (only once the questions are confirmed).
    if confirmed and len(pages) >= AFTER_BASELINE_PAGES and not any(eval_dir.glob("results-*-after.md")):
        return done(
            f"{len(pages)} curated pages exist: run the 'after' baseline in a session that did not write "
            f"them (start a new one if this one did): follow step 2 (Run) of {_BASELINE}, then run "
            f"`{BIN} sync`",
            8,
        )

    # Rule 9: the curation queue, bounded per session.
    rows = _queue_rows(config)
    if rows:
        return done(
            f"{rows} curation row(s) queued: run `{BIN} curate`, curate up to {ROWS_PER_SESSION} of "
            f"them, then run `{BIN} sync`; session done",
            9,
        )
    return done("nothing to do: session done", 10)


def next_lines(config: Config, *, fixes: Sequence[str] = ()) -> list[str]:
    """:meth:`NextStep.lines` of :func:`next_step`; [] (with a logged warning) when the state cannot be read,
    so a caller's own exit status never changes because of the hint."""
    try:
        return next_step(config, fixes=fixes).lines()
    except (OSError, AgentSyncError) as exc:
        _log.warning("next step: cannot read the loop state: %s", exc)
        return []
