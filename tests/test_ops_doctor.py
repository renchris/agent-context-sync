"""agentsync.ops.doctor: every check's pass and fail paths, ordering, crash isolation, and rendering.

Probes that would call other (W1b) modules or launchd are replaced with fakes; git and the bundled pandoc
run for real.
"""

from __future__ import annotations

import dataclasses
import errno
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentsync import cli, gitops
from agentsync.config import Config, parse_config
from agentsync.convert import ocr
from agentsync.model import PassKind, SourceKind, SourceState
from agentsync.ops import doctor, launchd
from agentsync.ops.doctor import CheckResult, Severity, format_results
from agentsync.ops.lock import LockInfo, SingleWriterLock, boot_time, write_heartbeat
from conftest import fails_without_a_fix
from test_ocr import write_fake

GIT = shutil.which("git") or "/usr/bin/git"
REAL_GIT_PATH = doctor._git_path
REAL_IN_LAUNCHD = doctor._in_launchd_job
REAL_CODESIGN_INFO = doctor._codesign_info
REAL_LAUNCHER_CANARY = doctor._launcher_canary
REAL_OCR_STATUS = doctor._ocr_status
REAL_DEVTOOLS_MISSING = doctor._devtools_missing
OCR_READY = "paper-vision revision 2, helper 0.3.0"


