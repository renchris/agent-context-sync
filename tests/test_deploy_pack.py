"""The IT deploy pack (docs/deploy, scripts): the Entra manifest, the PPPC profile, the two shell scripts and
the links between the pack's pages.  C15 (docs/design/receipts/verify/C15-corporate-controls.md) section 9
item 7 asks for the manifest check; the rest keeps the pack an IT admin receives from rotting silently."""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import itertools
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

import exec_stub
from agentsync.convert import speech

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "docs" / "deploy"
ENTRA = DEPLOY / "entra-app.json"
PPPC = DEPLOY / "mdm" / "agentsync-pppc.mobileconfig"
SCRIPTS = (ROOT / "scripts" / "install.sh", ROOT / "scripts" / "tenant-probes.sh")
SHELL_SCRIPTS = sorted(
    path
    for folder in ("scripts", "probes", "launcher", "docs/design/receipts/review/scripts")
    for path in (ROOT / folder).glob("*.sh")
)
BASH_SCRIPTS = [p for p in SHELL_SCRIPTS if p.read_text(encoding="utf-8").startswith("#!/bin/bash\n")]
BASH = "/bin/bash" if sys.platform == "darwin" else "bash"
"""The bash these scripts run under: on a Mac, /bin/bash is 3.2, whose parser is not bash 5's."""
SPEECH_PACKAGE = ROOT / "src" / "agentsync" / "convert" / "speech_helper" / "Package.swift"


def nemo_release() -> tuple[str, str]:
    """The archive and release repository of FluidAudio's one remote binary target, read from
    ``speech.NEMO_URL``, the one url the speech build fetches (B13 round 4 Q60: a second hand-copied table
    could go stale beside it). The url is checked against FluidAudio's own Package.swift at each pin by
    test_speech's FLUIDAUDIO_NEMO, a KeyError at a new pin until someone re-reads it."""
    m = re.fullmatch(
        r"https://github\.com/FluidInference/([^/]+)/releases/download/v[^/]+/([^/]+)", speech.NEMO_URL
    )
    assert m, speech.NEMO_URL
    return m.group(2), m.group(1)


REDIRECT_URIS = ["http://localhost", "msauth.com.msauth.unsignedapp://auth"]
"""C15 section 1.6: the loopback rung (MSAL uses http://localhost:<port>) and the macOS broker rung (MSAL
hard-codes the unsigned-app URI for any Python app)."""

ONE_PROMPT_INSTALL_FLAGS = {
    "--version",
    "--log-start",
    "--list-folders",
    "--log",
    "--source-local",
    "--report-only",
}
"""The install.sh flags the README's one prompt names; test_contracts.py checks they are in the frozen set."""

on_macos = pytest.mark.skipif(sys.platform != "darwin", reason="plutil is macOS-only")

_SHELLS = [s for s in ("bash", "zsh") if shutil.which(s)]
"""The shells a user's coding agent most likely runs the README's commands in."""


def entra() -> dict[str, Any]:
    data = json.loads(ENTRA.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_entra_manifest_matches_c15() -> None:
    app = entra()
    assert app["signInAudience"] == "AzureADMyOrg"  # single tenant; multi-tenant authorities hit AADSTS50194
    assert app["isFallbackPublicClient"] is False  # "Allow public client flows" is for device code only
    assert app["publicClient"]["redirectUris"] == REDIRECT_URIS  # exactly these two, in this order
    for key in ("web", "spa", "passwordCredentials", "keyCredentials"):
        assert not app.get(key), f"{key}: a public client has no web/SPA platform and no secrets"
    [graph] = next(v for k, v in app.items() if k.lower() == "requiredresourceaccess")
    assert graph["resourceAppId"] == "00000003-0000-0000-c000-000000000000"  # Microsoft Graph
    ids = [p["id"] for p in graph["resourceAccess"]]
    assert ids and len(ids) == len(set(ids))
    assert {p["type"] for p in graph["resourceAccess"]} == {"Scope"}  # delegated only, no application roles


def test_it_request_states_the_manifest_settings() -> None:
    text = (DEPLOY / "it-request.md").read_text(encoding="utf-8")
    assert "`AzureADMyOrg`" in text
    assert all(f"`{uri}`" in text for uri in REDIRECT_URIS)
    assert re.search(r"`isFallbackPublicClient`\) \| \*\*No\*\*", text)
    blocks = re.findall(r"```json\n(.*?)```", text, flags=re.DOTALL)
    assert blocks, "it-request.md carries the manifest inline"
    assert blocks[0] == ENTRA.read_text(encoding="utf-8")  # the page says "byte-identical to entra-app.json"


def test_it_request_names_the_speech_build_downloads() -> None:
    """H9 (B12 C1): IT is told every host and binary the speech build fetches, so an allowlist that has only
    github.com is not a surprise and the third-party binary is not hidden."""
    text = (DEPLOY / "it-request.md").read_text(encoding="utf-8")
    section = " ".join(text.split("\n## What leaves the Mac", 1)[1].split("\n## ", 1)[0].split())
    package = SPEECH_PACKAGE.read_text(encoding="utf-8")
    m = re.search(r'url: "https://github\.com/([^"/]+/[^"/]+?)\.git",\s*revision: "([0-9a-f]{40})"', package)
    assert m, "speech_helper/Package.swift pins FluidAudio by URL and revision"
    repo, revision = m.groups()
    assert f"`{repo}`" in section and f"`{revision[:7]}`" in section, "the git fetch and its pin"
    assert revision == speech.FLUIDAUDIO_PIN, "a new pin: re-read FluidAudio's binary targets (test_speech)"
    archive, release_repo = nemo_release()
    assert f"`{archive}`" in section and release_repo in section, "the prebuilt binary FluidAudio links"
    assert "`release-assets.githubusercontent.com`" in section, "the host GitHub serves release files from"
    assert "`[convert] recordings = false`" in section, "how IT turns the build off"


def _download_hosts() -> Any:
    spec = importlib.util.spec_from_file_location("download_hosts", ROOT / "scripts" / "download_hosts.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_weekly_check_follows_the_download_hosts_the_it_request_names() -> None:
    """B13 round 2 R119: the test above reads only repository text, so if GitHub moved its release files
    again (it moved them from objects.githubusercontent.com to release-assets.githubusercontent.com), the
    page and the test would agree and stay green. scripts/download_hosts.py follows the real redirect chains
    in a weekly workflow outside the gate. Here, offline: it reads the same section, builds the release URL
    the speech build fetches, and flags a chain that ends off the named host or passes an unnamed one."""
    hosts = _download_hosts()
    text = (DEPLOY / "it-request.md").read_text(encoding="utf-8")
    bullet = hosts.downloads_bullet(text)
    assert hosts.named_hosts(bullet) == {
        "astral.sh",
        "releases.astral.sh",
        "github.com",
        "pypi.org",
        "files.pythonhosted.org",
        "release-assets.githubusercontent.com",
    }
    url, end = hosts.release_file(bullet)
    assert url == speech.NEMO_URL, "the page names the release the speech build fetches (B13 round 4 Q60)"
    assert end == "release-assets.githubusercontent.com"
    script = SCRIPTS[0].read_text(encoding="utf-8")
    assert hosts.uv_installer_url(script) == "https://astral.sh/uv/install.sh"
    installer = (
        'if [ -n "${UV_DOWNLOAD_URL:-}" ]; then\n    ARTIFACT_DOWNLOAD_URLS="$UV_DOWNLOAD_URL"\nelse\n'
        '    ARTIFACT_DOWNLOAD_URLS="https://releases.astral.sh/github/uv/releases/download/0.13.0 '
        'https://github.com/astral-sh/uv/releases/download/0.13.0"\nfi\n'
    )
    assert hosts.uv_binary_urls(installer) == [
        f"https://releases.astral.sh/github/uv/releases/download/0.13.0/{hosts.UV_ASSET}",
        f"https://github.com/astral-sh/uv/releases/download/0.13.0/{hosts.UV_ASSET}",
    ], "the installer's own default, not an override variable"
    named = hosts.named_hosts(bullet)
    chains = {
        url: [url, "https://objects.githubusercontent.com/x"],
        "https://astral.sh/uv/install.sh": [
            "https://astral.sh/uv/install.sh",
            "https://cdn.example.net/i.sh",
        ],
        "https://pypi.org/simple/pip/": ["https://pypi.org/simple/pip/"],
    }
    steps = [("release", url, end), ("uv installer", "https://astral.sh/uv/install.sh", None)]
    steps.append(("PyPI", "https://pypi.org/simple/pip/", None))
    steps.insert(0, ("Python 3.11", "https://releases.astral.sh/py.tar.gz", None))

    def follow(link: str) -> list[str]:
        if link.endswith("py.tar.gz"):
            raise RuntimeError("curl exit 6: Could not resolve host: releases.astral.sh")
        return chains[link]

    problems = hosts.check(steps, named, follow=follow)
    assert len(problems) == 4, problems
    assert problems[0].startswith("Python 3.11 could not be followed (curl exit 6: Could not resolve host")
    assert problems[1].startswith("release now ends on objects.githubusercontent.com")
    assert "objects.githubusercontent.com, which it-request.md does not name" in problems[2]
    assert "uv installer passes cdn.example.net" in problems[3], "a failed chain leaves the others checked"
    workflow = (ROOT / ".github" / "workflows" / "download-hosts.yml").read_text(encoding="utf-8")
    trigger = workflow.split("\non:\n", 1)[1].split("\n\n", 1)[0]
    assert "schedule:" in trigger and "workflow_dispatch:" in trigger and "push" not in trigger
    assert "run: python3 -I scripts/download_hosts.py" in workflow
    guide = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    gate = guide.split("### Gate", 1)[1].split("```", 2)[1]
    assert "download_hosts" not in gate, "it reads the network: not in the gate"
    assert "`.github/workflows/download-hosts.yml` is not in this list" in guide


def test_claude_md_gives_cis_time_and_its_limit() -> None:
    """B13 round 17 U112: CLAUDE.md said `ci` takes about 6 minutes, the October 6 figure; its test job took
    15 to 20 minutes in round 16's runs, so a reader timing the read-back loop by it would think CI stuck. It
    gives the measured range and the job's limit, the `timeout-minutes` of ci.yml's one job, read here. B13
    round 19 W80: the job then took 23 to 24 of its 30 minutes, and the same tests and source 23:32 on one run
    and 20:23 on the next; the range is the measured one, and the limit keeps at least 10 minutes above its
    top. B13 round 20 X63: 23:32 and 20:23 were pytest's step, not the job, which took 24:00 and 20:59, and
    round 19's jobs took 20:59 to 24:46, so "19 to 24" named no job and one ran past its top: the range is
    the jobs' own, 21 to 25. B13 round 21 Y68: round 20's jobs took 21:17 to 26:13 but one, 33:53 on a
    loaded runner, 6 minutes under the limit of 40: the range is 21 to 26 with that run beside it, and the
    limit, 45, keeps 10 minutes above the slowest job seen. B13 round 22 Z67, Z88 (one defect): the eight jobs
    after round 20's (eaed1034 to fcafd734) took 18:05 to 28:34, six of them past 26 and one under 21, so
    a reader timing the read-back by "21 to 26" would think a healthy 28-minute job stuck: the range is 18
    to 29, and ci.yml's note adds those times after round 20's, which it keeps. B13 round 23 A74, A93 (one
    defect): round 22's nine jobs (fcafd734 to 7a54f938) took 19:29 to 31:21, one past 29: the range is 18 to
    32, with those nine beside round 21's, and the limit keeps 13 minutes above it."""
    guide = " ".join((ROOT / "CLAUDE.md").read_text(encoding="utf-8").split())
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    [limit] = re.findall(r"^jobs:\n  test:\n(?:    .*\n)*?    timeout-minutes: (\d+)\n", ci, re.M)
    assert "about 6 minutes" not in guide and "about 15 to 20 minutes" not in guide
    assert "about 19 to 24 minutes" not in guide and "23.5" not in guide
    assert "about 21 to 25 minutes" not in guide and "about 21 to 26 minutes" not in guide
    assert "about 18 to 29 minutes" not in guide
    assert (
        "`ci` takes about 18 to 32 minutes once its job starts (measured October 2026; 24 at 5120 tests, and"
        " the same tests and source ran 24 and then 21 minutes on two runs; the eight jobs from eaed1034 to"
        " fcafd734 took 18:05 to 28:34; the nine from fcafd734 to 7a54f938 took 19:29 to 31:21; one run took"
        " 34 on a runner loaded 5 to 8 per core), plus any wait for a macOS runner (one waited 21 minutes,"
        f" another 17); the job's limit is {limit} minutes (`timeout-minutes` in ci.yml)."
    ) in guide
    assert int(limit) - 32 >= 10 and int(limit) - 34 >= 10
    lanes = _page("docs/lanes/COMMON.md")
    assert "CI takes 18 to 32 minutes a run (one ran 34 on a loaded runner); its limit is 45" in lanes
    assert (
        "21 to 26 minutes" not in lanes
        and "18 to 29 minutes" not in lanes
        and f"its limit is {limit} " in lanes
    )
    # the workflow's own note gives the job's times, and pytest's step only as such
    note = " ".join(line.lstrip("# ") for line in ci.splitlines() if line.startswith("#"))
    assert "took 24:00 on one run and 20:59 on the next (pytest's step 23:32 and 20:23" in note
    assert "took 21:17 to 26:13 but one, 33:53 (64380e08, pytest's step 33:14," in note
    assert f"6 minutes under the limit of 40, so it is {limit}: 10 minutes above that job too" in note
    assert "(eaed1034 to fcafd734) took 18:05 to 28:34, all green on attempt 1" in note
    assert f"so {limit} stays {int(limit) - 29} minutes above them (B13 round 22 Z67, Z88)" in note
    assert "Round 22's nine jobs (fcafd734 to 7a54f938) took 19:29 to 31:21, all on attempt 1" in note
    assert f"so {limit} stays {int(limit) - 32} minutes above them (B13 round 23 A74, A93)" in note


def test_pppc_profile_parses_and_keeps_its_placeholders() -> None:
    profile = plistlib.loads(PPPC.read_bytes())
    tcc, managed = profile["PayloadContent"]
    # the key paths docs/deploy/mdm/README.md feeds to `plutil -replace`
    [grant] = tcc["Services"]["SystemPolicyAllFiles"]
    assert grant["Authorization"] == "Allow" and grant["Identifier"] == "com.agentsync.launcher"
    requirement = grant["CodeRequirement"]
    assert 'identifier "com.agentsync.launcher"' in requirement
    assert 'certificate leaf[subject.OU] = "__TEAMID__"' in requirement  # IT pins its Developer ID Team ID
    assert "cdhash" not in requirement  # an ad-hoc requirement cannot be pinned
    assert managed["Rules"][1]["TeamIdentifier"] == "__TEAMID__"
    assert profile["PayloadOrganization"] == "__ORG__" and "__ORG__" in profile["PayloadDisplayName"]


@on_macos
def test_pppc_profile_passes_plutil_lint() -> None:
    proc = subprocess.run(["plutil", "-lint", str(PPPC)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("script", BASH_SCRIPTS, ids=lambda p: p.name)
def test_scripts_parse_with_bash_n(script: Path) -> None:
    """Every ``#!/bin/bash`` script parses under the bash it runs under, /bin/bash 3.2 on a Mac: an apostrophe
    in a comment inside a multi-line ``"$( )"`` fails 3.2's ``-n`` and passes bash 5's (B13 round 4 Q84).  It
    does not catch the same apostrophe inside ``<( )`` or ``< <({ ... })``, which 3.2 parses and then fails at
    run time; test_no_apostrophe_in_a_comment_inside_a_substitution is for that."""
    proc = subprocess.run([BASH, "-n", str(script)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


def substitution_comment_apostrophes(text: str) -> list[int]:
    """The line numbers of comment lines holding a straight apostrophe inside a multi-line ``$(``, ``"$(``
    or ``<(`` block: one opened by a line ending in it, closed by a line whose first non-blank character is
    ``)``.  A line ending in another ``(`` (an array, a subshell) opens a block that is not a substitution.
    A line ending in ``$({`` or ``<({`` opens a braced substitution, closed by a line starting ``}``: its
    ``)`` may come only on a pipe's continuation line (B13 round 17 U108; a comment between that ``}`` line
    and the ``)`` is not read)."""
    found: list[int] = []
    open_: list[tuple[bool, str]] = []  # (a substitution, the closer it waits for)
    for number, line in enumerate(text.splitlines(), 1):
        body = line.strip()
        if body.startswith("#"):
            if any(sub for sub, _ in open_) and "'" in body:
                found.append(number)
            continue
        if open_ and body.startswith(open_[-1][1]):
            open_.pop()
        if re.search(r"[$<]\($", body):
            open_.append((True, ")"))
        elif re.search(r"[$<]\(\{$", body):
            open_.append((True, "}"))
        elif body.endswith("("):
            open_.append((False, ")"))
    return found


def test_the_substitution_lint_finds_the_round_3_shape() -> None:
    text = (
        'f() {\n\twhile read -r k; do\n\t\techo "$k"\n\tdone < <(\n\t\t# Contoso\'s odd one\n'
        '\t\tprintf "a\\n"\n\t)\n}\nx="$(\n\t# a quoted one\n\tdate\n)"\narr=(\n\t# Contoso\'s array\n)\n'
    )
    assert substitution_comment_apostrophes(text) == [5]
    # B13 round 17 U108: a braced "< <({" block, whose "}" line goes on in a pipe, was never opened, and a
    # "}...)" close rule would have held it open to the end of the file
    braced = (
        'g() {\n\twhile read -r k; do\n\t\techo "$k"\n\tdone < <({\n\t\t# Contoso\'s braced one\n'
        '\t\tprintf "a\\n"\n\t} 2>/dev/null | LC_ALL=C sed s/a/b/ |\n\t\tLC_ALL=C sort -u)\n}\n'
        "# Contoso's comment after the block\ny=\"$({\n\t# Fabrikam's one\n\tdate\n})\"\n"
    )
    assert substitution_comment_apostrophes(braced) == [5, 12]


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_no_apostrophe_in_a_comment_inside_a_substitution(script: Path) -> None:
    """/bin/bash 3.2 reads a straight apostrophe in a comment inside a multi-line ``$( )`` or ``<( )`` as an
    open quote.  Inside ``<( )`` neither its ``-n`` nor bash 5's nor shellcheck notices, and at run time the
    substitution fails ("bad substitution"): in round 3 that silently dropped every proxy pair of install.sh,
    when a comment there went from two apostrophes to three (B13 round 4 Q84).  So none is allowed, even an
    even number."""
    lines = substitution_comment_apostrophes(script.read_text(encoding="utf-8"))
    assert lines == [], (
        f"{script.name} lines {lines}: a comment inside a multi-line substitution holds an apostrophe; "
        "move the comment above the substitution or drop the apostrophe"
    )


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck is not installed")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_pass_shellcheck(script: Path) -> None:
    proc = subprocess.run(["shellcheck", str(script)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def tmp_home_env(home: Path) -> dict[str, str]:
    """A minimal environment whose HOME is ``home``: nothing reads or writes the real ~/agent-context."""
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "TMPDIR", "xcrun_db")}
    env["HOME"] = str(home)
    env["AGENTSYNC_OCR"] = "0"  # a real agentsync started under it looks for no OCR helper
    return env


def xcrun_db() -> dict[str, str]:
    """conftest's xcrun cache, if set, for an env built without os.environ (load root cause 2026-10-17 X3)."""
    v = os.environ.get("xcrun_db")  # noqa: SIM112 # xcrun reads this lowercase name
    return {"xcrun_db": v} if v else {}


def test_install_help_exits_zero(tmp_path: Path) -> None:
    proc = subprocess.run(
        ["bash", str(SCRIPTS[0]), "--help"],
        cwd=tmp_path,
        env=tmp_home_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("agentsync installer") and "AGENTSYNC_INSTALL_DRY_RUN=1" in proc.stdout
    assert sorted(p.name for p in tmp_path.iterdir()) == []  # --help changes nothing


def test_tenant_probes_dry_run_exits_zero(tmp_path: Path) -> None:
    ctx = tmp_path / "agent-context"
    ctx.mkdir()
    (tmp_path / "src").mkdir()
    (ctx / "sources.toml").write_text(
        f"""[agentsync]
docs_repo = "{ctx / "docs"}"
state_dir = "{tmp_path / "state"}"
cache_dir = "{tmp_path / "cache"}"
principal = "owner@contoso.com"

[graph]
client_id = "00000000-0000-0000-0000-000000000000"
tenant = "contoso.onmicrosoft.com"

[[source]]
id = "src"
kind = "local"
path = "{tmp_path / "src"}"
""",
        encoding="utf-8",
    )
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    env = tmp_home_env(tmp_path) | {"AGENTSYNC_PYTHON": sys.executable}
    proc = subprocess.run(
        ["bash", str(SCRIPTS[1]), "--dry-run"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.startswith("dry run: nothing is signed in, requested or written")
    assert "authority: https://login.microsoftonline.com/contoso.onmicrosoft.com" in proc.stdout
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before  # no report, no state, no token cache


def test_tenant_probes_config_attributes_exist(tmp_path: Path) -> None:
    """The probe's embedded Python runs past --dry-run only against a real tenant, so no test executes it;
    every ``config.<a>.<b>`` it reads must still exist (KISS K15 removed ``GraphConfig.company``)."""
    from agentsync.config import parse_config  # noqa: PLC0415

    text = SCRIPTS[1].read_text(encoding="utf-8")
    chains = set(re.findall(r"\bconfig((?:\.[a-z_]+)+)", text))
    assert chains
    config = parse_config(
        '[graph]\nclient_id = "00000000-0000-0000-0000-000000000000"\ntenant = "contoso.onmicrosoft.com"\n',
        config_path=tmp_path / "sources.toml",
    )
    for chain in sorted(chains):
        value: object = config
        for name in chain.lstrip(".").split("."):
            assert hasattr(value, name), f"tenant-probes.sh reads config{chain}, which no longer exists"
            value = getattr(value, name)


_FENCE_RE = re.compile(r"^(```|~~~).*?^\1", flags=re.DOTALL | re.MULTILINE)
_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")


def _prose(path: Path) -> str:
    """The page without fenced code blocks and inline code spans (neither holds real links)."""
    text = _FENCE_RE.sub("", path.read_text(encoding="utf-8"))
    return re.sub(r"`[^`\n]*`", "", text)


def _anchors(path: Path) -> set[str]:
    """GitHub heading anchors: lowercase, punctuation other than '-' and '_' dropped, spaces to '-'."""
    heads = re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", _prose(path), flags=re.MULTILINE)
    return {re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-") for h in heads}


DEPLOY_PAGES = sorted(DEPLOY.rglob("*.md"))


@pytest.mark.parametrize("page", DEPLOY_PAGES, ids=lambda p: str(p.relative_to(DEPLOY)))
def test_deploy_page_relative_links_resolve(page: Path) -> None:
    broken: list[str] = []
    for target in _LINK_RE.findall(_prose(page)):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, flags=re.IGNORECASE):
            continue  # https:, mailto: …
        rel, _, anchor = target.partition("#")
        dest = (page.parent / rel).resolve() if rel else page
        if not dest.exists():
            broken.append(f"{target}: no such file")
        elif anchor and dest.suffix == ".md" and anchor not in _anchors(dest):
            broken.append(f"{target}: no heading #{anchor}")
    assert broken == [], f"{page.relative_to(ROOT)}: {broken}"


def test_deploy_pages_were_found() -> None:
    names = {p.name for p in DEPLOY_PAGES}
    assert {"README.md", "it-request.md", "data-governance.md", "tenant-probes.md"} <= names


# ---- install.sh --source-local and the README one-prompt block ------------------------------------------


DRY_RUN_NEXT = "NEXT: re-run without AGENTSYNC_INSTALL_DRY_RUN=1 to apply the steps above"
"""The dry run's one NEXT line (KISS K17: the variable replaced --dry-run)."""


def _install_dry_run(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """install.sh's dry run (AGENTSYNC_INSTALL_DRY_RUN=1) with HOME = ``home`` and no uv on PATH (so no real
    uv is ever invoked)."""
    env = tmp_home_env(home) | {"PATH": "/usr/bin:/bin", "AGENTSYNC_INSTALL_DRY_RUN": "1"}
    return subprocess.run(
        ["bash", str(SCRIPTS[0]), *args],
        cwd=home,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _tree(root: Path) -> list[str]:
    return sorted(str(p) for p in root.rglob("*"))


def test_install_dry_run_passes_source_local_to_add_source(tmp_path: Path) -> None:
    """KISS K14: add-source creates a missing config, so a fresh install runs it once per folder, no init."""
    one = tmp_path / "OneDrive-Contoso" / "FY26 Projects"
    two = tmp_path / "notes"
    one.mkdir(parents=True)
    two.mkdir()
    before = _tree(tmp_path)
    proc = _install_dry_run(tmp_path, "--source-local", str(one), "--source-local", "~/notes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout
    assert f"source-local: {one}\n" in out and f"source-local: {two}\n" in out  # ~/ is expanded
    runs = [ln for ln in out.splitlines() if ln.startswith("[dry-run]")]
    config = tmp_path / "agent-context" / "sources.toml"
    added = [ln for ln in runs if " add-source " in ln]
    assert len(added) == 2, runs
    assert added[0].endswith(f" add-source {str(one).replace(' ', chr(92) + ' ')} --config {config}")
    assert added[1].endswith(f" add-source {two} --config {config}")
    assert not any(" init " in ln for ln in runs)
    assert out.rstrip().splitlines()[-1] == DRY_RUN_NEXT
    assert _tree(tmp_path) == before  # a dry run changes nothing


def test_install_dry_run_adds_sources_to_an_existing_config(tmp_path: Path) -> None:
    folder = tmp_path / "Projects"
    folder.mkdir()
    config = tmp_path / "agent-context" / "sources.toml"
    config.parent.mkdir()
    config.write_text("# existing\n", encoding="utf-8")
    before = _tree(tmp_path)
    proc = _install_dry_run(tmp_path, "--source-local", str(folder))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    runs = [ln for ln in proc.stdout.splitlines() if ln.startswith("[dry-run]")]
    assert any(ln.endswith(f"agentsync add-source {folder} --config {config}") for ln in runs), runs
    assert not any(" init " in ln for ln in runs)
    assert config.read_text(encoding="utf-8") == "# existing\n" and _tree(tmp_path) == before


def test_install_source_local_usage_errors_exit_2_before_any_step(tmp_path: Path) -> None:
    for args in (("--source-local", str(tmp_path / "missing")), ("--source-local",)):
        proc = _install_dry_run(tmp_path, *args)
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "usage error: --source-local" in proc.stderr and "uv:" not in proc.stdout


def _one_prompt_block() -> str:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("\n## Set up on a new Mac: one prompt\n", 1)[1].split("\n## ", 1)[0]
    match = re.search(r"^```text\n(.*?)^```$", section, flags=re.DOTALL | re.MULTILINE)
    assert match, "README.md: the one-prompt section has no ```text block"
    return match.group(1)


def _prompt_steps() -> dict[int, str]:
    """The block's numbered steps, number -> text (continuation lines joined with single spaces)."""
    parts = re.split(r"^(\d+)\. ", _one_prompt_block(), flags=re.MULTILINE)
    steps = {int(n): " ".join(body.split()) for n, body in zip(parts[1::2], parts[2::2], strict=True)}
    assert list(steps) == list(range(1, len(steps) + 1)), f"steps are numbered 1..N: {list(steps)}"
    return steps


def _preamble() -> str:
    return " ".join(re.split(r"^1\. ", _one_prompt_block(), maxsplit=1, flags=re.MULTILINE)[0].split())


def _prompt_version() -> int:
    [version] = re.findall(r"\(setup prompt v(\d+)\)", _one_prompt_block())
    return int(version)


def _spans(text: str) -> list[str]:
    return re.findall(r"`([^`]+)`", text)


_COMMAND_RE = re.compile(r"^(?:~/|git |export |mkdir |umask |sw_vers|xcode-select |test |printf |date )")
"""A code span that is a command the agent runs (not a path, a value or a line format)."""


def _commands(text: str) -> list[str]:
    return [s for s in _spans(text) if _COMMAND_RE.match(s)]


def _installer_help() -> str:
    """install.sh's --help text, read from its header comment (what show_help prints) without running it."""
    lines = SCRIPTS[0].read_text(encoding="utf-8").splitlines()[1:]
    return "\n".join(ln for ln in itertools.takewhile(lambda ln: ln.startswith("#"), lines))


def _installer_compat() -> int | None:
    """The prompt-compatibility number ``install.sh --version`` prints ("setup-prompt-compat <N>"), read from
    the script's text: a ``*COMPAT*=<N>`` assignment or a literal "setup-prompt-compat <N>". None when neither
    exists."""
    script = SCRIPTS[0].read_text(encoding="utf-8")
    if "setup-prompt-compat" not in script:
        return None
    assigned = re.findall(
        r"""^\s*(?:readonly\s+|declare\s+-r\s+)?\w*COMPAT\w*=["']?(\d+)["']?\s*(?:#.*)?$""",
        script,
        flags=re.MULTILINE,
    )
    literal = re.findall(r"setup-prompt-compat (\d+)\b", script)
    values = {int(v) for v in assigned + literal}
    if not values:
        return None
    assert len(values) == 1, f"scripts/install.sh states more than one setup-prompt-compat number: {values}"
    return values.pop()


STEP_TITLES = [
    "preflight, code and folders",
    "install and start",
    "sync loop and report",
]
"""The three steps of setup prompt v7 and later, by their opening words (setup_report.PROMPT_LAYOUTS[7] maps
them onto the form's Failed-at options, which keep v6's four names)."""

INSTALL_SH = "~/src/agent-context-sync/scripts/install.sh"
"""The installer as the prompt names it. Alone it is step 2's command on a Mac with no folder to add."""

INSTALL_WITH_FOLDER = f'{INSTALL_SH} --source-local "<folder>"'
"""Step 2's command with a folder to sync (a new Mac) or to add."""

PROMPT = (32, "26aed725e49bf1907dba82c48f98819056221c226c1ad8c1ea930dce0ebb8685")
"""The setup prompt's version and the SHA-256 of its block, as README.md has them. The version moves with
every change of the text, a reworded sentence included, so that a pasted copy is always known by its version
(scripts/install.sh, "Setup prompt"): change the text, then bump "setup prompt vN" and the two "prompt vN" /
"setup-prompt-compat N" numbers of step 1 and the "prompt vN, <agent>: " its stops log, SETUP_PROMPT_COMPAT
in scripts/install.sh, PROMPT_VERSION in setup_report.py and the form's placeholder, and set both values here
(``test_the_prompt_text_changes_only_with_its_version`` prints the new digest). The number is also written out
in tests/test_install_oneshot.py, tests/test_launcher.py and tests/test_setup_report.py, and in
docs/deploy/setup-feedback.md."""

START_AGENT = f"prompt v{PROMPT[0]}, <agent>"
"""What step 1 hands to ``install.sh --log-start``: the prompt's own version, then the agent's tool and
model id. The installer stops a copy whose version is not its own."""

CEILING = 'export GIT_CEILING_DIRECTORIES="$HOME/src"'
"""What step 1's command and each of its git recoveries start with since v19: git never climbs above ~/src
looking for a repository, so a checkout whose ``.git`` git does not accept gives "not a git repository"
instead
of reaching a home that is itself a repository (B13 round 10 L6, L61: the recovery then committed the home's
files there). An export, not a ``--git-dir`` option: an explicit git dir skips git's ownership check, so the
dubious-ownership stop would never fire (measured, git 2.54 and Apple git 2.50)."""

STEP1_COMMAND = (
    f"{CEILING} && sw_vers -productVersion && xcode-select -p && "
    f"{{ if [ -f ~/src/agent-context-sync/.git/HEAD ]"
    " || [ -f ~/src/agent-context-sync/.git ]; then"
    " git -C ~/src/agent-context-sync pull --ff-only; else"
    " git clone https://github.com/renchris/agent-context-sync.git ~/src/agent-context-sync; fi; }"
    f" && {INSTALL_SH} --version && {INSTALL_SH} --log-start '{START_AGENT}' && {INSTALL_SH} --list-folders"
)
"""Step 1 since setup prompt v6: preflight, clone or pull, the installer's compat line, the friction log's
attempt header and the folder list, in one command (one tool call, v5b review L5). Since v8 the header's
value starts with the prompt's version."""

V7_STEP1_COMMAND = STEP1_COMMAND.replace(START_AGENT, "<agent>")
"""Step 1 as setup prompts v6 and v7 had it: ``--log-start`` names no version. A saved copy still runs it."""

_CHECKOUT = "~/src/agent-context-sync"
FORMAT_PATCH = (
    f"git -C {_CHECKOUT} -c format.filenameMaxLength=128 format-patch -q"
    f' --suffix=".$(git -C {_CHECKOUT} branch --show-current).patch"'
    f" origin/main.. -o ~/agent-context/setup/local-work"
)
"""The keep commands' patch writer. Since v19 git's 64-character file-name cap is a config value of 128, so
the
branch suffix no longer cuts the subject to about 25 characters (B13 round 10 L17, L30: one defect). Not the
``--filename-max-length`` option: git before 2.30 fails on it after the commit, where an unknown config key is
ignored."""

TO_THE_UPDATE = f"git -C {_CHECKOUT} switch -q main && git -C {_CHECKOUT} reset -q --keep origin/main"
"""How each recovery moves main to the update since v20. Up to v19 it was ``switch -C main origin/main``,
which Apple git 2.39.5 (the Command Line Tools' git, and the CI runner's) runs even when another worktree
has main checked out: it moved that main under the other checkout, whose index and files then held a staged
revert of the update (B13 round 11 M69, measured). A plain ``switch main`` refuses a main checked out
elsewhere on every git from 2.39.5 to 2.54, with "is already checked out at" or "is already used by
worktree"; the tree is clean after the keep's commit, so ``reset --keep`` loses nothing. ``-q``: a plain
switch prints git's "Your branch is behind ... by N commits" before the count, false once the reset ran (the
lane's review); the refusal still prints."""

MAIN_UPSTREAM = (
    f'{{ test -n "$(git -C {_CHECKOUT} config --get branch.main.merge)"'
    f" || git -C {_CHECKOUT} branch --set-upstream-to=origin/main main; }}"
)
"""How each recovery gives main the Source's main as its upstream when it has none, ending the chain. The
no-tracking recovery has ended so since v16; the keep command and its retry since v21 (B13 round 12 N76):
v20's :data:`TO_THE_UPDATE` dropped the upstream ``switch -C main origin/main`` had set, so a keep run on a
branch with an upstream, from a main with none, left main with none, and step 1's next pull met "no
tracking information" and ran a second, empty keep (measured, git 2.54). Conditional, so a main that tracks
something keeps it; after the fetch, so origin/main exists; and safe to run again. Not inside
:data:`TO_THE_UPDATE`, which also sits inside the no-tracking recovery's if-branch."""

KEEP_LOCAL_WORK = (
    f"{CEILING} && git -C {_CHECKOUT} merge-base --is-ancestor main HEAD"
    f" && git -C {_CHECKOUT} switch -c local-work-$(date +%Y%m%d-%H%M%S) && git -C {_CHECKOUT} add -A"
    f" && {{ git -C {_CHECKOUT} diff --cached --quiet"
    f" || git -C {_CHECKOUT} -c user.name=agentsync -c user.email=agentsync@localhost commit -q"
    f" -m 'local changes kept before update'; }}"
    f" && {FORMAT_PATCH}"
    f" && git -C {_CHECKOUT} fetch origin && {TO_THE_UPDATE} && {MAIN_UPSTREAM}"
)
"""Step 1's recovery when the pull fails on local changes (field report 2026-10-05: an agent
stopped at step 1 because uncommitted work sat in the checkout and the prompt forbids stash and
reset). It also writes that work as patches beside the setup report, so it comes back with the
report. It commits and switches branches, so no allow rule covers it: it runs only on a failed
pull, and the tool asks first. Since v16 (B13 round 7 H19) it also keeps a local commit, which the
pull reports as "Not possible to fast-forward": it commits only when something is staged, writes a
patch for every commit the update lacks, and moves main to the update, where v15's commit failed on a
clean tree and left the checkout on a branch with no upstream. Its first step stops it, changing nothing,
when main has commits the checked-out branch lacks: the reset of main would leave them on no branch. Since
v20 main moves by :data:`TO_THE_UPDATE`, and since v21 it ends with :data:`MAIN_UPSTREAM`."""

KEEP_RETRY = f"{CEILING} && git -C {_CHECKOUT} fetch origin && {TO_THE_UPDATE} && {MAIN_UPSTREAM}"
"""The keep command's last four steps (the fetch, the switch, the reset and, since v21, setting main's
upstream), run again when only they failed (the changes are kept by then)."""

KEEP_BRANCH_WORK = (
    f"{CEILING} && git -C {_CHECKOUT} switch -c local-work-$(date +%Y%m%d-%H%M%S) && git -C {_CHECKOUT} "
    "add -A"
    f" && {{ git -C {_CHECKOUT} diff --cached --quiet"
    f" || git -C {_CHECKOUT} -c user.name=agentsync -c user.email=agentsync@localhost commit -q"
    f" -m 'local changes kept before update'; }}"
    f" && {FORMAT_PATCH}"
    f" && {{ if git -C {_CHECKOUT} merge-base --is-ancestor main HEAD; then"
    f" {TO_THE_UPDATE}; else git -C {_CHECKOUT} switch -q main; fi; }}"
    f' && n=$(git -C {_CHECKOUT} rev-list --count origin/main..@{{-1}}) && echo "$n"'
    f' && {{ [ "$n" -gt 0 ] || git -C {_CHECKOUT} branch -D @{{-1}}; }}'
    f" && {MAIN_UPSTREAM}"
)
"""Step 1's recovery when the pull says "no tracking information" (a branch with no upstream, such as one a
v15 keep command left) or the keep command stops at its first step (main has commits the checked-out branch
lacks). v16 ran a bare ``git switch main``, which an uncommitted edit to a file main changes refused, so step
1 looped, and which copied none of the branch's commits to local-work (B13 round 8 J7, J8: one defect, built
once). It keeps the work on a new branch, leaving the person's own branch as it was, writes a patch per
commit the update lacks (``origin/main..``, as the keep command: ``main..`` also copied the update's own
commits when the branch was cut from a newer origin/main, measured), goes back to main, and gives main the
Source's main as its upstream when it has none, so a main with no upstream does not loop either.

Since v18 it prints how many commits the local-work branch keeps, the number the words after it key on, and
tests main's upstream inside ``$( )``, so an upstream main already has prints no bare ``refs/heads/main``
(B13 round 9 K12, K13). Each patch name carries its keep's branch (``--suffix``), so a second keep no longer
overwrites a first keep's patch of the same number and subject (K28).

Since v20 a count of 0 deletes that branch, in the same chain right after the switch, where ``@{-1}`` is still
the local-work branch (B13 round 11 M83): it sat at the old main, a commit neither main nor origin/main is
at, so both readers counted it and every later report carried a local-work line. A count of 0 means
origin/main holds every commit of the branch, and an uncommitted change would have made it at least 1, so
nothing is lost. ``-D``, since on the ``switch main`` path the branch is an ancestor of origin/main, not of
main, and ``-d`` would refuse."""

KEEP_BRANCH_TELL = (
    "then tell me the name of the branch I was on. The number it printed is how many commits the local-work"
    " branch keeps: if it is 0, there was nothing to keep and the command removed that empty branch, so say"
    " that; otherwise tell me the name of the new local-work branch and say that all of my work, uncommitted"
    " changes included, is on it and copied as patches to ~/agent-context/setup/local-work, and, if the"
    " branch I was on is not main, that my commits also stay on it. Then run step 1's command again."
)
"""What step 1 says after :data:`KEEP_BRANCH_WORK`. v17 always said "my commits stay on my branch" and that
the work was "copied as patches": a main with no upstream and nothing of its own got neither (an empty
branch, no patch), and on a main with a commit of its own the next keep's ``switch -C main origin/main``
moves main (B13 round 9 K12). Since v20 the empty branch of a count of 0 is gone, so it is not named (M83)."""

WORKTREE_MOVE = f"git -C <that path> worktree move {_CHECKOUT} <new place>"
WORKTREE_REPAIR = "git -C <that path> worktree repair <new place>"
WORKTREE_DETACH = "git -C <that path> switch --detach"
WORKTREE_BACK = f"git -C {_CHECKOUT} switch main"
"""The two commands the worktree stop gives the person since v20 (B13 round 11 M17): moved by hand, a second
worktree leaves the other checkout an entry git marks prunable, and that checkout cannot switch to the
local-work branch the person was told keeps the work ("already used by worktree", measured); ``worktree
move`` moves it cleanly, ``worktree repair`` fixes a folder already moved."""

PERSONS_COMMANDS = "(those four commands are mine to run in my own terminal: do not run them yourself)"
"""What follows the git stop's quote since v18: its commands are the person's (B13 round 9 K31); four since
v26, the checkout's own remote first (B13 round 17 U32)."""

GIT_REMOTE_QUERY = "git -C ~/src/agent-context-sync config --get remote.origin.url"
"""What the git stop's quote names first since v26: this checkout's own remote, which step 1's pull reads and
which a company mirror changes (B13 round 17 U32; the report's install source line says `origin: other`).
Since v27 by its configured URL: `remote get-url` applies a URL rewrite, which the quote's next way names, so
a rewrite alone read as a mirror remote (B13 round 18 V70); and only once there is a checkout: a clone that
failed leaves no folder, and the query then fails (V33). Since v28 "if ~/src/agent-context-sync/.git
exists": a clone that failed leaves no checkout, but it keeps a folder that existed before it, and the query,
run in the person's terminal with no GIT_CEILING_DIRECTORIES, then reads any repository above it, such as a
dotfiles repo in ~, and prints that one's remote (B13 round 19 W26)."""

GIT_REWRITE_QUERY = "git config --get-regexp '^url\\..*\\.insteadof$'"
GIT_PROXY_QUERY = "git config --get-urlmatch http.proxy https://github.com"
"""The two read-only git queries the git stop's hint gives the person (since v15): the URL rewrites git
applies, and the proxy git's own setting names for github.com. The agent runs neither on its normal path."""

FRICTION_LOG_TEMPLATE = (
    f"{INSTALL_SH} --log '<step>' '<kind>' '<what happened>' '<what would have avoided it, or ->'"
)
"""The one command the agent logs each friction line with: install.sh appends it (0600), so no command in the
block redirects to a ``~`` path (v5b review L4), and the four values are single-quoted, so a backtick or
``$(...)`` in the agent's words is text, not a command (K7)."""

AGENTSYNC = "~/.local/bin/agentsync"
LOOP_COMMANDS = [f"{AGENTSYNC} sync", f"{AGENTSYNC} curate", f"{AGENTSYNC} sync", f"{AGENTSYNC} status"]
"""Setup prompt v7 step 3's loop commands, in the order the step names them: sync, the curate a NEXT line
usually names, sync again, and status (a sync's NEXT without syncing, unless a check FAILs:
:data:`STATUS_NEXT`). Each has an exact pre-allow rule."""

SYNC_NO_DOWNLOAD = f"{AGENTSYNC} sync --materialise-budget 0"
"""Setup prompt v8 step 3's sync before the report: one that downloads nothing, the form of install.sh's own
first sync (``FIRST_SYNC``). The plain sync downloads up to each folder's budget every time, and the files it
brings take the OCR time first, so twelve more of those cost up to 12 GiB a folder and read nothing again."""

VERSION_CHECK = f"{INSTALL_SH} --version"
"""What the Xcode and other-git-failure stops run before they log or report, since v19: step 1 updated
nothing there, so the copy on disk may be older than the prompt, and only this prompt's installer logs and
reports (B13 round 10 L31). The not-a-checkout, worktree and ownership stops run no install.sh at all."""

STOP_CAUSE = (
    "(say only this stop's cause: at the Xcode stop, the Command Line Tools installed; at the"
    " other-git-failure stop, git able to reach GitHub)"
)
"""What the version check's two no-report branches say fixes the stop, since v23 (B13 round 14 Q32): each
stop's own cause, never both."""

UNLOADED_CONFIG = (
    'or, if that file exists but does not load (the report\'s Configuration section says "loads: no:", with'
    ' the colon), list no folders and do not parse it yourself: say "unknown: ~/agent-context/sources.toml'
    ' does not load", followed by the reason the report gives'
)
"""The finish's case for a sources.toml that exists but does not load, since v20 (B13 round 11 M28). Since
v22 it keys on setup_report's own ``- loads: no: <error>``, colon included (B13 round 13 O44): v21's
"loads: no" is also the start of the shell report's ``- loads: not checked``, and came first. Since v24 on
that line alone (B13 round 15 S55): v23's "or step 2 printed a "configuration error" for it" also matched
the first sync's refusal of a config that loads (install.sh's exit-78 branch), which the report says
``- loads: yes, but`` for (:data:`REFUSED_CONFIG`)."""

REFUSED_CONFIG = (
    '; and, outside those two cases, if the report\'s Configuration section says "loads: yes, but" (the file'
    ' loads, but a sync refused it: fixing that is mine), say so after the folders or the "none", followed by'
    " the error that line gives;"
)
"""What the finish adds after :data:`UNKNOWN_FIRST` since v24 (B13 round 15 S8, S55): a config that loads
but that a sync refuses (a mirrored source deleted from it, a policy.toml that does not pass) is listed like
any other, or as "none" when its last local folder is the one deleted, and setup_report's ``- loads: yes,
but ...`` line gives the reason (``_sync_refusal``). "A sync refused it", not "every sync refuses it": the
line from step 2's install.out names a cause the report cannot check again (the lane's review)."""

UNCHECKED_CONFIG = (
    'or, if the report\'s Configuration section says "loads: not checked" (the shell report, when agentsync'
    " could not run), then whatever the file holds, list no folders and do not parse it yourself: say"
    ' "unknown: ~/agent-context/sources.toml was not checked", followed by the reason the report gives'
)
"""The finish's case for a sources.toml the shell report could not check, since v21 (B13 round 12 N25, lane
RB's words): with agentsync unable to run, the shell report prints ``- loads: not checked (<reason>)``, and
the finish had only "loads: no" and the folder list, neither of which the agent could read. Since v22 it comes
before :data:`UNLOADED_CONFIG` and forbids parsing the file, as that case does (B13 round 13 O28, O44): v21
said only "list no folders", so an agent that opened an inbox-only file could answer "none", which agentsync
never checked."""

STATUS_NEXT = (
    f"`{AGENTSYNC} status` prints the same NEXT: line without syncing, unless one of its checks FAILs: then"
    " it exits 1, its NEXT: names that [FAIL] line, unless a missing or out-of-date skill copy or a sign-in"
    " comes first, as in a sync's NEXT, and a [FAIL] fix that is mine (step 2) stays mine."
)
"""Step 3's sentence on status, since v23 (B13 round 14 Q2; lane B's words for its Q1, Q9, Q33): a sync's NEXT
does not stop at a failing check, status's does, and a person's fix stays the person's, as step 2 says.
Since v24 it names the steps rule 1 gives first, a FAIL or not (B13 round 15 S60): with the skill copy
missing or out of date, or a Graph source signed out, status's NEXT is that step, as a sync's is
(``loop.next_step``; :func:`test_status_next_names_rule_1s_own_steps_before_a_fail`)."""

UNKNOWN_FIRST = "(these two unknown cases come before the others)"
"""What closes :data:`UNLOADED_CONFIG` since v22 (B13 round 13 O28): the finish joins its folder cases with
"or", and nothing said which wins when the file also has, or lacks, a kind = "local" source."""

REPORT_COMMAND = f"{INSTALL_SH} --report-only"
"""Setup prompt v7 step 3's last command, the report, which first appends the friction log's end line (KISS
K17: no separate --log-end). v6 ran it after ``agentsync it-request ...;``; v7 drafts no IT request (K04)."""

FRICTION_KINDS = ("question", "click", "approval", "deviation", "error", "prompt")
"""Setup prompt v6's closed list of friction line kinds (judge findings J1, J2); the steps are timed by the
installer, so there is no start or end kind (v5b review V2, L9)."""

FRICTION_LOG = Path("agent-context") / "setup" / "friction.md"
"""The friction log install.sh --log-start, --log and --report-only append to, relative to HOME."""


def _report_step() -> int:
    """The step that runs the report (install.sh --report-only)."""
    [n] = [n for n, text in _prompt_steps().items() if any("--report-only" in c for c in _commands(text))]
    return n


def test_readme_prompt_names_its_version_and_the_installer_compat_line() -> None:
    """The prompt says which version it is in its first line and again in step 1, where it requires the
    installer's number to be exactly its own: a copy that is older than the installer must not be used, and
    one that is newer than the checkout cannot work. An older copy is copied again from the README on the
    main branch, where the two always match; for a newer one the installer says which of two causes it is
    (B13 round 5 F17): a copy from another branch or a fork, copied again, or a checkout that follows a fork
    or another branch, pointed at main. The prompt passes on what the installer says (B13 round 6 G15,
    G67)."""
    block = _one_prompt_block()
    assert block.startswith("Set up agentsync on this Mac (setup prompt v"), (
        "the version tag is in the first line"
    )
    version = _prompt_version()
    assert version == PROMPT[0], (
        "the prompt's version and PROMPT move together, with every change of its text"
    )
    steps = _prompt_steps()
    assert len(steps) == len(STEP_TITLES) == 3
    for text, title in zip(steps.values(), STEP_TITLES, strict=True):
        assert text.lower().startswith(title), (title, text)
    step1 = steps[1]
    flat1 = " ".join(step1.split())
    [required] = re.findall(r'does not end with "setup-prompt-compat (\d+)"', flat1)
    assert int(required) == version, "the installer's number is the prompt's own"
    assert "or higher" not in block, "a newer installer means this copy is old: it is not a pass any more"
    assert "If the --version inside step 1's one command does not end with \"setup-prompt-compat " in flat1, (
        "the compat check reads step 1's --version (v19: the Xcode and git stops run one of their own, L31)"
    )
    flat = " ".join(step1.split())
    # B13 round 6 G15, G67: the stop always said "copy the prompt again" and called every such copy older,
    # while the installer's error for a newer copy whose checkout follows a fork or another branch says to
    # point the checkout at main (a fresh copy stops the same way). The installer knows which cause it is, so
    # the prompt passes its words on, and copies again only when --version alone failed, with the checkout as
    # the next thing to look at.
    # B13 round 7 H65: installers v8 to v14 answer a newer copy with "Tell the person to copy the prompt
    # again", so v15's "tell me that, in its words" relayed only that, and the checkout sentence (reached only
    # with no such error) never came for a checkout that follows a fork: each fresh copy stopped the same way.
    # It now follows any error whose words do not name the checkout. H70: the installer's error ends with
    # "run step 1's command again" for a current copy whose --log-start value was changed, while the stop said
    # to run no other step. That sentence is printed for every copy, so the exception keys on the installer's
    # number being this prompt's own (a copy that does not match never sees it), and allows one rerun.
    assert (
        "If the --version inside step 1's one command does not end with "
        f'"setup-prompt-compat {version}", or the command says '
        "the pasted "
        f"prompt is not the installer's, stop and run no other step: this prompt is v{version}, and a copy "
        "that does not match the installer must not be used. But if the error says this installer is for "
        f"v{version}, this prompt's own number, the copy matches and only step 1's command was changed: run "
        "it again once, exactly as written here, and stop as above if it stops the same way again. When "
        "step 1 stops here, if the command's error says what to tell me, tell me that, in its words; "
        "otherwise (--version "
        "printed another number and no such error), tell me to copy the prompt again from README.md on the "
        "main branch of the Source above. Either way, unless the error's words already mention this Mac's "
        "checkout, also tell me that if a fresh copy stops here too, this Mac's checkout "
        "(~/src/agent-context-sync) does not follow main of the Source: point it there, or move it aside so "
        "step 1 clones it, then paste the prompt again."
    ) in flat
    assert "an older copy must not be used" not in flat
    script = SCRIPTS[0].read_text(encoding="utf-8")
    assert "Tell the person to %s." in script, "the error the prompt defers to says what to tell the person"
    assert 'what="$said and this installer is for setup prompt v$SETUP_PROMPT_COMPAT: $what"' in script, (
        "the words the exception keys on are the installer's, with its own number"
    )
    assert "run step 1'\\''s command again once, exactly as the prompt writes it." in script
    assert f"--log-start 'prompt v{version}, <agent>'" in step1, "the copy tells the installer its version"
    assert f"go to step {_report_step()}" in flat1.split("one command does not end", 1)[1], (
        "a failed folder listing ends at the report"
    )


def test_setup_feedback_shows_the_current_prompt_version() -> None:
    """B13 round 2 R66: setup-feedback.md's friction.md row still showed ``--log-start 'prompt v9, <agent>'``
    and ``Prompt: v9`` two versions later, while the same page's step 1 line said v11. Every example of what
    step 1 writes there is the current prompt's; "since v9" and the per-version lists are history."""
    page = (DEPLOY / "setup-feedback.md").read_text(encoding="utf-8")
    [row] = [ln for ln in page.splitlines() if ln.startswith("| `~/agent-context/setup/friction.md` |")]
    assert f"--log-start '{START_AGENT}'" in row and f"`Prompt: v{PROMPT[0]}`" in row
    starts = re.findall(r"--log-start 'prompt v(\d+), <agent>'", page)
    assert starts and set(starts) == {str(PROMPT[0])}, starts


def test_the_prompt_text_changes_only_with_its_version() -> None:
    """Twice a saved copy of the prompt was pasted again without anyone knowing it was old: its wording had
    changed on main while its version had not. The version now moves with every change of the text, and
    this holds the two together: the block's digest is pinned beside its version."""
    version, digest = PROMPT
    block = _one_prompt_block()
    found = hashlib.sha256(block.encode("utf-8")).hexdigest()
    assert (_prompt_version(), found) == (version, digest), (
        f"the setup prompt's text changed (its SHA-256 is now {found}): bump its version (see PROMPT) "
        "and set both values of PROMPT"
    )


def test_readme_prompt_compat_matches_the_installer() -> None:
    """The number the prompt requires is the one the installer in the same commit prints: a prompt whose
    text changes without a bump fails above, and one bumped without the installer fails here, not on a new
    Mac (judge finding I2). The report module and the issue form carry the same number."""
    from agentsync import setup_report  # noqa: PLC0415

    [required] = re.findall(
        r'does not end with "setup-prompt-compat (\d+)"', " ".join(_prompt_steps()[1].split())
    )
    compat = _installer_compat()
    assert compat is not None, "scripts/install.sh has no setup-prompt-compat constant"
    assert compat == int(required), (
        f"README requires setup-prompt-compat {required}, install.sh prints {compat}"
    )
    assert compat == setup_report.PROMPT_VERSION, "setup_report.PROMPT_VERSION is the installer's number"
    rule = _installer_help().split("# Setup prompt: SETUP_PROMPT_COMPAT", 1)[1].split("#\n", 1)[0]
    assert "Bump both on ANY change to the prompt's text" in " ".join(rule.replace("#", " ").split())


def test_readme_step1_is_one_command() -> None:
    """Step 1 is one line the agent runs as given (one approval at most): preflight, code, the friction log's
    attempt header and the folder list. The other commands it names are the ones it tells the person to run:
    the Command Line Tools install, and the two read-only git queries of the git stop's hint (since v15 in
    code marks, B13 round 6 G24), and the preamble names only the per-line --log template (no separate header
    command: v5b review L5)."""
    step1 = _prompt_steps()[1]
    assert " ".join(step1.split()).startswith(
        "Preflight, code and folders, in one command (replace <agent> with your tool and model id; its first "
        "part, which each git recovery below repeats, keeps git from using a repository above ~/src):"
    )
    assert _commands(step1) == [
        STEP1_COMMAND,
        "xcode-select --install",
        KEEP_LOCAL_WORK,
        KEEP_RETRY,
        KEEP_BRANCH_WORK,
        WORKTREE_MOVE,
        WORKTREE_REPAIR,
        WORKTREE_DETACH,
        WORKTREE_BACK,
        GIT_REMOTE_QUERY,
        GIT_REWRITE_QUERY,
        GIT_PROXY_QUERY,
        VERSION_CHECK,
        _LIST_FOLDERS,
    ]
    assert 'tell me: "Install the Xcode Command Line Tools with `xcode-select --install`' in step1
    assert "If --list-folders printed no folder paths (only a NEXT: line)" in step1
    assert "ask which to sync" in step1, "the folder question is step 1's, after the list"
    assert _commands(_preamble()) == [FRICTION_LOG_TEMPLATE]
    # B13 H1: the Xcode and git stops said "and stop" and ordered no line, so a session that stopped there
    # left nothing the report could read. Since v11 both go to the report and log the failed command first, as
    # a step 1 error (a step that is not a number has no step column, and no reader opens an attempt on it).
    report = _report_step()
    assert f"then paste this prompt again\", and go to step {report}'s report." in step1
    flat = " ".join(step1.split())
    # B13 round 2 R22: the git stop gave the person nothing to do once git works, which cost a "what now?"
    # turn. HTTPS_PROXY must be in the environment of the shell the agent runs in, so the person starts a new
    # session from a terminal where git reaches GitHub, then pastes the prompt again.
    # B13 round 5 F24: "check that setting" sent a person whose terminal holds a stale HTTPS_PROXY to git's
    # http.proxy, which printed nothing. A stale HTTPS_PROXY, https_proxy or ALL_PROXY fails exactly as git's
    # own setting does ("Could not resolve proxy: <its host>", measured, git 2.54), so the error alone cannot
    # tell the sources apart: the hint names each, with the command that shows it, and says which one wins.
    # `--get-urlmatch ... https://github.com` also prints a proxy scoped to that URL ([http "https://github.com"]),
    # which `--get http.proxy` misses (measured, round 5 review).
    # B13 round 6 G73: a URL rewrite (url.<base>.insteadOf = https://github.com/) also puts another host in
    # git's error ("Could not resolve host: <it>", git 2.54), and both proxy commands print nothing for it;
    # `--get-regexp 'url\..*insteadof'` prints it (git keeps key names in lower case). "Which wins" stays on
    # the proxy setting: a rewrite is another mechanism. G24: each command sits in code marks, as the Xcode
    # one does, so the person sees where it ends; `env | grep -i proxy` also shows a lower-case all_proxy.
    # B13 round 7 H21: that quote was one 106-word sentence, 86 words of it one parenthesis nesting the three
    # sources, with the one action after all of it; it is now sentences, ending on the action. H25: the
    # unanchored query also printed url.<base>.pushInsteadOf, which never changes a fetch or pull URL; the
    # anchor leaves it out and keeps every insteadOf, whatever its case (git lower-cases the key name).
    assert (
        'If git failed any other way, show me its error and tell me: "A corporate proxy may need HTTPS_PROXY '
        "set in your terminal. If git's error names a host other than github.com, git was told to use that "
        "host in one of four ways, and any of them may point at a network this Mac is not on: this "
        f"checkout's own remote, if ~/src/agent-context-sync/.git exists, which `{GIT_REMOTE_QUERY}` prints "
        "and which this setup expects to be https://github.com/renchris/agent-context-sync.git, never the "
        "cause after a clone that failed, since that leaves no checkout; a URL "
        f"rewrite, which `{GIT_REWRITE_QUERY}` prints; git's own proxy setting, which `{GIT_PROXY_QUERY}` "
        "prints and which wins over the terminal's; or the terminal's HTTPS_PROXY, https_proxy, ALL_PROXY or "
        "all_proxy, which `env | grep -i proxy` shows. Once git can reach GitHub, or the remote this "
        "checkout uses, from a terminal, start a "
        "new session from that terminal and paste this prompt again. If the setup report gets written, its "
        "`proxy in git's config` and `URL rewrite in git's config` entries say whether each is set, never "
        "which host, its install source line says `origin: other` when the checkout's remote is not GitHub, "
        "and what it says about proxy variables comes from the shell the report ran in, which "
        "may not be "
        f"your terminal\" {PERSONS_COMMANDS}, and go to step {report}'s report."
    ) in flat
    # B13 round 11 M33: the two labels stood unmarked in the quote ("its entries proxy in git's config and
    # ..."), which read as a broken sentence; code marks, as the quote's commands have, and no clash with
    # "git's". M21: "only set or not set" was false, since both reports add a bracket to "set" (a count of
    # rewrites, and that git's proxy wins over HTTPS_PROXY); the count and the bracket stay (H20), and the
    # quote says "whether".
    assert "say only set or not set" not in flat and "its entries proxy" not in flat
    # B13 round 10 L74: round 9's verifier offered a pointer to the report's proxy lines. The quote names only
    # the two labels both reports print (the Python report has no "proxy environment" line), says the report
    # gives set or not set and no host, and that its proxy-variable reading is the agent's shell, not the
    # person's terminal, where git fails.
    script = SCRIPTS[0].read_text(encoding="utf-8")
    report_py = (ROOT / "src" / "agentsync" / "setup_report.py").read_text(encoding="utf-8")
    for label in ("proxy in git's config", "URL rewrite in git's config"):
        assert f'say "- {label}: set' in script and f'say "- {label}: not set"' in script, label
        assert f"{label}: " in report_py, label
    assert "proxy environment" not in flat
    # B13 round 9 K31: the block opens with "Run each command yourself", and the quote named three commands
    # without saying whose they are; run by the agent, their output puts proxy hosts, company paths and any
    # stored credential in the chat, where the report prints only yes or no
    assert flat.count(PERSONS_COMMANDS) == 1
    quote = flat.split('tell me: "A corporate proxy', 1)[1].split('" (those four', 1)[0]
    assert "(" not in quote, "H21: sentences, no parenthesis"
    assert "check that setting" not in flat
    assert "that host is a proxy git was told to use" not in flat
    # B13 round 4 Q25: "if the error names a proxy, the one in git's own settings ... comes before" made an
    # always-true fact conditional, and "the one" read as the proxy the error named. An unreachable proxy in
    # git's settings fails as "Failed to connect to <its host> port <N>", which never says "proxy" (measured,
    # git 2.54), so the check is a host other than github.com, and the quote stays unconditional.
    assert "if the error names a proxy" not in flat
    # R29: git's own http.proxy comes before HTTPS_PROXY (measured: with both set, git used the config value),
    # so a hint naming only HTTPS_PROXY pointed the wrong way when git's error names a proxy. The setting is
    # named for the agent to read; the prompt does not tell it to change ~/.gitconfig.
    assert "~/.gitconfig" not in step1 and "git config --global" not in step1
    # R32: "either of these two stops" came right after three cases, the middle one a recovery, not a stop.
    # R25: the line a session logs here is the only one it writes before --log-start, so it carries the
    # prompt's version and the agent in --log-start's words, which the report reads for that attempt alone.
    # B13 round 8 J19: a ~/src/agent-context-sync that is not a git checkout has no installer, so a log
    # keyed on the folder ran an install.sh that does not exist (rc 127). The condition is the installer.
    # B13 round 9 K25: git's "detected dubious ownership" is a stop of its own, logged the same way.
    # B13 round 10 L31: those four stops ran whatever installer the copy held (a v14 archive's logged and
    # reported, measured), and the ownership stop ran another account's script. The not-a-checkout, worktree
    # (v20, M17) and ownership stops run no install.sh at all; the Xcode and git stops, where step 1 updated
    # nothing, log and report only when that copy's --version is this prompt's.
    assert (
        "Those three stops end there, with no report: the code in that folder is unknown, shared with "
        "another checkout or another account's, so run no ~/src/agent-context-sync/scripts/install.sh "
        'command at all after them; your final message still follows step 3\'s "Finish with".'
    ) in flat
    assert (
        "Before going to the report from the Xcode stop or the other-git-failure stop above, if "
        "~/src/agent-context-sync/scripts/install.sh exists, run "
        f"`{INSTALL_SH} --version`: step 1 did not update that copy (its pull did not run, or failed), so "
        "it may be older than this prompt. If it exists and its output "
        f'ends with "setup-prompt-compat {PROMPT[0]}", log the command that failed (git\'s pull or clone, or '
        "xcode-select -p; not step 1's whole command) and its error as the command printed it, host names "
        "included (the report replaces the host of a proxy, of a URL rewrite, of a git remote and of a "
        '"Could not resolve" or "Failed to connect to" message, but keeps a public host such as github.com; '
        "the role rule is for your own words), as a "
        "step 1 error, starting its "
        f"what-text with {START_AGENT} and a colon (the same words as in step 1's command; write no colon "
        "inside <agent>), and go to the report. If it exists and its output does not end so (or the command "
        "fails), that stop has no report either: tell me that the "
        "code on this Mac is not this prompt's version and that setup stopped before it could be updated, "
        "and that this asks nothing more of me: once the cause of this stop is fixed, the next paste of this "
        f"prompt tries to update that code first {STOP_CAUSE}."
    ) in flat
    # B13 round 14 Q31: "log the failed command" read literally logged step 1's whole one-liner, and the
    # Items line, cut at 220 characters, kept no word of git's error
    assert "log the failed command" not in flat
    # B13 round 13 O27: with no installer (the Xcode stop on a new Mac, a first clone that failed) there is no
    # output, so v21's bare "Otherwise" held and told the person the code on the Mac was not this prompt's
    # version, where there is no code and the next paste clones it. Each branch now says what to do.
    assert (
        "If it does not exist, run nothing more and write no report (step 3's last no-report case): tell me "
        "that setup stopped before the code was downloaded, and that once the cause of this stop is fixed, "
        f"the next paste of this prompt downloads it {STOP_CAUSE}."
    ) in flat
    # B13 round 14 Q32: both branches gave both stops' causes, so at the Xcode stop a literal agent told a
    # person with no git problem about GitHub, which would not help: git never ran there
    assert "(or git can reach GitHub)" not in flat and flat.count(STOP_CAUSE) == 2
    assert "go to the report. Otherwise that stop has no report" not in flat
    # B13 round 11 M32: "no pull ran" was false at the other-git-failure stop, where the pull ran and failed
    # (rc 1, FETCH_HEAD emptied, HEAD unchanged: measured). M16: on a field Mac with an older installer, the
    # person heard the stop's own action and then a bare "not this prompt's version", which read as a second
    # problem to solve; it now says the next paste tries the update (not that it succeeds: local changes or a
    # checkout off main have their own recoveries in step 1).
    assert "no pull ran" not in flat
    # B13 round 12 N27: "did not finish" read as cut off, but at the other-git-failure stop the pull ran to
    # its end and failed (rc 1, FETCH_HEAD emptied: measured)
    assert "did not finish" not in flat
    assert "the not-a-checkout stop, the ownership stop" not in flat
    assert "either of these two stops" not in flat
    assert "and stop. If git pull failed" not in step1 and "HTTPS_PROXY set) and stop" not in step1
    from agentsync import setup_report  # noqa: PLC0415

    stated = setup_report._STEP1_STATED_RE.match(f"{START_AGENT.replace('<agent>', 'Contoso Agent, m-1')}: x")
    assert stated is not None and stated.groups() == (str(PROMPT[0]), "Contoso Agent, m-1"), (
        "the words the prompt asks for are the ones the report reads"
    )


def test_prompt_routes_changes_to_the_source_not_the_checkout() -> None:
    """Field report 2026-10-05: an agent on the corporate Mac built converters and OCR in the checkout itself.
    The prompt forbids editing the checkout and sends wanted changes to fix-request.md; the finish lines name
    the one bring-back file, which carries the report, the fix request and step 1's local-work patch."""
    block = " ".join(_one_prompt_block().split())
    assert "Do not edit any file in ~/src/agent-context-sync" in block
    assert "write what and why to ~/agent-context/setup/fix-request.md" in block
    # The bring-back report (2026-10-06): the fix request comes back unredacted, so it names roles. B13 N44: a
    # report numbers placeholders in the order it registers values, so an added folder, a listing past its
    # bound or the shell fallback report gives a folder another number, while fix-request.md outlives the
    # report it was written against. The role names the folder; the placeholder only sits beside it.
    assert (
        "In that file, name a folder, a file or a person by a role that tells it apart from the others "
        "on this Mac, adding the setup report's placeholder if it has one (\"the folder of scanned "
        'invoices (<folder-2>)"), not by its real name, unless the name itself is the bug. Placeholder '
        "numbers can change from one report to the next; the role does not."
    ) in block
    naming = block.split("In that file, ", 1)[1].split(" agentsync never needs", 1)[0]
    assert "or by the setup report's placeholder" not in naming, "a placeholder alone does not survive"
    assert "a project folder" not in naming, "every synced folder is one: that role tells none apart"
    # The second bring-back (2026-10-07): the file outlives a session and nothing may be deleted, so a session
    # that found an earlier request could only append to it, and one that ran the report twice rewrote its own
    # "## Not used". The rules give the file its boundary: one part per session, under that session's heading.
    # The heading has the time too: a failed session and the one pasted after it are often the same day's, and
    # a heading with the date alone would be the same for both. A session that asks for no change still adds
    # step 3's "## Not used". Read literally, nothing put that section under the session's own heading, so it
    # could land under the last session's, and triage would read it as that session's, with the wrong version
    # and time (v10 rehearsal, 2026-10-08; B13 H5).
    assert (
        "arrives with the next pull. That file is kept from session to session: leave an earlier session's "
        "text as it is, write yours after it under a heading of your own that names this prompt's version "
        "and the current date and time, and rewrite only your own part. Write that heading even when this "
        'session asks for no change, since step 3\'s "## Not used" section goes under it; a session that '
        'asks for no change and adds no "## Not used" section (step 3 says when) writes no heading. In that '
        "file, name a folder"
    ) in block
    # B13 round 6 G26: "unless step 3 skips its "## Not used" section: that section goes under it" read as if
    # the skipped section still went under the heading; the reason now comes first, and the exception stays a
    # no-change session's (one that writes a fix request still heads it). G5: the exception named step 3's
    # skip, which the folder wait and the version stop never reach with a checkout present; it now names the
    # section, and step 3 says none of its three no-report cases has one.
    assert "unless step 3 skips" not in block
    assert 'Four cases have no report and no "## Not used" section:' in block
    # B13 round 5 F26: with no exception, a session whose checkout is missing created fix-request.md only to
    # hold a heading, and once the folder existed step 3's skip could never apply. With no checkout there is
    # nothing to head; the exception names step 3's skip, so the condition is written once, there.
    assert 'section asks for no change: step 3\'s "## Not used"' not in block
    assert "today's date" not in block, "the date alone does not tell two sessions of one day apart"
    assert len(re.findall(r"setup prompt v\d+", block)) == 1, "the heading rule holds no version of its own"
    feedback = " ".join((DEPLOY / "setup-feedback.md").read_text(encoding="utf-8").split())
    private = feedback.split("**Privately (no public post needed):**", 1)[1].split("**", 1)[0]
    assert "copy `~/agent-context/bring-back.md` back" in private
    assert "never paste this file into the public form" in private
    assert "Section 2 is the whole file, every session's part in the order written" in private
    assert "Since prompt v11 that includes a session that asks for no change" in private
    assert "then the lines from the first difference down are that later session's" in private
    assert "identify the folder by that role, not by the placeholder's number" in private
    # Where triage starts. Not at the last heading: nothing on the Mac knows what was copied back, so a part
    # whose file nobody copied sits above the next session's heading. The file received last is what is known.
    assert "Do not start at the last session heading: the Mac cannot know what was copied back" in private
    assert (
        "triage compares section 2 with section 2 of the last bring-back file received from that Mac, finds "
        "the first line that differs, and reads from the session heading above it down to the end. With no "
        "earlier file from that Mac, it reads all of section 2"
    ) in private
    assert "triaged then" not in feedback, "no page says an earlier part was already triaged: none knows it"
    # Section 3 is the local work kept since an earlier attempt's report; an older patch is one line there,
    # which the page names by the installer's own first words.
    assert "Section 3 holds the local work kept since an earlier attempt reached its report" in private
    # B13 round 17 U39: the line names the earlier report that held it first (.sent), the time only without it
    assert (
        "one `not repeated:` line names the earlier report that held it, `not repeated: a patch file an "
        "earlier attempt's report held (<time>)`" in private
    )
    assert "line then gives its line count and the first 12 hex digits of its SHA-256" in private
    assert "still in `~/agent-context/setup/local-work`" in private
    assert "}not repeated: a patch file last written $at," in SCRIPTS[0].read_text(encoding="utf-8")
    finish = _prompt_steps()[3].rsplit("Finish with:", 1)[1]
    assert "~/agent-context/bring-back.md, the one file I review and copy back" in finish
    # B13 round 4 Q24: only --report-only writes bring-back.md, so a failed clone left none, and the stop for
    # a copy that is not the installer's could leave an earlier session's. The clause follows whether a report
    # ran.
    assert " ".join(finish.split()).endswith(
        "step 1 kept, or a note of the commits it could not carry; a patch an earlier report carried is not "
        "repeated, only listed there, by its hash when "
        "it can be read); if the "
        "report's run printed that bring-back.md could not be written, say so, do not name "
        "~/agent-context/bring-back.md, and name the files the NEXT: line says to copy back instead; "
        f"{UNREAD_PATCH_TOLD} {LOCAL_WORK_TOLD} {CUT_OFF_TOLD} if no "
        'report ran this session, or you are waiting for my folder answer, say "none" for that file instead, '
        "and do not point me to an older bring-back.md."
    )
    # B13 round 17 U29: the report's NEXT for a kept patch it could not read asks for a chmod and the report
    # again, but the report is the last command and the finish relayed only the NEXT before it; the person is
    # told in that line's words, and the agent changes nothing and runs no report again. The words the finish
    # keys on are install.sh's own.
    script = SCRIPTS[0].read_text(encoding="utf-8")
    assert 'NEXT_MSG="$back does not hold $up this account cannot read' in script
    assert "kept patch file" in script.split('NEXT_MSG="$back does not hold $up', 1)[0].rsplit("up=", 1)[1]
    # B13 round 21 Y66: the cut-off clause's keyed words are install.sh's own NEXT, as the unreadable-patch
    # clause's are
    assert (
        'NEXT_MSG="part of the report was cut off or not measured (its Summary says which, and why)' in script
    )
    assert "part of the report was cut off or not measured" in CUT_OFF_TOLD
    # B13 round 12 N71 (lane RB's words): when the report could not write bring-back.md, the finish still
    # named that file; it now names what the report's NEXT: line says to copy back instead. B13 round 13
    # O15: v21's "the files the NEXT: line names" took in bring-back.md itself, which that NEXT names as the
    # earlier attempt's file not to send, against the same sentence's "do not name
    # ~/agent-context/bring-back.md"
    assert "the files the NEXT: line names" not in " ".join(finish.split())
    # B13 round 11 M35: "the patches of any work step 1 kept" was false on a re-run after an earlier report:
    # section 3 then holds only "not repeated:" lines, as the file's own header says. Not worded as what was
    # copied back: the installer decides by file time and cannot know that.
    assert "Section 3 sends no patch this time: each was in an earlier report and is listed by its hash" in (
        SCRIPTS[0].read_text(encoding="utf-8")
    ), "the header the finish's words match"
    step3 = _prompt_steps()[3]
    assert 'add a "## Not used" section to the end of ~/agent-context/setup/fix-request.md' in step3
    assert (
        "~/agent-context/setup/fix-request.md (one per session: if this session already added one, rewrite "
        "that one): one line for each part of agentsync this session never used"
    ) in step3
    assert step3.index("## Not used") < step3.index("Then the report, even after a failure")
    for verb in (
        "sync",
        "curate",
        "status",
        "add-source",
        "accept-deletions",
        "adopt",
        "purge",
        "hold",
        "offboard",
    ):
        assert verb in step3.split("## Not used", 1)[1].split("saying why", 1)[0], verb
    assert "purge, hold (a legal or records hold, not a pause), offboard;" in step3
    # B13 round 7 H26: after a stop in step 1 every one of the twelve parts is unused for the same reason, and
    # the line per part was twelve copies of it. Only a stop before step 2 shares a line: in any other session
    # the per-part reasons are what triage reads.
    assert (
        "background sync), saying why (if setup stopped before step 2, no part was used: one line saying so "
        "and naming the step it stopped at is enough); skip this part if "
        "~/src/agent-context-sync/scripts/install.sh does not exist."
    ) in " ".join(step3.split())


def test_step1_keeps_local_changes_on_a_branch_and_updates(tmp_path: Path) -> None:
    """Field report 2026-10-05: a checkout with uncommitted edits and new files that the update also touches.
    Step 1's recovery command keeps every change on a local branch, nothing stashed, reset or deleted, and
    leaves main fast-forwarded to the published commit."""
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "init.defaultBranch=main"]
    upstream, home = tmp_path / "upstream", tmp_path / "home"
    checkout = home / "src" / "agent-context-sync"
    subprocess.run([*git, "init", "-q", str(upstream)], check=True)
    (upstream / "convert.py").write_text("v6\n", encoding="utf-8")
    subprocess.run([*git, "-C", str(upstream), "add", "-A"], check=True)
    subprocess.run([*git, "-C", str(upstream), "commit", "-q", "-m", "v6"], check=True)
    subprocess.run([*git, "clone", "-q", str(upstream), str(checkout)], check=True)
    (upstream / "convert.py").write_text("v7\n", encoding="utf-8")
    subprocess.run([*git, "-C", str(upstream), "commit", "-q", "-am", "v7"], check=True)
    (checkout / "convert.py").write_text("local edit\n", encoding="utf-8")
    (checkout / "ocr.py").write_text("new local file\n", encoding="utf-8")
    pull = subprocess.run(
        ["git", "-C", str(checkout), "pull", "-q", "--ff-only"], capture_output=True, check=False
    )
    assert pull.returncode != 0, "the pull this recovery is for fails on the local edit"

    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", **xcrun_db()}
    proc = subprocess.run(
        ["/bin/zsh", "-c", KEEP_LOCAL_WORK], cwd=home, env=env, capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (checkout / "convert.py").read_text(encoding="utf-8") == "v7\n" and not (
        checkout / "ocr.py"
    ).exists()
    branches = subprocess.run(
        ["git", "-C", str(checkout), "branch", "--list", "local-work-*", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert len(branches) == 1
    kept = {
        name: subprocess.run(
            ["git", "-C", str(checkout), "show", f"{branches[0]}:{name}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for name in ("convert.py", "ocr.py")
    }
    assert kept == {"convert.py": "local edit\n", "ocr.py": "new local file\n"}
    [patch] = (home / "agent-context" / "setup" / "local-work").iterdir()
    text = patch.read_text(encoding="utf-8")
    assert "+local edit" in text and "+new local file" in text
    status = subprocess.run(
        ["git", "-C", str(checkout), "status", "--porcelain"], capture_output=True, text=True, check=True
    )
    assert status.stdout == ""


_GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "init.defaultBranch=main"]


def _git_out(checkout: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(checkout), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _behind_upstream(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    """An upstream with one commit, a checkout of it at ~/src/agent-context-sync in a throwaway HOME, and the
    upstream one commit ahead: (upstream, home, checkout, env)."""
    upstream, home = tmp_path / "upstream", tmp_path / "home"
    checkout = home / "src" / "agent-context-sync"
    subprocess.run([*_GIT, "init", "-q", str(upstream)], check=True)
    (upstream / "convert.py").write_text("v6\n", encoding="utf-8")
    subprocess.run([*_GIT, "-C", str(upstream), "add", "-A"], check=True)
    subprocess.run([*_GIT, "-C", str(upstream), "commit", "-q", "-m", "v6"], check=True)
    subprocess.run([*_GIT, "clone", "-q", str(upstream), str(checkout)], check=True)
    (upstream / "install.sh").write_text("v7\n", encoding="utf-8")
    subprocess.run([*_GIT, "-C", str(upstream), "add", "-A"], check=True)
    subprocess.run([*_GIT, "-C", str(upstream), "commit", "-q", "-m", "v7"], check=True)
    return (
        upstream,
        home,
        checkout,
        {"HOME": str(home), "PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", **xcrun_db()},
    )


def _zsh(command: str, home: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/zsh", "-c", command], cwd=home, env=env, capture_output=True, text=True, check=False
    )


def _next_second() -> None:
    """Wait for the clock's next second: the keep commands name their branch local-work-<date to the second>,
    and in the field step 1 runs between two of them; here nothing does."""
    now = int(time.time())
    while int(time.time()) == now:
        time.sleep(0.05)


def _pull(checkout: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(checkout), "pull", "--ff-only"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_readme_keeps_local_work_then_runs_step_1_again(tmp_path: Path) -> None:
    """B13 round 5 F28: step 1's local-changes command can fail at its last steps (a network that drops
    between the first pull's fetch and this one). The prompt said what to do only on success, so a session
    could read the failure as "log it and go to the report" and never name the branch. The command is an &&
    chain: when only the fetch or the switch fails, the branch, the commit and the patch already exist
    (measured here against an origin that is gone), so the prompt names the two steps to run again, then step
    1. Since v16 (B13 round 7 H19) those are a fetch and a switch of main to the update, not a pull."""
    step1 = " ".join(_prompt_steps()[1].split())
    assert (
        "then tell me the branch name (my changes are safe on it, and copied as patches to "
        "~/agent-context/setup/local-work) and run step 1's command again. If only its last four steps (the "
        "fetch, the switch, the reset or setting main's upstream) failed, my changes are already kept: tell "
        "me the branch name, run "
        f"`{KEEP_RETRY}`, then step 1's command again."
    ) in step1
    assert "only that command's last step, the pull" not in step1
    upstream, home, checkout, env = _behind_upstream(tmp_path)
    (checkout / "convert.py").write_text("local edit\n", encoding="utf-8")
    upstream.rename(tmp_path / "gone")  # the origin can no longer be reached
    proc = _zsh(KEEP_LOCAL_WORK, home, env)
    assert proc.returncode != 0, "the fetch fails"
    [branch] = _git_out(checkout, "branch", "--list", "local-work-*", "--format=%(refname:short)").split()
    assert _git_out(checkout, "show", f"{branch}:convert.py") == "local edit", "the changes are kept"
    [patch] = (home / "agent-context" / "setup" / "local-work").iterdir()
    assert "+local edit" in patch.read_text(encoding="utf-8"), "and copied as a patch"
    assert _git_out(checkout, "status", "--porcelain") == ""
    (tmp_path / "gone").rename(upstream)  # the network is back
    retry = _zsh(KEEP_RETRY, home, env)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert _git_out(checkout, "branch", "--show-current") == "main"
    assert _pull(checkout, env).returncode == 0, "step 1 again pulls a clean main"
    assert _git_out(checkout, "show", f"{branch}:convert.py") == "local edit", "the branch still holds them"


@pytest.mark.parametrize("version", ["v20", "v21"])
def test_a_keep_from_a_branch_leaves_main_with_an_upstream(tmp_path: Path, version: str) -> None:
    """B13 round 12 N76: a checkout on a branch with an upstream, local changes on it and a main with no
    upstream. v20's keep command moved main with ``switch main`` and ``reset --keep``, where v19's ``switch -C
    main origin/main`` had set the upstream, so step 1's next pull said "no tracking information" and the
    session ran the no-tracking recovery: a second keep, of nothing. Since v21 the keep command ends with
    :data:`MAIN_UPSTREAM`; v20's command is the control. A main that already tracks a branch keeps it."""
    upstream, home, checkout, env = _behind_upstream(tmp_path)
    subprocess.run([*_GIT, "-C", str(upstream), "branch", "feature", "main~1"], check=True)
    subprocess.run([*_GIT, "-C", str(checkout), "fetch", "-q", "origin"], check=True)
    track = ["switch", "-q", "-c", "feature", "--track", "origin/feature"]
    subprocess.run([*_GIT, "-C", str(checkout), *track], check=True)
    subprocess.run([*_GIT, "-C", str(checkout), "branch", "--unset-upstream", "main"], check=True)
    subprocess.run([*_GIT, "-C", str(upstream), "switch", "-q", "feature"], check=True)
    (upstream / "convert.py").write_text("v6 feature\n", encoding="utf-8")
    subprocess.run([*_GIT, "-C", str(upstream), "commit", "-q", "-am", "feature"], check=True)
    subprocess.run([*_GIT, "-C", str(upstream), "switch", "-q", "main"], check=True)
    (checkout / "convert.py").write_text("local edit\n", encoding="utf-8")
    pull = _pull(checkout, env)
    assert pull.returncode != 0 and "would be overwritten" in pull.stderr, pull.stderr
    command = KEEP_LOCAL_WORK if version == "v21" else KEEP_LOCAL_WORK.removesuffix(f" && {MAIN_UPSTREAM}")
    assert command != KEEP_LOCAL_WORK or version == "v21"
    proc = _zsh(command, home, env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _git_out(checkout, "rev-parse", "main") == _git_out(checkout, "rev-parse", "origin/main")
    again = _pull(checkout, env)
    if version == "v20":
        assert again.returncode != 0 and "no tracking information" in again.stderr, again.stderr
        return
    assert "set up to track 'origin/main'" in proc.stdout + proc.stderr, proc.stdout + proc.stderr
    assert again.returncode == 0 and "Already up to date" in again.stdout, again.stdout + again.stderr
    assert len(_git_out(checkout, "branch", "--list", "local-work-*").split()) == 1, "one keep, no second"
    # a main that already tracks a branch keeps it, and the retry sets nothing again
    tracks = ["branch", "--set-upstream-to=origin/feature", "main"]
    subprocess.run([*_GIT, "-C", str(checkout), *tracks], check=True)
    retry = _zsh(KEEP_RETRY, home, env)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert _git_out(checkout, "config", "--get", "branch.main.merge") == "refs/heads/feature"


@pytest.mark.parametrize("uncommitted", [False, True])
def test_readme_keeps_a_local_commit_and_moves_main_to_the_update(tmp_path: Path, uncommitted: bool) -> None:
    """B13 round 7 H19: a checkout with a local commit, behind its upstream. The pull fails with "Not
    possible to fast-forward", which the prompt did not name as local changes, and v15's keep command failed
    at its commit ("nothing to commit") and left the checkout on a branch with no upstream, so every later
    paste met "no tracking information" and got the network message. The v16 command commits only what is
    staged, writes one patch per commit the update lacks, and moves main to the update; the prompt names git's
    words for both kinds of local change, and the step back to main for a checkout left on a branch."""
    step1 = " ".join(_prompt_steps()[1].split())
    assert (
        "If git pull failed because of local changes in ~/src/agent-context-sync (uncommitted changes, which "
        'git reports as "would be overwritten", or a local commit, which git reports as "Not possible to '
        'fast-forward"), keep them on a new local branch and update, in one command:'
    ) in step1
    assert (
        'If the pull says "no tracking information", or the keep command exits 1 at once and prints nothing '
        "(main has commits the checked-out branch lacks), keep the checked-out branch's work on a new local "
        "branch and go back to main, moving main to the update when that branch keeps every commit of "
        "main, in "
        f"one command: `{KEEP_BRANCH_WORK}` {KEEP_BRANCH_TELL}"
    ) in step1
    assert step1.index(KEEP_BRANCH_WORK) < step1.index("If git failed any other way")
    rules = " ".join(_preamble().split())
    assert (
        "keeping local changes on a branch, moving main to the update and removing a local-work branch that "
        "kept nothing, as step 1 says, is none of these"
    ) in rules
    _upstream, home, checkout, env = _behind_upstream(tmp_path)
    (checkout / "ocr.py").write_text("local commit\n", encoding="utf-8")
    subprocess.run([*_GIT, "-C", str(checkout), "add", "-A"], check=True)
    subprocess.run([*_GIT, "-C", str(checkout), "commit", "-q", "-m", "local ocr"], check=True)
    if uncommitted:
        (checkout / "convert.py").write_text("local edit\n", encoding="utf-8")
    pull = _pull(checkout, env)
    assert pull.returncode != 0 and "Not possible to fast-forward" in pull.stderr, pull.stderr
    proc = _zsh(KEEP_LOCAL_WORK, home, env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _git_out(checkout, "branch", "--show-current") == "main"
    assert _git_out(checkout, "rev-parse", "main") == _git_out(checkout, "rev-parse", "origin/main")
    assert _git_out(checkout, "status", "--porcelain") == ""
    [branch] = _git_out(checkout, "branch", "--list", "local-work-*", "--format=%(refname:short)").split()
    assert _git_out(checkout, "show", f"{branch}:ocr.py") == "local commit", "the local commit is kept"
    patches = sorted((home / "agent-context" / "setup" / "local-work").iterdir())
    assert len(patches) == (2 if uncommitted else 1), patches
    text = "".join(p.read_text(encoding="utf-8") for p in patches)
    assert "+local commit" in text and ("+local edit" in text) is uncommitted
    assert _pull(checkout, env).returncode == 0, "the next step 1 pulls"


def test_a_checkout_a_v15_keep_left_on_its_branch_goes_back_to_main(tmp_path: Path) -> None:
    """B13 round 7 H19: v15's keep command, run on a local commit, stopped at its commit and left the checkout
    on its new branch, which has no upstream. The prompt's step back to main ends on the update with the
    commit
    kept. Up to v18 it went back to main as it was, so the next pull said "Not possible to fast-forward" and
    the keep command kept the same commit a second time, on a second branch and in a second patch (B13 round
    10 L9, probe G3c): since v19 a main whose every commit the new branch keeps is moved to the update."""
    _upstream, home, checkout, env = _behind_upstream(tmp_path)
    (checkout / "ocr.py").write_text("local commit\n", encoding="utf-8")
    subprocess.run([*_GIT, "-C", str(checkout), "add", "-A"], check=True)
    subprocess.run([*_GIT, "-C", str(checkout), "commit", "-q", "-m", "local ocr"], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "switch", "-q", "-c", "local-work-20261013-130336"], check=True
    )
    stuck = _pull(checkout, env)
    assert stuck.returncode != 0 and "no tracking information" in stuck.stderr, stuck.stderr
    assert _zsh(KEEP_BRANCH_WORK, home, env).returncode == 0
    assert _git_out(checkout, "branch", "--show-current") == "main"
    assert _git_out(checkout, "rev-parse", "main") == _git_out(checkout, "rev-parse", "origin/main")
    pull = _pull(checkout, env)
    assert pull.returncode == 0 and "Already up to date" in pull.stdout, pull.stdout + pull.stderr
    kept = _git_out(checkout, "branch", "--list", "local-work-*", "--format=%(refname:short)").split()
    assert len(kept) == 2 and "local-work-20261013-130336" in kept, "v15's branch and one new one, no third"
    patches = sorted((home / "agent-context" / "setup" / "local-work").iterdir())
    assert len(patches) == 1 and "+local commit" in patches[0].read_text(encoding="utf-8"), patches


def test_the_keep_command_leaves_a_main_with_commits_of_its_own_as_it_is(tmp_path: Path) -> None:
    """Round 7 lane P review of H19: on a checkout that is not on main, with a commit on main that the
    checked-out branch lacks, the keep command's reset of main (`switch -C main origin/main`) left that commit
    on no branch and in no patch (measured: `git branch -a --contains` printed nothing). Its first step now
    stops it, exit 1 with no output and nothing changed; the prompt's step back to main then keeps it."""
    _upstream, home, checkout, env = _behind_upstream(tmp_path)
    (checkout / "ocr.py").write_text("local commit\n", encoding="utf-8")
    subprocess.run([*_GIT, "-C", str(checkout), "add", "-A"], check=True)
    subprocess.run([*_GIT, "-C", str(checkout), "commit", "-q", "-m", "local ocr"], check=True)
    mine = _git_out(checkout, "rev-parse", "HEAD")
    subprocess.run([*_GIT, "-C", str(checkout), "switch", "-q", "-c", "dev", "origin/main"], check=True)
    (checkout / "convert.py").write_text("local edit\n", encoding="utf-8")
    proc = _zsh(KEEP_LOCAL_WORK, home, env)
    assert (proc.returncode, proc.stdout, proc.stderr) == (1, "", ""), "exits 1 at once and prints nothing"
    assert (
        _git_out(checkout, "branch", "--show-current") == "dev"
        and _git_out(checkout, "rev-parse", "main") == mine
    )
    assert not (home / "agent-context").exists() and "local-work" not in _git_out(checkout, "branch")
    assert _zsh(KEEP_BRANCH_WORK, home, env).returncode == 0
    _next_second()
    assert "Not possible to fast-forward" in _pull(checkout, env).stderr
    keep = _zsh(KEEP_LOCAL_WORK, home, env)
    assert keep.returncode == 0, keep.stdout + keep.stderr
    assert _git_out(checkout, "rev-parse", "main") == _git_out(checkout, "rev-parse", "origin/main")
    assert mine in _git_out(checkout, "rev-list", "--branches=local-work-*"), "main's own commit is kept"
    patches = "".join(
        p.read_text(encoding="utf-8") for p in (home / "agent-context" / "setup" / "local-work").iterdir()
    )
    assert "+local commit" in patches and "+local edit" in patches


def test_a_branch_with_no_upstream_and_an_edit_main_changes_is_kept_and_main_updated(tmp_path: Path) -> None:
    """B13 round 8 J7, J8 (one defect, built once): a branch with no upstream, one commit and an uncommitted
    edit to a file that main changes, while local main holds a commit the branch lacks. The pull says "no
    tracking information"; v16's `git switch main` refused the edit ("would be overwritten") and the keep
    command stopped at its first step, so step 1 looped, and nothing reached local-work. The recovery keeps
    everything on a new branch and as patches, leaves the person's branch as it was, and goes back to main,
    from which step 1's pull goes on."""
    upstream, home, checkout, env = _behind_upstream(tmp_path)
    (upstream / "convert.py").write_text("v8\n", encoding="utf-8")
    subprocess.run([*_GIT, "-C", str(upstream), "commit", "-q", "-am", "v8"], check=True)
    subprocess.run(["git", "-C", str(checkout), "pull", "-q", "--ff-only"], env=env, check=True)
    (upstream / "notes.md").write_text("v9\n", encoding="utf-8")
    subprocess.run([*_GIT, "-C", str(upstream), "add", "-A"], check=True)
    subprocess.run([*_GIT, "-C", str(upstream), "commit", "-q", "-m", "v9"], check=True)
    subprocess.run([*_GIT, "-C", str(checkout), "switch", "-q", "-c", "my-experiment", "HEAD~2"], check=True)
    (checkout / "exp.py").write_text("local tweak\n", encoding="utf-8")
    subprocess.run([*_GIT, "-C", str(checkout), "add", "-A"], check=True)
    subprocess.run([*_GIT, "-C", str(checkout), "commit", "-q", "-m", "local tweak"], check=True)
    (checkout / "convert.py").write_text("local edit\n", encoding="utf-8")
    mine = _git_out(checkout, "rev-parse", "my-experiment")
    stuck = _pull(checkout, env)
    assert stuck.returncode != 0 and "no tracking information" in stuck.stderr, stuck.stderr
    v16 = _zsh(f"git -C {_CHECKOUT} switch main", home, env)
    assert v16.returncode != 0 and "would be overwritten" in v16.stderr, "v16's step back to main loops"
    proc = _zsh(KEEP_BRANCH_WORK, home, env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _git_out(checkout, "branch", "--show-current") == "main"
    assert _git_out(checkout, "rev-parse", "my-experiment") == mine, "the person's branch is as it was"
    [kept] = _git_out(checkout, "branch", "--list", "local-work-*", "--format=%(refname:short)").split()
    assert _git_out(checkout, "show", f"{kept}:convert.py") == "local edit"
    assert _git_out(checkout, "show", f"{kept}:exp.py") == "local tweak"
    patches = sorted(p.name for p in (home / "agent-context" / "setup" / "local-work").iterdir())
    assert patches == [
        f"0001-local-tweak.{kept}.patch",
        f"0002-local-changes-kept-before-update.{kept}.patch",
    ], patches
    assert _git_out(checkout, "rev-parse", "--abbrev-ref", "main@{upstream}") == "origin/main", "left as is"
    assert _pull(checkout, env).returncode == 0, "step 1 again pulls main"
    assert _git_out(checkout, "rev-parse", "main") == _git_out(checkout, "rev-parse", "origin/main")


def test_a_main_with_no_upstream_gets_the_sources_main_and_does_not_loop(tmp_path: Path) -> None:
    """B13 round 8 J7's verifier: the pull also says "no tracking information" when main itself has no
    upstream, and going back to main changes nothing there, so step 1 looped (and each loop of the new
    recovery would add a branch). The recovery gives main origin/main as its upstream when it has none."""
    _upstream, home, checkout, env = _behind_upstream(tmp_path)
    subprocess.run(["git", "-C", str(checkout), "branch", "--unset-upstream"], check=True)
    (checkout / "convert.py").write_text("local edit\n", encoding="utf-8")
    assert "no tracking information" in _pull(checkout, env).stderr
    proc = _zsh(KEEP_BRANCH_WORK, home, env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _git_out(checkout, "rev-parse", "--abbrev-ref", "main@{upstream}") == "origin/main"
    [patch] = (home / "agent-context" / "setup" / "local-work").iterdir()
    assert "+local edit" in patch.read_text(encoding="utf-8")
    assert _pull(checkout, env).returncode == 0, "step 1 again pulls"
    assert _git_out(checkout, "status", "--porcelain") == ""


@pytest.mark.parametrize(
    ("case", "count", "upstream_line"),
    [("main-nothing", "0", True), ("main-commit", "1", True), ("branch-commit", "2", False)],
)
def test_the_branch_recovery_prints_what_it_kept_and_no_bare_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, count: str, upstream_line: bool
) -> None:
    """B13 round 9 K12, K13. On a main with no upstream and nothing of its own (G3m) v17's recovery kept
    nothing, while the words after it said "my commits stay on my branch" and "copied as patches"; it now
    prints the count of commits the local-work branch keeps, and the words key on it. With main already
    following the Source (G3: a branch with no upstream) v17's `config --get` printed a bare
    ``refs/heads/main`` nobody was told about; the ``$( )`` test prints nothing, and only setting an
    upstream prints git's own "set up to track" line.

    Since v19 (B13 round 10 L9) each of these three cases moves main to the update, since the new branch keeps
    every commit of main: ``switch -C main origin/main`` prints that line before the count, and step 1's pull
    then reports nothing to do, where v18 left a main with a commit of its own behind and ahead, and the next
    keep kept that commit again.

    Since v20 (B13 round 11 M69) main moves by ``switch main`` and ``reset --keep``, which set no upstream, so
    git's "set up to track" line comes from the upstream step, after the count, and only where main had none.
    M83: with a count of 0 (main behind, no upstream, nothing of its own) v19 left the local-work branch at
    the old main, a commit neither main nor origin/main is at, so both readers counted it and every later
    public report said "local-work branch(es) in the checkout: 1". The command now deletes that branch, and
    keeps one that holds a commit."""
    assert f"`{KEEP_BRANCH_WORK}` {KEEP_BRANCH_TELL}" in " ".join(_prompt_steps()[1].split())
    _upstream, home, checkout, env = _behind_upstream(tmp_path)
    if case.startswith("main"):
        subprocess.run(["git", "-C", str(checkout), "branch", "--unset-upstream"], check=True)
    else:
        subprocess.run([*_GIT, "-C", str(checkout), "switch", "-q", "-c", "my-experiment"], check=True)
    if case != "main-nothing":
        (checkout / "exp.py").write_text("local tweak\n", encoding="utf-8")
        subprocess.run([*_GIT, "-C", str(checkout), "add", "-A"], check=True)
        subprocess.run([*_GIT, "-C", str(checkout), "commit", "-q", "-m", "local tweak"], check=True)
    if case == "branch-commit":
        (checkout / "convert.py").write_text("local edit\n", encoding="utf-8")
    assert "no tracking information" in _pull(checkout, env).stderr
    proc = _zsh(KEEP_BRANCH_WORK, home, env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # v20's quiet switch prints no "Your branch is behind" line before the count (the lane's review)
    out = proc.stdout.splitlines()
    assert "refs/heads/main" not in proc.stdout, proc.stdout
    branches = _git_out(checkout, "branch", "--list", "local-work-*", "--format=%(refname:short)").split()
    deleted = re.compile(r"Deleted branch local-work-\d{8}-\d{6} \(was [0-9a-f]+\)\.")
    said = [ln for ln in out if not deleted.fullmatch(ln)]
    assert said == [count, *(["branch 'main' set up to track 'origin/main'."] if upstream_line else [])], out
    assert any(deleted.fullmatch(ln) for ln in out) is (count == "0"), out
    assert len(branches) == (0 if count == "0" else 1), branches
    from agentsync import setup_report  # noqa: PLC0415

    monkeypatch.setenv("HOME", str(home))
    line = setup_report._local_work_line()
    assert (line is None) is (count == "0"), line
    assert _git_out(checkout, "rev-parse", "main") == _git_out(checkout, "rev-parse", "origin/main")
    pull = _pull(checkout, env)
    assert pull.returncode == 0 and "Already up to date" in pull.stdout, pull.stdout + pull.stderr
    local_work = home / "agent-context" / "setup" / "local-work"
    patches = list(local_work.iterdir()) if local_work.exists() else []
    assert len(patches) == int(count), patches
    assert _git_out(checkout, "rev-parse", "--abbrev-ref", "main@{upstream}") == "origin/main"


def test_an_empty_keep_on_the_way_back_to_a_main_of_its_own_is_deleted(tmp_path: Path) -> None:
    """B13 round 11 M83 on the ``switch main`` path (the lane's review: no test told ``-D`` from ``-d``). A
    branch at origin/main with no upstream, and a main with a commit of its own: the recovery goes back to
    main as it is, and the local-work branch keeps nothing (count 0). It is an ancestor of origin/main but not
    of main, where ``-d`` refuses ("not fully merged") and leaves the empty branch both readers count."""
    _upstream, home, checkout, env = _behind_upstream(tmp_path)
    (checkout / "ocr.py").write_text("local commit\n", encoding="utf-8")
    subprocess.run([*_GIT, "-C", str(checkout), "add", "-A"], check=True)
    subprocess.run([*_GIT, "-C", str(checkout), "commit", "-q", "-m", "local ocr"], check=True)
    subprocess.run(["git", "-C", str(checkout), "fetch", "-q"], env=env, check=True)
    mine = _git_out(checkout, "rev-parse", "main")
    for command, left in ((KEEP_BRANCH_WORK.replace(" branch -D ", " branch -d "), 1), (KEEP_BRANCH_WORK, 0)):
        _next_second()
        subprocess.run(
            [*_GIT, "-C", str(checkout), "switch", "-q", "--no-track", "-C", "old", "origin/main"], check=True
        )
        assert "no tracking information" in _pull(checkout, env).stderr
        proc = _zsh(command, home, env)
        assert proc.stdout.splitlines()[0] == "0", proc.stdout + proc.stderr
        assert _git_out(checkout, "branch", "--show-current") == "main"
        assert _git_out(checkout, "rev-parse", "main") == mine, "main keeps its own commit"
        branches = _git_out(checkout, "branch", "--list", "local-work-*", "--format=%(refname:short)").split()
        assert len(branches) == left, (command, branches, proc.stderr)
        for branch in branches:  # -d's leftover, cleared for the -D run
            subprocess.run(["git", "-C", str(checkout), "branch", "-D", branch], check=True)


def test_a_second_keep_leaves_the_first_keeps_patch(tmp_path: Path) -> None:
    """B13 round 9 K28: two keeps, each with an uncommitted change, wrote the same
    0001-local-changes-kept-before-update.patch, so the second overwrote the first in the folder the person
    was told holds it; after a folder wait no report had read the first. Each patch name now carries its
    keep's local-work branch, so both stay, and the bring-back's flat *.patch read sends both."""
    _upstream, home, checkout, env = _behind_upstream(tmp_path)
    for change in ("FIRSTCHANGE", "SECONDCHANGE"):
        (checkout / "convert.py").write_text(f"{change}\n", encoding="utf-8")
        proc = _zsh(KEEP_LOCAL_WORK, home, env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        _next_second()
    branches = _git_out(checkout, "branch", "--list", "local-work-*", "--format=%(refname:short)").split()
    patches = sorted((home / "agent-context" / "setup" / "local-work").iterdir())
    assert (
        sorted(p.name for p in patches)
        == sorted(f"0001-local-changes-kept-before-update.{b}.patch" for b in branches)
        and len(branches) == 2
    ), patches
    text = "".join(p.read_text(encoding="utf-8") for p in patches)
    assert "+FIRSTCHANGE" in text and "+SECONDCHANGE" in text
    env = tmp_home_env(home) | {"PATH": "/usr/bin:/bin"}
    for args in (["--log-start", f"prompt v{PROMPT[0]}, Test Agent"], ["--report-only"]):
        cp = subprocess.run(
            ["bash", str(SCRIPTS[0]), *args], cwd=home, env=env, capture_output=True, text=True, check=False
        )
        assert cp.returncode == 0, cp.stdout + cp.stderr
    back = (home / "agent-context" / "bring-back.md").read_text(encoding="utf-8")
    back = back.split("## 3. Local work", 1)[1]
    assert "+FIRSTCHANGE" in back and "+SECONDCHANGE" in back


def test_a_kept_patch_name_keeps_its_whole_subject(tmp_path: Path) -> None:
    """B13 round 10 L17, L30 (one defect, built once): git counts format-patch's ``--suffix`` inside its
    64-character file-name cap, so with the keep's 33-character branch suffix a subject kept about 25
    characters ("0001-local-changes-kept-before.<branch>.patch", probes G3v, g1k). Both keep commands set
    the cap
    to 128 as a config value: git before 2.30 ignores an unknown key, where the ``--filename-max-length``
    option would fail the command after its commit, with nothing written and main not restored."""
    for command in (KEEP_LOCAL_WORK, KEEP_BRANCH_WORK):
        assert FORMAT_PATCH in command and "--filename-max-length" not in command
    v18 = KEEP_LOCAL_WORK.replace(" -c format.filenameMaxLength=128 format-patch", " format-patch")
    names: dict[str, list[str]] = {}
    for version, command in (("v18", v18), ("v19", KEEP_LOCAL_WORK)):
        _upstream, home, checkout, env = _behind_upstream(tmp_path / version)
        (checkout / "note.md").write_text("a note\n", encoding="utf-8")
        subprocess.run([*_GIT, "-C", str(checkout), "add", "-A"], check=True)
        subject = "a made-up Contoso note about the setup"
        subprocess.run([*_GIT, "-C", str(checkout), "commit", "-q", "-m", subject], check=True)
        (checkout / "convert.py").write_text("local edit\n", encoding="utf-8")
        proc = _zsh(command, home, env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        [kept] = _git_out(checkout, "branch", "--list", "local-work-*", "--format=%(refname:short)").split()
        found = sorted(p.name for p in (home / "agent-context" / "setup" / "local-work").iterdir())
        names[version] = [name.removesuffix(f".{kept}.patch") for name in found]
    assert names == {
        "v18": ["0001-a-made-up-Contoso-note-ab", "0002-local-changes-kept-before"],
        "v19": ["0001-a-made-up-Contoso-note-about-the-setup", "0002-local-changes-kept-before-update"],
    }, names
    one = subprocess.run(
        ["git", "-c", "no.such.key=1", "-C", str(checkout), "status", "-s"], capture_output=True, check=False
    )
    assert one.returncode == 0, "git ignores a config key it does not know, so an older git runs the command"


def test_a_folder_at_the_checkouts_path_that_is_not_a_checkout_is_named(tmp_path: Path) -> None:
    """B13 round 8 J19: ~/src/agent-context-sync holding files but no .git makes step 1's clone fail with
    words the prompt did not name, so the person got the proxy message, and the log and report steps, keyed
    on the folder, ran an installer that is not there (rc 127). The prompt names git's words (measured here
    with step 1's own if/else) and keys both steps on the installer."""
    step1 = " ".join(_prompt_steps()[1].split())
    said = (
        'If git clone says the folder "already exists and is not an empty directory", or git says "not a git '
        'repository" or "invalid gitfile format", ~/src/agent-context-sync is not a checkout this setup can '
        "use: tell me that setup stopped at step 1 and to move that folder aside so step 1 clones it, then "
        "paste this prompt again."
    )
    assert said in step1 and step1.index(said) < step1.index("If git failed any other way")
    upstream, home, checkout, env = _behind_upstream(tmp_path)
    shutil.rmtree(checkout)
    checkout.mkdir()
    (checkout / "README.md").write_text("a copy\n", encoding="utf-8")
    code = f"{CEILING} && {{ " + STEP1_COMMAND.split("&& { ", 1)[1].split("; } &&", 1)[0] + "; }"
    proc = _zsh(code.replace("https://github.com/renchris/agent-context-sync.git", str(upstream)), home, env)
    assert proc.returncode == 128 and "already exists and is not an empty directory" in proc.stderr, (
        proc.stderr
    )
    assert not (checkout / "scripts" / "install.sh").exists(), "the condition the log and report key on"
    # B13 round 9 K25: v17 pulled whenever .git was a folder, so an empty or broken .git gave git's "not a git
    # repository", which the prompt routed to the proxy advice; under a home that is itself a repository, git
    # climbed to it, and "no tracking information" sent the recovery to commit the whole home there. Step 1
    # now pulls only when .git/HEAD exists, so both go to the clone and its named stop.
    assert (
        'If git says "detected dubious ownership", the folder belongs to another user account of this Mac: '
        "tell me that setup stopped at step 1 and to move it aside (do not run the safe.directory command "
        "git "
        "suggests), then paste this prompt again. Those three stops end there, with no report"
    ) in step1
    code = code.replace("https://github.com/renchris/agent-context-sync.git", str(upstream))
    for broken in ("empty", "no-head", "home-repo"):
        shutil.rmtree(checkout)
        if broken == "no-head":
            subprocess.run([*_GIT, "clone", "-q", str(upstream), str(checkout)], check=True)
            (checkout / ".git" / "HEAD").unlink()
        else:
            (checkout / ".git").mkdir(parents=True)
            (checkout / "README.md").write_text("a copy\n", encoding="utf-8")
        if broken == "home-repo":
            subprocess.run([*_GIT, "init", "-q", str(home)], check=True)
        v17 = code.replace(f"{CEILING} && ", "").replace(
            "[ -f ~/src/agent-context-sync/.git/HEAD ] || [ -f ~/src/agent-context-sync/.git ]",
            "[ -d ~/src/agent-context-sync/.git ]",
        )
        old = _zsh(v17, home, env)
        proc = _zsh(code, home, env)
        assert proc.returncode == 128 and "already exists and is not an empty directory" in proc.stderr, (
            broken,
            proc.stderr,
        )
        if broken == "home-repo":
            assert "no tracking information" in old.stderr, "v17 climbed to the home's repository"
            head = subprocess.run(
                ["git", "-C", str(home), "rev-parse", "--verify", "-q", "HEAD"],
                capture_output=True,
                check=False,
            )
            assert head.returncode != 0, "nothing ran in the home's repository: it has no commit"
        else:
            assert "not a git repository" in old.stderr, (broken, old.stderr)


def _step1_pull(upstream: Path, *, v18: bool = False) -> str:
    """Step 1's clone-or-pull with its ceiling, cloning from ``upstream``; with ``v18``, as v18 had it (no
    ceiling, no ``.git`` file test)."""
    code = f"{CEILING} && {{ " + STEP1_COMMAND.split("&& { ", 1)[1].split("; } &&", 1)[0] + "; }"
    code = code.replace("https://github.com/renchris/agent-context-sync.git", str(upstream))
    if v18:
        code = code.replace(f"{CEILING} && ", "").replace(" || [ -f ~/src/agent-context-sync/.git ]", "")
    return code


def _without_ceiling(command: str) -> str:
    return command.replace(f"{CEILING} && ", "")


def test_step1_and_its_recoveries_never_reach_a_repository_above_the_checkout(tmp_path: Path) -> None:
    """B13 round 10 L6, L61 (one defect, built once). In a home that is itself a git repository, a checkout
    whose ``.git`` git does not accept (only a HEAD file, or a HEAD that names no ref) passed v18's
    ``[ -f .git/HEAD ]`` test, and git searched upward to the home: the pull said "no tracking information",
    and the recovery for it switched the HOME repository to a local-work branch and committed the home's
    untracked files there (probe e4h). Step 1's command and each git recovery now start by exporting
    GIT_CEILING_DIRECTORIES=$HOME/src, so git never searches above ~/src, and such a checkout gives "not a git
    repository", the not-a-checkout stop. The export also covers the git calls inside ``$( )``.

    L61's ``--git-dir=.git`` would also stop the search, but an explicit git dir skips git's ownership check:
    another account's checkout would pull, running that account's config and hooks, and the ownership stop
    would never fire. Measured here with git's own test switch for a foreign owner."""
    for command in (STEP1_COMMAND, KEEP_LOCAL_WORK, KEEP_RETRY, KEEP_BRANCH_WORK):
        assert command.startswith(f"{CEILING} && "), command
    upstream, home, checkout, env = _behind_upstream(tmp_path)
    shutil.rmtree(checkout)
    subprocess.run([*_GIT, "init", "-q", str(home)], check=True)
    (home / ".dotrc").write_text("dotfiles\n", encoding="utf-8")
    subprocess.run([*_GIT, "-C", str(home), "add", ".dotrc"], check=True)
    subprocess.run([*_GIT, "-C", str(home), "commit", "-q", "-m", "dotfiles"], check=True)
    (home / ".netrc_demo").write_text("a secret\n", encoding="utf-8")
    before = (_git_out(home, "branch", "--show-current"), _git_out(home, "rev-parse", "HEAD"))
    recoveries = (KEEP_LOCAL_WORK, KEEP_RETRY, KEEP_BRANCH_WORK)
    for head in ("ref: refs/heads/main", "garbage"):
        shutil.rmtree(checkout, ignore_errors=True)
        (checkout / ".git").mkdir(parents=True)
        if head == "garbage":  # a HEAD that names no ref is refused even beside objects/ and refs/
            (checkout / ".git" / "objects").mkdir()
            (checkout / ".git" / "refs").mkdir()
        (checkout / ".git" / "HEAD").write_text(f"{head}\n", encoding="utf-8")
        (checkout / "README.md").write_text("a copy\n", encoding="utf-8")
        for command in (_step1_pull(upstream), *recoveries):
            proc = _zsh(command, home, env)
            assert proc.returncode != 0 and "not a git repository" in proc.stderr, (head, proc.stderr)
        assert (_git_out(home, "branch", "--show-current"), _git_out(home, "rev-parse", "HEAD")) == before
    old = _zsh(_step1_pull(upstream, v18=True), home, env)
    assert "no tracking information" in old.stderr, "v18 reached the home's repository"
    old = _zsh(_without_ceiling(KEEP_BRANCH_WORK), home, env)
    assert _git_out(home, "branch", "--show-current").startswith("local-work-"), "v18's recovery: the home"
    assert ".netrc_demo" in _git_out(home, "show", "--name-only", "--format=", "HEAD")
    # The ceiling never hides a real checkout: a clone, one under a symlinked ~/src, and a dubious owner.
    shutil.rmtree(home / ".git")
    shutil.rmtree(checkout)
    subprocess.run([*_GIT, "clone", "-q", str(upstream), str(checkout)], check=True)
    assert _zsh(_step1_pull(upstream), home, env).returncode == 0
    foreign = env | {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}
    proc = _zsh(_step1_pull(upstream), home, foreign)
    assert proc.returncode != 0 and "detected dubious ownership" in proc.stderr, proc.stderr
    # A read with no fetch: a pull's fetch from a local upstream runs upload-pack, which the CI runner's git
    # also refuses under the foreign-owner switch, so it would fail for a reason that is not the checkout's.
    found = _zsh(f"git -C {_CHECKOUT} rev-parse HEAD", home, foreign)
    assert found.returncode != 0 and "detected dubious ownership" in found.stderr, found.stderr
    git_dir = _zsh(f"git -C {_CHECKOUT} --git-dir=.git rev-parse HEAD", home, foreign)
    assert git_dir.returncode == 0, "L61's --git-dir skips the ownership check: not built"
    (home / "src").rename(home / "real-src")
    (home / "src").symlink_to(home / "real-src")
    assert _zsh(_step1_pull(upstream), home, env).returncode == 0, "a symlinked ~/src still pulls"


def test_a_checkout_whose_git_is_a_file_pulls(tmp_path: Path) -> None:
    """B13 round 10 L27. A working checkout whose ``.git`` is a file (``git clone --separate-git-dir``, or a
    worktree) failed v18's ``[ -f .git/HEAD ]`` test, so step 1 cloned onto it and the person was told to move
    a good checkout aside. Step 1 also pulls when ``.git`` is a file. An empty or junk ``.git`` file then
    reaches the pull, whose "invalid gitfile format" the not-a-checkout stop names, as it names a dangling
    gitdir's "not a git repository"; both need the allow rule the ``.git`` test has. A second worktree of
    another checkout also pulls: ``test_a_recovery_in_a_second_worktree_leaves_the_other_checkouts_main``."""
    step1 = " ".join(_prompt_steps()[1].split())
    assert 'or git says "not a git repository" or "invalid gitfile format", ~/src/agent-context-sync' in step1
    assert "Bash([ -f ~/src/agent-context-sync/.git ])" in _claude_code_rules()
    upstream, home, checkout, env = _behind_upstream(tmp_path)
    shutil.rmtree(checkout)
    gitdir = home / "gitdirs" / "acs.git"
    gitdir.parent.mkdir()
    subprocess.run(
        [*_GIT, "clone", "-q", "--separate-git-dir", str(gitdir), str(upstream), str(checkout)], check=True
    )
    assert (checkout / ".git").is_file()
    old = _zsh(_step1_pull(upstream, v18=True), home, env)
    assert old.returncode == 128 and "already exists and is not an empty directory" in old.stderr, old.stderr
    proc = _zsh(_step1_pull(upstream), home, env)
    assert proc.returncode == 0 and "Already up to date" in proc.stdout, proc.stdout + proc.stderr
    for text, said in (("", "invalid gitfile format"), ("hello\n", "invalid gitfile format")):
        (checkout / ".git").write_text(text, encoding="utf-8")
        proc = _zsh(_step1_pull(upstream), home, env)
        assert proc.returncode == 128 and said in proc.stderr, (text, proc.stderr)
    (checkout / ".git").write_text(f"gitdir: {home / 'nowhere'}\n", encoding="utf-8")
    proc = _zsh(_step1_pull(upstream), home, env)
    assert proc.returncode == 128 and "not a git repository" in proc.stderr, proc.stderr


WORKTREE_STOP = (
    'If a recovery above, the keep command and its retry included, stops because main "is already used by'
    ' worktree" or "is already checked out at" a path (git\'s version decides which words), run no other'
    " recovery and not step 1's command again: ~/src/agent-context-sync and the checkout at that path share"
    " one repository, and that checkout has main checked out, so this setup cannot use the folder as it is."
    " Tell me that setup stopped at step 1; the name of the branch I was on, which is as it was, with its"
    " commits; the name of the local-work branch the recovery made, which this folder now has checked out and"
    " which holds my work, uncommitted changes included, also copied as patches to"
    " ~/agent-context/setup/local-work; then, if ~/src/agent-context-sync/.git is a file, that this folder is"
    " a second worktree of the checkout at that path, and to move it aside with"
    f" `{WORKTREE_MOVE}` (put in git's path; the new place is mine to choose), not by hand: that checkout"
    " marks a folder moved by hand as prunable, and pruning it, which git can also do on its own later,"
    f" leaves the moved folder no longer a checkout; if I already moved it by hand, `{WORKTREE_REPAIR}`"
    " fixes that; or, if .git is a folder, that this folder is the main checkout and the worktree at that"
    f" path has main checked out, and to take that worktree off main, for example with `{WORKTREE_DETACH}`,"
    f" and then run `{WORKTREE_BACK}`; then paste this prompt again (those commands are mine to run: do not"
    " run them yourself)."
)
"""Step 1's worktree stop since v20 (B13 round 11 M17, M69). The lane's review: the same refusal comes when
~/src/agent-context-sync is the MAIN checkout and a worktree of it elsewhere has main out, where ``worktree
move`` fails ("is a main working tree", measured on 2.39.5, 2.50.1 and 2.54.0), so the stop splits on the
``.git`` test step 1 already has; and after the keep command's refusal its retry and the no-tracking
recovery refused the same way, leaving a second local-work branch and the same patch twice, so it says to
run none of them."""


def _gits() -> list[str]:
    """The folders of the git programs this machine has, one per version: the PATH's ``/usr/bin/git`` and,
    on a Mac with Xcode, the Command Line Tools' own (Apple git 2.39.5, where M69's reset was measured)."""
    found: dict[str, str] = {}
    for folder in ("/usr/bin", "/Library/Developer/CommandLineTools/usr/bin"):
        if (Path(folder) / "git").exists():
            version = subprocess.run(
                [f"{folder}/git", "--version"], capture_output=True, text=True, check=False
            ).stdout.strip()
            if version:
                found.setdefault(version, folder)
    return list(found.values())


@pytest.mark.parametrize("case", ["behind", "diverged", "level", "free"])
@pytest.mark.parametrize("git_dir", _gits())
def test_a_recovery_in_a_second_worktree_leaves_the_other_checkouts_main(
    tmp_path: Path, git_dir: str, case: str
) -> None:
    """B13 round 11 M69. ~/src/agent-context-sync as a second worktree of another checkout, on a branch with
    no upstream, reaches the no-tracking recovery. v19's ``switch -C main origin/main`` there: git 2.50 and
    2.54 refuse ("is already used by worktree"), but Apple git 2.39.5, the Command Line Tools' git, and the CI
    runner's ``/usr/bin/git`` run it, moving the main the OTHER checkout has out, whose index and files then
    hold a staged revert of the update (behind), or refuse with "is already checked out at", words v19 did not
    name, so the person got the proxy advice (diverged). Since v20 every recovery goes back with a plain
    ``switch main``, which refuses a main checked out elsewhere on every version, then ``reset --keep``: the
    other checkout's main and files are untouched in each case, the work stays on the local-work branch, and
    the stop names both phrases. A worktree whose main is free still recovers (M69's verifier: a blanket
    worktree stop would turn that into a stop).

    M17: the stop named only the local-work branch. It names the branch the person was on and the
    local-work branch, says the folder is a second worktree, and gives ``worktree move`` (moved by hand, the
    other checkout marks the folder prunable, and a prune leaves the moved folder no checkout: measured)."""
    step1 = " ".join(_prompt_steps()[1].split())
    assert WORKTREE_STOP in step1 and step1.index(WORKTREE_STOP) < step1.index("If git failed any other way")
    assert "switch -C" not in _one_prompt_block()
    assert _one_prompt_block().count(TO_THE_UPDATE) == 3, (
        "the keep command, its retry and the no-tracking one"
    )
    upstream, home, checkout, env = _behind_upstream(tmp_path)
    env |= {"PATH": f"{git_dir}:/usr/bin:/bin"}
    shutil.rmtree(checkout)
    primary = home / "primary"
    subprocess.run([*_GIT, "clone", "-q", str(upstream), str(primary)], check=True)
    subprocess.run(
        [*_GIT, "-C", str(primary), "worktree", "add", "-q", "-b", "feat", str(checkout)], check=True
    )
    if case == "free":
        subprocess.run([*_GIT, "-C", str(primary), "switch", "-q", "-c", "other"], check=True)
    if case == "diverged":
        (primary / "mine.md").write_text("a commit of the other checkout\n", encoding="utf-8")
        subprocess.run([*_GIT, "-C", str(primary), "add", "-A"], check=True)
        subprocess.run([*_GIT, "-C", str(primary), "commit", "-q", "-m", "mine"], check=True)
    if case != "level":
        (upstream / "notes.md").write_text("v8\n", encoding="utf-8")
        subprocess.run([*_GIT, "-C", str(upstream), "add", "-A"], check=True)
        subprocess.run([*_GIT, "-C", str(upstream), "commit", "-q", "-m", "v8"], check=True)
        subprocess.run(["git", "-C", str(primary), "fetch", "-q"], env=env, check=True)
    (checkout / "convert.py").write_text("local edit\n", encoding="utf-8")
    feat = _git_out(checkout, "rev-parse", "feat")
    assert "no tracking information" in _zsh(_step1_pull(upstream), home, env).stderr
    before = (_git_out(primary, "rev-parse", "main"), _git_out(primary, "status", "--porcelain"))
    proc = _zsh(KEEP_BRANCH_WORK, home, env)
    [kept] = _git_out(checkout, "branch", "--list", "local-work-*", "--format=%(refname:short)").split()
    assert _git_out(checkout, "show", f"{kept}:convert.py") == "local edit", "the work is kept"
    assert _git_out(checkout, "rev-parse", "feat") == feat, "the person's branch is as it was"
    (patch,) = (home / "agent-context" / "setup" / "local-work").iterdir()
    assert "+local edit" in patch.read_text(encoding="utf-8")
    if case == "free":
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _git_out(checkout, "rev-parse", "main") == _git_out(checkout, "rev-parse", "origin/main")
        assert _git_out(primary, "status", "--porcelain") == before[1]
        return
    refused = ("is already used by worktree", "is already checked out at")
    assert proc.returncode != 0 and any(words in proc.stderr for words in refused), proc.stderr
    assert (_git_out(primary, "rev-parse", "main"), _git_out(primary, "status", "--porcelain")) == before
    assert _git_out(checkout, "branch", "--show-current") == kept
    # The keep command and its retry go back to main the same way, and refuse the same way.
    _next_second()
    for command in (KEEP_LOCAL_WORK, KEEP_RETRY):
        proc = _zsh(command, home, env)
        if (
            command == KEEP_LOCAL_WORK and case == "diverged"
        ):  # its ancestor test: main has a commit of its own
            assert (proc.returncode, proc.stdout, proc.stderr) == (1, "", "")
        else:
            assert proc.returncode != 0 and any(words in proc.stderr for words in refused), proc.stderr
        assert (_git_out(primary, "rev-parse", "main"), _git_out(primary, "status", "--porcelain")) == before
    # v19's step back to main where its ancestor test allowed the reset (diverged went to a plain switch main,
    # whose "is already checked out at" on 2.39.5 v19 did not name): on a git that runs it, main is checked
    # out in two worktrees, and behind, moved under the other one's files.
    if case == "diverged":
        return
    v19 = _zsh(f"git -C {_CHECKOUT} switch -C main origin/main", home, env)
    if v19.returncode == 0:
        assert (_git_out(primary, "status", "--porcelain") != before[1]) is (case == "behind")
    else:
        assert any(words in v19.stderr for words in refused), v19.stderr


def test_the_worktree_stops_commands_keep_a_moved_worktree(tmp_path: Path) -> None:
    """B13 round 11 M17: the worktree stop's two commands, run as the person would, with made-up paths.
    Moved by hand, the folder is a prunable entry of the other checkout, and a prune leaves it no checkout;
    ``worktree repair`` fixes that, and ``worktree move`` never makes it."""
    assert f"`{WORKTREE_MOVE}`" in WORKTREE_STOP and f"`{WORKTREE_REPAIR}`" in WORKTREE_STOP
    assert WORKTREE_STOP in " ".join(_prompt_steps()[1].split()), "the words this test runs are the prompt's"
    primary, home = tmp_path / "code" / "acs", tmp_path / "home"
    subprocess.run([*_GIT, "init", "-q", str(primary)], check=True)
    subprocess.run([*_GIT, "-C", str(primary), "commit", "-q", "--allow-empty", "-m", "a"], check=True)
    checkout = home / "src" / "agent-context-sync"
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", **xcrun_db()}
    for how in ("move", "by hand", "repair"):
        subprocess.run(
            [*_GIT, "-C", str(primary), "worktree", "add", "-q", "-b", f"lw-{how[:2]}", str(checkout)],
            check=True,
        )
        new = tmp_path / f"aside-{how[:2]}"
        if how == "move":
            command = WORKTREE_MOVE.replace("<that path>", str(primary)).replace("<new place>", str(new))
            assert _zsh(command, home, env).returncode == 0
        else:
            checkout.rename(new)
        if how == "repair":
            command = WORKTREE_REPAIR.replace("<that path>", str(primary)).replace("<new place>", str(new))
            assert _zsh(command, home, env).returncode == 0
        prunable = "prunable" in _git_out(primary, "worktree", "list", "--porcelain")
        assert prunable is (how == "by hand"), how
        subprocess.run(["git", "-C", str(primary), "worktree", "prune"], check=True)
        status = subprocess.run(["git", "-C", str(new), "status"], capture_output=True, check=False)
        assert (status.returncode == 0) is (how != "by hand"), how


@pytest.mark.parametrize("git_dir", _gits())
def test_a_recovery_in_the_main_checkout_whose_worktree_has_main_stops(tmp_path: Path, git_dir: str) -> None:
    """The lane's review of M17: ~/src/agent-context-sync can be the MAIN checkout, with a worktree of it
    elsewhere that has main out. The recovery refuses with the same words, but "a second worktree" is then
    backwards, and ``worktree move`` fails ("is a main working tree"). Its ``.git`` is a folder, which the
    stop keys on: the person takes the other worktree off main and puts this checkout back on it, and step 1
    pulls, with the work on the local-work branch."""
    step1 = " ".join(_prompt_steps()[1].split())
    assert "or, if .git is a folder, that this folder is the main checkout" in WORKTREE_STOP
    assert WORKTREE_STOP in step1
    upstream, home, checkout, env = _behind_upstream(tmp_path)
    env |= {"PATH": f"{git_dir}:/usr/bin:/bin"}
    linked = home / "linked"
    subprocess.run([*_GIT, "-C", str(checkout), "switch", "-q", "--no-track", "-c", "feat"], check=True)
    subprocess.run([*_GIT, "-C", str(checkout), "worktree", "add", "-q", str(linked), "main"], check=True)
    (checkout / "convert.py").write_text("local edit\n", encoding="utf-8")
    assert "no tracking information" in _zsh(_step1_pull(upstream), home, env).stderr
    before = (_git_out(linked, "rev-parse", "HEAD"), _git_out(linked, "status", "--porcelain"))
    proc = _zsh(KEEP_BRANCH_WORK, home, env)
    refused = ("is already used by worktree", "is already checked out at")
    assert proc.returncode != 0 and any(words in proc.stderr for words in refused), proc.stderr
    assert (_git_out(linked, "rev-parse", "HEAD"), _git_out(linked, "status", "--porcelain")) == before
    assert (checkout / ".git").is_dir(), "the case the stop tells apart"
    [kept] = _git_out(checkout, "branch", "--list", "local-work-*", "--format=%(refname:short)").split()
    move = WORKTREE_MOVE.replace("<that path>", str(linked)).replace("<new place>", str(home / "aside"))
    moved = _zsh(move, home, env)
    assert moved.returncode != 0 and "is a main working tree" in moved.stderr, moved.stderr
    for command in (WORKTREE_DETACH.replace("<that path>", str(linked)), WORKTREE_BACK):
        done = _zsh(command, home, env)
        assert done.returncode == 0, done.stderr
    pull = _zsh(_step1_pull(upstream), home, env)
    assert pull.returncode == 0, pull.stderr
    assert _git_out(checkout, "rev-parse", "main") == _git_out(checkout, "rev-parse", "origin/main")
    assert _git_out(checkout, "show", f"{kept}:convert.py") == "local edit", "the work is kept"


def _fake_step1_tools(bin_dir: Path, calls: Path) -> None:
    """Stand-ins for sw_vers, xcode-select and git that log their argv; the fake clone copies this tree's
    install.sh into the destination, so the real ``install.sh --version``, ``--log-start`` and
    ``--list-folders`` run from it."""
    bin_dir.mkdir()
    stubs = {
        "sw_vers": 'echo "15.5"',
        "xcode-select": 'echo "/Library/Developer/CommandLineTools"',
        "git": (
            'if [ "$1" = clone ]; then mkdir -p "$3/.git" "$3/scripts" && : > "$3/.git/HEAD" && '
            f'cp "{SCRIPTS[0]}" "$3/scripts/install.sh" && chmod +x "$3/scripts/install.sh"; '
            'else echo "Already up to date."; fi'
        ),
    }
    for name, body in stubs.items():
        # a Mach-O run by path, not a #! exec (B13 round 23 A97)
        exec_stub.write(bin_dir / name, f'#!/bin/sh\necho "{name} $*" >> "{calls}"\n{body}\n')


@pytest.mark.parametrize("shell", _SHELLS)
def test_readme_step1_command_clones_then_pulls(tmp_path: Path, shell: str) -> None:
    """The exact step 1 line under the user's likely shells, with stub tools: the first run clones and the
    second pulls (the if/else inside braces parses in both); each prints the compat line, appends an attempt
    header naming the agent and the prompt version, and lists the synced folders. Nothing is fetched from the
    network."""
    from agentsync import setup_report  # noqa: PLC0415

    home = tmp_path / "home"
    projects = home / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Projects"
    projects.mkdir(parents=True)
    calls = tmp_path / "calls.log"
    _fake_step1_tools(tmp_path / "bin", calls)
    env = tmp_home_env(home) | {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin"}
    command = STEP1_COMMAND.replace("<agent>", "Test Agent (model-1)")
    for _ in range(2):
        proc = subprocess.run(
            [shell, "-c", command], cwd=home, env=env, capture_output=True, text=True, check=False, timeout=60
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        out = proc.stdout.splitlines()
        assert f"setup-prompt-compat {_installer_compat()}" in out, proc.stdout
        assert any(ln.rstrip().endswith(str(projects)) for ln in out), proc.stdout
    checkout = home / "src" / "agent-context-sync"
    ours = [
        c
        for c in calls.read_text(encoding="utf-8").splitlines()
        if not c.startswith("git ") or c.startswith(("git clone ", f"git -C {checkout} pull"))
    ]
    assert ours == [
        "sw_vers -productVersion",
        "xcode-select -p",
        f"git clone https://github.com/renchris/agent-context-sync.git {checkout}",
        "sw_vers -productVersion",
        "xcode-select -p",
        f"git -C {checkout} pull --ff-only",
    ]
    friction = setup_report.parse_friction((home / FRICTION_LOG).read_text(encoding="utf-8"))
    assert len(friction.attempts) == 2, "each run of step 1 starts a new attempt; the first is kept"
    for attempt in friction.attempts:
        assert attempt.header.get("Prompt") == f"v{_prompt_version()}"
        assert attempt.header.get("Agent") == "Test Agent (model-1)"


@pytest.mark.parametrize("shell", _SHELLS)
def test_a_saved_copy_of_an_older_prompt_is_stopped_at_step_1(tmp_path: Path, shell: str) -> None:
    """Twice a saved v7 copy was pasted again without anyone knowing it was old. Its own check passes (the
    installer's number is "7 or higher"), so the installer is what stops it. ``--log-start``, which a copy
    from before v8 hands no version, logs the attempt as "v7 or older", says to stop and to copy the prompt
    again from the README on the main branch, and exits 2: the command never lists the folders. That copy's
    own text then goes to its report, whose NEXT line says the same, and not to bring the file back. The
    message tells an old copy no way past the stop: its one way on is for a prompt whose first line is the
    installer's version."""
    from agentsync import setup_report  # noqa: PLC0415

    home = tmp_path / "home"
    projects = home / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Projects"
    projects.mkdir(parents=True)
    _fake_step1_tools(tmp_path / "bin", tmp_path / "calls.log")
    env = tmp_home_env(home) | {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin"}
    command = V7_STEP1_COMMAND.replace("<agent>", "Test Agent (model-1)")
    assert "prompt v" not in command
    proc = subprocess.run(
        [shell, "-c", command], cwd=home, env=env, capture_output=True, text=True, check=False, timeout=60
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert f"setup-prompt-compat {PROMPT[0]}" in proc.stdout.splitlines(), (
        "7 or higher: the copy's check passes"
    )
    assert str(projects) not in proc.stdout, "the command stopped before --list-folders"
    source = "README.md on the main branch of https://github.com/renchris/agent-context-sync"
    assert " ".join(proc.stderr.split()) == (
        f"error: the pasted setup prompt is v7 or older and this installer is for setup prompt v{PROMPT[0]}: "
        "the pasted copy is not the current one. Stop here and run no other step of that prompt. Tell the "
        f'person to copy the prompt again from {source} ("Set up on a new Mac: one prompt") and paste it '
        f'into a new session. If the first line of your prompt says "setup prompt v{PROMPT[0]}", the '
        "--log-start value was changed: run step 1's command again once, exactly as the prompt writes it."
    )
    assert f"prompt v{PROMPT[0]}," not in proc.stderr, "the message shows no value that passes"
    [attempt] = setup_report.parse_friction((home / FRICTION_LOG).read_text(encoding="utf-8")).attempts
    assert attempt.header == {"Prompt": "v7 or older", "Agent": "Test Agent (model-1)"}
    assert attempt.version == 7 and attempt.finished, "the stop closes the attempt: no other step may follow"
    event, _closed = attempt.events
    assert (event.step, event.kind, event.fix) == (
        1,
        "error",
        f'copy the prompt again from {source}; or, if the first line of the prompt says "setup prompt '
        f"v{PROMPT[0]}\": run step 1's command again once, exactly as the prompt writes it",
    ), "both readings' remedies (B13 round 9 K18), the cause in the error column only (B13 round 10 L20)"
    assert event.what == (
        f"install.sh --log-start: the pasted setup prompt is v7 or older and this installer is for setup "
        f"prompt v{PROMPT[0]}: the pasted copy is not the current one, or the --log-start value was changed; "
        "setup stopped (exit 2)"
    )
    assert setup_report._PROMPT_STOP_RE.match(event.what), "the report still reads the line as the stop"
    report = subprocess.run(
        [shell, "-c", REPORT_COMMAND],
        cwd=home,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert report.returncode == 0, report.stdout + report.stderr
    last = report.stdout.rstrip().splitlines()[-1]
    assert last.startswith(
        "NEXT: this report is of an attempt started by a copy of the setup prompt that does not match this "
        f"installer, so do not send it or bring it back: copy the prompt again from {source} "
    ), report.stdout


def _intro() -> str:
    """The README prose between the one-prompt heading and its block, whitespace-normalised."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("\n## Set up on a new Mac: one prompt\n", 1)[1]
    return " ".join(section.split("```text\n", 1)[0].split())


def test_readme_intro_says_what_the_prompt_does() -> None:
    """The text above the block matches what the block does (K14): it lists the folders, asks one question,
    runs one install command, then the loop (KISS K04), and the person clicks Allow at most once (the terminal
    in step 1; KISS K11b: no launcher, so no second click), not "one installer command" and "one macOS
    prompt"."""
    intro = _intro()
    for phrase in (
        "lists your synced folders",
        "asks you one question (which to sync)",
        "runs one install command",
        "It starts no background job: background sync is optional and yours to turn on",
        "Then it runs the loop every session runs: `agentsync sync`, then what its `NEXT:` line says",
        "drafted baseline questions for you to confirm",
        "You click Allow at most once: if macOS asks about this terminal app.",
        "redacted setup report",
    ):
        assert phrase in intro, phrase
    assert "one installer command" not in intro and "one macOS prompt" not in intro
    assert "IT request" not in intro, "KISS K04: v7 drafts no IT request"
    steps = _prompt_steps()
    assert "macOS may ask whether this terminal app can access files managed by OneDrive" in steps[1]
    assert "agentsync-launcher" not in steps[2] and "asks for no second Allow click" in steps[2]
    installs = [c for c in _commands(steps[2]) if "scripts/install.sh" in c]
    assert installs == [INSTALL_WITH_FOLDER, INSTALL_SH], "one install command, with a folder or with none"


# ---- step 1's folder list: install.sh --list-folders (judge finding J9) ------------------------------------


_LIST_FOLDERS = f"{INSTALL_SH} --list-folders"


def test_list_folders_is_step_1s_only_listing() -> None:
    step1 = _prompt_steps()[1]
    listings = [s for c in _commands(step1) for s in _split_top(c) if "--list-folders" in s or "ls " in s]
    # B13 round 9 K9, K20, K32: after a reader timeout the list's NEXT offers the same listing once more
    assert listings == [_LIST_FOLDERS, _LIST_FOLDERS], "one command lists the folders (J9, J18)"
    assert "find " not in step1 and "ls -d" not in step1, "no hand-written listing to misread (K1)"
    assert "Allow" in step1 and "NEXT:" in step1, "a denied terminal is named, not read as 'not signed in'"


def _list_folders(home: Path) -> subprocess.CompletedProcess[str]:
    env = tmp_home_env(home) | {"PATH": "/usr/bin:/bin"}
    return subprocess.run(
        ["bash", str(SCRIPTS[0]), "--list-folders"],
        cwd=home,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _written_outside_setup_log(home: Path, before: list[str]) -> list[str]:
    """New paths under ``home`` other than the setup log (--list-folders may log its step there)."""
    setup = home / "agent-context"
    return [p for p in _tree(home) if p not in before and not Path(p).is_relative_to(setup)]


@pytest.mark.parametrize("depth3", [True, False], ids=["with-depth-3", "depth-2-only"])
def test_list_folders_on_fixtures(tmp_path: Path, depth3: bool) -> None:
    """On a fake ~/Library/CloudStorage it prints each library folder (depth 2) and its subfolders (depth 3)
    as full paths, nothing hidden, no file and nothing deeper, and it still lists when no depth-3 folder
    exists (the v3 glob exited 1 there: K1). It installs nothing."""
    cloud = tmp_path / "Library" / "CloudStorage"
    projects = cloud / "OneDrive-Contoso" / "Projects"
    projects.mkdir(parents=True)
    (cloud / "OneDrive-Contoso" / ".hidden").mkdir()
    (cloud / "OneDrive-SharedLibraries-Contoso" / "Team Site - Documents").mkdir(parents=True)
    (cloud / "OneDrive-Contoso" / "notes.txt").write_text("a file, not a folder\n", encoding="utf-8")
    expected = {projects, cloud / "OneDrive-SharedLibraries-Contoso" / "Team Site - Documents"}
    if depth3:
        (projects / "FY26" / "too deep").mkdir(parents=True)
        (projects / ".git").mkdir()
        expected.add(projects / "FY26")
    before = _tree(tmp_path)
    proc = _list_folders(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    root = str(cloud)
    listed = {Path(ln[ln.index(root) :].rstrip()) for ln in proc.stdout.splitlines() if root in ln}
    assert listed == expected, proc.stdout
    assert _written_outside_setup_log(tmp_path, before) == []


def test_list_folders_without_cloud_storage_exits_3_with_a_next_line(tmp_path: Path) -> None:
    """Not signed in: exit 3 and a NEXT: line, which step 1 shows the person (a denied terminal is exit 4)."""
    proc = _list_folders(tmp_path)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert proc.stdout.rstrip().splitlines()[-1].startswith("NEXT:"), proc.stdout
    assert not [ln for ln in proc.stdout.splitlines() if ln.startswith("/")], proc.stdout


def _first_sh_block(text: str) -> str:
    match = re.search(r"^```sh\n(.*?)^```$", text, flags=re.DOTALL | re.MULTILINE)
    assert match, "no ```sh block"
    return match.group(1)


def test_default_install_blocks_never_turn_on_background_sync() -> None:
    """KISS K11b review: the README's Install block and docs/deploy's first block are the default install, so
    none of their install.sh lines passes --confirm-install-agent (background sync is the operator's
    choice)."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    install = readme.split("\n## Install\n", 1)[1].split("\n## ", 1)[0]
    for block in (
        _first_sh_block(install),
        _first_sh_block((DEPLOY / "README.md").read_text(encoding="utf-8")),
    ):
        lines = [ln for ln in block.splitlines() if "install.sh" in ln and not ln.lstrip().startswith("#")]
        assert len(lines) >= 2, block
        assert not [ln for ln in lines if "--confirm-install-agent" in ln], lines


def test_every_launcher_example_passes_confirm_install_agent() -> None:
    """KISS K11b review: `install.sh --launcher PATH` alone is a usage error (exit 2), so every documented
    install.sh line that passes --launcher also passes --confirm-install-agent."""
    pages = [ROOT / "README.md", *DEPLOY_PAGES]
    examples = [
        (page.name, line)
        for page in pages
        for line in page.read_text(encoding="utf-8").splitlines()
        if re.search(r"install\.sh[^`\n]*--launcher ", line)
    ]
    assert examples, "the MDM pack hands the user an install.sh --launcher command"
    assert [e for e in examples if "--confirm-install-agent" not in e[1]] == []


# ---- steps 2 to 4 ----------------------------------------------------------------------------------------


def test_readme_step2_is_the_one_install_command() -> None:
    """One command, in its two forms: with a folder to sync or add, and with none (setup prompt v9: a Mac
    that already syncs folders keeps them, so a re-run names no folder). B13 H8 (B12 F34): its timeout is 30
    minutes, the speech helper's own build limit (20) plus a margin, since a first build took 687 s."""
    step2 = _prompt_steps()[2]
    assert _commands(step2) == [INSTALL_WITH_FOLDER, INSTALL_SH]
    assert "--confirm-install-agent" not in _one_prompt_block(), "KISS K11b: background sync is optional"
    from agentsync.convert import speech  # noqa: PLC0415

    limit = int(speech._BUILD_TIMEOUT_S) // 60
    [suggested] = re.findall(r"\((\d+) minutes if you can set it", step2)
    assert int(suggested) * 60 > speech._BUILD_TIMEOUT_S, (
        "H8: the suggested timeout outlasts the speech helper's own build limit (field: 687 s)"
    )
    assert f"the speech helper's build alone can take up to {limit} minutes" in step2, (
        "H8: step 2 says why the timeout is long, in the build limit's own number"
    )
    assert "run the same command again" in step2, "J11: a safe re-run"
    assert "NEXT:" in step2
    assert (
        "If your tool cannot wait that long in the foreground, run it in the background and read its output"
        " until the NEXT: line appears; that is expected, not a deviation." in step2
    ), "a background run is blessed, so it is not logged as a deviation"
    # B13 round 2 R40: a re-run's reinstall waits for a sync that holds the writer lock and prints "still
    # running" lines; the friction rule ("not in this prompt") and the kind list pulled two ways on it, so one
    # agent might log it and another not. The wait is named, with the installer's own cap, and not logged.
    script = SCRIPTS[0].read_text(encoding="utf-8")
    [wait] = re.findall(
        r'^REINSTALL_WAIT="\$\{AGENTSYNC_REINSTALL_WAIT_SECONDS:-(\d+)\}"', script, re.MULTILINE
    )
    assert int(wait) % 60 == 0
    # B13 round 7 H66: v15 tied the first sync's wait to the reinstall's ("then", "again", "10 more
    # minutes"), while the installer probes the lock before its first sync every time: a re-run at the same
    # commit reinstalls nothing, and a background sync can start during the helper builds. Two sentences now.
    said = (
        "It is safe to re-run: if your tool stopped it early, run the same command again. If another sync is"
        " running when it reinstalls agentsync, it says so and waits for that sync to end, up to"
        f' {int(wait) // 60} minutes, printing "still running" lines, and then goes on even if that sync'
        " has not ended. If another sync is running when its first sync starts (that same one, or one that"
        ' started since, such as a background sync on a Mac that already syncs), it says "another sync is'
        ' still running" and waits for it, up to 10 minutes, and skips that sync ("skipped, lock busy") if it'
        " is still running then. Both waits and that skip are expected, not deviations: do not log them."
    )
    assert said in " ".join(step2.split())
    assert "10 more minutes" not in step2 and "its first sync then" not in step2
    assert "step 2's wait ran out, or one started since" in script, "the installer's probe is unconditional"
    # B13 round 6 G37: after the reinstall's wait runs out the first sync waits for the same lock, as an
    # interactive sync does, and is skipped (75) if it is still held then. Only agentsync's WARNING lines said
    # so, and the prompt named neither, so its friction rule had the agent log them. The installer now says it
    # in its own line, the one the prompt quotes, and the cap is agentsync's own.
    wait_line = "another sync is still running; waiting for it to end, at most 10 minutes"
    assert f'say "$SYNC_LABEL: {wait_line}"' in script
    # B13 round 7 H31: agentsync's own stderr line names the holder, so the step's line says no more
    assert 'say "$SYNC_LABEL: skipped, lock busy"' in script
    assert "75) printf 'lock busy: another agentsync cycle is running' ;;" in script
    from agentsync import cycle  # noqa: PLC0415

    assert cycle.INTERACTIVE_LOCK_WAIT_S == 10 * 60
    assert "agentsync: reinstalling after the other sync ends" in script
    assert 'say "$1, still running, $((SECONDS - t0))s"' in script, "the ticker's words"
    assert "reinstalling without waiting longer" in script, "and it goes on when the wait runs out"


def test_readme_step3_runs_the_loop_then_the_report() -> None:
    """KISS K04: step 3 is the loop every session runs (sync, then what NEXT says, until NEXT itself says
    "session done"; a WAITING ON YOU line is shown, never a stop), then the report, and the finish. The words
    it keys on are the loop's own: NEXT's prefix and its "session done", and the WAITING prefix. Each sync
    gets step 2's long timeout, a non-zero exit with a NEXT line is followed, and the agent is told where the
    procedure a NEXT line names is written out. No IT request and no second prompt. B13 H3: the finish named
    "the last NEXT: or WAITING ON YOU: line of the loop", and v10 rehearsal agents read it two ways (the wait
    alone, or the NEXT with the waits); it now names the last NEXT: line printed before the report (the
    report's own NEXT excluded), or "none", with every wait the same command printed under it. B13 H4: a
    re-run's installer NEXT can already say "session done" (loop rules 5, 9 and 10) after step 2's sync,
    which downloads nothing, so it does not count toward the loop."""
    from agentsync import loop  # noqa: PLC0415

    block = _one_prompt_block()
    steps = _prompt_steps()
    step3 = steps[3]
    assert _commands(step3) == [*LOOP_COMMANDS, SYNC_NO_DOWNLOAD, REPORT_COMMAND]
    assert f"Run `{AGENTSYNC} sync` and do what its NEXT: line says" in step3
    flat = " ".join(step3.split())
    assert 'Repeat until the NEXT: line itself says "session done".' in flat
    assert (
        "Step 2's NEXT: line does not count, even when it already said \"session done\": step 2's sync"
        " downloaded nothing, so run this loop's sync at least once." in flat
    ), "a re-run's installer NEXT can say 'session done' (rules 5, 9, 10) after a sync that downloads nothing"
    assert "WAITING ON YOU: lines are mine: show them to me, but keep doing what NEXT: says." in flat
    assert (
        "Run each sync like step 2's command: with the longest command timeout your tool allows, or in the"
        " background, reading its output until the NEXT: line appears." in flat
    ), "the first downloading sync and a held listing (120 s) outlast a default tool timeout"
    assert "if it seems stuck, a macOS prompt may be waiting for me" in flat
    assert (
        "sync and curate print their NEXT: line even when they exit non-zero (curate exits 1 while it lists"
        " errors for you to fix): follow it, and go to the report only when no NEXT: line was printed."
        in flat
    )
    assert (
        'The steps a NEXT: line refers to (the agentsync-docs skill, its "Baseline questions" section and the'
        " page rules) are written out in ~/agent-context/docs/AGENTS.md (CLAUDE.md for Claude Code)" in flat
    )
    from agentsync import publish  # noqa: PLC0415

    guide = publish.root_guide()
    assert '"Baseline questions" section' in loop._BASELINE, "rule 4's NEXT names the section"
    assert "Baseline questions (the agentsync-docs skill's section of that name" in guide, (
        "the root CLAUDE.md/AGENTS.md the prompt points at carries that section"
    )
    # B13 round 14 Q2 (lane B's words, Q1, Q9, Q33): v22 said status prints "the same NEXT: line", but with a
    # check that FAILs status's NEXT is rule 1's, which names that check, where a sync's goes on to the
    # baseline (test_status_next_differs_from_a_syncs_only_on_a_failing_check)
    assert STATUS_NEXT in flat and "status` prints the same NEXT: line without syncing." not in flat
    loop_source = Path(loop.__file__).read_text(encoding="utf-8")
    assert loop.NEXT_PREFIX == "NEXT: " and loop.WAIT_PREFIX == "WAITING ON YOU: "
    assert loop_source.count("session done") >= 3, (
        "the loop's NEXT says 'session done' where the prompt stops"
    )
    finish = step3.split("Finish with:", 1)[1]
    # B13 round 2 R11, R30: "the folders synced" read two ways on a Mac whose sources.toml also lists
    # folders the person added as inbox sources, and had no words for a session that synced nothing. It is
    # now the folders the installer counts (live kind = "local" sources), and one "none" form.
    # B13 round 7 H9, H30 (one defect, built once): v15 built the list from step 1's [synced] marks and
    # step 2's additions, so a list that could mark nothing (an installed agentsync too old for the reader,
    # its 10 s timeout, a config it cannot load) with step 2 keeping the folders gave a false "none". The
    # list is now sources.toml's, every time; "added" is per folder, by add-source's own "added source" line,
    # since step 2's folders: line gives counts only. Not by the absence of its "already configured:" line:
    # a re-run of step 2 prints that for every folder the first run added (round 7 lane P review).
    # B13 round 7 H6: at the folder wait no report runs, so "before the report" had nothing to point at, and
    # the folder list's NEXT: line (an install.sh command with the folder left open) read as the answer.
    # B13 round 8 J6: at that wait, and at any stop before step 2, sources.toml does not exist yet (step 1's
    # --list-folders installs nothing), so "read sources.toml" named a missing file; the "none" form says so.
    assert (
        'the folders this Mac syncs, as full paths: the kind = "local" sources in'
        " ~/agent-context/sources.toml that are not paused or retired, read after step 2 or, if setup stopped"
        " before step 2, as they are"
        ' (never the inbox or another kind = "inbox" folder), saying which of them this session added: those'
        ' for which a run of step 2 this session printed an "added source" line; or, if'
        ' ~/agent-context/sources.toml does not exist yet or has no such kind = "local" source, "none: this'
        ' Mac syncs no folder" (if setup stopped before step 2, say'
        " at which step);"
        f" {UNCHECKED_CONFIG}; {UNLOADED_CONFIG} {UNKNOWN_FIRST}{REFUSED_CONFIG}"
        ' the last NEXT: line printed before the report, or "none" (also "none: waiting for your folder'
        ' answer" while you wait for my folder answer in step 1: there, the NEXT: line that leaves the'
        " folders to choose is not a step for me), and under it each WAITING ON YOU: line the same command"
        " printed;"
    ) in " ".join(finish.split()), (
        "one NEXT line, the report's own excluded, and every wait the same command printed"
    )
    assert "marked [synced] and those step 2 added" not in finish
    # B13 round 11 M28: a sources.toml that exists but does not load (mode 000, bad TOML, a schema error) had
    # no words: "read after step 2" could not be done, or parsed a file agentsync rejected, and the only other
    # case said "none: this Mac syncs no folder", false on a Mac that synced 2 folders until its config broke
    # (probe H). The case keys on "does not load", and the key the report prints for it is its own.
    from agentsync import setup_report  # noqa: PLC0415

    assert 'out.append(f"- loads: no: {r.config_error}")' in Path(setup_report.__file__).read_text(
        encoding="utf-8"
    ), "the report's words the finish keys on"
    # B13 round 13 O44: "loads: not checked" starts with "loads: no", so the failing case keys on the colon
    # form, which neither the shell report's line nor setup_report's "loads: pending:" holds
    assert 'says "loads: no",' not in " ".join(finish.split())
    assert 'say "- loads: not checked ($why)"' in SCRIPTS[0].read_text(encoding="utf-8")
    assert not "- loads: not checked".startswith("- loads: no:") and "loads: no:" not in "- loads: pending: x"
    from agentsync import cli  # noqa: PLC0415

    assert 'f"added source {sid!r} to {config.config_path}{note}:"' in Path(cli.__file__).read_text(
        encoding="utf-8"
    ), "the words the finish keys on are add-source's own, printed only for a folder it writes"
    assert '</dev/null 2>"$ERRF" | add_source_filter "$nth"' in SCRIPTS[0].read_text(encoding="utf-8"), (
        "step 2 passes add-source's lines on (the filter drops only its summary lines)"
    )
    assert "the folders synced (full paths)" not in finish
    script = SCRIPTS[0].read_text(encoding="utf-8")
    assert 's.kind.value == "local" and s.state.value == "live"' in script, "the folders the installer counts"
    assert "of the loop" not in finish and "three lines" not in step3, "no candidate set, no line count"
    assert "it-request" not in block and "IT request" not in block, "KISS K04: no IT request step"
    assert "--confirm-install-agent" not in block
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("\n## Set up on a new Mac: one prompt\n", 1)[1].split("\n## ", 1)[0]
    assert section.count("```text\n") == 1 and "Next, on the same Mac" not in readme, "no second prompt"


ALARM_SENTENCE = (
    'That "click Allow" appears on an alarm: line, indented under a source; any other alarm: line is'
    " agentsync's own account of that source, not a step for you: show it to me, but do not do what it"
    " suggests (that is mine to decide) and do not log it."
)
"""Step 3's sentence on a sync's indented ``alarm:`` line (setup prompt v11, B13 H7)."""


def test_readme_step3_says_an_alarm_line_is_not_a_step(capsys: pytest.CaptureFixture[str]) -> None:
    """B10 rehearsal, B11 held, B13 H7: a sync prints an indented ``alarm:`` line under a source, and the v10
    prompt named only NEXT:, WAITING ON YOU: and note: lines, so its friction rule had the agent log every
    alarm, and an alarm's advice (Files and Folders access, accept-deletions, an install re-run) read as a
    step. One alarm is a step: a listing macOS holds says "click Allow, then re-run the sync"
    (``arm_local.LocalArm._held``, the cycle's held cloud; tests/test_cycle.py), which the sentence before
    already handles, so this one excepts it. Most alarms have no wait line, so the sentence claims none."""
    from agentsync import cli  # noqa: PLC0415
    from agentsync.model import CycleMode, CycleReport, PassKind, SourceKind, SourceReport  # noqa: PLC0415

    step3 = _prompt_steps()[3]
    assert ALARM_SENTENCE in step3
    assert (
        step3.index("WAITING ON YOU: lines are mine")
        < step3.index('If a sync stops on "click Allow"')
        < step3.index(ALARM_SENTENCE)
        < step3.index("make one report enough")
    ), "after the click-Allow sentence it excepts, inside the loop, before the report syncs"
    assert _commands(ALARM_SENTENCE) == [], "it names no command"
    src = SourceReport("contoso-projects", SourceKind.LOCAL, PassKind.FULL, False, alarms=("example",))
    cli._print_report(CycleReport(run_id=1, mode=CycleMode.POLL, sources=(src,), changes=(), commit_sha=None))
    lines = capsys.readouterr().out.splitlines()
    assert lines[1].startswith("  contoso-projects: ") and lines[2] == "    alarm: example", (
        "a sync prints the alarm indented under its source, as the prompt says"
    )


def test_readme_step3_syncs_again_while_the_tool_says_so_before_the_report() -> None:
    """A bring-back file written right after the first sync on an upgraded Mac showed a re-read that had
    only begun, and the next question took another round. Step 3 now keeps syncing before the report, and
    leans on sync's own line for when to stop: the note that starts ``loop.SYNC_AGAIN``, which the tool
    prints only while another sync reads more (tests/test_loop.py). The cap is the cycle's two budgets: 12
    syncs at 2 minutes of re-reads and 3 of OCR are an hour of that work at most.

    These syncs download nothing. The plain sync downloads up to 1 GiB a folder every time, and the files
    it brings have the OCR time first: twelve more of them were up to 12 GiB a folder that step 1 never
    told the person about, and on a folder of scans they read nothing again. The first one runs whatever
    the last note said: a file the last sync downloaded is counted only by the next listing, and a note
    that says downloads took the OCR time does not say "sync again". The count is of these syncs alone, so
    the loop's own syncs before "session done" do not use it up. And the one command it runs is named,
    so "no command a WAITING line names" cannot be read as forbidding the sync itself."""
    import argparse  # noqa: PLC0415

    from agentsync import cli, cycle, loop  # noqa: PLC0415

    step3 = _prompt_steps()[3]
    said = (
        'Once this loop\'s NEXT: line has said "session done", make one report enough: run '
        f"`{SYNC_NO_DOWNLOAD}` (a sync that downloads nothing, so the files already on this Mac "
        "get its whole time), and run it again while a note: line of the last one starts with "
        '"sync again:" (files are still being read again; the note counts them) or its NEXT: line asks '
        "only for another sync. Run it up to 12 times in all; the syncs before "
        '"session done" are not counted. Run no other command for this: never purge, accept-deletions or '
        "offboard, and nothing else a WAITING ON YOU: line names."
    )
    assert said in step3
    assert f'"{loop.SYNC_AGAIN.strip()}"' in said and loop.NOTE_PREFIX.strip() in said
    assert 12 * (cycle._REREAD_BUDGET_S + cycle._OCR_BUDGET_S) == 3600, "an hour of re-reads and OCR"
    order = [
        'Repeat until the NEXT: line itself says "session done".',
        'Once this loop\'s NEXT: line has said "session done", make one report enough',
        'add a "## Not used" section',
        "Then the report, even after a failure",
    ]
    assert [step3.index(part) for part in order] == sorted(step3.index(part) for part in order)
    assert _commands(said) == [SYNC_NO_DOWNLOAD], "the one command it runs, and it names no other"
    assert "more times" not in step3 and "no command a WAITING" not in step3
    # The option is one sync has, though its help hides it, and 0 is what the installer's first sync passes.
    args = cli.build_parser().parse_args(SYNC_NO_DOWNLOAD.split()[1:])
    assert (args.command, args.materialise_budget) == ("sync", "0")
    sub = next(a for a in cli.build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    [option] = [a for a in sub.choices["sync"]._actions if "--materialise-budget" in a.option_strings]
    assert option.help is argparse.SUPPRESS, "KISS K13b: sync shows no option"
    assert "FIRST_SYNC=(sync --once --materialise-budget 0)" in SCRIPTS[0].read_text(encoding="utf-8")
    step1 = _prompt_steps()[1]
    assert "online-only files in them are downloaded by each sync, up to 1 GiB per folder per run" in step1, (
        "what step 1 tells the person is of the loop's own syncs; these add no download to it"
    )
    intro = _intro()
    assert "`agentsync sync --materialise-budget 0`: they download nothing" in intro
    assert "syncs once more, and again while `sync` says files are still being read again, up to 12" in intro
    assert "`sync --help` does not list the option" in intro


def test_readme_report_step_after_any_failure() -> None:
    """Every failure path goes to step 3's report, which runs even after a failure, before the code exists (it
    says what to tell the person then), and ends the prompt."""
    steps = _prompt_steps()
    report = _report_step()
    text = steps[report]
    assert text.startswith("Sync loop and report.")
    # B13 round 6 G25: "always" was followed at once by three cases with no report.
    assert (
        "Then the report, even after a failure (except in the four cases below); this is the last command "
        "you run:" in " ".join(text.split())
    )
    assert "Then the report, always" not in text
    # B13 round 2 R31: five places say "go to step 3's report", and step 3 used "the report" for its last
    # command alone, with the "## Not used" section before it. The term is defined once, where step 3 uses
    # it, as both parts in order; the section is skipped where its folder does not exist yet (an Xcode stop
    # on a new Mac, which has no report either).
    flat = " ".join(text.split())
    defined = (
        f"Step {report}'s report, where every \"go to step {report}'s report\" in this prompt lands, has two "
        'parts, in this order. First, add a "## Not used" section to the end of '
        "~/agent-context/setup/fix-request.md"
    )
    assert defined in flat
    # B13 round 5 F27: the skip read "if ~/agent-context/setup does not exist", which step 1's own --log or
    # --log-start creates on every path with a checkout, so it fired only when the checkout was missing too.
    # The checkout is now the one condition for the section, the report and the heading rule.
    # B13 round 8 J19: a folder at the checkout's path that is not a checkout has no installer, so the
    # condition is the installer, not the folder.
    assert flat.index(defined) < flat.index(
        "skip this part if ~/src/agent-context-sync/scripts/install.sh does not exist. Then the"
    )
    assert "~/agent-context/setup does not exist" not in flat
    assert flat.index("skip this part if") < flat.index("Then the report, even after a failure")
    block = " ".join(_one_prompt_block().split())
    assert block.count(f"go to step {report}'s report") >= 4, "the jump sites the definition names"
    # Two stops have no report (v10): step 1's folder question on a new Mac is a wait, not a failure, and a
    # copy that is not the installer's runs no other step. Every other stop of step 1 is a failure. Since v11
    # the second case names both of step 1's version checks, and the first as the line --version printed: at
    # an Xcode or git stop --version never ran, and those stops end with the report (B13 H2).
    # B13 round 5 F25: "Two cases have no report ... Every other stop still ends with the report" came right
    # after a parenthesis that ruled out a report when the checkout is missing, so the two contradicted each
    # other at a failed clone. The missing checkout is the third case, stated once, in the list.
    assert (
        '--report-only` Four cases have no report and no "## Not used" section: while you wait for my '
        "folder answer in step 1 (also when step 2 says no folder is synced yet, as step 1 says); when step "
        "1 stops (after at most that one rerun) because this prompt is not the installer's (either check: "
        "the --version line does not end with this prompt's number, or the command says so); at step 1's "
        "not-a-checkout, worktree and ownership stops, and at its Xcode and other-git-failure stops when the "
        "installer there is not this prompt's version (step 1 says what to tell me); and when "
        "~/src/agent-context-sync/scripts/install.sh does not exist (tell me "
        "instead that setup stopped before the code was downloaded). Every other stop still ends with the "
        "report. The report works out"
    ) in flat
    assert "Two cases have no report" not in flat
    # B13 round 11 M74: "as step 1's last sentence says" pointed at the add-source sentence; the rule is
    # earlier
    assert "step 1's last sentence says" not in flat
    assert flat.count("~/src/agent-context-sync/scripts/install.sh does not exist") == 2, (
        "the one condition, once per part"
    )
    assert "~/src/agent-context-sync does not exist" not in flat
    assert (
        "or the command says the pasted prompt is not the installer's, stop and run no other step: this "
        f"prompt is v{PROMPT[0]}" in " ".join(steps[1].split())
    )
    # two since v19: a new Mac's folder question, and the same wait after step 2 finds no folder (L10)
    assert _prompt_steps()[1].count("stop and wait for my answer") == 2
    assert "Do not send or upload anything" in text
    assert "outcome" in text and "run type" in text, "the report computes them (J3, J14)"
    # B13 round 6 G47/G65 (lane RB, prompt words in v15): a report that holds a "Private only:" line gets no
    # issue link line, and its NEXT names bring-back.md alone.
    assert (
        "its last lines are an issue link (none when the report says it is for a private copy only) and a "
        "NEXT: line" in " ".join(text.split())
    )
    assert len(steps) == 3 and report == len(steps), "the report ends the last step"
    elsewhere = _preamble() + " ".join(t for n, t in steps.items() if n != report)
    targets = set(re.findall(r"go to step (\d+)'s report", elsewhere))
    assert targets == {str(report)}, f"every failure path goes to step {report}'s report: {targets}"
    assert len(re.findall(r"go to step", elsewhere)) == len(re.findall(r"go to step \d+'s report", elsewhere))


def test_readme_report_is_the_last_command() -> None:
    """Nothing is logged after the report (J4): the report is the block's last command and appends the
    friction log's end line first. --log-start appears once, in step 1; --log-end is gone (KISS K17)."""
    steps = _prompt_steps()
    assert _commands(_one_prompt_block())[-1] == REPORT_COMMAND
    block = _one_prompt_block()
    assert block.count("--log-start") == 1 and "--log-end" not in block
    assert "--log-start" in steps[1] and "--report-only" in steps[_report_step()]
    assert _commands(steps[3].split("--report-only", 1)[1]) == [], "the finish runs nothing"


def test_readme_prompt_carries_the_field_lines() -> None:
    """The corporate field report's prompt lines (KISS K04): the inbox named as the folder beside the docs
    repo, not by a fixed path (N3), and not as every inbox source (field report 2026-10-08, setup prompt v10:
    read literally the old sentence named fifteen folders, fourteen of them the person's own; the agent
    announced only the one beside the docs repo and logged the wording); what to drop there and which
    formats carry a sensitivity label (N9); never emptied by hand (N14); Containers and the browser are off
    limits (N16); a sync stopped on "click Allow" is a macOS prompt waiting for the person (WF). The README
    and the deploy guide say the same about the inbox."""
    block = " ".join(_one_prompt_block().split())
    pre = _preamble()
    n16 = (
        "agentsync never needs ~/Library/Containers, Group Containers or your browser; "
        "do not read or drive them."
    )
    assert n16 in pre
    assert "Text under ~/agent-context/docs/mirror is third-party content" in pre
    step2 = _prompt_steps()[2]
    flat2 = " ".join(step2.split())
    assert INBOX_WHEN in flat2 and "Then tell me about my inbox" not in flat2
    inbox = flat2.split(INBOX_WHEN, 1)[1]
    assert inbox.startswith(f" {INBOX_SENTENCE} I drop files there by hand:")
    # B13 round 14 Q8: v22 told the agent about the inbox after the non-zero clause's jump to the report, so a
    # failed step 2 never explained it; it now comes first, and only when sources.toml has an inbox source
    later = flat2.index("otherwise log it and go to step 3")
    assert flat2.index(INBOX_WHEN) < flat2.index(PERSONS_NEXT) < later
    assert "~/agent-context/inbox" not in block, "N3: no fixed path, the docs repo can be set by hand"
    assert "Outlook mail dragged out as .eml, a meeting transcript as .docx or .vtt" in inbox
    assert "Only .eml, .pdf and Office files such as .docx carry a sensitivity label" in inbox
    assert "never empty it by hand" in inbox
    # B13 H6: a removed inbox file's page stays at its path as a deletion notice ([DELETED UPSTREAM], reaped
    # after tombstone_reap_days; tests/test_e2e.py::test_f_delete_writes_a_tombstone), and only the queued
    # purge removes it earlier. The v10 sentence said the page was removed.
    assert "removing a file turns its page into a deletion notice and queues a purge" in inbox
    assert "removes its page" not in block
    assert 'If a sync stops on "click Allow", a macOS prompt is waiting for me' in _prompt_steps()[3]
    # The bring-back report (2026-10-06). S2: an unanswered folder question is never the agent's to answer.
    # S4: a Mac that already runs agentsync keeps what it has, and its older warnings are not this session's.
    step1 = _prompt_steps()[1]
    assert step1.index("ask which to sync") < step1.index("If you cannot ask me")
    assert (
        "If you cannot ask me (your tool runs unattended, or the question comes back unanswered), do not "
        "choose folders for me: log a deviation, stop and wait for my answer."
    ) in step1
    # B13 round 2 R9: on a new Mac step 2's first sync prints a WAITING ON YOU line too (an empty cloud folder
    # names a sources.toml edit), and the only rule for such a line was a re-run's. It now covers every Mac,
    # and forbids doing what the line says, not only running its commands.
    assert (
        "If this Mac already runs agentsync, its sources, history and background jobs are kept. Any [warn] "
        "or WAITING ON YOU: line this command prints is mine (on such a Mac it may predate this session): "
        "show it to me and do not do what it says, neither its commands nor its sources.toml edits."
    ) in " ".join(step2.split())
    assert step2.index("run the same command again") < step2.index("If this Mac already runs agentsync")
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    # Field report 2026-10-08: the drop folder is the one inbox beside the docs repo, not every inbox-kind
    # source, and a sync does not create it (config.ensure_inbox runs for init and add-source only).
    other = 'ny other `kind = "inbox"` source %s is a folder you added'
    assert "**The inbox always exists.** `install.sh` keeps a drop folder" in readme
    assert "the `inbox` folder beside the docs repo (`~/agent-context/inbox` by default)" in readme
    assert other % "there" in readme
    assert "the inbox folders are" not in readme and "every sync keep" not in readme
    assert "keeps the inbox" not in readme, "a sync does not recreate an inbox folder removed by hand"
    assert "never empty it by hand" in readme and "carry a sensitivity label" in readme
    assert readme.count("install-skill") == 1, "every sync writes the skill; only a 2026-10-01 note names it"
    deploy = " ".join((DEPLOY / "README.md").read_text(encoding="utf-8").split())
    day1 = deploy.split("**Manual inbox.**", 1)[1].split("- **Check it:**", 1)[0]
    for phrase in (".vtt", ".teams.json", "carry a sensitivity label", "never empty it by hand"):
        assert phrase in day1, phrase
    assert "on the `inbox` folder beside the docs repo (`~/agent-context/inbox` by default)" in day1
    assert "That folder is the drop folder; a" + other % "in `sources.toml`" in day1
    assert "the inbox folders are" not in day1 and "every sync keep" not in day1
    from agentsync.model import TEAMS_MONTH_SCHEMA  # noqa: PLC0415

    teams = f"Teams messages go in as `.teams.json` files in the `{TEAMS_MONTH_SCHEMA}` shape"
    contract = "inbox writer contract](%sdesign/CONTRACTS.md#11-local-arm-and-hydration)"
    assert teams in readme and contract % "docs/" in readme, "N9: a Teams route, not only the label caveat"
    assert teams in day1 and contract % "../" in day1
    contracts = (ROOT / "docs" / "design" / "CONTRACTS.md").read_text(encoding="utf-8")
    assert "\n## 11. Local arm and hydration\n" in contracts and "**Inbox writer contract" in contracts


INBOX_SENTENCE = (
    'it is the folder named inbox beside the docs repo, one of the kind = "inbox" sources in '
    '~/agent-context/sources.toml; any other kind = "inbox" source there is a folder I added myself, not my '
    "inbox."
)
"""What setup prompt v10's step 2 tells the agent to say the inbox is."""

INBOX_WHEN = (
    "When it ends, whether or not it exited non-zero, unless its NEXT: line says the config does not load, if"
    ' ~/agent-context/sources.toml has a kind = "inbox" source, tell me about my inbox:'
)
"""When step 2 tells the agent to explain the inbox, since v23 (B13 round 14 Q8): before the non-zero clause,
whose "go to step 3's report" v22's "Then tell me about my inbox" came after, and only once the install wrote
sources.toml (an install that failed before it has no inbox to describe), and not after a configuration
error, whose file the finish says not to parse (B13 round 14 Q8 review). Since v24 that is install.sh's
config_failed NEXT, "does not load", not every "configuration error" (B13 round 15 S55): the first sync's
refusal prints one for a config that loads and whose inbox is real
(:func:`test_a_first_sync_refusal_is_no_config_that_does_not_load`)."""


def test_the_inbox_the_prompt_names_is_the_one_agentsync_keeps(sample_config: Any, tmp_path: Path) -> None:
    """The prompt's sentence is ``config.ensure_inbox``'s rule in words, and nothing else ties the two. On a
    Mac whose config already has an inbox source the person added, agentsync keeps one more: a folder named
    ``inbox`` beside the docs repo, a ``kind = "inbox"`` source like the other, and the only one of them it
    made."""
    from agentsync import config as cfg  # noqa: PLC0415
    from agentsync.model import SourceKind  # noqa: PLC0415

    assert INBOX_SENTENCE in _prompt_steps()[2]
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    cfg.append_to_config(sample_config.config_path, cfg.inbox_source_table("bridge", bridge))
    config, added = cfg.ensure_inbox(sample_config.config_path)
    assert added is not None and added.path is not None and added.kind is SourceKind.INBOX
    assert added.path.name == "inbox" and added.path.parent == sample_config.docs_repo.parent
    inboxes = {s.id: s.path for s in config.sources if s.kind is SourceKind.INBOX}
    assert inboxes == {"bridge": bridge, added.id: added.path}, "one of the inbox sources, not each of them"


ALREADY_SYNCED = "already synced on this Mac:"
"""How ``install.sh --list-folders`` starts its first line on a Mac whose config already syncs folders: the
words setup prompt v9's step 1 keys on."""

SIGNED_OUT = (
    "If the list's NEXT: line asks me to sign in and then re-run, tell me which folders are kept and that I"
    " must sign in to the app that line names, and wait for me as for a click; once I say so, run"
    " --list-folders again and go by what it prints."
)
"""Step 1's first exception, since v20, for a list whose synced folders are missing because OneDrive is signed
out (B13 round 11 M4, lane RB: ``--list-folders`` exits 3 and its NEXT: asks for the sign-in). Since v21 it
names no app: the NEXT: line names the one that is signed out (B13 round 12 N61, N41, lane RB's words). Since
v23 it keys on "sign in and then re-run", the signed-out lines' shape: the other-account tail's "(sign in to
that app with the account it belongs to, ...)" asks for a sign-in too, with no re-run, and is
:data:`GONE_FOLDER`'s (B13 round 14 Q15, Q73 review)."""

SIGNED_OUT_UNASKED = (
    "If you cannot ask me (your tool runs unattended, or the question comes back unanswered), do not wait and"
    " log nothing for it: go to step 3's report."
)
"""What the first exception's sign-in wait does with nobody to ask, since v21 (B13 round 12 N32, N6: one
defect). v20 had no rule, so an unattended agent waited forever or improvised. To the report, not to step 3's
no-report cases (N6's verifier): the report records the stop and its doctor line says to sign in, where a
no-report stop leaves the person no bring-back.md; and it is what a Mac that syncs no folder yet does with the
same sign-in (N7). Nothing is logged for it: the report reads the stop from install.log."""

NEW_MAC_SIGN_IN = (
    "If --list-folders printed no folder paths (only a NEXT: line), do what that line says if it is a click"
    " for me (a sign-in is not one: only the first exception below waits for a sign-in), otherwise show it to"
    " me and go to step 3's report."
)
"""Step 1's rule for a list with no folder path, since v21 (B13 round 12 N7). v20 waited for a sign-in "as
for a click" on a Mac that already syncs folders, and left open whether the same sign-in NEXT: on a Mac that
syncs none is "a click for me": read as one, the agent waited, logged a click and the report said "worked
with help". The report (``_LIST_STOPS``) and the guide already send that stop to the report; the prompt now
says so."""


GONE_FOLDER = (
    "If that NEXT: line says any of them is no longer on disk or is not on disk, tell me which, that step"
    " 2 fails on each until it is fixed, and the fix: for a folder that line says was renamed, moved or"
    " deleted, put it back under its name, sync it to this Mac in its app, or fix its [[source]] path;"
    " otherwise the fix that line names in parentheses (a sign-in with the account it belongs to, which is"
    " no sign-in to wait for, or Sync on its SharePoint page, or the [[source]] path). Do not add another"
    " folder in its place unless I name it."
)
"""The first exception's words, since v22, for the list's tail on a synced folder renamed or moved in a
signed-in app (B13 round 12 N41, N61, lane RB: "1 of them is no longer on disk although its app is signed
in"). v21 had none, and listed the renamed folder unmarked for the add question, so the person could be
offered their own folder under its new name as a new source (B13 round 13 O30). Since v23 it keys on "on
disk", which the list's other two tails also say ("is not on disk because" a provider folder of another
account, or a SharePoint library not synced here), and gives each tail's own fix: v22's "put it back under its
name" is wrong for a library that was never synced (B13 round 14 Q15, Q73: one defect). Since v25 the renamed
branch also syncs the folder to this Mac in its app: a folder unticked in a signed-in app's folder choice is
gone from disk too, and doctor's fix and the wait name that (B13 round 16 T72)."""

KEPT_UNASKED = (
    "Whether or not that line names such a folder, if you cannot ask me whether to add one, add none and go"
    " on to step 2, and still tell me in your final message any folder that line says is not on disk and its"
    " fix."
)
"""The first exception's no-answer rule, since v28. v27's "If you cannot ask me then, add none and go on to
step 2" came right after :data:`GONE_FOLDER`, so "then" could bind it to that sentence, and a Mac whose kept
folders are all on disk would have no such rule and fall back to the new Mac's: a logged deviation and a stop
(B13 round 19 W28). Kept in place, not moved before the gone-folder sentence: there an unattended agent could
go on to step 2 before it told the person which folders are off disk; the final message keeps them (W28's
verifier)."""

UNREAD_PATCH_TOLD = (
    "if the report's NEXT: line says bring-back.md does not hold a kept patch, name"
    " ~/agent-context/bring-back.md and then tell me, in that line's words, how many kept patch files in"
    " ~/agent-context/setup/local-work this account cannot read, that I should make them readable (chmod"
    " u+r) and run ~/src/agent-context-sync/scripts/install.sh --report-only again before I copy the file"
    " back; name no patch file (a patch's file name carries its commit's subject); do not change those files"
    " or run the report again yourself;"
)
"""The finish's words, since v26, for a report whose bring-back.md lacks a kept patch it could not read (B13
round 17 U29): the person makes it readable and runs the report; the agent neither, since the report is the
last command it runs. v26 asked which file, and no line names one, by design: a patch's file name carries
its commit's subject; since v27 the count, the chmod and the report's command, no file (B13 round 18 V12,
V17, V32, V79)."""

CUT_OFF_TOLD = (
    "whenever the report's NEXT: line says part of the report was cut off or not measured, tell me so in that"
    " line's words (its Summary says which, and why), and that if this Mac was busy I may run"
    " ~/src/agent-context-sync/scripts/install.sh --report-only again once it is idle, before I copy the file"
    " back; do not run it again yourself;"
)
"""The finish's words, since v29, for a report part of which was cut off or not measured (B13 round 20 X3):
round 19's W3 put that advice into the report's NEXT, but the finish relayed the report's NEXT only for the
could-not-write and unreadable-patch cases, and the report is the last command the agent runs. Its own clause,
after the patch case's "do not ... run the report again yourself", so it is not read as part of that case;
advice only, since a doctor that is always slow must not hold the bring-back or loop the agent (W3's reason).
The words it keys on are lane RB's X37 opening of the --report-only NEXT, which says "cut off or not measured"
since a busy state file also triggers it."""

MIRRORED_NO_BLOCK = (
    "If the list's NEXT: line says a folder or source is still mirrored but has no [[source]] block, tell"
    " me what it says (which folder, or how many sources) and the fix it names, and add that folder again"
    " only if I name it."
)
"""Step 1's words, since v25, for the list's tail on a source the manifest still mirrors whose [[source]]
block is gone (B13 round 16 T6, T30): v24 had none, and the list said "(none is needed)" while step 2's
status FAILed on it. Before the two exceptions, so both lists' tails reach it; the re-add stays the
person's to choose, since deleting a block may have been a deliberate stop."""

CONFIG_STOP_TOLD = (
    "If it says step 2 stops on something, tell me what it names and whose fix it says that is (mine, or the"
    " agent's: then it is yours once step 2 stops on it), and still go on to step 2."
)
"""Step 1's words, since v26, for the list's tail on a config step 2's config step will stop on: a policy.toml
or [governance] value that does not load, or a state file that cannot be read (B13 round 17 U12). v25 had
none, and the list said "(none is needed)" over it. Step 2 still runs: it stops at its config step with a
NEXT that names the stop as the person's. Since v27 also at its status or first sync: a damaged table page
passes the config step, and step 1's list now says so (B13 round 18 V21). Since v29 every tail names the
config step (a damaged table or index root, round 20 X72), and a state file this account cannot open is a
chmod the agent runs when step 2 stops on it, not the person's (round 20 X9, X26, X29): the words say whose
the tail says it is, not "mine" for every stop."""

PERSONS_NEXT = (
    "If it exits non-zero and its NEXT: line says it is not a step for you (the [FAIL] lines, the"
    " configuration error or a status error with no [FAIL] line are mine to fix), log it as a step 2 error,"
    " show me those lines and go to step 3's report; do not run the re-run it names. Otherwise, if it exits"
    " non-zero, do what NEXT: says only if"
)
"""Step 2's rule, since v23, for the NEXT: lines install.sh ends a person's stop with (B13 round 14 Q7): the
[FAIL] lines that are all the person's (``PERSONS_FAILS``) and a config that does not load. Each says "not a
step for the agent" and ends with the re-run once fixed, so v22's "do what NEXT: says only if it is an
install.sh ... command; otherwise log it" read both ways, and with no line logged the report's Items read
"none" beside "failed at step 2". Not every non-zero exit: a held listing's Allow click and the folder stop's
exit 2 are no errors. Since v24 the bracket also names a status that exits non-zero with no [FAIL] line (B13
round 15 lane RB's words for its S33, S58, S62), whose NEXT says the same "not a step for the agent"."""

FAIL_IS_MINE = (
    "A [FAIL] line whose fix starts with an edit to sources.toml (a [[source]] block or path, a [convert] "
    "line or a [governance] value) or policy.toml (a [policy] value), asks me to sign in, or to put back, "
    "rename or sync a folder or file, or says it is mine (Rosetta to install, a native pandoc copy to "
    "replace or remove, a docs-repo remote to remove or allow, a state file that is damaged, another "
    "account's or not a file, a state folder that is not a folder, or a state file or folder that an ACL, a "
    "privacy setting, a folder above it or a read-only volume refuses) is mine too: show it to me and do not "
    "make that edit or run its command. Otherwise, re-running the install.sh command its fix names is yours, "
    "once, even when the fix goes on to name a [convert] edit for me (the bundled pandoc's), which stays "
    "mine: if the same [FAIL] line comes back after it, show it to me, log it and go to step 3's report."
)
"""Step 2's words, since v22, on whose a [FAIL] line's fix is (B13 round 13 O11, O30): v21 named only [warn]
and WAITING ON YOU: lines as the person's, so a renamed folder's "fix path in [[source]]" read as the agent's
edit. Not every [FAIL]: a check that ran out of time is fixed by re-running install.sh, which its fix says.
Since v25 it names every kind of fix ``doctor.PERSONS_FIX_RE`` and install.sh's ``persons_fails`` read as the
person's (B13 round 16 T70, with lane B's T5, T2 and T9 words): a [[source]] block put back, a [governance] or
[policy] value, Rosetta to install and a native pandoc copy; and the re-run it leaves to the agent is an
install.sh command, once, and only for a fix that is not the person's: never Rosetta's softwareupdate, a
sign-in's ``agentsync status`` before the sign-in, or ``agentsync install-agent`` (B13 round 16 review).
Lane B added a state file that cannot be read (its T12). Since v29 a state file that is damaged or another
account's (B13 round 20 X26): a file of this account's own that it cannot open is a chmod the agent runs, the
fix doctor's state_dir.files line names, while "cannot be read" read as covering it. Since v31 the kind is
doctor.PERSONS_KINDS's (B13 round 22 Z65, lane B's words): a state file that is not a file, and a state file
or folder an ACL, a privacy setting, a folder above it or a read-only volume refuses."""

STATE_CHMOD_IS_YOURS = (
    "Each chmod 600 or chmod 700 of agentsync's own state folder or a file in it (the manifest, its -wal or "
    "-shm, the lock, the heartbeat, a governance file or another file there) that the NEXT: line or any "
    "[FAIL] line (state_dir, lock, heartbeat or governance) names as the fix is yours too, once each, in one "
    "pass: run each as written, a folder's first; if the NEXT: line also says to run ~/.local/bin/agentsync "
    "status (a shut folder hides its files' modes), run it, then each such chmod its [FAIL] lines name; then "
    "run the same install.sh command again. If the re-run names a chmod you already ran, show me, log it and "
    "go to step 3's report; a new one it names is yours the same way. A sudo or a chown stays mine."
)
"""Step 2's words, since v30, for a state folder or file of this account's own that it cannot open (B13 round
21 Y4, Y24, Y59; the lead's ownership rule): step 1 said that chmod is the agent's once step 2 stops on it,
but step 2 let the agent follow a failed run's NEXT only when it was an install.sh or agentsync command or a
click, and its [FAIL] rule only re-ran install.sh, so a literal reading logged "failed at step 2" and handed
the person the chmod. The grant is tied to the NEXT: or [FAIL] line's own words, so a later paste needs no
memory of step 1, and it is one chmod, once; sudo and chown stay the person's. Since v31 (B13 round 22 Z59,
Z8, Z81; the lead's ruling 1) it covers every state file, the lock, the heartbeat and the governance files
included, on any [FAIL] line that names it, and "once" is one pass, each chmod once, a folder's first: a shut
folder hides its files' modes, so its NEXT adds status, whose [FAIL] lines name them."""

STEP3_STATE_CHMOD_IS_YOURS = (
    "A chmod 600 or chmod 700 that a [FAIL] line (state_dir, lock, heartbeat or governance) names as the fix"
    " of agentsync's own state folder or a file in it is yours here too, once each in this step, as in step"
    " 2: run it as written, then the sync again. Log the sync that stopped as a step 3 error with its exit"
    " code and the chmod you ran (unlike step 2's install.sh, no record of a sync is kept for the report). If"
    " the sync stops on a chmod you already ran, show me, log it as a step 3 error and go to the report. A"
    " sudo or a chown stays mine."
)
"""Step 3's words, since v31, for the same chmod when a sync, then status, stops on it (B13 round 22 Z38):
v30 named it only in step 2, which ends in "the same install.sh command again", so step 3 had neither the
"once" bound nor what to run after it. "Once each in this step", so a chmod spent in step 2 does not use up
step 3's. Since v32 (B13 round 23 A36) the sync that stopped is logged as a step 3 error: no record of a sync
is kept for the report, which said "fully one command, 0 error" over it; the report reads that error as a
turn (worked with help)."""

LOCAL_WORK_TOLD = (
    "if the report's NEXT: line says bring-back.md does not carry the commits of some local-work branches,"
    " name ~/agent-context/bring-back.md and then tell me, in that line's words, how many local-work branches"
    " it does not carry, that its section 3 says how I may add them as patches and run"
    " ~/src/agent-context-sync/scripts/install.sh --report-only again, and that either way I review the file"
    " and copy it back; name no branch; do not add the patches or run the report again yourself;"
)
"""The finish's words, since v31, for the report's NEXT on a local-work branch whose commits bring-back.md
does not carry (B13 round 22 Z30, Z71, one defect: round 21 Y23's NEXT had no relay). Before the cut-off
clause, which still applies, since the cut-off NEXT wraps this one ("either way"); no branch is named, as a
branch's name can carry a work name, and the agent adds nothing and runs no report, the report being its last
command."""


def test_readme_prompt_asks_a_mac_already_set_up_for_no_folder() -> None:
    """Field report 2026-10-07 (setup prompt v9). v8 was run on a Mac that already synced two folders. The
    tool ran unattended, the folder question came back unanswered, and step 1 said to stop and wait: a
    round lost on a question the person had answered before. Stopping stays right on a new Mac. When
    ``--list-folders`` says folders are already synced, the agent says which and asks only whether to add
    one, and with nobody to ask it adds none and goes on, which is no deviation. Step 2 names the command
    for that. The line and the mark the prompt names are the installer's own.

    The folder to add is an unmarked one, in the prompt and in the list's own NEXT: a listed folder inside
    a synced one is synced already, and one that holds one would be read twice, and each has a mark that
    says so."""
    step1, step2 = _prompt_steps()[1], _prompt_steps()[2]
    stop = "do not choose folders for me: log a deviation, stop and wait for my answer."
    assert f"{stop} {MIRRORED_NO_BLOCK} {CONFIG_STOP_TOLD} Two exceptions." in step1, (
        "for both lists, before the exceptions"
    )
    # the words it reads are the list's own (B13 round 17 U12)
    assert "; step 2 stops at its config step on " in SCRIPTS[0].read_text(encoding="utf-8")
    kept = (
        "Two exceptions. First, if --list-folders printed a line that starts "
        f'"{ALREADY_SYNCED}", I chose those folders before and they are kept (each has [synced] before its '
        f"path in the list). {SIGNED_OUT} {SIGNED_OUT_UNASKED} If the list's NEXT: line does not ask me to "
        "sign in and then re-run, tell me which they are and ask only whether to add any unmarked folder. "
        f"{GONE_FOLDER} {KEPT_UNASKED} That is not a deviation: do not log it or stop, and say in your final "
        "message that I gave no answer, so no folder was added."
    )
    assert step1.endswith(f"{kept} {UNREAD_CONFIG}"), "the rule for a new Mac, then its exceptions, last"
    # B13 round 16 T6, T30: the mirrored-folder tail's words, which say neither "on disk" nor "not signed in"
    # (the keys of the gone-folder sentence and the sign-in wait), on both of the list's NEXTs
    script = SCRIPTS[0].read_text(encoding="utf-8")
    for tail in (
        "is still mirrored but has no [[source]] block",
        "are still mirrored but have no [[source]] block",
    ):
        lines = [ln for ln in script.splitlines() if tail in ln and "vtail=" in ln]
        assert len(lines) == 2 and not any("on disk" in ln or "signed in" in ln for ln in lines), lines
    # B13 round 11 M4 (lane RB's words): with OneDrive signed out, a Mac that already syncs folders got
    # "[synced]" lines for folders that are not on disk, and the exception sent an unattended agent on to a
    # step 2 that fails. The list now exits 3 with a NEXT: that asks the person to sign in; the exception
    # waits for that as for a click, before its "add none and go on", and reads the list again.
    assert step1.index(SIGNED_OUT) < step1.index(KEPT_UNASKED)
    # B13 round 19 W28: right after the gone-folder sentence, "If you cannot ask me then" could be read as
    # bound to it, and a Mac whose kept folders are all on disk would fall back to the rule for a new Mac (a
    # logged deviation, a stop); the sentence says it holds either way, and keeps the gone folders' fixes
    assert (
        KEPT_UNASKED.startswith("Whether or not that line names such a folder, ")
        and "then," not in KEPT_UNASKED
    )
    # B13 round 13 O30: the list's words the first exception's gone-folder sentence answers, both counts
    script = SCRIPTS[0].read_text(encoding="utf-8")
    # B13 round 15 S36: "1 of them", or "it" when it is the one synced folder, by one variable
    assert 'one="1 of them"' in script and "one=it " in script
    assert "$one is no longer on disk although its app is signed in" in script
    assert "of them are no longer on disk although their app is signed in" in script
    # B13 round 16 T72: both counts name the wait's cause too, inside the parenthesis the sentence reads, and
    # doctor's fix for it, so step 1 no longer leaves out a folder unticked in its app
    for count in ("$one is no longer on disk", "of them are no longer on disk"):
        [line] = [ln for ln in script.splitlines() if count in ln and "NEXT_MSG=" in ln]
        assert "(renamed, moved or deleted there, or not synced on this Mac)" in line, line
        assert "synced to this Mac again in its app" in line, line
    # B13 round 14 Q15, Q73: and the other two tails, the other account's folder and the library not synced,
    # each with the fix the sentence relays from its parentheses; "on disk" is in no other line of the list
    for tail in ("$one is not on disk because", "of them are not on disk because"):
        assert script.count(tail) == 2, tail
    assert "names the fix (sign in to that app with the account" in script
    assert "names the fix (sync the SharePoint library to this Mac with Sync on its SharePoint page" in script
    # the signed-out lines the sign-in wait keys on end "then re-run"; the other-account tail has no re-run
    for signed_out in (
        "sign in to $apps (or let it finish setting up), then re-run:",
        "sign in to OneDrive, then re-run:",
    ):
        assert signed_out in script, signed_out
    listing = script.split("list_folders() {", 1)[1].split("\n}\n", 1)[0]
    on_disk = [ln for ln in listing.splitlines() if "on disk" in ln and "NEXT_MSG=" in ln]
    assert len(on_disk) == 6
    assert all("no longer on disk" in ln or "not on disk because" in ln for ln in on_disk), on_disk
    assert not any("re-run" in ln for ln in on_disk), "no tail reads as the signed-out lines' sign-in wait"
    # O11, O30: a [FAIL] whose fix is the person's is theirs, as a [warn] line's is; re-running is the
    # agent's. The installer's NEXT for such a run says the same (tests/test_install_oneshot.py)
    assert " ".join(step2.split()).count(FAIL_IS_MINE) == 1 and "[FAIL] line's fix is mine" not in step2
    assert "the [FAIL] lines above are the person's to fix" in script
    # B13 round 14 Q7: both person-owned stops of step 2 say the words the prompt keys on; nothing else does
    assert PERSONS_NEXT in " ".join(step2.split())
    # (round 14 lane RB's Q22 adds a third, the first sync's refused config, in the same shape)
    code = [ln for ln in script.splitlines() if not ln.lstrip().startswith("#")]
    persons = [ln for ln in code if "not a step for the agent" in ln]
    assert any("PERSONS_FAILS=" in ln for ln in persons), persons
    assert any("the config $CONFIG does not load" in ln for ln in persons), persons
    for ln in persons:
        shown = "not a step for the agent, so show them (?:those lines|the error) and go on to the report"
        assert re.search(shown, ln), ln
    # B13 round 12 N32, N6: the sign-in wait has a rule for nobody to ask, and it is the report, so step 3's
    # no-report cases do not name it; N7: on a Mac that syncs no folder yet a sign-in is no click to wait for
    assert NEW_MAC_SIGN_IN in step1 and step1.index(NEW_MAC_SIGN_IN) < step1.index("Two exceptions.")
    assert step1.count("as for a click") == 1, "only the first exception waits for a sign-in"
    no_report = " ".join(_prompt_steps()[3].split()).split("Four cases have no report", 1)[1]
    no_report = no_report.split("Every other stop still ends with the report.", 1)[0]
    assert "sign" not in no_report, "an unanswered sign-in wait goes to the report"
    assert step1.index(stop) < step1.index(kept) and step1.count("log a deviation") == 2, "the wait and L10's"
    script = SCRIPTS[0].read_text(encoding="utf-8")
    # B13 round 14 Q39, Q44: "1 folder" or "N folders", from one string set before both forms
    assert script.count(f'say "{ALREADY_SYNCED} $sn (') == 2, "both forms of the line start so"
    assert 'sn="$synced folders" them=them' in script and '{ sn="1 folder" them=it; }' in script
    assert 'SYNCED_MARK="[synced]"' in script
    assert 'INSIDE_MARK="[inside a synced folder]"' in script
    assert 'CONTAINS_MARK="[contains a synced folder]"' in script
    assert "for each unmarked folder to add from the list above" in script
    none_to_add = (
        "On a Mac that already syncs folders, name only the folders I chose to add. With none to add, run it "
        f"with no --source-local, which keeps the folders already synced: `{INSTALL_SH}`"
    )
    assert none_to_add in step2
    assert step2.index(INSTALL_WITH_FOLDER) < step2.index(none_to_add) < step2.index("It installs, runs")
    assert "run $SELF, with one --source-local" in script, "the list's own NEXT names the same command"
    intro = _intro()
    assert "On a Mac that already runs agentsync the same block is a re-run." in intro
    assert "when it cannot ask you it adds none and goes on" in intro
    flat_intro = " ".join(intro.split())
    # B13 round 12 N9, N63 (one defect): since v20 step 1 also waits on a set-up Mac for the sign-in its
    # list asks for (round 11 M4, SIGNED_OUT above): the intro's "only on a Mac that syncs no folder yet" was
    # false
    assert (
        "It stops and waits for you only on a Mac that syncs no folder yet, or, on a Mac that already syncs "
        "folders, when the list asks you to sign in to OneDrive (or the app it names) first; once you say "
        "you have, it lists the folders again."
    ) in flat_intro and SIGNED_OUT in step1
    # B13 round 13 O12, O62, O71 (one defect): since v21 an agent that cannot ask does not wait for that
    # sign-in and goes to the report, which the intro did not say
    assert (
        "When it cannot ask you (an unattended run, or a question left unanswered), it does not wait for "
        "that sign-in and logs nothing for it: it goes to step 3's report (since v21)."
    ) in flat_intro
    assert (
        "If you cannot ask me (your tool runs unattended, or the question comes back unanswered), do not "
        in (" ".join(step1.split()))
        and "wait and log nothing for it: go to step 3's report." in " ".join(step1.split())
    )
    # B13 round 13 O61: when step 2 does not reinstall agentsync, round 12 N29's NEXT adds no folder and v21
    # asks nothing about folders
    assert (
        "When the installed agentsync is already this checkout's build, with its bundled pandoc whole, "
        "step 2 does not reinstall it and no folder can be added until the config is fixed, so the agent "
        "asks you nothing about folders and runs step 2 with no folder."
    ) in flat_intro
    # B13 round 17 U90: since round 16 T54 a build whose bundled pandoc is not the file RECORD lists is
    # reinstalled, and step 1's list then prints the reinstall sentence, as the README and the guide say
    assert (
        '\n\tstamp_is_current "$@" && bundled_pandoc_intact "${1%/*}"\n}\n'
        in script.split("\ninstall_is_current() {", 1)[1][:120]
    )
    assert "Step 2 reinstalls agentsync first" in script
    page = " ".join(_feedback_page().split())
    assert (
        "When the installed agentsync is already this checkout's build and its bundled pandoc is whole "
        "(install.sh's `install_is_current`; B13 round 16 T54), step 2 does not reinstall it, and the "
        "sentence says so instead:"
    ) in page
    assert (
        "A build whose bundled pandoc is missing or not the file pypandoc_binary installed is reinstalled "
        "by step 2, so step 1's list prints the sentence above that says step 2 reinstalls agentsync first "
        "(B13 round 17 U90)."
    ) in page
    assert "ask me nothing about folders: no folder can be added until I fix the config" in " ".join(
        step1.split()
    )
    # B13 round 9 K73: the intro says what the agent does when the list could not read the config
    assert "When the list cannot read which folders the Mac syncs, it marks none, and the agent says so" in (
        flat_intro
    )
    # B13 round 11 M75: not after `marks=failed`, where round 10 L11 withdrew the keep (step 2 may stop at its
    # config step and keep nothing), as the prompt's step 1 and install.sh's NEXT: line say
    assert "do not say step 2 keeps my folders" in step1
    assert "step 2 stops at its config step and nothing is kept or added until the config is" in script
    assert (
        "a run with no folder keeps whatever was synced, unless the installed agentsync failed while reading "
        "that config. Then step 2 may stop at its config step and keep nothing until the config is fixed"
    ) in flat_intro


UNREAD_CONFIG = (
    "Second, if the list's NEXT: line says this Mac has a config whose folders could not be read, the list"
    " marks none. If that line also says to re-run --list-folders to mark them, run"
    f" `{INSTALL_SH} --list-folders` once and go by what it prints: if the re-run's NEXT: line no longer says"
    " the config's folders could not be read, its config was read, so follow that list as on any Mac (with "
    "no folder marked, the folder "
    "question"
    " applies as on a new Mac). If no list marked a folder and the last list's NEXT: line still says this Mac"
    " has a config whose folders could not be read, tell me the list could not read which folders this Mac"
    " already syncs. If that NEXT: line says the installed agentsync failed while reading the config, show me"
    " that line in its own words and do not say step 2 keeps my folders; otherwise say that if this Mac "
    "was set"
    " up before, step 2 with no --source-local keeps them. If that line says step 2 does not reinstall it,"
    " ask me nothing about folders: no folder can be added until I fix the config; go on to step 2 with no"
    " --source-local. Otherwise ask only whether to add a folder from the list (none is needed)."
    " If you cannot ask me then, add none and go on to step 2 with no --source-local: as in the first"
    " exception, that is not a deviation, and your final message says I gave no answer, so no folder was"
    " added. If step 2 then says no folder is synced yet (that config held none), this Mac syncs no folder"
    " after all, so the folder question above applies as on a new Mac: ask me which folders from the list"
    " to sync. If you cannot ask me, do not choose them for me: log a deviation at step 1, stop and wait for"
    ' my answer exactly as in step 1. Run no --report-only and add no "## Not used" section. Your final'
    ' message says "none: waiting for your folder answer" for the NEXT: line and "none" for bring-back.md,'
    " because the one step 2 just wrote holds a provisional report. Once I answer, run step 2 again with one"
    " --source-local per folder I chose. Do not use the add-source that its NEXT: line names: no step 2 line"
    " reports those folders as added."
)
"""Step 1's second exception (setup prompt v18). Where the list could not read an existing config (no
agentsync installed, a reader that timed out, an older or failing build), v17 marked nothing, printed no
"already synced" line, and so had an agent that could not ask log a deviation and wait, while the list's own
NEXT said a run with no folder keeps that config (B13 round 9 K9, K20, K32, K73, K78: one defect, built
once). The words key on the installer's NEXT, and only on a list that printed folders: the sign-in NEXTs
carry the same clause and keep v17's no-folder-paths rule.

v19 (B13 round 10 L62): a re-run after a timeout that read the config prints no unread clause, and the old "if
no list marked a folder" still applied, so the agent said the config could not be read and, unattended, ran
step 2 with no folder. The exception now holds only while the last list still says so. L10: after step 2's "no
folder is synced yet" the wait is step 1's in full, but step 2 has already written a provisional report, so
the
words say which file to name and that the answer goes to step 2 again, not to the add-source its NEXT names
(which no step 2 line would report as added). L11 (lane RB's words): where the installed agentsync failed
while
reading the config, step 2 stops at its config step unless its reinstall can load it, so the words claim no
keep
there and show the person the list's own line."""


def test_a_folder_list_that_could_not_read_the_config_goes_on_unattended(tmp_path: Path) -> None:
    """:data:`UNREAD_CONFIG`, held to the installer's words: the list run on a Mac whose sources.toml cannot
    be read (here: no agentsync installed) prints folders, no "already synced" line and the NEXT clause the
    prompt keys on; after a reader timeout the clause also offers the re-run the prompt names; and step 2's
    no-folder ending is status's "no folder is synced yet"."""
    step1 = _prompt_steps()[1]
    assert UNREAD_CONFIG in step1 and step1.count("log a deviation") == 2  # a new Mac's wait, and L10's
    script = SCRIPTS[0].read_text(encoding="utf-8")
    assert "; this Mac has a config ($CONFIG) whose folders could not be read: if it was set up before, " in (
        script
    )
    assert 'kept="$kept; or re-run $SELF --list-folders to mark them"' in script
    loop = (ROOT / "src" / "agentsync" / "loop.py").read_text(encoding="utf-8")
    assert 'f"no folder is synced yet: ask the operator which folders to sync' in loop
    projects = tmp_path / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Projects"
    projects.mkdir(parents=True)
    (tmp_path / "agent-context").mkdir()
    (tmp_path / "agent-context" / "sources.toml").write_text("[[source]\nnot toml\n", encoding="utf-8")
    proc = _list_folders(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert str(projects) in proc.stdout and ALREADY_SYNCED not in proc.stdout, proc.stdout
    [next_line] = [ln for ln in proc.stdout.splitlines() if ln.startswith("NEXT:")]
    flat = next_line.replace(str(tmp_path / "agent-context" / "sources.toml"), "<config>")
    clause = "; this Mac has a config (<config>) whose folders could not be read: if it was set up before, "
    assert clause in flat, flat


@pytest.mark.parametrize("shell", _SHELLS)
def test_readme_step1_command_marks_the_folders_a_set_up_mac_syncs(tmp_path: Path, shell: str) -> None:
    """The exact step 1 line on a Mac that already syncs a folder, under the user's likely shells: the list
    starts with the line the prompt keys on, the synced folder has its mark, and the NEXT names the command
    with no folder. The installed agentsync that reads the config is the Python running these tests."""
    from agentsync.config import default_config_text, local_source_table  # noqa: PLC0415

    home = tmp_path / "home"
    cloud = home / "Library" / "CloudStorage" / "OneDrive-Contoso"
    projects, archive = cloud / "Projects", cloud / "Archive"
    for d in (projects, archive):
        d.mkdir(parents=True)
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir()
    cfg.write_text(default_config_text() + local_source_table("projects", projects), encoding="utf-8")
    tool_py = home / ".local" / "share" / "uv" / "tools" / "agentsync" / "bin" / "python"
    tool_py.parent.mkdir(parents=True)
    tool_py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    tool_py.chmod(0o755)
    _fake_step1_tools(tmp_path / "bin", tmp_path / "calls.log")
    env = tmp_home_env(home) | {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin"}
    command = STEP1_COMMAND.replace("<agent>", "Test Agent (model-1)")
    proc = subprocess.run(
        [shell, "-c", command], cwd=home, env=env, capture_output=True, text=True, check=False, timeout=60
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout.splitlines()
    listed = out[out.index(f"{ALREADY_SYNCED} 1 folder (marked [synced] below)") + 1 :]
    assert listed[:2] == [str(archive), f"[synced] {projects}"], proc.stdout
    assert listed[2].startswith("NEXT: this Mac already syncs 1 folder, and a re-run keeps it: run ")
    assert len(listed) == 3 and "warning:" not in proc.stderr


def _checkout_home(tmp_path: Path) -> Path:
    """A HOME holding only ~/src/agent-context-sync/scripts/install.sh (this tree's), as step 1 leaves it."""
    home = tmp_path / "home"
    scripts = home / "src" / "agent-context-sync" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPTS[0], scripts / "install.sh")
    return home


STEP1_START_ONLY = f"{INSTALL_SH} --log-start '{START_AGENT}'".replace("<agent>", "Test Agent (model-1)")
"""Step 1's --log-start part alone, filled in (the rest of step 1 is tested above)."""


def test_readme_friction_line_format_and_kinds() -> None:
    """One exact command per friction line (the per-line template), the single-quote rule that keeps it safe,
    and the closed list of kinds, which setup-report and the feedback page share."""
    pre = _preamble()
    intro = "Friction log: from step 1 on, whenever something happens that is not in this prompt, log it with"
    assert f"{intro} `{FRICTION_LOG_TEMPLATE}`" in pre
    assert "Keep the single quotes and write \u2019 instead of ' inside them." in pre
    assert "Do not log the steps themselves; the installer times them." in pre
    listed = pre.split("<kind> is one of:", 1)[1].split(". Do not log", 1)[0]
    kinds = [k.strip() for k in re.split(r"[;,.]", re.sub(r"\([^)]*\)", "", listed)) if k.strip()]
    assert tuple(kinds) == FRICTION_KINDS
    from agentsync import setup_report  # noqa: PLC0415

    code_kinds = getattr(setup_report, "FRICTION_KINDS", None)
    if code_kinds is not None:
        assert set(code_kinds) == set(FRICTION_KINDS), "setup-report counts the kinds the prompt names"
    table = _feedback_page().split("### Friction kinds", 1)[1].split("\n### ", 1)[0]
    documented = re.findall(r"`([a-z]+)`", " ".join(re.findall(r"^\| (.+?) \|", table, flags=re.MULTILINE)))
    assert tuple(documented) == FRICTION_KINDS, "setup-feedback.md triages every kind, in the prompt's order"
    # B13 round 4 Q8: step 1 says to log a deviation and wait when the folder question goes unanswered, but
    # the kind list defined a deviation as doing what the prompt did not say. Both definitions name that stop.
    assert (
        "deviation (you did something this prompt did not say, worked around a problem, or stopped to wait "
        "for my folder answer as step 1 says)"
    ) in pre
    assert "log a deviation, stop and wait for my answer" in _prompt_steps()[1]
    assert "or stopped at step 1 to wait for the folder answer |" in " ".join(table.split())
    # 2026-10-07 C12 (B13 N42): the friction lines are the only agent prose in the report's public part, and
    # four short forms of registered names reached it. Roles only: a typed placeholder made both of C12's
    # false residue hits, and a name that is the problem still gets its line (a question or click there
    # counts in the outcome).
    friction = pre.split("Friction log:", 1)[1]
    assert (
        "These lines go into the report, which I may post in public, so in them name a folder, a file, a "
        'person, my organization or a customer by its role in plain words ("a project folder"), never by its '
        "real name, a short form of it or a placeholder; if a name itself is the problem, still log the line "
        "by role and write the name in fix-request.md."
    ) in friction
    assert "<folder-" not in friction, "C12: an example placeholder in the friction rule invites typed ones"


def test_readme_question_kind_counts_a_go_ahead_given_in_the_chat() -> None:
    """2026-10-07 U4 (B13 N43): the person approved a printed fix in the chat and the agent logged only a
    deviation, which the report never counts. Since v11 the question kind covers a go-ahead or an answer given
    in the chat, as a line of its own beside the deviation, but not the folder answer or word of a click,
    which have their own rules."""
    listed = _preamble().split("<kind> is one of:", 1)[1].split(". Do not log", 1)[0]
    [question] = re.findall(r"question \(([^)]*)\)", listed)
    for words in (
        "in the chat",
        "go-ahead",
        "an answer",
        "which folders to sync",
        "my folder answer",
        "telling you I clicked something or signed in",
        "one line",
        "a question line of its own",
        "deviation",
    ):
        assert words in question, words
    [row] = [ln for ln in _feedback_page().splitlines() if ln.startswith("| `question` |")]
    assert "go-ahead" in row and "click" in row
    # B13 round 12 N5, N32 (one defect): the sign-in step 1 waits for is no click line, as an announced Allow
    # is not; v20's kind named only the Allow, so an agent logged the sign-in and the report read "worked
    # with help"
    [click] = re.findall(r"click \(([^)]*)\)", listed)
    assert click == "I clicked something other than an Allow or the sign-in this prompt announced", click


def _fill_line(step: int, kind: str, what: str, fix: str) -> str:
    """The per-line template with its four values filled the way the prompt says: inside the single quotes."""
    for placeholder, value in (
        ("'<step>'", str(step)),
        ("'<kind>'", kind),
        ("'<what happened>'", what),
        ("'<what would have avoided it, or ->'", fix),
    ):
        assert "'" not in value, "the prompt says to write \u2019 instead of ' inside a value"
        assert placeholder in FRICTION_LOG_TEMPLATE
    return (
        FRICTION_LOG_TEMPLATE.replace("'<step>'", f"'{step}'")
        .replace("'<kind>'", f"'{kind}'")
        .replace("'<what happened>'", f"'{what}'")
        .replace("'<what would have avoided it, or ->'", f"'{fix}'")
    )


def _run_shell(shell: str, cmd: str, home: Path) -> None:
    proc = subprocess.run(
        [shell, "-c", cmd],
        cwd=home,
        env=tmp_home_env(home) | {"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "NEXT:" not in proc.stdout, "a friction command is not a run: it prints no NEXT: line"
    assert not (home / "agent-context" / "setup-report.md").exists(), "only step 3 writes the report"


@pytest.mark.parametrize("shell", _SHELLS)
def test_readme_report_command_runs_without_agentsync(tmp_path: Path, shell: str) -> None:
    """The exact report line on a Mac where setup stopped before agentsync was installed: the friction log's
    attempt gets its end line, and install.sh --report-only writes the report and ends with the issue link and
    a NEXT: line."""
    from agentsync import setup_report  # noqa: PLC0415

    home = _checkout_home(tmp_path)
    env = tmp_home_env(home) | {"PATH": "/usr/bin:/bin"}
    _run_shell(shell, STEP1_START_ONLY, home)
    proc = subprocess.run(
        [shell, "-c", REPORT_COMMAND],
        cwd=home,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout.rstrip().splitlines()
    assert out[-1].startswith("NEXT:") and out[-2].startswith(setup_report.ISSUE_LINK_LABEL), out[-3:]
    assert (home / "agent-context" / "setup-report.md").is_file()
    [attempt] = setup_report.parse_friction((home / FRICTION_LOG).read_text(encoding="utf-8")).attempts
    assert attempt.finished


@pytest.mark.parametrize("shell", _SHELLS)
def test_readme_friction_log_two_attempts(tmp_path: Path, shell: str) -> None:
    """The prompt's exact friction commands (install.sh --log-start, --log and --report-only), as an agent
    runs them over two attempts under the user's likely shells. The file is private (0600 in a 0700 folder),
    keeps both attempts, and setup-report parses every line into the step and kind the agent gave."""
    from agentsync import setup_report  # noqa: PLC0415

    home = _checkout_home(tmp_path)
    for _ in range(2):
        _run_shell(shell, STEP1_START_ONLY, home)
        _run_shell(
            shell, _fill_line(1, "question", "asked which folders to sync | and whether all", "-"), home
        )
        _run_shell(shell, _fill_line(2, "error", "install.sh exited 3", "click Allow sooner"), home)
        done = subprocess.run(
            [shell, "-c", f"{INSTALL_SH} --report-only"],
            cwd=home,
            env=tmp_home_env(home) | {"PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        assert "friction log: attempt finished" in done.stdout
        (home / "agent-context" / "setup-report.md").unlink()  # step 3's report: the next attempt has none
    log = home / FRICTION_LOG
    assert log.stat().st_mode & 0o777 == 0o600 and log.parent.stat().st_mode & 0o777 == 0o700
    friction = setup_report.parse_friction(log.read_text(encoding="utf-8"))
    assert len(friction.attempts) == 2
    for attempt in friction.attempts:
        assert attempt.header.get("Prompt") == f"v{_prompt_version()}"
        assert attempt.header.get("Agent") == "Test Agent (model-1)" and attempt.finished
        assert [(e.step, e.kind) for e in attempt.events if e.kind != "finished"] == [
            (1, "question"),
            (2, "error"),
        ]
        assert attempt.events[0].what == "asked which folders to sync | and whether all"
        assert attempt.events[1].fix == "click Allow sooner"


@pytest.mark.parametrize("shell", _SHELLS)
def test_readme_friction_line_never_runs_the_agents_words(tmp_path: Path, shell: str) -> None:
    """K7: an agent quotes commands in backticks and ``$(...)``. Filled into the --log template as the prompt
    says, they are written to the log as text and nothing runs, under bash and zsh; a typographic apostrophe
    (the prompt's replacement for a straight one), ``${HOME}``, ``|`` and ``;`` survive unchanged."""
    from agentsync import setup_report  # noqa: PLC0415

    home = _checkout_home(tmp_path)
    witness = tmp_path / "ran"
    what = f"hint said `touch {witness}-backtick` and $(touch {witness}-subst) and ${{HOME}}; a | b"
    fix = f"don\u2019t print `touch {witness}-fix`; use $(touch {witness}-fix2)"
    _run_shell(shell, STEP1_START_ONLY, home)
    _run_shell(shell, _fill_line(2, "deviation", what, fix), home)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["home"], "no witness file was created"
    text = (home / FRICTION_LOG).read_text(encoding="utf-8")
    assert what in text and fix in text, text
    [attempt] = setup_report.parse_friction(text).attempts
    [event] = attempt.events
    assert (event.step, event.kind, event.what, event.fix) == (2, "deviation", what, fix)


def test_every_command_the_readme_one_prompt_names_exists() -> None:
    """The block a user pastes into a coding agent only names install.sh flags and agentsync subcommands and
    options that exist (a renamed flag would otherwise fail on the new Mac, not here).  install.sh is read,
    not run: each flag needs a case arm and a line in the header comment that --help prints."""
    import argparse  # noqa: PLC0415

    from agentsync import cli  # noqa: PLC0415

    spans = _spans(_one_prompt_block())
    script = SCRIPTS[0].read_text(encoding="utf-8")
    help_text = _installer_help()
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    install_flags: set[str] = set()
    commands: set[tuple[str, str]] = set()
    for span in spans:
        for part in span.split("install.sh")[1:]:
            install_flags |= set(
                re.findall(r"(?<!\S)(--[a-z][a-z-]*)", part.split("&&", 1)[0].split(";", 1)[0])
            )
        for m in re.finditer(r"(?:^|[\s/])agentsync\s+([a-z][a-z-]*)((?:\s+--[a-z][a-z-]*)*)", span):
            commands |= {(m.group(1), flag) for flag in m.group(2).split()} | {(m.group(1), "")}
    assert install_flags == ONE_PROMPT_INSTALL_FLAGS
    assert commands == {("sync", ""), ("sync", "--materialise-budget"), ("curate", ""), ("status", "")}
    missing: list[str] = []
    for flag in sorted(install_flags):
        if not re.search(rf"^\s*(?:-\S+ \| )*{re.escape(flag)}(?: \| -\S+)*\)", script, flags=re.MULTILINE):
            missing.append(f"install.sh has no case arm for {flag}")
        if not re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", help_text):
            missing.append(f"install.sh --help does not document {flag}")
    for command, flag in sorted(commands):
        if command not in sub.choices:
            missing.append(f"agentsync has no subcommand {command!r}")
        elif flag and flag not in {o for a in sub.choices[command]._actions for o in a.option_strings}:
            missing.append(f"agentsync {command} has no option {flag}")
    assert missing == [], "; ".join(missing)


# ---- "Fewer approval prompts": the optional pre-allow rules under the block (judge finding K11) -----------


def _approval_section() -> str:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("\n## Set up on a new Mac: one prompt\n", 1)[1].split("\n## ", 1)[0]
    after = section.split(_one_prompt_block(), 1)[1]
    match = re.search(
        r"<details>\n<summary><b>Fewer approval prompts</b>.*?</details>", after, flags=re.DOTALL
    )
    assert match, "README.md: no 'Fewer approval prompts' part after the one-prompt block"
    return match.group(0)


def _claude_code_rules() -> list[str]:
    [block] = re.findall(r"```json\n(.*?)```", _approval_section(), flags=re.DOTALL)
    settings = json.loads(block)
    assert set(settings) == {"permissions"} and set(settings["permissions"]) == {"allow"}
    rules: list[str] = settings["permissions"]["allow"]
    return rules


def _copilot_rules() -> list[str]:
    [block] = re.findall(r"```sh\n(.*?)```", _approval_section(), flags=re.DOTALL)
    [flag] = re.findall(r"--allow-tool='([^']*)'", block)
    assert block.startswith("cd ~ && copilot --allow-tool=")
    rules = [r.strip() for r in flag.split(",")]
    assert all(re.fullmatch(r"shell\([^()]+\)", r) for r in rules), rules
    return [r[len("shell(") : -1] for r in rules]


def _split_top(cmd: str) -> list[str]:
    """``cmd`` split at the shell separators Claude Code's documentation names (&&, ||, ;, |, |&, &, newline)
    outside quotes and ``$(...)``; braces and if/then/else/fi keywords dropped, so each piece is one simple
    command."""
    pieces: list[str] = []
    cur: list[str] = []
    quote = ""
    depth = 0
    i = 0
    while i < len(cmd):
        ch = cmd[i]
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif cmd.startswith("$(", i):
            depth += 1
            cur.append("$(")
            i += 2
            continue
        elif ch == ")" and depth:
            depth -= 1
        elif not depth and ch in ";|&\n":
            pieces.append("".join(cur))
            cur = []
            i += 2 if cmd[i : i + 2] in ("&&", "||", "|&") else 1
            continue
        cur.append(ch)
        i += 1
    pieces.append("".join(cur))
    simple: list[str] = []
    for piece in pieces:
        text = piece.strip()
        while text.startswith(("{ ", "if ", "then ", "else ")):
            text = text.split(" ", 1)[1].lstrip()
        text = text.removesuffix(" }").strip()
        if text not in ("", "{", "}", "fi"):
            simple.append(text)
    return simple


def _subcommands(cmd: str) -> list[str]:
    """Every simple command in ``cmd``, including those inside a ``$(...)`` outside single quotes (a deny or
    ask rule applies inside a command substitution too, so an allow list is only complete when it covers
    them)."""
    out: list[str] = []
    for part in _split_top(cmd):
        out.append(part)
        unquoted = re.sub(r"'[^']*'", "''", part)  # no substitution runs inside single quotes
        out += [s for inner in re.findall(r"\$\(([^()]*)\)", unquoted) for s in _subcommands(inner)]
    return out


def _claude_code_rule_matches(rule: str, command: str) -> bool:
    """Claude Code's documented Bash rule matching: the rule text matches the whole command, ``*`` stands for
    any text (spaces included), ``:*`` at the end is the same as `` *``, and a trailing `` *`` that is the
    rule's only wildcard also matches the bare command."""
    m = re.fullmatch(r"Bash\((.+)\)", rule)
    assert m, rule
    pattern = m.group(1)
    if pattern.endswith(":*"):
        pattern = pattern[:-2] + " *"
    if pattern.endswith(" *") and pattern.count("*") == 1 and command == pattern[:-2]:
        return True
    regex = ".*".join(re.escape(part) for part in pattern.split("*"))
    return re.fullmatch(regex, command, flags=re.DOTALL) is not None


def _copilot_rule_matches(rule: str, command: str) -> bool:
    """Copilot CLI's documented ``:*`` form: the text before it, alone or followed by a space and more text;
    without ``:*`` the command must be the text exactly."""
    if rule.endswith(":*"):
        stem = rule[:-2]
        return command == stem or command.startswith(stem + " ")
    return command == rule


def _agent_commands() -> list[str]:
    """Every command the block has the agent run on its normal path (``xcode-select --install``, the git
    stop's three queries and the worktree stop's two commands are the person's to run;
    :data:`KEEP_LOCAL_WORK`,
    :data:`KEEP_RETRY` and :data:`KEEP_BRANCH_WORK` run only after a failed pull or keep, and ask first)."""
    persons = (
        "xcode-select --install",
        KEEP_LOCAL_WORK,
        KEEP_RETRY,
        KEEP_BRANCH_WORK,
        WORKTREE_MOVE,
        WORKTREE_REPAIR,
        WORKTREE_DETACH,
        WORKTREE_BACK,
        GIT_REMOTE_QUERY,
        GIT_REWRITE_QUERY,
        GIT_PROXY_QUERY,
    )
    commands = [c for c in _commands(_one_prompt_block()) if c not in persons]
    assert commands == [
        FRICTION_LOG_TEMPLATE,
        STEP1_COMMAND,
        VERSION_CHECK,
        _LIST_FOLDERS,
        INSTALL_WITH_FOLDER,
        INSTALL_SH,
        *LOOP_COMMANDS,
        SYNC_NO_DOWNLOAD,
        REPORT_COMMAND,
    ], commands
    return commands


def test_split_top_follows_the_documented_separators() -> None:
    assert _subcommands(STEP1_COMMAND) == [
        CEILING,
        "sw_vers -productVersion",
        "xcode-select -p",
        "[ -f ~/src/agent-context-sync/.git/HEAD ]",
        "[ -f ~/src/agent-context-sync/.git ]",
        "git -C ~/src/agent-context-sync pull --ff-only",
        "git clone https://github.com/renchris/agent-context-sync.git ~/src/agent-context-sync",
        f"{INSTALL_SH} --version",
        f"{INSTALL_SH} --log-start '{START_AGENT}'",
        f"{INSTALL_SH} --list-folders",
    ]
    assert _subcommands(REPORT_COMMAND) == [f"{INSTALL_SH} --report-only"]
    assert [_subcommands(c) for c in LOOP_COMMANDS] == [[c] for c in LOOP_COMMANDS]
    assert _subcommands(SYNC_NO_DOWNLOAD) == [SYNC_NO_DOWNLOAD]
    assert _subcommands(FRICTION_LOG_TEMPLATE) == [FRICTION_LOG_TEMPLATE]
    assert _subcommands("a 'x; y' && b \"$(c; d)\" | e") == ["a 'x; y'", 'b "$(c; d)"', "c", "d", "e"]


def test_claude_code_rule_matching_follows_the_documented_examples() -> None:
    """The matcher reproduces the rows of Claude Code's permissions page (code.claude.com/docs/en/permissions,
    'Wildcard patterns'), so the coverage test below means what the tool does."""
    rows = [
        ("Bash(npm run build)", ["npm run build"], ["npm run build --watch"]),
        ("Bash(npm run *)", ["npm run build", "npm run test --watch", "npm run"], ["npm install"]),
        (
            "Bash(git log * main)",
            ["git log --oneline main", "git log -5 main"],
            ["git log main", "git push origin main"],
        ),
        ("Bash(* --version)", ["node --version"], ["node -v"]),
        ("Bash(ls *)", ["ls -la", "ls"], ["lsof"]),
        ("Bash(ls*)", ["ls -la", "lsof"], []),
        ("Bash(* --help *)", ["npm --help x"], ["npm --help"]),
        ("Bash(ls:*)", ["ls -la", "ls"], ["lsof"]),
    ]
    for rule, yes, no in rows:
        assert all(_claude_code_rule_matches(rule, c) for c in yes), (rule, yes)
        assert not any(_claude_code_rule_matches(rule, c) for c in no), (rule, no)


def test_readme_pre_allow_rules_cover_every_command() -> None:
    """Each simple command in every command the block has the agent run, the friction-log template filled with
    an agent's words included, matches at least one Claude Code rule under the documented semantics, and at
    least one Copilot CLI pattern (except the read-only ``[`` test). No rule is broader than the program
    and its subcommand: no bare ``Bash``, and a ``*`` only at the end (Claude Code warns about a wildcard
    before the subcommand). Every rule of either tool is needed by some command in the block."""
    claude = _claude_code_rules()
    copilot = _copilot_rules()
    assert all(re.fullmatch(r"Bash\([^*]+( \*)?\)", r) for r in claude), claude
    assert not any(r.startswith(("git:*", "git *")) or r in ("*", "git") for r in copilot), copilot
    filled = _fill_line(2, "deviation", "ran `ls` and $(pwd); then | a pipe", "don\u2019t && stop")
    subs = [s for c in [*_agent_commands(), filled] for s in _subcommands(c)]
    assert f"{INSTALL_SH} --log-start '{START_AGENT}'" in subs and filled in subs
    uncovered: list[str] = []
    for sub in subs:
        if not any(_claude_code_rule_matches(r, sub) for r in claude):
            uncovered.append(f"Claude Code: {sub}")
        if not sub.startswith("[ ") and not any(_copilot_rule_matches(r, sub) for r in copilot):
            uncovered.append(f"Copilot CLI: {sub}")
    assert uncovered == []
    unused = [r for r in claude if not any(_claude_code_rule_matches(r, s) for s in subs)]
    unused += [r for r in copilot if not any(_copilot_rule_matches(r, s) for s in subs)]
    assert unused == [], "every rule is needed by a command in the block"
    # The sync that downloads nothing has a rule of its own: the loop's sync rule is exact and does not cover
    # it, and a rule wide enough for both would also cover a download budget the person never agreed to.
    assert f"Bash({SYNC_NO_DOWNLOAD})" in claude and SYNC_NO_DOWNLOAD in copilot
    assert not _claude_code_rule_matches(f"Bash({AGENTSYNC} sync)", SYNC_NO_DOWNLOAD)
    assert not _copilot_rule_matches(f"{AGENTSYNC} sync", SYNC_NO_DOWNLOAD)
    exact = [r for r in claude if r.startswith(f"Bash({AGENTSYNC} ")]
    assert len(exact) == 4 and not any("*" in r for r in exact)
    assert "The four agentsync rules are exact" in " ".join(_approval_section().split())


def _redirects(cmd: str) -> list[str]:
    """The ``>`` and ``<`` characters in ``cmd`` outside quotes (a redirect, which Claude Code checks as a
    file write or read on top of the Bash rules)."""
    found: list[str] = []
    quote = ""
    for ch in cmd:
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch in "<>":
            found.append(ch)
    return found


def test_readme_block_redirects_to_no_file() -> None:
    """v5b review L4: Claude Code asks for every redirect whose target starts with ``~``, whatever the allow
    rules say, so the block has no redirect at all: every friction line goes through install.sh --log."""
    assert {c: _redirects(c) for c in _agent_commands()} == {c: [] for c in _agent_commands()}
    assert ">>" not in _one_prompt_block() and "printf" not in _one_prompt_block()


def test_readme_pre_allow_part_is_optional_and_honest() -> None:
    """The part says it is optional and the person's to add, sits right after the block, says why the block
    has no redirect, and says which rules step 1 needs."""
    part = " ".join(_approval_section().split())
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.index(_one_prompt_block()) < readme.index("<summary><b>Fewer approval prompts</b>")
    assert (
        "optional" in part
        and "only you add them" in part
        and "never asks the agent to change its tool" in part
    )
    assert "`~/.claude/settings.json`" in part and "https://code.claude.com/docs/en/permissions" in part
    assert "a `>>` target that starts with `~` always needs approval" in part
    assert "The rules cover commands only: the files the loop has the agent write" in part, (
        "the baseline draft's file writes still ask"
    )
    assert "`install.sh --log`" in part and "never with a `>>` redirect" in part
    assert "#tool-permission-patterns" in part
    first = f"Bash({INSTALL_SH} *)"
    step1_rules = _claude_code_rules()[: _claude_code_rules().index(first) + 1]
    assert f"step 1 needs the first {len(step1_rules)} rules" in part.replace("eight", "8")
    assert all(any(_claude_code_rule_matches(r, s) for r in step1_rules) for s in _subcommands(STEP1_COMMAND))


# ---- the setup feedback loop: issue form, feedback page, README steps 3 and 4 --------------------------


ISSUE_TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"
_FORM_TYPES = {"markdown", "textarea", "input", "dropdown", "checkboxes"}
FORM_IDS = ["outcome", "run_type", "prompt_version", "agent", "loop_stage", "report", "review"]
"""The form's field ids, stable because setup-report's pre-filled link names them (J23; K16b loop_stage)."""
PREFILLED_IDS = ["outcome", "run_type", "prompt_version", "agent", "loop_stage"]
RUN_TYPES = ["Real Mac", "Sandbox", "Sandbox with simulated launchd"]


def _form() -> dict[str, Any]:
    import yaml  # noqa: PLC0415

    form = yaml.safe_load((ISSUE_TEMPLATES / "setup-report.yml").read_text(encoding="utf-8"))
    assert isinstance(form, dict)
    return form


def _field(field_id: str) -> dict[str, Any]:
    item: dict[str, Any] = next(i for i in _form()["body"] if i.get("id") == field_id)
    return item


def test_setup_report_issue_form_structure() -> None:
    """GitHub's issue-form schema, as far as a typo would silently break the form (GitHub falls back to a
    blank issue on an invalid template)."""
    form = _form()
    assert {"name", "description", "body"} <= set(form) <= {"name", "description", "title", "labels", "body"}
    assert form["title"] == "Setup report: " and form["labels"] == ["setup-report"]
    ids: list[str] = []
    for item in form["body"]:
        assert item["type"] in _FORM_TYPES
        attrs = item["attributes"]
        if item["type"] == "markdown":
            assert attrs["value"].strip()
            continue
        ids.append(item["id"])
        assert re.fullmatch(r"[a-z][a-z0-9_-]*", item["id"]) and attrs["label"].strip()
        assert isinstance(item.get("validations", {}).get("required", False), bool)
        if item["type"] == "dropdown":
            options = attrs["options"]
            assert len(options) == len(set(options)) and all(isinstance(o, str) and o for o in options)
    assert len(ids) == len(set(ids)), "field ids are unique"
    assert ids == FORM_IDS

    report = _field("report")
    assert report["type"] == "textarea" and report["attributes"]["render"] == "markdown"
    assert report["validations"]["required"] is True
    assert _field("agent")["type"] == "input"
    loop_stage = _field("loop_stage")
    assert loop_stage["type"] == "input" and loop_stage["validations"]["required"] is False, (
        "a report from before the Loop line has no stage"
    )
    prompt = _field("prompt_version")
    assert prompt["type"] == "input" and prompt["validations"]["required"] is True
    assert prompt["attributes"]["placeholder"] == f"v{_prompt_version()}"
    [review] = _field("review")["attributes"]["options"]
    assert _field("review")["type"] == "checkboxes"
    assert review["label"] == "I reviewed this report and it contains no confidential names"
    assert review["required"] is True
    [intro] = [item["attributes"]["value"] for item in form["body"] if item["type"] == "markdown"]
    assert "pre-filled" in intro and "Summary" in intro, "the form says setup-report's link fills it"
    from agentsync import setup_report  # noqa: PLC0415

    label = getattr(setup_report, "ISSUE_LINK_LABEL", None)
    if label is not None:  # the line setup-report --out ends with (judge finding K3)
        assert f"`{label} <link>`" in _feedback_page().split("## 3. Send", 1)[1].split("\n## ", 1)[0]


def test_setup_report_form_outcomes_match_the_readme_prompt() -> None:
    """Fully one command, worked with help, and one 'Failed at step N (<step title>)' per v6 step (KISS K16b
    keeps v6's options so older reports keep theirs). Each README step's title starts with its layout title in
    setup_report.PROMPT_LAYOUTS and maps through ``form_step`` to exactly one of those options."""
    from agentsync import setup_report  # noqa: PLC0415

    outcome = _field("outcome")
    assert outcome["type"] == "dropdown" and outcome["validations"]["required"] is True
    options: list[str] = outcome["attributes"]["options"]
    assert options[:2] == ["Fully one command", "Worked with help"]
    failed = [re.fullmatch(r"Failed at step (\d+) \(([^)]+)\)", o) for o in options[2:]]
    assert all(failed), options
    titles = {int(m.group(1)): m.group(2) for m in failed if m}
    options_of = getattr(setup_report, "FORM_OUTCOME_OPTIONS", None)
    if options_of is None:  # an agentsync before B13 round 7 H11: v6's four, by PROMPT_STEPS
        assert titles == setup_report.PROMPT_STEPS, "the form's options are the v6 steps the module names"
    else:  # v6's options stay, and each layout's failed step gives one of them
        assert options == list(options_of), "the form's options are the ones the module names"
        kept = {(int(m.group(1)), m.group(2)) for m in failed if m}
        assert set(setup_report.PROMPT_STEPS.items()) <= kept, "v6's options stay, so old links stay valid"
        for layout in setup_report.PROMPT_LAYOUTS.values():
            for n in layout.steps:
                label = setup_report.Outcome("failed", n, (), layout.version).form_label
                assert label in options, (layout.version, n, label)
    steps = _prompt_steps()
    layout = setup_report.prompt_layout(_prompt_version())
    assert _prompt_version() == setup_report.PROMPT_VERSION, "the module's prompt is the README's"
    assert layout is setup_report.PROMPT_LAYOUTS[max(setup_report.PROMPT_LAYOUTS)], (
        "the README's prompt is read with the newest layout: a version that moves a step adds an entry"
    )
    assert list(layout.steps) == list(steps)
    for n, text in steps.items():
        assert text.lower().startswith(layout.steps[n].lower()), (n, layout.steps[n], text)
        assert setup_report.Outcome("failed", n, ()).form_label in options, (
            f"README step {n} maps to no option"
        )


def test_setup_report_form_has_a_run_type() -> None:
    """Sandbox runs, with or without simulated launchd, are told apart, so they never count as a success
    (judge findings I18, J14)."""
    run = _field("run_type")
    assert run["type"] == "dropdown" and run["validations"]["required"] is True
    assert run["attributes"]["options"] == RUN_TYPES


def test_setup_report_form_field_ids_are_documented_for_the_prefilled_link() -> None:
    """setup-feedback.md lists every field id with its type, the link's query keys are the five Summary
    fields, and the documented outcome and run type values are the form's options."""
    send = _feedback_page().split("## 3. Send", 1)[1].split("\n## ", 1)[0]
    rows = dict(re.findall(r"^\| `([a-z_]+)` \| ([a-z]+) \|", send, flags=re.MULTILINE))
    assert list(rows) == FORM_IDS
    assert rows == {i: _field(i)["type"] for i in FORM_IDS}
    [link] = re.findall(r"https://github\.com/\S+/issues/new\?template=setup-report\.yml&\S+", send)
    assert re.findall(r"&([a-z_]+)=", link) == ["title", *PREFILLED_IDS]
    for value in RUN_TYPES:
        assert f"`{value}`" in send
    assert "`Fully one command`" in send and "`Worked with help`" in send


def test_setup_report_prefills_the_forms_ids_and_labels() -> None:
    """What setup-report puts in its pre-filled link is what the form has: the field ids, the run type labels
    and one Outcome option per prompt step (J23). Checked only for the names the module defines."""
    from agentsync import setup_report  # noqa: PLC0415

    fields = getattr(setup_report, "ISSUE_FIELDS", None)
    if fields is not None:
        assert list(fields.values()) == PREFILLED_IDS
    run_types = getattr(setup_report, "ISSUE_RUN_TYPES", None)
    if run_types is not None:
        assert sorted(run_types.values()) == sorted(RUN_TYPES)
    steps = getattr(setup_report, "PROMPT_STEPS", None)
    options = getattr(setup_report, "FORM_OUTCOME_OPTIONS", None)
    if options is not None:  # since B13 round 7 H11
        assert list(options) == _field("outcome")["attributes"]["options"]
    elif steps is not None:
        labels = [f"Failed at step {n} ({title})" for n, title in sorted(steps.items())]
        assert labels == _field("outcome")["attributes"]["options"][2:]


def test_issue_template_config_links_the_private_route() -> None:
    """J17: a private route that needs no contact with the maintainer, plus the maintainer's GitHub profile as
    an optional one; no email address is published."""
    import yaml  # noqa: PLC0415

    text = (ISSUE_TEMPLATES / "config.yml").read_text(encoding="utf-8")
    config = yaml.safe_load(text)
    assert config["blank_issues_enabled"] is True
    private, profile = config["contact_links"]
    for link in (private, profile):
        assert set(link) == {"name", "url", "about"} and all(isinstance(v, str) and v for v in link.values())
    prefix = "https://github.com/renchris/agent-context-sync/blob/main/docs/deploy/"
    assert private["url"].startswith(prefix)
    page, _, anchor = private["url"].removeprefix(prefix).partition("#")
    assert anchor in _anchors(DEPLOY / page), private["url"]
    assert "machine you administer" in private["about"]
    assert profile["url"] == "https://github.com/renchris"


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


@pytest.mark.parametrize(
    "path",
    [ISSUE_TEMPLATES / "config.yml", ISSUE_TEMPLATES / "setup-report.yml", DEPLOY / "setup-feedback.md"],
    ids=lambda p: p.name,
)
def test_feedback_loop_publishes_no_email_address(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert _EMAIL_RE.findall(text) == [] and "mailto:" not in text


def test_readme_setup_report_steps_match_the_code() -> None:
    """The report step runs install.sh --report-only, which writes ~/agent-context/setup-report.md with or
    without agentsync and embeds friction.md (no heading for the agent to write, so none is duplicated: I4);
    the report's own link opens this issue form, and the finish names the report and the link."""
    from agentsync import setup_report  # noqa: PLC0415

    block = _one_prompt_block()
    steps = _prompt_steps()
    assert _commands(_one_prompt_block())[-1] == f"{INSTALL_SH} --report-only"
    assert "append a section" not in block and setup_report.FRICTION_HEADING not in block
    assert "~/agent-context/bring-back.md, the one file I review and copy back" in steps[len(steps)]
    assert "~/agent-context/setup-report.md" in SCRIPTS[0].read_text(encoding="utf-8"), (
        "--report-only's default"
    )
    assert setup_report.ISSUE_URL.startswith("https://github.com/renchris/agent-context-sync/issues/new?")
    assert (ISSUE_TEMPLATES / setup_report.ISSUE_URL.rsplit("=", 1)[1]).is_file()
    assert setup_report.ISSUE_URL in _feedback_page()


def _feedback_page() -> str:
    return (DEPLOY / "setup-feedback.md").read_text(encoding="utf-8")


def test_setup_feedback_page_is_linked_and_covers_the_fix_classes() -> None:
    page = _feedback_page()
    assert "(setup-feedback.md)" in (DEPLOY / "README.md").read_text(encoding="utf-8")
    for fix_class in (
        "prompt wording",
        "installer automation",
        "agentsync code",
        "IT pack",
        "unavoidable OS step",
    ):
        assert fix_class in page, fix_class
    assert "setup-report.yml" in page
    assert "needed help" not in page.split("### Known friction", 1)[0], "v5 triages by kind, not status"


def test_setup_feedback_page_defines_the_computed_outcome() -> None:
    """The page defines each computed outcome and run type (J3, J14) with the rules setup-report applies: the
    installer's step, the kinds that count, the unavoidable turns and the sandbox homes."""
    from agentsync import setup_report  # noqa: PLC0415

    section = _feedback_page().split("### The computed outcome", 1)[1].split("\n### ", 1)[0]
    flat = " ".join(section.split())
    for outcome in ("**Fully one command**", "**Worked with help**", "**Failed at step N**"):
        assert outcome in flat
    assert "**Failed at step 4 (finish)** is never computed" in flat
    for run_type in RUN_TYPES:
        assert f"**{run_type}**" in flat
    assert "`launchd=simulated`" in flat
    for name, expected in (
        ("PROBLEM_KINDS", ("error", "deviation", "prompt")),
        ("INSTALL_STEP", 2),
        ("FOLDER_QUESTION_STEP", 1),
        ("ALLOW_CLICK_STEPS", (1, 2)),
        ("V7_ALLOW_CLICK_STEPS", (1,)),
        ("SANDBOX_HOMES", ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders")),
    ):
        value = getattr(setup_report, name, expected)
        assert value == expected, f"setup_report.{name} changed: update setup-feedback.md section 4"
    assert "no `approval` line, no error that stopped the run, and doctor shows no `[FAIL]`" in flat
    assert "are agent friction" in flat and "they do not change the outcome" in flat and "N is 2" in flat
    assert "no `question` line and no `click` line" in flat, (
        "v6 logs neither the folder question nor an Allow"
    )
    assert "the folder question in step 1 nor the Allow clicks it announces in steps 1 and 2" in flat
    assert "v7 neither that question nor the one Allow click it announces in step 1" in flat, (
        "KISS K11b: v7's step 2 starts no launcher, so it announces no second Allow"
    )
    assert "`Prompt: v5` is judged with v5's six steps" in flat, "a v5 log is still read with its own steps"
    assert all(
        f"`{home}`" in flat for home in ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders")
    )


def test_setup_feedback_page_triages_by_friction_id() -> None:
    """Triage by F<n>, a closing comment mapping each id to a fix class and a commit/test or 'unavoidable:
    <evidence>', the sandbox rule, the private route and the unavoidable list (judge findings I15, I18,
    I19, J17)."""
    page = _feedback_page()
    [comment] = [
        b for b in re.findall(r"^```text\n(.*?)^```$", page, flags=re.DOTALL | re.MULTILINE) if "F1:" in b
    ]
    lines = comment.splitlines()
    assert lines[0].startswith(f"Prompt v<N>, run <{' | '.join(RUN_TYPES)}>, outcome")
    assert any(re.match(r"F\d+: prompt wording -> <commit>, test <", ln) for ln in lines)
    assert any(re.match(r"F\d+: unavoidable: <", ln) for ln in lines)
    assert "**A sandbox or simulated run never counts as a success metric.**" in page
    send = page.split("## 3. Send", 1)[1].split("\n## ", 1)[0]
    assert "no public post needed" in send and "back to the machine you administer" in send
    assert "no contact with the agentsync maintainer" in send
    unavoidable = page.split("## 5. ", 1)[1]
    for step in ("Choosing the folders", "TCC Allow click", "Agent-tool approvals", "IT consent"):
        assert step in unavoidable, step
    assert "Full Disk Access to the" in unavoidable and "pre-allows" in unavoidable
    assert "for the terminal (step 1)" in unavoidable
    assert "`install.sh --confirm-install-agent`" in unavoidable, "KISS K11b: launcher click optional"
    assert "within a 12 s budget" in page  # setup_report.TIME_BUDGET_S (judge finding I20)


def test_setup_feedback_names_every_installer_step() -> None:
    """Section 1 lists the steps install.sh logs (step_start NAME), the report step included (K13)."""
    script = SCRIPTS[0].read_text(encoding="utf-8")
    logged = list(dict.fromkeys(re.findall(r"^\s*step_start ([a-z-]+)\b", script, flags=re.MULTILINE)))
    section = _feedback_page().split("## 1. How a report is produced", 1)[1].split("\n## ", 1)[0]
    [listed] = re.findall(r"per step \(([^)]*); list-folders for a `--list-folders` run\)", section)
    assert sorted([*listed.split(", "), "list-folders"]) == sorted(logged)


_DENIAL_REMEDY = "System Settings > Privacy & Security > Files and Folders"


def test_denied_access_remedy_is_the_files_and_folders_toggle() -> None:
    """A denied Allow (exit 80, TCC_DENIED, or a denied terminal) is fixed by the person's toggle in System
    Settings, as install.sh's NEXT lines say; no page tells the person to run tccutil (K13)."""
    assert _DENIAL_REMEDY in SCRIPTS[0].read_text(encoding="utf-8")
    for page in (DEPLOY / "README.md", DEPLOY / "setup-feedback.md", ROOT / "README.md"):
        text = page.read_text(encoding="utf-8")
        assert "tccutil" not in text, page.name
    deploy = " ".join((DEPLOY / "README.md").read_text(encoding="utf-8").split())
    denied = deploy.split("**Denied or missed:**", 1)[1].split(" - **", 1)[0]
    exit80 = deploy.split("`80`", 1)[1].split("Logs are in", 1)[0]
    unavoidable = " ".join(_feedback_page().split("## 5. ", 1)[1].split())
    for text in (denied, exit80, unavoidable):
        assert _DENIAL_REMEDY in text, text


def test_setup_feedback_says_a_friction_time_is_the_second_of_the_call_that_wrote_it() -> None:
    """N40 (2026-10-06 bring-back, row 7): install.sh stamps each friction line with the second of its own
    call, so lines logged one after another share one time; section 1 says so, so triage does not read it as
    a fault."""
    section = _feedback_page().split("## 1. How a report is produced", 1)[1].split("\n## ", 1)[0]
    section = " ".join(section.split())
    assert (
        "Every time in these lines is the UTC second of the `install.sh` call that wrote the line, not the"
        " moment of the event" in section
    )
    assert "share one time (three lines at one second are not a fault)" in section


def test_known_friction_register_names_real_tests() -> None:
    """Every test the feedback page's Known friction table cites exists in this file (a glob '*' matches at
    least one), so a renamed test cannot leave a stale proof behind."""
    page = _feedback_page()
    table = page.split("### Known friction", 1)[1]
    cited = re.findall(r"`(test_[\w*]+)`", table)
    assert cited, "the register cites its tests"
    names = set(
        re.findall(r"^def (test_\w+)", Path(__file__).read_text(encoding="utf-8"), flags=re.MULTILINE)
    )
    for name in cited:
        assert any(re.fullmatch(name.replace("*", r"\w*"), n) for n in names), name
    ids = re.findall(r"^\| (K\d+) \|", table, flags=re.MULTILINE)
    assert ids == [f"K{i}" for i in range(1, len(ids) + 1)]


# ---- the IT request's placeholders ----------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"<[A-Za-z][A-Za-z0-9 _-]*>")


def test_it_request_placeholders_are_in_its_top_table() -> None:
    """One spelling per placeholder, every one (including entra-app.json's) in the table before the request's
    first section, with where its value comes from (judge finding I10)."""
    text = (DEPLOY / "it-request.md").read_text(encoding="utf-8")
    top = text.split("\n## ", 1)[0]
    rows = re.findall(r"^\| (`<.+?) \| (.+?) \| (.+?) \|$", top, flags=re.MULTILINE)
    table: dict[str, str] = {}
    for names, meaning, source in rows:
        assert meaning.strip() and source.strip()
        for name in re.findall(r"`(<[^`]+>)`", names):
            assert name not in table, f"{name} is listed twice"
            table[name] = source
    used = set(_PLACEHOLDER_RE.findall(text)) | set(
        _PLACEHOLDER_RE.findall(ENTRA.read_text(encoding="utf-8"))
    )
    assert used == set(table), f"not in the table: {used - set(table)}; unused rows: {set(table) - used}"
    assert all(re.fullmatch(r"<[a-z][a-z0-9-]*>", p) for p in table), "lower-case, hyphenated spellings"
    assert {"<it-contact>", "<tenant-id>", "<app-client-id>", "<org>", "<team>"} <= set(table)
    assert table["<it-contact>"] == "you fill in", "no IT contact is invented"
    commands = {p: s for p, s in table.items() if s.startswith("`")}
    assert set(commands) == {"<requester-name>", "<serial>", "<arch>", "<org>", "<arms-today>", "<date>"}
    assert (
        "`id -F`" in table["<requester-name>"] and "`system_profiler SPHardwareDataType`" in table["<serial>"]
    )
    assert "**To:** `<it-contact>`" in text
    assert "`agentsync it-request --out ~/agent-context/it-request-draft.md`" in top
    flat_top = " ".join(top.split()).replace("**CORRECTED", "\0").split("\0", 1)[0]
    assert "one-prompt setup writes" not in flat_top, "KISS K04: setup prompt v7 runs no it-request"


# ---- commits the tree cites --------------------------------------------------------------------------------

_CITING = ("tests", "src", "scripts", "launcher", "probes", "README.md", "docs/design/CONTRACTS.md")
"""Where a cited commit is a pointer the reader follows.  docs/plans and docs/research are left out on
purpose: they are dated records of branch work and name branch commits as such."""


def _git(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], input=stdin, capture_output=True, text=True, check=False
    )


def test_a_cited_commit_is_on_the_trunk() -> None:
    """B13 N38: three files cited the pre-rebase twin of a commit, an object that lives only in the clone the
    rebase ran in, so ``git show <sha>`` failed everywhere else.  Every hex word in the citing files that
    names a commit of this repository names an ancestor of HEAD.  A commit this clone does not hold names
    nothing here (a depth-1 CI checkout, another project's pin), so the check bites in the clone that still
    holds the stale twin, which is the one a rebase leaves behind."""
    if _git("rev-parse", "--git-dir").returncode != 0:
        pytest.skip("not a git checkout")
    cited: dict[str, set[str]] = {}
    for name in _git("ls-files", "-z", "--", *_CITING).stdout.split("\0"):
        try:
            text = (ROOT / name).read_text(encoding="utf-8") if name else ""
        except (OSError, UnicodeDecodeError):
            continue
        for word in re.findall(r"(?<![0-9A-Za-z])[0-9a-f]{7,40}(?![0-9A-Za-z])", text):
            cited.setdefault(word, set()).add(name)
    assert cited, "the citing files hold hex words (the scan found none, so it is broken)"
    words = sorted(cited)
    answers = _git("cat-file", "--batch-check", stdin="".join(f"{w}^{{commit}}\n" for w in words))
    rows = answers.stdout.splitlines()
    assert len(rows) == len(words), answers.stderr
    off_trunk = []
    for word, row in zip(words, rows, strict=True):
        sha, _, kind = row.partition(" ")
        if not kind.startswith("commit ") or _git("merge-base", "--is-ancestor", sha, "HEAD").returncode != 1:
            continue
        off_trunk.append(f"{word} in {', '.join(sorted(cited[word]))}")
    assert off_trunk == []


# ---- what the contract states, against the code ------------------------------------------------------------

CONTRACTS = ROOT / "docs" / "design" / "CONTRACTS.md"

_RETIRED_TEST_NAMES = {
    "test_an_empty_git_proxy_takes_no_line_after_it": "§16.40 says it was merged into another test",
    "test_an_inbox_file_the_walk_withheld_is_in_the_count_until_a_sync_lists_it": "§16.45 R101's dated line",
    "test_under_a_label_rule_a_msg_is_the_no_converter_stub": "§16.54 Q78's dated line",
    "test_a_proxy_git_scopes_to_fluidaudios_path_on_github_routes_the_seed": "§16.60 F78's dated line",
    "test_the_uv_a_fix_names_is_the_one_install_sh_finds": "§16.120 S29's §16.126 T54 dated line",
    "test_doctor_loads_convert_through_cycle_and_its_words_say_so": "§16.162 Z56's §16.168 A66 dated line",
    "test_the_entry_docstring_says_since_when_a_failed_probe_exits_1": "§16.162 Z58's §16.168 A66 dated line",
}
"""Test names CONTRACTS.md cites as history: a dated line beside each says the test is gone or replaced."""


def _contracts_section(heading: str, following: str) -> str:
    text = CONTRACTS.read_text(encoding="utf-8")
    start = text.index(heading)
    return text[start : text.index(following, start)]


def test_a_test_the_contract_cites_exists() -> None:
    """B13 round 4 Q62: §16.47 cited a test the commit that dropped the wheel pre-build had deleted, and no
    gate read the name.  Every backticked test name in CONTRACTS.md is a test under tests/, or the start of
    one's name (`test_sg04` names a group), or a name a dated line retires (:data:`_RETIRED_TEST_NAMES`)."""
    defined = {
        name
        for path in (ROOT / "tests").rglob("*.py")
        for name in re.findall(r"def (test_\w+)", path.read_text())
    }
    # B13 round 23 A66: the `test_<file>.py::<name>` form too, which held 270 names this scan never read
    cited = set(
        re.findall(r"`(?:(?:tests/)?[\w/]+\.py::)?(test_\w+)`", CONTRACTS.read_text(encoding="utf-8"))
    )
    assert len(cited) > 100, "the contract cites tests (the scan found few, so it is broken)"
    missing = sorted(
        name
        for name in cited - defined - set(_RETIRED_TEST_NAMES)
        if not any(found.startswith(name) for found in defined)
    )
    assert missing == []
    assert set(_RETIRED_TEST_NAMES) <= cited - defined, (
        "a retired name is cited and gone, or the list is stale"
    )
    # B13 round 5 F84: §16.55 counted "one of two names" while the list held three; it names the list instead.
    text = " ".join(CONTRACTS.read_text(encoding="utf-8").split())
    assert "one of two names a dated line retires" not in text
    assert "or a name a dated line retires, listed in `_RETIRED_TEST_NAMES`" in text


def _contracts_numbered_sections() -> list[tuple[str, str]]:
    """CONTRACTS.md as (section number, text) in order: a `## N.` or `### N.M` heading starts one, and an
    unnumbered heading (§15's module blocks) stays in the numbered section above it."""
    sections: list[tuple[str, list[str]]] = [("0", [])]
    for line in CONTRACTS.read_text(encoding="utf-8").splitlines():
        head = re.match(r"#{2,3} (\d+(?:\.\d+)?)[. ]", line)
        if head:
            sections.append((head.group(1), []))
        sections[-1][1].append(line)
    return [(number, "\n".join(lines)) for number, lines in sections]


def test_a_round_item_names_the_sections_that_hold_its_dated_lines() -> None:
    """B13 round 5 F85: round 4's text pointed to the wrong sections for its own dated lines (`§5` for a line
    in §15's doctor reference and another in §16.12, a `§16.4x` never filled in, R33 under §16.48).  For each
    round 4 item whose paragraph says "Dated line(s) ... §X", every § it names holds an `Amended 2026-10-12,
    §16.5N <item>` line, and every section holding one is named, or holds an "at R<n>" the sentence names.
    And Q78's "amends §16.48 R87 and R33" put R33 in the wrong section: each R<n> it amends follows its §."""
    sections = _contracts_numbered_sections()
    checked = 0
    for home in ("16.50", "16.51", "16.52", "16.53", "16.54", "16.55"):
        body = dict(sections)[home]
        for para in re.split(r"\n\s*\n", body):
            item = re.match(r"\*\*([QR]\d+)\b", para)
            flat = " ".join(para.split())
            pointer = re.search(r"Dated lines? (?:in|at) ([^.]*(?:\.\d+[^.]*)*)\.", flat)
            if not item or not pointer:
                continue
            marker = re.compile(rf"amended 2026-10-12, §{re.escape(home)} {item.group(1)}\b", re.IGNORECASE)
            holding = {
                number
                for number, text in sections
                if number != home and marker.search(" ".join(text.split()))
            }
            cited = set(re.findall(r"§(\d+(?:\.\d+)?)", pointer.group(1)))
            at_r = re.findall(r"at (R\d+)(?! \()", pointer.group(1))
            assert holding, item.group(1)
            assert cited <= holding, (item.group(1), "names a section holding no dated line", cited - holding)
            for number in holding - cited:
                assert any(re.search(rf"\b{r}\b", dict(sections)[number]) for r in at_r), (
                    item.group(1),
                    number,
                )
            checked += 1
    assert checked >= 4, "the scan found the round 4 pointers (it found few, so it is broken)"
    q78 = " ".join(_contracts_section("**Under a label rule a `.msg` that holds MIME text", "\n\n").split())
    amends = re.search(r"amends ([^)]*)\)", q78)
    assert amends is not None
    pairs = re.findall(r"(§[\d.]+ )?(R\d+)", amends.group(1))
    assert pairs and all(section for section, _r in pairs), "every R<n> it amends follows its own §"
    for section, r in pairs:
        assert re.search(rf"\b{r}\b", dict(sections)[section.strip().lstrip("§")]), (section, r)


def test_the_speech_marker_reason_says_the_freeze_allows_a_hidden_command() -> None:
    """B13 round 5 F101: §16.48 R140 kept no skip marker partly because "the frozen CLI has no command to
    clear a marker", while round 4's Q71 built the hidden `exclude-empty` under the same freeze.  The reason
    keeps its words (it was true of that round) and a dated line after it says the freeze no longer rules one
    out, and why the decision stands without it."""
    cli = (ROOT / "src" / "agentsync" / "cli.py").read_text(encoding="utf-8")
    assert re.search(r'"exclude-empty",\n(?:.*\n){1,4}?\s+hidden=True,', cli), "a hidden command exists"
    r140 = " ".join(
        _contracts_section("the frozen CLI has no command to clear", "#### PDF pictures and diagrams").split()
    )
    assert (
        "**Amended 2026-10-12, §16.61 F101 (by §16.51 Q71):** the freeze no longer rules out a clear" in r140
    )
    assert "The decision stands without it." in r140
    assert '"tries the build again on its next run", "rebuilds it") stay true' in r140


def test_section_7_routes_every_suffix_where_the_registry_does() -> None:
    """B13 round 4 Q56: §7's routing list, read in order so a later SUPERSEDED line wins, gave `.vtt` to
    `text-plain` and named no route for recordings.  With an OCR engine, the media helper and recordings on,
    every suffix the default registry claims is routed in §7 to that converter, and §7 routes no other.
    B13 round 5 F90: a label rule moves `.msg` to `msg-mime` and removes the image routes, and a pass with no
    policy could not see that, so §7 named no route for `msg-mime`.  The test now reads §7 twice, by its
    SUPERSEDED clauses: without a rule it skips a clause "with a label rule", and under a rule one for "no
    label rule" (the recordings clause says "under a label rule" and holds in both)."""
    from agentsync.config import ConvertConfig  # noqa: PLC0415
    from agentsync.convert.media import MediaEngine  # noqa: PLC0415
    from agentsync.convert.ocr import OcrEngine  # noqa: PLC0415
    from agentsync.convert.registry import Registry  # noqa: PLC0415
    from agentsync.policy import PolicyConfig  # noqa: PLC0415

    clauses = re.split(r"(?=\*\*SUPERSEDED )", _contracts_section("## 7. Conversion", "## 8. "))
    for rule, skip in ((None, "with a label rule"), (PolicyConfig(refuse_unlabelled=True), "no label rule")):
        routes: dict[str, str] = {}
        for clause in clauses:
            if skip in " ".join(clause.split()):
                continue
            for match in re.finditer(r"`(\.[^`]+)`\s*→\s*`([a-z0-9-]+)`", clause):
                routes.update(dict.fromkeys(match.group(1).split(), match.group(2)))
        reg = Registry.default(
            ConvertConfig(recordings=True),
            policy=rule,
            ocr=OcrEngine(Path("/nowhere/fake-ocr"), name="paper-vision", revision=2, helper_version="0.3.0"),
            media=MediaEngine(Path("/nowhere/fake-media"), name="paper-media", helper_version="0.1.0"),
        )
        claimed = {ext: reg.for_name(f"x{ext}").converter_id for ext in reg.extensions()}  # type: ignore[union-attr]
        assert {".vtt", ".mp4", ".potx", ".msg"} <= set(claimed)
        assert (".png" in claimed) == (rule is None), "a label rule removes the image routes"
        assert claimed[".msg"] == ("msg-olefile" if rule is None else "msg-mime")
        assert routes == claimed, rule


def test_the_contracts_class_table_names_every_quarantine_class() -> None:
    """B13 round 4 Q57: the `quarantine_class` table had no row for `withheld by label rule`.  Its Class
    column names every value of `QUARANTINE_CLASSES`; `(OCR found no text)` after `no text layer (OCR not
    run)` is short for `no text layer (OCR found no text)`."""
    from agentsync import setup_report  # noqa: PLC0415

    table = _contracts_section("**`quarantine_class`.**", "\n\n**OCR.**")
    named: set[str] = set()
    for row in re.findall(r"^\| (`.+?`) \|", table, flags=re.MULTILINE):
        stem = ""
        for cell in re.findall(r"`([^`]+)`", row):
            name = stem + cell if cell.startswith("(") and stem else cell
            stem = name.split("(", 1)[0] if "(" in name else ""
            named.add(name)
    assert named == set(setup_report.QUARANTINE_CLASSES)


def test_every_section_quoting_the_old_empty_lock_body_text_states_the_constant() -> None:
    """B13 round 4 Q50 and Q64: §9 step 2 quoted the empty-lock-body text that §16.45 R50 replaced, with no
    dated line.  A section of CONTRACTS.md that quotes the old text also states `EMPTY_BODY_HOLDER`."""
    from agentsync.ops.lock import EMPTY_BODY_HOLDER  # noqa: PLC0415

    old = "an unrecorded holder (install.sh while it reinstalls agentsync, or a check for an instant)"
    text = " ".join(CONTRACTS.read_text(encoding="utf-8").split())
    sections = re.split(r" (?=#{2,3} \d)", text)
    quoting = [section for section in sections if old in section]
    assert quoting, "the old text is quoted somewhere as history (the split found none, so it is broken)"
    assert [section[:40] for section in quoting if EMPTY_BODY_HOLDER not in section] == []


def test_the_contracts_suffix_count_for_the_log_scrub_test_is_the_converters() -> None:
    """B13 round 4 Q54: a clause citing the per-suffix scrub test said 40 suffixes after `.potx` made 41.
    The last count each clause gives (a dated amendment's, when it has one) is the number of suffixes a
    converter claims."""
    from agentsync.config import ConvertConfig  # noqa: PLC0415
    from agentsync.convert.image import ImageConverter  # noqa: PLC0415
    from agentsync.convert.recording import RecordingConverter  # noqa: PLC0415
    from agentsync.convert.registry import Registry  # noqa: PLC0415

    suffixes = {*Registry.default(ConvertConfig()).extensions(), *ImageConverter.extensions}
    suffixes |= {*RecordingConverter.extensions}
    clauses = re.findall(
        r"`test_a_log_line_never_names_a_file_a_converter_reads`([^)]*)\)",
        CONTRACTS.read_text(encoding="utf-8"),
    )
    counts = [re.findall(r"(\d+) (?:suffixes|since)", clause) for clause in clauses]
    assert counts and all(counts), clauses
    assert {int(found[-1]) for found in counts} == {len(suffixes)}


def _page(relative: str) -> str:
    return " ".join((ROOT / relative).read_text(encoding="utf-8").split())


def test_step_1s_failed_config_exception_reads_the_installers_words() -> None:
    """B13 round 10 L11 (lane RB) and lane P's v19: for a config the installed agentsync failed while reading,
    install.sh's NEXT keeps the words the prompt's second exception reads ("whose folders could not be read")
    and says the installed agentsync failed while reading it, with no claim that step 2 keeps the folders;
    the prompt tells the agent not to say step 2 keeps them when the NEXT says so."""
    readme = _page("README.md")
    assert "this Mac has a config whose folders could not be read" in readme
    assert (
        "If that NEXT: line says the installed agentsync failed while reading the config, show me that line "
        "in its own words and do not say step 2 keeps my folders" in readme
    )
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    [clause] = re.findall(
        r'(?m)^\t\tkept="(; this Mac has a config .*?failed while reading it.*)"$', installer
    )
    assert clause.startswith("; this Mac has a config ($CONFIG) whose folders could not be read: ")
    assert "the installed agentsync failed while reading it" in clause and "keeps" not in clause


def test_data_governance_states_the_secret_scan_and_msg_rules_the_code_runs() -> None:
    """B13 round 4 Q58 and Q69: the governance page limited the built-in secret patterns to files once held
    back (since R76 they read every page), said nothing of an archive copy the rescan matches (R74), called a
    withheld `.msg` page a `no converter` stub (R33 gave it its own words), and said any `.msg` with an
    encrypted Office attachment is screened as a whole, which since R87 holds only for an Outlook-format one.
    The README's archive guarantee names the one exception."""
    from agentsync import publish  # noqa: PLC0415

    page = _page("docs/deploy/data-governance.md")
    assert "agentsync's built-in patterns read every page" in page
    assert "by the built-in patterns too" not in page
    assert "A `[governance] archive` copy they match is deleted with its sidecars" in page
    assert "Save the message as .eml" in Path(publish.__file__).read_text(encoding="utf-8")
    assert "a `[policy]` label rule withholds it and that it can be saved as `.eml`" in page
    assert "an Outlook-format `.msg` that carries an encrypted Office attachment is screened" in page
    assert "A `.msg` that holds MIME text is read as an `.eml`" in page
    readme = _page("README.md")
    assert "reaping never removes it" in readme, "the archive guarantee stays"
    assert "The one exception is a copy that the secret scan's new rules match, which is deleted" in readme


def test_the_docs_state_the_msg_purge_and_banner_rules_of_round_4() -> None:
    """B13 round 5 F75 and F81 (one defect): since round 4's Q78 a label rule routes `.msg` to `msg-mime`,
    so every `.msg` on this Mac is fetched and one that holds MIME text is screened and read as an `.eml`,
    while the docs said no `.msg` is fetched or converted under a rule and none has its label read.  F87: Q45
    retired "runs once the file is gone again", which read as a run nobody starts.  F88, amended by F94 and
    round 6's G76 and G77: a stub or tombstone written before Q9 is rendered again once, so none keeps the
    heading above the banner."""
    from agentsync.config import ConvertConfig  # noqa: PLC0415
    from agentsync.convert.registry import Registry  # noqa: PLC0415
    from agentsync.policy import PolicyConfig  # noqa: PLC0415

    reg = Registry.default(ConvertConfig(), policy=PolicyConfig(refuse_unlabelled=True))
    assert reg.for_name("x.msg").converter_id == "msg-mime"  # type: ignore[union-attr]
    page = _page("docs/deploy/data-governance.md")
    assert "is not fetched or converted" not in page
    assert "No label is read from an Outlook `.msg`" not in page
    assert "has its `msip_labels` header read as an `.eml`'s" in page
    assert "a `.msg` on this Mac is read to staging, because only its bytes say which kind it is" in page
    # B13 round 5 F73: one that reading means a download for is withheld by its name
    assert "online-only, on a Graph source or one the OS will not hydrate is not downloaded" in page
    assert "as an `.eml` (an excluded label, or no label under `refuse_unlabelled`, refuses it)" in page
    assert "A policy change turns a binary `.msg` page published before it into that stub" in page
    for readme in ("README.md", "docs/deploy/README.md"):
        text = _page(readme)
        assert "is set a `.msg` is not converted" not in text, readme
        assert "(one saved as MIME text is screened and read like an `.eml`)" in text, readme
        assert "Only `.eml`, a `.msg` saved as MIME text, `.pdf`" in text, readme
    feedback = _page("docs/deploy/setup-feedback.md")
    assert "so no converter is registered for it" not in feedback
    assert "a `.msg` on this Mac that holds MIME text is screened and read as an `.eml`" in feedback
    # B13 round 6 G62: a .msg not on this Mac is withheld by its name too (B13 round 5 F73)
    assert (
        "a binary `.msg`, or any `.msg` not on this Mac (online-only, on a Graph source, or one the OS will "
        "not hydrate, which is not downloaded to tell), is `withheld by label rule` instead" in feedback
    )
    assert "unless `~/.local/bin/agentsync materialise PATH` names it" in feedback
    assert "runs once the file is gone again" not in page
    assert "once the file is gone, the next `agentsync purge --queue` runs it" in page
    # B13 round 6 G76, G77: a redacted stub and an old tombstone are rendered again once too
    assert "may carry a one-line title heading above it" not in page
    assert (
        "A stub or tombstone written before the banner came first (a credential, path or redacted stub"
        in page
    )
    # B13 round 7 H53, H59 (one defect): since round 6's G29 a withheld `.msg`'s page `reason:` and its
    # QUARANTINE.tsv row read `withheld by label rule`; only the stored reason keeps `no converter for .msg`.
    from agentsync.publish import _withheld_reason  # noqa: PLC0415

    assert "reason stays `no converter for .msg`" not in page
    assert f"`_sync/QUARANTINE.tsv` row both read `{_withheld_reason('.msg')}`" in page
    assert "only the reason agentsync stores keeps `no converter for .msg`" in page


def test_setup_feedback_states_the_report_rules_of_round_3() -> None:
    """B13 round 4 Q65 and Q68: the reviewer's guide lagged four round 3 changes: every entry a purge run
    keeps has a `note: stays queued:` line, a held one included, and a real run ends one of three ways
    (`cli._cmd_purge`); images join `withheld by label rule` only once the OCR part reads ready (R33); an
    attempt that names no agent reads approvals as not observable (R24); and an exclude line shows agentsync's
    own globs as written (R8)."""
    page = _page("docs/deploy/setup-feedback.md")
    cli = (ROOT / "src" / "agentsync" / "cli.py").read_text(encoding="utf-8")
    forms = {
        # B13 round 16 T32: the counts by number, "1 stays queued while its file is listed" for one
        "`N queued purges run; M still queued`": "queued purge')} run; {len(left)} still queued{also}",
        "`N queued purges run; M still queued (K of them while their file is listed)`": (
            " ({len(kept)} of them while {'its' if len(kept) == 1 else 'their'} file is listed)"
        ),
        "`N queued purges run; K stay queued while their file is listed`": (
            'else "stay queued while their file is listed"'
        ),
    }
    for quoted, printed in forms.items():
        assert quoted in page and printed in cli, quoted
    assert "are exactly those with a `note: stays queued:` line" in page
    assert "`note: stays queued: purge suspended by legal/records hold: ...`" in page
    assert "a `.msg` or an image with no converter is `withheld by label rule`" not in page
    # B13 round 9 K59: since round 8 J52 an image's class follows its stub page, not the OCR probe
    assert "So is an image, but only when the OCR part's `helper:` reads `ready`" not in page
    assert "or the attempt names no agent (no `Agent:` value)" in page
    assert "agentsync's own globs show as written" in page


def test_setup_feedback_states_the_report_rules_of_round_4() -> None:
    """B13 round 5 F60 and F82 (one defect): since round 4's Q80 the installer's shell fallback report shows
    an exclude list entry by entry, as the agentsync report does, and Q21/Q22 split a header-less step 1 error
    by its stated words, with two labels the page never named.  F67: since Q49 every runnable fix doctor
    prints names `~/.local/bin/agentsync`, while the page still quoted the bare command (the K table's history
    rows keep what was true then).  F91: the placeholder table had no row for `<tmp>`, `<hash-N>` or
    `<commit-N>`, which the report's legend names."""
    from agentsync import setup_report  # noqa: PLC0415
    from agentsync.ops import doctor  # noqa: PLC0415

    page = _page("docs/deploy/setup-feedback.md")
    assert "the installer's shell fallback report, show the whole list" not in page
    assert "since round 4, in the installer's shell fallback report" in page
    for label in setup_report._HEADERLESS_LABEL.values():
        assert label in page, label
    # B13 round 6 G64: since round 5 F23 a prefixed step 1 error after a header is a new attempt at any gap
    # (setup_report.parse_friction's orphan()); the page still gave round 4's rule, and this pin was a
    # substring of the stale sentence. The version, agent and gap checks follow a header-less one. An
    # `Attempt:` line alone is no header: the error stays (measured in the round 6 review).
    head = "Attempt: 2026-10-13T10:00:00Z\n"
    tail = (
        "2026-10-13T10:01:00Z | end | finished\n"
        "2026-10-13T10:03:00Z | step 1 | error | prompt v15, Tool-A: x\n"
    )
    # B13 round 7 H64: an `Outcome:` or `Run:` line (a v4 log's) is a header too (setup_report.FRICTION_KEYS)
    for header, count in (
        (head + "Prompt: v15\nAgent: Tool-A\n", 2),
        (head + "Outcome: Failed at step 2\n", 2),
        (head + "Run: 1\n", 2),
        (head, 1),
    ):
        assert len(setup_report.parse_friction(header + tail).attempts) == count, header
    assert setup_report.FRICTION_KEYS == ("Prompt", "Agent", "Outcome", "Run")
    flat = " ".join(page.split())
    header = (
        "a header (a `Prompt:`, `Agent:`, `Outcome:` or `Run:` line; the last two appear only in a v4 log)"
    )
    assert "(a `Prompt:` or `Agent:` line)" not in flat
    assert f"the open attempt has {header}, at any gap" in flat
    assert f"always starts its own attempt when the open attempt has {header}" in flat
    contracts = " ".join(CONTRACTS.read_text(encoding="utf-8").split())
    for section in ("16.58", "16.67"):
        dated = " ".join(dict(_contracts_numbered_sections())[section].split())
        assert "§16.73 H64:** a header is a `Prompt:`, `Agent:`, `Outcome:` or `Run:` line" in dated or (
            "§16.73 H64:** the header is a `Prompt:`, `Agent:`, `Outcome:` or `Run:` line" in dated
        ), section
    assert "a header (a Prompt:, Agent:, Outcome: or Run: line)" in contracts  # §16.64 G51
    assert "header or an `Attempt:` line" not in flat, "an Attempt: line alone splits nothing (orphan())"
    assert "after a headed attempt, at any gap (2 minutes after the end line is enough)" in flat
    # B13 round 15 S54: since round 14 Q28 (orphan()) a strict-form stated error after a header-less attempt
    # with stated words splits at any gap, even in the same words; the page and its friction.md row kept
    # round 4's version, agent and 10-minute comparisons, and this pin held the stale sentence
    assert "only when its version or agent differs from them" not in flat
    assert "with a version or agent that differs from them" not in flat
    assert (
        "Since round 14 (Q28) it also does so after a header-less attempt that already has stated words, at"
        " any gap and even in the same words, when the line is in install.sh's strict"
        " `<time> | step 1 | error |` form"
    ) in flat
    assert (
        "or the attempt has no header but stated words of its own and the line is in the strict"
        " `<time> \\| step 1 \\| error \\|` form, at any gap (since round 14, Q28)"
    ) in flat
    stated = "2026-10-13T10:00:00Z | step 1 | error | prompt v15, Tool-A: x\n"
    again = "2026-10-13T10:04:36Z | step 1 | error | prompt v15, Tool-A: y\n"
    assert len(setup_report.parse_friction(stated + again).attempts) == 2
    assert len(setup_report.parse_friction(stated + again.replace("| step 1", "|  step 1")).attempts) == 1
    assert (
        "with a version or agent that differs from that attempt's or more than 10 minutes after" not in flat
    )
    assert "when the open attempt has a header or stated words of its own and the error's version" not in flat
    assert "the shell fallback report names only `$TMPDIR`" in page
    code = Path(doctor.__file__).read_text(encoding="utf-8")
    assert doctor.BIN == "~/.local/bin/agentsync"
    for printed in (
        "run {BIN} sync, which makes these owner-only",
        "run {BIN} status again as it is",
        "run {BIN} status -v, which prints the traceback",
        "report it: {BIN} status -v prints where",
    ):
        assert printed in code, printed
    for quoted in (
        "(fix: run ~/.local/bin/agentsync sync, which makes these owner-only)",
        "`run ~/.local/bin/agentsync status again as it is`",
        "`~/.local/bin/agentsync status -v`, which prints the traceback",
        "report it: ~/.local/bin/agentsync status -v prints where",
    ):
        assert quoted in page, quoted
    for bare in (
        "`agentsync status -v`, which",
        "report it: agentsync status",
        "(fix: agentsync sync",
        "`agentsync status (",
    ):
        assert bare not in page, bare
    raw = (DEPLOY / "setup-feedback.md").read_text(encoding="utf-8")
    table = "\n".join(line.split(" | ", 1)[0] for line in raw.splitlines() if line.startswith("| `"))
    for entry in setup_report._LEGEND.values():
        placeholder = entry.split(" ", 1)[0]
        assert f"`{placeholder}`" in table, placeholder


def test_the_docs_state_what_round_5_changed() -> None:
    """B13 round 6: docs that lagged round 5's code.  G66: data-governance.md listed a label-only save as a
    known limit, while §16.60 F72 screens one under a label rule.  G70: setup-feedback.md's placeholder table
    said `(source N)` is in the evidence parts only, though the Configuration section's excludes line uses it,
    and that agentsync's own source ids are never redacted, though one is under CloudStorage.  G61, G68, G69,
    G72: CONTRACTS sections round 5 changed carry dated lines saying how (§16.54 Q52 and Q77 by F78 and F77;
    §16.51 Q9 and §16.61 by F94; §16.61 by F73; §16.28 by F23), and install.sh's comment on the stopped-copy
    reader says what F23 changed."""
    governance = _page("docs/deploy/data-governance.md")
    limits = governance.split("**Known limits:**", 1)[1].split("**Images and on-device OCR:**", 1)[0]
    assert "re-screened only when the content changes or the policy changes" not in governance
    assert "label" not in limits.replace("extractSensitivityLabels", "").replace("Graph-side label", "")
    assert (
        "Under a label rule, a save that changes only a file's label, not its content, is screened at the "
        "next sync that fetches the file, and its page is withheld or restored to match the new label "
        "(CONTRACTS §16.60 F72)." in governance
    )
    feedback = _page("docs/deploy/setup-feedback.md")
    assert (
        "except one of agentsync's own words such as `inbox` or `mail` whose folder is not under "
        "`~/Library/CloudStorage` (in both reports)" in feedback
    )
    assert (
        "in the evidence parts and the Configuration section's `source excludes beyond agentsync's own` line "
        "only" in feedback
    )
    assert "the shell fallback report's excludes line names every source this way" in feedback
    contracts = " ".join(CONTRACTS.read_text(encoding="utf-8").split())
    for line in (
        "**Amended 2026-10-13, §16.67 G61 (by §16.60 F78):** the query is `git config --type=path "
        "--get-urlmatch http <NEMO_URL>`",
        "**Amended 2026-10-13, §16.67 G61 (by §16.60 F77):** fixed text is de-duplicated a shape at a time",
        "**Amended 2026-10-13, §16.67 G61:** F78 amends §16.54 Q52",
        "**Amended 2026-10-13, §16.67 G68 (by §16.60 F94):** a credential or path stub is rendered again",
        "**Amended 2026-10-13, §16.67 G68 (by §16.60 F94):** credential and path stubs are rendered",
        "**Amended 2026-10-13, §16.67 G69 (by §16.60 F73):** a `.msg` that reading means a download for",
        "**Amended 2026-10-13, §16.67 G72 (by §16.58 F23):** a step 1 error that starts with",
        "**Amended 2026-10-13, §16.67 G72:** F23 amends §16.28's `friction_attempt_stopped` rule",
    ):
        assert line in contracts, line
    sections = dict(_contracts_numbered_sections())
    assert "§16.67 G72 (by §16.58 F23)" in " ".join(sections["16.28"].split())
    for number, item in (("16.54", "G61 (by §16.60 F78)"), ("16.54", "G61 (by §16.60 F77)")):
        assert f"§16.67 {item}" in " ".join(sections[number].split()), item
    assert "§16.67 G68 (by §16.60 F94)" in " ".join(sections["16.51"].split())
    script = " ".join(SCRIPTS[0].read_text(encoding="utf-8").replace("#", " ").split())
    assert (
        'so, at any gap, is a step 1 error that starts with "prompt vN, <agent>:", since that prefix is '
        "written only before --log-start and a stopped attempt has a header" in script
    )


def test_a_round_5_item_that_amends_another_has_a_dated_line_there() -> None:
    """B13 round 6 G61: §16.60 said F77 amends §16.54 Q77 and F78 changed Q52's query, but neither had a dated
    line in §16.54, and the pointer test reads only round 4's sections.  For each §16.60 item whose heading
    says "amends §N <item>" (and F78, whose dated line says it amends Q52), section N holds a dated line that
    names §16.60 and the item, or one of its pair."""
    sections = _contracts_numbered_sections()
    body = dict(sections)["16.60"]
    amends = [("F78", "16.54", "Q52")]
    for para in re.split(r"\n\s*\n", body):
        flat = " ".join(para.split())
        head = re.match(r"\*\*[^*]*\((F\d+(?:, F\d+)*); amends §(\d+\.\d+) ([QR]\d+)\)\.\*\*", flat)
        if head:
            amends.extend((f, head.group(2), head.group(3)) for f in head.group(1).split(", "))
    assert len({(s, q) for _f, s, q in amends}) >= 5, "the scan found §16.60's amends (few, so it is broken)"
    for section, item in {(s, q) for _f, s, q in amends}:
        flat = " ".join(dict(sections)[section].split())
        dated = [
            chunk
            for chunk in re.split(r"(?=Amended 2026-10-1\d, )", flat)
            if re.match(r"Amended 2026-10-1\d, (?:§16\.60\b|§16\.6\d G\d+ \(by §16\.60 )", chunk)
        ]
        named = {f for f, s, q in amends if (s, q) == (section, item)}
        # A dated line's marker and its text end at the next bold run.
        said = [" ".join(chunk.split("**")[:2]) for chunk in dated]
        found = [f for f in named if any(re.search(rf"\b{f}\b", line) for line in said)]
        assert found, (section, item, sorted(named))


def test_setup_feedback_states_the_report_rules_of_round_6() -> None:
    """B13 round 7: the reviewer's guide lagged round 6's report code.  H43, H61, H67 (one defect): §16.64
    G23's `stopped at step 1 (<note>)` outcome was missing from the computed outcomes, whose `Failed at step
    N` fallback and agent-friction sentence called such an attempt failed, and the `title` row said the title
    holds the outcome, while since G12 it names the step as the attempt's own prompt does."""
    from agentsync import setup_report  # noqa: PLC0415
    from agentsync.setup_report import Outcome, compute_outcome, parse_friction  # noqa: PLC0415

    page = _page("docs/deploy/setup-feedback.md")
    head = "Attempt: 2026-10-13T10:00:00Z\nPrompt: v15\nAgent: Tool-A\n"
    for what in ("stopped to wait for the folder answer", "listed the folders by hand"):
        [attempt] = parse_friction(f"{head}2026-10-13T10:01:00Z | step 1 | deviation | {what} | -\n").attempts
        stopped = compute_outcome(attempt, ())
        assert stopped.kind == "stopped", what
        assert f"`{stopped.text}`" in page, what
        assert f"`{stopped.form_label}`" in page
    assert "**Stopped at step 1**: no install run, no `error` line and no `end | finished` line" in page
    assert "failing that the last step logged (unless the attempt only stopped at step 1, below)" in page
    assert "the form has no stopped option, so it records a stopped attempt as step 1's failure" in page
    assert "and they do not change the outcome. They" not in page
    # B13 round 8 J58: since round 7 H40 a step 1 question, click, approval or prompt line beside a deviation
    # also reads stopped (`_WAIT_OK_KINDS`); the guide said every line must be a deviation
    assert "every line the attempt logged is a step 1 `deviation`" not in page
    assert "except that an attempt whose only lines are step 1 deviations reads 'stopped'" not in page
    assert (
        "except that an attempt that only stopped at step 1 (above) reads 'stopped' rather than 'failed'"
        in page
    )
    body = "2026-10-13T10:01:00Z | step 1 | approval | ran the folder list | -\n"
    body += "2026-10-13T10:01:00Z | step 1 | deviation | stopped to wait for the folder answer | -\n"
    [attempt] = parse_friction(head + body).attempts
    mixed = compute_outcome(attempt, ())
    assert mixed.kind == "stopped" and mixed.text == "stopped at step 1 (waiting for the folder answer)"
    assert f"(`{mixed.why[0].split('; ', 1)[1]}`)" in page
    stopped_rule = page[page.index("**Stopped at step 1**") : page.index("A line logged after an attempt's")]
    for kind in setup_report._WAIT_OK_KINDS:
        assert f"`{kind}`" in stopped_rule, kind
    assert '"Setup report: " and the outcome, run type and prompt version)' not in page
    # a v7 or later step 2 is named as the prompt names it, "install and start", in both (B13 round 14 Q25)
    failed = Outcome("failed", 2, (), 15)
    assert failed.title_label == failed.form_label == "Failed at step 2 (install and start)"
    flat = " ".join(page.split())
    assert f"step 2 is `{str(failed.title_label).split('(')[1].rstrip(')')}`" in flat
    assert f"such as `{failed.form_label}`" in flat
    v5 = Outcome("failed", 3, (), 5)  # a title that differs from its field: v5's own six steps
    assert v5.title_label != v5.form_label == failed.form_label
    assert f"its step 3 reads `{v5.title_label}`" in flat
    stopped = Outcome("stopped", 1, (), 15, "waiting for the folder answer")
    assert (
        f"A stopped attempt's title reads `{stopped.title_label}` and its field `{stopped.form_label}`"
        in page
    )
    contracts = _contracts_section("### 16.64 ", "### 16.65 ")
    assert '**Amended 2026-10-13, §16.73 H67:** "a v7 to v14 attempt" is every v7 or later' in contracts
    h43 = " ".join(_contracts_section("### 16.73 ", "### 16.74 ").split())
    assert "**Amended 2026-10-14, §16.79 J58:** the condition is at least one step 1 deviation" in h43
    # B13 round 9 K66: since round 8 J9 a --log-start stop the same agent ran again within the window reads
    # stopped despite its error and end line; the guide's bullet named no exception
    stop = (
        "Attempt: 2026-10-13T10:00:00Z\nPrompt: v61\nAgent: Tool-A\n2026-10-13T10:00:00Z | step 1 | error "
        "| install.sh --log-start: the pasted setup prompt is v61 and this installer is for setup prompt "
        "v18: this checkout is older than the prompt, or the --log-start value was changed; setup stopped "
        "(exit 2) | -\n"
        "2026-10-13T10:00:00Z | end | finished\nAttempt: 2026-10-13T10:00:18Z\nPrompt: v18\nAgent: Tool-A\n"
    )
    rerun = setup_report._rerun_note(parse_friction(stop), 0)
    assert rerun is not None and rerun[0] == "stopped"
    assert f"`{Outcome('stopped', 1, (), 61, rerun[1]).text}`" in stopped_rule
    # B13 round 9 K11: an older copy's stop stays failed, with a note naming both readings
    older = stop.replace("Prompt: v61", "Prompt: v17").replace("prompt is v61", "prompt is v17")
    older = older.replace("this checkout is older than the prompt", "the pasted copy is not the current one")
    failed = setup_report._rerun_note(parse_friction(older), 0)
    assert failed is not None and failed[0] == "failed"
    assert f"`{Outcome('failed', 1, (), 17, failed[1]).text}`".replace("18s", "23s") in " ".join(page.split())
    window = int(setup_report._RERUN_WINDOW.total_seconds())
    assert f"starts within {window} s of the stop" in stopped_rule
    assert (
        "One exception (since round 8, J9): an attempt whose step 1 error is install.sh's `--log-start` stop"
        in (stopped_rule)
    )
    assert (
        "A copy fetched again from the README and pasted into a new session stays `failed at step 1`" in page
    )
    assert '(the rerun exception under "Stopped at step 1")' in page
    assert "**Amended 2026-10-14, §16.85 K66 (by §16.74 J9):** an attempt whose step 1 error" in h43
    # B13 round 10 L46, L59: the prompt-version bullet, H43's K66 line and §16.85 K66 said any rerun reads
    # stopped; since K11 only the newer copy's stop does
    flat = " ".join(page.split())
    newer, older_why = "this checkout is older than the prompt", "the pasted copy is not the current one"
    assert newer in stop and older_why in older
    assert "An attempt the session ran again so reads stopped, not failed (the rerun" not in flat
    assert (
        f"(the stop reads `{newer}`), an attempt the session ran again so reads stopped, not failed "
        '(the rerun exception under "Stopped at step 1"). After the stop of an older value, or of one that '
        f"gives no version (`{older_why}`), it stays failed and its outcome names both readings" in flat
    )
    assert (
        "**Amended 2026-10-14, §16.91 L59 (by §16.80 K11):** only the stop of a copy newer than the "
        f"installer (`{newer}`) is that rerun; an older or unstated copy's stop (`{older_why}`) stays "
        "`failed at step 1`" in h43
    )
    k66 = " ".join(
        _contracts_section("**The guide's stopped rule has J9's rerun exception**", "\n\n").split()
    )
    assert (
        "Amended 2026-10-14, §16.91 L59 (by §16.80 K11): only a stop of a copy newer than the installer "
        f"(`{newer}`)" in k66
    )


def test_the_docs_say_a_label_escalation_entry_stays_queued_while_its_file_is_published() -> None:
    """B13 round 7 H58, H63: since round 6's G30 a `label-escalation` entry whose file the label policy
    publishes again stays queued, as an `upstream-deleted` one whose file is listed does
    (`governance.KEPT_REASONS`), and the run's K counts both; setup-feedback.md's Purge queue row said such an
    entry is erased, and data-governance.md never said it stays queued."""
    from agentsync.governance import KEPT_REASONS, PurgeReason  # noqa: PLC0415

    assert set(KEPT_REASONS) == {PurgeReason.UPSTREAM_DELETED, PurgeReason.LABEL_ESCALATION}
    feedback = _page("docs/deploy/setup-feedback.md")
    assert "or an entry with another reason, is still erased" not in feedback
    assert "and also a `label-escalation` entry whose file the label policy publishes again" in feedback
    assert (
        "a `label-escalation` entry whose file is published again (`note: stays queued: the label" in feedback
    )
    assert "when some of the M are listed upstream deletions" not in feedback
    assert "when some of the M are upstream deletions or label escalations whose file is listed" in feedback
    governance = _page("docs/deploy/data-governance.md")
    assert "the next sync publishes it again and the entry stays queued" in governance
    assert "While the file is published, `agentsync purge --queue` skips it" in governance
    assert (
        "If the policy refuses the file again, the next `agentsync purge --queue` runs the entry"
        in governance
    )
    assert "An `upstream-deleted` entry whose file is listed again" in governance


def test_the_placeholder_table_states_the_org_word_and_generic_folder_rules_of_round_6() -> None:
    """B13 round 7 H60: the `<org-N>` row gave round 5's rule (a one-word organisation the report prints is
    replaced only in the folder names) for every report word, while since round 6 that holds only for the
    reviewed generic words (`setup_report._CONTEXT_ORG_WORDS`) and any other one is replaced everywhere and
    makes the report Private only; and the paragraph under the table had no word of G48's exception, a
    configured generic folder (`_GENERIC_FOLDERS`) that leaves the report public.  The examples are checked
    against those lists, which stay the only full copies."""
    from agentsync import setup_report  # noqa: PLC0415

    page = _page("docs/deploy/setup-feedback.md")
    assert "(`Link`) only in those names, never the word in the report's sentences" not in page
    words = re.search(r"generic words the report prints \(([^)]*)\)", page)
    assert words is not None
    for word in re.findall(r"`(\w+)`", words.group(1)):
        assert word.lower() in setup_report._CONTEXT_ORG_WORDS, word
    other = re.search(r"any other one-word organization the report prints \(`(\w+)`\) everywhere", page)
    assert other is not None and other.group(1).lower() not in setup_report._CONTEXT_ORG_WORDS
    assert "which makes the report Private only (below)" in page
    folders = re.search(r"a name every OneDrive or Google Drive has \(([^)]*)\) is the exception", page)
    assert folders is not None
    for name in re.findall(r"`(\w+)`", folders.group(1)):
        assert name in setup_report._GENERIC_FOLDERS, name
    assert "so it does not make the report private (in either report)" in page


def test_the_proxy_row_gives_one_rule_for_both_reports() -> None:
    """B13 round 7 H62: the `<proxy-N>` row and §16.65 G4 said the agentsync report replaces a common-word
    proxy host only inside a URL, while its url mode also matches one before `:<port>`, as the shell report
    does."""
    from agentsync import setup_report  # noqa: PLC0415

    red = setup_report.Redactor()
    setup_report._add_proxy_host(red, "proxy")
    assert red.redact("proxy:8080 refused") == "<proxy-1>:8080 refused"
    assert red.redact("Could not resolve proxy: proxy") == "Could not resolve proxy: proxy"
    page = _page("docs/deploy/setup-feedback.md")
    assert "only where a URL names it, or in the shell fallback report" not in page
    assert "only where a URL or `host:port` names it, in both reports)" in page
    g4 = " ".join(_contracts_section("### 16.65 ", "### 16.66 ").split())
    assert "**Amended 2026-10-13, §16.73 H62:** the agentsync report's rule is not the URL alone" in g4


def test_the_banner_re_render_names_the_tombstones_it_covers() -> None:
    """B13 round 7 H68 narrowed data-governance.md to the tombstones of sources in sources.toml, which was all
    `Cycle._rerender_stubs` walked. B13 round 8 J74: it walks every source that holds a tombstone output, a
    source deleted from sources.toml included, so the page says every intact tombstone again."""
    from agentsync import cycle  # noqa: PLC0415

    walk = inspect.getsource(cycle._Cycle._rerender_stubs)
    assert "o.status is OutputStatus.TOMBSTONE" in walk and "self._rerender_tombstones(sid)" in walk
    page = _page("docs/deploy/data-governance.md")
    assert "from what it stored: an intact tombstone, and the intact stub of a live source." in page
    assert "of a source still in" not in page and "keeps its tombstones in the old layout" not in page


def test_the_send_step_counts_the_values_the_link_fills() -> None:
    """B13 round 7 H71: the issue link fills five fields (`setup_report.ISSUE_FIELDS`; `loop_stage` since
    2026-10-05), while the Send step said it fills the Summary's four values."""
    from agentsync import setup_report  # noqa: PLC0415

    words = {5: "five"}
    count = words[len(setup_report.ISSUE_FIELDS)]
    page = _page("docs/deploy/setup-feedback.md")
    assert "the Summary's four values" not in page
    assert "copy the four values from the Summary" not in page
    assert f"opens the form with the Summary's {count} values" in page
    assert f"copy the {count} values from the Summary" in page
    # B13 round 8 J42, J61: the shell fallback's sentence said "the four fields are yours to fill in from the
    # friction log", which holds no run type; it names the fields, and install.sh's comment quotes the page.
    assert "four fields" not in page
    shell = page[page.index("Its Summary has an `- outcome:` line.") : page.index("Not for a report with")]
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    # B13 round 9 K64: since round 8 J22 the computed line carries the run type and the link fills it
    # (install.sh's LINK_FIELDS). B13 round 10 L56: that type is compute_run_type's, simulated included
    # (round 9 K8), and the uncomputed case's own `- run:` line gives Run type; no case leaves it to section
    # 4's rule
    assert "computes no run type" not in shell
    computed, bare = shell.split("Otherwise the line says no outcome is computed")
    fields = max(re.findall(r'LINK_FIELDS="([^"]+)"', installer), key=len)
    assert fields.endswith("and Agent tool and model")
    for label in re.split(r", (?:and )?", fields):
        assert label in computed, label
    # B13 round 9 K8 and K26: the run type is compute_run_type's, the simulated one too, and the note names no
    # cause; not computed, a "- run:" line gives the run type
    assert "Sandbox with simulated launchd when the attempt's last run says `launchd=simulated`" in computed
    assert "Run type from the `- run:` line below it" in bare and "section 4's rule" not in bare
    j42 = " ".join(
        _contracts_section("**The Send step names the fields the shell report leaves", "\n\n").split()
    )
    k64 = " ".join(
        _contracts_section("**The guide says the shell report's computed line carries", "\n\n").split()
    )
    assert (
        "Amended 2026-10-14, §16.91 L56 (by §16.81 K8): the computed line's run type is `compute_run_type`'s"
        in j42
    )
    assert "no case leaves it to section 4's rule" in j42
    assert (
        'Amended 2026-10-14, §16.91 L56 (by §16.81 K8): "never simulated" and "keeps section 4\'s rule"'
        in k64
    )
    assert "step 1's pull did not update it" not in installer
    assert "the line's bracket also says so, naming no cause" in computed
    for label in ("Outcome", "Prompt version", "Agent tool and model"):
        assert label in bare, label
    # B13 round 10 L2: not computed, the link fills the run type and what the latest attempt states
    assert "the link fills Run type from the `- run:` line below it" in bare
    assert "where the friction log's latest attempt states them" in bare and "is bare" not in shell
    assert "In both cases the shell report has no `Loop:` line, and Loop stage stays empty" in bare
    j42 = " ".join(_contracts_section("**The Send step names the fields the shell report", "\n\n**").split())
    assert (
        "Amended 2026-10-14, §16.85 K64 (by §16.75 J22): the computed outcome line carries the run type"
        in j42
    )
    assert "copy the four values from the Summary" not in installer
    assert f'"copy the {count} values from the Summary" (setup-feedback.md)' in installer


def test_the_newer_copy_paragraph_quotes_the_summarys_cause() -> None:
    """B13 round 7 H72: since round 6's G20 the Summary's prompt clause names which cause the installer's fix
    column found (`setup_report._prompt_copy_note`), while the newer-copy paragraph sent the reader to the
    friction log's fix column alone."""
    from agentsync import setup_report  # noqa: PLC0415

    stop = (
        "install.sh --log-start: the pasted setup prompt is vM and this installer is for setup prompt vN: "
        "this checkout is older than the prompt; setup stopped"
    )
    fixes = (
        "copy the prompt again ...: this Mac's checkout follows that branch, so a copy newer than it came "
        "from another branch or a fork",
        "point this Mac's checkout at main of https://example.com/contoso/agent-context-sync (...)",
        "",
    )
    page = _page("docs/deploy/setup-feedback.md")
    for fix in fixes:
        log = f"Attempt: 2026-10-13T10:00:00Z\nPrompt: vM\n2026-10-13T10:00:00Z | step 1 | error | {stop} | "
        log += f"{fix}\n"
        [attempt] = setup_report.parse_friction(log.replace("vN", "v14").replace("vM", "v15")).attempts
        note = setup_report._prompt_copy_note(attempt).replace("v14", "vN")
        assert note.startswith(" (newer than the installer's vN"), note
        quoted = f"`prompt: vM{note}`" if ":" in note.split("vN", 1)[1] else f"the bare `{note.strip()}`"
        assert quoted in page, quoted
    assert "The shell report (`install.sh --report-only` without agentsync) has no such clause" in page


def test_g8_says_the_two_reports_number_a_deduplicated_id_apart() -> None:
    """B13 round 7 H69: §16.65 G8 said `reports-2` keeps `<source-2>` "as in the Python report", while the
    Python report spends no number on the id its folder pair covers, so `reports-2` is its `<source-1>`; the
    shell report numbers by table place (`test_the_shell_report_keeps_a_deduplicated_ids_own_number`)."""
    from agentsync.setup_report import Redactor  # noqa: PLC0415

    red = Redactor()
    red.add("folder", "Reports", fuzzy=True)
    red.add("source", "reports")
    red.add("source", "reports-2")
    assert red.redact("reports and reports-2") == "<folder-1> and <source-1>"
    g8 = " ".join(_contracts_section("### 16.65 ", "### 16.66 ").split())
    assert (
        "**Amended 2026-10-13, §16.73 H69:** `reports-2` keeps a `<source-N>` of its own, as in the Python "
        "report, but the number can differ" in g8
    )
    assert "the Python report numbers only the ids no folder pair covers (`<source-1>`" in g8


def test_the_guide_reads_a_folder_list_that_could_not_read_the_config(tmp_path: Path) -> None:
    """B13 round 8 J59, J70 (one defect): since round 7 H17, H28 and H8 a list that logged `synced=unreadable
    marks=<why>`, followed by an install run that kept folders, makes the folder question a turn agentsync
    caused (`setup_report.unread_synced`); the guide still called a stop there the expected one, and its
    install.log row, outcome rules and Summary table had no word of it.  Each wording is built by the code."""
    from agentsync import setup_report  # noqa: PLC0415

    at, run = "2026-10-13T10:00:00Z", "20261013T100000Z-42"
    log = tmp_path / "install.log"
    log.write_text(
        f"{at} run={run}-1 start install.sh compat=16 commit=0123456789ab kind=checkout source=- "
        "args=--list-folders\n"
        f"{at} run={run}-1 step=list-folders seconds=4 rc=0 result=done note=listed-3 synced=unreadable "
        "marks=timeout\n"
        f"{at} run={run}-1 end rc=0 seconds=4\n"
        f"{at} run={run}-2 start install.sh compat=16 commit=0123456789ab kind=checkout source=- args=\n"
        f"{at} run={run}-2 step=config seconds=1 rc=0 result=skipped note=exists kept=2 added=0\n"
        f"{at} run={run}-2 end rc=0 seconds=9\n",
        encoding="utf-8",
    )
    runs = setup_report.read_install_runs(log)
    assert setup_report.unread_synced(runs) == 2
    why = setup_report.unread_why(runs)
    friction = "Attempt: 2026-10-13T09:59:00Z\nPrompt: v16\nAgent: Tool-A\n"
    [attempt] = setup_report.parse_friction(friction).attempts
    outcome = setup_report.compute_outcome(attempt, runs)
    assert outcome.kind == "worked with help"
    page = _page("docs/deploy/setup-feedback.md")
    assert "`synced=unreadable marks=<why>`" in page
    for word in ("no-tool", "timeout", "older:<name>"):
        assert f"`{word}`" in page, word
    assert f"`{outcome.why[-1]}`" in page
    turns = setup_report._expected_turns_line(attempt, "Real Mac", 0, unread=2)
    first = turns.removeprefix("- ").split(" · ")[0]
    assert f"`{first} · ...`" in page
    assert f"`{first.removeprefix('expected turns: ').replace('2 folders', 'N folders')}`" in page
    note = (
        f"note: step 1's folder list could not read which folders sources.toml already syncs ({why}), so it"
    )
    assert note in page
    assert "a turn agentsync caused, not the expected stop" in page
    assert "With `kept=0`, no install run, or a stop at step 1, the question stays expected" in page
    assert "A list that finished without the count printed no such line" not in page


def test_the_guide_maps_each_prompts_steps_to_the_forms_options() -> None:
    """B13 round 8 J60: since round 7 H11 the form has v7's step 3 by its own name and `Outcome.form_label`
    gives a v7 or later step 3 that option; the guide said a v7 step N maps to v6's option."""
    from agentsync.setup_report import FORM_OUTCOME_OPTIONS, PROMPT_VERSION, Outcome  # noqa: PLC0415

    page = _page("docs/deploy/setup-feedback.md")
    assert "so its N maps to the same option" not in page
    para = page[
        page.index("An attempt whose header says `Prompt: v5`") : page.index("The outcome judges only")
    ]
    for version in (7, PROMPT_VERSION):
        labels = [Outcome("failed", step, (), version).form_label for step in (1, 2, 3)]
        assert all(label in FORM_OUTCOME_OPTIONS for label in labels)
        assert labels[2] == "Failed at step 3 (sync loop and report)"
        assert f"(`{labels[2]}`)" in para and f"takes `{labels[1]}`" in para
    v5 = [Outcome("failed", step, (), 5).form_label.split(" (")[0][-1] for step in range(1, 7)]
    assert v5 == ["1", "1", "2", "3", "3", "4"]
    assert "1 and 2 to 1, 3 to 2, 4 and 5 to 3, and 6 to 4" in para


def test_the_loop_line_paragraph_names_every_path_the_report_shows_in_words() -> None:
    """B13 round 8 J62: since round 7 H3 and H36 the Loop line shows the docs repo's AGENTS.md and
    _sync/STATE.md in words, as it does the _eval folder; the guide named only the _eval folder.  Every
    constant `_eval_by_name` substitutes is quoted, so the next one cannot leave the guide behind."""
    from agentsync import setup_report  # noqa: PLC0415

    replaced = re.findall(
        r"line\.replace\([^,]+, (_[A-Z]+_SHOWN)\)", inspect.getsource(setup_report._eval_by_name)
    )
    assert set(replaced) == {"_EVAL_SHOWN", "_AGENTS_SHOWN", "_STATE_SHOWN"}
    page = _page("docs/deploy/setup-feedback.md")
    para = page[
        page.index("The outcome judges only the install.") : page.index("The run type is computed too")
    ]
    for name in replaced:
        assert f"reads `{getattr(setup_report, name)}`" in para, name
    # B13 round 10 L44: since round 9 K43 the config file is shown as _CONFIG_SHOWN, its file name in words;
    # the guide still said "the config file its name", the bare name it showed before
    assert "line.replace(loop._shown(config.config_path), _CONFIG_SHOWN.format(config.config_path.name))" in (
        inspect.getsource(setup_report._eval_by_name)
    )
    assert "and the config file its name" not in para
    shown = setup_report._CONFIG_SHOWN
    assert (
        f"reads `{shown.format('<its file name>')}` (`{shown.format('sources.toml')}` for the default" in para
    )


def test_the_redaction_docs_name_the_git_url_rewrites_as_proxy_values() -> None:
    """B13 round 8 J63: since round 7 H20 both reports register each git URL rewrite's host and base as a
    `<proxy-N>` and count the rewrites on the Environment line; the guide's `<proxy-N>` row and machine
    sections, and the Python report's own legend and module docstring, named only proxy hosts and the PAC URL.
    (The shell legend is pinned in `test_the_shell_report_reads_past_a_dollar_quoted_install_log_word`.)
    Since round 8 J20 the base is no value: the host is, and its path when a company word is in it."""
    from agentsync import setup_report  # noqa: PLC0415

    assert "git URL rewrite (insteadOf) host and its company-named path" in setup_report._LEGEND["proxy"]
    doc = " ".join((setup_report.__doc__ or "").split())
    assert (
        "the host of each git URL rewrite (``insteadOf``; github.com kept) and its path when a company" in doc
    )
    page = _page("docs/deploy/setup-feedback.md")
    assert 'the host of each git URL rewrite (`[url "<base>"] insteadOf`, never `pushInsteadOf`' in page
    assert "github.com is never replaced" in page
    assert "how many URL rewrites it has (a count, never a host)" in page


def test_the_prompt_version_section_reads_a_prompt_newer_than_the_reports_agentsync() -> None:
    """B13 round 8 J64: since round 7 H23 the Summary's prompt line adds `(newer than this report's agentsync,
    for prompt vN)` when an older installed build wrote the report; the guide quoted only the installer's
    `newer than the installer's vN` forms (K18), which share the words."""
    from agentsync import setup_report  # noqa: PLC0415

    newer, own = f"v{setup_report.PROMPT_VERSION + 1}", f"v{setup_report.PROMPT_VERSION}"
    friction = f"Attempt: 2026-10-13T10:00:00Z\nPrompt: {newer}\nAgent: Tool-A\n"
    [attempt] = setup_report.parse_friction(friction).attempts
    note = setup_report._newer_than_me_note(attempt)
    assert note
    page = _page("docs/deploy/setup-feedback.md")
    # since round 8 J40 the note is a bare clause the prompt line brackets (_prompt_line_note)
    assert f"`prompt: vM ({note.replace(own, 'vN')})` is a different case: no installer stop" in page
    assert "Do not triage it as K18." in page


def test_every_contract_paragraph_that_names_sync_source_says_it_does_not_exist() -> None:
    """B13 round 8 J51: KISS K13b deleted `sync --source` on 2026-10-05, but §16.39 (twice), §16.45 R51 and
    §16.68 H73, all written later, name it as a run that selects sources.  `sync` refuses it, and each
    paragraph that names it holds the dated line that says so."""
    from agentsync import cli  # noqa: PLC0415

    with pytest.raises(SystemExit) as refused:
        cli.build_parser().parse_args(["sync", "--source", "contoso-docs"])
    assert refused.value.code == 2
    cli.build_parser().parse_args(["reconcile", "--source", "contoso-docs"])
    text = CONTRACTS.read_text(encoding="utf-8")
    paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if "`sync --source`" in p]
    assert len(paragraphs) == 4, "the four paragraphs J51 found"
    for para in paragraphs:
        assert "**Amended 2026-10-14, §16.79 J51:** `sync --source` does not exist" in para, para[:80]


def test_the_contracts_quote_the_shell_reports_list_and_send_lines_as_written() -> None:
    """B13 round 8 J67: §16.29's dated line said a list that could not read the config ends
    `synced=unreadable`, while since round 7 H8 install.sh writes `synced=unreadable marks=<why>`; §16.69
    H75 called the agentsync report's `Send it:` line, with its `: it would show that name`, the shell
    report's words, which hold no such clause.  Each gets a dated line, read against install.sh."""
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert 'unread=" synced=unreadable marks=$marks"' in installer
    send = next(line for line in installer.splitlines() if 'say "Send it: privately' in line)
    assert "it would show that name" not in send and "never as a public issue. Nothing is sent" in send
    s29 = " ".join(_contracts_section("### 16.29 ", "### 16.30 ").split())
    assert (
        "**Amended 2026-10-14, §16.79 J67 (by §16.70 H8):** the line no longer ends there: it reads " in s29
    )
    assert "`synced=unreadable marks=<why>`" in s29
    s69 = " ".join(_contracts_section("### 16.69 ", "### 16.70 ").split())
    assert "**Amended 2026-10-14, §16.79 J67:** not quite the shell report's words" in s69


_ROUND_7_PROMPT_AMENDS = (
    ("16.30", "**Not by file name.**", "H19"),
    ("16.49", '**The finish** (R11, R30): "the folders this Mac syncs', "H9"),
    ("16.43", "**The finish names one NEXT line** (H3;", "H6"),
    ("16.67", "**The git stop's hint names a URL rewrite and marks its commands** (G73, G24;", "H21, H25"),
    ("16.61", "**Amended 2026-10-13, §16.67 G73:** since v15", "H25"),
    ("16.67", "**The version stop passes on the installer's words** (G15, G67;", "H65"),
    ("16.59", "**Amended 2026-10-13, §16.67 G15:**", "H65"),
    ("16.67", "**The first sync's own wait is named** (G37;", "H66"),
    ("16.49", "**Amended 2026-10-13, §16.67 G37:** since v15", "H66"),
)
"""§16.72's targets (B13 round 8 J66): the section, a phrase only the amended paragraph or bullet holds,
and the round 7 items whose dated line it carries. §16.72 names them as paragraphs, so no heading scan finds
them."""


def test_round_7s_prompt_items_have_dated_lines_at_the_text_they_amend() -> None:
    """B13 round 8 J66: §16.72 (v16) amended §16.30, §16.43, §16.49 and §16.67 text with no dated line at
    any of it, "the older text stays as the record of its version", while §16.67 (v15) and §16 as a whole
    mark each target; so §16.30 still said `git format-patch -1` always writes one patch.  Each target, the
    v15 lines that quoted the text v16 replaced included, holds `Amended 2026-10-1N, §16.79 J66 (by §16.72
    H<n>)`."""
    sections = dict(_contracts_numbered_sections())
    assert len(_ROUND_7_PROMPT_AMENDS) >= 9
    for section, anchor, items in _ROUND_7_PROMPT_AMENDS:
        blocks = [" ".join(b.split()) for b in re.split(r"\n\s*\n|\n(?=\s*- \*\*)", sections[section])]
        [block] = [b for b in blocks if anchor in b]
        marker = rf"\*\*Amended 2026-10-1\d, §16\.79 J66 \(by §16\.72 {re.escape(items)}\):\*\* since v16"
        assert re.search(marker, block), f"§{section} {anchor[:40]} has no dated line for §16.72 {items}"
    opening = " ".join(sections["16.72"].split("\n\n")[1].split())
    assert "stays as the record of its version. **Amended 2026-10-14, §16.79 J66:** as" in opening
    assert 'is §16.49\'s "The finish" (R11, R30)' in opening


_ROUND_8_PROMPT_AMENDS = (
    ("16.72", "**The finish's folder list is the config's**", "J6"),
    ("16.72", "**The keep command keeps a local commit", "J7, J8"),
    ("16.49", '"Before going to the report from either of these two stops"', "J19"),
    ("16.61", "**A missing checkout is step 3's third case", "J19"),
)
"""§16.76's targets (B13 round 9 K65): the section, a phrase only the amended paragraph or bullet holds, and
the round 8 items whose dated line it carries. §16.76 names them by quoted phrase, which no heading scan
reads."""


def test_round_8s_prompt_items_have_dated_lines_at_the_text_they_amend() -> None:
    """B13 round 9 K65: §16.76 (v17) amended §16.72's finish and keep paragraphs, §16.49's logging sentence
    and §16.61's missing-checkout condition with "the older text stays as the record of its version" and no
    dated line, the defect round 8's J66 fixed for §16.72 while saying the rule was found nowhere else in §16.
    Each target holds `Amended 2026-10-1N, §16.85 K65 (by §16.76 J<n>)`, §16.76's opening a line saying so,
    and J66 a line correcting its claim."""
    sections = dict(_contracts_numbered_sections())
    for section, anchor, items in _ROUND_8_PROMPT_AMENDS:
        blocks = [" ".join(b.split()) for b in re.split(r"\n\s*\n|\n(?=\s*- )", sections[section])]
        [block] = [b for b in blocks if anchor in b]
        marker = rf"\*\*Amended 2026-10-1\d, §16\.85 K65 \(by §16\.76 {re.escape(items)}\):\*\* since v17"
        assert re.search(marker, block), f"§{section} {anchor[:40]} has no dated line for §16.76 {items}"
    opening = " ".join(sections["16.76"].split("\n\n")[1].split())
    assert (
        "stays as the record of its version. **Amended 2026-10-14, §16.85 K65:** as §16.79 J66 did" in opening
    )
    j66 = " ".join(_contracts_section("**Round 7's prompt items have dated lines", "\n\n").split())
    assert 'Amended 2026-10-14, §16.85 K65: "a rule found nowhere else in §16" was not so.' in j66


def _contracts_numbered_sections_at() -> list[tuple[str, int, str]]:
    """:func:`_contracts_numbered_sections` with each section's offset in CONTRACTS.md."""
    raw, found, offset = CONTRACTS.read_text(encoding="utf-8"), [], 0
    for number, text in _contracts_numbered_sections():
        assert raw[offset : offset + len(text)] == text
        found.append((number, offset, text))
        offset += len(text) + 1
    return found


def _contracts_blocks(text: str, start: int, separator: str) -> list[tuple[int, str]]:
    """``re.split(separator, text)``, each block with its offset in CONTRACTS.md (``text`` at ``start``)."""
    blocks: list[tuple[int, str]] = []
    pos = 0
    for cut in re.finditer(separator, text):
        blocks.append((start + pos, text[pos : cut.start()]))
        pos = cut.end()
    return [*blocks, (start + pos, text[pos:])]


def _dated_lines_at(start: int, block: str, marker: re.Pattern[str]) -> list[int]:
    """The CONTRACTS.md offsets of the dated lines ``marker`` matches in ``block`` (at ``start``), read on the
    block's flattened text as every dated-line test reads it. ``marker`` starts at the word "Amended"."""
    flat = " ".join(block.split())
    words = [found.start() for found in re.finditer("Amended", block)]
    return [start + words[flat[: hit.start()].count("Amended")] for hit in marker.finditer(flat)]


def _dated_line_scan() -> tuple[
    list[tuple[str, list[str], str, str]], set[tuple[str, str]], set[int], list[str]
]:
    """The claims of every round 7 or later section but §16.72 (home, its items, target section, target item),
    the bare sections a target list names (home, section: "§16.14" with no item after it), the offsets of the
    dated lines that alone hold a claim, and the claims none holds. See
    `test_a_round_7_or_later_item_that_amends_another_has_a_dated_line_there`."""
    numbered = _contracts_numbered_sections_at()
    sections = {number: (start, text) for number, start, text in numbered}
    claims: list[tuple[str, list[str], str, str]] = []
    bare: set[tuple[str, str]] = set()
    heading = (
        r"\(((?:[A-Z]\d+(?:, | and )?)+)[^;()]*(?:; [^;()]*)*?; (?:amends|corrects|dated lines? at) ([^)]*)\)"
    )
    tok = r"(?:§\d+\.\d+(?:'s)?(?: [A-Z]\d+\b)?|[A-Z]\d+\b)"
    # the "." inside "§16.68" ends no sentence, so the targets are tokens, not a [^.]* run
    sentence = rf"^\*\*([A-Z]\d+)\b[^*]*\*\*.*?\bDated lines? at ({tok}(?:(?:, | and ){tok})*)"
    # "**L42: ... (`agentsync.setup_report`; amends §16.80 K6).**": the bracket's first clause names modules
    lead = r"^\*\*((?:[A-Z]\d+(?:, | and )?)+):[^*]*?\((?:[^()]*?; )?(?:amends|corrects) ([^()]*)\)\.?\*\*"
    # every round from 7 on, the sections round 12 and later add included (B13 round 12 N72)
    homes = [n for n, _, _ in numbered if re.fullmatch(r"16\.\d+", n) and int(n[3:]) >= 68 and n != "16.72"]
    for home in homes:
        for para in re.split(r"\n\s*\n", sections[home][1]):
            flat = " ".join(para.split())
            found = re.findall(heading, flat) + re.findall(sentence, flat) + re.findall(lead, flat)
            for items, targets in found:
                section = ""
                for number, item in re.findall(r"(?:§(\d+\.\d+)(?:'s)? )?([A-Z]\d+)\b", targets):
                    section = number or section
                    claims.append((home, re.findall(r"[A-Z]\d+", items), section, item))
                bare.update(
                    (home, n) for n in re.findall(r"§(\d+\.\d+)(?!\d)(?!(?:'s)? [A-Z]\d+\b)", targets)
                )
    held_at: set[int] = set()
    unheld: list[str] = []
    for home, items, section, item in claims:
        at = "|".join(re.escape(f"§{home} {i}") for i in items)
        either = "|".join(items)
        marker = re.compile(
            rf"Amended \d{{4}}-\d\d-\d\d, (?:§{re.escape(home)} (?:[A-Z]\d+, )*(?:{either})\b"
            rf"|§16\.\d+ [A-Z]\d+(?:, [A-Z]\d+)* \((?:by )?(?:{at})(?:, [A-Z]\d+)*\)"
            rf"|§{re.escape(home)} [A-Z]\d+(?:, [A-Z]\d+)*: [^;]*?; (?:[A-Z]\d+, )*(?:{either})\b)"
        )
        start, body = sections[section]
        paragraphs = _contracts_blocks(body, start, r"\n\s*\n")
        flats = [" ".join(p.split()) for _, p in paragraphs]
        # the paragraph that names the item, or a dated paragraph right after it
        units = [
            unit
            for n, (_, _) in enumerate(paragraphs)
            if re.search(rf"\b{item}\b", flats[n])
            for unit in paragraphs[
                n : n + (2 if n + 1 < len(flats) and "Amended" in flats[n + 1][:12] else 1)
            ]
        ]
        # an item with a "#### N27: ..." heading of its own spans paragraphs: its whole subsection holds it
        units += [
            unit
            for sub_at, sub in _contracts_blocks(body, start, r"\n(?=#### )")
            if " ".join(sub.split()).startswith(f"#### {item}:")
            for unit in _contracts_blocks(sub, sub_at, r"\n\s*\n")
        ]
        lines = {line for unit_at, unit in units for line in _dated_lines_at(unit_at, unit, marker)}
        # a line holds the claim only when it is the claim's one line: beside a second, deleting it fails
        # nothing (B13 round 12 N72, §16.86's M71 and M49 lines)
        held_at.update(lines if len(lines) == 1 else ())
        if not lines:
            unheld.append(f"§{section} {item} has no dated line for §{home} {items}")
    return claims, bare, held_at, unheld


def test_a_round_7_or_later_item_that_amends_another_has_a_dated_line_there() -> None:
    """B13 round 8 J73: §16.71 H47 and H52 corrected §16.66 G52 and G29, and H77 its "`STUB_RENDER` is
    4", with no dated line at any of them, the defect class H54 fixed for round 6, whose test reads only G
    headings.  Every "(H<n>[, H<m>]; amends|corrects §A X ...)" heading and every "(<item>; dated line(s)
    at §A X ...)" claim in §16.68 to §16.71, §16.73 and §16.79 names an item X of section A whose paragraph,
    or a dated paragraph right after it, holds `Amended 2026-10-1N, §<home> <item>` (or `... (by §<home>
    <item>)`).  §16.72 names paragraphs, not items:
    `test_round_7s_prompt_items_have_dated_lines_at_the_text_they_amend`.  B13 round 9 K80: the scan skipped
    round 8's §16.74 to §16.78 (eight such claims, none checked) and could not read §16.78's sentence form,
    "**J50: ...** Dated line at §16.68 H37."; it reads every round 7 to 9 section but §16.72 (§16.76 is read
    but holds no claim, its targets being paragraphs:
    `test_round_8s_prompt_items_have_dated_lines_at_the_text_they_amend`), and that sentence after a bold item
    lead too, a possessive target ("§16.76's J7") and a dated line naming several items ("(by §16.82 K12,
    K13)").  B13 round 10 L60, L72 (one defect): the target letters were F, G, H, Q, R, J and K, so an L or N
    target (§16.80's "dated line at §16.40 N37") made no claim, and a heading with a "; ..." clause before its
    "dated lines at" (§16.80 K10, K46; §16.75 J37, J78, J71) did not match: deleting §16.80's K5, K10 and K46
    lines left the suite green.  Any capital and digits is a target now, a heading may carry such clauses (a
    "; " still comes right before the keyword, so "K72's dated line at §16.79 is lane D's" names nothing),
    §16.83 names its targets in that form, and the scan reads round 10's §16.86 to §16.91.  B13 round 11 M81:
    it read none of §16.88's or §16.90's claims and missed §16.89 L34, so 12 round 10 dated lines were held by
    nothing (renaming all 12 left the suite green).  §16.90's heads are a bold item lead closed by a "(...;
    amends §A X)" bracket, read now as `lead`; a target list may hold a bare section ("§16.37 and §16.83 K34")
    without dropping the items after it; a dated line may name its items after a "; " ("§16.89 L14, L36, L38:
    ...; L48, L67: ...") or name several items "(by §<home> <item>)"; §16.88 names L61's target in the heading
    form; the scan reads round 11's §16.92 to §16.97 too; and the lines at paragraphs, not items, are pinned
    by `test_round_10s_lines_at_unlettered_paragraphs_are_there`.  B13 round 12 N72: the scan
    (:func:`_dated_line_scan`) reads every section from §16.68 on, round 12's included, and records the line
    that holds each claim alone; `test_every_dated_line_from_round_11_on_is_held_by_a_test` reads those."""
    from agentsync import publish  # noqa: PLC0415

    claims, bare, _held_at, unheld = _dated_line_scan()
    sections = dict(_contracts_numbered_sections())
    assert ("16.71", ["H52"], "16.66", "G29") in claims, "the scan is broken"
    assert ("16.74", ["J39"], "16.68", "H32") in claims and ("16.78", ["J50"], "16.68", "H37") in claims
    assert ("16.80", ["K5", "K16"], "16.40", "N37") in claims  # an N target
    assert ("16.80", ["K10", "K46"], "16.74", "J20") in claims  # middle "; ..." clauses
    assert ("16.75", ["J37", "J78", "J71"], "16.69", "H74") in claims
    assert ("16.83", ["K33"], "16.68", "H32") in claims and ("16.83", ["K75"], "16.77", "J12") in claims
    assert ("16.90", ["L42"], "16.80", "K6") in claims  # a bold lead's bracket
    assert ("16.90", ["L47", "L54"], "16.71", "H52") in claims and (
        "16.90",
        ["L51"],
        "16.84",
        "K63",
    ) in claims
    assert ("16.89", ["L34"], "16.83", "K34") in claims  # after a bare §16.37
    assert ("16.90", ["L54"], "16.78", "J55") in claims  # L54's second line, which its head does not name
    assert ("16.89", ["L48", "L67"], "16.83", "K40") in claims  # held after a "; " in L14's line
    assert ("16.88", ["L61"], "16.82", "K25") in claims and ("16.87", ["L16"], "16.89", "L34") in claims
    assert len({(h, tuple(i), s, t) for h, i, s, t in claims}) >= 208, "the scan is broken"
    # B13 round 12 N72: a bare section in a target list is read too, and a pin holds its line
    assert ("16.92", "16.14") in bare and ("16.89", "16.37") in bare and ("16.92", "16.64") not in bare
    assert ("16.100", ["N76"], "16.94", "M69") in claims and ("16.100", ["N7"], "16.94", "M4") in claims
    # §16.83 K75's line in §16.22's unlettered bullet names no item the scan can read
    assert "Amended 2026-10-14, §16.83 K75: so does an inbox no wait names" in " ".join(
        sections["16.22"].split()
    )
    assert unheld == []
    render = next(p for p in re.split(r"\n\s*\n", sections["16.66"]) if p.startswith("**`STUB_RENDER` is 4"))
    assert "**Amended 2026-10-14, §16.79 J73 (by §16.71 H77):** `STUB_RENDER` is 5" in " ".join(
        render.split()
    )
    assert publish.STUB_RENDER >= 5


_ROUND_10_PARAGRAPH_AMENDS = (
    ("16.14", "logs it as the step's `note=converted-N-deferred-M`", "§16.89 L34"),
    ("16.22", "The friction section labels the attempt", "§16.86 L32"),
    ("16.28", "PDF, deck, deck template, Word and OpenDocument files with a page", "§16.86 L5"),
    ("16.48", "`_OCR_DOCUMENTS` gains `.potx` as `deck template`", "§16.86 L5"),
    ("16.52", "`test_the_installer_and_the_report_end_a_stopped_attempt_at_the_same_line`", "§16.86 L32"),
)
"""Round 10's dated lines at paragraphs and bullets that carry no item (B13 round 11 M81): the section, a
phrase only that paragraph or bullet holds, and the item whose line it carries. No claim names them as items,
so the heading scan cannot hold them."""


def test_round_10s_lines_at_unlettered_paragraphs_are_there() -> None:
    """B13 round 11 M81, M78: §16.86 L5 (§16.28 and §16.48), §16.86 L32 (§16.22 and §16.52) and §16.89 L34
    (§16.14) left dated lines no test read, and two claims named the wrong section: §16.89 L34's "Dated lines
    at §16.37" is in §16.14, at the note §16.37 amended, and §16.86 L32's "quoted in §16.28" is in §16.22.
    Each line is pinned in its block, and each wrong section has a dated line naming the right one."""
    sections = dict(_contracts_numbered_sections())
    for section, anchor, item in _ROUND_10_PARAGRAPH_AMENDS:
        blocks = [" ".join(b.split()) for b in re.split(r"\n\s*\n|\n(?=\s*- )", sections[section])]
        [block] = [b for b in blocks if anchor in b]
        assert re.search(rf"Amended 2026-10-1\d, {re.escape(item)}:", block), f"§{section} has no {item} line"
    l34 = " ".join(_contracts_section("**L34: when every file read again was a first read", "\n\n").split())
    assert "Amended 2026-10-15, §16.97 M78, M81: the dated line for §16.37 is in §16.14" in l34
    l32 = " ".join(
        _contracts_section("**The friction section's attempt line has one bracket** (L32)", "\n\n").split()
    )
    assert "Amended 2026-10-15, §16.97 M78: the first of those labels and its dated line are in §16.22" in l32


_ROUND_11_PARAGRAPH_AMENDS = (
    ("16.14", "v5 revision 2 (2026-09-30, judge findings", "§16.92 M36"),
    ("16.14", "v6 revision (2026-09-30, setup prompt v6", "§16.92 M34"),
    ("16.22", "**setup-report: two wording fixes.**", "§16.92 M36"),
    ("16.22", "- `bring-back.md`: the headings of sections 2 and 3", "§16.93 M1"),
    ("16.25", "**Who builds.** Only `scripts/install.sh`", "§16.93 M8"),
    ("16.25", "**Installer** (`scripts/install.sh`, inside the `launcher` step", "§16.96 M25"),
    ("16.25", "**Installer** (`scripts/install.sh`, inside the `launcher` step", "§16.93 M8 (by §16.96 M25)"),
    ("16.29", "- **Every synced folder is printed, with its mark**", "§16.93 M9"),
    ("16.29", "- **A set-up Mac with nothing to list is still set up**", "§16.93 M4"),
    ("16.30", "- **`read(s) again (N of them could not be converted", "§16.92 M12"),
    ("16.30", "- **The title says what is hidden.**", "§16.92 M19"),
    ("16.32", "| FAIL | Its fix now |", "§16.95 M10"),
    ("16.37", "- **The helper builds have a step of their own.**", "§16.96 M25"),
    ("16.41", "the first line reads `another sync is running (<who>)`", "§16.93 M43, M60"),
    ("16.56", "- cli.py's sign-in lines (`<bin> graph login`)", "§16.93 M13"),
    ("16.75", "**Step 1's NEXT over an unread config**", "§16.93 M37 (by §16.87 L11)"),
    ("16.86", "**The local-work line says only what it can know**", "§16.92 M71"),
    ("16.86", "**The local-work line says only what it can know**", "§16.92 M54"),
    ("16.86", "**The local-work line says only what it can know**", "§16.92 M49 (M70 the same defect)"),
    ("16.89", "**L68: a source with empty folders and one it cannot read", "§16.95 M42"),
)
"""Round 11's dated lines no claim holds alone (B13 round 12 N72): the section, a phrase only the amended
block holds, and the line's marker. Most sit at a section a target list names with no item
("§16.30's run table, §16.64 G23, §16.14, §16.22"), which no claim can hold; the rest beside a line that
satisfies the same claim, so deleting either left the suite green."""

_ROUND_12_PARAGRAPH_AMENDS = (
    ("16.34", "**The media helper** (`agentsync.convert.media`", "§16.103 N58 (by §16.96 M25, §16.93 M8)"),
    ("16.35", "**The speech engine** (`agentsync.convert.speech`", "§16.103 N58 (by §16.96 M25, §16.93 M8)"),
    ("16.22", "- nothing stored (no access, a missing folder or sentinel)", "§16.101 N12, N38"),
    ("16.32", "| FAIL | Its fix now |", "§16.101 N53"),
    ("16.45", "**R102.** The wait for a local source whose listing a folder blocks", "§16.101 N12, N38"),
    ("16.56", "- cli.py's sign-in lines (`<bin> graph login`)", "§16.101 N13"),
    ("16.29", "- **A set-up Mac with nothing to list is still set up**", "§16.99 N41, N61"),
    ("16.53", "- **It ends without inviting a send** (Q10, Q18, one defect).", "§16.99 N22"),
    ("16.12", "STATE.md quotes alarms, errors, skipped reasons and lint text in code spans", "§16.102 N73"),
    ("16.25", "**Installer** (`scripts/install.sh`, inside the `launcher` step", "§16.98 N48"),
    ("16.30", "- **`read(s) again (N of them could not be converted and kept their page)`**", "§16.98 N15"),
    ("16.37", "- **The helper builds have a step of their own.**", "§16.98 N48"),
)
"""Round 12's dated lines at sections its claims name with no item, as :data:`_ROUND_11_PARAGRAPH_AMENDS`."""

_ROUND_13_PARAGRAPH_AMENDS = (
    ("16.22", "- doctor `heartbeat.<id>` (incomplete for 3 passes or more) names no folder", "§16.107 O51"),
)
"""Round 13's dated lines at sections its claims name with no item, as :data:`_ROUND_12_PARAGRAPH_AMENDS`."""

_ROUND_14_PARAGRAPH_AMENDS = (
    ("7", "`.vtt` → `vtt-turns` (was `text-plain`, which it replaces).", "§16.114 Q62"),
    ("15", "pandoc_path: Path | None = None # None = the pypandoc_binary bundled pandoc", "§16.114 Q62"),
    ("16.32", "- **Which pandoc.** The one the check runs:", "§16.114 Q62"),
    ("16.22", "- nothing stored (no access, a missing folder or sentinel)", "§16.115 Q80 (by §16.107 O49)"),
    ("16.109", "Docs, dated lines and a test docstring that lagged round 12's code", "§16.115 Q67"),
    ("16.21", "Rule 1's NEXT for a FAIL never copies the fix", "§16.113 Q1, Q9, Q33"),
)
"""Round 14's dated lines at sections its claims name with no item, as :data:`_ROUND_12_PARAGRAPH_AMENDS`."""

_ROUND_15_PARAGRAPH_AMENDS = (
    ("16.29", "- Without the counts there is no `folders:` line.", "§16.116 S9"),
    (
        "16.22",
        "`add-source` and `init`: the docs repo line ends `N scaffold file(s) written)`",
        "§16.121 S57 (by §16.111 Q39)",
    ),
    (
        "16.24",
        "**Summary.** `; N comment(s) on M page(s)` follows the page count",
        "§16.121 S48, S57 (by §16.111 Q39)",
    ),
    ("16.26", "**One wording for every document.**", "§16.121 S48, S57 (by §16.111 Q39)"),
    ("16.26", "`PDF: 3 page(s), 1 read by on-device OCR", "§16.121 S48, S57 (by §16.111 Q39)"),
    ("16.26", "`Presentation: 3 slide(s); text of 2 picture(s)", "§16.121 S48, S57 (by §16.111 Q39)"),
    (
        "16.26",
        "`Word document; text of 2 picture(s) read by on-device OCR",
        "§16.121 S48, S57 (by §16.111 Q39)",
    ),
    (
        "16.26",
        "Every other name in `agentsync.convert.image` is private",
        "§16.121 S48, S57 (by §16.111 Q39)",
    ),
    (
        "16.37",
        'When every unknown folder of a `kind = "local"` walk is a zero-child cloud folder',
        "§16.121 S57 (by §16.113 Q24, Q38, Q42, Q57)",
    ),
    ("16.14", "**Prompt v7.** `PROMPT_LAYOUTS[7]`: 1 preflight, 2 install", "§16.121 S67 (by §16.110 Q25)"),
    ("16.32", "- **What it prints.** Nothing when the start is quick.", "§16.117 S17"),
    ("16.29", "- **Every synced folder is printed, with its mark**", "§16.117 S10"),
    ("15", "Converter: docx/odt/rtf/html via the pypandoc_binary-bundled", "§16.120 S49 (by §16.114 Q62)"),
    ("15", "def resolved_pandoc(pandoc_path: Path | None) -> tuple", "§16.120 S49 (by §16.114 Q62)"),
    ("16.32", "**Out of time** (`subprocess.TimeoutExpired`).", "§16.120 S29"),
    ("16.32", "**A program macOS will not start**", "§16.120 S53"),
    ("16.26", "- **Summary.** `Image 1440x900 px; OCR: 2 line(s)`.", "§16.120 S52"),
    ("16.42", "The summary is `Email from <From>, <Date>: <subject>; N attachment(s)`.", "§16.120 S52"),
)
"""Round 15's dated lines at sections its claims name with no item, as :data:`_ROUND_12_PARAGRAPH_AMENDS`."""

_ROUND_16_PARAGRAPH_AMENDS = (
    (
        "16.14",
        '§16.40 "The first-sync line counts what other runs read again"):**',
        "§16.127 T51, T69 (by §16.116 S26)",
    ),
    ("16.32", "**Out of time** (`subprocess.TimeoutExpired`).", "§16.126 T17, T77"),
    ("16.32", "**Out of time** (`subprocess.TimeoutExpired`).", "§16.126 T54, T59, T60, T65"),
    ("16.24", "**Summary.** `; N comment(s) on M page(s)` follows the page count", "§16.126 T62"),
    ("16.26", "**One wording for every document.**", "§16.126 T62"),
    ("16.26", "`PDF: 3 page(s), 1 read by on-device OCR", "§16.126 T62"),
    ("16.26", "`Presentation: 3 slide(s); text of 2 picture(s)", "§16.126 T62"),
    ("16.26", "`Word document; text of 2 picture(s) read by on-device OCR", "§16.126 T62"),
)
"""Round 16's dated lines at sections its claims name with no item, as :data:`_ROUND_12_PARAGRAPH_AMENDS`."""

_ROUND_17_PARAGRAPH_AMENDS = (
    ("16.29", "**Who calls it.** Every non-dry cycle", "§16.133 U83 (by §16.125 T32)"),
    ("16.14", "decoded by `EXIT_MEANINGS` (0 ok, 1, 2, 75, 77, 78, 79 TCC_PENDING,", "§16.133 U84"),
    ("16.24", "**Summary.** `; N comment(s) on M page(s)` follows the page count", "§16.132 U77"),
    ("16.26", "**One wording for every document.**", "§16.132 U77"),
    ("16.26", "`PDF: 3 page(s), 1 read by on-device OCR", "§16.132 U77"),
    ("16.26", "`Presentation: 3 slide(s); text of 2 picture(s)", "§16.132 U77"),
    ("16.26", "`Word document; text of 2 picture(s) read by on-device OCR", "§16.132 U77"),
    ("16.54", "**A template with no slides says what its layouts and master hold", "§16.132 U77"),
    ("16.111", "**A known count takes its noun from the count** (Q39, Q44", "§16.132 U77"),
    ("16.120", "**No converter count keeps `(s)` (S52;", "§16.132 U77"),
    ("16.28", "- A failed conversion is not retried.", "§16.132 U3"),
    ("16.106", "**A [FAIL] whose fix is the person's sends the agent to the report**", "§16.132 U80"),
    ("16.126", "**Doctor's pandoc time-out gives the installer's reason (T17, T77;", "§16.132 U104"),
    ("16.126", "- Doctor's bundled fix is `run scripts/install.sh again", "§16.132 U13, U30, U47, U78"),
    ("16.126", "**The gate journal keeps what a failure said (T90;", "§16.132 U106"),
)
"""Round 17's dated lines at sections its claims name with no item, as :data:`_ROUND_12_PARAGRAPH_AMENDS`:
lane D's two (U83, U84), U77's beside each of T62's eight, U3's at §16.28's not-retried bullet, U80's at
§16.106 O11, and U104's, the bundled fix's and U106's in §16.126."""

_ROUND_18_PARAGRAPH_AMENDS = (
    ("16.126", "**Rows, columns and windows count at one (T61;", "§16.139 V69 (by §16.132 U76)"),
    (
        "16.129",
        "**An exit 65 at the first sync is the person's** (U55, U65",
        "§16.139 V73 (by §16.131 U64, U95)",
    ),
    (
        "16.133",
        "**Exit 65 has its meaning in each exit-code list** (U84;",
        "§16.139 V73 (by §16.131 U64, U95)",
    ),
    (
        "16.16",
        "Exit 1 only when a blocking finding exists; rows alone exit 0",
        "§16.139 V89 (by §16.125 T32)",
    ),
    (
        "16.30",
        '- The dry run\'s note counted every commit as "would be rewritten"',
        "§16.139 V89 (by §16.131 U71)",
    ),
    (
        "16.36",
        "A dry run of an entry that targets nothing (no blob, no docs path",
        "§16.139 V89 (by §16.131 U71)",
    ),
    (
        "16.38",
        "`run_purge_queue` asks `_kept_upstream_deletion` before each entry.",
        "§16.139 V89 (by §16.131 U71)",
    ),
    ("16.39", "The cache count in the purge headline reads `1 cache entry`", "§16.139 V89 (by §16.125 T32)"),
    ("16.39", "- no empty cloud folder below the root (an unreadable folder", "§16.139 V89 (by §16.131 U71)"),
    (
        "16.62",
        "**A label-escalation purge stays queued while its file is published again** (G30;",
        "§16.139 V89 (by §16.131 U71)",
    ),
    ("16.14", "**Amended (2026-10-10):** the MDM server's host in the Environment line", "§16.135 V46"),
    ("16.21", "`loop.status_line(config)`: `loop: skill <current|stale|missing>", "§16.137 V20"),
    ("16.22", "folders that held mirrored files get no paste line", "§16.137 V43"),
)
"""Round 18's dated lines (lane D's §16.139), as :data:`_ROUND_12_PARAGRAPH_AMENDS`: V69's at §16.126 T61,
V73's at §16.129 and §16.133, and V89's seven at the paragraphs that quote the swept `(s)` lines. Each
records a round 16 or 17 item's change, so its marker names that item after "by". Lane RB's V46 at §16.14's
shell-fallback registration paragraph, a section its claim names with no item. Lane B's §16.137: V20's at
§16.21's loop-line item and V43's at §16.22's held-folders bullet."""

_ROUND_18_E_PARAGRAPH_AMENDS = (
    ("16.28", "| `converted_failed` | of those, conversions that failed", "§16.138 V62, V67 (by §16.132 U3)"),
    ("16.28", "(`cache_last_used`, `reread_count`, `last_reread_counts`).", "§16.138 V74 (by §16.132 U3)"),
    ("16.35", "(B13 H12, 2026-10-11, §16.42: only the check's own non-zero answer", "§16.138 V68"),
    ("16.35", "**Probe states** (`probe(cfg, cache_dir) -> (state, detail)`, never compiles", "§16.138 V68"),
    ("16.42", 'stored in `<helper>.failed` as "does not descend from the build floor"', "§16.138 V68"),
    ("16.48", '("tries the build again on its next run", "rebuilds it") stay true.', "§16.138 V68"),
)
"""Round 18 lane E's dated lines at sections its claims name with no item, as
:data:`_ROUND_12_PARAGRAPH_AMENDS`: V62 and V67's at §16.28's `converted_failed` row, V74's at its Tests
paragraph, and V68's four beside the installer texts it quotes in §16.35, §16.42 and §16.48."""

_ROUND_19_D_PARAGRAPH_AMENDS = (
    ("16.14", "decoded by `EXIT_MEANINGS` (0 ok, 1, 2, 75, 77, 78, 79 TCC_PENDING,", "§16.145 W72"),
)
"""Round 19 lane D's dated lines at sections its claims name with no item, as
:data:`_ROUND_12_PARAGRAPH_AMENDS`: W72's at §16.14's Background runs paragraph."""

_ROUND_19_B_PARAGRAPH_AMENDS = (
    ("16.32", "Its fix has two parts, `<run again>; if this line comes back: <then>`:", "§16.143 W19, W48"),
)
"""Round 19 lane B's dated lines at sections its claims name with no item, as
:data:`_ROUND_12_PARAGRAPH_AMENDS`: W19 and W48's beside §16.32's `<run again>` table."""

_ROUND_19_E_PARAGRAPH_AMENDS = (
    ("16.28", "- A failed conversion is not retried. The next pass finds the same", "§16.144 W2"),
    ("16.28", "| `legacy_retried` | **Amended 2026-10-20, §16.144 W18:** failed conversions", "§16.144 W18"),
    ("16.42", "- **Page.** One WHOLE unit: per diagram page a `## <page name>` heading", "§16.144 W27"),
    ("16.42", 'stored in `<helper>.failed` as "does not descend from the build floor"', "§16.144 W50"),
    ("16.35", "**Probe states** (`probe(cfg, cache_dir) -> (state, detail)`, never compiles", "§16.144 W67"),
)
"""Round 19 lane E's dated lines at sections its claims name with no item, as
:data:`_ROUND_12_PARAGRAPH_AMENDS`: W2's at §16.28's not-retried bullet, W18's `legacy_retried` row and
W27's at §16.42's diagram page bullet, W50's at its signal paragraph, W67's at §16.35's probe states."""

_ROUND_20_D_PARAGRAPH_AMENDS = (
    ("9", "`SingleWriterLock(state_paths.lock, label).acquire()`", "§16.151 X56 (by §16.143 W13, W14)"),
    ("9", "For each live source (errors isolated per source;", "§16.151 X56 (by §16.143 W41)"),
    ("16.28", "**Configuration.** One line: how many folders listed under", "§16.151 X64 (by §16.140 W17)"),
)
"""Round 20 lane D's dated lines at sections its claims name with no item, as
:data:`_ROUND_12_PARAGRAPH_AMENDS`: X56's two in §9's steps 2 and 5, and X64's at §16.28's Configuration
paragraph. Each records a round 19 item's change, so its marker names that item after "by"."""


_ROUND_20_RB_PARAGRAPH_AMENDS = (
    (
        "16.32",
        "- `with_progress` counts its first interval from `$PROGRESS_SINCE` when that is set",
        "§16.147 X36",
    ),
    ("16.47", "`--report-only` for an attempt `--log-start` stopped writes", "§16.147 X19"),
)
"""Round 20 lane RB's dated lines at sections its claims name with no item, as
:data:`_ROUND_12_PARAGRAPH_AMENDS`: X36's at §16.32's `with_progress` clock bullet, X19's at §16.47's stopped
copy's report (its R17 and R18 are in the heading alone)."""

_ROUND_21_B_PARAGRAPH_AMENDS = (
    ("16.12", "**Governance (security-governance-10/11/14).**", "§16.155 Y49 (by §16.149 X4)"),
    ("16.32", "Its fix has two parts, `<run again>; if this line comes back: <then>`:", "§16.155 Y20"),
)
"""Round 21 lane B's dated lines at sections its claims name with no item, as
:data:`_ROUND_12_PARAGRAPH_AMENDS`: Y49's at §16.12's governance paragraph, recording §16.149 X4's change, and
Y20's beside §16.32's `<run again>` table."""

_ROUND_22_RA_PARAGRAPH_AMENDS = (
    ("16.28", "| `legacy_cleared` |", "§16.158 Z35"),
    ("16.28", "| `legacy_settled` |", "§16.158 Z35"),
)
"""Round 22 lane RA's dated line at a section its claims name with no item, as
:data:`_ROUND_12_PARAGRAPH_AMENDS`: Z35's `legacy_cleared` and `legacy_settled` rows in §16.28's run-record
table."""

_ROUND_23_E_PARAGRAPH_AMENDS = (
    ("16.28", "| `legacy_waiting` |", "§16.168 A63"),
    ("16.42", "(the route set and the counts: 11 converters,", "§16.168 A65 (by §16.54 Q78)"),
)
"""Round 23 lane E's dated lines at sections its claims name with no item, as
:data:`_ROUND_12_PARAGRAPH_AMENDS`: A63's `legacy_waiting` row in §16.28's run-record table, and A65's at
§16.42's diagram Tests paragraph, whose label-rule count Q78 changed."""

_ROUND_24_E_PARAGRAPH_AMENDS = (("16.28", "| `legacy_waiting` |", "§16.175 B73"),)
"""Round 24 lane E's dated line at a section its claims name with no item, as
:data:`_ROUND_12_PARAGRAPH_AMENDS`: B73's in §16.28's `legacy_waiting` row (a recording only a download would
read is settled too)."""

_ROUND_23_B_PARAGRAPH_AMENDS = (
    ("16.29", "Every one is a path a sync makes owner-only", "§16.167 A94"),
    ("16.32", "Amended 2026-10-21, §16.155 Y20: `run scripts/install.sh again without", "§16.167 A94"),
    ("16.32", "**Every FAIL names a fix.** install.sh's `NEXT:` says so", "§16.167 A94"),
)
"""Round 23 lane B's dated lines at sections its claims name with no item, as
:data:`_ROUND_12_PARAGRAPH_AMENDS`: A94's at §16.29's permissions-fix bullet and §16.32's run-again and
crash-fix paragraphs, whose fixes nest no bracket now."""

_ROUND_23_K_PARAGRAPH_AMENDS = (
    ("16.30", "- **The patch's time** is its mtime, read with `/usr/bin/stat`", "§16.170 A97"),
    ("16.30", "- The count and the first 12 hex digits of the file's SHA-256", "§16.170 A97"),
)
"""Round 23 lane K's dated lines at a section its claim names with no item, as
:data:`_ROUND_12_PARAGRAPH_AMENDS`: A97's at §16.30's two bullets that name install.sh's patch hash, now
`/sbin/sha256sum` where it exists."""

_ROUND_20_E_PARAGRAPH_AMENDS = (
    ("16.25", "**Who builds.** Only `scripts/install.sh`, by running the module", "§16.150 X57"),
    ("16.34", "builds it, in the `helpers` step right after the OCR helper", "§16.150 X57"),
    ("16.35", "builds it, right after the media helper, by running", "§16.150 X57"),
)
"""Round 20 lane E's dated lines at sections its claims name with no item, as
:data:`_ROUND_12_PARAGRAPH_AMENDS`: X57's at the OCR, media and speech helpers' build paragraphs, whose exit 1
also covers a built helper that fails its probe."""


_ROUND_21_D_PARAGRAPH_AMENDS = (
    (
        "9",
        "CLI exit codes (`cli.py`): 0 ok",
        "§16.157 Y76 (by §16.125 T12; §16.131 U66; §16.143 W41, W65; §16.149 X50, X73)",
    ),
    ("16.28", "**Configuration.** One line: how many folders listed under", "§16.157 Y60 (by §16.146 X38)"),
)
"""Round 21 lane D's dated lines at sections its claims name with no item, as
:data:`_ROUND_12_PARAGRAPH_AMENDS`: Y76's under §9's exit-code list and Y60's at §16.28's Configuration
paragraph. Each records an earlier round's change, so its marker names those items after "by"."""


def _pinned_dated_lines(table: tuple[tuple[str, str, str], ...]) -> set[int]:
    """The CONTRACTS.md offsets of the dated lines ``table`` pins, each in the one block (a paragraph or a
    bullet) of its section that holds its phrase."""
    sections = {number: (start, text) for number, start, text in _contracts_numbered_sections_at()}
    found: set[int] = set()
    for section, anchor, marker in table:
        start, body = sections[section]
        blocks = _contracts_blocks(body, start, r"\n\s*\n|\n(?=\s*- )")
        [(at, block)] = [(at, b) for at, b in blocks if anchor in " ".join(b.split())]
        lines = _dated_lines_at(at, block, re.compile(rf"Amended \d{{4}}-\d\d-\d\d, {re.escape(marker)}:"))
        assert lines, f"§{section} {anchor[:40]} has no {marker} line"
        found.update(lines)
    return found


def test_round_11s_and_12s_lines_at_unlettered_paragraphs_are_there() -> None:
    """B13 round 12 N72: 19 of round 11's 74 dated lines were held by no test (renaming all 19 left the suite
    green): 14 at sections a target list names with no item, the class round 11 M81 pinned for round 10 only,
    and 5 beside a line that satisfies the same claim. Each is pinned in its block, as round 10's are; the
    rule that reads one claim's line alone also finds §16.86's M54 line. Round 12's §16.103 N58 has two, and
    §16.101 (lane B) four, §16.99 (lane RB) two, §16.102 (lane E) one: N73's at §16.12's STATE.md
    paragraph. Round 14's §16.114 (lane E) three, Q62's, and §16.115 (lane D) two: Q80's at §16.22's
    nothing-stored bullet and Q67's at §16.109's opening paragraph. Round 15's §16.116 (lane RA) one, S9's at
    §16.29's folders-line bullet, §16.121 (lane D) nine: S48 and S57's count lines at §16.22, §16.24,
    §16.26 (five) and §16.37, and S67's at §16.14's "Prompt v7." paragraph, and §16.120 (lane E) six: S49's
    two in §15's `convert.pandoc` section, S29's and S53's in §16.32, and S52's at §16.26 and §16.42. Round
    16's §16.127 (lane D) one: T51 and T69's at §16.14's amended re-read clause, and §16.126 (lane E) seven:
    T17's and T54's in §16.32's out-of-time paragraph, and T62's at §16.24 and at four §16.26 blocks. Round
    17's §16.133 (lane D) two, U83's and U84's, and §16.132 (lane E) thirteen: U77's beside each of T62's
    lines, U3's at §16.28, U80's at §16.106 and three in §16.126 (U106's landed last). Round 18's §16.139
    (lane D) ten: V69's at T61, V73's two and V89's seven (B13 round 18 V89: lane B's U71 left them undated),
    §16.138 (lane E) six: V62 and V67's and V74's in §16.28, and V68's in §16.35 (two), §16.42 and §16.48, and
    §16.137 (lane B) two: V20's at §16.21 and V43's at §16.22. Round 19's §16.145 (lane D) one: W72's at
    §16.14. Round 20's §16.151 (lane D) three: X56's two in §9 and X64's at §16.28, and §16.147 (lane RB)
    two: X36's at §16.32's `with_progress` clock bullet and X19's at §16.47's stopped copy's report,
    and §16.150 (lane E) three: X57's at §16.25, §16.34 and §16.35. Round 21's §16.157 (lane D) two: Y76's
    under §9's exit-code list and Y60's at §16.28's Configuration paragraph, and §16.155 (lane B) two:
    Y49's at §16.12's governance paragraph and Y20's at §16.32's `<run again>` table. Round 23's §16.170
    (lane K) two: A97's at §16.30's two patch-hash bullets. Round 24's §16.175 (lane E) one: B73's at §16.28's
    `legacy_waiting` row.
    """
    assert len(_pinned_dated_lines(_ROUND_11_PARAGRAPH_AMENDS)) == len(_ROUND_11_PARAGRAPH_AMENDS) == 20
    assert len(_pinned_dated_lines(_ROUND_12_PARAGRAPH_AMENDS)) == len(_ROUND_12_PARAGRAPH_AMENDS) == 12
    assert len(_pinned_dated_lines(_ROUND_13_PARAGRAPH_AMENDS)) == len(_ROUND_13_PARAGRAPH_AMENDS) == 1
    assert len(_pinned_dated_lines(_ROUND_14_PARAGRAPH_AMENDS)) == len(_ROUND_14_PARAGRAPH_AMENDS) == 6
    assert len(_pinned_dated_lines(_ROUND_15_PARAGRAPH_AMENDS)) == len(_ROUND_15_PARAGRAPH_AMENDS) == 18
    assert len(_pinned_dated_lines(_ROUND_16_PARAGRAPH_AMENDS)) == len(_ROUND_16_PARAGRAPH_AMENDS) == 8
    assert len(_pinned_dated_lines(_ROUND_17_PARAGRAPH_AMENDS)) == len(_ROUND_17_PARAGRAPH_AMENDS) == 15
    assert len(_pinned_dated_lines(_ROUND_18_PARAGRAPH_AMENDS)) == len(_ROUND_18_PARAGRAPH_AMENDS) == 13
    assert len(_pinned_dated_lines(_ROUND_18_E_PARAGRAPH_AMENDS)) == len(_ROUND_18_E_PARAGRAPH_AMENDS) == 6
    assert len(_pinned_dated_lines(_ROUND_19_D_PARAGRAPH_AMENDS)) == len(_ROUND_19_D_PARAGRAPH_AMENDS) == 1
    assert len(_pinned_dated_lines(_ROUND_19_E_PARAGRAPH_AMENDS)) == len(_ROUND_19_E_PARAGRAPH_AMENDS) == 5
    assert len(_pinned_dated_lines(_ROUND_19_B_PARAGRAPH_AMENDS)) == len(_ROUND_19_B_PARAGRAPH_AMENDS) == 1
    assert len(_pinned_dated_lines(_ROUND_20_D_PARAGRAPH_AMENDS)) == len(_ROUND_20_D_PARAGRAPH_AMENDS) == 3
    assert len(_pinned_dated_lines(_ROUND_20_RB_PARAGRAPH_AMENDS)) == len(_ROUND_20_RB_PARAGRAPH_AMENDS) == 2
    assert len(_pinned_dated_lines(_ROUND_20_E_PARAGRAPH_AMENDS)) == len(_ROUND_20_E_PARAGRAPH_AMENDS) == 3
    assert len(_pinned_dated_lines(_ROUND_21_D_PARAGRAPH_AMENDS)) == len(_ROUND_21_D_PARAGRAPH_AMENDS) == 2
    assert len(_pinned_dated_lines(_ROUND_21_B_PARAGRAPH_AMENDS)) == len(_ROUND_21_B_PARAGRAPH_AMENDS) == 2
    assert len(_pinned_dated_lines(_ROUND_23_K_PARAGRAPH_AMENDS)) == len(_ROUND_23_K_PARAGRAPH_AMENDS) == 2
    assert len(_pinned_dated_lines(_ROUND_24_E_PARAGRAPH_AMENDS)) == len(_ROUND_24_E_PARAGRAPH_AMENDS) == 1


def test_every_dated_line_from_round_11_on_is_held_by_a_test() -> None:
    """B13 round 12 N72: the bare-section gap recurred for a third round, because nothing said which dated
    lines a test holds. Every `Amended <date>, §16.<home> ...` line with a home of §16.92 or later (not a
    template's `M<n>`) is the one line of a claim the scan reads, or is pinned; a bare section that a claim
    from §16.92 on names has a pin there. Rounds 7 to 10's lines are not read: never mutation-tested."""
    _claims, bare, held_at, unheld = _dated_line_scan()
    assert unheld == []
    pins = (
        *_ROUND_11_PARAGRAPH_AMENDS,
        *_ROUND_12_PARAGRAPH_AMENDS,
        *_ROUND_13_PARAGRAPH_AMENDS,
        *_ROUND_14_PARAGRAPH_AMENDS,
        *_ROUND_15_PARAGRAPH_AMENDS,
        *_ROUND_16_PARAGRAPH_AMENDS,
        *_ROUND_17_PARAGRAPH_AMENDS,
        *_ROUND_18_PARAGRAPH_AMENDS,
        *_ROUND_18_E_PARAGRAPH_AMENDS,
        *_ROUND_19_D_PARAGRAPH_AMENDS,
        *_ROUND_19_E_PARAGRAPH_AMENDS,
        *_ROUND_19_B_PARAGRAPH_AMENDS,
        *_ROUND_20_D_PARAGRAPH_AMENDS,
        *_ROUND_20_RB_PARAGRAPH_AMENDS,
        *_ROUND_20_E_PARAGRAPH_AMENDS,
        *_ROUND_21_D_PARAGRAPH_AMENDS,
        *_ROUND_21_B_PARAGRAPH_AMENDS,
        *_ROUND_22_RA_PARAGRAPH_AMENDS,
        *_ROUND_23_E_PARAGRAPH_AMENDS,
        *_ROUND_23_B_PARAGRAPH_AMENDS,
        *_ROUND_23_K_PARAGRAPH_AMENDS,
        *_ROUND_24_E_PARAGRAPH_AMENDS,
    )
    pinned = _pinned_dated_lines(pins)
    text = CONTRACTS.read_text(encoding="utf-8")
    lines = [
        found.start()
        for found in re.finditer(r"Amended\s+\d{4}-\d\d-\d\d,\s+§16\.(\d+)\b(?!\s+[A-Z]<n>)", text)
        if int(found.group(1)) >= 92
    ]
    assert len(lines) >= 81, "the scan is broken"
    loose = [" ".join(text[at : at + 100].split()) for at in lines if at not in held_at | pinned]
    assert loose == [], "a dated line no test holds: give it a claim of its own, or pin it"
    later = sorted((home, section) for home, section in bare if int(home[3:]) >= 92)
    assert ("16.92", "16.14") in later and ("16.103", "16.34") in later
    for home, section in later:
        assert any(s == section and m.startswith(f"§{home} ") for s, _, m in pins), (
            f"§{home} names §{section}"
        )


def test_k02s_dated_line_follows_its_own_text() -> None:
    """B13 round 14 Q68: §16.106 O11, O30's dated line sat in the middle of §16.14's K02 paragraph, so K02's
    own sentences after it (2026-10-05: the exit 2 run with no folder, the exit 0 run, `UV_TOOL_BIN_DIR`) read
    as part of the 2026-10-17 amendment, the splice §16.103 N68 undid for v20. It ends the paragraph now,
    still inside it, so the dated-line scan reads it in K02's block."""
    k02 = " ".join(
        _contracts_section(
            "**SUPERSEDED in part (2026-10-05, KISS K02): install.sh ends on the loop's NEXT.**", "\n\n"
        ).split()
    )
    dated = k02.index(
        "Amended 2026-10-17, §16.106 O11, O30: when every blocking `[FAIL]` line's fix is the person's"
    )
    assert k02.index("A run that created the config and has no folder to sync exits 2") < dated
    assert k02.index("uv runs with `UV_TOOL_BIN_DIR` pinned to `~/.local/bin`.") < dated
    assert k02.endswith("any other blocking `[FAIL]` keeps `fix the [FAIL] lines above ...`.")


def test_the_q66_lines_onedrive_tokens_are_the_sign_in_maps() -> None:
    """B13 round 15 S37, S56 (one defect): §16.115 Q66's line in §16.109 O12's paragraph said the report
    says OneDrive for `apps=sharedlibraries`, a token §16.110 Q52 took out of `_SIGN_IN_APPS` after that line
    landed. The line stays; the dated line after it gives the map and the fallback as `_signed_out` reads
    them."""
    from agentsync import setup_report  # noqa: PLC0415

    o12 = " ".join(
        _contracts_section(
            "**The intro and both guides say what v21 does at the sign-in stop with nobody to ask**", "\n\n"
        ).split()
    )
    line = o12[o12.index("Amended 2026-10-18, §16.121 S37, S56 (by §16.110 Q52):") :]
    assert "sharedlibraries" not in setup_report._SIGN_IN_APPS
    assert "`_SIGN_IN_APPS` holds no `sharedlibraries` since Q52" in line
    onedrive = "OneDrive is not signed in"
    assert setup_report._signed_out({"apps": "onedrive", "synced": "2"})[0] == onedrive
    assert setup_report._signed_out({"apps": "sharedlibraries"})[0] == onedrive
    assert setup_report._signed_out({"apps": "sharedlibraries", "synced": "0"})[0] == onedrive
    assert (
        "OneDrive for `apps=onedrive`, or for a line with no `apps=` token of the map and no synced count"
        " of 1 or more" in line
    )
    said = setup_report._signed_out({"apps": "sharedlibraries", "synced": "2"})[0]
    assert f"`apps=sharedlibraries` with `synced=2` reads `{said}`" in line


def test_the_guide_gives_a_native_pandoc_copy_its_own_fix(
    sample_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 15 S50: since round 14 Q60 doctor's line for the operator's native arm64 pandoc says
    `(native arm64 copy)`, and its fix is to replace or remove that copy, with no slow-start reason and no
    Rosetta words. setup-feedback.md gave every pandoc FAIL the bundled pandoc's fix and read any `Bad CPU
    type` as a Mac without Rosetta. The guide quotes the native fix as `_check_pandoc` builds it."""
    from agentsync.ops import doctor  # noqa: PLC0415

    copy = Path.home() / ".local/share/agentsync/pandoc/<version>-arm64/bin/pandoc"
    monkeypatch.setattr(doctor, "_pandoc_route", lambda config: (copy, "native"))
    [result] = doctor._check_pandoc(sample_config)
    assert not result.ok and result.fix is not None and "Rosetta" not in result.fix
    page = _page("docs/deploy/setup-feedback.md")
    # B13 round 15 S47: that fix is the person's, and the guide says so
    assert (
        f"Its fix is the person's: `{result.fix}`, because reinstalling the bundled pandoc does not touch"
        " that copy" in page
    )
    assert "its time-out line gives no slow-first-start reason" in page
    assert "For a `(native arm64 copy)` line, the person replaces or removes that copy instead of" in page
    code = Path(doctor.__file__).read_text(encoding="utf-8")
    intel = "(it is an Intel program, which an Apple silicon Mac runs only with Rosetta)"
    assert intel in code and '"native": " (native arm64 copy)"' in code
    assert (
        f"could not be started: Bad CPU type in executable {intel}`, the bundled or configured pandoc's line,"
        " is" in page
    )
    assert (
        "- `pandoc ... could not be started: Bad CPU type in executable` is an Apple silicon Mac" not in page
    )
    assert "The same error on a `(native arm64 copy)` line is not about Rosetta" in page


def test_the_deploy_guide_names_every_file_install_sh_keeps_in_local_work() -> None:
    """B13 round 15 S61: since round 14 Q29 install.sh writes `setup/local-work/.not-sent` beside the kept
    patches, and docs/deploy/README.md's `setup/` row, which names that folder's files one by one, did not
    name it. Every dot-file install.sh writes in `$kept` (local-work) is named in the row."""
    script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert 'kept="$SETUP_DIR/local-work"' in script
    names = set(re.findall(r'>\s*"\$kept/(\.[A-Za-z][\w.-]*)"', script))
    assert ".not-sent" in names
    [row] = [
        line
        for line in (ROOT / "docs/deploy/README.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("| `~/agent-context/setup/`")
    ]
    for name in sorted(names):
        assert f"`local-work/{name}`" in row, name
    assert "it is not copied into `bring-back.md`), `proxy-hosts`" in row


def test_every_exit_code_a_job_reports_has_its_meaning_everywhere() -> None:
    """B13 round 17 U84: since round 16 T12 a sync exits 65 on a manifest that is no database or a damaged
    one, and a LaunchAgent's job passes it on, but the deploy guide's exit-code list, the report's
    `EXIT_MEANINGS` and install.sh's `rc_meaning` had no 65: the report printed a bare 65 and install.sh
    `exit 65`. Every `cli.EXIT_*` code has a meaning in each, and the guide lists every one a job can end on
    (a job's arguments are written by install-agent, so never `2`, a usage error). B13 round 18 V82: the
    guide had no 64, the launcher's usage exit, which a job whose program is not the launcher's pinned one
    ends on at every run (`EXIT_MEANINGS` and `rc_meaning` decode it), and called 78 a bad sources.toml only,
    though an invalid policy.toml exits 78 too. The launcher's own codes a job ends on, 64 and 80, are read
    from its source. B13 round 19 W72: so is 71, its spawn failure (a pinned interpreter that is gone ends
    every run on it), which none of the three decoded: the report printed a bare 71 and install.sh
    `exit 71`."""
    from agentsync import cli, setup_report  # noqa: PLC0415
    from agentsync.ops import doctor, launchd  # noqa: PLC0415

    codes = {v for k, v in vars(cli).items() if k.startswith("EXIT_") and isinstance(v, int)}
    assert {0, 1, 2, 65, 75, 77, 78, 79} <= codes, "the scan is broken"
    assert codes <= set(setup_report.EXIT_MEANINGS)
    fn = re.search(r"^rc_meaning\(\) \{\n.*?^\}\n", SCRIPTS[0].read_text(encoding="utf-8"), re.M | re.S)
    assert fn is not None
    order = sorted(codes)
    run = fn.group(0) + 'for c in "$@"; do rc_meaning "$c"; echo; done'
    cp = subprocess.run(
        ["/bin/bash", "-c", run, "-", *map(str, order)], capture_output=True, text=True, check=True
    )
    meanings = dict(zip(order, cp.stdout.splitlines(), strict=True))
    assert not [c for c, m in meanings.items() if m == f"exit {c}"], meanings
    assert meanings[65] == setup_report.EXIT_MEANINGS[65]
    readme = (DEPLOY / "README.md").read_text(encoding="utf-8")
    listed = " ".join(readme.split("## Exit codes the jobs report", 1)[1].split("\n## ", 1)[0].split())
    swift = (ROOT / "launcher" / "Sources" / "main.swift").read_text(encoding="utf-8")
    refused = int(re.findall(r"^let exUsage: Int32 = (\d+)$", swift, re.M)[0])
    denied = int(re.findall(r"^let exitTCCDenied: Int32 = (\d+)$", swift, re.M)[0])
    [spawn] = map(int, re.findall(r"^let exOSErr: Int32 = (\d+)$", swift, re.M))
    assert (refused, denied, spawn) == (64, launchd.EXIT_TCC_DENIED, 71)
    assert {refused, denied, spawn} <= set(setup_report.EXIT_MEANINGS)
    assert set(map(int, re.findall(r"(?:^|· )`(\d+)`", listed))) == codes - {cli.EXIT_USAGE} | {
        refused,
        denied,
        spawn,
    }
    cp = subprocess.run(
        ["/bin/bash", "-c", fn.group(0) + 'rc_meaning "$1"', "-", str(spawn)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert cp.stdout == setup_report.EXIT_MEANINGS[spawn] != f"exit {spawn}"
    spawn_failed = setup_report.EXIT_MEANINGS[spawn]
    assert spawn_failed.startswith("the launcher could not start agentsync: the Python it runs is missing")
    assert "and a SPAWN_FAILED line in the job's error log names the program and the error;" in spawn_failed
    # B13 round 20 X44: its callers put it in brackets (``71 (...)``, ``exit 71 (...)``), so a bracket of its
    # own nested there, and "re-run the install command" did not say which run: --report-only builds nothing
    assert "(" not in spawn_failed and ")" not in spawn_failed, spawn_failed
    decoded = setup_report.decode_exit(str(spawn))
    assert decoded == f"{spawn} ({spawn_failed})" and decoded.count("(") == 1, decoded
    # B13 round 21 Y35, Y46, Y56: round 19's "the options of the run that installed agentsync", which round
    # 20 X21 dropped from doctor's AGAIN_UNDER_INSTALL_SH, under a comment saying the two match. A 71 at every
    # run is a job pinned to a Python that moved: the re-pin, which doctor's plain run does not do
    assert (
        "a 71 at every run means run ~/.local/bin/agentsync install-agent, which points the job at the Python"
        " agentsync now runs in, or install.sh again with --confirm-install-agent, not --report-only, which"
        " builds nothing" in spawn_failed
    )
    assert "options of the run" not in spawn_failed
    # the plain run's own words (round 21 Y20 made them a clause of their own)
    assert "keeps the folders already synced" in doctor.AGAIN_UNDER_INSTALL_SH
    assert "keeps the folders already synced" not in spawn_failed
    assert "re-run the install command" not in spawn_failed
    assert (
        "`71` the launcher could not start agentsync: the Python it runs is missing or not executable (a"
        " `SPAWN_FAILED` line in the job's error log" in listed
    )
    assert (
        "a 71 at every run means run `~/.local/bin/agentsync install-agent`, which points the job at the"
        " Python agentsync now runs in, or `install.sh` again with `--confirm-install-agent`, not"
        " `--report-only`, which builds nothing · `75`" in listed
    )
    assert (
        "`65` the state file (`manifest.sqlite`) is no database or a damaged one: you, not an agent" in listed
    )
    assert f"`64` {setup_report.EXIT_MEANINGS[64]} (a `PROGRAM_REFUSED` or `USAGE` line" in listed
    assert setup_report.EXIT_MEANINGS[78] == "configuration invalid: sources.toml or policy.toml"
    # B13 round 21 Y62: 1, 65, 77 and 78 nested a bracket in decode_exit's own, the shape X44 removed from 71
    assert all("(" not in m and ")" not in m for m in setup_report.EXIT_MEANINGS.values())
    assert all(setup_report.decode_exit(str(k)).count("(") == 1 for k in setup_report.EXIT_MEANINGS)
    assert "`78` bad `sources.toml` or `policy.toml` ·" in listed


def test_the_guides_say_a_git_remotes_host_is_redacted_and_kept() -> None:
    """B13 round 17 U85: since round 16 T88 both readers redact the host of a git remote's URL as a
    `<proxy-N>`, and `--log-start` and `--log` keep it in `proxy-hosts`, but the deploy guide and the feedback
    guide named only the proxy and URL-rewrite hosts there. Each says it now, by the rule the code applies:
    a `://` URL other than `file://` or `[user@]host:path`, a host with a dot, no public or GitHub host."""
    from agentsync import setup_report  # noqa: PLC0415

    script = SCRIPTS[0].read_text(encoding="utf-8")
    body = script.split("\nrecord_proxy_hosts() {\n", 1)[1].split("\n}\n", 1)[0]
    assert "\t\tgit_remote_hosts # B13 round 16 T88\n" in body
    # B13 round 17 U15, U93: each remote URL registers as a rewrite base, its host first
    assert "for _host, url in _git_remotes(docs)" in inspect.getsource(setup_report)
    host = setup_report._remote_url_host
    assert (
        host("https://git.contoso.example/team/x.git")
        == host("me@git.contoso.example:x")
        == ("git.contoso.example")
    )
    for local in ("file:///srv/x.git", "../x", "/srv/x.git", "origin", "intranet:x", "git@github.com:a/b"):
        assert host(local) is None, local
    assert host("https://codeload.githubusercontent.com/a") is None and host("https://pypi.org/x") is None
    readme = " ".join((DEPLOY / "README.md").read_text(encoding="utf-8").split())
    assert (
        "`proxy-hosts` (the proxy and git URL-rewrite host names, and since 2026-10-18 the host of each git "
        "remote's URL, in force when the agent ran `--log-start` and `--log`"
    ) in readme
    assert "only to keep those hosts redacted after the proxy is unset or the remote changed" in readme
    page = " ".join(_feedback_page().split())
    assert (
        "the proxy, git proxy and URL rewrite hosts, and the host of each git remote's URL "
        "(since 2026-10-18), in force at that call"
    ) in page
    [row] = [ln for ln in _feedback_page().splitlines() if ln.startswith("| `<email-N>`, `<guid-N>`")]
    assert (
        "the host of each git remote's URL (`[remote \"<name>\"] url`, and, since 2026-10-21, git's older"
        ' dotted `[remote.<name>]` such as `[remote.origin]` or `[remote.contoso "docs"]`, in both reports'
        " (B13 round 22 Z76, CONTRACTS §16.158 and §16.159); from the same git config files and, "
        "since 2026-10-19, the docs repo's own config, read last; B13 round 16 T88, round 17 U93), and, "
        "since 2026-10-19, that URL's path when a word in it is not only one of those generic words, as a "
        "rewrite's (U15): a `://` URL other than `file://`, or git's `[user@]host:path` form, whose host "
        "has a dot and is none of the installer's public hosts, github.com or another GitHub host "
        "(`*.github.com`, `*.githubusercontent.com`); a local remote (a relative or absolute path, a bare "
        "word) names no host,"
    ) in row


def test_the_guide_gives_the_list_tail_for_a_mirrored_folder_whose_block_is_gone() -> None:
    """B13 round 17 U91: round 16 T6, T30 gave step 1's list a NEXT tail for a folder still mirrored after its
    [[source]] block was deleted, and the log field `vanished=N`, but the guide's list section, which gives
    every other list case and its log field, had neither, and its install.log row named no list field past
    `synced=`. The section quotes the tail install.sh writes, and the row points there for the others."""
    script = SCRIPTS[0].read_text(encoding="utf-8")
    [one] = re.findall(r'^\t\tvtail="; (1 folder in the list above [^"]*?)\(one', script, re.M)
    [other] = re.findall(
        r'^\t\tvtail="\$vtail; (1 source that no folder in the list above adds back[^.]*\.)', script, re.M
    )
    assert '[ $((vback + vother)) -eq 0 ] || vlog=" vanished=$((vback + vother))"' in script
    page = " ".join(_feedback_page().split())
    section = page.split("### A Mac that is already set up", 1)[1].split("### ", 1)[0]
    words = one.replace("$CONFIG", "<config>").replace("$vpaths", "<folder>")
    assert (
        "a folder still mirrored after its `[[source]]` block was deleted (the manifest still holds its id; "
        f"sources.toml no longer has it) drops `(none is needed)` from the `NEXT:` and adds `; {words}(one "
        '--source-local "<folder>") or its [[source]] block is put back, as it was or retired`'
    ) in section
    assert f"never by its id (`; {other.replace('$CONFIG', '<config>')} ...`)" in section
    assert "its log line adds `vanished=N` (those folders and sources together)" in section
    assert "`config.vanished_sources` [FAIL] ([CONTRACTS §16.124](../design/CONTRACTS.md)" in section
    [row] = [
        ln for ln in _feedback_page().splitlines() if ln.startswith("| `~/agent-context/setup/install.log`")
    ]
    assert (
        "and the list's other fields (`gone=`, `missing=`, `apps=`, `vanished=`, `config_err=`) are in "
        "[A Mac that is already set up](#a-mac-that-is-already-set-up)"
    ) in row
    for field in ("`gone=N`", "missing=M", "apps=<app>"):
        assert field in section, field


def test_the_guide_gives_the_lists_config_stop_tail() -> None:
    """B13 round 18 V77: since round 17 U12 step 1's list asks the installed agentsync whether step 2's config
    step would stop on policy.toml, a `[governance]` value or the state file, adds a `NEXT:` tail that says so
    and logs `config_err=<words>`, but the guide's list section had no bullet for it and its install.log row
    named no such field. The bullet quotes both tails' fixed openings and words as install.sh writes them."""
    script = SCRIPTS[0].read_text(encoding="utf-8")
    tails = re.findall(r'^\t\t\tctail="(; step 2 [^"$]*)\$ctail: ([^"]*)"$', script, re.M)
    assert [head for head, _ in tails] == [
        "; step 2 stops at its config step on ",
        "; step 2 reinstalls agentsync first, and if this checkout's agentsync rejects it too, step 2 stops "
        "at its config step on ",
    ]
    [end] = {end for _, end in tails}
    words = re.findall(
        r'^\t\tcase ",\$cerr," in \*",(\w+),"\*(?: \| \*",[\w-]+,"\*)?\) '
        r'ctail="(?:\$\{ctail:\+\$ctail and \})?([^"]*)" ;; esac$',
        script,
        re.M,
    )
    assert [w for w, _ in words] == ["policy", "governance", "state", "remote"]
    assert '\t\tclog=" config_err=$cerr"\n' in script
    # B13 round 20 X72: "state-table" gives the state clause; round 18 V21's tail of its own, "(at its status
    # or its first sync)", was false for an index root, which a status and a sync with nothing new never read
    assert '\t\tcase ",$cerr," in *",state,"* | *",state-table,"*) ctail=' in script
    assert "part of which cannot be read" not in script
    page = " ".join(_feedback_page().split())
    section = page.split("### A Mac that is already set up", 1)[1].split("### ", 1)[0]
    assert f"adds `{tails[0][0]}<what>: {end}`" in section
    assert f"the tail opens `{tails[1][0]}<what>: ...`" in section
    for _, said in words:
        assert f"`{said.replace('$CONFIG', '<config>')}`" in section, said
    assert "Since v29 that word gives the same `agentsync's state file, which cannot be read`" in section
    # B13 round 20 X9, X29: a state file this account cannot open has its own tails, by owner; round 21 Y24,
    # Y57: by the owner doctor.state_path_fix gives, and the -wal and -shm by the step that may stop on them
    opened = re.findall(
        r'^\t\tcase ",\$cerr," in \*",(state-[\w-]+),"\*\) stail="(; [^"]*)" ;; esac$', script, re.M
    )
    assert [w for w, _ in opened] == [
        # B13 round 23 A17, A83: the lock, the heartbeat, governance/ and its files, first (a later case wins)
        "state-file-owner",
        "state-file-access",
        "state-file-refused",
        "state-owner",
        "state-access",
        "state-refused",
        "state-side-owner",
        "state-side-access",
        "state-side-refused",
        "state-notfile",  # B13 round 22 Z11: a folder where manifest.sqlite belongs
        "state-lost",  # B13 round 24 ruling 7: no manifest, while heartbeat.json records a pass
        "state-notdir",  # B13 round 23 ruling 4: no folder where the state folder belongs
    ]
    for word, said in opened:
        assert f"`{word}`, `{said.replace('$fnoun', '<file>')}`" in section, word
    assert (
        "its log line adds `config_err=<words>`, the comma-joined fixed words `policy`, `governance`, "
        "`state`, `remote`, `state-table`, `state-access`, `state-owner`, `state-refused`, "
        "`state-side-access`, `state-side-owner`, `state-side-refused`, `state-notfile`, `state-lost`, "
        "`state-notdir`, "
        "`state-file-access`, `state-file-owner` and `state-file-refused`" in section
    )
    words_read = (
        "state-access|state-owner|state-refused|state-side-access|state-side-owner|state-side-refused"
        "|state-notfile|state-lost|state-notdir|state-file-access|state-file-owner|state-file-refused)$"
    )
    assert words_read in script, "the list reads every word config_errors writes"
    [row] = [
        ln for ln in _feedback_page().splitlines() if ln.startswith("| `~/agent-context/setup/install.log`")
    ]
    assert "`vanished=`, `config_err=`) are in" in row


def test_the_guides_placeholder_rows_follow_round_17s_redaction() -> None:
    """B13 round 18 V76: round 17 changed three placeholders and the guide's rows kept the old reach: U61's
    `<link-N>` is in the shell fallback report too, U8, U23 and U44 name a vanished source id
    `(<source-N>, not in the config)` in the evidence parts and the loads line, which the `<folder-N>` row
    still said name it by place, and U46 redacts the folder such a source's walk recorded; U93 and U15's
    remote hosts are read from the docs repo's config too, with the URL's path (the `<proxy-N>` row, pinned
    in `test_the_guides_say_a_git_remotes_host_is_redacted_and_kept`)."""
    script = SCRIPTS[0].read_text(encoding="utf-8")
    assert 'add_pair "$e" "<link-$lk>"' in script  # the shell report writes it
    rows = _feedback_page().splitlines()
    [link] = [ln for ln in rows if ln.startswith("| `<link-N>` |")]
    assert (
        "in both reports (the agentsync report since 2026-10-18, the shell fallback report since 2026-10-19, "
        "B13 round 17 U61)"
    ) in link
    assert "in the agentsync report, the Environment line names the folder it points into" in link
    [folder] = [ln for ln in rows if ln.startswith("| `<library-N>`, `<folder-N>`, `<source-N>` |")]
    assert "which name it by place" not in folder
    assert (
        "the evidence parts and the Configuration section's loads line included, as `(<source-N>, not in the "
        "config)` there (by place only where the report cannot read it, below)"
    ) in folder
    # in both reports since B13 round 18 V46: the shell fallback reads it from doctor's fix, in the forms
    # cycle.vanished_add_back_tail writes (round 19 W34)
    assert (
        "since 2026-10-19, the folder each such source's walk recorded (the agentsync report, B13 round 17 "
        "U46; the shell fallback report reads it from doctor's fix and the sync's refusal, "
        '`--source-local "<folder>"` or `add-source "<folder>"`, single-quoted for a name holding `!`, '
        "round 18 V46, round 19 W34)"
    ) in folder
    [place] = [ln for ln in rows if ln.startswith("| `(source N)`, `(not in the config, N)` |")]
    assert (
        "otherwise it reads `(<source-N>, not in the config)`" in place
    )  # the row the folder row now agrees with


def test_the_guides_outcome_rules_and_sync_row_give_round_17s_report_words() -> None:
    """B13 round 18 V78: round 17 U35 reads an earlier attempt that only listed the folders as `stopped at
    step 1 (after its folder list, with no report)`, and U42 names an earlier run's sync when the last install
    run stopped before its own, but the guide's outcome rules predicted `failed at step 1` for the first and
    its first-sync row had no clause for the second. Each quotes the report's words, from setup_report. B13
    round 19 W57: the row said the `sync`/`first sync` label follows the last run's config note, but round 18
    V48 takes it from the run whose sync the line shows (`pool[-1]`), the earlier run in this very case."""
    from agentsync import setup_report  # noqa: PLC0415

    source = inspect.getsource(setup_report)
    [note] = re.findall(r'_LISTED_AND_LEFT,\), outcome\.version, "([^"]+)"', source)
    [where] = re.findall(r'f"it stopped at its \{failing\[0\]\} step" if failing else "([^"]+)"', source)
    [head] = re.findall(
        r'parts\.append\(f"(no first sync in the last install run) \(install\.log: \{where\}\)"', source
    )
    [newest] = re.findall(
        r'f"(the newest earlier run\'s sync): \{text\} \(install\.log: \{_run_named', source
    )
    page = " ".join(_feedback_page().split())
    rules = page.split("- **Stopped at step 1**", 1)[1].split("A line logged after an attempt's", 1)[0]
    assert f"an earlier attempt also reads `stopped at step 1 ({note})`, with no deviation" in rules
    assert "The latest attempt is never read this way" in rules
    assert (
        "1 when only `--list-folders` ran (for an earlier attempt whose last list worked, see the U35" in page
    )
    [row] = [
        ln for ln in _feedback_page().splitlines() if ln.startswith("| Summary: `first sync:` or `sync:` |")
    ]
    assert f"the clause reads `{head} (install.log: it stopped at its <step> step)`, or `{where}`" in row
    assert f"then `· {newest}: <result> (install.log: the install run of <time>)`" in row
    assert 'config = pool[-1].step("config") or {}' in source
    assert "label follows the config note of the run whose sync the line shows" in row
    assert "label follows the last run's config note" not in row


def test_the_guides_give_the_reports_time_with_a_slow_doctor() -> None:
    """B13 round 19 W58: both guides said the report finishes within 12 s, but round 18 V4 gives a slow doctor
    at least `_DOCTOR_GRACE_S` and moves the deadline to make room (the report's own figure is about 35 s, and
    its took line names the moved limit), and V55 reads the docs repo's remotes outside the budget. Each
    figure and the took line's words are read from setup_report."""
    from agentsync import setup_report  # noqa: PLC0415

    assert (setup_report.TIME_BUDGET_S, setup_report._DOCTOR_GRACE_S) == (12.0, 20.0)
    source = inspect.getsource(setup_report)
    flat = " ".join(source.split())
    assert "the report then takes at most about 35 s, inside install.sh's 120 s bound" in flat
    [took] = re.findall(
        r'grace = f", moved to \{budget_s \+ r\.doctor_grace:\.1f\}s (for a slow doctor)"', source
    )
    page = " ".join(_feedback_page().split())
    assert "the whole report within 12 s" not in page
    assert (
        "the whole report within a 12 s budget (`TIME_BUDGET_S`); a slow doctor is given at least 20 s and"
        " the deadline moves to make room, so on a busy Mac the report can take about 35 s" in page
    )
    assert (
        f"e.g. `time limit 12s, moved to 26.2s {took}`), still inside install.sh's 120 s bound on it." in page
    )
    assert "a `governance.remote` FAIL) is not counted against the budget." in page
    readme = " ".join((DEPLOY / "README.md").read_text(encoding="utf-8").split())
    assert "takes under 12 s." not in readme
    assert "usually takes under 12 s (about 35 s at most, when doctor is slow)." in readme


def test_the_contract_calls_only_functions_the_code_has() -> None:
    """B13 round 19 W44, W59 (one defect): §16.137 V7 named `cycle.vanished_readd_tail(roots, every=...)`, a
    name lane B's merge had made `vanished_add_back_tail`, and no test read a function the prose names. Every
    backticked call `<module>.<name>(` from §16.68 on names a function that agentsync module has."""
    import pkgutil  # noqa: PLC0415

    import agentsync  # noqa: PLC0415

    modules: dict[str, str] = {}
    for found in pkgutil.walk_packages(agentsync.__path__, "agentsync."):
        modules.setdefault(found.name.rsplit(".", 1)[1], found.name)
    text = CONTRACTS.read_text(encoding="utf-8")
    calls = [
        (module, name)
        for span in re.findall(r"`([^`\n]+)`", text[text.index("### 16.68 ") :])
        for module, name in re.findall(r"(?<![\w.])([a-z_]+)\.([A-Za-z_]\w*)\(", span)
        if module in modules
    ]
    assert len(calls) >= 13 and ("cycle", "vanished_add_back_tail") in calls, "the scan is broken"
    missing = [f"{m}.{n}" for m, n in calls if not hasattr(importlib.import_module(modules[m]), n)]
    assert missing == []


def test_the_vanished_add_back_lines_say_where_v7_moved_it() -> None:
    """B13 round 19 W44, W60 (one defect): §16.131 U9 still said the add-back is the CLI's own verb, not
    install.sh's, with `doctor._vanished_add_backs` as the rule, and its §16.134 V18 line that the sync's
    refusal is unchanged; round 18 V7 moved the rule and the tail to cycle, gave install.sh its own
    `--source-local`, and ended the sync's refusal with the same tail. The old lines stay; a dated line after
    them says what V7 changed, and one at V18's paragraph where its `doctor.` names are defined. Each claim is
    read from the code."""
    from agentsync import cycle  # noqa: PLC0415
    from agentsync.ops import doctor  # noqa: PLC0415

    u9 = " ".join(
        _contracts_section("**U9, U25, U46: the vanished-source fix names the add-source", "\n\n").split()
    )
    [line] = re.findall(r"Amended 2026-10-20, §16\.145 W44, W60 \(by §16\.137 V7\): .*", u9)
    assert (
        "the rule and the tail moved to `cycle.vanished_add_backs` and `cycle.vanished_add_back_tail`" in line
    )
    assert 'the command is install.sh\'s own `<install.sh> --source-local "<folder>"`' in line
    assert "The sync's exit-78 refusal now ends with the same tail after `VANISHED_ADVICE`" in line
    tail = inspect.getsource(cycle.vanished_add_back_tail)
    assert (
        'os.environ.get("AGENTSYNC_NO_NEXT_HINT"' in tail and '"--source-local {_shown_arg(str(r))}"' in tail
    )
    refusal = inspect.getsource(cycle._Cycle._refuse_vanished_sources)
    assert 'raise ConfigError(f"{vanished_detail(vanished)}; {VANISHED_ADVICE}{tail}")' in refusal
    assert "return vanished_add_backs(config, manifest.get_meta, vanished)" in inspect.getsource(
        doctor._vanished_add_backs
    )
    assert doctor.vanished_add_backs is cycle.vanished_add_backs
    assert doctor.vanished_add_back_tail is cycle.vanished_add_back_tail
    v18 = " ".join(_contracts_section("**The loads line ends as Doctor's fix does** (V18;", "\n\n").split())
    assert (
        "Amended 2026-10-20, §16.145 W60 (by §16.137 V7): both helpers are defined in `cycle` since V7" in v18
    )


def test_v5s_dated_line_quotes_the_markers_retry_clause() -> None:
    """B13 round 19 W61: §16.137 V5 quotes the speech marker's retry clause as `; scripts/install.sh tries
    ...`, the form before round 18 V68 named install.sh by its path. A dated line quotes the clause as
    `speech._TIMED_OUT` ends."""
    from agentsync import skill  # noqa: PLC0415

    v5 = " ".join(_contracts_section("**V5, V28: a speech helper build that ran out of time", "\n\n").split())
    clause = speech._TIMED_OUT.removeprefix(speech.BUILD_TIMED_OUT)
    assert clause == f"; {skill.INSTALL_SH} tries the build again on its next run"
    assert (
        "Amended 2026-10-20, §16.145 W61 (by §16.138 V68): the marker's retry clause names install.sh by its"
        f" path: `{clause}` (`skill.INSTALL_SH`, V68)." in v5
    )


def test_v73s_lines_say_which_read_fails_status() -> None:
    """B13 round 19 W62: V73's dated lines at §16.129 and §16.133 called the cursors table one "no status read
    touches", then said status prints `cannot read the state` for it. Status's detail read of the sources
    reads that table (`source_statuses`, through `get_cursor`); only the loop's read decides the
    `state_dir.manifest` FAIL (`_loop_read`, then `_with_damaged_manifest`). Both lines name the loop's read,
    and each claim is read from the code."""
    from agentsync import cli, cycle  # noqa: PLC0415

    marker = r"Amended 2026-10-19, §16\.139 V73 \(by §16\.131 U64, U95\): .*"
    [u55] = re.findall(
        marker,
        " ".join(_contracts_section("**An exit 65 at the first sync is the person's** (U55", "\n\n").split()),
    )
    [u84] = re.findall(
        marker,
        " ".join(
            _contracts_section("**Exit 65 has its meaning in each exit-code list** (U84;", "\n\n").split()
        ),
    )
    assert "no status read touches" not in u55 + u84
    assert "when the loop's read of its state hits the damage (the items or runs table, measured)" in u55
    assert (
        "Damage under a table that read does not touch (the cursors table, measured) still passes status"
        in u55
    )
    assert (
        "(`source_statuses`, through `get_cursor`) hits the damage and prints only `status: cannot read"
        in u55
    )
    assert "Damage under a table the loop's read does not touch (the cursors table, measured) has no" in u84
    assert "though status's detail read of the sources prints `status: cannot read the state: ...`." in u84
    build = inspect.getsource(cli._build_status)
    assert "_loop_read(config)" in build and "_with_damaged_manifest(config, checks, state_exc)" in build
    assert "source_statuses(config, manifest" in inspect.getsource(cli._status_lines)
    assert "manifest.get_cursor(src.id)" in inspect.getsource(cycle.source_statuses)


def test_the_deploy_guide_gives_not_sents_third_field() -> None:
    """B13 round 16 T73: since round 15 S34 a `.not-sent` record whose patch a report sent has a third field,
    when that report's attempt began, and goes once a later attempt reports. The guide's `setup/` row gave
    two fields and never said when a record goes."""
    script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert 'notsent+="$id $nsat $begin"' in script  # sent now: the third field is the attempt's start
    assert 'if [ -n "$sentat" ] && [ "$sentat" != "$begin" ]; then' in script  # another attempt's: it goes
    [row] = [
        line
        for line in (ROOT / "docs/deploy/README.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("| `~/agent-context/setup/`")
    ]
    [not_sent] = re.findall(r"`local-work/\.not-sent` \(([^()]*(?:\([^()]*\)[^()]*)*)\)", row)
    assert "and that report's time, and, once a report sends it, when that report's attempt began" in not_sent
    assert "in each report of that attempt, and leaves the list once a later attempt reports" in not_sent


def test_the_guide_asks_for_a_not_repeated_patch_by_its_hash() -> None:
    """B13 round 16 T28: the guide told the reader to ask for "the file the line names", but install.sh's
    `not repeated:` lines name a folder and a hash prefix, and on purpose no file (a patch's name carries its
    commit's subject). The guide asks for the patch in that folder whose SHA-256 starts with the line's 12
    digits, the prefix install.sh cuts from `shasum -a 256` of the file. B13 round 17 U82: since round 16 T24
    install.sh writes four such lines, a held and a last-written form each with a hash and each without (a
    patch this account cannot read now), and this test read only the two `skipped="${skipped}` ones; the guide
    gives the no-hash lines their route: make the patch readable and run the report again."""
    script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    lines = [
        ln for ln in script.splitlines() if re.match(r'\s*skipped(?:\+="|="\$\{skipped\})not repeated: ', ln)
    ]
    assert len(lines) == 4
    hashed = [ln for ln in lines if ln.endswith("sha256 $sha; still in $shown/local-work\"$'\\n'")]
    blind = [ln for ln in lines if "so no line count or hash; still in $shown/local-work" in ln]
    assert len(hashed) == len(blind) == 2
    for line in lines:
        assert not re.search(r"\$\{?p\b|basename", line.replace('<"$p"', "")), line  # wc reads it; no name
    # B13 round 23 A97: sha256() is /sbin/sha256sum, or /usr/bin/shasum -a 256 elsewhere: the same digits
    assert 'sha="$(sha256 "$p" 2>/dev/null | cut -c 1-12 || true)"' in script
    from agentsync import setup_report  # noqa: PLC0415

    page = " ".join(_feedback_page().split())
    # the guide quotes the no-hash lines' own tail, as install.sh writes it
    [tail] = {
        re.findall(r"; (this account cannot read it now, [^;]*); still in \$shown", ln)[0] for ln in blind
    }
    assert (
        f"A line ending `{tail}; still in ...` has no hash to match (B13 round 17 U82). Ask the person to "
        "make that patch readable (`chmod u+r ~/agent-context/setup/local-work/*.patch`; the report gives no "
        "remedy for a patch an earlier report carried, and the command works only where their account owns "
        f"the file) and to run `{setup_report.LOCAL_WORK_AGAIN}` again: that rerun sends nothing new"
    ) in page
    # B13 round 18 V80: a carried patch is never in the report's unreadable clause (T24), so none is cited
    assert "unreadable clause says" not in page
    assert "ask for the file the line names" not in page
    assert (
        "the Mac cannot know what was copied back. The line names no file (a patch's file name carries its "
        "commit's subject), so ask the person to run "
        "`shasum -a 256 ~/agent-context/setup/local-work/*.patch` on that Mac (the folder the line's "
        "`still in` names, if it is another) and to send privately the patch "
        "whose hash starts with the line's 12 digits."
    ) in page


def test_the_guide_quotes_the_counted_re_read_clause() -> None:
    """B13 round 16 T51, T69 (one defect): since round 15 S26 the Summary's re-read clause takes its nouns
    from the counts (`N reads again by K other runs`), and the guide's `first sync:` row still quoted
    `N read(s) again by K other run(s)`. The CONTRACTS quotes have dated lines (the dated-line scan, and
    :data:`_ROUND_16_PARAGRAPH_AMENDS`)."""
    from agentsync import setup_report, text  # noqa: PLC0415

    code = " ".join(inspect.getsource(setup_report._reread_since).split())
    assert "'read')} again by \" f\"{_plural(len(others), 'other run')}" in code
    assert (text.plural(1, "read"), text.plural(1, "other run")) == ("1 read", "1 other run")
    page = " ".join(_feedback_page().split())
    assert "read(s) again by" not in page
    assert (
        "A last clause `since this install run began, N reads again by K other runs` "
        "(`1 read`, `1 other run` for one) sums what the other runs read again"
    ) in page
    # B13 round 16 T32: cli.py counts that one by number now, as the guide quotes it
    assert "`tightened N paths inside the docs repo" in page and "tightened N path(s)" not in page
    contracts = " ".join(CONTRACTS.read_text(encoding="utf-8").split("\n### 16.127 ")[0].split())
    quotes = contracts.split("N read(s) again by K other run(s)")[1:]
    assert len(quotes) == 2  # §16.14's amended line and §16.40 N32
    for quote in quotes:
        assert "§16.127 T51, T69 (by §16.116 S26): " in quote[:400]


def test_contracts_quote_the_tightened_line_as_cli_prints_it() -> None:
    """B13 round 17 U83: since round 16 T32 cli.py prints add-source's count line as `tightened N
    paths ...: they hold tenant data` (`1 path ...: it holds` for one), but §16.29 still quoted the `(s)`
    form with no dated line and §16.127 said cli.py still prints it. Every CONTRACTS paragraph that quotes
    the `(s)` form holds a dated line with the counted form, and §16.127's withdraws its claim."""
    from agentsync import cli, text  # noqa: PLC0415

    code = " ".join(inspect.getsource(cli).split())
    assert (
        "f\"tightened {plural(inside, 'path')} inside the docs repo, the cache or the logs: \" "
        "f\"{'it holds' if inside == 1 else 'they hold'} tenant data\""
    ) in code
    assert "tightened N path(s)" not in code and text.plural(1, "path") == "1 path"
    paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", CONTRACTS.read_text(encoding="utf-8"))]
    quoting = [p for p in paragraphs if "tightened N path(s)" in p]
    assert len(quoting) == 2  # §16.29's "Who calls it." and §16.127's T51 paragraph
    for para in quoting:
        [line] = re.findall(r"Amended 2026-10-19, §16\.133 U83\b(.*?)(?= Amended \d{4}-\d\d-\d\d, |$)", para)
        assert "`tightened N paths" in line, line
    who = next(p for p in quoting if p.startswith("**Who calls it.**"))
    assert (
        "§16.133 U83 (by §16.125 T32): the line counts its noun, `tightened N paths inside the docs repo, "
        "the cache or the logs: they hold tenant data` (`1 path ...: it holds tenant data` for one)"
    ) in who


def test_the_guide_places_a_vanished_sources_label_in_the_loads_line() -> None:
    """B13 round 16 T68: since round 15 S8 the Configuration section's `loads: yes, but a sync refuses it`
    line, and its step-2 form, names a source still in the manifest and gone from sources.toml as `(not in the
    config, N)`; the guide's placeholder row said that form was in the evidence parts only. Its scope clause
    stays as round 5 pinned it, and the excludes line, which reads only the configured sources, never shows
    the form. B13 round 17 U23: the form is the fallback for an id the report cannot read as a placeholder;
    the row says the placeholder form comes first."""
    from agentsync import setup_report  # noqa: PLC0415

    source = inspect.getsource(setup_report)
    assert 'return f"- loads: yes, but a sync refuses it: {live}"' in source
    assert 'return f"- loads: yes, but step 2\'s sync refused it (run {run.display_id}): "' in source
    assert setup_report._NOT_IN_CONFIG == "(not in the config, "
    assert "_NOT_IN_CONFIG" in inspect.getsource(setup_report._Labels)
    assert "for src in c.sources" in inspect.getsource(setup_report._excludes_line)
    [row] = [
        ln
        for ln in _feedback_page().splitlines()
        if ln.startswith("| `(source N)`, `(not in the config, N)`")
    ]
    assert "(evidence parts only)" not in row
    assert row.endswith(
        "so one source has one name (the evidence parts, and the Configuration section's "
        "`loads: yes, but a sync refuses it` line, or `... step 2's sync refused it` line, which names such "
        "a source the same way) |"
    )
    assert (
        "in the evidence parts and the Configuration section's `source excludes beyond agentsync's own` "
        in row
    )
    assert "`(<source-N>, not in the config)`" in row, "the one name Doctor's line gives it (U23)"


def test_contracts_quote_the_counted_unread_patch_lines() -> None:
    """B13 round 16 T74, T75, T76: round 15 S14 and S26 gave install.sh's unread-patch lines and the report's
    Installer line their count's noun, but §16.105 O25's S24 line, §16.116 S5's quote and §16.116 S26's line
    numbers did not follow. Each has a dated line: O25's quotes what install.sh prints for one patch, S5's the
    `_plural` form, and S26's names each line by words install.sh holds."""
    from agentsync import setup_report, text  # noqa: PLC0415

    script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    [again] = re.findall(r'^LOCAL_WORK_AGAIN="([^"]+)"$', script, re.M)
    assert '[ "$unread" != 1 ] || { up="1 patch file" uthem=it; }' in script
    [not_sent] = re.findall(r"printf 'not sent: %s in %s/local-work ([^']+)\\n'", script)
    [header] = re.findall(r'printf \'%s\\n\' "Section 3 sends no patch: \$up ([^"]+)"', script)
    o25 = " ".join(
        _contracts_section("**A patch this account cannot read is counted, not sent** (O25)", "\n\n").split()
    )
    [t74] = re.findall(r"Amended 2026-10-18, §16\.127 T74 \(by §16\.117 S14, S23\): .*", o25)
    assert (
        "`not sent: 1 patch file in ~/agent-context/setup/local-work "
        + not_sent.replace("%s readable", "it readable").replace("run %s again", f"run {again} again")
        + "`"
    ) in t74
    assert f"`Section 3 sends no patch: 1 patch file {header.replace('$uthem', 'it')}`" in t74
    assert "(`N patch files ... make them readable` in both for more than one)" in t74
    s5 = " ".join(
        _contracts_section(
            "**A [FAIL] fix that is the person's is no hint for the agent** (S5)", "\n\n"
        ).split()
    )
    assert "_plural(other_total, 'other instruction-like line')" in inspect.getsource(setup_report)
    lines = text.plural(0, "other instruction-like line")
    assert (
        "Amended 2026-10-18, §16.127 T75 (by §16.116 S26): the count takes its noun, so the line reads "
        f"`... and {lines} and 6 fix: on [FAIL]" in s5
    )
    s26 = " ".join(
        _contracts_section(
            "**A known count takes its noun from the count, everywhere in the report**", "\n\n"
        ).split()
    )
    # B13 round 17 U109: the line ends at the next dated line, so a later one in this paragraph is not T76's
    t76_line = r"Amended 2026-10-18, §16\.127 T76: (.*?)(?= Amended \d{4}-\d\d-\d\d, |$)"
    [t76] = re.findall(t76_line, s26)
    later = " Amended 2026-10-25, §16.999 X1: the `Contoso widget` line."
    assert re.findall(t76_line, s26 + later) == [t76]
    named = re.findall(r"`([^`]+)`", t76)
    assert len(named) == 9, named
    for words in named[:-2]:
        for part in words.split(" ... "):
            assert part.rstrip(":") in script, part
    assert named[-2:] == ["1 patch file", "N patch files"]


def test_a_round_6_item_that_amends_another_has_a_dated_line_there() -> None:
    """B13 round 7 H54: §16.66 said G59, G63, G76 and G77 amend items of §16.51, §16.54, §16.60 and §16.61,
    but wrote no dated line beside any of them, so Q77 still said the deck emitter's version is
    unchanged and F78 named a renamed test; the round 5 test reads only §16.60's F items. For every
    heading in §16.62 to §16.67 of the form "(G<n>[, G<m>]; amends §A X[, §B Y])" (and G59 at Q77, whose
    own heading names F77), the paragraph of section A that holds item X holds a dated line naming that
    G item: `Amended 2026-10-1N, §<home> G<n>` or a later round's `§16.7N H<n> (by §<home> G<n>)`."""
    sections = dict(_contracts_numbered_sections())
    claims = [("16.66", "G59", "16.54", "Q77")]
    for home in ("16.62", "16.63", "16.64", "16.65", "16.66", "16.67"):
        for para in re.split(r"\n\s*\n", sections[home]):
            flat = " ".join(para.split())
            for items, targets in re.findall(r"\((G\d+(?:, G\d+)*); amends ([^)]*)\)", flat):
                section = ""
                for number, item in re.findall(r"(?:§(\d+\.\d+) )?([FGQR]\d+)", targets):
                    section = number or section
                    claims.extend((home, g, section, item) for g in items.split(", "))
    assert len(claims) >= 7, "the scan found §16.66's amends (few, so it is broken)"
    for home, g, section, item in claims:
        at = re.escape(f"§{home} {g}")
        marker = re.compile(rf"Amended \d{{4}}-\d\d-\d\d, (?:{at}\b|§16\.\d+ [A-Z]\d+ \(by {at}\))")
        paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", sections[section])]
        assert any(marker.search(p) and re.search(rf"\b{item}\b", p) for p in paragraphs), (
            f"§{section} {item} has no dated line for §{home} {g}"
        )


def test_the_guide_classes_an_image_by_its_stub_page_not_the_ocr_probe() -> None:
    """B13 round 9 K59, K67 (one defect): since round 8 J52 and J54 the report classes an image's `no
    converter` row by its stub page's `reason:` (`_Mirror.withheld_for`), and the OCR probe decides only for a
    page it could not read, while the guide's Quarantine row and §16.28's class table said an image is
    withheld only when the OCR part reads ready and keeps `no converter` without an engine."""
    from agentsync import publish, setup_report  # noqa: PLC0415

    mirror = setup_report._Mirror.__new__(setup_report._Mirror)
    mirror.labels_active, mirror.withheld, mirror.images_by_probe = True, {".msg"}, False
    mirror.image_pages = {("s", "withheld"): True, ("s", "no-converter"): False}
    reason = f"{setup_report._NO_CONVERTER}.png"
    assert ".png" in mirror.withheld_for(reason, "s", "withheld")  # the page wins over a probe that is off
    mirror.images_by_probe = True
    assert ".png" not in mirror.withheld_for(reason, "s", "no-converter")  # and over one that is ready
    assert ".png" in mirror.withheld_for(reason, "s", "unread")  # no page read: the probe decides
    page = " ".join(_page("docs/deploy/setup-feedback.md").split())
    assert (
        "Without an OCR engine no image converter exists whatever the policy says, so an image keeps"
        not in page
    )
    assert "an OCR engine added or removed since then does not change the class" in page
    # B13 round 10 L52, L57, L70 (one defect): since round 9 K48 a page says withheld as the cycle reads it,
    # so a page from before G29, `reason: no converter for .png` under a withheld summary or body, is withheld
    # (and in QUARANTINE.tsv since round 10 L54); K58 named the unread stubs in the legend in both probe
    # states
    from agentsync import policy  # noqa: PLC0415

    banner = "> banner\n\n# a.png\n\n"
    assert policy.page_says_withheld(reason, f"{policy.REFUSED_PREFIX}{policy._WITHHELD_WORDS}: x", "")
    assert policy.page_says_withheld(reason, None, banner + policy._WITHHELD_BODY)
    assert not policy.page_says_withheld(reason, None, banner + "the file has no converter")
    assert "So is an image whose stub page's `reason:` says it is withheld." not in page
    assert "An image whose page says `no converter for` keeps the `no converter` class" not in page
    assert "(missing, a symlink, or past the report's read bound)" not in page
    assert (
        f"its `reason:`, or its summary less any leading `{policy.REFUSED_PREFIX}`, starts "
        f"`{policy._WITHHELD_WORDS}`, or its body under the heading says its content "
        "is withheld (content policy)" in page
    )
    assert (
        "the report classes it withheld, and so does `_sync/QUARANTINE.tsv`, which writes the withheld reason"
        in page
    )
    assert (
        "An image whose page says `no converter for` and nowhere says withheld keeps the `no converter` class"
        in page
    )
    assert "one that does not parse, one past the report's page-read time" in page
    assert f"at most {setup_report._PAGES_S} s), and one that says neither" in page
    mirror.pages_unread, mirror.pages_failed = 2, False  # B13 round 15 S31: "N image stubs ... are"
    ready = setup_report._unread_pages(mirror)
    mirror.images_by_probe = False
    off = setup_report._unread_pages(mirror)
    mirror.pages_failed = True
    failed = setup_report._unread_pages(mirror)
    assert f"`{ready.replace(' 2 image', ' N image')}`" in page
    assert f"`... {off.partition('counted ')[2]}`" in page
    assert f"(`{failed.partition(': ')[2]}`)" in page and "every image stub is counted" in failed
    table = " ".join(
        _contracts_section("| `withheld by label rule` | starts `no converter for `", "**OCR.**").split()
    )
    assert (
        "Amended 2026-10-14, §16.85 K59 (by §16.74 J52, J54): an image is no longer counted by whether"
        in table
    )
    assert f"`{publish._withheld_reason('<suffix>')}`" in table
    assert (
        "Amended 2026-10-14, §16.91 L52 (by §16.80 K48, K58): the page decides as `policy.page_says_withheld`"
        in (table)
    )
    j52 = " ".join(_contracts_section("**An image stub is classed as its page says** (J52", "\n\n").split())
    assert "Amended 2026-10-14, §16.91 L52 (by §16.80 K48, K58): the pages are read for a quarter" in j52
    k59 = " ".join(
        _contracts_section("**An image's class follows its stub page in the guide", "\n\n").split()
    )
    assert (
        "Amended 2026-10-14, §16.91 L57 (by §16.80 K48): the page decides through `policy.page_says_withheld`"
        in k59
    )


def test_a_page_read_that_says_neither_can_disagree_with_the_tsv() -> None:
    """B13 round 11 M79: the guide's Quarantine row and §16.91 said only a stub whose page could not be read
    can disagree with `_sync/QUARANTINE.tsv`.  A page read that says neither withheld nor `no converter
    for` also goes by the report's OCR probe (`read_image_pages` leaves it out of `image_pages`), while the
    TSV keeps the row's `no converter for` reason, so with OCR ready the two disagree there too."""
    from agentsync import policy, publish, setup_report  # noqa: PLC0415

    text = '---\nreason: "unsupported image layout"\nsummary: "an image"\n---\n\n# a.png\n\nthe page body\n'
    assert publish._withheld_on_page(text) is False  # the TSV keeps the row's reason
    read = publish._frontmatter_lines(text, ("reason",))
    shown = read[0]["reason"] if read is not None else None
    assert shown == "unsupported image layout"
    assert not policy.page_says_withheld(shown, "an image", "the page body")
    assert not shown.casefold().startswith(setup_report._NO_CONVERTER)  # so read_image_pages counts it unread
    mirror = setup_report._Mirror.__new__(setup_report._Mirror)
    mirror.labels_active, mirror.withheld, mirror.images_by_probe = True, {".msg"}, True
    mirror.image_pages = {}
    assert ".png" in mirror.withheld_for(f"{setup_report._NO_CONVERTER}.png", "s", "neither")  # the probe's
    page = " ".join(_page("docs/deploy/setup-feedback.md").split())
    assert (
        "Only a stub whose page could not be read, or that was read but says neither withheld nor "
        "`no converter for` (the legend below counts both as 'could not be read'), can disagree with the TSV"
        in page
    )
    s91 = " ".join(_contracts_section("### 16.91 ", "### 16.92 ").split())
    assert (
        "Amended 2026-10-15, §16.97 M79: so can a stub whose page was read but says neither withheld" in s91
    )


SYNC_WAITS = (
    "A sync can take many minutes. While it works it may print a line saying it is waiting: for the sync"
    ' lock, up to 10 minutes (a line starting "another sync is running", "another sync is still running" or'
    ' "the lock is held by"), or for new inbox files to settle, up to a minute; then it goes on by itself.'
    ' Those waits are expected, not deviations: do not log them. If a sync then ends "skipped: lock held"'
    " with no NEXT: line, run the same sync once more before going to the report; if that one is skipped"
    " too, log it as a step 3 error and go to the report. Otherwise a sync prints nothing until its report;"
    " if it seems stuck, a macOS prompt may be waiting for me, so tell me and keep waiting."
)
"""Step 3's words, since v27, for what a sync prints while it works (B13 round 18 V24, V39, with lane B's V40
openings): v26 said a sync prints nothing, and called no wait expected, so an agent following the friction-log
rule logged a foreground sync's lock wait and its inbox settle wait as deviations; and a sync that waited out
the lock ends "skipped: lock held" with no NEXT: line, which sent the loop to the report early."""


def test_step_3_calls_a_syncs_waits_expected_and_retries_a_lock_skip() -> None:
    """The step 3 sentence on a sync's waits (:data:`SYNC_WAITS`), and the code that prints what it names: the
    lock wait's openings (cycle._acquire_waiting; "the lock is held by" is lane B's V40 line for an empty lock
    body), the inbox settle wait (arm_local) and the lock skip (cli). v26's "prints nothing while it works" is
    gone."""
    step3 = _prompt_steps()[3]
    assert SYNC_WAITS in step3, step3
    assert "prints nothing while it works" not in step3
    src = ROOT / "src" / "agentsync"
    cycle = (src / "cycle.py").read_text(encoding="utf-8")
    assert '"another sync is running (' in cycle and '"another sync is still running (' in cycle
    assert "still settling; waiting " in (src / "arm_local.py").read_text(encoding="utf-8")
    assert 'f"skipped: lock held by ' in (src / "cli.py").read_text(encoding="utf-8")


def test_the_guide_names_step_3s_four_cases_with_no_report() -> None:
    """B13 round 11 M72: since v19 step 3 lists four cases with no report, the fourth being step 1's
    not-a-checkout and ownership stops, which run no installer, and its Xcode and git stops when the folder's
    installer is not the prompt's version; the guide still said every failure goes to its report, "Three stops
    have no report", "step 3's three cases" and that the Xcode and git stops always log their error."""
    report = " ".join(_prompt_steps()[_report_step()].split())
    assert 'Four cases have no report and no "## Not used" section' in report
    # v20 split the worktree stop out of the not-a-checkout one (round 11 M17, M69)
    assert "at step 1's not-a-checkout, worktree and ownership stops, and at its Xcode and" in report
    assert "Those three stops end there, with no report" in " ".join(_prompt_steps()[1].split())
    page = " ".join(_page("docs/deploy/setup-feedback.md").split())
    assert "one: every failure goes to its report" not in page
    assert "one: every failure but the four below goes to its report" in page
    assert "Four stops have no report (three before v19): a new Mac" in page
    assert (
        "since v19, step 1's not-a-checkout stop (git clone says the folder \"already exists and is not an "
        'empty directory", git says "not a git repository" or "invalid gitfile format", or main is used by '
        'another worktree) and its ownership stop ("detected dubious ownership"), which run no install.sh '
        "command at all" in page
    )
    assert "`install.sh --version` does not end with the prompt's own `setup-prompt-compat N`" in page
    assert "Since v19 step 3 lists four such cases, and none of them has a `## Not used` section." in page
    assert 'Since v20 the worktree stop (a recovery stops because main "is already used by worktree"' in page
    assert "cases with no report; since v19, in any of step 3's four)" in page
    assert (
        "the Xcode and git stops log that error before the report; since v19 only when the folder's "
        "installer is the prompt's version, and otherwise they log nothing and write no report)" in page
    )


def test_the_guides_unread_config_paragraph_says_what_v19_changed() -> None:
    """B13 round 11 M76: the guide's paragraph on a config whose folders could not be read stopped at v18. v19
    follows a re-run that read the config as any list (L62), makes no keep claim after `marks=failed` (L11),
    and after step 2's "no folder is synced yet" runs no report, finishes with `none` and runs step 2 again
    with `--source-local` (L10)."""
    step1 = " ".join(_prompt_steps()[1].split())
    assert (
        "line no longer says the config's folders could not be read, its config was read, so follow" in step1
    )
    assert "show me that line in its own words and do not say step 2 keeps my folders" in step1
    assert 'Run no --report-only and add no "## Not used" section.' in step1
    assert "run step 2 again with one --source-local per folder I chose" in step1
    page = " ".join(_page("docs/deploy/setup-feedback.md").split())
    v19 = page.partition("Since v19 (B13 round 10 L62, L11, L10): ")[2].partition(" The sign-in `NEXT:`s")[0]
    assert "re-run's `NEXT:` no longer says the config could not be read, step 1 follows that list" in v19
    assert "(`marks=failed`), step 1 shows that line in its own words and does not say step 2 keeps" in v19
    assert 'runs no `--report-only` and adds no "## Not used" section' in v19
    assert "`none: waiting for your folder answer` and `none` for bring-back.md" in v19
    assert "step 2 again with one `--source-local` per folder, not to the add-source its `NEXT:` names" in v19


def test_k52s_withheld_constants_are_named_where_they_live() -> None:
    """B13 round 10 L55: §16.84 K52 said J47's `_WITHHELD_BODY` and `_WITHHELD_WORDS` sit below
    `_STUB_RENDER_META`'s docstring in agentsync.cycle; round 9's K48 moved them to agentsync.policy, where
    `page_says_withheld` reads them, and a later commit made them private."""
    from agentsync import cycle, policy  # noqa: PLC0415

    for name in ("_WITHHELD_BODY", "_WITHHELD_WORDS"):
        assert hasattr(policy, name) and not hasattr(cycle, name), name
    k52 = " ".join(_contracts_section("**K52: `_STUB_RENDER_META` keeps its own docstring", "\n\n").split())
    assert (
        "Amended 2026-10-14, §16.91 L55 (by §16.80 K48): the two constants are no longer in `agentsync.cycle`"
        in k52
    )
    assert "`policy._WITHHELD_BODY` and `policy._WITHHELD_WORDS`" in k52


def test_the_guide_names_the_late_listing_and_the_local_work_line() -> None:
    """B13 round 10 L64: round 9's K76 uses a CloudStorage listing that finished a little late, registers its
    folders after every other value and says so on the Environment line; K30 adds a Run metadata line that
    counts the local work step 1 kept. The guide had neither, and its `<folder-N>` row named only configured
    folders, while both reports register step 1's listed folders and the agentsync report its listing's (since
    round 8 J69)."""
    from agentsync import setup_report  # noqa: PLC0415

    source = inspect.getsource(setup_report)
    page = " ".join(_page("docs/deploy/setup-feedback.md").split())
    bound = f"{setup_report._LISTING_S:.1f}"
    late = f"- redaction: the CloudStorage listing finished after {bound}s and was used for "
    assert late.replace(f"{setup_report._LISTING_S:.1f}", "{_LISTING_S:.1f}") in source
    assert f"`{late}redaction`" in page
    assert (
        setup_report._LATE_LISTING_S == 0.5
        and "(it waits at most half a second more there) is still used" in page
    )
    assert "and last a listing that finished after its 3-second bound but in time for the report" in page
    work = inspect.getsource(setup_report._local_work_line)
    # B13 round 10 L8, L25, L40, L58: the line reads by the patch count, and its bracket is LOCAL_WORK_WHERE
    for words in (
        "- local work kept by setup prompt step 1: ",
        "'patch file')} in the setup folder's local-work",  # B13 round 15 S31: the noun by the count
        "({LOCAL_WORK_WHERE}); ",
        '"local-work branch", "local-work branches"',
        "- local-work branches in the checkout: ",  # B13 round 17 U38: a known count takes no "(es)"
        'own = "its own" if len(branches) == 1 else "their own"',
    ):
        assert words in work, words
    assert (
        "`- local work kept by setup prompt step 1: N patch files in the setup folder's local-work, newest "
        f"written <time> ({setup_report.LOCAL_WORK_WHERE}); M local-work branches in the checkout`" in page
    )
    assert (  # B13 round 13 O23
        "`- local-work branches in the checkout: M with commits of their own, no patch file in the setup "
        "folder's local-work (moved, deleted or never written; those commits are not in bring-back.md, whose "
        "section 3 says how to send them)`" in page  # B13 round 16 T82
    )
    assert "which is no kept work" not in page
    # B13 round 14 Q30: section 3's own line carries the remedy now, with the keep's --suffix; round 15 S25
    # with its -c format.filenameMaxLength=128 too, and S23 names the report command
    assert (
        "git -C ~/src/agent-context-sync -c format.filenameMaxLength=128 format-patch -q "
        '--suffix=".<branch>.patch" origin/main..<branch> -o ~/agent-context/setup/local-work' in page
    )
    assert f"then run {setup_report.LOCAL_WORK_AGAIN} again" in page and "run this command again`" not in page
    # B13 round 15 S21: the line for a patch an earlier attempt's report embedded, by local-work/.sent
    assert "`not repeated: a patch file an earlier attempt's report held (<time>)`" in page
    # B13 round 18 V25: section 3's third form, a patch this account cannot read, which the guide left out
    assert (
        "`not sent: 1 patch file in ~/agent-context/setup/local-work this account cannot read; make it "
        f"readable (chmod u+r) and run {setup_report.LOCAL_WORK_AGAIN} again`" in page
    )
    # B13 round 14 Q29: the clause for patches this account cannot read
    assert (
        "`, U of them unreadable and not in bring-back.md (chmod u+r, then run "
        f"{setup_report.LOCAL_WORK_AGAIN} again)`" in page
    )
    # B13 round 14 Q65: every keep commit has one subject, so a format-patch without the keep's --suffix
    # wrote each branch's 0001-local-changes-kept-before-update.patch over the one before
    assert "format-patch origin/main..<branch> -o" not in page
    assert "Documents" in setup_report._GENERIC_FOLDERS
    assert (
        "each folder the agentsync report's CloudStorage listing showed, and each folder step 1's "
        "`--list-folders` runs printed into install.out (both reports), except a generic name such as "
        "`Documents`" in page
    )
    assert (
        "in the agentsync report, a `OneDrive-SharedLibraries-` entry at depth 2, which is a `<library-N>`"
        in page
    )
    assert 'shared = provider.startswith("OneDrive-SharedLibraries-")' in inspect.getsource(
        setup_report._add_listed
    )


def test_the_keep_commands_name_the_patch_from_the_local_work_branch() -> None:
    """B13 round 10 L65: §16.82 K28 said both keep commands run `format-patch` "right after" `switch -c`;
    `add -A` and a commit made only when something is staged come between, still on the new branch."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for command in (KEEP_LOCAL_WORK, KEEP_BRANCH_WORK):
        assert f"`{command}" in readme
        steps = [
            command.index(w)
            for w in ("switch -c local-work-", " add -A", "diff --cached --quiet ||", "format-patch")
        ]
        assert steps == sorted(steps)
        assert "switch" not in command[steps[0] + 6 : steps[3]], "a branch change before format-patch"
    k28 = " ".join(_contracts_section("**Each kept patch carries its keep's branch** (K28", "\n\n").split())
    assert 'Amended 2026-10-14, §16.91 L65: not "right after" `switch -c local-work-<time>`.' in k28
    assert "after `add -A` and the commit (made only when something is staged)" in k28


def test_the_repeat_conversions_row_quotes_the_columns_and_the_re_screen_note() -> None:
    """B13 round 9 K68: since round 8 J30 the Repeat conversions table counts the same file converted again
    from the same bytes and converter options, and a note under it says a label-rule re-screen is a new
    conversion while a secret-rule one is counted; the guide's row said "the bytes" alone and had no word of
    the note.  The row quotes the report's own header and note words."""
    from agentsync import setup_report  # noqa: PLC0415

    source = inspect.getsource(setup_report)
    page = _page("docs/deploy/setup-feedback.md")
    row = page[page.index("| Repeat conversions |") : page.index("| Background runs: installed plist |")]
    headers = re.findall(
        r'"(the same file from the same bytes[^"]*|of those, as the run just before)"', source
    )
    assert len(headers) == 2, headers
    for header in headers:
        assert f"`{header}`" in row, header
    assert "and converter options as an earlier run" in row
    note = re.search(
        r'f"- (re-screened for a rule change): \{each\}\. (A label rule is one of the converter)', source
    )
    assert note is not None
    assert f"`{note.group(1)}:`" in row and f"{note.group(2)}'s options" in row
    assert "a file re-screened after a secret-rule change is counted" in row
    assert "counts files converted again from the bytes their own page was made from, right after" not in row


def test_the_guide_names_step_1s_folder_list_as_a_redaction_source() -> None:
    """B13 round 9 K69: since round 8 J69 both reports register the folders step 1's `--list-folders` runs
    printed into install.out (the agentsync report after its CloudStorage listing, the shell report after
    install.log), and a listing that did not finish with no such list makes the agentsync report Private
    only; the guide gave one Private-only cause and neither registration order had the list."""
    from agentsync import setup_report  # noqa: PLC0415

    source = inspect.getsource(setup_report)
    line = re.search(
        r'"(Private only: the listing of ~/Library/CloudStorage for redaction did not finish) and "', source
    )
    assert line is not None and "red.listing_unread = not listing_ok and not has_list" in source
    assert "_add_listed(red, [*listed, *printed])" in source  # after the listing, before the rest
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert installer.index("printed into install.out") > installer.index('"$SETUP_LOG" 2>/dev/null |')
    page = _page("docs/deploy/setup-feedback.md")
    assert f"(`{line.group(1)} ...`)" in page
    assert "The shell fallback report never lists CloudStorage, so it never prints this line." in page
    assert (
        "then the CloudStorage listing, then the folders step 1's `--list-folders` runs printed into "
        "install.out, then configured folders elsewhere" in page
    )
    assert "then sources.toml, then install.log, then install.out's step 1 list)" in page


def test_the_first_sync_row_names_every_clause_the_summary_adds() -> None:
    """B13 round 9 K82: the Summary's first-sync clause can add a first-read count, the unreadable/failed
    split, the withheld-unread count and the refused-without-a-read count after its converted counts, each
    from install.out's line of the same sync (`_first_sync_line`), and name them in its source tag; the
    guide's row said only "what it converted and read again".  Each part's fixed words are read from the
    code that writes it."""
    from agentsync import setup_report  # noqa: PLC0415

    # one literal per part: the adjacent string pieces a long one is written in are joined
    # B13 round 23 A19: the clauses are written by _sync_line_end, which _first_sync_line ends in
    lines = inspect.getsource(setup_report._first_sync_line) + inspect.getsource(setup_report._sync_line_end)
    code = re.sub(r'"\s*\n\s*f?"', "", lines)
    page = _page("docs/deploy/setup-feedback.md")
    row = page[page.index("| Summary: `first sync:` or `sync:` |") : page.index("| Installer: every run |")]
    first = re.search(r'reread \+= \(?\s*f": \{first\[2\]\} (of them [^"]*)"', code)
    split = re.search(
        r'reread \+= f"; (of the converted): \{split\[1\]\} (unreadable), \{split\[2\]\} (failed)', code
    )
    counted = re.findall(r'\(r\.facts\.first_sync_\w+, "([^"]+)", "([^"]+)"\)', code)
    assert first and split and len(counted) == 2, "the clause code moved"
    assert f"`: F of them {first.group(1)[len('of them ') :]}`" in row
    assert f"`; {split.group(1)}: U {split.group(2)}, F {split.group(3)}`" in row
    for words, _tag in counted:
        assert re.search(rf"`; [A-Z] {re.escape(words)}`", row), words
    assert '"the unreadable/failed split"' in code and 'f"the {tag} count"' in code
    tag = dict(counted)["refused without a read"]
    assert f"(install.log; the unreadable/failed split and the {tag} count: install.out)" in row
    assert "copied from the sync's own line in install.out when that line describes the same sync" in row


def test_h31s_stderr_line_quotes_the_cli() -> None:
    """B13 round 9 K53: round 8's J11 made the CLI's lock-busy line `skipped: lock held by <holder>`, while
    §16.68 H31, whose test J11 changed, still quoted `skipped: lock held (...)` with no dated line."""
    cli = (ROOT / "src" / "agentsync" / "cli.py").read_text(encoding="utf-8")
    # B13 round 19 W14: the holder is exc.holder, but an empty body's in the wait line's words
    assert '_err(f"skipped: lock held by {_skip_holder(exc)}")' in cli
    h31 = " ".join(
        _contracts_section("**H31: install.sh's first sync waits for the lock quietly", "\n\n").split()
    )
    assert "Amended 2026-10-14, §16.85 K53 (by §16.77 J11): the stderr line H31 leans on" in h31
    assert "reads `skipped: lock held by <full holder>` (from `LockHeldError.holder`)" in h31


def test_no_line_of_the_prompt_block_is_a_lone_word_or_wider_than_the_block() -> None:
    """B13 round 8 J68: v16's re-wrap left a line holding only "If", and v15 had left a 132-column line, in a
    block a person reads in a ```text fence, which keeps its line breaks. A command line is as long as its
    command."""
    lines = _one_prompt_block().splitlines()
    assert [ln for ln in lines if len(ln.split()) == 1 and "`" not in ln] == []
    assert [ln for ln in lines if "`" not in ln and len(ln) > 120] == []


def test_the_deploy_guide_says_git_and_the_installer_do_not_read_the_network_proxy() -> None:
    """B13 round 8 J24: the guide the prompt points at said only "set `[network] proxy`", agentsync's own
    setting for its Graph traffic, which git, the uv download and `uv tool install` never read. It now says so
    and names HTTPS_PROXY for them, without git's own http.proxy, which step 1 diagnoses as a stale cause."""
    deploy = " ".join((DEPLOY / "README.md").read_text(encoding="utf-8").split())
    assert (
        "`[network] proxy` is agentsync's own setting in `sources.toml` and covers only its Graph traffic"
    ) in deploy
    assert (
        "git, the uv download and `uv tool install` do not read `[network] proxy`: behind a proxy, export "
        "`HTTPS_PROXY` (an `http://` proxy URL; PAC files are not evaluated) in the terminal before the "
        "clone, "
        "the pull or `install.sh`."
    ) in deploy
    assert "http.proxy" not in deploy


def test_the_readme_says_what_a_recording_page_reads_of_speech_and_which_decks_get_ocr() -> None:
    """B13 round 8 J56: the README said "No speech is read yet" while a recording page writes SPEAKING lines
    from the lit name label, and SAID lines, speech NOTE lines and a Voices block once the speech models are
    placed. J57: its OCR list named only .pptx, while a .potx template's slides are read the same way (its
    master and layouts are not)."""
    from agentsync.convert import pptx, recording_page  # noqa: PLC0415

    readme = " ".join(_page("README.md").split())
    assert "No speech is read yet" not in readme
    assert (  # B13 round 9 K63: SCREEN+ and SCREEN- too
        "(`SCREEN:`, `SCREEN+:` and `SCREEN-:` for a row on screen, added or removed, `TILE:`, `SPEAKING:`, "
        "`SAID v<N>:`, `KEYFRAME:`, `NOTE:`)"
    ) in readme
    assert (
        "A `SPEAKING:` line is the name label the meeting app lit on screen. It is read from the picture, "
        "needs "
        "no speech models, and follows sound, not identity."
    ) in readme
    assert "The index adds a Voices block with one `VOICE:` line per voice" in readme
    assert "The page does not say who spoke beyond the VOICE lines" in readme
    source = Path(recording_page.__file__).read_text(encoding="utf-8")
    assert (
        'f"SAID v{line.voice}"' in source
        and '"## Voices"' in source
        and "Not on this page: who spoke beyond the VOICE lines" in source
    )
    assert (
        "**Pictures on a deck's slides** (`.pptx`, or a `.potx` template that has slides; the pictures in a "
        "template's master and layouts are not read)"
    ) in readme
    assert ".potx" in pptx.PptxConverter.extensions


def test_the_deploy_guides_say_a_set_up_mac_with_a_missing_folder_exits_3() -> None:
    """B13 round 12 N62: since round 11 M4, `--list-folders` on a set-up Mac whose synced folder under
    ~/Library/CloudStorage is missing exits 3 with a NEXT that asks for the sign-in, and v20's step 1 waits
    for it. docs/deploy/README.md still said such a Mac "gets that line and exit 0 even when nothing else can
    be listed", and that the list exits 3 only "when there are none"."""
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert 'step_end failed "$rc" "not-signed-in apps=$tokens missing=$missing synced=$synced"' in installer
    deploy = _page("docs/deploy/README.md")
    assert "Such a Mac gets that line and exit 0 even when nothing else can be listed" not in deploy
    assert (
        "it exits 3 when there are none, or when a folder this Mac already syncs under "
        "`~/Library/CloudStorage` is missing because its app is signed out, and 4 when macOS denied this "
        "terminal access"
    ) in deploy
    assert (
        "When no synced folder under `~/Library/CloudStorage` is missing, such a Mac gets that line and exit "
        "0 even when nothing else can be listed"
    ) in deploy
    assert (
        "When a synced folder under `~/Library/CloudStorage` is missing and its app is signed out (its "
        "provider folder, such as `OneDrive-Contoso`, is gone with no other folder of that app beside it, or "
        "is there and empty), it gets the line, the `[synced]` lines and exit 3, logged `note=not-signed-in "
        "apps=<app> missing=M synced=N`."
    ) in deploy
    # B13 round 12 N41, N61: a folder gone while its app is signed in keeps exit 0 (B13 round 16 T72: or not
    # synced on this Mac, the wait's cause and doctor's fix)
    assert (
        "(renamed, moved or deleted there, not synced on this Mac, or under another account's provider "
        "folder) keeps exit 0" in deploy
    )
    assert "Since v20, step 1 of the one-prompt setup waits for that sign-in" in deploy
    assert "even when the list ends on a click (exit 4), which comes before the missing check." in deploy
    feedback = _page("docs/deploy/setup-feedback.md")
    assert (
        "- A set-up Mac whose synced folder under `~/Library/CloudStorage` is missing because its app is "
        "signed out, or still setting up (its provider folder gone with no other folder of that app beside "
        "it, or there and empty), gets the line, the `[synced]` lines and exit 3, logged `note=not-signed-in "
        "apps=<app> missing=M synced=N` (B13 round 11 M4, narrowed by round 12 N41, N61; the app and the "
        "missing count since round 13 O5)."
    ) in feedback
    assert (
        "(renamed, moved or deleted there, or not synced on this Mac) keeps exit 0, logged `gone=N`"
        in feedback
    )
    assert "Since v20, step 1 waits for that sign-in as for a click, then lists the folders again" in feedback
    # B13 round 13 O62, O71 (one defect with O12): since v21 an agent that cannot ask goes to the report
    assert (
        "Since v21, an agent that cannot ask (an unattended tool, or a question that comes back unanswered) "
        "does not wait there and logs nothing for it: it goes to step 3's report."
    ) in deploy
    assert (
        "Since v21 (B13 round 12 N32, N6), an agent that cannot ask does not wait and logs nothing for that "
        "sign-in: it goes to step 3's report, whose expected-turns line is the set-up Mac sign-in row below "
        "(K45)."
    ) in feedback
    # B13 round 13 O65: README's Install section gave round 10's exit 3 rule, with no set-up Mac
    readme = _page("README.md")
    assert "it exits 3 when OneDrive is not signed in or syncs no folder yet" not in readme
    # B13 round 14 Q74: a config the list could not read gets a new Mac's exits (synced is empty, so the
    # signed-out check never runs) and a NEXT that says so; the Install section said a set-up Mac exits 0
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert (
        'NEXT_MSG="OneDrive is not signed in on this Mac: sign in to OneDrive, then re-run: $RERUN$kept"'
        in installer
    )
    assert "On a Mac that already syncs folders it exits 3" not in readme
    assert (
        "On a Mac that syncs no folder yet, or whose config the list could not read (its `NEXT:` line then "
        "says so), it exits 3 when it finds none (OneDrive not signed in, or nothing synced yet). On a Mac "
        "whose config shows it already syncs folders it exits 3 when one of them under "
        "`~/Library/CloudStorage` is missing because its app is signed out, even if other folders are "
        "listed, and otherwise exits 0, even when nothing is listed. It exits 4, before any of these checks, "
        "when macOS denied this terminal access or is still asking"
    ) in readme


def test_the_guide_moves_a_late_step_1_run_at_any_gap() -> None:
    """B13 round 13 O60: round 12 N23 moved the unbroken run of step 1 lines dated at or after the end line
    to the attempt a step 1 error opens, whatever the gap (``late_run``'s ``at >= end``); the guide's second
    sentence on it still said "each more than 10 minutes after the earlier attempt's end line"."""
    from agentsync import setup_report  # noqa: PLC0415

    source = Path(setup_report.__file__).read_text(encoding="utf-8")
    late_run = source[source.index("    def late_run()") : source.index("        return moved\n")]
    assert "at >= end" in late_run and "_NEW_SESSION_GAP" not in late_run
    page = _page("docs/deploy/setup-feedback.md")
    assert "each more than 10 minutes after the earlier attempt's end line" not in page
    assert (
        "When a step 1 error opens such an attempt, the unbroken run of step 1 lines just before it, dated "
        "at or after the earlier attempt's end line, is that session's and moves with it, whatever the gap "
        "(since round 12, N23: the finished session runs nothing after its report); since round 13 (O24)"
    ) in page
    assert (
        "The unbroken run of step 1 lines just before it, at or after the closing line, moves with it" in page
    )


def test_the_guide_moves_a_late_step_1_run_to_the_next_attempt_line() -> None:
    """B13 round 14 Q64, Q77 (one defect): since round 13 O24 an `Attempt:` line takes the finished attempt's
    trailing step 1 lines too, in parse_friction and in install.sh's ``friction_latest_has_step``, but the
    guide still said a late line no step 1 error follows stays where it is, and that any other late line
    stays in its attempt. The guide calls a `Prompt:`, `Agent:`, `Outcome:` or `Run:` line a header, so it
    names the `Attempt:` line."""
    from agentsync import setup_report  # noqa: PLC0415

    source = Path(setup_report.__file__).read_text(encoding="utf-8")
    branch = source[source.index("        attempt = _ATTEMPT_RE.match(line)") :]
    assert "moved = late_run()" in branch[: branch.index("            continue\n")]
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    has_step = installer[installer.index("friction_latest_has_step() {") :]
    assert "has = carried" in has_step[: has_step.index("\n}\n")]
    page = _page("docs/deploy/setup-feedback.md")
    assert "a late line no such error follows stays where it is" not in page
    assert (
        "since round 13 (O24) the same run moves to the next `Attempt:` line too, and that attempt still "
        "begins at its `Attempt:` time (two `Attempt:` lines with no end line between them leave it where it "
        "was logged; install.sh's friction log check reads the same boundary); a late line that neither such "
        "an error nor an `Attempt:` line follows stays where it is."
    ) in page
    assert (
        "Any other late line (one of another step, or a step 1 line that such a line separates from the next "
        "session's error or `Attempt:` line) stays in its attempt"
    ) in page


def test_the_guide_says_a_config_step_2_cannot_reinstall_for_adds_no_folder() -> None:
    """B13 round 13 O61: since round 12 N29 a list whose installed agentsync is this checkout's build and
    failed on the config ends on `no folder can be added until the config is fixed`, not the folder question,
    and v21 asks nothing about folders there; the guide still called it the folder question's NEXT and said
    step 1 asks whether to add one."""
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert (
        'NEXT_MSG="no folder can be added until the config is fixed: run $SELF with no --source-local, which '
        'shows the error$kept"'
    ) in installer
    page = _page("docs/deploy/setup-feedback.md")
    assert (
        "In that case the list does not end on the folder question: its `NEXT:` opens `no folder can be "
        "added until the config is fixed: run <install.sh> with no --source-local, which shows the error`, "
        "followed by the same clause, and the list logs done (B13 round 12 N29)."
    ) in page
    assert (
        "and asks only whether to add one (since v21, nothing when step 2 does not reinstall agentsync)"
    ) in page
    assert (
        "Since v21, when that line says step 2 does not reinstall agentsync, step 1 asks nothing about "
        "folders and runs step 2 with no `--source-local`."
    ) in page


def test_the_guides_summary_table_has_the_unread_config_line_that_asks_nothing() -> None:
    """B13 round 14 Q69: since round 13 O8 a v21 or later attempt whose list logged `marks=failed` and whose
    first install run skipped agentsync as current reads `step 1 asks nothing` (``_unread_stuck``), but the
    Summary table had no row for that line and gave "asks at most whether to add one" to every v18 or later
    attempt."""
    from agentsync import setup_report  # noqa: PLC0415

    source = Path(setup_report.__file__).read_text(encoding="utf-8")
    stuck = source[source.index("        elif unasked and stuck:") : source.index("        elif unasked:\n")]
    assert "agentsync is already this checkout's build, so step {layout.install_step} does not " in stuck
    assert setup_report.UNREAD_CURRENT_ASKS_NOTHING == 21
    page = _page("docs/deploy/setup-feedback.md")
    assert "| Prompt v18 or later, when the install could not say either" not in page
    assert (
        "| Prompt v18 to v20, or v21 or later unless step 1's list logged `marks=failed` and the first "
        "install run's agentsync step logged `skipped note=current`"
    ) in page
    assert (
        "| `expected turns: no folder question (step 1's list could not read the config, and ..., so step 2 "
        "does not reinstall it: step 1 asks nothing, since no folder can be added until the config is fixed; "
        "not logged) · ...` | Prompt v21 or later"
    ) in page
    assert "as in the row above, the install could not say either (its config step logged no `kept=`)" in page
    # the clause the row leaves out names agentsync, which the CLI-name scan would read as a command
    assert "the left-out clause says the installed agentsync is already this checkout's build" in page


def test_the_guides_sync_row_names_the_newest_earlier_runs_sync() -> None:
    """B13 round 14 Q70: since round 13 O32 a one-attempt report whose install.out counts an earlier run
    printed adds the result of a newer earlier run's first-sync step and leaves those counts out
    (``_newer_sync``), but the guide's sync row said without condition that an earlier run's counts read
    `(install.out: the install run of <time>, before this attempt; ...)`."""
    from agentsync import setup_report  # noqa: PLC0415

    source = Path(setup_report.__file__).read_text(encoding="utf-8")
    newest = source[source.index("            newest = _newer_sync(") :]
    newest = newest[: newest.index("        else:\n")]
    assert 'f"the newest earlier run\'s sync: {said} (install.log: "' in newest
    assert "own_line = False" in newest
    page = _page("docs/deploy/setup-feedback.md")
    assert (
        "an earlier run's read `(install.out: the install run of <time>, before this attempt; ...)`, unless "
        "a newer earlier run, one that began after the run that printed those counts, logged a first-sync "
        "step (a sync skipped on a busy lock, say): then the line adds `· the newest earlier run's sync: "
        "<result> (install.log: the install run of <time>, before this attempt)`"
    ) in page
    assert "and leaves install.out's older counts out, since they are not the Mac's latest sync" in page
    assert (
        "and those of an install.out with no header `(install.out: which run printed it is not recorded"
        in page
    )


def test_section_5_counts_the_sign_in_step_1_waits_for() -> None:
    """B13 round 13 O64: section 5 listed the OneDrive sign-in only as a precondition at which "the prompt
    stops at step 1", though since v20 step 1 waits for it on a set-up Mac and the report counts it among the
    expected turns (round 12 N5); its unavoidable steps did not name it."""
    page = _page("docs/deploy/setup-feedback.md")
    unavoidable = page[page.index("Current unavoidable human steps") : page.index("Moved out of this list:")]
    assert (
        "- **The OneDrive sign-in step 1 waits for** (or the sign-in to the app the list names), on a Mac "
        "that already syncs folders whose app is signed out or still setting up."
    ) in unavoidable
    assert "An agent that cannot ask does not wait: it goes to the report." in unavoidable
    # the report counts that turn from v20 on, the prompt's logging rule is v21's
    from agentsync import setup_report  # noqa: PLC0415

    assert setup_report.SIGN_IN_WAITS == 20
    assert (
        "Since v21 the prompt logs it as neither a click nor a question; the report counts it among the "
        "expected turns for any v20 or later attempt"
    ) in unavoidable
    preconditions = page[page.index("Preconditions, outside the setup's count") :]
    assert (
        "- **Signing in to OneDrive** before setup. The OneDrive app owns that sign-in." not in preconditions
    )
    assert (
        "- **Signing in to OneDrive** before setup, on a Mac that syncs no folder yet. The OneDrive app owns "
        "that sign-in."
    ) in preconditions
    # B13 round 14 Q72: "reads as worked" names no outcome; its nearest, "worked with help", is what K45 fixed
    # The report's outcome is test_setup_report.py::test_the_sign_in_step_1_asked_for_is_no_extra_click's.
    assert "so a clean run still reads as worked" not in unavoidable
    assert 'so a clean run still reads "fully one command".' in unavoidable


def test_the_guide_names_every_source_of_a_proxy_number() -> None:
    """B13 round 12 N47: since round 11 M29 both reports also number a host a friction line's resolver error
    names and the hosts `--log-start` and `--log` keep in `proxy-hosts`; the guide's `<proxy-N>` row named
    only config, environment and LaunchAgent proxies, and its section 1 did not name the file. Both legends
    are pinned in
    `test_install_oneshot.py::test_both_legends_say_a_resolver_errors_host_need_not_be_a_proxy`."""
    from agentsync import setup_report  # noqa: PLC0415

    assert setup_report._RESOLVER_HOST_RE.pattern.startswith(r"Could not resolve (?:proxy|host): ")
    assert "Failed to connect to" in setup_report._RESOLVER_HOST_RE.pattern
    assert {"github.com", "astral.sh", "pypi.org"} <= setup_report._PUBLIC_HOSTS
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert 'printf \'%s\' "$out" >>"$d/proxy-hosts"' in installer
    page = _page("docs/deploy/setup-feedback.md")
    assert (
        "any host other than the installer's public hosts (github.com, astral.sh, pypi.org and their "
        "API and download hosts) that a `Could not resolve proxy:`, `Could not resolve host:` or `Failed to "
        "connect to <host> port N` message in friction.md names, which need not be a proxy, and the hosts "
        "`--log-start` and `--log` kept in `~/agent-context/setup/proxy-hosts`, so a proxy unset since an "
        "earlier line stays redacted"
    ) in page
    assert (
        "`--log-start` and `--log` also keep `proxy-hosts` beside friction.md (0600, one host a line, in "
        "lower case and, where it differs, as written: the proxy, git proxy and URL rewrite hosts, and the "
        "host of each git remote's URL (since 2026-10-18), in force at that call). It is never embedded; "
        "both reports use it only to redact."
    ) in page
    assert "Three sources feed one file" in page


def test_the_guide_quotes_round_11s_installer_bracket_and_list_rule() -> None:
    """B13 round 12 N66: since round 11 M44 the Summary's `installer output:` bracket reads `(this attempt's
    runs only; the Installer section also counts the N earlier run(s) in the lines read)`; the guide quoted
    the old `this attempt's runs only: ...`. N67: the `expected turns: none (step 1's list stopped ...)` row
    and §16.86's M7 line left out M7's own condition, a Mac that syncs no folder: the report then read a
    stopped list only `if not synced`. B13 round 13 O63: round 12 N31 has since removed that guard, so M4's
    sign-in exit (`synced=N`) has the next row's line, not the already-synced one, and an exit 4 that logged
    `synced=N` keeps the usual rules."""
    from agentsync import setup_report  # noqa: PLC0415

    source = Path(setup_report.__file__).read_text(encoding="utf-8")
    assert (
        '(this attempt\'s runs only; the Installer section also counts the "\n        f"{_plural(total - k, '
        "'earlier run')} in the lines read)\""
    ) in source
    # B13 round 12 N31: read on a Mac that already syncs too, whose sign-in stop has a line of its own
    assert "list_stop = _list_stopped(runs)\n" in source
    page = _page("docs/deploy/setup-feedback.md")
    assert "this attempt's runs only: ..." not in page
    assert (
        "and a bracket says so and names the earlier runs the Installer section also counts (`(this "
        "attempt's runs only; the Installer section also counts the N earlier runs in the lines read)`)"
    ) in page
    assert (
        "| On a Mac that syncs no folder (no list logged `synced=N`, N at least 1), the attempt's only runs "
        "are step 1 lists, every one failed"
    ) in page
    assert (
        "A list that finished, or an install run, keeps the usual rules, and so does an exit 4 that logged "
        "`synced=N`. install.sh's sign-in exit on a set-up Mac (§16.93 M4, `not-signed-in apps=<app> "
        "missing=M synced=N`; `not-signed-in synced=N` from an install.sh before B13 round 13 O5) has the "
        "next row's line."
    ) in page
    # B13 round 14 Q71: since round 13 O5 that exit logs the apps and the missing count before synced=N
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert 'not-signed-in apps=$tokens missing=$missing synced=$synced"' in installer
    m7 = " ".join(_contracts_section("Amended 2026-10-16, §16.92 M7:", "\n\n").split())
    assert "that all failed, on a Mac that syncs no folder, counts no folder question" in m7
    assert "A list that logged `synced=N`, N at least 1 (§16.93 M4's sign-in exit included), keeps the" in m7


def test_round_12s_contract_quotes_and_moves_read_as_written() -> None:
    """B13 round 12 N64: §16.93 M4 paraphrased v20's sign-in rule as "unless its NEXT asks me to sign in",
    which reads it backwards; it quotes v20's words now. N68: v20's M16/M32 line in §16.88 L31 sat before
    v19's version-check and no-report sentences, so "now" read as v20's; they are L31's text again, before it.
    N70:
    §16.94 M83's line sat mid-paragraph in §16.86 L58, above the "was not built" sentence it overturns; it
    follows the paragraph's other dated lines, and says when M71's reflog skip catches the old main."""
    m4 = " ".join(
        _contracts_section("**Step 1 asks for the sign-in when synced folders are missing**", "\n\n").split()
    )
    v20 = (
        "(step 1's first exception: \"If the list's NEXT: line asks me to sign in, tell me which folders are "
        "kept and that I must sign in to OneDrive, and wait for me as for a click; once I say so, run "
        "--list-folders again and go by what it prints.\") is lane P's (§16.94)."
    )
    assert v20 in m4 and m4.index(v20) < m4.index("Amended 2026-10-16, §16.103 N64:")
    assert "(step 1's first exception: unless" not in m4
    l31 = " ".join(
        _contracts_section(
            "**Only this prompt's installer logs or reports at a step 1 stop**", "\n\n"
        ).split()
    )
    v19 = (
        "The finish still applies after every one of these stops. Step 1's version check now reads \"the "
        "--version inside step 1's one command\", so the Xcode and git stops' own `--version` is not taken "
        "for it. Step 3's no-report cases are four. Test: `test_readme_step1_is_one_command`, "
        "`test_readme_report_step_after_any_failure`. Amended 2026-10-16, §16.94 M16, M32:"
    )
    assert v19 in l31
    v20_line = l31[
        l31.index("Amended 2026-10-16, §16.94 M16, M32:") : l31.index("Amended 2026-10-16, §16.103 N68:")
    ]
    assert v20_line.endswith("Test: `test_readme_step1_is_one_command`. "), v20_line
    l58 = " ".join(_contracts_section("**The local-work line says only what it can know**", "\n\n").split())
    m83 = "Amended 2026-10-16, §16.94 M83 (moved here by §16.103 N70): L58's prompt half is built in v20"
    assert l58.count("§16.94 M83") == 1
    assert l58.index("L58's prompt half (delete the empty branch) was not built") < l58.index(m83)
    assert l58.index("Amended 2026-10-15, §16.93 M18:") < l58.index(m83)
    assert "M71's reflog skip catches only when origin/main's reflog names it" in l58


def test_the_person_facing_docs_spell_organization() -> None:
    """B13 round 12 N79: round 11 M36 spelled it "organization" in both reports and setup-feedback.md, as
    the prompt does; the IT request draft's `<org>` note, docs/deploy/README.md and it-request.md's `<org>`
    row (which the wheel ships) still said "organisation". The note is pinned in test_it_request.py."""
    pages = [ROOT / "README.md", *sorted(DEPLOY.rglob("*.md"))]
    assert DEPLOY / "it-request.md" in pages
    assert [p.relative_to(ROOT) for p in pages if "organisation" in p.read_text(encoding="utf-8")] == []
    assert "| the organization name, also the app's display name" in (DEPLOY / "it-request.md").read_text(
        encoding="utf-8"
    )


def test_status_next_differs_from_a_syncs_only_on_a_failing_check(
    tmp_path: Path,
    local_source_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """B13 round 14 Q2: step 3's :data:`STATUS_NEXT`, measured. With every check passing, status prints the
    NEXT a sync's own call (``loop.next_lines(config)``) gives, here the baseline's; with one ERROR [FAIL],
    status exits 1 and its NEXT is rule 1's, which names that check, while a sync's call in the same state
    still gives the baseline's. Status's FAIL NEXT is not made a sync's: it would hide the check from the one
    read-only command meant to show it."""
    from agentsync import cli, loop  # noqa: PLC0415
    from agentsync.config import load_config  # noqa: PLC0415
    from agentsync.ops import doctor  # noqa: PLC0415

    cfg = tmp_path / "ctx" / "sources.toml"
    assert cli.main(["add-source", str(local_source_dir), "--config", str(cfg)]) == cli.EXIT_OK
    assert cli.main(["sync", "--config", str(cfg)]) == cli.EXIT_OK
    config = load_config(cfg)
    capsys.readouterr()
    [synced] = [ln for ln in loop.next_lines(config) if ln.startswith("NEXT: ")]
    assert "baseline" in synced, synced
    assert cli.main(["status", "--config", str(cfg)]) == cli.EXIT_OK
    assert capsys.readouterr().out.splitlines()[0] == synced, "no FAIL: the same NEXT as a sync's"
    failing = [doctor.CheckResult("fake.check", False, "broken", doctor.Severity.ERROR, fix="do the thing")]
    monkeypatch.setattr(doctor, "run_checks", lambda *_a, **_k: failing)
    monkeypatch.setattr(cli, "_extra_checks", lambda *_a, **_k: [])
    assert cli.main(["status", "--config", str(cfg)]) == cli.EXIT_FAILED
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith("NEXT: the fake.check check failed: "), first
    assert [ln for ln in loop.next_lines(config) if ln.startswith("NEXT: ")] == [synced], "a sync's goes on"


def test_status_next_names_rule_1s_own_steps_before_a_fail(
    tmp_path: Path,
    local_source_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """B13 round 15 S60: v23's :data:`STATUS_NEXT` said a failing status's NEXT names the [FAIL] line, but
    rule 1 gives its own skill and sign-in steps first (``loop.next_step``: the fixes come after them). With
    a skill copy missing and an ERROR [FAIL], status exits 1, prints the [FAIL] line, and its NEXT is the
    skill step, a sync's NEXT. v24 says so ("unless a missing or out-of-date skill copy or a sign-in comes
    first, as in a sync's NEXT"), keeping "it exits 1", so a non-zero status is not logged as a failed
    command."""
    from agentsync import cli, skill  # noqa: PLC0415
    from agentsync.ops import doctor  # noqa: PLC0415

    cfg = tmp_path / "ctx" / "sources.toml"
    assert cli.main(["add-source", str(local_source_dir), "--config", str(cfg)]) == cli.EXIT_OK
    assert cli.main(["sync", "--config", str(cfg)]) == cli.EXIT_OK
    capsys.readouterr()
    failing = [doctor.CheckResult("fake.check", False, "broken", doctor.Severity.ERROR, fix="do the thing")]
    monkeypatch.setattr(doctor, "run_checks", lambda *_a, **_k: failing)
    monkeypatch.setattr(cli, "_extra_checks", lambda *_a, **_k: [])
    assert cli.main(["status", "--config", str(cfg)]) == cli.EXIT_FAILED
    assert capsys.readouterr().out.splitlines()[0].startswith("NEXT: the fake.check check failed: ")
    skill.skill_paths()[0].unlink()  # a skill copy gone: rule 1's skill step comes before the fixes
    assert cli.main(["status", "--config", str(cfg)]) == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("NEXT: the agentsync-docs skill is missing or out of date: "), out
    assert "[FAIL]" in out and "fake.check" in out, "the [FAIL] line still prints"
    flat = " ".join(_prompt_steps()[3].split())
    assert STATUS_NEXT in flat and "its NEXT: names that [FAIL] line, and" not in flat
    row = next(
        ln
        for ln in (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        if ln.startswith("| **Every session**")
    )
    assert (
        "unless a missing or out-of-date skill copy or a sign-in comes first, as in a sync's `NEXT:`" in row
    )


def test_a_first_sync_refusal_is_no_config_that_does_not_load() -> None:
    """B13 round 15 S55 (S8's prompt half): round 14 Q22 made the first sync's exit 78 a stop the person
    owns, and the sync prints "configuration error: ..." for it, yet that config loads: setup-report says
    ``- loads: yes, but a sync refuses it`` (tests/test_setup_report.py). v23 keyed step 2's inbox skip and
    the finish's does-not-load case on "configuration error", so the agent skipped a real inbox and told the
    person the file did not load. v24 keys the inbox skip on config_failed's NEXT ("does not load"), which
    the exit-78 NEXT does not say, and the does-not-load case on the report's "loads: no:" alone; the refused
    config's folders are listed, with the report's reason (:data:`REFUSED_CONFIG`). The same holds for
    init's and add-source's exit 78 on a config that loads (a bad [governance] value or policy.toml): its
    config_failed NEXT says "loads, but agentsync refuses it", never "does not load" (B13 round 16 T1, T10,
    T29, T45, T67, T81)."""
    script = SCRIPTS[0].read_text(encoding="utf-8")
    code = [ln for ln in script.splitlines() if not ln.lstrip().startswith("#")]
    [refused] = [ln for ln in code if "NEXT_MSG=" in ln and "the first sync refused the config" in ln]
    [unloaded] = [ln for ln in code if "FAIL_NEXT=" in ln and "the config $CONFIG does not load" in ln]
    [loads] = [
        ln for ln in code if "FAIL_NEXT=" in ln and "the config $CONFIG loads, but agentsync refuses" in ln
    ]
    assert "configuration error" in refused and "does not load" not in refused, refused
    assert "configuration error" in loads and "does not load" not in loads, loads
    assert "not a step for the agent, so show them the error and go on to the report" in loads, loads
    step2 = " ".join(_prompt_steps()[2].split())
    assert INBOX_WHEN in step2 and "unless it printed a configuration error" not in step2
    assert "unless its NEXT: line says the config does not load" in step2 and "does not load" in unloaded
    # the person-owned stop clause still routes both exit-78 stops (log, show, report, no re-run)
    assert (
        PERSONS_NEXT in step2
        and "not a step for the agent" in refused
        and "not a step for the agent" in unloaded
    )
    finish = " ".join(_prompt_steps()[3].split("Finish with:", 1)[1].split())
    assert 'step 2 printed a "configuration error"' not in finish and UNLOADED_CONFIG in finish
    assert REFUSED_CONFIG in finish
    from agentsync import setup_report  # noqa: PLC0415

    source = Path(setup_report.__file__).read_text(encoding="utf-8")
    assert '"- loads: yes, but a sync refuses it: ' in source and "- loads: yes, but step 2's sync" in source


def test_readme_step2_gives_the_agent_the_chmod_of_its_own_state() -> None:
    """B13 round 21 Y4, Y24, Y59 (rehearsals A, B and E, probes PM, PMr, 51-55): step 1's tail and prompt
    called the chmod of this account's own state file the agent's once step 2 stops on it, while step 2's
    NEXT was "fix the error above" and step 2 allowed only install.sh or agentsync commands. Step 2 now grants
    that chmod, once, when its NEXT: line or a [FAIL] state_dir line names it, and keeps sudo and chown the
    person's; install.sh's NEXT names it (doctor.state_path_fix's chmod 600 or chmod 700, nothing else), and
    step 1's tail says when (tests/test_install_oneshot.py runs each)."""
    step2 = " ".join(_prompt_steps()[2].split())
    assert step2.count(STATE_CHMOD_IS_YOURS) == 1
    assert f"{FAIL_IS_MINE} {STATE_CHMOD_IS_YOURS} " in step2, "right after the [FAIL] rule it widens"
    assert (
        f"{PERSONS_NEXT} it is an install.sh or agentsync command, a click for me or those chmods (but after"
        in step2
    )
    # B13 round 22 Z59, Z8: install.sh's status step names the chmods of any state check; Z81: a folder's NEXT
    # adds the status the grant's "if the NEXT: line also says to run" reads
    assert "~/.local/bin/agentsync status and each chmod 600 or chmod 700 its [FAIL] lines name" in (
        SCRIPTS[0].read_text(encoding="utf-8")
    )
    assert r"(state_dir(\\.[a-z_]+)?|lock|heartbeat|governance\\.[a-z_]+)" in SCRIPTS[0].read_text(
        encoding="utf-8"
    )
    script = SCRIPTS[0].read_text(encoding="utf-8")
    body = script.split("\nstate_open_fix() {", 1)[1].split("\n}\n", 1)[0]
    assert 'fix.startswith(("chmod 600 ", "chmod 700 "))' in body and "state_path_fix(root" in body
    assert "the fix is the agent's: run $fix once, then re-run: $SELF$RERUN_ARGS" in script
    # B13 round 22 Z81: a folder's chmod 700 has its own arm, which adds status's chmods in the same pass
    assert '\t\t"chmod 700 "*)' in script and '\t\t"chmod 600 "*)' in script
    assert "run $fix once, then, in the same pass, since that folder's mode hid its files' modes" in script
    # B13 round 23 A37: a stop can take more than one chmod (a shut folder and its file, a -wal and a -shm)
    assert "the chmods that fix it are the agent's to run, once each, as step 2's NEXT names them" in script
    from agentsync.ops import doctor  # noqa: PLC0415

    fix = inspect.getsource(doctor.state_path_fix) + inspect.getsource(doctor.state_folder_fix)
    assert 'f"chmod 600 {_shell_path(at)}"' in fix and 'f"chmod 700 {_shell_path(' in fix


def test_readme_step3_gives_the_agent_the_same_chmod() -> None:
    """B13 round 22 Z38 (rehearsal F, home 2, h2-60 to h2-63): a manifest at 0000 at a step 3 sync stops
    the sync, then status, on its chmod 600; only step 2 named that chmod, ending "then the same install.sh
    command again", so step 3 had neither the bound nor what to run after it. Step 3 grants it once each in
    that step, after the sentence that keeps a [FAIL] fix of the person's theirs, with the sync as what comes
    next."""
    step3 = " ".join(_prompt_steps()[3].split())
    assert step3.count(STEP3_STATE_CHMOD_IS_YOURS) == 1
    assert (
        f"and a [FAIL] fix that is mine (step 2) stays mine. {STEP3_STATE_CHMOD_IS_YOURS} The steps" in step3
    )


def test_readme_finish_relays_the_local_work_next() -> None:
    """B13 round 22 Z30, Z71 (probe rs): the report's NEXT for a local-work branch whose commits bring-back.md
    does not carry, round 21 Y23's, had no clause in the finish, which relays the report's own NEXT only by
    case: an agent named bring-back.md and dropped the branch advice. The clause keys on install.sh's own
    words and stands before the cut-off clause, which wraps that NEXT."""
    finish = " ".join(_prompt_steps()[3].rsplit("Finish with:", 1)[1].split())
    assert f"{LOCAL_WORK_TOLD} {CUT_OFF_TOLD}" in finish
    script = SCRIPTS[0].read_text(encoding="utf-8")
    assert "$back does not carry the commits of " in script and "its section 3 says how to add them" in script


def test_readme_step2_does_not_log_a_failed_run_whose_next_it_follows() -> None:
    """B13 round 15 S18: v23 step 2's middle branch (do what a non-zero run's NEXT: says when it is an
    install.sh or agentsync command or a click) said nothing on logging, while the friction rule's "error (a
    command failed ...)" has no exception, so agents logged the retried run or did not. The report already
    names it (``_retry_line``: "exited 1 at its status step ...; the same command was run again and ended 0"),
    and a logged line adds a second item and a friction count for the same failure. v24 says it is not
    logged, and when the report names it: ``retried_runs`` pairs a failed run only with a later one that
    ended 0 (the lane's review). Not the finder's "log it as a step 2 error, then run it", which the
    verifier showed doubles the item on every retried install."""
    step2 = " ".join(_prompt_steps()[2].split())
    said = (
        "otherwise log it and go to step 3's report. A failed run whose NEXT: you follow this way is not "
        "logged (the report names it once a later run ends 0)."
    )
    assert said in step2 and "log it as a step 2 error, then run it" not in step2
    assert "error (a command failed; include its exit code)" in " ".join(_preamble().split())
    from agentsync import setup_report  # noqa: PLC0415

    assert "the same command was run again and ended 0" in Path(setup_report.__file__).read_text(
        encoding="utf-8"
    )


def test_section_9_says_who_logs_the_skip_and_what_isolation_leaves_out() -> None:
    """B13 round 20 X56: §9 step 2 still said `EMPTY_BODY_HOLDER` names an empty body "in the WARNING, the
    stderr line and doctor alike" and read as if every run logs the skip WARNING, and step 5 that errors are
    isolated per source: since round 19 W13 only the job's run logs it, since W14 the stderr line names the
    holder in the wait's words, and since W41 a state file that cannot be read leaves the source's isolation.
    Each claim of the two dated lines is read from the code."""
    from agentsync import cli, cycle  # noqa: PLC0415

    main = inspect.getsource(cli._main)
    skip = main[main.index("except LockHeldError as exc:") : main.index("return EXIT_LOCK_HELD")]
    assert 'f"skipped: lock held by {_skip_holder(exc)}"' in skip
    assert re.search(r"elif _in_job\(args\):[^\n]*\n\s+_log\.warning\(", skip)
    assert re.search(r"else:[^\n]*\n\s+_log\.debug\(", skip)
    assert "launchd.started_by_our_job(" in inspect.getsource(cli._in_job)
    assert "UNRECORDED_LOCK_HOLDER" in inspect.getsource(cli._skip_holder)
    isolate = inspect.getsource(cycle._Cycle._run_source)
    assert re.search(
        r"except Exception as exc:  # isolate[^\n]*\n\s+if _manifest_unreadable\(exc\):", isolate
    )
    section = " ".join(_contracts_section("## 9. The cycle", "## 10. ").split())
    step2 = section[section.index("2. `SingleWriterLock") : section.index("3. `Manifest(")]
    assert "Amended 2026-10-20, §16.151 X56 (by §16.143 W13, W14):** only the background job's run" in step2
    assert "`cycle.UNRECORDED_LOCK_HOLDER` (through `cli._skip_holder`)" in step2
    assert "`EMPTY_BODY_HOLDER`" in step2  # the empty-lock-body test's anchor
    step5 = section[section.index("5. For each live source") : section.index("6. Work queue")]
    assert (
        "Amended 2026-10-20, §16.151 X56 (by §16.143 W41):** the per-source isolation does not cover" in step5
    )
    assert "exits 65" in step5 and cli.EXIT_STATE == 65


def test_v21s_and_w1s_lines_say_table_damage_is_state_since_w41() -> None:
    """B13 round 20 X59: §16.136 V21 said `manifest_unreadable` is unchanged and init passes the config step
    on a table page it cannot read, and §16.142 W1 that `TD-items` fails without its reader; since round 19
    W41 the probe reads each table's first row before init, so `TD-items` reads `state`. Both have W41's
    line, read here against the probe and the install test's word map."""
    from agentsync.ops import doctor  # noqa: PLC0415

    assert "NOT INDEXED LIMIT 1" in inspect.getsource(doctor.manifest_unreadable)
    oneshot = (ROOT / "tests" / "test_install_oneshot.py").read_text(encoding="utf-8")
    assert '"TD-items": "state",' in oneshot and '"TD-items-older"' in oneshot
    claims, _bare, _held, unheld = _dated_line_scan()
    assert ("16.151", ["X59"], "16.136", "V21") in claims and ("16.151", ["X59"], "16.142", "W1") in claims
    assert unheld == []
    v21 = " ".join(
        _contracts_section("**Step 1 names a docs-repo remote [governance] refuses", "\n\n").split()
    )
    assert "§16.151 X59 (by §16.143 W41): `manifest_unreadable` is no longer unchanged" in v21
    assert "damage on a table's root or first-row page is `state`, and step 2 stops at its config step" in v21
    w1 = " ".join(_contracts_section("**Step 1's damaged-table check reads every table", "\n\n").split())
    assert "§16.151 X59 (by §16.143 W41): `TD-items` no longer reaches this reader" in w1


def test_the_recording_tests_limit_names_cis_own_limit() -> None:
    """B13 round 20 X61: round 19 W70's note and the `_wide_limit` fixture said 600 s is well under CI's 30
    minutes, though W80 had set the job's limit to 40 first. Both name the limit ci.yml sets."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    [limit] = re.findall(r"^jobs:\n  test:\n(?:    .*\n)*?    timeout-minutes: (\d+)\n", ci, re.M)
    fixture = " ".join((ROOT / "tests" / "test_convert_recording.py").read_text(encoding="utf-8").split())
    w70 = " ".join(
        _contracts_section("**The recording tests' time limit holds under load (W70", "\n\n").split()
    )
    for text in (fixture, w70):
        assert "CI's 30 minutes" not in text
        assert re.search(
            rf"well under the ci job's {limit} minutes[ ,(]+`+timeout-minutes`+ in ci\.yml", text
        )


def test_a_round_19_or_later_citation_names_a_section_that_holds_its_item() -> None:
    """B13 round 20 X62: §16.145 W58 cited the slow-doctor grace as §16.137's V4, which is §16.134's. Every
    `§16.N <item>` written from §16.140 (round 19) on names an item section N holds. B13 round 21 Y77: the
    read counted an item held wherever section N named it, a `§16.M <item>` citation of another section
    included, so §16.110's mention of Q27's home passed two citations of Q27 there; and it read only §16.140
    on, where eight older citations failed. Every section is read now, and an item is held only where the
    section names it outside a citation of another section (a dated line's own `Amended <date>, §16.M <item>:`
    marker counts: the section holds that item's line). Nine citations were corrected; :data:`named` holds the
    two that name a section's citation of an item, not the item's home."""
    sections = dict(_contracts_numbered_sections())
    cite = r"§(16\.\d+)(?:'s)? ((?:[A-Z]\d+)(?:(?:, | and )[A-Z]\d+)*)\b"

    def own(text: str) -> str:
        # the marker's own `§16.M <items>`, not a "(by ...)" list after it
        marker = r"Amended\s+\d{4}-\d\d-\d\d,\s+§16\.\d+(?:'s)? [A-Z]\d+(?:(?:, | and )[A-Z]\d+)*"
        markers = [m.span() for m in re.finditer(marker, text)]
        return re.sub(cite, lambda m: m[0] if any(a <= m.start() < b for a, b in markers) else "", text)

    holds = {number: own(text) for number, text in sections.items()}
    # "§16.80's N37 target": the N37 §16.80's heading targets; "§16.117 S34 and Q29's amend line": S34's line,
    # which amends Q29
    named = {("16.85", "§16.80's N37"), ("16.127", "§16.117 S34 and Q29")}
    read, wrong = 0, []
    for number, text in sections.items():
        if not re.fullmatch(r"16\.\d+", number):
            continue
        for found in re.finditer(cite, text):
            for item in re.findall(r"[A-Z]\d+", found.group(2)):
                read += 1
                if not re.search(rf"\b{item}\b", holds.get(found.group(1), "")):
                    wrong.append(f"§{number}: {found.group(0)}")
    assert read >= 2000, "the scan is broken"
    assert sorted(set(wrong)) == sorted(f"§{n}: {c}" for n, c in named)
    flat = {number: " ".join(text.split()) for number, text in sections.items()}
    assert "checkout's renderer would mix two builds in doctor (§16.111 Q27 turned it down;" in flat["16.123"]
    assert "the checkout's src stays refused (§16.111 Q27, §16.123 T11)." in flat["16.129"]
    assert "Read with §16.39 N16 and §16.53 Q26 (round 4 left its install.sh half unbuilt)." in flat["16.58"]
    assert "- `_describe_holder` (`agentsync.ops.lock`; N6): a held lock with an empty body" in flat["16.41"]
    assert "its 120 s wait plus the uv run, and for an `is_held` probe. §16.41 N6 changed" in flat["16.39"]
    assert "§16.134 V4 gives a slow doctor at least `_DOCTOR_GRACE_S`" in " ".join(sections["16.145"].split())


def test_a_code_comments_contracts_citation_names_a_section_that_holds_its_item() -> None:
    """B13 round 22 Z68: a comment in ops/lock.py cited "§16.151 X16", which §16.149 holds (the spec's slip,
    which §16.155 Y27's own entry had corrected), and the CONTRACTS-only scan above never read code. Every
    ``§16.N <item>`` in src/ and scripts/ names an item section N holds, by that scan's rule; two more were
    wrong and are corrected (setup_report's §16.97 M3 is §16.93's, install.sh's §16.110 Q27 §16.111's)."""
    sections = dict(_contracts_numbered_sections())
    cite = r"§(16\.\d+)(?:'s)? ((?:[A-Z]\d+)(?:(?:, | and )[A-Z]\d+)*)\b"
    marker = r"Amended\s+\d{4}-\d\d-\d\d,\s+§16\.\d+(?:'s)? [A-Z]\d+(?:(?:, | and )[A-Z]\d+)*"

    def own(text: str) -> str:
        spans = [m.span() for m in re.finditer(marker, text)]
        return re.sub(cite, lambda m: m[0] if any(a <= m.start() < b for a, b in spans) else "", text)

    holds = {number: own(text) for number, text in sections.items()}
    files = [*sorted((ROOT / "src").rglob("*.py")), *sorted((ROOT / "scripts").glob("*.sh"))]
    read, wrong = 0, []
    for path in files:
        for at, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for found in re.finditer(cite, line):
                for item in re.findall(r"[A-Z]\d+", found.group(2)):
                    read += 1
                    if not re.search(rf"\b{item}\b", holds.get(found.group(1), "")):
                        wrong.append(f"{path.relative_to(ROOT)}:{at}: {found.group(0)}")
    assert read >= 20, "the scan is broken"
    assert wrong == [], wrong


def _guide_line(name: str) -> str:
    """The whole line of the setup-feedback guide's row ``| <name> |``."""
    text = (ROOT / "docs" / "deploy" / "setup-feedback.md").read_text(encoding="utf-8")
    [line] = [ln for ln in text.splitlines() if ln.startswith(f"| {name} |")]
    return line


def test_the_agent_named_folders_row_says_the_agents_name_keeps_it() -> None:
    """B13 round 20 X64: the row said a configured agent-named folder is a placeholder, while since round 19
    W17 the agent's name keeps it as written and the report's Configuration line says so beside the counts.
    B13 round 21 Y60: round 20 X38 added a step 1 line that states its agent to the clause, and the row and
    §16.28's X64 line, which a check of the clause's shared start passed, left it out."""
    from agentsync import setup_report  # noqa: PLC0415

    source = inspect.getsource(setup_report)
    assert ", except as a word of the agent's name in the friction log's Agent: line" in source
    # the clause is two literals in the source: join them
    clause = re.search(r'"(, except as a word of the agent\'s name in [^"]*)"\s*"([^"]*)"', source)
    assert clause is not None
    clause_text = clause.group(1) + clause.group(2)
    assert clause_text == (
        ", except as a word of the agent's name in the friction log's Agent: line"
        " or a step 1 line that states it"
    )
    assert "def redact_agent(" in source and "def scrub_agent(" in source
    row = _guide_line("Configuration: agent-named folders")
    assert "A configured one is a placeholder, except as a word of the agent's name, which keeps it as" in row
    assert (
        "the friction log's `Agent:` line, a step 1 line that states it (`prompt vN, <agent>:`), and" in row
    )
    assert "the issue link; the line then says so; B13 round 19 W17, round 20 X38). |" in row
    assert "; a configured one is a placeholder. |" not in row
    configuration = " ".join(
        _contracts_section("**Configuration.** One line: how many folders", "\n\n").split()
    )
    y60 = configuration.split("Amended 2026-10-20, §16.157 Y60 (by §16.146 X38):")
    assert len(y60) == 2 and f"the line ends `{clause_text}`." in y60[1]


def test_the_folder_placeholder_row_names_a_project_folders_slug_id() -> None:
    """B13 round 20 X65: the redaction table said an id is a folder's `<folder-N>` only when it is a cloud
    folder's name in slug form; since round 19 W6 a project folder's slug id is too, in the agentsync report
    (its name registered fuzzy, or `Redactor.alias` for a nested one). install.sh's report is unchanged."""
    from agentsync import setup_report  # noqa: PLC0415

    build = inspect.getsource(setup_report._build_redactor)
    assert 'fuzzy="/" not in value and not joined' in build and "red.alias(src.id," in build
    row = _guide_line("`<library-N>`, `<folder-N>`, `<source-N>`")
    assert (
        "an id that is its cloud folder's name in slug form is that folder's `<folder-N>`, as, in the"
        " agentsync report since 2026-10-20 (B13 round 19 W6), is an id that is a project folder's name in"
        " slug form"
    ) in row


def test_the_ocr_row_says_a_deleted_files_count_goes_at_the_next_sync() -> None:
    """B13 round 20 X66: the OCR row said a finished scan's count stays in the record; since round 19 W75 the
    next sync drops a deleted file's (or one with no row), as the report's own lead says, and `deleted` and
    `with no row` show only until then or in an older build's record."""
    from agentsync import cycle, setup_report  # noqa: PLC0415

    assert "RowState.TOMBSTONE" in inspect.getsource(cycle._Cycle._reread_load)
    assert "the record keeps its count, a deleted file's only until the next sync" in inspect.getsource(
        setup_report
    )
    row = _guide_line("OCR")
    assert (
        "under a finished scan the count stays in the record, a deleted file's (or one with no row) only"
        in row
    )
    assert "and, until that sync or in a record an older build left, `deleted` or `with no row`)" in row


def test_the_repeat_conversions_row_names_the_upgrade_retry_note() -> None:
    """B13 round 20 X67: the row named the re-screen note and the converter-waiting line but not round 19
    W18's `tried again after an upgrade:` note, so an old conversion error back in the runs after an
    upgrade had no word in the guide, whose ordinary retry reads as ruling it out. The label is read from
    the report. B13 round 21 Y41, Y52, Y58, Y74 (one defect): the row gave the first try as the upgrade's
    run and the second as the next, which round 20 X53 made false for a source that run does not read; it
    quotes the report's bullet now, read from the report. B13 round 23 A77: since round 22 Z35 and Z55 the
    note names per run the files that run converted and those it settled with no try left, and gives the
    retry words only when a shown run has files left to retry; the row said every such file could show its
    old error. It names both, read from the report. B13 round 23 A63: and the files that run left waiting for
    a converter, which the first run with one reads again, so their old error can show then."""
    from agentsync import setup_report  # noqa: PLC0415

    source = inspect.getsource(setup_report)
    note = re.search(r'f"- (tried again after an upgrade): \{each\}', source)
    assert note is not None
    row = _guide_line("Repeat conversions")
    assert f"a `{note.group(1)}:` note under the table gives their count per run" in row
    assert "possibly in a background cycle nobody watched" in row and "§16.144, W18; §16.150, X53). |" in row
    # the move takes every source's failures, and a source the upgrade's run does not read is tried in its
    # own later runs (B13 round 20 X53); the report's literal is split over two source lines, so read the
    # one-line fragment
    tries = re.search(
        r"tries each again from the same bytes (in up to 2 runs of its source from that one on)", source
    )
    assert tries is not None
    assert (
        f"tried again from the same bytes {tries.group(1)}, so an old conversion error can show in those runs"
        in row
    )
    assert "makes its first try in a later run of its own" in row
    assert "the first in that run" not in row and "that run or the next" not in row
    # the files that run converted or settled show no old error, and the retry words need a file left
    tried = inspect.getsource(setup_report._repeat_part)
    done = re.search(
        r'\(cleared, "(converted)"\),\s*\(settled, "(settled with no try left)"\),\s*'
        r'\(waiting, "(left waiting for a converter)"\),',
        tried,
    )
    assert done is not None and 'f"{n} that run {verb}"' in tried
    assert "if any(rest for _words, rest, _waiting in said)" in tried
    assert (
        f"the files that run converted (`which that run {done.group(1)}`) and those it settled with no try"
        f" left (an unreadable stub, a refusal or a removal: `that run {done.group(2)}`); neither can show an"
        " old error, and the retry words follow only when some shown run still has files left to retry"
        " (CONTRACTS §16.158, Z35; §16.162, Z55)."
    ) in row
    assert row.index("makes its first try in a later run of its own") < row.index(
        f"`that run {done.group(2)}`"
    )
    assert (
        "The files it left waiting for a converter (a `no converter` stub, such as an image that run read"
        f" with no OCR engine: `that run {done.group(3)}`) are read again by the first run with a converter"
        " for their type, so an old conversion error can show in that run (CONTRACTS §16.168, A63)."
    ) in row
    # the note sits beside the other notes, before the Quarantine sentence
    assert row.index(f"`{note.group(1)}:`") < row.index("Quarantine by reason counts")


def test_the_guides_step_2_next_quotes_the_folders_same_pass_chmod() -> None:
    """B13 round 23 A70: since round 22 Z81 step 2's config-step NEXT for `chmod 700` of the state folder has
    the agent run status and each chmod its [FAIL] lines name for a path in that folder, in the same pass,
    before the re-run; only `chmod 600` keeps `run <fix> once, then re-run:`. The guide quoted the file's NEXT
    with `or chmod 700 <folder>` put in, as if the folder's had the same form. It quotes each, read from
    install.sh's NEXT lines."""
    script = SCRIPTS[0].read_text(encoding="utf-8")
    nexts = re.findall(
        r'FAIL_NEXT="[^"]*: the fix is the agent\'s: run \$fix( once, then[^"]*?re-run: )\$SELF', script
    )
    [folder] = [n for n in nexts if "in the same pass" in n]
    assert " once, then re-run: " in nexts and "status" in folder and "[FAIL]" in folder
    page = " ".join(_feedback_page().split())
    assert (
        "`NEXT:`: for a file, `agentsync's state folder or a file in it has a mode this account cannot open"
        " ...: the fix is the agent's: run chmod 600 <file> once, then re-run: ...` (with the -wal and -shm"
        " its mode refuses too, as status's `state_dir.files` names them); for the folder, "
        f"`... run chmod 700 <folder>{folder}...` (round 22 Z81"
    ) in page
    assert "or `chmod 700 <folder>`)" not in page


def test_the_guides_redaction_rows_name_own_moved_folders_and_dotted_remotes() -> None:
    """B13 round 23 A72: since round 22 Z21, Z28 the agentsync report registers each of agentsync's own
    folders that sources.toml moves (`_own_folder_values`) as a `<folder-N>`, after the configured folders
    elsewhere and before the source ids, and since Z76 both reports read git's older dotted
    `[remote.<name>]` as a remote; the guide's `<folder-N>` row, its numbering order and its host row named
    neither. The order and the dotted form are read from the report and the installer. Since round 23 A41 the
    shell fallback report registers them too, by a twin of `_own_folder_values`, after the configured folders
    elsewhere and before the source ids, so the guide says both reports."""
    from agentsync import setup_report  # noqa: PLC0415

    source = inspect.getsource(setup_report)
    projects = source.index("for path in project_paths:")
    own = source.index("_own_folder_values(expand(own_dir), expand(default))")
    assert projects < own < source.index("cloud_ids = {sid")
    script = SCRIPTS[0].read_text(encoding="utf-8")
    shell_projects = script.index('\t\t\tproject_values "$line"\n')
    shell_own = script.index('own_folder_values "$own_state" "$HOME/Library/Application Support/agentsync"')
    assert shell_projects < shell_own < script.index('add_pair "$v" "<source-$tables>" w')
    [row] = [ln for ln in _feedback_page().splitlines() if ln.startswith("| `<library-N>`, `<folder-N>`")]
    assert (
        "and, in the agentsync report since 2026-10-21 (B13 round 22 Z21, Z28, CONTRACTS §16.158) and in the"
        " installer's fallback report since 2026-10-22 (B13 round 23 A41, CONTRACTS §16.165), each of"
        " agentsync's own folders (`state_dir`, `docs_repo`, `cache_dir`, `log_dir`) that sources.toml moves"
        " from its default, as whole words:"
    ) in row
    assert "anywhere else, the whole path; one at its default takes none |" in row
    page = " ".join(_feedback_page().split())
    assert (
        "then configured folders elsewhere, then, in both reports, agentsync's own folders that"
        " sources.toml moves from their defaults, and last a listing that finished after its 3-second bound"
    ) in page
    assert 'section.startswith("remote.")' in source and "dotted [remote.<name>] (Z76)" in script
    [hosts] = [ln for ln in _feedback_page().splitlines() if ln.startswith("| `<email-N>`, `<guid-N>`")]
    assert (
        '(`[remote "<name>"] url`, and, since 2026-10-21, git\'s older dotted `[remote.<name>]` such as'
        ' `[remote.origin]` or `[remote.contoso "docs"]`, in both reports (B13 round 22 Z76, CONTRACTS'
        " §16.158 and §16.159);"
    ) in hosts


def test_the_cut_off_words_keep_every_check_ran_apart_from_the_state_read() -> None:
    """B13 round 23 A73: §16.152 Y22's round 22 dated line and `_cut_off_words`' docstring said a cut-off with
    no check named came in the hook's read of the loop state; since da077f3b the hook's "" (every check ran)
    is kept apart from its None as `doctor_cut_off_reading` and keeps the `a check` words. The docstring and a
    dated line say so."""
    from agentsync import setup_report  # noqa: PLC0415

    doc = " ".join((setup_report._cut_off_words.__doc__ or "").split())
    assert "so a cut-off with no check named came in that read" not in doc
    assert (
        'its None, ``doctor_cut_off_reading``) came in that read; its "" (every check ran) is not that read'
        ' and keeps the "a check" words'
    ) in doc
    assert "r.facts.doctor_cut_off_reading = at is None" in inspect.getsource(setup_report._read_cut_off)
    y22 = " ".join(_contracts_section("### 16.152 ", "### 16.153 ").split())
    assert "Amended 2026-10-22, §16.169 A73: not every cut-off with no check named came in that read." in y22
    assert (
        "A doctor cut off after every check ran names no check and gets neither the loop-state words" in y22
    )


def test_u78s_line_quotes_the_again_fix_head() -> None:
    """B13 round 20 X68: §16.132 U78 quoted `_again_fix` as `run the same <installer> command again ...`;
    since round 19 W19, W48 it starts `doctor.AGAIN_UNDER_INSTALL_SH` under install.sh, and U78 had no
    line."""
    from agentsync.ops import doctor  # noqa: PLC0415

    head = doctor.AGAIN_UNDER_INSTALL_SH.replace(doctor.INSTALL_SH, "<install.sh>")
    # B13 round 20 X21 (lane B) changed the head after X68's line: X68 quotes round 19's, X21 round 20's, and
    # B13 round 21 Y20 (lane B) the current, with no bracket of its own
    assert head.startswith("run <install.sh> again without --report-only, which builds nothing")
    claims, _bare, _held, _unheld = _dated_line_scan()
    assert ("16.151", ["X68"], "16.132", "U78") in claims
    assert ("16.149", ["X20", "X21"], "16.132", "U78") in claims
    assert ("16.155", ["Y20"], "16.132", "U78") in claims
    u78 = " ".join(
        _contracts_section(
            "**A time-out under install.sh does not name the same re-run twice", "\n\n"
        ).split()
    )
    round_19 = "run <install.sh> again with the options of the run that installed agentsync"
    round_19 += " (not --report-only, which builds nothing)"
    x68 = f"§16.151 X68 (by §16.143 W19, W48): under install.sh `_again_fix` starts `{round_19}`"
    assert x68 in u78
    round_20 = "run <install.sh> again (it keeps the folders already synced; not --report-only, which builds"
    round_20 += " nothing)"
    assert f"§16.149 X21: under install.sh `_again_fix` starts `{round_20}`" in u78
    assert f"§16.155 Y20: under install.sh `_again_fix` starts `{head}`" in u78


def test_y49s_line_says_staging_is_emptied_in_place() -> None:
    """B13 round 21 Y49: since round 20 X4 `cycle._clear_staging` empties staging in place, but
    `tm_exclude.set_exclusion`'s docstring and §16.12's governance paragraph still gave "recreated every
    cycle" as the setxattr's reason. The docstring names the callers that spare a `tmutil` call, and §16.12
    has the dated line (its text is kept as written)."""
    from agentsync import tm_exclude  # noqa: PLC0415

    doc = " ".join((tm_exclude.set_exclusion.__doc__ or "").split())
    assert "is recreated every cycle" not in doc and "cycle._clear_staging" in doc, doc
    assert "ensure_time_machine_exclusions" in doc
    governance = " ".join(
        _contracts_section("**Governance (security-governance-10/11/14).**", "\n\n").split()
    )
    assert "§16.155 Y49 (by §16.149 X4): `staging/` is no longer recreated" in governance
    assert len(_pinned_dated_lines(_ROUND_21_B_PARAGRAPH_AMENDS[:1])) == 1


def test_the_guide_routes_a_report_after_a_stopped_copy_as_install_sh_does() -> None:
    """B13 round 21 Y61: since round 20 X19 any run that writes a report after a copy install.sh stopped at
    step 1, with no --log-start since (a plain install.sh included), writes stopped-copy-report.md and adds a
    clause to its own NEXT, and since round 21 Y17 no run resolves the stop; the guide said only
    `--report-only` routes it, and that any step 1 run resolves a step 1 error. The clause is read from
    install.sh and Y17's no-resolve arm from `setup_report.stopping_error`. B13 round 22 Z62: §16.157's
    Y61 paragraph still gave X19's end-line rule, which Y17 had removed; its dated line says no run resolves
    it."""
    from agentsync import setup_report  # noqa: PLC0415

    install = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    added = re.search(
        r'NEXT_MSG="\$\{NEXT_MSG:\+\$NEXT_MSG; \}(the last setup attempt was a copy [^"]*)"', install
    )
    assert added is not None
    assert 'if tell="$(friction_attempt_stopped)"; then' in install
    assert '[ "$REPORT_ONLY" -eq 1 ] || stopped=2' in install
    stopping = inspect.getsource(setup_report.stopping_error)
    # B13 round 21 Y17 (lane RA): no run resolves that stop, one in the end line's own second included
    assert "if _PROMPT_STOP_RE.match(e.what):  # Y17: no run resolves the installer's own stop" in stopping
    assert "done = []" in stopping
    guide = " ".join((ROOT / "docs" / "deploy" / "setup-feedback.md").read_text(encoding="utf-8").split())
    assert (
        "a `--list-folders` re-run included, but not for install.sh's own `--log-start` stop of a copy"
        in guide
    )
    assert "That stop is resolved by no run (CONTRACTS §16.147, X19; §16.152, Y17:" in guide
    assert "so a later install.sh or `--list-folders` run leaves the attempt failed at step 1" in guide
    assert "Since 2026-10-20 (CONTRACTS §16.147, X19), any other run that writes a report after such" in guide
    assert "(a plain `install.sh` the stopped copy's Doctor asked for, say), routes it the same way" in guide
    assert f"It keeps its own `NEXT:` and adds (after a `; ` when there is one) `{added.group(1)}`." in guide
    y61 = dict(_contracts_numbered_sections())["16.157"].split(
        "**The guide routes a report after a stopped", 1
    )[1]
    y61 = " ".join(y61.split("\n**", 1)[0].split())
    assert y61.endswith(
        "Amended 2026-10-21, §16.163 Z62 (by §16.152 Y17): no run resolves the stop, a run in the end line's"
        " own second included; a re-run with the right copy opens an attempt of its own."
    )


def test_the_guides_private_only_rule_names_the_product_word_exception() -> None:
    """B13 round 22 Z63: since round 21 Y7, Y72 a configured folder or library named only with coding-agent
    product words is redacted but makes neither report private, yet the guide's Private-only rule named only a
    drive's own folders (`Documents`) as the exception. The new sentence's examples are checked against
    `setup_report._agent_words` and a `Redactor`, and none is a name of two words, which never made a report
    private."""
    from agentsync import setup_report  # noqa: PLC0415

    page = _page("docs/deploy/setup-feedback.md")
    found = re.search(
        r"So, since 2026-10-21, is one named only with coding-agent product words \(([^;)]*); the words are"
        r" ([a-z, ]+), \[CONTRACTS §16\.153\]\(\.\./design/CONTRACTS\.md\) Y7, Y72\): it is still redacted,"
        r" but it does not make either report private\.",
        page,
    )
    assert found is not None
    words = set(re.split(r", (?:and )?| and ", found.group(2)))
    assert words == setup_report._AGENT_WORDS
    examples = re.findall(r"`([^`]*)`", found.group(1))
    assert examples
    for name in examples:
        assert " " not in name and setup_report._agent_words(name), name
        red = setup_report.Redactor()
        red.add("folder", name)
        red.redact(f"the {name.lower()} of this report and {name}")
        assert not red.word_named_shown, name
    # a word the report prints that is no product word still makes it private: the exception is the words'
    red = setup_report.Redactor()
    red.add("folder", "Status")
    red.redact("Status")
    assert red.word_named_shown


def test_the_guides_stopped_copy_bullet_follows_the_end_line_filter() -> None:
    """B13 round 22 Z69: the stopped-copy bullet and the K28 row said an attempt with an install.sh run in it
    is read by the usual rules, while since round 21 Y18 `_stopped_before_the_list` leaves out a plain install
    run from the attempt's end line on (the stand-alone run the stopped copy's Doctor asks for). The filter is
    read from the report."""
    from agentsync import setup_report  # noqa: PLC0415

    source = inspect.getsource(setup_report._stopped_before_the_list)
    assert (
        "inside = [r for r in runs if r.list_only or end is None or r.started is None or r.started < end]"
        in (source)
    )
    page = _page("docs/deploy/setup-feedback.md")
    assert "an attempt with an `install.sh` run in it (the agent listed the folders all the same)" not in page
    assert "with no `install.sh` run in it, counts no folder question" not in page
    assert (
        "and so is a folder list the agent ran all the same, whenever it started: that list puts the attempt"
        " back under the usual rules. A plain `install.sh` run from the attempt's end line on (the"
        " stand-alone run the stopped copy's Doctor asks for) does not." in page
    )
    assert (
        "with no folder list in it (a stand-alone install.sh run after its end line does not count, §16.152"
        " Y18), counts no folder question" in page
    )


def test_the_guides_path_row_quotes_the_shell_reports_legend() -> None:
    """B13 round 22 Z70: since round 21 Y8 the shell report's `<path>` legend says install.log dollar-quotes a
    name with a tab or another control character, a curly apostrophe, an en dash or an accent (under the C
    locale, any character outside ASCII); the guide's `<path>` row still said a curly apostrophe, a dash or an
    accent. The legend's words are read from install.sh."""
    install = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    block = install.split('case "$used" in *" <path> "*)', 1)[1].split(";;", 1)[0]
    said = " ".join(" ".join(re.findall(r'^\s*say "  ?(.*)"$', block, re.M)).split())
    legend = re.search(r"which install\.log uses (for a name with [^)]*\))", said)
    assert legend is not None
    guide = (ROOT / "docs" / "deploy" / "setup-feedback.md").read_text(encoding="utf-8")
    row = next(line for line in guide.splitlines() if line.startswith("| `<path>` |"))
    assert "how install.log writes a name with a curly apostrophe, a dash or an accent" not in row
    assert f"every `$'...'` word, which install.log writes {legend.group(1)}," in row


def test_readme_section_4_says_curate_exits_1_on_a_state_it_cannot_open() -> None:
    """B13 round 22 Z66: README §4's 2026-10-04 line says curate exits 1 only on a blocking finding, true when
    written; since round 19 W65 and round 20 X73 a state file or the state folder this account cannot open
    exits 1 too (round 21 Y76 told §9 and curate's help). The dated line stays, and a later CORRECTED line
    says the rest, as curate's help line, read from `cli`, does."""
    from agentsync import cli  # noqa: PLC0415

    source = " ".join(inspect.getsource(cli).replace('"\n        "', "").split())
    assert "(rc 1 on a blocking finding or a state file this account cannot open, never on rows)" in source
    readme = _page("README.md")
    old = readme.index("**CORRECTED (2026-10-04):** `agentsync refresh-queue` is now a hidden alias")
    new = readme.index(
        "**CORRECTED (2026-10-21, B13 round 22):** curate also exits 1 when a state file or the state folder"
        " cannot be opened by this account"
    )
    assert old < new
    assert "Work rows still exit 0, so the work list is still the rows, not the exit code." in readme[new:]


def test_section_9s_exit_codes_name_65_and_a_state_file_this_account_cannot_open() -> None:
    """B13 round 21 Y76: §9's CLI exit-code list had no 65 (a damaged manifest, round 16 T12 and round 17
    U66) and said curate exits 1 only on a blocking finding, while since round 19 W65 and round 20 X73 a
    state file or folder this account cannot open exits 1 too, and curate's own help line said the same.
    Each code and arm is read from cli.py."""
    from agentsync import cli  # noqa: PLC0415

    source = inspect.getsource(cli)
    assert cli.EXIT_STATE == 65 and cli.EXIT_FAILED == 1
    assert "  65  the state file (manifest.sqlite) is no database or a damaged one" in cli._EPILOG
    assert "or the state file cannot be\n      opened by this account" in cli._EPILOG
    main = inspect.getsource(cli._main)
    # each arm up to the next one, so a later arm's return answers for none before it
    arms = {arm.split(maxsplit=1)[0]: arm for arm in main.split("\n    except ")[1:]}
    assert "return EXIT_STATE" in arms["StateUnreadableError"]
    assert "return EXIT_STATE" in arms["sqlite3.DatabaseError"]
    assert (
        "return EXIT_FAILED" in arms["ManifestOpenError"] and "return EXIT_FAILED" in arms["PermissionError"]
    )
    assert "_state_readable(config)" in inspect.getsource(cli._run_report)
    assert "with _state_readable(config)" in inspect.getsource(cli._ensure_setup)
    assert "rc 1 only on a blocking" not in source and "exit 1\n    only on a blocking" not in source
    usage = " ".join(cli.build_parser().format_help().split())
    assert "(rc 1 on a blocking finding or a state file this account cannot open, never on rows)" in usage
    exits = " ".join(_contracts_section("CLI exit codes (`cli.py`): 0 ok", "\n\n").split())
    marker = (
        "Amended 2026-10-20, §16.157 Y76 (by §16.125 T12; §16.131 U66; §16.143 W41, W65; §16.149 X50, X73): "
    )
    [y76] = exits.split(marker)[1:]
    assert y76.startswith("65 means the state file (manifest.sqlite) is no database or a damaged one")
    assert "1 also covers a state file or state folder this account cannot open (`ManifestOpenError`," in y76
    assert "So `curate` exits 1 on a blocking finding or on such a state file, never on rows." in y76


def test_the_shut_buckets_contract_lines_are_the_reports() -> None:
    """B13 round 21 Y40: §16.146 gave the shut bucket's warn as `not judged: the loop cannot read the state`,
    which round 20 X15, X46 changed, and §16.149 said the bucket is keyed on `doctor.state_unopenable`, which
    the report never calls: `_sort_doctor` keys it on `_state_shut`, which tests the file and its folder too.
    Both are read from the report."""
    from agentsync import setup_report  # noqa: PLC0415

    words = re.search(
        r'from_shut\.append\(f"\{name\} (warn \(not judged: [^)]*\))"\)', inspect.getsource(setup_report)
    )
    assert (
        words is not None and words.group(1) == "warn (not judged: this account cannot open the state file)"
    )
    sort = inspect.getsource(setup_report._sort_doctor)
    assert "_state_shut(r.config, read_error)" in sort and "state_unopenable" not in sort
    shut = inspect.getsource(setup_report._state_shut)
    assert "state_unopenable" not in shut and 'return "folder"' in shut
    x28 = " ".join(
        _contracts_section(
            "**A state file this account cannot open has its own doctor bucket, and the outcome", "\n\n"
        ).split()
    )
    assert (
        "Amended 2026-10-20, §16.157 Y40 (by §16.149 X15, X46):** the warn in that bucket reads"
        f" `heartbeat.<id> {words.group(1)}`" in x28
    )
    x15 = " ".join(
        _contracts_section(
            "**X14, X15, X46: the listing note over a state file that cannot be read", "\n\n"
        ).split()
    )
    assert (
        "Amended 2026-10-20, §16.157 Y40:** it is keyed on `setup_report._state_shut`, the report's own test"
        in x15
    )


def test_exit_1_names_a_state_file_this_account_cannot_open() -> None:
    """B13 round 20 X69: since round 19 W65 a sync on a manifest this account cannot open (root-owned after
    one sudo run) exits 1, and the deploy guide, the report's `EXIT_MEANINGS` and `--help` decoded 1 as a
    failed source or lint only. Each names it; the guide gives its fix."""
    from agentsync import cli, setup_report  # noqa: PLC0415

    main = inspect.getsource(cli._main)
    handler = main[main.index("except ManifestOpenError as exc:") :]
    assert handler[: handler.index("except AgentSyncError")].rstrip().endswith("return EXIT_FAILED")
    assert "or the state file cannot be opened by this account" in setup_report.EXIT_MEANINGS[1]
    assert "or the state file cannot be opened by this account" in " ".join(cli._EPILOG.split())
    readme = _page("docs/deploy/README.md")
    listed = readme.split("## Exit codes the jobs report", 1)[1]
    one = listed[listed.index("· `1` ") : listed.index("· `64` ")]
    assert "one such error is a state file this account cannot open" in one
    assert "(owner, mode 600); the file is not damaged, so do not move it aside" in one


def test_w41s_measurement_counts_the_tables_the_test_damages() -> None:
    """B13 round 20 X70: §16.143 W41 said 13 of the 14 tables failed on the pre-fix source, all but three that
    passed: 13 is the count of the 16 cases (11 tables and both last-leaf cases). The numbers are read from
    the test that measured them."""
    source = (ROOT / "tests" / "test_cli.py").read_text(encoding="utf-8")
    block = re.search(r"^_MANIFEST_TABLES = \(\n(.*?)^\)", source, re.M | re.S)
    assert block is not None
    tables = re.findall(r'^    "(\w+)",$', block.group(1), re.M)
    leaves = re.findall(r'\("(\w+)", "last leaf"\)', source)
    reached = ["meta", "items", "runs"]  # status's loop read already reached these before W41
    assert len(tables) == 14 and set(reached) <= set(tables) and len(leaves) == 2
    failed = len(tables) - len(reached)
    w41 = " ".join(_contracts_section("**W41: damage under any table fails status", "\n\n").split())
    assert (
        f"{failed} of the {len(tables)} tables (all but meta, items and runs, which status's loop read"
        f" already reached) and both last-leaf cases failed, {failed + len(leaves)} of the"
        f" {len(tables) + len(leaves)} cases"
    ) in w41
