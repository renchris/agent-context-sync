"""The one layer between commands and the streams: where a line goes, when a run may ask, what a signal does.

It is the only module allowed to call ``isatty``. It imports nothing heavy: every command start pays for it.
The contract is CONTRACTS.md §15 ``agentsync.clio`` and §16.300.1; the why is
``docs/plans/cli-conventions.md`` §2.
"""

from __future__ import annotations

import contextlib
import enum
import itertools
import json
import os
import platform
import shlex
import signal
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from types import FrameType
from typing import TextIO

from agentsync.text import safe_text

_STDOUT_FD = 1
_NO_PERSON_ENV = ("CI", "SSH_CONNECTION", "SSH_TTY")
_CAUGHT_SIGNALS = (signal.SIGTERM, signal.SIGHUP)
_stdout_closed = False
_json_mode = False


def _discard_stdout() -> None:
    """Point fd 1 at the null device. Bytes still buffered then go there, so the interpreter's flush at exit
    meets no EPIPE and cannot turn a finished run into exit 120."""
    global _stdout_closed  # noqa: PLW0603 - once per process: the reader of this pipe does not come back
    _stdout_closed = True
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, _STDOUT_FD)
    finally:
        os.close(devnull)


def _write_stdout(text: str) -> None:
    """A reader that went away (``| head``) ends the output, not the run. Flushed per line, since a
    block-buffered write would meet the closed pipe only at exit."""
    if _stdout_closed:
        return
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        _discard_stdout()


def _flush_stdout() -> None:
    """stdout goes before anything on stderr: in a pipe it is block-buffered and stderr is not, so a line
    printed before an error would land after it (B13 round 11 M26)."""
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        _discard_stdout()
    except (OSError, ValueError):
        pass


def result(text: str = "") -> None:
    """One line of the command's result, on stdout. Nothing in JSON mode, where only ``emit`` writes there."""
    if not _json_mode:
        _write_stdout(text)


def say(text: str) -> None:
    """One line for the person, on stderr. Nothing in JSON mode, where ``error_doc`` is all stderr carries."""
    if _json_mode:
        return
    _flush_stdout()
    sys.stderr.write(text + "\n")


def stdout_closed() -> bool:
    """True once a write or flush of stdout has met EPIPE."""
    return _stdout_closed


