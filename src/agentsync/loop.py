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
   unless sources.toml by now excludes every empty cloud folder that stopped it and no other folder the last
   walk could not list remains unexcluded, also when it stored no empty cloud folder (B13 round 12 N52), or
   no longer sets the sentinel that walk missed (N51); a recording still being read is a note, or a wait
   while no background sync is installed to read it, never this rule);
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

import contextlib
import json
import logging
import os
import re
import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agentsync import curate, gitops, governance, it_request, skill
from agentsync.arm_local import (
    UNKNOWN_MISSING,
    UNKNOWN_MODE,
    UNKNOWN_OTHER,
    UNKNOWN_PRIVACY,
    _unexcluded,
    cloud_provider_root,
    files_below,
    split_empty,
)
from agentsync.config import Config, SourceConfig, canonical_source_root
from agentsync.convert import NO_CONVERTER_PREFIX
from agentsync.convert.recording import RecordingConverter
from agentsync.cycle import (
    _CHECKPOINT_PENDING_META,
    _EMPTY_DIRS_META,
    _PRESENT,
    _REREAD_BUDGET_S,
    _REREAD_META,
    _ROOT_MISSING_STATE_META,
    _ROOT_UNLISTED_META,
    _SCOPE_ROOT_META,
    _SEED_PAGE_NAMES,
    _SENTINEL_MISSING_META,
    _UNLISTED_DIRS_META,
    _UNLISTED_KINDS_META,
    HYDRATION_REFUSED,
    LISTING_HELD,
    NETWORK_POLICY_FAILED,
    RECORDING_PROGRESS_META,
    RECORDING_WAITS,
    _reread_records,
)
from agentsync.errors import AgentSyncError, ConfigError
from agentsync.manifest import Manifest, ManifestBusyError, ManifestNotCurrentError
from agentsync.model import LintFinding, PassKind, RowState, SourceKind, Verdict
from agentsync.ops import launchd
from agentsync.paths import expand
from agentsync.text import plural

_log = logging.getLogger(__name__)

NO_NEXT_HINT_ENV = "AGENTSYNC_NO_NEXT_HINT"
"""Set to 1 (scripts/install.sh exports it) and ``sync`` prints no NEXT / WAITING ON YOU lines, so the
installer's own NEXT: is the only instruction in its output; ``ops.doctor`` words its IT-only fix for it."""

NEXT_PREFIX = "NEXT: "
WAIT_PREFIX = "WAITING ON YOU: "
NOTE_PREFIX = "note: "
BIN = skill.AGENTSYNC_BIN
INSTALL_SH = skill.INSTALL_SH
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
    # per source, how many of the waits name it, so doctor's heartbeat fix says "the line" for one and "each
    # line" for more (B13 round 6 G44); one empty-folder block can print three, and a breaker or download
    # wait about the same source is a line about it too
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


# what a wait says of the alarm line it points at, which shows the first five folders of all the source's
# unknown ones and, only past five, says how to list the rest (B13 round 9 K40: "names the folder(s)" was
# false past five; round 10 L36: "up to five, and how to list any more" promised a pointer the alarm of one
# to five folders does not hold). Kept only past five folders stored by the walk the alarm is from, the
# alarm's own count: at one to five it names every one, so the clause described nothing it shows (B13 round
# 14 Q23, Q41); no wait's own count equals the alarm's, so never chosen by that.
_UP_TO_FIVE = "up to five, and past five how to list the rest"
# what a could-not-be-listed wait says the alarm names: no count and no cause (B13 round 10 L67); a walk
# incomplete only for its missing sentinel has its own wait since B13 round 11 M59, keyed on the sentinel
# the walk stored as missing (round 12 N51), so a walk from before that was stored, with a sentinel, may
# still land here, and its alarm names the sentinel, no folder; that wait offers the sentinel's fix beside
# the folders' (round 13 O53). Only "or a missing sentinel" in its cause follows the stored miss (round 13
# O49)
_WHAT_FAILED = f"what could not be listed, {_UP_TO_FIVE}"


def _files(n: int) -> str:
    """``n`` files, the noun by the number (B13 round 14 Q42, Q81: "1 file(s)" beside known counts)."""
    return plural(n, "file")


def _alarm_names(what: str, stored: int) -> str:
    """What an alarm of ``stored`` folders of one kind names: ``what`` alone at one to five, which it lists
    in full; past five, five and the pointer at the rest (:data:`_UP_TO_FIVE`; B13 round 14 Q23, Q41)."""
    return what if stored <= 5 else f"{what}, {_UP_TO_FIVE}"


def _named(
    config: Config, ids: Sequence[str], what: str, *, in_state: bool = False, stopped: str | None = None
) -> str:
    """Where a wait's folders are named, read-only (B13 round 6 G13, G42): the docs repo's STATE.md keeps the
    ``alarm:`` line a sync prints under each source, and every cycle, the background one too, writes it there
    (a run that does not select the source keeps that source's last one, tagged with its run: round 7 H73).
    STATE.md comes first, since it holds the line wherever the wait prints: install.sh's closing step and
    ``status`` print no alarm line, only a plain sync does (B13 round 7 H35). The alarm names five and itself
    points at ``sync -v`` for the rest, so the wait never does (round 7 H12); a wait whose walk stored more
    than five folders of a kind says so, ``{what}, up to five, and past five how to list the rest``
    (:data:`_UP_TO_FIVE` via :func:`_alarm_names`; a could-not-be-listed wait, :data:`_WHAT_FAILED` via
    ``failed_names``), and at one to five, which the alarm lists in full, names ``{what}`` alone (B13 round
    14 Q23, Q41; round 15 S45: this said "true for any count" after those). The count is the walk's stored
    count, since the alarm caps each kind of unknown folder on its own, the folders it could not read and the
    empty cloud folders, never the wait's own count (B13 round 9 K40;
    round 10 L36, L67: the pointer is the alarm's only past five, and the could-not-be-listed waits said
    "names it" of six folders; round 11 M5, M45: one cap over both kinds hid an unreadable folder behind five
    empty ones). Two
    or more ids have one alarm line each, joined with "and" (round 7 H50). The wait itself keeps a count and
    never a path (B13 round 5 F43). ``in_state``: the wait is written into STATE.md itself, which names no
    file it is already in: the
    alarm line is under the source's own ``## <id>`` section below (B13 round 8 J33). ``stopped``: the check
    whose [FAIL] the sync's own guard stops on (:func:`_sync_stopped_on`), so no plain sync prints the alarm
    until it is fixed (B13 round 31 C520); the ``in_state`` forms never get it, since only a sync that runs
    writes STATE.md."""
    if in_state:
        if len(ids) == 1:
            return f"the alarm line under ## {ids[0]} below names {what} (a plain `{BIN} sync` prints it too)"
        sections = f"{', '.join(f'## {i}' for i in ids[:-1])} and ## {ids[-1]}"
        return f"the alarm lines under {sections} below name {what} (a plain `{BIN} sync` prints them too)"
    state = _shown(expand(config.docs_repo) / _STATE_MD)
    it = "it" if len(ids) == 1 else "them"
    prints = f"a plain `{BIN} sync` prints {it} too"
    if stopped is not None:
        prints = f"once the {stopped} [FAIL] line is fixed, {prints}; until then no sync runs"
    if len(ids) == 1:
        return f"{state} keeps the alarm line for {ids[0]}, which names {what} ({prints})"
    listed = f"{', '.join(ids[:-1])} and {ids[-1]}"
    return f"{state} keeps the alarm lines for {listed}, which name {what} ({prints})"


