"""The IT deploy pack (docs/deploy, scripts): the Entra manifest, the PPPC profile, the two shell scripts and
the links between the pack's pages.  C15 (docs/design/receipts/verify/C15-corporate-controls.md) section 9
item 7 asks for the manifest check; the rest keeps the pack an IT admin receives from rotting silently."""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "docs" / "deploy"
ENTRA = DEPLOY / "entra-app.json"
PPPC = DEPLOY / "mdm" / "agentsync-pppc.mobileconfig"
SCRIPTS = (ROOT / "scripts" / "install.sh", ROOT / "scripts" / "tenant-probes.sh")

REDIRECT_URIS = ["http://localhost", "msauth.com.msauth.unsignedapp://auth"]
"""C15 section 1.6: the loopback rung (MSAL uses http://localhost:<port>) and the macOS broker rung (MSAL
hard-codes the unsigned-app URI for any Python app)."""

on_macos = pytest.mark.skipif(sys.platform != "darwin", reason="plutil is macOS-only")


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


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_parse_with_bash_n(script: Path) -> None:
    proc = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck is not installed")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_pass_shellcheck(script: Path) -> None:
    proc = subprocess.run(["shellcheck", str(script)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def tmp_home_env(home: Path) -> dict[str, str]:
    """A minimal environment whose HOME is ``home``: nothing reads or writes the real ~/agent-context."""
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "TMPDIR")}
    env["HOME"] = str(home)
    return env


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
    assert proc.stdout.startswith("agentsync installer") and "--dry-run" in proc.stdout
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


def _install_dry_run(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """install.sh --dry-run with HOME = ``home`` and no uv on PATH (so no real uv is ever invoked)."""
    env = tmp_home_env(home) | {"PATH": "/usr/bin:/bin"}
    return subprocess.run(
        ["bash", str(SCRIPTS[0]), "--dry-run", *args],
        cwd=home,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _tree(root: Path) -> list[str]:
    return sorted(str(p) for p in root.rglob("*"))


def test_install_dry_run_passes_source_local_to_init(tmp_path: Path) -> None:
    one = tmp_path / "OneDrive-Contoso" / "FY26 Projects"
    two = tmp_path / "notes"
    one.mkdir(parents=True)
    two.mkdir()
    before = _tree(tmp_path)
    proc = _install_dry_run(tmp_path, "--source-local", str(one), "--source-local", "~/notes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout
    assert f"source-local: {one}\n" in out and f"source-local: {two}\n" in out  # ~/ is expanded
    [init] = [ln for ln in out.splitlines() if ln.startswith("[dry-run]") and " init " in ln]
    config = tmp_path / "agent-context" / "sources.toml"
    assert init.endswith(
        f" init --config {config} --source-local {str(one).replace(' ', chr(92) + ' ')} --source-local {two}"
    )
    assert "add-source" not in out
    assert out.rstrip().splitlines()[-1] == "NEXT: re-run without --dry-run to apply the steps above"
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


def test_every_command_the_readme_one_prompt_names_exists() -> None:
    """The block a user pastes into a coding agent only names install.sh flags and agentsync subcommands and
    options that exist (a renamed flag would otherwise fail on the new Mac, not here)."""
    import argparse  # noqa: PLC0415

    from agentsync import cli  # noqa: PLC0415

    spans = re.findall(r"`([^`]+)`", _one_prompt_block())
    script = SCRIPTS[0].read_text(encoding="utf-8")
    help_text = subprocess.run(
        ["bash", str(SCRIPTS[0]), "--help"], capture_output=True, text=True, check=True, timeout=60
    ).stdout
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    install_flags: set[str] = set()
    commands: set[tuple[str, str]] = set()
    for span in spans:
        if "install.sh" in span:
            install_flags |= set(re.findall(r"(?<!\S)(--[a-z][a-z-]*)", span.split("install.sh", 1)[1]))
        for m in re.finditer(r"(?:^|[\s/])agentsync\s+([a-z][a-z-]*)((?:\s+--[a-z][a-z-]*)*)", span):
            commands |= {(m.group(1), flag) for flag in m.group(2).split()} | {(m.group(1), "")}
    assert {"--source-local", "--confirm-install-agent"} <= install_flags
    assert {"doctor", "sync", "status"} <= {c for c, _ in commands}
    for flag in sorted(install_flags):
        assert re.search(rf"^\s*(?:-h \| )?{re.escape(flag)}\)", script, flags=re.MULTILINE), flag
        assert flag in help_text, f"install.sh --help does not document {flag}"
    for command, flag in sorted(commands):
        assert command in sub.choices, f"agentsync has no subcommand {command!r}"
        if flag:
            options = {o for a in sub.choices[command]._actions for o in a.option_strings}
            assert flag in options, f"agentsync {command} has no option {flag}"