def table(
    header: Sequence[str], rows: Iterable[Sequence[str]], *, to: Callable[[str], None] = result
) -> None:
    """A header and rows. Off a terminal, one tab between cells and no padding, for ``cut`` and ``awk``; on
    one, columns aligned by two spaces. A cell's own newline or tab is shown, so it cannot add a line or a
    column. Nothing in JSON mode, even with a ``to`` of the caller's."""
    if _json_mode:
        return
    lines = [[safe_text(cell, cell=True) for cell in row] for row in (header, *rows)]
    if not stdout_is_tty():
        for line in lines:
            to("\t".join(line))
        return
    widths = [max(map(len, column)) for column in itertools.zip_longest(*lines, fillvalue="")]
    for line in lines:
        to("  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=False)).rstrip())


def _isatty(stream: TextIO) -> bool:
    """A closed stream is not a terminal, and asking it raises ValueError."""
    try:
        return stream.isatty()
    except ValueError:
        return False


def stdout_is_tty() -> bool:
    """True when stdout is a terminal."""
    return _isatty(sys.stdout)


def interactive() -> bool:
    """True when a person can answer a prompt: stdin is a terminal, and this is neither CI nor an SSH session.
    It ignores stdout and stderr on purpose, so ``agentsync login 2>&1 | tee`` still works."""
    return _isatty(sys.stdin) and not any(os.environ.get(name) for name in _NO_PERSON_ENV)


def recovery(instruction: str, command: str) -> None:
    """The way out of an error, on stderr: a blank line, ``<instruction>:``, the command indented by four."""
    say(f"\n{instruction}:\n    {command}")


def set_json(on: bool) -> None:
    """Turn JSON mode on or off for this process."""
    global _json_mode  # noqa: PLW0603 - set once, from the parsed command line
    _json_mode = on


def json_mode() -> bool:
    """True once ``set_json(True)`` has run."""
    return _json_mode


def emit(doc: Mapping[str, object]) -> None:
    """One document on stdout: indented by two on a terminal, one compact line otherwise. Non-ASCII is
    escaped, so remote text cannot reach a terminal as a control character and any stream can encode it."""
    if stdout_is_tty():
        _write_stdout(json.dumps(dict(doc), indent=2))
    else:
        _write_stdout(json.dumps(dict(doc), separators=(",", ":")))


class Reason(enum.StrEnum):
    """Every value of a JSON error document's ``reason`` key. ``errors.py``'s ``exc.reason`` holds strerror
    words and never fills it."""

    usage = enum.auto()
    config_invalid = enum.auto()
    lock_held = enum.auto()
    login_required = enum.auto()
    sign_in_blocked = enum.auto()
    sign_in_failed = enum.auto()
    state_unreadable = enum.auto()
    state_misplaced = enum.auto()
    state_refused = enum.auto()
    governance_unreadable = enum.auto()
    manifest_schema = enum.auto()
    confirmation_required = enum.auto()
    failed = enum.auto()
    internal_error = enum.auto()
    canceled = enum.auto()
    api_error = enum.auto()
    rate_limited = enum.auto()
    network_policy = enum.auto()


def error_doc(
    reason: Reason,
    message: str,
    *,
    status: str = "error",
    next: Sequence[Mapping[str, object]] = (),
    data: Mapping[str, object] | None = None,
    user_action_required: bool = False,
    retryable: bool | None = None,
    retry_after: float | None = None,
    request_id: str | None = None,
    warnings: Sequence[Mapping[str, str]] = (),
) -> None:
    """The error as exactly one line on stderr, in JSON mode only: a caller writes it last, and calls it next
    to the human line without asking which mode this is. A key with nothing to say is absent."""
    if not _json_mode:
        return
    doc: dict[str, object] = {"status": status, "reason": reason.value, "message": message}
    if next:
        doc["next"] = [dict(step) for step in next]
    if data is not None:
        doc["data"] = dict(data)
    if user_action_required:
        doc["userActionRequired"] = True
    if retryable is not None:
        doc["retryable"] = retryable
    if retry_after is not None:
        doc["retryAfter"] = retry_after
    if request_id is not None:
        doc["requestId"] = request_id
    if warnings:
        doc["warnings"] = [dict(warning) for warning in warnings]
    _flush_stdout()
    sys.stderr.write(json.dumps(doc, separators=(",", ":")) + "\n")


def _ask(prompt: str) -> str:
    """The prompt on stderr, since stdout carries the result, then one line of stdin; "" at end of input."""
    _flush_stdout()
    sys.stderr.write(prompt)
    sys.stderr.flush()
    return sys.stdin.readline()


def confirm(
    *,
    tier: str,
    effects: Sequence[str],
    name: str | None,
    argv: Sequence[str],
    consequence: str,
    yes: bool,
    noun: str = "target",
) -> bool:
    """The one consent helper: True on ``--yes``, or when a person answers. It shows what would happen, then
    tier ``destructive`` asks yes or no, default no, and any other tier makes the person type ``name``.

    With no one to ask (off a terminal, or in JSON mode) it reads nothing and returns False, having said what
    would approve the run. ``argv`` is that approved command line.
    """
    if tier != "destructive" and name is None:
        raise ValueError(f"tier {tier} needs a name to type")
    if yes:
        return True
    for effect in effects:
        say(safe_text(effect))
    say(consequence)
    if _json_mode or not interactive():
        command = shlex.join(argv)
        message = "this run does not prompt, so nothing changed."
        error_doc(
            Reason.confirmation_required,
            message,
            status="action_required",
            next=[
                {
                    "command": command,
                    "argv": list(argv),
                    "when": f"Only after a human approves: {consequence}",
                }
            ],
            data={"effects": list(effects)},
            user_action_required=True,
        )
        say(f"error: {Reason.confirmation_required.value}: {message}")
        recovery("Only after a human approves, run", safe_text(command))
        return False
    if tier == "destructive":
        return _ask("Continue? (y/N) ").strip().lower() in ("y", "yes")
    shown = safe_text(str(name))
    if _ask(f'To confirm, type the {noun} name "{shown}": ').removesuffix("\n") == name:
        return True
    say(f'error: that name does not match. Expected "{shown}".')
    return False


def _bug_site(exc: BaseException) -> str:
    """``<Type> at <module>:<line>`` for the innermost agentsync frame. A module name, not a file: no path,
    and never ``str(exc)``, which can quote one."""
    site = ""
    tb = exc.__traceback__
    while tb is not None:
        module = str(tb.tb_frame.f_globals.get("__name__", ""))
        if module == "agentsync" or module.startswith("agentsync."):
            site = f" at {module}:{tb.tb_lineno}"
        tb = tb.tb_next
    return type(exc).__name__ + site


def bug_report(exc: BaseException, command: str | None, *, version: str, issues_url: str) -> None:
    """What a person can paste into an issue about a defect in agentsync: no message, path or argv."""
    where = f"agentsync {command}" if command else "agentsync"
    say(f"error: Unexpected error in {where}. This is a bug in agentsync.")
    say(f"  Error: {_bug_site(exc)}")
    host = f"macOS {platform.mac_ver()[0]}" if platform.mac_ver()[0] else platform.system()
    say(f"  Version: agentsync {version}, Python {platform.python_version()}, {host}")
    say(f"  Report: {issues_url}/issues/new")


class Terminated(KeyboardInterrupt):
    """SIGTERM or SIGHUP arrived. A ``KeyboardInterrupt``, so the cleanup Ctrl-C runs also runs for these."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


def _raise_terminated(signum: int, _frame: FrameType | None) -> None:
    raise Terminated(signum)


@contextlib.contextmanager
def signals() -> Iterator[None]:
    """Inside the block, SIGTERM and SIGHUP raise ``Terminated``; the previous handlers come back afterwards.

    A signal the process already ignores stays ignored, or ``nohup agentsync ...`` would die at the hangup it
    was started to survive. Python takes handlers only on the main thread, so elsewhere this changes nothing.
    """
    previous = []
    with contextlib.suppress(ValueError):
        for signum in _CAUGHT_SIGNALS:
            if signal.getsignal(signum) != signal.SIG_IGN:
                previous.append((signum, signal.signal(signum, _raise_terminated)))
    try:
        yield
    finally:
        for signum, handler in previous:
            # None: a handler Python did not install and cannot put back
            signal.signal(signum, signal.SIG_DFL if handler is None else handler)