@pytest.fixture(autouse=True)
def fakes(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Hermetic defaults: no launchd, no other W1b module, a real absolute git."""
    state: dict[str, object] = {"loaded": set()}
    monkeypatch.setattr(doctor, "_git_path", lambda: Path(GIT))
    monkeypatch.setattr(doctor, "_materialize_policy", lambda: 1)
    monkeypatch.setattr(doctor, "_volume_uuid", lambda p: "11111111-2222-3333-4444-555555555555")
    monkeypatch.setattr(doctor, "_is_loaded", lambda label: label in state["loaded"])  # type: ignore[operator]
    monkeypatch.setattr(
        doctor, "_auth_status", lambda cfg: doctor._AuthProbe(True, "chris@example.com", "keychain")
    )
    monkeypatch.setattr(doctor, "_disk_free", lambda p: 100 * 1024**3)
    monkeypatch.setattr(doctor, "_in_launchd_job", lambda cfg: False)
    monkeypatch.delenv(launchd.LAUNCHER_ENV, raising=False)
    monkeypatch.setattr(doctor, "_find_launcher", lambda: None)

    def no_codesign(path: Path) -> doctor._CodeSignature:
        raise AssertionError(f"reached real codesign: {path}")

    def no_canary(exe: Path, path: Path, timeout_s: float) -> tuple[int, str]:
        raise AssertionError(f"reached a real launcher canary: {path}")

    def no_xcode_select() -> bool:
        raise AssertionError("reached real xcode-select")

    monkeypatch.setattr(doctor, "_codesign_info", no_codesign)
    monkeypatch.setattr(doctor, "_launcher_canary", no_canary)
    monkeypatch.setattr(doctor, "_ocr_status", lambda cfg: ("ready", OCR_READY))
    monkeypatch.setattr(doctor, "_devtools_missing", no_xcode_select)

    def refuse(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"reached real launchctl: {argv}")

    monkeypatch.setattr(launchd, "_run_launchctl", refuse)
    return state


def run_checks(config: Config, *, tcc_canary: bool = True) -> list[CheckResult]:
    """``doctor.run_checks`` as the module names it at this call, so every result of every test below goes
    through conftest's ``_every_fail_names_its_fix``: no FAIL any check here can give lacks a fix."""
    return doctor.run_checks(config, tcc_canary=tcc_canary)


def by_name(results: list[CheckResult]) -> dict[str, CheckResult]:
    names = [r.name for r in results]
    assert len(names) == len(set(names)), f"duplicate check names: {names}"
    return {r.name: r for r in results}


def errors(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if not r.ok and r.severity is Severity.ERROR]


def install_agents(cfg: Config, loaded: set[str]) -> None:
    """Install both agents via a fake launchctl that marks them loaded."""

    def runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        verb = argv[1]
        if verb == "bootstrap":
            loaded.add(plistlib.loads(Path(argv[3]).read_bytes())["Label"])
            return subprocess.CompletedProcess(list(argv), 0, "", "")
        if verb == "print":
            label = argv[2].split("/", 2)[2]
            return subprocess.CompletedProcess(list(argv), 0 if label in loaded else 113, "", "")
        return subprocess.CompletedProcess(list(argv), 3 if verb == "bootout" else 0, "", "")

    for spec in (launchd.poll_spec(cfg), launchd.reconcile_spec(cfg)):
        launchd.install(spec, runner=runner)


# ------------------------------------------------------------------------------------------------ whole run


EXPECTED_ORDER = [
    "python",
    "git",
    "pandoc",
    "ocr",
    "docs_repo.location",
    "docs_repo.git",
    "docs_repo.symlinks",
    "docs_repo.permissions",
    "state_dir",
    "state_dir.files",
    "source.local-fixture.listable",
    "source.local-fixture.sentinel",
    "source.local-fixture.volume",
    "materialise.policy",
    "graph.client_id",
    "disk.state",
    "disk.docs",
    "launcher",
    "launchd.poll",
    "launchd.reconcile",
    "lock",
    "heartbeat.local-fixture",
    "logs",
]


def test_happy_path_has_no_errors_and_fixed_order(sample_config: Config) -> None:
    results = run_checks(sample_config)
    names = [r.name for r in results]
    expected = [
        n for n in EXPECTED_ORDER if not (n == "materialise.policy" and os.uname().sysname != "Darwin")
    ]
    assert names == expected
    assert errors(results) == [], format_results(results)
    r = by_name(results)
    for name in ("launcher", "launchd.poll", "launchd.reconcile"):  # KISS K11a: background sync is optional
        assert not r[name].ok and r[name].severity is Severity.INFO and r[name].fix is None, name
        assert r[name].detail.endswith(" not installed (optional background sync; see docs/deploy)"), name
    assert r["pandoc"].ok and r["pandoc"].detail.startswith("pandoc ")
    assert r["ocr"].ok and r["ocr"].detail == f"on-device OCR is ready: {OCR_READY}"
    assert r["git"].ok and "git version" in r["git"].detail
    assert r["materialise.policy"].detail.startswith("process policy off")
    assert [x.name for x in run_checks(sample_config)] == names, "stable order"


def test_all_ok_once_agents_installed(sample_config: Config, fakes: dict[str, object]) -> None:
    loaded: set[str] = fakes["loaded"]  # type: ignore[assignment]
    install_agents(sample_config, loaded)
    results = run_checks(sample_config)
    bad = [x for x in results if not x.ok]
    assert bad == [], format_results(results)
    assert "loaded (StartInterval 300 s)" in by_name(results)["launchd.poll"].detail


def test_crashing_check_is_isolated(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(p: Path) -> int:
        raise RuntimeError("statfs exploded")

    monkeypatch.setattr(doctor, "_disk_free", boom)
    r = by_name(run_checks(sample_config))
    assert not r["disk"].ok and "RuntimeError: statfs exploded" in r["disk"].detail
    assert "logs" in r and "launchd.poll" in r, "later checks still ran"


# ----------------------------------------------------------------------------------- python/git/pandoc


def test_old_python_fails(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "_python_version", lambda: (3, 10, 9))
    r = by_name(run_checks(sample_config))["python"]
    assert not r.ok and r.severity is Severity.ERROR and "3.10.9" in r.detail


def test_git_broken_shim(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    real_run = doctor._run

    def fake_run(argv: Sequence[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        if argv[1:] == ["--version"] and argv[0] == GIT:
            return subprocess.CompletedProcess(
                list(argv), 1, "", "xcrun: error: invalid active developer path"
            )
        return real_run(argv, timeout)

    monkeypatch.setattr(doctor, "_run", fake_run)
    r = by_name(run_checks(sample_config))["git"]
    assert not r.ok and "xcrun: error" in r.detail and r.fix == "xcode-select --install"


def test_git_missing(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    def missing() -> Path:
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(doctor, "_git_path", missing)
    r = by_name(run_checks(sample_config))["git"]
    assert not r.ok and r.fix == "xcode-select --install"


def test_git_path_falls_back_when_gitops_is_a_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    def stub() -> Path:
        raise NotImplementedError

    monkeypatch.setattr(gitops, "git_executable", stub)
    p = REAL_GIT_PATH()
    assert p.is_absolute() and p.name == "git"
    monkeypatch.setattr(gitops, "git_executable", lambda: Path("/opt/custom/git"))
    assert REAL_GIT_PATH() == Path("/opt/custom/git"), "gitops' resolver wins once implemented"


def test_pandoc_configured_but_missing(sample_config: Config, tmp_path: Path) -> None:
    cfg = dataclasses.replace(
        sample_config, convert=dataclasses.replace(sample_config.convert, pandoc_path=tmp_path / "nope")
    )
    r = by_name(run_checks(cfg))["pandoc"]
    assert not r.ok and "missing or not executable" in r.detail and "pandoc_path" in (r.fix or "")


def test_pandoc_bundled_path_is_absolute(sample_config: Config) -> None:
    p = doctor._pandoc_path(sample_config)
    assert p.is_absolute() and p.name == "pandoc"


# --------------------------------------------------------------------- a check that does not finish

RUN_AGAIN = "agentsync status (run it again as it is)"
RUN_AGAIN_INSTALL = "run the same scripts/install.sh command again as it is (its NEXT line names it)"
COMES_BACK = "; if this line comes back: "
STILL_NO_ANSWER = "report it (the program does not answer on this Mac, and no setup step clears that)"
AGAIN = RUN_AGAIN + COMES_BACK + STILL_NO_ANSWER  # a check with no fix of its own for a broken program
PANDOC_FIX = "uv sync (reinstalls pypandoc_binary) or set [convert] pandoc_path to an absolute pandoc"
OUT_OF_TIME = "the check ran out of time, so it could not say whether anything is wrong"
CRASH_FIX = (
    "agentsync status -v (prints the traceback: a check that crashes is a fault in agentsync to report, "
    "and no setup step clears it)"
)


def _silent(match: Callable[[Sequence[str]], bool]) -> Callable[..., subprocess.CompletedProcess[str]]:
    """A ``doctor._run`` under which a matching command never answers; any other one runs for real."""
    real_run = doctor._run

    def run(argv: Sequence[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        if match(argv):
            raise subprocess.TimeoutExpired(list(argv), timeout)
        return real_run(argv, timeout)

    return run


def test_a_pandoc_that_does_not_answer_in_time_names_the_rerun_and_is_no_crash(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The v9 rehearsal (2026-10-07): the first start of a newly installed pandoc took longer than the
    check's 60 s, and the line read ``check crashed: TimeoutExpired: Command '[...]' timed out after 60
    seconds`` with no fix, under an install.sh NEXT that said each FAIL names one. A check that runs out of
    time now says so, says what is known about the wait, and names the first thing to do: run again. Under
    install.sh that is its own command, which the setup prompt lets an agent run."""
    pandoc = doctor._pandoc_path(sample_config)
    monkeypatch.setattr(doctor, "_run", _silent(lambda argv: argv[0] == str(pandoc)))
    results = run_checks(sample_config)
    r = by_name(results)["pandoc"]
    assert (r.ok, r.severity, r.note) == (False, Severity.ERROR, None)
    assert r.detail == (
        f"{pandoc} --version did not answer within 60s: {OUT_OF_TIME} (the first start of a newly installed "
        "pandoc can take a minute, and later ones take under a second)"
    )
    assert "crashed" not in r.detail and "TimeoutExpired" not in r.detail
    assert r.fix == RUN_AGAIN + COMES_BACK + PANDOC_FIX
    assert_fix_parses(r.fix)
    assert [x.name for x in results if x.name != "materialise.policy"] == [
        n for n in EXPECTED_ORDER if n != "materialise.policy"
    ], "every other check still ran"

    monkeypatch.setenv(doctor.NO_NEXT_HINT_ENV, "1")  # as scripts/install.sh runs status
    r = by_name(run_checks(sample_config))["pandoc"]
    assert r.fix == RUN_AGAIN_INSTALL + COMES_BACK + PANDOC_FIX
    assert format_results([r]).endswith(
        f"later ones take under a second) (fix: {RUN_AGAIN_INSTALL}{COMES_BACK}{PANDOC_FIX})"
    )


def test_a_program_that_never_answers_is_told_what_to_do_when_the_line_comes_back(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of the v9 rehearsal fixes (2026-10-07): a pandoc that never answers gave the same line at
    every install.sh run, and that line's whole fix was to run again, with "it found no fault" and "nothing
    needs changing first". A slow start and a stuck program read the same to the check, so the line claims
    neither, and its fix goes on past "again": pandoc's names the check's own two ways out, and a check
    with no fix of its own says to report the line. Run after run, the line is the same and still says so."""
    monkeypatch.setenv(doctor.NO_NEXT_HINT_ENV, "1")  # as scripts/install.sh runs status
    monkeypatch.setattr(doctor, "_run", _silent(lambda argv: list(argv[1:]) == ["--version"]))
    first, second = (by_name(run_checks(sample_config)) for _ in range(2))
    assert first["pandoc"] == second["pandoc"] and first["git"] == second["git"]
    # The run an agent may make comes first, then the two ways out of a pandoc that is stuck.
    assert first["pandoc"].fix == f"{RUN_AGAIN_INSTALL}{COMES_BACK}{PANDOC_FIX}"
    assert first["git"].fix == f"{RUN_AGAIN_INSTALL}{COMES_BACK}{STILL_NO_ANSWER}"
    for line in (first["pandoc"], first["git"]):
        said = f"{line.detail} (fix: {line.fix})"
        assert "found no fault" not in said and "nothing needs changing" not in said, said
        assert said.count(COMES_BACK) == 1, said


def test_the_pandoc_check_waits_60_seconds(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    """The limit scripts/install.sh's own first start of pandoc stays outside of (it waits 300 s)."""
    seen: list[float] = []
    pandoc = doctor._pandoc_path(sample_config)

    def run(argv: Sequence[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        if argv[0] == str(pandoc):
            seen.append(timeout)
        return subprocess.CompletedProcess(list(argv), 0, "pandoc 9.9\n", "")

    monkeypatch.setattr(doctor, "_run", run)
    assert by_name(run_checks(sample_config))["pandoc"].ok and seen == [60.0]


def test_a_git_that_does_not_answer_in_time_is_handled_the_same_way(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other programs doctor starts had the pandoc check's shape. ``git --version`` is a FAIL that says
    the check ran out of time. ``git ls-files`` in the docs repo is a warn, as its non-zero exit is, and the
    two docs_repo lines above it stand where the whole group used to read "check crashed"."""
    monkeypatch.setattr(doctor, "_run", _silent(lambda argv: list(argv[1:]) == ["--version"]))
    r = by_name(run_checks(sample_config))["git"]
    assert (r.ok, r.severity, r.fix) == (False, Severity.ERROR, AGAIN)
    assert r.detail == f"{GIT} --version did not answer within 30s: {OUT_OF_TIME}"

    monkeypatch.setattr(doctor, "_run", _silent(lambda argv: "ls-files" in argv))
    r = by_name(run_checks(sample_config))
    assert r["docs_repo.location"].ok and r["docs_repo.git"].ok and "docs_repo" not in r
    links = r["docs_repo.symlinks"]
    assert (links.ok, links.severity, links.fix) == (False, Severity.WARN, AGAIN)
    assert links.detail == f"git ls-files did not answer within 120s: {OUT_OF_TIME}"


def test_a_codesign_or_launchctl_that_does_not_answer_in_time_is_handled_the_same_way(
    sample_config: Config, fakes: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """codesign (the launcher's signature) and launchctl (whether a job is loaded) run under limits of
    their own: the launcher line found before stands, and each line says which program did not answer."""
    exe = _launcher(monkeypatch)

    def no_codesign(path: Path) -> doctor._CodeSignature:
        raise subprocess.TimeoutExpired(["/usr/bin/codesign", "--verify", str(path)], 60)

    monkeypatch.setattr(doctor, "_codesign_info", no_codesign)
    r = by_name(run_checks(sample_config))
    assert r["launcher"].ok and str(exe.parents[2]) in r["launcher"].detail
    sig = r["launcher.signature"]
    assert (sig.ok, sig.severity, sig.fix) == (False, Severity.ERROR, AGAIN)
    assert sig.detail == f"codesign did not answer within 60s: {OUT_OF_TIME}"
    assert "launcher.requirement" not in r, "nothing is said about a signature that was not read"

    loaded: set[str] = fakes["loaded"]  # type: ignore[assignment]
    install_agents(sample_config, loaded)

    def no_launchctl(label: str) -> bool:
        raise subprocess.TimeoutExpired(["/bin/launchctl", "print", label], 120)

    monkeypatch.setattr(doctor, "_is_loaded", no_launchctl)
    for name in ("launchd.poll", "launchd.reconcile"):
        job = by_name(run_checks(sample_config))[name]
        assert (job.ok, job.severity, job.fix) == (False, Severity.WARN, AGAIN)
        assert job.detail == f"launchctl did not answer within 120s: {OUT_OF_TIME}"


def test_a_timeout_that_gets_past_its_check_is_still_no_crash(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A program started deeper than a check looks (here behind a probe) still reads as out of time: one
    source's line, a whole group's line, and a line of the integrator's checks in ``agentsync status``."""

    def slow_volume(p: Path) -> str:
        raise subprocess.TimeoutExpired(["/usr/sbin/diskutil", "info", str(p)], 20)

    monkeypatch.setattr(doctor, "_volume_uuid", slow_volume)
    src = by_name(run_checks(sample_config))["source.local-fixture"]
    assert (src.ok, src.severity, src.fix) == (False, Severity.ERROR, AGAIN)
    assert src.detail == f"diskutil did not answer within 20s: {OUT_OF_TIME}"

    def slow_disk(p: Path) -> int:
        raise subprocess.TimeoutExpired("df -k", 5)

    monkeypatch.setattr(doctor, "_disk_free", slow_disk)
    disk = by_name(run_checks(sample_config))["disk"]
    assert (disk.ok, disk.fix) == (False, AGAIN)
    assert disk.detail == f"df -k did not answer within 5s: {OUT_OF_TIME}"

    def slow_policy(config: Config) -> list[CheckResult]:
        raise subprocess.TimeoutExpired(["/usr/bin/git", "remote"], 600)

    monkeypatch.setattr(cli, "_policy_check", slow_policy)
    policy = by_name(cli._extra_checks(sample_config, offline=True))["policy"]
    assert (policy.ok, policy.severity, policy.fix) == (False, Severity.ERROR, AGAIN)
    assert policy.detail == f"git did not answer within 600s: {OUT_OF_TIME}"


def test_a_check_that_crashes_names_the_command_that_prints_its_traceback(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A crash is a fault in agentsync, which nothing on the Mac clears: its line keeps the exception and
    names ``agentsync status -v``. That command parses, and it does print the traceback, for a check of
    doctor's and for one of the integrator's."""

    def boom(p: Path) -> int:
        raise RuntimeError("statfs exploded")

    def bang(config: Config) -> list[CheckResult]:
        raise ValueError("no such table")

    monkeypatch.setattr(doctor, "_disk_free", boom)
    monkeypatch.setattr(cli, "_policy_check", bang)
    disk = by_name(run_checks(sample_config))["disk"]
    assert (disk.ok, disk.severity, disk.fix) == (False, Severity.ERROR, CRASH_FIX)
    assert disk.detail == "check crashed: RuntimeError: statfs exploded"
    policy = by_name(cli._extra_checks(sample_config, offline=True))["policy"]
    assert (policy.detail, policy.fix) == ("check crashed: ValueError: no such table", CRASH_FIX)
    assert_fix_parses(CRASH_FIX)

    capsys.readouterr()
    rc = cli.main(["status", "-v", "--config", str(sample_config.config_path)])
    said = capsys.readouterr()
    assert rc == cli.EXIT_FAILED and f"(fix: {CRASH_FIX})" in said.out
    for error in ('RuntimeError("statfs exploded")', 'ValueError("no such table")'):
        assert "Traceback (most recent call last)" in said.err and f"raise {error}" in said.err, said.err


def test_an_operating_system_error_is_no_crash_and_its_fix_names_the_path(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review of the v9 rehearsal fixes (2026-10-07): the crash fix says "a fault in agentsync to report,
    and no setup step clears it", and every exception got it, an operating-system error too, which the
    person or a later run often does clear. One that gets past its check now reads ``could not read
    <path>: <reason>``; its fix names the path and says to run again, then to report a line that stays.
    ``-v`` still prints where the check stopped. An error with no path keeps its type and the same fix."""
    seen: list[Path] = []

    def no_disk(p: Path) -> int:
        seen.append(p)
        raise OSError(errno.EIO, "Input/output error", str(p))

    def no_network(config: Config) -> list[CheckResult]:
        raise ConnectionResetError(errno.ECONNRESET, "Connection reset by peer")

    monkeypatch.setattr(doctor, "_disk_free", no_disk)
    monkeypatch.setattr(cli, "_policy_check", no_network)
    stays = "if the line stays, report it: agentsync status -v prints where the check stopped"
    disk = by_name(run_checks(sample_config))["disk"]
    assert (disk.ok, disk.severity) == (False, Severity.ERROR)
    assert disk.detail == f"could not read {seen[0]}: Input/output error"
    assert disk.fix == f"check that {seen[0]} is there and can be opened, then run again ({stays})"
    policy = by_name(cli._extra_checks(sample_config, offline=True))["policy"]
    assert policy.detail == "stopped on a system error: ConnectionResetError: Connection reset by peer"
    assert policy.fix == f"run again ({stays})"
    for line in (disk, policy):
        assert "crashed" not in line.detail and "no setup step" not in (line.fix or "")

    capsys.readouterr()
    rc = cli.main(["status", "-v", "--config", str(sample_config.config_path)])
    said = capsys.readouterr()
    assert rc == cli.EXIT_FAILED and f"(fix: {disk.fix})" in said.out
    for error in ("OSError(errno.EIO", "ConnectionResetError(errno.ECONNRESET"):
        assert "Traceback (most recent call last)" in said.err and f"raise {error}" in said.err, said.err


def test_a_git_or_pandoc_that_cannot_be_started_keeps_its_own_fix(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A program macOS will not start raised out of the check too. The line now says which one and keeps
    the check's fix. The bundled pandoc is an Intel program, so on an Apple silicon Mac without Rosetta the
    error is "Bad CPU type in executable": the line says what that means and names both ways out."""
    pandoc = doctor._pandoc_path(sample_config)
    real_run = doctor._run
    error = OSError(errno.ENOEXEC, "Exec format error")

    def run(argv: Sequence[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        if list(argv[1:]) == ["--version"]:
            raise error
        return real_run(argv, timeout)

    monkeypatch.setattr(doctor, "_run", run)
    r = by_name(run_checks(sample_config))
    assert (r["git"].ok, r["git"].fix) == (False, "xcode-select --install")
    assert r["git"].detail == f"{GIT} could not be started: Exec format error"
    assert r["pandoc"].detail == f"{pandoc} could not be started: Exec format error"
    assert r["pandoc"].fix == PANDOC_FIX, "the fix a pandoc that ran out of time names second"

    error = OSError(doctor._EBADARCH, "Bad CPU type in executable")
    r = by_name(run_checks(sample_config))
    assert r["pandoc"].detail == (
        f"{pandoc} could not be started: Bad CPU type in executable (it is an Intel program, which an Apple "
        "silicon Mac runs only with Rosetta)"
    )
    # Installing Rosetta changes the system: the line gives it to the person, and names the command whole.
    # `softwareupdate --help`: --agree-to-license is what lets it run "without user interaction"; without
    # it the command stops at a license question, which a shell with no keyboard cannot answer.
    assert r["pandoc"].fix == (
        "Rosetta is yours to install, not a setup step (IT's on a managed Mac): softwareupdate "
        "--install-rosetta --agree-to-license; or set [convert] pandoc_path to an absolute pandoc built for "
        "this Mac"
    )
    assert doctor._EBADARCH == 86


def test_the_fail_lines_that_named_no_fix_name_one(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Besides a crash, four FAIL lines named no fix: a folder that cannot be listed for a reason with no
    rule of its own, the volume line of a folder that is missing, and the two faults of the download
    policy. Each now says what to do, so install.sh's "each names its fix" holds for them."""
    root = sample_config.sources[0].path

    def io_error(p: Path) -> str | None:
        raise OSError(errno.EIO, "Input/output error")

    with monkeypatch.context() as patch:
        patch.setattr(doctor, "_first_entry", io_error)
        r = by_name(run_checks(sample_config))["source.local-fixture.listable"]
        assert (r.severity, r.detail) == (Severity.ERROR, f"{root}: Input/output error")
        assert r.fix == f"check that {root} opens in Finder"
        cfg, cloud = _cloud(sample_config)
        r = by_name(run_checks(cfg))["source.onedrive.listable"]
        assert r.fix == f"check that {cloud} opens in Finder and that its sync app is running and signed in"

    gone = dataclasses.replace(sample_config.sources[0], path=tmp_path / "gone")
    r = by_name(run_checks(dataclasses.replace(sample_config, sources=(gone,))))
    missing = "fix path in [[source]] id = 'local-fixture', or sign in to the sync client"
    assert (
        r["source.local-fixture.volume"].detail == f"volume UUID not checked: {tmp_path / 'gone'} is missing"
    )
    assert r["source.local-fixture.volume"].fix == r["source.local-fixture.listable"].fix == missing

    if os.uname().sysname != "Darwin":
        return  # the policy is a macOS call

    def no_policy() -> int:
        raise OSError(errno.ENOSYS, "no getiopolicy_np")

    report = (
        "report this line: agentsync cannot use this process's download policy on this macOS, and no setup "
        "step clears that"
    )
    for probe in (no_policy, lambda: 9):
        monkeypatch.setattr(doctor, "_materialize_policy", probe)
        r = by_name(run_checks(sample_config))
        assert (r["materialise.policy"].severity, r["materialise.policy"].fix) == (Severity.ERROR, report)


def test_the_suite_holds_every_fail_to_a_fix(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    """install.sh's NEXT says of the [FAIL] lines that "each names its fix". conftest holds every result of
    every test to it; this is that guard seen to work, on doctor's checks and on the integrator's."""

    def bare(config: Config) -> list[CheckResult]:
        return [CheckResult("made-up", False, "broken, and nobody says what to do")]

    assert fails_without_a_fix(run_checks(sample_config)) == []
    with monkeypatch.context() as patch:
        patch.setattr(doctor, "_CHECKS", (*doctor._CHECKS, ("made-up", bare)))
        with pytest.raises(AssertionError, match=r"\[FAIL\] line\(s\) that name no fix: made-up"):
            run_checks(sample_config)
        patch.setattr(cli, "_policy_check", bare)
        with pytest.raises(AssertionError, match="made-up"):
            cli._extra_checks(sample_config, offline=True)
    warn = CheckResult("made-up", False, "degraded", Severity.WARN)
    assert fails_without_a_fix([warn, CheckResult("fixed", False, "broken", fix="mend it")]) == []


def test_ignored_graph_company_warns_naming_the_line(sample_config: Config) -> None:
    """KISS K15: ``[graph] company`` is accepted but ignored; one WARN names the line to delete."""
    assert "config.graph_company" not in by_name(run_checks(sample_config))
    path = sample_config.config_path
    for line, where in ((7, "line 7 of"), (0, "the [graph] company key in")):
        r = by_name(run_checks(dataclasses.replace(sample_config, graph_company_line=line)))[
            "config.graph_company"
        ]
        assert not r.ok and r.severity is Severity.WARN and "ignored" in r.detail
        assert r.fix == f"delete {where} {path}"


# ------------------------------------------------------------------------------------------------ docs_repo


def test_docs_repo_in_cloudstorage(sample_config: Config) -> None:
    cloud = Path.home() / "Library" / "CloudStorage" / "OneDrive-Test" / "docs"  # tmp HOME: never real
    r = by_name(run_checks(dataclasses.replace(sample_config, docs_repo=cloud)))
    assert not r["docs_repo.location"].ok and "CloudStorage" in r["docs_repo.location"].detail


def test_docs_repo_symlink_into_cloudstorage(sample_config: Config, tmp_path: Path) -> None:
    cloud = Path.home() / "Library" / "CloudStorage" / "OneDrive-Test" / "docs"
    cloud.mkdir(parents=True)
    link = tmp_path / "docs-link"
    link.symlink_to(cloud)
    r = by_name(run_checks(dataclasses.replace(sample_config, docs_repo=link)))
    assert not r["docs_repo.location"].ok
    assert not r["docs_repo.symlinks"].ok and "is a symlink" in r["docs_repo.symlinks"].detail


def test_docs_repo_missing_but_creatable(sample_config: Config, tmp_path: Path) -> None:
    r = by_name(run_checks(dataclasses.replace(sample_config, docs_repo=tmp_path / "later" / "docs")))
    assert r["docs_repo.git"].ok and "does not exist yet" in r["docs_repo.git"].detail


def test_docs_repo_not_git_yet(sample_config: Config, tmp_path: Path) -> None:
    plain = tmp_path / "plain-docs"
    plain.mkdir()
    r = by_name(run_checks(dataclasses.replace(sample_config, docs_repo=plain)))
    assert r["docs_repo.git"].ok and "git init" in r["docs_repo.git"].detail


def test_docs_repo_is_a_file(sample_config: Config, tmp_path: Path) -> None:
    f = tmp_path / "docs-file"
    f.write_text("x")
    r = by_name(run_checks(dataclasses.replace(sample_config, docs_repo=f)))
    assert not r["docs_repo.git"].ok


def test_docs_repo_tracked_symlink(sample_config: Config) -> None:
    repo = sample_config.docs_repo
    (repo / "real.md").write_text("hi\n")
    (repo / "link.md").symlink_to("real.md")
    subprocess.run([GIT, "-C", str(repo), "add", "-A"], check=True)
    subprocess.run([GIT, "-C", str(repo), "commit", "-qm", "x"], check=True)
    r = by_name(run_checks(sample_config))["docs_repo.symlinks"]
    assert not r.ok and "link.md" in r.detail and "1 tracked symlink" in r.detail


# ------------------------------------------------------------------------------------------------ state_dir


def test_state_dir_loose_mode(sample_config: Config) -> None:
    sample_config.state_dir.chmod(0o755)
    r = by_name(run_checks(sample_config))["state_dir"]
    assert not r.ok and "0755" in r.detail and r.fix == f"chmod 700 '{sample_config.state_dir}'"


def test_state_dir_missing(sample_config: Config, tmp_path: Path) -> None:
    cfg = dataclasses.replace(sample_config, state_dir=tmp_path / "no-state")
    r = by_name(run_checks(cfg))
    assert not r["state_dir"].ok and r["state_dir"].severity is Severity.WARN
    assert "mkdir -m 700" in (r["state_dir"].fix or "")
    assert "state_dir.files" not in r


def test_state_db_modes(sample_config: Config) -> None:
    db = sample_config.state_paths.db
    db.write_bytes(b"")
    db.chmod(0o644)
    r = by_name(run_checks(sample_config))["state_dir.files"]
    assert not r.ok and "manifest.sqlite (0644)" in r.detail and "chmod 600" in (r.fix or "")
    db.chmod(0o600)
    r = by_name(run_checks(sample_config))["state_dir.files"]
    assert r.ok and "manifest.sqlite" in r.detail


# ------------------------------------------------------------------------------------------------ sources


def test_source_tcc_denied(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    def eperm(p: Path) -> str | None:
        raise PermissionError(errno.EPERM, "Operation not permitted", str(p))

    monkeypatch.setattr(doctor, "_first_entry", eperm)
    monkeypatch.setattr(
        doctor,
        "_process_image",
        lambda: Path(
            "/Library/Frameworks/Python.framework/Versions/3.11/Resources/Python.app/Contents/MacOS/Python"
        ),
    )
    r = by_name(run_checks(sample_config))["source.local-fixture.listable"]
    assert not r.ok and "TCC" in r.detail
    assert r.fix is not None
    assert (
        "Full Disk Access to /Library/Frameworks/Python.framework/Versions/3.11/Resources/Python.app "
        in r.fix
    )


def test_source_posix_permission_denied(sample_config: Config) -> None:
    src = sample_config.sources[0].path
    assert src is not None
    src.chmod(0o000)
    try:
        r = by_name(run_checks(sample_config))
    finally:
        src.chmod(0o755)
    assert not r["source.local-fixture.listable"].ok
    if os.geteuid() != 0:
        assert "permission denied" in r["source.local-fixture.listable"].detail
        assert not r["source.local-fixture.sentinel"].ok


def test_source_missing(sample_config: Config, tmp_path: Path) -> None:
    src = dataclasses.replace(sample_config.sources[0], path=tmp_path / "gone")
    r = by_name(run_checks(dataclasses.replace(sample_config, sources=(src,))))
    assert (
        not r["source.local-fixture.listable"].ok
        and "does not exist" in r["source.local-fixture.listable"].detail
    )
    assert not r["source.local-fixture.volume"].ok


def test_source_empty_dir_warns(sample_config: Config, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    src = dataclasses.replace(sample_config.sources[0], path=empty, sentinel=None)
    r = by_name(run_checks(dataclasses.replace(sample_config, sources=(src,))))
    assert r["source.local-fixture.listable"].severity is Severity.WARN
    assert r["source.local-fixture.sentinel"].ok


def test_empty_inbox_is_ok_unless_it_is_a_cloud_folder(sample_config: Config, tmp_path: Path) -> None:
    """KISS K05: every config has an inbox and it is empty until a file is dropped in, so an empty local
    inbox is OK; an empty inbox inside CloudStorage still warns (it may be an unenumerated folder)."""
    local = tmp_path / "inbox"
    local.mkdir()
    cloud = Path.home() / "Library" / "CloudStorage" / "OneDrive-Test" / "Inbox"
    cloud.mkdir(parents=True)
    for root, ok in ((local, True), (cloud, False)):
        src = dataclasses.replace(sample_config.sources[0], kind=SourceKind.INBOX, path=root, sentinel=None)
        listable = by_name(run_checks(dataclasses.replace(sample_config, sources=(src,))))[
            "source.local-fixture.listable"
        ]
        assert listable.ok is ok, root
        assert ("drop files you save by hand here" in listable.detail) is ok, listable.detail


def test_source_sentinel_missing(sample_config: Config) -> None:
    src = dataclasses.replace(sample_config.sources[0], sentinel="NOT-THERE.txt")
    r = by_name(run_checks(dataclasses.replace(sample_config, sources=(src,))))[
        "source.local-fixture.sentinel"
    ]
    assert not r.ok and r.severity is Severity.ERROR and "incomplete" in r.detail


def test_a_sentinel_the_system_will_not_read_is_its_own_line(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Review of the v9 rehearsal fixes (2026-10-07): the sentinel read knew two answers, "missing" and
    "not permitted". Any other one left the check, and the source's lines became a single ``check crashed``
    whose fix said no setup step clears it; the listable line, with the fix that does, went with them. A
    source whose path is a file, and a cloud folder whose sync app stopped answering, now get a line per
    part, each with a fix that names the folder."""
    afile = tmp_path / "afile"
    afile.write_text("not a folder\n", encoding="utf-8")
    src = dataclasses.replace(sample_config.sources[0], path=afile, sentinel=".keep")
    r = by_name(run_checks(dataclasses.replace(sample_config, sources=(src,))))
    assert "source.local-fixture" not in r, "no one line for the whole source: each part answered"
    listable = r["source.local-fixture.listable"]
    assert listable.detail == f"{afile} is not a directory"
    assert listable.fix == "point [[source]] id = 'local-fixture' path at a folder"
    sentinel = r["source.local-fixture.sentinel"]
    assert (sentinel.ok, sentinel.severity) == (False, Severity.ERROR)
    assert sentinel.detail == f"{afile / '.keep'}: Not a directory"
    assert sentinel.fix == f"check that {afile} opens in Finder"
    assert r["source.local-fixture.volume"].ok, "the file is there: its volume is read"

    cfg, cloud = _cloud(sample_config)
    cfg = dataclasses.replace(
        cfg, sources=(cfg.sources[0], dataclasses.replace(cfg.sources[1], sentinel=".keep"))
    )

    def dead(real: Callable[..., os.stat_result]) -> Callable[..., os.stat_result]:
        def read(self: Path, **kwargs: bool) -> os.stat_result:
            if self == cloud or cloud in self.parents:
                raise OSError(errno.ETIMEDOUT, "Operation timed out", str(self))
            return real(self, **kwargs)

        return read

    def no_answer(p: Path) -> str:
        raise OSError(errno.ETIMEDOUT, "Operation timed out", str(p))

    real_first_entry = doctor._first_entry
    monkeypatch.setattr(Path, "stat", dead(Path.stat))
    monkeypatch.setattr(Path, "lstat", dead(Path.lstat))
    monkeypatch.setattr(doctor, "_first_entry", lambda p: no_answer(p) if p == cloud else real_first_entry(p))
    monkeypatch.setattr(doctor, "_volume_uuid", no_answer)
    r = by_name(run_checks(cfg))
    assert "source.onedrive" not in r
    look = f"check that {cloud} opens in Finder and that its sync app is running and signed in"
    assert (r["source.onedrive.listable"].detail, r["source.onedrive.listable"].fix) == (
        f"{cloud}: Operation timed out",
        look,
    )
    assert (r["source.onedrive.sentinel"].detail, r["source.onedrive.sentinel"].fix) == (
        f"{cloud / '.keep'}: Operation timed out",
        look,
    )
    volume = r["source.onedrive.volume"]
    assert "is missing" not in volume.detail, "a folder that cannot be read is not one that is gone"
    assert volume.detail.startswith(f"File Provider root UUID unreadable for {cloud}: ")
    assert volume.fix == "check the volume is mounted (diskutil info <mount>)"


def test_cloud_source_without_sentinel_and_tcc_note(sample_config: Config) -> None:
    root = Path.home() / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    root.mkdir(parents=True)
    (root / "a.docx").write_bytes(b"x")
    src = dataclasses.replace(sample_config.sources[0], path=root, sentinel=None)
    r = by_name(run_checks(dataclasses.replace(sample_config, sources=(src,))))
    # Bring-back S9: add-source writes no sentinel, and the walk already holds deletions for a cloud folder
    # it finds empty or cannot list, so the missing sentinel is an ok line, not a warn with a hand edit.
    sentinel = r["source.local-fixture.sentinel"]
    assert sentinel.ok and sentinel.fix is None and "optional" in sentinel.detail
    assert "File Provider root UUID" in r["source.local-fixture.volume"].detail
    # Bring-back S21: an [ok] line is a note, never the Full Disk Access instruction, and points at lines
    # that exist in every branch.
    assert r["tcc"].ok and "needs its own" in r["tcc"].detail
    assert "grant Full Disk Access" not in r["tcc"].detail and "tcc.<source>" not in r["tcc"].detail
    assert r["tcc"].detail.endswith("(the launcher and tcc.* lines below report it)")


def test_cloud_source_empty_hints_fda(sample_config: Config) -> None:
    root = Path.home() / "Library" / "CloudStorage" / "OneDrive-Test" / "Empty"
    root.mkdir(parents=True)
    src = dataclasses.replace(sample_config.sources[0], path=root, sentinel=None)
    r = by_name(run_checks(dataclasses.replace(sample_config, sources=(src,))))[
        "source.local-fixture.listable"
    ]
    assert not r.ok and "not been enumerated" in r.detail and "Full Disk Access" in (r.fix or "")


def test_cloud_source_listing_times_out_with_click_allow_hint(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Field report N8: macOS holds a cloud listing until someone clicks Allow. doctor stops waiting at its
    time limit with a blocking FAIL (like EPERM, never "empty") and skips the reads that would wait too."""
    root = Path.home() / "Library" / "CloudStorage" / "OneDrive-Test" / "Held"
    root.mkdir(parents=True)
    (root / "a.docx").write_bytes(b"x")
    release = threading.Event()

    def held(p: Path) -> str | None:
        release.wait(5.0)
        return "a.docx"

    monkeypatch.setattr(doctor, "_first_entry", held)
    monkeypatch.setattr(doctor, "_LISTING_TIMEOUT_S", 0.2, raising=False)
    monkeypatch.setattr(
        doctor, "_volume_uuid", lambda p: pytest.fail("read the volume behind a held listing")
    )
    src = dataclasses.replace(sample_config.sources[0], path=root, sentinel="README.txt")
    try:
        started = time.monotonic()
        r = by_name(run_checks(dataclasses.replace(sample_config, sources=(src,))))
        assert time.monotonic() - started < 4.0
    finally:
        release.set()
    listable = r["source.local-fixture.listable"]
    assert not listable.ok and listable.severity is Severity.ERROR, listable
    assert "did not return" in listable.detail and "click Allow" in (listable.fix or "")
    assert "source.local-fixture.sentinel" not in r and "source.local-fixture.volume" not in r


def test_tcc_note_inside_launchd(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path.home() / "Library" / "CloudStorage" / "OneDrive-Test" / "P"
    root.mkdir(parents=True)
    monkeypatch.setattr(doctor, "_in_launchd_job", lambda cfg: True)
    src = dataclasses.replace(sample_config.sources[0], path=root, sentinel=None)
    r = by_name(run_checks(dataclasses.replace(sample_config, sources=(src,))))
    assert "authoritative" in r["tcc"].detail


def test_in_launchd_job_detects_xpc_service(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XPC_SERVICE_NAME", "com.agentsync.poll")
    assert REAL_IN_LAUNCHD(sample_config)
    monkeypatch.setenv("XPC_SERVICE_NAME", "application.com.apple.Terminal.123")
    assert not REAL_IN_LAUNCHD(sample_config)


def test_paused_source_not_checked(sample_config: Config) -> None:
    src = dataclasses.replace(sample_config.sources[0], state=SourceState.PAUSED)
    r = by_name(run_checks(dataclasses.replace(sample_config, sources=(src,))))
    assert r["source.local-fixture"].ok and "paused" in r["source.local-fixture"].detail
    assert "heartbeat.local-fixture" not in r


def test_volume_uuid_failures(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    def oserr(p: Path) -> str:
        raise OSError(errno.EINVAL, "getattrlist failed")

    monkeypatch.setattr(doctor, "_volume_uuid", oserr)
    r = by_name(run_checks(sample_config))["source.local-fixture.volume"]
    assert not r.ok and r.severity is Severity.ERROR and "stable ids" in r.detail

    def stub(p: Path) -> str:
        raise NotImplementedError

    monkeypatch.setattr(doctor, "_volume_uuid", stub)
    r = by_name(run_checks(sample_config))["source.local-fixture.volume"]
    assert not r.ok and r.severity is Severity.WARN


def test_fda_target_prefers_app_bundle() -> None:
    img = Path("/X/Python.framework/Versions/3.11/Resources/Python.app/Contents/MacOS/Python")
    assert doctor._fda_target(img) == Path("/X/Python.framework/Versions/3.11/Resources/Python.app")
    assert doctor._fda_target(Path("/usr/local/bin/python3.11")) == Path("/usr/local/bin/python3.11")


def test_process_image_is_real_file() -> None:
    img = doctor._process_image()
    assert img.is_absolute() and img.exists()


# ------------------------------------------------------------------------------------------------ materialise


def test_materialise_policy_errors(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    if os.uname().sysname != "Darwin":
        pytest.skip("macOS only")

    def oserr() -> int:
        raise OSError(errno.ENOSYS, "no getiopolicy_np")

    monkeypatch.setattr(doctor, "_materialize_policy", oserr)
    r = by_name(run_checks(sample_config))["materialise.policy"]
    assert not r.ok and r.severity is Severity.ERROR
    monkeypatch.setattr(doctor, "_materialize_policy", lambda: 9)
    assert not by_name(run_checks(sample_config))["materialise.policy"].ok
    monkeypatch.setattr(doctor, "_materialize_policy", lambda: 2)
    assert "process policy on" in by_name(run_checks(sample_config))["materialise.policy"].detail


# ------------------------------------------------------------------------------------------------ graph


def graph_config(tmp_path: Path, sample_config: Config, *, live: bool = True) -> Config:
    text = f"""
[agentsync]
docs_repo = "{sample_config.docs_repo}"
state_dir = "{sample_config.state_dir}"
cache_dir = "{tmp_path / "cache"}"
log_dir = "{sample_config.log_dir}"

[graph]
client_id = "00000000-0000-0000-0000-00000000abcd"
tenant = "contoso.onmicrosoft.com"

[[source]]
id = "od"
kind = "graph_drive"
drive_id = "me"
state = "{"live" if live else "paused"}"
"""
    return parse_config(text, config_path=sample_config.config_path)


def test_graph_signed_in(sample_config: Config, tmp_path: Path) -> None:
    r = by_name(run_checks(graph_config(tmp_path, sample_config)))
    assert r["graph.client_id"].ok and "contoso" in r["graph.client_id"].detail
    assert r["graph.token_cache"].ok and r["graph.signed_in"].ok
    assert "chris@example.com" in r["graph.signed_in"].detail


def test_graph_not_signed_in_with_live_source(
    sample_config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor, "_auth_status", lambda cfg: doctor._AuthProbe(False, None, "file-0600"))
    r = by_name(run_checks(graph_config(tmp_path, sample_config)))
    assert not r["graph.signed_in"].ok and r["graph.signed_in"].severity is Severity.ERROR
    assert r["graph.signed_in"].fix == "agentsync login" and "od" in r["graph.signed_in"].detail
    assert not r["graph.token_cache"].ok and r["graph.token_cache"].severity is Severity.WARN


def test_graph_not_signed_in_paused_source_is_warn(
    sample_config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor, "_auth_status", lambda cfg: doctor._AuthProbe(False, None, "keychain"))
    r = by_name(run_checks(graph_config(tmp_path, sample_config, live=False)))
    assert r["graph.signed_in"].severity is Severity.WARN


def test_graph_auth_probe_errors(
    sample_config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def keychain_locked(cfg: Config) -> doctor._AuthProbe:
        raise RuntimeError("keychain locked")

    monkeypatch.setattr(doctor, "_auth_status", keychain_locked)
    r = by_name(run_checks(graph_config(tmp_path, sample_config)))
    assert not r["graph.auth"].ok and "keychain locked" in r["graph.auth"].detail

    def stub(cfg: Config) -> doctor._AuthProbe:
        raise NotImplementedError

    monkeypatch.setattr(doctor, "_auth_status", stub)
    r = by_name(run_checks(graph_config(tmp_path, sample_config)))
    assert r["graph.auth"].severity is Severity.WARN


# ------------------------------------------------------------------------------------------------ disk


def test_low_disk(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "_disk_free", lambda p: 1024**3)
    r = by_name(run_checks(sample_config))
    assert not r["disk.state"].ok and not r["disk.docs"].ok and "1.0 GiB" in r["disk.state"].detail


# ------------------------------------------------------------------------------------------------ launchd


def test_launchd_installed_not_loaded(sample_config: Config, fakes: dict[str, object]) -> None:
    loaded: set[str] = fakes["loaded"]  # type: ignore[assignment]
    install_agents(sample_config, loaded)
    loaded.clear()
    r = by_name(run_checks(sample_config))["launchd.poll"]
    assert not r.ok and r.severity is Severity.WARN and "not loaded" in r.detail
    assert r.fix is not None and r.fix.startswith(f"launchctl bootstrap gui/{os.getuid()} ")


def test_launchd_plist_drift_and_fail_open(sample_config: Config, fakes: dict[str, object]) -> None:
    loaded: set[str] = fakes["loaded"]  # type: ignore[assignment]
    install_agents(sample_config, loaded)
    path = launchd.plist_path("com.agentsync.poll")
    d = plistlib.loads(path.read_bytes())
    d["StartInterval"] = 60
    path.write_bytes(plistlib.dumps(d))
    r = by_name(run_checks(sample_config))["launchd.poll"]
    assert not r.ok and r.severity is Severity.WARN and "StartInterval" in r.detail
    d["MaterializeDatalessFiles"] = True
    path.write_bytes(plistlib.dumps(d))
    r = by_name(run_checks(sample_config))["launchd.poll"]
    assert not r.ok and r.severity is Severity.ERROR and "fail-closed" in r.detail


def test_launchd_interpreter_gone(sample_config: Config, fakes: dict[str, object], tmp_path: Path) -> None:
    loaded: set[str] = fakes["loaded"]  # type: ignore[assignment]
    install_agents(sample_config, loaded)
    path = launchd.plist_path("com.agentsync.reconcile")
    d = plistlib.loads(path.read_bytes())
    d["ProgramArguments"][0] = str(tmp_path / "deleted-venv" / "bin" / "python")
    path.write_bytes(plistlib.dumps(d))
    r = by_name(run_checks(sample_config))["launchd.reconcile"]
    assert not r.ok and r.severity is Severity.ERROR and "missing" in r.detail
    assert r.fix == "agentsync install-agent"


def _pinned_tool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    """A launcher under the tmp HOME built for ``<tool>/bin/python``, as install.sh builds it, and that
    tool's ``bin``, where ``python3`` is the same file under another name. Returns the two paths."""
    exe = _launcher(monkeypatch)
    tool = tmp_path / "tool" / "bin"
    tool.mkdir(parents=True)
    python = tool / "python"
    python.write_text("#!/bin/sh\nexit 0\n")  # `python -I -c 'import agentsync'` succeeds
    python.chmod(0o755)
    (tool / "python3").symlink_to("python")
    info = {"CFBundleIdentifier": launchd.LAUNCHER_IDENTIFIER, "AgentSyncAllowedProgram": str(python)}
    (exe.parents[1] / "Info.plist").write_bytes(plistlib.dumps(info))
    return python, tool / "python3"


def test_launchd_job_that_names_the_launchers_pin_is_current_for_a_python3_build(
    sample_config: Config, fakes: dict[str, object], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Second bring-back (2026-10-07): both jobs read "installed plist differs (ProgramArguments)" on a
    Mac whose jobs ran fine. They named ``<tool>/bin/python``, which the launcher is built for, and the
    updated tool ran as ``<tool>/bin/python3``, the same file. The warn's fix, ``install-agent``, would
    have written ``python3``, which that launcher refuses. The expected plist now names the pin, so a job
    that names it is current, and the refresh is safe."""
    loaded: set[str] = fakes["loaded"]  # type: ignore[assignment]
    python, python3 = _pinned_tool(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "executable", str(python))  # install.sh's install-agent ran as bin/python
    install_agents(sample_config, loaded)
    installed = plistlib.loads(launchd.plist_path("com.agentsync.poll").read_bytes())["ProgramArguments"]
    assert installed[installed.index("--") + 1] == str(python)
    monkeypatch.setattr(sys, "executable", str(python3))  # the updated tool, as on the field Mac
    results = by_name(run_checks(sample_config))
    for name in ("launchd.poll", "launchd.reconcile"):
        assert results[name].ok and "loaded (StartInterval" in results[name].detail, results[name]


def test_launchd_job_whose_interpreter_the_launcher_refuses_is_a_warn_that_names_exit_64(
    sample_config: Config, fakes: dict[str, object], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A job an earlier build wrote while it ran as ``python3``: its launcher starts only ``python`` and
    refuses the job at every run, exit 64, and nothing said why. Doctor names it. It is a warn, because
    only background sync is down and a FAIL would stop install.sh, and its fix is the refresh, which now
    writes the pin."""
    loaded: set[str] = fakes["loaded"]  # type: ignore[assignment]
    python, python3 = _pinned_tool(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "executable", str(python3))
    install_agents(sample_config, loaded)
    path = launchd.plist_path("com.agentsync.poll")
    d = plistlib.loads(path.read_bytes())
    child = d["ProgramArguments"].index("--") + 1
    assert d["ProgramArguments"][child] == str(python), "this build writes the pin"
    d["ProgramArguments"][child] = str(python3)
    path.write_bytes(plistlib.dumps(d))
    r = by_name(run_checks(sample_config))["launchd.poll"]
    assert not r.ok and r.severity is Severity.WARN, r
    assert (
        f"job interpreter {python3} is not the one its launcher starts ({python}): every run exits 64 "
        "(PROGRAM_REFUSED)" in r.detail
    )
    assert r.fix == "agentsync install-agent"
    assert by_name(run_checks(sample_config))["launchd.reconcile"].ok, "the other job names the pin"
    # Under install.sh with no agent step it is the operator's, like every launchd warn: no fix line.
    monkeypatch.setenv(doctor.NO_NEXT_HINT_ENV, "1")
    r = by_name(run_checks(sample_config))["launchd.poll"]
    assert r.severity is Severity.WARN and r.fix is None and r.note == doctor._AGENT_YOURS_NOTE
    monkeypatch.delenv(doctor.NO_NEXT_HINT_ENV)
    # Run from another environment (a checkout's own venv), the refresh would not write the pin either:
    # the fix is then the installer's own agent step, run from the tool it installs.
    elsewhere = tmp_path / "venv" / "bin"
    elsewhere.mkdir(parents=True)
    (elsewhere / "python").symlink_to(python)
    monkeypatch.setattr(sys, "executable", str(elsewhere / "python"))
    r = by_name(run_checks(sample_config))["launchd.poll"]
    assert r.severity is Severity.WARN and (r.fix or "").startswith(
        "scripts/install.sh --confirm-install-agent"
    )


def test_launchd_unreadable_plist(sample_config: Config) -> None:
    path = launchd.plist_path("com.agentsync.poll")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"garbage")
    r = by_name(run_checks(sample_config))["launchd.poll"]
    assert not r.ok and "not a readable plist" in r.detail


# ------------------------------------------------------------------------------------------------ lock


def test_lock_states(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_path = sample_config.state_paths.lock
    assert by_name(run_checks(sample_config))["lock"].detail == "no lock file yet"
    lk = SingleWriterLock(lock_path, "reconcile")
    lk.acquire()
    try:
        r = by_name(run_checks(sample_config))["lock"]
        assert r.ok and f"pid {os.getpid()} (reconcile)" in r.detail
        lk.beat("convert", now_iso="2000-01-01T00:00:00Z")
        r = by_name(run_checks(sample_config))["lock"]
        assert not r.ok and r.severity is Severity.WARN and "silent for" in r.detail
        assert r.fix == f"if that process is hung: kill {os.getpid()}"
    finally:
        lk.release()
    assert by_name(run_checks(sample_config))["lock"].detail == "free"
    lock_path.write_text(LockInfo(99999, boot_time(), "2026-09-29T10:00:00Z", "poll").render() + "\n")
    r = by_name(run_checks(sample_config))["lock"]
    assert not r.ok and "without releasing" in r.detail and "FULL" in r.detail


# ------------------------------------------------------------------------------------------------ heartbeat


def _beat(cfg: Config, **kw: object) -> None:
    args: dict[str, object] = {
        "run_id": 1,
        "pass_kind": PassKind.FULL,
        "enumeration_complete": True,
        "ok": True,
        "auth_state": "OK",
        "now_iso": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    args.update(kw)
    write_heartbeat(cfg.state_paths.heartbeat, "local-fixture", **args)  # type: ignore[arg-type]


def test_heartbeat_states(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    name = "heartbeat.local-fixture"
    _beat(sample_config)
    assert by_name(run_checks(sample_config))[name].ok

    old = (datetime.now(UTC) - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sample_config.state_paths.heartbeat.unlink()
    _beat(sample_config, now_iso=old)
    r = by_name(run_checks(sample_config))[name]
    assert not r.ok and r.severity is Severity.WARN and "3 x cadence" in r.detail
    assert r.fix == "agentsync sync -v"
    assert_fix_parses(r.fix)

    sample_config.state_paths.heartbeat.unlink()
    for _ in range(3):
        _beat(sample_config, enumeration_complete=False)
    r = by_name(run_checks(sample_config))[name]
    assert not r.ok and "incomplete for 3" in r.detail
    # Bring-back S11: a local walk is always a full pass, so "another sync clears this" was false for it.
    # The loop's WAITING ON YOU line, which status prints above the checks, says what to do.
    assert r.fix == (
        "agentsync status (its WAITING ON YOU line about local-fixture says what stops the listing and what "
        "to do: another sync does not clear it)"
    )
    assert_fix_parses(r.fix)

    _beat(sample_config, ok=False, auth_state="REAUTH_REQUIRED")
    r = by_name(run_checks(sample_config))[name]
    assert not r.ok and r.severity is Severity.ERROR and r.fix == "agentsync login"
    assert_fix_parses(r.fix)

    sample_config.state_paths.heartbeat.unlink()
    _beat(sample_config, ok=False, pass_kind=None)
    r = by_name(run_checks(sample_config))[name]
    assert not r.ok and "no successful pass" in r.detail
    assert r.fix == "agentsync sync -v"
    assert_fix_parses(r.fix)


def test_heartbeat_incomplete_fix_by_source_kind(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bring-back S11: a Graph pass resumes and an inbox is incomplete while a file is being written, so
    sync again is their fix. A local folder points at the loop's WAITING ON YOU line, the one place that
    names the empty cloud folders and weighs what excluding them would retire: no folder name here, and no
    walk of the source. Under install.sh the line is a note, since install.sh prints the loop's line."""
    local = sample_config.sources[0]
    for kind in (SourceKind.GRAPH_DRIVE, SourceKind.INBOX):
        fix = doctor._incomplete_fix(dataclasses.replace(local, id="team-drive", kind=kind))
        assert fix == "agentsync sync -v (a full pass that lists all of team-drive clears this)"
        assert_fix_parses(fix)
    root = Path.home() / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Projects"
    (root / "Bids").mkdir(parents=True)
    (root / "a.docx").write_bytes(b"x")
    cloud = dataclasses.replace(
        sample_config, sources=(dataclasses.replace(local, path=root, sentinel=None),)
    )
    for _ in range(3):
        _beat(sample_config, enumeration_complete=False)
    monkeypatch.setattr(os, "scandir", lambda *a, **k: pytest.fail("the heartbeat check lists no folder"))
    r = by_name(doctor._check_heartbeat(cloud))["heartbeat.local-fixture"]
    assert r.fix is not None and r.fix.startswith("agentsync status (its WAITING ON YOU line about ")
    assert "Bids" not in r.fix and r.note is None
    monkeypatch.setenv(doctor.NO_NEXT_HINT_ENV, "1")
    r = by_name(doctor._check_heartbeat(cloud))["heartbeat.local-fixture"]
    assert not r.ok and r.severity is Severity.WARN and r.fix is None
    assert r.note == "yours: see WAITING ON YOU"
    [line] = format_results([r]).splitlines()
    assert line.endswith("consecutive passes (deletions held) (yours: see WAITING ON YOU)")


def assert_fix_parses(fix: str | None) -> None:
    """A fix naming an agentsync command names one the CLI still parses (KISS K13b: no deleted spelling
    such as ``sync --source`` or ``sync --dry-run``). The text after ``;`` and any parenthetical are prose."""
    assert fix is not None and fix.startswith("agentsync "), fix
    command = re.sub(r"\([^)]*\)", "", fix.split(";")[0]).removeprefix("agentsync ")
    try:
        cli.build_parser().parse_args(shlex.split(command))
    except SystemExit:
        pytest.fail(f"the fix names a command the CLI rejects: {fix}")


def test_assert_fix_parses_rejects_deleted_spellings() -> None:
    for fix in ("agentsync sync --source local-fixture -v", "agentsync sync --dry-run"):
        with pytest.raises(pytest.fail.Exception):
            assert_fix_parses(fix)


def test_heartbeat_corrupt(sample_config: Config) -> None:
    sample_config.state_paths.heartbeat.write_text("{{{")
    r = by_name(run_checks(sample_config))["heartbeat"]
    assert not r.ok and r.severity is Severity.WARN


# ------------------------------------------------------------------------------------------------ logs


def test_big_log_warns(sample_config: Config) -> None:
    sample_config.log_dir.mkdir(parents=True, exist_ok=True)
    big = sample_config.log_dir / "com.agentsync.poll.err.log"
    with big.open("wb") as fh:
        fh.truncate(65 * 1024**2)  # sparse: no real disk use
    (sample_config.log_dir / "small.log").write_text("x")
    r = by_name(run_checks(sample_config))["logs"]
    assert not r.ok and "com.agentsync.poll.err.log (65 MiB)" in r.detail
    assert r.fix == f": > '{big}'"


# ------------------------------------------------------------------------------------------------ format


def test_format_results_alignment_and_tags() -> None:
    results = [
        CheckResult("python", True, "Python 3.11.4"),
        CheckResult("source.x.listable", False, "EPERM", Severity.ERROR, fix="grant FDA"),
        CheckResult("launchd.poll", False, "not installed", Severity.WARN, fix="agentsync install-agent"),
        CheckResult("note", False, "fyi", Severity.INFO),
        CheckResult("ok-with-fix", True, "fine", fix="never shown"),
    ]
    text = format_results(results)
    lines = text.split("\n")
    assert lines == [
        "[ok  ] python            — Python 3.11.4",
        "[FAIL] source.x.listable — EPERM (fix: grant FDA)",
        "[warn] launchd.poll      — not installed (fix: agentsync install-agent)",
        "[info] note              — fyi",
        "[ok  ] ok-with-fix       — fine",
    ]
    assert not text.endswith("\n")
    assert format_results([]) == ""


def test_format_real_run_is_one_line_per_result(sample_config: Config) -> None:
    results = run_checks(sample_config)
    assert len(format_results(results).split("\n")) == len(results)


# ------------------------------------------------------------------------------------------------ launcher

ADHOC = doctor._CodeSignature(
    valid=True,
    verify_detail="",
    identifier="com.agentsync.launcher",
    team_id=None,
    adhoc=True,
    hardened_runtime=True,
    requirement='cdhash H"44e00e724067ed1cf39c5cdee56c6134bde0128b"',
)
DEVELOPER_ID = dataclasses.replace(
    ADHOC,
    team_id="ABCDE12345",
    adhoc=False,
    requirement=(
        'identifier "com.agentsync.launcher" and anchor apple generic and certificate '
        'leaf[subject.OU] = "ABCDE12345"'
    ),
)


def _stray_plist() -> None:
    """A ``com.agentsync.*`` plist under the tmp HOME: a Mac that runs background sync (KISS K11a)."""
    stray = launchd.plist_path("com.agentsync.old-job")
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"garbage")


def _launcher(
    monkeypatch: pytest.MonkeyPatch, sig: doctor._CodeSignature = ADHOC, *, agents: bool = True
) -> Path:
    """Install a stand-in launcher app under the tmp HOME and fake its codesign answer; ``agents`` adds a
    LaunchAgent plist, so the launcher is checked as a requirement."""
    if agents:
        _stray_plist()
    app = Path.home() / "Applications" / launchd.LAUNCHER_BUNDLE
    exe = app / "Contents" / "MacOS" / launchd.LAUNCHER_EXECUTABLE
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    monkeypatch.setattr(doctor, "_find_launcher", launchd.find_launcher)
    monkeypatch.setattr(doctor, "_codesign_info", lambda path: sig)
    return exe


def _cloud(cfg: Config) -> tuple[Config, Path]:
    root = Path.home() / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Projects"
    root.mkdir(parents=True)
    (root / "a.docx").write_bytes(b"x")
    src = dataclasses.replace(cfg.sources[0], id="onedrive", path=root, sentinel=None)
    return dataclasses.replace(cfg, sources=(cfg.sources[0], src)), root


def test_cloud_source_without_launchagents_is_info_only(sample_config: Config) -> None:
    """KISS K11a: a CloudStorage source with no LaunchAgent plist: launcher and launchd are INFO lines with
    no fix (background sync is optional), and status exits 0."""
    cfg, _root = _cloud(sample_config)
    assert launchd.launcher_required(cfg) and not launchd.agents_installed(cfg)
    results = run_checks(cfg)
    r = by_name(results)
    for name in ("launcher", "launchd.poll", "launchd.reconcile"):
        assert not r[name].ok and r[name].severity is Severity.INFO and r[name].fix is None, name
        assert "not installed (optional background sync; see docs/deploy)" in r[name].detail, name
    assert "install-agent" not in format_results(results)
    assert errors(results) == [], format_results(results)  # status exits 1 only on an ERROR line


@pytest.mark.parametrize("sig", [ADHOC, dataclasses.replace(ADHOC, valid=False, verify_detail="not signed")])
def test_present_launcher_without_launchagents_is_info_only(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch, sig: doctor._CodeSignature
) -> None:
    """KISS K11a: a launcher left by install.sh on a Mac with no LaunchAgent plist: its signature and
    requirement lines are INFO with no fix, and no TCC canary runs (nothing runs the launcher)."""
    _launcher(monkeypatch, sig, agents=False)
    cfg, _root = _cloud(sample_config)
    monkeypatch.setattr(doctor, "_launcher_canary", lambda *a: pytest.fail("the canary ran"))
    results = run_checks(cfg)
    r = by_name(results)
    for name in ("launcher.signature", "launcher.requirement"):
        assert not r[name].ok and r[name].severity is Severity.INFO and r[name].fix is None, name
    assert "tcc.onedrive" not in r and r["tcc.canary"].ok and "not run" in r["tcc.canary"].detail
    assert "install-agent" not in format_results(results)
    assert errors(results) == [], format_results(results)


@pytest.mark.parametrize("trigger", ["plist", "pending"])
def test_launcher_missing_but_required_is_an_error(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch, trigger: str
) -> None:
    """With a com.agentsync.* plist present, or install.sh's agent step pending, today's ERROR stands."""
    cfg, _root = _cloud(sample_config)
    if trigger == "plist":
        _stray_plist()
    else:
        monkeypatch.setenv(doctor.AGENT_STEP_PENDING_ENV, "1")
    r = by_name(run_checks(cfg))
    assert not r["launcher"].ok and r["launcher"].severity is Severity.ERROR
    assert "install.sh" in (r["launcher"].fix or "")
    assert not r["launchd.poll"].ok and r["launchd.poll"].severity is Severity.ERROR
    assert "TCC-protected" in r["launchd.poll"].detail
    assert "tcc.onedrive" not in r, "no launcher: no canary"


def test_launcher_adhoc_signature_warns_and_prints_requirement(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C15 requirement 20: doctor prints codesign -d -r- and warns on a cdhash/ad-hoc requirement."""
    exe = _launcher(monkeypatch)
    r = by_name(run_checks(sample_config))
    assert r["launcher"].ok and r["launcher"].detail == str(exe.parents[2])
    sig = r["launcher.signature"]
    assert not sig.ok and sig.severity is Severity.WARN
    assert "com.agentsync.launcher" in sig.detail and "ad hoc" in sig.detail and "hardened" in sig.detail
    assert "Developer ID" in (sig.fix or "")
    req = r["launcher.requirement"]
    assert not req.ok and req.severity is Severity.WARN
    assert req.detail.startswith('designated => cdhash H"44e00e72')
    assert errors(run_checks(sample_config)) == []
    names = [x.name for x in run_checks(sample_config)]
    assert names.index("launcher") < names.index("launcher.signature") < names.index("launchd.poll")


def test_launcher_developer_id_is_ok(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    _launcher(monkeypatch, DEVELOPER_ID)
    r = by_name(run_checks(sample_config))
    assert r["launcher.signature"].ok and "TeamIdentifier ABCDE12345" in r["launcher.signature"].detail
    assert r["launcher.requirement"].ok
    assert "certificate leaf[subject.OU]" in r["launcher.requirement"].detail


def test_launcher_bad_signature_or_identifier(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    _launcher(monkeypatch, dataclasses.replace(ADHOC, valid=False, verify_detail="code object is not signed"))
    r = by_name(run_checks(sample_config))["launcher.signature"]
    assert not r.ok and r.severity is Severity.ERROR and "not signed" in r.detail
    monkeypatch.setattr(
        doctor, "_codesign_info", lambda p: dataclasses.replace(DEVELOPER_ID, identifier="com.other")
    )
    r = by_name(run_checks(sample_config))["launcher.signature"]
    assert not r.ok and r.severity is Severity.WARN and "Info.plist says com.agentsync.launcher" in r.detail


def test_launcher_env_pointing_nowhere(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    _stray_plist()
    monkeypatch.setattr(doctor, "_find_launcher", launchd.find_launcher)
    monkeypatch.setenv(launchd.LAUNCHER_ENV, "/nonexistent/AgentSyncLauncher.app")
    r = by_name(run_checks(sample_config))["launcher"]
    assert not r.ok and r.severity is Severity.ERROR and launchd.LAUNCHER_ENV in (r.fix or "")


def test_launcher_fixes_naming_install_sh_name_confirm_install_agent(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KISS K11b review: plain install.sh builds no launcher, so a launcher fix that names install.sh without
    --confirm-install-agent loops the agent (doctor FAILs, install.sh builds nothing, doctor FAILs again)."""
    monkeypatch.setenv(doctor.AGENT_STEP_PENDING_ENV, "1")
    cfg, _root = _cloud(sample_config)

    def launcher_fixes() -> list[str]:
        return [x.fix or "" for x in run_checks(cfg) if x.name.startswith("launcher") and not x.ok]

    fixes = launcher_fixes()  # missing but required
    _launcher(monkeypatch)
    fixes += launcher_fixes()  # ad hoc
    monkeypatch.setattr(doctor, "_codesign_info", lambda p: dataclasses.replace(ADHOC, valid=False))
    fixes += launcher_fixes()  # invalid signature
    named = [f for f in fixes if "install.sh" in f]
    assert len(named) >= 3, fixes
    assert all("install.sh --confirm-install-agent" in f for f in named), named
    assert any("--confirm-install-agent --launcher <built .app>" in f for f in named), "the Developer ID fix"


@pytest.mark.parametrize(
    ("rc", "ok", "severity", "needle"),
    [
        (0, True, Severity.INFO, "as its own responsible process"),
        (launchd.EXIT_TCC_PENDING, False, Severity.ERROR, "TCC_PENDING"),
        (launchd.EXIT_TCC_DENIED, False, Severity.ERROR, "TCC_DENIED"),
        (launchd.EXIT_CANARY_MISSING, False, Severity.ERROR, "does not exist"),
        (launchd.EXIT_DISCLAIM_UNAVAILABLE, False, Severity.WARN, "disclaim"),
        (74, False, Severity.WARN, "exited 74"),
    ],
)
def test_tcc_canary_through_the_launcher(
    sample_config: Config,
    monkeypatch: pytest.MonkeyPatch,
    rc: int,
    ok: bool,
    severity: Severity,
    needle: str,
) -> None:
    exe = _launcher(monkeypatch)
    cfg, root = _cloud(sample_config)
    calls: list[tuple[Path, Path, float]] = []

    def canary(launcher: Path, path: Path, timeout_s: float) -> tuple[int, str]:
        calls.append((launcher, path, timeout_s))
        return rc, "CANARY_ERROR errno=5" if rc == 74 else ""

    monkeypatch.setattr(doctor, "_launcher_canary", canary)
    r = by_name(run_checks(cfg))
    assert calls == [(exe, root, 15.0)], "one timed canary per protected source, none for the tmp source"
    t = r["tcc.onedrive"]
    assert t.ok is ok and needle in t.detail
    if not ok:
        assert t.severity is severity
    if rc == launchd.EXIT_TCC_PENDING:
        assert "wants to access files managed by “OneDrive”" in t.detail
        assert "click Allow" in (t.fix or "") and "Full Disk Access" in (t.fix or "")
    if rc == launchd.EXIT_TCC_DENIED:
        fix = t.fix or ""
        assert "Full Disk Access" in fix and "tccutil reset All com.agentsync.launcher" in fix
    if rc == launchd.EXIT_DISCLAIM_UNAVAILABLE:  # its own instruction: the ok tcc note no longer carries one
        assert "grant Full Disk Access to" in (t.fix or "") and "AgentSyncLauncher.app" in (t.fix or "")
    assert "AgentSyncLauncher.app" in r["tcc"].detail, "the note names the launcher, not the interpreter"


def test_tcc_canary_false_runs_no_canary(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    """KISS K08a: status decides when the canary is due; when it is not, one ok tcc.canary line instead."""
    _launcher(monkeypatch)
    cfg, _root = _cloud(sample_config)
    monkeypatch.setattr(doctor, "_launcher_canary", lambda *a: pytest.fail("the canary ran"))
    r = by_name(run_checks(cfg, tcc_canary=False))
    assert "tcc.onedrive" not in r and r["tcc.canary"].ok and "not run" in r["tcc.canary"].detail
    assert "tcc.canary" not in by_name(run_checks(sample_config, tcc_canary=False)), "no protected source"


def test_launcher_canary_probe_argv(tmp_path: Path) -> None:
    """The real probe runs the launcher as its own responsible process, canary-only, never a program."""
    log = tmp_path / "argv.txt"
    stub = tmp_path / "agentsync-launcher"
    stub.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" > "{log}"\n'
        'echo "2026-09-29T00:00:00Z agentsync-launcher[1]: TCC_PENDING reason=canary" >&2\nexit 79\n'
    )
    stub.chmod(0o755)
    rc, line = REAL_LAUNCHER_CANARY(stub, tmp_path / "root", 2.0)
    assert rc == 79 and line == "TCC_PENDING reason=canary"
    assert log.read_text().splitlines() == [
        "--self-responsible",
        "--canary-only",
        "--canary-timeout",
        "2.0",
        "--canary",
        str(tmp_path / "root"),
    ]


def test_launcher_canary_probe_hard_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(argv: Sequence[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(list(argv), timeout)

    monkeypatch.setattr(doctor, "_run", hang)
    rc, line = REAL_LAUNCHER_CANARY(tmp_path / "x", tmp_path, 1.0)
    assert rc == launchd.EXIT_TCC_PENDING and "did not return" in line


@pytest.mark.skipif(not Path("/usr/bin/codesign").exists(), reason="macOS only")
def test_codesign_info_reads_a_platform_binary() -> None:
    sig = REAL_CODESIGN_INFO(Path("/bin/ls"))
    assert sig.valid and sig.identifier == "com.apple.ls" and not sig.adhoc
    assert sig.requirement is not None and "anchor apple" in sig.requirement


def test_installed_job_bypassing_the_launcher_is_an_error(
    sample_config: Config, fakes: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded: set[str] = fakes["loaded"]  # type: ignore[assignment]
    install_agents(sample_config, loaded)  # no launcher yet: the plist runs the interpreter
    _launcher(monkeypatch, DEVELOPER_ID)
    r = by_name(run_checks(sample_config))["launchd.poll"]
    assert not r.ok and r.severity is Severity.ERROR and "not the signed launcher" in r.detail
    install_agents(sample_config, loaded)
    assert by_name(run_checks(sample_config))["launchd.poll"].ok


def test_launchd_fix_reads_agent_step_below_while_the_installer_step_is_pending(
    sample_config: Config, fakes: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """install.sh --confirm-install-agent runs doctor before its agent step: no stray install-agent hint
    (K5)."""
    monkeypatch.setenv(doctor.AGENT_STEP_PENDING_ENV, "1")
    results = run_checks(sample_config)
    for name in ("launchd.poll", "launchd.reconcile"):
        r = by_name(results)[name]
        assert not r.ok and r.fix is None and r.note == "installed by the agent step below"
        line = next(ln for ln in format_results(results).splitlines() if f"] {name} " in ln)
        assert "is not installed" in line and "fix:" not in line
        assert line.endswith(" (installed by the agent step below)")
    # installed but not loaded: the bootstrap fix is the agent step's too
    loaded: set[str] = fakes["loaded"]  # type: ignore[assignment]
    install_agents(sample_config, loaded)
    loaded.clear()
    assert by_name(run_checks(sample_config))["launchd.poll"].note == doctor.AGENT_STEP_NOTE
    # any other value, or none: the fix itself
    for value in ("0", ""):
        monkeypatch.setenv(doctor.AGENT_STEP_PENDING_ENV, value)
        r = by_name(run_checks(sample_config))["launchd.poll"]
        assert r.fix is not None and r.fix.startswith("launchctl bootstrap ") and r.note is None
    # a failed check with neither fix nor note renders as before
    plain = CheckResult("x", False, "broken", Severity.WARN)
    assert format_results([plain]) == "[warn] x — broken"


def test_a_launchd_fail_keeps_its_fix_while_the_installer_step_is_pending(
    sample_config: Config, fakes: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A FAIL stops install.sh at its status step, before the agent step. So "installed by the agent step
    below" was false for one, and the line named no fix under a NEXT that says each does: a re-run stopped
    at the same line. A FAIL now keeps its fix there, as it does under AGENTSYNC_NO_NEXT_HINT alone."""
    loaded: set[str] = fakes["loaded"]  # type: ignore[assignment]
    install_agents(sample_config, loaded)
    plist = launchd.plist_path(launchd.poll_spec(sample_config).label)
    data = plistlib.loads(plist.read_bytes())
    data["MaterializeDatalessFiles"] = True
    plist.write_bytes(plistlib.dumps(data))
    monkeypatch.setenv(doctor.AGENT_STEP_PENDING_ENV, "1")
    monkeypatch.setenv(doctor.NO_NEXT_HINT_ENV, "1")  # install.sh sets both
    r = by_name(run_checks(sample_config))["launchd.poll"]
    assert (r.severity, r.fix, r.note) == (Severity.ERROR, "agentsync install-agent", None)
    assert format_results([r]).endswith(" (fix: agentsync install-agent)")


def test_launchd_warn_fix_is_the_operators_under_no_next_hint(
    sample_config: Config, fakes: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bring-back S16: install.sh without --confirm-install-agent on a Mac whose LaunchAgents an earlier
    install left: a launchd.* warn (not loaded, plist differs) is the operator's to refresh, so under
    AGENTSYNC_NO_NEXT_HINT=1 it carries a note and no ``fix:``. A FAIL keeps its fix, and so does every line
    outside install.sh."""
    loaded: set[str] = fakes["loaded"]  # type: ignore[assignment]
    install_agents(sample_config, loaded)
    loaded.clear()
    r = by_name(run_checks(sample_config))["launchd.poll"]
    assert r.severity is Severity.WARN and (r.fix or "").startswith("launchctl bootstrap ") and r.note is None
    monkeypatch.setenv(doctor.NO_NEXT_HINT_ENV, "1")
    results = run_checks(sample_config)
    for name in ("launchd.poll", "launchd.reconcile"):
        r = by_name(results)[name]
        assert (
            not r.ok and r.fix is None and r.note == "background sync is yours to refresh, not a setup step"
        )
        [line] = [ln for ln in format_results(results).splitlines() if f"] {name} " in ln]
        assert "installed but not loaded" in line and "fix:" not in line
    # the agent step's own note wins when install.sh is about to install them
    monkeypatch.setenv(doctor.AGENT_STEP_PENDING_ENV, "1")
    assert by_name(run_checks(sample_config))["launchd.poll"].note == doctor.AGENT_STEP_NOTE
    monkeypatch.delenv(doctor.AGENT_STEP_PENDING_ENV)
    # a FAIL is something to fix before syncing: its fix stays
    plist = launchd.plist_path(launchd.poll_spec(sample_config).label)
    data = plistlib.loads(plist.read_bytes())
    data["MaterializeDatalessFiles"] = True
    plist.write_bytes(plistlib.dumps(data))
    r = by_name(run_checks(sample_config))["launchd.poll"]
    assert r.severity is Severity.ERROR and r.fix == "agentsync install-agent" and r.note is None


def test_adhoc_launcher_fix_is_worded_for_it_under_no_next_hint(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L8 (K5's rest): under install.sh (AGENTSYNC_NO_NEXT_HINT=1) the ad hoc launcher's Developer ID rebuild
    is IT's action, not an instruction to the person or their agent: no ``fix:``, a note for IT."""
    _launcher(monkeypatch)
    monkeypatch.setenv(doctor.NO_NEXT_HINT_ENV, "1")
    results = run_checks(sample_config)
    lines = format_results(results).splitlines()
    for name in ("launcher.signature", "launcher.requirement"):
        r = by_name(results)[name]
        assert not r.ok and r.fix is None and r.note == doctor.ADHOC_IT_NOTE
        [line] = [ln for ln in lines if f"] {name} " in ln]
        assert line.endswith(" (for IT: Developer ID build (docs/deploy/mdm))") and "fix:" not in line
    assert (Path(__file__).parents[1] / "docs" / "deploy" / "mdm").is_dir(), "the note names a real folder"
    assert doctor.NO_NEXT_HINT_ENV == cli.NO_NEXT_HINT_ENV
    for value in ("0", ""):  # anything else: the fix itself, as `agentsync doctor` prints it by hand
        monkeypatch.setenv(doctor.NO_NEXT_HINT_ENV, value)
        r = by_name(run_checks(sample_config))["launcher.signature"]
        assert r.note is None and "SIGN_IDENTITY=" in (r.fix or "")


# ------------------------------------------------------------------------------------------------ ocr


def _real_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real probe on a Mac with OCR switched on (the suite switches it off, see conftest)."""
    monkeypatch.setattr(doctor, "_ocr_status", REAL_OCR_STATUS)
    monkeypatch.delenv("AGENTSYNC_OCR", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")


def test_ocr_ready_and_off_are_ok(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "_ocr_status", lambda cfg: ("off", "[convert] ocr = false"))
    r = by_name(run_checks(sample_config))["ocr"]
    assert r.ok and r.detail == "on-device OCR is off: [convert] ocr = false"


def test_ocr_not_built_is_an_info_line_and_doctor_never_builds(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decision D4: only scripts/install.sh compiles the helper.  With OCR on and nothing built, status
    starts no build and no compiler, writes nothing, and says who builds it.  The one thing it asks is
    whether there are developer tools (``xcode-select -p``, see the next test)."""
    _real_ocr(monkeypatch)
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: False)

    def no_build(*args: object, **kwargs: object) -> None:
        pytest.fail(f"doctor reached the OCR build: {args}")

    for name in ("build", "_compile", "_tool", "_run_helper"):
        monkeypatch.setattr(ocr, name, no_build)
    for results in (run_checks(sample_config), cli._status_checks(sample_config, offline=True)):
        r = by_name(results)["ocr"]
        assert (r.ok, r.severity, r.fix, r.note) == (False, Severity.INFO, None, None)
        assert r.detail == "the OCR helper is not built; scripts/install.sh builds it"
        assert errors([r]) == []
    assert not (sample_config.cache_dir / "ocr").exists()
    line = format_results([r])
    assert line.startswith("[info] ocr") and "fix:" not in line


def test_ocr_not_built_without_developer_tools_says_what_the_build_waits_for(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """scripts/install.sh tries no build on a Mac without developer tools and so leaves no failure to
    report: the state stays not-built.  The line must not send the reader to a script that will build nothing
    again.  It stays INFO with no fix: OCR is optional."""
    monkeypatch.setattr(doctor, "_ocr_status", lambda cfg: ("not-built", "the OCR helper is not built"))
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: True)
    results = run_checks(sample_config)
    r = by_name(results)["ocr"]
    assert (r.ok, r.severity, r.fix, r.note) == (False, Severity.INFO, None, None)
    assert r.detail == (
        "the OCR helper is not built; scripts/install.sh builds it once the Command Line Tools are "
        "installed (xcode-select --install)"
    )
    line = format_results([r])
    assert line.startswith("[info] ocr") and "fix:" not in line and errors(results) == []


def test_ocr_reads_the_helper_under_the_configured_cache_dir(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_ocr(monkeypatch)
    helper = write_fake(ocr._helper_path(sample_config.cache_dir))
    r = by_name(run_checks(sample_config))["ocr"]
    assert r.ok and r.detail == f"on-device OCR is ready: {OCR_READY}"
    assert by_name(run_checks(sample_config))["docs_repo.permissions"].ok, "the helper is owner-only"
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: False)
    write_fake(helper, version_exit=3)  # built, and it stopped answering: OCR is broken, not just not built
    r = by_name(run_checks(sample_config))["ocr"]
    assert (r.ok, r.severity, r.fix) == (False, Severity.WARN, None)
    assert r.detail == "on-device OCR is not working: the OCR helper exited 3: no message"
    helper.unlink()
    ocr._marker(helper).write_text("swiftc did not build the OCR helper (exit 1): error: no such module\n")
    r = by_name(run_checks(sample_config))["ocr"]
    assert (r.ok, r.severity, r.fix) == (False, Severity.WARN, None)
    assert r.detail == (
        "on-device OCR is not working: swiftc did not build the OCR helper (exit 1): error: no such module"
    )


@pytest.mark.parametrize("missing", [True, False])
def test_ocr_failed_is_a_warn_with_a_fix_only_without_developer_tools(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch, missing: bool
) -> None:
    reason = "no Xcode or Command Line Tools (xcode-select -p names no folder)"
    monkeypatch.setattr(doctor, "_ocr_status", lambda cfg: ("failed", reason))
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: missing)
    results = run_checks(sample_config)
    r = by_name(results)["ocr"]
    assert (r.ok, r.severity, r.detail) == (False, Severity.WARN, f"on-device OCR is not working: {reason}")
    assert r.fix == ("xcode-select --install, then run scripts/install.sh again" if missing else None)
    assert errors(results) == [], "OCR is optional: never a FAIL"
    assert "agentsync doctor" not in format_results([r])


def test_ocr_probe_crash_is_a_warn_without_the_exception_text(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(cfg: Config) -> tuple[str, str]:
        raise PermissionError(errno.EACCES, "Permission denied", str(cfg.cache_dir))

    monkeypatch.setattr(doctor, "_ocr_status", crash)
    results = run_checks(sample_config)
    r = by_name(results)["ocr"]
    assert (r.ok, r.severity, r.fix) == (False, Severity.WARN, None)
    assert r.detail == "on-device OCR could not be checked: PermissionError"
    assert errors(results) == []


def test_devtools_missing_asks_xcode_select_by_full_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[tuple[list[str], float]] = []
    answers = iter([(0, f"{tmp_path}\n"), (0, f"{tmp_path / 'gone'}\n"), (2, ""), (0, "\n")])

    def run(argv: Sequence[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        seen.append((list(argv), timeout))
        rc, out = next(answers)
        return subprocess.CompletedProcess(list(argv), rc, out, "")

    monkeypatch.setattr(doctor, "_run", run)
    assert [REAL_DEVTOOLS_MISSING() for _ in range(4)] == [False, True, True, True]
    assert set(map(tuple, (argv for argv, _ in seen))) == {("/usr/bin/xcode-select", "-p")}
    assert all(timeout <= 5.0 for _, timeout in seen)

    def hang(argv: Sequence[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(list(argv), timeout)

    monkeypatch.setattr(doctor, "_run", hang)
    assert REAL_DEVTOOLS_MISSING() is False, "unknown: no fix is better than a wrong one"
