"""install.sh's install.log, read run by run (B13 round 47 C3241, ruling 213).

The parser :mod:`agentsync.setup_report` reads install.log with, in a module of its own, so another reader
(``loop.next_step``'s rule 2, which gives the person's-choice NEXT after a completed ``--no-folder`` run)
reads the same runs and cannot drift from the report: :class:`InstallRun`, :func:`install_runs_only`,
:func:`read_install_runs` and their helpers. :mod:`agentsync.setup_report` re-exports each under its old
name."""

from __future__ import annotations

import dataclasses
import re
import shlex
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "InstallRun",
    "install_runs_only",
    "parse_time",
    "read_install_runs",
]

_INSTALL_LINE_RE = re.compile(r"^(?P<at>\S+) run=(?P<run>\S+) (?P<rest>.*)$")
_KV_RE = re.compile(r"(\w+)=(\S+)")
_END_RE = re.compile(r"^\S+ run=\S+ end ")
_SIMULATED_RE = re.compile(r"\blaunchd\s*[=:]\s*simulated\b", re.IGNORECASE)


def parse_time(text: str) -> datetime | None:
    """An ISO-8601 time (``date -u +%FT%TZ``) as an aware UTC datetime; None when it is not one."""
    text = text.strip().strip("*`").strip()
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


_BLOCKING_STATUS_NOTES = frozenset({"fail-lines", "status-error", "timeout"})
"""The status step's notes that block a run (B13 round 45 C2947, ruling 192): [FAIL] lines, an exit with no
[FAIL] line, the watchdog's TERM."""

_VALUE_OPTIONS = frozenset({"--source-local", "--cwd", "--launcher", "--timeout", "-n", "--limit"})
"""install.sh's options whose next word is their value (B13 round 45 C2933 with C2911 and C2846, ruling 192):
a folder that value names is never read as ``--no-folder``, however it is spelled."""


def _q_words(args: str) -> list[str]:
    """``args`` split into its top-level words as ``printf %q`` wrote them, each kept as written: a ``\\x``
    is one character, and a ``'..'`` or ``$'..'`` span (backslash escapes inside the latter) is skipped
    whole, so only a blank outside them ends a word (B13 round 45 C2911, ruling 192): the scan for an args=
    that :func:`shlex.split` refuses, a ``$'..'`` word holding ``\\'``."""
    words: list[str] = []
    word: list[str] | None = None
    i, n = 0, len(args)
    while i < n:
        c = args[i]
        if c in " \t\n":
            if word is not None:
                words.append("".join(word))
                word = None
            i += 1
            continue
        word = [] if word is None else word
        if c == "\\":
            end = i + 2
        elif args.startswith("$'", i):
            end = i + 2
            while end < n and args[end] != "'":
                end += 2 if args[end] == "\\" else 1
            end += 1
        elif c == "'":
            close = args.find("'", i + 1)
            end = n if close < 0 else close + 1
        else:
            end = i + 1
        word.append(args[i:end])
        i = end
    if word is not None:
        words.append("".join(word))
    return words


