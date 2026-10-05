"""agentsync.ops.doctor: every check's pass and fail paths, ordering, crash isolation, and rendering.

Probes that would call other (W1b) modules or launchd are replaced with fakes; git and the bundled pandoc
run for real.
"""

from __future__ import annotations

import dataclasses
import errno
import os
import plistlib
import shutil
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentsync import cli, gitops
from agentsync.config import Config, parse_config
from agentsync.model import PassKind, SourceState
from agentsync.ops import doctor, launchd
from agentsync.ops.doctor import CheckResult, Severity, format_results, run_checks
from agentsync.ops.lock import LockInfo, SingleWriterLock, boot_time, write_heartbeat

GIT = shutil.which("git") or "/usr/bin/git"
REAL_GIT_PATH = doctor._git_path
REAL_IN_LAUNCHD = doctor._in_launchd_job
REAL_CODESIGN_INFO = doctor._codesign_info
REAL_LAUNCHER_CANARY = doctor._launcher_canary


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

    monkeypatch.setattr(doctor, "_codesign_info", no_codesign)
    monkeypatch.setattr(doctor, "_launcher_canary", no_canary)

    def refuse(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"reached real launchctl: {argv}")

    monkeypatch.setattr(launchd, "_run_launchctl", refuse)
    return state


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
    assert r["launchd.poll"].severity is Severity.WARN and not r["launchd.poll"].ok
    assert r["launchd.poll"].fix == "agentsync install-agent"
    assert r["pandoc"].ok and r["pandoc"].detail.startswith("pandoc ")
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


def test_source_sentinel_missing(sample_config: Config) -> None:
    src = dataclasses.replace(sample_config.sources[0], sentinel="NOT-THERE.txt")
    r = by_name(run_checks(dataclasses.replace(sample_config, sources=(src,))))[
        "source.local-fixture.sentinel"
    ]
    assert not r.ok and r.severity is Severity.ERROR and "incomplete" in r.detail


def test_cloud_source_without_sentinel_and_tcc_note(sample_config: Config) -> None:
    root = Path.home() / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    root.mkdir(parents=True)
    (root / "a.docx").write_bytes(b"x")
    src = dataclasses.replace(sample_config.sources[0], path=root, sentinel=None)
    r = by_name(run_checks(dataclasses.replace(sample_config, sources=(src,))))
    assert r["source.local-fixture.sentinel"].severity is Severity.WARN
    assert "File Provider root UUID" in r["source.local-fixture.volume"].detail
    assert r["tcc"].ok and "needs its own" in r["tcc"].detail


def test_cloud_source_empty_hints_fda(sample_config: Config) -> None:
    root = Path.home() / "Library" / "CloudStorage" / "OneDrive-Test" / "Empty"
    root.mkdir(parents=True)
    src = dataclasses.replace(sample_config.sources[0], path=root, sentinel=None)
    r = by_name(run_checks(dataclasses.replace(sample_config, sources=(src,))))[
        "source.local-fixture.listable"
    ]
    assert not r.ok and "not been enumerated" in r.detail and "Full Disk Access" in (r.fix or "")


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

    sample_config.state_paths.heartbeat.unlink()
    for _ in range(3):
        _beat(sample_config, enumeration_complete=False)
    r = by_name(run_checks(sample_config))[name]
    assert not r.ok and "incomplete for 3" in r.detail

    _beat(sample_config, ok=False, auth_state="REAUTH_REQUIRED")
    r = by_name(run_checks(sample_config))[name]
    assert not r.ok and r.severity is Severity.ERROR and r.fix == "agentsync login"

    sample_config.state_paths.heartbeat.unlink()
    _beat(sample_config, ok=False, pass_kind=None)
    r = by_name(run_checks(sample_config))[name]
    assert not r.ok and "no successful pass" in r.detail


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


def _launcher(monkeypatch: pytest.MonkeyPatch, sig: doctor._CodeSignature = ADHOC) -> Path:
    """Install a stand-in launcher app under the tmp HOME and fake its codesign answer."""
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


def test_launcher_missing_but_required_is_an_error(sample_config: Config) -> None:
    cfg, _root = _cloud(sample_config)
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
    monkeypatch.setattr(doctor, "_find_launcher", launchd.find_launcher)
    monkeypatch.setenv(launchd.LAUNCHER_ENV, "/nonexistent/AgentSyncLauncher.app")
    r = by_name(run_checks(sample_config))["launcher"]
    assert not r.ok and r.severity is Severity.ERROR and launchd.LAUNCHER_ENV in (r.fix or "")


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
