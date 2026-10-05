"""LaunchAgent plists for the two jobs (poll, reconcile) and their install/uninstall (owner: ops).

Per-user agents only (``~/Library/LaunchAgents``, ``launchctl bootstrap gui/<uid>``): no admin rights needed.
Keys (design 4.7): ProcessType Background, ThrottleInterval 60, RunAtLoad true, LimitLoadToSessionType Aqua,
MaterializeDatalessFiles false (hydration is fail-closed; ``materialise`` opts in per read), a fixed PATH,
logs under Config.log_dir.

Every function that talks to launchd takes a keyword-only ``runner`` (default: run ``/bin/launchctl``), so
tests and ``--dry-run`` callers can record the exact argv without touching launchd.

The signed launcher (C15 §3, requirements 17-21).  ``ProgramArguments[0]`` is ``agentsync-launcher`` (the
``launcher/`` app bundle, installed at ``~/Applications/AgentSyncLauncher.app`` or named by
``$AGENTSYNC_LAUNCHER``) whenever it is present: it is the TCC-responsible process, so one approval (or one
PPPC ``SystemPolicyAllFiles`` entry) covers the job and never an interpreter that runs any script.  It
canaries every TCC-protected source root with a 10 s timeout before spawning Python (an unanswered prompt
exits 79 ``TCC_PENDING``, never an empty folder) and holds a hard wall-clock watchdog over the child.
Without the launcher, a config whose live local/inbox sources are all outside TCC-protected folders falls
back to the interpreter (logged); one with a protected source refuses (ConfigError naming the fix).
"""

from __future__ import annotations

import contextlib
import glob
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
from agentsync.model import CycleMode, SourceKind
from agentsync.paths import CLOUD_STORAGE_ROOT, expand, is_cloud_path, is_under

log = logging.getLogger(__name__)

LAUNCHD_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

LAUNCHER_ENV = "AGENTSYNC_LAUNCHER"  # path of the launcher .app or executable; "none" disables it
LAUNCHER_BUNDLE = "AgentSyncLauncher.app"
LAUNCHER_EXECUTABLE = "agentsync-launcher"
LAUNCHER_IDENTIFIER = "com.agentsync.launcher"
EXIT_TCC_PENDING = 79  # launcher: a canary or the watchdog timed out (log token TCC_PENDING)
EXIT_TCC_DENIED = 80  # launcher --canary-only: EPERM/EACCES (log token TCC_DENIED)
EXIT_CANARY_MISSING = 66  # launcher --canary-only: ENOENT/ENOTDIR
EXIT_CANARY_IO = 74  # launcher --canary-only: any other errno
EXIT_DISCLAIM_UNAVAILABLE = 81  # launcher --self-responsible: responsibility_spawnattrs_setdisclaim missing
CANARY_TIMEOUT_S = 10  # C15 requirement 17
WATCHDOG_MIN_S = 1800  # a job's wall-clock limit is max(this, 4 x its interval)
_WATCHDOG_FACTOR = 4
_WATCHDOG_GRACE_S = 30

_LAUNCHCTL = "/bin/launchctl"
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
_CALENDAR_KEYS = frozenset({"Minute", "Hour", "Day", "Weekday", "Month"})
_NOT_LOADED_RCS = frozenset({3, 113})  # "No such process" / "Could not find service" (measured, macOS 15)
_BOOTSTRAP_RETRY_RCS = frozenset({5, 37})  # EIO right after a bootout; "Operation already in progress"
_BOOTSTRAP_ATTEMPTS = 5
_BOOTOUT_POLLS = 10

_PLATFORM_INTERPRETERS = frozenset({"/usr/bin/python3", "/usr/bin/python", "/usr/bin/env"})

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
    umask: int | None = 0o077  # ops extension: launchd ``Umask`` (63): nothing the job creates is group/world


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
    """Absolute interpreter + ``-I -X utf8 -m agentsync sync --mode <mode> --config <abs path>`` (no PATH
    lookup; isolated mode ignores PYTHON* variables and the user site, and is the prefix the signed launcher
    pins, see launcher/Sources/main.swift)."""
    valid = {m.value for m in CycleMode}
    if mode not in valid:
        raise ValueError(f"mode must be one of {sorted(valid)}, got {mode!r}")
    cfg = config.config_path.expanduser()
    if not cfg.is_absolute():
        cfg = Path.cwd() / cfg
    return (_interpreter(), *CHILD_PREFIX, "--mode", str(mode), "--config", str(cfg))


CHILD_PREFIX: tuple[str, ...] = ("-I", "-X", "utf8", "-m", "agentsync", "sync")
"""What follows the interpreter in every job's child argv; the launcher's pin (``childPrefix``) is the
same."""


def _environment() -> dict[str, str]:
    """The fixed job environment: launchd's minimal PATH (no Homebrew) and UTF-8 mode."""
    return {"PATH": LAUNCHD_PATH, "PYTHONUTF8": "1"}


LOG_ROTATE_BYTES = 8 * 1024 * 1024
LOG_ROTATE_KEEP = 2