def _named_since(
    config: Config, sid: str, left: int, *, in_state: bool = False, stopped: str | None = None
) -> str:
    """Where a wait's empty cloud folders are named when some the alarm lists were excluded after the walk it
    is from (B13 round 15 S32): that alarm still names them, up to five, and past five may hide the
    ``left`` ones behind its "+N more", so the pointer says it predates the exclude and that a plain sync
    prints it again with those left. It replaces :func:`_named`'s whole pointer, its "(a plain sync prints it
    too)" as well, so sync is said once. ``in_state`` and ``stopped``: as :func:`_named`."""
    again = f"a plain `{BIN} sync` prints it again with the {plural(left, 'folder')} left"
    if stopped is not None and not in_state:
        again = f"once the {stopped} [FAIL] line is fixed, {again}"
    if in_state:
        return (
            f"the alarm line under ## {sid} below is from that source's last sync, before the exclude, so it "
            f"may name folders now excluded; {again}"
        )
    state = _shown(expand(config.docs_repo) / _STATE_MD)
    return (
        f"{state} keeps the alarm line for {sid} from its last sync, before the exclude, so it may name "
        f"folders now excluded; {again}"
    )


def _sync_stopped_on(config: Config) -> str | None:
    """The status check whose [FAIL] the sync's own guard stops on before it runs, by that guard itself
    (``doctor.state_not_a_file`` with the sync's flags, as ``cli`` calls it: a governance file or governance/
    not a file, damaged, or a list that lost a purge, the lock, heartbeat.json), or None: never a hold the
    hold list lost, whose sync runs (B13 round 31 C520; ruling 60). Also the purge record or the
    suppression list this account cannot open, which the sync stops on in its refused kind past that guard:
    governance.suppressed, the check that FAILs either, never holds.json, hold-record.json or audit.jsonl,
    which a sync runs over at 0000 (B13 round 32 C639; ruling 65)."""
    from agentsync.ops import doctor  # noqa: PLC0415 - doctor imports this module

    found = doctor.state_not_a_file(config, manifest=False, damaged=True, lost=True)  # never raises
    if found is not None:
        return found[1].name
    root = config.state_paths.root
    for read in (governance.recorded_purges, governance.load_suppressions):
        try:
            read(root)
        except governance.GovernanceError as exc:
            cause = exc.__cause__
            if type(exc) is governance.GovernanceError and isinstance(cause, PermissionError):
                return "governance.suppressed"
        except Exception:  # every other error is its own line's, as before
            continue
    return None


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
    # the noun, verb and pronoun by the count (B13 round 15 S65: "1 recording(s) ... they do not block")
    if files.recording:
        n = sum(files.recording.values())
        one = n == 1
        counted = f"{plural(n, 'recording')} in {_ids(files.recording)}"
        block = "it does not block the next step" if one else "they do not block the next step"
        if background:
            notes.append(
                f"{counted} {'is' if one else 'are'} still being read{_minutes(files)}; each background sync "
                f"reads more; {block}"
            )
        else:
            waits.append(
                f"{counted} {'waits' if one else 'wait'} to be read{_minutes(files)}, and no background sync "
                f"is installed to read {'it' if one else 'them'} (an interactive sync reads no recording): "
                f"run `{INSTALL_SH} --confirm-install-agent`, or `{BIN} materialise <file>` for one "
                f"recording; {block}"
            )
    if files.recording_refused:
        n = sum(files.recording_refused.values())
        one = n == 1
        notes.append(
            f"{plural(n, 'online-only recording')} in {_ids(files.recording_refused)} "
            f"({files.recording_refused_bytes / 1e9:.1f} GB) could not be downloaded by agentsync: in Finder "
            f"choose Always Keep on This Device on {'its' if one else 'their'} folder, or Download Now on a "
            f"file; the next background sync reads {'it; it does' if one else 'them; they do'} not block the "
            "next step"
        )
    return waits, notes


