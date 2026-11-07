"""The one layer between commands and the streams: where a line goes, when a run may ask, what a signal does.

It is the only module allowed to call ``isatty``. It imports nothing heavy: every command start pays for it.
The contract is CONTRACTS.md §15 ``agentsync.clio`` and §16.300.1; the why is
``docs/plans/cli-conventions.md`` §2.
"""

from __future__ import annotations

import contextlib
import itertools
import os
import signal
import sys
from collections.abc import Callable, Iterable, Iterator, Sequence
from types import FrameType
from typing import TextIO

from agentsync.text import safe_text

_STDOUT_FD = 1
_NO_PERSON_ENV = ("CI", "SSH_CONNECTION", "SSH_TTY")
_CAUGHT_SIGNALS = (signal.SIGTERM, signal.SIGHUP)
_stdout_closed = False


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


def result(text: str = "") -> None:
    """One line of the command's result, on stdout. A reader that went away (``| head``) ends the output, not
    the run. Flushed per line, since a block-buffered write would meet the closed pipe only at exit."""
    if _stdout_closed:
        return
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        _discard_stdout()


def say(text: str) -> None:
    """One line for the person, on stderr. stdout goes first: in a pipe it is block-buffered and stderr is
    not, so a line printed before an error would land after it (B13 round 11 M26)."""
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        _discard_stdout()
    except (OSError, ValueError):
        pass
    sys.stderr.write(text + "\n")


def stdout_closed() -> bool:
    """True once a write or flush of stdout has met EPIPE."""
    return _stdout_closed


def table(
    header: Sequence[str], rows: Iterable[Sequence[str]], *, to: Callable[[str], None] = result
) -> None:
    """A header and rows. Off a terminal, one tab between cells and no padding, for ``cut`` and ``awk``; on
    one, columns aligned by two spaces. A cell's own newline or tab is shown, so it cannot add a line or a
    column."""
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
