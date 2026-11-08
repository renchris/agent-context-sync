"""scripts/tenant-probes.sh run for real: its exit table, the paths past sign-in, the dry run, the parser,
--version, the help text, the error lines and the cleaning of remote text.

Nothing here signs in, reaches Graph or touches the Keychain.  The script runs ``"$PY" -I -X utf8 - ...``, and
``-I`` ignores PYTHONPATH, so a path past sign-in passes as ``--python`` a wrapper that drops those four
arguments and runs the real interpreter on a shim file.  The shim replaces MsalAuth and GraphClient, then runs
the script's Python from stdin as ``__main__``.
"""

from __future__ import annotations

import ast
import os
import select
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentsync.text import safe_text

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
from agentsync.errors import AgentSyncError, AuthRequiredError
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
        if SCENARIO == "other":
            raise AgentSyncError("disk \\x1b[31mfull|now")
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


def _shim_python(tmp_path: Path, text: str = SHIM) -> Path:
    """The wrapper for ``--python``: it refuses any other call shape, so a change to the script's is loud."""
    shim = tmp_path / "shim.py"
    shim.write_text(text, encoding="utf-8")
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
        (["--frob"], "Unknown option --frob for tenant-probes.sh."),
        (["x"], "Unexpected positional argument x for tenant-probes.sh."),
        (["--output", "-"], "--output needs a folder, not -"),
        (["-o", "json"], "-o takes a folder, not a format: tenant-probes.sh has no JSON output yet"),
        (["--output=table"], "--output takes a folder, not a format"),
        (["-o", "--dry-run"], "-o needs a value, got the option --dry-run (write --output=--dry-run"),
        (["--max-pages", "abc"], "--max-pages needs a non-negative integer, got 'abc'"),
        (["--max-pages=abc"], "--max-pages needs a non-negative integer, got 'abc'"),
        (["--config"], "--config needs a value"),
        (["--out", "--dry-run"], "--out needs a value, got the option --dry-run (write --out=--dry-run"),
        (["--out", "--config=x"], "--out needs a value, got the option --config=x"),
        (["--config="], "--config needs a value"),
        (["--max-bytes="], "--max-bytes needs a value"),
        (["--out", ""], "--out needs a value"),
        (["--", "x"], "Unexpected positional argument x for tenant-probes.sh."),
        (["--", "--dry-run"], "Unexpected positional argument --dry-run for tenant-probes.sh."),
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


def test_two_interpreter_failures_have_two_messages(tmp_path: Path) -> None:
    """None found and one that cannot run are different repairs, so each names its own; both exit 1."""
    proc = _run(tmp_path, "--python", str(tmp_path / "no-such-python"))
    assert (proc.returncode, proc.stdout) == (1, "")
    assert proc.stderr == (
        f"Error: Can't run {tmp_path}/no-such-python: it isn't executable."
        " Check --python or AGENTSYNC_PYTHON.\n"
    )
    # none found: a checkout with no .venv, no uv on PATH or under HOME, and AGENTSYNC_PYTHON empty
    repo = _checkout(tmp_path, "none")
    proc = _run(tmp_path, script=repo / "scripts" / "tenant-probes.sh", env={"AGENTSYNC_PYTHON": ""})
    assert (proc.returncode, proc.stdout) == (1, "")
    assert proc.stderr == (
        "Error: Can't find a Python with agentsync installed."
        " Run scripts/install.sh, or pass --python <PATH>.\n"
    )


def test_invalid_sources_toml_exits_78(tmp_path: Path) -> None:
    config = tmp_path / "sources.toml"
    config.write_text("this is [not toml\n", encoding="utf-8")
    proc = _run(tmp_path, "--config", str(config), "--output", str(tmp_path / "x"))
    assert proc.returncode == 78
    assert proc.stdout == ""
    assert proc.stderr.startswith("Error: Configuration invalid: ")
    assert not (tmp_path / "x").exists()