# Each note's words for many files (or a count not known) and for one (B13 round 15 S65: "1 file(s) in work
# are", "none of them")
_REREAD_AGAIN = (
    "are still to be read again, once, for what this build's converters have gained (each sync reads about "
    f"{_REREAD_BUDGET_S / 60:g} minutes' worth)"
)
_REREAD_AGAIN_ONE = _REREAD_AGAIN.replace("are still", "is still", 1)
_REREAD_STUCK = (
    "wait to be read again, and the last sync read none of them (a converter or on-device OCR that cannot "
    "run, or a folder that could not be listed): another sync does not clear it"
)
_REREAD_STUCK_ONE = _REREAD_STUCK.replace(
    "wait to be read again, and the last sync read none of them",
    "waits to be read again, and the last sync did not read it",
    1,
)
_REREAD_CROWDED = (
    "wait to be read again, and the last sync read none of them: new and changed files took its OCR time, "
    "more files joined, and more downloads wait. They are read once a sync has OCR time left"
)
_REREAD_CROWDED_ONE = _REREAD_CROWDED.replace(
    "wait to be read again, and the last sync read none of them",
    "waits to be read again, and the last sync did not read it",
    1,
).replace("They are read once", "It is read once", 1)
_REREAD_ONE = {
    _REREAD_AGAIN: _REREAD_AGAIN_ONE,
    _REREAD_STUCK: _REREAD_STUCK_ONE,
    _REREAD_CROWDED: _REREAD_CROWDED_ONE,
}


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
    - the run read none and has files whose converter could not start (``reread_waits``), which it leaves
      out of ``reread_left``, whether or not its OCR time ran out (B13 round 18 V57);
    - the run read none, its OCR time was used up, it left more than the run before, and ``downloads``:
      new files have the OCR time first, the ones converted past it join the files to read again, and the
      next sync downloads more.  Used-up OCR time is no progress then."""

    def said(ids: Sequence[str], count: int, words: str) -> str:
        # 0: no count is known (a held source, a run that left none), and "file(s)" stays
        counted = f"{_files(count)}" if count else "file(s)"
        words = _REREAD_ONE[words] if count == 1 else words
        return f"{counted} in {', '.join(ids)} {words}; it does not block the next step"

    notes: list[str] = []
    looked = sorted(set(sources) - set(unread))
    if looked:
        new = runs[0] if runs else None
        left = (new or {}).get("reread_left", 0)
        grew = len(runs) > 1 and left > runs[1].get("reread_left", 0)
        if new is not None and new.get("ocr_down"):
            notes.append(said(looked, left, _REREAD_STUCK))
        elif new is not None and new.get("reread_waits") and not new.get("reread"):
            # files whose converter could not start: no sync reads them while it cannot (B13 round 18 V57)
            notes.append(said(looked, left + new["reread_waits"], _REREAD_STUCK))
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

    # how many zero-child cloud folders the last walk stored: the alarm lists them all, excluded since or not
    # (B13 round 14 Q58: the words followed ``below``, so an exclude-empty before the next sync changed them)
    stored: int
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
    folders = _unexcluded(src, names)
    rows = manifest.iter_items(src.id, states=_PRESENT) if folders else ()  # rows read only for a folder
    below, holding = files_below(folders, (row.rel_path for row in rows if not row.is_dir))
    return _EmptyDirs(len(names), below, holding)


def _excludable(empty_dirs: _EmptyDirs) -> tuple[list[str], list[str]]:
    """Of ``below`` (:func:`_empty_dirs`), the folders with no mirrored file below them, split in two: those
    ``agentsync exclude-empty`` excludes, and those whose exclude glob would also prune a folder holding
    mirrored files (a ``[`` is written as ``?``: ``arm_local._glob_literal``), which it leaves, since
    excluding one would retire those pages at once. One rule for the command and the wait naming it (B13
    round 5 F65), so the wait never names a command that refuses its folder; the walk's alarm counts the
    waits by the same rule (``arm_local.split_empty``, B13 round 10 L15)."""
    clear, collides, _gone = split_empty(empty_dirs.below, empty_dirs.holding)
    return clear, collides


def empty_cloud_folders_held(manifest: Manifest, src: SourceConfig) -> bool:
    """Whether ``src``'s listing stays incomplete because of empty cloud folders its last walk stored and
    today's exclude does not prune: the case :func:`next_step` gives WAITING ON YOU lines that say another
    sync does not clear it (a local or inbox source, its row incomplete, its newest pass FULL and not held for
    a privacy prompt, and :func:`_empty_dirs` names some). Stored folders that are all excluded now are not
    this case: the next sync lists the source, unless a folder it could not read beside them is not excluded
    (:func:`_unlisted_dirs`; then the could-not-be-listed wait stays, B13 round 11 M58). Doctor's heartbeat
    check words its fix by it (B13 round 3 R53). Reads stored state only, never a folder."""
    if src.kind not in (SourceKind.LOCAL, SourceKind.INBOX):
        return False
    row = manifest.get_source(src.id)
    last = manifest.last_source_pass(src.id)
    if (row is not None and row.enumeration_complete) or last is None or last.pass_kind is not PassKind.FULL:
        return False
    if (last.skipped_reason or "").startswith(LISTING_HELD):
        return False
    return bool(_empty_dirs(manifest, src).below)


def _unlisted_stored(manifest: Manifest, src: SourceConfig) -> list[str]:
    """Every folder the source's last walk could not list for a cause other than an empty cloud folder
    (manifest meta ``unlisted_dirs:<id>``), excluded since or not; empty when that walk stored none, or the
    state is from before B13 round 10 L68."""
    try:
        stored = json.loads(manifest.get_meta(_UNLISTED_DIRS_META + src.id) or "[]")
    except ValueError:
        stored = []
    return [d for d in stored if isinstance(d, str)] if isinstance(stored, list) else []


def _unlisted_dirs(manifest: Manifest, src: SourceConfig) -> list[str]:
    """The folders of :func:`_unlisted_stored` that sources.toml does not exclude today."""
    return _unexcluded(src, _unlisted_stored(manifest, src))


def _unlisted_kinds(manifest: Manifest, src: SourceConfig, names: Sequence[str]) -> frozenset[str] | None:
    """The kinds (``arm_local.UNKNOWN_*``) of ``names``, folders the source's last walk could not list ("."
    for its root), from manifest meta ``unlisted_kinds:<id>``; None when a name has none (state from before
    B13 round 11 M42, or an older walk), so the wait keeps its generic words."""
    try:
        stored = json.loads(manifest.get_meta(_UNLISTED_KINDS_META + src.id) or "{}")
    except ValueError:
        return None
    if not isinstance(stored, dict) or not names or not all(isinstance(stored.get(n), str) for n in names):
        return None
    return frozenset(str(stored[n]) for n in names)


def _missing_root_wait(config: Config, sid: str, pause: str, *, state: str = "") -> str:
    """The wait for a source whose last walk found no root folder (``UNKNOWN_MISSING``; B13 round 14 Q6, Q11,
    Q34): it was renamed, moved or deleted, or what holds it is not there. Status reads no folder for it (a
    hidden Allow prompt can hold one, and status reads this: B13 round 13 O47): under ~/Library/CloudStorage
    the cause follows ``state``, what the provider folder said of its app's sign-in at that walk
    (``cycle._ROOT_MISSING_STATE_META``, doctor's ``provider_state``, so the wait and doctor's fix agree: B13
    round 15 S3): signed in, the folder was renamed, moved or deleted there or is not synced; another
    account's folder there, the one it belongs to is signed out; a SharePoint library not synced on this Mac;
    signed out, the sign-in first, and with the app's own folder gone (one lstat) no put-back, which cannot
    be done there (B13 round 16 T19); else the app is signed out or still setting up, or the folder was
    renamed. With no state stored (a walk from before S3), an app folder that is gone reads as signed out,
    as ``provider_state`` says of it; otherwise it is worded
    from the configured path and one lstat of the organization's ``OneDrive-<org>`` folder, as
    ``provider_state`` does, never a listing: a library folder is a library not synced only beside it, else
    OneDrive is signed out (B13 round 15 S41). Under /Volumes the volume is not mounted. No Files and Folders
    grant, which cannot bring back a folder that does not exist; no path, which the sync's alarm names."""
    src = next((s for s in config.sources if s.id == sid), None)
    fix_path = f'fix path in [[source]] id = "{sid}" in {_shown(config.config_path)}'
    root = expand(src.path) if src is not None and src.path is not None else None
    provider = cloud_provider_root(root) if root is not None else None
    if provider is not None and not state and provider.name.startswith("OneDrive-SharedLibraries-"):
        org = it_request.org_of(provider.name)
        state = "library" if org is not None and os.path.lexists(provider.parent / f"OneDrive-{org}") else ""
    # One lstat of the provider folder: gone, the app is signed out or the folder was never there, so the
    # put-back cannot be the fix (B13 round 16 T19)
    gone = provider is not None and not os.path.lexists(provider)
    if gone and not state:
        state = "signed-out"
    app = (provider.name.split("-", 1)[0] or "its sync app") if provider is not None else ""
    if provider is not None and state == "library":
        cause = "renamed, moved or deleted, or its SharePoint library is not synced on this Mac"
        fix = (
            "sync the SharePoint library to this Mac with Sync on its SharePoint page, put the folder back "
            f"under that name, or {fix_path}"
        )
    elif provider is not None and state == "signed-in":
        cause = f"renamed, moved or deleted in {app}, or not synced on this Mac"
        fix = f"put it back under that name, sync that folder in {app}, or {fix_path}"
    elif provider is not None and state == "other-account":
        cause = (
            f"renamed, moved or deleted, or the {app} account it belongs to is not signed in on this Mac, "
            f"while another {app} folder is there"
        )
        fix = f"sign in to {app} with the account it belongs to, put it back under that name, or {fix_path}"
    elif provider is not None and state == "signed-out" and gone:
        cause = (
            f"{app} is not signed in on this Mac, since its own folder is gone too, or it was renamed, moved "
            "or deleted"
        )
        fix = f"sign in to {app} or let it finish setting up, or {fix_path}"
    elif provider is not None and state == "signed-out":
        cause = (
            f"{app} is not signed in on this Mac or is still setting up, or it was renamed, moved or deleted "
            f"in {app}"
        )
        fix = f"sign in to {app} or let it finish setting up, put it back under that name, or {fix_path}"
    elif provider is not None:
        cause = f"renamed, moved or deleted in {app}, or {app} is not signed in on this Mac"
        fix = f"put it back under that name, sign in to {app} or let it finish setting up, or {fix_path}"
    elif root is not None and root.parts[:2] == ("/", "Volumes"):
        cause = "its volume is not mounted, or it was renamed, moved or deleted"
        fix = f"mount the volume, or {fix_path}"
    else:
        cause = "renamed, moved or deleted"
        fix = f"put it back under that name, or {fix_path}"
    return (
        f"the folder of {sid} does not exist ({cause}; nothing is deleted, and another sync does not clear "
        f"it): {fix}; to stop syncing it on purpose, {pause}"
    )


