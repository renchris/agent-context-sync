"""``agentsync doctor``: preflight checks with a concrete fix per failure (owner: ops).

Every probe that touches the machine or another module is a private module-level function (``_git_path``,
``_run``, ``_volume_uuid``, ``_auth_status``, ...) so tests replace it with a fake; ``run_checks`` never
raises for a single check: a probe that crashes becomes a failed :class:`CheckResult` naming the exception.
"""

from __future__ import annotations

import ctypes
import enum
import errno
import logging
import os
import plistlib
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentsync.config import Config, SourceConfig
from agentsync.model import SourceKind
from agentsync.ops import launchd
from agentsync.ops.lock import SingleWriterLock, read_heartbeat
from agentsync.paths import expand, is_cloud_path

log = logging.getLogger(__name__)

_MIN_PYTHON = (3, 11)
_MIN_FREE_BYTES = 2 * 1024**3
_MAX_LOG_BYTES = 64 * 1024**2
_STALE_FACTOR = 3  # design 4.7 watcher rule: cursor/success age > 3 x cadence
_INCOMPLETE_RUNS = 3  # consecutive passes with enumeration_complete false before we warn
_POLICY_NAMES = {0: "default", 1: "off", 2: "on"}


class Severity(enum.StrEnum):
    """How bad a failed check is."""

    ERROR = "error"  # sync will fail or be wrong
    WARN = "warn"  # sync works, something is degraded
    INFO = "info"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One check."""

    name: str
    ok: bool
    detail: str
    severity: Severity = Severity.ERROR
    fix: str | None = None  # the exact command or setting that fixes it


@dataclass(frozen=True, slots=True)
class _AuthProbe:
    """What doctor needs from graph.auth (no secrets, no network)."""

    signed_in: bool
    username: str | None
    cache_backend: str


# ---------------------------------------------------------------------------------------------------------
# probes (replaced by fakes in tests)
# ---------------------------------------------------------------------------------------------------------


def _python_version() -> tuple[int, int, int]:
    """The running interpreter's version."""
    v = sys.version_info
    return (v.major, v.minor, v.micro)


def _now() -> datetime:
    """Current UTC time (doctor output is diagnostic, never content)."""
    return datetime.now(UTC)


