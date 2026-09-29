"""LaunchAgent plists for the two jobs (poll, reconcile) and their install/uninstall (owner: ops).

Per-user agents only (``~/Library/LaunchAgents``, ``launchctl bootstrap gui/<uid>``): no admin rights needed.
Keys (design 4.7): ProcessType Background, ThrottleInterval 60, RunAtLoad true, LimitLoadToSessionType Aqua,
MaterializeDatalessFiles false (hydration is fail-closed; ``materialise`` opts in per read), a fixed PATH,
logs under Config.log_dir.

Every function that talks to launchd takes a keyword-only ``runner`` (default: run ``/bin/launchctl``), so
tests and ``--dry-run`` callers can record the exact argv without touching launchd.
"""

from __future__ import annotations

import contextlib
import logging
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agentsync.config import Config
from agentsync.errors import ConfigError
from agentsync.model import CycleMode

log = logging.getLogger(__name__)

LAUNCHD_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

_LAUNCHCTL = "/bin/launchctl"
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
_CALENDAR_KEYS = frozenset({"Minute", "Hour", "Day", "Weekday", "Month"})
_NOT_LOADED_RCS = frozenset({3, 113})  # "No such process" / "Could not find service" (measured, macOS 15)
_BOOTSTRAP_RETRY_RCS = frozenset({5, 37})  # EIO right after a bootout; "Operation already in progress"
_BOOTSTRAP_ATTEMPTS = 5
_BOOTOUT_POLLS = 10

_Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
"""Runs one argv (``argv[0]`` is ``/bin/launchctl``); returns the completed process, never raises on rc."""


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """One LaunchAgent."""

    label: str
    program_arguments: tuple[str, ...]
    stdout_path: Path
    stderr_path: Path
    start_interval_s: int | None = None
    start_calendar: Mapping[str, int] | None = None  # e.g. {"Minute": 7} for hourly at :07
    environment: Mapping[str, str] = field(default_factory=dict, hash=False)
    run_at_load: bool = True
    materialize_dataless_files: bool = False
    throttle_interval_s: int = 60
    low_priority_io: bool = True  # ops extension: throttled disk I/O on top of ProcessType Background


