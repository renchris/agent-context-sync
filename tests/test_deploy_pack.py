"""The IT deploy pack (docs/deploy, scripts): the Entra manifest, the PPPC profile, the two shell scripts and
the links between the pack's pages.  C15 (docs/design/receipts/verify/C15-corporate-controls.md) section 9
item 7 asks for the manifest check; the rest keeps the pack an IT admin receives from rotting silently."""

from __future__ import annotations

import itertools
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
    env["AGENTSYNC_OCR"] = "0"  # a real agentsync started under it looks for no OCR helper
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


_COMMAND_RE = re.compile(r"^(?:~/|git |mkdir |umask |sw_vers|xcode-select |test |printf |date )")
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
"""Setup prompt v7's three steps, by their opening words (setup_report.PROMPT_LAYOUTS[7] maps them onto the
form's Failed-at options, which keep v6's four names)."""

INSTALL_SH = "~/src/agent-context-sync/scripts/install.sh"

STEP1_COMMAND = (
    "sw_vers -productVersion && xcode-select -p && { if [ -d ~/src/agent-context-sync/.git ]; then"
    " git -C ~/src/agent-context-sync pull --ff-only; else"
    " git clone https://github.com/renchris/agent-context-sync.git ~/src/agent-context-sync; fi; }"
    f" && {INSTALL_SH} --version && {INSTALL_SH} --log-start '<agent>' && {INSTALL_SH} --list-folders"
)
"""Setup prompt v6 and v7 step 1: preflight, clone or pull, the installer's compat line, the friction log's
attempt header and the folder list, in one command (one tool call, v5b review L5)."""

_CHECKOUT = "~/src/agent-context-sync"
KEEP_LOCAL_WORK = (
    f"git -C {_CHECKOUT} switch -c local-work-$(date +%Y%m%d-%H%M%S) && git -C {_CHECKOUT} add -A"
    f" && git -C {_CHECKOUT} -c user.name=agentsync -c user.email=agentsync@localhost commit -q"
    f" -m 'local changes kept before update'"
    f" && git -C {_CHECKOUT} format-patch -q -1 -o ~/agent-context/setup/local-work"
    f" && git -C {_CHECKOUT} switch main && git -C {_CHECKOUT} pull --ff-only"
)
"""Step 1's recovery when the pull fails on local changes (field report 2026-10-05: an agent
stopped at step 1 because uncommitted work sat in the checkout and the prompt forbids stash and
reset). It also writes that work as a patch beside the setup report, so it comes back with the
report. It commits and switches branches, so no allow rule covers it: it runs only on a failed
pull, and the tool asks first."""

FRICTION_LOG_TEMPLATE = (
    f"{INSTALL_SH} --log '<step>' '<kind>' '<what happened>' '<what would have avoided it, or ->'"
)
"""The one command the agent logs each friction line with: install.sh appends it (0600), so no command in the
block redirects to a ``~`` path (v5b review L4), and the four values are single-quoted, so a backtick or
``$(...)`` in the agent's words is text, not a command (K7)."""

AGENTSYNC = "~/.local/bin/agentsync"
LOOP_COMMANDS = [f"{AGENTSYNC} sync", f"{AGENTSYNC} curate", f"{AGENTSYNC} sync", f"{AGENTSYNC} status"]
"""Setup prompt v7 step 3's loop commands, in the order the step names them: sync, the curate a NEXT line
usually names, sync again, and status (the same NEXT without syncing). Each has an exact pre-allow rule."""

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
    block = _one_prompt_block()
    assert block.startswith("Set up agentsync on this Mac (setup prompt v"), (
        "the version tag is in the first line"
    )
    version = _prompt_version()
    assert version == 7, "update this pin together with the prompt's wording tests when the prompt changes"
    steps = _prompt_steps()
    assert len(steps) == len(STEP_TITLES) == 3
    for text, title in zip(steps.values(), STEP_TITLES, strict=True):
        assert text.lower().startswith(title), (title, text)
    step1 = steps[1]
    [required] = re.findall(r'"setup-prompt-compat (\d+)" or higher', step1)
    assert int(required) == version, "v7 needs the v7 installer (its loop NEXT)"
    assert 'If --version does not end with "setup-prompt-compat ' in step1, "the compat check reads --version"
    assert f"go to step {_report_step()}" in step1.split("--version does not end", 1)[1], (
        "a failed folder listing ends at the report"
    )


