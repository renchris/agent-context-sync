"""The loop's next step, worked out from disk (KISS K01; owner: integrator).

:func:`next_step` reads the docs repo, the manifest, the skill copies and the governance queue (never the
network, never a write) and returns the one step an agent takes next, plus the operator waits. The first unmet
rule wins:

1. a status FAIL (a missing docs repo, a missing or stale skill copy, a Graph sign-in; then any fix the caller
   passes) names its fix;
2. no live source other than the inbox: ask which folders, then ``add-source``;
3. a source was never listed, a Graph listing is INCOMPLETE, or files already on this Mac are not converted
   yet: sync again (online-only files waiting for a download budget are a note, and a folder listing a sync
   ran but could not finish is an operator wait, never this rule: another sync would not clear either,
   unless sources.toml by now excludes every empty cloud folder that stopped it; a recording still being
   read is a note, or a wait while no background sync is installed to read it, never this rule);
4. no curated page and no ``_eval/questions.md``: draft the baseline questions;
5. ``_eval`` is still a draft: stop, the operator confirms;
6. no curated page and no ``_eval/results-*-before.md``: run the 'before' baseline in a fresh session;
7. checkpoint blockers: fix the pages ``curate`` lists;
8. :data:`AFTER_BASELINE_PAGES` or more curated pages and no after-results: run the 'after' baseline;
9. the curation queue is not empty: curate up to :data:`ROWS_PER_SESSION` rows, sync, session done;
10. nothing to do: session done.

A local or inbox source whose one-time re-read is not finished (CONTRACTS.md 16.27: files converted before
something this build's converters have) is a note, never a rule: ``sync again: N file(s) ...`` while another
sync reads more of them, and other words once the last sync read none or did not get to the source
(:func:`_reread_notes`).

The text is fixed wording plus counts and source ids, never a mirror path or file name (a page name is
third-party content). One wait is the exception: the ``exclude = [...]`` line for a source's empty cloud
folders names them, since the operator has to paste it; the setup report shows that list as ``<path>``.
The draft baseline's wait says where its two files are: the docs repo's ``_eval`` folder, by its path
(:func:`_shown`). That is the config's own folder and the tool's own file names, as a command line here
names ``~/.local/bin/agentsync``: no mirror path and no document's name.
``sync`` (without ``--mode``) and ``status`` print :meth:`NextStep.lines`.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agentsync import curate, gitops, governance, it_request, skill
from agentsync.arm_local import _unexcluded, exclude_advice
from agentsync.config import Config, SourceConfig
from agentsync.convert.recording import RecordingConverter
from agentsync.cycle import (
    _CHECKPOINT_PENDING_META,
    _EMPTY_DIRS_META,
    _PRESENT,
    _REREAD_BUDGET_S,
    _REREAD_META,
    _SEED_PAGE_NAMES,
    HYDRATION_REFUSED,
    LISTING_HELD,
    NETWORK_POLICY_FAILED,
    RECORDING_PROGRESS_META,
    RECORDING_WAITS,
    _reread_records,
)
from agentsync.errors import AgentSyncError
from agentsync.manifest import Manifest
from agentsync.model import LintFinding, PassKind, RowState, SourceKind, Verdict
from agentsync.ops import launchd
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
SYNC_AGAIN = "sync again: "
"""How the note of an unfinished re-read starts while another sync reads more of it (:func:`_reread_notes`).
The README's setup prompt runs ``sync --materialise-budget 0`` again while a ``note:`` line starts with it,
before its report."""
ROWS_PER_SESSION = 10
"""Rule 9's session bound: curate at most this many queue rows, then sync and end the session."""
AFTER_BASELINE_PAGES = 20
"""Rule 8: the 'after' baseline is due once this many curated pages exist."""

_EVAL_DIR = "_eval"
_BASELINE = 'the agentsync-docs skill\'s "Baseline questions" section'
_STATUS_LINE = re.compile(r"^\s*status:\s*(\S+)\s*$")
_STATUS_LINES_READ = 10  # the skill puts ``status:`` on the first line; a frontmatter fence may precede it
_UNCONVERTED = frozenset({Verdict.CREATED, Verdict.MAYBE_CHANGED, Verdict.CHANGED, Verdict.DEFERRED})
_MS_PER_MINUTE = 60_000
_RECORDING_SUFFIXES = frozenset(RecordingConverter.extensions)


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
    refused: dict[str, int] = field(default_factory=dict)  # the OS refused the download (no budget clears it)
    recording: dict[str, int] = field(default_factory=dict)  # recordings the recording pass is still reading
    read_ms: int = 0  # ... the milliseconds of them read so far (manifest meta recording_progress:)
    total_ms: int = 0  # ... and the length of those whose length is known
    unknown: int = 0  # ... how many have no length yet (no piece read)
    recording_refused: dict[str, int] = field(default_factory=dict)  # online-only recordings not downloaded
    recording_refused_bytes: int = 0


