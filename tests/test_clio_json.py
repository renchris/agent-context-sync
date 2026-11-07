"""The JSON documents, the consent helper and the bug report in ``agentsync.clio``."""

from __future__ import annotations

import json
import os
import pty
import sys
from collections.abc import Iterator

import pytest

from agentsync import clio, it_request, text

PURGE = {
    "tier": "severe",
    "effects": ["delete 3 files"],
    "name": "docs",
    "argv": ["agentsync", "purge", "--yes"],
    "consequence": "This cannot be undone.",
}


@pytest.fixture(autouse=True)
def _human_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both flags are per process and these tests set them; monkeypatch puts them back. A runner sets CI."""
    monkeypatch.setattr(clio, "_json_mode", False)
    monkeypatch.setattr(clio, "_stdout_closed", False)
    for name in ("CI", "SSH_CONNECTION", "SSH_TTY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def terminal_stdin(monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    """A real terminal behind ``sys.stdin``; the test types into the fd this yields."""
    master, slave = pty.openpty()
    with os.fdopen(slave, "r", encoding="utf-8") as stream:
        monkeypatch.setattr(sys, "stdin", stream)
        yield master
    os.close(master)


class _NobodyMayRead:
    """A terminal stdin whose read fails the test instead of waiting for an answer that never comes."""

    def isatty(self) -> bool:
        return True

    def readline(self) -> str:
        raise AssertionError("confirm read stdin")


def test_json_mode_mutes_human_lines(capsys: pytest.CaptureFixture[str]) -> None:
    clio.set_json(True)
    assert clio.json_mode() is True
    clio.result("x")
    clio.say("y")
    lines: list[str] = []
    clio.table(("kind",), [("a",)])
    clio.table(("kind",), [("a",)], to=lines.append)
    clio.emit({"a": 1})
    captured = capsys.readouterr()
    assert captured.out == '{"a":1}\n'
    assert captured.err == ""
    assert lines == []

    clio.set_json(False)
    assert clio.json_mode() is False
    clio.result("x")
    assert capsys.readouterr().out == "x\n"


def test_error_doc_is_one_line(capsys: pytest.CaptureFixture[str]) -> None:
    next_step = [{"command": "c", "argv": ["c"]}]
    clio.error_doc(clio.Reason.config_invalid, "m", next=next_step, data={"k": "v"})
    assert capsys.readouterr().err == ""  # human mode says it in words, through say

    clio.set_json(True)
    clio.error_doc(clio.Reason.config_invalid, "m\nsecond", next=next_step, data={"k": "v"})
    captured = capsys.readouterr()
    assert len(captured.err.splitlines()) == 1
    assert captured.out == ""
    assert json.loads(captured.err)["message"] == "m\nsecond"

    clio.error_doc(clio.Reason.config_invalid, "m", next=next_step, data={"k": "v"})
    assert json.loads(capsys.readouterr().err) == {
        "status": "error",
        "reason": "config_invalid",
        "message": "m",
        "next": [{"command": "c", "argv": ["c"]}],
        "data": {"k": "v"},
    }

    clio.error_doc(
        clio.Reason.rate_limited,
        "m",
        retryable=False,
        retry_after=0,
        request_id="r",
        warnings=[{"code": "w"}],
    )
    assert json.loads(capsys.readouterr().err) == {
        "status": "error",
        "reason": "rate_limited",
        "message": "m",
        "retryable": False,
        "retryAfter": 0,
        "requestId": "r",
        "warnings": [{"code": "w"}],
    }


def test_emit_is_pretty_on_a_tty_and_compact_off_it(monkeypatch: pytest.MonkeyPatch) -> None:
    master, slave = pty.openpty()
    try:
        with os.fdopen(slave, "w", encoding="utf-8") as stream:
            monkeypatch.setattr(sys, "stdout", stream)
            clio.emit({"a": 1})
            shown = os.read(master, 4096).decode("utf-8").replace("\r\n", "\n")
    finally:
        os.close(master)
    assert shown == '{\n  "a": 1\n}\n'

    read_end, write_end = os.pipe()
    with os.fdopen(write_end, "w", encoding="utf-8") as stream:
        monkeypatch.setattr(sys, "stdout", stream)
        clio.emit({"a": 1, "b": "\u202e"})
    with os.fdopen(read_end, encoding="utf-8") as piped:
        assert piped.read() == '{"a":1,"b":"\\u202e"}\n'


def test_action_required_carries_user_action(capsys: pytest.CaptureFixture[str]) -> None:
    clio.set_json(True)
    clio.error_doc(clio.Reason.login_required, "m", status="action_required", user_action_required=True)
    doc = json.loads(capsys.readouterr().err)
    assert doc["status"] == "action_required"
    assert doc["userActionRequired"] is True
    assert "retryable" not in doc


def test_canceled_doc_carries_remote_state(capsys: pytest.CaptureFixture[str]) -> None:
    clio.set_json(True)
    clio.error_doc(clio.Reason.canceled, "m", data={"remote": "none"})
    doc = json.loads(capsys.readouterr().err)
    assert doc["reason"] == "canceled"
    assert doc["data"]["remote"] == "none"
    assert "userActionRequired" not in doc


def test_confirm_never_prompts_without_a_terminal(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    read_end, write_end = os.pipe()
    os.write(write_end, b"docs\n")
    os.close(write_end)
    with os.fdopen(read_end, encoding="utf-8") as stream:
        monkeypatch.setattr(sys, "stdin", stream)
        assert clio.confirm(**PURGE, yes=False) is False
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.startswith("delete 3 files\nThis cannot be undone.\n")
        assert captured.err.count("confirmation_required") == 1
        assert captured.err.endswith("run:\n    agentsync purge --yes\n")

        clio.set_json(True)
        assert clio.confirm(**PURGE, yes=False) is False
        assert json.loads(capsys.readouterr().err) == {
            "status": "action_required",
            "reason": "confirmation_required",
            "message": "this run does not prompt, so nothing changed.",
            "next": [
                {
                    "command": "agentsync purge --yes",
                    "argv": ["agentsync", "purge", "--yes"],
                    "when": "Only after a human approves: This cannot be undone.",
                }
            ],
            "data": {"effects": ["delete 3 files"]},
            "userActionRequired": True,
        }
        assert os.read(read_end, 16) == b"docs\n"  # no byte of stdin was taken

    # JSON mode does not ask on a terminal either, and --yes needs no terminal
    monkeypatch.setattr(sys, "stdin", _NobodyMayRead())
    assert clio.confirm(**PURGE, yes=False) is False
    assert clio.confirm(**PURGE, yes=True) is True
    capsys.readouterr()

    with pytest.raises(ValueError, match="needs a name"):
        clio.confirm(**{**PURGE, "name": None}, yes=True)


def test_confirm_defaults_to_no_and_a_wrong_name_names_the_expected_one(
    capsys: pytest.CaptureFixture[str], terminal_stdin: int
) -> None:
    assert clio.interactive() is True  # so each answer below is what decides

    os.write(terminal_stdin, b"\n")
    assert clio.confirm(**{**PURGE, "tier": "destructive"}, yes=False) is False
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "delete 3 files\nThis cannot be undone.\nContinue? (y/N) "

    os.write(terminal_stdin, b"wrong\n")
    assert clio.confirm(**PURGE, noun="folder", yes=False) is False
    err = capsys.readouterr().err
    assert 'To confirm, type the folder name "docs": ' in err
    assert err.endswith('Expected "docs".\n')

    os.write(terminal_stdin, b"Y\n")
    assert clio.confirm(**{**PURGE, "tier": "destructive"}, yes=False) is True
    os.write(terminal_stdin, b"docs\n")
    assert clio.confirm(**PURGE, yes=False) is True


def test_bug_report_leaks_no_message_or_path(capsys: pytest.CaptureFixture[str]) -> None:
    try:
        raise ValueError("/Users/secret")
    except ValueError as exc:
        clio.bug_report(exc, "status", version="1.2.3", issues_url=it_request.REPO_URL)
    err = capsys.readouterr().err
    assert err.startswith("error: Unexpected error in agentsync status. This is a bug in agentsync.\n")
    assert "/Users/secret" not in err
    assert "  Error: ValueError\n" in err
    assert "Version: agentsync 1.2.3, Python " in err
    assert it_request.REPO_URL + "/issues/new" in err

    # The innermost agentsync frame names the place, with the stdlib frame under it skipped
    try:
        text.safe_text(None)  # type: ignore[arg-type]
    except TypeError as exc:
        clio.bug_report(exc, None, version="1.2.3", issues_url=it_request.REPO_URL)
    err = capsys.readouterr().err
    assert err.startswith("error: Unexpected error in agentsync. This is a bug in agentsync.\n")
    assert "  Error: TypeError at agentsync.text:" in err
    assert ".py" not in err
