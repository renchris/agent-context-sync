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
third-party content). No wait names a folder: a source's empty cloud folders are counted, the wait names
``exclude-empty <id>`` (B13 round 4 Q71), and the sync's alarm line, kept in the docs repo's STATE.md,
names the folders (:func:`_named`; round 5 F43, round 6 G13, G42). ``exclude-empty`` prints the
``exclude = [...]`` paste line only as its fallback, when it cannot write sources.toml (B13 round 6 G43).
The draft baseline's wait says where its two files are: the docs repo's ``_eval`` folder, by its path
(:func:`_shown`). That and STATE.md are the config's own folder and the tool's own file names, as a
command line here names ``~/.local/bin/agentsync``: no mirror path and no document's name. Rules 4, 6 and 8
name the docs repo's ``AGENTS.md`` the same way (:func:`_baseline`).
``sync`` (without ``--mode``) and ``status`` print :meth:`NextStep.lines`.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agentsync import curate, gitops, governance, it_request, skill
from agentsync.arm_local import _dir_excluded, _folder_glob, _unexcluded
from agentsync.config import Config, SourceConfig, canonical_source_root
from agentsync.convert import NO_CONVERTER_PREFIX
from agentsync.convert.recording import RecordingConverter
from agentsync.cycle import (
    _CHECKPOINT_PENDING_META,
    _EMPTY_DIRS_META,
    _PRESENT,
    _REREAD_BUDGET_S,
    _REREAD_META,
    _ROOT_UNLISTED_META,
    _SCOPE_ROOT_META,
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
_AGENTS_MD = "AGENTS.md"
"""The docs repo's root guide that also carries the Baseline questions section (``publish.root_guide``)."""
_STATE_MD = Path("_sync", "STATE.md")
"""The docs repo's live status page (``DocsLayout.state_md``), which every cycle rewrites with its alarms."""
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
    # the doctor checks the waits explain (the setup report's "yours", §16.39 N16); lines() never prints them
    checks: tuple[str, ...] = ()
    # per source, how many of the waits explain its ``heartbeat.<id>`` check, so doctor says "the line" for
    # one and "each line" for more (B13 round 6 G44); one empty-folder block can print three
    wait_counts: Mapping[str, int] = field(default_factory=dict)

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


def _named(config: Config, ids: str, what: str) -> str:
    """Where a wait's folders are named, read-only (B13 round 6 G13, G42): the ``alarm:`` line a sync prints
    under the source names them, and every cycle, the background one too, keeps it in the docs repo's
    STATE.md. The alarm names five, so ``sync -v`` (a full sync, which downloads what a plain one does) is
    named for the rest. The wait itself keeps a count and never a path (B13 round 5 F43)."""
    state = _shown(expand(config.docs_repo) / _STATE_MD)
    return (
        f"the alarm line a sync prints for {ids}, also kept in {state}, names {what} "
        f"(the first five; `{BIN} sync -v` logs each one)"
    )


def _baseline(docs: Path) -> str:
    """Where the Baseline questions section is, for the NEXT lines of rules 4, 6 and 8: the skill's section,
    and the docs repo's root AGENTS.md by its path (an agent that loads no skill reads that file; a bare
    "AGENTS.md" would be the agent's own project's when it runs from another folder)."""
    return f"{_BASELINE} (also in {_shown(docs / _AGENTS_MD)})"


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
    pass is still reading (:data:`RECORDING_WAITS`, or a ``no converter`` stub from before recordings were
    read that it has read in part, with the minutes read from its ``recording_progress:`` meta), and an
    online-only one whose download failed, which the person fixes in Finder, not with a sync.
    An online-only recording not tried yet waits for the recording download allowance, never for the
    source's document budget, so it is never over that budget."""
    out = _Files()
    for src in sources:
        for row in manifest.iter_items(src.id, states=(RowState.LIVE, RowState.DATALESS, RowState.REFUSED)):
            if row.is_dir:
                continue
            recording = Path(row.rel_path).suffix.lower() in _RECORDING_SUFFIXES
            key = f"{RECORDING_PROGRESS_META}{src.id}:{row.stable_id}"
            if row.state is RowState.REFUSED:  # counted only while the stub path is part way through it
                stub = (row.state_reason or "").startswith(NO_CONVERTER_PREFIX)
                if not (recording and stub and _progress(manifest.get_meta(key))):
                    continue
            if row.state_reason == RECORDING_WAITS or row.state is RowState.REFUSED:
                out.recording[src.id] = out.recording.get(src.id, 0) + 1
                read = _progress(manifest.get_meta(key))
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


@dataclass(frozen=True, slots=True)
class _EmptyDirs:
    """What :func:`_empty_dirs` reads in one walk of the source's rows."""

    stored: bool  # the last walk stored some zero-child cloud folder
    below: dict[str, int]  # those not excluded today, each with the mirrored files below it
    holding: frozenset[str]  # every folder (relative POSIX path) with a mirrored file at or below it


def _empty_dirs(manifest: Manifest, src: SourceConfig) -> _EmptyDirs:
    """The zero-child cloud folders the source's last walk stored (manifest meta ``empty_cloud_dirs:<id>``):
    whether it stored any, and those sources.toml does not exclude today, each with the number of files the
    mirror still holds below it. A folder usually became empty because its files were removed upstream; while
    the listing is incomplete those deletions are held, and excluding the folder would retire the pages as a
    scope change, past the deletion breaker and with no purge queued. A file counts below a folder whose
    name differs only in case, as the exclude glob matches (``paths.glob_match`` ignores case): the rows of a
    case-only rename upstream are still held under the old name (B13 round 6 G55). The same walk keeps every
    folder holding files, for :func:`_excludable`, so the rows are read once, not twice."""
    try:
        stored = json.loads(manifest.get_meta(_EMPTY_DIRS_META + src.id) or "[]")
    except ValueError:
        stored = []
    names = [d for d in stored if isinstance(d, str)] if isinstance(stored, list) else []
    below = dict.fromkeys(_unexcluded(src, names), 0)
    holding: set[str] = set()
    if below:
        by_key: dict[str, list[str]] = {}
        for d in below:
            by_key.setdefault(d.casefold(), []).append(d)
        for row in manifest.iter_items(src.id, states=_PRESENT):
            if row.is_dir:
                continue
            parts = row.rel_path.split("/")[:-1]
            folders = ["/".join(parts[: i + 1]) for i in range(len(parts))]
            holding.update(folders)
            for folder in reversed(folders):  # the nearest stored folder above the file
                if hit := by_key.get(folder.casefold()):
                    for d in hit:
                        below[d] += 1
                    break
    return _EmptyDirs(bool(names), below, frozenset(holding))


def _excludable(empty_dirs: _EmptyDirs) -> tuple[list[str], list[str]]:
    """Of ``below`` (:func:`_empty_dirs`), the folders with no mirrored file below them, split in two: those
    ``agentsync exclude-empty`` excludes, and those whose exclude glob would also prune a folder holding
    mirrored files (a ``[`` is written as ``?``: ``arm_local._glob_literal``), which it leaves, since
    excluding one would retire those pages at once. One rule for the command and the wait naming it (B13
    round 5 F65), so the wait never names a command that refuses its folder."""
    empty = [d for d, files in empty_dirs.below.items() if not files]
    if not empty:
        return [], []
    held = empty_dirs.holding
    clear: list[str] = []
    collides: list[str] = []
    for d in empty:
        (collides if any(_dir_excluded(h, [_folder_glob(d)]) for h in held) else clear).append(d)
    return clear, collides


def empty_cloud_folders_held(manifest: Manifest, src: SourceConfig) -> bool:
    """Whether ``src``'s listing stays incomplete because of empty cloud folders its last walk stored and
    today's exclude does not prune: the case :func:`next_step` gives WAITING ON YOU lines that say another
    sync does not clear it (a local or inbox source, its row incomplete, its newest pass FULL and not held
    for a privacy prompt, and :func:`_empty_dirs` names some). Stored folders that are all excluded now are
    not this case: the next sync lists the source. Doctor's heartbeat check words its fix by it (B13 round
    3 R53). Reads stored state only, never a folder."""
    if src.kind not in (SourceKind.LOCAL, SourceKind.INBOX):
        return False
    row = manifest.get_source(src.id)
    last = manifest.last_source_pass(src.id)
    if (row is not None and row.enumeration_complete) or last is None or last.pass_kind is not PassKind.FULL:
        return False
    if (last.skipped_reason or "").startswith(LISTING_HELD):
        return False
    return bool(_empty_dirs(manifest, src).below)


def _missed(manifest: Manifest, source_id: str) -> int:
    """The mirrored files the source's last walk did not list, of those last listed under the root it has
    now (manifest meta ``scope_root:<id>``, ``<run>:<path>``): a complete pass retires a row last listed under
    an earlier root as a scope change instead of checking it (``cycle._Cycle._root_since``)."""
    since = (manifest.get_meta(_SCOPE_ROOT_META + source_id) or "").partition(":")[0]
    return manifest.unlisted_count(source_id, since=int(since) if since.isdigit() else 0)


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
    checks: list[str] = []  # doctor's check for each wait that explains one (NextStep.checks)
    about: dict[str, int] = {}  # ... and per source, the waits that explain its heartbeat (wait_counts)

    def done(step: str, rule: int) -> NextStep:
        return NextStep(step, tuple(waits), tuple(notes), rule, tuple(checks), dict(about))

    def explains(*sources: str) -> None:
        """The wait just appended explains each source's heartbeat check."""
        for sid in sources:
            about[sid] = about.get(sid, 0) + 1

    # a kept upstream deletion is no wait: no run clears it while its file is listed; once the file is gone it
    # is a queued purge again, and the wait returns. A kept label escalation is no wait either: its file is
    # published again, and running it would erase that page (B13 round 6 G30)
    queued, kept = governance.partition_purges(config.state_paths)
    if queued:
        waits.append(
            f"{len(queued)} queued purge(s): preview with `{BIN} purge --queue --dry-run` (writes nothing) "
            f"before you run `{BIN} purge --queue` (rewrites history)"
        )
        checks.append("governance.purge_queue")
    notes += governance.kept_lines(kept)

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
    unlisted: list[tuple[str, int]] = []  # a local listing ran but could not finish: the operator's to fix
    root_unlisted: list[str] = []  # ... at the root folder itself, which no exclude can clear
    # ... because of these empty cloud folders: (source, files below each, the clear ones, the colliding ones,
    # mirrored files the last walk missed)
    empty: list[tuple[SourceConfig, dict[str, int], list[str], list[str], int]] = []
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
                    if ran_full and src.kind in (SourceKind.LOCAL, SourceKind.INBOX):
                        # An empty cloud folder is unknown on every pass in an inbox too: the same waits.
                        found = _empty_dirs(manifest, src)
                        stored, below = found.stored, found.below
                        if below:
                            clear, collides = _excludable(found)
                            empty.append((src, below, clear, collides, _missed(manifest, src.id)))
                        elif src.kind is SourceKind.INBOX:
                            inbox_partial.append(src.id)
                        elif stored:
                            incomplete.append(src.id)  # all excluded since that walk: the next sync lists it
                        elif manifest.get_meta(_ROOT_UNLISTED_META + src.id):
                            root_unlisted.append(src.id)
                        else:
                            unlisted.append((src.id, _missed(manifest, src.id)))
                    else:
                        incomplete.append(src.id)
                if row is not None and row.breaker_tripped_at is not None:
                    later = "" if row.enumeration_complete else f" once a sync lists {src.id} in full"
                    waits.append(
                        f"the deletion breaker tripped on {src.id} ({row.breaker_candidates or 0} file(s) "
                        f"gone): if they really were deleted, run `{BIN} accept-deletions {src.id}`{later}"
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
        checks.extend(f"heartbeat.{s}" for s in sorted(held))
        explains(*held)
    for src, below, clear, collides, missed in empty:
        checks.append(f"heartbeat.{src.id}")  # below is never empty: one wait or more follow
        # An empty cloud folder is unknown, never empty, on every pass. The line naming exclude-empty counts
        # only the folders that command excludes (:func:`_excludable`, one rule for both): one that held
        # mirrored files gets no such line, since excluding it retires those pages in one pass, past the
        # deletion breaker (as for a held listing), and one whose glob would also match a folder holding
        # mirrored files gets its own line (B13 round 5 F65: the command left it, so the wait never cleared).
        gone = [files for files in below.values() if files]
        # What completing the listing will check (N26): the mirrored files the last walk did not list, a
        # count and never a path, once per source, on its last line: the gone line when there is one (its
        # step is what completes the listing, and "in all" says its own count is among them), else the
        # paste line. A cleared folder holds none of them, so the paste retires none: each takes the
        # two-pass deletion check and the breaker (and a queued purge, under the purge setting).
        size = (
            f"the last walk of {src.id} did not list {missed} file(s) the mirror still has: once the listing "
            "is complete, later syncs take out the pages of those still absent, with the usual deletion check"
            if missed
            else ""
        )
        if clear:
            waits.append(
                # Each referent named, no pronoun (B13 round 4 Q11): "it" read as the folder, as if a sync
                # would delete it, and excluding is not the only way the held deletions are released.
                f"{len(clear)} empty cloud folder(s) keep the listing of {src.id} incomplete: an "
                "empty folder in a cloud folder is expected and loses nothing, but while the listing is "
                f"incomplete, deletions in {src.id} are held, not applied, and another sync does not "
                "complete it; if the folder(s) are meant to be empty, "
                # One command, not a hand edit of sources.toml (B13 round 4 Q71): it excludes every such
                # folder, so no second round for a sixth one, and checks the file before it writes it.
                f"run `{BIN} exclude-empty {shlex.quote(src.id)}` (it adds them to that source's exclude in "
                f"{_shown(config.config_path)}; an excluded folder is not mirrored if it later gains files); "
                # Which folders, by where they are named (B13 round 5 F43; round 6 G13, G42): the wait
                # carries a count and never a path, and status prints no folder of the source.
                + _named(config, src.id, "the folder(s)")
                + (f"; {size}" if size and not gone and not collides else "")
            )
            explains(src.id)
        if collides:
            waits.append(
                f"{len(collides)} empty cloud folder(s) in {src.id} have a [ in the name, so an exclude of "
                "one would also match a folder whose files are mirrored (the listing stays incomplete, so "
                f"deletions in {src.id} are held, and another sync does not clear it): rename or remove the "
                "empty folder(s) in the cloud drive; "
                + _named(config, src.id, "them")
                + (f"; {size}" if size and not gone else "")
            )
            explains(src.id)
        if gone:
            waits.append(
                f"{len(gone)} empty cloud folder(s) in {src.id} held {sum(gone)} file(s) the mirror still "
                "has (the listing stays incomplete, so their deletion is held; another sync does not clear "
                "it): if the files were removed on purpose, remove the empty folder(s) from the cloud drive "
                "too, and later syncs take the pages out with the usual deletion check; excluding such a "
                "folder instead retires its pages at once, with no deletion check and no purge queued; "
                + _named(config, src.id, "the folders")
                + (f"; in all, {size}" if size else "")
            )
            explains(src.id)
    # A source whose last walk missed mirrored files gets its own line: excluding the folder retires their
    # pages at once (the gone line's warning), so that line never offers the exclude as a plain choice.
    if bare := sorted(s for s, missed in unlisted if not missed):
        waits.append(
            f"a folder in {', '.join(bare)} could not be listed (no access, or a missing folder "
            f"or sentinel; another sync does not clear it): {_named(config, ', '.join(bare), 'it')}; grant "
            "Files and Folders access, or add it to that source's exclude in sources.toml"
        )
        explains(*bare)
    for sid, missed in sorted((s, m) for s, m in unlisted if m):
        waits.append(
            f"a folder in {sid} could not be listed (no access, or a missing folder or sentinel; another "
            f"sync does not clear it): {_named(config, sid, 'it')}; grant Files and Folders access (the safe "
            f"fix); the last walk of {sid} did not list {missed} file(s) the mirror still has, and adding "
            "the folder to that source's exclude retires the pages under it at once, with no deletion check "
            "and no purge queued"
        )
        explains(sid)
    checks.extend(f"heartbeat.{s}" for s, _ in sorted(unlisted))
    for sid in sorted(root_unlisted):  # a root cannot be excluded: never offer it
        waits.append(
            f"the folder of {sid} itself could not be listed or is empty (an unmounted volume, a sync client "
            "that has not filled it yet, or no access; another sync does not clear it): mount it or let the "
            "sync client fill it, or grant Files and Folders access; to stop syncing it on purpose, set "
            f'state = "paused" in [[source]] id = "{sid}" in {_shown(config.config_path)}'
        )
        checks.append(f"heartbeat.{sid}")
        explains(sid)
    if blocked:
        waits.append(
            f"the network refuses Microsoft Graph for {', '.join(sorted(blocked))} (proxy, TLS inspection or "
            f"PAC): IT must allow it; `{BIN} it-request --out {it_request.DEFAULT_OUT}` drafts the request"
        )
    if inbox_partial:
        notes.append(
            f"the inbox ({', '.join(sorted(inbox_partial))}) was not fully listed: a later sync lists a file "
            "that was still being written, but not a folder it cannot read or an empty cloud folder "
            f"(`{BIN} sync -v` names the cause); it does not block the next step"
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
            # "up to", "all of them if there are fewer": the skill drafts fewer than 10 for a small mirror,
            # and the file holds what is left after the operator's cuts, not what was drafted (B13 round 5
            # F1, F47). No count is read: the operator edits the file by hand.
            f"the baseline questions are a draft: in {_shown(eval_dir)}, keep up to about 10 in "
            "questions.md (all of them if there are fewer), correct the answers in answers.md, and change "
            "both files to status: confirmed"
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
            f"draft the baseline questions: follow step 1 (Draft) of {_baseline(docs)}, "
            f"then run `{BIN} sync`",
            4,
        )
    if questions is not None and not confirmed:
        # The draft's wait is among ``waits``, so there is one line or more; no place word, since install.sh
        # prints the waits above the NEXT and status below it (§16.51 Q74; B13 round 5 F10, F41).
        seen = "the WAITING ON YOU line" if len(waits) == 1 else "the WAITING ON YOU lines"
        return done(f"stop: the operator confirms the baseline questions (see {seen}); session done", 5)
    has_before = any(eval_dir.glob("results-*-before.md"))
    if not pages and not has_before:
        return done(
            "run the 'before' baseline in a session that did not draft the questions (start a new one if "
            f"this one did): follow step 2 (Run) of {_baseline(docs)}, then run `{BIN} sync`",
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
            f"them (start a new one if this one did): follow step 2 (Run) of {_baseline(docs)}, then run "
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
    own line). ``inbox`` is the folder the guides name (the live inbox source on the folder
    ``config.ensure_inbox`` keeps, else every live inbox source); doctor checks each source's folder. Disk
    only; a part that cannot be read shows ``?``."""
    docs = expand(config.docs_repo)

    def part(name: str, fn: Callable[[], object]) -> str:
        try:
            return f"{name} {fn()}"
        except (OSError, AgentSyncError) as exc:
            _log.warning("loop: cannot read %s: %s", name, exc)
            return f"{name} ?"

    def inbox() -> str:
        # The inbox the guides name (publish's root_guide): the live inbox source on the folder
        # config.ensure_inbox keeps, else every live inbox source. doctor checks each source's folder.
        boxes = [s.path for s in config.live_sources() if s.kind is SourceKind.INBOX and s.path is not None]
        if not boxes:
            return "off"
        kept = canonical_source_root(docs.parent / "inbox")
        named = [kept] if kept in boxes else boxes
        return "on" if all(expand(path).is_dir() for path in named) else "missing"

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