def _access_fix(kinds: frozenset[str] | None, *, one: bool = False) -> str:
    """The fix a could-not-be-listed wait names for folders of ``kinds`` (:func:`_unlisted_kinds`), before its
    exclude clause (B13 round 11 M42): TCC refuses with EPERM, which a Files and Folders grant clears; a
    folder's own permissions refuse with EACCES, which no grant overrides, so the owner and mode are checked
    (never a chmod: the folder may belong to another account); a provider error or a folder that vanished is
    the sync app's, as the walk's alarm says. Several kinds name each fix; unknown kinds keep the grant.
    ``one``: the wait counts one folder, so the fix says "it", never "each one" (B13 round 13 O35)."""
    if kinds is None or not kinds:
        return "grant Files and Folders access"
    several = len(kinds) > 1
    parts: list[str] = []
    if UNKNOWN_PRIVACY in kinds:
        parts.append("grant Files and Folders access" + (" for those refused with EPERM" if several else ""))
    if UNKNOWN_MODE in kinds:
        parts.append(
            "check the owner and mode (`ls -ld`) of those refused with EACCES"
            if several
            else "check its owner and mode with `ls -ld`, since its own permissions keep this account out "
            "and no Files and Folders grant overrides them"
            if one
            else "check the owner and mode of each one with `ls -ld`, since a folder's own permissions keep "
            "this account out and no Files and Folders grant overrides them"
        )
    if UNKNOWN_OTHER in kinds:
        parts.append(
            "check that the others open in Finder and that their sync app is running"
            if several
            else f"check that {'it' if one else 'each one'} opens in Finder and that its sync app is running"
        )
    if not parts:  # a kind this build does not know
        return "grant Files and Folders access"
    return ", and ".join(parts)


# the cause a could-not-be-listed wait names when the walk stored no kind it knows (B13 round 12 N12, N38)
_ANY_CAUSE = "no access, or a missing folder or sentinel"


def _unlisted_cause(kinds: frozenset[str] | None, sentinel: str | None) -> str:
    """The cause a could-not-be-listed wait names, before "; another sync does not clear it", for folders of
    ``kinds`` (:func:`_unlisted_kinds`) in a source whose last walk stored ``sentinel`` as missing (B13 round
    12 N12, N38): it said "no access, or a missing folder or sentinel" beside an EACCES fix, on a source with
    no sentinel. The refusal by its errno (the fix beside it says what that means), a provider error or a
    folder that vanished as the walk's alarm says; "or a missing sentinel" only when the last walk stored
    today's sentinel as missing (manifest meta ``sentinel_missing:<id>``), since a walk that also missed it
    lands here, not in the sentinel wait (B13 round 13 O49: keyed on the config alone, a sentinel the walk
    found was named as a cause). A sentinel found, or changed or removed since, adds nothing; state from
    before B13 round 12 N51 stored no miss, so it lacks the clause until the next sync. No kind, or one this
    build does not know: every cause."""
    if not kinds or not kinds <= {UNKNOWN_PRIVACY, UNKNOWN_MODE, UNKNOWN_OTHER}:
        return _ANY_CAUSE
    refused = [code for kind, code in ((UNKNOWN_PRIVACY, "EPERM"), (UNKNOWN_MODE, "EACCES")) if kind in kinds]
    parts = [f"refused with {' or '.join(refused)}"] if refused else []
    if UNKNOWN_OTHER in kinds:
        parts.append("a sync-app error or a folder that vanished")
    return ", or ".join(parts) + (", or a missing sentinel" if sentinel else "")


def _mirrored_below(manifest: Manifest, src: SourceConfig, folders: Sequence[str]) -> bool:
    """Whether the mirror holds a file at or below any of ``folders``, folders the source's last walk could
    not list (B13 round 11 M57): then excluding one retires pages at once, and its wait keeps the safe words.
    True when ``folders`` is empty: no folder is known, so no plain exclude is offered (B13 round 12 N54)."""
    if not folders:
        return True
    rows = manifest.iter_items(src.id, states=_PRESENT)
    return any(files_below(folders, (r.rel_path for r in rows if not r.is_dir))[0].values())


def _missed(manifest: Manifest, source_id: str) -> int:
    """The mirrored files the source's last walk did not list, of those last listed under the root it has
    now (manifest meta ``scope_root:<id>``, ``<run>:<path>``): a complete pass retires a row last listed under
    an earlier root as a scope change instead of checking it (``cycle._Cycle._root_since``)."""
    return manifest.unlisted_count(source_id, since=_root_since(manifest, source_id))


def _root_since(manifest: Manifest, source_id: str) -> int:
    """The run the source's current root was first walked in (manifest meta ``scope_root:<id>``), else 0."""
    since = (manifest.get_meta(_SCOPE_ROOT_META + source_id) or "").partition(":")[0]
    return int(since) if since.isdigit() else 0


def _missed_below(manifest: Manifest, source_id: str, gone: Sequence[str]) -> tuple[int, int]:
    """Of the files :func:`_missed` counts, how many lie under the gone empty folders ``gone``, and how many
    of those folders hold one: the "in all" clause's words follow them, not the missed count, which a row
    last listed under an earlier root leaves out and a folder the walk listed part of holds (B13 round 15
    S44)."""
    paths = manifest.unlisted_paths(source_id, since=_root_since(manifest, source_id))
    below, _holding = files_below(gone, paths)
    return sum(below.values()), sum(1 for n in below.values() if n)


def _under_gone(missed: int, k: int, holders: int) -> str:
    """Where the ``k`` of ``missed`` files under ``holders`` gone empty folders sit, for the "in all" clause
    (B13 round 15 S44): by ``k``, the files that are there, never by ``missed`` alone, which put "the one
    under the empty folder above" on a file under the folder that could not be listed."""
    folders = "the empty folder above" if holders == 1 else "the empty folders above"
    if k == missed:
        return f"the one under {folders}" if k == 1 else f"all under {folders}"
    return f"{'the one' if k == 1 else 'the ones'} under {folders} among them"


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


