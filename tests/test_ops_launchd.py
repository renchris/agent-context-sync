"""agentsync.ops.launchd: plist rendering (plutil-linted), specs, and install/uninstall via a fake launchctl.

launchd is never touched: every install/uninstall/is_loaded call goes through ``FakeLaunchctl``, and an
autouse fixture makes the real runner raise if anything reaches it.  HOME is a tmp dir (conftest), so
``~/Library/LaunchAgents`` is too.
"""

from __future__ import annotations

import dataclasses
import os
import plistlib
import shutil
import stat
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from agentsync.config import Config
from agentsync.errors import ConfigError
from agentsync.model import CycleMode
from agentsync.ops import launchd
from agentsync.ops.launchd import (
    LAUNCHD_PATH,
    AgentSpec,
    install,
    is_loaded,
    plist_path,
    poll_spec,
    program_arguments,
    reconcile_spec,
    render_plist,
    uninstall,
)

PLUTIL = "/usr/bin/plutil"
UID = os.getuid()


class FakeLaunchctl:
    """Records argv and models launchd's loaded set with the return codes measured on macOS 15."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.loaded: set[str] = set()
        self.bootstrap_rcs: list[int] = []  # queued non-zero rcs for the next bootstrap calls
        self.bootstrap_stderr = "Bootstrap failed: 5: Input/output error"
        self.bootout_rc: int | None = None  # forced rc for bootout

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        argv = list(argv)
        self.calls.append(argv)
        assert argv[0] == "/bin/launchctl"
        verb = argv[1]
        if verb == "print":
            label = argv[2].split("/", 2)[2]
            if label in self.loaded:
                return subprocess.CompletedProcess(argv, 0, f"{argv[2]} = {{ ... }}", "")
            return subprocess.CompletedProcess(argv, 113, "", f'Could not find service "{label}"')
        if verb == "bootout":
            label = argv[2].split("/", 2)[2]
            if self.bootout_rc is not None:
                return subprocess.CompletedProcess(argv, self.bootout_rc, "", "Boot-out failed: 1: weird")
            if label in self.loaded:
                self.loaded.discard(label)
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.CompletedProcess(argv, 3, "", "Boot-out failed: 3: No such process")
        if verb == "enable":
            return subprocess.CompletedProcess(argv, 0, "", "")
        if verb == "bootstrap":
            assert argv[2] == f"gui/{UID}"
            if self.bootstrap_rcs:
                return subprocess.CompletedProcess(argv, self.bootstrap_rcs.pop(0), "", self.bootstrap_stderr)
            data = plistlib.loads(Path(argv[3]).read_bytes())
            self.loaded.add(data["Label"])
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"unexpected launchctl verb {argv}")

    def verbs(self) -> list[str]:
        return [c[1] for c in self.calls]


@pytest.fixture(autouse=True)
def _no_real_launchctl(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"test reached the real launchctl: {list(argv)}")

    monkeypatch.setattr(launchd, "_run_launchctl", refuse)


@pytest.fixture
def fake() -> FakeLaunchctl:
    return FakeLaunchctl()


def _spec(tmp_path: Path, **kw: object) -> AgentSpec:
    base: dict[str, object] = {
        "label": "com.agentsync.test",
        "program_arguments": (sys.executable, "-m", "agentsync", "sync", "--mode", "poll"),
        "stdout_path": tmp_path / "logs" / "test.out.log",
        "stderr_path": tmp_path / "logs" / "test.err.log",
        "start_interval_s": 300,
        "environment": {"PATH": LAUNCHD_PATH},
    }
    base.update(kw)
    return AgentSpec(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------------ specs


def test_program_arguments_use_unresolved_absolute_interpreter(sample_config: Config) -> None:
    args = program_arguments(sample_config, "poll")
    assert args == (
        sys.executable,
        "-m",
        "agentsync",
        "sync",
        "--mode",
        "poll",
        "--config",
        str(sample_config.config_path),
    )
    assert Path(args[0]).is_absolute()
    # a venv's python is a symlink; resolving it would drop the venv's site-packages
    assert args[0] == sys.executable
    assert Path(args[-1]).is_absolute()
    assert program_arguments(sample_config, CycleMode.RECONCILE)[5] == "reconcile"
    assert program_arguments(sample_config, "dry_run")[5] == "dry_run"


def test_program_arguments_reject_unknown_mode(sample_config: Config) -> None:
    with pytest.raises(ValueError, match="mode"):
        program_arguments(sample_config, "sometimes")


def test_program_arguments_need_absolute_interpreter(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "executable", "")
    with pytest.raises(ConfigError, match="interpreter"):
        program_arguments(sample_config, "poll")


def test_poll_and_reconcile_specs(sample_config: Config) -> None:
    poll, rec = poll_spec(sample_config), reconcile_spec(sample_config)
    assert poll.label == "com.agentsync.poll"
    assert rec.label == "com.agentsync.reconcile"
    assert poll.start_interval_s == sample_config.poll_interval_s == 300
    assert rec.start_interval_s == sample_config.reconcile_interval_s == 3600
    assert poll.program_arguments[5] == "poll" and rec.program_arguments[5] == "reconcile"
    for spec in (poll, rec):
        assert spec.stdout_path.parent == sample_config.log_dir
        assert spec.stderr_path.parent == sample_config.log_dir
        assert spec.stdout_path != spec.stderr_path
        assert spec.environment["PATH"] == LAUNCHD_PATH
        assert "/opt/homebrew" not in spec.environment["PATH"]
        assert spec.materialize_dataless_files is False
        assert spec.run_at_load is True
        assert spec.throttle_interval_s == 60
        assert spec.start_calendar is None
    assert poll.stdout_path != rec.stdout_path


def test_label_prefix_from_config(sample_config: Config) -> None:
    cfg = dataclasses.replace(sample_config, launchd_label_prefix="com.example.docs-sync", poll_interval_s=60)
    spec = poll_spec(cfg)
    assert spec.label == "com.example.docs-sync.poll"
    assert spec.start_interval_s == 60
    bad = dataclasses.replace(sample_config, launchd_label_prefix="bad prefix/../x")
    with pytest.raises(ConfigError, match="launchd_label_prefix"):
        poll_spec(bad)


# ------------------------------------------------------------------------------------------------ plist


def test_render_plist_keys_and_determinism(sample_config: Config) -> None:
    spec = poll_spec(sample_config)
    data = render_plist(spec)
    assert data == render_plist(poll_spec(sample_config))
    assert data.startswith(b"<?xml")
    d = plistlib.loads(data)
    assert d == {
        "Label": "com.agentsync.poll",
        "ProgramArguments": list(spec.program_arguments),
        "StandardOutPath": str(spec.stdout_path),
        "StandardErrorPath": str(spec.stderr_path),
        "RunAtLoad": True,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "ThrottleInterval": 60,
        "LimitLoadToSessionType": "Aqua",
        "MaterializeDatalessFiles": False,
        "EnvironmentVariables": {"PATH": LAUNCHD_PATH, "PYTHONUTF8": "1"},
        "StartInterval": 300,
    }
    keys = [line.strip() for line in data.decode().splitlines() if line.strip().startswith("<key>")]
    top = [k for k in keys if k[5:-6] in d]
    assert top == sorted(top), "sort_keys=True"


def test_render_plist_calendar_and_flags(tmp_path: Path) -> None:
    spec = _spec(
        tmp_path,
        start_interval_s=None,
        start_calendar={"Minute": 7, "Hour": 2},
        run_at_load=False,
        materialize_dataless_files=True,
        low_priority_io=False,
        environment={},
    )
    d = plistlib.loads(render_plist(spec))
    assert d["StartCalendarInterval"] == {"Hour": 2, "Minute": 7}
    assert "StartInterval" not in d
    assert "EnvironmentVariables" not in d
    assert d["RunAtLoad"] is False and d["MaterializeDatalessFiles"] is True and d["LowPriorityIO"] is False


@pytest.mark.parametrize(
    ("kw", "match"),
    [
        ({"program_arguments": ()}, "empty"),
        ({"program_arguments": ("python3", "-m", "agentsync")}, "absolute"),
        ({"stdout_path": Path("rel.log")}, "absolute"),
        ({"start_interval_s": 0}, "start_interval_s"),
        ({"start_calendar": {"Second": 1}}, "start_calendar"),
        ({"start_calendar": {}}, "start_calendar"),
        ({"start_calendar": {"Minute": -1}}, "non-negative"),
        ({"throttle_interval_s": -1}, "throttle"),
    ],
)
def test_render_plist_rejects_bad_specs(tmp_path: Path, kw: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        render_plist(_spec(tmp_path, **kw))


def test_render_plist_rejects_bad_label(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        render_plist(_spec(tmp_path, label="has space"))


@pytest.mark.skipif(not Path(PLUTIL).exists(), reason="plutil is macOS-only")
@pytest.mark.parametrize("which", ["poll", "reconcile", "calendar"])
def test_generated_plist_passes_plutil_lint(sample_config: Config, tmp_path: Path, which: str) -> None:
    if which == "poll":
        spec = poll_spec(sample_config)
    elif which == "reconcile":
        spec = reconcile_spec(sample_config)
    else:
        spec = _spec(tmp_path, start_interval_s=None, start_calendar={"Minute": 7})
    path = tmp_path / f"{spec.label}.plist"
    path.write_bytes(render_plist(spec))
    cp = subprocess.run([PLUTIL, "-lint", str(path)], capture_output=True, text=True, check=False)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert cp.stdout.strip().endswith("OK")
    js = subprocess.run(
        [PLUTIL, "-extract", "MaterializeDatalessFiles", "raw", "-o", "-", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert js.stdout.strip() == "false"


def test_plist_path_is_under_home_launchagents(tmp_path: Path) -> None:
    p = plist_path("com.agentsync.poll")
    assert p == Path.home() / "Library" / "LaunchAgents" / "com.agentsync.poll.plist"
    assert "pytest" in str(Path.home()) or "home" in str(Path.home())  # conftest tmp HOME
    with pytest.raises(ConfigError):
        plist_path("../escape")


# ------------------------------------------------------------------------------------------------ install


def test_install_fresh(tmp_path: Path, fake: FakeLaunchctl) -> None:
    spec = _spec(tmp_path)
    path = install(spec, runner=fake)
    assert path == plist_path(spec.label)
    assert path.read_bytes() == render_plist(spec)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert spec.stdout_path.parent.is_dir()
    assert fake.calls == [
        ["/bin/launchctl", "bootout", f"gui/{UID}/{spec.label}"],
        ["/bin/launchctl", "enable", f"gui/{UID}/{spec.label}"],
        ["/bin/launchctl", "bootstrap", f"gui/{UID}", str(path)],
    ]
    assert spec.label in fake.loaded
    assert is_loaded(spec.label, runner=fake)
    assert [p.name for p in path.parent.iterdir()] == [path.name], "no temp files left"


def test_install_is_idempotent(tmp_path: Path, fake: FakeLaunchctl) -> None:
    spec = _spec(tmp_path)
    install(spec, runner=fake)
    fake.calls.clear()
    mtime = plist_path(spec.label).stat().st_mtime_ns
    install(spec, runner=fake)
    assert fake.verbs() == ["print"], "unchanged + loaded: nothing reloaded (RunAtLoad not re-fired)"
    assert plist_path(spec.label).stat().st_mtime_ns == mtime


def test_install_force_reloads(tmp_path: Path, fake: FakeLaunchctl) -> None:
    spec = _spec(tmp_path)
    install(spec, runner=fake)
    fake.calls.clear()
    install(spec, runner=fake, force=True)
    assert fake.verbs() == ["bootout", "enable", "bootstrap"]
    assert fake.calls[0][-1] == f"gui/{UID}/{spec.label}"
    assert spec.label in fake.loaded


def test_install_changed_spec_replaces_loaded_copy(tmp_path: Path, fake: FakeLaunchctl) -> None:
    install(_spec(tmp_path), runner=fake)
    fake.calls.clear()
    spec2 = _spec(tmp_path, start_interval_s=120)
    path = install(spec2, runner=fake)
    assert fake.verbs() == ["bootout", "enable", "bootstrap"]
    assert plistlib.loads(path.read_bytes())["StartInterval"] == 120


def test_install_unchanged_but_not_loaded_reloads(tmp_path: Path, fake: FakeLaunchctl) -> None:
    spec = _spec(tmp_path)
    install(spec, runner=fake)
    fake.loaded.clear()  # e.g. the user ran `launchctl bootout` by hand
    fake.calls.clear()
    install(spec, runner=fake)
    assert fake.verbs() == ["print", "bootout", "enable", "bootstrap"]
    assert spec.label in fake.loaded


def test_install_retries_bootstrap_eio(tmp_path: Path, fake: FakeLaunchctl) -> None:
    fake.bootstrap_rcs = [5, 5]
    slept: list[float] = []
    install(_spec(tmp_path), runner=fake, sleep=slept.append)
    assert fake.verbs().count("bootstrap") == 3
    assert slept == [0.5, 1.0]
    assert "com.agentsync.test" in fake.loaded


def test_install_raises_with_launchctl_stderr(tmp_path: Path, fake: FakeLaunchctl) -> None:
    fake.bootstrap_rcs = [5] * 10
    with pytest.raises(OSError, match="Input/output error") as ei:
        install(_spec(tmp_path), runner=fake, sleep=lambda _s: None)
    assert "bootstrap" in str(ei.value)
    assert fake.verbs().count("bootstrap") == 5


def test_install_without_gui_session_names_the_fix(tmp_path: Path, fake: FakeLaunchctl) -> None:
    fake.bootstrap_rcs = [125]
    fake.bootstrap_stderr = "Bootstrap failed: 125: Domain does not support specified action"
    with pytest.raises(OSError, match="log in at the Mac's console"):
        install(_spec(tmp_path), runner=fake, sleep=lambda _s: None)


def test_install_rejects_invalid_spec_before_touching_anything(tmp_path: Path, fake: FakeLaunchctl) -> None:
    with pytest.raises(ValueError):
        install(_spec(tmp_path, start_interval_s=0), runner=fake)
    assert fake.calls == []
    assert not plist_path("com.agentsync.test").exists()


def test_install_real_config_specs(sample_config: Config, fake: FakeLaunchctl) -> None:
    for spec in (poll_spec(sample_config), reconcile_spec(sample_config)):
        install(spec, runner=fake)
    assert fake.loaded == {"com.agentsync.poll", "com.agentsync.reconcile"}
    assert sample_config.log_dir.is_dir()


# ------------------------------------------------------------------------------------------------ uninstall


def test_uninstall_loaded_agent(tmp_path: Path, fake: FakeLaunchctl) -> None:
    spec = _spec(tmp_path)
    path = install(spec, runner=fake)
    fake.calls.clear()
    assert uninstall(spec.label, runner=fake) is True
    assert fake.calls == [["/bin/launchctl", "bootout", f"gui/{UID}/{spec.label}"]]
    assert not path.exists()
    assert spec.label not in fake.loaded
    assert uninstall(spec.label, runner=fake) is False, "second uninstall finds nothing"


def test_uninstall_plist_only(tmp_path: Path, fake: FakeLaunchctl) -> None:
    spec = _spec(tmp_path)
    install(spec, runner=fake)
    fake.loaded.clear()
    assert uninstall(spec.label, runner=fake) is True
    assert not plist_path(spec.label).exists()


def test_uninstall_loaded_without_plist(tmp_path: Path, fake: FakeLaunchctl) -> None:
    fake.loaded.add("com.agentsync.ghost")
    assert uninstall("com.agentsync.ghost", runner=fake) is True
    assert "com.agentsync.ghost" not in fake.loaded


def test_uninstall_unexpected_bootout_error_while_still_loaded(tmp_path: Path, fake: FakeLaunchctl) -> None:
    spec = _spec(tmp_path)
    install(spec, runner=fake)
    fake.bootout_rc = 1
    slept: list[float] = []
    with pytest.raises(OSError, match="weird"):
        uninstall(spec.label, runner=fake, sleep=slept.append)
    assert plist_path(spec.label).exists(), "a job we could not unload keeps its plist"
    assert len(slept) == 9 and fake.verbs().count("print") == 10


def test_bootout_in_progress_then_unloaded(tmp_path: Path, fake: FakeLaunchctl) -> None:
    spec = _spec(tmp_path)
    install(spec, runner=fake)
    fake.bootout_rc = 36  # "Operation now in progress": the job is still exiting
    polls = iter([True, True, False])
    real_call = fake.__call__

    def runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if argv[1] == "print":
            fake.calls.append(list(argv))
            return subprocess.CompletedProcess(list(argv), 0 if next(polls) else 113, "", "")
        return real_call(argv)

    slept: list[float] = []
    assert uninstall(spec.label, runner=runner, sleep=slept.append) is True
    assert slept == [0.5, 0.5]
    assert not plist_path(spec.label).exists()


def test_is_loaded_uses_launchctl_print(fake: FakeLaunchctl) -> None:
    assert is_loaded("com.agentsync.poll", runner=fake) is False
    fake.loaded.add("com.agentsync.poll")
    assert is_loaded("com.agentsync.poll", runner=fake) is True
    assert fake.calls[-1] == ["/bin/launchctl", "print", f"gui/{UID}/com.agentsync.poll"]


def test_default_runner_is_guarded(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="real launchctl"):
        is_loaded("com.agentsync.poll")


def test_installed_plist_helper(tmp_path: Path, fake: FakeLaunchctl) -> None:
    spec = _spec(tmp_path)
    assert launchd._installed_plist(spec.label) is None
    install(spec, runner=fake)
    got = launchd._installed_plist(spec.label)
    assert got is not None and got["Label"] == spec.label
    plist_path(spec.label).write_bytes(b"not a plist")
    assert launchd._installed_plist(spec.label) is None


@pytest.mark.skipif(shutil.which("launchctl") is None, reason="macOS only")
def test_launchctl_binary_path_exists() -> None:
    assert Path("/bin/launchctl").exists()