def _ids(counts: dict[str, int]) -> str:
    return ", ".join(sorted(counts))


def _shown(path: Path) -> str:
    """``path`` as a line names it: ``~/...`` under the home folder, as the commands in these lines are
    written, else as it is."""
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return path.as_posix()


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


def skill_state(docs_repo: Path) -> str:
    """``current`` when every skill copy exists and holds this build's text, ``missing`` when a copy does not
    exist (or cannot be read), else ``stale`` (a sync writes them; a failed write leaves them missing or
    stale)."""
    text = skill.skill_text(docs_repo)
    state = "current"
    for path in skill.skill_paths():
        try:
            if path.read_text(encoding="utf-8") != text:
                state = "stale"
        except (OSError, UnicodeDecodeError):
            return "missing"
    return state


def baseline_state(docs_repo: Path) -> str:
    """Where the baseline questions stand: ``missing`` (no ``_eval/questions.md``), ``draft`` (it or
    ``answers.md`` is not ``status: confirmed``), ``confirmed``, ``before`` (a 'before' results file exists)
    or ``after`` (an 'after' results file exists)."""
    eval_dir = docs_repo / _EVAL_DIR
    if any(eval_dir.glob("results-*-after.md")):
        return "after"
    if any(eval_dir.glob("results-*-before.md")):
        return "before"
    questions = _eval_status(eval_dir / "questions.md")
    if questions is None:
        return "missing"
    confirmed = questions == "confirmed" and _eval_status(eval_dir / "answers.md") == "confirmed"
    return "confirmed" if confirmed else "draft"


def _progress(value: str | None) -> tuple[int, int] | None:
    """``(done_ms, total_ms)`` of a ``recording_progress:`` meta value; None when it is missing, empty,
    malformed or has no length."""
    parts = (value or "").split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    done, total = int(parts[0]), int(parts[1])
    return (min(done, total), total) if total > 0 else None


def _unpublished(manifest: Manifest, sources: Sequence[SourceConfig]) -> _Files:
    """Count each source's files that are not converted yet, split into local, online-only, over-budget
    online-only and OS-refused (``state_reason`` :data:`HYDRATION_REFUSED`). An item that failed (``error``)
    is retried by every sync and is not counted: it would make rule 3 loop.

    Recordings have buckets of their own and never make rule 3 (spec S0 rules 3 and 6): one the recording
    pass is still reading (:data:`RECORDING_WAITS`, with the minutes read from its ``recording_progress:``
    meta), and an online-only one whose download failed, which the person fixes in Finder, not with a sync.
    An online-only recording not tried yet waits for the recording download allowance, never for the
    source's document budget, so it is never over that budget."""
    out = _Files()
    for src in sources:
        for row in manifest.iter_items(src.id, states=(RowState.LIVE, RowState.DATALESS)):
            if row.is_dir:
                continue
            recording = Path(row.rel_path).suffix.lower() in _RECORDING_SUFFIXES
            if row.state_reason == RECORDING_WAITS:
                out.recording[src.id] = out.recording.get(src.id, 0) + 1
                read = _progress(manifest.get_meta(f"{RECORDING_PROGRESS_META}{src.id}:{row.stable_id}"))
                if read is None:
                    out.unknown += 1
                else:
                    out.read_ms += read[0]
                    out.total_ms += read[1]
                continue
            pending = row.last_verdict in _UNCONVERTED or (
                row.last_verdict is Verdict.DATALESS and row.content_sha256 is None
            )
            if not pending:
                continue
            online = src.kind.is_graph or row.dataless or row.state is RowState.DATALESS
            if row.state_reason == HYDRATION_REFUSED and recording:
                bucket = out.recording_refused
                out.recording_refused_bytes += max(row.size or 0, 0)
            elif row.state_reason == HYDRATION_REFUSED:
                bucket = out.refused
            elif not online:
                bucket = out.local
            elif not recording and max(row.size or 0, 0) > src.max_materialise_bytes:
                bucket = out.over
            else:
                bucket = out.online
            bucket[src.id] = bucket.get(src.id, 0) + 1
    return out


