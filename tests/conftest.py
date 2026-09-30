"""Shared fixtures: isolated HOME, tmp docs repo, tmp state dir, a local source tree, a sample Config.

Safety: every test runs with HOME pointed at a tmp dir, so ``~`` defaults never touch the real
~/agent-context, ~/Library/Application Support/agentsync or ~/Library/LaunchAgents.  Tests marked
``fileprovider`` or ``network`` are skipped unless AGENTSYNC_E2E_FILEPROVIDER=1 / AGENTSYNC_E2E_NETWORK=1.
"""

from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentsync.config import Config, parse_config
from fixtures.make_fixtures import make_fixtures

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
FILEPROVIDER_ROOT = REAL_HOME / "Library" / "CloudStorage" / "OneDrive-Contoso"
E2E_SUBDIR = "agentsync-e2e"  # the ONLY place tests may write inside the File Provider mount

requires_macos = pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only syscalls")


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


@pytest.fixture(autouse=True, scope="session")
def _owner_only_umask() -> Iterator[None]:
    """Tests run under the umask production runs under (LaunchAgent ``Umask = 63``, ``cli.main`` 077)."""
    previous = os.umask(0o077)
    yield
    os.umask(previous)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path_factory.mktemp("home")
    gitconfig = home / ".gitconfig"
    gitconfig.write_text(
        "[user]\n\tname = agentsync-test\n\temail = test@localhost\n[init]\n\tdefaultBranch = main\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("AGENTSYNC_TM_EXCLUDE", "0")  # never run the (slow, sticky) real tmutil from tests
    monkeypatch.delenv("AGENTSYNC_CONFIG", raising=False)
    return home


@pytest.fixture(scope="session")
def fixture_files(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Real sample files (docx, xlsx x2, pptx, pdf, eml, teams.json, md, txt, html, csv), built once."""
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