def _run_launchctl(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Default runner: execute ``argv`` with a fixed environment, capture output, never raise on rc."""
    env = {"PATH": LAUNCHD_PATH, "LC_ALL": "C", "HOME": str(Path.home())}
    return subprocess.run(list(argv), capture_output=True, text=True, check=False, timeout=120, env=env)


def _domain() -> str:
    """Return this user's GUI launchd domain, ``gui/<uid>``."""
    return f"gui/{os.getuid()}"


def _service(label: str) -> str:
    """Return the service target ``gui/<uid>/<label>``."""
    return f"{_domain()}/{label}"


def _check_label(label: str) -> None:
    """Raise ConfigError unless ``label`` is a reverse-DNS style launchd label."""
    if not _LABEL_RE.match(label):
        raise ConfigError(
            f"launchd label {label!r} is invalid: use letters, digits, '.', '-', '_' "
            "(check [agentsync] launchd_label_prefix)"
        )


def _interpreter() -> str:
    """Absolute path of the running interpreter, NOT symlink-resolved (a venv python must stay the venv's)."""
    exe = sys.executable
    if not exe or not Path(exe).is_absolute():
        raise ConfigError(f"cannot determine an absolute interpreter path (sys.executable={exe!r})")
    return exe


def program_arguments(config: Config, mode: str) -> tuple[str, ...]:
    """Absolute interpreter + ``-m agentsync sync --mode <mode> --config <abs path>`` (no PATH lookup)."""
    valid = {m.value for m in CycleMode}
    if mode not in valid:
        raise ValueError(f"mode must be one of {sorted(valid)}, got {mode!r}")
    cfg = config.config_path.expanduser()
    if not cfg.is_absolute():
        cfg = Path.cwd() / cfg
    return (_interpreter(), "-m", "agentsync", "sync", "--mode", str(mode), "--config", str(cfg))


def _environment() -> dict[str, str]:
    """The fixed job environment: launchd's minimal PATH (no Homebrew) and UTF-8 mode."""
    return {"PATH": LAUNCHD_PATH, "PYTHONUTF8": "1"}


def _spec(config: Config, suffix: str, mode: CycleMode, interval_s: int) -> AgentSpec:
    """Build the AgentSpec for one mode."""
    label = f"{config.launchd_label_prefix}.{suffix}"
    _check_label(label)
    log_dir = config.log_dir
    return AgentSpec(
        label=label,
        program_arguments=program_arguments(config, mode.value),
        stdout_path=log_dir / f"{label}.out.log",
        stderr_path=log_dir / f"{label}.err.log",
        start_interval_s=interval_s,
        environment=_environment(),
    )


def poll_spec(config: Config) -> AgentSpec:
    """``<prefix>.poll``: StartInterval = config.poll_interval_s."""
    return _spec(config, "poll", CycleMode.POLL, config.poll_interval_s)


def reconcile_spec(config: Config) -> AgentSpec:
    """``<prefix>.reconcile``: StartInterval = config.reconcile_interval_s (hourly by default)."""
    return _spec(config, "reconcile", CycleMode.RECONCILE, config.reconcile_interval_s)


def _validate(spec: AgentSpec) -> None:
    """Reject a spec launchd would refuse or silently misread."""
    _check_label(spec.label)
    if not spec.program_arguments:
        raise ValueError(f"{spec.label}: program_arguments is empty")
    if not Path(spec.program_arguments[0]).is_absolute():
        raise ValueError(f"{spec.label}: program must be an absolute path, got {spec.program_arguments[0]!r}")
    for p in (spec.stdout_path, spec.stderr_path):
        if not p.is_absolute():
            raise ValueError(f"{spec.label}: log path must be absolute, got {p}")
    if spec.start_interval_s is not None and spec.start_interval_s <= 0:
        raise ValueError(f"{spec.label}: start_interval_s must be > 0, got {spec.start_interval_s}")
    if spec.start_calendar is not None:
        bad = sorted(set(spec.start_calendar) - _CALENDAR_KEYS)
        if bad or not spec.start_calendar:
            raise ValueError(
                f"{spec.label}: start_calendar keys must be a non-empty subset of "
                f"{sorted(_CALENDAR_KEYS)}, got {sorted(spec.start_calendar)}"
            )
        for k, v in spec.start_calendar.items():
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise ValueError(f"{spec.label}: start_calendar[{k!r}] must be a non-negative int, got {v!r}")
    if spec.throttle_interval_s < 0:
        raise ValueError(f"{spec.label}: throttle_interval_s must be >= 0")


def _plist_dict(spec: AgentSpec) -> dict[str, object]:
    """The launchd.plist dictionary for ``spec``."""
    d: dict[str, object] = {
        "Label": spec.label,
        "ProgramArguments": list(spec.program_arguments),
        "StandardOutPath": str(spec.stdout_path),
        "StandardErrorPath": str(spec.stderr_path),
        "RunAtLoad": spec.run_at_load,
        "ProcessType": "Background",
        "LowPriorityIO": spec.low_priority_io,
        "ThrottleInterval": spec.throttle_interval_s,
        "LimitLoadToSessionType": "Aqua",
        "MaterializeDatalessFiles": spec.materialize_dataless_files,
    }
    if spec.environment:
        d["EnvironmentVariables"] = dict(spec.environment)
    if spec.start_interval_s is not None:
        d["StartInterval"] = spec.start_interval_s
    if spec.start_calendar is not None:
        d["StartCalendarInterval"] = dict(spec.start_calendar)
    return d


def render_plist(spec: AgentSpec) -> bytes:
    """Return the XML plist bytes (plistlib, sort_keys=True: deterministic)."""
    _validate(spec)
    return plistlib.dumps(_plist_dict(spec), fmt=plistlib.FMT_XML, sort_keys=True)


def plist_path(label: str) -> Path:
    """Return ``~/Library/LaunchAgents/<label>.plist``."""
    _check_label(label)
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def _write_plist(path: Path, data: bytes) -> None:
    """Write the plist atomically with mode 0644 (launchd ignores group/world-writable plists)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fchmod(fh.fileno(), 0o644)
            os.fsync(fh.fileno())
        tmp_path.replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def _fail(what: str, cp: subprocess.CompletedProcess[str], hint: str = "") -> OSError:
    """Build the OSError for a failed launchctl call, carrying its stderr."""
    detail = (cp.stderr or cp.stdout or "").strip()
    msg = (
        f"{' '.join(cp.args) if isinstance(cp.args, list) else cp.args} failed (rc {cp.returncode}): {detail}"
    )
    if hint:
        msg += f" — {hint}"
    return OSError(cp.returncode, f"{what}: {msg}")


def _bootout(label: str, runner: _Runner, sleep: Callable[[float], None]) -> bool:
    """``launchctl bootout gui/<uid>/<label>``; True if something was unloaded, False if nothing was.

    A running job is sent SIGTERM and bootout may return non-zero (e.g. 36, in progress) while it exits, so an
    unexpected rc is followed by up to ~5 s of ``print`` polling before it counts as a failure.
    """
    cp = runner([_LAUNCHCTL, "bootout", _service(label)])
    if cp.returncode == 0:
        log.info("launchd: booted out %s", label)
        return True
    text = f"{cp.stderr}{cp.stdout}"
    if cp.returncode in _NOT_LOADED_RCS or "No such process" in text or "Could not find" in text:
        return False
    for attempt in range(_BOOTOUT_POLLS):
        if not is_loaded(label, runner=runner):
            log.info("launchd: booted out %s (rc %d while the job exited)", label, cp.returncode)
            return True
        if attempt < _BOOTOUT_POLLS - 1:
            sleep(0.5)
    raise _fail(f"unloading {label}", cp)


def install(
    spec: AgentSpec,
    *,
    runner: _Runner | None = None,
    force: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    """Write the plist (0644), ``launchctl bootout`` any loaded copy, then ``launchctl bootstrap gui/<uid>``;
    creates log dirs; returns the plist path.  Raises OSError / subprocess errors with launchctl's stderr.

    Idempotent: when the plist on disk already has exactly these bytes and the job is loaded, nothing is
    touched (so re-running install does not fire RunAtLoad again) unless ``force``.  A label previously
    ``launchctl disable``d is re-enabled before bootstrap.  Bootstrap is retried on EIO (5), which launchd
    returns for a moment after a bootout of the same label.
    """
    run = runner or _run_launchctl
    data = render_plist(spec)
    path = plist_path(spec.label)
    for d in {spec.stdout_path.parent, spec.stderr_path.parent}:
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
    unchanged = path.is_file() and path.read_bytes() == data
    if unchanged and not force and is_loaded(spec.label, runner=run):
        log.info("launchd: %s already installed and loaded; nothing to do", spec.label)
        return path
    _write_plist(path, data)
    _bootout(spec.label, run, sleep)
    enable = run([_LAUNCHCTL, "enable", _service(spec.label)])
    if enable.returncode != 0:
        log.warning(
            "launchd: enable %s returned %d: %s", spec.label, enable.returncode, enable.stderr.strip()
        )
    argv = [_LAUNCHCTL, "bootstrap", _domain(), str(path)]
    cp = run(argv)
    attempt = 1
    while cp.returncode in _BOOTSTRAP_RETRY_RCS and attempt < _BOOTSTRAP_ATTEMPTS:
        if is_loaded(spec.label, runner=run):
            break
        sleep(0.5 * attempt)
        attempt += 1
        cp = run(argv)
    if cp.returncode != 0 and not is_loaded(spec.label, runner=run):
        hint = ""
        if cp.returncode == 125 or "Domain does not support" in f"{cp.stderr}{cp.stdout}":
            hint = f"no GUI login session for uid {os.getuid()}: log in at the Mac's console, then retry"
        raise _fail(f"loading {spec.label}", cp, hint)
    log.info("launchd: installed %s -> %s", spec.label, path)
    return path


def uninstall(
    label: str, *, runner: _Runner | None = None, sleep: Callable[[float], None] = time.sleep
) -> bool:
    """``launchctl bootout gui/<uid>/<label>`` (ignore not-loaded) and remove the plist; True if anything
    removed.  A job that cannot be unloaded raises OSError and keeps its plist."""
    run = runner or _run_launchctl
    path = plist_path(label)
    removed = _bootout(label, run, sleep)
    try:
        path.unlink()
        removed = True
    except FileNotFoundError:
        pass
    if removed:
        log.info("launchd: uninstalled %s", label)
    return removed


def is_loaded(label: str, *, runner: _Runner | None = None) -> bool:
    """True when ``launchctl print gui/<uid>/<label>`` succeeds."""
    _check_label(label)
    run = runner or _run_launchctl
    return run([_LAUNCHCTL, "print", _service(label)]).returncode == 0


def _installed_plist(label: str) -> Mapping[str, object] | None:
    """Return the parsed plist installed for ``label`` (None if absent or unparseable)."""
    try:
        data = plist_path(label).read_bytes()
    except FileNotFoundError:
        return None
    try:
        parsed = plistlib.loads(data)
    except (plistlib.InvalidFileException, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None