def _minutes(files: _Files) -> str:
    """`` (35 of 127 minutes)`` for the recordings still being read, floored to whole minutes; ``of at least``
    while some have no length yet, and "" while none has one."""
    if not files.total_ms:
        return ""
    of = "of at least" if files.unknown else "of"
    return f" ({files.read_ms // _MS_PER_MINUTE} {of} {files.total_ms // _MS_PER_MINUTE} minutes)"


def _recording_lines(files: _Files, *, background: bool) -> tuple[list[str], list[str]]:
    """The waits and notes of the recordings (spec S0 rules 3 and 6); none of them blocks the next step.

    A recording still being read is a note once a background job is installed (each background sync reads
    more), else a wait naming the two runs that read recordings: the job ``install.sh`` installs, and
    ``materialise`` of the file. An online-only recording agentsync could not download is a note sending the
    person to Finder: it is never a wait, since the next background sync reads it once it is on the Mac."""
    waits: list[str] = []
    notes: list[str] = []
    if files.recording:
        counted = f"{sum(files.recording.values())} recording(s) in {_ids(files.recording)}"
        if background:
            notes.append(
                f"{counted} are still being read{_minutes(files)}; each background sync reads more; they do "
                "not block the next step"
            )
        else:
            waits.append(
                f"{counted} wait to be read{_minutes(files)}, and no background sync is installed to read "
                f"them (an interactive sync reads no recording): run `{INSTALL_SH} --confirm-install-agent`, "
                f"or `{BIN} materialise <file>` for one recording; they do not block the next step"
            )
    if files.recording_refused:
        notes.append(
            f"{sum(files.recording_refused.values())} online-only recording(s) in "
            f"{_ids(files.recording_refused)} ({files.recording_refused_bytes / 1e9:.1f} GB) could not be "
            "downloaded by agentsync: in Finder choose Always Keep on This Device on their folder, or "
            "Download Now on a file; the next background sync reads them; they do not block the next step"
        )
    return waits, notes


_REREAD_AGAIN = (
    "are still to be read again, once, for what this build's converters have gained (each sync reads about "
    f"{_REREAD_BUDGET_S / 60:g} minutes' worth)"
)
_REREAD_STUCK = (
    "wait to be read again, and the last sync read none of them (a converter or on-device OCR that cannot "
    "run, or a folder that could not be listed): another sync does not clear it"
)
_REREAD_CROWDED = (
    "wait to be read again, and the last sync read none of them: new and changed files took its OCR time, "
    "more files joined, and more downloads wait. They are read once a sync has OCR time left"
)


def _reread_notes(
    sources: Sequence[str], unread: Sequence[str], runs: Sequence[dict[str, int]], *, downloads: bool
) -> list[str]:
    """The notes for ``sources``, the ones whose one-time re-read is not finished (manifest meta
    ``reread:<source id>``: the newest record is not ``done``, or a cycle died reading a file); [] for
    none.  ``unread``: those of them whose newest pass was skipped or failed, so the last sync did not get
    to their files.  ``runs``: the counts of the two newest runs that looked, the newest first
    (``Manifest.last_reread_counts``).  ``downloads``: online-only files wait for a later sync's download
    budget, so the next sync brings new files.

    A note starts :data:`SYNC_AGAIN` only while another sync reads more.  That is: the newest run that
    looked read a file again; or it read none because other work used up its OCR time, which the next sync
    has for them; or there is no count to go by (it left none, so a file joined after it looked, or no run
    has looked yet).  Every other state gets other words, because "sync again" would never end in it:

    - a source of ``unread`` (macOS held its listing, or the source failed): no sync has looked since, so
      what an earlier run read says nothing.  It gets a note of its own, without a count;
    - the helper stopped working, or the run read none with OCR time left (a converter that cannot run, a
      folder it could not list);
    - the run read none, its OCR time was used up, it left more than the run before, and ``downloads``:
      new files have the OCR time first, the ones converted past it join the files to read again, and the
      next sync downloads more.  Used-up OCR time is no progress then."""

    def said(ids: Sequence[str], count: int, words: str) -> str:
        counted = f"{count} " if count else ""
        return f"{counted}file(s) in {', '.join(ids)} {words}; it does not block the next step"

    notes: list[str] = []
    looked = sorted(set(sources) - set(unread))
    if looked:
        new = runs[0] if runs else None
        left = (new or {}).get("reread_left", 0)
        grew = len(runs) > 1 and left > runs[1].get("reread_left", 0)
        if new is not None and new.get("ocr_down"):
            notes.append(said(looked, left, _REREAD_STUCK))
        elif new is None or not left or new.get("reread"):
            notes.append(SYNC_AGAIN + said(looked, left, _REREAD_AGAIN))
        elif not new.get("ocr_over"):
            notes.append(said(looked, left, _REREAD_STUCK))
        elif grew and downloads:
            notes.append(said(looked, left, _REREAD_CROWDED))
        else:
            notes.append(SYNC_AGAIN + said(looked, left, _REREAD_AGAIN))
    held = sorted(set(sources) & set(unread))
    if held:
        notes.append(said(held, 0, _REREAD_STUCK))
    return notes


