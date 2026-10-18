"""agentsync.ops.doctor: every check's pass and fail paths, ordering, crash isolation, and rendering.

Probes that would call other (W1b) modules or launchd are replaced with fakes; git and the bundled pandoc
run for real.
"""

from __future__ import annotations

import dataclasses
import errno
import fcntl
import importlib.metadata
import inspect
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentsync import cli, gitops, lints, setup_report
from agentsync.config import RETIRE_ADVICE, Config, local_source_table, parse_config
from agentsync.convert import media, ocr, speech
from agentsync.convert import pandoc as pandoc_mod
from agentsync.loop import BIN
from agentsync.manifest import Manifest
from agentsync.model import CycleMode, PassKind, RowState, SourceItem, SourceKind, SourceState, Verdict
from agentsync.ops import doctor, launchd
from agentsync.ops.doctor import CheckResult, Severity, format_results
from agentsync.ops.lock import EMPTY_BODY_HOLDER, LockInfo, SingleWriterLock, boot_time, write_heartbeat
from conftest import config_text, fails_without_a_fix
from media_kit import fake_media
from speech_kit import fake_speech, place_models
from test_ocr import write_fake

GIT = shutil.which("git") or "/usr/bin/git"
REAL_GIT_PATH = doctor._git_path
REAL_IN_LAUNCHD = doctor._in_launchd_job
REAL_CODESIGN_INFO = doctor._codesign_info
REAL_LAUNCHER_CANARY = doctor._launcher_canary
REAL_OCR_STATUS = doctor._ocr_status
REAL_MEDIA_STATUS = doctor._media_status
REAL_SPEECH_STATUS = doctor._speech_status
REAL_DEVTOOLS_MISSING = doctor._devtools_missing
OCR_READY = "paper-vision revision 2, helper 0.3.0"
MEDIA_READY = "paper-media, helper 0.1.0"
SPEECH_READY = "paper-speech, helper 0.1.0, FluidAudio 04e363c"


