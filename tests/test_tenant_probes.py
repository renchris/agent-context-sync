"""scripts/tenant-probes.sh run for real: its exit table, the paths past sign-in, the dry run, the parser and
--version.

Nothing here signs in, reaches Graph or touches the Keychain.  The script runs ``"$PY" -I -X utf8 - ...``, and
``-I`` ignores PYTHONPATH, so a path past sign-in passes as ``--python`` a wrapper that drops those four
arguments and runs the real interpreter on a shim file.  The shim replaces MsalAuth and GraphClient, then runs
the script's Python from stdin as ``__main__``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tenant-probes.sh"
SHA = "0123456789abcdef0123456789abcdef01234567"
FILE_NAME = "FILENAME-plan"
LINK = "https://graph.contoso.test/delta?token=LINKTOKEN-"

SHIM = f'''
import hashlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import agentsync.graph.auth
import agentsync.graph.client
from agentsync.errors import AuthRequiredError
from agentsync.graph.errors import AuthBlockedError, NetworkPolicyError

SCENARIO = os.environ["PROBES_SCENARIO"]
HASHES = {{"quickXorHash": "AAAA"}}
ITEMS = (
    {{"id": "1", "name": "{FILE_NAME}.pdf", "size": 9, "cTag": "c", "file": {{"hashes": HASHES}}}},
    {{"id": "2", "name": "{FILE_NAME}.docx", "size": 9, "file": {{"hashes": {{}}}}}},
)


class FakeAuth:
    last_token_source = "cache"

    def __init__(self, settings):
        pass

    def status(self):
        return SimpleNamespace(
            signed_in=True,
            username="owner@contoso.com",
            tenant_id="t",
            cache_backend="keychain",
            sign_in_method="broker",
        )

    def get_token(self):
        if SCENARIO == "required":
            raise AuthRequiredError("no cached token")
        if SCENARIO == "blocked":
            raise AuthBlockedError("blocked: consent", "AADSTS65001", "consent refused")
        return "opaque"


class FakeClient:
    def __init__(self, auth, **kwargs):
        self.links = 0

    def get_json(self, path, params=None):
        if SCENARIO == "network":
            raise NetworkPolicyError("TLS", "intercepted")
        if path.endswith("/root/delta"):
            self.links += 1
            return {{"@odata.deltaLink": "{LINK}" + str(self.links)}}
        return {{"id": path, "value": []}}

    def iter_pages(self, path, params=None):
        if SCENARIO == "mid-run":
            raise AuthRequiredError("the token was revoked")
        yield SimpleNamespace(value=ITEMS, next_link=None)

    def download(self, path, dest, max_bytes=None):
        Path(os.environ["PROBES_RECORD"]).write_text(str(dest.parent), encoding="utf-8")
        dest.write_bytes(b"%PDF-1.4\\n")
        return 9, hashlib.sha256(b"%PDF-1.4\\n").hexdigest()

    def close(self):
        pass


agentsync.graph.auth.MsalAuth = FakeAuth
agentsync.graph.client.GraphClient = FakeClient
exec(compile(sys.stdin.read(), "tenant-probes-embedded", "exec"), {{"__name__": "__main__"}})
'''


def _executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def _shim_python(tmp_path: Path) -> Path:
    """The wrapper for ``--python``: it refuses any other call shape, so a change to the script's is loud."""
    shim = tmp_path / "shim.py"
    shim.write_text(SHIM, encoding="utf-8")
    return _executable(
        tmp_path / "python-wrapper",
        '#!/bin/bash\n[ "$1 $2 $3 $4" = "-I -X utf8 -" ] || exit 99\nshift 4\n'
        f'exec "{sys.executable}" -X utf8 "{shim}" "$@"\n',
    )


def _marker_python(tmp_path: Path) -> tuple[Path, Path]:
    """An interpreter that only leaves a marker: the proof that a usage error did no work."""
    marker = tmp_path / "interpreter-ran"
    return _executable(tmp_path / "python-marker", f'#!/bin/sh\n: > "{marker}"\n'), marker


