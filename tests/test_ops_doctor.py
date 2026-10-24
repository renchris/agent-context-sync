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
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn

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
from agentsync.paths import expand
from agentsync.skill import HELPER_RETRY_CLAUSE
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
    # B13 round 15 S29: `uv sync` syncs the project in the folder it runs in, never the tool environment
    # install.sh made, and install.sh leaves uv off PATH; its step 2 makes that environment with Python 3.11.
    assert r.fix == f"run {INSTALL_SH} again (it installs agentsync with uv's Python 3.11)"


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
    pandoc = str(doctor._pandoc_route(sample_config)[0])
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
    p = doctor._pandoc_route(sample_config)[0]
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
    "the native pandoc copy at ~/'.local/share/agentsync/pandoc/3.9-arm64' is yours to replace or remove "
    "(the converters run it instead of the bundled pandoc), or set [convert] pandoc_path to an absolute "
    "pandoc"
)
NATIVE_COMES_BACK = (
    "show the person this line: the native pandoc copy at ~/'.local/share/agentsync/pandoc/3.9-arm64' is "
    "theirs to replace or remove (the converters run it instead of the bundled pandoc)"
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
        route = doctor._pandoc_route(sample_config)[0]
        assert route.resolve() == pandoc_mod.resolved_pandoc(None)[0].resolve() == copy
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
        # B13 round 17 U104: a busy Mac slows any copy's start; the first-start half is Rosetta's alone
        assert r.detail == (
            f"{copy} (native arm64 copy) --version did not answer within 60s: {OUT_OF_TIME} ({BUSY_START})"
        )
        assert "Rosetta" not in r.detail and "first start" not in r.detail
        assert r.fix == f"{RUN_AGAIN}{COMES_BACK}{NATIVE_COMES_BACK}"
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


def _configured(sample_config: Config, pandoc: Path) -> Config:
    return dataclasses.replace(
        sample_config, convert=dataclasses.replace(sample_config.convert, pandoc_path=pandoc)
    )


def test_a_configured_pandoc_that_fails_names_that_pandoc_and_not_the_bundled_one(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """B13 round 15 S29 (rehearsal probe 60): a [convert] pandoc_path that hangs, exits non-zero or is gone
    got the bundled pandoc's fix ("uv sync (reinstalls pypandoc_binary) or set [convert] pandoc_path to an
    absolute pandoc"), with pandoc_path already that absolute pandoc, and on a time-out the bundled one's
    slow-first-start reason. Its fix is now that path's own, and the time-out's names it once the line comes
    back; only a path that is not absolute keeps "set ... to an absolute pandoc"."""
    pandoc = tmp_path / "fakebin" / "pandoc"
    pandoc.parent.mkdir()
    pandoc.write_text("#!/bin/sh\necho broken >&2\nexit 3\n", encoding="utf-8")
    pandoc.chmod(0o755)
    cfg = _configured(sample_config, pandoc)
    own = (
        f"check or replace the pandoc at [convert] pandoc_path ('{pandoc}'), or remove that line to use the "
        "bundled pandoc or its native arm64 copy"
    )
    [r] = doctor._check_pandoc(cfg)
    assert (r.ok, r.detail, r.fix) == (False, f"{pandoc} --version failed (exit 3): broken", own)

    monkeypatch.setattr(doctor, "_run", _silent(lambda argv: argv[0] == str(pandoc)))
    first, second = (by_name(run_checks(cfg))["pandoc"] for _ in range(2))
    assert first == second
    assert first.detail == f"{pandoc} --version did not answer within 60s: {OUT_OF_TIME} ({BUSY_START})"
    assert first.fix == f"{RUN_AGAIN}{COMES_BACK}{own}"
    for line in (first.detail, first.fix):
        assert "pypandoc_binary" not in line and "first start" not in line, line

    pandoc.unlink()
    [r] = doctor._check_pandoc(cfg)
    assert (r.detail, r.fix) == (f"{pandoc} is missing or not executable", own)
    [r] = doctor._check_pandoc(_configured(sample_config, Path("fakebin/pandoc")))
    assert r.fix == "set [convert] pandoc_path to an absolute pandoc"


def test_no_pandoc_fix_names_a_bare_uv_sync(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """B13 round 15 S29: `uv sync` reaches only the project in the folder it runs in, not the uv tool
    environment install.sh made, and install.sh's reinstall is skipped when this commit is already
    installed. The bundled pandoc's fix reinstalls pypandoc_binary inside that environment, measured
    (2026-10-18, uv 0.12) to put a deleted file of a dependency back in place, the environment kept. Every
    pandoc FAIL, of every kind, names no `uv sync`."""
    stand_in = tmp_path / "fakebin" / "pandoc"
    stand_in.parent.mkdir()
    stand_in.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    stand_in.chmod(0o755)
    failures: tuple[OSError | subprocess.TimeoutExpired | None, ...] = (
        None,
        OSError(errno.ENOEXEC, "Exec format error"),
        subprocess.TimeoutExpired(["pandoc", "--version"], 60),
    )
    fixes: list[str] = []
    copy = _native_copy(monkeypatch, "exit 3")
    try:
        for route in ("native", "wheel", "configured"):
            if route == "wheel":
                copy.unlink()
            cfg = _configured(sample_config, stand_in) if route == "configured" else sample_config
            for failure in failures:

                def run(
                    argv: Sequence[str],
                    timeout: float = 30.0,
                    failure: OSError | subprocess.TimeoutExpired | None = failure,
                ) -> subprocess.CompletedProcess[str]:
                    if failure is not None:
                        raise failure
                    return subprocess.CompletedProcess(list(argv), 3, "", "")

                monkeypatch.setattr(doctor, "_run", run)
                fixes += [r.fix or "" for r in doctor._check_pandoc(cfg)]
        stand_in.unlink()
        fixes += [r.fix or "" for r in doctor._check_pandoc(_configured(sample_config, stand_in))]
        monkeypatch.setattr(pandoc_mod, "_wheel_pandoc", lambda: tmp_path / "gone")
        fixes += [r.fix or "" for r in doctor._check_pandoc(sample_config)]
    finally:
        pandoc_mod._wheel_pandoc_version.cache_clear()
    assert len(fixes) == 11 and all(fixes), fixes
    assert [f for f in fixes if re.search(r"(?<![\w/~.-])uv sync\b", f)] == []
    assert PANDOC_FIX in fixes and NATIVE_FIX in fixes


def test_the_bundled_pandocs_fix_is_install_shs_reinstall_and_no_bare_uv(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """B13 round 16 T54, T59, T60, T65: the bundled pandoc's fix was `<uv> tool upgrade --reinstall-package
    pypandoc_binary agentsync`, which took none of the sync locks install.sh holds around a reinstall, ran
    without the UV_SYSTEM_CERTS=1 it sets for networks that inspect TLS, rebuilt agentsync from the checkout
    as it stood, and moved every dependency, pypandoc_binary too, to its newest allowed version (measured
    with uv 0.12.17). The fix is now a re-run of install.sh, whose step 2 no longer skips while that pandoc is
    not the file pypandoc_binary installed (``bundled_pandoc_intact``, read here from the script)."""
    monkeypatch.setattr(pandoc_mod, "_wheel_pandoc", lambda: tmp_path / "gone")
    [r] = doctor._check_pandoc(sample_config)
    assert (r.ok, r.fix) == (False, PANDOC_FIX)
    assert "uv " not in r.fix and "tool upgrade" not in r.fix and "--reinstall" not in r.fix
    assert r.fix.startswith(doctor._PYTHON_FIX.split(" (", 1)[0] + " ("), "the re-run the python fix names"
    script = (Path(__file__).parents[1] / "scripts" / "install.sh").read_text(encoding="utf-8")
    current = re.search(r"(?ms)^install_is_current\(\) \{.*?^\}$", script)
    assert current is not None and 'bundled_pandoc_intact "${1%/*}"' in current.group(0)
    assert "UV_SYSTEM_CERTS=1" in script and "tool install --force --reinstall-package agentsync" in script
    guide = " ".join((Path(__file__).parents[1] / "docs/deploy/setup-feedback.md").read_text().split())
    assert f"for the bundled one `{PANDOC_FIX}`" in guide, "the guide quotes the fix doctor prints"
    assert "the `uv tool upgrade` command it names" not in guide


# --------------------------------------------------------------------- a check that does not finish

RUN_AGAIN = "run ~/.local/bin/agentsync status again as it is"
INSTALL_SH = "~/src/agent-context-sync/scripts/install.sh"
# B13 round 19 W19, W48: by what it is; no NEXT line names it under a warn, and --report-only builds nothing
# B13 round 20 X21: the plain command, which keeps the folders; no options the person never saw
RUN_AGAIN_INSTALL = (
    f"run {INSTALL_SH} again without --report-only, which builds nothing; a plain run keeps the folders "
    "already synced"
)
COMES_BACK = "; if this line comes back: "
STILL_NO_ANSWER = "report it: the program does not answer on this Mac, and no setup step clears that"
AGAIN = RUN_AGAIN + COMES_BACK + STILL_NO_ANSWER  # a check with no fix of its own for a broken program
PANDOC_PATH_THEIRS = "setting [convert] pandoc_path to an absolute pandoc is theirs"
PANDOC_FIX = (
    f"run {INSTALL_SH} again (it reinstalls agentsync when the bundled pandoc is missing or not the "
    "file pypandoc_binary installed); if this line comes back after that, show the person this line: "
    f"{PANDOC_PATH_THEIRS}"
)
# B13 round 17 U78: under install.sh, whose step 2 found the bundled file whole or reinstalled it, a time-out
# that comes back after the re-run names the person's edit, not that same re-run a second time
PANDOC_INSTALL_COMES_BACK = (
    f"show the person this line: {PANDOC_PATH_THEIRS} (the bundled pandoc does not answer on this Mac, and "
    "install.sh found its file whole)"
)
BUSY_START = "any start can take a minute on a busy Mac"
OUT_OF_TIME = "the check ran out of time, so it could not say whether anything is wrong"
CRASH_FIX = (
    "run ~/.local/bin/agentsync status -v, which prints the traceback: a check that crashes is a fault in "
    "agentsync to report, and no setup step clears it"
)


def test_the_install_sh_rerun_and_still_no_answer_fixes_nest_no_bracket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B13 round 21 Y20: every doctor line wraps its fix in ``(fix: ...)``, and under install.sh the time-out
    fix and the helper build's put ``(it keeps the folders already synced; ...)`` inside it, and
    ``_STILL_NO_ANSWER`` its own ``(the program ...)``: round 20's X7 and X44 had taken such inner brackets
    out. Neither has one now, and "not --report-only" stays an instruction."""
    monkeypatch.setenv(doctor.NO_NEXT_HINT_ENV, "1")
    assert doctor.AGAIN_UNDER_INSTALL_SH == RUN_AGAIN_INSTALL
    assert "without --report-only" in doctor.AGAIN_UNDER_INSTALL_SH
    assert "(" not in doctor._again_fix() and "(" not in doctor._STILL_NO_ANSWER
    words, minutes = doctor.helper_timed_out_words("speech")
    helper = doctor._helper_again_fix(f"{words} x", "speech")
    assert (
        helper
        == f"{RUN_AGAIN_INSTALL}; the build stops after {minutes} minutes, and if it stops again, run it "
        "when the Mac and the network are less busy"
    )
    assert "(" not in helper


def test_the_system_error_and_state_owner_fixes_nest_no_bracket(tmp_path: Path) -> None:
    """B13 round 22 Z7, Z49: ``_os_error``'s fix put ``(if the line stays, report it: ...)`` inside the
    line's ``(fix: ...)``, and round 21's state fixes their own ``(its owner and mode are right ...)``, ``(it
    is mounted read-only)`` and ``(the agent runs no sudo)``: the nesting Y20 took out elsewhere. Each says it
    after "; " or ", as" now, and still starts "the person " where it is the person's."""
    path = tmp_path / "state" / "x.db"
    errors = (
        OSError(errno.EIO, "Input/output error", str(path)),  # a path: "check that <path> ..."
        OSError(errno.EIO, "Input/output error"),  # none: "run again; ..."
    )
    fixes = [doctor._os_error("disk", exc, Severity.ERROR).fix or "" for exc in errors]
    persons = [
        doctor._search_fix(path, folder=False),
        doctor._search_fix(path.parent),
        doctor.read_only_fix(path),
        doctor._persons_chown(path.parent),
    ]
    for fix in fixes + persons:
        assert fix and "(" not in fix and ")" not in fix, fix
    assert all(doctor.is_persons_fix(fix) for fix in persons), persons
    assert fixes[1].startswith("run again; if the line stays, report it: "), fixes[1]


def test_the_run_again_crash_and_owner_only_fixes_nest_no_bracket(monkeypatch: pytest.MonkeyPatch) -> None:
    """B13 round 23 A94: by hand, ``_again_fix`` read ``~/.local/bin/agentsync status (run it again as it
    is)``, the crash fix ``... status -v (prints the traceback: ...)`` and the permissions fix ``... sync (it
    makes these owner-only)``: a bracket inside the line's own ``(fix: ...)``, and a "command" bash rejects
    when copied. Each starts ``run <command>`` now and says why after ", which", the install.sh form."""
    monkeypatch.delenv(doctor.NO_NEXT_HINT_ENV, raising=False)
    again = doctor._again_fix()
    assert again.startswith(RUN_AGAIN + "; if this line comes back: "), again
    for fix in (again, doctor._CRASH_FIX, CRASH_FIX):
        assert "(" not in fix and ")" not in fix, fix
    assert doctor._CRASH_FIX == CRASH_FIX
    assert not doctor.is_persons_fix(again) and not doctor.is_persons_fix(CRASH_FIX)


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
    pandoc = doctor._pandoc_route(sample_config)[0]
    monkeypatch.setattr(doctor, "_run", _silent(lambda argv: argv[0] == str(pandoc)))
    results = run_checks(sample_config)
    r = by_name(results)["pandoc"]
    assert (r.ok, r.severity, r.note) == (False, Severity.ERROR, None)
    assert r.detail == (
        f"{pandoc} --version did not answer within 60s: {OUT_OF_TIME} (a new copy's first start can take a "
        "minute, and any start can on a busy Mac)"
    )
    # B13 round 16 T17, T77: a re-run that installed nothing met it too (110 s at load 600, round 15 S17)
    assert "newly installed" not in r.detail and "under a second" not in r.detail
    assert "crashed" not in r.detail and "TimeoutExpired" not in r.detail
    assert r.fix == RUN_AGAIN + COMES_BACK + PANDOC_FIX
    assert_fix_parses(r.fix)
    assert [x.name for x in results if x.name != "materialise.policy"] == [
        n for n in EXPECTED_ORDER if n != "materialise.policy"
    ], "every other check still ran"

    monkeypatch.setenv(doctor.NO_NEXT_HINT_ENV, "1")  # as scripts/install.sh runs status
    r = by_name(run_checks(sample_config))["pandoc"]
    assert r.fix == RUN_AGAIN_INSTALL + COMES_BACK + PANDOC_INSTALL_COMES_BACK
    assert format_results([r]).endswith(
        f"and any start can on a busy Mac) (fix: {RUN_AGAIN_INSTALL}{COMES_BACK}{PANDOC_INSTALL_COMES_BACK})"
    )
    assert "again" not in r.fix.split(COMES_BACK, 1)[1], "the second re-run repeats the first (U78)"


def test_the_pandoc_slow_start_reason_is_the_installers_wait_line() -> None:
    """B13 round 16 T17, T77: round 15 S17 gave install.sh's ticker words true of every run (a re-run that
    installed nothing took 110 s at load 600), and doctor's time-out line kept "the first start of a newly
    installed pandoc ..., and later ones take under a second". The two now give one reason, read here from
    install.sh's own line."""
    installer = (Path(__file__).parents[1] / "scripts" / "install.sh").read_text(encoding="utf-8")
    [line] = [ln for ln in installer.splitlines() if 'say "pandoc: still running, ' in ln]
    assert line.rstrip().endswith(f's ({doctor._PANDOC_SLOW_START})"'), line


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
    assert first["pandoc"].fix == f"{RUN_AGAIN_INSTALL}{COMES_BACK}{PANDOC_INSTALL_COMES_BACK}"
    assert first["git"].fix == f"{RUN_AGAIN_INSTALL}{COMES_BACK}{STILL_NO_ANSWER}"
    for line in (first["pandoc"], first["git"]):
        said = f"{line.detail} (fix: {line.fix})"
        assert "found no fault" not in said and "nothing needs changing" not in said, said
        assert said.count(COMES_BACK) == 1, said


def test_the_pandoc_check_waits_60_seconds(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    """The limit scripts/install.sh's own first start of pandoc stays outside of (it waits 300 s)."""
    seen: list[float] = []
    pandoc = doctor._pandoc_route(sample_config)[0]

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
    assert disk.fix == f"check that {seen[0]} is there and can be opened, then run again; {stays}"
    policy = by_name(cli._extra_checks(sample_config, offline=True))["policy"]
    assert policy.detail == "stopped on a system error: ConnectionResetError: Connection reset by peer"
    assert policy.fix == f"run again; {stays}"
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
    pandoc = doctor._pandoc_route(sample_config)[0]
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
    assert not r.ok and r.detail == "1 tracked symlink: link.md", r.detail  # no "(s)" (B13 round 17 U71)
    (repo / "link2.md").symlink_to("real.md")
    subprocess.run([GIT, "-C", str(repo), "add", "-A"], check=True)
    subprocess.run([GIT, "-C", str(repo), "commit", "-qm", "y"], check=True)
    r = by_name(run_checks(sample_config))["docs_repo.symlinks"]
    assert r.detail == "2 tracked symlinks: link.md, link2.md", r.detail


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


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a file of any mode")
def test_state_files_this_account_cannot_open_fail_by_their_cause(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 19 W64: the check flagged only group and other bits, so mode 0000, 0400 and another
    account's file all read "are 0600". This account's own file it cannot read and write fails with
    ``chmod 600``; another account's with the folder's ``sudo chown -R`` (a per-file chown leaves the sudo
    sync's other files); "are 0600" is said only of mode 0600."""
    db = sample_config.state_paths.db
    db.write_bytes(b"")
    for mode in (0o000, 0o400):
        db.chmod(mode)
        r = by_name(run_checks(sample_config))["state_dir.files"]
        assert not r.ok and f"this account cannot read and write it: manifest.sqlite ({mode:04o})" in r.detail
        assert (r.fix or "").startswith("chmod 600 "), r.fix
    db.chmod(0o700)
    r = by_name(run_checks(sample_config))["state_dir.files"]
    assert r.ok and "are 0600" not in r.detail and "is 0600" not in r.detail, r.detail
    assert "manifest.sqlite (0700)" in r.detail, r.detail
    db.chmod(0o600)
    me = os.getuid()
    monkeypatch.setattr(doctor.os, "getuid", lambda: me + 1)
    results = by_name(run_checks(sample_config))
    r = results["state_dir.files"]
    assert not r.ok and "owned by another account" in r.detail and f"(owner uid {me}, mode 0600)" in r.detail
    assert "so this one cannot open it: manifest.sqlite" in r.detail, r.detail
    # B13 round 20 X74: the folder's chown is a sudo, the person's: the setup prompt bars the agent's
    root = doctor._shell_path(sample_config.state_paths.db.parent)
    assert r.fix == f"the person runs sudo chown -R {me + 1} {root}; the agent runs no sudo", r.fix
    assert results["state_dir"].fix == r.fix
    assert doctor.is_persons_fix(r.fix) and doctor.is_persons_fix(results["state_dir"].fix)


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a file of any mode")
def test_state_files_say_it_of_one_file_and_them_of_more(sample_config: Config) -> None:
    """B13 round 20 X17: the state_dir.files lines said "them" of one file ("cannot read and write them:
    manifest.sqlite (0000)") and "are 0600" of the manifest alone. One file is "it" and "is"; two are "them"
    and "are"."""
    db = sample_config.state_paths.db
    tokens = sample_config.state_paths.token_cache_fallback
    db.write_bytes(b"")
    db.chmod(0o600)
    r = by_name(run_checks(sample_config))["state_dir.files"]
    assert r.ok and r.detail == "manifest.sqlite is 0600", r.detail
    tokens.write_bytes(b"")
    tokens.chmod(0o600)
    r = by_name(run_checks(sample_config))["state_dir.files"]
    assert r.ok and r.detail == f"manifest.sqlite, {tokens.name} are 0600", r.detail
    db.chmod(0)
    tokens.chmod(0)
    try:
        r = by_name(run_checks(sample_config))["state_dir.files"]
    finally:
        db.chmod(0o600)
        tokens.chmod(0o600)
    assert not r.ok and r.detail == (
        f"this account cannot read and write them: manifest.sqlite (0000), {tokens.name} (0000)"
    ), r.detail


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a file of any mode")
def test_a_manifest_this_account_cannot_write_is_not_probed_so_one_chmod_fixes_it(
    sample_config: Config,
) -> None:
    """B13 round 20 X78: with a WAL manifest at 0400, status's read-only damage probe ran after the files
    check and created manifest.sqlite-wal and -shm at 0400 (sqlite gives them the file's mode), so after the
    one ``chmod 600`` the FAIL named, the next status FAILed on the two side files, and a sync could not write
    until a second chmod. A manifest this account cannot write is not probed; one chmod is the whole fix."""
    db = sample_config.state_paths.db
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (k TEXT)")
    conn.commit()
    conn.close()
    sides = [db.with_name(db.name + s) for s in ("-wal", "-shm")]
    assert not any(p.exists() for p in sides)
    db.chmod(0o400)
    try:
        r = by_name(run_checks(sample_config))["state_dir.files"]
        assert not r.ok and r.detail == "this account cannot read and write it: manifest.sqlite (0400)", (
            r.detail
        )
        assert r.fix == f"chmod 600 {doctor._shell_path(db)}", r.fix
        assert not any(p.exists() for p in sides), "the probe created side files"
        # every caller of the probe, a sync's state check included, leaves them uncreated too
        assert doctor.manifest_unreadable(db) is None and not any(p.exists() for p in sides)
    finally:
        db.chmod(0o600)
    results = by_name(run_checks(sample_config))
    assert results["state_dir.files"].ok, results["state_dir.files"]
    assert "state_dir.manifest" not in results
    writer = sqlite3.connect(db)
    writer.execute("INSERT INTO t VALUES ('a')")
    writer.commit()
    writer.close()


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a folder of any mode")
def test_a_state_folder_this_account_cannot_look_inside_fails_by_its_mode_and_the_rest_say_not_checked(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 20 X73: with the state folder at mode 0000 the files loop's lstat raised PermissionError, so
    the whole check became a generic "could not read <db> ... report it" FAIL that dropped the folder's own
    ``chmod 700`` line, and the lock, heartbeat and vanished-sources checks each FAILed the same way. The
    folder's mode FAIL is the answer; the checks that read files in it say not checked, an [info] line."""
    root = sample_config.state_paths.root
    sample_config.state_paths.db.write_bytes(b"")
    root.chmod(0)
    try:
        results = run_checks(sample_config)
        assert doctor.state_folder_fix(root) == f"chmod 700 {doctor._shell_path(root)}"
    finally:
        root.chmod(0o700)
    named = by_name(results)
    state = named["state_dir"]
    assert (
        not state.ok
        and "has mode 0000" in state.detail
        and state.fix == f"chmod 700 {doctor._shell_path(root)}"
    )
    assert "state_dir.files" not in named and "state_dir.manifest" not in named
    # (the permissions and logs checks read the log folder, which this test's config puts in the state
    # folder; its default is ~/Library/Logs/agentsync)
    stopped = [r for r in results if "could not read" in r.detail and r.name not in ("permissions", "logs")]
    assert stopped == [], stopped
    for group in ("config.vanished_sources", "lock", "heartbeat"):
        r = named[group]
        assert (
            not r.ok and r.severity is doctor.Severity.INFO and r.detail == doctor.STATE_FOLDER_UNCHECKED
        ), r
    # a folder this account owns at 0700 that still refuses a look inside (an ACL, a sandbox): one files FAIL
    # naming the folder, never chmod 600 on the files
    real_lstat = Path.lstat

    def refused(self: Path) -> os.stat_result:
        if self.parent == root:
            raise PermissionError(errno.EACCES, "Permission denied", str(self))
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", refused)
    named = by_name(run_checks(sample_config))
    assert named["state_dir"].ok
    files = named["state_dir.files"]
    assert not files.ok and files.detail == f"cannot look inside {root}: Permission denied", files
    assert files.fix is not None and "chmod 600" not in files.fix and doctor._shell_path(root) in files.fix
    assert doctor.state_folder_fix(root) == files.fix
    # B13 round 21, the ownership rule: an ACL deny is the person's, so no NEXT hands it to the agent
    assert doctor.is_persons_fix(files.fix), files.fix
    assert cli._fail_step(files).startswith("the state_dir.files check failed: its fix on the [FAIL] line ")


@pytest.mark.skipif(sys.platform != "darwin" or os.geteuid() == 0, reason="macOS ACLs; root ignores them")
def test_a_state_file_an_acl_refuses_at_mode_0600_is_the_persons(sample_config: Config) -> None:
    """B13 round 21, the ownership rule (lane P's measure): a 0600 manifest an ACL denies this account read
    and write got ``chmod 600 <db>``, which changes nothing, from state_dir.files and ``state_path_fix``. An
    own file whose mode already gives it access is refused by an ACL or a privacy setting: the person's."""
    root, db = sample_config.state_paths.root, sample_config.state_paths.db
    with Manifest(db) as manifest:
        manifest.sync_sources(list(sample_config.sources))
    db.chmod(0o600)
    user = os.environ.get("USER") or os.getlogin()
    subprocess.run(["/bin/chmod", "+a", f"user:{user} deny read,write", str(db)], check=True)
    try:
        assert not os.access(db, os.R_OK | os.W_OK), "the ACL is in force"
        named = by_name(run_checks(sample_config))
        fix = doctor.state_path_fix(Path(os.path.realpath(root)), Path(os.path.realpath(db)))
    finally:
        subprocess.run(["/bin/chmod", "-N", str(db)], check=True)
    files = named["state_dir.files"]
    assert not files.ok and files.detail.endswith(f"by an ACL or a privacy setting: {db.name} (0600)"), files
    assert files.fix == doctor._search_fix(db, folder=False) and doctor.is_persons_fix(files.fix), files.fix
    assert "chmod" not in files.fix.replace("chmod -N", "") and "state_dir.manifest" not in named
    assert doctor.is_persons_fix(fix) and "chmod 600" not in fix, fix


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a folder of any mode")
def test_a_state_folder_under_a_folder_this_account_cannot_search_is_the_persons(
    tmp_path: Path, sample_config: Config
) -> None:
    """B13 round 21, the ownership rule (lane P's seam note): a state folder whose parent refuses this account
    a search was given "make <root> this account's folder at mode 700", which ``is_persons_fix`` read as the
    agent's, while install.sh's step 1 tail calls it the person's. Its fix starts "the person "; this
    account's own folder at a wrong mode keeps its ``chmod 700``, the agent's."""
    parent = tmp_path / "shut"
    root = parent / "state"
    root.mkdir(parents=True, mode=0o700)
    root.chmod(0o755)
    assert doctor.state_folder_fix(root) == f"chmod 700 {doctor._shell_path(root)}"
    assert not doctor.is_persons_fix(doctor.state_folder_fix(root))
    config = dataclasses.replace(sample_config, state_dir=root)
    parent.chmod(0)
    try:
        fix = doctor.state_folder_fix(root)
        results = doctor._check_state_dir(config)
        refused = doctor.state_path_fix(root, root / "manifest.sqlite")
    finally:
        parent.chmod(0o700)
    want = (
        f"the person gives this account search access to {doctor._shell_path(parent)}, a folder above "
        f"{doctor._shell_path(root)} that refuses it one, then run the same command again; it is this "
        f"account's own folder at mode 0000, so chmod u+rwx {doctor._shell_path(parent)} gives it"
    )
    assert fix == want and doctor.is_persons_fix(fix), fix
    # status says the same (the reviewer's: its bare stat fell to the generic "report it" FAIL), and sync's
    # error line, which reads state_path_fix
    (state,) = results
    assert (state.name, state.ok, state.fix) == ("state_dir", False, want), state
    assert state.detail == f"cannot reach {root}: Permission denied", state
    assert refused == want


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a folder of any mode")
def test_the_parent_fix_names_a_chmod_only_for_this_accounts_own_folder_shut_by_its_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 22 Z14, Z72: the fix of a state folder this account cannot reach named no command, though
    a ``chmod u+rwx`` of this account's own folder at 0000 above it is the whole fix; and with no folder above
    refusing (an ACL on the state folder itself) it named the parent, which refuses nothing. It stays the
    person's either way, by the round 21 ownership rule."""
    parent = tmp_path / "shut"
    root = parent / "state"
    root.mkdir(parents=True, mode=0o700)
    parent.chmod(0o600)  # read but no search: the mode refuses it
    try:
        own = doctor._parent_fix(root)
        real_getuid = os.getuid()
        monkeypatch.setattr(doctor.os, "getuid", lambda: real_getuid + 1)
        theirs = doctor._parent_fix(root)
        monkeypatch.undo()
    finally:
        parent.chmod(0o700)
    tail = f"; it is this account's own folder at mode 0600, so chmod u+rwx {doctor._shell_path(parent)}"
    assert own.endswith(f"{tail} gives it"), own
    assert theirs.endswith("that refuses it one, then run the same command again"), theirs
    # an ACL that refuses the search at mode 0700: a chmod changes nothing, so none is named
    real_access = os.access
    monkeypatch.setattr(doctor.os, "access", lambda p, m: False if Path(p) == parent else real_access(p, m))
    acl = doctor._parent_fix(root)
    assert acl.endswith("then run the same command again") and "chmod" not in acl, acl
    # no folder above refuses: the folder itself, never its parent
    monkeypatch.setattr(doctor.os, "access", lambda p, m: True)
    itself = doctor._parent_fix(root)
    assert itself == (
        "the person gives this account read-attributes, read, write and search access to the folder "
        f"{doctor._shell_path(root)}; every folder above it allows a search, so an ACL or a privacy setting "
        f"on the folder itself refuses it, and chmod -N {doctor._shell_path(root)} removes its ACL"
    ), itself
    for fix in (own, theirs, acl, itself):
        assert doctor.is_persons_fix(fix) and "(" not in fix, fix


@pytest.mark.skipif(sys.platform != "darwin" or os.geteuid() == 0, reason="macOS ACLs; root ignores them")
def test_an_acl_on_this_accounts_own_folder_or_file_names_the_chmod_that_takes_it_off(tmp_path: Path) -> None:
    """B13 round 23 A15, measured: an ACL that denies this account its own 0700 state folder (or 0600 file)
    got a fix that said how to see the ACL (``ls -led``) but not how to remove it, while the parent fix names
    ``chmod -N``. Run by the owner it takes the ACL off and the folder lists again; it stays the person's."""
    user = os.environ.get("USER") or os.getlogin()
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    db = root / "manifest.sqlite"
    db.write_bytes(b"")
    db.chmod(0o600)
    subprocess.run(["/bin/chmod", "+a", f"user:{user} deny read,write", str(db)], check=True)
    try:
        file_fix = doctor.state_path_fix(root, db)
    finally:
        subprocess.run(["/bin/chmod", "-N", str(db)], check=True)
    subprocess.run(
        ["/bin/chmod", "+a", f"user:{user} deny list,search,add_file,delete_child", str(root)], check=True
    )
    try:
        folder_fix = doctor.state_path_fix(root, db)
        assert subprocess.run(["/bin/chmod", "-N", str(root)], check=False).returncode == 0
        assert [p.name for p in root.iterdir()] == ["manifest.sqlite"]  # the owner's chmod -N lists it again
    finally:
        subprocess.run(["/bin/chmod", "-N", str(root)], check=False)
    shown_db, shown_root = doctor._shell_path(db), doctor._shell_path(root)
    assert file_fix.endswith(f"; ls -le {shown_db} shows the ACL, and chmod -N {shown_db} removes it"), (
        file_fix
    )
    assert folder_fix.endswith(
        f"; ls -led {shown_root} shows the ACL, and chmod -N {shown_root} removes it"
    ), folder_fix
    for fix in (file_fix, folder_fix):
        assert doctor.is_persons_fix(fix) and "(" not in fix, fix


@pytest.mark.skipif(sys.platform != "darwin" or os.geteuid() == 0, reason="macOS ACLs; root ignores them")
def test_a_state_folder_whose_own_acl_denies_readattr_is_named_not_its_parent(sample_config: Config) -> None:
    """B13 round 22 Z72, measured: a 0700 state folder this account owns with an ACL that denies it readattr
    refuses its ``stat``, so state_dir said to ask for a search of its parent, which allows one."""
    root = sample_config.state_paths.root
    user = os.environ.get("USER") or os.getlogin()
    subprocess.run(["/bin/chmod", "+a", f"user:{user} deny readattr", str(root)], check=True)
    try:
        results = doctor._check_state_dir(sample_config)
        fix = doctor.state_folder_fix(root)
    finally:
        subprocess.run(["/bin/chmod", "-N", str(root)], check=True)
    (state,) = results
    assert not state.ok and state.fix == fix == doctor._parent_fix(root), state
    head = "the person gives this account read-attributes, read, write and search access to the folder"
    assert fix.startswith(f"{head} {doctor._shell_path(root)}; "), fix


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a folder of any mode")
def test_a_log_folder_in_a_state_folder_under_a_folder_that_refuses_a_search_is_not_checked(
    tmp_path: Path, sample_config: Config
) -> None:
    """B13 round 22 Z60: with a log_dir set inside the state folder (this config's), the logs and
    permissions checks stopped on its ``is_dir`` and ``exists`` under a folder above that refuses a search,
    generic "report it" FAILs, read as the agent's, which took status's NEXT. The log folder is not
    checked, and a check that still stops on a path in the state folder gets that path's fix (``stopped``),
    here the person's."""
    parent = tmp_path / "shut"
    root = parent / "state"
    root.mkdir(parents=True, mode=0o700)
    config = dataclasses.replace(sample_config, state_dir=root, log_dir=root / "logs")
    (root / "logs").mkdir(mode=0o700)
    parent.chmod(0)
    try:
        results = run_checks(config)
        refused = doctor.stopped(
            config, "lock", PermissionError(errno.EACCES, "Permission denied", str(root))
        )
        want = doctor._parent_fix(root)
    finally:
        parent.chmod(0o700)
    named = by_name(results)
    assert named["logs"].severity is Severity.INFO and named["logs"].detail == doctor.STATE_FOLDER_UNCHECKED
    errors = [r for r in results if not r.ok and r.severity is Severity.ERROR]
    assert [r.name for r in errors] == ["state_dir"], errors
    assert not any("could not read" in r.detail for r in results), results
    assert (refused.name, refused.ok) == ("lock", False) and refused.fix == want, refused
    assert doctor.is_persons_fix(refused.fix)
    # any other error keeps the generic line
    other = doctor.stopped(config, "lock", OSError(errno.EIO, "Input/output error", str(tmp_path / "x")))
    assert other.detail.startswith("could not read "), other


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a folder of any mode")
def test_an_own_state_folder_at_0755_that_still_refuses_it_gets_the_persons_fix_beside_its_chmod(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 22 Z64: an own state folder at 0755 that an ACL refuses got ``chmod 700`` alone from status,
    the agent's, which leaves it refused, while install.sh's step 1 and step 2 (``mode & 0o700``) call it the
    person's. ``state_folder_fix`` tests the access before the mode, and state_dir keeps its loose-mode line
    beside the person's ``cannot look inside`` line; an accessible own folder at 0755 is still ``chmod
    700``."""
    root = sample_config.state_paths.root
    sample_config.state_paths.db.write_bytes(b"")
    root.chmod(0o755)
    try:
        assert doctor.state_folder_fix(root) == f"chmod 700 {doctor._shell_path(root)}"  # nothing refuses
        real_access, real_lstat = os.access, Path.lstat
        monkeypatch.setattr(doctor.os, "access", lambda p, m: False if Path(p) == root else real_access(p, m))

        def refused(self: Path) -> os.stat_result:
            if self.parent == root:
                raise PermissionError(errno.EACCES, "Permission denied", str(self))
            return real_lstat(self)

        monkeypatch.setattr(Path, "lstat", refused)
        fix = doctor.state_folder_fix(root)
        named = {(r.name, r.fix): r for r in doctor._check_state_dir(sample_config)}
    finally:
        monkeypatch.undo()
        root.chmod(0o700)
    assert fix == doctor._search_fix(root) and doctor.is_persons_fix(fix), fix
    assert ("state_dir", f"chmod 700 {doctor._shell_path(root)}") in named, named
    files = named[("state_dir.files", fix)]
    assert files.detail == f"cannot look inside {root}: Permission denied", files


@pytest.mark.skipif(sys.platform != "darwin" or os.geteuid() == 0, reason="macOS ACLs; root ignores them")
def test_an_own_state_folder_at_0755_an_acl_refuses_is_the_persons_as_install_sh_says(
    sample_config: Config,
) -> None:
    """B13 round 22 Z64, measured: ``chmod +a 'everyone deny search,list,add_file,delete_child'`` on an own
    folder at 0755 holding a manifest. install.sh's step 1 and step 2 read ``mode & 0o700``, the person's."""
    root = sample_config.state_paths.root
    sample_config.state_paths.db.write_bytes(b"")
    root.chmod(0o755)
    deny = "everyone deny search,list,add_file,delete_child"
    subprocess.run(["/bin/chmod", "+a", deny, str(root)], check=True)
    try:
        fix = doctor.state_folder_fix(root)
        results = doctor._check_state_dir(sample_config)
    finally:
        subprocess.run(["/bin/chmod", "-N", str(root)], check=True)
        root.chmod(0o700)
    # A15: the owner's chmod -N, the person's, is named; no chmod 600 or 700 of the agent's
    assert doctor.is_persons_fix(fix) and "chmod" not in fix.replace("chmod -N", ""), fix
    assert [(r.name, r.fix) for r in results] == [
        ("state_dir", f"chmod 700 {doctor._shell_path(root)}"),
        ("state_dir.files", fix),
    ], results


@pytest.mark.skipif(not hasattr(os, "ST_RDONLY"), reason="statvfs flags")
def test_a_state_folder_on_a_read_only_volume_gets_the_volume_fix_whatever_its_mode(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 22 Z78: a state folder at 0755 on a read-only volume got ``chmod 700``, which fails there,
    as the mode was tested before the volume; so did a folder or file below it at a wrong mode. The volume
    fix, the person's, comes first."""
    root = sample_config.state_paths.root
    below = root / "governance"
    below.mkdir(mode=0o700)
    queue = below / "purge-queue.json"
    queue.write_text("{}\n", encoding="utf-8")
    root.chmod(0o755)
    below.chmod(0o500)
    queue.chmod(0o400)
    try:
        monkeypatch.setattr(doctor, "_read_only", lambda p: True)
        folder = doctor.state_folder_fix(root)
        real_access = os.access
        monkeypatch.setattr(doctor.os, "access", lambda p, m: True if Path(p) == root else real_access(p, m))
        sub = doctor.state_path_fix(root, below / "x")
        below.chmod(0o700)
        file = doctor.state_path_fix(root, queue)
    finally:
        below.chmod(0o700)
        root.chmod(0o700)
    assert folder == doctor.read_only_fix(root), folder
    assert sub == doctor.read_only_fix(below), sub
    assert file == doctor.read_only_fix(queue), file
    assert all(doctor.is_persons_fix(f) for f in (folder, sub, file))


@pytest.mark.skipif(not hasattr(os, "ST_RDONLY"), reason="statvfs flags")
def test_a_state_file_on_a_read_only_volume_is_the_persons_not_an_acl(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 21 lane B's reviewer: a 0600 manifest on a read-only volume fails ``os.access`` for writing
    as an ACL does, and got the ACL words. It names the read-only volume, the person's."""
    root, db = sample_config.state_paths.root, sample_config.state_paths.db
    db.write_bytes(b"")
    db.chmod(0o600)
    real_access = os.access
    monkeypatch.setattr(doctor.os, "access", lambda p, m: False if Path(p) == db else real_access(p, m))
    monkeypatch.setattr(doctor, "_read_only", lambda p: Path(p) == db)
    files = by_name(doctor._check_state_dir(sample_config))["state_dir.files"]
    assert files.detail == f"on a read-only volume, so this account cannot write {db.name} (0600)", files
    assert files.fix == doctor.read_only_fix(db) and doctor.is_persons_fix(files.fix)
    assert doctor.state_path_fix(root, db) == doctor.read_only_fix(db)


def test_every_state_file_on_a_read_only_volume_is_one_line_with_one_fix(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 23 A85: the manifest, its -wal and its -shm on a read-only volume gave three state_dir.files
    FAILs, each with the same person's fix, as the chmod's line already joins its files. One line names all
    three, with the volume's fix of the first, the fix sync's ``error: state:`` line gives
    (``refused_files_fix``); an ACL on one of them keeps its own line."""
    db = sample_config.state_paths.db
    files = [db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")]
    for f in files:
        f.write_bytes(b"")
        f.chmod(0o600)
    real_access = os.access
    monkeypatch.setattr(doctor.os, "access", lambda p, m: False if Path(p) in files else real_access(p, m))
    monkeypatch.setattr(doctor, "_read_only", lambda p: Path(p) in files)
    failed = [r for r in doctor._check_state_dir(sample_config) if not r.ok]
    (line,) = failed
    names = ", ".join(f"{f.name} (0600)" for f in files)
    assert line.name == "state_dir.files", failed
    assert line.detail == f"on a read-only volume, so this account cannot write {names}", line.detail
    assert line.fix == doctor.read_only_fix(db) == doctor.refused_files_fix(sample_config, files)
    monkeypatch.setattr(doctor, "_read_only", lambda p: Path(p) in files[:2])  # the -shm: an ACL
    failed = [r for r in doctor._check_state_dir(sample_config) if not r.ok]
    assert [r.detail.split(" so ")[0].split(":")[0] for r in failed] == [
        "on a read-only volume,",
        "refused to this account by an ACL or a privacy setting",
    ], failed


def test_a_state_file_without_read_or_write_on_a_read_only_volume_joins_the_volume_line(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 24 B65: on a read-only volume a 0400 file got the agent's ``chmod 600`` (its mode was tested
    before the volume), which fails with EROFS there, and beside a 0600 manifest A85's one volume line split
    in two. The volume comes first, as state_path_fix and refused_files_fix test it."""
    db = sample_config.state_paths.db
    wal = db.with_name(db.name + "-wal")
    db.parent.mkdir(parents=True, exist_ok=True)
    for f, mode in ((db, 0o600), (wal, 0o400)):
        f.write_bytes(b"")
        f.chmod(mode)
    files = [db, wal]
    real_access = os.access
    monkeypatch.setattr(doctor.os, "access", lambda p, m: False if Path(p) in files else real_access(p, m))
    monkeypatch.setattr(doctor, "_read_only", lambda p: Path(p) in files)
    failed = [r for r in doctor._check_state_dir(sample_config) if not r.ok]
    (line,) = failed
    assert "manifest.sqlite (0600), manifest.sqlite-wal (0400)" in line.detail, line.detail
    assert line.fix == doctor.read_only_fix(db) == doctor.refused_files_fix(sample_config, files)
    wal.unlink()
    db.chmod(0o400)
    (line,) = [r for r in doctor._check_state_dir(sample_config) if not r.ok]
    assert line.fix == doctor.read_only_fix(db) and not (line.fix or "").startswith("chmod "), line
    db.chmod(0o600)


def test_a_dangling_manifest_link_is_judged_by_what_a_sync_does_never_as_an_acl(
    sample_config: Config,
) -> None:
    """B13 round 24 B64: a symlink to a missing file at manifest.sqlite was "refused to this account by an
    ACL or a privacy setting" (the link's own mode is 0755), with a ``chmod -N`` that fails on a link to
    nothing. A link into a folder a sync can write is no FAIL (the first sync creates the file); one into a
    folder that is missing is the person's, to point it elsewhere or remove it; after earlier passes it is a
    lost manifest, which no sync replaces through the link."""
    from agentsync.manifest import LOST_MANIFEST, manifest_lost  # noqa: PLC0415

    db = sample_config.state_paths.db
    db.parent.mkdir(parents=True, exist_ok=True)
    for side in ("", "-wal", "-shm"):
        db.with_name(db.name + side).unlink(missing_ok=True)

    def failed() -> list[doctor.CheckResult]:
        out = [r for r in doctor._check_state_dir(sample_config) if not r.ok]
        assert not any("ACL" in r.detail or "chmod -N" in (r.fix or "") for r in out), out
        return out

    db.symlink_to(db.parent / "elsewhere.sqlite")  # (a) a folder the sync can write: no FAIL
    assert failed() == []
    db.unlink()
    db.symlink_to(db.parent / "gone" / "manifest.sqlite")  # (b) its folder is missing: the person's
    (line,) = failed()
    assert line.name == "state_dir.manifest" and "is a symlink to " in line.detail, line
    assert line.fix is not None and line.fix.startswith("the person points the link at an existing file")
    assert doctor.is_persons_fix(line.fix)
    db.unlink()
    db.symlink_to(db.parent / "elsewhere.sqlite")  # (c) earlier passes recorded: a lost manifest
    (db.parent / "heartbeat.json").write_text(
        '{"contoso": {"last_success_at": "2026-10-01T00:00:00Z"}}', encoding="utf-8"
    )
    assert manifest_lost(db)
    (line,) = failed()
    assert line.name == "state_dir.manifest" and line.detail.endswith(LOST_MANIFEST), line
    assert line.fix == doctor.LOST_MANIFEST_FIX
    # B13 round 25 D71: (d) earlier passes and a link into a missing folder: neither LOST_MANIFEST_FIX move
    # works through the link, so one FAIL names the link's removal first, then those moves; no second line
    db.unlink()
    db.symlink_to(db.parent / "gone" / "manifest.sqlite")
    assert manifest_lost(db)
    (line,) = failed()
    assert line.name == "state_dir.manifest" and line.detail.endswith(f", and {LOST_MANIFEST}"), line
    assert line.fix == (
        f"the person removes the link first: rm {doctor._shell_path(db)}; then "
        + doctor.LOST_MANIFEST_FIX.removeprefix("the person ")
    )
    assert doctor.is_persons_fix(line.fix)


SIDES = {
    "-wal": "the state file's write-ahead log",
    "-shm": "the state file's shared-memory file",
}


@pytest.mark.parametrize("side", ["-wal", "-shm", "token cache"])
def test_a_side_file_that_is_not_a_file_is_one_fail_named_by_its_role_with_a_copy_kept(
    sample_config: Config, side: str
) -> None:
    """B13 round 25 D6, D22, D10: a folder at the -shm read "is a folder, not the state file" (the
    manifest's words), its fix "the person moves ... aside" with no "keeping a copy", which install.sh's step
    1 and step 2 say, and an "[ok] state_dir.files ... are 0600" followed under the same name, one check
    counted twice. One FAIL, the file named by its role, the person's move aside keeping a copy, and no ok
    line beside it."""
    sp = sample_config.state_paths
    db = expand(sp.db)
    with Manifest(db):
        pass
    for p in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm"), sp.token_cache_fallback):
        if p.exists():
            p.chmod(0o600)
    there = expand(sp.token_cache_fallback) if side == "token cache" else db.with_name(db.name + side)
    there.unlink(missing_ok=True)
    there.mkdir(mode=0o700)
    files = [r for r in doctor._check_state_dir(sample_config) if r.name == "state_dir.files"]
    (line,) = files
    role = SIDES.get(side, "the token cache")
    assert not line.ok and line.detail == f"{there} is a folder, not a file ({role})", line
    assert line.fix == f"the person moves {doctor._shell_path(there)} aside, keeping a copy", line
    assert doctor.is_persons_fix(line.fix)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_a_side_that_is_a_folder_beside_a_loose_manifest_keeps_both_fails_and_no_ok(
    sample_config: Config,
) -> None:
    """B13 round 25 D10's verifier: only the ok line goes; a folder at -shm beside a 0644 manifest still gives
    two FAILs, the person's move aside and the agent's chmod 600, never one merged fix."""
    db = expand(sample_config.state_paths.db)
    with Manifest(db):
        pass
    db.with_name(db.name + "-shm").unlink(missing_ok=True)
    db.with_name(db.name + "-shm").mkdir(mode=0o700)
    db.chmod(0o644)
    files = [r for r in doctor._check_state_dir(sample_config) if r.name == "state_dir.files"]
    assert [r.ok for r in files] == [False, False], files
    assert files[0].fix is not None and files[0].fix.endswith("aside, keeping a copy"), files
    assert files[1].fix is not None and files[1].fix.startswith("chmod 600 "), files


@pytest.mark.skipif(os.geteuid() == 0, reason="root renames a folder of any mode")
@pytest.mark.parametrize("where", ["", "-shm"])
def test_a_folder_shut_to_this_account_at_a_state_file_names_its_chmod_700_first(
    sample_config: Config, where: str
) -> None:
    """B13 round 25 D72: a 0000 folder at manifest.sqlite or its -shm can be neither copied nor moved (macOS
    renames a folder only with write on it, and copies it only with read and search), so "keeping a copy, move
    it aside" failed until a chmod 700. The fix opens it first; a 0700 folder gets no such words, and the fix
    of a damaged manifest file (MANIFEST_FIX) is unchanged. Following the words ends in a moved folder."""
    db = expand(sample_config.state_paths.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    for side in ("", "-wal", "-shm"):
        db.with_name(db.name + side).unlink(missing_ok=True)
    there = db.with_name(db.name + where)
    there.mkdir(mode=0o700)
    name = "state_dir.manifest" if where == "" else "state_dir.files"

    def fix() -> str:
        (line,) = [r for r in doctor._check_state_dir(sample_config) if r.name == name and not r.ok]
        assert line.fix is not None and doctor.is_persons_fix(line.fix), line
        return line.fix

    open_fix = fix()
    assert "chmod" not in open_fix, open_fix
    there.chmod(0)
    try:
        shut_fix = fix()
    finally:
        there.chmod(0o700)
    opens = f"the person runs chmod 700 {doctor._shell_path(there)} first, which is reversible"
    assert shut_fix.startswith(opens), shut_fix
    if where == "":
        assert shut_fix.endswith(doctor.MANIFEST_FIX.removeprefix("the person ")), shut_fix
    else:
        assert shut_fix.endswith(f"; then moves {doctor._shell_path(there)} aside, keeping a copy"), shut_fix
    there.chmod(0)
    try:  # the words, followed: the chmod, a copy, the move, each exit 0
        for cmd in (["chmod", "700", str(there)], ["cp", "-R", str(there), str(there) + ".copy"]):
            assert subprocess.run(cmd, check=False).returncode == 0, cmd
        there.rename(there.with_name(there.name + ".aside"))
    finally:
        if there.exists():
            there.chmod(0o700)
    assert not there.exists()


def test_manifest_fix_says_to_move_heartbeat_json_aside_in_install_shs_words() -> None:
    """B13 round 25 D73: "move it aside, with heartbeat.json beside it" read as where the heartbeat is, not
    as a move, and moving the manifest alone stops the next sync on the lost-manifest guard; install.sh says
    "move the folder aside and heartbeat.json with it"."""
    assert (
        "or move it aside and heartbeat.json with it, so the next sync starts a new one"
        in doctor.MANIFEST_FIX
    )
    assert "beside it" not in doctor.MANIFEST_FIX and doctor.is_persons_fix(doctor.MANIFEST_FIX)


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a folder of any mode")
def test_two_sources_over_a_shut_state_folder_say_not_checked_for_shared_content(
    sample_config: Config,
) -> None:
    """B13 round 21 Y11, Y26 (one defect): X73's configs had one source, so ``_check_shared_content`` returned
    before its ``db.exists()``, which on Python 3.11 raises EACCES over a folder this account cannot search;
    with two live sources status gave a second, generic "could not read <db> ... report it" FAIL beside
    state_dir's, and the report filed it as unexpected. It says not checked, as its siblings do; a manifest at
    0000 in a searchable folder still gives nothing."""
    first = sample_config.sources[0]
    cfg = dataclasses.replace(
        sample_config,
        sources=(first, dataclasses.replace(first, id="contoso-b", path=first.path.parent / "b")),  # type: ignore[union-attr]
    )
    root, db = cfg.state_paths.root, cfg.state_paths.db
    with Manifest(db) as manifest:
        manifest.sync_sources(list(cfg.sources))
    root.chmod(0)
    try:
        results = run_checks(cfg)
    finally:
        root.chmod(0o700)
    shared = [r for r in results if r.name == "sources.shared_content"]
    assert [(r.ok, r.severity, r.detail) for r in shared] == [
        (False, Severity.INFO, doctor.STATE_FOLDER_UNCHECKED)
    ], shared
    stopped = [r for r in results if "could not read" in r.detail and r.name not in ("permissions", "logs")]
    assert stopped == [], stopped
    # (permissions, logs and the disk check read the log folder, which this test's config puts in the state
    # folder)
    assert "sources.shared_content" not in {r.name for r in results if r.severity is Severity.ERROR}
    # one source: no new line where the check never printed one
    root.chmod(0)
    try:
        assert "sources.shared_content" not in by_name(run_checks(sample_config))
    finally:
        root.chmod(0o700)
    db.chmod(0)
    try:
        assert doctor._check_shared_content(cfg) == []
    finally:
        db.chmod(0o600)


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


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a folder of any mode")
def test_a_token_cache_this_account_cannot_open_is_the_state_folders_not_a_logout(
    sample_config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 21 Y42: X73's not-checked list left out the graph check, whose MSAL read of the token cache
    in the state folder raised PermissionError over a folder at 0000, so graph.auth FAILed (a live Graph
    source) with ``logout && login``, which stops on the same refusal; and with another account's folder,
    ``_fail_steps`` put that agent's step ahead of the person's chown. Over a shut folder graph.auth is the
    not-checked [info] line; a cache file at 0000 in an open folder gets that file's ``chmod 600``."""
    cfg = graph_config(tmp_path, sample_config)
    root = expand(cfg.state_paths.root)
    cache = expand(cfg.state_paths.token_cache_fallback)
    cache.write_bytes(b"{}")

    def msal(_cfg: Config) -> doctor._AuthProbe:
        cache.read_bytes()  # MSAL's file fallback load, which a shut folder or a 0000 file refuses
        return doctor._AuthProbe(True, "chris@example.com", "file-0600")

    monkeypatch.setattr(doctor, "_auth_status", msal)
    root.chmod(0)
    try:
        results = run_checks(cfg)
        r = by_name(results)
        assert r["graph.client_id"].ok
        auth = r["graph.auth"]
        assert (auth.ok, auth.severity, auth.detail, auth.fix) == (
            False,
            Severity.INFO,
            doctor.STATE_FOLDER_UNCHECKED,
            None,
        ), auth
        assert not any(s.startswith("the graph.auth ") for s in cli._fail_steps(results))
        me = os.getuid()
        with monkeypatch.context() as other:  # another account's folder: the person's chown is the one step
            other.setattr(doctor.os, "getuid", lambda: me + 1)
            steps = cli._fail_steps(run_checks(cfg))
        assert not any("graph.auth" in s for s in steps), steps
    finally:
        root.chmod(0o700)
    cache.chmod(0)
    try:
        auth = by_name(run_checks(cfg))["graph.auth"]
    finally:
        cache.chmod(0o600)
    assert not auth.ok and auth.severity is Severity.ERROR, auth
    assert auth.detail == "cannot read the token cache: Permission denied", auth
    assert auth.fix == f"chmod 600 {doctor._shell_path(Path(os.path.realpath(cache)))}", auth
    assert "logout" not in (auth.fix or "")


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
    assert r.severity is Severity.WARN and (r.fix or "").startswith(f"{INSTALL_SH} --confirm-install-agent")


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
    # B13 round 17 U71, U102: noun and verb by the count
    assert r.detail.startswith("1 file in local-fixture has the same content as a file in team-copy: ")
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
    # B13 round 14 Q35: the sync it names may not run (a [FAIL] or a busy lock skips it), so no promise;
    # round 15 S11: nor end in an error, which ends install.sh before the answer
    assert "if this run's sync succeeds" in r.note
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


@pytest.mark.parametrize(
    "note", [doctor._LISTING_YOURS_NOTE, doctor._LISTING_SKIPPED_NOTE, doctor.LISTING_YOURS_REPORT_NOTE]
)
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
    not on PATH). A leading ``run `` and the text after ``;`` or ``, which`` are prose (B13 round 23 A94)."""
    assert fix is not None and fix.removeprefix("run ").startswith(f"{BIN} "), fix
    command = fix.removeprefix("run ").split(";")[0].split(", which")[0].removeprefix(f"{BIN} ")
    command = re.sub(r"\([^)]*\)", "", command).removesuffix(" again as it is")
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


def test_the_logs_line_counts_its_files(sample_config: Config) -> None:
    """B13 round 17 U71, U102: "N log file(s) in" beside a count it knows."""
    log_dir = sample_config.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    for n in (1, 2):
        (log_dir / f"com.agentsync.{n}.log").write_text("x")
        r = by_name(run_checks(sample_config))["logs"]
        assert r.ok and r.detail == f"{n} log file{'s' if n > 1 else ''} in {expand(log_dir)}", r.detail


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
@pytest.mark.timing  # doctor's 60 s per codesign call: at 15 per core it ran out (B13 round 23)
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
        assert r.detail == f"the OCR helper is not built; {INSTALL_SH} builds it"
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
        f"the OCR helper is not built; {INSTALL_SH} builds it once the Command Line Tools are "
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
    assert r.fix == (f"xcode-select --install, then run {INSTALL_SH} again" if missing else None)
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
        assert r.detail == f"the media helper is not built; {INSTALL_SH} builds it"
        assert errors([r]) == []
    assert not (sample_config.cache_dir / "media").exists()
    line = format_results([r])
    assert line.startswith("[info] media") and "fix:" not in line
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: True)
    assert by_name(run_checks(sample_config))["media"].detail == (
        f"the media helper is not built; {INSTALL_SH} builds it once the Command Line Tools are "
        "installed (xcode-select --install)"
    )


def test_media_reads_the_helper_under_the_configured_cache_dir(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _real_media(monkeypatch)
    helper = ocr._helper_path(sample_config.cache_dir, media.MEDIA)
    fake_media(helper.parent, name=helper.name)
    r = by_name(run_checks(sample_config))["media"]
    assert r.ok and r.detail == f"media helper: ready ({MEDIA_READY})"
    assert by_name(run_checks(sample_config))["docs_repo.permissions"].ok, "the helper is owner-only"
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: False)
    fake_media(helper.parent, version_exit=3, name=helper.name)  # built, and it stopped answering
    r = by_name(run_checks(sample_config))["media"]
    assert (r.ok, r.severity, r.fix) == (False, Severity.WARN, None)
    assert r.detail == "media helper: not working (the media helper exited 3: no message)"
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: True)
    r = by_name(run_checks(sample_config))["media"]
    assert r.fix == f"xcode-select --install, then run {INSTALL_SH} again"
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
    fake_speech(helper.parent, name=helper.name)
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
        assert r.detail == f"speech: not-built (the speech helper is not built; {INSTALL_SH} builds it)"
        assert errors([r]) == []
    assert not (sample_config.cache_dir / "speech").exists()


def test_speech_failed_is_a_warn_never_a_fail(sample_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    _real_speech(monkeypatch)
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: True)
    helper = ocr._helper_path(sample_config.cache_dir, speech.SPEECH)
    fake_speech(helper.parent, version_exit=3, name=helper.name)
    r = by_name(run_checks(sample_config))["speech"]
    assert (r.ok, r.severity) == (False, Severity.WARN)
    assert r.detail == "speech: failed (the speech helper exited 3: no message)"
    assert r.fix == f"xcode-select --install, then run {INSTALL_SH} again"

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


def test_a_speech_build_that_ran_out_of_time_has_a_fix_and_no_other_failure_gets_one(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 18 V5, V28: a speech helper build that ran out of time left a warn with no fix. Its marker's
    reason (an older build's, without the retry clause, too) now gets install.sh again, worded as
    ``_again_fix`` is under install.sh, naming no cause; any other failed build but a failed FluidAudio fetch
    (B42) or a step a signal stopped (B13 round 25 D85) keeps no fix."""
    _real_speech(monkeypatch)
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: False)
    marker = ocr._marker(ocr._helper_path(sample_config.cache_dir, speech.SPEECH))
    marker.parent.mkdir(parents=True, exist_ok=True)
    for reason in (speech._TIMED_OUT, speech.BUILD_TIMED_OUT):
        marker.write_text(reason, encoding="utf-8")
        assert speech.build_timed_out(sample_config.cache_dir)
        monkeypatch.delenv(doctor.NO_NEXT_HINT_ENV, raising=False)
        r = by_name(run_checks(sample_config))["speech"]
        assert (r.ok, r.severity) == (False, Severity.WARN)
        # B13 round 20 X20: the marker's retry clause is dropped where the fix says the re-run
        assert r.detail == f"speech: failed ({speech.BUILD_TIMED_OUT})", r.detail
        # B13 round 19 W20: the re-run said once (the marker's clause says it too), the 20 minutes a limit
        assert r.fix == (
            f"run {INSTALL_SH} again; the build stops after 20 minutes, and if it stops again, run it "
            "when the Mac and the network are less busy"
        )
        assert "builds the speech helper again" not in r.fix and "within" not in r.fix
        monkeypatch.setenv(doctor.NO_NEXT_HINT_ENV, "1")
        r = by_name(run_checks(sample_config))["speech"]
        assert r.fix is not None and r.fix.endswith(
            "; the build stops after 20 minutes, and if it stops again, "
            "run it when the Mac and the network are less busy"
        ), r.fix
        assert r.fix.startswith(f"{RUN_AGAIN_INSTALL}; the build stops"), r.fix  # B13 round 19 W19, W48
        assert "NEXT line" not in r.fix and "--report-only" in r.fix, r.fix
    # without the developer tools the fix is theirs, and the marker's clause is the only word of the re-run
    marker.write_text(speech._TIMED_OUT, encoding="utf-8")
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: True)
    r = by_name(run_checks(sample_config))["speech"]
    assert r.fix == doctor._OCR_DEVTOOLS_FIX and r.detail == f"speech: failed ({speech._TIMED_OUT})", r
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: False)
    marker.write_text("swift build failed (exit 1): error: no such module 'FluidAudio'", encoding="utf-8")
    assert not speech.build_timed_out(sample_config.cache_dir)
    r = by_name(run_checks(sample_config))["speech"]
    assert (r.severity, r.fix) == (Severity.WARN, None), r


def test_a_speech_build_whose_fetch_failed_has_the_re_run_as_its_fix(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 24 B42: a speech build that failed at ``swift package resolve`` (the FluidAudio fetch) left a
    warn with no fix, though a plain install.sh run built the helper in every home of the v32 rehearsal. Its
    fix is the re-run, named as a time-out's is, and where to look if it fails again; no time limit, no "less
    busy". Any other failed build, and the developer tools' case, keep theirs."""
    _real_speech(monkeypatch)
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: False)
    marker = ocr._marker(ocr._helper_path(sample_config.cache_dir, speech.SPEECH))
    marker.parent.mkdir(parents=True, exist_ok=True)
    reason = (
        "swift package resolve failed (exit 1): error: Git command '<command>' failed: Could not resolve host"
    )
    assert reason.startswith(speech.RESOLVE_FAILED)
    marker.write_text(reason, encoding="utf-8")
    monkeypatch.delenv(doctor.NO_NEXT_HINT_ENV, raising=False)
    r = by_name(run_checks(sample_config))["speech"]
    assert (r.ok, r.severity, r.detail) == (False, Severity.WARN, f"speech: failed ({reason})"), r
    assert r.fix == (
        f"run {INSTALL_SH} again; it fetches FluidAudio and the library it links from GitHub again, and if "
        "this line comes back, check that this Mac reaches github.com, or the SwiftPM mirror it uses for "
        "FluidAudio, and release-assets.githubusercontent.com (behind a proxy: HTTPS_PROXY set in the shell)"
    ), r.fix
    assert "less busy" not in r.fix and "minutes" not in r.fix
    # B13 round 25 D53, D65: resolve downloads the library from GitHub's release host too, and the reason's
    # URL is <url>, so the fix names every host resolve reaches, each one the installer's public hosts hold
    assert set(speech.FETCH_HOSTS) == {"github.com", "release-assets.githubusercontent.com"}
    for host in speech.FETCH_HOSTS:
        assert f" {host}" in r.fix and host in setup_report._PUBLIC_HOSTS, host
    monkeypatch.setenv(doctor.NO_NEXT_HINT_ENV, "1")
    r = by_name(run_checks(sample_config))["speech"]
    assert r.fix is not None and r.fix.startswith(
        f"{doctor.AGAIN_UNDER_INSTALL_SH}; it fetches FluidAudio"
    ), r
    for other in (
        "swift build failed (exit 1): error: no such module 'FluidAudio'",
        speech._PIN_REFUSAL,
        "git merge-base failed (exit 128): fatal: not a git repository",
    ):
        marker.write_text(other, encoding="utf-8")
        assert by_name(run_checks(sample_config))["speech"].fix is None, other
    # B13 round 26 E48: a resolve that needs a newer Swift fails the same way on every re-run, with the
    # network fine, so its fix is the toolchain's update and names no host
    too_old = f"{speech.RESOLVE_FAILED}1): " + speech.TOOLS_TOO_OLD.format(installed="5.10.0", needed="6.0.0")
    marker.write_text(too_old, encoding="utf-8")
    monkeypatch.delenv(doctor.NO_NEXT_HINT_ENV)
    r = by_name(run_checks(sample_config))["speech"]
    assert (r.ok, r.severity, r.detail) == (False, Severity.WARN, f"speech: failed ({too_old})"), r
    assert r.fix == (
        "update the Command Line Tools (or Xcode) in Software Update to a release with Swift 6.0.0 or later "
        f"(on a macOS too old for that release, update macOS first), then run {INSTALL_SH} again"
    ), r.fix
    assert "github" not in r.fix and "proxy" not in r.fix and "comes back" not in r.fix
    monkeypatch.setenv(doctor.NO_NEXT_HINT_ENV, "1")
    marker.write_text(reason, encoding="utf-8")
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: True)
    assert by_name(run_checks(sample_config))["speech"].fix == doctor._OCR_DEVTOOLS_FIX


@pytest.mark.parametrize("helper", ["ocr", "media", "speech"])
def test_a_helper_build_a_signal_stopped_has_the_re_run_as_its_fix(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch, helper: str
) -> None:
    """B13 round 25 D85: a build step a signal stopped (its reason ends in the retry clause) had no fix,
    though each install.sh run builds the helper again. Its fix is the plain re-run, said once; no time limit
    and no "less busy", as a signal is no time-out. The developer tools' fix and a failed build's none
    stay."""
    {"ocr": _real_ocr, "media": _real_media, "speech": _real_speech}[helper](monkeypatch)
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: False)
    kind = {"ocr": ocr._OCR, "media": media.MEDIA, "speech": speech.SPEECH}[helper]
    marker = ocr._marker(ocr._helper_path(sample_config.cache_dir, kind))
    marker.parent.mkdir(parents=True, exist_ok=True)
    head = "swift build was " if helper == "speech" else f"swiftc, building the {kind.name}, was "
    for reason in (ocr._signal_reason(head, -9), ocr._signal_reason(head, -11, b"error: crash")):
        assert reason.endswith(HELPER_RETRY_CLAUSE), reason
        marker.write_text(reason, encoding="utf-8")
        monkeypatch.delenv(doctor.NO_NEXT_HINT_ENV, raising=False)
        r = by_name(run_checks(sample_config))[helper]
        assert (r.ok, r.severity, r.fix) == (False, Severity.WARN, f"run {INSTALL_SH} again"), r
        assert reason.removesuffix(HELPER_RETRY_CLAUSE) in r.detail, r.detail
        assert "tries the build again on its next run" not in r.detail, r.detail
        monkeypatch.setenv(doctor.NO_NEXT_HINT_ENV, "1")
        r = by_name(run_checks(sample_config))[helper]
        assert r.fix == doctor.AGAIN_UNDER_INSTALL_SH, r.fix
        assert "less busy" not in r.fix and "minutes" not in r.fix
        monkeypatch.setattr(doctor, "_devtools_missing", lambda: True)
        assert by_name(run_checks(sample_config))[helper].fix == doctor._OCR_DEVTOOLS_FIX
        monkeypatch.setattr(doctor, "_devtools_missing", lambda: False)
    marker.write_text("swift build failed (exit 1): error: no such module 'FluidAudio'", encoding="utf-8")
    assert by_name(run_checks(sample_config))[helper].fix is None


@pytest.mark.parametrize("module", ["ocr", "media", "speech"])
def test_doctor_import_stops_when_a_convert_module_cannot_import(module: str) -> None:
    """B13 round 22 Z56: ``_RETRY_CLAUSE``'s docstring and the comments on doctor's lazy convert imports said
    doctor imports, and gives its line, even when convert is broken. doctor imports ``agentsync.cycle``, which
    imports convert, so a convert module that cannot import stops doctor's import. (B13 round 23 A66: the
    check of doctor's own words is gone, a re-assertion of the source.)"""
    code = (
        f"import sys\nsys.modules['agentsync.convert.{module}'] = None\n"
        "try:\n    import agentsync.ops.doctor\nexcept ImportError:\n    print('stopped')\n"
        "else:\n    print('imported')\n"
    )
    ran = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    )
    assert ran.stdout.strip() == "stopped", ran.stdout + ran.stderr


def test_doctor_defers_no_convert_import_its_own_import_already_made() -> None:
    """B13 round 23 A67: four of doctor's functions imported ``convert.ocr``, ``media`` or ``speech`` inside
    their bodies, "lazy, as the probes are", and ``_exit_words`` guarded its ``ocr`` import with an
    ``except``; but doctor imports ``agentsync.cycle``, which loads all three, so the deferral saved nothing
    and the guard caught nothing. A convert module doctor imports inside a function is one its own import
    leaves unloaded (``convert.pandoc``, for the pandoc check alone)."""
    code = (
        "import ast, sys\nimport agentsync.ops.doctor as d\n"
        "tree = ast.parse(open(d.__file__, encoding='utf-8').read())\n"
        "deferred = {f'{n.module}.{a.name}' if n.module == 'agentsync.convert' else n.module\n"
        "            for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)\n"
        "            for n in ast.walk(f) if isinstance(n, ast.ImportFrom)\n"
        "            and (n.module or '').startswith('agentsync.convert') for a in n.names}\n"
        "print(sorted(deferred)); print(sorted(m for m in deferred if m in sys.modules))\n"
    )
    ran = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    )
    deferred, loaded = ran.stdout.splitlines()
    assert "agentsync.convert.pandoc" in deferred, "the scan reads doctor's function-level imports"
    assert loaded == "[]", ran.stdout


@pytest.mark.parametrize("helper", ["ocr", "media"])
def test_an_ocr_or_media_build_that_ran_out_of_time_has_a_fix_and_no_other_failure_gets_one(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch, helper: str
) -> None:
    """B13 round 19 W49: the OCR and media helpers' builds that ran out of time left a warn with no fix and
    no retry clause, where speech's has both (V5, V28). Keyed on each helper's own marker words, the fix is
    install.sh again with their own 5 minutes; their builds fetch nothing, so no network is named."""
    from agentsync.convert import media  # noqa: PLC0415

    (_real_ocr if helper == "ocr" else _real_media)(monkeypatch)
    monkeypatch.setattr(doctor, "_devtools_missing", lambda: False)
    monkeypatch.delenv(doctor.NO_NEXT_HINT_ENV, raising=False)
    kind = ocr._OCR if helper == "ocr" else media.MEDIA
    marker = ocr._marker(ocr._helper_path(sample_config.cache_dir, kind))
    marker.parent.mkdir(parents=True, exist_ok=True)
    fix = (
        f"run {INSTALL_SH} again; the build stops after 5 minutes, and if it stops again, run it when "
        "the Mac is less busy"
    )
    # B13 round 21 Y54: the marker ocr's own build leaves when swiftc runs out of time, not a copy of it
    tools = {
        "-p": sample_config.cache_dir,
        "swiftc": Path(sys.executable),
        "--show-sdk-path": sample_config.cache_dir,
    }

    def timed_out(*_a: object, **_k: object) -> NoReturn:
        raise subprocess.TimeoutExpired("swiftc", 300)

    def none_yet(_helper: Path) -> NoReturn:
        raise kind.error("no helper yet")

    with monkeypatch.context() as m:
        m.setattr(ocr.sys, "platform", "darwin")
        m.setattr(ocr, "_tool", lambda argv: str(tools[argv[-1]]))
        m.setattr(ocr, "_run_build", timed_out)
        with pytest.raises(ocr.OcrError):
            ocr._build(sample_config.cache_dir, kind, none_yet)
    written = marker.read_text(encoding="utf-8").strip()
    assert written.startswith(f"{ocr.BUILD_TIMED_OUT} {kind.name}"), written
    for reason in (written, f"{ocr.BUILD_TIMED_OUT} {kind.name}"):
        marker.write_text(reason, encoding="utf-8")
        assert ocr.build_timed_out(sample_config.cache_dir, kind)
        r = by_name(run_checks(sample_config))[helper]
        assert (r.ok, r.severity, r.fix) == (False, Severity.WARN, fix), r
        # B13 round 20 X20: the re-run is said once, by the fix
        assert r.detail.rstrip(")").endswith(f"{ocr.BUILD_TIMED_OUT} {kind.name}"), r.detail
        assert "tries the build again" not in r.detail and INSTALL_SH not in r.detail, r.detail
    marker.write_text(
        f"swiftc did not build the {kind.name} (exit 1): error: no such module", encoding="utf-8"
    )
    assert not ocr.build_timed_out(sample_config.cache_dir, kind)
    r = by_name(run_checks(sample_config))[helper]
    assert (r.severity, r.fix) == (Severity.WARN, None), r


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


def test_a_state_folder_that_is_not_a_folder_is_named_from_the_top_down(tmp_path: Path) -> None:
    """B13 round 23 A7, A22, A33, A58 (round 23 ruling 4): the first path, from ``/`` down, of the state
    folder and the folders above it that is there but is not a folder, read through links: a link to a folder
    passes, a link to a file or a symlink loop does not; a missing folder is none (state_dir says the first
    sync creates it), and its FAIL is the person's move aside, never a chmod."""
    above = tmp_path / "Application Support"
    root = above / "agentsync"
    assert doctor._not_a_folder_at(root) is None  # missing: not this
    above.write_text("Contoso\n", encoding="utf-8")
    assert doctor._not_a_folder_at(root) == above
    above.unlink()
    real = tmp_path / "real"
    real.mkdir()
    above.symlink_to(real)  # a link to a folder is a folder
    assert doctor._not_a_folder_at(root) is None
    (real / "agentsync").write_text("Contoso\n", encoding="utf-8")
    assert doctor._not_a_folder_at(root) == root
    (real / "agentsync").unlink()
    (real / "agentsync").symlink_to(real / "agentsync")  # a loop
    assert doctor._not_a_folder_at(root) == root
    fail = doctor._not_a_folder_fail(root, root)
    assert fail.detail == f"{root} is a symlink loop, not a folder", fail.detail
    assert fail.fix == f"the person moves {doctor._shell_path(root)} aside, keeping a copy"
    assert doctor.is_persons_fix(fail.fix) and "(" not in fail.fix and "chmod" not in fail.fix


@pytest.mark.parametrize("link", ["a loop", "a link to a folder"])
def test_a_symlinked_manifest_that_is_no_file_fails_state_dir_manifest_not_an_acl_or_a_chmod(
    sample_config: Config, link: str
) -> None:
    """B13 round 23 A46: doctor read a symlink at manifest.sqlite as a file, so a loop got an ACL FAIL on
    state_dir.files and a link to a folder a ``chmod 600`` (which follows the link onto the folder), while the
    setup report, reading through the link, pointed at a state_dir.manifest line doctor never printed. A link
    is judged by what it points to now; a link to a regular file is still a file."""
    db = sample_config.state_paths.db
    target = db if link == "a loop" else db.parent / "elsewhere"
    if link != "a loop":
        target.mkdir()
    db.unlink(missing_ok=True)
    db.symlink_to(target)
    results = doctor._check_state_dir(sample_config)
    failed = [r for r in results if not r.ok]
    (manifest,) = [r for r in failed if r.name == "state_dir.manifest"]
    kind = "a symlink loop" if link == "a loop" else "a symlink to a folder"
    assert manifest.detail == f"{db} is {kind}, not the state file" and manifest.fix == doctor.MANIFEST_FIX
    assert not any(r.name == "state_dir.files" for r in failed), failed
    db.unlink()
    real = db.parent / "real.sqlite"
    real.write_bytes(b"")
    real.chmod(0o644)
    db.symlink_to(real)  # a link to a regular file is a file, as before
    assert doctor.state_file_kind(db) is None
    again = doctor._check_state_dir(sample_config)
    assert not any(r.name == "state_dir.manifest" and not r.ok for r in again), again


def test_a_lost_manifest_fails_whatever_else_is_in_the_state_folder(sample_config: Config) -> None:
    """B13 round 23 A29's reviewer: the lost-manifest FAIL sat after ``elif present``, so a stale -wal or the
    token-cache fallback file hid it, and status pointed at a state_dir.manifest line it never printed; and
    the refused open's ``str()`` read ``[Errno 2] None: ...``."""
    from agentsync.errors import ManifestLostError  # noqa: PLC0415
    from agentsync.manifest import LOST_MANIFEST, Manifest  # noqa: PLC0415

    sp = sample_config.state_paths
    db = sp.db
    db.unlink(missing_ok=True)
    sp.heartbeat.write_text('{"contoso-docs": {"run_id": 3}}', encoding="utf-8")
    sp.token_cache_fallback.write_bytes(b"")
    sp.token_cache_fallback.chmod(0o600)
    (lost,) = [r for r in doctor._check_state_dir(sample_config) if r.name == "state_dir.manifest"]
    assert not lost.ok and lost.fix == doctor.LOST_MANIFEST_FIX, lost
    with pytest.raises(ManifestLostError) as raised:
        Manifest(db)
    assert str(raised.value) == f"{db} {LOST_MANIFEST}" and not db.exists()