@pytest.fixture(autouse=True)
def fakes(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Hermetic defaults: no launchd, no other W1b module, a real absolute git."""
    state: dict[str, object] = {"loaded": set()}
    monkeypatch.setattr(doctor, "_git_path", lambda: Path(GIT))
    # the operator's native pandoc is not these tests' (conftest._operator_native_pandoc): their HOME only
    monkeypatch.setattr(pandoc_mod, "_native_pandoc", inspect.unwrap(pandoc_mod._native_pandoc))
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
    monkeypatch.setattr(doctor, "_media_status", lambda cfg: ("ready", MEDIA_READY))
    monkeypatch.setattr(doctor, "_speech_status", lambda cfg: ("ready", SPEECH_READY))
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
    "media",
    "speech",
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
    assert r["media"].ok and r["media"].detail == f"media helper: ready ({MEDIA_READY})"
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


def test_a_probe_a_signal_stopped_is_worded_as_the_converters_word_it(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """subprocess gives a signal's stop as a negative code: git and pandoc read "stopped by SIGKILL (signal
    9)", the phrase of ``ocr._ended``, not "exit -9"; a plain exit keeps its words (B13 round 2 R81)."""
    real_run = doctor._run
    pandoc = str(doctor._pandoc_path(sample_config))
    codes = {GIT: -9, pandoc: -9}

    def fake_run(argv: Sequence[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        if argv[1:] == ["--version"] and argv[0] in codes:
            return subprocess.CompletedProcess(list(argv), codes[argv[0]], "", "a line before the kill")
        return real_run(argv, timeout)

    monkeypatch.setattr(doctor, "_run", fake_run)
    found = by_name(run_checks(sample_config))
    assert found["git"].detail == f"{GIT} --version failed: stopped by SIGKILL (signal 9)"
    assert found["pandoc"].detail == f"{pandoc} --version failed (stopped by SIGKILL (signal 9))"
    codes.update({GIT: 1, pandoc: 2})
    found = by_name(run_checks(sample_config))
    assert found["git"].detail == f"{GIT} --version failed: a line before the kill"
    assert found["pandoc"].detail == f"{pandoc} --version failed (exit 2): a line before the kill"


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


def test_git_line_names_the_git_xcrun_resolved(
    sample_config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The doctor runs the git gitops resolved through xcrun, and its line says where that git is."""
    exe = tmp_path / "CommandLineTools" / "usr" / "bin" / "git"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\necho 'git version 2.50.1 (Apple Git-155)'\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setattr(gitops, "_git", exe)
    monkeypatch.setattr(doctor, "_git_path", REAL_GIT_PATH)
    r = by_name(run_checks(sample_config))["git"]
    assert r.ok and r.detail == f"git version 2.50.1 (Apple Git-155) at {exe}"


def test_pandoc_configured_but_missing(sample_config: Config, tmp_path: Path) -> None:
    cfg = dataclasses.replace(
        sample_config, convert=dataclasses.replace(sample_config.convert, pandoc_path=tmp_path / "nope")
    )
    r = by_name(run_checks(cfg))["pandoc"]
    assert not r.ok and "missing or not executable" in r.detail and "pandoc_path" in (r.fix or "")


def test_pandoc_bundled_path_is_absolute(sample_config: Config) -> None:
    p = doctor._pandoc_path(sample_config)
    assert p.is_absolute() and p.name == "pandoc"


def _native_copy(monkeypatch: pytest.MonkeyPatch, body: str = "echo 'pandoc 3.9'") -> Path:
    """A native arm64 copy of the wheel's pandoc under the test's HOME, as on a Mac where the operator
    installed one: darwin, this venv's wheel mapped to 3.9 (tests/test_pandoc_native.py)."""
    [record] = [
        entry.hash.value
        for entry in importlib.metadata.distribution("pypandoc_binary").files or ()
        if entry.as_posix() == "pypandoc/files/pandoc" and entry.hash is not None
    ]
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(pandoc_mod, "_WHEEL_PANDOC_VERSIONS", {record: "3.9"})
    pandoc_mod._wheel_pandoc_version.cache_clear()
    copy = Path.home() / ".local/share/agentsync/pandoc/3.9-arm64/bin/pandoc"
    copy.parent.mkdir(parents=True)
    copy.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    copy.chmod(0o755)
    return copy


NATIVE_FIX = (
    "replace or remove ~/'.local/share/agentsync/pandoc/3.9-arm64' (the converters run it instead of the "
    "bundled pandoc), or set [convert] pandoc_path to an absolute pandoc"
)


def test_the_pandoc_check_runs_the_native_copy_the_converters_run(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 14 Q56, Q60, Q76: since the converters prefer the operator's native arm64 copy (load root
    cause X26), doctor still ran the wheel's Intel pandoc, so a broken copy read OK while every conversion
    failed, and a Mac without Rosetta read a FAIL while conversions worked. The check resolves pandoc as the
    converters do, and its line says which copy it ran."""
    assert doctor._check_pandoc(sample_config)[0].detail.endswith(" (bundled with pypandoc_binary)")
    copy = _native_copy(monkeypatch)
    try:
        assert doctor._pandoc_path(sample_config).resolve() == pandoc_mod._bundled_pandoc().resolve() == copy
        [r] = doctor._check_pandoc(sample_config)
        assert (r.ok, r.detail) == (True, f"pandoc 3.9 at {copy} (native arm64 copy)")
    finally:
        pandoc_mod._wheel_pandoc_version.cache_clear()


def test_a_native_pandoc_that_fails_names_that_copy_and_not_rosetta(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 14 Q56, Q60: the fix for the operator's copy is that copy, which uv sync does not touch;
    Rosetta, and the slow first start Rosetta's translation makes, are the wheel's binary's alone."""
    copy = _native_copy(monkeypatch, "echo broken >&2; exit 3")
    try:
        [r] = doctor._check_pandoc(sample_config)
        assert (r.ok, r.detail) == (False, f"{copy} (native arm64 copy) --version failed (exit 3): broken")
        assert r.fix == NATIVE_FIX

        real_run = doctor._run
        error = OSError(doctor._EBADARCH, "Bad CPU type in executable")

        def run(argv: Sequence[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
            if argv[0] == str(copy):
                raise error
            return real_run(argv, timeout)

        monkeypatch.setattr(doctor, "_run", run)
        [r] = doctor._check_pandoc(sample_config)
        assert r.detail == f"{copy} (native arm64 copy) could not be started: Bad CPU type in executable"
        assert r.fix == NATIVE_FIX and "Rosetta" not in f"{r.detail} {r.fix}"

        monkeypatch.setattr(doctor, "_run", _silent(lambda argv: argv[0] == str(copy)))
        [r] = doctor._check_pandoc(sample_config)
        assert r.detail == f"{copy} (native arm64 copy) --version did not answer within 60s: {OUT_OF_TIME}"
        assert r.fix == f"{RUN_AGAIN}{COMES_BACK}{NATIVE_FIX}"
    finally:
        pandoc_mod._wheel_pandoc_version.cache_clear()


def test_a_missing_bundled_pandoc_is_the_checks_fail_and_no_crash(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """B13 round 14 Q56: the resolver raises ConversionError when the wheel's binary is missing, which the
    check did not catch; it names the binary in its own missing line, with its own fix."""
    gone = tmp_path / "pypandoc" / "files" / "pandoc"
    monkeypatch.setattr(pandoc_mod, "_wheel_pandoc", lambda: gone)
    [r] = doctor._check_pandoc(sample_config)
    assert (r.ok, r.detail, r.fix) == (False, f"{gone} is missing or not executable", PANDOC_FIX)


# --------------------------------------------------------------------- a check that does not finish

RUN_AGAIN = "~/.local/bin/agentsync status (run it again as it is)"
RUN_AGAIN_INSTALL = "run the same scripts/install.sh command again as it is (its NEXT line names it)"
COMES_BACK = "; if this line comes back: "
STILL_NO_ANSWER = "report it (the program does not answer on this Mac, and no setup step clears that)"
AGAIN = RUN_AGAIN + COMES_BACK + STILL_NO_ANSWER  # a check with no fix of its own for a broken program
PANDOC_FIX = "uv sync (reinstalls pypandoc_binary) or set [convert] pandoc_path to an absolute pandoc"
OUT_OF_TIME = "the check ran out of time, so it could not say whether anything is wrong"
CRASH_FIX = (
    "~/.local/bin/agentsync status -v (prints the traceback: a check that crashes is a fault in agentsync to "
    "report, "
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
    stays = "if the line stays, report it: ~/.local/bin/agentsync status -v prints where the check stopped"
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
    missing = 'fix path in [[source]] id = "local-fixture"'  # no sync client to sign in to (B13 round 11 M10)
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


def _as_the_report_shows(fix: str) -> str:
    """``fix`` after the setup report's redaction of the home folder (``setup_report._build_redactor``)."""
    red = setup_report.Redactor()
    home = Path.home()
    for h in sorted({str(home), str(home.resolve())}, key=len, reverse=True):
        red.add("home", h)
    return red.redact(fix)


def _run_fix(shown: str, work: Path) -> subprocess.CompletedProcess[str]:
    work.mkdir(exist_ok=True)
    return subprocess.run(["/bin/sh", "-c", shown], cwd=work, capture_output=True, text=True, check=False)


def test_a_state_dir_fix_under_home_runs_as_the_report_shows_it(
    sample_config: Config, tmp_path: Path
) -> None:
    """§16.39 N11: the report redacts the home folder to ``~``; inside quotes no shell expands it, so the
    mode fix failed and the mkdir made a folder named ``~`` in the working directory."""
    state = Path.home() / "Library" / "Application Support" / "agentsync"
    cfg = dataclasses.replace(sample_config, state_dir=state)
    work = tmp_path / "work"

    [missing] = doctor._check_state_dir(cfg)
    assert (
        missing.severity is Severity.WARN
        and missing.fix == "mkdir -m 700 -p ~/'Library/Application Support/agentsync'"
    )
    shown = _as_the_report_shows(missing.fix)
    assert shown == missing.fix, "the fix names no home folder, so the report leaves it as it is"
    assert _run_fix(shown, work).returncode == 0
    assert state.is_dir() and state.stat().st_mode & 0o777 == 0o700
    assert not (work / "~").exists()

    state.chmod(0o755)
    loose = doctor._check_state_dir(cfg)[0]
    assert not loose.ok and loose.fix == "chmod 700 ~/'Library/Application Support/agentsync'"
    done = _run_fix(_as_the_report_shows(loose.fix), work)
    assert done.returncode == 0, done.stderr
    assert state.stat().st_mode & 0o777 == 0o700


def test_shell_path_forms(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = Path.home()
    assert doctor._shell_path(home) == "~"
    assert doctor._shell_path(home / "a b" / "c") == "~/'a b/c'"
    assert doctor._shell_path("/Volumes/X y") == "'/Volumes/X y'"
    assert doctor._shell_path(f"{home}x/y") == f"'{home}x/y'", "a sibling whose name starts with the home's"
    # HOME is a symlink: a path spelled either way is below it. Under tmp_path, which pytest resolved.
    real, link = tmp_path / "real", tmp_path / "link"
    real.mkdir()
    link.symlink_to(real)
    monkeypatch.setenv("HOME", f"{link}/")
    assert doctor._shell_path(real / "docs" / "x") == "~/'docs/x'"
    assert doctor._shell_path(link / "docs" / "x") == "~/'docs/x'"
    assert doctor._shell_path(link) == "~"
    # A home folder that does not resolve costs no check its result.
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    monkeypatch.setenv("HOME", str(loop))
    assert doctor._shell_path(loop / "x") == "~/'x'"
    assert doctor._shell_path(tmp_path / "x") == f"'{tmp_path / 'x'}'"
    monkeypatch.setenv("HOME", "/")
    assert doctor._shell_path("/etc/x") == "'/etc/x'"


@pytest.mark.parametrize("shell", ["sh", "bash", "zsh", "dash"])
def test_shell_path_quotes_by_content_and_every_shell_reads_the_path_back(shell: str, tmp_path: Path) -> None:
    """§16.45 R78: a path with a ``'`` ended ``~/'<rest>'`` early. Such a path is ``~/"<rest>"`` when it has
    none of ``!$`\"``, else each ``'`` is spliced; sh, bash, zsh and dash each read every form back as the
    exact path."""
    exe = shutil.which(shell)
    if exe is None:
        pytest.skip(f"{shell} is not installed")
    home = Path.home()
    assert doctor._shell_path(home / "Contoso Docs" / "notes") == "~/'Contoso Docs/notes'"
    assert doctor._shell_path(home / "Contoso's Docs" / "notes") == '~/"Contoso\'s Docs/notes"'
    assert doctor._shell_path(home / "Contoso's $Docs") == "~/'Contoso'\\''s $Docs'"
    paths = [
        home / "Contoso Docs" / "notes",
        home / "Contoso's Docs" / "notes",
        home / "Contoso's $Docs" / "x",
        home / 'Fabrikam\'s "Q" !`a`\\b',
        tmp_path / "Project Alpha's notes",
        tmp_path / "Project Alpha's $notes",
    ]
    argv = [exe, "-f", "-c"] if shell == "zsh" else [exe, "-c"]
    for path in paths:
        form = doctor._shell_path(path)
        done = subprocess.run(
            [*argv, f"printf '%s' {form}"],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(home)},
            check=False,
        )
        assert done.returncode == 0 and done.stdout == str(path), (form, done.stdout, done.stderr)


def test_docs_repo_fixes_quote_the_path(sample_config: Config) -> None:
    """§16.45 R78: the docs repo fixes print the path as one shell word; ``mkdir -p`` then makes that folder,
    not one folder per word."""
    ro = Path.home() / "ro"
    ro.mkdir()
    repo = ro / "Contoso's Docs" / "docs"
    ro.chmod(0o500)
    try:
        r = by_name(doctor._check_docs_repo(dataclasses.replace(sample_config, docs_repo=repo)))[
            "docs_repo.git"
        ]
    finally:
        ro.chmod(0o700)
    assert r.fix == 'mkdir -p ~/"ro/Contoso\'s Docs/docs"'
    done = subprocess.run(["sh", "-c", r.fix], capture_output=True, text=True, cwd=Path.home(), check=False)
    assert done.returncode == 0 and repo.is_dir(), done.stderr
    shutil.rmtree(ro / "Contoso's Docs")
    repo.parent.mkdir()
    repo.write_text("")
    r = by_name(doctor._check_docs_repo(dataclasses.replace(sample_config, docs_repo=repo)))["docs_repo.git"]
    assert r.fix == 'move ~/"ro/Contoso\'s Docs/docs" aside'


def test_the_permissions_fix_under_home_runs_as_the_report_shows_it(tmp_path: Path) -> None:
    """§16.39 N11, the field case (2026-10-06 row 9): the chmod and the git config of a docs repo below the
    home folder, pasted from the report."""
    ctx = Path.home() / "agent-context"
    repo = ctx / "docs"
    repo.mkdir(parents=True)
    ctx.chmod(0o700)
    repo.chmod(0o700)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    page = repo / "mirror" / "x" / "page.md"  # inside mirror/: no sync walks it, so the fix is the chmod
    page.parent.mkdir(parents=True)
    page.write_text("Contoso\n", encoding="utf-8")
    page.chmod(0o644)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    text = config_text(repo, state, tmp_path / "cache", tmp_path / "source")
    cfg = parse_config(text, config_path=ctx / "sources.toml")

    [found] = doctor._check_permissions(cfg)
    assert not found.ok and found.fix is not None
    assert found.fix.startswith("chmod -R go-rwx ~/'agent-context' ~/'agent-context/docs' ")
    assert found.fix.endswith("; git -C ~/'agent-context/docs' config core.sharedRepository 0600")
    assert str(Path.home()) not in found.fix
    work = tmp_path / "work"
    done = _run_fix(_as_the_report_shows(found.fix), work)
    assert done.returncode == 0, done.stderr
    assert page.stat().st_mode & 0o777 == 0o600
    shared = subprocess.run(
        ["git", "-C", str(repo), "config", "core.sharedRepository"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert shared.stdout.strip() == "0600"
    assert not (work / "~").exists()


def test_a_cache_or_log_folder_that_holds_home_is_a_fail_and_no_chmod_target(tmp_path: Path) -> None:
    """§16.45 R79: a cache_dir that is the home folder and a log_dir above it are left out of the walk and of
    the chmod, as the sync leaves them (cycle._holds_home); each is a FAIL whose fix names its config key."""
    ctx = Path.home() / "agent-context"
    repo = ctx / "docs"
    repo.mkdir(parents=True)
    ctx.chmod(0o700)
    repo.chmod(0o700)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    page = repo / "mirror" / "x" / "page.md"
    page.parent.mkdir(parents=True)
    page.write_text("Contoso\n", encoding="utf-8")
    page.chmod(0o644)
    (Path.home() / "Public").mkdir(mode=0o755)  # the person's own: never reported as tenant data
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    cfg = parse_config(
        config_text(repo, state, tmp_path / "cache", tmp_path / "source"), config_path=ctx / "sources.toml"
    )
    cfg = dataclasses.replace(cfg, cache_dir=Path.home(), log_dir=Path.home().parent)
    results = by_name(doctor._check_permissions(cfg))
    assert set(results) == {"config.cache_dir", "config.log_dir", "docs_repo.permissions"}
    for key in ("cache_dir", "log_dir"):
        r = results[f"config.{key}"]
        assert not r.ok and r.severity is Severity.ERROR and r.fix is not None
        assert r.fix.startswith(f"set {key} in ~/'agent-context/sources.toml' to a folder of its own")
        assert "chmod" not in r.fix
    perms = results["docs_repo.permissions"]
    assert not perms.ok and "Public" not in perms.detail and "page.md" in perms.detail
    homes = {str(Path.home()), str(Path.home().parent), "/"}
    for r in results.values():
        words = shlex.split((r.fix or "").replace(";", " "))
        assert "~" not in words and not homes.intersection(words), r.fix
    assert perms.fix is not None and perms.fix.startswith("chmod -R go-rwx ~/'agent-context' ")


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
    local = sample_config.docs_repo.parent / "inbox"
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


def test_only_the_kept_inbox_is_called_the_drop_folder(sample_config: Config, tmp_path: Path) -> None:
    """Field report 2026-10-08: a Mac with inbox sources the person added (a bridge folder another tool
    writes) beside the inbox agentsync keeps. Both are empty and both are OK, but only the kept one, the
    folder ``inbox`` beside the docs repo whatever its id, is where files saved by hand go: the folder the
    guides name (``publish``'s inbox line)."""
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    kept = sample_config.docs_repo.parent / "inbox"
    kept.mkdir()
    first = sample_config.sources[0]
    sources = (
        dataclasses.replace(first, id="bridge", kind=SourceKind.INBOX, path=bridge, sentinel=None),
        dataclasses.replace(first, id="inbox-2", kind=SourceKind.INBOX, path=kept, sentinel=None),
    )
    r = by_name(run_checks(dataclasses.replace(sample_config, sources=sources)))
    assert r["source.bridge.listable"].ok and r["source.inbox-2.listable"].ok
    assert r["source.bridge.listable"].detail == f"{bridge} is empty"
    assert r["source.inbox-2.listable"].detail == f"{kept} is empty (drop files you save by hand here)"


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
    assert listable.fix == 'point [[source]] id = "local-fixture" path at a folder'
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
    assert volume.detail.startswith(f"volume UUID unreadable for {cloud}: ")
    assert volume.fix == "check the volume is mounted (diskutil info <mount>)"


def test_cloud_source_without_sentinel_and_tcc_note(sample_config: Config) -> None:
    root = Path.home() / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    root.mkdir(parents=True)
    (root / "a.docx").write_bytes(b"x")
    src = dataclasses.replace(sample_config.sources[0], path=root, sentinel=None)
    r = by_name(run_checks(dataclasses.replace(sample_config, sources=(src,))))
    # Bring-back S9: add-source writes no sentinel, and the walk already holds back deletions for a cloud
    # folder it finds empty or cannot list, so the missing sentinel is an ok line, not a warn to edit by hand.
    sentinel = r["source.local-fixture.sentinel"]
    assert sentinel.ok and sentinel.fix is None and "optional" in sentinel.detail
    # B13 round 5 F3: agentsync holds the deletions back, not the folder (the provider folder reads as a
    # File Provider domain here, as conftest makes every test's).
    assert sentinel.detail == (
        "no sentinel configured (optional: agentsync already holds back deletions under a cloud folder that "
        "is empty or cannot be listed)"
    )
    # B13 round 4 Q2: the value is the APFS volume's UUID, whatever the folder is, so it is named as one.
    volume = r["source.local-fixture.volume"].detail
    assert volume.startswith("volume UUID ") and "File Provider" not in volume
    # Bring-back S21: an [ok] line is a note, never the Full Disk Access instruction. B13 round 5 F2: with no
    # LaunchAgent installed or pending, it names none as running, nor this interpreter: a cloud root needs the
    # signed launcher.
    tcc = r["tcc"].detail
    assert r["tcc"].ok and "grant Full Disk Access" not in tcc and "tcc.<source>" not in tcc
    assert "the LaunchAgent runs" not in tcc and "python" not in tcc.lower(), tcc
    assert tcc == (
        "cloud roots were listed with this terminal's privacy grants; background sync is not installed: if "
        "it is turned on, its LaunchAgents run the signed launcher (agentsync-launcher), which needs its own "
        "grants"
    )


def test_the_tcc_note_names_the_lines_below_when_background_sync_is_wanted(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 5 F2: with the agent step pending (or a LaunchAgent installed), the note says the
    LaunchAgent needs its own grants and points at the launcher and tcc.* lines, which report it."""
    root = Path.home() / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    root.mkdir(parents=True)
    (root / "a.docx").write_bytes(b"x")
    monkeypatch.setenv(doctor.AGENT_STEP_PENDING_ENV, "1")
    src = dataclasses.replace(sample_config.sources[0], path=root, sentinel=None)
    tcc = by_name(run_checks(dataclasses.replace(sample_config, sources=(src,))))["tcc"]
    assert tcc.ok and "the LaunchAgent runs " in tcc.detail and "needs its own" in tcc.detail
    assert tcc.detail.endswith("(the launcher and tcc.* lines below report it)")
    assert "python" not in tcc.detail.lower(), "a cloud root needs the launcher, never the interpreter"


@pytest.mark.usefixtures("real_domain_check")
def test_a_plain_provider_folder_holds_back_deletions_only_for_a_folder_it_cannot_list(
    sample_config: Config,
) -> None:
    """B13 round 5 F50: outside a File Provider domain (a plain folder, as a sandbox's mkdir makes) the walk
    reads an empty folder as empty, so the sentinel line says only that a folder it cannot list holds the
    deletions back."""
    root = Path.home() / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    root.mkdir(parents=True)
    (root / "a.docx").write_bytes(b"x")
    src = dataclasses.replace(sample_config.sources[0], path=root, sentinel=None)
    sentinel = by_name(run_checks(dataclasses.replace(sample_config, sources=(src,))))[
        "source.local-fixture.sentinel"
    ]
    assert sentinel.ok and sentinel.fix is None
    assert sentinel.detail == (
        "no sentinel configured (optional: agentsync already holds back deletions under a cloud folder that "
        "cannot be listed)"
    )


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
    # B13 round 12 N56: the fix names what to run once Allow is clicked, as the sign-in's does
    assert (listable.fix or "").startswith(
        "click Allow on the macOS prompt, then run ~/.local/bin/agentsync status again; if no prompt shows: "
    ), listable.fix
    assert "source.local-fixture.sentinel" not in r and "source.local-fixture.volume" not in r


@pytest.mark.parametrize("there", [True, False])
def test_a_held_provider_folder_never_holds_status_past_the_listing_bound(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch, there: bool
) -> None:
    """B13 round 13 O47: status read every cloud source's provider folder for the missing-source fix before
    its timed listing, so a hidden Allow prompt holding reads under OneDrive-Contoso held status there, past
    the bound that gives the click-Allow FAIL. The fix is read only once the listing has answered, and within
    the same bound; unread, it keeps the path fix (``there``: the source folder exists, else it is
    missing)."""
    provider = Path.home() / "Library" / "CloudStorage" / "OneDrive-Contoso"
    root = provider / "Docs"
    (provider / "Other").mkdir(parents=True)
    if there:
        root.mkdir()
        (root / "a.docx").write_bytes(b"x")
    release = threading.Event()
    real_iterdir, real_first = Path.iterdir, doctor._first_entry

    def iterdir(self: Path) -> Iterator[Path]:
        if self == provider:
            release.wait(5.0)
        return real_iterdir(self)

    def first_entry(p: Path) -> str | None:
        if p == root and there:
            release.wait(5.0)
        return real_first(p)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    monkeypatch.setattr(doctor, "_first_entry", first_entry)
    monkeypatch.setattr(doctor, "_LISTING_TIMEOUT_S", 0.2, raising=False)
    src = dataclasses.replace(sample_config.sources[0], id="docs", path=root, sentinel=None)
    try:
        started = time.monotonic()
        r = by_name(doctor._check_local_source(sample_config, src, Path(sys.executable)))
        assert time.monotonic() - started < 2.0, "held behind the provider folder's read"
    finally:
        release.set()
    listable = r["source.docs.listable"]
    if there:
        assert "did not return" in listable.detail and "click Allow" in (listable.fix or ""), listable
    else:
        assert listable.fix == (
            'fix path in [[source]] id = "docs", or put the folder back under that name in OneDrive, or sync '
            "that folder in OneDrive"
        ), listable


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
    assert r["graph.signed_in"].fix == "~/.local/bin/agentsync login" and "od" in r["graph.signed_in"].detail
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
    assert r.fix == "~/.local/bin/agentsync install-agent"


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
    assert r.fix == "~/.local/bin/agentsync install-agent"
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


def test_a_gitleaks_only_this_shell_finds_is_a_warn_until_a_shell_run_records_it(
    sample_config: Config, fakes: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 R75: launchd's PATH does not reach a ``go install`` or MacPorts gitleaks, so background syncs
    would scan with the builtin patterns alone; a run from the shell records the path and clears it."""
    bin_dir = tmp_path / "shell-bin"
    bin_dir.mkdir()
    exe = bin_dir / "gitleaks"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    monkeypatch.setattr(lints, "_GITLEAKS_CANDIDATES", ())
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    optional = by_name(run_checks(sample_config))["gitleaks"]
    assert (
        optional.severity is Severity.INFO and optional.fix is None
    )  # no LaunchAgent: background is optional
    install_agents(sample_config, fakes["loaded"])  # type: ignore[arg-type]
    r = by_name(run_checks(sample_config))["gitleaks"]
    assert not r.ok and r.severity is Severity.WARN
    assert r.detail.startswith(f"{exe} is on this shell's PATH only: a background run would not find it")
    assert r.fix == "~/.local/bin/agentsync sync (a run from this shell records where gitleaks is)"
    lints.remember_gitleaks()
    assert "gitleaks" not in by_name(run_checks(sample_config))


# ------------------------------------------------------------------------------------------------ lock


def test_lock_states(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_path = sample_config.state_paths.lock
    assert by_name(run_checks(sample_config))["lock"].detail == "no lock file yet"
    lk = SingleWriterLock(lock_path, "reconcile", started_by="other")
    lk.acquire()
    try:
        r = by_name(run_checks(sample_config))["lock"]
        # B13 round 10 L73: the holder sidecar says who started it
        assert (
            r.ok
            and f"pid {os.getpid()} (a sync not started by the background job, its pass: reconcile)"
            in r.detail
        )
        lk.beat("convert", now_iso="2000-01-01T00:00:00Z")
        r = by_name(run_checks(sample_config))["lock"]
        assert not r.ok and r.severity is Severity.WARN and "silent for" in r.detail
        assert r.fix == f"if that process is hung: kill {os.getpid()}"
    finally:
        lk.release()
    assert by_name(run_checks(sample_config))["lock"].detail == "free"
    # B13 round 2 R62: install.sh's reinstall holds the lock through lockf with no body, and doctor said "held
    # by an unrecorded holder (install.sh while it reinstalls agentsync, ...) (a cycle is running)".
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        r = by_name(run_checks(sample_config))["lock"]
        assert r.ok and r.detail == f"held by {EMPTY_BODY_HOLDER}"
    finally:
        os.close(fd)
    lock_path.write_text(LockInfo(99999, boot_time(), "2026-09-29T10:00:00Z", "poll").render() + "\n")
    r = by_name(run_checks(sample_config))["lock"]
    assert not r.ok and "without releasing" in r.detail and "FULL" in r.detail


@pytest.mark.parametrize("label", ["exclude-empty", "governance"])
def test_lock_held_by_a_non_cycle_names_no_cycle(sample_config: Config, label: str) -> None:
    """F71 (B13 round 5): any parseable body got "(a cycle is running)", so exclude-empty or a purge holding
    the lock read as a cycle.  Only a sync mode's label (CycleMode) is a cycle."""
    lk = SingleWriterLock(sample_config.state_paths.lock, label)
    lk.acquire()
    try:
        r = by_name(run_checks(sample_config))["lock"]
    finally:
        lk.release()
    assert r.ok and f"pid {os.getpid()} ({label})" in r.detail, r.detail
    assert "a cycle is running" not in r.detail and r.detail.endswith(" (not a cycle)"), r.detail


@pytest.mark.parametrize("mode", list(CycleMode))
def test_lock_held_by_a_cycle_says_a_cycle_is_running(sample_config: Config, mode: CycleMode) -> None:
    """F71: every sync mode, a dry run included, is a cycle."""
    lk = SingleWriterLock(sample_config.state_paths.lock, mode.value)
    lk.acquire()
    try:
        r = by_name(run_checks(sample_config))["lock"]
    finally:
        lk.release()
    assert r.ok and r.detail.endswith(" (a cycle is running)"), r.detail


def test_lock_held_with_an_empty_body_names_no_cycle(sample_config: Config) -> None:
    """R50: install.sh's lockf holds the lock and writes no body, so doctor names the unrecorded holder, says
    no cycle is running, and reads no dead cycle's sidecar (no kill of an unrelated pid)."""
    lock_path = sample_config.state_paths.lock
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("")
    holder = lock_path.with_name(lock_path.name + ".json")
    holder.write_text('{"pid": 99999, "last_beat_at": "2000-01-01T00:00:00Z"}')
    fd = os.open(lock_path, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        r = by_name(run_checks(sample_config))["lock"]
    finally:
        os.close(fd)
    assert r.ok and r.detail == f"held by {EMPTY_BODY_HOLDER}" and r.fix is None
    assert "a cycle is running" not in r.detail


def test_two_sources_holding_the_same_files_is_an_info_line(sample_config: Config) -> None:
    """§16.45 R144: an advisory only (ok, never a warn), shown only when two non-inbox sources share live
    files by content; it names both ids and the count, and refuses nothing."""
    assert "sources.shared_content" not in by_name(run_checks(sample_config))
    first = sample_config.sources[0]
    cfg = dataclasses.replace(
        sample_config,
        sources=(first, dataclasses.replace(first, id="team-copy", path=first.path.parent / "copy")),  # type: ignore[union-attr]
    )
    with Manifest(cfg.state_paths.db) as manifest:
        manifest.sync_sources(list(cfg.sources))
        assert "sources.shared_content" not in by_name(doctor._check_shared_content(cfg))
        for sid in ("local-fixture", "team-copy"):
            manifest.upsert_observed(
                SourceItem(
                    source_id=sid,
                    stable_id="vol:1",
                    rel_path="a/brief.docx",
                    name="brief.docx",
                    size=10,
                    mtime_ns=1_000,
                    ctime_ns=1_000,
                ),
                run_id=1,
                verdict=Verdict.CREATED,
                state=RowState.LIVE,
            )
            h = "a" * 64
            manifest.set_content(
                sid, "vol:1", content_sha256=h, canonical_sha256=h, canonical_method="m", canonical_parts=[]
            )
    r = by_name(run_checks(cfg))["sources.shared_content"]
    assert r.ok and r.severity is Severity.INFO and r.fix is None
    assert r.detail.startswith("1 file(s) in local-fixture have the same content as a file in team-copy: ")
    # The advice is the cycle's own rule: deleting the [[source]] block is refused at the next sync (exit 78).
    # B13 round 5 F97: in the refusal's own words, from one constant
    assert r.detail.endswith(f"give its [[source]] block in sources.toml {RETIRE_ADVICE}"), r.detail
    assert "removing it" not in r.detail


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
    r = by_name(run_checks(sample_config))[name]
    assert r.ok and "incomplete" not in r.detail

    # B13 round 4 Q14/Q35: below the threshold the line stays ok, but an incomplete pass is named; it sets
    # last_success_at too, so a bare "last success" read as a clean pass beside a WAITING ON YOU line.
    sample_config.state_paths.heartbeat.unlink()
    for n in (1, 2):
        _beat(sample_config, enumeration_complete=False)
        r = by_name(run_checks(sample_config))[name]
        assert r.ok and r.severity is Severity.INFO and r.fix is None
        assert r.detail.startswith("last success ")
        # B13 round 15 S14: the count is known, so no "(s)"
        passes = "1 pass" if n == 1 else f"{n} passes"
        assert r.detail.endswith(
            f"; listing incomplete for the last {passes}, deletions held, a warn after 3"
        )
    _beat(sample_config)
    r = by_name(run_checks(sample_config))[name]
    assert r.ok and "incomplete" not in r.detail

    old = (datetime.now(UTC) - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sample_config.state_paths.heartbeat.unlink()
    _beat(sample_config, now_iso=old)
    r = by_name(run_checks(sample_config))[name]
    assert not r.ok and r.severity is Severity.WARN and "3 x cadence" in r.detail
    assert r.fix == "~/.local/bin/agentsync sync -v"
    assert_fix_parses(r.fix)

    sample_config.state_paths.heartbeat.unlink()
    for _ in range(3):
        _beat(sample_config, enumeration_complete=False)
    r = by_name(run_checks(sample_config))[name]
    assert not r.ok and "incomplete for 3" in r.detail
    # Bring-back S11: a local walk is always a full pass, so "another sync clears this" was false for it.
    # The loop's WAITING ON YOU line says what to do; R38: status prints this fix, so it names no command.
    assert r.fix == (
        "each WAITING ON YOU line about local-fixture says what stops the listing and what to do: another "
        "sync does not clear it"
    )

    _beat(sample_config, ok=False, auth_state="REAUTH_REQUIRED")
    r = by_name(run_checks(sample_config))[name]
    assert not r.ok and r.severity is Severity.ERROR and r.fix == "~/.local/bin/agentsync login"
    assert_fix_parses(r.fix)

    sample_config.state_paths.heartbeat.unlink()
    _beat(sample_config, ok=False, pass_kind=None)
    r = by_name(run_checks(sample_config))[name]
    assert not r.ok and "no successful pass" in r.detail
    assert r.fix == "~/.local/bin/agentsync sync -v"
    assert_fix_parses(r.fix)


def test_heartbeat_incomplete_fix_by_source_kind(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bring-back S11: an inbox is incomplete while a file is being written, so sync again is its fix, and a
    Graph source's too (B13 round 6 G54: the loop says the same, and a plain sync clears the count). A local
    folder points at the loop's WAITING ON YOU line, the one place that names the empty cloud folders and
    weighs what excluding them would retire: no folder name here, and no walk of the source. Under install.sh
    the line is a note, since install.sh prints the loop's line."""
    local = sample_config.sources[0]
    fix = doctor._incomplete_fix(dataclasses.replace(local, id="team-drive", kind=SourceKind.INBOX))
    assert fix == "~/.local/bin/agentsync sync -v; a full pass that lists all of team-drive clears this"
    assert_fix_parses(fix)
    # B13 round 6 G54: since F69 a delta resets the count, and the three FULL passes the warn needs come only
    # where a plain sync is FULL too, so a Graph source keeps sync -v, as the loop and CONTRACTS say.
    fix = doctor._incomplete_fix(dataclasses.replace(local, id="team-drive", kind=SourceKind.GRAPH_DRIVE))
    assert fix == "~/.local/bin/agentsync sync -v; a full pass that lists all of team-drive clears this"
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
    assert r.fix is not None and r.fix.startswith("each WAITING ON YOU line about ")
    assert "Bids" not in r.fix and r.note is None
    monkeypatch.setenv(doctor.NO_NEXT_HINT_ENV, "1")
    r = by_name(doctor._check_heartbeat(cloud))["heartbeat.local-fixture"]
    assert not r.ok and r.severity is Severity.WARN and r.fix is None
    assert r.note == doctor._LISTING_YOURS_NOTE
    [line] = format_results([r]).splitlines()
    # B13 round 12 N17, N34, N74: one bracket, the note's; what is held follows a "; "
    assert line.endswith(f"consecutive passes; deletions held ({doctor._LISTING_YOURS_NOTE})")
    assert line.count("(") == 1, line
    monkeypatch.delenv(doctor.NO_NEXT_HINT_ENV)
    r = by_name(doctor._check_heartbeat(cloud))["heartbeat.local-fixture"]
    [line] = format_results([r]).splitlines()
    assert "consecutive passes; deletions held (fix: each WAITING ON YOU line about " in line, line


def test_the_pre_sync_listing_note_promises_no_waiting_line(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install.sh's status runs before its sync and reads the last walk's stored state: a folder held for 3
    passes, then given a file, still reads as held there, and the sync then lists it complete and no WAITING
    ON YOU line follows. The note says where the answer is instead of pointing at a line (B13 round 4 Q26,
    Q32), and names no fix: the sync below runs anyway, or a FAIL skipped it."""
    for _ in range(3):
        _beat(sample_config, enumeration_complete=False)
    root = sample_config.sources[0].path
    root.mkdir(parents=True, exist_ok=True)
    (root / "now-filled.docx").write_bytes(b"x")
    monkeypatch.setenv(doctor.NO_NEXT_HINT_ENV, "1")
    name = "heartbeat.local-fixture"
    r = by_name(doctor._check_heartbeat(sample_config, loop_checks=(name,)))[name]
    assert r.fix is None and r.note == doctor._LISTING_YOURS_NOTE
    # no command: "sync" is named only as this run's (B13 round 6 G33), never as one to run
    assert "see WAITING ON YOU" not in r.note and "yours" not in r.note and "`" not in r.note
    assert "agentsync" not in r.note and "run " not in r.note.replace("this run", "")
    # no line type promised either (G33): a FAIL, a privacy-held listing or a cleared one print different ones
    assert "WAITING ON YOU" not in r.note and "whether it cleared it" in r.note
    # B13 round 14 Q35: the sync it names may not run (a [FAIL] or a busy lock skips it), so no promise
    assert "if this run's sync runs" in r.note
    assert "(" not in r.note, "the report's fix parser strips one level"


def test_heartbeat_incomplete_follows_the_loop_checks(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R151: given the loop's checks (status passes them), an incomplete listing points at WAITING ON YOU
    only when the loop has a wait for it, whatever the source kind; otherwise a local source syncs again."""
    for _ in range(3):
        _beat(sample_config, enumeration_complete=False)
    name = "heartbeat.local-fixture"
    r = by_name(doctor._check_heartbeat(sample_config, loop_checks=()))[name]
    assert r.fix == "~/.local/bin/agentsync sync; another full walk lists it" and r.note is None
    assert_fix_parses(r.fix)
    r = by_name(doctor.run_checks(sample_config, tcc_canary=False, loop_checks=()))[name]
    assert r.fix == "~/.local/bin/agentsync sync; another full walk lists it"
    r = by_name(doctor._check_heartbeat(sample_config, loop_checks=(name,)))[name]
    assert r.fix is not None and r.fix.startswith("each WAITING ON YOU line about local-fixture ")
    assert "(" not in r.fix, "the report's fix parser strips one level of parentheses"
    inbox = dataclasses.replace(
        sample_config, sources=(dataclasses.replace(sample_config.sources[0], kind=SourceKind.INBOX),)
    )
    r = by_name(doctor._check_heartbeat(inbox, loop_checks=(name,)))[name]
    assert r.fix is not None and r.fix.startswith("each WAITING ON YOU line about local-fixture ")
    monkeypatch.setenv(doctor.NO_NEXT_HINT_ENV, "1")
    r = by_name(doctor._check_heartbeat(inbox, loop_checks=(name,)))[name]
    assert r.fix is None and r.note == doctor._LISTING_YOURS_NOTE
    # B13 round 8 J12: under install.sh a local source no wait names reads the note too, not "sync; another
    # full walk": install's own sync is that walk, and the note stays true if a lock or a FAIL skips it
    r = by_name(doctor._check_heartbeat(sample_config, loop_checks=()))[name]
    assert r.fix is None and r.note == doctor._LISTING_YOURS_NOTE
    r = by_name(doctor.run_checks(sample_config, tcc_canary=False, loop_checks=()))[name]
    assert r.fix is None and r.note == doctor._LISTING_YOURS_NOTE
    # B13 round 9 K75: so does an inbox no wait names on status's path (loop_checks given): an inbox pass is
    # always full, so install's step-6 sync is that walk; round 8 said a plain sync may run a delta for one
    r = by_name(doctor._check_heartbeat(inbox, loop_checks=()))[name]
    assert r.fix is None and r.note == doctor._LISTING_YOURS_NOTE
    # the setup report (no loop_checks) runs after install's sync: an inbox not held by empty folders keeps
    # sync -v, which names a cause no plain sync clears
    r = by_name(doctor._check_heartbeat(inbox, loop_checks=None))[name]
    assert r.note is None and (r.fix or "").startswith("~/.local/bin/agentsync sync -v; "), r
    # and outside install.sh an inbox no wait names keeps sync -v on status's path too
    monkeypatch.delenv(doctor.NO_NEXT_HINT_ENV)
    r = by_name(doctor._check_heartbeat(inbox, loop_checks=()))[name]
    assert r.note is None and (r.fix or "").startswith("~/.local/bin/agentsync sync -v; "), r


@pytest.mark.parametrize("note", [doctor._LISTING_YOURS_NOTE, doctor.LISTING_YOURS_REPORT_NOTE])
def test_a_held_listing_line_says_held_once(note: str) -> None:
    """B13 round 13 O36: "deletions held (held at its last walk: ...)" used "held" for the deletions and for
    the listing's state, and the report's note a third time ("still holds it"). The note dates the state
    ("as of its last walk"), and the line has one "held"."""
    line = CheckResult(
        "heartbeat.contoso-docs",
        False,
        "enumeration incomplete for 4 consecutive passes; deletions held",
        Severity.WARN,
        note=note,
    )
    shown = format_results([line])
    assert shown.count("held") == 1 and "(as of its last walk: " in shown, shown


def assert_fix_parses(fix: str | None) -> None:
    """A fix naming an agentsync command names one the CLI still parses (KISS K13b: no deleted spelling
    such as ``sync --source`` or ``sync --dry-run``), by its full path (B13 round 4 Q49: ~/.local/bin is
    not on PATH). The text after ``;`` and any parenthetical are prose."""
    assert fix is not None and fix.startswith(f"{BIN} "), fix
    command = re.sub(r"\([^)]*\)", "", fix.split(";")[0]).removeprefix(f"{BIN} ")
    try:
        cli.build_parser().parse_args(shlex.split(command))
    except SystemExit:
        pytest.fail(f"the fix names a command the CLI rejects: {fix}")


def test_assert_fix_parses_rejects_deleted_spellings() -> None:
    for fix in (f"{BIN} sync --source local-fixture -v", f"{BIN} sync --dry-run"):
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


def test_a_missing_source_fix_names_the_sign_in_only_when_the_sign_in_is_the_cause(
    sample_config: Config,
) -> None:
    """B13 round 11 M10: every missing source's fix read "fix path ..., or sign in to the sync client", also
    one command after step 1 said OneDrive is not signed in. The fix follows what is missing: no provider
    folder of that app at all, the sign-in alone; the provider folder there but not the subfolder, the path
    or the app's folder choice, never a sign-in; a plain folder, the path alone; the provider folder missing
    beside another of the same app (another account), the sign-in or the path."""
    cloud = Path.home() / "Library" / "CloudStorage"
    gone = cloud / "OneDrive-Contoso"
    src = dataclasses.replace(sample_config.sources[0], id="docs", path=gone / "Docs", sentinel=None)
    cfg = dataclasses.replace(sample_config, sources=(src,))
    table = 'fix path in [[source]] id = "docs"'

    def fixes() -> set[str | None]:
        r = by_name(run_checks(cfg))
        return {r["source.docs.listable"].fix, r["source.docs.volume"].fix}

    # B13 round 12 N56: "then re-run" named no command; status is the read-only check that reads it again
    assert fixes() == {
        f"sign in to OneDrive on this Mac, since {gone} is missing, then run ~/.local/bin/agentsync status "
        "again"
    }
    (cloud / "OneDrive-Fabrikam").mkdir(parents=True)
    assert fixes() == {
        f"{gone} is missing while another OneDrive folder is there: sign in to OneDrive with the account it "
        f"belongs to, or {table}"
    }
    # B13 round 12 N53: a SharePoint library folder missing beside its own organization's OneDrive folder is a
    # library not synced here, never a sign-in (that account is signed in); beside another one's, as above
    library = cloud / "OneDrive-SharedLibraries-Contoso"
    lib = dataclasses.replace(src, path=library / "Team Site - Documents")
    cfg = dataclasses.replace(sample_config, sources=(lib,))
    assert fixes() == {
        f"{library} is missing while another OneDrive folder is there: sign in to OneDrive with the account "
        f"it belongs to, or {table}"
    }
    gone.mkdir()
    shared = {
        f"{library} is missing while OneDrive-Contoso is there: sync the SharePoint library to this Mac with "
        f"Sync on its SharePoint page, or {table}"
    }
    assert fixes() == shared and all("(" not in (f or "") for f in fixes())
    # B13 round 13 O52: the library folder there but empty beside OneDrive-Contoso is a library not synced or
    # still setting up, never the sign-in the empty-folder rule (N61) gave it
    library.mkdir()
    (library / ".tmp.drivedownload").mkdir()
    assert fixes() == {
        f"{library} is empty while OneDrive-Contoso is there: sync the SharePoint library to this Mac with "
        f"Sync on its SharePoint page, or let OneDrive finish setting it up, or {table}"
    }
    gone.rmdir()  # ... and beside no OneDrive-Contoso, the empty-folder rule's sign-in as before
    assert fixes() == {
        f"sign in to OneDrive on this Mac or let it finish setting up, since {library} is empty, then run "
        "~/.local/bin/agentsync status again"
    }
    shutil.rmtree(library)
    cfg = dataclasses.replace(sample_config, sources=(src,))
    gone.mkdir()
    (gone / ".tmp.drivedownload").mkdir()
    # B13 round 12 N61: a provider folder that shows nothing is stale, as install.sh's list reads it
    assert fixes() == {
        f"sign in to OneDrive on this Mac or let it finish setting up, since {gone} is empty, then run "
        "~/.local/bin/agentsync status again"
    }
    (gone / "Other").mkdir()
    # B13 round 15 S4: a folder renamed in a signed-in OneDrive is put back, as step 1 and the wait say
    assert fixes() == {
        f"{table}, or put the folder back under that name in OneDrive, or sync that folder in OneDrive"
    }
    plain = dataclasses.replace(src, path=sample_config.config_path.parent / "gone")
    r = by_name(run_checks(dataclasses.replace(cfg, sources=(plain,))))
    assert r["source.docs.listable"].fix == table
    assert all("(" not in (f or "") for f in fixes())  # a FAIL line puts its fix in one parenthesis


def test_a_source_fix_names_the_table_as_sources_toml_writes_it(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """§16.39 N19: each fix that names a [[source]] table by its id line quotes the id as sources.toml has
    it (config.local_source_table, the add-source writer). It printed Python's repr, id = 'x', which a search
    of the file never found, and which the setup prompt's single-quoted --log FIX had to rewrite."""
    written = sample_config.config_path.read_text(encoding="utf-8")
    src = sample_config.sources[0]
    afile = tmp_path / "afile"
    afile.write_text("x\n", encoding="utf-8")
    variants = [
        dataclasses.replace(src, path=None),
        dataclasses.replace(src, path=tmp_path / "gone"),
        dataclasses.replace(src, path=afile, sentinel=None),
        dataclasses.replace(src, sentinel="nope.txt"),
    ]
    fixes: list[tuple[str, str]] = []
    for v in variants:
        for r in doctor._check_local_source(sample_config, v, Path(sys.executable)):
            if not r.ok and r.fix and "[[source]]" in r.fix:
                fixes.append((r.name, r.fix))
    _launcher(monkeypatch)
    cfg, root = _cloud(sample_config)
    monkeypatch.setattr(doctor, "_launcher_canary", lambda *a: (launchd.EXIT_CANARY_MISSING, ""))
    t = by_name(run_checks(cfg))["tcc.onedrive"]
    fixes.append((t.name, t.fix or ""))
    assert [n for n, _ in fixes] == [
        "source.local-fixture",
        "source.local-fixture.listable",
        "source.local-fixture.sentinel",
        "source.local-fixture.volume",
        "source.local-fixture.listable",
        "source.local-fixture.sentinel",
        "tcc.onedrive",
    ]
    for name, fix in fixes:
        assert "id = '" not in fix, (name, fix)
        m = re.search(r'id = "[a-z0-9-]+"', fix)
        assert m is not None, (name, fix)
        sid = name.split(".")[1]
        assert m.group(0) in local_source_table(sid, root), (name, fix)
        if sid == src.id:
            assert m.group(0) in written, (name, fix)


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
    assert (r.severity, r.fix, r.note) == (Severity.ERROR, "~/.local/bin/agentsync install-agent", None)
    assert format_results([r]).endswith(" (fix: ~/.local/bin/agentsync install-agent)")


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
    assert r.severity is Severity.ERROR and r.fix == "~/.local/bin/agentsync install-agent" and r.note is None


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


# ------------------------------------------------------------------------------------------------ media


def _real_media(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real media probe on a Mac with recordings on (the suite switches OCR off, see conftest)."""
    monkeypatch.setattr(doctor, "_media_status", REAL_MEDIA_STATUS)
    monkeypatch.delenv("AGENTSYNC_OCR", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")


def test_media_off_is_ok_and_names_the_switch(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    _real_media(monkeypatch)
    off = dataclasses.replace(
        sample_config, convert=dataclasses.replace(sample_config.convert, recordings=False)
    )
    r = by_name(run_checks(off))["media"]
    assert (r.ok, r.detail) == (True, "media helper: off ([convert] recordings = false)")
    monkeypatch.setenv("AGENTSYNC_OCR", "0")
    r = by_name(run_checks(sample_config))["media"]
    assert (r.ok, r.detail) == (True, "media helper: off (AGENTSYNC_OCR=0)")


def test_media_not_built_is_an_info_line_and_doctor_never_builds(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only scripts/install.sh compiles the media helper: status and doctor start no build and no compiler,
    write nothing, and say who builds it."""
    _real_media(monkeypatch)
    _real_ocr(monkeypatch)
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: False)

    def no_build(*args: object, **kwargs: object) -> None:
        pytest.fail(f"doctor reached a helper build: {args}")

    for module, names in (
        (ocr, ("build", "_build", "_compile", "_tool", "_run_build", "_run_helper")),
        (media, ("build",)),
    ):
        for name in names:
            monkeypatch.setattr(module, name, no_build)
    for results in (run_checks(sample_config), cli._status_checks(sample_config, offline=True)):
        r = by_name(results)["media"]
        assert (r.ok, r.severity, r.fix, r.note) == (False, Severity.INFO, None, None)
        assert r.detail == "the media helper is not built; scripts/install.sh builds it"
        assert errors([r]) == []
    assert not (sample_config.cache_dir / "media").exists()
    line = format_results([r])
    assert line.startswith("[info] media") and "fix:" not in line
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: True)
    assert by_name(run_checks(sample_config))["media"].detail == (
        "the media helper is not built; scripts/install.sh builds it once the Command Line Tools are "
        "installed (xcode-select --install)"
    )


def test_media_reads_the_helper_under_the_configured_cache_dir(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _real_media(monkeypatch)
    helper = ocr._helper_path(sample_config.cache_dir, media.MEDIA)
    fake_media(helper.parent).replace(helper)
    r = by_name(run_checks(sample_config))["media"]
    assert r.ok and r.detail == f"media helper: ready ({MEDIA_READY})"
    assert by_name(run_checks(sample_config))["docs_repo.permissions"].ok, "the helper is owner-only"
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: False)
    fake_media(tmp_path, version_exit=3).replace(helper)  # built, and it stopped answering
    r = by_name(run_checks(sample_config))["media"]
    assert (r.ok, r.severity, r.fix) == (False, Severity.WARN, None)
    assert r.detail == "media helper: not working (the media helper exited 3: no message)"
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: True)
    r = by_name(run_checks(sample_config))["media"]
    assert r.fix == "xcode-select --install, then run scripts/install.sh again"
    assert errors(run_checks(sample_config)) == [], "the media helper is optional: never a FAIL"


def test_media_probe_crash_is_a_warn_without_the_exception_text(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(cfg: Config) -> tuple[str, str]:
        raise PermissionError(errno.EACCES, "Permission denied", str(cfg.cache_dir))

    monkeypatch.setattr(doctor, "_media_status", crash)
    results = run_checks(sample_config)
    r = by_name(results)["media"]
    assert (r.ok, r.severity, r.fix) == (False, Severity.WARN, None)
    assert r.detail == "media helper: could not be checked (PermissionError)"
    assert errors(results) == []


# ------------------------------------------------------------------------------------------------ speech


def _real_speech(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real speech probe on a Mac with the media helper's switches on (the suite switches them off)."""
    monkeypatch.setattr(doctor, "_speech_status", REAL_SPEECH_STATUS)
    monkeypatch.delenv("AGENTSYNC_OCR", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")


def test_speech_off_is_ok_and_names_the_switch_or_the_missing_models(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_speech(monkeypatch)
    off = dataclasses.replace(
        sample_config, convert=dataclasses.replace(sample_config.convert, recordings=False)
    )
    r = by_name(run_checks(off))["speech"]
    assert (r.ok, r.detail) == (True, "speech: off ([convert] recordings = false)")
    monkeypatch.setenv("AGENTSYNC_OCR", "0")
    r = by_name(run_checks(sample_config))["speech"]
    assert (r.ok, r.detail) == (True, "speech: off (AGENTSYNC_OCR=0)")
    monkeypatch.delenv("AGENTSYNC_OCR")
    helper = ocr._helper_path(sample_config.cache_dir, speech.SPEECH)
    fake_speech(helper.parent).replace(helper)
    r = by_name(run_checks(sample_config))["speech"]
    assert (r.ok, r.detail) == (
        True,
        "speech: off (the speech helper is built; the speech models are not placed: parakeet-tdt-0.6b-v3/ "
        "and speaker-diarization/)",
    )
    place_models(helper.parent)
    r = by_name(run_checks(sample_config))["speech"]
    assert (r.ok, r.detail) == (True, f"speech: ready ({SPEECH_READY})")
    assert by_name(run_checks(sample_config))["docs_repo.permissions"].ok, "the speech folder is owner-only"


def test_speech_not_built_is_an_info_line_and_doctor_never_builds_or_digests(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_speech(monkeypatch)
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: False)

    def no_build(*args: object, **kwargs: object) -> None:
        pytest.fail(f"doctor reached a speech build or digest: {args}")

    for name in ("build", "_compile", "_step", "folder_digest"):
        monkeypatch.setattr(speech, name, no_build)
    for results in (run_checks(sample_config), cli._status_checks(sample_config, offline=True)):
        r = by_name(results)["speech"]
        assert (r.ok, r.severity, r.fix) == (False, Severity.INFO, None)
        assert r.detail == "speech: not-built (the speech helper is not built; scripts/install.sh builds it)"
        assert errors([r]) == []
    assert not (sample_config.cache_dir / "speech").exists()


def test_speech_failed_is_a_warn_never_a_fail(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    _real_speech(monkeypatch)
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: True)
    helper = ocr._helper_path(sample_config.cache_dir, speech.SPEECH)
    fake_speech(helper.parent, version_exit=3).replace(helper)
    r = by_name(run_checks(sample_config))["speech"]
    assert (r.ok, r.severity) == (False, Severity.WARN)
    assert r.detail == "speech: failed (the speech helper exited 3: no message)"
    assert r.fix == "xcode-select --install, then run scripts/install.sh again"

    def crash(cfg: Config) -> tuple[str, str]:
        raise PermissionError(errno.EACCES, "Permission denied", str(cfg.cache_dir))

    monkeypatch.setattr(doctor, "_speech_status", crash)
    results = run_checks(sample_config)
    r = by_name(results)["speech"]
    assert (r.ok, r.severity, r.detail) == (
        False,
        Severity.WARN,
        "speech: could not be checked (PermissionError)",
    )
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
