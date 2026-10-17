"""Shared fixtures: isolated HOME, tmp docs repo, tmp state dir, a local source tree, a sample Config.

Safety: every test runs with HOME pointed at a tmp dir and CLAUDE_CONFIG_DIR unset, so ``~`` defaults never
touch the real ~/agent-context, ~/Library/Application Support/agentsync, ~/Library/LaunchAgents, or the
Claude Code skill folders (~/.claude/skills, $CLAUDE_CONFIG_DIR/skills) every sync writes.  Tests marked
``fileprovider`` or ``network`` are skipped unless AGENTSYNC_E2E_FILEPROVIDER=1 / AGENTSYNC_E2E_NETWORK=1.
"""

from __future__ import annotations

import contextlib
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from agentsync import arm_local
from agentsync.config import Config, parse_config
from agentsync.gitops import ensure_repo
from fixtures.make_fixtures import make_fixtures

if TYPE_CHECKING:
    from agentsync.ops.doctor import CheckResult

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
_REAL_IS_DOMAIN = arm_local._is_domain


def _fileprovider_root() -> Path:
    """The OneDrive File Provider mount for opt-in live tests: ``AGENTSYNC_FP_ROOT``, else the first
    ``~/Library/CloudStorage/OneDrive-*`` folder (no tenant name is hardcoded in this public repo)."""
    env = os.environ.get("AGENTSYNC_FP_ROOT")
    if env:
        return Path(env).expanduser()
    found = sorted((REAL_HOME / "Library" / "CloudStorage").glob("OneDrive-*"))
    return found[0] if found else REAL_HOME / "Library" / "CloudStorage" / "OneDrive-Contoso"


FILEPROVIDER_ROOT = _fileprovider_root()
E2E_SUBDIR = "agentsync-e2e"  # the ONLY place tests may write inside the File Provider mount

requires_macos = pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only syscalls")

FILE_PROVIDER_DOMAIN_XATTR = "com.apple.file-provider-domain-id"
"""The attribute a real File Provider domain root carries (``~/Library/CloudStorage/<provider>``)."""


def file_provider_domain(provider: Path) -> Path:
    """Make ``provider`` (``<HOME>/Library/CloudStorage/<provider>``) and give it the attribute a real File
    Provider domain root carries, so a sandbox fixture under it gets the walk's zero-children rule (b13 R1):
    a plain folder there is not a domain. Off macOS nothing is written: the walk cannot read the attribute
    there and treats the folder as a domain."""
    provider.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        from agentsync.tm_exclude import _libc_xattr  # noqa: PLC0415 - macOS only

        value = b"agentsync-test"
        name = FILE_PROVIDER_DOMAIN_XATTR.encode()
        assert _libc_xattr().setxattr(os.fsencode(provider), name, value, len(value), 0, 0x0001) == 0
    return provider


@pytest.fixture(autouse=True)
def _provider_folders_are_domains() -> Iterator[None]:
    """b13 R1: a zero-child folder under ~/Library/CloudStorage is unknown only in a File Provider domain,
    and a test's provider folder is a plain mkdir. In-process walks read every provider folder as a domain
    (the rule the tests were written for) unless the test asks for ``real_domain_check``, where
    ``file_provider_domain`` gives a provider folder the real attribute. A walk in another process does not
    see this patch: such a test needs ``file_provider_domain`` (none does today). Its own MonkeyPatch, as
    ``_isolate_home`` has."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(arm_local, "_is_domain", lambda provider: True)
        yield


@pytest.fixture
def real_domain_check(_provider_folders_are_domains: None) -> Iterator[None]:
    """The walk reads each provider folder's own attributes (``arm_local._is_domain``), as in production."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(arm_local, "_is_domain", _REAL_IS_DOMAIN)
        yield