def test_client_id_unset_exits_78_with_a_report(tmp_path: Path) -> None:
    proc = _run(tmp_path, "--config", str(_config(tmp_path, client_id=False)))
    assert proc.returncode == 78
    assert proc.stdout == ""
    report = _only_report(tmp_path / "state" / "probes")
    assert proc.stderr == f"Error: Can't run the probes. [graph] client_id is unset.\nReport: {report}\n"
    assert "**Not run:** `[graph] client_id` is unset" in report.read_text(encoding="utf-8")


def test_error_lines_are_plain_off_a_tty(tmp_path: Path) -> None:
    """``Error:`` in a log or a pipe and the ``✗`` mark only on a terminal, from bash and from Python."""
    missing = ("--python", str(tmp_path / "no-such-python"))
    bad = tmp_path / "bad.toml"
    bad.write_text("this is [not toml\n", encoding="utf-8")
    for args in (missing, ("--config", str(bad))):
        piped = _run(tmp_path, *args)
        assert piped.stderr.startswith("Error: ") and "✗" not in piped.stderr
        main, side = os.openpty()
        on_tty = b""
        try:
            proc = subprocess.run(
                ["/bin/bash", str(SCRIPT), *args],
                cwd=tmp_path,
                env={
                    "PATH": "/usr/bin:/bin",
                    "HOME": str(tmp_path / "home"),
                    "AGENTSYNC_PYTHON": sys.executable,
                },
                stdout=subprocess.PIPE,
                stderr=side,
                check=False,
                timeout=300,
            )
            # drain while the terminal is still open: macOS drops what is unread once its last side closes
            while select.select([main], [], [], 0.5)[0]:
                chunk = os.read(main, 4096)
                if not chunk:
                    break
                on_tty += chunk
        finally:
            os.close(side)
            os.close(main)
        assert proc.returncode == piped.returncode
        assert on_tty.decode().startswith("✗ "), on_tty
        assert on_tty.decode().replace("\r\n", "\n")[2:] == piped.stderr.removeprefix("Error: ")


def test_selftest_failures_go_to_stderr(tmp_path: Path) -> None:
    """A FAILED line is a diagnostic: stderr, exit 1, and stdout stays empty for a caller that reads it."""
    broken = (
        "import sys\n"
        'source = sys.stdin.read().replace("SgAAAAAAAAAAAAAAAQAAAAAAAAA=", "broken")\n'
        'exec(compile(source, "tenant-probes-embedded", "exec"), {"__name__": "__main__"})\n'
    )
    proc = _run(tmp_path, "--selftest", "--python", str(_shim_python(tmp_path, broken)))
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert proc.stderr.startswith("selftest FAILED: quickxor vector 1: ")


def test_selftest_exits_0(tmp_path: Path) -> None:
    proc = _run(tmp_path, "--selftest")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.startswith("selftest ok: ")
    assert proc.stderr == ""


# ---- past sign-in, through the shim ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scenario", "code", "stderr", "in_report"),
    [
        (
            "required",
            77,
            "not signed in; run `agentsync graph login`, then re-run; report ",
            "**reauth-required**",
        ),
        (
            "blocked",
            77,
            "sign-in blocked (blocked: consent): consent refused; report ",
            "**blocked: consent** (`AADSTS65001`)",
        ),
        (
            "mid-run",
            77,
            "stopped: sign-in required mid-run; run `agentsync graph login`, then re-run; report ",
            "**stopped: sign-in required mid-run**: the token was revoked",
        ),
        (
            "network",
            1,
            "failed: network-policy (TLS); see docs/deploy/README.md (TLS inspection / proxy); report ",
            "**failed: network-policy (TLS)**",
        ),
    ],
)
def test_past_sign_in_failures_write_the_report(
    tmp_path: Path, scenario: str, code: int, stderr: str, in_report: str
) -> None:
    """Each failure names its state and the report on stderr; stdout stays empty."""
    out = tmp_path / "out"
    proc = _run(
        tmp_path,
        "--config",
        str(_config(tmp_path)),
        "--output",
        str(out),
        "--python",
        str(_shim_python(tmp_path)),
        env={"PROBES_SCENARIO": scenario},
    )
    assert proc.returncode == code, proc.stdout + proc.stderr
    report = _only_report(out)
    assert in_report in report.read_text(encoding="utf-8")
    assert proc.stdout == ""
    assert proc.stderr == f"{stderr}{report}\n"