def next_step(
    config: Config, *, fixes: Sequence[str] = (), count_queue: bool = True, in_state: bool = False
) -> NextStep:
    """The first unmet rule of the loop (module docstring) and the operator's waits, from disk only (no
    source folder is listed: the empty cloud folders it names are the ones the last sync's walk stored).

    ``fixes`` are the fixes of status FAILs the caller already ran (rule 1, after the disk checks here).
    ``count_queue=False`` (STATE.md, written by every cycle) skips rule 9's count, which reads every uncited
    mirror page: past rule 8 the step sends the agent to ``curate``, which counts it. ``in_state=True``
    (STATE.md too): a wait points at the alarm line under its source's section, not at STATE.md
    (:func:`_named`)."""
    docs = expand(config.docs_repo)
    waits: list[str] = []
    notes: list[str] = []
    checks: list[str] = []  # doctor's check for each wait that explains one (NextStep.checks)
    about: dict[str, int] = {}  # ... and per source, the waits that name it (wait_counts)

    def done(step: str, rule: int) -> NextStep:
        return NextStep(step, tuple(waits), tuple(notes), rule, tuple(checks), dict(about))

    # a sync its own guard stops prints no alarm, so the waits' "a plain sync prints it" waits on that FAIL;
    # STATE.md's forms never say it, since only a sync that runs writes them (B13 round 31 C520)
    stopped = None if in_state else _sync_stopped_on(config)

    def named(ids: Sequence[str], what: str) -> str:
        return _named(config, ids, what, in_state=in_state, stopped=stopped)

    def named_since(sid: str, left: int) -> str:
        return _named_since(config, sid, left, in_state=in_state, stopped=stopped)

    def names_source(*sources: str) -> None:
        """The wait just appended names each of these sources."""
        for sid in sources:
            about[sid] = about.get(sid, 0) + 1

    def cannot_list(manifest: Manifest, src: SourceConfig, other: list[str], missed: int) -> None:
        """``src`` gets the could-not-be-listed wait for ``other``, the folders its last walk could not list:
        its fix and cause by their kinds, and ``missed`` with the safe words (B13 round 12 N12, N38); and how
        many there are, and how many that walk stored, when ``other`` names them (B13 round 13 O16, O35)."""
        unlisted.append((src.id, missed))
        kinds = _unlisted_kinds(manifest, src, other)
        all_kinds[src.id] = kinds
        access_fixes[src.id] = _access_fix(kinds)
        missed_sentinel = manifest.get_meta(_SENTINEL_MISSING_META + src.id)
        causes[src.id] = _unlisted_cause(
            kinds, src.sentinel if src.sentinel and missed_sentinel == src.sentinel else None
        )
        if other:
            counts[src.id] = (len(other), len(_unlisted_stored(manifest, src)))

    def cannot_list_any(manifest: Manifest, src: SourceConfig, other: list[str]) -> None:
        """As :func:`cannot_list` with the files the last walk missed, where no empty cloud folder takes that
        count: folders holding no mirrored file get the plain exclude, as the walk's alarm offers it, and the
        count a clause of its own, with no exclude warning, since excluding them retires nothing (B13 round 12
        N54: the wait warned against the exclude the alarm offered)."""
        missed = _missed(manifest, src.id)
        if other and not _mirrored_below(manifest, src, other):
            cannot_list(manifest, src, other, 0)
            if missed:
                held_elsewhere[src.id] = missed
        else:
            cannot_list(manifest, src, other, missed)

    # a kept upstream deletion is no wait: no run clears it while its file is listed; once the file is gone it
    # is a queued purge again, and the wait returns. A kept label escalation is no wait either: its file is
    # published again, and running it would erase that page (B13 round 6 G30)
    try:
        split = governance.partition_queue(config.state_paths, config=config)
    except (
        governance.GovernanceUnreadError
    ):  # a queue, or governance/, that cannot be read: no queue, and its
        # FAIL comes first; the listing waits below are still worked out (B13 round 29 C152)
        split = governance.QueueSplit([], [])
    queued, kept = split.runnable, split.kept
    hold_state = governance.hold_list_state(config.state_paths.root)
    if queued and hold_state is not None:
        queued = []  # an unread hold list is no empty one: its FAIL comes first (B13 round 27 P131)
    if hold_state == "lost":
        # a hold the hold list lost is in force: the person's, named by status's FAIL, with no owner (the
        # report quotes this line) and no purge invited (B13 round 29 C107; ruling 27)
        waits.append(governance.lost_hold_wait(config.state_paths.root))  # the folder's form too (C613)
        checks.append("governance.hold")
    on_hold: list[tuple[governance.QueuedPurge, list[governance.Hold]]] = []
    if queued:
        # a hold suspends its entries: a note, the records owner's move, never a run that refuses (B13 round
        # 28 I91); a [governance] that does not load keeps the wait, its governance.config FAIL speaks
        with contextlib.suppress(Exception):
            queued, on_hold = governance.held_purges(
                config.state_paths.root, governance.load_governance(config.config_path), queued
            )
    if on_hold:
        notes.append(governance.held_words(on_hold))
    if queued:
        exits = "; it exits 1 with the held ones still queued" if on_hold else ""
        waits.append(
            f"{plural(len(queued), 'queued purge')}: preview with `{BIN} purge --queue --dry-run` (writes "
            f"nothing{governance.looked_up_words(split.not_looked_up, queued)}) before you run "
            f"`{BIN} purge --queue` (rewrites history){exits}"
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
    # ... the fix for each such source's folders, by their kinds (:func:`_access_fix`), and the cause
    # (:func:`_unlisted_cause`)
    access_fixes: dict[str, str] = {}
    causes: dict[str, str] = {}
    all_kinds: dict[str, frozenset[str] | None] = {}  # ... the kinds the fixes follow
    # ... how many folders are not excluded today, and how many the walk stored, when it stored them
    counts: dict[str, tuple[int, int]] = {}
    # ... whose walk stored nothing that names the cause, while sources.toml sets a sentinel (B13 round 13
    # O53)
    sentinel_too: set[str] = set()
    # ... whose folders hold no mirrored file, with the files its last walk missed elsewhere (B13 round 12
    # N54)
    held_elsewhere: dict[str, int] = {}
    root_kinds: dict[str, frozenset[str] | None] = {}  # the kind of each root it could not list
    root_states: dict[str, str] = {}  # ... and, for a missing one, its provider's state at the walk (S3)
    # ... whose missed count also holds files under gone empty folders (B13 round 11 M57), with how many of
    # them do and how many of those folders hold one, which the words follow (B13 round 14 Q57, round 15 S44)
    in_all: dict[str, tuple[int, int]] = {}
    # ... and, for the gone line of a source with no such folder, the same split of its missed count, so a
    # count that is all the gone folders' own files is not said twice (B13 round 16 T39)
    gone_missed: dict[str, tuple[int, int]] = {}
    sentinel_missing: list[
        str
    ] = []  # a local walk incomplete only for its missing sentinel (B13 round 11 M59)
    root_unlisted: list[str] = []  # ... at the root folder itself, which no exclude can clear
    # ... because of these empty cloud folders: (source, files below each, the clear ones, the colliding ones,
    # mirrored files the last walk missed)
    empty: list[tuple[SourceConfig, dict[str, int], list[str], list[str], int, int]] = []
    inbox_partial: list[str] = []  # an inbox listing ran but could not finish (often a file being written)
    blocked: list[str] = []  # a Graph source the network policy fails: IT's to fix
    held: list[str] = []  # a local walk timed out on a read macOS holds for an Allow prompt (field N8)
    signin: list[str] = []
    rereading: list[str] = []  # the one-time re-read of files from before a capability is not finished
    unread: list[str] = []  # ... and the source's newest pass was skipped or failed: no sync got to them
    reread_runs: list[dict[str, int]] = []
    pending: str | None = None
    unread_state: ManifestNotCurrentError | ManifestBusyError | None = None
    opened: Manifest | None = None
    if db.exists():
        try:
            opened = Manifest(db)
        except (ManifestNotCurrentError, ManifestBusyError) as exc:
            # only under manifest.read_only_reads (the setup report), which never migrates (B13 round 31 C560)
            unread_state = exc
    if opened is not None:
        with opened as manifest:
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
                            missed = _missed(manifest, src.id)
                            # A folder it could not read beside them gets its own wait now, not only after
                            # exclude-empty and another sync (B13 round 10 L68). Its wording follows the
                            # folders themselves (B13 round 11 M57): one holding mirrored files keeps the safe
                            # words and takes the missed count, said once, "in all" beside a gone folder (the
                            # count also holds files under the empty folders and deletions held elsewhere, so
                            # it is never split); one holding none gets the plain exclude, and the count stays
                            # on the empty folders' line, as with no such folder.
                            other = _unlisted_dirs(manifest, src)
                            also = bool(other) and src.kind is SourceKind.LOCAL
                            held_below = also and _mirrored_below(manifest, src, other)
                            empty.append((src, below, clear, collides, 0 if held_below else missed, stored))
                            if missed and not held_below and any(below.values()):
                                gone_missed[src.id] = _missed_below(
                                    manifest, src.id, [d for d, n in below.items() if n]
                                )
                            if also:
                                cannot_list(manifest, src, other, missed if held_below else 0)
                                if held_below and any(below.values()):
                                    in_all[src.id] = _missed_below(
                                        manifest, src.id, [d for d, n in below.items() if n]
                                    )
                            elif other:
                                inbox_partial.append(src.id)
                        elif manifest.get_meta(_ROOT_UNLISTED_META + src.id) and _unlisted_kinds(
                            manifest, src, ["."]
                        ) == frozenset({UNKNOWN_MISSING}):
                            # a root that does not exist, an inbox's too: no later sync lists it (B13 round
                            # 14 Q34; an inbox's "a later sync lists it" note promised one)
                            root_unlisted.append(src.id)
                            root_kinds[src.id] = frozenset({UNKNOWN_MISSING})
                            root_states[src.id] = manifest.get_meta(_ROOT_MISSING_STATE_META + src.id) or ""
                        elif src.kind is SourceKind.INBOX:
                            inbox_partial.append(src.id)
                        elif stored and (other := _unlisted_dirs(manifest, src)):
                            # Every empty cloud folder is excluded since that walk, but a folder it could not
                            # read beside them is not: another sync does not complete the listing (B13 round
                            # 11 M58), so its wait stays.
                            cannot_list_any(manifest, src, other)
                        elif stored:
                            incomplete.append(src.id)  # all excluded since that walk: the next sync lists it
                        elif manifest.get_meta(_ROOT_UNLISTED_META + src.id):
                            root_unlisted.append(src.id)
                            root_kinds[src.id] = _unlisted_kinds(manifest, src, ["."])
                        elif other := _unlisted_dirs(manifest, src):
                            cannot_list_any(manifest, src, other)
                        elif (missed_sentinel := manifest.get_meta(_SENTINEL_MISSING_META + src.id)) and not (
                            last is not None and last.error
                        ):
                            # No empty folder, no unlisted root and no other folder left unexcluded: the walk
                            # stored the sentinel it missed, which no grant or exclude clears (B13 round 11
                            # M59), while sources.toml still sets it; removed or changed since, the next sync
                            # looks again (round 12 N51: keyed on today's config, a removed sentinel turned
                            # this into a could-not-be-listed wait no sync kept). A pass that failed after its
                            # walk stored nothing new, so it keeps the folder wait.
                            if src.sentinel == missed_sentinel:
                                sentinel_missing.append(src.id)
                            else:
                                incomplete.append(src.id)
                        elif manifest.get_meta(_UNLISTED_DIRS_META + src.id):
                            # every folder that walk could not list is excluded since: the next sync lists
                            # it, as with the empty folders above (B13 round 12 N52)
                            incomplete.append(src.id)
                        else:
                            # nothing stored that names the cause: state from before B13 round 10 L68 or
                            # round 12 N51, or a pass that failed after its walk. With a sentinel set the
                            # cause may be the sentinel, so its fix joins the folders' (B13 round 13 O53),
                            # which the wording alone carries: no absent meta tells "found" from "never
                            # stored", so the cause is never keyed on one
                            cannot_list_any(manifest, src, [])
                            if src.sentinel:
                                sentinel_too.add(src.id)
                    else:
                        incomplete.append(src.id)
                if row is not None and row.breaker_tripped_at is not None:
                    later = "" if row.enumeration_complete else f" once a sync lists {src.id} in full"
                    tripped = row.breaker_candidates or 0
                    waits.append(
                        f"the deletion breaker tripped on {src.id} ({_files(tripped)} gone): if "
                        f"{'it really was' if tripped == 1 else 'they really were'} deleted, run "
                        f"`{BIN} accept-deletions {src.id}`{later}"
                    )
                    names_source(src.id)
                if src.kind.is_graph and row is not None and row.auth_state != "ok":
                    signin.append(src.id)
            files = _unpublished(manifest, live)
            pending = manifest.get_meta(_CHECKPOINT_PENDING_META) or None
    elif unread_state is None:
        incomplete = [s.id for s in live]
    if files.over:
        one = sum(files.over.values()) == 1
        waits.append(
            f"{plural(sum(files.over.values()), 'online-only file')} in {_ids(files.over)} "
            f"{'is' if one else 'are'} larger than the per-run download budget: run `{BIN} materialise "
            f"--budget BYTES` with BYTES above {'its' if one else 'their'} size, or raise that source's "
            "max_materialise_bytes"
        )
        names_source(*files.over)
    if files.refused:
        one = sum(files.refused.values()) == 1
        waits.append(
            f"{plural(sum(files.refused.values()), 'online-only file')} in {_ids(files.refused)} could not "
            "be downloaded (macOS refused): in Finder, choose Download Now (or Always Keep on This Device) "
            f"on {'its' if one else 'their'} folder, then run `{BIN} sync`"
        )
        names_source(*files.refused)
    if held:
        waits.append(
            f"macOS held the listing of {', '.join(sorted(held))} for a privacy prompt: click Allow on the "
            f"macOS prompt (it can sit behind other windows), then run `{BIN} sync`"
        )
        checks.extend(f"heartbeat.{s}" for s in sorted(held))
        names_source(*held)

    def gone_tail(sid: str, missed: int, size: str) -> str:
        """The gone line's missed count (B13 round 16 T39): nothing when every missed file lies under the
        gone folders, whose own words already say later syncs take those pages out; else "in all", with
        how many of them lie there, as the could-not-be-listed line says it (:func:`_under_gone`); with
        none there, the count alone."""
        if not size:
            return ""
        k, holders = gone_missed.get(sid, (0, 0))
        if k == missed:
            return ""
        if not k:
            return f"; {size}"
        return (
            f"; in all, the last walk of {sid} did not list {_files(missed)} the mirror still has, "
            + _under_gone(missed, k, holders)
            + ": once the listing is complete, later syncs take out the pages of those still absent, with "
            "the usual deletion check"
        )

    for src, below, clear, collides, missed, n_stored in empty:
        checks.append(f"heartbeat.{src.id}")  # below is never empty: one wait or more follow
        # An empty cloud folder is unknown, never empty, on every pass. The line naming exclude-empty counts
        # only the folders that command excludes (:func:`_excludable`, one rule for both): one that held
        # mirrored files gets no such line, since excluding it retires those pages in one pass, past the
        # deletion breaker (as for a held listing), and one whose glob would also match a folder holding
        # mirrored files gets its own line (B13 round 5 F65: the command left it, so the wait never cleared).
        gone = [files for files in below.values() if files]
        # What completing the listing will check (N26): the mirrored files the last walk did not list, a
        # count and never a path, once per source, on its last line: the gone line when there is one (its
        # step is what completes the listing, and "in all" says its own count is among them, unless the
        # count is only its own: gone_tail), else the paste line. A cleared folder holds none of them, so the
        # paste retires none: each takes the two-pass deletion check and the breaker (and a queued purge,
        # under the purge setting).
        size = (
            f"the last walk of {src.id} did not list {_files(missed)} the mirror still has: once the listing "
            "is complete, later syncs take out "
            + ("its page if it is still absent" if missed == 1 else "the pages of those still absent")
            + ", with the usual deletion check"
            if missed
            else ""
        )
        # singular or plural by the count each line states (B13 round 13 O35: "1 ... folder(s) keep", "them");
        # what the alarm names, by every empty folder it lists, which one line's count may be a part of: the
        # walk's stored count, not ``below``, which drops the folders excluded since (B13 round 14 Q58)
        alone = n_stored == 1
        # Folders the walk stored and excluded since (by hand, or exclude-empty before the next sync): the
        # alarm the pointer names is from before the exclude (B13 round 15 S32), and each line's folders are
        # "still not excluded", so its count below the alarm's reads true, as folders_in's (round 15 S66,
        # the option round 14 Q58's verifier asked for)
        since = len(below) < n_stored
        still = ", still not excluded," if since else ""

        # the noun form on every wait (B13 round 17 U49: the collides wait's "which names them" had no plural
        # before it; the alarm names every stored empty folder, so "the folders" is true at every count)
        if since:
            points = named_since(src.id, len(below))
        else:
            points = named([src.id], _alarm_names("the folder" if alone else "the folders", n_stored))
        if clear:
            one = len(clear) == 1
            waits.append(
                # Each referent named, no pronoun (B13 round 4 Q11): "it" read as the folder, as if a sync
                # would delete it, and excluding is not the only way the held deletions are released.
                f"{len(clear)} empty cloud {'folder' if one else 'folders'}{still} "
                f"{'keeps' if one else 'keep'} the listing of "
                f"{src.id} incomplete: an "
                "empty folder in a cloud folder is expected and loses nothing, but while the listing is "
                f"incomplete, deletions in {src.id} are held, not applied, and another sync does not "
                f"complete it; if {'the folder is' if one else 'the folders are'} meant to be empty, "
                # One command, not a hand edit of sources.toml (B13 round 4 Q71): it excludes every such
                # folder, so no second round for a sixth one, and checks the file before it writes it.
                # "it adds it" named the command and the folder with one word (B13 round 14 Q43)
                f"run `{BIN} exclude-empty {shlex.quote(src.id)}` (it adds {'the folder' if one else 'them'} "
                "to that "
                f"source's exclude in "
                f"{_shown(config.config_path)}; an excluded folder is not mirrored if it later gains files); "
                # Which folders, by where they are named (B13 round 5 F43; round 6 G13, G42): the wait
                # carries a count and never a path, and status prints no folder of the source.
                + points
                + (f"; {size}" if size and not gone and not collides else "")
            )
            names_source(src.id)
        if collides:
            one = len(collides) == 1
            waits.append(
                f"{len(collides)} empty cloud "
                + (
                    f"folder in {src.id}{still} has a [ in its name, so an exclude of it"
                    if one
                    else f"folders in {src.id}{still} have a [ in the name, so an exclude of one"
                )
                + " would also match a folder whose files are mirrored (the listing stays incomplete, so "
                f"deletions in {src.id} are held, and another sync does not clear it): rename or remove the "
                f"empty {'folder' if one else 'folders'} in the cloud drive; "
                + points
                + (f"; {size}" if size and not gone else "")
            )
            names_source(src.id)
        if gone:
            one = len(gone) == 1
            one_file = sum(gone) == 1  # the agreeing words follow the file count too (B13 round 14 Q81)
            waits.append(
                f"{len(gone)} empty cloud {'folder' if one else 'folders'} in {src.id}{still} held "
                f"{_files(sum(gone))} the mirror still has (the listing stays incomplete, so "
                f"{'its' if one_file else 'their'} deletion is held; another sync does not clear it): if "
                f"{'the file was' if one_file else 'the files were'} removed on purpose, remove the empty "
                f"{'folder' if one else 'folders'} from the cloud drive too, and later syncs take "
                f"{'its page' if one_file else 'the pages'} out with the usual deletion check; "
                # one file is one page (B13 round 17 U51); "its" stays the folder's, never "their". Two or
                # more folders are "them" and "those pages": each may hold one file (B13 round 18 V43)
                + (
                    "excluding such a folder instead retires "
                    f"{'its page' if one_file else 'its pages'} at once, "
                    if one
                    else "excluding them instead retires those pages at once, "
                )
                + "with no deletion check and no purge queued; "
                + points
                + gone_tail(src.id, missed, size)
            )
            names_source(src.id)

    # A source whose last walk missed mirrored files gets its own line: excluding the folder retires their
    # pages at once (the gone line's warning), so that line never offers the exclude as a plain choice.
    # One line for every such source with the same fix and cause (B13 round 11 M42: the fix follows the kinds
    # the walk stored, so sources whose folders were refused differently cannot share one; round 12 N12, N38:
    # nor can a source with a sentinel and one without, or one of no stored kind). A source whose last walk
    # missed files elsewhere has a line of its own, which counts them (B13 round 12 N54).
    def folders_in(sid: str) -> tuple[str, bool]:
        """The subject of a source's own could-not-be-listed line, and whether it counts one folder: the
        count the walk stored (B13 round 13 O16, O35: "one or more" beside a WARNING and an alarm that
        count them), "still" when some were excluded since, so a count below the alarm's reads true; "one
        or more" only for state that stored none (never "a folder": B13 round 10 L48, L67)."""
        if sid not in counts:
            return f"one or more folders in {sid} could not be listed", False
        n, stored = counts[sid]
        since = (
            (" and is still not excluded" if n == 1 else " and are still not excluded") if n < stored else ""
        )
        return f"{n} {'folder' if n == 1 else 'folders'} in {sid} could not be listed{since}", n == 1

    def failed_names(sids: Sequence[str]) -> str:
        """What the alarm lines of ``sids`` name for the folders their walks could not list: those folders,
        when each walk stored one to five, all of which its alarm lists (B13 round 14 Q41: "up to five, and
        past five how to list the rest" beside one folder); else :data:`_WHAT_FAILED`, also for state that
        stored no count and for a walk whose alarm may name only a missing sentinel."""
        if not all(s in counts and s not in sentinel_too and counts[s][1] <= 5 for s in sids):
            return _WHAT_FAILED
        one = len(sids) == 1 and counts[sids[0]][1] == 1
        return "the folder that could not be listed" if one else "the folders that could not be listed"

    def sentinel_fix(sid: str) -> str:
        """The sentinel's fix beside the folders', for a source whose walk stored no cause (B13 round 13
        O53)."""
        return (
            f"if the sentinel set for {sid} is what is missing, restore it in the folder of {sid}, or change "
            f'or remove sentinel in [[source]] id = "{sid}" in {_shown(config.config_path)}'
        )

    by_fix: dict[tuple[str, str, str], list[str]] = {}
    for sid in sorted(s for s, missed in unlisted if not missed):
        key = (causes.get(sid, _ANY_CAUSE), access_fixes.get(sid, _access_fix(None)))
        own = sid if sid in held_elsewhere or sid in sentinel_too else ""
        by_fix.setdefault((*key, own), []).append(sid)
    for (cause, fix, _own), bare in by_fix.items():
        held_there = (
            f"; the last walk of {bare[0]} did not list {_files(held_elsewhere[bare[0]])} the mirror still "
            f"has, and {'its' if held_elsewhere[bare[0]] == 1 else 'their'} deletion stays held until a sync "
            f"lists {bare[0]} in full"
            if bare[0] in held_elsewhere
            else ""
        )
        subject, one = folders_in(bare[0])
        fix_one = _access_fix(all_kinds.get(bare[0]), one=True) if one else fix
        waits.append(
            # the alarm named by what it names (B13 round 10 L67): it names five of six
            f"{subject} ({cause}; another sync does not clear "
            f"it): {named(bare, failed_names(bare))}; {fix_one}, or add {'it' if one else 'each one'} to "
            f"that source's exclude in {_shown(config.config_path)}"
            + (f"; {sentinel_fix(bare[0])}" if bare[0] in sentinel_too else "")
            + held_there
            if len(bare) == 1
            # One line for every such source, plural throughout (B13 round 7 H50): "a folder in A, B" and
            # "that source's exclude" read as one folder of one source.
            else f"one or more folders in each of {', '.join(bare[:-1])} and {bare[-1]} could not be listed "
            f"({cause}; another sync does not clear it): "
            f"{named(bare, failed_names(bare))}; {fix}, or add each to its source's "
            # the config by its path, as the empty-folder wait names it (B13 round 9 K43)
            f"exclude in {_shown(config.config_path)}"
        )
        names_source(*bare)
    for sid, missed in sorted((s, m) for s, m in unlisted if m):
        subject, one = folders_in(sid)
        fix = _access_fix(all_kinds.get(sid), one=True) if one else access_fixes.get(sid, _access_fix(None))
        waits.append(
            f"{subject} ({causes.get(sid, _ANY_CAUSE)}; "
            f"another sync does not clear it): {named([sid], failed_names([sid]))}; "
            + (
                f"{fix}, or, {sentinel_fix(sid)} (the safe fixes); "
                if sid in sentinel_too
                else f"{fix} (the safe fix); "
            )
            + (
                f"in all, the last walk of {sid} did not list {_files(missed)} the mirror still has, "
                + _under_gone(missed, *in_all[sid])
                + ", and "
                if in_all.get(sid, (0, 0))[0]
                else f"the last walk of {sid} did not list {_files(missed)} the mirror still has, and "
            )
            + "adding such a folder to that source's exclude retires the pages under it at once, with no "
            "deletion check and no purge queued"
        )
        names_source(sid)
    for sid in sentinel_missing:
        waits.append(
            f"the sentinel set for {sid} was missing at its last sync, so its listing is incomplete "
            f"and deletions in {sid} are held (another sync does not clear it): restore it in the folder of "
            f'{sid}, or change or remove sentinel in [[source]] id = "{sid}" in {_shown(config.config_path)}'
        )
        checks.append(f"heartbeat.{sid}")
        names_source(sid)
    # once per source: one with empty cloud folders beside has its check already (B13 round 10 L68)
    checks.extend(c for s, _ in sorted(unlisted) if (c := f"heartbeat.{s}") not in checks)
    for sid in sorted(root_unlisted):  # a root cannot be excluded: never offer it
        pause = f'set state = "paused" in [[source]] id = "{sid}" in {_shown(config.config_path)}'
        if root_kinds.get(sid) == frozenset({UNKNOWN_MISSING}):
            waits.append(_missing_root_wait(config, sid, pause, state=root_states.get(sid, "")))
        elif root_kinds.get(sid) == frozenset({UNKNOWN_MODE}):
            # B13 round 11 M42: a root refused with EACCES is no unmounted volume, and no grant overrides it
            waits.append(
                f"the folder of {sid} itself could not be listed: its own permissions do not let this "
                "account read it (EACCES; no Files and Folders grant overrides them, and another sync does "
                "not clear it): check its owner and mode with `ls -ld`; to stop syncing it on purpose, "
                f"{pause}"
            )
        else:
            waits.append(
                f"the folder of {sid} itself could not be listed or is empty (an unmounted volume, a sync "
                "client that has not filled it yet, or no access; another sync does not clear it): mount it "
                "or let the sync client fill it, or grant Files and Folders access; to stop syncing it on "
                f"purpose, {pause}"
            )
        checks.append(f"heartbeat.{sid}")
        names_source(sid)
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
        one = sum(files.online.values()) == 1
        notes.append(
            f"{plural(sum(files.online.values()), 'online-only file')} in {_ids(files.online)} "
            f"{'waits' if one else 'wait'} for a later sync's download budget; "
            f"{'it does' if one else 'they do'} not block the next step"
        )
    recording_waits, recording_notes = _recording_lines(
        files, background=bool(files.recording) and launchd.agents_installed(config)
    )
    waits += recording_waits
    notes += recording_notes
    if recording_waits:
        names_source(*files.recording)
    notes += _reread_notes(rereading, unread, reread_runs, downloads=bool(files.online))
    eval_dir = docs / _EVAL_DIR
    questions = _eval_status(eval_dir / "questions.md")
    answers = _eval_status(eval_dir / "answers.md")
    confirmed = questions == "confirmed" and answers == "confirmed"
    waits += docs_waits(config)  # the baseline draft's, also status's when the state cannot be read (D11)
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
    if isinstance(unread_state, ManifestBusyError):
        return done(
            "agentsync's state file changed while it was read (a sync is writing it): once that sync ends, "
            f"run `{BIN} status` to read the next step",
            3,
        )
    if unread_state is not None:
        return done(
            "agentsync's state file is not yet at the schema this build reads (an upgrade the next sync "
            f"finishes): run `{BIN} sync`",
            3,
        )
    if incomplete or files.local:
        behind = sorted(set(incomplete) | set(files.local))
        return done(
            f"{plural(len(behind), 'source')} not fully listed or converted yet ({', '.join(behind)}): "
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
            f"{plural(blockers, 'curation error')} {'holds' if blockers == 1 else 'hold'} the checkpoint: "
            f"run `{BIN} curate`, fix every ERROR it lists, then run `{BIN} sync`",
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
            f"{plural(rows, 'curation row')} queued: run `{BIN} curate`, "
            + ("curate it" if rows == 1 else f"curate up to {ROWS_PER_SESSION} of them")
            + f", then run `{BIN} sync`; session done",
            9,
        )
    return done("nothing to do: session done", 10)


def docs_waits(config: Config) -> list[str]:
    """The WAITING ON YOU lines that read the docs repo alone, never the state: the baseline questions'
    draft. :func:`next_step` gives them in its order, and status gives them after the NEXT of a FAIL when the
    state cannot be read, so a broken state folder or manifest no longer drops the operator's reminder (B13
    round 25 D11, D29: the loop's line said "baseline draft" while no WAITING ON YOU line did)."""
    eval_dir = expand(config.docs_repo) / _EVAL_DIR
    questions = _eval_status(eval_dir / "questions.md")
    answers = _eval_status(eval_dir / "answers.md")
    confirmed = questions == "confirmed" and answers == "confirmed"
    if questions is None or confirmed:
        return []
    # Where the two files are, not only their names: whoever confirms them did not write them (v9 rehearsal,
    # 2026-10-07). "up to", "all of them if there are fewer": the skill drafts fewer than 10 for a small
    # mirror, and the file holds what is left after the operator's cuts, not what was drafted (B13 round 5
    # F1, F47). No count is read: the operator edits the file by hand.
    return [
        f"the baseline questions are a draft: in {_shown(eval_dir)}, keep up to about 10 in "
        "questions.md (all of them if there are fewer), correct the answers in answers.md, and change "
        "both files to status: confirmed"
    ]


def next_lines(
    config: Config,
    *,
    fixes: Sequence[str] = (),
    count_queue: bool = True,
    in_state: bool = False,
    quiet: bool = False,
) -> list[str]:
    """:meth:`NextStep.lines` of :func:`next_step`; [] (with a logged warning) when the state cannot be read,
    so a caller's own exit status never changes because of the hint. ``quiet``: the caller prints a [FAIL]
    line that already says why (status with a failing check), so the warning is logged at debug: a
    timestamped ``WARNING agentsync.loop`` line above status's own NEXT repeated it (B13 round 20 X16, X30).
    A sync's call keeps the warning, the only record a background job's err.log has of it."""
    try:
        return next_step(config, fixes=fixes, count_queue=count_queue, in_state=in_state).lines()
    except (OSError, AgentSyncError) as exc:
        (_log.debug if quiet else _log.warning)("next step: cannot read the loop state: %s", exc)
        return []


def status_line(config: Config) -> str:
    """``status``'s one loop line (KISS K08a): ``loop: skill <current|stale|missing> · inbox <on|missing|off>
    · baseline <missing|draft|confirmed|before|after> · topics N · checkpoint <date|never> · to curate N ·
    archive <on|off>``. ``to curate`` is :func:`queue_rows` (not the purge queue, which status prints on its
    own line). ``inbox`` is the folder the guides name (the live inbox source on the folder
    ``config.ensure_inbox`` keeps, else every live inbox source); doctor checks each source's folder. Disk
    only; a part that cannot be read shows ``?``, one the configuration refuses ``not read (the configuration
    is refused)``, with no WARNING line: the governance.config or policy [FAIL] beside it gives the words
    (B13 round 18 V20)."""
    docs = expand(config.docs_repo)

    def part(name: str, fn: Callable[[], object]) -> str:
        try:
            return f"{name} {fn()}"
        except ConfigError:
            return f"{name} not read (the configuration is refused)"
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