def pytest_configure(config: pytest.Config) -> None:
    """A warm dated xcrun db for the whole run, and dead basetemp locks cleared before this run's own.

    On macOS /usr/bin/git is an xcrun shim, and under a suite its lookup cache missed about once a second:
    each miss cost ~77M instructions and an xcodebuild child, and the shared db was rewritten 71 times in 90 s
    (load root cause 2026-10-17 X3c). One db per day, in the user's own temp folder, warmed once here under
    the real HOME, serves every worker: they inherit the variable from the controller. An xcrun_db already
    set wins."""
    controller = not hasattr(config, "workerinput")
    if sys.platform == "darwin" and "xcrun_db" not in os.environ:
        db = _dated_xcrun_db()
        if db is not None:
            os.environ["xcrun_db"] = str(db)  # noqa: SIM112 - xcrun reads this lower-case name
            if controller:
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    subprocess.run(
                        ["/usr/bin/xcrun", "--find", "git"],
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        timeout=30,
                        check=False,
                    )
    if controller and config.option.basetemp is None:
        _clear_dead_basetemp_locks()


def _dated_xcrun_db() -> Path | None:
    """``<DARWIN_USER_TEMP_DIR>/cc-test-xcrun_db.<yyyymmdd>``: warm across the day's runs, ~1-2 MB a day."""
    try:
        cp = subprocess.run(
            ["/usr/bin/getconf", "DARWIN_USER_TEMP_DIR"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    tmp = cp.stdout.strip()
    if cp.returncode != 0 or not tmp.startswith("/") or not Path(tmp).is_dir():
        return None
    return Path(tmp) / f"cc-test-xcrun_db.{time.strftime('%Y%m%d')}"


def _clear_dead_basetemp_locks() -> None:
    """Unlink ``pytest-of-<user>/pytest-N/.lock`` when the run that wrote it is gone, so pytest's own keep-3
    cleanup at this run's exit deletes those folders now rather than once the lock is 3 days old.

    pytest removes its lock at exit; a run that was SIGKILLed leaves it, and 36 such folders held ~8.7 GB
    (load root cause 2026-10-17 X20). A lock is dead only when its pid is not running, or is running but
    started after the lock was written (the pid was reused). Other runs on this Mac share the folder, so a
    lock this cannot read, or a census it cannot read, keeps every lock, and nothing here deletes a folder.
    One ``ps`` call for all the pids; no signal is sent to any of them."""
    from _pytest.tmpdir import get_user  # noqa: PLC0415 - pytest's own name for the folder

    root = Path(os.environ.get("PYTEST_DEBUG_TEMPROOT") or tempfile.gettempdir()).resolve()
    locks: dict[int, list[tuple[Path, float]]] = {}
    with contextlib.suppress(OSError):
        for lock in (root / f"pytest-of-{get_user() or 'unknown'}").glob("pytest-*/.lock"):
            with contextlib.suppress(OSError, ValueError):
                pid = int(lock.read_bytes().strip())
                if pid > 0:
                    locks.setdefault(pid, []).append((lock, lock.stat().st_mtime))
    if not locks:
        return
    try:
        cp = subprocess.run(
            ["/bin/ps", "-o", "pid=,lstart=", "-p", ",".join(map(str, locks))],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return
    if cp.returncode not in (0, 1) or cp.stderr.strip():
        return  # ps exits 1 when a pid is gone; any other status, or a complaint, is not a census
    started: dict[int, float] = {}
    for line in cp.stdout.splitlines():
        pid_text, _, lstart = line.strip().partition(" ")
        try:
            started[int(pid_text)] = time.mktime(
                time.strptime(" ".join(lstart.split()), "%a %b %d %H:%M:%S %Y")
            )
        except ValueError:
            return
    for pid, held in locks.items():
        for lock, written in held:
            if pid not in started or started[pid] > written + 1:
                with contextlib.suppress(OSError):
                    lock.unlink()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip opt-in markers unless their environment switch is set."""
    gates = {
        "fileprovider": ("AGENTSYNC_E2E_FILEPROVIDER", "set AGENTSYNC_E2E_FILEPROVIDER=1 to touch OneDrive"),
        "network": ("AGENTSYNC_E2E_NETWORK", "set AGENTSYNC_E2E_NETWORK=1 to call real services"),
    }
    for item in items:
        for marker, (env, reason) in gates.items():
            if marker in item.keywords and os.environ.get(env) != "1":
                item.add_marker(pytest.mark.skip(reason=reason))


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip a ``timing`` test when the Mac is too busy for its time bound to mean anything.

    A ``timing`` test asserts a time bound (wall clock or CPU) that is the contract (the report's time
    budget). On a Mac whose load is many runnable processes per core, that time says nothing about the code:
    such a run skips them, and says why; CI's runners and an idle Mac run them. AGENTSYNC_TIMING=1 runs them
    anyway. The load is read when each timing test starts, not once at collection: under ``-n auto`` each
    worker collects at startup, and a long run whose load rose later still ran them (B13 round 7 H78)."""
    if item.get_closest_marker("timing") is None or os.environ.get("AGENTSYNC_TIMING") == "1":
        return
    per_core = os.getloadavg()[0] / (os.cpu_count() or 1)
    if per_core > _TIMING_LOAD_PER_CORE:
        pytest.skip(f"load {per_core:.0f} per core: a time bound cannot be judged")


_TIMING_LOAD_PER_CORE = 4.0
"""The 1-minute load per core above which the ``timing`` tests are skipped (B13 round 2 R98: the report's
budget tests failed at 10 to 60 per core, and pass on CI's runners and an idle Mac, well under 1)."""


@pytest.fixture(autouse=True, scope="session")
def _owner_only_umask() -> Iterator[None]:
    """Tests run under the umask production runs under (LaunchAgent ``Umask = 63``, ``cli.main`` 077)."""
    previous = os.umask(0o077)
    yield
    os.umask(previous)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Its own MonkeyPatch, not the shared ``monkeypatch`` fixture: a test that calls ``monkeypatch.undo()``
    (test_cycle.py does, to heal a fault it injected) must not put HOME back on the real home folder."""
    home = tmp_path_factory.mktemp("home")
    gitconfig = home / ".gitconfig"
    gitconfig.write_text(
        "[user]\n\tname = agentsync-test\n\temail = test@localhost\n[init]\n\tdefaultBranch = main\n"
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HOME", str(home))
        mp.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
        mp.setenv("GIT_CONFIG_NOSYSTEM", "1")
        mp.setenv("LC_ALL", "C")
        mp.setenv("AGENTSYNC_TM_EXCLUDE", "0")  # never run the (slow, sticky) real tmutil from tests
        mp.setenv("AGENTSYNC_OCR", "0")  # no test runs an OCR helper unless it asks to (tests/test_ocr.py)
        mp.delenv("AGENTSYNC_CONFIG", raising=False)
        mp.delenv("CLAUDE_CONFIG_DIR", raising=False)  # every sync writes the skill there too
        yield home


@contextlib.contextmanager
def zone(name: str) -> Iterator[None]:
    """Run with ``TZ`` set to ``name``, then put the old value back (or remove it) and run ``tzset`` after
    that, so ``time``'s cached zone follows the restored value. A ``monkeypatch`` restore comes after the
    test, with no ``tzset`` behind it (B13 round 9 K55)."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def fails_without_a_fix(results: Iterable[CheckResult]) -> list[str]:
    """The names of the FAIL results in ``results`` that name no fix."""
    return [r.name for r in results if not r.ok and r.severity.value == "error" and not r.fix]


@pytest.fixture(autouse=True)
def _every_fail_names_its_fix(_isolate_home: Path) -> Iterator[None]:
    """scripts/install.sh says of the ``[FAIL]`` lines above its NEXT that "each names its fix", and the
    setup prompt lets an agent act only on what a line names. So every result the checks of ``status`` give
    in any test is held to that: a FAIL with no fix fails the test that produced it. Both producers are
    wrapped where they are looked up at each call (``doctor.run_checks`` and the integrator's
    ``cli._extra_checks``), so a test reaches the wrapper through ``agentsync status``, ``setup-report`` or
    a direct ``doctor.run_checks(...)`` alike. Its own MonkeyPatch, as ``_isolate_home`` has."""
    from agentsync import cli  # noqa: PLC0415 - not at import time: HOME is the real one there
    from agentsync.ops import doctor  # noqa: PLC0415

    def held(real: Callable[..., list[CheckResult]]) -> Callable[..., list[CheckResult]]:
        def checks(*args: object, **kwargs: object) -> list[CheckResult]:
            results = real(*args, **kwargs)
            bare = fails_without_a_fix(results)
            assert not bare, f"[FAIL] line(s) that name no fix: {', '.join(bare)}"
            return results

        return checks

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(doctor, "run_checks", held(doctor.run_checks))
        mp.setattr(cli, "_extra_checks", held(cli._extra_checks))
        yield


@pytest.fixture(scope="session")
def docs_repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An empty docs repo exactly as ``gitops.ensure_repo`` creates one, built once per session.

    ``ensure_repo`` costs about a dozen git subprocesses.  A test that needs such a repo only as a starting
    point copies this tree (``copy_docs_repo``) instead of paying them again; a test of ``ensure_repo`` itself
    still calls it.  Built under its own HOME and git config, because a session fixture is set up before the
    function-scoped ``_isolate_home``."""
    home = tmp_path_factory.mktemp("template-home")
    gitconfig = home / ".gitconfig"
    gitconfig.write_text("[init]\n\tdefaultBranch = main\n")
    repo = tmp_path_factory.mktemp("template") / "docs"
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HOME", str(home))
        mp.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
        mp.setenv("GIT_CONFIG_NOSYSTEM", "1")
        mp.setenv("LC_ALL", "C")
        mp.setenv("AGENTSYNC_OCR", "0")
        assert ensure_repo(repo) is True
    return repo


def copy_docs_repo(template: Path, repo: Path) -> Path:
    """Copy ``docs_repo_template`` to ``repo`` (parents created owner-only, as ``ensure_repo`` does)."""
    repo.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copytree(template, repo)
    return repo


@pytest.fixture(scope="session")
def fixture_files(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Real sample files (docx, xlsx x2, pptx, pdf, eml, teams.json, md, txt, html, csv, vtt), built once."""
    return make_fixtures(tmp_path_factory.mktemp("fixtures"))


@pytest.fixture
def tmp_docs_repo(tmp_path: Path) -> Path:
    """An empty git repo at <tmp>/agent-context/docs (branch main, local identity)."""
    repo = tmp_path / "agent-context" / "docs"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "core.precomposeunicode", "true"], check=True)
    return repo


@pytest.fixture
def tmp_state_dir(tmp_path: Path) -> Path:
    """A 0700 state dir outside the docs repo."""
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    return state


@pytest.fixture
def local_source_dir(tmp_path: Path, fixture_files: dict[str, Path]) -> Path:
    """A local source tree: every fixture under ``projects/``, a nested dir, the ``README.txt`` sentinel."""
    root = tmp_path / "source"
    (root / "projects" / "acme").mkdir(parents=True)
    for name, path in fixture_files.items():
        shutil.copy2(path, root / "projects" / name)
    shutil.copy2(fixture_files["sample.docx"], root / "projects" / "acme" / "Kickoff Notes.docx")
    (root / "README.txt").write_text("sentinel: this file must always be present\n", encoding="utf-8")
    (root / "~$lockfile.docx").write_bytes(b"lock")
    return root


def config_text(docs_repo: Path, state_dir: Path, cache_dir: Path, source_dir: Path) -> str:
    """Return a sources.toml pointing at tmp dirs with one live local source ``local-fixture``."""
    return f"""
[agentsync]
docs_repo = "{docs_repo}"
state_dir = "{state_dir}"
cache_dir = "{cache_dir}"
log_dir = "{state_dir / "logs"}"

[[source]]
id = "local-fixture"
kind = "local"
path = "{source_dir}"
sentinel = "README.txt"
max_materialise_bytes = "50MB"
max_files = 100
"""


@pytest.fixture
def sample_config(tmp_path: Path, tmp_docs_repo: Path, tmp_state_dir: Path, local_source_dir: Path) -> Config:
    """A validated Config over the tmp docs repo, state dir, cache dir and local source tree."""
    cfg_path = tmp_path / "agent-context" / "sources.toml"
    text = config_text(tmp_docs_repo, tmp_state_dir, tmp_path / "cache", local_source_dir)
    cfg_path.write_text(text, encoding="utf-8")
    return parse_config(text, config_path=cfg_path)


@pytest.fixture
def fileprovider_e2e_dir() -> Iterator[Path]:
    """``~/Library/CloudStorage/OneDrive-Contoso/agentsync-e2e`` (real home), created on demand; nothing else.

    Use only from tests marked ``@pytest.mark.fileprovider``.
    """
    if not FILEPROVIDER_ROOT.is_dir():
        pytest.skip(f"File Provider mount not present: {FILEPROVIDER_ROOT}")
    target = FILEPROVIDER_ROOT / E2E_SUBDIR
    target.mkdir(exist_ok=True)
    yield target