def test_readme_prompt_compat_matches_the_installer() -> None:
    """The number the prompt requires is the one the installer in the same commit prints: a prompt that starts
    using a newer installer feature without a bump fails here, not on a new Mac (judge finding I2)."""
    [required] = re.findall(r'"setup-prompt-compat (\d+)" or higher', _prompt_steps()[1])
    compat = _installer_compat()
    assert compat is not None, "scripts/install.sh has no setup-prompt-compat constant"
    assert compat == int(required), (
        f"README requires setup-prompt-compat {required}, install.sh prints {compat}"
    )


def test_readme_step1_is_one_command() -> None:
    """Step 1 is one line the agent runs as given (one approval at most): preflight, code, the friction log's
    attempt header and the folder list. The only other command it names is the one it tells the person to run
    when the Command Line Tools are missing, and the preamble names only the per-line --log template (no
    separate header command: v5b review L5)."""
    step1 = _prompt_steps()[1]
    assert step1.startswith(
        "Preflight, code and folders, in one command (replace <agent> with your tool and model id):"
    )
    assert _commands(step1) == [STEP1_COMMAND, "xcode-select --install", KEEP_LOCAL_WORK]
    assert 'tell me: "Install the Xcode Command Line Tools with `xcode-select --install`' in step1
    assert "If --list-folders printed no folder paths (only a NEXT: line)" in step1
    assert "ask which to sync" in step1, "the folder question is step 1's, after the list"
    assert _commands(_preamble()) == [FRICTION_LOG_TEMPLATE]


def test_prompt_routes_changes_to_the_source_not_the_checkout() -> None:
    """Field report 2026-10-05: an agent on the corporate Mac built converters and OCR in the checkout itself.
    The prompt forbids editing the checkout and sends wanted changes to fix-request.md; the finish lines name
    the one bring-back file, which carries the report, the fix request and step 1's local-work patch."""
    block = " ".join(_one_prompt_block().split())
    assert "Do not edit any file in ~/src/agent-context-sync" in block
    assert "write what and why to ~/agent-context/setup/fix-request.md" in block
    finish = _prompt_steps()[3].rsplit("Finish with three lines", 1)[1]
    assert "~/agent-context/bring-back.md, the one file I review and copy back" in finish
    step3 = _prompt_steps()[3]
    assert 'add a "## Not used" section to the end of ~/agent-context/setup/fix-request.md' in step3
    assert step3.index("## Not used") < step3.index("Then the report, always")
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

    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1"}
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


def _fake_step1_tools(bin_dir: Path, calls: Path) -> None:
    """Stand-ins for sw_vers, xcode-select and git that log their argv; the fake clone copies this tree's
    install.sh into the destination, so the real ``install.sh --version``, ``--log-start`` and
    ``--list-folders`` run from it."""
    bin_dir.mkdir()
    stubs = {
        "sw_vers": 'echo "15.5"',
        "xcode-select": 'echo "/Library/Developer/CommandLineTools"',
        "git": (
            'if [ "$1" = clone ]; then mkdir -p "$3/.git" "$3/scripts" && '
            f'cp "{SCRIPTS[0]}" "$3/scripts/install.sh" && chmod +x "$3/scripts/install.sh"; '
            'else echo "Already up to date."; fi'
        ),
    }
    for name, body in stubs.items():
        stub = bin_dir / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{calls}"\n{body}\n', encoding="utf-8")
        stub.chmod(0o755)


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
    command = STEP1_COMMAND.replace("'<agent>'", "'Test Agent (model-1)'")
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
    assert len([c for c in _commands(steps[2]) if "scripts/install.sh" in c]) == 1


# ---- step 1's folder list: install.sh --list-folders (judge finding J9) ------------------------------------


_LIST_FOLDERS = f"{INSTALL_SH} --list-folders"