def _empty_dirs(manifest: Manifest, src: SourceConfig) -> tuple[bool, dict[str, int]]:
    """The zero-child cloud folders the source's last walk stored (manifest meta ``empty_cloud_dirs:<id>``):
    whether it stored any, and those sources.toml does not exclude today, each with the number of files the
    mirror still holds below it. A folder usually became empty because its files were removed upstream; while
    the listing is incomplete those deletions are held, and excluding the folder would retire the pages as a
    scope change, past the deletion breaker and with no purge queued."""
    try:
        stored = json.loads(manifest.get_meta(_EMPTY_DIRS_META + src.id) or "[]")
    except ValueError:
        stored = []
    names = [d for d in stored if isinstance(d, str)] if isinstance(stored, list) else []
    below = dict.fromkeys(_unexcluded(src, names), 0)
    if below:
        for row in manifest.iter_items(src.id, states=_PRESENT):
            if row.is_dir:
                continue
            parent = row.rel_path
            while "/" in parent:
                parent = parent.rsplit("/", 1)[0]
                if parent in below:
                    below[parent] += 1
                    break
    return bool(names), below


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


def queue_rows(config: Config) -> int:
    """Rule 9: refresh-queue rows (STALE and the other verdicts) plus mirror pages no curated page cites."""
    layout = config.layout
    rc, verdicts = curate.refresh_queue(layout)
    rows, _entities, _findings = curate.generate_depends(layout)  # live: pages written since the last sync
    uncovered = curate.uncovered_mirror_pages(layout, rows)
    return (len(verdicts) if rc != 2 else 0) + len(uncovered)


