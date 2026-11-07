"""The shared stream, mode and signal layer, and the sanitizer it prints through."""

from __future__ import annotations

import os
import signal
import sys
from collections.abc import Iterator
from pathlib import Path
from types import FrameType

import pytest

from agentsync import clio, text

SRC = Path(__file__).resolve().parents[1] / "src" / "agentsync"

VTT_CLASS_AT_D62AC148 = (
    r"[\x00-\x1f\x7f-\x9f\ud800-\udfff\u2028\u2029\U000e0000-\U000e007f\u115f\u1160\u3164\uffa0\u2800]"
)


@pytest.fixture(autouse=True)
def _stdout_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """The closed flag is per process and a test sets it; monkeypatch puts it back."""
    monkeypatch.setattr(clio, "_stdout_closed", False)


@pytest.fixture
def known_signals() -> Iterator[None]:
    """Each signal the test touches starts from a state it sets itself: a detached gate ignores SIGHUP."""
    saved = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGHUP)}
    yield
    for signum, handler in saved.items():
        signal.signal(signum, handler)


def test_result_survives_a_closed_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    read_end, write_end = os.pipe()
    os.close(read_end)
    saved = os.dup(1)
    os.dup2(write_end, 1)
    os.close(write_end)
    try:
        with open(1, "w", encoding="utf-8", closefd=False) as stream:
            monkeypatch.setattr(sys, "stdout", stream)
            clio.result("x")
            assert clio.stdout_closed() is True
            clio.result("the next line is dropped, not raised")
            stream.flush()  # the line EPIPE left buffered goes to the null device, as it would at exit
    finally:
        os.dup2(saved, 1)
        os.close(saved)


def test_say_flushes_stdout_first(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clio.result("a")
    clio.say("b")
    captured = capsys.readouterr()
    assert captured.out == "a\n"
    assert captured.err == "b\n"

    # One file behind both streams, as `2>&1` makes it, shows the order: stdout buffers and stderr does not.
    shared = tmp_path / "both"
    with (
        shared.open("w", encoding="utf-8") as out,
        shared.open("a", encoding="utf-8", buffering=1) as err,
    ):
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setattr(sys, "stderr", err)
        out.write("a\n")
        clio.say("b")
    assert shared.read_text(encoding="utf-8") == "a\nb\n"


def test_safe_text_strips_controls_bidi_zero_width() -> None:
    got = text.safe_text("a\x1b[31mb\u202ec\u200bd\x85e\nf")
    for gone in ("\x1b", "\u202e", "\u200b", "\x85"):
        assert gone not in got
    assert got == "abcde\\nf"
    assert text.safe_text("a\tb", cell=True) == "a\\tb"
    assert text.safe_text("a\x1b]0;title\x07b") == "ab"


def test_table_keeps_the_header_off_a_tty(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    clio.table(("kind", "state"), [("a", "b")])
    assert capsys.readouterr().out == "kind\tstate\na\tb\n"

    clio.table(("kind",), [("a\tb\nc",)])
    assert capsys.readouterr().out == "kind\na\\tb\\nc\n"

    monkeypatch.setattr(clio, "stdout_is_tty", lambda: True)
    lines: list[str] = []
    clio.table(("kind", "state"), [("longer", "b")], to=lines.append)
    assert lines == ["kind    state", "longer  b"]


def test_interactive_is_false_under_ci_ssh_or_piped_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CI", "SSH_CONNECTION", "SSH_TTY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    assert clio.interactive() is True  # so each False below is the case's doing

    with monkeypatch.context() as case:
        case.setenv("CI", "true")
        assert clio.interactive() is False
    with monkeypatch.context() as case:
        case.setenv("SSH_TTY", "/dev/ttys001")
        assert clio.interactive() is False
    with monkeypatch.context() as case:
        case.setattr(sys.stdin, "isatty", lambda: False, raising=False)
        assert clio.interactive() is False


@pytest.mark.usefixtures("known_signals")
def test_signals_raise_terminated_and_restore() -> None:
    def before(_signum: int, _frame: FrameType | None) -> None:
        raise AssertionError("the previous handler ran inside the block")

    signal.signal(signal.SIGTERM, before)
    signal.signal(signal.SIGHUP, before)
    with pytest.raises(clio.Terminated) as caught, clio.signals():
        os.kill(os.getpid(), signal.SIGTERM)
    assert caught.value.signum == signal.SIGTERM == 15
    assert signal.getsignal(signal.SIGTERM) is before
    assert signal.getsignal(signal.SIGHUP) is before

    # nohup: a hangup the process was started to ignore stays ignored
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    with clio.signals():
        assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
    assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN


def test_recovery_layout(capsys: pytest.CaptureFixture[str]) -> None:
    clio.recovery("To remove, run", "agentsync offboard --confirm '/x'")
    assert capsys.readouterr().err == "\nTo remove, run:\n    agentsync offboard --confirm '/x'\n"


def test_isatty_lives_only_in_clio() -> None:
    callers = [
        str(path.relative_to(SRC))
        for path in sorted(SRC.rglob("*.py"))
        if path.name != "clio.py" and "isatty(" in path.read_text(encoding="utf-8")
    ]
    assert callers == []


def test_unprintable_re_is_the_vtt_class() -> None:
    assert text.UNPRINTABLE_RE.pattern == VTT_CLASS_AT_D62AC148
