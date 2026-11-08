"""The CLI's streams and signals (cli-conventions B-4.1): a reader that goes away ends a read with 0 and no
traceback, and SIGTERM or SIGHUP during a sync runs Ctrl-C's cleanup and exits 128 + the signal."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from agentsync import cli, governance, loop
from agentsync import cycle as cycle_mod
from agentsync.config import Config, load_config
from agentsync.loop import NO_NEXT_HINT_ENV
from agentsync.manifest import Manifest
from test_cli import _synced


@pytest.fixture
def initialised(tmp_path: Path, local_source_dir: Path, capsys: pytest.CaptureFixture[str]) -> Config:
    cfg = tmp_path / "ctx" / "sources.toml"
    assert cli.main(["add-source", str(local_source_dir), "--config", str(cfg)]) == cli.EXIT_OK
    capsys.readouterr()
    return load_config(cfg)


def _status(cfg: Path, *, close: str, command: str = "status") -> subprocess.Popen[bytes]:
    """``agentsync status`` with the pipe ``close`` names closed at once; an open stderr is a pipe."""
    env = {k: v for k, v in os.environ.items() if k not in {"CLAUDE_CONFIG_DIR", NO_NEXT_HINT_ENV}}
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentsync", command, "--config", str(cfg)],
        stdout=subprocess.PIPE if close == "stdout" else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=env,
    )
    stream = proc.stdout if close == "stdout" else proc.stderr
    assert stream is not None
    stream.close()
    return proc


def test_a_closed_stdout_ends_status_quietly(initialised: Config) -> None:
    proc = _status(initialised.config_path, close="stdout")
    assert proc.stderr is not None
    err = proc.stderr.read().decode()
    assert proc.wait() == 0, err
    assert "Traceback" not in err and "BrokenPipeError" not in err, err


def test_a_closed_stderr_ends_status_quietly(initialised: Config) -> None:
    """``agentsync status 2>&1 | head -1``: stderr's reader goes too. A healthy status says nothing on
    stderr, so this runs ``doctor``, status under its old name, whose first line is the rename on stderr.
    A traceback could not be read here, but it would end the run with 1."""
    proc = _status(initialised.config_path, close="stderr", command="doctor")
    assert proc.wait() == 0


def _sync_killed_by(
    signum: int,
    initialised: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, str]:
    cfg = _synced(initialised)
    real_manifest = cycle_mod.Manifest
    opened: list[Path] = []

    def stop(db: Path) -> Manifest:  # the sync's second open, the cycle's, under the lock
        opened.append(db)
        if len(opened) == 2:
            os.kill(os.getpid(), signum)
            pytest.fail("the signal did not arrive")
        return real_manifest(db)

    monkeypatch.setattr(cycle_mod, "Manifest", stop)
    capsys.readouterr()
    # a detached gate starts its tests with SIGHUP (and maybe SIGTERM) ignored, which clio.signals keeps
    previous = signal.signal(signum, signal.SIG_DFL)
    try:
        rc = cli.main(["sync", "--config", cfg])
        assert signal.getsignal(signum) == signal.SIG_DFL  # the handler is put back
    finally:
        signal.signal(signum, previous)
    return rc, capsys.readouterr().err


def test_sigterm_during_sync_cleans_up_and_exits_143(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _sync_killed_by(signal.SIGTERM, initialised, monkeypatch, capsys)
    assert rc == 143
    assert "interrupted" in err and "interrupted: Stopped." in err, err
    assert f"    {loop.BIN} status" in err.splitlines(), err
    assert not (initialised.state_paths.root / "agentsync.lock.json").exists()


def test_sighup_exits_129(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, err = _sync_killed_by(signal.SIGHUP, initialised, monkeypatch, capsys)
    assert rc == 129
    assert "interrupted" in err, err
    assert not (initialised.state_paths.root / "agentsync.lock.json").exists()


def test_an_interrupted_purge_says_stopped_not_nothing_changed(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A purge may be part done when it is interrupted, so it never says nothing was changed (R294)."""

    def interrupted(root: Path) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(governance, "carry_round28_record", interrupted)
    assert cli.main(["purge", "id=abc", "--config", str(initialised.config_path)]) == 130
    err = capsys.readouterr().err
    assert "interrupted: Stopped." in err and f"    {loop.BIN} status" in err.splitlines(), err
    assert "Nothing was changed" not in err, err