def _config(tmp_path: Path, *, client_id: bool = True) -> Path:
    """A sources.toml with every path under tmp_path and a direct connection, so no system PAC matters."""
    ctx = tmp_path / "agent-context"
    ctx.mkdir()
    (tmp_path / "src").mkdir()
    graph = 'client_id = "00000000-0000-0000-0000-000000000000"\n' if client_id else ""
    path = ctx / "sources.toml"
    path.write_text(
        f"""[agentsync]
docs_repo = "{ctx / "docs"}"
state_dir = "{tmp_path / "state"}"
cache_dir = "{tmp_path / "cache"}"
principal = "owner@contoso.com"

[graph]
{graph}tenant = "contoso.onmicrosoft.com"

[network]
proxy = "direct"

[[source]]
id = "src"
kind = "local"
path = "{tmp_path / "src"}"
""",
        encoding="utf-8",
    )
    return path


def _run(
    tmp_path: Path,
    *args: str,
    script: Path = SCRIPT,
    python: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    home, tmpdir = tmp_path / "home", tmp_path / "tmpdir"
    home.mkdir(exist_ok=True)
    tmpdir.mkdir(exist_ok=True)
    return subprocess.run(
        ["/bin/bash", str(script), *args],
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(home),
            "TMPDIR": str(tmpdir),
            "AGENTSYNC_OCR": "0",
            "AGENTSYNC_PYTHON": str(python or sys.executable),
            **(env or {}),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def _tree(root: Path) -> list[str]:
    return sorted(str(p) for p in root.rglob("*"))


def _only_report(out: Path) -> Path:
    reports = sorted(out.glob("tenant-probes-*.md"))
    assert len(reports) == 1, reports
    assert reports[0].stat().st_mode & 0o777 == 0o600
    return reports[0]


# ---- the exit table, no stub needed ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "words"),
    [
        (["--frob"], "unknown argument --frob"),
        (["--max-pages", "abc"], "--max-pages needs a non-negative integer, got 'abc'"),
        (["--max-pages=abc"], "--max-pages needs a non-negative integer, got 'abc'"),
        (["--config"], "--config needs a value"),
        (["--out", "--dry-run"], "--out needs a value, got the option --dry-run (write --out=--dry-run"),
        (["--out", "--config=x"], "--out needs a value, got the option --config=x"),
        (["--config="], "--config needs a value"),
        (["--max-bytes="], "--max-bytes needs a value"),
        (["--out", ""], "--out needs a value"),
        (["--", "x"], "unexpected argument x"),
        (["--", "--dry-run"], "unexpected argument --dry-run"),
    ],
)
def test_usage_errors_exit_2_before_any_work(tmp_path: Path, args: list[str], words: str) -> None:
    python, marker = _marker_python(tmp_path)
    before = _tree(tmp_path)
    proc = _run(tmp_path, *args, python=python)
    assert proc.returncode == 2, proc.stderr
    assert proc.stdout == ""
    assert proc.stderr.startswith("usage error: ")
    assert words in proc.stderr
    assert not marker.exists()
    assert [p for p in _tree(tmp_path) if p not in before and not p.endswith(("/home", "/tmpdir"))] == []


def test_missing_python_path_exits_1(tmp_path: Path) -> None:
    proc = _run(tmp_path, "--python", str(tmp_path / "no-such-python"))
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert proc.stderr.startswith("error: no interpreter with agentsync installed")


def test_invalid_sources_toml_exits_78(tmp_path: Path) -> None:
    config = tmp_path / "sources.toml"
    config.write_text("this is [not toml\n", encoding="utf-8")
    proc = _run(tmp_path, "--config", str(config), "--out", str(tmp_path / "x"))
    assert proc.returncode == 78
    assert proc.stdout == ""
    assert proc.stderr.startswith("configuration invalid: ")
    assert not (tmp_path / "x").exists()


def test_client_id_unset_exits_78_with_a_report(tmp_path: Path) -> None:
    proc = _run(tmp_path, "--config", str(_config(tmp_path, client_id=False)))
    assert proc.returncode == 78
    assert proc.stdout == ""
    report = _only_report(tmp_path / "state" / "probes")
    assert proc.stderr == f"not run: [graph] client_id is unset; report {report}\n"
    assert "**Not run:** `[graph] client_id` is unset" in report.read_text(encoding="utf-8")


def test_selftest_exits_0(tmp_path: Path) -> None:
    proc = _run(tmp_path, "--selftest")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.startswith("selftest ok: ")
    assert proc.stderr == ""


# ---- past sign-in, through the shim ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scenario", "code", "stderr", "in_report"),
    [
        ("required", 77, "not signed in; report ", "**reauth-required**"),
        (
            "blocked",
            77,
            "sign-in blocked (blocked: consent); report ",
            "**blocked: consent** (`AADSTS65001`)",
        ),
        ("mid-run", 77, "", "**stopped: sign-in required mid-run**: the token was revoked"),
        ("network", 1, "", "**failed: network-policy (TLS)**"),
    ],
)
def test_past_sign_in_failures_write_the_report(
    tmp_path: Path, scenario: str, code: int, stderr: str, in_report: str
) -> None:
    out = tmp_path / "out"
    proc = _run(
        tmp_path,
        "--config",
        str(_config(tmp_path)),
        "--out",
        str(out),
        "--python",
        str(_shim_python(tmp_path)),
        env={"PROBES_SCENARIO": scenario},
    )
    assert proc.returncode == code, proc.stdout + proc.stderr
    report = _only_report(out)
    assert in_report in report.read_text(encoding="utf-8")
    if stderr:
        assert proc.stdout == ""
        assert proc.stderr == f"{stderr}{report}\n"
    else:
        assert proc.stderr == ""
        assert proc.stdout == f"report: {report}\n"