def rotate_logs(
    log_dir: Path, *, max_bytes: int = LOG_ROTATE_BYTES, keep: int = LOG_ROTATE_KEEP
) -> list[Path]:
    """Rotate the LaunchAgent logs (``*.log`` in ``log_dir``) above ``max_bytes``: ``x.log`` -> ``x.log.1``
    -> ... -> ``x.log.<keep>`` (the oldest is deleted).  launchd opens StandardOut/ErrorPath per spawn and
    never rotates them, so each cycle calls this first: a job log holds at most (keep + 1) x max_bytes.
    A run in progress keeps writing to its (renamed) file; the next spawn starts a fresh one.
    Returns the logs rotated."""
    rotated: list[Path] = []
    if not log_dir.is_dir():
        return rotated
    for path in sorted(log_dir.glob("*.log")):
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size <= max_bytes:
                continue
            for n in range(keep, 0, -1):
                older = path.with_name(f"{path.name}.{n}")
                if n == keep:
                    older.unlink(missing_ok=True)
                elif older.exists():
                    older.replace(path.with_name(f"{path.name}.{n + 1}"))
            path.replace(path.with_name(f"{path.name}.1"))
            rotated.append(path)
        except OSError as exc:
            log.warning("log rotation of %s failed: %s", path, exc)
    return rotated


def launcher_identifier(launcher: Path | None = None) -> str:
    """The launcher bundle's own ``CFBundleIdentifier`` (a corporate build sets ``BUNDLE_ID``); the default
    identifier when there is no bundle or its Info.plist is unreadable.  PPPC and ``tccutil`` name this."""
    exe = launcher if launcher is not None else _safe_find_launcher()
    app = launcher_app(exe) if exe is not None else None
    if app is None and exe is not None and exe.suffix == ".app":
        app = exe
    if app is None:
        return LAUNCHER_IDENTIFIER
    try:
        info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return LAUNCHER_IDENTIFIER
    ident = info.get("CFBundleIdentifier") if isinstance(info, dict) else None
    return ident if isinstance(ident, str) and ident else LAUNCHER_IDENTIFIER


def _safe_find_launcher() -> Path | None:
    try:
        return find_launcher()
    except ConfigError:
        return None


def default_launcher_app() -> Path:
    """Where ``scripts/install.sh`` installs the launcher: ``~/Applications/AgentSyncLauncher.app``."""
    return Path.home() / "Applications" / LAUNCHER_BUNDLE


def launcher_executable(path: Path) -> Path:
    """The Mach-O inside ``path`` when it is the .app bundle, else ``path`` itself."""
    if path.suffix == ".app":
        return path / "Contents" / "MacOS" / LAUNCHER_EXECUTABLE
    return path


def launcher_app(executable: Path) -> Path | None:
    """The .app bundle enclosing ``executable`` (what a PPPC payload or the FDA list names), if any."""
    for parent in executable.parents:
        if parent.suffix == ".app":
            return parent
    return None


def find_launcher() -> Path | None:
    """The launcher executable to use: ``$AGENTSYNC_LAUNCHER`` (app or binary; ``none`` disables), else the
    default ``~/Applications`` install; None when absent.

    An explicit but unusable path raises ConfigError.
    """
    explicit = os.environ.get(LAUNCHER_ENV)
    if explicit is not None:
        if explicit.strip().lower() in ("", "none", "0"):
            return None
        exe = launcher_executable(Path(explicit).expanduser())
        if not exe.is_absolute() or not os.access(exe, os.X_OK):
            raise ConfigError(
                f"${LAUNCHER_ENV}={explicit!r} is not an executable launcher ({exe}); build it with "
                "launcher/build.sh or unset the variable"
            )
        return exe
    exe = launcher_executable(default_launcher_app())
    return exe if os.access(exe, os.X_OK) else None


def tcc_protected(path: Path) -> bool:
    """True for a path macOS privacy (TCC) gates per responsible process: a File Provider tree, the
    Documents/Desktop/Downloads folders, iCloud Drive, or a removable/network volume."""
    p = expand(path)
    if is_cloud_path(p):
        return True
    home = Path.home()
    gated = (
        home / "Documents",
        home / "Desktop",
        home / "Downloads",
        home / "Library" / "Mobile Documents",
        Path("/Volumes"),
    )
    return any(is_under(p, g) for g in gated)


def canary_paths(config: Config) -> tuple[Path, ...]:
    """The launcher's canaries: each live local/inbox source root under a TCC-protected folder, then its
    sentinel when one is configured (config order, deduplicated)."""
    out: list[Path] = []
    for src in config.live_sources():
        if src.kind not in (SourceKind.LOCAL, SourceKind.INBOX) or src.path is None:
            continue
        root = expand(src.path)
        if not tcc_protected(root):
            continue
        for p in (root, root / src.sentinel) if src.sentinel else (root,):
            if p not in out:
                out.append(p)
    return tuple(out)


def launcher_required(config: Config) -> bool:
    """True when a live source sits under a TCC-protected folder, so the job must run the signed launcher.
    install-agent refuses without it; doctor reports a missing launcher only once :func:`agents_installed`
    (or install.sh's agent step is pending), since background sync is optional (KISS K11a)."""
    return bool(canary_paths(config))