@dataclasses.dataclass(frozen=True, slots=True)
class InstallRun:
    """One scripts/install.sh run from install.log."""

    run_id: str
    started: datetime | None
    rc: int | None  # the end line's exit status; None: no end line (stopped early, or still running)
    seconds: int | None
    steps: tuple[tuple[str, dict[str, str]], ...]  # (step name, fields) in log order, the report step too
    simulated: bool  # a line says launchd=simulated
    lines: tuple[str, ...]
    args: str = ""  # the start line's arguments, as install.sh logged them (shell-quoted)
    label: str = ""  # display_id, numbered over the whole log (read_install_runs)

    def step(self, name: str) -> dict[str, str] | None:
        """The fields of step ``name``, if the run logged it."""
        return next((v for k, v in self.steps if k == name), None)

    def failed_step(self) -> tuple[str, dict[str, str]] | None:
        """The last step with ``result=failed`` (the report step excluded)."""
        failed = [(k, v) for k, v in self.steps if v.get("result") == "failed" and k != "report"]
        return failed[-1] if failed else None

    @property
    def completed_none(self) -> bool:
        """Whether this run ended rc 0 with no failed step and no status note that blocks (``fail-lines``,
        ``status-error``, ``timeout``): a ``--no-folder`` run is scored as the person's choice only then (B13
        round 45 C2947 with C2837 and C2861, ruling 192). An older install.sh ended a --no-folder run over a
        config it created 0 even when status printed [FAIL] (C2909's report half)."""
        status = self.step("status") or self.step("doctor") or {}
        blocked = status.get("note", "") in _BLOCKING_STATUS_NOTES
        return self.rc == 0 and self.failed_step() is None and not blocked

    @property
    def chose_no_folder(self) -> bool:
        """Whether install.sh logged ``--no-folder`` on this run's start line, the person's none to the folder
        question (B13 round 44 ruling 178, C2605): a word of ``args=``, never inferred from an exit code.

        B13 round 45 C2933 with C2911 and C2846 (ruling 192): a shell word, never a regex over the line, which
        read a folder logged as ``/w/Project\\ --no-folder`` as the flag: ``args=`` split as the shell splits
        it (:func:`shlex.split`), the word after each of :data:`_VALUE_OPTIONS` skipped, true when a bare
        ``--no-folder`` remains; an ``args=`` shlex refuses is read by :func:`_q_words`, whose top-level words
        alone are tested."""
        try:
            words = shlex.split(self.args)
        except ValueError:
            words = _q_words(self.args)
        it = iter(words)
        for word in it:
            if word in _VALUE_OPTIONS:
                next(it, None)
            elif word == "--no-folder":
                return True
        return False

    @property
    def no_folder_first_run(self) -> bool:
        """Whether this is the run v40 prescribes before the folder answer (B13 round 44 C2605's predicate): a
        bare step 2 that made the config (``config result=done note=created...``), skipped its first sync for
        no source (``first-sync result=skipped note=no-sources``), failed no step and ended 2. A usage
        error's 2 logs no config step and is never one."""
        config, first = self.step("config") or {}, self.step("first-sync") or {}
        return (
            self.rc == 2
            and config.get("result") == "done"
            and config.get("note", "").startswith("created")
            and first.get("result") == "skipped"
            and first.get("note") == "no-sources"
            and self.failed_step() is None
        )

    @property
    def display_id(self) -> str:
        """The run id without its process-id suffix (a weak fingerprint, of no use to triage); a later run
        started in the same second as an earlier one is ``<stamp>.2``, ``.3`` (:func:`read_install_runs`)."""
        return self.label or re.sub(r"-\d+$", "", self.run_id)

    @property
    def report_after_end(self) -> bool:
        """Whether the ``step=report`` line comes after the run's ``end`` line (install.sh writes the setup
        report after closing the run: the report step is outside the run's seconds)."""
        end = next((i for i, ln in enumerate(self.lines) if _END_RE.search(ln)), None)
        report = next((i for i, ln in enumerate(self.lines) if " step=report " in f"{ln} "), None)
        return end is not None and report is not None and report > end

    @property
    def list_only(self) -> bool:
        """Whether this is an ``install.sh --list-folders`` run (setup prompt step 1), not an install run."""
        return self.step("list-folders") is not None


def install_runs_only(runs: Sequence[InstallRun]) -> list[InstallRun]:
    """``runs`` without the ``--list-folders`` runs (what the outcome, run type and agent state judge)."""
    return [r for r in runs if not r.list_only]


def read_install_runs(log: Path) -> list[InstallRun]:
    """install.log's runs, oldest first. Each is labelled by its run id less the process id; runs started in
    the same second are numbered in log order over the whole log, the first keeping the bare stamp
    (``20261016T155217Z``, ``20261016T155217Z.2``): step 1's ``--list-folders`` and the install run often
    start in one second, and both read as one id (B13 round 12 N21). Counted over the whole log, so a label
    does not change as the window of shown runs slides; a "." inside the token, which neither pid strip
    removes again, as "-2" would be."""
    out: list[InstallRun] = []
    seen: Counter[str] = Counter()
    for run_id, fields, lines in _install_runs(log):
        stamp = re.sub(r"-\d+$", "", run_id)
        seen[stamp] += 1
        end = fields.get("end", {})
        rc_text, secs = end.get("rc", ""), end.get("seconds", "")
        steps = tuple((k, v) for k, v in fields.items() if k not in ("start", "end"))
        out.append(
            InstallRun(
                run_id=run_id,
                started=parse_time(fields.get("start", {}).get("at", "")),
                # B13 round 45 C2847: ASCII digits only, as C2677's readers take them; \d matches U+0660
                rc=int(rc_text) if re.fullmatch(r"-?[0-9]+", rc_text) else None,
                seconds=int(secs) if secs.isascii() and secs.isdigit() else None,  # B13 round 44 C2677
                steps=steps,
                simulated=any(_SIMULATED_RE.search(ln) for ln in lines),
                lines=tuple(lines),
                args=fields.get("start", {}).get("args", ""),
                label=stamp if seen[stamp] == 1 else f"{stamp}.{seen[stamp]}",
            )
        )
    return out


def _install_runs(log: Path) -> list[tuple[str, dict[str, dict[str, str]], list[str]]]:
    """install.log grouped by run id, oldest first: (run id, {"start"/"end"/step name: fields}, lines)."""
    runs: dict[str, tuple[dict[str, dict[str, str]], list[str]]] = {}
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _INSTALL_LINE_RE.match(line)
        if m is None:
            continue
        fields, lines = runs.setdefault(m["run"], ({}, []))
        lines.append(line)
        rest = m["rest"]
        kv = dict(_KV_RE.findall(rest.split(" args=", 1)[0]))
        kv["at"] = m["at"]
        if rest.startswith("start "):
            kv["args"] = rest.split(" args=", 1)[1] if " args=" in rest else ""
            fields["start"] = kv
        elif rest.startswith("end "):
            fields["end"] = kv
        elif "step" in kv:
            fields[kv["step"]] = kv
    return [(run_id, fields, lines) for run_id, (fields, lines) in runs.items()]