def test_a_failed_run_prints_nothing_on_stdout(tmp_path: Path) -> None:
    """Any other agentsync error: exit 1, one stderr line with the cleaned message and the report, no stdout;
    the report quotes the same message cleaned."""
    out = tmp_path / "out"
    proc = _run(
        tmp_path,
        "--config",
        str(_config(tmp_path)),
        "--output",
        str(out),
        "--python",
        str(_shim_python(tmp_path)),
        env={"PROBES_SCENARIO": "other"},
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert proc.stdout == ""
    report = _only_report(out)
    assert proc.stderr == f"failed: disk full/now; report {report}\n"
    text = report.read_text(encoding="utf-8")
    assert "**failed**: disk full/now" in text
    assert "\x1b" not in text


def test_canned_drive_exits_0_and_keeps_names_and_links_out_of_the_report(tmp_path: Path) -> None:
    out, record = tmp_path / "out", tmp_path / "download-parent"
    proc = _run(
        tmp_path,
        "--config",
        str(_config(tmp_path)),
        "--output",
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


def test_output_is_the_name_and_out_an_alias_that_warns(tmp_path: Path) -> None:
    """``-o``, ``--output`` and ``--out`` set the same folder; only ``--out`` adds one stderr line naming
    ``--output``. ``--output -`` is a usage error: the folder also holds deltalinks.json and login files."""
    config, out = str(_config(tmp_path)), tmp_path / "x"
    runs = {
        spelling: _run(tmp_path, "--config", config, *args, "--dry-run")
        for spelling, args in {
            "--output": ("--output", str(out)),
            "-o": ("-o", str(out)),
            "--output=": (f"--output={out}",),
            "--out": ("--out", str(out)),
            "--out=": (f"--out={out}",),
        }.items()
    }
    for spelling, proc in runs.items():
        assert proc.returncode == 0, (spelling, proc.stderr)
        assert proc.stdout == runs["--output"].stdout
        assert f"report: {out}/tenant-probes-" in proc.stdout
        alias = spelling in ("--out", "--out=")
        want = "warning: --out is deprecated; use --output <DIR>\n" if alias else ""
        assert proc.stderr == want, spelling
    assert not out.exists()
    proc = _run(tmp_path, "--config", config, "--output", "-", "--dry-run")
    assert (proc.returncode, proc.stdout) == (2, "")
    assert proc.stderr.startswith("usage error: --output needs a folder, not -")


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
    assert dash_h.stdout.startswith("Usage: scripts/tenant-probes.sh [options]\n")
    assert dash_h.stderr == ""
    for spelling in ("--help", "help"):
        proc = _run(tmp_path, spelling)
        assert (proc.returncode, proc.stdout, proc.stderr) == (0, dash_h.stdout, "")


def test_help_wins_over_version(tmp_path: Path) -> None:
    help_text = _run(tmp_path, "--help").stdout
    for args in (["--version", "--help"], ["--help", "--version"], ["--version", "help"]):
        proc = _run(tmp_path, *args)
        assert (proc.returncode, proc.stdout, proc.stderr) == (0, help_text, "")


def _help_section(help_text: str, title: str) -> list[str]:
    """The indented lines under ``title:`` up to the next blank line."""
    lines = help_text.split(f"\n{title}:\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert lines and all(line.startswith("  ") for line in lines), lines
    return lines


def test_help_lists_exactly_the_parsed_options(tmp_path: Path) -> None:
    """Every option the parse loop has is in Options, and nothing else: ``--out`` (the alias that warns) and
    the word ``help`` parse and stay out, and each ``--name=*`` arm has its separated arm."""
    from test_contracts import _install_case_arms  # noqa: PLC0415

    arms = _install_case_arms(SCRIPT.read_text(encoding="utf-8"), "while [ $# -gt 0 ]; do", "done")
    equals = {a.removesuffix("=*") for a in arms if a.endswith("=*")}
    assert equals and equals <= set(arms)
    parsed = {a for a in arms if not a.endswith("=*")} - {"*", "--out", "help"}
    listed: list[str] = []
    for line in _help_section(_run(tmp_path, "--help").stdout, "Options"):
        left = line[2:].split("  ", 1)[0]
        if left.startswith("-"):
            listed += [part.split(" ")[0] for part in left.split(", ")]
    assert sorted(listed) == sorted(parsed)
    longs = [n for n in listed if n.startswith("--") and n != "--"]
    assert longs == sorted(longs)  # sorted by long name


def test_help_fits_80_columns(tmp_path: Path) -> None:
    text = _run(tmp_path, "--help").stdout
    assert text.endswith("Learn more: docs/deploy/tenant-probes.md\n")
    assert [line for line in text.splitlines() if len(line) > 80] == []


def test_every_probe_help_example_parses(tmp_path: Path) -> None:
    """R100: each ``Examples:`` command, with ``--dry-run`` added where absent, exits 0 against a Contoso
    config and changes nothing under tmp_path. A dry run never signs in."""
    config = _config(tmp_path)
    examples = _help_section(_run(tmp_path, "--help").stdout, "Examples")
    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "tmpdir").mkdir(exist_ok=True)
    before = _tree(tmp_path)
    for example in examples:
        argv = shlex.split(example)
        assert argv[0] == "scripts/tenant-probes.sh", example
        args = argv[1:] + ([] if "--dry-run" in argv else ["--dry-run"])
        proc = _run(tmp_path, *args, env={"AGENTSYNC_CONFIG": str(config)})
        assert proc.returncode == 0, (example, proc.stderr)
        assert proc.stdout.startswith("dry run: nothing is signed in, requested or written")
        assert _tree(tmp_path) == before, example


def _script_helpers() -> dict[str, object]:
    """``clean`` and ``cell`` as the script defines them, taken from its heredoc with what they read."""
    text = SCRIPT.read_text(encoding="utf-8")
    body = text.split("<<'PYTHON' || rc=$?\n", 1)[1].split("\nPYTHON\n", 1)[0]
    wanted = {"re", "unicodedata", "ESCAPE_RE", "DROPPED", "clean", "cell"}
    keep = [
        node
        for node in ast.parse(body).body
        if (isinstance(node, ast.Import) and node.names[0].name in wanted)
        or (isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) in wanted)
        or (isinstance(node, ast.FunctionDef) and node.name in wanted)
    ]
    assert len(keep) == len(wanted), [ast.dump(n)[:40] for n in keep]
    namespace: dict[str, object] = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "tenant-probes-helpers", "exec"), namespace)
    return namespace