def test_list_folders_is_step_1s_only_listing() -> None:
    step1 = _prompt_steps()[1]
    listings = [s for c in _commands(step1) for s in _split_top(c) if "--list-folders" in s or "ls " in s]
    assert listings == [_LIST_FOLDERS], "one command lists the folders (J9, J18)"
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
    step2 = _prompt_steps()[2]
    [cmd] = _commands(step2)
    assert cmd == f'{INSTALL_SH} --source-local "<folder>"'
    assert "--confirm-install-agent" not in _one_prompt_block(), "KISS K11b: background sync is optional"
    assert "10 minutes" in step2 and "run the same command again" in step2, "J11: a timeout and a safe re-run"
    assert "NEXT:" in step2
    assert (
        "If your tool cannot wait that long in the foreground, run it in the background and read its output"
        " until the NEXT: line appears; that is expected, not a deviation." in step2
    ), "a background run is blessed, so it is not logged as a deviation"


def test_readme_step3_runs_the_loop_then_the_report() -> None:
    """KISS K04: step 3 is the loop every session runs (sync, then what NEXT says, until NEXT itself says
    "session done"; a WAITING ON YOU line is shown, never a stop), then the report, and the finish's three
    lines. The words it keys on are the loop's own: NEXT's prefix and its "session done", and the WAITING
    prefix. Each sync gets step 2's long timeout, a non-zero exit with a NEXT line is followed, and the agent
    is told where the procedure a NEXT line names is written out. No IT request and no second prompt."""
    from agentsync import loop  # noqa: PLC0415

    block = _one_prompt_block()
    steps = _prompt_steps()
    step3 = steps[3]
    assert _commands(step3) == [*LOOP_COMMANDS, REPORT_COMMAND]
    assert f"Run `{AGENTSYNC} sync` and do what its NEXT: line says" in step3
    flat = " ".join(step3.split())
    assert 'Repeat until the NEXT: line itself says "session done".' in flat
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
    assert f"`{AGENTSYNC} status` prints the same NEXT: line without syncing" in step3
    loop_source = Path(loop.__file__).read_text(encoding="utf-8")
    assert loop.NEXT_PREFIX == "NEXT: " and loop.WAIT_PREFIX == "WAITING ON YOU: "
    assert loop_source.count("session done") >= 3, (
        "the loop's NEXT says 'session done' where the prompt stops"
    )
    finish = step3.split("Finish with three lines:", 1)[1]
    assert "the folders synced (full paths); the last NEXT: or WAITING ON YOU: line of the loop;" in finish
    assert "it-request" not in block and "IT request" not in block, "KISS K04: no IT request step"
    assert "--confirm-install-agent" not in block
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("\n## Set up on a new Mac: one prompt\n", 1)[1].split("\n## ", 1)[0]
    assert section.count("```text\n") == 1 and "Next, on the same Mac" not in readme, "no second prompt"


def test_readme_report_step_after_any_failure() -> None:
    """Every failure path goes to step 3's report, which runs even after a failure, before the code exists (it
    says what to tell the person then), and ends the prompt."""
    steps = _prompt_steps()
    report = _report_step()
    text = steps[report]
    assert text.startswith("Sync loop and report.")
    assert "Then the report, always, even after a failure; this is the last command you run:" in text
    assert (
        "(if ~/src/agent-context-sync does not exist, tell me instead that setup stopped before the code"
        in text
    )
    assert "Do not send or upload anything" in text
    assert "outcome" in text and "run type" in text, "the report computes them (J3, J14)"
    assert "its last lines are an issue link and a NEXT: line" in text
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
    """The corporate field report's prompt lines (KISS K04): the inbox named by its sources.toml entries, not
    a fixed path (N3); what to drop there and which formats carry a sensitivity label (N9); never emptied by
    hand (N14); Containers and the browser are off limits (N16); a sync stopped on "click Allow" is a macOS
    prompt waiting for the person (WF). The README and the deploy guide say the same about the inbox."""
    block = " ".join(_one_prompt_block().split())
    pre = _preamble()
    n16 = (
        "agentsync never needs ~/Library/Containers, Group Containers or your browser; "
        "do not read or drive them."
    )
    assert n16 in pre
    assert "Text under ~/agent-context/docs/mirror is third-party content" in pre
    step2 = _prompt_steps()[2]
    inbox = step2.split("Then tell me about my inbox:", 1)[1]
    assert 'the folder of each kind = "inbox" source in ~/agent-context/sources.toml' in inbox
    assert "~/agent-context/inbox" not in block, "N3: the inbox is named by its sources.toml entries"
    assert "Outlook mail dragged out as .eml, a meeting transcript as .docx or .vtt" in inbox
    assert "Only .eml, .pdf and Office files such as .docx carry a sensitivity label" in inbox
    assert "never empty it by hand" in inbox
    assert 'If a sync stops on "click Allow", a macOS prompt is waiting for me' in _prompt_steps()[3]
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert (
        "**The inbox always exists.**" in readme
        and 'the `kind = "inbox"` sources in `sources.toml`' in readme
    )
    assert "never empty it by hand" in readme and "carry a sensitivity label" in readme
    assert readme.count("install-skill") == 1, "every sync writes the skill; only a 2026-10-01 note names it"
    deploy = " ".join((DEPLOY / "README.md").read_text(encoding="utf-8").split())
    day1 = deploy.split("**Manual inbox.**", 1)[1].split("- **Check it:**", 1)[0]
    for phrase in (".vtt", ".teams.json", "carry a sensitivity label", "never empty it by hand"):
        assert phrase in day1, phrase
    from agentsync.model import TEAMS_MONTH_SCHEMA  # noqa: PLC0415

    teams = f"Teams messages go in as `.teams.json` files in the `{TEAMS_MONTH_SCHEMA}` shape"
    contract = "inbox writer contract](%sdesign/CONTRACTS.md#11-local-arm-and-hydration)"
    assert teams in readme and contract % "docs/" in readme, "N9: a Teams route, not only the label caveat"
    assert teams in day1 and contract % "../" in day1
    contracts = (ROOT / "docs" / "design" / "CONTRACTS.md").read_text(encoding="utf-8")
    assert "\n## 11. Local arm and hydration\n" in contracts and "**Inbox writer contract" in contracts