def next_step(config: Config, *, fixes: Sequence[str] = (), count_queue: bool = True) -> NextStep:
    """The first unmet rule of the loop (module docstring) and the operator's waits, from disk only (no
    source folder is listed: the empty cloud folders it names are the ones the last sync's walk stored).

    ``fixes`` are the fixes of status FAILs the caller already ran (rule 1, after the disk checks here).
    ``count_queue=False`` (STATE.md, written by every cycle) skips rule 9's count, which reads every uncited
    mirror page: past rule 8 the step sends the agent to ``curate``, which counts it."""
    docs = expand(config.docs_repo)
    waits: list[str] = []
    notes: list[str] = []

    def done(step: str, rule: int) -> NextStep:
        return NextStep(step, tuple(waits), tuple(notes), rule)

    queued = governance.pending_purges(config.state_paths.root)
    if queued:
        waits.append(f"{len(queued)} queued purge(s): run `{BIN} purge --queue`")

    # Rule 1: status FAILs.
    live = config.live_sources()
    if not (docs / ".git").exists():
        # add-source creates whatever is missing (KISS K14; init is hidden). A source id, never its path.
        folder = next((s for s in live if s.kind is SourceKind.LOCAL), None)
        which = f"the folder of source {folder.id!r}" if folder is not None else "a folder to sync"
        return done(
            f'the docs repo does not exist yet: run `{BIN} add-source "<folder>"` with {which} '
            "(it creates whatever is missing)",
            1,
        )
    db = config.state_paths.db
    files = _Files()
    incomplete: list[str] = []
    unlisted: list[str] = []  # a local listing ran but could not finish: the operator's to fix
    empty: list[tuple[SourceConfig, dict[str, int]]] = []  # ... because of these empty cloud folders
    inbox_partial: list[str] = []  # an inbox listing ran but could not finish (often a file being written)
    blocked: list[str] = []  # a Graph source the network policy fails: IT's to fix
    held: list[str] = []  # a local walk timed out on a read macOS holds for an Allow prompt (field N8)
    signin: list[str] = []
    rereading: list[str] = []  # the one-time re-read of files from before a capability is not finished
    unread: list[str] = []  # ... and the source's newest pass was skipped or failed: no sync got to them
    reread_runs: list[dict[str, int]] = []
    pending: str | None = None
    if db.exists():
        with Manifest(db) as manifest:
            reread_runs = manifest.last_reread_counts()
            for src in live:
                newest = next(iter(_reread_records(manifest.get_meta(_REREAD_META + src.id))), None)
                if newest is not None and (not newest[1] or newest[3] is not None):
                    rereading.append(src.id)
                row = manifest.get_source(src.id)
                last = manifest.last_source_pass(src.id)
                if src.id in rereading and last is not None and (last.skipped_reason or last.error):
                    unread.append(src.id)
                if (
                    src.kind.is_graph
                    and last is not None
                    and (last.skipped_reason or "").startswith(NETWORK_POLICY_FAILED)
                ):
                    blocked.append(src.id)
                elif last is not None and (last.skipped_reason or "").startswith(LISTING_HELD):
                    # Never "exclude it": once the prompt is answered, a narrowed scope retires the folder's
                    # pages in one pass, past the deletion breaker.
                    held.append(src.id)
                elif row is None or not row.enumeration_complete:
                    # A local walk is always FULL: one that ran and still came back incomplete hit a folder
                    # it cannot list (TCC, an empty cloud folder, a missing root or sentinel), which another
                    # sync does not clear. A Graph FULL pass resumes, so sync again is right for it.
                    ran_full = last is not None and last.pass_kind is PassKind.FULL
                    if ran_full and src.kind is SourceKind.LOCAL:
                        stored, below = _empty_dirs(manifest, src)
                        if below:
                            empty.append((src, below))
                        elif stored:
                            incomplete.append(src.id)  # all excluded since that walk: the next sync lists it
                        else:
                            unlisted.append(src.id)
                    elif ran_full and src.kind is SourceKind.INBOX:
                        inbox_partial.append(src.id)
                    else:
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
    if files.refused:
        waits.append(
            f"{sum(files.refused.values())} online-only file(s) in {_ids(files.refused)} could not be "
            "downloaded (macOS refused): in Finder, choose Download Now (or Always Keep on This Device) on "
            f"their folder, then run `{BIN} sync`"
        )
    if held:
        waits.append(
            f"macOS held the listing of {', '.join(sorted(held))} for a privacy prompt: click Allow on the "
            f"macOS prompt (it can sit behind other windows), then run `{BIN} sync`"
        )
    for src, below in empty:
        # An empty cloud folder is unknown, never empty, on every pass. The line to paste names only the
        # folders with nothing mirrored below them; one that held mirrored files gets no paste line, since
        # excluding it retires those pages in one pass, past the deletion breaker (as for a held listing).
        clear = [d for d, files in below.items() if not files]
        gone = [files for files in below.values() if files]
        if clear:
            waits.append(
                f"{len(clear)} empty cloud folder(s) keep the listing of {src.id} incomplete (deletions "
                "held; another sync does not clear it): if they are meant to be empty, "
                f"{exclude_advice(src, clear)}"
            )
        if gone:
            waits.append(
                f"{len(gone)} empty cloud folder(s) in {src.id} held {sum(gone)} file(s) the mirror still "
                "has (the listing stays incomplete, so their deletion is held; another sync does not clear "
                "it): if the files were removed on purpose, remove the empty folder(s) from the cloud drive "
                "too, and later syncs take the pages out with the usual deletion check; excluding such a "
                "folder instead retires its pages at once, with no deletion check and no purge queued. "
                f"`{BIN} sync -v` names the folders"
            )
    if unlisted:
        waits.append(
            f"a folder in {', '.join(sorted(unlisted))} could not be listed (no access, or a missing folder "
            f"or sentinel; another sync does not clear it): `{BIN} sync -v` names it; grant Files and "
            "Folders access, or add it to that source's exclude in sources.toml"
        )
    if blocked:
        waits.append(
            f"the network refuses Microsoft Graph for {', '.join(sorted(blocked))} (proxy, TLS inspection or "
            f"PAC): IT must allow it; `{BIN} it-request --out {it_request.DEFAULT_OUT}` drafts the request"
        )
    if inbox_partial:
        notes.append(
            f"the inbox ({', '.join(sorted(inbox_partial))}) was not fully listed (a file still being "
            "written, or a folder that cannot be read); a later sync lists it; it does not block the next "
            "step"
        )
    if files.online:
        notes.append(
            f"{sum(files.online.values())} online-only file(s) in {_ids(files.online)} wait for a later "
            "sync's download budget; they do not block the next step"
        )
    recording_waits, recording_notes = _recording_lines(
        files, background=bool(files.recording) and launchd.agents_installed(config)
    )
    waits += recording_waits
    notes += recording_notes
    notes += _reread_notes(rereading, unread, reread_runs, downloads=bool(files.online))
    eval_dir = docs / _EVAL_DIR
    questions = _eval_status(eval_dir / "questions.md")
    answers = _eval_status(eval_dir / "answers.md")
    confirmed = questions == "confirmed" and answers == "confirmed"
    if questions is not None and not confirmed:
        # Where the two files are, not only their names: whoever confirms them did not write them (v9
        # rehearsal, 2026-10-07).
        waits.append(
            f"the baseline questions are a draft: in {_shown(eval_dir)}, keep about 10 in questions.md, "
            "correct the answers in answers.md, and change both files to status: confirmed"
        )
    if skill_state(docs) != "current":
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
    if not count_queue:
        return done(
            f"run `{BIN} curate` and follow its NEXT line (it counts the curation queue; curate up to "
            f"{ROWS_PER_SESSION} rows, then run `{BIN} sync`; session done)",
            9,
        )
    rows = queue_rows(config)
    if rows:
        return done(
            f"{rows} curation row(s) queued: run `{BIN} curate`, curate up to {ROWS_PER_SESSION} of "
            f"them, then run `{BIN} sync`; session done",
            9,
        )
    return done("nothing to do: session done", 10)