# Where the two agree. They differ, on purpose, on a carriage return (the script shows it as \r, safe_text
# drops it), on private-use and unassigned code points (the script drops them by category) and on the blank
# fillers safe_text lists by code point.
SAMPLES = (
    "plain words",
    "café 日本 🎉",
    "\x1b[31mred\x1b[0m and \x9b1mcsi8",
    "\x1b]8;;https://contoso.test\x07link\x1b]8;;\x1b\\ end",
    "bidi \u202eflip\u202c and \u2066iso\u2069",
    "zero\u200bwidth\ufeffjoin\u200d",
    "line\nbreak and tab\there",
    "nul\x00 bel\x07 del\x7f nel\x85 c1\x9f",
    "line\u2028sep\u2029para",
    "a|b|c",
    "x" * 300,
)


@pytest.mark.parametrize("sample", SAMPLES)
def test_cell_matches_safe_text_on_samples(sample: str) -> None:
    """The script keeps its own cleaner (its interpreter may hold an older agentsync), held equal here to
    agentsync.text.safe_text with ``cell=True``."""
    helpers = _script_helpers()
    clean, cell = helpers["clean"], helpers["cell"]
    assert callable(clean) and callable(cell)
    assert clean(sample) == safe_text(sample, cell=True)
    assert cell(sample, None) == safe_text(sample, cell=True).replace("|", "/")
    cut = cell(sample)
    assert len(cut) <= 160
    assert cut.endswith("…") == (len(safe_text(sample, cell=True)) > 160)