def watchdog_s(interval_s: int) -> int:
    """The launcher's hard wall-clock limit for one run of a job started every ``interval_s`` seconds."""
    return max(WATCHDOG_MIN_S, _WATCHDOG_FACTOR * interval_s)


def job_arguments(config: Config, mode: str, interval_s: int, launcher: Path | None) -> tuple[str, ...]:
    """``ProgramArguments`` for one job: the launcher with its watchdog and canaries, then ``--`` and
    :func:`program_arguments`; just :func:`program_arguments` when ``launcher`` is None."""
    child = program_arguments(config, mode)
    if launcher is None:
        return child
    if not launcher.is_absolute():
        raise ConfigError(f"launcher path {launcher} is not absolute")
    argv = [
        str(launcher),
        "--timeout",
        str(watchdog_s(interval_s)),
        "--grace",
        str(_WATCHDOG_GRACE_S),
        "--canary-timeout",
        str(CANARY_TIMEOUT_S),
    ]
    for p in canary_paths(config):
        argv += ["--canary", str(p)]
    return (*argv, "--", *child)


def tcc_prompt_text(config: Config) -> str | None:
    """The exact TCC prompt the user approves on the first launchd run (None when no source needs one).

    The template is TCC's ``REQUEST_ACCESS_SERVICE_kTCCServiceFileProviderDomain`` string (C15 §3.1);
    the provider name is read from the CloudStorage folder name (``OneDrive-Contoso`` -> ``OneDrive``).
    """
    cloud = [p for p in canary_paths(config) if is_cloud_path(p)]
    if not cloud:
        return None
    root = expand(CLOUD_STORAGE_ROOT)
    providers: list[str] = []
    for p in cloud:
        rel = p.relative_to(root).parts
        name = rel[0].split("-", 1)[0] if rel else "the File Provider"
        if name not in providers:
            providers.append(name)
    q_open, q_close = "\u201c", "\u201d"
    return " / ".join(
        f"{q_open}{LAUNCHER_EXECUTABLE}{q_close} wants to access files managed by {q_open}{name}{q_close}."
        for name in providers
    )


def _spec(config: Config, suffix: str, mode: CycleMode, interval_s: int) -> AgentSpec:
    """Build the AgentSpec for one mode (the launcher wraps the interpreter whenever it is installed)."""
    label = f"{config.launchd_label_prefix}.{suffix}"
    _check_label(label)
    launcher = find_launcher()
    if launcher is None:
        if launcher_required(config):
            protected = ", ".join(str(p) for p in canary_paths(config))
            raise ConfigError(
                f"{label}: live sources under TCC-protected folders ({protected}) need the signed "
                f"{LAUNCHER_EXECUTABLE}: run scripts/install.sh (or launcher/build.sh and copy "
                f"build/{LAUNCHER_BUNDLE} to {default_launcher_app().parent}/), then install-agent again"
            )
        log.warning(
            "launchd: %s not found; %s runs the interpreter directly (fine only while no source is under a "
            "TCC-protected folder)",
            LAUNCHER_EXECUTABLE,
            label,
        )
    log_dir = config.log_dir
    return AgentSpec(
        label=label,
        program_arguments=job_arguments(config, mode.value, interval_s, launcher),
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
    if spec.umask is not None and not 0 <= spec.umask <= 0o777:
        raise ValueError(f"{spec.label}: umask must be within 0..0o777, got {spec.umask!r}")
    if spec.program_arguments[0] in _PLATFORM_INTERPRETERS:
        raise ValueError(
            f"{spec.label}: {spec.program_arguments[0]} is an Apple platform binary: TCC denies it every "
            f"protected folder with nothing to approve; run the signed {LAUNCHER_EXECUTABLE} instead"
        )


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
    if spec.umask is not None:
        d["Umask"] = spec.umask
    return d


def render_plist(spec: AgentSpec) -> bytes:
    """Return the XML plist bytes (plistlib, sort_keys=True: deterministic)."""
    _validate(spec)
    return plistlib.dumps(_plist_dict(spec), fmt=plistlib.FMT_XML, sort_keys=True)


def plist_path(label: str) -> Path:
    """Return ``~/Library/LaunchAgents/<label>.plist``."""
    _check_label(label)
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


_DEFAULT_LABEL_PREFIX = "com.agentsync"


def agents_installed(config: Config) -> bool:
    """True when any ``<prefix>.*.plist`` sits in ``~/Library/LaunchAgents`` (the config's
    ``launchd_label_prefix`` or the default ``com.agentsync``), readable or not: background sync is optional
    (KISS K11a), so doctor checks the launcher and the jobs only on a Mac that has them."""
    agents = Path.home() / "Library" / "LaunchAgents"
    prefixes = {config.launchd_label_prefix, _DEFAULT_LABEL_PREFIX}
    try:
        return any(any(agents.glob(f"{glob.escape(p)}.*.plist")) for p in prefixes)
    except OSError:
        return False


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