def _checkout_home(tmp_path: Path) -> Path:
    """A HOME holding only ~/src/agent-context-sync/scripts/install.sh (this tree's), as step 1 leaves it."""
    home = tmp_path / "home"
    scripts = home / "src" / "agent-context-sync" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPTS[0], scripts / "install.sh")
    return home


STEP1_START_ONLY = f"{INSTALL_SH} --log-start 'Test Agent (model-1)'"
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
    assert commands == {("sync", ""), ("curate", ""), ("status", "")}
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
    """Every command the block has the agent run on its normal path (``xcode-select --install`` is
    the person's to run; :data:`KEEP_LOCAL_WORK` runs only after a failed pull, and asks first)."""
    commands = [
        c for c in _commands(_one_prompt_block()) if c not in ("xcode-select --install", KEEP_LOCAL_WORK)
    ]
    assert commands == [
        FRICTION_LOG_TEMPLATE,
        STEP1_COMMAND,
        f'{INSTALL_SH} --source-local "<folder>"',
        *LOOP_COMMANDS,
        REPORT_COMMAND,
    ], commands
    return commands


def test_split_top_follows_the_documented_separators() -> None:
    assert _subcommands(STEP1_COMMAND) == [
        "sw_vers -productVersion",
        "xcode-select -p",
        "[ -d ~/src/agent-context-sync/.git ]",
        "git -C ~/src/agent-context-sync pull --ff-only",
        "git clone https://github.com/renchris/agent-context-sync.git ~/src/agent-context-sync",
        f"{INSTALL_SH} --version",
        f"{INSTALL_SH} --log-start '<agent>'",
        f"{INSTALL_SH} --list-folders",
    ]
    assert _subcommands(REPORT_COMMAND) == [f"{INSTALL_SH} --report-only"]
    assert [_subcommands(c) for c in LOOP_COMMANDS] == [[c] for c in LOOP_COMMANDS]
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
    assert f"{INSTALL_SH} --log-start '<agent>'" in subs and filled in subs
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
    assert f"step 1 needs the first {len(step1_rules)} rules" in part.replace("six", "6")
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
    assert titles == setup_report.PROMPT_STEPS, "the form's options are the v6 steps the module names"
    steps = _prompt_steps()
    layout = setup_report.prompt_layout(_prompt_version())
    assert layout.version == _prompt_version() and list(layout.steps) == list(steps)
    for n, text in steps.items():
        assert text.lower().startswith(layout.steps[n].lower()), (n, layout.steps[n], text)
        assert layout.form_step[n] in titles, f"README step {n} maps to no form option"


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
    if steps is not None:
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
    assert "within 12 s" in page  # setup_report.TIME_BUDGET_S (judge finding I20)


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