def test_canned_drive_exits_0_and_keeps_names_and_links_out_of_the_report(tmp_path: Path) -> None:
    out, record = tmp_path / "out", tmp_path / "download-parent"
    proc = _run(
        tmp_path,
        "--config",
        str(_config(tmp_path)),
        "--out",
        str(out),
        "--python",
        str(_shim_python(tmp_path)),
        env={"PROBES_SCENARIO": "ok", "PROBES_RECORD": str(record)},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stderr == ""
    report = _only_report(out)
    assert proc.stdout == f"report: {report}\n"
    text = report.read_text(encoding="utf-8")
    assert "## Probe 1:" in text and "## Probe 4b:" in text and "## Probe 9:" in text
    assert FILE_NAME not in text
    assert "LINKTOKEN" not in text
    store = out / "deltalinks.json"
    assert store.stat().st_mode & 0o777 == 0o600
    assert store.read_text(encoding="utf-8").count("LINKTOKEN") == 3
    temp_root = Path(record.read_text(encoding="utf-8"))  # the folder the two downloads went to
    assert temp_root.is_relative_to(tmp_path / "tmpdir")
    assert not temp_root.exists()


# ---- --dry-run writes nothing ----------------------------------------------------------------------------


@pytest.mark.parametrize("with_out", [False, True])
def test_dry_run_without_client_id_writes_nothing(tmp_path: Path, with_out: bool) -> None:
    config = _config(tmp_path, client_id=False)
    (tmp_path / "home").mkdir()
    (tmp_path / "tmpdir").mkdir()
    out = tmp_path / "x"
    before = _tree(tmp_path)
    proc = _run(tmp_path, "--dry-run", "--config", str(config), *(["--out", str(out)] if with_out else []))
    assert proc.returncode == 78
    assert "client_id is unset" in proc.stderr
    assert "report " not in proc.stderr
    assert proc.stdout == ""
    assert _tree(tmp_path) == before
    assert not out.exists()


# ---- the parser ------------------------------------------------------------------------------------------


def test_equals_form_is_the_separated_form(tmp_path: Path) -> None:
    config = _config(tmp_path)
    proc = _run(tmp_path, f"--config={config}", "--dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.startswith("dry run: nothing is signed in, requested or written")


def test_a_repeat_takes_the_last_value(tmp_path: Path) -> None:
    config = _config(tmp_path)
    proc = _run(tmp_path, "--config", str(config), "--max-pages", "3", "--max-pages=7", "--dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "at most 7 pages" in proc.stdout


def test_the_equals_form_takes_a_value_that_is_an_option(tmp_path: Path) -> None:
    """The escape the refusal of ``--out --dry-run`` names: it reaches OUT and the dry run still runs."""
    proc = _run(tmp_path, "--config", str(_config(tmp_path)), "--out=--dry-run", "--dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"report: {tmp_path}/--dry-run/tenant-probes-" in proc.stdout
    assert not (tmp_path / "--dry-run").exists()


def test_bare_double_dash_ends_the_options(tmp_path: Path) -> None:
    proc = _run(tmp_path, "--config", str(_config(tmp_path)), "--dry-run", "--")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.startswith("dry run: nothing is signed in, requested or written")


# ---- --version and help ----------------------------------------------------------------------------------


def _checkout(tmp_path: Path, layout: str) -> Path:
    """A checkout holding a copy of the script, its .git in the given shape."""
    repo = tmp_path / "checkout"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "scripts" / "tenant-probes.sh")
    dot = repo / ".git"
    if layout == "none":
        return repo
    if layout.startswith("linked"):
        common = tmp_path / "main" / ".git"
        dot = common / "worktrees" / "checkout"
        dot.mkdir(parents=True)
        (dot / "commondir").write_text("../..\n", encoding="utf-8")
        (repo / ".git").write_text(f"gitdir: {dot}\n", encoding="utf-8")
    else:
        common = dot
        dot.mkdir()
    ref = common / "refs" / "heads" / "topic"
    if layout in ("detached", "linked-detached"):
        (dot / "HEAD").write_text(f"{SHA}\n", encoding="utf-8")
    elif layout == "garbage":
        (dot / "HEAD").write_text("not a commit\n", encoding="utf-8")
    else:
        (dot / "HEAD").write_text("ref: refs/heads/topic\n", encoding="utf-8")
        if layout in ("packed", "linked-packed"):
            (common / "packed-refs").write_text(f"# pack-refs\n{SHA} refs/heads/topic\n", encoding="utf-8")
        else:
            ref.parent.mkdir(parents=True)
            ref.write_text(f"{SHA}\n", encoding="utf-8")
    return repo


@pytest.mark.parametrize(
    ("layout", "printed"),
    [
        ("detached", SHA[:12]),
        ("loose", SHA[:12]),
        ("packed", SHA[:12]),
        ("linked-detached", SHA[:12]),
        ("linked-loose", SHA[:12]),
        ("linked-packed", SHA[:12]),
        ("none", "unknown"),
        ("garbage", "unknown"),
    ],
)
def test_version_prints_one_value_and_exits_0(tmp_path: Path, layout: str, printed: str) -> None:
    """One value and nothing else, before the config or the interpreter, and git is never run."""
    repo = _checkout(tmp_path, layout)
    python, marker = _marker_python(tmp_path)
    tools = tmp_path / "tools"
    tools.mkdir()
    git_ran = tmp_path / "git-ran"
    _executable(tools / "git", f'#!/bin/sh\n: > "{git_ran}"\n')
    proc = _run(
        tmp_path,
        "--version",
        "--config",
        str(tmp_path / "no-such-config"),
        script=repo / "scripts" / "tenant-probes.sh",
        python=python,
        env={"PATH": f"{tools}:/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == f"{printed}\n"
    assert proc.stderr == ""
    assert not marker.exists()
    assert not git_ran.exists()


def test_help_word_matches_dash_h(tmp_path: Path) -> None:
    dash_h = _run(tmp_path, "-h")
    assert dash_h.returncode == 0
    assert dash_h.stdout.startswith("agentsync tenant probes:")
    assert dash_h.stderr == ""
    for spelling in ("--help", "help"):
        proc = _run(tmp_path, spelling)
        assert (proc.returncode, proc.stdout, proc.stderr) == (0, dash_h.stdout, "")


def test_help_wins_over_version(tmp_path: Path) -> None:
    help_text = _run(tmp_path, "--help").stdout
    for args in (["--version", "--help"], ["--help", "--version"], ["--version", "help"]):
        proc = _run(tmp_path, *args)
        assert (proc.returncode, proc.stdout, proc.stderr) == (0, help_text, "")