def next_lines(config: Config, *, fixes: Sequence[str] = (), count_queue: bool = True) -> list[str]:
    """:meth:`NextStep.lines` of :func:`next_step`; [] (with a logged warning) when the state cannot be read,
    so a caller's own exit status never changes because of the hint."""
    try:
        return next_step(config, fixes=fixes, count_queue=count_queue).lines()
    except (OSError, AgentSyncError) as exc:
        _log.warning("next step: cannot read the loop state: %s", exc)
        return []


def status_line(config: Config) -> str:
    """``status``'s one loop line (KISS K08a): ``loop: skill <current|stale|missing> · inbox <on|missing|off>
    · baseline <missing|draft|confirmed|before|after> · topics N · checkpoint <date|never> · to curate N ·
    archive <on|off>``. ``to curate`` is :func:`queue_rows` (not the purge queue, which status prints on its
    own line). Disk only; a part that cannot be read shows ``?``."""
    docs = expand(config.docs_repo)

    def part(name: str, fn: Callable[[], object]) -> str:
        try:
            return f"{name} {fn()}"
        except (OSError, AgentSyncError) as exc:
            _log.warning("loop: cannot read %s: %s", name, exc)
            return f"{name} ?"

    def inbox() -> str:
        boxes = [s for s in config.live_sources() if s.kind is SourceKind.INBOX and s.path is not None]
        if not boxes:
            return "off"
        return "on" if all(expand(s.path).is_dir() for s in boxes if s.path is not None) else "missing"

    def checkpoint() -> str:
        found = gitops.curated_checkpoint(docs) if (docs / ".git").exists() else None
        return found[1][:10] if found is not None else "never"

    def archive() -> str:
        return "on" if governance.load_governance(config.config_path).archive else "off"

    parts = (
        part("skill", lambda: skill_state(docs)),
        part("inbox", inbox),
        part("baseline", lambda: baseline_state(docs)),
        part("topics", lambda: len(curate.iter_topic_pages(config.layout))),
        part("checkpoint", checkpoint),
        part("to curate", lambda: queue_rows(config) if (docs / ".git").exists() else 0),
        part("archive", archive),
    )
    return "loop: " + " · ".join(parts)