def _run(argv: Sequence[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` with launchd's PATH, capturing text output; never raises on a non-zero exit."""
    env = {"PATH": launchd.LAUNCHD_PATH, "LC_ALL": "C", "HOME": str(Path.home()), "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(list(argv), capture_output=True, text=True, check=False, timeout=timeout, env=env)


def _git_path() -> Path:
    """The git the sync will use (gitops' resolver once merged; else /usr/bin/git, else launchd PATH)."""
    try:
        from agentsync import gitops  # noqa: PLC0415 - lazy: doctor must import even if gitops is broken

        return gitops.git_executable()
    except NotImplementedError:
        pass
    candidate = Path("/usr/bin/git")
    if os.access(candidate, os.X_OK):
        return candidate
    found = shutil.which("git", path=launchd.LAUNCHD_PATH)
    if found is None:
        raise FileNotFoundError("git not found in /usr/bin or on launchd's PATH")
    return Path(found)


def _pandoc_path(config: Config) -> Path:
    """The pandoc the converters will run: [convert] pandoc_path, else pypandoc_binary's bundled binary."""
    if config.convert.pandoc_path is not None:
        return config.convert.pandoc_path
    import pypandoc  # noqa: PLC0415 - lazy: optional at doctor import time

    return Path(pypandoc.__file__).parent / "files" / "pandoc"


def _materialize_policy() -> int:
    """The process's materialise-dataless-files I/O policy (materialise.get_materialize_policy)."""
    from agentsync import materialise  # noqa: PLC0415

    return materialise.get_materialize_policy()


def _volume_uuid(path: Path) -> str:
    """Volume UUID of ``path`` (arm_local.volume_uuid)."""
    from agentsync import arm_local  # noqa: PLC0415

    return arm_local.volume_uuid(path)


def _auth_status(config: Config) -> _AuthProbe:
    """Cached sign-in state and token-cache backend (graph.auth; no network)."""
    from agentsync.graph import auth  # noqa: PLC0415

    provider = auth.MsalAuth(auth.settings_from_config(config))
    status = provider.status()
    return _AuthProbe(status.signed_in, status.username, status.cache_backend)


def _is_loaded(label: str) -> bool:
    """launchd.is_loaded with the real launchctl."""
    return launchd.is_loaded(label)


def _first_entry(path: Path) -> str | None:
    """Name of the first directory entry of ``path`` (None when empty); lists metadata only, opens no file."""
    with os.scandir(path) as it:
        entry = next(it, None)
    return None if entry is None else entry.name


def _disk_free(path: Path) -> int:
    """Free bytes available to this user on the volume holding ``path``."""
    return shutil.disk_usage(path).free


def _process_image() -> Path:
    """The executable image this process runs (what TCC grants attach to), via proc_pidpath."""
    if sys.platform == "darwin":
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            buf = ctypes.create_string_buffer(4096)
            n = libc.proc_pidpath(os.getpid(), buf, ctypes.c_uint32(len(buf)))
            if n > 0:
                return Path(buf.raw[:n].decode("utf-8", errors="replace"))
        except (OSError, AttributeError):
            pass
    return Path(os.path.realpath(sys.executable))


def _in_launchd_job(config: Config) -> bool:
    """True when doctor itself runs inside one of our LaunchAgents (so TCC results are authoritative)."""
    return os.environ.get("XPC_SERVICE_NAME", "").startswith(config.launchd_label_prefix + ".")


# ---------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------


def _fda_target(image: Path) -> Path:
    """The path to add under Full Disk Access: the enclosing .app bundle if there is one, else the binary."""
    for parent in image.parents:
        if parent.suffix == ".app":
            return parent
    return image


def _fda_fix(image: Path) -> str:
    """The exact Full Disk Access instruction for ``image``."""
    target = _fda_target(image)
    return (
        f"grant Full Disk Access to {target} (System Settings > Privacy & Security > Full Disk Access > '+', "
        "Cmd-Shift-G to paste the path; on a managed Mac: an MDM PPPC profile allowing SystemPolicyAllFiles "
        "for it)"
    )


def _nearest_existing(path: Path) -> Path:
    """``path`` or its closest existing ancestor."""
    p = expand(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def _gib(n: int) -> str:
    """Format a byte count in GiB with one decimal."""
    return f"{n / 1024**3:.1f} GiB"


def _age_s(iso: object, now: datetime) -> int | None:
    """Seconds since an ISO-8601 ``...Z`` timestamp; None if absent or malformed."""
    if not isinstance(iso, str):
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00") if iso.endswith("Z") else iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int((now - dt).total_seconds())


def _ok(name: str, detail: str, severity: Severity = Severity.INFO) -> CheckResult:
    """A passing check."""
    return CheckResult(name=name, ok=True, detail=detail, severity=severity)


def _bad(name: str, detail: str, severity: Severity = Severity.ERROR, fix: str | None = None) -> CheckResult:
    """A failing check."""
    return CheckResult(name=name, ok=False, detail=detail, severity=severity, fix=fix)


# ---------------------------------------------------------------------------------------------------------
# checks, in run order
# ---------------------------------------------------------------------------------------------------------


def _check_python(config: Config) -> list[CheckResult]:
    """Python >= 3.11."""
    v = _python_version()
    text = f"Python {v[0]}.{v[1]}.{v[2]} at {sys.executable}"
    if v[:2] >= _MIN_PYTHON:
        return [_ok("python", text)]
    return [_bad("python", f"{text}; agentsync needs >= 3.11", fix="uv python install 3.11 && uv sync")]


def _check_git(config: Config) -> list[CheckResult]:
    """git resolves to an absolute path and runs."""
    try:
        git = _git_path()
    except (OSError, ValueError) as exc:
        return [_bad("git", f"not found: {exc}", fix="xcode-select --install")]
    if not git.is_absolute():
        return [_bad("git", f"resolved to a relative path {git}", fix="install git at /usr/bin/git")]
    cp = _run([str(git), "--version"], timeout=30)
    out = cp.stdout.strip()
    if cp.returncode == 0 and out.startswith("git version"):
        return [_ok("git", f"{out} at {git}")]
    detail = (cp.stderr or cp.stdout).strip().splitlines()
    first = detail[0] if detail else f"exit {cp.returncode}"
    return [_bad("git", f"{git} --version failed: {first}", fix="xcode-select --install")]


def _check_pandoc(config: Config) -> list[CheckResult]:
    """pandoc (configured or bundled) runs and reports a version."""
    fix = "uv sync (reinstalls pypandoc_binary) or set [convert] pandoc_path to an absolute pandoc"
    try:
        pandoc = _pandoc_path(config)
    except ImportError as exc:
        return [_bad("pandoc", f"pypandoc_binary is not importable: {exc}", fix=fix)]
    if not pandoc.is_absolute():
        return [
            _bad("pandoc", f"pandoc path {pandoc} is not absolute (launchd has no Homebrew PATH)", fix=fix)
        ]
    if not os.access(pandoc, os.X_OK):
        return [_bad("pandoc", f"{pandoc} is missing or not executable", fix=fix)]
    cp = _run([str(pandoc), "--version"], timeout=60)
    lines = cp.stdout.strip().splitlines()
    if cp.returncode == 0 and lines and lines[0].startswith("pandoc"):
        return [_ok("pandoc", f"{lines[0]} at {pandoc}")]
    return [
        _bad(
            "pandoc", f"{pandoc} --version failed (exit {cp.returncode}): {cp.stderr.strip()[:200]}", fix=fix
        )
    ]


def _check_docs_repo(config: Config) -> list[CheckResult]:
    """docs_repo outside CloudStorage, a git repo (or creatable), no symlinks."""
    repo = expand(config.docs_repo)
    out: list[CheckResult] = []
    real = Path(os.path.realpath(repo))
    if is_cloud_path(repo) or is_cloud_path(real):
        out.append(
            _bad(
                "docs_repo.location",
                f"{repo} (real path {real}) is inside ~/Library/CloudStorage; a git dir in a File Provider "
                "tree inherits every sync-client failure",
                fix="set [agentsync] docs_repo to a local path such as ~/agent-context/docs",
            )
        )
    else:
        out.append(_ok("docs_repo.location", f"{repo} is outside ~/Library/CloudStorage"))

    is_git = False
    if not repo.exists() and not repo.is_symlink():
        anchor = _nearest_existing(repo.parent)
        if anchor.is_dir() and os.access(anchor, os.W_OK | os.X_OK):
            out.append(_ok("docs_repo.git", f"{repo} does not exist yet; `agentsync init` creates it"))
        else:
            out.append(
                _bad(
                    "docs_repo.git",
                    f"{repo} does not exist and {anchor} is not writable",
                    fix=f"mkdir -p {repo}",
                )
            )
    elif not repo.is_dir():
        out.append(_bad("docs_repo.git", f"{repo} exists but is not a directory", fix=f"move {repo} aside"))
    elif (repo / ".git").exists():
        is_git = True
        out.append(_ok("docs_repo.git", f"{repo} is a git repository"))
    else:
        out.append(_ok("docs_repo.git", f"{repo} is not a git repo yet; `agentsync init` runs git init"))

    if repo.is_symlink():
        out.append(
            _bad(
                "docs_repo.symlinks",
                f"{repo} is a symlink to {repo.readlink()}",
                fix=f"set [agentsync] docs_repo to the real directory {real}",
            )
        )
    elif is_git:
        git = _git_path()
        cp = _run([str(git), "-C", str(repo), "ls-files", "-s", "-z"], timeout=120)
        if cp.returncode != 0:
            out.append(
                _bad("docs_repo.symlinks", f"git ls-files failed: {cp.stderr.strip()[:200]}", Severity.WARN)
            )
        else:
            links = sorted(
                rec.split("\t", 1)[1]
                for rec in cp.stdout.split("\0")
                if rec.startswith("120000 ") and "\t" in rec
            )
            if links:
                shown = ", ".join(links[:5]) + (f" (+{len(links) - 5} more)" if len(links) > 5 else "")
                out.append(
                    _bad(
                        "docs_repo.symlinks",
                        f"{len(links)} tracked symlink(s): {shown}",
                        fix=f"git -C {repo} rm --cached <path> for each, and replace them with real files",
                    )
                )
            else:
                out.append(_ok("docs_repo.symlinks", "no tracked symlinks"))
    else:
        out.append(_ok("docs_repo.symlinks", "not a symlink"))
    return out


def _check_state_dir(config: Config) -> list[CheckResult]:
    """state_dir exists with mode 0700 (owned by this user); the db and token fallback are 0600."""
    sp = config.state_paths
    root = expand(sp.root)
    out: list[CheckResult] = []
    try:
        st = root.stat()
    except FileNotFoundError:
        out.append(
            _bad(
                "state_dir",
                f"{root} does not exist; the first sync creates it",
                Severity.WARN,
                fix=f"mkdir -m 700 -p '{root}'",
            )
        )
        return out
    if not stat.S_ISDIR(st.st_mode):
        out.append(_bad("state_dir", f"{root} is not a directory", fix=f"move '{root}' aside"))
        return out
    mode = stat.S_IMODE(st.st_mode)
    if st.st_uid != os.getuid():
        out.append(
            _bad(
                "state_dir",
                f"{root} is owned by uid {st.st_uid}, not {os.getuid()}",
                fix=f"sudo chown -R {os.getuid()} '{root}'",
            )
        )
    elif mode != 0o700:
        out.append(
            _bad(
                "state_dir",
                f"{root} has mode {mode:04o}; cursors and tokens need 0700",
                fix=f"chmod 700 '{root}'",
            )
        )
    else:
        out.append(_ok("state_dir", f"{root} (mode 0700)"))

    loose: list[str] = []
    present: list[str] = []
    for p in (
        sp.db,
        sp.db.with_name(sp.db.name + "-wal"),
        sp.db.with_name(sp.db.name + "-shm"),
        sp.token_cache_fallback,
    ):
        try:
            m = stat.S_IMODE(p.lstat().st_mode)
        except FileNotFoundError:
            continue
        present.append(p.name)
        if m & 0o077:
            loose.append(f"{p.name} ({m:04o})")
    if loose:
        paths = " ".join(f"'{root / n.split(' ')[0]}'" for n in loose)
        out.append(
            _bad("state_dir.files", f"readable by others: {', '.join(loose)}", fix=f"chmod 600 {paths}")
        )
    elif present:
        out.append(_ok("state_dir.files", f"{', '.join(present)} are 0600"))
    else:
        out.append(_ok("state_dir.files", "no manifest yet (created 0600 by the first sync)"))
    return out


def _check_local_source(config: Config, src: SourceConfig, image: Path) -> list[CheckResult]:
    """One local/inbox source: listable, sentinel present, volume UUID readable."""
    base = f"source.{src.id}"
    if not src.is_live:
        return [_ok(base, f"{src.state.value}: not checked")]
    if src.path is None:
        return [_bad(base, "no path configured", fix=f"set path in the [[source]] with id = {src.id!r}")]
    root = expand(src.path)
    cloud = is_cloud_path(root)
    out: list[CheckResult] = []
    listed = False
    try:
        first = _first_entry(root)
        listed = True
        if first is None:
            why = (
                "a File Provider folder that has not been enumerated, or a TCC denial reading as empty"
                if cloud
                else "the folder is empty"
            )
            out.append(
                _bad(
                    f"{base}.listable",
                    f"{root} lists 0 entries ({why})",
                    Severity.WARN,
                    fix=f"open {root} in Finder once; if still empty: {_fda_fix(image)}" if cloud else None,
                )
            )
        else:
            out.append(_ok(f"{base}.listable", f"{root} is listable"))
    except FileNotFoundError:
        out.append(
            _bad(
                f"{base}.listable",
                f"{root} does not exist",
                fix=f"fix path in [[source]] id = {src.id!r}, or sign in to the sync client",
            )
        )
    except NotADirectoryError:
        out.append(
            _bad(
                f"{base}.listable",
                f"{root} is not a directory",
                fix=f"point [[source]] id = {src.id!r} path at a folder",
            )
        )
    except PermissionError as exc:
        if exc.errno == errno.EPERM:
            out.append(
                _bad(
                    f"{base}.listable",
                    f"{root}: Operation not permitted (macOS privacy/TCC)",
                    fix=_fda_fix(image),
                )
            )
        else:
            out.append(_bad(f"{base}.listable", f"{root}: permission denied", fix=f"chmod u+rx '{root}'"))
    except OSError as exc:
        out.append(_bad(f"{base}.listable", f"{root}: {exc.strerror or exc}"))

    if src.sentinel:
        target = root / src.sentinel
        try:
            target.lstat()  # lstat never hydrates a dataless placeholder
            out.append(_ok(f"{base}.sentinel", f"{src.sentinel} present"))
        except FileNotFoundError:
            out.append(
                _bad(
                    f"{base}.sentinel",
                    f"{target} is missing: every pass would be incomplete (no deletions, no cursor)",
                    fix=f"restore {target}, or change sentinel in [[source]] id = {src.id!r}",
                )
            )
        except PermissionError:
            out.append(_bad(f"{base}.sentinel", f"{target}: not permitted (TCC)", fix=_fda_fix(image)))
    elif cloud:
        out.append(
            _bad(
                f"{base}.sentinel",
                "no sentinel: a TCC-hidden or unenumerated tree cannot be told apart from an empty one",
                Severity.WARN,
                fix=f'set sentinel = "<a file that always exists>" in [[source]] id = {src.id!r}',
            )
        )
    else:
        out.append(_ok(f"{base}.sentinel", "no sentinel configured"))

    kind = "File Provider root" if cloud else "volume"
    if not listed and not root.exists():
        out.append(_bad(f"{base}.volume", f"{kind} UUID not checked: {root} is missing"))
        return out
    try:
        uuid = _volume_uuid(root)
        out.append(_ok(f"{base}.volume", f"{kind} UUID {uuid}"))
    except NotImplementedError:
        out.append(
            _bad(
                f"{base}.volume",
                f"{kind} UUID not checked: arm_local.volume_uuid is not implemented",
                Severity.WARN,
            )
        )
    except OSError as exc:
        out.append(
            _bad(
                f"{base}.volume",
                f"{kind} UUID unreadable for {root}: {exc}; stable ids cannot be formed",
                fix="check the volume is mounted (diskutil info <mount>)",
            )
        )
    return out


def _check_sources(config: Config) -> list[CheckResult]:
    """Every local/inbox source in configuration order, plus the TCC context note for cloud roots."""
    image = _process_image()
    out: list[CheckResult] = []
    local = [s for s in config.sources if s.kind in (SourceKind.LOCAL, SourceKind.INBOX)]
    for src in local:
        try:
            out.extend(_check_local_source(config, src, image))
        except Exception as exc:
            log.debug("doctor: source %s check crashed", src.id, exc_info=True)
            out.append(_bad(f"source.{src.id}", f"check crashed: {type(exc).__name__}: {exc}"))
    if any(s.is_live and s.path is not None and is_cloud_path(s.path) for s in local):
        if _in_launchd_job(config):
            out.append(
                _ok("tcc", f"checked inside the LaunchAgent ({image}): results above are authoritative")
            )
        else:
            out.append(
                _ok(
                    "tcc",
                    f"cloud roots were listed with this terminal's privacy grants; the LaunchAgent runs "
                    f"{image} and needs its own: {_fda_fix(image)}",
                )
            )
    return out


def _check_materialise(config: Config) -> list[CheckResult]:
    """The materialise-dataless-files I/O policy is readable (so fail-closed can be enforced)."""
    if sys.platform != "darwin":
        return [_ok("materialise.policy", f"not macOS ({sys.platform}): no dataless files")]
    try:
        p = _materialize_policy()
    except NotImplementedError:
        return [
            _bad(
                "materialise.policy",
                "not checked: materialise.get_materialize_policy is not implemented",
                Severity.WARN,
            )
        ]
    except OSError as exc:
        return [_bad("materialise.policy", f"getiopolicy_np failed: {exc}; hydration cannot be fail-closed")]
    name = _POLICY_NAMES.get(p, f"unknown({p})")
    if p not in _POLICY_NAMES:
        return [_bad("materialise.policy", f"unexpected process policy {name}")]
    return [_ok("materialise.policy", f"process policy {name}; the cycle sets it off and opts in per read")]


def _check_graph(config: Config) -> list[CheckResult]:
    """Client id set, token cache backend, signed in (cache only, no network)."""
    live_graph = [s.id for s in config.live_sources() if s.kind.is_graph]
    if config.graph.client_id is None:
        return [_ok("graph.client_id", "not set: Graph sources are off; local sources work without IT")]
    out = [_ok("graph.client_id", f"{config.graph.client_id} (authority {config.graph.authority})")]
    severity = Severity.ERROR if live_graph else Severity.WARN
    try:
        status = _auth_status(config)
    except NotImplementedError:
        out.append(_bad("graph.auth", "not checked: graph.auth is not implemented", Severity.WARN))
        return out
    except Exception as exc:  # MSAL / Keychain errors are many; each is a diagnosis, not a crash
        out.append(
            _bad(
                "graph.auth",
                f"cannot read the token cache: {type(exc).__name__}: {exc}",
                severity,
                fix="agentsync logout && agentsync login",
            )
        )
        return out
    if status.cache_backend == "keychain":
        out.append(_ok("graph.token_cache", "login Keychain"))
    else:
        out.append(
            _bad(
                "graph.token_cache",
                f"{status.cache_backend}: the Keychain was unavailable",
                Severity.WARN,
                fix="run `agentsync login` from a logged-in GUI session so the login Keychain is used",
            )
        )
    if status.signed_in:
        out.append(_ok("graph.signed_in", f"as {status.username or '<unknown user>'}"))
    else:
        needed = f" ({len(live_graph)} live Graph source(s): {', '.join(live_graph)})" if live_graph else ""
        out.append(_bad("graph.signed_in", f"no cached account{needed}", severity, fix="agentsync login"))
    return out


def _check_disk(config: Config) -> list[CheckResult]:
    """At least 2 GiB free on the state and docs volumes."""
    out: list[CheckResult] = []
    for name, path in (("disk.state", config.state_dir), ("disk.docs", config.docs_repo)):
        anchor = _nearest_existing(path)
        free = _disk_free(anchor)
        if free >= _MIN_FREE_BYTES:
            out.append(_ok(name, f"{_gib(free)} free at {anchor}"))
        else:
            out.append(
                _bad(
                    name,
                    f"only {_gib(free)} free at {anchor} (need >= {_gib(_MIN_FREE_BYTES)})",
                    fix=f"free disk space on the volume holding {anchor}",
                )
            )
    return out


def _check_launchd_job(spec: launchd.AgentSpec, suffix: str) -> CheckResult:
    """One LaunchAgent: installed, current, fail-closed, interpreter present, loaded."""
    name = f"launchd.{suffix}"
    path = launchd.plist_path(spec.label)
    installed = launchd._installed_plist(spec.label)  # ops-internal helper
    if installed is None:
        if path.exists():
            return _bad(name, f"{path} is not a readable plist", Severity.WARN, fix="agentsync install-agent")
        return _bad(
            name, f"{spec.label} is not installed (no {path})", Severity.WARN, fix="agentsync install-agent"
        )
    problems: list[tuple[Severity, str, str]] = []
    args = installed.get("ProgramArguments")
    program = args[0] if isinstance(args, list) and args and isinstance(args[0], str) else None
    if program is None or not os.access(program, os.X_OK):
        problems.append(
            (Severity.ERROR, f"job interpreter {program!r} is missing", "agentsync install-agent")
        )
    if installed.get("MaterializeDatalessFiles") is not False:
        problems.append(
            (
                Severity.ERROR,
                "plist does not set MaterializeDatalessFiles=false (hydration not fail-closed)",
                "agentsync install-agent",
            )
        )
    expected = plistlib.loads(launchd.render_plist(spec))
    if dict(installed) != expected:
        diff = sorted(k for k in set(expected) | set(installed) if expected.get(k) != installed.get(k))
        problems.append(
            (
                Severity.WARN,
                f"installed plist differs from this config/interpreter ({', '.join(diff)})",
                "agentsync install-agent",
            )
        )
    loaded = _is_loaded(spec.label)
    if not loaded:
        problems.append(
            (Severity.WARN, "installed but not loaded", f"launchctl bootstrap gui/{os.getuid()} '{path}'")
        )
    if not problems:
        interval = installed.get("StartInterval")
        return _ok(name, f"{spec.label} loaded (StartInterval {interval} s)")
    rank = {Severity.ERROR: 0, Severity.WARN: 1, Severity.INFO: 2}
    problems.sort(key=lambda p: rank[p[0]])
    return _bad(
        name, f"{spec.label}: " + "; ".join(p[1] for p in problems), problems[0][0], fix=problems[0][2]
    )


def _check_launchd(config: Config) -> list[CheckResult]:
    """Both LaunchAgents (WARN when not installed or not loaded)."""
    out: list[CheckResult] = []
    builders: tuple[tuple[str, Callable[[Config], launchd.AgentSpec]], ...] = (
        ("poll", launchd.poll_spec),
        ("reconcile", launchd.reconcile_spec),
    )
    for suffix, build in builders:
        try:
            out.append(_check_launchd_job(build(config), suffix))
        except Exception as exc:
            out.append(
                _bad(f"launchd.{suffix}", f"check crashed: {type(exc).__name__}: {exc}", Severity.WARN)
            )
    return out


def _check_lock(config: Config) -> list[CheckResult]:
    """Who holds the single-writer lock, whether it is silent, and whether a crash left it stale."""
    path = config.state_paths.lock
    if not path.exists():
        return [_ok("lock", "no lock file yet")]
    held = SingleWriterLock.is_held(path)
    body = SingleWriterLock.read_body(path).strip()
    desc = SingleWriterLock.describe(path)
    if held:
        holder = SingleWriterLock.read_holder(path)
        age = _age_s(holder.get("last_beat_at"), _now()) if holder else None
        limit = max(2 * config.reconcile_interval_s, 3600)
        if age is not None and age > limit:
            pid = holder.get("pid") if holder else None
            return [
                _bad(
                    "lock",
                    f"held by {desc}; silent for {age}s (> {limit}s)",
                    Severity.WARN,
                    fix=f"if that process is hung: kill {pid}" if isinstance(pid, int) else None,
                )
            ]
        return [_ok("lock", f"held by {desc} (a cycle is running)")]
    if body:
        return [
            _bad(
                "lock",
                f"free, but {desc} ended without releasing it; the next cycle breaks it and runs FULL passes",
                Severity.WARN,
                fix=f"read the crash in {config.log_dir}/*.err.log",
            )
        ]
    return [_ok("lock", "free")]


def _check_heartbeat(config: Config) -> list[CheckResult]:
    """Per live source: auth state, last success within 3 x cadence, enumeration completing."""
    path = config.state_paths.heartbeat
    try:
        beats: Mapping[str, Mapping[str, object]] = read_heartbeat(path)
    except ValueError as exc:
        return [_bad("heartbeat", f"{exc}; the next completed pass rewrites it", Severity.WARN)]
    now = _now()
    out: list[CheckResult] = []
    for src in config.live_sources():
        name = f"heartbeat.{src.id}"
        entry = beats.get(src.id)
        if entry is None:
            out.append(_ok(name, "no completed pass recorded yet"))
            continue
        if entry.get("auth_state") == "REAUTH_REQUIRED":
            out.append(
                _bad(
                    name,
                    "auth: REAUTH_REQUIRED; no Graph cursor advances until you sign in again",
                    fix="agentsync login",
                )
            )
            continue
        limit = _STALE_FACTOR * max(src.cadence_s, config.poll_interval_s)
        last = entry.get("last_success_at")
        age = _age_s(last, now)
        incomplete = entry.get("consecutive_incomplete")
        if age is None:
            out.append(
                _bad(
                    name, "no successful pass yet", Severity.WARN, fix=f"agentsync sync --source {src.id} -v"
                )
            )
        elif age > limit:
            out.append(
                _bad(
                    name,
                    f"last success {last} ({age}s ago) > {_STALE_FACTOR} x cadence ({limit}s)",
                    Severity.WARN,
                    fix=f"agentsync sync --source {src.id} -v",
                )
            )
        elif isinstance(incomplete, int) and incomplete >= _INCOMPLETE_RUNS:
            out.append(
                _bad(
                    name,
                    f"enumeration incomplete for {incomplete} consecutive passes (deletions held)",
                    Severity.WARN,
                    fix=f"agentsync doctor; agentsync sync --mode reconcile --source {src.id}",
                )
            )
        else:
            out.append(_ok(name, f"last success {last} ({age}s ago, {entry.get('pass_kind')})"))
    return out


def _check_logs(config: Config) -> list[CheckResult]:
    """launchd never rotates StandardOut/ErrorPath: warn before a log grows without bound."""
    log_dir = expand(config.log_dir)
    if not log_dir.is_dir():
        return [_ok("logs", f"{log_dir} does not exist yet")]
    files = sorted(p for p in log_dir.glob("*.log") if p.is_file())
    big = [(p, p.stat().st_size) for p in files if p.stat().st_size > _MAX_LOG_BYTES]
    if big:
        names = ", ".join(f"{p.name} ({s // 1024**2} MiB)" for p, s in big)
        return [
            _bad("logs", f"large logs: {names}", Severity.WARN, fix="; ".join(f": > '{p}'" for p, _ in big))
        ]
    return [_ok("logs", f"{len(files)} log file(s) in {log_dir}")]


_CHECKS: tuple[tuple[str, Callable[[Config], list[CheckResult]]], ...] = (
    ("python", _check_python),
    ("git", _check_git),
    ("pandoc", _check_pandoc),
    ("docs_repo", _check_docs_repo),
    ("state_dir", _check_state_dir),
    ("sources", _check_sources),
    ("materialise", _check_materialise),
    ("graph", _check_graph),
    ("disk", _check_disk),
    ("launchd", _check_launchd),
    ("lock", _check_lock),
    ("heartbeat", _check_heartbeat),
    ("logs", _check_logs),
)


def run_checks(config: Config) -> list[CheckResult]:
    """Run every check, in a fixed order, never raising for a single failed check.

    python >= 3.11; git absolute path; pandoc (configured or bundled) runs and reports a version; docs_repo
    outside CloudStorage, a git repo (or creatable), no symlinks; state_dir exists with mode 0700 and the db
    0600; each local/inbox source root is listable (EPERM => "grant Full Disk Access to <interpreter>"),
    sentinel present, File Provider root (volume UUID readable); materialisation policy readable; graph:
    client id set, token cache backend (Keychain vs file), signed in (no network); disk free >= 2 GiB on state
    and docs volumes; launchd agents loaded (WARN if not).
    """
    results: list[CheckResult] = []
    for group, check in _CHECKS:
        try:
            results.extend(check(config))
        except Exception as exc:
            log.debug("doctor: check %s crashed", group, exc_info=True)
            results.append(_bad(group, f"check crashed: {type(exc).__name__}: {exc}"))
    return results


_TAGS = {Severity.ERROR: "FAIL", Severity.WARN: "warn", Severity.INFO: "info"}


def format_results(results: list[CheckResult]) -> str:
    """Render results as aligned text lines ``[ok|FAIL|warn] name — detail (fix: ...)``."""
    if not results:
        return ""
    width = max(len(r.name) for r in results)
    lines: list[str] = []
    for r in results:
        tag = "ok" if r.ok else _TAGS[r.severity]
        line = f"[{tag:<4}] {r.name:<{width}} — {r.detail}"
        if not r.ok and r.fix:
            line += f" (fix: {r.fix})"
        lines.append(line)
    return "\n".join(lines)
